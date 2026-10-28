"""0x020D AdvancedParaEQ, G HUB's FeatureC020DAdvancedParaEQ: a headset's parametric
equalizers, one an instance, each with its slots.

Its functions as G HUB sent them to the A50 X (G HUB's captures of 2026-10-21, B3 and
B4-04 to B4-06): getCustomEQ (1) of an instance and a slot, which answers one byte, then
the slot's ten bands, for the slot the instance plays alone and error 0x0b for another;
setCustomEQ (2) of an instance, the slot it plays and 00, then ten bands; and the read of
any slot's ten bands with no byte before them (5). A band is its frequency in Hz (two
bytes, big-endian), its Q (one byte) and its gain (two bytes, big-endian), 120 plus 20 a
dB: G HUB's -6 to +6 dB in 0.05 dB steps. The Q byte is Q times 32, and 00 is G HUB's
simple mode, which sends no Q. Function 3, which G HUB reads too and which answers the slot
played, is not sent.

The A50 X lists version 1. The G522 and the G325 list version 2, read and written in the
same layout (Solaar's advanced_para_eq.py, from captures of a G522; HeadsetControl's G522
driver; the G325's Solaar logs, whose getCustomEQ of instance 0, slot 0 answered `00` and
ten such bands, and slot 1 error 0x0b): each model's record gives its instances and the
slot they play. Solaar reads a version 0 or 1 in bands of three bytes, a frequency and a
gain in whole dB, but the A50 X, at version 1, answers the five-byte bands above: the
layout is not taken from the version.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ..hidpp import UnreadableAnswer
from ..wording import counted, given_count, number, whole, written

ID = 0x020D
WORDS = "equalizer"
FN_GET, FN_SET, FN_SLOT = 1, 2, 5
BAND_COUNT = 10
BAND_BYTES = 5
GAIN_ZERO = 120
STEPS_A_DB = 20
GAIN_RANGE = (-6, 6)
#: The Q G HUB writes with each of the A50 X's presets' bands, 0x16, 0.6875 (B3, B4-06,
#: B8).
PRESET_Q = 22
#: A band's frequency in Hz, G HUB's Advanced mode's 20 Hz to 20 kHz (the A50 X's captures,
#: X2, where the headset echoed 33 Hz for band 1; above 20 kHz was never sent), and its Q
#: byte's whole range.
FREQ_RANGE = (20, 20000)
Q_RANGE = (0, 0xFF)


@dataclass(frozen=True)
class Band:
    """One band: its frequency in Hz, its Q byte as its feature takes it, and its gain in
    dB."""

    freq: int
    q: int
    gain: float


def preset_of[K](bands: tuple[Band, ...], presets: Mapping[K, tuple[Band, ...]]) -> K | None:
    """The key of the first preset whose frequencies and gains `bands` have, or None for
    none: the Q is left out, since G HUB writes a preset with a Q of its own (PRESET_Q)."""
    def heard(held: tuple[Band, ...]) -> list[tuple[int, float]]:
        return [(band.freq, band.gain) for band in held]

    return next((name for name, held in presets.items() if heard(held) == heard(bands)), None)


def fields_of(entry: object) -> tuple[object, object, object]:
    """A band's `freq`, `q` and `gain` from `entry` as a request or a file gives it;
    ValueError with the sentence for an entry that is not an object."""
    if not isinstance(entry, dict):
        why = f"a band is an object of its freq, q and gain, not {written(entry)}"
        raise ValueError(why)  # noqa: TRY004 -- refused in words, as every value of a request is
    return entry.get("freq"), entry.get("q"), entry.get("gain")


def band_of(entry: object) -> Band:
    """A band from `entry`, an object of its `freq`, `q` and `gain` as a request or a file
    gives it, each checked: the frequency in FREQ_RANGE, the Q byte in Q_RANGE and the gain
    as raw_gain takes it. ValueError with the sentence for one it does not take."""
    freq, q, gain = fields_of(entry)
    low, high = FREQ_RANGE
    if not whole(freq) or not low <= freq <= high:
        raise ValueError(f"a band's frequency is a whole number of Hz from {low} to {high}, "
                         f"not {written(freq)}")
    if not whole(q) or not Q_RANGE[0] <= q <= Q_RANGE[1]:
        raise ValueError(f"a band's Q is a whole number from {Q_RANGE[0]} to {Q_RANGE[1]}, "
                         f"not {written(q)}")
    # On the wire's 0.05 dB steps, as the headset will read it back.
    return Band(freq, q, gain_of(raw_gain(gain)))


def bands_from(entries: object) -> tuple[Band, ...]:
    """The ten bands of `entries`, a list of BAND_COUNT objects band_of takes; ValueError
    with the sentence for any other."""
    if not isinstance(entries, list) or len(entries) != BAND_COUNT:
        raise ValueError(f"an equalizer takes {counted(BAND_COUNT, 'band')}, "
                         f"not {given_count(entries, 'band')}")
    return tuple(band_of(entry) for entry in entries)


def gain_of(raw: int) -> float:
    return (raw - GAIN_ZERO) / STEPS_A_DB


def raw_gain(gain: object) -> int:
    """A gain in dB on the wire, to the nearest 0.05 dB; ValueError with the sentence for one
    outside GAIN_RANGE or not a number."""
    low, high = GAIN_RANGE
    if not number(gain) or not low <= gain <= high:
        raise ValueError(f"each gain is a number of dB from {low} to {high}, "
                         f"not {written(gain)}")
    return GAIN_ZERO + round(gain * STEPS_A_DB)


def bands_of(reply: bytes, start: int = 1) -> tuple[Band, ...]:
    """The ten bands from byte `start` of a reply, getCustomEQ's 1 and fn 5's 0;
    UnreadableAnswer for a reply too short, or a band with no frequency or a gain out of
    range."""
    want = start + BAND_COUNT * BAND_BYTES
    if len(reply) < want:
        raise UnreadableAnswer(f"the equalizer answered {counted(len(reply), 'byte')}, "
                               f"not {want}")
    bands = []
    for at in range(start, want, BAND_BYTES):
        freq, q = int.from_bytes(reply[at:at + 2], "big"), reply[at + 2]
        raw = int.from_bytes(reply[at + 3:at + 5], "big")
        if not freq or raw > GAIN_ZERO * 2:
            raise UnreadableAnswer(f"the equalizer answered a band {reply[at:at + 5].hex(' ')}, "
                                   "which Gearbench cannot read")
        bands.append(Band(freq, q, gain_of(raw)))
    return tuple(bands)


def written_bands(instance: int, slot: int, bands: tuple[Band, ...]) -> bytes:
    """setCustomEQ's parameters: the instance, the slot and 00, then each band, each gain
    checked first (raw_gain)."""
    params = bytes([instance, slot, 0])
    for band in bands:
        params += (band.freq.to_bytes(2, "big") + bytes([band.q])
                   + raw_gain(band.gain).to_bytes(2, "big"))
    return params
