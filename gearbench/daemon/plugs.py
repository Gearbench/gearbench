"""The daemon's watch on /dev (plug_watch): a hidraw node made and open to this user, removed,
or opened to it by udev brings each look that takes its pace from Plugs.pace forward to the
next pass, where it runs as its timer would run it, and the tray's refresh with them. While the
watch runs, those looks are a backstop every LOOK_BACKSTOP; without it, as when the account has
used up its inotify instances, they keep their own pace.

Any account can put events on the watch: utimensat(2) lets an account that may write a node
set its times, every account may write /dev/null, and each such call is an IN_ATTRIB on the
watch (inotify(7)). So the watch is read at most once a READ_GAP, and a full queue runs the
looks CABLE_LOOK, their pace without the watch, after the watch last ran them, or at the time
a node made or an earlier full queue already set (pump)."""

from __future__ import annotations

import errno
import logging
import os
import selectors
import time
from typing import TYPE_CHECKING, cast

from ..plug_watch import DEV, IN_CREATE, IN_Q_OVERFLOW, open_watch, read_events
from ..wording import reason
from .shared import CABLE_LOOK, look_due, node_denied

if TYPE_CHECKING:
    from . import Daemon

log = logging.getLogger("gearbench")

#: The names of the nodes the looks find: the kernel's hidraw class names them "hidraw%d"
#: (drivers/hid/hidraw.c).
NODE_PREFIX = "hidraw"

#: Seconds between the timed looks while the watch runs, a backstop for a node the watch did
#: not report (a full queue reports IN_Q_OVERFLOW, which runs the looks within CABLE_LOOK,
#: pump). 30 s, the pace of the retry of a missing virtual device (VIRTUAL_RETRY), for two
#: walks of sysfs a minute.
LOOK_BACKSTOP = 30.0

#: Seconds the watch goes unread after each read (pump), what comes meanwhile waiting in the
#: kernel's queue: however fast any account brings events (the module's docstring), the loop
#: reads the watch once a READ_GAP at most, a pass for the read and one for the gap's end.
#: 50 ms is then the most a node's event is heard late, where the looks without the watch
#: take up to CABLE_LOOK.
READ_GAP = 0.05

#: The causes of open_watch's refusals whose strerror names another: inotify_init1(2)'s
#: EMFILE is the account's inotify instances used up, or this process's descriptors, and
#: inotify_add_watch(2)'s ENOSPC the account's watches, not a full disk.
WATCH_LIMITS: dict[int | None, str] = {
    errno.EMFILE: "past fs.inotify.max_user_instances or the open-file limit",
    errno.ENOSPC: "past fs.inotify.max_user_watches"}


class Plugs:
    """The watch, and whether the looks are due at the next pass (`came`)."""

    def __init__(self, daemon: Daemon) -> None:
        self.d = daemon
        #: The watch's descriptor while it runs, else None.
        self.fd: int | None = None
        #: A node came, went or was opened to this user since the looks last ran.
        self._news = False
        #: When the looks run for the nodes made that this user could not open yet, CABLE_LOOK
        #: after the first, whether or not udev's grant came before, or for a full queue
        #: (pump); None until either comes, and again once that time is past (came).
        self._grant_by: float | None = None
        #: When the watch is read again after a read (READ_GAP), None while the loop reads it.
        self._read_at: float | None = None
        #: When a full queue runs the looks where _grant_by is None (pump): CABLE_LOOK after
        #: the watch last ran them (came), 0.0 until it first has.
        self._overflow_by = 0.0

    def start(self) -> None:
        """Start the watch, or say why the looks keep their own pace."""
        try:
            self.fd = open_watch(DEV)
        except OSError as exc:
            log.warning("cannot watch %s for devices plugged in (%s); looking for them every "
                        "%g s instead", DEV, WATCH_LIMITS.get(exc.errno, reason(exc)),
                        CABLE_LOOK)
            return
        self._listen()

    def _listen(self) -> None:
        """Have the loop read the watch when it has events."""
        self.d.selector.register(cast("int", self.fd), selectors.EVENT_READ, "plug")

    def pace(self, unwatched: float) -> float:
        """Seconds between a timed look's runs: LOOK_BACKSTOP while the watch runs, else its
        own `unwatched`."""
        return unwatched if self.fd is None else LOOK_BACKSTOP

    def pump(self) -> None:
        """Read what the watch reported. A hidraw node removed, or there and open to this
        user, makes the looks due; one made that this user may not open yet, as the kernel
        makes every node before udev grants it, is looked for at the IN_ATTRIB of udev's
        grant, and at most CABLE_LOOK, the looks' own pace, after it was made in any case: one
        made while another waits shares that one's time. So a node udev never grants is found
        and its refusal said as soon as without the watch. A full queue makes them due
        CABLE_LOOK after the watch last ran them, at once where that is past, or shares the
        time a node made or an earlier full queue set. The watch then goes unread for READ_GAP
        (came)."""
        # The loop reads the watch only while it runs and is not in its gap: start and came
        # register it, this and close unregister it.
        fd = cast("int", self.fd)
        now = time.monotonic()
        self.d.selector.unregister(fd)
        self._read_at = now + READ_GAP
        for name, mask in read_events(fd):
            if mask & IN_Q_OVERFLOW:
                if self._grant_by is None:
                    log.debug("the watch on %s lost events; looking for devices within %g s",
                              DEV, CABLE_LOOK)
                    self._grant_by = self._overflow_by
            elif not name.startswith(NODE_PREFIX):
                continue
            elif not node_denied(DEV / name):
                log.debug("%s changed (inotify %#x); looking for devices now", DEV / name, mask)
                self._news = True
            elif mask & IN_CREATE and self._grant_by is None:
                log.debug("%s made, not yet open to Gearbench; waiting for udev", DEV / name)
                self._grant_by = now + CABLE_LOOK

    def deadlines(self) -> list[float]:
        """When a node made waits no longer for udev's grant, or a full queue for its looks
        (pump), and when the watch's gap ends."""
        return [at for at in (self._grant_by, self._read_at) if at is not None]

    def came(self) -> bool:
        """Whether the looks run at this pass (pump), once; and the watch read again once
        its gap is past."""
        now = time.monotonic()
        # Not look_due: its slack would shorten the gap.
        if self._read_at is not None and now >= self._read_at:
            self._listen()
            self._read_at = None
        if self._grant_by is not None and look_due(now, self._grant_by):
            self._news, self._grant_by = True, None
        news, self._news = self._news, False
        if news:
            self._overflow_by = now + CABLE_LOOK
        return news

    def close(self) -> None:
        """Stop the watch, at the daemon's stop, before its selector closes."""
        if self.fd is None:
            return
        # In its gap the loop reads it no more (pump).
        if self._read_at is None:
            self.d.selector.unregister(self.fd)
        os.close(self.fd)
        self.fd = None
