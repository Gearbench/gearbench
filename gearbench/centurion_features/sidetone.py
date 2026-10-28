"""0x0604 HeadsetAudioSidetone, G HUB's FeatureC0604HeadsetSidetone: the microphone's level
in the earcups, read with getSidetoneLevel, function 0, and set with setSidetoneLevel,
function 1.

Version 2, the A50 X's, measured on it (G HUB's captures of 2026-10-21, B4-01): the read
answers a count of microphones, a microphone's id, 0xFF and the level, `01 01 ff 03`, and
the write takes the id, 0xFF and the level, `01 ff 06`; function 2 answers the level count
last, `ee 00 07` for 0 to 6 (HeadsetControl PR 531 sends the A50 Gen 5's the same; the G522's
and the G325's list version 2, HeadsetControl's G522 driver writes `01 ff <level>` and reads
the level at byte 3, and the G325's function 2 answered `e8 09 0a`, ten levels, in its Solaar
log). Version 1, the PRO X 2 LIGHTSPEED's: Solaar's headset-sidetone reads `<count> <id>
<level>` and writes `<id> <level>` for a version below 2, as HeadsetControl does for a read
of three bytes, and Solaar's listing of a PRO X 2 read its level, 55, so.
"""

from __future__ import annotations

from .setting import Setting

ID = 0x0604
WORDS = "sidetone"
READ, WRITE = 0, 1
#: The microphone's id the writes name: 1 on the A50 X, and in both drivers.
MICROPHONE = 0x01


def setting(levels: int, version: int) -> Setting:
    """The sidetone of a model with `levels` levels, 0 up, in version `version`'s layout."""
    at, before = (3, bytes([MICROPHONE, 0xFF])) if version >= 2 else (2, bytes([MICROPHONE]))
    return Setting("sidetone", "sidetone level", ID, read=READ, at=at, write=WRITE,
                   before=before, wire=tuple(range(levels)))
