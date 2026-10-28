"""The Centurion headsets' preset libraries: the equalizer presets Gearbench keeps on this
computer, a file for each model (library_path), nothing shared between models (the owner's
decision of 2026-10-22, "One file per model"), apart from the headset and from any presets
in it, each a name, the equalizer it is of, one of the model's (a50x.Model.equalizers),
and its bands as that equalizer takes them (bands_of): 0x020D's ten, or the five of the PRO X
2's 0x0636 (centurion_features.equalizer.Band). The window and the CLI read and write the files; the
daemon never does. Picking one writes its bands into the slot its equalizer plays, through
the daemon's `a50x_eq` (Preset.request); the slots of G HUB's presets are never written.
Each model's library starts with Media, Gaming and Everyday (STARTERS), the order of the A50
Gen 4's slots and the G432's shipped presets. Standard library only.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

from . import eq_presets, settings_files
from .a50x import A50_GEN5, A50_X, G325, G522, PRO_X_2, Model, equalizer_refused
from .centurion_features import equalizer, onboard_eq
from .centurion_features.equalizer import Band

#: A library file's version. A model's file is eq_presets.LIBRARY_NAME with its key
#: (a50x.Model.key): the A50 X's is a50x-eq.json, the name of the one file every model shared
#: before each kept its own, so a file saved then is read where it is as the A50 X's library,
#: never renamed, as config.json keeps the name of a key it stored.
LIBRARY_VERSION = 1


@dataclass(frozen=True)
class Preset:
    """A preset of a library: its name, the equalizer it is of, by its socket word, and its
    bands (bands_of)."""

    name: str
    instance: str
    bands: tuple[Band, ...]

    def request(self) -> dict:
        """The daemon's `a50x_eq` request that picks it: its bands into the slot its equalizer
        plays."""
        return {"command": "a50x_eq", "instance": self.instance,
                "bands": [asdict(band) for band in self.bands]}


class Library(eq_presets.Saved[Preset]):
    """A model's library, its presets (eq_presets.Saved)."""

    def of(self, instance: str) -> list[tuple[int, Preset]]:
        """The presets of the equalizer `instance`, each with its number."""
        return [(number, preset) for number, preset in zip(self.numbers, self.presets,
                                                           strict=True)
                if preset.instance == instance]

    def playing(self, instance: str, bands: tuple[Band, ...]) -> int | None:
        """The number of the first preset of `instance` whose frequencies and gains `bands`
        have, as equalizer.preset_of tells a G HUB preset; None for none."""
        return equalizer.preset_of(bands, {number: preset.bands
                                           for number, preset in self.of(instance)})


def _bands(*bands: tuple[int, int, float]) -> tuple[Band, ...]:
    """Bands, each its frequency in Hz, its Q byte and its gain in dB."""
    return tuple(Band(*band) for band in bands)


#: The A50 X's Media, Gaming and Everyday for the headphones, the three the owner tuned by
#: ear on 2026-10-22: the research's sets, with the owner's stronger correction at 1150 Hz and
#: 3850 Hz in each.
A50_X_TUNED = (
    Preset(eq_presets.MEDIA, "headphones", _bands(
        (21, 50, 3.0), (40, 22, 3.5), (90, 32, 1.0), (250, 22, 0.0), (500, 22, 0.0),
        (1150, 96, -3.5), (2500, 22, 0.0), (3850, 42, 4.0), (10000, 22, 0.0),
        (16000, 45, 1.2))),
    Preset(eq_presets.GAMING, "headphones", _bands(
        (20, 22, 0.0), (45, 18, -4.0), (125, 22, 0.0), (250, 22, 0.0), (500, 22, 0.0),
        (1150, 96, -3.5), (2600, 28, 2.0), (3850, 42, 4.0), (10000, 22, 0.0),
        (20000, 22, 0.0))),
    Preset(eq_presets.EVERYDAY, "headphones", _bands(
        (21, 50, 3.0), (50, 22, 0.0), (125, 22, 0.0), (250, 22, 0.0), (500, 22, 0.0),
        (1150, 96, -3.5), (2500, 22, 0.0), (3850, 42, 4.0), (10000, 22, 0.0),
        (20000, 22, 0.0))),
)
#: What a model's library holds with no file, by the model's key: on the A50 X, A50_X_TUNED,
#: and on each other model with published measurements Media, Gaming and Everyday for the
#: headphones made to sound like them (the owner's decision of 2026-10-23, "Sound like the
#: A50 X"): fitted from those measurements to the A50 X's sound with them at the ear, in the
#: model's own equalizer's format, the boosts summing to +12 dB at most at any frequency, with
#: no preamp cut (the owner's decisions of 2026-10-23 and 2026-10-18). A save writes them to the file
#: with its change, and a deleted one stays deleted.
STARTERS: Mapping[str, tuple[Preset, ...]] = {
    A50_X.key: A50_X_TUNED,
    # Fitted on Gadgetry Tech's measurement of an A50 Gen 5, paired with its A50 X's.
    A50_GEN5.key: (
        Preset(eq_presets.MEDIA, "headphones", _bands(
            (20, 23, 6.0), (38, 16, 3.6), (90, 17, 1.9), (400, 18, 1.0), (500, 22, 0.0),
            (1155, 73, -4.5), (3699, 56, 2.35), (4789, 64, 2.6), (12000, 32, -1.25),
            (16000, 45, 1.2))),
        Preset(eq_presets.GAMING, "headphones", _bands(
            (20, 47, 2.5), (49, 32, -1.4), (125, 22, 0.0), (269, 21, 1.25), (676, 96, 0.7),
            (1137, 96, -4.45), (3317, 35, 3.1), (4569, 46, 2.95), (9636, 32, -1.25),
            (20000, 22, 0.0))),
        Preset(eq_presets.EVERYDAY, "headphones", _bands(
            (20, 38, 6.0), (40, 16, 1.85), (104, 16, 0.8), (400, 16, 1.1), (500, 22, 0.0),
            (1154, 71, -4.6), (3701, 56, 2.35), (4789, 64, 2.6), (12000, 32, -1.25),
            (20000, 22, 0.0))),
    ),
    # Fitted on three measurements of the leatherette pads, RTINGS', Filk's and Gadgetry
    # Tech's: the five fixed bands at Q 1 cannot make the A50 X's narrow cut at 1.15 kHz and
    # lift at 3.85 kHz.
    PRO_X_2.key: (
        Preset(eq_presets.MEDIA, "headphones", _bands(
            (80, 1, -2.0), (240, 1, 0.0), (750, 1, -2.0), (2200, 1, 2.0), (6600, 1, 5.0))),
        Preset(eq_presets.GAMING, "headphones", _bands(
            (80, 1, -11.0), (240, 1, 1.0), (750, 1, -3.0), (2200, 1, 4.0), (6600, 1, 5.0))),
        Preset(eq_presets.EVERYDAY, "headphones", _bands(
            (80, 1, -6.0), (240, 1, 0.0), (750, 1, -2.0), (2200, 1, 2.0), (6600, 1, 5.0))),
    ),
    # Fitted on Gadgetry Tech's measurement of a G522, which dips from 4 to 6.3 kHz: bands 7
    # and 8, a twelfth of an octave apart (about a quarter in Gaming), lift it together by up
    # to +12 dB.
    G522.key: (
        Preset(eq_presets.MEDIA, "headphones", _bands(
            (23, 29, 6.0), (65, 23, 6.0), (93, 50, -1.35), (207, 50, 1.5), (663, 96, 3.05),
            (1411, 40, -5.85), (4455, 31, 6.0), (4720, 31, 6.0), (12000, 32, 3.35),
            (16000, 45, 1.2))),
        Preset(eq_presets.GAMING, "headphones", _bands(
            (20, 22, 0.0), (43, 50, -1.3), (112, 50, -0.55), (202, 50, 1.7), (657, 96, 2.8),
            (1370, 47, -5.6), (4179, 27, 6.0), (5000, 24, 6.0), (12000, 32, 2.8),
            (20000, 22, 0.0))),
        Preset(eq_presets.EVERYDAY, "headphones", _bands(
            (22, 26, 4.9), (65, 50, 2.4), (170, 50, 0.65), (211, 50, 1.5), (661, 96, 3.1),
            (1413, 40, -5.85), (4456, 31, 6.0), (4721, 31, 6.0), (12000, 32, 3.35),
            (20000, 22, 0.0))),
    ),
    # Untuned: no measurement of a G325 is published, so it takes the A50 X's three as they
    # are, which its equalizer, the A50 X's format, takes whole.
    G325.key: A50_X_TUNED,
}


def library_path(model: Model) -> Path:
    """The file of `model`'s library."""
    return settings_files.config_dir() / eq_presets.LIBRARY_NAME.format(model.key)


def entry(preset: Preset) -> dict:
    """A preset as a library's file keeps it: its name, its equalizer and its bands, each
    its frequency, its Q byte and its gain."""
    request = preset.request()
    return {"name": preset.name, "instance": request["instance"], "bands": request["bands"]}


def bands_of(model: Model, instance: str, entries: object) -> tuple[Band, ...]:
    """`entries`, the bands of a preset of `model`'s equalizer `instance`, in that
    equalizer's format: the PRO X 2's headphones' five of 0x0636 (onboard_eq.bands_from), and
    every other equalizer's ten of 0x020D (equalizer.bands_from). ValueError with the sentence
    for bands of another count or format."""
    if model.onboard(instance):
        return onboard_eq.bands_from(entries)
    return equalizer.bands_from(entries)


def preset_of(saved: dict, model: Model) -> Preset:
    """A preset of `model`'s library from `saved`, entry's shape, each value checked: its
    name by the A50's rule (eq_presets.preset_name), its equalizer one of the model's
    (a50x.equalizer_refused) and its bands in that equalizer's format (bands_of). ValueError
    with the sentence for one it does not take."""
    name = eq_presets.preset_name(saved.get("name"))
    instance = saved.get("instance")
    if (refused := equalizer_refused(model, instance, model.equalizers)) is not None:
        raise ValueError(refused)
    # equalizer_refused passes only a name of the model's equalizers.
    held = cast("str", instance)
    return Preset(name, held, bands_of(model, held, saved.get("bands")))


def library_of(data: object, path: Path, model: Model) -> Library:
    """`model`'s library in a library file's `data` (eq_presets.library_of).
    eq_presets.LibraryUnread for data of another shape or version."""
    return Library(*eq_presets.library_of(data, path, LIBRARY_VERSION,
                                          lambda saved: preset_of(saved, model)))


def load_library(model: Model) -> Library:
    """`model`'s library, its STARTERS with no file. eq_presets.LibraryUnread for a file that
    does not read."""
    return eq_presets.load_library(library_path(model),
                                   lambda data, path: library_of(data, path, model),
                                   Library(STARTERS[model.key]))


def save_library(library: Library, model: Model) -> None:
    """`library` to `model`'s file, atomically, each preset checked first
    (eq_presets.save_library)."""
    eq_presets.save_library(library_path(model), LIBRARY_VERSION, library.presets, entry,
                            lambda saved: preset_of(saved, model), f".{model.key}-eq-")


def edit(change: Callable[[Library], Library], model: Model) -> Library:
    """Save what `change` makes of `model`'s library, and answer it. eq_presets'
    LibraryUnread for a file that does not read and LibraryPartial for one read in part
    (Library.whole), each left as it is."""
    library = change(load_library(model).whole())
    save_library(library, model)
    return library
