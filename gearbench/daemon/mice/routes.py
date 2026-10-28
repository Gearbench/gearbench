"""The routes of a mouse with a cable and a receiver, and which one the daemon drives: one
attached on its cable covers its receiver route (Mice.attached_models); one attached through
its receiver has its cable looked for (`look_for_cables`); and an attach on the other route
drops the route the mouse left (`left_route`). Nothing here writes to a mouse itself:
`left_route` asks the route left one probe, and a cable found is set up by
Mice.connect_mouse."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ...mouse import CABLED_IDS as MOUSE_CABLED, find_attached_mice
from ..shared import CABLE_LOOK, look_due, node_denied
from .attached import AttachedMouse, dpi_keys, other_route

if TYPE_CHECKING:
    from .. import Daemon
    from . import Mice

log = logging.getLogger("gearbench")


class Routes:
    """Which route of each mouse with two is driven."""

    def __init__(self, daemon: Daemon, mice: Mice) -> None:
        self.d = daemon
        self.mice = mice
        #: When the cable is next looked for, monotonic (look_for_cables); 0.0 until a look
        #: has run or look_now has asked for one.
        self._look_at = 0.0
        #: The cable nodes the look tried to attach and did not, by path: left to the attach
        #: retry and to what the node says (Mice.keep_waiting) while the node is there, since
        #: each probe that goes unanswered costs the loop 1.5 s. Not one this user could not
        #: open yet, which costs nothing: the look udev's grant brings (Plugs) asks it again.
        self._tried: set[Path] = set()

    def waits_for_cable(self) -> bool:
        """Whether a mouse is attached through its receiver whose model has a cable."""
        return any(m.product not in MOUSE_CABLED and other_route(m.product)
                   for m in self.mice.attached)

    def deadlines(self) -> list[float]:
        """When the cable is next looked for (look_for_cables), while a mouse waits for one,
        once a look has run or look_now has asked for one."""
        return [self._look_at] if self._look_at and self.waits_for_cable() else []

    def look_for_cables(self) -> None:
        """Attach the cable of a mouse attached through its receiver once its node is there,
        looked for through sysfs at the looks' pace (Plugs.pace), or at the next pass after
        look_now: in HOST mode, which it keeps across the move, its button spy reports on the
        cable's node, which nothing reads until the cable is attached. The attach drops the
        receiver route it leaves (left_route). Measured on the G502 X PLUS: on its cable it
        stops answering on its receiver, whose node stays, and it keeps HOST mode; how soon
        after its cable's node appears it stops is not."""
        now = time.monotonic()
        if not look_due(now, self._look_at) or not self.waits_for_cable():
            return
        self._look_at = now + self.d.plugs.pace(CABLE_LOOK)
        ids = {key for m in self.mice.attached if m.product not in MOUSE_CABLED
               for key in dpi_keys(m.product)}
        covered = self.mice.attached_models()
        cables = [found for found in find_attached_mice()
                  if found[1] in MOUSE_CABLED and f"{found[1]:04x}" in ids
                  and found[1] not in covered]
        self._tried &= {found[0] for found in cables}
        for found in cables:
            if found[0] in self._tried:
                continue
            # Asked before the attempt, so a node udev grants during it is not left out.
            refused = node_denied(found[0])
            if not self.mice.connect_mouse(found) and not refused:
                self._tried.add(found[0])

    def look_now(self) -> None:
        """Have the next pass look for the cables (look_for_cables): a node came (Plugs)."""
        self._look_at = time.monotonic()

    def left_route(self, product: int, path: Path) -> list[AttachedMouse]:
        """Drop the same model on its other route if it has gone silent there; answers the
        entries dropped.

        A G502 X PLUS put on its cable stops answering on its receiver, whose node stays, and
        only a node that has gone tears a mouse down. A route whose node has gone, its receiver
        unplugged before the loop read the node's end, is left too. One that answers is a
        second mouse of the same model and stays, as does one on another id of the same kind,
        the G102 LIGHTSYNC's cable id beside the G203 LIGHTSYNC's, which is not asked. The
        dropped entry's state passes to the new one through Mice._mouse_kept.
        """
        cabled = product in MOUSE_CABLED
        left = []
        for other in [m for m in self.mice.attached
                      if (m.product in MOUSE_CABLED) != cabled
                      and f"{m.product:04x}" in dpi_keys(product)]:
            if not other.silent and self.mice.mouse_answers(other, gone=False):
                continue
            # Marked first, so the teardown sends no release to a route that will not answer;
            # not through mark_silent, whose line promises a recheck of an entry dropped here.
            other.silent = True
            log.info("%s moved from %s to %s", other.name, other.dev.path, path)
            self.mice.teardown_mouse(other)
            left.append(other)
        return left
