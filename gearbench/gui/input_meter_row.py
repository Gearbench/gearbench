"""The Input level row, the last of each headset page's Microphone group: a level bar of the
headset's microphone, read live by pw-record (gearbench.input_meter) on GNOME's scale.

It reads only while the row is on screen, mapped, which it is while its page shows on the
Headset tab of a window not hidden, and while that window is not suspended (polls.py says
when Mutter suspends one); and only while its page names a headset (`show_headset`). It
stops at once when any of these ends, and at the window's close (`close`). One pw-record
at most runs for the row. The microphone's node is looked up with pw-dump on the worker
thread as the row starts, and pw-record's samples are read on the main loop as its pipe has
them. Building it runs nothing.
"""

from __future__ import annotations

import subprocess
from typing import cast

from gi.repository import Adw, GLib, GObject, Gtk

from .. import input_meter, pipewire_client
from ..wording import reason
from .style import SLIDER_WIDTH, displayable, percent, plain_row
from .worker import DaemonWorker, Result

TITLE = "Input level"
#: The bar's name for a screen reader: what it measures.
BAR_NAME = "Microphone input level"
#: The row's line where it cannot read: the node not in PipeWire, pw-dump failed (the cause
#: goes in), and pw-record ended on its own (its words in the tooltip). pw-record missing or
#: not starting says so in pipewire_client.not_run's words. NOT_FOUND is also the volume
#: rows' line, Mic input's above this one.
NOT_FOUND = "Not in PipeWire"
NOT_READ = "PipeWire could not be read: {}"
STOPPED = f"{input_meter.PROGRAM} stopped"
#: What the pipe's watch wakes for: samples, or pw-record's end of it closed.
WOKEN_BY = GLib.IOCondition.IN | GLib.IOCondition.HUP | GLib.IOCondition.ERR


class InputMeterRow:
    """The row and its bar (`row`, `bar`), for the page of the headset `show_headset` names.
    `run` runs pw-dump and `spawn` starts pw-record."""

    def __init__(self, worker: DaemonWorker, run: pipewire_client.Run = subprocess.run,
                 spawn: input_meter.Spawn = subprocess.Popen) -> None:
        self._worker = worker
        self._run = run
        self._spawn = spawn
        #: The headset the page names, a key of spatial.HEADSET_KEYED; None for none.
        self.key: str | None = None
        self._looking = self._closed = False
        #: The window the row is on while mapped, None while it is not, and its
        #: notify::suspended handler.
        self._window: Gtk.Window | None = None
        self._suspended_handler = 0
        self.recording: input_meter.Recording | None = None
        #: The pipe's watch while `recording` is set.
        self._watch = 0
        #: The percent the bar last said to a screen reader.
        self._said: int | None = None
        self.row = plain_row(Adw.ActionRow, TITLE, "")
        # GNOME's: 0 to 1 in one color, with no low or high marks (cc-level-bar.c).
        self.bar = Gtk.LevelBar(min_value=0, max_value=1, valign=Gtk.Align.CENTER)
        for offset in (Gtk.LEVEL_BAR_OFFSET_LOW, Gtk.LEVEL_BAR_OFFSET_HIGH,
                       Gtk.LEVEL_BAR_OFFSET_FULL):
            self.bar.remove_offset_value(offset)
        self.bar.set_size_request(SLIDER_WIDTH, -1)
        self.bar.update_property([Gtk.AccessibleProperty.LABEL], [BAR_NAME])
        self._show(0.0)
        self.row.add_suffix(self.bar)
        self.row.connect("map", self._on_map)
        self.row.connect("unmap", self._on_unmap)

    def show_headset(self, key: str | None) -> None:
        """The headset the page names now, None once it has none: the meter starts for it
        where the row is on screen, again after a failure, and stops for none or another."""
        if key != self.key:
            self.key = key
            self._stop()
            self._say("")
        self._update()

    def close(self) -> None:
        """The window's close: stopped, and never started again."""
        self._closed = True
        self._let_go_of_window()
        self._stop()

    def _wanted(self) -> str | None:
        """The headset whose microphone the row reads now: none while it is off screen, its
        window suspended or closed."""
        if self._window is None or self._closed or self._window.is_suspended():
            return None
        return self.key

    def _update(self) -> None:
        key = self._wanted()
        if key is None:
            self._stop()
        else:
            self._start(key)

    def _on_map(self, _row: Gtk.Widget) -> None:
        # A row is mapped only inside a window, its root.
        window = self._window = cast(Gtk.Window, self.row.get_root())
        self._suspended_handler = window.connect("notify::suspended", self._on_suspended)
        self._update()

    def _on_unmap(self, _row: Gtk.Widget) -> None:
        self._let_go_of_window()
        self._update()

    def _on_suspended(self, _window: Gtk.Window, _pspec: GObject.ParamSpec) -> None:
        self._update()

    def _let_go_of_window(self) -> None:
        if self._window is not None:
            self._window.disconnect(self._suspended_handler)
            self._window = None

    def _start(self, key: str) -> None:
        """Look `key`'s microphone up on the worker, unless pw-record runs or a look is out."""
        if self.recording is not None or self._looking:
            return
        self._looking = True
        self._worker.run(lambda: input_meter.find(self._run, key),
                         lambda result: self._found(result, key))

    def _found(self, result: Result, key: str) -> None:
        """The look's node: pw-record started on it while the row still wants `key`'s
        microphone; else the look again, for the headset named since, or nothing."""
        self._looking = False
        if self._wanted() != key:
            self._update()
        elif isinstance(result, Exception):
            self._say(NOT_READ.format(reason(result)))
        elif not isinstance(result, str):
            self._say(NOT_FOUND)
        else:
            self._record(result)

    def _record(self, node: str) -> None:
        """pw-record started on `node`, its pipe watched; or why not, in the row."""
        try:
            recording = input_meter.Recording.start(node, self._spawn)
        except pipewire_client.StepError as err:
            self._say(str(err))
            return
        self.recording = recording
        self._watch = GLib.io_add_watch(recording.fileno(), GLib.PRIORITY_DEFAULT, WOKEN_BY,
                                        self._on_samples, recording)
        self._say("")

    def _on_samples(self, _fd: int, _condition: GLib.IOCondition,
                    recording: input_meter.Recording) -> bool:
        """What the pipe holds, onto the bar; at its end, pw-record's words in the row."""
        level, ended = recording.read()
        if level is not None:
            self._show(level)
        if not ended:
            return True
        self.recording = None
        self._say(STOPPED, recording.stop())
        self._show(0.0)
        return False

    def _stop(self) -> None:
        """pw-record stopped and its watch gone, the bar at 0."""
        if self.recording is not None:
            GLib.source_remove(self._watch)
            recording, self.recording = self.recording, None
            recording.stop()
        self._show(0.0)

    def _show(self, level: float) -> None:
        """The bar at `level`, and its value in words while the percent changes."""
        self.bar.set_value(level)
        shown = round(level * 100)
        if shown != self._said:
            self._said = shown
            self.bar.update_property([Gtk.AccessibleProperty.VALUE_TEXT], [percent(shown)])

    def _say(self, line: str, detail: str | None = None) -> None:
        self.row.set_subtitle(displayable(line))
        self.row.set_tooltip_text(detail)
