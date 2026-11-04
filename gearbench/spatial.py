"""Spatial sound: its configuration, its user service, and what PipeWire says of it.

Gearbench Spatial is one 7.1 sink, rendered for headphones on the headset it plays to: the
Astro A50's or the Astro A50 X's Game output, the own output of a headset of the Logitech
G432's family (pipewire_headsets.G432_FAMILY), or the first output of an A50 Gen 5, a PRO X
2, a G522 or a G325; for a headset whose equalizer gearbench.g432_eq runs, that equalizer's
sink in place of its output while the sink its unit's process made is in PipeWire; and
Gearbench Game in place of either while gearbench-outputs.service runs it
(spatial_unit.target). gearbench-spatial.service's process (gearbench.spatial_unit) follows
PipeWire and runs the filter chain, rendered from gearbench-spatial.conf.in and
spatial.json's head, room and headset, while a headset is there, and renders and starts it
again when the headset or the node it plays to changes; on and off is the unit's enable
link, stored nowhere else. A `Target` is where the chain plays: a
pipewire_headsets.Headset, which holds how a headset's card and output are found and the
words that name them, and a node. Standard library only; the daemon never imports it, and
of its functions `gearbench-ctl daemon` calls `reenable_old_switch_on` alone, once before
the daemon starts. systemd and PipeWire are run and read through gearbench.pipewire_client,
each program through a `run` argument.
"""

from __future__ import annotations

import re
import struct
import subprocess
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from . import pipewire_client, settings_files, wording
from .pipewire_headsets import (
    FIRST_CONNECTED,
    HEADSET_KEYED,
    HEADSETS,
    JOINED_HINT,
    OUTSIDE_RULE,
    UNTIL_RESTART,
    Headset,
    card_of,
    card_set,
    headset_of,
    pick,
    present,
    stranded,
)

UNIT = "gearbench-spatial.service"
#: The program's name, not a headset's, since one output plays to each headset (the owner's
#: decision of 2026-10-18), and the unit's: so one launch option reaches every headset.
SINK = "gearbench-spatial"
OUTPUT = "gearbench-spatial.output"
DESCRIPTION = "Gearbench Spatial"
ENVIRONMENT = f"PULSE_SINK={SINK}"
LAUNCH_OPTION = f"{ENVIRONMENT} %command%"
#: The A50's spatial output's name before Gearbench Spatial, retired with no alias: a game
#: whose launch option still names it plays on the default output (stale_hint). Its stream
#: carries the name as target.object, as pipewire-pulse gave a PULSE_SINK client's stream in
#: a throwaway PipeWire on 2026-10-18.
RETIRED_SINK = "gearbench-a50-spatial"


HEAD_NAMES = {"ku100": "KU100", "kemar": "KEMAR"}
HEAD_SUBJECTS = {"ku100": "D1", "kemar": "D2"}
HEADS = tuple(HEAD_NAMES)
ROOM_GAINS = {"competitive": 0.0, "immersive": 0.1}
ROOMS = tuple(ROOM_GAINS)
ROOM_CONTROLS = ("outL:Gain 2", "outR:Gain 2")
#: 0.5 in every conf. A chain that has not played since its start shows every control as 0
#: while holding its real values (PipeWire 1.6.2), so a 0 here means the room is not known.
LEVEL_CONTROL = "outL:Gain 1"
TEMPLATE = "gearbench-spatial.conf.in"
ROOM_WAV = "room-150ms.wav"
HRIR_FILES = ("azi_30,0_ele_0,0.wav", "azi_330,0_ele_0,0.wav", "azi_0,0_ele_0,0.wav",
              "azi_90,0_ele_0,0.wav", "azi_270,0_ele_0,0.wav", "azi_150,0_ele_0,0.wav",
              "azi_210,0_ele_0,0.wav")
#: Hard-coded in the spatial unit; the tests hold the unit to it.
CONF_NAME = "gearbench-spatial.conf"
#: The props the template gives the chain's sink: the headset it plays to, by key, and the
#: node its output is aimed at, which read_state reads back.
TARGET_PROPS = ("gearbench.headset", "gearbench.target")
PACKAGED = Path("/usr/share/gearbench/spatial")
BLOCK_TIMEOUT = 30.0   # s, for a start or restart that is waited for
#: set_room's failure when a starting chain's sink has not come within
#: pipewire_client.SETTLE.
STILL_STARTING = (f"{DESCRIPTION} was still starting after {pipewire_client.SETTLE:g} s; "
                  "it keeps the room it started with until its next start")
#: render's refusal of a node name the chain's conf cannot hold, given at {} escaped.
UNNAMED_OUTPUT = "the output '{}' cannot be named in the chain's conf"

#: The properties `systemctl show` gives of Spatial's unit and of Outputs' (unit_fields).
UNIT_PROPERTIES = ("--property=Id,LoadState,ActiveState,SubState,UnitFileState,Result,MainPID,"
                   "StatusText")
SHOW = pipewire_client.systemctl(UNIT_PROPERTIES, "show", UNIT)
ENABLED_STATES = ("enabled", "enabled-runtime")
#: The link a switch-on of Gearbench 1.1.0 to 1.2.1 made below XDG_CONFIG_HOME, while the
#: unit's [Install] named default.target: a user manager that lingering or another session
#: keeps up across a logout reaches that target once, so the unit did not start again with
#: PipeWire.
OLD_WANTS = Path("systemd", "user", "default.target.wants", UNIT)
#: What to do about a missing unit file, in unit_kind and under the CLI's systemctl error.
INSTALL_HINT = "run `./install.sh` again, or reinstall the Gearbench package"
#: What to do about a file of the chain's data that is missing or not the WAV it takes: the
#: checkout tracks these files, and the package installs them under PACKAGED.
DATA_HINT = "restore the checkout's spatial folder, or reinstall the Gearbench package"


@dataclass(frozen=True)
class Choices:
    head: str = "ku100"
    room: str = "competitive"
    #: The headset picked to play to, a key of HEADSET_KEYED; None for none picked, which
    #: plays to the one connected first (pick).
    headset: str | None = None


@dataclass(frozen=True)
class Target:
    """Where the chain plays: the headset, the node its output is aimed at, and the words
    the conf's comments name that node by."""

    headset: Headset
    node: str
    words: str


@dataclass(frozen=True)
class Stream:
    name: str               # first of application.name, node.description, node.name, "node <id>"
    channels: int | None    # its Format param's channels, else audio.channels, else None


@dataclass(frozen=True)
class InstalledUnitState(pipewire_client.UnitState):
    """What `systemctl --user show` and pw-dump say of an installed user unit of Gearbench's
    (unit_fields): UnitState's fields and these, before the unit's own, which State and
    outputs_state.State add."""

    installed: bool = True            # LoadState != "not-found"
    enabled: bool = False             # UnitFileState in ENABLED_STATES
    result: str = "success"           # Result
    #: The unit's StatusText, None for none: Spatial's process's word on why no chain runs
    #: for a headset that is there (spatial_unit.status), and Outputs' on where the outputs
    #: play while the headset is away.
    status: str | None = None


@dataclass(frozen=True)
class State(InstalledUnitState):
    sink_id: int | None = None
    output_id: int | None = None
    #: The headset the chain's sink says it plays to (TARGET_PROPS), None without a chain.
    headset: str | None = None
    #: The node the chain's sink says its output is aimed at, None without a chain.
    target: str | None = None
    #: The headsets whose output is in PipeWire, by key, in the order their cards came
    #: (present).
    present: tuple[str, ...] = ()
    #: A Link from OUTPUT into an Audio/Sink, its state not "error" or "unlinked".
    linked: bool = False
    link_error: str | None = None
    #: The node that link plays to: its node.description, else its node.name, as text.
    through: str | None = None
    running: bool = False             # the SINK node's info.state == "running"
    #: "outL:Gain 2" in SINK's Props params (else OUTPUT's); None while not known.
    room_gain: float | None = None
    #: The device.profile-set of the card of the headset the chain plays to; None without
    #: a chain or the card.
    profile_set: str | None = None
    #: The headsets with a named output whose card is in PipeWire without it, by key, each
    #: with its card's profile set (stranded): an A50 X under default.conf has no Game node.
    stranded: tuple[tuple[str, str], ...] = ()
    streams: tuple[Stream, ...] = ()  # nodes linked into SINK, deduplicated, by id
    #: Streams that ask for RETIRED_SINK (its target.object), by name, each once.
    stale: tuple[str, ...] = ()
    choices: Choices = Choices()


def choices_path() -> Path:
    return settings_files.config_dir() / "spatial.json"


def load_choices(path: Path | None = None) -> Choices:
    """The saved head, room and headset, each field falling back to its default on its own:
    a file from before the headset has none, which is none picked."""
    target = choices_path() if path is None else path
    try:
        data = settings_files.read_json(target)
    except (OSError, settings_files.ConfigError):
        return Choices()
    if not isinstance(data, dict):
        return Choices()
    default = Choices()
    head = data.get("head")
    room = data.get("room")
    headset = data.get("headset")
    return Choices(head=head if head in HEADS else default.head,
                   room=room if room in ROOMS else default.room,
                   headset=headset if isinstance(headset, str) and headset in HEADSET_KEYED
                   else None)


def _check(choices: Choices) -> None:
    if choices.head not in HEADS:
        raise ValueError(f"unknown head {choices.head!r}: one of {', '.join(HEADS)}")
    if choices.room not in ROOMS:
        raise ValueError(f"unknown room {choices.room!r}: one of {', '.join(ROOMS)}")
    if choices.headset is not None and choices.headset not in HEADSET_KEYED:
        raise ValueError(f"unknown headset {choices.headset!r}: one of "
                         f"{', '.join(HEADSET_KEYED)}")


def save_choices(choices: Choices, path: Path | None = None) -> None:
    """`choices` to spatial.json, or to `path`, atomically (settings_files.write_json), once _check
    passes them. A folder behind a link to nothing is an OSError naming the link, as any
    save that fails: a front end takes a StepError from set_head or set_headset for a
    restart that failed after the save."""
    _check(choices)
    target = choices_path() if path is None else path
    # make_folder refuses this link too, but as a ConfigError, where a failed save is an OSError.
    if (place := settings_files.link_to_nothing(target.parent)) is not None:
        raise OSError(settings_files.link_gone(place))
    settings_files.make_folder(target)
    # No headset key for none picked, so a file with none reads as before it had one.
    picked = {} if choices.headset is None else {"headset": choices.headset}
    settings_files.write_json(target, {"head": choices.head, "room": choices.room, **picked},
                      ".spatial-")


def data_dir() -> Path:
    """The checkout's spatial/ when running from one, else the package's."""
    here = Path(__file__).resolve().parents[1] / "spatial"
    return here if (here / TEMPLATE).is_file() else PACKAGED


def file_bytes(path: Path) -> bytes:
    """`path`'s bytes. An OSError from the read after the open names no file, so each is
    given `path`, which the open's names already: spatial_unit's Chain.aim takes one that
    names none for the conf's write."""
    try:
        return path.read_bytes()
    except OSError as err:
        err.filename = str(path)
        raise


def wav_info(path: Path) -> tuple[int, int]:
    """(channels, rate) from the RIFF chunks."""
    data = file_bytes(path)
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise pipewire_client.StepError(f"{path} is not a WAV file: {DATA_HINT}")
    fmt = None
    has_data = False
    pos = 12
    while pos + 8 <= len(data):
        chunk, length = struct.unpack_from("<4sI", data, pos)
        body = data[pos + 8:pos + 8 + length]
        if chunk == b"fmt " and len(body) >= 16:
            fmt = struct.unpack_from("<HHI", body)
        elif chunk == b"data":
            has_data = True
        pos += 8 + length + (length & 1)   # chunks are word-aligned
    if fmt is None or not has_data:
        raise pipewire_client.StepError(f"{path} has no fmt or data chunk: {DATA_HINT}")
    return fmt[1], fmt[2]


def render(choices: Choices, target: Target, data: Path | None = None) -> str:
    """The chain's conf for `choices`, from `data`/TEMPLATE (data_dir() by default), aimed
    at `target`."""
    # A headset with no game plays to a node name read from pw-dump (output_of).
    if not pipewire_client.conf_holds(target.node):
        raise pipewire_client.StepError(UNNAMED_OUTPUT.format(wording.escaped(target.node)))
    # PipeWire resolves a relative filename against its own working directory.
    base = (data_dir() if data is None else data).absolute()
    template = file_bytes(base / TEMPLATE).decode("utf-8")
    hrir = base / "sadie-ii" / HEAD_SUBJECTS[choices.head]
    room = base / ROOM_WAV
    for path in (hrir, room):
        if pipewire_client.NOT_IN_CONF.search(str(path)):
            raise pipewire_client.StepError(
                f"{wording.escaped(path)}: a path with \", \\ or a control character such as "
                "a tab cannot go in the chain's conf; move the checkout to a path without them "
                "and run `./install.sh` again")
    for path in (*(hrir / name for name in HRIR_FILES), room):
        if not path.is_file():
            raise pipewire_client.StepError(f"{path} is missing: {DATA_HINT}")
        channels, rate = wav_info(path)
        if (channels, rate) != (2, 48000):
            raise pipewire_client.StepError(f"{path} is {wording.counted(channels, 'channel')} "
                                            f"at {rate} Hz, not 2 channels at 48000 Hz: "
                                            f"{DATA_HINT}")
    fills = {"@HRIR@": str(hrir), "@ROOM@": str(room),
             "@ROOM_GAIN@": f"{ROOM_GAINS[choices.room]:.1f}", "@TARGET@": target.node,
             "@HEADSET@": target.headset.key, "@PLAYS_ON@": target.words,
             "@CLIENT_CONTEXT@": pipewire_client.CLIENT_CONTEXT,
             "@CLIENT_MODULES@": pipewire_client.CLIENT_MODULES}
    # One pass over the template alone: a fill is never read again, so a folder named like
    # a placeholder stays in its path.
    for left in re.findall(r"@[A-Z_]+@", template):
        if left not in fills:
            raise pipewire_client.StepError(f"{base / TEMPLATE} has {left}, which nothing fills")
    return re.sub(r"@[A-Z_]+@", lambda match: fills[match.group()], template)


def replace_conf(path: Path, text: str) -> None:
    """`text` as the PipeWire conf at `path`, replaced atomically, mode 0600."""
    # replace_file's mkstemp makes it 0600, and the replace keeps PipeWire from reading a
    # half-written conf. The conf is in the runtime folder, so its folder is not synced.
    settings_files.replace_file(path, text.encode("utf-8"), f".{path.name}-", "")


def read_state(run: pipewire_client.Run = subprocess.run) -> State:
    """What systemd and PipeWire say now (pipewire_client.unit_and_graph), and the saved
    choices. Never raises."""
    return State(choices=load_choices(), **pipewire_client.unit_and_graph(
        run, SHOW, unit_fields, lambda objects, unit: graph_state(objects, _chain_owner(unit))))


def unit_fields(unit: dict[str, str]) -> dict:
    """An installed user unit's InstalledUnitState fields from `systemctl show`'s properties,
    which this State and outputs_state.State hold."""
    return {"installed": unit.get("LoadState") != "not-found",
            "enabled": unit.get("UnitFileState") in ENABLED_STATES,
            "active": unit.get("ActiveState") or "inactive",
            "result": unit.get("Result") or "success",
            "status": unit.get("StatusText") or None}


def _props_value(info: dict, name: str) -> float | None:
    """`name` from a node's Props params, a flat list of name, value."""
    for entry in (info.get("params") or {}).get("Props") or []:
        values = entry.get("params") if isinstance(entry, dict) else None
        if not isinstance(values, list):
            continue
        for key, value in zip(values[::2], values[1::2], strict=False):
            if key == name and wording.number(value):
                return float(value)
    return None


def _room_gain(info: dict) -> float | None:
    """The room's gain in a node's Props, None while its values are not known."""
    if not _props_value(info, LEVEL_CONTROL):
        return None
    return _props_value(info, ROOM_CONTROLS[0])


def _channels(info: dict) -> int | None:
    """A stream's channel count, a whole number alone: true or false is none (the owner's
    decision of 2026-10-07)."""
    for fmt in (info.get("params") or {}).get("Format") or []:
        if isinstance(fmt, dict) and wording.whole(fmt.get("channels")):
            return fmt["channels"]
    channels = (info.get("props") or {}).get("audio.channels")
    return channels if wording.whole(channels) else None


#: The longest name of a client's node kept whole. The client sets it, and GTK takes seconds
#: to wrap a few thousand characters with no space, each time the Headset tab shows a new one.
NAME_MAX = 100


def shown_name(name: object) -> str:
    """A node's name as text the window shows unescaped: a client names its own nodes, and
    a newline or a bidi override in one would break or turn the row. Cut at NAME_MAX."""
    text = wording.printable(str(name))
    return text[:NAME_MAX - 1] + "…" if len(text) > NAME_MAX else text


def _stream_name(props: dict, node_id: int) -> str:
    return shown_name(props.get("application.name") or props.get("node.description")
                  or props.get("node.name") or f"node {node_id}")


def _stream(info: dict, node_id: int) -> Stream:
    return Stream(_stream_name(info.get("props") or {}, node_id), _channels(info))


def _chain_owner(unit: dict[str, str] | None) -> int | None:
    """The pid whose child's nodes graph_state takes for the chain's: the unit's main
    process, its watcher (spatial_unit), whose child `pipewire -c` makes them; 0, matching
    nothing, while it has none or systemctl did not answer. None, matching by name and
    class, while the unit is neither enabled nor running: a chain run by hand, which summary
    calls manual."""
    if unit is None:
        return 0
    pid = pipewire_client.main_pid(unit)
    if pid:
        return pid
    return 0 if unit.get("UnitFileState") in ENABLED_STATES else None


def _graph(run: pipewire_client.Run, owner: int | None) -> dict:
    """graph_state of a fresh pw-dump (pipewire_client.dumped)."""
    return pipewire_client.dumped(run, lambda objects: graph_state(objects, owner))


def graph_state(objects: Sequence[object], owner: int | None = None) -> dict:
    """State's pw-dump fields from pw-dump's objects.

    The chain's sink and output are the Audio/Sink and stream of their names made by a
    child of the process `owner` (_chain_owner), the unit's watcher, by the pid PipeWire
    took from the owning client's socket (pipewire_client.node_pids) and that process's
    parent (pipewire_client.parent_of), since any client can name a node gearbench-spatial;
    with `owner` None the name and class decide, the first node of a client PipeWire did
    not restrict (pipewire_client.trusted_clients) before any other. The headset and the
    node it plays to are the ones its sink's props name (TARGET_PROPS), and the profile set
    is that headset's card's.
    """
    found_nodes = pipewire_client.of_type(objects, "Node")
    nodes = {node: info for node, info, _props in found_nodes}
    pids = {node: pid for node, _info, _props, pid in pipewire_client.node_pids(objects)}
    links = [info for obj in objects if isinstance(obj, dict)
             and obj.get("type") == "PipeWire:Interface:Link"
             and isinstance(info := obj.get("info"), dict)]

    trusted = pipewire_client.trusted_clients(objects) if owner is None else set()

    def first(name: str, media_class: str) -> int | None:
        # The name and the class first: pipewire_client.parent_of reads /proc for the nodes
        # that have both.
        named = [(node, props) for node, _info, props in found_nodes
                 if props.get("node.name") == name and props.get("media.class") == media_class]
        if owner is None:
            # A sandboxed app's node of the name, listed first, would take the room set on
            # the user's own chain; min keeps pw-dump's order among equals.
            return min(named, key=lambda each: pipewire_client.node_client(each[1]) not in trusted,
                       default=(None, {}))[0]
        return next((node for node, _props in named
                     if owner > 0 and node in pids
                     and pipewire_client.parent_of(pids[node]) == owner), None)

    sink_id = first(SINK, "Audio/Sink")
    output_id = first(OUTPUT, "Stream/Output/Audio")
    # A link into a capture, as a per-stream level meter's from the output itself, is not
    # where the chain plays, and listed first it would name the meter.
    sinks = {node for node, _info, props in found_nodes
             if props.get("media.class") == "Audio/Sink"}
    from_output = [link for link in links if output_id is not None
                   and link.get("output-node-id") == output_id
                   and link.get("input-node-id") in sinks]
    live = [link for link in from_output if link.get("state") not in ("error", "unlinked")]
    link_error = None
    if not live:
        link_error = next((str(link.get("error") or "error") for link in from_output
                           if link.get("state") == "error"), None)
    # No sink or output finds no node: dict.get takes a None key, and typeshed's does not.
    sink = nodes.get(sink_id, {})  # type: ignore[arg-type]
    played_id = live[0].get("input-node-id") if live else None
    played = nodes[played_id].get("props") or {} if wording.whole(played_id) else {}
    through = played.get("node.description") or played.get("node.name")
    sink_props = sink.get("props") or {}
    headset = headset_of(sink_props.get(TARGET_PROPS[0]))
    target = sink_props.get(TARGET_PROPS[1])
    card = None if headset is None else card_of(headset, objects)
    room_gain = _room_gain(sink)
    if room_gain is None:
        room_gain = _room_gain(nodes.get(output_id, {}))  # type: ignore[arg-type]
    inputs = sorted(nodes.keys()
                    & {link.get("output-node-id") for link in links
                       if sink_id is not None and link.get("input-node-id") == sink_id})
    return {
        "sink_id": sink_id,
        "output_id": output_id,
        "headset": None if headset is None else headset.key,
        "target": target if isinstance(target, str) else None,
        "present": tuple(found.key for found, _output in present(objects)),
        "linked": bool(live),
        "link_error": link_error,
        "through": None if through is None else shown_name(through),
        "running": sink.get("state") == "running",
        "room_gain": room_gain,
        "profile_set": None if card is None else card_set(card[0]),
        "stranded": stranded(objects),
        "streams": tuple(_stream(nodes[node_id], node_id) for node_id in inputs),
        "stale": tuple(dict.fromkeys(
            _stream_name(props, node) for node, _info, props in found_nodes
            if props.get("target.object") == RETIRED_SINK)),
    }


def joined_headset(state: State) -> tuple[Headset, str] | None:
    """The headset whose card is on another profile set than the one that gives the chain
    its output (Headset.profile_set), with that set: the one the chain plays to, where its
    card is on another, else, while no headset is there to play to, the first in HEADSETS'
    order of those plugged in with no output for the chain (State.stranded) on another set
    than the one that gives it that output; None for none.
    A card on its own set lacks the output only under a profile picked without it, as the
    A50's with Chat alone or Off, which restarting WirePlumber does not change (the owner's
    decision of 2026-10-26)."""
    on = headset_of(state.headset)
    if on is not None and state.profile_set is not None and state.profile_set != on.profile_set:
        return on, state.profile_set
    if state.present:
        return None
    return next(((HEADSET_KEYED[key], profile_set) for key, profile_set in state.stranded
                 if profile_set != HEADSET_KEYED[key].profile_set), None)


def restart_needed(state: State, headset: Headset) -> bool:
    """Whether the outputs rows ask for WirePlumber's restart for `headset`, joined_headset's:
    for one with a named output (Headset.game), whose other set joins Game and Chat or leaves
    Game out until then; for another, while that set left it without the output the chain
    plays to. One that kept it, as the G432's analog output under default.conf, plays the
    same until then, so the restart is not asked for."""
    return headset.game is not None or headset.key not in state.present


def reset_failed(run: pipewire_client.Run, unit: str) -> None:
    """Zero `unit`'s start and restart counters and clear a failed state (systemctl(1)),
    so the next start, a try-restart's included, is not refused by the start limit. Its exit
    status is not checked: systemd refuses it for a unit it has not loaded ("Unit ... not
    loaded.", systemd 259), which has nothing to clear."""
    pipewire_client.run_program(run, pipewire_client.systemctl("reset-failed", unit),
                                check_exit=False)


def _no_block(verb: str, block: bool) -> tuple[list[str], float]:
    if block:
        return pipewire_client.systemctl(verb, UNIT), BLOCK_TIMEOUT
    return pipewire_client.systemctl("--no-block", verb, UNIT), pipewire_client.TIMEOUT


def switch_on(run: pipewire_client.Run = subprocess.run, block: bool = False) -> None:
    """Enable the unit and start it; its process runs the chain while a headset is there."""
    pipewire_client.run_program(run, pipewire_client.systemctl("enable", UNIT))
    reset_failed(run, UNIT)
    pipewire_client.run_program(run, *_no_block("start", block))


def switch_off(run: pipewire_client.Run = subprocess.run) -> None:
    pipewire_client.run_program(run, pipewire_client.systemctl("--now", "disable", UNIT))


def reenable_old_switch_on(manager_config_home: Path,
                           run: pipewire_client.Run = subprocess.run) -> bool:
    """Enable the unit again by its own [Install] where OLD_WANTS is a link below
    `manager_config_home`, the user manager's XDG_CONFIG_HOME, where systemctl has the
    manager make it: reenable drops every link named after the unit, that one included,
    and makes the one [Install] names. True if it ran, False where there is no such link.
    StepError: systemctl failed."""
    # The link is the switch-on, so is-enabled is not asked: it would count one under
    # pipewire.service the same.
    if not (manager_config_home / OLD_WANTS).is_symlink():
        return False
    pipewire_client.run_program(run, pipewire_client.systemctl("reenable", UNIT))
    return True


def restart(run: pipewire_client.Run = subprocess.run, block: bool = False) -> None:
    """Restart the unit if it runs, so its process renders the chain from what is saved. A
    failed unit is left failed for summary to report: try-restart does nothing for a unit
    that does not run, and reset-failed would only erase the failure."""
    if (_unit(run) or {}).get("ActiveState") == "failed":
        return
    reset_failed(run, UNIT)
    pipewire_client.run_program(run, *_no_block("try-restart", block))


def set_head(head: str, run: pipewire_client.Run = subprocess.run) -> None:
    """Save the head and restart the unit if it runs, which renders the head."""
    save_choices(replace(load_choices(), head=head))
    restart(run)


def set_headset(headset: str | None, run: pipewire_client.Run = subprocess.run) -> None:
    """Save the headset picked to play to, a key of HEADSET_KEYED or None for the one
    connected first, and restart the unit if it runs, which picks again (pick)."""
    save_choices(replace(load_choices(), headset=headset))
    restart(run)


def _unit(run: pipewire_client.Run) -> dict[str, str] | None:
    """The unit as `systemctl show` gives it; None when systemctl fails,
    which read_state reports."""
    try:
        return pipewire_client.unit_of(run, SHOW)
    except pipewire_client.StepError:
        return None


def _starting(unit: dict[str, str] | None) -> bool:
    """systemd says the unit is active or activating, other than waiting out
    RestartSec: the start after it renders the room just saved. A systemctl
    that failed says neither."""
    unit = unit or {}
    return (unit.get("ActiveState") in ("active", "activating")
            and unit.get("SubState") not in ("auto-restart", "auto-restart-queued"))


def set_room(room: str, run: pipewire_client.Run = subprocess.run, *,
             by_hand: bool = True) -> None:
    """Save the room and set it live on a running sink, waiting up to pipewire_client.SETTLE
    for the sink of a unit that is starting. With no chain to set, because the unit is not
    running (where a PipeWire that cannot be read shows no chain run by hand), no headset is
    there for it to play to, the unit's process could not write the chain (its StatusText)
    or its chain went away during the set, the room applies at its next start. With
    `by_hand` false, a chain run by hand is not set either: `headset-spatial on` starts the
    unit, which renders the room saved, and a sink Gearbench did not start may be any
    program's.
    StepError: no sink has come within SETTLE (STILL_STARTING), the room did not change
    on the sink, or PipeWire could not be read or set otherwise."""
    save_choices(replace(load_choices(), room=room))
    unit = _unit(run)
    owner = _chain_owner(unit)
    # Owner 0 matches no node, and owner None a chain run by hand: with no start under way
    # there is no chain of the unit's to set.
    if (owner == 0 or (owner is None and not by_hand)) and not _starting(unit):
        return
    try:
        graph = _graph(run, owner)
    except pipewire_client.StepError:
        # Owner None with no start under way: only a chain run by hand could take the
        # room live, and a PipeWire that cannot be read shows none.
        if owner is None and not _starting(unit):
            return
        raise
    # A start under way may have rendered the conf before the save.
    deadline = time.monotonic() + pipewire_client.SETTLE
    while graph["sink_id"] is None:
        # No chain is coming: the next one renders the saved room.
        if not _starting(unit) or not graph["present"] or (unit or {}).get("StatusText"):
            return
        if time.monotonic() >= deadline:
            raise pipewire_client.StepError(STILL_STARTING)
        time.sleep(pipewire_client.SETTLE_STEP)
        unit = _unit(run)
        owner = _chain_owner(unit)
        graph = _graph(run, owner)
    # pw-cli 1.6 exits 0 when its command fails, so the read-back decides and
    # pw-cli's words only name the cause.
    words = pipewire_client.pw_cli(
        run, ["pw-cli", "set-param", str(graph["sink_id"]), "Props", room_props(room)])
    graph = _graph(run, owner)
    if graph["sink_id"] is None:
        return
    gain = graph["room_gain"]
    # An absent gain, with no Props or on a chain that has not played yet,
    # is not known, and without an error from pw-cli not a failure.
    if _room_of(gain) == room or (gain is None and not words):
        return
    cause = f" ({words})" if words else ""
    raise pipewire_client.StepError(f"the room did not change while {DESCRIPTION} runs{cause}; "
                       "it takes effect at its next start")


def room_props(room: str) -> str:
    """The Props that `pw-cli set-param` sets for `room`."""
    gain = f"{ROOM_GAINS[room]:.1f}"
    pairs = " ".join(f'"{control}" {gain}' for control in ROOM_CONTROLS)
    return f"{{ params = [ {pairs} ] }}"


def _room_of(gain: float | None) -> str | None:
    """The room a read-back gain belongs to. The chain holds its controls as
    floats, so the match has a tolerance."""
    if gain is None:
        return None
    return next((room for room, value in ROOM_GAINS.items() if abs(gain - value) < 0.005), None)


def _streams_words(streams: tuple[Stream, ...]) -> str:
    """Each name and channel count once, with a count when a program plays
    several streams: Arc Raiders played three (measured 2026-09-28)."""
    words = []
    for stream, count in Counter(streams).items():
        parts = [stream.name]
        if stream.channels is not None:
            parts.append(wording.counted(stream.channels, "channel"))
        if count > 1:
            parts.append(f"{count} streams")
        words.append(", ".join(parts))
    return "; ".join(words)


#: The playing kind's words: summary's go on to list the streams, describe's stop here.
IN_USE = "in use"


#: The outputs the chain plays to, each kind once; a headset plugged in on a profile without
#: its output has none.
ANY_OUTPUT = wording.word_list(list(dict.fromkeys(headset.waited_for for headset in HEADSETS)))


def unit_kind(state: InstalledUnitState, unit: str) -> tuple[str, str] | None:
    """(kind, words) for the first of the installed user unit `unit`'s own conditions below
    that holds; None while it runs and pw-dump was read. A failed unit's journal is named by
    a field journald sets, which no program of the user's can."""
    if state.systemd:
        return "no_manager", pipewire_client.NO_MANAGER.format(state.systemd)
    if not state.installed:
        return "not_installed", f"not installed: {INSTALL_HINT}"
    if not state.enabled:
        return "off", "off"
    if state.active == "failed":
        return "failed", f"did not start: `journalctl --user _SYSTEMD_USER_UNIT={unit}` says why"
    if state.active in ("activating", "reloading"):
        return "starting", "starting"
    if state.active == "deactivating":
        return "stopping", "stopping"
    if state.active != "active":
        return "stopped", "on, but stopped: switch it off and on again"
    if not state.tools:
        return "no_tools", pipewire_client.NO_TOOLS
    if state.pipewire:
        return "pipewire", pipewire_client.PIPEWIRE_NOT_READ.format(state.pipewire)
    return None


def summary(state: State) -> tuple[str, str]:
    """(kind, words) for `state`: the unit's own (unit_kind), but off with a sink there that
    Gearbench did not start, else the first condition below that holds, those of a unit that
    runs with no sink in _without_sink."""
    if (kind := unit_kind(state, UNIT)) is not None:
        if kind[0] == "off" and state.sink_id is not None:
            return "manual", f"off, but a {DESCRIPTION} runs that Gearbench did not start"
        return kind
    if state.sink_id is None:
        return _without_sink(state)
    if not state.linked:
        on = headset_of(state.headset)
        words = f"on, but not linked to {'a headset' if on is None else on.linked_to}"
        return "unlinked", f"{words}: {state.link_error}" if state.link_error else words
    if state.streams:
        return "playing", f"{IN_USE}: {_streams_words(state.streams)}"
    return "ready", f"on, linked to {state.through or 'its output'}"


def _without_sink(state: State) -> tuple[str, str]:
    """summary's (kind, words) for a unit that runs with no sink."""
    if not state.present:
        return "waiting", f"on, waiting for {ANY_OUTPUT}"
    if state.status:
        return "no_chain", f"on, but no chain runs: {state.status}"
    return "starting", "starting"


def stereo_hint(state: State) -> str | None:
    """What to do about a game that sends stereo into the 7.1 sink."""
    for stream in state.streams:
        if stream.channels == 2:
            return (f"{stream.name} sends 2 channels: set its audio output to surround "
                    "or System Default, and start it again")
    return None


def stale_hint(state: State) -> str | None:
    """What to do about games whose launch option names RETIRED_SINK, which no sink has."""
    if not state.stale:
        return None
    one = len(state.stale) == 1
    return (f"{wording.word_list(state.stale, 'and')} {'plays' if one else 'play'} on the "
            f"default output: change {RETIRED_SINK} in "
            f"{'its launch option' if one else 'their launch options'} to {SINK}")


def aimed(state: State) -> Headset | None:
    """The headset the chain plays to, as its sink says, or with no chain the one pick takes
    among those there; None for none."""
    if state.headset in HEADSET_KEYED:
        return HEADSET_KEYED[state.headset]
    chosen = pick([(HEADSET_KEYED[key], "") for key in state.present], state.choices.headset)
    return None if chosen is None else chosen[0]


def headset_words(choices: Choices) -> str:
    """The headset picked to play to, or the rule for none picked."""
    return FIRST_CONNECTED if choices.headset is None else HEADSET_KEYED[choices.headset].name


def describe(state: State) -> list[tuple[str, str]]:
    """(label, value) rows for the CLI and the About dialog."""
    head = state.choices.head
    room = state.choices.room
    running = _room_of(state.room_gain)
    kind, words = summary(state)
    rows = [
        # The playing row lists the streams, so this one does not repeat them.
        ("spatial", IN_USE if kind == "playing" else words),
        ("output", f"{DESCRIPTION} ({SINK})"),
        ("headset", headset_words(state.choices)),
        ("head", f"{HEAD_NAMES[head]} (SADIE II {HEAD_SUBJECTS[head]})"),
        ("room", f"{room}; running: {running}" if running not in (None, room) else room),
    ]
    if state.streams:
        rows.append(("playing", _streams_words(state.streams)))
    rows += [("note", hint) for hint in (stereo_hint(state), stale_hint(state)) if hint]
    rows.append(("launch option", LAUNCH_OPTION))
    # The headset played to, while its card's profile set is known.
    played = headset_of(state.headset) if state.profile_set is not None else None
    if (joined := joined_headset(state)) is not None:
        headset, profile_set = joined
        # One plugged in with nothing to play to is named: why the chain waits.
        words = headset.outputs_joined.format(profile_set) + (
            "" if headset is played else f" on the {headset.name}")
        rows.append(("outputs", f"{words}, {OUTSIDE_RULE}" if not headset.ruled
                     else f"{words}; {JOINED_HINT}" if restart_needed(state, headset)
                     else f"{words} {UNTIL_RESTART}"))
    elif played is not None:
        rows.append(("outputs", played.outputs_apart))
    return rows
