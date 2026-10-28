"""The Logitech G432 and the other HID++ headsets of gearbench.g432's MODELS: finding one,
its firmware version, sidetone, battery, power-off time and the equalizer in the headset
of a model that has one, its lights where it lists them (lights.py, which gives the
`g432_lights` command), what its G keys do on it (key_action), and the `g432`,
`g432_sidetone`, `g432_auto_off` and `g432_headset_eq` commands.

A headset speaks HID++ on its own node, cabled, on long reports alone (gearbench.g432). The
daemon sends it only what g432.SENT names, and its lights only what headset_lights.SENT
names: when it attaches, its feature table and the root lookups of the features of
g432.LOOKED_UP the table lists, of 0x8310 on a model with an equalizer of its own and of
0x8010 on a model with G keys (HeadsetModel.g_keys), then the firmware version's reads, the
reads of its sidetone, battery, power-off time and, on a model with an equalizer of its own,
the equalizer, then the RAM gains of the preset picked for the model, where one is
(g432_library.load_picked), and the equalizer's reads again, or with none picked 0 dB on
every band to the RAM (_send_flat), then 0x8070's root lookup and its lights' zones and
startup effect, where it lists each, and while they follow the keyboard that leads
(lights.py) their claim and the keyboard's color, then its G keys' events turned on
(0x8010 fn 2 `01`), where it lists them; later, on request, 0x8300's read and its writes,
the battery's read, the power-off time's write and read, the equalizer's reads and RAM
gains, and the lights' claim, effects and release, their startup effect's write and read,
and the effects they keep; while the lights follow the keyboard, its color at each change of
it, and again at a return and after a battery read where it is owed; as it is let go, the
lights' release again while Gearbench holds them or held them before a return with Follow
the keyboard off, or at a power-off while they follow the keyboard, black in its place with
the claim kept (lights.py), and the G keys' events turned off (fn 2 `00`) while they are on;
every KEYBOARD_BATTERY_INTERVAL and after a resume the battery's read; after a battery read that
goes through, and at `g432`'s read, each read of the attach whose value is still unknown again:
the sidetone's, the power-off time's and the equalizer's, then the preset picked's RAM gains
and the equalizer's reads again, or flat's gains, the lights' zones' and startup effect's,
and the G keys' events turned on where their last turn-on failed; and, as
the headset comes back while it stayed attached (after a resume, or at a reading while it
was taken to be away), its G keys' events turned on again, since it may
have lost them with its RAM, as G HUB turns a G933's on at each reconnect, and, where its
equalizer was read before, the RAM gains of the preset picked and the equalizer's reads
again.
The first read the daemon makes of a headset's sidetone keeps the level found for its product
id, and a reset writes it back, or SIDETONE_UNREAD_RESET where no read found one before the
first write. A preset of the model's library picked through `g432_headset_eq` is kept in its
own file, the daemon's alone, and sent again at each attach, or once an equalizer the attach
could not read is read, since the headset keeps the gains it is sent in its RAM alone (the
owner's decision of 2026-10-23, "Library and re-send"), and once at each return, since no
location gives the gains a G933 plays (EQ_ACTIVE; the owner's decisions of 2026-10-25);
Flat forgets it. With none picked, each attach sends flat, 0 dB on every band, since the
headset may still play gains sent before it, as across a restart of the daemon, and a return
from away, which is taken as a switch-on, plays flat with nothing sent (the owner's decisions
of 2026-10-25): what the headset plays reads flat from either until a write answers
(_playing).
The node also carries the headset's volume wheel and mute, whose reports pump reads and
drops, the battery's broadcasts, which it takes as readings, but for one of no state
from behind a receiver, which marks the headset not answering (_take_broadcast), and its G
keys' events, each key going down or up handed to the bindings (Bindings.on_headset_key),
none after a key's action whose write found the node gone and let the headset go (call).
"""

from __future__ import annotations

import contextlib
import logging
import selectors
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ... import g432_library
from ...battery import CABLE_CHARGE, F_ADC, UNKNOWN
from ...eq_presets import NO_PRESETS
from ...g432 import (
    BY_RECEIVER,
    F_EQUALIZER,
    G432,
    MODEL_OF,
    NO_HEADSET_EQ,
    SIDETONE,
    SIDETONE_RANGE,
    SIDETONE_UNREAD_RESET,
    Equalizer,
    HeadsetModel,
    auto_off_words,
    find_g432,
    kept_refusal,
    not_listed,
    open_g432,
    sidetone_level,
)
from ...hidpp import (
    NO_RULE,
    DeviceGone,
    HidppFailure,
    HidppTimeout,
    UnreadableAnswer,
)
from ...keys import F_GKEY, KeyEvent
from ...report import untested_note
from ...settings_files import ConfigError, shown_path, unprefixed
from ...tray import Battery
from ...wording import reason, route
from ..shared import (
    DEVICE_GONE,
    DEVICE_NOT_SET_UP,
    KEYBOARD_BATTERY_INTERVAL,
    NODE_DENIED,
    AttachNotes,
    BatteryNotices,
    Lookout,
    attached_entry,
    failure_reason,
    hidpp_refusal,
    look_due,
)
from .lights import HeadsetLighting

if TYPE_CHECKING:
    from .. import Daemon

log = logging.getLogger("gearbench")

NO_G432 = "no G432 or other headset of its family found; plug in its USB adapter, receiver or cable"
#: The level a G key turns sidetone on at where it turned none off: the middle of
#: SIDETONE_RANGE, Claude Code's call of 2026-10-25, pending the owner.
SIDETONE_ON = 50
#: `g432_headset_eq`'s refusal of more than one of gains, a preset and flat, before anything
#: is sent.
ONE_CHANGE = "give the equalizer gains, a preset or flat, one of the three"
#: Its refusal when the preset picked plays and could not be kept, or Flat's when the preset
#: picked could not be forgotten: the preset's name or the headset's short name, then the
#: picked file and the cause (_file_cause).
NOT_KEPT = "{} plays, but is not sent again when the headset next connects: {}"
NOT_FORGOTTEN = ("the {} plays flat, but the preset picked is sent again when the headset "
                 "next connects: {}")
#: NOT_KEPT where an earlier pick stays kept: the preset's name, the earlier one's, then the
#: file and the cause.
NOT_KEPT_OVER = "{} plays, but {} is sent again when the headset next connects: {}"


def flat_gains(eq: Equalizer) -> tuple[int, ...]:
    """0 dB on every band of `eq`."""
    return (0,) * len(eq.frequencies)


def _file_cause(model: HeadsetModel, done: str, exc: OSError | ConfigError) -> str:
    """Why `model`'s picked file could not be `done`, written or removed: its path and the
    cause, the path said once where a ConfigError starts with it."""
    path = g432_library.picked_path(model)
    return f"{shown_path(path)} could not be {done} ({unprefixed(reason(exc), path)})"


class G432Headset(BatteryNotices):
    """A headset of the family while one is attached, and what it last said."""

    BATTERY_POWERED = "is charging"
    BATTERY_BODY = CABLE_CHARGE

    def __init__(self, daemon: Daemon) -> None:
        self.d = daemon
        self.dev: G432 | None = None
        self.path: Path | None = None
        #: The level last read or written, None while unknown.
        self.level: int | None = None
        #: The firmware version read at the attach (G432.firmware), None while unknown.
        self.firmware: str | None = None
        #: The last battery reading, from a read or a broadcast, and the power-off time read
        #: at the attach or after (_read_unread), in minutes; None while unknown or for a
        #: headset with neither. And when the battery is read next.
        self.battery: dict | None = None
        self.power_off: int | None = None
        self._battery_due = 0.0
        #: When the battery reading was taken, in seconds since the epoch; and whether the
        #: headset answers its battery's read, False on a headset behind its receiver from one
        #: that fails, or a broadcast of no state, until a reading comes: switched off, it
        #: fails each one.
        self.battery_at: int | None = None
        self.answering = True
        #: The equalizer in the headset as last read, on a model that has one, None before a
        #: read or without one: its active gains are location 1's, which a G933 does not play
        #: (EQ_ACTIVE), so the reply gives _playing's; the gains of the last write it answered
        #: since the attach or the switch-on; and the name of the preset picked for the model,
        #: which _send_picked sends again, None for none.
        self.eq: Equalizer | None = None
        self.sent_gains: tuple[int, ...] | None = None
        self.picked: str | None = None
        #: Whether the headset may have lost its RAM gains while it stayed attached: a
        #: reading came while it was taken to be away (away), as from one switched off and
        #: on behind its receiver, or a resume came. tend then sends the preset picked again;
        #: each call of _send_picked clears the mark, sent or not.
        self._back = False
        #: Whether the G keys' events were turned on since the attach, which the teardown
        #: turns off, and whether they are owed again, at a return, which may have lost them
        #: with the headset's RAM: tend turns them on once it is no longer away.
        self.keys_on = False
        self._keys_owed = False
        #: Whether the last turn-on of the G keys' events failed, which a battery read that
        #: goes through sends again (_read_unread).
        self._keys_failed = False
        #: Its lights, where it lists them (lights.py).
        self.lights = HeadsetLighting(daemon, self)
        #: By product id, the sidetone level a G key turned off, which the next turns on.
        self._sidetone_was: dict[int, int] = {}
        #: The model the battery notices were last armed for: another's attach arms them
        #: again, as KeyboardBattery.poll_battery does for another keyboard.
        self._battery_of: str | None = None
        #: By product id, the level the first read since the daemon started found, kept
        #: across a replug, and each id written before any read found one: a read after it
        #: finds the daemon's own level.
        self._first: dict[int, int] = {}
        self._written: set[int] = set()
        #: When to look for a headset again, and the node whose lookup got no answer.
        self._lookout = Lookout()
        #: A node refused to this user and why a found headset's setup failed, for
        #: problems() and the journal (AttachNotes).
        self._notes = AttachNotes()

    def commands(self) -> dict[str, Callable[[dict], dict]]:
        return {"g432": self._cmd_g432, "g432_sidetone": self._cmd_g432_sidetone,
                "g432_auto_off": self._cmd_g432_auto_off,
                "g432_headset_eq": self._cmd_g432_headset_eq, **self.lights.commands()}

    @property
    def model(self) -> HeadsetModel:
        """The attached headset's model; read only with one attached."""
        return cast("G432", self.dev).model

    def attached_name(self) -> str | None:
        return None if self.dev is None else self.dev.model.short

    def answers(self) -> bool:
        """Whether the headset attached answers, as `g432`'s `answering` gives it."""
        return self.answering

    def status_entry(self) -> dict | None:
        """`status`'s entry for the headset attached (attached_entry); None with none."""
        if self.dev is None:
            return None
        return attached_entry(self.model.short, str(self.path), route(self.model.plugged_by),
                              self.battery, self.battery_at, answering=self.answers(),
                              battery_found=self.dev.has(F_ADC))

    def tray_battery(self) -> Battery | None:
        """The tray's line for the headset: its last reading, from a read or a broadcast,
        while one is attached."""
        if self.dev is None or self.battery is None:
            return None
        return Battery.of(f"{self.dev.product:04x}", self.model.short, self.battery,
                          answering=self.answering, read_at=self.battery_at)

    def deadlines(self) -> list[float]:
        if self.dev is None:
            return self._lookout.deadlines(self.d.plugs.pace)
        return [self._battery_due] if self.dev.has(F_ADC) else []

    def recheck(self) -> None:
        """After a resume: a missing headset looked for at once, and the battery of an attached
        one that lists it read at the next pass, since it may have charged or been switched off
        in the suspend, and its return marked (_back, HeadsetLighting.returned), since the
        suspend may have cut its power."""
        self._lookout.recheck()
        if self.dev is not None:
            self._battery_due = time.monotonic()
            self._back = self._keys_owed = True
            self.lights.returned()

    # -- attaching ---------------------------------------------------------

    def maybe_connect(self) -> None:
        """Look for a headset once the retry is due, or at once when one whose lookup got
        no answer has lost its node; the next look is timed from the end."""
        if self.dev is not None or not self._lookout.due():
            return
        try:
            self.connect()
        finally:
            self._lookout.looked()

    def connect(self) -> None:
        """Attach the headset find_g432 finds if it answers the probe, read its feature table,
        its firmware version and what it lists of its sidetone, battery, power-off time and
        equalizer, send the preset picked for its model to the RAM again, or flat with none
        picked (_send_picked), read its lights and give them the keyboard's color where they
        follow it (HeadsetLighting.give_owed), and turn its G keys' events on (_keys_on);
        nothing more once a write finds the node gone and lets the headset go (call). Never
        raises."""
        self._notes.looking()
        self._lookout.unanswered = None
        try:
            found = find_g432()
        except OSError as exc:
            log.debug("headsets: could not look for one: %s", exc)
            return
        if found is None:
            return
        path, product = found
        model = MODEL_OF[product]
        started = time.monotonic()
        try:
            dev = open_g432(path, product)
        except PermissionError as exc:
            self._notes.refused(path, NODE_DENIED.format(path, model.short),
                                f"{model.key}: cannot open the {model.short}'s {path}: "
                                f"{reason(exc)}; {NO_RULE}")
            return
        except OSError as exc:
            log.debug("%s: not reachable on %s: %s", model.key, path, exc)
            return
        self._notes.opened(path)
        try:
            features = dev.find_features()
        except HidppFailure as exc:
            dev.close()
            self._not_set_up_on(path, model, exc, started)
            return
        self._notes.attached()
        self.dev, self.path = dev, path
        self.BATTERY_PREFIX = f"{model.key}: "
        if model.key != self._battery_of:
            self._battery_of = model.key
            self.battery_rearmed(f"the headset read before the {model.short} is no longer attached")
        self.d.selector.register(dev.fileno, selectors.EVENT_READ, "g432")
        self._try_read("firmware version", self._read_firmware)
        # A model not yet run on asks for a device report (report.untested_note).
        note = "" if model.tested else f"; {untested_note(model.short)}"
        if SIDETONE not in features:
            log.info("%s: connected on %s; it lists no sidetone feature (0x8300)%s",
                     model.key, path, note)
        else:
            log.info("%s: connected on %s, sidetone at feature index 0x%02x%s", model.key,
                     path, features[SIDETONE].index, note)
        # Each read that fails is said in the journal and leaves its value unknown, read again
        # later: the battery by tend, the rest by _read_unread. A G key's action the battery's
        # read takes may find the node gone and let the headset go (call).
        for feature_id, what, read in ((SIDETONE, "sidetone", self._read),
                                       (F_ADC, "battery", self._read_battery),
                                       (F_ADC, "power-off time", self._power),
                                       (F_EQUALIZER, "equalizer", self._read_eq)):
            if feature_id in features and self.dev is not None:
                self._try_read(what, read)
        if self.dev is None:
            return
        if model.onboard_eq:
            self._send_picked(attach=True)
        self.lights.read_at_attach()
        self.lights.give_owed()
        if self.dev is None:
            # The color's write found the node gone and let the headset go.
            return
        self._keys_on()
        self.d.forget_slow("g432")
        self.d.note_slow(started, f"{model.short} connect on {path}")

    def _not_set_up_on(self, path: Path, model: HeadsetModel, exc: HidppFailure,
                       started: float) -> None:
        """A found headset whose setup failed: a timeout is no answer, which makes the next
        look wait longer; a node gone is no headset found. Said once at info."""
        quiet = isinstance(exc, HidppTimeout)
        why = "no answer" if quiet else failure_reason(exc)
        self._lookout.unanswered = path if quiet else None
        self._notes.not_set_up = (None if isinstance(exc, DeviceGone) else
                                  DEVICE_NOT_SET_UP.format(model.short, why,
                                                           f"its {model.plugged_by}"))
        self._notes.failed(path, f"{model.key}: the {model.short} on {path} could not be set up: "
                                 f"{why}")
        self.d.note_slow(started, f"{model.short} connect on {path}", why, repeats="g432")

    def teardown(self) -> str | None:
        """Let go of the headset, sending it nothing but the lights' release where Gearbench
        holds them or held them before a return with Follow the keyboard off (_may_hold), so
        it shows its own lighting again, unless a power-off sent them black and kept their
        claim (HeadsetLighting.go_dark), and its G keys' events off where they
        are on, so they are its own again; what its G keys hold is let go, since their
        releases will not come, and a macro one of them started stops (Bindings.let_go_headset).
        What was let go, for shutdown's line, or None with none
        attached. Never raises: a node gone fails the release at once."""
        lights_said = self.lights.let_go()
        dev, self.dev = self.dev, None
        if dev is None:
            return None
        if self.keys_on:
            with contextlib.suppress(HidppFailure, OSError):
                dev.divert_keys(False)
        self.d.bindings.let_go_headset(dev.model.g_keys, gone=True)
        name = dev.model.short
        with contextlib.suppress(KeyError, ValueError):
            self.d.selector.unregister(dev.fileno)
        with contextlib.suppress(OSError):
            dev.close()
        self.path = self.level = self.firmware = None
        self.battery = self.power_off = self.eq = self.battery_at = self.picked = None
        self.sent_gains = None
        self.answering, self._back = True, False
        self.keys_on = self._keys_owed = self._keys_failed = False
        return f"{name} let go" + (f", {lights_said}" if lights_said else "")

    def pending(self) -> bool:
        """Whether reports a request read are queued, which do not wake select."""
        return self.dev is not None and self.dev.pending

    def pump(self) -> None:
        """The node is readable, or a request left events queued: the volume wheel's or the
        mute's reports, which are dropped, the battery's broadcasts, the G keys' events, or
        the node gone. Never raises."""
        try:
            readings = cast("G432", self.dev).pump()
        except DeviceGone as exc:
            self._gone(exc)
            return
        except HidppFailure as exc:
            log.info("%s: reading the %s's node failed (%s); letting it go", self.model.key,
                     self.model.short, failure_reason(exc))
            self.teardown()
            return
        self._take_queued(readings)

    def tend(self) -> None:
        """Read the battery of a headset that has one once it is due, then, where the read
        goes through and no G key's action it took let the headset go (_read_battery), what
        the attach could not read (_read_unread) and the color its lights
        are owed where they follow the keyboard (HeadsetLighting.give_owed); and where the
        headset is back (_back) and not taken to be away, the preset picked (_send_picked), that
        color and its G keys' events on again (_keys_on). Never raises:
        a node gone tears the headset down, and a battery read that fails is said at debug,
        since a wireless headset switched off fails each one, and marks a headset behind its
        receiver not answering."""
        if self.dev is None:
            return
        if self.dev.has(F_ADC) and look_due(time.monotonic(), self._battery_due):
            try:
                self._read_battery()
                if self.dev is None:
                    # A G key's action the read took let the headset go.
                    return
                self._read_unread()
                self.lights.give_owed()
            except DeviceGone as exc:
                # The teardown forgets a return.
                self._gone(exc)
            except HidppFailure as exc:
                # The battery's: _read_unread raises DeviceGone alone.
                log.debug("%s: its battery could not be read: %s", self.model.key,
                          failure_reason(exc))
                # Taken to be the headset switched off or out of range behind its receiver,
                # as the Centurion's and the mice's: a switched-off G933's receiver answers
                # each read with error 0x05 at once (measured 2026-10-25). A wired one has no
                # receiver to be away from.
                if self.model.plugged_by == BY_RECEIVER:
                    self.answering = False
        # Not while it is away, which would fail the write. Sent whatever the equalizer
        # gives, since no location gives the gains a G933 plays (EQ_ACTIVE); one still
        # unknown is _read_unread's, which sends it once it reads it.
        if self._back and not self.away():
            self._back = False
            if self.eq is not None:
                self._send_picked()
            self.lights.give_owed()
        if self._keys_owed and not self.away():
            self._keys_owed = False
            self._keys_on()

    def away(self) -> bool:
        """Whether the headset is taken to be off or out of reach: marked not answering, or
        its last reading of no state (battery.adc)."""
        return not self.answering or (self.battery is not None
                                      and self.battery.get("state") == UNKNOWN)

    def _gone(self, exc: DeviceGone) -> None:
        """The node gone: said, and the headset let go."""
        log.info("%s: the %s went away: %s", self.model.key, self.model.short,
                 failure_reason(exc))
        self.teardown()

    # -- what problems() and the refusals read -------------------------------

    def problems(self) -> list[tuple[str, str]]:
        """The headset's entries for the daemon's `problems`, as (code, message). Nothing is
        sent to it."""
        found: list[tuple[str, str]] = []
        if (denied := self._notes.denied_problem()) is not None:
            found.append(("g432_no_permission", denied))
        if self._notes.not_set_up is not None:
            found.append(("g432_not_set_up", self._notes.not_set_up))
        return found

    def missing(self) -> str:
        """Why no command can go to a headset, with none attached."""
        return next((message for _code, message in self.problems()), NO_G432)

    # -- reading and the commands ---------------------------------------------

    def _try_read(self, what: str, read: Callable[[], object]) -> None:
        """Run `read`, a read of the headset's `what`; one that fails is said in the journal."""
        try:
            read()
        except HidppFailure as exc:
            log.info("%s: its %s could not be read: %s", self.model.key, what,
                     failure_reason(exc))

    def _read_firmware(self) -> None:
        self.firmware = cast("G432", self.dev).firmware()

    def _power(self) -> None:
        self.power_off = cast("G432", self.dev).power_off()

    def _read(self) -> None:
        """Read the level; the first read before any write is kept. Raises HidppFailure."""
        dev = cast("G432", self.dev)
        level = self.level = dev.sidetone()
        product = dev.product
        if product not in self._first and product not in self._written:
            self._first[product] = level
            log.info("%s: sidetone at %d when first read", self.model.key, level)

    def _read_battery(self) -> None:
        """Read the battery, and time the next read from now. No reading is taken where a G
        key's action the read queued let the headset go (_take_queued). Raises
        HidppFailure."""
        self._battery_due = time.monotonic() + KEYBOARD_BATTERY_INTERVAL
        dev = cast("G432", self.dev)
        reading = dev.battery()
        # A broadcast the read queued came before its answer, which is the newest.
        self._take_queued(dev.queued())
        if self.dev is None:
            return
        self._take_battery(reading)

    def _read_eq(self) -> None:
        """Read the equalizer in the headset. Raises HidppFailure."""
        self.eq = cast("G432", self.dev).equalizer()

    def _playing(self, eq: Equalizer) -> tuple[int, ...]:
        """The gains the headset plays, as the reply gives them: those of the last write it
        answered since the attach or the switch-on (_played), else flat, as a G933 plays at a
        power-on (EQ_ACTIVE; the owner's decisions of 2026-10-25). After an attach whose write
        failed or was not sent, the headset may still play what it played before."""
        return flat_gains(eq) if self.sent_gains is None else self.sent_gains

    def _played(self, gains: Sequence[int]) -> None:
        """`gains` answered by the headset: they play, whether or not a read follows or
        answers, until the next attach or switch-on (_playing). The G933's location 1 gave the
        same gains before and after writes the owner heard play (2026-10-25), so a read must
        not replace them."""
        self.sent_gains = tuple(gains)

    def _read_unread(self) -> None:
        """Make again each read of the attach whose value is still unknown, in the attach's
        order: the sidetone's level, the power-off time, the equalizer, then, once it reads,
        the preset picked sent again, or flat with none picked, as at the attach
        (_send_picked), the lights' zones, and the G keys' events turned on where their last
        turn-on failed (_divert). Raises DeviceGone."""
        dev = cast("G432", self.dev)
        if dev.has(SIDETONE) and self.level is None:
            self._read_again("sidetone", self._read)
        # From a 0x1F20 older than version 2 it stays None, and nothing is sent for it
        # (G432.power_off).
        if dev.has(F_ADC) and self.power_off is None:
            self._read_again("power-off time", self._power)
        if (dev.has(F_EQUALIZER) and self.eq is None
                and self._read_again("equalizer", self._read_eq)):
            self._send_picked(attach=True)
        if self.lights.unread is not None:
            self._read_again("lights", self.lights.read)
        # A divert that failed, the attach's or a return's: a G933 attached while it was still
        # starting refused it (error 0x05) and kept its G keys to itself (measured
        # 2026-10-26). A return marked in the same pass sends it itself (tend, _keys_owed).
        if dev.has(F_GKEY) and self._keys_failed and not self._keys_owed:
            try:
                self._divert()
            except DeviceGone:
                raise
            except HidppFailure as exc:
                log.debug("%s: its G keys could not be turned on: %s", self.model.key,
                          failure_reason(exc))

    def _read_again(self, what: str, read: Callable[[], object]) -> bool:
        """One read of _read_unread: whether it went through. One that fails is said at
        debug, since it is made again at each battery read until one does. Raises
        DeviceGone."""
        try:
            read()
        except DeviceGone:
            raise
        except HidppFailure as exc:
            log.debug("%s: its %s could not be read: %s", self.model.key, what,
                      failure_reason(exc))
            return False
        return True

    def _send_picked(self, attach: bool = False) -> None:
        """The preset picked for the model sent again, its gains to the RAM, then the
        equalizer read back; with none picked, flat with `attach` (_send_flat), else nothing,
        since a return plays flat. Each call clears _back, sent or not, so a return gets one
        try. A pick that does not read, an equalizer not read, or bands or gains the headset
        does not take is said in the journal with nothing sent; a write that fails, which may
        have reached the headset (Hidpp.request writes before it reads the answer), and a
        read-back that fails are said too. Never raises."""
        self._back = False
        model, dev = self.model, cast("G432", self.dev)
        try:
            picked = g432_library.load_picked(model)
        except (OSError, ValueError) as exc:
            # A ConfigError names the file; an OSError's reason does not.
            why = (f"{g432_library.picked_path(model)}: {reason(exc)}"
                   if isinstance(exc, OSError) else reason(exc))
            log.info("%s: the preset picked could not be read, so none was sent again: %s",
                     model.key, why)
            return
        if picked is None:
            if attach:
                self._send_flat()
            return
        self.picked = picked.name
        if self.eq is None:
            log.info("%s: %s was not sent again: its equalizer was not read", model.key,
                     picked.name)
            return
        try:
            gains = g432_library.gains_for(picked, self.eq.frequencies, model.short)
            dev.set_gains(self.eq, gains)
            self._played(gains)
        except ValueError as exc:
            log.info("%s: %s was not sent again: %s", model.key, picked.name, exc)
            return
        except HidppFailure as exc:
            log.info("%s: %s could not be sent again: %s", model.key, picked.name,
                     failure_reason(exc))
            return
        log.info("%s: equalizer set to %s again, the preset picked: %s", model.key,
                 picked.name, " ".join(f"{gain:+d}" for gain in gains))
        self._try_read("equalizer", self._read_eq)

    def _send_flat(self) -> None:
        """0 dB on every band to the RAM, with no read after it, since the headset may still
        play gains sent before the attach, as across a restart of the daemon, while the reply
        gives flat (_playing; the owner's decision of 2026-10-25). An equalizer whose writes
        are refused, or a write that fails, is said in the journal. Never raises."""
        if self.eq is None:
            # Not listed, of which the journal says nothing, or not read at the attach, which
            # said why: _read_unread sends flat once a listed one reads.
            return
        try:
            cast("G432", self.dev).set_gains(self.eq, flat_gains(self.eq))
        except (ValueError, HidppFailure) as exc:
            log.info("%s: its equalizer could not be set flat: %s", self.model.key,
                     failure_reason(exc))
            return
        log.info("%s: equalizer set flat, no preset picked", self.model.key)

    def _take_queued(self, queued: list[dict | KeyEvent]) -> None:
        """What the node queued, in its order: each battery reading taken as a broadcast,
        and each G key going down or up handed to the bindings; nothing after a G key's
        action whose write found the node gone and let the headset go (call)."""
        for item in queued:
            if self.dev is None:
                # The teardown let go of what the keys held: a key pressed now would never
                # see its release.
                return
            if isinstance(item, KeyEvent):
                self.d.bindings.on_headset_key(item.key, item.pressed)
            else:
                self._take_broadcast(item)

    def _keys_on(self) -> None:
        """The G keys' events on, on a headset `find_features` found 0x8010 on, as G HUB
        turns a G933's on at each connect; what the keys held is let go first, since the
        headset forgets which are held (G432.divert_keys). A failure is said in the journal,
        and a battery read that goes through sends it again until the headset answers it,
        unless a return marked in the same pass sends it (_read_unread). Never raises."""
        if not cast("G432", self.dev).has(F_GKEY):
            return
        try:
            self._divert()
        except HidppFailure as exc:
            log.info("%s: its G keys could not be turned on: %s", self.model.key,
                     failure_reason(exc))

    def _divert(self) -> None:
        """What the keys held let go, then their events on (G432.divert_keys). Raises
        HidppFailure."""
        self.d.bindings.let_go_headset(self.model.g_keys)
        # Failed until answered: _read_unread sends it again.
        self._keys_failed = True
        cast("G432", self.dev).divert_keys(True)
        self.keys_on, self._keys_failed = True, False

    def _take_broadcast(self, reading: dict) -> None:
        """A 0x1F20 broadcast: one in the unknown state from behind a receiver, which the
        kernel takes as the headset off or out of reach (battery.ADC_STATES), marks it not
        answering and keeps the last reading; any other is taken (_take_battery). A G933's
        receiver sends one with no millivolts and no flags as the headset switches off
        (measured 2026-10-25)."""
        if reading["state"] == UNKNOWN and self.model.plugged_by == BY_RECEIVER:
            self.answering = False
            return
        self._take_battery(reading)

    def _take_battery(self, reading: dict) -> None:
        # A reading from a headset away is its return, taken as a switch-on, which loses the
        # gains sent with the headset's RAM (_playing) and what its lights showed; tend waits
        # for one with a state.
        if self.away():
            self._back = self._keys_owed = True
            self.sent_gains = None
            self.lights.returned()
        self.battery, self.battery_at, self.answering = reading, round(time.time()), True
        self.warn_battery(reading)

    def battery_names(self) -> tuple[str, str]:
        return self.model.short, f"the {self.model.short}"

    def call(self, work: Callable[[], object]) -> dict | None:
        """Run `work` on the headset: None when it went through, else the refusal. A node
        gone tears the headset down."""
        model = self.model
        try:
            work()
        except DeviceGone as exc:
            self._gone(exc)
            return hidpp_refusal(model.short, exc)
        except HidppFailure as exc:
            if not isinstance(exc, HidppTimeout | UnreadableAnswer):
                log.info("%s: a request failed: %s", model.key, failure_reason(exc))
            return hidpp_refusal(model.short, exc)
        return None

    def checked_call(self, work: Callable[[], object]) -> dict | None:
        """`call`, with a ValueError from `work`, a value or a write refused before it is
        sent, as the refusal in its own words."""
        try:
            return self.call(work)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

    def reply(self) -> dict:
        """`g432`'s answer from what the headset last said."""
        dev = cast("G432", self.dev)
        model = self.model
        return {
            "ok": True,
            "name": model.short,
            "model": model.key,
            # A model not yet run on, whose page asks for a device report (report.untested),
            # and why a write the headset keeps is refused on it, null for none.
            "untested": not model.tested,
            "flash_refused": kept_refusal(model),
            "device": str(self.path),
            "product": f"{dev.product:04x}",
            # As "U1 42.00.B0001", or null where it could not be read.
            "firmware": self.firmware,
            # Whether the table listed 0x8300: without it `sidetone` stays null.
            "sidetone_found": dev.has(SIDETONE),
            "sidetone": self.level,
            "sidetone_first": self._first.get(dev.product),
            "sidetone_range": list(SIDETONE_RANGE),
            # Whether the table listed 0x1F20, the last reading in battery.read's shape, and
            # the minutes of silence it powers off after, 0 for never, null where not read.
            "battery_found": dev.has(F_ADC),
            "battery": self.battery,
            # When it was taken, and whether the headset answers its read: false once one
            # fails on a headset behind its receiver, as one switched off does (tend).
            "battery_at": self.battery_at,
            "answering": self.answering,
            "power_off": self.power_off,
            # What each G key does now, by the name it binds under, on the layer in force or
            # its default (Bindings.headset_binding); none on a model with none.
            "g_keys": {key: self.d.bindings.headset_binding(key) for key in model.g_keys},
            # On a model with an equalizer of its own, the headset's bands, their range in
            # dB, the gains it keeps (EQ_SAVED) and the ones it plays (_playing), why a write
            # is refused, null for none, and the name of the preset picked, which _send_picked
            # sends again, null for none; null on another model, or where not read.
            "equalizer": None if self.eq is None else {
                "frequencies": list(self.eq.frequencies), "range": [self.eq.low, self.eq.high],
                "saved": list(self.eq.saved), "active": list(self._playing(self.eq)),
                "refused": self.eq.refused, "picked": self.picked},
            # On a headset that lists lights, what lights.py gives of them; null on another.
            "lights": self.lights.reply(),
            # Why the attach could not read them, as a write's refusal says it, null where
            # it did.
            "lights_unread": self.lights.unread_words(),
        }

    def _cmd_g432(self, request: dict) -> dict:
        """The headset's state; with `read`, its sidetone and its battery read again first,
        then what the attach could not read (_read_unread), refused as gone where a G key's
        action the battery's read took let the headset go."""
        if self.dev is None:
            return {"ok": False, "error": self.missing()}
        if request.get("read") is True:
            model = self.model
            if not self.dev.has(SIDETONE):
                return {"ok": False, "error": not_listed(model, SIDETONE)}
            if (refused := self.call(self._read)) is not None:
                return refused
            if self.dev.has(F_ADC) and (refused := self.call(self._read_battery)) is not None:
                return refused
            if self.dev is None:
                # A G key's action the battery's read took found the node gone (call).
                return {"ok": False, "error": DEVICE_GONE.format(model.short)}
            if (refused := self.call(self._read_unread)) is not None:
                return refused
        return self.reply()

    def _cmd_g432_sidetone(self, request: dict) -> dict:
        """Write `level`, or with `reset` the level the first read before any write found
        (SIDETONE_UNREAD_RESET where none did), then read it back."""
        if self.dev is None:
            return {"ok": False, "error": self.missing()}
        dev = self.dev
        if request.get("reset") is True:
            level = self._first.get(dev.product, SIDETONE_UNREAD_RESET)
        else:
            try:
                level = sidetone_level(request.get("level"))
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
        if not dev.has(SIDETONE):
            return {"ok": False, "error": not_listed(self.model, SIDETONE)}

        def write() -> None:
            # Before the write: a write that fails part way may still have reached it.
            self._written.add(dev.product)
            dev.set_sidetone(level)
            log.info("%s: sidetone set to %d", self.model.key, level)
            self.level = level
            self._read()

        return self.call(write) or self.reply()

    def _cmd_g432_auto_off(self, request: dict) -> dict:
        """Write `minutes`, the minutes of silence the headset powers off after, 0 for
        never, then read it back (G432.set_power_off). Refused with nothing sent for a time
        not offered, a headset that lists no 0x1F20 or an older one, or a model not tested
        on this hardware (kept_refusal)."""
        if self.dev is None:
            return {"ok": False, "error": self.missing()}
        dev, model = self.dev, self.model
        if not dev.has(F_ADC):
            return {"ok": False, "error": not_listed(model, F_ADC)}

        def write() -> None:
            minutes = request.get("minutes")
            try:
                self.power_off = dev.set_power_off(minutes)
            except HidppFailure:
                # Unknown: a write that fails part way may still have reached it.
                self.power_off = None
                raise
            log.info("%s: auto shut-off set to %s, read back as %s", model.key,
                     auto_off_words(minutes), auto_off_words(self.power_off))

        return self.checked_call(write) or self.reply()

    def key_action(self, action: str, key: str) -> None:
        """What a G key of the headset, `key`, bound to `action`, one of
        config.HEADSET_ACTIONS but none, does as it goes down: the next step of the
        brightness cycle or the lights on or off (HeadsetLighting.step), the next preset of the
        model's library (_next_preset), or sidetone on or off (_toggle_sidetone), each through
        the command that sets it. A refusal is said in the journal."""
        steps = {"brightness-cycle": lambda: self.lights.step(cycle=True),
                 "lights-toggle": lambda: self.lights.step(cycle=False),
                 "eq-next": self._next_preset, "sidetone-toggle": self._toggle_sidetone}
        # Taken first: a write that finds the node gone lets the headset go (call).
        name = self.model.key
        reply = steps[action]()
        if not reply["ok"]:
            log.info("%s: %s did nothing: %s", name, key, reply["error"])

    def _next_preset(self) -> dict:
        """The preset after the one picked in the model's library, or its first after the
        last or with none picked, picked as `g432_headset_eq` with `preset` picks it. Refused
        before any read on a model with no equalizer of its own, as the G635, whose
        `<key>-eq.json` holds its equalizer on the computer, another library."""
        if not self.model.onboard_eq:
            return {"ok": False, "error": NO_HEADSET_EQ.format(self.model.short)}
        try:
            presets = g432_library.load_library(self.model).presets
        except ConfigError as exc:
            return {"ok": False, "error": str(exc)}
        if not presets:
            return {"ok": False, "error": NO_PRESETS}
        names = [preset.name for preset in presets]
        after = names.index(self.picked) + 1 if self.picked in names else 0
        return self._cmd_g432_headset_eq(presets[after % len(presets)].request())

    def _toggle_sidetone(self) -> dict:
        """Sidetone off where it is on, its level kept by product id, or on again at that
        level, else at SIDETONE_ON; an unread level is taken as off."""
        product = cast("G432", self.dev).product
        if self.level:
            self._sidetone_was[product] = self.level
            return self._cmd_g432_sidetone({"level": 0})
        return self._cmd_g432_sidetone({"level": self._sidetone_was.get(product, SIDETONE_ON)})

    def _cmd_g432_headset_eq(self, request: dict) -> dict:
        """The equalizer in the headset: `gains`, a whole dB a band, written to its RAM; or
        `preset`, a library preset's name with its `bands`, its gains written there and the
        preset kept to send again (_pick); or with `flat` 0 dB on every band written there
        and the preset picked forgotten; then read again, the gains written standing as the
        playing ones (_played), and a pick kept or forgotten where the headset plays them
        (_plays), whether or not the read after the write answers, a read that fails being the
        reply's refusal. With none, read again. Refused on a model with no equalizer of its
        own, and, with nothing sent, for more than one of the three (ONE_CHANGE)."""
        if self.dev is None:
            return {"ok": False, "error": self.missing()}
        dev, model = self.dev, self.model
        if not model.onboard_eq:
            return {"ok": False, "error": NO_HEADSET_EQ.format(model.short)}
        if not dev.has(F_EQUALIZER):
            return {"ok": False, "error": not_listed(model, F_EQUALIZER)}
        flat = request.get("flat") is True
        if sum((request.get("gains") is not None, request.get("preset") is not None,
                flat)) > 1:
            return {"ok": False, "error": ONE_CHANGE}
        if self.eq is None and (refused := self.call(self._read_eq)) is not None:
            return refused
        eq = cast("Equalizer", self.eq)
        if request.get("preset") is not None:
            return self._pick(eq, request)
        gains = flat_gains(eq) if flat else request.get("gains")
        if gains is None:
            return self.call(self._read_eq) or self.reply()
        refused = self._set_gains(eq, gains, "equalizer gains set to")
        if not flat or (refused is not None and not self._plays(gains)):
            return refused or self.reply()
        try:
            if g432_library.drop_picked(model):
                log.info("%s: no preset is sent again at its attach now", model.key)
        except OSError as exc:
            return {"ok": False, "error": NOT_FORGOTTEN.format(
                model.short, _file_cause(model, "removed", exc))}
        self.picked = None
        return refused or self.reply()

    def _pick(self, eq: Equalizer, request: dict) -> dict:
        """`g432_headset_eq` with `preset`: the preset's gains to the RAM, each band's
        frequency the headset's (g432_library.gains_for), then the preset kept as the one
        picked, which _send_picked sends again, where the headset plays them (_plays), with
        the read's refusal where the read after the write failed. Refused, with no gains sent,
        for a preset or bands the headset does not take."""
        model = self.model
        try:
            preset = g432_library.preset_of({"name": request.get("preset"),
                                             "bands": request.get("bands")})
            gains = g432_library.gains_for(preset, eq.frequencies, model.short)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        refused = self._set_gains(eq, gains, f"equalizer set to {preset.name}:")
        if refused is not None and not self._plays(gains):
            return refused
        try:
            g432_library.save_picked(preset, model)
        except (OSError, ConfigError) as exc:
            why = _file_cause(model, "written", exc)
            said = (NOT_KEPT.format(preset.name, why) if self.picked is None
                    else NOT_KEPT_OVER.format(preset.name, self.picked, why))
            return {"ok": False, "error": said}
        self.picked = preset.name
        return refused or self.reply()

    def _plays(self, gains: Sequence[int]) -> bool:
        """Whether the headset plays `gains`: those of a write it answered (_played), whether
        or not the read after it answered too."""
        return self.sent_gains == tuple(gains)

    def _set_gains(self, eq: Equalizer, gains: object, said: str) -> dict | None:
        """Write `gains` to the RAM, standing as the playing ones once answered (_played), said
        in the journal after `said`, then read the equalizer again: None when it went through,
        else the refusal, with nothing sent for gains set_gains does not take."""
        dev, model = cast("G432", self.dev), self.model

        def write() -> None:
            dev.set_gains(eq, gains)
            played = cast("Sequence[int]", gains)
            self._played(played)
            log.info("%s: %s %s", model.key, said, " ".join(f"{gain:+d}" for gain in played))
            self._read_eq()

        return self.checked_call(write)
