"""The Headset tab's page for the Logitech G432, and the stack that shows it or the A50's.

The daemon reads the G432 and sets its sidetone (`g432`, `g432_sidetone`). The window runs
the noise suppression (gearbench.g432_mic) and Logitech G432 Equalizer (gearbench.g432_eq)
itself: their programs and the files they save go through the worker thread, as the
Spatial sound group's do. Copy from Astro A50 lists the presets the daemon holds of the A50
(`headset`, which then asks the base station nothing). Building it runs nothing.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import replace
from typing import cast

from gi.repository import Adw, GLib, GObject, Gtk

from .. import a50_library, astro, eq_presets, g432_eq, g432_fit, g432_mic, spatial
from ..config import shown_path
from ..g432 import PRODUCTS, SIDETONE_MEANS, SIDETONE_RANGE, SIDETONE_UNREAD_RESET
from ..wording import reason, whole
from .style import (
    ASLEEP,
    PRESET_RESET,
    SAVE_FAILED,
    SAVE_FAILED_LINE,
    EntryTexts,
    confirm,
    displayable,
    name_refusal,
    percent,
    plain_row,
    preset_label,
    put_dot,
    set_dot,
    spoken_scale,
    status_dot,
    toast_not_saved,
    toast_note,
    top_switcher,
    unit_words,
)
from .worker import Call, DaemonWorker, LoadingGuard, Reply, Result, Settle, unreachable

#: The G432's name, as `status` gives it among its headsets.
NAME = PRODUCTS[0x0A9C]
NOT_FOUND = "Not found"
CONNECTED = "Connected"
#: The firmware row's title, and its subtitle where the attach could not read the version,
#: as the Sidetone row's is where the daemon gave no level.
FIRMWARE = "Firmware"
NOT_READ = "Not read"
#: The sidetone row's title, and its slider's name for a screen reader.
SIDETONE = f"Sidetone: {SIDETONE_MEANS}"
SIDETONE_HELP = "You may hear it only while a program has the microphone open"
NO_SIDETONE = "This G432 lists no sidetone"
PUT_BACK = "Put back {}%"
PUT_BACK_HELP = "The level Gearbench first read from the headset"
PUT_BACK_NONE = f"Put back to {SIDETONE_UNREAD_RESET}%"
PUT_BACK_NONE_HELP = "Gearbench read no level before it first wrote one"
#: The next step where switching on leaves the default output alone, and the warning
#: that what plays straight to the G432 meanwhile plays at 0 dB.
EQ_HELP = ("A separate output: pick it in GNOME's Sound settings unless switching it on "
           "makes it the default. While it is on, the G432's own output stays at 0 dB.")
RESET_HELP = "The active preset's own name and gains back"
#: The row that deletes the active preset, one saved as new, and its confirmation.
DELETE = "Delete preset"
DELETE_HELP = "The preset before it plays in its place"
DELETE_BUTTON = "Delete"
DELETE_HEADING = "Delete {}?"
DELETE_BODY = f"{DELETE_HELP}. This cannot be undone."
#: The Save as new row's title: the name typed there is the new preset's.
SAVE_NEW = "Save as new"
COPY = "Copy from Astro A50"
COPY_HELP = "Replaces the active preset's name and gains"
COPY_BUTTON = "Copy"
#: The Copy row's dropdown's name for a screen reader.
A50_PRESET = "Astro A50 preset"
#: The Copy row's subtitle before the first `headset` reply, and for a refusal with no words.
NO_A50 = "Plug in the Astro A50's base station"
#: Its subtitle with the base station there and none of its presets held: the daemon reads
#: them for the A50's page on the Headset tab of the active window, or as the headset links.
A50_UNREAD = "The Astro A50's presets are not read yet"
#: Its subtitle with the base station there, not answering, and none of its presets held.
A50_SILENT = "The Astro A50's base station is not answering"
MIC_HELP = f"Makes {g432_mic.DESCRIPTION}, a cleaned-up input; it stops when you log out"
#: Each action's toast line when its failure is too long for a toast, the whole failure
#: behind Details.
MIC_NOT_STARTED = f"{g432_mic.FEATURE} did not start"
MIC_NOT_STOPPED = f"{g432_mic.FEATURE} did not stop"
NOT_STARTED = f"{g432_eq.DESCRIPTION} did not start"
NOT_STOPPED = f"{g432_eq.DESCRIPTION} did not stop"
NOT_SET = "The gains did not change"
NOT_PICKED = "The preset did not change"
NOT_RENAMED = "The name did not change"
NOT_RESET = "The preset was not reset"
NOT_SAVED_NEW = "The new preset was not saved"
NOT_DELETED = "The preset was not deleted"
NOT_COPIED = "The A50 preset was not copied"
#: The toast line for any of them whose change was saved and not set on the running chain
#: (g432_eq.NotSetLive).
SAVED_NOT_SET = "Saved, but not set on the equalizer"
#: How long a slider waits after the last change before it sends, so a drag is one write.
SETTLE_MS = 300
#: The dot for each summary kind, the equalizer's and the noise suppression's; a kind
#: missing here is red.
DOTS: dict[str, bool | str | None] = {
    "ready": True, "off": None, **dict.fromkeys(("starting", "waiting", "unlinked"), ASLEEP)}


class G432Page:
    """The G432's page: `call` is the window's, for the sidetone's writes, whose refusals
    it shows; reads go to the worker straight, so a poll that finds no G432 toasts
    nothing. `run` runs the equalizer's and the noise suppression's programs; `parent` is
    the window, which Delete preset's confirmation opens over. HeadsetStack puts the
    Spatial sound group last in `page` while this page shows."""

    def __init__(self, parent: Gtk.Window, worker: DaemonWorker, call: Call,
                 toast: Callable[..., object], run: spatial.Run = subprocess.run) -> None:
        self._parent = parent
        self._worker = worker
        self._call = call
        self._toast = toast
        self._run = run
        self._loading = LoadingGuard()
        #: A read is out, the G432's, the equalizer's, the A50's or the noise suppression's;
        #: an action runs; the window has closed.
        self._pending = self._eq_pending = self._a50_pending = self._mic_pending = False
        self.busy = False
        self._closed = False
        #: The units, "eq" or "mic", whose read an action held back, dropped as it landed or
        #: skipped by a tick, for the action's end to read (_acted).
        self._owed: set[str] = set()
        #: The level the last reply gave, None while there is none, and whether it said this
        #: G432 lists no sidetone (NO_SIDETONE).
        self._level: int | None = None
        self._unlisted = False
        #: Sidetone writes sent and not answered yet, any of which may still move the level.
        self._sidetone_unanswered = 0
        self._sidetone_send = Settle(self._send_sidetone)
        self._gains_send = Settle(self._send_gains)
        #: The gains the last read gave or the last send asked for: a send of the same skips;
        #: and the preset the sliders show, its number and name, None before a read.
        self._gains = g432_eq.Gains()
        self._gains_of: tuple[int, str] | None = None
        #: The presets the last read gave, None before the first, and the Preset row's
        #: labels; the Name row's text as the page put it.
        self._presets: g432_eq.Presets | None = None
        self._preset_labels: list[str] = []
        self._shown = EntryTexts()
        #: The A50's presets Copy can take, by number, from the daemon's last `headset`
        #: reply, and the Copy row's dropdown's labels, in their order.
        self._a50: dict[int, astro.EqPreset] = {}
        self._a50_labels: list[str] = []
        self.kind: str | None = None
        #: The noise suppression's state kind (g432_mic.summary), None before a read.
        self.mic_kind: str | None = None
        self.widget = self._build()

    # -- construction -----------------------------------------------------

    def _build(self) -> Gtk.Widget:
        self.page = page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                                   margin_top=18, margin_bottom=18, margin_start=18,
                                   margin_end=18)
        page.append(self._status_group())
        page.append(self._sidetone_group())
        page.append(self._mic_group())
        page.append(self._eq_group())
        return Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
                                  child=page)

    def _status_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title=NAME)
        self.state_row = plain_row(Adw.ActionRow, NOT_FOUND, "")
        self.state_dot = status_dot()
        self.state_row.add_prefix(self.state_dot)
        group.add(self.state_row)
        # Shown while the G432 is there; the version is the adapter's own text.
        self.firmware_row = plain_row(Adw.ActionRow, FIRMWARE, "", visible=False)
        group.add(self.firmware_row)
        return group

    def _sidetone_group(self) -> Adw.PreferencesGroup:
        self.sidetone_group = Adw.PreferencesGroup(title="Sidetone", description=SIDETONE_HELP)
        self.sidetone_row = plain_row(Adw.ActionRow, SIDETONE, "")
        self.sidetone_scale = spoken_scale(*SIDETONE_RANGE, SIDETONE)
        self.sidetone_scale.connect("value-changed", self.on_sidetone)
        self.sidetone_row.add_suffix(self.sidetone_scale)
        self.sidetone_group.add(self.sidetone_row)
        self.put_back_row = Adw.ButtonRow(title=PUT_BACK_NONE,
                                          start_icon_name="edit-undo-symbolic")
        self.put_back_row.connect("activated", self.on_put_back)
        self.sidetone_group.add(self.put_back_row)
        return self.sidetone_group

    def _mic_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Microphone", description=MIC_HELP)
        self.mic_row = plain_row(Adw.SwitchRow, g432_mic.FEATURE, "")
        self.mic_dot = status_dot()
        self.mic_row.connect("notify::active", self.on_mic_switch)
        group.add(self.mic_row)
        return group

    def _eq_group(self) -> Adw.PreferencesGroup:
        group = self.eq_group = Adw.PreferencesGroup(title="Equalizer", description=EQ_HELP)
        self.switch_row = plain_row(Adw.SwitchRow, g432_eq.DESCRIPTION, "")
        self.eq_dot = status_dot()
        self.switch_row.connect("notify::active", self.on_switch)
        group.add(self.switch_row)
        self.preset_row = Adw.ComboRow(title="Preset", model=Gtk.StringList.new([]))
        self.preset_row.connect("notify::selected", self.on_preset)
        group.add(self.preset_row)
        self.name_row = Adw.EntryRow(title="Name", show_apply_button=True)
        self.name_row.connect("apply", self.on_name)
        group.add(self.name_row)
        low, high = g432_eq.DB_RANGE
        names = ("Bass", *(g432_eq.hertz(freq) for freq in g432_eq.BAND_FREQS), "Treble")
        #: The bass, each band and the treble, in the order Gains.all gives them.
        self.gain_rows: list[Adw.ActionRow] = []
        self.gain_scales: list[Gtk.Scale] = []
        for name in names:
            row = plain_row(Adw.ActionRow, name, "")
            scale = spoken_scale(low, high, f"{name} gain", page=1, origin=False,
                                 spoken=g432_eq.signed)
            scale.add_mark(0, Gtk.PositionType.BOTTOM, None)
            scale.connect("value-changed", self.on_gain, row)
            row.add_suffix(scale)
            group.add(row)
            self.gain_rows.append(row)
            self.gain_scales.append(scale)
        group.add(self._copy_row())
        self.save_row = Adw.EntryRow(title=SAVE_NEW, show_apply_button=True)
        self.save_row.connect("apply", self.on_save_new)
        group.add(self.save_row)
        self.reset_row = Adw.ButtonRow(title=PRESET_RESET, start_icon_name="edit-undo-symbolic",
                                       tooltip_text=RESET_HELP)
        self.reset_row.connect("activated", self.on_reset)
        group.add(self.reset_row)
        self.delete_row = plain_row(Adw.ActionRow, DELETE, DELETE_HELP)
        self.delete_button = Gtk.Button(label=DELETE_BUTTON, valign=Gtk.Align.CENTER,
                                        css_classes=["destructive-action"])
        self.delete_button.connect("clicked", self.on_delete)
        self.delete_row.add_suffix(self.delete_button)
        group.add(self.delete_row)
        self._set_sensitive()
        return group

    def _copy_row(self) -> Adw.ActionRow:
        """Copy from Astro A50: a dropdown of the A50's presets and the Copy button."""
        self.copy_row = plain_row(Adw.ActionRow, COPY, NO_A50, tooltip_text=COPY_HELP)
        self.a50_list = Gtk.DropDown(model=Gtk.StringList.new([]), valign=Gtk.Align.CENTER)
        self.a50_list.update_property([Gtk.AccessibleProperty.LABEL], [A50_PRESET])
        self.copy_button = Gtk.Button(label=COPY_BUTTON, valign=Gtk.Align.CENTER)
        self.copy_button.connect("clicked", self.on_copy)
        self.copy_row.add_suffix(self.a50_list)
        self.copy_row.add_suffix(self.copy_button)
        return self.copy_row

    # -- the sidetone -------------------------------------------------------

    def refresh(self) -> None:
        """Ask the daemon for the G432, unless a read is out. It answers from what it read
        at the attach and each write since, sending the headset nothing."""
        if self._pending or self._closed:
            return
        self._pending = True
        self._worker.call({"command": "g432"}, self._on_info)

    def _on_info(self, info: Reply) -> None:
        self._pending = False
        if self._closed:
            return
        if isinstance(info, Exception):
            self._missing(unreachable(info))
        elif not info.get("ok"):
            self._missing(str(info.get("error") or NOT_FOUND))
        else:
            self.show(info)

    def _missing(self, why: str) -> None:
        set_dot(self.state_dot, False)
        self.state_row.set_title(NOT_FOUND)
        self.state_row.set_subtitle(why)
        self.state_row.set_tooltip_text(None)
        self.firmware_row.set_visible(False)
        self._level = None
        self._show_level()
        self.sidetone_group.set_sensitive(False)

    def show(self, info: dict) -> None:
        """The state row, the firmware version, the slider and Put back from the `g432`
        reply. The slider stays where it is while a send settles or a write is out."""
        set_dot(self.state_dot, True)
        self.state_row.set_title(CONNECTED)
        self.state_row.set_subtitle("")
        self.state_row.set_tooltip_text(str(info.get("device") or "") or None)
        self.firmware_row.set_subtitle(displayable(str(info.get("firmware") or NOT_READ)))
        self.firmware_row.set_visible(True)
        low, high = SIDETONE_RANGE
        level, first = info.get("sidetone"), info.get("sidetone_first")
        self._unlisted = info.get("sidetone_found") is not True
        self.sidetone_group.set_sensitive(not self._unlisted)
        self._level = level if whole(level) and low <= level <= high else None
        if (self._level is not None and not self._sidetone_send.pending
                and not self._sidetone_unanswered):
            self._loading.put_value(self.sidetone_scale, self._level)
        self._show_level()
        known = whole(first) and low <= first <= high
        self.put_back_row.set_title(PUT_BACK.format(first) if known else PUT_BACK_NONE)
        self.put_back_row.set_tooltip_text(PUT_BACK_HELP if known else PUT_BACK_NONE_HELP)

    def _show_level(self) -> None:
        """The sidetone row's subtitle: NO_SIDETONE for a G432 that lists none, NOT_READ
        while the daemon holds no level and no write is settling or out, since the slider
        then sits where the page left it, else the slider's level."""
        if self._unlisted:
            words = NO_SIDETONE
        elif (self._level is None and not self._sidetone_send.pending
              and not self._sidetone_unanswered):
            words = NOT_READ
        else:
            words = percent(round(self.sidetone_scale.get_value()))
        self.sidetone_row.set_subtitle(words)

    def on_sidetone(self, _scale: Gtk.Scale) -> None:
        # The send first: the subtitle gives the level of a move that settles.
        if not self._loading.active:
            self._sidetone_send.restart(SETTLE_MS)
        self._show_level()

    def _send_sidetone(self) -> None:
        """Write the slider's level, unless it is the last reply's and no write is out that
        may yet move the headset away from it."""
        level = round(self.sidetone_scale.get_value())
        if level == self._level and not self._sidetone_unanswered:
            return
        self._sidetone_unanswered += 1
        self._call({"command": "g432_sidetone", "level": level}, self._answered)

    def on_put_back(self, _row: Adw.ButtonRow) -> None:
        self._sidetone_send.cancel()
        self._sidetone_unanswered += 1
        self._call({"command": "g432_sidetone", "reset": True}, self._answered)

    def _answered(self, reply: dict | None) -> None:
        """A write's reply, the whole `g432` reply; None, a refusal the window showed,
        reads the G432 again, which puts the slider back once no other write is out."""
        self._sidetone_unanswered -= 1
        if reply is None:
            self.refresh()
        elif not self._closed:
            self.show(reply)

    # -- the equalizer ------------------------------------------------------

    def refresh_eq(self) -> None:
        """Read the equalizer's state on the worker thread, unless a read is out."""
        if self._eq_pending or self._closed:
            return
        self._eq_pending = True
        self._worker.run(lambda: g432_eq.read_state(run=self._run), self._on_eq_state)

    def _on_eq_state(self, result: Result) -> None:
        self._eq_pending = False
        if self._closed:
            return
        # A read that lands while an action runs was queued before it: the action's end
        # reads it again (_acted).
        if self.busy:
            self._owed.add("eq")
            return
        if not isinstance(result, g432_eq.State):
            self.kind = None
            put_dot(self.switch_row, self.eq_dot, False, f"Could not be read: {result}")
            self._set_sensitive()
            return
        self.kind = self._show_switch(self.switch_row, self.eq_dot, result,
                                      g432_eq.summary(result))
        if not self._gains_send.pending:
            self._show_gains(result.presets)
        self._show_presets(result.presets)
        self._set_sensitive()

    def _show_gains(self, presets: g432_eq.Presets) -> None:
        """The active preset's gains on the sliders, the preset a move then goes to."""
        self._gains = gains = presets.current.gains
        self._gains_of = presets.active, presets.current.name
        for row, scale, gain in zip(self.gain_rows, self.gain_scales, gains.all(),
                                    strict=True):
            self._loading.put_value(scale, gain)
            row.set_subtitle(g432_eq.signed(gain))

    def _show_switch(self, row: Adw.SwitchRow, dot: Gtk.Image,
                     state: g432_eq.State | g432_mic.State, summed: tuple[str, str]) -> str:
        """A switch row on its unit's `state` and its summary, (kind, words): the words its
        subtitle, the switch on while the unit runs or starts, and its dot. The kind."""
        kind, words = summed
        words = unit_words(words)
        row.set_subtitle(words)
        if state.systemd is None:
            self._loading.put_active(row, state.active in ("active", "activating"))
        put_dot(row, dot, DOTS.get(kind, False), words)
        return kind

    # -- the noise suppression ------------------------------------------------

    def refresh_mic(self) -> None:
        """Read the noise suppression's state on the worker thread, unless a read is out."""
        if self._mic_pending or self._closed:
            return
        self._mic_pending = True
        self._worker.run(lambda: g432_mic.read_state(run=self._run), self._on_mic_state)

    def _on_mic_state(self, result: Result) -> None:
        self._mic_pending = False
        if self._closed:
            return
        # As the equalizer's.
        if self.busy:
            self._owed.add("mic")
            return
        if isinstance(result, g432_mic.State):
            self.mic_kind = self._show_switch(self.mic_row, self.mic_dot, result,
                                              g432_mic.summary(result))
        else:
            self.mic_kind = None
            put_dot(self.mic_row, self.mic_dot, False, f"Could not be read: {result}")
        self._set_sensitive()

    def on_mic_switch(self, row: Adw.SwitchRow, _pspec: GObject.ParamSpec) -> None:
        if self._loading.active:
            return
        on = row.get_active()
        switch = g432_mic.switch_on if on else g432_mic.switch_off
        self._act(lambda: switch(run=self._run), MIC_NOT_STARTED if on else MIC_NOT_STOPPED,
                  mic=True, switching=True)

    def _show_presets(self, presets: g432_eq.Presets) -> None:
        """The Preset row's list and its pick, and the Name row's text but while it is
        being typed in, which a switch of preset puts back. The group's description says
        why a file that does not read, or read in part, takes no change (Presets.refused),
        while the rows that change a preset are insensitive (_set_sensitive)."""
        switched = self._presets is None or presets.active != self._presets.active
        self._presets = presets
        # Markup: the sentence quotes the file's path, a lone surrogate in it as U+FFFD, and
        # its cause, a preset's name among them.
        self.eq_group.set_description(GLib.markup_escape_text(displayable(presets.refused))
                                      if presets.refused else EQ_HELP)
        labels = [preset_label(number, presets.get(number).name)
                  for number in presets.numbers]
        if labels != self._preset_labels:
            self._preset_labels = labels
            self._loading.put_model(self.preset_row, Gtk.StringList.new(labels))
        self._loading.put_selected(self.preset_row, presets.active - 1)
        if switched or not self._shown.typing(self._parent, self.name_row):
            self._shown.show(self.name_row, presets.current.name)

    def _set_sensitive(self) -> None:
        self.switch_row.set_sensitive(self.kind not in (None, "no_manager") and not self.busy)
        self.mic_row.set_sensitive(self.mic_kind not in (None, "no_manager") and not self.busy)
        # The presets are in a file of the window's own: no unit or PipeWire needed. One
        # that does not read, or read in part, takes no change (Presets.whole).
        changeable = self._presets is not None and not self._presets.refused
        editable = changeable and not self.busy
        for row in (self.preset_row, self.name_row):
            row.set_sensitive(editable)
        # Until a read gives the gains and the preset they show (_gains_of), the sliders sit
        # at the scale's low end, which a move would save over a preset's. Not tied to
        # `busy`: the page sends gains while a drag goes on.
        for gain_row in self.gain_rows:
            gain_row.set_sensitive(changeable)
        self.copy_row.set_sensitive(editable and bool(self._a50))
        presets = self._presets
        # A preset saved as new has no default, and a shipped one is not deleted; past
        # PRESETS_MAX no more can be saved.
        shipped = presets is not None and presets.active in g432_eq.SHIPPED
        self.reset_row.set_sensitive(editable and shipped)
        self.reset_row.set_tooltip_text(
            RESET_HELP if shipped or presets is None
            else g432_eq.NO_DEFAULT.format(presets.active))
        self.delete_row.set_sensitive(editable and not shipped)
        self.delete_row.set_subtitle(
            g432_eq.NO_DELETE.format(presets.active) if shipped and presets is not None
            else DELETE_HELP)
        room = presets is not None and len(presets.presets) < eq_presets.PRESETS_MAX
        self.save_row.set_sensitive(editable and room)
        self.save_row.set_tooltip_text(None if room or presets is None
                                       else eq_presets.PRESETS_FULL)

    def gains(self) -> g432_eq.Gains:
        """The gains the sliders show."""
        values = [round(scale.get_value()) for scale in self.gain_scales]
        return g432_eq.Gains(bands=tuple(values[1:-1]), bass=values[0], treble=values[-1])

    def on_gain(self, scale: Gtk.Scale, row: Adw.ActionRow) -> None:
        row.set_subtitle(g432_eq.signed(round(scale.get_value())))
        if not self._loading.active:
            self._gains_send.restart(SETTLE_MS)

    def _send_gains(self) -> None:
        """The sliders' gains to the preset they show, where the file holds it then
        (g432_eq.set_gains), unless they are the last read's or send's. The rows are
        sensitive only once a read gave the presets (_set_sensitive), and with them that
        preset."""
        gains = self.gains()
        if gains != self._gains:
            self._gains = gains
            number, name = cast("tuple[int, str]", self._gains_of)
            self._act(lambda: g432_eq.set_gains(gains, number, name, run=self._run), NOT_SET)

    # -- the presets --------------------------------------------------------

    def _update(self, change: Callable[[g432_eq.Presets], g432_eq.Presets],
                failed: str) -> None:
        """Save `change` of the presets and set the active preset's gains on the running
        chain, on the worker (g432_eq.update); `failed` as _act's."""
        self._act(lambda: g432_eq.update(change, run=self._run), failed)

    def on_preset(self, row: Adw.ComboRow, _pspec: GObject.ParamSpec) -> None:
        number = row.get_selected() + 1
        if (self._loading.active or self._presets is None
                or number not in self._presets.numbers or number == self._presets.active):
            return
        name = self._presets.get(number).name
        # A gain still settling was the preset shown's: it goes there first.
        self._gains_send.flush()
        self._update(lambda presets: replace(
            presets, active=eq_presets.number_named(presets.presets, number, name)), NOT_PICKED)

    def on_name(self, row: Adw.EntryRow) -> None:
        """No loading guard: apply comes only from Enter or the apply button, never from
        set_text. A name changes nothing the chain plays, so it is only saved."""
        if self._presets is None:
            return
        name = row.get_text().strip()
        if (refusal := name_refusal(name)) is not None:
            self._toast(refusal)
            return
        number, shown = self._presets.active, self._presets.current.name
        self._shown.took(row)
        # A move before the next read goes to the preset by its new name.
        if self._gains_of == (number, shown):
            self._gains_of = number, name
        self._act(lambda: g432_eq.edit(lambda presets: presets.named(
            eq_presets.number_named(presets.presets, number, shown), name)), NOT_RENAMED)

    def on_save_new(self, row: Adw.EntryRow) -> None:
        """The active preset, its gains as the sliders show them once they are sent, saved
        as a new preset named as typed, which plays."""
        if self._presets is None:
            return
        name = row.get_text().strip()
        if (refusal := name_refusal(name)) is not None:
            self._toast(refusal)
            return
        # A gain still settling goes to the preset shown first, so the new one has it.
        self._gains_send.flush()
        self._shown.show(row, "")
        # A move before the next read goes to the new preset, which plays then.
        self._gains_of = len(self._presets.presets) + 1, name
        self._update(lambda presets: presets.saved_as(name), NOT_SAVED_NEW)

    def on_reset(self, _row: Adw.ButtonRow) -> None:
        if self._presets is None:
            return
        self._gains_send.cancel()
        number = self._presets.active
        self._update(lambda presets: presets.reset(number), NOT_RESET)

    def on_delete(self, _button: Gtk.Button) -> None:
        """The active preset, one saved as new, deleted once asked, where the file holds it
        then (eq_presets.number_named); the preset before it plays, set on the running chain
        (g432_eq.update). The row is sensitive only once a read gave the presets
        (_set_sensitive)."""
        presets = cast("g432_eq.Presets", self._presets)
        number, name = presets.active, presets.current.name

        def delete() -> None:
            self._gains_send.cancel()
            self._update(lambda presets: presets.deleted(
                eq_presets.number_named(presets.presets, number, name)), NOT_DELETED)
        confirm(self._parent, DELETE_HEADING.format(name), DELETE_BODY, DELETE_BUTTON, delete)

    def on_copy(self, _button: Gtk.Button) -> None:
        index = self.a50_list.get_selected()
        numbers = sorted(self._a50)
        if self._presets is None or not 0 <= index < len(numbers):
            return
        self._gains_send.cancel()
        number, name = self._presets.active, self._presets.current.name
        preset = self._a50[numbers[index]]
        self._update(lambda presets: presets.put(
            eq_presets.number_named(presets.presets, number, name), g432_fit.from_a50(preset)),
            NOT_COPIED)

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
        found: dict[int, astro.EqPreset] = {}
        if isinstance(reply, Exception):
            why = unreachable(reply)
        elif not reply.get("ok"):
            why = str(reply.get("error") or NO_A50)
        else:
            why = A50_SILENT if reply.get("answering") is False else A50_UNREAD
            entries = reply.get("presets")
            for entry in entries if isinstance(entries, list) else ():
                # a50_preset first: it gives None for an entry that is not a dict.
                if ((preset := a50_library.a50_preset(entry)) is not None
                        and (number := entry.get("preset")) in astro.PRESETS):
                    found[number] = preset
        self.show_a50(found, why)

    def show_a50(self, found: dict[int, astro.EqPreset], why: str) -> None:
        """The Copy row: the A50's presets it can take in its dropdown, or `why` there are
        none."""
        labels = [preset_label(number, found[number].name) for number in sorted(found)]
        if labels != self._a50_labels:
            self._a50_labels = labels
            self.a50_list.set_model(Gtk.StringList.new(labels))
        self._a50 = found
        self.copy_row.set_subtitle("" if found else why)
        self._set_sensitive()

    def on_switch(self, row: Adw.SwitchRow, _pspec: GObject.ParamSpec) -> None:
        if self._loading.active:
            return
        on = row.get_active()
        switch = g432_eq.switch_on if on else g432_eq.switch_off
        self._act(lambda: switch(run=self._run), NOT_STARTED if on else NOT_STOPPED,
                  switching=True)

    def _act(self, work: Callable[[], object], failed: str, mic: bool = False,
             switching: bool = False) -> None:
        """Run `work` on the worker, the equalizer's, or with `mic` the noise suppression's;
        `failed` is the toast line for a failure too long for a toast. `switching` for a
        switch, whose saves are in the runtime folder; every other action saves g432-eq.json."""
        self.busy = True
        self._set_sensitive()
        self._worker.run(work, lambda result: self._acted(result, failed, mic, switching))

    def _acted(self, result: Result, failed: str, mic: bool, switching: bool) -> None:
        """An action's end: its failure toasted, or the notes a switch gave, one toast each,
        then the state it changed read again, and any read it held back."""
        if self._closed:
            return
        self.busy = False
        words = None
        if isinstance(result, list):
            for note in result:
                toast_note(self._toast, note, g432_eq.DESCRIPTION)
        elif isinstance(result, spatial.SpatialError):
            words = str(result)
            if isinstance(result, g432_eq.NotSetLive):
                failed = SAVED_NOT_SET
        elif isinstance(result, OSError):
            # Only a save raises one, or the runtime folder's stat: the programs' failures come
            # as SpatialError.
            if switching:
                words = SAVE_FAILED.format(f"in {shown_path(g432_eq.switch_folder())}",
                                           reason(result))
                failed = SAVE_FAILED_LINE.format("in the runtime folder")
            else:
                toast_not_saved(self._toast, g432_eq.gains_path(), result)
        elif isinstance(result, Exception):
            words = f"{g432_mic.FEATURE if mic else 'Equalizer'}: {result}"
        if words is not None:
            toast_note(self._toast, words, failed, one_sentence=True)
        owed, self._owed = self._owed | {"mic" if mic else "eq"}, set()
        if "eq" in owed:
            self.refresh_eq()
        if "mic" in owed:
            self.refresh_mic()

    # -- the window's ------------------------------------------------------

    def tick(self) -> None:
        """The window's poll: the G432 and the A50's presets from the daemon, and the
        equalizer's and the noise suppression's states but while an action runs, whose end
        reads them."""
        self.refresh()
        self.refresh_a50()
        if self.busy:
            self._owed |= {"eq", "mic"}
        else:
            self.refresh_eq()
            self.refresh_mic()

    def flush(self) -> bool:
        """Send what is still settling now, for the window's close; True if any."""
        sent = self._sidetone_send.flush()
        return self._gains_send.flush() or sent

    def close(self) -> None:
        self._closed = True
        self._sidetone_send.cancel()
        self._gains_send.cancel()


class HeadsetStack:
    """The Headset tab: the A50's page or the G432's, by what `status` says is attached,
    with a switcher between them while both are. The A50's shows while neither is.
    `attached` runs as `status` first says the G432 is there, for its page to read it.
    `boxes` are the A50's page's box and the G432's, and `spatial`, the one Spatial sound
    group, goes last in the box of the page that shows: a widget has one parent."""

    def __init__(self, a50: Gtk.Widget, g432: Gtk.Widget, attached: Callable[[], None],
                 spatial: Gtk.Widget, boxes: tuple[Gtk.Box, Gtk.Box]) -> None:
        self._attached = attached
        self._spatial, self._boxes = spatial, boxes
        boxes[0].append(spatial)
        #: The last status said a G432 is attached.
        self.g432_attached = False
        self.stack = Gtk.Stack()
        self.stack.add_titled(a50, "a50", "Astro A50")
        self.stack.add_titled(g432, "g432", NAME)
        # Ahead of the window's own handler (Polls.on_page), which reads the group on the
        # page that came up.
        self.stack.connect("notify::visible-child-name", self._place)
        self.switcher = top_switcher(self.stack)
        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.widget.append(self.switcher)
        self.widget.append(self.stack)

    def take(self, status: dict) -> None:
        headsets = status.get("headsets")
        g432 = isinstance(headsets, list) and NAME in headsets
        both = g432 and status.get("headset") is True
        came = g432 and not self.g432_attached
        # Before the flip, whose read (Polls.on_page) asks the G432's page only while one is.
        self.g432_attached = g432
        self.switcher.set_visible(both)
        if not both:
            self.stack.set_visible_child_name("g432" if g432 else "a50")
        if came:
            self._attached()

    @property
    def showing_g432(self) -> bool:
        return self.stack.get_visible_child_name() == "g432"

    def _place(self, _stack: Gtk.Stack, _pspec: GObject.ParamSpec) -> None:
        cast(Gtk.Box, self._spatial.get_parent()).remove(self._spatial)
        self._boxes[1 if self.showing_g432 else 0].append(self._spatial)
