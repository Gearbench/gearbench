"""What a keyboard's setup reads off it that its firmware and board fix: its feature table, its
lighting's tables, its G and M key counts and its control list, read at a connect or taken from
the last connect of the same keyboard on the same route (Known). The same keyboard is the one
whose getDeviceInfo (0x0003 fn 0, its unit id among it) and running main application (fn 1)
answer as they did then; kept in memory alone, so each start of the daemon reads them all
again. Each connect builds the controller's capabilities, 0x1D4B index, lighting, G keys and
FN's reports from them (take_parts), and reads what the first paint does not need after it
(finish)."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from ...hidpp import (
    F_DEVICE_INFO,
    FN_DEVICE_INFO,
    FN_FW_INFO,
    MODEL_IDS_SINCE,
    UNIT_ID,
    Feature,
    HidppError,
    UnreadableAnswer,
    running_application,
)
from ...keyboard import (
    MODEL_OF as KEYBOARDS,
    capabilities as keyboard_capabilities,
    name as keyboard_name,
)
from ...keyboard_zones import KeyboardZones, power_modes
from ...keys import DivertedKeys
from ...lighting import Lighting, LightingTables
from ...modifiers import F_MODIFIERS, Modifiers
from ..shared import F_WIRELESS

if TYPE_CHECKING:
    from ...hidpp import Hidpp
    from . import Keyboard

log = logging.getLogger("gearbench")


@dataclass(frozen=True)
class Identity:
    """One keyboard on one firmware: getDeviceInfo's reply, its unit id among it, and the index
    and getFwInfo reply of the main application it runs."""

    device_info: bytes
    entity: int
    firmware: bytes


@dataclass(frozen=True)
class Kept:
    """What a connect read off the keyboard of `identity` that its firmware and board fix: each
    feature it resolved (Hidpp.known_features), its 0x8071 lighting's tables, None for 0x8070's
    zones, for none or where it refused its zone bitmap (Lighting.tables), its G and M key
    counts, None without G keys, and its control list, None where it has or gave none."""

    identity: Identity
    features: Mapping[int, Feature | None]
    lighting: LightingTables | None
    key_counts: tuple[int, int] | None
    controls: tuple[int, ...] | None


def read_identity(dev: Hidpp) -> Identity | None:
    """The identity of the keyboard on `dev`: getDeviceInfo, then getFwInfo of each firmware
    entity in turn up to the active main application (hidpp.running_application). None where
    it has no 0x0003 that gives a unit id, gives a zero one, cuts an entity's reply short or
    runs no main application it lists: then nothing can tell it from another of its model."""
    found = dev.feature(F_DEVICE_INFO)
    if found is None or found.version < MODEL_IDS_SINCE:
        return None
    info = dev.request(found.index, FN_DEVICE_INFO)
    if not any(info[UNIT_ID]):
        return None
    try:
        running = running_application(
            lambda entity: dev.request(found.index, FN_FW_INFO, bytes([entity])), info[0],
            "the keyboard")
    except UnreadableAnswer:
        return None
    return None if running is None else Identity(info, *running)


def same_device(dev: Hidpp, identity: Identity) -> bool:
    """Whether the keyboard on `dev` is the one of `identity` on the same firmware: its
    getDeviceInfo and its main application's getFwInfo answer as they did, the second unasked
    where the first differs."""
    found = dev.feature(F_DEVICE_INFO)
    return (found is not None
            and dev.request(found.index, FN_DEVICE_INFO) == identity.device_info
            and dev.request(found.index, FN_FW_INFO, bytes([identity.entity]))
            == identity.firmware)


class Known:
    """What the controller's connects read off each keyboard that its firmware fixes, by the
    product id of the route it came on."""

    def __init__(self, keyboard: Keyboard) -> None:
        self.keyboard = keyboard
        #: What the last connect on each route read off its keyboard, by the route's product id.
        self.kept: dict[int, Kept] = {}

    def recall(self, product: int) -> Kept | None:
        """What the last connect on the route of id `product` read, where the keyboard now open
        on it is the same one on the same firmware (same_device), its feature table taken as
        resolved (Hidpp.know); None at a first connect, for another keyboard or firmware, and
        where it refuses to say. Its receiver unable to reach it, or no answer, raises into the
        setup's guard."""
        kept = self.kept.get(product)
        if kept is None:
            return None
        dev = cast("Hidpp", self.keyboard.dev)
        name = keyboard_name(product)
        try:
            same = same_device(dev, kept.identity)
        except HidppError as exc:
            if exc.unreachable:
                raise
            log.debug("the %s refused to say which it is (%s); reading it whole", name, exc)
            return None
        if not same:
            log.debug("the %s is not the one of the last connect on this route, or runs other "
                      "firmware; reading it whole", name)
            return None
        dev.know(kept.features)
        log.debug("the %s is the one of the last connect on this route; what that connect "
                  "read is taken", name)
        return kept

    def take_parts(self, product: int) -> Kept | None:
        """The controller's capabilities, 0x1D4B index, lighting, G keys and FN's reports on its
        open keyboard of id `product`, each built from what its last connect kept where that is
        the same keyboard (recall), which it returns, else read off it, a lighting's 0x8081
        zone bitmap among it for the fills' last zone (Paint.fill_last)."""
        keyboard, model = self.keyboard, KEYBOARDS[product]
        kept = self.recall(product)
        dev = cast("Hidpp", keyboard.dev)
        keyboard.keyboard_able = keyboard_capabilities(dev, model)
        wireless = dev.feature(F_WIRELESS)
        keyboard.keyboard_wireless = wireless.index if wireless else None
        tables = None if kept is None else kept.lighting
        keyboard.lighting = (KeyboardZones.find(dev, area_zones=model.area_zones)
                             if model.zone_lighting
                             else Lighting.find(dev, zone_bitmap=True, whole_board=model.whole_board)
                             if tables is None else Lighting(dev, tables=tables))
        keyboard.keys = (DivertedKeys(dev, None if kept is None else kept.key_counts)
                         if "g_keys" in keyboard.keyboard_able else None)
        fn_lit = (model.fn_keys and keyboard.lighting is not None and keyboard.lighting.per_key
                  and dev.feature(F_MODIFIERS) is not None)
        keyboard.modifiers = Modifiers(dev) if fn_lit else None
        return kept

    def finish(self, product: int, kept: Kept | None) -> None:
        """After the first paint: the custom assignments' reads (KeyControl.set_up), `kept`'s
        control list in place of a read, then what this connect read kept for the next on the
        route of id `product`, under the keyboard's identity, read off it now where `kept`
        does not give it. A keyboard with none, or that refuses to say, keeps nothing."""
        keyboard = self.keyboard
        keyboard.key_control.set_up(None if kept is None else kept.controls)
        dev = cast("Hidpp", keyboard.dev)
        try:
            identity = read_identity(dev) if kept is None else kept.identity
        except HidppError as exc:
            if exc.unreachable:
                raise
            log.debug("the %s refused to say which it is (%s); its next connect reads it "
                      "whole", keyboard_name(product), exc)
            identity = None
        if identity is None:
            self.kept.pop(product, None)
            return
        lighting, keys = keyboard.lighting, keyboard.keys
        self.kept[product] = Kept(
            identity, dev.known_features(),
            lighting.tables if lighting is not None and power_modes(lighting) else None,
            None if keys is None else (keys.g_count, keys.m_count),
            keyboard.key_control.controls)
