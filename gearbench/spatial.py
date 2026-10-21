"""Spatial sound: its configuration, its user service, and what PipeWire says of it.

Gearbench Spatial is one 7.1 sink, rendered for headphones on the headset it plays to: the
Astro A50's Game output, or the Logitech G432, through Logitech G432 Equalizer while that
runs. gearbench-spatial.service's process (gearbench.spatial_unit) follows PipeWire and runs
the filter chain, rendered from gearbench-spatial.conf.in and spatial.json's head, room and
headset, while a headset is there, and renders and starts it again when the headset or the
node it plays to changes; on and off is the unit's enable link, stored nowhere else. A
`Headset` holds how a headset's card and output are found and the words that name them,
and a `Target` where the chain plays. Standard library only, never run in the daemon.
Every outside program goes through a `run` argument, and a process is read under `PROC`.
"""

from __future__ import annotations

import json
import re
import struct
import subprocess
import time
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from . import config, wording

UNIT = "gearbench-spatial.service"
CARD = "alsa_card.usb-Astro_Gaming_Astro_A50-00"
GAME = "alsa_output.usb-Astro_Gaming_Astro_A50-00.stereo-game"
#: Hard-coded in the WirePlumber rule; the tests hold the rule to it.
CHAT = "alsa_output.usb-Astro_Gaming_Astro_A50-00.stereo-chat"
PROFILE_SET = "gearbench-astro-a50.conf"
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
#: The G432's card in PipeWire: a Device with these ids, in the form PipeWire 1.6.2 printed
#: the A50's on 2026-09-27, `0x` and four lowercase digits.
G432_VENDOR_ID, G432_PRODUCT_ID = "0x046d", "0x0a9c"
#: The pipewire.access of a client PipeWire did not restrict when it connected, which the
#: client cannot change. A sandboxed app's, "flatpak" or "restricted", may make Devices and
#: nodes with any props, a card's ids and device.id among them: WirePlumber 0.5.13 gives it
#: "rx" (access-default.lua).
TRUSTED_ACCESS = frozenset({"unrestricted", "default"})
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
#: The client's settings, which each chain's conf opens with after its first comment.
CLIENT_CONTEXT = """\
# PipeWire's default: at 0, a conf that fails to load leaves the unit's
# journal nothing to say why.
context.properties = {
    log.level = 2
}

context.spa-libs = {
    audio.convert.* = audioconvert/libspa-audioconvert
    support.*       = support/libspa-support
}"""
#: The modules a client that makes nodes loads, first in each chain's context.modules.
CLIENT_MODULES = """\
    { name = libpipewire-module-rt
        flags = [ ifexists nofail ]
    }
    { name = libpipewire-module-protocol-native }
    { name = libpipewire-module-client-node }
    { name = libpipewire-module-adapter }"""
ROOM_WAV = "room-150ms.wav"
HRIR_FILES = ("azi_30,0_ele_0,0.wav", "azi_330,0_ele_0,0.wav", "azi_0,0_ele_0,0.wav",
              "azi_90,0_ele_0,0.wav", "azi_270,0_ele_0,0.wav", "azi_150,0_ele_0,0.wav",
              "azi_210,0_ele_0,0.wav")
#: Hard-coded in the spatial unit; the tests hold the unit to it.
CONF_NAME = "gearbench-spatial.conf"
#: What a conf's SPA-JSON string cannot hold: " and \ would end or escape it, and PipeWire
#: refuses a character below 0x20 ("Character not allowed").
NOT_IN_CONF = re.compile(r'["\\\x00-\x1f]')
#: The props the template gives the chain's sink: the headset it plays to, by key, and the
#: node its output is aimed at, which read_state reads back.
TARGET_PROPS = ("gearbench.headset", "gearbench.target")
PACKAGED = Path("/usr/share/gearbench/spatial")
TIMEOUT = 5.0          # any one program
BLOCK_TIMEOUT = 30.0   # a blocking start or restart (the CLI's)
#: How long a wait for PipeWire to catch up with a start or a change lasts, and how often
#: it looks: systemctl start and systemd-run return before PipeWire has built the chain.
SETTLE = 2.0
SETTLE_STEP = 0.2
#: set_room's failure when a starting chain's sink has not come within SETTLE.
STILL_STARTING = (f"{DESCRIPTION} was still starting after {SETTLE:g} s; "
                  "it keeps the room it started with until its next start.")
Run = Callable[..., subprocess.CompletedProcess]

SHOW = ["systemctl", "--user",
        "--property=Id,LoadState,ActiveState,SubState,UnitFileState,Result,MainPID,StatusText",
        "show", UNIT]
ENABLED_STATES = ("enabled", "enabled-runtime")
DUMP = ["pw-dump", "--no-colors"]
#: pw-dump's monitor. pw-dump 1.6.2 prints its first dump, then each object that changes,
#: whole, and {"id": N, "info": null} for one removed, each print a JSON array whose "["
#: and "]" stand alone on their lines (a throwaway PipeWire, 2026-10-17).
MONITOR = ["pw-dump", "--monitor", "--no-colors"]
#: The program that runs a filter chain's conf, `pipewire -c`.
PIPEWIRE = "/usr/bin/pipewire"
#: The most one print may hold before printed drops it: far over the 74 KB of a
#: throwaway PipeWire's first dump (2026-10-17), and far under what would fill the memory.
PRINT_MAX = 64 * 2 ** 20
#: The journal line of a unit following pw-dump --monitor for a print it did not read, by
#: the unit's name, then the cause.
UNREAD = "{}: a print of pw-dump --monitor was not read ({{}}); it reads on"
#: Why a print was not read: printed's None.
NOT_A_PRINT = f"no JSON, or over {PRINT_MAX // 2 ** 20} MiB"
#: What to do about a missing unit file, in summary and under the CLI's systemctl error.
INSTALL_HINT = "run ./install.sh again, or install the Gearbench package"
#: What to do about a card whose outputs are joined, in describe's outputs row.
JOINED_HINT = "restart WirePlumber: systemctl --user restart wireplumber"
#: Why a pw-dump whose objects shaped cannot read is refused.
UNKNOWN_SHAPE = "pw-dump printed objects of a shape Gearbench does not know"
#: Where parent_of reads a process; a test points it at a tree of its own.
PROC = Path("/proc")


class SpatialError(Exception):
    """A step that failed, in words for a toast or stderr."""


@dataclass(frozen=True)
class Choices:
    head: str = "ku100"
    room: str = "competitive"
    #: The headset picked to play to, a key of HEADSET_NAMES; None for none picked, which
    #: plays to the one connected first (pick).
    headset: str | None = None


@dataclass(frozen=True)
class Headset:
    """A headset the chain plays to: its key, its name, the props its card's Device has in
    PipeWire, the node it plays to there (None: the card's first Audio/Sink), and the words
    for it."""

    key: str
    name: str
    card: tuple[tuple[str, str], ...]
    game: str | None
    #: The output as the conf's first comment names it.
    plays_on: str
    #: The output as the summary says the chain is not linked to it, and as it says the
    #: chain waits for it.
    linked_to: str
    waited_for: str
    #: The card's profile set that gives it the output the chain needs, and describe's
    #: outputs row for it, and for another, named at {}, before JOINED_HINT.
    profile_set: str
    outputs_apart: str
    outputs_joined: str
    #: The window's outputs row while the card has another profile set.
    joined_shown: str


@dataclass(frozen=True)
class Target:
    """Where the chain plays: the headset, the node its output is aimed at, and the words
    the conf's comments name that node by."""

    headset: Headset
    node: str
    words: str


A50 = Headset(
    key="a50", name="Astro A50", card=(("device.name", CARD),), game=GAME,
    plays_on="the Astro A50's Game output", linked_to="Astro A50 Game",
    waited_for="Astro A50 Game", profile_set=PROFILE_SET,
    outputs_apart="Astro A50 Game and Chat apart", outputs_joined="one entry ({})",
    joined_shown="Astro A50 Game and Chat show as one output")
G432 = Headset(
    key="g432", name="Logitech G432",
    card=(("device.vendor.id", G432_VENDOR_ID), ("device.product.id", G432_PRODUCT_ID)),
    game=None, plays_on="the Logitech G432's own output", linked_to="the G432",
    waited_for="a Logitech G432's output", profile_set="analog-only.conf",
    outputs_apart="one analog output", outputs_joined="an analog and a digital output ({})",
    joined_shown="The G432 shows an analog and a digital output")
HEADSETS = A50, G432
#: The headsets the chain plays to, by the key spatial.json keeps the one picked under.
HEADSET_NAMES = {headset.key: headset.name for headset in HEADSETS}
#: The words for no headset picked, which plays to the one connected first (pick).
FIRST_CONNECTED = "the one connected first"
#: render's target when none is given.
A50_GAME = Target(headset=A50, node=GAME, words=A50.plays_on)


def headset_of(key: object) -> Headset | None:
    """The headset of HEADSETS whose key is `key`, a value pw-dump or State holds; None for
    none."""
    return next((headset for headset in HEADSETS if headset.key == key), None)


@dataclass(frozen=True)
class Stream:
    name: str               # application.name, else node.description, else node.name
    channels: int | None    # its Format param's channels, else audio.channels, else None


@dataclass(frozen=True)
class State:
    systemd: str | None = None        # why `systemctl --user show` failed
    installed: bool = True            # LoadState != "not-found"
    enabled: bool = False             # UnitFileState in ENABLED_STATES
    active: str = "inactive"          # ActiveState
    result: str = "success"           # Result
    #: The unit's StatusText, its process's word on why no chain runs for a headset that is
    #: there (spatial_unit.status); None for none.
    status: str | None = None
    tools: bool = True                # False: pw-dump not installed
    pipewire: str | None = None       # why pw-dump failed or did not parse
    sink_id: int | None = None
    output_id: int | None = None
    #: The headset the chain's sink says it plays to (TARGET_PROPS), None without a chain.
    headset: str | None = None
    #: The node the chain's sink says its output is aimed at, None without a chain.
    target: str | None = None
    #: The headsets whose output is in PipeWire, by key, in the order their cards came
    #: (present).
    present: tuple[str, ...] = ()
    linked: bool = False              # a Link from OUTPUT, state not "error"/"unlinked"
    link_error: str | None = None
    #: The node that link plays to: its node.description, else its node.name, as text.
    through: str | None = None
    running: bool = False             # the SINK node's info.state == "running"
    #: "outL:Gain 2" in SINK's Props params (else OUTPUT's); None while not known.
    room_gain: float | None = None
    #: The device.profile-set of the card of the headset the chain plays to; None without
    #: a chain or the card.
    profile_set: str | None = None
    streams: tuple[Stream, ...] = ()  # nodes linked into SINK, deduplicated, by id
    #: Streams that ask for RETIRED_SINK (its target.object), by name, each once.
    stale: tuple[str, ...] = ()
    choices: Choices = Choices()


def choices_path() -> Path:
    return config.config_dir() / "spatial.json"


def load_choices(path: Path | None = None) -> Choices:
    """The saved head, room and headset, each field falling back to its default on its own:
    a file from before the headset has none, which is none picked."""
    target = choices_path() if path is None else path
    try:
        data = config.read_json(target)
    except (OSError, config.ConfigError):
        return Choices()
    if not isinstance(data, dict):
        return Choices()
    default = Choices()
    head = data.get("head")
    room = data.get("room")
    headset = data.get("headset")
    return Choices(head=head if head in HEADS else default.head,
                   room=room if room in ROOMS else default.room,
                   headset=headset if isinstance(headset, str) and headset in HEADSET_NAMES
                   else None)


def _check(choices: Choices) -> None:
    if choices.head not in HEADS:
        raise ValueError(f"unknown head {choices.head!r}: one of {', '.join(HEADS)}")
    if choices.room not in ROOMS:
        raise ValueError(f"unknown room {choices.room!r}: one of {', '.join(ROOMS)}")
    if choices.headset is not None and choices.headset not in HEADSET_NAMES:
        raise ValueError(f"unknown headset {choices.headset!r}: one of "
                         f"{', '.join(HEADSET_NAMES)}")


def save_choices(choices: Choices, path: Path | None = None) -> None:
    """`choices` to spatial.json, or to `path`, atomically (config.write_json), once _check
    passes them."""
    _check(choices)
    target = choices_path() if path is None else path
    target.parent.mkdir(parents=True, exist_ok=True)
    # No headset key for none picked, so a file with none reads as before it had one.
    picked = {} if choices.headset is None else {"headset": choices.headset}
    config.write_json(target, {"head": choices.head, "room": choices.room, **picked},
                      ".spatial-")


def data_dir() -> Path:
    """The checkout's spatial/ when running from one, else the package's."""
    here = Path(__file__).resolve().parents[1] / "spatial"
    return here if (here / TEMPLATE).is_file() else PACKAGED


def wav_info(path: Path) -> tuple[int, int]:
    """(channels, rate) from the RIFF chunks."""
    data = path.read_bytes()
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise SpatialError(f"{path} is not a WAV file")
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
        raise SpatialError(f"{path} has no fmt or data chunk")
    return fmt[1], fmt[2]


def render(choices: Choices, data: Path | None = None, target: Target = A50_GAME) -> str:
    """The chain's conf for `choices`, from `data`/TEMPLATE (data_dir() by default), aimed
    at `target`."""
    # A G432's node name comes from pw-dump.
    if not target.node or NOT_IN_CONF.search(target.node):
        raise SpatialError(f"the output '{wording.escaped(target.node)}' cannot be named in "
                           "the chain's conf")
    # PipeWire resolves a relative filename against its own working directory.
    base = (data_dir() if data is None else data).absolute()
    template = (base / TEMPLATE).read_text(encoding="utf-8")
    hrir = base / "sadie-ii" / HEAD_SUBJECTS[choices.head]
    room = base / ROOM_WAV
    for path in (hrir, room):
        if NOT_IN_CONF.search(str(path)):
            raise SpatialError(f"{wording.escaped(path)}: a path with \", \\ or a control "
                               "character such as a tab cannot go in the chain's conf; move "
                               "the checkout to a path without them and run ./install.sh again")
    for path in (*(hrir / name for name in HRIR_FILES), room):
        if not path.is_file():
            raise SpatialError(f"{path} is missing")
        channels, rate = wav_info(path)
        if (channels, rate) != (2, 48000):
            raise SpatialError(f"{path} is {wording.counted(channels, 'channel')} "
                               f"at {rate} Hz, not 2 channels at 48000 Hz")
    fills = {"@HRIR@": str(hrir), "@ROOM@": str(room),
             "@ROOM_GAIN@": f"{ROOM_GAINS[choices.room]:.1f}", "@TARGET@": target.node,
             "@HEADSET@": target.headset.key, "@PLAYS_ON@": target.words,
             "@CLIENT_CONTEXT@": CLIENT_CONTEXT, "@CLIENT_MODULES@": CLIENT_MODULES}
    # One pass over the template alone: a fill is never read again, so a folder named like
    # a placeholder stays in its path.
    for left in re.findall(r"@[A-Z_]+@", template):
        if left not in fills:
            raise SpatialError(f"{base / TEMPLATE} has {left}, which nothing fills")
    return re.sub(r"@[A-Z_]+@", lambda match: fills[match.group()], template)


def write_conf(path: Path, choices: Choices, target: Target) -> None:
    """Render the chain for `choices` and `target` and replace `path` atomically, mode
    0600."""
    # replace_file's mkstemp makes it 0600, and the replace keeps PipeWire from reading a
    # half-written conf. The conf is in the runtime folder, so its folder is not synced.
    text = render(choices, target=target)
    config.replace_file(path, text.encode("utf-8"), f".{path.name}-", "")


class NotInstalled(SpatialError):
    """The program is not installed."""


def program_words(done: subprocess.CompletedProcess, program: str) -> str:
    """A program's stderr on one line, else how it ended."""
    return " ".join(done.stderr.split()) or wording.ended(program, done.returncode)


def run_program(run: Run, argv: list[str], timeout: float = TIMEOUT,
                check_exit: bool = True) -> subprocess.CompletedProcess:
    """Run one program; any failure is a SpatialError in words."""
    program = argv[0]
    try:
        # errors="replace": any PipeWire client names its own nodes, and one name that is not
        # UTF-8 would make all of pw-dump unreadable.
        done = run(argv, check=False, capture_output=True, encoding="utf-8",
                   errors="replace", timeout=timeout)
    except FileNotFoundError:
        raise NotInstalled(f"{program} is not installed") from None
    except subprocess.TimeoutExpired:
        raise SpatialError(f"{program} took over {timeout:g} s") from None
    except OSError as err:
        raise SpatialError(f"{program} did not run: {wording.reason(err)}") from None
    if check_exit and done.returncode != 0:
        raise SpatialError(program_words(done, program))
    return done


def dump_objects(run: Run) -> list:
    """pw-dump's objects; SpatialError for an answer that is no list of them."""
    try:
        objects = json.loads(run_program(run, DUMP).stdout)
    except (ValueError, RecursionError):
        raise SpatialError("pw-dump printed something other than JSON") from None
    if not isinstance(objects, list):
        raise SpatialError("pw-dump printed something other than a list of objects")
    return objects


def printed(lines: Iterable[str]) -> Iterator[list | None]:
    """Each print of pw-dump --monitor in `lines`, a JSON array, or None for one that is
    no JSON or is over PRINT_MAX, which is not kept past it. A print ends at a line that
    is "]" alone."""
    held: list[str] = []
    size = 0
    for line in lines:
        size += len(line)
        if size <= PRINT_MAX:
            held.append(line)
        if line.rstrip("\n") != "]":
            continue
        # One over PRINT_MAX lacks its closing "]", so it is no JSON; JSON that ends at a
        # "]" is an array.
        try:
            found = json.loads("".join(held))
        except (ValueError, RecursionError):
            found = None
        held, size = [], 0
        yield found


def merge(objects: dict[int, dict], found: list) -> None:
    """Fold one print into `objects`, by id: an object printed whole replaces the one
    before it, and one removed, printed with no type, goes."""
    for obj in found:
        if isinstance(obj, dict) and wording.whole(obj.get("id")):
            if "type" in obj:
                objects[obj["id"]] = obj
            else:
                objects.pop(obj["id"], None)


def read_state(run: Run = subprocess.run) -> State:
    """What systemd and PipeWire say now, and the saved choices. Never raises."""
    fields: dict = {"choices": load_choices()}
    unit = None
    try:
        unit = unit_of(run)
    except SpatialError as err:
        fields["systemd"] = str(err)
    else:
        fields.update(
            installed=unit.get("LoadState") != "not-found",
            enabled=unit.get("UnitFileState") in ENABLED_STATES,
            active=unit.get("ActiveState") or "inactive",
            result=unit.get("Result") or "success",
            status=unit.get("StatusText") or None)
    try:
        fields.update(_graph(run, _chain_owner(unit)))
    except NotInstalled:
        fields.update(tools=False)
    except SpatialError as err:
        fields.update(pipewire=str(err))
    return State(**fields)


def unit_of(run: Run, show: Sequence[str] = SHOW) -> dict[str, str]:
    """The unit, Gearbench Spatial's or the one `show` asks for, as `systemctl show` gives
    it; SpatialError when systemctl fails."""
    units = parse_units(run_program(run, list(show)).stdout)
    return units[0] if units else {}


def main_pid(unit: dict[str, str]) -> int:
    """The unit's MainPID, 0 for none, which matches no client's pid."""
    return _whole(unit.get("MainPID")) or 0


def parse_units(text: str) -> list[dict[str, str]]:
    """`systemctl show` output, one dict per unit in argument order."""
    units = []
    for block in re.split(r"\n[ \t]*\n", text.strip()):
        unit = {}
        for line in block.splitlines():
            key, sep, value = line.partition("=")
            if sep:
                unit[key] = value
        if unit:
            units.append(unit)
    return units


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


def _shown(name: object) -> str:
    """A node's name as text the window shows unescaped: a client names its own nodes, and
    a newline or a bidi override in one would break or turn the row. Cut at NAME_MAX."""
    text = wording.printable(str(name))
    return text[:NAME_MAX - 1] + "…" if len(text) > NAME_MAX else text


def _stream_name(props: dict, node_id: int) -> str:
    return _shown(props.get("application.name") or props.get("node.description")
                  or props.get("node.name") or f"node {node_id}")


def _stream(info: dict, node_id: int) -> Stream:
    return Stream(_stream_name(info.get("props") or {}, node_id), _channels(info))


def _chain_owner(unit: dict[str, str] | None) -> int | None:
    """The pid whose child's nodes graph_state takes for the chain's: the unit's main
    process, its watcher (spatial_unit), whose child `pipewire -c` makes them; 0, matching
    nothing, while it has none or systemctl did not answer. None, matching by name alone,
    while the unit is neither enabled nor running: a chain run by hand, which summary calls
    manual."""
    if unit is None:
        return 0
    pid = main_pid(unit)
    if pid:
        return pid
    return 0 if unit.get("UnitFileState") in ENABLED_STATES else None


def parent_of(pid: int) -> int | None:
    """The pid of process `pid`'s parent, the fourth field of its stat; None for a process
    that cannot be read: one gone since pw-dump printed it, or a pid no process has."""
    try:
        stat = (PROC / str(pid) / "stat").read_bytes()
    except OSError:
        return None
    # The name, the second field, is in parentheses and can hold spaces and ")" itself.
    return int(stat.rpartition(b")")[2].split()[1])


def _whole(value: object) -> int | None:
    """A whole number from pw-dump or systemctl, as a JSON number or a string of digits,
    else None."""
    if wording.whole(value):
        return value
    try:
        return int(value) if isinstance(value, str) and value.isdecimal() else None
    except ValueError:      # past int()'s 4300 digits
        return None


def shaped[T](parse: Callable[[Sequence[object]], T], objects: Sequence[object]) -> T:
    """`parse` of pw-dump's objects; SpatialError on objects of a shape it cannot read,
    such as an id or a prop that is a list where a set lookup needs one it can hash."""
    try:
        return parse(objects)
    # OverflowError: float() of a whole number no float holds, in a control's place.
    # RecursionError: str() of a value nested deeper than it walks, which json.loads
    # still builds.
    except (AttributeError, TypeError, OverflowError, RecursionError):
        raise SpatialError(UNKNOWN_SHAPE) from None


def dumped[T](run: Run, parse: Callable[[Sequence[object]], T]) -> T:
    """`parse` of a fresh pw-dump's objects, through shaped."""
    return shaped(parse, dump_objects(run))


def settled[S](read_state: Callable[[], S], summary: Callable[[S], tuple[str, str]],
               waits: bool) -> S:
    """read_state's state, read again every SETTLE_STEP for up to SETTLE while `waits` and
    its summary is starting."""
    state = read_state()
    deadline = time.monotonic() + SETTLE
    while waits and summary(state)[0] == "starting" and time.monotonic() < deadline:
        time.sleep(SETTLE_STEP)
        state = read_state()
    return state


def _graph(run: Run, owner: int | None) -> dict:
    """graph_state of a fresh pw-dump (dumped)."""
    return dumped(run, lambda objects: graph_state(objects, owner))


def of_type(objects: Sequence[object], kind: str) -> list[tuple[int, dict, dict]]:
    """Each object of PipeWire's type `kind` with a whole id and an info, as (id, info,
    props), props that are missing, null, false, 0 or empty as {}; TypeError for any other
    props that are no object, which shaped refuses as UNKNOWN_SHAPE."""
    found = []
    for obj in objects:
        if (isinstance(obj, dict) and obj.get("type") == f"PipeWire:Interface:{kind}"
                and wording.whole(obj.get("id")) and isinstance(obj.get("info"), dict)):
            props = obj["info"].get("props") or {}
            if not isinstance(props, dict):
                raise TypeError(f"{kind} {obj['id']}: props that are no object")
            found.append((obj["id"], obj["info"], props))
    return found


def card_nodes(objects: Sequence[object], card: Sequence[tuple[str, object]]
               ) -> tuple[dict[int, dict], list[dict]]:
    """The Devices in pw-dump's objects with each of `card`'s props, a Headset's card, by id
    with their props, and the props of their nodes, each in pw-dump's order. A client sets
    the props of what it makes, device.id included, so a Device counts only when its
    client.id names a Client PipeWire did not restrict (TRUSTED_ACCESS), and a node only when
    its device.id is such a Device and its client.id is that Device's; one with no client.id
    counts for none."""
    trusted = {client for client, _info, props in of_type(objects, "Client")
               if props.get("pipewire.access") in TRUSTED_ACCESS}
    cards = {found: props for found, _info, props in of_type(objects, "Device")
             if all(props.get(key) == value for key, value in card)
             and _whole(props.get("client.id")) in trusted}
    nodes = [props for _node, _info, props in of_type(objects, "Node")
             if (found := _whole(props.get("device.id"))) in cards
             and _whole(props.get("client.id")) == _whole(cards[found].get("client.id"))]
    return cards, nodes


def _card_of(headset: Headset, objects: Sequence[object]) -> tuple[dict, list[dict], int] | None:
    """`headset`'s card in pw-dump's objects: the first Device with each of its card's
    props, the nodes of every such Device (card_nodes), and when the first came, its
    object.serial, which PipeWire counts up, else its id; None without one."""
    cards, nodes = card_nodes(objects, headset.card)
    if not cards:
        return None
    card, props = next(iter(cards.items()))
    came = _whole(props.get("object.serial"))
    return props, nodes, card if came is None else came


def output_of(headset: Headset, nodes: Sequence[dict]) -> str | None:
    """`headset`'s output among its card's `nodes` (card_nodes): the Audio/Sink named its
    game, or for a headset with no game the first with a name; None while there is none."""
    for props in nodes:
        name = props.get("node.name")
        if (props.get("media.class") == "Audio/Sink" and isinstance(name, str)
                and (headset.game is None or name == headset.game)):
            return name
    return None


def present(objects: Sequence[object]) -> list[tuple[Headset, str]]:
    """The headsets whose output is in pw-dump's objects, each with that output's name, in
    the order their cards came: the one connected first, first."""
    found = []
    for headset in HEADSETS:
        card = _card_of(headset, objects)
        if card is not None and (output := output_of(headset, card[1])) is not None:
            found.append((card[2], headset, output))
    return [(headset, output) for _came, headset, output in sorted(found, key=lambda f: f[0])]


def pick(found: Sequence[tuple[Headset, str]], picked: str | None) -> tuple[Headset, str] | None:
    """The headset the chain plays to among `found`, present's, with its output: the one
    picked while it is there, else the one connected first, which with two is the other one
    when the pick is unplugged (the owner's decision of 2026-10-18); None for none."""
    return next((each for each in found if each[0].key == picked), found[0] if found else None)


def graph_state(objects: Sequence[object], owner: int | None = None) -> dict:
    """State's pw-dump fields from pw-dump's objects.

    The chain's sink and output are the Audio/Sink and stream of their names made by a
    child of the process `owner` (_chain_owner), the unit's watcher, by the pid PipeWire
    took from the owning client's socket and that process's parent (parent_of), since any
    client can name a node gearbench-spatial; with `owner` None the name and class decide.
    The headset and the node it plays to are the ones its sink's props name (TARGET_PROPS),
    and the profile set is that headset's card's.
    """
    nodes = {node: info for node, info, _props in of_type(objects, "Node")}
    clients = {client: _whole(props.get("pipewire.sec.pid"))
               for client, _info, props in of_type(objects, "Client")}
    links = [info for obj in objects if isinstance(obj, dict)
             and obj.get("type") == "PipeWire:Interface:Link"
             and isinstance(info := obj.get("info"), dict)]

    def first(name: str, media_class: str) -> int | None:
        for node_id, info in nodes.items():
            props = info.get("props") or {}
            # No client id finds no pid: dict.get takes a None key, and typeshed's does not.
            pid = clients.get(_whole(props.get("client.id")))  # type: ignore[arg-type]
            if (props.get("node.name") == name and props.get("media.class") == media_class
                    and (owner is None or (owner > 0 and pid is not None
                                           and parent_of(pid) == owner))):
                return node_id
        return None

    sink_id = first(SINK, "Audio/Sink")
    output_id = first(OUTPUT, "Stream/Output/Audio")
    from_output = [link for link in links if output_id is not None
                   and link.get("output-node-id") == output_id]
    live = [link for link in from_output if link.get("state") not in ("error", "unlinked")]
    link_error = None
    if not live:
        link_error = next((str(link.get("error") or "error") for link in from_output
                           if link.get("state") == "error"), None)
    # No sink or output finds no node: dict.get takes a None key, and typeshed's does not.
    sink = nodes.get(sink_id, {})  # type: ignore[arg-type]
    played_id = live[0].get("input-node-id") if live else None
    played = (nodes.get(played_id) or {}).get("props") or {} if wording.whole(played_id) else {}
    through = played.get("node.description") or played.get("node.name")
    sink_props = sink.get("props") or {}
    headset = headset_of(sink_props.get(TARGET_PROPS[0]))
    target = sink_props.get(TARGET_PROPS[1])
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
        "through": None if through is None else _shown(through),
        "running": sink.get("state") == "running",
        "room_gain": room_gain,
        "profile_set": None if headset is None else _profile_set(headset, objects),
        "streams": tuple(_stream(nodes[node_id], node_id) for node_id in inputs),
        "stale": tuple(dict.fromkeys(
            _stream_name(props, node) for node, _info, props in of_type(objects, "Node")
            if props.get("target.object") == RETIRED_SINK)),
    }


def _profile_set(headset: Headset, objects: Sequence[object]) -> str | None:
    """The profile set of `headset`'s card, None without the card. ACP reads default.conf
    when the card names none. Text, as State has it: one that cannot be printed is refused
    with the rest (_graph), where describe would raise on it."""
    card = _card_of(headset, objects)
    return None if card is None else str(card[0].get("device.profile-set") or "default.conf")


def _systemctl(*args: str) -> list[str]:
    """Options go before the verb: with POSIXLY_CORRECT set, systemctl's getopt
    stops at the verb and reads a later option as a unit name."""
    return ["systemctl", "--user", *args]


def _reset_failed(run: Run) -> None:
    """Clear a start-limit failure so the next start is not refused. Its exit
    status is not checked: systemd refuses it for a unit it has not loaded
    ("Unit ... not loaded.", systemd 259), which has nothing to clear."""
    run_program(run, _systemctl("reset-failed", UNIT), check_exit=False)


def _no_block(verb: str, block: bool) -> tuple[list[str], float]:
    if block:
        return _systemctl(verb, UNIT), BLOCK_TIMEOUT
    return _systemctl("--no-block", verb, UNIT), TIMEOUT


def switch_on(run: Run = subprocess.run, block: bool = False) -> None:
    """Enable the unit and start it; its process runs the chain while a headset is there."""
    run_program(run, _systemctl("enable", UNIT))
    _reset_failed(run)
    run_program(run, *_no_block("start", block))


def switch_off(run: Run = subprocess.run) -> None:
    run_program(run, _systemctl("--now", "disable", UNIT))


def restart(run: Run = subprocess.run, block: bool = False) -> None:
    """Restart the unit if it runs, so its process renders the chain from what is saved."""
    _reset_failed(run)
    run_program(run, *_no_block("try-restart", block))


def set_head(head: str, run: Run = subprocess.run, block: bool = False) -> None:
    """Save the head and restart the unit if it runs, which renders the head."""
    save_choices(replace(load_choices(), head=head))
    restart(run, block)


def set_headset(headset: str | None, run: Run = subprocess.run, block: bool = False) -> None:
    """Save the headset picked to play to, a key of HEADSET_NAMES or None for the one
    connected first, and restart the unit if it runs, which picks again (pick)."""
    save_choices(replace(load_choices(), headset=headset))
    restart(run, block)


def _unit(run: Run) -> dict[str, str] | None:
    """The unit as `systemctl show` gives it; None when systemctl fails,
    which read_state reports."""
    try:
        return unit_of(run)
    except SpatialError:
        return None


def _starting(unit: dict[str, str] | None) -> bool:
    """systemd says the unit is active or activating, other than waiting out
    RestartSec: the start after it renders the room just saved. A systemctl
    that failed says neither."""
    unit = unit or {}
    return (unit.get("ActiveState") in ("active", "activating")
            and unit.get("SubState") not in ("auto-restart", "auto-restart-queued"))


def set_room(room: str, run: Run = subprocess.run) -> None:
    """Save the room and set it live on a running sink, waiting up to SETTLE
    for the sink of a unit that is starting. With no chain to set, because the unit is not
    running, no headset is there for it to play to, the unit's process could not write the
    chain (its StatusText) or its chain went away during the set, the room applies at its
    next start. SpatialError: no sink has come within SETTLE (STILL_STARTING), or the room
    did not change on the sink."""
    save_choices(replace(load_choices(), room=room))
    unit = _unit(run)
    owner = _chain_owner(unit)
    # Owner 0 matches no node: with no start under way there is no chain to set.
    if owner == 0 and not _starting(unit):
        return
    graph = _graph(run, owner)
    # A start under way may have rendered the conf before the save.
    deadline = time.monotonic() + SETTLE
    while graph["sink_id"] is None:
        # No chain is coming: the next one renders the saved room.
        if not _starting(unit) or not graph["present"] or (unit or {}).get("StatusText"):
            return
        if time.monotonic() >= deadline:
            raise SpatialError(STILL_STARTING)
        time.sleep(SETTLE_STEP)
        unit = _unit(run)
        owner = _chain_owner(unit)
        graph = _graph(run, owner)
    sink_id = graph["sink_id"]
    # pw-cli 1.6 exits 0 when its command fails, so the read-back decides and
    # pw-cli's words only name the cause.
    done = run_program(run, ["pw-cli", "set-param", str(sink_id), "Props", room_props(room)],
                       check_exit=False)
    words = program_words(done, "pw-cli") if done.returncode or "Error:" in done.stderr else ""
    graph = _graph(run, owner)
    if graph["sink_id"] is None:
        return
    gain = graph["room_gain"]
    # An absent gain, with no Props or on a chain that has not played yet,
    # is not known, and without an error from pw-cli not a failure.
    if _room_of(gain) == room or (gain is None and not words):
        return
    cause = f" ({words})" if words else ""
    raise SpatialError(f"The room did not change while {DESCRIPTION} runs{cause}; "
                       "it takes effect at its next start.")


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
    for room, value in ROOM_GAINS.items():
        if abs(gain - value) < 0.005:
            return room
    return None


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


#: The outputs the chain plays to; a headset plugged in on a profile without its output has none.
ANY_OUTPUT = wording.word_list([headset.waited_for for headset in HEADSETS])


def summary(state: State) -> tuple[str, str]:
    """(kind, words) for `state`: the first condition below that holds, those of a unit that
    runs with no sink in _without_sink."""
    if state.systemd:
        return "no_manager", f"systemctl --user did not answer: {state.systemd}"
    if not state.installed:
        return "not_installed", f"not installed: {INSTALL_HINT}"
    if not state.enabled:
        if state.sink_id is not None:
            return "manual", f"off, but a {DESCRIPTION} runs that Gearbench did not start"
        return "off", "off"
    if state.active == "failed":
        return "failed", f"did not start: journalctl --user _SYSTEMD_USER_UNIT={UNIT} says why"
    if state.active in ("activating", "reloading"):
        return "starting", "starting"
    if state.active != "active":
        return "stopped", "on, but stopped: switch it off and on again"
    if not state.tools:
        return "no_tools", "on, but PipeWire's pw-dump is not installed"
    if state.pipewire:
        return "pipewire", f"on, but PipeWire could not be read: {state.pipewire}"
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
        return "waiting", f"on; waits for {ANY_OUTPUT}"
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
    return (f"{wording.word_list(state.stale, 'and')} {'asks' if one else 'ask'} for "
            f"{RETIRED_SINK}, an old name, so {'it plays' if one else 'they play'} on the "
            f"default output: change {'its' if one else 'their'} launch option to "
            f"{LAUNCH_OPTION}")


def aimed(state: State) -> Headset | None:
    """The headset the chain plays to, as its sink says, or with no chain the one pick takes
    among those there; None for none."""
    by_key = {headset.key: headset for headset in HEADSETS}
    if state.headset in by_key:
        return by_key[state.headset]
    chosen = pick([(by_key[key], "") for key in state.present], state.choices.headset)
    return None if chosen is None else chosen[0]


def headset_words(choices: Choices) -> str:
    """The headset picked to play to, or the rule for none picked."""
    return FIRST_CONNECTED if choices.headset is None else HEADSET_NAMES[choices.headset]


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
    on = headset_of(state.headset)
    if on is not None and state.profile_set is not None:
        rows.append(("outputs", on.outputs_apart if state.profile_set == on.profile_set
                     else f"{on.outputs_joined.format(state.profile_set)}; {JOINED_HINT}"))
    return rows
