"""The device report: what every Logitech HID++ device attached over USB says about itself,
for an owner of any model to post, so that a model nobody here holds can be supported and
its faults fixed (`gearbench-ctl report`). Where it is posted, REPORT_FORM, and which
attached models it is asked of, those not yet run on (`untested`, `untested_note`), are
here too.

It reads and never writes, by construction. READS lists each (feature id, function) the
report may send, with the function's name and two sources that call it a read, or for
0x1B10's two the one there is; `ReadOnly`, the one channel the report reaches a device
through, refuses any other request before a byte is written (NotARead), its burst of reads
sent back to back included, and offers no other send and no register function. A request
goes only to an index a root lookup named: an index is what one firmware reports, never
taken from another table.

What it reads, a node at a time (`gather`): from sysfs, with nothing opened, the ids, the
interface, the driver and the report descriptor (`describe`, which tells a HID++
collection, a long-only one and key reports); then, on a node with a HID++ collection that
this account may open, the device index and read_device's sections, every read with its
reply. A node the daemon holds gets what the daemon itself says of it instead. Sent
nothing are that node, every other on its USB device, a receiver's own node with devices
paired to it as nodes of their own, a node sysfs no longer names as the device it listed,
and, while a daemon runs or may, a node of a model this build drives, since the daemon may
open it too.

Nothing that names the unit or its owner is read: no serial number, no friendly name, no
host names, no HID_PHYS or HID_UNIQ, no user sector of the onboard memory, which can hold
macro text. The unit id that getDeviceInfo carries is zeroed as it is read.
"""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, cast

from . import battery
from .a50x import MODELS as A50X_OF
from .g432 import (
    CELLS as HEADSET_CELLS,
    F_EQUALIZER,
    F_FEATURE_SET,
    MODEL_OF as HEADSET_OF,
    SIDETONE as F_SIDETONE,
    eq_frequencies,
    signed_byte,
)
from .hidpp import (
    CAPABILITIES_SINCE,
    DEVICE_INDEX,
    ENTITIES_MAX,
    EXTENDED_MODEL_SINCE,
    F_DEVICE_INFO,
    FN_FW_INFO,
    MODEL_IDS_SINCE,
    RECEIVER_INDEX,
    REPORT_LONG,
    REPORT_SHORT,
    REQUEST_RETRIES,
    REQUEST_TIMEOUT,
    ROOT_FEATURE,
    SET_REGISTER,
    UNIT_ID,
    DeviceGone,
    Feature,
    Hidpp,
    HidppError,
    HidppFailure,
    SysfsNode,
    feature_of,
    find_index,
    firmware_entity,
    not_there,
    paired,
    still_there,
    walk_nodes,
)
from .keyboard import CELLS as KEYBOARD_CELLS, MODEL_OF as KEYBOARD_OF
from .keycontrol import (
    CONTROLS_A_READ,
    F_CONTROL_LIST,
    FN_CONTROL_COUNT,
    FN_CONTROLS,
    read_controls,
)
from .lighting import (
    F_BRIGHTNESS,
    F_PERKEY,
    F_RGB,
    ZONE_PAGES,
    ZONE_PRESENCE,
    Cluster,
    Lighting,
    page_zones,
)
from .mouse import CELLS as MOUSE_CELLS, MODEL_OF as MOUSE_OF
from .mouse_lighting import F_COLOR_LED, ZoneLighting
from .onboard import F_ONBOARD, MODE_NAMES, ROM_SECTOR_BASE, OnboardError, OnboardProfiles
from .sensor import (
    EXTENDED_RATES,
    F_DPI,
    F_EXTENDED_DPI,
    F_EXTENDED_RATE,
    F_REPORT_RATE,
    ExtendedDpi,
    dpi_range,
    extended_bitmap,
    extended_rate,
    extended_rates,
    report_rates,
)
from .wording import counted, reason, word_list

#: The JSON form's version: a reader of posted reports takes the fields by it.
FORMAT = 1
#: The public repository's Device report issue form, which asks for the report's block.
REPORT_FORM = "https://github.com/gearbench/gearbench/issues/new?template=device-report.yml"
#: The next step a line gives for a device report: the command, and where its block goes.
SEND_REPORT = f"run `gearbench-ctl report` and paste its block into {REPORT_FORM}"


class ModelRecord(Protocol):
    """What the report reads of a model record of any family."""

    @property
    def name(self) -> str: ...

    @property
    def tested(self) -> bool: ...


#: The model records of each family on Logitech's vendor id this build drives, by product
#: id: the products the daemon's finders open, which the report sends nothing while a
#: daemon runs or may.
MODEL_RECORDS: tuple[Mapping[int, ModelRecord], ...] = (KEYBOARD_OF, MOUSE_OF, HEADSET_OF,
                                                        A50X_OF)


def model_record(product: int) -> ModelRecord | None:
    """The record of `product`'s model, None for a product this build does not drive."""
    return next((records[product] for records in MODEL_RECORDS if product in records), None)


def untested(product: int | None) -> bool:
    """Whether `product` is the id of a model this build drives that Gearbench has not yet run
    on (its record's `tested`); None is no device."""
    model = None if product is None else model_record(product)
    return model is not None and not model.tested


#: The first letters that take "an" before a model's name: "an Astro A50 X".
VOWELS = frozenset("AEIOU")


def untested_note(name: str) -> str:
    """What the command line and the journal say of a model `untested` names, and what to
    do: a device report from it shows what works on it and what to fix."""
    article = "an" if name[:1] in VOWELS else "a"
    return f"not yet tested on {article} {name}: {SEND_REPORT}"


F_NAME = 0x0005


# -- the report descriptor -----------------------------------------------------

#: The vendor usage pages a HID++ collection is on: 0xFF00 on the keyboards and mice, 0xFF43
#: on the G432 (its descriptor, read off the hardware 2026-10-17).
HIDPP_PAGES = frozenset({0xFF00, 0xFF43})
#: Generic Desktop's page, and its keyboard and keypad usages: a top-level collection of
#: either carries keystrokes (HUT 1.5, section 4).
DESKTOP_PAGE, KEYBOARD_USAGES = 0x01, frozenset({0x06, 0x07})
#: The Keyboard/Keypad page (HUT 1.5, section 10): an Input item on it carries keystrokes,
#: whatever collection it is in.
KEYBOARD_KEYPAD_PAGE = 0x07


@dataclass(frozen=True)
class Descriptor:
    """What a report descriptor declares, as the report says it: each report id with the
    usage page of the top-level collection it is in (declared between collections, the next
    one), and whether the node carries keystrokes, which anyone who may open it can read: a
    top-level collection that is a keyboard or keypad, or an Input item on the
    Keyboard/Keypad page in any collection."""

    reports: Mapping[int, int]
    key_reports: bool
    #: Whether the walk ended inside an item: a descriptor cut short.
    cut: bool

    @property
    def hidpp_short(self) -> bool:
        return self.reports.get(REPORT_SHORT) in HIDPP_PAGES

    @property
    def hidpp_long(self) -> bool:
        return self.reports.get(REPORT_LONG) in HIDPP_PAGES

    @property
    def hidpp(self) -> str | None:
        """The HID++ reports in words, None for none."""
        if self.hidpp_short and self.hidpp_long:
            return "short and long"
        if self.hidpp_long:
            return "long only"
        return "short only" if self.hidpp_short else None


#: Item tags (HID 1.11, section 6.2.2): main Input, Collection and End Collection; global
#: Usage Page, Report ID, Push and Pop; local Usage.
_INPUT, _COLLECTION, _END_COLLECTION = (0, 0x8), (0, 0xA), (0, 0xC)
_USAGE_PAGE, _REPORT_ID, _PUSH, _POP = (1, 0x0), (1, 0x8), (1, 0xA), (1, 0xB)
_USAGE = (2, 0x0)
#: A long item's prefix byte.
_LONG_ITEM = 0xFE


def describe(data: bytes) -> Descriptor:
    """Walk a report descriptor's items, HID 1.11's short and long forms, and keep what
    Descriptor holds. Bytes past an item cut short are not read."""
    reports: dict[int, int] = {}
    key_reports = cut = False
    page, pages, depth, top_page = 0, [], 0, 0
    usage: tuple[int | None, int] = (None, 0)
    # Report ids declared outside every collection: a Report ID is global (HID 1.11,
    # 6.2.2.7), so one before a top-level collection numbers that collection's reports.
    waiting: list[int] = []
    for kind, value, size in _items(data):
        if kind == _CUT:
            cut = True
        elif kind == _USAGE_PAGE:
            page = value
        elif kind == _PUSH:
            pages.append(page)
        elif kind == _POP and pages:
            page = pages.pop()
        elif kind == _INPUT:
            key_reports |= page == KEYBOARD_KEYPAD_PAGE
        elif kind == _USAGE:
            # A four-byte usage carries its own page in its top half (HID 1.11, 6.2.2.8).
            usage = (value >> 16 if size == 4 else None, value & 0xFFFF)
        elif kind == _COLLECTION:
            if depth == 0:
                # A collection named by a four-byte usage is on that usage's page; by a
                # shorter one (page None), on the page in effect at the collection.
                top_page = page if usage[0] is None else usage[0]
                key_reports |= top_page == DESKTOP_PAGE and usage[1] in KEYBOARD_USAGES
                while waiting:
                    reports.setdefault(waiting.pop(), top_page)
            depth += 1
            usage = (None, 0)
        elif kind == _END_COLLECTION:
            depth = max(depth - 1, 0)
        elif kind == _REPORT_ID and depth == 0:
            waiting.append(value)
        elif kind == _REPORT_ID:
            reports.setdefault(value, top_page)
    return Descriptor(reports, key_reports, cut=cut)


#: What _items gives for an item that runs past the end of the descriptor, and its last.
_CUT = (-1, -1)


def _items(data: bytes) -> Iterator[tuple[tuple[int, int], int, int]]:
    """Each short item as ((type, tag), value, size), the value little-endian as HID gives
    it; long items are stepped over, and one cut short ends the walk with _CUT."""
    at = 0
    while at < len(data):
        prefix = data[at]
        size = (data[at + 1] if at + 1 < len(data) else 0xFF) if prefix == _LONG_ITEM \
            else (0, 1, 2, 4)[prefix & 0x03]
        header = 3 if prefix == _LONG_ITEM else 1
        if at + header + size > len(data):
            yield _CUT, 0, 0
            return
        if prefix != _LONG_ITEM:
            yield ((prefix >> 2) & 0x03, prefix >> 4), int.from_bytes(
                data[at + 1:at + 1 + size], "little"), size
        at += header + size


# -- the reads -------------------------------------------------------------------

#: Where each read on the list is found: Logitech's public feature specifications (the
#: "hidpp20 public" folder Solaar's docs link, and its 2016 copies at lekensteyn.nl), and
#: the code of the programs that send them.
SPEC = "Logitech's {} specification"
SOLAAR = "Solaar's {}"
LIBRATBAG = "libratbag's hidpp20.c, {}"
KERNEL = "Linux's hid-logitech-hidpp.c, {}"
#: A read no public specification names, seen answered in a user's posted traffic.
TRAFFIC = "the traffic in Solaar issue {}"
#: A read no public source names, seen sent by G HUB and answered in a capture of its traffic.
CAPTURE = "G HUB's traffic with a {}"


def _any(_params: bytes) -> bool:
    return True


def _general_info(params: bytes) -> bool:
    """0x8071 getInfo with typeOfInfo 0, GeneralInfo, the one Solaar and lighting.py send: its
    other values are in Logitech's specification alone."""
    return len(params) >= 3 and params[2] == 0


def _zone_presence(params: bytes) -> bool:
    """0x8081 getInfo of a page of the zone bitmap, the one typeOfInfo x8081 v0 names."""
    return len(params) >= 2 and params[0] == ZONE_PRESENCE and params[1] < ZONE_PAGES


#: 0x1B0C, the PRO X2 SUPERSTRIKE's HITS buttons' tuning (Solaar's AnalogButtonTuning): fn 0
#: its capabilities, fn 2 one button's actuation, rapid trigger and haptics, fn 1 their write,
#: never sent. Its capabilities count three buttons, of which the left and right, 0 and 1,
#: are the ones Solaar reads; the report reads those alone.
F_ANALOG_BUTTONS = 0x1B0C
ANALOG_BUTTONS = (0, 1)


def _analog_button(params: bytes) -> bool:
    """0x1B0C fn 2 of the left or the right button (ANALOG_BUTTONS)."""
    return len(params) >= 1 and params[0] in ANALOG_BUTTONS


# 0x1B10, the control list of the G515 generation's keyboards (keycontrol.F_CONTROL_LIST):
# G HUB alone sends it, so its reads are the list's only ones with a single source, and the
# report sends them only to a model whose custom assignments need its key ids
# (KeyboardModel.custom_assignments), the G515 TKL's generation.


def _control_offset(params: bytes) -> bool:
    """0x1B10 fn 1 from an entry at a multiple of CONTROLS_A_READ, as G HUB asks it."""
    return len(params) >= 1 and params[0] % CONTROLS_A_READ == 0


def _factory_sector(params: bytes) -> bool:
    """0x8100 memoryRead of a factory sector, 0x0100 and up (onboard.ROM_SECTOR_BASE): the
    read-only profiles, the same on every unit. The user sectors below can hold macro text
    (the owner's decision of 2026-10-17)."""
    return len(params) >= 2 and int.from_bytes(params[0:2], "big") >= ROM_SECTOR_BASE


@dataclass(frozen=True)
class Read:
    """One function on the list: its name, two sources that call it a read (0x1B10's have
    one, F_CONTROL_LIST), which parameters it may carry, and the reply bytes zeroed as it is
    read, since they name the unit."""

    name: str
    sources: tuple[str] | tuple[str, str]
    allowed: Callable[[bytes], bool] = _any
    private: slice | None = None


#: Every (feature id, function) the report may send. A function only one source calls a
#: read stays off but 0x1B10's (F_CONTROL_LIST), as do reads of what names the unit or its
#: owner: 0x0003's serial number (function 2), 0x0007's friendly name, 0x1814 and 0x1815's
#: host names. The DFU features (0x00C0 to 0x00D0) and 0x1802's reset are never on it.
READS: Mapping[tuple[int, int], Read] = {
    (ROOT_FEATURE, 0): Read("getFeature", (SPEC.format("x0000 root v2"),
                                           SOLAAR.format("hidpp20.py, FeaturesArray"))),
    (ROOT_FEATURE, 1): Read("getProtocolVersion", (SPEC.format("x0000 root v2"),
                                                   SOLAAR.format("base.py, ping"))),
    (F_FEATURE_SET, 0): Read("getCount", (SPEC.format("x0001 feature set v2"),
                                          SOLAAR.format("hidpp20.py, FeaturesArray"))),
    (F_FEATURE_SET, 1): Read("getFeatureID", (SPEC.format("x0001 feature set v2"),
                                              SOLAAR.format("hidpp20.py, get_feature"))),
    (F_DEVICE_INFO, 0): Read("getDeviceInfo", (SPEC.format("x0003 device information v4"),
                                               SOLAAR.format("hidpp20.py, get_firmware")),
                             private=UNIT_ID),
    (F_DEVICE_INFO, 1): Read("getFwInfo", (SPEC.format("x0003 device information v4"),
                                           SOLAAR.format("hidpp20.py, get_firmware"))),
    (F_NAME, 0): Read("getDeviceNameCount", (SPEC.format("x0005 device type and name v2"),
                                             SOLAAR.format("hidpp20.py, get_name"))),
    (F_NAME, 1): Read("getDeviceName", (SPEC.format("x0005 device type and name v2"),
                                        SOLAAR.format("hidpp20.py, get_name"))),
    (F_NAME, 2): Read("getDeviceType", (SPEC.format("x0005 device type and name v2"),
                                        SOLAAR.format("hidpp20.py, get_kind"))),
    (F_RGB, 0): Read("getInfo", (SPEC.format("x8071 RGB effects v4"),
                                 SOLAAR.format("hidpp20.py, RGBEffectsInfo")),
                     allowed=_general_info),
    (F_PERKEY, 0): Read("getInfo", (SPEC.format("x8081 per-key lighting v0"),
                                    SOLAAR.format("settings_templates.py, PerKeyLighting")),
                        allowed=_zone_presence),
    (F_COLOR_LED, 0): Read("getInfo", (SPEC.format("x8070 color LED effects v7"),
                                       SOLAAR.format("hidpp20.py, LEDEffectsInfo"))),
    (F_COLOR_LED, 1): Read("getZoneInfo", (SPEC.format("x8070 color LED effects v7"),
                                           SOLAAR.format("hidpp20.py, LEDZoneInfo"))),
    (F_COLOR_LED, 2): Read("getZoneEffectInfo", (SPEC.format("x8070 color LED effects v7"),
                                                 SOLAAR.format("hidpp20.py, LEDEffectInfo"))),
    (F_BRIGHTNESS, 0): Read("getInfo", (SPEC.format("x8040 brightness control v1"),
                                        SOLAAR.format("settings_templates.py, "
                                                      "BrightnessControl"))),
    (F_BRIGHTNESS, 1): Read("getBrightness", (SPEC.format("x8040 brightness control v1"),
                                              SOLAAR.format("settings_templates.py, "
                                                            "BrightnessControl"))),
    (F_ONBOARD, 0): Read("getInfo", (LIBRATBAG.format("GET_PROFILES_DESCR"),
                                     SOLAAR.format("hidpp20.py, OnboardProfiles"))),
    (F_ONBOARD, 2): Read("getOnboardMode", (LIBRATBAG.format("GET_ONBOARD_MODE"),
                                            SOLAAR.format("hidpp20.py, get_onboard_mode"))),
    (F_ONBOARD, 4): Read("getCurrentProfile", (LIBRATBAG.format("GET_CURRENT_PROFILE"),
                                               SOLAAR.format("settings_templates.py, "
                                                             "OnboardProfiles"))),
    (F_ONBOARD, 5): Read("memoryRead", (LIBRATBAG.format("MEMORY_READ"),
                                        SOLAAR.format("hidpp20.py, read_sector")),
                         allowed=_factory_sector),
    (F_DPI, 0): Read("getSensorCount", (SPEC.format("x2201 adjustable DPI"),
                                        LIBRATBAG.format("GET_SENSOR_COUNT"))),
    (F_DPI, 1): Read("getSensorDpiList", (SPEC.format("x2201 adjustable DPI"),
                                          SOLAAR.format("settings_templates.py, AdjustableDpi"))),
    (F_DPI, 2): Read("getSensorDpi", (SPEC.format("x2201 adjustable DPI"),
                                      SOLAAR.format("settings_templates.py, AdjustableDpi"))),
    (F_REPORT_RATE, 0): Read("getReportRateList", (SPEC.format("x8060 report rate v0"),
                                                   SOLAAR.format("settings_templates.py, "
                                                                 "ReportRate"))),
    (F_REPORT_RATE, 1): Read("getReportRate", (SPEC.format("x8060 report rate v0"),
                                               SOLAAR.format("hidpp20.py, get_polling_rate"))),
    # No specification of 0x2202 or 0x8061 is public: the names are those of the 0x2201 and
    # 0x8060 functions each mirrors, and getSensorCapabilities for 0x2202's flags.
    (F_EXTENDED_DPI, 1): Read("getSensorCapabilities", (SOLAAR.format("settings_templates.py, "
                                                             "ExtendedAdjustableDpi"),
                                               TRAFFIC.format("2207, its log of 2024-03-29"))),
    (F_EXTENDED_DPI, 2): Read("getSensorDpiList", (SOLAAR.format("settings_templates.py, "
                                                         "ExtendedAdjustableDpi"),
                                           TRAFFIC.format("2207, its log of 2024-03-29"))),
    (F_EXTENDED_DPI, 5): Read("getSensorDpi", (SOLAAR.format("settings_templates.py, "
                                                    "ExtendedAdjustableDpi"),
                                      TRAFFIC.format("3353, whose read found lift-off 3"))),
    (F_EXTENDED_RATE, 1): Read("getReportRateList", (SOLAAR.format("settings_templates.py, "
                                                           "ExtendedReportRate"),
                                             TRAFFIC.format("2631"))),
    (F_EXTENDED_RATE, 2): Read("getReportRate", (SOLAAR.format("hidpp20.py, get_polling_rate"),
                                        TRAFFIC.format("2904, read at bit 5"))),
    (F_ANALOG_BUTTONS, 0): Read("getCapabilities", (
        SOLAAR.format("settings_templates.py, AnalogButtonTuning"),
        TRAFFIC.format("3132, whose listing reads it"))),
    (F_ANALOG_BUTTONS, 2): Read("getButtonConfig", (
        SOLAAR.format("settings_templates.py, AnalogButtonTuning"),
        TRAFFIC.format("3132, whose listing reads both buttons")),
        allowed=_analog_button),
    # No source names 0x1B10's functions: these say what each answers, as 0x1B04's count is
    # getCount.
    (F_CONTROL_LIST, FN_CONTROL_COUNT): Read("getCount", (
        CAPTURE.format("G515 TKL, 2026-10-21: the count, then the list"),)),
    (F_CONTROL_LIST, FN_CONTROLS): Read("getControlList", (
        CAPTURE.format("G515 TKL, 2026-10-21: 16 reads, 8 entries each"),),
        allowed=_control_offset),
    (battery.F_STATUS, 0): Read("getBatteryLevelStatus",
                                (KERNEL.format("CMD_BATTERY_LEVEL_STATUS_GET_BATTERY_LEVEL_STATUS"),
                                 SOLAAR.format("hidpp20.py, get_battery_status"))),
    (battery.F_VOLTAGE, 0): Read("getBatteryVoltage",
                                 (KERNEL.format("CMD_BATTERY_VOLTAGE_GET_BATTERY_VOLTAGE"),
                                  SOLAAR.format("hidpp20.py, get_battery_voltage"))),
    (battery.F_UNIFIED, 1): Read("getStatus", (KERNEL.format("CMD_UNIFIED_BATTERY_GET_STATUS"),
                                               SOLAAR.format("hidpp20.py, get_battery_unified"))),
    (battery.F_ADC, 0): Read("getADCMeasurement",
                             (KERNEL.format("CMD_ADC_MEASUREMENT_GET_ADC_MEASUREMENT"),
                              SOLAAR.format("hidpp20.py, get_adc_measurement"))),
    (F_SIDETONE, 0): Read("getSidetoneLevel", (SPEC.format("x8300 sidetone adjustment v1"),
                                               SOLAAR.format("settings_templates.py, Sidetone"))),
    (F_EQUALIZER, 0): Read("getEqInfo", (SPEC.format("x8310 audio equalizer v2"),
                                         SOLAAR.format("settings_templates.py, Equalizer"))),
    (F_EQUALIZER, 1): Read("getFrequencies", (SPEC.format("x8310 audio equalizer v2"),
                                              SOLAAR.format("settings_templates.py, Equalizer"))),
    (F_EQUALIZER, 2): Read("getFrequencyGains", (SPEC.format("x8310 audio equalizer v2"),
                                                 SOLAAR.format("settings_templates.py, "
                                                               "Equalizer"))),
}


class NotARead(Exception):
    """A request off READS, refused before anything was written: a fault in the report's
    own code, never the device's. Not a HidppFailure, so no guard of a read takes it for a
    device's refusal."""


class UnsureIndex(HidppFailure):
    """The device named one index for two features, so which feature a request to it
    reaches is not known, or an index of SET_REGISTER (0x80) and up, the range that holds
    HID++ 1.0's register requests and the error replies: no read goes to that index."""


class Absent(HidppFailure):
    """A read needs a feature the device's root lookup does not find: nothing is sent."""


#: The ping byte getProtocolVersion's reply echoes.
PING = 0x5A
#: How long a burst's replies are waited for, in seconds: as long as one try of a read
#: waits for its reply.
BURST_WAIT = REQUEST_TIMEOUT


def _cut(read: Read, reply: bytes) -> bytes:
    """`reply` with the bytes that name the unit, `read.private`, zeroed."""
    if read.private is None:
        return reply
    cut = bytearray(reply)
    cut[read.private] = bytes(len(cut[read.private]))
    return bytes(cut)


@dataclass
class ReadOnly:
    """A wrapper of a `Hidpp` that sends only READS, each to an index a root lookup named.
    It offers what the decoders call on a read path, `feature`, `require` and `request`,
    `read` by feature id for the report's own reads, and `burst`, the same check's requests
    sent back to back; no other send, no register function. `log` keeps each read and its
    reply or failure, and each request of a burst with its software id, or the failure that
    ended the burst, for the JSON form."""

    dev: Hidpp
    #: Each index a root lookup named, and its feature id: index 0 is the root's.
    ids: dict[int, int] = field(default_factory=lambda: {0: ROOT_FEATURE})
    found: dict[int, Feature | None] = field(default_factory=dict)
    log: list[dict] = field(default_factory=list)
    #: The feature ids the device's FeatureSet table lists, once feature_table has read it
    #: whole, None before. A feature off it is not looked up: the G432 answers a lookup of a
    #: feature it lacks with an error, where the other models answer index 0 (2026-10-18).
    listed: frozenset[int] | None = None
    #: Each index the device named for two features, and the UnsureIndex text that says so:
    #: no read goes to it.
    unsure: dict[int, str] = field(default_factory=dict)

    @classmethod
    def open(cls, path: str | os.PathLike[str], device_index: int, *,
             long_only: bool) -> ReadOnly:
        """The node opened at `device_index`. Raises OSError, PermissionError for a node
        this account may not open."""
        return cls(Hidpp(path, device_index, long_only=long_only))

    def close(self) -> None:
        self.dev.close()

    def feature(self, feature_id: int, *, timeout: float = REQUEST_TIMEOUT,
                retries: int = REQUEST_RETRIES) -> Feature | None:
        """A feature id's index as the root lookup answers it, or None if absent. An answer is
        kept, so a feature is asked again only after its lookup raised, UnsureIndex included,
        and never asked when the table does not list it (`listed`)."""
        if feature_id not in self.found:
            found = None
            if self.listed is None or feature_id in self.listed:
                found = feature_of(self.request(0, 0, feature_id.to_bytes(2, "big"),
                                                timeout=timeout, retries=retries))
                if found is not None:
                    if found.index >= SET_REGISTER:
                        raise UnsureIndex(f"the device gave 0x{feature_id:04x} index "
                                          f"0x{found.index:02x}, in the range that holds HID++ "
                                          "1.0's register requests and the error replies")
                    named = self.ids.setdefault(found.index, feature_id)
                    if named != feature_id:
                        self.unsure[found.index] = (
                            f"the device gave index 0x{found.index:02x} to both "
                            f"0x{named:04x} and 0x{feature_id:04x}")
                        raise UnsureIndex(self.unsure[found.index])
            self.found[feature_id] = found
        return self.found[feature_id]

    def require(self, feature_id: int, name: str) -> Feature:
        """As Hidpp.require: a feature the decoders need, which the report looked up first."""
        found = self.feature(feature_id)
        if found is None:
            raise Absent(f"this firmware has no 0x{feature_id:04x} {name}")
        return found

    def request(self, feature_index: int, function: int, params: bytes = b"", *,
                timeout: float = REQUEST_TIMEOUT, long: bool = False,
                retries: int = REQUEST_RETRIES) -> bytes:
        """One request of READS, as Hidpp.request sends it; NotARead, with nothing
        written, for any other, or for an index no root lookup named, and UnsureIndex for
        one the device named for two features."""
        read, kept = self._checked(feature_index, function, params)
        try:
            reply = self.dev.request(feature_index, function, params, timeout=timeout,
                                     long=long, retries=retries)
        except HidppFailure as exc:
            self.log.append({**kept, "error": str(exc)})
            raise
        reply = _cut(read, reply)
        self.log.append({**kept, "reply": reply.hex()})
        return reply

    def burst(self, feature_id: int, function: int,
              params: Sequence[bytes]) -> tuple[list[int], list[tuple[int, bytes | HidppError]]]:
        """Requests of one read of READS, one per `params`, sent back to back as
        Hidpp.send_burst sends them, and what came back for BURST_WAIT: each request's
        software id, and each reply with the id it carries. NotARead or UnsureIndex, with
        nothing written, as `request` refuses, for any of them."""
        index = self.require(feature_id, "feature").index
        checked = [self._checked(index, function, each) for each in params]
        try:
            sent, came = self.dev.send_burst(index, function, params, timeout=BURST_WAIT)
        except HidppFailure as exc:
            self.log += [{**kept, "error": str(exc)} for _read, kept in checked]
            raise
        read = checked[0][0]
        self.log += [{**kept, "burst": software_id}
                     for (_read, kept), software_id in zip(checked, sent, strict=True)]
        return sent, [(software_id, answer if isinstance(answer, HidppError)
                       else _cut(read, answer)) for software_id, answer in came]

    def _checked(self, feature_index: int, function: int,
                 params: bytes) -> tuple[Read, dict[str, object]]:
        """The read of READS a request is, and how `log` names it; NotARead for one off the
        list or to an index no root lookup named, and UnsureIndex for one to an index the
        device named for two features."""
        feature_id = self.ids.get(feature_index)
        read = READS.get((feature_id, function)) if feature_id is not None else None
        if read is None or not read.allowed(params):
            raise NotARead(f"function {function} of feature index 0x{feature_index:02x} "
                           f"({'not looked up' if feature_id is None else f'0x{feature_id:04x}'}) "
                           f"with {params.hex(' ') or 'no parameters'} is not a read "
                           "the report sends")
        if feature_index in self.unsure:
            raise UnsureIndex(self.unsure[feature_index])
        return read, {"feature": f"0x{feature_id:04x}", "function": function,
                      "params": params.hex()}

    def has(self, feature_id: int) -> bool:
        """Whether the root lookup finds `feature_id`."""
        return self.feature(feature_id) is not None

    def read(self, feature_id: int, function: int, params: bytes = b"", *,
             long: bool = False) -> bytes:
        """One read by feature id, of the root at index 0 or of a feature `has` found."""
        index = 0 if feature_id == ROOT_FEATURE else self.require(feature_id, "feature").index
        return self.request(index, function, params, long=long)


def _as_hidpp(dev: ReadOnly) -> Hidpp:
    """`dev` for a decoder that takes a Hidpp: those the report runs call only `feature`,
    `require` and `request`, which ReadOnly has, on the paths it runs, so any other call
    fails with AttributeError before a byte is written."""
    return cast("Hidpp", dev)


# -- what one device says --------------------------------------------------------

#: getDeviceType's values (x0005 v2).
DEVICE_TYPES = ("keyboard", "remote control", "numpad", "mouse", "trackpad", "trackball",
                "presenter", "receiver", "headset", "webcam", "steering wheel", "joystick",
                "gamepad")
#: getDeviceInfo's transport bits, bit 0 first (x0003 v4).
TRANSPORTS = ("Bluetooth", "Bluetooth Low Energy", "eQuad", "USB")
#: The x0001 version that added featureVersion to getFeatureID's reply (x0001 v2's
#: changelog): a v0 FeatureSet, as the G432's, gives no version.
FEATURE_VERSIONS_SINCE = 1
#: The most name characters and factory profiles read, their directory besides, whatever a
#: reply says, as hidpp.ENTITIES_MAX for the firmware entities and lighting.CLUSTERS_MAX
#: and EFFECTS_MAX for the lighting walks: a byte the device gets wrong cannot lengthen
#: those walks.
NAME_MAX, FACTORY_SECTORS_MAX = 64, 16


def _hex16(value: int) -> str:
    return f"0x{value:04x}"


def feature_table(dev: ReadOnly) -> list[dict]:
    """FeatureSet's rows, index 1 up: each id, its flags byte and its version, None from a
    FeatureSet older than FEATURE_VERSIONS_SINCE, whose reply has no version byte. Read
    whole, its ids are kept as `dev.listed`, so the sections after it look up no other
    feature."""
    versioned = dev.require(F_FEATURE_SET, "FEATURE_SET").version >= FEATURE_VERSIONS_SINCE
    count = dev.read(F_FEATURE_SET, 0)[0]
    rows, ids = [], set()
    for index in range(1, count + 1):
        reply = dev.read(F_FEATURE_SET, 1, bytes([index]))
        feature_id = int.from_bytes(reply[0:2], "big")
        ids.add(feature_id)
        rows.append({"index": index, "id": _hex16(feature_id), "flags": reply[2],
                     "version": reply[3] if versioned else None})
    dev.listed = frozenset(ids)
    return rows


def device_info(dev: ReadOnly) -> dict | None:
    """getDeviceInfo, its unit id left out, with the fields of the version the root lookup
    gives the feature, and each entity's getFwInfo. A field a later version added is not
    read: the G432's 0x0003 is v2, so its byte 14 is no capabilities byte."""
    found = dev.feature(F_DEVICE_INFO)
    if found is None:
        return None
    info = dev.read(F_DEVICE_INFO, 0)
    out: dict = {"entities": info[0]}
    if found.version >= MODEL_IDS_SINCE:
        transport = int.from_bytes(info[5:7], "big")
        out["transports"] = [name for bit, name in enumerate(TRANSPORTS) if transport >> bit & 1]
        out["model_ids"] = [_hex16(int.from_bytes(info[i:i + 2], "big")) for i in range(7, 13, 2)]
    if found.version >= EXTENDED_MODEL_SINCE:
        out["extended_model_id"] = info[13]
    if found.version >= CAPABILITIES_SINCE:
        out["capabilities"] = info[14]
    firmware = []
    for entity in range(min(info[0], ENTITIES_MAX)):
        fw = firmware_entity(dev.read(F_DEVICE_INFO, FN_FW_INFO, bytes([entity])))
        firmware.append({"type": fw.kind, "prefix": fw.prefix, "version": fw.version,
                         "active": fw.active, "transport_id": _hex16(fw.transport_id)})
    return {**out, "firmware": firmware}


def device_name(dev: ReadOnly) -> dict | None:
    """getDeviceName's characters, at most NAME_MAX, in as many reads as they take, and
    getDeviceType."""
    if not dev.has(F_NAME):
        return None
    wanted = min(dev.read(F_NAME, 0)[0], NAME_MAX)
    name = b""
    while len(name) < wanted:
        chunk = dev.read(F_NAME, 1, bytes([len(name)]), long=True)
        name += chunk[:wanted - len(name)]
    kind = dev.read(F_NAME, 2)[0]
    return {"name": name.rstrip(b"\x00").decode("utf-8", errors="replace"),
            "type": DEVICE_TYPES[kind] if kind < len(DEVICE_TYPES) else f"type {kind}"}


def _clusters(clusters: Sequence[Cluster]) -> list[dict]:
    """lighting.Cluster records, 0x8071's clusters or 0x8070's zones, as the report gives
    them."""
    return [{"index": c.index, "location": _hex16(c.location), "name": c.name,
             "effects": [{"index": e.index, "id": _hex16(e.id), "name": e.name}
                         for e in c.effects]}
            for c in clusters]


def lighting(dev: ReadOnly, product: int) -> dict | None:
    """0x8071's clusters and their effects, the whole board's after them on a keyboard whose
    model reads it (keyboard.KeyboardModel.whole_board), and 0x8040's levels and brightness
    where it has both, as lighting.Lighting reads them."""
    model = KEYBOARD_OF.get(product)
    found = Lighting.find(_as_hidpp(dev), whole_board=model.whole_board if model else ())
    if found is None:
        return None
    levels = list(found.brightness_levels)
    return {"clusters": _clusters(found.effect_clusters), "brightness_levels": levels,
            "brightness": found.get_brightness() if levels else None}


def per_key(dev: ReadOnly) -> dict | None:
    """0x8081's zone bitmap, a page a read, and the zones it sets as runs: the zones a
    keyboard has an LED for, which it checks a range write's ends against (the G515 TKL
    refused a range that ended at the G915's logo, 2026-10-18). A page the device refuses
    is said in its place, and the rest are read; a device not there is raised, and ends
    the reads (read_device)."""
    if not dev.has(F_PERKEY):
        return None
    pages: list[dict] = []
    zones: list[int] = []
    for page in range(ZONE_PAGES):
        try:
            bitmap = dev.read(F_PERKEY, 0, bytes([ZONE_PRESENCE, page]))[2:]
        except HidppError as exc:
            if not_there(exc):
                raise
            pages.append({"page": page, "refused": str(exc)})
            continue
        # A bitmap cut short raises IndexError, which read_device says in words.
        zones += page_zones(page, bitmap)
        pages.append({"page": page, "bitmap": bitmap.hex()})
    return {"zones": runs(zones), "pages": pages}


def runs(numbers: list[int]) -> list[list[int]]:
    """Ascending `numbers` as [first, last] runs of consecutive ones."""
    found: list[list[int]] = []
    for number in numbers:
        if found and number == found[-1][1] + 1:
            found[-1][1] = number
        else:
            found.append([number, number])
    return found


def zones(dev: ReadOnly) -> dict | None:
    """0x8070's zones and their effects, as mouse_lighting.ZoneLighting reads them. The
    report sends no effect, so no model's claim before each one applies."""
    found = ZoneLighting.find(_as_hidpp(dev), claim_each_effect=False)
    return None if found is None else {"zones": _clusters(found.clusters)}


def onboard(dev: ReadOnly) -> dict | None:
    """0x8100's getInfo, mode and running profile, and its factory sectors, 0x0100 (their
    directory) to the last factory profile, each whole in hex; never a user sector."""
    if not dev.has(F_ONBOARD):
        return None
    memory = OnboardProfiles(_as_hidpp(dev))
    info = memory.info
    mode = memory.get_mode()
    factory = range(ROM_SECTOR_BASE, ROM_SECTOR_BASE + 1
                    + min(info.rom_profile_count, FACTORY_SECTORS_MAX))
    return {"memory_model": info.memory_model, "profile_format": info.profile_format,
            "macro_format": info.macro_format, "profiles": info.profile_count,
            "factory_profiles": info.rom_profile_count, "buttons": info.button_count,
            "sector_count": info.sector_count, "sector_size": info.sector_size,
            "mode": MODE_NAMES.get(mode, f"0x{mode:02x}"),
            "running_sector": _hex16(memory.current_profile()),
            "factory_sectors": {_hex16(sector): factory_sector(memory, sector)
                                for sector in factory}}


def factory_sector(memory: OnboardProfiles, sector: int) -> str:
    """A factory sector whole in hex, or why the device refused it: a refusal of one leaves
    the others, and the rest of the memory's part, to be read. Silence, and a device not
    there, are raised."""
    try:
        return memory.read_sector(sector).hex()
    except HidppError as exc:
        return refusal(exc)


def refusal(exc: HidppError) -> str:
    """A read the device refused, in the place of what it would have read. A receiver's word
    that the device is not there (hidpp.not_there) is no refusal: it is raised, and ends the
    reads (read_device)."""
    if not_there(exc):
        raise exc
    return f"refused: {exc}"


def battery_reading(dev: ReadOnly, cell: battery.CellCurves | None) -> dict | None:
    """The first battery feature of battery.READERS the table lists, read and decoded as
    battery.read decodes it, None for none. A refusal is raised, where battery.read gives
    None, so that read_device names it in `failed`."""
    for feature_id, function, decode in battery.READERS:
        if dev.has(feature_id):
            return decode(dev.read(feature_id, function), cell)
    return None


def dpi(dev: ReadOnly) -> dict | None:
    """0x2201's sensor count, and sensor 0's DPI list, as sensor.dpi_range decodes it, its DPI
    and its default."""
    if not dev.has(F_DPI):
        return None
    count = dev.read(F_DPI, 0)
    found = dpi_range(dev.read(F_DPI, 1, bytes([0x00]), long=True))
    now = dev.read(F_DPI, 2, bytes([0x00]))
    return {"sensors": count[0],
            "range": None if found is None else found.reply,
            "dpi": int.from_bytes(now[1:3], "big"), "default": int.from_bytes(now[3:5], "big")}


def report_rate(dev: ReadOnly) -> dict | None:
    """0x8060's rates, in Hz as sensor.report_rates gives them, and its interval now in ms."""
    if not dev.has(F_REPORT_RATE):
        return None
    return {"rates": report_rates(dev.read(F_REPORT_RATE, 0)[0]),
            "interval_ms": dev.read(F_REPORT_RATE, 1)[0]}


def extended_dpi(dev: ReadOnly) -> dict | None:
    """0x2202's flags, sensor 0's list, as sensor.banded_range decodes it, and its DPI."""
    found = dev.feature(F_EXTENDED_DPI)
    if found is None:
        return None
    sensor = ExtendedDpi(_as_hidpp(dev), found)
    span = sensor.read_range()
    now = sensor.params()
    return {"flags": sensor.flags, "range": None if span is None else span.reply,
            "bands": [] if span is None else [list(band) for band in span.bands],
            "dpi": now.x, "dpi_y": now.y, "lift_off": now.lift_off}


def extended_report_rate(dev: ReadOnly) -> dict | None:
    """0x8061's rates, every one it lists: in Hz, and by bit where no source names the rate;
    and the one in force: its bit, and its Hz where a source names the bit."""
    if not dev.has(F_EXTENDED_RATE):
        return None
    listed = extended_bitmap(dev.read(F_EXTENDED_RATE, 1))
    bit = dev.read(F_EXTENDED_RATE, 2)[0]
    return {"rates": extended_rates(listed),
            "unnamed_bits": [each for each in range(len(EXTENDED_RATES), listed.bit_length())
                             if listed >> each & 1],
            "bit": bit, "hz": extended_rate(bit)}


def analog_buttons(dev: ReadOnly) -> dict | None:
    """0x1B0C's capabilities and the left and right buttons' tuning, as the device gives them:
    Solaar has changed how it reads their bytes (issue 3202), so the report keeps them raw."""
    if not dev.has(F_ANALOG_BUTTONS):
        return None
    return {"capabilities": dev.read(F_ANALOG_BUTTONS, 0)[:5].hex(" "),
            "buttons": [dev.read(F_ANALOG_BUTTONS, 2, bytes([button]))[:4].hex(" ")
                        for button in ANALOG_BUTTONS]}


def control_list(dev: ReadOnly, product: int) -> dict | None:
    """0x1B10's count, and each entry in hex, the control id at that key id, from 0
    (keycontrol.read_controls), on a keyboard whose custom assignments need its key ids
    (KeyboardModel.key_ids); None for any other model, which is sent nothing of it."""
    model = KEYBOARD_OF.get(product)
    if model is None or not model.custom_assignments or not dev.has(F_CONTROL_LIST):
        return None
    # A reply cut short raises IndexError, which read_device says in words.
    controls = read_controls(lambda function, params: dev.read(F_CONTROL_LIST, function,
                                                               params))
    return {"count": len(controls), "controls": [f"{control:04x}" for control in controls]}


def sidetone(dev: ReadOnly) -> dict | None:
    """0x8300's level."""
    return {"level": dev.read(F_SIDETONE, 0)[0]} if dev.has(F_SIDETONE) else None


def equalizer(dev: ReadOnly) -> dict | None:
    """0x8310's getEqInfo, its bands' frequencies, and getFrequencyGains at both locations,
    0 the saved gains and 1 the active ones (x8310 v2), each a signed dB a byte, or why the
    device refused that location: the G432 answers location 0 and refuses 1 (2026-10-18).
    The spec's table puts band 0 at byte 1 and its example at byte 0, so a band more is
    kept than the count: the hardware settles which."""
    if not dev.has(F_EQUALIZER):
        return None
    info = dev.read(F_EQUALIZER, 0)
    bands = info[0]
    frequencies = eq_frequencies(lambda params: dev.read(F_EQUALIZER, 1, params), bands)
    gains = {location: _gains(dev, location, bands + 1) for location in (0, 1)}
    return {"bands": bands, "db_range": info[1], "capabilities": info[2],
            "db_min": signed_byte(info[3]), "db_max": signed_byte(info[4]),
            "frequencies": frequencies, "saved_gains": gains[0], "active_gains": gains[1]}


def _gains(dev: ReadOnly, location: int, count: int) -> list[int] | str:
    """getFrequencyGains' first `count` bytes at `location`, signed, or why the device refused
    it: a refusal of one location leaves the other to be read. Silence, and a device not
    there, are raised."""
    try:
        reply = dev.read(F_EQUALIZER, 2, bytes([location]))
    except HidppError as exc:
        return refusal(exc)
    return [signed_byte(byte) for byte in reply[:count]]


#: The reply-order check's requests: getFeatureID of the table's rows 1 and 2, two reads of
#: one function whose replies differ, so a reply's payload says which request it answers.
#: Two, which the words of order_words are for.
BURST_PARAMS = (b"\x01", b"\x02")
#: How many times the check sends its burst: one burst gave a device different answers in
#: different runs, the rarer one in one run of three (2026-10-18). Five show an answer a
#: device gives one burst in three at least once with odds of 1 - (2/3) ** 5, about 87 %. A
#: burst short of a reply waits BURST_WAIT for it, so the check adds at most BURSTS times
#: that, 5 s, to a node's read.
BURSTS = 5
#: What a burst whose replies carry its requests' software ids in order says, and one that
#: had none in BURST_WAIT.
IN_ORDER, NO_REPLY = "replies in order", "no reply"
#: Each of BURST_PARAMS' requests by its place, as order_words names the id it went out with.
ORDINALS = ("first", "second")


def reply_order(dev: ReadOnly) -> dict:
    """BURST_PARAMS' reads sent back to back, without waiting between the two
    (ReadOnly.burst), BURSTS times: each burst's software ids, each of its replies with the
    id it carries, and what they say (order_words), then every burst's answer counted in
    words. The first write of each burst drops a reply that came late to the one before; one
    that comes later still is said with the id it carries. It is the last section, so a
    reply that comes after the last burst's BURST_WAIT reaches no other read."""
    bursts = [burst_answer(dev) for _ in range(BURSTS)]
    return {"bursts": bursts,
            "result": (f"{times(len(bursts))}, each given {BURST_WAIT:g} s for its replies: "
                       f"{tally_words([burst['result'] for burst in bursts])}")}


def burst_answer(dev: ReadOnly) -> dict:
    """One burst of BURST_PARAMS' reads: the software ids its requests went out with, each
    reply with the id it carries, and what they say (order_words). A receiver's word that the
    device is not there is no reply of the device's: it is raised, and ends the reads
    (read_device)."""
    sent, came = dev.burst(F_FEATURE_SET, 1, BURST_PARAMS)
    for _software_id, answer in came:
        if isinstance(answer, HidppError) and not_there(answer):
            raise answer
    replies = [{"software_id": software_id,
                **({"error": str(answer)} if isinstance(answer, HidppError)
                   else {"reply": answer.hex()})}
               for software_id, answer in came]
    return {"sent": sent, "replies": replies,
            "result": order_words(sent, [software_id for software_id, _answer in came])}


def order_words(sent: list[int], ids: list[int]) -> str:
    """What the software ids of a burst's replies, in the order they came, say of the ids its
    requests went out with. Each id is named by the request that had it, never by its
    number, so that bursts sent with other ids whose replies came back alike are said alike."""
    if ids == sent:
        return IN_ORDER
    whose = [_whose(sent, software_id) for software_id in ids]
    if not whose:
        return NO_REPLY
    if len(whose) == 1:
        return f"a single reply with {whose[0]}"
    if len(set(ids)) == 1:
        return f"both replies with {whose[0]}"
    return f"replies with {', then '.join(whose)}"


def _whose(sent: list[int], software_id: int) -> str:
    """A reply's software id by the request of `sent` that went out with it."""
    if software_id in sent:
        return f"the {ORDINALS[sent.index(software_id)]} request's id"
    return "an id neither request had"


def tally_words(results: list[str]) -> str:
    """Each answer of `results` with how many bursts gave it, the most given first and those
    given as often in the order they first came; one answer alone is given each time."""
    counts = Counter(results).most_common()
    if len(counts) == 1:
        return f"{counts[0][0]} each time"
    return word_list([f"{words} {times(count)}" for words, count in counts], "and")


def times(count: int) -> str:
    """How many times, in words: "once", "twice", "3 times"."""
    return {1: "once", 2: "twice"}.get(count) or counted(count, "time")


#: What a section can fail with, named in `failed`: a failed request or a reply Gearbench
#: cannot read (HidppFailure, UnreadableAnswer among them, a lighting reply too short to
#: decode too), and a reply onboard found out of shape. A device not there ends the rest
#: (read_device); a reply too short to index is said apart.
SECTION_FAILURES = (HidppFailure, OnboardError)


def read_device(dev: ReadOnly, product: int) -> dict:
    """Everything the report reads of the device on `dev`, section by section in the
    report's order. A section that fails is named in `failed` with why, and the rest go on;
    the first that finds the device not there (hidpp.not_there) ends the reads, which would
    each wait out their timeout, and so does a device gone. NotARead is let through."""
    cell = (KEYBOARD_CELLS.get(product) or MOUSE_CELLS.get(product)
            or HEADSET_CELLS.get(product))
    sections: tuple[tuple[str, Callable[[], object]], ...] = (
        ("protocol", lambda: protocol(dev)), ("features", lambda: feature_table(dev)),
        ("device", lambda: device_info(dev)), ("name", lambda: device_name(dev)),
        ("lighting", lambda: lighting(dev, product)), ("per_key", lambda: per_key(dev)),
        ("zones", lambda: zones(dev)),
        ("onboard", lambda: onboard(dev)),
        ("battery", lambda: battery_reading(dev, cell)),
        ("dpi", lambda: dpi(dev)), ("report_rate", lambda: report_rate(dev)),
        ("extended_dpi", lambda: extended_dpi(dev)),
        ("extended_report_rate", lambda: extended_report_rate(dev)),
        ("analog_buttons", lambda: analog_buttons(dev)),
        ("control_list", lambda: control_list(dev, product)),
        ("sidetone", lambda: sidetone(dev)), ("equalizer", lambda: equalizer(dev)),
        ("reply_order", lambda: reply_order(dev)))
    out: dict = {"failed": []}
    for name, section in sections:
        try:
            value = section()
        except DeviceGone as exc:
            out["failed"].append(f"{name}: the device went away: {exc}")
            break
        except IndexError:
            out["failed"].append(f"{name}: a reply too short to read")
            continue
        except SECTION_FAILURES as exc:
            out["failed"].append(f"{name}: {exc}")
            if not_there(exc):
                break
            continue
        if value is not None:
            out[name] = value
    return out


def protocol(dev: ReadOnly) -> str:
    """getProtocolVersion's major and minor, as "4.2"."""
    reply = dev.read(ROOT_FEATURE, 1, bytes([0, 0, PING]))
    return f"{reply[0]}.{reply[1]}"


# -- every node --------------------------------------------------------------------

#: Where the nodes `walk_nodes` names are opened.
DEV = Path("/dev")

#: A node's state in the report, each in the words the report gives it.
READ = "read"
NOT_HIDPP = "no HID++ collection, so nothing was sent"
HELD = "held by the Gearbench daemon, so nothing was sent"
BESIDE_HELD = "on the USB device of a node the Gearbench daemon holds, so nothing was sent"
UNSURE = "not read: the daemon is running or may be, and may open it"
RECEIVER = "a receiver, sent nothing: each device paired to it has a node of its own"
NO_PERMISSION = "not read: no permission to open it"
#: Any other failed open, with its cause.
NOT_OPENED = "not read: it could not be opened: {}"
NO_ANSWER = "no HID++ 2.0 device answered on it"
#: A node unplugged, or taken by another device, between the listing and its open.
REPLACED = "not read: unplugged or taken by another device after the report listed it"


def model_name(product: int) -> str | None:
    """The name of a model this build drives, None for any other."""
    model = model_record(product)
    return None if model is None else model.name


def gather(held: Mapping[str, Mapping[str, object]], unsure: Callable[[], bool]) -> list[dict]:
    """Every Logitech USB node's part of the report, in the kernel's order (walk_nodes).

    `held` is what the daemon says of each node it holds, by node name: such a node is sent
    nothing and gets the daemon's fields (HELD). Every other node with a HID++ collection on
    its USB device is sent nothing too, with empty daemon fields (BESIDE_HELD), but a
    receiver's own node with devices paired to it as nodes of their own, which is sent
    nothing as a receiver. `unsure` says whether a daemon runs or may, and while one does, a
    model this build drives is not opened either: the daemon may hold it, and its attach
    retries open and probe such a node it does not hold, where two processes on one node
    take each other's replies. It is asked again for each node it may leave unopened, since
    a daemon may start while the nodes before it are read.
    """
    nodes = walk_nodes()
    taken = {node.usb for node in nodes if node.name in held and node.usb is not None}
    behind: dict[Path, list[str]] = {}
    for node in nodes:
        if node.usb is not None and paired(node.product, node.usb_product):
            behind.setdefault(node.usb, []).append(node.name)
    return [node_report(node, held, unsure, taken, behind) for node in nodes]


def node_report(node: SysfsNode, held: Mapping[str, Mapping[str, object]],
                unsure: Callable[[], bool], taken: set[Path],
                behind: Mapping[Path, list[str]]) -> dict:
    """One node's part: its sysfs part, then what its state lets the report read."""
    found = describe(node.descriptor)
    record: dict = {
        "node": node.name, "product": f"{node.product:04x}", "model": model_name(node.product),
        "interface": node.interface, "driver": node.driver,
        "descriptor": {"bytes": len(node.descriptor), "hex": node.descriptor.hex(),
                       "hidpp": found.hidpp, "key_reports": found.key_reports, "cut": found.cut,
                       "reports": {f"0x{report:02x}": _hex16(page)
                                   for report, page in sorted(found.reports.items())}}}
    if found.hidpp is None:
        return {**record, "state": NOT_HIDPP}
    receiving = (behind.get(node.usb) if node.usb is not None
                 and not paired(node.product, node.usb_product) else None)
    if node.name in held or (node.usb in taken and receiving is None):
        return {**record, "state": HELD if node.name in held else BESIDE_HELD,
                "daemon": dict(held.get(node.name, {}))}
    if record["model"] is not None and unsure():
        return {**record, "state": UNSURE}
    if receiving is not None:
        return {**record, "state": RECEIVER, "paired": receiving}
    # The nodes before this one were read since the listing: a node unplugged meanwhile can
    # now be another device's, of any vendor.
    if not still_there(node):
        return {**record, "state": REPLACED}
    if not os.access(DEV / node.name, os.R_OK | os.W_OK):
        return {**record, "state": NO_PERMISSION}
    return {**record, **read_node(DEV / node.name, node, found)}


def read_node(path: Path, node: SysfsNode, found: Descriptor) -> dict:
    """The HID++ part of a node: the device index the probe finds, the receiver's first
    slot first for a device paired through one, then read_device's sections, with every
    read and its reply in `reads`."""
    def opener(at: str | os.PathLike[str], index: int) -> ReadOnly:
        # A collection that declares no short report gets every request on the long one.
        return ReadOnly.open(at, index, long_only=not found.hidpp_short)

    try:
        opened = find_index(path, F_FEATURE_SET, opener,
                            RECEIVER_INDEX if paired(node.product, node.usb_product)
                            else DEVICE_INDEX)
    except PermissionError:
        return {"state": NO_PERMISSION}
    except OSError as exc:
        return {"state": NOT_OPENED.format(reason(exc))}
    if opened is None:
        return {"state": NO_ANSWER}
    dev, index = opened
    try:
        read = read_device(dev, node.product)
    finally:
        dev.close()
    return {"state": READ, "device_index": f"0x{index:02x}", **read, "reads": dev.log}
