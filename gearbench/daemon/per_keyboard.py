"""The lighting and the brightness in force, by keyboard model, and the leader: which entry of
the layer in force shows on each keyboard, the entry a lighting change edits, and each model's
brightness. config.json keeps both by model (keyboard.model_key), a game profile its lighting.

The daemon drives every keyboard that answers (Keyboards). One leads (`leader`): the model
chosen (config.sync_keyboard) while it is driven, else the driven one set up first since the
start; with none driven, the one chosen or set up first. Each other keyboard follows it while
its follow switch is on (`follows`): it shows the leader's lighting, cut to what it can show,
at the leader's brightness (the owner's decisions of 2026-10-21, "B: separate pages, follow
switch" and "The leader's"). The mice follow the leader (`shown_model`, through
`Keyboard.shown_color` and `Keyboard.at_level`). Before any keyboard's node has been found,
answering or not, a lighting or brightness change is refused (NO_KEYBOARD_YET). A model with no
lighting or brightness of its own starts from the leader's or, where the leader has none,
another model's (keyboard.source_entry), cut to what it can show (keyboard.shown_lighting,
shown_brightness), and attaching it saves no copy: its first change saves that copy, with the
change, for it alone."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ..config import PER_KEY, Lighting, layer_color
from ..keyboard import (
    MODEL_KEYS,
    find_attached_keyboard,
    keep_entry,
    model_ids,
    model_key,
    model_named,
    own_entry,
    shown_brightness,
    shown_lighting,
)
from ..keyboard_zones import takes_brightness, takes_paint
from ..lighting import ANIMATIONS, offered_effects
from ..profiles import layer_in_force
from ..settings_files import ConfigError
from .shared import (
    keyboard_model_named,
    scaled,
    switch_value,
    with_note,
)

if TYPE_CHECKING:
    from pathlib import Path

    from . import Daemon
    from .keyboard import Keyboard

#: The refusal of a lighting or brightness change made before any keyboard was attached.
NO_KEYBOARD_YET = ("no keyboard has been connected since the daemon started, and each "
                   "keyboard model keeps its own lighting and brightness: connect a keyboard "
                   "first")


class PerKeyboard:
    """The lighting and brightness each keyboard shows, and the leader."""

    def __init__(self, daemon: Daemon) -> None:
        self.d = daemon
        #: The models of the keyboards set up since the start, in the order Keyboard.set_up
        #: found them answering (`found`), which `leader` goes by.
        self.attached: dict[str, None] = {}
        #: The level Paint.write_brightness last left a keyboard of each model at, sent or held
        #: already, by its key (Keyboard.model), in memory only: Paint.read_brightness does not
        #: take it for one set with the keyboard's own key, such as the leader's level on a
        #: keyboard that followed and now leads (the owner's decision of 2026-10-26, "Yes, while
        #: running").
        self.written_brightness: dict[str | None, int] = {}

    def commands(self) -> dict[str, Callable[[dict], dict]]:
        return {"sync_keyboard": self._cmd_sync_keyboard,
                "follow_leader": self._cmd_follow_leader}

    def found(self, product: int) -> None:
        """Keep the model of keyboard `product`, which Keyboard.set_up found answering, among
        those set up since the start."""
        self.attached.setdefault(model_key(product))

    def chosen(self) -> str | None:
        """The keyboard model chosen to lead (config.sync_keyboard), by its key (model_ids): one
        written by hand under another of the model's ids, the G915 TKL's c545, is the model's,
        as a lighting entry is. None for none chosen."""
        chosen = self.d.config.sync_keyboard
        return None if chosen is None else model_ids(chosen)[0]

    def leader(self) -> str | None:
        """The leader in force: the model chosen (`chosen`) while it is driven, else the first
        driven (Keyboards.of_model) in the order set up since the start (`attached`); with none
        driven, the model chosen, else the first set up since the start. The owner's decisions of
        2026-10-17 and 2026-10-20: "falling back to the other attached keyboard when absent and
        the first attached until one is picked"."""
        chosen = self.chosen()
        driven = [model for model in self.attached if self.d.keyboards.of_model(model) is not None]
        if not driven:
            return chosen or next(iter(self.attached), None)
        return chosen if chosen in driven else driven[0]

    def follows(self, model: str | None) -> bool:
        """Whether a keyboard of `model` shows the leader's lighting at the leader's
        brightness in place of its own: one that does not lead, while its follow switch is on
        (`follow_switch`)."""
        leader = self.leader()
        return (model is not None and leader is not None and model != leader
                and self.follow_switch(model))

    def follow_switch(self, model: str) -> bool:
        """The follow switch of keyboard model `model` (config.follow_leader), under any of its
        ids: on unless it is set off, the owner's decision of 2026-10-21 ("on by
        default")."""
        return own_entry(self.d.config.follow_leader, model) is not False

    def shown_model(self, keyboard: Keyboard) -> str | None:
        """The model `keyboard` shows the lighting of, before any follow: its own, or with no
        keyboard driven, for the leader's controller (Keyboards.leading), the leader's, which
        the mice show. With no leader, as before any keyboard has answered, its own: one found
        switched off at the start takes a change for its model (check_model)."""
        if keyboard.dev is None and keyboard.leads():
            return self.leader() or keyboard.model()
        return keyboard.model()

    def find_keyboard(self) -> tuple[Path, int, int] | None:
        """The node of the keyboard Keyboards connects first (keyboard.find_attached_keyboard),
        the leader's model first. From sysfs; nothing is opened."""
        return find_attached_keyboard(leader=self.leader())

    def check_model(self, keyboard: Keyboard) -> str:
        """The model of `keyboard`, or the refusal of a change made before any keyboard was
        attached."""
        model = keyboard.model()
        if model is None:
            raise ConfigError(NO_KEYBOARD_YET)
        return model

    def entries(self) -> dict[str, Lighting]:
        """The lighting of the layer in force, by keyboard model: the loaded game profile's,
        else the active M profile's."""
        return layer_in_force(self.d.games.overlay, self.d.config.profile).lighting

    def shown(self, entries: dict[str, Lighting] | None = None,
              keyboard: Keyboard | None = None) -> Lighting:
        """What `entries`, the layer in force's by default, show on `keyboard`, the leader's
        by default (shown_on)."""
        keyboard = self.d.keyboard if keyboard is None else keyboard
        return self.shown_on(keyboard.model(), entries, keyboard=keyboard)

    def shown_on(self, model: str | None, entries: dict[str, Lighting] | None = None, *,
                 keyboard: Keyboard) -> Lighting:
        """What `entries`, the layer in force's by default, show on keyboard model `model`: its
        own, or copy first's, or while it follows the leader's (`follows`), cut to the effects
        `keyboard` reports, to whether it shows painted keys, key by key or by area
        (takes_paint), and to its board. Not for a change: a copy is in no profile."""
        lighting = keyboard.lighting
        effects = None if lighting is None else offered_effects(lighting.effect_clusters)
        per_key = None if lighting is None else takes_paint(lighting)
        return shown_lighting(self.entries() if entries is None else entries, model,
                              self.leader(), effects, per_key, self.follows(model))[0]

    def edited(self, keyboard: Keyboard) -> tuple[Lighting, Callable[[], None]]:
        """The lighting a lighting change for `keyboard` edits: the layer in force's own for
        its model, made from what it shows (shown) where it has none; and what drops one made
        here, for a change whose save is refused, so that no later save writes it. Refused
        before any keyboard was attached (check_model)."""
        entries, model = self.entries(), self.check_model(keyboard)
        own = own_entry(entries, model)
        if own is not None:
            # Under the model's key: one under its other id, written by hand, moves there.
            keep_entry(entries, model, own)
            return own, lambda: None
        # check_model gives a model key, so shown cuts a copy (keyboard.cut_lighting) that
        # shares nothing with the source's entry.
        made = self.shown(entries, keyboard)
        keep_entry(entries, model, made)

        def drop() -> None:
            entries.pop(model, None)

        return made, drop

    def active_lighting(self, keyboard: Keyboard
                        ) -> tuple[str, int, dict[str, int], dict[str, int]]:
        """Effect, color, per-key colors and effect params of the layer in force, the overlay
        first, as `keyboard` shows it (shown_model, shown_on). The color is the background in
        per-key mode without an animation, the Static color otherwise. An animation the
        keyboard does not report shows static (Paint.shows_static), as copy first's cut makes
        one (keyboard.cut_lighting)."""
        layer = self.shown_on(self.shown_model(keyboard), keyboard=keyboard)
        effect = "static" if keyboard.paint.shows_static(layer.effect) else layer.effect
        shows_keys = layer.mode == PER_KEY and effect not in ANIMATIONS
        per_key = dict(layer.keys) if shows_keys else {}
        return (layer.effect, layer_color(effect, layer.mode, layer.color, layer.background),
                per_key, layer.settings())

    def brightness_in_force(self, keyboard: Keyboard | None) -> tuple[int, bool]:
        """The brightness `keyboard`, the leader's for None, shows (shown_model), and
        whether it is its model's own: else the one copy first gives it, or while it follows,
        the leader's (keyboard.shown_brightness)."""
        model = self.shown_model(self.d.keyboard if keyboard is None else keyboard)
        return shown_brightness(self.d.config.brightness, model, self.leader(),
                                self.follows(model))

    def brightness(self, keyboard: Keyboard | None = None) -> int:
        """The brightness `keyboard`, the leader's by default, shows (brightness_in_force)."""
        return self.brightness_in_force(keyboard)[0]

    def model_brightness(self, keyboard: Keyboard) -> int:
        """The brightness of `keyboard`'s model, which `status` gives beside that model's
        lighting (`shown`): its own, else the one copy first gives it, or while it follows, the
        leader's (keyboard.shown_brightness). With none driven, `brightness` gives the leader's
        controller the leader's level in its place, which the mice show."""
        model = keyboard.model()
        return shown_brightness(self.d.config.brightness, model, self.leader(),
                                self.follows(model))[0]

    def view(self, keyboard: Keyboard) -> tuple[Lighting, int]:
        """The lighting and the brightness `keyboard` shows (shown_model), which a change
        compares before and after to tell whether to repaint it."""
        return (self.shown_on(self.shown_model(keyboard), keyboard=keyboard),
                self.brightness(keyboard))

    def at_brightness(self, color: int) -> int:
        """`color` at the brightness of the leader's keyboard (shared.scaled): the keyboard's
        color as a mouse takes it, which has no brightness of its own. Whole where the leader's
        keyboard takes no brightness (keyboard_zones.takes_brightness)."""
        model = model_named(self.leader())
        if takes_brightness(self.d.keyboard.lighting, model is not None and model.zone_lighting):
            return scaled(color, self.brightness())
        return color

    def with_brightness(self, level: int, keyboard: Keyboard) -> dict[str, int]:
        """config.json's brightness with `level` for the model of `keyboard`; refused before
        any keyboard was attached (check_model)."""
        levels = dict(self.d.config.brightness)
        keep_entry(levels, self.check_model(keyboard), level)
        return levels

    def keep_brightness(self, level: int, keyboard: Keyboard) -> Keyboard:
        """Keep `level`, which `keyboard` shows already, as the level it shows: the leader's
        while it follows (`follows`), else its own model's, in config.json, saved through
        persist: a file that cannot be saved keeps it live with a warning. The answer is the
        keyboard whose model's level it is."""
        kept = self.d.keyboard if self.follows(keyboard.model()) else keyboard
        self.d.config.brightness = self.with_brightness(level, kept)
        self.d.persist()
        return kept

    def known(self) -> list[str]:
        """The keyboard models Gearbench knows of, in MODEL_OF's order: those config.json keeps
        lighting or brightness for, those the loaded game profile keeps lighting for, those
        set up since the start, and the one chosen to lead."""
        overlay = self.d.games.overlay
        kept = {*self.d.config.brightness, *(model for profile in self.d.config.profiles.values()
                                             for model in profile.lighting),
                *(overlay.lighting if overlay is not None else ()), *self.attached, self.chosen()}
        return [key for key in MODEL_KEYS if kept & set(model_ids(key))]

    def status(self, keyboard: Keyboard) -> dict:
        """What `status` gives of the models, by key: that of `keyboard`, the one status gives
        the entries of (Keyboards.shown_in), its follow switch and whether it follows, the one
        chosen to lead (`chosen`) or None, the leader in force and those known (`known`)."""
        model = keyboard.model()
        return {"lighting_model": model, "sync_keyboard": self.chosen(),
                "follow_leader": model is None or self.follow_switch(model),
                "follows": self.follows(model),
                "sync_leader": self.leader(), "keyboard_models": self.known()}

    def _cmd_sync_keyboard(self, request: dict) -> dict:
        """Choose the keyboard model that leads (`leader`), by any of its ids or its name, or
        with null none; saved in config.json under the model's key. Each keyboard driven whose
        lighting or brightness the choice changes (`view`), or with none driven the new
        leader's controller where what the mice show changes, is woken and painted, as at a
        profile switch, the leader's first; the reply's note is the leader's apply's. A new
        leader not painted gives the mice its color all the same."""
        value = request["value"]
        chosen = (None if value is None
                  else keyboard_model_named(value, "sync_keyboard"))
        led, driven = self.d.keyboard, self.d.keyboards.driven()
        was = {keyboard: self.view(keyboard) for keyboard in driven or [led]}
        self.d.save_setting(sync_keyboard=chosen)
        reply = {"ok": True, "sync_keyboard": chosen, "sync_leader": self.leader()}
        leading, failed, painted = self.d.keyboard, None, False
        if not driven:
            # With none driven, the mice show the leader's controller's view, and the choice can
            # hand the lead to another controller: that one is told by what the mice showed.
            was = {leading: was[led]}
        for keyboard in sorted(was, key=lambda keyboard: keyboard is not leading):
            if self.view(keyboard) == was[keyboard]:
                continue
            keyboard.idle.wake_for_change()
            note = keyboard.apply_profile()
            if keyboard is leading:
                # With none driven, a leader never set up since the start has no controller of
                # its own, and the one standing in for it would say its own model is away.
                failed = note if keyboard.model() == self.leader() else None
                painted = True
        if leading is not led and not painted:
            # A follower's apply gives the mice nothing (Keyboard.sync_devices), and a new leader
            # whose view did not change was not painted.
            leading.sync_devices(leading.shown_color())
        return with_note(reply, failed, leading.last_apply_detail)

    def _cmd_follow_leader(self, request: dict) -> dict:
        """The follow switch of the keyboard model `keyboard` names, by any of its ids or its
        name, on or off: on, while another keyboard leads, it shows the leader's lighting at the
        leader's brightness; off, its own (the owner's decision of 2026-10-21, "B: separate
        pages, follow switch"). Saved in config.json under the model's key, for a model not
        attached too. The keyboard of that model driven is woken and painted where the switch
        changes what it shows (`view`), with its apply's note."""
        model = keyboard_model_named(request["keyboard"], "keyboard")
        value = switch_value(request.get("value"), "follow_leader")
        keyboard = self.d.keyboards.of_model(model)
        was = None if keyboard is None else self.view(keyboard)
        switches = dict(self.d.config.follow_leader)
        keep_entry(switches, model, value)
        self.d.save_setting(follow_leader=switches)
        reply = {"ok": True, "keyboard": model, "follow_leader": value,
                 "follows": self.follows(model), "sync_leader": self.leader()}
        if keyboard is None or self.view(keyboard) == was:
            return reply
        keyboard.idle.wake_for_change()
        return with_note(reply, keyboard.apply_profile(), keyboard.last_apply_detail)
