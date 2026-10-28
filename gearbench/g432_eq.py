"""The equalizer of the Logitech G432, and of each headset of its family with none of its own
(EQ_MODELS): a PipeWire filter chain that plays the active preset of gearbench.g432_presets,
the transient user unit that runs it, and what PipeWire says of it.

Logitech G432 Equalizer is a stereo sink whose output plays to the G432's own output and
no other: a bass shelf, ten peaking filters at G HUB's bands and a treble shelf, PipeWire's
own builtin filters, with no preamp, so a boost is a boost on top of the volume. It plays
the active one of its presets, each a name and its gains, kept in g432-eq.json. Off by
default. On is `gearbench-g432-eq.service` running: a transient unit that `systemd-run
--user` makes for `render`'s conf, so it is off again after a logout, and off
stops it, which removes the sink. While it runs the G432's own output is held at 0 dB and
unmuted, so the equalizer's volume and mute are the ones that apply: on gives the equalizer
the G432's volume and mute and off gives the G432 the equalizer's, each as loud as the other
was. While it runs, a second transient unit, `gearbench-g432-eq-watch.service`, runs
`python3 -m gearbench.g432_eq watch`, which follows PipeWire and holds a G432 plugged back
in at 0 dB and unmuted again. On while the G432's own output is the default makes the equalizer the
default; off while it is the default output makes the G432's own output the default, and
with that output gone then (the G432 unplugged, or its card on a profile with no output),
the next on makes the equalizer the default again. A gain
change while it runs is set live with `pw-cli`. Gearbench Spatial, while it plays to the
G432, goes through the equalizer while the sink its unit's process made is in PipeWire
(gearbench.spatial_unit), so on waits for it to move there before the hold, and for
anything else that plays straight to the G432's own output to leave it: a stream with an
output of its own that stays leaves the G432 unheld. So does a volume the sink did not take,
or a failed pick of the sink as the default while the G432's own output is it, with no wait.

Another model's equalizer is the G432's under the model's own names and sentences, from its
key and its name (gearbench.g432_eq_words): Logitech G733 Equalizer,
`gearbench-g733-eq.service`, g733-eq.json. The functions take the model as `model`, the G432
by default for a caller outside this module (the ones only this module calls require it), and
the module's constants are the G432's. Standard library only, never run in the daemon; every
outside program goes through a `run` argument and `pipewire_client.run_program`, but for the
watch's pw-dump, which goes through `main`'s `spawn`, and a failure is a
`pipewire_client.StepError` in words.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from itertools import pairwise
from pathlib import Path
from typing import IO, cast

from . import (
    eq_presets,
    pipewire_client,
    pipewire_unit,
    runtime_folder,
    settings_files,
    spatial,
    wording,
)
from .g432 import G432_MODEL, HeadsetModel
from .g432_eq_words import EQ_KEYED, WORDS
from .g432_presets import (
    BAND_FREQS,
    BAND_Q,
    BASS_FREQ,
    SHELF_Q,
    TREBLE_FREQ,
    Gains,
    Presets,
    checked,
    edit,
    load_gains,
    load_presets,
    presets_file_name,
)
from .wording import hertz, numbered, signed

#: The G432's words, the default model's, under the module's own names.
G432_WORDS = WORDS[G432_MODEL.key]
UNIT = G432_WORDS.unit
WATCH_NAME = G432_WORDS.watch_name
SINK = G432_WORDS.sink
OUTPUT = G432_WORDS.output
DESCRIPTION = G432_WORDS.description
CONF_NAME = G432_WORDS.conf_name
RECORD_NAME = G432_WORDS.record_name
#: The sink's channels, FL and FR in the template.
CHANNELS = 2
#: The most switch_on gives the sink, its 100 %: above it the sink adds gain of its own,
#: which clips a loud passage before the G432 under it, where the G432's own volume above
#: 0 dB was the adapter's.
CARRY_MAX = 1.0
NO_G432 = G432_WORDS.no_headset
NO_OUTPUT = G432_WORDS.no_output
NOT_CARRIED = G432_WORDS.not_carried
NO_SINK = G432_WORDS.no_sink
STOPPED = G432_WORDS.stopped
NOT_HELD = G432_WORDS.not_held
NOT_PUT_BACK = G432_WORDS.not_put_back
NOT_PUT_BACK_MUTED = G432_WORDS.not_put_back_muted
NO_ROUTE = G432_WORDS.no_route
MOVED = G432_WORDS.moved
NOT_MOVED = G432_WORDS.not_moved
MOVED_NOWHERE = G432_WORDS.moved_nowhere
RESELECTED = G432_WORDS.reselected
#: Why a pick of the default output did not go: WirePlumber keeps the "default" metadata.
NO_METADATA = "pw-metadata found no default metadata, which WirePlumber keeps"
#: Why a pick that went did not show: WirePlumber did not make it the default in time.
NOT_SHOWN = f"WirePlumber did not make it the default within {pipewire_client.SETTLE:g} s"
NOT_RESELECTED = G432_WORDS.not_reselected
SELECTED = G432_WORDS.selected
NOT_SELECTED = G432_WORDS.not_selected
SPATIAL_NOT_MOVED = G432_WORDS.spatial_not_moved
SPATIAL_UNREAD = G432_WORDS.spatial_unread
PLAYED_STRAIGHT = G432_WORDS.played_straight
STRAIGHT_UNREAD = G432_WORDS.straight_unread
NO_WATCH = G432_WORDS.no_watch
#: After the headset returns, how long the watch holds its route again each time it shows
#: another volume or a mute, in seconds: WirePlumber 0.5.13 sets a returning card's routes more
#: than once, from the levels and mute it stored (select-routes.lua, state-routes.lua).
#: Assumed, not timed on the G432.
RETURN_WINDOW = 5.0
#: How near volumeBase a channel counts as held: pw-cli sets six places, which PipeWire
#: keeps as a 32-bit float.
HELD_TOLERANCE = 1e-5
WATCH_HELD = G432_WORDS.watch_held
WATCH_UNREAD = G432_WORDS.watch_unread
WATCH_ENDED = G432_WORDS.watch_ended
WATCH_NO_CHAIN = G432_WORDS.watch_no_chain
NOT_SET_LIVE = G432_WORDS.not_set_live
#: The watch's command line: the G432's alone, or another model's by its key.
USAGE = (f"usage: python3 -m gearbench.g432_eq watch "
         f"[{'|'.join(key for key in EQ_KEYED if key != G432_MODEL.key)}]")


class EqError(pipewire_client.StepError):
    """A step that failed, in words."""


class NotSetLive(EqError):
    """update's failure after its save, Words.not_set_live's sentence: the change is kept."""


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
class Record:
    """What switch_on and switch_off keep in Words.record_name between them: the headset's
    own volume from before the hold, each channel's, which marks a hold switch_off has to
    undo and is what it puts back when the equalizer's volume cannot be read, while the
    headset's route is at volumeBase (is_held; stale_record_dropped forgets it); whether the
    headset's own output was muted then, which the hold clears and which goes with that
    volume; and whether the equalizer was the default output when it went off with the
    headset's own output gone, for switch_on to make it the default again."""

    volumes: tuple[float, ...] | None = None
    muted: bool = False
    reselect: bool = False


@dataclass(frozen=True)
class Seen:
    """What the watch saw at pw-dump's last print: whether the headset's route was there,
    until when, after the print that showed it again, the watch holds it again, and whether
    it said that return's hold."""

    present: bool = False
    until: float = 0.0
    said: bool = False


@dataclass(frozen=True)
class State(pipewire_unit.UnitState):
    sink_id: int | None = None        # the chain's sink, made by the unit's process
    target: str | None = None         # the headset's output's node name, None while it has none
    linked: bool = False              # a link from the chain's output to that output
    presets: Presets = field(default_factory=Presets)


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


def render(gains: Gains, target: str, *, model: HeadsetModel = G432_MODEL) -> str:
    """The chain's conf for `gains`, its output aimed at `target`, the headset's output."""
    checked(gains)
    named = WORDS[model.key]
    if not target or pipewire_client.NOT_IN_CONF.search(target):
        raise EqError(named.not_named.format(wording.escaped(target)))
    nodes, links, first, last = graph_lines(gains)
    indent = " " * 20
    return TEMPLATE.format(nodes="\n".join(indent + line for line in nodes),
                           links="\n".join(indent + line for line in links),
                           first=first, last=last, channels=CHANNELS,
                           description=named.description, sink=named.sink,
                           output=named.output, target=target,
                           presets_name=presets_file_name(model), unit=named.unit,
                           short=model.short, client_context=pipewire_client.CLIENT_CONTEXT,
                           client_modules=pipewire_client.CLIENT_MODULES)


TEMPLATE = """\
# Gearbench: {description}. Gearbench writes this file from the active
# preset's gains in its {presets_name} at each switch-on, and a transient
# user unit, {unit}, runs it: pipewire -c <this file>.
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
                # Gearbench sets its volume at each switch-on, to the {short}'s
                # own: WirePlumber 0.5.13 neither restores a volume it saved
                # nor saves one for a node with this (state-stream.lua).
                state.restore-props = false
            }}
            playback.props = {{
                node.name          = "{output}"
                node.passive       = true
                target.object      = "{target}"
                # It plays on the {short} or nowhere, never on another output.
                node.dont-fallback = true
                node.linger        = true
            }}
        }}
    }}
]
"""


def conf_path(*, model: HeadsetModel) -> Path:
    """The model's conf in the runtime folder, its record's too: runtime_folder.private_runtime's,
    refused as EqError while XDG_RUNTIME_DIR is unset or relative (runtime_folder.runtime_home)
    or names a folder another account could swap for its own (runtime_folder.open_to_others)."""
    named = WORDS[model.key]
    return (runtime_folder.private_runtime(EqError, named.no_runtime, named.description)
            / named.conf_name)


def output_node(objects: Sequence[object], pid: int, *, model: HeadsetModel) -> dict | None:
    """The props of the headset's output in pw-dump's objects (pipewire_unit.g432_node): the
    one the chain of process `pid`, the unit's MainPID, plays to while its output is in
    PipeWire, the card switch_on rendered it for; else, as at switch_on, the one Gearbench
    Spatial plays to. None while there is none; `pid` 0 for no chain."""
    aimed = pipewire_unit.aimed_at(objects, pid, WORDS[model.key].output)
    return pipewire_unit.g432_node(objects, model=model, aimed=aimed)


def find_target(objects: Sequence[object], *, model: HeadsetModel = G432_MODEL,
                pid: int = 0) -> str | None:
    """The headset's output's node name in pw-dump's objects (output_node), None while there
    is none."""
    props = output_node(objects, pid, model=model)
    return None if props is None else props["node.name"]


def find_route(objects: Sequence[object], *, model: HeadsetModel = G432_MODEL,
               pid: int = 0) -> pipewire_unit.Route | None:
    """The headset's output's route (output_node) in pw-dump's objects
    (pipewire_unit.route_of), None while that output is not in PipeWire."""
    props = output_node(objects, pid, model=model)
    return None if props is None else pipewire_unit.route_of(objects, props)


def sink_volumes(objects: Sequence[object], pid: int, *, model: HeadsetModel = G432_MODEL
                 ) -> tuple[tuple[float, ...] | None, bool]:
    """The chain's sink's volume on each channel in pw-dump's objects and whether it is
    muted (pipewire_unit.props_volumes), from the sink of the client whose process is
    `pid` (pipewire_client.node_pids), as graph takes it; (None, False) while there is no
    such sink."""
    sink = WORDS[model.key].sink
    for _node, info, props, made_by in pipewire_client.node_pids(objects):
        if props.get("node.name") == sink and made_by == pid:
            return pipewire_unit.props_volumes(info)
    return None, False


def graph(objects: Sequence[object], pid: int, *, model: HeadsetModel = G432_MODEL) -> dict:
    """State's PipeWire fields from pw-dump's objects. The chain's sink and output are the
    nodes of those names made by the client whose process is `pid`, the unit's MainPID, by
    the pid PipeWire took from its socket, since any client can give a node those names;
    the headset's output is its card's own node, the one the chain aims at (output_node)."""
    named = WORDS[model.key]
    nodes = pipewire_unit.owned_nodes(objects, pid)
    found = output_node(objects, pid, model=model)
    return {"sink_id": nodes.get(named.sink),
            "target": None if found is None else found["node.name"],
            "linked": pipewire_unit.linked(objects, nodes.get(named.output),
                                           pipewire_unit.node_id(objects, found))}


def dumped_graph(run: pipewire_client.Run, pid: int, *, model: HeadsetModel) -> dict:
    """graph of a fresh pw-dump (pipewire_client.dumped)."""
    return pipewire_client.dumped(run, lambda objects: graph(objects, pid, model=model))


def straight_nodes(objects: Sequence[object], pid: int, *, model: HeadsetModel) -> set[int]:
    """The ids of the nodes that play straight into the headset's output (output_node) in
    pw-dump's objects (pipewire_unit.feeding), but for the chain's output, the node of that
    name process `pid` made."""
    into = pipewire_unit.node_id(objects, output_node(objects, pid, model=model))
    own = pipewire_unit.owned_nodes(objects, pid).get(WORDS[model.key].output)
    return pipewire_unit.feeding(objects, into) - {own}


def dumped_straight(run: pipewire_client.Run, pid: int, *, model: HeadsetModel) -> set[int]:
    """straight_nodes of a fresh pw-dump (pipewire_client.dumped)."""
    return pipewire_client.dumped(run, lambda objects: straight_nodes(objects, pid, model=model))


def read_state(run: pipewire_client.Run = subprocess.run, *,
               model: HeadsetModel = G432_MODEL) -> State:
    """What systemd and PipeWire say now, and the saved presets. Never raises."""
    return State(presets=load_presets(model=model), **pipewire_unit.read_fields(
        run, WORDS[model.key].unit, lambda objects, pid: graph(objects, pid, model=model)))


def summary(state: State, *, model: HeadsetModel = G432_MODEL) -> tuple[str, str]:
    """(kind, words) for `state`: the unit's own (pipewire_unit.unit_summary), else the
    first condition below that holds."""
    if (unit := pipewire_unit.unit_summary(state)) is not None:
        return unit
    named = WORDS[model.key]
    if state.sink_id is None:
        return "starting", "starting"
    if state.target is None:
        return "waiting", named.waiting
    if not state.linked:
        return "unlinked", named.unlinked
    return "ready", named.ready


def preset_words(presets: Presets, number: int) -> str:
    """A preset as the CLI names it: its number and its name (numbered)."""
    return numbered(number, presets.get(number).name)


def describe(state: State, number: int | None = None, *,
             model: HeadsetModel = G432_MODEL) -> list[tuple[str, str]]:
    """(label, value) rows for the CLI: the state, the output, the presets, and the gains
    of preset `number`, its row naming the active one if that is another; the active one's
    when None, or when `number` was read off presets another program has deleted it from since."""
    named = WORDS[model.key]
    presets = state.presets
    shown = number if number in presets.numbers else presets.active
    gains = presets.get(shown).gains
    active = "" if shown == presets.active else (
        f" (not the active one, {preset_words(presets, presets.active)})")
    return [
        ("equalizer", summary(state, model=model)[1]),
        ("output", f"{named.description} ({named.sink})"),
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


def record_path(*, model: HeadsetModel) -> Path:
    return conf_path(model=model).with_name(WORDS[model.key].record_name)


def load_record(*, model: HeadsetModel = G432_MODEL) -> Record:
    """The record, an empty one with no runtime folder, which switch_on refuses to run
    without, or no file that reads as a JSON object. `volumes` is none for a value that is
    not a list of 1 to 8 finite numbers of 0 or more, and `muted` and `reselect` each true
    only for true.
    EqError for a runtime folder conf_path refuses: a record there may be another account's,
    and put_back would give the headset its volume, saved."""
    if runtime_folder.runtime_home() is None:
        return Record()
    try:
        data = settings_files.read_json(record_path(model=model))
    except (OSError, settings_files.ConfigError):
        return Record()
    if not isinstance(data, dict):
        return Record()
    volumes = pipewire_unit.levels(data.get("volumes"))
    return Record(volumes if volumes is not None and len(volumes) <= 8 else None,
                  data.get("muted") is True, data.get("reselect") is True)


def save_record(record: Record, *, model: HeadsetModel = G432_MODEL) -> None:
    """The record, atomically and the user's alone (write_json's mkstemp), or for an empty
    one no file."""
    path = record_path(model=model)
    if record == Record():
        path.unlink(missing_ok=True)
    else:
        settings_files.write_json(path, {"volumes": record.volumes, "muted": record.muted,
                                         "reselect": record.reselect}, f".{path.name}-")


def _pw_cli(run: pipewire_client.Run, argv: list[str]) -> str | None:
    """Run a `pw-cli set-param` (pipewire_client.pw_cli): None when it went through, else
    why not, in words."""
    try:
        return pipewire_client.pw_cli(run, argv) or None
    except pipewire_client.StepError as err:
        return str(err)


def spread(volumes: Sequence[float], count: int) -> list[float]:
    """`volumes` on `count` channels: as they are for as many, else the loudest on each."""
    return list(volumes) if len(volumes) == count else [max(volumes)] * count


def carried_on(volumes: Sequence[float], base: float) -> list[float]:
    """The sink's volume for the headset's own `volumes`, as loud through a headset held at
    0 dB: ACP plays a route's volume v at v / volumeBase on the adapter's PCM (acp.c's
    set_volume, PipeWire 1.6.2, read 2026-10-17), so the sink takes v / volumeBase, at most
    CARRY_MAX. Not yet heard on the G432."""
    return [min(volume / base, CARRY_MAX) for volume in spread(volumes, CHANNELS)]


def carried_off(volumes: Sequence[float], route: pipewire_unit.Route) -> list[float]:
    """The headset's own volume for the sink's `volumes`, as loud with the chain gone as
    through it: each times the route's own volume on its channel, volumeBase while held,
    which the chain played under, so a hold that failed or a level set since is no jump
    either; carried_on the other way, with no cap."""
    return [volume * own for volume, own
            in zip(spread(volumes, len(route.volumes)), route.volumes, strict=True)]


def watch_argv(*, model: HeadsetModel) -> list[str]:
    """systemd-run's command line for the transient unit that watches for the headset's
    return: bound to its equalizer's unit, so it stops when that does (BindsTo= in
    systemd.unit(5); pipewire_unit.transient_argv), and given the package's folder on
    PYTHONPATH, where the window and the CLI found it. `watch` alone watches the G432, and
    `watch KEY` another model."""
    named = WORDS[model.key]
    return [*pipewire_unit.transient_argv(named.watch_unit, named.watching, named.unit,
                                          after=named.unit),
            f"--setenv=PYTHONPATH={Path(__file__).resolve().parent.parent}",
            "/usr/bin/python3", "-s", "-P", "-m", "gearbench.g432_eq", "watch",
            *(() if model is G432_MODEL else (model.key,))]


def held_and_watched(run: pipewire_client.Run, route: pipewire_unit.Route | None, *,
                     model: HeadsetModel) -> str | None:
    """hold, then, once the hold went through, the watch for the headset's return: the note
    to say, else None."""
    if (note := hold(run, route, model=model)) is not None:
        return note
    try:
        pipewire_client.run_program(run, watch_argv(model=model))
    except pipewire_client.StepError as err:
        return WORDS[model.key].no_watch.format(err)
    return None


def carry(run: pipewire_client.Run, route: pipewire_unit.Route, record: Record, *,
          model: HeadsetModel) -> str | None:
    """Give the sink, once the unit's process has made it, the headset's own volume as
    carried_on has it: the record's, a hold's whose put-back never ran and which still
    holds the headset (switch_on drops one that does not first), else the route's, read
    before the hold; and muted where the route is, which the hold then clears, or with the
    record's volume where the record keeps the mute that hold cleared. Words.not_carried's
    note when it could not, else None."""
    named = WORDS[model.key]
    own, muted = ((route.volumes, route.mute) if record.volumes is None
                  else (record.volumes, route.mute or record.muted))
    try:
        sink_id = _running_sink(run, model=model)
    except pipewire_client.StepError as err:
        return named.not_carried.format(err)
    if sink_id is None:
        return named.not_carried.format(named.no_sink)
    failed = _pw_cli(run, pipewire_unit.sink_argv(sink_id, carried_on(own, route.base), muted))
    return None if failed is None else named.not_carried.format(failed)


def hold(run: pipewire_client.Run, route: pipewire_unit.Route | None, *,
         model: HeadsetModel) -> str | None:
    """Set the headset's own output to 0 dB, and unmuted where it is muted, unsaved, so the
    sink's mute is the one that applies, keeping the volume and mute it had in the record
    first; a record already there is a hold whose put-back never ran, whose level and mute it
    keeps: the watch's, or one switch_on found still holding the headset
    (stale_record_dropped). Words.not_held's note when it could not be held, else None."""
    named = WORDS[model.key]
    if route is None:
        return named.not_held.format(named.no_route)
    record = load_record(model=model)
    if record.volumes is None:
        save_record(replace(record, volumes=route.volumes, muted=route.mute), model=model)
    failed = _pw_cli(run, pipewire_unit.route_argv(
        route, [route.base] * len(route.volumes), save=False,
        mute=False if route.mute else None))
    return None if failed is None else named.not_held.format(failed)


def put_back(run: pipewire_client.Run, route: pipewire_unit.Route | None,
             volumes: tuple[float, ...] | None, muted: bool, *,
             model: HeadsetModel) -> str | None:
    """With a hold in the record, give the headset's own output, saved, the sink's `volumes`
    times the route's own volume (carried_off), or with none read the volume the record
    keeps while the headset is still held (is_held), muted when the sink is (`muted`) or,
    with none read, when the record keeps a mute, and forget the hold. With no route there
    is none to set, as while the headset is unplugged,
    and WirePlumber gives it the level it saved, which the hold never was; with no volumes
    and a route off volumeBase, the headset has its own level again, and nothing is set
    either. Words.not_put_back's note when the write failed, not_put_back_muted's when it
    carried the mute too; the record then stays for the next switch_off."""
    record = load_record(model=model)
    if record.volumes is None:
        return None
    muted = muted or (volumes is None and record.muted)
    if route is not None:
        level = (carried_off(volumes, route) if volumes is not None
                 else record.volumes if is_held(route) else None)
        if level is not None and (failed := _pw_cli(run, pipewire_unit.route_argv(
                route, level, True, mute=True if muted else None))):
            named = WORDS[model.key]
            return (named.not_put_back_muted if muted else named.not_put_back).format(
                failed, pipewire_unit.gnome_percent(max(level)))
    save_record(replace(record, volumes=None, muted=False), model=model)
    return None


def is_held(route: pipewire_unit.Route) -> bool:
    """Whether each channel of `route` is at volumeBase, as a hold leaves it."""
    return all(abs(volume - route.base) <= HELD_TOLERANCE for volume in route.volumes)


def stale_record_dropped(route: pipewire_unit.Route | None, *, model: HeadsetModel) -> None:
    """Forget the record's level and mute when `route` shows the headset is not held: a
    PipeWire stop stops the unit with no switch_off, and WirePlumber's next start gives the
    headset the level and mute it saved, which the user may have changed since."""
    record = load_record(model=model)
    if record.volumes is not None and route is not None and not is_held(route):
        save_record(replace(record, volumes=None, muted=False), model=model)


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


def _pick(run: pipewire_client.Run, name: str) -> str | None:
    """Make `name` the default output and wait up to pipewire_client.SETTLE for WirePlumber to
    pick it, which moves the streams that follow the default with it: None once WirePlumber
    shows the pick, else why not, in words. A pick not seen is not known to have gone, so the
    music may still play where it did."""
    try:
        done = pipewire_client.run_program(run, default_argv(name))
    except pipewire_client.StepError as err:
        return str(err)
    # pw-metadata 1.6.2 says "set property:" for a write that went, and exits 0 saying
    # nothing for a metadata it does not find (measured 2026-10-17).
    if "set property:" not in done.stdout:
        return NO_METADATA
    deadline = time.monotonic() + pipewire_client.SETTLE
    while True:
        try:
            if pipewire_client.dumped(run, default_sink) == name:
                return None
        except pipewire_client.StepError as err:
            return str(err)
        if time.monotonic() >= deadline:
            return NOT_SHOWN
        time.sleep(pipewire_client.SETTLE_STEP)


def give_back_default(run: pipewire_client.Run, target: str | None, *,
                      model: HeadsetModel) -> str:
    """With the equalizer the default output, make the headset's own output, `target`, the
    default before the sink goes, so the streams that follow the default move to the headset
    first: WirePlumber 0.5.13 pauses the MPRIS players of the streams playing to a sink that
    goes (linking/mpris-pause.lua). With the headset's own output gone, keep that the
    equalizer was the default, for the next switch_on; with it there, the next switch_on
    finds it the default, unless the user picked another, which it leaves. The note to
    say."""
    named = WORDS[model.key]
    if target is None:
        save_record(replace(load_record(model=model), reselect=True), model=model)
        return named.moved_nowhere
    failed = _pick(run, target)
    return named.moved if failed is None else named.not_moved.format(failed)


def spatial_moved(run: pipewire_client.Run, *, model: HeadsetModel) -> str | None:
    """Wait up to pipewire_client.SETTLE, while Gearbench Spatial's chain plays to the headset
    aimed at another node than the equalizer's sink, for its unit's process to aim it there,
    which it does once the sink is in PipeWire, and while its unit or PipeWire cannot be read,
    since its chain may then still play to the headset: Words.spatial_not_moved's note when
    it did not move, Words.spatial_unread's with the cause when it was still not read, else
    None. A chain on another headset, or none, waits for nothing here; straight_gone then
    waits for whatever else plays straight to the headset's own output."""
    named = WORDS[model.key]
    deadline = time.monotonic() + pipewire_client.SETTLE
    while True:
        state = spatial.read_state(run)
        unread = state.systemd or state.pipewire
        if unread is None and (state.headset != model.key or state.target == named.sink):
            return None
        if time.monotonic() >= deadline:
            return (named.spatial_not_moved if unread is None
                    else named.spatial_unread.format(unread))
        time.sleep(pipewire_client.SETTLE_STEP)


def straight_gone(run: pipewire_client.Run, *, model: HeadsetModel) -> str | None:
    """Wait up to pipewire_client.SETTLE for every node but the chain's output that plays
    straight to the headset's own output (straight_nodes) to leave it, as a stream that
    follows the default does once the equalizer is the default, and while the unit or
    PipeWire cannot be read: Words.played_straight's note when one still plays there, as a
    stream with an output of its own picked in its app or moved there by hand does, which a
    hold would make louder; Words.straight_unread's with the cause when it was still not
    read; else None."""
    named = WORDS[model.key]
    deadline = time.monotonic() + pipewire_client.SETTLE
    while True:
        try:
            pid = pipewire_client.main_pid(pipewire_unit.unit_of(run, named.unit))
            if not dumped_straight(run, pid, model=model):
                return None
            note = named.played_straight
        except pipewire_client.StepError as err:
            note = named.straight_unread.format(err)
        if time.monotonic() >= deadline:
            return note
        time.sleep(pipewire_client.SETTLE_STEP)


def switch_on(run: pipewire_client.Run = subprocess.run, *,
              model: HeadsetModel = G432_MODEL) -> list[str]:
    """Write the conf for the active preset's gains, aimed at the headset's output, start the
    unit, give its sink the headset's volume and mute, make it the default output if the
    headset's own output is the default or it was the default when it went off with the
    headset's own output gone, wait for Gearbench Spatial on the headset to move to it and for
    anything else that plays straight to the headset's own output to leave it, and then hold
    the headset at 0 dB and unmuted, so a stream that moves to the equalizer leaves the
    headset before the headset is at 0 dB, and is never louder on the way, and start the
    watch for the headset's return; nothing while it runs or starts. A sink that did not take
    the volume and mute, or a pick that failed or did not show (_pick) while the headset's
    own output is the default, leaves the headset unheld and unwatched, with no wait; a
    Gearbench Spatial that did not move or could not be read, or sound still straight on the
    headset's own output, or a look for it that could not be read (straight_gone), leaves it
    unheld and unwatched too.
    EqError while the headset's output is not in PipeWire, Words.no_output while its card
    is, for a runtime folder conf_path refuses, and Words.stopped when the unit stopped as it
    started, looked for once it has settled (pipewire_unit.settle_start), before any pick or
    hold. The notes to say, in words."""
    named = WORDS[model.key]
    if pipewire_unit.running(run, named.unit):
        return []
    objects = pipewire_client.dump_objects(run)
    target = pipewire_client.shaped(lambda found: find_target(found, model=model), objects)
    default = pipewire_client.shaped(default_sink, objects)
    if target is None:
        raise EqError(named.no_output if pipewire_unit.g432_cards(objects, model=model)
                      else named.no_headset)
    conf = conf_path(model=model)
    spatial.replace_conf(conf, render(load_gains(model=model), target, model=model))
    pipewire_client.run_program(
        run, pipewire_unit.pipewire_argv(named.unit, named.description, conf))
    route = pipewire_client.shaped(lambda found: find_route(found, model=model), objects)
    stale_record_dropped(route, model=model)
    record = load_record(model=model)
    carried = None if route is None else carry(run, route, record, model=model)
    pipewire_unit.settle_start(run, named.unit, lambda: read_state(run=run, model=model),
                               lambda state: summary(state, model=model),
                               EqError(named.stopped))
    notes = [] if carried is None else [carried]
    failed = None
    if record.reselect or default == target:
        failed = _pick(run, named.sink)
        if failed is None:
            notes.append(named.reselected if record.reselect else named.selected)
        else:
            notes.append((named.not_selected if default == target
                          else named.not_reselected).format(failed))
        save_record(replace(record, reselect=False), model=model)
    # The music, spatial sound or a stream with an output of its own may still be on the
    # headset's own output, which a hold would make louder.
    if carried is None and not (failed is not None and default == target) and (
            note := (spatial_moved(run, model=model) or straight_gone(run, model=model)
                     or held_and_watched(run, route, model=model))):
        notes.append(note)
    return notes


def switch_off(run: pipewire_client.Run = subprocess.run, *,
               model: HeadsetModel = G432_MODEL) -> list[str]:
    """Stop the watch, give the headset's own output the equalizer's volume and mute in place
    of the hold, make the headset's own output the default if the equalizer was it, then stop
    the unit, which removes the sink; a unit not loaded is off already. A put-back that fails,
    or a pick that fails or does not show (_pick), is a note; a failed stop of the watch, or
    read of systemd or PipeWire after it, puts nothing back and is a note only with a hold in
    the record. The unit stops all the same, after a record's save that raises OSError too,
    which is then raised. EqError,
    with nothing written or stopped, for a runtime folder conf_path refuses (load_record): a
    hold the record there cannot vouch for then stays under the equalizer's volume, never
    louder. The notes to say, in words."""
    named = WORDS[model.key]
    held = load_record(model=model).volumes
    try:
        # First: a watch still running holds the headset again at the put-back's own
        # print, since the record keeps the hold until put_back drops it.
        pipewire_unit.stop(run, named.watch_unit)
        pid = pipewire_client.main_pid(pipewire_unit.unit_of(run, named.unit))
        route, target, default, (volumes, muted) = pipewire_client.dumped(run, lambda found: (
            find_route(found, model=model, pid=pid),
            find_target(found, model=model, pid=pid),
            default_sink(found), sink_volumes(found, pid, model=model)))
    except pipewire_client.StepError as err:
        notes = [] if held is None else [
            named.not_put_back.format(err, pipewire_unit.gnome_percent(max(held)))]
    else:
        notes = [note] if (note := put_back(run, route, volumes, muted, model=model)) else []
        if default == named.sink:
            notes.append(give_back_default(run, target, model=model))
    finally:
        # A record that could not be saved, an OSError, stops it too: else no off would.
        pipewire_unit.stop(run, named.unit)
    return notes


def _running_sink(run: pipewire_client.Run, *, model: HeadsetModel) -> int | None:
    """The running chain's sink, waiting up to pipewire_client.SETTLE for the sink of a unit
    that is starting, whose conf may hold the gains from before a save. EqError,
    Words.no_sink's words, for a unit that runs or starts with no sink by then; None with no
    unit running."""
    named = WORDS[model.key]
    deadline = time.monotonic() + pipewire_client.SETTLE
    while True:
        unit = pipewire_unit.unit_of(run, named.unit)
        pid = pipewire_client.main_pid(unit)
        sink_id = dumped_graph(run, pid, model=model)["sink_id"] if pid else None
        if sink_id is not None or unit.get("ActiveState") not in pipewire_unit.RUNS_OR_STARTS:
            return sink_id
        if time.monotonic() >= deadline:
            raise EqError(named.no_sink)
        time.sleep(pipewire_client.SETTLE_STEP)


def update(change: Callable[[Presets], Presets], run: pipewire_client.Run = subprocess.run, *,
           model: HeadsetModel = G432_MODEL) -> bool:
    """edit, then set the active preset's gains on the running chain, which a set before
    that failed may have left on others. False when it does not run, so they apply at its
    next start; NotSetLive when the unit or the sink could not be read, the sink of a unit
    that runs or starts did not show within pipewire_client.SETTLE, or the set failed."""
    gains = edit(change, model=model).current.gains
    not_set_live = WORDS[model.key].not_set_live
    try:
        sink_id = _running_sink(run, model=model)
    except pipewire_client.StepError as err:
        raise NotSetLive(not_set_live.format(err)) from None
    if sink_id is None:
        return False
    failed = _pw_cli(run, ["pw-cli", "set-param", str(sink_id), "Props", props(gains)])
    if failed is not None:
        raise NotSetLive(not_set_live.format(failed))
    return True


def set_gains(gains: Gains, number: int, name: str,
              run: pipewire_client.Run = subprocess.run, *,
              model: HeadsetModel = G432_MODEL) -> bool:
    """`gains` to the preset `name`, which `number` held when a page read the file, where
    the file holds it now (eq_presets.number_named), through update: another program, or a
    pick still running, can make another preset active after that read."""
    return update(lambda presets: presets.with_gains(
        eq_presets.number_named(presets.presets, number, name), gains), run, model=model)


def returned(seen: Seen, route: pipewire_unit.Route | None, now: float) -> tuple[Seen, bool]:
    """The watch's next Seen for the headset's `route` at `now`, and whether to hold it at
    0 dB and unmuted now: while RETURN_WINDOW runs from the print that showed the route
    again, each time a channel is off volumeBase or the route is muted, as WirePlumber gives
    back a mute it saved at any level, volumeBase included. The first print counts as a
    return, since the headset can come back between the switch-on's hold and the watch's
    start."""
    if route is None:
        return Seen(), False
    if not seen.present:
        seen = Seen(True, now + RETURN_WINDOW)
    return seen, (route.mute or not is_held(route)) and now < seen.until


def watch(lines: Iterable[str], run: pipewire_client.Run = subprocess.run,
          clock: Callable[[], float] = time.monotonic, *,
          model: HeadsetModel = G432_MODEL) -> None:
    """Follow pw-dump --monitor's `lines` and, while a hold is in the record, hold the
    headset at 0 dB again each time `returned` says to: after a replug WirePlumber gives it
    the level and mute it saved, which the hold never was. Says on stderr, which the unit's journal
    keeps, a unit not read at the start, the first hold of each return, however many follow
    it, each hold that failed, and each print not read. The headset is output_node's for
    the unit's MainPID, read at the start, 0 when the unit has none or was not read. A
    runtime folder conf_path refuses ends it, as load_record raises EqError there."""
    named = WORDS[model.key]
    objects: dict[int, dict] = {}
    seen = Seen()
    try:
        pid = pipewire_client.main_pid(pipewire_unit.unit_of(run, named.unit))
    except pipewire_client.StepError as err:
        print(named.watch_no_chain.format(err), file=sys.stderr, flush=True)
        pid = 0
    for found in pipewire_client.printed(lines):
        if found is None:
            print(named.watch_unread.format(pipewire_client.NOT_A_PRINT), file=sys.stderr,
                  flush=True)
            continue
        pipewire_client.merge(objects, found)
        try:
            route = pipewire_client.shaped(
                lambda known: find_route(known, model=model, pid=pid), list(objects.values()))
        except pipewire_client.StepError as err:
            print(named.watch_unread.format(err), file=sys.stderr, flush=True)
            continue
        seen, due = returned(seen, route, clock())
        if due and load_record(model=model).volumes is not None:
            note = hold(run, route, model=model)
            if note is not None:
                print(named.watch_back.format(note), file=sys.stderr, flush=True)
            elif not seen.said:
                print(named.watch_held, file=sys.stderr, flush=True)
                seen = replace(seen, said=True)


def main(argv: Sequence[str] | None = None,
         spawn: Callable[..., subprocess.Popen] = subprocess.Popen,
         run: pipewire_client.Run = subprocess.run) -> int:
    """`python3 -m gearbench.g432_eq watch [KEY]`, the watch unit's process: watch the G432,
    or the model of EQ_MODELS KEY names, until pw-dump --monitor ends, as at PipeWire's
    stop, which ends the unit as failed."""
    match sys.argv[1:] if argv is None else list(argv):
        case ["watch"]:
            model = G432_MODEL
        case ["watch", key] if key in EQ_KEYED:
            model = EQ_KEYED[key]
        case _:
            print(USAGE, file=sys.stderr)
            return 2
    try:
        with spawn(pipewire_client.MONITOR, stdout=subprocess.PIPE, encoding="utf-8",
                   errors="replace") as monitor:
            watch(cast("IO[str]", monitor.stdout), run=run, model=model)
    except OSError as err:
        why = str(pipewire_client.not_run("pw-dump", err))
    else:
        why = wording.ended("pw-dump", monitor.returncode)
    print(WORDS[model.key].watch_ended.format(why), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
