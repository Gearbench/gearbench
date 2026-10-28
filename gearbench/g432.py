"""The Logitech headsets that speak HID++ 2.0 on a HID interface of their own, the G432 and
its family (MODELS): each model's ids and what Gearbench drives on it, and their channel,
`G432`, with the requests it sends them (SENT). Nothing here imports the daemon.

Each is a USB Audio Class device, a wireless one through its dongle, with one HID interface,
03, which carries the volume wheel's and the mute's reports beside HID++. The G432's report
descriptor holds HID++'s long report, 0x11, alone, on Logitech's page 0xFF43 (read off the
hardware, 2026-10-17), and HeadsetControl finds the G633 family's HID++ on the same page and
usage, so every request goes out long (`Hidpp.long_only`).

The channel reads the feature table (0x0001 FeatureSet) first and looks up only a feature it
lists: the G432's adapter answers a root lookup of a feature it lacks with an error where
most firmware answers index 0 (the device report, 2026-10-18). An index is the one the
headset reports, never the one another program sends to (hidpp.py), and a feature's version
is the root lookup's, since a version 0 FeatureSet gives none.

G HUB's device list gives the G432 no onboard memory, yet the adapter kept its sidetone
level through a replug (the hardware, 2026-10-17): a reset writes back the level the
daemon first read, or SIDETONE_UNREAD_RESET where it read none before a write
(daemon/g432/).
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .battery import (
    F_ADC,
    G533_CELL,
    G535_CELL,
    G733_0AB5_CELL,
    G733_0AFE_CELL,
    G733_0B1F_CELL,
    G933_G935_CELL,
    PRO_X_WIRELESS_CELL,
    CellCurves,
    adc,
)
from .config import G633_G_KEYS, G635_G_KEYS, G933_G_KEYS, G935_G_KEYS
from .hidpp import (
    DEVICE_INDEX,
    F_DEVICE_INFO,
    FN_DEVICE_INFO,
    FN_FW_INFO,
    PROBE_RETRIES,
    PROBE_TIMEOUT,
    REQUEST_TIMEOUT,
    ROOT_FEATURE,
    Feature,
    Hidpp,
    HidppError,
    UnreadableAnswer,
    feature_of,
    find_attached,
    firmware_entity,
    running_application,
)
from .keys import F_GKEY, GKEY_SET_DIVERT, KeyEvent, key_changes
from .wording import NOT_READ, counted, whole, word_list, written

#: Its HID interface, the one each model's udev rule grants: interface 3, the one HID
#: interface in public hardware probes' lsusb of the G933, G935, G733 (each of its ids), G535
#: and PRO X Wireless, and Solaar's interface for the G533 too; assumed on the G431, G433,
#: G633 and G635, whose probes give no lsusb.
INTERFACE = 3
#: A wireless model's `plugged_by`: the receiver that stays plugged in while the headset is
#: switched off.
BY_RECEIVER = "receiver"


@dataclass(frozen=True)
class HeadsetModel:
    """One model of the family; `key` is its name in the settings and the equalizer's, and
    `cells` the cell of each of its ids with a battery."""

    key: str
    name: str
    products: tuple[int, ...]
    #: Run on this hardware: a model not yet is asked for a device report (report.untested).
    tested: bool = False
    #: G HUB's hasOnboardEq: the equalizer is 0x8310 in the headset, else the window's
    #: PipeWire chain stands in (the owner's decision of 2026-10-21, "In the headset, no cut").
    onboard_eq: bool = False
    cells: Mapping[int, CellCurves] = field(default_factory=dict)
    #: What plugs into the computer: the wired ones' USB adapter or cable, the wireless ones'
    #: receiver.
    plugged_by: str = BY_RECEIVER
    #: The names its G keys bind under, G1's first, a bit each of 0x8010's events from bit 0:
    #: the G933's three, which the Windows captures of 2026-10-25 read, and the G935's,
    #: G633's and G635's three, taken to be the G933's, untested (the owner's decision of
    #: 2026-10-25): Logitech's setup guides give the G935 and G635 a programmable G1, G2 and
    #: G3, and the G633 shares the G933's G-key layout, but no source names their feature.
    #: Empty on a model whose count no source gives, though G HUB's device list gives G keys
    #: to more of the family.
    g_keys: tuple[str, ...] = ()

    @property
    def short(self) -> str:
        """The name without the maker's: "G432" for "Logitech G432"."""
        return self.name.removeprefix("Logitech ")


#: Each model, by G HUB's device list (`ghub-extract/devicedb`, build 869589): its ids, its
#: HID interface's usage and its flags; the wireless ones' cells from their G HUB device
#: packages (battery.py). The G431 and the G433 take the G432's sidetone in HeadsetControl's
#: driver for the three; the G933s shares the G935's id and the G633s the G635's.
MODELS = (
    HeadsetModel("g432", "Logitech G432", (0x0A9C,), tested=True, plugged_by="USB adapter"),
    HeadsetModel("g431", "Logitech G431", (0x0A9B,), plugged_by="USB adapter"),
    HeadsetModel("g433", "Logitech G433", (0x0A6D,), plugged_by="USB adapter"),
    HeadsetModel("g533", "Logitech G533", (0x0A66,), cells={0x0A66: G533_CELL}),
    HeadsetModel("g535", "Logitech G535", (0x0AC4,), cells={0x0AC4: G535_CELL}),
    HeadsetModel("g633", "Logitech G633", (0x0A5C,), onboard_eq=True, plugged_by="cable",
                 g_keys=G633_G_KEYS),
    HeadsetModel("g635", "Logitech G635", (0x0A89,), plugged_by="cable", g_keys=G635_G_KEYS),
    HeadsetModel("g733", "Logitech G733", (0x0AB5, 0x0AFE, 0x0B1F),
                 cells={0x0AB5: G733_0AB5_CELL, 0x0AFE: G733_0AFE_CELL,
                        0x0B1F: G733_0B1F_CELL}),
    HeadsetModel("g933", "Logitech G933", (0x0A5B,), onboard_eq=True, tested=True,
                 cells={0x0A5B: G933_G935_CELL}, g_keys=G933_G_KEYS),
    HeadsetModel("g935", "Logitech G935", (0x0A87,), onboard_eq=True,
                 cells={0x0A87: G933_G935_CELL}, g_keys=G935_G_KEYS),
    HeadsetModel("pro_x_wireless", "Logitech PRO X Wireless", (0x0ABA,),
                 cells={0x0ABA: PRO_X_WIRELESS_CELL}),
)
#: The family in the words a description that names the A50 beside it uses, in --help's
#: and the man page's NAME line.
FAMILY = "the G432 and other Logitech G headsets"
#: Each model by its product ids, its name as the window, the tray, the notices and the CLI
#: give it, without the maker's (`short`), and its cell by them. PipeWire's nodes keep
#: `name`, as in "Logitech G432 Equalizer".
MODEL_OF = {product: model for model in MODELS for product in model.products}
PRODUCTS = {product: model.short for product, model in MODEL_OF.items()}
CELLS = {product: cell for model in MODELS for product, cell in model.cells.items()}
#: Each model by its key.
MODEL_KEYED = {model.key: model for model in MODELS}
#: The G432, the model taken where none is named.
G432_MODEL = MODEL_KEYED["g432"]

SIDETONE = 0x8300
FN_GET_SIDETONE = 0
FN_SET_SIDETONE = 1
#: What sidetone is, in the window's words and the CLI's: the owner read the window's
#: "Level" row as the headset's volume (2026-10-17).
SIDETONE_MEANS = "your voice in the earcups"
#: The level the feature takes: x8300 v1's setSidetoneLevel (0 to 100), as Solaar's
#: Sidetone setting takes it.
SIDETONE_RANGE = (0, 100)
#: What a reset writes where no read found a level before the first write.
SIDETONE_UNREAD_RESET = 0

#: 0x0001 FeatureSet's getCount, the features past the root, and getFeatureID, one index's
#: id (x0001 v2; Solaar's hidpp20.py, FeaturesArray).
F_FEATURE_SET = 0x0001
FN_COUNT, FN_FEATURE_ID = 0, 1
#: The most rows the table walk reads, whatever getCount says: the family lists 6 to 9
#: (Solaar's listings), and a byte the device gets wrong cannot lengthen the walk.
FEATURES_MAX = 32
#: 0x1F20's getADCMeasurement (battery.F_ADC), and its power-off time, in minutes with 0 for
#: never, which Solaar's ADCPower setting reads at function 1 from version 2 on, and which
#: function 2 sets, echoed, as G HUB sets a G933's (the Windows captures of 2026-10-25).
FN_ADC = 0
FN_POWER_OFF = 1
FN_SET_POWER_OFF = 2
#: The broadcast 0x1F20 sends as its reading changes, event 0, in getADCMeasurement's layout
#: (the kernel's EVENT_ADC_MEASUREMENT_STATUS_BROADCAST).
EVENT_ADC = 0
#: 0x8010's event 0, the G keys held as a bitmask from bit 0, G1's, `00` once each is let go
#: (keys.py; a G933's `01`, `02` and `04`, the Windows captures of 2026-10-25), and the byte
#: of fn 2, enableSoftwareControl, that turns those events on and off: G HUB sends a G933
#: `01` at each connect, answered with its echo, and `00` as it lets one go.
EVENT_KEYS = 0
KEYS_ON, KEYS_OFF = 0x01, 0x00

#: G HUB's headset bands, an octave apart (its app.min.js labels them 32 to 16K): those of the
#: G432's equalizer on the computer (g432_presets), and those assumed for the equalizer in the
#: G933, G935 and G633, which each lists of its own (getFrequencies).
BAND_FREQS = (32, 64, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)
#: 0x8310, the equalizer in the headset (Logitech's x8310 v2): getEqInfo, getFrequencies
#: from a band, getFrequencyGains at a location, and setFrequencyGains with a persistence.
F_EQUALIZER = 0x8310
FN_EQ_INFO, FN_FREQUENCIES, FN_GAINS, FN_SET_GAINS = 0, 1, 2, 3
#: getFrequencyGains' locations: 0 the EEPROM's gains, and 1 the RAM's, which version 1
#: added. A G933's gave the same gains before and after writes it played, no location gave
#: the gains of a RAM write, and it played flat after a power-on, not the gains it keeps
#: (measured 2026-10-25, on Linux and on Windows, the last by ear), so the daemon shows the
#: gains it sent in their place, and flat before any.
EQ_SAVED, EQ_ACTIVE = 0, 1
RAM_GAINS_SINCE = 1
#: setFrequencyGains' persistence 0, volatile (RAM) alone: the only one `G432.request`
#: sends; 1 and 2 write the EEPROM.
EQ_RAM = 0
#: getFrequencies' bands a reply carries (x8310 v2), and the most bands Gearbench takes: a
#: long report carries the persistence byte and 15 gains.
BANDS_A_REPLY = 7
EQ_BANDS_MAX = 15
POWER_OFF_SINCE = 2
#: getEqInfo's reply up to its signed maximum, the bytes eq_range reads (x8310 v2).
EQ_INFO_LENGTH = 5
#: What a gain's signed byte carries, in dB: eq_range cuts a range a headset gives to it.
GAIN_MIN, GAIN_MAX = -0x80, 0x7F

#: Every (feature id, function) `G432` sends a headset; its `request` refuses any other
#: before anything is written. Each is a read but 0x8300's, 0x8310's and 0x1F20's writes and
#: 0x8010's, which the headset loses at a power-off, as G HUB sends it again each time:
#: 0x0001's and 0x0003's in Logitech's x0001 v2 and x0003 v4 specifications and Solaar's
#: hidpp20.py (FeaturesArray, get_firmware), the G432's adapter answering both (read
#: 2026-10-17 and 2026-10-18); 0x1F20's reads in the kernel's hid-logitech-hidpp.c
#: (CMD_ADC_MEASUREMENT_GET_ADC_MEASUREMENT) and Solaar's settings_templates.py (ADCPower's
#: read), and its write as G HUB sends it.
SENT = frozenset({
    (ROOT_FEATURE, 0), (F_FEATURE_SET, FN_COUNT), (F_FEATURE_SET, FN_FEATURE_ID),
    (SIDETONE, FN_GET_SIDETONE), (SIDETONE, FN_SET_SIDETONE),
    (F_DEVICE_INFO, FN_DEVICE_INFO), (F_DEVICE_INFO, FN_FW_INFO),
    (F_ADC, FN_ADC), (F_ADC, FN_POWER_OFF), (F_ADC, FN_SET_POWER_OFF),
    (F_EQUALIZER, FN_EQ_INFO), (F_EQUALIZER, FN_FREQUENCIES), (F_EQUALIZER, FN_GAINS),
    (F_EQUALIZER, FN_SET_GAINS), (F_GKEY, GKEY_SET_DIVERT)})
#: The writes of SENT the headset keeps through a power-off, or is taken to: the power-off
#: time, which G HUB never sent again at a reconnect (the Windows captures of 2026-10-25).
#: `request` refuses each on a model not tested on this hardware (kept_refusal).
KEPT = frozenset({(F_ADC, FN_SET_POWER_OFF)})
#: The features `find_features` looks up once the table lists them, for their index and
#: version.
LOOKED_UP = (F_DEVICE_INFO, SIDETONE, F_ADC)


def find_g432() -> tuple[Path, int] | None:
    """A headset of the family's hidraw node and product id, read from sysfs by the vid, pid
    and interface its udev rule matches, the models in MODELS' order; opens nothing."""
    found = find_attached(product=tuple(MODEL_OF), interface=INTERFACE)
    return found[:2] if found else None


def open_g432(path: str | os.PathLike[str], product: int) -> G432:
    """Open the node and wrap it for `product`'s model; raises PermissionError without the
    rule."""
    return G432(Hidpp(path, DEVICE_INDEX, long_only=True), product)


#: Each looked-up feature's words in not_listed: what it is, and what Gearbench cannot do.
LISTED_WORDS = {
    SIDETONE: ("sidetone", "read or set it"),
    F_DEVICE_INFO: ("its device information", "read its firmware version"),
    F_ADC: ("its battery", "read its battery or its power-off time"),
    F_EQUALIZER: ("its equalizer", "read or set it"),
    F_GKEY: ("its G keys", "hear them"),
}
#: Why an equalizer request is refused on a model G HUB gives none of its own, whose
#: equalizer the window runs in PipeWire, and a write on a version 0 0x8310, which has no RAM
#: gains to read back and may keep what it is sent; each with the model's name.
NO_HEADSET_EQ = "the {}'s equalizer runs on the computer; `gearbench-ctl g432-eq` sets it"
EQ_UNTESTED = ("Gearbench does not write the {}'s equalizer until that is tested on this model: "
               "it may keep the gains it is sent")
#: Why a write the headset keeps (KEPT, and its lights' in headset_lights) is refused on a
#: model not tested on this hardware, the owner's rule of 2026-10-17: its name goes in.
KEPT_UNTESTED = ("Gearbench does not write the {}'s memory until that is tested on this "
                 "model: what it is sent there stays through a power-off")
#: Why a gain list is refused: the band count, then the range's ends.
NOT_GAINS = "the equalizer takes {}, each a whole number of dB from {} to {}"

#: Why a sidetone level is refused, SIDETONE_RANGE's ends put in.
NOT_A_LEVEL = "the sidetone level is a whole number from {} to {}, not {}"


@dataclass(frozen=True)
class Equalizer:
    """0x8310 as the headset gave it: each band's frequency in Hz, the gains' range in dB,
    and the gains at each location, the active ones None from a version 0 feature or one
    that refused them; and why a write is refused, None for none."""

    frequencies: tuple[int, ...]
    low: int
    high: int
    saved: tuple[int, ...]
    active: tuple[int, ...] | None
    refused: str | None


def signed_byte(byte: int) -> int:
    return byte - 0x100 if byte > 0x7F else byte


def eq_frequencies(read: Callable[[bytes], bytes], bands: int) -> list[int]:
    """`bands` frequencies from getFrequencies, `read` asking it from a band, BANDS_A_REPLY a
    reply after the band's own byte (x8310 v2). UnreadableAnswer for a reply too short to
    hold the frequencies it was asked for."""
    frequencies: list[int] = []
    for start in range(0, bands, BANDS_A_REPLY):
        reply = read(bytes([start]))
        taken = min(BANDS_A_REPLY, bands - start)
        # A short report's three bytes, from a device that declares one.
        if len(reply) < 1 + 2 * taken:
            raise UnreadableAnswer(f"the equalizer answered its frequencies from band {start} "
                                   f"with {counted(len(reply), 'byte')}, not {1 + 2 * taken}")
        frequencies += [int.from_bytes(reply[1 + 2 * i:3 + 2 * i], "big") for i in range(taken)]
    return frequencies


def eq_range(info: bytes) -> tuple[int, int]:
    """getEqInfo's range in dB: its signed minimum and maximum, or for either one 0 its
    dbRange, below and above (x8310 v2), within GAIN_MIN to GAIN_MAX, since a dbRange past
    127 gives ends a gain's byte cannot carry."""
    low, high = signed_byte(info[3]), signed_byte(info[4])
    return max(low or -info[1], GAIN_MIN), min(high or info[1], GAIN_MAX)


#: The label of the minutes of silence a wireless one powers off after, in the text output
#: of `headset`, `g432-sidetone` and `g432-auto-off`: the window's row, Auto shut-off.
AUTO_OFF_LABEL = "auto shut-off"
#: The power-off times Gearbench sets, in minutes, 0 for never: those G HUB offers a G933,
#: each sent at 0x1F20's function 2 and read back (the Windows captures of 2026-10-25; the
#: owner's answer of 2026-10-25).
POWER_OFF_CHOICES = (0, 5, 15, 30, 60)
#: POWER_OFF_CHOICES in words but 0, which each sentence words as never on its own.
POWER_OFF_OFFERED = word_list([str(minutes) for minutes in POWER_OFF_CHOICES[1:]])
#: Why a power-off time is refused: the choices, then the value.
NOT_A_POWER_OFF = "the auto shut-off is {} minutes, or 0 for never, not {}"
#: Why it is refused on a headset whose 0x1F20 is older than POWER_OFF_SINCE: the model's
#: name and the version.
NO_POWER_OFF = ("the {} has no auto shut-off Gearbench sets: its HID++ feature 0x1F20 is "
                "version {}, and the time is read and set from version 2 on")


def auto_off_words(minutes: object) -> str:
    """A headset's auto shut-off time as the CLI and the journal give it, in the words of the
    window's entries, lowercase: so many minutes, never for 0, or not read for none."""
    if minutes == 0:
        return "never"
    if whole(minutes):
        return counted(minutes, "minute")
    return NOT_READ


def power_off_minutes(value: object) -> int:
    """A power-off time of POWER_OFF_CHOICES, or ValueError saying what it takes."""
    if not whole(value) or value not in POWER_OFF_CHOICES:
        raise ValueError(NOT_A_POWER_OFF.format(POWER_OFF_OFFERED, written(value)))
    return value


def sidetone_level(value: object) -> int:
    """A sidetone level the feature takes, or ValueError saying what it takes."""
    low, high = SIDETONE_RANGE
    if not whole(value) or not low <= value <= high:
        raise ValueError(NOT_A_LEVEL.format(low, high, written(value)))
    return value


def kept_refusal(model: HeadsetModel) -> str | None:
    """Why `model` is sent no write it keeps: KEPT_UNTESTED for a model not tested on this
    hardware, else None."""
    return None if model.tested else KEPT_UNTESTED.format(model.short)


def not_listed(model: HeadsetModel, feature_id: int) -> str:
    """The refusal of a request to `feature_id`, a key of LISTED_WORDS, `model` did not list."""
    what, cannot = LISTED_WORDS[feature_id]
    return (f"the {model.short} did not list {what} (feature 0x{feature_id:04x}), so "
            f"Gearbench cannot {cannot}")


class G432:
    """A headset's HID++ channel, on a cabled device's index, and the requests in SENT."""

    def __init__(self, dev: Hidpp, product: int) -> None:
        self.dev = dev
        self.model = MODEL_OF[product]
        self.product = product
        #: The ids the feature table lists, by index, and by id each feature the root
        #: lookups of `find_features` found; empty before it.
        self.table: dict[int, int] = {}
        self.features: dict[int, Feature] = {}
        #: The G keys held, as the last of 0x8010's events gave them.
        self._keys_held = 0

    @property
    def fileno(self) -> int:
        return self.dev.fileno

    @property
    def pending(self) -> bool:
        """Whether notifications a request read while it waited are queued, of any feature
        (Hidpp.pending)."""
        return self.dev.pending

    @property
    def cell(self) -> CellCurves | None:
        return self.model.cells.get(self.product)

    def close(self) -> None:
        self.dev.close()

    def has(self, feature_id: int) -> bool:
        return feature_id in self.features

    def pump(self) -> list[dict | KeyEvent]:
        """Read what is queued: the volume wheel's and the mute's reports share the node and
        are dropped, and the rest, a request's included, is what `queued` gives."""
        self.dev.pump()
        return self.queued()

    def queued(self) -> list[dict | KeyEvent]:
        """What a request queued, the newest last, without reading the node, since one read
        now could come after the request's answer: each 0x1F20 broadcast's battery reading,
        and each G key going down or up by 0x8010's events, which `find_features` looks up
        on a model with G keys alone. Another feature's event is dropped."""
        found: list[dict | KeyEvent] = []
        battery, keys = self.features.get(F_ADC), self.features.get(F_GKEY)
        while (event := self.dev.next_event()) is not None:
            if (battery is not None and event.feature_index == battery.index
                    and event.address >> 4 == EVENT_ADC):
                found.append(adc(event.data, self.cell))
            elif (keys is not None and event.feature_index == keys.index
                  and event.address >> 4 == EVENT_KEYS):
                held = int.from_bytes(event.data[0:4], "little")
                found += key_changes(self.model.g_keys, self._keys_held, held)
                self._keys_held = held
        return found

    def request(self, feature_id: int, function: int, params: bytes = b"", *,
                probe: bool = False) -> bytes:
        """One request of SENT, to the root's index 0 or to the index its root lookup gave,
        on the probe's budget with `probe`; ValueError for any other, for a write the headset
        keeps on a model not tested (kept_refusal), for a feature `find_features` did not
        find, or for setFrequencyGains with a persistence other than RAM, before anything is
        written. Raises HidppFailure."""
        if (feature_id, function) not in SENT:
            raise ValueError(f"0x{feature_id:04x} function {function} is not a request "
                             f"Gearbench sends the {self.model.short}")
        if (feature_id, function) in KEPT and (refused := kept_refusal(self.model)) is not None:
            raise ValueError(refused)
        if (feature_id, function) == (F_EQUALIZER, FN_SET_GAINS) and params[:1] != bytes(
                [EQ_RAM]):
            raise ValueError(f"the {self.model.short}'s equalizer is sent its RAM gains alone")
        index = 0 if feature_id == ROOT_FEATURE else self._index(feature_id)
        if probe:
            return self.dev.request(index, function, params, timeout=PROBE_TIMEOUT,
                                    retries=PROBE_RETRIES)
        return self.dev.request(index, function, params, timeout=REQUEST_TIMEOUT)

    def _index(self, feature_id: int) -> int:
        """The index the root lookup found for a feature of SENT; ValueError for one
        `find_features` did not find."""
        if feature_id not in self.features:
            raise ValueError(not_listed(self.model, feature_id))
        return self.features[feature_id].index

    def find_features(self) -> dict[int, Feature]:
        """The root lookup of 0x0001, on the probe's budget, since it is the first request
        after the node opens; then the table, FEATURES_MAX rows at most, and the root lookup
        of each feature of LOOKED_UP it lists. None of the lookups is of a feature the table
        does not list, 0x8310 only on a model with an equalizer of its own and 0x8010 only
        on one with G keys (`HeadsetModel.g_keys`). Kept as `table` and `features`; raises
        HidppFailure, and UnreadableAnswer for a headset with no 0x0001."""
        found = feature_of(self.request(ROOT_FEATURE, 0, F_FEATURE_SET.to_bytes(2, "big"),
                                        probe=True))
        if found is None:
            raise UnreadableAnswer("it has no feature table, HID++ feature 0x0001")
        self.features = {F_FEATURE_SET: found}
        count = self.request(F_FEATURE_SET, FN_COUNT)[0]
        self.table = {index: int.from_bytes(
            self.request(F_FEATURE_SET, FN_FEATURE_ID, bytes([index]))[0:2], "big")
            for index in range(1, min(count, FEATURES_MAX) + 1)}
        onboard = (F_EQUALIZER,) if self.model.onboard_eq else ()
        keys = (F_GKEY,) if self.model.g_keys else ()
        for feature_id in (*LOOKED_UP, *onboard, *keys):
            if (feature := self.lookup(feature_id)) is not None:
                self.features[feature_id] = feature
        return self.features

    def lookup(self, feature_id: int) -> Feature | None:
        """The root lookup of a feature the table lists, or None, with nothing sent, for one
        it does not list, and None for one whose lookup finds none. Raises HidppFailure."""
        if feature_id not in self.table.values():
            return None
        return feature_of(self.request(ROOT_FEATURE, 0, feature_id.to_bytes(2, "big")))

    def firmware(self) -> str | None:
        """The firmware that runs, its name and version as getFwInfo gives them, U1
        42.00.B0001 on the G432 read on 2026-10-17: the active main application of the
        entities getDeviceInfo counts (hidpp.running_application). None where the headset
        lists no 0x0003 or no such entity. Raises HidppFailure, UnreadableAnswer for an entry
        too short to decode."""
        if not self.has(F_DEVICE_INFO):
            return None
        count = self.request(F_DEVICE_INFO, FN_DEVICE_INFO)[0]
        running = running_application(
            lambda entity: self.request(F_DEVICE_INFO, FN_FW_INFO, bytes([entity])), count,
            f"the {self.model.short}")
        if running is None:
            return None
        found = firmware_entity(running[1])
        return f"{found.prefix} {found.version}"

    def sidetone(self) -> int:
        """The level the headset reports, byte 0 of function 0's reply, where Solaar's
        RangeValidator reads it. UnreadableAnswer for one outside SIDETONE_RANGE."""
        level = self.request(SIDETONE, FN_GET_SIDETONE)[0]
        low, high = SIDETONE_RANGE
        if not low <= level <= high:
            raise UnreadableAnswer(f"the {self.model.short} reported a sidetone level of "
                                   f"{level}, outside {low} to {high}")
        return level

    def set_sidetone(self, level: int) -> None:
        """Write `level`, checked first, as function 1's one byte."""
        self.request(SIDETONE, FN_SET_SIDETONE, bytes([sidetone_level(level)]))

    def divert_keys(self, on: bool) -> None:
        """The G keys' events on (KEYS_ON) or off (KEYS_OFF), at 0x8010's function 2, the
        keys held forgotten: off, the headset has them, and on, after a switch-off too, none
        is held. The headset loses it at a power-off. Raises HidppFailure, and ValueError for
        a headset `find_features` found no 0x8010 on."""
        self._keys_held = 0
        self.request(F_GKEY, GKEY_SET_DIVERT, bytes([KEYS_ON if on else KEYS_OFF]))

    def battery(self) -> dict:
        """getADCMeasurement in battery.read's shape, with the model's cell for this id.
        Raises HidppFailure, and ValueError for a headset that lists no 0x1F20."""
        return adc(self.request(F_ADC, FN_ADC), self.cell)

    def equalizer(self) -> Equalizer:
        """0x8310's bands, EQ_BANDS_MAX at most, their range and their gains at each
        location, signed dB a byte from byte 0 as the spec's example and Solaar's Equalizer
        read them. The active gains are asked from RAM_GAINS_SINCE on, and a refusal of them
        is no gains; they are what the headset gives, which is not always what it plays
        (EQ_ACTIVE). Raises HidppFailure, and ValueError for a headset whose table lists no
        0x8310 or a model with no equalizer of its own; UnreadableAnswer for an info, a
        frequencies or a gains reply too short to decode."""
        info = self.request(F_EQUALIZER, FN_EQ_INFO)
        # A short report's three bytes, from a device that declares one.
        if len(info) < EQ_INFO_LENGTH:
            raise UnreadableAnswer(f"the {self.model.short} answered the equalizer's info "
                                   f"with {counted(len(info), 'byte')}, not {EQ_INFO_LENGTH}")
        bands = min(info[0], EQ_BANDS_MAX)
        frequencies = eq_frequencies(
            lambda params: self.request(F_EQUALIZER, FN_FREQUENCIES, params), bands)
        low, high = eq_range(info)
        saved = self._gains(EQ_SAVED, bands)
        recent = self.features[F_EQUALIZER].version >= RAM_GAINS_SINCE
        active = None
        if recent:
            with contextlib.suppress(HidppError):
                active = self._gains(EQ_ACTIVE, bands)
        return Equalizer(tuple(frequencies), low, high, saved, active,
                         None if recent else EQ_UNTESTED.format(self.model.short))

    def _gains(self, location: int, bands: int) -> tuple[int, ...]:
        reply = self.request(F_EQUALIZER, FN_GAINS, bytes([location]))
        if len(reply) < bands:
            raise UnreadableAnswer(f"the {self.model.short} answered "
                                   f"{counted(len(reply), 'gain')} for {counted(bands, 'band')}")
        return tuple(signed_byte(byte) for byte in reply[:bands])

    def set_gains(self, eq: Equalizer, gains: object) -> None:
        """Write `gains`, one whole dB a band in `eq`'s range, to the RAM alone (EQ_RAM);
        ValueError, with nothing sent, for gains of another count or range, or for an `eq`
        whose write is refused. Raises HidppFailure."""
        if eq.refused is not None:
            raise ValueError(eq.refused)
        count = len(eq.frequencies)
        if (not isinstance(gains, list | tuple) or len(gains) != count
                or not all(whole(gain) and eq.low <= gain <= eq.high for gain in gains)):
            raise ValueError(NOT_GAINS.format(counted(count, "gain"), eq.low, eq.high))
        self.request(F_EQUALIZER, FN_SET_GAINS,
                     bytes([EQ_RAM, *(gain & 0xFF for gain in gains)]))

    def power_off(self) -> int | None:
        """The minutes of silence the headset powers off after, 0 for never, at 0x1F20's
        function 1 from POWER_OFF_SINCE on; None from an older 0x1F20, whose function 1
        Logitech's documentation of version 1 does not give (Solaar's ADCPower). Raises
        HidppFailure, and ValueError for a headset that lists no 0x1F20."""
        feature = self.features.get(F_ADC)
        if feature is not None and feature.version < POWER_OFF_SINCE:
            return None
        return self.request(F_ADC, FN_POWER_OFF)[0]

    def set_power_off(self, minutes: object) -> int | None:
        """Write `minutes`, checked first (power_off_minutes), at 0x1F20's function 2, then
        read it back (power_off): what the headset now gives. ValueError, with nothing sent,
        for a time not offered, a headset that lists no 0x1F20 or one older than
        POWER_OFF_SINCE (NO_POWER_OFF), or a model not tested (kept_refusal). Raises
        HidppFailure."""
        checked = power_off_minutes(minutes)
        feature = self.features.get(F_ADC)
        if feature is not None and feature.version < POWER_OFF_SINCE:
            raise ValueError(NO_POWER_OFF.format(self.model.short, feature.version))
        self.request(F_ADC, FN_SET_POWER_OFF, bytes([checked]))
        return self.power_off()
