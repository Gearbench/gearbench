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
    from gi.repository import Adw, GObject, Gtk

    from .window import Window


class Polls:
    """The three poll timers, started and stopped as the window comes and goes.

    `tick` reads status once, unless a read is still out or a drag is on.
    """

    #: The screen is locked or blanked (`session_locked`, `screen_blanked`), which stops the
    #: device polls. A daemon found down clears it: no daemon reports a lock.
    _away = False
    #: The window has shown once (on_presence).
    _shown = False

    def __init__(self, window: Window, tick: Callable[[], None]) -> None:
        self.win = window
        self._tick = tick
        #: The poll timers by name ("status", "mouse", "headset"), run only while the window
        #: is on screen (on_presence).
        self._timers: dict[str, int] = {}
        #: The polls that were stopped, which read once when they start again.
        self._paused: set[str] = set()
        #: Whether the window was active when last looked at, or None until it first is
        #: (on_presence).
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
        mouse or the A50 asked if its tab shows, even with an unasked read still out; the
        A50 not while a save runs (HeadsetPage.poll). The groups every headset page shows
        and each headset page attached wait for the read they have out. The first show reads
        Outputs as on_tab does (_read_outputs_for_balance), once for the window, and nothing
        more, nor does the first activation: the window read status, the mouse and the
        headset when built.
        """
        shown = self.win.get_mapped() and not self.win.is_suspended()
        if shown and not self._shown:
            self._shown = True
            self._read_outputs_for_balance()
        active = shown and self.win.is_active()
        back = active and self._was_active is False
        if active or self._was_active is not None:
            self._was_active = active
        devices = shown and not self._away
        status = self._run_poll("status", shown, 3, self._status_poll)
        mouse = self._run_poll("mouse", devices, 5, self._mouse_tick)
        headset = self._run_poll("headset", devices, 5, self._headset_tick)
        if status or back:
            self._tick()
        # tick skips while the poll's unasked read is out, so not through it unless mid-drag.
        if back and devices and self.on_mouse_tab() and not self.win.dragging:
            self.win.mouse.refresh()
        elif mouse or (back and devices):
            self._mouse_tick()
        if back and devices and self.on_headset_tab() and not self.win.dragging:
            self._read_headset_tab()
        elif headset or (back and devices):
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
        """Whether a read the window makes on its own, not for a click or write, asks the mouse."""
        return self.on_mouse_tab() and self.win.is_active()

    def on_headset_tab(self) -> bool:
        return self.win.stack.get_visible_child_name() == "headset"

    def on_tab(self, _stack: Adw.ViewStack, _pspec: GObject.ParamSpec) -> None:
        """Read the mouse or the headset as its tab comes up: off it, the
        poll asks the daemon only for what it already holds. Outputs first
        (_read_outputs_for_balance): queued before the mouse's read on the one worker thread,
        it lands before that reply shows the rows."""
        self._read_outputs_for_balance()
        if self.on_mouse_tab():
            self.win.mouse.refresh()
        elif self.on_headset_tab():
            self._read_headset_tab()

    def on_page(self, _stack: Gtk.Stack, _pspec: GObject.ParamSpec) -> None:
        """On the Headset tab, read the groups every headset page shows as the page that
        comes up shows them, and each headset page while its headset is attached, as on_tab
        does."""
        if self.on_headset_tab():
            self._read_pages()

    def _read_outputs_for_balance(self) -> None:
        """Read Outputs while the Keyboard or Mouse tab shows, for their entries, rows and G keys
        on a balance word, which say whether it runs (OutputsGroup.balance_unheard)."""
        if self.win.stack.get_visible_child_name() in ("keyboard", "mouse"):
            self.win.outputs.refresh()

    def _read_headset_tab(self) -> None:
        """The Headset tab's reads as it comes into view: the A50's page as
        HeadsetPage.poll says, then _read_pages."""
        self.win.headset.poll()
        self._read_pages()

    def _read_pages(self) -> None:
        """The groups every headset page shows (Window.headset_groups), and each headset page
        while its headset is attached (HeadsetStack.poll), each unless a read of theirs is
        out."""
        for group in self.win.headset_groups:
            group.refresh()
        self.win.headset_tab.poll(True)

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
        on_tab = self.on_headset_tab()
        self.win.headset.tick(self.win.dragging, ask=on_tab and self.win.is_active())
        # Only on its tab: every read runs systemctl and pw-dump.
        if on_tab:
            for group in self.win.headset_groups:
                group.tick()
        self.win.headset_tab.poll(on_tab)
        return True

    def take_away(self, status: dict) -> None:
        """Take `session_locked` and `screen_blanked`: the device polls stop while either
        holds (on_presence), and read again once neither does, a daemon found down included."""
        away = status.get("session_locked") is True or status.get("screen_blanked") is True
        if away != self._away:
            self._away = away
            self.on_presence()
