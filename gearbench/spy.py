"""0x8110 MouseButtonSpy: the mouse's buttons reported over HID++.

G HUB runs the G502 X PLUS in HOST mode with this feature armed, and sees every button as a
bit in a mask the mouse sends unprompted. The daemon does the same (Mice.arm_host,
Buttons.take_spy_mask, Mice.hand_back; checked on the hardware on 2026-09-25). Each fact is
marked measured on this machine, seen in a G HUB capture (times in seconds from its first
record), or inferred.

    fn 0  count      how many controls the mask covers: `0b` on the G502 X
                     PLUS, its eleven onboard entries (measured)
    fn 1  start      `00 00 00`, sent before fn 4; the masks come after both
                     (measured). What the three bytes mean is not known
    fn 2  stop       the masks stop (capture: after it, clicks came natively
                     only). The daemon's stop sends it and it is answered
                     (2026-09-25); the masks stopping has not been watched
                     from Linux
    fn 3  table      the table in force, byte i for control i. Before G HUB
                     wrote one the G502 X PLUS read `01` to `0b`, one byte per
                     control (capture, 12.7579)
    fn 4  set table  byte i nonzero: control i reports natively and in the
                     mask; zero: in the mask only (inferred: every nonzero byte
                     seen is i + 1)

    notification, fn 0 at address 0: data[0:2] is the mask, big-endian, and
    the control at onboard index i is bit `1 << i`

The bit rule is measured for G4 to G9 (G4 `0x0008`, onboard index 3) and seen in a capture
for the left button, `00 01` in the same millisecond as its native report (at 23.9507). Two
neighbors are little-endian: 0x8010's G-key mask (keys.py) and the mouse's input report on
endpoint 0x81 (`01 00` for that left button, at 23.9499).

With G HUB's table `01 02 03` the left, right and middle buttons report natively and in the
mask, and G4 to G9 in the mask only (measured). The wheel tilts report in the mask only too,
`00 40` and `00 80`, indices 6 and 7, with no report on endpoint 0x81 (capture, 20.16 to
26.68) and nothing on the mouse's evdev node (measured on Linux on 2026-09-25). A table with
`07 08` in those two slots makes them mouse buttons 7 and 8, BTN_BACK and BTN_TASK, not a
scroll (measured the same day), so the daemon keeps G HUB's table and makes the tilt's
scroll itself (`tilt`, Buttons.follow_tilt). Without fn 2 an armed spy goes on reporting after
the mouse is back in ONBOARD mode (capture: ONBOARD at 4.2780, a mask at 5.3109).
"""

from __future__ import annotations

from dataclasses import dataclass

from .hidpp import Hidpp
from .mouse import EXTRA_BUTTONS, G502X as G502X_MOUSE, MODEL_OF

F_SPY = 0x8110

FN_COUNT = 0
FN_START = 1
FN_STOP = 2
FN_TABLE = 3
FN_SET_TABLE = 4

#: What fn 1 is sent, as G HUB and the probe of 2026-09-24 sent it.
START = bytes(3)


def decode_mask(data: bytes) -> int:
    """The mask in a notification's data (Event.data): bytes 0 and 1,
    big-endian. `00 08` is G4 on the G502 X PLUS."""
    return int.from_bytes(data[0:2], "big")


def mask_bit(index: int) -> int:
    """The bit in the mask of the control at onboard index `index`, counted
    from 0 as `mouse.EXTRA_BUTTONS` counts: `1 << index`. Index 3, G4, is
    `0x0008`, not the `0x0010` that calling G4 "bit 4" would suggest."""
    return 1 << index


@dataclass(frozen=True)
class SpyModel:
    """What one model's spy is known to do: measured, not probed."""

    #: What fn 0 answers. Anything else is firmware this entry does not describe.
    count: int
    #: What fn 4 is sent: G HUB's table.
    table: bytes
    #: Button name -> its bit in the mask (mask_bit), for the buttons a binding can take: not
    #: left, right or the wheel click, native under `table`, nor the tilts, which no binding
    #: takes.
    buttons: dict[str, int]
    #: The wheel tilts' bits -> the way each scrolls: -1 left, +1 right, as
    #: REL_HWHEEL counts. They report in the mask only under `table`, and
    #: the daemon scrolls for them (tilt).
    tilts: dict[int, int]


_G502X = SpyModel(
    count=11,
    table=bytes([0x01, 0x02, 0x03]),
    buttons={name: mask_bit(index) for name, (index, _button) in EXTRA_BUTTONS.items()},
    # Onboard indices 6 and 7: found on Windows by giving each index its own button number,
    # and seen on Linux on 2026-09-25 (tilt left 0x0040, tilt right 0x0080).
    tilts={mask_bit(6): -1, mask_bit(7): +1},
)

#: The models HOST mode may drive, by product id: the G502 X PLUS, under each id mouse.MODEL_OF
#: gives it. On its receiver and on its cable (0xC095) the spy is at index 0x0c and G4 came as
#: its mask (measured 2026-09-26). The G305 has the spy at index 0x08 (count 6, its DPI button
#: 0x0020 under G HUB's table `01 02 03 04 05 00`, 2026-09-25) but stays in ONBOARD mode, where
#: that button works with or without Gearbench: the owner's decision of 2026-09-26.
SPY_MODELS: dict[int, SpyModel] = {product: _G502X for product, model in MODEL_OF.items()
                                   if model is G502X_MOUSE}


class ButtonSpy:
    """The five requests on one mouse's 0x8110.

    Each goes as a long report, zero padded: the probe of 2026-09-24 sent fn 0, 1, 3 and 4 so
    on this machine and each was answered (measured). G HUB sends all but fn 4 short, with the
    same bytes (capture); fn 2 went out long and was answered in the probes of 2026-09-25
    (measured). Each takes `Hidpp.request`'s `timeout` and `retries` and has no default for
    either: its caller names the budget, as the daemon does with MOUSE_PROBE.
    """

    def __init__(self, dev: Hidpp, index: int, model: SpyModel) -> None:
        self.dev = dev
        #: The feature's index, where its notifications arrive too: 0x0c on
        #: the G502 X PLUS through its receiver (capture) and on its cable
        #: (measured).
        self.index = index
        #: What this model's spy is known to do.
        self.model = model

    @classmethod
    def find(cls, dev: Hidpp, product: int) -> ButtonSpy | None:
        """The spy on `dev`, a `product`, or None where the model is not in SPY_MODELS, which
        is not asked, or the device has no 0x8110. Not looked up when a Mouse is made
        (Mice.find_spy); Hidpp keeps the answer for the handle's life. A failed lookup raises
        and keeps nothing."""
        model = SPY_MODELS.get(product)
        if model is None:
            return None
        found = dev.feature(F_SPY)
        return cls(dev, found.index, model) if found is not None else None

    def _ask(self, function: int, params: bytes, timeout: float, retries: int) -> bytes:
        return self.dev.request(self.index, function, params, long=True,
                                timeout=timeout, retries=retries)

    def count(self, *, timeout: float, retries: int) -> int:
        """fn 0: how many controls the mask covers."""
        return self._ask(FN_COUNT, b"", timeout, retries)[0]

    def table(self, *, timeout: float, retries: int) -> bytes:
        """fn 3: the table in force, all sixteen bytes as answered: what fn 4 sent, zeros
        after it, even after a stop and a switch to ONBOARD mode (capture, 18.6101 and
        47.2679)."""
        return self._ask(FN_TABLE, b"", timeout, retries)

    def start(self, *, timeout: float, retries: int) -> None:
        """fn 1 `00 00 00`: the masks start."""
        self._ask(FN_START, START, timeout, retries)

    def set_table(self, table: bytes, *, timeout: float, retries: int) -> None:
        """fn 4: byte i for control i, zero padded to sixteen, so a control
        past the end of `table` reports in the mask only, as G4 to G9 do under
        G HUB's `01 02 03` (measured)."""
        self._ask(FN_SET_TABLE, table, timeout, retries)

    def stop(self, *, timeout: float, retries: int) -> None:
        """fn 2: the masks stop. G HUB sends it three times on its way back to ONBOARD mode
        (capture, 46.1115 to 46.1632); whether once is enough is not measured."""
        self._ask(FN_STOP, b"", timeout, retries)


def edges(old: int, new: int, model: SpyModel) -> list[tuple[str, bool]]:
    """The presses and releases between two masks, as (button name, pressed).

    Releases come first, then presses, each in `model.buttons`' order: a roll
    from one button to another inside one mask lets go of the first before
    the second acts, as two masks would. A bit that is none of the model's
    buttons (left, right, the wheel click and tilts, bits 11 to 15) gives
    nothing, and so does a mask equal to the last.
    """
    changed = old ^ new
    moved = [(name, bool(new & bit)) for name, bit in model.buttons.items() if changed & bit]
    return [edge for edge in moved if not edge[1]] + [edge for edge in moved if edge[1]]


def tilt(mask: int, model: SpyModel) -> int:
    """The wheel tilt a mask holds: -1 left, +1 right, 0 neither. Both at
    once, which one wheel cannot do, cancel out."""
    return sum(way for bit, way in model.tilts.items() if mask & bit)
