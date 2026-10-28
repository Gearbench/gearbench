"""A mouse's DPI and report rate, through whichever of two features each it has: 0x2201
ADJUSTABLE_DPI or 0x2202 EXTENDED_ADJUSTABLE_DPI, and 0x8060 REPORT_RATE or 0x8061
EXTENDED_ADJUSTABLE_REPORT_RATE. Mouse (mouse_device.py) holds one of each, found by
dpi_feature and rate_feature, and asks it alone.

The extended pair is the PRO X SUPERLIGHT 2's and its relatives'. No specification of either
is public; the functions are Solaar's (settings_templates.py, ExtendedAdjustableDpi and
ExtendedReportRate, and hidpp20.py, get_polling_rate, read 2026-10-21), each checked against
the traffic its users posted from a PRO X 2 (Solaar issues 2207 and 2631) and a PRO X 2 DEX
(issue 2904's log):

    0x2202 fn 1  capabilities of sensor 0: byte 2 bit 0, a Y DPI of its own; bit 1, a lift-off
                 distance. `00 05 0f` from both mice
         fn 2  sensor 0's list, `00 <direction> <page>`: bytes 3 to 15 of each page run on into
               the next, 16-bit values as 0x2201's list holds them, ended by 0x0000
         fn 5  sensor 0's DPI: X at bytes 1 and 2, Y at 5 and 6, the lift-off distance at 9
         fn 6  set it, `00 <X> <Y> <lift-off>`, answered with the same bytes
    0x8061 fn 1  the rates, bytes 0 and 1 big-endian, bit n EXTENDED_RATES[n]: `00 1f` up to
                 2000 Hz in 2024, `00 7f` up to 8000 Hz on later firmware
         fn 2  the rate in force, by its bit's number
         fn 3  set it, by its bit's number

Outside host mode the mouse refuses both writes: 0x2202 fn 6 with error 5 and 0x8061 fn 3 with
error 2 (issue 2207's log of 2024-03-29), and the users of issues 2819 and 2961 could set
neither until they switched the onboard profiles off. The DPI written is not kept across a
reboot (issue 3132, a PRO X2 SUPERSTRIKE's); that the rate written is not either is assumed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .hidpp import REQUEST_RETRIES, REQUEST_TIMEOUT, Feature, Hidpp
from .onboard import DPI_OUTSIDE, DPI_STAGES, dpi_fits, stage_dpi

F_DPI = 0x2201               # ADJUSTABLE_DPI
F_EXTENDED_DPI = 0x2202      # EXTENDED_ADJUSTABLE_DPI
F_REPORT_RATE = 0x8060
F_EXTENDED_RATE = 0x8061     # EXTENDED_ADJUSTABLE_REPORT_RATE

#: A list value whose top three bits are set is a range marker, its step in the rest.
RANGE_MARKER = 0xE000
#: 0x2202 fn 1's flags: a Y DPI apart from the X, and a lift-off distance (Solaar).
OWN_Y, LIFT_OFF = 0x01, 0x02
#: The pages of 0x2202's list read at most, whatever the list says: a device that never ends
#: it cannot set how many reads follow. The PRO X 2 DEX's ends on its fourth (issue 2904).
LIST_PAGES_MAX = 8
#: The bytes of a page that hold the list, after its sensor, direction and page.
PAGE_START, PAGE_END = 3, 16
#: 0x8061's rates in Hz, by bit: 8 ms, 4, 2 and 1 ms, then 500, 250 and 125 µs (Solaar's
#: ExtendedReportRate; issue 2904's log set bit 6 and read 125 µs back).
EXTENDED_RATES = (125, 250, 500, 1000, 2000, 4000, 8000)


class MouseError(RuntimeError):
    """What went wrong with a mouse, in words."""


@dataclass(frozen=True)
class DpiRange:
    """The DPI a sensor takes: `minimum` to `maximum` in steps of `step`, or, where `bands`
    holds any, each band in its own step, as 0x2202's list gives them."""

    minimum: int
    maximum: int
    step: int
    #: Each band's (lowest, highest, step), in order, the first starting at `minimum` and
    #: each next at the last one's end; where it is empty, `step` holds throughout.
    bands: tuple[tuple[int, int, int], ...] = ()

    def clamp(self, value: int) -> int:
        """`value` within the range, on the nearer step of the band it falls in, a tie
        going up."""
        value = max(self.minimum, min(self.maximum, value))
        low, high, step = next((band for band in self.bands if value <= band[1]),
                               (self.minimum, self.maximum, self.step))
        if step > 1:
            # In whole numbers: round() takes a tie to the even multiple, so two DPIs one
            # step apart could land on one.
            value = low + (value - low + step // 2) // step * step
        return min(value, high)

    @property
    def reply(self) -> list[int]:
        """The `mouse` reply's `dpi_range`: [minimum, maximum, step], or for bands whose steps
        differ [minimum, maximum, finest step, coarsest step]."""
        steps = [step for _low, _high, step in self.bands] or [self.step]
        finest, coarsest = min(steps), max(steps)
        return [self.minimum, self.maximum, finest] + ([coarsest] if coarsest != finest else [])


def reply_range(value: list[int] | None) -> tuple[int, int, list[int]] | None:
    """A `mouse` reply's `dpi_range` (DpiRange.reply) as (lowest, highest, steps), `steps`
    the one step or the finest and the coarsest; None where the reply gives none."""
    return (value[0], value[1], value[2:]) if value and len(value) in (3, 4) else None


def range_words(shown: tuple[int, int, list[int]]) -> str:
    """reply_range's DPI range in words, "100 to 25600, in steps of 50". A step of 0 takes
    any DPI, as a step of 1 does (DpiRange.clamp), so it is said as 1."""
    low, high, steps = shown
    said = dict.fromkeys(max(step, 1) for step in steps)
    return f"{low} to {high}, in steps of {' to '.join(map(str, said))}"


def stage_index(stages: list[int], wanted: int | None, previous: int | None,
                previous_index: int, what: str) -> int:
    """Which stage the default or the shift lands on. A `wanted` that is not a stage raises,
    since picking another would hide the mistake. `None`, which the window sends when it
    edits the stage in this slot, keeps the previous value if it is still a stage, else its
    position, clamped to the list."""
    if wanted is not None:
        if wanted not in stages:
            among = "the one stage" if len(stages) == 1 else "one of the stages"
            raise MouseError(f"{what} DPI {wanted} is not {among}, "
                             f"{', '.join(map(str, stages))}")
        return stages.index(wanted)
    if previous in stages:
        return stages.index(previous)
    return min(previous_index, len(stages) - 1)


def place_stages(stages: list[int], default: int | None, shift: int | None,
                 old: list[int], old_default: int, old_shift: int,
                 dpi_range: DpiRange | None) -> tuple[list[int], int, int]:
    """A new stage list, snapped to the sensor's step and put lowest first, and the indices its
    default and shift land on, for profile 1 and a game profile alike. `default` and `shift`
    are DPI values, or None to keep the old one's DPI if it is still a stage, else its index in
    `stages` as entered, clamped (stage_index); `old_default` and `old_shift` index `old`, an
    index past its end taken as its last stage, as stage_dpi shows it (the owner's decision of
    2026-10-11). A value outside the sensor's range raises MouseError: clamped, a typing slip
    would be written to flash as the range's end."""

    def snap[V: int | None](value: V) -> V | int:
        if not dpi_range or value is None:
            return value
        if not dpi_range.minimum <= value <= dpi_range.maximum:
            raise MouseError(f"DPI {value} is outside the sensor's range, "
                             f"{dpi_range.minimum} to {dpi_range.maximum}")
        return dpi_range.clamp(value)

    snapped = [snap(s) for s in stages]
    default, shift = snap(default), snap(shift)
    if not 1 <= len(snapped) <= DPI_STAGES:
        raise MouseError(f"1 to {DPI_STAGES} DPI stages, not {len(snapped)}")
    for value in snapped:
        if not dpi_fits(value):
            raise MouseError(DPI_OUTSIDE.format(value))
    if len(set(snapped)) != len(snapped):
        typed_twice = len(set(stages)) != len(stages)
        listed = ", ".join(map(str, stages if typed_twice else snapped))
        raise MouseError(f"DPI stages must be distinct; got {listed}"
                         f"{'' if typed_twice else ' after snapping'}")
    if old:
        # A byte naming an unused slot past the stages: the stage it is shown as keeps its
        # place, where the raw index would land on a stage the user never chose.
        old_default, old_shift = min(old_default, len(old) - 1), min(old_shift, len(old) - 1)
    was_default, was_shift = stage_dpi(old, old_default), stage_dpi(old, old_shift)
    at_default = stage_index(snapped, default, was_default, old_default, "default")
    at_shift = stage_index(snapped, shift, was_shift, old_shift, "shift")
    # Lowest first, since DPI up and down move by position (next_stage): the window sends a
    # stage added last. The default and the shift keep their DPI, not their place (the owner's
    # decision of 2026-10-25).
    ordered = sorted(snapped)
    return ordered, ordered.index(snapped[at_default]), ordered.index(snapped[at_shift])


def next_stage(landed: list[int], current: int, direction: int, wrap: bool) -> int:
    """The index Mouse.step_dpi moves to, up (+1) or down (-1) from the stage nearest
    `current`, `landed` each stage's DPI as the sensor takes it: at an end it stays, or with
    `wrap` goes round to the other end. A stage that lands on the nearest one's DPI is stepped
    over, since a game profile's own list is kept unsnapped where snapping would give one DPI
    twice (Dpi.tuning_for): a cycle would otherwise stop there."""
    nearest = min(range(len(landed)), key=lambda i: abs(landed[i] - current))
    target = nearest
    for step in range(nearest + direction, nearest + direction * (len(landed) + 1), direction):
        target = step % len(landed) if wrap else max(0, min(len(landed) - 1, step))
        if landed[target] != landed[nearest]:
            break
    return target


def dpi_range(reply: bytes) -> DpiRange | None:
    """Decode 0x2201's getSensorDpiList reply: 16-bit values after the sensor index, where one
    with the top bits 0xe000 is a range marker carrying the step, between the minimum and the
    maximum; None for a list with no usable value."""
    values = [int.from_bytes(reply[i:i + 2], "big") for i in range(1, 15, 2)]
    for i, value in enumerate(values):
        if value & RANGE_MARKER == RANGE_MARKER and i and i + 1 < len(values):
            return DpiRange(values[i - 1], values[i + 1], value & 0x1FFF)
    usable = [v for v in values if v and v & RANGE_MARKER != RANGE_MARKER]
    return DpiRange(min(usable), max(usable), 1) if usable else None


def banded_range(listing: bytes) -> DpiRange | None:
    """Decode 0x2202's list, its pages' bytes run together: each value a band's end, from the
    value before it in the step of the marker between them, or, with no marker, in one step to
    it. It ends at 0x0000 or at a value no higher than the last; None for a list with no value."""
    bands: list[tuple[int, int, int]] = []
    last = step = None
    for at in range(0, len(listing) - 1, 2):
        value = int.from_bytes(listing[at:at + 2], "big")
        if value & RANGE_MARKER == RANGE_MARKER:
            step = value & 0x1FFF
            continue
        if not value or (last is not None and value <= last):
            break
        if last is not None:
            bands.append((last, value, value - last if step is None else step))
        last, step = value, None
    if last is None:
        return None
    if not bands:
        return DpiRange(last, last, 1)
    return DpiRange(bands[0][0], last, bands[0][2], tuple(bands))


def list_ended(listing: bytes) -> bool:
    """Whether 0x2202's list read so far holds its end, a 0x0000 value."""
    return any(listing[at:at + 2] == bytes(2) for at in range(0, len(listing) - 1, 2))


def report_rates(bitmap: int) -> list[int]:
    """The rates in Hz, highest first, that 0x8060's getReportRateList bitmap of intervals
    lists: bit n set means n+1 ms is supported."""
    return [1000 // (bit + 1) for bit in range(8) if bitmap >> bit & 1]


def extended_bitmap(reply: bytes) -> int:
    """0x8061 fn 1's list as its bitmap, bytes 0 and 1 big-endian: bit n EXTENDED_RATES[n]."""
    return int.from_bytes(reply[0:2], "big")


def extended_rates(bitmap: int) -> list[int]:
    """The rates in Hz, highest first, that 0x8061's list holds (EXTENDED_RATES)."""
    return [hz for bit, hz in reversed(list(enumerate(EXTENDED_RATES))) if bitmap >> bit & 1]


def extended_rate(bit: int) -> int | None:
    """The rate in Hz of 0x8061's bit `bit`, None for a bit no source names."""
    return EXTENDED_RATES[bit] if bit < len(EXTENDED_RATES) else None


class SensorDpi(Protocol):
    """What Mouse asks of its DPI feature, either of the two."""

    #: Whether a write carries the stage's level byte, which flashes the stage's light.
    stage_level: bool
    #: Whether the mouse refuses a write outside host mode.
    host_only: bool

    def read_range(self) -> DpiRange | None: ...

    def get(self, timeout: float, retries: int) -> int: ...

    def set(self, value: int, level: int, timeout: float, retries: int) -> None: ...


class AdjustableDpi:
    """0x2201: sensor 0's list (fn 1), its DPI (fn 2), and a write with the stage's level
    byte (fn 3), which the mouse takes in either mode."""

    stage_level = True
    host_only = False

    def __init__(self, dev: Hidpp, feature: Feature) -> None:
        self.dev, self.index = dev, feature.index

    def read_range(self) -> DpiRange | None:
        return dpi_range(self.dev.request(self.index, 1, bytes([0x00]), long=True))

    def get(self, timeout: float, retries: int) -> int:
        return int.from_bytes(self.dev.request(self.index, 2, bytes([0x00]), timeout=timeout,
                                               retries=retries)[1:3], "big")

    def set(self, value: int, level: int, timeout: float, retries: int) -> None:
        self.dev.request(self.index, 3, bytes([0x00, value >> 8, value & 0xFF, level]),
                         long=True, timeout=timeout, retries=retries)


@dataclass(frozen=True)
class DpiParams:
    """0x2202 fn 5's answer for sensor 0."""

    x: int
    y: int
    lift_off: int


class ExtendedDpi:
    """0x2202 (the module's docstring): its capabilities and list, its DPI, and its write (set)."""

    stage_level = False
    host_only = True

    def __init__(self, dev: Hidpp, feature: Feature) -> None:
        self.dev, self.index = dev, feature.index
        #: fn 1's flags, once read_range has read them.
        self.flags = 0
        #: The lift-off distance as last read (fn 5), None before a read.
        self.lift_off: int | None = None

    def read_range(self) -> DpiRange | None:
        """The flags, then the X list, page by page until its end or LIST_PAGES_MAX."""
        self.flags = self.dev.request(self.index, 1, bytes([0x00]), long=True)[2]
        listing = b""
        for page in range(LIST_PAGES_MAX):
            listing += self.dev.request(self.index, 2, bytes([0x00, 0x00, page]),
                                        long=True)[PAGE_START:PAGE_END]
            if list_ended(listing):
                break
        return banded_range(listing)

    def params(self, timeout: float = REQUEST_TIMEOUT, retries: int = REQUEST_RETRIES) -> DpiParams:
        """fn 5, its lift-off distance kept for the next write. Sliced, as 0x2201's DPI is
        read: a reply cut short reads 0 where it ends."""
        reply = self.dev.request(self.index, 5, bytes([0x00]), long=True, timeout=timeout,
                                 retries=retries)
        found = DpiParams(int.from_bytes(reply[1:3], "big"), int.from_bytes(reply[5:7], "big"),
                          int.from_bytes(reply[9:10], "big"))
        self.lift_off = found.lift_off
        return found

    def get(self, timeout: float, retries: int) -> int:
        return self.params(timeout, retries).x

    def set(self, value: int, level: int, timeout: float, retries: int) -> None:
        """fn 6 for `value`; `level` has no byte here. Writes with a Y and a lift-off distance
        of 0 were refused with error 2 (issues 2207 and 2836), so a sensor with a Y of its own
        gets the X there too, and one with a lift-off distance the one read last, read first
        where none was."""
        y = value if self.flags & OWN_Y else 0
        lift_off = 0
        if self.flags & LIFT_OFF:
            lift_off = (self.lift_off if self.lift_off is not None
                        else self.params(timeout, retries).lift_off)
        self.dev.request(self.index, 6, bytes([0x00, *value.to_bytes(2, "big"),
                                               *y.to_bytes(2, "big"), lift_off]),
                         timeout=timeout, retries=retries)


class SensorRate(Protocol):
    """What Mouse asks of its report rate feature, either of the two."""

    def rates(self) -> list[int]:
        """Every rate the mouse lists, in Hz, highest first."""

    def get(self) -> int | None: ...

    def set_live(self, hz: int, timeout: float, retries: int) -> None: ...


class ReportRate:
    """0x8060: the intervals it lists (fn 0), the one in force (fn 1) and a live write (fn 2),
    in milliseconds."""

    def __init__(self, dev: Hidpp, feature: Feature) -> None:
        self.dev, self.index = dev, feature.index

    def rates(self) -> list[int]:
        return report_rates(self.dev.request(self.index, 0)[0])

    def get(self) -> int:
        return 1000 // max(self.dev.request(self.index, 1)[0], 1)

    def set_live(self, hz: int, timeout: float, retries: int) -> None:
        self.dev.request(self.index, 2, bytes([1000 // hz]), timeout=timeout, retries=retries)


class ExtendedReportRate:
    """0x8061 (the module's docstring), each rate by its bit in EXTENDED_RATES."""

    def __init__(self, dev: Hidpp, feature: Feature) -> None:
        self.dev, self.index = dev, feature.index

    def rates(self) -> list[int]:
        return extended_rates(extended_bitmap(self.dev.request(self.index, 1)))

    def get(self) -> int | None:
        """The rate in force, or None for a bit no source names."""
        return extended_rate(self.dev.request(self.index, 2)[0])

    def set_live(self, hz: int, timeout: float, retries: int) -> None:
        self.dev.request(self.index, 3, bytes([EXTENDED_RATES.index(hz)]), timeout=timeout,
                         retries=retries)


def dpi_feature(dev: Hidpp) -> SensorDpi | None:
    """The mouse's DPI feature, 0x2201 where it has it, else 0x2202, else None; looked up,
    nothing read."""
    found = dev.feature(F_DPI)
    if found is not None:
        return AdjustableDpi(dev, found)
    extended = dev.feature(F_EXTENDED_DPI)
    return None if extended is None else ExtendedDpi(dev, extended)


def rate_feature(dev: Hidpp) -> SensorRate | None:
    """The mouse's report rate feature, 0x8060 where it has it, else 0x8061, else None."""
    found = dev.feature(F_REPORT_RATE)
    if found is not None:
        return ReportRate(dev, found)
    extended = dev.feature(F_EXTENDED_RATE)
    return None if extended is None else ExtendedReportRate(dev, extended)
