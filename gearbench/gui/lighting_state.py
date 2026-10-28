"""The lighting the Keyboard tab shows and edits, the window's copy of what the daemon shows:
the state (LightingState), what a read of the files makes it (adopt_lighting), and the
keyboard it shows on, as the last status gives it (ShownOn).
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, replace
from typing import NamedTuple

from ..config import (
    DEFAULT_BACKGROUND,
    DEFAULT_COLOR,
    PER_KEY,
    STATIC,
    Profile,
    direction_shown,
    speed_shown,
)
from ..keyboard import shown_lighting
from ..lighting import ANIMATIONS, DEFAULT_SECOND_COLOR, OFF, SPEEDS
from ..profiles import GameProfile, layer_in_force


@dataclass
class LightingState:
    """What the editor shows: the mode, and the colors behind it."""

    #: The Lighting mode row's choice: STATIC, PER_KEY, an animation (lighting.ANIMATIONS),
    #: which shows in place of the profile's mode, or OFF, the disabled effect on a Static
    #: board (adopt_lighting).
    mode: str
    #: The brush in per-key mode, the whole-board color in static, and the
    #: Static color an animation takes.
    color: int
    #: What an unpainted key shows in per-key mode, kept apart from the brush: saved as the
    #: background, the brush would flood the board at the next reload.
    background: int
    per_key: dict[str, int]
    #: The Static color as the file holds it, held while Per-key shows for the switch back
    #: (choose_mode): `color` too outside Per-key, unless the board is disabled and drawn
    #: black. Taken from the file and each color committed outside Per-key (commit), never
    #: from `color` at the switch: a disabled board keeps its stored one.
    static_color: int
    #: Each animation's speed in ms as it runs, saved or default, for the Speed row.
    speeds: dict[str, int]
    #: Wave's direction as it runs, by name, for the Direction row.
    direction: str
    #: Pulsar Point's second color, for the Second color row.
    second_color: int = DEFAULT_SECOND_COLOR

    @classmethod
    def placeholder(cls) -> LightingState:
        """What the window shows until its first read: static, in the default colors."""
        return cls(mode=STATIC, color=DEFAULT_COLOR, per_key={}, background=DEFAULT_BACKGROUND,
                   static_color=DEFAULT_COLOR, speeds={}, direction="horizontal")

    def copy(self) -> LightingState:
        """A copy, which a later change to this one does not reach."""
        return replace(self, per_key=dict(self.per_key), speeds=dict(self.speeds))

    def take(self, state: LightingState) -> None:
        """Make this one a copy of `state`, in place: the window keeps one object for as long
        as it is open. In Per-key the brush stays this one's own."""
        brush = self.color
        vars(self).update(vars(state.copy()))
        if state.mode == PER_KEY:
            self.color = brush

    def choose_mode(self, mode: str) -> bool:
        """Show `mode`, and whether `color` moved: outside Per-key the Static color comes back
        (the owner's decision of 2026-09-23), since `color` can hold the brush or the black a
        disabled board is drawn in, and sending it would save it over the Static color. An
        animation runs in the Static color too. Off, the disabled effect, draws both colors
        black, as adopt_lighting draws a disabled board. The brush is not kept."""
        shown = 0x000000 if mode == OFF else self.static_color
        moved = mode != PER_KEY and self.color != shown
        if moved:
            self.color = shown
        if mode == OFF:
            self.background = 0x000000
        self.mode = mode
        return moved

    def commit(self, color: int) -> None:
        """A color committed: the brush in Per-key, else the color shown and the Static color
        from now on, for the next switch back from Per-key (choose_mode)."""
        self.color = color
        if self.mode != PER_KEY:
            self.static_color = color


class ShownOn(NamedTuple):
    """The keyboard the Keyboard tab's lighting shows on, from the last status: its model and
    the leader's, by key, for copy first; the effects it reports and whether it shows painted
    keys, key by key or by area, both None, which cut nothing, while no keyboard with lighting
    is connected; and whether it follows the leader. Before any status, the defaults: copy
    first's source, whole."""

    model: str | None = None
    leader: str | None = None
    effects: tuple[str, ...] | None = None
    per_key: bool | None = None
    follows: bool = False

    @classmethod
    def of(cls, status: dict) -> ShownOn:
        able = status.get("capabilities") if status.get("connected") else None
        lit = able if isinstance(able, list) and "lighting" in able else None
        effects = status.get("effects")
        model, leader = (status.get(key) if isinstance(status.get(key), str) else None
                         for key in ("lighting_model", "sync_leader"))
        return cls(model, leader, tuple(map(str, effects)) if lit and isinstance(
            effects, list) else None, ("per_key" in lit or "areas" in lit) if lit else None,
                   status.get("follows") is True)

    def reports(self, effect: str) -> bool:
        """Whether the keyboard reports `effect`: any, while `effects` cuts nothing."""
        return self.effects is None or effect in self.effects


#: What the lighting shows on before the first status.
BEFORE_STATUS = ShownOn()


def adopt_lighting(overlay: GameProfile | None, profile: Profile,
                   animations: Collection[str] = ANIMATIONS,
                   shown_on: ShownOn = BEFORE_STATUS) -> LightingState:
    """The state to show for a loaded game profile, or for the M profile, on a keyboard that
    has `animations`, of the model `shown_on` gives.

    Both store a Static color for the whole board, the brush in static mode, and a background
    under the keys in per-key mode. Both colors come from the file in either mode, as the keys
    do, so the next switch of mode sends the one kept (the owner's decisions of 2026-09-23).
    In per-key mode the window keeps its own brush over `color` (LightingState.take).

    Under the disabled effect both colors show black, as the board does: drawn in the stored
    color, the next key click or switch to Per-key would light the whole board. The mode is
    the profile's own, never inferred from its keys; an animation shows in its place, with the
    Static color it takes (the owner's decision of 2026-09-24). An animation the keyboard lacks
    shows the mode, as the board shows it static (Paint.write_lit). Off shows in place of
    Static under the disabled effect, where the keyboard reports it (the user's decision of
    2026-10-27); a Per-key layout's keys show over it, so Per-key shows there. The lighting is
    the model's own, or what copy first gives it, as the daemon shows it
    (keyboard.shown_lighting).
    """
    layer = shown_lighting(layer_in_force(overlay, profile).lighting, *shown_on)[0]
    stored = layer.color
    # Drawn dark, but `stored` is kept for the switch back to Static once a paint turns the
    # board on (the owner's call of 2026-09-23).
    dark = layer.effect == OFF
    static, background = (0x000000, 0x000000) if dark else (stored, layer.background)
    speeds = {name: speed_shown(name, layer.settings(name)) for name in SPEEDS}
    direction = direction_shown(layer.settings("wave"))
    if dark and layer.mode == STATIC and shown_on.reports(OFF):
        shown = OFF
    else:
        shown = layer.effect if layer.effect in animations else layer.mode
    return LightingState(shown, static, background, dict(layer.keys), stored, speeds, direction,
                         layer.second_color)
