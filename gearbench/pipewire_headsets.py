"""The headsets Gearbench knows in PipeWire, for every audio feature: each `Headset`, how its
card and output are found in pw-dump's objects and the words that name them (HEADSETS,
HEADSET_KEYED), which of them are plugged in with their output (present, card_of, output_of)
and which one the chain plays to (pick), and the ones plugged in with nothing to play to
(stranded). Standard library only, and of the package pipewire_client, g432's models and
hidpp's vendor id alone, so the Spatial unit's process loads neither a50x nor centurion.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from . import pipewire_client
from .g432 import MODELS as G432_MODELS
from .hidpp import VENDOR_ID

#: The A50 Gen 4's card, outputs and profile set as PipeWire names them, defined here alone:
#: the tests hold its WirePlumber rule and Gearbench's profile set to them.
CARD = "alsa_card.usb-Astro_Gaming_Astro_A50-00"
GAME = "alsa_output.usb-Astro_Gaming_Astro_A50-00.stereo-game"
#: Hard-coded in the WirePlumber rule; the tests hold the rule to it.
CHAT = "alsa_output.usb-Astro_Gaming_Astro_A50-00.stereo-chat"
PROFILE_SET = "gearbench-astro-a50.conf"


def usb_ids(ids: Iterable[int]) -> tuple[str, ...]:
    """USB ids in the form PipeWire 1.6.2 printed the A50's card's on 2026-09-27, `0x` and
    four lowercase digits."""
    return tuple(f"0x{usb_id:04x}" for usb_id in ids)


#: Logitech's vendor id as a card's props in PipeWire give it: with a product id, how each
#: headset but the A50 is found.
(G432_VENDOR_ID,) = usb_ids((VENDOR_ID,))
#: What to do about a card on another profile set than the one its rule gives it: the
#: outputs rows say it while restart_needed, and a volume's refusal where the output is
#: missing.
JOINED_HINT = "restart WirePlumber: `systemctl --user restart wireplumber`"
#: How long a card stays on a set that joins its outputs where a rule of Gearbench's gives
#: it another (Headset.ruled).
UNTIL_RESTART = "until WirePlumber restarts"
#: Where another profile set comes from on a card no rule of Gearbench's sets, in place of
#: JOINED_HINT, which would change nothing there.
OUTSIDE_RULE = "from a rule outside Gearbench"


@dataclass(frozen=True)
class Headset:
    """A headset the chain plays to: its key, its name, the props its card's Device has in
    PipeWire, each with the values it may take, the node it plays to there (None: the card's
    first Audio/Sink), its Chat output beside that node where it has one, and the words for
    it."""

    key: str
    name: str
    card: tuple[tuple[str, tuple[str, ...]], ...]
    game: str | None
    #: The output as the conf's first comment names it.
    plays_on: str
    #: The output as the summary says the chain is not linked to it, and as it says the
    #: chain waits for it.
    linked_to: str
    waited_for: str
    #: The card's profile set that gives it the output the chain needs, and describe's
    #: outputs row for it, and for another, named at {}, before JOINED_HINT, UNTIL_RESTART or
    #: OUTSIDE_RULE.
    profile_set: str
    outputs_apart: str
    outputs_joined: str
    #: The window's outputs row while the card has another profile set, in the words of the
    #: outputs GNOME lists.
    joined_shown: str
    #: Whether a rule of Gearbench's gives the card profile_set, so that restarting
    #: WirePlumber puts it in place (JOINED_HINT); else another set is OUTSIDE_RULE's.
    ruled: bool = True
    #: The Chat output Gearbench's profile set gives the card beside `game`, by its node
    #: name; None for a headset with one output, or whose Chat node is not known.
    chat: str | None = None


A50 = Headset(
    key="a50", name="Astro A50 Gen 4", card=(("device.name", (CARD,)),), game=GAME,
    plays_on="the Astro A50 Gen 4's Game output", linked_to="Astro A50 Game",
    waited_for="Astro A50 Game", profile_set=PROFILE_SET,
    outputs_apart="Astro A50 Game and Chat apart", outputs_joined="one entry ({})",
    joined_shown="Astro A50 Game and Chat show as one output", chat=CHAT)


def logitech(key: str, name: str, products: tuple[str, ...]) -> Headset:
    """A headset of the G432's family, found by Logitech's vendor id and any of `products`,
    its own output its card's first Audio/Sink. The profile set its WirePlumber rule gives
    it, PipeWire's analog-only.conf, and the outputs that set leaves and the one it takes
    away are the G432's, as the hardware gave them on 2026-10-17; on the others they are
    assumed (wireplumber/60-gearbench-hidpp-headsets.conf)."""
    return Headset(
        key=key, name=name,
        card=(("device.vendor.id", (G432_VENDOR_ID,)), ("device.product.id", products)),
        game=None, plays_on=f"the {name}'s own output", linked_to=f"the {name}",
        waited_for="a Logitech G headset's output", profile_set="analog-only.conf",
        outputs_apart="one analog output", outputs_joined="an analog and a digital output ({})",
        joined_shown=f"The {name} shows an analog and a digital output")


#: The G432's family, gearbench.g432.MODELS in their order: each by its key, its short name
#: and any of its USB ids.
G432_FAMILY = tuple(logitech(model.key, model.short, usb_ids(model.products))
                    for model in G432_MODELS)
#: The A50 X's card and those first_output makes, by Logitech's id (G432_VENDOR_ID) and each
#: one's own, a50x.MODELS' in the G432's form, copied: importing a50x would load its protocol
#: modules into the spatial unit's process. The A50 X has the A50 Gen 4's layout, Voice on
#: PCM device 0 and Game on 1, and PipeWire's default.conf gives it Voice alone (pw-dump,
#: 2026-10-21): its WirePlumber rule gives it Gearbench's profile set, A50_X_PROFILE_SET, whose
#: stereo-game is device 1, and it plays to that, its card's device.name,
#: alsa_card.usb-Logitech_A50_X-00, naming the node (pw-dump, 2026-10-22).
A50_X_GAME = "alsa_output.usb-Logitech_A50_X-00.stereo-game"
#: Hard-coded in the A50 X's WirePlumber rule; the tests hold the rule to it.
A50_X_CHAT = "alsa_output.usb-Logitech_A50_X-00.stereo-chat"
A50_X_PROFILE_SET = "gearbench-astro-a50x.conf"
A50_X = Headset(
    key="a50x", name="Astro A50 X",
    card=(("device.vendor.id", (G432_VENDOR_ID,)), ("device.product.id", ("0x0b0b",))),
    game=A50_X_GAME, plays_on="the Astro A50 X's Game output", linked_to="A50 X Game",
    waited_for="a Logitech G headset's output", profile_set=A50_X_PROFILE_SET,
    outputs_apart="A50 X Game and Chat apart", outputs_joined="Game not listed ({})",
    joined_shown="The A50 X's Game output is not listed", chat=A50_X_CHAT)


def first_output(key: str, name: str, short: str, products: tuple[str, ...]) -> Headset:
    """A headset not read off one, so assumed: it plays to its card's first output with a
    name, found by Logitech's vendor id and any of `products`, under PipeWire's
    default.conf, until a first connect reads both; Gearbench ships no rule for its card.
    `short` is the shorter name its summary and window words use."""
    return Headset(
        key=key, name=name,
        card=(("device.vendor.id", (G432_VENDOR_ID,)), ("device.product.id", products)),
        game=None, plays_on=f"the {name}'s first output", linked_to=f"the {short}",
        waited_for="a Logitech G headset's output", profile_set="default.conf",
        outputs_apart="the outputs PipeWire gives it", outputs_joined="another profile set ({})",
        joined_shown=f"The {short} lists other outputs", ruled=False)


#: The A50 Gen 5's first output taken to be its Game output, which G HUB's device list names
#: "A50 Game".
A50_GEN5 = first_output("a50gen5", "Astro A50 Gen 5", "A50 Gen 5", ("0x0b1c",))
#: The PRO X 2 LIGHTSPEED's receiver's one output (G HUB lists one audio interface for it).
PRO_X_2 = first_output("pro_x_2", "PRO X 2 LIGHTSPEED", "PRO X 2", ("0x0af7",))
#: The G522 LIGHTSPEED's, through its receiver or on its cable, and the G325 LIGHTSPEED's
#: (G HUB lists one audio interface for each of their ids).
G522 = first_output("g522", "G522 LIGHTSPEED", "G522", ("0x0b18", "0x0b19"))
G325 = first_output("g325", "G325 LIGHTSPEED", "G325", ("0x0b21",))
#: In the order the window's picker and `headset-spatial --headset` list them.
HEADSETS = (
    A50, *G432_FAMILY, A50_X, A50_GEN5, PRO_X_2, G522, G325)
#: The headsets the chain plays to, by the key spatial.json keeps the one picked under,
#: which is gearbench.g432.MODELS' for the G432's family.
HEADSET_KEYED = {headset.key: headset for headset in HEADSETS}
#: The words for no headset picked, which plays to the one connected first (pick), and the
#: CLI's for no keyboard chosen to lead.
FIRST_CONNECTED = "the one connected first"


def headset_of(key: object) -> Headset | None:
    """The headset of HEADSETS whose key is `key`, a value pw-dump or State holds; None for
    none."""
    return next((headset for headset in HEADSETS if headset.key == key), None)


def card_of(headset: Headset, objects: Sequence[object]) -> tuple[dict, list[dict], int] | None:
    """`headset`'s card in pw-dump's objects: of the Devices with each of its card's props
    (pipewire_client.card_nodes), the one that came last among those with its output
    (output_of), else the one that came last, with that card's own nodes and when it came,
    its object.serial, which PipeWire counts up, else its id; None without one. A headset
    found by two ids, as the G522 by its receiver's and its cable's, so plays on the one
    plugged in last (the owner's decision of 2026-10-22)."""
    cards, nodes = pipewire_client.card_nodes(objects, headset.card)
    if not cards:
        return None

    def came(card: int) -> int:
        serial = pipewire_client.as_whole(cards[card].get("object.serial"))
        return card if serial is None else serial

    def own(card: int) -> list[dict]:
        return [props for props in nodes
                if pipewire_client.as_whole(props.get("device.id")) == card]

    card = max(cards, key=lambda found: (output_of(headset, own(found)) is not None, came(found)))
    return cards[card], own(card), came(card)


def output_of(headset: Headset, nodes: Sequence[dict]) -> str | None:
    """`headset`'s output among its card's `nodes` (pipewire_client.card_nodes): the
    Audio/Sink named its game, or for a headset with no game the first with a name; None
    while there is none."""
    for props in nodes:
        name = props.get("node.name")
        if (props.get("media.class") == "Audio/Sink" and isinstance(name, str)
                and (headset.game is None or name == headset.game)):
            return name
    return None


def present(objects: Sequence[object]) -> list[tuple[Headset, str]]:
    """The headsets whose output is in pw-dump's objects, each with that output's name, in
    the order their cards came (card_of): the one connected first, first."""
    found = []
    for headset in HEADSETS:
        card = card_of(headset, objects)
        if card is not None and (output := output_of(headset, card[1])) is not None:
            found.append((card[2], headset, output))
    return [(headset, output) for _came, headset, output in sorted(found, key=lambda f: f[0])]


def pick(found: Sequence[tuple[Headset, str]], picked: str | None) -> tuple[Headset, str] | None:
    """The headset the chain plays to among `found`, present's, with its output: the one
    picked while it is there, else the one connected first, which with two is the other one
    when the pick is unplugged (the owner's decision of 2026-10-18); None for none."""
    return next((each for each in found if each[0].key == picked), found[0] if found else None)


def card_set(props: dict) -> str:
    """A card's profile set, by its Device's props: ACP reads default.conf when the card
    names none. Text, as State has it: one that cannot be printed is refused with the rest
    (_graph), where describe would raise on it."""
    return str(props.get("device.profile-set") or "default.conf")


def stranded(objects: Sequence[object]) -> tuple[tuple[str, str], ...]:
    """The headsets with a named output, their `game`, whose card is in pw-dump's objects
    without it, each with its card's profile set: plugged in, with nothing the chain can
    play to, as an A50 X whose card fell back to PipeWire's default.conf."""
    found = []
    for headset in HEADSETS:
        card = card_of(headset, objects) if headset.game is not None else None
        if card is not None and output_of(headset, card[1]) is None:
            found.append((headset.key, card_set(card[0])))
    return tuple(found)
