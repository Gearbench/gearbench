"""The keyboards' controllers: `Keyboards`, every keyboard the daemon drives at once, a
`Keyboard` each, with the nodes of keyboards that did not answer kept in its part waiting.py,
and `Keyboard`, one keyboard's state (its device, model, light levels and
what an apply left owed), connecting it (`connect`, which opens no node held, and `set_up`,
which sets the keyboard up on it), whether it answers, painting the profile in force
(`apply_profile`), the M and MR LEDs, what a synced mouse shows, the keyboard's reports, FN's
among them (`take_fn`), and the teardown. The parts a `Keyboard` builds do the rest: the writes
that put the lighting on the board (paint.py), the keyboard left on its own profile
(onboard_hold.py), the command handlers (commands.py), the idle notices and the light level
(idle.py), the claim and the setup again on its link (link.py), what status and the battery
notices read off it (readings.py), its low-battery notices and the battery reports it sends
(battery.py), its custom assignments and Game Mode (key_control.py), and what its setup reads
off it that its firmware fixes, kept for the next connect of the same keyboard (known.py)."""

from __future__ import annotations

import contextlib
import logging
import selectors
import time
from collections.abc import Callable, Iterable, Iterator
from functools import partial
from typing import TYPE_CHECKING, cast

from ...config import PROFILE_IDS
from ...hidpp import NO_RULE, DeviceGone, Event, Hidpp, HidppError, HidppFailure
from ...keyboard import (
    CABLED_IDS,
    attached_keyboards,
    key_name,
    model_key,
    name as keyboard_name,
)
from ...keyboard_zones import KeyboardLights, lighting_capabilities, power_modes
from ...keycontrol import TriggeredKeys
from ...keys import DivertedKeys
from ...lighting import BRIGHTNESS_EVENT, DARK, DIM, IDLE_WAKE_EVENT, LIT, Lighting, LightingError
from ...modifiers import Modifiers
from ...onboard import F_ONBOARD, MODE_HOST, MODE_ONBOARD, read_mode, running_sector, write_mode
from ...settings_files import ConfigError
from ...wording import escaped, reason
from ..shared import (
    F_WIRELESS,
    MOUSE_PROBE,
    MOUSE_SILENT_RECHECK,
    ProbeBudget,
    darker,
    failure_reason,
    hidpp_refusal,
    keyboard_model_named,
    look_due,
    open_keyboard,
    said_once,
    unanswered,
    unreachable,
)
from .battery import KeyboardBattery
from .commands import KeyboardCommands, connected_words
from .idle import Idle
from .key_control import KeyControl
from .known import Known
from .link import Link
from .notes import (
    IDLE_NOTE,
    KEYBOARD_AWAY,
    KEYBOARD_REFUSED,
    KEYBOARD_SILENT,
    KEYBOARD_UNREACHED,
    LED_SETTLE,
    LIGHTS_DOWN_NOTE,
    NO_ANSWER_ASKS,
    NO_LIGHTING,
    NOT_SET_UP_YET,
    RECONNECT_DELAY,
    named_words,
    unanswered_note,
)
from .onboard_hold import OnboardHold
from .paint import Paint, dimmed_color, keepable
from .readings import Readings
from .waiting import Found, Waiting

if TYPE_CHECKING:
    from ...config import Lighting as ProfileLighting
    from .. import Daemon

log = logging.getLogger("gearbench")

#: What `color` and `effect` say for a keyboard attached without that lighting: saved, and
#: shown on a synced mouse alone.
NO_LIGHTING_NOTE = f"saved; {NO_LIGHTING}, so the keyboard does not show it"

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
        except (HidppFailure, IndexError) as exc:
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
    """The keyboard and what the daemon holds for it."""

    # -- the controller ------------------------------------------------------

    def __init__(self, daemon: Daemon) -> None:
        self.d = daemon
        self.dev: Hidpp | None = None
        self.lighting: KeyboardLights | None = None
        self.keys: DivertedKeys | None = None
        #: The keyboard's FN reports, on a model with FN keys to light (KeyboardModel.fn_keys),
        #: or None.
        self.modifiers: Modifiers | None = None
        #: The product id of the keyboard this controller drove last, or last tried to set up,
        #: kept while it is away (`model`); it differs per route.
        self.keyboard_product: int | None = None
        #: What the attached keyboard can do, for the window to render from.
        self.keyboard_able: list[str] = []
        #: The keyboard's 0x1D4B index, or None. On the receiver the node stays open while the
        #: keyboard is off, so its power-on report here says it came back without its diversion,
        #: claim and host mode, as the receiver's link-up notice does with keyboard_link_notices on.
        self.keyboard_wireless: int | None = None
        #: The last connect failure logged on each node, by its path, so a repeat is said once
        #: (said_once): a keyboard passed over and one standing in for it each keep their own.
        self._connect_said: dict[str, str] = {}
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
        #: The level every repaint goes through, LIT, DIM or DARK: the darker of the lock's
        #: level (Keyboards.lock_lights) and the one below (Idle.set_lights).
        self.lights = LIT
        #: The level the idle notices ask for (LIT under never_dim).
        self.idle_lights = LIT
        #: The latest `preview` since the last apply or paint, sent again after a wake repaint so
        #: that the repaint does not replace a drag in progress.
        self.last_preview: dict | None = None
        #: A saved lighting change the keyboard did not get, marked not answering or not
        #: answering its write: applied once it answers (Idle.tend_idle).
        self.apply_owed = False
        #: The steps the last teardown_device carried out, by their names (teardown_steps): one
        #: cut short, or a keyboard marked not answering, leaves "onboard mode" out.
        self.handed_back: list[str] = []
        #: The parts, each built with this controller, which it keeps as `keyboard`, and the
        #: daemon where it reads it. The command handlers are `handlers`: `commands` is the
        #: table below.
        self.paint = Paint(daemon, self)
        self.hold = OnboardHold(daemon, self)
        self.link = Link(daemon, self)
        self.idle = Idle(daemon, self)
        self.readings = Readings(self)
        self.battery = KeyboardBattery(daemon, self)
        self.key_control = KeyControl(daemon, self)
        self.known = Known(self)
        self.handlers = KeyboardCommands(daemon, self)

    def commands(self) -> dict[str, Callable[[dict], dict]]:
        return self.handlers.commands()

    def model(self) -> str | None:
        """The model of the keyboard this controller drives, or drove last, or last tried to
        set up, by its key (keyboard.model_key); None before any."""
        return None if self.keyboard_product is None else model_key(self.keyboard_product)

    def leads(self) -> bool:
        """Whether this is the leader's controller (Daemon.keyboard), whose lighting the mice
        show."""
        return self is self.d.keyboard

    def sync_devices(self, shown: int, repeat: bool = True) -> None:
        """The color a synced mouse and a synced headset's lights take for `shown`, the
        board's, to them (Daemon.sync_to_leader) where this controller leads: they follow the
        leader alone, and a leader once it is set up: a connect's first paint, before set_up
        registers its node, leaves them be, and set_up gives them the color at its end (the
        owner's decision of 2026-10-25, "Sync after setup")."""
        if self.leads() and self.node_registered():
            self.d.sync_to_leader(shown, repeat=repeat)

    def node_registered(self) -> bool:
        """Whether the loop reads this keyboard's node, or none is open: not during a connect's
        first paint, which set_up registers the node after."""
        return self.dev is None or self.dev.fileno in self.d.selector.get_map()

    def deadlines(self) -> list[float]:
        """When the keyboard next needs the loop."""
        due = (*self.link.deadlines(), self.leds_due, *self.idle.deadlines())
        return [at for at in due if at is not None]

    # `dev`, `lighting` and `keys` for code reached only while the one it uses is there, after a
    # check made in another function, which the type checker does not follow. Nothing checks
    # these casts.

    @property
    def _dev(self) -> Hidpp:
        return cast("Hidpp", self.dev)

    @property
    def _lighting(self) -> KeyboardLights:
        return cast("KeyboardLights", self.lighting)

    @property
    def _keys(self) -> DivertedKeys:
        return cast("DivertedKeys", self.keys)

    # -- connecting ----------------------------------------------------------

    def connect(self, found: Found) -> bool | None:
        """Set the keyboard on node `found`, which Keyboards found and no controller holds, up
        (set_up), or leave nothing behind. A node held (OnboardHold.held_onboard) is not
        opened. Whether it was set up; None when no keyboard answered the probe (set_up)."""
        if self.hold.held_onboard(found) is not None:
            return False
        return self.set_up(found)

    def set_up(self, found: Found) -> bool | None:
        """Open the keyboard on the node `found` names and take it over, or leave nothing
        behind.

        Every device call up to the registration is inside one guard: a keyboard unplugged or
        answering short part way through leaves half-built handles, and the loop retries only
        while `dev` is None.

        Whether it was set up, None when no keyboard answers its probe, as one switched off
        behind its receiver, for Keyboards.connect_on to wait on the node (Waiting.wait_for); a
        cable's is tried again every RECONNECT_DELAY. A model whose G keys type its own keys is
        left on its own profile while Gearbench cannot type them, the virtual keyboard missing
        or the session not active (OnboardHold.keep_onboard, hold_for_session); unless it was
        found in host mode and handed back, its node is not opened again until Gearbench can
        (held_onboard).
        """
        path, product, _derived = found
        self.keyboard_product = product
        started = time.monotonic()
        try:
            # The index is probed, not assumed: on the receiver's node 0xFF is the receiver
            # itself. On the probe budget, since the loop waits while this runs.
            opened = open_keyboard(found)
            if opened is None:
                # Once at info: this repeats while the keyboard is off behind its receiver.
                said = f"no keyboard answered on {path}; {NO_ANSWER_ASKS[product in CABLED_IDS]}"
                said_once(self._connect_said, str(path), said, logging.INFO)
                self.d.note_slow(started, f"keyboard connect on {path}",
                               "no keyboard answering on it", repeats="keyboard")
                return None
            self.d.per_keyboard.found(product)
            self.dev, index = opened
            kept = self.known.take_parts(product)
            if self.hold.keep_onboard(found):
                return False
            if self.set_keyboard_mode(MODE_HOST) == MODE_HOST and not self._connected_once:
                # set_keyboard_mode says nothing when the mode already matches; this line is
                # the only trace of a stop that did not hand the keyboard back.
                log.info("%s was already in host mode at start: whatever drove it last "
                         "did not hand it back", keyboard_name(product))
            self._connected_once = True
            self.link.take_over()
            if self.lighting is not None:
                self.keyboard_able.extend(lighting_capabilities(self.lighting))
            self.paint.read_brightness()
            self.apply_profile()
            if not self.keyboard_answering():
                # apply_profile tears down rather than raising when the keyboard goes away, and
                # marks one whose paint goes unanswered (not_shown): each read after it would wait
                # out its timeout, so it is handed nothing back, as in connect_failed.
                why = "went away" if self.dev is None else "stopped answering"
                if self.dev is not None:
                    self.teardown_device(hand_back=False)
                self.d.note_slow(started, f"keyboard connect on {path}",
                               f"it {why} during the first paint", repeats="keyboard")
                return False
            self.known.finish(product, kept)
            self.idle.expect_claim_wake()
            self.d.selector.register(self.dev.fileno, selectors.EVENT_READ, ("hid", self))
        except (OSError, HidppFailure, IndexError, RuntimeError) as exc:
            self.connect_failed(str(path), started, exc)
            return False
        # The node is registered now: the first paint's sync_devices wrote nothing.
        self.sync_devices(self.shown_color())
        self._connect_said.clear()
        # A power-on report queued during the connect asks for the setup it just had.
        self.link.keyboard_reapplied_at = time.monotonic()
        self.d.forget_slow("keyboard")
        self.d.note_slow(started, f"keyboard connect on {path}")
        log.info("%s", connected_words(product, path, index, self.keyboard_able))
        self.link.turn_on_link_notices()
        return True

    def connect_failed(self, path: str, started: float, exc: Exception) -> None:
        """What set_up does with a failure inside its guard, on the node at `path`: say it,
        take down what was built and note a slow attempt.

        Said once per distinct failure on each node: a node the udev rule never granted fails
        the same way every RECONNECT_DELAY. A keyboard gone quiet after its probe is off or out
        of range, not a fault, and is handed nothing back: each step would wait out its own
        timeout.
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
        said_once(self._connect_said, path, said, level)
        self.teardown_device(hand_back=not quiet, connected=False)
        self.d.note_slow(started, f"keyboard connect on {path}", reason(exc), repeats="keyboard")

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

    # -- answering and silence -----------------------------------------------

    def keyboard_answering(self) -> bool:
        return self.dev is not None and self.keyboard_silent_until <= time.monotonic()

    def keyboard_answers(self, budget: ProbeBudget = MOUSE_PROBE) -> bool:
        """Whether the keyboard answers one cheap request on `budget`, the probe's unless
        given. One that does not is marked not answering; a write or read that failed is no
        answer, and marks it too; an error reply is an answer. For a poll and a suspend, which
        must not wait out the full timeout on a keyboard that is off."""
        if not self.keyboard_answering():
            return False
        try:
            self._dev.lookup(F_WIRELESS, **budget)
        except DeviceGone:
            log.info("the %s went away", self.model_name())
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
                "the %s is not answering (%s); left unasked for %d s, or until it sends "
                "something", self.model_name(), why, MOUSE_SILENT_RECHECK)
        self._silence_said = True
        self.keyboard_silent_until = time.monotonic() + MOUSE_SILENT_RECHECK
        followed = self.idle_level_known()
        self._unheard = True
        self.readings.forget_live()
        self.repaint_synced(followed)

    def mark_keyboard_answering(self, quiet: bool = False) -> None:
        """Clear the mark: the keyboard sent a report or answered a request. The first answer
        after the not-answering line is logged, unless `quiet`: the caller says it."""
        if self._silence_said and not quiet:
            log.info("the %s is answering again", self.model_name())
        self._silence_said, self.keyboard_silent_until = False, 0.0

    def heard(self, quiet: bool = False) -> None:
        """The keyboard sent a report or was set up again: the mark cleared, as an answer clears
        it (mark_keyboard_answering, which `quiet` goes to), and its idle notices coming again
        (idle_level_known). Heard after the mark, it lights the others (Idle.back_owed)."""
        self.mark_keyboard_answering(quiet)
        if self._unheard:
            self.idle.back_owed = True
        self._unheard = False

    def keyboard_unanswered(self, exc: BaseException) -> None:
        """A keyboard request went unanswered (`unanswered`): mark it not answering and look for its
        cable at once, through sysfs. A cable found drops the receiver route, and the caller finds
        `dev` gone."""
        self.mark_keyboard_silent("its receiver says it cannot reach it"
                                  if unreachable(exc) else "no reply")
        self.link.keyboard_moved_to_cable(force=True)

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

    # -- the lighting and its apply path -------------------------------------

    def shown_color(self) -> int:
        """The color the board shows around any per-key overrides: the layer's color
        (PerKeyboard.active_lighting), or black under disabled, which is in STATIC_EFFECTS so that
        overrides show over it and whose color must not fill the board."""
        effect_name, color, _keys, _params = self.d.per_keyboard.active_lighting(self)
        return 0x000000 if effect_name == "disabled" else color

    def change_unreached(self) -> str | None:
        """Why a lighting change cannot be written now: KEYBOARD_AWAY for a keyboard away, or
        KEYBOARD_SILENT for one with lighting marked not answering, which is owed the apply
        (Idle.tend_idle). None otherwise, for a keyboard without lighting too, which each caller
        refuses or skips on its own."""
        if self.dev is None or self.lighting is None or self.keyboard_answering():
            return KEYBOARD_AWAY.format(model=self.model_name()) if self.dev is None else None
        self.apply_owed = True
        return KEYBOARD_SILENT.format(model=self.model_name())

    def model_name(self) -> str:
        """The model's name for a note, "keyboard" before one is known (keyboard.name)."""
        return keyboard_name(self.keyboard_product)

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
        effect_name, color, per_key, extra_params = self.d.per_keyboard.active_lighting(self)
        shown = self.shown_color()
        self.leds_due = self.last_preview = None
        # Guarded on lighting, not keys: without diversion only the LEDs are skipped.
        failed = self.change_unreached()
        detail = None
        if self.lighting is None or failed is not None:
            self.sync_devices(shown)
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
                self.paint.darken(self.lighting)
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
        self.sync_devices(shown)
        self.last_apply_error, self.last_apply_detail = failed, detail
        return failed or self.lights_note()

    def not_shown(self, exc: HidppFailure | LightingError, what: str) -> tuple[str, str | None]:
        """Why the keyboard does not show `what`, saved already, after its write failed, and the
        device's words: gone (torn down), not answering (keyboard_unanswered, owed unless its
        cable look dropped the receiver route), not reached (a link error, marked and owed) or
        refused."""
        name = self.model_name()
        if isinstance(exc, DeviceGone):
            log.info("the %s went away while applying %s: %s", name, what, failure_reason(exc))
            # Its node unregistered: gone during its connect's first paint, which set_up
            # registers the node after, so no other keyboard showed it.
            self.teardown_device(connected=self.node_registered())
            return KEYBOARD_AWAY.format(model=name), None
        if unanswered(exc):
            # Off, out of range or gone to its cable.
            log.info("%s not applied on the %s: %s", what, name, exc)
            self.keyboard_unanswered(exc)
            # Not owed to a receiver route the cable look dropped: the cable's connect applies it.
            self.apply_owed = self.dev is not None
            return unanswered_note(exc, self.dev is None, name), str(exc)
        if isinstance(exc, HidppFailure) and not isinstance(exc, HidppError):
            # The kernel failed the write or read outside hidpp.GONE_ERRNOS, such as a USB
            # link error: no answer, as keyboard_answers takes it, so marked and owed.
            log.info("%s not applied on the %s: %s", what, name, exc)
            self.mark_keyboard_silent(str(exc))
            self.apply_owed = True
            return KEYBOARD_UNREACHED.format(model=name, why=failure_reason(exc)), str(exc)
        log.warning("could not apply %s on the %s: %s", what, name, exc)
        return KEYBOARD_REFUSED.format(model=name, why=exc), str(exc)

    def write_led(self, action: Callable[[DivertedKeys], None], what: str,
                  owed: bool = False) -> None:
        """One indicator LED write, `action` on the diverted keys, that cannot take the daemon
        down with it: the keyboard can vanish before the write. `owed` is an apply's own LED
        write (write_due_leds), where a write the kernel fails outside hidpp.GONE_ERRNOS is no
        answer and marks the keyboard, as on the apply's lighting write (not_shown)."""
        keys = self.keys
        if keys is None:
            return
        try:
            action(keys)
        except DeviceGone as exc:
            log.info("the %s went away writing the %s LED: %s", self.model_name(), what,
                     failure_reason(exc))
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
                    self.write_led(lambda keys: keys.set_mr_led(self.d.bindings.shifted_on(self)),
                                   "MR", owed=True)
            except OSError as exc:
                log.warning("writing the %s's M and MR LEDs failed (%s); reconnecting",
                            self.model_name(), reason(exc))
                self.teardown_device()
        if self.dev is not None and not self.keyboard_answering():
            # A keyboard gone gets the apply at its connect instead.
            self.apply_owed = True

    def switch_profile(self, index: int, wake: bool = False) -> str | None:
        """Make an M profile active, or just relight its LED if it already is.

        The M profile is every keyboard's: this one is painted first, then each other driven
        (Keyboards.apply_each). The firmware clears the LED on every lighting write. Not relit
        while the board is dark: the apply that lights it writes the LEDs. Nor on a keyboard
        marked not answering, whose timeouts would hold the loop. With `wake`, for the
        `profile` command, a switch counts as activity at the keyboard
        (Idle.wake_for_change). The answer is this keyboard's apply_profile's.
        """
        if not self.d.config.switches_to(index):
            if index in PROFILE_IDS and self.lights != DARK and self.keyboard_answering():
                self.write_led(lambda keys: keys.set_m_led(index), "M")
            return None
        self.d.config.active = index
        if wake:
            # After the switch: a keyboard it wakes beside this one is painted at once
            # (Idle.wake_with_another), with the profile switched to.
            self.idle.wake_for_change()
        log.info("profile %d (%s)", index, escaped(self.d.config.profile.name))
        failed = self.d.keyboards.apply_each(self)
        self.d.bindings.stop_orphaned_macro()
        self.d.persist()
        return failed

    def lights_note(self, saved: bool = True) -> str | None:
        """What a lighting change says while the lights are down, in the words of what put them
        down, the idle keyboard's only when the lock did not. None while they are lit. What
        saves nothing passes `saved` False for the words without "saved; "."""
        if self.lights == LIT:
            return None
        down = IDLE_NOTE if self.d.keyboards.lock_lights != self.lights else LIGHTS_DOWN_NOTE
        note = down[self.lights]
        return note if saved else note.removeprefix("saved; ")

    def no_lighting_note(self) -> str | None:
        """What `color` and `effect` add for a keyboard attached without lighting, where
        apply_profile has nothing to say; None otherwise."""
        if self.dev is None or self.lighting is not None:
            return None
        return NO_LIGHTING_NOTE.format(self.model_name())

    # -- what a synced mouse shows -------------------------------------------

    def idle_level_known(self) -> bool:
        """Whether the keyboard's idle level is its own: attached with lighting that has idle
        notices (0x8071's, keyboard_zones.power_modes), and it has sent a report or been set up
        again since it was last marked not answering (an answer to a request does not count).
        Away or off it sends no idle notice."""
        return self.lighting is not None and power_modes(self.lighting) and not self._unheard

    def repaint_synced(self, followed: bool, shown: str | None = None) -> None:
        """Repaint a synced mouse and a synced headset's lights at once where what they show
        changes, since nothing else repaints them until the next input or the desktop's next
        level: the desktop's idle level
        when the leader's own level stops being followed (`followed` is idle_level_known before
        the mark or teardown) while the desktop is idle (the owner's decision of 2026-09-29),
        and at a teardown with `hand_back` of the keyboard the mice followed, whose model's
        lighting they showed is `shown`, another model's lighting where they show it now
        (PerKeyboard.shown_model). Only for the leader's controller, or the one that led until
        the teardown (`shown`); nothing during a connect's first paint (node_registered), as in
        sync_devices."""
        leading = self.d.keyboard
        moved = shown is not None and self.d.per_keyboard.shown_model(leading) != shown
        if self.node_registered() and (
                (followed and self.leads() and self.d.session.idle_for_both) or moved):
            self.d.sync_to_leader()

    def mouse_level(self) -> str:
        """The level a synced mouse shows, and a synced headset's lights: the darker of the
        lock's level and its idle level,
        which is lit until the whole desktop is idle (Session.idle_level), then the keyboard's
        own, or while that is not known (idle_level_known) the desktop's, which goes by the
        keyboard's times. The mouse may be in use while the keyboard is idle."""
        desktop = self.d.session.idle_level
        idle = desktop if desktop == LIT or not self.idle_level_known() else self.idle_lights
        return darker(self.d.keyboards.lock_lights, idle)

    def at_level(self, color: int) -> int:
        """`color` as a synced mouse and a synced headset's lights show it: at the keyboard's
        brightness (at_brightness), then black or halved at mouse_level."""
        level = self.mouse_level()
        if level == DARK:
            return 0x000000
        color = self.d.per_keyboard.at_brightness(color)
        return dimmed_color(color) if level == DIM else color

    # -- pumping reports -----------------------------------------------------

    def pump(self) -> None:
        """The keyboard's reports, for a keyboard driven, and any reply no request took
        (Heard.reply), which clears the not-answering mark. Nothing for one torn down."""
        if self.dev is None:
            return
        try:
            if self.dev.pump().reply:
                # Such as the late answer to a request that gave up while the keyboard woke
                # behind its receiver: an answer, so it clears the mark as one in time does, and
                # leaves the idle level unknown (idle_level_known). Before the reports, so a
                # link-down notice among them marks the keyboard again.
                self.mark_keyboard_answering()
            # `dev` is re-read every pass: a key handler can tear the keyboard down mid-batch.
            # A keyboard without diversion is drained too, or its fd stays readable and its
            # power-on report unread.
            while self.dev is not None and (event := self.dev.next_event()) is not None:
                if event.link:
                    # The receiver's, not the keyboard talking.
                    self.link.take_link_notice(event.link_down)
                    continue
                self.heard()
                source = self.key_source(event)
                if source is not None:
                    for key_event in source.decode(event):
                        if self.dev is None:
                            # Torn down by the key before: the rest of its report goes too.
                            break
                        self.d.bindings.on_key(key_event.key, key_event.pressed, self)
                else:
                    self.on_device_event(event)
        except DeviceGone:
            log.info("%s disconnected; will reconnect when it returns", self.model_name())
            self.teardown_device()
        except (HidppFailure, OSError, IndexError) as exc:
            # A read failing with an errno other than the gone ones, a handler letting one through,
            # or a report too short for its decoder: reconnect, since raising would end the daemon.
            log.warning("%s handling failed (%s); reconnecting", self.model_name(), reason(exc))
            self.teardown_device()

    def key_source(self, event: Event) -> DivertedKeys | TriggeredKeys | None:
        """What reports keys in `event`: the G keys' diversion, the keys custom assignments set
        to report, or neither."""
        return next((keys for keys in (self.keys, self.key_control.keys)
                     if keys is not None and keys.handles(event)), None)

    def on_device_event(self, event: Event) -> None:
        if self.modifiers is not None and self.modifiers.handles(event):
            held = self.modifiers.take(event)
            if held is not None:
                self.take_fn(held)
            return
        if (self.lighting is not None and event.feature_index == self.lighting.brightness_index
                and event.address == BRIGHTNESS_EVENT):
            self.take_brightness_key(int.from_bytes(event.data[:2], "big"))
            return
        if (self.lighting is not None and power_modes(self.lighting)
                and event.feature_index == self.lighting.rgb_index
                and event.address == IDLE_WAKE_EVENT):
            self.idle.take_notice(event.data)
            return
        if (event.feature_index == self.keyboard_wireless
                and event.address == 0 and event.data[1] == 1):
            # 0x1D4B fn 0, byte 1 set: the keyboard lost its diversion, claim and host mode.
            self.link.take_power_on_report(event.data)
            return
        if self.key_control.take_event(event) or self.battery.take_report(event):
            return
        log.debug("unhandled notification: feature idx 0x%02x addr 0x%02x %s",
                  event.feature_index, event.address, event.data[:6].hex(" "))

    def take_brightness_key(self, level: int) -> None:
        """Keep the level the keyboard's own brightness key set (0x8040 event 0, big-endian) over
        the one stored, in config.json (PerKeyboard.keep_brightness), unless keepable refuses it.

        Nothing is written back to this keyboard, and the daemon's own write coming back matches
        the stored level, since each write saves the level the keyboard keeps, so this cannot
        loop. The keyboard holds it whatever is kept (Paint.held_brightness).
        """
        self.paint.held_brightness = level
        if level == self.d.per_keyboard.brightness(self) or not keepable(level, self.model_name()):
            return
        log.info("the %s's brightness key set it to %d; keeping that", self.model_name(), level)
        # The leader's and each that follows it show one brightness, which a key on any of
        # them sets: the owner's decision of 2026-10-21, "The leader's".
        source = self.d.per_keyboard.keep_brightness(level, self)
        for other in (source, *self.d.keyboards.sharing(source)):
            if other is not self:
                other.apply_profile()
        self.d.sync_to_leader()

    # -- FN ------------------------------------------------------------------

    def take_fn(self, held: bool) -> None:
        """FN held or let go (Modifiers.take): the board's frame for it (Paint.write_fn), at
        the level the lights are at, or nothing while they are off. Whether it is held stays
        with the keyboard (Modifiers.fn_held), so an apply meanwhile, a wake's or a profile
        switch's, keeps the FN keys white (Paint.fn_zones). A failed write is said as an
        apply's (not_shown)."""
        log.debug("FN %s", "held" if held else "let go")
        if self.lights == DARK:
            return
        effect_name, color, per_key, extra_params = self.d.per_keyboard.active_lighting(self)
        try:
            # FN reports come only with per-key lighting (set_up), which is 0x8071's.
            self.paint.write_fn(cast("Lighting", self.lighting), effect_name, color, per_key,
                                extra_params, self.shown_color(), self.lights == DIM)
        except (HidppFailure, LightingError) as exc:
            self.not_shown(exc, "the FN keys' colors")

    # -- teardown ------------------------------------------------------------

    def teardown_device(self, lights_off: bool = False, hand_back: bool = True,
                        stopping: bool = False, connected: bool = True) -> str:
        """Hand everything back. Safe to call when half-constructed or already gone.

        `lights_off` turns every cluster off and keeps the claim and HOST mode instead of
        handing the panel back: a release or ONBOARD mode makes the firmware repaint its own
        effect. A keyboard whose G keys type its own keys gets ONBOARD mode back all the same,
        after the dark (teardown_steps).

        The shift layer goes, since a keyboard that disappears with MR down never sends the
        release; so do a macro a G key started and a key a G key holds, whose releases still go
        to the virtual devices. A macro a mouse button started keeps running, or a keyboard node
        that cannot be opened would cancel it every RECONNECT_DELAY.

        `hand_back` False writes nothing, for a route the keyboard has left, a keyboard held on
        its own profile (OnboardHold.keep_onboard) and one that stopped answering during its
        connect or setup, which stays in HOST mode with its keys diverted until the next
        connect. Nothing is written to a keyboard marked not answering either, and a step that
        gets no answer skips the rest, each of which would wait three seconds. The answer says
        what was and was not done, for the journal, and `handed_back` keeps the names of the
        steps done.

        `stopping` is the daemon's own stop, which gives a synced mouse no idle level: a plain
        stop releases the mouse next, and a power-off darkens it. Short of it, with `hand_back`,
        each other keyboard driven that shows another lighting once this one is gone, as one
        that followed it, is painted again (Keyboards.repaint_changed). `connected` False is a
        connect cut short: the others never showed it, nor did a synced mouse or a synced
        headset's lights (sync_devices), though it counted as driven from its open, so no other
        keyboard is painted again.
        """
        self.d.bindings.keyboard_gone(source=self)
        followed = self.idle_level_known()
        # The model whose lighting the mice showed, while this controller led them, and what
        # each other keyboard shows: neither for a teardown with hand_back False, whose
        # keyboard is still plugged in.
        shown = self.d.per_keyboard.shown_model(self) if self.leads() and hand_back else None
        others = self.d.keyboards.views(self) if hand_back and connected and not stopping else {}
        silent = self.keyboard_silent_until > time.monotonic()
        self.keyboard_silent_until = 0.0
        self._unheard = self._silence_said = False
        self.link.cable_look_at = None
        # Dropped: a new claim writes the times and applies again.
        self.leds_due, self.apply_owed, self.idle.times_owed = None, False, False
        if self.dev is not None:
            with contextlib.suppress(KeyError, ValueError):
                self.d.selector.unregister(self.dev.fileno)
        steps = self.teardown_steps(lights_off)
        self.handed_back, missed = run_steps(steps if hand_back and not silent else ())
        said = self.teardown_said(lights_off, hand_back, silent, self.handed_back, missed)
        if self.dev is not None:
            self.dev.close()
        self.dev = self.lighting = self.keys = self.modifiers = self.keyboard_wireless = None
        self.key_control.forget()
        # Its idle level goes with it: claimed again, it is awake.
        self.idle.keyboard_awake()
        if not stopping:
            self.repaint_synced(followed, shown)
            self.d.keyboards.repaint_changed(others)
        return said

    def teardown_steps(self, lights_off: bool) -> tuple[Step, ...]:
        """What teardown_device hands back, in order, for a keyboard that has each piece: the M
        and MR LEDs off and the key diversion, an empty BASE layer where custom assignments
        set keys to report (KeyControl.owes_restore), then a G515's own on-board mode where it
        was left in host mode (KeyControl.give_back_mode), the LED claim and ONBOARD mode, or
        with `lights_off` every cluster off in their place, then ONBOARD mode on a keyboard
        whose G keys type its own keys while unbound (`typed_keys`)."""
        onboard = (self.dev is not None and "onboard" in self.keyboard_able,
                   lambda: self.set_keyboard_mode(MODE_ONBOARD), "onboard mode")
        # Kept in HOST mode, its G keys would type nothing, bound or not, until it is switched
        # off and on (KeyboardModel.g_key_defaults). Last: ONBOARD mode may repaint its own
        # effect, as the G915's does.
        go_dark = ((self.lighting, self.lights_out, "lights out"),
                   *((onboard,) if "typed_keys" in self.keyboard_able else ()))
        # A G515's on-board mode before the release: released while in host mode, the G515 TKL
        # kept showing the last paint, though the on-board mode followed (2026-10-24).
        give_back = ((self.key_control.host_mode, self.key_control.give_back_mode, "onboard mode"),
                     (self.lighting, lambda: self._lighting.release(), "LED claim"), onboard)
        keys = ((self.keys, lambda: self._keys.all_leds_off(), "M/MR LEDs"),
                (self.keys, lambda: self._keys.restore(), "key diversion"),
                (self.key_control.owes_restore(), self.key_control.restore, "custom assignments"))
        return keys + (go_dark if lights_off else give_back)

    def lights_out(self) -> None:
        """The dark of a teardown with `lights_off`: every cluster off with the claim kept
        (Paint.darken), then a synced mouse black as the keyboard goes dark, whichever keyboard
        leads. Mice.teardown_all writes it black too, for a keyboard away or not answering."""
        self.paint.darken(self._lighting)
        self.d.mice.color.sync_mouse_color(0x000000)

    def teardown_said(self, lights_off: bool, hand_back: bool, silent: bool,
                      done: list[str], missed: list[str]) -> str:
        """What teardown_device did and did not do, for the journal; asked while the handle is
        still held."""
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


#: The commands that act on one keyboard: the leader's, or the one a request names in
#: `keyboard` (Keyboards.aimed). The rest are every keyboard's: the M profile and its name, the
#: saved colors and the idle times.
AIMED = frozenset({"brightness", "color", "effect", "preview", "paint"})


class Keyboards:
    """Every keyboard the daemon drives at once, a controller (`Keyboard`) each: the owner's
    decision of 2026-10-21, "B: separate pages, follow switch". Daemon.keyboard is the
    leader's controller (`leading`). Each keyboard plugged in that no controller holds is
    connected (connect_due). The node of one that does not answer, as a receiver's with its
    keyboard switched off, is waited on (Waiting) and asked again only at its look: a probe
    nothing answers holds the daemon's one thread, the other keyboards' keys with it. A look
    that fails other than by an unanswered probe ends the wait (connect_on)."""

    def __init__(self, daemon: Daemon) -> None:
        self.d = daemon
        #: The lock's level, every keyboard's: lit, or dim or dark while the screen is locked
        #: or blank, dark from a suspend's start to its resume.
        self.lock_lights = LIT
        #: Each controller made: the first at the start, and one more for each keyboard driven
        #: beside the others, kept once its keyboard goes for that model's next connect (spare).
        self.controllers = [Keyboard(daemon)]
        #: The keyboard nodes waited on (Waiting.nodes, by path).
        self.waiting = Waiting(daemon)
        #: When the keyboards plugged in are next looked for beside the one connect_due
        #: connects first (connect_others); 0.0 until the first look.
        self._look_at = 0.0

    # -- which keyboard ------------------------------------------------------

    def leading(self) -> Keyboard:
        """The leader's controller: the one driving the leader in force (PerKeyboard.leader),
        else the first driving any keyboard; with none driven, the one that drove the leader's
        model last, else the first made."""
        leader = self.d.per_keyboard.leader()
        driven = [keyboard for keyboard in self.controllers if keyboard.dev is not None]
        among = driven or self.controllers
        return next((keyboard for keyboard in among if keyboard.model() == leader), among[0])

    def driven(self) -> list[Keyboard]:
        """Each controller with its keyboard open, the leader's first, so a slow one does not
        delay it, then in the order they were made."""
        leading = self.leading()
        return sorted((keyboard for keyboard in self.controllers if keyboard.dev is not None),
                      key=lambda keyboard: keyboard is not leading)

    def of_model(self, model: str | None) -> Keyboard | None:
        """The controller driving a keyboard of `model`, or None."""
        return next((keyboard for keyboard in self.controllers
                     if keyboard.dev is not None and keyboard.model() == model), None)

    def spare(self, model: str) -> Keyboard:
        """A controller with nothing open for a keyboard of `model`: the one that drove that
        model last, or last tried to set it up, else one never used, else a new one shown at
        the lock's level (lock_lights, Idle.keyboard_awake)."""
        free = [keyboard for keyboard in self.controllers if keyboard.dev is None]
        for keyboard in free:
            if keyboard.model() == model:
                return keyboard
        for keyboard in free:
            if keyboard.keyboard_product is None:
                return keyboard
        made = Keyboard(self.d)
        made.idle.keyboard_awake()
        self.controllers.append(made)
        return made

    def sharing(self, keyboard: Keyboard) -> list[Keyboard]:
        """The other keyboards driven that show `keyboard`'s own lighting at its brightness:
        with `keyboard` the leader's, each that follows it (PerKeyboard.follows); none
        otherwise."""
        if not keyboard.leads():
            return []
        follows = self.d.per_keyboard.follows
        return [other for other in self.driven()
                if other is not keyboard and follows(other.model())]

    # -- connecting ----------------------------------------------------------

    def connect_due(self, next_attempt: float) -> float:
        """Connect the keyboard PerKeyboard finds first (find_keyboard) once `next_attempt`
        has passed while none is driven, each other keyboard plugged in that no controller
        holds (connect_others), and each waited on whose look is due (connect_waited). The
        answer is when the first is tried next, after a try that connected none (retry_in)."""
        tried = None
        if self.d.keyboard.dev is None and time.monotonic() >= next_attempt:
            tried = self.d.per_keyboard.find_keyboard()
            if tried is None or not self.connect_on(tried):
                next_attempt = time.monotonic() + (delay := self.retry_in(tried))
                log.debug("no keyboard set up; looking again in %g s", delay)
        self.connect_others(tried)
        self.connect_waited()
        return next_attempt

    def retry_in(self, found: Found | None) -> float:
        """Seconds to the next look after one that set up no keyboard on `found`: the looks'
        pace (Plugs.pace) where none was found or the node is waited on (Waiting.wait_for); a
        node found that failed otherwise is tried again RECONNECT_DELAY later, since no event
        comes for a node already there."""
        if found is None or self.waiting.waits_on(found):
            return self.d.plugs.pace(RECONNECT_DELAY)
        return RECONNECT_DELAY

    def connect_on(self, found: Found) -> bool:
        """Connect the keyboard on node `found` on a spare controller, unless the node is
        waited on and its look is not due (Waiting.due). Whether it was set up. A node no
        keyboard answered on is waited on, or its wait doubled (Waiting.wait_for); one set up
        is waited on no more. So is one whose connect fails otherwise, its open refused, its
        keyboard held on its own profile or its setup cut short: that look would be due at
        every pass, and the loop's select would not wait (deadlines). It is tried every
        RECONNECT_DELAY again (connect_others), as a cable's node is."""
        path = str(found[0])
        if not self.waiting.due(path):
            return False
        keyboard = self.spare(model_key(found[1]))
        others = self.views(keyboard)
        done = keyboard.connect(found)
        if done is None:
            self.waiting.wait_for(found)
            return False
        self.waiting.stop_waiting(path)
        if done:
            self.repaint_changed(others)
        return done

    def connect_others(self, tried: Found | None) -> None:
        """At the looks' pace (Plugs.pace), connect each keyboard plugged in
        (keyboard.attached_keyboards) whose model no controller drives, but for `tried`, which
        connect_due tried already in this pass, and one held
        (OnboardHold.held_onboard); sooner after one that failed (retry_in). From sysfs, over
        one walk."""
        now = time.monotonic()
        if not look_due(now, self._look_at):
            return
        self._look_at = now + self.d.plugs.pace(RECONNECT_DELAY)
        for found in attached_keyboards():
            if (found != tried and self.of_model(model_key(found[1])) is None
                    and not self.connect_on(found)):
                self._look_at = min(self._look_at, now + self.retry_in(found))

    def look_for_cables(self, force: bool = False) -> bool:
        """Each driven keyboard's look for its cable while on its receiver, when due or with
        `force` (Link.keyboard_moved_to_cable, look_for_cable_again): True when one moved to
        it, for the loop to connect it at once."""
        # Each keyboard looks, not only those up to the first that moved.
        moved = [keyboard.link.keyboard_moved_to_cable(force)
                 or keyboard.link.look_for_cable_again() for keyboard in self.driven()]
        return any(moved)

    def look_now(self) -> None:
        """Have the next pass look for the keyboards plugged in (connect_others): one moved to
        its cable, or one whose node the watch saw come (Plugs), is connected at once."""
        self._look_at = time.monotonic()

    # -- waiting for a keyboard switched off ---------------------------------

    def connect_waited(self) -> None:
        """Connect the keyboard of each node waited on whose look is due (Waiting.wait_for,
        Waiting.pump_waiting); one whose model is driven on another node, as on its cable, is
        waited on no more. One whose look fails other than by an unanswered probe is looked
        for again RECONNECT_DELAY later (retry_in), as in connect_others."""
        for path, waited in list(self.waiting.nodes.items()):
            if self.of_model(model_key(waited.found[1])) is not None:
                self.waiting.stop_waiting(path)
            elif not self.connect_on(waited.found):
                self._look_at = min(self._look_at, time.monotonic() + self.retry_in(waited.found))

    # -- the loop ------------------------------------------------------------

    def deadlines(self) -> list[float]:
        """When the keyboards next need the loop: each driven's own, each battery poll (tend),
        each look of a node waited on, and the look for the keyboards plugged in
        (connect_others) once the first has run."""
        return [*(at for keyboard in self.driven() for at in keyboard.deadlines()),
                *(at for keyboard in self.driven() or [self.d.keyboard]
                  for at in keyboard.battery.deadlines()),
                *self.waiting.deadlines(),
                *([self._look_at] if self._look_at else [])]

    def tend(self) -> None:
        """Once a pass, the battery poll of each keyboard driven, or of the leader's while none
        is (KeyboardBattery.poll_battery), then the work each keyboard driven has due."""
        for keyboard in self.driven() or [self.d.keyboard]:
            keyboard.battery.poll_battery()
        for keyboard in self.driven():
            keyboard.idle.tend_idle()
            keyboard.write_due_leds()
            keyboard.key_control.tend()

    def pending(self) -> bool:
        """Whether a keyboard driven has reports queued, which do not wake select."""
        return any(keyboard.dev is not None and keyboard.dev.pending for keyboard in self.driven())

    def pump_queued(self) -> None:
        """Read each keyboard driven whose reports a request queued while it waited: otherwise
        a G key pressed during a status read or a command waits for the next report, and a
        quick tap comes out with its release."""
        for keyboard in self.driven():
            if keyboard.dev is not None and keyboard.dev.pending:
                keyboard.pump()

    # -- every keyboard at once ----------------------------------------------

    def views(self, besides: Keyboard) -> dict[Keyboard, tuple[ProfileLighting, int]]:
        """The lighting and the brightness each keyboard driven but `besides` shows
        (PerKeyboard.view), for repaint_changed."""
        return {keyboard: self.d.per_keyboard.view(keyboard) for keyboard in self.driven()
                if keyboard is not besides}

    def repaint_changed(self, was: dict[Keyboard, tuple[ProfileLighting, int]]) -> None:
        """Paint each keyboard of `was` still driven that shows other lighting or brightness
        now than `was` gives it (PerKeyboard.view)."""
        for keyboard, view in was.items():
            if keyboard.dev is not None and self.d.per_keyboard.view(keyboard) != view:
                keyboard.apply_profile()

    def apply_each(self, first: Keyboard | None = None) -> str | None:
        """Show the layer in force on every keyboard driven, `first`, the leader's by default,
        before the others; the answer is first's apply_profile's."""
        first = self.d.keyboard if first is None else first
        failed = first.apply_profile()
        for keyboard in self.driven():
            if keyboard is not first:
                keyboard.apply_profile()
        return failed

    def wake_each(self) -> None:
        """A change to the layer in force counts as activity at every keyboard driven, and at
        the leader's while none is (Idle.wake_for_change)."""
        for keyboard in self.driven() or [self.d.keyboard]:
            keyboard.idle.wake_for_change()

    def wake_others(self, woken: Keyboard, why: str) -> None:
        """Each other keyboard driven below lit is lit with `woken` (Idle.wake_with_another,
        for `why`), whatever `woken` shows, since they dim together (the owner's decision of
        2026-10-21, "Together")."""
        for keyboard in self.driven():
            if keyboard is not woken and keyboard.idle_lights != LIT:
                keyboard.idle.wake_with_another(why)

    def idle_changed(self, before: tuple[int, int, bool], paint: bool = True) -> str | None:
        """New idle settings, from `before`: a synced mouse and a synced headset's lights follow
        the desktop's idleness at them (Session.watch_idle_for_both), then every keyboard driven
        takes them (Idle.idle_changed), the leader's first; the answer is the leader's note."""
        leading = self.d.keyboard
        if before != leading.idle.idle_settings():
            self.d.session.watch_idle_for_both(paint)
        note = leading.idle.idle_changed(before, paint)
        for keyboard in self.driven():
            if keyboard is not leading:
                keyboard.idle.idle_changed(before, paint)
        return note

    def problems(self) -> list[tuple[str, str, str | None]]:
        """The leader's keyboard's problem (KeyboardCommands.problems), then each other
        keyboard driven that does not answer, and each other held
        (KeyboardCommands.kept_problem), in the order they were made, each as (code,
        message, the key of the model it is about)."""
        leading = self.d.keyboard
        found: list[tuple[str, str, str | None]] = []
        for keyboard in sorted(self.controllers, key=lambda keyboard: keyboard is not leading):
            undriven = keyboard is not leading and keyboard.dev is None
            found += keyboard.handlers.kept_problem() if undriven else keyboard.handlers.problems()
        return found

    def in_use(self, idle: Keyboard) -> Keyboard | None:
        """Another keyboard driven, beside `idle`, that has been typed on since its own idle
        notice, or None: the keyboards dim together, once none has been typed on (the owner's
        decision of 2026-10-21, "Together"). One whose idle level is not known, as one
        switched off, is passed by (Keyboard.idle_level_known)."""
        return next((keyboard for keyboard in self.driven() if keyboard is not idle
                     and keyboard.idle_level_known() and not keyboard.idle.idle_now()), None)

    def set_lights(self, level: str, why: str, paint: bool = True) -> bool:
        """The lock's level to `level` (lock_lights), and every keyboard at it
        (Idle.set_lights), each driven painted unless `paint` is False, said at info for the
        leader's alone; the others take it at their claim (Idle.keyboard_awake). Whether the
        leader's lights changed."""
        before, self.lock_lights = self.lock_lights, level
        leading = self.d.keyboard
        changed = leading.idle.set_lights(level, why, paint=paint, lock_before=before)
        for keyboard in self.controllers:
            if keyboard is leading:
                continue
            if keyboard.dev is not None:
                keyboard.idle.set_lights(level, why, paint=paint, quiet=True, lock_before=before)
            else:
                keyboard.idle.keyboard_awake()
        return changed

    def dark_for_sleep(self) -> str:
        """The lock's level dark until the resume (lock_lights), so a keyboard set up before
        it comes up dark too, and every keyboard driven dark before a suspend
        (Idle.dark_for_sleep), as `named` runs it: what happened, for the journal."""
        self.lock_lights = DARK
        return "; ".join(self.named(lambda keyboard: keyboard.idle.dark_for_sleep()))

    def awake_after_sleep(self, level: str) -> None:
        """After a resume: the lock's level to `level` (lock_lights), which dark_for_sleep set
        dark, and every controller's own level back to lit, shown at the lock's level but not
        painted (Idle.keyboard_awake): each keyboard driven is set up again after
        (Link.recheck_after_resume)."""
        self.lock_lights = level
        for keyboard in self.controllers:
            keyboard.idle.keyboard_awake()

    def named(self, act: Callable[[Keyboard], str]) -> list[str]:
        """`act` on every keyboard driven, the leader's first, or on the leader's alone while
        none is: what each says, named by its keyboard where more than one was driven."""
        driven = self.driven() or [self.d.keyboard]
        return [named_words(keyboard.model_name(), act(keyboard), len(driven) > 1)
                for keyboard in driven]

    def teardown_all(self, lights_off: bool) -> list[str]:
        """Hand every keyboard driven back, or darken it with `lights_off`, as `named` runs it,
        and close each node waited on, for the daemon's stop: what each teardown did."""
        said = self.named(lambda keyboard: keyboard.teardown_device(lights_off=lights_off,
                                                                    stopping=True))
        self.waiting.stop_all()
        return said

    # -- commands ------------------------------------------------------------

    def commands(self) -> dict[str, Callable[[dict], dict]]:
        """Each keyboard command, sent to the keyboard it acts on (run)."""
        return {name: partial(self.run, name) for name in self.controllers[0].commands()}

    def run(self, name: str, request: dict) -> dict:
        """Command `name` on the keyboard it acts on: one of AIMED on the one its request
        names (`aimed`), any other on the leader's, whose M profile, saved colors and idle
        times are every keyboard's. A keyboard gone mid-command is torn down and the command
        refused, as handle_command does for one of no controller."""
        keyboard = self.aimed(request) if name in AIMED else self.d.keyboard
        try:
            return keyboard.commands()[name](request)
        except DeviceGone as exc:
            keyboard.teardown_device()
            return hidpp_refusal("keyboard", exc)

    def aimed(self, request: dict) -> Keyboard:
        """The keyboard a lighting command acts on: the one its `keyboard` names, by any of
        its model's ids or its name (shared.keyboard_model_named), driven or set up since the
        start, else the leader's (the owner's decision of 2026-10-21, "The leader, --keyboard
        for another"). A model none of whose keyboards has been set up since the start is
        refused (NOT_SET_UP_YET), with nothing changed."""
        named = request.get("keyboard")
        if named is None:
            return self.d.keyboard
        model = keyboard_model_named(named, "keyboard")
        # Not by a controller's model, which names a keyboard nothing answered on too
        # (Keyboard.set_up).
        if model not in self.d.per_keyboard.attached:
            raise ConfigError(NOT_SET_UP_YET.format(key_name(model)))
        return self.of_model(model) or next(
            keyboard for keyboard in self.controllers if keyboard.model() == model)

    def shown_in(self, request: dict) -> Keyboard:
        """The keyboard `status` gives the entries of: the one its `keyboard` names, as
        `aimed` finds it, else the leader's, as for a model none of whose keyboards has been
        set up since the start, so a window on a keyboard gone still reads."""
        try:
            return self.aimed(request)
        except ConfigError:
            return self.d.keyboard

    def plugged_in(self) -> list[dict]:
        """`status`'s `keyboards_attached`: each keyboard model plugged in now
        (keyboard.attached_keyboards), by key, and whether it is switched off (the owner's
        decision of 2026-10-20): one not driven whose node is waited on, since nothing
        answered its probe (Waiting.waits_on). Nothing is sent to a keyboard here."""
        return [{"model": (model := model_key(found[1])),
                 "switched_off": self.of_model(model) is None and self.waiting.waits_on(found)}
                for found in attached_keyboards()]

    def summary(self) -> list[dict]:
        """`status`'s `keyboards_driven`: each keyboard driven, the leader's first, by its
        model, name and product id, whether it answers and whether it follows the leader."""
        follows = self.d.per_keyboard.follows
        return [{"model": keyboard.model(), "name": keyboard.model_name(),
                 "product": f"{keyboard.keyboard_product:04x}",
                 "answering": keyboard.keyboard_answering(), "follows": follows(keyboard.model())}
                for keyboard in self.driven()]
