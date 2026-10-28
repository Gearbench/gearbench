"""The nodes of keyboards that did not answer (Waiting), as a receiver's node with its
keyboard switched off: each kept open for the keyboard's own report and looked at again on a
pace of its own (wait_for, pump_waiting). Keyboards holds it as `Keyboards.waiting`."""

from __future__ import annotations

import contextlib
import logging
import selectors
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ...hidpp import Hidpp, HidppFailure
from ...keyboard import CABLED_IDS, waited_index
from ...wording import reason
from .notes import KEYBOARD_RETRY, KEYBOARD_RETRY_MAX

if TYPE_CHECKING:
    from .. import Daemon

log = logging.getLogger("gearbench")

#: A keyboard's node as keyboard.find_attached_keyboard and attached_keyboards give it: the
#: node, the product id and the device index its topology gives.
Found = tuple[Path, int, int]


@dataclass
class Waited:
    """A keyboard node nothing answered on, kept open for the keyboard's own report
    (Waiting.wait_for): the node open, the node as attached_keyboards gives it, when it is
    asked again and the wait before the next, and until when a report it sends does not
    bring that forward again."""

    dev: Hidpp
    found: Found
    retry_at: float
    wait: float = KEYBOARD_RETRY
    nudged_at: float = 0.0


class Waiting:
    """The keyboard nodes waited on, each kept open on the daemon's selector for the loop to
    read (pump_waiting) and asked again only at its look (due), since a probe nothing
    answers holds the daemon's one thread."""

    def __init__(self, daemon: Daemon) -> None:
        self.d = daemon
        #: The nodes waited on (wait_for), by path.
        self.nodes: dict[str, Waited] = {}

    def wait_for(self, found: Found) -> None:
        """Keep the node `found` names open after its probe went unanswered, on its keyboard's
        device index (waited_index), so the keyboard is heard when it comes on (pump_waiting),
        and ask it again KEYBOARD_RETRY later, twice the last wait after each further
        unanswered probe, up to KEYBOARD_RETRY_MAX: a keyboard switched off waits for its own
        report, as a mouse does (Mice.keep_waiting; the owner's decision of 2026-10-21).
        Not a cable's node, which a keyboard switched off leaves, nor one that cannot be
        opened: the loop asks those again every RECONNECT_DELAY."""
        path = str(found[0])
        now = time.monotonic()
        waited = self.nodes.get(path)
        if waited is not None:
            waited.wait = min(waited.wait * 2, KEYBOARD_RETRY_MAX)
            waited.retry_at = now + waited.wait
            return
        if found[1] in CABLED_IDS:
            return
        try:
            dev = Hidpp(found[0], device_index=waited_index(found))
        except OSError as exc:
            log.debug("cannot keep %s open to wait for its keyboard: %s", path, reason(exc))
            return
        self.nodes[path] = Waited(dev, found, now + KEYBOARD_RETRY)
        self.d.selector.register(dev.fileno, selectors.EVENT_READ, ("keyboard-wait", path))
        log.debug("waiting on %s for its keyboard; asked again in %g s", path, KEYBOARD_RETRY)

    def waits_on(self, found: Found) -> bool:
        """Whether the node `found` names is waited on: nothing answered its probe."""
        return str(found[0]) in self.nodes

    def due(self, path: str) -> bool:
        """Whether the node at `path` may be looked at now: not waited on, or its look due."""
        waited = self.nodes.get(path)
        return waited is None or time.monotonic() >= waited.retry_at

    def deadlines(self) -> list[float]:
        """When each node waited on is looked at next."""
        return [waited.retry_at for waited in self.nodes.values()]

    def stop_waiting(self, path: str) -> None:
        """Close and unregister the node at `path`, if it is waited on."""
        if (waited := self.nodes.pop(path, None)) is None:
            return
        with contextlib.suppress(KeyError, ValueError):
            self.d.selector.unregister(waited.dev.fileno)
        waited.dev.close()

    def stop_all(self) -> None:
        """Close and unregister every node waited on, for the daemon's stop."""
        for path in list(self.nodes):
            self.stop_waiting(path)

    def pump_waiting(self, path: str) -> None:
        """Read a node waited on. An event from it, the power-on report a G915 sends as it is
        switched on or the receiver's link-up notice, brings its look forward to now, at most
        once per KEYBOARD_RETRY, unless the last link notice read is a link-down; a reply does
        not. A node gone is closed."""
        waited = self.nodes[path]
        try:
            waited.dev.pump()
        except HidppFailure as exc:
            log.debug("the keyboard node %s waited on has gone: %s", path, exc)
            self.stop_waiting(path)
            return
        # Events alone: a reply reaches this handle too, the receiver's error to the loop's
        # own probe of the node among them, and queues none.
        said = down = False
        while (event := waited.dev.next_event()) is not None:
            said = True
            down = event.link_down if event.link else down
        now = time.monotonic()
        if said and not down and now >= waited.nudged_at:
            log.info("the keyboard on %s said something; setting it up", path)
            waited.nudged_at = now + KEYBOARD_RETRY
            waited.retry_at = now
