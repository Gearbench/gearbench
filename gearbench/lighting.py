"""Lighting for the keyboards and the mice with 0x8071: 0x8071 zone effects, 0x8081 per-key,
0x8040 brightness.

The rules here come from the device and from a capture of G HUB's USB traffic; where the two
disagree, this follows the device.

On the G915 a static zone effect alone leaves the keys dark; the per-key fill on top is what
shows (measured in HOST mode, 2026-09-22). A drag preview is one per-key frame, about 6 ms.
A paint larger than one frame is split across frames, which does not show because a frame
commits only what it carries.

The G502 X PLUS keeps its per-key effects on cluster 0xff, which the counted cluster walk never
reaches; a caller asks for it with `per_key_cluster`. The G515 generation's keyboards list their
whole-board effects there, read with `whole_board` (Lighting._read_board); a keyboard is never
probed for it.

Nothing here writes a non-volatile persistence flag or touches 0x8100 ONBOARD_PROFILES.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, replace
from itertools import batched, groupby
from typing import ClassVar, Literal, TypedDict, cast

from .hidpp import (
    REQUEST_RETRIES,
    REQUEST_TIMEOUT,
    Feature,
    Hidpp,
    HidppError,
    HidppFailure,
    HidppTimeout,
    UnreadableAnswer,
)
from .wording import counted

log = logging.getLogger("gearbench")

F_RGB = 0x8071
F_PERKEY = 0x8081
F_BRIGHTNESS = 0x8040
#: 0x8040's event address of the level its brightness key just set, big-endian in its
#: first two bytes (Lighting.brightness_index).
BRIGHTNESS_EVENT = 0x00

# 0x8071 function 5 setSWControl: [getOrSet, control, notify], getOrSet 0 to read and 1 to
# write. The firmware stores control and notify exactly as sent (its code at 0x2a6bc-0x2a6f0,
# read 2026-09-23); link setup zeroes both. Control bit 0: the host owns the effects (fn 1
# answers error 5 without it). Bit 1: the host owns the power modes, which fn 8 needs; while it
# is set the firmware skips its own power saving and sleep. Notify bit 0 asks for the
# effect-sync report (event address 0x00), bit 1 for the wake notice and bit 2 for the idle
# notice (IDLE_WAKE_EVENT).
SW_GET, SW_SET = 0x00, 0x01
SW_HOST = 0x03
#: The control byte a release leaves: effects and power modes back to the firmware, as G HUB
#: leaves them (`01 00 06`, the owner's decision of 2026-09-23).
SW_FIRMWARE = 0x00
NOTIFY_SYNC, NOTIFY_WAKE, NOTIFY_IDLE = 0x01, 0x02, 0x04
#: The event address of the idle and wake notices, and their data byte. The effect-sync
#: report, at address 0x00, comes while an animation runs.
IDLE_WAKE_EVENT = 0x10
IDLE_NOTICE, WAKE_NOTICE = 0x00, 0x01
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

#: 0x8081 getInfo's typeOfInfo for the zone bitmap, RGBZonePresence, and its pages, param1
#: 0 to 2: each reply echoes the two, then gives 14 bytes, a bit a zone, bit 0 of the first
#: byte the page's first zone, so the pages hold zones 0 to 111, 112 to 223 and 224 to 335
#: (x8081 v0, tables 5 to 7, read as Solaar's PerKeyLighting reads them).
ZONE_PRESENCE, ZONE_PAGES, ZONES_A_PAGE = 0x00, 3, 112

# Zone-write long reports a frame stages; one more gets no reply and is silently dropped.
MAX_REPORTS_PER_FRAME = 27

# How long a per-key frame waits after the last FrameEnd before its first zone write: one
# started about 1 ms after is applied in part.
FRAME_SETTLE = 0.02
# A contiguous run costs a third of a range report or one thirteenth of a
# list report per zone, so a range only pays off from five zones up.
MIN_RANGE_RUN = 5

# The first frame after a claim sometimes landed before the firmware had left onboard mode,
# and painted the logo only. So no write that shows something goes until this long after the
# claim (_settle_claim), and each frame is sent once. Requests that show nothing, the idle
# times, the diversion and host mode among them, go within it (the owner's decision of
# 2026-10-24).
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
    # G HUB's Pulsar Point (SIGNATURE_ALGORITHMIC_ACTIVE), the whole board's on cluster 0xff:
    # two colors, the period in ms and the intensity in percent, by G HUB's dual-color encoder,
    # read in its agent, which writes its signature index at [9] (SIGNATURE_BYTE), 0 for
    # Pulsar Point (its G512 X records), left 0 here; SIGNATURES run on it by other indexes.
    # Its writes on the G915 X LIGHTSPEED and the G515 TKL carry the same ten bytes (Windows
    # captures of 2026-10-23 and 2026-10-21).
    0x18: ("pulsar-point", {"color": (0, 1, 2), "second_color": (3, 4, 5), "period": (6, 7),
                            "intensity": (8,)}),
}

#: G HUB's other signature animations, which run on another's effect id by the signature index
#: G HUB writes at its SIGNATURE_BYTE, by name: (that id, the index). Remind Me Of, Neon and
#: Outer Space on Pulsar Point's 0x0018, indexes 1 to 3: G HUB's G512 X 75 and 98 records give
#: each its signatureEffectIndex on the whole board (ZONE_ALL), its window copies it into
#: the dual-color parameters it sends, and its agent's encoder writes that to [9] (each read
#: 2026-10-24). Each takes one color, its id's first: the records give each a first color
#: alone, and for the second the window sends none, which the encoder takes as its message's
#: empty color, [3..5] left 0 here (inferred; effect_layout). An entry of one is a cluster's
#: entry of the id under its name (Effect.own_name), made where its model lists it
#: (Lighting.whole_board), as no read of the keyboard tells them apart.
SIGNATURES: dict[str, tuple[int, int]] = {
    "remind-me-of": (0x18, 1),
    "neon": (0x18, 2),
    "outer-space": (0x18, 3),
}
#: The byte of an effect's ten parameter bytes that holds the signature index of one of
#: SIGNATURES.
SIGNATURE_BYTE = 9

#: Effect name -> the ids that run it, in EFFECTS' order, then SIGNATURES' each by its one id.
EFFECT_IDS: dict[str, tuple[int, ...]] = {
    **{name: tuple(eid for eid, (named, _) in EFFECTS.items() if named == name)
       for name, _ in EFFECTS.values()},
    **{name: (eid,) for name, (eid, _) in SIGNATURES.items()},
}

#: (effect id, parameter) -> the largest value it takes, where that is less than its bytes
#: hold: COLORWAVE_S's directions, 0 for its default to 8 center-in (the x8071 v4 spec).
PARAM_LIMITS = {(0x16, "direction"): 8}

#: The saturation an effect with one runs at, G HUB's default.
FULL_SATURATION = 0xFF

#: The intensity an effect that has one runs at.
FULL_INTENSITY = 100

#: The second color of an effect with two, where a profile has none: magenta, the other color
#: of G HUB's Pulsar Point preset, which beside the default blue reads as a second color (the
#: owner's decision of 2026-10-24, "Magenta #ff00ab").
DEFAULT_SECOND_COLOR = 0xFF00AB

#: The parameter bytes between an effect write's cluster and effect index and its persistence
#: byte.
PARAMETER_BYTES = 10


class EffectParams(TypedDict, total=False):
    """An effect's parameters by name, as default_params answers them and set_effect
    takes them by keyword. The names are those of EFFECTS' layouts."""

    color: int
    second_color: int
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
#: runs it and whatever a model calls it, or by its SIGNATURES name: cycle and wave as G HUB
#: stores them in the G915's onboard M1 profile, the others as G HUB was seen sending them, and
#: SIGNATURES' as G HUB's window gives a signature animation a period where the model's record
#: gives none, as the G512 X's do: 3000, its default smooth speed (read 2026-10-24). fn 0's
#: effect info has no period to read.
DEFAULT_PERIODS = {"cycle": 2100, "wave": 2100, "breathe": 3000, "ripple": 1000,
                   "pulsar-point": 3000, **dict.fromkeys(SIGNATURES, 3000)}

#: The animations a profile can show in place of Static or Per-key, in the
#: order the window lists them. Those of SPEEDS have a speed; wave has a direction.
ANIMATIONS = ("breathe", "cycle", "wave", "ripple", "pulsar-point", *SIGNATURES)

#: The period range in ms, (fastest, slowest), of each animation that takes a speed, in
#: ANIMATIONS' order: G HUB's speed slider runs 1000 to 20000. Ripple runs 100 to 1000, the
#: owner's choice of 2026-09-24 after watching it on the G915. Pulsar Point takes none: the
#: owner's G915 X LIGHTSPEED ran it at one pace with its period at 1000 and at 20000 ms, in two
#: runs (2026-10-24), and G HUB's window offers no speed for it, so it goes at G HUB's 3000
#: (DEFAULT_PERIODS) and has no Speed row (Claude Code's call of 2026-10-24, which the owner
#: confirmed). SIGNATURES take none either, as each runs on its effect.
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
#: Each of them in the window's Direction row's order, with the row's words for the way it
#: runs as watched above: the row's labels and `effect --direction`'s help.
DIRECTION_WORDS = {"horizontal": "Left to right", "reverse-horizontal": "Right to left",
                   "vertical": "Top to bottom", "reverse-vertical": "Bottom to top",
                   "center-out": "From the center out", "center-in": "From the edges in"}


def effect_layout(effect: int, name: str | None = None) -> dict[str, tuple[int, ...]]:
    """An effect id's parameters and their byte offsets, from EFFECTS; an id it lacks takes
    none. Run as `name`, one of SIGNATURES, it takes those of the id but its second color."""
    layout = EFFECTS.get(effect, ("", {}))[1]
    if name in SIGNATURES:
        return {key: offsets for key, offsets in layout.items() if key != "second_color"}
    return layout


def param_max(effect: int, key: str) -> int:
    """The largest value parameter `key` of effect id `effect` takes: its PARAM_LIMITS, or
    what its bytes in effect_layout hold."""
    return PARAM_LIMITS.get((effect, key), (1 << 8 * len(effect_layout(effect)[key])) - 1)


def fits(effect: int, key: str, value: int) -> bool:
    """Whether `value` is one the effect's `key` parameter takes (param_max)."""
    return 0 <= value <= param_max(effect, key)


#: The effect that turns the lights off, which the window's Effect rows call Off.
OFF = "disabled"

#: Effects whose color the firmware renders as a flat wash, by name. Per-key paint is only
#: visible on top of one of these; an animating effect overwrites it.
STATIC_EFFECTS = frozenset({OFF, "static"})

#: The zone effects a keyboard can be offered, by name, for front ends: EFFECTS also holds the
#: mouse's signature effects and `custom`, the per-key mode. A keyboard is offered those its
#: clusters report (offered_effects), and a cluster without one falls back to static.
KEYBOARD_EFFECTS = ("static", *ANIMATIONS, OFF)


def has_color(name: str, names: Mapping[int, str] | None = None) -> bool:
    """Whether the effect `name` takes a color: the layout of one of its ids has one
    (effect_layout). Its ids are those a device model calls `name` in `names` (Lighting's),
    else EFFECT_IDS', which must have it."""
    ids = [eid for eid, own in (names or {}).items() if own == name] or EFFECT_IDS[name]
    return any("color" in effect_layout(eid, name) for eid in ids)


#: The animations with no color of their own: no EFFECTS layout of theirs has one.
COLORLESS = tuple(name for name in ANIMATIONS if not has_color(name))


#: The animations with a second color, in ANIMATIONS' order: Pulsar Point. The layout of one
#: of its ids has one (effect_layout), as none of SIGNATURES' does.
TWO_COLORED = tuple(name for name in ANIMATIONS
                    if any("second_color" in effect_layout(eid, name) for eid in EFFECT_IDS[name]))


#: The effects a dim takes the intensity of and leaves the colors whole, by id: Pulsar Point,
#: as G HUB dims it at the idle notice, its intensity 0x32 for 0x64 and its colors as they were
#: (Windows capture G4, 2026-10-23), and SIGNATURES on its id, which G HUB's encoder dims in the
#: same branch, its intensity times the dim (read in its agent; no capture of them dimmed).
DIMMED_BY_INTENSITY = frozenset({0x18})


def dimmed_parameter(effect: int,
                     params: Mapping[str, object]) -> Literal["color", "intensity"] | None:
    """The parameter of `params`, effect id `effect`'s (default_params), that a dim halves: the
    intensity of one of DIMMED_BY_INTENSITY, else the color where it has one, else the
    intensity where it has one; None where it has neither."""
    if effect in DIMMED_BY_INTENSITY:
        return "intensity"
    if "color" in params:
        return "color"
    return "intensity" if "intensity" in params else None

#: The lighting levels a repaint goes through: a keyboard's (Keyboard.lights) and a synced
#: mouse's (Keyboard.mouse_level). On the keyboard, dim halves what dimmed_parameter names and
#: leaves the saved brightness untouched, and dark is every cluster Disabled with the claim held.
LIT, DIM, DARK = "lit", "dim", "dark"
LEVEL_WORDS = {LIT: "on", DIM: "dimmed", DARK: "off"}


class LightingError(RuntimeError):
    pass


#: The refusal of a brightness read or write on a device without 0x8040.
NO_BRIGHTNESS = "this device has no brightness control"


def need_reply(reply: bytes, length: int, what: str) -> bytes:
    """The reply, if long enough to decode; a short one raises hidpp.UnreadableAnswer, the
    one class of an answer Gearbench cannot read.

    Decoding it would read a wrong value, or raise IndexError, which the CLI does not catch.
    """
    if len(reply) < length:
        raise UnreadableAnswer(f"{what} reply is {len(reply)} bytes, needs {length}: "
                               f"{reply.hex(' ')}")
    return reply


@dataclass(frozen=True)
class Effect:
    cluster: int
    index: int
    id: int
    #: The device model's own name for this id, in place of EFFECTS' (Lighting's `names`), or
    #: one of SIGNATURES it runs on the id (Lighting.whole_board); None for EFFECTS' name.
    own_name: str | None = None

    @property
    def name(self) -> str:
        if self.own_name is not None:
            return self.own_name
        known = EFFECTS.get(self.id)
        return known[0] if known else f"effect-0x{self.id:04x}"

    def runs(self, which: str | int) -> bool:
        """Whether `which` is this effect: its id, its own name where it has one, else a name
        whose ids in EFFECT_IDS hold its id but one of SIGNATURES. An id with a name of its own
        answers to no other name: EFFECTS' for it is another model's; and one of SIGNATURES
        runs only on an entry of its name, which its model lists."""
        if not isinstance(which, str):
            return which == self.id
        if self.own_name is not None:
            return which == self.own_name
        return which not in SIGNATURES and self.id in EFFECT_IDS.get(which, ())


@dataclass(frozen=True)
class Cluster:
    index: int
    location: int
    effects: tuple[Effect, ...]
    #: The name the device gives it in place of its location's: WHOLE_BOARD for a keyboard's
    #: cluster 0xff (Lighting.whole_board); None for the location's.
    own_name: str | None = None

    #: 0x8071 location codes. Only the two this keyboard reports are named.
    LOCATIONS: ClassVar[dict[int, str]] = {0x01: "primary", 0x02: "logo"}
    #: Cluster 0xff: the mouse's per-key cluster, or a keyboard's whole board (WHOLE_BOARD).
    #: getInfo with [0xff, 0xff] describes the device, so it has no location or effect count of
    #: its own: byte 7 of that reply counts its effects, and a mouse's are probed where that is
    #: 0 (Lighting._probe_per_key).
    PERKEY_INDEX = 0xFF
    #: A keyboard's cluster 0xff, whose effects run over every key and the logo at once.
    WHOLE_BOARD: ClassVar[str] = "whole board"

    @property
    def name(self) -> str:
        if self.own_name is not None:
            return self.own_name
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
        has = ", ".join(e.name for e in self.effects) or "none"
        if isinstance(which, str) and which not in EFFECT_IDS:
            raise LightingError(f"unknown effect {which!r}; this cluster has {has}")
        raise LightingError(
            f"cluster {self.index} ({self.name}) has no effect {which!r}; it has {has}")


def effect_order(names: Iterable[str]) -> list[str]:
    """`names` in the one order of every Effect row and effects line, a keyboard's, a
    mouse's and a headset's: static, the per-key mode, the animations in KEYBOARD_EFFECTS'
    order, then a device's own effects as they come, and disabled, the lights off, last."""
    shared = ("static", "custom", *ANIMATIONS)

    def rank(name: str) -> int:
        if name == "disabled":
            return len(shared) + 1
        return shared.index(name) if name in shared else len(shared)

    return sorted(names, key=rank)


def offered_effects(clusters: tuple[Cluster, ...]) -> tuple[str, ...]:
    """The KEYBOARD_EFFECTS one of `clusters` reports, in KEYBOARD_EFFECTS' order: what a
    keyboard is offered. An id this build has no name for is never one of them."""
    return tuple(name for name in KEYBOARD_EFFECTS if any(c.has_effect(name) for c in clusters))


def default_params(chosen: Effect, color: int,
                   overrides: Mapping[str, int] | None = None) -> EffectParams:
    """The parameters effect `chosen` takes, with defaults, and `overrides` (a profile's saved
    settings, config.Lighting.settings) on top, for 0x8071's clusters and 0x8070's zones alike.

    Passing cycle a color is an error, so a caller handing every effect the same kwargs
    falls back instead of animating. Wave runs horizontal by default, the direction in the
    G915's onboard M1 record, an effect with a saturation at FULL_SATURATION, and one with a
    second color in DEFAULT_SECOND_COLOR. One of SIGNATURES takes its layout and its period by
    its own name.
    """
    takes = effect_layout(chosen.id, chosen.own_name)
    named = chosen.name if chosen.own_name in SIGNATURES else EFFECTS.get(chosen.id, ("", {}))[0]
    given = {"color": color, "second_color": DEFAULT_SECOND_COLOR,
             "saturation": FULL_SATURATION,
             **({"period": DEFAULT_PERIODS[named]} if "period" in takes else {}),
             "intensity": FULL_INTENSITY, "direction": WAVE_DIRECTIONS["horizontal"],
             "rate": SIGNATURE_RATE, **(overrides or {})}
    # Every key is a name from the effect's layout in EFFECTS, which EffectParams lists.
    return cast("EffectParams", {key: value for key, value in given.items() if key in takes})


def effect_parameters(chosen: Effect, params: Mapping[str, int]) -> bytes:
    """The ten parameter bytes of a write of effect `chosen`: each of `params` at its offsets
    (effect_layout), most significant byte first, zeros elsewhere, STATIC_RAMP for a static
    that `params` gives no ramp, and one of SIGNATURES' index at SIGNATURE_BYTE. A parameter
    the effect lacks, or a value it does not take (fits), raises LightingError."""
    takes = effect_layout(chosen.id, chosen.own_name)
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
                f"{chosen.name} {key} {value} outside 0 to {param_max(chosen.id, key)}")
        offsets = takes[key]
        for offset, byte in zip(offsets, value.to_bytes(len(offsets), "big"), strict=True):
            body[offset] = byte
    if chosen.own_name in SIGNATURES:
        body[SIGNATURE_BYTE] = SIGNATURES[chosen.name][1]
    return bytes(body)


def _rgb(color: int) -> bytes:
    return (color & 0xFFFFFF).to_bytes(3, "big")


def _wait_until(moment: float) -> None:
    """Sleep until `moment`, a time.monotonic(); nothing once it has passed."""
    wait = moment - time.monotonic()
    if wait > 0:
        time.sleep(wait)


def page_zones(page: int, bitmap: bytes) -> list[int]:
    """The zones one page of 0x8081's zone bitmap sets, from its 14 bytes after the echo: bit n
    of byte b is zone page * ZONES_A_PAGE + 8b + n. A bitmap cut short raises IndexError."""
    return [page * ZONES_A_PAGE + bit for bit in range(ZONES_A_PAGE)
            if bitmap[bit // 8] >> bit % 8 & 1]


def snap_brightness(levels: tuple[int, ...], level: int) -> int:
    """`level` floored to one of `levels`, as the device floors it; `level` when none is known."""
    return max((m for m in levels if m <= level), default=level)


@dataclass(frozen=True)
class LightingTables:
    """What a Lighting read off its device at its start: the clusters, the whole board, the
    brightness levels and the highest zone the zone bitmap sets (Lighting.tables), each fixed
    by the device's firmware and its board, for a later Lighting on the same device."""

    clusters: tuple[Cluster, ...]
    whole_board: Cluster | None
    marks: tuple[int, ...]
    listed_last: int | None


class Lighting:
    def __init__(self, dev: Hidpp, per_key_cluster: bool = False,
                 names: Mapping[int, str] | None = None, zone_bitmap: bool = False,
                 whole_board: Collection[str] = (), *,
                 tables: LightingTables | None = None) -> None:
        """`dev`'s lighting, its tables read off it (discover, and the zone bitmap with
        `zone_bitmap`), or with `tables` taken from those an earlier Lighting read off the same
        device, nothing read: the caller vouches for the device being the same."""
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
        #: The whole-board effects its model's record lists (keyboard.KeyboardModel.whole_board),
        #: for which cluster 0xff is read as a keyboard's whole board where the device info
        #: counts effects on it (discover), each kept where a counted cluster lacks it
        #: (_read_board); with none, nothing is read there.
        self._board_effects = tuple(whole_board)
        self.clusters: tuple[Cluster, ...] = ()
        #: A keyboard's cluster 0xff, with `whole_board`, where it lists one of those effects,
        #: which run over the whole board at once (_read_board): outside `clusters`, whose
        #: loops write each cluster.
        self.whole_board: Cluster | None = None
        self._claimed = False
        #: When the last per-key FrameEnd went (_settle_frame), and when a write that shows
        #: something may go after the last claim (_settle_claim).
        self._frame_end_at = self._claim_settles_at = float("-inf")
        self.brightness_levels: tuple[int, ...] = ()
        #: The highest zone of MIN_ZONE to MAX_ZONE that 0x8081's zone bitmap sets, where it
        #: was read, with `zone_bitmap`, as a keyboard's is (_read_listed_last); None where
        #: it was not, was refused or sets none there.
        self.listed_last: int | None = None
        #: Whether 0x8081 refused the zone bitmap (_read_listed_last), which is this
        #: Lighting's alone: a later one reads it again (tables).
        self._bitmap_refused = False
        if tables is not None:
            self.clusters, self.whole_board = tables.clusters, tables.whole_board
            self.brightness_levels, self.listed_last = tables.marks, tables.listed_last
            return
        self.discover()
        if zone_bitmap and self._perkey is not None:
            self.listed_last = self._read_listed_last(self._perkey)

    @property
    def tables(self) -> LightingTables | None:
        """What this Lighting read off its device, for a later one on the same device; None
        where the device refused its zone bitmap, so a later one reads it all again."""
        if self._bitmap_refused:
            return None
        return LightingTables(self.clusters, self.whole_board, self.brightness_levels,
                              self.listed_last)

    @classmethod
    def find(cls, dev: Hidpp, per_key_cluster: bool = False,
             names: Mapping[int, str] | None = None,
             zone_bitmap: bool = False,
             whole_board: Collection[str] = ()) -> Lighting | None:
        """`dev`'s lighting, or None when it has no 0x8071 or its 0x8071 lists no cluster:
        nothing to claim or paint. `names` are its model's own effect names, `zone_bitmap`
        has its zone bitmap read and `whole_board` its cluster 0xff for those effects
        (__init__)."""
        if dev.feature(F_RGB) is None:
            return None
        found = cls(dev, per_key_cluster, names, zone_bitmap, whole_board)
        return found if found.clusters else None

    # -- discovery -------------------------------------------------------

    def discover(self) -> None:
        """Read the cluster and effect tables, a keyboard's whole board (whole_board), and the
        brightness levels."""
        info = self.dev.request(self._rgb.index, 0, bytes([0xFF, 0xFF, 0x00]))
        # Hidpp drops a report shorter than its id's length, so a reply has 3 bytes at least.
        count = info[2]
        # Byte 7 counts the multi-cluster effects, the ones on cluster 0xff
        # (OpenLogi's x8071 page): 5 on the G502 X PLUS, 0 on the G915, 8 on the G915 X
        # LIGHTSPEED and 6 on the G515 TKL (Windows captures of 2026-10-23 and 2026-10-21).
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
        if self._board_effects:
            self.whole_board = self._read_board(multi)

        if self._brightness is None:
            return
        binfo = self.dev.request(self._brightness.index, 0)
        maximum = int.from_bytes(binfo[0:2], "big")
        steps = binfo[2] & 0x0F
        # The levels this firmware keeps: max=100, steps=5 gives (0, 12, 25, 50, 100).
        self.brightness_levels = tuple(
            sorted({maximum >> i for i in range(max(steps - 1, 1))} | {0}))

    def _probe_per_key(self, count: int) -> Cluster | None:
        """Cluster 0xff, which the counted walk cannot reach, read effect by effect.

        With `count`, byte 7 of the device query, that many are read, EFFECTS_MAX at most
        (_read_effects). That rests on one device, the G502 X PLUS, so with 0 they are asked
        for one at a time, up to 16, each once with PROBE_TIMEOUT, until an error, silence or
        an echo that is not this effect. DeviceGone propagates: it is not a NAK.
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
            probed = self._effect(r, f"0x8071 fn 0 cluster {index} effect {e}")
            if probed.cluster != index or probed.index != e:
                break
            effects.append(probed)
        if not effects:
            return None
        return Cluster(index, 0x0000, tuple(effects))

    def _read_board(self, count: int) -> Cluster | None:
        """A keyboard's cluster 0xff, its `count` effects, byte 7 of the device info, read one
        at a time as G HUB reads them, never probed, and kept to the effects its model lists
        (whole_board) that a counted cluster lacks: each that runs one of them, then for each
        of SIGNATURES among them the first entry of its id under its name. None where it lists
        none of those, a count of 0 included.

        The G915 X LIGHTSPEED lists 0x000C, 0x0013, 0x0016, 0x0017, 0x000A, 0x0015, 0x0018
        and 0x0019 there, the G515 TKL six of them (Windows captures of 2026-10-23 and
        2026-10-21). Wave and ripple are kept on the G915 X LIGHTSPEED, whose logo's cluster
        lacks them, and left to the G515 TKL's one cluster, which runs both; breathe and cycle
        run on the counted clusters, as each was checked there; custom is no effect offered;
        and 0x0013 and 0x0019 are left out: G HUB names neither, though it writes 0x0013
        before each per-key frame and the keyboards' stored profiles hold 0x0019."""
        index = Cluster.PERKEY_INDEX
        listed = self._read_effects(index, count)
        first = {effect.id: effect for effect in reversed(listed)}
        lacked = [name for name in self._board_effects
                  if any(not cluster.has_effect(name) for cluster in self.clusters)]
        kept = (*(effect for effect in listed if any(effect.runs(name) for name in lacked)),
                *(replace(first[SIGNATURES[name][0]], own_name=name)
                  for name in lacked if name in SIGNATURES and SIGNATURES[name][0] in first))
        return Cluster(index, 0x0000, kept, own_name=Cluster.WHOLE_BOARD) if kept else None

    def _read_effects(self, cluster: int, count: int) -> tuple[Effect, ...]:
        """Effects 0 to count - 1 of a cluster, EFFECTS_MAX at most, asked for one at a time."""
        return tuple(self._effect(self.dev.request(self._rgb.index, 0, bytes([cluster, e, 0x00])),
                                  f"0x8071 fn 0 cluster {cluster} effect {e}")
                     for e in range(min(count, EFFECTS_MAX)))

    def _read_listed_last(self, perkey: Feature) -> int | None:
        """The highest zone of MIN_ZONE to MAX_ZONE that the zone bitmap sets, its pages read
        one at a time (page_zones), or None where it sets none there. A range write ending on
        a zone the keyboard does not set may be refused whole: the G915 X LIGHTSPEED refused
        1 to 254 and took 1 to 210, and the G515 TKL took 1 to 254 and refused 1 to 210, each
        ending on the highest zone its bitmap sets (2026-10-23, 2026-10-18). A refusal is said
        at debug, gives None and leaves no tables for a later Lighting (tables); a receiver
        that cannot reach the keyboard (HidppError.unreachable) and any other failure go to
        the caller, as the walk's do."""
        zones: list[int] = []
        try:
            for page in range(ZONE_PAGES):
                reply = self.dev.request(perkey.index, 0, bytes([ZONE_PRESENCE, page]))
                need_reply(reply, 2 + ZONES_A_PAGE // 8, f"0x8081 fn 0 zone bitmap page {page}")
                zones += page_zones(page, reply[2:])
        except HidppError as exc:
            if exc.unreachable:
                raise
            log.debug("the zone bitmap was refused (%s); fills end as with none read", exc)
            self._bitmap_refused = True
            return None
        return max((zone for zone in zones if MIN_ZONE <= zone <= MAX_ZONE), default=None)

    def _effect(self, reply: bytes, what: str) -> Effect:
        """One effect-info reply: [cluster, index, id uint16 BE, caps uint32 BE], named by the
        model where it names the id."""
        need_reply(reply, 8, what)
        effect_id = int.from_bytes(reply[2:4], "big")
        return Effect(cluster=reply[0], index=reply[1], id=effect_id,
                      own_name=self._names.get(effect_id))

    @property
    def effect_clusters(self) -> tuple[Cluster, ...]:
        """Every cluster an effect can be written to: `clusters`, then the whole board where
        it was read (whole_board)."""
        return (*self.clusters, *((self.whole_board,) if self.whole_board else ()))

    def cluster(self, which: str | int) -> Cluster:
        """The cluster of effect_clusters with index or name `which`."""
        for c in self.effect_clusters:
            if c.index == which or c.name == which:
                return c
        raise LightingError(
            f"no cluster {which!r}; this device has "
            f"{', '.join(f'{c.index}={c.name}' for c in self.effect_clusters)}"
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

    def sw_control(self, timeout: float = REQUEST_TIMEOUT, retries: int = REQUEST_RETRIES) -> int:
        """fn 5's notify byte (sw_state)."""
        return self.sw_state(timeout, retries)[1]

    def sw_state(self, timeout: float = REQUEST_TIMEOUT,
                 retries: int = REQUEST_RETRIES) -> tuple[int, int]:
        """fn 5's control and notify bytes, which SW_STATES names."""
        reply = self.dev.request(self._rgb.index, 5, bytes([SW_GET]),
                                 timeout=timeout, retries=retries)
        return reply[1], reply[2]

    def claim(self) -> None:
        """Take software control of the lighting, the way G HUB does.

        A single write does not settle, and a zero control byte releases; either shows as a
        paint the firmware effect takes back on the first keypress. Nothing is waited here:
        the first write that shows something waits out SETTLE_AFTER_CLAIM (_settle_claim),
        and the caller's other requests go meanwhile.
        """
        for value in SW_CLAIM_SEQUENCE:
            self.dev.request(self._rgb.index, 5, bytes([SW_SET, SW_HOST, value]))
        self._claimed = True
        self._claim_settles_at = time.monotonic() + SETTLE_AFTER_CLAIM

    def _settle_claim(self) -> None:
        """Wait out what is left of SETTLE_AFTER_CLAIM since the last claim, before a write
        that shows something: an effect, a per-key frame or a brightness."""
        _wait_until(self._claim_settles_at)

    def release(self, timeout: float = REQUEST_TIMEOUT, retries: int = REQUEST_RETRIES) -> None:
        """Hand the LEDs back and let the firmware repaint from its onboard profile.

        Required on exit: per-key paint has no persistence flag, but nothing reclaims a cluster
        that is not animating, so the keyboard would keep showing the last paint. Released while
        in host mode, the wired G515 TKL kept the last paint all the same (2026-10-24), so a
        keyboard of the G515 generation is set to its on-board mode first.

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
        self._settle_claim()
        self.dev.request(
            self._rgb.index, 1, self._effect_payload(cluster, effect, params),
            long=True, reply=wait,
        )

    def send_effect(self, cluster: str | int, effect: str | int,
                    **params: int) -> tuple[int, int, int]:
        """One zone effect sent without waiting: the key of its reply (Hidpp.answer), as
        send_fill, for a mouse that may be asleep."""
        self._settle_claim()
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

    def _settle_frame(self) -> None:
        """Wait out what is left of FRAME_SETTLE since the last FrameEnd and of the claim's
        settle (_settle_claim), in one wait."""
        _wait_until(max(self._frame_end_at + FRAME_SETTLE, self._claim_settles_at))

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
                raise LightingError(f"zone {zone} outside {MIN_ZONE} to {MAX_ZONE}")
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

        Writes stage until FrameEnd, and past MAX_REPORTS_PER_FRAME staged reports the firmware
        NAKs by silence. An abandoned frame leaves its writes, and a late write's resend, staged
        against the next frame's cap, so every later write could fail. Committing empties the
        buffer; the next fill paints over it. One short attempt, since a keyboard that stopped
        answering would cost the full budget again; a failure is swallowed so the error raised is
        the one that stopped the frame.
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
                        f"per-key write {n} of {len(frame)} in this frame got no reply"
                    ) from exc
                except HidppFailure:
                    self._commit_anyway(perkey)
                    raise
            last = self._frame_end_at
            self.dev.request(perkey.index, PERKEY_FRAME_END, bytes([0x00]))
            self._frame_end_at = time.monotonic()
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
        is untried on the G915. Past MAX_REPORTS_PER_FRAME it splits like `paint`, the first
        frame with the fill.
        """
        perkey = self._require_per_key()
        # Always an explicit range: on the G915 the broadcast id 255 through fn 6 left the
        # per-key buffer untouched, and the frame end showed its old color over the zone effect.
        reports = [(PERKEY_SET_RANGE, bytes([MIN_ZONE, last]) + _rgb(color))]
        reports += self._pack(colors)
        self._write_frames(perkey, reports)

    def fill(self, color: int, last: int = MAX_ZONE) -> None:
        """Set zones MIN_ZONE to `last` to one color and commit.

        The default range covers the logo, zone 210; pass `last` to leave it alone.
        """
        self.fill_and_paint(color, {}, last)

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
    def brightness_index(self) -> int | None:
        """0x8040's feature index, or None without it. The G915 reports there
        (BRIGHTNESS_EVENT) the level its brightness key just set."""
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
        if not 0 <= level <= max(self.brightness_levels):
            raise LightingError(f"brightness {level} outside 0 to {max(self.brightness_levels)}")
        chosen = snap_brightness(self.brightness_levels, level)
        self._settle_claim()
        self.dev.request(self._brightness.index, 2, chosen.to_bytes(2, "big"))
        return chosen
