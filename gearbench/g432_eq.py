"""The Logitech G432's equalizer: a PipeWire filter chain, its presets and their gains, the
transient user unit that runs it, and what PipeWire says of it.

Logitech G432 Equalizer is a stereo sink whose output plays to the G432's own output and
no other: a bass shelf, ten peaking filters at G HUB's bands and a treble shelf, PipeWire's
own builtin filters, with no preamp, so a boost is a boost on top of the volume. It plays
the active one of its presets, each a name and its gains, kept in g432-eq.json. Off by
default. On is `gearbench-g432-eq.service` running: a transient unit that `systemd-run
--user` makes for `render`'s conf, so it is off again after a logout, and off
stops it, which removes the sink. While it runs the G432's own output is held at 0 dB, so
the equalizer's volume is the one that applies: on gives the equalizer the G432's volume
and off gives the G432 the equalizer's, each as loud as the other was. While it runs, a
second transient unit, `gearbench-g432-eq-watch.service`, runs `python3 -m
gearbench.g432_eq watch`, which follows PipeWire and holds a G432 plugged back in at 0 dB
again. On while the G432's own output is the default makes the equalizer the default; off
while it is the default output makes the G432's own output the default, and with the G432
unplugged then, the next on makes the equalizer the default again. A gain change while it
runs is set live with `pw-cli`. Gearbench Spatial, while it plays to the G432, goes
through the equalizer while its sink is in PipeWire (gearbench.spatial_unit), so on waits
for it to move there before the hold. Standard library only, never run in the daemon; every
outside program goes through a `run` argument and `spatial.run_program`, but for the watch's
pw-dump, which goes through `main`'s `spawn`, and a failure is a `spatial.SpatialError` in
words.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from itertools import pairwise
from pathlib import Path
from typing import IO, cast

from . import config, eq_presets, pipewire_unit, spatial, wording
from .eq_presets import PRESETS_FULL, PRESETS_MAX, preset_name

UNIT = "gearbench-g432-eq.service"
#: The name in the journal of the unit that watches for the G432's return while UNIT runs.
WATCH_NAME = "gearbench-g432-eq-watch"
WATCH_UNIT = f"{WATCH_NAME}.service"
SINK = "gearbench-g432-eq"
OUTPUT = "gearbench-g432-eq.output"
DESCRIPTION = "Logitech G432 Equalizer"
CONF_NAME = "gearbench-g432-eq.conf"
#: Beside the conf, what switch_on and switch_off keep between them (Record). The runtime
#: folder goes at a logout, and so does the hold: it is set unsaved, so WirePlumber gives
#: the G432 the level it saved at its next start or plug-in. A PipeWire restart ends the
#: hold and keeps the file, so its level counts only while the G432 is held (is_held).
RECORD_NAME = "gearbench-g432-eq.json"
#: The sink's channels, FL and FR in the template.
CHANNELS = 2
#: The most switch_on gives the sink, its 100 %: above it the sink adds gain of its own,
#: which clips a loud passage before the G432 under it, where the G432's own volume above
#: 0 dB was the adapter's.
CARRY_MAX = 1.0
#: G HUB's headset bands, an octave apart (its app.min.js), and its slider, in whole dB.
BAND_FREQS = (32, 64, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)
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

NO_G432 = (f"No Logitech G432 output in PipeWire: plug in the headset's USB adapter, then "
           f"switch {DESCRIPTION} on again.")
#: The G432's card in PipeWire on a profile with no output: its input alone, or off. Not yet
#: checked on a G432.
NO_OUTPUT = ("The Logitech G432's sound profile has no output: pick one with its output in the "
             f"sound settings, then switch {DESCRIPTION} on again.")
NO_RUNTIME = f"XDG_RUNTIME_DIR is not set, so {DESCRIPTION}'s files have nowhere to go."
#: switch_on's note when the sink could not take the G432's volume, with the cause; the
#: G432 is then not held, so the sink's first volume, 1.0, is never over a G432 at 0 dB.
NOT_CARRIED = ("The G432's own volume stayed where it was. Setting "
               f"{DESCRIPTION}'s volume to match it failed ({{}}), so both volumes apply.")
#: Why there was no sink to set: the unit's process made none in time.
NO_SINK = f"{DESCRIPTION} did not show in PipeWire within {spatial.SETTLE:g} s"
#: switch_on's refusal when the unit stopped as it started, before any pick or hold.
STOPPED = pipewire_unit.STOPPED.format(DESCRIPTION, UNIT)
#: switch_on's note when the G432's own output could not be held, with the cause.
NOT_HELD = ("The G432's own volume stayed where it was. Setting it to 0 dB failed ({}), so "
            f"{DESCRIPTION}'s volume and the G432's both apply.")
#: switch_off's note when the G432's own volume could not be put back: the cause, then the
#: level in GNOME's percent.
NOT_PUT_BACK = ("The G432's own volume is still at 0 dB. Putting it back failed ({}); set it "
                "to {}% in GNOME's Sound settings.")
#: Why there is nothing to hold: the G432's card gives no route of the shape find_route reads.
NO_ROUTE = "PipeWire gives no volume for the G432's output"
#: The notes for the default output: switch_off's when the equalizer was it, and switch_on's
#: when the equalizer was it before it went off with the G432 unplugged or the G432's own
#: output is it.
MOVED = "The G432's own output is the default output now."
NOT_MOVED = ("The default output did not move to the G432's own output. Picking it failed "
             "({}); pick an output in GNOME's Sound settings.")
MOVED_NOWHERE = (f"{DESCRIPTION} was the default output. With the G432 unplugged, PipeWire "
                 "picks another.")
RESELECTED = f"{DESCRIPTION} is the default output again."
#: Why a pick of the default output did not go: WirePlumber keeps the "default" metadata.
NO_METADATA = "pw-metadata found no default metadata, which WirePlumber keeps"
NOT_RESELECTED = (f"{DESCRIPTION} did not become the default output again. Picking it failed "
                  "({}); pick it in GNOME's Sound settings.")
SELECTED = f"{DESCRIPTION} is the default output now."
#: switch_on's note when the G432's own output is the default and stays so: the G432 is
#: then not held, so what plays there is never louder.
NOT_SELECTED = (f"{DESCRIPTION} did not become the default output. Picking it failed ({{}}), "
                f"so the G432 keeps its own volume; switch {DESCRIPTION} off and on again to "
                "try once more.")
#: switch_on's note when Gearbench Spatial plays to the G432 and did not move to the
#: equalizer within spatial.SETTLE: the G432 is then not held, so spatial sound, still
#: played straight to it, is never louder.
SPATIAL_NOT_MOVED = (f"{spatial.DESCRIPTION} did not move to {DESCRIPTION} within "
                     f"{spatial.SETTLE:g} s. The G432's own volume stayed where it was; "
                     f"switch {DESCRIPTION} off and on again to try once more.")
#: switch_on's note when the watch for the G432's return did not start, with the cause.
NO_WATCH = ("Gearbench is not watching for the G432's return. Starting the watch failed "
            "({}): if the G432 is unplugged and plugged back in, switch "
            f"{DESCRIPTION} off and on again to hold it at 0 dB.")
#: After the G432 returns, how long the watch holds its route again each time it shows
#: another volume, in seconds: WirePlumber 0.5.13 sets a returning card's routes more than
#: once, from the levels it stored (select-routes.lua, state-routes.lua). Assumed, not
#: timed on the G432.
RETURN_WINDOW = 5.0
#: How near volumeBase a channel counts as held: pw-cli sets six places, which PipeWire
#: keeps as a 32-bit float.
HELD_TOLERANCE = 1e-5
#: The watch's journal lines.
WATCH_HELD = f"{WATCH_NAME}: the G432 is back; its own output is at 0 dB again"
WATCH_UNREAD = spatial.UNREAD.format(WATCH_NAME)
WATCH_ENDED = (f"{WATCH_NAME}: pw-dump --monitor ended ({{}}), so a G432 plugged back in "
               "stays at its own volume")
#: update's failure after its save, with the cause: said as saved, so a retry does not make
#: the change again, as a second --save-as would save a second preset.
NOT_SET_LIVE = (f"The change is saved, but was not set on {DESCRIPTION} ({{}}); "
                "it applies at its next start.")


class EqError(spatial.SpatialError):
    """A step that failed, or gains or a filter the chain cannot take, in words."""


class NotSetLive(EqError):
    """update's failure after its save, NOT_SET_LIVE's sentence: the change is kept."""


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
#: clip.
DEFAULT_PRESETS = (
    Preset("Rock and Alt", Gains(bands=(0, 5, -5, -5, -3, 0, 3, 8, 3, 0), bass=8, treble=5)),
    Preset("FPS Spatial", Gains(bands=(0, 3, -5, -3, 0, 0, 8, 10, 0, 0), bass=-5, treble=3)),
    Preset("Everyday", Gains(bands=(0, 3, -3, -3, 0, 0, 5, 8, 3, 0), bass=5, treble=3)),
)
#: The shipped presets' numbers, as the tab and the CLI give them; a preset saved as new
#: takes the next number after the last.
SHIPPED = tuple(range(1, len(DEFAULT_PRESETS) + 1))
#: The preset played with no file: Everyday, the daily one, in place of a flat one.
DEFAULT_ACTIVE = 3
#: g432-eq.json's version since the presets; a file with no version is version 1's
#: gains alone.
PRESETS_VERSION = 2
#: The name a version 1 file's gains take as preset 1, unless they are all 0 dB.
CUSTOM = "Custom"
#: Where a version 1 file's bytes are kept, in the settings folder's backups, before its
#: first save as version 2.
BACKUP_NAME = "g432-eq.v1.json"


#: Why a preset saved as new has no Reset to default, and why a shipped one is not deleted.
NO_DEFAULT = "Preset {} was saved as new, so it has no default to go back to."
NO_DELETE = "Preset {} ships with Gearbench and stays; a reset puts its default back."
#: Why a g432-eq.json that does not read takes no change: the presets in it are the user's
#: own, so it is never written over. The file's path, then the cause.
UNREAD = ("{} could not be read ({}), so the equalizer plays the shipped presets and "
          "Gearbench leaves the file as it is: fix it, or move it away to start from them.")


class PresetsRefused(config.ConfigError):
    """A change to g432-eq.json while it does not read or was read in part (Presets.whole):
    UNREAD's sentence or eq_presets.PARTIAL's."""


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
            raise ValueError(f"A preset is a number from 1 to {len(self.presets)}, not "
                             f"{wording.written(value)}.")
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


@dataclass(frozen=True)
class Filter:
    """One of the chain's builtin filters: its node's name, which its control is set by,
    its label, and its frequency in Hz, its Q and its gain in dB."""

    name: str
    label: str
    freq: int
    q: float
    gain: int


@dataclass(frozen=True)
class Route:
    """The G432's output as its card's Route param gives it: the card Device's id, the
    route's index and device, each channel's volume, and volumeBase, the volume at which
    the adapter's own is at 0 dB: PipeWire's ACP sets the hardware volume to the volume
    over volumeBase, which it takes from the mixer path's highest dB (acp.c's
    mixer_volume_init and set_volume, read 2026-10-17)."""

    card: int
    index: int
    device: int
    volumes: tuple[float, ...]
    base: float


@dataclass(frozen=True)
class Record:
    """What switch_on and switch_off keep in RECORD_NAME between them: the G432's own
    volume from before the hold, each channel's, which marks a hold switch_off has to undo
    and is what it puts back when the equalizer's volume cannot be read, while the G432's
    route is at volumeBase (is_held; stale_record_dropped forgets it), and whether the
    equalizer was the default output when it went off with the G432 unplugged, for
    switch_on to make it the default again."""

    volumes: tuple[float, ...] | None = None
    reselect: bool = False


@dataclass(frozen=True)
class Seen:
    """What the watch saw at pw-dump's last print: whether the G432's route was there,
    until when, after the print that showed it again, the watch holds it again, and whether
    it said that return's hold."""

    present: bool = False
    until: float = 0.0
    said: bool = False


@dataclass(frozen=True)
class State(pipewire_unit.UnitState):
    sink_id: int | None = None        # the chain's sink, made by the unit's process
    target: str | None = None         # the G432's output's node name, None while unplugged
    linked: bool = False              # a link from the chain's output to that output
    presets: Presets = Presets()


def hertz(freq: int) -> str:
    """A frequency as the tab and the CLI give it: 500 Hz, 2 kHz."""
    return f"{freq // 1000} kHz" if freq >= 1000 else f"{freq} Hz"


def signed(gain: int) -> str:
    return f"{gain:+d} dB"


def checked(gains: Gains) -> Gains:
    """`gains`, or ValueError naming the first that is not a whole dB in DB_RANGE."""
    low, high = DB_RANGE
    if len(gains.bands) != len(BAND_FREQS):
        raise ValueError(f"The equalizer has {len(BAND_FREQS)} bands, not {len(gains.bands)}.")
    names = ("bass", *(hertz(freq) for freq in BAND_FREQS), "treble")
    for name, gain in zip(names, gains.all(), strict=True):
        if not wording.whole(gain) or not low <= gain <= high:
            raise ValueError(f"The {name} gain is a whole number of dB from {low} to "
                             f"{high}, not {wording.written(gain)}.")
    return gains


def gains_path() -> Path:
    return config.config_dir() / "g432-eq.json"


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


def _added(data: object, saved: list) -> tuple[list[tuple[int, Preset]], list[str]]:
    """The presets saved as new in a version 2 file's `data`, `saved` its entries, past the
    shipped ones, each with its number in the file, and what was left out
    (eq_presets.read_each): a `presets` that is neither a list nor null, each preset that is
    not an object or has a name preset_name refuses, and those past PRESETS_MAX."""
    added, left_out = eq_presets.read_each(
        data, saved, lambda entry: Preset(preset_name(entry.get("name")), _gains_of(entry)),
        len(SHIPPED))
    if (past := len(SHIPPED) + len(added) - PRESETS_MAX) > 0:
        left_out.append(f"{wording.counted(past, 'preset')} past the {PRESETS_MAX}th")
    return added[:PRESETS_MAX - len(SHIPPED)], left_out


def presets_of(data: object, path: Path) -> Presets:
    """The presets in `data`, g432-eq.json's at `path`. Version 1's gains are preset 1,
    CUSTOM, the active one, unless they are all 0 dB, which are the defaults. In version 2
    each shipped preset is read on its own (_preset_of), a list too short for them keeps
    the defaults past its end, each saved as new after them is kept if it reads (_added),
    up to PRESETS_MAX in all, and the active one is the preset the file's `active`
    numbered, DEFAULT_ACTIVE when that one was left out or is no preset. Presets that are
    neither a list nor null, and each preset left out, give eq_presets.PARTIAL's sentence
    (`refused`); data of another shape or version is the defaults, with UNREAD's."""
    if isinstance(data, dict) and "version" not in data:
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
    added, left_out = _added(data, saved)
    numbers = [*SHIPPED, *(number for number, _preset in added)]
    active = cast("dict", data).get("active")
    return Presets(numbers.index(active) + 1 if wording.whole(active) and active in numbers
                   else DEFAULT_ACTIVE,
                   (*presets, *(preset for _number, preset in added)),
                   eq_presets.sentence(eq_presets.PARTIAL, path, "; ".join(left_out))
                   if left_out else "")


def load_presets() -> Presets:
    """The saved presets, the defaults with no file. A file that does not read gives the
    defaults, and one read in part the presets it held that read, each refusing a change
    (Presets.refused)."""
    target = gains_path()
    try:
        data = config.read_json(target)
    except FileNotFoundError:
        return Presets()
    except (OSError, config.ConfigError) as exc:
        why = config.unprefixed(wording.reason(exc), target)
        return Presets(refused=eq_presets.sentence(UNREAD, target, why))
    return presets_of(data, target)


def load_gains() -> Gains:
    """The active preset's gains, which the chain plays."""
    return load_presets().current.gains


def save_presets(presets: Presets) -> None:
    """The presets to g432-eq.json, atomically, each checked first. A version 1 file's
    bytes go to the backups first (config.keep_old_version), once."""
    if not len(SHIPPED) <= len(presets.presets) <= PRESETS_MAX:
        raise ValueError(f"g432-eq.json holds {len(SHIPPED)} to {PRESETS_MAX} presets, not "
                         f"{len(presets.presets)}.")
    for preset in presets.presets:
        checked(preset.gains)
        preset_name(preset.name)
    presets.number(presets.active)
    target = gains_path()
    # Version 1 is the only one before PRESETS_VERSION, so BACKUP_NAME's.
    config.keep_old_version(target, lambda _version: config.backup_dir() / BACKUP_NAME,
                            PRESETS_VERSION)
    eq_presets.write(target, PRESETS_VERSION, {"active": presets.active},
                     [{"name": preset.name, "bands": preset.gains.bands,
                       "bass": preset.gains.bass, "treble": preset.gains.treble}
                      for preset in presets.presets], ".g432-eq-")


def filters(gains: Gains) -> list[Filter]:
    """The chain's filters for `gains`, in the order the sound goes through them."""
    return [Filter("bass", "bq_lowshelf", BASS_FREQ, SHELF_Q, gains.bass),
            *(Filter(f"band{number}", "bq_peaking", freq, BAND_Q, gain) for number, (freq, gain)
              in enumerate(zip(BAND_FREQS, gains.bands, strict=True), 1)),
            Filter("treble", "bq_highshelf", TREBLE_FREQ, SHELF_Q, gains.treble)]


def graph_lines(gains: Gains) -> tuple[list[str], list[str], str, str]:
    """The graph's nodes, each filter, the links that chain them in that order, and the
    graph's one input and one output port. PipeWire counts every port no link reaches as
    the graph's, and runs the graph once a channel only when it has one input and one
    output ("invalid ports" otherwise, on the G432 on 2026-10-17)."""
    chain = filters(gains)
    nodes = [f'{{ type = builtin label = {filt.label} name = {filt.name} '
             f'control = {{ "Freq" = {filt.freq:.1f} "Q" = {filt.q} '
             f'"Gain" = {filt.gain:.1f} }} }}' for filt in chain]
    links = [f'{{ output = "{before.name}:Out" input = "{after.name}:In" }}'
             for before, after in pairwise(chain)]
    return nodes, links, f"{chain[0].name}:In", f"{chain[-1].name}:Out"


def render(gains: Gains, target: str) -> str:
    """The chain's conf for `gains`, its output aimed at `target`, the G432's output."""
    checked(gains)
    if not target or spatial.NOT_IN_CONF.search(target):
        raise EqError(f"the G432's output, {wording.escaped(target)}, cannot be named in "
                      "the chain's conf")
    nodes, links, first, last = graph_lines(gains)
    indent = " " * 20
    return TEMPLATE.format(nodes="\n".join(indent + line for line in nodes),
                           links="\n".join(indent + line for line in links),
                           first=first, last=last, channels=CHANNELS,
                           description=DESCRIPTION, sink=SINK, output=OUTPUT, target=target,
                           client_context=spatial.CLIENT_CONTEXT,
                           client_modules=spatial.CLIENT_MODULES)


TEMPLATE = """\
# Gearbench: {description}. Gearbench writes this file from the active
# preset's gains in its g432-eq.json at each switch-on, and a transient
# user unit, gearbench-g432-eq.service, runs it: pipewire -c <this file>.
#
# A bass shelf, ten peaking filters an octave apart and a treble shelf:
# PipeWire's builtin biquads, with no preamp, so a boost is a boost on top of
# the volume. The filter chain runs the graph once for each of the two
# channels.

{client_context}

context.modules = [
{client_modules}

    {{ name = libpipewire-module-filter-chain
        args = {{
            node.description = "{description}"
            media.name       = "{description}"
            filter.graph = {{
                nodes = [
{nodes}
                ]
                links = [
{links}
                ]
                inputs  = [ "{first}" ]
                outputs = [ "{last}" ]
            }}
            audio.channels = {channels}
            audio.position = [ FL FR ]
            capture.props = {{
                node.name   = "{sink}"
                media.class = Audio/Sink
                # Gearbench sets its volume at each switch-on, to the G432's
                # own: WirePlumber 0.5.13 neither restores a volume it saved
                # nor saves one for a node with this (state-stream.lua).
                state.restore-props = false
            }}
            playback.props = {{
                node.name          = "{output}"
                node.passive       = true
                target.object      = "{target}"
                # It plays on the G432 or nowhere, never on another output.
                node.dont-fallback = true
                node.linger        = true
            }}
        }}
    }}
]
"""


def conf_path() -> Path:
    """CONF_NAME in the runtime folder, RECORD_NAME's too: config.private_runtime's, refused
    as EqError while XDG_RUNTIME_DIR is unset or names a folder another account owns or may
    write in."""
    return config.private_runtime(EqError, NO_RUNTIME, DESCRIPTION) / CONF_NAME


def levels(value: object) -> tuple[float, ...] | None:
    """`value` as volumes, one a channel, if it is a list of numbers of 0 or more, at least
    one; else None."""
    if not (isinstance(value, list) and value
            and all(wording.number(v) and v >= 0 for v in value)):
        return None
    return tuple(float(v) for v in value)


def _props(info: dict) -> dict:
    props = info.get("props")
    return props if isinstance(props, dict) else {}


def g432_cards(objects: Sequence[object]) -> set[int]:
    """The ids of the G432's card Devices in pw-dump's objects, on any profile
    (spatial.card_nodes)."""
    return set(spatial.card_nodes(objects, spatial.G432.card)[0])


def g432_node(objects: Sequence[object], media_class: str = "Audio/Sink") -> dict | None:
    """The props of the G432's output, or of its node of `media_class`, in pw-dump's objects:
    the first of its card's nodes (spatial.card_nodes) with a name, None while there is
    none."""
    _cards, nodes = spatial.card_nodes(objects, spatial.G432.card)
    return next((props for props in nodes if props.get("media.class") == media_class
                 and isinstance(props.get("node.name"), str)), None)


def node_id(objects: Sequence[object], props: dict | None) -> int | None:
    """The id of the Node in pw-dump's objects whose props are `props`, as g432_node gives
    them: by those props, not their node.name, which any client can give a node of its
    own; None for None."""
    return next((node for node, _info, found in spatial.of_type(objects, "Node")
                 if found is props), None)


def find_target(objects: Sequence[object]) -> str | None:
    """The G432's output's node name in pw-dump's objects, None while there is none."""
    props = g432_node(objects)
    return None if props is None else props["node.name"]


def _route(route: object, card: int, device: object) -> Route | None:
    """`route`, one of the card's Route params, as a Route if it is the output's: its
    direction Output, its device the node's card.profile.device, a whole index, and a
    volume of 0 or more for each channel and a volumeBase above 0 in its props."""
    if not isinstance(route, dict) or route.get("direction") != "Output":
        return None
    values = _props(route)
    volumes, base = levels(values.get("channelVolumes")), values.get("volumeBase")
    if (not wording.whole(device) or route.get("device") != device
            or not wording.whole(route.get("index")) or volumes is None
            or not wording.number(base) or not base > 0):
        return None
    return Route(card, route["index"], device, volumes, float(base))


def find_route(objects: Sequence[object]) -> Route | None:
    """The G432's output's route in pw-dump's objects, None while the G432 is not in
    PipeWire or its card gives no route of the shape _route reads."""
    props = g432_node(objects)
    if props is None:
        return None
    card = props["device.id"]
    for card_id, info, _card in spatial.of_type(objects, "Device"):
        params = info.get("params") if card_id == card else None
        routes = params.get("Route") if isinstance(params, dict) else None
        for route in routes if isinstance(routes, list) else ():
            if (found := _route(route, card, props.get("card.profile.device"))) is not None:
                return found
    return None


def sink_volumes(objects: Sequence[object], pid: int) -> tuple[float, ...] | None:
    """The chain's sink's volume on each channel in pw-dump's objects, the channelVolumes of
    its Props, from the sink of the client whose process is `pid`, as graph takes it; None
    while there is none, or its Props give none."""
    owned = pipewire_unit.clients_of(objects, pid)
    for _node, info, props in spatial.of_type(objects, "Node"):
        if props.get("node.name") == SINK and props.get("client.id") in owned:
            params = info.get("params")
            entries = params.get("Props") if isinstance(params, dict) else None
            found = (levels(entry.get("channelVolumes")) for entry in entries
                     if isinstance(entry, dict)) if isinstance(entries, list) else ()
            return next((volumes for volumes in found if volumes is not None), None)
    return None


def graph(objects: Sequence[object], pid: int) -> dict:
    """State's PipeWire fields from pw-dump's objects. The chain's sink and output are the
    nodes of those names made by the client whose process is `pid`, the unit's MainPID, by
    the pid PipeWire took from its socket, since any client can name a node
    gearbench-g432-eq; the G432's output is its card's own node (g432_node)."""
    nodes = pipewire_unit.owned_nodes(objects, pid)
    found = g432_node(objects)
    return {"sink_id": nodes.get(SINK), "target": None if found is None else found["node.name"],
            "linked": pipewire_unit.linked(objects, nodes.get(OUTPUT), node_id(objects, found))}


def dumped_graph(run: spatial.Run, pid: int) -> dict:
    """graph of a fresh pw-dump (spatial.dumped)."""
    return spatial.dumped(run, lambda objects: graph(objects, pid))


def read_state(run: spatial.Run = subprocess.run) -> State:
    """What systemd and PipeWire say now, and the saved presets. Never raises."""
    return State(presets=load_presets(), **pipewire_unit.read_fields(run, UNIT, graph))


def summary(state: State) -> tuple[str, str]:
    """(kind, words) for `state`: the unit's own (pipewire_unit.unit_summary), else the
    first condition below that holds."""
    if (unit := pipewire_unit.unit_summary(state)) is not None:
        return unit
    if state.sink_id is None:
        return "starting", "starting"
    if state.target is None:
        return "waiting", "on; it plays nothing until the G432 is plugged in"
    if not state.linked:
        return "unlinked", "on, but not linked to the G432"
    return "ready", "on, playing to the G432"


def preset_words(presets: Presets, number: int) -> str:
    """A preset as the CLI names it: its number and its name."""
    return f"{number}, {presets.get(number).name}"


def describe(state: State, number: int | None = None) -> list[tuple[str, str]]:
    """(label, value) rows for the CLI: the state, the output, the presets, and the gains
    of preset `number`, the active one when None, which a row says it is not."""
    presets = state.presets
    shown = presets.active if number is None else number
    gains = presets.get(shown).gains
    active = "" if shown == presets.active else (
        f" (not the active one, {preset_words(presets, presets.active)})")
    return [
        ("equalizer", summary(state)[1]),
        ("output", f"{DESCRIPTION} ({SINK})"),
        ("presets", "; ".join(preset_words(presets, n) for n in presets.numbers)),
        ("preset", preset_words(presets, shown) + active),
        ("bass", f"{signed(gains.bass)}, a shelf at {hertz(BASS_FREQ)}"),
        *((hertz(freq), signed(gain)) for freq, gain in zip(BAND_FREQS, gains.bands, strict=True)),
        ("treble", f"{signed(gains.treble)}, a shelf at {hertz(TREBLE_FREQ)}"),
    ]


def props(gains: Gains) -> str:
    """The Props `pw-cli set-param` sets for `gains`: each filter's gain."""
    pairs = (f'"{filt.name}:Gain" {filt.gain:.1f}' for filt in filters(gains))
    return f"{{ params = [ {' '.join(pairs)} ] }}"


def record_path() -> Path:
    return conf_path().with_name(RECORD_NAME)


def switch_folder() -> Path:
    """The runtime folder, where a switch's OSError comes from, the equalizer's or the noise
    suppression's: the folder's stat, the conf's write or the equalizer's record. Named
    without conf_path, whose second stat could raise again; XDG_RUNTIME_DIR is set, since
    conf_path refuses it unset before anything is written."""
    return cast("Path", config.runtime_home())


def load_record() -> Record:
    """The record, an empty one with no runtime folder, which switch_on refuses to run
    without, or no file. `volumes` is none for a value that is not a list of 1 to 8 numbers
    of 0 or more, and `reselect` true only for true. EqError for a runtime folder another
    account owns or may write in (conf_path): a record there may be that account's, and
    put_back would give the G432 its volume, saved."""
    if config.runtime_home() is None:
        return Record()
    try:
        data = config.read_json(record_path())
    except (OSError, config.ConfigError):
        return Record()
    if not isinstance(data, dict):
        return Record()
    volumes = levels(data.get("volumes"))
    return Record(volumes if volumes is not None and len(volumes) <= 8 else None,
                  data.get("reselect") is True)


def save_record(record: Record) -> None:
    """The record, atomically and the user's alone (write_json's mkstemp), or for an empty
    one no file."""
    path = record_path()
    if record == Record():
        path.unlink(missing_ok=True)
    else:
        config.write_json(path, {"volumes": record.volumes, "reselect": record.reselect},
                          f".{RECORD_NAME}-")


def route_argv(route: Route, volumes: Sequence[float], save: bool) -> list[str]:
    """pw-cli's set-param of the G432's output's route to `volumes`, as WirePlumber 0.5.13's
    apply-routes.lua sets a route: its index and device, the volumes in its props, and
    save, which decides whether WirePlumber keeps the level (state-routes.lua)."""
    shown = " ".join(f"{volume:.6f}" for volume in volumes)
    param = (f"{{ index: {route.index}, device: {route.device}, props: {{ channelVolumes: "
             f"[ {shown} ] }}, save: {'true' if save else 'false'} }}")
    return ["pw-cli", "set-param", str(route.card), "Route", param]


def _pw_cli(run: spatial.Run, argv: list[str]) -> str | None:
    """Run a `pw-cli set-param`: None when it went through, else why not, in words.
    pw-cli 1.6 exits 0 when its command fails (spatial.set_room): its words decide."""
    try:
        done = spatial.run_program(run, argv, check_exit=False)
    except spatial.SpatialError as err:
        return str(err)
    if done.returncode or "Error:" in done.stderr:
        return spatial.program_words(done, "pw-cli")
    return None


def spread(volumes: Sequence[float], count: int) -> list[float]:
    """`volumes` on `count` channels: as they are for as many, else the loudest on each."""
    return list(volumes) if len(volumes) == count else [max(volumes)] * count


def carried_on(volumes: Sequence[float], base: float) -> list[float]:
    """The sink's volume for the G432's own `volumes`, as loud through a G432 held at 0 dB:
    ACP plays a route's volume v at v / volumeBase on the adapter's PCM (acp.c's
    set_volume, PipeWire 1.6.2, read 2026-10-17), so the sink takes v / volumeBase, at most
    CARRY_MAX. Not yet heard on the G432."""
    return [min(volume / base, CARRY_MAX) for volume in spread(volumes, CHANNELS)]


def carried_off(volumes: Sequence[float], route: Route) -> list[float]:
    """The G432's own volume for the sink's `volumes`, as loud with the hold gone: each times
    volumeBase, carried_on the other way, with no cap."""
    return [volume * route.base for volume in spread(volumes, len(route.volumes))]


def sink_argv(sink_id: int, volumes: Sequence[float]) -> list[str]:
    """pw-cli's set-param of the sink's Props to `volumes`, as GNOME's slider sets them."""
    shown = " ".join(f"{volume:.6f}" for volume in volumes)
    return ["pw-cli", "set-param", str(sink_id), "Props", f"{{ channelVolumes: [ {shown} ] }}"]


def gnome_percent(volume: float) -> int:
    """A route's volume as GNOME's Sound settings shows it: the cube root, in percent, as
    WirePlumber's 0.064 shows as 40 %."""
    return round(volume ** (1 / 3) * 100)


def watch_argv() -> list[str]:
    """systemd-run's command line for the transient unit that watches for the G432's
    return: bound to UNIT, so it stops when UNIT does (BindsTo= in systemd.unit(5)), and
    given the package's folder on PYTHONPATH, where the window and the CLI found it."""
    return ["systemd-run", "--user", f"--unit={WATCH_UNIT}",
            f"--description={DESCRIPTION}, watching for the G432", "--collect", "--quiet",
            "--slice=session.slice", f"--property=BindsTo={UNIT}", f"--property=After={UNIT}",
            *(f"--property={setting}" for setting in pipewire_unit.HARDENING),
            f"--setenv=PYTHONPATH={Path(__file__).resolve().parent.parent}",
            "/usr/bin/python3", "-s", "-P", "-m", "gearbench.g432_eq", "watch"]


def held_and_watched(run: spatial.Run, route: Route | None) -> str | None:
    """hold, then, once the hold went through, the watch for the G432's return: the note
    to say, else None."""
    if (note := hold(run, route)) is not None:
        return note
    try:
        spatial.run_program(run, watch_argv())
    except spatial.SpatialError as err:
        return NO_WATCH.format(err)
    return None


def carry(run: spatial.Run, route: Route, record: Record) -> str | None:
    """Give the sink, once the unit's process has made it, the G432's own volume as
    carried_on has it: the record's, a hold's whose put-back never ran and which still
    holds the G432 (switch_on drops one that does not first), else the route's, read
    before the hold. NOT_CARRIED's note when it could not, else None."""
    own = route.volumes if record.volumes is None else record.volumes
    try:
        sink_id = _running_sink(run)
    except spatial.SpatialError as err:
        return NOT_CARRIED.format(err)
    if sink_id is None:
        return NOT_CARRIED.format(NO_SINK)
    failed = _pw_cli(run, sink_argv(sink_id, carried_on(own, route.base)))
    return None if failed is None else NOT_CARRIED.format(failed)


def hold(run: spatial.Run, route: Route | None) -> str | None:
    """Set the G432's own output to 0 dB, unsaved, keeping the volume it had in the record
    first; a record already there is a hold whose put-back never ran, whose level it keeps:
    the watch's, or one switch_on found still holding the G432 (stale_record_dropped).
    NOT_HELD's note when it could not be held, else None."""
    if route is None:
        return NOT_HELD.format(NO_ROUTE)
    record = load_record()
    if record.volumes is None:
        save_record(replace(record, volumes=route.volumes))
    failed = _pw_cli(run, route_argv(route, [route.base] * len(route.volumes), save=False))
    return None if failed is None else NOT_HELD.format(failed)


def put_back(run: spatial.Run, route: Route | None,
             volumes: tuple[float, ...] | None) -> str | None:
    """With a hold in the record, give the G432's own output, saved, the sink's `volumes` as
    carried_off has them, or with none read the volume the record keeps while the G432 is
    still held (is_held), and forget the hold. With no route there is none to set, as while
    the G432 is unplugged, and WirePlumber gives it the level it saved, which the hold never
    was; with no volumes and a route off volumeBase, the G432 has its own level again, and
    nothing is set either. NOT_PUT_BACK's note when the write failed, which keeps the
    record for the next switch_off."""
    record = load_record()
    if record.volumes is None:
        return None
    if route is not None:
        level = (carried_off(volumes, route) if volumes is not None
                 else record.volumes if is_held(route) else None)
        if level is not None and (failed := _pw_cli(run, route_argv(route, level, True))):
            return NOT_PUT_BACK.format(failed, gnome_percent(max(level)))
    save_record(replace(record, volumes=None))
    return None


def is_held(route: Route) -> bool:
    """Whether each channel of `route` is at volumeBase, as a hold leaves it."""
    return all(abs(volume - route.base) <= HELD_TOLERANCE for volume in route.volumes)


def stale_record_dropped(route: Route | None) -> None:
    """Forget the record's level when `route` shows the G432 is not held: a PipeWire stop
    stops the unit with no switch_off, and WirePlumber's next start gives the G432 the
    level it saved, which the user may have changed since."""
    record = load_record()
    if record.volumes is not None and route is not None and not is_held(route):
        save_record(replace(record, volumes=None))


def default_sink(objects: Sequence[object]) -> str | None:
    """The default output's node name in pw-dump's objects: the "default" Metadata's
    default.audio.sink, the one WirePlumber picked, None for none."""
    for obj in objects:
        if (isinstance(obj, dict) and obj.get("type") == "PipeWire:Interface:Metadata"
                and isinstance(obj.get("props"), dict)
                and obj["props"].get("metadata.name") == "default"
                and isinstance(obj.get("metadata"), list)):
            for item in obj["metadata"]:
                value = item.get("value") if isinstance(item, dict) else None
                if (isinstance(value, dict) and item.get("subject") == 0
                        and item.get("key") == "default.audio.sink"
                        and isinstance(value.get("name"), str)):
                    return value["name"]
    return None


def default_argv(name: str) -> list[str]:
    """pw-metadata's write of the output picked as the default, default.configured.audio.sink,
    as GNOME's pick and wpctl set-default write it; WirePlumber 0.5.13 then makes it the
    default output while it is there (find-selected-default-node.lua)."""
    return ["pw-metadata", "-n", "default", "0", "default.configured.audio.sink",
            json.dumps({"name": name}), "Spa:String:JSON"]


def _pick(run: spatial.Run, name: str) -> str | None:
    """Make `name` the default output and wait up to spatial.SETTLE for WirePlumber to pick
    it, which moves the streams that follow the default with it: None, else why the write
    failed, in words. A pick that has not shown by then is waited for no longer."""
    try:
        done = spatial.run_program(run, default_argv(name), check_exit=False)
    except spatial.SpatialError as err:
        return str(err)
    if done.returncode:
        return spatial.program_words(done, "pw-metadata")
    # pw-metadata 1.6.2 says "set property:" for a write that went, and exits 0 saying
    # nothing for a metadata it does not find (measured 2026-10-17).
    if "set property:" not in done.stdout:
        return NO_METADATA
    deadline = time.monotonic() + spatial.SETTLE
    while time.monotonic() < deadline:
        try:
            if spatial.dumped(run, default_sink) == name:
                break
        except spatial.SpatialError:
            # Only a wait: the write went through, and nothing here depends on seeing it.
            break
        time.sleep(spatial.SETTLE_STEP)
    return None


def give_back_default(run: spatial.Run, target: str | None) -> str:
    """With the equalizer the default output, make the G432's own output, `target`, the
    default before the sink goes, so the streams that follow the default move to the G432
    first: WirePlumber 0.5.13 pauses the MPRIS players of the streams playing to a sink that
    goes (linking/mpris-pause.lua). With the G432 unplugged, keep that the equalizer was
    the default, for the next switch_on; with it there, the next switch_on finds its
    output the default, unless the user picked another, which it leaves. The note to
    say."""
    if target is None:
        save_record(replace(load_record(), reselect=True))
        return MOVED_NOWHERE
    failed = _pick(run, target)
    return MOVED if failed is None else NOT_MOVED.format(failed)


def spatial_moved(run: spatial.Run) -> str | None:
    """Wait up to spatial.SETTLE, while Gearbench Spatial's chain plays to the G432 aimed at
    another node than the equalizer's sink, for its unit's process to aim it there, which
    it does once the sink is in PipeWire: SPATIAL_NOT_MOVED's note when it did not, else
    None. A chain on the A50, none, or one not read waits for nothing: with no chain on the
    G432's own output, a hold makes nothing louder."""
    deadline = time.monotonic() + spatial.SETTLE
    while True:
        state = spatial.read_state(run)
        if state.headset != spatial.G432.key or state.target == SINK:
            return None
        if time.monotonic() >= deadline:
            return SPATIAL_NOT_MOVED
        time.sleep(spatial.SETTLE_STEP)


def switch_on(run: spatial.Run = subprocess.run) -> list[str]:
    """Write the conf for the active preset's gains, aimed at the G432's output, start the
    unit, give its sink the G432's volume, make it the default output if the G432's own
    output is the default or it was the default when it went off with the G432 unplugged,
    wait for Gearbench Spatial on the G432 to move to it, and then hold the G432 at 0 dB, so
    a stream that moves to the equalizer leaves the G432 before the G432 is at 0 dB, and is
    never louder on the way, and start the watch for the G432's return; nothing while it
    runs or starts. A sink that did not take the volume, a pick that failed while the
    G432's own output is the default, or a Gearbench Spatial that did not move, leaves the
    G432 unheld and unwatched. EqError while the G432's output is not in PipeWire, NO_OUTPUT
    while its card is, for a runtime folder conf_path refuses, and STOPPED when the unit
    stopped as it started, looked for once it has settled (pipewire_unit.settle_start),
    before any pick or hold. The notes to say, in words."""
    if pipewire_unit.running(run, UNIT):
        return []
    objects = spatial.dump_objects(run)
    target = spatial.shaped(find_target, objects)
    default = spatial.shaped(default_sink, objects)
    if target is None:
        raise EqError(NO_OUTPUT if g432_cards(objects) else NO_G432)
    conf = conf_path()
    # Mode 0600 (replace_file's mkstemp), and replaced whole, so PipeWire never reads half.
    config.replace_file(conf, render(load_gains(), target).encode("utf-8"), f".{CONF_NAME}-", "")
    spatial.run_program(run, pipewire_unit.pipewire_argv(UNIT, DESCRIPTION, conf))
    route = spatial.shaped(find_route, objects)
    stale_record_dropped(route)
    carried = None if route is None else carry(run, route, load_record())
    pipewire_unit.settle_start(run, UNIT, lambda: read_state(run=run), summary,
                               EqError(STOPPED))
    notes = [] if carried is None else [carried]
    record = load_record()
    failed = None
    if record.reselect or default == target:
        failed = _pick(run, SINK)
        if failed is None:
            notes.append(RESELECTED if record.reselect else SELECTED)
        else:
            notes.append((NOT_SELECTED if default == target else NOT_RESELECTED).format(failed))
        save_record(replace(record, reselect=False))
    if (unmoved := spatial_moved(run)) is not None:
        notes.append(unmoved)
    # The music, or spatial sound, stays on the G432's own output, which a hold would make
    # louder.
    stayed = (failed is not None and default == target) or unmoved is not None
    if carried is None and not stayed and (note := held_and_watched(run, route)) is not None:
        notes.append(note)
    return notes


def switch_off(run: spatial.Run = subprocess.run) -> list[str]:
    """Stop the watch, give the G432's own output the equalizer's volume in place of the
    hold, make the G432's own output the default if the equalizer was it, then stop the
    unit, which removes the sink; a unit not loaded is off already. A put-back or a pick
    that fails is a note; a failed stop of the watch, or read of systemd or PipeWire after
    it, puts nothing back and is a note only with a hold in the record. The unit stops all
    the same. EqError, with nothing written or stopped, for a runtime folder another account
    owns or may write in (load_record): a hold the record there cannot vouch for then stays
    under the equalizer's volume, never louder. The notes to say, in words."""
    held = load_record().volumes
    try:
        # First: a watch still running holds the G432 again at the put-back's own print,
        # since the record keeps the hold until put_back drops it.
        pipewire_unit.stop(run, WATCH_UNIT)
        pid = spatial.main_pid(pipewire_unit.unit_of(run, UNIT))
        objects = spatial.dump_objects(run)
        route, target, default = (spatial.shaped(find_route, objects),
                                  spatial.shaped(find_target, objects),
                                  spatial.shaped(default_sink, objects))
        volumes = spatial.shaped(lambda found: sink_volumes(found, pid), objects)
    except spatial.SpatialError as err:
        notes = [] if held is None else [NOT_PUT_BACK.format(err, gnome_percent(max(held)))]
    else:
        notes = [note] if (note := put_back(run, route, volumes)) else []
        if default == SINK:
            notes.append(give_back_default(run, target))
    pipewire_unit.stop(run, UNIT)
    return notes


def _running_sink(run: spatial.Run) -> int | None:
    """The running chain's sink, waiting up to spatial.SETTLE for the sink of a unit that is
    starting, whose conf may hold the gains from before a save. EqError, NO_SINK's words,
    for a unit that runs or starts with no sink by then; None with no unit running."""
    deadline = time.monotonic() + spatial.SETTLE
    while True:
        unit = pipewire_unit.unit_of(run, UNIT)
        pid = spatial.main_pid(unit)
        sink_id = dumped_graph(run, pid)["sink_id"] if pid else None
        if sink_id is not None or unit.get("ActiveState") not in ("active", "activating"):
            return sink_id
        if time.monotonic() >= deadline:
            raise EqError(NO_SINK)
        time.sleep(spatial.SETTLE_STEP)


def edit(change: Callable[[Presets], Presets]) -> Presets:
    """Save what `change` makes of the saved presets, and set nothing on the chain: for a
    name, or gains that apply at the next start. The presets saved. PresetsRefused for a
    file that does not read or was read in part (Presets.whole), left as it is."""
    presets = change(load_presets().whole())
    save_presets(presets)
    return presets


def update(change: Callable[[Presets], Presets], run: spatial.Run = subprocess.run) -> bool:
    """edit, then set the active preset's gains on the running chain, which a set before
    that failed may have left on others. False when it does not run, so they apply at its
    next start; NotSetLive when the unit or the sink could not be read, the sink of a unit
    that runs or starts did not show within spatial.SETTLE, or the set failed."""
    gains = edit(change).current.gains
    try:
        sink_id = _running_sink(run)
    except spatial.SpatialError as err:
        raise NotSetLive(NOT_SET_LIVE.format(err)) from None
    if sink_id is None:
        return False
    failed = _pw_cli(run, ["pw-cli", "set-param", str(sink_id), "Props", props(gains)])
    if failed is not None:
        raise NotSetLive(NOT_SET_LIVE.format(failed))
    return True


def set_gains(gains: Gains, number: int, name: str, run: spatial.Run = subprocess.run) -> bool:
    """`gains` to the preset `name`, which `number` held when a page read the file, where
    the file holds it now (eq_presets.number_named), through update: another program, or a
    pick still running, can make another preset active after that read."""
    return update(lambda presets: presets.with_gains(
        eq_presets.number_named(presets.presets, number, name), gains), run)


def returned(seen: Seen, route: Route | None, now: float) -> tuple[Seen, bool]:
    """The watch's next Seen for the G432's `route` at `now`, and whether to hold it at 0
    dB now: while RETURN_WINDOW runs from the print that showed the route again, each
    time a channel is off volumeBase. The first print counts as a return, since the G432
    can come back between the switch-on's hold and the watch's start."""
    if route is None:
        return Seen(), False
    if not seen.present:
        seen = Seen(True, now + RETURN_WINDOW)
    return seen, not is_held(route) and now < seen.until


def watch(lines: Iterable[str], run: spatial.Run = subprocess.run,
          clock: Callable[[], float] = time.monotonic) -> None:
    """Follow pw-dump --monitor's `lines` and, while a hold is in the record, hold the G432
    at 0 dB again each time `returned` says to: after a replug WirePlumber gives it the
    level it saved, which the hold never was. Says on stderr, which the unit's journal
    keeps, the first hold of each return, however many follow it, each hold that failed,
    and each print not read. A runtime folder conf_path refuses ends it, as load_record
    raises EqError there."""
    objects: dict[int, dict] = {}
    seen = Seen()
    for found in spatial.printed(lines):
        if found is None:
            print(WATCH_UNREAD.format(spatial.NOT_A_PRINT), file=sys.stderr, flush=True)
            continue
        spatial.merge(objects, found)
        try:
            route = spatial.shaped(find_route, list(objects.values()))
        except spatial.SpatialError as err:
            print(WATCH_UNREAD.format(err), file=sys.stderr, flush=True)
            continue
        seen, due = returned(seen, route, clock())
        if due and load_record().volumes is not None:
            note = hold(run, route)
            if note is not None:
                print(f"{WATCH_NAME}: the G432 is back. {note}", file=sys.stderr, flush=True)
            elif not seen.said:
                print(WATCH_HELD, file=sys.stderr, flush=True)
                seen = replace(seen, said=True)


def main(argv: Sequence[str] | None = None,
         spawn: Callable[..., subprocess.Popen] = subprocess.Popen,
         run: spatial.Run = subprocess.run) -> int:
    """`python3 -m gearbench.g432_eq watch`, the watch unit's process: watch until pw-dump
    --monitor ends, as at PipeWire's stop, which ends the unit as failed."""
    args = sys.argv[1:] if argv is None else list(argv)
    if args != ["watch"]:
        print("usage: python3 -m gearbench.g432_eq watch", file=sys.stderr)
        return 2
    try:
        with spawn(spatial.MONITOR, stdout=subprocess.PIPE, encoding="utf-8",
                   errors="replace") as monitor:
            watch(cast("IO[str]", monitor.stdout), run=run)
    except OSError as err:
        print(WATCH_ENDED.format(f"pw-dump did not run: {wording.reason(err)}"), file=sys.stderr)
        return 1
    print(WATCH_ENDED.format(wording.ended("pw-dump", monitor.returncode)), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
