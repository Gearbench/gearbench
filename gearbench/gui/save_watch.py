"""The Headset tab's Save on the A50 Gen 4, followed from the click to the reply that says the
base station's flash write is over."""

from __future__ import annotations

import time
from collections.abc import Callable

from gi.repository import GLib

from ..wording import capitalized

#: What the toast says once a save worked.
SAVED = "Saved to the base station"
#: How often SaveWatch asks whether a save is done, and for how long.
SAVE_POLL_MS = 500
SAVE_WAIT = 10.0


class SaveWatch:
    """A save of the base station's flash, from Save to the reply that says it is over:
    once the daemon accepts it, `ask` reads the headset from what the daemon holds every
    SAVE_POLL_MS until the save ends or SAVE_WAIT has passed; then `say` gets how it went,
    and `read` has every setting read again unless the save worked and was read back. It
    builds no widget."""

    def __init__(self, ask: Callable[[], None], say: Callable[[str], object],
                 read: Callable[[], None]) -> None:
        self._ask = ask
        self._say = say
        self._read = read
        #: headset_save is out and unanswered: a second click sends nothing.
        self.sent = False
        #: A save was accepted and its end not yet seen (take).
        self.waiting = False
        self._deadline = 0.0
        #: The fast poll's GLib source while it runs.
        self.poll: int | None = None
        #: headset_save's round trip raised: the daemon may still have run it, which the
        #: next reply says (take).
        self.unsure = False
        #: The save_id of the save waited for: a later reply with another, a replugged base's
        #: or a restarted daemon's, ends the wait as lost.
        self._save_id: object = None

    def send(self) -> bool:
        """Whether Save may send headset_save now, which marks it sent: none is out and
        unanswered, and no save it accepted is still waited for (answered, unanswered)."""
        if self.sent or self.waiting:
            return False
        self.sent = True
        return True

    def answered(self, reply: dict | None) -> None:
        """headset_save's reply: an accept waits for the save's end, and a refusal (None)
        leaves Save to be clicked again."""
        self.sent = False
        if reply is not None:
            self.unsure = False
            self._save_id = reply.get("save_id")
            self._wait()

    def unanswered(self) -> None:
        """headset_save's round trip raised. The daemon still reads a request whose client
        gave up, and answers the read sent after it: that reply says whether the save
        runs."""
        self.sent = False
        self.unsure = True

    def _wait(self) -> None:
        self.waiting = True
        self._deadline = time.monotonic() + SAVE_WAIT
        if self.poll is None:
            self.poll = GLib.timeout_add(SAVE_POLL_MS, self.tick)

    def tick(self) -> bool:
        """The fast poll: `ask`, until SAVE_WAIT has passed."""
        if time.monotonic() >= self._deadline:
            # Past the wait the five second poll sees the end.
            self.poll = None
            return False
        self._ask()
        return True

    def take(self, info: dict, saving: bool) -> None:
        """A `headset` reply, `saving` what it says of a save running."""
        if self.unsure:
            self.unsure = False
            if saving:
                self._save_id = info.get("save_id")
                self._wait()
            return
        if not self.waiting:
            return
        if info.get("save_id") != self._save_id:
            # The base or the daemon went between two replies, and the save with it.
            self.lost()
            return
        if saving:
            return
        self.stop()
        error = info.get("save_error")
        self._say(capitalized(str(error)) if error else SAVED)
        # A save that worked was read back before this reply, which then lists nothing
        # unsaved; one that failed, or whose read-back was cut short, owes the read.
        if error or info.get("unsaved"):
            self._read()

    def stop(self) -> None:
        """End the wait and its fast poll."""
        self.waiting = False
        if self.poll is not None:
            GLib.source_remove(self.poll)
            self.poll = None

    def lost(self) -> None:
        """The base or the daemon went, and the save with it: its end is unknown, so
        nothing is said."""
        self.stop()
        self.unsure = False
