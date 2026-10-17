"""The rules every library file keeps, a game profile's and a macro's alike.

A name's slug names its file, and every lookup goes by it (`slug`, `by_name`). A save or a
rename refuses to write over another's file (`refuse_taken`), and a delete that found no
file says so (`not_deleted`). A file's version and name are checked on the way in
(`check_version`, `read_name`). A folder is read so that a file that will not load costs
only itself, a folder that cannot be listed is not an empty one, and of two files of one
name one is kept (`read_folder`, `list_folder`, `Folder`).
"""

from __future__ import annotations

import hashlib
import os
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn, Protocol, cast

from .config import (
    ConfigError,
    SkipWarnings,
    expect,
    link_to_nothing,
    reason,
    refuse_name,
    shown_path,
    version_problem,
)

__all__ = ["SLUG_MAX_BYTES", "Folder", "Named", "Titled", "by_name", "check_version",
           "file_name", "list_folder", "not_deleted", "read_folder", "read_name",
           "refuse_taken", "slug"]


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


class Named(Titled, Protocol):
    """A game profile or a macro, as the code their two libraries share reads one."""

    #: Its file, set by the `load` that read it.
    path: Path | None


def refuse_taken(target: Path, load: Callable[[Path], Named], what: str) -> NoReturn:
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
        f"{target.name} already holds the {what} {other.name!r}; "
        f"rename or delete that one first"
    )


def not_deleted(name: str) -> str:
    """The refusal of a delete whose library found no file of `name` at the delete: once
    the command had found it, the file was removed, or changed to name another or to fail
    to load."""
    return (f"could not delete {name!r}: its file changed or was removed during the "
            "delete; look at the list again to see what is there now")


def check_version(data: dict, where: str, expected: int) -> None:
    """Refuse a library file of a format version other than `expected`, in
    config.version_problem's words."""
    if (problem := version_problem(data, expected)) is not None:
        raise ConfigError(f"{where}: {problem}")


def read_name(data: dict, where: str, what: str) -> str:
    """A library file's name, stripped: text, not blank, and one `refuse_name` passes."""
    # Text only: str() of a deeply nested list raises RecursionError or makes a huge name.
    name = data.get("name", "Unnamed")
    expect(name, str, f"{where}.name", "text")
    name = name.strip()
    if not name:
        raise ConfigError(f"{where}.name: a {what} needs a name")
    refuse_name(name, f"{where}.name")
    return name


@dataclass
class Folder[N: Named]:
    """What `read_folder` found, as `profiles.Library` and `macros.MacroLibrary` keep it."""

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


def read_folder[N: Named](directory: Path, load: Callable[[Path], N],
                          warnings: SkipWarnings) -> Folder[N]:
    """Read a folder of game profiles or of macros, one JSON file each, with `load`.

    The one reading rule of both libraries: a file that will not load costs only itself, a
    folder that cannot be listed is not an empty one, and of two files of one name one is
    kept. `warnings` says each file left out once.
    """
    folder: Folder[N] = Folder()
    try:
        paths = list_folder(directory)
    except OSError as exc:
        # None before the first save. A folder that is a link to nothing, or is in one, is
        # not that: its files are somewhere, and must not read as deleted.
        if isinstance(exc, FileNotFoundError) and link_to_nothing(directory) is None:
            return folder
        warnings.skip(directory, exc, folder=True)
        folder.unlisted_reason = reason(exc)
        # The row names the folder by its name alone, so the reason gives its path, in words.
        said = f"{shown_path(directory)} cannot be listed: {folder.unlisted_reason}"
        folder.skipped.append((directory.name, said))
        return folder
    warnings.read(directory)
    found = []
    for path in paths:
        try:
            found.append(load(path))
        except (ConfigError, OSError) as exc:
            # One bad file must not hide the rest, and is named once, or a profile that
            # stops loading looks deleted and a macro that stops working like a lost binding.
            warnings.skip(path, exc)
            folder.skipped.append((path.name, reason(exc)))
    # One file per slug, since every lookup goes by slug: a second file with the same name,
    # a file manager's copy say, could be listed but never addressed, and one that sorts
    # first would be read in place of the file `file_name` gives and block writing it.
    # `<slug>.json` wins, else the first by filename.
    # Each item has a path: the `load` that returned it set it (GameProfile.load, Macro.load).
    keep: dict[str, N] = {}
    for item in found:
        key = slug(item.name)
        if key not in keep or cast(Path, item.path).name == file_name(item.name):
            keep[key] = item
    for item in found:
        winner, file = keep[slug(item.name)], cast(Path, item.path)
        if winner is item:
            folder.kept.append(item)
            warnings.read(file)
            continue
        duplicate = ConfigError(
            f"the same name as {cast(Path, winner.path).name}, which is read instead")
        warnings.skip(file, duplicate)
        folder.skipped.append((file.name, str(duplicate)))
        folder.shadowed.add(file.name)
    return folder
