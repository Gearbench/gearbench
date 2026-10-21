"""The Astro A50's preset library: presets Gearbench keeps in a50-eq.json, apart from the base
station's three slots, each a name and five bands as the base station takes them
(astro.EqPreset). The window and the CLI read and write the file; the daemon never does. A
preset written to a slot goes to the base station's memory through the daemon's `headset_eq`,
as any edit does. The slots' defaults, which Reset to default writes, are here too
(DEFAULT_SLOTS). Standard library only.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

from . import astro, config, eq_presets, wording

LIBRARY_NAME = "a50-eq.json"
#: a50-eq.json's version.
LIBRARY_VERSION = 1
#: Why a library that does not read is left as it is: every preset in it is the user's own,
#: so it is never written over. The file's path, then the cause.
UNREAD = ("{} could not be read ({}), so Gearbench leaves it as it is: fix it, or move it "
          "away to start an empty library.")
#: The three slots' defaults, which Reset to default writes to a slot, slot 1 first: the A50
#: EQ research's three presets of 2026-09-27, designed against Harman over-ear 2018 from
#: published measurements of the A50 Gen 4 and a model of the base station's filters
#: (eq-presets-report.md and its .astroeq files), in the slots the owner's base station held
#: them in on 2026-10-18 (a50-presets-before.json), both outside the repository.
DEFAULT_SLOTS = (
    astro.EqPreset("Rock and Alt", (4, -7, 2, 5, 3), (80, 180, 2700, 4400, 12000),
                   (1.6, 0.5, 0.3)),
    astro.EqPreset("FPS Spatial", (-5, -4, 5, 4, 1), (150, 220, 2500, 4300, 12000),
                   (1.5, 0.7, 0.3)),
    astro.EqPreset("Everyday", (2, -5, 3, 4, 2), (80, 180, 2700, 4300, 12000),
                   (1.5, 0.5, 0.4)),
)
#: Why a number names no preset of the library, by how many it holds.
NO_PRESETS = "Your library has no presets yet."
NOT_A_PRESET = "A preset of your library is a number from 1 to {}, not {}."
#: A preset's refusal for a band it lacks or one with no frequency: the band's number.
NO_BAND = "Band {} is missing or has no frequency."


class LibraryUnread(config.ConfigError):
    """a50-eq.json is there and does not read: UNREAD's sentence."""


class LibraryPartial(config.ConfigError):
    """a50-eq.json read with presets left out, which refuses a change: eq_presets.PARTIAL's
    sentence."""


@dataclass(frozen=True)
class Library:
    """The library's presets, preset 1 first, at most eq_presets.PRESETS_MAX, and `left_out`,
    eq_presets.PARTIAL's sentence for what the file held that did not read, empty for a file
    read whole or a library made here."""

    presets: tuple[astro.EqPreset, ...] = ()
    left_out: str = ""

    @property
    def numbers(self) -> tuple[int, ...]:
        return tuple(range(1, len(self.presets) + 1))

    def number(self, value: object) -> int:
        """`value` if it is one of these presets' numbers, else ValueError in words."""
        if not self.presets:
            raise ValueError(NO_PRESETS)
        if not wording.whole(value) or value not in self.numbers:
            raise ValueError(NOT_A_PRESET.format(len(self.presets), wording.written(value)))
        return value

    def get(self, number: int) -> astro.EqPreset:
        return self.presets[number - 1]

    def whole(self) -> Library:
        """These presets, or LibraryPartial while the file left some out: a save would drop
        them."""
        if self.left_out:
            raise LibraryPartial(self.left_out)
        return self

    def has_room(self) -> Library:
        """These presets, or ValueError at PRESETS_MAX, which leaves no room for one more."""
        if len(self.presets) >= eq_presets.PRESETS_MAX:
            raise ValueError(eq_presets.PRESETS_FULL)
        return self

    def added(self, preset: astro.EqPreset) -> Library:
        """These presets with `preset` after the last; ValueError at PRESETS_MAX (has_room),
        or for a name eq_presets.preset_name refuses."""
        eq_presets.preset_name(preset.name)
        return Library((*self.has_room().presets, preset))

    def put(self, number: int, preset: astro.EqPreset) -> Library:
        """These presets with `preset` in `number`'s place."""
        presets = list(self.presets)
        presets[self.number(number) - 1] = preset
        return Library(tuple(presets))

    def deleted(self, number: int) -> Library:
        """These presets without `number`, those after it a number lower."""
        index = self.number(number) - 1
        return Library(self.presets[:index] + self.presets[index + 1:])

    def named(self, number: int, name: str) -> Library:
        return self.put(number, replace(self.get(self.number(number)),
                                        name=eq_presets.preset_name(name)))


def default_slot(slot: int) -> astro.EqPreset:
    """Slot `slot`'s default, 1 to 3, which Reset to default writes."""
    return DEFAULT_SLOTS[astro.PRESETS.index(slot)]


def library_path() -> Path:
    return config.config_dir() / LIBRARY_NAME


def entry(preset: astro.EqPreset) -> dict:
    """A preset as a50-eq.json keeps it: `headset_eq`'s name, gains and bands for it."""
    return {key: value for key, value in preset.request().items() if key != "command"}


def _check(name: object, gains: object, bands: dict[int, object],
           named: bool = True) -> astro.EqPreset:
    """The preset of `name`, `gains` and `bands`, each band by its number, each value as the
    daemon's own checks take it (astro.preset_name, the name only when `named`, preset_gains
    and band_width). ValueError with their sentence for a value they refuse, or NO_BAND."""
    if named:
        astro.preset_name(name)
    astro.preset_gains(gains)
    found: list[astro.Band] = []
    for number in astro.BANDS:
        band = bands.get(number)
        if not isinstance(band, dict) or band.get("freq") is None:
            raise ValueError(NO_BAND.format(number))
        astro.band_width(number, band.get("freq"), band.get("width"))
        # band_width took a whole frequency and, off the shelves, a number for the width.
        found.append(astro.Band(band["freq"],
                                None if number in astro.SHELVES else float(band["width"])))
    # The checks above took five whole gains and the name: astro.preset_name's, or the text
    # a50_preset checks when not `named`.
    return astro.EqPreset.of_bands(cast("str", name), cast("list[int]", gains), found)


def _bands(value: object, take: Callable[[dict], object]) -> dict[int, object]:
    """A list of band objects by their `band`, each as `take` gives it; {} for no list. One
    whose `band` is no whole number (true, a list) is left out, so _check finds that band
    missing."""
    return ({band["band"]: take(band) for band in value
             if isinstance(band, dict) and wording.whole(band.get("band"))}
            if isinstance(value, list) else {})


def _of_entry(saved: dict) -> astro.EqPreset:
    """A preset of a50-eq.json, `entry`'s shape, through _check."""
    return _check(saved.get("name"), saved.get("gains"),
                  _bands(saved.get("bands"), lambda band: band))


def _active(pair: object) -> object:
    """The `active` half of the daemon's {"active", "saved"} pair, what the base runs."""
    return pair.get("active") if isinstance(pair, dict) else None


def a50_preset(slot: object, named: bool = True) -> astro.EqPreset | None:
    """The A50 preset an entry of the daemon's `headset` reply's `presets` gives, from what
    the base station runs: its name, gains and bands. None for an entry of another shape or
    with a value _check refuses, as one not read whole. `named` False takes the name as
    the base station gave it, one astro.preset_name refuses too, for a caller that names the
    preset itself."""
    if not isinstance(slot, dict) or not isinstance(name := _active(slot.get("name")), str):
        return None
    try:
        return _check(name, _active(slot.get("gains")), _bands(slot.get("bands"), _active),
                      named)
    except ValueError:
        return None


def library_of(data: object, path: Path) -> Library:
    """The library in a50-eq.json's `data`: each preset read on its own, up to PRESETS_MAX,
    and eq_presets.PARTIAL's sentence for presets that are neither a list nor null, each that
    does not read and those past PRESETS_MAX. LibraryUnread for data of another shape or
    version."""
    saved = eq_presets.entries(data, LIBRARY_VERSION)
    if saved is None:
        why = eq_presets.unread_why(data, LIBRARY_VERSION)
        raise LibraryUnread(eq_presets.sentence(UNREAD, path, why))
    taken, left_out = eq_presets.read_each(data, saved, _of_entry)
    presets = [preset for _number, preset in taken]
    if (past := len(presets) - eq_presets.PRESETS_MAX) > 0:
        left_out.append(f"{wording.counted(past, 'preset')} past the {eq_presets.PRESETS_MAX} "
                        "a library holds")
    return Library(tuple(presets[:eq_presets.PRESETS_MAX]),
                   eq_presets.sentence(eq_presets.PARTIAL, path, "; ".join(left_out))
                   if left_out else "")


def load_library() -> Library:
    """The saved library, empty with no file. LibraryUnread for a file that does not read."""
    target = library_path()
    try:
        data = config.read_json(target)
    except FileNotFoundError:
        return Library()
    except (OSError, config.ConfigError) as exc:
        why = config.unprefixed(wording.reason(exc), target)
        raise LibraryUnread(eq_presets.sentence(UNREAD, target, why)) from None
    return library_of(data, target)


def save_library(library: Library) -> None:
    """The library to a50-eq.json, atomically (eq_presets.write), each preset checked first."""
    if len(library.presets) > eq_presets.PRESETS_MAX:
        raise ValueError(eq_presets.PRESETS_FULL)
    for preset in library.presets:
        _of_entry(entry(preset))
    eq_presets.write(library_path(), LIBRARY_VERSION, {},
                     [entry(preset) for preset in library.presets], ".a50-eq-")


def edit(change: Callable[[Library], Library]) -> Library:
    """Save what `change` makes of the saved library, and answer it. LibraryUnread for a file
    that does not read and LibraryPartial for one read in part (Library.whole), each left as
    it is."""
    library = change(load_library().whole())
    save_library(library)
    return library
