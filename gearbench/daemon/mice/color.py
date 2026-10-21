"""The mice's lighting: which a mouse has (`find_lighting`), the claim and its release, a
mouse's own color or effect, the keyboard's color on a synced mouse, and the lighting given
back after a power-on, a silence or a DPI flash. A mouse that waits to claim (WAITS_TO_CLAIM)
is released once at its attach when it has nothing to show, claimed with its first lighting
write, and released once it has none to show again. A mouse
that may be asleep is sent its lighting without waiting for
the replies, one write in flight at a time, settled as they arrive (`check_mouse_write`).
With them is the read of a mouse's HID++ node (`read_node`), which that check shares with
Mice.pump_mouse."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, TypeGuard

from ...config import format_color, parse_color
from ...hidpp import DeviceGone, Heard, HidppError, HidppFailure
from ...lighting import Lighting, LightingError
from ...mouse import (
    CLAIM_EACH_EFFECT,
    EFFECT_NAMES,
    LIGHTING_TESTED,
    WAITS_TO_CLAIM,
    ZONE_LIGHTING,
    Mouse,
    MouseError,
)
from ...mouse_lighting import MouseLights, ZoneLighting
from ...wording import reason
from ..shared import failure_reason, name_text, on_or_off, switch_refusal, with_note
from .attached import AttachedMouse, SentColor, effect_names

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
#: Why color sync leaves a mouse as it is, by its name: the journal's line, said once an
#: attach (sync_mouse_color), and the note of `mouse_sync` turned on.
NOT_SYNCED = "the {} has no lighting Gearbench drives, so color sync leaves it as it is"


def takes_fill(lighting: MouseLights) -> TypeGuard[Lighting]:
    """Whether a color on this mouse ends with the per-key fill: 0x8071 with 0x8081, as on
    the G502 X PLUS. 0x8070's zones and an 0x8071 mouse without 0x8081 take the static
    effects alone: Lighting's fill would raise on it."""
    return isinstance(lighting, Lighting) and lighting.per_key


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

    @staticmethod
    def find_lighting(mouse: Mouse) -> MouseLights | None:
        """The mouse's lighting: 0x8071's clusters that enumerate, its per-key cluster among
        them, or on a model in ZONE_LIGHTING 0x8070's zones, each effect claimed on a model
        in CLAIM_EACH_EFFECT; None for neither. A mouse's 0x8070 is driven on such a model
        alone: the G305 lists it, answers with one zone, accepts a color write and has no
        lights. 0x8071's effects take the model's own names where it has them
        (EFFECT_NAMES)."""
        found = Lighting.find(mouse.dev, per_key_cluster=True,
                              names=EFFECT_NAMES.get(mouse.product))
        if found is None and mouse.product in ZONE_LIGHTING:
            return ZoneLighting.find(mouse.dev,
                                     claim_each_effect=mouse.product in CLAIM_EACH_EFFECT)
        return found

    def lighting_under_test(self) -> list[str]:
        """The attached mice whose lighting is driven before its writes passed their test on
        this hardware (mouse.LIGHTING_TESTED), by name, for `status`."""
        return [m.name for m in self.mice.attached
                if m.lighting is not None and m.product not in LIGHTING_TESTED]

    def lighting_shown_by_mouse(self) -> list[dict]:
        """Whose lighting each attached mouse that waits to claim shows (lighting_shown), by
        name, for `status`."""
        return [{"name": m.name, "shown": m.lighting_shown} for m in self.mice.attached
                if m.lighting_shown is not None]

    @staticmethod
    def claim_at_attach(mouse: Mouse, lighting: MouseLights | None) -> None:
        """The claim connect_mouse sends inside its guard outside HOST mode: to a mouse with
        lighting that does not wait to claim (WAITS_TO_CLAIM). Raises what the request
        raises."""
        if lighting is not None and mouse.product not in WAITS_TO_CLAIM:
            lighting.claim()

    def claim_waits(self, attached: AttachedMouse) -> bool:
        """Whether a mouse that waits to claim (WAITS_TO_CLAIM) has no lighting to be given:
        color sync off, and no color or effect set for it while the daemon runs, which a
        replug keeps (KeptMouse). It is sent no claim, and one it holds or may hold is
        released (give_back_lighting)."""
        return (attached.product in WAITS_TO_CLAIM and not self.d.config.mouse_sync_color
                and attached.color is None and attached.effect is None)

    def claim(self, attached: AttachedMouse) -> None:
        """The lighting claim, through mouse_call, which raises MouseError when it fails, after
        which the mouse counts as holding it (claim_unsure); once it lands the mouse no longer
        shows its own lighting."""
        try:
            self.mice.mouse_call(attached.its_lighting.claim, target=attached)
        except MouseError:
            attached.claim_unsure = True
            raise
        attached.own_lighting = attached.claim_unsure = False

    def claim_first(self, target: AttachedMouse) -> bool:
        """Claim the lighting of a mouse that shows its own (own_lighting) before a lighting
        write; True when the write may go. One not answering is sent nothing, and one that may
        be asleep and leaves the claim unanswered is sent no write: either answers False, and
        send_or_write owes the write, its claim with it (tend_mice), a drag's preview excepted.
        Any other failed claim raises mouse_call's MouseError."""
        if not target.own_lighting:
            return True
        if target.silent:
            return False
        # Read before the claim: its timeout leaves any mouse unheard for MOUSE_AWAKE or more.
        asleep = target.may_be_asleep()
        try:
            self.claim(target)
        except MouseError:
            if asleep and target.silent:
                # As a color sent to it without waiting is owed (check_mouse_write).
                return False
            raise
        return True

    def let_go(self, attached: AttachedMouse) -> bool:
        """Release the claim of a mouse whose claim waits again (claim_waits), so it shows its
        own lighting: color sync turned off with no color or effect of its own set (Claude
        Code's call of 2026-10-18, pending the owner's), a claim that failed and may have landed
        (claim_unsure), or the release it is owed from its attach (release_owed), which ends a
        claim an earlier run left. That one goes once, whether it lands or not. True when the
        release landed, or none was due."""
        at_attach = attached.own_lighting and not attached.claim_unsure
        if at_attach and not attached.release_owed:
            return True
        attached.release_owed = False
        try:
            self.mice.mouse_call(attached.its_lighting.release, target=attached)
        except MouseError as exc:
            self.say_lighting_back_failed(attached, exc)
            return False
        attached.own_lighting, attached.claim_unsure = True, False
        attached.forget_showing()
        log.info("%s shows its own lighting %s", attached.name,
                 "from its attach, any claim an earlier run left let go" if at_attach
                 else "again")
        return True

    def claim_and_paint(self, attached: AttachedMouse) -> bool:
        """The lighting claim, then the mouse's lighting (give_back_lighting). True when both
        took, or there is no lighting. A mouse whose claim waits (claim_waits) is sent no
        claim, and one it holds or may hold is released."""
        if attached.lighting is None:
            return True
        if not self.claim_waits(attached):
            try:
                self.claim(attached)
            except (MouseError, OSError) as exc:
                self.say_lighting_back_failed(attached, exc)
                return False
        return self.give_back_lighting(attached)

    def give_back_lighting(self, attached: AttachedMouse) -> bool:
        """Write the mouse's lighting: synced, else its own color or effect (from _mouse_kept
        at attach); for a mouse whose claim waits (claim_waits), release a claim it holds or
        may hold (let_go). Answers whether it took. A mouse not answering is owed it
        (tend_mice)."""
        if attached.lighting is None:
            return True
        if attached.silent:
            attached.owed_lighting = True
            return False
        attached.owed_lighting = False
        if self.claim_waits(attached):
            took = self.let_go(attached)
        elif self.d.config.mouse_sync_color:
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
        """Static color on every cluster of `target` that has it, then the per-key fill on a
        mouse that has one (takes_fill): the write mouse_color and the keyboard sync share.
        `own` False is a synced write, and `repeat` False a drag's preview, not counted as
        shown, so the release writes its color again, and not waited for unless the mouse's
        lighting claims each effect (claim_each_effect). A failure raises mouse_call's
        MouseError, or a LightingError the write raises.
        A mouse that shows its own lighting is claimed first (claim_first).

        A mouse that may be asleep is sent the color without waiting (send_or_write), one
        write in flight at a time: a color asked for meanwhile is owed, but a preview is
        dropped: owed, the stored color would go out between two previews.
        """
        lighting = target.its_lighting
        # A preview waits on a mouse that claims each effect: an unwaited claim's refusal goes
        # unseen, and the effect after it could be stored.
        wait = repeat or (isinstance(lighting, ZoneLighting) and lighting.claim_each_effect)

        def send() -> tuple[tuple[int, int, int], ...]:
            effects = tuple(lighting.send_effect(cluster.index, "static", color=color)
                            for cluster in lighting.clusters if cluster.has_effect("static"))
            return effects + (lighting.send_fill(color) if takes_fill(lighting) else ())

        def write() -> None:
            for cluster in lighting.clusters:
                if cluster.has_effect("static"):
                    lighting.set_effect(cluster.index, "static", wait=wait, color=color)
            if takes_fill(lighting):
                lighting.fill(color)

        # A preview's color is not counted as shown.
        if self.send_or_write(target, color, send, write, repeat=repeat, owe=repeat) and repeat:
            target.showing = color
        if own:
            # Not for a synced write: the keyboard's color would replace the mouse's own effect.
            target.color, target.effect = color, None

    def send_or_write(self, target: AttachedMouse, color: int,
                      send: Callable[[], tuple[tuple[int, int, int], ...]],
                      write: Callable[[], None], *, repeat: bool, owe: bool) -> bool:
        """One lighting write to `target`: none while another is in flight or while claim_first
        holds it back, owed when `owe` (tend_mice); `send` to a mouse that may be asleep,
        without waiting, its replies settled as they arrive (check_mouse_write) and counted as
        shown then when `repeat`; else `write`, through mouse_call. True once `write` returned.
        A failure raises mouse_call's MouseError, or a LightingError the write raises."""
        if target.sent is not None or not self.claim_first(target):
            if owe:
                target.owed_lighting = True
            return False
        # Unknown until it has taken.
        target.showing = None
        if target.may_be_asleep():
            replies = self.mice.mouse_call(send, target=target, answered=False)
            target.sent = SentColor(color, replies, time.monotonic() + MOUSE_WRITE_DEADLINE,
                                    repeat)
            return False
        self.mice.mouse_call(write, target=target)
        return True

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
            log.info("%s went away: %s", attached.name, failure_reason(exc))
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
        `target`, when sync is on. Answers whether every write took. A mouse without lighting
        the daemon drives is left as it is, in NOT_SYNCED's words once an attach.

        Never raises into the keyboard color paths that call it: a mouse missing or asleep
        mid-write is skipped. A preview's effect writes are not waited for, so a push per
        preview is cheap, except on a mouse whose lighting claims each effect. `repeat` False
        is a preview (write_mouse_color).
        """
        if not self.d.config.mouse_sync_color:
            return True
        # Every path to a synced mouse's color comes here, so its level is applied here.
        color = self.d.keyboard.at_level(color)
        took = True
        for attached in [target] if target is not None else list(self.mice.attached):
            if attached.lighting is None:
                if not attached.sync_skip_said:
                    attached.sync_skip_said = True
                    log.info("%s", NOT_SYNCED.format(attached.name))
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
        waiting to one that may be asleep, owed while a write is in flight (send_or_write).
        A mouse that shows its own lighting is claimed first (claim_first)."""
        lighting = target.its_lighting
        clusters = [c for c in lighting.clusters if c.has_effect(name)]
        if not clusters:
            return []
        writes = [(c.index, lighting.effect_params(c.index, name, color)) for c in clusters]

        def send() -> tuple[tuple[int, int, int], ...]:
            return tuple(lighting.send_effect(index, name, **params) for index, params in writes)

        def write() -> None:
            for index, params in writes:
                lighting.set_effect(index, name, wait=True, **params)

        target.forget_showing()     # an effect: no color is shown
        self.send_or_write(target, color, send, write, repeat=False, owe=True)
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
        (dimmed or dark), but one that waits to claim with none is released (give_back_lighting).
        Either way the desktop's idleness is followed as the switch says
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
        took it. A mouse that waits to claim (WAITS_TO_CLAIM) takes none and shows its own
        lighting, a claim it holds or may hold released (give_back_lighting)."""
        if (attached.product in WAITS_TO_CLAIM or attached.color is not None
                or attached.effect is not None):
            return False
        attached.color = self.d.per_keyboard.at_brightness(color)
        return True

    def repaint_after_flash(self, attached: AttachedMouse) -> None:
        """The DPI flash is over and ended dark (Dpi.after_dpi_write): the lighting is owed
        again, whatever `showing` says."""
        attached.repaint_at = None
        attached.forget_showing()
        if not self.d.config.mouse_sync_color:
            # One with nothing of its own to give back adopts as the color sync off does, not dark;
            # one that waits to claim adopts none (adopt_color), and the lighting owed below
            # lets go of a claim it holds or may hold.
            self.adopt_color(attached, self.d.keyboard.shown_color())
        attached.owed_lighting = True
        log.debug("%s's DPI flash is over; its lighting is owed again", attached.name)

    def mouse_with_lights(self) -> bool:
        """Whether a mouse with lights is attached: one a sync can color."""
        return any(m.lighting is not None for m in self.mice.attached)

    def lit_mouse(self, request: dict) -> AttachedMouse:
        """The mouse a lighting command is for (Mice.mouse_for: `device`, else the selected
        one). MouseError for no such mouse, for a mouse with no lights, such as the G305, and
        with sync on, which only a mouse with lights follows."""
        target = self.mice.mouse_for(request)
        if target.lighting is None:
            raise MouseError(f"{target.name} has no lighting")
        if self.d.config.mouse_sync_color:
            raise MouseError(SYNC_ON)
        return target

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
        if not value:
            return reply
        # The color the mice now follow; off, each shows its own, which the `mouse` command
        # gives.
        reply["color"] = format_color(color)
        return with_note(reply, "; ".join(NOT_SYNCED.format(m.name) for m in self.mice.attached
                                          if m.lighting is None))

    def _cmd_mouse_color(self, request: dict) -> dict:
        mouse = self.lit_mouse(request)
        color = parse_color(request["value"], "mouse_color")
        self.write_mouse_color(color, mouse)
        return {"ok": True, "color": format_color(color)}

    def _cmd_mouse_effect(self, request: dict) -> dict:
        mouse = self.lit_mouse(request)
        name = name_text(request["value"], "mouse_effect")
        color = parse_color(request.get("color", "#0080ff"), "mouse_effect.color")
        clusters = self.write_mouse_effect(name, color, mouse)
        if not clusters:
            has = ", ".join(effect_names(mouse.its_lighting.clusters))
            return {"ok": False, "error": f"{mouse.name} has no effect {name!r}; "
                                          f"its effects are {has}"}
        return {"ok": True, "effect": name, "clusters": clusters}
