"""The battery each mouse last read or reported, with when, and the mice's low-battery notices,
by the keyboard's and the headsets' rule (BatteryNotices), from each reading the daemon takes
of a mouse: a battery report the mouse sends by itself (`take_report`), a `mouse` read, and
for a mouse that sends no report the loop's read every MOUSE_BATTERY_INTERVAL (`poll`), which
sends the battery's read alone. Nothing here writes to a mouse."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from ...battery import on_power, read as read_mouse_battery, unified as unified_battery
from ...mouse import CELLS, MouseError
from ..shared import KEYBOARD_BATTERY_INTERVAL, BatteryNotices, look_due
from .attached import AttachedMouse, dpi_keys

if TYPE_CHECKING:
    from .. import Daemon
    from . import Mice

log = logging.getLogger("gearbench")

#: How often the loop reads the battery of a mouse that sends no battery report, for the
#: notices: the keyboard's interval, G HUB's for a keyboard on its receiver; assumed for a
#: mouse, whose read G HUB was not watched making.
MOUSE_BATTERY_INTERVAL = KEYBOARD_BATTERY_INTERVAL


class MouseBattery(BatteryNotices):
    """One mouse's low-battery notices on one route, kept while it is away so a mouse switched
    off and on is not warned of again, and so charging on its cable takes down the notice
    raised on its receiver once no mouse answers there (MouseBatteries.take). Its body fits a
    mouse that charges and one that takes a cell: the records do not say which a model is."""

    BATTERY_PREFIX = ""
    # A 0x1001 reading on external power may be fully charged or not charging.
    BATTERY_POWERED = "is on external power"
    BATTERY_BODY = "Charge it or replace its battery."

    def __init__(self, daemon: Daemon, name: str) -> None:
        self.d = daemon
        #: The model's name, in the notice and the journal.
        self.name = name

    def battery_names(self) -> tuple[str, str]:
        return self.name, f"the {self.name}"


class MouseBatteries:
    """Each mouse's notices (MouseBattery), and the readings they are given, each kept on its
    mouse (`take`)."""

    def __init__(self, daemon: Daemon, mice: Mice) -> None:
        self.d = daemon
        self.mice = mice
        #: Each mouse's notices, by its product id: a second mouse of the model may be attached
        #: on its other route (Routes.left_route).
        self._notices: dict[int, MouseBattery] = {}

    def take(self, attached: AttachedMouse, reading: dict, at: int | None = None) -> None:
        """Keep `reading`, the battery just read from `attached` or reported by it, with when
        in wall-clock seconds (`at`, now by default), and warn of it."""
        attached.battery = reading
        attached.battery_at = round(time.time()) if at is None else at
        self._notices.setdefault(attached.product, MouseBattery(self.d, attached.name)
                                 ).warn_battery(reading)
        if not on_power(reading):
            return
        # On power on one route: what the model's other routes warned of ends too where no
        # mouse answers on them, taken for this mouse moved here, as Routes.left_route takes a
        # route gone silent. A second mouse of the model answering there keeps its notices.
        answering = {m.product for m in self.mice.attached if not m.silent}
        for key in dpi_keys(attached.product)[1:]:
            left = self._notices.get(int(key, 16))
            if left is not None and int(key, 16) not in answering:
                left.warn_battery(reading)

    def take_report(self, attached: AttachedMouse, data: bytes) -> None:
        """Keep the battery a mouse reported by itself, with when, for `mouse`, and warn of it.
        The G502 X PLUS sends 0x1004's status when its battery changes and just after it links
        up, and G HUB reads the battery from those alone.

        A silent mouse is marked answering. Otherwise `heard_at` is left as it was: a battery
        report says nothing about the DPI or the rate, so it does not make the other readings
        due.
        """
        if attached.silent:
            self.mice.mark_answering(attached)
        reading = unified_battery(data)
        attached.battery_reports = True
        log.debug("%s reported its battery: %.0f%% %s", attached.name,
                  reading["percent"], reading["state"])
        self.take(attached, reading)

    def polled(self, attached: AttachedMouse) -> bool:
        """Whether the loop reads this mouse's battery: it has a battery feature, sends no
        report of it, and answers, so no read waits on a mouse off or out of range."""
        return ("battery" in attached.mouse.capabilities and not attached.battery_reports
                and not attached.silent)

    def poll(self) -> None:
        """Every MOUSE_BATTERY_INTERVAL, the battery of each mouse polled() says, and its
        notices. A read the mouse does not answer marks it, as a `mouse` read does
        (Mice.mouse_call); a reading that comes back empty keeps the last one. One it answers
        sets `heard_at`, as any answer does (mark_answering), so the next `mouse` reads the
        mouse in full: that answer also tells a lighting write whose answer was lost from a
        mouse that is off (MouseLighting.check_mouse_write)."""
        now = time.monotonic()
        for attached in list(self.mice.attached):
            if not self.polled(attached):
                continue
            if attached.battery_poll_at is None:
                attached.battery_poll_at = now + MOUSE_BATTERY_INTERVAL
            if not look_due(now, attached.battery_poll_at):
                continue
            attached.battery_poll_at = now + MOUSE_BATTERY_INTERVAL
            try:
                reading = self.mice.mouse_call(read_mouse_battery, attached.dev,
                                               CELLS.get(attached.product), attached.name,
                                               target=attached)
            except MouseError:
                continue
            if reading is not None:
                self.take(attached, reading)

    def deadlines(self) -> list[float]:
        """When poll is next due for each mouse it reads, once a pass has polled it."""
        return [attached.battery_poll_at for attached in self.mice.attached
                if self.polled(attached) and attached.battery_poll_at is not None]
