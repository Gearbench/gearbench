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
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

from ..battery import BATTERY_WARNINGS, icon_name, on_power, shown_percent
from ..config import PROFILE_CHOICES, expect
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
)
from ..keyboard import key_named, opening
from ..library_files import slug
from ..lighting import DARK, DIM, LIT
from ..mouse import (
    MODEL_IDS as MOUSE_IDS,
    MODELS as MOUSE_MODELS,
    MouseError,
    key_name as mouse_key_name,
)
from ..settings_files import ConfigError
from ..wording import kind_of, number, reason, whole, word_list, written

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
MOUSE_PROBE_TIMEOUT = PROBE_TIMEOUT
MOUSE_PROBE_RETRIES = PROBE_RETRIES
MOUSE_PROBE: ProbeBudget = {"timeout": MOUSE_PROBE_TIMEOUT, "retries": MOUSE_PROBE_RETRIES}

#: How long a device that stopped answering is left unasked before one probe.
#: A mouse's wait doubles up to mice.attached.MOUSE_RETRY_MAX while it stays silent;
#: the keyboard's and the A50 base station's stay at this. A report or an answer from a
#: mouse or the keyboard clears its mark early; the base station sends nothing unasked,
#: so its mark lasts until the probe is answered.
MOUSE_SILENT_RECHECK = 30.0

#: How long the keyboard's battery reading stands, and how often the loop reads it for the
#: low-battery notices on the receiver; G HUB reads it every 60 s.
KEYBOARD_BATTERY_INTERVAL = 60.0

#: A reading at or above this arms both notices (battery.BATTERY_WARNINGS) again and takes one
#: showing down, for a charge the daemon never read (the owner's decision of 2026-09-28). It is
#: 20 points over the 30 % notice, so the percentage's rise when the lights go off does not
#: reach it.
BATTERY_REARM = 50
#: Why the last notice was not posted when the notification server refused it.
BATTERY_REFUSED = "the notification server refused it"

#: The refusal of a number that is no M profile, the number put in.
NOT_AN_M_PROFILE = f"profile is {PROFILE_CHOICES}, not {{}}"
#: The refusal of a command naming a game profile the library does not hold, with its name
#: quoted (wording.quoted). Not wording.NO_GAME_PROFILE, the choice of no profile at all.
NO_GAME_PROFILE_NAMED = "no game profile named {}"

#: Why a virtual device is missing, after the colon of each problem that says what stops
#: working without it.
UINPUT_NOT_OPENED = "/dev/uinput could not be opened"

#: A power-on report comes in pairs; one re-apply per this many seconds answers both.
REAPPLY_INTERVAL = 2.0

#: Seconds between the looks for the cable of a mouse or a headset driven through its
#: receiver, each a walk of sysfs that sends nothing, while the watch on /dev does not run
#: (Plugs.pace): the keyboard's pace (daemon/keyboard/notes.py's RECONNECT_DELAY), so a cable
#: plugged in takes the device over within 2 s.
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


def on_or_off(value: object) -> bool | None:
    """A request's switch value as a bool, or None when it is not one."""
    if isinstance(value, str):
        return {"on": True, "true": True, "1": True,
                "off": False, "false": False, "0": False}.get(value.strip().lower())
    return value if isinstance(value, bool) else None


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
    """What a switch command says to a value on_or_off could not read."""
    if isinstance(value, str):
        return f"{command} is on or off, not {value!r}"
    return f"{command} is on or off, not {kind_of(value)}"


def switch_value(value: object, command: str) -> bool:
    """A switch command's value as a bool (on_or_off), or ConfigError in switch_refusal's
    words, which handle_command answers as the refusal."""
    switch = on_or_off(value)
    if switch is None:
        raise ConfigError(switch_refusal(command, value))
    return switch


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


def not_a_request(request: object) -> str:
    """Why the control socket runs nothing for `request`: it is not a JSON object, or its
    command is not text, which str() would look up as Python writes it ("['x']")."""
    if isinstance(request, dict):
        return f"command is text, not {kind_of(request['command'])}"
    return f"a request is a JSON object, not {kind_of(request)}"


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


#: Why a mouse or a headset's lights do not follow the keyboard, by the device's name: the
#: journal's line, said once an attach, and the note of `mouse_sync` or `headset_sync` turned
#: on.
NOT_SYNCED = "the {} has no lighting Gearbench drives, so it does not follow the keyboard"
#: The refusal of a mouse's or a headset's own lighting while it follows the keyboard that
#: leads: the device, the leader by its name (keyboard.followed) and the command that turns
#: its switch off.
SYNC_ON = ("the {device} follows the {leader}'s color; turn off Follow the {leader} (or "
           "`gearbench-ctl {command} off`) first")

#: hidpp_refusal's sentences, each with the device's name put in.
DEVICE_GONE = "the {} went away"
DEVICE_NO_REPLY = "the {} did not answer; it may be asleep or switched off"
DEVICE_OFF = "the {} is switched off or out of range"
DEVICE_REFUSED = "the {} refused that"

#: A device node this user may not open, with its path and the device's words put in:
#: "Astro A50 X's base station", "G432".
NODE_DENIED = "no permission to open {}, the {}'s node: " + NO_RULE
#: A found device whose setup failed: its name, why ("no answer" for a timeout), and what
#: it is plugged in by, as "its base station".
DEVICE_NOT_SET_UP = "the {} could not be set up ({}); unplug {} and plug it back in"
#: The reply to an answer Gearbench cannot read, with the device's name.
NOT_UNDERSTOOD = "the {} gave an answer Gearbench does not understand"


def hidpp_refusal(device: str, failure: HidppFailure) -> dict:
    """A HID++ failure's reply, worded for `device`, the name put in its sentences."""
    if isinstance(failure, UnreadableAnswer):
        return refusal(NOT_UNDERSTOOD.format(device), failure)
    if isinstance(failure, DeviceGone):
        return refusal(DEVICE_GONE.format(device), failure)
    if isinstance(failure, HidppTimeout):
        return refusal(DEVICE_NO_REPLY.format(device), failure)
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
                   battery_at: float | None, *, answering: bool = True,
                   battery_found: bool = True) -> dict:
    """A mouse or a headset attached, as `status`'s `mice_attached` and `headsets_attached`
    give it: its name; its node, None for a headset away whose receiver is let go; its route
    in wording.route's words; whether it lists a battery, its last reading and when that was
    taken; and whether it answers, as its own reply's `answering`."""
    return {"name": name, "device": device, "route": route, "battery_found": battery_found,
            "battery": battery, "battery_at": battery_at, "answering": answering}


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


class BatteryNotices:
    """A device's low-battery notices (warn_battery), one rule for every device. A subclass
    sets the words and battery_names(); the session's desktop posts the notices."""

    #: The last percentage on battery, the lowest threshold warned of, the notice showing,
    #: and why the last one could not be posted, so a repeat of it is said at debug; and how
    #: many notices went to the desktop, so one whose id comes after a later send is taken
    #: down.
    _battery_before: int | None = None
    battery_warned: int | None = None
    _battery_notice: int | None = None
    _battery_unposted: str | None = None
    _battery_sends = 0
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

    def warn_battery(self, reading: dict) -> None:
        """A notice when two readings in a row are below a threshold not yet
        warned of; on external power or charging, or at BATTERY_REARM or more,
        arm both again and take it down."""
        subject = self.battery_names()[1]
        if on_power(reading):
            self.battery_rearmed(f"{subject} {self.BATTERY_POWERED}")
            return
        percent = reading.get("percent")
        if not isinstance(percent, int | float):
            return
        # So a notice below 30 % never says 30 % left.
        percent = shown_percent(percent)
        if percent >= BATTERY_REARM:
            self.battery_rearmed(f"{subject}'s battery is at {percent}%")
            return
        before, self._battery_before = self._battery_before, percent
        if before is None:
            return
        for threshold in sorted(BATTERY_WARNINGS):
            if percent < threshold and before < threshold:
                if self.battery_warned is None or threshold < self.battery_warned:
                    self.send_battery_notice(threshold, percent)
                return

    def battery_rearmed(self, why: str) -> None:
        """Both notices armed again and the one showing taken down, `why` for the log line."""
        if self.battery_warned is not None:
            log.info("%s%s; its battery warning is cleared", self.BATTERY_PREFIX, why)
        self._battery_before = self.battery_warned = self._battery_unposted = None
        if self._battery_notice is not None:
            self.d.session.desktop.close_notification(self._battery_notice)
            self._battery_notice = None

    def send_battery_notice(self, threshold: int, percent: int) -> None:
        """The notice for `threshold`, in place of the one showing. One the
        desktop did not take is not counted, so the next reading tries again."""
        session = self.d.session
        name, subject = self.battery_names()
        lowest = threshold == min(BATTERY_WARNINGS)
        summary = f"{name} battery {'very low' if lowest else 'low'}"
        body = f"{percent}% left. {self.BATTERY_BODY}"
        before, self.battery_warned = self.battery_warned, threshold
        said = f"{self.BATTERY_PREFIX}{subject}'s battery is at {percent}%, below {threshold}%"
        sent = self._battery_sends + 1

        def done(notice: int | None) -> None:
            if notice is None:
                if self.battery_warned == threshold:
                    self.battery_warned = before
                self._battery_unposted = BATTERY_REFUSED
            elif self.battery_warned is None or sent != self._battery_sends:
                # Armed again since, or sent again before this id came, which that send could
                # not replace: both would stay up.
                session.desktop.close_notification(notice)
            else:
                self._battery_notice = notice

        # The icon the Battery row and the tray show for the same reading.
        icon = icon_name({"percent": percent})
        if not session.desktop.notify(summary, body, icon,
                                      replaces_id=self._battery_notice or 0, done=done):
            self.battery_warned = before
            self.battery_unposted(said)
            return
        # Counted once sent: `done` comes from the bus's reply, at a later dispatch.
        self._battery_sends = sent
        # The desktop warns of a refusal once; each reading tries again, said at debug.
        refused, self._battery_unposted = self._battery_unposted == BATTERY_REFUSED, None
        log.log(logging.DEBUG if refused else logging.INFO, "%s; posting a notice", said)

    def battery_unposted(self, said: str) -> None:
        """Log that a battery notice `said` could not be posted, and why: at
        info unless the last why was the same, since without python3-gi that
        is every reading."""
        why = ("python3-gi is not installed" if not self.d.session.desktop.available
               else "the session bus is not connected")
        log.log(logging.DEBUG if why == self._battery_unposted else logging.INFO,
                "%s; no notice posted, since %s; trying again at the next reading", said, why)
        self._battery_unposted = why
