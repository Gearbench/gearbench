"""Gearbench: a Linux control panel for the Logitech G915 keyboard, the G502 X PLUS
and G305 mice, and the Astro A50 Gen 4 headset.

Only the G915 keyboard, the G502 X PLUS and the G305 attach, by the ids in
`keyboard.PRODUCT_IDS` and `mouse.PRODUCT_IDS`; a model is added once it is tested on
the hardware. Each one's abilities are probed from its feature table. The A50 Gen 4's
base station speaks its own protocol, not HID++ (gearbench.astro).

`gearbench.daemon` owns the devices and holds the session's lighting claim;
`gearbench.cli` and `gearbench.gui` talk to it over a unix socket and, but for
`gearbench-ctl probe`, never touch hardware themselves.
"""

__version__ = "1.0.0"
