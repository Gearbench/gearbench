"""The keyboard kept set up on its link: the lighting claim and the rest a power cycle drops
(take_over), the receiver's link notices, the setup again after a power-on report, a link
notice or a resume (reconfigure_keyboard, recheck_after_resume), and the move from its
receiver to its cable."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from ...hidpp import NOTIFICATION_FLAGS, WIRELESS_NOTICES, HidppError, HidppFailure
from ...keyboard import (
    CABLED_IDS as KEYBOARD_CABLED,
    RECEIVER_NODE_IDS as KEYBOARD_RECEIVERS,
    find_attached_keyboard,
)
from ...keyboard_zones import power_modes
from ...onboard import MODE_HOST
from ...wording import reason
from ..shared import MOUSE_PROBE, REAPPLY_INTERVAL, look_due, unanswered
from .notes import RECONNECT_DELAY

if TYPE_CHECKING:
    from ...keyboard_zones import KeyboardLights
    from .. import Daemon
    from . import Keyboard

log = logging.getLogger("gearbench")

#: How long after a power-on report on the receiver the cable is looked for again: the
#: report also comes as the cable goes in, once 0.32 s before the kernel saw it. Waiting for
#: the cable before setting the keyboard up showed the onboard wave at every power-on.
CABLE_WAIT = 0.5


class Link:
    """The keyboard's claim, and its setup again on its link or its cable."""

    def __init__(self, daemon: Daemon, keyboard: Keyboard) -> None:
        self.d = daemon
        self.keyboard = keyboard
        #: When to look next for the cable while on the receiver (keyboard_moved_to_cable).
        self._cable_check_at = 0.0
        #: When the keyboard was last set up (Keyboard.set_up, reconfigure_keyboard); forgotten
        #: at a link-down.
        self.keyboard_reapplied_at = 0.0
        #: The second cable look, CABLE_WAIT after a power-on report, or None
        #: (look_for_cable_again); a teardown drops it.
        self.cable_look_at: float | None = None

    def deadlines(self) -> list[float]:
        """When the link next needs the loop: the cable's look on the receiver, and the second
        look a power-on report asks for."""
        cable = self.keyboard.keyboard_product not in KEYBOARD_CABLED
        due = (self._cable_check_at if cable else None, self.cable_look_at)
        return [at for at in due if at is not None]

    def claim_keyboard(self, lighting: KeyboardLights) -> None:
        """The lighting claim, then the idle and sleep times (fn 7, Idle.claimed) and the active
        power state (fn 8 `01 01`, Lighting.set_active), as G HUB's claim has them, though G HUB
        sends those two between the claim's two writes. 0x8070's zones have neither
        (keyboard_zones), so their claim is all. After the claim's `05` the wake notice is off,
        so the times bring none; the one its `07` brings is left unanswered
        (Idle.expect_claim_wake). The keyboard's error reply to either leaves it claimed; other
        failures go to the caller."""
        lighting.claim()
        if power_modes(lighting):
            self.keyboard.idle.claimed()
            try:
                lighting.set_active()
            except HidppError as exc:
                if exc.unreachable:
                    raise
                log.info("the %s refused the active power state (%s)",
                         self.keyboard.model_name(), exc)
        self.keyboard.idle.keyboard_awake()

    def take_over(self) -> None:
        """What a power cycle drops and each setup writes again after a 0x8100 keyboard's host
        mode: the claim, the key diversion and FN's reports, then on the G515 generation its
        own host mode, after the claim and before the custom assignments' file, as G HUB's
        takeover sends it (KeyControl.take_host_mode). A refusal one of its parts catches leaves
        the rest to run; any other failure goes to the caller."""
        keyboard = self.keyboard
        if keyboard.lighting is not None:
            self.claim_keyboard(keyboard.lighting)
        if keyboard.keys is not None:
            keyboard.keys.divert(True)
        self.turn_on_fn_reports()
        keyboard.key_control.take_host_mode()

    def turn_on_fn_reports(self) -> None:
        """Have a keyboard with FN keys to light report FN (Modifiers.report), at each setup:
        a power cycle turns the reports off, as a replug does where the cable is the keyboard's
        only power. A refusal leaves its FN keys as the
        layer has them, said at info; any other failure goes to the caller."""
        modifiers = self.keyboard.modifiers
        if modifiers is None:
            return
        try:
            modifiers.report()
        except HidppError as exc:
            if exc.unreachable:
                raise
            log.info("the %s refused to report FN (%s); its FN keys stay as the layer has "
                     "them", self.keyboard.model_name(), exc)
        else:
            log.debug("FN reports turned on")

    def turn_on_link_notices(self) -> None:
        """With `keyboard_link_notices` on and the keyboard on its receiver's own node
        (keyboard.RECEIVER_NODE_IDS), have the receiver send its link notices
        (take_link_notice) by setting register 0x00's wireless bit. A failure is logged and
        changes nothing else."""
        dev = self.keyboard.dev
        if (not self.d.config.keyboard_link_notices or dev is None
                or self.keyboard.keyboard_product not in KEYBOARD_RECEIVERS):
            return
        receiver = f"the {self.keyboard.model_name()}'s receiver"
        step = "the first read"
        try:
            found = dev.read_register(NOTIFICATION_FLAGS, **MOUSE_PROBE)
            if found[1] & WIRELESS_NOTICES:
                log.info("%s sends link notices already (register 0x00 %s)",
                         receiver, found.hex(" "))
                return
            wanted = bytes([found[0], found[1] | WIRELESS_NOTICES, found[2]])
            step = f"the write of {wanted.hex(' ')} over {found.hex(' ')}"
            dev.write_register(NOTIFICATION_FLAGS, wanted, **MOUSE_PROBE)
            step = f"the read back after writing {wanted.hex(' ')}"
            now = dev.read_register(NOTIFICATION_FLAGS, **MOUSE_PROBE)
        except HidppFailure as exc:
            log.warning("could not turn on link notices at %s (%s): %s", receiver, step, exc)
            return
        if now[1] & WIRELESS_NOTICES:
            log.info("%s sends link notices now (register 0x00 %s, was %s)",
                     receiver, now.hex(" "), found.hex(" "))
        else:
            log.warning("%s took register 0x00 %s and reads back %s: no link notices",
                        receiver, wanted.hex(" "), now.hex(" "))

    def take_link_notice(self, down: bool) -> None:
        """The receiver's notice that the keyboard's link went down or came up (hidpp.LINK_NOTICE,
        turn_on_link_notices), `down` its Event.link_down.

        Down, the keyboard is off, out of range or on its cable: marked not answering, its held
        keys let go of, the cable looked for, and REAPPLY_INTERVAL reset, since the next link is a
        new link setup. Up, it is set up again at once, since link setup drops the claim; its
        power-on report then falls within REAPPLY_INTERVAL. An unanswered request marks the
        keyboard in place of a down notice that does not come, and the power-on report sets it
        up in place of an up notice.
        """
        if down:
            self.keyboard_reapplied_at = float("-inf")
            self.keyboard.mark_keyboard_silent("its receiver lost the link")
            self.d.bindings.keyboard_gone(held_only=True, source=self.keyboard)
            self.keyboard_moved_to_cable(force=True)
            return
        self.say_set_up_again("linked to its receiver again")
        self.reconfigure_keyboard()

    def take_power_on_report(self, data: bytes) -> None:
        """0x1D4B fn 0 with byte 1 set (Keyboard.on_device_event): the keyboard lost its
        diversion, claim and host mode. Byte 2 cannot tell a power cycle from the cable going
        in: the G915 sends `01 01 01` at both. The cable can reach the kernel after the report,
        so on the receiver it is looked for now and CABLE_WAIT later (look_for_cable_again),
        the keyboard set up in between."""
        reconnected = data[2] == 1
        # The second report of a pair, or one just after a connect, sets nothing up.
        self.say_set_up_again("reconnected" if reconnected else "asked to be set up again")
        if self.keyboard.keyboard_product in KEYBOARD_CABLED:
            self.reconfigure_keyboard()
        elif not self.keyboard_moved_to_cable(force=True):
            self.reconfigure_keyboard()
            if self.keyboard.dev is not None and self.cable_look_at is None:
                self.cable_look_at = time.monotonic() + CABLE_WAIT

    def say_set_up_again(self, what: str) -> None:
        """Say what asked for the setup again: at info once REAPPLY_INTERVAL has passed, else
        at debug, since reconfigure_keyboard then sets nothing up."""
        name = self.keyboard.model_name()
        if time.monotonic() - self.keyboard_reapplied_at >= REAPPLY_INTERVAL:
            log.info("%s %s; setting it up again", name, what)
        else:
            log.debug("%s %s; set up under %d s ago, so not again", name, what, REAPPLY_INTERVAL)

    def reconfigure_keyboard(self) -> bool:
        """Host mode, the claim, the diversion and FN's reports again (take_over), the custom
        assignments' file owed again (KeyControl.lost_power), the brightness the keyboard holds
        read again (Paint.read_brightness), then the profile, and each other keyboard whose
        lighting or brightness that read moves (Keyboards.repaint_changed).

        On the handle the daemon holds, which through the receiver is still good: a teardown and
        connect would show the firmware's effect in between. Held keys are let go, since none is
        held across a power cycle. At most once per REAPPLY_INTERVAL, counted from a connect too:
        a mouse's power-on report comes in pairs, and the keyboard's has not been measured. A
        failure starts over with a reconnect, handing nothing back to a keyboard that did not
        answer; where that moves the lead, a synced mouse, a synced headset's lights and each
        other keyboard whose lighting or brightness the move changes are painted, since that
        keyboard may stay off. False when the interval kept it from running.
        """
        now = time.monotonic()
        if now - self.keyboard_reapplied_at < REAPPLY_INTERVAL:
            return False
        self.keyboard_reapplied_at = now
        keyboard = self.keyboard
        # Its caller has said it is back.
        keyboard.heard(quiet=True)
        self.d.bindings.keyboard_gone(held_only=True, source=keyboard)
        others = self.d.keyboards.views(keyboard)
        try:
            keyboard.set_keyboard_mode(MODE_HOST)
            self.take_over()
            keyboard.key_control.lost_power()
            keyboard.paint.read_brightness()
        except (HidppFailure, IndexError, OSError) as exc:
            quiet = unanswered(exc)
            # Gone to its cable, or off again: the cable is looked for first, and a keyboard
            # that did not answer is handed nothing back, since each step would wait out a timeout.
            if quiet and self.keyboard_moved_to_cable(force=True):
                return True
            log.log(logging.INFO if quiet else logging.WARNING,
                    "could not set the %s up again (%s); reconnecting", keyboard.model_name(),
                    reason(exc))
            leader = self.d.per_keyboard.leader()
            keyboard.teardown_device(hand_back=not quiet)
            if quiet and self.d.per_keyboard.leader() != leader:
                # Unlike a route left, whose cable's connect follows at the next pass, a
                # keyboard off again may stay off: a synced mouse, a synced headset's lights and
                # each other keyboard the new lead changes are painted now.
                self.d.sync_to_leader()
                self.d.keyboards.repaint_changed(others)
            return True
        keyboard.apply_profile()
        if keyboard.dev is None:
            # apply_profile tears down rather than raising, and the teardown repaints what it
            # owes the others: none for a route the keyboard has left.
            return True
        keyboard.idle.expect_claim_wake()
        self.d.keyboards.repaint_changed(others)
        return True

    def recheck_after_resume(self) -> None:
        """After a resume (Session.woke_up): the link notices again, the readings kept by their
        age dropped (Readings.forget_live), then the keyboard asked and, if it answers, set up
        again in full, or its profile applied where REAPPLY_INTERVAL keeps the setup from
        running."""
        # A receiver that lost power in the suspend may keep its node and lose register 0x00.
        self.turn_on_link_notices()
        self.keyboard.readings.forget_live()
        # Asked now, as the mice and the headsets are: a mark set before the suspend, its own
        # probe's included, outlives it, since time.monotonic() stops while the machine sleeps.
        self.keyboard.mark_keyboard_answering(quiet=True)
        if not self.keyboard.keyboard_answers():
            return          # away or off: set up when it is back
        if not self.reconfigure_keyboard():
            self.keyboard.apply_profile()

    def keyboard_moved_to_cable(self, force: bool = False) -> bool:
        """Drop the keyboard's receiver route once its cable is plugged in.

        A G915 given its cable answers on the cable's node, and its receiver's node stays,
        answering each request for it with HID++ 1.0 error 0x08, unknown device; no DeviceGone
        comes. The connect's lookup (find_attached_keyboard) prefers the cable; this makes the
        same choice while attached.
        Looked for through sysfs at the looks' pace (Plugs.pace), or at once with `force`, as
        when a node comes, among the model's own ids alone: another model's cable is another
        keyboard, and leaves the route as it is. True when the receiver route was dropped, for
        the loop to connect again at once.
        """
        keyboard = self.keyboard
        if (keyboard.dev is None or keyboard.keyboard_product in KEYBOARD_CABLED
                or (not look_due(time.monotonic(), self._cable_check_at) and not force)):
            return False
        self._cable_check_at = time.monotonic() + self.d.plugs.pace(RECONNECT_DELAY)
        found = find_attached_keyboard(cabled_only=True, same_model_as=keyboard.keyboard_product)
        if found is None:
            return False
        log.info("%s moved from its receiver (%s) to its cable (%s)",
                 keyboard.model_name(), keyboard.dev.path, found[0])
        # Nothing is handed back through the receiver, which cannot reach it; the cable's
        # connect takes it over, at the next pass beside another keyboard driven too.
        keyboard.teardown_device(hand_back=False)
        self.d.keyboards.look_now()
        return True

    def look_for_cable_again(self) -> bool:
        """The second look for the cable, CABLE_WAIT after a power-on report on the receiver.
        True when the receiver route was dropped."""
        if self.cable_look_at is None or time.monotonic() < self.cable_look_at:
            return False
        self.cable_look_at = None
        return self.keyboard_moved_to_cable(force=True)
