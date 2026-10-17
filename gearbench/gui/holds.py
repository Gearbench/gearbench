"""What holds a Headset tab row against a poll: a send still settling, a slider the pointer
holds down, a write not answered yet.

A poll sent before a change would put the old value back on its row, so the tab skips a row
that is held. This part owns the three collections and the settle timers; its page builds it and
reaches them through its methods, by key.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable

from gi.repository import GLib


class Holds:
    """The Headset tab's sends and writes on their way, by key: a setting, "gains:P" or
    "band_N:P" for preset P, "preset", "name", "revert", "import".

    EQ keys name their preset, so a write to a preset the headset's EQ button switched away
    from does not hold the new preset's rows.
    """

    def __init__(self) -> None:
        #: Sends waiting to settle, by key: the GLib source and the send.
        self._settling: dict[str, tuple[int, Callable[[], None]]] = {}
        #: Writes sent and not answered yet, counted by key.
        self._unanswered: Counter[str] = Counter()
        #: Sliders the pointer holds down, which send once it lets go.
        self._pressed: set[str] = set()

    def held(self, key: str) -> bool:
        return key in self._settling or key in self._pressed or self._unanswered[key] > 0

    # -- writes not answered yet ------------------------------------------

    def sent(self, key: str) -> None:
        self._unanswered[key] += 1

    def answered(self, key: str) -> None:
        self._unanswered[key] -= 1

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

    def release(self, key: str) -> bool:
        """The pointer let go of `key`'s slider; False if it was not holding it."""
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

    def flush(self, prefixes: tuple[str, ...] = ("",)) -> bool:
        """Send now what settles under a key that starts with one of `prefixes`, every key
        by default, in the order their waits began; True if any."""
        keys = [key for key in self._settling if key.startswith(prefixes)]
        for key in keys:
            source, send = self._settling.pop(key)
            GLib.source_remove(source)
            send()
        return bool(keys)
