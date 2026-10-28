"""The preset libraries of the G432's family's models with an equalizer of their own, the
G933, G935 and G633 (g432.HeadsetModel.onboard_eq): the equalizer presets Gearbench keeps on
this computer, a file for each model in the settings folder (library_path), as each
Centurion headset's is kept (a50x_library). A preset is a name and its bands, each a
frequency in Hz and a gain in whole dB: the format of the headset's own equalizer, 0x8310,
which carries no width and is the headphones' alone, so a preset holds neither a Q nor an
equalizer's name. Each model's library starts with Media, Gaming and Everyday (STARTERS), as
a50x_library's do. The window and the CLI read and write the files; the daemon reads one alone for a G key
that picks the next preset (daemon/g432/, the owner's answer of 2026-10-25). Picking one
sends its gains through the daemon's `g432_headset_eq` (Preset.request), which keeps the
preset picked in a file of its own beside the library (picked_path) and sends it again each
time the headset attaches or comes back, since the headset keeps what Gearbench sends in
its RAM alone (the owner's decisions of 2026-10-23, "Library and re-send", and 2026-10-25).
Standard library only.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

from . import eq_presets, settings_files
from .eq_presets import EVERYDAY, GAMING, MEDIA
from .g432 import BAND_FREQS, EQ_BANDS_MAX, GAIN_MAX, GAIN_MIN, HeadsetModel
from .wording import given_count, hertz, whole, word_list, written

#: A library file's version.
LIBRARY_VERSION = 1
#: The highest frequency in Hz a band can have: getFrequencies gives each in two bytes.
FREQ_MAX = 0xFFFF
#: The file of a model's preset picked, by its key, which the daemon alone writes, and its
#: version: the preset's name and bands, as a library's file keeps a preset (entry).
PICKED_NAME = "{}-eq-picked.json"
PICKED_VERSION = 1
#: Why a preset is not sent to a headset that plays other bands: the model's short name,
#: then the bands the headset plays.
OTHER_BANDS = "the preset's bands are not the {}'s: the headset plays {}"


@dataclass(frozen=True)
class Band:
    """One band: its frequency in Hz and its gain in whole dB."""

    freq: int
    gain: int


@dataclass(frozen=True)
class Preset:
    """A preset of a library: its name and its bands."""

    name: str
    bands: tuple[Band, ...]

    def request(self) -> dict:
        """The daemon's `g432_headset_eq` request that picks it: its gains to the headset,
        each for the band of its frequency."""
        return {"command": "g432_headset_eq", "preset": self.name,
                "bands": [asdict(band) for band in self.bands]}


class Library(eq_presets.Saved[Preset]):
    """A model's library, its presets (eq_presets.Saved)."""

    def playing(self, bands: tuple[Band, ...]) -> int | None:
        """The number of the first preset whose bands are `bands`, those the headset plays;
        None for none."""
        return next((number for number, preset in zip(self.numbers, self.presets, strict=True)
                     if preset.bands == bands), None)


def bands_of(frequencies: Sequence[int], gains: Sequence[int]) -> tuple[Band, ...]:
    """The bands of `frequencies` at `gains`, as the daemon's reply gives the headset's."""
    return tuple(Band(freq, gain) for freq, gain in zip(frequencies, gains, strict=True))


def _bands(*gains: int) -> tuple[Band, ...]:
    """G HUB's ten bands (g432.BAND_FREQS) at `gains`, the lowest first."""
    return bands_of(BAND_FREQS, gains)


#: The G933's Media, Gaming and Everyday, made to sound like the A50 X's with its tuned three
#: (the owner's decision of 2026-10-23, "Sound like the A50 X"): fitted on RTINGS' measurement
#: of a G933 to the A50 X's sound with each, on G HUB's ten bands in whole dB, with no preamp
#: cut (research-eq/like-a50x-hidpp.md, outside the repository). The G933 measures 4 to 6 dB
#: over the target from 30 to 200 Hz and at 3.15 to 4 kHz, so the three are mostly cuts.
G933_LIKE_A50X = (
    Preset(MEDIA, _bands(-2, -2, -4, -2, 2, -2, 2, -5, 2, 1)),
    Preset(GAMING, _bands(-10, -7, -6, -2, 2, -2, 3, -4, 2, 0)),
    Preset(EVERYDAY, _bands(-5, -4, -5, -2, 2, -2, 2, -5, 2, 0)),
)
#: What a model's library holds with no file, by the model's key. A save writes them to the
#: file with its change, and a deleted one stays deleted. Each assumes G HUB's ten bands;
#: on a headset that lists others, the daemon refuses a pick of one.
STARTERS: Mapping[str, tuple[Preset, ...]] = {
    "g933": G933_LIKE_A50X,
    # Fitted as the G933's, on the median of RTINGS' measurement of a G935 and crinacle's of
    # a G933s, the G935 under its Asia-Pacific name.
    "g935": (
        Preset(MEDIA, _bands(2, 3, -5, -5, 1, 0, 0, 1, 0, 1)),
        Preset(GAMING, _bands(-7, -2, -7, -6, 2, -1, 2, 2, 0, 0)),
        Preset(EVERYDAY, _bands(-1, 0, -6, -5, 1, 0, 0, 1, 0, 0)),
    ),
    # Inferred: no measurement of a G633 is published, so it takes the G933's, its wired twin
    # with the same 40 mm driver (the owner's decision of 2026-10-23, "The G933's fitted
    # set"), until a G633 is measured or heard.
    "g633": G933_LIKE_A50X,
}


def library_path(model: HeadsetModel) -> Path:
    """The file of `model`'s library."""
    return settings_files.config_dir() / eq_presets.LIBRARY_NAME.format(model.key)


def entry(preset: Preset) -> dict:
    """A preset as a library's file keeps it: its name and its bands, each its frequency and
    its gain."""
    return {"name": preset.name, "bands": [asdict(band) for band in preset.bands]}


def band_of(saved: object) -> Band:
    """A band from `saved`, an object of its `freq` and `gain`: a whole number of Hz from 1 to
    FREQ_MAX and a whole dB a gain's byte carries, GAIN_MIN to GAIN_MAX; the headset's own
    range is the daemon's to check. ValueError with the sentence for one it does not take."""
    if not isinstance(saved, dict):
        why = f"a band is an object of its freq and gain, not {written(saved)}"
        raise ValueError(why)  # noqa: TRY004 -- refused in words, as every value of a request is
    freq, gain = saved.get("freq"), saved.get("gain")
    if not whole(freq) or not 1 <= freq <= FREQ_MAX:
        raise ValueError(f"a band's frequency is a whole number of Hz from 1 to {FREQ_MAX}, "
                         f"not {written(freq)}")
    if not whole(gain) or not GAIN_MIN <= gain <= GAIN_MAX:
        raise ValueError(f"a band's gain is a whole number of dB from {GAIN_MIN} to "
                         f"{GAIN_MAX}, not {written(gain)}")
    return Band(freq, gain)


def bands_from(saved: object) -> tuple[Band, ...]:
    """The bands of `saved`, a list of 1 to EQ_BANDS_MAX objects band_of takes, the most a
    long report's setFrequencyGains carries; ValueError with the sentence for any other."""
    if not isinstance(saved, list) or not 1 <= len(saved) <= EQ_BANDS_MAX:
        raise ValueError(f"a preset has 1 to {EQ_BANDS_MAX} bands, "
                         f"not {given_count(saved, 'band')}")
    return tuple(band_of(band) for band in saved)


def preset_of(saved: dict) -> Preset:
    """A preset from `saved`, entry's shape, each value checked: its name by the A50's rule
    (eq_presets.preset_name) and its bands (bands_from). ValueError with the sentence for one
    it does not take."""
    return Preset(eq_presets.preset_name(saved.get("name")), bands_from(saved.get("bands")))


def library_of(data: object, path: Path) -> Library:
    """The library in a library file's `data` (eq_presets.library_of).
    eq_presets.LibraryUnread for data of another shape or version."""
    return Library(*eq_presets.library_of(data, path, LIBRARY_VERSION, preset_of))


def load_library(model: HeadsetModel) -> Library:
    """`model`'s library, its STARTERS with no file. eq_presets.LibraryUnread for a file that
    does not read."""
    return eq_presets.load_library(library_path(model), library_of,
                                   Library(STARTERS.get(model.key, ())))


def save_library(library: Library, model: HeadsetModel) -> None:
    """`library` to `model`'s file, atomically, each preset checked first
    (eq_presets.save_library)."""
    eq_presets.save_library(library_path(model), LIBRARY_VERSION, library.presets, entry,
                            preset_of, f".{model.key}-eq-")


def edit(change: Callable[[Library], Library], model: HeadsetModel) -> Library:
    """Save what `change` makes of `model`'s library, and answer it. eq_presets'
    LibraryUnread for a file that does not read and LibraryPartial for one read in part
    (Library.whole), each left as it is."""
    library = change(load_library(model).whole())
    save_library(library, model)
    return library


def gains_for(preset: Preset, frequencies: tuple[int, ...], short: str) -> list[int]:
    """`preset`'s gains for the headset `short` names, in the order of `frequencies`, the bands
    it plays: ValueError, OTHER_BANDS, where they are not the preset's bands'."""
    if tuple(band.freq for band in preset.bands) != frequencies:
        played = word_list([hertz(freq) for freq in frequencies], "and") if frequencies \
            else "no band"
        raise ValueError(OTHER_BANDS.format(short, played))
    return [band.gain for band in preset.bands]


def picked_path(model: HeadsetModel) -> Path:
    """The file of the preset picked for `model`."""
    return settings_files.config_dir() / PICKED_NAME.format(model.key)


def load_picked(model: HeadsetModel) -> Preset | None:
    """The preset picked for `model`, None with no file. settings_files.ConfigError naming the
    file, cut to its two ends as settings_files.from_file cuts a refusal, for a file that does
    not read, is of another version or holds a preset preset_of does not take, and OSError for
    a read that fails."""

    def picked(data: object) -> Preset:
        # entries' check of an object at the version, as for a library's file; it holds no
        # list.
        if eq_presets.entries(data, PICKED_VERSION) is None:
            raise ValueError(eq_presets.unread_why(data, PICKED_VERSION))
        return preset_of(cast("dict", data))

    try:
        return settings_files.from_file(picked_path(model), picked)
    except FileNotFoundError:
        return None


def save_picked(preset: Preset, model: HeadsetModel) -> None:
    """`preset` as the one picked for `model`, to its file atomically (settings_files.write_json).
    Raises OSError, and settings_files.ConfigError."""
    path = picked_path(model)
    settings_files.make_folder(path)
    settings_files.write_json(path, {"version": PICKED_VERSION, **entry(preset)},
                      f".{model.key}-eq-picked-")


def drop_picked(model: HeadsetModel) -> bool:
    """No preset picked for `model` any more: its file removed, if there is one. Whether it
    was there; raises OSError for a removal that fails."""
    path = picked_path(model)
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    settings_files.sync_removal(path)
    return True
