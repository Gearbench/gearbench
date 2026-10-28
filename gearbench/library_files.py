"""The rules every library file keeps, a game profile's and a macro's alike.

A name's slug names its file, and every lookup goes by it (`slug`, `by_name`), as does the
numbering that makes an imported name unique (`Taken`). A save or a rename refuses to write
over another's file (`refuse_taken`), a delete that found no file says so
(`not_deleted`), and a lookup that missed a name names the files that may hold it
(`may_hold_it`). A file's version and name are checked on the way in
(`check_version`, `read_name`). A folder is read so that a file that will not load costs
only itself, a folder that cannot be listed is not an empty one, and of two files of one
name one is kept (`read_folder`, `list_folder`, `Folder`); a library keeps what its last read
left out (`FolderLibrary`); each file left out is said once (`SkipWarnings`), its reason
without its name for a row titled with it (`unread_reason`). A rename writes the new file and
removes the old one (`save_renamed`): an old file that is a link moves, and one that cannot
be removed refuses the rename.
"""

from __future__ import annotations

import hashlib
import logging
import os
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn, Protocol

from .config import expect, refuse_name
from .settings_files import (
    ConfigError,
    link_to_nothing,
    shown_path,
    sync_removal,
    version_problem,
    write_json,
)
from .wording import escaped, quoted, reason, word_list

__all__ = ["SLUG_MAX_BYTES", "Folder", "FolderLibrary", "SkipWarnings", "Taken", "Titled",
           "by_name", "check_version", "file_name", "list_folder", "may_hold_it", "not_deleted",
           "read_folder", "read_name", "refuse_taken", "save_renamed", "slug", "unread_reason"]

log = logging.getLogger("gearbench")


#: The longest stem in UTF-8 bytes, a long name's hash suffix included: a filename is at
#: most 255, and ".json" goes after it.
SLUG_MAX_BYTES = 200


def file_name(name: str) -> str:
    """The file `name` calls for in a library folder, which the library reads of two files
    of that name (read_folder). A game profile read from a file saves back to it, not to
    this one."""
    return f"{slug(name)}.json"


def slug(name: str) -> str:
    """A filesystem-safe stem for a profile or macro name.

    Names are user text that become filenames, so anything that could escape the directory
    is stripped. Letters and digits in any script are kept, and a combining mark after one,
    so "कि" and "का" stay apart. The name is NFC-normalized and casefolded first.

    A name with no letters or digits gets a short hash of that text with spaces collapsed, so
    two such names stay apart and each matches its own spelling in another case or spacing.
    A stem over SLUG_MAX_BYTES is cut and given a hash of the whole stem, so long names that
    share a start stay apart.
    """
    text = unicodedata.normalize("NFC", unicodedata.normalize("NFC", name).casefold())
    kept, after_letter = [], False
    for c in text:
        # A mark stays only after a kept character, or a dropped symbol leaves an invisible
        # one in the stem, as the variation selector of "Doom ⚔️".
        if c.isalnum() or (after_letter and unicodedata.category(c).startswith("M")):
            kept.append(c)
            after_letter = True
        else:
            kept.append(" ")
            after_letter = False
    cleaned = "-".join("".join(kept).split())
    if not cleaned:
        # The hash is of the normalized text, so "ⓐ" finds "Ⓐ" as "a" finds "A". A lone
        # surrogate, argv's for a byte that is not UTF-8, is hashed as itself: as "?" it
        # would find the profile named "?".
        digest = hashlib.sha1(" ".join(text.split()).encode(errors="surrogatepass"),
                              usedforsecurity=False).hexdigest()[:8]
        return f"profile-{digest}"
    if len(cleaned.encode()) <= SLUG_MAX_BYTES:
        return cleaned
    # Of the stem, not the name, so a long name matches itself in another case or spacing.
    digest = hashlib.sha1(cleaned.encode(), usedforsecurity=False).hexdigest()[:8]
    cut = cleaned.encode()[:SLUG_MAX_BYTES - len(digest) - 1].decode(errors="ignore")
    return f"{cut.rstrip('-')}-{digest}"


class Titled(Protocol):
    """A game profile, a macro or a G HUB profile, as a lookup by name reads one."""

    name: str


def by_name[T: Titled](items: Iterable[T], name: str) -> T | None:
    """The first of `items` that `name` names: every lookup goes by slug."""
    wanted = slug(name)
    return next((item for item in items if slug(item.name) == wanted), None)


class Taken:
    """Names given so far, as the library compares them, and where `unique`
    stopped counting for each numbered form. Kept across calls: rebuilt for
    each name and counted from (2) each time, they would make the G HUB
    import's parse quadratic in the profiles of one application."""

    def __init__(self, names: Iterable[str] = ()) -> None:
        self._slugs = {slug(name) for name in names}
        self._counts: dict[str, int] = {}

    def add(self, name: str) -> None:
        self._slugs.add(slug(name))

    def unique(self, wanted: str) -> str:
        """`wanted`, or `wanted (2)`, `(3)`... whichever no name given so far
        has, compared as the library compares them. The count starts where it
        stopped for names whose numbered forms share a slug: a Taken only
        grows, so the names it passed over are still taken."""
        if slug(wanted) not in self._slugs:
            return wanted
        # Keyed by the numbered form, not `wanted`: a name with no letter or digit
        # slugs as a hash of itself, but "!!! (2)" and "@@@ (2)" both slug as "2".
        key = slug(f"{wanted} (n)")
        n = self._counts.get(key, 2)
        while slug(f"{wanted} ({n})") in self._slugs:
            n += 1
        self._counts[key] = n
        return f"{wanted} ({n})"


def refuse_taken(target: Path, load: Callable[[Path], Titled], what: str) -> NoReturn:
    """Raise ConfigError for a file a save or a rename must not write over: that of
    another `what` ("game profile" or "macro"), named, or one `load` could not read."""
    try:
        other = load(target)
    except (ConfigError, OSError):
        raise ConfigError(
            f"{target.name} already exists and is not a {what} Gearbench "
            f"can read; fix or remove it first"
        ) from None
    raise ConfigError(
        f"{target.name} already holds the {what} {quoted(other.name)}; "
        f"rename or delete that one first"
    )


def _write_renamed(old: Path | None, target: Path, payload: dict, prefix: str) -> None:
    """Write `payload` to `target`, the file its name calls for, as write_json does; `old` is
    the file it was read from, if any. Where `old` is another file and a link, the link moves
    to `target`, still naming its file, and the write goes through it, as a save's does: a
    file of its own at `target` would leave the linked file behind, holding the old name. The
    link moves before the write, and back if the write fails. Over a file at `target`, the
    macro a rename replaces, it moves after the write instead, since that file goes only once
    the write has succeeded; a move that fails then leaves the new contents under the old file
    name."""
    if old is None or old == target or not old.is_symlink():
        write_json(target, payload, prefix)
    elif target.exists(follow_symlinks=False):
        write_json(old, payload, prefix)
        os.replace(old, target)
    else:
        os.rename(old, target)
        try:
            write_json(target, payload, prefix)
        except BaseException:
            os.rename(target, old)
            raise


def _remove_renamed(old: Path, target: Path, written_over: bool) -> None:
    """Remove `old`, the file a renamed game profile or macro was read from, once
    `_write_renamed` has written `target`. Where `old` cannot be removed, its error is raised
    and `target` goes too, so the rename is refused whole and no two files hold the one
    profile or macro, unless `written_over`: `target` was the file of the macro a rename
    replaces, whose contents are gone already, and it stays."""
    try:
        old.unlink(missing_ok=True)     # gone already where it was a link and moved
    except OSError:
        if not written_over:
            target.unlink(missing_ok=True)
            sync_removal(target)
        raise


def save_renamed(old: Path | None, target: Path, payload: dict, prefix: str,
                 written_over: bool = False) -> None:
    """A rename's write, a game profile's and a macro's alike: `payload` to `target`
    (_write_renamed), then, where `old` is another file, `old` removed (_remove_renamed, which
    `written_over` is passed to) and its removal synced (settings_files.sync_removal)."""
    _write_renamed(old, target, payload, prefix)
    if old is not None and old != target:
        _remove_renamed(old, target, written_over)
        sync_removal(old)


def may_hold_it(files: list[str]) -> str:
    """"a.json and b.json will not load, and may hold it": the files a lookup by name could
    not read, any of which may hold the name it missed, since a file's stem need not be the
    name's slug."""
    return f"{word_list(files, 'and')} will not load, and may hold it"


def not_deleted(name: str) -> str:
    """The refusal of a delete whose library found no file of `name` at the delete: once
    the command had found it, the file was removed, or changed to name another or to fail
    to load."""
    return (f"could not delete {quoted(name)}: its file changed or was removed during the "
            "delete; look at the list again to see what is there now")


def check_version(data: dict, where: str, expected: int, oldest: int | None = None) -> int:
    """A library file's format version: `expected`, or one from `oldest` where an older one is
    still read. Any other is refused in settings_files.version_problem's words."""
    first = expected if oldest is None else oldest
    if (problem := version_problem(data, expected, first)) is not None:
        raise ConfigError(f"{where}: {problem}")
    return data.get("version", first)


def read_name(data: dict, where: str, needs_a_name: str) -> str:
    """A library file's name, stripped: text, not blank, and one `refuse_name` passes. A blank
    one is refused in `needs_a_name`, the wording module's sentence for its kind."""
    # Text only: str() of a deeply nested list raises RecursionError or makes a huge name.
    name = data.get("name", "Unnamed")
    expect(name, str, f"{where}.name", "text")
    name = name.strip()
    if not name:
        raise ConfigError(f"{where}.name: {needs_a_name}")
    refuse_name(name, f"{where}.name")
    return name


@dataclass
class Folder[N: Titled]:
    """What `read_folder` found, as `FolderLibrary` keeps it."""

    #: One per name, in file name order.
    kept: list[N] = field(default_factory=list)
    #: (file name, why) for each file left out. A folder it could not list is one entry,
    #: under its own name, its path and reason in words.
    skipped: list[tuple[str, str]] = field(default_factory=list)
    #: Why the folder could not be listed, in words, or None.
    unlisted_reason: str | None = None
    #: The files in `skipped` that loaded, left out for another file of the same name.
    shadowed: set[str] = field(default_factory=set)


def list_folder(directory: Path) -> list[Path]:
    """The files `read_folder` reads from a folder, sorted. os.scandir's OSError is raised.

    Not Path.glob, which reads an unlistable directory as empty, so every file looks
    deleted. Dotfiles are skipped: write_json's temporaries start with a dot
    (`.profile-*.json`, `.macro-*.json`), and one a killed write left would shadow the
    real file and block saving it.
    """
    with os.scandir(directory) as entries:
        return sorted(directory / e.name for e in entries
                      if e.name.endswith(".json") and not e.name.startswith("."))


def unread_reason(name: str, why: str) -> str:
    """A skipped file's reason without the file's name or path in front, for a line or a
    row that names the file itself: `<path>/doom.json: invalid JSON…` and
    `doom.json: version 99…` lose it, `doom.json.mode: …` keeps the field. A folder's
    entry (library_files.read_folder), which gives the folder's path in words, is left whole."""
    head, found, rest = why.partition(f"{name}: ")
    if found and (not head or head.endswith("/")):
        return rest
    # A field follows the name at the start alone: a folder in a path can begin with the name
    # and a dot (`profiles.old/`).
    return why.removeprefix(f"{name}.")


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

    def listed(self, paths: Sequence[Path]) -> None:
        """The folder was listed and holds `paths`, or is not there and holds none: every other
        path is forgotten, the folder's own among them, so a file skipped and since removed keeps
        no entry for as long as the daemon runs, and one put back is news."""
        for gone in self._said.keys() - set(paths):
            del self._said[gone]


def read_folder[N: Titled](directory: Path, load: Callable[[Path], N],
                           warnings: SkipWarnings) -> Folder[N]:
    """Read a folder of game profiles or of macros, one JSON file each, with `load`.

    The one reading rule of both libraries: a file that will not load costs only itself, a
    folder that cannot be listed is not an empty one, and of two files of one name one is
    kept. `warnings` says each file left out once, and forgets each that has gone.
    """
    folder: Folder[N] = Folder()
    try:
        paths = list_folder(directory)
    except OSError as exc:
        # None before the first save. A folder that is a link to nothing, or is in one, is
        # not that: its files are somewhere, and must not read as deleted.
        if isinstance(exc, FileNotFoundError) and link_to_nothing(directory) is None:
            # Gone with its folder, so each is news when the folder comes back as it was.
            warnings.listed([])
            return folder
        warnings.skip(directory, exc, folder=True)
        folder.unlisted_reason = reason(exc)
        # The row names the folder by its name alone, so the reason gives its path, in words.
        said = f"{shown_path(directory)} could not be listed ({folder.unlisted_reason})"
        folder.skipped.append((directory.name, said))
        return folder
    # The folder's own break, and each file's that has gone, forgotten: the daemon reads the
    # folder for as long as it runs.
    warnings.listed(paths)
    found: list[tuple[Path, N]] = []
    for path in paths:
        try:
            found.append((path, load(path)))
        except (ConfigError, OSError) as exc:
            # One bad file must not hide the rest, and is named once, or a profile that
            # stops loading looks deleted and a macro that stops working like a lost binding.
            warnings.skip(path, exc)
            folder.skipped.append((path.name, reason(exc)))
    # One file per slug, since every lookup goes by slug: a second file with the same name,
    # a file manager's copy say, could be listed but never addressed, and one that sorts
    # first would be read in place of the file `file_name` gives and block writing it.
    # `<slug>.json` wins, else the first by filename.
    keep: dict[str, Path] = {}
    for path, item in found:
        key = slug(item.name)
        if key not in keep or path.name == file_name(item.name):
            keep[key] = path
    for path, item in found:
        winner = keep[slug(item.name)]
        if winner == path:
            folder.kept.append(item)
            warnings.read(path)
            continue
        duplicate = ConfigError(f"the same name as {winner.name}, which is read instead")
        warnings.skip(path, duplicate)
        folder.skipped.append((path.name, str(duplicate)))
        folder.shadowed.add(path.name)
    return folder


class FolderLibrary[N: Titled]:
    """A library folder read through `read_folder`, and what its last read left out."""

    def __init__(self, directory: Path, what: str) -> None:
        self.directory = directory
        self._skipped = SkipWarnings(what)
        #: The last read's `Folder.skipped`.
        self.skipped: list[tuple[str, str]] = []
        #: The last read's `Folder.unlisted_reason`.
        self.unlisted_reason: str | None = None
        #: The last read's `Folder.shadowed`: files that loaded, so they hold no item a
        #: lookup by name misses.
        self.shadowed: set[str] = set()

    def read(self, load: Callable[[Path], N]) -> list[N]:
        """The folder's items, one per name, each read by `load`; what it left out is kept."""
        folder = read_folder(self.directory, load, self._skipped)
        self.skipped, self.unlisted_reason = folder.skipped, folder.unlisted_reason
        self.shadowed = folder.shadowed
        return folder.kept

    def unread(self) -> list[tuple[str, str]]:
        """The entries of `skipped` the last read could not read: a file that will not load,
        or the folder. Not those in `shadowed`, which loaded."""
        return [(file, why) for file, why in self.skipped if file not in self.shadowed]
