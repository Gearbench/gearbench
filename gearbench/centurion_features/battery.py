"""0x0104 BatterySOC, G HUB's name for it (Solaar's CENTURION_BATTERY_SOC): the headset's
battery, read with getBatteryStatus, function 0, and sent as the feature's function 0
event when it changes, in the same layout: a percent, a second percent, and a status
(the A50 X's reads and events, 2026-10-21; HeadsetControl PR 531's push of the A50 Gen 5's
`02 0c 06 00 06 00 <percent> <level> <charging>`; the G325's `5a 5a 00` and `59 5a 02`,
HeadsetControl issue 581; Solaar's decipher_battery_centurion).

The status is 0x1004's (battery.unified): 0 on battery, 1 and 2 charging, 3 full by
Solaar's reading, which the A50 X answered docked and charging at 85 % in G HUB's captures.
The first percent is the one read; the G325's owner found G HUB showing the second, 90 %
where the first was 89 (issue 581), and the A50 X's base answered `52 55` where G HUB
showed 85 %. Which is the one to show waits on a first connect.
"""

from __future__ import annotations

from ..battery import unified
from .setting import unreadable

ID = 0x0104
WORDS = "battery"
READ = 0
LENGTH = 3


def of(reply: bytes) -> dict:
    """getBatteryStatus's reply, or an event's, in battery.read's shape; UnreadableAnswer for
    one too short or a percent over 100."""
    if len(reply) < LENGTH or reply[0] > 100:
        raise unreadable(WORDS, reply[:LENGTH])
    return unified(reply)
