"""Noise suppression for the microphone of the Logitech G432 and of each headset of its family:
PipeWire's echo-cancel module with its webrtc plugin, which makes Logitech G432 Microphone, a
cleaned-up input a game or a chat app can pick, from the G432's own microphone.

Off by default. On is `gearbench-g432-mic.service` running: a transient unit that
`systemd-run --user` makes for `render`'s conf, as Logitech G432 Equalizer's is
(g432_eq), so it is off again after a logout, and off stops it, which removes the input.
Gearbench never makes the input the default. Another model's is the G432's under the model's
own names and sentences (`words`), Logitech G733 Microphone and `gearbench-g733-mic.service`:
the functions take the model as `model`, the G432 by default for a caller outside this
module (the ones only this module calls require it), and the module's constants are the
G432's. Standard library only, never run in the daemon; every outside program goes through a
`run` argument and `pipewire_client.run_program`, and a program's failure is a
`pipewire_client.StepError` in words; writing the conf can raise OSError.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from . import pipewire_client, pipewire_unit, runtime_folder, spatial, wording
from .g432 import G432_MODEL, MODELS, HeadsetModel

#: What the window calls it.
FEATURE = "Noise suppression"


@dataclass(frozen=True)
class Words:
    """A model's noise suppression's names and sentences (`words`). A {} is the microphone's
    node name, put in where the sentence is said."""

    unit: str
    #: The input's node, the stream that captures the headset's microphone for it, and the
    #: stream that reads the default output's monitor for the echo canceler.
    source: str
    capture: str
    monitor: str
    description: str
    conf_name: str
    #: switch_on's refusals: no microphone of the headset's in PipeWire, its card on a
    #: profile with no input (its output alone, or off), a runtime folder unset or relative,
    #: and the unit stopped as it started.
    no_mic: str
    no_input: str
    no_runtime: str
    stopped: str
    #: render's refusal of the microphone's node name.
    not_named: str
    #: summary's words while the unit runs, and describe's for a microphone not in PipeWire.
    waiting: str
    unlinked: str
    ready: str
    absent: str
    #: The conf's word on the microphone's channels: the G432's, read off it, or assumed.
    mono: str


def words(model: HeadsetModel) -> Words:
    """`model`'s noise suppression's names and sentences."""
    key, name, short = model.key, model.name, model.short
    unit, input_name = f"gearbench-{key}-mic.service", f"{name} Microphone"
    return Words(
        unit=unit, source=f"gearbench-{key}-mic", capture=f"gearbench-{key}-mic.capture",
        monitor=f"gearbench-{key}-mic.monitor", description=input_name,
        conf_name=f"gearbench-{key}-mic.conf",
        no_mic=(f"the {short}'s microphone is not in PipeWire: plug in the headset's "
                f"{model.plugged_by}, then switch noise suppression on again"),
        no_input=(f"the {short}'s Configuration in GNOME's Sound settings has no microphone: "
                  "pick one that has, then switch noise suppression on again"),
        no_runtime=runtime_folder.not_set(f"{input_name}'s file has"),
        stopped=pipewire_unit.STOPPED.format(input_name, unit),
        not_named=f"the {short}'s microphone '{{}}' cannot be named in the input's conf",
        waiting=f"on, waiting for the {short}'s microphone",
        unlinked=f"on, but not linked to the {short}'s microphone",
        ready=f"on, linked to the {short}'s microphone",
        absent=f"the {short}'s, not in PipeWire",
        # Assumed on the others, whose cards no source lists.
        mono=f"The {short}'s microphone is mono" + ("" if model.tested
                                                     else ", assumed from the G432's"))


#: Each model's, by its key.
WORDS = {model.key: words(model) for model in MODELS}
#: The G432's, which the tests, the window and the CLI name.
G432_WORDS = WORDS[G432_MODEL.key]
UNIT = G432_WORDS.unit
SOURCE = G432_WORDS.source
CAPTURE = G432_WORDS.capture
MONITOR = G432_WORDS.monitor
DESCRIPTION = G432_WORDS.description
CONF_NAME = G432_WORDS.conf_name
NO_MIC = G432_WORDS.no_mic
NO_INPUT = G432_WORDS.no_input
STOPPED = G432_WORDS.stopped


class MicError(pipewire_client.StepError):
    """A step that failed, in words."""


@dataclass(frozen=True)
class State(pipewire_unit.UnitState):
    source_id: int | None = None      # the input, made by the unit's process
    target: str | None = None         # the microphone's node name, None while not in PipeWire
    linked: bool = False              # a link from that microphone to the capture


def find_mic(objects: Sequence[object], *, model: HeadsetModel = G432_MODEL) -> str | None:
    """The headset's microphone's node name in pw-dump's objects, on the card whose output
    Gearbench Spatial plays to (pipewire_unit.mic_of), None while there is none."""
    return pipewire_unit.mic_of(objects, spatial.HEADSET_KEYED[model.key])


def render(target: str, *, model: HeadsetModel = G432_MODEL) -> str:
    """The input's conf, its capture aimed at `target`, the headset's microphone."""
    named = WORDS[model.key]
    if not target or pipewire_client.NOT_IN_CONF.search(target):
        raise MicError(named.not_named.format(wording.escaped(target)))
    return TEMPLATE.format(description=named.description, source=named.source,
                           capture=named.capture, monitor=named.monitor, target=target,
                           unit=named.unit, short=model.short, mono=named.mono,
                           client_context=pipewire_client.CLIENT_CONTEXT,
                           client_modules=pipewire_client.CLIENT_MODULES)


TEMPLATE = """\
# Gearbench: {description}. Gearbench writes this file at each switch-on, aimed at
# the {short}'s microphone, and a transient user unit, {unit},
# runs it: pipewire -c <this file>.
#
# PipeWire's echo-cancel module with its webrtc plugin, for its noise
# suppression: it captures the {short}'s microphone and gives what it cleaned as
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
            # {mono}.
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
                # It cleans the {short}'s microphone or none, never another.
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


def conf_path(*, model: HeadsetModel) -> Path:
    """The model's conf in the runtime folder: runtime_folder.private_runtime's, refused as
    MicError while XDG_RUNTIME_DIR is unset or relative (runtime_folder.runtime_home) or names
    a folder another account could swap for its own (runtime_folder.open_to_others)."""
    named = WORDS[model.key]
    return (runtime_folder.private_runtime(MicError, named.no_runtime, named.description)
            / named.conf_name)


def graph(objects: Sequence[object], pid: int, *, model: HeadsetModel = G432_MODEL) -> dict:
    """State's PipeWire fields from pw-dump's objects. The input and its capture are the
    nodes of those names made by the client whose process is `pid`, the unit's MainPID, by
    the pid PipeWire took from its socket, since any client can give a node those names;
    the headset's microphone is its card's own node, the one the capture aims at while it is
    in PipeWire, else find_mic's (pipewire_unit.g432_node)."""
    named = WORDS[model.key]
    nodes = pipewire_unit.owned_nodes(objects, pid)
    found = pipewire_unit.g432_node(objects, pipewire_unit.MIC_CLASS, model=model,
                                    aimed=pipewire_unit.aimed_at(objects, pid, named.capture))
    return {"source_id": nodes.get(named.source),
            "target": None if found is None else found["node.name"],
            "linked": pipewire_unit.linked(objects, pipewire_unit.node_id(objects, found),
                                           nodes.get(named.capture))}


def read_state(run: pipewire_client.Run = subprocess.run, *,
               model: HeadsetModel = G432_MODEL) -> State:
    """What systemd and PipeWire say now. Never raises."""
    return State(**pipewire_unit.read_fields(
        run, WORDS[model.key].unit, lambda objects, pid: graph(objects, pid, model=model)))


def summary(state: State, *, model: HeadsetModel = G432_MODEL) -> tuple[str, str]:
    """(kind, words) for `state`: the unit's own (pipewire_unit.unit_summary), else the
    first condition below that holds."""
    if (unit := pipewire_unit.unit_summary(state)) is not None:
        return unit
    named = WORDS[model.key]
    if state.source_id is None:
        return "starting", "starting"
    if state.target is None:
        return "waiting", named.waiting
    if not state.linked:
        return "unlinked", named.unlinked
    return "ready", named.ready


def describe(state: State, *, model: HeadsetModel = G432_MODEL) -> list[tuple[str, str]]:
    """(label, value) rows for the CLI: the state, the input and the microphone it cleans."""
    named = WORDS[model.key]
    return [("noise suppression", summary(state, model=model)[1]),
            ("input", f"{named.description} ({named.source})"),
            ("microphone", state.target or named.absent)]


def switch_on(run: pipewire_client.Run = subprocess.run, *,
              model: HeadsetModel = G432_MODEL) -> State | None:
    """Write the conf, aimed at the headset's microphone, start the unit and wait up to
    pipewire_client.SETTLE while it starts (pipewire_unit.settle_start): the state it settled on;
    None, with nothing done, while it runs or starts. MicError in words for each refusal,
    Words.stopped when the unit stopped as it started."""
    named = WORDS[model.key]
    if pipewire_unit.running(run, named.unit):
        return None
    objects = pipewire_client.dump_objects(run)
    target = pipewire_client.shaped(lambda found: find_mic(found, model=model), objects)
    if target is None:
        raise MicError(named.no_input if pipewire_unit.g432_cards(objects, model=model)
                       else named.no_mic)
    conf = conf_path(model=model)
    spatial.replace_conf(conf, render(target, model=model))
    pipewire_client.run_program(
        run, pipewire_unit.pipewire_argv(named.unit, named.description, conf))
    return pipewire_unit.settle_start(run, named.unit, lambda: read_state(run=run, model=model),
                                      lambda state: summary(state, model=model),
                                      MicError(named.stopped))


def switch_off(run: pipewire_client.Run = subprocess.run, *,
               model: HeadsetModel = G432_MODEL) -> None:
    """Stop the unit, which removes the input; a unit not loaded is off already."""
    pipewire_unit.stop(run, WORDS[model.key].unit)
