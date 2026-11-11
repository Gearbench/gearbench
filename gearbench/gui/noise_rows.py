"""The Noise suppression row of each headset page's Microphone group (gearbench.headset_mic):
a switch on the noise suppression of the headset the page names, which the window runs
itself, its programs on the worker thread, as the Spatial sound group's are. It shows only for
a headset whose noise suppression runs on the computer (headset_mic.WORDS), and its state is
read only while it is on screen, mapped, as Mic input's is: as it comes on screen, as the
page names another headset and at each of the page's polls (`refresh`), but while its own
switch runs, whose end reads it. Test the microphone under it counts COUNT_IN, then records
TEST_SECONDS of the cleaned input, counting down, and plays them back (headset_mic.Take,
Playback), on the main loop: the count is a timer, pw-record's pipe is watched as Input
level's is, and pw-play's end looked at each PLAY_LOOK_MS. The switch shows its unit's state
and words a switch that raised as unit_switch does. Building it runs nothing.
"""

from __future__ import annotations

import contextlib
import subprocess
import time
from collections.abc import Callable
from typing import cast

from gi.repository import Adw, GLib, GObject, Gtk

from .. import headset_mic, input_meter, pipewire_client
from ..wording import capitalized, counted
from .input_meter_row import WOKEN_BY
from .style import (
    COULD_NOT_READ,
    name_button,
    plain_row,
    put_dot,
    status_dot,
    toast_clause,
    toast_note,
)
from .unit_switch import NOT_STARTED_OF, NOT_STOPPED_OF, show_switch, switch_failure
from .worker import DaemonWorker, LoadingGuard, Result

#: The switch's tooltip, the input's name in.
HELP_OF = "Makes {} the default input until you switch it off or log out"
#: The switch's toast line when its failure is too long for a toast.
NOT_STARTED = NOT_STARTED_OF.format(headset_mic.FEATURE)
NOT_STOPPED = NOT_STOPPED_OF.format(headset_mic.FEATURE)
#: The test's row, its button, its line, its line as it counts before it records, by the
#: number counted, while it records, by the seconds left, and while it plays back (the
#: owner's decision of 2026-10-28), and its toast's line for a failure too long for a toast.
TEST_TITLE = "Test the microphone"
TEST_OPENER = "Test"
TEST_LINE = f"Records {headset_mic.TEST_SECONDS} seconds, then plays them back"
COUNTING = "Recording in {}"
RECORDING = "Recording: {} left"
PLAYING = "Playing back…"
NOT_TESTED = "The microphone test did not finish"
#: How often pw-play's end is looked for, and the count's step, in ms.
PLAY_LOOK_MS = 100
COUNT_STEP_MS = headset_mic.COUNT_STEP * 1000


class NoiseRows:
    """The Noise suppression switch (`row`) and its dot, and Test the microphone
    (`test_row`) and its Test button (`test_button`), for the headset `show_headset` names.
    `run` runs the switch's programs and `spawn` the test's, pw-record and pw-play; `toast`
    shows a failure, and `kept_up` is told of a switch's (Window.kept_up)."""

    def __init__(self, worker: DaemonWorker, toast: Callable[..., object],
                 kept_up: Callable[[], None], run: pipewire_client.Run = subprocess.run,
                 spawn: input_meter.Spawn = subprocess.Popen) -> None:
        self._worker = worker
        self._toast = toast
        self._kept_up = kept_up
        self._run = run
        self._spawn = spawn
        #: The number the test's count shows while it counts, else 0; its recording while it
        #: records; its playback while it plays; and the GLib sources that drive them: the
        #: pipe's watch and the timer.
        self._count = 0
        self._take: headset_mic.Take | None = None
        self._playback: headset_mic.Playback | None = None
        self._watch = self._timer = 0
        #: When the playback's time is up, by time.monotonic.
        self._play_until = 0.0
        #: The headset the test runs for, the one named at its click.
        self._tested = ""
        self._loading = LoadingGuard()
        #: The headset the page names, a key of pipewire_headsets.HEADSET_KEYED; None for none.
        self.key: str | None = None
        #: The state's kind (headset_mic.summary), None while the state of `key` is unknown.
        self.kind: str | None = None
        #: A read is out; a switch runs, whose end reads the state; the window has closed.
        self._reading = self._acting = self._closed = False
        self.row = plain_row(Adw.SwitchRow, headset_mic.FEATURE, "", visible=False)
        self.dot = status_dot()
        self.row.connect("notify::active", self.on_switch)
        self.row.connect("map", lambda _row: self.refresh())
        # A button starts it, as Copy and Delete… do theirs: an activatable row with no
        # control looked like the readings beside it.
        self.test_row = plain_row(Adw.ActionRow, TEST_TITLE, TEST_LINE, visible=False)
        self.test_button = Gtk.Button(label=TEST_OPENER, valign=Gtk.Align.CENTER)
        name_button(self.test_button, TEST_TITLE)
        self.test_button.connect("clicked", self.on_test)
        self.test_row.add_suffix(self.test_button)
        self.test_row.set_activatable_widget(self.test_button)
        self._set_sensitive()

    def show_headset(self, key: str | None) -> None:
        """The headset the page's last reply names, None for none: the row shows, under the
        headset's input's name, for one whose noise suppression runs on the computer, grayed
        out until a read gives its state; the last headset's state is dropped, its words and
        dot too, and this one's read where the row is on screen (refresh); a read out for the
        last is read again as it lands (_took)."""
        if key == self.key:
            return
        self.key, self.kind = key, None
        words = headset_mic.WORDS.get(key) if key is not None else None
        self.row.set_visible(words is not None)
        self.test_row.set_visible(words is not None)
        if words is not None:
            self.row.set_tooltip_text(HELP_OF.format(words.description))
        self.row.set_subtitle("")
        put_dot(self.row, self.dot, None, "")
        self._set_sensitive()
        # The read asked for as the row came on screen can land for the last headset before
        # the reply that names this one; unread, the row stays blank until the next poll.
        self.refresh()

    def refresh(self) -> None:
        """Read the state on the worker, at the page's poll, as the row comes on screen and as
        the page names another headset (show_headset): unless a read is out, the window has
        closed, the row is off screen, which it is for a headset it does not show for, or a
        switch runs, whose end reads. Each read runs systemctl and pw-dump."""
        if self._reading or self._closed or self._acting or not self.row.get_mapped():
            return
        # The row shows only while the page names a headset of headset_mic.WORDS.
        key = cast("str", self.key)
        self._reading = True
        self._worker.run(lambda: headset_mic.read_state(run=self._run, key=key),
                         lambda result: self._took(result, key))

    def _took(self, result: Result, key: str) -> None:
        """The read's state on the row while the page still names `key`, and no switch runs,
        whose end reads it again; else a read for the headset named since."""
        self._reading = False
        if self._closed:
            return
        if key != self.key:
            self.refresh()
            return
        # Queued before the switch: the switch's end reads again.
        if self._acting:
            return
        if isinstance(result, headset_mic.State):
            self.kind = show_switch(self._loading, self.row, self.dot, result,
                                    headset_mic.summary(result, key=key))
        else:
            self.kind = None
            words = COULD_NOT_READ.format(result)
            put_dot(self.row, self.dot, False, words)
            self.row.set_subtitle(words)
        self._set_sensitive()

    def on_switch(self, row: Adw.SwitchRow, _pspec: GObject.ParamSpec) -> None:
        """A switch by hand: on or off on the worker, for the headset named at the click."""
        if self._loading.active:
            return
        # The row shows, and so takes a click, only while the page names a headset.
        on, key = row.get_active(), cast("str", self.key)
        switch = headset_mic.switch_on if on else headset_mic.switch_off
        self._acting = True
        self._set_sensitive()
        self._worker.run(lambda: switch(run=self._run, key=key),
                         lambda result: self._switched(result, NOT_STARTED if on
                                                       else NOT_STOPPED))

    def _switched(self, result: Result, failed: str) -> None:
        """A switch's end: its failure toasted, or each note it gave of the default input,
        as Outputs' switch toasts its own, and the window told (kept_up) of a failure or a
        note of a failed step; then the state read again, both rows grayed out until it
        lands."""
        if self._closed:
            return
        # The state before the switch says nothing of the one after: unknown until the read.
        self._acting, self.kind = False, None
        if isinstance(result, Exception):
            self._kept_up()
            words, line = switch_failure(result, failed, headset_mic.FEATURE)
            toast_note(self._toast, words, line, one_sentence=True)
        notes = (result.notes if isinstance(result, headset_mic.Switched)
                 else result if isinstance(result, list) else [])
        if pipewire_client.step_failed(notes):
            self._kept_up()
        for note in notes:
            toast_clause(self._toast, capitalized(note), headset_mic.FEATURE)
        self._set_sensitive()
        self.refresh()

    # -- the test -----------------------------------------------------------

    @property
    def testing(self) -> bool:
        """The test counts, records or plays back."""
        return bool(self._count) or self._take is not None or self._playback is not None

    def on_test(self, _button: Gtk.Button) -> None:
        """The test's start, for the headset named at the click: its count, COUNT_IN numbers
        one each COUNT_STEP_MS, then the recording (_on_count). The row, and with it its
        button, takes a click only while the input is ready."""
        self._tested = cast("str", self.key)
        self._count = headset_mic.COUNT_IN
        self._timer = GLib.timeout_add(COUNT_STEP_MS, self._on_count)
        self._say(COUNTING.format(self._count))

    def _on_count(self) -> bool:
        """The count's next number, or past its last the recording's start: pw-record on the
        cleaned input, its pipe watched, and its time set; or why not, toasted."""
        self._count -= 1
        if self._count:
            self._say(COUNTING.format(self._count))
            return True
        self._timer = 0
        try:
            self._take = headset_mic.Take.start(self._spawn, key=self._tested)
        except pipewire_client.StepError as err:
            self._failed(err)
            return False
        self._watch = GLib.io_add_watch(self._take.fileno(), GLib.PRIORITY_DEFAULT, WOKEN_BY,
                                        self._on_samples)
        self._timer = GLib.timeout_add_seconds(headset_mic.RECORD_SECONDS, self._on_record_time)
        self._say(RECORDING.format(counted(self._take.seconds_left(), "second")))
        return False

    def _on_samples(self, _fd: int, _condition: GLib.IOCondition) -> bool:
        """What the pipe holds into the recording, its line on the seconds left; once it is
        whole or pw-record has ended, its end (_recorded)."""
        take = cast("headset_mic.Take", self._take)
        if not take.read():
            self._say(RECORDING.format(counted(take.seconds_left(), "second")))
            return True
        self._watch = 0
        self._recorded()
        return False

    def _on_record_time(self) -> bool:
        """The recording's time is up: what came by then is played back (_recorded)."""
        self._timer = 0
        self._recorded()
        return False

    def _recorded(self) -> None:
        """The recording's end: pw-record stopped and its samples played back, or why not,
        toasted."""
        self._drop_sources()
        take, self._take = cast("headset_mic.Take", self._take), None
        said = take.stop()
        try:
            if not take.samples:
                raise take.no_sound(said)
            self._playback = headset_mic.Playback.start(bytes(take.samples), self._spawn)
        except pipewire_client.StepError as err:
            self._failed(err)
            return
        self._play_until = time.monotonic() + self._playback.seconds
        self._timer = GLib.timeout_add(PLAY_LOOK_MS, self._on_playing)
        self._say(PLAYING)

    def _on_playing(self) -> bool:
        """pw-play's end looked for: once it has ended or its time is up, the test's end, or
        its failure toasted (Playback.finish)."""
        playback = cast("headset_mic.Playback", self._playback)
        if not playback.done() and time.monotonic() < self._play_until:
            return True
        self._timer = 0
        self._playback = None
        try:
            playback.finish()
        except pipewire_client.StepError as err:
            self._failed(err)
            return False
        self._say(TEST_LINE)
        return False

    def _failed(self, error: pipewire_client.StepError) -> None:
        """The test's failure, toasted, and the row back on its line."""
        toast_note(self._toast, capitalized(str(error)), NOT_TESTED, one_sentence=True)
        self._say(TEST_LINE)

    def _say(self, line: str) -> None:
        self.test_row.set_subtitle(line)
        self._set_sensitive()

    def _drop_sources(self) -> None:
        for source in (self._watch, self._timer):
            if source:
                GLib.source_remove(source)
        self._watch = self._timer = 0

    def _set_sensitive(self) -> None:
        self.row.set_sensitive(self.kind is not None and self.kind not in pipewire_client.LOCKED
                               and not self._acting and not self.testing)
        self.test_row.set_sensitive(self.kind == "ready" and not self._acting
                                    and not self.testing)

    @property
    def busy(self) -> bool:
        """A switch runs, which the window's close waits for."""
        return self._acting

    def close(self) -> None:
        """The window's close: nothing read after, and the test stopped where it is, its
        count's timer dropped, or pw-record or pw-play ended."""
        self._closed = True
        self._drop_sources()
        take, self._take = self._take, None
        playback, self._playback = self._playback, None
        if take is not None:
            take.stop()
        if playback is not None:
            # Stopped by the close, so how it ended is no failure to show.
            with contextlib.suppress(pipewire_client.StepError):
                playback.finish()
