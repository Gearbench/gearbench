"""An Astro A50 preset's sound as a Logitech G432 Equalizer preset: the A50 preset's curve,
drawn by a model of the base station's filters, fitted onto the G432's ten bands in whole
dB, with its bass and treble shelves at 0, and its name kept.

The A50's model is its EQ research's (2026-09-27), from the base station's firmware: a
peak is a Regalia-Mitra allpass whose bandwidth in Hz is the width times the frequency, a
shelf a first-order allpass whose corner is the frequency, at 48 kHz, each with its gain
applied in the same form for a cut as for a boost. The G432's peaks are PipeWire 1.6.2's
bq_peaking at g432_presets.BAND_Q and g432_presets.RATE (spa/plugins/audioconvert/biquad.c, read
2026-10-18), the filters the chain runs. Standard library only.
"""

from __future__ import annotations

import cmath
import math
from collections.abc import Sequence
from functools import cache, partial

from . import astro, g432_presets

#: The base station's sample rate, which its filters' coefficients are for (the A50 EQ
#: research's disassembly of its firmware, 2026-09-27).
A50_RATE = 48000
#: Where the curves are compared: twelve points an octave, from 20 Hz to 19.3 kHz.
FIT_FREQS = tuple(20 * 2 ** (step / 12) for step in range(120))
#: The fit's most passes (fitted), each over the ten bands or a pair move. It stops at
#: the first that moves no gain: the A50's three presets take 3 to 5 (measured 2026-10-18).
MAX_PASSES = 50


def _z(freq: float, rate: int) -> complex:
    """z to the -1 on the unit circle at `freq`."""
    return cmath.exp(-2j * math.pi * freq / rate)


def _a50_peak(freq: float, center: int, width: float, gain: int) -> complex:
    """A band 2 to 4 of the A50's at `freq`. A bandwidth at half the rate or past it, which
    Command Center's fields allow (a width of 3.0 at 15 kHz), is taken just under it,
    where the band is its gain at every frequency, its widest."""
    big = 10 ** (gain / 20)
    bandwidth = min(width * center, A50_RATE / 2 - 1)
    t = math.tan(math.pi * bandwidth / A50_RATE)
    alpha = (1 - t) / (1 + t)
    beta = math.cos(2 * math.pi * center / A50_RATE)
    z = _z(freq, A50_RATE)
    allpass = ((alpha - beta * (1 + alpha) * z + z * z)
               / (1 - beta * (1 + alpha) * z + alpha * z * z))
    return (1 + big) / 2 + (1 - big) / 2 * allpass


def _a50_shelf(freq: float, corner: int, gain: int, high: bool) -> complex:
    """Band 1 of the A50's, the low shelf, or band 5, the high one, at `freq`."""
    big = 10 ** (gain / 20)
    t = math.tan(math.pi * corner / A50_RATE)
    a = (1 - t) / (1 + t)
    z = _z(freq, A50_RATE)
    allpass = (z - a) / (1 - a * z)
    return 1 + (big - 1) / 2 * ((1 - allpass) if high else (1 + allpass))


def a50_curve(preset: astro.EqPreset) -> tuple[float, ...]:
    """The A50 preset's gain in dB at each of FIT_FREQS."""
    low, *peaks, high = astro.BANDS
    curve = []
    for freq in FIT_FREQS:
        h = _a50_shelf(freq, preset.freqs[low - 1], preset.gains[low - 1], high=False)
        for band, width in zip(peaks, preset.widths, strict=True):
            h *= _a50_peak(freq, preset.freqs[band - 1], width, preset.gains[band - 1])
        h *= _a50_shelf(freq, preset.freqs[high - 1], preset.gains[high - 1], high=True)
        curve.append(20 * math.log10(abs(h)))
    return tuple(curve)


@cache
def _band_curve(center: int, gain: int) -> tuple[float, ...]:
    """A G432 band's gain in dB at each of FIT_FREQS: bq_peaking, A = 10^(dB/40) and
    alpha = sin(w0) / (2Q), as biquad.c computes it."""
    big = 10 ** (gain / 40)
    w0 = 2 * math.pi * center / g432_presets.RATE
    alpha, k = math.sin(w0) / (2 * g432_presets.BAND_Q), math.cos(w0)
    curve = []
    for freq in FIT_FREQS:
        z = _z(freq, g432_presets.RATE)
        h = ((1 + alpha * big - 2 * k * z + (1 - alpha * big) * z * z)
             / (1 + alpha / big - 2 * k * z + (1 - alpha / big) * z * z))
        curve.append(20 * math.log10(abs(h)))
    return tuple(curve)


def _nearest(freq: int) -> int:
    """The index of the point of FIT_FREQS nearest `freq`, an octave being 12 points: 8
    to 116 for the bands, 32 Hz to 16 kHz, inside the 120."""
    return round(12 * math.log2(freq / FIT_FREQS[0]))


def _curve_of(gains: Sequence[int]) -> list[float]:
    """The ten bands' curve at FIT_FREQS for `gains`: a cascade's dB add."""
    return [sum(column) for column in zip(*(
        _band_curve(freq, gain) for freq, gain in zip(g432_presets.BAND_FREQS, gains, strict=True)),
        strict=True)]


def _error(gains: Sequence[int], curve: Sequence[float]) -> float:
    return sum((got - want) ** 2 for got, want in zip(_curve_of(gains), curve, strict=True))


def _miss(rest: Sequence[float], freq: int, gain: int, curve: Sequence[float]) -> float:
    """The squared error of the band at `freq` at `gain` over `rest`, the other bands."""
    return sum((r + own - want) ** 2
               for r, own, want in zip(rest, _band_curve(freq, gain), curve, strict=True))


def _each_band(gains: list[int], curve: Sequence[float]) -> bool:
    """One pass: each band in turn takes its best gain with the others as they are.
    Whether any changed."""
    low, high = g432_presets.DB_RANGE
    total = _curve_of(gains)
    changed = False
    for index, freq in enumerate(g432_presets.BAND_FREQS):
        rest = [t - own for t, own in zip(total, _band_curve(freq, gains[index]), strict=True)]
        best = min(range(low, high + 1), key=partial(_miss, rest, freq, curve=curve))
        changed = changed or best != gains[index]
        gains[index] = best
        total = [r + own for r, own in zip(rest, _band_curve(freq, best), strict=True)]
    return changed


def _pairs(gains: list[int]) -> list[list[int]]:
    """`gains` with two neighbor bands moved 1 dB each, the same way or apart, which a pass
    of one band at a time cannot reach: each such move that stays in g432_presets.DB_RANGE."""
    low, high = g432_presets.DB_RANGE
    moves = []
    for index in range(len(gains) - 1):
        for one, two in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
            moved = list(gains)
            moved[index] += one
            moved[index + 1] += two
            if low <= moved[index] <= high and low <= moved[index + 1] <= high:
                moves.append(moved)
    return moves


def fitted(curve: Sequence[float]) -> g432_presets.Gains:
    """The ten band gains, whole dB in g432_presets.DB_RANGE, that a local least-squares
    search brings near `curve`, given at FIT_FREQS, with the shelves at 0: gains a little
    nearer can exist."""
    low, high = g432_presets.DB_RANGE
    gains = [min(high, max(low, round(curve[_nearest(freq)]))) for freq in g432_presets.BAND_FREQS]
    for _ in range(MAX_PASSES):
        if _each_band(gains, curve):
            continue
        best = min(_pairs(gains), key=lambda moved: _error(moved, curve))
        if _error(best, curve) >= _error(gains, curve):
            break
        gains = best
    return g432_presets.Gains(bands=tuple(gains))


def from_a50(preset: astro.EqPreset) -> g432_presets.Preset:
    """The A50 preset as a G432 preset: its name, and its curve fitted onto the ten bands."""
    return g432_presets.Preset(preset.name, fitted(a50_curve(preset)))
