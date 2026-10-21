"""The Logitech G432's USB adapter: finding it, its sidetone and firmware version, and the
`g432` and `g432_sidetone` commands.

The adapter speaks HID++ on its own node, cabled, on long reports alone (gearbench.g432).
The daemon sends it only what g432.SENT names: the root lookup of 0x8300 when it attaches,
then the firmware version's reads, then 0x8300's read, and its writes. The first read the
daemon makes keeps the level found, and a reset writes it back, or SIDETONE_UNREAD_RESET where
no read found one before the first write. The node also carries the adapter's volume wheel and mute, whose
reports pump reads and drops.
"""

from __future__ import annotations

import contextlib
import logging
import selectors
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ..g432 import (
    G432,
    NO_SIDETONE,
    PRODUCTS,
    SIDETONE_RANGE,
    SIDETONE_UNREAD_RESET,
    UnreadableAnswer,
    find_g432,
    open_g432,
    sidetone_level,
)
from ..hidpp import NO_RULE, DeviceGone, HidppFailure, HidppTimeout
from ..wording import reason
from .shared import Lookout, failure_reason, hidpp_refusal, node_denied, refusal

if TYPE_CHECKING:
    from . import Daemon

log = logging.getLogger("gearbench")

#: Seconds between looks for a missing adapter, as for the A50's base station. A found
#: adapter whose lookup gets no answer costs the loop the probe's budget, so its wait
#: doubles each time, up to G432_RETRY_MAX.
G432_RETRY = 5.0
G432_RETRY_MAX = 60.0

NO_G432 = "No Logitech G432 found."
DENIED = "The G432's node cannot be opened: " + NO_RULE
#: A found adapter whose root lookup failed, with why: "no answer" for a timeout.
NOT_SET_UP = "The G432 could not be set up ({}). Unplug its USB adapter and plug it back in."
#: The reply to an answer Gearbench cannot read.
NOT_UNDERSTOOD = "The G432 gave an answer Gearbench does not understand."


def g432_refusal(exc: HidppFailure) -> dict:
    """A sidetone request's failure in words: hidpp_refusal's, but for an answer that
    could not be read."""
    if isinstance(exc, UnreadableAnswer):
        return refusal(NOT_UNDERSTOOD, exc)
    return hidpp_refusal("G432", exc)


class G432Headset:
    """The adapter while one is attached, and its sidetone level."""

    def __init__(self, daemon: Daemon) -> None:
        self.d = daemon
        self.dev: G432 | None = None
        self.path: Path | None = None
        self.product: int | None = None
        #: The level last read or written, None while unknown.
        self.level: int | None = None
        #: The firmware version read at the attach (G432.firmware), None while unknown.
        self.firmware: str | None = None
        #: The level the first read since the daemon started found, kept across a replug,
        #: and whether a write came before any read found one: a read after it finds the
        #: daemon's own level.
        self.first: int | None = None
        self._written = False
        #: When to look for an adapter again, and the node whose lookup got no answer.
        self._lookout = Lookout(G432_RETRY, G432_RETRY_MAX)
        #: A node found and refused to this user, and why a found adapter's root lookup
        #: failed, for problems(); connect clears them. The nodes a refusal was warned of,
        #: and the last lookup failure logged at info, so a retry says each once.
        self._denied: Path | None = None
        self._denied_said: set[str] = set()
        self._not_set_up: str | None = None
        self._said: str | None = None

    def commands(self) -> dict[str, Callable[[dict], dict]]:
        return {"g432": self._cmd_g432, "g432_sidetone": self._cmd_g432_sidetone}

    @property
    def name(self) -> str | None:
        """The attached adapter's name, None with none attached."""
        return None if self.product is None else PRODUCTS[self.product]

    def deadlines(self) -> list[float]:
        return [self._lookout.retry_at] if self.dev is None and self._lookout.retry_at else []

    def recheck(self) -> None:
        """After a resume: a missing adapter looked for at once."""
        self._lookout.recheck()

    # -- attaching ---------------------------------------------------------

    def maybe_connect(self) -> None:
        """Look for an adapter once the retry is due, or at once when one whose lookup got
        no answer has lost its node; the next look is timed from the end."""
        # Unplugged, as NOT_SET_UP asks: its replug is not left to the doubled wait.
        if self.dev is not None or not self._lookout.due():
            return
        try:
            self.connect()
        finally:
            self._lookout.looked()

    def connect(self) -> None:
        """Attach the adapter find_g432 finds if it answers the root lookup, and read its
        firmware version and its sidetone. Never raises."""
        self._denied = self._lookout.unanswered = self._not_set_up = None
        try:
            found = find_g432()
        except OSError as exc:
            log.debug("g432: could not look for the adapter: %s", exc)
            return
        if found is None:
            return
        path, product = found
        started = time.monotonic()
        try:
            dev = open_g432(path)
        except PermissionError as exc:
            self._denied = path
            if str(path) not in self._denied_said:
                self._denied_said.add(str(path))
                log.warning("g432: cannot open the adapter's %s: %s; %s", path, reason(exc),
                            NO_RULE)
            return
        except OSError as exc:
            log.debug("g432: adapter not reachable on %s: %s", path, exc)
            return
        self._denied_said.discard(str(path))
        try:
            feature = dev.find_sidetone()
        except HidppFailure as exc:
            dev.close()
            quiet = isinstance(exc, HidppTimeout)
            why = "no answer" if quiet else failure_reason(exc)
            self._lookout.unanswered = path if quiet else None
            # A node gone is no adapter found.
            self._not_set_up = None if isinstance(exc, DeviceGone) else why
            said = f"g432: the adapter on {path} could not be set up: {why}"
            log.log(logging.DEBUG if said == self._said else logging.INFO, "%s", said)
            self._said = said
            self.d.note_slow(started, f"G432 connect on {path}", why, repeats="g432")
            return
        self._said = None
        self.dev, self.path, self.product = dev, path, product
        self.d.selector.register(dev.fileno, selectors.EVENT_READ, "g432")
        self._read_firmware()
        if feature is None:
            log.info("g432: connected on %s; it lists no sidetone feature (0x8300)", path)
        else:
            log.info("g432: connected on %s, sidetone at feature index 0x%02x", path,
                     feature.index)
            try:
                self._read()
            except HidppFailure as exc:
                log.info("g432: its sidetone could not be read: %s", failure_reason(exc))
        self.d.forget_slow("g432")
        self.d.note_slow(started, f"G432 connect on {path}")

    def teardown(self) -> str | None:
        """Let go of the adapter, sending it nothing; what was let go, for shutdown's line,
        or None with none attached. Never raises."""
        dev, self.dev = self.dev, None
        if dev is None:
            return None
        name = cast("str", self.name)
        with contextlib.suppress(KeyError, ValueError):
            self.d.selector.unregister(dev.fileno)
        with contextlib.suppress(OSError):
            dev.close()
        self.path = self.product = self.level = self.firmware = None
        return f"{name} let go"

    def pump(self) -> None:
        """The node is readable: the volume wheel's or the mute's reports, which are dropped,
        or the node gone. Never raises. Only dispatch calls it, for the node registered while
        an adapter is attached."""
        try:
            cast("G432", self.dev).pump()
        except DeviceGone as exc:
            log.info("g432: the adapter went away: %s", failure_reason(exc))
            self.teardown()
        except HidppFailure as exc:
            log.info("g432: reading the adapter's node failed (%s); letting the adapter go",
                     failure_reason(exc))
            self.teardown()

    # -- what problems() and the refusals read -------------------------------

    def problems(self) -> list[tuple[str, str]]:
        """The adapter's entries for the daemon's `problems`, as (code, message). Nothing is
        sent to it."""
        found: list[tuple[str, str]] = []
        if self._denied is not None and node_denied(self._denied):
            found.append(("g432_no_permission", DENIED))
        if self._not_set_up is not None:
            found.append(("g432_not_set_up", NOT_SET_UP.format(self._not_set_up)))
        return found

    def missing(self) -> str:
        """Why no command can go to an adapter, with none attached."""
        return next((message for _code, message in self.problems()), NO_G432)

    def _read_firmware(self) -> None:
        """Read the firmware version into `firmware`; a read that fails is said in the
        journal and leaves it unknown."""
        try:
            self.firmware = cast("G432", self.dev).firmware()
        except HidppFailure as exc:
            log.info("g432: its firmware version could not be read: %s", failure_reason(exc))

    # -- sidetone ------------------------------------------------------------

    def _read(self) -> int:
        """Read the level; the first read before any write is kept. Raises HidppFailure."""
        level = self.level = cast("G432", self.dev).sidetone()
        if self.first is None and not self._written:
            self.first = level
            log.info("g432: sidetone at %d when first read", level)
        return level

    def _call(self, work: Callable[[], object]) -> dict | None:
        """Run `work` on the adapter: None when it went through, else the refusal. A node
        gone tears the adapter down."""
        try:
            work()
        except DeviceGone as exc:
            log.info("g432: the adapter went away: %s", failure_reason(exc))
            self.teardown()
            return g432_refusal(exc)
        except HidppFailure as exc:
            if not isinstance(exc, HidppTimeout | UnreadableAnswer):
                log.info("g432: a sidetone request failed: %s", failure_reason(exc))
            return g432_refusal(exc)
        return None

    def reply(self) -> dict:
        """`g432`'s answer from what the adapter last said."""
        dev = cast("G432", self.dev)
        return {
            "ok": True,
            "name": self.name,
            "device": str(self.path),
            "product": f"{self.product:04x}",
            # As "U1 42.00.B0001", or null where it could not be read.
            "firmware": self.firmware,
            # Whether the root lookup found 0x8300: without it `sidetone` stays null.
            "sidetone_found": dev.sidetone_feature is not None,
            "sidetone": self.level,
            "sidetone_first": self.first,
            "sidetone_range": list(SIDETONE_RANGE),
        }

    def _cmd_g432(self, request: dict) -> dict:
        """The adapter's state; with `read`, its sidetone read again first."""
        if self.dev is None:
            return {"ok": False, "error": self.missing()}
        if request.get("read") is True:
            if self.dev.sidetone_feature is None:
                return {"ok": False, "error": NO_SIDETONE}
            if (refused := self._call(self._read)) is not None:
                return refused
        return self.reply()

    def _cmd_g432_sidetone(self, request: dict) -> dict:
        """Write `level`, or with `reset` the level the first read before any write found
        (SIDETONE_UNREAD_RESET where none did), then read it back."""
        if self.dev is None:
            return {"ok": False, "error": self.missing()}
        dev = self.dev
        if request.get("reset") is True:
            level = SIDETONE_UNREAD_RESET if self.first is None else self.first
        else:
            try:
                level = sidetone_level(request.get("level"))
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
        if dev.sidetone_feature is None:
            return {"ok": False, "error": NO_SIDETONE}

        def write() -> None:
            # Before the write: a write that fails part way may still have reached it.
            self._written = True
            dev.set_sidetone(level)
            log.info("g432: sidetone set to %d", level)
            self.level = level
            self._read()

        return self._call(write) or self.reply()
