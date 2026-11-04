"""gearbench-outputs.service's process: Gearbench Game, Chat and Media, kept on the headset
Gearbench Spatial plays to, `python3 -m gearbench.outputs_unit run PATH`.

It binds the socket (outputs.socket_path), follows PipeWire with `pw-dump --monitor` and
runs one `pipewire -c PATH` holding the three chains, rendered at the first print that gives
them somewhere to play. It never stops them while it runs: when the node one of them should
play to changes (wanted), it aims that chain's output at the new one with one pw-metadata
write (pipewire_unit.aim_argv), so no sink goes and no player pauses. With the headset gone
it waits AWAY_WAIT, then aims the three at the default output the user had before Outputs
(standing_default), and back at the headset when it returns. The default output itself is
left alone. Once a start, as the three's sinks come, it puts back on them the programs
WirePlumber keeps there, which a restart left on the default output (put_back). Each word on the socket moves the balance, which sets Game's and Chat's gains
with pw-cli and which outputs.json keeps; this process is that file's only writer while it
runs.

The programs' streams, the key WirePlumber keeps each one's choice of output by, a stream's
prop as text, whether a stream names its own output and the write that moves one
(program_streams, key_of, text_of, names_own_output, move_argv) are here for the Apps rows
and the switches (outputs_state) too, as are outputs.json's reads and writes.

The chains ending on their own, or pw-dump's end, ends the process with 1, and systemd
starts it again (Restart=on-failure). Standard library only, never run in the daemon; each
program goes through `main`'s `spawn` or `run` argument.
"""

from __future__ import annotations

import os
import re
import selectors
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import IO

from . import (
    outputs,
    pipewire_client,
    pipewire_headsets,
    pipewire_unit,
    settings_files,
    spatial,
    spatial_unit,
    wording,
)
from .astro import BALANCE_RANGE
from .json_text import JsonRefused, json_value
from .outputs import CHAT, GAME, MEDIA, OUTPUTS

NAME = outputs.UNIT.removesuffix(".service")
CONF_NAME = f"{NAME}.conf"
#: How long the outputs wait for a headset that went before they play on the default output
#: the user had: a suspend's resume takes the headset away and brings it back. Assumed, not
#: timed: the release's hardware check times one suspend and resume.
AWAY_WAIT = 2.0
#: How often a line of one kind (now_and_then) is said in the journal at most, in seconds:
#: any program of the user's can send words.
SAID_EVERY = 60.0
#: The headsets whose balance between voice and game is their own, set on the headset, so a
#: word moves nothing while one is the headset played to: the A50 Gen 4's buttons, the
#: A50 X's dial and the A50 Gen 5's.
OWN_BALANCE = frozenset({pipewire_headsets.A50.key, pipewire_headsets.A50_X.key,
                         pipewire_headsets.A50_GEN5.key})
#: Game's and Chat's gains while the headset played to sets its own balance: both at full
#: (the owner's decision 7 of 2026-10-28).
UNITY = outputs.Gains(1.0, 1.0)
#: Each chain's mixer by its name in the conf, and the control that is its gain: a set names
#: it "gain:Gain 1", as pw-dump gives Spatial's mixer's (spatial.LEVEL_CONTROL). Not yet set
#: on the hardware: the release's hardware check reads it back from pw-dump.
MIXER, GAIN_CONTROL = "gain", "Gain 1"
#: The longest datagram read: one byte past outputs.WORD_MAX, so a longer word reads as too
#: long whatever the kernel cut it to.
READ_MAX = outputs.WORD_MAX + 1
#: The journal's lines; a node's name in them printable (wording.printable), as a client
#: names its own nodes.
PLAYS = f"{NAME}: {{}} plays to {{}}"
NOT_AIMED = f"{NAME}: {{}} was not aimed at {{}} ({{}}); it plays where it did"
#: A program's stream put back on its output after the unit's start, by the stream, its
#: key, then the output; one not put back, then why; WirePlumber's file not read, by why.
PUT_BACK = f"{NAME}: stream {{}} of {{}} plays on {{}} again, the output WirePlumber keeps for it"
NOT_PUT_BACK = f"{NAME}: stream {{}} of {{}} was not put back on {{}} ({{}}); it plays where it is"
TARGETS_UNREAD = (f"{NAME}: the outputs WirePlumber keeps for the programs were not read "
                  f"({{}}), so no program was put back on {GAME.description}, "
                  f"{CHAT.description} or {MEDIA.description}")
AWAY = f"{NAME}: the {{}} went, so the outputs play to {{}} until it returns"
NOT_RENDERED = f"{NAME}: the chains were not written ({{}}); switch Outputs off and on again"
CHAINS_ENDED = f"{NAME}: the chains ended ({{}})"
UNREAD = pipewire_client.UNREAD.format(NAME)
NOT_LISTENING = f"{NAME}: no socket for the balance's words ({{}})"
WORD_REFUSED = f"{NAME}: a word was refused ({{}}); said at most once a minute"
OWN_BALANCE_WORD = (f"{NAME}: the {{}} sets its own balance, so a word changes nothing; "
                    "said once")
#: By the file or folder the save failed on, then the cause.
NOT_SAVED = f"{NAME}: the balance was not saved ({{}}: {{}})"
#: The same for write_json's refusal of a file over settings_files.MAX_JSON_BYTES, by its
#: words, which name the file.
NOT_SAVED_OVER = f"{NAME}: the balance was not saved ({{}})"
#: By the output, then the cause.
NOT_SET = (f"{NAME}: {{}}'s gain was not set ({{}}); the balance applies at the unit's next "
           "start; said at most once a minute")
NOT_ANSWERED = f"{NAME}: a word was not answered ({{}}); said at most once a minute"
#: The unit's StatusText while the outputs play to the default output for a headset away, by
#: that output's name, cut as Spatial's rows cut one (spatial.shown_name) since the window
#: and `headset-outputs` show it, then the headset's.
PLAYING_ON = "Playing on {} until the {} returns"
#: The answer to a word while a headset with its own balance is played to, by its name.
OWN_BALANCE_ANSWER = "the {} sets its own balance"
#: How Gearbench names its own nodes: a sink of such a name that no unit of Gearbench's made
#: is no default to save, and a stream of one is no program's. Every node Gearbench names
#: carries it; the tests hold each name to it.
GEARBENCH_PREFIX = "gearbench-"
#: The props WirePlumber 0.5.13 keys a stream's saved choice by, the first a stream has
#: (state-stream.lua's formKey, read 2026-10-28), after its media class less "Stream/".
KEY_PROPS = ("media.role", "application.id", "application.name", "media.name", "node.name")
#: The three by their sinks' names.
SINK_OUTPUTS = {output.sink: output for output in OUTPUTS}
#: WirePlumber 0.5.13's state file of each stream's saved props, by the stream's key
#: (KEY_PROPS): node/state-stream.lua's State "stream-properties", which lib/wp/state.c keeps
#: as a GLib key file of one group of that name, in $XDG_STATE_HOME/wireplumber, or
#: ~/.local/state/wireplumber while that is unset (wp_get_xdg_state_dir), each key escaped
#: (escape_string) and each value the JSON of the stream's saved props, "target" the node
#: name of its output. Read only, never written.
STREAM_STATE = "stream-properties"
#: The most of it read, in bytes: one with the entries of 23 programs held 7,407 (measured
#: 2026-10-28).
STATE_MAX = 1 << 20
#: The escapes of a key of that file, escape_string's, and of a value, GLib's
#: g_key_file_set_string's, each by the character after the backslash; a backslash before
#: any other is kept. GLib also escapes a leading space and a tab, newline or return in a
#: value, which WirePlumber's JSON never holds raw.
KEY_ESCAPES = {"\\": "\\", "s": " ", "e": "=", "o": "[", "c": "]"}
VALUE_ESCAPES = {"\\": "\\"}
ESCAPE = re.compile(r"\\(.)", re.DOTALL)


@dataclass(frozen=True)
class Settings:
    """outputs.json: the balance, 0 all voice to 255 all game (outputs.STOPS), and the
    default output the user had when Outputs was switched on, which it is given back at the
    switch-off and which the outputs play to while the headset is away; None for none."""

    balance: int = outputs.CENTER
    default: str | None = None


def settings_path() -> Path:
    return settings_files.config_dir() / "outputs.json"


def load_settings() -> Settings:
    """The saved balance and default output, each falling back to Settings' own on its own:
    a balance that is no whole number from 0 to 255, or a default that is no name a conf
    can hold (pipewire_client.conf_holds) or is one of the three's sinks (SINK_OUTPUTS),
    which the outputs would play into, is none saved."""
    try:
        data = settings_files.read_json(settings_path())
    except (OSError, settings_files.ConfigError):
        return Settings()
    if not isinstance(data, dict):
        return Settings()
    balance, default = data.get("balance"), data.get("default")
    return Settings(
        balance if wording.whole(balance) and BALANCE_RANGE[0] <= balance <= BALANCE_RANGE[1]
        else Settings.balance,
        default if isinstance(default, str) and pipewire_client.conf_holds(default)
        and default not in SINK_OUTPUTS else None)


def save_settings(settings: Settings) -> None:
    """`settings` to outputs.json atomically (settings_files.write_json). A folder behind a
    link to nothing is an OSError naming the link, as any save that fails, but for a file
    over settings_files.MAX_JSON_BYTES, write_json's ConfigError."""
    target = settings_path()
    if (place := settings_files.link_to_nothing(target.parent)) is not None:
        raise OSError(settings_files.link_gone(place))
    settings_files.make_folder(target)
    # No default key for none, so a file with none reads as one from before it had one.
    default = {} if settings.default is None else {"default": settings.default}
    settings_files.write_json(target, {"balance": settings.balance, **default}, ".outputs-")


def headset_aims(objects: Sequence[object], picked: str | None
                 ) -> tuple[pipewire_headsets.Headset, dict[str, str]] | None:
    """The headset Gearbench Spatial plays to in pw-dump's objects, and the node each output
    plays to there, by the output's key: Spatial's target on the headset
    (spatial_unit.headset_target), its output or its equalizer's sink, for all three, never
    Gearbench Game, where Spatial plays while the chains run; on a headset with a Chat output
    of its own (pipewire_headsets.Headset.chat), Chat on that while it is there. None for no
    headset."""
    aimed = spatial_unit.headset_target(objects, picked)
    if aimed is None:
        return None
    nodes = dict.fromkeys(outputs.OUTPUT_KEYED, aimed.node)
    chat = aimed.headset.chat
    card = pipewire_headsets.card_of(aimed.headset, objects)
    if chat is not None and card is not None and any(
            props.get("node.name") == chat and props.get("media.class") == "Audio/Sink"
            for props in card[1]):
        nodes[CHAT.key] = chat
    return aimed.headset, nodes


def sink_there(objects: Sequence[object], name: str) -> bool:
    """Whether pw-dump's objects hold an Audio/Sink named `name`, any client's."""
    return any(props.get("node.name") == name and props.get("media.class") == "Audio/Sink"
               for _node, _info, props in pipewire_client.of_type(objects, "Node"))


def standing_default(objects: Sequence[object], saved: str | None) -> str | None:
    """The node that stands for `saved`, the default output saved at the switch-on, in
    pw-dump's objects: for a headset's output whose equalizer runs (spatial_unit.running_eq),
    the equalizer's sink, since the headset's own output is held at 0 dB under it; else
    `saved` while an Audio/Sink of that name is there; else the Audio/Sink of the cards with
    the highest priority.session, by the cards' nodes alone (pipewire_client.card_nodes), so
    no filter chain, Gearbench's or another program's; else None."""
    if saved is not None:
        for headset, output in pipewire_headsets.present(objects):
            if output == saved and (eq := spatial_unit.running_eq(objects, headset)) is not None:
                return eq.sink
        if sink_there(objects, saved):
            return saved
    sinks = [props for props in pipewire_client.card_nodes(objects, ())[1]
             if props.get("media.class") == "Audio/Sink"
             and isinstance(props.get("node.name"), str)]
    if not sinks:
        return None
    return max(sinks, key=lambda props: pipewire_client.as_whole(
        props.get("priority.session")) or 0)["node.name"]


def text_of(props: dict, name: str) -> str:
    """The prop `name` as the text PipeWire holds and WirePlumber keys a stream by, "" where
    it is missing or its text is lost. pw-dump 1.6.2 prints text that reads as a whole number
    as a JSON number, and "true" or "false" as JSON's literal (pw-dump.c's put_value): each
    is read back as its digits or its word, the text itself but for a plus sign, a leading
    zero or a space. A fraction's text is lost."""
    value = props.get(name)
    if isinstance(value, bool):
        return "true" if value else "false"
    if wording.whole(value):
        return str(value)
    return value if isinstance(value, str) else ""


def key_of(props: dict) -> str | None:
    """A stream's key as WirePlumber keeps its choice of output by (KEY_PROPS): formKey takes
    the first of them the stream has, "" included, as its text (text_of). None for a stream
    with none of them, or whose first is one whose text is lost, since its key is not known
    and the next prop's would be another program's."""
    media_class = text_of(props, "media.class").removeprefix("Stream/")
    name = next((name for name in KEY_PROPS if props.get(name) is not None), None)
    if name is None or not ((value := text_of(props, name)) or props[name] == ""):
        return None
    return f"{media_class}:{name}:{value}"


def names_own_output(props: dict) -> bool:
    """Whether a stream names its output in its own props (target.object), as its program's
    own pick or a launch option's PULSE_SINK does: such a stream is its program's to move,
    and neither the Apps rows nor put_back move it."""
    return "target.object" in props


def program_streams(objects: Sequence[object]) -> list[tuple[int, dict]]:
    """The programs' playback streams in pw-dump's objects, by id with their props: not a
    filter chain's or a loopback's, which carry node.link-group, and not one of Gearbench's own
    (GEARBENCH_PREFIX)."""
    return [(node, props) for node, _info, props in pipewire_client.of_type(objects, "Node")
            if props.get("media.class") == "Stream/Output/Audio"
            and "node.link-group" not in props
            and not text_of(props, "node.name").startswith(GEARBENCH_PREFIX)]


def move_argv(stream: int, serial: int | None) -> list[str]:
    """pw-metadata's write that moves `stream` to the node of object.serial `serial`, as
    WirePlumber 0.5.13 writes a target it restores and saves only a serial so written
    (state-stream.lua); with None, the delete of its target, so it follows the default."""
    if serial is None:
        return ["pw-metadata", "-n", "default", "-d", str(stream), "target.object"]
    return ["pw-metadata", "-n", "default", str(stream), "target.object", str(serial),
            "Spa:Id"]


def stream_state_path() -> Path:
    """Where WirePlumber keeps STREAM_STATE: under $XDG_STATE_HOME, or ~/.local/state while
    that is unset or empty."""
    base = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return Path(base) / "wireplumber" / STREAM_STATE


def unescaped(text: str, escapes: Mapping[str, str]) -> str:
    """`text` with each backslash and the letter after it that `escapes` names put back."""
    return ESCAPE.sub(lambda found: escapes.get(found[1], found[0]), text)


def saved_targets(path: Path) -> dict[str, str]:
    """The output WirePlumber keeps for each stream's key in its state file at `path`
    (STREAM_STATE): the "target" of each entry of the file's STREAM_STATE group, by the
    entry's key, each unescaped (KEY_ESCAPES, VALUE_ESCAPES); an entry whose value is not a
    JSON object with a target as text is left out. No file is no target; OSError for a file
    not read, and settings_files.ConfigError for one that is no regular file or is over
    STATE_MAX."""
    try:
        data = settings_files.read_capped(path, STATE_MAX)
    except FileNotFoundError:
        return {}
    targets: dict[str, str] = {}
    group = None
    # Split at "\n" alone, less a "\r" before it, as GKeyFile ends a line: splitlines()
    # also splits at U+2028, "\f" and others a program's name in a key can hold.
    for line in data.decode("utf-8", "replace").split("\n"):
        line = line.removesuffix("\r")
        if line.startswith("["):
            group = line.removeprefix("[").removesuffix("]")
            continue
        key, _equals, value = line.partition("=")
        try:
            entry = json_value(unescaped(value, VALUE_ESCAPES).encode(), one_name=False)
        except JsonRefused:
            continue
        if (group == STREAM_STATE and isinstance(entry, dict)
                and isinstance(target := entry.get("target"), str)):
            targets[unescaped(key, KEY_ESCAPES)] = target
    return targets


def made_sinks(objects: Sequence[object], pid: int) -> dict[str, tuple[int, int]]:
    """The three's sinks the chains' process `pid` made in pw-dump's objects, by name, never
    another client's of their names: each one's id and its object.serial, a sink with no
    whole serial left out."""
    return {props["node.name"]: (node, serial)
            for node, _info, props, made_by in pipewire_client.node_pids(objects)
            if made_by == pid and props.get("node.name") in SINK_OUTPUTS
            and (serial := pipewire_client.as_whole(props.get("object.serial"))) is not None}


def strays(objects: Sequence[object], sinks: Mapping[str, tuple[int, int]],
           targets: Mapping[str, str]) -> list[tuple[int, str, str]]:
    """Each program's stream in pw-dump's objects (program_streams) whose key WirePlumber
    keeps on one of `sinks` (made_sinks), by `targets` (saved_targets), and that does not
    play into it now, with its key and that sink's name. A stream that names its output in
    its own props is its program's to move, as the Apps rows leave it."""
    found = []
    for node, props in program_streams(objects):
        key = key_of(props) or ""
        if ((sink := targets.get(key, "")) in sinks and not names_own_output(props)
                and not pipewire_unit.linked(objects, node, sinks[sink][0])):
            found.append((node, key, sink))
    return found


def gain_text(gain: float) -> str:
    """A mixer's gain as the conf and pw-cli give it, to six places as a volume's are."""
    return f"{gain:.6f}"


def gain_argv(sink: int, gain: float) -> list[str]:
    """pw-cli's set-param of the mixer's gain (MIXER, GAIN_CONTROL) on the chain whose sink
    is the node `sink`."""
    return ["pw-cli", "set-param", str(sink), "Props",
            f'{{ params = [ "{MIXER}:{GAIN_CONTROL}" {gain_text(gain)} ] }}']


def render(nodes: Mapping[str, str], gains: outputs.Gains) -> str:
    """The chains' conf, each output aimed at the node of its key in `nodes`, Game's and
    Chat's mixers at `gains` and Media's at 1.0. StepError for a name a conf cannot hold
    (pipewire_client.conf_holds)."""
    for name in nodes.values():
        if not pipewire_client.conf_holds(name):
            raise pipewire_client.StepError(f"the output '{wording.escaped(name)}' cannot be "
                                            "named in the chains' conf")
    gain_of = {GAME.key: gains.game, CHAT.key: gains.chat, MEDIA.key: 1.0}
    return TEMPLATE.format(
        unit=outputs.UNIT, client_context=pipewire_client.CLIENT_CONTEXT,
        client_modules=pipewire_client.CLIENT_MODULES,
        chains="\n".join(CHAIN.format(description=output.description, sink=output.sink,
                                      output=output.output, target=nodes[output.key],
                                      mixer=MIXER, control=GAIN_CONTROL,
                                      gain=gain_text(gain_of[output.key]))
                         for output in OUTPUTS))


TEMPLATE = """\
# Gearbench: Gearbench Game, Chat and Media. {unit}'s process
# writes this file each time it starts, aimed at the outputs they play to
# then, and runs it as a PipeWire client of its own: pipewire -c <this file>.
# It moves an output to another device with pw-metadata, never by a restart.
#
# Each is a stereo sink whose one builtin mixer runs once a channel, at a
# gain of 1.0 or less, Game's and Chat's set by the balance: nothing it plays
# is louder than what it is sent.

{client_context}

context.modules = [
{client_modules}
{chains}
]
"""
CHAIN = """
    {{ name = libpipewire-module-filter-chain
        args = {{
            node.description = "{description}"
            media.name       = "{description}"
            filter.graph = {{
                nodes = [
                    {{ type = builtin label = mixer name = {mixer}
                       control = {{ "{control}" = {gain} }} }}
                ]
                inputs  = [ "{mixer}:In 1" ]
                outputs = [ "{mixer}:Out" ]
            }}
            audio.channels = 2
            audio.position = [ FL FR ]
            capture.props = {{
                node.name   = "{sink}"
                media.class = Audio/Sink
            }}
            playback.props = {{
                node.name          = "{output}"
                node.passive       = true
                target.object      = "{target}"
                # It waits for the output it is aimed at; the process aims it.
                node.dont-fallback = true
                node.linger        = true
                # WirePlumber neither saves nor restores where it plays.
                state.restore-target = false
            }}
        }}
    }}"""


class Outputs:
    """The process's state: the chains' process, the node each output is aimed at, the
    headset last played to and since when it is away, the one played to at the last print,
    the balance and the gains Game's and Chat's chains were given, and when each line said
    at most once a minute was last said.
    `picked` reads the headset picked in spatial.json."""

    def __init__(self, conf: Path, settings: Settings, *,
                 spawn: Callable[..., subprocess.Popen], run: pipewire_client.Run,
                 picked: Callable[[], str | None]) -> None:
        self.conf = conf
        self.settings = settings
        self.spawn = spawn
        self.run = run
        self.picked = picked
        self.process: subprocess.Popen | None = None
        #: The node each output's stream is aimed at, by the output's key; the conf's at the
        #: start, then each write's.
        self.aimed: dict[str, str] = {}
        #: The nodes a conf could not be written for, so it is tried again only once they change.
        self.unwritten: dict[str, str] | None = None
        #: The gain Game's and Chat's chains were given, by the output's key: the conf's at
        #: the start, then each set's, a set that failed included.
        self.given: dict[str, float] = {}
        self.objects: dict[int, dict] = {}
        #: The headset last played to; since when it is away, until it returns; and whether
        #: the wait for it is over, so the outputs play to the default output.
        self.headset: pipewire_headsets.Headset | None = None
        self.away_since: float | None = None
        self.waited = False
        #: The node the journal and the StatusText last said the outputs play to for the
        #: headset away.
        self.standing: str | None = None
        #: The headset the outputs play to at the last print read (headset_aims), None while
        #: none is there.
        self.played: pipewire_headsets.Headset | None = None
        self.said_at: dict[str, float] = {}
        self.own_said = False
        #: Whether the programs were put back on the three since the chains started.
        self.put_back_done = False

    def ended(self) -> str | None:
        """How the chains' process ended, once it ended on its own, else None."""
        return spatial_unit.chain_ended(self.process)

    def deadline(self) -> float | None:
        """When the outputs stop waiting for the headset away (AWAY_WAIT), while they wait."""
        if self.away_since is None or self.waited:
            return None
        return self.away_since + AWAY_WAIT

    def known(self) -> list[dict]:
        return list(self.objects.values())

    def take_print(self, found: list | None, now: float) -> str | None:
        """Fold one print of pw-dump --monitor into the objects and aim the outputs for them:
        how the chains ended once they end, else None."""
        if (ended := self.ended()) is not None:
            return ended
        if found is None:
            spatial_unit.say(UNREAD.format(pipewire_client.NOT_A_PRINT))
            return None
        pipewire_client.merge(self.objects, found)
        return self.aim_for(now)

    def aim_for(self, now: float) -> str | None:
        """Aim the outputs at `wanted`'s nodes for the objects at `now`, then, while the chains
        run, put the programs back on them (put_back) and give Game and Chat the gains wanted
        (apply), or start them (start) while none run: start's value, else None."""
        try:
            nodes = pipewire_client.shaped(lambda known: self.wanted(known, now), self.known())
        except pipewire_client.StepError as err:
            spatial_unit.say(UNREAD.format(err))
            return None
        if nodes is None:
            return None
        if self.process is None:
            return self.start(nodes)
        self.reaim(self.process, nodes)
        self.put_back(self.process)
        self.apply(now)
        return None

    def wanted(self, objects: Sequence[object], now: float) -> dict[str, str] | None:
        """Where each output plays for pw-dump's `objects` at `now`, by the output's key: the
        headset's nodes (headset_aims); with no headset, the node standing_default gives,
        once AWAY_WAIT has passed since the headset played to went, or at once while none
        has been played to or no chains run; None while the outputs wait, or with nowhere to
        play. The headset found is kept as `played`, for own_balance."""
        found = headset_aims(objects, self.picked())
        self.played = None if found is None else found[0]
        if found is not None:
            if self.standing is not None:
                spatial_unit.status(NAME, "")
            self.headset = found[0]
            self.away_since, self.waited, self.standing = None, False, None
            return found[1]
        if self.process is not None and self.headset is not None and not self.waited:
            if self.away_since is None:
                self.away_since = now
            if now < self.away_since + AWAY_WAIT:
                return None
            self.waited = True
        standing = standing_default(objects, self.settings.default)
        if standing is None:
            return None
        if self.headset is not None and standing != self.standing:
            self.standing = standing
            sink = pipewire_client.described(objects, standing)
            spatial_unit.say(AWAY.format(self.headset.name, sink))
            spatial_unit.status(NAME, PLAYING_ON.format(spatial.shown_name(sink),
                                                        self.headset.name))
        return dict.fromkeys(outputs.OUTPUT_KEYED, standing)

    def start(self, nodes: dict[str, str]) -> str | None:
        """Render the conf for `nodes` and the gains wanted now (wanted_gains) and run the
        chains (spatial_unit.start_chain): how they ended when pipewire could not be run or
        ended within spatial_unit.START_WAIT of its start, else None, a conf not written
        included (said, and tried again only once the nodes change)."""
        if nodes == self.unwritten:
            return None
        gains = self.wanted_gains()
        try:
            spatial.replace_conf(self.conf, render(nodes, gains))
        except (pipewire_client.StepError, OSError) as err:
            self.unwritten = nodes
            where = f"{err.filename or self.conf}: " if isinstance(err, OSError) else ""
            spatial_unit.say(NOT_RENDERED.format(f"{where}{wording.reason(err)}"))
            return None
        self.process, ended = spatial_unit.start_chain(self.spawn, self.conf)
        if ended is None:
            self.aimed = dict(nodes)
            self.given = {GAME.key: gains.game, CHAT.key: gains.chat}
            for output in OUTPUTS:
                spatial_unit.say(PLAYS.format(output.description,
                                              wording.printable(nodes[output.key])))
        return ended

    def reaim(self, process: subprocess.Popen, nodes: dict[str, str]) -> None:
        """Aim each output whose node changed at its node of `nodes`, once its stream, which
        the chains' `process` made, is in PipeWire; a write that fails is said, and tried
        again only once its node changes."""
        streams = pipewire_unit.owned_nodes(self.known(), process.pid)
        for output in OUTPUTS:
            target = nodes[output.key]
            stream = streams.get(output.output)
            if self.aimed.get(output.key) == target or stream is None:
                continue
            self.aimed[output.key] = target
            failed = pipewire_unit.written(self.run, pipewire_unit.aim_argv(stream, target))
            shown = wording.printable(target)
            spatial_unit.say(PLAYS.format(output.description, shown) if failed is None
                             else NOT_AIMED.format(output.description, shown, failed))

    def put_back(self, process: subprocess.Popen) -> None:
        """Once a start, at the first print with each of the three's sinks the chains'
        `process` made (made_sinks): move each program's stream that WirePlumber keeps on one
        of them and that plays elsewhere (strays) to that sink, by its object.serial, with the
        Apps rows' write (move_argv). A start makes the sinks with new serials, and a stream's
        target.object still names the old sink's, so the stream plays on the default output
        until its program starts it again (the owner's decision of 2026-10-28: only the
        user's own saved choices, only at the start). WirePlumber's file is read then alone
        (saved_targets); one not read is said and moves nothing, as is a print whose links
        are of a shape not read. Each write is said, or why it failed."""
        if self.put_back_done:
            return
        sinks = made_sinks(self.known(), process.pid)
        if len(sinks) < len(OUTPUTS):
            return
        self.put_back_done = True
        path = stream_state_path()
        try:
            targets = saved_targets(path)
        except OSError as err:
            spatial_unit.say(TARGETS_UNREAD.format(f"{path}: {wording.reason(err)}"))
            return
        except settings_files.ConfigError as err:
            spatial_unit.say(TARGETS_UNREAD.format(err))
            return
        try:
            found = pipewire_client.shaped(lambda known: strays(known, sinks, targets),
                                           self.known())
        except pipewire_client.StepError as err:
            spatial_unit.say(UNREAD.format(err))
            return
        for stream, key, sink in found:
            words = (stream, wording.printable(key), SINK_OUTPUTS[sink].description)
            failed = pipewire_unit.written(self.run, move_argv(stream, sinks[sink][1]))
            spatial_unit.say(PUT_BACK.format(*words) if failed is None
                             else NOT_PUT_BACK.format(*words, failed))

    def tick(self, now: float) -> None:
        """At `now`, aim the outputs once the headset has been away AWAY_WAIT, which ends the
        wait."""
        if (deadline := self.deadline()) is not None and now >= deadline:
            # Ended here as well as in wanted: a print of a shape not read stops aim_for
            # before wanted ends the wait, and a deadline left in the past spins serve's select.
            self.waited = True
            self.aim_for(now)

    def now_and_then(self, line: str, kind: str, now: float) -> None:
        """Say `line` unless a line of its `kind` was said within SAID_EVERY of `now`."""
        if now - self.said_at.get(kind, -SAID_EVERY) >= SAID_EVERY:
            self.said_at[kind] = now
            spatial_unit.say(line)

    def own_balance(self) -> pipewire_headsets.Headset | None:
        """The headset played to at the last print read (played) while it sets its own
        balance (OWN_BALANCE), else None."""
        played = self.played
        return played if played is not None and played.key in OWN_BALANCE else None

    def wanted_gains(self) -> outputs.Gains:
        """Game's and Chat's gains now: the balance's (outputs.gains), or UNITY while the
        headset played to sets its own balance."""
        return UNITY if self.own_balance() is not None else outputs.gains(self.settings.balance)

    def apply(self, now: float) -> None:
        """Give Game's and Chat's chains the gains wanted (wanted_gains), with one pw-cli
        set-param on the sink of each whose gain differs from the one it was given, once that
        sink, which the chains' process made, is in PipeWire. A set that fails is said at
        most once a minute, and tried again only once the gain wanted changes. Nodes or
        clients of a shape not read set nothing, said at most once a minute."""
        if self.process is None:
            return
        gains = self.wanted_gains()
        pid = self.process.pid
        try:
            sinks = pipewire_client.shaped(lambda known: pipewire_unit.owned_nodes(known, pid),
                                           self.known())
        except pipewire_client.StepError as err:
            self.now_and_then(UNREAD.format(err), "unread", now)
            return
        for output, gain in ((GAME, gains.game), (CHAT, gains.chat)):
            sink = sinks.get(output.sink)
            if self.given.get(output.key) == gain or sink is None:
                continue
            self.given[output.key] = gain
            if failed := pipewire_client.pw_cli_failed(self.run, gain_argv(sink, gain)):
                self.now_and_then(NOT_SET.format(output.description, failed), "gain", now)

    def take_words(self, datagrams: Sequence[bytes], now: float) -> list[str]:
        """The balance after the words `datagrams` hold, in order, applied once (apply) and
        saved once, and each one's answer (outputs.BALANCE_ANSWER or REFUSED_ANSWER). While
        the headset played to sets its own balance (own_balance), a word moves nothing and
        is said once."""
        own = self.own_balance()
        balance = self.settings.balance
        answers: list[str | None] = []
        for data in datagrams:
            try:
                word = outputs.parse(data)
            except outputs.WordRefused as err:
                self.now_and_then(WORD_REFUSED.format(err), "refused", now)
                answers.append(outputs.REFUSED_ANSWER.format(err))
                continue
            if own is not None:
                if not self.own_said:
                    self.own_said = True
                    spatial_unit.say(OWN_BALANCE_WORD.format(own.name))
                answers.append(outputs.REFUSED_ANSWER.format(OWN_BALANCE_ANSWER.format(own.name)))
                continue
            balance = outputs.moved(balance, word)
            answers.append(None)
        if balance != self.settings.balance:
            self.settings = replace(self.settings, balance=balance)
            self.apply(now)
            try:
                save_settings(self.settings)
            except OSError as err:
                # Escaped: a link's target is quoted as it is, and the journal splits a line
                # at each newline.
                where, why = err.filename or settings_path(), wording.reason(err)
                spatial_unit.say(NOT_SAVED.format(wording.escaped(where), wording.escaped(why)))
            except settings_files.ConfigError as err:
                # A default written in by hand that load_settings takes can make the file
                # over the cap once each character past ASCII is its six-byte escape.
                spatial_unit.say(NOT_SAVED_OVER.format(err))
        return [outputs.BALANCE_ANSWER.format(balance) if answer is None else answer
                for answer in answers]


def listen(path: Path) -> socket.socket:
    """The socket at `path`, bound for this user alone: one left by a process that ended is
    removed first, and the folder is one no other account may write in, though one may look
    in it (outputs.socket_path)."""
    path.unlink(missing_ok=True)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    # Bound under a umask that leaves it 0600 at most, since a datagram socket takes words
    # from its bind on, with no listen to wait for. The bind applies a folder's default ACL
    # as well as the umask, so the chmod gives back the owner's write that a default ACL of
    # user::r-x takes away.
    umask = os.umask(0o177)
    try:
        sock.bind(str(path))
        os.chmod(path, 0o600)
    except OSError:
        sock.close()
        raise
    finally:
        os.umask(umask)
    sock.setblocking(False)
    return sock


def answer(sock: socket.socket, unit: Outputs, now: float) -> None:
    """Take every word queued on `sock`, then answer each sender that has an address of its
    own, never waiting: a sender gone or full is said at most once a minute."""
    # A sender's address: a path, an abstract name as bytes, or None for an unbound one.
    datagrams: list[tuple[bytes, str | bytes | None]] = []
    while True:
        try:
            datagrams.append(sock.recvfrom(READ_MAX))
        except BlockingIOError:
            break
    answers = unit.take_words([data for data, _sender in datagrams], now)
    for (_data, sender), words in zip(datagrams, answers, strict=True):
        # An unbound sender, as the daemon's, has no address to answer to.
        if not sender:
            continue
        try:
            sock.sendto(words.encode("ascii", "replace"), socket.MSG_DONTWAIT, sender)
        except OSError as err:
            unit.now_and_then(NOT_ANSWERED.format(wording.reason(err)), "unanswered", now)


def serve(monitor: IO[bytes], sock: socket.socket, unit: Outputs,
          clock: Callable[[], float] = time.monotonic) -> str | None:
    """Follow `monitor`, pw-dump --monitor's stdout, and the words on `sock` for `unit`, and
    the end of each wait for a headset away, looking at the running chains every
    spatial_unit.LOOK_EVERY between prints: how the chains ended once they end, or None once
    the monitor ends."""
    reader = pipewire_client.Prints()
    with selectors.DefaultSelector() as selector:
        selector.register(monitor, selectors.EVENT_READ, "monitor")
        selector.register(sock, selectors.EVENT_READ, "words")
        while True:
            waits = [] if unit.process is None else [spatial_unit.LOOK_EVERY]
            if (deadline := unit.deadline()) is not None:
                waits.append(max(0.0, deadline - clock()))
            events = selector.select(min(waits, default=None))
            now = clock()
            if not events and (ended := unit.ended()) is not None:
                return ended
            for key, _mask in events:
                if key.data == "words":
                    answer(sock, unit, now)
                    continue
                chunk = os.read(monitor.fileno(), pipewire_client.CHUNK)
                if not chunk:
                    return None
                for found in reader.feed_bytes(chunk):
                    if (ended := unit.take_print(found, now)) is not None:
                        return ended
            unit.tick(now)


def main(argv: Sequence[str] | None = None,
         spawn: Callable[..., subprocess.Popen] = subprocess.Popen,
         run: pipewire_client.Run = subprocess.run) -> int:
    """`python3 -m gearbench.outputs_unit run PATH`, the unit's process: bind the socket,
    then serve until pw-dump --monitor ends, as at PipeWire's stop, or the chains end, either
    of which ends the unit as failed, 1, as does a socket it cannot bind."""
    args = sys.argv[1:] if argv is None else list(argv)
    if len(args) != 2 or args[0] != "run":
        print("usage: python3 -m gearbench.outputs_unit run PATH", file=sys.stderr)
        return 2
    try:
        path = outputs.socket_path()
        sock = listen(path)
    except (outputs.SocketRefused, OSError) as err:
        spatial_unit.say(NOT_LISTENING.format(wording.reason(err)))
        return 1
    unit = Outputs(Path(args[1]), load_settings(), spawn=spawn, run=run,
                   picked=lambda: spatial.load_choices().headset)
    try:
        return spatial_unit.monitored(spawn, NAME, lambda monitor: serve(monitor, sock, unit),
                                      lambda: unit.process, CHAINS_ENDED)
    finally:
        sock.close()
        path.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
