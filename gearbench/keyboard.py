"""Which keyboard is attached, and what it can do.

Capabilities are probed, as the mouse's are: the three Logitech devices use three battery
features and two lighting features between them, so what answers matters more than the
model. The G915 is the only keyboard measured; discovery attaches only `PRODUCT_IDS`, and
a model is added there once it is tested on the hardware.
"""

from __future__ import annotations

from pathlib import Path

from .hidpp import Hidpp, find_attached
from .keys import F_GKEY, F_MKEYS, F_MR
from .lighting import F_BRIGHTNESS, F_PERKEY
from .onboard import F_ONBOARD

#: Every keyboard this build looks for: the G915 is 0xC33E cabled and 0xC541 through its
#: LIGHTSPEED receiver (read off the hardware 2026-09-22).
PRODUCT_IDS = (0xC33E, 0xC541)

#: The ids a keyboard reports on its own cable; the others are its receiver's. They decide
#: the route, never the interface (both use VENDOR_INTERFACE): cabled, the keyboard is on
#: external power and its battery is not asked; on the receiver the link notices are
#: turned on and the cable is looked for by these ids.
CABLED_IDS = frozenset({0xC33E})

#: The vendor HID++ collection's interface, on the cable and the c541 receiver alike; 00 and
#: 01 carry keystrokes and a pointer and are never opened. The c541 gives the keyboard no
#: node of its own (udev/70-g915.rules).
VENDOR_INTERFACE = 2

MODELS = {
    0xC33E: "G915",
    0xC541: "G915",
}

#: A keyboard has GKEY and a receiver does not, which is what
#: `hidpp.open_device` uses to tell them apart on a receiver's own node.
WITNESS = F_GKEY

#: Capability to the feature that provides it, in the order front ends list them.
CAPABILITIES = (
    ("g_keys", F_GKEY),
    ("m_keys", F_MKEYS),
    ("mr", F_MR),
    ("brightness", F_BRIGHTNESS),
    ("per_key", F_PERKEY),
    ("onboard", F_ONBOARD),
)


def name(product: int | None) -> str:
    return MODELS[product] if product else "keyboard"


def find_attached_keyboard(cabled_only: bool = False) -> tuple[Path, int, int] | None:
    """The keyboard's vendor node as (node, product, index): on its cable first, then its
    receiver, or with `cabled_only` on its cable alone. From sysfs; nothing is opened."""
    return find_attached(product=sorted(CABLED_IDS) if cabled_only else PRODUCT_IDS,
                         interface=VENDOR_INTERFACE)


def capabilities(dev: Hidpp) -> list[str]:
    """What this keyboard can do, from its own feature table.

    Not lighting: a device can advertise the feature with nothing behind it, so the
    daemon adds `lighting` once clusters enumerate.
    """
    return [able for able, feature in CAPABILITIES if dev.feature(feature) is not None]
