"""The helpers below the daemon's controllers, among them the reply helpers, the request
readers, the failure tests, the budgets they share and the low-battery notices; and the core's
own beside them: its checks of a request and its reply to a mouse's failure."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, TypedDict

from ..battery import BATTERY_WARNINGS
from ..config import PROFILE_CHOICES, expect
from ..hidpp import (
    PROBE_RETRIES,
    PROBE_TIMEOUT,
    DeviceGone,
    HidppError,
    HidppFailure,
    HidppTimeout,
    not_there,
    open_device,
)
from ..keyboard import MODEL_OF as KEYBOARDS
from ..library_files import slug
from ..lighting import DARK, DIM, LIT
from ..mouse import MouseError
from ..wording import kind_of, number, reason, whole, written

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
#: the keyboard's and the headset's stay at this. Any report or answer from it
#: clears the mark early.
MOUSE_SILENT_RECHECK = 30.0

#: How long the keyboard's battery reading stands, and how often the loop reads it for the
#: low-battery notices on the receiver; G HUB reads it every 60 s.
KEYBOARD_BATTERY_INTERVAL = 60.0

#: A reading at or above this arms both notices (battery.BATTERY_WARNINGS) again and takes one
#: showing down, for a charge the daemon never read (the owner's decision of 2026-09-28). It is
#: 20 points over the 30 % notice, so the percentage's rise when the lights go off does not
#: reach it.
BATTERY_REARM = 50
#: Adwaita's battery-level icons come in steps of ICON_STEP percent, and a notice shows the
#: step at or below its threshold.
BATTERY_ICON = "battery-level-{}-symbolic"
ICON_STEP = 10
#: Why the last notice was not posted when the notification server refused it.
BATTERY_REFUSED = "the notification server refused it"

#: The refusal of a number that is no M profile.
NOT_AN_M_PROFILE = f"profile must be {PROFILE_CHOICES}"

#: How long the loop waits before looking for a missing keyboard again.
RECONNECT_DELAY = 2.0

#: Why a virtual device is missing, after the colon of each problem that says what stops
#: working without it.
UINPUT_NOT_OPENED = "/dev/uinput could not be opened"

#: A power-on report comes in pairs; one re-apply per this many seconds answers both.
REAPPLY_INTERVAL = 2.0

#: What a lighting change made while the lights are down says: it is saved, and shows when they
#: are back. IDLE_NOTE when the idle keyboard alone put them down; a preview's drops "saved; "
#: (lights_note).
LIGHTS_DOWN_NOTE = {
    DIM: "saved; the lights are dimmed while the screen is locked or blank",
    DARK: "saved; the lights are off while the screen is locked or blank",
}
IDLE_NOTE = {
    DIM: "saved; the keyboard is dimmed until the next keypress",
    DARK: "saved; the keyboard's lights are off until the next keypress",
}


#: 0x1D4B WirelessStatus. A device that comes back sends an unprompted fn 0 report whose
#: second byte asks to be configured again and whose third is 1 for "power switch
#: activated": G305 `01 01 01`, measured 2026-09-22, as in G HUB's capture and Solaar.
F_WIRELESS = 0x1D4B


class HeadsetError(RuntimeError):
    """A headset command the daemon could not carry out. Raised from the
    AstroFailure that caused it, which `headset_refusal` reads, or alone as
    a finished sentence."""


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
    """Log `line`, an attempt's on the device node `node`, at `level`, or at debug where it is
    the last line `said` keeps for that node, and keep it there: an attempt is tried again
    every RECONNECT_DELAY, and two nodes tried in one attempt each keep their own."""
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
    """A request's value as int() reads it, or ValueError saying what `command` wants:
    int()'s own refusal names Python's types, and its OverflowError is for an infinity,
    which json.loads makes of 1e400."""
    try:
        # The stub takes only what int() can read; anything else raises TypeError, caught here.
        return int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{command} wants a whole number, not {written(value)}") from None


def name_text(value: object, where: str) -> str:
    """The name a request looks something up by, as text: text as it is, and a number as
    str() writes it, so a game profile named "2" loads for a JSON 2. Anything else is
    refused in expect's words: str() would look a null up as the name "None"."""
    if not number(value):
        expect(value, str, where, "text")
    return str(value)


def checked_layer(layer: object) -> dict:
    """A request's `layer` as the window sends it, or TypeError: an object whose profile is
    an M profile's number and whose overlay is a game profile's name, each null or left out.
    check_layer compares them with ==, by which true is M1, and its refusal names the
    overlay, which str() of a list would show in Python's brackets."""
    if not isinstance(layer, dict):
        raise TypeError("layer must be an object with profile and overlay")
    profile, overlay = layer.get("profile"), layer.get("overlay")
    if not (profile is None or whole(profile)):
        raise TypeError(f"layer.profile must be an M profile's number or null, not "
                        f"{written(profile)}")
    if not (overlay is None or isinstance(overlay, str)):
        raise TypeError(f"layer.overlay must be a game profile's name or null, not "
                        f"{written(overlay)}")
    return layer


def switch_refusal(command: str, value: object) -> str:
    """What a switch command says to a value on_or_off could not read."""
    if isinstance(value, str):
        return f"{command} wants on or off, not {value.strip().lower()!r}"
    return f"{command} wants on or off"


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
        return f"command: must be text, not {kind_of(request['command'])}"
    return f"a request must be a JSON object, not {kind_of(request)}"


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
    return refusal(f"Gearbench could not reach the {device} ({reason(error)}).",
                   failure)


#: hidpp_refusal's sentences, each with the device's name put in.
DEVICE_GONE = "The {} went away."
DEVICE_NO_REPLY = "The {} did not answer. It may be asleep or switched off."
DEVICE_OFF = "The {} is switched off or out of range."
DEVICE_REFUSED = "The {} refused that."


def hidpp_refusal(device: str, failure: HidppFailure) -> dict:
    """A HID++ failure's reply, worded for `device`, the name put in its sentences."""
    if isinstance(failure, DeviceGone):
        return refusal(DEVICE_GONE.format(device), failure)
    if isinstance(failure, HidppTimeout):
        return refusal(DEVICE_NO_REPLY.format(device), failure)
    if unreachable(failure):
        # The receiver's answer, not the device's: it is not there.
        return refusal(DEVICE_OFF.format(device), failure)
    return not_reached(device, failure) or refusal(DEVICE_REFUSED.format(device), failure)


def mouse_refusal(exc: MouseError) -> dict:
    """A mouse failure's reply, told by the HID++ failure mouse_call chained as its cause; a
    MouseError with no cause is already a sentence. One chained from another MouseError, as
    flash_write's sector left unknown, follows that one's reply."""
    cause = exc.__cause__
    if isinstance(cause, HidppFailure):
        return hidpp_refusal("mouse", cause)
    if isinstance(cause, MouseError):
        reply = mouse_refusal(cause)
        reply["error"] = f"{reply['error'].rstrip('.')}. {exc}"
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


def same_profile(one: str, other: str) -> bool:
    """Whether two game profile names mean the same profile: the library
    matches on the filename, so "Team Fortress 2" is "team-fortress-2"."""
    return slug(one) == slug(other)


def node_denied(path: Path) -> bool:
    """Whether a device node is there and this user may not open it: its udev
    rule is missing or not yet applied, or another account or a text console has
    the seat. A node that has gone is only absent."""
    return path.exists() and not os.access(path, os.R_OK | os.W_OK)


class Answered(NamedTuple):
    """A keyboard that answered its probe (open_keyboard)."""

    #: Its node, as keyboard.find_attached_keyboard gives it.
    found: tuple[Path, int, int]
    #: The open device, and the index it answered on.
    opened: tuple[Hidpp, int]


def open_keyboard(found: tuple[Path, int, int]) -> tuple[Hidpp, int] | None:
    """The keyboard at `found`, (node, product id, derived index) as
    keyboard.find_attached_keyboard gives it, open on the index that answers its model's
    witness (hidpp.open_device), the derived one first, on MOUSE_PROBE, with that index; None
    where nothing answers, as on a receiver's node with its keyboard switched off. Raises
    PermissionError for a node the udev rule did not grant."""
    path, product, derived = found
    model = KEYBOARDS[product]
    return open_device(path, model.probe, prefer=derived, long_only=model.long_only,
                       **MOUSE_PROBE)


class Lookout:
    """When a controller looks again for its missing device: `first` seconds after a look,
    twice the last wait after each further look whose node gave no answer, up to `longest`,
    and at once when that node is gone, since the problem it is reported under asks for an
    unplug and a replug."""

    def __init__(self, first: float, longest: float) -> None:
        self.first, self.longest = first, longest
        #: The node whose probe got no answer, None while none did.
        self.unanswered: Path | None = None
        self.retry_at = 0.0
        #: The wait after the next look.
        self.wait = first

    def due(self) -> bool:
        gone = self.unanswered is not None and not self.unanswered.exists()
        return gone or time.monotonic() >= self.retry_at

    def looked(self) -> None:
        """Time the next look from the end of this one."""
        if self.unanswered is not None:
            wait, self.wait = self.wait, min(self.wait * 2, self.longest)
        else:
            wait = self.wait = self.first
        self.retry_at = time.monotonic() + wait

    def recheck(self) -> None:
        """After a resume: due at once, since time.monotonic() stops while the machine
        sleeps."""
        self.retry_at = min(self.retry_at, time.monotonic())


class BatteryNotices:
    """A device's low-battery notices (warn_battery), the keyboard's (KeyboardBattery) and the
    headset's (Headset), one rule for both. A subclass sets the words and battery_names();
    the session's desktop posts the notices."""

    #: The last percentage on battery, the lowest threshold warned of, the notice showing,
    #: and why the last one could not be posted, so a repeat of it is said at debug.
    _battery_before: int | None = None
    battery_warned: int | None = None
    _battery_notice: int | None = None
    _battery_unposted: str | None = None
    #: The journal's prefix for the device's lines, its word in "the <word>'s battery", why
    #: both are armed again on power, and the notice's last sentence.
    BATTERY_PREFIX: str
    BATTERY_DEVICE: str
    BATTERY_POWERED: str
    BATTERY_BODY: str
    #: The daemon, set by the subclass's __init__.
    d: Daemon

    def battery_names(self) -> tuple[str, str]:
        """The device's name in the notice, and the journal's possessive, as "the headset's"."""
        raise NotImplementedError

    def warn_battery(self, reading: dict) -> None:
        """A notice when two readings in a row are below a threshold not yet
        warned of; on external power or charging, or at BATTERY_REARM or more,
        arm both again and take it down."""
        if reading.get("external_power") or reading.get("charging"):
            self.battery_rearmed(self.BATTERY_POWERED)
            return
        percent = reading.get("percent")
        if not isinstance(percent, int | float):
            return
        # The whole percent every reading is shown as (`:.0f`), so a notice below 30 % never
        # says 30 % left.
        percent = round(percent)
        if percent >= BATTERY_REARM:
            self.battery_rearmed(f"the {self.BATTERY_DEVICE}'s battery is at {percent}%")
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
        name, owner = self.battery_names()
        lowest = threshold == min(BATTERY_WARNINGS)
        summary = f"{name} battery {'very low' if lowest else 'low'}"
        body = f"{percent}% left. {self.BATTERY_BODY}"
        before, self.battery_warned = self.battery_warned, threshold
        said = f"{self.BATTERY_PREFIX}{owner} battery is at {percent}%, below {threshold}%"

        def done(notice: int | None) -> None:
            if notice is None:
                if self.battery_warned == threshold:
                    self.battery_warned = before
                self._battery_unposted = BATTERY_REFUSED
            elif self.battery_warned is None:
                session.desktop.close_notification(notice)     # armed again since
            else:
                self._battery_notice = notice

        icon = BATTERY_ICON.format(threshold // ICON_STEP * ICON_STEP)
        if not session.desktop.notify(summary, body, icon,
                                      replaces_id=self._battery_notice or 0, done=done):
            self.battery_warned = before
            self.battery_unposted(said)
            return
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
