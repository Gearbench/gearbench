"""What the headsets' preset files share, g432-eq.json's, a50-eq.json's and each model's
library's: the shipped presets' names, a preset's name, the most presets a file holds, where
an action finds the preset a page read, a file's version and its atomic write, each preset
read on its own, and the words for a file that does not read or was read in part; and a
library's, a50-eq.json's and a model's (LIBRARY_NAME), a Centurion headset's or a G432
family model's with an equalizer of its own: its presets and their changes (Saved), its read
and its checked write. Standard library only.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, ClassVar, Protocol, Self, cast

from . import astro, settings_files, wording

#: The names of the three presets Gearbench ships for a headset, each model's tuned to its
#: own sound (the owner's decision of 2026-10-22: "Name them Everyday, Gaming, Media").
EVERYDAY, GAMING, MEDIA = "Everyday", "Gaming", "Media"
#: A model's presets file in the settings folder, by the model's key: its library's, or
#: g432_presets' for a model of the G432's family with no equalizer of its own.
LIBRARY_NAME = "{}-eq.json"
#: The most presets a preset file holds: a bound of Gearbench's own, no device's, which keeps
#: the file small and its list short.
PRESETS_MAX = 16
#: Why no more presets can be saved.
PRESETS_FULL = f"the limit is {PRESETS_MAX} presets: delete one, or change one instead"
#: Why a number names no preset of a library, by how many there are: none, one, or more.
NO_PRESETS = "your library has no presets yet"
NOT_THE_PRESET = "your library has one preset, number 1, not {}"
NOT_A_PRESET = "a preset of your library is a number from 1 to {}, not {}"
#: An action's refusal when the preset a list showed is not found in its file again: another
#: program changed the file since the list was read.
CHANGED = "the presets changed after {} was shown, so the change was not made"
#: Why a preset file read in part takes no change: a save would drop the presets left out, and
#: each is the user's own. The file's path, then what was left out.
PARTIAL = ("{} has presets Gearbench leaves out ({}), so Gearbench changes nothing in it "
           "until they are fixed or taken out")
#: Why a library that does not read is left as it is: every preset in it is the user's own,
#: so it is never written over. The file's path, then the cause.
UNREAD = ("{} could not be read ({}), so Gearbench leaves it as it is: fix it, or move it "
          "away to start the library over")


class LibraryUnread(settings_files.ConfigError):
    """A library is there and does not read: UNREAD's sentence, or the one load_library is
    given."""


class LibraryPartial(settings_files.ConfigError):
    """A library read with presets left out, which refuses a change: PARTIAL's sentence."""


class Named(Protocol):
    """A preset of any file, which number_named finds by its name."""

    @property
    def name(self) -> str: ...


class Record(Named, Protocol):
    """A preset a library holds: a dataclass with a name, which Saved.named replaces, and
    the daemon's request that sends it."""

    # dataclasses.replace takes any dataclass, typeshed's DataclassInstance, whose fields are
    # each of its own type.
    __dataclass_fields__: ClassVar[dict[str, Any]]

    def request(self) -> dict: ...


@dataclass(frozen=True)
class Saved[T: Record]:
    """A library's presets, preset 1 first, at most PRESETS_MAX, and `left_out`, PARTIAL's
    sentence for what the file held that did not read, empty for a file read whole or a
    library made here."""

    presets: tuple[T, ...] = ()
    left_out: str = ""

    @property
    def numbers(self) -> tuple[int, ...]:
        return tuple(range(1, len(self.presets) + 1))

    def number(self, value: object) -> int:
        """`value` if it is one of these presets' numbers, else ValueError in words."""
        if not self.presets:
            raise ValueError(NO_PRESETS)
        if not wording.whole(value) or value not in self.numbers:
            written = wording.written(value)
            raise ValueError(NOT_THE_PRESET.format(written) if len(self.presets) == 1
                             else NOT_A_PRESET.format(len(self.presets), written))
        return value

    def get(self, number: int) -> T:
        return self.presets[number - 1]

    def whole(self) -> Self:
        """These presets, or LibraryPartial while the file left some out: a save would drop
        them."""
        if self.left_out:
            raise LibraryPartial(self.left_out)
        return self

    def has_room(self) -> Self:
        """These presets, or ValueError at PRESETS_MAX, which leaves no room for one more."""
        if len(self.presets) >= PRESETS_MAX:
            raise ValueError(PRESETS_FULL)
        return self

    def added(self, preset: T) -> Self:
        """These presets with `preset` after the last; ValueError at PRESETS_MAX (has_room),
        or for a name preset_name refuses."""
        preset_name(preset.name)
        return type(self)((*self.has_room().presets, preset))

    def put(self, number: int, preset: T) -> Self:
        """These presets with `preset` in `number`'s place."""
        presets = list(self.presets)
        presets[self.number(number) - 1] = preset
        return type(self)(tuple(presets))

    def deleted(self, number: int) -> Self:
        """These presets without `number`, those after it a number lower."""
        index = self.number(number) - 1
        return type(self)(self.presets[:index] + self.presets[index + 1:])

    def named(self, number: int, name: str) -> Self:
        return self.put(number, replace(self.get(self.number(number)), name=preset_name(name)))


def number_named(presets: Sequence[Named], number: int, name: str) -> int:
    """Where the preset `name`, which `number` held when a page read its file, is in
    `presets`, the file read again for an action, since another program can change it
    after that read: `number` while it holds a preset of that name, else the one preset
    of that name; ValueError in words, CHANGED, when none or more than one is. By name,
    since a page's own sliders change the gains."""
    return number_among(list(enumerate((preset.name for preset in presets), 1)), number, name)


def number_among(named: Sequence[tuple[int, str]], number: int, name: str) -> int:
    """number_named over `named`, the number and name of each preset the action may take:
    an equalizer's own, in a library that holds two equalizers' presets
    (a50x_library.Library.of), where a name can be the other's too."""
    if (number, name) in named:
        return number
    found = [held for held, each in named if each == name]
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
    reason, each preset that is no object, each `read` refuses with a ValueError, in its
    words, and those read past PRESETS_MAX, the first `skip` counted."""
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
    if (past := skip + len(taken) - PRESETS_MAX) > 0:
        left_out.append(f"{wording.counted(past, 'preset')} past the {PRESETS_MAX} a file "
                        "holds")
    return taken[:PRESETS_MAX - skip], left_out


def sentence(text: str, path: Path, why: str) -> str:
    """`text`, a file's refusal such as PARTIAL, for `path` and `why`, cut to its two ends
    (wording.both_ends): what it quotes from the file can run to its megabyte, which the
    window lays out whole."""
    return wording.both_ends(text.format(settings_files.shown_path(path), why))


def write(path: Path, version: int, fields: dict, presets: list[dict], prefix: str) -> None:
    """A preset file at `version`, with `fields` and then `presets`, to `path` atomically
    (settings_files.write_json), its folder made first (settings_files.make_folder)."""
    settings_files.make_folder(path)
    settings_files.write_json(path, {"version": version, **fields, "presets": presets}, prefix)


def library_of[T](data: object, path: Path, version: int, read: Callable[[dict], T]
                  ) -> tuple[tuple[T, ...], str]:
    """The presets of a library's `data` at `version`, each read on its own by `read` (read_each),
    up to PRESETS_MAX, and PARTIAL's sentence for a `presets` that is neither a list nor
    null, each preset that does not read and those past PRESETS_MAX, or "" for none.
    LibraryUnread for data of another shape or version."""
    saved = entries(data, version)
    if saved is None:
        raise LibraryUnread(sentence(UNREAD, path, unread_why(data, version)))
    taken, left_out = read_each(data, saved, read)
    return (tuple(preset for _number, preset in taken),
            sentence(PARTIAL, path, "; ".join(left_out)) if left_out else "")


def load_library[L](path: Path, of: Callable[[object, Path], L], empty: L,
                    unread: str = UNREAD) -> L:
    """The library at `path` as `of` reads its data, `empty` with no file. LibraryUnread,
    `unread`'s sentence, for a file that does not read, and for one behind a link to nothing,
    the file's or a folder's above it (settings_files.link_to_nothing): its presets are somewhere,
    and a save would write `empty` and its change at the link's target."""
    try:
        data = settings_files.read_json(path)
    except FileNotFoundError:
        if (place := settings_files.link_to_nothing(path)) is None:
            return empty
        why = settings_files.link_gone(place, named=place != path)
        raise LibraryUnread(sentence(unread, path, why)) from None
    except (OSError, settings_files.ConfigError) as exc:
        why = settings_files.unprefixed(wording.reason(exc), path)
        raise LibraryUnread(sentence(unread, path, why)) from None
    return of(data, path)


def save_library[T](path: Path, version: int, presets: Sequence[T], entry: Callable[[T], dict],
                    read: Callable[[dict], object], prefix: str) -> None:
    """A library's `presets` to `path` atomically (write), each one's `entry` read back by
    `read` first, which raises its ValueError."""
    saved = [entry(preset) for preset in presets]
    for each in saved:
        read(each)
    write(path, version, {}, saved, prefix)
