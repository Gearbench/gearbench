"""The helpers below the daemon's controllers, among them the reply helpers, the request
readers, the failure tests, the budgets they share and the low-battery notices; and the core's
own beside them: its checks of a request and its reply to a mouse's failure."""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict, TypeGuard

from ..battery import FULL, icon_name, notice_levels, on_power, shown_percent
from ..config import NO_NOTICES, PROFILE_CHOICES, URGENT, battery_notices_of, expect
from ..hidpp import (
    NO_RULE,
    PROBE_RETRIES,
    PROBE_TIMEOUT,
    DeviceGone,
    HidppError,
    HidppFailure,
    HidppTimeout,
    UnreadableAnswer,
    not_there,
    open_device,
    power_supplied,
)
from ..keyboard import key_named, opening
from ..library_files import slug
from ..lighting import DARK, DIM, LIT
from ..mouse import (
    MODEL_IDS as MOUSE_IDS,
    MODELS as MOUSE_MODELS,
    key_name as mouse_key_name,
)
from ..sensor import MouseError
from ..settings_files import ConfigError
from ..tray import Battery
from ..wording import ASLEEP_OR_SWITCHED_OFF, kind_of, number, reason, whole, word_list, written

if TYPE_CHECKING:
    from ..hidpp import Hidpp
    from . import Daemon

log = logging.getLogger("gearbench")


class ProbeBudget(TypedDict):
    """A request's `timeout` and `retries`, as the transport's calls take them by keyword."""

    timeout: float
    retries: int


#: The budget for a request to a device that may be asleep, writes included, from the first
#: probe to the hand-back: the transport's (hidpp.PROBE_TIMEOUT, which has the measurement).
MOUSE_PROBE: ProbeBudget = {"timeout": PROBE_TIMEOUT, "retries": PROBE_RETRIES}

#: How long a device that stopped answering is left unasked before one probe.
#: A mouse's wait doubles up to MOUSE_RETRY_MAX while it stays silent;
#: the keyboard's and the A50 base station's stay at this. A report or an answer from a
#: mouse or the keyboard clears its mark early; the base station sends nothing unasked,
#: so its mark lasts until the probe is answered.
MOUSE_SILENT_RECHECK = 30.0

#: How long the keyboard's battery reading stands, and how often the loop reads it for the
#: low-battery notices on the receiver; G HUB reads it every 60 s.
KEYBOARD_BATTERY_INTERVAL = 60.0

#: A reading at or above this arms both notices (battery.PRESET_LEVELS) again and takes one
#: showing down, for a charge the daemon never read (the owner's decision of 2026-09-28). It is
#: 20 points over the 30 % notice, so the percentage's rise when the lights go off does not
#: reach it. A percent read from the cell's voltage arms them only at its second reading in a
#: row up here (BatteryNotices.held_rise).
BATTERY_REARM = 50
#: Why the last notice was not posted when the notification server refused it.
BATTERY_REFUSED = "the notification server refused it"
#: Each desktop that warns of a device's battery itself where the kernel gives the device a
#: power supply, by its entry in XDG_CURRENT_DESKTOP, with the name a line gives it (the
#: owner's decision of 2026-10-28, "The 'also warns' rule goes by desktop"). Both read in
#: their source: GNOME's gsd-power posts a notice for each device battery UPower lists, at
#: UPower's levels, any kind of device (gsd-power-manager.c, engine_charge_low); KDE
#: Plasma's PowerDevil 6.8.2 one for each battery Solid lists that is no power supply, as
#: it falls to 10% or below by default (powerdevilcore.cpp,
#: emitBatteryChargePercentNotification), and Solid lists UPower's mouse, keyboard, headset
#: and headphones among others. Any other desktop counts as not warning.
WARNING_DESKTOPS = {"GNOME": "GNOME", "KDE": "KDE Plasma"}

#: The refusal of a number that is no M profile, the number put in.
NOT_AN_M_PROFILE = f"the M profile is {PROFILE_CHOICES}, not {{}}"
#: The refusal of a command naming a game profile the library does not hold, with its name
#: quoted (wording.quoted). Not wording.NO_GAME_PROFILE, the choice of no profile at all.
NO_GAME_PROFILE_NAMED = "no game profile named {}"

#: Why a virtual device is missing, after the colon of each problem that says what stops
#: working without it.
UINPUT_NOT_OPENED = "/dev/uinput could not be opened"

#: A power-on report comes in pairs; one re-apply per this many seconds answers both.
REAPPLY_INTERVAL = 2.0

#: Seconds between the looks for a missing keyboard and for the cable of a keyboard, a mouse
#: or a headset driven through its receiver, each a walk of sysfs that sends nothing, while
#: the watch on /dev does not run (Plugs.pace), so a cable plugged in takes the device over
#: within 2 s.
CABLE_LOOK = 2.0

#: Seconds between the looks at whether the node of a headset found that did not answer has
#: gone, while its look waits (Lookout) and the watch on /dev, whose event for the node wakes
#: the loop, does not run (Plugs.pace): the GUIDE has an unplug of more than about a second
#: end that wait.
GONE_LOOK = 1.0

#: Seconds between looks for a missing headset (Lookout), every headset's alike. A found one
#: that does not answer costs the loop its probe's time, so its wait doubles each time, up to
#: HEADSET_RETRY_MAX.
HEADSET_RETRY = 5.0
HEADSET_RETRY_MAX = 60.0

#: How often a missing mouse, or a keyboard switched off behind its receiver, is probed,
#: doubling after each probe nothing answers up to MOUSE_RETRY_MAX: one that is off keeps its
#: receiver's node and never answers, and each probe holds the loop 1.5 s.
MOUSE_RETRY = 10.0
MOUSE_RETRY_MAX = 120.0

#: How early, in seconds, a periodic look or poll may be taken (look_due). The looks a pass
#: sets come due as far apart as the work between them took in it, a walk of /proc or a
#: request, and each would wake the loop on its own a few milliseconds after the first:
#: taken with the first, they share its wakeup and its walk of sysfs (hidpp.one_listing).
LOOK_SLACK = 0.05


def look_due(now: float, at: float) -> bool:
    """Whether a periodic look or poll due at `at` is taken at `now`: due, or due within
    LOOK_SLACK."""
    return now + LOOK_SLACK >= at


#: 0x1D4B WirelessStatus. A device that comes back sends an unprompted fn 0 report whose
#: second byte asks to be configured again and whose third is 1 for "power switch
#: activated": G305 `01 01 01`, measured 2026-09-22, as in G HUB's capture and Solaar.
F_WIRELESS = 0x1D4B


def darker(one: str, other: str) -> str:
    """The darker of two lighting levels."""
    return max(one, other, key=(LIT, DIM, DARK).index)


def scaled(color: int, percent: int) -> int:
    """Each of a color's red, green and blue at `percent` of itself, rounded down. A color at
    the keyboard's brightness goes through PerKeyboard.at_brightness."""
    if percent >= 100:
        return color
    return sum(((color >> shift & 0xFF) * percent // 100) << shift for shift in (16, 8, 0))


def said_once(said: dict[str, str], node: str, line: str, level: int) -> None:
    """Log `line`, an attempt's on `node`, a device node or another thing tried again, at
    `level`, or at debug where it is the last line `said` keeps for that node, and keep it
    there: an attempt is tried again, a connect at RECONNECT_DELAY or longer, and two nodes
    tried in one attempt each keep their own."""
    log.log(logging.DEBUG if said.get(node) == line else level, "%s", line)
    said[node] = line


def failure_reason(exc: BaseException) -> str:
    """Why a device request failed, in words: the reason of the OSError the node's
    transport chained as `exc`'s cause, whose own text holds the errno, else `exc`'s."""
    cause = exc.__cause__
    return reason(cause) if isinstance(cause, OSError) else reason(exc)


def unreachable(exc: BaseException) -> bool:
    """Whether a failure is a receiver saying it cannot reach the device
    (HidppError.unreachable), itself or behind the error it caused."""
    return any(isinstance(failure, HidppError) and failure.unreachable
               for failure in (exc, exc.__cause__))


def unanswered(exc: BaseException) -> bool:
    """Whether a failure says the device is not there to answer: no reply
    (a HidppTimeout, raised or behind the error it caused), or `unreachable`,
    which the c541 answers at once for a G915 switched off behind it."""
    return any(not_there(failure) for failure in (exc, exc.__cause__))


def whole_number(value: object, command: str) -> int:
    """A request's value as a whole number: one in JSON (wording.whole), or text of decimal
    digits after at most one minus; else ValueError saying what `command` wants. int()
    would take true and false as 1 and 0 and cut a fraction toward zero."""
    if whole(value):
        return value
    # One minus, and decimal digits only: isdigit() passes a superscript int() refuses.
    if isinstance(value, str) and value.strip().removeprefix("-").isdecimal():
        # int() still refuses \x1c to \x1f, which strip() takes, and over 4300 digits.
        with contextlib.suppress(ValueError):
            return int(value)
    raise ValueError(f"{command} is a whole number, not {written(value)}")


def name_text(value: object, where: str) -> str:
    """The name a request looks something up by, as text: text as it is, and a number as
    str() writes it, so a game profile named "2" loads for a JSON 2. Anything else is
    refused in expect's words: str() would look a null up as the name "None"."""
    if not number(value):
        expect(value, str, where, "text")
    return str(value)


def mouse_model_named(text: str) -> str | None:
    """The key (mouse.model_key) of the mouse model `text` names, by any of its product ids or
    its name, in any case, as `--mouse` and `mouse_select` take one; None for anything
    else."""
    wanted = text.strip().lower()
    return next((key for key, ids in MOUSE_IDS.items()
                 if wanted in ids or wanted == mouse_key_name(key).lower()), None)


def keyboard_model_named(value: object, field: str) -> str:
    """The key (keyboard.model_key) of the keyboard model `value` names, as `sync_keyboard` and
    a request's `keyboard` take one (keyboard.key_named). Anything else is a ConfigError that
    names `field`: name_text's for a value not text or a number, else key_named's, which
    lists the models there are."""
    return key_named(name_text(value, field), field)


def attached_list(labels: Iterable[str]) -> str:
    """The mice attached, by their labels (AttachedMouse.label), as a refusal of a mouse asked
    for lists them."""
    return "attached: " + ", ".join(labels)


def mouse_model_asked(value: object, attached: Iterable[str]) -> str:
    """The key (mouse.model_key) of the mouse model a request's `device` names by any of its
    product ids or its name (mouse_model_named), attached or not. A value neither text nor a
    number is name_text's refusal; one that names no mouse model is a ConfigError listing the
    mice attached by their labels (`attached`), or with none the models there are."""
    text = name_text(value, "device")
    key = mouse_model_named(text)
    if key is None:
        labels = list(attached)
        listed = (attached_list(labels) if labels else
                  f"they are {word_list([mouse_key_name(model) for model in MOUSE_IDS], 'and')}")
        raise ConfigError(f"{text.strip().lower()!r} names no mouse model Gearbench drives; "
                          f"{listed}")
    return key


def kept_pick(kept: str | None) -> int | None:
    """config.json's `mouse_selected` as the product id the mice are selected on (Mice
    .selected), which Mice.current finds on either route; None for none, and for an id no
    mouse model of this build has, which stays in the file."""
    return int(kept, 16) if kept is not None and int(kept, 16) in MOUSE_MODELS else None


def checked_layer(layer: object) -> dict:
    """A request's `layer` as the window sends it, or TypeError: an object whose profile is
    an M profile's number and whose overlay is a game profile's name, each null or left out.
    check_layer compares them with ==, by which true is M1, and its refusal names the
    overlay, which str() of a list would show in Python's brackets."""
    if not isinstance(layer, dict):
        raise TypeError(f"layer is an object with profile and overlay, not {kind_of(layer)}")
    profile, overlay = layer.get("profile"), layer.get("overlay")
    if not (profile is None or whole(profile)):
        raise TypeError(f"layer.profile is an M profile's number or null, not "
                        f"{written(profile)}")
    if not (overlay is None or isinstance(overlay, str)):
        raise TypeError(f"layer.overlay is a game profile's name or null, not "
                        f"{written(overlay)}")
    return layer


def no_effect(device: str, name: str, has: tuple[str, ...] | list[str],
              none: str = "it has none Gearbench can set") -> str:
    """The refusal of the effect `name` on `device`, whose lighting offers the effects `has`,
    or `none` in their place where it offers none."""
    listed = f"it has {word_list(has, 'and')}" if has else none
    return f"the {device} has no {name} effect; {listed}"


def switch_refusal(command: str, value: object) -> str:
    """What a switch command says to a value switch_value could not read."""
    if isinstance(value, str):
        return f"{command} is on or off, not {value!r}"
    return f"{command} is on or off, not {kind_of(value)}"


def switch_value(value: object, command: str) -> bool:
    """A switch command's value as a bool: a bool as itself, or on, true, 1, off, false or 0 as
    text in any case and with spaces around it; else ConfigError in switch_refusal's words,
    which handle_command answers as the refusal."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        switch = {"on": True, "true": True, "1": True,
                  "off": False, "false": False, "0": False}.get(value.strip().lower())
        if switch is not None:
            return switch
    raise ConfigError(switch_refusal(command, value))


def nested_past(value: object, limit: int) -> bool:
    """Whether `value` nests lists and objects more than `limit` deep, the outermost counted.
    A loop, since json.loads builds nesting deeper than the stack."""
    stack = [(value, 1)] if isinstance(value, (dict, list)) else []
    while stack:
        item, depth = stack.pop()
        if depth > limit:
            return True
        stack.extend((child, depth + 1)
                     for child in (item.values() if isinstance(item, dict) else item)
                     if isinstance(child, (dict, list)))
    return False


def is_request(request: object) -> TypeGuard[dict]:
    """Whether the control socket runs `request`: a JSON object whose command, where it has
    one, is text. One with no command is looked up as "", which no command is."""
    return isinstance(request, dict) and isinstance(request.get("command", ""), str)


def not_a_request(request: object) -> str:
    """Why the control socket runs nothing for `request`, one is_request refuses: it is not a
    JSON object, or its command is not text, which str() would look up as Python writes it
    ("['x']")."""
    if isinstance(request, dict):
        return f"bad request: command is text, not {kind_of(request['command'])}"
    return f"bad request: a request is a JSON object, not {kind_of(request)}"


def digest(shown: object) -> str:
    """Eight hex digits of `shown` as JSON, for a status field a front end compares
    with the one it last read."""
    return hashlib.sha1(json.dumps(shown).encode(), usedforsecurity=False).hexdigest()[:8]


def refusal(sentence: str, raw: BaseException) -> dict:
    """A refused command's reply: what a person reads, and the failure's own text, apart: what
    the device or the system said, or the request that got no answer."""
    return {"ok": False, "error": sentence, "detail": str(raw)}


def not_reached(device: str, failure: BaseException) -> dict | None:
    """The reply for a failure caused by an OSError on the device's node, or None for any
    other. Call it after the gone cases and the base station's timeout, which can chain an
    OSError too."""
    error = failure.__cause__
    if not isinstance(error, OSError):
        return None
    return refusal(f"Gearbench could not reach the {device} ({reason(error)})",
                   failure)


#: What is said of a keyboard or a mouse attached without lighting Gearbench drives (its
#: `lighting` None), by the device's name: "Gearbench drives", since some such devices have
#: lights, as the PRO X SUPERLIGHT has the DPI indicator its firmware owns.
NO_LIGHTING = "the {} has no lighting Gearbench drives"
#: Why a mouse or a headset's lights do not follow the keyboard, by the device's name: the
#: journal's line, said once an attach, and the note of `mouse_sync` or `headset_sync` turned
#: on.
NOT_SYNCED = NO_LIGHTING + ", so it does not follow the keyboard"
#: The way out of a refusal that a Follow switch causes: the leader by its name
#: (keyboard.followed) and the command that turns the switch off.
SYNC_OFF_FIRST = "turn off Follow the {leader} (or `gearbench-ctl {command} off`) first"
#: The refusal of a mouse's or a headset's own lighting while it follows the keyboard that
#: leads, by the device's name.
SYNC_ON = "the {device} follows the {leader}'s color; " + SYNC_OFF_FIRST

#: hidpp_refusal's sentences, each with the device's name put in.
DEVICE_GONE = "the {} went away"
DEVICE_NO_REPLY = f"the {{}} did not answer; it may be {ASLEEP_OR_SWITCHED_OFF}"
#: The same for a wired device or a keyboard on its cable, which asleep or switched off does
#: not explain: its name and what it is plugged in by, as "its USB adapter".
DEVICE_NO_REPLY_WIRED = "the {} did not answer; unplug {} and plug it back in"
DEVICE_OFF = "the {} is switched off or out of range"
DEVICE_REFUSED = "the {} refused that"

#: A device node this user may not open, with its path and the device's words put in:
#: "Astro A50 X's base station", "G432".
NODE_DENIED = "no permission to open {}, the {}'s node: " + NO_RULE
#: A found device whose setup failed: its name and why ("no answer" for a timeout).
SET_UP_FAILED = "the {} could not be set up ({})"
#: SET_UP_FAILED's, with what the device is plugged in by, as "its base station", for the
#: replug that sets it up again.
DEVICE_NOT_SET_UP = SET_UP_FAILED + "; unplug {} and plug it back in"
#: The reply to an answer Gearbench cannot read, with the device's name.
NOT_UNDERSTOOD = "the {} gave an answer Gearbench does not understand"


def hidpp_refusal(device: str, failure: HidppFailure, *, cabled: bool = False) -> dict:
    """A HID++ failure's reply, worded for `device`, the name put in its sentences. `cabled` is
    for a device on its cable, which asleep or switched off cannot explain."""
    if isinstance(failure, UnreadableAnswer):
        return refusal(NOT_UNDERSTOOD.format(device), failure)
    if isinstance(failure, DeviceGone):
        return refusal(DEVICE_GONE.format(device), failure)
    if isinstance(failure, HidppTimeout):
        said = (DEVICE_NO_REPLY_WIRED.format(device, "its cable") if cabled
                else DEVICE_NO_REPLY.format(device))
        return refusal(said, failure)
    if unreachable(failure):
        # The receiver's answer, not the device's: it is not there.
        return refusal(DEVICE_OFF.format(device), failure)
    return not_reached(device, failure) or refusal(DEVICE_REFUSED.format(device), failure)


def mouse_refusal(exc: MouseError) -> dict:
    """A mouse failure's reply: one mouse_call chained from a HID++ failure holds
    hidpp_refusal's sentence with the mouse's name, the failure its detail; a MouseError with
    no cause is already a sentence. One chained from another MouseError, as flash_write's
    sector left unknown, follows that one's reply."""
    cause = exc.__cause__
    if isinstance(cause, HidppFailure):
        return refusal(str(exc), cause)
    if isinstance(cause, MouseError):
        reply = mouse_refusal(cause)
        reply["error"] = f"{reply['error']}; {exc}"
        return reply
    return {"ok": False, "error": str(exc)}


def with_note(reply: dict, note: str | None, detail: str | None = None) -> dict:
    """A reply with `note` added when there is something to say, and the
    device's own words behind it as `detail`."""
    if note:
        reply["note"] = note
        if detail:
            reply["detail"] = detail
    return reply


def attached_entry(name: str, device: str | None, route: str, battery: dict | None,
                   battery_at: float | None, notices: BatteryNotices, *,
                   answering: bool = True) -> dict:
    """A mouse or a headset attached, as `status`'s `mice_attached` and `headsets_attached`
    give it: its name; its node, None for a headset away whose receiver is let go; its route
    in wording.route's words; whether it lists a battery (`notices`' battery_found), its last
    reading and when that was taken; the lowest level its `notices` warned of since they were
    last armed again, as the keyboard's `battery_warned`; and whether it answers, as its own
    reply's `answering`."""
    return {"name": name, "device": device, "route": route,
            "battery_found": notices.battery_found(), "battery": battery,
            "battery_at": battery_at, "battery_warned": notices.battery_warned,
            "answering": answering}


def same_profile(one: str, other: str) -> bool:
    """Whether two game profile names mean the same profile: the library
    matches on the filename, so "Team Fortress 2" is "team-fortress-2"."""
    return slug(one) == slug(other)


def node_denied(path: Path) -> bool:
    """Whether a device node is there and this user may not open it: its udev
    rule is missing or not yet applied, or another account or a text console has
    the seat. A node that has gone is only absent."""
    return path.exists() and not os.access(path, os.R_OK | os.W_OK)


def open_keyboard(found: tuple[Path, int, int]) -> tuple[Hidpp, int] | None:
    """The keyboard at `found`, (node, product id, derived index) as
    keyboard.find_attached_keyboard gives it, open on the index that answers its model's
    witness (hidpp.open_device), the derived one first, on MOUSE_PROBE, with that index; None
    where nothing answers, as on a receiver's node with its keyboard switched off, or the
    node is gone. Raises the OSError of any other failed open, PermissionError for a node the
    udev rule did not grant among them."""
    path, product, derived = found
    return open_device(path, **opening(product, derived), **MOUSE_PROBE)


class Lookout:
    """When a headset's controller looks again for its missing headset: HEADSET_RETRY seconds
    after a look, twice the last wait after each further look whose node gave no answer, up to
    HEADSET_RETRY_MAX, and at once when that node is gone, since the problem it is reported
    under asks for an unplug and a replug."""

    def __init__(self) -> None:
        #: The node whose probe got no answer, None while none did.
        self.unanswered: Path | None = None
        self.retry_at = 0.0
        #: The wait after the next look if its node gives no answer; HEADSET_RETRY after any
        #: other.
        self.wait = HEADSET_RETRY

    def due(self) -> bool:
        gone = self.unanswered is not None and not self.unanswered.exists()
        return gone or look_due(time.monotonic(), self.retry_at)

    def deadlines(self, pace: Callable[[float], float]) -> list[float]:
        """When `due` is next asked: the next look, once the first has run, and while a node
        gave no answer, GONE_LOOK from now at `pace` (Plugs.pace), to see whether it has
        gone."""
        due = [self.retry_at] if self.retry_at else []
        if self.unanswered is not None:
            due.append(time.monotonic() + pace(GONE_LOOK))
        return due

    def looked(self) -> None:
        """Time the next look from the end of this one."""
        if self.unanswered is not None:
            wait, self.wait = self.wait, min(self.wait * 2, HEADSET_RETRY_MAX)
        else:
            wait = self.wait = HEADSET_RETRY
        self.retry_at = time.monotonic() + wait

    def recheck(self) -> None:
        """After a resume: due at once, since time.monotonic() stops while the machine
        sleeps."""
        self.retry_at = min(self.retry_at, time.monotonic())


class AttachNotes:
    """What a controller's looks for its missing device found, for its problems() and the
    journal: a node refused to this user, warned of, and why a found device could not be set
    up, said at info. A repeat of a node's same line is said at debug (said_once): of a refusal
    until that node opens, of a failure until an attach goes through. `looking` clears both
    problems as a look starts, and `attached` as an attach goes through, a node's tried
    before it in the same look among them."""

    def __init__(self) -> None:
        #: The node refused, with its problem's sentence, and a found device's setup failure.
        self.denied: tuple[Path, str] | None = None
        self.not_set_up: str | None = None
        #: The refusal line said of each node, and the last failure line said of each.
        self._denied_said: dict[str, str] = {}
        self._said: dict[str, str] = {}

    def looking(self) -> None:
        self.denied = self.not_set_up = None

    def refused(self, path: Path, problem: str, line: str) -> None:
        """The node `path` refused to this user: `problem` for problems(), `line` warned of."""
        self.denied = path, problem
        said_once(self._denied_said, str(path), line, logging.WARNING)

    def opened(self, path: Path) -> None:
        """The node `path` opened: a later refusal of it is warned of again."""
        self._denied_said.pop(str(path), None)

    def failed(self, path: Path, line: str) -> None:
        """Why the device on the node `path` is not attached, said at info, or at debug where
        it is the last line said of that node: a look that tries two nodes keeps each one's."""
        said_once(self._said, str(path), line, logging.INFO)

    def attached(self) -> None:
        self.looking()
        self._said.clear()

    def denied_problem(self) -> str | None:
        """The refused node's sentence while that node is there and still refused."""
        if self.denied is not None and node_denied(self.denied[0]):
            return self.denied[1]
        return None


def warning_desktop() -> str | None:
    """The name of the desktop running, where it warns of a battery the kernel gives a device
    (WARNING_DESKTOPS); None for any other, or none named. It relies on XDG_CURRENT_DESKTOP in
    the daemon's environment, which the user manager's gives the unit: GNOME's gnome-session
    puts its whole environment there (gsm-util.c, gsm_util_export_user_environment), and
    Plasma's startplasma before it starts its session's target (startplasma.cpp,
    syncDBusEnvironment, through KUpdateLaunchEnvironmentJob's SetEnvironment). Read in
    their source and measured on 2026-11-03: GNOME under GNOME Shell 50 and 51, KDE under
    Plasma 6.8.1."""
    entries = os.environ.get("XDG_CURRENT_DESKTOP", "").split(":")
    return next((WARNING_DESKTOPS[entry] for entry in entries if entry in WARNING_DESKTOPS),
                None)


class BatteryNotices:
    """A device's low-battery notices (warn_battery), one rule for every device, at the levels
    of its preset in config.json's battery_notices (battery_preset). A subclass sets the words
    and each method here that raises NotImplementedError; the session's desktop posts the
    notices."""

    #: The last percentage on battery, the lowest threshold warned of, the notice showing,
    #: and why the last one could not be posted, so a repeat of it is said at debug; and how
    #: many notices went to the desktop, so one whose id comes after a later send is taken
    #: down, and one whose refusal comes after it changes nothing.
    _battery_before: int | None = None
    battery_warned: int | None = None
    _battery_notice: int | None = None
    _battery_unposted: str | None = None
    _battery_sends = 0
    #: The preset the last reading was warned of by, so a reading under another arms both
    #: notices again: a notice up for a level the new preset does not have would stay up.
    _battery_preset: str | None = None
    #: The model the notices were last armed for (armed_for): another model's readings are
    #: another battery's.
    battery_of: str | None = None
    #: The journal's prefix for the device's lines, what it is doing on power after its name
    #: ("is charging"), and the notice's last sentence.
    BATTERY_PREFIX: str
    BATTERY_POWERED: str
    BATTERY_BODY: str
    #: The daemon, set by the subclass's __init__.
    d: Daemon

    def battery_names(self) -> tuple[str, str]:
        """The device's name in the notice, and in the journal, as "the headset"."""
        raise NotImplementedError

    def battery_ids(self) -> Sequence[str]:
        """The names config.json's battery_notices may keep the device's preset under, the one
        it is kept under first (config.battery_notices_of): a keyboard's or a mouse's model ids
        (keyboard.model_ids, mouse.model_ids), or a headset's key alone."""
        raise NotImplementedError

    def battery_preset(self) -> str:
        """The device's preset of config.BATTERY_PRESETS (config.battery_notices_of)."""
        return battery_notices_of(self.d.config.battery_notices, self.battery_ids())

    def tray_line(self, key: str, name: str, reading: dict | None, *, answering: bool = True,
                  cabled: bool = False, read_at: float | None = None) -> Battery:
        """The tray's line for a reading of the device's (tray.Battery.of), low below the lower
        level of its preset (battery_preset)."""
        return Battery.of(key, name, reading, answering=answering, cabled=cabled,
                          read_at=read_at, preset=self.battery_preset())

    def battery_node(self) -> Path | None:
        """The hidraw node the device is driven on, None while it is not."""
        raise NotImplementedError

    def battery_found(self) -> bool:
        """Whether the device driven has a battery; a subclass whose models may have none says
        which."""
        return True

    def notices_reply(self) -> dict:
        """The device's preset, whether the kernel gives it a power supply and the desktop
        that warns of its battery itself, for its reply: the window's Battery notices row and
        the Battery row's colors."""
        return {"battery_notices": self.battery_preset(),
                "kernel_battery": self.kernel_battery(), "desktop_warns": self.desktop_warns()}

    def kernel_battery(self) -> bool:
        """Whether the kernel gives the device's node a power supply (hidpp.power_supplied),
        which UPower reads and a desktop may warn of (desktop_warns)."""
        node = self.battery_node()
        return node is not None and power_supplied(node)

    def desktop_warns(self) -> str | None:
        """The desktop that warns of the device's battery itself (warning_desktop), where the
        kernel gives it one (kernel_battery), None where none does: its very-low notice is not
        urgent then, and its row offers no urgent preset (the owner's decisions of
        2026-10-28)."""
        return warning_desktop() if self.kernel_battery() else None

    def warn_battery(self, reading: dict) -> None:
        """A notice when two readings in a row are below a level of the device's preset not yet
        warned of, none under NO_NOTICES; a fully charged reading is never below, as the window
        and the tray compare it (battery.below). On external power or charging, at
        BATTERY_REARM or more, but for a rise held back (held_rise), or with another preset
        than the last reading's, arm both again and take it down."""
        subject = self.battery_names()[1]
        preset = self.battery_preset()
        if preset != self._battery_preset:
            self._battery_preset = preset
            self.battery_rearmed(f"{subject}'s battery notices are now {preset}")
        if on_power(reading):
            self.battery_rearmed(f"{subject} {self.BATTERY_POWERED}")
            return
        percent = reading.get("percent")
        if not isinstance(percent, int | float):
            return
        # So a notice below 30 % never says 30 % left.
        percent = shown_percent(percent)
        # A fully charged reading starts no run of two below, so the next one alone posts none.
        full = reading.get("state") == FULL
        before, self._battery_before = self._battery_before, None if full else percent
        if percent >= BATTERY_REARM:
            if not self.held_rise(reading, before, percent):
                self.battery_rearmed(f"{subject}'s battery is at {percent}%")
            return
        if full or before is None or preset == NO_NOTICES:
            return
        levels = notice_levels(preset)
        for threshold in sorted(levels):
            if percent < threshold and before < threshold:
                if self.battery_warned is None or threshold < self.battery_warned:
                    self.send_battery_notice(threshold, percent, threshold == min(levels))
                return

    def held_rise(self, reading: dict, before: int | None, percent: int) -> bool:
        """Whether a reading at BATTERY_REARM or more leaves the notices as they are: one read
        from the cell's voltage, which gives its millivolts, after one below. Such a percent
        moves with the load: an old G933's read 18%, 51% and 28% within 23 minutes while its
        music played, paused and played again (measured 2026-10-28), and its notice came
        twice. So it arms them again only at its second reading in a row up there, as a
        notice comes only at the second below its level; a percent the device works out
        itself arms them at once, since a device may report one only as it changes."""
        if "millivolts" not in reading or before is None or before >= BATTERY_REARM:
            return False
        if self.battery_warned is not None:
            log.debug("%s%s's battery is at %d%%; its battery warning stays until a second "
                      "reading in a row at %d%% or more", self.BATTERY_PREFIX,
                      self.battery_names()[1], percent, BATTERY_REARM)
        return True

    def armed_for(self, model: str, why: str) -> None:
        """Both notices armed again (battery_rearmed, `why` for its line) when `model` is
        another than the one they were last armed for."""
        if model != self.battery_of:
            self.battery_of = model
            self.battery_rearmed(why)

    def battery_rearmed(self, why: str) -> None:
        """Both notices armed again and the one showing taken down, `why` for the log line."""
        if self.battery_warned is not None:
            log.info("%s%s; its battery warning is cleared", self.BATTERY_PREFIX, why)
        self._battery_before = self.battery_warned = self._battery_unposted = None
        if self._battery_notice is not None:
            self.d.session.desktop.close_notification(self._battery_notice)
            self._battery_notice = None

    def send_battery_notice(self, threshold: int, percent: int, lowest: bool) -> None:
        """The notice for `threshold`, the lower of the preset's levels where `lowest`, in place
        of the one showing. One the desktop did not take is not counted, so the next reading
        tries again."""
        session = self.d.session
        name, subject = self.battery_names()
        # Urgent only where no desktop warns of it too, where its row offers the preset.
        urgent = lowest and self._battery_preset == URGENT and self.desktop_warns() is None
        summary = f"{name} battery {'very low' if lowest else 'low'}"
        body = f"{percent}% left. {self.BATTERY_BODY}"
        before, self.battery_warned = self.battery_warned, threshold
        said = f"{self.BATTERY_PREFIX}{subject}'s battery is at {percent}%, below {threshold}%"
        sent = self._battery_sends + 1

        def done(notice: int | None, refused: bool) -> None:
            if notice is None:
                # A refusal that comes after a later send leaves what that send set.
                if sent != self._battery_sends:
                    return
                # Armed again since (battery_rearmed), the refusal was the last warning's.
                if self.battery_warned == threshold:
                    self.battery_warned = before
                    if refused:
                        self._battery_unposted = BATTERY_REFUSED
            elif self.battery_warned is None or sent != self._battery_sends:
                # Armed again since, or sent again before this id came, which that send could
                # not replace: both would stay up.
                session.desktop.close_notification(notice)
            else:
                self._battery_notice = notice

        # The icon the Battery row and the tray show for the same reading.
        icon = icon_name({"percent": percent})
        if not session.desktop.notify(summary, body, icon, replaces_id=self._battery_notice or 0,
                                      done=done, urgent=urgent):
            self.battery_warned = before
            why = ("python3-gi is not installed" if not session.desktop.available
                   else "the session bus is not connected")
            # At debug when the last why was the same: without python3-gi that is every reading.
            log.log(logging.DEBUG if why == self._battery_unposted else logging.INFO,
                    "%s; no notice posted, since %s; trying again at the next reading", said, why)
            self._battery_unposted = why
            return
        # Counted once sent: `done` comes from the bus's reply, at a later dispatch.
        self._battery_sends = sent
        # The desktop warns of a refusal once; each reading tries again, said at debug.
        refused, self._battery_unposted = self._battery_unposted == BATTERY_REFUSED, None
        log.log(logging.DEBUG if refused else logging.INFO, "%s; posting a notice", said)
