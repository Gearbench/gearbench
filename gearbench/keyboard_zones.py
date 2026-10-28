"""A keyboard's 0x8070 COLOR_LED_EFFECTS zones, the G213 Prodigy's, as the keyboard's controller
drives them in place of `lighting.Lighting`'s 0x8071 clusters (`KeyboardZones`), and the
functions that tell the two apart.

A keyboard lit through 0x8070 gets a color on each zone, the animations its zones report, and
the dark, and it never dims for its own idleness: no notice says it is idle. Where its model
names the zone of each of its board's areas (keyboard.KeyboardModel.area_zones) and it lists
them all, a layer's painted keys show as their areas' colors.

The claim is the mice's 0x8070 claim, fn 8 `01`, sent again right before each effect write
(mouse_lighting.ZoneLighting's claim_each_effect), with the effect's persistence byte
RAM_ONLY, and the release fn 8 `00`: the PRO WIRELESS's firmware stores a zone effect written
without that claim first, and no source says whether the G213's does. That this keeps the
G213's writes out of its memory is not checked on the hardware.
"""

from __future__ import annotations

from typing import Self, TypeGuard

from .hidpp import REQUEST_RETRIES, REQUEST_TIMEOUT, Hidpp
from .lighting import Cluster, Lighting
from .mouse_lighting import F_COLOR_LED, ZoneLighting


class KeyboardZones(ZoneLighting):
    """0x8070's zones on a keyboard whose model says so (keyboard.KeyboardModel.zone_lighting):
    what the keyboard's controller asks of its lighting."""

    #: 0x8070 has no per-key lighting and no brightness, so a board of these zones takes no
    #: per-key paint, fill or frame, and no brightness is read or written.
    per_key = False
    brightness_levels: tuple[int, ...] = ()
    brightness_index: int | None = None
    #: 0x8070 has no cluster 0xff: its effects run on its zones alone
    #: (lighting.Lighting.whole_board).
    whole_board: Cluster | None = None

    def __init__(self, dev: Hidpp, *, claim_each_effect: bool,
                 area_zones: tuple[int, ...] = ()) -> None:
        super().__init__(dev, claim_each_effect=claim_each_effect)
        self._claimed = False
        listed = {cluster.index for cluster in self.clusters}
        #: The zone of each of its board's areas, `area_zones`, where it lists every one of
        #: them, so that each area takes a color of its own; empty otherwise.
        self.area_zones = area_zones if listed.issuperset(area_zones) else ()

    @classmethod
    def find(cls, dev: Hidpp, *, claim_each_effect: bool = True,
             area_zones: tuple[int, ...] = ()) -> Self | None:
        """`dev`'s zones with its board's `area_zones`, or None when it has no 0x8070 or its
        0x8070 lists no zone, as ZoneLighting.find; each effect claimed first, as on the
        G213, unless `claim_each_effect` is False."""
        if dev.feature(F_COLOR_LED) is None:
            return None
        found = cls(dev, claim_each_effect=claim_each_effect, area_zones=area_zones)
        return found if found.clusters else None

    @property
    def effect_clusters(self) -> tuple[Cluster, ...]:
        """Every zone an effect can be written to, as lighting.Lighting.effect_clusters gives
        its clusters."""
        return self.clusters

    @property
    def claimed(self) -> bool:
        """Whether the last answered of a claim and a release was the claim."""
        return self._claimed

    def claim(self) -> None:
        super().claim()
        self._claimed = True

    def release(self, timeout: float = REQUEST_TIMEOUT, retries: int = REQUEST_RETRIES) -> None:
        super().release(timeout, retries)
        self._claimed = False


#: A keyboard's lighting, as its controller drives it: 0x8071's clusters or 0x8070's zones.
type KeyboardLights = Lighting | KeyboardZones


def power_modes(lighting: KeyboardLights) -> TypeGuard[Lighting]:
    """Whether `lighting` is 0x8071's, with the claim's power modes, the idle times and the
    idle and wake notices: 0x8070's zones have none of them."""
    return not isinstance(lighting, KeyboardZones)


def paints_keys(lighting: KeyboardLights) -> TypeGuard[Lighting]:
    """Whether `lighting` takes a per-key paint, a fill and a frame: 0x8071 with 0x8081, as
    0x8070's zones never do (KeyboardZones.per_key)."""
    return lighting.per_key


def paints_areas(lighting: KeyboardLights) -> TypeGuard[KeyboardZones]:
    """Whether `lighting` shows a layer's painted keys as the colors of its board's areas:
    0x8070's zones with each area's zone listed (KeyboardZones.area_zones)."""
    return isinstance(lighting, KeyboardZones) and bool(lighting.area_zones)


def takes_brightness(lighting: KeyboardLights | None, zone_lighting: bool) -> bool:
    """Whether a keyboard takes a brightness: by its `lighting`'s levels where that is found,
    else by its model, none where `zone_lighting` says it is lit through 0x8070's zones,
    which have no brightness (KeyboardZones.brightness_levels)."""
    return bool(lighting.brightness_levels) if lighting is not None else not zone_lighting


def takes_paint(lighting: KeyboardLights) -> bool:
    """Whether `lighting` shows a layer's painted keys at all, key by key (paints_keys) or by
    area (paints_areas)."""
    return paints_keys(lighting) or paints_areas(lighting)


def lighting_capabilities(lighting: KeyboardLights) -> tuple[str, ...]:
    """What a keyboard's status lists for `lighting`, found: `lighting`, then `areas` where
    it paints its board's areas (paints_areas)."""
    return ("lighting", "areas") if paints_areas(lighting) else ("lighting",)
