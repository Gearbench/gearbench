"""The lights of the G432's family's headsets that list 0x8070 COLOR_LED_EFFECTS: the G933,
G935, G633, G635 and G733 (G HUB's device list gives each two zones). Nothing here imports
the daemon.

`find_lights` looks 0x8070 up through the headset's channel (`G432.lookup`), which sends
nothing for a feature its table does not list, and reads its zones as `HeadsetZones`, a
mouse_lighting `ZoneLighting`, over `LightsChannel`, which sends only what `SENT` names:
0x8070's three reads, its host control (fn 8) and its zone effect (fn 3) with the
persistence byte RAM_ONLY, the startup effect's setting's read (fn 4) on a headset whose
fn 0 caps list it, and on a model tested on this hardware the two writes the headset keeps,
as G HUB sends them to the G933: a zone effect with the persistence byte STORED and the
startup effect's setting (fn 5). On a model not tested, each of those is refused
(g432.kept_refusal). The claim, `01 01` as G HUB sends it to the G933
(`HeadsetZones.CLAIM`), goes right before each effect, as on the PRO WIRELESS, whose
firmware stores a zone effect sent without it (mouse_lighting): with both, every firmware
read so far keeps the effect in RAM. The G933's fn 7, the control's read, answers invalid
function, and its fn 8 takes the claim (the Windows captures of 2026-10-25). A headset whose
fn 8 answers invalid function gives no such guard, so whether its effects persist is
unknown: its writes are refused (`NO_CONTROL`), the effect never sent.

Each color of an effect in RAM goes out `corrected`, as G HUB sends a G933's: sent as it is,
a dark red showed pink on a G933 and an orange yellow (the owner's eye, 2026-10-26). A
stored effect's color goes as it is, as G HUB stores it.
"""

from __future__ import annotations

from typing import cast

from .g432 import G432, kept_refusal
from .hidpp import (
    REQUEST_RETRIES,
    REQUEST_TIMEOUT,
    Feature,
    Hidpp,
    UnreadableAnswer,
)
from .mouse_lighting import (
    DEVICE_CONTROL,
    F_COLOR_LED,
    GET_EFFECT_INFO,
    GET_INFO,
    GET_NV_CONFIG,
    GET_ZONE_INFO,
    HOST_CONTROL,
    RAM_ONLY,
    SET_CONTROL,
    SET_EFFECT,
    SET_NV_CONFIG,
    STORED,
    ZoneLighting,
)

#: The 0x8070 functions LightsChannel sends; every other is refused before anything is
#: written.
SENT = frozenset({GET_INFO, GET_ZONE_INFO, GET_EFFECT_INFO, SET_CONTROL, SET_EFFECT,
                  GET_NV_CONFIG, SET_NV_CONFIG})
#: setZoneEffect's parameters: the zone, the effect's index, ten parameter bytes and the
#: persistence byte, which goes out RAM_ONLY, or STORED for an effect the headset keeps.
EFFECT_PARAMS = 13
#: The startup effect's setting of 0x8070's non-volatile configuration, fn 4's and fn 5's
#: first two bytes, its value on and off, as G HUB reads and writes a G933's (the Windows
#: captures of 2026-10-25), and fn 0's cap for it: the G933 lists caps 0x0001 and answers
#: setting 0x0001 (inferred: the setting's bit). The only setting LightsChannel reads or
#: writes.
STARTUP_SETTING = bytes([0x00, 0x01])
STARTUP_ON, STARTUP_OFF = 0x01, 0x02
STARTUP_CAP = 0x0001
STARTUP_WRITES = frozenset(STARTUP_SETTING + bytes([value]) for value in (STARTUP_ON, STARTUP_OFF))
#: The brightness cycle's steps a headset's G key runs (daemon/g432/lights.py), in percent of the
#: effect's own, the last off: G HUB's G1 on a G933 (the Windows captures of 2026-10-25,
#: H5c), as the owner's answer of 2026-10-25 gives them, "full, 50 %, 25 %, 12 %, off".
BRIGHTNESS_STEPS = (100, 50, 25, 12, 0)
FULL_BRIGHTNESS = BRIGHTNESS_STEPS[0]
#: Why a headset's lights are not written: its 0x8070 takes no host control, so the
#: effect would go with no claim; the model's name goes in.
NO_CONTROL = ("Gearbench does not write the {}'s lights until that is tested on this model: it "
              "may keep an effect it is sent")
#: The sRGB curve's power part (IEC 61966-2-1), from a channel's share of full, 0 to 1, to
#: its light: ((share + SRGB_OFFSET) / (1 + SRGB_OFFSET)) ** SRGB_EXPONENT. Times CHANNEL_MAX,
#: the fraction cut off, it gives each channel as G HUB sent a G933's in RAM: 00B4FF as
#: 00 74 ff, 00DBFF as 00 b4 ff and 00FFFF as itself, and 00DB77, its field's hex as the owner
#: read it, as 00 b4 2f (the Windows captures of 2026-10-25, partC-g933). A single power,
#: rounded or cut, gives the first three but not the last. The curve's linear part, below a
#: share of 0.04045, would change no byte: the power part cuts to 0 there too.
SRGB_OFFSET, SRGB_EXPONENT = 0.055, 2.4
#: A color channel's full value, a byte's.
CHANNEL_MAX = 0xFF
#: The parameters of an effect that hold a color (lighting.EFFECTS).
COLOR_PARAMS = frozenset({"color", "second_color"})


def corrected(color: int) -> int:
    """`color`, 0xRRGGBB, each channel the light the sRGB curve gives it (SRGB_OFFSET), as G
    HUB sends a G933 each color it shows in RAM."""
    return sum(_light(color >> shift & CHANNEL_MAX) << shift for shift in (16, 8, 0))


def _light(channel: int) -> int:
    """A channel's value, 0 to CHANNEL_MAX, through the sRGB curve, its fraction cut off."""
    share = channel / CHANNEL_MAX
    return int(((share + SRGB_OFFSET) / (1 + SRGB_OFFSET)) ** SRGB_EXPONENT * CHANNEL_MAX)


class HeadsetZones(ZoneLighting):
    """A headset's 0x8070 zones, claimed as G HUB claims the G933's, the colors of an effect
    in RAM corrected as it corrects them, and its startup effect's setting."""

    #: fn 8's claim as G HUB sends it to the G933 at each connect, which answers with its
    #: echo: the host's control and a second byte whose meaning is not known (the Windows
    #: captures of 2026-10-25).
    CLAIM = bytes([HOST_CONTROL, 0x01])

    def set_effect(self, zone: int, effect: str | int, *, wait: bool = True,
                   **params: int) -> None:
        """ZoneLighting.set_effect, an effect in RAM, each of its colors `corrected`."""
        super().set_effect(zone, effect, wait=wait, **{
            key: corrected(value) if key in COLOR_PARAMS else value
            for key, value in params.items()})

    def store_effect(self, zone: int, effect: str, **params: int) -> None:
        """One zone's effect written for the headset to keep (STORED), right after its claim,
        waited for, as G HUB's "effects on device" writes a G933's, its colors as given, not
        `corrected`: what the lights show without Gearbench, after a power-off too.
        ValueError, with nothing sent, the claim included, on a model not tested on this
        hardware (LightsChannel.kept_refused). Raises HidppFailure, LightingError."""
        if (refused := cast("LightsChannel", self.dev).kept_refused()) is not None:
            raise ValueError(refused)
        payload = self._payload(zone, effect, params, STORED)
        self._claim_for_effect()
        self.dev.request(self._index, SET_EFFECT, payload, long=True)

    def startup(self) -> bool | None:
        """Whether the headset shows its startup effect as it switches on, fn 4's read of
        the setting; None, with nothing sent, where fn 0's caps list no such setting.
        UnreadableAnswer for a value but on or off. Raises HidppFailure."""
        if not self.nv_caps & STARTUP_CAP:
            return None
        value = self.dev.request(self._index, GET_NV_CONFIG, STARTUP_SETTING)[0]
        if value not in (STARTUP_ON, STARTUP_OFF):
            raise UnreadableAnswer(f"the startup effect's setting read 0x{value:02x}, neither "
                                   f"on (0x{STARTUP_ON:02x}) nor off (0x{STARTUP_OFF:02x})")
        return value == STARTUP_ON

    def set_startup(self, on: bool) -> bool | None:
        """Write the startup effect's setting (fn 5), then read it back (startup): what
        the headset now gives. ValueError, with nothing sent, on a model not tested on this
        hardware (LightsChannel). Raises HidppFailure."""
        value = STARTUP_ON if on else STARTUP_OFF
        self.dev.request(self._index, SET_NV_CONFIG, STARTUP_SETTING + bytes([value]))
        return self.startup()


class LightsChannel:
    """What ZoneLighting asks of a Hidpp on the paths a headset runs, `feature`, `require` and
    `request`, for one headset's 0x8070 at the index the root lookup gave, the requests in SENT
    alone: each waits for its reply. It has no `send` (ZoneLighting.send_effect, which no
    headset path calls), so a call of a Hidpp method it lacks fails with AttributeError
    before a byte is written."""

    def __init__(self, headset: G432, feature: Feature) -> None:
        self._headset = headset
        self._feature = feature

    def feature(self, feature_id: int) -> Feature | None:
        return self._feature if feature_id == F_COLOR_LED else None

    def require(self, feature_id: int, name: str) -> Feature:
        found = self.feature(feature_id)
        if found is None:
            raise ValueError(f"the {self._headset.model.short}'s lights are 0x8070 alone, "
                             f"not {name}")
        return found

    def kept_refused(self) -> str | None:
        """Why a write the headset keeps is refused, on a model not tested on this hardware
        (kept_refusal); None on a tested one."""
        return kept_refusal(self._headset.model)

    def request(self, feature_index: int, function: int, params: bytes = b"", *,
                timeout: float = REQUEST_TIMEOUT, long: bool = False,
                retries: int = REQUEST_RETRIES, reply: bool = True) -> bytes:
        """One request of SENT to 0x8070's index, waited for; ValueError, before anything is
        written, for any other request or for parameters the checks below refuse, a write the
        headset keeps on a model not tested (kept_refused) among them. Raises HidppFailure."""
        short = self._headset.model.short
        if feature_index != self._feature.index or function not in SENT or not reply:
            raise ValueError(f"0x8070 function {function} at index 0x{feature_index:02x} is "
                             f"not a request Gearbench sends the {short}'s lights")
        if function == SET_EFFECT and (len(params) != EFFECT_PARAMS
                                       or params[-1] not in (RAM_ONLY, STORED)):
            raise ValueError(f"the {short}'s lights are sent an effect with persistence 0, "
                             f"RAM, or 2, kept, alone")
        if function == SET_CONTROL and params not in (HeadsetZones.CLAIM,
                                                      bytes([DEVICE_CONTROL])):
            raise ValueError(f"the {short}'s lights take host or device control alone")
        if function == SET_NV_CONFIG and params not in STARTUP_WRITES:
            raise ValueError(f"the {short}'s lights are sent their startup effect's setting, "
                             f"on or off, alone")
        if function == GET_NV_CONFIG and params != STARTUP_SETTING:
            raise ValueError(f"the {short}'s lights are asked their startup effect's setting "
                             f"alone")
        kept = function == SET_NV_CONFIG or (function == SET_EFFECT and params[-1] == STORED)
        if kept and (refused := self.kept_refused()) is not None:
            raise ValueError(refused)
        return self._headset.dev.request(feature_index, function, params, timeout=timeout,
                                         long=long, retries=retries)


def find_lights(headset: G432) -> HeadsetZones | None:
    """The headset's zones, read through LightsChannel, or None for a table that lists no
    0x8070, a lookup that finds none, or zones none of. Raises HidppFailure, an
    UnreadableAnswer for a reply too short to read."""
    found = headset.lookup(F_COLOR_LED)
    if found is None:
        return None
    channel = LightsChannel(headset, found)
    return HeadsetZones.find(cast("Hidpp", channel), claim_each_effect=True)
