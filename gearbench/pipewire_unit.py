"""A transient user unit of Gearbench's: its command line and its stop, the equalizer's
watch's included; for one that runs `pipewire -c` on a conf of Gearbench's, its state as
systemd and PipeWire give it, the wait after its start and the runtime folder a switch's
OSError comes from (switch_folder); a node a unit of Gearbench's made (made_in), and each such
node of some names (unit_nodes), and the node a chain's stream aims at (aimed_at); the default
output or input, read and picked (default_sink, users_defaults, pick_default), and a
pw-metadata write's outcome (written), or that of several (wrote_to, reached). Standard
library only; every outside program goes through a `run` argument and
`pipewire_client.run_program`, and a failure is a `pipewire_client.StepError` in words.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Collection, Iterator, Sequence
from pathlib import Path
from typing import Literal, cast

from . import pipewire_client, runtime_folder, wording

#: systemctl's exit status for a stop of a unit not loaded, as a transient unit is once it
#: has stopped: off already. systemd's EXIT_NOTINSTALLED, from its documented exit codes,
#: not yet seen from this stop.
NOT_LOADED = 5
#: PipeWire's filter-chain.service's hardening, which the spatial and Outputs units carry
#: too.
HARDENING = ("LockPersonality=yes", "MemoryDenyWriteExecute=yes", "NoNewPrivileges=yes",
             "RestrictNamespaces=yes", "SystemCallArchitectures=native",
             "SystemCallFilter=@system-service")
#: A chain's unit, a client of the session's PipeWire, is stopped with it and starts after
#: it and its session manager, as PipeWire's filter-chain.service does; the spatial and
#: Outputs units carry the same BindsTo= and After=.
PIPEWIRE_BOUND_TO = "pipewire.service"
PIPEWIRE_AFTER = "pipewire.service pipewire-session-manager.service"
#: The ActiveStates of a unit that runs or starts.
RUNS_OR_STARTS = ("active", "activating")
#: A switch-on's refusal when its unit stopped as it started, by the unit's description
#: and its name.
STOPPED = "{} stopped as it started: `journalctl --user -u {}` says why"
#: Why a pw-metadata write did not go (wrote_to): WirePlumber keeps the "default" metadata.
NO_METADATA = "pw-metadata found no default metadata, which WirePlumber keeps"
#: pw-metadata 1.6.2's line for the Metadata it took, by the id pw-dump lists it under, read
#: from its format string (2026-11-06): it comes before the write's own line, which holds a
#: name a client chose.
FOUND = re.compile(r'^Found "default" metadata (\d+)$', re.MULTILINE)
#: Why a write went to a Metadata default_metadatas does not count.
NOT_WIREPLUMBERS = ("pw-metadata wrote to a default metadata another program made, not "
                    "WirePlumber's: close that program")
#: Why a pick that went did not show: WirePlumber did not make it the default in time.
NOT_SHOWN = f"WirePlumber did not make it the default within {pipewire_client.SETTLE:g} s"
#: What the "default" metadata keeps a default node of, as its keys end: an output, "sink",
#: or an input, "source".
type DefaultKind = Literal["sink", "source"]


class WentUnread(str):
    """Why a write's outcome is not known, in words: the write went, and the pw-dump after it
    failed (reached, pick_default). A str to whatever says it."""

    __slots__ = ()


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
    return [*transient_argv(unit, description, PIPEWIRE_BOUND_TO, after=PIPEWIRE_AFTER),
            # Each $ doubled: the user manager expands ${NAME} in the command line, an unset
            # one to nothing, and reads $$ as $ (systemd.service(5), "Command lines"), so
            # PipeWire would read a conf in a folder private_runtime never checked.
            pipewire_client.PIPEWIRE, "-c", str(conf).replace("$", "$$")]


def show_argv(unit: str) -> list[str]:
    """`systemctl --user show`'s command line for `unit`, with the properties read here."""
    return pipewire_client.systemctl("--property=Id,LoadState,ActiveState,SubState,MainPID",
                                     "show", unit)


def unit_of(run: pipewire_client.Run, unit: str) -> dict[str, str]:
    """`unit` as `systemctl --user show` gives it; StepError when systemctl fails."""
    return pipewire_client.unit_of(run, show_argv(unit))


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


def maker(objects: Sequence[object], name: str, unit: str, *,
          media_class: str = "Audio/Sink") -> int | None:
    """The pid of the process of systemd's `unit` that made the node of `media_class` named
    `name` in pw-dump's `objects` (unit_nodes); None for none."""
    return next((pid for _node, props, pid in unit_nodes(objects, (name,), unit)
                 if props.get("media.class") == media_class), None)


def made_in(objects: Sequence[object], name: str, unit: str, *,
            media_class: str = "Audio/Sink") -> bool:
    """Whether a process of systemd's `unit` made the node of `media_class` named `name` in
    pw-dump's `objects` (maker)."""
    return maker(objects, name, unit, media_class=media_class) is not None


def unit_nodes(objects: Sequence[object], names: Collection[str], unit: str
               ) -> list[tuple[int, dict, int]]:
    """Each node in pw-dump's `objects` named one of `names` that a process running in
    systemd's `unit` made (pipewire_client.unit_of_process), as (id, props, pid), each of a
    name made twice included, by the pid PipeWire took from its client's socket
    (pipewire_client.node_pids): any client can name a node so, a sandboxed app and
    pipewire-pulse for a pulse client among them, and a node with no maker
    (pipewire_client.node_client) is no unit's. The gap: an unsandboxed program of the same
    user can move itself into a cgroup of the unit's name."""
    return [(node, props, pid) for node, _info, props, pid in pipewire_client.node_pids(objects)
            if props.get("node.name") in names and pipewire_client.unit_of_process(pid) == unit]


def default_key(kind: DefaultKind, *, configured: bool = False) -> str:
    """The "default" metadata's key for the default node of `kind`: the one WirePlumber
    picked, default.audio.<kind>, or with `configured` the one the user picked,
    default.configured.audio.<kind>."""
    return f"default.{'configured.' if configured else ''}audio.{kind}"


def default_metadatas(objects: Sequence[object]) -> Iterator[dict]:
    """Each "default" Metadata in pw-dump's objects that counts. A client that may use
    PipeWire's metadata factory can make a Metadata of that name and fill it, so one counts
    only where its client.id, which the factory sets, names a Client PipeWire did not
    restrict (pipewire_client.trusted_clients); one with no client.id counts for none, as
    pipewire_client.card_nodes' Devices. With PipeWire 1.6.2 and WirePlumber 0.5.13, the
    session's "default" Metadata's client.id named WirePlumber's Client, whose
    pipewire.access was unrestricted (measured 2026-10-30)."""
    trusted = pipewire_client.trusted_clients(objects)
    for obj in objects:
        if (isinstance(obj, dict) and obj.get("type") == "PipeWire:Interface:Metadata"
                and isinstance(obj.get("props"), dict)
                and obj["props"].get("metadata.name") == "default"
                and pipewire_client.as_whole(obj["props"].get("client.id")) in trusted):
            yield obj


def default_metadata(objects: Sequence[object]) -> int | None:
    """The object.serial of the "default" Metadata in pw-dump's objects (default_metadatas),
    None for none. PipeWire counts serials up, so a new one is a new Metadata, with none of
    the old one's entries: WirePlumber makes it (scripts/metadata.lua), and it goes at each
    of WirePlumber's stops."""
    return next((serial for obj in default_metadatas(objects)
                 if (serial := pipewire_client.as_whole(obj["props"].get("object.serial")))
                 is not None), None)


def default_named(objects: Sequence[object], key: str) -> str | None:
    """The node name the "default" Metadata in pw-dump's objects (default_metadatas) holds
    under `key` for the whole session, subject 0; None for none."""
    for obj in default_metadatas(objects):
        if isinstance(obj.get("metadata"), list):
            for item in obj["metadata"]:
                value = item.get("value") if isinstance(item, dict) else None
                if (isinstance(value, dict) and item.get("subject") == 0
                        and item.get("key") == key and isinstance(value.get("name"), str)):
                    return value["name"]
    return None


def default_sink(objects: Sequence[object]) -> str | None:
    """The default output's node name in pw-dump's objects, the one WirePlumber picked
    (default_named), None for none."""
    return default_named(objects, default_key("sink"))


def users_defaults(objects: Sequence[object], kind: DefaultKind) -> list[str]:
    """The node names in pw-dump's objects that stand for the user's default node of `kind`,
    each that is set, once: first the one the user picked, which GNOME's pick and wpctl
    set-default write and which WirePlumber 0.5.13 keeps while that node is away, playing on
    another meanwhile (state-default-nodes.lua, find-selected-default-node.lua); then the
    one WirePlumber picked (default_named)."""
    names = (default_named(objects, default_key(kind, configured=True)),
             default_named(objects, default_key(kind)))
    return list(dict.fromkeys(name for name in names if name is not None))


def default_argv(name: str, kind: DefaultKind = "sink") -> list[str]:
    """pw-metadata's write of the node picked as the default of `kind`, its configured key
    (default_key), as GNOME's pick and wpctl set-default write it; WirePlumber 0.5.13 then
    makes it the default while it is there, and keeps it as the user's pick while it is
    away (find-selected-default-node.lua, state-default-nodes.lua)."""
    return ["pw-metadata", "-n", "default", "0", default_key(kind, configured=True),
            json.dumps({"name": name}), "Spa:String:JSON"]


def written(run: pipewire_client.Run, argv: list[str]) -> str | None:
    """Why the pw-metadata write `argv` did not reach a Metadata default_metadatas counts, in
    words; None once it did. pw-metadata 1.6.2 takes the first Metadata of the name PipeWire
    lists and has no option to pick one (getopt_long with "hVr:lmdn:"), so a client's that
    stayed through a WirePlumber restart would take the write: read from their sources, not
    run. This says so with one pw-dump a write, and cannot stop the write; a WentUnread where
    that pw-dump fails."""
    return reached(run, [wrote_to(run, argv)])[0]


def wrote_to(run: pipewire_client.Run, argv: list[str]) -> int | str:
    """The id of the Metadata the pw-metadata write `argv` took (FOUND), else why it did not
    go, in words."""
    try:
        done = pipewire_client.run_program(run, argv)
    except pipewire_client.StepError as err:
        return str(err)
    # pw-metadata 1.6.2 exits 0 saying nothing for a metadata it does not find (measured
    # 2026-10-17).
    found = FOUND.search(done.stdout)
    return NO_METADATA if found is None else int(found[1])


def reached(run: pipewire_client.Run, taken: Sequence[int | str]) -> list[str | None]:
    """written's answer for each of `taken` (wrote_to), by one pw-dump for them all and none
    where no write went: a client sets how many streams a caller moves."""
    if all(isinstance(took, str) for took in taken):
        return [str(took) for took in taken]
    try:
        counted = pipewire_client.dumped(
            run, lambda objects: {obj.get("id") for obj in default_metadatas(objects)})
    except pipewire_client.StepError as err:
        unread = WentUnread(err)
        return [took if isinstance(took, str) else unread for took in taken]
    return [took if isinstance(took, str) else None if took in counted else NOT_WIREPLUMBERS
            for took in taken]


def aim_argv(stream: int, target: str) -> list[str]:
    """pw-metadata's write that aims the node `stream`, a stream, at the node named `target`:
    its target.object in the "default" metadata, which WirePlumber 0.5.13 takes over the
    target.object in the stream's own props and links it by (find-defined-target.lua). "--"
    ends the options: a node's name is whatever its client gave it, and pw-metadata 1.6.2
    reads an argument that starts with "-" as an option wherever it stands (getopt_long with
    "hVr:lmdn:", read 2026-11-01), so a sink named "-d" would make the write a delete."""
    return ["pw-metadata", "-n", "default", "--", str(stream), "target.object", target]


def pick_default(run: pipewire_client.Run, name: str, kind: DefaultKind = "sink"
                 ) -> str | None:
    """Make `name` the default of `kind`, the default output unless told, and wait up to
    pipewire_client.SETTLE for WirePlumber to pick it, which moves the streams that follow
    the default with it: None once WirePlumber shows the pick, else why not, in words, a
    WentUnread where a pw-dump failed after the write. A pick not seen is not known to have
    gone, so the sound may still go where it did."""
    if (failed := written(run, default_argv(name, kind))) is not None:
        return failed

    def look() -> str | None:
        try:
            shown = pipewire_client.dumped(
                run, lambda objects: default_named(objects, default_key(kind)))
        except pipewire_client.StepError as err:
            return WentUnread(err)
        return None if shown == name else NOT_SHOWN
    return pipewire_client.waited(look, lambda failed: failed is not NOT_SHOWN)


def aimed_at(objects: Sequence[object], pid: int, stream: str) -> str | None:
    """The node the stream `stream` of the chain whose process is `pid` aims at, by name: its
    target.object, the one its conf was rendered with; None while there is no such stream,
    or for 0, no process (pipewire_client.node_pids)."""
    return next((target
                 for _node, _info, props, made_by in pipewire_client.node_pids(objects)
                 if made_by == pid and props.get("node.name") == stream
                 and isinstance(target := props.get("target.object"), str)), None)


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
    MainPID, 0 where systemctl failed (pipewire_client.unit_and_graph). Never raises."""
    return pipewire_client.unit_and_graph(
        run, show_argv(unit), lambda found: {"active": found.get("ActiveState") or "inactive"},
        lambda objects, found: graph(objects,
                                     0 if found is None else pipewire_client.main_pid(found)))


def unit_summary(state: pipewire_client.UnitState) -> tuple[str, str] | None:
    """(kind, words) for the first of the unit's own conditions below that holds, in
    spatial.unit_kind's words for the kinds the two share; None while it runs and pw-dump was
    read."""
    if state.systemd:
        return "no_manager", pipewire_client.NO_MANAGER.format(state.systemd)
    if state.active in ("activating", "reloading"):
        return "starting", "starting"
    if state.active != "active":
        return "off", "off"
    if not state.tools:
        return "no_tools", pipewire_client.NO_TOOLS
    if state.pipewire:
        return "pipewire", pipewire_client.PIPEWIRE_NOT_READ.format(state.pipewire)
    return None


def switch_folder() -> Path:
    """The runtime folder, where a switch's OSError comes from, the equalizer's or the noise
    suppression's. Named without conf_path, which stats the folder again and could refuse it;
    XDG_RUNTIME_DIR is a full path, since each unit's conf_path, g432_eq's and headset_mic's,
    refuses it unset or relative before anything is written."""
    return cast("Path", runtime_folder.runtime_home())


def stop(run: pipewire_client.Run, unit: str) -> None:
    """Stop `unit`; one not loaded is off already. StepError in systemctl's words when the
    stop fails."""
    done = pipewire_client.run_program(run, pipewire_client.systemctl("stop", unit),
                                       check_exit=False)
    if done.returncode not in (0, NOT_LOADED):
        raise pipewire_client.StepError(pipewire_client.program_words(done, "systemctl"))
