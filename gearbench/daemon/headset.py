"""The Astro A50 Gen 4's base station: finding it, its settings in RAM and
flash, the headset's link and battery, the Headset tab's commands, and the
reply one of them gets when the base station fails it (`headset_refusal`).

Not HID++: gearbench.astro speaks the base's own protocol. The base answers
each request once and sends nothing unasked, so between requests the loop
reads its node only for a save's answer, which comes after the loop has moved
on, and for a reply that came after its request gave up.
"""

from __future__ import annotations

import contextlib
import logging
import selectors
import time
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ..astro import (
    A50,
    BANDS,
    NAME,
    NO_SETTING,
    NOT_ANSWERING,
    PRESET_SETTLE,
    PRESETS,
    PRODUCTS,
    READS_SAVED,
    SAVE_OK,
    SAVE_RETRY,
    SAVE_RETRY_DELAY,
    SAVE_TIMEOUT,
    SAVING,
    SETTING_NAMES,
    SETTING_WORDS,
    SETTINGS,
    AstroError,
    AstroFailure,
    AstroTimeout,
    BaseGone,
    NodeFailure,
    band_width,
    find_base,
    open_a50,
    preset_gains,
    preset_name,
    preset_number,
    read_setting,
)
from ..battery import BASE_CHARGE, astro as astro_battery
from ..hidpp import NO_RULE
from ..tray import Battery
from ..wording import reason, route, whole, word_list, written
from .shared import (
    DEVICE_NOT_SET_UP,
    KEYBOARD_BATTERY_INTERVAL,
    MOUSE_SILENT_RECHECK,
    NODE_DENIED,
    NOT_UNDERSTOOD,
    AttachNotes,
    BatteryNotices,
    Lookout,
    attached_entry,
    failure_reason,
    look_due,
    name_text,
    not_reached,
    refusal,
    whole_number,
    with_note,
)

if TYPE_CHECKING:
    from . import Daemon

log = logging.getLogger("gearbench")

#: Seconds between asks whether the headset is on (54) and, while it is, for the live
#: balance (72) and active preset (6C), which the headset's buttons change unasked: three
#: short requests the base answers itself, none relayed. Command Center asks every 2 s;
#: the window asks too while its tab shows.
HEADSET_STATUS_INTERVAL = 5.0

#: No base station found, with what to do, in the keyboard's and the mouse's shape: only PC
#: mode has the control interface.
NO_BASE = f"no {NAME} base station found; plug it in on USB, its switch on PC"
#: NODE_DENIED's words for the base station, its model named as the A50 X's is.
BASE_WORDS = f"{NAME}'s base station"
#: A sentence of a revert's note, as NOT_REVERTED is: the window toasts a short note as it is.
NO_SAVED_VALUE = "Auto shut-off has no saved value to go back to."
#: A revert's note of each refused value the read after it did not find back at its flash
#: value, with why: Revert to saved's word, since Put back is the first-read values' (the
#: Centurion's and the G432's).
NOT_REVERTED = "Not reverted: {}."
#: The base answered with an error, named by the error's name.
REFUSED = f"the {BASE_WORDS} refused that ({{}})"

#: What the loop catches around a connect or a pass: the base's own
#: failures, a node that failed, and a reply too short to parse.
FAILURES = (OSError, AstroFailure, ValueError, IndexError)


class HeadsetError(RuntimeError):
    """A headset command the daemon could not carry out. Raised from the
    AstroFailure that caused it, which `headset_refusal` reads, or alone as
    a finished sentence."""


@dataclass
class Save:
    """A save in flight: 61's seq, its deadline, and the retry the base may ask for once."""

    seq: int
    deadline: float
    retried: bool = False
    retry_at: float | None = None


def refused(exc: HeadsetError) -> bool:
    """Whether the base answered and would not do it: an error reply or one Gearbench cannot
    read. A node that failed is no answer."""
    cause = exc.__cause__
    return isinstance(cause, AstroFailure) and not isinstance(
        cause, (BaseGone, AstroTimeout, NodeFailure))


def differs(entry: dict | None) -> bool:
    """Whether a setting's or a preset part's RAM value is not its flash value; False for one
    not read (None). A setting outside READS_SAVED has no flash value and is never passed."""
    return entry is not None and entry["active"] != entry["saved"]


def preset_differs(eq: dict) -> bool:
    return differs(eq["name"]) or differs(eq["gains"]) or any(map(differs, eq["bands"]))


def setting_value(setting: str, value: object) -> int | str:
    """A request's value as A50.set and set_preset take it; A50 checks the ranges."""
    if setting in SETTING_WORDS:
        if not isinstance(value, str):
            raise ValueError(f"{setting} is {word_list(SETTING_WORDS[setting])}, "
                             f"not {written(value)}")
        return value.strip().lower()
    return whole_number(value, setting)


def eq_change(request: dict, active: int | None
              ) -> tuple[int | None, str | None, list[int] | None,
                         list[tuple[int, int, float | None]]]:
    """headset_eq's preset (`active` when the request names none), name, gains and bands, all
    checked before anything is written, so a bad band leaves the name unwritten too. Raises
    ValueError with the sentence."""
    preset = request.get("preset")
    preset = active if preset is None else preset_number(preset)
    name, gains, bands = request.get("name"), request.get("gains"), request.get("bands")
    if name is None and gains is None and bands in (None, []):
        raise ValueError("give a name, gains or bands to change")
    # The setters' own checks (astro), run here before the first write.
    if name is not None:
        preset_name(name)
    if gains is not None:
        preset_gains(gains)
    changes: list[tuple[int, int, float | None]] = []
    if bands is not None and not isinstance(bands, list):
        raise ValueError(f"the bands are a list, not {written(bands)}")
    for band in bands or ():
        # One condition: ruff's TRY004 flags a ValueError raised on an isinstance test alone.
        if not isinstance(band, dict) or not band:
            # An object here is an empty one, shown as it is written.
            given = "{}" if isinstance(band, dict) else written(band)
            raise ValueError(f"each band is an object of band, freq and width, not {given}")
        number, freq, width = band.get("band"), band.get("freq"), band.get("width")
        if not whole(number) or number not in BANDS or number in {c[0] for c in changes}:
            raise ValueError(f"a band is 1 to {len(BANDS)}, each once, not {written(number)}")
        band_width(number, freq, width)
        # The frequency is a whole number: astro.band_width refuses any other. The width to a
        # tenth, which is all the read shows (A50.band): a finer one would differ from the
        # flash unseen.
        changes.append((number, cast("int", freq),
                        None if width is None else round(float(width), 1)))
    return preset, name, gains, changes


def save_failed(exc: HeadsetError) -> str:
    """What `save_error` says when 61 or its answer failed. A base gone is not here:
    headset_call lets it go, and its save with it."""
    cause = exc.__cause__
    if isinstance(cause, AstroTimeout):
        return "the base station did not answer the save"
    if isinstance(cause, AstroError):
        return f"the base station refused to save ({cause.name})"
    if isinstance(cause, NodeFailure):
        return ("Gearbench could not reach the base station during the save "
                f"({failure_reason(cause)})")
    return "the base station answered the save in a way Gearbench does not understand"


def headset_refusal(exc: HeadsetError) -> dict:
    """A headset failure's reply, told by the failure headset_call chained as its cause; a
    HeadsetError with no cause is already a sentence."""
    cause = exc.__cause__
    # Each names the model, as the A50 X family's refusals do (BASE_WORDS).
    if isinstance(cause, BaseGone):
        return refusal(f"the {BASE_WORDS} went away", cause)
    if isinstance(cause, AstroTimeout):
        return refusal(f"the {BASE_WORDS} did not answer; if this goes on, unplug it and plug "
                       "it back in", cause)
    if isinstance(cause, AstroError):
        return refusal(REFUSED.format(cause.name), cause)
    if isinstance(cause, AstroFailure):
        return (not_reached(BASE_WORDS, cause)
                or refusal(NOT_UNDERSTOOD.format(BASE_WORDS), cause))
    return {"ok": False, "error": str(exc)}


class Headset(BatteryNotices):
    """The base station while one is attached, and what it last said."""

    BATTERY_PREFIX = "headset: "
    BATTERY_POWERED = "is charging"
    BATTERY_BODY = BASE_CHARGE

    def __init__(self, daemon: Daemon) -> None:
        self.d = daemon
        #: The base's commands, its node, its product id and its version (03, 55).
        self.a50: A50 | None = None
        self.path: Path | None = None
        self.product: int | None = None
        self.version: str | None = None
        #: When to look for a base again, and the node found that does not answer, which
        #: problems() reads too.
        self._lookout = Lookout()
        #: A node refused to this user and why a found base could not be set up, for
        #: problems() and the journal (AttachNotes).
        self._notes = AttachNotes()
        #: The last pass failure said at info (tend), so a repeat says it at debug.
        self._said: str | None = None
        self._clear()

    def _clear(self) -> None:
        """Forget what the attached base said. The battery notices' state stays: a replug of
        the base does not charge the headset."""
        self.headset_version: str | None = None
        self.docked = self.linked = False
        self.silent = False
        self._recheck_at = 0.0
        self._status_at = 0.0
        self._battery_due = 0.0
        self.battery: dict | None = None
        self.battery_wall: int | None = None
        self.balance: int | None = None
        #: By setting, {"active", "saved"}, or {"active"} alone outside
        #: READS_SAVED; a setting the base would not read is left out.
        self.settings: dict[str, dict] = {}
        self.preset: int | None = None
        #: By preset, as `headset` answers it.
        self.presets: dict[int, dict] = {}
        #: Auto shut-off, outside READS_SAVED, once written since the last
        #: save or the connect: nothing else says it is unsaved.
        self._written: set[str] = set()
        self.saving: Save | None = None
        self.save_error: str | None = None
        self.read_wall: int | None = None
        #: A full read owed, since the cache may be stale (each place that sets it says
        #: why): done at the next status answer while linked.
        self._read_owed = False
        #: Until then 6C still reports the preset before the last 67.
        self._settle_until = 0.0
        #: The headset's version asked and the ask timed out or its node failed: a full read
        #: outside the status poll clears _read_owed without asking it.
        self._version_owed = False

    def commands(self) -> dict[str, Callable[[dict], dict]]:
        return {
            "headset": self._cmd_headset,
            "headset_set": self._cmd_headset_set,
            "headset_eq": self._cmd_headset_eq,
            "headset_save": self._cmd_headset_save,
            "headset_revert": self._cmd_headset_revert,
        }

    @property
    def name(self) -> str:
        # Read only with a base attached: connect sets the product with it, and teardown
        # takes the name before it clears the product.
        return PRODUCTS[cast("int", self.product)]

    def attached_name(self) -> str | None:
        """The base's name, None with none attached."""
        return self.name if self.a50 is not None else None

    def answers(self) -> bool:
        """Whether the base attached answers, as `headset`'s `answering` gives it."""
        return not self.silent

    def status_entry(self) -> dict | None:
        """`status`'s entry for the base attached (attached_entry), with `linked`, which dates
        a reading kept while the headset is off its link (battery.kept_at); None with none."""
        if self.a50 is None:
            return None
        return {**attached_entry(self.name, str(self.path), route("base station"), self.battery,
                                 self.battery_wall, answering=self.answers()),
                "linked": self.linked}

    def tray_battery(self) -> Battery | None:
        """The tray's line for the headset with a reading: while it is not live, with the
        headset off its link or the base silent, the last one with when it was taken, as the
        window's Battery row has it (Battery.of drops the time while it is live). Switched off
        on its base station the headset charges (the dock re-arms the notices in _take_status
        too), so the reading kept from before counts as plugged in and is not a low battery."""
        if self.a50 is None or self.battery is None:
            return None
        shown = Battery.of("headset", self.name, self.battery,
                           answering=self.linked and not self.silent, read_at=self.battery_wall)
        return replace(shown, plugged=True) if self.docked and not self.linked else shown

    def deadlines(self) -> list[float]:
        if self.a50 is None:
            return self._lookout.deadlines(self.d.plugs.pace)
        if self.saving is not None:
            save = self.saving
            return [save.retry_at if save.retry_at is not None else save.deadline]
        if self.silent:
            return [self._recheck_at]
        return [self._status_at, *([self._battery_due] if self.linked else [])]

    # -- attaching ---------------------------------------------------------

    def maybe_connect(self) -> None:
        """Look for a base once the retry is due, or at once when a base that did not answer has
        lost its node; the next look is timed from the attempt's end."""
        # Unplugged, as NOT_ANSWERING asks: looked for now, so it stops being said, and its
        # replug is not left to the doubled wait.
        if self.a50 is not None or not self._lookout.due():
            return
        try:
            self.connect()
        finally:
            self._lookout.looked()

    def connect(self) -> None:
        """Attach the base find_base finds if it answers the probe, 03, 55 and 54, on the base's
        own timeout. Never raises. With the headset on, its version, battery and every setting are
        read before the node is registered; the gate sorts out what they fail on."""
        self._notes.looking()
        self._lookout.unanswered = None
        try:
            found = find_base()
        except OSError as exc:
            log.debug("headset: could not look for a base station: %s", exc)
            found = None
        if found is None:
            return
        path, product = found
        started = time.monotonic()
        try:
            a50 = open_a50(path)
        except PermissionError as exc:
            self._notes.refused(path, NODE_DENIED.format(path, BASE_WORDS),
                                f"headset: cannot open the base station's {path}: "
                                f"{reason(exc)}; {NO_RULE}")
            return
        except OSError as exc:
            log.debug("headset: base station not reachable on %s: %s", path, exc)
            return
        self._notes.opened(path)
        try:
            version = a50.info()["version"]
            docked, linked = a50.status()
        except FAILURES as exc:
            self._close(a50)
            quiet = isinstance(exc, AstroTimeout)
            self._lookout.unanswered = path if quiet else None
            # Kept apart from the lookout's node, whose wait doubles; a base gone is none found.
            self._notes.not_set_up = None if quiet or isinstance(exc, BaseGone) else (
                DEVICE_NOT_SET_UP.format(PRODUCTS[product], reason(exc), "its base station"))
            why = "no answer to the probe" if quiet else f"could not set it up: {reason(exc)}"
            self._notes.failed(path, f"headset: the base station on {path}: {why}")
            self.d.note_slow(started, f"headset connect on {path}", why, repeats="headset")
            return
        self._notes.attached()
        self._said = None
        self._clear()
        self.a50, self.path, self.product, self.version = a50, path, product, version
        self._status_at = time.monotonic() + HEADSET_STATUS_INTERVAL
        try:
            self._take_status(docked, linked)
        except (HeadsetError, *FAILURES) as exc:
            log.info("headset: setting up the base station on %s stopped: %s", path, reason(exc))
        if self.a50 is not a50:
            return                  # it went away during the setup
        self.d.selector.register(a50.base.fileno, selectors.EVENT_READ, "headset")
        log.info("headset: connected on %s (%s, base %s)", path, self.name, version)
        self.d.forget_slow("headset")
        self.d.note_slow(started, f"{self.name} connect on {path}")

    @staticmethod
    def _close(a50: A50) -> None:
        with contextlib.suppress(OSError):
            a50.base.close()

    def teardown(self) -> str | None:
        """Let go of the base: nothing is sent to it, since its settings stay
        in its RAM whoever holds the node. Answers what was let go, for
        shutdown's line, or None with none attached; never raises."""
        a50, self.a50 = self.a50, None
        if a50 is None:
            return None
        name = self.name
        with contextlib.suppress(KeyError, ValueError):
            self.d.selector.unregister(a50.base.fileno)
        self._close(a50)
        self._clear()
        self.path = self.product = self.version = None
        return f"{name} let go"

    def recheck(self) -> None:
        """After a resume: the status poll, or a silent base's recheck, due
        now, a full read owed, and a missing base looked for."""
        self._status_at = self._recheck_at = time.monotonic()
        self._lookout.recheck()
        # A base that lost power in the suspend may keep its node, its RAM back at flash values.
        self._read_owed = True

    # -- what problems() and the refusals read -----------------------------

    def problems(self) -> list[tuple[str, str]]:
        """The base station's entries for the daemon's `problems`, as (code, message). Nothing
        is sent to it."""
        found: list[tuple[str, str]] = []
        if (denied := self._notes.denied_problem()) is not None:
            found.append(("headset_no_permission", denied))
        if self._lookout.unanswered is not None or self.silent:
            found.append(("headset_not_answering", NOT_ANSWERING))
        if self._notes.not_set_up is not None:
            found.append(("headset_not_set_up", self._notes.not_set_up))
        return found

    def missing(self) -> str:
        """Why no command can go to a base, with none attached, as a sentence: the first of its
        problems (a base marked not answering is an attached one), or NO_BASE."""
        return next((message for _code, message in self.problems()), NO_BASE)

    # -- the gate ------------------------------------------------------------

    def headset_call[**P, T](self, what: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
        """Run one piece of work on the base and sort out a failure, as mouse_call does for a
        mouse. Only a node gone tears the base down; a base that does not answer is sent
        nothing but the recheck until it does. Each AstroFailure is raised as HeadsetError from
        it, which headset_refusal words; a ValueError, a value refused before anything is sent,
        passes through."""
        try:
            result = what(*args, **kwargs)
        except BaseGone as exc:
            log.info("headset: the base station went away: %s", failure_reason(exc))
            self.teardown()
            raise HeadsetError(f"the base station went away: {failure_reason(exc)}") from exc
        except AstroTimeout as exc:
            self.mark_silent()
            raise HeadsetError(f"the base station is not answering: {exc}") from exc
        except NodeFailure as exc:
            # A write that failed may follow one that took: read it all again.
            self._read_owed = True
            raise HeadsetError(str(exc)) from exc
        except AstroError as exc:
            raise HeadsetError(f"the base station refused: {exc}") from exc
        except AstroFailure as exc:
            raise HeadsetError(f"the base station's answer could not be read: {exc}") from exc
        if self.silent:
            log.info("headset: the base station is answering again")
            self.silent = False
        return result

    def _unless_refused[**P, T](self, what: Callable[P, T], *args: P.args,
                                **kwargs: P.kwargs) -> T | None:
        """headset_call, with a refusal logged and answered None; any other failure still
        raises, which stops the caller."""
        try:
            return self.headset_call(what, *args, **kwargs)
        except HeadsetError as exc:
            if not refused(exc):
                raise
            log.debug("headset: %s", exc)
            return None

    def mark_silent(self) -> None:
        if self.silent:
            return
        log.info("headset: the base station is not answering; asking again in %d s",
                 MOUSE_SILENT_RECHECK)
        self.silent = True
        self._recheck_at = time.monotonic() + MOUSE_SILENT_RECHECK

    def ready(self) -> A50:
        """The base, for a command that sends to it. HeadsetError with the
        sentence when there is none, it is not answering, or it is saving."""
        if self.a50 is None:
            raise HeadsetError(self.missing())
        if self.silent:
            raise HeadsetError(NOT_ANSWERING)
        if self.saving is not None:
            raise HeadsetError(SAVING)
        return self.a50

    @property
    def _a50(self) -> A50:
        """The base, for the passes and the reads, which run only while one is attached: the
        type checker does not follow a check made in another method, and this cast checks
        nothing."""
        return cast("A50", self.a50)

    # -- the loop's passes ---------------------------------------------------

    def tend(self) -> None:
        """Once a pass: a save's retry and deadline, a silent base's recheck,
        the status poll and the battery. Never raises."""
        if self.a50 is None:
            return
        try:
            self._tend(time.monotonic())
        except HeadsetError as exc:
            # headset_call logs a base gone or not answering. A refusal of any request but 54
            # never gets here (_unless_refused); any other failure is said here, at info once
            # until a poll goes through (_poll_status).
            said = f"headset: {exc}"
            if isinstance(exc.__cause__, (BaseGone, AstroTimeout)) or said == self._said:
                log.debug("%s", said)
            else:
                log.info("%s", said)
                self._said = said
        except FAILURES as exc:
            log.info("headset: a pass over the base station stopped: %s", reason(exc))

    def _tend(self, now: float) -> None:
        if self.saving is not None:
            self._tend_save(now)
            return
        if self.silent:
            if now >= self._recheck_at:
                self._recheck_at = now + MOUSE_SILENT_RECHECK
                self._poll_status(now)
            return
        if look_due(now, self._status_at):
            self._poll_status(now)
        if self.linked and look_due(time.monotonic(), self._battery_due):
            self._read_battery()

    def pending(self) -> bool:
        """Whether reports a request read are queued: never, since the base's node keeps no
        queue (astro.Base.answer drops what is not its answer)."""
        return False

    def pump(self) -> None:
        """The node is readable: a save's answer, or a reply that came after
        its request gave up, which is dropped. Never raises; a node that
        fails to read is torn down, or select would wake for it forever."""
        if self.a50 is None:
            return
        try:
            if self.saving is not None and self.saving.retry_at is None:
                self._take_save_answer()
            else:
                self.a50.base.pump()
        except HeadsetError as exc:
            log.debug("headset: %s", exc)
        except BaseGone as exc:
            log.info("headset: the base station went away: %s", failure_reason(exc))
            self.teardown()
        except (OSError, AstroFailure) as exc:
            log.info("headset: %s; letting the base station go", reason(exc))
            self.teardown()
        except (ValueError, IndexError) as exc:
            log.info("headset: %s", exc)

    def _poll_status(self, now: float) -> bool:
        """54 and what its change means (_take_status), then while linked 72 and 6C, but 6C
        not after the full read _take_status may do, which asks it. Answers whether it did one."""
        self._status_at = now + HEADSET_STATUS_INTERVAL
        docked, linked = self.headset_call(self._a50.status)
        fresh = self._take_status(docked, linked)
        if self.linked:
            self.balance = self._unless_refused(self._a50.balance)
            if not fresh:
                self._read_active_preset()
        self._said = None           # a failure after this one is said again (tend)
        return fresh

    def _take_status(self, docked: bool, linked: bool) -> bool:
        """Take 54's answer. The link coming up owes a full read, done while linked (answering
        True); one a timeout cuts short stays owed. The headset's version is asked first, and
        after an ask that timed out or whose node failed, at each answer while linked until an
        ask returns, a refusal included. A change of the dock makes the battery due."""
        if docked != self.docked:
            # 7C's charging bit follows the dock below 100 %, and at 100 % astro_battery takes
            # the state from the dock: a reading from before the change is stale.
            self._battery_due = 0.0
        was, self.docked, self.linked = self.linked, docked, linked
        if docked:
            # 54 says docked with the headset off too, when 7C's charging bit is not read.
            self.warn_battery({"external_power": True})
        if linked and not was:
            log.info("headset: the headset is on and linked to its base station")
            self._read_owed = True
        elif was and not linked:
            log.info("headset: the headset's link went down: switched off or out of range")
            self.balance = None
        if linked and self.headset_version is None and (self._read_owed or self._version_owed):
            self._version_owed = True
            self.headset_version = self._unless_refused(self._a50.headset_version)
            self._version_owed = False
        if not (linked and self._read_owed):
            return False
        self._read_battery()
        self._read_settings()
        return True

    # -- reading -------------------------------------------------------------

    def _read_battery(self) -> None:
        self._battery_due = time.monotonic() + KEYBOARD_BATTERY_INTERVAL
        answer = self._unless_refused(self._a50.battery)
        if answer is None:
            return
        self.battery = astro_battery(*answer, docked=self.docked)
        self.battery_wall = round(time.time())
        self.warn_battery(self.battery)

    def _read_setting(self, name: str) -> None:
        value = self._unless_refused(read_setting, self._a50, name)
        if value is None:
            self.settings.pop(name, None)
        else:
            self.settings[name] = value

    @staticmethod
    def _preset_eq(a50: A50, preset: int) -> dict:
        name = {"active": a50.name(preset), "saved": a50.name(preset, saved=True)}
        active, saved = a50.gains(preset)
        bands = []
        for band in BANDS:
            now, flash = a50.band(preset, band)
            bands.append({"band": band, "active": asdict(now), "saved": asdict(flash)})
        return {"preset": preset, "name": name,
                "gains": {"active": active, "saved": saved}, "bands": bands}

    def _read_preset_eq(self, preset: int) -> None:
        eq = self._unless_refused(self._preset_eq, self._a50, preset)
        if eq is None:
            self.presets.pop(preset, None)
        else:
            self.presets[preset] = eq

    def _read_active_preset(self) -> None:
        if time.monotonic() >= self._settle_until:
            self.preset = self._unless_refused(self._a50.preset)

    def _read_settings(self) -> None:
        """Read every setting, every preset and, unless a 67 is within PRESET_SETTLE, the active
        one, each on its own: one the base refuses is left out. Any other failure stops the read,
        which stays owed."""
        self._read_owed = True
        for name in SETTINGS:
            self._read_setting(name)
        for preset in PRESETS:
            self._read_preset_eq(preset)
        self._read_active_preset()
        self.read_wall = round(time.time())
        self._read_owed = False

    def unsaved(self) -> list[str]:
        """What differs from the base's flash: a setting whose RAM and flash
        values differ, "preset_N" for a preset whose name, gains or a band
        does, and a setting without a flash value written since the last
        save or the connect."""
        found = [name for name in READS_SAVED if differs(self.settings.get(name))]
        found += [f"preset_{p}" for p, eq in self.presets.items() if preset_differs(eq)]
        return sorted([*found, *self._written])

    def reply(self) -> dict:
        """`headset`'s answer from what the base last said."""
        versions = {"base": self.version}
        if self.headset_version is not None:
            versions["headset"] = self.headset_version
        return {
            "ok": True,
            "device": str(self.path),
            "name": self.name,
            "product": f"{self.product:04x}",
            "answering": self.answers(),
            "docked": self.docked,
            "linked": self.linked,
            "versions": versions,
            "battery": self.battery,
            "battery_at": self.battery_wall,
            "balance": self.balance,
            "settings": {name: self.settings[name] for name in SETTINGS if name in self.settings},
            "preset": self.preset,
            "presets": [self.presets[p] for p in PRESETS if p in self.presets],
            "unsaved": self.unsaved(),
            "saving": self.saving is not None,
            "save_error": self.save_error,
            "read_at": self.read_wall,
        }

    # -- the battery notices -------------------------------------------------

    def battery_names(self) -> tuple[str, str]:
        return self.name, "the headset"

    # -- saving ----------------------------------------------------------------

    def _tend_save(self, now: float) -> None:
        # _tend runs this only with a save in flight.
        save = cast("Save", self.saving)
        if save.retry_at is not None:
            if now < save.retry_at:
                return
            save.retry_at = None
            try:
                save.seq = self.headset_call(self._a50.start_save)
            except HeadsetError as exc:
                self._save_over(save_failed(exc))
                return
            save.deadline = time.monotonic() + SAVE_TIMEOUT
            log.info("headset: the base station asked for the save again; sent once more")
        elif now >= save.deadline:
            # tend runs before select: an answer can be waiting unread.
            self._take_save_answer()
            if self.saving is save and save.retry_at is None:
                self._save_over("the base station did not say it had saved")

    def _take_save_answer(self) -> None:
        # pump and _tend_save run this only with a save in flight.
        save = cast("Save", self.saving)
        try:
            code = self.headset_call(self._a50.save_result, save.seq)
        except HeadsetError as exc:
            self._save_over(save_failed(exc))
            return
        if code is None:
            return
        if code == SAVE_OK:
            self.saving = None
            self._written.clear()
            log.info("headset: saved to flash")
            self._read_settings()
        elif code == SAVE_RETRY and not save.retried:
            save.retried = True
            save.retry_at = time.monotonic() + SAVE_RETRY_DELAY
        else:
            self._save_over(f"the base station did not save (it answered {code})")

    def _save_over(self, error: str) -> None:
        if self.a50 is None:
            return          # torn down, and the save with it
        self.saving = None
        self.save_error = error
        # Whether the flash was written is not known: the cache's saved values may be stale.
        self._read_owed = True
        log.warning("headset: %s", error)

    # -- commands --------------------------------------------------------------

    def _cmd_headset(self, request: dict) -> dict:
        # Attaches nothing: the loop does. Answers from what the base last said, with nothing
        # sent, when `ask` is false (a client's request for just that), the base is not
        # answering or a save is in flight.
        if self.a50 is None:
            return {"ok": False, "error": self.missing()}
        if request.get("ask", True) is not False and not self.silent and self.saving is None:
            try:
                self._ask(request.get("read") is True)
            except HeadsetError as exc:
                if not isinstance(exc.__cause__, AstroTimeout):
                    raise
        return self.reply()

    def _ask(self, read: bool) -> None:
        """The status poll now, then while linked the battery when due; with
        `read`, every setting and preset."""
        fresh = self._poll_status(time.monotonic())
        if self.linked and time.monotonic() >= self._battery_due:
            self._read_battery()
        if read and not fresh:
            self._read_settings()

    def _cmd_headset_set(self, request: dict) -> dict:
        """One setting, or `preset`, the active one, written to the base's RAM
        and read back; the flash is written only by headset_save.
        6C is not asked straight after a 67: it says the old preset."""
        setting = name_text(request["setting"], "headset_set.setting")
        setting = setting.strip().lower().replace("-", "_")
        if setting != "preset" and setting not in SETTINGS:
            return {"ok": False, "error": NO_SETTING.format(
                written(setting), word_list([*SETTINGS, "preset"], "and"))}
        try:
            value = setting_value(setting, request["value"])
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        a50 = self.ready()
        if setting == "preset":
            # A whole number: setting_value gives a word only to SETTING_WORDS' settings.
            preset = cast("int", value)
            try:
                self.headset_call(a50.set_preset, preset)
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            self.preset = preset
            self._settle_until = time.monotonic() + PRESET_SETTLE
            return self.reply()
        try:
            self._write_then_read(lambda: self.headset_call(a50.set, setting, value),
                                  lambda: self._read_setting(setting))
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        except HeadsetError:
            self._mark_written(setting)
            raise
        self._mark_written(setting)
        return self.reply()

    def _mark_written(self, setting: str) -> None:
        """A setting with no flash value to compare, once sent, even by a
        write that failed: unsaved until the next save."""
        if setting not in READS_SAVED:
            self._written.add(setting)

    def _write_then_read(self, write: Callable[[], object], read: Callable[[], object]) -> None:
        """`write`, then `read` back what it touched. A write that failed
        part way may still have reached the base's RAM: one the base refused
        is read back too, and after a timeout or a node that failed
        everything is read once the base answers (_read_owed)."""
        try:
            write()
        except HeadsetError as exc:
            if refused(exc):
                with contextlib.suppress(HeadsetError):
                    read()
            raise
        else:
            read()
        finally:
            if self.silent:
                self._read_owed = True

    def _cmd_headset_eq(self, request: dict) -> dict:
        """A preset's name, gains or bands, the active preset's by default,
        written to the base's RAM and that preset read back. The headset's EQ
        button switches presets unseen, so the active one is asked first,
        unless a 67 within PRESET_SETTLE says it better than 6C can."""
        a50 = self.ready()
        active = self.preset if time.monotonic() < self._settle_until else None
        try:
            preset, name, gains, bands = eq_change(request, active)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if preset is None:
            preset = self.preset = self.headset_call(a50.preset)

        def write() -> None:
            if name is not None:
                self.headset_call(a50.set_name, preset, name)
            if gains is not None:
                self.headset_call(a50.set_gains, preset, gains)
            for band, freq, width in bands:
                self.headset_call(a50.set_band, preset, band, freq, width)

        self._write_then_read(write, lambda: self._read_preset_eq(preset))
        return self.reply()

    def _cmd_headset_save(self, request: dict) -> dict:
        """Copy what the base holds in RAM to its flash: 61, answered on the
        node after more than 2 s, so the reply says only that it started
        (`saving`); pump takes the answer."""
        a50 = self.ready()
        seq = self.headset_call(a50.start_save)
        self.saving = Save(seq=seq, deadline=time.monotonic() + SAVE_TIMEOUT)
        self.save_error = None
        log.info("headset: saving to the base station's flash")
        return self.reply()

    def _parts(self, a50: A50) -> Iterator[tuple[str, dict, Callable[..., object],
                                                tuple[object, ...]]]:
        """Each setting and preset part the cache holds with its flash value: its name in
        headset_revert's note, a preset's by its slot, its entry, and the setter and
        arguments that write the flash value back."""
        for name in SETTINGS:
            entry = self.settings.get(name)
            if entry is not None and "saved" in entry:
                yield SETTING_NAMES[name], entry, a50.set, (name, entry["saved"])
        for preset, eq in sorted(self.presets.items()):
            yield (f"slot {preset}'s name", eq["name"], a50.set_name,
                   (preset, eq["name"]["saved"]))
            yield (f"slot {preset}'s gains", eq["gains"], a50.set_gains,
                   (preset, eq["gains"]["saved"]))
            for band in eq["bands"]:
                saved = band["saved"]
                yield (f"slot {preset}'s band {band['band']}", band, a50.set_band,
                       (preset, band["band"], saved["freq"], saved["width"]))

    def _cmd_headset_revert(self, request: dict) -> dict:
        """Every setting and preset that differs from the flash written back
        from it, then all read again. One the setter or the base refuses is
        named in the note and in the reply's `not_reverted`, unless the read
        after it finds it back at its flash value, and the rest go back. Auto
        shut-off has no flash value to read, so the note says so when it was
        written."""
        a50 = self.ready()
        if self.read_wall is None or self._read_owed:
            # Put back from what the base holds, not from a read never done.
            self._read_settings()
        refusals: dict[str, str] = {}

        def put_back() -> None:
            for what, entry, write, args in self._parts(a50):
                if not differs(entry):
                    continue
                try:
                    self.headset_call(write, *args)
                except (ValueError, HeadsetError) as exc:
                    if isinstance(exc, HeadsetError) and not refused(exc):
                        raise
                    # An error reply by its name, as headset_refusal gives it.
                    cause = exc.__cause__
                    refusals[what] = (f"the base station refused it: {cause.name}"
                                      if isinstance(cause, AstroError) else str(exc))

        self._write_then_read(put_back, self._read_settings)
        # An answer Gearbench cannot read counts as a refusal, though the base may have taken
        # the value: the read says whether it did.
        back = {what for what, entry, _write, _args in self._parts(a50) if not differs(entry)}
        missed = {what: why for what, why in refusals.items() if what not in back}
        # The refusal first: the window's toast shows only a long note's first sentence
        # (style.short_note).
        notes = []
        if missed:
            notes.append(NOT_REVERTED.format("; ".join(f"{what} ({why})"
                                                       for what, why in missed.items())))
        if self._written:
            notes.append(NO_SAVED_VALUE)
        # A part the read after its refused write dropped is in no `unsaved` list, so the
        # command line's exit status reads this one.
        return with_note({**self.reply(), "not_reverted": list(missed)}, " ".join(notes))
