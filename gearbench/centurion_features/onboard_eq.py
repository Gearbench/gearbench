"""0x0636, G HUB's FeatureC0636OnboardEq (Solaar's HEADSET_ONBOARD_EQ): the equalizer the
PRO X 2 LIGHTSPEED keeps in itself. Read with getEQParameters of a slot: the slot, the count
of bands, then each band's frequency in Hz (two bytes, big-endian), its gain in whole dB (one
signed byte) and its Q (one byte), as Solaar's get_onboard_eq_params reads it, -12 to 12 dB
(its headset-onboard-eq). Its write carries the bands and their filters' coefficients for the
headset's DSP (Solaar's and HeadsetControl's), and Solaar writes a second slot, 0x80, "so EQ
survives power cycle": Gearbench reads it alone. A preset of the library on the computer
holds the five bands in that format (bands_from), for the day its write is tested.
"""

from __future__ import annotations

from ..hidpp import UnreadableAnswer
from ..wording import counted, given_count, number, whole, written
from .equalizer import Band, fields_of

ID = 0x0636
WORDS = "onboard equalizer"
FN_GET = 1
#: The slot the read asks, the one the headset plays (Solaar's slot 0x00).
SLOT = 0x00
GAIN_RANGE = (-12, 12)
BAND_BYTES = 4
#: The five bands' frequencies in Hz, fixed (Solaar's listing of one; G HUB shows the same
#: five, HeadsetControl's driver), and their Q byte, 1: HeadsetControl writes it and refuses
#: any other ("This headset uses a fixed Q factor of 1.0"). What the headset reads back as
#: its Q is not published.
FREQUENCIES = (80, 240, 750, 2200, 6600)
Q = 1


def bands_of(reply: bytes) -> tuple[Band, ...]:
    """getEQParameters' bands, each with its Q byte and its gain; UnreadableAnswer for a
    reply too short for the count it gives, a count of no bands, a band with no frequency or
    a gain out of GAIN_RANGE."""
    count = reply[1] if len(reply) >= 2 else 0
    if len(reply) < 2 + count * BAND_BYTES:
        raise UnreadableAnswer(f"the onboard equalizer answered {counted(len(reply), 'byte')}, "
                               "fewer than its bands take")
    if not count:
        raise UnreadableAnswer("the onboard equalizer answered no bands")
    bands = []
    low, high = GAIN_RANGE
    for at in range(2, 2 + count * BAND_BYTES, BAND_BYTES):
        freq = int.from_bytes(reply[at:at + 2], "big")
        gain = int.from_bytes(reply[at + 2:at + 3], "big", signed=True)
        if not freq or not low <= gain <= high:
            raise UnreadableAnswer(f"the onboard equalizer answered a band "
                                   f"{reply[at:at + BAND_BYTES].hex(' ')}, which Gearbench "
                                   "cannot read")
        bands.append(Band(freq, reply[at + 3], float(gain)))
    return tuple(bands)


def bands_from(entries: object) -> tuple[Band, ...]:
    """The five bands of `entries`, a list of objects of a band's `freq`, `q` and `gain` as
    a request or a file gives them: each at its frequency of FREQUENCIES, in their order,
    with the Q byte Q and a whole dB in GAIN_RANGE. ValueError with the sentence for any
    other."""
    if not isinstance(entries, list) or len(entries) != len(FREQUENCIES):
        raise ValueError(f"the {WORDS} takes {counted(len(FREQUENCIES), 'band')}, "
                         f"not {given_count(entries, 'band')}")
    low, high = GAIN_RANGE
    bands = []
    for at, (entry, want) in enumerate(zip(entries, FREQUENCIES, strict=True), 1):
        freq, q, gain = fields_of(entry)
        if not whole(freq) or freq != want:
            raise ValueError(f"band {at} of the {WORDS} is at {want} Hz, not {written(freq)}")
        if not whole(q) or q != Q:
            raise ValueError(f"a band's Q on the {WORDS} is {Q}, not {written(q)}")
        # A file keeps a gain as a float, -6.0, and a flat one can be -0.0, which int() makes
        # the 0.0 the ten bands' reader gives. The range is checked first: float() of a JSON
        # integer too long for a float overflows.
        if not number(gain) or not low <= gain <= high or not float(gain).is_integer():
            raise ValueError(f"each gain on the {WORDS} is a whole number of dB from {low} to "
                             f"{high}, not {written(gain)}")
        bands.append(Band(freq, q, float(int(gain))))
    return tuple(bands)
