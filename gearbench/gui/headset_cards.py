"""The Headset tab's groups for what a headset of the G432's family holds itself: the
equalizer in the headset, on a model with one of its own, with its preset library on this
computer (library_card's HEADSET), the lights, on one that lists them
(gearbench.headset_lights), with the switch that has them follow the keyboard that leads,
and the G keys, on one that has them, bound as the Mouse tab's buttons are (binding_rows).

Each card owns its group, its rows and their handlers. The G432's page builds it, places
the group, hands it each `g432` reply's part, and gives it the window's `call`. The
equalizer's and the lights' writes answer with the whole `g432` reply, which `answered`
takes, but the follow switch's, after which the card reads the headset again; the G keys
card reads each `bind` reply itself. While a write of a card is
out (`Writes`), a reply leaves its sliders, or its effect and color, as they are.
`band_row` is a band's row, the page's equalizer's as well as the card's.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING, cast

from gi.repository import Adw, GObject, Gtk

from ..config import format_color, parse_color
from ..g432 import HeadsetModel
from ..keyboard import followed
from ..wording import hertz, signed
from .binding_rows import binding_row, binding_words, read_pick, show_binding
from .color import ColorPicker, SavedColors
from .dialogs import CaptureDialog
from .library_card import HEADSET, LibraryCard
from .style import (
    CHANGES_WAIT,
    CUSTOM,
    EFFECT,
    FOLLOW,
    GEARBENCH_LIGHTING_SHOWN,
    LIGHTS_OWN,
    OWN_LIGHTING_SHOWN,
    PRESET,
    SYNC_HELP,
    TEST_WAIT,
    WAITS_FOR_TEST,
    confirm,
    effect_label,
    effect_tips,
    plain_row,
    preset_label,
    spoken_scale,
)
from .tips import explain_choices
from .worker import Call, DaemonWorker, Done, LoadingGuard, Settle, named_layer

if TYPE_CHECKING:                       # pragma: no cover
    from .window import Window

#: The equalizer in the headset: its group's line. The gains a slider sets play until the
#: headset powers off; a preset picked, each time it connects again (the owner's decision of
#: 2026-10-23, "Library and re-send").
HEADSET_EQ_HELP = "In the headset; a preset picked comes back each time it connects"
#: The Preset row's entry that sends 0 dB to every band and stops a preset being sent again,
#: shown while the headset plays it, as after a switch-on (the owner's decision of
#: 2026-10-25).
FLAT = "Flat"
#: The one equalizer in the headset, the headphones', as its library's rows know it.
HEADPHONES = "headphones"
#: The lights: the Color row's line, and the tooltip of the row that gives the lighting
#: back to the headset (style.LIGHTS_OWN); and the switch of the effect they show as the
#: headset switches on, G HUB's name for it.
LIGHTS_COLOR_HELP = "Sent when you let go"
LIGHTS_OWN_HELP = "The lighting the headset shows without Gearbench"
STARTUP_EFFECT = "Startup effect"
#: The row that keeps the lighting shown in the headset as its own, its ellipsis for the
#: question it opens (the HIG's writing style), and its tooltip; the question, since it writes
#: over the lighting the headset keeps, and its response.
LIGHTS_STORE = "Save as its own lighting"
LIGHTS_STORE_OPENER = f"{LIGHTS_STORE}…"
LIGHTS_STORE_HELP = "The headset keeps it through a power-off"
LIGHTS_STORE_HEADING = f"{LIGHTS_STORE}?"
LIGHTS_STORE_BODY = "The headset keeps the lighting shown now in place of its own."
LIGHTS_STORE_RESPONSE = "Save"
#: The Lighting group's line where the writes the headset keeps wait for a test: the store's
#: row is a button row, with no line of its own, and Startup effect says it on its row.
STORE_WAITS = f"{LIGHTS_STORE} waits {TEST_WAIT}"
#: A G key's binding to nothing, what a clear sends.
UNBOUND = "none"
#: A G key row's built-in actions, in config.HEADSET_ACTIONS' order (the owner's answers of
#: 2026-10-25), and one tooltip line per entry, in the rows' order: these, then
#: binding_rows.CHOICES.
G_KEY_CHOICES = [(UNBOUND, "Nothing"), ("brightness-cycle", "Brightness cycle"),
                 ("lights-toggle", "Lighting on or off"), ("eq-next", "Next equalizer preset"),
                 ("sidetone-toggle", "Sidetone on or off")]
G_KEY_TIPS = ("The key does nothing.",
              "Steps the lighting down to off, then back to full.",
              "Turns the lighting off, or back on at full.",
              "Plays the next preset of the equalizer's library.",
              "Turns sidetone off, or back on at its level.",
              "Pick a key or a combination for this key.",
              "Pick one of your macros for this key.")
G_KEY_CHANGE_HELP = "Pick another key or macro for this key"


def band_row(name: str, low: int, high: int,
             changed: Callable[[Gtk.Scale, Adw.ActionRow], None]
             ) -> tuple[Adw.ActionRow, Gtk.Scale]:
    """A band's row and its slider, named "<name> gain" for a screen reader and said in
    signed dB, with a mark at 0 dB; `changed` gets each move and the row."""
    row = plain_row(Adw.ActionRow, name, "")
    scale = spoken_scale(low, high, f"{name} gain", page=1, origin=False,
                         spoken=signed)
    scale.add_mark(0, Gtk.PositionType.BOTTOM, None)
    scale.connect("value-changed", changed, row)
    row.add_suffix(scale)
    return row, scale


class Writes:
    """Writes through the window's `call`, each reply to `answered`, and how many are out:
    the worker answers a read queued before a write first, and that read's reply gives what
    the write replaces."""

    def __init__(self, call: Call, answered: Done) -> None:
        self._call = call
        self._answered = answered
        self.out = 0

    def send(self, request: dict) -> None:
        self.out += 1
        self._call(request, self._took)

    def _took(self, reply: dict | None) -> None:
        self.out -= 1
        self._answered(reply)


class HeadsetEqCard:
    """The equalizer in the headset: its Preset row, which lists the model's library, Flat
    and Custom, the library's rows (LibraryCard, `saved`), and a row a band, built from the
    reply's bands. `settle_ms` is the page's wait after the last move before a send; `worker`
    reads and saves the library."""

    def __init__(self, worker: DaemonWorker, toast: Callable[..., object],
                 loading: LoadingGuard, call: Call, answered: Done, settle_ms: int) -> None:
        self._loading = loading
        self._writes = Writes(call, answered)
        self._settle_ms = settle_ms
        self._send = Settle(self._send_gains)
        #: The bands and the range in dB the rows were built for, and the gains the last reply
        #: gave or the last send asked for, so a send of the same skips; whether the last
        #: reply's gains are 0 dB on every band; and what the Preset row lists before Flat,
        #: each a library preset's number, and its entries' names and labels.
        self._built: tuple[tuple[int, ...], int, int] = ((), 0, 0)
        self._gains: list[int] = []
        self._flat = False
        self._picks: list[int] = []
        self._names: list[str] = []
        self._labels: list[str] = []
        self.group = Adw.PreferencesGroup(title="Equalizer", description=HEADSET_EQ_HELP,
                                          visible=False)
        # The name as the subtitle, as the Keyboard tab's Active: a preset's name is one
        # people type, and the inline value is cut at 20 characters.
        self.preset_row = Adw.ComboRow(title=PRESET, use_subtitle=True,
                                       model=Gtk.StringList.new([]), visible=False)
        self.preset_row.connect("notify::selected", self.on_preset)
        self.group.add(self.preset_row)
        #: Why the last reply refuses a write, None while none is.
        self._refused: object = None
        self.saved = LibraryCard(worker, toast, self._saved_changed, HEADSET)
        self.saved.add_rows(HEADPHONES, self.group, self._shown)
        self.rows: list[Adw.ActionRow] = []
        self.scales: list[Gtk.Scale] = []

    @property
    def busy(self) -> bool:
        """A change of the library is out, which the window's close waits for."""
        return self.saved.busy

    def refresh(self) -> None:
        """The library of the model shown read again, at each of the page's reads."""
        self.saved.refresh()

    def set_usable(self, usable: bool) -> None:
        """The group on while the headset answers."""
        self.group.set_sensitive(usable)

    def show(self, eq: object, model: HeadsetModel | None) -> None:
        """The group from the reply's `equalizer` of `model`, hidden without one: the rows
        built again for bands or a range of their own, which drops a send still settling,
        each slider at the gain the headset plays, unless a send settles or a write is out,
        the Preset row and the library's rows for those gains, and every row insensitive with
        CHANGES_WAIT as the group's line while a write is refused."""
        self.group.set_visible(isinstance(eq, dict))
        self.saved.model_shown(model if isinstance(eq, dict) else None)
        if not isinstance(eq, dict):
            return
        bands = tuple(eq.get("frequencies") or ())
        low, high = eq.get("range") or (0, 0)
        if (bands, low, high) != self._built:
            # The new sliders hold no gain the user moved: they start at the range's floor.
            self._send.cancel()
            self._built = (bands, low, high)
            self._build(bands, low, high)
        gains = eq.get("active") or []
        if not self._send.pending and not self._writes.out and len(gains) == len(bands):
            self._gains = list(gains)
            for row, scale, gain in zip(self.rows, self.scales, gains, strict=True):
                self._loading.put_value(scale, gain)
                row.set_subtitle(signed(gain))
        refused = self._refused = eq.get("refused")
        for widget in (self.preset_row, *self.rows):
            widget.set_sensitive(not refused)
        played = ([{"freq": freq, "gain": gain} for freq, gain in zip(bands, gains, strict=True)]
                  if len(gains) == len(bands) else None)
        self._flat = not any(gains)
        self.saved.show(HEADPHONES, played, not refused)
        self._saved_changed()

    def _saved_changed(self) -> None:
        """The Preset row (_show_presets) and the group's line: CHANGES_WAIT while a write is
        refused, else why the library takes no change (LibraryCard.said), else
        HEADSET_EQ_HELP."""
        self.group.set_description(CHANGES_WAIT if self._refused
                                   else self.saved.said() or HEADSET_EQ_HELP)
        self._show_presets()

    def _show_presets(self) -> None:
        """The Preset row: the library's presets, by number and name, then Flat, then Custom
        while the headset plays none of them, which names its gains and picks nothing
        (on_preset); the one it plays picked, a library preset of flat gains before Flat.
        Hidden while no bands show."""
        listed = self.saved.of(HEADPHONES)
        self._picks = [number for number, _name in listed]
        playing = self.saved.playing(HEADPHONES)
        custom = [CUSTOM] if playing is None and not self._flat else []
        self._names = [*(name for _number, name in listed), FLAT, *custom]
        labels = [*(preset_label(number, name) for number, name in listed), FLAT, *custom]
        if labels != self._labels:
            self._labels = labels
            self._loading.put_model(self.preset_row, Gtk.StringList.new(labels))
        self._loading.put_selected(self.preset_row, len(labels) - 1 if playing is None
                                   else self._picks.index(playing))
        self.preset_row.set_visible(bool(self._built[0]))

    def _shown(self) -> str:
        """What the Preset row says the headset plays, the name Save as new starts from."""
        index = self.preset_row.get_selected()
        return self._names[index] if index < len(self._names) else CUSTOM

    def on_preset(self, row: Adw.ComboRow, _pspec: GObject.ParamSpec) -> None:
        """A library preset picked by hand goes to the headset, its gains sent and the
        preset kept by the daemon to send again (g432_library); Flat sends 0 dB to every band
        and forgets the preset picked. Custom is listed only while it shows, so picking it
        changes nothing."""
        index = row.get_selected()
        if self._loading.active or index > len(self._picks):
            return
        self._send.cancel()
        self._writes.send(self.saved.request(self._picks[index]) if index < len(self._picks)
                          else {"command": "g432_headset_eq", "flat": True})

    def _build(self, bands: tuple[int, ...], low: int, high: int) -> None:
        for row in self.rows:
            self.group.remove(row)
        self.rows, self.scales = [], []
        for freq in bands:
            row, scale = band_row(hertz(freq), low, high, self.on_gain)
            self.group.add(row)
            self.rows.append(row)
            self.scales.append(scale)
        # The library's Save as new and Delete preset after the bands.
        self.saved.to_end(HEADPHONES)

    def on_gain(self, scale: Gtk.Scale, row: Adw.ActionRow) -> None:
        row.set_subtitle(signed(round(scale.get_value())))
        if not self._loading.active:
            self._send.restart(self._settle_ms)

    def _send_gains(self) -> None:
        """The sliders' gains to the headset, unless they are the last reply's or send's."""
        gains = [round(scale.get_value()) for scale in self.scales]
        if gains != self._gains:
            self._gains = gains
            self._writes.send({"command": "g432_headset_eq", "gains": gains})

    def flush(self) -> bool:
        return self._send.flush()

    def cancel(self) -> None:
        self._send.cancel()


class LightsCard:
    """The lights: the switch that has them follow the keyboard that leads, as the Mouse tab's
    has a mouse, the effect and the color every zone takes, the row that gives them back to
    the headset, the row that keeps them in it, and the switch of their startup effect. A
    color goes as the picker is let go, with the effect picked. `parent` is the window, whose
    focus says a color is being typed and whose saved colors the picker's Saved colors row
    lists; `reread` reads the headset again, for the follow switch, whose reply is not the
    `g432` reply; `kb_base` gives the color the lights take from the keyboard while they
    follow it, which the picker shows then (Window.kb_base)."""

    def __init__(self, parent: Window, loading: LoadingGuard, call: Call,
                 answered: Done, toast: Callable[..., object],
                 reread: Callable[[], object], kb_base: Callable[[], int]) -> None:
        self._parent = parent
        self._loading = loading
        self._call = call
        self._reread = reread
        self._kb_base = kb_base
        self._writes = Writes(call, answered)
        #: The effects the Effect row lists, by name, in its order.
        self._effects: list[str] = []
        #: How many follow switches are out, whose read puts the switch as the daemon has it;
        #: and the last reply's lights and flash_refused, which a flip of it shows again.
        self._sync_out = 0
        self._shown: tuple[dict, object] = ({}, None)
        self.group = Adw.PreferencesGroup(title="Lighting", visible=False)
        self.sync_row = Adw.SwitchRow(title=FOLLOW.format(followed(None)), subtitle=SYNC_HELP)
        self.sync_row.connect("notify::active", self.on_sync)
        self.group.add(self.sync_row)
        self.effect_row = Adw.ComboRow(title=EFFECT, model=Gtk.StringList.new([]))
        #: Each effect's tooltip, changed in place with the list (explain_choices).
        self._effect_tips: list[str] = []
        explain_choices(self.effect_row, self._effect_tips)
        self.effect_row.connect("notify::selected", self.on_effect)
        self.group.add(self.effect_row)
        self.picker = ColorPicker(lambda _color: None, self._send, toast,
                                  subtitle=LIGHTS_COLOR_HELP)
        #: The Saved colors row under the picker, which Window.rebuild_saved fills.
        self.saved = SavedColors(self.picker, parent)
        self.group.add(self.picker.row)
        # No icon: it hands the lights over and undoes nothing, where edit-undo-symbolic is
        # every row's that puts values back.
        self.own_row = Adw.ButtonRow(title=LIGHTS_OWN, tooltip_text=LIGHTS_OWN_HELP)
        self.own_row.connect("activated", self.on_own)
        self.group.add(self.own_row)
        self.store_row = Adw.ButtonRow(title=LIGHTS_STORE_OPENER, tooltip_text=LIGHTS_STORE_HELP,
                                       start_icon_name="document-save-symbolic")
        self.store_row.connect("activated", self.on_store)
        self.group.add(self.store_row)
        self.startup_row = Adw.SwitchRow(title=STARTUP_EFFECT, visible=False)
        self.startup_row.connect("notify::active", self.on_startup)
        self.group.add(self.startup_row)

    def show(self, lights: object, flash_refused: object = None) -> None:
        """The group from the reply's `lights`, hidden without them: the follow switch as the
        reply's `synced` gives it unless one is out, the effects, the one Gearbench last wrote
        picked unless a write is out, and on the picker, unless a color is being picked or
        typed, the keyboard's color while the lights follow it, else the color that effect
        was written with unless a write is out; every row but the follow switch insensitive
        with CHANGES_WAIT as the group's line while the headset's writes are refused, else
        STORE_WAITS where the reply's `flash_refused` refuses the writes the headset keeps,
        else whose lighting it shows, in the Mouse tab's words; Show its own lighting only
        while Gearbench holds the lights. Save as its own lighting only while Gearbench shows
        lighting, and Startup effect where the lights list one, on or off as read unless a
        write is out; both insensitive too where `flash_refused` refuses them, Startup effect
        saying WAITS_FOR_TEST on its row. While the lights follow
        the keyboard, the Effect and Color rows, Show its own lighting and Save as its own
        lighting are insensitive, as the Mouse tab's own color rows are."""
        self.group.set_visible(isinstance(lights, dict))
        if not isinstance(lights, dict):
            return
        self._shown = (lights, flash_refused)
        if not self._sync_out:
            self._loading.put_active(self.sync_row, lights.get("synced") is True)
        synced = self.sync_row.get_active()
        self._show_kept(lights, flash_refused, synced)
        effects = [name for name in lights.get("effects") or () if isinstance(name, str)]
        if effects != self._effects:
            self._effects = effects
            self._effect_tips[:] = effect_tips(effects)
            self._loading.put_model(self.effect_row,
                                    Gtk.StringList.new([effect_label(name) for name in effects]))
        shown = lights.get("shown")
        # A reply read before a write still out gives what that write replaces.
        shown = shown if isinstance(shown, dict) and not self._writes.out else {}
        if shown.get("effect") in effects:
            self._loading.put_selected(self.effect_row, effects.index(shown["effect"]))
        if synced:
            self._take_color(self._kb_base())
        elif isinstance(shown.get("color"), str):
            self._take_color(parse_color(shown["color"], "lights color"))
        refused = lights.get("refused")
        claimed = lights.get("claimed") is True
        self.group.set_description(
            CHANGES_WAIT if refused else STORE_WAITS if flash_refused
            else GEARBENCH_LIGHTING_SHOWN if claimed else OWN_LIGHTING_SHOWN)
        own = not refused and not synced
        for row in (self.effect_row, self.picker.row):
            row.set_sensitive(own)
        self.own_row.set_sensitive(own and claimed)

    def _take_color(self, color: int) -> None:
        """`color` on the picker, but not under a drag, a run of keys or a color being typed,
        as on the Mouse tab: the 5 s poll gives the color last written, which the release
        would then send."""
        if not self.picker.dragging and not self.picker.typing(self._parent):
            self.picker.set_color(color)

    def set_usable(self, usable: bool) -> None:
        """The group on while the headset answers."""
        self.group.set_sensitive(usable)

    def take_leader(self, leader: str | None) -> None:
        """The follow switch named for the keyboard that leads, by its key, as the Mouse
        tab's is."""
        self.sync_row.set_title(FOLLOW.format(followed(leader)))

    def follow_keyboard(self) -> None:
        """While the lights follow the keyboard, its color on the picker (Window.kb_base)."""
        if self.sync_row.get_active():
            self._take_color(self._kb_base())

    def on_sync(self, row: Adw.SwitchRow, _pspec: GObject.ParamSpec) -> None:
        """Send the follow switch, every click, the rows grayed or not at once; then read the
        headset again, whose reply puts the switch back where the daemon refused it."""
        if self._loading.active:
            return
        self._sync_out += 1
        self.show(*self._shown)

        def done(_reply: dict | None) -> None:
            self._sync_out -= 1
            self._reread()

        self._call({"command": "headset_sync", "value": row.get_active()}, done)

    def _show_kept(self, lights: dict, flash_refused: object, synced: bool) -> None:
        """The rows that write what the headset keeps: Save as its own lighting, refused
        while the lights follow the keyboard (`synced`), and Startup effect."""
        startup = lights.get("startup")
        self.startup_row.set_visible(isinstance(startup, bool))
        if isinstance(startup, bool) and not self._writes.out:
            self._loading.put_active(self.startup_row, startup)
        usable = not lights.get("refused") and not flash_refused
        self.startup_row.set_subtitle(WAITS_FOR_TEST if flash_refused else "")
        self.startup_row.set_sensitive(usable)
        self.store_row.set_sensitive(usable and not synced
                                     and isinstance(lights.get("shown"), dict))

    def on_startup(self, row: Adw.SwitchRow, _pspec: GObject.ParamSpec) -> None:
        if not self._loading.active:
            self._writes.send({"command": "g432_lights", "startup": row.get_active()})

    def on_effect(self, _row: Adw.ComboRow, _pspec: GObject.ParamSpec) -> None:
        if not self._loading.active:
            self._send(self.picker.color)

    def _send(self, color: int) -> None:
        """The effect the Effect row picks, with `color`, to every zone."""
        index = self.effect_row.get_selected()
        if index < len(self._effects):
            self._writes.send({"command": "g432_lights", "effect": self._effects[index],
                               "color": format_color(color)})

    def on_own(self, _row: Adw.ButtonRow) -> None:
        self._writes.send({"command": "g432_lights", "release": True})

    def on_store(self, row: Adw.ButtonRow) -> None:
        """The lighting shown kept in the headset as its own once asked: it writes over the
        lighting the headset keeps, as a write to a mouse's profile 1 asks first."""
        confirm(row, LIGHTS_STORE_HEADING, LIGHTS_STORE_BODY, LIGHTS_STORE_RESPONSE,
                lambda: self._writes.send({"command": "g432_lights", "store": True}),
                destructive=False)

    def flush(self) -> bool:
        """Send a run of keys on the picker still settling, for the window's close."""
        return self.picker.flush()

    def cancel(self) -> None:
        """Drop a run of keys still settling and take the picker's rules off the display,
        which holds them past the widget."""
        self.picker.destroy()


def key_label(key: str) -> str:
    """A headset's G key as its earcup names it: G1 for G933-G1."""
    return key.rpartition("-")[2]


class HeadsetKeysCard:
    """A headset's G keys, a row each as the Mouse tab's buttons (binding_rows), from the
    reply's `g_keys`, by the names they bind under: a built-in action, a keystroke or a
    macro, picked in the capture dialog, bound through `bind` on the layer the window edits,
    as Editing says: an action picked on the one the window's `call` names at the send, a
    key or a macro captured on the one the window was on when the dialog opened. `parent`
    is the window, which the capture dialog opens over."""

    def __init__(self, parent: Window, worker: DaemonWorker, loading: LoadingGuard,
                 call: Call, toast: Callable[..., object]) -> None:
        self._parent = parent
        self._worker = worker
        self._loading = loading
        self._call = call
        self._toast = toast
        #: What each key does now, as the last reply or bind gave it, and how many binds are
        #: out: a reply read before one gives what it replaces.
        self._bindings: dict[str, str] = {}
        self._out = 0
        self.rows: dict[str, Adw.ComboRow] = {}
        self.change_buttons: dict[str, Gtk.Button] = {}
        self.group = Adw.PreferencesGroup(title="G keys", visible=False)
        # Which layer a binding is written to, as the Mouse tab says it. Not markup.
        self.editing_row = Adw.ActionRow(title="Editing", use_markup=False)
        self.group.add(self.editing_row)

    def set_editing(self, text: str) -> None:
        """Say which layer the bindings are written to."""
        self.editing_row.set_title(text)

    def show(self, keys: object) -> None:
        """A row for each key of the reply's `g_keys`, put on what it does now unless a bind
        is out; the group hidden on a headset with none, and with no headset."""
        keys = keys if isinstance(keys, dict) else {}
        self.group.set_visible(bool(keys))
        for key in keys:
            if key not in self.rows:
                row, self.change_buttons[key] = binding_row(
                    key_label(key), G_KEY_CHOICES, G_KEY_TIPS, G_KEY_CHANGE_HELP,
                    partial(self.on_change, key))
                row.connect("notify::selected", self.on_binding, key)
                self.rows[key] = row
                self.group.add(row)
        for key, row in self.rows.items():
            row.set_visible(key in keys)
        if self._out:
            return
        self._bindings = {key: str(spec) for key, spec in keys.items()}
        for key in self._bindings:
            self._show_held(key)

    def _show_held(self, key: str) -> None:
        show_binding(self._loading, self.rows[key], self.change_buttons[key],
                     self._bindings.get(key, UNBOUND), G_KEY_CHOICES)

    def on_binding(self, row: Adw.ComboRow, _pspec: GObject.ParamSpec, key: str) -> None:
        if self._loading.active:
            return
        action, macros = read_pick(row, G_KEY_CHOICES)
        if action is not None:
            self._bind(key, action)
            return
        self._capture(key, macros=macros)

    def on_change(self, key: str) -> None:
        """Change… on a row bound to a key or a macro, opened on the macro list for a
        macro."""
        self._capture(key, macros=read_pick(self.rows[key], G_KEY_CHOICES)[1])

    def _capture(self, key: str, macros: bool) -> None:
        """Ask for a keystroke or a macro for `key`, and bind what comes back on the layer the
        window was on when the dialog opened: the polls go on while it is open, and an M key,
        the tray or a program can move the window's meanwhile. The dialog has no Unbind:
        Nothing in the row clears a key. Closed with nothing picked, the row goes back to
        what the key does."""
        dialog = CaptureDialog(self._parent, key_label(key), worker=self._worker,
                               can_unbind=False)
        picked = False
        layer = named_layer(self._parent.layer)

        def done(spec: str | None) -> None:
            nonlocal picked
            picked = True
            # Set, a key or a macro picked: with no Unbind, the dialog hands on no None.
            self._bind(key, cast("str", spec), layer)

        def closed(_dialog: Adw.Dialog) -> None:
            if not picked:
                self._show_held(key)
        dialog.run(done, macros=macros)
        dialog.connect("closed", closed)

    def _bind(self, key: str, spec: str, layer: dict | None = None) -> None:
        """Bind `key` to `spec` on `layer`, where given, else on the one the window's `call`
        names at the send; the row takes the reply's binding, or goes back on a refusal, a
        moved layer's included, now rather than at the next poll, and a toast says it, as the
        Keyboard and Mouse tabs' do: unbound for a clear, and Undo, on the same layer,
        whenever the bind replaced or cleared what the key did."""
        self._out += 1
        # The layer an Undo binds on: the one this bind is sent for.
        named = layer if layer is not None else named_layer(self._parent.layer)

        def done(reply: dict | None) -> None:
            self._out -= 1
            if reply is not None:
                was, bound = self._bindings.get(key), str(reply["binding"])
                self._bindings[key] = bound
                label = key_label(key)
                self._toast(f"{label} unbound" if spec == UNBOUND
                            else f"{label} → {binding_words(bound, G_KEY_CHOICES)}",
                            undo=(lambda: self._bind(key, was, named))
                            if was not in (None, UNBOUND) and was != bound else None)
            self._show_held(key)
        request = {"command": "bind", "key": key, "value": spec}
        self._call(request if layer is None else {**request, "layer": layer}, done)
