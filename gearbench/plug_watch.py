"""The device nodes as they are made, changed and removed in /dev, from inotify(7), so the
daemon looks for a device as its node appears and not on a timer alone. Standard library only:
inotify_init1 and inotify_add_watch through ctypes, the events read with os.read.

What the kernel and udev guarantee, read in their sources on 2026-10-24: the kernel makes a
node in devtmpfs, root's and mode 0600, before it sends the uevent udev acts on
(drivers/base/core.c device_add, drivers/base/devtmpfs.c; drivers/hid/hidraw.c's class sets
no mode). udev then applies its rules, the uaccess ACL of 73-seat-late.rules among them, set
with acl_set_file (systemd's src/shared/acl-util.c devnode_acl); a chmod, a chown and an ACL
each come to inotify as IN_ATTRIB on the node (include/linux/fsnotify.h fsnotify_change and
fsnotify_xattr, fs/posix_acl.c vfs_set_acl). So a node's IN_CREATE comes before this user may
open it, and an IN_ATTRIB comes as udev grants it. On unplug the kernel wakes the node's
readers before it removes the node (hidraw.c drop_ref), so a reader hears its device go no
later than the IN_DELETE.
"""

from __future__ import annotations

import ctypes
import os
import struct
from pathlib import Path

#: Where the kernel makes the device nodes.
DEV = Path("/dev")

#: inotify(7)'s event bits: metadata changed (a mode, an owner, an ACL), a name made in the
#: folder, a name removed from it, and the queue overflowed, which names no node.
IN_ATTRIB = 0x00000004
IN_CREATE = 0x00000100
IN_DELETE = 0x00000200
IN_Q_OVERFLOW = 0x00004000
#: What the watch asks for: a write to a node, which the daemon makes all the time, is
#: IN_MODIFY and is not asked for.
WATCHED = IN_CREATE | IN_ATTRIB | IN_DELETE

#: struct inotify_event's fixed part, wd, mask, cookie and len, each followed by len bytes of
#: its name, padded with NULs (inotify(7)).
EVENT = struct.Struct("=iIII")
#: The most one read takes. The kernel hands over whole events only, each at most EVENT.size
#: plus NAME_MAX + 1 bytes; what is left stays in the kernel's queue for the next read.
READ_SIZE = 64 * 1024


def open_watch(folder: Path) -> int:
    """A non-blocking descriptor that reports the names made, changed and removed in
    `folder` (WATCHED), closed on exec. OSError where the kernel refuses: EMFILE past the
    account's fs.inotify.max_user_instances or this process's open files, ENOSPC past
    max_user_watches, EACCES on a folder this account may not read."""
    libc = ctypes.CDLL(None, use_errno=True)
    # inotify_init1 takes O_NONBLOCK and O_CLOEXEC as IN_NONBLOCK and IN_CLOEXEC (inotify(7)).
    descriptor: int = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
    if descriptor < 0:
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number))
    if libc.inotify_add_watch(descriptor, os.fsencode(folder), WATCHED) < 0:
        number = ctypes.get_errno()
        os.close(descriptor)
        raise OSError(number, os.strerror(number), str(folder))
    return descriptor


def read_events(descriptor: int) -> list[tuple[str, int]]:
    """What one read of the watch `descriptor` gives, each event's name and mask; none where
    nothing is waiting."""
    try:
        return parsed(os.read(descriptor, READ_SIZE))
    except BlockingIOError:
        return []


def parsed(data: bytes) -> list[tuple[str, int]]:
    """Each inotify event in `data`, as (its name, its mask), the name "" where it has none,
    as IN_Q_OVERFLOW's."""
    events = []
    at = 0
    while at + EVENT.size <= len(data):
        _watch, mask, _cookie, length = EVENT.unpack_from(data, at)
        name = data[at + EVENT.size:at + EVENT.size + length].split(b"\0", 1)[0]
        events.append((os.fsdecode(name), mask))
        at += EVENT.size + length
    return events
