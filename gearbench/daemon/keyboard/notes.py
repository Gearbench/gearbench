"""What the keyboard's controller and its parts share: how long the M and MR LEDs wait after
an effect write, how long a keyboard switched off, or a node whose setup failed, is waited
for, the words for a change the keyboard does not show or cannot take and for a probe
nothing answered, a journal line named by its keyboard (named_words), a connect's
(connected_words), a failed connect's (connect_failed_words) and a teardown's
(teardown_said)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...hidpp import NO_RULE
from ...keyboard import name as keyboard_name
from ...lighting import DARK, DIM
from ...report import untested, untested_note
from ...wording import reason
from ..shared import MOUSE_RETRY, MOUSE_RETRY_MAX, unanswered, unreachable

if TYPE_CHECKING:
    from pathlib import Path

# Delay from an apply's last lighting write to the M/MR LED writes: the firmware acknowledges an
# effect write before applying it, and applying it clears the indicator LEDs, so an LED write
# inside that window is lost. A loop deadline (Keyboard.write_due_leds), not a sleep.
LED_SETTLE = 0.2

#: How long the node of a keyboard that did not answer its probe is waited on before it is
#: asked again, doubling after each further probe nothing answers up to KEYBOARD_RETRY_MAX
#: (Waiting.wait_for): a missing mouse's.
#: A probe holds the daemon's one thread 1.5 s for each device index that
#: stays silent (hidpp.find_index): 1.5 s on a receiver's own node, which answers for its
#: keyboard switched off, up to 10.5 s on a node where no index answers, and at most 1.5 s on
#: a keyboard's own node behind its receiver, where only its slot is asked.
KEYBOARD_RETRY, KEYBOARD_RETRY_MAX = MOUSE_RETRY, MOUSE_RETRY_MAX

#: How long the loop waits before it tries a keyboard's node again after a setup there failed
#: (Keyboards.retry_in), since no event comes for a node already there.
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
# Without SHOWS_WHEN_BACK, the first two are a preview's note, since a preview saves nothing.
KEYBOARD_NOT_ATTACHED = "the {model} is not attached"
KEYBOARD_NOT_ANSWERING = "the {model} is not answering"

KEYBOARD_AWAY = KEYBOARD_NOT_ATTACHED + "; " + SHOWS_WHEN_BACK

KEYBOARD_SILENT = KEYBOARD_NOT_ANSWERING + "; " + SHOWS_WHEN_BACK

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

#: The refusal of `brightness` on a keyboard whose lighting has no brightness levels, the
#: G213's 0x8070 zones, by its model's name.
KEYBOARD_NO_BRIGHTNESS = ("the {} has no brightness control Gearbench can set; its own "
                          "brightness key sets it")

#: The refusal of a lighting command whose `keyboard` names a model none of whose keyboards
#: has answered since the daemon started (Keyboards.aimed), with the model's name.
NOT_SET_UP_YET = "no {} has answered since the daemon started: connect it or switch it on"

#: What a lighting change for a keyboard that follows the leader says, with the keyboard's
#: name and the leader's: saved for it, and not shown as its own while it follows, though a
#: leader that copies that entry shows it (the owner's decision of 2026-10-21, "B: separate
#: pages, follow switch"). A preview, which saves nothing, says it without "saved; ". The
#: third value is the keyboard's model key, which `--keyboard` takes: the window grays out
#: these changes for a keyboard that follows, so the reader of this note is the CLI's.
FOLLOWING = ("saved; the {} shows the {}'s lighting while it follows it; "
             "`gearbench-ctl keyboard-follow off --keyboard {}` gives it its own")


def named_words(name: str, words: str, several: bool) -> str:
    """`words` for the journal, after the keyboard model's `name` where `several` keyboards
    are driven, so a line says which one it is about."""
    return f"{name}: {words}" if several else words


def connected_words(product: int, path: Path, index: int, able: list[str]) -> str:
    """The journal's line for the keyboard set up on `path` at device index `index`: its
    model, its id and what this build drives on it, `able`, and for a model not yet run on
    (report.untested) the note that asks for a device report from it."""
    name = keyboard_name(product)
    said = (f"{name} connected on {path} ({product:04x}, device index 0x{index:02x}): "
            f"{', '.join(able) or 'nothing this build drives'}")
    return f"{said}; {untested_note(name)}" if untested(product) else said


def connect_failed_words(path: str, exc: Exception) -> str:
    """The journal's line for a failure inside Keyboard.set_up's guard on the node at `path`:
    a keyboard gone quiet after its probe (`unanswered`), a node it may not open, or any other
    failure, with its reason."""
    if unanswered(exc):
        return f"the keyboard on {path} stopped answering part way through its setup: {exc}"
    if isinstance(exc, PermissionError):
        # Raised only by open_device's open: the transport turns a later OS error into a
        # HidppFailure. NO_RULE since nothing else in the journal says why.
        return f"cannot open the keyboard's {path}: {reason(exc)}; {NO_RULE}"
    return f"could not set the keyboard up on {path}: {reason(exc)}"


def unanswered_note(exc: BaseException, moved: bool, model: str) -> str:
    """The note for a write to the keyboard `model` names that went unanswered (`unanswered`),
    after the cable look it starts: `moved` when that look found the cable and dropped the
    receiver route."""
    note = KEYBOARD_ON_CABLE if moved else KEYBOARD_OFF if unreachable(exc) else KEYBOARD_NO_REPLY
    return note.format(model=model)


def teardown_said(attached: bool, lights_off: bool, hand_back: bool, silent: bool,
                  done: list[str], missed: list[str]) -> str:
    """What Keyboard.teardown_device did and did not do, for the journal: `attached` while it
    held a keyboard's handle, `done` and `missed` its steps run and failed."""
    if not attached:
        said = "no keyboard"
    elif not hand_back:
        said = "keyboard left on the route it moved from"
    elif silent:
        said = "keyboard not answering, nothing handed back"
    else:
        said = (f"keyboard {'darkened' if lights_off else 'handed back'}: "
                f"{', '.join(done) or 'nothing'}")
        if missed:
            said += f"; not {', '.join(missed)}"
    return said
