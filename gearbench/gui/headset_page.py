"""The headset tab: the Astro A50's base station settings, and Load preset to headset, the
dialog over the presets Gearbench keeps beside them (presets_dialog).

A change goes to the base station's RAM at once and to its flash only on Save,
so a banner lists what differs from flash. The window's 5 s headset poll (polls.Polls) calls
tick, which reads the headset unless a drag is under way or its last read is still out, as
the Mouse tab's does. Game output and Chat output
follow Balance, their volume as GNOME's Sound settings set it (volume_rows), and the
Microphone group ends with Noise suppression, the window's own on the computer, with Test the
microphone under it (noise_rows), Mic input, the microphone's volume set the same way, and
Input level, its level live (input_meter_row).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict, replace
from functools import partial
from pathlib import Path
from typing import cast

from gi.repository import Adw, Gio, GLib, GObject, Gtk

from ..a50_library import EqPreset, a50_preset, default_slot, slot_number
from ..astro import (
    AUTO_OFF_MINUTES,
    BALANCE_RANGE,
    BANDS,
    BASE_SETTINGS,
    DOCKED_OFF,
    FREQ_RANGE,
    GAIN_RANGE,
    MIC_SETTINGS,
    NAME as A50_NAME,
    NOISE_GATES,
    NOT_ANSWERING as A50_NOT_ANSWERING,
    OFF_OR_AWAY,
    SETTING_CHOICES,
    SETTING_NAMES,
    SETTING_RANGES,
    SETTING_WORDS,
    SETTINGS,
    SHELVES,
    SLOW_REQUEST_WAIT,
    STREAM_SETTINGS,
    WIDTH_RANGE,
    Band,
)
from ..astroeq import load_astroeq
from ..battery import kept_at
from ..pipewire_headsets import A50
from ..wording import (
    capitalized,
    hertz,
    number,
    printable,
    reason,
    shares_words,
    signed,
    whole,
    whole_within,
)
from .computer_rows import ComputerRows
from .device_rows import show_gone, status_rows
from .holds import Holds
from .presets_dialog import SAVE_BODY, TITLE, PresetsDialog
from .save_watch import SaveWatch
from .style import (
    ASLEEP,
    ASTROEQ_IMPORT,
    ASTROEQ_NOT_IMPORTED,
    ASTROEQ_REFUSED,
    ASTROEQ_TITLE,
    BALANCE,
    DAEMON_WAIT,
    HEADSET_NEAR,
    HEADSET_TAB,
    LOOKING,
    NOT_FOUND,
    NOT_READ,
    PRESET,
    PRESET_RESET,
    REVERT_TO_SAVED,
    SAVE_HEADING,
    SIDETONE,
    STREAM_HELP,
    STREAM_MIX,
    WRITING_BASE,
    DeviceView,
    EntryTexts,
    NoticesRow,
    ask_name,
    astroeq_dialog,
    between,
    confirm,
    displayable,
    firmware_row,
    header_spinner,
    holds_focus,
    name_button,
    name_refusal,
    offer_save_new,
    percent,
    picked_path,
    plain_row,
    preset_label,
    save_new_row,
    set_dot,
    show_battery,
    show_choice,
    show_firmware,
    show_header_spinner,
    spoken_scale,
    through,
    toast_note,
    watch_name,
    watch_pointer,
)
from .tips import NOISE_GATE_TIPS, NOT_READ_TIP, explain_choices
from .worker import (
    REPLY_WAIT,
    SETTLE_MS,
    SPIN_SETTLE_MS,
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
#: Its reason while no headset of any family is attached and the status names no problem,
#: in place of the A50 Gen 4's own refusal, which names that model alone: the A50 Gen 4 is
#: found only with its switch on PC, the mode with the control interface.
NO_HEADSET_FOUND = f"no headset found; plug one in, an {A50_NAME}'s switch on PC"
#: The Status row's subtitle, by state: the route while the headset is linked, the
#: headset off its link, docked or not, and the base station silent, in status's words; off it
#: and silent, the next step.
LINKED = through("base station")
HEADSET_OFF = f"{capitalized(OFF_OR_AWAY)}; {HEADSET_NEAR.format('base station')}"
HEADSET_DOCKED = capitalized(DOCKED_OFF)
NOT_ANSWERING = capitalized(A50_NOT_ANSWERING)

#: The banner's text for unsaved settings and during a save, which hides the button.
NOT_SAVED = "Not saved to the base station: {}"
SAVING = "Saving to the base station…"
SAVE_LABEL = "Save"
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
#: The combo rows' labels, in SETTING_CHOICES's order: a row's index is its value's there.
CHOICE_LABELS = {**{name: tuple(word.title() for word in words)
                    for name, words in SETTING_WORDS.items()},
                 "auto_off": tuple(f"{m} minutes" for m in AUTO_OFF_MINUTES)}
#: The tooltip of the row that saves the active preset into the library, and the row that
#: opens the library's dialog to load one into a slot.
SAVE_NEW_HELP = "Keeps the active preset's sound on this computer"
LOAD_PRESET = f"{TITLE}…"
LOAD_PRESET_HELP = "Puts a preset of the library into slot 1, 2 or 3"
#: The Reset to default row's tooltip, with the name of the active slot's default: what
#: comes back, as the G432's tooltip says it. Put back is the first-read values' word.
RESET_HELP = "{} back in this slot; Save keeps it"
MIX_HELP = "Set by the headset's Game and Voice buttons"
#: A band's Width row's unit, as Hz is its Frequency row's: the bandwidth over the
#: frequency, 1/Q (astro.Band).
WIDTH_UNIT = "Multiple of the frequency"

#: The room a slider other than a band's leaves at its right, where a band row draws its
#: expander arrow, so the ends of the style.SLIDER_WIDTH sliders line up down the tab:
#: libadwaita 1.9 draws the arrow 16 px wide with a 3 px margin, after the row's 6 px
#: spacing.
ARROW_ROOM = 25
#: The balance sliders' ticks, 25, 50 and 75 % of 0-255, and how near one a
#: release of the Default balance must be to land on it: about 4 %.
BALANCE_TICKS = (64, 128, 191)
SNAP_STEPS = 10

#: The start of each equalizer edit's Holds key. gains_key and band_key add its preset, so a
#: write to a preset the headset's EQ button switched away from holds none of the new
#: preset's rows.
GAINS_KEY, BAND_KEY = "gains:", "band_"


def gains_key(preset: int) -> str:
    return f"{GAINS_KEY}{preset}"


def band_key(band: int, preset: int) -> str:
    return f"{BAND_KEY}{band}:{preset}"


def _active_bands(preset: dict) -> dict[int, dict]:
    """Each band's `active` values in a preset of the reply, by its number; a list or an
    entry the reply gives in a shape it should not is left out."""
    found = preset.get("bands")
    return {b["band"]: values for b in (found if isinstance(found, list) else ())
            if isinstance(b, dict) and whole(b.get("band"))
            and isinstance(values := b.get("active"), dict)}


def balance_text(value: int) -> str:
    """A Game/Voice balance, 0 all Voice and 255 all Game, in percent."""
    return shares_words(value, BALANCE_RANGE[1])


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
    """The Firmware row's subtitle: the firmware versions the reply gives, or "" for none,
    which hides the row (show_firmware)."""
    versions = versions if isinstance(versions, dict) else {}
    parts = [f"{what} {versions[key]}"
             for what, key in (("Base station", "base"), ("headset", "headset"))
             if isinstance(versions.get(key), str)]
    return ", ".join(parts)


def _spoken_as(row: Adw.SpinRow, name: str) -> Adw.SpinRow:
    """`row`, with the spin button that takes its focus named `name` (name_button); the row
    shows its title still. The row's delegate is the button (libadwaita 1.9's, which the
    tests check)."""
    name_button(cast(Gtk.SpinButton, row.get_delegate()), name)
    return row


#: The status problems' code prefixes the Headset tab says: the A50 Gen 4's, then the
#: other families'.
HEADSET_PREFIXES = ("headset_", "a50x_", "g432_")


def headset_problems(problems: Sequence[dict], attached: bool, seen: bool
                     ) -> tuple[dict | None, list[dict]]:
    """The status's headset problems as the Headset tab says them: the one for the A50's
    page (HeadsetPage.show_problem), the A50 Gen 4's first, then another family's while no
    headset is attached (`attached` False), when the A50's page shows for all
    (HeadsetStack.take): an attached A50's Status row says the problem it is given
    (HeadsetPage.show_state). Then the tab banner's (HeadsetStack.show_problems): every
    family's, but the one the A50's status page says in place of its rows while no headset
    is attached and the page has seen none (`seen` False)."""
    said = [p for p in problems if p["code"].startswith(HEADSET_PREFIXES)]
    own = next((p for p in said if p["code"].startswith("headset_")), None)
    if not attached:
        own = own or next(iter(said), None)
    on_page = own if not attached and not seen else None
    return own, [p for p in said if p is not on_page]


class HeadsetPage:
    """The Headset tab: status, microphone, equalizer, stream mix and base
    station, with Save and Revert for what differs from flash.

    `call` is the window's. Every write to the base station goes through `call` and answers
    with the whole `headset` reply, which sets the rows. `kept_up` is told of an action the
    page runs itself that failed (Window.kept_up). HeadsetStack puts the groups every
    headset page shares in `shared` while this page shows: under the status page until an
    A50 is seen, then under Revert, in `page`. This page shows for every headset while none
    is attached, and the groups work without one, so Outputs' line for a headset away shows
    here once that headset's page has gone (seen on the hardware, 2026-10-28).
    """

    #: The last reply said the base station answers, and a save is running.
    answering = True
    saving = False
    #: The rows take a change (_set_editable).
    _editable = False

    def __init__(self, parent: Gtk.Window, worker: DaemonWorker, call: Call,
                 toast: Callable[..., object], kept_up: Callable[[], None],
                 set_status: SetStatus) -> None:
        self._parent = parent
        self._worker = worker
        self._call = call
        self._toast = toast
        self._kept_up = kept_up
        self._set_status = set_status

        self._loading = LoadingGuard()
        self._pending = False
        #: The headset problem the last status gave the page, and what the status page says
        #: under NO_HEADSET in place of the reply's refusal: that problem, else with no headset
        #: of any family attached NO_HEADSET_FOUND (show_problem).
        self._problem: str | None = None
        self._reason: str | None = None
        #: A `read: True` was answered by a responsive base station with no save
        #: running, so the daemon ran it, and the last reply still has its `read_at`. The
        #: tab asks for one when first shown and after the base or the daemon was lost,
        #: between two polls too.
        self.read_done = False
        #: Each setting's active value in the last reply, and the active preset.
        self._active: dict[str, object] = {}
        self._preset: int | None = None
        self._presets: dict[int, dict] = {}
        #: What the preset dropdown lists: the preset numbers and their labels.
        self._preset_ids: list[int] = []
        self._preset_labels: list[str] = []
        self._holds = Holds()
        #: How many equalizer writes are unanswered: the Equalizer group's spinner shows while
        #: any is (_write).
        self._eq_writes = 0
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
        self.computer = ComputerRows(worker, toast, kept_up, inset=ARROW_ROOM)
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
        self.library = PresetsDialog(self._parent, self._worker, self._toast, self._kept_up,
                                     self.write_preset, self.shown_preset, self._show_save_new)
        page.append(self._eq_group())
        page.append(self._stream_group())
        page.append(self._base_group())
        page.append(self._revert_group())

        # A top bar, so it shows whatever the tab is scrolled to. Not markup: the unsaved
        # list can carry the daemon's words (unsaved_text). The headset's problems are the
        # Status row's (show_state, _failed).
        self.save_banner = Adw.Banner(use_markup=False)
        self.save_banner.connect("button-clicked", self.on_save)
        # The groups every headset page shares (HeadsetStack), under the status page too: this
        # page shows for every headset while none is attached, and the groups work without one.
        self.shared = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.view = DeviceView(page, "audio-headset-symbolic", LOOKING, under=self.shared)
        toolbar = Adw.ToolbarView(content=self.view.stack)
        toolbar.add_top_bar(self.save_banner)
        return toolbar

    def _status_group(self) -> Adw.PreferencesGroup:
        # Titled from the reply: its name.
        status = self.status_group = Adw.PreferencesGroup(title=HEADSET_TAB)
        self.state_row, self.state_dot, self.battery_row, self.battery_icon = status_rows(
            status)
        # Before the readings, as on every device page.
        self.notices = NoticesRow(status, self._call)
        # Its own row, as the G432's: the state row says the state.
        self.firmware_row = firmware_row(status)
        # A reading, set by the headset's buttons, as the Centurion's: a grayed slider would
        # read as a setting the device refuses, beside the Default balance slider.
        self.mix_row = plain_row(Adw.ActionRow, BALANCE, "", tooltip_text=MIX_HELP,
                                 visible=False)
        status.add(self.mix_row)
        # Under Balance (the owner's decision of 2026-10-25).
        for row in self.computer.output_rows:
            status.add(row)
        return status

    def _mic_group(self) -> Adw.PreferencesGroup:
        mic = self.mic_group = Adw.PreferencesGroup(title="Microphone")
        mic.add(self._slider_row("mic_level"))
        mic.add(self._slider_row("sidetone"))
        mic.add(self._combo_row("noise_gate", tuple(NOISE_GATE_TIPS[gate]
                                                      for gate in NOISE_GATES)))
        mic.add(self._combo_row("mic_eq"))
        for row in self.computer.mic_rows:
            mic.add(row)
        return mic

    def _eq_group(self) -> Adw.PreferencesGroup:
        eq = self.eq_group = Adw.PreferencesGroup(title="Equalizer")
        self.eq_spinner = header_spinner(eq)
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
        valid_only = Gtk.SpinButtonUpdatePolicy.IF_VALID
        for band in BANDS:
            row = Adw.ExpanderRow(title=f"Band {band}")
            gain = spoken_scale(*GAIN_RANGE, f"Band {band} gain", page=1, origin=False,
                                spoken=signed)
            gain.add_mark(0, Gtk.PositionType.BOTTOM, None)
            gain.connect("value-changed", self.on_gain, band)
            row.add_suffix(gain)
            # Named by band for a screen reader, as the gain is: five rows say Frequency. A word
            # or a number past the range typed in either row puts back the value shown
            # (IF_VALID), where GTK's default would send the nearer end, and for a word ("2k")
            # the lowest.
            freq = _spoken_as(Adw.SpinRow(
                title="Frequency", subtitle="Hz", update_policy=valid_only,
                adjustment=Gtk.Adjustment(
                    lower=FREQ_RANGE[0], upper=FREQ_RANGE[1], value=1000,
                    step_increment=10, page_increment=100)), f"Band {band} frequency")
            freq.connect("notify::value", self.on_band, band)
            row.add_row(freq)
            if band not in SHELVES:
                width = _spoken_as(Adw.SpinRow(
                    title="Width", subtitle=WIDTH_UNIT, digits=1, update_policy=valid_only,
                    adjustment=Gtk.Adjustment(
                        lower=WIDTH_RANGE[0], upper=WIDTH_RANGE[1], value=1.0,
                        step_increment=0.1, page_increment=0.5)), f"Band {band} width")
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
        self.revert_group = Adw.PreferencesGroup()
        self.revert_row = Adw.ButtonRow(title=f"{REVERT_TO_SAVED}…",
                                        start_icon_name="edit-undo-symbolic")
        self.revert_row.connect("activated", self.on_revert)
        self.revert_group.add(self.revert_row)
        return self.revert_group

    def _slider_row(self, name: str) -> Adw.ActionRow:
        row = Adw.ActionRow(title=ROW_TITLES[name], use_markup=False, visible=False)
        if name == "default_balance":
            scale = spoken_scale(*BALANCE_RANGE, SLIDER_NAMES[name], origin=False,
                                 spoken=balance_text)
            for tick in BALANCE_TICKS:
                scale.add_mark(tick, Gtk.PositionType.BOTTOM, None)
            suffix: Gtk.Widget = between(scale, "Voice", "Game")
        else:
            scale = suffix = spoken_scale(*SETTING_RANGES[name], SLIDER_NAMES[name])
        scale.connect("value-changed", self.on_slider, name)
        # Clear of where a band's expander arrow sits (ARROW_ROOM).
        suffix.set_margin_end(ARROW_ROOM)
        row.add_suffix(suffix)
        self.sliders[name] = scale
        self.setting_rows[name] = row
        return row

    def _combo_row(self, name: str, tips: tuple[str, ...] = ()) -> Adw.ComboRow:
        row = Adw.ComboRow(title=ROW_TITLES[name], visible=False,
                           model=Gtk.StringList.new(CHOICE_LABELS[name]))
        if tips:
            # Not read's last, the entry show_choice lists after the values.
            explain_choices(row, (*tips, NOT_READ_TIP))
        row.connect("notify::selected", self.on_choice, name)
        self.setting_rows[name] = row
        return row

    # -- reading ----------------------------------------------------------

    def tick(self, dragging: bool, ask: bool = True) -> None:
        """The five second poll, asking as poll() does."""
        if not dragging and not self._pending:
            self.poll(ask)

    def poll(self, ask: bool = True) -> None:
        """Ask for the headset: every setting until a full read is done (read_done), its state
        after that; with `ask` False, and while a save runs, what the daemon holds. With `ask`,
        the library is read again where PresetsDialog.refresh reads it: a change the CLI made
        shows."""
        asking = ask and not self.saving
        self.refresh(ask=asking, read=asking and not self.read_done)
        self.computer.refresh()
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

    def show_problem(self, problem: dict | None, none_attached: bool = False) -> None:
        """Take a problem from the daemon's status: a `headset_*` one, or another family's
        while no headset is attached (headset_problems), and whether the status named
        no headset of any family attached, when this page shows for all (HeadsetStack).
        Until a headset is seen the status page shows the problem, else with none attached
        NO_HEADSET_FOUND; after that the Status row of a base station not answering shows
        the problem, saying how to wake it (show_state), and the reply's refusal says the
        others (_failed)."""
        message = str((problem or {}).get("message") or "")
        self._problem = displayable(capitalized(message)) if message else None
        self._reason = self._problem or (NO_HEADSET_FOUND if none_attached else None)
        if self._reason and not self.view.seen:
            self.view.missing(NO_HEADSET, self._reason)

    def _failed(self, detail: str, title: str = NO_HEADSET) -> None:
        """No headset to show: said on the status page until one has been seen, and after
        that on the Status row alone, as the Keyboard tab says it: a toast would repeat it."""
        self.read_done = False
        show_gone(self.state_row, self.state_dot, self.battery_row, self.battery_icon,
                  self.notices, detail)
        show_firmware(self.firmware_row, None)
        self.mix_row.set_visible(False)
        self.computer.gone()
        self._set_editable(False)
        self.revert_row.set_sensitive(False)
        self.save_banner.set_sensitive(False)
        if not self.view.seen:
            self.view.missing(title, self._reason if title == NO_HEADSET and self._reason
                              else detail)

    def _set_editable(self, editable: bool) -> None:
        """Load preset to headset stays sensitive: its dialog renames, deletes and adds with
        no base station, and grays out its own Load (set_writable). Graying the Name row or a
        band's spin row with the cursor in it takes the cursor out first, and in the spin row
        drops a number typed and not yet taken."""
        focus = self._parent.get_focus()
        if not editable and holds_focus(focus, self.name_row):
            if self._shown.typed(self.name_row):
                # Grayed out, the row loses the cursor, which EntryTexts.typing goes by.
                self._typed_when_grayed = True
            # GTK grays a widget before it moves the cursor out, and a grayed widget's
            # controllers get no focus-out: the row's text still took itself for focused, so
            # a name put in it later started a cursor blink, which warned "did not receive a
            # focus-out event" at the next frame (GTK 4.22.4).
            self._parent.set_focus(None)
        for spin in (*self.freq_rows, *self.width_rows.values()):
            if not editable and holds_focus(focus, spin):
                # The same for a spin row, whose focus-out takes the text in it, half typed or
                # not. The signal has GtkSpinButton write its value over the text; a set_value
                # that changes no value leaves the text alone (measured with libadwaita 1.9.1).
                with self._loading:
                    spin.get_adjustment().emit("value-changed")
                    self._parent.set_focus(None)
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
        self.computer.show_headset(A50.key)
        self.answering = info.get("answering") is not False
        self.saving = info.get("saving") is True
        # The daemon skips a read while the base is silent or a save runs,
        # and a timeout cuts one short, which leaves the base silent too. A reply with no
        # read_at is from a daemon that holds no full read: one restarted, or a base station
        # replugged, between two polls, which no failed reply showed.
        self.read_done = info.get("read_at") is not None and (
            self.read_done or (read and self.answering and not self.saving))
        self._set_editable(self.answering and not self.saving)
        self.save_banner.set_sensitive(self.answering)
        self.show_state(info)
        self.show_settings(info.get("settings"), info.get("read_at") is not None)
        self.show_presets(info, force)
        self.show_unsaved(info.get("unsaved"))
        self.save_watch.take(info, self.saving)

    def show_state(self, info: dict) -> None:
        """The status group's title and rows, from the reply."""
        self.status_group.set_title(
            GLib.markup_escape_text(displayable(str(info.get("name") or HEADSET_TAB))))
        linked = info.get("linked") is True
        state: bool | str
        if not self.answering:
            # Status's headset problem where it gave one (another can come first);
            # NOT_ANSWERING's words until then.
            words, state = self._problem or NOT_ANSWERING, ASLEEP
        elif linked:
            words, state = LINKED, True
        else:
            words = HEADSET_DOCKED if info.get("docked") is True else HEADSET_OFF
            state = ASLEEP
        set_dot(self.state_dot, state)
        if state == ASLEEP:
            # set_dot's label is the bare state; these words say what it is on a base station.
            self.state_dot.update_property([Gtk.AccessibleProperty.LABEL], [words])
        self.state_row.set_subtitle(words)
        show_firmware(self.firmware_row, versions_text(info.get("versions")))
        device = info.get("device")
        self.state_row.show_node(str(device) if device else None)
        battery = info.get("battery") if isinstance(info.get("battery"), dict) else None
        # A headset away from the base says when its reading was taken.
        show_battery(self.battery_row, self.battery_icon, battery, kept_at(info),
                     preset=info.get("battery_notices"))
        self.notices.show({"headset": A50.key}, info)
        balance = whole_within(info.get("balance"), *BALANCE_RANGE)
        self.mix_row.set_visible(balance is not None)
        if balance is not None:
            self.mix_row.set_subtitle(balance_text(balance))

    def show_settings(self, settings: object, read_whole: bool) -> None:
        """Each setting's row on its active value. One the reply lacks, or gives in a shape it
        should not, is Not read with its control off until the base station's first full
        read (`read_whole`), as the A50 X's and the G432's rows are, and hides its row after
        it: a slider's row says it beneath, a list picks it (show_choice)."""
        settings = settings if isinstance(settings, dict) else {}
        self._active = {}
        for name in SETTINGS:
            value = self._active_of(settings.get(name), object)
            if name in SETTING_RANGES:
                value = whole_within(value, *SETTING_RANGES[name])
            elif value not in SETTING_CHOICES[name]:
                value = None
            row = self.setting_rows[name]
            row.set_visible(value is not None or not read_whole)
            if value is None:
                # A slider grayed out or hidden under the pointer never sees its release.
                self._holds.release(name)
                row.set_sensitive(False)
                if name in self.sliders:
                    row.set_subtitle(NOT_READ)
                else:
                    show_choice(self._loading, cast(Adw.ComboRow, row), CHOICE_LABELS[name],
                                None)
                continue
            self._active[name] = value
            if self._holds.held(name):
                continue
            # _build gives a slider to each of SETTING_RANGES, whose value whole_within
            # passed above, and a combo row (_combo_row) to every other setting.
            if name in self.sliders:
                level = cast(int, value)
                self._loading.put_value(self.sliders[name], level)
                row.set_subtitle(slider_text(name, level))
            else:
                show_choice(self._loading, cast(Adw.ComboRow, row), CHOICE_LABELS[name],
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
        presets = {slot: p for p in (listed if isinstance(listed, list) else ())
                   if (slot := slot_number(p)) is not None}
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
                and all(whole_within(g, *GAIN_RANGE) is not None for g in gains)):
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
        freq = whole_within(found.get("freq"), *FREQ_RANGE)
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
        the rows back. The Equalizer group's spinner shows while a `headset_eq` is out."""
        self._holds.sent(key)
        # Only a write that changes the name puts it back over one being typed.
        force = key in ("name", "revert", "import")
        # Each takes several round trips, up to SLOW_REQUEST_WAIT.
        equalizer = request["command"] == "headset_eq"
        if equalizer:
            self._eq_writes += 1
            show_header_spinner(self.eq_spinner, WRITING_BASE)

        def landed() -> None:
            self._holds.answered(key)
            if equalizer:
                self._eq_writes -= 1
                if not self._eq_writes:
                    show_header_spinner(self.eq_spinner, None)

        def done(reply: dict | None) -> None:
            landed()
            if reply is None:
                self.refresh(force=force)
                return
            self._on_info(reply, force=force)
            if then is not None:
                then(reply)

        def unanswered(_error: Exception) -> None:
            # `call` set the status; the next poll reads the headset.
            landed()
        self._call(request, done, unanswered, timeout=timeout)

    def _flush_eq(self) -> None:
        self._holds.flush((GAINS_KEY, BAND_KEY))

    def flush(self) -> bool:
        """Send what is still settling now, a frequency or a width typed and not yet taken
        and a volume among it (ComputerRows.flush), for the window's close or a save; True if
        any."""
        take_typed(*self.freq_rows, *self.width_rows.values())
        return self.computer.flush() | self._holds.flush()

    def close(self) -> None:
        self._closed = True
        self._holds.cancel_all()
        self.save_watch.stop()
        self.computer.close()

    def on_slider(self, scale: Gtk.Scale, name: str) -> None:
        value = round(scale.get_value())
        self.setting_rows[name].set_subtitle(slider_text(name, value))
        if self._loading.active or self._holds.pressing(name):
            return
        self._send_later(name)

    def _send_later(self, name: str) -> None:
        self._holds.settle(
            name, lambda: self._send_setting(name, round(self.sliders[name].get_value())),
            SETTLE_MS)

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
        self._holds.settle(gains_key(preset), lambda: self._send_gains(preset), SETTLE_MS)

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
        """An action of the library (PresetsDialog.busy) or of the computer's rows
        (ComputerRows.busy) is out, which the window's close waits for; the Import row's own
        read (import_path) is not counted."""
        return self.library.busy or self.computer.busy

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
        is read whole, grayed out with the library full (style.offer_save_new), which the
        Equalizer group's line then says; again as each read or change of the library lands."""
        self.eq_group.set_description(offer_save_new(
            self.save_new_row, self._editable and self.shown_preset() is not None,
            self.library.saved()))

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

    def toast_overlay(self) -> Adw.ToastOverlay | None:
        """Where the window's messages go while the Load preset to headset dialog is up
        (PresetsDialog.toast_overlay); None while it is not."""
        return self.library.toast_overlay()

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
        if self.saving or not watch.send():
            return
        self.flush()

        def done(reply: dict | None) -> None:
            watch.answered(reply)
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
