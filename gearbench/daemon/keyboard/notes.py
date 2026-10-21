"""What the keyboard's controller and its parts share: how long the M and MR LEDs wait after
an effect write, and the words for a change the keyboard does not show or cannot take."""

from __future__ import annotations

from ..shared import unreachable

# Delay from an apply's last lighting write to the M/MR LED writes: the firmware acknowledges an
# effect write before applying it, and applying it clears the indicator LEDs, so an LED write
# inside that window is lost. A loop deadline (Keyboard.write_due_leds), not a sleep.
LED_SETTLE = 0.2

# Why the keyboard does not show what was applied: status's `last_apply_error`, a reply's `note`.
KEYBOARD_AWAY = "the keyboard is away; the change is saved and shows when it is back"

KEYBOARD_SILENT = ("the keyboard is not answering; the change is saved and shows "
                   "when it is back")

#: The same, from a failed write: its receiver cannot reach the keyboard, or nothing answered.
KEYBOARD_OFF = ("the keyboard is switched off or out of range; the change is saved "
                "and shows when it is back")

KEYBOARD_NO_REPLY = ("the keyboard did not answer; the change is saved and shows "
                     "when it is back")

#: A failed write on the receiver after which the cable look found the keyboard's cable and
#: dropped the receiver route: the cable's connect applies what is saved.
KEYBOARD_ON_CABLE = ("the keyboard moved to its cable; the change is saved and shows once "
                     "it is set up there")

#: What is said of a keyboard attached without lighting (`Keyboard.lighting` None).
NO_LIGHTING = "Gearbench cannot drive this keyboard's lighting"


def unanswered_note(exc: BaseException, moved: bool) -> str:
    """The note for a write that went unanswered (`unanswered`), after the cable look it starts:
    `moved` when that look found the cable and dropped the receiver route."""
    if moved:
        return KEYBOARD_ON_CABLE
    return KEYBOARD_OFF if unreachable(exc) else KEYBOARD_NO_REPLY
