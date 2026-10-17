"""The keyboard's commands: the M profile switch and its name, the brightness, the color, the
effect, a drag's preview, the layout editor's paint, the saved colors and the idle dimming.
With them the digest of the lighting in force and the saved colors as the front ends want
them, and the keyboard's entries in `status` and its problem for `problems`."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from ...config import (
    PROFILE_IDS,
    SAVED_COLORS_MAX,
    ConfigError,
    direction_problem,
    direction_shown,
    expect,
    format_color,
    idle_problem,
    parse_color,
    speed_problem,
    speed_shown,
)
from ...hidpp import NO_RULE, HidppFailure
from ...keyboard import CABLED_IDS, find_attached_keyboard, name as keyboard_name
from ...keycodes import LOGO, ZONE_NAMES
from ...lighting import (
    ANIMATIONS,
    KEYBOARD_EFFECTS,
    MAX_ZONE,
    MIN_ZONE,
    Lighting,
    LightingError,
    snap_brightness,
)
from ...profiles import PER_KEY, STATIC
from ..shared import (
    DARK,
    DIM,
    LIT,
    NOT_AN_M_PROFILE,
    digest,
    name_text,
    node_denied,
    on_or_off,
    switch_refusal,
    unanswered,
    whole_number,
    with_note,
)
from .paint import LED_SETTLE, NO_LIGHTING, unanswered_note

if TYPE_CHECKING:
    from .. import Daemon
    from . import Keyboard

log = logging.getLogger("gearbench")

#: Said of a keyboard with zone lighting alone.
NO_PER_KEY = "this keyboard has no per-key lighting"


def parse_zones(raw: object, field: str) -> dict[int, int]:
    """`paint`'s and `preview`'s `zones`: zone id -> color, none for an absent or null one.
    TypeError and ValueError are a bad request."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise TypeError(f"{field} must be an object of zone ids and colors")
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
            return {"ok": False, "error": NOT_AN_M_PROFILE}
        if self.d.config.switches_to(index):
            self.keyboard.wake_for_change()
        failed = self.keyboard.switch_profile(index)
        return with_note({"ok": True, "profile": self.d.config.active}, failed,
                         self.keyboard.last_apply_detail)

    def _cmd_rename(self, request: dict) -> dict:
        # Text, as config.json reads a name: str() would name the profile "None" for a null.
        expect(request["value"], str, "rename", "text")
        name = request["value"].strip()
        if not name:
            return {"ok": False, "error": "a profile needs a name"}
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
            return {"ok": False, "error": NO_LIGHTING}
        if not 0 <= level <= 100:
            return {"ok": False, "error": f"brightness {level} outside 0..100"}
        self.d.check_config_writable()
        # The wake paints at full first: the brightness write alone would leave a board woken
        # from its idle dim at half its colors.
        self.keyboard.wake_for_change(paint=True)
        failed = detail = None
        if (self.keyboard.lighting is not None and self.keyboard.keyboard_answering()
                and self.keyboard.lights == LIT):
            before = self.d.config.brightness
            try:
                with self.keyboard.keyboard_writing():
                    written = self.keyboard.lighting.set_brightness(level)
            except (HidppFailure, LightingError) as exc:
                if not unanswered(exc):
                    raise
                # No answer (marked by keyboard_writing): saved, owed below unless the cable
                # look dropped the receiver route, and said as not_shown says it.
                log.info("brightness %d not written: %s", level, exc)
                failed = unanswered_note(exc, self.keyboard.dev is None)
                detail = str(exc)
            else:
                self.d.config.brightness = written
                self.commit_lighting(lambda: setattr(self.d.config, "brightness", before),
                                     to_config=True)
                self.d.mice.color.sync_mouse_color(self.keyboard.shown_color())
                return {"ok": True, "requested": level, "applied": written,
                        "levels": list(self.keyboard.lighting.brightness_levels)}
        # Saved, and written by apply_profile when the keyboard or the lights are back; never to a
        # keyboard marked not answering, whose timeouts would hold the loop. Snapped as
        # set_brightness snaps where the levels are known; else apply_profile saves the level the
        # keyboard keeps.
        levels = self.keyboard.lighting.brightness_levels if self.keyboard.lighting else ()
        applied = snap_brightness(levels, level)
        self.d.save_setting(brightness=applied)
        unreached = self.keyboard.change_unreached()
        # A synced mouse shows the saved level (at_level), the keyboard away or not.
        self.d.mice.color.sync_mouse_color(self.keyboard.shown_color())
        return with_note({"ok": True, "requested": level, "applied": applied,
                          "levels": list(levels)},
                         failed or unreached or self.keyboard.lights_note(), detail)

    def _cmd_color(self, request: dict) -> dict:
        """Set the color and save it, on the loaded game profile when there is one, since that
        is what shows."""
        color = parse_color(request["value"], "color")
        # The window sends "mode": "static" with every color in Static mode: the layer goes
        # Static, which turns a disabled board back on and stops an animation, its layout and
        # background kept. Without it (the CLI) the color goes to what shows; breathe and ripple
        # take the Static color.
        static = request.get("mode") == STATIC
        self.check_preview_writable()
        was = self.lighting_digest()
        layer = self.d.games.lighting_layer()
        before = (layer.color, layer.background, layer.mode, layer.effect)
        if static:
            layer.color, layer.mode, layer.effect = color, STATIC, "static"
        elif layer.mode == PER_KEY and layer.effect not in ANIMATIONS:
            layer.background = color
        else:
            layer.color = color

        def undo() -> None:
            layer.color, layer.background, layer.mode, layer.effect = before

        return self.show_layer_change({"ok": True, "color": format_color(color)}, undo, was)

    def _cmd_effect(self, request: dict) -> dict:
        """Set the effect the board shows and save it, on the loaded game profile or the M
        profile.

        `speed`, an animation's period in milliseconds, and wave's `direction` are saved for
        that animation, so an effect named without them runs at what was saved, or the
        default. The mode is left alone: `effect static` brings back whichever of Static and
        Per-key the layer was on.
        """
        name = name_text(request["value"], "effect")
        if name not in KEYBOARD_EFFECTS:
            return {"ok": False, "error": f"no effect named {name!r}; the keyboard "
                                          f"effects are {', '.join(KEYBOARD_EFFECTS)}"}
        speed, direction = request.get("speed"), request.get("direction")
        if speed is not None and (problem := speed_problem(name, speed)) is not None:
            return {"ok": False, "error": problem}
        if direction is not None:
            if name != "wave":
                return {"ok": False, "error": f"only wave has a direction; {name} has none"}
            if (problem := direction_problem(direction)) is not None:
                return {"ok": False, "error": problem}
        self.d.check_layer_writable()
        was = self.lighting_digest()
        layer = self.d.games.lighting_layer()
        before = (layer.effect, dict(layer.speeds), layer.direction)
        layer.effect = name
        if speed is not None:
            layer.speeds[name] = speed
        if direction is not None:
            layer.direction = direction

        def undo() -> None:
            layer.effect, layer.speeds, layer.direction = before

        reply: dict = {"ok": True, "effect": name}
        if name in ANIMATIONS:
            settings = layer.settings()
            reply["speed"] = speed_shown(name, settings)
            if name == "wave":
                reply["direction"] = direction_shown(settings)
        return self.show_layer_change(reply, undo, was)

    def show_layer_change(self, reply: dict, undo: Callable[[], None], was: str) -> dict:
        """Show and save a change made to the layer in force, and answer `reply` with the overlay
        it went to and the digests (lighting_digest)."""
        self.keyboard.wake_for_change()
        failed = self.keyboard.apply_profile() or self.keyboard.no_lighting_note()
        self.commit_lighting(undo)
        if self.d.games.overlay is not None:
            reply["overlay"] = self.d.games.overlay.name
        reply["lighting"], reply["was"] = self.lighting_digest(), was
        return with_note(reply, failed, self.keyboard.last_apply_detail)

    def _cmd_preview(self, request: dict) -> dict:
        """Live preview while a color is dragged, or, with `paint`'s `base` and `zones`, while a
        drag paints keys (_preview_keys).

        Per-key frames alone, at most one for a color, without the zone effects and M/MR LEDs an
        apply writes, which would make a drag lag. A pending LED write keeps its time: it waits
        out zone effects, and a per-key frame is not one. Nothing is saved: the release sends
        `color`. A layer the release would be refused on is refused here first
        (check_preview_writable).
        """
        if self.keyboard.dev is not None and self.keyboard.lighting is None:
            return {"ok": False, "error": NO_LIGHTING}
        if "zones" in request:
            return self._preview_keys(request)
        color = parse_color(request["value"], "preview")
        self.check_preview_writable()
        # The wake paints the board at full first, LEDs included: the fill leaves the M and MR
        # LEDs as a dark left them, and under an animation writes nothing.
        self.keyboard.wake_for_change(paint=True)
        # After the wake's apply, which drops the last one.
        self.keyboard.last_preview = request
        reply = {"ok": True, "color": format_color(color)}
        # Nothing to a keyboard marked not answering, whose timeouts would hold the loop on
        # every frame of the drag, nor while the lights are down; a synced mouse still follows
        # the drag. A disabled board is drawn (Paint.shows_static): the release turns it
        # static.
        if self.keyboard.lighting is not None and self.keyboard.lights != LIT:
            reply["note"] = self.keyboard.lights_note(saved=False)
        elif (self.keyboard.lighting is not None and self.keyboard.lighting.per_key
              and self.keyboard.keyboard_answering()
              and self.keyboard.paint.shows_static(self.d.games.active_lighting()[0])):
            # Every zone, the logo included: a per-key frame does not make the logo re-ramp
            # and flicker as a zone-effect write does.
            with self.keyboard.keyboard_writing():
                self.keyboard.lighting.fill(color, last=LOGO)
        self.d.mice.color.sync_mouse_color(color, repeat=False)
        return reply

    def check_preview_writable(self) -> None:
        """check_layer_writable for a preview and for its release, `color` or `paint`, so a drag
        on a layer that would refuse the release shows no frame nothing can save. A drag with a
        preview accepted (last_preview), its layer refused since, gets the lighting the daemon
        holds back first, whether or not that preview showed a frame, and whether the refusal
        comes at a later frame or at the release."""
        try:
            self.d.check_layer_writable()
        except ConfigError:
            if self.keyboard.last_preview is not None:
                self.keyboard.apply_profile()
            raise

    def _preview_keys(self, request: dict) -> dict:
        """`preview` with `paint`'s `base` and `zones`: a drag on the window's board.

        The whole board each time, since the window replaces a `preview` still waiting with the
        next and a part would be lost: fill_and_paint, one per-key frame unless the zones pass
        its report cap. Nothing is saved: the release sends `paint`. A synced mouse is written
        only for a base other than the one showing.
        """
        if self.keyboard.lighting is not None and not self.keyboard.lighting.per_key:
            return {"ok": False, "error": NO_PER_KEY}
        base = parse_color(request.get("base", "#000000"), "preview.base")
        zones = parse_zones(request.get("zones"), "preview.zones")
        # The frame's own range (Lighting._pack), checked before the wake writes anything. An id
        # no key has is painted; `paint` refuses it, since only keys are saved.
        for zone in zones:
            if not MIN_ZONE <= zone <= MAX_ZONE:
                return {"ok": False, "error": f"preview.zones.{zone}: no such zone"}
        self.check_preview_writable()
        reply = {"ok": True, "base": format_color(base), "zones": len(zones)}
        mouse = base != self.keyboard.shown_color()
        self.keyboard.wake_for_change(paint=True)
        self.keyboard.last_preview = request
        if self.keyboard.lighting is not None and self.keyboard.lights != LIT:
            reply["note"] = self.keyboard.lights_note(saved=False)
        elif (self.keyboard.lighting is not None and self.keyboard.keyboard_answering()
              and self.keyboard.paint.shows_static(self.d.games.active_lighting()[0])):
            # A disabled board is drawn (Paint.shows_static): the release's paint turns it
            # static.
            with self.keyboard.keyboard_writing():
                self.keyboard.lighting.fill_and_paint(base, zones, last=LOGO)
        if mouse:
            self.d.mice.color.sync_mouse_color(base, repeat=False)
        return reply

    def _cmd_paint(self, request: dict) -> dict:
        """A base color for everything, then zone overrides on top: what the layout editor sends.

        Saved first, even with the keyboard away or not answering, then shown through the
        write a static apply uses (Paint.write_static). A failed write is answered as
        apply_profile answers it. A zone no key has is refused, since only the keys are stored.
        """
        if self.keyboard.dev is not None and self.keyboard.lighting is None:
            return {"ok": False, "error": NO_LIGHTING}
        if self.keyboard.lighting is not None and not self.keyboard.lighting.per_key:
            return {"ok": False, "error": NO_PER_KEY}
        base = parse_color(request.get("base", "#000000"), "paint.base")
        zones = parse_zones(request.get("zones"), "paint.zones")
        for zone in zones:
            if zone not in ZONE_NAMES:
                return {"ok": False, "error": f"paint.zones.{zone}: no key has that zone"}
        self.check_preview_writable()
        was = self.lighting_digest()
        keys = {ZONE_NAMES[z]: c for z, c in zones.items()}
        layer = self.d.games.lighting_layer()
        before = (layer.mode, layer.background, layer.keys, layer.effect)
        # `base` is the background. The Static color is kept for the next switch to Static.
        layer.mode, layer.background, layer.keys, layer.effect = PER_KEY, base, keys, "static"

        def undo() -> None:
            layer.mode, layer.background, layer.keys, layer.effect = before

        self.commit_lighting(undo)
        # A drag's release, or a click after it: no drag frame to send again over the saved
        # paint at a wake repaint (tend_idle).
        self.keyboard.last_preview = None
        reply = {"ok": True, "base": format_color(base), "zones": len(zones),
                 "lighting": self.lighting_digest(), "was": was}
        woke = self.keyboard.wake_for_change()
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
                self.keyboard.paint.write_static(cast("Lighting", self.keyboard.lighting),
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
        self.d.mice.color.sync_mouse_color(base)
        return reply

    def commit_lighting(self, undo: Callable[[], None], to_config: bool = False) -> None:
        """Save the layer a lighting change went to, or undo the change.

        A failed save puts memory back and repaints what is saved, or the next unrelated save
        would write the change; the error goes on to the client. `to_config` saves
        config.json whatever is loaded: the brightness lives there.
        """
        try:
            if to_config:
                self.d.save_config()
            else:
                self.d.save_layer_file()
        except BaseException:
            undo()
            # apply_profile writes nothing to a keyboard marked not answering.
            self.keyboard.apply_profile()
            raise

    def lighting_digest(self) -> str:
        """A short digest of the lighting in force, overlay first.

        The layer counts whole, including the mode, layout, colors and speeds that do not show:
        the window keeps them for the next mode switch and the Speed row. `color`, `effect` and
        `paint` answer with it and with `was`, the digest they were made on: the window takes
        the answer's as seen only when `was` is the one it last read, or a change made
        elsewhere since then would never be read. What shows (Games.active_lighting) follows
        from the layer's fields.
        """
        games = self.d.games
        layer = games.lighting_layer()
        return digest([
            games.overlay.name if games.overlay else None, layer.effect,
            sorted(layer.settings().items()), layer.mode, sorted(layer.keys.items()),
            layer.color, layer.background, sorted(layer.speeds.items()), layer.direction])

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
        """The keyboard's entries in `status`. The lighting state is asked first, since it finds
        a silent keyboard cheaply and the battery is then not asked; both before the rest, since
        either can tear the keyboard down."""
        sw_control = self.keyboard.keyboard_sw_control()
        battery = self.keyboard.keyboard_battery()
        lighting = self.keyboard.lighting
        return {
            "connected": self.keyboard.dev is not None,
            # A keyboard switched off behind its receiver stays connected.
            "answering": self.keyboard.keyboard_answering(),
            "device": str(self.keyboard.dev.path) if self.keyboard.dev else None,
            "name": keyboard_name(self.keyboard.keyboard_product),
            "product": (f"{self.keyboard.keyboard_product:04x}"
                        if self.keyboard.keyboard_product else None),
            # The window shows only these sections. `lighting` needs its clusters to enumerate:
            # a feature id does not prove it.
            "capabilities": list(self.keyboard.keyboard_able),
            "battery": battery,
            "saved_colors": self.saved_colors(),
            "brightness_levels": list(lighting.brightness_levels) if lighting else [],
            "claimed": bool(lighting and lighting.claimed),
            "sw_control": sw_control,
            "diverted": bool(self.keyboard.keys and self.keyboard.keys.diverted),
            "last_apply_error": self.keyboard.last_apply_error,
            "clusters": [c.name for c in lighting.clusters] if lighting else [],
            # The level shown: the darker of the lock's and `idle_lights`.
            "lights": self.keyboard.lights,
            "idle_lights": self.keyboard.idle_lights,
            # What the board shows, either layer, in eight hex digits; a front end rereads the
            # files when it changes. `effect` and `color` miss a game profile's color and the
            # painted keys, so a stale copy would go back on the window's next click.
            "lighting": self.lighting_digest(),
        }

    def problems(self) -> list[tuple[str, str]]:
        """The keyboard's entry for the daemon's `problems`, if it has one, as (code, message),
        as Mice.problems gives the mice's. Only sysfs is read: nothing is sent to the keyboard."""
        if self.keyboard.dev is not None:
            if self.keyboard.keyboard_answering():
                return []
            return [("keyboard_not_answering",
                     (f"the {keyboard_name(self.keyboard.keyboard_product)} is not answering: "
                      f"asleep or switched off"))]
        node = find_attached_keyboard()
        if node is None:
            return [("keyboard_no_node", "no keyboard found; plug in its cable or its receiver")]
        if node_denied(node[0]):
            return [("keyboard_no_permission",
                     f"no permission to open {node[0]}, the keyboard's node; {NO_RULE}")]
        if node[1] in CABLED_IDS:
            return [("keyboard_no_answer",
                     (f"the {keyboard_name(node[1])} on {node[0]} does not answer: "
                      f"journalctl --user -u gearbench says why"))]
        return [("keyboard_no_answer",
                 (f"the {keyboard_name(node[1])} does not answer its receiver on {node[0]}: "
                  f"asleep, switched off or out of range"))]

    def _cmd_idle(self, request: dict) -> dict:
        """Idle dimming: `dim_after` and `off_after` in seconds and `never_dim`, any of them,
        each checked before anything changes; with none, it reads them."""
        changes: dict[str, object] = {}
        for name in ("dim_after", "off_after"):
            if name in request:
                problem = idle_problem(request[name])
                if problem is not None:
                    return {"ok": False, "error": f"{name} {problem}"}
                changes[f"idle_{name}"] = request[name]
        if "never_dim" in request:
            value = on_or_off(request["never_dim"])
            if value is None:
                return {"ok": False, "error": switch_refusal("never_dim", request["never_dim"])}
            changes["never_dim"] = value
        note = None
        if changes:
            before = self.keyboard.idle_settings()
            self.d.save_setting(**changes)
            note = self.keyboard.idle_changed(before)
        dim_after, off_after, never_dim = self.keyboard.idle_settings()
        return with_note({"ok": True, "idle_dim_after": dim_after, "idle_off_after": off_after,
                          "never_dim": never_dim, "idle_lights": self.keyboard.idle_lights,
                          "lights": self.keyboard.lights}, note)
