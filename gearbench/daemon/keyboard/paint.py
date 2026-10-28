"""The writes that put the lighting on the keyboard: the lit board (the brightness, sent only
on change to a keyboard that keeps it, then the layer in force), Disabled on one cluster, a
zone effect, a flat color with its per-key fill and overrides, cut to the keyboard's own keys
where its model says so, or with each area's
color on a board lit by areas, a drag's `preview` frame, the frame at FN's press and release,
and the dark that keeps the claim. Each write is given the lighting it writes
to, 0x8071's clusters or 0x8070's zones (keyboard_zones.KeyboardLights), by a caller that has
checked there is one."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

from ...hidpp import HidppError
from ...keyboard_zones import KeyboardLights, KeyboardZones, paints_areas, paints_keys
from ...keycodes import LOGO, ZONE_NAMES, by_zone
from ...lighting import (
    EFFECT_IDS,
    MAX_ZONE,
    STATIC_EFFECTS,
    Cluster,
    Lighting,
    LightingError,
    dimmed_parameter,
    fits,
    snap_brightness,
)
from ...wording import counted, escaped, word_list
from ..shared import scaled

if TYPE_CHECKING:
    from .. import Daemon
    from . import Keyboard

log = logging.getLogger("gearbench")

#: What the FN keys show while FN is held: white, every other key as the layer has it (the
#: owner's decision of 2026-10-21, "FN keys white, rest as is").
FN_WHITE = 0xFFFFFF
#: What a dim, for idleness or the lock, leaves of each color or intensity, in percent: half,
#: on the board and on a synced mouse's and headset's lights alike (Keyboard.at_level).
DIM_PERCENT = 50


def dimmed_color(color: int) -> int:
    """`color` as a dimmed board shows it, and a synced mouse's and headset's lights."""
    return scaled(color, DIM_PERCENT)


def dimmed(color: int, overrides: Mapping[int, int]) -> tuple[int, dict[int, int]]:
    """A fill and its overrides as a dimmed board shows them (dimmed_color)."""
    return dimmed_color(color), {zone: dimmed_color(value) for zone, value in overrides.items()}


def keepable(level: int, name: str) -> bool:
    """Whether `level`, a brightness the keyboard of model `name` reported, can be kept as its
    model's: one outside 0 to 100 is dropped with a warning, since config.json refuses it at the
    next start and set_brightness at every apply."""
    if 0 <= level <= 100:
        return True
    log.warning("the %s reported brightness %d, outside 0 to 100; not kept", name, level)
    return False


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
        #: (model, effect) of each effect write_lit has said hides the painted keys at info.
        self._keys_hidden_said: set[tuple[str, str]] = set()
        #: The brightness the keyboard holds, where it is known: the level read off it at its
        #: setup (read_brightness), then the level written last, or the one its own key
        #: reported (Keyboard.take_brightness_key). None from each setup until one of them, and
        #: while a write is out, so that one that fails is sent again.
        self.held_brightness: int | None = None
        #: Whether a brightness the `brightness` command saved has not reached the keyboard
        #: yet, as one set while it was away or not answering: its next setup's read keeps it
        #: (read_brightness) for its apply to write, in place of the level the keyboard holds.
        self.brightness_owed = False

    def shows_static(self, effect_name: str) -> bool:
        """Whether this effect renders as a flat color plus per-key overrides.

        An effect no cluster has renders as static, fill and all: the animated path has no fill
        and leaves the keys dark. With the keyboard away, a name lighting.EFFECT_IDS lacks is
        one no cluster has.
        """
        lighting = self.keyboard.lighting
        known = (effect_name in EFFECT_IDS if lighting is None
                 else any(c.has_effect(effect_name) for c in lighting.effect_clusters))
        return effect_name in STATIC_EFFECTS or not known

    def write_lit(self, lighting: KeyboardLights, effect_name: str, color: int,
                  per_key: Mapping[str, int], extra_params: Mapping[str, int],
                  shown: int, dim: bool) -> None:
        """The board with its lights up, at full or dimmed with `dim`: the brightness
        (write_brightness), then the layer in force, which is `effect_name`, `color`,
        `per_key` and `extra_params` (PerKeyboard.active_lighting), and `shown`, the color
        around the per-key overrides (Keyboard.shown_color).

        A static color goes through write_static, and so does an effect no cluster reports,
        which the journal says (say_missing_effect), and an animation while FN is held, paused
        under the FN keys (fn_zones). An animation is written by write_animation, which shows
        no painted key: where the layer holds some under an effect outside ANIMATIONS, a
        `custom` set by hand, the journal says so, at info the first time for each model and
        effect. A failed write goes to the caller.
        """
        brightness, own = self.d.per_keyboard.brightness_in_force(self.keyboard)
        if lighting.brightness_levels:
            written = self.write_brightness(lighting, brightness)
            # A copy another model's level gave it is sent, never saved: attaching saves no copy.
            if written != brightness and own:
                # Set while the keyboard was away, when its levels were not known, or the 13 a
                # G515's FN+F8 sets, where its levels have 12.
                log.info("brightness %d is %d on the %s; saving that", brightness, written,
                         self.keyboard.model_name())
                self.d.per_keyboard.keep_brightness(written, self.keyboard)
        known = any(c.has_effect(effect_name) for c in lighting.effect_clusters)
        if not known:
            self.say_missing_effect(effect_name)
        static = self.shows_static(effect_name)
        held = self.fn_zones()
        overrides = by_zone(per_key) if static else {}
        log.debug(
            "apply_profile: overlay=%s effect=%s color=#%06x overrides=%d brightness=%d%s",
            escaped(self.d.games.overlay.name) if self.d.games.overlay else None,
            escaped(effect_name), color, len(overrides), brightness,
            ", dimmed" if dim else "",
        )
        if static or held:
            self.write_static(lighting, shown, overrides, effect_name if known else "static",
                              extra_params=extra_params, dim=dim)
        else:
            self.write_animation(lighting, effect_name, color, extra_params, dim)
            if per_key:
                # Once at info: every apply meets it again, a dim's and a wake's among them.
                model = self.keyboard.model_name()
                said = (model, effect_name)
                log.log(logging.DEBUG if said in self._keys_hidden_said else logging.INFO,
                        "the %s does not show per-key colors under the %s effect", model,
                        effect_name)
                self._keys_hidden_said.add(said)

    def read_brightness(self) -> None:
        """At each setup, on a model that keeps its brightness (KeyboardModel.keeps_brightness)
        or is read all the same (reads_brightness, the G515 generation's untested relatives),
        the level the keyboard holds, read off it (0x8040 fn 1), as held_brightness, and kept
        as the level it shows where it shows its own, in place of the one saved: a change made
        with its own brightness key while Gearbench was not driving it shows (the owner's
        decision of 2026-10-22, "Read the keyboard's"). One that follows the leader shows the
        leader's, and one owed a level the `brightness` command saved (brightness_owed) that
        level, which its apply then writes. One that holds the level Gearbench last left its
        model at (PerKeyboard.written_brightness), as one that followed the leader holds the
        leader's, keeps the one saved, which its apply writes (the owner's decision of
        2026-10-26, "Yes, while running"). A read the keyboard refuses leaves the level not
        known, so its apply writes the saved one; any other failure goes to the caller."""
        self.held_brightness = None
        lighting = self.keyboard.lighting
        model = self.keyboard.key_control.model()
        if (not (model.keeps_brightness or model.reads_brightness) or lighting is None
                or not lighting.brightness_levels):
            return
        try:
            # 0x8071's: 0x8070's zones list no brightness levels.
            held = cast("Lighting", lighting).get_brightness()
        except HidppError as exc:
            if exc.unreachable:
                raise
            log.info("the %s refused its brightness read (%s); writing the level in effect",
                     model.name, exc)
            return
        self.held_brightness = held
        shown = self.d.per_keyboard.brightness(self.keyboard)
        if (held == shown or self.brightness_owed
                or self.d.per_keyboard.follows(self.keyboard.model())
                or not keepable(held, model.name)):
            return
        if held == self.d.per_keyboard.written_brightness.get(self.keyboard.model()):
            log.info("the %s holds brightness %d, which Gearbench wrote to it; keeping "
                     "Gearbench's %d", model.name, held, shown)
            return
        log.info("the %s holds brightness %d, where Gearbench had %d; keeping the keyboard's",
                 model.name, held, shown)
        self.d.per_keyboard.keep_brightness(held, self.keyboard)

    def write_brightness(self, lighting: KeyboardLights, level: int) -> int:
        """`level` to the keyboard, floored to a level it keeps (Lighting.set_brightness), and
        the level it holds after. A model that keeps its brightness when switched off
        (KeyboardModel.keeps_brightness) stores each write, so it is sent nothing where it holds
        that level already (held_brightness): the owner's decision of 2026-10-22, "Only on
        change". The level held counts as asked whole or floored: the 13 a G515's FN+F8 sets,
        which no write gives, is kept where 13 is asked, and the 12 written where 20 is. Either
        way the keyboard then holds the level asked, so none is owed (brightness_owed), and that
        level is the one Gearbench last left its model at (PerKeyboard.written_brightness)."""
        floored = snap_brightness(lighting.brightness_levels, level)
        shown = level if self.held_brightness == level else floored
        if not (self.keyboard.key_control.model().keeps_brightness
                and self.held_brightness == shown):
            self.held_brightness = None
            # 0x8071's: each caller checks for brightness levels first, which 0x8070's zones lack.
            self.held_brightness = shown = cast("Lighting", lighting).set_brightness(level)
            log.debug("brightness %d written", shown)
        self.brightness_owed = False
        self.d.per_keyboard.written_brightness[self.keyboard.model()] = shown
        return shown

    def write_animation(self, lighting: KeyboardLights, effect_name: str, color: int,
                        extra_params: Mapping[str, int], dim: bool) -> None:
        """An animation on every cluster, on a per-key board Disabled first, so its per-key
        buffer does not show through. 0x8070's zones run it on the zones that report it alone:
        the G213's whole-board zone runs it over its five areas, and a static written to an
        area would show over it (assumed: OpenRGB's G213 controller writes its effects to that
        zone alone).

        One the whole board runs (Lighting.whole_board), Pulsar Point, one of the G512 X's
        three more on its effect (lighting.SIGNATURES), or wave or ripple where a counted
        cluster lacks it, is one write on it and nothing on the clusters, Disabled included, as
        G HUB writes Pulsar Point. G HUB's static after Pulsar Point was static on clusters 0
        and 1 alone (Windows capture G2, 2026-10-23), and the same static after a wave and a
        ripple on the G915 X LIGHTSPEED's whole board took its keys and its logo back
        (measured 2026-10-24)."""
        board = lighting.whole_board
        if board is not None and board.has_effect(effect_name):
            self.write_zone_effect(lighting, board, effect_name, color, extra_params, dim=dim)
            return
        for cluster in lighting.clusters:
            if isinstance(lighting, KeyboardZones) and not cluster.has_effect(effect_name):
                continue
            if lighting.per_key:
                self.disable_cluster(lighting, cluster)
            self.write_zone_effect(lighting, cluster, effect_name, color, extra_params, dim=dim)

    def fn_zones(self) -> dict[int, int]:
        """The FN keys of the attached keyboard's model in FN_WHITE while FN is held
        (KeyboardModel.fn_keys, Modifiers.fn_held); empty otherwise."""
        modifiers = self.keyboard.modifiers
        if modifiers is None or not modifiers.fn_held:
            return {}
        model = self.keyboard.key_control.model()
        return dict.fromkeys(model.fn_keys, FN_WHITE)

    def write_fn(self, lighting: Lighting, effect_name: str, color: int,
                 per_key: Mapping[str, int], extra_params: Mapping[str, int], shown: int,
                 dim: bool) -> None:
        """The board as FN goes down or up (Keyboard.take_fn), with write_lit's arguments:
        one per-key frame of the layer in force (write_frame), its fill as a drag's preview
        has it, with the FN keys over it while FN is held, every color halved
        with `dim`. Under an animation the press pauses it, each cluster Disabled first, and
        the release writes it again, which starts it over (the owner's decision of 2026-10-21,
        "Pause the animation"). Neither writes the brightness or the M and MR LEDs."""
        if not self.shows_static(effect_name):
            if not self.fn_zones():
                self.write_animation(lighting, effect_name, color, extra_params, dim)
                return
            for cluster in lighting.clusters:
                self.disable_cluster(lighting, cluster)
        self.write_frame(lighting, shown, by_zone(per_key), dim)

    def say_missing_effect(self, effect_name: str) -> None:
        """Say that the attached keyboard shows static in place of `effect_name`, an effect the
        layer in force saved and none of its clusters reports: at info the first time for each
        model and effect, at debug after."""
        model = self.keyboard.model_name()
        said = (model, effect_name)
        log.log(logging.DEBUG if said in self._effect_said else logging.INFO,
                "the %s has no %s effect; it shows static in its place", model,
                escaped(effect_name))
        self._effect_said.add(said)

    def darken(self, lighting: KeyboardLights) -> None:
        """Turn every cluster off and keep the claim.

        Disabled is the one write that clears a cluster, per-key content included; a cluster
        without it is left as it is (disable_cluster), so the rest still go dark. The claim
        stays because the firmware repaints the moment it gets the panel back.
        """
        for cluster in lighting.clusters:
            self.disable_cluster(lighting, cluster)
        log.debug("lights out")

    def disable_cluster(self, lighting: KeyboardLights, cluster: Cluster) -> None:
        """Write Disabled to one cluster. A cluster without it is left as it is."""
        try:
            lighting.set_effect(cluster.index, "disabled")
        except LightingError as exc:
            log.debug("  cluster %d: disabled failed: %s", cluster.index, exc)

    def write_zone_effect(self, lighting: KeyboardLights, cluster: Cluster, effect_name: str,
                          color: int, extra_params: Mapping[str, int], dim: bool) -> None:
        """One cluster's zone effect, or static when the cluster lacks it or, with a warning,
        when a saved parameter does not fit the effect: a cluster left on its onboard effect is
        worse than one showing the right color in the wrong shape. The whole board has no
        static, so in its place every cluster is static.

        The G915's logo cluster has no wave and no ripple. Under wave the keys' wave takes the
        logo LED along; under ripple the logo stays steady. A keyboard whose whole board runs
        them has them written there (write_animation). `dim` halves the parameter
        lighting.dimmed_parameter names, an intensity never from 1 to 0.
        """
        try:
            params = lighting.effect_params(
                cluster.index, effect_name, color, extra_params,
            )
            # Halved only where the full level fits its bytes: halving takes an intensity of 300
            # into its byte, and scaled() drops a color's bits past 24, so a value the lit board
            # shows static would animate dimmed.
            effect_id = cluster.effect(effect_name).id
            key = dimmed_parameter(effect_id, params)
            if dim and key is not None and fits(effect_id, key, params[key]):
                params[key] = (dimmed_color(params[key]) if key == "color"
                               else params[key] * DIM_PERCENT // 100 or params[key])
            lighting.set_effect(cluster.index, effect_name, **params)
            log.debug("  cluster %d (%s): %s %s", cluster.index, cluster.name,
                      effect_name, params)
        except LightingError as exc:
            for shown in lighting.clusters if cluster is lighting.whole_board else (cluster,):
                lighting.set_effect(shown.index, "static",
                                    color=dimmed_color(color) if dim else color)
            log.debug("  cluster %d (%s): static in place of %s: %s",
                      cluster.index, cluster.name, effect_name, exc)
            if cluster.has_effect(effect_name):
                # Not the logo's missing wave: speeds and the direction are checked as the file
                # is read, so this is a hand-edited lighting.params value, which only an M
                # profile has. Once at warning: every apply meets it again, on every cluster.
                said = (self.d.config.active, effect_name, str(exc))
                if said not in self._params_said:
                    self._params_said.add(said)
                    log.warning("profile %d's lighting params do not fit their effect (%s); "
                                "the %s shows static", self.d.config.active, exc,
                                self.keyboard.model_name())

    def board_zones(self, zones: Mapping[int, int]) -> Mapping[int, int]:
        """`zones` without the ones the attached keyboard lacks, where its model is cut to its
        board (KeyboardModel.cut_to_board), so that a write naming one, which its firmware
        might refuse whole, is never sent. Said at info the first time for each model, and at
        debug after that."""
        model = self.keyboard.key_control.model()
        if not model.cut_to_board:
            return zones
        cut = sorted(zone for zone in zones if zone not in model.board.zones)
        if not cut:
            return zones
        names = word_list([ZONE_NAMES.get(zone, f"zone {zone}") for zone in cut])
        said = "its color is" if len(cut) == 1 else "their colors are"
        log.log(logging.DEBUG if model.name in self._cut_said else logging.INFO,
                "the %s has no %s to light; %s not sent", model.name, names, said)
        self._cut_said.add(model.name)
        return {zone: color for zone, color in zones.items() if zone in model.board.zones}

    def area_colors(self, lighting: KeyboardLights, color: int,
                    overrides: Mapping[int, int]) -> dict[int, int]:
        """Each area's color of `overrides` over `color`, by the 0x8070 zone that lights it
        (layout.Board.area_colors), where `lighting` paints areas (paints_areas); empty on
        any other."""
        if not paints_areas(lighting):
            return {}
        board = self.keyboard.key_control.model().board
        return dict(zip(lighting.area_zones, board.area_colors(overrides, color), strict=True))

    @staticmethod
    def fill_last(lighting: Lighting, unlisted: int = MAX_ZONE) -> int:
        """The last zone of a fill: the highest the keyboard's zone bitmap sets
        (Lighting.listed_last), whatever its model, or `unlisted` where the bitmap was not
        read. A fill ending on a zone the keyboard does not set may be refused whole."""
        return unlisted if lighting.listed_last is None else lighting.listed_last

    def preview_last(self) -> int:
        """The last zone of a preview's fill where the keyboard's zone bitmap was not read
        (fill_last): the logo, as on the G915, or, on a model cut to its board that has no
        logo, its own highest zone below it. The G515 TKL refused a range ending at the logo
        whole, invalid argument, and took one ending at 254 (measured once, 2026-10-18)."""
        model = self.keyboard.key_control.model()
        if not model.cut_to_board or LOGO in model.board.zones:
            return LOGO
        return max(zone for zone in model.board.zones if zone < LOGO)

    def write_frame(self, lighting: Lighting, fill: int, zones: Mapping[int, int],
                    dim: bool = False) -> None:
        """One per-key frame of the whole board: `fill` over every zone to fill_last, or to
        preview_last where the zone bitmap was not read, with `zones` and the FN keys held
        (fn_zones) over it, cut to the board (board_zones), every color halved with `dim`.
        A failed write goes to the caller."""
        overrides = self.board_zones({**zones, **self.fn_zones()})
        if dim:
            fill, overrides = dimmed(fill, overrides)
        lighting.fill_and_paint(fill, overrides, last=self.fill_last(lighting, self.preview_last()))

    def write_static(self, lighting: KeyboardLights, color: int, overrides: Mapping[int, int],
                     effect_name: str = "static", *, extra_params: Mapping[str, int],
                     dim: bool) -> None:
        """A flat color over the whole board, its fill to fill_last, or to MAX_ZONE where the
        zone bitmap was not read, then the per-key overrides with the FN keys held over them
        (fn_zones), every color halved with `dim`. Shared by a static apply and `paint`, so
        the two cannot drift. The overrides are cut to the keyboard's board first
        (board_zones).

        On a per-key board the zone effect alone does not render the keys in HOST mode, so the
        per-key fill over every key is what shows, and with overrides the clusters are Disabled
        rather than given the effect: an applied static effect re-renders its color over per-key
        content. On a board lit by areas with painted keys every zone takes a static, under
        disabled too, as a per-key board shows its painted keys over black: the whole board's
        zone `color`, then each area's zone its area's color of the overrides (area_colors).
        """
        per_key = lighting.per_key
        overrides = self.board_zones({**overrides, **self.fn_zones()})
        areas = self.area_colors(lighting, color, overrides)
        # Painted areas show as static under disabled too, as a per-key board's painted keys
        # show over its black.
        zone_effect = "static" if areas and overrides else effect_name
        for cluster in lighting.clusters:
            if overrides and per_key:
                self.disable_cluster(lighting, cluster)
            else:
                self.write_zone_effect(lighting, cluster, zone_effect,
                                       areas.get(cluster.index, color), extra_params, dim=dim)
        if dim:
            color, overrides = dimmed(color, overrides)
        if not paints_keys(lighting):
            # A zonal board: the zone effect is all that renders.
            return
        log.debug("  per-key fill #%06x and %s", color, counted(len(overrides), "override"))
        lighting.fill(color, last=self.fill_last(lighting))
        if overrides:
            lighting.paint(overrides)
