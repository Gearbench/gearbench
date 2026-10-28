"""What the keyboard's controller and its parts share: how long the M and MR LEDs wait after
an effect write, how long a keyboard missing or switched off is waited for, the words for a
change the keyboard does not show or cannot take and for a probe nothing answered, and a
journal line named by its keyboard (named_words)."""

from __future__ import annotations

from ...lighting import DARK, DIM
from ..shared import unreachable

# Delay from an apply's last lighting write to the M/MR LED writes: the firmware acknowledges an
# effect write before applying it, and applying it clears the indicator LEDs, so an LED write
# inside that window is lost. A loop deadline (Keyboard.write_due_leds), not a sleep.
LED_SETTLE = 0.2

#: How long the node of a keyboard that did not answer its probe is waited on before it is
#: asked again, doubling after each further probe nothing answers up to KEYBOARD_RETRY_MAX
#: (Waiting.wait_for): the mice's 10 s and 120 s (mice.attached.MOUSE_RETRY).
#: A probe holds the daemon's one thread 1.5 s for each device index that
#: stays silent (hidpp.find_index): 1.5 s behind a receiver, which answers for its keyboard
#: switched off, and up to 10.5 s on a node where no index answers.
KEYBOARD_RETRY = 10.0
KEYBOARD_RETRY_MAX = 120.0

#: How long the loop waits before looking for a missing keyboard again, or its cable, while
#: the watch on /dev does not run (Plugs.pace), and before it tries a keyboard's node again
#: after a setup there failed (Keyboards.retry_in).
RECONNECT_DELAY = 2.0

#: The question that ends the journal line of a node no keyboard answered the probe on, by
#: whether the node is a cable's (CABLED_IDS): a keyboard switched off leaves its cable's node
#: (Waiting.wait_for), so one listed there that does not answer is most often being
#: unplugged as the loop looks.
NO_ANSWER_ASKS = {False: "is it switched on?", True: "was its cable just unplugged?"}

#: The end of a note for a change that waits for the keyboard to be back.
SHOWS_WHEN_BACK = "the change is saved and shows when it is back"

# Why the keyboard does not show what was applied: status's `last_apply_error`, a reply's `note`.
# Each names the model (Keyboard.model_name), put in as `model`, as the problems and the banner
# do: with two keyboards "the keyboard" names neither. Not attached, as `status` says it.
KEYBOARD_AWAY = "the {model} is not attached; " + SHOWS_WHEN_BACK

KEYBOARD_SILENT = "the {model} is not answering; " + SHOWS_WHEN_BACK

#: The same, from a failed write: its receiver cannot reach the keyboard, or nothing answered.
KEYBOARD_OFF = "the {model} is switched off or out of range; " + SHOWS_WHEN_BACK

KEYBOARD_NO_REPLY = "the {model} did not answer; " + SHOWS_WHEN_BACK

#: The same, from a write or read the kernel failed outside hidpp.GONE_ERRNOS, with its
#: reason as `why`.
KEYBOARD_UNREACHED = "Gearbench could not reach the {model} ({why}); " + SHOWS_WHEN_BACK

#: A lighting write the keyboard refused, with the device's words as `why`.
KEYBOARD_REFUSED = "the {model} refused that ({why})"

#: A failed write on the receiver after which the cable look found the keyboard's cable and
#: dropped the receiver route: the cable's connect applies what is saved.
KEYBOARD_ON_CABLE = ("the {model} moved to its cable; the change is saved and shows once "
                     "it is set up there")

#: What a lighting change made while the lights are down says: it is saved, and shows when they
#: are back. IDLE_NOTE when the idle keyboard alone put them down; what saves nothing drops
#: "saved; " (Keyboard.lights_note).
LIGHTS_DOWN_NOTE = {
    DIM: "saved; the lights are dimmed while the screen is locked or blank",
    DARK: "saved; the lights are off while the screen is locked or blank",
}
IDLE_NOTE = {
    DIM: "saved; the keyboard is dimmed until the next keypress",
    DARK: "saved; the keyboard's lights are off until the next keypress",
}

#: What is said of a keyboard attached without lighting (`Keyboard.lighting` None); the
#: model's name goes in.
NO_LIGHTING = "Gearbench cannot drive the {}'s lighting"

#: The refusal of `brightness` on a keyboard whose lighting has no brightness levels, the
#: G213's 0x8070 zones, by its model's name.
KEYBOARD_NO_BRIGHTNESS = ("the {} has no brightness control Gearbench can set; its own "
                          "brightness key sets it")

#: The refusal of a lighting command whose `keyboard` names a model none of whose keyboards
#: has been set up since the daemon started (Keyboards.aimed), with the model's name.
NOT_SET_UP_YET = ("no {} has been connected since the daemon started: connect it "
                  "first")

#: What a lighting change for a keyboard that follows the leader says, with the keyboard's
#: name and the leader's: saved for it, and not shown as its own while it follows, though a
#: leader that copies that entry shows it (the owner's decision of 2026-10-21, "B: separate
#: pages, follow switch"). A preview, which saves nothing, says it without "saved; ".
FOLLOWING = "saved; the {} shows the {}'s lighting while it follows it"


def named_words(name: str, words: str, several: bool) -> str:
    """`words` for the journal, after the keyboard model's `name` where `several` keyboards
    are driven, so a line says which one it is about."""
    return f"{name}: {words}" if several else words


def unanswered_note(exc: BaseException, moved: bool, model: str) -> str:
    """The note for a write to the keyboard `model` names that went unanswered (`unanswered`),
    after the cable look it starts: `moved` when that look found the cable and dropped the
    receiver route."""
    note = KEYBOARD_ON_CABLE if moved else KEYBOARD_OFF if unreachable(exc) else KEYBOARD_NO_REPLY
    return note.format(model=model)
