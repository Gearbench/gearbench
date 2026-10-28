"""0x0609 HeadetMix (sic), G HUB's name for it: the Game and Voice balance, read with
function 0 and set with function 1, one byte, 0 all Voice to 12 all Game; the headset's
dial moves the same value and sends it as the function 0 event (the A50 X, G HUB's
captures of 2026-10-21, B4-03 and B5; PR 531 on the A50 Gen 5).
"""

from __future__ import annotations

from .setting import Setting

ID = 0x0609
WORDS = "Game and Voice balance"
READ, WRITE = 0, 1
#: Its key, and its values: 0 all Voice to 12 all Game.
KEY = "balance"
WIRE = tuple(range(13))


def setting() -> Setting:
    return Setting(KEY, WORDS, ID, read=READ, at=0, write=WRITE, before=b"", wire=WIRE)


def game_share(value: int) -> int:
    """The Game share of a balance, in whole percent; Voice has the rest."""
    return round(value * 100 / WIRE[-1])
