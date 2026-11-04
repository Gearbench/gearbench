"""What the window and the CLI show of Outputs, the switches they run, and the Apps rows they
move: the unit's state as systemd and PipeWire give it (read_state, summary, describe), and
one row for each program's streams as WirePlumber keeps their choice of output, by its key
(apps), moved to Gearbench Game, Chat or Media, or back to the default output (move_app).

switch_on saves the default output the user picked to outputs.json while the unit is
stopped (saved_default), starts the unit and makes Gearbench Game the default; switch_off
gives the default back (give_back) before it stops the unit, and writes back the user's pick
after the stop while that output is away (handed_back).

Standard library only, never run in the daemon; every program goes through a `run` argument
and pipewire_client.run_program, and a failure is a pipewire_client.StepError in words.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from . import (
    astro,
    headset_audio,
    outputs,
    pipewire_client,
    pipewire_headsets,
    pipewire_unit,
    spatial,
    spatial_unit,
    wording,
)
from .g432_eq_words import WORDS as EQ_WORDS
from .outputs import GAME
from .outputs_unit import (
    GEARBENCH_PREFIX,
    SINK_OUTPUTS,
    Settings,
    key_of,
    load_settings,
    move_argv,
    names_own_output,
    program_streams,
    save_settings,
    sink_there,
    standing_default,
    text_of,
)

SHOW = pipewire_client.systemctl(spatial.UNIT_PROPERTIES, "show", outputs.UNIT)
#: A move's choices beside each output's key: no output of its own, so the default's.
DEFAULT = "default"
CHOICES = (*outputs.OUTPUT_KEYED, DEFAULT)
#: The media role whose key every music player shares.
MUSIC = "Music"
#: The prop a stream names its program's process by.
BINARY = "application.process.binary"
#: The application.name a library gives a program's streams, with the word an App's title
#: adds to the program's process. Relies on WebRTC's PulseAudio output naming each call it
#: plays "WEBRTC VoiceEngine", whatever the program (its
#: modules/audio_device/linux/audio_device_pulse_linux.cc), as Discord's call was named
#: beside Discord's own "discord" (measured 2026-10-28).
LIBRARY_WORDS = {"WEBRTC VoiceEngine": "voice"}
LIBRARY_TITLE = "{} ({})"
#: An App's notes: the other programs that share its key, and the music role's. Each is a
#: lowercase fragment, as the CLI's row writes it after "; "; the window's row capitalizes
#: it where it shows it (gui/outputs_group.app_line).
SHARED = "shared with {}"
MUSIC_NOTE = "moves every music player"
#: A stream that names its output in its props: the program asked for it, or libpulse did
#: from PULSE_SINK, which a game's launch option sets. Relies on pipewire-pulse 1.6.2
#: setting target.object from the one sink name libpulse 17.0 sends either way, so no prop
#: tells the two apart and the words fit both.
SET_IN = "set in its settings or launch option"
#: move_app's refusals, by the key, the program, or the output's description, and for
#: DEFAULT with no default output (NO_DEFAULT); and its note when the streams did not show on
#: their new output within pipewire_client.SETTLE. NOT_THERE's two steps are for Outputs off
#: and for Outputs on but waiting for an output to play to.
NO_APP = "no program plays under {} now: `gearbench-ctl headset-outputs` lists them"
FIXED = "{} plays where its own settings or launch option set it: change it there"
NOT_THERE = ("{} is not in PipeWire: switch Outputs on (`gearbench-ctl headset-outputs on`), or "
             "connect an output for it to play to")
NO_DEFAULT = "no output is the default now: connect one, then try again"
NOT_MOVED = "{} did not show on {} within {:g} s; WirePlumber may have refused the move"
#: move_app's words for a step after its first write that failed, the writes before it
#: kept: a later write's, raised, by how many of the program's streams it wrote, and the
#: read back's, a note, as pipewire_unit.pick_default gives one.
PARTLY_MOVED = "{}'s move to {} stopped after {} of its {} streams: {}"
NOT_CHECKED = "{}'s move to {} was not checked: {}"
#: summary's words for a unit that runs with Game not yet in PipeWire.
WAITING = "on, waiting for an output to play to"
#: switch_on's notes. Each switch's note opens with a short clause before "; ", what failed
#: where something did, so a toast too short for the note shows it (style.toast_clause).
SELECTED = f"{GAME.description} is the default output"
NOT_SELECTED = f"{GAME.description} is not the default output; {{}}"
NO_GAME = (f"{GAME.description} was not there within {pipewire_client.SETTLE:g} s; the "
           f"default output is where it was: `journalctl --user "
           f"_SYSTEMD_USER_UNIT={outputs.UNIT}` says why")
#: switch_off's notes, by the default output given back as GNOME lists it.
GIVEN_BACK = "{} is the default output again"
NOT_GIVEN_BACK = "{} is not the default output again; {}"
#: switch_off's notes for a saved default output away: by the one given back meanwhile, then
#: the saved one's name; by why it was not kept, the one given back, then the saved one.
UNTIL_BACK = "{} is the default output; {} takes over once it is back"
NOT_KEPT = ("the default output you had was not kept for its return; {}; {} plays until you "
            "pick {} once it is back")
DEFAULT_UNREAD = "the default output was not given back; {}"
SPATIAL_BACK = f"{spatial.DESCRIPTION} is the default output again"
#: By the default output given back, then why Spatial is not it.
SPATIAL_NOT_BACK = f"{{}} stays the default output, not {spatial.DESCRIPTION}; {{}}"
SPATIAL_LATE = (f"it was not back on the headset within {pipewire_client.SETTLE:g} s; pick "
                "it as your output")


@dataclass(frozen=True)
class App:
    """One program's streams as WirePlumber keeps their choice of output: the key, the program
    by name, the one line it may need (SHARED, MUSIC_NOTE or SET_IN), where it plays now as
    GNOME lists that output (node_words; None while it is linked into no sink), and the key of
    the one of the three it plays on, by the sink the unit made (chain_sinks), None for any
    other; its streams' ids, and whether a stream names its own output, which WirePlumber then
    never restores, so no move sticks."""

    key: str
    title: str
    note: str | None
    plays_on: str | None
    output: str | None
    streams: tuple[int, ...]
    fixed: bool


@dataclass(frozen=True)
class State(spatial.InstalledUnitState):
    #: Each output's key with where its stream plays, None while it plays nowhere; none
    #: while the unit's chains are not in PipeWire.
    plays_on: tuple[tuple[str, str | None], ...] = ()
    apps: tuple[App, ...] = ()
    #: The key of the headset the outputs play to, or would while the unit is off: spatial's
    #: pick among the headsets in PipeWire, as the unit's process picks it; None for none.
    headset: str | None = None
    settings: Settings = field(default_factory=Settings)


def program_name(props: dict, key: str) -> str:
    """The program a stream is of, as its App names it: its application.name; its process
    with LIBRARY_WORDS' word where the name is a library's; its process where the name is
    that in lower case, as Discord's "discord" is; its process where it has no name, else
    `key`. For a game under Proton, whose process is Wine's loader, it relies on Wine
    naming the streams by the game's product name, else its .exe's file name
    (dlls/mmdevapi/client.c's get_application_name, Wine 10.0)."""
    name, binary = text_of(props, "application.name"), text_of(props, BINARY)
    if binary and name in LIBRARY_WORDS:
        name = LIBRARY_TITLE.format(binary, LIBRARY_WORDS[name])
    elif binary.lower() == name:
        name = binary
    return spatial.shown_name(name or binary or key)


def program_names(key: str, streams: Sequence[tuple[int, dict]]) -> list[str]:
    """Each program of `streams` once, by program_name, in the order its first stream comes.
    Programs are told apart by their process, since two whose streams share a key may give
    one name; a later one of the first's name goes by its process."""
    by_process: dict[str, str] = {}
    for _node, props in streams:
        name = program_name(props, key)
        by_process.setdefault(text_of(props, BINARY) or name, name)
    (_process, first), *others = by_process.items()
    return [first, *dict.fromkeys(spatial.shown_name(process) if name == first else name
                                  for process, name in others)]


def linked_into(objects: Sequence[object], node: int) -> int | None:
    """The Audio/Sink the node `node` plays into by its first link in neither error nor
    unlinked state (pipewire_unit.feeding's), None for none. A link into another node, as a
    level meter's or an app-audio capture's from the stream itself, is not where it plays."""
    sinks = {found for found, _info, props in pipewire_client.of_type(objects, "Node")
             if props.get("media.class") == "Audio/Sink"}
    return next((info["input-node-id"] for _link, info, _props
                 in pipewire_client.of_type(objects, "Link")
                 if info.get("output-node-id") == node
                 and info.get("state") not in ("error", "unlinked")
                 and info.get("input-node-id") in sinks), None)


def node_words(objects: Sequence[object], node: int | None) -> str | None:
    """The node `node` as GNOME lists it, by its own props (pipewire_client.listed_name):
    another client can make a node of its name. Cut as Spatial's rows cut a node's name
    (spatial.shown_name); None for none, or for a node.name that is no text."""
    props = next((props for found, _info, props in pipewire_client.of_type(objects, "Node")
                  if found == node), {})
    return (spatial.shown_name(pipewire_client.listed_name(props))
            if isinstance(props.get("node.name"), str) else None)


def apps(objects: Sequence[object]) -> list[App]:
    """One App for each key the programs' streams in pw-dump's objects have
    (program_streams), in the order their first streams come."""
    keyed: dict[str, list[tuple[int, dict]]] = {}
    for node, props in program_streams(objects):
        if (key := key_of(props)) is not None:
            keyed.setdefault(key, []).append((node, props))
    sinks = chain_sinks(objects)
    found = []
    for key, streams in keyed.items():
        titles = program_names(key, streams)
        fixed = any(names_own_output(props) for _node, props in streams)
        if fixed:
            note: str | None = SET_IN
        elif len(titles) > 1:
            note = SHARED.format(wording.word_list(titles[1:], "and"))
        elif streams[0][1].get("media.role") == MUSIC:
            note = MUSIC_NOTE
        else:
            note = None
        into = linked_into(objects, streams[0][0])
        found.append(App(key, titles[0], note, node_words(objects, into),
                         None if into is None else sinks.get(into),
                         tuple(node for node, _props in streams), fixed))
    return found


def chain_streams(objects: Sequence[object]) -> dict[str, int]:
    """The id of each output's stream a process of the unit made (pipewire_unit.unit_nodes),
    by the output's key: of a name made twice, the last."""
    keys = {output.output: output.key for output in outputs.OUTPUTS}
    return {keys[props["node.name"]]: node
            for node, props, _pid in pipewire_unit.unit_nodes(objects, keys, outputs.UNIT)}


def graph(objects: Sequence[object], picked: str | None) -> dict:
    """State's PipeWire fields from pw-dump's objects, with `picked` the headset picked in
    spatial.json."""
    streams = chain_streams(objects)
    headset = pipewire_headsets.pick(pipewire_headsets.present(objects), picked)
    return {"plays_on": tuple((output.key, node_words(objects, linked_into(objects, stream)))
                              for output in outputs.OUTPUTS
                              if (stream := streams.get(output.key)) is not None),
            "apps": tuple(apps(objects)),
            "headset": None if headset is None else headset[0].key}


def read_state(run: pipewire_client.Run = subprocess.run) -> State:
    """What systemd and PipeWire say now (pipewire_client.unit_and_graph), outputs.json, and
    the headset spatial.json picks. Never raises."""
    settings = load_settings()
    picked = spatial.load_choices().headset
    return State(settings=settings, **pipewire_client.unit_and_graph(
        run, SHOW, spatial.unit_fields, lambda objects, _unit: graph(objects, picked)))


def levels(balance: int) -> str:
    """Chat's and Game's gains at `balance`, 0 to 255 (outputs.gains), each as GNOME's Sound
    settings show a volume (headset_audio.gnome_percent), in wording.levels_words' words:
    "Chat 100% · Game 83%"."""
    gains = outputs.gains(balance)
    return wording.levels_words(headset_audio.gnome_percent(gains.chat),
                                headset_audio.gnome_percent(gains.game))


def balance_words(balance: int) -> str:
    """A balance, 0 to 255, as describe's balance row and the CLI's answer to a word give it:
    the number on its scale, then the window's levels (levels), as `audio` gives the A50 X's
    "6 of 12, Voice 50% · Game 50%"."""
    return f"{balance} of {astro.BALANCE_RANGE[1]}, {levels(balance)}"


def summary(state: State) -> tuple[str, str]:
    """(kind, words) for `state`: the unit's own (spatial.unit_kind), else the first condition
    below that holds."""
    if (kind := spatial.unit_kind(state, outputs.UNIT)) is not None:
        return kind
    if not state.plays_on:
        return "waiting", WAITING
    return "on", "on"


def describe(state: State) -> list[tuple[str, str]]:
    """(label, value) rows for the CLI: the state, where each output plays while the unit
    runs, the balance, where the outputs play while the headset is away, and the Apps rows,
    each with the key `headset-outputs app` takes."""
    rows = [("outputs", summary(state)[1])]
    for key, plays_on in state.plays_on:
        rows.append((key, "plays nowhere yet" if plays_on is None else f"plays on {plays_on}"))
    rows.append(("balance", balance_words(state.settings.balance)))
    if state.status:
        rows.append(("status", state.status))
    for app in state.apps:
        where = "plays nowhere yet" if app.plays_on is None else f"on {app.plays_on}"
        note = "" if app.note is None else f"; {app.note}"
        rows.append(("app", f"{app.title}, {where}{note} ({app.key})"))
    return rows


def sink_of(objects: Sequence[object], choice: str) -> tuple[int, int | None] | None:
    """`choice`'s sink in pw-dump's objects, by id with the object.serial a move names it by:
    the output's sink of that key the unit made (pipewire_unit.unit_nodes), or for DEFAULT the
    default output's node, which a move names by no serial; None while it is not there."""
    if choice == DEFAULT:
        default = pipewire_unit.default_sink(objects)
        return next(((node, None) for node, _info, props
                     in pipewire_client.of_type(objects, "Node")
                     if props.get("node.name") == default
                     and props.get("media.class") == "Audio/Sink"), None)
    sink = outputs.OUTPUT_KEYED[choice].sink
    return next(((node, serial) for node, props, _pid
                 in pipewire_unit.unit_nodes(objects, (sink,), outputs.UNIT)
                 if props.get("media.class") == "Audio/Sink"
                 and (serial := pipewire_client.as_whole(props.get("object.serial")))
                 is not None), None)


def chain_sinks(objects: Sequence[object]) -> dict[int, str]:
    """The id of each of the three's sinks a process of the unit made in pw-dump's objects
    (sink_of), with its output's key."""
    return {sink[0]: key for key in outputs.OUTPUT_KEYED
            if (sink := sink_of(objects, key)) is not None}


def move_app(key: str, choice: str, run: pipewire_client.Run = subprocess.run) -> str | None:
    """Move each stream of the program `key` names now playing to `choice`'s output, an
    output's key or DEFAULT, from one pw-dump: by the sink's object.serial, which WirePlumber
    saves as the program's choice, or for DEFAULT the delete of the target. Then wait up to
    pipewire_client.SETTLE for each still playing to play there: NOT_MOVED's note when one did
    not, NOT_CHECKED's when a read back failed, else None. StepError, before any write, for a
    key no program plays under now (NO_APP), a program that sets its own output (FIXED), and
    an output not there (NOT_THERE, or NO_DEFAULT for DEFAULT); and for a write that failed,
    in PARTLY_MOVED's words after the first."""
    objects = pipewire_client.dump_objects(run)
    app = next((found for found in pipewire_client.shaped(apps, objects) if found.key == key),
               None)
    if app is None:
        raise pipewire_client.StepError(NO_APP.format(key))
    if app.fixed:
        raise pipewire_client.StepError(FIXED.format(app.title))
    sink = pipewire_client.shaped(lambda found: sink_of(found, choice), objects)
    words = "the default output" if choice == DEFAULT else outputs.OUTPUT_KEYED[choice].description
    if sink is None:
        raise pipewire_client.StepError(NO_DEFAULT if choice == DEFAULT
                                        else NOT_THERE.format(words))
    for written, stream in enumerate(app.streams):
        try:
            pipewire_client.run_program(run, move_argv(stream, sink[1]))
        except pipewire_client.StepError as err:
            if not written:
                raise
            raise pipewire_client.StepError(PARTLY_MOVED.format(
                app.title, words, written, len(app.streams), err)) from None

    def moved(now: Sequence[object]) -> bool:
        # Each stream still playing plays into the sink; one that ended since is no stream
        # to wait for.
        target = sink_of(now, choice)
        playing = {node for node, _props in program_streams(now)}
        return target is not None and all(pipewire_unit.linked(now, stream, target[0])
                                          for stream in app.streams if stream in playing)
    not_moved = NOT_MOVED.format(app.title, words, pipewire_client.SETTLE)

    def look() -> str | None:
        try:
            return None if pipewire_client.dumped(run, moved) else not_moved
        except pipewire_client.StepError as err:
            return NOT_CHECKED.format(app.title, words, err)
    return pipewire_client.waited(look, lambda note: note is not not_moved)


def saved_default(objects: Sequence[object]) -> str | None:
    """The default output to save at the switch-on, for pw-dump's objects: the first of the
    user's (pipewire_unit.users_defaults) that saved_as gives one for, so the output the user
    picked, unplugged now or not, comes before the one WirePlumber plays on meanwhile, and a
    pick of one of the three left from a unit that stopped without its switch-off, which
    gives none, is passed over. None for none."""
    return next((saved for default in pipewire_unit.users_defaults(objects, "sink")
                 if (saved := saved_as(objects, default)) is not None), None)


def saved_as(objects: Sequence[object], default: str) -> str | None:
    """What to save for `default`, a default output, in pw-dump's objects: for an equalizer's
    sink its own unit made, the headset output that equalizer's stream aims at
    (pipewire_unit.aimed_at), which it names while that output is away too; Gearbench
    Spatial's sink where its unit made it; nothing for any other sink named as Gearbench names
    its own (GEARBENCH_PREFIX), the three's included, which may be any program's; else the
    default itself."""
    for eq in EQ_WORDS.values():
        if default == eq.sink:
            pid = pipewire_unit.maker(objects, eq.sink, eq.unit)
            return None if pid is None else pipewire_unit.aimed_at(objects, pid, eq.output)
    if default == spatial.SINK and pipewire_unit.made_in(objects, spatial.SINK, spatial.UNIT):
        return default
    return None if default.startswith(GEARBENCH_PREFIX) else default


def game_came(run: pipewire_client.Run) -> bool:
    """Whether Gearbench Game, made by a process of the unit, is in PipeWire within
    pipewire_client.SETTLE of now: systemctl start returns before the chains are built."""
    return pipewire_client.waited(lambda: pipewire_client.dumped(
        run, lambda objects: pipewire_unit.made_in(objects, GAME.sink, outputs.UNIT)), bool)


def switch_on(run: pipewire_client.Run = subprocess.run) -> list[str]:
    """Save the default output (saved_default) to outputs.json while the unit is stopped,
    enable the unit and start it, wait for Gearbench Game (game_came), and make it the default
    output, which moves the streams that follow the default to it: on every headset, an A50's
    included (the owner's decision of 2026-10-28). With the unit running already, only the
    enable. StepError when systemctl fails, or pw-dump before the start; an OSError when
    outputs.json is not saved, or save_settings' ConfigError for one too big, before the
    start. The notes to say."""
    if pipewire_unit.running(run, outputs.UNIT):
        pipewire_client.run_program(run, pipewire_client.systemctl("enable", outputs.UNIT))
        return []
    saved = pipewire_client.dumped(run, saved_default)
    save_settings(replace(load_settings(), default=saved))
    pipewire_client.run_program(run, pipewire_client.systemctl("enable", outputs.UNIT))
    spatial.reset_failed(run, outputs.UNIT)
    pipewire_client.run_program(run, pipewire_client.systemctl("start", outputs.UNIT),
                                spatial.BLOCK_TIMEOUT)
    try:
        came = game_came(run)
    except pipewire_client.StepError as err:
        # The unit runs by now: a pw-dump that fails is a note, as Game not there in time is.
        return [NOT_SELECTED.format(err)]
    if not came:
        return [NO_GAME]
    failed = pipewire_unit.pick_default(run, GAME.sink)
    return [SELECTED if failed is None else NOT_SELECTED.format(failed)]


@dataclass(frozen=True)
class GiveBack:
    """give_back's answer: the node to make the default output before the unit stops, None
    for none; whether Gearbench Spatial is to follow it; and the default output saved at the
    switch-on while it is away, which switch_off writes back as the user's pick once the unit
    has stopped, where it gave `node` back, None for none."""

    node: str | None
    follows: bool = False
    away: str | None = None


def give_back(objects: Sequence[object], saved: str | None, picked: str | None) -> GiveBack:
    """What switch_off gives back, for pw-dump's objects and `saved`, the default output saved
    at the switch-on: nothing while the default is not one of the three, since the user picked
    another since; for a saved Gearbench Spatial that its unit made, the node it plays to once
    Gearbench Game goes (spatial_unit.headset_target, for the headset `picked`), since Spatial
    would pause its players at the restart that takes it off Game; else what stands for the
    saved one (standing_default), with the saved one away while no sink of its name is there
    (sink_there), as a headset unplugged since, unless it is named as Gearbench names its own
    (GEARBENCH_PREFIX), a sink its own switch makes the default."""
    default = pipewire_unit.default_sink(objects)
    if (default is None or default not in SINK_OUTPUTS
            or not pipewire_unit.made_in(objects, default, outputs.UNIT)):
        return GiveBack(None)
    if saved == spatial.SINK and pipewire_unit.made_in(objects, spatial.SINK, spatial.UNIT):
        aimed = spatial_unit.headset_target(objects, picked)
        return GiveBack(None) if aimed is None else GiveBack(aimed.node, follows=True)
    standing = standing_default(objects, saved)
    if saved is None or saved.startswith(GEARBENCH_PREFIX) or sink_there(objects, saved):
        return GiveBack(standing)
    return GiveBack(standing, away=saved)


def spatial_back(run: pipewire_client.Run, given: str) -> str:
    """Make Gearbench Spatial the default output again once its unit's process shows it
    aimed off Gearbench Game, within pipewire_client.SETTLE, and say so; else say that
    `given`, the default output now, stays it."""

    def aimed(state: spatial.State) -> bool:
        return state.sink_id is not None and state.target not in (None, GAME.sink)
    if not aimed(pipewire_client.waited(lambda: spatial.read_state(run), aimed)):
        return SPATIAL_NOT_BACK.format(given, SPATIAL_LATE)
    failed = pipewire_unit.pick_default(run, spatial.SINK)
    return SPATIAL_BACK if failed is None else SPATIAL_NOT_BACK.format(given, failed)


def switch_off(run: pipewire_client.Run = subprocess.run) -> list[str]:
    """Give the default output back (give_back) before the unit stops, so the streams that
    follow it move first: WirePlumber pauses the players of a sink that goes. Then disable
    and stop the unit, which keeps each app's choice of output (the owner's decision of
    2026-10-28), then finish the give-back (handed_back). A pw-dump or pick that fails is a
    note, and the unit stops all the same; StepError when the stop fails. The notes to
    say."""
    notes: list[str] = []
    #: The default output given back, as GNOME lists it, once WirePlumber showed the pick.
    given: str | None = None
    try:
        objects = pipewire_client.dump_objects(run)
        back = pipewire_client.shaped(
            lambda found: give_back(found, load_settings().default,
                                    spatial.load_choices().headset), objects)
        if back.node is not None:
            words = pipewire_client.described(objects, back.node)
            failed = pipewire_unit.pick_default(run, back.node)
            if failed is None:
                given = words
            else:
                notes.append(NOT_GIVEN_BACK.format(words, failed))
    except pipewire_client.StepError as err:
        notes.append(DEFAULT_UNREAD.format(err))
    pipewire_client.run_program(run, pipewire_client.systemctl("--now", "disable", outputs.UNIT))
    if given is not None:
        notes.append(handed_back(run, given, back))
    return notes


def handed_back(run: pipewire_client.Run, given: str, back: GiveBack) -> str:
    """switch_off's note once the unit has stopped, for `given`, the default output given
    back as GNOME lists it, and give_back's `back`: for a saved Gearbench Spatial, its pick
    once it is back on the headset (spatial_back); for a saved default away, its write as the
    user's pick (pipewire_unit.default_argv), its name said as text a line holds
    (wording.printable), with no wait, since WirePlumber makes it the default only on its
    return and plays on `given` meanwhile; else GIVEN_BACK. The write comes after the stop:
    before it, WirePlumber 0.5.13 could take Gearbench Game, the pick before, from its list of
    past picks while the saved one is away (state-default-nodes.lua), and its players would
    pause as it went."""
    if back.follows:
        return spatial_back(run, given)
    if back.away is None:
        return GIVEN_BACK.format(given)
    failed = pipewire_unit.written(run, pipewire_unit.default_argv(back.away))
    away = wording.printable(back.away)
    return (UNTIL_BACK.format(given, away) if failed is None
            else NOT_KEPT.format(failed, given, away))
