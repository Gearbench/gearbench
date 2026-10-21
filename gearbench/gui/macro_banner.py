"""The window's macro banner: which macro runs or counts down, and its Stop.

This part owns the banner, what it shows and the one-second watch that counts it down and
asks whether the macro still runs. Its window builds it, places the banner, hands it each
`status` reply and removes the watch at the close; the Macros tab hands it Play's
`macro_run` reply.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from gi.repository import Adw, GLib

from ..wording import number
from .macro_page import REPEAT_CHOICES, REPEAT_LABELS

if TYPE_CHECKING:                       # pragma: no cover
    from .worker import Call

#: The macro banner, up while a macro runs or counts down. Stop sends `macro_stop`.
MACRO_STARTS = "Macro {name} starts in {seconds} s. Click into the window it is for."
MACRO_RUNNING = "Macro {name} is running ({mode})"
STOP_LABEL = "Stop"
#: The repeat modes as the banner names them, the editor's labels in lower case.
MACRO_MODES = dict(zip(REPEAT_CHOICES, (label.lower() for label in REPEAT_LABELS),
                       strict=True))
#: How often the banner counts down and asks whether the macro still runs.
MACRO_WATCH_MS = 1000


class MacroBanner:
    """The banner that names the running macro, with its Stop.

    `call` is the window's, for `macro_stop`. `tick` reads status once, as the window's
    status poll does, and `suspended` says whether the window is: nobody sees it then.
    """

    #: The banner's timer while it is up (_on_watch); the close removes it.
    _watch: int | None = None

    def __init__(self, call: Call, tick: Callable[[], None],
                 suspended: Callable[[], bool]) -> None:
        self._call = call
        self._tick = tick
        self._suspended = suspended
        # Up while a macro runs or counts down, however it started: a toggled or played macro
        # has no key held to stop it. Not markup: the title carries the macro's name.
        self.banner = Adw.Banner(button_label=STOP_LABEL, use_markup=False)
        self.banner.connect("button-clicked", self._on_stop)
        #: What the banner shows: the running macro as (name, repeat mode, its first
        #: step's time.monotonic(), or None once begun), or None.
        self.running: tuple[str, str, float | None] | None = None

    @property
    def watching(self) -> bool:
        """The watch runs, and reads status every second: the status poll leaves it to that."""
        return self._watch is not None

    def close(self) -> None:
        """Remove the watch, which holds the window."""
        if self._watch is not None:
            GLib.source_remove(self._watch)
            self._watch = None

    def take(self, reply: dict) -> None:
        """The macro banner from a `status` or `macro_run` reply, which carry the same three
        fields. `macro_starts_in` becomes a time on this clock, so the banner counts down
        between two replies."""
        name, starts_in = reply.get("macro"), reply.get("macro_starts_in")
        if not isinstance(name, str) or not name:
            self.running = None
        else:
            self.running = (
                name, str(reply.get("macro_repeat") or ""),
                time.monotonic() + starts_in if number(starts_in) and starts_in > 0 else None)
        self.show()

    def show(self) -> None:
        """Put `running` on the macro banner, and keep the watch
        running while it is up."""
        if self.running is None:
            self.banner.set_revealed(False)
            return
        name, repeat, due = self.running
        left = math.ceil(due - time.monotonic()) if due is not None else 0
        self.banner.set_title(
            MACRO_STARTS.format(name=name, seconds=left) if left > 0
            else MACRO_RUNNING.format(name=name, mode=MACRO_MODES.get(repeat, repeat)))
        self.banner.set_revealed(True)
        if self._watch is None:
            self._watch = GLib.timeout_add(MACRO_WATCH_MS, self._on_watch)

    def _on_watch(self) -> bool:
        """Every second the banner is up, count it down and ask whether the macro still runs.

        Active or not, since Play's countdown is for clicking into another window; not while
        suspended, where nobody sees it, and coming back reads once.
        """
        if self.running is None:
            self._watch = None
            return False
        self.show()
        if not self._suspended():
            self._tick()
        return True

    def _on_stop(self, _banner: Adw.Banner) -> None:
        """The banner's Stop: whatever macro runs, however it started."""
        self._call({"command": "macro_stop"},
                   lambda reply: self.take({}) if reply is not None else None)
