"""The writes that put the lighting on the keyboard: the lit board (the brightness, then the
layer in force), Disabled on one cluster, a zone effect, a flat color with its per-key fill
and overrides, cut to the keyboard's own keys where its model says so, and the dark that keeps
the claim. Each write is given the `Lighting` it writes to, by a caller that has checked there
is one."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Literal, cast

from ...keyboard import MODEL_OF as KEYBOARDS, name as keyboard_name
from ...keycodes import LOGO, ZONE_NAMES, ZONES
from ...lighting import EFFECT_IDS, STATIC_EFFECTS, Cluster, Lighting, LightingError, fits
from ...wording import escaped, word_list
from ..shared import scaled

if TYPE_CHECKING:
    from .. import Daemon
    from . import Keyboard

log = logging.getLogger("gearbench")

class Paint:
    """What puts the lighting in force on the keyboard."""

    def __init__(self, daemon: Daemon, keyboard: Keyboard) -> None:
        self.d = daemon
        self.keyboard = keyboard
        #: (profile, effect, refusal) of each saved parameter write_zone_effect has said at
        #: warning.
        self._params_said: set[tuple[int, str, str]] = set()
        #: The models board_zones has said at info that it cut a paint for.
        self._cut_said: set[str] = set()
        #: (model, effect) of each saved effect say_missing_effect has said at info.
        self._effect_said: set[tuple[str, str]] = set()

    def shows_static(self, effect_name: str) -> bool:
        """Whether this effect renders as a flat color plus per-key overrides.

        An effect no cluster has renders as static, fill and all: the animated path has no fill
        and leaves the keys dark. With the keyboard away, a name lighting.EFFECTS lacks is one
        no cluster has.
        """
        lighting = self.keyboard.lighting
        known = (effect_name in EFFECT_IDS if lighting is None
                 else any(c.has_effect(effect_name) for c in lighting.clusters))
        return effect_name in STATIC_EFFECTS or not known

    def write_lit(self, lighting: Lighting, effect_name: str, color: int,
                  per_key: Mapping[str, int], extra_params: Mapping[str, int],
                  shown: int, dim: bool) -> None:
        """The board with its lights up, at full or dimmed with `dim`: the brightness, then the
        layer in force, which is `effect_name`, `color`, `per_key` and `extra_params`
        (PerKeyboard.active_lighting), and `shown`, the color around the per-key overrides
        (Keyboard.shown_color).

        A static color goes through write_static, and so does an effect no cluster reports,
        which the journal says (say_missing_effect). An animation is Disabled then the effect,
        so the per-key buffer does not show through. A failed write goes to the caller.
        """
        brightness, own = self.d.per_keyboard.brightness_in_force()
        if lighting.brightness_levels:
            written = lighting.set_brightness(brightness)
            # A copy another model's level gave it is sent, never saved: attaching saves no copy.
            if written != brightness and own:
                # Set while the keyboard was away, when its levels were not known.
                log.info("brightness %d is %d on this keyboard; saving that", brightness,
                         written)
                self.d.per_keyboard.keep_brightness(written)
        known = any(c.has_effect(effect_name) for c in lighting.clusters)
        if not known:
            self.say_missing_effect(effect_name)
        static = self.shows_static(effect_name)
        overrides = (
            {ZONES[name]: value for name, value in per_key.items() if name in ZONES}
            if static else {}
        )
        log.debug(
            "apply_profile: overlay=%s effect=%s color=#%06x overrides=%d brightness=%d%s",
            escaped(self.d.games.overlay.name) if self.d.games.overlay else None,
            escaped(effect_name), color, len(overrides), brightness,
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

    def say_missing_effect(self, effect_name: str) -> None:
        """Say that the attached keyboard shows static in place of `effect_name`, an effect the
        layer in force saved and none of its clusters reports, such as wave saved on a G915 and
        shown on a G515: at info the first time for each model and effect, at debug after."""
        model = keyboard_name(self.keyboard.keyboard_product)
        said = (model, effect_name)
        log.log(logging.DEBUG if said in self._effect_said else logging.INFO,
                "the %s has no %s effect; it shows static in its place", model,
                escaped(effect_name))
        self._effect_said.add(said)

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
            if dim and key is not None and fits(cluster.effect(effect_name).id, key, params[key]):
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

    def board_zones(self, zones: Mapping[int, int]) -> Mapping[int, int]:
        """`zones` without the ones the attached keyboard lacks, where its model is cut to its
        board (KeyboardModel.cut_to_board), so that a write naming one, which its firmware
        might refuse whole, is never sent. Said at info the first time for each model, and at
        debug after that."""
        # Set by connect before it finds the lighting each caller holds. Nothing checks the cast.
        model = KEYBOARDS[cast("int", self.keyboard.keyboard_product)]
        if not model.cut_to_board:
            return zones
        cut = sorted(zone for zone in zones if zone not in model.board.zones)
        if not cut:
            return zones
        names = word_list([ZONE_NAMES.get(zone, f"zone {zone}") for zone in cut])
        said = "the color for it is" if len(cut) == 1 else "the colors for them are"
        log.log(logging.DEBUG if model.name in self._cut_said else logging.INFO,
                "the %s has no %s to light; %s left out of what it is sent", model.name, names,
                said)
        self._cut_said.add(model.name)
        return {zone: color for zone, color in zones.items() if zone in model.board.zones}

    def preview_last(self) -> int:
        """The last zone of a preview's fill: the logo, as on the G915, or, on a model cut to
        its board that has no logo, its own highest zone below it. The G515 TKL refused a
        range ending at the logo whole, invalid argument, and took one ending at 254
        (measured once, 2026-10-18)."""
        model = KEYBOARDS[cast("int", self.keyboard.keyboard_product)]
        if not model.cut_to_board or LOGO in model.board.zones:
            return LOGO
        return max(zone for zone in model.board.zones if zone < LOGO)

    def write_static(self, lighting: Lighting, color: int, overrides: Mapping[int, int],
                     effect_name: str = "static", *, extra_params: Mapping[str, int],
                     dim: bool) -> None:
        """A flat color over the whole board, then the per-key overrides, every color halved
        with `dim`. Shared by a static apply and `paint`, so the two cannot drift. The
        overrides are cut to the keyboard's board first (board_zones).

        On a per-key board the zone effect alone does not render the keys in HOST mode, so the
        per-key fill over every key is what shows, and with overrides the clusters are Disabled
        rather than given the effect: an applied static effect re-renders its color over per-key
        content.
        """
        per_key = lighting.per_key
        overrides = self.board_zones(overrides)
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
