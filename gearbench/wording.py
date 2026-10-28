"""How the package words and escapes what it says, and the JSON value checks a refusal
rests on. Standard library only, and below every other module, the device layers included.
"""

from __future__ import annotations

import json
import time
import unicodedata
from collections.abc import Collection
from pathlib import Path
from typing import TypeGuard

#: The longest refusal of a file kept whole. A refusal quotes the refused value, which can
#: be most of the file and comes out of repr() up to four times as long; the libraries keep
#: it, and the window lays it out on its main thread in time that grows with the square of
#: its length (100,000 characters 3.5 s, 200,000 14 s, measured 2026-10-12). The start
#: holds the file's name, up to 255 bytes, and the field.
MAX_REFUSAL = 1000
#: What every view says of a value the device has not given, its battery included, while
#: the device answers: "Not read" on a row, "not read" in the tray and the CLI.
NOT_READ = "not read"
#: What the window's Status dot, the CLI's note and the tray's battery line say of a device
#: that does not answer.
ASLEEP_OR_SWITCHED_OFF = "asleep or switched off"
#: The choice of no game profile, the M profile alone: the Keyboard tab's Loaded row and the
#: tray's Game profile submenu.
NO_GAME_PROFILE = "No game profile"
#: The refusal of an empty name, each kind of profile and a macro by its own name: the
#: daemon's, and the window's toast before it asks.
M_PROFILE_NEEDS_A_NAME = "an M profile needs a name"
GAME_PROFILE_NEEDS_A_NAME = "a game profile needs a name"
MACRO_NEEDS_A_NAME = "a macro needs a name"
#: What a file that could not be read, or that a pack holds and never imports, is listed
#: under: a group's title, a row's first words, `pack`'s heading.
LEFT_OUT = "Left out"
#: The heading of a refusal: the window's Details dialog, a long refusal's toast line when
#: its first clause is too long for one too (refusal_line), and the tray's notice of a
#: refused menu choice.
REFUSED_HEADING = "That did not work"
#: The reason said for a refusal or a failure that came with none of its own.
NO_REASON = "no reason given"
#: The user unit the daemon runs as (systemd/gearbench.service), and the command that shows
#: its journal.
SERVICE = "gearbench"
SERVICE_JOURNAL = f"journalctl --user -u {SERVICE}"


def service_command(verb: str) -> str:
    """The command that runs `verb` (stop, start, restart) on the daemon's unit."""
    return f"systemctl --user {verb} {SERVICE}"


#: The step after a failure only a newer daemon fixes, naming the daemon as every message
#: does.
RESTART_DAEMON = f"restart the daemon: `{service_command('restart')}`"


def both_ends(text: str, most: int = MAX_REFUSAL) -> str:
    """`text`, or, when it is longer than `most`, its start and end around an ellipsis,
    U+2026, the mark the tray cuts a line with, `most` characters in all, so a text that
    quotes a long value keeps its words on both sides of it."""
    if len(text) <= most:
        return text
    head = most // 2
    return f"{text[:head]}…{text[head + 1 - most:]}"


def reason(exc: BaseException) -> str:
    """Why `exc` was raised, in words, for a sentence that names the file or the device
    itself: an OSError's `strerror`, since its own text is "[Errno 13] Permission denied:
    '/dev/hidraw3'", the errno and the path again; any other error's text as it is. An
    OSError raised with a message alone has no strerror, and gives the message."""
    if isinstance(exc, OSError) and exc.strerror:
        return exc.strerror
    return str(exc)


def capitalized(text: str) -> str:
    """A refusal or a problem as a window, the tray or a notice shows it: the daemon and the
    command line write one as a lowercase fragment, with no final period and "; " before a
    second clause, and each place that shows one capitalizes its first letter (the owner's
    decision of 2026-10-22). A command, a daemon or a file keeps its case: "systemctl --user
    did not answer", "logind is not reachable", "config.json could not be read"."""
    first = text.split(" ", 1)[0]
    if (first in ("systemctl", "gearbench-ctl", "ratbagd", "logind", "pw-dump", "pw-cli")
            or "." in first or "/" in first):
        return text
    return text[:1].upper() + text[1:]


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


def home_shortened(text: str) -> str:
    """`text` with each path in the home folder starting ~/, as settings_files.shown_path writes
    one. The folder is found as it is, as `escaped` writes it, and as `escaped` writes it in a
    string json.dumps writes without ensure_ascii, with a quote and a backslash as escapes:
    a text escaped before it is shortened names no login. stderr writes a byte that is not
    UTF-8 as `escaped` does, backslashreplace being its errors handler."""
    home = f"{Path.home()}/"
    for spelling in (home, escaped(home), escaped(json.dumps(home, ensure_ascii=False)[1:-1])):
        text = text.replace(spelling, "~/")
    return text


def printable(name: str) -> str:
    """`name` with U+FFFD for each control, format, line or paragraph separator character
    and lone surrogate: any process can set its argv[0] and comm, and a PipeWire client its
    stream's name, to an escape sequence or a bidi override, PyGObject raises on the lone
    surrogate surrogateescape leaves for a byte in a scope's name that is not UTF-8, and
    str.splitlines breaks at Zl and Zp. Other spaces stay, so a program's name with a
    no-break space matches (apps.names_match)."""
    return "".join("\ufffd" if unicodedata.category(c) in ("Cc", "Cf", "Cs", "Zl", "Zp")
                   else c for c in name)


def quoted(name: str) -> str:
    """A saved thing's name, a macro's or a game profile's, in a reply's sentence: in double
    quotes, as the window's key search says a name, its controls printable's U+FFFD. Python's
    repr stays for a refused value of unknown shape (written)."""
    return f'"{printable(name)}"'


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


def given_count(value: object, noun: str) -> str:
    """What a refusal of a list of `noun`s says was given: "9 bands" for a list or tuple,
    "the number 5" for a number, so it does not read as a count, or written(value)."""
    if isinstance(value, (list, tuple)):
        return counted(len(value), noun)
    if number(value):
        return f"the number {value!r}"
    return written(value)


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
    """"1 macro", "2 macros", "13,210 lines"."""
    return f"{n:,} {noun}{'' if n == 1 else 's'}"


def route(plugged_by: str) -> str:
    """How a device reaches the computer, by what plugs into it, as every view says it,
    the window with a capital: "on its cable", or "through its receiver", "through its
    base station", "through its USB adapter"."""
    return "on its cable" if plugged_by == "cable" else f"through its {plugged_by}"


def route_of(product: object, cabled: Collection[int]) -> str | None:
    """The route of a keyboard or mouse, from its reply's product id, the hex the daemon
    writes, and its model's cable ids; None with no id."""
    try:
        pid = int(str(product), 16)
    except ValueError:
        return None
    return product_route(pid, cabled)


def product_route(pid: int, cabled: Collection[int]) -> str:
    """The route of a keyboard or mouse by its product id and its model's cable ids."""
    return route("cable" if pid in cabled else "receiver")


#: The range of a level every view gives as a percent, "55%"; a level of another range is
#: "{n} of {top}".
PERCENT_RANGE = (0, 100)


def hertz(freq: int) -> str:
    """An equalizer band's frequency: 500 Hz under 1000, else kHz with its fraction, 2 kHz,
    1.5 kHz."""
    return f"{freq / 1000:g} kHz" if freq >= 1000 else f"{freq} Hz"


def shares_words(game: int) -> str:
    """A Game/Voice balance as its two shares, by its Game share in whole percent, as the
    window, the CLI and the GUIDE give it: "Voice 33% · Game 67%"."""
    return f"Voice {100 - game}% · Game {game}%"


def signed(gain: float) -> str:
    """A gain as every view gives it, with its sign, a zero's too, so a column of gains
    lines up: "+3 dB", "-1.5 dB", "+0 dB"."""
    return f"{gain:+zg} dB"


def numbered(number: int, name: str) -> str:
    """An equalizer preset by its number and its name, as every view gives it: "2 · Gaming"."""
    return f"{number} · {name}"


def gain_range(low: float, high: float) -> str:
    """A range of gains as `signed` gives a gain, dB once at its end: "-7 to +7 dB"."""
    return f"{low:+zg} to {signed(high)}"


#: The end of a reading a device that no longer answers was last read with, {} its
#: clock_shown: the same in the window, the tray and the CLI.
READ_AT = ", read at {}"


def clock_shown(at: float, now: float | None = None) -> str:
    """When a reading was taken: its time, with the date before it for a reading not from
    `now`'s day, today when None. A device that stopped answering keeps its last reading
    for as long as it is attached, so the time alone can be days old."""
    then = time.localtime(at)
    today = then[:3] == time.localtime(now)[:3]
    return time.strftime("%H:%M" if today else "%Y-%m-%d %H:%M", then)
