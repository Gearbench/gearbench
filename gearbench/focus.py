"""Which program has focus, for game profiles.

Two sources, the first in force wins:

1. Gearbench's GNOME Shell extension (gnome-shell/gearbench-focus@wmuschetto.github.io),
   through desktop.py: the pid of Mutter's focused window, native Wayland windows
   included, which SDL3 games open under GNOME for its Wayland fifo protocol.
2. Xwayland's _NET_ACTIVE_WINDOW (xfocus.py): X11 windows only, a Proton game's among
   them unless it runs Wine's Wayland driver. Its connection is kept while the extension
   runs, so a stopped extension falls back at once.

With neither, game profiles follow the running programs (games.py).

The extension's changes settle as XFocus's do (`xfocus.Settle`): Alt-Tab passes through
windows, and a handoff to a window that takes focus itself, as Wine's do, may pass
through none. Its first report and its stop act at once.
"""

from __future__ import annotations

import logging
import selectors
from collections.abc import Callable

from .xfocus import BY_RUNNING_PROGRAMS, Settle, XFocus

log = logging.getLogger("gearbench")

#: `status`'s focus sources: the Shell extension, Xwayland, and the running programs.
SHELL, XWAYLAND, PROCESSES = "gnome-shell", "xwayland", "processes"
#: The sources under which a game profile follows the focused window.
FOLLOWED = (SHELL, XWAYLAND)


def held_until(program: str, followed: bool) -> str:
    """When a hold-off that `program` holds ends, `followed` being whether game profiles
    follow focus."""
    return f"focus moves away from {program}" if followed else f"{program} exits"


class Focus:
    """The focused program, from the Shell extension while it is in force, else Xwayland.

    `x_factory(changed)` builds the xfocus.XFocus. `changed()` is called when focus comes
    into force, goes out of it, moves to another source, or settles on another process.
    """

    def __init__(self, changed: Callable[[], None],
                 x_factory: Callable[[Callable[[], None]], XFocus], *,
                 clock: Callable[[], float]) -> None:
        self._changed = changed
        self._clock = clock
        self.x = x_factory(self._x_changed)
        self.x.standby = lambda: self.shell
        #: Whether the extension is in force: it reported, and has not stopped since.
        self.shell = False
        self._shell_pid: int | None = None
        #: Its latest report, while a change settles.
        self._reported: int | None = None
        #: When that report settles.
        self._settle = Settle()

    @property
    def ready(self) -> bool:
        """Whether focus is in force, from either source."""
        return self.shell or self.x.ready

    @property
    def pending(self) -> bool:
        """The first X connection is being made, and the extension is not in force."""
        return not self.shell and self.x.pending

    @property
    def pid(self) -> int | None:
        """The settled focus, None for no program in the foreground."""
        return self._shell_pid if self.shell else self.x.pid

    @property
    def source(self) -> str:
        """`status`'s focus source: gnome-shell, xwayland, or processes."""
        if self.shell:
            return SHELL
        return XWAYLAND if self.x.ready else PROCESSES

    # ------------------------------------------------------------ the loop

    def start(self, selector: selectors.BaseSelector, tag: object) -> bool:
        return self.x.start(selector, tag)

    def dispatch(self, mask: int) -> None:
        self.x.dispatch(mask)

    def deadline(self) -> float | None:
        due = [when for when in (self.x.deadline(), self._settle.at) if when is not None]
        return min(due) if due else None

    def tick(self) -> None:
        self.x.tick()
        if self._settle.due(self._clock()) and self._reported != self._shell_pid:
            self._shell_pid = self._reported
            self._changed()

    def close(self) -> None:
        self.x.close()
        self._stop_shell()

    # ------------------------------------------------------ the extension

    def shell_focus(self, pid: int | None) -> None:
        """The extension's focus, 0 for no window; None once it is not
        running: stopped, switched off, or the bus gone."""
        if pid is None:
            if not self.shell:
                return
            self._stop_shell()
            log.info("Gearbench's GNOME Shell extension stopped; %s",
                     "following window focus on Xwayland" if self.x.ready
                     else BY_RUNNING_PROGRAMS)
            self._changed()
            return
        if not self.shell:
            self.shell = True
            self._shell_pid = pid or None
            log.info("following window focus through Gearbench's GNOME Shell extension")
            self._changed()
            return
        self._reported = pid or None
        self._settle.change(self._clock())

    def _stop_shell(self) -> None:
        self.shell = False
        self._settle.clear()

    def _x_changed(self) -> None:
        """XFocus changed: it counts only while the extension is not in force."""
        if not self.shell:
            self._changed()
