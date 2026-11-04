"""0x0110, G HUB's FeatureC0110LEDBrightness: the brightness of a light, on the A50 X its
base station's, read with function 0 and set with function 1, one byte, the percent, 0 to
100 (G HUB's slider wrote 0x32 for 50, 00 and 0x64 for 100 to the A50 X on
2026-10-21). Its write is echoed by 0x0004's event, not by an event of its own, so the
value is read again after a write.
"""

from __future__ import annotations

from .setting import Setting

ID = 0x0110
WORDS = "brightness"
READ, WRITE = 0, 1
#: Its key, the socket's word for it.
KEY = "brightness"


def setting(words: str) -> Setting:
    """The brightness of the light `words` names: "base station's brightness"."""
    return Setting(KEY, words, ID, read=READ, at=0, write=WRITE, before=b"",
                   wire=tuple(range(101)))
