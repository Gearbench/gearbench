"""What status, the tray and the battery notices read off the keyboard: who holds its lighting
(keyboard_sw_control), its battery, kept per handle (keyboard_battery), both as status reads them
(status_readings), both dropped at a mark and at a resume (forget_live), a battery report it
sent by itself kept the same way (keep_report), and the reading the low-battery notices count
once (battery_for_notices)."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, cast

from ...hidpp import DeviceGone, HidppFailure
from ...keyboard import CABLED_IDS as KEYBOARD_CABLED, read_battery
from ...keyboard_zones import power_modes
from ..shared import (
    KEYBOARD_BATTERY_INTERVAL,
    MOUSE_PROBE_TIMEOUT,
    MOUSE_SILENT_RECHECK,
    ProbeBudget,
    unanswered,
)

if TYPE_CHECKING:
    from ...hidpp import Hidpp
    from . import Keyboard

log = logging.getLogger("gearbench")

#: The lighting-state read's budget for a keyboard on its cable: the probe's half second, sent
#: twice.
CABLE_SW_CONTROL: ProbeBudget = {"timeout": MOUSE_PROBE_TIMEOUT, "retries": 1}
#: The same read's budget for a keyboard behind its receiver, and the notices poll's probe's
#: (battery_for_notices): 1.25 s a send, sent twice, 2.5 s from the first send to the give-up.
#: The G915 X LIGHTSPEED idle on its receiver answered past the cable's 1.0 s: 1.0 to 1.8 s
#: after the first send about 45 s idle with its lights on,
#: 1.24 s at its deepest sleep, two minutes after its lights went off, and at most 1.43 s seen
#: after that (measured 2026-10-24, and once on the G915 on its c541). The receiver answers at
#: once for a keyboard switched off (shared.unanswered), so the wait is spent on one asleep.
RECEIVER_SW_CONTROL: ProbeBudget = {"timeout": 1.25, "retries": 1}


class Readings:
    """The keyboard's lighting state and battery, as status and the notices ask for them."""

    def __init__(self, keyboard: Keyboard) -> None:
        self.keyboard = keyboard
        #: The last battery reading, as (handle, monotonic time, reading). Keyed by the handle: a
        #: reconnect or another route is another reading.
        self.battery_read: tuple[Hidpp, float, dict] | None = None
        #: The last reading with the wall-clock time it was taken, kept while the keyboard does
        #: not answer, for status and the tray to show with its time (kept_battery).
        self.battery_kept: tuple[Hidpp, int, dict] | None = None
        #: When the reading the low-battery notices last counted was taken, so one counts once.
        self._battery_counted: float | None = None
        #: Status's last live read (status_readings), as (handle, monotonic time, the lighting
        #: state it found, the seconds status gives that again behind a receiver).
        self._status_read: tuple[Hidpp, float, int | None, float] | None = None

    def status_readings(self) -> tuple[int | None, dict | None]:
        """The lighting state and the battery reading status gives (keyboard_sw_control, then
        keyboard_battery, which is not asked once the first went unanswered and marked the
        keyboard), both None for a keyboard away or marked not answering, which is asked
        nothing.

        On its cable each status reads them. Behind its receiver they are read once per
        KEYBOARD_BATTERY_INTERVAL on a handle, the battery's pace (the owner's decision of
        2026-10-26), MOUSE_SILENT_RECHECK after its own lighting-state read went unanswered,
        and again after a resume or any other mark drops what was read (forget_live); in
        between status gives what that read found and the last battery reading of the handle
        (battery_kept): the G915 X LIGHTSPEED idle there answered every request 0.9 to 1.5 s
        after it was sent and slept again after each answer (measured 2026-10-24), the window
        asks every three seconds, and the daemon's one thread waits for each answer.
        """
        keyboard = self.keyboard
        if not keyboard.keyboard_answering():
            return None, None
        dev, now, last = cast("Hidpp", keyboard.dev), time.monotonic(), self._status_read
        if (keyboard.keyboard_product not in KEYBOARD_CABLED and last is not None
                and last[0] is dev and now - last[1] < last[3]):
            kept = self._kept_here()
            return last[2], None if kept is None else kept[0]
        control = self.keyboard_sw_control()
        # Asked again as the mark runs out: given as answering after it, unasked, a keyboard
        # switched off would show as answering until the battery's pace came round.
        wait = (KEYBOARD_BATTERY_INTERVAL if keyboard.keyboard_answering()
                else MOUSE_SILENT_RECHECK)
        self._status_read = (dev, now, control, wait)
        return control, self.keyboard_battery()

    def keyboard_sw_control(self) -> int | None:
        """Who holds the lighting, as the keyboard says, or None if it does not say: one lit
        through 0x8070's zones has no such read (keyboard_zones.power_modes).

        A live request whenever status reads the keyboard (status_readings): on CABLE_SW_CONTROL,
        or behind a receiver on RECEIVER_SW_CONTROL, which waits for a keyboard waking from its
        sleep; either fits the three seconds the CLI and the window give status in all. No
        answer leaves the keyboard unasked, battery included, for MOUSE_SILENT_RECHECK, or until
        it sends something, a late reply included (Keyboard.pump).
        """
        keyboard = self.keyboard
        if keyboard.lighting is None or not power_modes(keyboard.lighting):
            return None
        budget = (CABLE_SW_CONTROL if keyboard.keyboard_product in KEYBOARD_CABLED
                  else RECEIVER_SW_CONTROL)
        try:
            control = keyboard.lighting.sw_control(**budget)
        except DeviceGone:
            log.info("the %s went away while reading its lighting state", keyboard.model_name())
            keyboard.teardown_device()
            return None
        except (HidppFailure, IndexError) as exc:
            if unanswered(exc):
                keyboard.keyboard_unanswered(exc)
            else:
                log.debug("the %s's lighting-state read failed: %s", keyboard.model_name(), exc)
            return None
        keyboard.mark_keyboard_answering()
        return control

    def keyboard_battery(self) -> dict | None:
        """The keyboard's battery reading, or None when there is nothing to say.

        Kept for KEYBOARD_BATTERY_INTERVAL per handle (battery_read), since the window asks every
        three seconds. No reading, or no answer, is followed by one probe
        (Keyboard.keyboard_answers), which marks only a keyboard that is off. One with no battery
        feature is asked nothing: nothing would be kept, so each call would probe.
        """
        keyboard = self.keyboard
        if not keyboard.keyboard_answering() or "battery" not in keyboard.keyboard_able:
            return None
        kept = self._standing()
        if kept is not None:
            return kept[2]
        dev = cast("Hidpp", keyboard.dev)
        try:
            reading = read_battery(dev, keyboard.keyboard_product, keyboard.model_name())
            if reading is not None:
                self.battery_read = (dev, time.monotonic(), reading)
                self.battery_kept = (dev, round(time.time()), reading)
                keyboard.mark_keyboard_answering()
            else:
                # A read refused or failed: the probe says whether the keyboard answers.
                keyboard.keyboard_answers()
            return reading
        except DeviceGone:
            log.info("the %s went away while reading its battery", keyboard.model_name())
            keyboard.teardown_device()
            return None
        except (HidppFailure, IndexError) as exc:
            # IndexError is the decode's, behind the transport's own length check. The rest of
            # status still stands.
            log.debug("the %s's battery read failed: %s", keyboard.model_name(), exc)
            if unanswered(exc):
                # One probe, not keyboard_unanswered: a lone missed read does not mark a
                # keyboard that answers.
                keyboard.keyboard_answers()
            return None

    def _standing(self) -> tuple[Hidpp, float, dict] | None:
        """battery_read while it stands: of the handle attached now, and younger than
        KEYBOARD_BATTERY_INTERVAL."""
        kept = self.battery_read
        return (kept if kept is not None and kept[0] is self.keyboard.dev
                and time.monotonic() - kept[1] < KEYBOARD_BATTERY_INTERVAL else None)

    def forget_live(self) -> None:
        """Drop the battery reading and the lighting state kept by their age, at a mark
        (Keyboard.mark_keyboard_silent) and at a resume: taken before the mark, either would
        stand for a keyboard that may still be off once the mark runs out, and taken before
        the suspend, as fresh after it, since time.monotonic() stops while the machine sleeps.
        battery_kept stays, dated by the wall clock."""
        self.battery_read = self._status_read = None

    def keep_report(self, reading: dict) -> None:
        """Keep `reading`, a battery report the keyboard sent by itself
        (KeyboardBattery.take_report), as the last reading of its handle, and as one the
        notices have counted, since they were given it at once."""
        dev = cast("Hidpp", self.keyboard.dev)
        now = time.monotonic()
        self.battery_read = (dev, now, reading)
        self.battery_kept = (dev, round(time.time()), reading)
        self._battery_counted = now

    def kept_battery(self) -> tuple[dict, int] | None:
        """The last reading of the keyboard attached now and when it was taken, for a keyboard
        that does not answer; None for one that answers, or with none read of this handle."""
        return None if self.keyboard.keyboard_answering() else self._kept_here()

    def _kept_here(self) -> tuple[dict, int] | None:
        """The last reading of the keyboard attached now and when it was taken (battery_kept),
        or None with none read of this handle."""
        kept = self.battery_kept
        return None if kept is None or kept[0] is not self.keyboard.dev else (kept[2], kept[1])

    def battery_for_notices(self) -> dict | None:
        """The keyboard's reading for the battery notices, or None. On its cable the keyboard is
        charging and not asked. A reading already counted is read again, so one cannot count
        twice, after a probe on RECEIVER_SW_CONTROL's budget: behind its receiver, the only
        route left here, one asleep answers past the probe's own 1.5 s."""
        keyboard = self.keyboard
        if keyboard.keyboard_product in KEYBOARD_CABLED:
            return {"external_power": True}
        kept = self._standing()
        if kept is None or kept[1] == self._battery_counted:
            if not keyboard.keyboard_answers(RECEIVER_SW_CONTROL):
                return None
            self.battery_read = None
        reading = self.keyboard_battery()
        if reading is not None and self.battery_read is not None:
            self._battery_counted = self.battery_read[1]
        return reading
