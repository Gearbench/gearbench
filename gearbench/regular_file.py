"""Opening a file to read only if it is a regular file, on the standard library alone ("The
file rules")."""

from __future__ import annotations

import errno
import os
import stat

#: The cause a read gives where /proc/self/fd, which open_regular opens a file through, is
#: not mounted.
NO_PROC_FD = "no /proc/self/fd to reopen it through"


def open_regular(path: str | os.PathLike[str]) -> int | None:
    """A descriptor open for reading on `path`, a regular file or a link to one, or None for
    anything else. The path is resolved once, with O_PATH, which runs no driver's open
    (open(2)), and the type is read on that descriptor; a regular file is then opened
    through it (/proc/self/fd), the same file, so nothing put at the path meanwhile is
    opened. A device's open acts on the device (a serial port's raises DTR, which can reset
    the board behind it), and a link in a file from elsewhere, such as a -wal beside a G HUB
    database, can name one, or be re-pointed at one by whoever may write its folder. The
    open does not block: under a write lease the file's owner holds, one that blocks waits
    out the lease break time, 45 s by default (/proc/sys/fs/lease-break-time). OSError
    passes through, naming `path`."""
    located = os.open(path, os.O_PATH)
    try:
        if not stat.S_ISREG(os.fstat(located).st_mode):
            return None
        try:
            return os.open(f"/proc/self/fd/{located}", os.O_RDONLY | os.O_NONBLOCK)
        except FileNotFoundError:
            # The file is held open, so only a missing /proc/self/fd fails so: never a file
            # not there, which a caller of read_json takes for no file at all.
            raise OSError(errno.ENOSYS, NO_PROC_FD, os.fspath(path)) from None
        except OSError as exc:
            exc.filename = os.fspath(path)    # not /proc/self/fd/N, which a refusal shows
            raise
    finally:
        os.close(located)
