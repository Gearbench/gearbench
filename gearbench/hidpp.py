"""HID++ 2.0 transport over a hidraw node, and HID++ 1.0 registers for a receiver.

Speaks the vendor collection: report ID 0x10 (short, 7 bytes) and 0x11 (long, 20 bytes).
A cabled device answers on index 0xFF, one behind a receiver on its slot there, 0x01 for
the first: `attached_index` derives it from the topology where it can, and `open_device`
probes where it cannot, since a cabled device and a receiver's own interface look alike
there and a receiver answers on 0xFF itself. A receiver's node carries every paired
device's traffic, so a reply is matched on the device index as well as the feature and
function. Nothing here knows about lighting or keys. Feature indices are resolved through
the root feature (0x0000), never hardcoded: an index is what one firmware reports, not a
contract.
"""

from __future__ import annotations

import enum
import errno
import logging
import os
import select
import time
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import cycle
from pathlib import Path
from typing import Final, Self

log = logging.getLogger("gearbench")

#: Logitech. Which of its products this build knows is keyboard.py's and mouse.py's call.
VENDOR_ID = 0x046D
#: The kernel's BUS_USB. Every device this build drives is on USB, cabled or through a
#: receiver, and the udev rules grant no other bus.
BUS_USB = 0x03

REPORT_SHORT = 0x10
REPORT_LONG = 0x11
REPORT_LENGTH = {REPORT_SHORT: 7, REPORT_LONG: 20}

#: Errnos that mean the node is gone (DeviceGone), not a transient failure. Any other errno
#: is a plain HidppFailure, the device maybe still there. A USB link error (EPROTO, EILSEQ,
#: EPIPE, ETIMEDOUT) is one, the write between an unplug and the hub noticing it included;
#: the reads after it raise DeviceGone. request() and pump() raise only HidppFailure and its
#: subclasses, which the daemon's guards rely on; the exception is request()'s ValueError
#: for parameters too long for a report, a caller's mistake.
GONE_ERRNOS = frozenset({errno.ENODEV, errno.EIO, errno.ESHUTDOWN})

#: A cabled device's index, and only the default: one behind a receiver answers on its
#: slot, the G502 X PLUS on 0x01.
DEVICE_INDEX = 0xFF
#: A receiver's first slot: `attached_index`'s answer when a node does not name its slot.
RECEIVER_INDEX = 0x01
#: The highest slot hid-logitech-dj gives a paired device (DJ_DEVICE_INDEX_MAX).
DJ_SLOT_MAX = 7

# Software id, the function byte's low nibble. The device echoes it on replies and sends
# events with zero, which tells a reply from a notification. Each write takes the next id
# in rotation, so a late reply to a call that gave up is dropped, not taken as the next
# call's answer: with a fixed id, one late 0x8100 memoryRead reply shifted every following
# chunk of a sector image by one (measured on the mouse).
#
# The rotation skips the ids other software on the node matches its replies on: 0x01 the
# kernel's hid-logitech-hidpp, which probes the G502 X PLUS's node at every power-on report,
# as the daemon writes to it; 0x07 OpenRGB's; 0x0B Solaar's; 0x0F the firmware's own, for
# enumerating sub-devices on a cable. 0x01 is the kernel's LINUX_KERNEL_SW_ID; Solaar's list
# (lib/logitech_receiver/base.py) gives the others and also 0x0A and 0x0D, Windows programs'.
RESERVED_SOFTWARE_IDS = frozenset({0x01, 0x07, 0x0B, 0x0F})
SOFTWARE_IDS = tuple(i for i in range(0x01, 0x10) if i not in RESERVED_SOFTWARE_IDS)

ROOT_FEATURE = 0x0000

#: HID++ 2.0 error codes, from a reply with 0xFF in byte 2.
ERROR_NAMES = {
    0x00: "no error",
    0x01: "unknown",
    0x02: "invalid argument",
    0x03: "out of range",
    0x04: "hardware error",
    0x05: "logitech internal",
    0x06: "invalid feature index",
    0x07: "invalid function id",
    0x08: "busy",
    0x09: "unsupported",
}
ERROR_BUSY = 0x08

#: HID++ 1.0 error codes, from a reply with 0x8F in byte 2: a receiver's, or a device's for
#: a register request. The numbers differ from 2.0's: 0x08 is busy there, unknown device
#: here. Names from the kernel's hid-logitech-hidpp.c and Solaar's hidpp10_constants.py; the
#: kernel retries only 0x07 as busy.
HIDPP10_ERROR_NAMES = {
    0x00: "success",
    0x01: "invalid sub id",
    0x02: "invalid address",
    0x03: "invalid value",
    0x04: "connection request failed",
    0x05: "too many devices",
    0x06: "already exists",
    0x07: "busy",
    0x08: "unknown device",
    0x09: "resource error",
    0x0A: "request unavailable",
    0x0B: "invalid parameter value",
    0x0C: "wrong PIN code",
}
#: The HID++ 1.0 codes for a device not there to answer, sent by a receiver at once where a
#: request would time out: the c541 answered `unknown device` for a G915 switched off behind
#: it (2026-09-23). The kernel's hid-logitech-hidpp reads 0x08 and 0x09 as not connected
#: (hidpp_root_get_protocol_version); Solaar's ping reads 0x04 and 0x09 as unreachable.
HIDPP10_UNREACHABLE = frozenset({0x04, 0x08, 0x09})

#: The HID++ 1.0 sub-id of a receiver's notice that a paired device's link went down or up,
#: on a short report with the device's slot: byte 3 is the radio protocol, and the flags
#: byte after it has LINK_DOWN set while the link is down. The G502 X PLUS's receiver sent
#: `10 01 41 11 72 99 40` at switch-off and `10 01 41 11 b2 99 40` on return (2026-09-23),
#: `99 40` its wireless id. Matched exactly, unlike Solaar's 0x40 and up: a reply from
#: feature index 0x40 to 0x4F carries its index in the same byte.
LINK_NOTICE = 0x41
LINK_DOWN = 0x40

#: HID++ 1.0 register requests on a short report, `10 <index> <sub-id> <register> p0 p1 p2`.
#: The reply repeats the sub-id and register before the value; an error is `10 <index> 8f
#: <sub-id> <register> <code>`. The kernel's hid-logitech-hidpp.c (HIDPP_SET_REGISTER,
#: HIDPP_GET_REGISTER, struct rap, hidpp_match_error) and Solaar's hidpp10.py and base.py.
SET_REGISTER = 0x80
GET_REGISTER = 0x81

#: Register 0x00, the notification flags: three bytes, the second the receiver's. Its 0x01
#: has a receiver send LINK_NOTICE. Solaar's hidpp10_constants.py: NOTIFICATIONS 0x00,
#: NotificationFlag.WIRELESS 0x000100 of a big-endian 24-bit value. The kernel's
#: hid-logitech-dj.c writes `00 09 00` there, wireless and software present, to every
#: receiver it drives; the c541 is not one.
NOTIFICATION_FLAGS = 0x00
WIRELESS_NOTICES = 0x01

# Backoff for BUSY replies, ms. By the G915's firmware code (read 2026-09-23), BUSY comes
# from 0x8081 FrameEnd while the previous frame is pending; 0x8071 fn 1 has no busy check.
BUSY_BACKOFF_MS = (30, 60, 90)


class _Busy(enum.Enum):
    """What Hidpp._answer returns for a BUSY reply, for request() to back off from."""

    BUSY = enum.auto()


_BUSY: Final = _Busy.BUSY

#: How many sends (Hidpp.send) a channel keeps a reply for at once, well above what a
#: mouse's lighting sends before looking for the answers: an effect per cluster and a
#: frame's two. Past this the oldest are forgotten, so replies nobody collects cannot
#: pile up.
AWAITED_MAX = 32


class HidppFailure(Exception):
    """Base for a request that did not get a usable reply.

    Catch this, not HidppError alone: a dropped report times out where a rejected one
    errors, and the timeout would escape.
    """


class HidppError(HidppFailure):
    """The device replied with an error rather than a result.

    `code` is from HIDPP10_ERROR_NAMES when `hidpp10` (a 0x8F reply), else from ERROR_NAMES
    (0xFF). For a register request the first two arguments are its sub-id and register,
    and `about` names it in the message.
    """

    def __init__(self, feature_index: int, function: int, code: int,
                 hidpp10: bool = False, about: str | None = None) -> None:
        self.code = code
        self.hidpp10 = hidpp10
        if hidpp10:
            name = HIDPP10_ERROR_NAMES.get(code, "unnamed")
            detail = f"HID++ 1.0 error 0x{code:02x}"
        else:
            name = ERROR_NAMES.get(code, "unnamed")
            detail = f"0x{code:02x}"
        about = about or f"feature index 0x{feature_index:02x} fn {function}"
        super().__init__(f"{about}: {name} ({detail})")

    @property
    def unreachable(self) -> bool:
        """A receiver saying it cannot reach the device (HIDPP10_UNREACHABLE): switched off,
        out of range or gone to its cable. Callers treat it as a timeout, not a refusal."""
        return self.hidpp10 and self.code in HIDPP10_UNREACHABLE


class HidppTimeout(HidppFailure):
    """The device did not reply in time."""


def _no_reply(what: str, retries: int) -> HidppTimeout:
    """The timeout of `what`, sent `retries` + 1 times without an answer."""
    return HidppTimeout(f"{what}: no reply after {retries + 1} attempt{'s' if retries else ''}")


def not_there(exc: BaseException | None) -> bool:
    """Whether a failed read means the device is not there: a timeout, or its receiver's
    HidppError.unreachable, which comes at once where a timeout waits. None, an error with
    no cause behind it, is neither."""
    return isinstance(exc, HidppTimeout) or (isinstance(exc, HidppError) and exc.unreachable)


class DeviceGone(HidppFailure):
    """The hidraw node disappeared: unplugged, or the USB link reset.

    A HidppFailure, so a handler for a failed request also covers a device gone
    mid-request, most often a wireless mouse asleep while its Hidpp is built, which must
    not reach the top of the daemon.
    """


@dataclass(frozen=True)
class Feature:
    index: int
    type: int
    version: int


@dataclass(frozen=True)
class Event:
    """An unsolicited notification from the device, or with `link` its receiver's
    notice that the device's link went down or up (LINK_NOTICE): a short report
    whose byte 2 is 0x41, which a feature at index 0x41 also puts there."""

    feature_index: int
    address: int
    data: bytes
    link: bool = False


@dataclass(frozen=True)
class Heard:
    """What `pump` read: any HID++ report (0x10 or 0x11), and any other report, a
    mouse's motion through its receiver or a keystroke the kernel may forward to the
    mouse's node from a keyboard paired to the same receiver."""

    hidpp: bool = False
    input: bool = False


def usb_product(node: Path) -> int | None:
    """The idProduct of the USB device the hidraw node hangs off.

    A cabled device's own; for one paired through a receiver, the receiver's, since the
    paired device's node carries its own HID id under the receiver's USB device.
    `attached_index` reads that difference.
    """
    path = (node / "device").resolve()
    while path != path.parent:
        try:
            return int((path / "idProduct").read_text().strip(), 16)
        except (OSError, ValueError):
            path = path.parent
    return None


def attached_index(node: Path, product: int, phys: str) -> int:
    """Which HID++ device index answers on this node, from the topology.

    A node whose HID id matches its USB device's idProduct, or that has no USB device
    above it, is taken as cabled, on 0xFF; any other speaks through a receiver, on its
    slot. Measured: a cabled G915 is 0xC33E on 0xC33E and answers on 0xFF; a G502 X PLUS
    is 0x4099 on its 0xC547 receiver and answers on 0x01. So nothing here maps product ids
    to connections.

    The slot comes from `phys` (HID_PHYS): hid-logitech-dj names a paired device after its
    receiver with the slot appended, `...-8.4/input2:1` for this G502 X PLUS, and rewrites
    byte 1 of every write to that slot but not the replies, so a wrong slot drops every
    reply as another device's. RECEIVER_INDEX where the name carries no slot.
    """
    parent = usb_product(node)
    if parent is None or parent == product:
        return DEVICE_INDEX
    _, colon, slot = phys.rpartition(":")
    # Decimal digits only: isdigit() also passes a superscript, which int() refuses.
    if colon and slot.isdecimal():
        try:
            number = int(slot)
        except ValueError:      # past int()'s 4300 digits
            return RECEIVER_INDEX
        if 1 <= number <= DJ_SLOT_MAX:
            return number
    return RECEIVER_INDEX


def _uevent(node: Path) -> dict[str, str]:
    """The KEY=value lines of a hidraw node's HID device. Raises OSError."""
    # Decoded with replace: HID_NAME can hold any bytes (the kernel cuts a name at 127 bytes,
    # mid-character too), and no caller reads it. Split at "\n" alone, as the kernel ends
    # lines: splitlines() and text-mode reads also split at "\r", U+2028 and others a name
    # or serial can hold, and a line in the serial, written after HID_PHYS, could pose as it.
    # The last line of a key wins: a paired device's name, written before HID_PHYS, is the
    # words it gave its receiver, "\n" included, and its serial the kernel writes in hex.
    text = (node / "device" / "uevent").read_bytes().decode("utf-8", errors="replace")
    return dict(line.split("=", 1) for line in text.split("\n") if "=" in line)


#: The receiver slots `open_device` tries after `prefer`. HID++ numbers them from 1;
#: slot 7 (DJ_SLOT_MAX), which hid-logitech-dj also gives a paired device, is tried
#: only as `prefer`. 0xFF addresses the receiver itself, which answers but is not the
#: device.
RECEIVER_SLOTS = tuple(range(0x01, 0x07))

#: The budget for a request to a device that may be asleep, and what `open_device`
#: gives each index: three tries of half a second. A sleeping mouse answers the second
#: or third at about 650 ms, and an untouched one up to 1.63 s in G HUB's captures.
PROBE_TIMEOUT = 0.5
PROBE_RETRIES = 2

#: Why a hidraw node's open raised PermissionError: the end of every refusal the daemon
#: reports for one, and of probe's. uaccess gives a node to the account whose session
#: has the seat, so a rule in place refuses too while the seat is elsewhere.
NO_RULE = ("its udev rule is missing or not applied yet, or another account or a text "
           "console has the seat (see gearbench-ctl(1), FILES)")


def open_device(path: str | os.PathLike[str], witness: int,
                prefer: int = DEVICE_INDEX, *, timeout: float = PROBE_TIMEOUT,
                retries: int = PROBE_RETRIES) -> tuple[Hidpp, int] | None:
    """Open `path` and find the device index that owns `witness`.

    The topology cannot always tell: a cabled device and a receiver's vendor interface look
    the same, and a receiver answers on 0xFF itself (measured on the G915's c541: 0xFF
    answers with error 0x01, the keyboard is on slot 1). So the index is probed for
    `witness`, a feature the device has and a receiver lacks, `prefer` (the derived answer,
    right for everything cabled) first.

    Each index is asked once with `timeout` and `retries`, which cover the first request
    after opening a node, often dropped. A slot silent after an earlier index answered ends
    the scan: the receiver routes, so the device is off or out of range, and each further
    slot would cost the budget again, while connect() scans every 2 s. A timeout before any
    answer does not: a hid-logitech-dj node for one paired device rewrites every write to
    its slot, so the others time out.

    Returns the open device and its index, or None with everything it opened closed; a
    device gone mid-probe ends it at once. Raises PermissionError for a node the udev rule
    did not grant, so a caller can say so instead of reporting that nothing answered.
    """
    answered = False
    for index in (prefer, *(i for i in RECEIVER_SLOTS if i != prefer)):
        try:
            dev = Hidpp(path, device_index=index)
        except PermissionError:
            raise
        except OSError:
            return None
        try:
            if dev.feature(witness, timeout=timeout, retries=retries) is not None:
                return dev, index
            answered = True
        except DeviceGone:
            # Before HidppFailure, its base, or a gone node is reopened for every slot.
            dev.close()
            return None
        except HidppTimeout:
            if answered:
                dev.close()
                log.debug("slot 0x%02x on %s did not answer after the receiver did; "
                          "the scan stops there", index, path)
                return None
        except HidppFailure as exc:
            # An error reply is an answer: 0xFF on a receiver refuses the
            # witness lookup. A failed write is not.
            answered = answered or isinstance(exc, HidppError)
        dev.close()
    return None


#: The kernel's list of hidraw nodes, which `find_attached` walks.
HIDRAW_CLASS = Path("/sys/class/hidraw")


def find_attached(
    vendor: int = VENDOR_ID,
    product: int | Iterable[int] = (),
    interface: int | None = None,
) -> tuple[Path, int, int] | None:
    """The hidraw node for a vendor HID++ collection: (node, product, index).

    `product` may be several ids, tried in order, for a device with one id cabled and
    another through a receiver; the product found says which route it came by.

    Walks /sys, since /dev/hidrawN numbers move with plug order, and matches what the udev
    rule matches (USB, vid, pid, interface number), so a node found is one the rule grants.

    `interface` picks the HID++ interface where the others carry keystrokes: on a cable,
    and on a receiver like the keyboard's c541, which gives its device no node and carries
    its HID++ traffic on interface 02. A device with its own node behind a receiver, like
    a mouse on the c547, has no bInterfaceNumber, and its node is taken whatever
    `interface` says.
    """
    for pid in (product,) if isinstance(product, int) else product:
        # The ids from the HID device's own name, which the kernel formats (hid_add_device),
        # not from uevent's HID_ID: a name or serial holding "\n" writes a line of its own
        # there. USB alone: a Bluetooth device declares any vendor and product.
        want = f"{BUS_USB:04X}:{vendor:04X}:{pid:04X}."
        for node in sorted(HIDRAW_CLASS.glob("hidraw*")):
            # device -> HID device; its parent is the USB interface, which a
            # device paired through a receiver does not have.
            hid = (node / "device").resolve()
            if not hid.name.startswith(want):
                continue
            try:
                fields = _uevent(node)
            except OSError:
                continue
            try:
                number = int((hid.parent / "bInterfaceNumber").read_text().strip(), 16)
            except (OSError, ValueError):
                number = None
            if interface is not None and number is not None and number != interface:
                continue
            phys = fields.get("HID_PHYS", "")
            return Path("/dev") / node.name, pid, attached_index(node, pid, phys)
    return None


class HidrawNode:
    """An open hidraw node the channel owns: closed once, and read against a
    deadline through the subclass's `_raw_read`."""

    _fd: int

    def close(self) -> None:
        if self._fd >= 0:
            try:
                os.close(self._fd)
            finally:
                self._fd = -1

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def fileno(self) -> int:
        return self._fd

    def _raw_read(self) -> bytes | None:
        """One buffered report, or None when nothing is waiting: each node's own."""
        raise NotImplementedError

    def _read(self, deadline: float) -> bytes | None:
        """One report, or None once `deadline` passes."""
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            # No InterruptedError to catch: select retries with the time left (PEP 475).
            ready, _, _ = select.select([self._fd], [], [], remaining)
            if not ready:
                return None
            if (report := self._raw_read()) is not None:
                return report


class Hidpp(HidrawNode):
    """A synchronous request/reply channel with interleaved event delivery.

    Requests are answered in order: only one that waits is ever outstanding. A notification
    read while waiting is queued, since the keyboard sends G-key events at any time, mid
    lighting write too. A `send` waits for nothing, and its reply is kept whenever it arrives.
    """

    def __init__(self, path: str | os.PathLike[str], device_index: int) -> None:
        self._setup(Path(path), os.open(path, os.O_RDWR | os.O_NONBLOCK), device_index)

    @classmethod
    def from_fd(cls, fd: int, device_index: int = DEVICE_INDEX) -> Hidpp:
        """A channel over a descriptor somebody else opened, and now owns."""
        dev = cls.__new__(cls)
        dev._setup(Path(f"<fd {fd}>"), fd, device_index)
        return dev

    def _setup(self, path: Path, fd: int, device_index: int) -> None:
        self.path = path
        self.device_index = device_index
        self._fd = fd
        # Bounded: requests file events between the daemon's pops, and the CLI never pops.
        # Dropping the oldest is right: an event reports current state.
        self._events: deque[Event] = deque(maxlen=256)
        self._features: dict[int, Feature | None] = {}
        self._software_ids = cycle(SOFTWARE_IDS)
        #: Replies to `send`, by (feature index, function, software id): None
        #: until one comes, then its payload or the HidppError it carried.
        self._awaited: dict[tuple[int, int, int], bytes | HidppError | None] = {}

    # -- wire ------------------------------------------------------------

    def _write(self, feature_index: int, function: int, params: bytes, *, long: bool,
               drain: bool = True) -> int:
        """Send one request and return the software id it went out with.

        Too-long parameters raise ValueError before anything is read or written. `drain`
        False is a resend within one call, which reads the node itself first: what waits
        there may answer an earlier attempt.
        """
        report_id = REPORT_LONG if (long or len(params) > 3) else REPORT_SHORT
        max_params = REPORT_LENGTH[report_id] - 4
        if len(params) > max_params:
            raise ValueError(
                f"{len(params)} bytes of parameters exceed the {max_params} a "
                f"0x{report_id:02x} report carries"
            )
        if drain:
            self.pump()
        software_id = next(self._software_ids)
        body = bytes([report_id, self.device_index, feature_index, (function << 4) | software_id])
        self._put((body + params).ljust(REPORT_LENGTH[report_id], b"\x00"))
        return software_id

    def _put(self, packet: bytes) -> None:
        try:
            os.write(self._fd, packet)
        except OSError as exc:
            if exc.errno in GONE_ERRNOS:
                raise DeviceGone(str(exc)) from exc
            raise HidppFailure(f"write to the device failed: {exc}") from exc

    def _raw_read(self) -> bytes | None:
        """One buffered report, or None when nothing is waiting.

        With _put, the only place an OSError, or an empty read, becomes DeviceGone or
        another HidppFailure, which every caller's guard relies on. A HID++ report shorter
        than its id's REPORT_LENGTH is dropped, so later code can index a whole report: the
        kernel hands hidraw a short report at the length it came in.
        """
        while True:
            try:
                report = os.read(self._fd, 64)
            except BlockingIOError:
                return None
            except OSError as exc:
                if exc.errno in GONE_ERRNOS:
                    raise DeviceGone(str(exc)) from exc
                raise HidppFailure(f"read from the device failed: {exc}") from exc
            if not report:
                # hidraw never reads empty; a descriptor whose far end closed does (from_fd).
                raise DeviceGone("the node closed")
            if len(report) >= REPORT_LENGTH.get(report[0], 0):
                return report
            log.debug("dropped truncated report %s", report.hex(" "))

    def pump(self) -> Heard:
        """Read whatever is buffered, without sending or waiting, and say what kinds of
        report it was. Every request, send and register request calls it before its first
        write.

        Events are queued and a reply a send awaits is kept for `answer`; any other reply
        is nobody's, typically the late answer to a call that gave up, and is dropped so it
        cannot pass for the next call's answer.
        """
        hidpp = other = False
        while (report := self._raw_read()) is not None:
            if report[0] in REPORT_LENGTH:
                hidpp = True
            else:
                other = True
            self._file_or_drop(report)
        return Heard(hidpp, other)

    def _file_or_drop(self, report: bytes,
                      waiting: tuple[int, int, list[int]] | None = None) -> None:
        """Queue a notification, keep the reply to a send, and drop the rest, another
        device's reports included. `waiting` is a waiting request's feature index, function
        and software ids, for the line that logs a reply dropped as stale."""
        if report[0] not in REPORT_LENGTH or report[1] != self.device_index:
            return
        if report[2] not in (0xFF, 0x8F):
            link = self._is_link_notice(report)
            if link or self._is_event(report):
                self._events.append(Event(feature_index=report[2], address=report[3],
                                          data=report[4:], link=link))
                return
        if self._keep_awaited(report):
            return
        if waiting is not None:
            self._log_stale(report, *waiting)
        else:
            log.debug("dropped unsolicited reply %s", report.hex(" "))

    def _keep_awaited(self, report: bytes) -> bool:
        """Keep a reply, or an error reply, that a send is waiting for, and
        say whether it was one. The report's device index is already ours."""
        failed = report[2] in (0xFF, 0x8F)
        index, function_byte = self._addressed(report)
        key = (index, function_byte >> 4, function_byte & 0x0F)
        if key not in self._awaited or self._awaited[key] is not None:
            return False
        self._awaited[key] = (HidppError(index, key[1], report[5], hidpp10=report[2] == 0x8F)
                              if failed else report[4:])
        return True

    @staticmethod
    def _addressed(report: bytes) -> bytes:
        """A reply's feature index and function byte: bytes 2 and 3, or 3 and 4 in an error.
        HID++ 2.0 error: [id, dev, 0xFF, feature, fn|swid, code]; HID++ 1.0 error:
        [id, dev, 0x8F, sub, address, code], the request's feature index and function byte,
        so both forms match the same way."""
        return report[3:5] if report[2] in (0xFF, 0x8F) else report[2:4]

    @staticmethod
    def _is_link_notice(report: bytes) -> bool:
        """A receiver's link notice (LINK_NOTICE), which _is_event misses: its
        byte 3 is the radio protocol, 0x11 on LIGHTSPEED, not a function and
        software id."""
        return report[0] == REPORT_SHORT and report[2] == LINK_NOTICE

    @staticmethod
    def _is_event(report: bytes) -> bool:
        # Events carry software id zero. Meaningless on an error report (0xFF or 0x8F in
        # byte 2), whose byte 3 is a feature index: callers check for errors first.
        return (report[3] & 0x0F) == 0

    def next_event(self) -> Event | None:
        return self._events.popleft() if self._events else None

    @property
    def pending(self) -> bool:
        """Whether notifications are queued. A request files the ones it reads
        while waiting for its reply, and leaves the fd with nothing to wake
        select for."""
        return bool(self._events)

    # -- requests --------------------------------------------------------

    def send(self, feature_index: int, function: int, params: bytes = b"", *,
             long: bool = False) -> tuple[int, int, int]:
        """Write one request and return at once, without waiting for its reply.

        For a device slow to answer: an untouched G502 X PLUS answered 0.42 to 1.63 s after
        a request in G HUB's captures. The reply is kept under the key this returns, read
        by `answer` and dropped by `forget`. Sent once: a caller wanting an answer by a time
        keeps that time itself.
        """
        key = (feature_index, function, self._write(feature_index, function, params, long=long))
        self._awaited.pop(key, None)
        self._awaited[key] = None
        while len(self._awaited) > AWAITED_MAX:
            del self._awaited[next(iter(self._awaited))]
        return key

    def answer(self, key: tuple[int, int, int]) -> bytes | HidppError | None:
        """The reply to a send: its payload, the HidppError it carried, or
        None while none has come. Kept until `forget`."""
        return self._awaited.get(key)

    def forget(self, *keys: tuple[int, int, int]) -> None:
        """Stop keeping these sends' replies; one that comes later is dropped as nobody's."""
        for key in keys:
            self._awaited.pop(key, None)

    def request(
        self,
        feature_index: int,
        function: int,
        params: bytes = b"",
        *,
        timeout: float = 1.0,
        long: bool = False,
        retries: int = 2,
        reply: bool = True,
    ) -> bytes:
        """Call one feature function and return its reply payload, without the 4-byte header.

        Raises HidppError on a device error, HidppTimeout if nothing comes back, DeviceGone
        if the node went away.

        reply=False sends once and returns b"" without waiting, as Lighting.set_effect does
        for a color preview synced to the mouse (0x8071 fn 1 setEffectByIndex); the mouse does
        answer it, and an error in that answer goes unseen. A reply that arrives is drained
        before the next write.

        Every write, retries and BUSY resends included, takes a fresh software id, and a
        reply echoing any id this call sent is its answer: the attempts are one request, so
        the late answer to an earlier one, as an untouched mouse gives, is as good as the
        resend's. A resend first reads what arrived since the last wait instead of draining
        it. A reply to the same feature and function with an id this call never sent is
        dropped as the late answer to an earlier call, unless a `send` awaits it: then it is kept.

        A BUSY reply is answered work, so it costs a backoff from BUSY_BACKOFF_MS, not one of
        `retries`. Once the backoffs are spent the BUSY is raised as HidppError: a device
        that answered every time did not time out.
        """
        if not reply:
            self._write(feature_index, function, params, long=long)
            return b""

        sent: list[int] = []
        busy_attempt = 0
        attempt = 0
        while attempt <= retries:
            answer = self._answer(feature_index, function, sent, None) if sent else None
            if answer is None:
                sent.append(self._write(feature_index, function, params, long=long,
                                        drain=not sent))
                answer = self._answer(feature_index, function, sent,
                                      time.monotonic() + timeout)
            if answer is None:
                attempt += 1  # timed out; resend
            elif answer is _BUSY:
                if busy_attempt == len(BUSY_BACKOFF_MS):
                    raise HidppError(feature_index, function, ERROR_BUSY)
                time.sleep(BUSY_BACKOFF_MS[busy_attempt] / 1000.0)
                busy_attempt += 1
            else:
                return answer

        raise _no_reply(f"feature index 0x{feature_index:02x} fn {function}", retries)

    def _answer(self, feature_index: int, function: int, sent: list[int],
                deadline: float | None) -> bytes | _Busy | None:
        """This call's answer: its payload, _BUSY, or None once `deadline` passes.

        `sent` is every software id the call has written with. `deadline` None reads only
        what is buffered, as a resend does before it goes out. An error reply other than a
        HID++ 2.0 BUSY is raised as HidppError; reports for others go to _file_or_drop.
        """
        while True:
            report = self._raw_read() if deadline is None else self._read(deadline)
            if report is None:
                return None
            # Another paired device's reply can share our feature index and function, and
            # the node carries input reports too: a G502 X PLUS's motion report (id 0x02)
            # with the left button down has the slot index in byte 1, and a small movement
            # can match a pending request byte for byte.
            if report[0] in REPORT_LENGTH and report[1] == self.device_index:
                failed = report[2] in (0xFF, 0x8F)
                index, function_byte = self._addressed(report)
                # Tested before _file_or_drop sees the report, which would queue a short
                # reply from a feature at index 0x41 as a link notice. No id sent is zero,
                # so an event never passes.
                if (index == feature_index and function_byte >> 4 == function
                        and function_byte & 0x0F in sent):
                    if not failed:
                        return report[4:]
                    # A 1.0 error is never taken for BUSY: its 0x08 is an unknown
                    # device, which the kernel reads as one that is not connected.
                    if report[2] == 0xFF and report[5] == ERROR_BUSY:
                        return _BUSY
                    raise HidppError(feature_index, function, report[5],
                                     hidpp10=report[2] == 0x8F)
            self._file_or_drop(report, (feature_index, function, sent))

    @staticmethod
    def _log_stale(report: bytes, feature_index: int, function: int, sent: list[int]) -> None:
        function_byte = Hidpp._addressed(report)[1]
        log.debug(
            "dropped stale reply with software id 0x%x while feature index 0x%02x fn %d "
            "waited for %s: %s", function_byte & 0x0F, feature_index, function,
            ", ".join(f"0x{i:x}" for i in sent), report.hex(" "),
        )

    # -- HID++ 1.0 registers --------------------------------------------

    def read_register(self, register: int, *, timeout: float = 1.0, retries: int = 2) -> bytes:
        """A short register's three bytes (GET_REGISTER)."""
        return self._register(GET_REGISTER, register, bytes(3), timeout, retries)

    def write_register(self, register: int, value: bytes, *, timeout: float = 1.0,
                       retries: int = 2) -> None:
        """Write a short register's three bytes (SET_REGISTER), returning once it is answered."""
        if len(value) != 3:
            raise ValueError(f"a short register takes 3 bytes, not {len(value)}")
        self._register(SET_REGISTER, register, value, timeout, retries)

    def _register(self, sub_id: int, register: int, value: bytes, timeout: float,
                  retries: int) -> bytes:
        """One register request on index 0xFF, the receiver itself on a receiver's node
        whatever slot this handle speaks to, sent again after each silent `timeout`.

        No software id, since byte 3 is the register: the answer is matched on device index,
        sub-id and register, as the kernel and Solaar match it, so a late answer to an
        earlier attempt counts. Anything else read meanwhile is filed as request() files it.
        """
        what = f"register 0x{register:02x} {'write' if sub_id == SET_REGISTER else 'read'}"
        self.pump()
        for _attempt in range(retries + 1):
            self._put(bytes([REPORT_SHORT, DEVICE_INDEX, sub_id, register]) + value)
            deadline = time.monotonic() + timeout
            while (report := self._read(deadline)) is not None:
                if report[0] == REPORT_SHORT and report[1] == DEVICE_INDEX:
                    if report[2:4] == bytes([sub_id, register]):
                        return report[4:7]
                    if report[2:5] == bytes([0x8F, sub_id, register]):
                        raise HidppError(sub_id, register, report[5], hidpp10=True, about=what)
                self._file_or_drop(report)
        raise _no_reply(what, retries)

    # -- feature discovery ----------------------------------------------

    def feature(self, feature_id: int, *, timeout: float = 1.0,
                retries: int = 2) -> Feature | None:
        """Resolve a feature id to its index on this device, or None if absent.

        `timeout` and `retries` are request()'s, for a lookup that is a probe.
        """
        if feature_id not in self._features:
            self._features[feature_id] = self.lookup(feature_id, timeout=timeout,
                                                     retries=retries)
        return self._features[feature_id]

    def lookup(self, feature_id: int, *, timeout: float = 1.0,
               retries: int = 2) -> Feature | None:
        """A feature id's index, or None if absent, asked of the device each time, for a
        probe that must reach it; `feature` caches the answer."""
        reply = self.request(ROOT_FEATURE, 0, feature_id.to_bytes(2, "big"),
                             timeout=timeout, retries=retries)
        return None if reply[0] == 0 else Feature(index=reply[0], type=reply[1],
                                                  version=reply[2])

    def require(self, feature_id: int, name: str) -> Feature:
        found = self.feature(feature_id)
        if found is None:
            raise RuntimeError(
                f"this firmware has no 0x{feature_id:04x} {name}; "
                f"Gearbench cannot drive it"
            )
        return found
