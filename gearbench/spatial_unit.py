"""gearbench-spatial.service's process: Gearbench Spatial's chain, kept on the headset it plays
to, `python3 -m gearbench.spatial_unit run PATH`.

It follows PipeWire with `pw-dump --monitor` and, at each print, takes the target, the
headset and the node the chain plays to (`target`). When the target changes it stops the
chain, renders the conf at PATH for the new one and starts it, `pipewire -c PATH`; with no
headset in PipeWire it runs no chain, so the sink is gone. The headset picked is read from
spatial.json once, at the start, and a change of it or of the head restarts the unit
(spatial.set_headset, set_head); each render reads the head and the room then, so a room
set live since is kept.
The chain ending on its own, seen at the next print or within LOOK_EVERY between prints, or
pw-dump's end, ends the process with 1, and systemd starts
it again (Restart=on-failure) up to the unit's start limit. Why no chain runs for a headset
that is there goes to the unit's StatusText as well (`status`), which spatial.summary shows.
Standard library only, never run in the daemon; the programs go through `main`'s `spawn`.
"""

from __future__ import annotations

import os
import selectors
import socket
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import IO, cast

from . import (
    outputs,
    pipewire_client,
    pipewire_headsets,
    pipewire_unit,
    runtime_folder,
    spatial,
    wording,
)
from .g432_eq_words import WORDS as EQ_WORDS, Words as EqWords

NAME = spatial.UNIT.removesuffix(".service")
#: How long a chain just started may take to fail, in seconds, before it counts as running:
#: one that cannot load its conf exits before it makes a node, which no print would show.
#: Assumed, not timed.
START_WAIT = 0.5
#: How long a stopped chain gets to exit on SIGTERM before SIGKILL, in seconds.
STOP_WAIT = 5.0
#: How often a unit looks at its running chain between prints, in seconds: one that exits
#: past START_WAIT before it makes a node leaves no print to show it. Assumed, not timed.
LOOK_EVERY = 1.0
#: The journal's lines.
PLAYS = f"{NAME}: plays to {{}}"
NO_HEADSET = f"{NAME}: no chain runs until {spatial.ANY_OUTPUT} is in PipeWire"
UNREAD = pipewire_client.UNREAD.format(NAME)
#: Why no chain runs for a target whose conf was not written, by the target, then the cause:
#: the unit's StatusText, and after the unit's name its journal line (NOT_RENDERED).
NOT_WRITTEN = "the chain was not written for {} ({}); switch it off and on again"
NOT_RENDERED = f"{NAME}: {NOT_WRITTEN}"
CHAIN_ENDED = f"{NAME}: the chain ended ({{}})"
#: By the unit's name, then how pw-dump ended.
MONITOR_ENDED = "{}: pw-dump --monitor ended ({})"
#: By the unit's name, then the cause.
NOT_NOTIFIED = "{}: its status was not sent to systemd ({})"
#: By the unit's name, then runtime_folder.refusal's words.
FOLDER_REFUSED = "{}: {}"
#: Where systemd takes a service's notices (sd_notify(3)): set only with NotifyAccess=, which
#: a unit installed before it lacks. That systemd then shows STATUS= from the main process as
#: StatusText is from systemd.service(5), not yet checked on this unit.
NOTIFY_SOCKET = "NOTIFY_SOCKET"


def target(objects: Sequence[object], picked: str | None) -> spatial.Target | None:
    """Where the chain plays for pw-dump's `objects` and the headset `picked`: into Gearbench
    Game while the sink of that name gearbench-outputs.service made is there
    (pipewire_unit.made_in), since Outputs plays to the same headset and the balance and
    Game's volume then apply to spatial sound too; else headset_target's. None for no
    headset."""
    aimed = headset_target(objects, picked)
    if aimed is not None and pipewire_unit.made_in(objects, outputs.GAME.sink, outputs.UNIT):
        return spatial.Target(aimed.headset, outputs.GAME.sink,
                              f"the {aimed.headset.name}, through {outputs.GAME.description}")
    return aimed


def headset_target(objects: Sequence[object], picked: str | None) -> spatial.Target | None:
    """Where the chain plays on the headset for pw-dump's `objects` and the headset `picked`:
    pipewire_headsets.pick's headset, at its output, or at its equalizer's sink while that equalizer
    runs (running_eq), since spatial sound goes through the equalizer then; None for no
    headset."""
    chosen = pipewire_headsets.pick(pipewire_headsets.present(objects), picked)
    if chosen is None:
        return None
    headset, output = chosen
    if (eq := running_eq(objects, headset)) is not None:
        return spatial.Target(headset, eq.sink, f"the {headset.name}, through {eq.description}")
    return spatial.Target(headset, output, headset.plays_on)


def running_eq(objects: Sequence[object], headset: pipewire_headsets.Headset) -> EqWords | None:
    """The equalizer g432_eq runs for `headset`, the G432 or another of its family, while
    pw-dump's `objects` hold the sink its unit's process made (pipewire_unit.made_in), else
    None: the headset's sound goes through that sink then, so it stands for the headset's
    output."""
    eq = EQ_WORDS.get(headset.key)
    return eq if eq is not None and pipewire_unit.made_in(objects, eq.sink, eq.unit) else None


def say(line: str) -> None:
    """A line for the unit's journal, escaped: a path or a program's words it quotes can hold a
    newline, where journald ends the record and reads a `<N>` after it as a priority."""
    print(wording.escaped(line), file=sys.stderr, flush=True)


def status(name: str, line: str) -> None:
    """`line` as the StatusText of the unit `name`, whose process this is, "" for none:
    sd_notify(3)'s STATUS=, sent to $NOTIFY_SOCKET, an abstract socket where its name starts
    with @. Nothing without the variable; a send that fails is said in the journal under
    `name`."""
    address = os.environ.get(NOTIFY_SOCKET)
    if not address:
        return
    if address.startswith("@"):
        address = "\0" + address[1:]
    # Escaped: a newline would end STATUS= and start another of systemd's assignments.
    notice = f"STATUS={wording.escaped(line)}".encode()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.sendto(notice, address)
    except OSError as err:
        say(NOT_NOTIFIED.format(name, wording.reason(err)))


def stop(chain: subprocess.Popen) -> None:
    """End a running chain and wait for it: SIGTERM, then SIGKILL past STOP_WAIT."""
    chain.terminate()
    try:
        chain.wait(STOP_WAIT)
    except subprocess.TimeoutExpired:
        chain.kill()
        chain.wait()


def chain_ended(process: subprocess.Popen | None) -> str | None:
    """How a chain's `process` ended, once it ended on its own, else None."""
    status = None if process is None else process.poll()
    return None if status is None else wording.ended("pipewire", status)


def start_chain(spawn: Callable[..., subprocess.Popen], conf: Path
                ) -> tuple[subprocess.Popen | None, str | None]:
    """Run `pipewire -c conf` through `spawn`: the process, None where it did not run, and
    how it ended when it did not run or ended within START_WAIT of its start, else None.
    Exec'd as this process's own child, with no shell between: a chain's nodes are known by
    the process that made them, a child of the unit's MainPID (spatial.read_state) or the
    Outputs unit's own child (pipewire_unit.owned_nodes)."""
    try:
        process = spawn([pipewire_client.PIPEWIRE, "-c", str(conf)])
    except OSError as err:
        return None, str(pipewire_client.not_run("pipewire", err))
    try:
        return process, wording.ended("pipewire", process.wait(START_WAIT))
    except subprocess.TimeoutExpired:
        return process, None


class Chain:
    """The chain as follow runs it: the process, the target it plays to, the conf's path,
    the headset picked, and how it starts one."""

    def __init__(self, conf: Path, picked: str | None,
                 spawn: Callable[..., subprocess.Popen]) -> None:
        self.conf = conf
        self.picked = picked
        self.spawn = spawn
        self.process: subprocess.Popen | None = None
        self.aimed: spatial.Target | None = None

    def ended(self) -> str | None:
        """How the running chain ended, once it ended on its own, else None."""
        return chain_ended(self.process)

    def aim(self, aimed: spatial.Target | None) -> str | None:
        """Run the chain for `aimed`, stopping the one for the last target first: how it
        ended when it did not run or ended within START_WAIT of its start, else None."""
        if aimed == self.aimed:
            return None
        if self.process is not None:
            stop(self.process)
            self.process = None
        self.aimed = aimed
        # Cleared before the render reads the room: set_room takes a StatusText for no chain
        # coming, and the last target's would skip the wait for this one's sink.
        status(NAME, "")
        if aimed is None:
            say(NO_HEADSET)
            return None
        try:
            spatial.replace_conf(self.conf, spatial.render(spatial.load_choices(), aimed))
        except (pipewire_client.StepError, OSError) as err:
            # Kept as aimed: a target that cannot be written is tried again only once
            # it changes. An OSError that names no file came from writing the conf.
            where = f"{err.filename or self.conf}: " if isinstance(err, OSError) else ""
            cause = f"{where}{wording.reason(err)}"
            say(NOT_RENDERED.format(aimed.words, cause))
            status(NAME, NOT_WRITTEN.format(aimed.words, cause))
            return None
        self.process, ended = start_chain(self.spawn, self.conf)
        if ended is None:
            say(PLAYS.format(aimed.words))
        return ended


def take(found: list | None, objects: dict[int, dict], chain: Chain) -> str | None:
    """Fold one print of pw-dump --monitor into `objects` and keep `chain` on its target:
    how the chain ended once it ends, else None."""
    if (ended := chain.ended()) is not None:
        return ended
    if found is None:
        say(UNREAD.format(pipewire_client.NOT_A_PRINT))
        return None
    pipewire_client.merge(objects, found)
    try:
        aimed = pipewire_client.shaped(lambda known: target(known, chain.picked),
                                       list(objects.values()))
    except pipewire_client.StepError as err:
        say(UNREAD.format(err))
        return None
    return chain.aim(aimed)


def follow(monitor: IO[bytes], chain: Chain) -> str | None:
    """Keep `chain` on the target of each print of `monitor`, pw-dump --monitor's stdout,
    looking at a running chain every LOOK_EVERY between prints: how the chain ended once it
    ends, or None once the monitor ends."""
    objects: dict[int, dict] = {}
    reader = pipewire_client.Prints()
    with selectors.DefaultSelector() as selector:
        selector.register(monitor, selectors.EVENT_READ)
        while True:
            if not selector.select(None if chain.process is None else LOOK_EVERY):
                if (ended := chain.ended()) is not None:
                    return ended
                continue
            chunk = os.read(monitor.fileno(), pipewire_client.CHUNK)
            if not chunk:
                return None
            for found in reader.feed_bytes(chunk):
                if (ended := take(found, objects, chain)) is not None:
                    return ended


def refuses_folder(name: str, what: str, conf: Path) -> bool:
    """Whether the unit `name` may not run `what`'s chain from `conf`, as runtime_folder.refusal
    finds of the conf's folder, said in the journal: the unit's %t, which XDG_RUNTIME_DIR need
    not name. Another account that could write there could give `pipewire -c` a conf or a
    PATH.d drop-in of its own, and a conf can run any program (context.exec)."""
    refused = runtime_folder.refusal(conf.parent, what)
    if refused is not None:
        say(FOLDER_REFUSED.format(name, refused))
    return refused is not None


def main(argv: Sequence[str] | None = None,
         spawn: Callable[..., subprocess.Popen] = subprocess.Popen) -> int:
    """`python3 -m gearbench.spatial_unit run PATH`, the unit's process: follow until pw-dump
    --monitor ends, as at PipeWire's stop, or the chain ends, either of which ends the unit
    as failed, 1, as does a folder of PATH it refuses (refuses_folder)."""
    args = sys.argv[1:] if argv is None else list(argv)
    if len(args) != 2 or args[0] != "run":
        print("usage: python3 -m gearbench.spatial_unit run PATH", file=sys.stderr)
        return 2
    conf = Path(args[1])
    if refuses_folder(NAME, spatial.DESCRIPTION, conf):
        return 1
    chain = Chain(conf, spatial.load_choices().headset, spawn)
    return monitored(spawn, NAME, lambda monitor: follow(monitor, chain), lambda: chain.process,
                     CHAIN_ENDED)


def monitored(spawn: Callable[..., subprocess.Popen], name: str,
              served: Callable[[IO[bytes]], str | None],
              chain: Callable[[], subprocess.Popen | None], chain_ended: str) -> int:
    """Run pw-dump --monitor through `spawn` and hand its stdout to `served` until it or the
    chain ends; stop the chain still running and say why under the unit `name`, a chain's
    end in `chain_ended`'s words: 1, the unit failed, for systemd's Restart=on-failure."""
    try:
        with spawn(pipewire_client.MONITOR, stdout=subprocess.PIPE) as monitor:
            ended = served(cast("IO[bytes]", monitor.stdout))
            if (process := chain()) is not None:
                stop(process)
            if ended is None:
                say(MONITOR_ENDED.format(name, wording.ended("pw-dump", monitor.wait())))
            else:
                say(chain_ended.format(ended))
                monitor.terminate()
    except OSError as err:
        say(MONITOR_ENDED.format(name, pipewire_client.not_run("pw-dump", err)))
    return 1


if __name__ == "__main__":
    sys.exit(main())
