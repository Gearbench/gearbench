"""What holds a Headset tab row against a poll: a send still settling, a slider the pointer
holds down, a write not answered yet.

A poll sent before a change would put the old value back on its row, so the tab skips a row
that is held. This part owns the three collections, the settle timers and the paces of the
sliders a pointer drags; the A50's page, the Centurion headsets' page and the volume rows
each build one and reach them through its methods, by key.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable

from gi.repository import GLib

from .worker import Pace


class Holds:
    """Its builder's sends and writes on their way, by the key it gives each row."""

    def __init__(self) -> None:
        #: Sends waiting to settle, by key: the GLib source and the send.
        self._settling: dict[str, tuple[int, Callable[[], None]]] = {}
        #: Writes sent and not answered yet, counted by key.
        self._unanswered: Counter[str] = Counter()
        #: Sliders the pointer holds down, which send once it lets go.
        self._pressed: set[str] = set()
        #: The pace of each slider dragged so far, which sends while the pointer drags it.
        self._paces: dict[str, Pace] = {}

    def held(self, key: str) -> bool:
        return key in self._settling or key in self._pressed or self.answering(key)

    # -- writes not answered yet ------------------------------------------

    def sent(self, key: str) -> None:
        self._unanswered[key] += 1

    def answered(self, key: str) -> None:
        # Not below 0: a write sent before forget() is answered after it.
        if self._unanswered[key] > 0:
            self._unanswered[key] -= 1

    def answering(self, key: str) -> bool:
        """A write of `key` is not answered yet."""
        return self._unanswered[key] > 0

    def unchanged(self, key: str, same: bool) -> bool:
        """Nothing to send: the value is what the last reply had, and no write
        of `key` is unanswered, which may yet move the base away from it."""
        return same and not self._unanswered[key]

    # -- sliders held down ------------------------------------------------

    def press(self, key: str) -> None:
        """The pointer went down on `key`'s slider: its send still settling is dropped."""
        self._pressed.add(key)
        self.cancel(key)

    def pressing(self, key: str) -> bool:
        return key in self._pressed

    def dragged(self, key: str, send: Callable[[], None], ms: int) -> None:
        """The pointer moved `key`'s slider: `send` now, and a later move at most once each
        `ms` (worker.Pace)."""
        self._paces.setdefault(key, Pace(send, ms)).changed()

    def release(self, key: str) -> bool:
        """The pointer let go of `key`'s slider, and a move its drag still owes is dropped;
        False if it was not holding it."""
        if key in self._paces:
            self._paces[key].cancel()
        if key not in self._pressed:
            return False
        self._pressed.remove(key)
        return True

    def release_all(self) -> None:
        self._pressed.clear()

    # -- sends still settling ---------------------------------------------

    def settle(self, key: str, send: Callable[[], None], ms: int) -> None:
        self.cancel(key)
        self._settling[key] = (GLib.timeout_add(ms, self._settled, key), send)

    def _settled(self, key: str) -> bool:
        _source, send = self._settling.pop(key)
        send()
        return False

    def cancel(self, key: str) -> None:
        held = self._settling.pop(key, None)
        if held is not None:
            GLib.source_remove(held[0])

    def cancel_all(self) -> None:
        for key in list(self._settling):
            self.cancel(key)

    def forget(self) -> None:
        """Drop every slider held down, send still settling and write counted, for rows made
        again; a drag's pace (dragged) is left to release."""
        self.cancel_all()
        self._pressed.clear()
        self._unanswered.clear()

    def flush(self, prefixes: tuple[str, ...] = ("",)) -> bool:
        """Send now what settles under a key that starts with one of `prefixes`, every key
        by default, in the order their waits began, then what a drag of such a key owes;
        True if any."""
        keys = [key for key in self._settling if key.startswith(prefixes)]
        for key in keys:
            source, send = self._settling.pop(key)
            GLib.source_remove(source)
            send()
        paced = [pace.flush() for key, pace in self._paces.items() if key.startswith(prefixes)]
        return bool(keys) or any(paced)
