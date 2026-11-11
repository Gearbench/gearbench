"""The keyboard's low-battery notices, the poll that reads its battery for them and the battery
reports it sends by itself, one per controller (Keyboard.battery), so a notice stays with its
keyboard when the leader changes."""

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING, cast

from ...battery import CABLE_CHARGE, F_UNIFIED, unified as unified_battery
from ...keyboard import model_ids as keyboard_model_ids
from ..shared import KEYBOARD_BATTERY_INTERVAL, BatteryNotices, look_due

if TYPE_CHECKING:
    from pathlib import Path

    from ...hidpp import Event, Hidpp
    from .. import Daemon
    from . import Keyboard

log = logging.getLogger("gearbench")


class KeyboardBattery(BatteryNotices):
    BATTERY_PREFIX = ""
    BATTERY_POWERED = "is on external power"
    BATTERY_BODY = CABLE_CHARGE

    def __init__(self, daemon: Daemon, keyboard: Keyboard) -> None:
        self.d = daemon
        #: The controller whose keyboard these notices are of.
        self.keyboard = keyboard
        #: When the battery is read next.
        self._battery_poll_at = 0.0

    def deadlines(self) -> list[float]:
        """When poll_battery is next due, once the first has run."""
        return [self._battery_poll_at] if self.battery_polled() and self._battery_poll_at else []

    @staticmethod
    def assumed_battery() -> float | None:
        """GEARBENCH_ASSUME_BATTERY as a percentage on battery, or None: how the
        notices are tested on the hardware."""
        raw = os.environ.get("GEARBENCH_ASSUME_BATTERY", "").strip()
        try:
            value = float(raw)
        except ValueError:
            return None
        return value if 0 <= value <= 100 else None

    def assumed_here(self) -> float | None:
        """The assumed percentage (assumed_battery) for this controller's notices: the
        leader's alone."""
        return self.assumed_battery() if self.keyboard.leads() else None

    def battery_polled(self) -> bool:
        """Whether the loop reads the battery for the notices: while the keyboard is driven,
        and for the leader's controller while a reading is assumed."""
        return self.keyboard.dev is not None or self.assumed_here() is not None

    def poll_battery(self) -> None:
        """Every KEYBOARD_BATTERY_INTERVAL, the battery for the notices: the keyboard's
        reading, or for the leader's controller the assumed one in its place. Another keyboard
        model than the last poll's arms both notices again and takes the last one's down."""
        now = time.monotonic()
        if not look_due(now, self._battery_poll_at) or not self.battery_polled():
            return
        self._battery_poll_at = now + KEYBOARD_BATTERY_INTERVAL
        # By name: the cable's and the receiver's ids of one model share it.
        driven = self.keyboard.model_name()
        self.armed_for(driven, f"the battery notices are now the {driven}'s, not the "
                               f"{self.battery_of}'s")
        assumed = self.assumed_here()
        reading = ({"percent": assumed, "external_power": False} if assumed is not None
                   else self.keyboard.readings.battery_for_notices())
        if reading is not None:
            self.warn_battery(reading)

    def take_report(self, event: Event) -> bool:
        """Whether `event` was the keyboard's 0x1004 status, which it sends as its battery
        changes (battery.unified), on its cable and through its receiver alike: the G915 X
        LIGHTSPEED sent one on each on 2026-10-23, and taking it is the owner's decision of
        that day. The reading is kept
        for `status` and the tray (Readings.keep_report) and given to the notices at once,
        as on external power on its cable, as the poll gives them too
        (Readings.cable_power), unless a reading is assumed for them (assumed_here)."""
        unified = cast("Hidpp", self.keyboard.dev).feature(F_UNIFIED)
        # hidpp's _raw_read drops a report shorter than its id's REPORT_LENGTH, so an event
        # has 3 bytes at least.
        if unified is None or event.feature_index != unified.index or event.address != 0:
            return False
        reading = unified_battery(event.data)
        self.keyboard.readings.keep_report(reading)
        log.debug("the %s reported its battery: %.0f%% %s", self.keyboard.model_name(),
                  reading["percent"], reading["state"])
        if self.assumed_here() is None:
            self.warn_battery(self.keyboard.readings.cable_power() or reading)
        return True

    def battery_names(self) -> tuple[str, str]:
        name = self.keyboard.model_name()
        # The notice's title opens with the name: "keyboard" before one is attached.
        return name[:1].upper() + name[1:], f"the {name}"

    def battery_ids(self) -> tuple[str, ...]:
        """The model ids of the keyboard driven, none before one is: a reading assumed then
        goes by the default preset."""
        product = self.keyboard.keyboard_product
        return () if product is None else keyboard_model_ids(f"{product:04x}")

    def battery_found(self) -> bool:
        return "battery" in self.keyboard.keyboard_able

    def battery_node(self) -> Path | None:
        dev = self.keyboard.dev
        return None if dev is None else dev.path
