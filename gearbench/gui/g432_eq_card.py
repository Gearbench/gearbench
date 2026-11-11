"""The equalizer the window runs on this computer (gearbench.g432_eq) for the G432 and the
headsets of its family with none of their own, the G432's page's Equalizer group: its
switch, Preset, Name, the gain sliders, Copy from Astro A50 Gen 4, which lists the presets
the daemon holds of the A50 (`headset`, which then asks the base station nothing), and the
rows that save a preset as new, reset one and delete one. Its programs and the files it
saves run on the worker thread. Building it runs nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING, cast

from gi.repository import Adw, GLib, GObject, Gtk

from .. import (
    a50_library,
    astro,
    eq_presets,
    g432_eq,
    g432_fit,
    g432_presets,
    pipewire_client,
)
from ..g432 import BAND_FREQS, G432_MODEL, HeadsetModel
from ..g432_eq_words import WORDS as EQ_WORDS
from ..wording import capitalized, hertz, signed
from .headset_cards import band_row
from .style import (
    COULD_NOT_READ,
    DELETE_HEADING,
    DELETE_OPENER,
    DELETE_PRESET,
    DELETE_RESPONSE,
    NOT_DELETED,
    NOT_RENAMED,
    NOT_SAVED_NEW,
    PRESET,
    PRESET_RESET,
    SAVE_BUTTON,
    SAVE_HEADING,
    EntryTexts,
    ask_name,
    confirm,
    displayable,
    name_button,
    name_refusal,
    offer_save_new,
    opener_row,
    plain_row,
    preset_label,
    put_dot,
    status_dot,
    toast_not_saved,
    toast_note,
    watch_name,
)
from .unit_switch import NOT_STARTED_OF, NOT_STOPPED_OF, show_switch, switch_failure
from .worker import DaemonWorker, LoadingGuard, Reply, Result, Settle, unreachable

if TYPE_CHECKING:                       # pragma: no cover
    from .window import Window

#: The next step where Outputs is off and switching on leaves the default output alone, and
#: the warning that what plays straight to the headset meanwhile plays at 0 dB: the model's
#: short name goes in, and EQ_HELP is the G432's.
EQ_HELP_OF = ("Pick it as your output unless Outputs is on or switching it on makes it the "
              "default; the {}'s own output stays at 0 dB while it is on")
EQ_HELP = EQ_HELP_OF.format(G432_MODEL.short)
RESET_HELP = "The active preset's own name and gains back"
#: Reset to default's confirmation, which its row's button opens, its ellipsis for that
#: question (the HIG's writing style), where the A50 Gen 4's row, which asks nothing, has
#: none: it writes over the preset's name and gains.
RESET_HEADING = "Reset {} to default?"
RESET_BODY = "The name and gains it has now are lost."
RESET_BUTTON = "Reset"
RESET_OPENER = f"{RESET_BUTTON}…"
#: The row that deletes the active preset, one saved as new (DELETE_PRESET), and its
#: confirmation.
DELETE_HELP = "The preset before it plays in its place"
DELETE_BODY = f"{DELETE_HELP}. This cannot be undone."
#: Save as new preset's line, the new preset playing at once, and its button, which asks the
#: name.
SAVE_NEW_HELP = "Keeps the active preset's gains as a new preset, then plays it"
SAVE_OPENER = f"{SAVE_BUTTON}…"
COPY = f"Copy from {astro.NAME}"
COPY_HELP = "Replaces the active preset's name and gains"
#: Its button, its ellipsis for the question it opens (the HIG's writing style), and the
#: question's own response.
COPY_RESPONSE = "Copy"
COPY_OPENER = f"{COPY_RESPONSE}…"
#: Copy's confirmation, by the active preset's name and the A50 Gen 4 preset's.
COPY_HEADING = "Copy onto {}?"
COPY_BODY = f"The {astro.NAME}'s {{}} replaces this preset's name and gains."
#: The Copy row's dropdown's name for a screen reader.
A50_PRESET = f"{astro.NAME} preset"
#: The Copy row's subtitle before the first `headset` reply, and for a refusal with no words.
NO_A50 = "Plug in its base station"
#: Its subtitle with the base station there and none of its presets held: the daemon reads
#: them for the A50's page on the Headset tab of the active window, or as the headset links.
A50_UNREAD = "Its presets are not read yet: show its page on this tab"
#: Its subtitle with the base station there, not answering, and none of its presets held.
A50_SILENT = capitalized(astro.BASE_SILENT)
#: Each action's toast line when its failure is too long for a toast, the whole failure
#: behind Details; a switch's is unit_switch's NOT_STARTED_OF or NOT_STOPPED_OF.
NOT_SET = "The gains did not change"
NOT_PICKED = "The preset did not change"
NOT_RESET = "The preset was not reset"
NOT_COPIED = f"The {astro.NAME} preset was not copied"
#: The toast line for any of them whose change was saved and not set on the running chain
#: (g432_eq.NotSetLive).
SAVED_NOT_SET = "Saved, but not set on the equalizer"


class G432EqCard:
    """The Equalizer group of the G432's page (`group`), for the model the page shows
    (show_model), shown on a model with no equalizer of its own. `run` runs the equalizer's
    programs, on `worker`'s thread as its saves and reads; `parent` is the window, which its
    dialogs open over and which it tells of a change that failed (Window.kept_up);
    `loading` is the page's guard; a slider's move is sent `settle_ms` after the last."""

    def __init__(self, parent: Window, worker: DaemonWorker, toast: Callable[..., object],
                 loading: LoadingGuard, run: pipewire_client.Run, settle_ms: int) -> None:
        self._parent = parent
        self._worker = worker
        self._toast = toast
        self._loading = loading
        self._run = run
        self._settle_ms = settle_ms
        #: A read is out, the equalizer's or the A50's; the window has closed.
        self._eq_pending = self._a50_pending = False
        self._closed = False
        #: How many of the equalizer's actions are out (busy): one can start while another
        #: runs.
        self._acting = 0
        #: The model the page shows, the G432 before a reply names one (show_model).
        self.model: HeadsetModel = G432_MODEL
        self._gains_send = Settle(self._send_gains)
        #: The gains the last read gave or the last send asked for: a send of the same skips;
        #: and the preset the sliders show, its number and name, None before a read.
        self._gains = g432_presets.Gains()
        self._gains_of: tuple[int, str] | None = None
        #: The presets the last read gave, None before the first, and the Preset row's
        #: labels; the Name row's text as the card put it.
        self._presets: g432_presets.Presets | None = None
        self._preset_labels: list[str] = []
        self._shown = EntryTexts()
        #: The A50's presets Copy can take, by number, from the daemon's last `headset`
        #: reply, and the Copy row's dropdown's labels, in their order.
        self._a50: dict[int, a50_library.EqPreset] = {}
        self._a50_labels: list[str] = []
        self.kind: str | None = None
        self.group = self._build()

    def _build(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Equalizer", description=EQ_HELP)
        self.switch_row = plain_row(Adw.SwitchRow, g432_eq.G432_WORDS.description, "")
        self.eq_dot = status_dot()
        self.switch_row.connect("notify::active", self.on_switch)
        group.add(self.switch_row)
        # The name as the subtitle, as the Keyboard tab's Active: a preset's name is one
        # people type, and the inline value is cut at 20 characters.
        self.preset_row = Adw.ComboRow(title=PRESET, use_subtitle=True,
                                       model=Gtk.StringList.new([]))
        self.preset_row.connect("notify::selected", self.on_preset)
        group.add(self.preset_row)
        self.name_row = Adw.EntryRow(title="Name", show_apply_button=True)
        self.name_row.connect("apply", self.on_name)
        watch_name(self.name_row)
        group.add(self.name_row)
        low, high = g432_presets.DB_RANGE
        names = ("Bass", *(hertz(freq) for freq in BAND_FREQS), "Treble")
        #: The bass, each band and the treble, in the order Gains.all gives them.
        self.gain_rows: list[Adw.ActionRow] = []
        self.gain_scales: list[Gtk.Scale] = []
        for name in names:
            row, scale = band_row(name, low, high, self.on_gain)
            group.add(row)
            self.gain_rows.append(row)
            self.gain_scales.append(scale)
        group.add(self._copy_row())
        # Rows with a line of their own, as Delete preset's: the group's line is EQ_HELP's,
        # so each row's own line says why it is grayed out, which a tooltip would not tell a
        # keyboard or a screen reader.
        self.save_row, self.save_button = opener_row(SAVE_HEADING, SAVE_NEW_HELP, SAVE_OPENER,
                                                     self.on_save_new)
        group.add(self.save_row)
        self.reset_row, self.reset_button = opener_row(PRESET_RESET, RESET_HELP, RESET_OPENER,
                                                       self.on_reset)
        group.add(self.reset_row)
        self.delete_row, self.delete_button = opener_row(DELETE_PRESET, DELETE_HELP, DELETE_OPENER,
                                                         self.on_delete)
        group.add(self.delete_row)
        self._set_sensitive()
        return group

    def _copy_row(self) -> Adw.ActionRow:
        """Copy from Astro A50 Gen 4: a dropdown of the A50's presets and the Copy button."""
        self.copy_row = plain_row(Adw.ActionRow, COPY, NO_A50, tooltip_text=COPY_HELP)
        self.a50_list = Gtk.DropDown(model=Gtk.StringList.new([]), valign=Gtk.Align.CENTER)
        self.a50_list.update_property([Gtk.AccessibleProperty.LABEL], [A50_PRESET])
        self.copy_button = Gtk.Button(label=COPY_OPENER, valign=Gtk.Align.CENTER)
        name_button(self.copy_button, COPY)
        self.copy_button.connect("clicked", self.on_copy)
        self.copy_row.add_suffix(self.a50_list)
        self.copy_row.add_suffix(self.copy_button)
        return self.copy_row

    @property
    def busy(self) -> bool:
        """One of the equalizer's actions is out, which the window's close waits for."""
        return self._acting > 0

    def show_model(self, model: HeadsetModel) -> None:
        """The card on `model`: shown on a model with no equalizer of its own, under its
        names, and the last model's state and gains still settling dropped, until the next
        tick reads them for it. A read out for the last model is read again as it lands
        (_on_eq_state)."""
        self.model = model
        eq = EQ_WORDS.get(model.key)
        self.group.set_visible(eq is not None)
        if eq is not None:
            self.switch_row.set_title(eq.description)
            self.group.set_description(EQ_HELP_OF.format(model.short))
        self._gains_send.cancel()
        self._presets = self._gains_of = self.kind = None
        self._set_sensitive()

    # -- the equalizer ------------------------------------------------------

    def refresh_eq(self) -> None:
        """Read the equalizer's state on the worker thread, unless a read is out or the model
        has one of its own."""
        model = self.model
        if self._eq_pending or self._closed or model.onboard_eq:
            return
        self._eq_pending = True
        self._worker.run(lambda: g432_eq.read_state(run=self._run, model=model),
                         lambda result: self._on_eq_state(result, model))

    def _on_eq_state(self, result: Result, model: HeadsetModel) -> None:
        self._eq_pending = False
        if self._closed:
            return
        # A read for the model before: read again for this one.
        if model is not self.model:
            self.refresh_eq()
            return
        # A read that lands while an action runs was queued before it: the action's end
        # reads it again (_acted).
        if self._acting:
            return
        if not isinstance(result, g432_eq.State):
            self.kind = None
            words = COULD_NOT_READ.format(result)
            put_dot(self.switch_row, self.eq_dot, False, words)
            self.switch_row.set_subtitle(words)
            self._set_sensitive()
            return
        self.kind = show_switch(self._loading, self.switch_row, self.eq_dot, result,
                                g432_eq.summary(result, model=model))
        if not self._gains_send.pending:
            self._show_gains(result.presets)
        self._show_presets(result.presets)
        self._set_sensitive()

    def _show_gains(self, presets: g432_presets.Presets) -> None:
        """The active preset's gains on the sliders, the preset a move then goes to."""
        self._gains = gains = presets.current.gains
        self._gains_of = presets.active, presets.current.name
        for row, scale, gain in zip(self.gain_rows, self.gain_scales, gains.all(),
                                    strict=True):
            self._loading.put_value(scale, gain)
            row.set_subtitle(signed(gain))

    # -- the equalizer's rows -----------------------------------------------

    def _show_presets(self, presets: g432_presets.Presets) -> None:
        """The Preset row's list and its pick, and the Name row's text but while it is
        being typed in, which a switch of preset puts back. The group's description says
        why a file that does not read, or read in part, takes no change (Presets.refused),
        while the rows that change a preset are insensitive (_set_sensitive)."""
        switched = self._presets is None or presets.active != self._presets.active
        self._presets = presets
        # Markup: the sentence quotes the file's path, a lone surrogate in it as U+FFFD, and
        # its cause, a preset's name among them.
        self.group.set_description(GLib.markup_escape_text(displayable(presets.refused))
                                      if presets.refused
                                      else EQ_HELP_OF.format(self.model.short))
        labels = [preset_label(number, presets.get(number).name)
                  for number in presets.numbers]
        if labels != self._preset_labels:
            self._preset_labels = labels
            self._loading.put_model(self.preset_row, Gtk.StringList.new(labels))
        self._loading.put_selected(self.preset_row, presets.active - 1)
        if switched or not self._shown.typing(self._parent, self.name_row):
            self._shown.show(self.name_row, presets.current.name)

    def _set_sensitive(self) -> None:
        self.switch_row.set_sensitive(self.kind is not None
                                      and self.kind not in pipewire_client.LOCKED
                                      and not self._acting)
        # The presets are in a file of the window's own: no unit or PipeWire needed. One
        # that does not read, or read in part, takes no change (Presets.whole).
        changeable = self._presets is not None and not self._presets.refused
        editable = changeable and not self._acting
        for row in (self.preset_row, self.name_row):
            row.set_sensitive(editable)
        # Until a read gives the gains and the preset they show (_gains_of), the sliders sit
        # at the scale's low end, or, after a reset, delete or copy, at the gains it wrote
        # over, which a move would save over a preset's. Not tied to `busy`: the page sends
        # gains while a drag goes on.
        for gain_row in self.gain_rows:
            gain_row.set_sensitive(changeable and self._gains_of is not None)
        self.copy_row.set_sensitive(editable and bool(self._a50))
        presets = self._presets
        # A preset saved as new has no default, and a shipped one is not deleted; past
        # PRESETS_MAX no more can be saved.
        shipped = presets is not None and presets.active in g432_presets.SHIPPED
        self.reset_row.set_sensitive(editable and shipped)
        self.reset_row.set_subtitle(
            RESET_HELP if shipped or presets is None
            else capitalized(g432_presets.NO_DEFAULT.format(presets.active)))
        self.delete_row.set_sensitive(editable and not shipped)
        self.delete_row.set_subtitle(
            capitalized(g432_presets.NO_DELETE.format(presets.active))
            if shipped and presets is not None
            else DELETE_HELP)
        full = offer_save_new(self.save_row, editable,
                              None if presets is None else presets.presets)
        self.save_row.set_subtitle(full or SAVE_NEW_HELP)

    def on_gain(self, scale: Gtk.Scale, row: Adw.ActionRow) -> None:
        row.set_subtitle(signed(round(scale.get_value())))
        if not self._loading.active:
            self._gains_send.restart(self._settle_ms)

    def _send_gains(self) -> None:
        """The sliders' gains to the preset they show, where the file holds it then
        (g432_eq.set_gains), unless they are the last read's or send's. The rows are
        sensitive only once a read gave the presets (_set_sensitive), and with them that
        preset."""
        values = [round(scale.get_value()) for scale in self.gain_scales]
        gains = g432_presets.Gains(bands=tuple(values[1:-1]), bass=values[0], treble=values[-1])
        if gains != self._gains:
            model = self.model
            self._gains = gains
            number, name = cast("tuple[int, str]", self._gains_of)
            self._act(lambda: g432_eq.set_gains(gains, number, name, run=self._run, model=model),
                      NOT_SET)

    # -- the presets --------------------------------------------------------

    def _update(self, change: Callable[[g432_presets.Presets], g432_presets.Presets],
                failed: str, model: HeadsetModel) -> None:
        """Save `change` of `model`'s presets and set the active preset's gains on its
        running chain, on the worker (g432_eq.update); `failed` as _act's."""
        self._act(lambda: g432_eq.update(change, run=self._run, model=model), failed,
                  model=model)

    def on_preset(self, row: Adw.ComboRow, _pspec: GObject.ParamSpec) -> None:
        number = row.get_selected() + 1
        if (self._loading.active or self._presets is None
                or number not in self._presets.numbers or number == self._presets.active):
            return
        name = self._presets.get(number).name
        # A gain still settling was the preset shown's: it goes there first.
        self._gains_send.flush()
        self._update(lambda presets: replace(
            presets, active=eq_presets.number_named(presets.presets, number, name)), NOT_PICKED,
            self.model)

    def on_name(self, row: Adw.EntryRow) -> None:
        """No loading guard: apply comes only from Enter or the apply button, never from
        set_text. A name changes nothing the chain plays, so it is only saved."""
        if self._presets is None:
            return
        name = row.get_text().strip()
        if (refusal := name_refusal(name)) is not None:
            self._toast(refusal)
            return
        number, shown, model = self._presets.active, self._presets.current.name, self.model
        self._shown.took(row)
        # A move before the next read goes to the preset by its new name.
        if self._gains_of == (number, shown):
            self._gains_of = number, name
        self._act(lambda: g432_presets.edit(lambda presets: presets.named(
            eq_presets.number_named(presets.presets, number, shown), name), model=model),
            NOT_RENAMED)

    def on_save_new(self, _button: Gtk.Button) -> None:
        """Ask the new preset's name, the active one's to start, then save it as the A50 Gen
        4's row does (_save_new_as), for the model shown at the click. The row is sensitive
        only once a read gave the presets (_set_sensitive)."""
        presets, model = cast("g432_presets.Presets", self._presets), self.model
        ask_name(self._parent, presets.current.name,
                 lambda name: self._save_new_as(name, model), SAVE_HEADING)

    def _save_new_as(self, name: str, model: HeadsetModel) -> None:
        """The active preset, its gains as the sliders show them once they are sent, saved
        as a new preset named `name`, which plays."""
        # A gain still settling goes to the preset shown first, so the new one has it.
        self._gains_send.flush()
        if model is self.model and self._presets is not None:
            # A move before the next read goes to the new preset, which plays then.
            self._gains_of = len(self._presets.presets) + 1, name
        self._update(lambda presets: presets.saved_as(name), NOT_SAVED_NEW, model)

    def _change_once_asked(
            self, heading: str, body: str, button: str, failed: str,
            change: Callable[[g432_presets.Presets, int], g432_presets.Presets]) -> None:
        """`change` of the active preset once asked, `heading` taking its name. The preset,
        where the file holds it then (eq_presets.number_named), and the model are the ones
        shown at the click: a poll can name another headset while the confirmation is open."""
        if self._presets is None:
            return
        number, name, model = self._presets.active, self._presets.current.name, self.model

        def change_it() -> None:
            self._gains_send.cancel()
            # The sliders show what the change writes over until the read after it.
            self._gains_of = None
            self._update(lambda presets: change(
                presets, eq_presets.number_named(presets.presets, number, name)), failed, model)
        confirm(self._parent, heading.format(name), body, button, change_it)

    def on_reset(self, _button: Gtk.Button) -> None:
        """The active preset's default back once asked, as Delete preset is: it writes over
        the preset's name and gains (the owner's decision of 2026-10-22)."""
        self._change_once_asked(RESET_HEADING, RESET_BODY, RESET_BUTTON, NOT_RESET,
                                g432_presets.Presets.reset)

    def on_delete(self, _button: Gtk.Button) -> None:
        """The active preset, one saved as new, deleted once asked; the preset before it
        plays, set on the running chain (g432_eq.update)."""
        self._change_once_asked(DELETE_HEADING, DELETE_BODY, DELETE_RESPONSE, NOT_DELETED,
                                g432_presets.Presets.deleted)

    def on_copy(self, _button: Gtk.Button) -> None:
        """The A50 Gen 4 preset picked, fitted over the active preset once asked: it writes
        over the preset's name and gains."""
        index = self.a50_list.get_selected()
        numbers = sorted(self._a50)
        if index >= len(numbers):
            return
        preset = self._a50[numbers[index]]
        self._change_once_asked(
            COPY_HEADING, COPY_BODY.format(preset.name), COPY_RESPONSE, NOT_COPIED,
            lambda presets, number: presets.put(number, g432_fit.from_a50(preset)))

    def refresh_a50(self) -> None:
        """Ask the daemon for the A50's presets, which it answers from what it holds,
        unless a read is out."""
        if self._a50_pending or self._closed:
            return
        self._a50_pending = True
        self._worker.call({"command": "headset", "ask": False}, self._on_a50)

    def _on_a50(self, reply: Reply) -> None:
        self._a50_pending = False
        if self._closed:
            return
        found: dict[int, a50_library.EqPreset] = {}
        if isinstance(reply, Exception):
            why = unreachable(reply)
        elif not reply.get("ok"):
            why = str(reply.get("error") or NO_A50)
        else:
            why = A50_SILENT if reply.get("answering") is False else A50_UNREAD
            entries = reply.get("presets")
            for entry in entries if isinstance(entries, list) else ():
                if ((number := a50_library.slot_number(entry)) is not None
                        and (preset := a50_library.a50_preset(entry)) is not None):
                    found[number] = preset
        self.show_a50(found, why)

    def show_a50(self, found: dict[int, a50_library.EqPreset], why: str) -> None:
        """The Copy row: the A50's presets it can take in its dropdown, or `why` there are
        none."""
        labels = [preset_label(number, found[number].name) for number in sorted(found)]
        if labels != self._a50_labels:
            self._a50_labels = labels
            self.a50_list.set_model(Gtk.StringList.new(labels))
        self._a50 = found
        self.copy_row.set_subtitle("" if found else displayable(capitalized(why)))
        self._set_sensitive()

    def on_switch(self, row: Adw.SwitchRow, _pspec: GObject.ParamSpec) -> None:
        if self._loading.active:
            return
        on, model = row.get_active(), self.model
        switch = g432_eq.switch_on if on else g432_eq.switch_off
        description = EQ_WORDS[model.key].description
        self._act(lambda: switch(run=self._run, model=model),
                  (NOT_STARTED_OF if on else NOT_STOPPED_OF).format(description), switching=True)

    def _act(self, work: Callable[[], object], failed: str, switching: bool = False,
             model: HeadsetModel | None = None) -> None:
        """Run `work`, the equalizer's, on the worker for `model`, the model shown unless
        given; `failed` is the toast line for a failure too long for a toast. `switching` for
        a switch, whose saves are in the runtime folder; every other action saves the
        model's presets file."""
        self._acting += 1
        self._set_sensitive()
        model = self.model if model is None else model
        self._worker.run(work, lambda result: self._acted(result, failed, switching, model))

    def _acted(self, result: Result, failed: str, switching: bool, model: HeadsetModel) -> None:
        """An action's end for `model`: its failure toasted, and the window told (kept_up),
        or the notes a switch gave, one toast each, and the window told of a note of a failed
        step; then the equalizer's state read again."""
        if self._closed:
            return
        self._acting -= 1
        if isinstance(result, list):
            named = EQ_WORDS[model.key]
            # Any other note says a step failed, and the toast is all that says it.
            if set(result) - {named.selected, named.reselected, named.moved, named.moved_nowhere}:
                self._parent.kept_up()
            # Notes come from an equalizer's switch alone, on a model whose equalizer runs here.
            for note in result:
                toast_note(self._toast, note, named.description)
        elif isinstance(result, OSError) and not switching:
            # Only a save raises one: the programs' failures come as StepError.
            self._parent.kept_up()
            toast_not_saved(self._toast, g432_presets.gains_path(model=model), result)
        elif isinstance(result, Exception):
            if not isinstance(result, g432_eq.NotSetLive):
                # A change saved but not set live is kept, so the close need not stay for it.
                self._parent.kept_up()
            words, line = switch_failure(
                result, SAVED_NOT_SET if isinstance(result, g432_eq.NotSetLive) else failed,
                "Equalizer")
            toast_note(self._toast, words, line, one_sentence=True)
        self.refresh_eq()

    def tick(self) -> None:
        """The window's poll, on a model whose equalizer the window runs: the A50's presets,
        and the equalizer's state but while an action runs, whose end reads it."""
        if not self.model.onboard_eq:
            self.refresh_a50()
        if not self._acting:
            self.refresh_eq()

    def flush(self) -> bool:
        """Send a gain still settling now, for the window's close; True if one was."""
        return self._gains_send.flush()

    def close(self) -> None:
        """The window closed: a gain still settling is dropped, and a read or an action that
        lands after shows nothing."""
        self._closed = True
        self._gains_send.cancel()
