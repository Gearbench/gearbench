"""0x060B, G HUB's FeatureC060BHeadsetTones: the tones the headset plays, read with
function 0 and set with function 1, one byte: 00 none, 01 all, 02 minimal, G HUB's three
choices, each write echoed by the function 0 event (the A50 X, version 1, G HUB's captures
of 2026-10-21, B4-11, where G HUB wrote each pick, read it back and wrote it again; it is
written once here).
"""

from __future__ import annotations

from .setting import Setting

ID = 0x060B
#: Singular, since the refusals that name a feature say "it" of it.
WORDS = "tones setting"
READ, WRITE = 0, 1


def setting() -> Setting:
    return Setting("tones", WORDS, ID, read=READ, at=0, write=WRITE, before=b"",
                   wire=(0x00, 0x01, 0x02), names=("none", "all", "minimal"))
