"""How the package words and escapes what it says: a refusal's quoted value and its two
ends, an error's reason, a program's end, a count, a list of names, a clock time, text made
safe for a terminal or a window, and the JSON value checks a refusal rests on. Standard
library only, and below every other module, the device layers included.
"""

from __future__ import annotations

import json
import time
import unicodedata
from typing import TypeGuard

#: The longest refusal of a file kept whole. A refusal quotes the refused value, which can
#: be most of the file and comes out of repr() up to four times as long; the libraries keep
#: it, and the window lays it out on its main thread in time that grows with the square of
#: its length (100,000 characters 3.5 s, 200,000 14 s, measured 2026-10-12). The start
#: holds the file's name, up to 255 bytes, and the field.
MAX_REFUSAL = 1000


def both_ends(text: str, most: int = MAX_REFUSAL) -> str:
    """`text`, or, when it is longer than `most`, its start and end around "...", `most`
    characters in all: the start names the file and the field, the end says what is
    wrong."""
    if len(text) <= most:
        return text
    head = most // 2
    return f"{text[:head]}...{text[head + 3 - most:]}"


def reason(exc: BaseException) -> str:
    """Why `exc` was raised, in words, for a sentence that names the file or the device
    itself: an OSError's `strerror`, since its own text is "[Errno 13] Permission denied:
    '/dev/hidraw3'", the errno and the path again; any other error's text as it is. An
    OSError raised with a message alone has no strerror, and gives the message."""
    if isinstance(exc, OSError) and exc.strerror:
        return exc.strerror
    return str(exc)


def ended(program: str, returncode: int) -> str:
    """How `program` ended, for a run that failed and said nothing. subprocess gives a child
    that a signal ended the signal's number below zero, which is no exit status."""
    if returncode < 0:
        return f"{program} was killed by signal {-returncode}"
    return f"{program} exited {returncode}"


def escaped(value: object) -> str:
    """`value` with each character a terminal would act on or not show, bidi overrides
    (Cf) included, written as its escape: names from devices, files, processes and
    PipeWire can hold an escape sequence. --json needs none: json.dumps escapes them."""
    return "".join(c if c.isprintable() else repr(c)[1:-1] for c in str(value))


def printable(name: str) -> str:
    """`name` with U+FFFD for each control, format, line or paragraph separator character
    and lone surrogate: any process can set its argv[0] and comm, and a PipeWire client its
    stream's name, to an escape sequence or a bidi override, PyGObject raises on the lone
    surrogate surrogateescape leaves for a byte in a scope's name that is not UTF-8, and
    str.splitlines breaks at Zl and Zp. Other spaces stay, so a program's name with a
    no-break space matches (apps.names_match)."""
    return "".join("\ufffd" if unicodedata.category(c) in ("Cc", "Cf", "Cs", "Zl", "Zp")
                   else c for c in name)


def whole(value: object) -> TypeGuard[int]:
    """Whether `value` is a whole number in JSON: 800.0 is in [800], and
    true is 1."""
    return isinstance(value, int) and not isinstance(value, bool)


def number(value: object) -> TypeGuard[int | float]:
    """Whether `value` is a number in JSON, whole or not: true and false are not."""
    return isinstance(value, int | float) and not isinstance(value, bool)


#: What json.loads returns other than null, true and false, by JSON's names for it,
#: for a refusal.
JSON_KINDS = {int: "a number", float: "a number", str: "text", list: "a list",
              dict: "an object"}


def written(value: object) -> str:
    """`value` as a refusal quotes it: null, true or false as the file writes them, a
    list or an object by its kind, since one can run to the file's megabyte, and a
    number or text by repr()."""
    if value is None or isinstance(value, (bool, list, dict)):
        return kind_of(value)
    return repr(value)


def kind_of(value: object) -> str:
    """What `value` is, as a refusal names it: null, true or false as the file writes
    them, or the kind of value."""
    if value is None or isinstance(value, bool):
        return json.dumps(value)
    return JSON_KINDS.get(type(value), type(value).__name__)


#: Characters that turn the drawing direction of the text after them (embeddings, overrides,
#: isolates, and the characters that end them).
DIRECTION_TURNS = frozenset(map(chr, (*range(0x202A, 0x202F), *range(0x2066, 0x206A))))


def turns_replaced(text: str) -> str:
    """`text` with U+FFFD for each line or paragraph separator (Zl, Zp) and each of
    DIRECTION_TURNS. GTK starts a new line at a separator and draws the rest of a line in
    another order after a direction turn. Each is 3 bytes in UTF-8, as U+FFFD is."""
    return "".join("\ufffd" if c in DIRECTION_TURNS or unicodedata.category(c) in ("Zl", "Zp")
                   else c for c in text)


def without_controls(text: str) -> str:
    """`text` less its control characters (Cc), "?" for a lone surrogate, which a JSON
    escape can put in the file and GTK and D-Bus cannot take, and turns_replaced. A
    control character starts a terminal escape sequence, and the game profile and macro
    files refuse it. A separator could add lines to the preview, a macro's steps among
    them, and a direction turn could draw the preview's warning around a program's name in
    another order. U+FFFD is what the daemon makes of either in a running program's name,
    so a program name still matches."""
    text = text.encode("utf-8", "replace").decode("utf-8")
    return turns_replaced("".join(c for c in text if unicodedata.category(c) != "Cc"))


def word_list(names: tuple[str, ...] | list[str], last: str = "or") -> str:
    """"a, b or c", for a refusal that names the choices; `last` "and" for
    "a, b and c"."""
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} {last} {names[-1]}"


def first_of(names: list[str], shown: int) -> str:
    """"g305, g403": the first `shown` of `names`, then how many more, "g305, g403 and 380
    more"."""
    more = len(names) - shown
    return ", ".join(names[:shown]) + (f" and {more} more" if more > 0 else "")


def counted(n: int, noun: str) -> str:
    """"1 macro", "2 macros"."""
    return f"{n} {noun}{'' if n == 1 else 's'}"


def clock_shown(at: float, now: float | None = None) -> str:
    """When a reading was taken: its time, with the date before it for a reading not from
    `now`'s day, today when None. A device that stopped answering keeps its last reading
    for as long as it is attached, so the time alone can be days old."""
    then = time.localtime(at)
    today = then[:3] == time.localtime(now)[:3]
    return time.strftime("%H:%M" if today else "%Y-%m-%d %H:%M", then)
