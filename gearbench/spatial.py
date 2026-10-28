"""Spatial sound: its configuration, its user service, and what PipeWire says of it.

Gearbench Spatial is one 7.1 sink, rendered for headphones on the headset it plays to: the
Astro A50's or the Astro A50 X's Game output, the own output of a headset of the Logitech
G432's family (G432_FAMILY), or the first output of an A50 Gen 5, a PRO X 2, a G522 or a G325;
for a headset whose equalizer gearbench.g432_eq runs, that equalizer's sink in place of its
output while the sink its unit's process made is in PipeWire (spatial_unit.target).
gearbench-spatial.service's process (gearbench.spatial_unit) follows PipeWire and runs the
filter chain, rendered from gearbench-spatial.conf.in and spatial.json's head, room and
headset, while a headset is there, and renders and starts it again when the headset or the
node it plays to changes; on and off is the unit's enable link, stored nowhere else. A
`Headset` holds how a headset's card and output are found and the words that name them, and
a `Target` where the chain plays. Standard library only, never run in the daemon. systemd
and PipeWire are run and read through gearbench.pipewire_client, each program through a
`run` argument.
"""

from __future__ import annotations

import re
import struct
import subprocess
import time
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from . import pipewire_client, settings_files, wording
from .g432 import MODELS as G432_MODELS
from .hidpp import VENDOR_ID

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


def usb_ids(ids: Iterable[int]) -> tuple[str, ...]:
    """USB ids in the form PipeWire 1.6.2 printed the A50's card's on 2026-09-27, `0x` and
    four lowercase digits."""
    return tuple(f"0x{usb_id:04x}" for usb_id in ids)


#: Logitech's vendor id as a card's props in PipeWire give it: with a product id, how each
#: headset but the A50 is found.
(G432_VENDOR_ID,) = usb_ids((VENDOR_ID,))
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
BLOCK_TIMEOUT = 30.0   # a blocking start or restart (the CLI's)
#: set_room's failure when a starting chain's sink has not come within
#: pipewire_client.SETTLE.
STILL_STARTING = (f"{DESCRIPTION} was still starting after {pipewire_client.SETTLE:g} s; "
                  "it keeps the room it started with until its next start")

SHOW = ["systemctl", "--user",
        "--property=Id,LoadState,ActiveState,SubState,UnitFileState,Result,MainPID,StatusText",
        "show", UNIT]
ENABLED_STATES = ("enabled", "enabled-runtime")
#: What to do about a missing unit file, in summary and under the CLI's systemctl error.
INSTALL_HINT = "run `./install.sh` again, or install the Gearbench package"
#: What to do about a card whose outputs are joined, in describe's outputs row.
JOINED_HINT = "restart WirePlumber: `systemctl --user restart wireplumber`"
#: Where another profile set comes from on a card no rule of Gearbench's sets, in place of
#: JOINED_HINT, which would change nothing there.
OUTSIDE_RULE = "from a rule outside Gearbench"


@dataclass(frozen=True)
class Choices:
    head: str = "ku100"
    room: str = "competitive"
    #: The headset picked to play to, a key of HEADSET_KEYED; None for none picked, which
    #: plays to the one connected first (pick).
    headset: str | None = None


@dataclass(frozen=True)
class Headset:
    """A headset the chain plays to: its key, its name, the props its card's Device has in
    PipeWire, each with the values it may take, the node it plays to there (None: the card's
    first Audio/Sink), and the words for it."""

    key: str
    name: str
    card: tuple[tuple[str, tuple[str, ...]], ...]
    game: str | None
    #: The output as the conf's first comment names it.
    plays_on: str
    #: The output as the summary says the chain is not linked to it, and as it says the
    #: chain waits for it.
    linked_to: str
    waited_for: str
    #: The card's profile set that gives it the output the chain needs, and describe's
    #: outputs row for it, and for another, named at {}, before JOINED_HINT or OUTSIDE_RULE.
    profile_set: str
    outputs_apart: str
    outputs_joined: str
    #: The window's outputs row while the card has another profile set, in the words of the
    #: outputs GNOME lists.
    joined_shown: str
    #: Whether a rule of Gearbench's gives the card profile_set, so that restarting
    #: WirePlumber puts it in place (JOINED_HINT); else another set is OUTSIDE_RULE's.
    ruled: bool = True


@dataclass(frozen=True)
class Target:
    """Where the chain plays: the headset, the node its output is aimed at, and the words
    the conf's comments name that node by."""

    headset: Headset
    node: str
    words: str


A50 = Headset(
    key="a50", name="Astro A50 Gen 4", card=(("device.name", (CARD,)),), game=GAME,
    plays_on="the Astro A50 Gen 4's Game output", linked_to="Astro A50 Game",
    waited_for="Astro A50 Game", profile_set=PROFILE_SET,
    outputs_apart="Astro A50 Game and Chat apart", outputs_joined="one entry ({})",
    joined_shown="Astro A50 Game and Chat show as one output")


def logitech(key: str, name: str, products: tuple[str, ...]) -> Headset:
    """A headset of the G432's family, found by Logitech's vendor id and any of `products`,
    its own output its card's first Audio/Sink. The profile set its WirePlumber rule gives
    it, PipeWire's analog-only.conf, and the outputs that set leaves and the one it takes
    away are the G432's, as the hardware gave them on 2026-10-17; on the others they are
    assumed (wireplumber/60-gearbench-hidpp-headsets.conf)."""
    return Headset(
        key=key, name=name,
        card=(("device.vendor.id", (G432_VENDOR_ID,)), ("device.product.id", products)),
        game=None, plays_on=f"the {name}'s own output", linked_to=f"the {name}",
        waited_for="a Logitech G headset's output", profile_set="analog-only.conf",
        outputs_apart="one analog output", outputs_joined="an analog and a digital output ({})",
        joined_shown=f"The {name} shows an analog and a digital output")


#: The G432's family, gearbench.g432.MODELS in their order: each by its key, its short name
#: and any of its USB ids.
G432_FAMILY = tuple(logitech(model.key, model.short, usb_ids(model.products))
                    for model in G432_MODELS)
#: The A50 X's card and those first_output makes, by Logitech's id (G432_VENDOR_ID) and each
#: one's own, a50x.MODELS' in the G432's form, copied: importing a50x would load its protocol
#: modules into the spatial unit's process. The A50 X has the A50 Gen 4's layout, Voice on
#: PCM device 0 and Game on 1, and PipeWire's default.conf gives it Voice alone (pw-dump,
#: 2026-10-21): its WirePlumber rule gives it Gearbench's profile set, A50_X_PROFILE_SET, whose
#: stereo-game is device 1, and it plays to that, its card's device.name,
#: alsa_card.usb-Logitech_A50_X-00, naming the node (pw-dump, 2026-10-22).
A50_X_GAME = "alsa_output.usb-Logitech_A50_X-00.stereo-game"
#: Hard-coded in the A50 X's WirePlumber rule; the tests hold the rule to it.
A50_X_CHAT = "alsa_output.usb-Logitech_A50_X-00.stereo-chat"
A50_X_PROFILE_SET = "gearbench-astro-a50x.conf"
A50_X = Headset(
    key="a50x", name="Astro A50 X",
    card=(("device.vendor.id", (G432_VENDOR_ID,)), ("device.product.id", ("0x0b0b",))),
    game=A50_X_GAME, plays_on="the Astro A50 X's Game output", linked_to="A50 X Game",
    waited_for="a Logitech G headset's output", profile_set=A50_X_PROFILE_SET,
    outputs_apart="A50 X Game and Chat apart", outputs_joined="Game not listed ({})",
    joined_shown="The A50 X's Game output is not listed")


def first_output(key: str, name: str, short: str, products: tuple[str, ...]) -> Headset:
    """A headset not read off one, so assumed: it plays to its card's first output with a
    name, found by Logitech's vendor id and any of `products`, under PipeWire's
    default.conf, until a first connect reads both; Gearbench ships no rule for its card.
    `short` is the shorter name its summary and window words use."""
    return Headset(
        key=key, name=name,
        card=(("device.vendor.id", (G432_VENDOR_ID,)), ("device.product.id", products)),
        game=None, plays_on=f"the {name}'s first output", linked_to=f"the {short}",
        waited_for="a Logitech G headset's output", profile_set="default.conf",
        outputs_apart="the outputs PipeWire gives it", outputs_joined="another profile set ({})",
        joined_shown=f"The {short} lists other outputs", ruled=False)


#: The A50 Gen 5's first output taken to be its Game output, which G HUB's device list names
#: "A50 Game".
A50_GEN5 = first_output("a50gen5", "Astro A50 Gen 5", "A50 Gen 5", ("0x0b1c",))
#: The PRO X 2 LIGHTSPEED's receiver's one output (G HUB lists one audio interface for it).
PRO_X_2 = first_output("pro_x_2", "PRO X 2 LIGHTSPEED", "PRO X 2", ("0x0af7",))
#: The G522 LIGHTSPEED's, through its receiver or on its cable, and the G325 LIGHTSPEED's
#: (G HUB lists one audio interface for each of their ids).
G522 = first_output("g522", "G522 LIGHTSPEED", "G522", ("0x0b18", "0x0b19"))
G325 = first_output("g325", "G325 LIGHTSPEED", "G325", ("0x0b21",))
#: In the order the window's picker and `headset-spatial --headset` list them.
HEADSETS = (
    A50, *G432_FAMILY, A50_X, A50_GEN5, PRO_X_2, G522, G325)
#: The headsets the chain plays to, by the key spatial.json keeps the one picked under,
#: which is gearbench.g432.MODELS' for the G432's family.
HEADSET_KEYED = {headset.key: headset for headset in HEADSETS}
#: The words for no headset picked, which plays to the one connected first (pick).
FIRST_CONNECTED = "the one connected first"


def headset_of(key: object) -> Headset | None:
    """The headset of HEADSETS whose key is `key`, a value pw-dump or State holds; None for
    none."""
    return next((headset for headset in HEADSETS if headset.key == key), None)


@dataclass(frozen=True)
class Stream:
    name: str               # first of application.name, node.description, node.name, "node <id>"
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
        raise pipewire_client.StepError(f"{path} is not a WAV file")
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
        raise pipewire_client.StepError(f"{path} has no fmt or data chunk")
    return fmt[1], fmt[2]


def render(choices: Choices, target: Target, data: Path | None = None) -> str:
    """The chain's conf for `choices`, from `data`/TEMPLATE (data_dir() by default), aimed
    at `target`."""
    # A G432's node name comes from pw-dump.
    if not target.node or pipewire_client.NOT_IN_CONF.search(target.node):
        raise pipewire_client.StepError(f"the output '{wording.escaped(target.node)}' cannot "
                                        "be named in the chain's conf")
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
            raise pipewire_client.StepError(f"{path} is missing")
        channels, rate = wav_info(path)
        if (channels, rate) != (2, 48000):
            raise pipewire_client.StepError(f"{path} is {wording.counted(channels, 'channel')} "
                                            f"at {rate} Hz, not 2 channels at 48000 Hz")
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
    """What systemd and PipeWire say now, and the saved choices. Never raises."""
    fields: dict = {"choices": load_choices()}
    unit = None
    try:
        unit = pipewire_client.unit_of(run, SHOW)
    except pipewire_client.StepError as err:
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
    except pipewire_client.NotInstalled:
        fields.update(tools=False)
    except pipewire_client.StepError as err:
        fields.update(pipewire=str(err))
    return State(**fields)


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
    pid = pipewire_client.main_pid(unit)
    if pid:
        return pid
    return 0 if unit.get("UnitFileState") in ENABLED_STATES else None


def _graph(run: pipewire_client.Run, owner: int | None) -> dict:
    """graph_state of a fresh pw-dump (pipewire_client.dumped)."""
    return pipewire_client.dumped(run, lambda objects: graph_state(objects, owner))


def card_of(headset: Headset, objects: Sequence[object]) -> tuple[dict, list[dict], int] | None:
    """`headset`'s card in pw-dump's objects: of the Devices with each of its card's props
    (pipewire_client.card_nodes), the one that came last among those with its output
    (output_of), else the one that came last, with that card's own nodes and when it came,
    its object.serial, which PipeWire counts up, else its id; None without one. A headset
    found by two ids, as the G522 by its receiver's and its cable's, so plays on the one
    plugged in last (the owner's decision of 2026-10-22)."""
    cards, nodes = pipewire_client.card_nodes(objects, headset.card)
    if not cards:
        return None

    def came(card: int) -> int:
        serial = pipewire_client.as_whole(cards[card].get("object.serial"))
        return card if serial is None else serial

    def own(card: int) -> list[dict]:
        return [props for props in nodes
                if pipewire_client.as_whole(props.get("device.id")) == card]

    card = max(cards, key=lambda found: (output_of(headset, own(found)) is not None, came(found)))
    return cards[card], own(card), came(card)


def output_of(headset: Headset, nodes: Sequence[dict]) -> str | None:
    """`headset`'s output among its card's `nodes` (pipewire_client.card_nodes): the
    Audio/Sink named its game, or for a headset with no game the first with a name; None
    while there is none."""
    for props in nodes:
        name = props.get("node.name")
        if (props.get("media.class") == "Audio/Sink" and isinstance(name, str)
                and (headset.game is None or name == headset.game)):
            return name
    return None


def present(objects: Sequence[object]) -> list[tuple[Headset, str]]:
    """The headsets whose output is in pw-dump's objects, each with that output's name, in
    the order their cards came (card_of): the one connected first, first."""
    found = []
    for headset in HEADSETS:
        card = card_of(headset, objects)
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
    took from the owning client's socket (pipewire_client.node_pids) and that process's
    parent (pipewire_client.parent_of), since any client can name a node gearbench-spatial;
    with `owner` None the name and class decide. The headset and the node it plays to are
    the ones its sink's props name (TARGET_PROPS), and the profile set is that headset's
    card's.
    """
    found_nodes = pipewire_client.of_type(objects, "Node")
    nodes = {node: info for node, info, _props in found_nodes}
    pids = {node: pid for node, _info, _props, pid in pipewire_client.node_pids(objects)}
    links = [info for obj in objects if isinstance(obj, dict)
             and obj.get("type") == "PipeWire:Interface:Link"
             and isinstance(info := obj.get("info"), dict)]

    def first(name: str, media_class: str) -> int | None:
        # The name and the class first: pipewire_client.parent_of reads /proc for the nodes
        # that have both.
        return next((node for node, _info, props in found_nodes
                     if props.get("node.name") == name and props.get("media.class") == media_class
                     and (owner is None
                          or (owner > 0 and node in pids
                              and pipewire_client.parent_of(pids[node]) == owner))),
                    None)

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
        "stranded": stranded(objects),
        "streams": tuple(_stream(nodes[node_id], node_id) for node_id in inputs),
        "stale": tuple(dict.fromkeys(
            _stream_name(props, node) for node, _info, props in found_nodes
            if props.get("target.object") == RETIRED_SINK)),
    }


def _profile_set(headset: Headset, objects: Sequence[object]) -> str | None:
    """The profile set of `headset`'s card (_card_set), None without the card."""
    card = card_of(headset, objects)
    return None if card is None else _card_set(card[0])


def _card_set(props: dict) -> str:
    """A card's profile set, by its Device's props: ACP reads default.conf when the card
    names none. Text, as State has it: one that cannot be printed is refused with the rest
    (_graph), where describe would raise on it."""
    return str(props.get("device.profile-set") or "default.conf")


def stranded(objects: Sequence[object]) -> tuple[tuple[str, str], ...]:
    """The headsets with a named output, their `game`, whose card is in pw-dump's objects
    without it, each with its card's profile set: plugged in, with nothing the chain can
    play to, as an A50 X whose card fell back to PipeWire's default.conf."""
    found = []
    for headset in HEADSETS:
        card = card_of(headset, objects) if headset.game is not None else None
        if card is not None and output_of(headset, card[1]) is None:
            found.append((headset.key, _card_set(card[0])))
    return tuple(found)


def stranded_headset(state: State) -> tuple[Headset, str] | None:
    """While no headset is there to play to, the first of those plugged in with no output
    for the chain on another profile set than the one that gives it that output
    (Headset.profile_set), in HEADSETS' order, with that set; None for none. A card on its
    own set lacks the output only under a profile picked without it, as the A50's with Chat
    alone or Off, which restarting WirePlumber does not change (the owner's decision of
    2026-10-26)."""
    if state.present:
        return None
    return next(((HEADSET_KEYED[key], profile_set) for key, profile_set in state.stranded
                 if profile_set != HEADSET_KEYED[key].profile_set), None)


def joined_headset(state: State) -> tuple[Headset, str] | None:
    """The headset whose card is on another profile set than the one that gives the chain
    its output (Headset.profile_set), with that set: the one the chain plays to, where its
    card is on another, else stranded_headset's; None for none."""
    on = headset_of(state.headset)
    if on is not None and state.profile_set is not None and state.profile_set != on.profile_set:
        return on, state.profile_set
    return stranded_headset(state)


def _systemctl(*args: str) -> list[str]:
    """Options go before the verb: with POSIXLY_CORRECT set, systemctl's getopt
    stops at the verb and reads a later option as a unit name."""
    return ["systemctl", "--user", *args]


def _reset_failed(run: pipewire_client.Run) -> None:
    """Zero the unit's start and restart counters and clear a failed state (systemctl(1)),
    so the next start, a try-restart's included, is not refused by the start limit. Its exit
    status is not checked: systemd refuses it for a unit it has not loaded ("Unit ... not
    loaded.", systemd 259), which has nothing to clear."""
    pipewire_client.run_program(run, _systemctl("reset-failed", UNIT), check_exit=False)


def _no_block(verb: str, block: bool) -> tuple[list[str], float]:
    if block:
        return _systemctl(verb, UNIT), BLOCK_TIMEOUT
    return _systemctl("--no-block", verb, UNIT), pipewire_client.TIMEOUT


def switch_on(run: pipewire_client.Run = subprocess.run, block: bool = False) -> None:
    """Enable the unit and start it; its process runs the chain while a headset is there."""
    pipewire_client.run_program(run, _systemctl("enable", UNIT))
    _reset_failed(run)
    pipewire_client.run_program(run, *_no_block("start", block))


def switch_off(run: pipewire_client.Run = subprocess.run) -> None:
    pipewire_client.run_program(run, _systemctl("--now", "disable", UNIT))


def restart(run: pipewire_client.Run = subprocess.run, block: bool = False) -> None:
    """Restart the unit if it runs, so its process renders the chain from what is saved. A
    failed unit is left failed for summary to report: try-restart does nothing for a unit
    that does not run, and reset-failed would only erase the failure."""
    if (_unit(run) or {}).get("ActiveState") == "failed":
        return
    _reset_failed(run)
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


#: The states this unit and the G432 family's equalizer and noise suppression units give in
#: the same words (pipewire_unit.unit_summary), `{}` the failure's own: systemctl not
#: answering, pw-dump not installed, and pw-dump failing.
NO_MANAGER = "systemctl --user did not answer: {}"
NO_TOOLS = "on, but PipeWire's pw-dump is not installed"
PIPEWIRE_NOT_READ = "on, but PipeWire could not be read: {}"


def summary(state: State) -> tuple[str, str]:
    """(kind, words) for `state`: the first condition below that holds, those of a unit that
    runs with no sink in _without_sink."""
    if state.systemd:
        return "no_manager", NO_MANAGER.format(state.systemd)
    if not state.installed:
        return "not_installed", f"not installed: {INSTALL_HINT}"
    if not state.enabled:
        if state.sink_id is not None:
            return "manual", f"off, but a {DESCRIPTION} runs that Gearbench did not start"
        return "off", "off"
    if state.active == "failed":
        return "failed", f"did not start: `journalctl --user _SYSTEMD_USER_UNIT={UNIT}` says why"
    if state.active in ("activating", "reloading"):
        return "starting", "starting"
    if state.active != "active":
        return "stopped", "on, but stopped: switch it off and on again"
    if not state.tools:
        return "no_tools", NO_TOOLS
    if state.pipewire:
        return "pipewire", PIPEWIRE_NOT_READ.format(state.pipewire)
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
    return (f"{wording.word_list(state.stale, 'and')} {'asks' if one else 'ask'} for "
            f"{RETIRED_SINK}, an old name, so {'it plays' if one else 'they play'} on the "
            f"default output: change {'its' if one else 'their'} launch option to "
            f"{LAUNCH_OPTION}")


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
        rows.append(("outputs", f"{words}; {JOINED_HINT}" if headset.ruled
                     else f"{words}, {OUTSIDE_RULE}"))
    elif played is not None:
        rows.append(("outputs", played.outputs_apart))
    return rows
