"""A Centurion headset's setting of one value, read and written whole, which each feature
module's factory builds for the layout a model's version of its feature has; the words of an
off and on setting (SWITCH); and the failure for a reply Gearbench cannot read (unreadable).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..hidpp import UnreadableAnswer
from ..wording import PERCENT_RANGE, counted, whole, word_list, written

#: The words of an off and on setting, which the window shows as a switch.
SWITCH = ("off", "on")


def unreadable(words: str, reply: bytes) -> UnreadableAnswer:
    """UnreadableAnswer saying the `words` answered `reply`, which Gearbench cannot read."""
    return UnreadableAnswer(f"the {words} answered {reply.hex(' ') or 'nothing'}, which "
                            "Gearbench cannot read")


@dataclass(frozen=True)
class Setting:
    """A setting read and written whole: its socket word, its words, its feature, the
    function that reads it and the byte of the reply that holds the value, the function that
    writes it and the bytes before the value, and the values it takes on the wire, with the
    words for them where it has words. A write that carries other bytes of the feature's has
    `kept`, the bytes of the read it sends back with the value put in at `at`. `unit` is a
    number's unit in the singular, "minute", and `never` the word for 0, "never". The
    feature's event, function 0, says the value in the read's layout."""

    key: str
    words: str
    feature: int
    read: int
    at: int
    write: int
    before: bytes
    wire: tuple[int, ...]
    names: tuple[str, ...] = ()
    kept: int = 0
    unit: str = ""
    never: str = ""

    @property
    def range(self) -> tuple[int, int]:
        return self.wire[0], self.wire[-1]

    @property
    def ranged(self) -> bool:
        """Whether it is a number that takes each whole value of its range."""
        return not self.names and self.wire == tuple(range(self.wire[0], self.wire[-1] + 1))

    @property
    def span(self) -> str:
        """The values it takes, in words: "off, night, home or tournament", "0 to 6", or "0,
        15, 30 or 60 minutes"."""
        if self.names:
            return word_list(self.names)
        low, high = self.range
        unit = f" {self.unit}s" if self.unit else ""
        return (f"{low} to {high}{unit}" if self.ranged
                else f"{word_list([str(raw) for raw in self.wire])}{unit}")

    def value(self, raw: int) -> int | str:
        """A wire value as the socket gives it: a word, or the number."""
        return self.names[self.wire.index(raw)] if self.names else raw

    def shown(self, value: int | str) -> str:
        """A value as the window says it: its word, "never" for a 0 where the setting has that
        word, or the number with its unit."""
        if isinstance(value, str):
            return value
        if value == 0 and self.never:
            return self.never
        return counted(value, self.unit) if self.unit else str(value)

    def level(self, value: int | str) -> str:
        """A value as the window's rows and `audio` give it: its word, its number with its
        unit (shown), a 0-to-100 level as a percent, "55%", or a number of its range's
        top, "3 of 6"."""
        if isinstance(value, str) or self.unit:
            return self.shown(value)
        # As the A50 Gen 4's and the G432's levels are given.
        if self.range == PERCENT_RANGE:
            return f"{value}%"
        return f"{value} of {self.range[1]}"

    def checked(self, value: object) -> int:
        """`value`, a word or a whole number the setting takes, on the wire; ValueError with
        the sentence for any other."""
        if self.names:
            if value in self.names:
                return self.wire[self.names.index(value)]
        elif whole(value) and value in self.wire:
            return value
        taken = f"a whole number from {self.span}" if self.ranged else self.span
        raise ValueError(f"the {self.words} is {taken}, not {written(value)}")

    def decoded(self, reply: bytes) -> int | str:
        """The value in a read's reply, or an event's; UnreadableAnswer for one it does not
        take."""
        if len(reply) <= self.at or reply[self.at] not in self.wire:
            raise unreadable(self.words, reply)
        return self.value(reply[self.at])

    def params(self, value: object, read: bytes) -> bytes:
        """The write's parameters for `value`, checked first (checked): the bytes before it
        and the value, or for a setting with `kept` the read's first `kept` bytes, `read`,
        with the value put in at `at`; UnreadableAnswer for a read too short for them."""
        raw = self.checked(value)
        if not self.kept:
            return self.before + bytes([raw])
        if len(read) < self.kept:
            raise UnreadableAnswer(f"the {self.words} answered {read.hex(' ') or 'nothing'}, "
                                   f"not the {counted(self.kept, 'byte')} its write sends "
                                   "back")
        sent = bytearray(read[:self.kept])
        sent[self.at] = raw
        return bytes(sent)
