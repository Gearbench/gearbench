"""0x0602, Solaar's HEADSET_MIC_SNR, which G HUB's records call onboard noise reduction
(hasCenturionOnboardNoiseReduction, on the PRO X 2 LIGHTSPEED and the G325): the
microphone's noise suppression, as every view calls it, the G432 family's on the computer
too; on or off, read with function 0 and set with function 1, one byte, as Solaar's
headset-mic-snr reads and writes it (its PRO X 2 LIGHTSPEED listing gave `Mic SNR: True`, and
the G325's log of 2026-05-16 read `00` at index 13, version 1). The socket keeps its word,
noise_reduction.
"""

from __future__ import annotations

from .setting import SWITCH, Setting

ID = 0x0602
WORDS = "noise suppression"
READ, WRITE = 0, 1


def setting() -> Setting:
    return Setting("noise_reduction", WORDS, ID, read=READ, at=0, write=WRITE,
                   before=b"", wire=(0x00, 0x01), names=SWITCH)
