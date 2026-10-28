"""The volume rows of a headset page, each a slider of a volume as GNOME's Sound settings show
and set it (gearbench.system_volume): Game output and Chat output, which the A50 Gen 4's and
the A50 X's pages put under Balance and which show for a headset of system_volume.OUTPUTS,
and Mic input, which each headset page puts above Input level and which shows for every
headset. A page builds the rows it puts somewhere (`words`).

The rows read with pw-dump on the worker thread at each of the page's polls (`refresh`), as
they come on screen and for another headset named (`show_headset`), and only while one of
them is on screen, mapped, as the Input level row runs, so a change made in GNOME shows at
the page's next poll. A slider the pointer drags sets its volume at most once each LIVE_MS, and its last
value when the pointer lets go; one moved by its keys sets it once they settle. Each set runs
pw-dump and pw-cli on the worker thread too, and a read leaves a slider where it is while its
set waits, the pointer holds it, or a set is not answered (holds.Holds). Building the rows
runs nothing.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from functools import partial
from typing import cast

from gi.repository import Adw, Gtk

from .. import pipewire_client, system_volume
from ..wording import reason
from .holds import Holds
from .input_meter_row import NOT_FOUND, NOT_READ as PIPEWIRE_UNREAD
from .style import (
    NOT_READ,
    page_of,
    percent,
    plain_row,
    spoken_scale,
    toast_note,
    watch_pointer,
)
from .worker import LIVE_MS, SETTLE_MS, DaemonWorker, LoadingGuard, Result

#: Each row's title by its word: an output's by its name in system_volume, then the
#: microphone's.
TITLES = {**{word: f"{name} output" for word, name in system_volume.OUTPUT_NAMES.items()},
          system_volume.MIC: "Mic input"}
#: The words of the output rows, which the A50s' pages put under Balance.
OUTPUT_WORDS = tuple(system_volume.OUTPUT_NAMES)
#: Each slider's name for a screen reader.
NAMES = {word: f"{title} volume" for word, title in TITLES.items()}
#: A row's line once read: whose volume it is, beside the headset's own, and its level.
LEVEL = "GNOME's volume: {}"
#: A set that failed: the toast's line, by the row's title, with the cause after it.
NOT_SET = "The {}'s volume did not change"


class VolumeRows:
    """The rows (`rows`) and their sliders (`scales`) of `words`, by each one's word, for the
    headset `show_headset` names; `inset` is the room each slider leaves at its right, to line
    up with the page's other sliders. Every read and set runs programs with `run`."""

    def __init__(self, worker: DaemonWorker, toast: Callable[..., object],
                 run: pipewire_client.Run = subprocess.run, inset: int = 0,
                 words: Sequence[str] = tuple(TITLES)) -> None:
        self._worker = worker
        self._toast = toast
        self._run = run
        self._loading = LoadingGuard()
        self._holds = Holds()
        #: The headset the page names, a key of system_volume.VOLUMES; None for none.
        self.key: str | None = None
        self._reading = self._closed = False
        #: Each row's level as last read or set, while it is known.
        self._levels: dict[str, int] = {}
        self.rows: dict[str, Adw.ActionRow] = {}
        self.scales: dict[str, Gtk.Scale] = {}
        for word in words:
            row = plain_row(Adw.ActionRow, TITLES[word], NOT_READ, visible=False,
                            sensitive=False)
            scale = spoken_scale(0, system_volume.LEVEL_MAX, NAMES[word],
                                 page=page_of(0, system_volume.LEVEL_MAX))
            scale.set_margin_end(inset)
            scale.connect("value-changed", self.on_slider, word)
            watch_pointer(scale, partial(self.on_press, word), partial(self.on_release, word))
            row.add_suffix(scale)
            # Each, since the outputs' group and the microphone's show apart.
            row.connect("map", lambda _row: self.refresh())
            self.rows[word], self.scales[word] = row, scale

    def _shown(self, key: str | None) -> list[str]:
        """The words of the rows that show for the headset of `key`: those it has
        (system_volume.VOLUMES); none for None."""
        return [] if key is None else [word for word in self.rows
                                       if word in system_volume.VOLUMES[key]]

    def show_headset(self, key: str | None) -> None:
        """The headset the page's last reply names, None for none: another one's rows of the
        volumes it has show, Not read until they read, which they do at once where one is on
        screen."""
        if key == self.key:
            return
        self.key = key
        self._let_go()
        shown = self._shown(key)
        for word, row in self.rows.items():
            row.set_visible(word in shown)
            row.set_subtitle(NOT_READ)
            row.set_sensitive(False)
        self.refresh()

    def refresh(self) -> None:
        """Read the headset's volumes on the worker, the page's poll: unless a read is out,
        or the rows are off screen, closed or name none."""
        key = self.key
        if (key is None or self._reading or self._closed
                or not any(row.get_mapped() for row in self.rows.values())):
            return
        self._reading = True
        self._worker.run(lambda: system_volume.read_volumes([key], self._run)[key],
                         lambda result: self._took(result, key))

    def _took(self, result: Result, key: str) -> None:
        """The read's volumes on the rows that show while the page still names `key`, but a
        slider held (_holds), which stays where it is; else a read for the headset named
        since."""
        self._reading = False
        if self._closed:
            return
        if key != self.key:
            self.refresh()
            return
        for word in self._shown(key):
            row = self.rows[word]
            found = None if isinstance(result, Exception) else cast(
                "dict[str, system_volume.Volume | None]", result)[word]
            row.set_sensitive(found is not None)
            if found is None:
                self._levels.pop(word, None)
                self._let_go(word)
                row.set_subtitle(NOT_FOUND if not isinstance(result, Exception)
                                 else PIPEWIRE_UNREAD.format(reason(result)))
            elif not self._holds.held(word):
                self._levels[word] = found.level
                self._loading.put_value(self.scales[word], found.level)
                row.set_subtitle(LEVEL.format(percent(found.level)))

    def _let_go(self, *words: str) -> None:
        """Drop what `words`' sliders, every one by default, still owe: the pointer's hold, a
        set waiting; a row grayed out under the pointer never sees its release."""
        for word in words or self.rows:
            self._holds.release(word)
            self._holds.cancel(word)

    # -- setting ----------------------------------------------------------

    def on_press(self, word: str) -> None:
        """The pointer went down on `word`'s slider: from here it sets at the drag's pace."""
        self._holds.press(word)

    def on_release(self, word: str) -> None:
        """The pointer let go of `word`'s slider: its level is set now, unless it is the one
        last read or set. Set whatever the loading guard says: a release is one event."""
        if self._holds.release(word):
            self._send(word)

    def on_slider(self, scale: Gtk.Scale, word: str) -> None:
        """A slider moved: its row says the level, and a move by hand sets it, at the drag's
        pace while the pointer holds it, else once its keys settle."""
        self.rows[word].set_subtitle(LEVEL.format(percent(round(scale.get_value()))))
        if self._loading.active:
            return
        if self._holds.pressing(word):
            self._holds.dragged(word, partial(self._send, word), LIVE_MS)
        else:
            self._holds.settle(word, partial(self._send, word), SETTLE_MS)

    def _send(self, word: str) -> None:
        """Set `word`'s volume to its slider's level on the worker, unless it is the level
        last read or set and no set of it is out."""
        # A set waits only while the page names a headset: show_headset and close let go.
        key, level = cast("str", self.key), round(self.scales[word].get_value())
        if self._holds.unchanged(word, level == self._levels.get(word)):
            return
        self._levels[word] = level
        self._holds.sent(word)
        self._worker.run(lambda: system_volume.set_level(key, word, level, self._run),
                         partial(self._set, word))

    def _set(self, word: str, result: Result) -> None:
        """A set answered: its failure toasted, and the volumes read again once nothing of
        `word` is on its way, which puts its row on the level PipeWire holds."""
        self._holds.answered(word)
        if self._closed:
            return
        if isinstance(result, Exception):
            # one_sentence: pw-cli's words can hold ". ".
            line = NOT_SET.format(TITLES[word])
            toast_note(self._toast, f"{line}: {reason(result)}", line, one_sentence=True)
        if not self._holds.held(word):
            self.refresh()

    # -- the window's -----------------------------------------------------

    @property
    def busy(self) -> bool:
        """A set is out, which the window's close waits for."""
        return any(self._holds.answering(word) for word in self.rows)

    def flush(self) -> bool:
        """Set now what still waits, for the window's close; True if anything did."""
        return self._holds.flush()

    def close(self) -> None:
        self._closed = True
        self._let_go()
