"""The keyboard's controller, `Keyboard`: connecting on either route, the claim, painting the
profile in force (`apply_profile`), the M and MR LEDs, the idle notices and the light level,
and the teardown. The parts it builds do the rest: the writes that put the lighting on the
board (paint.py) and the command handlers (commands.py)."""

from __future__ import annotations

import contextlib
import logging
import selectors
import time
from collections.abc import Callable, Iterable, Iterator
from typing import TYPE_CHECKING, cast

from ...battery import read as read_battery
from ...config import PROFILE_IDS, escaped, reason
from ...hidpp import (
    LINK_DOWN,
    NO_RULE,
    NOTIFICATION_FLAGS,
    WIRELESS_NOTICES,
    DeviceGone,
    Event,
    Hidpp,
    HidppError,
    HidppFailure,
    open_device,
)
from ...keyboard import (
    CABLED_IDS as KEYBOARD_CABLED,
    WITNESS as KEYBOARD_WITNESS,
    capabilities as keyboard_capabilities,
    find_attached_keyboard,
    name as keyboard_name,
)
from ...keys import DivertedKeys
from ...lighting import LEVEL_WORDS, Lighting, LightingError
from ...onboard import (
    F_ONBOARD,
    MODE_HOST,
    MODE_ONBOARD,
    read_mode,
    running_sector,
    write_mode,
)
from ..shared import (
    DARK,
    DIM,
    F_WIRELESS,
    IDLE_NOTE,
    KEYBOARD_BATTERY_INTERVAL,
    LIGHTS_DOWN_NOTE,
    LIT,
    MOUSE_PROBE,
    MOUSE_PROBE_TIMEOUT,
    MOUSE_SILENT_RECHECK,
    REAPPLY_INTERVAL,
    RECONNECT_DELAY,
    darker,
    failure_reason,
    scaled,
    unanswered,
    unreachable,
)
from .commands import KeyboardCommands
from .paint import KEYBOARD_AWAY, KEYBOARD_SILENT, LED_SETTLE, NO_LIGHTING, Paint, unanswered_note

if TYPE_CHECKING:
    from .. import Daemon

log = logging.getLogger("gearbench")

#: What `idle` says when the keyboard refused the new times or their write failed; not owed.
IDLE_TIMES_NOTE = ("saved; the keyboard did not take the new times, and gets them when it "
                   "is next set up")

#: What `idle` says when the keyboard is away or not answering: owed, or given at the claim.
KEYBOARD_AWAY_NOTE = ("saved; the keyboard is away or not answering, and gets the new "
                      "times when it is back")

#: What `idle` says for a keyboard attached without the lighting (0x8071) the times go to.
NO_LIGHTING_TIMES_NOTE = f"saved; {NO_LIGHTING}, so the keyboard does not get the new times"

#: What `color` and `effect` say for a keyboard attached without that lighting: saved, and
#: shown on a synced mouse alone.
NO_LIGHTING_NOTE = f"saved; {NO_LIGHTING}, so the keyboard does not show it"

# Each idle or wake notice (0x8071 address 0x10) comes as a train of eight over about 4 s until
# answered. A notice of the same kind within this of the previous answered copy is the same
# train: the last gap inside a train is 2.0 s, the shortest seen between trains 27 s. Timed
# from that copy, not the last repaint, which the eighth copy outlasts.
EVENT_TRAIN_GAP = 3.0

#: An idle notice sooner than the idle time less this after the idle times were written (fn 7)
#: is stale: the write restarts the keyboard's count, so the notice predates it. Two seconds
#: covers the firmware's rounding (Lighting.set_power_times).
STALE_IDLE_MARGIN = 2.0

#: A lighting change rewrites the idle times (fn 7) to restart the idle count, as G HUB does, at
#: most once this often, so a color drag does not send fn 7 each frame. A skipped write lets the
#: keyboard dim up to 2 s early, a fifth of IDLE_RANGE's 10 s minimum. An idle-dimmed keyboard
#: has its first change written unless a change's write failed under 2 s before: a write the
#: keyboard took that recent would make its notice stale.
IDLE_RESTART_INTERVAL = 2.0
#: After a lighting change wakes the keyboard from idleness, the wake its times write brings ends
#: after the notice and on the G915 loses a per-key paint sent before then: a repaint at the
#: notice stayed dark, one 0.4 s later showed (measured 2026-09-26).
WAKE_REPAINT_DELAY = 0.5

#: The address of the idle and wake notices: data 00 is idle, 01 is wake. Address 0x00 is the
#: effect-sync report, which the claim also turns on and which comes while an animation runs.
IDLE_WAKE_EVENT = 0x10

IDLE_NOTICE, WAKE_NOTICE = 0x00, 0x01

#: How long after a reconnection report on the receiver the cable is looked for again: the
#: report also comes as the cable goes in, once 0.32 s before the kernel saw it. Waiting for
#: the cable before setting the keyboard up showed the onboard wave at every power-on.
CABLE_WAIT = 0.5

#: One step of the keyboard's hand-back: whether there is anything to do, the step, and what
#: it restores, for the stop's log line.
Step = tuple[object, Callable[[], object], str]


def run_steps(steps: Iterable[Step]) -> tuple[list[str], list[str]]:
    """Run each step that has something to do, in order: what was done, and what was not,
    each with why. A step that gets no answer skips the rest, each of which would wait three
    seconds, and nothing raises. The keyboard's own: a mouse's hand-back asks once more after
    a first step nothing answered (Mice.hand_back)."""
    done: list[str] = []
    missed: list[str] = []
    skip = None
    for present, action, what in steps:
        if not present:
            continue
        if skip is not None:
            missed.append(f"{what} ({skip})")
            continue
        try:
            action()
        except (HidppFailure, IndexError, OSError) as exc:
            # IndexError: set_keyboard_mode indexes the reply, behind the transport's own
            # length check. Nothing on the cleanup path may raise.
            log.debug("could not restore %s: %s", what, exc)
            missed.append(f"{what} ({failure_reason(exc)})")
            if unanswered(exc):
                skip = ("skipped after a timeout" if not unreachable(exc)
                        else "skipped: its receiver cannot reach it")
        else:
            done.append(what)
    return done, missed


class Keyboard:
    """The G915 and what the daemon holds for it."""

    # -- the controller ------------------------------------------------------

    def __init__(self, daemon: Daemon) -> None:
        self.d = daemon
        self.dev: Hidpp | None = None
        self.lighting: Lighting | None = None
        self.keys: DivertedKeys | None = None
        #: The attached keyboard's product id, which differs per route; the battery curve is
        #: keyed to it.
        self.keyboard_product: int | None = None
        #: What the attached keyboard can do, for the window to render from.
        self.keyboard_able: list[str] = []
        #: The keyboard's 0x1D4B index, or None. On the receiver the node stays open while the
        #: keyboard is off, so its power-on report here says it came back without its diversion,
        #: claim and host mode, as the receiver's link-up notice does with keyboard_link_notices on.
        self.keyboard_wireless: int | None = None
        #: The last connect failure logged, so a repeat is said once.
        self._connect_said: str | None = None
        #: Only the first connect to reach its HOST write says it found the keyboard in HOST
        #: mode: a move to the cable, and a connect the keyboard stopped answering, keep the
        #: mode on purpose.
        self._connected_once = False
        #: Monotonic time before which nothing is asked of or written to the keyboard, once it is
        #: marked not answering (mark_keyboard_silent). Cleared by any report or answer from it.
        self.keyboard_silent_until = 0.0
        #: Marked not answering, with no report from it and no setup again since, the mark run out
        #: or not: its idle notices are not coming, so its idle level says nothing
        #: (idle_level_known).
        self._unheard = False
        #: Said at info to be not answering, with no report or answer from it since: a recheck
        #: it misses is said at debug, and its first answer at info (mark_keyboard_answering).
        self._silence_said = False
        #: Why the last apply_profile did not reach the keyboard, or None; for status. And what
        #: the device said then, for a reply's `detail`, or None.
        self.last_apply_error: str | None = None
        self.last_apply_detail: str | None = None
        #: When the M and MR LEDs are due (write_due_leds), or None.
        self.leds_due: float | None = None
        #: The last battery reading, as (handle, monotonic time, reading). Keyed by the handle: a
        #: reconnect or another route is another reading.
        self._battery_read: tuple[Hidpp, float, dict] | None = None
        #: When the reading the low-battery notices last counted was taken, so one counts once.
        self._battery_counted: float | None = None
        #: When to look next for the cable while on the receiver (keyboard_moved_to_cable).
        self._cable_check_at = 0.0
        #: When the keyboard was last set up: connected, or set up again after a power-on report
        #: or a link notice. Forgotten at a link-down.
        self._keyboard_reapplied_at = 0.0
        #: The second cable look, CABLE_WAIT after a reconnection report, or None
        #: (look_for_cable_again).
        self._cable_look_at: float | None = None
        #: The level every repaint goes through, LIT, DIM or DARK: the darker of the two below
        #: (set_lights).
        self.lights = LIT
        #: The level the screen lock asks for (LIT unless lights_off_when_locked), and the one
        #: the idle notices ask for (LIT under never_dim).
        self.lock_lights = LIT
        self.idle_lights = LIT
        #: When the idle notice dimmed the keyboard; it goes dark idle_off_after later
        #: (idle_deadline). None while awake.
        self._idle_since: float | None = None
        #: When the idle times were last written (fn 7), which restarts the idle count, or None
        #: when the keyboard refused the claim's: an idle notice sooner than the idle time after
        #: is stale.
        self._times_written_at: float | None = None
        #: The idle time that write carried, which the keyboard counts by until it takes
        #: another: idle_dim_after runs ahead of it after a refused or owed write.
        self._dim_written = 0
        #: A stale idle notice since that write was put back; the next is only answered.
        self._stale_put_back = False
        #: The keyboard's last answered notice was an idle one (fn 5 `03`), so once it is idle a
        #: times write wakes it, lit or not: under never dim, or after a stale notice left answered.
        self._idle_answered = False
        #: When a lighting change last wrote or tried the idle times, for IDLE_RESTART_INTERVAL.
        self._change_times_at = float("-inf")
        #: A lighting change or new idle times woke the keyboard from idleness, whose wake loses a
        #: per-key paint sent before it ends: keyboard_woke has it painted again at `_repaint_due`.
        self._paint_at_wake = False
        self._repaint_due: float | None = None
        #: The latest `preview` since the last apply or paint, sent again after a wake repaint so
        #: that the repaint does not replace a drag in progress.
        self.last_preview: dict | None = None
        #: A saved lighting change the keyboard did not get, marked not answering or not
        #: answering its write: applied once it answers (tend_idle).
        self.apply_owed = False
        #: The same for the idle times (write_idle_times): written once it answers. A claim
        #: writes them, so it clears this.
        self._times_owed = False
        #: When the keyboard last sent an idle or wake notice that was answered, or was claimed by
        #: connect() or reconfigure_keyboard(), which brings a wake notice within 1.5 s; and its
        #: kind, since the other kind starts a train of its own.
        self._last_rgb_event = 0.0
        self._last_rgb_kind: int | None = None
        #: The parts, each built with the daemon and this controller, which it keeps as
        #: `keyboard`. The command handlers are `handlers`: `commands` is the table below.
        self.paint = Paint(daemon, self)
        self.handlers = KeyboardCommands(daemon, self)

    def commands(self) -> dict[str, Callable[[dict], dict]]:
        return self.handlers.commands()

    def deadlines(self) -> list[float]:
        """When the keyboard next needs the loop."""
        cable = self.dev is not None and self.keyboard_product not in KEYBOARD_CABLED
        due = (self._cable_check_at if cable else None, self._cable_look_at, self.leds_due,
               self.idle_deadline(), self._repaint_due)
        return [at for at in due if at is not None]

    # `dev`, `lighting` and `keys` for code reached only while the one it uses is there, after a
    # check made in another function, which the type checker does not follow. Nothing checks
    # these casts.

    @property
    def _dev(self) -> Hidpp:
        return cast("Hidpp", self.dev)

    @property
    def _lighting(self) -> Lighting:
        return cast("Lighting", self.lighting)

    @property
    def _keys(self) -> DivertedKeys:
        return cast("DivertedKeys", self.keys)

    # -- connecting and claiming ---------------------------------------------

    def connect(self) -> bool:
        """Open the keyboard and take it over, or leave nothing behind.

        Every device call up to the registration is inside one guard: a keyboard unplugged or
        answering short part way through leaves half-built handles, and the loop, which calls this
        every RECONNECT_DELAY, retries only while `dev` is None.
        """
        found = find_attached_keyboard()
        if found is None:
            return False
        path, product, derived = found
        self.keyboard_product = product
        started = time.monotonic()
        try:
            # The index is probed, not assumed: on the receiver's node 0xFF is the receiver
            # itself. On the probe budget, since the loop waits while this runs.
            opened = open_device(path, KEYBOARD_WITNESS, prefer=derived, **MOUSE_PROBE)
            if opened is None:
                # Once at info: this repeats while the keyboard is off behind its receiver.
                said = f"no keyboard answered on {path}; is it switched on?"
                log.log(logging.DEBUG if said == self._connect_said else logging.INFO, "%s", said)
                self._connect_said = said
                self.d.note_slow(started, f"keyboard connect on {path}",
                               "no keyboard answering on it", repeats="keyboard")
                return False
            self.dev, index = opened
            self.keyboard_able = keyboard_capabilities(self.dev)
            wireless = self.dev.feature(F_WIRELESS)
            self.keyboard_wireless = wireless.index if wireless else None
            # Each piece is built only if the keyboard has its feature. Lighting needs only 0x8071;
            # per-key (0x8081) is optional.
            self.lighting = Lighting.find(self.dev)
            self.keys = DivertedKeys(self.dev) if "g_keys" in self.keyboard_able else None
            if self.set_keyboard_mode(MODE_HOST) == MODE_HOST and not self._connected_once:
                # set_keyboard_mode says nothing when the mode already matches; this line is
                # the only trace of a stop that did not hand the keyboard back.
                log.info("%s was already in host mode at start: whatever drove it last "
                         "did not hand it back", keyboard_name(product))
            self._connected_once = True
            if self.lighting is not None:
                self.claim_keyboard()
                self.keyboard_able.append("lighting")
            if self.keys is not None:
                self.keys.divert(True)
            self.apply_profile()
            if self.dev is None:
                # apply_profile tears down rather than raising when the keyboard goes away.
                self.d.note_slow(started, f"keyboard connect on {path}",
                               "it went away during the first paint", repeats="keyboard")
                return False
            # Makes the claim's wake notice a copy of a wake already taken (`_last_rgb_event`).
            self._last_rgb_event, self._last_rgb_kind = time.monotonic(), WAKE_NOTICE
            self.d.selector.register(self.dev.fileno, selectors.EVENT_READ, "hid")
        except (OSError, HidppFailure, IndexError, RuntimeError) as exc:
            self.connect_failed(str(path), started, exc)
            return False
        self._connect_said = None
        # A power-on report queued during the connect asks for the setup it just had.
        self._keyboard_reapplied_at = time.monotonic()
        self.d.forget_slow("keyboard")
        self.d.note_slow(started, f"keyboard connect on {path}")
        log.info("%s connected on %s (%04x, device index 0x%02x): %s",
                 keyboard_name(product), path, product, index,
                 ", ".join(self.keyboard_able) or "nothing this build drives")
        self.turn_on_link_notices()
        return True

    def connect_failed(self, path: str, started: float, exc: Exception) -> None:
        """What connect does with a failure inside its guard, on the node at `path`: say it,
        take down what was built and note a slow attempt.

        Said once per distinct failure: a node the udev rule never granted fails the same way
        every RECONNECT_DELAY. A keyboard gone quiet after its probe is off or out of range,
        not a fault, and is handed nothing back: each step would wait out its own timeout.
        """
        quiet = unanswered(exc)
        if quiet:
            said = f"the keyboard on {path} stopped answering part way through its setup: {exc}"
        elif isinstance(exc, PermissionError):
            # Raised only by open_device's open: the transport turns a later OS error into a
            # HidppFailure. NO_RULE since nothing else in the journal says why.
            said = f"cannot open the keyboard's {path}: {reason(exc)}; {NO_RULE}"
        else:
            said = f"could not set the keyboard up on {path}: {reason(exc)}"
        level = logging.INFO if quiet else logging.WARNING
        log.log(logging.DEBUG if said == self._connect_said else level, "%s", said)
        self._connect_said = said
        self.teardown_device(hand_back=not quiet)
        self.d.note_slow(started, f"keyboard connect on {path}", str(exc), repeats="keyboard")

    def set_keyboard_mode(self, mode: int) -> int | None:
        """0x8100 setOnboardMode on the keyboard: HOST while the daemon runs.

        The only 0x8100 write made on the keyboard; its profile memory is never touched. The
        mode is confirmed with getCurrentProfile, which names a profile only in ONBOARD mode.
        The answer is the mode the keyboard was found in, or None when it has no 0x8100.
        """
        onboard = self._dev.feature(F_ONBOARD)
        if onboard is None:
            return None
        current = read_mode(self._dev, onboard)
        if current != mode:
            if mode == MODE_HOST:
                # Before the write: a connect cut short after it, a lost reply included, left
                # HOST mode itself (connect's `_connected_once`).
                self._connected_once = True
            write_mode(self._dev, onboard, mode)
        running = running_sector(self._dev, onboard)
        expected_profile = 0 if mode == MODE_HOST else 1
        if (running == 0) != (mode == MODE_HOST):
            log.warning("keyboard mode %#04x set but getCurrentProfile reports %d, expected %d",
                        mode, running, expected_profile)
        elif current != mode:
            log.info("keyboard onboard mode %#04x -> %#04x", current, mode)
        return current

    def claim_keyboard(self) -> None:
        """The lighting claim, then the idle and sleep times (fn 7) and the active power state
        (fn 8 `01 01`), as G HUB's claim has them, though G HUB sends those two between the
        claim's two writes.

        The times go at every claim because link setup reloads them from the onboard profile;
        the active state because the firmware never returns to it by itself while the host holds
        the power modes. After the claim's `05` the wake notice is off, so the times bring none.
        The keyboard's error reply to either leaves it claimed, a refused fn 7 with its own times;
        other failures go to the caller.
        """
        self._lighting.claim()
        self._times_written_at, self._paint_at_wake, self._repaint_due = None, False, None
        self._times_owed = self._idle_answered = False
        try:
            self._send_idle_times()
        except HidppError as exc:
            if exc.unreachable:
                raise
            log.info("the keyboard refused its idle times (%s); it keeps its own", exc)
        try:
            self._lighting.set_active()
        except HidppError as exc:
            if exc.unreachable:
                raise
            log.info("the keyboard refused the active power state (%s)", exc)
        self.keyboard_awake()

    def _send_idle_times(self) -> None:
        """Write the configured idle times (fn 7) and note when, for stale_idle_notice."""
        self._lighting.set_power_times(self.d.config.idle_dim_after, self.d.config.idle_off_after)
        self._times_written_at, self._stale_put_back = time.monotonic(), False
        self._dim_written = self.d.config.idle_dim_after

    def keyboard_awake(self, lock: str | None = None) -> None:
        """The keyboard's own level back to lit, with no dark due, not painted: the caller
        paints. A resume passes the lock's level as `lock` (Session.woke_up), so the shown
        level is set once."""
        if lock is not None:
            self.lock_lights = lock
        self.idle_lights = LIT
        self._idle_since = None
        self.lights = self.lock_lights

    def turn_on_link_notices(self) -> None:
        """With `keyboard_link_notices` on and the keyboard on its receiver, have the receiver
        send its link notices (take_link_notice) by setting register 0x00's wireless bit. A
        failure is logged and changes nothing else."""
        if (not self.d.config.keyboard_link_notices or self.dev is None
                or self.keyboard_product in KEYBOARD_CABLED):
            return
        receiver = f"the {keyboard_name(self.keyboard_product)}'s receiver"
        step = "the first read"
        try:
            found = self.dev.read_register(NOTIFICATION_FLAGS, **MOUSE_PROBE)
            if found[1] & WIRELESS_NOTICES:
                log.info("%s sends link notices already (register 0x00 %s)",
                         receiver, found.hex(" "))
                return
            wanted = bytes([found[0], found[1] | WIRELESS_NOTICES, found[2]])
            step = f"the write of {wanted.hex(' ')} over {found.hex(' ')}"
            self.dev.write_register(NOTIFICATION_FLAGS, wanted, **MOUSE_PROBE)
            step = f"the read back after writing {wanted.hex(' ')}"
            now = self.dev.read_register(NOTIFICATION_FLAGS, **MOUSE_PROBE)
        except HidppFailure as exc:
            log.warning("could not turn on link notices at %s (%s): %s", receiver, step, exc)
            return
        if now[1] & WIRELESS_NOTICES:
            log.info("%s sends link notices now (register 0x00 %s, was %s)",
                     receiver, now.hex(" "), found.hex(" "))
        else:
            log.warning("%s took register 0x00 %s and reads back %s: no link notices",
                        receiver, wanted.hex(" "), now.hex(" "))

    def take_link_notice(self, data: bytes) -> None:
        """The receiver's notice that the keyboard's link went down or came up (hidpp.LINK_NOTICE,
        turn_on_link_notices).

        Down, the keyboard is off, out of range or on its cable: marked not answering, its held
        keys let go of, the cable looked for, and REAPPLY_INTERVAL reset, since the next link is a
        new link setup. Up, it is set up again at once, since link setup drops the claim; its
        power-on report then falls within REAPPLY_INTERVAL. The silent mark's recheck and the
        power-on report cover a notice that does not come.
        """
        if data[0] & LINK_DOWN:
            self._keyboard_reapplied_at = float("-inf")
            self.mark_keyboard_silent("its receiver lost the link")
            self.d.bindings.keyboard_gone(held_only=True)
            self.keyboard_moved_to_cable(force=True)
            return
        self.say_set_up_again("linked to its receiver again")
        self.reconfigure_keyboard()

    def say_set_up_again(self, what: str) -> None:
        """Say what asked for the setup again: at info once REAPPLY_INTERVAL has passed, else
        at debug, since reconfigure_keyboard then sets nothing up."""
        name = keyboard_name(self.keyboard_product)
        if time.monotonic() - self._keyboard_reapplied_at >= REAPPLY_INTERVAL:
            log.info("%s %s; setting it up again", name, what)
        else:
            log.debug("%s %s; set up under %d s ago, so not again", name, what, REAPPLY_INTERVAL)

    def reconfigure_keyboard(self) -> bool:
        """Host mode, the claim and the diversion again, then the profile.

        On the handle the daemon holds, which through the receiver is still good: a teardown and
        connect would show the firmware's effect in between. Held keys are let go, since none is
        held across a power cycle. At most once per REAPPLY_INTERVAL, counted from a connect too:
        a mouse's power-on report comes in pairs, and the keyboard's has not been measured. A
        failure starts over with a reconnect, handing nothing back to a keyboard that did not
        answer. False when the interval kept it from running.
        """
        now = time.monotonic()
        if now - self._keyboard_reapplied_at < REAPPLY_INTERVAL:
            return False
        self._keyboard_reapplied_at = now
        # Its caller has said it is back.
        self.mark_keyboard_answering(quiet=True)
        self._unheard = False
        self.d.bindings.keyboard_gone(held_only=True)
        try:
            self.set_keyboard_mode(MODE_HOST)
            if self.lighting is not None:
                self.claim_keyboard()
            if self.keys is not None:
                self.keys.divert(True)
        except (HidppFailure, IndexError, OSError) as exc:
            quiet = unanswered(exc)
            # Gone to its cable, or off again: the cable is looked for first, and a keyboard
            # that did not answer is handed nothing back, since each step would wait out a timeout.
            if quiet and self.keyboard_moved_to_cable(force=True):
                return True
            log.log(logging.INFO if quiet else logging.WARNING,
                    "could not set the keyboard up again (%s); reconnecting", reason(exc))
            self.teardown_device(hand_back=not quiet)
            return True
        self.apply_profile()
        # Makes the claim's wake notice a copy of a wake already taken (`_last_rgb_event`).
        self._last_rgb_event, self._last_rgb_kind = time.monotonic(), WAKE_NOTICE
        return True

    def keyboard_moved_to_cable(self, force: bool = False) -> bool:
        """Drop the keyboard's receiver route once its cable is plugged in.

        A G915 given its cable answers on the cable's node, and its receiver's node stays,
        answering each request for it with HID++ 1.0 error 0x08, unknown device; no DeviceGone
        comes. connect() prefers the cable; this makes the same choice while attached. Looked
        for through sysfs every RECONNECT_DELAY, or at once with `force`. True when the receiver
        route was dropped, for the loop to connect again at once.
        """
        if (self.dev is None or self.keyboard_product in KEYBOARD_CABLED
                or (time.monotonic() < self._cable_check_at and not force)):
            return False
        self._cable_check_at = time.monotonic() + RECONNECT_DELAY
        found = find_attached_keyboard(cabled_only=True)
        if found is None:
            return False
        log.info("%s moved from its receiver (%s) to its cable (%s)",
                 keyboard_name(self.keyboard_product), self.dev.path, found[0])
        # Nothing is handed back through the receiver, which cannot reach it; the cable's
        # connect takes it over.
        self.teardown_device(hand_back=False)
        return True

    def look_for_cable_again(self) -> bool:
        """The second look for the cable, CABLE_WAIT after a reconnection report on the receiver.
        True when the receiver route was dropped."""
        if self._cable_look_at is None or time.monotonic() < self._cable_look_at:
            return False
        self._cable_look_at = None
        return self.keyboard_moved_to_cable(force=True)

    # -- answering, silence and the battery ----------------------------------

    def keyboard_answering(self) -> bool:
        return self.dev is not None and self.keyboard_silent_until <= time.monotonic()

    def keyboard_answers(self) -> bool:
        """Whether the keyboard answers one cheap request on the probe's budget. One that does
        not is marked not answering; a write or read that failed is no answer, and marks it
        too; an error reply is an answer. For a poll and a suspend, which must not wait out the
        full timeout on a keyboard that is off."""
        if not self.keyboard_answering():
            return False
        try:
            self._dev.lookup(F_WIRELESS, **MOUSE_PROBE)
        except DeviceGone:
            log.info("the keyboard went away")
            self.teardown_device()
            return False
        except HidppFailure as exc:
            if unanswered(exc):
                self.keyboard_unanswered(exc)
                return False
            if not isinstance(exc, HidppError):
                # A write or read that failed outside hidpp.GONE_ERRNOS, such as a USB link error:
                # no answer, so marked, as a timeout is.
                self.mark_keyboard_silent(str(exc))
                return False
        self.mark_keyboard_answering()
        return True

    def mark_keyboard_silent(self, why: str) -> None:
        """Ask and write the keyboard nothing for MOUSE_SILENT_RECHECK, or until it sends
        something: each unanswered write holds the daemon's only thread three seconds. `why` is
        for the log line."""
        if self.keyboard_silent_until > time.monotonic():
            return
        log.log(logging.DEBUG if self._silence_said else logging.INFO,
                "the keyboard is not answering (%s); left unasked for %d s, or until it sends "
                "something", why, MOUSE_SILENT_RECHECK)
        self._silence_said = True
        self.keyboard_silent_until = time.monotonic() + MOUSE_SILENT_RECHECK
        followed = self.idle_level_known()
        self._unheard = True
        # Kept, it would answer for a keyboard that may still be off once the mark runs out.
        self._battery_read = None
        self.mouse_follows_the_desktop(followed)

    def mark_keyboard_answering(self, quiet: bool = False) -> None:
        """Clear the mark: the keyboard sent a report or answered a request. The first answer
        after the not-answering line is logged, unless `quiet`: the caller says it."""
        if self._silence_said and not quiet:
            log.info("the keyboard is answering again")
        self._silence_said = False
        self.keyboard_silent_until = 0.0

    def keyboard_unanswered(self, exc: BaseException) -> None:
        """A keyboard request went unanswered (`unanswered`): mark it not answering and look for its
        cable at once, through sysfs. A cable found drops the receiver route, and the caller finds
        `dev` gone."""
        self.mark_keyboard_silent("its receiver says it cannot reach it"
                                  if unreachable(exc) else "no reply")
        self.keyboard_moved_to_cable(force=True)

    @contextlib.contextmanager
    def keyboard_writing(self) -> Iterator[None]:
        """Around a command's own keyboard write: no answer marks the keyboard not answering,
        and the failure goes on to the caller; an answer clears the mark."""
        try:
            yield
        except (HidppFailure, LightingError) as exc:
            if unanswered(exc):
                self.keyboard_unanswered(exc)
            raise
        self.mark_keyboard_answering()

    def keyboard_sw_control(self) -> int | None:
        """Who holds the lighting, as the keyboard says, or None if it does not say.

        A live request on every status, and how status learns the keyboard is off behind its
        receiver: on a short budget, since the CLI and the window give status three seconds in
        all. No answer leaves the keyboard unasked, battery included, for MOUSE_SILENT_RECHECK.
        """
        if self.lighting is None or self.keyboard_silent_until > time.monotonic():
            return None
        try:
            control = self.lighting.sw_control(timeout=MOUSE_PROBE_TIMEOUT, retries=1)
        except DeviceGone:
            log.info("the keyboard went away while reading its lighting state")
            self.teardown_device()
            return None
        except (HidppFailure, IndexError) as exc:
            if unanswered(exc):
                self.keyboard_unanswered(exc)
            else:
                log.debug("the keyboard's lighting-state read failed: %s", exc)
            return None
        self.mark_keyboard_answering()
        return control

    def keyboard_battery(self) -> dict | None:
        """The keyboard's battery reading, or None when there is nothing to say.

        Kept for KEYBOARD_BATTERY_INTERVAL per handle, since the window asks every three seconds,
        and dropped when the keyboard is marked not answering. No reading, or no answer, is
        followed by one probe (keyboard_answers), which marks only a keyboard that is off.
        """
        if not self.keyboard_answering():
            return None
        kept = self._battery_read
        if (kept is not None and kept[0] is self.dev
                and time.monotonic() - kept[1] < KEYBOARD_BATTERY_INTERVAL):
            return kept[2]
        try:
            reading = read_battery(self._dev, self.keyboard_product, "keyboard")
            if reading is not None:
                self._battery_read = (self._dev, time.monotonic(), reading)
                self.mark_keyboard_answering()
            else:
                # A refusal, or no battery feature: the probe says whether the keyboard answers.
                self.keyboard_answers()
            return reading
        except DeviceGone:
            log.info("the keyboard went away while reading its battery")
            self.teardown_device()
            return None
        except (HidppFailure, IndexError) as exc:
            # IndexError is the decode's, behind the transport's own length check. The rest of
            # status still stands.
            log.debug("the keyboard's battery read failed: %s", exc)
            if unanswered(exc):
                # One probe, not keyboard_unanswered: a lone missed read does not mark a
                # keyboard that answers.
                self.keyboard_answers()
            return None

    def battery_for_notices(self) -> dict | None:
        """The keyboard's reading for the battery notices, or None. On its cable the keyboard is
        charging and not asked. A reading already counted is read again, so one cannot count
        twice."""
        if self.keyboard_product in KEYBOARD_CABLED:
            return {"external_power": True}
        kept = self._battery_read
        fresh = (kept is not None and kept[0] is self.dev
                 and time.monotonic() - kept[1] < KEYBOARD_BATTERY_INTERVAL
                 and kept[1] != self._battery_counted)
        if not fresh:
            if not self.keyboard_answers():
                return None
            self._battery_read = None
        reading = self.keyboard_battery()
        if reading is not None and self._battery_read is not None:
            self._battery_counted = self._battery_read[1]
        return reading

    # -- the lighting and its apply path -------------------------------------

    def shown_color(self) -> int:
        """The color the board shows around any per-key overrides: the layer's color
        (Games.active_lighting), or black under disabled, which is in STATIC_EFFECTS so that
        overrides show over it and whose color must not fill the board."""
        effect_name, color, _keys, _params = self.d.games.active_lighting()
        return 0x000000 if effect_name == "disabled" else color

    def change_unreached(self) -> str | None:
        """Why a lighting change cannot be written now: KEYBOARD_AWAY for a keyboard away, or
        KEYBOARD_SILENT for one with lighting marked not answering, which is owed the apply
        (tend_idle). None otherwise, for a keyboard without lighting too, which each caller
        refuses or skips on its own."""
        if self.dev is None or self.lighting is None or self.keyboard_answering():
            return KEYBOARD_AWAY if self.dev is None else None
        self.apply_owed = True
        return KEYBOARD_SILENT

    def apply_profile(self) -> str | None:
        """Write the active lighting to the keyboard once: lit, the board is Paint.write_lit's
        and the loop writes the M/MR LEDs LED_SETTLE later (write_due_leds); dark, the board and
        the LEDs go off now. Every apply drops an LED write left waiting.

        A keyboard gone part way through is torn down, never raised: this runs from the loop and
        the socket. One marked not answering is not written to; a write nothing answers marks it.

        The answer is why the keyboard does not show the profile, kept as `last_apply_error` for
        status with the device's words in `last_apply_detail`; else the lights-down note, or None.
        A command passes it on as its reply's note.
        """
        effect_name, color, per_key, extra_params = self.d.games.active_lighting()
        shown = self.shown_color()
        self.leds_due = self.last_preview = None
        # Guarded on lighting, not keys: without diversion only the LEDs are skipped.
        failed = self.change_unreached()
        detail = None
        if self.lighting is None or failed is not None:
            self.d.mice.color.sync_mouse_color(shown)
            self.last_apply_error, self.last_apply_detail = failed, detail
            return failed
        # Owed again only by a write nothing answers (not_shown): a refusal would be refused
        # again at every pass.
        self.apply_owed = False
        try:
            if self.lights == DARK:
                # Board and M/MR LEDs off, nothing else written. A synced mouse goes by at_level:
                # the keyboard's idleness counts only once the whole desktop is idle.
                if self.keys is not None:
                    self.keys.all_leds_off()
                self.paint.darken(self.lighting, mouse=False)
            else:
                self.paint.write_lit(self.lighting, effect_name, color, per_key, extra_params,
                                     shown, self.lights == DIM)
                if self.keys is not None:
                    self.leds_due = time.monotonic() + LED_SETTLE
            self.mark_keyboard_answering()
        except (HidppFailure, LightingError) as exc:
            overlay = self.d.games.overlay
            what = (f"game profile {overlay.name!r}" if overlay is not None
                    else f"profile {self.d.config.active}")
            failed, detail = self.not_shown(exc, what)
        # After the keyboard, so a slow mouse never delays it, and outside its try, so a mouse
        # failure cannot abort the apply.
        self.d.mice.color.sync_mouse_color(shown)
        self.last_apply_error, self.last_apply_detail = failed, detail
        return failed or self.lights_note()

    def not_shown(self, exc: HidppFailure | LightingError, what: str) -> tuple[str, str | None]:
        """Why the keyboard does not show `what`, saved already, after its write failed, and the
        device's words: gone (torn down), not answering (keyboard_unanswered, owed unless its
        cable look dropped the receiver route), not reached (a link error, marked and owed) or
        refused. For apply_profile and `paint`, which answer it alike."""
        if isinstance(exc, DeviceGone):
            log.info("keyboard went away while applying %s: %s", what, exc)
            self.teardown_device()
            return KEYBOARD_AWAY, None
        if unanswered(exc):
            # Off, out of range or gone to its cable.
            log.info("%s not applied: %s", what, exc)
            self.keyboard_unanswered(exc)
            # Not owed to a receiver route the cable look dropped: the cable's connect applies it.
            self.apply_owed = self.dev is not None
            return unanswered_note(exc, self.dev is None), str(exc)
        if isinstance(exc, HidppFailure) and not isinstance(exc, HidppError):
            # The kernel failed the write or read outside hidpp.GONE_ERRNOS, such as a USB
            # link error: no answer, as keyboard_answers takes it, so marked and owed.
            log.info("%s not applied: %s", what, exc)
            self.mark_keyboard_silent(str(exc))
            self.apply_owed = self.dev is not None
            return (f"Gearbench could not reach the keyboard ({failure_reason(exc)}); the change "
                    "is saved and shows when it is back"), str(exc)
        log.warning("could not apply %s: %s", what, exc)
        return f"the keyboard did not take it: {exc}", str(exc)

    def write_led(self, action: Callable[[DivertedKeys], None], what: str,
                  owed: bool = False) -> None:
        """One indicator LED write, `action` on the diverted keys, that cannot take the daemon
        down with it: these run from the key handler on the main loop, and the keyboard can
        vanish before the write. `owed` is an apply's own LED write (write_due_leds), where a
        write the kernel fails outside hidpp.GONE_ERRNOS is no answer and marks the keyboard,
        as on the apply's lighting write (not_shown)."""
        keys = self.keys
        if keys is None:
            return
        try:
            action(keys)
        except DeviceGone as exc:
            log.info("keyboard went away writing the %s LED: %s", what, exc)
            self.teardown_device()
        except HidppFailure as exc:
            if unanswered(exc):
                self.keyboard_unanswered(exc)
            elif owed and not isinstance(exc, HidppError):
                self.mark_keyboard_silent(str(exc))
            log.debug("could not write the %s LED: %s", what, exc)
        else:
            self.mark_keyboard_answering()

    def show_mr(self, on: bool, dark_too: bool = False) -> None:
        """The MR LED as MR is held or let go. Not on a keyboard marked not answering, whose
        timeout would hold the loop. On a dark board only with `dark_too`: the apply that
        lights it writes the LED (write_due_leds)."""
        if (dark_too or self.lights != DARK) and self.keyboard_answering():
            self.write_led(lambda keys: keys.set_mr_led(on), "MR")

    def write_due_leds(self) -> None:
        """Write the M and MR LEDs once LED_SETTLE has passed since the apply that lit the board.
        What they show is read now, so an M key or MR pressed meanwhile counts.

        Nothing is written to a keyboard gone, dark or marked not answering. One marked, or whose
        write nothing answered or the kernel failed outside hidpp.GONE_ERRNOS (a link error),
        is owed the apply, which writes them again once it answers; such an M write skips the MR
        one. An
        OSError, which write_led lets through (its teardown closes the node, and a close can
        fail), starts the keyboard over, where it would otherwise end the loop.
        """
        if self.leds_due is None or time.monotonic() < self.leds_due:
            return
        self.leds_due = None
        if self.lights == DARK:
            return
        if self.keyboard_answering():
            try:
                self.write_led(lambda keys: keys.set_m_led(self.d.config.active), "M", owed=True)
                if self.keyboard_answering():
                    self.write_led(lambda keys: keys.set_mr_led(self.d.bindings.shifted), "MR",
                                   owed=True)
            except OSError as exc:
                log.warning("writing the M and MR LEDs failed (%s); reconnecting", reason(exc))
                self.teardown_device()
        if self.dev is not None and not self.keyboard_answering():
            # A keyboard gone gets the apply at its connect instead.
            self.apply_owed = True

    def switch_profile(self, index: int) -> str | None:
        """Make an M profile active, or just relight its LED if it already is.

        The firmware clears the LED on every lighting write. Not relit while the board is dark:
        the apply that lights it writes the LEDs. Nor on a keyboard marked not answering, whose
        timeouts would hold the loop. The answer is apply_profile's.
        """
        if not self.d.config.switches_to(index):
            if index in PROFILE_IDS and self.lights != DARK and self.keyboard_answering():
                self.write_led(lambda keys: keys.set_m_led(index), "M")
            return None
        self.d.config.active = index
        log.info("profile %d (%s)", index, escaped(self.d.config.profile.name))
        failed = self.apply_profile()
        self.d.bindings.stop_orphaned_macro()
        self.d.persist()
        return failed

    # -- the idle notices and the light level --------------------------------

    def take_notice(self, data: bytes) -> None:
        """An idle notice (data 00) or a wake notice (01) from the keyboard.

        Every copy is answered at once, which stops the train. A train's first answered copy
        moves the keyboard's own level through set_lights, which paints only when what the board
        shows changes, so an animation is not restarted for nothing. An unanswered copy does not
        count, so the next, queued meanwhile, can still be the first. A notice of the other kind
        starts its own train.
        """
        kind = data[0] if data else None
        if kind not in (IDLE_NOTICE, WAKE_NOTICE):
            log.info("0x8071 event addr 0x%02x data %s: neither idle nor wake; left alone",
                     IDLE_WAKE_EVENT, data[:4].hex(" "))
            return
        now = time.monotonic()
        if not self.answer_notice(kind):
            return
        previous, self._last_rgb_event = self._last_rgb_event, now
        before, self._last_rgb_kind = self._last_rgb_kind, kind
        if kind == before and now - previous < EVENT_TRAIN_GAP:
            log.debug("0x8071 event addr 0x%02x data %s (same burst, answered, not repainting)",
                      IDLE_WAKE_EVENT, data[:4].hex(" "))
            return
        if kind == IDLE_NOTICE:
            self.keyboard_went_idle()
        else:
            self.keyboard_woke()

    def answer_notice(self, kind: int) -> bool:
        """Answer an idle or wake notice. False when the keyboard did not answer, which marks it
        not answering and leaves the level as it is; after an error reply the level still
        moves. DeviceGone goes to the caller (pump)."""
        try:
            if kind == IDLE_NOTICE:
                self._lighting.answer_idle()
            else:
                self._lighting.answer_wake()
        except DeviceGone:
            raise
        except HidppFailure as exc:
            what = "idle" if kind == IDLE_NOTICE else "wake"
            if unanswered(exc):
                log.info("the keyboard's %s notice could not be answered: %s", what, exc)
                self.keyboard_unanswered(exc)
                return False
            if isinstance(exc, HidppError):
                log.warning("the keyboard refused the answer to its %s notice: %s", what, exc)
            else:
                log.warning("could not answer the keyboard's %s notice: %s", what, exc)
        else:
            self._idle_answered = kind == IDLE_NOTICE
        return True

    def keyboard_went_idle(self) -> None:
        """The first answered copy of an idle notice: dim now and dark idle_off_after later
        (idle_deadline), unless never dim is on. Only from lit, so a repeat notice does not put
        the dark off."""
        why = "the keyboard is idle"
        if self.d.config.never_dim:
            log.info("%s; answered, not dimmed: never dim is on", why)
            return
        if self.stale_idle_notice():
            return
        if self.idle_lights != LIT:
            log.info("%s; answered, already %s", why, LEVEL_WORDS[self.idle_lights])
            return
        self._idle_since = time.monotonic()
        if not self.set_lights(DIM, why, idle=True):
            log.info("%s; answered, the lights stay %s", why, LEVEL_WORDS[self.lights])

    def stale_idle_notice(self) -> bool:
        """True for an idle notice sent before the idle times were last written (sooner after
        the write than the idle time it carried less STALE_IDLE_MARGIN); it does not dim.

        Its answer turned the idle notice off, so the keyboard is put back as a wake notice's
        answer leaves it (answer_wake) and its idle notice comes when the time runs out.
        Rewriting the times does not do it: a times write wakes only a keyboard that is idle,
        and fn 5 stayed `03 03` until a key. Once per times write: a second stale notice is
        left answered, for the next wake notice's answer to put back.
        """
        if self._times_written_at is None:
            return False
        age = time.monotonic() - self._times_written_at
        if age >= self._dim_written - STALE_IDLE_MARGIN:
            return False
        said = (f"the keyboard's idle notice came {age:.1f} s after its idle times were "
                f"written, sooner than {self._dim_written} s can run out: answered, "
                f"not dimmed")
        if self._stale_put_back:
            log.info("%s; its idle notice left off until its next wake notice", said)
            return True
        self._stale_put_back = True
        try:
            self._lighting.answer_wake()
        except DeviceGone:
            raise
        except HidppFailure as exc:
            if unanswered(exc):
                log.info("%s; its idle notice could not be put back on: %s", said, exc)
                self.keyboard_unanswered(exc)
            elif isinstance(exc, HidppError):
                log.warning("%s; putting its idle notice back on was refused: %s", said, exc)
            else:
                log.warning("%s; its idle notice could not be put back on: %s", said, exc)
            return True
        self._idle_answered = False
        log.info("%s, and its idle notice put back on", said)
        return True

    def keyboard_woke(self) -> None:
        """The first answered copy of a wake notice: a key pressed on a keyboard whose idle
        notice was answered, or the idle times written again, which brings it about 70 ms later.
        Lit, with a full apply when that changes what the board shows; the lock's level stands.
        After a lighting change or new idle times woke the keyboard, the board is painted again
        once the wake is over (_repaint_due)."""
        self._idle_since = None
        written = self._times_written_at
        for_times = written is not None and time.monotonic() - written < EVENT_TRAIN_GAP
        repaint, self._paint_at_wake = self._paint_at_wake and for_times, False
        why = "the keyboard woke for its idle times" if for_times else "a key woke the keyboard"
        changed = self.set_lights(LIT, why, idle=True)
        if repaint:
            log.info("%s; painting again in %.1f s, once the wake is over", why, WAKE_REPAINT_DELAY)
            self._repaint_due = time.monotonic() + WAKE_REPAINT_DELAY
        elif not changed:
            log.info("%s; answered, the lights stay %s", why, LEVEL_WORDS[self.lights])

    def idle_deadline(self) -> float | None:
        """When a keyboard dimmed for idleness goes dark, or None. Read from the config each
        time, so a changed idle_off_after counts from the notice that dimmed it."""
        if self._idle_since is None or self.idle_lights != DIM:
            return None
        return self._idle_since + self.d.config.idle_off_after

    def tend_idle(self) -> None:
        """Once a pass: an apply and idle times owed to a keyboard that answers again, asked on
        the probe's budget at most once per MOUSE_SILENT_RECHECK; the repaint a wake owes
        (_repaint_due), with a drag's latest frame over it, or the apply owed in its place to a
        keyboard marked not answering by then; dark, the claim held, once the idle deadline
        passes."""
        if self.apply_owed and self.lighting is not None and self.keyboard_answers():
            log.info("showing the keyboard the lighting it missed")
            self.apply_profile()
        if self._times_owed and self.keyboard_answers():
            log.info("giving the keyboard the idle times it missed")
            self.write_idle_times()
        if self._repaint_due is not None and self._repaint_due <= time.monotonic():
            self._repaint_due = None
            if self.lights != DARK and self.keyboard_answering():
                log.info("the keyboard's wake is over; painting it again")
                frame = self.last_preview
                self.apply_profile()
                if frame is not None and self.keyboard_answering():
                    # A drag in progress: its latest frame goes back over the repaint. Through
                    # the socket's handler, since nothing here may raise into the loop.
                    self.d.handle_command(frame)
            elif self.lights != DARK and self.dev is not None:
                # Marked since the wake: the paint it lost is owed, as write_due_leds owes one.
                self.apply_owed = True
        due = self.idle_deadline()
        if due is None or due > time.monotonic():
            return
        why = f"the keyboard has been idle {self.d.config.idle_off_after} s since it dimmed"
        if not self.set_lights(DARK, why, idle=True):
            log.info("%s; the lights stay off", why)

    def write_idle_times(self) -> str | None:
        """Write the idle times to the keyboard again: None when it took them, or a note saying
        why not. The write counts as activity (Lighting.set_power_times).

        Times a keyboard attached but marked not answering, or not answering their write, did
        not get are owed (_times_owed) and written once it answers (tend_idle): it keeps its
        handle, and no claim comes when it answers. A keyboard away gets them at its claim. A
        refusal is not owed, since it would be refused again at every pass, nor are times for a
        keyboard attached without lighting, which has nowhere to take them.
        """
        if self.lighting is None and self.dev is not None:
            self._times_owed = False
            return NO_LIGHTING_TIMES_NOTE
        if self.lighting is None or not self.keyboard_answering():
            self._times_owed = self.dev is not None
            return KEYBOARD_AWAY_NOTE
        self._times_owed = False
        try:
            with self.keyboard_writing():
                self._send_idle_times()
        except DeviceGone:
            log.info("the keyboard went away while taking its idle times")
            self.teardown_device()
            return KEYBOARD_AWAY_NOTE
        except HidppFailure as exc:
            log.info("the keyboard did not take its idle times: %s", exc)
            if unanswered(exc):
                # Marked by keyboard_writing; on the receiver the cable look may have let it go.
                self._times_owed = self.dev is not None
                return KEYBOARD_AWAY_NOTE
            return IDLE_TIMES_NOTE
        if self.idle_lights != LIT or self._idle_answered:
            # Taken by a keyboard whose idle notice was answered, below lit or not, the times
            # wake it, and that wake loses a per-key paint sent before it ends: painted again
            # after it (keyboard_woke).
            self._paint_at_wake = True
        return None

    def wake_for_change(self, paint: bool = False) -> bool:
        """Treat a lighting change from the window or the CLI as activity at the keyboard, as
        G HUB does: rewrite the idle times, which restarts the idle count and wakes a keyboard
        that is idle, lit or not, painted again once that wake is over (keyboard_woke).

        Called by each command that paints, after its checks and right before its paint, so a
        refused command writes nothing. At most once per IDLE_RESTART_INTERVAL; nothing under
        never dim, or to a keyboard away or not answering. A write the keyboard did not take
        moves no level and never fails the change.

        A write it took on a keyboard below lit sets the idle level lit, painted here with
        `paint`, for a command whose own write does not repaint the whole board and the M and MR
        LEDs (`preview`, `brightness`). True when the level changed what shows and nothing has
        painted it yet, which the `paint` command answers with a full apply. The lock's level
        stands.
        """
        if self.d.config.never_dim or self.lighting is None or not self.keyboard_answering():
            return False
        now = time.monotonic()
        if now - self._change_times_at < IDLE_RESTART_INTERVAL:
            return False
        self._change_times_at = now
        if self.write_idle_times() is not None:
            return False
        if self.idle_lights == LIT:
            log.debug("a lighting change: the idle times written again, which restarts "
                      "the keyboard's idle count")
            return False
        self._idle_since = None
        why = "a lighting change woke the keyboard"
        if self.set_lights(LIT, why, idle=True, paint=paint):
            return not paint
        log.info("%s; the lights stay %s", why, LEVEL_WORDS[self.lights])
        return False

    def idle_settings(self) -> tuple[int, int, bool]:
        """(idle_dim_after, idle_off_after, never_dim), to tell a change by."""
        return self.d.config.idle_dim_after, self.d.config.idle_off_after, self.d.config.never_dim

    def idle_changed(self, before: tuple[int, int, bool], paint: bool = True) -> str | None:
        """Act on idle settings changed from `before`: new times go to the keyboard, waking one
        that is idle, repainted after the wake (keyboard_woke); never dim turned on lights it,
        painted unless `paint` is False; never dim turned off waits for the next idle notice. A
        synced mouse follows the desktop's idleness at the new times too
        (Session.watch_idle_for_both). The answer is write_idle_times' note, or None."""
        note = None
        if before != self.idle_settings():
            self.d.session.watch_idle_for_both(paint)
        if before[:2] != self.idle_settings()[:2]:
            note = self.write_idle_times()
        if self.d.config.never_dim and not before[2] and self.idle_lights != LIT:
            if paint:
                self._idle_since = None
                self.set_lights(LIT, "never dim turned on", idle=True)
            else:
                self.keyboard_awake()
        return note

    def set_lights(self, level: str, why: str, idle: bool = False,
                   paint: bool = True) -> bool:
        """Set the lock's level, or with `idle` the idle keyboard's, and paint at the darker of
        the two unless the lights are at it already, so the unlocked readings at start do not
        repaint. True when they changed; `paint` False leaves the paint to the caller.

        A keypress wakes both, each through its own signal: Mutter's input watch lights the
        lock's level and the keyboard's wake notice its own."""
        lock_before = self.lock_lights
        if idle:
            self.idle_lights = level
        else:
            self.lock_lights = level
        shown = darker(self.lock_lights, self.idle_lights)
        if shown == self.lights:
            if paint and self.lock_lights != lock_before:
                # The idleness holds the board where it is, but a synced mouse follows the
                # idleness only once the whole desktop is idle (at_level). Said, or a blank over
                # a dark board leaves no line in the journal.
                log.info("%s; keyboard lights stay %s, for idleness", why, LEVEL_WORDS[shown])
                self.d.mice.color.sync_mouse_color(self.shown_color())
            return False
        log.info("%s; keyboard lights %s", why, LEVEL_WORDS[shown])
        self.lights = shown
        if paint:
            self.apply_profile()
        return True

    def lights_note(self, saved: bool = True) -> str | None:
        """What a lighting change says while the lights are down, in the words of what put them
        down, the idle keyboard's only when the lock did not. None while they are lit. A
        preview, which saves nothing, passes `saved` False for the words without "saved; "."""
        if self.lights == LIT:
            return None
        if self.lock_lights != self.lights:
            note = IDLE_NOTE[self.lights]
        else:
            note = LIGHTS_DOWN_NOTE[self.lights]
        return note if saved else note.removeprefix("saved; ")

    def no_lighting_note(self) -> str | None:
        """What `color` and `effect` add for a keyboard attached without lighting, where
        apply_profile has nothing to say; None otherwise."""
        return NO_LIGHTING_NOTE if self.dev is not None and self.lighting is None else None

    def idle_level_known(self) -> bool:
        """Whether the keyboard's idle level is its own: attached with its lighting, and it has
        sent a report or been set up again since it was last marked not answering (an answer to
        a request does not count). Away or off it sends no idle notice."""
        return self.lighting is not None and not self._unheard

    def mouse_follows_the_desktop(self, followed: bool) -> None:
        """Give a synced mouse the desktop's idle level at once when the keyboard's own level
        stops being followed (`followed` is idle_level_known before the mark or teardown) while
        the desktop is idle: nothing else repaints the mouse until the next input or the
        desktop's next level (the owner's decision of 2026-09-29)."""
        if followed and self.d.session.idle_for_both:
            self.d.mice.color.sync_mouse_color(self.shown_color())

    def mouse_idle_level(self) -> str:
        """The idle level a synced mouse shows: lit until the whole desktop is idle
        (Session.idle_level), then the keyboard's own, or while that is not known
        (idle_level_known) the desktop's, which goes by the keyboard's times. The mouse may be
        in use while the keyboard is idle."""
        desktop = self.d.session.idle_level
        if desktop == LIT or not self.idle_level_known():
            return desktop
        return self.idle_lights

    def at_level(self, color: int) -> int:
        """`color` as a synced mouse shows it: at the keyboard's brightness, then black or halved
        at the darker of the lock's level and mouse_idle_level."""
        level = darker(self.lock_lights, self.mouse_idle_level())
        if level == DARK:
            return 0x000000
        color = scaled(color, self.d.config.brightness)
        return scaled(color, 50) if level == DIM else color

    def dark_for_sleep(self) -> str:
        """Dark before a suspend. The answer is what happened, for the journal. A board shown
        dark with nothing owed or refused is asked nothing; otherwise the keyboard is asked
        first, on the probe's budget. With a dark, or a change made to a dark board, still
        owed to a keyboard that was not answering (`apply_owed`), or a dark it refused
        (`last_apply_error`), the dark is written here, not taken for one it shows."""
        self.lock_lights = DARK
        if self.lights == DARK and not self.apply_owed and self.last_apply_error is None:
            # Dark for idleness, which a synced mouse follows only once the desktop is idle
            # (at_level).
            self.d.mice.color.sync_mouse_color(self.shown_color())
            # A dark no board shows: the keyboard left once its board was dark and
            # keyboard_awake kept the lock's level, or it has no lighting.
            if self.dev is None:
                return "keyboard lights not turned off (the keyboard is away)"
            if self.lighting is None:
                return NO_LIGHTING
            return "keyboard lights already off"
        self.keyboard_answers()
        self.lights = DARK
        # The apply darkens a synced mouse, a keyboard without lighting (None) or not.
        failed = self.apply_profile()
        if failed == LIGHTS_DOWN_NOTE[DARK]:
            return "keyboard lights off"
        if failed is None and self.lighting is None:
            return NO_LIGHTING
        # apply_profile answers in a reply's words ("the change is saved"), untrue at a suspend.
        why = ("the keyboard is away" if self.dev is None
               else "the keyboard is not answering" if not self.keyboard_answering()
               else failed)
        return f"keyboard lights not turned off ({why})"

    # -- pumping reports -----------------------------------------------------

    def pump(self) -> None:
        if self.dev is None:
            return
        try:
            self.dev.pump()
            # `dev` is re-read every pass: a key handler can tear the keyboard down mid-batch.
            # A keyboard without diversion is drained too, or its fd stays readable and its
            # power-on report unread.
            while self.dev is not None and (event := self.dev.next_event()) is not None:
                if event.link:
                    # The receiver's, not the keyboard talking.
                    self.take_link_notice(event.data)
                    continue
                self.mark_keyboard_answering()
                self._unheard = False
                if self.keys is not None and self.keys.handles(event):
                    for key_event in self.keys.decode(event):
                        if self.dev is None:
                            # Torn down by the key before: the rest of its report goes too.
                            break
                        self.d.bindings.on_key(key_event.key, key_event.pressed)
                else:
                    self.on_device_event(event)
        except DeviceGone:
            log.info("keyboard disconnected; will reconnect when it returns")
            self.teardown_device()
        except (HidppFailure, OSError, IndexError) as exc:
            # A read failing with an errno other than the gone ones, a handler letting one through,
            # or a report too short for its decoder: reconnect, since raising would end the daemon.
            log.warning("keyboard handling failed (%s); reconnecting", reason(exc))
            self.teardown_device()

    def on_device_event(self, event: Event) -> None:
        if (self.lighting is not None and event.feature_index == self.lighting.brightness_index
                and event.address == 0 and len(event.data) >= 2):
            self.take_brightness_key(int.from_bytes(event.data[:2], "big"))
            return
        if (self.lighting is not None and event.feature_index == self.lighting.rgb_index
                and event.address == IDLE_WAKE_EVENT):
            self.take_notice(event.data)
            return
        if (event.feature_index == self.keyboard_wireless
                and event.address == 0 and len(event.data) > 1 and event.data[1] == 1):
            # 0x1D4B fn 0, byte 1 set: the keyboard lost its diversion, claim and host mode. Byte 2
            # cannot tell a power cycle from the cable going in: the G915 sends `01 01 01` at both.
            # The cable can reach the kernel after the report, so on the receiver it is looked for
            # now and CABLE_WAIT later (look_for_cable_again), the keyboard set up in between.
            reconnected = len(event.data) > 2 and event.data[2] == 1
            # The second report of a pair, or one just after a connect, sets nothing up.
            self.say_set_up_again("reconnected" if reconnected else "asked to be set up again")
            if self.keyboard_product in KEYBOARD_CABLED:
                self.reconfigure_keyboard()
            elif not self.keyboard_moved_to_cable(force=True):
                self.reconfigure_keyboard()
                if self.dev is not None and self._cable_look_at is None:
                    self._cable_look_at = time.monotonic() + CABLE_WAIT
            return
        log.debug(
            "unhandled notification: feature idx 0x%02x addr 0x%02x %s",
            event.feature_index, event.address, event.data[:6].hex(" "),
        )

    def take_brightness_key(self, level: int) -> None:
        """Keep the level the keyboard's own brightness key set (0x8040 event 0, big-endian) over
        the one stored.

        Saved to config.json: the keyboard shows it already, so a file that cannot be saved
        keeps it live with a warning (persist). A level outside 0..100, which config.json would
        refuse, is dropped with a warning. Nothing is written back, and the daemon's own write
        coming back matches the stored level, since each write saves the level the keyboard
        keeps, so this cannot loop.
        """
        if level == self.d.config.brightness:
            return
        if not 0 <= level <= 100:
            # config.json refuses it at the next start, and set_brightness at every apply.
            log.warning("the keyboard reported brightness %d, outside 0..100; not kept", level)
            return
        log.info("the keyboard's brightness key set it to %d; keeping that", level)
        self.d.config.brightness = level
        self.d.persist()
        self.d.mice.color.sync_mouse_color(self.shown_color())

    # -- teardown ------------------------------------------------------------

    def teardown_device(self, lights_off: bool = False, hand_back: bool = True,
                        stopping: bool = False) -> str:
        """Hand everything back. Safe to call when half-constructed or already gone.

        `lights_off` turns every cluster off and keeps the claim and HOST mode instead of
        handing the panel back: a release or ONBOARD mode makes the firmware repaint its own
        effect.

        The shift layer goes, since a keyboard that disappears with MR down never sends the
        release; so do a macro a G key started and a key a G key holds, whose releases still go
        to the virtual devices. A macro a mouse button started keeps running, or a keyboard node
        that cannot be opened would cancel it every RECONNECT_DELAY.

        `hand_back` False writes nothing, for a route the keyboard has left and a keyboard that
        stopped answering during its connect or setup, which stays in HOST mode with its keys
        diverted until the next connect. Nothing is written to a keyboard marked not answering
        either, and a step that gets no answer skips the rest, each of which would wait three
        seconds. The answer says what was and was not done, for shutdown's log line.

        `stopping` is the daemon's own stop, which gives a synced mouse no idle level: a plain
        stop releases the mouse next, and a power-off darkens it.
        """
        self.d.bindings.keyboard_gone()
        followed = self.idle_level_known()
        silent = self.keyboard_silent_until > time.monotonic()
        self.keyboard_silent_until = 0.0
        self._unheard = self._silence_said = False
        self._cable_look_at = None
        # Dropped: a new claim writes the times and applies again.
        self.leds_due, self.apply_owed, self._times_owed = None, False, False
        if self.dev is not None:
            with contextlib.suppress(KeyError, ValueError):
                self.d.selector.unregister(self.dev.fileno)
        steps = self.teardown_steps(lights_off)
        done, missed = run_steps(steps if hand_back and not silent else ())
        said = self.teardown_said(lights_off, hand_back, silent, done, missed)
        if self.dev is not None:
            self.dev.close()
        self.dev = self.lighting = self.keys = None
        self.keyboard_wireless = None
        # Its idle level goes with it: claimed again, it is awake.
        self.keyboard_awake()
        if not stopping:
            self.mouse_follows_the_desktop(followed)
        return said

    def teardown_steps(self, lights_off: bool) -> tuple[Step, ...]:
        """What teardown_device hands back, in order, for a keyboard that has each piece: the M
        and MR LEDs off and the key diversion, then the LED claim and ONBOARD mode, or with
        `lights_off` every cluster off in their place."""
        give_back = (
            (self.lighting, lambda: self._lighting.release(), "LED claim"),
            (self.dev is not None and "onboard" in self.keyboard_able,
             lambda: self.set_keyboard_mode(MODE_ONBOARD), "onboard mode"),
        )
        go_dark = ((self.lighting, lambda: self.paint.darken(self._lighting), "lights out"),)
        return (
            (self.keys, lambda: self._keys.all_leds_off(), "M/MR LEDs"),
            (self.keys, lambda: self._keys.restore(), "key diversion"),
        ) + (go_dark if lights_off else give_back)

    def teardown_said(self, lights_off: bool, hand_back: bool, silent: bool,
                      done: list[str], missed: list[str]) -> str:
        """What teardown_device did and did not do, for shutdown's log line; asked while the
        handle is still held."""
        if self.dev is None:
            said = "no keyboard"
        elif not hand_back:
            said = "keyboard left on the route it moved from"
        elif silent:
            said = "keyboard not answering, nothing handed back"
        else:
            said = (f"keyboard {'darkened' if lights_off else 'handed back'}: "
                    f"{', '.join(done) or 'nothing'}")
            if missed:
                said += f"; not {', '.join(missed)}"
        return said
