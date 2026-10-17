"""Spatial sound for the Astro A50: its configuration, its user service, and what PipeWire
says of it.

Astro A50 Spatial is a 7.1 sink rendered for headphones on the A50's Game output.
gearbench-spatial.service renders its filter chain from gearbench-spatial.conf.in and
spatial.json's head and room at each start, and runs it as its own PipeWire client; on and
off is the unit's enable link, stored nowhere else. Standard library only, never run in the
daemon. Every outside program goes through a `run` argument.
"""

from __future__ import annotations

import json
import re
import struct
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from . import config

UNIT = "gearbench-spatial.service"
DEVICE_UNIT = "dev-snd-by\\x2did-usb\\x2dAstro_Gaming_Astro_A50\\x2d00.device"
CARD = "alsa_card.usb-Astro_Gaming_Astro_A50-00"
GAME = "alsa_output.usb-Astro_Gaming_Astro_A50-00.stereo-game"
#: Hard-coded in the WirePlumber rule; the tests hold the rule to it.
CHAT = "alsa_output.usb-Astro_Gaming_Astro_A50-00.stereo-chat"
PROFILE_SET = "gearbench-astro-a50.conf"
SINK = "gearbench-a50-spatial"
OUTPUT = "gearbench-a50-spatial.output"
DESCRIPTION = "Astro A50 Spatial"
ENVIRONMENT = f"PULSE_SINK={SINK}"
LAUNCH_OPTION = f"{ENVIRONMENT} %command%"
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
PACKAGED = Path("/usr/share/gearbench/spatial")
TIMEOUT = 5.0          # any one program
BLOCK_TIMEOUT = 30.0   # a blocking start or restart (the CLI's)
#: How long set_room and the CLI's report after a start wait for a starting chain's sink,
#: and how often they look: systemctl start returns before PipeWire has built the sink.
SETTLE = 2.0
SETTLE_STEP = 0.2
#: set_room's failure when a starting chain's sink has not come within SETTLE.
STILL_STARTING = (f"{DESCRIPTION} was still starting after {SETTLE:g} s; "
                  "it keeps the room it started with until its next start.")
Run = Callable[..., subprocess.CompletedProcess]

SHOW = ["systemctl", "--user",
        "--property=Id,LoadState,ActiveState,SubState,UnitFileState,Result,MainPID",
        "show", UNIT, DEVICE_UNIT]
ENABLED_STATES = ("enabled", "enabled-runtime")
DUMP = ["pw-dump", "--no-colors"]
#: What to do about a missing unit file, in summary and under the CLI's systemctl error.
INSTALL_HINT = "run ./install.sh again, or install the Gearbench package"
#: Why a pw-dump whose objects _graph cannot read is refused.
UNKNOWN_SHAPE = "pw-dump printed objects of a shape Gearbench does not know"


class SpatialError(Exception):
    """A step that failed, in words for a toast or stderr."""


@dataclass(frozen=True)
class Choices:
    head: str = "ku100"
    room: str = "competitive"


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
    plugged: bool = True              # DEVICE_UNIT ActiveState == "active"
    tools: bool = True                # False: pw-dump not installed
    pipewire: str | None = None       # why pw-dump failed or did not parse
    sink_id: int | None = None
    output_id: int | None = None
    game: bool = True                 # the GAME node is there
    linked: bool = False              # a Link OUTPUT -> GAME, state not "error"/"unlinked"
    link_error: str | None = None
    running: bool = False             # the SINK node's info.state == "running"
    #: "outL:Gain 2" in SINK's Props params (else OUTPUT's); None while not known.
    room_gain: float | None = None
    profile_set: str | None = PROFILE_SET  # CARD's device.profile-set; None without the card
    streams: tuple[Stream, ...] = ()  # nodes linked into SINK, deduplicated, by id
    choices: Choices = Choices()


def choices_path() -> Path:
    return config.config_dir() / "spatial.json"


def load_choices(path: Path | None = None) -> Choices:
    """The saved head and room, each field falling back to its default on its own."""
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
    return Choices(head=head if head in HEADS else default.head,
                   room=room if room in ROOMS else default.room)


def _check(choices: Choices) -> None:
    if choices.head not in HEADS:
        raise ValueError(f"unknown head {choices.head!r}: one of {', '.join(HEADS)}")
    if choices.room not in ROOMS:
        raise ValueError(f"unknown room {choices.room!r}: one of {', '.join(ROOMS)}")


def save_choices(choices: Choices, path: Path | None = None) -> None:
    """The one write to the config folder outside the daemon."""
    _check(choices)
    target = choices_path() if path is None else path
    target.parent.mkdir(parents=True, exist_ok=True)
    config.write_json(target, {"head": choices.head, "room": choices.room}, ".spatial-")


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


def render(choices: Choices, data: Path | None = None) -> str:
    """The chain's conf for `choices`, from `data`/TEMPLATE (data_dir() by default)."""
    # PipeWire resolves a relative filename against its own working directory.
    base = (data_dir() if data is None else data).absolute()
    template = (base / TEMPLATE).read_text(encoding="utf-8")
    hrir = base / "sadie-ii" / HEAD_SUBJECTS[choices.head]
    room = base / ROOM_WAV
    for path in (hrir, room):
        # The paths go inside SPA-JSON strings, which " and \ would end or escape, and in
        # which PipeWire refuses a character below 0x20 ("Character not allowed").
        if re.search(r'["\\\x00-\x1f]', str(path)):
            raise SpatialError(f"{config.escaped(path)}: a path with \", \\ or a control "
                               "character such as a tab cannot go in the chain's conf; move "
                               "the checkout to a path without them and run ./install.sh again")
    for path in (*(hrir / name for name in HRIR_FILES), room):
        if not path.is_file():
            raise SpatialError(f"{path} is missing")
        channels, rate = wav_info(path)
        if (channels, rate) != (2, 48000):
            raise SpatialError(f"{path} is {config.counted(channels, 'channel')} "
                               f"at {rate} Hz, not 2 channels at 48000 Hz")
    fills = {"@HRIR@": str(hrir), "@ROOM@": str(room),
             "@ROOM_GAIN@": f"{ROOM_GAINS[choices.room]:.1f}"}
    # One pass over the template alone: a fill is never read again, so a folder named like
    # a placeholder stays in its path.
    for left in re.findall(r"@[A-Z_]+@", template):
        if left not in fills:
            raise SpatialError(f"{base / TEMPLATE} has {left}, which nothing fills")
    return re.sub(r"@[A-Z_]+@", lambda match: fills[match.group()], template)


def write_conf(target: Path) -> None:
    """Render the saved choices and replace `target` atomically, mode 0600."""
    # replace_file's mkstemp makes it 0600, and the replace keeps PipeWire from reading a
    # half-written conf. The conf is in the runtime folder, so its folder is not synced.
    config.replace_file(target, render(load_choices()).encode("utf-8"), f".{target.name}-", "")


def main(argv: list[str] | None = None) -> int:
    """`python3 -m gearbench.spatial render PATH`, the unit's ExecStartPre."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or args[0] != "render":
        print("usage: python3 -m gearbench.spatial render PATH", file=sys.stderr)
        return 2
    try:
        write_conf(Path(args[1]))
    except (SpatialError, OSError) as err:
        # An OSError's file is the template, a WAV or the conf's temporary: nothing else
        # here says which. One that names no file came from writing the conf, which is named.
        where = f"{err.filename or args[1]}: " if isinstance(err, OSError) else ""
        print(f"gearbench-spatial: {where}{config.reason(err)}", file=sys.stderr)
        return 1
    return 0


class _NotInstalled(SpatialError):
    pass


def _words(done: subprocess.CompletedProcess, program: str) -> str:
    """A program's stderr on one line, else how it ended."""
    return " ".join(done.stderr.split()) or config.ended(program, done.returncode)


def _call(run: Run, argv: list[str], timeout: float = TIMEOUT,
          check_exit: bool = True) -> subprocess.CompletedProcess:
    """Run one program; any failure is a SpatialError in words."""
    program = argv[0]
    try:
        # errors="replace": any PipeWire client names its own nodes, and one name that is not
        # UTF-8 would make all of pw-dump unreadable.
        done = run(argv, check=False, capture_output=True, encoding="utf-8",
                   errors="replace", timeout=timeout)
    except FileNotFoundError:
        raise _NotInstalled(f"{program} is not installed") from None
    except subprocess.TimeoutExpired:
        raise SpatialError(f"{program} took over {timeout:g} s") from None
    except OSError as err:
        raise SpatialError(f"{program} did not run: {config.reason(err)}") from None
    if check_exit and done.returncode != 0:
        raise SpatialError(_words(done, program))
    return done


def _dump(run: Run) -> list:
    try:
        objects = json.loads(_call(run, DUMP).stdout)
    except (ValueError, RecursionError):
        raise SpatialError("pw-dump printed something other than JSON") from None
    if not isinstance(objects, list):
        raise SpatialError("pw-dump printed something other than a list of objects")
    return objects


def read_state(run: Run = subprocess.run) -> State:
    """What systemd and PipeWire say now, and the saved choices. Never raises."""
    fields: dict = {"choices": load_choices()}
    unit = None
    try:
        units = parse_units(_call(run, SHOW).stdout)
    except SpatialError as err:
        fields["systemd"] = str(err)
    else:
        unit, device = (units + [{}, {}])[:2]
        fields.update(
            installed=unit.get("LoadState") != "not-found",
            enabled=unit.get("UnitFileState") in ENABLED_STATES,
            active=unit.get("ActiveState") or "inactive",
            result=unit.get("Result") or "success",
            plugged=device.get("ActiveState") == "active")
    try:
        fields.update(_graph(run, _chain_owner(unit)))
    except _NotInstalled:
        fields.update(tools=False, profile_set=None)
    except SpatialError as err:
        fields.update(pipewire=str(err), profile_set=None)
    return State(**fields)


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
            if key == name and config.number(value):
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
        if isinstance(fmt, dict) and config.whole(fmt.get("channels")):
            return fmt["channels"]
    channels = (info.get("props") or {}).get("audio.channels")
    return channels if config.whole(channels) else None


#: The longest stream name kept whole. The client sets it, and GTK takes seconds to wrap
#: a few thousand characters with no space, each time the Headset tab shows a new one.
NAME_MAX = 100


def _stream(info: dict, node_id: int) -> Stream:
    props = info.get("props") or {}
    name = (props.get("application.name") or props.get("node.description")
            or props.get("node.name") or f"node {node_id}")
    # The client names its stream, and the window shows the name unescaped: a newline
    # or a bidi override in it would break or turn the row.
    name = config.printable(str(name))
    if len(name) > NAME_MAX:
        name = name[:NAME_MAX - 1] + "…"
    return Stream(name, _channels(info))


def _chain_owner(unit: dict[str, str] | None) -> int | None:
    """The pid whose nodes graph_state takes for the chain's: the unit's main process,
    `pipewire -c`, or 0, matching nothing, while it has none or systemctl did not answer.
    None, matching by name alone, while the unit is neither enabled nor running: a chain run
    by hand, which summary calls manual."""
    if unit is None:
        return 0
    pid = _whole(unit.get("MainPID"))
    if pid:
        return pid
    return 0 if unit.get("UnitFileState") in ENABLED_STATES else None


def _whole(value: object) -> int | None:
    """A whole number from pw-dump or systemctl, as a JSON number or a string of digits,
    else None."""
    if config.whole(value):
        return value
    try:
        return int(value) if isinstance(value, str) and value.isdecimal() else None
    except ValueError:      # past int()'s 4300 digits
        return None


def _graph(run: Run, owner: int | None) -> dict:
    """graph_state of a fresh pw-dump; SpatialError on objects of a shape it
    cannot read."""
    objects = _dump(run)
    try:
        return graph_state(objects, owner)
    # OverflowError: float() of a whole number no float holds, in a control's place.
    # RecursionError: str() of a value nested deeper than it walks, which json.loads
    # still builds.
    except (AttributeError, TypeError, OverflowError, RecursionError):
        raise SpatialError(UNKNOWN_SHAPE) from None


def graph_state(objects: list, owner: int | None = None) -> dict:
    """State's pw-dump fields from pw-dump's objects.

    The chain's sink and output are the Audio/Sink and stream of their names made by the
    process `owner` (_chain_owner), by the pid PipeWire took from the owning client's socket,
    since any client can name a node gearbench-a50-spatial; with `owner` None the name and
    class decide. Game is the Audio/Sink of its name whose device.id is that of a Device
    named the A50 card. The client sets device.id, so this refuses a node of Game's name made
    for no A50 card, but not one whose client copied the card's id. The profile set is read
    from the first Device named the A50 card.
    """
    nodes: dict[int, dict] = {}
    clients: dict[int, int | None] = {}
    links: list[dict] = []
    card = None
    card_ids: set[int | None] = set()
    for obj in objects:
        if not isinstance(obj, dict) or not isinstance(obj.get("info"), dict):
            continue
        info = obj["info"]
        props = info.get("props") or {}
        if obj.get("type") == "PipeWire:Interface:Node" and config.whole(obj.get("id")):
            nodes[obj["id"]] = info
        elif obj.get("type") == "PipeWire:Interface:Client" and config.whole(obj.get("id")):
            clients[obj["id"]] = _whole(props.get("pipewire.sec.pid"))
        elif obj.get("type") == "PipeWire:Interface:Link":
            links.append(info)
        elif obj.get("type") == "PipeWire:Interface:Device" and props.get("device.name") == CARD:
            card = card or props
            card_ids.add(_whole(obj.get("id")))

    def first(name: str, media_class: str, made: Callable[[dict], bool]) -> int | None:
        for node_id, info in nodes.items():
            props = info.get("props") or {}
            if (props.get("node.name") == name and props.get("media.class") == media_class
                    and made(props)):
                return node_id
        return None

    def by_owner(props: dict) -> bool:
        # No client id finds no pid: dict.get takes a None key, and typeshed's does not.
        pid = clients.get(_whole(props.get("client.id")))  # type: ignore[arg-type]
        return owner is None or (owner > 0 and pid == owner)

    def by_card(props: dict) -> bool:
        device_id = _whole(props.get("device.id"))
        return device_id is not None and device_id in card_ids

    sink_id = first(SINK, "Audio/Sink", by_owner)
    output_id = first(OUTPUT, "Stream/Output/Audio", by_owner)
    game_id = first(GAME, "Audio/Sink", by_card)
    to_game = [link for link in links if output_id is not None and game_id is not None
               and link.get("output-node-id") == output_id
               and link.get("input-node-id") == game_id]
    linked = any(link.get("state") not in ("error", "unlinked") for link in to_game)
    link_error = None
    if not linked:
        link_error = next((str(link.get("error") or "error") for link in to_game
                           if link.get("state") == "error"), None)
    # No sink or output finds no node: dict.get takes a None key, and typeshed's does not.
    sink = nodes.get(sink_id, {})  # type: ignore[arg-type]
    room_gain = _room_gain(sink)
    if room_gain is None:
        room_gain = _room_gain(nodes.get(output_id, {}))  # type: ignore[arg-type]
    inputs = sorted(nodes.keys()
                    & {link.get("output-node-id") for link in links
                       if sink_id is not None and link.get("input-node-id") == sink_id})
    return {
        "sink_id": sink_id,
        "output_id": output_id,
        "game": game_id is not None,
        "linked": linked,
        "link_error": link_error,
        "running": sink.get("state") == "running",
        "room_gain": room_gain,
        "profile_set": _profile_set(card),
        "streams": tuple(_stream(nodes[node_id], node_id) for node_id in inputs),
    }


def _profile_set(card: dict | None) -> str | None:
    """The profile set of the A50 card's props, None without the card. ACP reads
    default.conf when the card names none. Text, as State has it: one that cannot be printed
    is refused with the rest (_graph), where describe would raise on it."""
    return None if card is None else str(card.get("device.profile-set") or "default.conf")


def _systemctl(*args: str) -> list[str]:
    """Options go before the verb: with POSIXLY_CORRECT set, systemctl's getopt
    stops at the verb and reads a later option as a unit name."""
    return ["systemctl", "--user", *args]


def _reset_failed(run: Run) -> None:
    """Clear a start-limit failure so the next start is not refused. Its exit
    status is not checked: systemd refuses it for a unit it has not loaded
    ("Unit ... not loaded.", systemd 259), which has nothing to clear."""
    _call(run, _systemctl("reset-failed", UNIT), check_exit=False)


def _no_block(verb: str, block: bool) -> tuple[list[str], float]:
    if block:
        return _systemctl(verb, UNIT), BLOCK_TIMEOUT
    return _systemctl("--no-block", verb, UNIT), TIMEOUT


def switch_on(run: Run = subprocess.run, block: bool = False) -> bool:
    """Enable the unit, and start it if the base station is plugged in.
    True if it started, False if it waits for the base station."""
    _call(run, _systemctl("enable", UNIT))
    _reset_failed(run)
    units = parse_units(_call(run, SHOW).stdout)
    if len(units) < 2 or units[1].get("ActiveState") != "active":
        return False
    _call(run, *_no_block("start", block))
    return True


def switch_off(run: Run = subprocess.run) -> None:
    _call(run, _systemctl("--now", "disable", UNIT))


def set_head(head: str, run: Run = subprocess.run, block: bool = False) -> None:
    """Save the head and restart the unit if it runs; its ExecStartPre renders the head."""
    save_choices(replace(load_choices(), head=head))
    _reset_failed(run)
    _call(run, *_no_block("try-restart", block))


def _unit(run: Run) -> dict[str, str] | None:
    """The unit as `systemctl show` gives it; None when systemctl fails,
    which read_state reports."""
    try:
        units = parse_units(_call(run, SHOW).stdout)
    except SpatialError:
        return None
    return units[0] if units else {}


def _starting(unit: dict[str, str] | None) -> bool:
    """systemd says the unit is active or activating, other than waiting out
    RestartSec: that start's ExecStartPre has yet to read the saved room. A
    systemctl that failed says neither."""
    unit = unit or {}
    return (unit.get("ActiveState") in ("active", "activating")
            and unit.get("SubState") not in ("auto-restart", "auto-restart-queued"))


def set_room(room: str, run: Run = subprocess.run) -> bool:
    """Save the room and set it live on a running sink, waiting up to SETTLE
    for the sink of a unit that is starting. False: the unit is not running,
    or its chain went away during the set, so the room applies at its next
    start."""
    save_choices(replace(load_choices(), room=room))
    unit = _unit(run)
    owner = _chain_owner(unit)
    # Owner 0 matches no node: with no start under way there is no chain to set.
    if owner == 0 and not _starting(unit):
        return False
    sink_id = _graph(run, owner)["sink_id"]
    # Its ExecStartPre may have rendered the conf before the save.
    deadline = time.monotonic() + SETTLE
    while sink_id is None:
        # No start under way, or one that failed in the wait: no chain to set.
        if not _starting(unit):
            return False
        if time.monotonic() >= deadline:
            raise SpatialError(STILL_STARTING)
        time.sleep(SETTLE_STEP)
        unit = _unit(run)
        owner = _chain_owner(unit)
        sink_id = _graph(run, owner)["sink_id"]
    # pw-cli 1.6 exits 0 when its command fails, so the read-back decides and
    # pw-cli's words only name the cause.
    done = _call(run, ["pw-cli", "set-param", str(sink_id), "Props", room_props(room)],
                 check_exit=False)
    words = _words(done, "pw-cli") if done.returncode or "Error:" in done.stderr else ""
    graph = _graph(run, owner)
    if graph["sink_id"] is None:
        return False
    gain = graph["room_gain"]
    # An absent gain, with no Props or on a chain that has not played yet,
    # is not known, and without an error from pw-cli not a failure.
    if _room_of(gain) == room or (gain is None and not words):
        return True
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
            parts.append(config.counted(stream.channels, "channel"))
        if count > 1:
            parts.append(f"{count} streams")
        words.append(", ".join(parts))
    return "; ".join(words)


#: The playing kind's words: summary's go on to list the streams, describe's stop here.
IN_USE = "in use"


def summary(state: State) -> tuple[str, str]:
    """(kind, words) for `state`: the first condition below that holds."""
    if state.systemd:
        return "no_manager", f"systemctl --user did not answer: {state.systemd}"
    if not state.installed:
        return "not_installed", f"not installed: {INSTALL_HINT}"
    if not state.enabled:
        if state.sink_id is not None:
            return "manual", f"off, but an {DESCRIPTION} runs that Gearbench did not start"
        return "off", "off"
    if state.active == "failed":
        return "failed", f"did not start: journalctl --user _SYSTEMD_USER_UNIT={UNIT} says why"
    if state.active in ("activating", "reloading"):
        return "starting", "starting"
    if not state.plugged:
        return "waiting", "on; starts when the base station is plugged in"
    if state.active != "active":
        return "stopped", "on, but stopped: switch it off and on again"
    if not state.tools:
        return "no_tools", "on, but PipeWire's pw-dump is not installed"
    if state.pipewire:
        return "pipewire", f"on, but PipeWire could not be read: {state.pipewire}"
    if state.sink_id is None:
        return "starting", "starting"
    if not state.linked:
        words = "on, but not linked to Astro A50 Game"
        return "unlinked", f"{words}: {state.link_error}" if state.link_error else words
    if state.streams:
        return "playing", f"{IN_USE}: {_streams_words(state.streams)}"
    return "ready", "on, linked to Astro A50 Game"


def stereo_hint(state: State) -> str | None:
    """What to do about a game that sends stereo into the 7.1 sink."""
    for stream in state.streams:
        if stream.channels == 2:
            return (f"{stream.name} sends 2 channels: set its audio output to surround "
                    "or System Default, and start it again")
    return None


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
        ("head", f"{HEAD_NAMES[head]} (SADIE II {HEAD_SUBJECTS[head]})"),
        ("room", f"{room}; running: {running}" if running not in (None, room) else room),
    ]
    if state.streams:
        rows.append(("playing", _streams_words(state.streams)))
    hint = stereo_hint(state)
    if hint:
        rows.append(("note", hint))
    rows.append(("launch option", LAUNCH_OPTION))
    if state.profile_set is not None:
        rows.append(("outputs", "Astro A50 Game and Chat apart" if state.profile_set == PROFILE_SET
                     else f"one entry ({state.profile_set}); restart WirePlumber: "
                          "systemctl --user restart wireplumber"))
    return rows


if __name__ == "__main__":
    sys.exit(main())
