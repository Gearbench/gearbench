"""The Astro A50's preset library: presets Gearbench keeps in a50-eq.json, apart from the base
station's three slots, each a name, five gains and five bands as the base station takes them
(astro.EqPreset). The window and the CLI read and write the file; the daemon never does. A
preset written to a slot goes to the base station's memory through the daemon's `headset_eq`,
as any edit does. The slots' defaults, which Reset to default writes, are here too
(DEFAULT_SLOTS). Standard library only.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast

from . import astro, eq_presets, settings_files, wording

LIBRARY_NAME = "a50-eq.json"
#: a50-eq.json's version.
LIBRARY_VERSION = 1
#: The three slots' defaults, which Reset to default writes to a slot, slot 1 first, its name
#: too: the A50 EQ research's three presets of 2026-09-27, designed against Harman over-ear
#: 2018 from published measurements of the A50 Gen 4 and a model of the base station's
#: filters, in the slots the owner's base station held them in on 2026-10-18. The research
#: named the first two Rock and Alt and FPS Spatial; a headset's
#: shipped presets take eq_presets' names (the owner's decision of 2026-10-22).
DEFAULT_SLOTS = (
    astro.EqPreset(eq_presets.MEDIA, (4, -7, 2, 5, 3), (80, 180, 2700, 4400, 12000),
                   (1.6, 0.5, 0.3)),
    astro.EqPreset(eq_presets.GAMING, (-5, -4, 5, 4, 1), (150, 220, 2500, 4300, 12000),
                   (1.5, 0.7, 0.3)),
    astro.EqPreset(eq_presets.EVERYDAY, (2, -5, 3, 4, 2), (80, 180, 2700, 4300, 12000),
                   (1.5, 0.5, 0.4)),
)
#: A preset's refusal for a band it lacks: the band's number.
NO_BAND = "band {} is missing"
#: A preset's refusal for a band its list gives more than once: the band's number.
TWICE = "band {} is given more than once"


class Library(eq_presets.Saved[astro.EqPreset]):
    """The library's presets (eq_presets.Saved), each an A50 preset."""


def default_slot(slot: int) -> astro.EqPreset:
    """Slot `slot`'s default, 1 to 3, which Reset to default writes."""
    return DEFAULT_SLOTS[astro.PRESETS.index(slot)]


def library_path() -> Path:
    return settings_files.config_dir() / LIBRARY_NAME


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
        if not isinstance(band, dict):
            raise ValueError(NO_BAND.format(number))  # noqa: TRY004 -- read_each catches ValueError
        astro.band_width(number, band.get("freq"), band.get("width"))
        # band_width took a whole frequency and, off the shelves, a number for the width.
        found.append(astro.Band(band["freq"],
                                None if number in astro.SHELVES else float(band["width"])))
    # The checks above took five whole gains and the name: astro.preset_name's, or the text
    # a50_preset checks when not `named`.
    return astro.EqPreset.of_bands(cast("str", name), cast("list[int]", gains), found)


def _bands(value: object, take: Callable[[dict], object]) -> dict[int, object]:
    """A list of band objects by their `band`, each as `take` gives it; {} for no list. One
    whose `band` is none of astro.BANDS (7, true, a list) is left out, as _check reads BANDS
    alone. ValueError, TWICE, for one of BANDS the list gives twice: which one was meant is
    unknown, and a save would drop the other."""
    if not isinstance(value, list):
        return {}
    found: dict[int, object] = {}
    for band in value:
        # whole first: True in astro.BANDS is True.
        if (isinstance(band, dict) and wording.whole(number := band.get("band"))
                and number in astro.BANDS):
            if number in found:
                raise ValueError(TWICE.format(number))
            found[number] = take(band)
    return found


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
    with a value _check or _bands refuses, as one not read whole. `named` False takes the name as
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
    """The library in a50-eq.json's `data` (eq_presets.library_of). eq_presets.LibraryUnread
    for data of another shape or version."""
    return Library(*eq_presets.library_of(data, path, LIBRARY_VERSION, _of_entry))


def load_library() -> Library:
    """The saved library, empty with no file. eq_presets.LibraryUnread for a file that does not
    read."""
    return eq_presets.load_library(library_path(), library_of, Library())


def save_library(library: Library) -> None:
    """The library to a50-eq.json, atomically, each preset checked first
    (eq_presets.save_library)."""
    eq_presets.save_library(library_path(), LIBRARY_VERSION, library.presets, entry, _of_entry,
                            ".a50-eq-")


def edit(change: Callable[[Library], Library]) -> Library:
    """Save what `change` makes of the saved library, and answer it. eq_presets'
    LibraryUnread for a file that does not read and LibraryPartial for one read in part
    (Library.whole), each left as it is."""
    library = change(load_library().whole())
    save_library(library)
    return library
