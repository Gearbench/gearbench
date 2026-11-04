"""What holds a Headset tab row against a poll: a send still settling, a slider the pointer
holds down, a move its drag still owes, a write not answered yet.

A poll sent before a change would put the old value back on its row, so the tab skips a row
that is held.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable

from .worker import Pace, Settle


class Holds:
    """Its builder's sends and writes on their way, by the key it gives each row."""

    def __init__(self) -> None:
        #: The Settle of each key settled so far, which sends once its key's changes stop.
        self._settling: dict[str, Settle] = {}
        #: Writes sent and not answered yet, counted by key.
        self._unanswered: Counter[str] = Counter()
        #: Sliders the pointer holds down, which send once it lets go.
        self._pressed: set[str] = set()
        #: The pace of each slider dragged so far, which sends while the pointer drags it and
        #: a move it owes at its interval's end.
        self._paces: dict[str, Pace] = {}

    def held(self, key: str) -> bool:
        return ((key in self._settling and self._settling[key].pending) or key in self._pressed
                or (key in self._paces and self._paces[key].pending) or self.answering(key))

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
        """The pointer went down on `key`'s slider: its send still settling is dropped, and a
        move its last drag still owes (release's keep_owed) kept."""
        self._pressed.add(key)
        self._unsettle(key)

    def pressing(self, key: str) -> bool:
        return key in self._pressed

    def dragged(self, key: str, send: Callable[[], None], ms: int) -> None:
        """The pointer moved `key`'s slider: `send` now, and a later move at most once each
        `ms` (worker.Pace)."""
        self._paces.setdefault(key, Pace(send, ms)).changed()

    def release(self, key: str, keep_owed: bool = False) -> bool:
        """The pointer let go of `key`'s slider, and a move its drag still owes is dropped,
        or with `keep_owed` sent at its interval's end; False if it was not holding it."""
        if not keep_owed:
            self._end_pace(key)
        if key not in self._pressed:
            return False
        self._pressed.remove(key)
        return True

    def release_all(self) -> None:
        self._pressed.clear()

    # -- sends still settling ---------------------------------------------

    def settle(self, key: str, send: Callable[[], None], ms: int) -> None:
        """`send` once `ms` pass with no other change of `key`, in place of what `key`
        still settles or its drag owes."""
        self.cancel(key)
        # Popped by cancel and put back last, so flush sends in the order the waits began.
        self._settling[key] = settling = Settle(send)
        settling.restart(ms)

    def cancel(self, key: str) -> None:
        """Drop what `key` still settles and the move its drag owes, and end the drag's interval."""
        self._unsettle(key)
        self._end_pace(key)

    def _unsettle(self, key: str) -> None:
        held = self._settling.pop(key, None)
        if held is not None:
            held.cancel()

    def _end_pace(self, key: str) -> None:
        if key in self._paces:
            self._paces[key].cancel()

    def cancel_all(self) -> None:
        for key in [*self._settling, *self._paces]:
            self.cancel(key)

    def forget(self) -> None:
        """Drop every slider held down, send still settling, drag's interval and write
        counted, for rows made again."""
        self.cancel_all()
        self._pressed.clear()
        self._unanswered.clear()

    def flush(self, prefixes: tuple[str, ...] = ("",)) -> bool:
        """Send now what settles under a key that starts with one of `prefixes`, every key
        by default, in the order their waits began, then what a drag of such a key owes;
        True if any."""
        settled = [self._settling.pop(key).flush()
                   for key in [key for key in self._settling if key.startswith(prefixes)]]
        paced = [pace.flush() for key, pace in self._paces.items() if key.startswith(prefixes)]
        return any(settled) or any(paced)
