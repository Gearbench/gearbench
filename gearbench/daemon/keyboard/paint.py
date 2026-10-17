"""The writes that put the lighting on the keyboard: the lit board (the brightness, then the
layer in force), Disabled on one cluster, a zone effect, a flat color with its per-key fill
and overrides, and the dark that keeps the claim. Each write is given the `Lighting` it writes
to, by a caller that has checked there is one.

And what the controller and its command handlers both read: how long the M and MR LEDs wait
after an effect write, and the words for a change the keyboard does not show or cannot take."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Literal

from ...config import escaped
from ...keycodes import ZONES
from ...lighting import NAME_TO_EFFECT, STATIC_EFFECTS, Cluster, Lighting, LightingError, fits
from ..shared import scaled, unreachable

if TYPE_CHECKING:
    from .. import Daemon
    from . import Keyboard

log = logging.getLogger("gearbench")

# Delay from an apply's last lighting write to the M/MR LED writes: the firmware acknowledges an
# effect write before applying it, and applying it clears the indicator LEDs, so an LED write
# inside that window is lost. A loop deadline (Keyboard.write_due_leds), not a sleep.
LED_SETTLE = 0.2

# Why the keyboard does not show what was applied: status's `last_apply_error`, a reply's `note`.
KEYBOARD_AWAY = "the keyboard is away; the change is saved and shows when it is back"

KEYBOARD_SILENT = ("the keyboard is not answering; the change is saved and shows "
                   "when it is back")

#: The same, from a failed write: its receiver cannot reach the keyboard, or nothing answered.
KEYBOARD_OFF = ("the keyboard is switched off or out of range; the change is saved "
                "and shows when it is back")

KEYBOARD_NO_REPLY = ("the keyboard did not answer; the change is saved and shows "
                     "when it is back")

#: A failed write on the receiver after which the cable look found the keyboard's cable and
#: dropped the receiver route: the cable's connect applies what is saved.
KEYBOARD_ON_CABLE = ("the keyboard moved to its cable; the change is saved and shows once "
                     "it is set up there")

#: What is said of a keyboard attached without lighting (`Keyboard.lighting` None): the refusal
#: and the start of the notes saved with it.
NO_LIGHTING = "Gearbench cannot drive this keyboard's lighting"


def unanswered_note(exc: BaseException, moved: bool) -> str:
    """The note for a write that went unanswered (`unanswered`), after the cable look it starts:
    `moved` when that look found the cable and dropped the receiver route."""
    if moved:
        return KEYBOARD_ON_CABLE
    return KEYBOARD_OFF if unreachable(exc) else KEYBOARD_NO_REPLY


class Paint:
    """What puts the lighting in force on the keyboard."""

    def __init__(self, daemon: Daemon, keyboard: Keyboard) -> None:
        self.d = daemon
        self.keyboard = keyboard
        #: (profile, effect, refusal) of each saved parameter write_zone_effect has said at
        #: warning.
        self._params_said: set[tuple[int, str, str]] = set()

    def shows_static(self, effect_name: str) -> bool:
        """Whether this effect renders as a flat color plus per-key overrides.

        An effect no cluster has renders as static, fill and all: the animated path has no fill
        and leaves the keys dark. With the keyboard away, a name lighting.EFFECTS lacks is one
        no cluster has.
        """
        lighting = self.keyboard.lighting
        known = (effect_name in NAME_TO_EFFECT if lighting is None
                 else any(c.has_effect(effect_name) for c in lighting.clusters))
        return NAME_TO_EFFECT.get(effect_name) in STATIC_EFFECTS or not known

    def write_lit(self, lighting: Lighting, effect_name: str, color: int,
                  per_key: Mapping[str, int], extra_params: Mapping[str, int],
                  shown: int, dim: bool) -> None:
        """The board with its lights up, at full or dimmed with `dim`: the brightness, then the
        layer in force, which is `effect_name`, `color`, `per_key` and `extra_params`
        (Games.active_lighting), and `shown`, the color around the per-key overrides
        (Keyboard.shown_color).

        A static color goes through write_static. An animation is Disabled then the effect, so
        the per-key buffer does not show through. A failed write goes to the caller.
        """
        if lighting.brightness_levels:
            written = lighting.set_brightness(self.d.config.brightness)
            if written != self.d.config.brightness:
                # Set while the keyboard was away, when its levels were not known.
                log.info("brightness %d is %d on this keyboard; saving that",
                         self.d.config.brightness, written)
                self.d.config.brightness = written
                self.d.persist()
        known = any(c.has_effect(effect_name) for c in lighting.clusters)
        static = self.shows_static(effect_name)
        overrides = (
            {ZONES[name]: value for name, value in per_key.items() if name in ZONES}
            if static else {}
        )
        log.debug(
            "apply_profile: overlay=%s effect=%s color=#%06x overrides=%d brightness=%d%s",
            escaped(self.d.games.overlay.name) if self.d.games.overlay else None,
            escaped(effect_name), color, len(overrides), self.d.config.brightness,
            ", dimmed" if dim else "",
        )
        if static:
            self.write_static(lighting, shown, overrides, effect_name if known else "static",
                              extra_params=extra_params, dim=dim)
        else:
            for cluster in lighting.clusters:
                self.disable_cluster(lighting, cluster)
                self.write_zone_effect(lighting, cluster, effect_name, color, extra_params,
                                       dim=dim)
            if per_key:
                log.info("per-key colors are not shown under the %s effect", effect_name)

    def darken(self, lighting: Lighting, mouse: bool = True) -> None:
        """Turn every cluster off and keep the claim, and with `mouse` turn a synced mouse black.

        Disabled is the one write that clears a cluster, per-key content included; a cluster
        without it is left as it is (disable_cluster), so the rest still go dark. The claim
        stays because the firmware repaints the moment it gets the panel back.
        """
        for cluster in lighting.clusters:
            self.disable_cluster(lighting, cluster)
        if mouse:
            self.d.mice.color.sync_mouse_color(0x000000)
        log.debug("lights out")

    def disable_cluster(self, lighting: Lighting, cluster: Cluster) -> None:
        """Write Disabled to one cluster. A cluster without it is left as it is."""
        try:
            lighting.set_effect(cluster.index, "disabled")
        except LightingError as exc:
            log.debug("  cluster %d: disabled failed: %s", cluster.index, exc)

    def write_zone_effect(self, lighting: Lighting, cluster: Cluster, effect_name: str,
                          color: int, extra_params: Mapping[str, int], dim: bool) -> None:
        """One cluster's zone effect, or static when the cluster lacks it or, with a warning,
        when a saved parameter does not fit the effect: a cluster left on its onboard effect is
        worse than one showing the right color in the wrong shape.

        The logo's cluster has no wave and no ripple. Under wave the keys' wave takes the logo LED
        along; under ripple the logo stays steady. `dim` halves the color, each of its red,
        green and blue rounded down, or the intensity of an effect without a color (cycle,
        wave), rounded down but never from 1 to 0. A value too wide for its bytes is left
        whole, so the dimmed board shows static, with the warning, as lit.
        """
        try:
            params = lighting.effect_params(
                cluster.index, effect_name, color, extra_params,
            )
            # Halved only where the full level fits its bytes: halving takes an intensity of 300
            # into its byte, and scaled() drops a color's bits past 24, so a value the lit board
            # shows static would animate dimmed.
            key: Literal["color", "intensity"] | None = (
                "color" if "color" in params else "intensity" if "intensity" in params else None)
            if dim and key is not None and fits(NAME_TO_EFFECT[effect_name], key, params[key]):
                params[key] = (scaled(params[key], 50) if key == "color"
                               else params[key] // 2 or params[key])
            lighting.set_effect(cluster.index, effect_name, **params)
            log.debug("  cluster %d (%s): %s %s", cluster.index, cluster.name,
                      effect_name, params)
        except LightingError as exc:
            lighting.set_effect(cluster.index, "static",
                                color=scaled(color, 50) if dim else color)
            log.debug("  cluster %d (%s): static in place of %s: %s",
                      cluster.index, cluster.name, effect_name, exc)
            if cluster.has_effect(effect_name):
                # Not the logo's missing wave: speeds and the direction are checked as the file
                # is read, so this is a hand-edited lighting.params value, which only an M
                # profile has. Once at warning: every apply meets it again, on every cluster.
                said = (self.d.config.active, effect_name, str(exc))
                if said not in self._params_said:
                    self._params_said.add(said)
                    log.warning("profile %d's lighting params do not fit %s (%s); the keyboard "
                                "shows static", self.d.config.active, effect_name, exc)

    def write_static(self, lighting: Lighting, color: int, overrides: Mapping[int, int],
                     effect_name: str = "static", *, extra_params: Mapping[str, int],
                     dim: bool) -> None:
        """A flat color over the whole board, then the per-key overrides, every color halved
        with `dim`. Shared by a static apply and `paint`, so the two cannot drift.

        On a per-key board the zone effect alone does not render the keys in HOST mode, so the
        per-key fill over every key is what shows, and with overrides the clusters are Disabled
        rather than given the effect: an applied static effect re-renders its color over per-key
        content.
        """
        per_key = lighting.per_key
        for cluster in lighting.clusters:
            if overrides and per_key:
                self.disable_cluster(lighting, cluster)
            else:
                self.write_zone_effect(lighting, cluster, effect_name, color, extra_params,
                                       dim=dim)
        if dim:
            color = scaled(color, 50)
            overrides = {zone: scaled(value, 50) for zone, value in overrides.items()}
        if not per_key:
            # A zonal board: the zone effect is all that renders.
            return
        log.debug("  per-key fill #%06x and %d override%s", color, len(overrides),
                  "" if len(overrides) == 1 else "s")
        lighting.fill(color)
        if overrides:
            lighting.paint(overrides)
