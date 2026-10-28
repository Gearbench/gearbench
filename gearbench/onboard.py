"""0x8100 ONBOARD_PROFILES: reading, editing and writing a mouse's profile sectors.

The keyboard code takes only this module's constants and its mode functions, read_mode,
write_mode and running_sector, which hold no sector write: the keyboard's profile memory is
never written, the standing constraint. The daemon writes the keyboard's 0x8100 mode only in
its own set_keyboard_mode: HOST while it holds the keyboard, since in ONBOARD mode the
firmware owns the M keys and ignores software LED writes, and ONBOARD when it hands the
keyboard back.

The functions, as worked out against the hardware on Windows: fn 0 getInfo, 1
setOnboardMode, 2 getOnboardMode, 3 setCurrentProfile, 4 getCurrentProfile, 5 memoryRead
(16 bytes), 6 addrWrite, 7 memoryWrite (16 bytes, acked with a running counter) and 8
writeEnd, the commit. Sectors and offsets are uint16 big-endian.

Sector 0x0000 is the directory: four-byte entries (the profile's sector, 01 enabled or 00
not, a spare byte) ended by sector 0xffff, under the same CRC as a profile. The daemon
never writes it. 0x0001 and up are the writable profiles, 0x0101 and up their read-only
factory copies. The G502 X PLUS has 255-byte sectors, so a read at offset 0xf0 fails and
the last read is at 0xef. In ONBOARD mode a written profile takes effect at its commit,
with nothing else sent (measured on the G305 and the G502 X PLUS on 2026-09-24).
"""

from __future__ import annotations

import binascii
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

from .config import RATE_CHOICES, REPORT_RATES
from .hidpp import REQUEST_RETRIES, REQUEST_TIMEOUT, WRITE_ONCE_TIMEOUT, Feature, Hidpp
from .settings_files import ConfigError, backup_dir, link_to_nothing, missing_behind, replace_synced
from .wording import counted, escaped, reason

log = logging.getLogger("gearbench")

F_ONBOARD = 0x8100

MODE_ONBOARD = 0x01
MODE_HOST = 0x02
MODE_NAMES = {MODE_ONBOARD: "onboard", MODE_HOST: "host"}

FN_GET_INFO = 0
FN_SET_MODE = 1
FN_GET_MODE = 2
FN_SET_CURRENT_PROFILE = 3
FN_CURRENT_PROFILE = 4
FN_READ = 5
FN_ADDR_WRITE = 6
FN_WRITE = 7
FN_WRITE_END = 8

CHUNK = 16
DIRECTORY_SECTOR = 0x0000
FIRST_PROFILE_SECTOR = 0x0001
ROM_SECTOR_BASE = 0x0100

# Profile image layout. The shift DPI byte (0x02) and the name (0xA0) are
# mapped as libratbag, Solaar and cvuchener/hidpp map them.
OFF_RATE = 0x00
OFF_DEFAULT_DPI = 0x01
OFF_SHIFT_DPI = 0x02
OFF_DPI = 0x03
DPI_STAGES = 5
#: The DPI this code gives a mouse, in a stage slot or by setSensorDpi (Mouse.set_dpi):
#: two bytes, short of 0x0000 and 0xffff, which mark a stage slot unused (dpi_slot).
DPI_LOWEST, DPI_HIGHEST = 0x0001, 0xFFFE
#: The refusal of a DPI outside them, the value put in: "outside", as the Mouse tab's toast
#: says it; "out of range" is the radio's word.
DPI_OUTSIDE = f"DPI {{}} is outside {DPI_LOWEST} to {DPI_HIGHEST}"
OFF_BUTTONS = 0x20
# 0x60 is the G-Shift bank, which nothing binds (remap scope is the G/M keys and the six
# extra mouse buttons) and which round-trips untouched. It bounds the base table's 16
# four-byte entries.
OFF_GSHIFT = 0x60
ENTRY_SIZE = 4
OFF_NAME = 0xA0
NAME_UNITS = 24

#: Suffix of a backup set aside after a write, done or failed, a restore included, left the
#: sector as it was (set_aside, from Mouse._set_aside_unchanged); newest_backup never picks
#: one.
UNCHANGED = ".unchanged"

#: Report rate byte: the period in milliseconds, for each of config's REPORT_RATES.
RATE_BYTES = {1000 // hz: hz for hz in REPORT_RATES}
RATE_TO_BYTE = {hz: byte for byte, hz in RATE_BYTES.items()}

# Button entry forms, all verified by writing them to the mouse on Windows.
ENTRY_MACRO = 0x00
ENTRY_HID = 0x80
ENTRY_FUNCTION = 0x90
HID_NOOP = 0x00
HID_MOUSE = 0x01
HID_KEYSTROKE = 0x02
HID_CONSUMER = 0x03
NOOP = bytes([ENTRY_HID, HID_NOOP, 0xFF, 0xFF])
UNUSED = b"\xff\xff\xff\xff"

FUNCTION_NAMES = {
    0x00: "no action", 0x01: "tilt left", 0x02: "tilt right", 0x03: "DPI up",
    0x04: "DPI down", 0x05: "DPI cycle", 0x06: "default DPI", 0x07: "DPI shift",
    0x08: "next profile", 0x09: "previous profile", 0x0A: "profile cycle",
    0x0B: "G-Shift", 0x0C: "battery status", 0x0D: "profile select",
    0x0E: "mode switch", 0x0F: "host button", 0x10: "scroll down", 0x11: "scroll up",
}


class OnboardError(RuntimeError):
    pass


def read_mode(dev: Hidpp, feature: Feature, *, timeout: float = REQUEST_TIMEOUT,
              retries: int = REQUEST_RETRIES) -> int:
    """fn 2 getOnboardMode on the device's 0x8100 `feature`: the mode byte as answered."""
    return dev.request(feature.index, FN_GET_MODE, long=True, timeout=timeout,
                       retries=retries)[0]


def write_mode(dev: Hidpp, feature: Feature, mode: int, *, timeout: float = REQUEST_TIMEOUT,
               retries: int = REQUEST_RETRIES) -> None:
    """fn 1 setOnboardMode, MODE_ONBOARD or MODE_HOST; another mode is refused unsent."""
    if mode not in MODE_NAMES:
        raise OnboardError(f"onboard mode must be 0x01 or 0x02, not {mode:#x}")
    dev.request(feature.index, FN_SET_MODE, bytes([mode]), long=True, timeout=timeout,
                retries=retries)


def running_sector(dev: Hidpp, feature: Feature, *, timeout: float = REQUEST_TIMEOUT,
                   retries: int = REQUEST_RETRIES) -> int:
    """fn 4 getCurrentProfile: the sector the device runs, 1 for profile 1, 0x0101 for its ROM
    copy, 0 in HOST mode, where no profile runs."""
    reply = dev.request(feature.index, FN_CURRENT_PROFILE, long=True, timeout=timeout,
                        retries=retries)
    return int.from_bytes(reply[0:2], "big")


def crc16(data: bytes | bytearray) -> int:
    """CRC-16/CCITT-FALSE: crc_hqx is CRC-CCITT (poly 0x1021, no reflection, no final
    xor) from the value given. crc16(b"123456789") is 0x29b1."""
    return binascii.crc_hqx(data, 0xFFFF)


def sector_crc_ok(image: bytes | bytearray) -> bool:
    """Whether the big-endian CRC in the last two bytes covers the rest."""
    return len(image) > 2 and int.from_bytes(image[-2:], "big") == crc16(image[:-2])


def crc_words(ok: bool) -> str:
    """The words for a sector's CRC check, "CRC ok" or "CRC fails", the window's words for a
    profile that fails it, which RECOVERY in gearbench-ctl(1) tells the reader to look for."""
    return "CRC ok" if ok else "CRC fails"


# -- button entries ----------------------------------------------------------

def mouse_button(n: int) -> bytes:
    """Entry that sends mouse button n, 1-based."""
    if not 1 <= n <= 16:
        raise OnboardError(f"mouse button {n} outside 1..16")
    return bytes([ENTRY_HID, HID_MOUSE]) + (1 << (n - 1)).to_bytes(2, "big")


def entry_mouse_button(entry: bytes) -> int | None:
    """The button number a mouse-button entry sends, or None for any other entry."""
    if len(entry) != ENTRY_SIZE or entry[0] != ENTRY_HID or entry[1] != HID_MOUSE:
        return None
    mask = int.from_bytes(entry[2:4], "big")
    if mask == 0 or mask & (mask - 1):
        return None
    return mask.bit_length()


#: What the first five mouse buttons do, as a mouse's own entry sends them, in words.
MOUSE_BUTTON_JOBS = {1: "left click", 2: "right click", 3: "middle click", 4: "back",
                     5: "forward"}


def entry_job(entry: bytes) -> str:
    """What an entry has the mouse do, in a reader's words, for a row that shows the onboard
    profile's buttons: its job without describe_entry's codes, else describe_entry's words."""
    if entry == NOOP:
        return "nothing"
    button = entry_mouse_button(entry)
    if button in MOUSE_BUTTON_JOBS:
        return MOUSE_BUTTON_JOBS[button]
    if entry[0] == ENTRY_FUNCTION and entry[1] in FUNCTION_NAMES:
        return FUNCTION_NAMES[entry[1]]
    if entry[0] == ENTRY_HID and entry[1] == HID_KEYSTROKE:
        return "a keystroke"
    if entry[0] == ENTRY_HID and entry[1] == HID_CONSUMER:
        return "a media key"
    if entry[0] == ENTRY_MACRO:
        return "a macro"
    mask = int.from_bytes(entry[2:4], "big")
    if entry[0] == ENTRY_HID and entry[1] == HID_MOUSE and mask & (mask - 1):
        return "several mouse buttons"
    return describe_entry(entry)


def describe_entry(entry: bytes) -> str:
    if entry == UNUSED:
        return "unused"
    if entry == NOOP:
        return "no-op"
    kind = entry[0]
    value = int.from_bytes(entry[2:4], "big")
    if kind == ENTRY_HID and entry[1] == HID_MOUSE:
        button = entry_mouse_button(entry)
        return f"mouse button {button}" if button else f"mouse buttons mask 0x{value:04x}"
    if kind == ENTRY_HID and entry[1] == HID_KEYSTROKE:
        return f"keystroke usage 0x{entry[3]:02x} modifiers 0x{entry[2]:02x}"
    if kind == ENTRY_HID and entry[1] == HID_CONSUMER:
        return f"consumer control 0x{value:04x}"
    if kind == ENTRY_FUNCTION:
        name = FUNCTION_NAMES.get(entry[1], f"function 0x{entry[1]:02x}")
        # 0xff fills byte 3 as 0x00 does: the G502 HERO's function entries end ff ff, the
        # G502 X PLUS's 00 00.
        return f"{name} (arg {entry[3]})" if entry[3] not in (0x00, 0xFF) else name
    if kind == ENTRY_MACRO:
        return f"macro page {entry[1]} offset 0x{value:04x}"
    return entry.hex(" ")


# -- the profile image -------------------------------------------------------

def dpi_fits(value: int) -> bool:
    """Whether `value` is a DPI this code gives a mouse: DPI_LOWEST to DPI_HIGHEST."""
    return DPI_LOWEST <= value <= DPI_HIGHEST


def stage_dpi(stages: list[int], index: int) -> int | None:
    """The DPI of a default or shift index into `stages`, or None with no stages. An index
    past them, from a byte naming an unused slot (ProfileImage._stage_position), is taken as
    the last stage, as the daemon's DPI-shift paddle drops to it."""
    return stages[min(index, len(stages) - 1)] if stages else None


def stages_words(stages: list[int], default: int | None, shift: int | None) -> str:
    """A stage list with its default and shift on one line: "800, 1600, 3200 DPI, default
    1600, shift 800"."""
    return f"{', '.join(str(s) for s in stages)} DPI, default {default}, shift {shift}"


class ProfileImage:
    """One profile sector, edited in place so every byte this code does not understand is
    carried through. Only `to_bytes` recomputes the CRC."""

    def __init__(self, data: bytes) -> None:
        if len(data) < OFF_NAME + 2 * NAME_UNITS + 2:
            raise OnboardError(f"profile image is {counted(len(data), 'byte')}, "
                               f"too short to be a profile")
        self.data = bytearray(data)

    # -- CRC ---------------------------------------------------------------

    @property
    def crc_ok(self) -> bool:
        return sector_crc_ok(self.data)

    def to_bytes(self) -> bytes:
        body = bytes(self.data[:-2])
        return body + crc16(body).to_bytes(2, "big")

    # -- report rate ---------------------------------------------------------

    @property
    def report_rate(self) -> int | None:
        return RATE_BYTES.get(self.data[OFF_RATE])

    @report_rate.setter
    def report_rate(self, hz: int) -> None:
        if hz not in RATE_TO_BYTE:
            raise OnboardError(f"{hz} Hz is not one of {RATE_CHOICES} Hz")
        self.data[OFF_RATE] = RATE_TO_BYTE[hz]

    # -- DPI -----------------------------------------------------------------

    @property
    def dpi_stages(self) -> list[int]:
        """Active stages in slot order."""
        return [dpi for slot in range(DPI_STAGES) if (dpi := self.dpi_slot(slot)) is not None]

    @dpi_stages.setter
    def dpi_stages(self, stages: list[int]) -> None:
        if not 1 <= len(stages) <= DPI_STAGES:
            raise OnboardError(f"1 to {DPI_STAGES} DPI stages, not {len(stages)}")
        for value in stages:
            if not dpi_fits(value):
                raise OnboardError(DPI_OUTSIDE.format(value))
        # Unused slots are 0x0000, as in the G305 factory profile.
        self.data[OFF_DPI:OFF_DPI + 2 * DPI_STAGES] = b"".join(
            value.to_bytes(2, "little") for value in stages).ljust(2 * DPI_STAGES, b"\x00")

    def dpi_slot(self, slot: int) -> int | None:
        """The DPI in stage slot `slot`, 0 to 4, or None for an unused slot (0x0000 or 0xffff)
        or one past the table. The mouse counts its stages by slot, unused ones included, in
        0x8100 event 1 and fn 0xb (OpenLogi issue 1194; Solaar and libratbag index the five
        slots so)."""
        if not 0 <= slot < DPI_STAGES:
            return None
        value = int.from_bytes(self.data[OFF_DPI + 2 * slot:OFF_DPI + 2 * slot + 2], "little")
        return None if value in (0x0000, 0xFFFF) else value

    # The default and shift bytes name a slot, as the mouse counts them; these indices are
    # positions in dpi_stages, which leaves unused slots out.

    @property
    def default_dpi_index(self) -> int:
        return self._stage_position(self.data[OFF_DEFAULT_DPI])

    @default_dpi_index.setter
    def default_dpi_index(self, index: int) -> None:
        self.data[OFF_DEFAULT_DPI] = self._stage_slot(index)

    @property
    def shift_dpi_index(self) -> int:
        return self._stage_position(self.data[OFF_SHIFT_DPI])

    @shift_dpi_index.setter
    def shift_dpi_index(self, index: int) -> None:
        self.data[OFF_SHIFT_DPI] = self._stage_slot(index)

    @property
    def dpi_slots(self) -> list[int]:
        """The slot each of dpi_stages sits in: its level byte and indicator color count slots,
        unused ones included."""
        return [s for s in range(DPI_STAGES) if self.dpi_slot(s) is not None]

    def _stage_position(self, slot: int) -> int:
        """Where the stage in `slot` is in dpi_stages. A slot without one comes back as it is,
        past the stages unless the table has a gap; stage_dpi clamps it."""
        slots = self.dpi_slots
        return slots.index(slot) if slot in slots else slot

    def _stage_slot(self, index: int) -> int:
        used = self.dpi_slots
        if not 0 <= index < len(used):
            raise OnboardError(f"DPI stage index {index} outside the "
                               f"{counted(len(used), 'active stage')}")
        return used[index]

    # -- buttons -------------------------------------------------------------

    def button(self, index: int) -> bytes:
        offset = OFF_BUTTONS + ENTRY_SIZE * index
        return bytes(self.data[offset:offset + ENTRY_SIZE])

    def set_button(self, index: int, entry: bytes) -> None:
        if len(entry) != ENTRY_SIZE:
            raise OnboardError(f"a button entry is {ENTRY_SIZE} bytes, not {len(entry)}")
        if entry[0] == ENTRY_MACRO:
            # 0x00 in byte 0 is a macro pointer, not "disabled" (libratbag issue 1898); the
            # no-op is NOOP, 80 00 ff ff.
            raise OnboardError("refusing to write a macro pointer as a button entry")
        offset = OFF_BUTTONS + ENTRY_SIZE * index
        self.data[offset:offset + ENTRY_SIZE] = entry

    # -- name ----------------------------------------------------------------

    @property
    def name(self) -> str:
        raw = bytes(self.data[OFF_NAME:OFF_NAME + 2 * NAME_UNITS])
        end = next((i for i in range(0, len(raw), 2)
                    if raw[i:i + 2] in (b"\xff\xff", b"\x00\x00")), len(raw))
        return raw[:end].decode("utf-16-le", errors="replace")

    def describe(self, button_count: int) -> dict:
        # button_count is getInfo's byte 5, from the device: the base table holds 16 entries.
        count = min(button_count, (OFF_GSHIFT - OFF_BUTTONS) // ENTRY_SIZE)
        return {
            "report_rate": self.report_rate,
            "dpi_stages": self.dpi_stages,
            "default_dpi_index": self.default_dpi_index,
            "shift_dpi_index": self.shift_dpi_index,
            "dpi_slots": self.dpi_slots,
            "buttons": [describe_entry(self.button(i)) for i in range(count)],
            "name": self.name,
            "crc_ok": self.crc_ok,
        }


# -- the device ----------------------------------------------------------------

@dataclass(frozen=True)
class OnboardInfo:
    """fn 0 getInfo's first nine bytes, named in wire order; the reply's later bytes are not
    kept. `sector_count` is the spec's name for byte [6], which is not the sector size; it is
    unverified on this hardware, so do not build on it."""

    memory_model: int
    profile_format: int
    macro_format: int
    profile_count: int
    rom_profile_count: int
    button_count: int
    sector_count: int
    sector_size: int

    @classmethod
    def from_reply(cls, reply: bytes) -> OnboardInfo:
        if len(reply) < 9:
            raise OnboardError(f"getInfo reply too short: {reply.hex(' ')}")
        return cls(
            memory_model=reply[0], profile_format=reply[1], macro_format=reply[2],
            profile_count=reply[3], rom_profile_count=reply[4], button_count=reply[5],
            sector_count=reply[6], sector_size=int.from_bytes(reply[7:9], "big"),
        )


class OnboardProfiles:
    def __init__(self, dev: Hidpp) -> None:
        self.dev = dev
        self._feature = dev.require(F_ONBOARD, "ONBOARD_PROFILES")
        self.info = OnboardInfo.from_reply(
            self.dev.request(self._feature.index, FN_GET_INFO, long=True)
        )
        if not CHUNK < self.info.sector_size <= 4096:
            raise OnboardError(f"implausible sector size {self.info.sector_size}")
        #: What the last backup_all saved so far, by sector: the file and the image in it.
        self.backed_up: dict[int, tuple[Path, bytes]] = {}

    @property
    def profile_sectors(self) -> list[int]:
        return [FIRST_PROFILE_SECTOR + i for i in range(self.info.profile_count)]

    # -- mode ----------------------------------------------------------------

    def get_mode(self, *, timeout: float = REQUEST_TIMEOUT, retries: int = REQUEST_RETRIES) -> int:
        return read_mode(self.dev, self._feature, timeout=timeout, retries=retries)

    def set_mode(self, mode: int, *, timeout: float = REQUEST_TIMEOUT,
                 retries: int = REQUEST_RETRIES) -> None:
        write_mode(self.dev, self._feature, mode, timeout=timeout, retries=retries)

    def current_profile(self, *, timeout: float = REQUEST_TIMEOUT,
                        retries: int = REQUEST_RETRIES) -> int:
        """The sector the mouse runs (running_sector)."""
        return running_sector(self.dev, self._feature, timeout=timeout, retries=retries)

    def set_current_profile(self, sector: int, *, timeout: float = REQUEST_TIMEOUT,
                            retries: int = REQUEST_RETRIES) -> None:
        """Run another profile with fn 3, sent short as G HUB sends it. It writes no flash."""
        self.dev.request(self._feature.index, FN_SET_CURRENT_PROFILE,
                         sector.to_bytes(2, "big"), timeout=timeout, retries=retries)

    # -- memory --------------------------------------------------------------

    def read_sector(self, sector: int) -> bytes:
        """The whole sector, in 16-byte reads that never run past its end. A writable sector
        whose CRC fails is read once more, since a chunk the transport delivered late is the
        likeliest cause; a ROM sector's CRC field is 0xffff, so it is not. The caller still
        checks the CRC."""
        image = self._read_sector_once(sector)
        if sector < ROM_SECTOR_BASE and not sector_crc_ok(image):
            log.info("sector 0x%04x read with a bad CRC; reading it again", sector)
            image = self._read_sector_once(sector)
        return image

    def _read_sector_once(self, sector: int) -> bytes:
        size = self.info.sector_size
        out = bytearray(size)
        for offset in range(0, size, CHUNK):
            offset = min(offset, size - CHUNK)
            reply = self.dev.request(
                self._feature.index, FN_READ,
                sector.to_bytes(2, "big") + offset.to_bytes(2, "big"), long=True,
            )
            if len(reply) < CHUNK:
                # A short slice assigned would resize the bytearray and shrink the image silently.
                raise OnboardError(
                    f"sector 0x{sector:04x} offset 0x{offset:02x} read back "
                    f"{counted(len(reply), 'byte')}, not {CHUNK}: "
                    f"{reply.hex(' ')}"
                )
            out[offset:offset + CHUNK] = reply[:CHUNK]
        return bytes(out)

    @staticmethod
    def reseal_rom(image: bytes) -> bytes:
        """A factory ROM copy with a CRC that verifies, ready to be written back. The copies
        carry 0xffff in place of a CRC, so write_sector refuses them as read. In G HUB's backup
        images of the G502 X PLUS, sector 0x0101 matches 0x0001 but for those two bytes, and the
        CRC computed over the rest is the one 0x0001 stores, so this restores the factory profile
        exactly."""
        return ProfileImage(image).to_bytes()

    def write_sector(self, sector: int, image: bytes) -> bytes:
        """The write transaction, then a read-back that must match byte for byte, returned. The
        image must carry a valid CRC (ProfileImage.to_bytes, reseal_rom)."""
        size = self.info.sector_size
        if len(image) != size:
            raise OnboardError(f"image is {counted(len(image), 'byte')}, sector is {size:,}")
        if not sector_crc_ok(image):
            raise OnboardError("refusing to write an image whose CRC does not verify")
        if sector >= ROM_SECTOR_BASE:
            raise OnboardError(f"sector 0x{sector:04x} is a read-only ROM copy")

        self.dev.request(
            self._feature.index, FN_ADDR_WRITE,
            sector.to_bytes(2, "big") + b"\x00\x00" + size.to_bytes(2, "big"), long=True,
        )
        frames = (size + CHUNK - 1) // CHUNK
        padded = image.ljust(frames * CHUNK, b"\xff")
        for n in range(frames):
            # A resend whose first copy also landed stages a frame too many. The next frame's
            # counter catches that; nothing would after the last, so it goes once.
            last = n == frames - 1
            reply = self.dev.request(
                self._feature.index, FN_WRITE, padded[n * CHUNK:(n + 1) * CHUNK], long=True,
                timeout=WRITE_ONCE_TIMEOUT if last else REQUEST_TIMEOUT,
                retries=0 if last else REQUEST_RETRIES,
            )
            counter = int.from_bytes(reply[0:2], "big")
            if counter != n + 1:
                raise OnboardError(
                    f"write frame {n + 1} of {frames} acknowledged with counter "
                    f"{counter}, so the write stopped before its commit; try it again"
                )
        self.dev.request(self._feature.index, FN_WRITE_END, b"\x00\x00\x00", long=True)
        time.sleep(0.05)

        back = self.read_sector(sector)
        if back != image:
            # read_sector returns `size` bytes, as many as image has, so an index differs.
            first = next(i for i in range(size) if back[i] != image[i])
            raise OnboardError(
                f"sector 0x{sector:04x} read back differently from what was written, "
                f"first at offset 0x{first:02x}; {self._after_failed_write(sector, back)}"
            )
        log.info("wrote sector 0x%04x, %d bytes, CRC 0x%04x verified by read-back",
                 sector, size, int.from_bytes(image[-2:], "big"))
        return back

    @staticmethod
    def _after_failed_write(sector: int, back: bytes) -> str:
        """What to do about a sector that did not read back as written. What the firmware does
        at power-on with a sector whose CRC fails is not measured, so the advice is to put a
        good one back first."""
        if sector != FIRST_PROFILE_SECTOR:
            # mouse-restore writes profile 1 only, so it cannot put this sector back, and a
            # directory whose CRC verifies can still disable profile 1.
            if sector_crc_ok(back):
                return ("it still holds a valid image, not the one written; write it "
                        "again before switching the mouse off")
            return (f"it now fails its CRC; write a good backup of sector 0x{sector:04x} "
                    f"back before switching the mouse off (see RECOVERY in gearbench-ctl(1))")
        if sector_crc_ok(back):
            return ("it still holds a valid profile, not the one written; nothing "
                    "needs restoring, so try the write again")
        return ("it now fails its CRC; run `gearbench-ctl mouse-restore` before switching "
                "the mouse off (see RECOVERY in gearbench-ctl(1))")

    # -- backups -------------------------------------------------------------

    @staticmethod
    def backup(device: str, sector: int, image: bytes, directory: Path | None = None) -> Path:
        """Write one sector image to a new file, never over an existing one: `.bin` when its CRC
        verifies, else `.bad-read`, kept as evidence where no restore picks it."""
        target_dir = directory or backup_dir()
        suffix = ".bin" if sector_crc_ok(image) else ".bad-read"
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        stem = f"{device}-sector-{sector:04X}-{stamp}"
        path = target_dir / f"{stem}{suffix}"
        n = 1
        # Nor a name set aside (UNCHANGED): setting this one aside too would rename over it.
        while path.exists() or path.with_name(path.name + UNCHANGED).exists():
            path = target_dir / f"{stem}-{n}{suffix}"
            n += 1
        # On disk before the flash write it protects. Written straight to its final name, a full
        # disk would leave a truncated .bin and a power loss a zero-length one, and newest_backup
        # picks a .bin by name.
        replace_synced(path, image, ".backup-", ".tmp")
        return path

    def backup_all(self, device: str, directory: Path | None = None) -> dict[int, bytes]:
        """Back up the directory, which says which profiles are enabled, and every profile
        sector; returns what was read, by sector."""
        self.backed_up = {}
        for sector in (DIRECTORY_SECTOR, *self.profile_sectors):
            image = self.read_sector(sector)
            path = self.backup(device, sector, image, directory)
            self.backed_up[sector] = (path, image)
            log.info("backed up sector 0x%04x to %s (%s)", sector, path,
                     crc_words(sector_crc_ok(image)))
        return {sector: image for sector, (_, image) in self.backed_up.items()}

    @staticmethod
    def newest_backup(device: str, sector: int, directory: Path | None = None) -> Path | None:
        """The most recent `.bin` backup of one sector, or None where there is none or no
        folder. `.bad-read` files and backups set aside (UNCHANGED) are never candidates, nor
        is a link whose target is gone. A folder that cannot be listed, or that is behind a link
        to nothing, is a ConfigError: it is not one without backups."""
        target_dir = directory or backup_dir()
        prefix = f"{device}-sector-{sector:04X}-"
        try:
            # Not Path.glob, which takes a folder it cannot list for an empty one.
            with os.scandir(target_dir) as entries:
                paths = [target_dir / entry.name for entry in entries
                         if entry.name.startswith(prefix) and entry.name.endswith(".bin")]
        except FileNotFoundError:
            if (place := link_to_nothing(target_dir)) is None:
                return None
            raise ConfigError(missing_behind(target_dir, place)) from None
        except OSError as exc:
            raise ConfigError(f"{target_dir} could not be listed ({reason(exc)})") from None
        found = []
        for path in paths:
            # On equal mtimes, as in a copy that kept whole seconds or no times, the later stamp
            # wins, then the later of its second's backups, which backup() numbers -1, -2 ...
            stamp, _, n = path.name[len(prefix):-len(".bin")].partition("-")
            try:
                found.append((path.stat().st_mtime_ns, stamp, int(n) if n.isdecimal() else 0,
                              path.name, path))
            except OSError as exc:
                # Anyone who can name a file in the folder chooses this name.
                log.warning("skipping backup %s: %s", escaped(path), reason(exc))
        return max(found)[-1] if found else None

    @staticmethod
    def set_aside(path: Path) -> Path | None:
        """Rename a `.bin` backup to `<name>.unchanged` (UNCHANGED), which newest_backup never
        picks; backup gives no new file the name it had. Returns the new path, or None for a
        file that is no `.bin`, left as it is: a `.bad-read`, which no restore picks. Raises
        OSError."""
        if path.suffix != ".bin":
            return None
        aside = path.with_name(path.name + UNCHANGED)
        path.rename(aside)
        return aside
