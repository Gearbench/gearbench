"""A mouse's 0x8070 COLOR_LED_EFFECTS lighting: two zones, primary and logo, on the G502 HERO,
the G502 LIGHTSPEED and the PRO WIRELESS, each of the last two claimed before each effect.
The HERO's primary zone is its DPI stage lights, the color shown on the light of the stage in
use alone (watched 2026-10-18).

0x8070 is the older of the two lighting features, zones with effects and nothing more: no
per-key lighting, brightness, power modes or idle notices. `ZoneLighting` reads the zones and
their effects off the device, as `lighting.Lighting` reads 0x8071's clusters, and writes an
effect by its index in its zone, its parameters checked and placed where `lighting.EFFECTS`
puts them, as 0x8071's are (`lighting.effect_parameters`). The
daemon drives it only on a model whose `MouseModel` says so (`mouse.ZONE_LIGHTING`): the
G305 lists 0x8070 too, answers with one zone and accepts a color write, and has no lights.

The functions are numbered as libratbag's hidpp20.c, Solaar's settings_templates.py and
OpenRGB's LogitechProtocolCommon.h number them, and the HERO's answers to the reads are the
ones Solaar's issue 2313 logged off a G502 SE HERO.

The PRO WIRELESS's firmware stores a zone effect to flash unless fn 8 `01` came first, and
clears that byte at a power-state change (its image, read 2026-10-18). On such a model
(`claim_each_effect`) every effect write goes right after a claim; the HERO, cabled, is
claimed once, before its first write after a setup or a release.
"""

from __future__ import annotations

from collections.abc import Mapping

from .hidpp import Hidpp
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
# host's, 0 the device's (Solaar's "LED Control", OpenRGB's SET_SW_CTL). fn 4 writes the
# feature's non-volatile configuration and is never sent.
GET_INFO, GET_ZONE_INFO, GET_EFFECT_INFO, SET_EFFECT, SET_CONTROL = 0, 1, 2, 3, 8
HOST_CONTROL, DEVICE_CONTROL = 0x01, 0x00

# fn 3's last byte. libratbag sends 0x01 there and calls it "write to RAM and flash"; Solaar
# and OpenRGB send 0x00 on 0x8070, where OpenRGB sends 0x01 on 0x8071. The HERO answers
# persistency caps 0x00 for both zones, and its firmware image 127.03.10 never reads the
# byte (read 2026-10-17; the owner's HERO runs 169.00.9). On the owner's HERO a red sent with
# it, the claim held, did not outlive a replug (2026-10-18). The PRO WIRELESS's image
# 15.02.26 never reads it either: whether it stores an effect goes by fn 8.
RAM_ONLY = 0x00


class ZoneLighting:
    """0x8070's zones on one device, for the mice: what `Mice` asks of a mouse's lighting
    (the claim and its release, an effect written or sent, its parameters), with each zone a
    lighting.Cluster so the zones are named and resolved as 0x8071's clusters are."""

    def __init__(self, dev: Hidpp, *, claim_each_effect: bool) -> None:
        self.dev = dev
        self._index = dev.require(F_COLOR_LED, "COLOR_LED_EFFECTS").index
        #: Whether each effect write goes right after a claim (mouse.CLAIM_EACH_EFFECT).
        self.claim_each_effect = claim_each_effect
        self.clusters = self._read_zones()

    @classmethod
    def find(cls, dev: Hidpp, *, claim_each_effect: bool) -> ZoneLighting | None:
        """`dev`'s zones, or None when it has no 0x8070 or its 0x8070 lists no zone."""
        if dev.feature(F_COLOR_LED) is None:
            return None
        found = cls(dev, claim_each_effect=claim_each_effect)
        return found if found.clusters else None

    def _read_zones(self) -> tuple[Cluster, ...]:
        """Each zone, its location and its effects, CLUSTERS_MAX and EFFECTS_MAX at most, asked
        for one at a time. A reply too short to read raises LightingError (need_reply), which
        the attach's guard takes as a mouse it could not set up."""
        zones = []
        # Hidpp drops a report shorter than its id's length, so a reply has 3 bytes at least.
        count = self.dev.request(self._index, GET_INFO)[0]
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
        """fn 8 `01`: the host's effects shown in place of the onboard profile's. Solaar's LED
        Control says its zone effects show only with this set."""
        self.dev.request(self._index, SET_CONTROL, bytes([HOST_CONTROL]))

    def release(self, timeout: float = 1.0, retries: int = 2) -> None:
        """fn 8 `00`: the lighting back to the device, which shows its onboard profile's."""
        self.dev.request(self._index, SET_CONTROL, bytes([DEVICE_CONTROL]), timeout=timeout,
                         retries=retries)

    def set_effect(self, zone: int, effect: str | int, *, wait: bool = True,
                   **params: int) -> None:
        """Write one zone's effect, RAM_ONLY, waiting for its reply unless `wait` is False,
        after its claim on a model that claims each effect (_claim_for_effect). `zone` is its
        index, which is its place in `clusters`."""
        payload = self._payload(zone, effect, params)
        self._claim_for_effect(wait)
        self.dev.request(self._index, SET_EFFECT, payload, long=True, reply=wait)

    def send_effect(self, zone: int, effect: str | int, **params: int) -> tuple[int, int, int]:
        """One zone's effect sent without waiting, for a mouse that may be asleep, after its
        claim, unwaited too, on a model that claims each effect: the key of the effect's
        reply (Hidpp.answer). The claim is taken to land first, both going out on one link
        in order, and its answer is dropped unread: not yet checked on a mouse."""
        payload = self._payload(zone, effect, params)
        self._claim_for_effect(wait=False)
        return self.dev.send(self._index, SET_EFFECT, payload, long=True)

    def _claim_for_effect(self, wait: bool) -> None:
        """fn 8 `01` right before an effect write on a model that claims each effect
        (claim_each_effect), waited for unless `wait` is False; nothing on another. A waited
        claim refused or unanswered raises, and its effect is not sent; an unwaited one's
        answer is dropped unread, so its effect goes whatever the claim met."""
        if self.claim_each_effect:
            self.dev.request(self._index, SET_CONTROL, bytes([HOST_CONTROL]), reply=wait)

    def _payload(self, zone: int, effect: str | int, params: Mapping[str, int]) -> bytes:
        """fn 3's parameters, checked and laid out as 0x8071's are (lighting.effect_parameters):
        an effect the zone lacks (Cluster.effect), a parameter the effect lacks and a value it
        does not take each raise LightingError."""
        chosen = self.clusters[zone].effect(effect)
        return bytes([zone, chosen.index, *effect_parameters(chosen, params), RAM_ONLY])

    def effect_params(self, zone: int, effect: str | int, color: int) -> EffectParams:
        """The parameters a zone's effect takes, with 0x8071's defaults
        (lighting.default_params)."""
        return default_params(self.clusters[zone].effect(effect), color)


#: A mouse's lighting, as the daemon's mice drive it: 0x8071's clusters or 0x8070's zones.
type MouseLights = Lighting | ZoneLighting
