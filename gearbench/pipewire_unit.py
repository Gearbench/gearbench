"""A transient user unit of Gearbench's: its command line and its stop, the equalizer's
watch's included; for one that runs `pipewire -c` on a conf of Gearbench's, its state as
systemd and PipeWire give it, the wait after its start and the runtime folder a switch's
OSError comes from (switch_folder); and the headset's own nodes in pw-dump, which each
chain's conf aims at and the input meter (input_meter) records from, with the volume of such
a node where pipewire-pulse and GNOME keep it: its card's Route or its own Props (route_of,
props_volumes), their pw-cli sets (route_argv, sink_argv), and GNOME's percent
(gnome_percent). Standard library only; every outside program goes through a `run` argument
and `pipewire_client.run_program`, and a failure is a `pipewire_client.StepError` in words.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from . import pipewire_client, runtime_folder, spatial, wording
from .g432 import HeadsetModel

#: systemctl's exit status for a stop of a unit not loaded, as a transient unit is once it
#: has stopped: off already. systemd's EXIT_NOTINSTALLED, from its documented exit codes,
#: not yet seen from this stop.
NOT_LOADED = 5
#: PipeWire's filter-chain.service's hardening, which the spatial unit carries too.
HARDENING = ("LockPersonality=yes", "MemoryDenyWriteExecute=yes", "NoNewPrivileges=yes",
             "RestrictNamespaces=yes", "SystemCallArchitectures=native",
             "SystemCallFilter=@system-service")
#: The ActiveStates of a unit that runs or starts.
RUNS_OR_STARTS = ("active", "activating")
#: A switch-on's refusal when its unit stopped as it started, by the unit's description
#: and its name.
STOPPED = "{} stopped as it started: `journalctl --user -u {}` says why"
#: The media class of a headset's microphone in PipeWire, an input of its card.
MIC_CLASS = "Audio/Source"
#: The A50 X's Stream Out, the stream mix's capture: an input of its card beside the
#: microphone, the profile set's mapping of this name
#: (alsa-card-profile/gearbench-astro-a50x.conf), which names the node's device.profile.name.
STREAM_OUT = "stereo-stream"
#: GNOME's 100 %, a volume of 1.0.
GNOME_FULL = 100


@dataclass(frozen=True)
class UnitState:
    """What systemd and pw-dump say of the unit: the fields unit_summary reads, before those
    of the conf's own nodes, which each unit's State adds."""

    systemd: str | None = None        # why `systemctl --user show` failed
    active: str = "inactive"          # the unit's ActiveState
    tools: bool = True                # False: pw-dump not installed
    pipewire: str | None = None       # why pw-dump failed or did not parse


def transient_argv(unit: str, description: str, bound_to: str, *, after: str) -> list[str]:
    """systemd-run's command line, up to the command, for a transient user unit of
    Gearbench's, `unit`: stopped with `bound_to` and started after `after`, in the session's
    slice, with HARDENING. --collect unloads it once it stops, failed or not, so the next
    start can make it again."""
    return ["systemd-run", "--user", f"--unit={unit}", f"--description={description}",
            "--collect", "--quiet", "--slice=session.slice",
            f"--property=BindsTo={bound_to}", f"--property=After={after}",
            *(f"--property={setting}" for setting in HARDENING)]


def pipewire_argv(unit: str, description: str, conf: Path) -> list[str]:
    """systemd-run's command line for a transient unit, `unit`, that runs `pipewire -c` on
    `conf` (transient_argv)."""
    # A client of the session's PipeWire, stopped with it.
    return [*transient_argv(unit, description, "pipewire.service",
                            after="pipewire.service pipewire-session-manager.service"),
            # Each $ doubled: the user manager expands ${NAME} in the command line, an unset
            # one to nothing, and reads $$ as $ (systemd.service(5), "Command lines"), so
            # PipeWire would read a conf in a folder private_runtime never checked.
            pipewire_client.PIPEWIRE, "-c", str(conf).replace("$", "$$")]


def unit_of(run: pipewire_client.Run, unit: str) -> dict[str, str]:
    """`unit` as `systemctl --user show` gives it; StepError when systemctl fails."""
    return pipewire_client.unit_of(run, ["systemctl", "--user",
                                         "--property=Id,LoadState,ActiveState,SubState,MainPID",
                                         "show", unit])


def running(run: pipewire_client.Run, unit: str) -> bool:
    """Whether `unit` runs or starts (RUNS_OR_STARTS); StepError when systemctl fails."""
    return unit_of(run, unit).get("ActiveState") in RUNS_OR_STARTS


def stopped(run: pipewire_client.Run, unit: str) -> bool:
    """Whether `unit` stopped as it started: a transient unit whose process exited at start
    is collected (--collect) and reads inactive, or failed (systemd.unit(5)'s CollectMode),
    not yet seen with either unit. A systemctl that does not answer is no stop known."""
    try:
        return not running(run, unit)
    except pipewire_client.StepError:
        return False


def settle_start[S](run: pipewire_client.Run, unit: str, read_state: Callable[[], S],
                    summary: Callable[[S], tuple[str, str]],
                    refusal: pipewire_client.StepError) -> S:
    """The state `unit`, just started by systemd-run, settles on (pipewire_client.settled,
    which reads it again while it starts); `refusal` raised once it has, where the unit
    stopped as it started (`stopped`). The look waits for the start: a transient unit whose
    process exits at once reads ActiveState=active for under 50 ms after systemd-run
    returns, and inactive from 50 ms on (hardware round 4, 2026-10-20)."""
    state = pipewire_client.settled(read_state, summary, True)
    if stopped(run, unit):
        raise refusal
    return state


def g432_cards(objects: Sequence[object], *, model: HeadsetModel) -> set[int]:
    """The ids of the headset's card Devices in pw-dump's objects, on any profile
    (pipewire_client.card_nodes)."""
    return set(pipewire_client.card_nodes(objects, spatial.HEADSET_KEYED[model.key].card)[0])


def aimed_at(objects: Sequence[object], pid: int, stream: str) -> str | None:
    """The node the stream `stream` of the chain whose process is `pid` aims at, by name: its
    target.object, the one its conf was rendered with; None while there is no such stream,
    or for 0, no process (pipewire_client.node_pids)."""
    return next((target
                 for _node, _info, props, made_by in pipewire_client.node_pids(objects)
                 if made_by == pid and props.get("node.name") == stream
                 and isinstance(target := props.get("target.object"), str)), None)


def g432_node(objects: Sequence[object], media_class: str = "Audio/Sink", *,
              model: HeadsetModel, aimed: str | None = None) -> dict | None:
    """headset_node of `model`, a model of the G432's family."""
    return headset_node(objects, spatial.HEADSET_KEYED[model.key], media_class, aimed=aimed)


def headset_node(objects: Sequence[object], headset: spatial.Headset, media_class: str, *,
                 aimed: str | None = None) -> dict | None:
    """The props of `headset`'s node of `media_class` in pw-dump's objects: with `aimed`, a
    node's name, such as a running chain's (aimed_at), the node of that name among its cards'
    nodes (pipewire_client.card_nodes), so a second headset of the model plugged in meanwhile
    is never taken for it; else the first with a name of the nodes of the card Gearbench
    Spatial plays to (spatial.card_of), the A50 X's Stream Out left out. None while there is
    none."""
    if aimed is None:
        card = spatial.card_of(headset, objects)
        nodes = [] if card is None else card[1]
    else:
        nodes = pipewire_client.card_nodes(objects, headset.card)[1]
    return next((props for props in nodes if props.get("media.class") == media_class
                 and props.get("device.profile.name") != STREAM_OUT
                 and isinstance(name := props.get("node.name"), str)
                 and (aimed is None or name == aimed)), None)


def mic_of(objects: Sequence[object], headset: spatial.Headset) -> str | None:
    """The node name of `headset`'s microphone in pw-dump's objects, headset_node's input;
    None while there is none."""
    props = headset_node(objects, headset, MIC_CLASS)
    return None if props is None else props["node.name"]


def node_id(objects: Sequence[object], props: dict | None) -> int | None:
    """The id of the Node in pw-dump's objects whose props are `props`, as g432_node gives
    them: by those props, not their node.name, which any client can give a node of its
    own; None for None."""
    return next((node for node, _info, found in pipewire_client.of_type(objects, "Node")
                 if found is props), None)


@dataclass(frozen=True)
class Route:
    """A node of a headset's card, its output or its microphone, as the card's Route param
    gives it: the card Device's id, the route's index and device, each channel's volume,
    volumeBase, the volume at which the adapter's own is at 0 dB: PipeWire's ACP sets the
    hardware volume to the volume over volumeBase, which it takes from the mixer path's
    highest dB (acp.c's mixer_volume_init and set_volume, read 2026-10-17), and whether it
    is muted."""

    card: int
    index: int
    device: int
    volumes: tuple[float, ...]
    base: float
    mute: bool


def levels(value: object) -> tuple[float, ...] | None:
    """`value` as volumes, one a channel, if it is a list of finite numbers of 0 or more, at
    least one; else None."""
    # json.loads reads 1e999 and Infinity as an infinity, and a whole number past the largest
    # float raises OverflowError at float(); NaN fails each comparison.
    if not (isinstance(value, list) and value
            and all(wording.number(v) and 0 <= v <= sys.float_info.max for v in value)):
        return None
    return tuple(float(v) for v in value)


def _route(route: object, card: int, device: object, direction: str) -> Route | None:
    """`route`, one of the card's Route params, as a Route if it is the node's: its
    direction `direction`, its device the node's card.profile.device, a whole index, and a
    volume of 0 or more for each channel and a volumeBase above 0 in its props, whose mute
    is true only for a JSON true."""
    if (not isinstance(route, dict) or route.get("direction") != direction
            or not isinstance(values := route.get("props"), dict)):
        return None
    volumes, base = levels(values.get("channelVolumes")), values.get("volumeBase")
    if (not wording.whole(device) or route.get("device") != device
            or not wording.whole(route.get("index")) or volumes is None
            or not wording.number(base) or not base > 0):
        return None
    return Route(card, route["index"], device, volumes, float(base), values.get("mute") is True)


def route_of(objects: Sequence[object], props: dict, direction: str = "Output"
             ) -> Route | None:
    """The route of the node whose props are `props`, a node of a card's in pw-dump's
    objects (pipewire_client.card_nodes), an output, or with `direction` "Input" a
    microphone: its card's Route param for its card.profile.device, None where the card gives
    none of the shape _route reads. pipewire-pulse 1.6.2 matches the device alone
    (collect.c's collect_device_info, read 2026-10-25): a device of an ACP card is an output
    or an input, so the direction finds the same route."""
    card = props["device.id"]
    for card_id, info, _card_props in pipewire_client.of_type(objects, "Device"):
        params = info.get("params") if card_id == card else None
        routes = params.get("Route") if isinstance(params, dict) else None
        for route in routes if isinstance(routes, list) else ():
            if (found := _route(route, card, props.get("card.profile.device"),
                                direction)) is not None:
                return found
    return None


def props_volumes(info: dict) -> tuple[tuple[float, ...] | None, bool]:
    """A node's volume on each channel and whether it is muted, from its info in pw-dump: the
    channelVolumes and the mute of the first entry of its Props with channelVolumes, the
    volumes None where they are not levels and the mute true only for a JSON true. (None,
    False) where it has no such entry."""
    params = info.get("params")
    entries = params.get("Props") if isinstance(params, dict) else None
    entry = next((entry for entry in (entries if isinstance(entries, list) else ())
                  if isinstance(entry, dict) and "channelVolumes" in entry), {})
    return levels(entry.get("channelVolumes")), entry.get("mute") is True


def route_argv(route: Route, volumes: Sequence[float], save: bool, *,
               mute: bool | None) -> list[str]:
    """pw-cli's set-param of `route` to `volumes`, and to `mute` unless None, as
    WirePlumber 0.5.13's apply-routes.lua sets a route: its index and device, the volumes and
    the mute in its props, and save, which decides whether WirePlumber keeps the level and
    the mute (state-routes.lua)."""
    shown = " ".join(f"{volume:.6f}" for volume in volumes)
    muted = "" if mute is None else f", mute: {'true' if mute else 'false'}"
    param = (f"{{ index: {route.index}, device: {route.device}, props: {{ channelVolumes: "
             f"[ {shown} ]{muted} }}, save: {'true' if save else 'false'} }}")
    return ["pw-cli", "set-param", str(route.card), "Route", param]


def sink_argv(sink_id: int, volumes: Sequence[float], mute: bool) -> list[str]:
    """pw-cli's set-param of the Props of the node `sink_id`, a sink or a microphone, to
    `volumes`, muted when `mute`, as GNOME's slider and its mute set them."""
    shown = " ".join(f"{volume:.6f}" for volume in volumes)
    return ["pw-cli", "set-param", str(sink_id), "Props",
            f"{{ channelVolumes: [ {shown} ]{', mute: true' if mute else ''} }}"]


def gnome_percent(volume: float) -> int:
    """A route's volume as GNOME's Sound settings shows it: the cube root, in percent, as
    WirePlumber's 0.064 shows as 40 %."""
    return round(volume ** (1 / 3) * GNOME_FULL)


def gnome_volume(percent: float) -> float:
    """The volume GNOME's slider sets for `percent`: gnome_percent the other way, its
    cube."""
    return (percent / GNOME_FULL) ** 3


def owned_nodes(objects: Sequence[object], pid: int) -> dict[str, int]:
    """The ids of the nodes the client whose process is `pid` made
    (pipewire_client.node_pids), by name, the first of each name; none for 0, no process."""
    nodes: dict[str, int] = {}
    for node, _info, props, made_by in pipewire_client.node_pids(objects):
        name = props.get("node.name")
        if isinstance(name, str) and name not in nodes and made_by == pid:
            nodes[name] = node
    return nodes


def feeding(objects: Sequence[object], into: int | None) -> set[int]:
    """The ids of the nodes that pw-dump's objects link into the node `into` by a link in a
    state other than error or unlinked; none while `into` is None."""
    links = [] if into is None else pipewire_client.of_type(objects, "Link")
    return {info["output-node-id"] for _link, info, _props in links
            if info.get("input-node-id") == into and wording.whole(info.get("output-node-id"))
            and info.get("state") not in ("error", "unlinked")}


def linked(objects: Sequence[object], output: int | None, into: int | None) -> bool:
    """Whether pw-dump's objects hold a link from the node `output` to the node `into` that
    `feeding` counts; False while either is None."""
    return output is not None and output in feeding(objects, into)


def read_fields(run: pipewire_client.Run, unit: str,
                graph: Callable[[Sequence[object], int], dict]) -> dict:
    """UnitState's fields for `unit`, and `graph`'s of a fresh pw-dump's objects for its
    MainPID (pipewire_client.dumped). Never raises."""
    fields: dict = {}
    pid = 0
    try:
        found = unit_of(run, unit)
    except pipewire_client.StepError as err:
        fields["systemd"] = str(err)
    else:
        fields["active"] = found.get("ActiveState") or "inactive"
        pid = pipewire_client.main_pid(found)
    try:
        fields.update(pipewire_client.dumped(run, lambda objects: graph(objects, pid)))
    except pipewire_client.NotInstalled:
        fields["tools"] = False
    except pipewire_client.StepError as err:
        fields["pipewire"] = str(err)
    return fields


def unit_summary(state: UnitState) -> tuple[str, str] | None:
    """(kind, words) for the first of the unit's own conditions below that holds, in
    spatial.summary's words for the kinds the two share; None while it runs and pw-dump was
    read."""
    if state.systemd:
        return "no_manager", spatial.NO_MANAGER.format(state.systemd)
    if state.active in ("activating", "reloading"):
        return "starting", "starting"
    if state.active != "active":
        return "off", "off"
    if not state.tools:
        return "no_tools", spatial.NO_TOOLS
    if state.pipewire:
        return "pipewire", spatial.PIPEWIRE_NOT_READ.format(state.pipewire)
    return None


def switch_folder() -> Path:
    """The runtime folder, where a switch's OSError comes from, the equalizer's or the noise
    suppression's: the folder's stat, the conf's write or the equalizer's record. Named
    without conf_path, whose second stat could raise again; XDG_RUNTIME_DIR is a full path,
    since each unit's conf_path, g432_eq's and g432_mic's, refuses it unset or relative
    before anything is written."""
    return cast("Path", runtime_folder.runtime_home())


def stop(run: pipewire_client.Run, unit: str) -> None:
    """Stop `unit`; one not loaded is off already. StepError in systemctl's words when the
    stop fails."""
    done = pipewire_client.run_program(run, ["systemctl", "--user", "stop", unit],
                                       check_exit=False)
    if done.returncode not in (0, NOT_LOADED):
        raise pipewire_client.StepError(pipewire_client.program_words(done, "systemctl"))
