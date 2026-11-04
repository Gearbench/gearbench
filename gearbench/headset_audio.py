"""A headset's nodes in pw-dump and their volume: the nodes of its card that each chain's conf
aims at and the input meter (input_meter) records from (headset_nodes, headset_node, mic_of,
cards_of), and the volume of such a node where pipewire-pulse and GNOME keep it: its card's
Route or its own Props (route_of, props_volumes), their pw-cli sets (route_argv, sink_argv),
and GNOME's percent (gnome_percent, gnome_volume); and whether a node's card route is not
unavailable, so WirePlumber may make it the default (route_available). Standard library
only.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass

from . import pipewire_client, pipewire_headsets, wording

#: A card's profile by each desktop's word for it, to follow "the G432's": GNOME's Sound
#: settings call it Configuration, KDE Plasma's call it Profile.
CARD_PROFILE = "profile, its Configuration in GNOME's Sound settings and Profile in KDE Plasma's"
#: The media class of a headset's microphone in PipeWire, an input of its card.
MIC_CLASS = "Audio/Source"
#: The A50 X's Stream Out, the stream mix's capture: an input of its card beside the
#: microphone, the profile set's mapping of this name
#: (alsa-card-profile/gearbench-astro-a50x.conf), which names the node's device.profile.name.
STREAM_OUT = "stereo-stream"
#: GNOME's 100 %, a volume of 1.0.
GNOME_FULL = 100


def cards_of(objects: Sequence[object], headset: pipewire_headsets.Headset) -> set[int]:
    """The ids of `headset`'s card Devices in pw-dump's objects, on any profile
    (pipewire_client.card_nodes)."""
    return set(pipewire_client.card_nodes(objects, headset.card)[0])


def headset_nodes(objects: Sequence[object], headset: pipewire_headsets.Headset,
                  media_class: str, *, aimed: str | None = None) -> list[dict]:
    """The props of `headset`'s nodes of `media_class` in pw-dump's objects, the A50 X's
    Stream Out left out, in pw-dump's order: with `aimed`, a node's name, such as a running
    chain's (aimed_at), those of that name among its cards' nodes (pipewire_client.card_nodes),
    so a second headset of the model plugged in meanwhile is never taken for it; else those
    with a name of the nodes of the card Gearbench Spatial plays to (pipewire_headsets.card_of)."""
    if aimed is None:
        card = pipewire_headsets.card_of(headset, objects)
        nodes = [] if card is None else card[1]
    else:
        nodes = pipewire_client.card_nodes(objects, headset.card)[1]
    return [props for props in nodes if props.get("media.class") == media_class
            and props.get("device.profile.name") != STREAM_OUT
            and isinstance(name := props.get("node.name"), str)
            and (aimed is None or name == aimed)]


def headset_node(objects: Sequence[object], headset: pipewire_headsets.Headset, media_class: str, *,
                 aimed: str | None = None) -> dict | None:
    """The first of headset_nodes; None while there is none."""
    return next(iter(headset_nodes(objects, headset, media_class, aimed=aimed)), None)


def mic_of(objects: Sequence[object], headset: pipewire_headsets.Headset) -> str | None:
    """The node name of `headset`'s microphone in pw-dump's objects, headset_node's input;
    None while there is none."""
    props = headset_node(objects, headset, MIC_CLASS)
    return None if props is None else props["node.name"]


def node_id(objects: Sequence[object], props: dict | None) -> int | None:
    """The id of the Node in pw-dump's objects whose props are `props`, as headset_node gives
    them: by those props, not their node.name, which any client can give a node of its
    own; None for None."""
    return next((node for node, _info, found in pipewire_client.of_type(objects, "Node")
                 if found is props), None)


@dataclass(frozen=True)
class Route:
    """A node of a headset's card, its output or its microphone, as the card's Route param
    gives it: the card Device's id, the route's index and device, each channel's volume,
    volumeBase, the volume at which the adapter's own is at 0 dB: PipeWire's ACP sets the
    hardware volume to the volume over volumeBase, which it takes from the mixer path's
    highest dB (acp.c's mixer_volume_init and set_volume, read 2026-10-17), and whether it
    is muted."""

    card: int
    index: int
    device: int
    volumes: tuple[float, ...]
    base: float
    mute: bool


def levels(value: object) -> tuple[float, ...] | None:
    """`value` as volumes, one a channel, if it is a list of finite numbers of 0 or more, at
    least one; else None."""
    # json.loads reads 1e999 and Infinity as an infinity, and a whole number past the largest
    # float raises OverflowError at float(); NaN fails each comparison.
    if not (isinstance(value, list) and value
            and all(wording.number(v) and 0 <= v <= sys.float_info.max for v in value)):
        return None
    return tuple(float(v) for v in value)


def _route(route: object, card: int, device: object, direction: str) -> Route | None:
    """`route`, one of the card's Route params, as a Route if it is the node's: its
    direction `direction`, its device the node's card.profile.device, a whole index, and a
    volume of 0 or more for each channel and a volumeBase above 0 in its props, whose mute
    is true only for a JSON true."""
    if (not isinstance(route, dict) or route.get("direction") != direction
            or not isinstance(values := route.get("props"), dict)):
        return None
    volumes, base = levels(values.get("channelVolumes")), values.get("volumeBase")
    if (not wording.whole(device) or route.get("device") != device
            or not wording.whole(route.get("index")) or volumes is None
            or not wording.number(base) or not base > 0):
        return None
    return Route(card, route["index"], device, volumes, float(base), values.get("mute") is True)


def route_of(objects: Sequence[object], props: dict, direction: str = "Output"
             ) -> Route | None:
    """The route of the node whose props are `props`, a node of a card's in pw-dump's
    objects (pipewire_client.card_nodes), an output, or with `direction` "Input" a
    microphone: its card's Route param for its card.profile.device, None where the card gives
    none of the shape _route reads. pipewire-pulse 1.6.2 matches the device alone
    (collect.c's collect_device_info, read 2026-10-25): a device of an ACP card is an output
    or an input, so the direction finds the same route."""
    card = props["device.id"]
    for card_id, info, _card_props in pipewire_client.of_type(objects, "Device"):
        params = info.get("params") if card_id == card else None
        routes = params.get("Route") if isinstance(params, dict) else None
        for route in routes if isinstance(routes, list) else ():
            if (found := _route(route, card, props.get("card.profile.device"),
                                direction)) is not None:
                return found
    return None


def route_available(objects: Sequence[object], props: dict) -> bool:
    """Whether WirePlumber 0.5.13 may make the node whose props in pw-dump's objects are
    `props` a default node, as lib/linking-utils.lua's haveAvailableRoutes says, which
    default-nodes/rescan.lua asks (read in its source 2026-11-01): False only where its
    card's first Route for its card.profile.device is available "no", or, with no such
    Route, each EnumRoute that lists that device is, one at least listing it."""
    device = props.get("card.profile.device")
    card = pipewire_client.as_whole(props.get("device.id"))
    params = next((info.get("params") for found, info, _props
                   in pipewire_client.of_type(objects, "Device") if found == card), None)
    if not wording.whole(device) or not isinstance(params, dict):
        return True
    routes = params.get("Route")
    for route in routes if isinstance(routes, list) else ():
        if isinstance(route, dict) and route.get("device") == device:
            return route.get("available") != "no"
    listed = params.get("EnumRoute")
    listing = [route for route in (listed if isinstance(listed, list) else ())
               if isinstance(route, dict) and isinstance(route.get("devices"), list)
               and device in route["devices"]]
    return not listing or any(route.get("available") != "no" for route in listing)


def props_volumes(info: dict) -> tuple[tuple[float, ...] | None, bool]:
    """A node's volume on each channel and whether it is muted, from its info in pw-dump: the
    channelVolumes and the mute of the first entry of its Props with channelVolumes, the
    volumes None where they are not levels and the mute true only for a JSON true. (None,
    False) where it has no such entry."""
    params = info.get("params")
    entries = params.get("Props") if isinstance(params, dict) else None
    entry = next((entry for entry in (entries if isinstance(entries, list) else ())
                  if isinstance(entry, dict) and "channelVolumes" in entry), {})
    return levels(entry.get("channelVolumes")), entry.get("mute") is True


def route_argv(route: Route, volumes: Sequence[float], save: bool, *,
               mute: bool | None) -> list[str]:
    """pw-cli's set-param of `route` to `volumes`, and to `mute` unless None, as
    WirePlumber 0.5.13's apply-routes.lua sets a route: its index and device, the volumes and
    the mute in its props, and save, which decides whether WirePlumber keeps the level and
    the mute (state-routes.lua)."""
    shown = " ".join(f"{volume:.6f}" for volume in volumes)
    muted = "" if mute is None else f", mute: {'true' if mute else 'false'}"
    param = (f"{{ index: {route.index}, device: {route.device}, props: {{ channelVolumes: "
             f"[ {shown} ]{muted} }}, save: {'true' if save else 'false'} }}")
    return ["pw-cli", "set-param", str(route.card), "Route", param]


def sink_argv(sink_id: int, volumes: Sequence[float], mute: bool) -> list[str]:
    """pw-cli's set-param of the Props of the node `sink_id`, a sink or a microphone, to
    `volumes`, muted when `mute`, as GNOME's slider and its mute set them."""
    shown = " ".join(f"{volume:.6f}" for volume in volumes)
    return ["pw-cli", "set-param", str(sink_id), "Props",
            f"{{ channelVolumes: [ {shown} ]{', mute: true' if mute else ''} }}"]


def gnome_percent(volume: float) -> int:
    """A volume as GNOME's Sound settings shows it: the cube root, in percent, as
    WirePlumber's 0.064 shows as 40 %."""
    return round(volume ** (1 / 3) * GNOME_FULL)


def gnome_volume(percent: float) -> float:
    """The volume GNOME's slider sets for `percent`: gnome_percent the other way, its
    cube."""
    return (percent / GNOME_FULL) ** 3
