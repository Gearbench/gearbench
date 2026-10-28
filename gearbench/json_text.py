"""The JSON value a file's bytes hold, or why they hold none Gearbench reads, on the standard
library and `wording` alone ("The file rules")."""

from __future__ import annotations

import json

from .wording import both_ends

#: A codec's reason in words where Python gives it in its own notation: a UTF-32 unit past
#: U+10FFFF.
CODEC_WORDS = {"code point not in range(0x110000)": "a value past Unicode's last character"}
#: The words, after a path or a name, for what json.loads builds or raises past JSON's own
#: errors: nesting deeper than the stack walks, and an integer literal longer than Python
#: converts (sys.get_int_max_str_digits, 4300 by default).
TOO_DEEP = "nested too deeply to read"
NUMBER_TOO_LONG = "holds a number too long to read"


class JsonRefused(ValueError):
    """Why a file's bytes are no JSON value Gearbench reads, in words for after its path."""


def one_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """A JSON object from its pairs (json.loads' object_pairs_hook), refusing a name given
    twice: json.loads would keep the last, and the next save would write that one alone over
    the hand edit that gave both."""
    found: dict[str, object] = {}
    for key, value in pairs:
        if key in found:
            raise JsonRefused(f"{both_ends(repr(key))} is named twice in one object")
        found[key] = value
    return found


def codec_words(exc: UnicodeDecodeError, data: bytes | bytearray) -> str:
    """Why json.loads could not decode `data`, in words: the codec it chose by the first
    bytes, as UTF-16 or UTF-32 cut short is not a UTF-8 problem, and the byte it failed at."""
    # utf-8-sig decodes the bytes after its BOM, so the offset is counted back from the end
    # of what it was given.
    at = exc.start + len(data) - len(exc.object)
    why = CODEC_WORDS.get(exc.reason, exc.reason)
    return f"not {exc.encoding.upper()} text ({why} at byte {at})"


def json_value(data: bytes, one_name: bool = True) -> object:
    """The JSON value `data` holds, or JsonRefused.

    json.loads tells UTF-8, UTF-16 and UTF-32 apart by the first bytes (Windows PowerShell
    5's `>` writes UTF-16). While `one_name`, an object that names a key twice is refused
    (one_object); without it the last is kept, as json.loads keeps it, for a file another
    program wrote and Gearbench never writes back.
    """
    try:
        return json.loads(data, object_pairs_hook=one_object if one_name else None)
    except JsonRefused:
        raise
    except json.JSONDecodeError as exc:
        raise JsonRefused(f"invalid JSON at line {exc.lineno}: {exc.msg}") from None
    except UnicodeDecodeError as exc:
        raise JsonRefused(codec_words(exc, data)) from None
    except RecursionError:
        raise JsonRefused(TOO_DEEP) from None
    except ValueError:
        # What json.loads raises that is none of the above: an integer literal too long.
        raise JsonRefused(NUMBER_TOO_LONG) from None
