"""Noise suppression for a headset's microphone: PipeWire's echo-cancel module with its webrtc
plugin, which makes a cleaned-up input a game or a chat app can pick from the headset's own
microphone, G432 Microphone (Noise Filter) from the G432's. Every headset Gearbench Spatial
plays to has it (HEADSETS) but those whose noise suppression is in the headset
(OWN_SUPPRESSION).

Off by default. On is `gearbench-g432-mic.service` running: a transient unit that
`systemd-run --user` makes for `render`'s conf, as Logitech G432 Equalizer's is
(g432_eq), so it is off again after a logout, and off stops it, which removes the input.
On makes the input the default input and off gives back the one the user had, saved at the
switch-on (saved_input, back_to), unless the user picked another since, as Outputs does with
the default output (the owner's decision of 2026-10-28). Another headset's is the G432's under its own
names and sentences (`words`), Astro A50 X Microphone (Noise Filter) and
`gearbench-a50x-mic.service` on the A50 X: the functions take the headset by its key in
pipewire_headsets.HEADSET_KEYED as `key`, and G432_KEY and G432_WORDS are the G432's. A
headset Gearbench ships no rule for, which plays to its card's first output
(pipewire_headsets.first_output), is refused at switch-on while its card lists more than
one input, each named, until a device report shows which is its microphone.

The test counts COUNT_IN, then records TEST_SECONDS of the cleaned input with input_meter's
pw-record into memory (Take, record), counting down the seconds left, then plays them back to
the default output with pw-play (Playback, play_back) from an anonymous file in memory:
nothing is written to disk. It runs only while the input is ready (test_refusal).

Standard library only, never run in the daemon; every outside program goes through a `run`
argument and `pipewire_client.run_program`, or for the test a `spawn` one, and a program's
failure is a `pipewire_client.StepError` in words; writing the conf can raise OSError.
"""

from __future__ import annotations

import contextlib
import json
import os
import select
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from . import (
    a50x,
    g432,
    headset_audio,
    input_meter,
    pipewire_client,
    pipewire_headsets,
    pipewire_unit,
    runtime_folder,
    settings_files,
    spatial,
    wording,
)
from .centurion_features import noise_reduction
from .report import SEND_REPORT

#: What the window calls it.
FEATURE = "Noise suppression"
#: The input's name in GNOME's Sound settings and every app's list of inputs, by the
#: headset's short name, so it is told apart from the microphone it is made from (the
#: owner's decision of 2026-10-28).
INPUT_NAME = "{} Microphone (Noise Filter)"
#: The default input's notes: switch_on's, by the input's name, then why not; switch_off's,
#: by the input given back as GNOME lists it, then why not; for one given back or picked
#: since that is away, by its name, then why it was not kept; and a read that failed before
#: the give-back, by why. In a note of an input not picked, given back or kept, the cause
#: follows "; ", so a toast too short for the note shows what failed (style.toast_clause),
#: and each of those and the read's goes out as a pipewire_client.FailedStep.
SELECTED = "{} is the default input"
NOT_SELECTED = "{} is not the default input; {}"
GIVEN_BACK = "{} is the default input again"
NOT_GIVEN_BACK = "{} is not the default input again; {}"
UNTIL_BACK = "{} is the default input again once it is back"
NOT_KEPT = ("{} was not kept as the default input for its return; {}; pick it as your input "
            "once it is back")
DEFAULT_UNREAD = "the default input was not given back; {}"
#: The media classes of the nodes WirePlumber 0.5.13 may make the default input, a virtual
#: input and an output by its monitor among them (default-nodes/rescan.lua, read in its
#: source; a virtual input was the default input, and given back, on 2026-10-30).
DEFAULT_INPUT_CLASSES = ("Audio/Source", "Audio/Source/Virtual", "Audio/Duplex", "Audio/Sink")
#: The media class of a headset's input: PipeWire 1.6.2's echo-cancel module gives its source
#: this one where the conf sets none, as TEMPLATE does (module-echo-cancel.c, read in its
#: source; pw-dump gave the G432's input this class on 2026-10-30).
INPUT_CLASS = "Audio/Source"
#: The headsets whose noise suppression is in the headset, each by its key with its name: the
#: Centurion records with 0x0602 (a50x.MODELS).
OWN_SUPPRESSION = {model.key: model.name for model in a50x.MODELS.values()
                   if model.setting_of(noise_reduction.ID) is not None}
#: Every other headset Gearbench Spatial plays to, in its order (pipewire_headsets.HEADSETS).
HEADSETS = tuple(headset for headset in pipewire_headsets.HEADSETS
                 if headset.key not in OWN_SUPPRESSION)
#: The conf's word on the microphone's channels, by the headset's short name: read off a
#: model of the G432's family Gearbench has run on (`tested`), mapped to one channel by the
#: profile set Gearbench gives the card (its mono-chat), else assumed.
MONO_READ = "The {}'s microphone is mono"
MONO_MAPPED = "The {}'s microphone is mono, as its profile set maps it"
MONO_ASSUMED = "The {}'s microphone is mono, assumed from the G432's"
#: The profile sets of Gearbench's that map a headset's microphone, the A50 Gen 4's and the
#: A50 X's.
MAPPED_SETS = (pipewire_headsets.PROFILE_SET, pipewire_headsets.A50_X_PROFILE_SET)
#: The test's count before it records, in numbers said one each COUNT_STEP seconds, so the
#: user is speaking as the recording starts: "Recording in 3, 2, 1" (the owner's decision of
#: 2026-10-28). Each front end shows the count and waits it out itself.
COUNT_IN = 3
COUNT_STEP = 1
#: The test's length, and its samples' bytes at input_meter's format, s16 at 48000 Hz on one
#: channel.
TEST_SECONDS = 5
BYTES_A_SECOND = input_meter.RATE * input_meter.SAMPLE_BYTES * input_meter.CHANNELS
TEST_BYTES = TEST_SECONDS * BYTES_A_SECOND
#: How long the recording waits past TEST_SECONDS for its samples, and the playback past its
#: own length for pw-play to end: for the streams' start and link and pw-play's drain; a
#: margin, not measured.
TEST_SLACK = 3
#: How long the recording may take from pw-record's start: the test's length and its slack.
RECORD_SECONDS = TEST_SECONDS + TEST_SLACK
#: The recording's stream, input_meter's own but named for the test, which records the
#: cleaned input or none; the playback's, to the default output; and the player.
TEST_STREAM = {**input_meter.PROPERTIES, "node.name": "gearbench-mic-test",
               "media.name": "Gearbench microphone test"}
PLAYBACK = {"application.name": "Gearbench", "node.name": "gearbench-mic-test.playback",
            "media.name": "Gearbench microphone test"}
PLAYER = "pw-play"
#: The anonymous file the samples are played from, by its name in /proc.
MEMORY_FILE = "gearbench-mic-test"
#: The playback's refusals: it outran its time, by the seconds it had to a tenth, and the samples
#: found no room in memory, by the cause.
NOT_ENDED = f"{PLAYER} did not end within {{:g}} s, so it was stopped"
NO_MEMORY = "the recording could not be held in memory: {}"


@dataclass(frozen=True)
class Facts:
    """What a headset's sentences take: its short name, which the input's is made from
    (INPUT_NAME), the words for what plugs into the computer, and its MONO_* sentence."""

    short: str
    plugged_by: str
    mono: str


def facts(headset: pipewire_headsets.Headset) -> Facts:
    """`headset`'s Facts: a model of the G432's family from its record (g432.MODEL_KEYED), its
    microphone read where Gearbench has run on one; any other by its name, plugged in by any of
    its records' dongles (a50x.MODELS), and the A50 Gen 4, which has none there, by its base
    station, the A50 X's record's word for its own."""
    if (model := g432.MODEL_KEYED.get(headset.key)) is not None:
        return Facts(model.short, model.plugged_by, MONO_READ if model.tested else MONO_ASSUMED)
    dongles = [model.dongle for model in a50x.MODELS.values() if model.key == headset.key]
    return Facts(headset.name, wording.word_list(dongles or [a50x.A50_X.dongle]),
                 MONO_MAPPED if headset.profile_set in MAPPED_SETS else MONO_ASSUMED)


@dataclass(frozen=True)
class Words:
    """A headset's noise suppression's names and sentences (`words`). A {} is put in where the
    sentence is said."""

    unit: str
    #: The input's node, the stream that captures the headset's microphone for it, and the
    #: stream that reads an output's monitor for the echo canceler: the headset's own (heard),
    #: else the default output's.
    source: str
    capture: str
    monitor: str
    description: str
    #: The conf's file name in the runtime folder, and the one of the input saved at the
    #: switch-on (save_input).
    conf_name: str
    saved_name: str
    short: str
    #: switch_on's refusals: no microphone of the headset's in PipeWire, its card on a
    #: profile with no input (its output alone, or off), more than one input on a card whose
    #: microphone is not known, by their names, a runtime folder unset or relative, and the
    #: unit stopped as it started.
    no_mic: str
    no_input: str
    two_inputs: str
    no_runtime: str
    stopped: str
    #: render's refusals of the microphone's node name and of the output's.
    not_named: str
    output_not_named: str
    #: The test's refusals: the input not ready, by the summary's words; no sound before
    #: pw-record ended by itself, by its words; and none in the test's time, at which
    #: pw-record was stopped (Take.no_sound).
    untestable: str
    no_sound: str
    silent: str
    #: summary's words while the unit runs, and describe's for a microphone not in PipeWire.
    waiting: str
    unlinked: str
    ready: str
    absent: str
    #: The conf's word on the microphone's channels.
    mono: str


def words(headset: pipewire_headsets.Headset) -> Words:
    """`headset`'s noise suppression's names and sentences."""
    key, named = headset.key, facts(headset)
    short = named.short
    unit, input_name = f"gearbench-{key}-mic.service", INPUT_NAME.format(short)
    return Words(
        unit=unit, source=f"gearbench-{key}-mic", capture=f"gearbench-{key}-mic.capture",
        monitor=f"gearbench-{key}-mic.monitor", description=input_name,
        conf_name=f"gearbench-{key}-mic.conf", saved_name=f"gearbench-{key}-mic.json",
        short=short,
        no_mic=(f"the {short}'s microphone is not in PipeWire: plug in the headset's "
                f"{named.plugged_by}, then switch noise suppression on again"),
        no_input=(f"the {short}'s {headset_audio.CARD_PROFILE}, has no microphone: pick one "
                  "that has, then switch noise suppression on again"),
        two_inputs=(f"the {short} lists {{}} as inputs, and which is its microphone is not "
                    f"known yet: {SEND_REPORT}"),
        no_runtime=runtime_folder.not_set(f"the file of {input_name} has"),
        stopped=pipewire_unit.STOPPED.format(input_name, unit),
        not_named=f"the {short}'s microphone '{{}}' cannot be named in the input's conf",
        output_not_named=f"the {short}'s output '{{}}' cannot be named in the input's conf",
        untestable=f"{input_name} is not ready to test ({{}}): test once noise suppression "
                   "is on and linked",
        no_sound=f"no sound came from {input_name}: {{}}",
        silent=f"no sound came from {input_name} within {RECORD_SECONDS} s",
        waiting=f"on, waiting for the {short}'s microphone",
        unlinked=f"on, but not linked to the {short}'s microphone",
        ready=f"on, linked to the {short}'s microphone",
        absent=f"the {short}'s, not in PipeWire",
        mono=named.mono.format(short))


#: Each headset's, by its key.
WORDS = {headset.key: words(headset) for headset in HEADSETS}
#: Each headset's input's node name, with the headset's key.
INPUT_KEYS = {named.source: key for key, named in WORDS.items()}
#: The G432's.
G432_KEY = g432.G432_MODEL.key
G432_WORDS = WORDS[G432_KEY]


class MicError(pipewire_client.StepError):
    """A step that failed, in words."""


@dataclass(frozen=True)
class State(pipewire_client.UnitState):
    source_id: int | None = None      # the input, made by the unit's process
    target: str | None = None         # the microphone's node name, None while not in PipeWire
    linked: bool = False              # a link from that microphone to the capture


def aim(objects: Sequence[object], key: str) -> str:
    """The node name of the microphone switch_on aims the capture at, headset_audio.mic_of's,
    for the headset of `key`; MicError while there is none, and for a headset Gearbench ships
    no rule for (pipewire_headsets.Headset.ruled) whose card lists more than one input,
    which names each as GNOME's Sound settings do (pipewire_client.listed_name), quoted and
    escaped."""
    headset, named = pipewire_headsets.HEADSET_KEYED[key], WORDS[key]
    inputs = headset_audio.headset_nodes(objects, headset, headset_audio.MIC_CLASS)
    if not inputs:
        raise MicError(named.no_input if headset_audio.cards_of(objects, headset)
                       else named.no_mic)
    if len(inputs) > 1 and not headset.ruled:
        shown = [f"'{wording.escaped(pipewire_client.listed_name(props))}'" for props in inputs]
        raise MicError(named.two_inputs.format(wording.word_list(shown, "and")))
    return inputs[0]["node.name"]


def heard(objects: Sequence[object], key: str) -> str | None:
    """The node name of the output the echo canceler reads, for the headset of `key`: the
    headset's own (pipewire_headsets.output_of on pipewire_headsets.card_of's card), where
    Gearbench Spatial and Gearbench Game and Media play, through the headset's equalizer while
    it is on, whichever the default output is, and Chat too but on a headset with a Chat
    output of its own (pipewire_headsets.Headset.chat); None while its card has none."""
    headset = pipewire_headsets.HEADSET_KEYED[key]
    card = pipewire_headsets.card_of(headset, objects)
    return None if card is None else pipewire_headsets.output_of(headset, card[1])


def render(target: str, output: str | None, *, key: str) -> str:
    """The input's conf, its capture aimed at `target`, the headset's microphone, and its
    echo canceler's stream at `output`, the headset's output (heard), else at the default
    output's."""
    named = WORDS[key]
    for name, refusal in ((target, named.not_named), (output, named.output_not_named)):
        if name is not None and not pipewire_client.conf_holds(name):
            raise MicError(refusal.format(wording.escaped(name)))
    reads = (MONITOR_DEFAULT if output is None else MONITOR_AIMED).format(
        monitor=named.monitor, description=named.description, output=output, short=named.short,
        stream_class=STREAM_CLASS)
    return TEMPLATE.format(description=named.description, source=named.source,
                           capture=named.capture, target=target, reads=reads,
                           stream_class=STREAM_CLASS,
                           unit=named.unit, short=named.short, mono=named.mono,
                           client_context=pipewire_client.CLIENT_CONTEXT,
                           client_modules=pipewire_client.CLIENT_MODULES)


#: The media class of the module's two streams, the microphone's capture and the output's
#: monitor, as WirePlumber marks its own loopbacks' inner streams (monitors/alsa.lua,
#: monitors/bluez.lua): a desktop's microphone icon shows while a program records, and a
#: program recording counts by its PulseAudio source output, which pipewire-pulse lists for
#: a node of Stream/Input/Audio alone, so these two are none and the icon shows only while
#: a program records the input itself. Relies on pipewire-pulse 1.6.2
#: (module-protocol-pulse/manager.c, pw_manager_object_is_source_output), GNOME Shell 50.1
#: (js/ui/status/volume.js, InputStreamSlider._maybeShowInput, which counts each source
#: output), and WirePlumber 0.5.13 linking a stream by the Input in its class
#: (lib/common-utils.lua mediaClassToDirection, node/create-item.lua), each read in its
#: source. Measured on 2026-11-03: no microphone icon while nothing records, on GNOME 50
#: and 51 and Plasma 6.8.1, and the icon while the test records. With the class the module
#: gives them by default, the two lit the icon the whole time the unit ran (measured
#: 2026-10-28).
STREAM_CLASS = "Stream/Input/Audio/Internal"

#: The capture lingers: WirePlumber 0.5.13 destroys a stream with node.dont-fallback whose
#: target.object is not there unless it has node.linger (linking/find-defined-target.lua,
#: read in its source), and nothing would make it again once the microphone is back. With
#: it, the capture stays while the G432 is away and relinks when it is back (measured on
#: WirePlumber 0.5.13 on 2026-11-03). In a throwaway PipeWire 1.6.2 on 2026-10-18 the conf
#: made the input, the capture linked to a stand-in of the G432's microphone and the
#: monitor's stream, and white noise came through 17.8 dB down, against 0.3 dB with the
#: suppression off.
TEMPLATE = """\
# Gearbench: {description}. Gearbench writes this file at each switch-on, aimed at
# the {short}'s microphone, and a transient user unit, {unit},
# runs it: pipewire -c <this file>.
#
# PipeWire's echo-cancel module with its webrtc plugin, for its noise
# suppression: it captures the {short}'s microphone and gives what it cleaned as
# an input of its own. Its echo canceler runs too, on an output's monitor
# (monitor.mode), so the module makes no output of its own. Its two streams
# are {stream_class}, so no desktop counts them as a program
# recording: its microphone icon shows only while one records this input.

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
                media.class        = "{stream_class}"
                target.object      = "{target}"
                # It cleans the {short}'s microphone or none, never another, and
                # waits for it while it is away.
                node.dont-fallback = true
                node.linger        = true
                # The microphone runs only while something records the input.
                node.passive       = true
            }}
            source.props = {{
                node.name        = "{source}"
                node.description = "{description}"
            }}
{reads}
        }}
    }}
]
"""
#: TEMPLATE's sink.props, the stream that in monitor.mode reads an output's monitor for the
#: echo canceler: the headset's own output (heard), else, with no target, the default
#: output's. Relies on WirePlumber 0.5.13, read in its source: for a stream with
#: stream.capture.sink, which monitor.mode sets on it, a target.object by node name is a
#: sink (linking/find-defined-target.lua, lib/common-utils.lua getTargetDirection) whose
#: monitor ports it links (lib/linking-utils.lua canLink); with none it links the default
#: output's. The release's hardware check reads the links back.
MONITOR_AIMED = """\
            # In monitor.mode the stream that reads an output's monitor for the
            # echo canceler: the {short}'s own, whichever the default output is.
            sink.props = {{
                node.name        = "{monitor}"
                node.description = "{description}"
                media.class      = "{stream_class}"
                target.object    = "{output}"
            }}"""
MONITOR_DEFAULT = """\
            # In monitor.mode the stream that reads the default output's monitor
            # for the echo canceler: the headset's card had no output at the
            # switch-on.
            sink.props = {{
                node.name        = "{monitor}"
                node.description = "{description}"
                media.class      = "{stream_class}"
            }}"""


def conf_path(*, key: str) -> Path:
    """The headset's conf in the runtime folder: runtime_folder.private_runtime's, refused as
    MicError while XDG_RUNTIME_DIR is unset or relative (runtime_folder.runtime_home) or names
    a folder another account could swap for its own (runtime_folder.open_to_others) or one a
    stat on the way cannot look at."""
    named = WORDS[key]
    return (runtime_folder.private_runtime(MicError, named.no_runtime, named.description)
            / named.conf_name)


def inputs_there(objects: Sequence[object]) -> set[str]:
    """The node names in pw-dump's objects of the nodes WirePlumber may make the default
    input (DEFAULT_INPUT_CLASSES), on a route WirePlumber does not hold unavailable
    (headset_audio.route_available), any client's but a headset's input of Gearbench's
    (INPUT_KEYS), which counts only where that headset's unit made it
    (pipewire_unit.made_in): any program can name a node so, as Outputs passes over a sink
    named as Gearbench's (outputs_state.saved_as)."""
    return {name for _node, _info, props in pipewire_client.of_type(objects, "Node")
            if props.get("media.class") in DEFAULT_INPUT_CLASSES
            and headset_audio.route_available(objects, props)
            and isinstance(name := props.get("node.name"), str)
            and (name not in INPUT_KEYS or pipewire_unit.made_in(
                objects, name, WORDS[INPUT_KEYS[name]].unit, media_class=INPUT_CLASS))}


def saved_input(objects: Sequence[object], key: str) -> str | None:
    """The default input to save at the switch-on of the headset of `key`, for pw-dump's
    objects: the first of the user's (pipewire_unit.users_defaults), the one picked, there
    or away, before the one WirePlumber records from meanwhile, but this headset's own input,
    a pick of it a logout left, which stops the unit with nothing given back, and another
    headset's input that its unit has not made (inputs_there), gone with its unit or another
    program's of that name; None for none. After a logout WirePlumber 0.5.13 keeps the input
    as the configured default, gone, and records from the most recent earlier pick that is
    there (default-nodes/state-default-nodes.lua)."""
    there = inputs_there(objects)
    return next((name for name in pipewire_unit.users_defaults(objects, "source")
                 if name != WORDS[key].source and (name not in INPUT_KEYS or name in there)),
                None)


def saved_path(*, key: str) -> Path:
    """The file of the input saved at the switch-on, beside the conf (conf_path), in the
    runtime folder, so a logout takes it with the unit."""
    return conf_path(key=key).with_name(WORDS[key].saved_name)


def save_input(name: str, *, key: str) -> None:
    """`name`, the input to give back at the switch-off, to its file (saved_path),
    atomically and the user's alone. OSError where it is not written."""
    path = saved_path(key=key)
    settings_files.replace_file(path, json.dumps({"input": name}).encode("ascii"),
                                f".{path.name}-", "")


def saved_of(key: str) -> str | None:
    """The input saved at the switch-on of the headset of `key` (save_input); None where
    none is: switched on by a build that saved none, the runtime folder emptied by a logout,
    or a file this module did not write."""
    try:
        data = settings_files.read_json(saved_path(key=key))
    except (OSError, settings_files.ConfigError, MicError):
        return None
    name = data.get("input") if isinstance(data, dict) else None
    return name if isinstance(name, str) and name else None


def back_to(objects: Sequence[object], key: str) -> str | None:
    """The input switch_off gives back for the headset of `key`, for pw-dump's objects: the
    one saved at its switch-on (saved_of); for another headset's input saved there that its
    unit has not made (inputs_there), gone with its unit or another program's of that name,
    the one saved at that one's switch-on, and so on, so two switched off in either order give
    back the input there was before the first; with none saved, or a chain that ends on an
    input of Gearbench's its unit has not made, or on this headset's own, which the
    switch-off removes, the headset's own microphone (headset_audio.mic_of), the one it
    cleans; None while there is none."""
    there, name = inputs_there(objects) - {WORDS[key].source}, saved_of(key)
    for _hop in WORDS:
        other = INPUT_KEYS.get(name) if name is not None else None
        if other is None or name in there:
            break
        name = saved_of(other)
    if name is None or (name in INPUT_KEYS and name not in there):
        return headset_audio.mic_of(objects, pipewire_headsets.HEADSET_KEYED[key])
    return name


def graph(objects: Sequence[object], pid: int, *, key: str) -> dict:
    """State's PipeWire fields from pw-dump's objects. The input and its capture are the
    nodes of those names made by the client whose process is `pid`, the unit's MainPID, by
    the pid PipeWire took from its socket, since any client can give a node those names;
    the headset's microphone is its card's own node, the one the capture aims at while it is
    in PipeWire, else headset_audio.mic_of's (headset_audio.headset_node)."""
    named = WORDS[key]
    nodes = pipewire_unit.owned_nodes(objects, pid)
    found = headset_audio.headset_node(
        objects, pipewire_headsets.HEADSET_KEYED[key], headset_audio.MIC_CLASS,
        aimed=pipewire_unit.aimed_at(objects, pid, named.capture))
    return {"source_id": nodes.get(named.source),
            "target": None if found is None else found["node.name"],
            "linked": pipewire_unit.linked(objects, headset_audio.node_id(objects, found),
                                           nodes.get(named.capture))}


def read_state(run: pipewire_client.Run = subprocess.run, *, key: str) -> State:
    """What systemd and PipeWire say now. Never raises."""
    return State(**pipewire_unit.read_fields(
        run, WORDS[key].unit, lambda objects, pid: graph(objects, pid, key=key)))


def summary(state: State, *, key: str) -> tuple[str, str]:
    """(kind, words) for `state`: the unit's own (pipewire_unit.unit_summary), else the
    first condition below that holds."""
    if (unit := pipewire_unit.unit_summary(state)) is not None:
        return unit
    named = WORDS[key]
    if state.source_id is None:
        return "starting", "starting"
    if state.target is None:
        return "waiting", named.waiting
    if not state.linked:
        return "unlinked", named.unlinked
    return "ready", named.ready


def describe(state: State, *, key: str) -> list[tuple[str, str]]:
    """(label, value) rows for the CLI: the state, the input and the microphone it cleans."""
    named = WORDS[key]
    return [("noise suppression", summary(state, key=key)[1]),
            ("input", f"{named.description} ({named.source})"),
            ("microphone", state.target or named.absent)]


@dataclass(frozen=True)
class Switched:
    """switch_on's answer: the state the unit settled on, and the notes to say, in words."""

    state: State
    notes: tuple[str, ...]


def switch_on(run: pipewire_client.Run = subprocess.run, *, key: str) -> Switched | None:
    """Write the conf, aimed at the headset's microphone (aim) and its echo canceler at the
    headset's output (heard), save the default input to give back at the switch-off
    (saved_input, else the headset's microphone, for none), start the unit and wait up to
    pipewire_client.SETTLE while it starts (pipewire_unit.settle_start), then make the input
    the default input (pipewire_unit.pick_default): the state it settled on and the pick's
    note; None, with nothing done, while it runs or starts. MicError in words for each
    refusal, Words.stopped when the unit stopped as it started; an OSError where the conf or
    the saved input is not written, before the start."""
    named = WORDS[key]
    if pipewire_unit.running(run, named.unit):
        return None
    target, output, saved = pipewire_client.dumped(
        run, lambda found: (aim(found, key), heard(found, key), saved_input(found, key)))
    conf = conf_path(key=key)
    spatial.replace_conf(conf, render(target, output, key=key))
    save_input(target if saved is None else saved, key=key)
    pipewire_client.run_program(
        run, pipewire_unit.pipewire_argv(named.unit, named.description, conf))
    state = pipewire_unit.settle_start(run, named.unit, lambda: read_state(run=run, key=key),
                                       lambda state: summary(state, key=key),
                                       MicError(named.stopped))
    failed = pipewire_unit.pick_default(run, named.source, "source")
    return Switched(state, (
        SELECTED.format(named.description) if failed is None
        else pipewire_client.FailedStep(NOT_SELECTED.format(named.description, failed)),))


def switch_off(run: pipewire_client.Run = subprocess.run, *, key: str) -> list[str]:
    """While the input is the default input, give back the one switch_on saved (back_to)
    before the unit stops, so a program that records the default moves to it first; one away,
    or on a route WirePlumber holds unavailable (inputs_there), is written as the user's pick
    after the stop (pipewire_unit.default_argv), which WirePlumber makes the default on its
    return, recording from another meanwhile, as Outputs gives back an output away. A pick
    made since that is away (saved_input), which WirePlumber keeps while it records from the
    input meanwhile, is written after the stop in the saved one's place. With another input
    the default, the user's pick since, nothing is given back. Then stop the unit, which
    removes the input; a unit not loaded is off already. A read or a pick that fails is a
    note, and the unit stops all the same; StepError when the stop fails, its words followed
    by the notes said by then. The notes to say."""
    named = WORDS[key]
    notes: list[str] = []
    away = None
    try:
        objects = pipewire_client.dump_objects(run)
        back, since = pipewire_client.shaped(
            lambda found: (back_to(found, key), saved_input(found, key))
            if pipewire_unit.default_named(found, pipewire_unit.default_key("source"))
            == named.source else (None, None), objects)
        if back is not None and back in inputs_there(objects):
            failed = pipewire_unit.pick_default(run, back, "source")
            words = spatial.shown_name(pipewire_client.described(objects, back))
            notes.append(GIVEN_BACK.format(words) if failed is None
                         else pipewire_client.FailedStep(NOT_GIVEN_BACK.format(words, failed)))
            away = since
        else:
            away = since or back
    except pipewire_client.StepError as err:
        notes.append(pipewire_client.FailedStep(DEFAULT_UNREAD.format(err)))
    try:
        pipewire_unit.stop(run, named.unit)
    except pipewire_client.StepError as err:
        # What was given back stays given back while the input runs on: the error says so.
        raise pipewire_client.StepError("; ".join([str(err), *notes])) from None
    if away is not None:
        failed = pipewire_unit.written(run, pipewire_unit.default_argv(away, "source"))
        shown = spatial.shown_name(pipewire_client.described(objects, away))
        notes.append(UNTIL_BACK.format(shown) if failed is None
                     else pipewire_client.FailedStep(NOT_KEPT.format(shown, failed)))
    return notes


def test_refusal(state: State, *, key: str) -> str | None:
    """Why the test cannot run on `state`: the input not ready (summary); None while it
    is."""
    kind, words = summary(state, key=key)
    return None if kind == "ready" else WORDS[key].untestable.format(words)


class Take:
    """A test's recording: input_meter's pw-record on the headset's cleaned input
    (Words.source), its samples held in memory up to TEST_BYTES and none written."""

    def __init__(self, recording: input_meter.Recording, words: Words) -> None:
        self._recording = recording
        #: The headset's names and sentences, which no_sound words its refusal in.
        self._words = words
        self.samples = bytearray()
        #: pw-record had closed the pipe at the last read, as it does as it ends.
        self.closed = False

    @classmethod
    def start(cls, spawn: input_meter.Spawn, *, key: str) -> Take:
        """pw-record started on the input of the headset of `key`
        (input_meter.Recording.start)."""
        named = WORDS[key]
        return cls(input_meter.Recording.start(named.source, spawn, TEST_STREAM), named)

    def fileno(self) -> int:
        """The pipe's end the samples are read from."""
        return self._recording.fileno()

    def read(self) -> bool:
        """What the pipe holds now into `samples`, up to TEST_BYTES: True once they are
        whole or pw-record has closed the pipe."""
        data, self.closed = self._recording.take()
        self.samples += data[:TEST_BYTES - len(self.samples)]
        return self.closed or len(self.samples) >= TEST_BYTES

    def seconds_left(self) -> int:
        """The seconds of samples still to come, as the count shows them: TEST_SECONDS before
        the first, 1 in the last (wording.countdown_seconds)."""
        return wording.countdown_seconds((TEST_BYTES - len(self.samples)) / BYTES_A_SECOND)

    def stop(self) -> str:
        """pw-record ended, and how (input_meter.Recording.stop)."""
        return self._recording.stop()

    def no_sound(self, said: str) -> MicError:
        """The refusal of a take with no samples, `said` being how pw-record ended (stop):
        the headset's Words.no_sound in those words where it ended by itself, else its
        Words.silent, since then they are the words of the stop at the test's time."""
        return MicError(self._words.no_sound.format(said) if self.closed
                        else self._words.silent)


class Playback:
    """pw-play playing a test's samples to the default output."""

    def __init__(self, process: subprocess.Popen, seconds: float) -> None:
        self._process = process
        #: How long it may take: the samples' length and TEST_SLACK.
        self.seconds = seconds

    @classmethod
    def start(cls, samples: bytes, spawn: input_meter.Spawn) -> Playback:
        """pw-play started on `samples`, its stdin an anonymous file in memory that holds them
        (os.memfd_create), never one on disk, closed here once pw-play has its own; MicError
        (NO_MEMORY) where they find no room there, pipewire_client.not_run's refusal where
        pw-play does not start."""
        with contextlib.ExitStack() as stack:
            try:
                # Unbuffered: a buffer would hold a short take's samples past this try, and
                # the close at the stack's end would raise its failed write over MicError.
                memory = stack.enter_context(open(os.memfd_create(MEMORY_FILE), "w+b",
                                                  buffering=0))
                held = memoryview(samples)
                while held:
                    # A raw write may take part of them; the next raises where memory ran out.
                    held = held[memory.write(held):]
                memory.seek(0)
            except OSError as err:
                raise MicError(NO_MEMORY.format(wording.reason(err))) from None
            try:
                process = spawn(play_argv(), stdin=memory, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE)
            except OSError as err:
                raise pipewire_client.not_run(PLAYER, err) from None
        return cls(process, len(samples) / BYTES_A_SECOND + TEST_SLACK)

    def done(self) -> bool:
        """Whether pw-play has ended."""
        return self._process.poll() is not None

    def finish(self, wait: float = 0.0) -> None:
        """Wait up to `wait` s for pw-play's end, then stop it where it still runs
        (input_meter.ended): MicError where it had to be stopped (NOT_ENDED), StepError in
        its words where it ended other than with 0."""
        try:
            self._process.wait(wait)
        except subprocess.TimeoutExpired:
            self._process.terminate()
            input_meter.ended(self._process, PLAYER)
            raise MicError(NOT_ENDED.format(round(self.seconds, 1))) from None
        words = input_meter.ended(self._process, PLAYER)
        if self._process.returncode != 0:
            raise pipewire_client.StepError(words)


def play_argv() -> list[str]:
    """pw-play's command line: input_meter's raw format from its stdin, "-", to the default
    output, which it plays to with no --target."""
    return [PLAYER, *input_meter.RAW_FORMAT, f"--properties={json.dumps(PLAYBACK)}", "-"]


def record(counted: Callable[[int], object], spawn: input_meter.Spawn = subprocess.Popen, *,
           key: str, clock: Callable[[], float] = time.monotonic) -> bytes:
    """The test's recording, for the CLI, blocking: its samples once whole, pw-record's end,
    or past RECORD_SECONDS what came by then; MicError (Take.no_sound) where none came.
    `counted` is given the seconds left (Take.seconds_left) at the start and at each
    change."""
    take = Take.start(spawn, key=key)
    deadline = clock() + RECORD_SECONDS
    try:
        counted(shown := take.seconds_left())
        while not take.read() and (left := deadline - clock()) > 0:
            if (seconds := take.seconds_left()) != shown:
                counted(shown := seconds)
            select.select([take.fileno()], [], [], left)
    finally:
        said = take.stop()
    if not take.samples:
        raise take.no_sound(said)
    return bytes(take.samples)


def play_back(samples: bytes, spawn: input_meter.Spawn = subprocess.Popen) -> None:
    """`samples` played to the default output, for the CLI, blocking until pw-play ends or
    its time is up (Playback.finish)."""
    playback = Playback.start(samples, spawn)
    playback.finish(playback.seconds)
