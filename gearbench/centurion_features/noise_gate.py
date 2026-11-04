"""0x0801 MixerMic, G HUB's FeatureC0801MixerMic: the microphone's noise gate, read with
function 1, the value first, and set with setNoiseGate, function 2 (the A50 X, measured
against G HUB, 2026-10-21; its function 3 lists the four values). Each model's
record gives the bytes of its four choices.
"""

from __future__ import annotations

from .setting import Setting

ID = 0x0801
WORDS = "noise gate"
READ, WRITE = 1, 2
#: Its key, the socket's word for it.
KEY = "noise_gate"
#: G HUB's order of the four choices (its Microphone page, 2026-10-21).
NAMES = ("off", "night", "home", "tournament")


def setting(wire: tuple[int, ...]) -> Setting:
    """The noise gate, NAMES' values on the wire in `wire`."""
    return Setting(KEY, WORDS, ID, read=READ, at=0, write=WRITE, before=b"",
                   wire=wire, names=NAMES)
