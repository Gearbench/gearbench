"""The desktop session: the screen lock, the blank, suspend, input while
locked, a synced mouse's idleness, and the desktop notices."""

from __future__ import annotations

import contextlib
import logging
import os
import selectors
import time
from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING, cast

from ..desktop import Desktop
from ..keyboard import name as keyboard_name
from ..lighting import DARK, DIM, LEVEL_WORDS, LIT
from .shared import (
    KEYBOARD_BATTERY_INTERVAL,
    BatteryNotices,
    on_or_off,
    switch_refusal,
)

if TYPE_CHECKING:
    from . import Daemon

log = logging.getLogger("gearbench")

DPI_NOTICE_ICON = "input-mouse-symbolic"
#: The DPI notice's title, a mouse's name and the DPI it is on now. DpiNotice posts it with no
#: body.
DPI_NOTICE_TITLE = "{}: {} DPI"
#: Seconds after the last step that the daemon takes its DPI notice down: GNOME holds a
#: banner under a fullscreen window and would show a stale one later; under Do Not Disturb it
#: shows none but keeps the notice in its list. Its banner lasts about 4 s, so a step is not
#: cut short.
DPI_NOTICE_CLOSE = 5.0
#: Seconds after a post that the next step may still replace the notice in place. GNOME
#: Shell 50.1 (messageTray.js) eases a banner out 4 s after its 0.2 s ease in, or 2 s after
#: the first input when it came up on an idle desktop, and a replacement during that 0.2 s
#: ease out throws in _showNotificationCompleted and leaves the Shell showing no banner from
#: any program until it restarts. 1.5 s is short of the 2.2 s by more than the bus takes;
#: a later step closes the notice, which is safe in every state, and posts a new one.
DPI_NOTICE_REPLACE = 1.5

#: While locked, the keyboard dims this long after the last input and goes
#: dark this long after it. A diverted G, M or MR key never reaches Mutter, so
#: its press restarts both on the daemon's own deadlines (note_key_input).
LOCKED_DIM_MS = 30_000
LOCKED_DARK_MS = 60_000

#: Input this soon after the lights go down is the tail of what locked the
#: screen (letting go of Super+L), not someone coming back.
LOCK_SETTLE = 1.0

#: The two groups of waits on the desktop's idle time, each armed and dropped on its own, with
#: its own user-active watch in Mutter: the lock's (input while locked, its dim and dark) and a
#: synced mouse's (its dim and dark, and the input after them).
LOCK_WATCHES = "lock"
MOUSE_WATCHES = "mouse"


class KeyboardBattery(BatteryNotices):
    """The keyboard's low-battery notices, and the poll that reads its battery for them."""

    BATTERY_PREFIX = ""
    BATTERY_DEVICE = "keyboard"
    BATTERY_POWERED = "the keyboard is on external power"
    BATTERY_BODY = "Plug in its cable to charge it."

    def __init__(self, daemon: Daemon) -> None:
        self.d = daemon
        #: When the battery is read next.
        self._battery_poll_at = 0.0
        #: The name of the keyboard model the readings are of: another model's start over.
        self._battery_of: str | None = None

    def deadlines(self) -> list[float]:
        """When poll_battery is next due, once the first has run."""
        return [self._battery_poll_at] if self.battery_polled() and self._battery_poll_at else []

    @staticmethod
    def assumed_battery() -> float | None:
        """GEARBENCH_ASSUME_BATTERY as a percentage on battery, or None: how the
        notices are tested on the hardware."""
        raw = os.environ.get("GEARBENCH_ASSUME_BATTERY", "").strip()
        try:
            value = float(raw)
        except ValueError:
            return None
        return value if 0 <= value <= 100 else None

    def battery_polled(self) -> bool:
        """Whether the loop reads the battery for the notices."""
        return self.d.keyboard.dev is not None or self.assumed_battery() is not None

    def poll_battery(self) -> None:
        """Every KEYBOARD_BATTERY_INTERVAL, the battery for the notices: the keyboard's
        reading, or the assumed one in its place. Another keyboard model than the last read's
        arms both notices again and takes the last one's down."""
        now = time.monotonic()
        if now < self._battery_poll_at or not self.battery_polled():
            return
        self._battery_poll_at = now + KEYBOARD_BATTERY_INTERVAL
        # By name: the cable's and the receiver's ids of one model share it.
        driven = keyboard_name(self.d.keyboard.keyboard_product)
        if driven != self._battery_of:
            self._battery_of = driven
            self.battery_rearmed(f"the keyboard read before the {driven} is no longer driven")
        assumed = self.assumed_battery()
        reading = ({"percent": assumed, "external_power": False} if assumed is not None
                   else self.d.keyboard.battery_for_notices())
        if reading is not None:
            self.warn_battery(reading)

    def battery_names(self) -> tuple[str, str]:
        name = keyboard_name(self.d.keyboard.keyboard_product)
        # The notice's title opens with the name: "keyboard" before one is attached.
        return name[:1].upper() + name[1:], f"the {name}'s"


class DpiNotice:
    """The notice a DPI button's step posts: the one showing, whether a post is on its way to
    the server, a step made meanwhile, when the notice is taken down, and until when a step
    may replace it (DPI_NOTICE_REPLACE). `wanted` is the switch, asked at each step."""

    def __init__(self, desktop: Desktop, wanted: Callable[[], bool]) -> None:
        self.desktop, self.wanted = desktop, wanted
        self._shown: int | None = None
        self._sending = False
        self._next: str | None = None
        self._close_at: float | None = None
        self._replace_until = 0.0

    def deadlines(self) -> list[float]:
        """When tend is next due: a post on its way is left out, since its reply wakes the
        fd and sets the time."""
        return [self._close_at] if self._close_at is not None and not self._sending else []

    def tend(self, now: float) -> None:
        """Take the notice down DPI_NOTICE_CLOSE after the last step."""
        if self._close_at is not None and self._close_at <= now and not self._sending:
            # A post on its way sets the time again when its id comes back. The id is there:
            # post's `done` sets the time with it, and both are cleared together.
            self.desktop.close_notification(cast("int", self._shown))
            self._shown = self._close_at = None

    def post(self, mouse: str, dpi: int) -> None:
        """The notice that `mouse` is on `dpi` now, worded by DPI_NOTICE_TITLE and posted as
        _post posts one."""
        self._post(DPI_NOTICE_TITLE.format(mouse, dpi))

    def _post(self, summary: str) -> None:
        """`summary` in place of the last notice within DPI_NOTICE_REPLACE of its post; later,
        the last is closed and a new one posted. A step made while a post is on its way waits
        for its id, and of several only the last is said. With the switch off, nothing."""
        if not self.wanted():
            return
        if self._sending:
            self._next = summary
            return
        now = time.monotonic()
        if self._shown is not None and now >= self._replace_until:
            self.desktop.close_notification(self._shown)
            self._shown = self._close_at = None
        # From the send: the Shell starts the banner's time when the post reaches it, later.
        replace_until = now + DPI_NOTICE_REPLACE

        def done(notice: int | None) -> None:
            self._sending = False
            if notice is not None:
                self._shown = notice
                self._replace_until = replace_until
                self._close_at = time.monotonic() + DPI_NOTICE_CLOSE
            waiting, self._next = self._next, None
            if waiting is not None:
                self._post(waiting)

        self._sending = self.desktop.notify(
            summary, "", DPI_NOTICE_ICON, replaces_id=self._shown or 0, done=done,
            transient=True)


class Session:
    """Follows the desktop over D-Bus (desktop.Desktop) and acts on it."""

    def __init__(self, daemon: Daemon) -> None:
        self.d = daemon
        #: logind's LockedHint and GNOME's screen blank; None until said.
        self.session_locked: bool | None = None
        self.screen_blanked: bool | None = None
        #: logind's Active, None until said; None counts as active, so typing works without logind.
        self.session_active: bool | None = None
        #: When the lights last went down for a lock or a blank (LOCK_SETTLE).
        self._went_down_at = float("-inf")
        #: From logind's announcement of a suspend to the resume: input does
        #: not light the board, and no macro starts (why_away).
        self._asleep = False
        #: Idle levels a key press while locked put off, by ms, and when due.
        self._idle_owed: dict[int, float] = {}
        #: The waits on Mutter's idle time, by (group, ms): a serial, so a reply
        #: for a wait dropped or armed again since is dropped, and when its idle
        #: time is read next, None while a read is on its way (idle_read).
        self._idle_reads: dict[tuple[str, int], tuple[int, float | None]] = {}
        self._idle_serial = 0
        #: The whole desktop's idle level, keyboard and mouse alike: dim after idle_dim_after
        #: without input, dark idle_off_after later, lit at the next input; a synced mouse goes
        #: by it (Keyboard.mouse_level). And the (idle_dim_after, idle_off_after) it
        #: follows, None while sync is off or never dim is on.
        self.idle_level = LIT
        self._idle_times: tuple[int, int] | None = None
        #: The keyboard's low-battery notices and their poll.
        self.keyboard_battery = KeyboardBattery(daemon)
        #: Started in run(), so a Daemon built and not run connects to nothing.
        self.desktop = Desktop(locked=self.on_locked, blanked=self.on_blanked,
                               sleeping=self.on_sleeping, user_active=self.on_watched_input,
                               focus=lambda pid: self.d.games.focus.shell_focus(pid),
                               active=self.on_active)
        #: The notice at a DPI button's step, on the desktop above.
        self.dpi_notice = DpiNotice(self.desktop, lambda: self.d.config.dpi_notices)
        self._desktop_started = False

    def commands(self) -> dict[str, Callable[[dict], dict]]:
        return {
            "dpi_notices": self._cmd_dpi_notices,
            "lock_lights": self._cmd_lock_lights,
        }

    # -- the desktop's connection --------------------------------------------

    def start_desktop(self) -> None:
        """Connect to the session's buses and register the desktop's GLib eventfd with the
        loop; without python3-gi the lock, suspend and notices are all off."""
        self._desktop_started = True
        if not self.desktop.start():
            return
        self.d.selector.register(self.desktop.fileno(), selectors.EVENT_READ, "desktop")
        if self.d.config.lights_off_when_locked:
            self.desktop.take_inhibitor()
        self.watch_idle_for_both(paint=False)

    def stop_desktop(self) -> None:
        """At the stop: take the desktop's eventfd out of the loop and close its connections,
        which releases the inhibitor, so a suspend waiting on it goes."""
        if self.desktop.available:
            with contextlib.suppress(KeyError):
                self.d.selector.unregister(self.desktop.fileno())
        self.desktop.close()

    def desktop_missing(self) -> str | None:
        """What lights off when locked needs and does not have, in words."""
        if not self._desktop_started:
            return None
        if not self.desktop.available:
            return ("python3-gi is not installed, so the lights do not follow the screen "
                    "lock or a suspend")
        up = self.desktop.status()
        missing = []
        if not up["logind"]:
            missing.append("logind is not reachable, so the lights do not follow the screen "
                           "lock or a suspend")
        if not up["idle_monitor"]:
            missing.append("GNOME's idle monitor is not reachable, so input while the screen "
                           "is locked does not light the keyboard; a G, M or MR key or the "
                           "unlock does")
        return "; ".join(missing) or None

    def tend_desktop(self) -> None:
        """Run what is due, including the desktop's own timeouts, which do not wake its fd."""
        due = self.desktop.deadline()
        if due is not None and due <= time.monotonic():
            self.desktop.dispatch()
        now = time.monotonic()
        for ms, at in sorted(self._idle_owed.items()):
            if at <= now:
                del self._idle_owed[ms]
                self.idle_reached(ms)
        self.read_idle_times(now)
        self.dpi_notice.tend(now)
        self.keyboard_battery.poll_battery()

    def deadlines(self) -> list[float]:
        """When tend_desktop is next due. A read or a notice on its way is left out: its reply
        wakes the fd."""
        due = list(self._idle_owed.values())
        due.extend(at for _serial, at in self._idle_reads.values() if at is not None)
        desktop_due = self.desktop.deadline()
        if desktop_due is not None:
            due.append(desktop_due)
        due.extend(self.dpi_notice.deadlines())
        due.extend(self.keyboard_battery.deadlines())
        return due

    # -- the lock and the blank ----------------------------------------------

    @property
    def away(self) -> bool:
        """Whether the screen is locked or blanked."""
        return bool(self.session_locked) or bool(self.screen_blanked)

    def stop_away(self, why: str) -> None:
        """Stop a macro, and let go of what the keys, the buttons and a wheel tilt hold; `why`
        is for the journal."""
        self.d.bindings.stop_macro_away(why)
        self.d.bindings.let_go_away(why)
        self.d.mice.buttons.stop_tilts(why)

    def on_locked(self, locked: bool) -> None:
        """logind's LockedHint, at start and at each change."""
        self.session_locked = locked
        if locked:
            self.stop_away("the screen locked")
            self.lights_down("the screen locked")
        else:
            self.lights_back("the screen was unlocked")

    def on_blanked(self, blanked: bool) -> None:
        """GNOME's screen blank, which comes without a lock where locking is
        off: it counts as a lock."""
        self.screen_blanked = blanked
        if blanked:
            self.stop_away("the screen blanked")
            self.lights_down("the screen blanked")
        else:
            self.lights_back("the screen came back")

    def on_active(self, active: bool) -> None:
        """logind's Active, at start and at each change. Another session
        or a text console has the seat while it is False, and the virtual
        devices would type there. The lights are left as they are, but on a
        keyboard whose G keys type its own keys, which gets its own profile
        back until the session is active (OnboardHold.hold_for_session)."""
        was, self.session_active = self.session_active, active
        if not active:
            log.info("the session went inactive; nothing is typed until it is active again")
            self.stop_away("the session went inactive")
            self.d.keyboard.hold.hold_for_session()
        elif was is False:
            log.info("the session is active again")

    def why_inactive(self) -> str | None:
        """Why no binding may type now, or None when one may. Only an
        inactive session refuses: a lock alone refuses no key binding, so a
        G key bound to Shift still types a capital in the password."""
        if self.session_active is False:
            return "the session is not active"
        return None

    def why_away(self) -> str | None:
        """Why no macro may start now, or None when one may. A blank counts:
        a G key does not bring the screen back, so a macro would type behind
        it. Without python3-gi nothing is refused."""
        inactive = self.why_inactive()
        if inactive is not None:
            return inactive
        if self.session_locked:
            return "the screen is locked"
        if self.screen_blanked:
            return "the screen is blank"
        if self._asleep:
            return "the machine is going to sleep"
        return None

    def away_word(self) -> str:
        """The journal's word for the screen while away: a lock is named over a blank."""
        return "locked" if self.session_locked else "blank"

    def lights_down(self, why: str, paint: bool = True) -> None:
        """Locked or blanked: dark until the next input (on_user_active)."""
        if not self.d.config.lights_off_when_locked:
            return
        self._idle_owed.clear()
        self.unwatch(LOCK_WATCHES)
        self.d.keyboard.set_lights(DARK, why, paint=paint)
        self._went_down_at = time.monotonic()
        self.desktop.add_user_active_watch(LOCK_WATCHES)

    def lights_back(self, why: str) -> None:
        """Unlocked or unblanked: lit, once neither holds."""
        if self.away or not self.d.config.lights_off_when_locked:
            return
        self._idle_owed.clear()
        self.unwatch(LOCK_WATCHES)
        self.d.keyboard.set_lights(LIT, why)

    # -- the desktop's idle time ----------------------------------------------
    # Read with GetIdletime, not watched with AddIdleWatch: Mutter holds an idle
    # watch back while an idle inhibitor is held, as Steam's overlay holds one
    # while a game runs, and GetIdletime counts on through it
    # (desktop.Desktop.idle_time). Its user-active watch fires whatever inhibits.

    def read_idle_at(self, group: str, ms: int, due: float) -> None:
        """Read Mutter's idle time at `due` for `group`'s wait of `ms`, in place of any
        read for that wait before it (idle_read)."""
        self._idle_serial += 1
        self._idle_reads[(group, ms)] = (self._idle_serial, due)

    def drop_idle_reads(self, group: str) -> None:
        for key in [key for key in self._idle_reads if key[0] == group]:
            del self._idle_reads[key]

    def unwatch(self, group: str) -> None:
        """`group`'s user-active watch and its idle time reads, gone."""
        self.desktop.remove_watches(group)
        self.drop_idle_reads(group)

    def read_idle_times(self, now: float) -> None:
        """Ask for the idle time for each wait whose read is due."""
        for key, (serial, due) in sorted(self._idle_reads.items()):
            if due is None or due > now:
                continue
            self._idle_reads[key] = (serial, None)
            done = partial(self.idle_read, key, serial)
            if not self.desktop.idle_time(done):
                done(None)

    def idle_read(self, key: tuple[str, int], serial: int, ms: int | None) -> None:
        """Mutter's idle time, `ms`, read for `key`'s wait. Reached: the wait is over and its
        group acts on it (on_idle, on_idle_for_both). Short of it: read again when the rest
        has run out, except for the desktop's level dark already, which a longer idle_off_after
        dims again (watch_idle_for_both). None, no answer: read again a whole wait later."""
        if self._idle_reads.get(key) != (serial, None):
            return          # dropped or armed again while the read was on its way
        group, wait = key
        again = group == MOUSE_WATCHES and self.idle_level == DARK
        if ms is None or (ms < wait and not again):
            self._idle_reads[key] = (serial, time.monotonic() + (wait - (ms or 0)) / 1000)
            return
        del self._idle_reads[key]
        if group == MOUSE_WATCHES:
            self.on_idle_for_both(ms)
        else:
            self.on_idle(wait)

    # -- input while locked --------------------------------------------------

    def on_watched_input(self, group: str) -> None:
        """A user-active watch fired: the lock's (on_user_active) or a synced mouse's."""
        if group == MOUSE_WATCHES:
            self.on_input_after_idle()
        else:
            self.on_user_active()

    def watch_idle(self) -> None:
        """Arm the waits that dim and darken a locked screen's lights, counted from now."""
        now = time.monotonic()
        for ms in (LOCKED_DIM_MS, LOCKED_DARK_MS):
            self.read_idle_at(LOCK_WATCHES, ms, now + ms / 1000)

    def on_user_active(self) -> None:
        """Input Mutter saw while locked: lit, and the waits for its dim and dark again."""
        if not (self.d.config.lights_off_when_locked and self.away) or self._asleep:
            return          # a suspend under way: the resume decides
        if time.monotonic() - self._went_down_at < LOCK_SETTLE:
            self.desktop.add_user_active_watch(LOCK_WATCHES)
            return
        self._idle_owed.clear()
        self.d.keyboard.set_lights(LIT, f"input while the screen is {self.away_word()}")
        self.watch_idle()

    def on_idle(self, ms: int) -> None:
        """Mutter's idle time reached `ms` while locked (idle_read), unless a key
        Mutter never saw put that level off (tend_desktop ends it then)."""
        if ms not in self._idle_owed:
            self.idle_reached(ms)

    def idle_reached(self, ms: int) -> None:
        """Dim at LOCKED_DIM_MS, from lit only; dark at LOCKED_DARK_MS."""
        if not (self.d.config.lights_off_when_locked and self.away):
            return
        level = DARK if ms >= LOCKED_DARK_MS else DIM
        if level == DIM and self.d.keyboard.lock_lights != LIT:
            return
        self.d.keyboard.set_lights(
            level, f"{ms // 1000} s without input while the screen is {self.away_word()}")
        self.desktop.add_user_active_watch(LOCK_WATCHES)

    def note_key_input(self, paint: bool = True) -> bool:
        """A G, M or MR key pressed while locked, which Mutter never sees: lit,
        with dim and dark due again from the press. `paint` False leaves the
        repaint to the caller. True when the lights came back."""
        if not (self.d.config.lights_off_when_locked and self.away) or self._asleep:
            return False
        now = time.monotonic()
        self._idle_owed = {ms: now + ms / 1000 for ms in (LOCKED_DIM_MS, LOCKED_DARK_MS)}
        self.desktop.add_user_active_watch(LOCK_WATCHES)
        if self.d.keyboard.lock_lights == LIT:
            return False
        return self.d.keyboard.set_lights(
            LIT, f"a key pressed while the screen is {self.away_word()}", paint=paint)

    # -- a synced mouse and the whole desktop's idleness ----------------------

    @property
    def idle_for_both(self) -> bool:
        """Nothing on the desktop, keyboard or mouse, touched for idle_dim_after."""
        return self.idle_level != LIT

    def watch_idle_for_both(self, paint: bool = True) -> None:
        """Follow the desktop's idle level while color sync is on and never dim off: dim at
        idle_dim_after of idle time, dark idle_off_after later. A new idle_dim_after, never dim
        or sync switch starts it over lit, the mouse painted unless `paint` is False. Times
        already followed change nothing; a new idle_off_after alone moves only the dark, and
        takes the desktop's level, dark already, back to dim while the new dark is still ahead
        (the owner's decision of 2026-10-07). A synced mouse shows that dim only while the
        keyboard's own idle level is not known (Keyboard.mouse_level)."""
        config = self.d.config
        times = ((config.idle_dim_after, config.idle_off_after)
                 if config.mouse_sync_color and not config.never_dim else None)
        before, self._idle_times = self._idle_times, times
        if times == before:
            return
        if times is not None and before is not None and times[0] == before[0]:
            if self.idle_level != LIT:
                # Read at once: the dark counts from the dim, and the idle time is not known here.
                self.drop_idle_reads(MOUSE_WATCHES)
                self.read_idle_at(MOUSE_WATCHES, sum(times) * 1000, time.monotonic())
            return
        self.unwatch(MOUSE_WATCHES)
        was, self.idle_level = self.idle_level, LIT
        if times is not None:
            self.read_idle_at(MOUSE_WATCHES, times[0] * 1000, time.monotonic() + times[0])
        if was != LIT and paint:
            self.d.mice.color.sync_mouse_color(self.d.keyboard.shown_color())

    def on_idle_for_both(self, ms: int) -> None:
        """The desktop's idle time, `ms`, reached a wait, or was read short of a longer dark
        while dark (idle_read): dim at idle_dim_after, dark idle_off_after later, until the next
        input (on_input_after_idle). A synced mouse shows the keyboard's own idle level, this one
        while the keyboard's own is not known, or the lock's when that is as dark or darker
        (Keyboard.mouse_level)."""
        # The times are there: watch_idle_for_both drops this group's reads when it stops
        # following them.
        dark = sum(cast("tuple[int, int]", self._idle_times)) * 1000
        level = DARK if ms >= dark else DIM
        if level == self.idle_level:
            return          # dark already, and the new idle_off_after has run out as well
        before, self.idle_level = self.idle_level, level
        if before == LIT:
            self.desktop.add_user_active_watch(MOUSE_WATCHES)
        if self.idle_level == DIM:
            self.read_idle_at(MOUSE_WATCHES, dark, time.monotonic() + (dark - ms) / 1000)
        own = self.d.keyboard.idle_level_known()
        # Said only for a mouse with lights; the level is followed without one, for a mouse
        # attached later.
        if self.d.mice.color.mouse_with_lights() and (before == LIT or not own):
            # A lock's level down without a lock or a blank is the suspend's dark
            # (going_to_sleep), held until the resume.
            lock = self.d.keyboard.lock_lights
            shown = self.d.keyboard.mouse_level()
            if lock != LIT and shown == lock:
                follows = ("a synced mouse follows the lock until the unlock"
                           if self.session_locked else
                           "a synced mouse follows the blank until the screen comes back"
                           if self.screen_blanked else
                           "a synced mouse stays dark until the machine wakes up")
            else:
                follows = ("a synced mouse follows the keyboard's idle level" if own else
                           "the keyboard's own idle level is not known (away, without lighting, "
                           "or no report from it since it stopped answering), so a synced mouse "
                           "follows the keyboard's idle times") + f", now {LEVEL_WORDS[shown]}"
            log.info("the desktop has been idle %d s; %s", ms // 1000, follows)
        self.d.mice.color.sync_mouse_color(self.d.keyboard.shown_color())

    def on_input_after_idle(self) -> None:
        """Input after the desktop went idle: lit, a synced mouse with it, and idle_dim_after
        counted from now. The keyboard is lit by its own wake notice, which only a key
        brings."""
        if self.idle_level == LIT:
            return
        self.idle_level = LIT
        self.drop_idle_reads(MOUSE_WATCHES)
        # The times are there: watch_idle_for_both sets the level lit when it stops following
        # them.
        dim = cast("tuple[int, int]", self._idle_times)[0]
        self.read_idle_at(MOUSE_WATCHES, dim * 1000, time.monotonic() + dim)
        if self.d.mice.color.mouse_with_lights():
            log.info("input after the desktop went idle; a synced mouse no longer dims or "
                     "darkens for the desktop's idleness")
        self.d.mice.color.sync_mouse_color(self.d.keyboard.shown_color())

    # -- suspend -------------------------------------------------------------

    def on_sleeping(self, going: bool) -> None:
        """logind's PrepareForSleep: true before a suspend, false after."""
        if going:
            self.stop_away("the machine is going to sleep")
            self.going_to_sleep()
        else:
            self.woke_up()

    def going_to_sleep(self) -> None:
        """Dark, then let the suspend go. logind waits for the delay inhibitor at most
        InhibitDelayMaxSec (5 s by default, 30 s with unattended-upgrades installed), so a
        keyboard not dark already is asked on the probe's budget first. With the switch off
        nothing is done to the lights."""
        self._asleep = True
        if not self.d.config.lights_off_when_locked:
            log.info("the machine is going to sleep")
            return
        self._idle_owed.clear()
        said = self.d.keyboard.dark_for_sleep()
        self.desktop.release_inhibitor()
        log.info("the machine is going to sleep: %s; letting the suspend go", said)

    def woke_up(self) -> None:
        """After a resume: every device set up again, whatever the switch says. With it on, a
        new inhibitor, and the lights dark with a user-active watch while locked or blank;
        otherwise lit. The level is set before the keyboard is set up, so it is painted once."""
        log.info("the machine woke up; setting every device up again")
        self._asleep = False
        on = self.d.config.lights_off_when_locked
        if on:
            self.desktop.take_inhibitor()
        self._idle_owed.clear()
        self.d.keyboard.keyboard_awake(lock=DARK if on and self.away else LIT)
        if self.d.keyboard.lock_lights == DARK:
            self.unwatch(LOCK_WATCHES)
            self.desktop.add_user_active_watch(LOCK_WATCHES)
        self.d.mice.recheck_mice()
        self.d.headset.recheck()
        self.d.g432.recheck()
        self.d.keyboard.handlers.recheck_after_resume()

    # -- commands ------------------------------------------------------------

    def _cmd_dpi_notices(self, request: dict) -> dict:
        """The notice at a DPI button's step, on or off; off takes nothing down at once."""
        value = on_or_off(request.get("value"))
        if value is None:
            return {"ok": False, "error": switch_refusal("dpi_notices", request.get("value"))}
        self.d.save_setting(dpi_notices=value)
        return {"ok": True, "dpi_notices": value}

    def _cmd_lock_lights(self, request: dict) -> dict:
        """Lights off when locked, on or off; a resume still sets the devices
        up. The value the switch has already changes nothing, as at a reload:
        on again would darken a board lit by input while locked."""
        value = on_or_off(request.get("value"))
        if value is None:
            return {"ok": False, "error": switch_refusal("lock_lights", request.get("value"))}
        was = self.d.config.lights_off_when_locked
        self.d.save_setting(lights_off_when_locked=value)
        if value != was:
            self.lock_lights_changed(value)
        return {"ok": True, "lights_off_when_locked": value, "lights": self.d.keyboard.lights}

    def lock_lights_changed(self, on: bool, paint: bool = True) -> None:
        """Act on the switch changed to `on` or off, by the command or a reload. Off:
        the lights back, the watches and the inhibitor gone. `paint` False
        leaves the paint to the caller."""
        if on:
            self.desktop.take_inhibitor()
            if self.away:
                self.lights_down("lights off when locked turned on while the screen is "
                                 f"{self.away_word()}", paint)
        else:
            self._idle_owed.clear()
            self.unwatch(LOCK_WATCHES)
            self.desktop.release_inhibitor()
            self.d.keyboard.set_lights(LIT, "lights off when locked turned off", paint=paint)
