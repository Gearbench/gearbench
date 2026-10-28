"""0x0115, G HUB's EU power mode (hasEuPowerModeControl; Solaar's
CENTURION_EU_POWER_MODE), which G HUB's A50 X page calls Deep sleep mode: "When Deep Sleep
mode is enabled, base station will go to sleep after 15mins of inactivity" (its tooltip).
Read with function 0 and set with function 1, one byte, 01 on and 00 off, each write echoed
by the function 0 event (the A50 X, G HUB's captures of 2026-10-21, B4-09).
"""

from __future__ import annotations

from .setting import SWITCH, Setting

ID = 0x0115
WORDS = "deep sleep mode"
READ, WRITE = 0, 1


def setting() -> Setting:
    return Setting("deep_sleep", WORDS, ID, read=READ, at=0, write=WRITE,
                   before=b"", wire=(0x00, 0x01), names=SWITCH)
