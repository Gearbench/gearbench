"""FN held on a keyboard of the G515 generation, over 0x8051 LogiModifiers: the write that
turns its reports on, and their decode.

From G HUB 39.1.2's USB traffic with a wired G515 TKL, firmware U1 67.01.B0008 (captured
2026-10-21). The keyboard reports nothing for FN alone until a host writes fn 3 with
`03 ff`, as G HUB does at its takeover; that write alone turned the reports on with G HUB
quit, and a replug turned them off again. Then each change of the modifiers held comes as
event 0, whose first two bytes are a big-endian mask: bit 0x0100 is FN and the low byte the
HID modifier byte, so a Shift press comes too; 0x0200 is likely G-Shift. The keys FN
combines with did the same with the reports on or off: the keyboard carries out its own FN
functions. The G915 X LIGHTSPEED reported FN the same way on 2026-10-23, and the
generation's other keyboards with FN keys to light are taken to, each at its own index.
"""

from __future__ import annotations

from .hidpp import Event, Hidpp

F_MODIFIERS = 0x8051
#: fn 3, which takes the mask of the modifiers to report, two bytes big-endian; its reply is
#: empty.
SET_REPORTED = 3
#: The mask fn 3 is given: G HUB's `03 ff`, fn 0's first two bytes on the G515 TKL (its reply
#: `03 ff 03`): the eight HID modifiers, FN and 0x0200.
REPORTED = 0x03FF
#: FN's bit in event 0's mask.
FN_BIT = 0x0100
#: Event 0's address: the event number in the high nibble, software id 0.
MODIFIERS_EVENT = 0x00


class Modifiers:
    """A keyboard's 0x8051, and whether FN is held by the last report it sent."""

    def __init__(self, dev: Hidpp) -> None:
        self.dev = dev
        self._feature = dev.require(F_MODIFIERS, "LOGI_MODIFIERS")
        #: Whether FN is held, by the last report; False until one says it is.
        self.fn_held = False

    def report(self) -> None:
        """fn 3 `03 ff`: the keyboard reports its modifiers, FN among them, until it is
        switched off, as unplugging a keyboard with no battery does: the G915 X LIGHTSPEED, on
        its battery, kept reporting through a replug of its cable and stopped when switched off
        (Windows captures G4b and G5c, 2026-10-23). FN is taken as let go, as a keyboard set up
        again after a power cycle has it."""
        self.dev.request(self._feature.index, SET_REPORTED, REPORTED.to_bytes(2, "big"))
        self.fn_held = False

    def handles(self, event: Event) -> bool:
        return event.feature_index == self._feature.index and event.address == MODIFIERS_EVENT

    def take(self, event: Event) -> bool | None:
        """Whether FN is held after a report `handles` claimed, or None where FN is as it was:
        another modifier moved."""
        held = bool(int.from_bytes(event.data[:2], "big") & FN_BIT)
        if held == self.fn_held:
            return None
        self.fn_held = held
        return held

    def let_go(self) -> bool:
        """FN taken as let go with no report, where its release cannot come; whether it was
        held."""
        held, self.fn_held = self.fn_held, False
        return held
