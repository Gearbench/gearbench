"""The Headset tab's page for the Logitech G432 and the other headsets of its family
(gearbench.g432.MODELS).

The daemon reads the headset and writes it (`g432`, the page's own writes and the cards',
headset_cards). The window itself runs the noise suppression (gearbench.g432_mic) of every
model and the equalizer (gearbench.g432_eq) of each with none of its own: their programs and
the files they save go through the worker thread, as the Spatial sound group's do. The
Microphone group ends with Mic input, the microphone's volume as GNOME's Sound settings set
it (volume_rows), and Input level, which reads the microphone live (input_meter_row). Copy
from Astro A50 Gen 4 lists the presets the daemon holds of the A50 (`headset`, which then
asks the base station nothing). Building it runs nothing.
"""

from __future__ import annotations

import subprocess
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
    g432_mic,
    g432_presets,
    pipewire_client,
    pipewire_unit,
)
from ..battery import kept_at
from ..g432 import (
    G432_MODEL,
    MODEL_KEYED,
    POWER_OFF_CHOICES,
    PRODUCTS,
    SIDETONE_RANGE,
    SIDETONE_UNREAD_RESET,
    HeadsetModel,
)
from ..g432_eq_words import WORDS as EQ_WORDS
from ..pipewire_unit import RUNS_OR_STARTS
from ..settings_files import shown_path
from ..system_volume import MIC
from ..wording import capitalized, counted, hertz, reason, signed, whole
from .headset_cards import HeadsetEqCard, HeadsetKeysCard, LightsCard, Writes, band_row
from .input_meter_row import InputMeterRow
from .style import (
    ASLEEP,
    DELETE_HEADING,
    DELETE_RESPONSE,
    FIRMWARE,
    HEADSET_ASLEEP,
    HEADSET_TAB,
    NOT_DELETED,
    NOT_FOUND,
    NOT_READ,
    NOT_RENAMED,
    NOT_SAVED_NEW,
    PRESET,
    PRESET_RESET,
    SAVE_FAILED,
    SAVE_FAILED_LINE,
    SAVE_HEADING,
    SIDETONE,
    STARTING,
    WAITS_FOR_TEST,
    EntryTexts,
    ask_name,
    confirm,
    delete_preset_row,
    displayable,
    name_refusal,
    offer_save_new,
    percent,
    plain_row,
    preset_label,
    put_dot,
    save_new_row,
    set_dot,
    show_battery,
    show_looking,
    show_untested,
    spoken_scale,
    status_dot,
    status_rows,
    through,
    toast_not_saved,
    toast_note,
    untested_row,
    watch_name,
)
from .volume_rows import VolumeRows
from .worker import Call, DaemonWorker, LoadingGuard, Reply, Result, Settle, unreachable

if TYPE_CHECKING:                       # pragma: no cover
    from .window import Window

#: The page's name in the Headset tab's stack, the G432's name, as `status` gives it among
#: its headsets, which titles the page before `status` names a model, and every name of its
#: family.
PAGE = "g432"
NAME = G432_MODEL.short
NAMES = frozenset(PRODUCTS.values())
SIDETONE_HELP = "You may hear it only while a program has the microphone open"
PUT_BACK = "Put back {}%"
PUT_BACK_HELP = "The level Gearbench first read from the headset"
#: With no level read before any write, the level a reset writes, in PUT_BACK's form.
PUT_BACK_NONE = PUT_BACK.format(SIDETONE_UNREAD_RESET)
PUT_BACK_NONE_HELP = "Gearbench read no level before any write"
#: The next step where switching on leaves the default output alone, and the warning
#: that what plays straight to the headset meanwhile plays at 0 dB: the model's short name
#: goes in, and EQ_HELP is the G432's.
EQ_HELP_OF = ("Pick it in GNOME's Sound settings unless switching it on makes it the default; "
              "the {}'s own output stays at 0 dB while it is on")
EQ_HELP = EQ_HELP_OF.format(G432_MODEL.short)
RESET_HELP = "The active preset's own name and gains back"
#: Reset to default, its ellipsis for the question it opens (the HIG's writing style), where
#: the A50 Gen 4's, which asks nothing, has none; and its confirmation: it writes over the
#: preset's name and gains.
RESET_OPENER = f"{PRESET_RESET}…"
RESET_HEADING = "Reset {} to default?"
RESET_BODY = "The name and gains it has now are lost."
RESET_BUTTON = "Reset"
#: The row that deletes the active preset, one saved as new (DELETE_PRESET), and its
#: confirmation.
DELETE_HELP = "The preset before it plays in its place"
DELETE_BODY = f"{DELETE_HELP}. This cannot be undone."
#: Save as new preset…'s tooltip: the new preset plays at once.
SAVE_NEW_HELP = "Keeps the active preset's gains as a new preset, which plays"
COPY = "Copy from Astro A50 Gen 4"
COPY_HELP = "Replaces the active preset's name and gains"
#: Its button, its ellipsis for the question it opens (the HIG's writing style), and the
#: question's own response.
COPY_RESPONSE = "Copy"
COPY_OPENER = f"{COPY_RESPONSE}…"
#: Copy's confirmation, by the active preset's name and the A50 Gen 4 preset's.
COPY_HEADING = "Copy over {}?"
COPY_BODY = "The Astro A50 Gen 4's {} replaces its name and gains."
#: The Copy row's dropdown's name for a screen reader.
A50_PRESET = "Astro A50 Gen 4 preset"
#: The Copy row's subtitle before the first `headset` reply, and for a refusal with no words.
NO_A50 = "Plug in the Astro A50 Gen 4's base station"
#: Its subtitle with the base station there and none of its presets held: the daemon reads
#: them for the A50's page on the Headset tab of the active window, or as the headset links.
A50_UNREAD = "The Astro A50 Gen 4's presets are not read yet"
#: Its subtitle with the base station there, not answering, and none of its presets held.
A50_SILENT = "The Astro A50 Gen 4's base station is not answering"
#: The Noise suppression row's tooltip, the input's name in, and the G432's.
MIC_HELP_OF = "Pick {} in your chat program; it stops when you log out"
MIC_HELP = MIC_HELP_OF.format(g432_mic.DESCRIPTION)
#: Each action's toast line when its failure is too long for a toast, the whole failure
#: behind Details: the unit's name goes in a switch's.
NOT_STARTED_OF, NOT_STOPPED_OF = "{} did not start", "{} did not stop"
MIC_NOT_STARTED = NOT_STARTED_OF.format(g432_mic.FEATURE)
MIC_NOT_STOPPED = NOT_STOPPED_OF.format(g432_mic.FEATURE)
NOT_SET = "The gains did not change"
NOT_PICKED = "The preset did not change"
NOT_RESET = "The preset was not reset"
NOT_COPIED = "The Astro A50 Gen 4 preset was not copied"
#: The toast line for any of them whose change was saved and not set on the running chain
#: (g432_eq.NotSetLive).
SAVED_NOT_SET = "Saved, but not set on the equalizer"
#: How long a slider waits after the last change before it sends, so a drag is one write.
SETTLE_MS = 300
#: The dot for each summary kind, the equalizer's and the noise suppression's: a spinner
#: while it starts; a kind missing here is red.
DOTS: dict[str, bool | str | None] = {
    "ready": True, "off": None, "starting": STARTING,
    **dict.fromkeys(("waiting", "unlinked"), ASLEEP)}


def auto_off_label(minutes: int) -> str:
    """A time as the Auto shut-off row lists it, as the A50 Gen 4's row words its own."""
    return "Never" if minutes == 0 else counted(minutes, "minute")


#: The Auto shut-off row's title: the A50 Gen 4's name for the same job.
AUTO_OFF = astro.SETTING_NAMES["auto_off"]
#: The Auto shut-off row's choices, in POWER_OFF_CHOICES' order.
AUTO_OFF_LABELS = tuple(auto_off_label(minutes) for minutes in POWER_OFF_CHOICES)


class G432Page:
    """The page of the G432 or the headset of its family the daemon names: `call` is the
    window's, for the writes to the headset, whose refusals it shows; reads go to the worker
    straight, so a poll that finds no headset toasts nothing. `run` runs the equalizer's
    and the noise suppression's programs, Input level's pw-dump and Mic input's pw-dump and
    pw-cli, for `model`, the model the last reply named; `parent` is the window, which the
    page's dialogs open over and which the page and its cards use (headset_cards); `kb_base`
    gives the color a synced device takes from the keyboard, as the window holds it
    (Window.kb_base). HeadsetStack puts the Spatial sound group last in `page` while this page
    shows."""

    def __init__(self, parent: Window, worker: DaemonWorker, call: Call,
                 toast: Callable[..., object], kb_base: Callable[[], int],
                 run: pipewire_client.Run = subprocess.run) -> None:
        self._parent = parent
        self._worker = worker
        self._toast = toast
        self._run = run
        self._loading = LoadingGuard()
        #: A read is out, the G432's, the equalizer's, the A50's or the noise suppression's;
        #: the window has closed.
        self._pending = self._eq_pending = self._a50_pending = self._mic_pending = False
        self._closed = False
        #: How many actions of the equalizer's or the noise suppression's are out (busy): one
        #: can start while another runs.
        self._acting = 0
        #: The units, "eq" or "mic", whose read an action held back, dropped as it landed or
        #: skipped by a tick, for the action's end to read (_acted).
        self._owed: set[str] = set()
        #: The level the last reply gave, None while there is none.
        self._level: int | None = None
        #: The model the last reply named, the G432 before one: its equalizer runs here
        #: but on a model with one of its own, and its noise suppression on each.
        self.model: HeadsetModel = G432_MODEL
        #: The sidetone's writes, `out` of them not answered yet, any of which may still move
        #: the level.
        self._sidetone_writes = Writes(call, self._eq_answered)
        self._sidetone_send = Settle(self._send_sidetone)
        #: The power-off time's writes, the time the last reply gave, None while unknown, and
        #: the Auto shut-off row's labels.
        self._power_writes = Writes(call, self._eq_answered)
        self._power_off: int | None = None
        self._power_labels = list(AUTO_OFF_LABELS)
        self._gains_send = Settle(self._send_gains)
        #: What the headset holds itself, each a card of its own (gui/headset_cards.py).
        self.headset_eq = HeadsetEqCard(worker, toast, self._loading, call, self._eq_answered,
                                        SETTLE_MS)
        self.lights = LightsCard(parent, self._loading, call, self._eq_answered, toast,
                                 self.refresh, kb_base)
        self.keys = HeadsetKeysCard(parent, worker, self._loading, call, toast)
        #: The Lights card's Saved colors row, which Window.rebuild_saved fills.
        self.saved = self.lights.saved
        #: The gains the last read gave or the last send asked for: a send of the same skips;
        #: and the preset the sliders show, its number and name, None before a read.
        self._gains = g432_presets.Gains()
        self._gains_of: tuple[int, str] | None = None
        #: The presets the last read gave, None before the first, and the Preset row's
        #: labels; the Name row's text as the page put it.
        self._presets: g432_presets.Presets | None = None
        self._preset_labels: list[str] = []
        self._shown = EntryTexts()
        #: The A50's presets Copy can take, by number, from the daemon's last `headset`
        #: reply, and the Copy row's dropdown's labels, in their order.
        self._a50: dict[int, astro.EqPreset] = {}
        self._a50_labels: list[str] = []
        self.kind: str | None = None
        #: The noise suppression's state kind (g432_mic.summary), None before a read.
        self.mic_kind: str | None = None
        self.meter = InputMeterRow(worker, run)
        self.volumes = VolumeRows(worker, toast, run, words=(MIC,))
        self.widget = self._build()

    # -- construction -----------------------------------------------------

    def _build(self) -> Gtk.Widget:
        self.page = page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                                   margin_top=18, margin_bottom=18, margin_start=18,
                                   margin_end=18)
        page.append(self._status_group())
        page.append(self._mic_group())
        page.append(self._eq_group())
        # The equalizer in the headset shows where the one above does not, on a model with
        # an equalizer of its own.
        page.append(self.headset_eq.group)
        page.append(self.lights.group)
        page.append(self.keys.group)
        return Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
                                  child=page)

    def _status_group(self) -> Adw.PreferencesGroup:
        """The group titled by the headset's name, its rows in every device group's order
        (the owner's decision of 2026-10-22)."""
        # The tab's noun until a reply names the model.
        group = self.status_group = Adw.PreferencesGroup(title=HEADSET_TAB)
        self.state_row, self.state_dot, self.battery_row, self.battery_icon = status_rows(
            group)
        show_looking(self.state_row, self.state_dot)
        self.battery_row.set_visible(False)
        self.untested_row = untested_row(group, self._toast)
        # Shown while a headset is there; the version is the headset's own text.
        self.firmware_row = plain_row(Adw.ActionRow, FIRMWARE, "", visible=False)
        group.add(self.firmware_row)
        self.power_row = Adw.ComboRow(title=AUTO_OFF, visible=False,
                                      model=Gtk.StringList.new(AUTO_OFF_LABELS))
        self.power_row.connect("notify::selected", self.on_auto_off)
        group.add(self.power_row)
        return group

    def _sidetone_rows(self, group: Adw.PreferencesGroup) -> None:
        """The sidetone's slider and its Put back, the Microphone group's first rows, as the
        A50 Gen 4's: one job, one place (the owner's decision of 2026-10-22)."""
        self.sidetone_row = plain_row(Adw.ActionRow, SIDETONE, "", tooltip_text=SIDETONE_HELP)
        self.sidetone_scale = spoken_scale(*SIDETONE_RANGE, SIDETONE)
        self.sidetone_scale.connect("value-changed", self.on_sidetone)
        self.sidetone_row.add_suffix(self.sidetone_scale)
        group.add(self.sidetone_row)
        self.put_back_row = Adw.ButtonRow(title=PUT_BACK_NONE,
                                          start_icon_name="edit-undo-symbolic")
        self.put_back_row.connect("activated", self.on_put_back)
        group.add(self.put_back_row)
        # Grayed until a reply (show): before one the slider sits at 0 and Put back names
        # 0%, whatever the daemon would put back.
        self._sidetone_usable(False)

    def _sidetone_usable(self, usable: bool) -> None:
        """The sidetone's rows sensitive while a headset that lists a sidetone is there."""
        for row in (self.sidetone_row, self.put_back_row):
            row.set_sensitive(usable)

    def _eq_answered(self, reply: dict | None) -> None:
        """A write's reply, the whole `g432` reply; None, a failure the window showed, reads
        the headset again, which puts the rows back."""
        if reply is None:
            self.refresh()
        elif not self._closed:
            self.show(reply)

    def _mic_group(self) -> Adw.PreferencesGroup:
        group = self.mic_group = Adw.PreferencesGroup(title="Microphone")
        self._sidetone_rows(group)
        # Its help is its tooltip: the group holds the sidetone too.
        self.mic_row = plain_row(Adw.SwitchRow, g432_mic.FEATURE, "", tooltip_text=MIC_HELP)
        self.mic_dot = status_dot()
        self.mic_row.connect("notify::active", self.on_mic_switch)
        group.add(self.mic_row)
        group.add(self.volumes.rows[MIC])
        group.add(self.meter.row)
        return group

    def _eq_group(self) -> Adw.PreferencesGroup:
        group = self.eq_group = Adw.PreferencesGroup(title="Equalizer", description=EQ_HELP)
        self.switch_row = plain_row(Adw.SwitchRow, g432_eq.DESCRIPTION, "")
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
        names = ("Bass", *(hertz(freq) for freq in g432_presets.BAND_FREQS), "Treble")
        #: The bass, each band and the treble, in the order Gains.all gives them.
        self.gain_rows: list[Adw.ActionRow] = []
        self.gain_scales: list[Gtk.Scale] = []
        for name in names:
            row, scale = band_row(name, low, high, self.on_gain)
            group.add(row)
            self.gain_rows.append(row)
            self.gain_scales.append(scale)
        group.add(self._copy_row())
        self.save_row = save_new_row(SAVE_NEW_HELP, self.on_save_new)
        group.add(self.save_row)
        self.reset_row = Adw.ButtonRow(title=RESET_OPENER, start_icon_name="edit-undo-symbolic",
                                       tooltip_text=RESET_HELP)
        self.reset_row.connect("activated", self.on_reset)
        group.add(self.reset_row)
        self.delete_row, self.delete_button = delete_preset_row(DELETE_HELP, self.on_delete)
        group.add(self.delete_row)
        self._set_sensitive()
        return group

    def _copy_row(self) -> Adw.ActionRow:
        """Copy from Astro A50 Gen 4: a dropdown of the A50's presets and the Copy button."""
        self.copy_row = plain_row(Adw.ActionRow, COPY, NO_A50, tooltip_text=COPY_HELP)
        self.a50_list = Gtk.DropDown(model=Gtk.StringList.new([]), valign=Gtk.Align.CENTER)
        self.a50_list.update_property([Gtk.AccessibleProperty.LABEL], [A50_PRESET])
        self.copy_button = Gtk.Button(label=COPY_OPENER, valign=Gtk.Align.CENTER)
        self.copy_button.connect("clicked", self.on_copy)
        self.copy_row.add_suffix(self.a50_list)
        self.copy_row.add_suffix(self.copy_button)
        return self.copy_row

    # -- the headset and its sidetone ---------------------------------------

    @property
    def busy(self) -> bool:
        """An action the page runs itself, which the window's close waits for: one of the
        equalizer's or the noise suppression's on the computer, a change of the library of
        the equalizer in the headset, or Mic input's volume set."""
        return self._acting > 0 or self.headset_eq.busy or self.volumes.busy

    def refresh(self) -> None:
        """Ask the daemon for the G432, unless a read is out. It answers from what it last
        read from the headset or wrote to it, sending the headset nothing. The library of
        the equalizer in the headset is read again too."""
        self.headset_eq.refresh()
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
        self.state_row.set_subtitle(capitalized(why))
        self.state_row.set_tooltip_text(None)
        for row in (self.firmware_row, self.power_row, self.untested_row):
            row.set_visible(False)
        # A model last shown with a battery keeps the row, with a dash, as every device page.
        if self.battery_row.get_visible():
            show_battery(self.battery_row, self.battery_icon, None, gone=True)
        self.headset_eq.show(None, None)
        self.lights.show(None)
        self.keys.show(None)
        self.meter.show_headset(None)
        self.volumes.show_headset(None)
        self._level = None
        self._show_level()
        self._sidetone_usable(False)

    def show(self, info: dict) -> None:
        """The headset's name, the state row, the firmware version, the battery and its
        power-off time where it has them, the slider and Put back from the `g432` reply. The
        slider stays where it is while a send settles or a write is out."""
        model = MODEL_KEYED.get(str(info.get("model")), G432_MODEL)
        if model is not self.model:
            self._take_model(model)
        self.meter.show_headset(model.key)
        self.volumes.show_headset(model.key)
        self.status_group.set_title(model.short)
        # A wireless one switched off behind its receiver, as a keyboard or a mouse asleep:
        # amber, what to do, its last battery reading, and the headset's own rows off.
        answering = info.get("answering") is not False
        set_dot(self.state_dot, True if answering else ASLEEP)
        # The route, as the Keyboard tab's row; the node is in the tooltip.
        self.state_row.set_subtitle(through(model.plugged_by) if answering
                                    else HEADSET_ASLEEP)
        self.state_row.set_tooltip_text(str(info.get("device") or "") or None)
        self.firmware_row.set_subtitle(displayable(str(info.get("firmware") or NOT_READ)))
        self.firmware_row.set_visible(True)
        self._show_power(info)
        show_untested(self.untested_row, info.get("untested"))
        self.headset_eq.show(info.get("equalizer"), model)
        self.lights.show(info.get("lights"), info.get("flash_refused"))
        self.keys.show(info.get("g_keys"))
        low, high = SIDETONE_RANGE
        level, first = info.get("sidetone"), info.get("sidetone_first")
        # A control the headset lacks is hidden, as on every page; the CLI keeps its refusal.
        listed = info.get("sidetone_found") is True
        for row in (self.sidetone_row, self.put_back_row):
            row.set_visible(listed)
        self._sidetone_usable(listed and answering)
        for card in (self.headset_eq, self.lights):
            card.set_usable(answering)
        self._level = level if whole(level) and low <= level <= high else None
        if (self._level is not None and not self._sidetone_send.pending
                and not self._sidetone_writes.out):
            self._loading.put_value(self.sidetone_scale, self._level)
        self._show_level()
        known = whole(first) and low <= first <= high
        self.put_back_row.set_title(PUT_BACK.format(first) if known else PUT_BACK_NONE)
        self.put_back_row.set_tooltip_text(PUT_BACK_HELP if known else PUT_BACK_NONE_HELP)

    def take_leader(self, leader: str | None) -> None:
        """The keyboard that leads, by its key, for the Lighting group's follow switch
        (LightsCard.take_leader)."""
        self.lights.take_leader(leader)

    def follow_keyboard(self) -> None:
        """While the lights follow the keyboard, its color on their picker
        (LightsCard.follow_keyboard)."""
        self.lights.follow_keyboard()

    def set_editing(self, text: str) -> None:
        """Say which layer the G keys card's bindings are written to
        (HeadsetKeysCard.set_editing)."""
        self.keys.set_editing(text)

    def _take_model(self, model: HeadsetModel) -> None:
        """The page on `model`: its equalizer group, shown on a model with none of its own,
        and its noise suppression under their names, and the last model's states and gains
        still settling dropped, until the next tick reads them for it. A read out for the
        last model is read again as it lands (_on_eq_state, _on_mic_state)."""
        self.model = model
        eq = EQ_WORDS.get(model.key)
        self.eq_group.set_visible(eq is not None)
        if eq is not None:
            self.switch_row.set_title(eq.description)
            self.eq_group.set_description(EQ_HELP_OF.format(model.short))
        self.mic_row.set_tooltip_text(MIC_HELP_OF.format(g432_mic.WORDS[model.key].description))
        self._gains_send.cancel()
        self.headset_eq.cancel()
        self._presets = self._gains_of = self.kind = self.mic_kind = None
        self._set_sensitive()

    def _show_power(self, info: dict) -> None:
        """The Battery and Auto shut-off rows of a headset that lists a battery, hidden on
        another. Auto shut-off picks the time read, unless a write of it is out, from its
        choices and, where it is none of them or not read, one more entry that says it; it is
        grayed out until a time is read, while the headset does not answer, and where the
        daemon refuses the writes the headset keeps, saying WAITS_FOR_TEST beneath."""
        found = info.get("battery_found") is True
        self.battery_row.set_visible(found)
        self.power_row.set_visible(found)
        if not found:
            return
        battery = info.get("battery")
        # One that does not answer keeps its last reading, with when it was taken.
        show_battery(self.battery_row, self.battery_icon,
                     battery if isinstance(battery, dict) else None, kept_at(info))
        answering = info.get("answering") is not False
        minutes = info.get("power_off")
        self._power_off = minutes if whole(minutes) else None
        if not self._power_writes.out:
            self._show_power_off(self._power_off)
        refused = info.get("flash_refused")
        self.power_row.set_subtitle(WAITS_FOR_TEST if refused else "")
        self.power_row.set_sensitive(answering and not refused and self._power_off is not None)

    def _show_power_off(self, minutes: int | None) -> None:
        """Auto shut-off's choices, with an entry for a time that is none of them or not
        read, and the time picked."""
        offered = minutes in POWER_OFF_CHOICES
        labels = list(AUTO_OFF_LABELS)
        if not offered:
            labels.append(NOT_READ if minutes is None else auto_off_label(minutes))
        if labels != self._power_labels:
            self._power_labels = labels
            self._loading.put_model(self.power_row, Gtk.StringList.new(labels))
        self._loading.put_selected(self.power_row, POWER_OFF_CHOICES.index(minutes) if offered
                                   else len(POWER_OFF_CHOICES))

    def on_auto_off(self, row: Adw.ComboRow, _pspec: GObject.ParamSpec) -> None:
        """A time picked by hand goes to the headset, unless it is the last reply's and no
        write is out that may yet move the headset away from it."""
        index = row.get_selected()
        if (not self._loading.active and 0 <= index < len(POWER_OFF_CHOICES)
                and (self._power_writes.out or POWER_OFF_CHOICES[index] != self._power_off)):
            self._power_writes.send({"command": "g432_auto_off",
                                     "minutes": POWER_OFF_CHOICES[index]})

    def _show_level(self) -> None:
        """The sidetone row's subtitle: NOT_READ while the daemon holds no level and no write
        is settling or out, since the slider then sits where the page left it, else the
        slider's level."""
        if (self._level is None and not self._sidetone_send.pending
                and not self._sidetone_writes.out):
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
        if level == self._level and not self._sidetone_writes.out:
            return
        self._sidetone_writes.send({"command": "g432_sidetone", "level": level})

    def on_put_back(self, _row: Adw.ButtonRow) -> None:
        self._sidetone_send.cancel()
        self._sidetone_writes.send({"command": "g432_sidetone", "reset": True})

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
            self._owed.add("eq")
            return
        if not isinstance(result, g432_eq.State):
            self.kind = None
            put_dot(self.switch_row, self.eq_dot, False, f"Could not be read: {result}")
            self._set_sensitive()
            return
        self.kind = self._show_switch(self.switch_row, self.eq_dot, result,
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

    def _show_switch(self, row: Adw.SwitchRow, dot: Gtk.Image,
                     state: g432_eq.State | g432_mic.State, summed: tuple[str, str]) -> str:
        """A switch row on its unit's `state` and its summary, (kind, words): the words its
        subtitle, the switch on while the unit runs or starts, and its dot. The kind."""
        kind, words = summed
        words = capitalized(words)
        row.set_subtitle(words)
        if state.systemd is None:
            self._loading.put_active(row, state.active in RUNS_OR_STARTS)
        put_dot(row, dot, DOTS.get(kind, False), words)
        return kind

    # -- the noise suppression ------------------------------------------------

    def refresh_mic(self) -> None:
        """Read the noise suppression's state on the worker thread, unless a read is out."""
        if self._mic_pending or self._closed:
            return
        self._mic_pending = True
        model = self.model
        self._worker.run(lambda: g432_mic.read_state(run=self._run, model=model),
                         lambda result: self._on_mic_state(result, model))

    def _on_mic_state(self, result: Result, model: HeadsetModel) -> None:
        self._mic_pending = False
        if self._closed:
            return
        # As the equalizer's.
        if model is not self.model:
            self.refresh_mic()
            return
        # As the equalizer's.
        if self._acting:
            self._owed.add("mic")
            return
        if isinstance(result, g432_mic.State):
            self.mic_kind = self._show_switch(self.mic_row, self.mic_dot, result,
                                              g432_mic.summary(result, model=model))
        else:
            self.mic_kind = None
            put_dot(self.mic_row, self.mic_dot, False, f"Could not be read: {result}")
        self._set_sensitive()

    def on_mic_switch(self, row: Adw.SwitchRow, _pspec: GObject.ParamSpec) -> None:
        if self._loading.active:
            return
        on, model = row.get_active(), self.model
        switch = g432_mic.switch_on if on else g432_mic.switch_off
        self._act(lambda: switch(run=self._run, model=model),
                  MIC_NOT_STARTED if on else MIC_NOT_STOPPED, mic=True, switching=True)

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
        self.eq_group.set_description(GLib.markup_escape_text(displayable(presets.refused))
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
        self.switch_row.set_sensitive(self.kind not in (None, "no_manager") and not self._acting)
        self.mic_row.set_sensitive(self.mic_kind not in (None, "no_manager") and not self._acting)
        # The presets are in a file of the window's own: no unit or PipeWire needed. One
        # that does not read, or read in part, takes no change (Presets.whole).
        changeable = self._presets is not None and not self._presets.refused
        editable = changeable and not self._acting
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
        shipped = presets is not None and presets.active in g432_presets.SHIPPED
        self.reset_row.set_sensitive(editable and shipped)
        self.reset_row.set_tooltip_text(
            RESET_HELP if shipped or presets is None
            else capitalized(g432_presets.NO_DEFAULT.format(presets.active)))
        self.delete_row.set_sensitive(editable and not shipped)
        self.delete_row.set_subtitle(
            capitalized(g432_presets.NO_DELETE.format(presets.active))
            if shipped and presets is not None
            else DELETE_HELP)
        offer_save_new(self.save_row, editable, None if presets is None else presets.presets,
                       SAVE_NEW_HELP)

    def gains(self) -> g432_presets.Gains:
        """The gains the sliders show."""
        values = [round(scale.get_value()) for scale in self.gain_scales]
        return g432_presets.Gains(bands=tuple(values[1:-1]), bass=values[0], treble=values[-1])

    def on_gain(self, scale: Gtk.Scale, row: Adw.ActionRow) -> None:
        row.set_subtitle(signed(round(scale.get_value())))
        if not self._loading.active:
            self._gains_send.restart(SETTLE_MS)

    def _send_gains(self) -> None:
        """The sliders' gains to the preset they show, where the file holds it then
        (g432_eq.set_gains), unless they are the last read's or send's. The rows are
        sensitive only once a read gave the presets (_set_sensitive), and with them that
        preset."""
        gains, model = self.gains(), self.model
        if gains != self._gains:
            self._gains = gains
            number, name = cast("tuple[int, str]", self._gains_of)
            self._act(lambda: g432_eq.set_gains(gains, number, name, run=self._run, model=model),
                      NOT_SET)

    # -- the presets --------------------------------------------------------

    def _update(self, change: Callable[[g432_presets.Presets], g432_presets.Presets],
                failed: str, model: HeadsetModel | None = None) -> None:
        """Save `change` of `model`'s presets, the model shown's unless given, and set the
        active preset's gains on its running chain, on the worker (g432_eq.update); `failed`
        as _act's."""
        model = self.model if model is None else model
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
        number, shown, model = self._presets.active, self._presets.current.name, self.model
        self._shown.took(row)
        # A move before the next read goes to the preset by its new name.
        if self._gains_of == (number, shown):
            self._gains_of = number, name
        self._act(lambda: g432_presets.edit(lambda presets: presets.named(
            eq_presets.number_named(presets.presets, number, shown), name), model=model),
            NOT_RENAMED)

    def on_save_new(self, _row: Adw.ButtonRow) -> None:
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
            self._update(lambda presets: change(
                presets, eq_presets.number_named(presets.presets, number, name)), failed, model)
        confirm(self._parent, heading.format(name), body, button, change_it)

    def on_reset(self, _row: Adw.ButtonRow) -> None:
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
        self.copy_row.set_subtitle("" if found else capitalized(why))
        self._set_sensitive()

    def on_switch(self, row: Adw.SwitchRow, _pspec: GObject.ParamSpec) -> None:
        if self._loading.active:
            return
        on, model = row.get_active(), self.model
        switch = g432_eq.switch_on if on else g432_eq.switch_off
        description = EQ_WORDS[model.key].description
        self._act(lambda: switch(run=self._run, model=model),
                  (NOT_STARTED_OF if on else NOT_STOPPED_OF).format(description), switching=True)

    def _act(self, work: Callable[[], object], failed: str, mic: bool = False,
             switching: bool = False, model: HeadsetModel | None = None) -> None:
        """Run `work` on the worker, the equalizer's, or with `mic` the noise suppression's,
        for `model`, the model shown unless given; `failed` is the toast line for a failure
        too long for a toast. `switching` for a switch, whose saves are in the runtime
        folder; every other action saves the model's presets file."""
        self._acting += 1
        self._set_sensitive()
        model = self.model if model is None else model
        self._worker.run(work, lambda result: self._acted(result, failed, mic, switching, model))

    def _acted(self, result: Result, failed: str, mic: bool, switching: bool,
               model: HeadsetModel) -> None:
        """An action's end for `model`: its failure toasted, or the notes a switch gave, one
        toast each, then the state it changed read again, and any read it held back."""
        if self._closed:
            return
        self._acting -= 1
        words = None
        if isinstance(result, list):
            # Notes come from an equalizer's switch alone, on a model whose equalizer runs here.
            for note in result:
                toast_note(self._toast, note, EQ_WORDS[model.key].description)
        elif isinstance(result, pipewire_client.StepError):
            # A refusal is a fragment where it is made, capitalized where it is shown.
            words = capitalized(str(result))
            if isinstance(result, g432_eq.NotSetLive):
                failed = SAVED_NOT_SET
        elif isinstance(result, OSError):
            # Only a save raises one, or the runtime folder's stat: the programs' failures come
            # as StepError.
            if switching:
                words = SAVE_FAILED.format(f"in {shown_path(pipewire_unit.switch_folder())}",
                                           reason(result))
                failed = SAVE_FAILED_LINE.format("in the runtime folder")
            else:
                toast_not_saved(self._toast, g432_presets.gains_path(model=model), result)
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
        """The window's poll: the headset from the daemon, the noise suppression's state,
        and on a model whose equalizer the window runs the A50's presets and the
        equalizer's state, but for the states while an action runs, whose end reads them; and
        Mic input's volume (VolumeRows.refresh)."""
        self.refresh()
        self.volumes.refresh()
        if not self.model.onboard_eq:
            self.refresh_a50()
        if self._acting:
            self._owed |= {"eq", "mic"}
        else:
            self.refresh_eq()
            self.refresh_mic()

    def flush(self) -> bool:
        """Send what is still settling now, for the window's close; True if any."""
        sent = [settle.flush() for settle in (self._sidetone_send, self._gains_send,
                                               self.headset_eq, self.lights, self.volumes)]
        return any(sent)

    def close(self) -> None:
        self._closed = True
        for settle in (self._sidetone_send, self._gains_send, self.headset_eq, self.lights):
            settle.cancel()
        self.meter.close()
        self.volumes.close()
