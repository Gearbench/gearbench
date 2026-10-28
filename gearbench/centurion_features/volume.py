"""0x0200 iVolumeSet, G HUB's name for it: the headset's own volume, read with function 0
and set with function 1. On the A50 X, measured (G HUB's captures of 2026-10-21, B4-02, and
the volume wheel's events): the read and the event answer a second scale of the level, 00
and the level, `10 00 0b` at 11; the write takes 0xFF and the level, 0 to 21, the wheel's
22 steps (PR 531's notes on the A50 Gen 5 say the same).
"""

from __future__ import annotations

from .setting import Setting

ID = 0x0200
WORDS = "volume"
READ, WRITE = 0, 1


def setting() -> Setting:
    return Setting("volume", WORDS, ID, read=READ, at=2, write=WRITE, before=b"\xff",
                   wire=tuple(range(22)))
