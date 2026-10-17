"""The headset tab: the Astro A50's base station settings.

A change goes to the base station's RAM at once and to its flash only on Save,
so a banner lists what differs from flash. The tab polls the daemon on its own
timer, with its own re-entry guard, as the Mouse tab does.
"""

from __future__ import annotations

import time
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import cast

from gi.repository import Adw, Gio, GLib, GObject, Gtk

from ..astro import (
    AUTO_OFF_MINUTES,
    BALANCE_RANGE,
    BANDS,
    FREQ_RANGE,
    GAIN_RANGE,
    NAME_BYTES,
    PRESETS,
    SETTING_CHOICES,
    SETTING_NAMES,
    SETTING_RANGES,
    SETTING_WORDS,
    SETTINGS,
    SHELVES,
    WIDTH_RANGE,
    EqPreset,
    balance_shares,
    load_astroeq,
)
from ..config import number, reason, whole
from .holds import Holds
from .style import (
    ASLEEP,
    DAEMON_WAIT,
    DeviceView,
    EntryTexts,
    confirm,
    picked_path,
    pointer_button,
    set_dot,
    show_battery,
    status_rows,
    toast_note,
)
from .tips import explain_choices
from .worker import (
    Call,
    DaemonWorker,
    LoadingGuard,
    Reply,
    SetStatus,
    daemon_down,
    unreachable,
)

#: The status page's titles while no headset has been seen (DeviceView).
LOOKING = "Looking for the headset…"
NO_HEADSET = "Headset not available"
#: The status row's title, by state.
HEADSET_ON = "Headset on"
HEADSET_OFF = "Headset off or out of range"
NOT_ANSWERING = "Not answering"

#: The banner's text for unsaved settings and during a save, which hides the button.
NOT_SAVED = "Not saved to the base station: {}"
SAVING = "Saving to the base station…"
SAVE_LABEL = "Save"
SAVED = "Saved to the base station."
REVERT_HEADING = "Revert to saved?"
REVERT_BODY = ("Unsaved changes go back to what the base station has saved. "
               "Auto shut-off stays as it is: the base station cannot report its saved value.")
#: A revert's toast when neither its note nor the note's first sentence
#: fits in one (short_note).
NOT_PUT_BACK = "Not every change was put back"
#: The Name row's refusals of what the daemon refuses too (astro.preset_name).
NAME_BLANK = "A preset name cannot be blank"
NAME_LONG = "A preset name is at most {} bytes, not {}: an accented letter takes 2 or more"
NAME_CONTROL = "A preset name cannot hold a control character, such as a tab or a line break"
IMPORT_LABEL = "Import from Astro Command Center…"
IMPORT_TITLE = "Import from Astro Command Center"

#: Each setting's row title: its name (SETTING_NAMES), but the stream port's rows
#: are titled without "Stream", under their group's title.
ROW_TITLES = {**SETTING_NAMES, "stream_game": "Game", "stream_chat": "Chat",
              "stream_mic": "Mic", "stream_aux": "Aux"}
#: The settings each group holds; a group with none of them in the reply hides.
MIC_SETTINGS = ("mic_level", "sidetone", "noise_gate", "mic_eq")
STREAM_SETTINGS = ("stream_game", "stream_chat", "stream_mic", "stream_aux")
BASE_SETTINGS = ("default_balance", "alert_volume", "auto_off")
#: The settings that are sliders, with their ranges.
SLIDER_RANGES = SETTING_RANGES
#: The combo rows: the values the socket takes, in the order the rows list them.
CHOICES = SETTING_CHOICES
CHOICE_LABELS = {**{name: tuple(word.title() for word in words)
                    for name, words in SETTING_WORDS.items()},
                 "auto_off": tuple(f"{m} minutes" for m in AUTO_OFF_MINUTES)}
#: Command Center's own descriptions of the noise gates, shortened, in
#: NOISE_GATES order.
NOISE_GATE_TIPS = (
    "The lightest gate, for a quiet room: the most natural voice, with the room in it.",
    "Very lightly gated, for quiet speech late at night.",
    "Astro's default: removes most light background noise.",
    "The most aggressive, for loud rooms. It gates the sidetone too.",
)
STREAM_HELP = "The mix the base station sends to its stream port."
EQ_HELP = "Changes go to the active preset."
MIX_HELP = "Set by the headset's Game and Voice buttons"

#: Every slider's width, fixed so the ends line up down the tab. The other sliders
#: leave room at the right for a band row's expander arrow: libadwaita 1.9 draws it
#: 16 px wide with a 3 px margin, after the row's 6 px spacing.
SLIDER_WIDTH = 280
ARROW_ROOM = 25
#: The balance sliders' ticks, 25, 50 and 75 % of 0-255, and how near one a
#: release of the Default balance must be to land on it: about 4 %.
BALANCE_TICKS = (64, 128, 191)
SNAP_STEPS = 10

#: How long a slider and a spin row wait after the last change before sending, so a
#: drag is one write.
SLIDER_SETTLE_MS = 300
SPIN_SETTLE_MS = 600
#: How often the page asks whether a save is done, and for how long.
SAVE_POLL_MS = 500
SAVE_WAIT = 10.0
#: An EQ write or a revert is many round trips to the base station.
SLOW_TIMEOUT = 10.0

def _int(value: object, low: int, high: int) -> int | None:
    """`value` if it is an int in low..high, else None."""
    return value if whole(value) and low <= value <= high else None


def balance_text(value: int) -> str:
    """A Game/Voice balance, 0 all Voice and 255 all Game, in percent."""
    voice, game = balance_shares(value)
    return f"Voice {voice}% · Game {game}%"


def slider_text(name: str, value: int) -> str:
    return balance_text(value) if name == "default_balance" else f"{value}%"


def snapped(value: int) -> int:
    """`value`, or the balance tick within SNAP_STEPS of it."""
    tick = min(BALANCE_TICKS, key=lambda t: abs(t - value))
    return tick if abs(tick - value) <= SNAP_STEPS else value


def unsaved_text(keys: list[str]) -> str:
    """The banner's list: `preset_2` is "Preset 2", a setting its name."""
    words = [f"Preset {key.removeprefix('preset_')}" if key.startswith("preset_")
             else SETTING_NAMES.get(key, key.replace("_", " ")) for key in keys]
    return NOT_SAVED.format(", ".join(words))


def versions_text(versions: object) -> str:
    """The status row's subtitle: the firmware versions the reply gives."""
    versions = versions if isinstance(versions, dict) else {}
    parts = [f"{what} {versions[key]}"
             for what, key in (("base station", "base"), ("headset", "headset"))
             if isinstance(versions.get(key), str)]
    return f"Firmware: {', '.join(parts)}" if parts else ""


def preset_label(number: int, name: str | None) -> str:
    return f"{number} · {name}" if name else f"Preset {number}"


def _scale(low: float, high: float, label: str, page: float = 10, origin: bool = True,
           spoken: Callable[[int], str] = "{}%".format) -> Gtk.Scale:
    """A slider; `origin` False for a balance or a gain, where a fill from
    the left end would read as a level. `spoken` is its value as a screen
    reader says it, the words its row shows: without it, a balance is a
    bare 0 to 255."""
    scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, draw_value=False, hexpand=False,
                      valign=Gtk.Align.CENTER, has_origin=origin,
                      adjustment=Gtk.Adjustment(lower=low, upper=high, value=low,
                                                step_increment=1, page_increment=page))
    scale.set_round_digits(0)
    scale.set_size_request(SLIDER_WIDTH, -1)
    scale.update_property([Gtk.AccessibleProperty.LABEL], [label])

    def say(scale: Gtk.Scale) -> None:
        scale.update_property([Gtk.AccessibleProperty.VALUE_TEXT],
                              [spoken(round(scale.get_value()))])
    scale.connect("value-changed", say)
    say(scale)
    return scale


def _balance_scale(label: str) -> Gtk.Scale:
    """A Game/Voice balance's slider, with its ticks."""
    scale = _scale(*BALANCE_RANGE, label, origin=False, spoken=balance_text)
    for tick in BALANCE_TICKS:
        scale.add_mark(tick, Gtk.PositionType.BOTTOM, None)
    return scale


def _between(scale: Gtk.Scale) -> Gtk.Box:
    """A balance slider with Voice and Game dimmed under its ends: beside
    it, Game would push it out of line with the other sliders."""
    words = Gtk.CenterBox(
        start_widget=Gtk.Label(label="Voice", css_classes=["dimmed", "caption"]),
        end_widget=Gtk.Label(label="Game", css_classes=["dimmed", "caption"]))
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
    box.append(scale)
    box.append(words)
    return box


def _in_line(widget: Gtk.Widget) -> Gtk.Widget:
    """A slider row's suffix, clear of where a band's expander arrow sits."""
    widget.set_margin_end(ARROW_ROOM)
    return widget


class HeadsetPage:
    """The Headset tab: status, microphone, equalizer, stream port and base
    station, with Save and Revert for what differs from flash.

    `call` and `refused` are the window's. Every write goes through `call` and answers
    with the whole `headset` reply, which sets the rows. `extra` goes under Revert.
    """

    #: The last reply said the base station answers, and a save is running.
    answering = True
    saving = False

    def __init__(self, parent: Gtk.Window, worker: DaemonWorker, call: Call,
                 toast: Callable[..., object],
                 set_status: SetStatus,
                 refused: Callable[[dict], None],
                 extra: Gtk.Widget | None = None) -> None:
        self._parent = parent
        self._worker = worker
        self._call = call
        self._toast = toast
        self._refused = refused
        self._set_status = set_status

        self._loading = LoadingGuard()
        self._pending = False
        #: The last failure shown, so a missing base does not toast every poll and the
        #: problem banner stays down meanwhile, and the `headset_*` problem the last status
        #: carried (show_problem).
        self._error: str | None = None
        self._problem: str | None = None
        #: A `read: True` was answered by a responsive base station with no save
        #: running, so the daemon ran it. The tab asks for one when first shown and
        #: after the base or the daemon was lost.
        self.read_done = False
        #: Each setting's active value in the last reply, and the active preset.
        self._active: dict[str, object] = {}
        self._preset: int | None = None
        self._presets: dict[int, dict] = {}
        #: What the preset dropdown lists: the preset numbers and their labels.
        self._preset_ids: list[int] = []
        self._preset_labels: list[str] = []
        self._holds = Holds()
        self._shown = EntryTexts()
        #: headset_save is out and unanswered: a second click sends nothing.
        self._save_sent = False
        #: A save was accepted and its end not yet seen (_check_save).
        self._save_wait = False
        self._save_deadline = 0.0
        self._save_poll: int | None = None
        #: headset_save's round trip raised: the daemon may still have run it, which the
        #: next reply says (_check_save).
        self._save_unsure = False
        self.widget = self._build(extra)

    # -- construction -----------------------------------------------------

    def _build(self, extra: Gtk.Widget | None) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                       margin_top=18, margin_bottom=18, margin_start=18, margin_end=18)
        self.sliders: dict[str, Gtk.Scale] = {}
        self.setting_rows: dict[str, Adw.ActionRow] = {}
        page.append(self._status_group())
        page.append(self._mic_group())
        page.append(self._eq_group())
        page.append(self._stream_group())
        page.append(self._base_group())
        page.append(self._revert_group())
        if extra is not None:
            page.append(extra)

        # Top bars, so they show whatever the tab is scrolled to. Not markup: the problem
        # is the daemon's words, and the unsaved list can carry them (unsaved_text).
        self.problem_banner = Adw.Banner(use_markup=False)
        self.save_banner = Adw.Banner(use_markup=False)
        self.save_banner.connect("button-clicked", self.on_save)
        self.view = DeviceView(page, "audio-headset-symbolic", LOOKING)
        toolbar = Adw.ToolbarView(content=self.view.stack)
        toolbar.add_top_bar(self.problem_banner)
        toolbar.add_top_bar(self.save_banner)
        return toolbar

    def _status_group(self) -> Adw.PreferencesGroup:
        """The status group: the headset's state, its battery and its live balance."""
        # Titled from the reply: its name.
        status = self.status_group = Adw.PreferencesGroup(title="Headset")
        self.state_row, self.state_dot, self.battery_row, self.battery_icon = status_rows(
            status, NOT_ANSWERING)
        # Read only, set by the headset's buttons; grayed out so it does not look draggable.
        self.mix_scale = _balance_scale("Game and voice balance")
        self.mix_scale.update_property([Gtk.AccessibleProperty.READ_ONLY], [True])
        mix = _between(self.mix_scale)
        mix.set_sensitive(False)
        self.mix_row = Adw.ActionRow(title="Balance", use_markup=False, visible=False)
        self.mix_row.add_suffix(_in_line(mix))
        status.add(self.mix_row)
        return status

    def _mic_group(self) -> Adw.PreferencesGroup:
        """The Microphone group: two sliders, the noise gate and the mic EQ."""
        mic = self.mic_group = Adw.PreferencesGroup(title="Microphone")
        mic.add(self._slider_row("mic_level"))
        mic.add(self._slider_row("sidetone"))
        mic.add(self._combo_row("noise_gate", NOISE_GATE_TIPS))
        mic.add(self._combo_row("mic_eq"))
        return mic

    def _eq_group(self) -> Adw.PreferencesGroup:
        """The Equalizer group: the preset and its name, a row per band, and the import."""
        eq = self.eq_group = Adw.PreferencesGroup(title="Equalizer", description=EQ_HELP)
        self.preset_row = Adw.ComboRow(title="Preset", model=Gtk.StringList.new([]))
        self.preset_row.connect("notify::selected", self.on_preset)
        eq.add(self.preset_row)
        self.name_row = Adw.EntryRow(title="Name", show_apply_button=True)
        self.name_row.connect("apply", self.on_name)
        eq.add(self.name_row)
        self.band_rows: list[Adw.ExpanderRow] = []
        self.gain_scales: list[Gtk.Scale] = []
        self.freq_rows: list[Adw.SpinRow] = []
        self.width_rows: dict[int, Adw.SpinRow] = {}
        for band in BANDS:
            row = Adw.ExpanderRow(title=f"Band {band}")
            gain = _scale(*GAIN_RANGE, f"Band {band} gain", page=1, origin=False,
                          spoken="{:+d} dB".format)
            gain.add_mark(0, Gtk.PositionType.BOTTOM, None)
            gain.connect("value-changed", self.on_gain, band)
            row.add_suffix(gain)
            freq = Adw.SpinRow(title="Frequency", subtitle="Hz", adjustment=Gtk.Adjustment(
                lower=FREQ_RANGE[0], upper=FREQ_RANGE[1], value=1000,
                step_increment=10, page_increment=100))
            freq.connect("notify::value", self.on_band, band)
            row.add_row(freq)
            if band not in SHELVES:
                width = Adw.SpinRow(title="Width", digits=1, adjustment=Gtk.Adjustment(
                    lower=WIDTH_RANGE[0], upper=WIDTH_RANGE[1], value=1.0,
                    step_increment=0.1, page_increment=0.5))
                width.connect("notify::value", self.on_band, band)
                row.add_row(width)
                self.width_rows[band] = width
            self.band_rows.append(row)
            self.gain_scales.append(gain)
            self.freq_rows.append(freq)
            eq.add(row)
        self.import_row = Adw.ButtonRow(title=IMPORT_LABEL,
                                        start_icon_name="document-open-symbolic")
        self.import_row.connect("activated", self.on_import)
        eq.add(self.import_row)
        return eq

    def _stream_group(self) -> Adw.PreferencesGroup:
        """The Stream port group: a slider per STREAM_SETTINGS name."""
        stream = self.stream_group = Adw.PreferencesGroup(title="Stream port",
                                                          description=STREAM_HELP)
        for name in STREAM_SETTINGS:
            stream.add(self._slider_row(name))
        return stream

    def _base_group(self) -> Adw.PreferencesGroup:
        """The Base station group: the default balance, the alert volume and auto shut-off."""
        base = self.base_group = Adw.PreferencesGroup(title="Base station")
        base.add(self._slider_row("default_balance"))
        pointer = Gtk.EventControllerLegacy(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        pointer.connect("event", self._on_balance_pointer)
        self.sliders["default_balance"].add_controller(pointer)
        base.add(self._slider_row("alert_volume"))
        base.add(self._combo_row("auto_off"))
        return base

    def _revert_group(self) -> Adw.PreferencesGroup:
        """The group that holds Revert to saved."""
        self.revert_group = Adw.PreferencesGroup()
        self.revert_row = Adw.ButtonRow(title="Revert to saved",
                                        start_icon_name="edit-undo-symbolic")
        self.revert_row.connect("activated", self.on_revert)
        self.revert_group.add(self.revert_row)
        return self.revert_group

    def _slider_row(self, name: str) -> Adw.ActionRow:
        """A setting's slider."""
        balance = name == "default_balance"
        row = Adw.ActionRow(title=ROW_TITLES[name], use_markup=False, visible=False)
        scale = (_balance_scale(SETTING_NAMES[name]) if balance
                 else _scale(*SLIDER_RANGES[name], SETTING_NAMES[name]))
        scale.connect("value-changed", self.on_slider, name)
        row.add_suffix(_in_line(_between(scale) if balance else scale))
        self.sliders[name] = scale
        self.setting_rows[name] = row
        return row

    def _combo_row(self, name: str, tips: tuple[str, ...] = ()) -> Adw.ComboRow:
        row = Adw.ComboRow(title=ROW_TITLES[name], visible=False,
                           model=Gtk.StringList.new(CHOICE_LABELS[name]))
        if tips:
            explain_choices(row, tips)
        row.connect("notify::selected", self.on_choice, name)
        self.setting_rows[name] = row
        return row

    # -- reading ----------------------------------------------------------

    def tick(self, dragging: bool, ask: bool = True) -> None:
        """The five second poll, asking as poll() does."""
        if not dragging and not self._pending:
            self.poll(ask)

    def poll(self, ask: bool = True) -> None:
        """Ask for the headset as its tab needs it: every setting until the base station has
        answered a full read, and again after it or the daemon was lost (read_done); its
        state after that. `ask` False, and while a save runs, the daemon answers from what it
        holds."""
        asking = ask and not self.saving
        self.refresh(ask=asking, read=asking and not self.read_done)

    def refresh(self, force: bool = False, ask: bool = True, read: bool = False) -> None:
        """Ask for the headset. `read` has the daemon read every setting again
        first; `force` puts the name back even with the cursor in it."""
        self._pending = True
        request: dict = {"command": "headset"}
        if read:
            request["read"] = True
        elif not ask:
            request["ask"] = False
        self._worker.call(request, lambda info: self._on_info(info, force, read))

    def daemon_gone(self, error: Exception) -> None:
        """A read failed, the window's or the tab's: the tab says it is the daemon."""
        if daemon_down(error):
            self._save_lost()
        self._failed(unreachable(error), DAEMON_WAIT)

    def show_problem(self, problem: dict | None) -> None:
        """Show a `headset_*` problem from the daemon's status in the banner, or hide it.
        Until a headset is seen the status page shows it instead, and while the reply says
        there is none the status row does: the banner would repeat it."""
        message = str((problem or {}).get("message") or "")
        self._problem = message[:1].upper() + message[1:] if message else None
        if self._problem:
            self.problem_banner.set_title(self._problem)
            if not self.view.seen:
                self.view.missing(NO_HEADSET, self._problem)
        self.problem_banner.set_revealed(
            bool(self._problem) and self.view.seen and self._error is None)

    def _failed(self, detail: str, title: str = NO_HEADSET) -> None:
        """No headset to show: said on the status page until one has been
        seen, and after that on the status row and in one toast."""
        self.read_done = False
        set_dot(self.state_dot, False)
        self.state_row.set_title(detail[:1].upper() + detail[1:])
        self.state_row.set_subtitle("")
        # The node, the battery reading and the live balance were the gone base's, as the
        # Mouse and Keyboard tabs have it.
        self.state_row.set_tooltip_text(None)
        show_battery(self.battery_row, self.battery_icon, None)
        self.mix_row.set_visible(False)
        self._set_editable(False)
        self.revert_row.set_sensitive(False)
        self.save_banner.set_sensitive(False)
        if not self.view.seen:
            self.view.missing(title, self._problem if title == NO_HEADSET and self._problem
                              else detail)
            return
        # The row says why there is no headset, in the problem's own sentence when the
        # daemon refused the reply: the banner would repeat it.
        self.problem_banner.set_revealed(False)
        if detail != self._error:
            self._error = detail
            self._toast(f"Headset: {detail}")

    def _set_editable(self, editable: bool) -> None:
        for row in (*self.setting_rows.values(), self.preset_row, self.name_row,
                    *self.band_rows, self.import_row):
            row.set_sensitive(editable)
        if not editable:
            # A row grayed out under the pointer never sees its release.
            self._holds.release_all()

    def _on_info(self, info: Reply, force: bool = False, read: bool = False) -> None:
        self._pending = False
        if isinstance(info, Exception):
            self._set_status(False, info, poll=True)
            self.daemon_gone(info)
            return
        if not info.get("ok"):
            self._save_lost()                   # no base: the daemon dropped the save with it
            self.save_banner.set_revealed(False)    # and it is back with its flash values
            self._failed(str(info.get("error") or "not connected"))
            return
        self.view.found()
        self.problem_banner.set_revealed(self._problem is not None)
        self._error = None
        self.answering = info.get("answering") is not False
        self.saving = info.get("saving") is True
        # The daemon skips a read while the base is silent or a save runs,
        # and a timeout cuts one short, which leaves the base silent too.
        self.read_done = self.read_done or (read and self.answering and not self.saving)
        self._set_editable(self.answering and not self.saving)
        self.save_banner.set_sensitive(self.answering)
        self.show_state(info)
        self.show_settings(info.get("settings"))
        self.show_presets(info, force)
        self.show_unsaved(info.get("unsaved"))
        self._check_save(info)

    def show_state(self, info: dict) -> None:
        """The status group: its title, the dot, battery and the live balance."""
        self.status_group.set_title(GLib.markup_escape_text(str(info.get("name") or "Headset")))
        linked = info.get("linked") is True
        state: bool | str
        if not self.answering:
            title, state = NOT_ANSWERING, ASLEEP
        elif linked:
            title, state = HEADSET_ON, True
        else:
            title, state = HEADSET_OFF, ASLEEP
        set_dot(self.state_dot, state)
        # set_dot's ASLEEP label, asleep or switched off, is wrong for a silent base station.
        self.state_dot.update_property([Gtk.AccessibleProperty.LABEL], [title])
        self.state_row.set_title(title)
        self.state_row.set_subtitle(versions_text(info.get("versions")))
        device = info.get("device")
        self.state_row.set_tooltip_text(str(device) if device else None)
        battery = info.get("battery") if isinstance(info.get("battery"), dict) else None
        # A headset away from the base says when its reading was taken.
        at = info.get("battery_at") if not (self.answering and linked) else None
        show_battery(self.battery_row, self.battery_icon, battery,
                     at if isinstance(at, int | float) else None)
        balance = _int(info.get("balance"), *BALANCE_RANGE)
        self.mix_row.set_visible(balance is not None)
        if balance is not None:
            self.mix_scale.set_value(balance)
            self.mix_row.set_subtitle(f"{balance_text(balance)}\n{MIX_HELP}")

    def show_settings(self, settings: object) -> None:
        """Each setting's row on its active value; a setting the reply lacks,
        or gives in a shape it should not, hides its row."""
        settings = settings if isinstance(settings, dict) else {}
        self._active = {}
        for name in SETTINGS:
            value = self._active_of(settings.get(name), object)
            if name in SLIDER_RANGES:
                value = _int(value, *SLIDER_RANGES[name])
            elif value not in CHOICES[name]:
                value = None
            self.setting_rows[name].set_visible(value is not None)
            if value is None:
                continue
            self._active[name] = value
            if self._holds.held(name):
                continue
            # _build gives a slider to each of SLIDER_RANGES, whose value _int passed above,
            # and a combo row (_combo_row) to every other setting.
            if name in self.sliders:
                self._show_slider(name, cast(int, value))
            else:
                self._loading.put_selected(cast(Adw.ComboRow, self.setting_rows[name]),
                                   CHOICES[name].index(value))
        for group, names in ((self.mic_group, MIC_SETTINGS), (self.stream_group, STREAM_SETTINGS),
                             (self.base_group, BASE_SETTINGS)):
            group.set_visible(any(name in self._active for name in names))

    def show_presets(self, info: dict, force: bool) -> None:
        """The preset dropdown, and the active preset's name, gains and bands."""
        if self._holds.held("preset"):
            return                              # a reply to a read sent before the switch
        presets = {p["preset"]: p for p in info.get("presets") or ()
                   if isinstance(p, dict) and p.get("preset") in PRESETS}
        active = info.get("preset") if info.get("preset") in presets else None
        switched = active != self._preset
        if switched:
            # An edit still settling was made on the preset shown: it goes there.
            self._flush_eq()
        self._presets, self._preset = presets, active
        self.eq_group.set_visible(active is not None)
        if active is None:
            return
        ids = sorted(presets)
        labels = [preset_label(n, self._active_of(presets[n].get("name"), str)) for n in ids]
        if (ids, labels) != (self._preset_ids, self._preset_labels):
            self._preset_ids, self._preset_labels = ids, labels
            self._loading.put_model(self.preset_row, Gtk.StringList.new(labels))
        self._loading.put_selected(self.preset_row, ids.index(active))
        preset = presets[active]
        name = self._active_of(preset.get("name"), str)
        self.name_row.set_visible(name is not None)
        # A name being typed was for the preset shown: a switch drops it.
        if name is not None and not self._holds.held("name") and (
                force or switched or not self._shown.typing(self._parent, self.name_row)):
            # No loading guard: set_text never emits `apply`, the row's only handler (on_name).
            self._shown.show(self.name_row, name)
        gains = self._active_of(preset.get("gains"), list)
        if not (isinstance(gains, list) and len(gains) == len(BANDS)
                and all(_int(g, *GAIN_RANGE) is not None for g in gains)):
            gains = None
        bands = {b.get("band"): values for b in preset.get("bands") or ()
                 if isinstance(b, dict) and isinstance(values := b.get("active"), dict)}
        for index, band in enumerate(BANDS):
            self._show_band(index, band, None if gains is None else gains[index],
                            bands.get(band, {}))

    def _show_band(self, index: int, band: int, gain: int | None, found: dict) -> None:
        """One band's row: shown with its gain, and its frequency and width
        when the reply has them."""
        self.band_rows[index].set_visible(gain is not None)
        freq = _int(found.get("freq"), *FREQ_RANGE)
        width = found.get("width")
        shown = freq is not None and (band in SHELVES or (
            number(width) and WIDTH_RANGE[0] <= width <= WIDTH_RANGE[1]))
        self.freq_rows[index].set_visible(shown)
        if band in self.width_rows:
            self.width_rows[band].set_visible(shown)
        if gain is not None and not self._holds.held(f"gains:{self._preset}"):
            self._loading.put_value(self.gain_scales[index], gain)
        # `shown` says the frequency is there: asking again only tells the type checker so.
        if shown and freq is not None and not self._holds.held(f"band_{band}:{self._preset}"):
            self._loading.put_value(self.freq_rows[index], freq)
            if band in self.width_rows:
                # A band with a width row is no shelf (_build), so `shown` saw a number.
                self._loading.put_value(self.width_rows[band], round(cast(float, width), 1))
        self._band_subtitle(band)

    def show_unsaved(self, unsaved: object) -> None:
        """The banner: what differs from flash with Save, or the save running."""
        keys = [k for k in unsaved if isinstance(k, str)] if isinstance(unsaved, list) else []
        if self.saving:
            self.save_banner.set_title(SAVING)
            self.save_banner.set_button_label("")
        elif keys:
            self.save_banner.set_title(unsaved_text(keys))
            self.save_banner.set_button_label(SAVE_LABEL)
        self.save_banner.set_revealed(self.saving or bool(keys))
        self.revert_row.set_sensitive(self.answering and not self.saving and bool(keys))

    @staticmethod
    def _active_of[T](pair: object, kind: type[T]) -> T | None:
        """The `active` half of an {"active", "saved"} pair, if it is a `kind`."""
        value = pair.get("active") if isinstance(pair, dict) else None
        return value if isinstance(value, kind) else None

    def _show_slider(self, name: str, value: int) -> None:
        self._loading.put_value(self.sliders[name], value)
        self.setting_rows[name].set_subtitle(slider_text(name, value))

    def _band_subtitle(self, band: int) -> None:
        index = BANDS.index(band)
        gain = f"{round(self.gain_scales[index].get_value()):+d} dB"
        freq = self.freq_rows[index]
        self.band_rows[index].set_subtitle(
            f"{round(freq.get_value())} Hz, {gain}" if freq.get_visible() else gain)

    # -- writes -----------------------------------------------------------

    def _write(self, key: str, request: dict, timeout: float = 3.0,
               then: Callable[[dict], None] | None = None) -> None:
        """Send a write through the window's `call` and take the `headset` reply it
        answers with. A refusal, which `call` says, reads the headset again, which puts
        the rows back."""
        self._holds.sent(key)
        # Only a write that changes the name puts it back over one being typed.
        force = key in ("name", "revert", "import")

        def done(reply: dict | None) -> None:
            self._holds.answered(key)
            if reply is None:
                self.refresh(force=force)
                return
            self._on_info(reply, force=force)
            if then is not None:
                then(reply)

        def unanswered(_error: Exception) -> None:
            # `call` set the status; the next poll reads the headset.
            self._holds.answered(key)
        self._call(request, done, unanswered, timeout=timeout)

    def _flush_eq(self) -> None:
        self._holds.flush(("gains:", "band_"))

    def flush(self) -> bool:
        """Send what is still settling now, for the window's close or a save; True if any."""
        return self._holds.flush()

    def close(self) -> None:
        self._holds.cancel_all()
        self._stop_save_wait()

    def on_slider(self, scale: Gtk.Scale, name: str) -> None:
        value = round(scale.get_value())
        self.setting_rows[name].set_subtitle(slider_text(name, value))
        if self._loading.active or self._holds.pressing(name):
            return
        self._send_later(scale, name)

    def _send_later(self, scale: Gtk.Scale, name: str) -> None:
        self._holds.settle(name, lambda: self._send_setting(name, round(scale.get_value())),
                           SLIDER_SETTLE_MS)

    def _on_balance_pointer(self, controller: Gtk.EventControllerLegacy,
                            _event: object) -> bool:
        if (change := pointer_button(controller)) is not None:
            # A canceled touch counts as the release, or the slider stays held and never sends.
            (self.on_press if change.pressed else self.on_release)("default_balance")
        return False

    def on_press(self, name: str) -> None:
        """The pointer went down on a slider that snaps: nothing is sent
        until it lets go."""
        self._holds.press(name)

    def on_release(self, name: str) -> None:
        """The pointer let go: the slider snaps to a tick near it, and sends
        once it settles. Sent whatever the loading guard says: a release is
        one event, and a poll's rows can hold the guard when it comes."""
        if not self._holds.release(name):
            return
        scale = self.sliders[name]
        value = snapped(round(scale.get_value()))
        scale.set_value(value)
        self.setting_rows[name].set_subtitle(slider_text(name, value))
        self._send_later(scale, name)

    def on_choice(self, row: Adw.ComboRow, _p: GObject.ParamSpec, name: str) -> None:
        if self._loading.active:
            return
        index = row.get_selected()
        if 0 <= index < len(CHOICES[name]):
            self._send_setting(name, CHOICES[name][index])

    def _send_setting(self, name: str, value: int | str) -> None:
        """Write one setting, unless it is unchanged, or the daemon never gave it."""
        if name not in self._active or self._holds.unchanged(name, value == self._active[name]):
            return
        self._write(name, {"command": "headset_set", "setting": name, "value": value})

    def on_preset(self, row: Adw.ComboRow, _p: GObject.ParamSpec) -> None:
        if self._loading.active:
            return
        index = row.get_selected()
        if not 0 <= index < len(self._preset_ids) or self._holds.unchanged(
                "preset", self._preset_ids[index] == self._preset):
            return
        self._flush_eq()
        self._write("preset", {"command": "headset_set", "setting": "preset",
                               "value": self._preset_ids[index]})

    def on_name(self, row: Adw.EntryRow) -> None:
        """No loading guard: apply comes only from Enter or the apply button,
        never from set_text, so a guard could only drop a real one."""
        if self._preset is None:
            return
        name = row.get_text().strip()
        size = len(name.encode())
        # NAME_BYTES starts at 1, so the only name too short is the empty one.
        if not name:
            self._toast(NAME_BLANK)
            return
        if size > NAME_BYTES[1]:
            self._toast(NAME_LONG.format(NAME_BYTES[1], size))
            return
        if any(unicodedata.category(c) == "Cc" for c in name):
            self._toast(NAME_CONTROL)
            return
        self._shown.took(row)
        self._write("name", {"command": "headset_eq", "preset": self._preset, "name": name},
                    SLOW_TIMEOUT)

    def on_gain(self, _scale: Gtk.Scale, band: int) -> None:
        self._band_subtitle(band)
        preset = self._preset
        if self._loading.active or preset is None:
            return
        self._holds.settle(f"gains:{preset}", lambda: self._send_gains(preset), SLIDER_SETTLE_MS)

    def _send_gains(self, preset: int) -> None:
        key = f"gains:{preset}"
        gains = [round(scale.get_value()) for scale in self.gain_scales]
        shown = self._active_of(self._presets.get(preset, {}).get("gains"), list)
        if self._holds.unchanged(key, gains == shown):
            return
        self._write(key, {"command": "headset_eq", "preset": preset, "gains": gains},
                    SLOW_TIMEOUT)

    def on_band(self, _row: Adw.SpinRow, _p: GObject.ParamSpec, band: int) -> None:
        self._band_subtitle(band)
        preset = self._preset
        if self._loading.active or preset is None:
            return
        self._holds.settle(f"band_{band}:{preset}", lambda: self._send_band(preset, band),
                           SPIN_SETTLE_MS)

    def _send_band(self, preset: int, band: int) -> None:
        key = f"band_{band}:{preset}"
        freq = round(self.freq_rows[BANDS.index(band)].get_value())
        width = round(self.width_rows[band].get_value(), 1) if band in self.width_rows else None
        held = next((b.get("active") for b in self._presets.get(preset, {}).get("bands") or ()
                     if isinstance(b, dict) and b.get("band") == band), None)
        if self._holds.unchanged(key, held == {"freq": freq, "width": width}):
            return
        self._write(key, {"command": "headset_eq", "preset": preset,
                          "bands": [{"band": band, "freq": freq, "width": width}]},
                    SLOW_TIMEOUT)

    # -- import -----------------------------------------------------------

    def on_import(self, _row: Adw.ButtonRow) -> None:
        if self._preset is None:
            return
        dialog = Gtk.FileDialog(title=IMPORT_TITLE, modal=True)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        presets = Gtk.FileFilter(name="Command Center presets")
        presets.add_pattern("*.astroeq")
        filters.append(presets)
        dialog.set_filters(filters)
        # The preset the dialog was opened for, whatever is active when it closes.
        dialog.open(self._parent, None,
                    lambda d, result, p=self._preset: self._picked(d, result, p))

    def _picked(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult, preset: int) -> None:
        if (path := picked_path(dialog, result, self._toast)) is not None:
            self.import_path(path, preset)

    def import_path(self, path: Path, preset: int) -> None:
        """Parse an .astroeq file on the worker and write it to `preset`."""
        def parsed(result: EqPreset | Exception) -> None:
            if isinstance(result, Exception):
                # As the G HUB and pack imports do: one over TOAST_MAX opens a dialog.
                self._refused({"error": f"{path.name} could not be imported: {reason(result)}"})
                return
            # The daemon reads the preset back before it answers.
            self._write("import", {**result.request(), "preset": preset}, SLOW_TIMEOUT)
        self._worker.run(lambda: load_astroeq(path), parsed)

    # -- save and revert --------------------------------------------------

    def on_save(self, _banner: Adw.Banner) -> None:
        """Save: what still settles goes first, then `headset_save`, then the
        page asks every SAVE_POLL_MS until the save is over or SAVE_WAIT has passed."""
        if self._save_sent or self._save_wait or self.saving:
            return
        self.flush()
        self._save_sent = True

        def done(reply: dict | None) -> None:
            self._save_sent = False
            if reply is None:
                return
            self._save_unsure = False
            self._wait_for_save()
            self._on_info(reply)

        def unanswered(_error: Exception) -> None:
            # The daemon still reads a request whose client gave up, and answers this
            # read after it: the reply says whether the save runs.
            self._save_sent = False
            self._save_unsure = True
            self.refresh(ask=False)
        self._call({"command": "headset_save"}, done, unanswered)

    def _wait_for_save(self) -> None:
        self._save_wait = True
        self._save_deadline = time.monotonic() + SAVE_WAIT
        if self._save_poll is None:
            self._save_poll = GLib.timeout_add(SAVE_POLL_MS, self._poll_save)

    def _poll_save(self) -> bool:
        if time.monotonic() >= self._save_deadline:
            # Past the wait the five second poll sees the end.
            self._save_poll = None
            return False
        if not self._pending:
            self.refresh(ask=False)
        return True

    def _check_save(self, info: dict) -> None:
        """Once a reply says the save accepted is over: say how it went, and
        read every setting again unless the save worked and was read back."""
        if self._save_unsure:
            self._save_unsure = False
            if self.saving:
                self._wait_for_save()
            return
        if not self._save_wait or self.saving:
            return
        self._stop_save_wait()
        error = info.get("save_error")
        self._toast(str(error) if error else SAVED)
        # A save that worked was read back before this reply, which then lists nothing
        # unsaved; one that failed, or whose read-back was cut short, owes the read.
        if error or info.get("unsaved"):
            self.refresh(read=True)

    def _stop_save_wait(self) -> None:
        self._save_wait = False
        if self._save_poll is not None:
            GLib.source_remove(self._save_poll)
            self._save_poll = None

    def _save_lost(self) -> None:
        """The base or the daemon went, and a running save with it. Its end is unknown,
        so nothing is said; the unsaved list says the rest once the base is back."""
        self._stop_save_wait()
        self._save_unsure = False
        if self.saving:
            self.saving = False
            self.save_banner.set_revealed(False)

    def on_revert(self, _row: Adw.ButtonRow) -> None:
        self._ask(REVERT_HEADING, REVERT_BODY, "Revert", self._revert)

    def _revert(self) -> None:
        """What still settles is dropped: it is what Revert takes back."""
        self._holds.cancel_all()

        def done(reply: dict) -> None:
            # The daemon read every setting again before it answered.
            if reply.get("note"):
                toast_note(self._toast, reply["note"], NOT_PUT_BACK)
        self._write("revert", {"command": "headset_revert"}, SLOW_TIMEOUT, then=done)

    def _ask(self, heading: str, body: str, response: str, then: Callable[[], None]) -> None:
        confirm(self._parent, heading, body, response, then)
