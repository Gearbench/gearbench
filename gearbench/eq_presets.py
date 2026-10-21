"""What the headsets' preset files share, g432-eq.json's and a50-eq.json's: a preset's name,
the most presets a file holds, where an action finds the preset a page read, a file's
version and its atomic write, each preset read on its own, and the words for a file that
does not read or was read in part. Standard library only.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol, cast

from . import astro, config, wording

#: The most presets a preset file holds: a bound of Gearbench's own, no device's, which keeps
#: the file small and its list short.
PRESETS_MAX = 16
#: Why no more presets can be saved.
PRESETS_FULL = f"The limit is {PRESETS_MAX} presets: delete one, or change one instead."
#: An action's refusal when the preset a list showed is not found in its file again: another
#: program changed the file since the list was read.
CHANGED = "The presets changed after {} was shown, so nothing was done."
#: Why a preset file read in part takes no change: a save would drop the presets left out, and
#: each is the user's own. The file's path, then what was left out.
PARTIAL = ("{} has presets Gearbench leaves out ({}), so it changes nothing in the file until "
           "they are fixed or taken out.")


class Named(Protocol):
    """A preset of either file, which number_named finds by its name."""

    @property
    def name(self) -> str: ...


def number_named(presets: Sequence[Named], number: int, name: str) -> int:
    """Where the preset `name`, which `number` held when a page read its file, is in
    `presets`, the file read again for an action, since another program can change it
    after that read: `number` while it holds a preset of that name, else the one preset
    of that name; ValueError in words, CHANGED, when none or more than one is. By name,
    since a page's own sliders change the gains."""
    if 1 <= number <= len(presets) and presets[number - 1].name == name:
        return number
    found = [held for held, preset in enumerate(presets, 1) if preset.name == name]
    if len(found) != 1:
        raise ValueError(CHANGED.format(name))
    return found[0]


def preset_name(name: object) -> str:
    """`name` if it is a preset's by the A50's rule (astro.preset_name), which an .astroeq
    file's and the A50's own names keep. ValueError in words for another."""
    astro.preset_name(name)
    return cast("str", name)


def is_name(name: object) -> bool:
    """Whether preset_name takes `name`."""
    try:
        preset_name(name)
    except ValueError:
        return False
    return True


def entries(data: object, version: int) -> list | None:
    """The `presets` of a preset file's `data` at `version`, [] where it holds no list of
    them, or None for data that is no object or of another version: true is no version,
    though Python counts it 1."""
    if not isinstance(data, dict) or not wording.whole(held := data.get("version")) \
            or held != version:
        return None
    found = data.get("presets")
    return found if isinstance(found, list) else []


def unread_why(data: object, version: int) -> str:
    """Why `data`, which entries gives None for at `version`, does not read, for a file's
    refusal (sentence)."""
    if not isinstance(data, dict):
        return f"it holds {wording.kind_of(data)}, not an object"
    if "version" not in data:
        return "it has no version"
    return f"version {wording.written(data['version'])} is not {version}"


def not_a_list(data: object) -> list[str]:
    """The reason for PARTIAL's list when the `presets` of a file's `data`, which entries
    read, are of another kind than a list, else none. Null holds none to lose, and entries
    gives no presets for another kind, so none is left out beside it."""
    held = cast("dict", data).get("presets")
    if held is None or isinstance(held, list):
        return []
    return [f"its presets are {wording.kind_of(held)}, not a list"]


def read_each[T](data: object, saved: list, read: Callable[[dict], T], skip: int = 0
                 ) -> tuple[list[tuple[int, T]], list[str]]:
    """Each preset of `saved`, the entries' list of `data`, past its first `skip`, as `read`
    takes it, with its number in the file, and what was left out, for PARTIAL: not_a_list's
    reason, each preset that is no object and each `read` refuses with a ValueError, in its
    words."""
    left_out = not_a_list(data)
    taken: list[tuple[int, T]] = []
    for number, entry in enumerate(saved[skip:], skip + 1):
        if not isinstance(entry, dict):
            left_out.append(f"preset {number} in the file is {wording.kind_of(entry)}, "
                            "not an object")
            continue
        try:
            taken.append((number, read(entry)))
        except ValueError as exc:
            left_out.append(f"preset {number} in the file: {str(exc).removesuffix('.')}")
    return taken, left_out


def sentence(text: str, path: Path, why: str) -> str:
    """`text`, a file's refusal such as PARTIAL, for `path` and `why`, cut to its two ends
    (wording.both_ends): what it quotes from the file can run to its megabyte, which the
    window lays out whole."""
    return wording.both_ends(text.format(config.shown_path(path), why))


def write(path: Path, version: int, fields: dict, presets: list[dict], prefix: str) -> None:
    """A preset file at `version`, with `fields` and then `presets`, to `path` atomically
    (config.write_json), its folder made first."""
    path.parent.mkdir(parents=True, exist_ok=True)
    config.write_json(path, {"version": version, **fields, "presets": presets}, prefix)
