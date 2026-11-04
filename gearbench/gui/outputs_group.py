"""The Headset tab's Outputs group: Gearbench Game, Chat and Media on or off, a line while they
cannot play on the headset, the balance between voice chat and the game, and the Apps rows,
each of which moves a program to one of the three or back to the default output. The window
builds one, as it builds the Spatial sound group, and the tab moves both to the headset page
that shows (HeadsetStack).

The window runs this, never the daemon. A read, a switch and a move run systemctl, pw-dump
and pw-metadata (gearbench.outputs_state, gearbench.outputs_unit), and a balance goes to the
unit's socket as a word (gearbench.outputs), each on the window's worker thread; building the
group runs nothing.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable

from gi.repository import Adw, GObject, Gtk

from .. import outputs, outputs_state, outputs_unit, pipewire_client
from ..outputs import STOPS, SetTo
from ..wording import capitalized
from .holds import Holds
from .style import (
    ASLEEP,
    BALANCE,
    COULD_NOT_READ,
    between,
    displayable,
    page_of,
    plain_row,
    put_dot,
    spoken_scale,
    status_dot,
    toast_clause,
    toast_not_saved,
    toast_note,
    watch_pointer,
)
from .unit_switch import NOT_STARTED_OF, NOT_STOPPED_OF, UNIT_DOTS, failure_words
from .worker import FOLLOW_UP_MS, SETTLE_MS, DaemonWorker, LoadingGuard, Result, Settle

TITLE = "Outputs"
SWITCH_TITLE = "Game, Chat and Media"
SWITCH_HELP = "Outputs for games, voice chat and music"
APPS = "Apps"
APPS_HELP = "Where each program plays"
NO_APPS = "No program is playing"
#: An App row's line, and its fifth entry, for a program on another output than the three, by
#: that output's name (elsewhere).
PLAYS_ON = "On {}"
#: The Apps rows' choices, in outputs_state.CHOICES' order. The default's names the output,
#: apart from an equalizer's Default preset on the same page.
CHOICE_LABELS = tuple("Default output" if choice == outputs_state.DEFAULT else choice.capitalize()
                      for choice in outputs_state.CHOICES)
DEFAULT_CHOICE = outputs_state.CHOICES.index(outputs_state.DEFAULT)
#: Each action's toast line when its failure is too long for a toast, with the whole failure
#: behind Details: a switch's, a move's by the program, and a balance's.
NOT_STARTED = NOT_STARTED_OF.format(TITLE)
NOT_STOPPED = NOT_STOPPED_OF.format(TITLE)
NOT_MOVED = "{} did not move"
BALANCE_NOT_SET = "The balance did not change"
#: The dot for each summary kind; a kind missing here is red.
DOTS: dict[str, bool | str | None] = {"on": True, "waiting": ASLEEP, **UNIT_DOTS}
#: The Balance slider's key in the group's Holds.
BALANCE_KEY = "balance"
#: The summary kinds (outputs_state.summary) in which the Outputs process does not run, so a
#: balance word a key or a button sends moves nothing.
NOT_RUNNING = frozenset({"not_installed", "off", "failed", "stopping", "stopped"})


def stop_of(balance: int) -> int:
    """The place in outputs.STOPS of the stop nearest `balance`, as the unit's process takes
    a balance (outputs.moved)."""
    return STOPS.index(outputs.moved(balance, SetTo(balance)))


def levels_of(stop: int) -> str:
    """The Balance slider's place `stop` in outputs.STOPS as its row says it: Chat's and
    Game's levels there (outputs_state.levels)."""
    return outputs_state.levels(STOPS[stop])


def spoken_levels(stop: int) -> str:
    """levels_of's words as the slider gives them to a screen reader: "Chat 100%, Game 83%".
    A comma is read as a pause, where Orca at its higher punctuation levels can say the row's
    middle dot aloud (not checked here)."""
    return levels_of(stop).replace(" · ", ", ")


def elsewhere(app: outputs_state.App) -> bool:
    """Whether `app` plays on none of the three, on an output that is its own to move."""
    return not app.fixed and app.plays_on is not None and app.output is None


def labels_of(app: outputs_state.App) -> tuple[str, ...]:
    """An App row's entries: CHOICE_LABELS, then where the program plays while elsewhere, so
    that Default output is a pick that moves it."""
    if elsewhere(app):
        return (*CHOICE_LABELS, displayable(PLAYS_ON.format(app.plays_on)))
    return CHOICE_LABELS


def choice_of(app: outputs_state.App) -> int:
    """The entry an App row selects (labels_of): the output `app` plays on (App.output), the
    fifth entry while elsewhere, else the default output's."""
    if app.output is not None:
        return outputs_state.CHOICES.index(app.output)
    return len(CHOICE_LABELS) if elsewhere(app) else DEFAULT_CHOICE


def app_line(app: outputs_state.App) -> str:
    """An App row's one line: where the program plays while elsewhere, else its note
    capitalized, if any."""
    if elsewhere(app):
        return PLAYS_ON.format(app.plays_on)
    return capitalized(app.note) if app.note else ""


class OutputsGroup:
    """The group and its polls; every read, switch and move runs programs with `run`, and
    `kept_up` is told of an action or a balance that failed (Window.kept_up)."""

    def __init__(self, worker: DaemonWorker, toast: Callable[..., object],
                 kept_up: Callable[[], None],
                 run: pipewire_client.Run = subprocess.run) -> None:
        self._worker = worker
        self._toast = toast
        self._kept_up = kept_up
        self._run = run
        self._loading = LoadingGuard()
        self._holds = Holds()
        #: A read is out; a switch or a move runs, which the window's close waits for.
        self._pending = self._acting = False
        #: The read again FOLLOW_UP_MS after an action (_acted).
        self._follow_up = Settle(self.refresh)
        #: The last summary kind shown, None while no state is shown.
        self.kind: str | None = None
        #: The headset the outputs play to at the last read that could see PipeWire.
        self._headset: str | None = None
        #: Each App row by its key, in the order shown.
        self.app_rows: dict[str, Adw.ComboRow] = {}
        #: Each App row's entries as last listed (labels_of), by its key.
        self._row_labels: dict[str, tuple[str, ...]] = {}
        #: The Balance slider's stop as the last read or send left it: a release or a settle
        #: that ends there sends nothing, since the unit may have moved since (a key's word).
        self._balance_stop = stop_of(outputs.CENTER)
        self.widget = self._build()

    def _build(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title=TITLE)
        self.switch_row = plain_row(Adw.SwitchRow, SWITCH_TITLE, SWITCH_HELP)
        # Added once there is a state to show (put_dot).
        self.dot = status_dot()
        self.switch_row.connect("notify::active", self.on_switch)
        group.add(self.switch_row)
        self.status_row = plain_row(Adw.ActionRow, "", "", visible=False)
        group.add(self.status_row)

        self.balance_row = plain_row(Adw.ActionRow, BALANCE, levels_of(self._balance_stop),
                                     visible=False)
        last = len(STOPS) - 1
        self.balance = spoken_scale(0, last, BALANCE, page=page_of(0, last), origin=False,
                                    spoken=spoken_levels)
        for stop in range(len(STOPS)):
            self.balance.add_mark(stop, Gtk.PositionType.BOTTOM, None)
        # Before its handler: the balance before any read is no move to send.
        self.balance.set_value(self._balance_stop)
        self.balance.connect("value-changed", self.on_balance)
        watch_pointer(self.balance, self.on_press, self.on_release)
        self.balance_row.add_suffix(between(self.balance, "Chat", "Game"))
        group.add(self.balance_row)

        self.apps_row = plain_row(Adw.ExpanderRow, APPS, APPS_HELP, visible=False)
        group.add(self.apps_row)
        self._set_sensitive()
        return group

    @property
    def balance_unheard(self) -> bool:
        """Whether the last read found the Outputs process not running (NOT_RUNNING), for the
        rows and tooltips of keys and buttons bound to a balance word. False before a read
        (Polls says when the window makes one), and after one that failed: nothing is known
        then."""
        return self.kind in NOT_RUNNING

    @property
    def busy(self) -> bool:
        """A switch or a move runs, or a balance sent is not answered yet: the window's close
        waits for each."""
        return self._acting or self._holds.answering(BALANCE_KEY)

    # -- reading ----------------------------------------------------------

    def refresh(self) -> None:
        """Read the state on the worker thread, unless a read is already out."""
        if self._pending:
            return
        self._pending = True
        self._worker.run(lambda: outputs_state.read_state(run=self._run), self._on_state)

    def tick(self) -> None:
        """The window's poll, which leaves a running switch or move to read after itself."""
        if not self._acting:
            self.refresh()

    def flush(self) -> bool:
        """Send now a balance still settling, for the window's close; True if one was."""
        return self._holds.flush()

    def close(self) -> None:
        self._holds.cancel_all()
        self._follow_up.cancel()

    def _on_state(self, result: Result) -> None:
        self._pending = False
        # A read that lands while an action runs was queued before it: the action's own read
        # follows.
        if self._acting:
            return
        if isinstance(result, outputs_state.State):
            self.show(result)
            return
        self.kind = None
        words = COULD_NOT_READ.format(result)
        put_dot(self.switch_row, self.dot, False, words)
        self._say(words)
        self._set_sensitive()

    def show(self, state: outputs_state.State) -> None:
        """Set every row from `state`. A read whose systemctl failed leaves the switch as the
        last look or a click left it, and one whose pw-dump failed leaves the headset and the
        Apps rows, as a read that failed leaves every row (_on_state)."""
        kind, words = outputs_state.summary(state)
        self.kind = kind
        if state.systemd is None:
            self._loading.put_active(self.switch_row, state.enabled)
        put_dot(self.switch_row, self.dot, DOTS.get(kind, False), capitalized(words))
        self._say(None if kind == "off" else state.status if kind == "on"
                  else capitalized(words))
        if state.tools and state.pipewire is None:
            self._headset = state.headset
            self._show_apps(state.apps)
        if not self._holds.held(BALANCE_KEY):
            self._balance_stop = stop_of(state.settings.balance)
            self._loading.put_value(self.balance, self._balance_stop)
        # The switch, not state.enabled, as for the switch itself above. A headset that sets
        # its own balance has a Balance row of its own on its page.
        on = self.switch_row.get_active()
        self.balance_row.set_visible(on and self._headset not in outputs_unit.OWN_BALANCE)
        self.apps_row.set_visible(on)
        self._set_sensitive()

    def _say(self, line: str | None) -> None:
        """The status row shows `line`, or hides for None."""
        self.status_row.set_visible(line is not None)
        if line is not None:
            self.status_row.set_title(displayable(line))

    def _show_apps(self, apps: tuple[outputs_state.App, ...]) -> None:
        """A row for each App, made again only when the programs change, so the keyboard
        focus on one stays in place."""
        if tuple(app.key for app in apps) != tuple(self.app_rows):
            for row in self.app_rows.values():
                self.apps_row.remove(row)
            self.app_rows = {app.key: self._app_row(app.key) for app in apps}
            self._row_labels = dict.fromkeys(self.app_rows, CHOICE_LABELS)
            for row in self.app_rows.values():
                self.apps_row.add_row(row)
        for app in apps:
            row = self.app_rows[app.key]
            row.set_title(displayable(app.title))
            row.set_subtitle(displayable(app_line(app)))
            labels = labels_of(app)
            if labels != self._row_labels[app.key]:
                self._row_labels[app.key] = labels
                self._loading.put_model(row, Gtk.StringList.new(labels))
            self._loading.put_selected(row, choice_of(app))
            # A program that names its own output is moved only in its own settings.
            row.set_sensitive(not app.fixed)
        self.apps_row.set_subtitle(APPS_HELP if apps else NO_APPS)

    def _app_row(self, key: str) -> Adw.ComboRow:
        row = plain_row(Adw.ComboRow, "", "", model=Gtk.StringList.new(CHOICE_LABELS))
        row.connect("notify::selected", self.on_app, key)
        return row

    def _set_sensitive(self) -> None:
        idle = not self._acting
        self.switch_row.set_sensitive(idle and self.kind is not None
                                      and self.kind not in pipewire_client.LOCKED)
        # The balance's words and a move need the unit's process and its sinks there.
        live = idle and self.kind == "on"
        self.balance_row.set_sensitive(live)
        if not (live and self.balance_row.get_visible()):
            # A slider grayed out or hidden under the pointer never sees its release.
            self._holds.release(BALANCE_KEY)
        self.apps_row.set_sensitive(live and bool(self.app_rows))

    # -- actions ----------------------------------------------------------

    def on_switch(self, row: Adw.SwitchRow, _pspec: GObject.ParamSpec) -> None:
        if self._loading.active:
            return
        on = row.get_active()
        switch = outputs_state.switch_on if on else outputs_state.switch_off
        self._act(lambda: switch(run=self._run), NOT_STARTED if on else NOT_STOPPED)

    def on_app(self, row: Adw.ComboRow, _pspec: GObject.ParamSpec, key: str) -> None:
        # No check for nothing selected: a combo row over a list of four keeps its
        # selection when told to drop it (libadwaita 1.9, measured 2026-10-28).
        if self._loading.active:
            return
        if row.get_selected() >= len(outputs_state.CHOICES):
            # The fifth entry, picked after a move and before the read that lists the row
            # again: no output to move to, and that read puts the row where the program is.
            return
        choice = outputs_state.CHOICES[row.get_selected()]
        self._act(lambda: outputs_state.move_app(key, choice, run=self._run),
                  NOT_MOVED.format(row.get_title()))

    def _act(self, work: Callable[[], object], failed: str) -> None:
        """Run `work` on the worker; `failed` is the toast line for a failure too long for a
        toast, other than an OSError from saving outputs.json (_acted)."""
        self._acting = True
        self._set_sensitive()
        self._worker.run(work, lambda result: self._acted(result, failed))

    def _acted(self, result: Result, failed: str) -> None:
        """An action's end: each note a switch gave, or a move's, toasted, or its failure;
        then the state read again, and once more FOLLOW_UP_MS later, as the Spatial sound
        group reads after its actions."""
        self._acting = False
        self._set_sensitive()
        notes = [result] if isinstance(result, str) else result if isinstance(result, list) else []
        for note in notes:
            toast_clause(self._toast, capitalized(note), outputs.DESCRIPTION)
        if isinstance(result, Exception):
            self._kept_up()
        if isinstance(result, OSError):
            # Only saving outputs.json raises one: systemctl's and PipeWire's arrive as
            # StepError.
            toast_not_saved(self._toast, outputs_unit.settings_path(), result)
        elif isinstance(result, Exception):
            toast_note(self._toast, failure_words(result, TITLE), failed, one_sentence=True)
        self.refresh()
        self._follow_up.restart(FOLLOW_UP_MS)

    # -- the balance --------------------------------------------------------

    def on_balance(self, scale: Gtk.Scale) -> None:
        self.balance_row.set_subtitle(levels_of(round(scale.get_value())))
        if self._loading.active or self._holds.pressing(BALANCE_KEY):
            return
        self._holds.settle(BALANCE_KEY, self._send_balance, SETTLE_MS)

    def on_press(self) -> None:
        """The pointer went down on the slider: nothing is sent until it lets go."""
        self._holds.press(BALANCE_KEY)

    def on_release(self) -> None:
        """The pointer let go: the balance it left goes once it settles, unless it is the one
        last read or sent (_send_balance). Sent whatever the loading guard says: a release is
        one event, and a poll's rows can hold the guard."""
        if self._holds.release(BALANCE_KEY):
            self._holds.settle(BALANCE_KEY, self._send_balance, SETTLE_MS)

    def _send_balance(self) -> None:
        """Send the slider's stop, unless it is the one last read or sent and no send of it
        is out."""
        stop = round(self.balance.get_value())
        if self._holds.unchanged(BALANCE_KEY, stop == self._balance_stop):
            return
        self._balance_stop = stop
        balance = STOPS[stop]
        self._holds.sent(BALANCE_KEY)
        self._worker.run(lambda: outputs.ask(SetTo(balance)), self._balance_answered)

    def _balance_answered(self, result: Result) -> None:
        """The unit's answer to a balance: a refusal, or a word not sent or not answered,
        toasted; then the state read again, which puts the slider where the unit has it."""
        self._holds.answered(BALANCE_KEY)
        words = None
        if isinstance(result, str):
            words = outputs.refusal(result)
        elif isinstance(result, Exception):
            # ask's own failures are in words; any other is named as the Balance row's.
            words = (str(result) if isinstance(result, outputs.NotTaken | outputs.SocketRefused)
                     else f"{BALANCE}: {result}")
        if words is not None:
            self._kept_up()
            toast_note(self._toast, capitalized(words), BALANCE_NOT_SET, one_sentence=True)
        self.refresh()
