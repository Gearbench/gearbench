"""Noise suppression for the Logitech G432's microphone: PipeWire's echo-cancel module with its
webrtc plugin, which makes Logitech G432 Microphone, a cleaned-up input a game or a chat app
can pick, from the G432's own microphone.

Off by default. On is `gearbench-g432-mic.service` running: a transient unit that
`systemd-run --user` makes for `render`'s conf, as Logitech G432 Equalizer's is
(g432_eq), so it is off again after a logout, and off stops it, which removes the input.
Gearbench never makes the input the default. Standard library only, never run in the
daemon; every outside program goes through a `run` argument and `spatial.run_program`, and
a program's failure is a `spatial.SpatialError` in words; writing the conf can raise OSError.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from . import config, g432_eq, pipewire_unit, spatial, wording

UNIT = "gearbench-g432-mic.service"
#: The input's node, the stream that captures the G432's microphone for it, and the stream
#: that reads the default output's monitor for the echo canceler.
SOURCE = "gearbench-g432-mic"
CAPTURE = "gearbench-g432-mic.capture"
MONITOR = "gearbench-g432-mic.monitor"
DESCRIPTION = "Logitech G432 Microphone"
#: What the window calls it.
FEATURE = "Noise suppression"
CONF_NAME = "gearbench-g432-mic.conf"
#: The G432's microphone's media class in PipeWire, which the capture reads.
MIC_CLASS = "Audio/Source"
NO_MIC = ("No Logitech G432 microphone in PipeWire: plug in the headset's USB adapter, then "
          "switch noise suppression on again.")
#: The G432's card in PipeWire on a profile with no input: its output alone, or off.
NO_INPUT = ("The Logitech G432's sound profile has no microphone: pick one with its input in "
            "the sound settings, then switch noise suppression on again.")
#: switch_on's refusal when the unit stopped as it started.
STOPPED = pipewire_unit.STOPPED.format(DESCRIPTION, UNIT)


class MicError(spatial.SpatialError):
    """A step that failed, in words."""


@dataclass(frozen=True)
class State(pipewire_unit.UnitState):
    source_id: int | None = None      # the input, made by the unit's process
    target: str | None = None         # the G432's microphone's node name, None while unplugged
    linked: bool = False              # a link from that microphone to the capture


def find_mic(objects: Sequence[object]) -> str | None:
    """The G432's microphone's node name in pw-dump's objects, None while there is none."""
    props = g432_eq.g432_node(objects, MIC_CLASS)
    return None if props is None else props["node.name"]


def render(target: str) -> str:
    """The input's conf, its capture aimed at `target`, the G432's microphone."""
    if not target or spatial.NOT_IN_CONF.search(target):
        raise MicError(f"the G432's microphone, {wording.escaped(target)}, cannot be named in "
                       "the input's conf")
    return TEMPLATE.format(description=DESCRIPTION, source=SOURCE, capture=CAPTURE,
                           monitor=MONITOR, target=target,
                           client_context=spatial.CLIENT_CONTEXT,
                           client_modules=spatial.CLIENT_MODULES)


TEMPLATE = """\
# Gearbench: {description}. Gearbench writes this file at each switch-on, aimed at
# the G432's microphone, and a transient user unit, gearbench-g432-mic.service,
# runs it: pipewire -c <this file>.
#
# PipeWire's echo-cancel module with its webrtc plugin, for its noise
# suppression: it captures the G432's microphone and gives what it cleaned as
# an input of its own. Its echo canceler runs too, on the default output's
# monitor (monitor.mode), so the module makes no output of its own.

{client_context}

context.modules = [
{client_modules}

    {{ name = libpipewire-module-echo-cancel
        args = {{
            library.name     = aec/libspa-aec-webrtc
            node.description = "{description}"
            monitor.mode     = true
            # The G432's microphone is mono.
            audio.channels   = 1
            audio.position   = [ MONO ]
            aec.args = {{
                webrtc.noise_suppression = true
                # Off: the microphone keeps the level set for it.
                webrtc.gain_control      = false
            }}
            capture.props = {{
                node.name          = "{capture}"
                node.description   = "{description}"
                target.object      = "{target}"
                # It cleans the G432's microphone or none, never another.
                node.dont-fallback = true
                # The microphone runs only while something records the input.
                node.passive       = true
            }}
            source.props = {{
                node.name        = "{source}"
                node.description = "{description}"
            }}
            # In monitor.mode the stream that reads the default output's monitor.
            sink.props = {{
                node.name        = "{monitor}"
                node.description = "{description}"
            }}
        }}
    }}
]
"""


def conf_path() -> Path:
    """CONF_NAME in the runtime folder: config.private_runtime's, refused as MicError while
    XDG_RUNTIME_DIR is unset or names a folder another account owns or may write in."""
    unset = f"XDG_RUNTIME_DIR is not set, so {DESCRIPTION}'s file has nowhere to go."
    return config.private_runtime(MicError, unset, DESCRIPTION) / CONF_NAME


def graph(objects: Sequence[object], pid: int) -> dict:
    """State's PipeWire fields from pw-dump's objects. The input and its capture are the
    nodes of those names made by the client whose process is `pid`, the unit's MainPID, by
    the pid PipeWire took from its socket, since any client can name a node
    gearbench-g432-mic; the G432's microphone is its card's own node (g432_eq.g432_node)."""
    nodes = pipewire_unit.owned_nodes(objects, pid)
    found = g432_eq.g432_node(objects, MIC_CLASS)
    return {"source_id": nodes.get(SOURCE),
            "target": None if found is None else found["node.name"],
            "linked": pipewire_unit.linked(objects, g432_eq.node_id(objects, found),
                                           nodes.get(CAPTURE))}


def read_state(run: spatial.Run = subprocess.run) -> State:
    """What systemd and PipeWire say now. Never raises."""
    return State(**pipewire_unit.read_fields(run, UNIT, graph))


def summary(state: State) -> tuple[str, str]:
    """(kind, words) for `state`: the unit's own (pipewire_unit.unit_summary), else the
    first condition below that holds."""
    if (unit := pipewire_unit.unit_summary(state)) is not None:
        return unit
    if state.source_id is None:
        return "starting", "starting"
    if state.target is None:
        return "waiting", "on, waiting for the G432's microphone"
    if not state.linked:
        return "unlinked", "on, but not linked to the G432's microphone"
    return "ready", f"on: pick {DESCRIPTION} as the input"


def describe(state: State) -> list[tuple[str, str]]:
    """(label, value) rows for the CLI: the state, the input and the microphone it cleans."""
    return [("noise suppression", summary(state)[1]),
            ("input", f"{DESCRIPTION} ({SOURCE})"),
            ("microphone", state.target or "the G432's, not in PipeWire")]


def switch_on(run: spatial.Run = subprocess.run) -> State | None:
    """Write the conf, aimed at the G432's microphone, start the unit and wait up to
    spatial.SETTLE while it starts (pipewire_unit.settle_start): the state it settled on;
    None, with nothing done, while it runs or starts. MicError while the G432's microphone
    is not in PipeWire, for a runtime folder conf_path refuses, and STOPPED when the unit
    stopped as it started."""
    if pipewire_unit.running(run, UNIT):
        return None
    objects = spatial.dump_objects(run)
    target = spatial.shaped(find_mic, objects)
    if target is None:
        raise MicError(NO_INPUT if g432_eq.g432_cards(objects) else NO_MIC)
    conf = conf_path()
    # Mode 0600 (replace_file's mkstemp), and replaced whole, so PipeWire never reads half.
    config.replace_file(conf, render(target).encode("utf-8"), f".{CONF_NAME}-", "")
    spatial.run_program(run, pipewire_unit.pipewire_argv(UNIT, DESCRIPTION, conf))
    return pipewire_unit.settle_start(run, UNIT, lambda: read_state(run=run), summary,
                                      MicError(STOPPED))


def switch_off(run: spatial.Run = subprocess.run) -> None:
    """Stop the unit, which removes the input; a unit not loaded is off already."""
    pipewire_unit.stop(run, UNIT)
