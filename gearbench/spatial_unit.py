"""gearbench-spatial.service's process: Gearbench Spatial's chain, kept on the headset it plays
to, `python3 -m gearbench.spatial_unit run PATH`.

It follows PipeWire with `pw-dump --monitor` and, at each print, takes the target, the
headset and the node the chain plays to (`target`). When the target changes it stops the
chain, renders the conf at PATH for the new one and starts it, `pipewire -c PATH`; with no
headset in PipeWire it runs no chain, so the sink is gone. The headset picked is read from
spatial.json once, at the start, and a change of it or of the head restarts the unit
(spatial.set_headset, set_head); each render reads the head and the room then, so a room
set live since is kept.
The chain ending on its own, or pw-dump's end, ends the process with 1, and systemd starts
it again (Restart=on-failure) up to the unit's start limit. Why no chain runs for a headset
that is there goes to the unit's StatusText as well (`status`), which spatial.summary shows.
Standard library only, never run in the daemon; the programs go through `main`'s `spawn`.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import IO, cast

from . import pipewire_client, spatial, wording
from .g432_eq_words import WORDS as EQ_WORDS

NAME = spatial.UNIT.removesuffix(".service")
#: How long a chain just started may take to fail, in seconds, before it counts as running:
#: one that cannot load its conf exits before it makes a node, which no print would show.
#: Assumed, not timed.
START_WAIT = 0.5
#: How long a stopped chain gets to exit on SIGTERM before SIGKILL, in seconds.
STOP_WAIT = 5.0
#: The journal's lines.
PLAYS = f"{NAME}: plays to {{}}"
NO_HEADSET = (f"{NAME}: no headset's output in PipeWire, so no chain runs until "
              f"{spatial.ANY_OUTPUT} is there")
UNREAD = pipewire_client.UNREAD.format(NAME)
#: Why no chain runs for a target whose conf was not written, by the target, then the cause:
#: the unit's StatusText, and after the unit's name its journal line (NOT_RENDERED).
NOT_WRITTEN = "the chain was not written for {} ({}); switch it off and on again"
NOT_RENDERED = f"{NAME}: {NOT_WRITTEN}"
CHAIN_ENDED = f"{NAME}: the chain ended ({{}})"
MONITOR_ENDED = f"{NAME}: pw-dump --monitor ended ({{}})"
NOT_NOTIFIED = f"{NAME}: its status was not sent to systemd ({{}})"
#: Where systemd takes a service's notices (sd_notify(3)): set only with NotifyAccess=, which
#: a unit installed before it lacks. That systemd then shows STATUS= from the main process as
#: StatusText is from systemd.service(5), not yet checked on this unit.
NOTIFY_SOCKET = "NOTIFY_SOCKET"


def made_in(objects: Sequence[object], sink: str, unit: str) -> bool:
    """Whether pw-dump's `objects` hold an Audio/Sink named `sink` made by a client whose
    process runs in systemd's `unit` (pipewire_client.unit_of_process), by the pid PipeWire took
    from the client's socket (pipewire_client.node_pids): any client can name a sink so, a
    sandboxed app and pipewire-pulse for a pulse client among them, and a sink of no client
    is no unit's."""
    return any(props.get("node.name") == sink and props.get("media.class") == "Audio/Sink"
               and pipewire_client.unit_of_process(pid) == unit
               for _node, _info, props, pid in pipewire_client.node_pids(objects))


def target(objects: Sequence[object], picked: str | None) -> spatial.Target | None:
    """Where the chain plays for pw-dump's `objects` and the headset `picked`: spatial.pick's
    headset, at its output, or for the G432, or another of its family whose equalizer
    g432_eq runs, at that equalizer's sink while `objects` hold the one its unit's process
    made (made_in), since spatial sound goes through the equalizer while it runs;
    None for no headset."""
    chosen = spatial.pick(spatial.present(objects), picked)
    if chosen is None:
        return None
    headset, output = chosen
    eq = EQ_WORDS.get(headset.key)
    if eq is not None and made_in(objects, eq.sink, eq.unit):
        return spatial.Target(headset, eq.sink, f"the {headset.name}, through {eq.description}")
    return spatial.Target(headset, output, headset.plays_on)


def say(line: str) -> None:
    """A line for the unit's journal."""
    print(line, file=sys.stderr, flush=True)


def status(line: str) -> None:
    """`line` as the unit's StatusText, "" for none: sd_notify(3)'s STATUS=, sent to
    $NOTIFY_SOCKET, an abstract socket where its name starts with @. Nothing without the
    variable; a send that fails is said in the journal."""
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
        say(NOT_NOTIFIED.format(wording.reason(err)))


def stop(chain: subprocess.Popen) -> None:
    """End a running chain and wait for it: SIGTERM, then SIGKILL past STOP_WAIT."""
    chain.terminate()
    try:
        chain.wait(STOP_WAIT)
    except subprocess.TimeoutExpired:
        chain.kill()
        chain.wait()


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
        status = None if self.process is None else self.process.poll()
        return None if status is None else wording.ended("pipewire", status)

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
        status("")
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
            status(NOT_WRITTEN.format(aimed.words, cause))
            return None
        try:
            # Exec'd as this process's own child, with no shell between: spatial.read_state
            # knows the chain's nodes as those made by a child of the unit's MainPID.
            self.process = self.spawn([pipewire_client.PIPEWIRE, "-c", str(self.conf)])
        except OSError as err:
            return str(pipewire_client.not_run("pipewire", err))
        try:
            return wording.ended("pipewire", self.process.wait(START_WAIT))
        except subprocess.TimeoutExpired:
            say(PLAYS.format(aimed.words))
            return None


def follow(lines: Iterable[str], chain: Chain) -> str | None:
    """Keep `chain` on the target of each print of pw-dump --monitor's `lines`: how the
    chain ended once it ends, or None once the lines end."""
    objects: dict[int, dict] = {}
    for found in pipewire_client.printed(lines):
        if (ended := chain.ended()) is not None:
            return ended
        if found is None:
            say(UNREAD.format(pipewire_client.NOT_A_PRINT))
            continue
        pipewire_client.merge(objects, found)
        try:
            aimed = pipewire_client.shaped(lambda known: target(known, chain.picked),
                                           list(objects.values()))
        except pipewire_client.StepError as err:
            say(UNREAD.format(err))
            continue
        if (ended := chain.aim(aimed)) is not None:
            return ended
    return None


def main(argv: Sequence[str] | None = None,
         spawn: Callable[..., subprocess.Popen] = subprocess.Popen) -> int:
    """`python3 -m gearbench.spatial_unit run PATH`, the unit's process: follow until pw-dump
    --monitor ends, as at PipeWire's stop, or the chain ends, either of which ends the unit
    as failed, 1."""
    args = sys.argv[1:] if argv is None else list(argv)
    if len(args) != 2 or args[0] != "run":
        print("usage: python3 -m gearbench.spatial_unit run PATH", file=sys.stderr)
        return 2
    chain = Chain(Path(args[1]), spatial.load_choices().headset, spawn)
    try:
        with spawn(pipewire_client.MONITOR, stdout=subprocess.PIPE, encoding="utf-8",
                   errors="replace") as monitor:
            ended = follow(cast("IO[str]", monitor.stdout), chain)
            if chain.process is not None:
                stop(chain.process)
            if ended is None:
                say(MONITOR_ENDED.format(wording.ended("pw-dump", monitor.wait())))
            else:
                say(CHAIN_ENDED.format(ended))
                monitor.terminate()
    except OSError as err:
        say(MONITOR_ENDED.format(pipewire_client.not_run("pw-dump", err)))
    return 1

if __name__ == "__main__":
    sys.exit(main())
