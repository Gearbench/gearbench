"""0x8070 COLOR_LED_EFFECTS lighting: a device's zones and their effects. 0x8070 is the older
of the two lighting features, zones with effects and nothing more: no per-key lighting,
brightness, power modes or idle notices. Of the mice, the daemon drives it only on a model
whose `MouseModel` says so (`mouse.ZONE_LIGHTING`).

The functions are numbered as libratbag's hidpp20.c, Solaar's settings_templates.py and
OpenRGB's LogitechProtocolCommon.h number them, and the G502 HERO's answers to the reads are
the ones Solaar's issue 2313 logged off a G502 SE HERO.

The PRO WIRELESS's firmware stores a zone effect to flash unless fn 8 `01` came first, and
clears that byte at a power-state change (its image, read 2026-10-18). On such a model
(`claim_each_effect`) every effect write goes right after a claim; the G502 HERO, cabled, is
claimed once, before its first write after a setup or a release.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Self

from .hidpp import REQUEST_RETRIES, REQUEST_TIMEOUT, Hidpp
from .lighting import (
    CLUSTERS_MAX,
    EFFECTS_MAX,
    Cluster,
    Effect,
    EffectParams,
    Lighting,
    default_params,
    effect_parameters,
    need_reply,
)

F_COLOR_LED = 0x8070

# fn 0 [] -> [zone count, nv caps BE16, ext caps BE16]; fn 1 [zone] -> [zone, location BE16,
# effect count, persistency caps]; fn 2 [zone, effect] -> [zone, effect, id BE16, caps BE16,
# period BE16]; fn 3 [zone, effect, 10 parameter bytes, persistence]; fn 8 [control], 1 the
# host's, 0 the device's (Solaar's "LED Control", OpenRGB's SET_SW_CTL). fn 4 [setting BE16]
# reads a setting of the feature's non-volatile configuration and fn 5 [setting BE16, value]
# writes it, as G HUB reads and writes a G933's startup effect (the Windows captures of
# 2026-10-25); neither goes to a mouse, since the PRO WIRELESS's image stores a default at fn 4
# where it holds none.
GET_INFO, GET_ZONE_INFO, GET_EFFECT_INFO, SET_EFFECT, SET_CONTROL = 0, 1, 2, 3, 8
GET_NV_CONFIG, SET_NV_CONFIG = 4, 5
HOST_CONTROL, DEVICE_CONTROL = 0x01, 0x00

# fn 3's last byte. libratbag sends 0x01 there and calls it "write to RAM and flash"; Solaar
# and OpenRGB send 0x00 on 0x8070, where OpenRGB sends 0x01 on 0x8071. The HERO answers
# persistency caps 0x00 for both zones, and its firmware image 127.03.10 never reads the
# byte (read 2026-10-17; the owner's HERO runs 169.00.9). On the owner's HERO a red sent with
# it, the claim held, did not outlive a replug (2026-10-18). The PRO WIRELESS's image
# 15.02.26 never reads it either: whether it stores an effect goes by fn 8.
RAM_ONLY = 0x00
#: fn 3's last byte for an effect the device keeps, as G HUB's "effects on device" writes a
#: G933's (the Windows captures of 2026-10-25); sent to a headset alone (headset_lights).
STORED = 0x02


class ZoneLighting:
    """0x8070's zones on one device, each a lighting.Cluster so the zones are named and
    resolved as 0x8071's clusters are."""

    #: fn 8's parameters for a claim: the host's control alone, or a kind's own.
    CLAIM = bytes([HOST_CONTROL])

    def __init__(self, dev: Hidpp, *, claim_each_effect: bool) -> None:
        self.dev = dev
        self._index = dev.require(F_COLOR_LED, "COLOR_LED_EFFECTS").index
        #: Whether each effect write goes right after a claim (a mouse's: mouse.CLAIM_EACH_EFFECT).
        self.claim_each_effect = claim_each_effect
        #: fn 0's non-volatile caps, a bit a setting of fn 4 and fn 5, as _read_zones reads them.
        self.nv_caps = 0
        self.clusters = self._read_zones()

    @classmethod
    def find(cls, dev: Hidpp, *, claim_each_effect: bool) -> Self | None:
        """`dev`'s zones, or None when it has no 0x8070 or its 0x8070 lists no zone."""
        if dev.feature(F_COLOR_LED) is None:
            return None
        found = cls(dev, claim_each_effect=claim_each_effect)
        return found if found.clusters else None

    def _read_zones(self) -> tuple[Cluster, ...]:
        """Each zone, its location and its effects, CLUSTERS_MAX and EFFECTS_MAX at most, asked
        for one at a time, after fn 0's count and its non-volatile caps, kept as `nv_caps`. A
        reply too short to read raises hidpp.UnreadableAnswer (need_reply)."""
        zones = []
        # Hidpp drops a report shorter than its id's length, so a reply has 3 bytes at least.
        info = self.dev.request(self._index, GET_INFO)
        count, self.nv_caps = info[0], int.from_bytes(info[1:3], "big")
        for zone in range(min(count, CLUSTERS_MAX)):
            info = need_reply(self.dev.request(self._index, GET_ZONE_INFO, bytes([zone])), 4,
                              f"0x8070 fn 1 zone {zone} info")
            effects = []
            for index in range(min(info[3], EFFECTS_MAX)):
                reply = need_reply(
                    self.dev.request(self._index, GET_EFFECT_INFO, bytes([zone, index])), 4,
                    f"0x8070 fn 2 zone {zone} effect {index}")
                effects.append(Effect(cluster=zone, index=reply[1],
                                      id=int.from_bytes(reply[2:4], "big")))
            zones.append(Cluster(zone, int.from_bytes(info[1:3], "big"), tuple(effects)))
        return tuple(zones)

    def claim(self) -> None:
        """fn 8 with CLAIM: the host's effects shown in place of the onboard profile's.
        Solaar's LED Control says its zone effects show only with this set."""
        self.dev.request(self._index, SET_CONTROL, self.CLAIM)

    def release(self, timeout: float = REQUEST_TIMEOUT, retries: int = REQUEST_RETRIES) -> None:
        """fn 8 `00`: the lighting back to the device, which shows its onboard profile's."""
        self.dev.request(self._index, SET_CONTROL, bytes([DEVICE_CONTROL]), timeout=timeout,
                         retries=retries)

    def set_effect(self, zone: int, effect: str | int, *, wait: bool = True,
                   **params: int) -> None:
        """Write one zone's effect, RAM_ONLY, waiting for its reply unless `wait` is False,
        after its claim, waited for, on a model that claims each effect (_claim_for_effect).
        `zone` is its index, which is its place in `clusters`."""
        payload = self._payload(zone, effect, params)
        self._claim_for_effect()
        self.dev.request(self._index, SET_EFFECT, payload, long=True, reply=wait)

    def send_effect(self, zone: int, effect: str | int, **params: int) -> tuple[int, int, int]:
        """One zone's effect sent without waiting, for a mouse that may be asleep: the key of
        its reply (Hidpp.answer). Never sent to a model that claims each effect, whose claim
        is answered before its effect goes (MouseLighting.send_or_write)."""
        return self.dev.send(self._index, SET_EFFECT, self._payload(zone, effect, params),
                             long=True)

    def _claim_for_effect(self) -> None:
        """The claim right before an effect write on a model that claims each effect
        (claim_each_effect), waited for; nothing on another. A claim refused or unanswered
        raises, and its effect is not sent."""
        if self.claim_each_effect:
            self.dev.request(self._index, SET_CONTROL, self.CLAIM)

    def _payload(self, zone: int, effect: str | int, params: Mapping[str, int],
                 persistence: int = RAM_ONLY) -> bytes:
        """fn 3's parameters, checked and laid out as 0x8071's are (lighting.effect_parameters),
        RAM_ONLY or a headset's STORED last: an effect the zone lacks (Cluster.effect), a
        parameter the effect lacks and a value it does not take each raise LightingError."""
        chosen = self.clusters[zone].effect(effect)
        return bytes([zone, chosen.index, *effect_parameters(chosen, params), persistence])

    def effect_params(self, zone: int, effect: str | int, color: int,
                      overrides: Mapping[str, int] | None = None) -> EffectParams:
        """The parameters a zone's effect takes (lighting.default_params)."""
        return default_params(self.clusters[zone].effect(effect), color, overrides)


#: A mouse's lighting, as the daemon's mice drive it: 0x8071's clusters or 0x8070's zones.
type MouseLights = Lighting | ZoneLighting
