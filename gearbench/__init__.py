"""Gearbench: a Linux control panel for Logitech G keyboards, mice and headsets, and the
Astro A50 Gen 4 headset.

Only the ids in `keyboard.PRODUCT_IDS`, `mouse.PRODUCT_IDS`, `g432.PRODUCTS` and the A50's
attach. A model is added once it is tested on the hardware, but for the wider-devices
trial's, which attach before their test, for reads and volatile writes, with no flash write
sent. The HID++ devices' abilities are probed from their feature tables; the A50 Gen 4's
base station speaks its own protocol, not HID++ (gearbench.astro). The G432's adapter keeps
the sidetone level it is sent through an unplug (gearbench.g432).

`gearbench.daemon` owns the devices and holds the session's lighting claim;
`gearbench.cli` and `gearbench.gui` talk to it over a unix socket and, but for
`gearbench-ctl probe` and `gearbench-ctl report`, never touch hardware themselves.
"""

__version__ = "1.1.0"
