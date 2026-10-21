"""A transient user unit that runs `pipewire -c` on a conf of Gearbench's: its command line,
its state as systemd and PipeWire give it, the wait after its start, and its stop. Standard
library only; every outside program goes through a `run` argument and `spatial.run_program`,
and a failure is a `spatial.SpatialError` in words.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from . import spatial

#: systemctl's exit status for a stop of a unit not loaded, as a transient unit is once it
#: has stopped: off already. systemd's EXIT_NOTINSTALLED, from its documented exit codes,
#: not yet seen from this stop.
NOT_LOADED = 5
#: PipeWire's filter-chain.service's hardening, which the spatial unit carries too.
HARDENING = ("LockPersonality=yes", "MemoryDenyWriteExecute=yes", "NoNewPrivileges=yes",
             "RestrictNamespaces=yes", "SystemCallArchitectures=native",
             "SystemCallFilter=@system-service")
#: A switch-on's refusal when its unit stopped as it started, by the unit's description
#: and its name.
STOPPED = "{} stopped as it started: journalctl --user -u {} says why."


@dataclass(frozen=True)
class UnitState:
    """What systemd and pw-dump say of the unit: the fields unit_summary reads, before those
    of the conf's own nodes, which each unit's State adds."""

    systemd: str | None = None        # why `systemctl --user show` failed
    active: str = "inactive"          # the unit's ActiveState
    tools: bool = True                # False: pw-dump not installed
    pipewire: str | None = None       # why pw-dump failed or did not parse


def pipewire_argv(unit: str, description: str, conf: Path) -> list[str]:
    """systemd-run's command line for a transient unit, `unit`, that runs `pipewire -c` on
    `conf`. --collect unloads it once it stops, failed or not, so the next switch-on can
    make it again."""
    return ["systemd-run", "--user", f"--unit={unit}", f"--description={description}",
            "--collect", "--quiet", "--slice=session.slice",
            # A client of the session's PipeWire, stopped with it.
            "--property=BindsTo=pipewire.service",
            "--property=After=pipewire.service pipewire-session-manager.service",
            *(f"--property={setting}" for setting in HARDENING),
            spatial.PIPEWIRE, "-c", str(conf)]


def unit_of(run: spatial.Run, unit: str) -> dict[str, str]:
    """`unit` as `systemctl --user show` gives it; SpatialError when systemctl fails."""
    return spatial.unit_of(run, ["systemctl", "--user",
                                 "--property=Id,LoadState,ActiveState,SubState,MainPID",
                                 "show", unit])


def running(run: spatial.Run, unit: str) -> bool:
    """Whether `unit` runs or starts; SpatialError when systemctl fails."""
    return unit_of(run, unit).get("ActiveState") in ("active", "activating")


def stopped(run: spatial.Run, unit: str) -> bool:
    """Whether `unit` stopped as it started: a transient unit whose process exited at start
    is collected (--collect) and reads inactive, or failed (systemd.unit(5)'s CollectMode),
    not yet seen with either unit. A systemctl that does not answer is no stop known."""
    try:
        return not running(run, unit)
    except spatial.SpatialError:
        return False


def settle_start[S](run: spatial.Run, unit: str, read_state: Callable[[], S],
                    summary: Callable[[S], tuple[str, str]],
                    refusal: spatial.SpatialError) -> S:
    """The state `unit`, just started by systemd-run, settles on (spatial.settled, which
    reads it again while it starts); `refusal` raised once it has, where the unit stopped
    as it started (`stopped`). The look waits for the start: a transient unit whose process
    exits at once reads ActiveState=active for under 50 ms after systemd-run returns, and
    inactive from 50 ms on (hardware round 4, 2026-10-20)."""
    state = spatial.settled(read_state, summary, True)
    if stopped(run, unit):
        raise refusal
    return state


def clients_of(objects: Sequence[object], pid: int) -> set[int]:
    """The clients whose process is `pid`, by the pid PipeWire took from its socket; none for
    0, no process, which SO_PEERCRED gives for a client outside PipeWire's pid namespace."""
    return {client for client, _info, props in spatial.of_type(objects, "Client")
            if pid and props.get("pipewire.sec.pid") == pid}


def owned_nodes(objects: Sequence[object], pid: int) -> dict[str, int]:
    """The ids of the nodes the client whose process is `pid` made (clients_of), by name,
    the first of each name."""
    owned = clients_of(objects, pid)
    nodes: dict[str, int] = {}
    for node, _info, props in spatial.of_type(objects, "Node"):
        name = props.get("node.name")
        if isinstance(name, str) and name not in nodes and props.get("client.id") in owned:
            nodes[name] = node
    return nodes


def linked(objects: Sequence[object], output: int | None, into: int | None) -> bool:
    """Whether pw-dump's objects hold a link from the node `output` to the node `into`, in a
    state other than error or unlinked; False while either is None."""
    return output is not None and into is not None and any(
        (info.get("output-node-id"), info.get("input-node-id")) == (output, into)
        and info.get("state") not in ("error", "unlinked")
        for _link, info, _props in spatial.of_type(objects, "Link"))


def read_fields(run: spatial.Run, unit: str,
                graph: Callable[[Sequence[object], int], dict]) -> dict:
    """UnitState's fields for `unit`, and `graph`'s of a fresh pw-dump's objects for its
    MainPID (spatial.dumped). Never raises."""
    fields: dict = {}
    pid = 0
    try:
        found = unit_of(run, unit)
    except spatial.SpatialError as err:
        fields["systemd"] = str(err)
    else:
        fields["active"] = found.get("ActiveState") or "inactive"
        pid = spatial.main_pid(found)
    try:
        fields.update(spatial.dumped(run, lambda objects: graph(objects, pid)))
    except spatial.NotInstalled:
        fields["tools"] = False
    except spatial.SpatialError as err:
        fields["pipewire"] = str(err)
    return fields


def unit_summary(state: UnitState) -> tuple[str, str] | None:
    """(kind, words) for the first of the unit's own conditions below that holds; None while
    it runs and pw-dump was read."""
    if state.systemd:
        return "no_manager", f"systemctl --user did not answer: {state.systemd}"
    if state.active in ("activating", "reloading"):
        return "starting", "starting"
    if state.active != "active":
        return "off", "off"
    if not state.tools:
        return "no_tools", "on, but PipeWire's pw-dump is not installed"
    if state.pipewire:
        return "pipewire", f"on, but PipeWire could not be read: {state.pipewire}"
    return None


def stop(run: spatial.Run, unit: str) -> None:
    """Stop `unit`; one not loaded is off already. SpatialError in systemctl's words when the
    stop fails."""
    done = spatial.run_program(run, ["systemctl", "--user", "stop", unit], check_exit=False)
    if done.returncode not in (0, NOT_LOADED):
        raise spatial.SpatialError(spatial.program_words(done, "systemctl"))
