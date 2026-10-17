"""The mice's lighting: the claim, a mouse's own color or effect, the keyboard's color on a
synced mouse, and the lighting given back after a power-on, a silence or a DPI flash. A
mouse that may be asleep is sent its lighting without waiting for the replies, one write in
flight at a time, settled as they arrive (`check_mouse_write`). With them is the read of a
mouse's HID++ node (`read_node`), which that check shares with Mice.pump_mouse."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from ...config import format_color, parse_color, reason
from ...hidpp import DeviceGone, Heard, HidppError, HidppFailure
from ...lighting import LightingError
from ...mouse import MouseError
from ..shared import name_text, on_or_off, scaled, switch_refusal
from .attached import AttachedMouse, SentColor, effect_names, no_mouse

if TYPE_CHECKING:
    from .. import Daemon
    from . import Mice

log = logging.getLogger("gearbench")

# How long a color sent without waiting has to be answered: a waiting request's budget, three
# tries of 1 s.
MOUSE_WRITE_DEADLINE = 3.0

# Refusal for the mouse's own color commands while it follows the keyboard.
SYNC_ON = ("the mouse follows the keyboard color; turn off Sync with keyboard (or "
           "`gearbench-ctl mouse-sync off`) first")


class MouseLighting:
    """The lighting of every attached mouse that has any, and the read of each attached
    mouse's node (read_node)."""

    def __init__(self, daemon: Daemon, mice: Mice) -> None:
        self.d = daemon
        self.mice = mice

    def commands(self) -> dict[str, Callable[[dict], dict]]:
        return {
            "mouse_sync": self._cmd_mouse_sync,
            "mouse_color": self._cmd_mouse_color,
            "mouse_effect": self._cmd_mouse_effect,
        }

    def claim_and_paint(self, attached: AttachedMouse) -> bool:
        """The lighting claim, then the mouse's lighting (give_back_lighting).
        True when both took, or there is no lighting."""
        if attached.lighting is None:
            return True
        try:
            self.mice.mouse_call(attached.lighting.claim, target=attached)
        except (MouseError, OSError) as exc:
            self.say_lighting_back_failed(attached, exc)
            return False
        return self.give_back_lighting(attached)

    def give_back_lighting(self, attached: AttachedMouse) -> bool:
        """Write the mouse's lighting: synced, else its own color or effect (from _mouse_kept
        at attach). Answers whether it took. A mouse not answering is owed it (tend_mice)."""
        if attached.lighting is None:
            return True
        if attached.silent:
            attached.owed_lighting = True
            return False
        attached.owed_lighting = False
        if self.d.config.mouse_sync_color:
            took = self.sync_mouse_color(self.d.keyboard.shown_color(), target=attached)
        else:
            took = True
            try:
                if attached.effect is not None:
                    self.write_mouse_effect(attached.effect, attached.color or 0, attached)
                elif attached.color is not None and attached.showing != attached.color:
                    self.write_mouse_color(attached.color, target=attached)
            except (LightingError, MouseError, OSError) as exc:
                self.say_lighting_back_failed(attached, exc)
                took = False
        if attached.silent:
            attached.owed_lighting = True
        return took

    def say_lighting_back_failed(self, attached: AttachedMouse, exc: Exception) -> None:
        """Log why the claim or the lighting did not take: at debug for a mouse gone or not
        answering, which mouse_call has said at info."""
        level = logging.INFO if self.mice.mouse_answering(attached) else logging.DEBUG
        log.log(level, "could not give %s its lighting back: %s", attached.name, reason(exc))

    def write_mouse_color(self, color: int, target: AttachedMouse, repeat: bool = True,
                          own: bool = True) -> None:
        """Static color on every cluster of `target` that has it, then the per-key fill: the
        write mouse_color and the keyboard sync share. `own` False is a synced write, and
        `repeat` False a drag's preview, not counted as shown, so the release writes its color
        again. A failure raises mouse_call's MouseError, or a LightingError the write raises.

        A mouse that may be asleep is sent the color without waiting (send_mouse_color), one
        write in flight at a time: a color asked for meanwhile is owed, but a preview is
        dropped: owed, the stored color would go out between two previews.
        """
        lighting = target.its_lighting
        if target.sent is not None:
            if repeat:
                target.owed_lighting = True
        elif target.may_be_asleep():
            self.send_mouse_color(target, color, repeat)
        else:
            def write() -> None:
                for cluster in lighting.clusters:
                    if cluster.has_effect("static"):
                        lighting.set_effect(cluster.index, "static", wait=repeat, color=color)
                lighting.fill(color)

            # Unknown until it has taken, and after a preview.
            target.showing = None
            self.mice.mouse_call(write, target=target)
            if repeat:
                target.showing = color
        if own:
            # Not for a synced write: the keyboard's color would replace the mouse's own effect.
            target.color, target.effect = color, None

    def send_mouse_color(self, target: AttachedMouse, color: int, repeat: bool) -> None:
        """write_mouse_color for a mouse that may be asleep: the static effects and one per-key
        frame, sent without waiting, their replies checked as they arrive (check_mouse_write)."""
        lighting = target.its_lighting

        def send() -> tuple[tuple[int, int, int], ...]:
            effects = tuple(lighting.send_effect(cluster.index, "static", color=color)
                            for cluster in lighting.clusters if cluster.has_effect("static"))
            return effects + lighting.send_fill(color)

        target.showing = None
        replies = self.mice.mouse_call(send, target=target, answered=False)
        target.sent = SentColor(color, replies, time.monotonic() + MOUSE_WRITE_DEADLINE,
                                repeat)

    def check_mouse_write(self, attached: AttachedMouse) -> None:
        """Settle a color sent without waiting, once its answers are in or its deadline passed.

        Answered, the mouse is answering and a committed color counts as shown. Unanswered at
        the deadline, or answered by its receiver that it is out of reach, it is marked not
        answering and owed the lighting (tend_mice); one heard from since the send is awake and
        its reply was lost, so it is owed but not marked. An error reply is a refusal, logged
        and not owed. An effect goes out with `repeat` False, so it never counts as shown.
        """
        sent = attached.sent
        if sent is None:
            return
        answers = [attached.dev.answer(key) for key in sent.replies]
        # One reading for both tests below: a deadline passed between two would settle an
        # answer left on the node as no answer, the node not read.
        due = time.monotonic() >= sent.deadline
        if None in answers and due:
            # A handler that held the loop past the deadline leaves the answer unread on the
            # node. Read by read_node: pump_mouse would settle this write again (mouse_events).
            if self.read_node(attached) is None:
                return
            answers = [attached.dev.answer(key) for key in sent.replies]
        refused = next((a for a in answers if isinstance(a, HidppError)), None)
        if refused is None and None in answers and not due:
            return
        attached.dev.forget(*sent.replies)
        attached.sent = None
        if refused is None and None in answers:
            attached.owed_lighting = True
            if attached.heard_at > sent.deadline - MOUSE_WRITE_DEADLINE:
                log.debug("%s did not answer its lighting, but was heard since; sent again",
                          attached.name)
                return
            log.info("%s did not answer its lighting within %.0f s; it is owed it",
                     attached.name, MOUSE_WRITE_DEADLINE)
            self.mice.mark_silent(attached)
            return
        if refused is not None and refused.unreachable:
            log.info("%s is switched off or out of range; it is owed its lighting (%s)",
                     attached.name, refused)
            self.mice.mark_silent(attached)
            attached.owed_lighting = True
            return
        self.mice.mark_answering(attached)
        if refused is not None:
            log.info("%s refused its lighting: %s", attached.name, refused)
            return
        if sent.repeat:
            attached.showing = sent.color

    def read_node(self, attached: AttachedMouse) -> Heard | None:
        """Read what one mouse's HID++ node has buffered (Hidpp.pump); a report that is not
        HID++ marks the mouse answering. None once a node that has gone or failed has torn the
        mouse down."""
        try:
            heard = attached.dev.pump()
        except DeviceGone as exc:
            log.info("%s went away: %s", attached.name, exc)
            self.mice.teardown_mouse(attached)
            return None
        except HidppFailure as exc:
            # Any other errno, which hidpp reports as a HidppFailure: the handle is done with,
            # and the retry builds a new one.
            log.warning("%s's node failed (%s); reconnecting", attached.name, exc)
            self.mice.teardown_mouse(attached)
            return None
        if heard.input:
            # Motion through its receiver, or a keystroke of a keyboard on the same receiver
            # (Heard): taken for the mouse awake. One marked silent is marked answering, so
            # tend_mice gives it no recheck: the next full pass gives it the lighting and
            # tuning it is owed, and a full setup only if it was cut_short.
            self.mice.mark_answering(attached)
        return heard

    def sync_mouse_color(self, color: int, repeat: bool = True,
                         target: AttachedMouse | None = None) -> bool:
        """Push the keyboard's base color to every attached mouse with lighting, or only
        `target`, when sync is on. Answers whether every write took.

        Never raises into the keyboard color paths that call it: a mouse missing or asleep
        mid-write is skipped. A preview's effect writes are not waited for, so a push per
        preview is cheap. `repeat` False is a preview (write_mouse_color).
        """
        if not self.d.config.mouse_sync_color:
            return True
        # Every path to a synced mouse's color comes here, so its level is applied here.
        color = self.d.keyboard.at_level(color)
        took = True
        for attached in [target] if target is not None else list(self.mice.attached):
            if attached.lighting is None:
                continue
            if attached.silent:
                # Nothing is written to it unasked until it answers: owed instead.
                attached.owed_lighting = True
                took = False
                continue
            if attached.showing == color:
                # Already showing it: a write would wait for an idle mouse to wake.
                continue
            try:
                self.write_mouse_color(color, repeat=repeat, target=attached, own=False)
            except (LightingError, MouseError, OSError) as exc:
                if not self.mice.mouse_answering(attached) or not repeat:
                    # mouse_call said it is gone or not answering; a drag sends many previews.
                    log.debug("mouse color sync skipped for %s: %s", attached.name, exc)
                else:
                    log.info("could not give %s the keyboard's color: %s", attached.name, reason(exc))
                took = False
        return took

    def write_mouse_effect(self, name: str, color: int, target: AttachedMouse) -> list[str]:
        """An effect on every cluster of `target` that has it (the zone and per-key clusters
        have different ones); answers the clusters written. Recorded like a color, so a power
        cycle puts back the effect chosen last. Waited for on an awake mouse, sent without
        waiting to one that may be asleep (check_mouse_write), owed while a write is in flight."""
        lighting = target.its_lighting
        clusters = [c for c in lighting.clusters if c.has_effect(name)]
        if not clusters:
            return []
        writes = [(c.index, lighting.effect_params(c.index, name, color)) for c in clusters]
        target.forget_showing()     # an effect: no color is shown
        if target.sent is not None:
            target.owed_lighting = True
        elif target.may_be_asleep():
            replies = self.mice.mouse_call(
                lambda: tuple(lighting.send_effect(index, name, **params)
                                   for index, params in writes),
                target=target, answered=False)
            target.sent = SentColor(color, replies, time.monotonic() + MOUSE_WRITE_DEADLINE,
                                    False)
        else:
            def write() -> None:
                for index, params in writes:
                    lighting.set_effect(index, name, wait=True, **params)
            self.mice.mouse_call(write, target=target)
        # Only for an effect that takes a color: for cycle and wave the request's default would
        # become the mouse's own color.
        if any("color" in params for _index, params in writes):
            target.color = color
        target.effect = name
        return [c.name for c in clusters]

    def sync_changed(self, on: bool, color: int, paint: bool = True) -> None:
        """Act on the sync switch set `on` or off; `color` is the keyboard's shown color. On:
        every mouse takes it, unless `paint` is False (the caller's apply_profile paints). Off:
        each mouse gets its own lighting back; one with none takes `color` at the keyboard's
        brightness as its own (adopt_color), written only when it is known to show another
        (dimmed or dark). Either way the desktop's idleness is followed as the switch says
        (Session.watch_idle_for_both)."""
        self.d.session.watch_idle_for_both(paint=False)
        if on:
            if paint:
                self.sync_mouse_color(color)
            return
        for attached in [m for m in self.mice.attached if m.lighting is not None]:
            if self.adopt_color(attached, color) and attached.showing in (None, attached.color):
                continue
            self.give_back_lighting(attached)

    def adopt_color(self, attached: AttachedMouse, color: int) -> bool:
        """A mouse with no color or effect of its own takes `color`, the keyboard's shown color,
        at the keyboard's brightness as its own: what color sync off gives it. True when it
        took it."""
        if attached.color is not None or attached.effect is not None:
            return False
        attached.color = scaled(color, self.d.config.brightness)
        return True

    def repaint_after_flash(self, attached: AttachedMouse) -> None:
        """The DPI flash is over and ended dark (Dpi.after_dpi_write): the lighting is owed
        again, whatever `showing` says."""
        attached.repaint_at = None
        attached.forget_showing()
        if not self.d.config.mouse_sync_color:
            # One with nothing of its own to give back adopts as the color sync off does, not dark.
            self.adopt_color(attached, self.d.keyboard.shown_color())
        attached.owed_lighting = True
        log.debug("%s's DPI flash is over; its lighting is owed again", attached.name)

    def mouse_with_lights(self) -> bool:
        """Whether a mouse with lights is attached: one a sync can color."""
        return any(m.lighting is not None for m in self.mice.attached)

    def no_mouse_lighting(self) -> dict | None:
        """The refusal for a lighting command on the selected mouse, or None: no mouse, a mouse
        with no lights, such as the G305, or sync on, which only a mouse with lights follows."""
        current = self.mice.current
        if current is None:
            return {"ok": False, "error": no_mouse(self.mice.denied_mice())}
        if current.lighting is None:
            return {"ok": False, "error": f"{current.name} has no lighting"}
        if self.d.config.mouse_sync_color:
            return {"ok": False, "error": SYNC_ON}
        return None

    def _cmd_mouse_sync(self, request: dict) -> dict:
        value = on_or_off(request.get("value"))
        if value is None:
            return {"ok": False, "error": switch_refusal("mouse_sync", request.get("value"))}
        # Saved before it acts: a refused save changes nothing.
        was = self.d.config.mouse_sync_color
        self.d.save_setting(mouse_sync_color=value)
        color = self.d.keyboard.shown_color()
        if value or was:
            self.sync_changed(value, color)
        reply: dict = {"ok": True, "mouse_sync_color": value,
                       "mouse": self.mouse_with_lights()}
        if value:
            # The color the mice now follow; off, each shows its own, which the `mouse`
            # command gives.
            reply["color"] = format_color(color)
        return reply

    def _cmd_mouse_color(self, request: dict) -> dict:
        if (refused := self.no_mouse_lighting()) is not None:
            return refused
        color = parse_color(request["value"], "mouse_color")
        # no_mouse_lighting found the selected mouse and its lighting.
        self.write_mouse_color(color, cast(AttachedMouse, self.mice.current))
        return {"ok": True, "color": format_color(color)}

    def _cmd_mouse_effect(self, request: dict) -> dict:
        if (refused := self.no_mouse_lighting()) is not None:
            return refused
        # no_mouse_lighting found the selected mouse and its lighting.
        mouse = cast(AttachedMouse, self.mice.current)
        name = name_text(request["value"], "mouse_effect")
        color = parse_color(request.get("color", "#0080ff"), "mouse_effect.color")
        clusters = self.write_mouse_effect(name, color, mouse)
        if not clusters:
            has = ", ".join(effect_names(mouse.its_lighting.clusters))
            return {"ok": False, "error": f"{mouse.name} has no effect {name!r}; "
                                          f"its effects are {has}"}
        return {"ok": True, "effect": name, "clusters": clusters}
