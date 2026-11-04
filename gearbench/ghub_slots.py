"""G HUB's slots of the mice: each mouse model by its slot prefix in G HUB's device database
(MICE), which of them a profile's slots name (mice_in), and each model's buttons and other
controls by G HUB's slot (mouse_slots, fixed_slots) and by what the preview calls them
(button_label, mouse_where, and SHIFTED on the G-Shift layer). ghub.py maps a settings
document through them, and ghub_plan.py's preview names the buttons with them."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from .mouse import MODEL_OF, ButtonTable, MouseModel

#: A profile's slot as `ghub._slot` reads its id: (device, control, bank, shifted).
Slot = tuple[str, str, int, bool]

#: What follows where a key or a mouse control is on the base layer, to name where it is on the
#: G-Shift layer: "G1 with G-Shift".
SHIFTED = " with G-Shift"

#: Each mouse model with buttons to bind, by its slotPrefix in G HUB's device database (read
#: 2026-10-18), from one of its product ids. Only the G502 X PLUS's slots have been read from
#: a settings file.
MICE = {device: MODEL_OF[product] for device, product in (
    ("g502x-plus", 0x4099), ("g502hero", 0xC08B), ("g502x-lightspeed", 0x409F),
    ("proxwirelessmouse", 0x4093), ("g502x", 0xC099), ("g502x-wl", 0x40B2),
    ("g502wireless", 0x407F), ("prowirelessmouse", 0x4079), ("g903hero", 0x4087),
    ("prox2wirelessmouse", 0x40A9), ("proxrwirelessmouse", 0x40B8),
    ("prox2compactwirelessmouse", 0x40B7),
    ("prox2superstrikewirelessmouse", 0x40BD),
    ("g102g203", 0xC092))}


def mice_in(slots: Mapping[Slot, object]) -> list[tuple[str, MouseModel]]:
    """The mice of MICE whose buttons a profile's import reads, by G HUB's slot prefix: each
    the profile has a slot of, so a mouse G HUB has nothing for keeps an M profile's own."""
    return [(device, model) for device, model in MICE.items()
            if any(slot[0] == device for slot in slots)]


def mouse_slots(model: MouseModel) -> dict[str, str]:
    """G HUB's slots of a mouse model's buttons, to the names Gearbench binds them under. G
    HUB's slot gN is the button entry N - 1 of the onboard profile (ButtonSlot.index): so the
    G502 X PLUS's settings file has it, its paddle at index 4 being G HUB's g5 and Gearbench's
    G6, and so the G502 HERO's device package draws it. Taken for every model's table."""
    return {f"g{slot.index + 1}": slot.name
            for slot in cast(ButtonTable, model.button_table).buttons}


def fixed_slots(model: MouseModel) -> dict[str, tuple[str, str]]:
    """A mouse model's other controls by G HUB's slot, which Gearbench does not rebind, with
    what each is and the card it holds out of the box: the three main buttons, the wheel
    tilts and a profile button at any entry past them no button or tilt holds, each at its
    entry's slot (mouse_slots' rule), and the wheel. Holding that card loses nothing;
    anything else is listed as not imported."""
    table = cast(ButtonTable, model.button_table)
    tilts = {f"g{index + 1}": ("wheel tilt left", "040b") if way < 0
             else ("wheel tilt right", "040c")
             for index, way in table.tilts}
    held = {slot.index for slot in table.buttons} | {index for index, _way in table.tilts}
    # Such an entry is the G903 LIGHTSPEED HERO's profile cycle at 11. Its card out of the box
    # is taken as the one the G502 X PLUS's G9 holds for that job: no settings file with a
    # G903 has been read.
    rest = {f"g{index + 1}": ("profile button", "0901")
            for index in range(3, table.count) if index not in held}
    return {"g1": ("left button", "0201"), "g2": ("right button", "0202"),
            "g3": ("middle button", "0203"), **tilts, **rest, "scroll1": ("wheel up", "0409"),
            "scroll2": ("wheel down", "040a")}


def button_label(model: MouseModel, button: str) -> str:
    """What the mouse model calls its button `button`: "G4 (side, rear)"."""
    return next(slot.label for slot in cast(ButtonTable, model.button_table).buttons
                if slot.name == button)


def mouse_where(model: MouseModel, button: str, shifted: bool = False) -> str:
    """How the preview names a mouse button, and what `GhubProfile.labels` holds its label
    under: "G502 X PLUS G4 (side, rear)", with SHIFTED on the G-Shift layer when `shifted`."""
    return f"{model.name} {button_label(model, button)}" + (SHIFTED if shifted else "")
