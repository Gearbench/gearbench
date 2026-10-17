"""Reading one device's evdev node without grabbing it.

Used for the mouse's own /dev/input/eventN, which udev/70-g502x.rules gives the gearbench
group to read (GROUP), so nothing here needs root. Hard rules:

- Never EVIOCGRAB: GNOME keeps seeing the ordinary buttons and the pointer.
- Nodes are found in /sys/class/input, never by opening each /dev/input/event*: sysfs
  touches no device, and its vendor:product is the udev rule's match.
- After SYN_DROPPED the reader catches up with the kernel's key state (`_resync`): a lost
  release would leave a DPI shift on, or BTN_SIDE down on the virtual mouse.
- The reader takes key events only (EVIOCSMASK), as each motion report, up to 1000 a
  second, woke the daemon; filtered, it leaves an empty packet, which the kernel drops
  without a wakeup. The mask is this reader's alone: the desktop's pointer is untouched.
"""

from __future__ import annotations

import ctypes
import fcntl
import grp
import logging
import os
import pwd
import struct
import sys
from collections.abc import Iterator
from pathlib import Path

from .config import reason
from .hidpp import GONE_ERRNOS, DeviceGone
from .uinput import EV_KEY, EV_SYN, EVENT, IOC_READ, IOC_WRITE, KEY_MAX, SYN_REPORT, ioc

SYN_DROPPED = 3

#: Bytes in the key-state bitmap, KEY_MAX + 1 bits.
KEY_BITMAP = (KEY_MAX + 1) // 8
#: EVIOCGKEY(len): _IOC(_IOC_READ, 'E', 0x18, len).
EVIOCGKEY = ioc(IOC_READ, ord("E"), 0x18, KEY_BITMAP)
#: EVIOCSMASK: _IOC(_IOC_WRITE, 'E', 0x93, sizeof(struct input_mask)), and
#: that struct: the event type the mask is for, its size in bytes, and a
#: pointer to its bits. For type EV_SYN the bits are the event types.
INPUT_MASK = struct.Struct("=IIQ")
EVIOCSMASK = ioc(IOC_WRITE, ord("E"), 0x93, INPUT_MASK.size)
#: The type mask's length, one long, which holds every event type (EV_MAX is 0x1f): the
#: kernel takes a mask in whole longs and refuses anything else with EINVAL (bits_from_user).
MASK_BYTES = ctypes.sizeof(ctypes.c_ulong)

log = logging.getLogger("gearbench")

SYS_INPUT = Path("/sys/class/input")

#: The group udev/70-g502x.rules gives the G502 X PLUS's event nodes, and their mode: the
#: group reads them and cannot write events to them (its ioctls still work).
GROUP = "gearbench"
MODE = 0o640


def find_event_nodes(vendor: int, product: int) -> list[Path]:
    """Every /dev/input/event* whose input device carries this vendor:product."""
    found = []
    for node in sorted(SYS_INPUT.glob("event*")):
        ids = node / "device" / "id"
        try:
            vid = int((ids / "vendor").read_text().strip(), 16)
            pid = int((ids / "product").read_text().strip(), 16)
        except (OSError, ValueError):
            continue
        if (vid, pid) == (vendor, product):
            found.append(Path("/dev/input") / node.name)
    return found


def group_denied(path: Path | str) -> str | None:
    """Why this process may not read `path`, a node with the rule's MODE that GROUP reads;
    else None.

    Under PrivateUsers=yes the node's group and this process's groups all read as the
    overflow gid, though the kernel still grants by them, so the mode tells the rule's node
    from another, and the group database who is in GROUP. The daemon has the groups its user
    manager started with: an account added since reads the node only once that manager
    restarts, which lingering puts off to the machine's next restart. A node no rule grants,
    such as the cabled mouse's keyboard collection, is refused by design.
    """
    try:
        mode = os.stat(path).st_mode & 0o777
        members = grp.getgrnam(GROUP).gr_mem
    except (OSError, KeyError):
        return None
    if mode != MODE:
        return None
    then = "log out of every session and back in, or, with lingering on, restart the machine"
    try:
        user = pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        # The group lists its members by name, and usermod takes one: neither works on a uid.
        return (f"uid {os.getuid()} has no name this service can look up: run "
                f"sudo usermod -aG {GROUP} $USER, then {then}")
    if user in members:
        return f"{user} joined the {GROUP} group after this service's user manager started: {then}"
    return f"{user} is not in the {GROUP} group: run sudo usermod -aG {GROUP} {user}, then {then}"


def parse_events(buffer: bytes) -> Iterator[tuple[int, int, int]]:
    """Yield (type, code, value) for each whole input_event in `buffer`."""
    size = EVENT.size
    for offset in range(0, len(buffer) - size + 1, size):
        _sec, _usec, type_, code, value = EVENT.unpack_from(buffer, offset)
        yield type_, code, value


class EventReader:
    """A non-blocking reader for one evdev node."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        #: Key codes this reader has reported pressed and not yet released.
        self._down: set[int] = set()
        self._keys_only()

    def _keys_only(self) -> None:
        """Have the kernel queue only key events for this reader (EV_SYN is never filtered).
        A kernel or node that refuses the mask passes everything, which read() handles; the
        journal says so."""
        # The kernel reads the mask as longs in the machine's byte order (bits_from_user).
        types = (1 << EV_SYN) | (1 << EV_KEY)
        bits = ctypes.create_string_buffer(types.to_bytes(MASK_BYTES, sys.byteorder), MASK_BYTES)
        mask = INPUT_MASK.pack(EV_SYN, MASK_BYTES, ctypes.addressof(bits))
        try:
            fcntl.ioctl(self._fd, EVIOCSMASK, mask)
        except OSError as exc:
            log.info("%s passes every event to this reader, motion included: %s",
                     self.path, reason(exc))

    @property
    def fileno(self) -> int:
        return self._fd

    def read(self) -> list[tuple[int, int, int]]:
        """Drain what is buffered. Raises DeviceGone once the node is unplugged.

        The node is non-blocking and never reads as end of file, so a drained
        one raises BlockingIOError, and that is where the batch is resynced.
        """
        events: list[tuple[int, int, int]] = []
        while True:
            try:
                chunk = os.read(self._fd, EVENT.size * 64)
            except BlockingIOError:
                break
            except OSError as exc:
                if exc.errno in GONE_ERRNOS:
                    raise DeviceGone(reason(exc)) from exc
                raise
            if not chunk:
                break
            events.extend(parse_events(chunk))
        return self._resync(events)

    def _resync(self, events: list[tuple[int, int, int]]) -> list[tuple[int, int, int]]:
        """The events, with an overflow turned back into a consistent state: SYN_DROPPED to
        the next SYN_REPORT is a partial packet, thrown away as the evdev documentation says,
        and presses and releases are added for the keys that differ from the kernel's state."""
        out: list[tuple[int, int, int]] = []
        dropped = skipping = False
        for type_, code, value in events:
            if type_ == EV_SYN and code == SYN_DROPPED:
                dropped = skipping = True
                continue
            if skipping:
                if type_ == EV_SYN and code == SYN_REPORT:
                    skipping = False
                continue
            out.append((type_, code, value))
            if type_ == EV_KEY and value == 1:
                self._down.add(code)
            elif type_ == EV_KEY and value == 0:
                self._down.discard(code)
        if dropped:
            out.extend(self._key_changes())
        return out

    def _key_changes(self) -> list[tuple[int, int, int]]:
        """Presses and releases that bring what was reported to the kernel's state."""
        try:
            bits = fcntl.ioctl(self._fd, EVIOCGKEY, bytes(KEY_BITMAP))
        except OSError:
            # Raising would lose the batch, a release read before the drop included. The
            # next read that fails says whether the node went.
            return []
        # The kernel writes the state as longs in the machine's byte order (bits_to_user).
        size = MASK_BYTES * 8
        words = [int.from_bytes(bits[i:i + MASK_BYTES], sys.byteorder)
                 for i in range(0, KEY_BITMAP, MASK_BYTES)]
        now = {code for code in range(KEY_BITMAP * 8) if words[code // size] >> (code % size) & 1}
        changes = ([(EV_KEY, code, 0) for code in sorted(self._down - now)]
                   + [(EV_KEY, code, 1) for code in sorted(now - self._down)])
        self._down = now
        return changes

    def close(self) -> None:
        if self._fd >= 0:
            try:
                os.close(self._fd)
            finally:
                self._fd = -1
