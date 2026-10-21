"""Which keyboard is attached, what it can do, and what it shows of the lighting a profile keeps
for another model: copy first, for a keyboard with none of its own (`shown_lighting`).

Capabilities are probed, as the mouse's are, since what answers matters more than the model.
The G915 is the only keyboard measured in full; where each other model's ids and features come
from is said beside its KeyboardModel and in MODEL_OF's comment, and `tested` marks those
Gearbench has run on.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path

from .battery import G915_CELL, CellCurves, has_battery, read as read_any_battery
from .config import (
    DEFAULT_BRIGHTNESS,
    F_ROW_KEYS,
    G_KEYS,
    STATIC,
    Lighting,
    ModelKeys,
    entry_under,
    keep_under,
)
from .hidpp import Hidpp, find_attached, find_each_attached
from .keycodes import ZONES
from .keys import F_GKEY, F_MKEYS, F_MR
from .layout import G515_BOARD, G815_BOARD, G915_BOARD, G915_TKL_BOARD, Board
from .lighting import F_BRIGHTNESS, F_PERKEY, F_RGB, WAVE_DIRECTIONS
from .onboard import F_ONBOARD

#: The vendor HID++ collection's interface on the G915, on the cable and the c541 receiver
#: alike; 00 and 01 carry keystrokes and a pointer and are never opened. The c541 gives the
#: keyboard no node of its own (udev/70-g915.rules).
VENDOR_INTERFACE = 2

#: A keyboard has GKEY and a receiver does not, which is what `hidpp.open_device` uses to tell
#: the G915 from its receiver on the receiver's own node.
WITNESS = F_GKEY


@dataclass(frozen=True)
class KeyboardModel:
    """What this project knows of one keyboard model, on every route it answers on."""

    #: Its name, as Gearbench shows and logs it.
    name: str
    #: The feature `hidpp.open_device` asks each device index for, which the keyboard has and
    #: a receiver lacks.
    probe: int
    #: The keys the window draws.
    board: Board
    #: The wave directions it takes, by lighting.WAVE_DIRECTIONS' names.
    wave_directions: tuple[str, ...]
    #: The USB interface of its vendor HID++ collection, which `find_attached` matches and its
    #: udev rule grants.
    interface: int = VENDOR_INTERFACE
    #: Whether a per-key paint is cut to the board's zones before it is sent. The G915 is sent
    #: every zone a request names, and lights those it has.
    cut_to_board: bool = False
    #: The names its G keys bind under, from G1 on, as the window labels them: config's
    #: G_KEYS or F_ROW_KEYS, the names a layer of bindings keeps (bound_as). Empty for a
    #: keyboard with no G keys.
    g_key_names: tuple[str, ...] = ()
    #: The key each of its G keys types while nothing is bound to it, from G1 on, in a
    #: binding's spelling, where its G keys are keys of its own board that only its onboard
    #: profile makes type: in HOST mode Gearbench types them (keystroke), and its stop hands
    #: it ONBOARD mode back, a shutdown's included, or they type nothing until it is switched
    #: off and on. Empty where an unbound G key types nothing.
    g_key_defaults: tuple[str, ...] = ()
    #: Whether its HID++ collection declares the long report alone, so that every request goes
    #: out long (Hidpp.long_only), the probe's included: the G815's interface 01.
    long_only: bool = False
    #: Whether Gearbench has run on one on the hardware. Until it has, the front ends and the
    #: journal ask for a device report from it (report.untested).
    tested: bool = False
    #: The cell its 0x1001 millivolts are read against for a percentage, or None: then it
    #: reports volts and no percentage, since another cell's curve would give a wrong number.
    cell: CellCurves | None = None


#: The G915's wave directions: the four the owner watched on it on 2026-09-24, and
#: reverse-vertical and center-in, 7 and 8, which the owner watched run as named on it on
#: 2026-10-18 (hardware round 2) and its firmware image's direction check takes; its own
#: lighting key runs 7 (read 2026-10-17). The check refuses G HUB's 4 and 5, which
#: lighting.WAVE_DIRECTIONS leaves out, after the write is answered as a success.
G915_WAVE = ("horizontal", "vertical", "center-out", "reverse-horizontal", "reverse-vertical",
             "center-in")
G915 = KeyboardModel("G915", WITNESS, G915_BOARD, wave_directions=G915_WAVE,
                     g_key_names=G_KEYS, tested=True, cell=G915_CELL)
# No 0x8010 in the wired G515's feature table (read 2026-10-17), so the probe asks for
# 0x8071: cabled, the G515 answers on 0xFF, where no receiver is. Its HID++ collection is on
# interface 02, as the G915's. A zone it lacks is cut, since how its firmware takes one is not
# known. Its wave, COLORWAVE_S, takes the G915's directions: the owner watched each of the six
# run as named on a wired G515 TKL on 2026-10-18. The G515 LIGHTSPEED TKL is taken to be the
# same; through its receiver it answers on a node of its own (MODEL_OF).
G515_TKL = KeyboardModel("G515 TKL", F_RGB, G515_BOARD, wave_directions=G915_WAVE,
                         cut_to_board=True, tested=True)
G515_LIGHTSPEED = KeyboardModel("G515 LIGHTSPEED TKL", F_RGB, G515_BOARD,
                                wave_directions=G915_WAVE, cut_to_board=True)
# The G915's feature tables and lighting code, from its firmware image (MPK14.02_B0022, read
# 2026-10-17), with a board of its own. A paint naming a zone it has no LED for is refused
# whole there (error 2), so its paints are cut. Its wave takes the G915's directions: the
# same firmware's handler, its own direction check not read. Its G1 to G12 are its F row,
# F1 to F12 in its onboard profile; in HOST mode they type nothing unless Gearbench types
# them (the owner's decision of 2026-10-18, "Gearbench types F keys"). They bind as the
# G915's G keys do, each typing its own key while unbound, under names of their own (the
# owner's decisions of 2026-10-18, "Bindable, F keys by default" and "Their own"): the names
# on their keycaps, Claude Code's call of 2026-10-18, pending the owner. The G913 TKL is on
# the same ids. It takes the G915's cell: the power model in G HUB's
# g915_tkl_us package has the G915's discharge and charging curves point for point, and
# differs only in its charging time and power figures (compared 2026-10-18).
G915_TKL = KeyboardModel("G915 TKL", WITNESS, G915_TKL_BOARD, wave_directions=G915_WAVE,
                         cut_to_board=True, g_key_names=F_ROW_KEYS,
                         g_key_defaults=tuple(name.lower() for name in F_ROW_KEYS),
                         cell=G915_CELL)
# The G915's lighting, keys and onboard features at the G915's versions, and its replies byte
# for byte (Solaar's listing of U1 31.02.B0018, five more units' in Solaar's issues, and the
# raw HID++ of Solaar issue 2968 and libratbag issues 1127 and 1752); cabled alone. Its HID++
# shares interface 01 with what is typed on it, and that collection declares the long report
# alone, no short one. Its paints are cut, since how this firmware takes a zone it lacks is
# not known; its zones are the G915's. Its wave takes the G915's directions: the same effect
# records, its own direction check not read. The G813 is on the same id.
G815 = KeyboardModel("G815", WITNESS, G815_BOARD, wave_directions=G915_WAVE, interface=1,
                     cut_to_board=True, g_key_names=G_KEYS, long_only=True)

#: Each product id's model, in the order they are looked for: the G915 on its cable, then on
#: its receiver (both read off the hardware 2026-09-22), then the wired-only G515 TKL and the
#: G515 LIGHTSPEED TKL on its cable (G HUB's device database), then the G515 LIGHTSPEED TKL on
#: a node of its own that hid-logitech-dj gives it behind its c547 receiver, which the installed
#: 7.0.0 kernel binds, as the G502 X PLUS's 4099 node shows (Solaar 1.1.13's listing of it on
#: 40B4), after its cable, then the G915 TKL on its cable, on its own node on a kernel whose
#: hid-logitech-dj binds its c545 receiver, and
#: through the receiver's own node where hid-generic holds the receiver, as the installed
#: 7.0.0 kernel does (the image's ids and Solaar's records). The node of its own goes first:
#: a kernel whose hid-logitech-dj binds the c545 keeps the receiver's node too, and hands the
#: keyboard's replies to the other (inferred from the c547, where the G502 X PLUS answers on
#: a node of its own). Then the G815, on its cable alone (Solaar's listing; G HUB's record).
MODEL_OF: dict[int, KeyboardModel] = {
    0xC33E: G915,
    0xC541: G915,
    0xC358: G515_TKL,
    0xC355: G515_LIGHTSPEED,
    0x40B4: G515_LIGHTSPEED,
    0xC343: G915_TKL,
    0x408E: G915_TKL,
    0xC545: G915_TKL,
    0xC33F: G815,
}
PRODUCT_IDS = tuple(MODEL_OF)
MODELS = {product: model.name for product, model in MODEL_OF.items()}
CELLS = {product: model.cell for product, model in MODEL_OF.items() if model.cell is not None}


#: The names a model's lighting and brightness are kept under in config.json and a game
#: profile's file: its ids, by the first, its key, "c33e" for the G915 on its cable and on its
#: receiver, so a c541 written by hand is the G915's. In MODEL_OF's order, which copy first
#: falls back on.
KEYING = ModelKeys(MODEL_OF)
MODEL_IDS = KEYING.ids
MODEL_KEYS = tuple(MODEL_IDS)
model_key, model_ids, model_named = KEYING.model_key, KEYING.model_ids, KEYING.model_named

#: The G915's: G HUB's lighting, which the G HUB import reads off the G915's settings alone, and
#: the pack's, drawn for the G915, are kept under it.
G915_KEY = model_key(0xC33E)


def key_name(key: str) -> str:
    """The name of the model whose key (MODEL_IDS) is `key`, for a line or a label: "keyboard c999"
    for any other id, a model's other ids included."""
    model = model_named(key)
    return f"keyboard {key}" if model is None else model.name


def wave_directions(key: str | None) -> tuple[str, ...]:
    """The wave directions the model whose key is `key` takes (KeyboardModel.wave_directions):
    every one for any other id and for None, before any keyboard, as nothing is cut then."""
    model = model_named(key)
    return tuple(WAVE_DIRECTIONS) if model is None else model.wave_directions


# -- copy first: a keyboard with no lighting of its own in a profile ----------

def own_entry[T](entries: Mapping[str, T], key: str) -> T | None:
    """What `entries`, kept by keyboard model, holds for the model named `key`, under any of its
    ids (model_ids); None for nothing."""
    return entry_under(entries, model_ids(key))


def keep_entry[T](entries: dict[str, T], key: str, entry: T) -> None:
    """Keep `entry` in `entries` as the model named `key`'s, and drop one under its other ids,
    which own_entry would read in its place: a G915 entry under c541, written by hand."""
    keep_under(entries, model_ids(key), entry)


def source_entry[T](entries: Mapping[str, T], key: str | None, leader: str | None) -> T | None:
    """The entry a keyboard of model `key` starts from where `entries` holds none of its own:
    `leader`'s, the keyboard chosen to lead or attached first, then a model's with the same
    board, then each model's in MODEL_OF's order, the G915's first. With `key` None, before any
    keyboard was attached, the leader's and then MODEL_OF's order. None when none of them has
    an entry."""
    model = model_named(key)
    same = [other for other in MODEL_KEYS
            if model is not None and MODEL_OF[int(other, 16)].board == model.board]
    first = [leader] if leader else []
    return next((entry for other in [*first, *same, *MODEL_KEYS]
                 if (entry := own_entry(entries, other)) is not None), None)


def cut_lighting(entry: Lighting, model: KeyboardModel, effects: Collection[str] | None,
                 per_key: bool | None) -> Lighting:
    """`entry`, another keyboard model's lighting, cut to what a keyboard of `model` can show:
    what it starts from with no lighting of its own in a profile. `effects` are the effects it
    reports and `per_key` whether it has per-key lighting, each None while that is not known,
    which cuts nothing for it.

    An effect it does not report becomes static and the mode stays, as the board shows one
    (Paint.write_lit). A painted key stays only where its board has the key. A board without
    per-key lighting is static. A wave direction outside its wave_directions is dropped, so
    wave runs in its default one. A hand-set `params`, set for the other model's firmware,
    is dropped. The colors, the background and the speeds stay. The copy shares nothing with
    `entry`."""
    effect = entry.effect if effects is None or entry.effect in effects else "static"
    direction = entry.direction if entry.direction in model.wave_directions else None
    return Lighting(effect=effect, color=entry.color, speeds=dict(entry.speeds),
                    direction=direction,
                    keys={name: color for name, color in entry.keys.items()
                          if ZONES.get(name) in model.board.zones},
                    mode=STATIC if per_key is False else entry.mode,
                    background=entry.background)


def shown_brightness(levels: Mapping[str, int], key: str | None,
                     leader: str | None) -> tuple[int, bool]:
    """The brightness `levels`, kept by model, give a keyboard of model `key`, and whether it is
    the model's own: its level, else source_entry's, else DEFAULT_BRIGHTNESS. The
    keyboard floors it to a level it keeps as it is written (lighting.snap_brightness)."""
    own = None if key is None else own_entry(levels, key)
    if own is not None:
        return own, True
    source = source_entry(levels, key, leader)
    return (DEFAULT_BRIGHTNESS if source is None else source), False


def shown_lighting(entries: Mapping[str, Lighting], key: str | None, leader: str | None,
                   effects: Collection[str] | None = None,
                   per_key: bool | None = None) -> tuple[Lighting, bool]:
    """The lighting a profile's `entries` show on a keyboard of model `key`, and whether it is
    the model's own: its entry, else source_entry's, cut for it (cut_lighting, with
    `effects` and `per_key`), else the defaults. With `key` None, before any keyboard, the
    source's whole. Its own entry and the source's whole are the profile's own objects, not
    copies."""
    own = None if key is None else own_entry(entries, key)
    if own is not None:
        return own, True
    source = source_entry(entries, key, leader)
    entry = Lighting() if source is None else source
    model = model_named(key)
    return (entry if model is None else cut_lighting(entry, model, effects, per_key)), False

#: The ids a keyboard reports on its own cable; the others are its receiver's. They decide
#: the route, never the interface: cabled, the keyboard is on external power and its battery
#: is not asked; on the receiver the cable is looked for by these ids, and on a receiver's
#: own node the link notices are turned on (RECEIVER_NODE_IDS).
CABLED_IDS = frozenset({0xC33E, 0xC358, 0xC355, 0xC343, 0xC33F})

#: The ids of a receiver's own node, which a keyboard is reached through on its slot, and
#: where the receiver's registers are: its link notices are turned on there
#: (Keyboard.turn_on_link_notices). A keyboard on a node of its own behind its receiver, the
#: G915 TKL's 408E or the G515 LIGHTSPEED TKL's 40B4, is not on it: hid-logitech-dj writes the
#: keyboard's slot into every request sent there (hidpp.attached_index), so a register request
#: would go to the keyboard. That driver turns the receiver's link notices on itself
#: (hidpp.NOTIFICATION_FLAGS) and hands the keyboard's node each one for its slot.
RECEIVER_NODE_IDS = frozenset({0xC541, 0xC545})

#: Capability to the feature that provides it, in the order front ends list them.
CAPABILITIES = (
    ("g_keys", F_GKEY),
    ("m_keys", F_MKEYS),
    ("mr", F_MR),
    ("brightness", F_BRIGHTNESS),
    ("per_key", F_PERKEY),
    ("onboard", F_ONBOARD),
)


def name(product: int | None) -> str:
    return MODELS[product] if product else "keyboard"


def read_battery(dev: Hidpp, product: int | None, name: str) -> dict | None:
    """battery.read against the cell of `product`'s model (CELLS): a 0x1001 reading with no
    cell gives volts and no percent."""
    return read_any_battery(dev, None if product is None else CELLS.get(product), name)


def find_attached_keyboard(cabled_only: bool = False, same_model_as: int | None = None,
                           leader: str | None = None) -> tuple[Path, int, int] | None:
    """The keyboard's vendor node as (node, product, index), on its model's interface: by
    PRODUCT_IDS' order, the ids of the model keyed `leader` (model_key) first, or with
    `cabled_only` among CABLED_IDS alone, and with `same_model_as` among the ids of that
    product's model alone. From sysfs; nothing is opened."""
    products = sorted(CABLED_IDS) if cabled_only else PRODUCT_IDS
    # sorted is stable: the leader's ids, and the others, keep their order.
    for product in sorted(products, key=lambda product: model_key(product) != leader):
        if same_model_as is not None and MODEL_OF[product] is not MODEL_OF[same_model_as]:
            continue
        found = find_attached(product=product, interface=MODEL_OF[product].interface)
        if found is not None:
            return found
    return None


def attached_keyboards() -> list[tuple[Path, int, int]]:
    """Each keyboard model plugged in now, in MODEL_KEYS' order, by the node
    find_attached_keyboard finds for it: the first of its ids with a node on its model's
    interface, by PRODUCT_IDS' order, so a receiver's own node counts with its keyboard off.
    From sysfs, over one walk; nothing is opened."""
    first: dict[str, tuple[Path, int, int]] = {}
    # PRODUCT_IDS holds each model's ids together, so the models come in MODEL_KEYS' order.
    for found in find_each_attached({product: MODEL_OF[product].interface
                                     for product in PRODUCT_IDS}):
        first.setdefault(model_key(found[1]), found)
    return list(first.values())


def capabilities(dev: Hidpp, model: KeyboardModel) -> list[str]:
    """What this keyboard of `model` can do, from its own feature table: CAPABILITIES, then
    `battery` for any of the battery features (battery.has_battery), which the wired G515 TKL
    lacks. A model whose G keys type its own keys while unbound
    (KeyboardModel.g_key_defaults) has `typed_keys` after `g_keys`: its stop hands it its
    onboard profile back.

    Not lighting: a device can advertise the feature with nothing behind it, so the
    daemon adds `lighting` once clusters enumerate.
    """
    able = [able for able, feature in CAPABILITIES if dev.feature(feature) is not None]
    if model.g_key_defaults:
        # Found by its 0x8010 (WITNESS), such a keyboard has g_keys.
        able.insert(able.index("g_keys") + 1, "typed_keys")
    return able + ["battery"] if has_battery(dev) else able


def m_profiles_in_menu(able: Collection[str]) -> bool:
    """Whether a keyboard that can do `able` (capabilities) switches its M profiles from the
    tray's menu and the window's M profile group: lighting and no M keys (the owner's decision
    of 2026-10-18)."""
    return "lighting" in able and "m_keys" not in able


def bound_as(product: int | None, key: str) -> str:
    """The name G key `key` ("G1"), as 0x8010 reports it, binds under on the keyboard of id
    `product` (KeyboardModel.g_key_names): "F1" on the G915 TKL. `key` itself past the model's
    names, and with no keyboard found yet."""
    names = () if product is None else MODEL_OF[product].g_key_names
    number = int(key[1:])
    return names[number - 1] if number <= len(names) else key


def keystroke(product: int | None, name: str) -> str | None:
    """What the G key bound under `name` types on the keyboard of id `product` while nothing
    is bound to it, in a binding's spelling: "f1" for the G915 TKL's F1
    (KeyboardModel.g_key_defaults). None for a model whose unbound G keys type nothing, for a
    name it has no default for, and with no keyboard found yet."""
    model = None if product is None else MODEL_OF[product]
    return None if model is None else dict(zip(model.g_key_names, model.g_key_defaults)).get(name)


def own_named_keys(keys: Collection[str]) -> list[str]:
    """The G keys of each model that binds them under names of its own, not the G915's G_KEYS,
    where `keys` holds its first, for a line: "G915 TKL's F1 to F12"."""
    named = {model.g_key_names: model.name for model in MODEL_OF.values()
             if model.g_key_names not in ((), G_KEYS)}
    return [f"{name}'s {names[0]} to {names[-1]}" for names, name in named.items()
            if names[0] in keys]


def typed_words(product: int | None) -> str | None:
    """What the G keys of the keyboard of id `product` type while unbound, for a line or a
    label: "F1 to F12" on the G915 TKL. None where keystroke gives none."""
    typed = () if product is None else MODEL_OF[product].g_key_defaults
    return f"{typed[0].upper()} to {typed[-1].upper()}" if typed else None
