"""Lighting for the keyboards and the G502 X PLUS: 0x8071 zone effects, 0x8081 per-key,
0x8040 brightness.

The rules here come from the device and from a capture of G HUB's USB traffic; where the two
disagree, this follows the device.

On the G915 a static zone effect alone leaves the keys dark; the per-key fill on top is what
shows (measured in HOST mode, 2026-09-22). A drag preview is one per-key frame, about 6 ms.
A paint larger than one frame is split across frames, which does not show because a frame
commits only what it carries.

The G502 X PLUS keeps its per-key effects on cluster 0xff, which the counted cluster walk never
reaches; a caller asks for it with `per_key_cluster`. The keyboard is never probed for it.

Nothing here writes a non-volatile persistence flag or touches 0x8100 ONBOARD_PROFILES.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import batched, groupby
from typing import ClassVar, TypedDict, cast

from .hidpp import Feature, Hidpp, HidppError, HidppFailure, HidppTimeout
from .wording import counted

log = logging.getLogger("gearbench")

F_RGB = 0x8071
F_PERKEY = 0x8081
F_BRIGHTNESS = 0x8040

# 0x8071 function 5 setSWControl: [getOrSet, control, notify], getOrSet 0 to read and 1 to
# write. The firmware stores control and notify exactly as sent (its code at 0x2a6bc-0x2a6f0,
# read 2026-09-23); link setup zeroes both. Control bit 0: the host owns the effects (fn 1
# answers error 5 without it). Bit 1: the host owns the power modes, which fn 8 needs; while it
# is set the firmware skips its own power saving and sleep. Notify bit 0 asks for the
# effect-sync report (event address 0x00), bit 1 for the wake notice and bit 2 for the idle
# notice (address 0x10, data 01 and 00).
SW_GET, SW_SET = 0x00, 0x01
SW_HOST = 0x03
#: The control byte a release leaves: effects and power modes back to the firmware, as G HUB
#: leaves them (`01 00 06`, the owner's decision of 2026-09-23).
SW_FIRMWARE = 0x00
NOTIFY_SYNC, NOTIFY_WAKE, NOTIFY_IDLE = 0x01, 0x02, 0x04
# G HUB's claim: two writes of the notify byte, in this order. 07 asks for all three, and a
# wake notice follows it; 05 leaves the wake notice off until an idle notice is answered.
# The first alone does not settle, yet a read returns 03 05 either way: no proof of a claim.
SW_CLAIM_SEQUENCE = (0x07, 0x05)
#: The notify byte a release leaves: wake and idle, as in the 00 00 06 G HUB reads at its
#: start, an earlier session's release.
SW_RELEASE = NOTIFY_WAKE | NOTIFY_IDLE
#: Answers to the idle and wake notices, as G HUB sends them: each turns its own notice off
#: and the other on.
IDLE_ANSWER = NOTIFY_SYNC | NOTIFY_WAKE
WAKE_ANSWER = NOTIFY_SYNC | NOTIFY_IDLE
#: What fn 5's (control, notify) pair says when read back. Link setup zeroes both.
SW_STATES = {
    (SW_HOST, WAKE_ANSWER): "held",
    (SW_HOST, IDLE_ANSWER): "held and idle",
    (SW_FIRMWARE, SW_RELEASE): "let go",
    (0x00, 0x00): "not held since it linked",
}

# 0x8071 function 7 sets the idle and sleep times, [01, 00, 00, idle BE16, sleep BE16];
# function 8 sets the power state, [01, state], 1 active, 2 power saving, 3 sleep, and needs
# control bit 1. The times count in units of 1000/1024 s: G HUB writes 60, and the idle notice
# came 58.60 s later. Link setup reloads both times from the onboard profile, so a keyboard
# switched off or moved to another route has them written again.
POWER_TIMES, POWER_STATE = 7, 8
POWER_ACTIVE = 0x01
TIME_UNIT = 1000 / 1024
TIME_UNITS_MAX = 0xFFFF

# Trailing byte of setEffectByIndex: persistence in bits 0-1, power-mode
# target in bits 2-3. VOLATILE only, full-power target. Never 0x02.
PERSIST_VOLATILE = 0x01

# The byte after the color in a static write, 0x02 as G HUB sends it. With 0x00 the keys stayed
# dark on a profile apply, and the logo re-ramped and flickered under a preview stream.
STATIC_RAMP = 0x02

PERKEY_SET_INDIVIDUAL = 1   # up to 4 x (zone, r, g, b)
PERKEY_SET_RANGE = 5        # up to 3 x (first, last, r, g, b)
PERKEY_SET_MULTI = 6        # (r, g, b) + up to 13 zone ids
PERKEY_FRAME_END = 7

MIN_ZONE, MAX_ZONE = 1, 254

# Zone-write long reports a frame stages; one more gets no reply and is silently dropped.
MAX_REPORTS_PER_FRAME = 27

# How long a per-key frame waits after the last FrameEnd before its first zone write: one
# started about 1 ms after is applied in part.
FRAME_SETTLE = 0.02
# A contiguous run costs a third of a range report or one thirteenth of a
# list report per zone, so a range only pays off from five zones up.
MIN_RANGE_RUN = 5

# The first frame after a claim sometimes landed before the firmware had left onboard mode,
# and painted the logo only. So the claim settles first, and each frame is sent once.
SETTLE_AFTER_CLAIM = 0.15
# The G915 and the G502 X PLUS NAK an effect index past the last by silence, and the probe's
# walk always asks for one: the default 1 s with two retries would hold the daemon's only
# thread 3 s at a mouse reconnect.
PROBE_TIMEOUT = 0.1
#: The most clusters (0x8071) or zones (0x8070), and effects in one, a walk reads, whatever
#: a count byte says, as hidpp.ENTITIES_MAX for the firmware entities: a byte the device
#: gets wrong cannot set how many reads follow.
CLUSTERS_MAX, EFFECTS_MAX = 16, 32

# The FrameEnd after a failed per-key write; an answering keyboard replies in milliseconds.
COMMIT_TIMEOUT = 0.25

#: Effect id -> (name, {parameter: byte offsets}). Offsets index the 10 parameter bytes after
#: the cluster and effect-index bytes of a setEffectByIndex payload, most significant byte
#: first. They are per byte because COLORWAVE splits its period: LSB at [6], MSB at [9]. The
#: G915's and the G502 X PLUS's layouts are G HUB's, checked field by field against a capture
#: of its writes. A name can have more than one id, and a cluster runs it by the one it
#: reports (Cluster.effect). An unknown id is still settable by its id, with every parameter
#: byte zero, but no front end offers one (offered_effects).
EFFECTS: dict[int, tuple[str, dict[str, tuple[int, ...]]]] = {
    0x00: ("disabled", {}),
    0x01: ("static", {"color": (0, 1, 2), "ramp": (3,)}),
    0x03: ("cycle", {"period": (5, 6), "intensity": (7,)}),
    0x04: ("wave", {"period": (9, 6), "direction": (7,), "intensity": (8,)}),
    0x0A: ("breathe", {"color": (0, 1, 2), "period": (3, 4), "form": (5,), "intensity": (6,)}),
    0x0B: ("ripple", {"color": (0, 1, 2), "period": (4, 5)}),
    # The per-key mode, and the G502 X PLUS's two stored signature effects, from its onboard
    # lighting records: frame rate at [8] and brightness at [9] of the whole payload. The
    # mouse's records carry 0x64 there; sent zero, the effects run at brightness 0.
    0x0C: ("custom", {}),
    0x0F: ("snipe-pulse", {"rate": (6,), "intensity": (7,)}),
    0x10: ("neural-wave", {"rate": (6,), "intensity": (7,)}),
    # COLOR_CYCLE_S, COLORWAVE_S and RIPPLE_S, the G515 TKL's cycle, wave and ripple, from G
    # HUB's encoders, read in its agent, and the x8071 v4 spec's table, which agree; no capture
    # of them yet. Each has a saturation byte: at 0, where the classic cycle's layout leaves
    # it, the board showed near-white (2026-10-17). Ripple's [4] is its startup-animation flag,
    # left 0 as G HUB sends it for the onboard profile.
    0x15: ("cycle", {"saturation": (1,), "period": (6, 7), "intensity": (8,)}),
    0x16: ("wave", {"saturation": (1,), "period": (6, 7), "intensity": (8,),
                    "direction": (9,)}),
    0x17: ("ripple", {"color": (0, 1, 2), "saturation": (3,), "period": (6, 7)}),
}

#: Effect name -> the ids that run it, in EFFECTS' order.
EFFECT_IDS: dict[str, tuple[int, ...]] = {
    name: tuple(eid for eid, (named, _) in EFFECTS.items() if named == name)
    for name, _ in EFFECTS.values()
}

#: (effect id, parameter) -> the largest value it takes, where that is less than its bytes
#: hold: COLORWAVE_S's directions, 0 for its default to 8 center-in (the x8071 v4 spec).
PARAM_LIMITS = {(0x16, "direction"): 8}

#: The saturation an effect with one runs at, G HUB's default.
FULL_SATURATION = 0xFF

#: The intensity an effect that has one runs at.
FULL_INTENSITY = 100

#: The parameter bytes between an effect write's cluster and effect index and its persistence
#: byte.
PARAMETER_BYTES = 10


class EffectParams(TypedDict, total=False):
    """An effect's parameters by name, as default_params answers them and set_effect
    takes them by keyword. The names are those of EFFECTS' layouts."""

    color: int
    ramp: int
    period: int
    intensity: int
    direction: int
    form: int
    rate: int
    saturation: int


#: The rate byte of the two signature effects: G HUB's one captured write of either carries
#: 0x20 (neural-wave), and its device database marks both fixedRate. The mouse's records hold 0.
SIGNATURE_RATE = 0x20

#: Default periods in ms, by the name EFFECTS gives the effect's id, whichever of a name's ids
#: runs it and whatever a model calls it: cycle and wave as G HUB stores them in the G915's
#: onboard M1 profile, the others as G HUB was seen sending them. fn 0's effect info has no
#: period to read.
DEFAULT_PERIODS = {"cycle": 2100, "wave": 2100, "breathe": 3000, "ripple": 1000}

#: The animations a profile can show in place of Static or Per-key, in the
#: order the window lists them. Each has a speed; wave has a direction.
ANIMATIONS = ("breathe", "cycle", "wave", "ripple")

#: Each animation's period range in ms, (fastest, slowest): G HUB's speed slider runs 1000 to
#: 20000. Ripple runs 100 to 1000, the owner's choice of 2026-09-24 after watching it on the G915.
SPEEDS = {"breathe": (1000, 20000), "cycle": (1000, 20000), "wave": (1000, 20000),
          "ripple": (100, 1000)}

#: COLORWAVE's direction byte, by G HUB's window labels, as its protobuf enum numbers them and
#: its capture sent 1, 2, 3 and 6; COLORWAVE_S numbers them the same (the x8071 v4 spec). The
#: enum's 4 INWARD and 5 OUTWARD are left out, as no model takes them: G HUB's window offers
#: them on no device, the G915's firmware image refuses both, and on a wired G515 TKL, where
#: the six ran as named, each turned the whole board dark, no wave (the owner, 2026-10-18).
#: Which ones a keyboard takes is its model's (keyboard.KeyboardModel.wave_directions). On the
#: G915, watched by the owner on 2026-09-24: horizontal runs left to right, reverse-horizontal
#: right to left, vertical top to bottom, center-out from the middle; and on 2026-10-18:
#: reverse-vertical bottom to top, center-in from the edges in.
WAVE_DIRECTIONS = {
    "horizontal": 1,
    "vertical": 2,
    "center-out": 3,
    "reverse-horizontal": 6,
    "reverse-vertical": 7,
    "center-in": 8,
}


def effect_layout(effect: int) -> dict[str, tuple[int, ...]]:
    """An effect id's parameters and their byte offsets, from EFFECTS; an id it lacks takes
    none."""
    return EFFECTS.get(effect, ("", {}))[1]


def param_max(effect: int, key: str) -> int:
    """The largest value parameter `key` of effect id `effect` takes: its PARAM_LIMITS, or
    what its bytes in effect_layout hold."""
    return PARAM_LIMITS.get((effect, key), (1 << 8 * len(effect_layout(effect)[key])) - 1)


def fits(effect: int, key: str, value: int) -> bool:
    """Whether `value` is one the effect's `key` parameter takes (param_max)."""
    return 0 <= value <= param_max(effect, key)


#: Effects whose color the firmware renders as a flat wash, by name. Per-key paint is only
#: visible on top of one of these; an animating effect overwrites it.
STATIC_EFFECTS = frozenset({"disabled", "static"})

#: The zone effects a keyboard can be offered, by name, for front ends: EFFECTS also holds the
#: mouse's signature effects and `custom`, the per-key mode. A keyboard is offered those its
#: clusters report (offered_effects), and a cluster without one falls back to static.
KEYBOARD_EFFECTS = ("static", "breathe", "cycle", "wave", "ripple", "disabled")


def has_color(name: str, names: Mapping[int, str] | None = None) -> bool:
    """Whether the effect `name` takes a color: the EFFECTS layout of one of its ids has one.
    Its ids are those a device model calls `name` in `names` (Lighting's), else EFFECT_IDS',
    which must have it."""
    ids = [eid for eid, own in (names or {}).items() if own == name] or EFFECT_IDS[name]
    return any("color" in effect_layout(eid) for eid in ids)


#: The animations with no color of their own: no EFFECTS layout of theirs has one.
COLORLESS = tuple(name for name in ANIMATIONS if not has_color(name))

#: The lighting level every repaint goes through. Dim is half of every color, or of an effect's
#: intensity where it has none, and the saved brightness is untouched; dark is every cluster
#: Disabled with the claim held. The lock and the keyboard's idleness each set one, and the
#: keyboard shows the darker; a synced mouse counts the idleness only once the whole desktop is
#: idle (Keyboard.at_level).
LIT, DIM, DARK = "lit", "dim", "dark"
LEVEL_WORDS = {LIT: "on", DIM: "dimmed", DARK: "off"}


class LightingError(RuntimeError):
    pass


#: The refusal of a brightness read or write on a device without 0x8040.
NO_BRIGHTNESS = "this device has no brightness control"


def need_reply(reply: bytes, length: int, what: str) -> bytes:
    """The reply, if long enough to decode; a short one raises LightingError.

    Decoding it would read a wrong value, or raise IndexError, which the CLI does not catch.
    """
    if len(reply) < length:
        raise LightingError(f"{what} reply is {len(reply)} bytes, needs {length}: {reply.hex(' ')}")
    return reply


@dataclass(frozen=True)
class Effect:
    cluster: int
    index: int
    id: int
    #: The device model's own name for this id, in place of EFFECTS' (Lighting's `names`), or
    #: None for EFFECTS' name.
    own_name: str | None = None

    @property
    def name(self) -> str:
        if self.own_name is not None:
            return self.own_name
        known = EFFECTS.get(self.id)
        return known[0] if known else f"effect-0x{self.id:04x}"

    def runs(self, which: str | int) -> bool:
        """Whether `which` is this effect: its id, its own name where it has one, else a name
        whose ids in EFFECT_IDS hold its id. An id with a name of its own answers to no other
        name: EFFECTS' for it is another model's."""
        if not isinstance(which, str):
            return which == self.id
        if self.own_name is not None:
            return which == self.own_name
        return self.id in EFFECT_IDS.get(which, ())


@dataclass(frozen=True)
class Cluster:
    index: int
    location: int
    effects: tuple[Effect, ...]

    #: 0x8071 location codes. Only the two this keyboard reports are named.
    LOCATIONS: ClassVar[dict[int, str]] = {0x01: "primary", 0x02: "logo"}
    #: The mouse's per-key cluster has no location of its own; getInfo with
    #: [0xff, 0xff] describes the device, so it is only ever found by probing
    #: its effects.
    PERKEY_INDEX = 0xFF

    @property
    def name(self) -> str:
        if self.index == self.PERKEY_INDEX:
            return "per-key"
        return self.LOCATIONS.get(self.location, f"cluster-{self.index}")

    def has_effect(self, which: str | int) -> bool:
        return any(e.runs(which) for e in self.effects)

    def effect(self, which: str | int) -> Effect:
        """Resolve an effect name or id to this cluster's own entry: for a name, the first of
        the cluster's effects that runs it (Effect.runs), so cycle is 0x03 on the G915 and
        0x15 on the G515 TKL.

        Indices differ between clusters (cluster 0 has Cycle before Breathe, cluster 1 after),
        so never cache one across clusters.
        """
        for e in self.effects:
            if e.runs(which):
                return e
        has = ", ".join(e.name for e in self.effects)
        if isinstance(which, str) and which not in EFFECT_IDS:
            raise LightingError(f"unknown effect {which!r}; this cluster has {has}")
        raise LightingError(
            f"cluster {self.index} ({self.name}) has no effect {which!r}; it has {has}")


def offered_effects(clusters: tuple[Cluster, ...]) -> tuple[str, ...]:
    """The KEYBOARD_EFFECTS one of `clusters` reports, in KEYBOARD_EFFECTS' order: what a
    keyboard is offered. An id this build has no name for is never one of them."""
    return tuple(name for name in KEYBOARD_EFFECTS if any(c.has_effect(name) for c in clusters))


def default_params(chosen: Effect, color: int,
                   overrides: Mapping[str, int] | None = None) -> EffectParams:
    """The parameters effect `chosen` takes, with defaults, and `overrides` (a profile's saved
    settings, config.effect_settings) on top, for 0x8071's clusters and 0x8070's zones alike.

    Passing cycle a color is an error, so a caller handing every effect the same kwargs
    falls back instead of animating. Wave runs horizontal by default, the direction in the
    G915's onboard M1 record, and an effect with a saturation at FULL_SATURATION.
    """
    takes = effect_layout(chosen.id)
    given = {"color": color, "saturation": FULL_SATURATION,
             **({"period": DEFAULT_PERIODS[EFFECTS[chosen.id][0]]} if "period" in takes else {}),
             "intensity": FULL_INTENSITY, "direction": WAVE_DIRECTIONS["horizontal"],
             "rate": SIGNATURE_RATE, **(overrides or {})}
    # Every key is a name from the effect's layout in EFFECTS, which EffectParams lists.
    return cast("EffectParams", {key: value for key, value in given.items() if key in takes})


def effect_parameters(chosen: Effect, params: Mapping[str, int]) -> bytes:
    """The ten parameter bytes of a write of effect `chosen`: each of `params` at its EFFECTS
    offsets, most significant byte first, zeros elsewhere, and STATIC_RAMP for a static that
    `params` gives no ramp. A parameter the effect lacks, or a value it does not take (fits),
    raises LightingError."""
    takes = effect_layout(chosen.id)
    if chosen.id == 0x01 and "ramp" not in params:
        params = {**params, "ramp": STATIC_RAMP}
    body = bytearray(PARAMETER_BYTES)
    for key, value in params.items():
        if key not in takes:
            raise LightingError(
                f"{chosen.name} has no parameter {key!r}: it takes "
                f"{', '.join(sorted(takes)) or 'none'}"
            )
        if not fits(chosen.id, key, value):
            raise LightingError(
                f"{chosen.name} {key} {value} outside 0..{param_max(chosen.id, key)}")
        offsets = takes[key]
        for offset, byte in zip(offsets, value.to_bytes(len(offsets), "big"), strict=True):
            body[offset] = byte
    return bytes(body)


def _rgb(color: int) -> bytes:
    return (color & 0xFFFFFF).to_bytes(3, "big")


def snap_brightness(levels: tuple[int, ...], level: int) -> int:
    """`level` floored to one of `levels`, as the device floors it; `level` when none is known."""
    return max((m for m in levels if m <= level), default=level)


class Lighting:
    def __init__(self, dev: Hidpp, per_key_cluster: bool = False,
                 names: Mapping[int, str] | None = None) -> None:
        self.dev = dev
        #: The device model's own name for an effect id, where it has one (Effect.own_name):
        #: mouse.EFFECT_NAMES' for a mouse.
        self._names = dict(names or {})
        self._rgb = dev.require(F_RGB, "RGB_EFFECTS")
        # Optional: only per-key painting needs 0x8081, and zonal keyboards do not have it.
        self._perkey = dev.feature(F_PERKEY)
        # Optional: the G502 X PLUS has no 0x8040.
        self._brightness = dev.feature(F_BRIGHTNESS)
        #: Whether to read cluster 0xff, outside the counted walk: the G502 X PLUS's per-key one.
        self._per_key_cluster = per_key_cluster
        self.clusters: tuple[Cluster, ...] = ()
        self._claimed = False
        #: When the last per-key FrameEnd went (_settle_frame).
        self._frame_end_at = float("-inf")
        self._marks: tuple[int, ...] = ()
        self.discover()

    @classmethod
    def find(cls, dev: Hidpp, per_key_cluster: bool = False,
             names: Mapping[int, str] | None = None) -> Lighting | None:
        """`dev`'s lighting, or None when it has no 0x8071 or its 0x8071 lists no cluster:
        nothing to claim or paint. `names` are its model's own effect names (__init__)."""
        if dev.feature(F_RGB) is None:
            return None
        found = cls(dev, per_key_cluster, names)
        return found if found.clusters else None

    # -- discovery -------------------------------------------------------

    def discover(self) -> None:
        """Read the cluster and effect tables, and the brightness levels."""
        info = self.dev.request(self._rgb.index, 0, bytes([0xFF, 0xFF, 0x00]))
        # Hidpp drops a report shorter than its id's length, so a reply has 3 bytes at least.
        count = info[2]
        # Byte 7 counts the multi-cluster effects, the ones on cluster 0xff
        # (OpenLogi's x8071 page): 5 on the G502 X PLUS, 0 on the G915.
        multi = info[7] if len(info) > 7 else 0
        clusters = []
        for c in range(min(count, CLUSTERS_MAX)):
            reply = self.dev.request(self._rgb.index, 0, bytes([c, 0xFF, 0x00]))
            need_reply(reply, 5, f"0x8071 fn 0 cluster {c} info")
            clusters.append(Cluster(c, int.from_bytes(reply[2:4], "big"),
                                    self._read_effects(c, reply[4])))
        if self._per_key_cluster:
            probed = self._probe_per_key(multi)
            if probed is not None:
                clusters.append(probed)
        self.clusters = tuple(clusters)

        if self._brightness is None:
            return
        binfo = self.dev.request(self._brightness.index, 0)
        maximum = int.from_bytes(binfo[0:2], "big")
        steps = binfo[2] & 0x0F
        # The levels this firmware keeps: max=100, steps=5 gives (0, 12, 25, 50, 100).
        self._marks = tuple(sorted({maximum >> i for i in range(max(steps - 1, 1))} | {0}))

    def _probe_per_key(self, count: int) -> Cluster | None:
        """Cluster 0xff, which the counted walk cannot reach, read effect by effect.

        With `count`, byte 7 of the device query, exactly that many are asked for. That rests
        on one device, the G502 X PLUS, so with 0 they are asked for one at a time, up to 16,
        each once with PROBE_TIMEOUT, until an error, silence or an echo that is not this
        effect. DeviceGone propagates: it is not a NAK.
        """
        index = Cluster.PERKEY_INDEX
        if count:
            return Cluster(index, 0x0000, self._read_effects(index, count))
        effects = []
        for e in range(16):
            try:
                r = self.dev.request(self._rgb.index, 0, bytes([index, e, 0x00]),
                                     timeout=PROBE_TIMEOUT, retries=0)
            except (HidppError, HidppTimeout):
                break
            probed = self._effect(r, f"cluster {index} effect {e}")
            if probed.cluster != index or probed.index != e:
                break
            effects.append(probed)
        if not effects:
            return None
        return Cluster(index, 0x0000, tuple(effects))

    def _read_effects(self, cluster: int, count: int) -> tuple[Effect, ...]:
        """Effects 0 to count - 1 of a cluster, EFFECTS_MAX at most, asked for one at a time."""
        return tuple(self._effect(self.dev.request(self._rgb.index, 0, bytes([cluster, e, 0x00])),
                                  f"cluster {cluster} effect {e}")
                     for e in range(min(count, EFFECTS_MAX)))

    def _effect(self, reply: bytes, what: str) -> Effect:
        """One effect-info reply: [cluster, index, id uint16 BE, caps uint32 BE], named by the
        model where it names the id."""
        need_reply(reply, 8, what)
        effect_id = int.from_bytes(reply[2:4], "big")
        return Effect(cluster=reply[0], index=reply[1], id=effect_id,
                      own_name=self._names.get(effect_id))

    def cluster(self, which: str | int) -> Cluster:
        for c in self.clusters:
            if c.index == which or c.name == which:
                return c
        raise LightingError(
            f"no cluster {which!r}; this device has "
            f"{', '.join(f'{c.index}={c.name}' for c in self.clusters)}"
        )

    # -- ownership -------------------------------------------------------

    @property
    def claimed(self) -> bool:
        return self._claimed

    @property
    def rgb_index(self) -> int:
        """0x8071's feature index: the idle and wake notices and the effect-sync report come
        from it."""
        return self._rgb.index

    def sw_control(self, timeout: float = 1.0, retries: int = 2) -> int:
        """fn 5's notify byte: 0x05 while claimed, 0x03 while an idle notice is answered, 0x06
        after a release."""
        return self.sw_state(timeout, retries)[1]

    def sw_state(self, timeout: float = 1.0, retries: int = 2) -> tuple[int, int]:
        """fn 5's control and notify bytes: (0x03, 0x05) while claimed, (0x00,
        0x06) after the keyboard's release, and both zero after link setup."""
        reply = self.dev.request(self._rgb.index, 5, bytes([SW_GET]),
                                 timeout=timeout, retries=retries)
        return reply[1], reply[2]

    def claim(self) -> None:
        """Take software control of the lighting, the way G HUB does.

        A single write does not settle, and a zero control byte releases; either shows as a
        paint the firmware effect takes back on the first keypress.
        """
        for value in SW_CLAIM_SEQUENCE:
            self.dev.request(self._rgb.index, 5, bytes([SW_SET, SW_HOST, value]))
        self._claimed = True
        time.sleep(SETTLE_AFTER_CLAIM)

    def release(self, timeout: float = 1.0, retries: int = 2) -> None:
        """Hand the LEDs back and let the firmware repaint from its onboard profile.

        Required on exit: per-key paint has no persistence flag, but nothing reclaims a cluster
        that is not animating, so the keyboard would keep showing the last paint.

        The keyboard and the mice with 0x8071 alike are left `01 00 06` (SW_FIRMWARE), which
        also gives back power saving and sleep; a mouse with 0x8070's zones is released by
        ZoneLighting.release. With `01 03 06` the keyboard never slept, and the G502 X
        PLUS would get its lighting back only through a 0x0020 cookie clear, which a stop does
        not send (daemon.mice.MOUSE_COOKIE).
        """
        self.dev.request(self._rgb.index, 5, bytes([SW_SET, SW_FIRMWARE, SW_RELEASE]),
                         timeout=timeout, retries=retries)
        self._claimed = False

    # -- idle and wake ---------------------------------------------------

    def set_power_times(self, idle: float, sleep: float) -> None:
        """fn 7: the idle time before the idle notice and the sleep time after it, in seconds,
        written rounded to TIME_UNIT.

        The firmware sleeps by itself only while it owns the power modes; claimed, the sleep
        time is the host's to act on. A write counts as activity: the idle time restarts, and
        a keyboard whose idle notice was answered sends its wake notice.
        """
        units = tuple(min(TIME_UNITS_MAX, max(0, round(seconds / TIME_UNIT)))
                      for seconds in (idle, sleep))
        self.dev.request(self._rgb.index, POWER_TIMES,
                         bytes([SW_SET, 0x00, 0x00]) + units[0].to_bytes(2, "big")
                         + units[1].to_bytes(2, "big"))

    def set_active(self) -> None:
        """fn 8 `01 01`: the active power state. The firmware never goes back
        to it by itself while the host holds the power modes."""
        self.dev.request(self._rgb.index, POWER_STATE, bytes([SW_SET, POWER_ACTIVE]))

    def answer_idle(self) -> None:
        """The idle notice's answer, fn 5 `01 03 03`: the wake notice on and
        the idle one off. Without it the notice comes eight times over 3.97 s."""
        self.dev.request(self._rgb.index, 5, bytes([SW_SET, SW_HOST, IDLE_ANSWER]))

    def answer_wake(self) -> None:
        """The wake notice's answer, fn 8 `01 01` then fn 5 `01 03 05`: active,
        and the idle notice on again with the wake one off."""
        self.set_active()
        self.dev.request(self._rgb.index, 5, bytes([SW_SET, SW_HOST, WAKE_ANSWER]))

    # -- zone effects ----------------------------------------------------

    @property
    def per_key(self) -> bool:
        """Whether this device can be painted key by key."""
        return self._perkey is not None

    def _require_per_key(self) -> Feature:
        """0x8081, or LightingError on a device without it."""
        if self._perkey is None:
            raise LightingError(
                "this device has zone lighting but no per-key lighting "
                "(0x8081), so individual keys cannot be painted")
        return self._perkey

    def set_effect(self, cluster: str | int, effect: str | int, *, wait: bool = True,
                   **params: int) -> None:
        """Write one zone effect, waiting for its reply unless `wait` is False. A BUSY is
        retried by request(), and an error reply raised.

        fn 1 queues into one pending record with no busy check (the firmware's code, read
        2026-09-23), so a write sent before the last is answered can replace it: on the G915
        the zone layer then kept its old color (2026-09-22). The G502 X PLUS replies too and
        is waited for, except for a drag's preview and a mouse that may be asleep (send_effect).
        """
        self.dev.request(
            self._rgb.index, 1, self._effect_payload(cluster, effect, params),
            long=True, reply=wait,
        )

    def send_effect(self, cluster: str | int, effect: str | int,
                    **params: int) -> tuple[int, int, int]:
        """One zone effect sent without waiting: the key of its reply (Hidpp.answer), as
        send_fill, for a mouse that may be asleep."""
        return self.dev.send(self._rgb.index, 1, self._effect_payload(cluster, effect, params),
                             long=True)

    def _effect_payload(self, cluster: str | int, effect: str | int,
                        params: Mapping[str, int]) -> bytes:
        """fn 1's parameters: the cluster, the effect and its encoded parameters
        (effect_parameters)."""
        target = self.cluster(cluster)
        chosen = target.effect(effect)
        return bytes([target.index, chosen.index, *effect_parameters(chosen, params),
                      PERSIST_VOLATILE])

    def effect_params(
        self, cluster: str | int, effect: str | int, color: int, overrides: Mapping[str, int] | None = None
    ) -> EffectParams:
        """The parameters a cluster's effect takes (default_params)."""
        return default_params(self.cluster(cluster).effect(effect), color, overrides)

    # -- per-key ---------------------------------------------------------

    def frame_end(self) -> None:
        perkey = self._require_per_key()
        self.dev.request(perkey.index, PERKEY_FRAME_END, bytes([0x00]))
        self._frame_end_at = time.monotonic()

    def _settle_frame(self) -> None:
        """Wait out what is left of FRAME_SETTLE since the last FrameEnd."""
        wait = self._frame_end_at + FRAME_SETTLE - time.monotonic()
        if wait > 0:
            time.sleep(wait)

    def _pack(self, colors: Mapping[int, int]) -> list[tuple[int, bytes]]:
        """Zone-write reports for a paint, as (function, payload).

        Per color, runs of MIN_RANGE_RUN or more go as ranges and the rest as id lists. The
        last lists of four or fewer ids are pooled across colors into individual writes when
        that takes fewer reports than a list each.
        """
        ranges: list[bytes] = []
        lists: list[tuple[bytes, tuple[int, ...]]] = []
        small: list[tuple[bytes, tuple[int, ...]]] = []
        by_color: dict[int, list[int]] = {}
        for zone, color in colors.items():
            if not MIN_ZONE <= zone <= MAX_ZONE:
                raise LightingError(f"zone {zone} outside {MIN_ZONE}..{MAX_ZONE}")
            by_color.setdefault(color, []).append(zone)

        for color, zones in by_color.items():
            rgb = _rgb(color)
            ids: list[int] = []
            # Consecutive ids differ from their position by a constant, so
            # grouping on that difference gives the contiguous runs.
            for _, group in groupby(enumerate(sorted(zones)), lambda p: p[1] - p[0]):
                run = [z for _, z in group]
                if len(run) >= MIN_RANGE_RUN:
                    ranges.append(bytes([run[0], run[-1]]) + rgb)
                else:
                    ids.extend(run)
            for chunk in batched(ids, 13):
                (lists if len(chunk) > 4 else small).append((rgb, chunk))

        reports = [(PERKEY_SET_RANGE, b"".join(batch)) for batch in batched(ranges, 3)]
        reports += [(PERKEY_SET_MULTI, rgb + bytes(ids)) for rgb, ids in lists]
        singles = [(z, rgb) for rgb, ids in small for z in ids]
        if (len(singles) + 3) // 4 < len(small):
            reports += [
                (PERKEY_SET_INDIVIDUAL, b"".join(bytes([z]) + rgb for z, rgb in batch))
                for batch in batched(singles, 4)
            ]
        else:
            reports += [(PERKEY_SET_MULTI, rgb + bytes(ids)) for rgb, ids in small]
        return reports

    def _commit_anyway(self, perkey: Feature) -> None:
        """FrameEnd on the way out of a frame that failed part way.

        Writes stage until FrameEnd, and past 27 staged reports the firmware NAKs by silence.
        An abandoned frame leaves its writes, and a late write's resend, staged against the
        next frame's cap, so every later write could fail. Committing empties the buffer; the
        next fill paints over it. One short attempt, since a keyboard that stopped answering
        would cost the full budget again; a failure is swallowed so the error raised is the
        one that stopped the frame.
        """
        try:
            self.dev.request(perkey.index, PERKEY_FRAME_END, bytes([0x00]),
                             timeout=COMMIT_TIMEOUT, retries=0)
        except HidppFailure:
            pass
        self._frame_end_at = time.monotonic()

    def _write_frames(self, perkey: Feature, reports: list[tuple[int, bytes]]) -> None:
        """Zone writes, a FrameEnd after each MAX_REPORTS_PER_FRAME of them, each frame said
        at debug with the time since the last FrameEnd was answered: on the G515 TKL a drag's
        preview frames showed only once the drag ended (2026-10-17), and the line tells its
        hardware test whether they went out and at what pace."""
        for frame in batched(reports, MAX_REPORTS_PER_FRAME):
            self._settle_frame()
            for n, (function, payload) in enumerate(frame, 1):
                try:
                    self.dev.request(perkey.index, function, payload, long=True)
                except HidppTimeout as exc:
                    self._commit_anyway(perkey)
                    # A HidppTimeout still, so not_there and hidpp_refusal read it as no answer.
                    raise HidppTimeout(
                        f"per-key write {n} of {len(frame)} in this frame got no "
                        f"reply; the device may be asleep"
                    ) from exc
                except HidppFailure:
                    self._commit_anyway(perkey)
                    raise
            last = self._frame_end_at
            self.frame_end()
            log.debug("per-key frame of %s committed, %s", counted(len(frame), "write"),
                      "the first" if last == float("-inf")
                      else f"{(self._frame_end_at - last) * 1000:.0f} ms after the last")

    def paint(self, colors: Mapping[int, int]) -> None:
        """Set individual zones to 0xrrggbb colors and commit the frame."""
        self._write_frames(self._require_per_key(), self._pack(colors))

    def fill_and_paint(self, color: int, colors: Mapping[int, int], last: int = MAX_ZONE) -> None:
        """Zones MIN_ZONE to `last` in `color`, then `colors` over it, shown by one FrameEnd, so
        the board never shows the fill without the zones, as a `fill` then a `paint` does.

        The zone writes go after the range to overwrite what it staged; within one frame that
        is untried on the G915. For the preview of a per-key paint, which shows the whole board
        at each key a drag reaches. Past MAX_REPORTS_PER_FRAME it splits like `paint`, the
        first frame with the fill.
        """
        perkey = self._require_per_key()
        reports = [(PERKEY_SET_RANGE, bytes([MIN_ZONE, last]) + _rgb(color))]
        reports += self._pack(colors)
        self._write_frames(perkey, reports)

    def fill(self, color: int, last: int = MAX_ZONE) -> None:
        """Set zones MIN_ZONE to `last` to one color and commit.

        The default range covers the logo, zone 210; pass `last` to leave it alone.
        """
        # Always an explicit range: on the G915 the broadcast id 255 through fn 6 left the
        # per-key buffer untouched, and the frame end showed its old color over the zone effect.
        self._write_frames(self._require_per_key(),
                           [(PERKEY_SET_RANGE, bytes([MIN_ZONE, last]) + _rgb(color))])

    def send_fill(self, color: int) -> tuple[tuple[int, int, int], ...]:
        """One frame of `fill` sent without waiting for either reply, for a mouse that may be
        asleep and slow to answer (Hidpp.send); returns the replies' keys (Hidpp.answer)."""
        perkey = self._require_per_key()
        payload = bytes([MIN_ZONE, MAX_ZONE]) + _rgb(color)
        self._settle_frame()
        sent = (self.dev.send(perkey.index, PERKEY_SET_RANGE, payload, long=True),
                self.dev.send(perkey.index, PERKEY_FRAME_END, bytes([0x00])))
        self._frame_end_at = time.monotonic()
        return sent

    # -- brightness ------------------------------------------------------

    @property
    def brightness_levels(self) -> tuple[int, ...]:
        return self._marks

    @property
    def brightness_index(self) -> int | None:
        """0x8040's feature index, or None without it. The G915 reports there, as event 0 with
        the level big-endian in its first two bytes, the level its brightness key just set."""
        return self._brightness.index if self._brightness is not None else None

    def get_brightness(self) -> int:
        if self._brightness is None:
            raise LightingError(NO_BRIGHTNESS)
        return int.from_bytes(self.dev.request(self._brightness.index, 1)[0:2], "big")

    def set_brightness(self, level: int) -> int:
        """Set backlight brightness, snapped to a level the firmware keeps; returns that level.

        The device floors a request to the level at or below it, so 10 of 0..100 turns the
        backlight off; the return value tells the caller so.
        """
        if self._brightness is None:
            raise LightingError(NO_BRIGHTNESS)
        if not 0 <= level <= max(self._marks):
            raise LightingError(f"brightness {level} outside 0..{max(self._marks)}")
        chosen = snap_brightness(self._marks, level)
        self.dev.request(self._brightness.index, 2, chosen.to_bytes(2, "big"))
        return chosen
