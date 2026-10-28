"""The presets of each headset's equalizer that gearbench.g432_eq runs, the G432's and those
of the models of its family with none of their own (g432_eq_words.EQ_MODELS): G HUB's gain
range and the filters' shape, a preset's gains, the shipped presets, and each model's presets
file, read, checked and saved. Standard library only, and no outside program, so
gearbench.g432_fit takes it without the PipeWire chain's module.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import cast

from . import eq_presets, settings_files, wording
from .eq_presets import EVERYDAY, GAMING, MEDIA, PRESETS_FULL, PRESETS_MAX, preset_name
from .g432 import BAND_FREQS, G432_MODEL, HeadsetModel

#: G HUB's slider of a band (g432.BAND_FREQS), in whole dB.
DB_RANGE = (-12, 12)
#: Assumed, since no source gives them: a one-octave peak's Q, the square root of 2, and
#: the shelves' corners and Q, one over the square root of 2, a slope with no bump. G
#: HUB's bass and treble give no frequency.
BAND_Q = round(math.sqrt(2), 2)
SHELF_Q = round(1 / math.sqrt(2), 2)
BASS_FREQ = 100
TREBLE_FREQ = 8000
#: PipeWire's default clock.rate, which the chain runs at: a filter's frequency is below
#: half of it, the highest a biquad at that rate has.
RATE = 48000


@dataclass(frozen=True)
class Gains:
    """Each band's gain in dB, in BAND_FREQS' order, and the shelves'."""

    bands: tuple[int, ...] = (0,) * len(BAND_FREQS)
    bass: int = 0
    treble: int = 0

    def all(self) -> tuple[int, ...]:
        """Every gain, in the order the sound goes through the filters."""
        return (self.bass, *self.bands, self.treble)


@dataclass(frozen=True)
class Preset:
    """One preset: its name and its gains."""

    name: str
    gains: Gains


#: The shipped presets' defaults, which Reset to default puts back: the three aims of
#: the A50's presets, designed for the G432 against Harman over-ear 2018 from RTINGS' and
#: Gadgetry Tech's published measurements of it, then each gain times 2.5, rounded half
#: away from zero, since the gains as designed were too subtle on a G432 by ear (the
#: owner's decision of 2026-10-18). There is no preamp, so near full volume a boost can
#: clip. Every other model of EQ_MODELS ships these too: the G432's design, not measured
#: on that model. The design named the first two Rock and Alt and FPS Spatial; a headset's
#: shipped presets take eq_presets' names (the owner's decision of 2026-10-22), and a file
#: keeps the names it holds.
DEFAULT_PRESETS = (
    Preset(MEDIA, Gains(bands=(0, 5, -5, -5, -3, 0, 3, 8, 3, 0), bass=8, treble=5)),
    Preset(GAMING, Gains(bands=(0, 3, -5, -3, 0, 0, 8, 10, 0, 0), bass=-5, treble=3)),
    Preset(EVERYDAY, Gains(bands=(0, 3, -3, -3, 0, 0, 5, 8, 3, 0), bass=5, treble=3)),
)
#: The shipped presets' numbers, as the tab and the CLI give them; a preset saved as new
#: takes the next number after the last.
SHIPPED = tuple(range(1, len(DEFAULT_PRESETS) + 1))
#: The preset played with no file: Everyday, the daily one, in place of a flat one.
DEFAULT_ACTIVE = 3
#: g432-eq.json's version since the presets; a file with no version and no presets is
#: version 1's gains alone.
PRESETS_VERSION = 2
#: The name a version 1 file's gains take as preset 1, unless they are all 0 dB.
CUSTOM = "Custom"
#: Where a version 1 presets file's bytes are kept in the settings folder's backups, before
#: its first save as version 2, by the model's key.
BACKUP_NAME = "{}-eq.v1.json"


#: Why a value is no preset's number: how many presets there are, then the value.
NOT_A_NUMBER = "a preset is a number from 1 to {}, not {}"
#: Why a preset saved as new has no Reset to default, and why a shipped one is not deleted.
NO_DEFAULT = "preset {} was saved as new, so it has no default to go back to"
NO_DELETE = "preset {} ships with Gearbench and stays; a reset puts its default back"
#: Why a presets file that does not read takes no change: the presets in it are the user's
#: own, so it is never written over. The file's path, then the cause.
UNREAD = ("{} could not be read ({}), so the equalizer plays the shipped presets and "
          "Gearbench leaves the file as it is: fix it, or move it away to start from them")


class PresetsRefused(settings_files.ConfigError):
    """A change to the presets' file while it does not read or was read in part
    (Presets.whole): UNREAD's sentence or eq_presets.PARTIAL's."""


@dataclass(frozen=True)
class Presets:
    """The presets, preset 1 first: the shipped ones, then those saved as new, the number
    of the active one, which the chain plays, and `refused`, UNREAD's sentence for a file
    that does not read, which plays the defaults, or eq_presets.PARTIAL's for one read with
    presets left out; empty for a file read whole or presets made here."""

    active: int = DEFAULT_ACTIVE
    presets: tuple[Preset, ...] = DEFAULT_PRESETS
    refused: str = ""

    @property
    def numbers(self) -> tuple[int, ...]:
        return tuple(range(1, len(self.presets) + 1))

    def number(self, value: object) -> int:
        """`value` if it is one of these presets' numbers, else ValueError in words."""
        if not wording.whole(value) or value not in self.numbers:
            raise ValueError(NOT_A_NUMBER.format(len(self.presets), wording.written(value)))
        return value

    def get(self, number: int) -> Preset:
        return self.presets[number - 1]

    def whole(self) -> Presets:
        """These presets, or PresetsRefused while the file did not read or left some out
        (`refused`): a save would write over what it holds."""
        if self.refused:
            raise PresetsRefused(self.refused)
        return self

    @property
    def current(self) -> Preset:
        return self.get(self.active)

    def put(self, number: int, preset: Preset) -> Presets:
        """These presets with `preset` in `number`'s place."""
        presets = list(self.presets)
        presets[number - 1] = preset
        return replace(self, presets=tuple(presets))

    def with_gains(self, number: int, gains: Gains) -> Presets:
        return self.put(number, replace(self.get(number), gains=gains))

    def named(self, number: int, name: str) -> Presets:
        return self.put(number, replace(self.get(number), name=name))

    def reset(self, number: int) -> Presets:
        """These presets with `number`, a shipped one, back to its default, its name and
        its gains; ValueError for one saved as new, which has none."""
        if number not in SHIPPED:
            raise ValueError(NO_DEFAULT.format(number))
        return self.put(number, DEFAULT_PRESETS[number - 1])

    def deleted(self, number: int) -> Presets:
        """These presets without `number`, one saved as new, those after it a number lower;
        the active one plays on, or, for `number` itself, the preset before it.
        ValueError for a shipped one, which a reset puts back to its default."""
        if number in SHIPPED:
            raise ValueError(NO_DELETE.format(number))
        active = self.active - 1 if self.active >= self.number(number) else self.active
        return Presets(active, self.presets[:number - 1] + self.presets[number:])

    def saved_as(self, name: str) -> Presets:
        """These presets with the active one's gains saved as a new preset, `name`, after
        the last, which becomes the active one; ValueError at PRESETS_MAX."""
        if len(self.presets) >= PRESETS_MAX:
            raise ValueError(PRESETS_FULL)
        return Presets(len(self.presets) + 1,
                       (*self.presets, Preset(preset_name(name), self.current.gains)))


def checked(gains: Gains) -> Gains:
    """`gains`, or ValueError naming the first that is not a whole dB in DB_RANGE."""
    low, high = DB_RANGE
    if len(gains.bands) != len(BAND_FREQS):
        raise ValueError(f"the equalizer has {len(BAND_FREQS)} bands, not {len(gains.bands)}")
    names = ("bass", *(wording.hertz(freq) for freq in BAND_FREQS), "treble")
    for name, gain in zip(names, gains.all(), strict=True):
        if not wording.whole(gain) or not low <= gain <= high:
            raise ValueError(f"the {name} gain is a whole number of dB from {low} to "
                             f"{high}, not {wording.written(gain)}")
    return gains


def presets_file_name(model: HeadsetModel) -> str:
    """The model's presets file's name: eq_presets.LIBRARY_NAME with its key, as a library's
    (g432_library), since a model keeps one or the other (HeadsetModel.onboard_eq)."""
    return eq_presets.LIBRARY_NAME.format(model.key)


def gains_path(*, model: HeadsetModel = G432_MODEL) -> Path:
    return settings_files.config_dir() / presets_file_name(model)


def _gain(value: object) -> int:
    """A saved gain, 0 for one that is not a whole dB in DB_RANGE."""
    low, high = DB_RANGE
    return value if wording.whole(value) and low <= value <= high else 0


def _gains_of(data: dict) -> Gains:
    """The gains a saved preset holds, each 0 where it lacks one or holds one out of range,
    and every band 0 for a list of bands of another length."""
    bands = data.get("bands")
    return Gains(bands=tuple(map(_gain, bands)) if isinstance(bands, list)
                 and len(bands) == len(BAND_FREQS) else Gains().bands,
                 bass=_gain(data.get("bass")), treble=_gain(data.get("treble")))


def _preset_of(entry: object, default: Preset) -> Preset:
    """A saved preset: `default` for one that is not an object, its name for a name
    eq_presets.is_name refuses."""
    if not isinstance(entry, dict):
        return default
    name = entry["name"] if eq_presets.is_name(entry.get("name")) else default.name
    return Preset(name, _gains_of(entry))


def presets_of(data: object, path: Path) -> Presets:
    """The presets in `data`, a presets file's at `path`. Version 1's gains, in an object
    with no version and no presets, are preset 1, CUSTOM, the active one, unless they are
    all 0 dB, which are the defaults. In version 2 each shipped preset is read on its own
    (_preset_of), a list too short for them keeps the defaults past its end, each saved as
    new after them is kept if it reads (eq_presets.read_each), up to PRESETS_MAX in all,
    and the active one is the preset the file's `active` numbered, DEFAULT_ACTIVE when that
    one was left out or is no preset. Presets that are neither a list nor null, and each
    preset left out, give eq_presets.PARTIAL's sentence (`refused`); data of another shape
    or version, or with presets and no version, is the defaults, with UNREAD's."""
    if isinstance(data, dict) and "version" not in data and "presets" not in data:
        gains = _gains_of(data)
        return (Presets() if gains == Gains()
                else replace(Presets().put(1, Preset(CUSTOM, gains)), active=1))
    saved = eq_presets.entries(data, PRESETS_VERSION)
    if saved is None:
        why = eq_presets.unread_why(data, PRESETS_VERSION)
        return Presets(refused=eq_presets.sentence(UNREAD, path, why))
    presets = tuple(_preset_of(saved[index] if index < len(saved) else None, default)
                    for index, default in enumerate(DEFAULT_PRESETS))
    # Past the shipped ones a preset saved as new has no default: one that does not read
    # is left out, and the active one is found by its number in the file.
    added, left_out = eq_presets.read_each(
        data, saved, lambda entry: Preset(preset_name(entry.get("name")), _gains_of(entry)),
        len(SHIPPED))
    numbers = [*SHIPPED, *(number for number, _preset in added)]
    active = cast("dict", data).get("active")
    return Presets(numbers.index(active) + 1 if wording.whole(active) and active in numbers
                   else DEFAULT_ACTIVE,
                   (*presets, *(preset for _number, preset in added)),
                   eq_presets.sentence(eq_presets.PARTIAL, path, "; ".join(left_out))
                   if left_out else "")


def load_presets(*, model: HeadsetModel = G432_MODEL) -> Presets:
    """The saved presets, the defaults with no file. A file that does not read, or a link to
    nothing, the file's or a folder's above it, gives the defaults, and one read in part the
    presets it held that read, each refusing a change (Presets.refused)."""
    try:
        return eq_presets.load_library(gains_path(model=model), presets_of, Presets(), UNREAD)
    except eq_presets.LibraryUnread as exc:
        return Presets(refused=str(exc))


def load_gains(*, model: HeadsetModel = G432_MODEL) -> Gains:
    """The active preset's gains, which the chain plays."""
    return load_presets(model=model).current.gains


def save_presets(presets: Presets, *, model: HeadsetModel = G432_MODEL) -> None:
    """The presets to the model's presets file, atomically, each checked first. A version 1
    file's bytes go to the backups first (settings_files.keep_old_version), once."""
    if not len(SHIPPED) <= len(presets.presets) <= PRESETS_MAX:
        raise ValueError(f"{presets_file_name(model)} holds {len(SHIPPED)} to {PRESETS_MAX} "
                         f"presets, not {len(presets.presets)}")
    for preset in presets.presets:
        checked(preset.gains)
        preset_name(preset.name)
    presets.number(presets.active)
    target = gains_path(model=model)
    # Version 1 is the only one before PRESETS_VERSION, so BACKUP_NAME's.
    settings_files.keep_old_version(
        target, lambda _version: settings_files.backup_dir() / BACKUP_NAME.format(model.key),
        PRESETS_VERSION)
    eq_presets.write(target, PRESETS_VERSION, {"active": presets.active},
                     [{"name": preset.name, **asdict(preset.gains)} for preset in presets.presets],
                     f".{target.stem}-")


def edit(change: Callable[[Presets], Presets], *, model: HeadsetModel = G432_MODEL) -> Presets:
    """Save what `change` makes of the saved presets, and set nothing on the chain: for a
    name, or gains that apply at the next start. The presets saved. PresetsRefused for a
    file that does not read or was read in part (Presets.whole), left as it is."""
    presets = change(load_presets(model=model).whole())
    save_presets(presets, model=model)
    return presets
