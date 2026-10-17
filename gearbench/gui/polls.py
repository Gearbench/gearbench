"""The window's polls: which of the three timers run, and what each asks.

This part owns the status, mouse and headset timers, whether the window was active when
last looked at, and whether the screen is locked or blanked. The status read itself, and
what a reply is taken for, stay the window's.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from gi.repository import GLib

if TYPE_CHECKING:                       # pragma: no cover
    from gi.repository import Adw, GObject

    from .window import Window


class Polls:
    """The three poll timers, started and stopped as the window comes and goes.

    `tick` reads status once, unless a read is still out or a drag is on.
    """

    #: The screen is locked or blanked (`session_locked`, `screen_blanked`), which stops the
    #: device polls. A daemon found down clears it: no daemon reports a lock.
    _away = False

    def __init__(self, window: Window, tick: Callable[[], None]) -> None:
        self.win = window
        self._tick = tick
        #: The poll timers by name ("status", "mouse", "headset"), run only while the window
        #: is on screen (on_presence).
        self._timers: dict[str, int] = {}
        #: The polls that were stopped, which read once when they start again.
        self._paused: set[str] = set()
        #: Whether the window was active when last looked at, or None until it first is: that
        #: first time reads nothing, as the window read everything when built.
        self._was_active: bool | None = None

    def stop(self) -> None:
        """Remove every timer, for the close: a timeout source holds the window."""
        for source in self._timers.values():
            GLib.source_remove(source)
        self._timers = {}

    def on_presence(self, *_args: object) -> None:
        """Run the polls only while the window is on screen, focused or not: the owner's
        decision of 2026-09-25, "Refresh while visible". While the window is not the active
        one the mouse and headset polls ask with `ask` false, so the daemon answers from what
        it holds and sends the device nothing.

        All stop when the window is unmapped or suspended, and the device polls also while the
        last status says the screen is locked or blanked. Mutter (50) sends the xdg_toplevel
        `suspended` state 3 s after nothing of a window is left to see: minimized, on another
        workspace, covered, or behind the lock screen, which GNOME's idle blank raises too. On
        Wayland that is a client's only word of being minimized. The status poll runs on
        through a lock, since the next status is how the window hears of the unlock before
        Mutter suspends it.

        A poll started again reads at once, and so does the window becoming active again, the
        mouse or the headset asked if its tab shows, even with an unasked read still out; the
        headset not while a save runs (HeadsetPage.poll). The first show or activation reads
        nothing more.
        """
        shown = self.win.get_mapped() and not self.win.is_suspended()
        active = shown and self.win.is_active()
        back = active and self._was_active is False
        if active or self._was_active is not None:
            self._was_active = active
        status = self._run_poll("status", shown, 3, self._status_poll)
        mouse = self._run_poll("mouse", shown and not self._away, 5, self._mouse_tick)
        headset = self._run_poll("headset", shown and not self._away, 5, self._headset_tick)
        if status or back:
            self._tick()
        # Back on its tab the device is asked (the headset not while a save runs) even while
        # the poll's unasked read is still out, which tick skips as pending; mid-drag tick
        # skips it as it always does.
        if (back and "mouse" in self._timers and self.on_mouse_tab()
                and not self.win.dragging):
            self.win.mouse.refresh()
        elif mouse or (back and "mouse" in self._timers):
            self._mouse_tick()
        if (back and "headset" in self._timers and self.on_headset_tab()
                and not self.win.dragging):
            self.win.headset.poll()
            self.win.spatial.tick()
        elif headset or (back and "headset" in self._timers):
            self._headset_tick()

    def _run_poll(self, name: str, wanted: bool, seconds: int,
                  tick: Callable[[], bool]) -> bool:
        """Start or stop one poll's timer. True when it starts again after
        a stop, for the caller to read at once."""
        running = self._timers.get(name)
        if wanted and running is None:
            self._timers[name] = GLib.timeout_add_seconds(seconds, tick)
            if name in self._paused:
                self._paused.discard(name)
                return True
        elif not wanted and running is not None:
            GLib.source_remove(self._timers.pop(name))
            self._paused.add(name)
        return False

    def on_mouse_tab(self) -> bool:
        return self.win.stack.get_visible_child_name() == "mouse"

    def asks_mouse(self) -> bool:
        """Whether a read the window makes on its own, not for a click or a write, asks the
        mouse: only on its tab of the active window. Elsewhere the daemon answers from what it
        holds (on_presence)."""
        return self.on_mouse_tab() and self.win.is_active()

    def on_headset_tab(self) -> bool:
        return self.win.stack.get_visible_child_name() == "headset"

    def on_tab(self, _stack: Adw.ViewStack, _pspec: GObject.ParamSpec) -> None:
        """Read the mouse or the headset as its tab comes up: off it, the
        poll asks the daemon only for what it already holds. The Headset tab
        asks as HeadsetPage.poll says; its Spatial sound group reads every
        time."""
        if self.on_mouse_tab():
            self.win.mouse.refresh()
        elif self.on_headset_tab():
            self.win.headset.poll()
            self.win.spatial.refresh()

    def _status_poll(self) -> bool:
        """The 3 s status poll, which leaves status to the macro banner's one-second watch
        while that runs."""
        if not self.win.macro_banner.watching:
            self._tick()
        return True

    def _mouse_tick(self) -> bool:
        # Off its tab or inactive, the poll still asks the daemon, which brings a silent mouse's
        # recheck and an absent one's reconnect forward, with nothing sent to the mouse.
        self.win.mouse.tick(self.win.dragging, ask=self.asks_mouse())
        return True

    def _headset_tick(self) -> bool:
        self.win.headset.tick(self.win.dragging,
                              ask=self.on_headset_tab() and self.win.is_active())
        # Only on its tab: every read runs systemctl and pw-dump.
        if self.on_headset_tab():
            self.win.spatial.tick()
        return True

    def take_away(self, status: dict) -> None:
        """Take `session_locked` and `screen_blanked`: the device polls stop while either
        holds (on_presence), and read again once neither does, a daemon found down included."""
        away = status.get("session_locked") is True or status.get("screen_blanked") is True
        if away != self._away:
            self._away = away
            self.on_presence()
