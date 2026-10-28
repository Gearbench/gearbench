"""The drawn keyboard's animation: the color each lit key of a model's board shows at a moment of
an animation the Keyboard tab shows (lighting.ANIMATIONS), one still frame of it for a desktop
that turns animations off, and the key a key typed in the window ripples from.

A close likeness of what the keyboard runs, not frame-exact (the owner's decision of
2026-10-24, "Close approximation"), from the effect's own parameters: its color or colors, its
period (the Speed row) and wave's direction. Breathe, cycle and wave run as the window's tips
say, ripple and Pulsar point as the owner watched the G915 X LIGHTSPEED run them on
2026-10-24; each look that is a guess says so where it is set.
"""

from __future__ import annotations

import functools
import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from typing import NamedTuple

from ..keyboard import KeyboardModel
from ..keycodes import LOGO, ZONES
from ..layout import Board
from ..lighting import SIGNATURES, TWO_COLORED
from ..uinput import KEYCODES
from .color import pure_hue

#: How many key units a ripple's ring spreads in one period: one key width. The owner timed the
#: G915 X LIGHTSPEED's ring from Esc to the far right keys, about 22 key widths, with a
#: stopwatch on 2026-10-24: about 20 s at 1000 ms (two runs) and about 2 s at 100 ms (one run),
#: about a key a period at both speeds (inferred from the two).
RIPPLE_SPREAD = 1.0
#: How wide a ripple's ring is, in key units: a guess.
RIPPLE_WIDTH = 1.5
#: How much of its brightness a ripple's ring has lost when it has passed the key farthest from
#: where it started, lost evenly on its way: a guess, which keeps it seen at the far keys, where
#: the owner timed the keyboard's ring arriving.
RIPPLE_FADE = 0.5
#: How many ripples the drawn board keeps spreading, the newest, the oldest dropped as a new one
#: starts: a guess that bounds a frame's cost, since every lit key is shaded for each ring and a
#: ring lasts about 23 periods on a full-size board.
RIPPLE_LIMIT = 8
#: The least of its brightness Pulsar point's second color falls to between its wave's crests:
#: a guess, about a third of full. The owner saw "Dimmer magenta" there, never dark (2026-10-24).
PULSAR_FLOOR = 0.35
#: The keys an animation holds steady in its color, by zone: Pulsar point's W, A, S and D in its
#: first color, as the owner watched the G915 X LIGHTSPEED run it on 2026-10-24.
STEADY_KEYS: dict[str, frozenset[int]] = {
    "pulsar-point": frozenset(ZONES[name] for name in ("W", "A", "S", "D")),
}


@dataclass(frozen=True)
class Animation:
    """An animation as the board draws it: the effect's name, its color, its second color (Pulsar
    point's), its period in ms, wave's direction (lighting.WAVE_DIRECTIONS' names), and whether
    the logo shows the color steady in place of the animation (logo_steady)."""

    effect: str
    color: int
    second_color: int
    period: int
    direction: str
    logo_steady: bool = False


@dataclass(frozen=True)
class Ripple:
    """A ripple spreading from where a key stands (places), at `x` and `y` key units from the
    board's corner, started `start` seconds into the animation, that lasts `periods` periods
    (ripple_on)."""

    x: float
    y: float
    start: float
    periods: float


class Place(NamedTuple):
    """Where a lit key stands, or on a board lit by areas the middle of its area: `x` and `y` in
    key units from the board's corner, and `across` and `down` the same as a fraction of the
    board's width and height."""

    x: float
    y: float
    across: float
    down: float


def logo_steady(effect: str, model: KeyboardModel | None) -> bool:
    """Whether the logo shows the Static color steady under `effect` on a keyboard of `model`
    (None before a status names one): under ripple, which the logo's own cluster lacks, as the
    daemon then writes it static, unless the keyboard is read for ripple on its whole board
    (KeyboardModel.whole_board), where it takes the logo along, as the G915 X family's does.
    Under wave the logo waves: the G915's keys' wave takes it along, which the owner watched on
    2026-09-24, and a wave on the whole board does too. A logo's cluster that lacks ripple: the
    G915's, and the G915 X LIGHTSPEED's, which lists disabled, static, breathe and cycle (its
    first connect, 2026-10-23); every other model's is taken to be the same (assumed). A model
    whose board draws no logo key, or lights it with its area (frame), shows none steady."""
    if effect != "ripple":
        return False
    return model is None or ("ripple" not in model.whole_board
                             and LOGO in model.board.zones and not model.board.areas)


@functools.cache
def places(board: Board) -> dict[int, Place]:
    """Where each lit key of `board` stands, by zone (Place): on a board lit by areas each key
    at the middle of its area, which shows one color."""
    middles = {key.zone: (key.x - board.origin_x + key.w / 2, key.y - board.origin_y + key.h / 2)
               for key in board.keys if key.zone is not None}
    for area in board.areas:
        inside = [middles[zone] for zone in area]
        xs, ys = [x for x, _ in inside], [y for _, y in inside]
        middle = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
        middles.update(dict.fromkeys(area, middle))
    return {zone: Place(x, y, x / board.width, y / board.height)
            for zone, (x, y) in middles.items()}


def hue(turn: float) -> int:
    """The color `turn` of the way round the color wheel from red, a turn on is the same."""
    return pure_hue(turn % 1.0)


def scaled(color: int, level: float) -> int:
    """`color` at `level` of its brightness, from 0, black, to 1, red, green and blue each."""
    return sum(round(((color >> shift) & 0xFF) * level) << shift for shift in (16, 8, 0))


def _swell(turn: float) -> float:
    """A smooth rise and fall over one turn: 0 at its start and end, 1 halfway."""
    return (1 - math.cos(2 * math.pi * turn)) / 2


#: How far along a wave's way each place is, from 0 to 1, by the wave's direction: one rainbow
#: across the board (a guess), the center-out pair across its width from the middle, as the
#: owner watched them run on the G915 (lighting.WAVE_DIRECTIONS).
WAVE_ALONG: dict[str, Callable[[Place], float]] = {
    "horizontal": lambda place: place.across,
    "reverse-horizontal": lambda place: 1 - place.across,
    "vertical": lambda place: place.down,
    "reverse-vertical": lambda place: 1 - place.down,
    "center-out": lambda place: abs(2 * place.across - 1),
    "center-in": lambda place: 1 - abs(2 * place.across - 1),
}


def ripple_on(board: Board, x: float, y: float, start: float) -> Ripple:
    """A ripple on `board` from `x` and `y`, started `start` seconds in, that lasts until its
    ring has passed the lit key farthest from there by the ring's width."""
    farthest = max(math.hypot(place.x - x, place.y - y) for place in places(board).values())
    return Ripple(x, y, start, (farthest + RIPPLE_WIDTH) / RIPPLE_SPREAD)


def _ring(place: Place, ripple: Ripple, turns: float) -> float:
    """How bright `ripple`'s ring is at `place`, `turns` periods after it started, from 0 to 1:
    its ring spreads RIPPLE_SPREAD key units a period and fades by RIPPLE_FADE over its life,
    after which it lies past every key of its board."""
    # A frame's time, the frame clock's, can be a little before the press that started it.
    if turns < 0:
        return 0.0
    off = abs(math.hypot(place.x - ripple.x, place.y - ripple.y) - RIPPLE_SPREAD * turns)
    return max(0.0, 1 - off / RIPPLE_WIDTH) * (1 - RIPPLE_FADE * turns / ripple.periods)


#: What a place shows `turns` periods into each animation, given the animation, the place, the
#: turns and the ripples spreading, each started `start` turns in.
Shade = Callable[[Animation, Place, float, tuple[Ripple, ...]], int]


def _twinkle(animation: Animation, place: Place, turns: float,
             _ripples: tuple[Ripple, ...]) -> int:
    """The G512 X's three, a guess: the color fading in and out on each key at its own beat, a
    fraction of a turn taken from where it stands."""
    beat = math.sin(place.x * 12.9898 + place.y * 78.233) * 43758.5453
    return scaled(animation.color, _swell(turns + beat % 1.0))


def _pulsar(animation: Animation, place: Place, turns: float,
            _ripples: tuple[Ripple, ...]) -> int:
    """Pulsar point: its second color, its brightness a wave from PULSAR_FLOOR to full that
    crosses the board from left to right once a period, one crest across the board (a guess):
    the crest is at the left edge as a period starts and in the middle of the board half a
    period on, and comes in again at the left as it leaves at the right."""
    return scaled(animation.second_color,
                  PULSAR_FLOOR + (1 - PULSAR_FLOOR) * _swell(place.across - turns + 0.5))


SHADES: dict[str, Shade] = {
    "breathe": lambda animation, _place, turns, _ripples: scaled(animation.color,
                                                                  _swell(turns)),
    "cycle": lambda _animation, _place, turns, _ripples: hue(turns),
    "wave": lambda animation, place, turns, _ripples: hue(
        turns - WAVE_ALONG[animation.direction](place)),
    "ripple": lambda animation, place, turns, ripples: scaled(
        animation.color, max((_ring(place, ripple, turns - ripple.start) for ripple in ripples),
                             default=0.0)),
    "pulsar-point": _pulsar,
    **dict.fromkeys(SIGNATURES, _twinkle),
}


def frame(animation: Animation, board: Board, seconds: float,
          ripples: Iterable[Ripple] = ()) -> dict[int, int]:
    """The color each lit key of `board` shows `seconds` into `animation`, by zone, with
    `ripples` spreading: on a board lit by areas each area's at its middle (places), and the
    logo in the animation's color where it stays steady (Animation.logo_steady), as are the
    animation's STEADY_KEYS the board has."""
    shade = SHADES[animation.effect]
    turns = seconds * 1000 / animation.period
    started = tuple(replace(ripple, start=ripple.start * 1000 / animation.period)
                    for ripple in ripples)
    shown = {zone: shade(animation, place, turns, started)
             for zone, place in places(board).items()}
    if animation.logo_steady and LOGO in shown and not board.areas:
        shown[LOGO] = animation.color
    steady = shown.keys() & STEADY_KEYS.get(animation.effect, frozenset())
    shown.update(dict.fromkeys(steady, animation.color))
    return shown


def still_frame(animation: Animation, board: Board) -> dict[int, int]:
    """One frame that names the animation by its colors, for a desktop that turns animations
    off: cycle and wave every color across the board, wave's in its direction, Pulsar point W,
    A, S and D in its first color and its second on the other keys with its crest in the middle
    of the board, and the others their color on every key."""
    if animation.effect in ("cycle", "wave"):
        direction = animation.direction if animation.effect == "wave" else "horizontal"
        return frame(replace(animation, effect="wave", direction=direction), board, 0.0)
    if animation.effect in TWO_COLORED:
        # Half a period in, Pulsar point's crest is in the middle of the board.
        return frame(animation, board, animation.period / 2000)
    return dict.fromkeys(places(board), animation.color)


def live(ripples: Iterable[Ripple], seconds: float, period: int) -> list[Ripple]:
    """The ripples still spreading `seconds` into an animation of `period` ms: each lasts its
    `periods` periods (ripple_on)."""
    return [ripple for ripple in ripples
            if seconds - ripple.start < ripple.periods * period / 1000]


#: The zone names of the keys whose evdev name is not KEY_ and the zone's name.
_TYPED_NAMES = {
    "KEY_GRAVE": "`", "KEY_MINUS": "-", "KEY_EQUAL": "=", "KEY_LEFTBRACE": "[",
    "KEY_RIGHTBRACE": "]", "KEY_BACKSLASH": "\\", "KEY_CAPSLOCK": "CAPS LOCK",
    "KEY_SEMICOLON": ";", "KEY_APOSTROPHE": "'", "KEY_COMMA": ",", "KEY_DOT": ".",
    "KEY_SLASH": "/", "KEY_LEFTSHIFT": "LEFT SHIFT", "KEY_RIGHTSHIFT": "RIGHT SHIFT",
    "KEY_LEFTCTRL": "LEFT CTRL", "KEY_RIGHTCTRL": "RIGHT CTRL", "KEY_LEFTALT": "LEFT ALT",
    "KEY_RIGHTALT": "RIGHT ALTGR", "KEY_LEFTMETA": "LEFT WINDOWS",
    "KEY_RIGHTMETA": "RIGHT WINDOWS", "KEY_SYSRQ": "PRINT", "KEY_SCROLLLOCK": "SCROLL LOCK",
    "KEY_PAGEUP": "PAGE UP", "KEY_PAGEDOWN": "PAGE DOWN", "KEY_KPSLASH": "KEYPAD /",
    "KEY_KPASTERISK": "KEYPAD *", "KEY_KPMINUS": "KEYPAD -", "KEY_KPPLUS": "KEYPAD +",
    "KEY_KPENTER": "KEYPAD ENTER", "KEY_KPDOT": "KEYPAD .",
    **{f"KEY_KP{number}": f"KEYPAD {number}" for number in range(10)},
}
#: The zone of each key a press in the window names on the G915's board, by its evdev code:
#: KEY_A is A's, KEY_F1 F1's, KEY_LEFT the left arrow's, and the others by _TYPED_NAMES.
#: typed_zone gives a media key's zone on the board drawn instead.
TYPED_ZONES: dict[int, int] = {
    **{KEYCODES[f"KEY_{name}"]: zone for name, zone in ZONES.items()
       if f"KEY_{name}" in KEYCODES},
    **{KEYCODES[evdev]: ZONES[name] for evdev, name in _TYPED_NAMES.items()},
}
#: The media keys' labels on every board that draws them, by evdev code: a press names one by
#: its label, since the PRO X TKL LIGHTSPEED lights them at ids of its own
#: (layout.PRO_X_TKL_KEYS), not the G915's.
MEDIA_LABELS = {KEYCODES["KEY_PREVIOUSSONG"]: "Prev", KEYCODES["KEY_PLAYPAUSE"]: "Play",
                KEYCODES["KEY_NEXTSONG"]: "Next", KEYCODES["KEY_MUTE"]: "Mute"}


def typed_zone(board: Board, code: int) -> int | None:
    """The zone of the lit key of `board` a press of evdev `code` names: a media key's by its
    label there (MEDIA_LABELS), any other's by TYPED_ZONES; None for a key it names none of."""
    label = MEDIA_LABELS.get(code)
    if label is None:
        return TYPED_ZONES.get(code)
    return next((key.zone for key in board.keys if key.label == label), None)
