"""The files Gearbench keeps in its settings folder, ~/.config/gearbench: where they are
(`config_dir`, `backup_dir`), how one is read (`read_capped`, `read_json`, `from_file`) and
written (`write_json`, `replace_file`, `replace_synced`, `make_folder`), the hand-edit check
(`file_stamp`, `changed_on_disk`), a link to nothing (`link_to_nothing`), an older format's
bytes kept before a save (`keep_old_version`), the format-version rule (`version_problem`)
and the errors (`ConfigError`, `FileChanged`, `FileUnreadable`). On the standard library,
`wording` and the file rules' `json_text`, `regular_file` and `runtime_folder` alone, so a
module that keeps a file of its own loads none of config.json's vocabulary.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

from .json_text import TOO_DEEP, JsonRefused, json_value
from .regular_file import open_regular
from .runtime_folder import make_private, missing_folders
from .wording import MAX_REFUSAL, both_ends, escaped, reason, whole, word_list, written

log = logging.getLogger("gearbench")

#: The first version of each file format with a newer one: the oldest one read or backed up,
#: and the one a file without `version` is.
FIRST_VERSION = 1
#: The most read_json takes from one file. What Gearbench writes is a few
#: kilobytes, so anything past this is not one of its files.
MAX_JSON_BYTES = 1 << 20


def config_home() -> Path:
    """XDG_CONFIG_HOME, or ~/.config when it is unset, empty or relative, as the XDG
    spec and the unit's %E have it. %E reads the user manager's own, so the unit's
    %E/gearbench can differ from config_dir() (cli.service.cmd_daemon)."""
    base = Path(os.environ.get("XDG_CONFIG_HOME") or "")
    return base if base.is_absolute() else Path.home() / ".config"


def config_dir() -> Path:
    return config_home() / "gearbench"


def shown_path(path: Path) -> str:
    """`path` as the window names it, the home folder written ~."""
    try:
        return f"~/{path.relative_to(Path.home())}"
    except ValueError:
        return str(path)


def backup_dir() -> Path:
    """The settings folder's backups: the mouse's onboard sectors before each write
    (onboard.py), and a file's bytes in an older format before its first save in its newest
    (keep_old_version)."""
    return config_dir() / "backup"


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


def link_gone(place: Path, named: bool = True) -> str:
    """`place`, a link to nothing (link_to_nothing), in words; `named` False leaves out its
    path, for a sentence that has just given it."""
    link = f"{place} is a link" if named else "a link"
    return f"{link} to {os.readlink(place)}, which is not there"


def missing_behind(target: Path, place: Path) -> str:
    """Why `target` cannot be read or written: `place`, it or a folder above it, is a
    link to nothing (link_to_nothing)."""
    return f"{target}: {link_gone(place, named=place != target)}"


def make_folder(target: Path) -> None:
    """Make the folder `target` is saved in, where it is not there, and each missing one
    above it, each at PRIVATE_FOLDER (make_private), and sync each folder made into the
    folder it is in, since a folder made, and the file saved in it, is on disk only once that
    one is synced (fsync(2)); the file's own write syncs only its folder. A mkdir or a sync
    that fails is raised before anything is written, the folders made removed, as
    replace_synced removes its own: left, the next save would take them as on disk and sync
    none of their parents. A folder that is, or sits behind, a link to nothing is a
    ConfigError naming the link: mkdir fails it as "File exists", naming neither the link
    nor where it points."""
    if (place := link_to_nothing(target.parent)) is not None:
        raise ConfigError(f"{missing_behind(target, place)}; nothing is saved")
    made = missing_folders(target.parent)
    try:
        make_private(target.parent, made)
        for folder in made:
            sync_directory(folder.parent)
    except OSError:
        remove_leftovers(folders_left(made))
        raise


def folders_left(made: Sequence[Path]) -> list[tuple[Path, Callable[[], None]]]:
    """The folders of `made` there now, deepest first, each with its removal: a mkdir that
    failed may have stopped short of one."""
    return [(folder, folder.rmdir) for folder in made if folder.exists(follow_symlinks=False)]


def remove_leftovers(leftovers: Sequence[tuple[Path, Callable[[], None]]]) -> None:
    """Remove what a write that failed left, a removal that fails a warning, so it is the
    write's failure that is raised."""
    for leftover, remove in leftovers:
        try:
            remove()
        except OSError as exc:
            log.warning("%s could not be removed after a write that failed (%s)",
                        escaped(leftover), reason(exc))


#: The end of a refusal over a file that could not be read: what to do, the config banner's
#: words for the window's own read, with the command that reloads it.
FIX_AND_RELOAD = "fix it, then reload it (`gearbench-ctl reload`)"


def changed_on_disk(path: Path, save: bool = True) -> ConfigError:
    """The refusal for a file edited since it was read. `save` False is a macro's file,
    which a delete leaves too, and leaves out `gearbench-ctl save`, which writes no
    macro: the daemon holds no copy of one."""
    kept = "nothing is saved over it" if save else "it is not saved over or deleted"
    return FileChanged(
        f"{path} changed on disk since the daemon read it; {kept}; "
        f"`gearbench-ctl reload` takes your edit"
        + (", and `gearbench-ctl save` writes the daemon's copy over it" if save else "")
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
BACKUP_REFUSED = ("{target} is not saved; its settings from version {version} could not be "
                  "copied to {kept} first ({why})")


def keep_old_version(target: Path, backup: Callable[[int], Path], current: int) -> None:
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
        old = json_value(data)
    except (FileNotFoundError, ValueError):
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
    there, and each missing one above it, each at PRIVATE_FOLDER (make_private), then that
    folder synced and the folder each one made is in, since a folder made is on disk only
    once the folder it is in is synced (fsync(2)). On an
    OSError, `target`, once replace_file has put it in place, and the folders made are
    removed before it is raised: left, the next try would take them as on disk and sync none
    of their parents. A removal that fails is a warning, so it is the write's failure that
    is raised. A folder behind a link to nothing (link_to_nothing) is an OSError naming the
    link, before anything is made."""
    # mkdir fails such a folder as "File exists", naming neither the link nor where it points.
    if (place := link_to_nothing(target.parent)) is not None:
        raise OSError(link_gone(place))
    made = missing_folders(target.parent)
    placed = False
    try:
        make_private(target.parent, made)
        replace_file(target, data, prefix, suffix)
        placed = True
        sync_directory(target.parent)
        for folder in made:
            sync_directory(folder.parent)
    except OSError:
        remove_leftovers(([(target, target.unlink)] if placed else []) + folders_left(made))
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


def read_capped(path: str | os.PathLike[str], limit: int) -> bytes:
    """A regular file's bytes, a link to one included (open_regular), or a ConfigError
    naming it if it is anything else or holds more than `limit` bytes.

    The daemon reads its libraries on its one thread, where a FIFO would block it for
    good and a link to /dev/zero grow it until killed. Run as the unit, the daemon leads
    its session with no terminal, so a terminal no session holds, opened without
    O_NOCTTY, would become its own, and a ^C byte or a hangup on it would stop the daemon.
    open_regular opens none of them. OSError passes through.
    """
    if (fd := open_regular(path)) is None:
        raise ConfigError(f"{path}: not a regular file")
    try:
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

    Read as bytes, which json_text.json_value decodes as UTF-8, UTF-16 or UTF-32, and
    anything else is a ConfigError, where read_text() would raise a UnicodeDecodeError no
    caller expects. So is a file nested too deeply, not regular, over MAX_JSON_BYTES, or
    with an object that names a key twice. OSError passes through, so a caller can tell a
    missing file from a bad one.
    """
    data = read_capped(path, MAX_JSON_BYTES)
    try:
        return json_value(data)
    except JsonRefused as exc:
        raise ConfigError(f"{path}: {exc}") from None


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
        raise ConfigError(f"{path}: {TOO_DEEP}") from None


def unprefixed(why: str, path: str | os.PathLike[str]) -> str:
    """`why` without the `<path>: ` a refusal of the file at `path` starts with, for a
    message that names the file already."""
    return why.removeprefix(f"{path}: ")


def version_problem(data: dict, expected: int, oldest: int) -> str | None:
    """Why a file's `version` is not one from `oldest` to `expected`, `expected` alone where
    no older one is still read, in words, or None. A file without one is `oldest`."""
    version = data.get("version", oldest)
    taken = word_list([str(n) for n in range(oldest, expected + 1)])
    # written() gives a list or an object by its kind, which "version ... is not supported"
    # can't hold.
    if isinstance(version, (list, dict)):
        return f"version is {taken}, not {written(version)}"
    # true == 1 and 1.0 == 1, so the type is checked first.
    if not whole(version) or not oldest <= version <= expected:
        return f"version {written(version)} is not supported (expected {taken})"
    return None
