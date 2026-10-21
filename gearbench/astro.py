"""The Astro A50 Gen 4 base station's control protocol. Nothing here imports the daemon.

Not HID++: one vendor HID interface, report id 2, 64 bytes each way on hidraw. A request
is `02 CMD B1 payload`, zero-padded, where B1 is `seq << 6 | len(payload)`; a command at
0x80 or above is relayed to the headset, and its payload starts with a 0x0A byte. A reply
is `02 STATUS B1 data`: status 02 carries data, 00 accepts, 01 is an error (a u32 code
and a NUL-terminated name). The base echoes the seq, answers each request once and sends
nothing unasked.
"""

from __future__ import annotations

import errno
import logging
import os
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from .hidpp import GONE_ERRNOS, HidrawNode, find_attached
from .wording import counted, escaped, number, whole, word_list, written

log = logging.getLogger("gearbench")

VENDOR_ID = 0x9886
# Only PC mode is measured; the Xbox edition (002d) and console mode (002b,
# which has no HID interface) stay out until read off the hardware.
PRODUCTS = {0x002C: "Astro A50"}
INTERFACE = 6
REPORT_ID = 0x02
REPORT_SIZE = 64
MAX_PAYLOAD = REPORT_SIZE - 3
RELAYED = 0x80
RELAY_BYTE = 0x0A

STATUS_ACCEPTED = 0x00
STATUS_ERROR = 0x01
STATUS_DATA = 0x02

# Reset, bootloader, raw I2C, radio flash, the base's and headset's ids,
# kiosk mode, the headset's updater. Listed so a test can prove SENT misses
# every one; frame() refuses anything outside SENT.
NEVER_SEND = frozenset(
    {0x01, 0x02, 0x08, 0x65, 0x81, 0x82}
    | (set(range(0x41, 0x5D)) - {0x54, 0x55})
    | set(range(0xF0, 0x100))
)
SENT = frozenset({
    0x03, 0x54, 0x55, 0x61, 0x62, 0x63, 0x64, 0x67, 0x68, 0x69, 0x6A, 0x6C,
    0x6D, 0x6E, 0x6F, 0x70, 0x71, 0x72, 0x73, 0x74, 0x76, 0x77, 0x78, 0x7A,
    0x7B, 0x7C, 0xD6, 0xDA,
})

REQUEST_TIMEOUT = 1.0   # every command but 61 answers in milliseconds
SAVE_TIMEOUT = 6.0      # 61 takes over 2 s
SAVE_RETRY_DELAY = 1.5  # after 61 answers code 2
SAVE_OK = 0
SAVE_RETRY = 2
PRESET_SETTLE = 0.72    # 6C reports the old preset this long after a 67

# The socket's words for the settings `A50.set` takes. Sliders are the base's
# slider ids.
SLIDERS = {
    "mic_level": 4, "sidetone": 5,
    "stream_game": 2, "stream_chat": 1, "stream_mic": 0, "stream_aux": 3,
}
NOISE_GATES = ("streaming", "night", "home", "tournament")   # wire values 0-3
MIC_EQS = ("bright", "airy", "warm")                          # wire values 0-2
AUTO_OFF_MINUTES = (10, 15, 20, 25, 30)
#: The settings whose value is a word, with their words.
SETTING_WORDS = {"noise_gate": NOISE_GATES, "mic_eq": MIC_EQS}
SETTINGS = (
    *SLIDERS, "noise_gate", "mic_eq", "default_balance", "alert_volume", "auto_off",
)
# Settings whose flash value can be read beside the RAM one; the rest can
# only be marked unsaved by having been written since the last save.
READS_SAVED = frozenset({*SLIDERS, "noise_gate", "mic_eq", "default_balance", "alert_volume"})
# Each setting's name, in a refusal and in the window: its socket word spaced and
# capitalized, or its own where the word does not spell it.
SETTING_NAMES = {**{name: name.replace("_", " ").capitalize() for name in SETTINGS},
                 "mic_eq": "Mic EQ", "auto_off": "Auto shut-off"}

PRESETS = (1, 2, 3)
BANDS = (1, 2, 3, 4, 5)
SHELVES = (1, 5)         # no width; sent as 0
GAIN_RANGE = (-7, 7)     # dB, what Command Center offers
GAIN_OFFSET = 12         # the wire's gain byte is dB plus this
FREQ_RANGE = (80, 15000)
WIDTH_RANGE = (0.1, 3.0)
WIDTH_SCALE = 4096
NAME_BYTES = (1, 49)
#: How long a front end waits, in seconds, in place of send_command's 3 s, for a request
#: that is tens of round trips to the base station, one at a time: a full read, an EQ
#: write, a revert.
SLOW_REQUEST_WAIT = 10.0
LEVEL_RANGE = (0, 100)    # the sliders and the alert volume, in percent
BALANCE_RANGE = (0, 255)  # 0 all Voice, 255 all Game
#: The settings that take a whole number from a range, each with its range; auto shut-off
#: takes one of AUTO_OFF_MINUTES. A50.set, the CLI and the window check values against it.
SETTING_RANGES = {**dict.fromkeys(SLIDERS, LEVEL_RANGE), "default_balance": BALANCE_RANGE,
                  "alert_volume": LEVEL_RANGE}
#: The settings whose value is one of a list (the words, and auto shut-off's minutes), as
#: the CLI and the window list them.
SETTING_CHOICES: dict[str, tuple[str, ...] | tuple[int, ...]] = {
    **SETTING_WORDS, "auto_off": AUTO_OFF_MINUTES}


def balance_shares(value: int) -> tuple[int, int]:
    """A Game/Voice balance, 0 all Voice to 255 all Game, as (Voice, Game) in whole
    percent: what `gearbench-ctl headset` and the Headset tab show."""
    game = round(value * 100 / BALANCE_RANGE[1])
    return 100 - game, game


class AstroFailure(Exception):
    """A failure on the base station's node, or a missing or unusable answer; a request
    `frame` refuses raises ValueError instead."""


class AstroError(AstroFailure):
    """The base answered with an error: `code` and its `name`, such as
    VALUE_OUT_OF_RANGE or SLAVE_NO_SLAVE, or "error 13" for a reply that
    names none, since the refusal sentences quote it."""

    def __init__(self, code: int, name: str) -> None:
        super().__init__(f"{name or 'error'} ({code})")
        self.code = code
        self.name = name or f"error {code}"


class AstroTimeout(AstroFailure):
    """No reply before the deadline, or a write the base did not take."""


class BaseGone(AstroFailure):
    """The node went away: the base station was unplugged."""


class NodeFailure(AstroFailure):
    """A read or write on the node failed with an errno that is neither gone
    nor a timeout: nothing was answered."""


@dataclass(frozen=True)
class Reply:
    """One reply. `data` is everything after B1, padding included: the
    length field is not trusted, so parse fixed offsets."""

    status: int
    seq: int
    data: bytes


@dataclass(frozen=True)
class Band:
    """One EQ band: `freq` in Hz, `width` the bandwidth as a multiple of `freq`, so 1/Q
    (None on the shelves)."""

    freq: int
    width: float | None


@dataclass(frozen=True)
class EqPreset:
    """An .astroeq file: five gains in dB, five frequencies, the widths of
    bands 2 to 4."""

    name: str
    gains: tuple[int, int, int, int, int]
    freqs: tuple[int, int, int, int, int]
    widths: tuple[float, float, float]

    @classmethod
    def of_bands(cls, name: str, gains: Sequence[int], bands: Sequence[Band]) -> EqPreset:
        """The preset of `name`, a gain and a Band for each of BANDS, band 1 first, as
        `bands` gives them back: each band's frequency, and the width of each but SHELVES."""
        # The caller gives five gains and five bands, BANDS' count.
        return cls(name, cast("tuple[int, int, int, int, int]", tuple(gains)),
                   cast("tuple[int, int, int, int, int]", tuple(band.freq for band in bands)),
                   cast("tuple[float, float, float]", tuple(
                       band.width for number, band in zip(BANDS, bands, strict=True)
                       if number not in SHELVES)))

    def bands(self) -> list[Band]:
        return [Band(self.freqs[band - 1], None if band in SHELVES else self.widths[band - 2])
                for band in BANDS]

    def request(self) -> dict:
        """`headset_eq` for this file: its name, gains and five bands, on the active
        preset unless the caller adds one."""
        return {"command": "headset_eq", "name": self.name, "gains": list(self.gains),
                "bands": [{"band": number, "freq": band.freq, "width": band.width}
                          for number, band in zip(BANDS, self.bands(), strict=True)]}


def find_base() -> tuple[Path, int] | None:
    """The base station's hidraw node and product id, read from sysfs by the
    same vid, pid and interface `70-astro-a50.rules` matches; opens nothing."""
    found = find_attached(vendor=VENDOR_ID, product=tuple(PRODUCTS), interface=INTERFACE)
    return (found[0], found[1]) if found else None


def open_a50(path: str | os.PathLike[str]) -> A50:
    """Open the node and wrap it; raises PermissionError without the rule."""
    return A50(Base(path))


def frame(cmd: int, payload: bytes = b"", seq: int = 0) -> bytes:
    """The 64-byte report for one request; raises ValueError on a bad cmd, payload or seq."""
    if cmd not in SENT:
        raise ValueError(f"0x{cmd:02X} is not a command Gearbench sends to the base station")
    if seq not in range(4):
        raise ValueError(f"seq {seq} is not 0 to 3")
    body = bytes([RELAY_BYTE]) + payload if cmd >= RELAYED else payload
    if len(body) > MAX_PAYLOAD:
        raise ValueError(f"a {len(body)}-byte payload does not fit in a report")
    return (bytes([REPORT_ID, cmd, seq << 6 | len(body)]) + body).ljust(REPORT_SIZE, b"\x00")


def parse(report: bytes) -> Reply | None:
    """A report read from the node, or None when it is not a reply."""
    if len(report) < 3 or report[0] != REPORT_ID:
        return None
    return Reply(status=report[1], seq=report[2] >> 6, data=report[3:])


def _checked(reply: Reply, what: str) -> Reply:
    """The reply, or the error it carries."""
    if reply.status == STATUS_ERROR:
        name = reply.data[4:].split(b"\x00", 1)[0].decode("ascii", "replace")
        name = name.removeprefix("HID_ERROR_")  # as sent: HID_ERROR_NO_EQ_WITH_THAT_VALUE
        # Escaped: journal lines quote the name, and the journal splits a record at each
        # newline; a foreground daemon's terminal acts on ESC.
        raise AstroError(int.from_bytes(reply.data[:4], "little"), escaped(name))
    if reply.status not in (STATUS_ACCEPTED, STATUS_DATA):
        raise AstroFailure(f"{what} answered with status 0x{reply.status:02X}")
    return reply


def _dropped(report: bytes) -> None:
    log.debug("astro: dropped %s", report.rstrip(b"\x00").hex(" ") or "an empty report")


class Base(HidrawNode):
    """The base station's hidraw node: one request in flight, no resend."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._setup(os.open(path, os.O_RDWR | os.O_NONBLOCK))

    @classmethod
    def from_fd(cls, fd: int) -> Base:
        """A channel over a descriptor somebody else opened, and now owns."""
        base = cls.__new__(cls)
        base._setup(fd)
        return base

    def _setup(self, fd: int) -> None:
        self._fd = fd
        self._seq = 3  # so the first request takes 0

    def request(self, cmd: int, payload: bytes = b"", *,
                timeout: float = REQUEST_TIMEOUT) -> Reply:
        """Send one request and wait for the reply that echoes its seq, dropping what was
        queued and any other seq. Raises AstroError on status 01, or another AstroFailure."""
        seq = self.send(cmd, payload)
        deadline = time.monotonic() + timeout
        while (report := self._read(deadline)) is not None:
            reply = parse(report)
            if reply is not None and reply.seq == seq:
                return _checked(reply, f"0x{cmd:02X}")
            _dropped(report)
        raise AstroTimeout(f"no reply to 0x{cmd:02X} within {timeout:g} s")

    def send(self, cmd: int, payload: bytes = b"") -> int:
        """Send one request without waiting and return its seq, for `answer`
        (61). The base never speaks unasked, so anything already queued is a
        late reply and goes first."""
        seq = (self._seq + 1) % 4
        packet = frame(cmd, payload, seq)
        self._seq = seq
        self.pump()
        try:
            os.write(self._fd, packet)
        except OSError as exc:
            raise _node_failure(exc, "write to") from exc
        return seq

    def answer(self, seq: int) -> Reply | None:
        """The reply to a `send`, if it has arrived; reads without blocking
        and drops anything else. Raises AstroError on status 01."""
        while (report := self._raw_read()) is not None:
            reply = parse(report)
            if reply is not None and reply.seq == seq:
                return _checked(reply, f"the request with seq {seq}")
            _dropped(report)
        return None

    def pump(self) -> None:
        """Read and drop what is queued, for a readable node nobody waits on."""
        while (report := self._raw_read()) is not None:
            _dropped(report)

    # -- wire ------------------------------------------------------------

    def _raw_read(self) -> bytes | None:
        """One queued report, or None when nothing is waiting. With send,
        the only place an errno becomes an AstroFailure."""
        try:
            report = os.read(self._fd, REPORT_SIZE)
        except BlockingIOError:
            return None
        except OSError as exc:
            raise _node_failure(exc, "read from") from exc
        if not report:
            # hidraw never reads empty; a descriptor whose far end closed does.
            raise BaseGone("the node closed")
        return report


def _node_failure(exc: OSError, what: str) -> AstroFailure:
    if exc.errno in GONE_ERRNOS:
        return BaseGone(str(exc))
    if exc.errno == errno.ETIMEDOUT:
        # A write the base does not take fails after the kernel's 5 s USB timeout,
        # O_NONBLOCK or not, and counts as no answer.
        return AstroTimeout(f"{what} the base station timed out: {exc}")
    return NodeFailure(f"{what} the base station failed: {exc}")


class A50:
    """The typed commands over a `Base`. Reads return what the base holds; `saved=True`
    asks for the flash value. Setters raise ValueError for a value outside the ranges
    above, before sending, and AstroFailure on a wrong echo. A preset's checks are
    `preset_name`, `preset_gains` and `band_width`, for a caller that checks every part
    before its first write."""

    def __init__(self, base: Base) -> None:
        self.base = base

    def _ask(self, cmd: int, payload: bytes = b"", *, need: int = 1,
             echo: bytes = b"") -> bytes:
        """A read's data, at least `need` bytes of it, after checking `echo`.
        The length field is not trusted, so this counts what arrived."""
        reply = self.base.request(cmd, payload)
        if reply.status != STATUS_DATA:
            raise AstroFailure(f"0x{cmd:02X} answered without data")
        if (got := len(reply.data)) < need:
            raise AstroFailure(f"0x{cmd:02X} answered {counted(got, 'byte')}, not {need}")
        _check_echo(cmd, reply.data, echo)
        return reply.data

    def _put(self, cmd: int, payload: bytes, echo: bytes) -> None:
        reply = self.base.request(cmd, payload)
        if reply.status != STATUS_DATA:
            raise AstroFailure(f"0x{cmd:02X} was accepted without its echo")
        _check_echo(cmd, reply.data, echo)

    # Identity and state
    def info(self) -> dict:
        """03 and 55: {"version": "40372.43", "built": "2022-04-20 09:04:39"}."""
        prod = self._ask(0x03, need=25)
        radio = self._ask(0x55)[0]
        build = int.from_bytes(prod[21:25], "little")
        year = int.from_bytes(prod[8:10], "little")
        month, day, hour, minute, second = prod[10:15]
        return {
            "version": f"{build}.{radio}",
            "built": f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}",
        }

    def headset_version(self) -> str:
        """DA and D6, relayed: "39964.43". Only while linked."""
        mcu = int.from_bytes(self._ask(0xDA, need=4)[:4], "little")
        return f"{mcu}.{self._ask(0xD6)[0]}"

    def status(self) -> tuple[bool, bool]:
        """54: (docked, linked)."""
        value = self._ask(0x54)[0]
        return bool(value & 0x01), bool(value & 0x02)

    def battery(self) -> tuple[int, bool]:
        """7C: (percent clamped to 0-100, charging). Stale while unlinked."""
        value = self._ask(0x7C)[0]
        return min(value & 0x7F, 100), bool(value & 0x80)

    def balance(self) -> int:
        """72: the live Game/Voice mix the headset's buttons set, 0-255."""
        return self._ask(0x72)[0]

    # Settings: an (active, saved) pair where one reply carries both
    def slider(self, name: str) -> tuple[int, int]:
        slider = _slider(name)
        data = self._ask(0x68, bytes([slider]), need=4, echo=bytes([0x68, slider]))
        return data[2], data[3]

    def noise_gate(self) -> tuple[str, str]:
        data = self._ask(0x6A, need=3, echo=b"\x6a")
        return _word(0x6A, NOISE_GATES, data[1]), _word(0x6A, NOISE_GATES, data[2])

    def mic_eq(self, saved: bool = False) -> str:
        return _word(0x7B, MIC_EQS, self._ask(0x7B, _selector(saved))[0])

    def default_balance(self, saved: bool = False) -> int:
        return self._ask(0x77, _selector(saved))[0]

    def alert_volume(self, saved: bool = False) -> int:
        return self._ask(0x7A, _selector(saved))[0]

    def auto_off(self) -> int:
        return self._ask(0x78)[0]

    def set(self, setting: str, value: int | str) -> None:
        """Write one of SETTINGS; `value` is an int, or a word from
        NOISE_GATES or MIC_EQS."""
        if not isinstance(setting, str) or setting not in SETTINGS:
            raise _not_a_setting(setting)
        what = SETTING_NAMES[setting]
        if setting in SLIDERS:
            slider = SLIDERS[setting]
            self._put(0x62, bytes([slider, _whole(value, *SETTING_RANGES[setting], what)]),
                      bytes([0x62, slider]))
        elif setting == "noise_gate":
            gate = _choice(value, NOISE_GATES, what)
            self._put(0x64, bytes([gate]), bytes([gate]))
        elif setting == "mic_eq":
            self._put(0x71, bytes([_choice(value, MIC_EQS, what)]), b"\x71")
        elif setting == "default_balance":
            self._put(0x73, bytes([_whole(value, *SETTING_RANGES[setting], what)]), b"\x73")
        elif setting == "alert_volume":
            self._put(0x76, bytes([_whole(value, *SETTING_RANGES[setting], what)]), b"\x76")
        elif setting == "auto_off":
            minutes = _one_of(value, AUTO_OFF_MINUTES, what, " minutes")
            # Unverified on the hardware: the echo may be 74 or nothing at all.
            reply = self.base.request(0x74, bytes([minutes]))
            if reply.status == STATUS_DATA:
                _check_echo(0x74, reply.data, b"\x74")

    # Equalizer
    def preset(self) -> int:
        """6C: the active preset, 1-3."""
        preset = self._ask(0x6C)[0]
        if preset not in PRESETS:
            raise AstroFailure(f"0x6C named preset {preset}")
        return preset

    def set_preset(self, preset: int) -> None:
        preset = _one_of(preset, PRESETS, "Preset")
        self._put(0x67, bytes([preset]), bytes([0x67, preset]))

    def gains(self, preset: int) -> tuple[list[int], list[int]]:
        """69: (active, saved) gains in dB."""
        preset = _one_of(preset, PRESETS, "Preset")
        data = self._ask(0x69, bytes([preset]), need=12, echo=bytes([0x69, preset]))
        return ([g - GAIN_OFFSET for g in data[2:7]], [g - GAIN_OFFSET for g in data[7:12]])

    def set_gains(self, preset: int, gains: list[int] | tuple[int, ...]) -> None:
        preset = _one_of(preset, PRESETS, "Preset")
        wire = preset_gains(gains)
        self._put(0x63, bytes([preset, *wire]), bytes([0x63, preset]))

    def band(self, preset: int, band: int) -> tuple[Band, Band]:
        """70: (active, saved)."""
        preset, band = _one_of(preset, PRESETS, "Preset"), _one_of(band, BANDS, "Band")
        data = self._ask(0x70, bytes([preset, band]), need=11, echo=bytes([0x70, preset, band]))
        bw_active, bw_saved, fc_active, fc_saved = (
            int.from_bytes(data[i:i + 2], "little") for i in (3, 5, 7, 9))

        def at(bw: int, freq: int) -> Band:
            return Band(freq, None if band in SHELVES else round(bw / WIDTH_SCALE, 1))

        return at(bw_active, fc_active), at(bw_saved, fc_saved)

    def set_band(self, preset: int, band: int, freq: int, width: float | None) -> None:
        preset = _one_of(preset, PRESETS, "Preset")
        bw = band_width(band, freq, width)
        payload = bytes([preset, band]) + bw.to_bytes(2, "little") + freq.to_bytes(2, "little")
        self._put(0x6F, payload, bytes([0x6F, preset]))

    def name(self, preset: int, saved: bool = False) -> str:
        preset = _one_of(preset, PRESETS, "Preset")
        data = self._ask(0x6E, bytes([preset]) + _selector(saved), need=2,
                         echo=bytes([0x6E, preset]))
        return data[2:].split(b"\x00", 1)[0].decode("utf-8", "replace")

    def set_name(self, preset: int, name: str) -> None:
        preset = _one_of(preset, PRESETS, "Preset")
        text = preset_name(name, "A preset name")
        self._put(0x6D, bytes([preset, len(text) + 1]) + text + b"\x00", bytes([0x6D, preset]))

    # Flash
    def start_save(self) -> int:
        """Send 61 without waiting; returns the seq for `save_result`."""
        return self.base.send(0x61)

    def save_result(self, seq: int) -> int | None:
        """61's code once it has answered (SAVE_OK, SAVE_RETRY, other), or None."""
        reply = self.base.answer(seq)
        if reply is None:
            return None
        if reply.status != STATUS_DATA:
            raise AstroFailure("0x61 was accepted without its echo")
        _check_echo(0x61, reply.data, b"\x61")
        if len(reply.data) < 2:
            raise AstroFailure("0x61 answered without a code")
        return reply.data[1]


def read_setting(a50: A50, name: str) -> dict:
    """One of SETTINGS as the base holds it: {"active": value}, with "saved", the flash
    value, for each of READS_SAVED. Raises ValueError for any other name, as A50.set does,
    before sending."""
    active: int | str
    saved: int | str
    if name in SLIDERS:
        active, saved = a50.slider(name)
    elif name == "noise_gate":
        active, saved = a50.noise_gate()
    elif name == "mic_eq":
        active, saved = a50.mic_eq(), a50.mic_eq(saved=True)
    elif name == "default_balance":
        active, saved = a50.default_balance(), a50.default_balance(saved=True)
    elif name == "alert_volume":
        active, saved = a50.alert_volume(), a50.alert_volume(saved=True)
    elif name == "auto_off":
        return {"active": a50.auto_off()}
    else:
        raise _not_a_setting(name)
    return {"active": active, "saved": saved}


def _check_echo(cmd: int, data: bytes, echo: bytes) -> None:
    if data[:len(echo)] != echo:
        raise AstroFailure(f"0x{cmd:02X} answered {data[:len(echo)].hex(' ') or 'nothing'}, "
                           f"not {echo.hex(' ')}")


def _word(cmd: int, words: tuple[str, ...], value: int) -> str:
    if value >= len(words):
        raise AstroFailure(f"0x{cmd:02X} answered {value}, which names nothing")
    return words[value]


def _selector(saved: bool) -> bytes:
    """The byte that picks the flash value over the RAM one."""
    return b"\x01" if saved else b"\x00"


def _whole(value: object, low: int, high: int, what: str) -> int:
    if not whole(value) or not low <= value <= high:
        raise ValueError(f"{what} must be a whole number from {low} to {high}, "
                         f"not {written(value)}.")
    return value


def _choice(value: object, words: tuple[str, ...], what: str) -> int:
    if not isinstance(value, str) or value not in words:
        raise ValueError(f"{what} must be {word_list(words)}, not {written(value)}.")
    return words.index(value)


def _not_a_setting(name: object) -> ValueError:
    return ValueError(f"{written(name)} is not a headset setting; "
                      f"the settings are {', '.join(SETTINGS)}.")


def _slider(name: str) -> int:
    if not isinstance(name, str) or name not in SLIDERS:
        raise ValueError(f"{written(name)} is not a slider; "
                         f"the sliders are {', '.join(SLIDERS)}.")
    return SLIDERS[name]


def _one_of(value: object, choices: tuple[int, ...], what: str, unit: str = "") -> int:
    if not whole(value) or value not in choices:
        raise ValueError(f"{what} must be {word_list([str(c) for c in choices])}{unit}, "
                         f"not {written(value)}.")
    return value


#: Unicode's control characters (Cc), NUL aside.
_CONTROL = re.compile(r"[\x01-\x1f\x7f-\x9f]")


def preset_name(name: object, what: str = "A preset's name") -> bytes:
    """A preset's name as 6D takes it, in UTF-8. Raises ValueError with a sentence `what`
    starts."""
    low, high = NAME_BYTES
    try:
        text = name.encode("utf-8") if isinstance(name, str) else b""
    except UnicodeEncodeError:
        # UTF-8 refuses only a surrogate, which json.loads keeps from a lone "\ud800".
        raise ValueError(f"{what} cannot hold a lone surrogate: {name!r}.") from None
    if not low <= len(text) <= high:
        raise ValueError(f"{what} must be text of {low} to {high} bytes in UTF-8, "
                         f"not {written(name)}.")
    if b"\x00" in text:
        raise ValueError(f"{what} cannot hold a NUL character: {name!r}.")
    # The base keeps the name in flash and A50.name passes it on as read, so a control
    # character from a shared .astroeq would reach every place that shows the name, each
    # left to escape it. The name is text here: anything else was encoded as b"" above,
    # which NAME_BYTES refuses.
    if _CONTROL.search(cast("str", name)):
        raise ValueError(f"{what} cannot hold a control character: {name!r}.")
    return text


def preset_gains(gains: object) -> list[int]:
    """A preset's five gains in dB as 63 takes them, each plus GAIN_OFFSET. Raises ValueError
    with the sentence."""
    if not isinstance(gains, (list, tuple)) or len(gains) != len(BANDS):
        # A list of the wrong length is counted: its items can be anything, and as many as
        # a request holds.
        given = counted(len(gains), "gain") if isinstance(gains, (list, tuple)) else written(gains)
        raise ValueError(f"An equalizer takes {len(BANDS)} gains, not {given}.")
    return [_whole(g, *GAIN_RANGE, "Each gain") + GAIN_OFFSET for g in gains]


def band_width(band: object, freq: object, width: object) -> int:
    """A band's width as 6F takes it, 0 on a shelf, whose width is None. Raises ValueError
    with the sentence for a band, frequency or width it does not take."""
    band = _one_of(band, BANDS, "Band")
    _whole(freq, *FREQ_RANGE, f"Band {band}'s frequency")
    if band in SHELVES:
        if width is not None:
            raise ValueError(f"Band {band} is a shelf and takes no width, "
                             f"not {written(width)}.")
        return 0
    low, high = WIDTH_RANGE
    if not number(width) or not low <= width <= high:
        raise ValueError(f"Band {band}'s width must be {low} to {high}, not {written(width)}.")
    # Truncated, as Command Center does: 1.7 is 6963.
    return int(width * WIDTH_SCALE)
