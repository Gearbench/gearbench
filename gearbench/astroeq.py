"""Astro Command Center's .astroeq preset files, Qt INI read as QSettings reads it, into an
astro.EqPreset. Standard library only.
"""

from __future__ import annotations

import configparser
import os
import re

from .astro import BANDS, FREQ_RANGE, GAIN_RANGE, SHELVES, WIDTH_RANGE, Band, EqPreset, preset_name
from .settings_files import ConfigError, read_capped, unprefixed
from .wording import both_ends, turns_replaced

# How Qt 5's QSettings writes a string: quoted when it holds , ; = or an edge
# space, \" and \\ escaped, non-ASCII as UTF-16 \xNNNN units, a leading @
# doubled.
_QT_ESCAPE = re.compile(r'\\(?:x([0-9A-Fa-f]+)|([0-7]+)|(.?))|"([ \t]*)', re.DOTALL)
_QT_CHARS = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v",
             '"': '"', "?": "?", "'": "'", "\\": "\\"}


def _qt_string(raw: str) -> str:
    """A string value as QSettings reads it back."""
    quoted = False

    def one(match: re.Match[str]) -> str:
        nonlocal quoted
        hexa, octal, char, spaces = match.groups()
        if hexa is not None:
            return chr(int(hexa, 16) & 0xFFFF)
        if octal is not None:
            return chr(int(octal, 8) & 0xFFFF)
        if char is not None:
            # Qt drops an unknown escape, and a backslash that ends the value ("").
            return _QT_CHARS.get(char, "")
        # A quote, dropped; Qt skips the spaces and tabs after a closing one.
        quoted = not quoted
        return spaces if quoted else ""

    text = _QT_ESCAPE.sub(one, raw)
    text = text.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")
    return text[1:] if text.startswith("@@") else text


_INI_PROBLEMS = (
    (configparser.DuplicateOptionError, "repeats a key"),
    (configparser.DuplicateSectionError, "repeats a section"),
    (configparser.MissingSectionHeaderError, "comes before any [section]"),
)


def read_astroeq(text: str) -> EqPreset:
    """Parse an .astroeq file: Qt INI, a BOM and CRLF are fine. Raises
    ValueError naming the line that is not INI, or the key that is missing
    or whose value is refused."""
    # Qt has no DEFAULT section, whose keys configparser lends every other section: no
    # header line can name "\n".
    parser = configparser.ConfigParser(interpolation=None, delimiters=("=",), default_section="\n")
    # Qt's keys are case-sensitive. The ignore: typeshed has a method here, and
    # configparser's documentation assigns to it.
    parser.optionxform = str  # type: ignore[method-assign, assignment]
    try:
        parser.read_string(text.removeprefix("\ufeff"))
    except configparser.Error as exc:
        # configparser's own text names the source '<string>' and runs over several lines.
        errors = getattr(exc, "errors", None)
        line = getattr(exc, "lineno", None) or (errors[0][0] if errors else "?")
        what = next((w for kind, w in _INI_PROBLEMS if isinstance(exc, kind)), "is not key=value")
        raise ValueError(f"not an .astroeq file: line {line} {what}") from None

    def value(section: str, key: str) -> str:
        if not parser.has_option(section, key):
            raise ValueError(f"the file has no {key} in [{section}]")
        return parser.get(section, key).strip()

    def number[N: (int, float)](key: str, kind: type[N], low: float, high: float) -> N:
        raw = value("Eq_Bands", key)
        out_of_range = f"{key} is {low} to {high}, not {raw}"
        try:
            got = kind(raw)
        except ValueError:
            # int() also refuses a run of more digits than sys.get_int_max_str_digits(),
            # 4,300 by default; said as out of range, which it is unless zero-padded.
            if re.fullmatch(r"[+-]?\d+", raw):
                raise ValueError(out_of_range) from None
            raise ValueError(f"{key} is {raw!r}, "
                             f"not a {'whole ' if kind is int else ''}number") from None
        if not low <= got <= high:
            raise ValueError(out_of_range)
        return got

    # A shared file's name must not turn the text around it or break it into lines, as the
    # G HUB import has it (wording.without_controls).
    name = turns_replaced(_qt_string(value("General", "Name")))
    preset_name(name, "Name")
    # Read in this order, all gains, then all frequencies, then the widths, so a file
    # short of several keys is refused for the first of them.
    gains = [number(f"Eq_Band_{b}", int, *GAIN_RANGE) for b in BANDS]
    freqs = [number(f"Eq_Freq_Band_{b}", int, *FREQ_RANGE) for b in BANDS]
    widths = {b: number(f"Eq_Bandwidth_{b}", float, *WIDTH_RANGE)
              for b in BANDS if b not in SHELVES}
    return EqPreset.of_bands(name, gains, [Band(freq, widths.get(b))
                                           for b, freq in zip(BANDS, freqs, strict=True)])


#: The largest .astroeq file read. Command Center's own are under 300 bytes.
#: configparser's work on a line without "=" grows with the square of a run of white
#: space inside it, and on a run of bad lines with the square of their count.
ASTROEQ_MAX = 16 << 10


def load_astroeq(path: str | os.PathLike[str]) -> EqPreset:
    """`read_astroeq` on a file. Raises OSError when it cannot be opened, and
    ValueError for anything but a regular file (settings_files.read_capped) of at most
    ASTROEQ_MAX bytes."""
    try:
        data = read_capped(path, ASTROEQ_MAX)
    except ConfigError as exc:
        raise ValueError(f"not an .astroeq file: it is {unprefixed(str(exc), path)}") from None
    try:
        decoded = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"not an .astroeq file: it is not UTF-8 text "
                         f"({exc.reason} at byte {exc.start})") from None
    try:
        return read_astroeq(decoded)
    except ValueError as exc:
        # The message repeats the refused name or value, which can be most of
        # the file; the window's dialog or a stderr line gets both ends.
        raise ValueError(both_ends(str(exc), 200)) from None
