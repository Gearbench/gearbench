"""The headset tab: the Astro A50's base station settings, and Load preset to headset, the
dialog over the presets Gearbench keeps beside them (presets_dialog).

A change goes to the base station's RAM at once and to its flash only on Save,
so a banner lists what differs from flash. The tab polls the daemon on its own
timer, with its own re-entry guard, as the Mouse tab does. Game output and Chat output
follow Balance, their volume as GNOME's Sound settings set it (volume_rows), and the
Microphone group ends with Mic input, the microphone's volume set the same way, and Input
level, its level live (input_meter_row).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict, replace
from functools import partial
from pathlib import Path
from typing import cast

from gi.repository import Adw, Gio, GLib, GObject, Gtk

from ..a50_library import a50_preset, default_slot
from ..astro import (
    AUTO_OFF_MINUTES,
    BALANCE_RANGE,
    BANDS,
    BASE_SILENT,
    DOCKED_OFF,
    FREQ_RANGE,
    GAIN_RANGE,
    NOISE_GATES,
    OFF_OR_AWAY,
    PRESETS,
    SETTING_CHOICES,
    SETTING_NAMES,
    SETTING_RANGES,
    SETTING_WORDS,
    SETTINGS,
    SHELVES,
    SLOW_REQUEST_WAIT,
    WIDTH_RANGE,
    Band,
    EqPreset,
    game_share,
)
from ..astroeq import load_astroeq
from ..battery import kept_at
from ..spatial import A50
from ..system_volume import MIC
from ..wording import capitalized, hertz, number, printable, reason, shares_words, signed, whole
from .holds import Holds
from .input_meter_row import InputMeterRow
from .presets_dialog import SAVE_BODY, TITLE, PresetsDialog
from .style import (
    ASLEEP,
    ASTROEQ_IMPORT,
    ASTROEQ_NOT_IMPORTED,
    ASTROEQ_REFUSED,
    ASTROEQ_TITLE,
    BALANCE,
    DAEMON_WAIT,
    FIRMWARE,
    HEADSET_TAB,
    LOOKING,
    NOT_FOUND,
    NOT_READ,
    PRESET,
    PRESET_RESET,
    REVERT_TO_SAVED,
    SAVE_HEADING,
    SIDETONE,
    STREAM_MIX,
    DeviceView,
    EntryTexts,
    ask_name,
    astroeq_dialog,
    confirm,
    displayable,
    name_refusal,
    offer_save_new,
    percent,
    picked_path,
    plain_row,
    preset_label,
    save_new_row,
    set_dot,
    show_battery,
    spoken_scale,
    status_rows,
    through,
    toast_note,
    watch_name,
    watch_pointer,
)
from .tips import NOISE_GATE_TIPS, explain_choices
from .volume_rows import OUTPUT_WORDS, VolumeRows
from .worker import (
    REPLY_WAIT,
    Call,
    DaemonWorker,
    LoadingGuard,
    Reply,
    SetStatus,
    daemon_down,
    take_typed,
    unreachable,
)

#: The status page's title over the reason while no headset has been seen (DeviceView).
NO_HEADSET = "Headset not available"
#: The Status row's subtitle, by state: the route while the headset is linked, the
#: headset off its link, docked or not, and the base station silent.
LINKED = through("base station")
HEADSET_OFF = capitalized(OFF_OR_AWAY)
HEADSET_DOCKED = capitalized(DOCKED_OFF)
NOT_ANSWERING = capitalized(BASE_SILENT)

#: The banner's text for unsaved settings and during a save, which hides the button.
NOT_SAVED = "Not saved to the base station: {}"
SAVING = "Saving to the base station…"
SAVE_LABEL = "Save"
SAVED = "Saved to the base station"
REVERT_HEADING = "Revert to saved?"
REVERT_BODY = "Auto shut-off stays as it is: the base station cannot report its saved value."
#: A revert's toast when neither its note nor the note's first sentence
#: fits in one (short_note).
NOT_REVERTED = "Not every change was reverted"
#: Each setting's slider's name for a screen reader: its name (SETTING_NAMES), but the
#: sidetone's is every headset page's row title, which says what it is (the owner's
#: decision of 2026-10-17); the banner and the refusals keep Sidetone.
SLIDER_NAMES = {**SETTING_NAMES, "sidetone": SIDETONE}
#: Each setting's row title: its slider's name, but the stream mix's rows are titled
#: without "Stream", under their group's title.
ROW_TITLES = {**SLIDER_NAMES, "stream_game": "Game", "stream_chat": "Voice",
              "stream_mic": "Mic", "stream_aux": "Aux"}
#: The settings each group holds; a group with none of them in the reply hides.
MIC_SETTINGS = ("mic_level", "sidetone", "noise_gate", "mic_eq")
STREAM_SETTINGS = ("stream_game", "stream_chat", "stream_mic", "stream_aux")
BASE_SETTINGS = ("default_balance", "alert_volume", "auto_off")
#: The combo rows' labels, in SETTING_CHOICES's order: a row's index is its value's there.
CHOICE_LABELS = {**{name: tuple(word.title() for word in words)
                    for name, words in SETTING_WORDS.items()},
                 "auto_off": tuple(f"{m} minutes" for m in AUTO_OFF_MINUTES)}
STREAM_HELP = "What the base station's stream port sends"
#: The tooltip of the row that saves the active preset into the library, and the row that
#: opens the library's dialog to load one into a slot.
SAVE_NEW_HELP = "Keeps the active preset's sound on this computer"
LOAD_PRESET = f"{TITLE}…"
LOAD_PRESET_HELP = "Puts a preset of the library into slot 1, 2 or 3"
#: The Reset to default row's tooltip, with the name of the active slot's default: what
#: comes back, as the G432's tooltip says it. Put back is the first-read values' word.
RESET_HELP = "{} back in this slot; Save keeps it"
MIX_HELP = "Set by the headset's Game and Voice buttons"

#: The room a slider other than a band's leaves at its right, where a band row draws its
#: expander arrow, so the ends of the style.SLIDER_WIDTH sliders line up down the tab:
#: libadwaita 1.9 draws the arrow 16 px wide with a 3 px margin, after the row's 6 px
#: spacing.
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
#: The start of each equalizer edit's Holds key. gains_key and band_key add its preset, so a
#: write to a preset the headset's EQ button switched away from holds none of the new
#: preset's rows.
GAINS_KEY, BAND_KEY = "gains:", "band_"


def gains_key(preset: int) -> str:
    return f"{GAINS_KEY}{preset}"


def band_key(band: int, preset: int) -> str:
    return f"{BAND_KEY}{band}:{preset}"


def _int(value: object, low: int, high: int) -> int | None:
    """`value` if it is an int in low..high, else None."""
    return value if whole(value) and low <= value <= high else None


def _active_bands(preset: dict) -> dict[int, dict]:
    """Each band's `active` values in a preset of the reply, by its number; a list or an
    entry the reply gives in a shape it should not is left out."""
    found = preset.get("bands")
    return {b["band"]: values for b in (found if isinstance(found, list) else ())
            if isinstance(b, dict) and whole(b.get("band"))
            and isinstance(values := b.get("active"), dict)}


def balance_text(value: int) -> str:
    """A Game/Voice balance, 0 all Voice and 255 all Game, in percent."""
    return shares_words(game_share(value))


def slider_text(name: str, value: int) -> str:
    return balance_text(value) if name == "default_balance" else percent(value)


def snapped(value: int) -> int:
    """`value`, or the balance tick within SNAP_STEPS of it."""
    tick = min(BALANCE_TICKS, key=lambda t: abs(t - value))
    return tick if abs(tick - value) <= SNAP_STEPS else value


def unsaved_text(keys: list[str]) -> str:
    """The banner's list: `preset_2` is "Slot 2", the base station's place, a setting its
    name."""
    words = [f"Slot {key.removeprefix('preset_')}" if key.startswith("preset_")
             else SETTING_NAMES.get(key, key.replace("_", " ")) for key in keys]
    return NOT_SAVED.format(", ".join(words))


def versions_text(versions: object) -> str:
    """The Firmware row's subtitle: the firmware versions the reply gives, or NOT_READ."""
    versions = versions if isinstance(versions, dict) else {}
    parts = [f"{what} {versions[key]}"
             for what, key in (("Base station", "base"), ("headset", "headset"))
             if isinstance(versions.get(key), str)]
    return ", ".join(parts) if parts else NOT_READ


def _balance_scale(label: str) -> Gtk.Scale:
    """A Game/Voice balance's slider, with its ticks."""
    scale = spoken_scale(*BALANCE_RANGE, label, origin=False, spoken=balance_text)
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


class SaveWatch:
    """A save of the base station's flash, from Save to the reply that says it is over:
    once the daemon accepts it, `ask` reads the headset from what the daemon holds every
    SAVE_POLL_MS until the save ends or SAVE_WAIT has passed; then `say` gets how it went,
    and `read` has every setting read again unless the save worked and was read back. It
    builds no widget."""

    def __init__(self, ask: Callable[[], None], say: Callable[[str], object],
                 read: Callable[[], None]) -> None:
        self._ask = ask
        self._say = say
        self._read = read
        #: headset_save is out and unanswered: a second click sends nothing.
        self.sent = False
        #: A save was accepted and its end not yet seen (take).
        self.waiting = False
        self._deadline = 0.0
        #: The fast poll's GLib source while it runs.
        self.poll: int | None = None
        #: headset_save's round trip raised: the daemon may still have run it, which the
        #: next reply says (take).
        self.unsure = False

    def answered(self, accepted: bool) -> None:
        """headset_save's reply: one `accepted` waits for the save's end, and a refusal
        leaves Save to be clicked again."""
        self.sent = False
        if accepted:
            self.unsure = False
            self._wait()

    def unanswered(self) -> None:
        """headset_save's round trip raised. The daemon still reads a request whose client
        gave up, and answers the read sent after it: that reply says whether the save
        runs."""
        self.sent = False
        self.unsure = True

    def _wait(self) -> None:
        self.waiting = True
        self._deadline = time.monotonic() + SAVE_WAIT
        if self.poll is None:
            self.poll = GLib.timeout_add(SAVE_POLL_MS, self.tick)

    def tick(self) -> bool:
        """The fast poll: `ask`, until SAVE_WAIT has passed."""
        if time.monotonic() >= self._deadline:
            # Past the wait the five second poll sees the end.
            self.poll = None
            return False
        self._ask()
        return True

    def take(self, info: dict, saving: bool) -> None:
        """A `headset` reply, `saving` what it says of a save running."""
        if self.unsure:
            self.unsure = False
            if saving:
                self._wait()
            return
        if not self.waiting or saving:
            return
        self.stop()
        error = info.get("save_error")
        self._say(capitalized(str(error)) if error else SAVED)
        # A save that worked was read back before this reply, which then lists nothing
        # unsaved; one that failed, or whose read-back was cut short, owes the read.
        if error or info.get("unsaved"):
            self._read()

    def stop(self) -> None:
        """End the wait and its fast poll."""
        self.waiting = False
        if self.poll is not None:
            GLib.source_remove(self.poll)
            self.poll = None

    def lost(self) -> None:
        """The base or the daemon went, and the save with it: its end is unknown, so
        nothing is said."""
        self.stop()
        self.unsure = False


class HeadsetPage:
    """The Headset tab: status, microphone, equalizer, stream mix and base
    station, with Save and Revert for what differs from flash.

    `call` is the window's. Every write to the base station goes through `call` and answers
    with the whole `headset` reply, which sets the rows. HeadsetStack puts the Spatial sound
    group under Revert, in `page`, while this page shows.
    """

    #: The last reply said the base station answers, and a save is running.
    answering = True
    saving = False
    #: The rows take a change (_set_editable).
    _editable = False

    def __init__(self, parent: Gtk.Window, worker: DaemonWorker, call: Call,
                 toast: Callable[..., object],
                 set_status: SetStatus) -> None:
        self._parent = parent
        self._worker = worker
        self._call = call
        self._toast = toast
        self._set_status = set_status

        self._loading = LoadingGuard()
        self._pending = False
        #: The headset problem the last status gave the page (show_problem).
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
        #: The window's close ran (close): the band rows send nothing from then on.
        self._closed = False
        self._shown = EntryTexts()
        #: The Name row held text being typed when _set_editable grayed it out, which took
        #: the cursor: the polls leave that text, as they leave typing, until a name is put.
        self._typed_when_grayed = False
        # Lambdas, so each finds the page's method as it runs, a stand-in set later included.
        self.save_watch = SaveWatch(lambda: None if self._pending else self.refresh(ask=False),
                                    lambda text: self._toast(text),
                                    lambda: self.refresh(read=True))
        self.meter = InputMeterRow(worker)
        self.volumes = VolumeRows(worker, toast, inset=ARROW_ROOM)
        self.widget = self._build()

    # -- construction -----------------------------------------------------

    def _build(self) -> Gtk.Widget:
        self.page = page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                                   margin_top=18, margin_bottom=18, margin_start=18,
                                   margin_end=18)
        self.sliders: dict[str, Gtk.Scale] = {}
        self.setting_rows: dict[str, Adw.ActionRow] = {}
        page.append(self._status_group())
        page.append(self._mic_group())
        self.library = PresetsDialog(self._parent, self._worker, self._toast, self.write_preset,
                                     self.shown_preset, self._show_save_new)
        page.append(self._eq_group())
        page.append(self._stream_group())
        page.append(self._base_group())
        page.append(self._revert_group())

        # A top bar, so it shows whatever the tab is scrolled to. Not markup: the unsaved
        # list can carry the daemon's words (unsaved_text). The headset's problems are the
        # Status row's (show_state, _failed).
        self.save_banner = Adw.Banner(use_markup=False)
        self.save_banner.connect("button-clicked", self.on_save)
        self.view = DeviceView(page, "audio-headset-symbolic", LOOKING)
        toolbar = Adw.ToolbarView(content=self.view.stack)
        toolbar.add_top_bar(self.save_banner)
        return toolbar

    def _status_group(self) -> Adw.PreferencesGroup:
        """The status group: the headset's state and its readings."""
        # Titled from the reply: its name.
        status = self.status_group = Adw.PreferencesGroup(title=HEADSET_TAB)
        self.state_row, self.state_dot, self.battery_row, self.battery_icon = status_rows(
            status)
        # Its own row, as the G432's: the state row says the state.
        self.firmware_row = plain_row(Adw.ActionRow, FIRMWARE, "", visible=False)
        status.add(self.firmware_row)
        # A reading, set by the headset's buttons, as the Centurion's: a grayed slider would
        # read as a setting the device refuses, beside the Default balance slider.
        self.mix_row = plain_row(Adw.ActionRow, BALANCE, "", tooltip_text=MIX_HELP,
                                 visible=False)
        status.add(self.mix_row)
        # Under Balance (the owner's decision of 2026-10-25).
        for word in OUTPUT_WORDS:
            status.add(self.volumes.rows[word])
        return status

    def _mic_group(self) -> Adw.PreferencesGroup:
        mic = self.mic_group = Adw.PreferencesGroup(title="Microphone")
        mic.add(self._slider_row("mic_level"))
        mic.add(self._slider_row("sidetone"))
        mic.add(self._combo_row("noise_gate", tuple(NOISE_GATE_TIPS[gate]
                                                      for gate in NOISE_GATES)))
        mic.add(self._combo_row("mic_eq"))
        mic.add(self.volumes.rows[MIC])
        mic.add(self.meter.row)
        return mic

    def _eq_group(self) -> Adw.PreferencesGroup:
        eq = self.eq_group = Adw.PreferencesGroup(title="Equalizer")
        # The name as the subtitle, as the Keyboard tab's Active: a preset's name is one
        # people type, and the inline value is cut at 20 characters.
        self.preset_row = Adw.ComboRow(title=PRESET, use_subtitle=True,
                                       model=Gtk.StringList.new([]))
        self.preset_row.connect("notify::selected", self.on_preset)
        eq.add(self.preset_row)
        self.name_row = Adw.EntryRow(title="Name", show_apply_button=True)
        self.name_row.connect("apply", self.on_name)
        watch_name(self.name_row)
        eq.add(self.name_row)
        self.band_rows: list[Adw.ExpanderRow] = []
        self.gain_scales: list[Gtk.Scale] = []
        self.freq_rows: list[Adw.SpinRow] = []
        self.width_rows: dict[int, Adw.SpinRow] = {}
        for band in BANDS:
            row = Adw.ExpanderRow(title=f"Band {band}")
            gain = spoken_scale(*GAIN_RANGE, f"Band {band} gain", page=1, origin=False,
                                spoken=signed)
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
        self.import_row = Adw.ButtonRow(title=ASTROEQ_IMPORT,
                                        start_icon_name="document-open-symbolic")
        self.import_row.connect("activated", self.on_import)
        # The rows that bring a curve in first, as the G432's Copy, then the save and the
        # reset: the equalizer group's one order (the owner's decision of 2026-10-22).
        eq.add(self.import_row)
        self.load_row = Adw.ButtonRow(title=LOAD_PRESET, tooltip_text=LOAD_PRESET_HELP,
                                      start_icon_name="document-send-symbolic")
        self.load_row.connect("activated", lambda _row: self.library.open())
        eq.add(self.load_row)
        self.save_new_row = save_new_row(SAVE_NEW_HELP, self.on_save_new)
        eq.add(self.save_new_row)
        self.reset_row = Adw.ButtonRow(title=PRESET_RESET, start_icon_name="edit-undo-symbolic")
        self.reset_row.connect("activated", self.on_reset)
        eq.add(self.reset_row)
        return eq

    def _stream_group(self) -> Adw.PreferencesGroup:
        """The Stream mix group: a slider per STREAM_SETTINGS name."""
        stream = self.stream_group = Adw.PreferencesGroup(title=STREAM_MIX,
                                                          description=STREAM_HELP)
        for name in STREAM_SETTINGS:
            stream.add(self._slider_row(name))
        return stream

    def _base_group(self) -> Adw.PreferencesGroup:
        base = self.base_group = Adw.PreferencesGroup(title="Base station")
        base.add(self._slider_row("default_balance"))
        watch_pointer(self.sliders["default_balance"], partial(self.on_press, "default_balance"),
                      partial(self.on_release, "default_balance"))
        base.add(self._slider_row("alert_volume"))
        base.add(self._combo_row("auto_off"))
        return base

    def _revert_group(self) -> Adw.PreferencesGroup:
        """The group that holds Revert to saved."""
        self.revert_group = Adw.PreferencesGroup()
        self.revert_row = Adw.ButtonRow(title=f"{REVERT_TO_SAVED}…",
                                        start_icon_name="edit-undo-symbolic")
        self.revert_row.connect("activated", self.on_revert)
        self.revert_group.add(self.revert_row)
        return self.revert_group

    def _slider_row(self, name: str) -> Adw.ActionRow:
        """A setting's slider."""
        balance = name == "default_balance"
        row = Adw.ActionRow(title=ROW_TITLES[name], use_markup=False, visible=False)
        scale = (_balance_scale(SETTING_NAMES[name]) if balance
                 else spoken_scale(*SETTING_RANGES[name], SLIDER_NAMES[name]))
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
        holds. With `ask`, the open Load preset to headset dialog reads the library again too:
        a change the CLI made shows. The volumes are read again where their rows show
        (VolumeRows.refresh)."""
        asking = ask and not self.saving
        self.refresh(ask=asking, read=asking and not self.read_done)
        self.volumes.refresh()
        if ask:
            self.library.refresh()

    def refresh(self, force: bool = False, ask: bool = True, read: bool = False) -> None:
        """Ask for the headset. `read` has the daemon read every setting again
        first, tens of round trips, so it waits SLOW_REQUEST_WAIT as the CLI's `--read`
        does; `force` puts the name back even with the cursor in it."""
        self._pending = True
        request: dict = {"command": "headset"}
        if read:
            request["read"] = True
        elif not ask:
            request["ask"] = False
        self._worker.call(request, lambda info: self._on_info(info, force, read),
                          SLOW_REQUEST_WAIT if read else REPLY_WAIT)

    def daemon_gone(self, error: Exception) -> None:
        """A read failed, the window's or the tab's: the tab says it is the daemon."""
        if daemon_down(error):
            self._save_lost()
        self._failed(unreachable(error), DAEMON_WAIT)

    def show_problem(self, problem: dict | None) -> None:
        """Take a problem from the daemon's status: a `headset_*` one, or another family's
        while no headset is attached (Window._take_devices). Until a headset is seen the
        status page shows it, and after that the Status row of a base station not answering
        does, the problem saying how to wake it (show_state); the reply's refusal says the
        others (_failed)."""
        message = str((problem or {}).get("message") or "")
        self._problem = capitalized(message) if message else None
        if self._problem and not self.view.seen:
            self.view.missing(NO_HEADSET, self._problem)

    def _failed(self, detail: str, title: str = NO_HEADSET) -> None:
        """No headset to show: said on the status page until one has been seen, and after
        that on the Status row alone, as the Keyboard tab says it: a toast would repeat it."""
        self.read_done = False
        set_dot(self.state_dot, False)
        self.state_row.set_subtitle(capitalized(detail))
        # The readings were the gone base's, as the Mouse and Keyboard tabs have it.
        self.state_row.set_tooltip_text(None)
        show_battery(self.battery_row, self.battery_icon, None, gone=True)
        self.firmware_row.set_visible(False)
        self.mix_row.set_visible(False)
        self.meter.show_headset(None)
        self.volumes.show_headset(None)
        self._set_editable(False)
        self.revert_row.set_sensitive(False)
        self.save_banner.set_sensitive(False)
        if not self.view.seen:
            self.view.missing(title, self._problem if title == NO_HEADSET and self._problem
                              else detail)

    def _set_editable(self, editable: bool) -> None:
        """Load preset to headset stays sensitive: its dialog renames, deletes and adds with
        no base station, and grays out its own Load (set_writable)."""
        if not editable and self._shown.typing(self._parent, self.name_row):
            # Grayed out, the row loses the cursor, which EntryTexts.typing goes by.
            self._typed_when_grayed = True
        for row in (*self.setting_rows.values(), self.preset_row, self.name_row,
                    *self.band_rows, self.import_row, self.reset_row):
            row.set_sensitive(editable)
        self._editable = editable
        self._show_save_new()
        self.library.set_writable(editable)
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
            # The words of the other headset pages for a refusal that gives none.
            self._failed(str(info.get("error") or NOT_FOUND))
            return
        self.view.found()
        self.meter.show_headset(A50.key)
        self.volumes.show_headset(A50.key)
        self.answering = info.get("answering") is not False
        self.saving = info.get("saving") is True
        # The daemon skips a read while the base is silent or a save runs,
        # and a timeout cuts one short, which leaves the base silent too.
        self.read_done = self.read_done or (read and self.answering and not self.saving)
        self._set_editable(self.answering and not self.saving)
        self.save_banner.set_sensitive(self.answering)
        self.show_state(info)
        self.show_settings(info.get("settings"), info.get("read_at") is not None)
        self.show_presets(info, force)
        self.show_unsaved(info.get("unsaved"))
        self.save_watch.take(info, self.saving)

    def show_state(self, info: dict) -> None:
        """The status group's title and rows, from the reply."""
        self.status_group.set_title(GLib.markup_escape_text(str(info.get("name")
                                                                or HEADSET_TAB)))
        linked = info.get("linked") is True
        state: bool | str
        if not self.answering:
            # The daemon's problem says how to wake it, where status gave one.
            words, state = self._problem or NOT_ANSWERING, ASLEEP
        elif linked:
            words, state = LINKED, True
        else:
            words = HEADSET_DOCKED if info.get("docked") is True else HEADSET_OFF
            state = ASLEEP
        set_dot(self.state_dot, state)
        if state == ASLEEP:
            # set_dot's label, asleep or switched off, is wrong for a silent base station.
            self.state_dot.update_property([Gtk.AccessibleProperty.LABEL], [words])
        self.state_row.set_subtitle(words)
        self.firmware_row.set_subtitle(displayable(versions_text(info.get("versions"))))
        self.firmware_row.set_visible(True)
        device = info.get("device")
        self.state_row.set_tooltip_text(str(device) if device else None)
        battery = info.get("battery") if isinstance(info.get("battery"), dict) else None
        # A headset away from the base says when its reading was taken.
        show_battery(self.battery_row, self.battery_icon, battery, kept_at(info))
        balance = _int(info.get("balance"), *BALANCE_RANGE)
        self.mix_row.set_visible(balance is not None)
        if balance is not None:
            self.mix_row.set_subtitle(balance_text(balance))

    def show_settings(self, settings: object, read_whole: bool) -> None:
        """Each setting's row on its active value. One the reply lacks, or gives in a shape it
        should not, says Not read with its control off until the base station's first full
        read (`read_whole`), as the A50 X's and the G432's rows do, and hides its row after
        it."""
        settings = settings if isinstance(settings, dict) else {}
        self._active = {}
        for name in SETTINGS:
            value = self._active_of(settings.get(name), object)
            if name in SETTING_RANGES:
                value = _int(value, *SETTING_RANGES[name])
            elif value not in SETTING_CHOICES[name]:
                value = None
            row = self.setting_rows[name]
            row.set_visible(value is not None or not read_whole)
            if value is None:
                row.set_subtitle(NOT_READ)
                row.set_sensitive(False)
                continue
            if name not in self.sliders:
                # Its value shows in the row itself.
                row.set_subtitle("")
            self._active[name] = value
            if self._holds.held(name):
                continue
            # _build gives a slider to each of SETTING_RANGES, whose value _int passed above,
            # and a combo row (_combo_row) to every other setting.
            if name in self.sliders:
                self._show_slider(name, cast(int, value))
            else:
                self._loading.put_selected(cast(Adw.ComboRow, self.setting_rows[name]),
                                   SETTING_CHOICES[name].index(value))
        for group, names in ((self.mic_group, MIC_SETTINGS), (self.stream_group, STREAM_SETTINGS),
                             (self.base_group, BASE_SETTINGS)):
            group.set_visible(not read_whole or any(name in self._active for name in names))

    def show_presets(self, info: dict, force: bool) -> None:
        """The preset dropdown, and the active preset's name, gains and bands; a preset or a
        band the reply gives in a shape it should not is left out, as a setting is."""
        if self._holds.held("preset"):
            return                              # a reply to a read sent before the switch
        listed = info.get("presets")
        presets = {p["preset"]: p for p in (listed if isinstance(listed, list) else ())
                   if isinstance(p, dict) and whole(p.get("preset")) and p["preset"] in PRESETS}
        given = info.get("preset")
        active = given if whole(given) and given in presets else None
        switched = active != self._preset
        if switched:
            # An edit still settling was made on the preset shown: it goes there.
            self._flush_eq()
        self._presets, self._preset = presets, active
        self.eq_group.set_visible(active is not None)
        if active is None:
            return
        ids = sorted(presets)
        names = [self._active_of(presets[n].get("name"), str) for n in ids]
        labels = [preset_label(n, name) for n, name in zip(ids, names, strict=True)]
        if (ids, labels) != (self._preset_ids, self._preset_labels):
            self._preset_ids, self._preset_labels = ids, labels
            self._loading.put_model(self.preset_row, Gtk.StringList.new(labels))
        self.library.show_slots(ids, names, active)
        self._loading.put_selected(self.preset_row, ids.index(active))
        self.reset_row.set_tooltip_text(RESET_HELP.format(default_slot(active).name))
        preset = presets[active]
        name = self._active_of(preset.get("name"), str)
        self.name_row.set_visible(name is not None)
        # A name being typed was for the preset shown: a switch drops it.
        typing = self._shown.typing(self._parent, self.name_row) or (
            self._typed_when_grayed and self._shown.typed(self.name_row))
        if name is not None and not self._holds.held("name") and (
                force or switched or not typing):
            # No loading guard: set_text never emits `apply`, the row's only handler (on_name).
            self._shown.show(self.name_row, name)
            self._typed_when_grayed = False
        gains = self._active_of(preset.get("gains"), list)
        if not (gains is not None and len(gains) == len(BANDS)
                and all(_int(g, *GAIN_RANGE) is not None for g in gains)):
            gains = None
        bands = _active_bands(preset)
        for index, band in enumerate(BANDS):
            self._show_band(active, index, band, None if gains is None else gains[index],
                            bands.get(band, {}))
        self._show_save_new()

    def _show_band(self, preset: int, index: int, band: int, gain: int | None,
                   found: dict) -> None:
        """One band's row of `preset`, the active one: shown with its gain, and its frequency
        and width when the reply has them."""
        self.band_rows[index].set_visible(gain is not None)
        freq = _int(found.get("freq"), *FREQ_RANGE)
        width = found.get("width")
        shown = freq is not None and (band in SHELVES or (
            number(width) and WIDTH_RANGE[0] <= width <= WIDTH_RANGE[1]))
        self.freq_rows[index].set_visible(shown)
        if band in self.width_rows:
            self.width_rows[band].set_visible(shown)
        if gain is not None and not self._holds.held(gains_key(preset)):
            self._loading.put_value(self.gain_scales[index], gain)
        # `shown` says the frequency is there: asking again only tells the type checker so.
        if shown and freq is not None and not self._holds.held(band_key(band, preset)):
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
        gain = signed(round(self.gain_scales[index].get_value()))
        freq = self.freq_rows[index]
        # In every view's words (hertz); the Frequency row, a value typed, keeps Hz.
        self.band_rows[index].set_subtitle(
            f"{hertz(round(freq.get_value()))}, {gain}" if freq.get_visible() else gain)

    # -- writes -----------------------------------------------------------

    def _write(self, key: str, request: dict, timeout: float = REPLY_WAIT,
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
        self._holds.flush((GAINS_KEY, BAND_KEY))

    def flush(self) -> bool:
        """Send what is still settling now, a frequency or a width typed and not yet taken
        and a volume among it (VolumeRows), for the window's close or a save; True if any."""
        take_typed(*self.freq_rows, *self.width_rows.values())
        return self.volumes.flush() | self._holds.flush()

    def close(self) -> None:
        self._closed = True
        self._holds.cancel_all()
        self.save_watch.stop()
        self.meter.close()
        self.volumes.close()

    def on_slider(self, scale: Gtk.Scale, name: str) -> None:
        value = round(scale.get_value())
        self.setting_rows[name].set_subtitle(slider_text(name, value))
        if self._loading.active or self._holds.pressing(name):
            return
        self._send_later(name)

    def _send_later(self, name: str) -> None:
        self._holds.settle(
            name, lambda: self._send_setting(name, round(self.sliders[name].get_value())),
            SLIDER_SETTLE_MS)

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
        self._send_later(name)

    def on_choice(self, row: Adw.ComboRow, _p: GObject.ParamSpec, name: str) -> None:
        if self._loading.active:
            return
        index = row.get_selected()
        if index < len(SETTING_CHOICES[name]):
            self._send_setting(name, SETTING_CHOICES[name][index])

    def _send_setting(self, name: str, value: int | str) -> None:
        """Write one setting, unless it is unchanged, or the daemon never gave it."""
        if name not in self._active or self._holds.unchanged(name, value == self._active[name]):
            return
        self._write(name, {"command": "headset_set", "setting": name, "value": value})

    def on_preset(self, row: Adw.ComboRow, _p: GObject.ParamSpec) -> None:
        if self._loading.active:
            return
        index = row.get_selected()
        if index >= len(self._preset_ids) or self._holds.unchanged(
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
        if (refusal := name_refusal(name)) is not None:
            self._toast(refusal)
            return
        self._shown.took(row)
        self._write("name", {"command": "headset_eq", "preset": self._preset, "name": name},
                    SLOW_REQUEST_WAIT)

    def on_gain(self, _scale: Gtk.Scale, band: int) -> None:
        self._band_subtitle(band)
        preset = self._preset
        if self._loading.active or preset is None:
            return
        self._holds.settle(gains_key(preset), lambda: self._send_gains(preset), SLIDER_SETTLE_MS)

    def _send_gains(self, preset: int) -> None:
        key = gains_key(preset)
        gains = [round(scale.get_value()) for scale in self.gain_scales]
        shown = self._active_of(self._presets.get(preset, {}).get("gains"), list)
        if self._holds.unchanged(key, gains == shown):
            return
        self._write(key, {"command": "headset_eq", "preset": preset, "gains": gains},
                    SLOW_REQUEST_WAIT)

    def on_band(self, _row: Adw.SpinRow, _p: GObject.ParamSpec, band: int) -> None:
        # GTK takes a number typed in the row as the window is destroyed, after the close
        # (worker.take_typed).
        if self._closed:
            return
        self._band_subtitle(band)
        preset = self._preset
        if self._loading.active or preset is None:
            return
        self._holds.settle(band_key(band, preset), lambda: self._send_band(preset, band),
                           SPIN_SETTLE_MS)

    def _send_band(self, preset: int, band: int) -> None:
        key = band_key(band, preset)
        shown = asdict(self._shown_band(band))
        held = _active_bands(self._presets.get(preset, {})).get(band)
        if self._holds.unchanged(key, held == shown):
            return
        self._write(key, {"command": "headset_eq", "preset": preset,
                          "bands": [{"band": band, **shown}]},
                    SLOW_REQUEST_WAIT)

    def _shown_band(self, band: int) -> Band:
        """A band as its rows show it: no width on a shelf, which has no Width row."""
        width = self.width_rows.get(band)
        return Band(round(self.freq_rows[BANDS.index(band)].get_value()),
                    None if width is None else round(width.get_value(), 1))

    # -- the library ------------------------------------------------------

    @property
    def busy(self) -> bool:
        """A save of the library is out, or the library's read of a file to import before one,
        or a volume set (VolumeRows), which the window's close waits for; the Import row's
        own read (import_path) is not counted."""
        return self.library.busy or self.volumes.busy

    def shown_preset(self) -> EqPreset | None:
        """The active preset as its rows show it, an edit still settling included, under the
        name the base station gave, which Save as new preset's dialog starts from and Replace
        with the active preset drops, so a name astro.preset_name refuses is taken too; None
        while the base station has not given its name, gains and bands whole."""
        read = (None if self._preset is None
                else a50_preset(self._presets.get(self._preset), named=False))
        if read is None:
            return None
        return EqPreset.of_bands(
            read.name, [round(scale.get_value()) for scale in self.gain_scales],
            [self._shown_band(band) for band in BANDS])

    def _show_save_new(self) -> None:
        """Save as new preset, usable while the rows are (_set_editable) and the active preset
        is read whole, grayed out with the library full (style.offer_save_new); again as each
        read or change of the library lands."""
        offer_save_new(self.save_new_row, self._editable and self.shown_preset() is not None,
                       self.library.saved(), SAVE_NEW_HELP)

    def on_save_new(self, _row: Adw.ButtonRow) -> None:
        """Save as new preset: what the rows show, an edit still settling sent first, into
        the library under the name typed."""
        if (preset := self.shown_preset()) is None:
            return
        self._flush_eq()
        ask_name(self._parent, preset.name,
                 lambda name: self.library.save_new(replace(preset, name=name)),
                 SAVE_HEADING, SAVE_BODY)

    def on_reset(self, _row: Adw.ButtonRow) -> None:
        """Reset to default: the active slot's own name and curve written back, as an import
        writes them, unsaved until Save. An edit of it still settling is dropped, as Revert
        drops one: it is what the reset takes back."""
        if (preset := self._preset) is None:
            return
        for key in (gains_key(preset), *(band_key(band, preset) for band in BANDS)):
            self._holds.cancel(key)
        self._write("import", {**default_slot(preset).request(), "preset": preset},
                    SLOW_REQUEST_WAIT)

    def write_preset(self, slot: int, preset: EqPreset) -> None:
        """A preset of the library to `slot`, as an import writes one: its name, gains and
        bands, then read back. An edit still settling goes first, so this one is written
        over it."""
        self._flush_eq()
        self._write("import", {**preset.request(), "preset": slot}, SLOW_REQUEST_WAIT)

    # -- import -----------------------------------------------------------

    def on_import(self, _row: Adw.ButtonRow) -> None:
        if self._preset is None:
            return
        # The preset the dialog was opened for, whatever is active when it closes.
        astroeq_dialog(ASTROEQ_TITLE).open(self._parent, None,
                                           lambda d, result, p=self._preset: self._picked(
                                               d, result, p))

    def _picked(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult, preset: int) -> None:
        if (path := picked_path(dialog, result, self._toast)) is not None:
            self.import_path(path, preset)

    def import_path(self, path: Path, preset: int) -> None:
        """Parse an .astroeq file on the worker and write it to `preset`."""
        def parsed(result: EqPreset | Exception) -> None:
            if isinstance(result, Exception):
                # One sentence: the file's name can hold ". ", where short_note's first-sentence
                # line would cut it, as the Load preset dialog's import has it.
                # Whoever shared the file named it: a direction turn or a separator in the
                # name would draw the rest of the sentence in another order or on a new line.
                said = ASTROEQ_REFUSED.format(printable(path.name), reason(result))
                toast_note(self._toast, said, ASTROEQ_NOT_IMPORTED, one_sentence=True)
                return
            # The daemon reads the preset back before it answers.
            self._write("import", {**result.request(), "preset": preset}, SLOW_REQUEST_WAIT)
        self._worker.run(lambda: load_astroeq(path), parsed)

    # -- save and revert --------------------------------------------------

    def on_save(self, _banner: Adw.Banner) -> None:
        """Save: what still settles goes first, then `headset_save`, whose end save_watch
        follows."""
        watch = self.save_watch
        if watch.sent or watch.waiting or self.saving:
            return
        self.flush()
        watch.sent = True

        def done(reply: dict | None) -> None:
            watch.answered(reply is not None)
            if reply is not None:
                self._on_info(reply)

        def unanswered(_error: Exception) -> None:
            watch.unanswered()
            self.refresh(ask=False)
        self._call({"command": "headset_save"}, done, unanswered)

    def _save_lost(self) -> None:
        """The base or the daemon went, and a running save with it. Its end is unknown,
        so nothing is said; the unsaved list says the rest once the base is back."""
        self.save_watch.lost()
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
                toast_note(self._toast, reply["note"], NOT_REVERTED)
        self._write("revert", {"command": "headset_revert"}, SLOW_REQUEST_WAIT, then=done)

    def _ask(self, heading: str, body: str, response: str, then: Callable[[], None]) -> None:
        confirm(self._parent, heading, body, response, then)
