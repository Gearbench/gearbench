"""Configuration for Gearbench: ~/.config/gearbench/config.json.

Three profiles, one per M key, each with its G-key bindings, a shift layer held on
MR, its lighting for each keyboard model, and its bindings for each mouse model's buttons. A
binding is a key spec like "ctrl+f13" or `macro:<name>`; a mouse button also takes a
built-in action. G keys ship unbound, so nothing sends keystrokes the user did not ask
for; the G915 TKL's, its F1 to F12, type their own keys while unbound
(keyboard.keystroke). JSON, since the daemon writes this file and the standard library
only reads TOML.

Version 2 keeps the lighting and the brightness by keyboard model, under the model's
first product id (keyboard.model_key). Version 1 kept one of each, which are read as the
G915's (V1_KEYBOARD). Version 3 keeps the mouse bindings by mouse model in the same way
(mouse.model_key); versions 1 and 2 kept one set, read as the G502 X PLUS's (V2_MOUSE).
"""

from __future__ import annotations

import json
import logging
import os
import stat
import string
import tempfile
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from .lighting import ANIMATIONS, DEFAULT_PERIODS, SPEEDS, WAVE_DIRECTIONS
from .uinput import UinputError, parse_binding
from .wording import (
    MAX_REFUSAL,
    both_ends,
    escaped,
    kind_of,
    printable,
    reason,
    whole,
    word_list,
    written,
)

log = logging.getLogger("gearbench")

VERSION = 3
#: The first version of each file format with a newer one: the oldest one read or backed up,
#: and the one a file without `version` is.
FIRST_VERSION = 1
#: The first format to keep the mouse bindings by mouse model; an older one's are V2_MOUSE's.
MOUSE_BY_MODEL = 3
#: The model a version 1 file's lighting and brightness are read as: the G915, which
#: keyboard.model_key names "c33e", the one keyboard Gearbench drove before version 2.
V1_KEYBOARD = "c33e"
#: The model a version 1 or 2 file's mouse bindings are read as: the G502 X PLUS, which
#: mouse.model_key names "4099", the one mouse with bindings before version 3.
V2_MOUSE = "4099"
#: How a refusal names a keyboard model where a file keys one: keyboard.model_key's form.
KEYBOARD_MODEL = "a keyboard model like 'c33e'"
#: The same for a mouse model: mouse.model_key's form.
MOUSE_MODEL = "a mouse model like '4099'"
#: The brightness of a keyboard model with none saved and none to copy.
DEFAULT_BRIGHTNESS = 50
#: The G915's and the G815's G keys, by the names a layer of bindings keeps them under.
G_KEYS = ("G1", "G2", "G3", "G4", "G5")
#: The G915 TKL's G keys, its F row, by the names its keycaps and the window give them, so a
#: binding made for the G915's G1 is not the TKL's F1 (the owner's decision of 2026-10-18,
#: "Their own"). keyboard.KeyboardModel.g_key_names gives each model its own.
F_ROW_KEYS = tuple(f"F{n}" for n in range(1, 13))
#: Every name a layer of bindings keeps, in the order its files write them.
BINDABLE_KEYS = G_KEYS + F_ROW_KEYS
PROFILE_IDS = (1, 2, 3)          # one per M key


def profile_label(index: int, name: str) -> str:
    """M profile `index` named `name` as the window, the tray and a refusal name it:
    "M2 · Shooter"."""
    return f"M{index} · {name}"


#: The two lighting modes, of M and game profiles alike: one color for the whole
#: board, or the per-key layout over a background. profiles.py imports and
#: re-exports them.
STATIC, PER_KEY = "static", "perkey"
MODES = (STATIC, PER_KEY)
#: A lighting layer's Static color and per-key background where its file has none.
DEFAULT_COLOR, DEFAULT_BACKGROUND = 0x0080FF, 0x000000

#: The names a mouse model's programmable buttons are bound under, in display order: each
#: model has some of them (mouse.ButtonTable).
MOUSE_BUTTONS = ("g4", "g5", "g6", "g7", "g8", "g9")
#: What each one is on the G502 X PLUS, for the model tables that share its labels (mouse.py)
#: and the window's rows until a reply gives the shown model's.
MOUSE_LABELS = {
    "g4": "G4 (side, rear)", "g5": "G5 (side, front)", "g6": "G6 (paddle)",
    "g7": "G7 (DPI down)", "g8": "G8 (DPI up)", "g9": "G9 (behind the wheel)",
}
#: Built-in actions a mouse button can be bound to instead of a key spec.
#: back and forward hold the virtual mouse's buttons uinput.POINTER_ACTIONS names;
#: dpi-shift drops to the profile's shift stage while held; dpi-cycle steps up and from the
#: last stage round to the first, as the PRO WIRELESS's DPI button does.
MOUSE_ACTIONS = ("none", "back", "forward", "dpi-shift", "dpi-up", "dpi-down", "dpi-cycle")
#: What versions 1 and 2 gave a button their file left out: the G502 X PLUS's factory jobs
#: for G4 to G8 and nothing on G9, a profile cycle there (mouse.G502X_BUTTONS.defaults).
MOUSE_DEFAULTS = {"g4": "back", "g5": "forward", "g6": "dpi-shift",
                  "g7": "dpi-down", "g8": "dpi-up", "g9": "none"}

#: The report rates a mouse's onboard profile and its live write take, in Hz, the fastest
#: first, as a message lists them: an interval of 1, 2, 4 or 8 ms (onboard.RATE_BYTES).
REPORT_RATES = (1000, 500, 250, 125)

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


def others_may_write(folder: Path) -> bool:
    """Whether another account owns `folder` or may write in it, through its group or as any
    account (mode & 0o022): it could bind a socket there first, or put its own file in place
    of one written there before it is read. OSError passes through."""
    info = os.stat(folder)
    return info.st_uid != os.geteuid() or bool(info.st_mode & 0o022)


def open_to_others(folder: Path) -> Path | None:
    """The first path through which another account could put a folder of its own where
    this process looks for the runtime folder `folder`, a full path; None when there is
    none. That is `folder` itself, links followed, when another account owns it or others
    may write in it (others_may_write); a folder or link on the way, by the path as given
    and by the path its links lead to, a link given as `folder` included, that an account
    other than root or this one owns; and a folder on the way that others may write in
    without the sticky bit, since they may rename what it holds."""
    if others_may_write(folder):
        return folder
    uid = os.geteuid()
    # Root as this process sees it: in a user namespace that does not map root, as the
    # unit's PrivateUsers=yes makes one (systemd.exec(5)), stat gives root's files the
    # overflow id, 65534 by default (user_namespaces(7)), and every other unmapped
    # account's files too, which this cannot tell from root's.
    root = Path("/").stat().st_uid
    for path in dict.fromkeys((*reversed(folder.parents), folder,
                               *reversed(folder.resolve().parents))):
        info = path.stat(follow_symlinks=False)
        if info.st_uid not in (root, uid) or (
                stat.S_ISDIR(info.st_mode) and info.st_mode & 0o022
                and not info.st_mode & stat.S_ISVTX):
            return path
    return None


#: Why a runtime folder another account owns or may write in is refused, and the way out.
SHARED_FOLDER = ("another account owns it or other accounts may write in it. Set "
                 "XDG_RUNTIME_DIR to a folder only you may write in, as /run/user/{uid} is")
#: private_runtime's refusal of such a folder.
SHARED_RUNTIME = "{what} uses no file in {folder}: " + SHARED_FOLDER + "."


def private_runtime(error: type[Exception], unset: str, what: str) -> Path:
    """XDG_RUNTIME_DIR for `what`'s files, or `error`: with `unset` while it is not set
    (runtime_home), and with why not where another account owns it or may write in it
    (others_may_write), as Daemon.listen refuses it, since that account could swap a conf
    before PipeWire reads it, or put its own file, or a link, where one is read or written.
    The folder's stat raises its OSError, as a write in it would."""
    folder = runtime_home()
    if folder is None:
        raise error(unset)
    if others_may_write(folder):
        raise error(SHARED_RUNTIME.format(what=what, folder=escaped(folder), uid=os.geteuid()))
    return folder


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


def backup_dir() -> Path:
    """The settings folder's backups: the mouse's onboard sectors before each write
    (onboard.py), and a file's bytes in an older format before its first save in its newest
    (keep_old_version)."""
    return config_dir() / "backup"


def config_backup(version: int) -> Path:
    """Where config.json's bytes in format `version` are kept (keep_old_version)."""
    return backup_dir() / f"config.v{version}.json"


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


#: keep_old_version's refusal of a save whose older version's bytes could not be copied.
BACKUP_REFUSED = ("{target} is not saved: its settings from version {version} could not be "
                  "copied to {kept} first ({why}).")


def keep_old_version(target: Path, backup: Callable[[int], Path], current: int = VERSION) -> None:
    """Copy `target`'s bytes to `backup(version)` just before its first save in `current`, its
    format's newest, where they are an older `version`'s file, which a Gearbench from before
    `current` reads and `current` is not. Once: a backup already there, from an earlier save,
    is never written over. Nothing is copied for a `target` not there, or not a JSON file of
    a version before `current` that read_json would read: one the save writes over, as
    `gearbench-ctl save` does a hand edit. A copy, or a sync of a folder it is in, that fails
    refuses the save with the backup's path and the cause, `target` untouched, and the copy
    and the folders made for it removed (replace_synced), so the next save makes them again;
    a read of `target` that fails goes to the caller as the save's own write would."""
    try:
        data = read_capped(target, MAX_JSON_BYTES)
        old = json.loads(data)
    except (FileNotFoundError, ValueError, RecursionError):
        return
    if not isinstance(old, dict) or version_problem(old, current - 1, FIRST_VERSION) is not None:
        return
    version = old.get("version", FIRST_VERSION)
    kept = backup(version)
    if kept.exists(follow_symlinks=False):
        return
    # Synced here: the save's own write syncs only its file's folder.
    try:
        replace_synced(kept, data, ".backup-", ".json")
    except OSError as exc:
        raise ConfigError(BACKUP_REFUSED.format(target=target, version=version, kept=kept,
                                                why=reason(exc))) from None


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


def replace_synced(target: Path, data: bytes, prefix: str, suffix: str) -> None:
    """replace_file into `target`, a new file, in its folder, made first where it is not
    there, then that folder synced and the folder each one the mkdir made is in, since a
    folder made is on disk only once the folder it is in is synced (fsync(2)). On an
    OSError, `target`, once replace_file has put it in place, and the folders made are
    removed before it is raised: left, the next try would take them as on disk and sync none
    of their parents. A removal that fails is a warning, so it is the write's failure that
    is raised."""
    made = [folder for folder in (target.parent, *target.parent.parents)
            if not folder.exists(follow_symlinks=False)]
    placed = False
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        replace_file(target, data, prefix, suffix)
        placed = True
        sync_directory(target.parent)
        for folder in made:
            sync_directory(folder.parent)
    except OSError:
        # A mkdir that failed may have stopped short of a folder.
        leftovers: list[tuple[Path, Callable[[], None]]] = (
            [(target, target.unlink)] if placed else [])
        leftovers += [(folder, folder.rmdir) for folder in made
                      if folder.exists(follow_symlinks=False)]
        for leftover, remove in leftovers:
            try:
                remove()
            except OSError as exc:
                log.warning("%s could not be removed after a write that failed (%s)",
                            escaped(leftover), reason(exc))
        raise


def sync_directory(directory: Path) -> None:
    """Put a change to `directory`'s entries, a replace, an unlink or a new folder, on
    disk: until then a power loss can undo it (fsync(2)). Opened as a directory
    (O_DIRECTORY), so a file or a named pipe put at its path since the change is refused
    at the open, where a pipe would block."""
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


def version_problem(data: dict, expected: int, oldest: int | None = None) -> str | None:
    """Why a file's `version` is not `expected`, or one from `oldest` to `expected` where an
    older one is still read, in words, or None. A file without one is the oldest read."""
    first = expected if oldest is None else oldest
    version = data.get("version", first)
    taken = word_list([str(n) for n in range(first, expected + 1)])
    # written() gives a list or an object by its kind, which "version ... is not supported"
    # can't hold.
    if isinstance(version, (list, dict)):
        return f"version must be {taken}, not {written(version)}"
    # true == 1 and 1.0 == 1, so the type is checked first.
    if not whole(version) or not first <= version <= expected:
        return f"version {written(version)} is not supported (expected {taken})"
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


def expect(value: object, kind: type, where: str, what: str) -> None:
    """Raise ConfigError unless `value` is a `kind`, naming what was found (kind_of)."""
    if not isinstance(value, kind):
        raise ConfigError(f"{where}: must be {what}, not {kind_of(value)}")


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


#: The M profiles as a refusal of any other number names them: "1, 2 or 3".
PROFILE_CHOICES = word_list([str(i) for i in PROFILE_IDS])
#: REPORT_RATES as a refusal of any other rate names them: "1000, 500, 250 or 125".
RATE_CHOICES = word_list([str(rate) for rate in REPORT_RATES])


def speed_problem(effect: str, value: object) -> str | None:
    """Why `value` is not a speed `effect` takes, in words, or None when it is."""
    if effect not in ANIMATIONS:
        return f"{effect} has no speed; only {word_list(ANIMATIONS, 'and')} have one"
    fastest, slowest = SPEEDS[effect]
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
            fastest, slowest = SPEEDS[name]
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
                    params: dict[str, int]) -> dict[str, int]:
    """What a profile saved for `effect`, as set_effect parameters: `params` (an M
    profile's raw ones), overridden by its speed as `period` and, for wave, its
    direction's byte. Nothing saved runs at lighting's defaults."""
    settings = dict(params)
    if effect in speeds:
        settings["period"] = speeds[effect]
    if effect == "wave" and direction is not None:
        settings["direction"] = WAVE_DIRECTIONS[direction]
    return settings


def speed_shown(effect: str, settings: dict[str, int]) -> int:
    """The period `effect` runs at, from effect_settings: the saved one, or the default."""
    return settings.get("period", DEFAULT_PERIODS[effect])


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


def mouse_entry(raw: object, where: str) -> dict[str, str]:
    """The mouse bindings an object of JSON holds, by button name, in the file's order. A
    button a model lacks is kept and does nothing on it; one it has and the object leaves out
    takes the model's default (mouse.bindings_of)."""
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
    return found


def read_mouse_bindings(raw: object, where: str, version: int) -> dict[str, dict[str, str]]:
    """A profile's mouse bindings by mouse model (mouse.model_key), from a file of format
    `version`: an older format's one set, MOUSE_DEFAULTS filled in, is V2_MOUSE's. Absent or
    null is none."""
    if version >= MOUSE_BY_MODEL:
        return by_model(raw, where, mouse_entry, MOUSE_MODEL, "mouse")
    return {} if raw is None else {V2_MOUSE: MOUSE_DEFAULTS | mouse_entry(raw, where)}


def mouse_json(entries: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    """A profile's mouse bindings as its file writes them: by model, sorted."""
    return {model: dict(entry) for model, entry in sorted(entries.items())}


def product_key(key: str, where: str, what: str) -> str:
    """A device's product id where a file keys one, in four lowercase hex digits, or a
    ConfigError naming `where` and saying it is not `what`, the kind of id it should be."""
    product = key.strip().lower()
    if len(product) != 4 or not all(c in string.hexdigits for c in product):
        raise ConfigError(f"{where}: {key!r} is not {what}")
    return product


def by_model[T](raw: object, where: str, read: Callable[[object, str], T], what: str,
                kind: str) -> dict[str, T]:
    """What a file keeps by device model, each value read by `read(value, where)`: absent or
    null is nothing. A key is a model's product id, `what` (KEYBOARD_MODEL, MOUSE_MODEL), read
    in lowercase, and one named twice is refused, in any key order, as naming a `kind`
    another key names."""
    if raw is None:
        return {}
    expect(raw, dict, where, f"an object keyed by {what}")
    raw = cast(dict, raw)       # expect raised for anything else
    out: dict[str, T] = {}
    for key, value in raw.items():
        model = product_key(key, where, what)
        if model in out:
            raise ConfigError(f"{where}: {key!r} names a {kind} another key names")
        out[model] = read(value, f"{where}.{key}")
    return out


def key_under(entries: Mapping[str, object], ids: Sequence[str]) -> str | None:
    """The first of `ids` that `entries`, kept by device model, has: the one entry_under reads.
    `ids` are one model's ids in the order they are read: its key first (keyboard.model_ids,
    mouse.model_ids), or a mouse's own route first (daemon.mice.attached.dpi_keys). None for
    none."""
    return next((name for name in ids if name in entries), None)


def entry_under[T](entries: Mapping[str, T], ids: Sequence[str]) -> T | None:
    """What `entries`, kept by device model, holds under the first of `ids` it has
    (key_under). None for nothing."""
    key = key_under(entries, ids)
    return None if key is None else entries[key]


def keep_under[T](entries: dict[str, T], ids: Sequence[str], entry: T) -> None:
    """Keep `entry` in `entries` under `ids[0]` (key_under) and drop one under its other ids:
    beside a model's key the others would be left unread, and beside a mouse's own route the
    entry under its other route would be read there in its place. A key kept already keeps its
    place in `entries`."""
    for name in ids[1:]:
        entries.pop(name, None)
    entries[ids[0]] = entry


def merge_under[T](entries: dict[str, T], imported: Mapping[str, T],
                   ids: Callable[[str], tuple[str, ...]]) -> None:
    """Keep in `entries` each model's entry of `imported`, as keep_under does: where `imported`
    holds a model under two of its ids, the one entry_under reads there. `ids` gives a model's
    ids from any of them (keyboard.model_ids, mouse.model_ids). A model `imported` lacks keeps
    its entry."""
    for name, entry in imported.items():
        model = ids(name)
        if name == key_under(imported, model):
            keep_under(entries, model, entry)


class ModelKeys[M]:
    """The names the files keep a kind of device's models under (keyboard.KEYING, mouse.KEYING):
    each model's product ids, in four lowercase hex digits in `model_of`'s order, by the
    first, its key."""

    def __init__(self, model_of: Mapping[int, M]) -> None:
        self.model_of = model_of
        #: Each model's ids, by its key, in `model_of`'s order.
        self.ids = {ids[0]: ids for ids in (
            tuple(f"{product:04x}" for product, known in model_of.items() if known is model)
            for model in model_of.values())}

    def key_of(self, model: M) -> str:
        """The name `model` is kept under in the files."""
        return next(key for key in self.ids if self.model_of[int(key, 16)] is model)

    def model_key(self, product: int) -> str:
        """The name the model of `product` is kept under in the files, on whichever route it
        answers."""
        return self.key_of(self.model_of[product])

    def model_ids(self, key: str) -> tuple[str, ...]:
        """The ids the files may keep the model of id `key` under, its key first, for `key`
        any of its ids: one under another of its ids was written by hand, and is the
        model's. An id no model has is its own alone."""
        return next((ids for ids in self.ids.values() if key in ids), (key,))

    def model_named(self, key: str | None) -> M | None:
        """The model whose key is `key`; None for any other id, a model's other ids
        included, and for None."""
        return self.model_of[int(key, 16)] if key in self.ids else None


def read_brightness(value: object, where: str) -> int:
    """A keyboard's saved brightness, a whole number from 0 to 100, or ConfigError naming
    `where`."""
    if not whole(value) or not 0 <= value <= 100:
        raise ConfigError(f"{where} must be a whole number from 0 to 100, not {written(value)}")
    return value


def per_mouse(raw: object, name: str, fits: Callable[[object], bool],
              what: str) -> dict[str, int]:
    """A whole number kept per mouse model, the field `name` of config.json, read by by_model
    as every map kept by model is (the owner's decision of 2026-10-20): each value one that
    `fits`, else a ConfigError saying it is not `what`."""
    def read(value: object, where: str) -> int:
        if not fits(value):
            raise ConfigError(f"{printable(where)}: {written(value)} is not {what}")
        return cast(int, value)     # `fits` takes whole numbers alone
    return by_model(raw, name, read, MOUSE_MODEL, "mouse")


def mouse_dpi_map(raw: object) -> dict[str, int]:
    """The remembered DPI per mouse model, by product id."""
    return per_mouse(raw, "mouse_dpi", lambda value: whole(value) and value > 0,
                     "a positive whole number")


def mouse_rate_map(raw: object) -> dict[str, int]:
    """The report rate set live per mouse model, by product id, each one of REPORT_RATES."""
    return per_mouse(raw, "mouse_rate", lambda value: whole(value) and value in REPORT_RATES,
                     f"one of {RATE_CHOICES} Hz")


def not_bindable(key: object) -> str:
    """The refusal of a key no binding takes. It names the bindable keys, because M1-M3
    and MR look bindable and are not."""
    # The G915 TKL is keyboard.G915_TKL's name, which this module may not import.
    return (f"{written(key)} is not bindable; bindable keys are {G_KEYS[0]} to {G_KEYS[-1]}, "
            f"and {F_ROW_KEYS[0]} to {F_ROW_KEYS[-1]} on the G915 TKL. "
            "M1-M3 switch profiles and MR is the shift layer.")


def g_key_layer(raw: object, where: str) -> dict[str, str | None]:
    """A layer of G-key bindings from JSON, every one of BINDABLE_KEYS, unbound ones None;
    game profiles share it."""
    if raw is None:
        raw = {}
    expect(raw, dict, where, "an object of G keys and bindings")
    raw = cast(dict, raw)       # expect raised for anything else
    out: dict[str, str | None] = dict.fromkeys(BINDABLE_KEYS)
    for key, spec in raw.items():
        if key not in BINDABLE_KEYS:
            raise ConfigError(f"{where}: {not_bindable(key)}")
        if spec is None or spec == "":
            continue
        expect(spec, str, f"{where}.{key}", "a key spec or macro:<name>")
        out[key] = validate_binding(spec, f"{where}.{key}")
    return out


def layer_json(layer: dict[str, str | None]) -> dict[str, str | None]:
    """A layer of bindings as its file writes it: each of G_KEYS, bound or not, and one of
    F_ROW_KEYS only while it is bound, so a file that binds none of them is written as before
    they were bindable."""
    return {key: spec for key, spec in layer.items() if key in G_KEYS or spec is not None}


@dataclass
class Lighting:
    """A profile's lighting for one keyboard model: one zone effect, plus optional per-key
    overrides. An M profile and a game profile keep one by model (Profile.lighting).

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
    #: whatever effect runs. Only a hand edit fills them; a game profile's
    #: lighting has none, and copy first drops them (keyboard.cut_lighting).
    #: `speeds` and `direction` win over them.
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
    #: them; PER_KEY shows `keys` over `background`.
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
    def from_json(cls, data: object, where: str, *, color_field: str = "color",
                  with_params: bool = True) -> Lighting:
        """One keyboard model's lighting from a file's JSON: config.json's, and a game
        profile's (profiles.game_lighting), whose Static color is its `color_field` and
        whose `params`, which only an M profile has, are left unread (`with_params`)."""
        expect(data, dict, where, "an object")
        data = cast(dict, data)     # expect raised for anything else
        # Only null is the default, as in Config.from_json.
        params = data.get("params") if with_params else None
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
        color = parse_color(data.get(color_field, DEFAULT_COLOR), f"{where}.{color_field}")
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
    #: G-key bindings by BINDABLE_KEYS' names, None meaning unbound. Values are specs like
    #: "ctrl+f5".
    keys: dict[str, str | None] = field(default_factory=lambda: dict.fromkeys(BINDABLE_KEYS))
    #: Bindings used while MR is held.
    shift: dict[str, str | None] = field(default_factory=lambda: dict.fromkeys(BINDABLE_KEYS))
    #: Its lighting by keyboard model (keyboard.model_key). A model with none shows another
    #: model's, cut to what it can show (keyboard.shown_lighting), until its first change.
    lighting: dict[str, Lighting] = field(default_factory=dict)
    #: Its mouse bindings by mouse model (mouse.model_key), each by button name: a built-in
    #: action, a key spec or a macro, never None. A model with none, or a button its entry
    #: leaves out, takes the model's defaults (mouse.bindings_of).
    mouse: dict[str, dict[str, str]] = field(default_factory=dict)

    def binding(self, key: str, shifted: bool) -> str | None:
        """A G key's binding, the MR layer's when `shifted` and it has one (layer_binding)."""
        return layer_binding(self.keys, self.shift, key, shifted)

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "keys": layer_json(self.keys),
            "shift": layer_json(self.shift),
            "lighting": {model: entry.to_json() for model, entry in sorted(self.lighting.items())},
            "mouse": mouse_json(self.mouse),
        }

    @classmethod
    def from_json(cls, data: object, where: str, default_name: str, version: int) -> Profile:
        """An M profile from a file of format `version`: version 1's one lighting, its defaults
        for any field it lacks, is V1_KEYBOARD's, and an older format's mouse bindings are
        read_mouse_bindings'."""
        expect(data, dict, where, "an object")
        data = cast(dict, data)     # expect raised for anything else

        name = data.get("name", default_name)
        expect(name, str, f"{where}.name", "text")
        refuse_name(name, f"{where}.name")
        # Only null is the default; false or [] is a wrong type, refused.
        lighting = data.get("lighting")
        if version == FIRST_VERSION:
            entries = {V1_KEYBOARD: Lighting.from_json({} if lighting is None else lighting,
                                                       f"{where}.lighting")}
        else:
            entries = by_model(lighting, f"{where}.lighting", Lighting.from_json,
                               KEYBOARD_MODEL, "keyboard")
        return cls(
            name=name,
            keys=g_key_layer(data.get("keys"), f"{where}.keys"),
            shift=g_key_layer(data.get("shift"), f"{where}.shift"),
            lighting=entries,
            mouse=read_mouse_bindings(data.get("mouse"), f"{where}.mouse", version),
        )


#: The most saved colors config.json holds: the Keyboard tab's swatch row has no scroller.
SAVED_COLORS_MAX = 24


@dataclass
class Config:
    active: int = 1
    #: Each keyboard model's brightness, by keyboard.model_key, one for every layer. A model with
    #: none starts from the one copy first picks (keyboard.source_entry), or DEFAULT_BRIGHTNESS.
    brightness: dict[str, int] = field(default_factory=dict)
    #: Colors the user chose to keep; nothing is added just for being tried.
    saved_colors: list[int] = field(default_factory=list)
    #: Game profile overlaying the M profile, by name, or None.
    active_game_profile: str | None = None
    #: The mouse follows the keyboard's lighting: its base color, live previews included,
    #: its brightness, the lock, and its idle dimming once the whole desktop is idle. A
    #: device preference, not a profile's: the mouse is one device whatever profile is active.
    mouse_sync_color: bool = False
    #: The keyboard model the mice follow, by keyboard.model_key or, written by hand, another
    #: of the model's ids (keyboard.model_ids), which a keyboard with no lighting or brightness
    #: of its own also starts from; None for the first attached since the daemon started.
    sync_keyboard: str | None = None
    #: The keyboard's lights, and a synced mouse's, go off while the screen is locked
    #: or blanked and before a suspend (the owner's decision of 2026-09-23).
    lights_off_when_locked: bool = True
    #: Idle dimming (IDLE_DIM_AFTER): seconds from the last keypress to the dim, and from
    #: the dim to lights off. never_dim turns both off; the keyboard's idle and wake
    #: notices are answered either way.
    idle_dim_after: int = IDLE_DIM_AFTER
    idle_off_after: int = IDLE_OFF_AFTER
    never_dim: bool = False
    #: A desktop notice with the new DPI when a DPI button steps it; a step within
    #: session.DPI_NOTICE_REPLACE of its post replaces it in place, a later one closes it and
    #: posts a new one (the owner's decisions of 2026-09-24 and 2026-10-18). Its own switch
    #: because GNOME's switch for the app would silence the battery notices too.
    dpi_notices: bool = True
    #: HOST mode for each mouse model in spy.SPY_MODELS (MouseModel.host_mode): while the
    #: daemon runs, the mouse is in HOST mode with 0x8110 armed and its buttons reach the
    #: daemon as the spy's masks; at a stop it goes back to ONBOARD mode, on profile 1 where
    #: its writes are tested (the owner's decision of 2026-09-24). Off, every mouse runs its
    #: onboard profile.
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
    #: The report rate last set live on each mouse model whose onboard profile Gearbench does
    #: not write (Dpi.live_rate), by product id as mouse_dpi is: sent at each arm of
    #: HOST mode in place of profile 1's, which the mouse keeps for when Gearbench stops
    #: (Claude Code's call of 2026-10-18, pending the owner).
    mouse_rate: dict[str, int] = field(default_factory=dict)
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
            "brightness": dict(sorted(self.brightness.items())),
            "saved_colors": [format_color(c) for c in self.saved_colors],
            "active_game_profile": self.active_game_profile,
            "mouse_sync_color": self.mouse_sync_color,
            "sync_keyboard": self.sync_keyboard,
            "lights_off_when_locked": self.lights_off_when_locked,
            "idle_dim_after": self.idle_dim_after,
            "idle_off_after": self.idle_off_after,
            "never_dim": self.never_dim,
            "dpi_notices": self.dpi_notices,
            "mouse_host_mode": self.mouse_host_mode,
            "tray": self.tray,
            "keyboard_link_notices": self.keyboard_link_notices,
            "mouse_dpi": self.mouse_dpi,
            "mouse_rate": self.mouse_rate,
            "profiles": {str(i): p.to_json() for i, p in sorted(self.profiles.items())},
        }

    @classmethod
    def from_json(cls, data: object) -> Config:
        if not isinstance(data, dict):
            raise ConfigError(f"config must be a JSON object, not {kind_of(data)}")
        if (problem := version_problem(data, VERSION, FIRST_VERSION)) is not None:
            raise ConfigError(f"config {problem}")
        version = data.get("version", FIRST_VERSION)
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
                                            f"Profile {i}", version)
        active = data.get("active_profile", 1)
        # An int check first: 2.0 is `in` PROFILE_IDS and would reach `1 << (which - 1)`.
        if not whole(active) or active not in PROFILE_IDS:
            raise ConfigError(f"active_profile must be {PROFILE_CHOICES}, "
                              f"not {written(active)}")
        if version == FIRST_VERSION:
            brightness = {V1_KEYBOARD: read_brightness(
                data.get("brightness", DEFAULT_BRIGHTNESS), "brightness")}
        else:
            brightness = by_model(data.get("brightness"), "brightness", read_brightness,
                                  KEYBOARD_MODEL, "keyboard")
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
        leader = data.get("sync_keyboard")
        if leader is not None:
            expect(leader, str, "sync_keyboard", f"{KEYBOARD_MODEL} or null")
            leader = product_key(leader, "sync_keyboard", KEYBOARD_MODEL)
        return cls(
            active=active, brightness=brightness, profiles=profiles, saved_colors=saved,
            active_game_profile=overlay or None,
            mouse_sync_color=flag(data, "mouse_sync_color", False),
            sync_keyboard=leader,
            lights_off_when_locked=flag(data, "lights_off_when_locked", True),
            never_dim=flag(data, "never_dim", False),
            dpi_notices=flag(data, "dpi_notices", True),
            mouse_host_mode=flag(data, "mouse_host_mode", True),
            tray=flag(data, "tray", True),
            keyboard_link_notices=flag(data, "keyboard_link_notices", True),
            mouse_dpi=mouse_dpi_map(data.get("mouse_dpi")),
            mouse_rate=mouse_rate_map(data.get("mouse_rate")),
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
        keep_old_version(target, config_backup)
        write_json(target, self.to_json(), ".config-")
        return target
