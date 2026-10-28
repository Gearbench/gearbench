"""The system volume of a headset's Game and Chat outputs (OUTPUTS) and of its microphone
(VOLUMES), the level GNOME's Sound settings show and set for each.

An output is the Audio/Sink of its name among its headset's card's own nodes in pw-dump
(pipewire_client.card_nodes), so a node another client gave that name is never it. The microphone
is the card's own Audio/Source that Input level records and noise suppression captures
(pipewire_unit.headset_node). A set goes to the id that pw-dump gave the node it found. Its
volume is where pipewire-pulse, which GNOME goes through, keeps it: on the card's route for
the node's device where the card has one (pipewire_unit.route_of), else in the node's own
Props (pipewire_unit.props_volumes), as pipewire-pulse 1.6.2 reads and sets a sink's and a
source's alike (collect.c's collect_device_info and pulse-server.c's do_set_volume, read
2026-10-25). Gearbench's profile sets give no mapping a mixer path and so no route (the A50
X's device report listed no output route, 2026-10-23); PipeWire's own
usb-gaming-headset.conf gives each A50 output one, and analog-only.conf the G432's output
one (the hardware, 2026-10-18) and, assumed, its microphone. GNOME shows the volume as the
cube root of the loudest channel's, in percent (pipewire_unit.gnome_percent), and its slider
sets the loudest channel to the level (pipewire_unit.gnome_volume) and the others in
proportion (libgvc's gvc_mixer_stream_set_volume, through PulseAudio's pa_cvolume_scale); a
route is set saved, so WirePlumber keeps it, as pipewire-pulse sets one. The mute is left as
it is.

Standard library only, never run in the daemon; pw-dump and pw-cli go through a `run`
argument (pipewire_client.dumped, pipewire_client.pw_cli), and a failure is a
pipewire_client.StepError in words.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass

from . import pipewire_client, pipewire_unit, spatial

#: Each headset whose card Gearbench's profile set gives a Game and a Chat output, by its key
#: in spatial.HEADSET_KEYED, with each output's node name by its word.
OUTPUTS = {spatial.A50.key: {"game": spatial.GAME, "chat": spatial.CHAT},
           spatial.A50_X.key: {"game": spatial.A50_X_GAME, "chat": spatial.A50_X_CHAT}}
#: The microphone's word.
MIC = "mic"
#: Each headset's volumes by its key, their words in the order they are shown: its outputs'
#: where it has them (OUTPUTS), then its microphone's.
VOLUMES = {headset.key: (*OUTPUTS.get(headset.key, ()), MIC) for headset in spatial.HEADSETS}
#: Each output's word as a sentence names it.
OUTPUT_NAMES = {"game": "Game", "chat": "Chat"}
#: The top of GNOME's slider, 100 %, with over-amplification off, its default.
LEVEL_MAX = 100
#: A set's refusal where an output is not in PipeWire, by the headset's name and the
#: output's: its base station is out, or its card is still on another profile set.
NOT_FOUND = ("the {}'s {} output is not in PipeWire: plug in its base station; with it in, "
             f"{spatial.JOINED_HINT}")
#: A set's refusal where the microphone is not, by the headset's name: the headset is not
#: connected, or its card is on a profile with no input, which GNOME calls a Configuration.
MIC_NOT_FOUND = ("the {}'s microphone is not in PipeWire: connect the headset; with it "
                 "connected, pick a Configuration with a microphone in GNOME's Sound settings")


@dataclass(frozen=True)
class Volume:
    """An output's or a microphone's volume in pw-dump: its node's id, each channel's volume,
    and its card's route for it, None where the card has none and the node's Props hold the
    volume."""

    node: int
    volumes: tuple[float, ...]
    route: pipewire_unit.Route | None = None

    @property
    def level(self) -> int:
        """The volume as GNOME shows it, in percent."""
        return pipewire_unit.gnome_percent(max(self.volumes))


def _volume(objects: Sequence[object], props: dict, direction: str) -> Volume | None:
    """The volume of the node whose props are `props`, one of pw-dump's objects, where
    pipewire-pulse reads it: its card's route in `direction` for it, else its Props; None
    where neither gives a volume."""
    node, info = next((node, info)
                      for node, info, found in pipewire_client.of_type(objects, "Node")
                      if found is props)
    if (route := pipewire_unit.route_of(objects, props, direction)) is not None:
        return Volume(node, route.volumes, route)
    volumes = pipewire_unit.props_volumes(info)[0]
    return None if volumes is None else Volume(node, volumes)


def output_of(objects: Sequence[object], key: str, name: str) -> Volume | None:
    """The output named `name` of the headset of `key` in pw-dump's objects, among its card's
    own nodes (pipewire_unit.headset_node); None while it is not there or gives no volume
    (_volume)."""
    props = pipewire_unit.headset_node(objects, spatial.HEADSET_KEYED[key], "Audio/Sink",
                                       aimed=name)
    return None if props is None else _volume(objects, props, "Output")


def mic_of(objects: Sequence[object], key: str) -> Volume | None:
    """The microphone of the headset of `key` in pw-dump's objects
    (pipewire_unit.headset_node); None while it is not there or gives no volume
    (_volume)."""
    props = pipewire_unit.headset_node(objects, spatial.HEADSET_KEYED[key],
                                       pipewire_unit.MIC_CLASS)
    return None if props is None else _volume(objects, props, "Input")


def volume_of(objects: Sequence[object], key: str, word: str) -> Volume | None:
    """The volume `word`, one of VOLUMES[key], of the headset of `key` in pw-dump's objects."""
    return mic_of(objects, key) if word == MIC else output_of(objects, key, OUTPUTS[key][word])


def read_volumes(keys: Sequence[str], run: pipewire_client.Run = subprocess.run
                 ) -> dict[str, dict[str, Volume | None]]:
    """The volumes of each headset of `keys` (VOLUMES) in one fresh pw-dump, by its key and
    their words, None for one not there (volume_of). StepError where pw-dump fails or its
    objects are of a shape pipewire_client.shaped refuses."""
    return pipewire_client.dumped(run, lambda objects: {
        key: {word: volume_of(objects, key, word) for word in VOLUMES[key]} for key in keys})


def scaled(volumes: Sequence[float], loudest: float) -> list[float]:
    """`volumes` with the loudest at `loudest` and the others in proportion, as GNOME's
    slider sets them; each at `loudest` where every one is at 0."""
    top = max(volumes)
    return [volume * loudest / top for volume in volumes] if top > 0 else [loudest] * len(volumes)


def set_level(key: str, word: str, level: int, run: pipewire_client.Run = subprocess.run) -> None:
    """Set the volume `word`, one of VOLUMES[key], of the headset of `key` to `level`, GNOME's
    percent, where a fresh pw-dump holds it (volume_of): its route's, saved, or its node's
    Props. StepError in words where it is not in PipeWire, or pw-dump or pw-cli fails."""
    found = pipewire_client.dumped(run, lambda objects: volume_of(objects, key, word))
    if found is None:
        name = spatial.HEADSET_KEYED[key].name
        raise pipewire_client.StepError(MIC_NOT_FOUND.format(name) if word == MIC
                                        else NOT_FOUND.format(name, OUTPUT_NAMES[word]))
    volumes = scaled(found.volumes, pipewire_unit.gnome_volume(level))
    argv = (pipewire_unit.sink_argv(found.node, volumes, False) if found.route is None
            else pipewire_unit.route_argv(found.route, volumes, True, mute=None))
    if failed := pipewire_client.pw_cli(run, argv):
        raise pipewire_client.StepError(failed)
