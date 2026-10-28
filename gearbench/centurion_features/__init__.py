"""The Centurion features Gearbench drives, one module each, by G HUB's feature id: what a
feature's functions are, the layouts of its replies and requests as the A50 X measured them
and as the sources give other versions of them, and the settings a model's version of it
builds. A headset's record (gearbench.a50x) lists the features it has, each at the version
whose layout it takes. Nothing here knows a model or sends a request.
"""

from __future__ import annotations

from . import (
    auto_sleep,
    battery,
    eco_mode,
    equalizer,
    led_brightness,
    mix,
    mixer,
    noise_gate,
    noise_reduction,
    onboard_eq,
    power_mode,
    sidetone,
    tones,
    volume,
)

#: Each feature's words, by its id, for a refusal that names one a headset did not list.
WORDS = {feature.ID: feature.WORDS
         for feature in (battery, sidetone, volume, mix, noise_gate, equalizer, auto_sleep,
                         power_mode, led_brightness, tones, mixer, noise_reduction,
                         onboard_eq, eco_mode)}
