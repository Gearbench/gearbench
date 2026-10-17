"""Configuration for Gearbench: ~/.config/gearbench/config.json.

Three profiles, one per M key, each with its G-key bindings, a shift layer held on
MR, its lighting, and bindings for the mouse's G4 to G9. A binding is a key spec
like "ctrl+f13" or `macro:<name>`; a mouse button also takes a built-in action.
G keys ship unbound, so nothing sends keystrokes the user did not ask for. JSON,
since the daemon writes this file and the standard library only reads TOML.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import string
import tempfile
import time
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeGuard, cast

from .lighting import ANIMATIONS, WAVE_DIRECTIONS, default_speed, speed_range
from .uinput import UinputError, parse_binding

log = logging.getLogger("gearbench")

VERSION = 1
G_KEYS = ("G1", "G2", "G3", "G4", "G5")
PROFILE_IDS = (1, 2, 3)          # one per M key

#: The two lighting modes, of M and game profiles alike: one color for the whole
#: board, or the per-key layout over a background. profiles.py imports and
#: re-exports them.
STATIC, PER_KEY = "static", "perkey"
MODES = (STATIC, PER_KEY)
#: A lighting layer's Static color and per-key background where its file has none.
DEFAULT_COLOR, DEFAULT_BACKGROUND = 0x0080FF, 0x000000

#: The mouse's programmable buttons, by Logitech name, in display order.
MOUSE_BUTTONS = ("g4", "g5", "g6", "g7", "g8", "g9")
#: What each one is on the mouse, for the CLI and the window.
MOUSE_LABELS = {
    "g4": "G4 (side, rear)", "g5": "G5 (side, front)", "g6": "G6 (paddle)",
    "g7": "G7 (DPI down)", "g8": "G8 (DPI up)", "g9": "G9 (behind the wheel)",
}
#: Built-in actions a mouse button can be bound to instead of a key spec.
#: back and forward hold the virtual mouse's buttons uinput.POINTER_ACTIONS names;
#: dpi-shift drops to the profile's shift stage while held.
MOUSE_ACTIONS = ("none", "back", "forward", "dpi-shift", "dpi-up", "dpi-down")
#: What the factory profile did with G4 to G8; G9, a profile cycle there,
#: does nothing.
MOUSE_DEFAULTS = {"g4": "back", "g5": "forward", "g6": "dpi-shift",
                  "g7": "dpi-down", "g8": "dpi-up", "g9": "none"}

#: Idle dimming, the owner's decision of 2026-09-23, G HUB's values: the
#: keyboard dims to half this many seconds after its last keypress and goes
#: dark IDLE_OFF_AFTER seconds after that; a keypress brings it back.
IDLE_DIM_AFTER, IDLE_OFF_AFTER = 60, 300
#: The seconds either time may be set to. The firmware keeps up to 65535
#: units of 1000/1024 s; an hour is plenty for a setting in the window.
IDLE_RANGE = (10, 3600)

#: Prefix of a binding that names a macro, on a G key or a mouse button (macros.py).
MACRO_PREFIX = "macro:"
#: The most read_json takes from one file. What Gearbench writes is a few
#: kilobytes, so anything past this is not one of its files.
MAX_JSON_BYTES = 1 << 20


def config_home() -> Path:
    """XDG_CONFIG_HOME, or ~/.config when it is unset, empty or relative, as the XDG
    spec has it. systemd's %E ignores a relative value too, but it is the user
    manager's own XDG_CONFIG_HOME, which environment.d does not change: the unit makes
    and opens %E/gearbench, and the daemon logs an error at start when that is not
    config_dir() (cli.service.cmd_daemon)."""
    base = Path(os.environ.get("XDG_CONFIG_HOME") or "")
    return base if base.is_absolute() else Path.home() / ".config"


def runtime_home(environ: Mapping[str, str] = os.environ) -> Path | None:
    """XDG_RUNTIME_DIR, or None when it is unset, empty or relative, which the XDG spec
    says to ignore: a relative one is a folder under the working one, where, in /tmp or a
    shared checkout, another account can listen in the daemon's place."""
    base = Path(environ.get("XDG_RUNTIME_DIR") or "")
    return base if base.is_absolute() else None


def config_dir() -> Path:
    return config_home() / "gearbench"


def shown_path(path: Path) -> str:
    """`path` as the window names it, the home folder written ~."""
    try:
        return f"~/{path.relative_to(Path.home())}"
    except ValueError:
        return str(path)


def shown_config_dir() -> str:
    """config_dir() as the window names it, the home folder written ~."""
    return shown_path(config_dir())


def config_path() -> Path:
    return config_dir() / "config.json"


def file_stamp(path: Path) -> tuple[int, int, int, int] | None:
    """A file version's identity: inode, size, mtime and ctime, or None if stat fails, as
    for a config directory that is a file.

    Compared with the stamp from the daemon's last read or write, it says whether
    someone else wrote the file since: an atomic replace changes the inode, an edit
    in place the times. ctime is there because nothing can set it (`cp -p` and
    `touch -r` restore mtime).
    """
    try:
        info = path.stat()
    except OSError:
        return None
    return info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def link_to_nothing(path: Path) -> Path | None:
    """The first of `path` and its folders that is a link to nothing, or None: a file or
    folder missing behind one is somewhere, in a dotfiles folder not mounted say, and must
    not read as never made."""
    return next((place for place in (path, *path.parents)
                 if place.is_symlink() and not place.exists()), None)


def missing_behind(target: Path, place: Path) -> str:
    """Why `target` cannot be read or written: `place`, it or a folder above it, is a
    link to nothing (link_to_nothing)."""
    link = "a link" if place == target else f"{place} is a link"
    return f"{target}: {link} to {os.readlink(place)}, which is not there"


def changed_on_disk(path: Path, save: bool = True) -> ConfigError:
    """The refusal for a file edited since it was read. `save` False is a macro's file,
    which a delete leaves too, and leaves out `gearbench-ctl save`, which writes no
    macro: the daemon holds no copy of one."""
    kept = "nothing is saved over it" if save else "it is not saved over or deleted"
    return FileChanged(
        f"{path} changed on disk since the daemon read it; {kept}. "
        f"`gearbench-ctl reload` takes your edit"
        + ("; `gearbench-ctl save` writes the daemon's copy over it." if save else ".")
    )


def write_json(target: Path, payload: dict, prefix: str) -> None:
    """Write `payload` to `target` atomically, through a temporary file.

    A symlink is written through: replacing the link would cut off the file the read
    follows (read_capped). The temporary sits in that file's directory, so the replace
    stays on one filesystem, and `prefix` names a leftover after its caller. The
    directory is synced after the replace, or a power loss could bring the old file back
    (fsync(2)); a sync that fails is a warning, since the file is already in place. It is
    opened before anything is written, so a folder that cannot be
    opened refuses the save with the old file in place, and one moved away after the
    replace is still the one synced. O_DIRECTORY refuses a file or a named pipe at its
    path, where a pipe would block. Text over MAX_JSON_BYTES is refused before anything
    is written: read_json would not read it back. The new file is its owner's alone,
    whatever the old file's mode or the umask: mkstemp makes the temporary 0600, which a
    umask cannot widen, and a macro's text step can hold a password.
    """
    # ASCII (ensure_ascii), so its length is its size in bytes.
    text = json.dumps(payload, indent=2) + "\n"
    if len(text) > MAX_JSON_BYTES:
        raise ConfigError(f"{target.name}: {len(text)} bytes is more than the "
                          f"{MAX_JSON_BYTES} a file may hold; nothing is saved")
    target = target.resolve()
    directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        replace_file(target, text.encode("ascii"), prefix, ".json")
        # Raised, the caller would undo a change the file holds and keep the stamp of the
        # file it replaced, so that its next save is refused as a hand edit.
        try:
            os.fsync(directory)
        except OSError as exc:
            log.warning("%s is saved, but its folder could not be synced (%s): a power loss "
                        "may bring the old file back", escaped(target), reason(exc))
    finally:
        os.close(directory)


def replace_file(target: Path, data: bytes, prefix: str, suffix: str) -> None:
    """Put `data` in a temporary beside `target`, on disk (fsync), then rename it over
    `target`, so a full disk or a power loss leaves the old file or the new one whole. A
    caller whose file must outlast a power loss syncs the directory. On any error the
    temporary is removed."""
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=prefix, suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def sync_directory(directory: Path) -> None:
    """Put a change to `directory`'s entries, a replace or an unlink, on disk: until
    then a power loss can undo it (fsync(2)). Opened as a directory (O_DIRECTORY), so a
    file or a named pipe put at its path since the change is refused at the open, where
    a pipe would block."""
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def sync_removal(removed: Path) -> None:
    """sync_directory after `removed` was unlinked. A sync that fails is a warning, as in
    write_json: the file is already gone, and raised, the daemon would answer a delete or a
    rename that happened as refused and go on naming the file that is gone."""
    try:
        sync_directory(removed.parent)
    except OSError as exc:
        log.warning("%s is removed, but its folder could not be synced (%s): a power loss "
                    "may bring it back", escaped(removed), reason(exc))


class ConfigError(ValueError):
    #: The file problem, for a front end that shows it in a banner; None for a bad value.
    kind: str | None = None


class FileChanged(ConfigError):
    """A file edited on disk since the daemon read it, which is not written over."""

    kind = "config_changed"


class FileUnreadable(ConfigError):
    """config.json or the loaded game profile's file could not be read, so nothing is
    written over it."""

    kind = "config_unreadable"


def read_capped(path: Path, limit: int) -> bytes:
    """A regular file's bytes, or a ConfigError naming it if it is anything else or
    holds more than `limit` bytes.

    The daemon reads its libraries on its one thread, where a FIFO would block it for
    good and a link to /dev/zero grow it until killed. The open does not block and the
    type is checked on the open file, so a symlink to a regular file still reads. Run as
    the unit, the daemon leads its session with no terminal, so a terminal no session
    holds, opened without O_NOCTTY, would become its own, and a ^C byte or a hangup on it
    would stop the daemon.
    OSError passes through.
    """
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOCTTY)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ConfigError(f"{path}: not a regular file")
        # Joined once at the end: bytes += would copy all read so far at each short read.
        chunks: list[bytes] = []
        size = 0
        while size <= limit and (chunk := os.read(fd, limit + 1 - size)):
            chunks.append(chunk)
            size += len(chunk)
    finally:
        os.close(fd)
    if size > limit:
        raise ConfigError(f"{path}: larger than {limit} bytes")
    return b"".join(chunks)


def read_json(path: Path) -> object:
    """A JSON file's contents, or a ConfigError naming the file.

    Read as bytes: json.loads tells UTF-8, UTF-16 and UTF-32 apart (Windows
    PowerShell 5's `>` writes UTF-16), and anything else is a ConfigError, where
    read_text() would raise a UnicodeDecodeError no caller expects. So is a file
    nested too deeply, not regular, or over MAX_JSON_BYTES. OSError passes through,
    so a caller can tell a missing file from a bad one.
    """
    data = read_capped(path, MAX_JSON_BYTES)
    try:
        return json.loads(data)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: invalid JSON at line {exc.lineno}: {exc.msg}") from None
    except UnicodeDecodeError as exc:
        # The codec json.loads chose by the first bytes: UTF-16 or UTF-32 cut short is not
        # a UTF-8 problem. utf-8-sig decodes the bytes after its BOM, so the offset is
        # counted back from the end of what it was given.
        at = exc.start + len(data) - len(exc.object)
        raise ConfigError(f"{path}: not {exc.encoding.upper()} text "
                          f"({exc.reason} at byte {at})") from None
    except RecursionError:
        raise ConfigError(f"{path}: nested too deeply to read") from None
    except ValueError:
        # What json.loads raises that is neither of the above: an integer literal
        # longer than Python converts (sys.get_int_max_str_digits, 4300 by default).
        raise ConfigError(f"{path}: holds a number too long to read") from None


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


def from_file[T](path: Path, parse: Callable[[object], T]) -> T:
    """`parse` over one file's JSON, with every failure a ConfigError.

    A bad file must be a ConfigError naming it, so a library skips it and keeps the
    rest, and the daemon starts on the defaults instead of crash-looping. A TypeError,
    AttributeError or ValueError from a shape `parse` does not check is the same kind
    of bad file. A refusal longer than MAX_REFUSAL is cut to its two ends (both_ends).
    """
    raw = read_json(path)
    try:
        return parse(raw)
    except ConfigError as exc:
        if len(str(exc)) <= MAX_REFUSAL:
            raise
        raise ConfigError(both_ends(str(exc))) from None
    except (TypeError, AttributeError, ValueError) as exc:
        raise ConfigError(both_ends(f"{path}: {exc}")) from exc
    except RecursionError:
        # json.loads accepts nesting deeper than str() and repr() can walk,
        # so a value parse only turns into text can still be too deep.
        raise ConfigError(f"{path}: nested too deeply to read") from None


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


def unprefixed(why: str, path: Path) -> str:
    """`why` without the `<path>: ` that read_json, from_file and Config.load put in front
    of an error, for a message that names the file already."""
    return why.removeprefix(f"{path}: ")


def unusable(exc: BaseException) -> str:
    """Why config.json would not load, without its path, for a message that names the file:
    Config.load starts each of its errors with the path, and an OSError from the open quotes
    it after the reason."""
    return unprefixed(reason(exc), config_path())


def unread_reason(name: str, why: str) -> str:
    """A skipped file's reason without the file's name or path in front, for a line or a
    row that names the file itself: `<path>/doom.json: invalid JSON…` and
    `doom.json: version 99…` lose it, `doom.json.mode: …` keeps the field. A folder's
    entry (library_files.read_folder), which gives the folder's path in words, is left whole."""
    for prefix in (f"{name}: ", f"{name}."):
        head, found, rest = why.partition(prefix)
        if found and (not head or head.endswith("/")):
            return rest
    return why


class SkipWarnings:
    """Say once that a library is skipping a file, and again only if it changes or
    has been read (`read`) since.

    The daemon reads the game-profile library every five seconds and the macro
    library at a press of a macro-bound key, so a warning per read floods the log.
    """

    def __init__(self, what: str) -> None:
        self.what = what
        self._said: dict[Path, tuple[int | None, str]] = {}

    def skip(self, path: Path, exc: Exception, *, folder: bool = False) -> None:
        """`folder`: `path` is the library's own folder, which could not be listed."""
        try:
            stamp: int | None = path.stat().st_mtime_ns
        except OSError:
            stamp = None
        said = (stamp, str(exc))
        if self._said.get(path) != said:
            self._said[path] = said
            # The name and the error come from a file anyone who can name one
            # chooses, and the CLI prints this line to a terminal.
            if folder:
                log.warning("cannot list %s: %s", escaped(path), escaped(reason(exc)))
            else:
                log.warning("skipping %s %s: %s", self.what, escaped(path.name),
                            escaped(unread_reason(path.name, reason(exc))))

    def read(self, path: Path) -> None:
        """`path` was read, so its next skip is news even with the same time and error."""
        self._said.pop(path, None)


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


def version_problem(data: dict, expected: int) -> str | None:
    """Why a file's `version` is not `expected`, in words, or None; a file without one is
    `expected`."""
    version = data.get("version", expected)
    # written() gives a list or an object by its kind, which "version ... is not supported"
    # can't hold.
    if isinstance(version, (list, dict)):
        return f"version must be {expected}, not {written(version)}"
    # true == 1 and 1.0 == 1, so the type is checked first.
    if not whole(version) or version != expected:
        return f"version {written(version)} is not supported (expected {expected})"
    return None


def idle_problem(value: object) -> str | None:
    """Why `value` is not a whole number of seconds in IDLE_RANGE, or None."""
    low, high = IDLE_RANGE
    if not whole(value) or not low <= value <= high:
        return f"must be a whole number of seconds from {low} to {high}, not {written(value)}"
    return None


def idle_seconds(value: object, where: str) -> int:
    """An idle time from the file, or ConfigError naming the field."""
    problem = idle_problem(value)
    if problem is not None:
        raise ConfigError(f"{where} {problem}")
    # A whole number: idle_problem found nothing wrong with it.
    return cast(int, value)


def flag(data: dict, name: str, default: bool) -> bool:
    """A true-or-false field of the file, or ConfigError naming it."""
    value = data.get(name, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{name} must be true or false, not {written(value)}")
    return value


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


def expect(value: object, kind: type, where: str, what: str) -> None:
    """Raise ConfigError unless `value` is a `kind`, naming what was found (kind_of)."""
    if not isinstance(value, kind):
        raise ConfigError(f"{where}: must be {what}, not {kind_of(value)}")


#: Characters that turn the drawing direction of the text after them (embeddings, overrides,
#: isolates, and the characters that end them).
DIRECTION_TURNS = frozenset(map(chr, (*range(0x202A, 0x202F), *range(0x2066, 0x206A))))


def without_controls(text: str) -> str:
    """`text` less its control characters (Cc), "?" for a lone surrogate, which a JSON
    escape can put in the file and GTK and D-Bus cannot take, and U+FFFD for a line or
    paragraph separator (Zl, Zp) and for each of DIRECTION_TURNS. A control character
    starts a terminal escape sequence, and the game profile and macro files refuse it. GTK
    starts a new line at a separator, so a name could add lines to the preview, a macro's
    steps among them, and draws the rest of a line in another order after a direction turn,
    the preview's warning around a program's name included. U+FFFD is what the daemon
    makes of either in a running program's name, so a program name still matches."""
    text = text.encode("utf-8", "replace").decode("utf-8")
    return "".join("\ufffd" if c in DIRECTION_TURNS or unicodedata.category(c) in ("Zl", "Zp")
                   else c for c in text if unicodedata.category(c) != "Cc")


def refuse_controls(text: str, where: str) -> None:
    """Raise ConfigError if `text` holds a control character (Cc) or a lone surrogate (Cs).

    Names and bindings are printed to terminals, where a control character can start
    an escape sequence that hides or rewrites output. A lone surrogate, from a JSON
    escape, is not UTF-8: PyGObject raises on it, and the window could not open. A refusal quotes
    `text` cut to its two ends (both_ends): a caller can measure a name without the white
    space around it, which can be most of a request."""
    for c in text:
        category = unicodedata.category(c)
        if category == "Cc":
            raise ConfigError(f"{where}: {both_ends(repr(text))} holds a control character")
        if category == "Cs":
            raise ConfigError(f"{where}: {both_ends(repr(text))} is not valid text")


#: The most characters a game profile, macro or M profile name may hold. A row wraps a
#: name with no space in time that grows with its square (40,000 characters: 29 s,
#: measured 2026-10-12). Room above the G HUB import's 256-character cut
#: (ghub.MAX_NAME_CHARS) for the " (N)" an import adds.
MAX_NAME_LENGTH = 1000


def refuse_name(text: str, where: str) -> None:
    """refuse_controls for a name, length first, so a refusal never quotes a long name."""
    if len(text) > MAX_NAME_LENGTH:
        raise ConfigError(f"{where}: {len(text)} characters is more than the "
                          f"{MAX_NAME_LENGTH} a name may hold")
    refuse_controls(text, where)


def parse_color(value: object, where: str) -> int:
    """A color from a file or a command: '#rrggbb' text, the '#' optional, or a whole
    number from 0 to 0xffffff. Anything else is a ConfigError naming `where`."""
    if isinstance(value, str):
        # Exactly six hex digits, the owner's decision of 2026-09-26: int() alone takes
        # "fff" as 0x000fff, and "0x", "_" and fullwidth digits.
        text = value.strip().removeprefix("#")
        if len(text) == 6 and all(c in string.hexdigits for c in text):
            return int(text, 16)
    elif whole(value):
        if not 0 <= value <= 0xFFFFFF:
            raise ConfigError(f"{where}: {written(value)} is not a color like '#0080ff' "
                              "or a number from 0 to 16777215")
        return value
    raise ConfigError(f"{where}: {written(value)} is not a color like '#0080ff'")


def format_color(color: int) -> str:
    """A color as the files and the replies write it: '#rrggbb', lowercase."""
    return f"#{color:06x}"


def word_list(names: tuple[str, ...] | list[str], last: str = "or") -> str:
    """"a, b or c", for a refusal that names the choices; `last` "and" for
    "a, b and c"."""
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} {last} {names[-1]}"


#: The M profiles as a refusal of any other number names them: "1, 2 or 3".
PROFILE_CHOICES = word_list([str(i) for i in PROFILE_IDS])


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


def speed_problem(effect: str, value: object) -> str | None:
    """Why `value` is not a speed `effect` takes, in words, or None when it is."""
    if effect not in ANIMATIONS:
        return f"{effect} has no speed; only {word_list(ANIMATIONS, 'and')} have one"
    fastest, slowest = speed_range(effect)
    if not whole(value) or not fastest <= value <= slowest:
        return (f"{effect}'s speed is {fastest} to {slowest} ms, how long one cycle "
                f"takes, lower faster; not {written(value)}")
    return None


def direction_problem(value: object) -> str | None:
    """Why `value` is not one of wave's directions, in words, or None."""
    if isinstance(value, str) and value in WAVE_DIRECTIONS:
        return None
    return f"wave's direction is {word_list(list(WAVE_DIRECTIONS))}, not {written(value)}"


#: (where, effect, ms) of each clamp read_speeds has said at info.
_CLAMPS_SAID: set[tuple[str, str, int]] = set()


def read_speeds(raw: object, where: str) -> dict[str, int]:
    """A profile's saved animation speeds from JSON, {effect: ms}, each in its range.
    Absent or null is none saved: every animation at its default.

    A whole number outside its range is clamped to the nearer end and said once at
    info, so a narrowed range (ripple's, to 1000 ms on 2026-09-24) does not leave the
    daemon without its configuration. The file keeps the old number until the next
    save. Anything else wrong is refused, naming the field."""
    if raw is None:
        return {}
    expect(raw, dict, where, "an object of effect names and milliseconds")
    raw = cast(dict, raw)       # expect raised for anything else
    speeds = {}
    for name, value in raw.items():
        if name in ANIMATIONS and whole(value):
            fastest, slowest = speed_range(name)
            if not fastest <= value <= slowest:
                kept = min(max(value, fastest), slowest)
                # Once: the daemon reads every game profile every 5 s.
                said = (where, name, value)
                log.log(logging.DEBUG if said in _CLAMPS_SAID else logging.INFO,
                        "%s.%s: %d ms is outside %s's %d to %d; using %d", escaped(where), name,
                        value, name, fastest, slowest, kept)
                _CLAMPS_SAID.add(said)
                value = kept
        problem = speed_problem(name, value)
        if problem is not None:
            raise ConfigError(f"{where}.{name}: {problem}")
        speeds[name] = value
    return speeds


def read_direction(raw: object, where: str) -> str | None:
    """Wave's saved direction from JSON, by name; absent or null for none."""
    if raw is None:
        return None
    problem = direction_problem(raw)
    if problem is not None:
        raise ConfigError(f"{where}: {problem}")
    return str(raw)


def effect_settings(effect: str, speeds: dict[str, int], direction: str | None,
                    params: dict[str, int] | None = None) -> dict[str, int]:
    """What a profile saved for `effect`, as set_effect parameters: `params` (an M
    profile's raw ones), overridden by its speed as `period` and, for wave, its
    direction's byte. Nothing saved runs at lighting's defaults."""
    settings = dict(params or {})
    if effect in speeds:
        settings["period"] = speeds[effect]
    if effect == "wave" and direction is not None:
        settings["direction"] = WAVE_DIRECTIONS[direction]
    return settings


def speed_shown(effect: str, settings: dict[str, int]) -> int:
    """The period `effect` runs at, from effect_settings: the saved one, or the default."""
    return settings.get("period", default_speed(effect))


def direction_shown(settings: dict[str, int]) -> str:
    """Wave's direction by name from its settings; unsaved or unnamed, horizontal."""
    wanted = settings.get("direction")
    return next((name for name, code in WAVE_DIRECTIONS.items() if code == wanted),
                "horizontal")


def layer_color(effect: str, mode: str, static: int, background: int) -> int:
    """The color a layer shows, an M profile's or a game profile's: `static` under an
    animation or in static mode, `background` in per-key mode. The disabled effect's
    black is the caller's."""
    return static if effect in ANIMATIONS or mode != PER_KEY else background


def layer_binding(keys: dict[str, str | None], shift: dict[str, str | None], key: str,
                  shifted: bool) -> str | None:
    """A G key's binding on a layer, an M profile's or a game profile's. An unbound key
    on the shift layer falls through to the base layer, so holding MR does not silently
    disable keys the user did bind."""
    value = shift.get(key) if shifted else None
    return keys.get(key) if value is None else value


def read_mode(data: dict, where: str) -> str:
    """A layer's lighting mode from JSON, STATIC when absent."""
    mode = data.get("mode", STATIC)
    expect(mode, str, f"{where}.mode", word_list(MODES))
    if mode not in MODES:
        raise ConfigError(f"{where}.mode: must be {word_list(MODES)}, not {mode!r}")
    return mode


def macro_name(spec: object) -> str | None:
    """The macro a binding names, or None if it names a key instead."""
    text = str(spec)
    if not text.startswith(MACRO_PREFIX):
        return None
    return text[len(MACRO_PREFIX):].strip()


def validate_binding(spec: str, where: str) -> str:
    """One G-key binding: a key spec, or `macro:<name>`. Answers it as it is stored, without
    the white space around it, which a key spec is split at (uinput.binding_parts) and a
    macro's name drops (the owner's decision of 2026-10-07).

    A macro name is not checked against the library: a macro file can be added after
    the profile is read, and the order the two were written in must not matter. An
    unknown name is logged when the key is pressed.
    """
    spec = spec.strip()     # before the prefix is read, so " macro:Foo" is a macro
    name = macro_name(spec)
    if name is None:
        try:
            parse_binding(spec)
        except UinputError as exc:
            raise ConfigError(f"{where}: {exc}") from None
    elif not name:
        raise ConfigError(f"{where}: {spec!r} names no macro")
    else:
        refuse_name(name, where)
        refuse_controls(spec, where)    # also the white space macro_name strips
    return spec


def parse_mouse_binding(spec: object, where: str) -> str:
    """Validate one mouse binding: a built-in action, a key spec or a macro.

    None and "" mean "none"; anything else must be text, so a JSON 5 is not the 5 key.
    An action matches in any case and with spaces around it, since " Back" is held as
    "Back" otherwise, which parse_binding reads as KEY_BACK. The rest takes a G key's check,
    `macro:<name>` included, and is stored as a G key's is.
    """
    if spec is None or spec == "":
        return "none"
    expect(spec, str, where, "an action, a key spec or macro:<name>")
    spec = cast(str, spec)      # expect raised for anything else
    word = spec.strip().lower()
    if word in MOUSE_ACTIONS:
        return word
    return validate_binding(spec, where)


def mouse_button_name(name: object) -> str | None:
    """The config key for a mouse button name, in any case, or None."""
    text = str(name).lower()
    return text if text in MOUSE_BUTTONS else None


def mouse_layer(raw: object, where: str) -> dict[str, str]:
    """The six mouse bindings from JSON, defaults filled in."""
    if raw is None:
        raw = {}
    expect(raw, dict, where, "an object of mouse buttons and bindings")
    raw = cast(dict, raw)       # expect raised for anything else
    found: dict[str, str] = {}
    for key, spec in raw.items():
        name = mouse_button_name(key)
        if name is None:
            raise ConfigError(
                f"{where}: {key!r} is not a mouse button; they are {', '.join(MOUSE_BUTTONS)}"
            )
        # Refused in any key order, or the later of "G4" and "g4" wins unsaid.
        if name in found:
            raise ConfigError(f"{where}: {key!r} names a button another key names")
        found[name] = parse_mouse_binding(spec, f"{where}.{key}")
    return {name: found.get(name, MOUSE_DEFAULTS[name]) for name in MOUSE_BUTTONS}


def mouse_dpi_map(raw: object) -> dict[str, int]:
    """The remembered DPI per mouse model, by product id."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"mouse_dpi must be an object, not {written(raw)}")
    out = {}
    for product, value in raw.items():
        if not whole(value) or value <= 0:
            raise ConfigError(
                f"mouse_dpi[{product!r}]: {written(value)} is not a positive whole number")
        key = product.lower()
        if key in out:
            raise ConfigError(f"mouse_dpi: {product!r} names a mouse another key names")
        out[key] = value
    return out


def not_bindable(key: object) -> str:
    """The refusal of a key no binding takes. It names the bindable keys, because M1-M3
    and MR look bindable and are not."""
    return (f"{written(key)} is not bindable; bindable keys are {', '.join(G_KEYS)}. "
            "M1-M3 switch profiles and MR is the shift layer.")


def g_key_layer(raw: object, where: str) -> dict[str, str | None]:
    """The five G-key bindings from JSON, unbound ones None; game profiles share it."""
    if raw is None:
        raw = {}
    expect(raw, dict, where, "an object of G keys and bindings")
    raw = cast(dict, raw)       # expect raised for anything else
    out: dict[str, str | None] = dict.fromkeys(G_KEYS)
    for key, spec in raw.items():
        if key not in G_KEYS:
            raise ConfigError(f"{where}: {not_bindable(key)}")
        if spec is None or spec == "":
            continue
        expect(spec, str, f"{where}.{key}", "a key spec or macro:<name>")
        out[key] = validate_binding(spec, f"{where}.{key}")
    return out


@dataclass
class Lighting:
    """A profile's lighting: one zone effect, plus optional per-key overrides.

    The board shows one choice, as the window lists it: an animation
    (lighting.ANIMATIONS) when `effect` is one, else `mode`, Static or Per-key. An
    animation hides the painted keys as Static does and leaves `mode` alone, so
    `effect static` brings the earlier choice back.
    """

    effect: str = "static"
    #: The Static color: the whole board in static mode, and the color an
    #: animation takes, breathe's and ripple's, whatever the mode.
    color: int = DEFAULT_COLOR
    #: Extra effect parameters, e.g. {"period": 5000} for cycle, applied to
    #: whatever effect runs. Only a hand edit fills them, and a game profile's
    #: lighting taken over clears them; `speeds` and `direction` win over them.
    params: dict[str, int] = field(default_factory=dict)
    #: Each animation's speed, its period in ms, by effect name, within lighting.SPEEDS;
    #: one missing runs at lighting.DEFAULT_PERIODS. Kept per effect, so another choice
    #: and back finds it (the owner's decision of 2026-09-24).
    speeds: dict[str, int] = field(default_factory=dict)
    #: Wave's direction, a lighting.WAVE_DIRECTIONS name, or None for
    #: horizontal (or a `direction` in `params`).
    direction: str | None = None
    #: Per-key overrides by key name, e.g. {"G1": 0xff0000}.
    keys: dict[str, int] = field(default_factory=dict)
    #: STATIC shows `color` on every key and keeps `keys` without showing
    #: them; PER_KEY shows `keys` over `background`. The same field a game
    #: profile has.
    mode: str = STATIC
    #: What an unpainted key shows in per-key mode, apart from `color` (the owner's
    #: decision of 2026-09-23).
    background: int = DEFAULT_BACKGROUND

    def mode_color(self) -> int:
        """The color of the choice it shows, but for the disabled effect's black."""
        return layer_color(self.effect, self.mode, self.color, self.background)

    def settings(self, effect: str | None = None) -> dict[str, int]:
        """What this profile saved for `effect`, its own by default (effect_settings)."""
        return effect_settings(effect or self.effect, self.speeds, self.direction,
                               self.params)

    def to_json(self) -> dict:
        return {
            "effect": self.effect,
            "color": format_color(self.color),
            "params": dict(self.params),
            "speeds": dict(self.speeds),
            "direction": self.direction,
            "mode": self.mode,
            "background": format_color(self.background),
            "keys": {k: format_color(v) for k, v in self.keys.items()},
        }

    @classmethod
    def from_json(cls, data: object, where: str) -> Lighting:
        expect(data, dict, where, "an object")
        data = cast(dict, data)     # expect raised for anything else
        # Only null is the default, as in Config.from_json.
        params = data.get("params")
        if params is None:
            params = {}
        expect(params, dict, f"{where}.params", "an object")
        # A bool is an int and would go out as 1; a negative number fits no byte.
        for name, value in params.items():
            if not whole(value) or value < 0:
                raise ConfigError(f"{where}.params.{name}: {written(value)} is not a whole "
                                  "number, 0 or more")
        keys = data.get("keys")
        if keys is None:
            keys = {}
        expect(keys, dict, f"{where}.keys", "an object of key names and colors")
        mode = read_mode(data, where)
        color = parse_color(data.get("color", DEFAULT_COLOR), f"{where}.color")
        effect = data.get("effect", "static")
        expect(effect, str, f"{where}.effect", "an effect name")
        return cls(
            effect=effect,
            color=color,
            params=dict(params),
            speeds=read_speeds(data.get("speeds"), f"{where}.speeds"),
            direction=read_direction(data.get("direction"), f"{where}.direction"),
            keys={k: parse_color(v, f"{where}.keys.{k}") for k, v in keys.items()},
            mode=mode,
            background=parse_color(data.get("background", DEFAULT_BACKGROUND),
                                   f"{where}.background"),
        )


@dataclass
class Profile:
    name: str = "Profile"
    #: G-key bindings, None meaning unbound. Values are specs like "ctrl+f5".
    keys: dict[str, str | None] = field(default_factory=lambda: dict.fromkeys(G_KEYS))
    #: Bindings used while MR is held.
    shift: dict[str, str | None] = field(default_factory=lambda: dict.fromkeys(G_KEYS))
    lighting: Lighting = field(default_factory=Lighting)
    #: The mouse's G4-G9: a built-in action, a key spec or a macro, never None.
    mouse: dict[str, str] = field(default_factory=lambda: dict(MOUSE_DEFAULTS))

    def binding(self, key: str, shifted: bool) -> str | None:
        """A G key's binding, the MR layer's when `shifted` and it has one (layer_binding)."""
        return layer_binding(self.keys, self.shift, key, shifted)

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "keys": dict(self.keys),
            "shift": dict(self.shift),
            "lighting": self.lighting.to_json(),
            "mouse": dict(self.mouse),
        }

    @classmethod
    def from_json(cls, data: object, where: str, default_name: str) -> Profile:
        expect(data, dict, where, "an object")
        data = cast(dict, data)     # expect raised for anything else

        name = data.get("name", default_name)
        expect(name, str, f"{where}.name", "text")
        refuse_name(name, f"{where}.name")
        # Only null is the default; false or [] is a wrong type, refused.
        lighting = data.get("lighting")
        return cls(
            name=name,
            keys=g_key_layer(data.get("keys"), f"{where}.keys"),
            shift=g_key_layer(data.get("shift"), f"{where}.shift"),
            lighting=Lighting.from_json({} if lighting is None else lighting,
                                        f"{where}.lighting"),
            mouse=mouse_layer(data.get("mouse"), f"{where}.mouse"),
        )


#: The most saved colors config.json holds: the Keyboard tab's swatch row has no scroller.
SAVED_COLORS_MAX = 24


@dataclass
class Config:
    active: int = 1
    brightness: int = 50
    #: Colors the user chose to keep; nothing is added just for being tried.
    saved_colors: list[int] = field(default_factory=list)
    #: Game profile overlaying the M profile, by name, or None.
    active_game_profile: str | None = None
    #: The mouse follows the keyboard's lighting: its base color, live previews included,
    #: its brightness, the lock, and its idle dimming once the whole desktop is idle. A
    #: device preference, not a profile's: the mouse is one device whatever profile is active.
    mouse_sync_color: bool = False
    #: The keyboard's lights, and a synced mouse's, go off while the screen is locked
    #: or blanked and before a suspend (the owner's decision of 2026-09-23).
    lights_off_when_locked: bool = True
    #: Idle dimming (IDLE_DIM_AFTER): seconds from the last keypress to the dim, and from
    #: the dim to lights off. never_dim turns both off; the keyboard's idle and wake
    #: notices are answered either way.
    idle_dim_after: int = IDLE_DIM_AFTER
    idle_off_after: int = IDLE_OFF_AFTER
    never_dim: bool = False
    #: A desktop notice with the new DPI when a mouse button bound to dpi-up or dpi-down
    #: steps it, replaced at each step (the owner's decision of 2026-09-24). Its own
    #: switch because GNOME's switch for the app would silence the battery notices too.
    dpi_notices: bool = True
    #: HOST mode for a mouse whose button spy is measured (spy.SPY_MODELS): while the
    #: daemon runs, the mouse is in HOST mode with 0x8110 armed and G4 to G9 reach the
    #: daemon as the spy's masks; at a stop it goes back to ONBOARD mode on profile 1
    #: (the owner's decision of 2026-09-24). Off, every mouse runs onboard profile 1.
    mouse_host_mode: bool = True
    #: The tray icon (the owner's scope of 2026-09-26): the batteries and problems, and a
    #: menu of DPI stages, game profiles and these switches.
    tray: bool = True
    #: Tell the keyboard's receiver to send its link notices (register 0x00's wireless
    #: bit) when the keyboard connects through it. On by the owner's decision of
    #: 2026-09-26; set by hand only.
    keyboard_link_notices: bool = True
    #: The DPI last chosen for each mouse model, by product id, re-applied when that mouse
    #: reappears: after a power cycle it is on its last stage in ONBOARD mode and on
    #: profile 1's default stage in HOST mode, neither of which need be the DPI chosen
    #: over 0x2201. After a write that changes the stage list it is the DPI the mouse
    #: reads then. A mouse absent from the map keeps its own stage. Keyed by product id, so
    #: a mouse of another model is not set to the first one's DPI.
    mouse_dpi: dict[str, int] = field(default_factory=dict)
    profiles: dict[int, Profile] = field(
        default_factory=lambda: {
            i: Profile(name=f"Profile {i}") for i in PROFILE_IDS
        }
    )

    @property
    def profile(self) -> Profile:
        return self.profiles[self.active]

    def switches_to(self, index: int) -> bool:
        """Whether `index` names an M profile other than the active one, so that a switch to
        it makes it active."""
        return index in PROFILE_IDS and index != self.active

    def to_json(self) -> dict:
        return {
            "version": VERSION,
            "active_profile": self.active,
            "brightness": self.brightness,
            "saved_colors": [format_color(c) for c in self.saved_colors],
            "active_game_profile": self.active_game_profile,
            "mouse_sync_color": self.mouse_sync_color,
            "lights_off_when_locked": self.lights_off_when_locked,
            "idle_dim_after": self.idle_dim_after,
            "idle_off_after": self.idle_off_after,
            "never_dim": self.never_dim,
            "dpi_notices": self.dpi_notices,
            "mouse_host_mode": self.mouse_host_mode,
            "tray": self.tray,
            "keyboard_link_notices": self.keyboard_link_notices,
            "mouse_dpi": self.mouse_dpi,
            "profiles": {str(i): p.to_json() for i, p in sorted(self.profiles.items())},
        }

    @classmethod
    def from_json(cls, data: object) -> Config:
        if not isinstance(data, dict):
            raise ConfigError(f"config must be a JSON object, not {kind_of(data)}")
        if (problem := version_problem(data, VERSION)) is not None:
            raise ConfigError(f"config {problem}")
        # A missing field takes the default. Null does too where a field may be empty, and
        # is refused elsewhere. A wrong type is refused even when falsy (false, 0, []): read
        # as the default, it would be saved over the hand edit.
        stored = data.get("profiles")
        if stored is None:
            stored = {}
        expect(stored, dict, "profiles", 'an object keyed "1", "2" and "3"')
        # Read only by these keys, so any other would be dropped at the next save.
        for key in stored:
            if key not in {str(i) for i in PROFILE_IDS}:
                raise ConfigError(f'profiles: {key!r} is not a profile; they are '
                                  f'"1", "2" and "3", one per M key')
        profiles = {}
        for i in PROFILE_IDS:
            raw = stored.get(str(i))
            profiles[i] = Profile.from_json({} if raw is None else raw, f"profiles.{i}",
                                            f"Profile {i}")
        active = data.get("active_profile", 1)
        # An int check first: 2.0 is `in` PROFILE_IDS and would reach `1 << (which - 1)`.
        if not whole(active) or active not in PROFILE_IDS:
            raise ConfigError(f"active_profile must be {PROFILE_CHOICES}, "
                              f"not {written(active)}")
        brightness = data.get("brightness", 50)
        if not whole(brightness) or not 0 <= brightness <= 100:
            raise ConfigError("brightness must be a whole number from 0 to 100, "
                              f"not {written(brightness)}")
        colors = data.get("saved_colors")
        if colors is None:
            colors = []
        expect(colors, list, "saved_colors", "a list of colors")
        # Refused, not cut to the newest: the next save would write the cut over the hand edit.
        if len(colors) > SAVED_COLORS_MAX:
            raise ConfigError(f"saved_colors: {len(colors)} colors is more than the "
                              f"{SAVED_COLORS_MAX} the Saved colors row may hold")
        saved = [parse_color(c, f"saved_colors[{i}]") for i, c in enumerate(colors)]
        overlay = data.get("active_game_profile")
        if overlay is not None:
            expect(overlay, str, "active_game_profile", "a game profile's name or null")
        return cls(
            active=active, brightness=brightness, profiles=profiles, saved_colors=saved,
            active_game_profile=overlay or None,
            mouse_sync_color=flag(data, "mouse_sync_color", False),
            lights_off_when_locked=flag(data, "lights_off_when_locked", True),
            never_dim=flag(data, "never_dim", False),
            dpi_notices=flag(data, "dpi_notices", True),
            mouse_host_mode=flag(data, "mouse_host_mode", True),
            tray=flag(data, "tray", True),
            keyboard_link_notices=flag(data, "keyboard_link_notices", True),
            mouse_dpi=mouse_dpi_map(data.get("mouse_dpi")),
            idle_dim_after=idle_seconds(data.get("idle_dim_after", IDLE_DIM_AFTER),
                                        "idle_dim_after"),
            idle_off_after=idle_seconds(data.get("idle_off_after", IDLE_OFF_AFTER),
                                        "idle_off_after"),
        )


    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        """Read config.json; defaults when there is no file, and a ConfigError for a
        link to nothing, the file's or a folder's above it, so nothing is saved over it.
        Every ConfigError names the file."""
        target = path or config_path()

        def parse(raw: object) -> Config:
            try:
                return cls.from_json(raw)
            except ConfigError as exc:
                raise ConfigError(f"{target}: {exc}") from None

        try:
            return from_file(target, parse)
        except FileNotFoundError:
            place = link_to_nothing(target)
            if place is None:
                return cls()
            raise ConfigError(missing_behind(target, place)) from None

    def save(self, path: Path | None = None) -> Path:
        """Write atomically, so a crash mid-write cannot leave a truncated config. A folder
        behind a link to nothing is a ConfigError, as at load."""
        # The rename command takes any text, and a name that load refuses
        # would hold every profile back at the next start.
        for index, profile in self.profiles.items():
            refuse_name(profile.name, f"profiles.{index}.name")
        target = path or config_path()
        # mkdir fails a folder behind a link to nothing as "File exists", naming neither
        # the link nor where it points. config.json's own link is written through.
        if (place := link_to_nothing(target.parent)) is not None:
            raise ConfigError(f"{missing_behind(target, place)}; nothing is saved")
        target.parent.mkdir(parents=True, exist_ok=True)
        write_json(target, self.to_json(), ".config-")
        return target
