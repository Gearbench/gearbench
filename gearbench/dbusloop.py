"""The D-Bus plumbing under desktop.py and sni.py: a private GLib.MainContext that the
daemon's select loop drives (`Loop`), the private connections to the message buses (`Bus`),
and the calls, signal subscriptions and name watches made on them.

With desktop.session_class, the only daemon code that imports gi, and only lazily:
python3-gi is a Recommends, and without it the desktop's features are off. Hard rules:

- No thread or main loop of its own: the daemon's select loop drives the context through
  `fileno()`, `dispatch()` and `deadline()`. It is made with OWNERLESS_POLLING: GDBus wakes
  a plain context only while a thread has it acquired, and select slept through every
  signal (measured 2026-09-23).
- Every call is asynchronous, since a `*_sync` call can stall the loop for 25 s, and starts
  under `home()`: GDBus replies to the context that was the thread's default at the call.
  desktop.session_class alone blocks, before the loop exists, with a timer thread that
  cancels its connect.
- Every connection is private: GLib's shared session bus exits the process when it
  closes, and nothing else in the daemon may find this context as its default.
- A callback that raises stops `dispatch()`, and the exception comes out of it; another that
  raises in the same dispatch is logged.
"""

from __future__ import annotations

import contextlib
import logging
import os
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, cast, overload

from .runtime_folder import runtime_home
from .wording import escaped

if TYPE_CHECKING:
    from gi.repository import Gio, GLib

log = logging.getLogger("gearbench")

ACCESS_DENIED = "org.freedesktop.DBus.Error.AccessDenied"
SYSTEM_BUS = "unix:path=/run/dbus/system_bus_socket"

# Seconds a call waits for its reply. GDBus times it with a source on the context, which
# deadline() reports, never a wait on the loop.
CALL_TIMEOUT = 10.0
# Seconds before a failed connect or call is tried again. One that backs off doubles its
# wait, up to RETRY_MAX, while it keeps failing. A lost session bus is usually the session
# ending, so asking sooner gains nothing.
RETRY = 10.0
RETRY_MAX = 120.0

#: The most of a peer's error message `said` keeps.
SAID_MAX = 500


def bus_addresses(environ: Mapping[str, str] = os.environ) -> tuple[str, str | None]:
    """The system and session bus addresses as GLib finds them, less its X11 autolaunch,
    which runs dbus-launch and waits for it, with a relative XDG_RUNTIME_DIR ignored
    (runtime_home) and each byte past ASCII escaped (ascii_address). The session's is None
    when none is found."""
    system = ascii_address(environ.get("DBUS_SYSTEM_BUS_ADDRESS") or SYSTEM_BUS)
    session = ascii_address(environ.get("DBUS_SESSION_BUS_ADDRESS", "")) or None
    runtime = runtime_home(environ)
    if session is None and runtime is not None and (runtime / "bus").exists():
        session = f"unix:path={escape_address(str(runtime / 'bus'))}"
    return system, session


def ascii_address(address: str) -> str:
    """`address` with each byte past ASCII as %XX, which the specification asks of every byte
    it does not list: os.environ holds a byte that is not UTF-8 as a lone surrogate, which
    PyGObject raises at rather than pass to GDBus."""
    return "".join(chr(b) if b < 0x80 else f"%{b:02x}" for b in os.fsencode(address))


@overload
def utf8(value: str) -> str: ...
@overload
def utf8(value: object) -> object: ...
def utf8(value: object) -> object:
    """`value` with every str made encodable as UTF-8, `?` for what is not. A lone surrogate,
    as in a file name read with surrogateescape, cannot go into a D-Bus string: PyGObject
    raises out of dispatch(), which stops the daemon."""
    if isinstance(value, str):
        return value.encode("utf-8", "replace").decode("utf-8")
    if isinstance(value, tuple):
        return tuple(utf8(item) for item in value)
    if isinstance(value, list):
        return [utf8(item) for item in value]
    return value


def said(err: GLib.Error) -> str:
    """A GLib.Error's message without GDBus's `GDBus.Error:org.example.Error: ` prefix,
    escaped and cut at SAID_MAX characters: the peer that answered wrote it, the tray's
    watcher can be a sandboxed app (sni.py), and its words go to `status` and the journal,
    which takes each line as a record of its own (cli.service.JournalFormatter)."""
    # An error name is at most 255 bytes, so the prefix fits in the extra 300.
    message = err.message[:SAID_MAX + 300]
    if message.startswith("GDBus.Error:") and ": " in message:
        message = message.split(": ", 1)[1]
    return escaped(message)[:SAID_MAX]


def trying_again() -> str:
    """How the journal line of a failure that Loop.backoff tries again ends: RETRY apart at
    first, doubling to RETRY_MAX."""
    return f"trying again, {RETRY:g} s apart doubling to {RETRY_MAX:g} s"


def escape_address(value: str) -> str:
    """A value escaped for a D-Bus address: every byte but [-0-9A-Za-z_/.\\*]
    as %XX, as the specification's "Server Addresses" section says."""
    safe = set(b"-_/.\\*0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
    return "".join(chr(b) if b in safe else f"%{b:02x}" for b in os.fsencode(value))


class Retried(Protocol):
    """What `Loop.backoff` times: the run still waiting, and the wait before the next."""

    retry: GLib.Source | None
    backoff: float


@dataclass
class Bus:
    """One connection to a bus, and what hangs off it: the system bus's, the session bus's
    or the tray item's own."""

    name: str
    address: str | None
    conn: Gio.DBusConnection | None = None
    #: Cancels the connect and every call made with it, at halt().
    cancellable: Gio.Cancellable | None = None
    #: The next connect's wait; a factory, so a test can shorten RETRY.
    backoff: float = field(default_factory=lambda: RETRY)
    #: The GLib timeout source of a pending reconnect.
    retry: GLib.Source | None = None
    closed_handler: int = 0
    subscriptions: list[int] = field(default_factory=list)
    watchers: list[int] = field(default_factory=list)
    #: Well-known name -> its unique owner; absent or None while it has none.
    owners: dict[str, str | None] = field(default_factory=dict)
    #: Whether a connect failed or the connection closed since the last success, so the
    #: next success is said.
    failed: bool = False

    def halt(self) -> None:
        """Drop the reconnect still waiting, and cancel the connect on its way and every call
        made with it."""
        if self.retry is not None:
            self.retry.destroy()
            self.retry = None
        if self.cancellable is not None:
            self.cancellable.cancel()
            self.cancellable = None


class Loop:
    """The private context and the bus work done on it: connects, calls, signal subscriptions
    and name watches, each callback guarded so that the first it raises comes out of
    `dispatch()`; and the journal lines said once a topic.

    Build it, then `start()`. Until a successful start `fileno()` is -1, `dispatch()` does
    nothing and `deadline()` is None.
    """

    def __init__(self) -> None:
        #: True once gi was imported and the context made.
        self.available = False
        if TYPE_CHECKING:
            # mypy reads gi's modules and the context as start() leaves them. Nothing uses one
            # before that: each use is behind `available` or follows from a connection, and
            # none is opened before start().
            self.gio, self.glib = Gio, GLib
            self._ctx: GLib.MainContext
        else:
            self.gio = self.glib = None
            self._ctx = None
        self._fd = -1
        #: The first exception a callback raised, for dispatch() to raise.
        self._raised: BaseException | None = None
        #: The last message said for each topic, so a repeat goes to debug.
        self._said: dict[str, str] = {}

    def start(self) -> ImportError | ValueError | None:
        """Import gi and make the context: None once made, else what the import raised, for
        python3-gi or its Gio typelib missing."""
        try:
            import gi  # noqa: PLC0415 -- optional: the daemon runs without python3-gi
            gi.require_version("Gio", "2.0")
            gi.require_version("GLib", "2.0")
            from gi.repository import Gio, GLib  # noqa: PLC0415 -- optional, as gi above
        except (ImportError, ValueError) as exc:
            return exc
        self.gio, self.glib = Gio, GLib
        self._ctx = GLib.MainContext.new_with_flags(GLib.MainContextFlags.OWNERLESS_POLLING)
        _ready, priority = self._ctx.prepare()
        _timeout, fds = self._ctx.query(priority)
        # Only the wakeup eventfd: GDBus polls its sockets on its own thread.
        self._fd = fds[0].fd
        self.available = True
        return None

    def fileno(self) -> int:
        """The context's wakeup fd: readable when a reply or signal waits."""
        return self._fd

    def dispatch(self) -> None:
        """Run the replies, signals and timeouts the context has waiting, until a callback
        raises: that exception comes out here."""
        if not self.available:
            return
        with self.home():
            while self._ctx.iteration(False) and self._raised is None:
                pass
        if self._raised is not None:
            raised, self._raised = self._raised, None
            raise raised

    def deadline(self) -> float | None:
        """When the context's next timeout falls due, on time.monotonic()'s
        clock, which is GLib's too; None when nothing is timed."""
        if not self.available:
            return None
        # A ready source makes query's timeout 0: GLib's own iteration polls with it.
        _ready, priority = self._ctx.prepare()
        timeout, _fds = self._ctx.query(priority)
        return None if timeout < 0 else time.monotonic() + timeout / 1000

    @contextlib.contextmanager
    def home(self) -> Iterator[None]:
        """This context as the thread's default, so a reply to a call made
        inside comes back here."""
        self._ctx.push_thread_default()
        try:
            yield
        finally:
            self._ctx.pop_thread_default()

    def guard[R](self, fn: Callable[..., R]) -> Callable[..., R]:
        """`fn` as a GLib callback: an exception is kept for dispatch() to
        raise, rather than printed by PyGObject and dropped. GLib can run another callback
        before dispatch() raises: what that one raises is logged."""
        def guarded(*args: object) -> R:
            try:
                return fn(*args)
            except Exception as exc:
                if self._raised is None:
                    self._raised = exc
                else:
                    log.error("another D-Bus callback failed in the same dispatch: %r", exc,
                              exc_info=exc)
                # False stands in for what `fn` did not return: it ends Loop.timeout's source.
                return cast("R", False)
        return guarded

    def say(self, topic: str, message: str, level: int = logging.WARNING) -> None:
        """`message` at `level` the first time `topic` says it, at debug
        while it keeps saying the same. `quiet(topic)` resets it."""
        if self._said.get(topic) == message:
            level = logging.DEBUG
        self._said[topic] = message
        log.log(level, "%s", message)

    def quiet(self, topic: str) -> None:
        self._said.pop(topic, None)

    def benign(self, err: GLib.Error) -> bool:
        """A call that failed because halt() canceled it or its connection closed under it,
        which a `closed` handler says."""
        return any(err.matches(self.gio.io_error_quark(), code)
                   for code in (self.gio.IOErrorEnum.CANCELLED, self.gio.IOErrorEnum.CLOSED))

    def timeout(self, seconds: float, fn: Callable[[], None]) -> GLib.Source:
        """Run `fn` once after `seconds`, from dispatch()."""
        source = self.glib.timeout_source_new(round(seconds * 1000))

        def fire() -> bool:
            fn()
            return False
        source.set_callback(self.guard(fire))
        source.attach(self._ctx)
        return source

    def open(self, address: str, cancellable: Gio.Cancellable,
             done: Callable[[object, Gio.AsyncResult], None]) -> None:
        """Start a private connection to the message bus at `address`: authenticate, then
        Hello. `done(source, result)` runs from dispatch()."""
        flags = (self.gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
                 | self.gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION)
        with self.home():
            self.gio.DBusConnection.new_for_address(address, flags, None, cancellable,
                                                    self.guard(done))

    def connect(self, bus: Bus, ready: Callable[[Gio.DBusConnection], None],
                failed: Callable[[str], None],
                closed: Callable[[GLib.Error | None], None]) -> None:
        """Open `bus`'s connection at its address (`open`). `ready(conn)` runs once it is up,
        with `bus.conn` set, its backoff back at RETRY and a `closed` handler that calls
        `closed(error)`; `failed(why)` runs for a connect that failed or hung up right after
        Hello. Neither runs for a connect halt() canceled."""
        bus.retry = None
        cancellable = bus.cancellable = self.gio.Cancellable()

        def done(_source: object, result: Gio.AsyncResult) -> None:
            conn: Gio.DBusConnection
            try:
                conn = self.gio.DBusConnection.new_for_address_finish(result)
            except self.glib.Error as err:
                # A bus that hangs up during Hello fails here with CLOSED: a failed connect,
                # since no "closed" handler exists yet to try it again.
                why: str | None = said(err)
            else:
                # GDBus emits "closed" from an idle it may queue ahead of this callback, before
                # a handler can exist (GLib 2.88). Closed here, the bus hung up right after
                # Hello: a failed connect, tried further apart. A later close comes through the
                # handler below, since its idle cannot run before this returns.
                why = "it hung up after Hello" if conn.is_closed() else None
            if bus.cancellable is not cancellable:
                # halt() canceled it while it connected, and a canceled connect's finish raises
                # IOErrorEnum.CANCELLED, even one that connected first (GTask's
                # check-cancellable), so there is no connection here.
                return
            if why is not None:
                failed(why)
                return
            bus.conn, bus.backoff = conn, RETRY
            bus.closed_handler = conn.connect("closed", self.guard(
                lambda _closing, _vanished, error: closed(error)))
            ready(conn)
        # The caller gives the bus its address before its first connect.
        self.open(cast("str", bus.address), cancellable, done)

    def drop(self, bus: Bus) -> Gio.DBusConnection | None:
        """Forget `bus`'s connection and what hung off it: its `closed` handler, its signal
        subscriptions, its name watches and the owners they found. The connection, still
        open, or None without one."""
        conn, bus.conn = bus.conn, None
        if conn is not None:
            conn.disconnect(bus.closed_handler)
            for subscription in bus.subscriptions:
                conn.signal_unsubscribe(subscription)
        bus.subscriptions.clear()
        for watcher in bus.watchers:
            self.gio.bus_unwatch_name(watcher)
        bus.watchers.clear()
        bus.owners.clear()
        return conn

    def close(self, conn: Gio.DBusConnection) -> None:
        """Close `conn` without waiting: close_sync flushes and waits for GDBus's worker.
        Under home(), since GDBus ends the close from an idle on the thread's default context,
        which outside dispatch() is the process's own, one the daemon never runs."""
        with self.home():
            conn.close(None, None)

    def backoff(self, holder: Retried, fn: Callable[[], None]) -> None:
        """Run `fn` after `holder.backoff`, in place of a run still waiting, and double that,
        to RETRY_MAX, for next time."""
        if holder.retry is not None:
            holder.retry.destroy()
        wait, holder.backoff = holder.backoff, min(holder.backoff * 2, RETRY_MAX)
        holder.retry = self.timeout(wait, fn)

    def subscribe(self, bus: Bus, sender: str, interface: str, member: str, path: str,
                  handler: Callable[[tuple], None], arg0: str | None = None,
                  signature: str | None = None) -> int:
        """Subscribe to one signal from the owner of `sender`; `handler` gets its arguments.
        GDBus checks the sender against the name's current owner, so another client cannot
        send these, though anything in the Shell's process, another extension among them,
        sends as the Shell. With `signature`, one of another type is dropped and said, at debug
        when it is the type dropped last: its handler would raise out of dispatch(), which
        stops the daemon. A handler that reads its arguments needs `signature`."""
        def received(_conn: object, _sender: str, _path: str, _iface: str, _member: str,
                     parameters: GLib.Variant) -> None:
            kind = parameters.get_type_string()
            if signature is not None and kind != signature:
                self.say(f"signature {interface}.{member}",
                         f"{interface}.{member} from {sender} came as {kind}, not "
                         f"{signature}; dropped")
                return
            handler(parameters.unpack())
        with self.home():
            # On a live connection: a connect sets it before the setup that subscribes, and
            # `call` runs `then` only while its connection is current.
            subscription = cast("Gio.DBusConnection", bus.conn).signal_subscribe(
                sender, interface, member, path, arg0, self.gio.DBusSignalFlags.NONE,
                self.guard(received))
        bus.subscriptions.append(subscription)
        return subscription

    def watch(self, bus: Bus, name: str, appeared: Callable[[str], object] | None = None,
              vanished: Callable[[], None] | None = None) -> None:
        """Follow who owns `name`, in bus.owners, and call back on a change."""
        def on_appeared(_conn: object, _name: str, owner: str) -> None:
            bus.owners[name] = owner
            if appeared is not None:
                appeared(owner)

        def on_vanished(_conn: object, _name: str) -> None:
            had = bus.owners.get(name)
            bus.owners[name] = None
            if had is not None and vanished is not None:
                vanished()
        with self.home():
            # As in `subscribe`: a connect sets the connection before the setup that watches.
            watcher = self.gio.bus_watch_name_on_connection(
                cast("Gio.DBusConnection", bus.conn), name, self.gio.BusNameWatcherFlags.NONE,
                self.guard(on_appeared), self.guard(on_vanished))
        bus.watchers.append(watcher)

    def call(self, bus: Bus, destination: str, path: str, interface: str, method: str,
             args: GLib.Variant | None, reply: str | None,
             then: Callable[[tuple], None] | None = None,
             failed: Callable[[GLib.Error], None] | None = None) -> bool:
        """Call a method; `then` gets the unpacked reply and `failed` every GLib.Error, benign
        ones too. False, and neither is called, without a connection.

        A reply is always dispatched before its connection's `closed`, and one still waiting at
        close() fails as canceled (GLib 2.88, measured 2026-10-13), so `then` runs only while its
        connection is current."""
        conn = bus.conn
        if conn is None:
            return False

        def finish(source: Gio.DBusConnection, result: Gio.AsyncResult) -> None:
            try:
                value = source.call_finish(result)
            except self.glib.Error as err:
                if failed is not None:
                    failed(err)
                elif not self.benign(err):
                    log.debug("%s.%s on the %s bus failed: %s", interface, method,
                              bus.name, said(err))
                return
            if then is not None:
                then(value.unpack())
        with self.home():
            conn.call(destination, path, interface, method, args,
                      self.glib.VariantType(reply) if reply else None,
                      self.gio.DBusCallFlags.NONE, round(CALL_TIMEOUT * 1000),
                      bus.cancellable, self.guard(finish))
        return True
