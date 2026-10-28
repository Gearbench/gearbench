"""The keyboard's commands: the M profile switch and its name, the brightness, the color, the
effect, a drag's preview, the layout editor's paint, the saved colors and the idle dimming.
A lighting change shows on the keyboard it is for, unless that one follows the leader
(`following`), and on each keyboard driven that shows the same lighting (Keyboards.sharing)
or whose copy first it moves (`views`). With them the digest of the lighting in force and the
saved colors as the front ends want them, the keyboard's entries in `status` and its problem
for `problems`, and the journal's line at its connect (`connected_words`)."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from ...config import (
    PER_KEY,
    PROFILE_IDS,
    SAVED_COLORS_MAX,
    STATIC,
    direction_problem,
    direction_shown,
    expect,
    format_color,
    idle_problem,
    parse_color,
    speed_problem,
    speed_shown,
)
from ...hidpp import DeviceGone, HidppFailure
from ...keyboard import (
    CABLED_IDS,
    MODEL_OF as KEYBOARDS,
    key_name,
    model_key,
    name as keyboard_name,
    typed_words,
    wave_directions,
)
from ...keyboard_zones import (
    KeyboardLights,
    paints_keys,
    power_modes,
    takes_brightness,
    takes_paint,
)
from ...keycodes import ZONE_NAMES
from ...lighting import (
    ANIMATIONS,
    DARK,
    DIM,
    KEYBOARD_EFFECTS,
    LIT,
    MAX_ZONE,
    MIN_ZONE,
    SPEEDS,
    TWO_COLORED,
    LightingError,
    offered_effects,
    snap_brightness,
)
from ...report import untested, untested_note
from ...settings_files import ConfigError
from ...wording import M_PROFILE_NEEDS_A_NAME, SERVICE_JOURNAL, kind_of, word_list
from ..shared import (
    NODE_DENIED,
    NOT_AN_M_PROFILE,
    digest,
    failure_reason,
    name_text,
    no_effect,
    node_denied,
    switch_value,
    unanswered,
    whole_number,
    with_note,
)
from .notes import FOLLOWING, KEYBOARD_NO_BRIGHTNESS, LED_SETTLE, NO_LIGHTING, unanswered_note

if TYPE_CHECKING:
    from pathlib import Path

    from ...config import Lighting as ProfileLighting
    from .. import Daemon
    from . import Keyboard

log = logging.getLogger("gearbench")


def connected_words(product: int, path: Path, index: int, able: list[str]) -> str:
    """The journal's line for the keyboard set up on `path` at device index `index`: its
    model, its id and what this build drives on it, `able`, and for a model not yet run on
    (report.untested) the note that asks for a device report from it."""
    name = keyboard_name(product)
    said = (f"{name} connected on {path} ({product:04x}, device index 0x{index:02x}): "
            f"{', '.join(able) or 'nothing this build drives'}")
    return f"{said}; {untested_note(name)}" if untested(product) else said


#: Said of a keyboard with zone lighting alone; the model's name goes in.
NO_PER_KEY = "the {} has no per-key lighting"


def parse_zones(raw: object, field: str) -> dict[int, int]:
    """`paint`'s and `preview`'s `zones`: zone id -> color, none for an absent or null one.
    TypeError and ValueError are a bad request."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise TypeError(f"{field} is an object of zone ids and colors, not {kind_of(raw)}")
    zones = {}
    for z, c in raw.items():
        try:
            zone = int(z)
        except ValueError:
            raise ValueError(f"{field}: {z!r} is not a zone id") from None
        zones[zone] = parse_color(c, f"{field}.{z}")
    return zones


class KeyboardCommands:
    """The handlers of the keyboard's commands, and the keyboard's share of `status` and
    `problems`."""

    def __init__(self, daemon: Daemon, keyboard: Keyboard) -> None:
        self.d = daemon
        self.keyboard = keyboard

    def commands(self) -> dict[str, Callable[[dict], dict]]:
        return {
            "profile": self._cmd_profile,
            "rename": self._cmd_rename,
            "brightness": self._cmd_brightness,
            "color": self._cmd_color,
            "effect": self._cmd_effect,
            "preview": self._cmd_preview,
            "paint": self._cmd_paint,
            "save_color": self._cmd_save_color,
            "forget_color": self._cmd_forget_color,
            "idle": self._cmd_idle,
        }

    def _cmd_profile(self, request: dict) -> dict:
        index = whole_number(request["value"], "profile")
        if index not in PROFILE_IDS:
            return {"ok": False, "error": NOT_AN_M_PROFILE.format(index)}
        failed = self.keyboard.switch_profile(index, wake=True)
        return with_note({"ok": True, "profile": self.d.config.active}, failed,
                         self.keyboard.last_apply_detail)

    def _refused(self, refusal: str) -> dict:
        """The refusal `refusal`, with this keyboard's model's name in it."""
        return {"ok": False, "error": refusal.format(self.keyboard.model_name())}

    def _cmd_rename(self, request: dict) -> dict:
        # Text, as config.json reads a name: str() would name the profile "None" for a null.
        expect(request["value"], str, "rename", "text")
        name = request["value"].strip()
        if not name:
            return {"ok": False, "error": M_PROFILE_NEEDS_A_NAME}
        profile = self.d.config.profile
        before, profile.name = profile.name, name
        try:
            self.d.save_config()
        except BaseException:
            profile.name = before
            raise
        log.info("profile %d renamed to %r", self.d.config.active, name)
        return {"ok": True, "name": name, "profile": self.d.config.active}

    def _cmd_brightness(self, request: dict) -> dict:
        level = whole_number(request["value"], "brightness")
        if self.keyboard.dev is not None and self.keyboard.lighting is None:
            return self._refused(NO_LIGHTING)
        product = self.keyboard.keyboard_product
        # 0x8070's zones, the G213's, have none, attached or away.
        if not takes_brightness(self.keyboard.lighting,
                                product is not None and KEYBOARDS[product].zone_lighting):
            return self._refused(KEYBOARD_NO_BRIGHTNESS)
        if not 0 <= level <= 100:
            return {"ok": False, "error": f"brightness is 0 to 100, not {level}"}
        self.d.per_keyboard.check_model(self.keyboard)
        self.d.check_config_writable()
        following, shown = self.following(), self.views()
        if following is None:
            # The wake paints at full first: the brightness write alone would leave a board
            # woken from its idle dim at half its colors.
            self.keyboard.idle.wake_for_change(paint=True)
        failed = detail = None
        if (following is None and self.keyboard.lighting is not None
                and self.keyboard.keyboard_answering() and self.keyboard.lights == LIT):
            try:
                with self.keyboard.keyboard_writing():
                    written = self.keyboard.paint.write_brightness(self.keyboard.lighting, level)
            except (HidppFailure, LightingError) as exc:
                if not unanswered(exc):
                    raise
                # No answer (marked by keyboard_writing): saved, owed below unless the cable
                # look dropped the receiver route, and said as not_shown says it.
                log.info("brightness %d not written: %s", level, exc)
                failed = unanswered_note(exc, self.keyboard.dev is None,
                                         self.keyboard.model_name())
                detail = str(exc)
            else:
                try:
                    self.d.save_setting(brightness=self.d.per_keyboard.with_brightness(
                        written, self.keyboard))
                except BaseException:
                    # The keyboard shows `written`: apply_profile puts the saved level back.
                    self.keyboard.apply_profile()
                    raise
                self.show_on_sharing(shown)
                self.keyboard.sync_devices(self.keyboard.shown_color())
                return {"ok": True, "requested": level, "applied": written,
                        "levels": list(self.keyboard.lighting.brightness_levels)}
        # Saved, and written by apply_profile when the keyboard or the lights are back; never to a
        # keyboard marked not answering, whose timeouts would hold the loop. Snapped as
        # set_brightness snaps where the levels are known; else apply_profile saves the level the
        # keyboard keeps.
        levels = self.keyboard.lighting.brightness_levels if self.keyboard.lighting else ()
        applied = snap_brightness(levels, level)
        self.d.save_setting(brightness=self.d.per_keyboard.with_brightness(applied,
                                                                            self.keyboard))
        # Owed: the next setup's read leaves it in force over the level the keyboard holds.
        self.keyboard.paint.brightness_owed = True
        unreached = self.keyboard.change_unreached()
        self.show_on_sharing(shown)
        # A synced mouse shows the saved level (at_level), the keyboard away or not.
        self.keyboard.sync_devices(self.keyboard.shown_color())
        return with_note({"ok": True, "requested": level, "applied": applied,
                          "levels": list(levels)},
                         following or failed or unreached or self.keyboard.lights_note(), detail)

    def _cmd_color(self, request: dict) -> dict:
        """Set the color and save it, on the loaded game profile when there is one, since that
        is what shows."""
        color = parse_color(request["value"], "color")
        # The window sends "mode": "static" with every color in Static mode: the layer goes
        # Static, which turns a disabled board back on and stops an animation, its layout and
        # background kept. Without it (the CLI) the color goes to what shows; an animation with
        # a color takes the Static color.
        static = request.get("mode") == STATIC
        self.check_preview_writable()
        was, shown = self.lighting_digest(), self.views()
        layer, made = self.d.per_keyboard.edited(self.keyboard)
        before = (layer.color, layer.background, layer.mode, layer.effect)
        if static:
            layer.color, layer.mode, layer.effect = color, STATIC, "static"
        elif layer.mode == PER_KEY and layer.effect not in ANIMATIONS:
            layer.background = color
        else:
            layer.color = color

        def undo() -> None:
            layer.color, layer.background, layer.mode, layer.effect = before
            made()

        return self.show_layer_change({"ok": True, "color": format_color(color)}, undo, was,
                                      shown)

    def _cmd_effect(self, request: dict) -> dict:
        """Set the effect the board shows and save it, on the loaded game profile or the M
        profile.

        `speed`, the period in milliseconds of an animation that takes one (SPEEDS), wave's
        `direction` and Pulsar Point's `second_color` are saved for that animation, so an
        effect named without them runs at what was saved, or the default. The mode is left alone: `effect static` brings back
        whichever of Static and Per-key the layer was on. The window's Off sends `"mode":
        "static"` with disabled, as its Static color does: the layer goes Static too, so a
        Per-key layout's keys go dark with the rest, kept for Per-key (the user's decision of
        2026-10-27). An effect the attached keyboard does not report is refused
        (missing_effect), with the ones it does, and a direction the keyboard model in force
        does not take (missing_direction), with the ones it takes.
        """
        name = name_text(request["value"], "effect")
        if name not in KEYBOARD_EFFECTS:
            return {"ok": False, "error": f"no effect named {name!r}; the keyboard "
                                          f"effects are {word_list(KEYBOARD_EFFECTS, 'and')}"}
        if (missing := self.missing_effect(name)) is not None:
            return {"ok": False, "error": missing}
        speed, direction = request.get("speed"), request.get("direction")
        second = request.get("second_color")
        if (problem := self.settings_problem(name, speed, direction, second)) is not None:
            return {"ok": False, "error": problem}
        if second is not None:
            second = parse_color(second, "second_color")
        self.d.check_layer_writable()
        was, shown = self.lighting_digest(), self.views()
        layer, made = self.d.per_keyboard.edited(self.keyboard)
        before = (layer.effect, dict(layer.speeds), layer.direction, layer.second_color,
                  layer.mode)
        layer.effect = name
        if request.get("mode") == STATIC:
            layer.mode = STATIC
        if speed is not None:
            layer.speeds[name] = speed
        if direction is not None:
            layer.direction = direction
        if second is not None:
            layer.second_color = second

        def undo() -> None:
            (layer.effect, layer.speeds, layer.direction, layer.second_color,
             layer.mode) = before
            made()

        reply: dict = {"ok": True, "effect": name}
        settings = layer.settings()
        if name in SPEEDS:
            reply["speed"] = speed_shown(name, settings)
        if name == "wave":
            reply["direction"] = direction_shown(settings)
        if name in TWO_COLORED:
            reply["second_color"] = format_color(layer.second_color)
        return self.show_layer_change(reply, undo, was, shown)

    def settings_problem(self, name: str, speed: object, direction: object,
                         second: object) -> str | None:
        """Why `effect` refuses the effect `name` with the `speed`, `direction` and second
        color a request gave, each None where it gave none, in words; None where it takes
        them. A direction goes to wave alone, one the keyboard model in force takes
        (missing_direction), and a second color to an effect with two (TWO_COLORED)."""
        if speed is not None and (problem := speed_problem(name, speed)) is not None:
            return problem
        if direction is not None:
            if name != "wave":
                return f"only wave has a direction; {name} has none"
            if (problem := direction_problem(direction)) is not None:
                return problem
            # direction_problem passed it: one of WAVE_DIRECTIONS' names.
            if (missing := self.missing_direction(cast("str", direction))) is not None:
                return missing
        if second is not None and name not in TWO_COLORED:
            return f"only {word_list(TWO_COLORED, 'and')} has a second color; {name} has none"
        return None

    def missing_effect(self, name: str) -> str | None:
        """Why the attached keyboard is not offered the effect `name`: its clusters, its whole
        board among them, do not report it (offered_effects). None when they do, or when no
        keyboard's lighting is known,
        and the effect is saved for the keyboard that comes."""
        lighting = self.keyboard.lighting
        if lighting is None:
            return None
        has = offered_effects(lighting.effect_clusters)
        if name in has:
            return None
        return no_effect(self.keyboard.model_name(), name, has)

    def missing_direction(self, direction: str) -> str | None:
        """Why wave does not run in `direction`, a name direction_problem passed, on the keyboard
        model in force, with the directions it takes; None when it takes it. Before any
        keyboard, the refusal of a lighting change then (PerKeyboard.check_model)."""
        key = self.d.per_keyboard.check_model(self.keyboard)
        takes = wave_directions(key)
        if direction in takes:
            return None
        return (f"the {key_name(key)} has no {direction} wave direction; its directions are "
                f"{word_list(takes, 'and')}")

    def show_layer_change(self, reply: dict, undo: Callable[[], None], was: str,
                          shown: dict[Keyboard, tuple[ProfileLighting, int]]) -> dict:
        """Show and save a change made to the layer in force, and answer `reply` with the overlay
        it went to and the digests (lighting_digest). A keyboard that follows the leader shows
        none of it of its own, and says so (`following`). Each keyboard the change moves from
        what it showed (`shown`, from `views`) is painted (show_on_sharing), this one among
        them while it follows a leader that copies its entry."""
        failed = following = self.following()
        if following is None:
            self.keyboard.idle.wake_for_change()
            failed = self.keyboard.apply_profile() or self.keyboard.no_lighting_note()
        self.show_on_sharing(shown)
        self.commit_lighting(undo)
        if self.d.games.overlay is not None:
            reply["overlay"] = self.d.games.overlay.name
        reply["lighting"], reply["was"] = self.lighting_digest(), was
        return with_note(reply, failed,
                         self.keyboard.last_apply_detail if following is None else None)

    def following(self, saved: bool = True) -> str | None:
        """What a lighting change for this keyboard says while it follows the leader
        (PerKeyboard.follows), which shows none of it of its own: FOLLOWING, with "saved; "
        unless `saved` is False, for a preview. None otherwise."""
        per_keyboard = self.d.per_keyboard
        if not per_keyboard.follows(self.keyboard.model()):
            return None
        note = FOLLOWING.format(self.keyboard.model_name(),
                                key_name(cast("str", per_keyboard.leader())))
        return note if saved else note.removeprefix("saved; ")

    def _cmd_preview(self, request: dict) -> dict:
        """Live preview while a color is dragged, or, with `paint`'s `base` and `zones`, while a
        drag paints keys (_preview_keys).

        Past the wake (show_frame), per-key frames alone, at most one for a color, without the
        zone effects and M/MR LEDs an apply writes, which would make a drag lag. A pending LED
        write keeps its time: it waits out zone effects, and a per-key frame is not one. Nothing
        is saved: the release sends `color`. A layer the release would be refused on is refused
        here first (check_preview_writable).
        """
        if self.keyboard.dev is not None and self.keyboard.lighting is None:
            return self._refused(NO_LIGHTING)
        if "zones" in request:
            return self._preview_keys(request)
        color = parse_color(request["value"], "preview")
        self.check_preview_writable()
        return self.show_frame(request, {"ok": True, "color": format_color(color)}, color, {},
                               mouse=True, color_drag=True)

    def show_frame(self, request: dict, reply: dict, base: int, zones: dict[int, int],
                   mouse: bool, color_drag: bool = False) -> dict:
        """A drag's frame, `base` with `zones` over it, once `preview` has passed its checks:
        the wake, the frame on this board (preview_board) and on each lit keyboard that shows
        its lighting (frame_on), but for a `color_drag` one that keeps its background
        (keeps_background), and with `mouse` the base on a synced mouse and a synced headset's
        lights (sync_devices). `reply` gets the lights-down note, or a follower's (`following`)
        in place of all of it."""
        if (following := self.following(saved=False)) is not None:
            return with_note(reply, following)
        # The wake paints the board at full first, LEDs included: the fill leaves the M and MR
        # LEDs as a dark left them, and under an animation writes nothing.
        self.keyboard.idle.wake_for_change(paint=True)
        # After the wake's apply, which drops the last one.
        self.keyboard.last_preview = request
        # Nothing to a keyboard marked not answering, whose timeouts would hold the loop on
        # every frame of the drag, nor while the lights are down; a synced mouse and a synced
        # headset's lights still follow the drag.
        if self.keyboard.lighting is not None and self.keyboard.lights != LIT:
            reply["note"] = self.keyboard.lights_note(saved=False)
        else:
            self.preview_board(self.keyboard, base, zones)
        if not (color_drag and self.keeps_background()):
            for keyboard in self.d.keyboards.sharing(self.keyboard):
                if keyboard.lights == LIT:
                    self.frame_on(keyboard, base, zones)
        if mouse:
            self.keyboard.sync_devices(base, repeat=False)
        return reply

    def keeps_background(self) -> bool:
        """Whether each keyboard that shows this one's lighting shows the background and the
        painted keys under a color drag: this layer runs an animation in Per-key mode, which a
        keyboard that shows a static board lacks (keyboard.cut_lighting; one that runs it gets
        no frame, preview_board), and the drag's release, `color` with no mode, changes the
        Static color alone, which such a board does not show."""
        layer = self.d.per_keyboard.shown(keyboard=self.keyboard)
        return layer.mode == PER_KEY and layer.effect in ANIMATIONS

    def frame_on(self, keyboard: Keyboard, base: int, zones: dict[int, int]) -> None:
        """A drag's frame (preview_board) on `keyboard`, another that shows this one's
        lighting (Keyboards.sharing), whose failure is its own: gone, it is torn down; any
        other is logged, and the release's apply reaches it, owed while keyboard_writing has it
        marked not answering (change_unreached). The drag goes on on this keyboard either
        way."""
        try:
            self.preview_board(keyboard, base, zones)
        except DeviceGone as exc:
            log.info("the %s went away during a drag: %s",
                     keyboard.model_name(), failure_reason(exc))
            keyboard.teardown_device()
        except (HidppFailure, LightingError) as exc:
            log.debug("a drag's frame did not reach the %s: %s",
                      keyboard.model_name(), failure_reason(exc))

    def preview_board(self, keyboard: Keyboard, base: int, zones: dict[int, int]) -> None:
        """A drag's frame on `keyboard` (Paint.write_frame): every zone in `base`, the logo
        included where the board has one, since a per-key frame does not make the logo
        re-ramp and flicker as a zone-effect write does, with `zones` (none for a color drag)
        over it. Only on a per-key board that answers and shows the layer static
        (Paint.shows_static): a disabled board is drawn too, and the release turns it
        static."""
        lighting = keyboard.lighting
        if (lighting is not None and paints_keys(lighting) and keyboard.keyboard_answering()
                and keyboard.paint.shows_static(self.d.per_keyboard.active_lighting(keyboard)[0])):
            with keyboard.keyboard_writing():
                keyboard.paint.write_frame(lighting, base, zones)

    def check_preview_writable(self) -> None:
        """check_layer_writable for a preview and for its release, `color` or `paint`, so a drag
        on a layer that would refuse the release shows no frame nothing can save. A drag with a
        preview accepted (last_preview), its layer refused since, gets back the lighting the
        daemon holds first, whether or not that preview showed a frame, and whether the refusal
        comes at a later frame or at the release."""
        try:
            self.d.per_keyboard.check_model(self.keyboard)
            self.d.check_layer_writable()
        except ConfigError:
            if self.keyboard.last_preview is not None:
                self.keyboard.apply_profile()
                self.show_on_sharing({})
            raise

    def _preview_keys(self, request: dict) -> dict:
        """`preview` with `paint`'s `base` and `zones`: a drag on the window's board.

        The whole board each time, since the window replaces a `preview` still waiting with the
        next and a part would be lost (preview_board): one per-key frame unless the zones pass
        fill_and_paint's report cap. Nothing is saved: the release sends `paint`. A synced mouse
        and a synced headset's lights are written only for a base other than the one showing.
        """
        if self.keyboard.lighting is not None and not self.keyboard.lighting.per_key:
            return self._refused(NO_PER_KEY)
        base = parse_color(request.get("base", "#000000"), "preview.base")
        zones = parse_zones(request.get("zones"), "preview.zones")
        # The frame's own range (Lighting._pack), checked before the wake writes anything. An id
        # no key has is painted; `paint` refuses it, since only keys are saved.
        for zone in zones:
            if not MIN_ZONE <= zone <= MAX_ZONE:
                return {"ok": False, "error": f"preview.zones.{zone}: no such zone"}
        self.check_preview_writable()
        reply = {"ok": True, "base": format_color(base), "zones": len(zones)}
        # The base showing is read before the wake.
        return self.show_frame(request, reply, base, zones,
                               mouse=base != self.keyboard.shown_color())

    def _cmd_paint(self, request: dict) -> dict:
        """A base color for everything, then zone overrides on top: what the layout editor sends.

        Saved first, even with the keyboard away or not answering, then shown through the
        write a static apply uses (Paint.write_static), on a board lit by areas as each area's
        color. A failed write is answered as apply_profile answers it. A zone no key has is
        refused, since only the keys are stored, and so is a paint for lighting that shows no
        painted key (keyboard_zones.takes_paint).
        """
        if self.keyboard.dev is not None and self.keyboard.lighting is None:
            return self._refused(NO_LIGHTING)
        if self.keyboard.lighting is not None and not takes_paint(self.keyboard.lighting):
            return self._refused(NO_PER_KEY)
        base = parse_color(request.get("base", "#000000"), "paint.base")
        zones = parse_zones(request.get("zones"), "paint.zones")
        for zone in zones:
            if zone not in ZONE_NAMES:
                return {"ok": False, "error": f"paint.zones.{zone}: no key has that zone"}
        self.check_preview_writable()
        was, shown = self.lighting_digest(), self.views()
        keys = {ZONE_NAMES[z]: c for z, c in zones.items()}
        layer, made = self.d.per_keyboard.edited(self.keyboard)
        before = (layer.mode, layer.background, layer.keys, layer.effect)
        # `base` is the background. The Static color is kept for the next switch to Static.
        layer.mode, layer.background, layer.keys, layer.effect = PER_KEY, base, keys, "static"

        def undo() -> None:
            layer.mode, layer.background, layer.keys, layer.effect = before
            made()

        self.commit_lighting(undo)
        # A drag's release, or a click after it: no drag frame to send again over the saved
        # paint at a wake repaint (tend_idle).
        self.keyboard.last_preview = None
        reply = {"ok": True, "base": format_color(base), "zones": len(zones),
                 "lighting": self.lighting_digest(), "was": was}
        if (following := self.following()) is not None:
            self.show_on_sharing(shown)
            return with_note(reply, following)
        woke = self.keyboard.idle.wake_for_change()
        if (unreached := self.keyboard.change_unreached()) is not None:
            reply["note"] = unreached
        elif woke:
            # Woken from idleness: one full apply shows the saved paint and the M and MR LEDs,
            # which the paint's own write leaves off.
            with_note(reply, self.keyboard.apply_profile(), self.keyboard.last_apply_detail)
        elif self.keyboard.lights == DARK:
            reply["note"] = self.keyboard.lights_note()
        else:
            try:
                # The layer's own effect parameters, as a static apply passes them. The
                # lighting is set: a keyboard without it is refused above, and one away is
                # change_unreached's.
                self.keyboard.paint.write_static(cast("KeyboardLights", self.keyboard.lighting),
                                                 base, zones, extra_params=layer.settings(),
                                                 dim=self.keyboard.lights == DIM)
            except (HidppFailure, LightingError) as exc:
                with_note(reply, *self.keyboard.not_shown(exc, "a paint"))
            else:
                # Not keyboard_writing: not_shown marks a failure already.
                self.keyboard.mark_keyboard_answering()
                if self.keyboard.leds_due is not None:
                    # This wrote zone effects too, so a pending LED write waits out its settle.
                    self.keyboard.leds_due = time.monotonic() + LED_SETTLE
                with_note(reply, self.keyboard.lights_note())
        self.show_on_sharing(shown)
        self.keyboard.sync_devices(base)
        return reply

    def commit_lighting(self, undo: Callable[[], None]) -> None:
        """Save the layer a lighting change went to, or undo the change.

        A failed save puts memory back and repaints what is saved, on each keyboard the change
        moved too (show_on_sharing), or the next unrelated save would write the change; the
        error goes on to the client.
        """
        try:
            self.d.save_layer_file()
        except BaseException:
            shown = self.views()
            undo()
            # apply_profile writes nothing to a keyboard marked not answering.
            self.keyboard.apply_profile()
            self.show_on_sharing(shown)
            raise

    def show_on_sharing(self, was: dict[Keyboard, tuple[ProfileLighting, int]]) -> None:
        """Paint the profile on each other keyboard driven that shows this keyboard's lighting
        and brightness (Keyboards.sharing): the leader's change shows on those that follow it,
        the owner's decisions of 2026-10-21. Of `was`, what each showed before the change
        (`views`), each keyboard the change moved is painted too, as one that shows this
        keyboard's entry by copy first (Keyboards.repaint_changed), this one included while it
        follows a leader that copies its entry."""
        sharing = self.d.keyboards.sharing(self.keyboard)
        for keyboard in sharing:
            keyboard.apply_profile()
        self.d.keyboards.repaint_changed(
            {keyboard: view for keyboard, view in was.items() if keyboard not in sharing})

    def views(self) -> dict[Keyboard, tuple[ProfileLighting, int]]:
        """What each other keyboard driven shows (Keyboards.views), and this one while it
        follows, whose change paints nothing of its own: show_on_sharing's `was`."""
        views = self.d.keyboards.views(self.keyboard)
        if self.keyboard.dev is not None and self.following() is not None:
            views[self.keyboard] = self.d.per_keyboard.view(self.keyboard)
        return views

    def lighting_digest(self) -> str:
        """A short digest of the lighting in force, overlay first.

        The layer counts whole, including the mode, layout, colors and speeds that do not show:
        the window keeps them for the next mode switch and the Speed row. `color`, `effect` and
        `paint` answer with it and with `was`, the digest they were made on: the window takes
        the answer's as seen only when `was` is the one it last read, or a change made
        elsewhere since then would never be read. What shows (PerKeyboard.active_lighting)
        follows from the layer's fields.
        """
        games = self.d.games
        layer = self.d.per_keyboard.shown(keyboard=self.keyboard)
        # The model too: another keyboard attached shows another entry of the same files. The
        # layer as config.json keeps it (Lighting.to_json), so a field saved there counts here;
        # its keys sorted, so the order a paint listed its keys in does not move the digest.
        return digest([self.keyboard.model(), games.overlay.name if games.overlay else None,
                       json.dumps(layer.to_json(), sort_keys=True)])

    def _cmd_save_color(self, request: dict) -> dict:
        color = parse_color(request["value"], "save_color")
        self.d.check_config_writable()
        before = self.d.config.saved_colors
        if color not in before:
            self.d.save_setting(saved_colors=(before + [color])[-SAVED_COLORS_MAX:])
        return {"ok": True, "saved_colors": self.saved_colors()}

    def _cmd_forget_color(self, request: dict) -> dict:
        color = parse_color(request["value"], "forget_color")
        self.d.check_config_writable()
        before = self.d.config.saved_colors
        if color in before:
            self.d.save_setting(saved_colors=[c for c in before if c != color])
        return {"ok": True, "saved_colors": self.saved_colors()}

    def saved_colors(self) -> list[str]:
        """The kept colors as the front ends want them, newest last."""
        return [format_color(c) for c in self.d.config.saved_colors]

    def status(self) -> dict:
        """The keyboard's entries in `status`. The lighting state and the battery come first
        (Readings.status_readings), since either read can tear the keyboard down."""
        sw_control, battery = self.keyboard.readings.status_readings()
        # A keyboard that does not answer gives its last reading, with when it was taken.
        kept = self.keyboard.readings.kept_battery()
        lighting = self.keyboard.lighting
        return {
            "connected": self.keyboard.dev is not None,
            # A keyboard switched off behind its receiver stays connected, marked not answering.
            "answering": self.keyboard.keyboard_answering(),
            "device": str(self.keyboard.dev.path) if self.keyboard.dev else None,
            "name": self.keyboard.model_name(),
            "product": (f"{self.keyboard.keyboard_product:04x}"
                        if self.keyboard.keyboard_product else None),
            # A model not yet run on, whose note asks for a device report (report.untested).
            "untested": untested(self.keyboard.keyboard_product),
            # The window shows only these sections. `lighting` needs its clusters to enumerate:
            # a feature id does not prove it.
            "capabilities": self.keyboard.keyboard_able,
            # The window shows the idle rows on a keyboard that dims alone.
            "dims": self.dims(),
            "battery": battery if kept is None else kept[0],
            "battery_at": None if kept is None else kept[1],
            "saved_colors": self.saved_colors(),
            "brightness_levels": list(lighting.brightness_levels) if lighting else [],
            "claimed": bool(lighting and lighting.claimed),
            "sw_control": sw_control,
            "diverted": bool(self.keyboard.keys and self.keyboard.keys.diverted),
            # What Gearbench types for the unbound G keys of a keyboard whose G keys are keys
            # of its own, "F1 to F12" on the G915 TKL; else None.
            "typed": (typed_words(self.keyboard.keyboard_product)
                      if self.keyboard.dev is not None else None),
            "last_apply_error": self.keyboard.last_apply_error,
            **self.keyboard.key_control.status(),
            "clusters": [c.name for c in lighting.clusters] if lighting else [],
            # What `effect` takes on it, which the window's Effect row and `status` list.
            "effects": list(offered_effects(lighting.effect_clusters)) if lighting else [],
            # The level shown: the darker of the lock's and `idle_lights`.
            "lights": self.keyboard.lights,
            "idle_lights": self.keyboard.idle_lights,
            # What the board shows, either layer, in eight hex digits; a front end rereads the
            # files when it changes. `effect` and `color` miss a game profile's color and the
            # painted keys, so a stale copy would go back on the window's next click.
            "lighting": self.lighting_digest(),
        }

    def dims(self) -> bool | None:
        """Whether the keyboard's lighting dims while idle (keyboard_zones.power_modes), or None
        while its lighting is not known."""
        lighting = self.keyboard.lighting
        return None if lighting is None else power_modes(lighting)

    def problems(self) -> list[tuple[str, str, str | None]]:
        """The keyboard's entry for the daemon's `problems`, if it has one, as (code, message,
        the key of the model it is about: this keyboard's, Keyboard.model, or that of the
        keyboard on the node the look finds), as Mice.problems gives the mice's. Nothing is
        sent to the keyboard."""
        model = self.keyboard.model()
        if self.keyboard.dev is not None:
            if self.keyboard.keyboard_answering():
                return []
            return [("keyboard_not_answering",
                     (f"the {self.keyboard.model_name()} is not answering: "
                      f"asleep or switched off"), model)]
        node = self.d.per_keyboard.find_keyboard()
        if node is None:
            return [("keyboard_no_node", "no keyboard found; plug in its receiver or cable",
                     model)]
        # Ahead of the permission, since while the session is not active the node is the seat's,
        # and of another model's node found first.
        kept = self.kept_problem()
        if kept:
            return kept
        if any(keyboard.hold.held_onboard(node) is not None
               for keyboard in self.d.keyboards.controllers):
            # Another controller's keyboard, held: its kept_problem says so
            # (Keyboards.problems).
            return []
        # The node's model, not this keyboard's: with the leader's unplugged, the look finds
        # another model's node, whose own tab says it.
        found = model_key(node[1])
        if node_denied(node[0]):
            return [("keyboard_no_permission",
                     NODE_DENIED.format(node[0], keyboard_name(node[1])), found)]
        if node[1] in CABLED_IDS:
            return [("keyboard_no_answer",
                     (f"the {keyboard_name(node[1])} on {node[0]} does not answer: "
                      f"`{SERVICE_JOURNAL}` says why"), found)]
        return [("keyboard_no_answer",
                 (f"the {keyboard_name(node[1])} does not answer its receiver on {node[0]}: "
                  f"asleep, switched off or out of range"), found)]

    def kept_problem(self) -> list[tuple[str, str, str | None]]:
        """The entry of this keyboard held on its own profile, or in HOST mode by a hand-back
        cut short (OnboardHold.held_problem), or none, as `problems` gives it: a controller
        holds only a node of its own model."""
        held = self.keyboard.hold.held_problem()
        return [] if held is None else [("keyboard_kept_onboard", held, self.keyboard.model())]

    def _cmd_idle(self, request: dict) -> dict:
        """Idle dimming: `dim_after` and `off_after` in seconds and `never_dim`, any of them,
        each checked before anything changes; with none, it reads them. The reply says whether
        the keyboard dims (`dims`, as `status` has it) and names it, so a read on one that
        never dims can say so."""
        changes: dict[str, object] = {}
        for name in ("dim_after", "off_after"):
            if name in request:
                problem = idle_problem(request[name])
                if problem is not None:
                    return {"ok": False, "error": f"{name} {problem}"}
                changes[f"idle_{name}"] = request[name]
        if "never_dim" in request:
            changes["never_dim"] = switch_value(request["never_dim"], "never_dim")
        note = None
        if changes:
            before = self.keyboard.idle.idle_settings()
            self.d.save_setting(**changes)
            note = self.d.keyboards.idle_changed(before)
        dim_after, off_after, never_dim = self.keyboard.idle.idle_settings()
        return with_note({"ok": True, "idle_dim_after": dim_after, "idle_off_after": off_after,
                          "never_dim": never_dim, "idle_lights": self.keyboard.idle_lights,
                          "lights": self.keyboard.lights, "dims": self.dims(),
                          "name": self.keyboard.model_name()}, note)
