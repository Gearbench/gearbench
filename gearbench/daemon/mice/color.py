"""The mice's lighting: which a mouse has (`find_lighting`), the claim and its release, a
mouse's own color or effect, the color a synced mouse takes from the leader
(Daemon.synced_color) written to it (`sync_mouse_color`), black at a power-off (`go_dark`),
the command that sets a game profile's mouse color (`profile_mouse_color`), and the lighting
given back after a power-on, a silence or a DPI flash. A mouse that waits to claim
(WAITS_TO_CLAIM) is released once at its attach when it has nothing to show, claimed with its
first lighting write, and released once it has none to show again. A mouse that may be asleep
is sent its lighting without waiting for the replies, one write in flight at a time, settled
as they arrive (`check_mouse_write`), but for one whose lighting claims each effect, which
gets a claim first and waited writes. With them is the read of a mouse's HID++ node
(`read_node`), which that check shares with Mice.pump_mouse."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, TypeGuard

from ...config import DEFAULT_COLOR, format_color, parse_color
from ...hidpp import REQUEST_RETRIES, REQUEST_TIMEOUT, DeviceGone, Heard, HidppError, HidppFailure
from ...keyboard import followed
from ...lighting import Lighting, LightingError
from ...mouse import (
    CLAIM_EACH_EFFECT,
    EFFECT_NAMES,
    LIGHTING_TESTED,
    OWN_LIGHTING,
    WAITS_TO_CLAIM,
    ZONE_LIGHTING,
    MouseError,
    lighting_shown_words,
)
from ...mouse_device import Mouse
from ...mouse_lighting import MouseLights, ZoneLighting
from ...profiles import mouse_color_json, read_mouse_color
from ...wording import quoted, reason
from ..shared import (
    NO_GAME_PROFILE_NAMED,
    NOT_SYNCED,
    SYNC_ON,
    failure_reason,
    name_text,
    no_effect,
    switch_value,
    with_note,
)
from .attached import AttachedMouse, SentColor, aimed, effect_names

if TYPE_CHECKING:
    from .. import Daemon
    from . import Mice

log = logging.getLogger("gearbench")

# How long a color sent without waiting has to be answered: a waiting request's budget.
MOUSE_WRITE_DEADLINE = REQUEST_TIMEOUT * (REQUEST_RETRIES + 1)

#: The refusal of `mouse_effect`'s `release` for a mouse claimed at its setup (not one of
#: WAITS_TO_CLAIM), by its name: whether it shows its own lighting once let go while
#: Gearbench runs is not known.
KEEPS_SENT_LIGHTING = "the {} shows the lighting Gearbench sends while Gearbench runs"


def takes_fill(lighting: MouseLights) -> TypeGuard[Lighting]:
    """Whether a color on this mouse ends with the per-key fill: 0x8071 with 0x8081, as on
    the G502 X PLUS. 0x8070's zones and an 0x8071 mouse without 0x8081 take the static
    effects alone: Lighting's fill would raise on it."""
    return isinstance(lighting, Lighting) and lighting.per_key


def claims_each_effect(lighting: MouseLights) -> bool:
    """Whether each effect on this mouse goes right after a claim of its own
    (mouse.CLAIM_EACH_EFFECT), which is waited for: sent without waiting, that claim's refusal
    would go unseen, and the effect after it could be stored."""
    return isinstance(lighting, ZoneLighting) and lighting.claim_each_effect


class MouseLighting:
    """The lighting of every attached mouse that has any, and the read of each attached
    mouse's node (read_node)."""

    def __init__(self, daemon: Daemon, mice: Mice) -> None:
        self.d = daemon
        self.mice = mice
        #: The mice whose black at a power-off (go_dark) did not take: the stop line says so.
        self._black_missed: list[AttachedMouse] = []

    def commands(self) -> dict[str, Callable[[dict], dict]]:
        return {
            "mouse_sync": self._cmd_mouse_sync,
            "mouse_color": self._cmd_mouse_color,
            "mouse_effect": self._cmd_mouse_effect,
            "profile_mouse_color": self._cmd_profile_mouse_color,
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
        write, and of one whose lighting claims each effect (claims_each_effect) that may be
        asleep: sent without waiting, that claim's refusal would go unseen and the effect after
        it be stored. Once the claim lands the mouse is heard from, so its write waits. True
        when the write may go. One not answering is sent nothing, and one that may be asleep
        and leaves the claim unanswered is sent no write: either answers False, and
        send_or_write owes the write, its claim with it (tend_mice), a drag's preview excepted.
        Any other failed claim raises mouse_call's MouseError."""
        if not target.own_lighting and not (claims_each_effect(target.its_lighting)
                                            and target.may_be_asleep()):
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
        own lighting: color sync turned off with no color or effect of its own set,
        a claim that failed and may have landed
        (claim_unsure), or the release it is owed from its attach (release_owed), which ends a
        claim an earlier run left. That one goes once, whether it lands or not. True when the
        release landed, or none was due."""
        at_attach = attached.own_lighting and not attached.claim_unsure
        if at_attach and not attached.release_owed:
            return True
        attached.release_owed = False
        try:
            self.release(attached)
        except MouseError as exc:
            self.say_lighting_back_failed(attached, exc)
            return False
        log.info("%s shows its own lighting %s", attached.name,
                 "from its attach, any claim an earlier run left let go" if at_attach
                 else "again")
        return True

    def release(self, attached: AttachedMouse) -> None:
        """The release of the lighting claim, through mouse_call, which raises MouseError when
        it fails; once it lands the mouse shows its own lighting, and what it shows is no
        longer Gearbench's to know. A release that lands ends any claim an earlier run left,
        so the one owed from its attach is due no more (release_owed)."""
        self.mice.mouse_call(attached.its_lighting.release, target=attached)
        attached.own_lighting, attached.claim_unsure, attached.release_owed = True, False, False
        attached.forget_showing()

    def release_lighting(self, request: dict) -> dict:
        """`mouse_effect`'s `release`: a mouse that waits to claim its lighting shows its own
        again, its color and effect forgotten, as at an attach with none set (claim_waits),
        until a color or effect is sent. Refused as lit_mouse refuses, and for a mouse
        claimed at its setup (KEEPS_SENT_LIGHTING). One that shows its own is sent nothing."""
        target = self.lit_mouse(request)
        if target.lighting_shown is None:
            raise MouseError(KEEPS_SENT_LIGHTING.format(target.name))
        target.color = target.effect = None
        if target.held_lighting is not None:
            self.release(target)
            log.info("%s shows its own lighting again, as asked", target.name)
        return aimed(target, {"ok": True,
                              "note": lighting_shown_words(target.name, OWN_LIGHTING)})

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
            took = self.sync_mouse_color(self.d.synced_color(), target=attached)
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
        mouse that has one (takes_fill). `own` False is a synced write, and `repeat` False a
        drag's preview, not counted as shown, so the release writes its color again, its static
        effects not waited for unless the mouse's lighting claims each effect
        (claims_each_effect). A failure raises mouse_call's MouseError, or a LightingError the
        write raises. A mouse that shows its own lighting is claimed first (claim_first).

        A mouse that may be asleep, unless its lighting claims each effect, is sent the color
        without waiting (send_or_write), one write in flight at a time: a color asked for
        meanwhile is owed, but a preview is dropped: owed, the stored color would go out
        between two previews.
        """
        lighting = target.its_lighting
        # A preview's effects are waited for on a mouse that claims each effect (the owner's
        # decision of 2026-10-19); ZoneLighting.set_effect waits for its claims either way.
        wait = repeat or claims_each_effect(lighting)
        static = [cluster.index for cluster in lighting.clusters if cluster.has_effect("static")]

        def send() -> tuple[tuple[int, int, int], ...]:
            effects = tuple(lighting.send_effect(index, "static", color=color) for index in static)
            return effects + (lighting.send_fill(color) if takes_fill(lighting) else ())

        def write() -> None:
            for index in static:
                lighting.set_effect(index, "static", wait=wait, color=color)
            if takes_fill(lighting):
                lighting.fill(color)

        if self.send_or_write(target, color, send, write, repeat=repeat, owe=repeat) and repeat:
            target.showing = color
        if own:
            # Not for a synced write: the keyboard's color would replace the mouse's own effect.
            target.color, target.effect = color, None

    def send_or_write(self, target: AttachedMouse, color: int,
                      send: Callable[[], tuple[tuple[int, int, int], ...]],
                      write: Callable[[], None], *, repeat: bool, owe: bool) -> bool:
        """One lighting write to `target`: none while another is in flight or while claim_first
        holds it back, owed when `owe` (tend_mice); `send` to a mouse that may be asleep, but
        for one whose lighting claims each effect (claims_each_effect), without waiting, its
        replies settled as they arrive (check_mouse_write) and counted as shown then when
        `repeat`; else `write`, through mouse_call. True once `write` returned. A failure
        raises mouse_call's MouseError, or a LightingError the write raises."""
        if target.sent is not None or not self.claim_first(target):
            if owe:
                target.owed_lighting = True
            return False
        # Unknown until it has taken.
        target.showing = None
        # claim_first can find one that claims each effect awake a moment before this finds
        # it quiet: its claims are never sent unwaited.
        if target.may_be_asleep() and not claims_each_effect(target.its_lighting):
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
        answering and owed the lighting (tend_mice); one heard from since the send, a
        notification read and still queued included, is awake and its reply was lost, so it is
        owed but not marked here, and a queued link-down notice marks it as it is taken
        (take_link_notice). An error reply is a refusal, logged
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
            # A notification read since and still queued, which mouse_events takes next, is
            # the mouse heard from too.
            if attached.heard_at > sent.deadline - MOUSE_WRITE_DEADLINE or attached.dev.pending:
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
            log.warning("%s: %s; reconnecting", attached.name, exc)
            self.mice.teardown_mouse(attached)
            return None
        if heard.input:
            # Motion or a bound key through its receiver, or a keystroke of a keyboard on the
            # same receiver (Heard): taken for the mouse awake. One marked silent is marked
            # answering, so tend_mice gives it no recheck: the next full pass gives it the
            # lighting and tuning it is owed, and a full setup only if it was cut_short.
            self.mice.mark_answering(attached)
        return heard

    def color_to_save(self, request: dict) -> int | None:
        """The mouse color `save_profile` gives the game profile it saves: the request's
        `mouse_color`, a null for none, else the layer's, the loaded game profile's or none
        from an M profile, as its own DPI stages go (GameCommands._cmd_save_profile)."""
        if "mouse_color" in request:
            return read_mouse_color(request["mouse_color"], "save_profile.mouse_color")
        return self.d.profile_color()

    def sync_mouse_color(self, color: int, repeat: bool = True,
                         target: AttachedMouse | None = None) -> bool:
        """Push `color` to every attached mouse with lighting, or only `target`, when sync is
        on: the one a synced mouse takes from the leader (Daemon.synced_color), or black as
        the keyboard goes dark. Answers whether every write took. A mouse without lighting
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
                    log.info("could not give %s the synced color: %s", attached.name, reason(exc))
                took = False
        return took

    def go_dark(self) -> None:
        """At a power-off with color sync on: black on each attached mouse
        (sync_mouse_color), one mouse at a time, so that dark_words can tell the ones whose
        black did not take, a write refused or failed, or a mouse not answering, from the
        rest. A black sent without waiting counts as taken: nothing waits for its answer."""
        self._black_missed = [attached for attached in list(self.mice.attached)
                              if not self.sync_mouse_color(0x000000, target=attached)]

    def dark_words(self, going: AttachedMouse) -> str:
        """Mice.teardown_mouse's words for a synced mouse whose lighting claim it keeps at a
        power-off: left dark with the keyboard, or not where its black (go_dark) did not
        take."""
        if any(attached is going for attached in self._black_missed):
            return f"{going.name} not left dark, its lighting claim held"
        return f"{going.name} left dark with the keyboard, its lighting claim held"

    def write_mouse_effect(self, name: str, color: int, target: AttachedMouse) -> list[str]:
        """An effect on every cluster of `target` that has it (the zone and per-key clusters
        have different ones); answers the clusters written. Recorded like a color, so a power
        cycle puts back the effect chosen last. Waited for on an awake mouse, sent without
        waiting to one that may be asleep unless its lighting claims each effect
        (claims_each_effect), owed while a write is in flight (send_or_write). A mouse that
        shows its own lighting is claimed first (claim_first)."""
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
        """Act on the sync switch set `on` or off; `color` is the one a synced mouse takes
        (Daemon.synced_color). On: every mouse takes it, unless `paint` is False (the
        caller's apply_profile paints). Off: each mouse gets its own lighting back; one with
        none takes `color` at the keyboard's brightness as its own (adopt_color), written only
        when it is known to show another (dimmed or dark), but one that waits to claim with
        none is released (give_back_lighting). Either way the desktop's idleness is followed
        as the switch says (Session.watch_idle_for_both)."""
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
        """A mouse with no color or effect of its own takes `color` at the keyboard's
        brightness as its own, as color sync off gives it; True when it took it. A mouse that
        waits to claim (WAITS_TO_CLAIM) takes none."""
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
        """The mouse a lighting command is for (Mice.mouse_for: `device`, else `current`).
        MouseError for no such mouse, for a mouse with no lights, such as the G305, and with
        sync on, which only a mouse with lights follows."""
        target = self.mice.mouse_for(request)
        if target.lighting is None:
            raise MouseError(f"the {target.name} has no lighting")
        if self.d.config.mouse_sync_color:
            raise MouseError(SYNC_ON.format(device="mouse", command="mouse-sync",
                                            leader=followed(self.d.per_keyboard.leader())))
        return target

    def _cmd_mouse_sync(self, request: dict) -> dict:
        value = switch_value(request.get("value"), "mouse_sync")
        # Saved before it acts: a refused save changes nothing.
        was = self.d.config.mouse_sync_color
        self.d.save_setting(mouse_sync_color=value)
        color = self.d.synced_color()
        if value or was:
            self.sync_changed(value, color)
        # `mouse_lights`, as status names it. `mouse` is the same fact under 1.1.0's name,
        # which a mouse write's reply now gives the mouse written (aimed): kept through the
        # next release for a script written for 1.1.0.
        lights = self.mouse_with_lights()
        reply: dict = {"ok": True, "mouse_sync_color": value, "mouse_lights": lights,
                       "mouse": lights}
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
        return aimed(mouse, {"ok": True, "color": format_color(color)})

    def _cmd_mouse_effect(self, request: dict) -> dict:
        if request.get("release") is True:
            return self.release_lighting(request)
        mouse = self.lit_mouse(request)
        name = name_text(request["value"], "mouse_effect")
        # Left out, the color the mouse shows, the last one sent it, as the keyboard's effect
        # keeps its layer's; DEFAULT_COLOR where it was sent none (the owner's decision of
        # 2026-10-27).
        color = (parse_color(request["color"], "mouse_effect.color") if "color" in request
                 else DEFAULT_COLOR if mouse.color is None else mouse.color)
        clusters = self.write_mouse_effect(name, color, mouse)
        if not clusters:
            return {"ok": False, "error": no_effect(
                mouse.name, name, effect_names(mouse.its_lighting.clusters))}
        return aimed(mouse, {"ok": True, "effect": name, "clusters": clusters})

    def _cmd_profile_mouse_color(self, request: dict) -> dict:
        """Set the color a synced mouse shows while game profile `value` is loaded, or with a
        null `color` none, so that it shows the keyboard's (the owner's decision of
        2026-10-23); on the daemon's own copy when it is the loaded one, whose synced mouse
        takes it at once, as Games' commands edit a profile. With `if_none` true, as the pack's
        fill sends it, a profile that has a color by then keeps it, refused."""
        name = name_text(request["value"], "profile_mouse_color")
        color = read_mouse_color(request["color"], "profile_mouse_color.color")
        loaded, profile = self.d.games.loaded_copy(name)
        if profile is None:
            return {"ok": False, "error": NO_GAME_PROFILE_NAMED.format(quoted(name))}
        if request.get("if_none") is True and profile.mouse_color is not None:
            # Read after the import's 'game profile "X"'s mouse color: ' (import_plan._what).
            return {"ok": False, "error": "one was set since the import read it; import again "
                                          "to choose"}
        previous, profile.mouse_color = profile.mouse_color, color
        try:
            profile.save()
        except BaseException:
            profile.mouse_color = previous
            raise
        if loaded:
            self.d.sync_to_leader()
        return {"ok": True, "profile": profile.name, "mouse_color": mouse_color_json(color)}
