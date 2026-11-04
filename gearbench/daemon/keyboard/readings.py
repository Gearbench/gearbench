"""What status, the tray and the battery notices read off the keyboard: who holds its lighting
(keyboard_sw_control), its battery, kept per handle (keyboard_battery), both as status reads them
(status_readings), both dropped at a mark and at a resume (forget_live), a battery report it
sent by itself kept the same way (keep_report), and the reading the low-battery notices count
once (battery_for_notices), and external power for them on the cable (cable_power)."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, cast

from ...hidpp import PROBE_TIMEOUT, DeviceGone, HidppError, HidppFailure
from ...keyboard import CABLED_IDS as KEYBOARD_CABLED, read_battery
from ...keyboard_zones import power_modes
from ..shared import (
    KEYBOARD_BATTERY_INTERVAL,
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
CABLE_SW_CONTROL: ProbeBudget = {"timeout": PROBE_TIMEOUT, "retries": 1}
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
        #: state it found, the seconds status gives that again behind a receiver, the battery
        #: reading it found).
        self._status_read: tuple[Hidpp, float, int | None, float, dict | None] | None = None
        #: When a battery read of the handle behind its receiver last gave no reading while the
        #: keyboard answered, as (handle, monotonic time), which holds its reads and their probe
        #: off for KEYBOARD_BATTERY_INTERVAL (keyboard_battery).
        self._battery_unread: tuple[Hidpp, float] | None = None

    def status_readings(self) -> tuple[int | None, dict | None]:
        """The lighting state and the battery reading status gives (keyboard_sw_control, then
        keyboard_battery, which is not asked once the first went unanswered and marked the
        keyboard), both None for a keyboard away or marked not answering, which is asked
        nothing.

        On its cable each status reads the lighting state, and the battery where no reading of
        the handle younger than KEYBOARD_BATTERY_INTERVAL stands (keyboard_battery). Behind its
        receiver both are read once per KEYBOARD_BATTERY_INTERVAL on a handle, the battery's
        pace (the owner's decision of 2026-10-26), MOUSE_SILENT_RECHECK after its own
        lighting-state read went unanswered, and again after a resume or any other mark drops
        what was read (forget_live); in between status gives what that read found, no battery
        reading included, or one taken since (battery_read), by the tray's read or the
        keyboard's own report: the G915 X LIGHTSPEED idle there answered every request 0.9 to
        1.5 s after it was sent and slept again after each answer (measured 2026-10-24), the
        window asks every three seconds, and the daemon's one thread waits for each answer.
        """
        keyboard = self.keyboard
        if not keyboard.keyboard_answering():
            return None, None
        dev, now, last = cast("Hidpp", keyboard.dev), time.monotonic(), self._status_read
        if (keyboard.keyboard_product not in KEYBOARD_CABLED and last is not None
                and last[0] is dev and now - last[1] < last[3]):
            taken = self.battery_read
            if taken is not None and taken[0] is dev and taken[1] >= last[1]:
                return last[2], taken[2]
            return last[2], last[4]
        control = self.keyboard_sw_control()
        # Asked again as the mark runs out: given as answering after it, unasked, a keyboard
        # switched off would show as answering until the battery's pace came round.
        wait = (KEYBOARD_BATTERY_INTERVAL if keyboard.keyboard_answering()
                else MOUSE_SILENT_RECHECK)
        self._status_read = (dev, now, control, wait, None)
        battery = self.keyboard_battery()
        if self._status_read is not None:
            # Unless the battery's probe marked the keyboard, whose mark dropped the read.
            self._status_read = (dev, now, control, wait, battery)
        return control, battery

    def keyboard_sw_control(self) -> int | None:
        """Who holds the lighting, as the keyboard says, or None if it does not say: one lit
        through 0x8070's zones has no such read (keyboard_zones.power_modes).

        A live request whenever status reads the keyboard (status_readings): on CABLE_SW_CONTROL,
        or behind a receiver on RECEIVER_SW_CONTROL, which waits for a keyboard waking from its
        sleep; either fits the three seconds the CLI and the window give status in all. No
        answer, a write or read the kernel failed included, leaves the keyboard unasked, battery
        included, for MOUSE_SILENT_RECHECK, or until it sends something, a late reply included
        (Keyboard.pump).
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
            elif isinstance(exc, HidppFailure) and not isinstance(exc, HidppError):
                # The kernel failed the write or read outside hidpp.GONE_ERRNOS: no answer, as
                # keyboard_answers takes it, so marked, and status asks again as the mark runs out.
                keyboard.mark_keyboard_silent(str(exc))
            else:
                log.debug("the %s's lighting-state read failed: %s", keyboard.model_name(), exc)
            return None
        keyboard.mark_keyboard_answering()
        return control

    def keyboard_battery(self) -> dict | None:
        """The keyboard's battery reading, or None when there is nothing to say.

        Kept for KEYBOARD_BATTERY_INTERVAL per handle (battery_read), since the window asks every
        three seconds. No reading, or no answer, is followed by one probe
        (Keyboard.keyboard_answers), which marks only a keyboard that is off. Behind its
        receiver, a read that gave no reading while the keyboard answered is not made again,
        nor its probe, for KEYBOARD_BATTERY_INTERVAL on the handle (_battery_unread): the tray
        asks every two seconds and at each client command, and each request there waits about a
        second. On its cable each call reads again. One with no battery feature is asked
        nothing: nothing would be kept, so each call would probe.
        """
        keyboard = self.keyboard
        if not keyboard.keyboard_answering() or "battery" not in keyboard.keyboard_able:
            return None
        kept = self._standing()
        if kept is not None:
            return kept[2]
        dev = cast("Hidpp", keyboard.dev)
        unread = self._battery_unread
        if (unread is not None and unread[0] is dev
                and time.monotonic() - unread[1] < KEYBOARD_BATTERY_INTERVAL):
            return None
        try:
            reading = read_battery(dev, keyboard.keyboard_product, keyboard.model_name())
            if reading is not None:
                self.battery_read = (dev, time.monotonic(), reading)
                self.battery_kept = (dev, round(time.time()), reading)
                keyboard.mark_keyboard_answering()
            else:
                # A read refused or failed: the probe says whether the keyboard answers.
                keyboard.keyboard_answers()
                self._unread(dev)
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
            self._unread(dev)
            return None

    def _unread(self, dev: Hidpp) -> None:
        """Note a read of `dev` behind its receiver that gave no reading (_battery_unread),
        unless its probe marked the keyboard, whose mark drops what was kept (forget_live)."""
        keyboard = self.keyboard
        if keyboard.keyboard_product not in KEYBOARD_CABLED and keyboard.keyboard_answering():
            self._battery_unread = (dev, time.monotonic())

    def _standing(self) -> tuple[Hidpp, float, dict] | None:
        """battery_read while it stands: of the handle attached now, and younger than
        KEYBOARD_BATTERY_INTERVAL."""
        kept = self.battery_read
        return (kept if kept is not None and kept[0] is self.keyboard.dev
                and time.monotonic() - kept[1] < KEYBOARD_BATTERY_INTERVAL else None)

    def forget_live(self) -> None:
        """Drop the battery reading, a read that gave none and the lighting state kept by their
        age, at a mark (Keyboard.mark_keyboard_silent) and at a resume: taken before the mark,
        each would stand for a keyboard that may still be off once the mark runs out, and taken
        before the suspend, as fresh after it, since time.monotonic() stops while the machine
        sleeps.
        battery_kept stays, dated by the wall clock."""
        self.battery_read = self._status_read = self._battery_unread = None

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
        kept = self.battery_kept
        if self.keyboard.keyboard_answering() or kept is None or kept[0] is not self.keyboard.dev:
            return None
        return kept[2], kept[1]

    def cable_power(self) -> dict | None:
        """What the battery notices take from the keyboard on its cable, which charges it,
        whatever it reads or reports: external power. None on its receiver."""
        return ({"external_power": True} if self.keyboard.keyboard_product in KEYBOARD_CABLED
                else None)

    def battery_for_notices(self) -> dict | None:
        """The keyboard's reading for the battery notices, or None. On its cable the keyboard is
        charging and not asked. A reading already counted is read again, so one cannot count
        twice, after a probe on RECEIVER_SW_CONTROL's budget: behind its receiver, the only
        route left here, one asleep answers past the probe's own 1.5 s."""
        if (power := self.cable_power()) is not None:
            return power
        keyboard = self.keyboard
        kept = self._standing()
        if kept is None or kept[1] == self._battery_counted:
            if not keyboard.keyboard_answers(RECEIVER_SW_CONTROL):
                return None
            self.battery_read = None
        reading = self.keyboard_battery()
        if reading is not None and self.battery_read is not None:
            self._battery_counted = self.battery_read[1]
        return reading
