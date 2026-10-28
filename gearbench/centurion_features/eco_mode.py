"""0x0618, Solaar's HEADSET_BATTERY_SAVER, which G HUB's records call the battery's eco mode
(hasBatteryEcoMode, on the G522 and the G325): on or off, the first byte of function 0's
read, set with function 1, as Solaar's headset-eco-mode reads and writes it (the G325's log
of 2026-05-16 read `01 3d` and `01 5f` at index 18, version 1, Solaar showing Eco Mode on;
the second byte, which changed between the reads, is not read). Solaar's note: the G522
refuses a write of the value it already holds with error 0x0b.
"""

from __future__ import annotations

from .setting import SWITCH, Setting

ID = 0x0618
WORDS = "eco mode"
READ, WRITE = 0, 1


def setting() -> Setting:
    return Setting("eco_mode", WORDS, ID, read=READ, at=0, write=WRITE, before=b"",
                   wire=(0x00, 0x01), names=SWITCH)
