"""G, M and MR key diversion over HID++.

These keys emit no evdev codes: nothing sees them until 0x8010 function 2 diverts them into
HID++ notifications. That one call diverts G, M and MR; 0x8020 and 0x8030 only drive the M and
MR LEDs, so only 0x8010 is required. The M and MR keys report on the indices of 0x8020 and
0x8030, though, so a keyboard without those is heard on its G keys alone. A headset's G keys
take the same call and report the same way, through its own channel (g432.G432), which
decodes them with `key_changes`.

The notifications, all at address 0x00, carry state, not edges, so presses and releases come
from diffing against the previous mask:

    GKEY  (0x8010)   data[0:4]   little-endian bitmask, bit 0 = G1
    MKEYS (0x8020)   data[0]     bitmask, bit 0 = M1
    MR    (0x8030)   data[0]     zero or non-zero
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from .hidpp import Event, Hidpp

F_GKEY = 0x8010
F_MKEYS = 0x8020
F_MR = 0x8030

GKEY_SET_DIVERT = 2  # enableSoftwareControl(1)
MKEYS_SET_LEDS = 1
MR_SET_LED = 0


@dataclass(frozen=True)
class KeyEvent:
    #: "G<n>" or "M<n>", n from 1 to the keyboard's count, or "MR"; a headset's G key by the
    #: name it binds under (g432.HeadsetModel.g_keys).
    key: str
    pressed: bool


class DivertedKeys:
    """Diversion state plus the M/MR LEDs; keeps each group's last mask to diff against."""

    def __init__(self, dev: Hidpp, counts: tuple[int, int] | None = None) -> None:
        """The keys on `dev`, their G and M counts read off it, or with `counts` an earlier
        DivertedKeys' `g_count` and `m_count` on the same device, nothing read: the caller
        vouches for the device being the same."""
        self.dev = dev
        self._gkey = dev.require(F_GKEY, "GKEY")
        self._mkeys = dev.feature(F_MKEYS)
        self._mr = dev.feature(F_MR)
        if counts is None:
            counts = (self.dev.request(self._gkey.index, 0)[0],
                      self.dev.request(self._mkeys.index, 0)[0] if self._mkeys is not None else 0)
        self.g_count, self.m_count = counts
        self._g_state = 0
        self._m_state = 0
        self._mr_state = False
        self._diverted = False

    # -- diversion -------------------------------------------------------

    @property
    def diverted(self) -> bool:
        return self._diverted

    def divert(self, enable: bool) -> None:
        """Turn diversion on or off, forgetting which keys were held.

        Off, the firmware has the keys; on again after a power cycle, the keyboard was off.
        A key remembered as down across either would make its next press no change.
        """
        self.dev.request(self._gkey.index, GKEY_SET_DIVERT, bytes([0x01 if enable else 0x00]))
        self._diverted = enable
        self._g_state = self._m_state = 0
        self._mr_state = False

    def restore(self) -> None:
        """Hand the keys back to the firmware: left diverted, they emit nothing."""
        self.divert(False)

    # -- LEDs ------------------------------------------------------------

    def set_m_leds(self, mask: int) -> None:
        """Light M-key LEDs from a bitmask, bit 0 = M1."""
        if self._mkeys is None:
            return
        self.dev.request(self._mkeys.index, MKEYS_SET_LEDS, bytes([mask]))

    def set_m_led(self, which: int) -> None:
        """Light exactly one M LED, 1-based. An M profile past the keyboard's own M keys has no
        LED: nothing is written."""
        if which > self.m_count:
            return
        self.set_m_leds(1 << (which - 1))

    def set_mr_led(self, on: bool) -> None:
        if self._mr is None:
            return
        self.dev.request(self._mr.index, MR_SET_LED, bytes([0x01 if on else 0x00]))

    def all_leds_off(self) -> None:
        self.set_m_leds(0)
        self.set_mr_led(False)

    # -- notifications ---------------------------------------------------

    def handles(self, event: Event) -> bool:
        return event.address == 0x00 and event.feature_index in {
            f.index for f in (self._gkey, self._mkeys, self._mr) if f is not None}

    def decode(self, event: Event) -> Iterator[KeyEvent]:
        """Key events from a notification `handles` claimed: none from one that changes nothing."""
        if event.feature_index == self._gkey.index:
            state = int.from_bytes(event.data[0:4], "little")
            yield from key_changes(numbered("G", self.g_count), self._g_state, state)
            self._g_state = state
        elif self._mkeys is not None and event.feature_index == self._mkeys.index:
            state = event.data[0]
            yield from key_changes(numbered("M", self.m_count), self._m_state, state)
            self._m_state = state
        elif self._mr is not None and event.feature_index == self._mr.index:
            state = bool(event.data[0])
            if state != self._mr_state:
                yield KeyEvent("MR", state)
            self._mr_state = state


def numbered(prefix: str, count: int) -> list[str]:
    """`count` keys' names from 1: "G1", "G2" and on."""
    return [f"{prefix}{number}" for number in range(1, count + 1)]


def key_changes(names: Sequence[str], old: int, new: int) -> Iterator[KeyEvent]:
    """A key event for each of `names`, the first at bit 0, whose bit differs between `old`
    and `new`, two masks of the keys held: down where `new` sets it. A bit past them is no
    key's."""
    changed = old ^ new
    for bit, name in enumerate(names):
        if changed >> bit & 1:
            yield KeyEvent(name, bool(new >> bit & 1))
