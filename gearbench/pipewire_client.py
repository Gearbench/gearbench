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
from dataclasses import dataclass
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
#: What pw-cli 1.6.2 writes on stderr for a command that failed, as it exits 0 all the same:
#: its own refusal, 'Error: "..."', and an error PipeWire sent back for the command, such as
#: a node gone before the set reached it, "remote 0: error id:..." (pw-cli.c's main and
#: on_core_error; both seen on stderr, with exit status 0, on 2026-11-03).
PW_CLI_FAILED = ("Error:", ": error id:")
#: How long a wait for PipeWire to catch up with a start or a change lasts, and how often
#: it looks: systemctl start and systemd-run return before PipeWire has built the chain.
SETTLE = 2.0
SETTLE_STEP = 0.2
Run = Callable[..., subprocess.CompletedProcess]
DUMP = ["pw-dump", "--no-colors"]
#: pw-dump's monitor. pw-dump 1.6.2 prints its first dump, then each object that changes,
#: whole, and {"id": N, "info": null} for one removed, each print a JSON array whose "["
#: and "]" stand alone on their lines (a throwaway PipeWire, 2026-10-17). A Metadata is the
#: exception: printed again, it holds only the entries set since its last print, and a key
#: removed is in no print (pw-dump.c's metadata_dump and metadata_property, read
#: 2026-10-31), so the defaults are read from a fresh pw-dump (dump_objects).
MONITOR = ["pw-dump", "--monitor", "--no-colors"]
#: The program that runs a filter chain's conf, `pipewire -c`.
PIPEWIRE = "/usr/bin/pipewire"
#: The most one print may hold before printed drops it: far over the 74 KB of a
#: throwaway PipeWire's first dump (2026-10-17), and far under what would fill the memory.
PRINT_MAX = 64 * 2 ** 20
#: The most pw-dump --monitor's pipe is read in one go, in bytes.
CHUNK = 1 << 16
#: The journal line of a unit following pw-dump --monitor for a print it did not read, by
#: the unit's name, then the cause.
UNREAD = "{}: a print of pw-dump --monitor was not read ({{}}); it reads on"
#: Why a print was not read: printed's None.
NOT_A_PRINT = f"no JSON, or over {wording.size_words(PRINT_MAX)}"
#: Why a pw-dump whose objects shaped cannot read is refused.
UNKNOWN_SHAPE = "pw-dump printed objects of a shape Gearbench does not know"
#: Where parent_of and unit_of_process read a process; a test points it at a tree of its own.
PROC = Path("/proc")


def conf_holds(name: str) -> bool:
    """Whether `name` can be a node's name in a conf's SPA-JSON string: not empty, and none
    of NOT_IN_CONF."""
    return bool(name) and NOT_IN_CONF.search(name) is None


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
    when it did not run. pw-cli 1.6 exits 0 when its command fails: its words decide
    (PW_CLI_FAILED)."""
    done = run_program(run, argv, check_exit=False)
    failed = done.returncode or any(mark in done.stderr for mark in PW_CLI_FAILED)
    return program_words(done, "pw-cli") if failed else ""


def pw_cli_failed(run: Run, argv: list[str]) -> str | None:
    """Why the `pw-cli` command `argv` did not go through or did not run, in words (pw_cli);
    None once it went through."""
    try:
        return pw_cli(run, argv) or None
    except StepError as err:
        return str(err)


def dump_objects(run: Run) -> list:
    """pw-dump's objects; StepError for an answer that is no list of them."""
    try:
        objects = json.loads(run_program(run, DUMP).stdout)
    except (ValueError, RecursionError):
        raise StepError("pw-dump printed something other than JSON") from None
    if not isinstance(objects, list):
        raise StepError("pw-dump printed something other than a list of objects")
    return objects


class Prints:
    """The prints of pw-dump --monitor, read off its pipe as each part comes (feed_bytes),
    for a process that waits on more than the monitor too, or a line at a time (feed)."""

    def __init__(self) -> None:
        self._held: list[str] = []
        self._size = 0
        #: The part of a line read before its end (lines_of).
        self._pending = bytearray()

    def feed_bytes(self, chunk: bytes) -> Iterator[list | None]:
        """Each print `chunk`, read from pw-dump --monitor's pipe, ends after what came before
        it, cut into lines by lines_of, a line past PRINT_MAX included (feed)."""
        for line in lines_of(chunk, self._pending):
            yield from self.feed(line)

    def feed(self, line: str) -> Iterator[list | None]:
        """The print `line` ends, if it ends one: a JSON array, or None for one that is no
        JSON or is over PRINT_MAX, which is not kept past it. A print ends at a line that is
        "]" alone."""
        self._size += len(line)
        if self._size <= PRINT_MAX:
            self._held.append(line)
        if line.rstrip("\n") != "]":
            return
        # One over PRINT_MAX lacks its closing "]", so it is no JSON; JSON that ends at a
        # "]" is an array.
        try:
            found = json.loads("".join(self._held))
        except (ValueError, RecursionError):
            found = None
        self._held, self._size = [], 0
        yield found


def printed(chunks: Iterable[bytes]) -> Iterator[list | None]:
    """Each print of pw-dump --monitor in `chunks`, the parts of its pipe as they were read
    (Prints.feed_bytes)."""
    reader = Prints()
    for chunk in chunks:
        yield from reader.feed_bytes(chunk)


def lines_of(chunk: bytes, pending: bytearray) -> list[str]:
    """The whole lines `chunk` ends, after what `pending` holds of a line before it, each
    as text, a byte that is not UTF-8 replaced, as pw-dump's names can hold one; `pending`
    keeps the rest. A line past PRINT_MAX is given as it is, so the print that holds it is
    dropped (Prints) and nothing more is kept."""
    searched = len(pending)
    pending += chunk
    # Only the chunk is searched: `pending` holds no newline, and a line that comes in many
    # reads is not searched again at each.
    end = pending.rfind(b"\n", searched) + 1
    lines = [line.decode("utf-8", "replace") + "\n" for line in pending[:end].split(b"\n")[:-1]]
    del pending[:end]
    if len(pending) > PRINT_MAX:
        lines.append(pending.decode("utf-8", "replace"))
        pending.clear()
    return lines


def merge(objects: dict[int, dict], found: list) -> None:
    """Fold one print into `objects`, by id: an object printed replaces the one before it,
    and one removed, printed with no type, goes. A Metadata printed again holds only its
    entries set since (MONITOR), so `objects` holds no whole Metadata."""
    for obj in found:
        if isinstance(obj, dict) and wording.whole(obj.get("id")):
            if "type" in obj:
                objects[obj["id"]] = obj
            else:
                objects.pop(obj["id"], None)


def systemctl(*args: str) -> list[str]:
    """Options go before the verb: with POSIXLY_CORRECT set, systemctl's getopt
    stops at the verb and reads a later option as a unit name."""
    return ["systemctl", "--user", *args]


def unit_of(run: Run, show: list[str]) -> dict[str, str]:
    """The unit `show`, a `systemctl show` command line, asks for, as systemctl gives it: the
    first block of its output with a key, {} for none; StepError when systemctl fails."""
    for block in re.split(r"\n[ \t]*\n", run_program(run, show).stdout.strip()):
        if unit := dict(line.split("=", 1) for line in block.splitlines() if "=" in line):
            return unit
    return {}


def main_pid(unit: dict[str, str]) -> int:
    """The unit's MainPID, 0 for none, which matches no client's pid."""
    return as_whole(unit.get("MainPID")) or 0


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
    """A whole number given as a JSON number or a string of digits, else None."""
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


def waited[T](look: Callable[[], T], done: Callable[[T], bool]) -> T:
    """look's value, read again every SETTLE_STEP for up to SETTLE while `done` says no: the
    last one read. What look raises goes up at once."""
    deadline = time.monotonic() + SETTLE
    value = look()
    while not done(value) and time.monotonic() < deadline:
        time.sleep(SETTLE_STEP)
        value = look()
    return value


def settled[S](read_state: Callable[[], S], summary: Callable[[S], tuple[str, str]],
               waits: bool) -> S:
    """read_state's state, read again (waited) while `waits` and its summary is starting."""
    return waited(read_state, lambda state: not waits or summary(state)[0] != "starting")


#: The states Gearbench's user units give in the same words, Spatial's and Outputs'
#: (unit_kind) and each equalizer's and noise suppression's (pipewire_unit.unit_summary),
#: `{}` the failure's own: systemctl not answering, pw-dump not installed, and pw-dump failing.
NO_MANAGER = "systemctl --user did not answer: {}"
NO_TOOLS = "on, but PipeWire's pw-dump is not installed"
PIPEWIRE_NOT_READ = "on, but PipeWire could not be read ({})"
#: The kinds of spatial.unit_kind and pipewire_unit.unit_summary in which systemctl cannot
#: act on the unit, so a window grays out its switch.
LOCKED = frozenset({"no_manager", "not_installed"})


@dataclass(frozen=True)
class UnitState:
    """What systemd and pw-dump say of the unit: the fields unit_summary reads, before those
    of the conf's own nodes, which each unit's State adds."""

    systemd: str | None = None        # why `systemctl --user show` failed
    active: str = "inactive"          # the unit's ActiveState
    tools: bool = True                # False: pw-dump not installed
    pipewire: str | None = None       # why pw-dump failed or did not parse


def unit_and_graph(run: Run, show: list[str], from_unit: Callable[[dict[str, str]], dict],
                   graph: Callable[[Sequence[object], dict[str, str] | None], dict]) -> dict:
    """A unit's State fields: `from_unit`'s of the unit `show`, a `systemctl show` command
    line, asks for (unit_of), else `systemd`, why systemctl failed; then `graph`'s of a fresh
    pw-dump's objects (dumped) and that unit, None where systemctl failed, else `tools` False
    for a pw-dump not installed or `pipewire`, why it failed or did not parse. Never raises."""
    fields: dict = {}
    unit = None
    try:
        unit = unit_of(run, show)
    except StepError as err:
        fields["systemd"] = str(err)
    else:
        fields.update(from_unit(unit))
    try:
        fields.update(dumped(run, lambda objects: graph(objects, unit)))
    except NotInstalled:
        fields["tools"] = False
    except StepError as err:
        fields["pipewire"] = str(err)
    return fields


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


def listed_name(props: dict) -> str:
    """A node, by its props in pw-dump, as GNOME's Sound settings list it: its
    node.description, else its node name."""
    label = props.get("node.description")
    return label if isinstance(label, str) and label else props["node.name"]


def described(objects: Sequence[object], name: str) -> str:
    """The node named `name` in pw-dump's objects as GNOME lists it (listed_name), else the
    name, as text a line holds (wording.printable)."""
    props = next((props for _node, _info, props in of_type(objects, "Node")
                  if props.get("node.name") == name), {"node.name": name})
    return wording.printable(listed_name(props))


def client_pids(objects: Sequence[object]) -> dict[int, int]:
    """The process of each Client in pw-dump's objects that has one, by the Client's id: the
    pipewire.sec.pid PipeWire took from the client's socket, which the client cannot set.
    Each is read by as_whole, text of digits included and true not; 0, which SO_PEERCRED
    gives a client outside PipeWire's pid namespace, is no process."""
    return {client: pid for client, _info, props in of_type(objects, "Client")
            if (pid := as_whole(props.get("pipewire.sec.pid")))}


def node_client(props: dict) -> int | None:
    """The Client that made a Node, by the node's props in pw-dump: its client.id, else None,
    and None for a node made with object.linger true. PipeWire 1.6.2's adapter and spa-node
    factories set client.id to the maker's own only on a node that does not linger
    (module-adapter.c's and module-spa-node-factory.c's create_object), so a lingering node
    keeps any client.id its maker gave it, a sandboxed client's included. pw-dump prints the
    "true" and "1" PipeWire reads as true (spa_atob) as true and 1."""
    if props.get("object.linger") in (True, 1):
        return None
    return as_whole(props.get("client.id"))


def node_pids(objects: Sequence[object]) -> list[tuple[int, dict, dict, int]]:
    """Each Node in pw-dump's objects whose maker (node_client) is a Client with a process
    (client_pids), as (id, info, props, pid), so the pid tells whose a node is where the
    node's name cannot."""
    pids = client_pids(objects)
    return [(node, info, props, pids[client]) for node, info, props in of_type(objects, "Node")
            if (client := node_client(props)) in pids]


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
    a node only when its device.id is such a Device and its maker (node_client) is that
    Device's client, or another such Client of the same process (one_process); one with no
    maker counts for none."""
    trusted = trusted_clients(objects)
    pids = client_pids(objects)
    cards = {found: props for found, _info, props in of_type(objects, "Device")
             if all(props.get(key) in values for key, values in card)
             and as_whole(props.get("client.id")) in trusted}
    nodes = [props for _node, _info, props in of_type(objects, "Node")
             if (found := as_whole(props.get("device.id"))) in cards
             and (client := node_client(props)) in trusted
             and one_process(client, as_whole(cards[found].get("client.id")), pids)]
    return cards, nodes


def one_process(client: int | None, other: int | None, pids: dict[int, int]) -> bool:
    """Whether the Clients `client` and `other` are one, or two of one process by `pids`,
    client_pids': WirePlumber 0.5.18 makes a card's Device through one of its connections
    and the card's nodes through another (measured on Arch Linux, 2026-10-28). A client of no
    process is one only with itself."""
    return client == other or (client in pids and other in pids and pids[client] == pids[other])
