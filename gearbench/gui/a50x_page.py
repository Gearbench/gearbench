"""The Headset tab's page for the Centurion headsets: the Astro A50 X, the A50 Gen 5 and the
LIGHTSPEED ones.

The daemon holds what it read of the headset (`a50x`) and sends the page's writes, and
refuses every write to a model not tested on this hardware: the page says so before a click
and keeps those rows off. A slider the pointer drags follows it live, its number and the
headset, at most once each LIVE_MS, and sends its last value when the pointer lets go; one
moved by its keys sends once they settle (the owner's decision of 2026-10-22). Each
equalizer's Preset row lists the headset's own presets under Headset, then the model's
library on this computer under Library, which its LibraryCard (library_card) keeps, so
the headset's Gaming and the library's are told apart by their group. On a model with a
Game and a Chat output, Game output and Chat output follow Balance, their volume as GNOME's
Sound settings set it (volume_rows). The Microphone group ends with Mic input, the
microphone's volume set the same way, and Input level, its level live (input_meter_row).
Reads go to the worker straight, so a poll that finds no headset toasts nothing; writes go
through the window's `call`, which shows a refusal. Building it runs nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import cast

from gi.repository import Adw, Gio, GLib, GObject, Gtk

from ..a50x import INSTANCE_NAMES, INSTANCES, MODELS, PRODUCTS, Model
from ..astro import SETTING_NAMES
from ..battery import kept_at
from ..centurion_features import mix, mixer, noise_reduction, onboard_eq
from ..centurion_features.equalizer import GAIN_RANGE
from ..centurion_features.setting import SWITCH, Setting
from ..system_volume import MIC
from ..wording import capitalized, hertz, shares_words, signed, whole
from .holds import Holds
from .input_meter_row import InputMeterRow
from .library_card import CENTURION, LibraryCard
from .style import (
    ASLEEP,
    BALANCE,
    CHANGES_WAIT,
    CUSTOM,
    HEADSET_ASLEEP,
    HEADSET_TAB,
    NOT_FOUND,
    NOT_READ,
    PRESET,
    SIDETONE,
    STREAM_MIX,
    confirm,
    displayable,
    page_of,
    plain_row,
    preset_label,
    set_dot,
    show_battery,
    show_looking,
    show_untested,
    spoken_scale,
    status_rows,
    through,
    untested_row,
    watch_pointer,
)
from .tips import NOISE_GATE_TIPS, explain_choices
from .volume_rows import OUTPUT_WORDS, VolumeRows
from .worker import (
    LIVE_MS,
    SETTLE_MS,
    Call,
    DaemonWorker,
    LoadingGuard,
    Pace,
    Reply,
    Settle,
    take_typed,
    unreachable,
)

#: The page's name in the Headset tab's stack, the names `status` gives its models, and its
#: title in the tab's switcher before `status` names the model and on its status group
#: before a reply does: the tab's noun, which names no model.
PAGE = "a50x"
NAMES = frozenset(PRODUCTS.values())
TITLE = HEADSET_TAB
#: The row shown while the daemon refuses every write, said before a click.
READ_ONLY = "Read only"
#: An equalizer row's tooltip where the daemon reads its bands and never writes them, on a
#: tested model too, where Read only no longer shows.
EQUALIZER_READ_ONLY_HELP = "Gearbench reads this equalizer and does not change it"
BALANCE_HELP = "Set by the headset's balance dial"
#: The microphone's boom, the Microphone group's first row: down, live; up, muted. Not Mic,
#: which is the stream mix's level of the microphone.
BOOM, LIVE, MUTED = "Boom", "Live", "Muted"
#: The stream mix's rows by the words the socket gives their sources.
MIX_TITLES = {mixer.STREAM: "Stream port", "game": "Game", "voice": "Voice",
              "mic_out": "Mic", "bluetooth": "Bluetooth"}
#: Each stream mix slider's name for a screen reader, the A50 Gen 4's for the same levels,
#: which keeps the mix's Mic apart from the Microphone group's rows; the port's own level
#: is Stream port level.
MIX_NAMES = {mixer.STREAM: f"{MIX_TITLES[mixer.STREAM]} level",
             "game": SETTING_NAMES["stream_game"], "voice": SETTING_NAMES["stream_chat"],
             "mic_out": SETTING_NAMES["stream_mic"], "bluetooth": "Stream Bluetooth"}
#: Each setting's row title, and its slider's or dropdown's name for a screen reader, by its
#: key, of those the Microphone and Settings groups show: the balance shows in the status
#: group, as the headset's dial sets it.
ROW_TITLES = {"sidetone": SIDETONE, "volume": "Volume",
              "noise_gate": "Noise gate", "auto_sleep": "Auto sleep",
              "brightness": "Brightness", "deep_sleep": "Deep sleep", "tones": "Tones",
              "noise_reduction": capitalized(noise_reduction.WORDS), "eco_mode": "Eco mode"}
#: The settings the Microphone group shows; Settings shows the rest. Each takes the model
#: record's order, one order on every model (the owner's decision of 2026-10-22).
MIC_SETTINGS = frozenset({"sidetone", "noise_gate", "noise_reduction"})
#: A slider's tooltip, where its title needs one: its subtitle is its value.
ROW_HELP = {"brightness": "The base station's light"}
#: A spin row's subtitle, the unit of its bare number, as the Keyboard tab's idle times say
#: theirs.
SPIN_HELP = {"auto_sleep": "Minutes idle before the headset sleeps"}
#: A switch's subtitle where its title needs one, said without a pointer: Deep sleep's
#: says which device sleeps, beside Auto sleep in the Settings group.
SWITCH_HELP = {"deep_sleep": "The base station, after 15 minutes idle",
               "eco_mode": "The headset's battery saver"}
#: The row that asks to put every first-read setting back, its ellipsis for the question (the
#: HIG's writing style), and the question's own response.
PUT_BACK = "Put back"
PUT_BACK_OPENER = f"{PUT_BACK}…"
#: Its tooltip and its confirmation, each with BALANCE_TOO in the gap on a model with the
#: balance, which shows in the status group: the headset keeps each value written.
PUT_BACK_HELP = "Every setting, {}Microphone's too, as Gearbench first read it"
PUT_BACK_HEADING = "Put back every setting?"
PUT_BACK_BODY = ("Each setting, {}Microphone's too, goes back to what Gearbench first read. "
                 "The stream mix and the equalizers stay as they are.")
BALANCE_TOO = "the balance and "
#: The preset row's groups' headers: the presets the headset holds in its slots, then those of
#: its model's library on this computer; Custom, the last choice, is in a group with no
#: header.
HEADSET_GROUP, LIBRARY_GROUP = "Headset", "Library"


def balance_words(value: int) -> str:
    """The balance, 0 all Voice to 12 all Game, in whole percent."""
    return shares_words(mix.game_share(value))


def model_of(info: dict) -> Model | None:
    """The record of the model an `a50x` reply names, None for one this install does not
    know."""
    product = info.get("product")
    return MODELS.get(int(product, 16)) if isinstance(product, str) else None


def bands_written(model: Model | None, instance: str) -> bool:
    """Whether the daemon writes `instance`'s bands: those of the model's 0x020D equalizers
    (`instances`) and no others, so not 0x0636's, which it only reads; on a model this
    install does not know, the daemon decides."""
    return model is None or instance in model.instances


def mix_words(level: int) -> str:
    """A stream mix level in words, as its row and a screen reader say it: "16 of 32"."""
    return f"{level} of {mixer.LEVELS}"


def choice_values(setting: Setting) -> list[int | str]:
    """The values a setting picked from a list takes, as the socket gives them."""
    return [setting.value(raw) for raw in setting.wire]


def choice_words(setting: Setting, raw: int) -> str:
    """A value's words in a list: "Night", "Never", "15 minutes"."""
    return capitalized(setting.shown(setting.value(raw)))


# The keys of an instance's bands, a stream mix's slider and a source's mute, in the page's
# sends, pointer holds and last sent values, each prefix keeping them apart from a setting's
# key and from each other.
def gains_key(instance: str) -> str:
    return f"gains:{instance}"


def mix_key(source: str) -> str:
    return f"mix:{source}"


def mute_key(source: str) -> str:
    return f"muted:{source}"


def gain_name(instance: str, freq: int) -> str:
    """A band slider's name for a screen reader: "Headphones 50 Hz gain"."""
    return f"{INSTANCE_NAMES[instance]} {hertz(freq)} gain"


class PresetGroups:
    """A Preset row's list in groups, each a section under its header, by Gtk.SectionModel,
    which a Gtk.FlattenListModel of the groups' lists is: so a name in two groups, the
    headset's Gaming and the library's, is told apart by its group. `factory` is the row's
    header factory, and `headers` each header's title by the place its group starts."""

    def __init__(self) -> None:
        self.headers: dict[int, str] = {}
        self.factory = Gtk.SignalListItemFactory()
        self.factory.connect("setup", self._setup)
        self.factory.connect("bind", self._bind)

    def model(self, groups: tuple[tuple[str | None, tuple[str, ...]], ...]) -> Gio.ListModel:
        """The list of `groups`, each (its header or None for none, its names), a group with
        no names left out, as GTK makes no section of an empty list."""
        lists = Gio.ListStore.new(Gtk.StringList)
        self.headers, start = {}, 0
        for header, names in groups:
            if names:
                if header is not None:
                    self.headers[start] = header
                lists.append(Gtk.StringList.new(list(names)))
                start += len(names)
        return Gtk.FlattenListModel.new(lists)

    @staticmethod
    def _setup(_factory: Gtk.SignalListItemFactory, header: Gtk.ListHeader) -> None:
        header.set_child(Gtk.Label(xalign=0, css_classes=["heading"]))

    def _bind(self, _factory: Gtk.SignalListItemFactory, header: Gtk.ListHeader) -> None:
        """A section's header: its group's title, nothing for a group with none."""
        title = self.headers.get(header.get_start(), "")
        label = cast("Gtk.Label", header.get_child())
        label.set_label(title)
        label.set_visible(bool(title))


@dataclass
class Sends:
    """A slider's sends: `now` writes its value unless it is the last sent or the last
    reply's, `settle` once a run of its keys stops, and `pace` while the pointer drags it."""

    now: Callable[[], None]
    settle: Settle
    pace: Pace

    @classmethod
    def of(cls, now: Callable[[], None]) -> Sends:
        return cls(now, Settle(now), Pace(now, LIVE_MS))

    @property
    def pending(self) -> bool:
        return self.settle.pending or self.pace.pending

    def cancel(self) -> None:
        self.settle.cancel()
        self.pace.cancel()

    def flush(self) -> bool:
        """Send now what waits, both; True if anything did."""
        settled, paced = self.settle.flush(), self.pace.flush()
        return settled or paced


class A50XPage:
    """The Centurion headset's page, the A50 X's, the A50 Gen 5's or a LIGHTSPEED one's:
    `call` is the window's, for the writes, whose refusals it shows. HeadsetStack puts the
    Spatial sound group last in `page` while this page shows."""

    def __init__(self, worker: DaemonWorker, call: Call,
                 toast: Callable[..., object]) -> None:
        self._worker = worker
        self._call = call
        self._toast = toast
        self._loading = LoadingGuard()
        self._pending = self._closed = False
        #: The last reply's settings, equalizers, each instance's presets by name and the one
        #: it plays, and whether it let writes through; what each Preset row lists before
        #: Custom: a preset's name, or a library preset's number; and the groups it lists.
        self._settings: dict[str, object] = {}
        self._equalizers: dict[str, list[float]] = {}
        self._presets: dict[str, list[str]] = {}
        self._playing: dict[str, object] = {}
        self._writable = False
        self._picks: dict[str, list[str | int]] = {}
        self._groups: dict[str, tuple[tuple[str | None, tuple[str, ...]], ...]] = {}
        self.saved = LibraryCard(worker, toast, self._saved_changed, CENTURION)
        #: Each slider's sends by its key: a setting's, an instance's bands' and each stream
        #: mix row's; a setting picked from a list sends at once.
        self._sends: dict[str, Sends] = {
            **{gains_key(instance): Sends.of(partial(self._send_gains, instance))
               for instance in INSTANCES},
            **{mix_key(source): Sends.of(partial(self._send_mix, source))
               for source in MIX_TITLES}}
        #: The value each key's last write sent, while a write of it is not answered.
        self._sent: dict[str, object] = {}
        #: The last reply's stream mix: each source's level and mute, and the stream port's
        #: level, by the source's word; none while it is not known.
        self._mix: dict[str, dict] = {}
        #: The sliders the pointer holds down and how many writes of each key are not
        #: answered, by key: a setting's, `gains_key`'s, `mix_key`'s or `mute_key`'s. A reply
        #: meanwhile leaves the slider where it is (_held).
        self._holds = Holds()
        self.meter = InputMeterRow(worker)
        self.volumes = VolumeRows(worker, toast)
        self.widget = self._build()

    # -- construction -----------------------------------------------------

    def _build(self) -> Gtk.Widget:
        self.page = page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                                   margin_top=18, margin_bottom=18, margin_start=18,
                                   margin_end=18)
        # Every headset page's order: the microphone and the sound, then the device's own
        # settings (the owner's decision of 2026-10-22).
        page.append(self._status_group())
        page.append(self._mic_group())
        page.append(self._eq_group())
        page.append(self._mix_group())
        page.append(self._settings_group())
        return Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
                                  child=page)

    def _status_group(self) -> Adw.PreferencesGroup:
        group = self.status_group = Adw.PreferencesGroup(title=TITLE)
        self.state_row, self.state_dot, self.battery_row, self.battery_icon = status_rows(
            group)
        show_looking(self.state_row, self.state_dot)
        # Every device group's order: Status, Battery, the untested note, Read only, then
        # the readings (the owner's decision of 2026-10-22).
        self.untested_row = untested_row(group, self._toast)
        self.read_only_row = plain_row(Adw.ActionRow, READ_ONLY, CHANGES_WAIT, visible=False)
        group.add(self.read_only_row)
        self.balance_row = plain_row(Adw.ActionRow, BALANCE, "", tooltip_text=BALANCE_HELP,
                                     visible=False)
        group.add(self.balance_row)
        # Under Balance (the owner's decision of 2026-10-25).
        for word in OUTPUT_WORDS:
            group.add(self.volumes.rows[word])
        return group

    def _mic_group(self) -> Adw.PreferencesGroup:
        """Boom first, the reading of a model with the stream mix's boom, then the rows made
        with the Settings group's, for a model whose settings have them, and Mic input and
        Input level last; shown while Boom or a setting's row is."""
        self.mic_group = Adw.PreferencesGroup(title="Microphone", visible=False)
        self.boom_row = plain_row(Adw.ActionRow, BOOM, "", visible=False)
        self.mic_group.add(self.boom_row)
        self._add_mic_rows()
        return self.mic_group

    def _add_mic_rows(self) -> None:
        """Mic input and Input level, after the Microphone group's other rows."""
        self.mic_group.add(self.volumes.rows[MIC])
        self.mic_group.add(self.meter.row)

    def _settings_group(self) -> Adw.PreferencesGroup:
        group = self.settings_group = Adw.PreferencesGroup(title="Settings")
        #: The model whose settings the rows are, made when a reply names it, and each row
        #: and its control by its setting's key.
        self.model: Model | None = None
        self.rows: dict[str, Adw.ActionRow] = {}
        self.scales: dict[str, Gtk.Scale] = {}
        self.spins: dict[str, Adw.SpinRow] = {}
        self.choices: dict[str, Adw.ComboRow] = {}
        self.switches: dict[str, Adw.SwitchRow] = {}
        # Off until a reply lets writes through and holds first reads (show).
        self.put_back_row = Adw.ButtonRow(title=PUT_BACK_OPENER,
                                          tooltip_text=PUT_BACK_HELP.format(self._balance_too),
                                          start_icon_name="edit-undo-symbolic", sensitive=False)
        self.put_back_row.connect("activated", self.on_put_back)
        group.add(self.put_back_row)
        return group

    def _show_model(self, model: Model | None) -> None:
        """The Microphone and Settings groups' rows for `model`'s settings, made again for
        another model; none for a model this install does not know."""
        self.saved.model_shown(model)
        if model == self.model:
            return
        for send in self._sends.values():
            send.cancel()
        for key in (*self.scales, *self.spins):
            del self._sends[key]
        for key, row in self.rows.items():
            self._group_of(key).remove(row)
        # Each taken out and put back after the rows, so it stays last in its group.
        self.mic_group.remove(self.volumes.rows[MIC])
        self.mic_group.remove(self.meter.row)
        self.settings_group.remove(self.put_back_row)
        self.rows, self.scales, self.spins, self.choices, self.switches = {}, {}, {}, {}, {}
        # The bands too: another model's may take another range.
        for instance, expander in self.instance_rows.items():
            for band_row in self.band_rows[instance]:
                expander.remove(band_row)
            self.band_rows[instance], self.gain_scales[instance] = [], []
            expander.set_visible(model is None or instance in model.equalizers)
            # On the expander, whose tooltip GTK shows over a grayed row in it with none.
            expander.set_tooltip_text(None if bands_written(model, instance)
                                      else EQUALIZER_READ_ONLY_HELP)
        self._holds.forget()
        self._sent = {}
        self.model = model
        self.put_back_row.set_tooltip_text(PUT_BACK_HELP.format(self._balance_too))
        for setting in model.settings if model else ():
            if setting.key in ROW_TITLES:
                self._setting_row(setting)
        self._add_mic_rows()
        self.settings_group.add(self.put_back_row)

    def _show_mic_group(self) -> None:
        self.mic_group.set_visible(self.boom_row.get_visible()
                                   or not MIC_SETTINGS.isdisjoint(self.rows))

    def _setting_row(self, setting: Setting) -> None:
        """A setting's row: a spin row for an exact number in a unit that takes each value of
        its range, a slider for a level that does, a switch for off and on, else a list of its
        values."""
        key, title = setting.key, ROW_TITLES[setting.key]
        row: Adw.ActionRow
        if setting.names == SWITCH:
            row = self.switches[key] = Adw.SwitchRow(title=title)
            row.connect("notify::active", self.on_choice, key)
        elif setting.ranged and setting.unit:
            # Minutes, as the Keyboard tab's idle times are seconds: a spin row, Never for 0,
            # sent once its steps settle.
            low, high = setting.range
            row = self.spins[key] = Adw.SpinRow(
                title=title, subtitle=NOT_READ, adjustment=Gtk.Adjustment(
                    lower=low, upper=high, value=low, step_increment=1,
                    page_increment=page_of(low, high)))
            row.connect("output", self.on_spin_output, setting)
            row.connect("notify::value", self.on_spin, key)
            self._sends[key] = Sends.of(partial(self._send, key))
        elif setting.ranged:
            row = plain_row(Adw.ActionRow, title, NOT_READ)
            # A screen reader says the row's words, the unit with the number.
            scale = spoken_scale(*setting.range, title, page=page_of(*setting.range),
                                 spoken=setting.level)
            scale.connect("value-changed", self.on_slider, key)
            self._held_by_pointer(scale, key)
            row.add_suffix(scale)
            self.scales[key] = scale
            self._sends[key] = Sends.of(partial(self._send, key))
        else:
            row = Adw.ComboRow(title=title, model=Gtk.StringList.new(
                [choice_words(setting, raw) for raw in setting.wire]))
            if key == "noise_gate":
                # The A50 Gen 4's tips, each entry its own.
                explain_choices(row, [NOISE_GATE_TIPS[name] for name in setting.names])
            row.connect("notify::selected", self.on_choice, key)
            self.choices[key] = row
        row.set_tooltip_text(ROW_HELP.get(key))
        self._group_of(key).add(row)
        self.rows[key] = row

    def _group_of(self, key: str) -> Adw.PreferencesGroup:
        return self.mic_group if key in MIC_SETTINGS else self.settings_group

    @property
    def _balance_too(self) -> str:
        """What Put back's words add for the model shown: the balance, which its reset writes
        back too, on a model with one."""
        return (BALANCE_TOO if self.model is not None
                and self.model.setting_of(mix.ID) is not None else "")

    def _mix_group(self) -> Adw.PreferencesGroup:
        """The stream mix's rows, each a slider of its level, the sources' with a button that
        mutes it; shown for a model that has the mix."""
        group = self.mix_group = Adw.PreferencesGroup(title=STREAM_MIX, visible=False)
        self.mix_rows: dict[str, Adw.ActionRow] = {}
        self.mix_scales: dict[str, Gtk.Scale] = {}
        self.mutes: dict[str, Gtk.ToggleButton] = {}
        for source, title in MIX_TITLES.items():
            row = plain_row(Adw.ActionRow, title, NOT_READ)
            scale = spoken_scale(0, mixer.LEVELS, MIX_NAMES[source], page=1, spoken=mix_words)
            scale.connect("value-changed", self.on_mix_level, source)
            self._held_by_pointer(scale, mix_key(source))
            row.add_suffix(scale)
            if source != mixer.STREAM:
                mute = Gtk.ToggleButton(icon_name="audio-volume-muted-symbolic",
                                        valign=Gtk.Align.CENTER, tooltip_text=f"Mute {title}")
                mute.update_property([Gtk.AccessibleProperty.LABEL], [f"Mute {title}"])
                mute.connect("toggled", self.on_mute, source)
                row.add_suffix(mute)
                self.mutes[source] = mute
            group.add(row)
            self.mix_rows[source], self.mix_scales[source] = row, scale
        return group

    def _eq_group(self) -> Adw.PreferencesGroup:
        group = self.eq_group = Adw.PreferencesGroup(title="Equalizer")
        #: Each instance's row; its preset row, shown while its bands are read and it lists a
        #: preset, the headset's or the library's; and its band rows and their sliders, made at
        #: the first read that gives the instance its bands.
        self.instance_rows: dict[str, Adw.ExpanderRow] = {}
        self.preset_rows: dict[str, Adw.ComboRow] = {}
        self.preset_groups: dict[str, PresetGroups] = {}
        self.band_rows: dict[str, list[Adw.ActionRow]] = {}
        self.gain_scales: dict[str, list[Gtk.Scale]] = {}
        for instance in INSTANCES:
            row = plain_row(Adw.ExpanderRow, INSTANCE_NAMES[instance], NOT_READ)
            group.add(row)
            self.instance_rows[instance] = row
            groups = self.preset_groups[instance] = PresetGroups()
            # The name as the subtitle, as the Keyboard tab's Active: a preset's name is one
            # people type, and the inline value is cut at 20 characters.
            preset = Adw.ComboRow(title=PRESET, visible=False, use_subtitle=True,
                                  model=groups.model(()), header_factory=groups.factory)
            preset.connect("notify::selected", self.on_preset, instance)
            row.add_row(preset)
            self.preset_rows[instance] = preset
            self.saved.add_rows(instance, row, row.get_subtitle)
            self.band_rows[instance], self.gain_scales[instance] = [], []
        return group

    def _band_row(self, instance: str, freq: int) -> None:
        """One more band row for `instance`, its slider named for a screen reader."""
        number = len(self.band_rows[instance])
        row = plain_row(Adw.ActionRow, hertz(freq), "")
        gains = (onboard_eq.GAIN_RANGE
                 if self.model is not None and self.model.onboard(instance) else GAIN_RANGE)
        scale = spoken_scale(*gains, gain_name(instance, freq), page=1, origin=False,
                             spoken=signed)
        scale.add_mark(0, Gtk.PositionType.BOTTOM, None)
        scale.connect("value-changed", self.on_gain, instance, number)
        self._held_by_pointer(scale, gains_key(instance))
        row.add_suffix(scale)
        self.instance_rows[instance].add_row(row)
        self.band_rows[instance].append(row)
        self.gain_scales[instance].append(scale)

    # -- reading ----------------------------------------------------------

    def refresh(self) -> None:
        """Ask the daemon for the base station, unless a read is out. It answers from what it
        read and the events since, sending the base nothing. The saved presets are read
        again too, and the volumes where their rows show (VolumeRows.refresh)."""
        self.saved.refresh()
        self.volumes.refresh()
        if self._pending or self._closed:
            return
        self._pending = True
        self._worker.call({"command": "a50x"}, self._on_info)

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
        self.state_row.set_subtitle(displayable(capitalized(why)))
        self.state_row.set_tooltip_text(None)
        show_battery(self.battery_row, self.battery_icon, None, gone=True)
        # The readings were the gone headset's, as the A50 Gen 4's page has it.
        self.balance_row.set_visible(False)
        self.boom_row.set_visible(False)
        self.meter.show_headset(None)
        self.volumes.show_headset(None)
        self._show_mic_group()
        self._writable = False
        # The library's rows too, as while the headset is away: the bands were the gone one's.
        for instance in INSTANCES:
            self.saved.writable(instance, False)
        self._set_sensitive()

    def show(self, info: dict) -> None:
        """Every row from the `a50x` reply. A slider stays where it is while its send
        settles."""
        self.status_group.set_title(GLib.markup_escape_text(str(info.get("name") or TITLE)))
        model = model_of(info)
        # Away behind its receiver, as a keyboard or a mouse asleep: amber, what to do, and
        # the last readings with every row that writes off.
        answering = info.get("answering") is not False
        set_dot(self.state_dot, True if answering else ASLEEP)
        # The route, as the Keyboard tab's row; the node is in the tooltip.
        self.state_row.set_subtitle(HEADSET_ASLEEP if not answering
                                    else through(model.dongle) if model else "")
        self.state_row.set_tooltip_text(str(info.get("device") or "") or None)
        battery = info.get("battery")
        # A headset that does not answer keeps its last reading, with when it was taken.
        show_battery(self.battery_row, self.battery_icon,
                     battery if isinstance(battery, dict) else None, kept_at(info))
        settings = info.get("settings")
        self._settings = settings if isinstance(settings, dict) else {}
        balance = self._settings.get("balance")
        self.balance_row.set_visible(whole(balance))
        if whole(balance):
            self.balance_row.set_subtitle(balance_words(balance))
        live = info.get("mic_live")
        self.boom_row.set_visible(isinstance(live, bool))
        self.boom_row.set_subtitle(LIVE if live is True else MUTED)
        # Read only says a test is owed; an away headset's rows are off with it answering.
        self.read_only_row.set_visible(info.get("writes_refused") is not None)
        self._writable = info.get("writes_refused") is None and answering
        show_untested(self.untested_row, info.get("untested"))
        # Before the rows are made again for another model, which takes the meter's row out
        # and puts it back: it looks for the new model's microphone once.
        self.meter.show_headset(None if model is None else model.key)
        self.volumes.show_headset(None if model is None else model.key)
        self._show_model(model)
        self._show_mic_group()
        for key in (*self.scales, *self.spins):
            self._show_level(key)
        for key in self.choices:
            self._show_choice(key)
        for key in self.switches:
            self._show_switch(key)
        self.mix_group.set_visible(self.model is not None and self.model.mixer)
        mix = info.get("mix")
        self._show_mix(mix if isinstance(mix, dict) else None)
        equalizers, presets = info.get("equalizer"), info.get("presets")
        for instance in INSTANCES:
            read = equalizers.get(instance) if isinstance(equalizers, dict) else None
            named = presets.get(instance) if isinstance(presets, dict) else None
            self._presets[instance] = ([str(name) for name in named]
                                       if isinstance(named, list) else [])
            self._show_equalizer(instance, read if isinstance(read, dict) else None)
        self.put_back_row.set_sensitive(self._writable and bool(info.get("first")))
        self._set_sensitive()

    def _show_level(self, key: str) -> None:
        """A setting's slider and its number, or its spin row and its unit, from the reply,
        but while it is held (_held), when both stay the row's."""
        value, setting = self._settings.get(key), cast("Model", self.model).setting(key)
        low, high = setting.range
        if not whole(value) or not low <= value <= high:
            self.rows[key].set_subtitle(NOT_READ)
        elif not self._held(key):
            if key in self.spins:
                self._loading.put_value(self.spins[key], value)
                self.rows[key].set_subtitle(SPIN_HELP.get(key, ""))
            else:
                self._loading.put_value(self.scales[key], value)
                self.rows[key].set_subtitle(setting.level(value))

    def _show_mix(self, mix: dict | None) -> None:
        """Each stream mix row from the reply's mix, or Not read; a slider settling or held
        stays where it is."""
        sources = mix.get("sources") if mix else None
        held = {mixer.STREAM: {"level": mix.get("stream"), "muted": False}} if mix else {}
        held.update(sources if isinstance(sources, dict) else {})
        self._mix = {}
        for source, row in self.mix_rows.items():
            entry = held.get(source)
            level = entry.get("level") if isinstance(entry, dict) else None
            if not whole(level):
                row.set_subtitle(NOT_READ)
                continue
            muted = bool(cast("dict", entry).get("muted"))
            self._mix[source] = {"level": level, "muted": muted}
            if source in self.mutes:
                self._loading.put_active(self.mutes[source], muted)
            if self._held(mix_key(source)):
                level = round(self.mix_scales[source].get_value())
            else:
                self._loading.put_value(self.mix_scales[source], level)
            row.set_subtitle(MUTED if muted else mix_words(level))

    def _show_switch(self, key: str) -> None:
        value = self._settings.get(key)
        if value in SWITCH:
            self._loading.put_active(self.switches[key], value == SWITCH[1])
        self.switches[key].set_subtitle(SWITCH_HELP.get(key, "") if value in SWITCH
                                        else NOT_READ)

    def _show_choice(self, key: str) -> None:
        values = choice_values(cast("Model", self.model).setting(key))
        value = self._settings.get(key)
        known = isinstance(value, int | str) and value in values
        if known:
            self._loading.put_selected(self.choices[key], values.index(cast("int | str", value)))
        self.choices[key].set_subtitle("" if known else NOT_READ)

    def _show_equalizer(self, instance: str, read: dict | None) -> None:
        """An instance's preset and band rows from its read, or Not read."""
        bands = read.get("bands") if read else None
        row = self.instance_rows[instance]
        writes = self._writable and bands_written(self.model, instance)
        self.saved.show(instance, bands, writes)
        if not isinstance(bands, list) or not bands:
            row.set_subtitle(NOT_READ)
            self._equalizers.pop(instance, None)
            self.preset_rows[instance].set_visible(False)
            for band_row in self.band_rows[instance]:
                band_row.set_visible(False)
            return
        self._playing[instance] = cast("dict", read).get("preset")
        self._show_presets(instance)
        if len(self.band_rows[instance]) < len(bands):
            for band in bands[len(self.band_rows[instance]):]:
                self._band_row(instance, int(band["freq"]))
            self.saved.to_end(instance)
        gains = [float(band["gain"]) for band in bands]
        self._equalizers[instance] = gains
        settling = self._held(gains_key(instance))
        for number, (band_row, scale) in enumerate(zip(self.band_rows[instance],
                                                       self.gain_scales[instance],
                                                       strict=True)):
            shown = number < len(bands)
            band_row.set_visible(shown)
            if shown:
                freq = int(bands[number]["freq"])
                if band_row.get_title() != hertz(freq):
                    # A preset can move a band: its slider's name says its row's frequency.
                    band_row.set_title(hertz(freq))
                    scale.update_property([Gtk.AccessibleProperty.LABEL],
                                          [gain_name(instance, freq)])
                if not settling:
                    self._loading.put_value(scale, gains[number])
                    # Said as the row says it: a gain read can be a 0.05 dB step, which
                    # spoken_scale's whole number rounds, 1.55 dB to 2.
                    words = signed(gains[number])
                    band_row.set_subtitle(words)
                    scale.update_property([Gtk.AccessibleProperty.VALUE_TEXT], [words])

    def _show_presets(self, instance: str) -> None:
        """An instance's preset row: each preset its reply names under Headset, by name, then
        each of the library under Library, by number and name, then Custom, the one the
        instance plays picked, and its row's subtitle that one's name; the row hidden while it
        lists none but Custom."""
        row, names = self.preset_rows[instance], self._presets[instance]
        saved = self.saved.of(instance)
        picks = self._picks[instance] = [*names, *(number for number, _name in saved)]
        groups = ((HEADSET_GROUP, tuple(name.title() for name in names)),
                  (LIBRARY_GROUP, tuple(preset_label(number, name) for number, name in saved)),
                  (None, (CUSTOM,)))
        titles = [*groups[0][1], *(name for _number, name in saved), CUSTOM]
        playing, number = self._playing.get(instance), self.saved.playing(instance)
        index = (names.index(playing) if isinstance(playing, str) and playing in names
                 else picks.index(number) if number is not None else len(picks))
        row.set_visible(bool(picks))
        if self._groups.get(instance) != groups:
            self._groups[instance] = groups
            self._loading.put_model(row, self.preset_groups[instance].model(groups))
        self._loading.put_selected(row, index)
        self.instance_rows[instance].set_subtitle(titles[index])

    def _saved_changed(self) -> None:
        """The saved presets read or changed: each Preset row of an equalizer read lists them
        again, and the group says why the library takes no change while it does not."""
        self.eq_group.set_description(self.saved.said())
        for instance in self._equalizers:
            self._show_presets(instance)

    def _set_sensitive(self) -> None:
        """The rows that write on only while the daemon lets writes through and a value is
        known for each; the bands of an equalizer the daemon only reads stay off. A slider the
        pointer holds is let go once its row goes off, so the replies move it again."""
        # The keys of the rows that go off.
        off: set[str] = set()
        for key, row in self.rows.items():
            row.set_sensitive(on := self._writable and key in self._settings)
            if not on:
                off.add(key)
        if not self._writable:
            self.put_back_row.set_sensitive(False)
        for instance in INSTANCES:
            read = (self._writable and instance in self._equalizers
                    and bands_written(self.model, instance))
            self.preset_rows[instance].set_sensitive(read)
            for band_row in self.band_rows[instance]:
                band_row.set_sensitive(read)
            if not read:
                off.add(gains_key(instance))
        for source, row in self.mix_rows.items():
            row.set_sensitive(on := self._writable and source in self._mix)
            if not on:
                off.add(mix_key(source))
        # A slider grayed out under the pointer never sees its release; while writes go
        # through, a change its drag still owes goes at the interval's end.
        for key in off:
            if self._holds.release(key) and not self._writable:
                self._sends[key].pace.cancel()

    # -- writing ------------------------------------------------------------

    def _held_by_pointer(self, scale: Gtk.Scale, key: str) -> None:
        watch_pointer(scale, partial(self.on_press, key), partial(self.on_release, key))

    def on_press(self, key: str) -> None:
        """The pointer went down on `key`'s slider: from here it sends at the drag's pace."""
        self._holds.press(key)
        self._sends[key].settle.cancel()

    def on_release(self, key: str) -> None:
        """The pointer let go of `key`'s slider: its value is sent now, unless it is the last
        sent or the last reply's. Sent whatever the loading guard says: a release is one
        event, and a poll's rows can hold the guard when it comes, or did when the drag's
        last change came."""
        if not self._holds.release(key):
            return
        sends = self._sends[key]
        sends.pace.cancel()
        sends.now()

    def _held(self, key: str) -> bool:
        """Whether `key`'s slider stays where it is through a reply: the pointer holds it, a
        send of it waits, or a write of it is not answered yet."""
        return self._holds.held(key) or self._sends[key].pending

    def _moved(self, key: str) -> bool:
        """`key`'s slider moved: sent at the drag's pace while the pointer holds it, which no
        reply moves (_held), else once its keys settle; False for a move the page made."""
        if self._holds.pressing(key):
            self._sends[key].pace.changed()
        elif self._loading.active:
            return False
        else:
            self._sends[key].settle.restart(SETTLE_MS)
        return True

    def on_spin(self, row: Adw.SpinRow, _pspec: GObject.ParamSpec, key: str) -> None:
        # Not after the close: GTK takes a number typed in the row as the window is
        # destroyed (worker.take_typed). Nor from a row another model's rows replaced
        # (_show_model): GTK takes a number typed in it once the focus leaves it.
        if not self._closed and self.spins.get(key) is row:
            self._moved(key)

    @staticmethod
    def on_spin_output(row: Adw.SpinRow, setting: Setting) -> bool:
        """A spin row's number as it shows: Never for 0, where the setting has that word.
        True once the row's text is set, the signal's contract."""
        if round(row.get_value()) == 0 and setting.never:
            row.set_text(capitalized(setting.never))
            return True
        return False

    def on_slider(self, scale: Gtk.Scale, key: str) -> None:
        if self._moved(key):
            setting = cast("Model", self.model).setting(key)
            self.rows[key].set_subtitle(setting.level(round(scale.get_value())))

    def on_mix_level(self, scale: Gtk.Scale, source: str) -> None:
        held = self._mix.get(source)
        if self._moved(mix_key(source)) and held is not None and not held["muted"]:
            self.mix_rows[source].set_subtitle(mix_words(round(scale.get_value())))

    def on_mute(self, button: Gtk.ToggleButton, source: str) -> None:
        """Write a source's mute as its button shows it, unless it is the last sent or the last
        reply's, or the mix is not known."""
        held, muted, key = self._mix.get(source), button.get_active(), mute_key(source)
        if (not self._loading.active and held is not None
                and muted != self._sent.get(key, held["muted"])):
            self._write(key, {"command": "a50x_mix", "source": source, "muted": muted}, muted)

    def _send_mix(self, source: str) -> None:
        """Write a stream mix source's level as its slider shows it, unless it is the last
        sent or the last reply's, or the mix is not known."""
        key = mix_key(source)
        held, level = self._mix.get(source), round(self.mix_scales[source].get_value())
        if held is not None and level != self._sent.get(key, held["level"]):
            self._write(key, {"command": "a50x_mix", "source": source, "level": level}, level)

    def on_choice(self, _row: Adw.ComboRow | Adw.SwitchRow, _pspec: GObject.ParamSpec,
                  key: str) -> None:
        if not self._loading.active:
            self._send(key)

    def _send(self, key: str) -> None:
        """Write a setting's value as its row shows it, unless it is the last sent or the last
        reply's."""
        setting = cast("Model", self.model).setting(key)
        value: object
        if key in self.switches:
            value = SWITCH[self.switches[key].get_active()]
        elif key in self.choices:
            value = choice_values(setting)[self.choices[key].get_selected()]
        else:
            value = round((self.spins.get(key) or self.scales[key]).get_value())
        if value == self._sent.get(key, self._settings.get(key)):
            return
        self._write(key, {"command": "a50x_set", "setting": key, "value": value}, value)

    def _write(self, key: str, request: dict, value: object) -> None:
        """Send `request`, a write of `key`'s `value`, kept as sent until its reply comes."""
        self._sent[key] = value
        self._holds.sent(key)
        self._call(request, partial(self._written, key))

    def _written(self, key: str, reply: dict | None) -> None:
        """A write of `key` answered: once none is left, its slider follows the replies."""
        self._holds.answered(key)
        if not self._holds.answering(key):
            self._sent.pop(key, None)
        self._answered(reply)

    def on_put_back(self, row: Adw.ButtonRow) -> None:
        """Every first-read value written back once asked: it writes over each setting at
        once (the owner's decision of 2026-10-22)."""
        def put_back() -> None:
            for key in (*self.scales, *self.spins):
                self._sends[key].cancel()
            self._call({"command": "a50x_set", "reset": True}, self._answered)
        confirm(row, PUT_BACK_HEADING, PUT_BACK_BODY.format(self._balance_too), PUT_BACK,
                put_back)

    def on_preset(self, row: Adw.ComboRow, _pspec: GObject.ParamSpec, instance: str) -> None:
        """A preset picked by hand is copied into the instance's equalizer: one of the
        headset's, or one saved on this computer, whose bands go there; Custom sends
        nothing."""
        picks = self._picks.get(instance, [])
        index = row.get_selected()
        if not self._loading.active and index < len(picks):
            self._sends[gains_key(instance)].cancel()
            pick = picks[index]
            self._call(self.saved.request(pick) if isinstance(pick, int)
                       else {"command": "a50x_eq", "instance": instance, "preset": pick},
                       self._answered)

    def on_gain(self, scale: Gtk.Scale, instance: str, number: int) -> None:
        """A band moved by hand: its row says the whole dB it sends."""
        if self._moved(gains_key(instance)):
            self.band_rows[instance][number].set_subtitle(signed(round(scale.get_value())))

    def _send_gains(self, instance: str) -> None:
        """Write an instance's gains: each slider moved by hand, the others as read."""
        read = self._equalizers.get(instance)
        if read is None:
            return
        # Exact: a slider not moved holds the gain it was put to, which may be a half dB, and
        # one moved by hand lands on a whole dB, which may be that gain rounded.
        gains = [float(round(scale.get_value())) if scale.get_value() != gain
                 else gain for scale, gain in zip(self.gain_scales[instance], read,
                                                  strict=False)]
        key = gains_key(instance)
        if gains == self._sent.get(key, read):
            return
        self._write(key, {"command": "a50x_eq", "instance": instance, "gains": gains}, gains)

    def _answered(self, reply: dict | None) -> None:
        """A write's reply, the whole `a50x` reply; None, a refusal or a failed round trip the
        window showed, asks the daemon again: each row goes back, and one whose write may have
        landed shows Not read until it is read again."""
        if self._closed:
            return
        if reply is None:
            self.refresh()
        else:
            self.show(reply)

    # -- the window's ------------------------------------------------------

    def flush(self) -> bool:
        """Send what is still waiting now, a number typed in a spin row and not yet taken
        among it, for the window's close; True if any."""
        take_typed(*self.spins.values())
        # Each one sent: any() over a generator would stop at the first that was.
        flushed = [send.flush() for send in self._sends.values()]
        return self.volumes.flush() or any(flushed)

    @property
    def busy(self) -> bool:
        """An action the page runs itself, which the window's close waits for: a change of
        the saved presets or a volume set (VolumeRows); every write to the headset goes
        through `call`."""
        return self.saved.busy or self.volumes.busy

    def close(self) -> None:
        self._closed = True
        self.meter.close()
        self.volumes.close()
        for send in self._sends.values():
            send.cancel()
