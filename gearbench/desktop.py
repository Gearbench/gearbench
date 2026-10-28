"""The desktop session over D-Bus: lock, screen blank, suspend, idle, notices, the tray icon
(sni.py) and the focused window from Gearbench's GNOME Shell extension (focus.py), on
dbusloop.py's context and connections, whose hard rules hold here too. One more:

- No idle watch: Mutter holds one back while an idle inhibitor is held, as Steam's overlay
  does during a game. `idle_time` counts on through it.
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from . import tray as tray_model
from .dbusloop import (
    ACCESS_DENIED,
    CALL_TIMEOUT,
    RETRY,
    Bus,
    Loop,
    bus_addresses,
    said,
    trying_again,
    utf8,
)
from .sni import SHELL, TrayExport
from .wording import SERVICE

if TYPE_CHECKING:
    from gi.repository import Gio, GLib

log = logging.getLogger("gearbench")

LOGIN1 = "org.freedesktop.login1"
LOGIN1_PATH = "/org/freedesktop/login1"
MANAGER = "org.freedesktop.login1.Manager"
USER = "org.freedesktop.login1.User"
#: The caller's own user: logind resolves `self` from the caller's pid.
USER_SELF = "/org/freedesktop/login1/user/self"
SESSION = "org.freedesktop.login1.Session"
#: The Class logind gives a display manager's login screen session, GDM's.
GREETER = "greeter"
PROPERTIES = "org.freedesktop.DBus.Properties"
SCREENSAVER = "org.gnome.ScreenSaver"
SCREENSAVER_PATH = "/org/gnome/ScreenSaver"
NOTIFICATIONS = "org.freedesktop.Notifications"
NOTIFICATIONS_PATH = "/org/freedesktop/Notifications"
IDLE_MONITOR = "org.gnome.Mutter.IdleMonitor"
IDLE_MONITOR_PATH = "/org/gnome/Mutter/IdleMonitor/Core"
#: Gearbench's GNOME Shell extension, gnome-shell/gearbench-focus@gearbench.github.io.
FOCUS = "io.github.gearbench.Gearbench.Focus"
FOCUS_PATH = "/io/github/gearbench/Gearbench/Focus"
EXTENSION_UUID = "gearbench-focus@gearbench.github.io"
#: Its metadata.json's name, which GNOME's Extensions app lists it by.
EXTENSION_NAME = "Gearbench focus"
#: GNOME Shell's extension interface, the one `gnome-extensions enable` calls, at its path
#: on the Shell's own name, sni.SHELL. The name org.gnome.Shell.Extensions is a service D-Bus
#: starts for a call, which passes it on to there and exits 2 s after its last caller
#: (GNOME Shell 50.1, js/dbusServices/extensions/extensionsService.js and dbusService.js).
SHELL_EXTENSIONS = "org.gnome.Shell.Extensions"
SHELL_EXTENSIONS_PATH = "/org/gnome/Shell"
APPLICATION = "org.freedesktop.Application"
#: The window's object path: the app id, dots to slashes, per the Desktop Entry spec.
APP_PATH = "/io/github/gearbench/Gearbench"

#: The desktop entry's Name.
APP_NAME = "Gearbench"
#: What Gearbench is, in the one sentence the About dialog, the desktop entry's Comment, the
#: metainfo's summary, pyproject.toml's description, `gearbench-ctl --help` and the man
#: page's NAME and DESCRIPTION give: the
#: device families, not each model (the owner's decision of 2026-10-22), within AppStream's
#: 90 characters for a summary (the owner's decision of 2026-10-25).
APP_SUMMARY = ("Lighting, G keys, DPI and macros for Logitech G keyboards and mice, and headset "
               "settings")
#: The desktop entry's id, also gui.window.APP_ID. GNOME finds the app through it, which gives
#: Gearbench's notices a switch in Settings > Notifications.
DESKTOP_ENTRY = "io.github.gearbench.Gearbench"
#: The notification spec's urgency byte: 0 low, 1 normal, 2 critical.
URGENCY_NORMAL = 1

#: The delay inhibitor's Why, as `systemd-inhibit --list` shows it; its Who is APP_NAME.
INHIBIT_WHY = "Turning the keyboard lights off before the machine sleeps"
#: Said when logind's Inhibit reply names no fd that came with it: none came, or its index
#: is outside those that did.
INHIBIT_NO_FD = "logind's Inhibit reply lacked its fd, so a suspend may not wait for the lights"
#: Said at debug when GetIdletime fails, unless halt() canceled it or its bus closed under
#: it: the session reads again a wait later.
IDLE_TIME_UNANSWERED = "Mutter's IdleMonitor did not answer GetIdletime: %s"

#: What is off while logind's Active is not followed: Session.why_inactive takes unknown for
#: active.
NOT_HELD_BACK = "key bindings and macros are not held back from another account or a text console"
#: What is off while the graphical session's LockedHint or Active cannot be read.
UNREAD_SESSION = {"LockedHint": "the lights do not follow the screen lock", "Active": NOT_HELD_BACK}
#: What the session bus brings, all off without it or python3-gi.
ON_THE_SESSION_BUS = ("the screen blank, the idle monitor, the focus from Gearbench's GNOME "
                      "Shell extension, the notices and the tray icon")


def session_class() -> str | None:
    """logind's Class for this account's graphical session (user/self's Display): GREETER at a
    login screen. `gearbench-ctl daemon` reads it once, before the loop: each Get blocks up to
    CALL_TIMEOUT, the connect's authentication too (a timer cancels it), and its Hello up to
    GDBus's 25 s. None without python3-gi, logind or a graphical session."""
    try:
        import gi  # noqa: PLC0415 -- optional: the daemon runs without python3-gi
        gi.require_version("Gio", "2.0")
        gi.require_version("GLib", "2.0")
        from gi.repository import Gio, GLib  # noqa: PLC0415 -- optional, as gi above
    except (ImportError, ValueError):
        return None
    conn = None
    # GDBus's authentication has no timeout: a bus that accepts and never answers holds it
    # for good. Gio.Cancellable.cancel is thread-safe; Hello, after it, takes no cancellable
    # and times out in GDBus's 25 s.
    cancellable = Gio.Cancellable()
    timer = threading.Timer(CALL_TIMEOUT, cancellable.cancel)
    timer.daemon = True
    try:
        timer.start()
        conn = Gio.DBusConnection.new_for_address_sync(
            bus_addresses()[0], Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
            | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, cancellable)

        def get(path: str, interface: str, name: str) -> object:
            return conn.call_sync(
                LOGIN1, path, PROPERTIES, "Get", GLib.Variant("(ss)", (interface, name)),
                GLib.VariantType("(v)"), Gio.DBusCallFlags.NONE, round(CALL_TIMEOUT * 1000),
                None).unpack()[0]
        # logind's org.freedesktop.login1.User gives Display as (so): a session id and its path.
        _id, path = cast("tuple[str, str]", get(USER_SELF, USER, "Display"))
        found = get(path, SESSION, "Class") if path and path != "/" else None
    except GLib.Error as err:
        # Only the connect takes `cancellable`, and only the timer cancels it.
        why = (f"the system bus did not answer within {CALL_TIMEOUT:g} s"
               if err.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED) else said(err))
        log.debug("cannot read the session's class from logind: %s", why)
        return None
    finally:
        timer.cancel()
        if conn is not None:
            # Fails on a connection the bus already closed.
            with contextlib.suppress(GLib.Error):
                conn.close_sync(None)
    return found if isinstance(found, str) else None


class Desktop:
    """The session's lock, blank, sleep and idle state, and its notices.

    Build it, then `start()`. Until a successful start nothing reaches a bus: `fileno()` is
    -1, `status()` is all False, and a wanted inhibitor, watch or tray icon is only recorded.
    `available`, `fileno()`, `dispatch()` and `deadline()` are dbusloop.Loop's, and the tray
    icon is sni.TrayExport's. The callbacks below run from `dispatch()`:

    - `locked(bool)`, `active(bool)`: LockedHint and Active of user/self's Display session;
      Active is False while another session has the seat. The daemon's own logind session
      (user@.service) is a manager session, so `session/auto` is the wrong one, and
      PropertiesChanged comes only from the real path. The session's Lock and Unlock
      signals are requests, not the state. Neither is called without logind or while
      Display cannot be read.
    - `blanked(bool)`: the ScreenSaver's state. GNOME blanks without locking when
      lock-enabled is off.
    - `sleeping(bool)`: PrepareForSleep, True before a suspend and False after.
    - `user_active(group)`: `group`'s user-active watch fired, once, on the next input.
    - `focus(pid)`: the extension's focused window, 0 for none, None while it is not there.
      It exports on the Shell's own connection, so its signals come from org.gnome.Shell.
    """

    def __init__(self, *, locked: Callable[[bool], None], blanked: Callable[[bool], None],
                 sleeping: Callable[[bool], None], user_active: Callable[[str], None],
                 focus: Callable[[int | None], None], active: Callable[[bool], None],
                 system_address: str | None = None,
                 session_address: str | None = None) -> None:
        self._on_locked = locked
        self._on_active = active
        self._on_blanked = blanked
        self._on_sleeping = sleeping
        self._on_user_active = user_active
        self._on_focus = focus
        self._loop = Loop()
        self._system = Bus("system", system_address)
        self._session = Bus("session", session_address)
        self._tray_export = TrayExport(self._loop, self._session)
        #: The last LockedHint, Active and ScreenSaver state passed on; None
        #: before the first reading, so that one always goes through.
        self._locked: bool | None = None
        self._active: bool | None = None
        self._blanked: bool | None = None
        #: The graphical session's path, and its PropertiesChanged subscription.
        self._session_path: str | None = None
        self._session_sub: int | None = None
        #: The delay inhibitor: whether one is wanted, logind's fd while one is held, and
        #: whether an Inhibit call is on its way.
        self._want_inhibitor = False
        self._inhibitor_fd: int | None = None
        self._inhibiting = False
        #: The groups that want a user-active watch, each watch's group by its IdleMonitor id,
        #: and the groups whose Add is on its way. A group's epoch moves on at remove_watches(),
        #: so an Add answered after that is taken back.
        self._wanted: set[str] = set()
        self._watches: dict[int, str] = {}
        self._adding: set[str] = set()
        self._watch_epochs: dict[str, int] = {}
        #: The extension's last focus passed on, None while it is not in
        #: force; and a count of its signals, so a Subscribe answer that a
        #: FocusChanged overtook is dropped.
        self._focus: int | None = None
        self._focus_signals = 0

    # ------------------------------------------------------------ the loop

    def start(self) -> bool:
        """Make the loop's context and start connecting both buses. False without python3-gi
        or its Gio typelib, said once at warning."""
        missing = self._loop.start()
        if missing is not None:
            log.warning("no python3-gi (%s), so the lights do not follow the screen lock or "
                        "a suspend, %s, and %s are off", missing, NOT_HELD_BACK,
                        ON_THE_SESSION_BUS)
            return False
        defaults = bus_addresses()
        self._system.address = self._system.address or defaults[0]
        self._session.address = self._session.address or defaults[1]
        self._connect(self._system)
        if self._session.address is None:
            log.warning("no session bus address (DBUS_SESSION_BUS_ADDRESS is not set and "
                        "there is no $XDG_RUNTIME_DIR/bus), so %s are off", ON_THE_SESSION_BUS)
        else:
            self._connect(self._session)
        return True

    @property
    def available(self) -> bool:
        return self._loop.available

    def fileno(self) -> int:
        return self._loop.fileno()

    def dispatch(self) -> None:
        self._loop.dispatch()

    def deadline(self) -> float | None:
        return self._loop.deadline()

    def status(self) -> dict[str, bool]:
        """Whether logind, a notification server and Mutter's IdleMonitor are there to use."""
        def up(bus: Bus, name: str) -> bool:
            return bus.conn is not None and bus.owners.get(name) is not None
        return {"logind": up(self._system, LOGIN1),
                "notifications": up(self._session, NOTIFICATIONS),
                "idle_monitor": up(self._session, IDLE_MONITOR)}

    @property
    def inhibiting(self) -> bool:
        """Whether a delay inhibitor is held; for the tests."""
        return self._inhibitor_fd is not None

    def close(self) -> None:
        """Release the inhibitor and close the connections, the tray item's with the session's.
        The focus goes with them unsaid: the daemon is stopping, not falling back."""
        self.release_inhibitor()
        self._focus = None
        if not self.available:
            return
        for bus in (self._system, self._session):
            bus.halt()
            conn = self._drop(bus)
            if conn is not None:
                self._loop.close(conn)

    # ------------------------------------------------------ the connections

    def _connect(self, bus: Bus) -> None:
        # start() leaves a bus without an address unconnected.
        self._loop.connect(bus, lambda _conn: self._connected(bus),
                           lambda why: self._failed(bus, why),
                           lambda error: self._closed(bus, error))

    def _failed(self, bus: Bus, why: str) -> None:
        """A connect that failed, or a bus that hung up right after Hello, which no "closed"
        handler tries again: said once a topic, and tried again further apart."""
        self._loop.say(f"connect {bus.name}", f"cannot connect to the {bus.name} bus at "
                       f"{bus.address}: {why}; {trying_again()}")
        self._retry(bus)

    def _connected(self, bus: Bus) -> None:
        self._loop.quiet(f"connect {bus.name}")
        if bus.failed:
            log.info("connected to the %s bus again", bus.name)
            bus.failed = False
        else:
            log.debug("connected to the %s bus at %s", bus.name, bus.address)
        if bus is self._system:
            self._setup_system(bus)
        else:
            self._setup_session(bus)

    def _closed(self, bus: Bus, error: GLib.Error | None) -> None:
        why = f" ({error.message})" if error is not None else ""
        log.warning("the %s bus connection closed%s; trying again in %g s",
                    bus.name, why, bus.backoff)
        self._drop(bus)
        self._retry(bus)

    def _retry(self, bus: Bus) -> None:
        """Connect again after the backoff, and double it for next time."""
        bus.failed = True
        self._loop.backoff(bus, lambda: self._connect(bus))

    def _drop(self, bus: Bus) -> Gio.DBusConnection | None:
        """Forget a connection and everything that died with it (Loop.drop); the connection,
        still open, or None without one."""
        conn = self._loop.drop(bus)
        if bus is self._system:
            self._session_path = self._session_sub = None
            self._inhibiting = False
        else:
            self._set_focus(None)
            # Mutter drops a connection's watches when it goes; the wanted
            # ones are added again on the next connection.
            self._watches.clear()
            self._adding.clear()
            self._tray_export.dropped()
        return conn

    # ----------------------------------------------------- logind, the system bus

    def _setup_system(self, bus: Bus) -> None:
        self._loop.subscribe(bus, LOGIN1, MANAGER, "PrepareForSleep", LOGIN1_PATH,
                             lambda args: self._on_sleeping(args[0]), signature="(b)")
        self._loop.watch(bus, LOGIN1, appeared=self._login1_appeared, vanished=self._login1_gone)

    def _login1_appeared(self, owner: str) -> None:
        self._read_display(owner)
        self._inhibit()

    def _read_display(self, owner: str) -> None:
        """user/self's Display, taken, and asked again RETRY after a failure, while this
        logind and this connection last: the lock and Active are followed only from that
        session's path."""
        conn = self._system.conn

        # A logind that went away or was replaced is asked by its successor's
        # _login1_appeared; one that gave up its name with its connection open still answers.
        def current() -> bool:
            return self._system.conn is conn and self._system.owners.get(LOGIN1) == owner

        def got(reply: tuple) -> None:
            if current():
                self._got_display(reply)

        def failed(err: GLib.Error) -> None:
            if self._loop.benign(err) or not current():
                return
            self._loop.say("display", f"cannot read the graphical session from logind "
                                      f"({said(err)}), so the lights do not follow the screen "
                                      f"lock, and {NOT_HELD_BACK}; trying again every "
                                      f"{RETRY:g} s")
            self._loop.timeout(RETRY, lambda: self._read_display(owner) if current() else None)
        self._loop.call(self._system, LOGIN1, USER_SELF, PROPERTIES, "Get",
                        self._loop.glib.Variant("(ss)", (USER, "Display")), "(v)", got, failed)

    def _login1_gone(self) -> None:
        self._forget_session()
        self._inhibiting = False

    def _forget_session(self) -> None:
        if self._session_sub is not None:
            # A subscription outlasts no connection: _drop forgets it with the one it was on.
            cast("Gio.DBusConnection", self._system.conn).signal_unsubscribe(self._session_sub)
            self._system.subscriptions.remove(self._session_sub)
        self._session_sub = self._session_path = None

    def _got_display(self, reply: tuple) -> None:
        (_id, path), = reply
        self._forget_session()
        if not path or path == "/":
            self._loop.say("display", "logind names no graphical session for this user, so "
                                      f"the lights do not follow the screen lock, and "
                                      f"{NOT_HELD_BACK}")
        elif not cast("Gio.DBusConnection", self._system.conn).is_closed():
            # Not after the bus went, between this answer and its dispatch: GLib prints a
            # critical line of its own for a subscription or a watch made on a closed
            # connection. `closed` is dispatched next, and the next connection reads Display
            # again.
            self._loop.quiet("display")
            self._session_path = path
            self._session_sub = self._loop.subscribe(self._system, LOGIN1, PROPERTIES,
                                                     "PropertiesChanged", path,
                                                     self._session_changed, arg0=SESSION,
                                                     signature="(sa{sv}as)")
            for name, then in self._session_properties():
                self._read_session(name, then)

    def _session_properties(self) -> tuple[tuple[str, Callable[[bool], None]], ...]:
        return (("LockedHint", self._set_locked), ("Active", self._set_active))

    def _read_session(self, name: str, then: Callable[[bool], None]) -> None:
        """`name` of the followed session, taken while that subscription lasts. One logind left
        unanswered is asked again RETRY later while it lasts: until a reading or a change
        comes, the lock or Active stays unknown. Any other failure leaves it so, as a logind
        without it does, and is said unless its connection closed or its logind went."""
        # GDBus numbers subscriptions from one counter for every connection, so an old
        # connection's never equals a new one's.
        sub = self._session_sub

        def got(reply: tuple) -> None:
            # From a logind that gave up its name with its connection open: its successor's
            # reading is the one that counts.
            if self._session_sub != sub:
                return
            self._loop.quiet(f"session {name}")
            then(bool(reply[0]))

        def failed(err: GLib.Error) -> None:
            # A logind that went away or was replaced is read again from its successor's
            # _login1_appeared: its NoReply comes after _login1_gone forgot the session.
            if self._session_sub != sub or self._loop.benign(err):
                return
            gio = self._loop.gio
            if err.matches(gio.io_error_quark(), gio.IOErrorEnum.TIMED_OUT):
                self._loop.say(f"session {name}", f"logind did not answer a read of the "
                               f"graphical session's {name} within {CALL_TIMEOUT:g} s; "
                               f"asking again in {RETRY:g} s")
                self._loop.timeout(RETRY, lambda: self._read_session(name, then)
                                   if self._session_sub == sub else None)
            else:
                self._loop.say(f"session {name}", f"cannot read the graphical session's {name} "
                               f"from logind ({said(err)}), so {UNREAD_SESSION[name]} until "
                               "logind says it changed")
        # The path lasts as long as the subscription this is read under: _got_display sets it
        # first, and _forget_session and _drop clear the two together.
        self._loop.call(self._system, LOGIN1, cast("str", self._session_path), PROPERTIES,
                        "Get", self._loop.glib.Variant("(ss)", (SESSION, name)), "(v)", got,
                        failed)

    def _session_changed(self, args: tuple) -> None:
        _interface, changed, invalidated = args
        for name, then in self._session_properties():
            if name in changed:
                then(bool(changed[name]))
            elif name in invalidated:
                self._read_session(name, then)

    def _set_locked(self, value: bool) -> None:
        if value != self._locked:
            self._locked = value
            self._on_locked(value)

    def _set_active(self, value: bool) -> None:
        if value != self._active:
            self._active = value
            self._on_active(value)

    def take_inhibitor(self) -> None:
        """Hold a logind delay inhibitor for sleep, now or once logind is
        there. Held until release_inhibitor(); one at a time."""
        self._want_inhibitor = True
        self._inhibit()

    def release_inhibitor(self) -> None:
        """Close the inhibitor's fd, which is what lets a waiting suspend go."""
        self._want_inhibitor = False
        fd, self._inhibitor_fd = self._inhibitor_fd, None
        if fd is not None:
            os.close(fd)
            log.debug("released the sleep delay inhibitor")

    def _inhibit(self) -> None:
        bus = self._system
        if (not self._want_inhibitor or self._inhibitor_fd is not None or self._inhibiting
                or bus.conn is None or bus.owners.get(LOGIN1) is None):
            return
        self._inhibiting = True
        owner = bus.owners.get(LOGIN1)

        def finish(source: Gio.DBusConnection, result: Gio.AsyncResult) -> None:
            # A call to a logind that went away or was replaced, or on a connection since
            # closed, is not current: _login1_gone or _drop reset the flag, and the next
            # logind may hold a call _login1_appeared made. Relies on the bus sending
            # NameOwnerChanged before the NoReply for the call it dropped (dbus-daemon 1.16.2).
            current = source is self._system.conn and self._system.owners.get(LOGIN1) == owner
            if current:
                self._inhibiting = False
            try:
                value, fds = source.call_with_unix_fd_list_finish(result)
            except self._loop.glib.Error as err:
                if not current or self._loop.benign(err):
                    return
                gio = self._loop.gio
                if err.matches(gio.io_error_quark(), gio.IOErrorEnum.TIMED_OUT):
                    # No answer is no refusal: unlike one, it is asked again from here.
                    # _inhibit's guards make the retry nothing once released or held.
                    self._loop.say("inhibit", f"logind did not answer the request for a sleep "
                                              f"delay inhibitor within {CALL_TIMEOUT:g} s, so a "
                                              f"suspend may not wait for the lights; asking "
                                              f"again in {RETRY:g} s")
                    self._loop.timeout(RETRY, self._inhibit)
                    return
                self._loop.say("inhibit", f"logind refused a sleep delay inhibitor, so a "
                                          f"suspend may not wait for the lights: {said(err)}")
                return
            stolen = fds.steal_fds() if fds is not None else []
            (index,) = value.unpack()
            keep = stolen[index] if current and 0 <= index < len(stolen) else None
            for fd in stolen:
                if fd != keep:
                    os.close(fd)
            if not current:
                return
            if keep is None:
                self._loop.say("inhibit", INHIBIT_NO_FD)
            elif not self._want_inhibitor or self._inhibitor_fd is not None:
                # Released while the call was on its way, or a logind that gave up its name
                # and took it back answered its first call first.
                os.close(keep)
            else:
                self._loop.quiet("inhibit")
                self._inhibitor_fd = keep
                log.debug("holding a sleep delay inhibitor")
        with self._loop.home():
            bus.conn.call_with_unix_fd_list(
                LOGIN1, LOGIN1_PATH, MANAGER, "Inhibit",
                self._loop.glib.Variant("(ssss)", ("sleep", APP_NAME, INHIBIT_WHY, "delay")),
                self._loop.glib.VariantType("(h)"), self._loop.gio.DBusCallFlags.NONE,
                round(CALL_TIMEOUT * 1000), None, bus.cancellable, self._loop.guard(finish))

    # ------------------------------------------------ GNOME, the session bus

    def _setup_session(self, bus: Bus) -> None:
        self._loop.subscribe(bus, SCREENSAVER, SCREENSAVER, "ActiveChanged", SCREENSAVER_PATH,
                             lambda args: self._set_blanked(args[0]), signature="(b)")
        self._loop.subscribe(bus, IDLE_MONITOR, IDLE_MONITOR, "WatchFired", IDLE_MONITOR_PATH,
                             lambda args: self._watch_fired(args[0]), signature="(u)")
        self._loop.watch(bus, SCREENSAVER, appeared=lambda _owner: self._loop.call(
            bus, SCREENSAVER, SCREENSAVER_PATH, SCREENSAVER, "GetActive", None, "(b)",
            lambda reply: self._set_blanked(reply[0])))
        self._loop.watch(bus, NOTIFICATIONS)
        self._loop.watch(bus, IDLE_MONITOR, appeared=self._idle_monitor_appeared)
        self._loop.subscribe(bus, SHELL, FOCUS, "FocusChanged", FOCUS_PATH,
                             self._focus_changed, signature="(u)")
        self._loop.subscribe(bus, SHELL, FOCUS, "Stopped", FOCUS_PATH,
                             lambda _args: self._set_focus(None))
        self._loop.subscribe(bus, SHELL, FOCUS, "Ready", FOCUS_PATH,
                             lambda _args: self._subscribe_focus(bus))
        self._loop.watch(bus, SHELL, appeared=lambda _owner: self._shell_appeared(bus),
                         vanished=lambda: self._set_focus(None))
        self._tray_export.connected()

    def _shell_appeared(self, bus: Bus) -> None:
        self._subscribe_focus(bus)
        self._tray_export.shell_appeared()

    def _subscribe_focus(self, bus: Bus) -> None:
        """Ask the extension for the focus and its changes. A Shell without it answers
        UnknownMethod, and one that takes this process for another program, AccessDenied:
        focus then stays with Xwayland."""
        signals = self._focus_signals

        def got(reply: tuple) -> None:
            if self._focus_signals == signals:
                self._set_focus(reply[0])

        def refused(err: GLib.Error) -> None:
            if self._loop.benign(err):
                return
            if self._loop.gio.DBusError.get_remote_error(err) == ACCESS_DENIED:
                self._loop.say("focus refused", "Gearbench's GNOME Shell extension refused "
                               f"this daemon: it answers the {SERVICE}.service unit alone, so a "
                               "daemon run by hand follows focus on Xwayland", logging.INFO)
            else:
                log.debug("Subscribe to Gearbench's GNOME Shell extension failed: %s",
                          said(err))
        self._loop.call(bus, SHELL, FOCUS_PATH, FOCUS, "Subscribe", None, "(u)", got, refused)

    def _focus_changed(self, args: tuple) -> None:
        self._focus_signals += 1
        self._set_focus(args[0])

    def _set_focus(self, pid: int | None) -> None:
        if pid != self._focus:
            self._focus = pid
            self._on_focus(pid)

    def _set_blanked(self, value: bool) -> None:
        if value != self._blanked:
            self._blanked = value
            self._on_blanked(value)

    def notify(self, summary: str, body: str, icon: str, replaces_id: int = 0,
               done: Callable[[int | None], None] | None = None,
               transient: bool = False) -> bool:
        """Post a notice; `done` gets its id, or None when the call failed. False, and `done`
        is not called, without a session bus.

        `replaces_id` is a previous notice's id: while the server still holds it, this one
        updates its banner in place and restarts its time; once it has gone, the server hands
        out a new id. The body is escaped, as the server reads markup; `icon` is an icon name
        for the image-path hint. With `transient`, GNOME Shell 50.1 (notificationDaemon.js,
        messageTray.js) drops the notice from its list when the banner goes. The urgency
        stays normal: GNOME shows no banner for a low one, only the list entry.
        """
        if not self.available:
            return False
        hints = {"desktop-entry": self._loop.glib.Variant("s", DESKTOP_ENTRY),
                 "urgency": self._loop.glib.Variant("y", URGENCY_NORMAL),
                 "category": self._loop.glib.Variant("s", "device"),
                 "image-path": self._loop.glib.Variant("s", icon)}
        if transient:
            hints["transient"] = self._loop.glib.Variant("b", True)
        args = self._loop.glib.Variant("(susssasa{sv}i)", (
            APP_NAME, replaces_id, DESKTOP_ENTRY, utf8(summary),
            self._loop.glib.markup_escape_text(utf8(body), -1), [], hints, -1))

        def sent(reply: tuple) -> None:
            self._loop.quiet("notify")
            if done is not None:
                done(reply[0])

        def refused(err: GLib.Error) -> None:
            if not self._loop.benign(err):
                self._loop.say("notify", f"the notification server did not take a notice: "
                                         f"{said(err)}")
            if done is not None:
                done(None)
        return self._loop.call(self._session, NOTIFICATIONS, NOTIFICATIONS_PATH, NOTIFICATIONS,
                               "Notify", args, "(u)", sent, refused)

    def close_notification(self, notice: int) -> None:
        """Take a notice down."""
        if self.available:
            self._loop.call(self._session, NOTIFICATIONS, NOTIFICATIONS_PATH, NOTIFICATIONS,
                            "CloseNotification", self._loop.glib.Variant("(u)", (notice,)),
                            None)

    # IdleMonitor calls go to its unique name: a watch id belongs to one owner, and an old
    # owner's id sent to its successor could remove one of the successor's own watches.

    def remove_watches(self, group: str) -> None:
        """Remove `group`'s watch, and forget that it asked for one. Another group's stays."""
        self._wanted.discard(group)
        self._adding.discard(group)
        self._watch_epochs[group] = self._watch_epochs.get(group, 0) + 1
        owner = self._session.owners.get(IDLE_MONITOR)
        for watch_id, watcher in list(self._watches.items()):
            if watcher != group:
                continue
            if owner is not None:
                self._loop.call(self._session, owner, IDLE_MONITOR_PATH, IDLE_MONITOR,
                                "RemoveWatch", self._loop.glib.Variant("(u)", (watch_id,)),
                                None)
            del self._watches[watch_id]

    def idle_time(self, done: Callable[[int | None], None]) -> bool:
        """Ask Mutter's IdleMonitor for the idle time; `done` gets it in ms, or None when the
        call failed. False, and `done` is not called, with no IdleMonitor to ask.

        Mutter 50.1 (meta-idle-monitor.c) answers `now - last_event_time`, and sets
        last_event_time at every input whatever inhibits idleness: an idle inhibitor holds back
        only its idle watches' ready time, and its end sets last_event_time to now.
        """
        owner = self._session.owners.get(IDLE_MONITOR)
        if owner is None:
            return False

        def refused(err: GLib.Error) -> None:
            if not self._loop.benign(err):
                log.debug(IDLE_TIME_UNANSWERED, said(err))
            done(None)
        return self._loop.call(self._session, owner, IDLE_MONITOR_PATH, IDLE_MONITOR,
                               "GetIdletime", None, "(t)", lambda reply: done(reply[0]),
                               refused)

    def add_user_active_watch(self, group: str) -> None:
        """Call user_active(group) once, on the next input; one watch per group."""
        self._wanted.add(group)
        owner = self._session.owners.get(IDLE_MONITOR)
        if owner is None or group in self._adding or group in self._watches.values():
            return
        self._adding.add(group)
        epoch = self._watch_epochs.get(group, 0)

        def added(reply: tuple) -> None:
            (watch_id,) = reply
            if self._session.owners.get(IDLE_MONITOR) != owner:
                return          # that owner is gone, and its watches with it
            if epoch != self._watch_epochs.get(group, 0) or group not in self._wanted:
                self._loop.call(self._session, owner, IDLE_MONITOR_PATH, IDLE_MONITOR,
                                "RemoveWatch", self._loop.glib.Variant("(u)", (watch_id,)),
                                None)
                return
            self._adding.discard(group)
            self._watches[watch_id] = group
            self._loop.quiet("watch")

        def refused(err: GLib.Error) -> None:
            if (self._session.owners.get(IDLE_MONITOR) != owner
                    or epoch != self._watch_epochs.get(group, 0)):
                return          # that owner is gone, or the group's watch was removed since
            self._adding.discard(group)
            if not self._loop.benign(err):
                self._loop.say("watch", f"Mutter's IdleMonitor did not add a watch, so input "
                                        f"may not bring the lights back: {said(err)}; asking "
                                        f"again in {RETRY:g} s")
                # The daemon may not ask for this group's watch again: a synced
                # mouse's is asked for once, as the desktop goes idle.
                self._loop.timeout(RETRY, lambda: self.add_user_active_watch(group)
                                   if group in self._wanted
                                   and epoch == self._watch_epochs.get(group, 0) else None)
        self._loop.call(self._session, owner, IDLE_MONITOR_PATH, IDLE_MONITOR,
                        "AddUserActiveWatch", None, "(u)", added, refused)

    def _idle_monitor_appeared(self, _owner: str) -> None:
        # A new owner: whatever the last one held went with it.
        self._watches.clear()
        self._adding.clear()
        for group in sorted(self._wanted):
            self.add_user_active_watch(group)

    def _watch_fired(self, watch_id: int) -> None:
        # The IdleMonitor removes a user-active watch once it fires.
        group = self._watches.pop(watch_id, None)
        if group is None:
            return
        self._wanted.discard(group)
        self._on_user_active(group)

    # ----------------------------------------------------------- the tray icon

    def show_tray(self, menu: tray_model.TrayMenu) -> None:
        self._tray_export.show(menu)

    def hide_tray(self) -> None:
        self._tray_export.hide()

    def refresh_tray(self) -> None:
        self._tray_export.refresh()

    def tray_state(self) -> tuple[bool, str | None]:
        return self._tray_export.state()

    def open_app(self) -> bool:
        """Open the window: Activate on its app id, which D-Bus starts it for
        when it is not running. The daemon is sandboxed, and a window it ran
        itself would be its child. False without a session bus."""
        if not self.available:
            return False
        token = self._tray_export.take_token()
        platform = ({key: self._loop.glib.Variant("s", token)
                     for key in ("activation-token", "desktop-startup-id")} if token else {})

        def failed(err: GLib.Error) -> None:
            if not self._loop.benign(err):
                self._loop.say("open", f"could not open the window: {said(err)}; D-Bus starts "
                                       f"it from {DESKTOP_ENTRY}.service")
        return self._loop.call(self._session, DESKTOP_ENTRY, APP_PATH, APPLICATION, "Activate",
                               self._loop.glib.Variant("(a{sv})", (platform,)), None,
                               lambda _reply: self._loop.quiet("open"), failed)
