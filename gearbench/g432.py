"""The Logitech G432's USB adapter (046d:0a9c): its HID interface, and the HID++ requests
its channel, `G432`, sends it (SENT). Nothing here imports the daemon.

The adapter is a USB Audio Class 1 device with one HID interface, 03, which carries the
volume wheel's and the mic mute's reports beside HID++. Its report descriptor holds HID++'s
long report, 0x11, alone, on Logitech's page 0xFF43 (read off the hardware, 2026-10-17),
so every request goes out long (`Hidpp.long_only`).

G HUB's device list gives the G432 no onboard memory, yet the adapter kept its sidetone
level through a replug (the hardware, 2026-10-17): a reset writes back the level the
daemon first read, or SIDETONE_UNREAD_RESET where it read none before a write
(daemon/g432.py). The feature's index is looked up through the root feature, never taken as
the 5 one other program sends to, though the G432 read has it there: an index is what one
firmware reports (hidpp.py).
The adapter lists 0x8310 too, an equalizer, which `G432` sends nothing (SENT); the device
report reads its functions 0 to 2 through its own read-only channel (report.READS).
"""

from __future__ import annotations

import os
from pathlib import Path

from .hidpp import (
    DEVICE_INDEX,
    ENTITIES_MAX,
    F_DEVICE_INFO,
    FN_DEVICE_INFO,
    FN_FW_INFO,
    FW_INFO_LENGTH,
    MAIN_APPLICATION,
    PROBE_RETRIES,
    PROBE_TIMEOUT,
    ROOT_FEATURE,
    VENDOR_ID,
    Feature,
    Hidpp,
    HidppFailure,
    feature_of,
    find_attached,
    firmware_entity,
)
from .wording import counted, whole

#: The G432's adapter. The G431 (0a9b), which G HUB's device list gives the same interfaces,
#: stays out until one is read off the hardware.
PRODUCTS = {0x0A9C: "Logitech G432"}
#: Its HID interface, the one its udev rule grants (lsusb of a G432, interface 3).
INTERFACE = 3

SIDETONE = 0x8300
FN_GET_SIDETONE = 0
FN_SET_SIDETONE = 1
#: What sidetone is, in the window's words and the CLI's: the owner read the window's
#: "Level" row as the headset's volume (2026-10-17).
SIDETONE_MEANS = "your voice in the earcups"
#: The level the feature takes: Solaar's Sidetone setting (0 to 100), and the device range
#: of the one other program that writes the G432's sidetone.
SIDETONE_RANGE = (0, 100)
#: What a reset writes where no read found a level before the first write.
SIDETONE_UNREAD_RESET = 0

#: The wait for a reply to any request but the probe's, each of its three tries:
#: Hidpp.request's own default.
REQUEST_TIMEOUT = 1.0

#: Every (feature id, function) `G432` sends the adapter; its `request` refuses any other
#: before anything is written. 0x0003's getDeviceInfo and getFwInfo are reads in
#: Logitech's x0003 device information v4 specification and in Solaar's hidpp20.py
#: (get_firmware); the adapter lists 0x0003 at index 0x02 and answered both (read
#: 2026-10-17 and 2026-10-18).
SENT = frozenset({(ROOT_FEATURE, 0), (SIDETONE, FN_GET_SIDETONE), (SIDETONE, FN_SET_SIDETONE),
                  (F_DEVICE_INFO, FN_DEVICE_INFO), (F_DEVICE_INFO, FN_FW_INFO)})


def find_g432() -> tuple[Path, int] | None:
    """The adapter's hidraw node and product id, read from sysfs by the same vid, pid and
    interface `70-g432.rules` matches; opens nothing."""
    found = find_attached(vendor=VENDOR_ID, product=tuple(PRODUCTS), interface=INTERFACE)
    return (found[0], found[1]) if found else None


def open_g432(path: str | os.PathLike[str]) -> G432:
    """Open the node and wrap it; raises PermissionError without the rule."""
    return G432(Hidpp(path, DEVICE_INDEX, long_only=True))


#: Why a sidetone request is refused on an adapter whose root lookup found no 0x8300.
NO_SIDETONE = ("The G432 did not list sidetone (HID++ feature 0x8300), so Gearbench cannot "
               "read or set it.")
#: Why a firmware read is refused before a root lookup found 0x0003.
NO_DEVICE_INFO = ("The G432 did not list its device information (HID++ feature 0x0003), so "
                  "Gearbench cannot read its firmware version.")


#: Why a sidetone level is refused, SIDETONE_RANGE's ends put in.
NOT_A_LEVEL = "The sidetone level is a whole number from {} to {}."


class UnreadableAnswer(HidppFailure):
    """The adapter answered with something Gearbench cannot read, such as a level outside
    SIDETONE_RANGE."""


def sidetone_level(value: object) -> int:
    """A sidetone level the feature takes, or ValueError saying what it takes."""
    low, high = SIDETONE_RANGE
    if not whole(value) or not low <= value <= high:
        raise ValueError(NOT_A_LEVEL.format(low, high))
    return value


class G432:
    """The adapter's HID++ channel, on a cabled device's index, and the requests in SENT."""

    def __init__(self, dev: Hidpp) -> None:
        self.dev = dev
        #: 0x8300 and 0x0003 as the root lookup found each: None before its lookup, and
        #: after one that found it absent.
        self.sidetone_feature: Feature | None = None
        self.device_info_feature: Feature | None = None

    @property
    def fileno(self) -> int:
        return self.dev.fileno

    def close(self) -> None:
        self.dev.close()

    def pump(self) -> None:
        """Read what is queued: the volume wheel's and the mute's reports share the node,
        and nothing here reads them."""
        self.dev.pump()

    def request(self, feature_id: int, function: int, params: bytes = b"", *,
                probe: bool = False) -> bytes:
        """One request of SENT, to the root's index 0 or to the index the lookup found, on
        the probe's budget with `probe`; ValueError for any other, or for 0x8300 or 0x0003
        before its lookup found it, before anything is written. Raises HidppFailure."""
        if (feature_id, function) not in SENT:
            raise ValueError(f"0x{feature_id:04x} function {function} is not a request "
                             "Gearbench sends the G432")
        index = 0 if feature_id == ROOT_FEATURE else self._index(feature_id)
        if probe:
            return self.dev.request(index, function, params, timeout=PROBE_TIMEOUT,
                                    retries=PROBE_RETRIES)
        return self.dev.request(index, function, params, timeout=REQUEST_TIMEOUT)

    def _index(self, feature_id: int) -> int:
        """The index the root lookup found for 0x8300 or 0x0003, SENT's other features;
        ValueError for one it has not found."""
        sidetone = feature_id == SIDETONE
        found = self.sidetone_feature if sidetone else self.device_info_feature
        if found is None:
            raise ValueError(NO_SIDETONE if sidetone else NO_DEVICE_INFO)
        return found.index

    def find_sidetone(self) -> Feature | None:
        """The root lookup of 0x8300, on the probe's budget, since it is the first request
        after the node opens; kept as `sidetone_feature`. Raises HidppFailure."""
        self.sidetone_feature = feature_of(
            self.request(ROOT_FEATURE, 0, SIDETONE.to_bytes(2, "big"), probe=True))
        return self.sidetone_feature

    def firmware(self) -> str | None:
        """The firmware that runs, its name and version as getFwInfo gives them, U1
        42.00.B0001 on the G432 read on 2026-10-17: the active main application of the
        entities getDeviceInfo counts, ENTITIES_MAX at most, after the root lookup of 0x0003,
        kept as `device_info_feature`. None where the adapter lists no 0x0003 or no such
        entity. Raises HidppFailure, UnreadableAnswer for an entry too short to decode."""
        self.device_info_feature = feature_of(
            self.request(ROOT_FEATURE, 0, F_DEVICE_INFO.to_bytes(2, "big")))
        if self.device_info_feature is None:
            return None
        count = self.request(F_DEVICE_INFO, FN_DEVICE_INFO)[0]
        for entity in range(min(count, ENTITIES_MAX)):
            entry = self.request(F_DEVICE_INFO, FN_FW_INFO, bytes([entity]))
            # A short report's three bytes, from a device that declares one.
            if len(entry) < FW_INFO_LENGTH:
                raise UnreadableAnswer(f"the G432 answered firmware entry {entity} with "
                                       f"{counted(len(entry), 'byte')}, not {FW_INFO_LENGTH}")
            found = firmware_entity(entry)
            if found.kind == MAIN_APPLICATION and found.active:
                return f"{found.prefix} {found.version}"
        return None

    def sidetone(self) -> int:
        """The level the adapter reports, byte 0 of function 0's reply, where Solaar's
        RangeValidator reads it. UnreadableAnswer for one outside SIDETONE_RANGE."""
        level = self.request(SIDETONE, FN_GET_SIDETONE)[0]
        low, high = SIDETONE_RANGE
        if not low <= level <= high:
            raise UnreadableAnswer(f"the G432 reported a sidetone level of {level}, outside "
                                   f"{low} to {high}")
        return level

    def set_sidetone(self, level: int) -> None:
        """Write `level`, checked first, as function 1's one byte."""
        self.request(SIDETONE, FN_SET_SIDETONE, bytes([sidetone_level(level)]))
