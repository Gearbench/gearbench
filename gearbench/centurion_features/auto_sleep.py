"""0x0108, G HUB's autoSleep (Solaar's CENTURION_AUTO_SLEEP): how long the headset waits,
idle, before it sleeps, read with function 0 and set with function 1, in minutes, 0 for
never.

Version 3, the A50 X's, measured on it (G HUB's captures of 2026-10-21, B4-08): the read
and the event answer `3c MM`, MM the minutes, and G HUB wrote `3c 00` for Never, then `3c
0f`, `3c 1e` and `3c 3c` for its 15, 30 and 60 minutes, the first byte 0x3c in each, which
is sent as G HUB sent it (Solaar's _AutoSleepRangeValidator keeps that byte as read).
G HUB offers those four choices alone, and they are the values the A50 X takes here.

Version 4, the G522's, three bytes, the minutes first, then the times after which its lights
dim and go off (HeadsetControl's G522 driver, which reads them and writes them back with the
minutes, "the headset rejects a write without them"; Solaar's _AutoSleepRangeValidator
keeps them as read too). Versions 1 and 2 are the minutes alone, one byte (Solaar's; the
G325's version 2 read `1e`, 30 minutes, in its log of 2026-05-16, and HeadsetControl writes
the PRO X 2 LIGHTSPEED's so).
"""

from __future__ import annotations

from .setting import Setting

ID = 0x0108
WORDS = "auto sleep"
READ, WRITE = 0, 1
#: The byte before the minutes in each of G HUB's writes to the A50 X.
A50_X_FIRST = 0x3C


#: The bytes version 4's write sends back from its read: the minutes and the two lights'
#: times.
KEPT = 3


def setting(minutes: tuple[int, ...], version: int) -> Setting:
    """The auto sleep in version `version`'s layout, which takes `minutes`."""
    at, before = (1, bytes([A50_X_FIRST])) if version == 3 else (0, b"")
    return Setting("auto_sleep", "auto sleep time", ID, read=READ, at=at, write=WRITE,
                   before=before, wire=minutes, kept=KEPT if version >= 4 else 0,
                   unit="minute", never="never")
