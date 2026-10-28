"""The programs systemd and PipeWire are run and read through, for the modules that run a
chain's unit or read PipeWire: each through a `run` argument, a process read under `PROC`,
and a failure a `StepError` in words. Standard library only.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from pathlib import Path

from . import wording

#: The pipewire.access of a client PipeWire did not restrict when it connected, which the
#: client cannot change. It is not enough alone: PipeWire 1.6.2's module-access gives a
#: Flatpak app's client "flatpak" only in its legacy mode, the default while access.socket
#: is unset, and with access.socket set the socket's access, "default" on pipewire-0, as it
#: gives a host program's (read 2026-10-22). A sandboxed app may make Devices and nodes with
#: any props, a card's ids and device.id among them: WirePlumber 0.5.13 gives it "rx"
#: (access-default.lua).
TRUSTED_ACCESS = frozenset({"unrestricted", "default"})
#: The marks PipeWire 1.6.2 gives a Flatpak app's client in either mode: module-access sets
#: both, pipewire-pulse the app_id. A client can set no pipewire.sec.* key and change no
#: pipewire.* key once set (impl-client's check_client_property_update).
SANDBOXED = ("pipewire.sec.flatpak", "pipewire.access.portal.app_id")
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
#: What a conf's SPA-JSON string cannot hold: " and \ would end or escape it, and PipeWire
#: refuses a character below 0x20 ("Character not allowed").
NOT_IN_CONF = re.compile(r'["\\\x00-\x1f]')
TIMEOUT = 5.0          # any one program
#: How long a wait for PipeWire to catch up with a start or a change lasts, and how often
#: it looks: systemctl start and systemd-run return before PipeWire has built the chain.
SETTLE = 2.0
SETTLE_STEP = 0.2
Run = Callable[..., subprocess.CompletedProcess]
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
#: Why a pw-dump whose objects shaped cannot read is refused.
UNKNOWN_SHAPE = "pw-dump printed objects of a shape Gearbench does not know"
#: Where parent_of and unit_of_process read a process; a test points it at a tree of its own.
PROC = Path("/proc")


class StepError(Exception):
    """A step that failed, in words for a toast or stderr."""


class NotInstalled(StepError):
    """The program is not installed."""


def program_words(done: subprocess.CompletedProcess, program: str) -> str:
    """A program's stderr on one line, else how it ended."""
    return " ".join(done.stderr.split()) or wording.ended(program, done.returncode)


def not_run(program: str, err: OSError) -> StepError:
    """Why `program` did not start, `err` being what its start raised: NotInstalled for a
    program that is not there."""
    if isinstance(err, FileNotFoundError):
        return NotInstalled(f"{program} is not installed")
    return StepError(f"{program} did not run: {wording.reason(err)}")


def run_program(run: Run, argv: list[str], timeout: float = TIMEOUT,
                check_exit: bool = True) -> subprocess.CompletedProcess:
    """Run one program; any failure is a StepError in words."""
    program = argv[0]
    try:
        # errors="replace": any PipeWire client names its own nodes, and one name that is not
        # UTF-8 would make all of pw-dump unreadable.
        done = run(argv, check=False, capture_output=True, encoding="utf-8",
                   errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        raise StepError(f"{program} took over {timeout:g} s") from None
    except OSError as err:
        raise not_run(program, err) from None
    if check_exit and done.returncode != 0:
        raise StepError(program_words(done, program))
    return done


def pw_cli(run: Run, argv: list[str]) -> str:
    """Run a `pw-cli` command: "" when it went through, else why not, in words; StepError
    when it did not run. pw-cli 1.6 exits 0 when its command fails: its words decide."""
    done = run_program(run, argv, check_exit=False)
    return program_words(done, "pw-cli") if done.returncode or "Error:" in done.stderr else ""


def dump_objects(run: Run) -> list:
    """pw-dump's objects; StepError for an answer that is no list of them."""
    try:
        objects = json.loads(run_program(run, DUMP).stdout)
    except (ValueError, RecursionError):
        raise StepError("pw-dump printed something other than JSON") from None
    if not isinstance(objects, list):
        raise StepError("pw-dump printed something other than a list of objects")
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


def unit_of(run: Run, show: list[str]) -> dict[str, str]:
    """The unit `show`, a `systemctl show` command line, asks for, as systemctl gives it;
    StepError when systemctl fails."""
    units = parse_units(run_program(run, show).stdout)
    return units[0] if units else {}


def main_pid(unit: dict[str, str]) -> int:
    """The unit's MainPID, 0 for none, which matches no client's pid."""
    return as_whole(unit.get("MainPID")) or 0


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


def parent_of(pid: int) -> int | None:
    """The pid of process `pid`'s parent, the fourth field of its stat; None for a process
    that cannot be read: one gone since pw-dump printed it, or a pid no process has."""
    try:
        stat = (PROC / str(pid) / "stat").read_bytes()
    except OSError:
        return None
    # The name, the second field, is in parentheses and can hold spaces and ")" itself.
    return int(stat.rpartition(b")")[2].split()[1])


def unit_of_process(pid: int) -> str | None:
    """The systemd unit process `pid` runs in: the last part of the cgroup v2 path on the
    0:: line of its cgroup file (cgroups(7)), which systemd names after the unit. None for a
    process that cannot be read, as parent_of, or with no such line."""
    try:
        lines = (PROC / str(pid) / "cgroup").read_text(errors="replace").splitlines()
    except OSError:
        return None
    return next((line.rpartition("/")[2] for line in lines if line.startswith("0::")), None)


def as_whole(value: object) -> int | None:
    """A whole number from pw-dump or systemctl, as a JSON number or a string of digits,
    else None."""
    if wording.whole(value):
        return value
    try:
        return int(value) if isinstance(value, str) and value.isdecimal() else None
    except ValueError:      # past int()'s 4300 digits
        return None


def shaped[T](parse: Callable[[Sequence[object]], T], objects: Sequence[object]) -> T:
    """`parse` of pw-dump's objects; StepError on objects of a shape it cannot read,
    such as an id or a prop that is a list where a set lookup needs one it can hash."""
    try:
        return parse(objects)
    # OverflowError: float() of a whole number no float holds, in a control's place.
    # RecursionError: str() of a value nested deeper than it walks, which json.loads
    # still builds.
    except (AttributeError, TypeError, OverflowError, RecursionError):
        raise StepError(UNKNOWN_SHAPE) from None


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


def node_pids(objects: Sequence[object]) -> list[tuple[int, dict, dict, int]]:
    """Each Node in pw-dump's objects whose client.id names a Client with a process, as (id,
    info, props, pid): the pipewire.sec.pid PipeWire took from that client's socket, which
    the client cannot set, so it tells whose a node is where the node's name cannot. Each is
    read by as_whole, text of digits included and true not; 0, which SO_PEERCRED
    gives a client outside PipeWire's pid namespace, is no process."""
    pids = {client: pid for client, _info, props in of_type(objects, "Client")
            if (pid := as_whole(props.get("pipewire.sec.pid")))}
    return [(node, info, props, pids[client]) for node, info, props in of_type(objects, "Node")
            if (client := as_whole(props.get("client.id"))) in pids]


def trusted_clients(objects: Sequence[object]) -> set[int]:
    """The ids of the Clients in pw-dump's objects that PipeWire did not restrict: a
    pipewire.access of TRUSTED_ACCESS, none of SANDBOXED's marks, and no less asked for in
    pipewire.client.access, which WirePlumber 0.5.13 holds a client to (access-default.lua)."""
    return {client for client, _info, props in of_type(objects, "Client")
            if props.get("pipewire.access") in TRUSTED_ACCESS
            and not any(mark in props for mark in SANDBOXED)
            and props.get("pipewire.client.access", "default") in TRUSTED_ACCESS}


def card_nodes(objects: Sequence[object], card: Sequence[tuple[str, Sequence[object]]]
               ) -> tuple[dict[int, dict], list[dict]]:
    """The Devices in pw-dump's objects with each of `card`'s props at one of its values, a
    Headset's card, by id with their props, and the props of their nodes, each in pw-dump's
    order. A client sets the props of what it makes, device.id included, so a Device counts
    only when its client.id names a Client PipeWire did not restrict (trusted_clients), and
    a node only when its device.id is such a Device and its client.id is that Device's; one
    with no client.id counts for none."""
    trusted = trusted_clients(objects)
    cards = {found: props for found, _info, props in of_type(objects, "Device")
             if all(props.get(key) in values for key, values in card)
             and as_whole(props.get("client.id")) in trusted}
    nodes = [props for _node, _info, props in of_type(objects, "Node")
             if (found := as_whole(props.get("device.id"))) in cards
             and as_whole(props.get("client.id")) == as_whole(cards[found].get("client.id"))]
    return cards, nodes
