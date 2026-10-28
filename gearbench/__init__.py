"""Gearbench: a Linux control panel for Logitech G keyboards, mice and headsets, and the
Astro A50 headsets.

Only the ids in `keyboard.PRODUCT_IDS`, `mouse.PRODUCT_IDS`, `g432.PRODUCTS`,
`a50x.PRODUCTS` and `astro.PRODUCTS` attach. A model is added once it is tested on the
hardware, but for the wider-devices trial's, which attach before their test: for reads and
volatile writes, and for a keyboard's brightness and a G432 relative's sidetone, which the
device may keep, with no onboard profile written; and for reads alone on a Centurion headset
(gearbench.a50x) whose writes are not tested. The HID++ devices' abilities are probed from
their feature tables; the A50 Gen 4's base station speaks its own protocol, not HID++
(gearbench.astro). The G432's adapter keeps the sidetone level it is sent through an unplug
(gearbench.g432).

`gearbench.daemon` owns the devices and holds the session's lighting claim;
`gearbench.cli` and `gearbench.gui` talk to it over a unix socket and, but for
`gearbench-ctl probe` and `gearbench-ctl report`, never touch hardware themselves.
"""

__version__ = "1.2.1"
