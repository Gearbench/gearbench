"""The worker thread, the loading guard, the settle timer, and the helpers that read a
round trip's reply.

`DaemonWorker` is the one thread that talks to the daemon and does the window's
file reads. `LoadingGuard` keeps a row the program sets from being read back as
the user's change, and sets a row's value only when that changes it. `Settle`
sends once after a run of changes stops. `daemon_down` and `unreachable` read a
failed round trip through the daemon client's `failure`, as the CLI does, and
`preview_reply` a preview's reply, the same way for every tab and dialog.
`daemon_listening` says whether a daemon accepts on the socket. Nothing here builds a
widget, so all of it is testable without a display.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Never, Protocol, Self

from gi.repository import GLib

from ..daemon import BUSY, DOWN, UNREAD, failure, listening, send_command, socket_path
from ..wording import reason

if TYPE_CHECKING:                       # pragma: no cover
    from gi.repository import Adw, Gio, Gtk

#: A call's callback gets the daemon's reply or what send_command raised: an OSError
#: (ConnectionError among them), or ValueError for a reply that does not parse.
Reply = dict | Exception
ReplyCallback = Callable[[Reply], None]
#: A `run` callback gets what the function returned, or the exception it raised.
Result = object | Exception
ResultCallback = Callable[[Result], None]
#: A callback taking the daemon's reply, or None once the failure was shown.
Done = Callable[[dict | None], None]


class Call(Protocol):
    """The window's `call`: `unanswered(error)`, when given, runs instead of
    `callback(None)` for a round trip that raised. `timeout` is the worker's, longer for
    a request that can wait out the mouse's own timeouts."""

    def __call__(self, request: dict, callback: Done | None = None,
                 unanswered: Callable[[Exception], None] | None = None, /, *,
                 timeout: float = 3.0) -> None: ...


class SetStatus(Protocol):
    """The window's `set_status`. `poll` True for a read, which carries no change: one
    that finds no daemon, or a socket it cannot use, does not keep a closing window up."""

    def __call__(self, reachable: bool, error: Exception | None = None, /, *,
                 poll: bool = False) -> None: ...


#: What `unreachable` says for UNREAD, BUSY and DOWN.
NOT_READ = "the daemon's reply could not be read"
NOT_IN_TIME = "the daemon did not answer in time; it may be busy"
NOT_RUNNING = "the daemon is not running"


def daemon_down(error: BaseException | None) -> bool:
    """Whether a failed round trip means no daemon is running: `failure`'s DOWN."""
    return failure(error) == DOWN


def daemon_listening() -> bool:
    """Whether a daemon accepts on the socket; a file left by a killed daemon refuses."""
    return listening(socket_path())


def unreachable(error: BaseException) -> str:
    """A failed round trip in words for a device's status row or a dialog's error line.

    The socket's own error ("timed out") reads as the device's fault and gives the user
    nothing to act on; it is quoted only for a socket that could not be used, where it
    says what to fix.
    """
    kind = failure(error)
    if kind == UNREAD:
        return NOT_READ
    if kind == BUSY:
        return NOT_IN_TIME
    if kind == DOWN:
        return NOT_RUNNING
    return f"the daemon could not be reached at {socket_path()}: {reason(error)}"


def preview_reply(reply: Reply, last: str | None,
                  set_status: Callable[[bool, Exception | None], None],
                  refused: Callable[[dict], object]) -> str | None:
    """A preview's reply: fire and forget, but a refusal must still show. Returns the
    refusal now standing, `last` the one before. Shown once per distinct message: a drag
    sends previews at its own rate and a refusal holds for the whole drag."""
    if isinstance(reply, Exception):
        set_status(False, reply)
        return last
    if reply.get("ok"):
        return None
    message = str(reply.get("error", "the preview was refused"))
    if message != last:
        refused({**reply, "error": message})
    return message


@dataclass
class Job:
    """A daemon request, or with `work` set a function to run, for the worker thread.
    `request` stays writable so a queued preview can be replaced in place."""

    request: dict
    #: Takes the reply, or with `work` what it returned or raised. `call`, `preview` and
    #: `run` each check their callback against what it gets; the two kinds share the queue,
    #: so the type says nothing of the argument.
    callback: Callable[[Never], None] | None
    timeout: float
    work: Callable[[], object] | None = None


class LoadingGuard:
    """Tells the row handlers a row is being set by the program, not by the user.

    A depth counter, released on the next idle since GTK delivers some notify::selected
    signals after the code that set the row returns. Several releases can be pending at
    once, and a flag cleared by the first would let a later block's rows write to the device.
    """

    def __init__(self) -> None:
        self._depth = 0

    @property
    def active(self) -> bool:
        return self._depth > 0

    def __enter__(self) -> Self:
        self._depth += 1
        return self

    def __exit__(self, *_exc: object) -> None:
        GLib.idle_add(self._release)

    def _release(self) -> bool:
        self._depth = max(0, self._depth - 1)
        return GLib.SOURCE_REMOVE

    # The guard only for a change: every poll comes through these, and a click landing
    # while it is held is dropped.
    def put_active(self, row: Adw.SwitchRow, value: bool) -> None:
        if row.get_active() != value:
            with self:
                row.set_active(value)

    def put_selected(self, row: Adw.ComboRow, index: int) -> None:
        if row.get_selected() != index:
            with self:
                row.set_selected(index)

    def put_value(self, widget: Adw.SpinRow | Gtk.Scale, value: float) -> None:
        if widget.get_value() != value:
            with self:
                widget.set_value(value)

    def put_model(self, row: Adw.ComboRow, model: Gio.ListModel) -> None:
        """A new list for a dropdown, always held: the selection it resets notifies."""
        with self:
            row.set_model(model)


class Settle:
    """One send that waits until changes stop for a while: a drag, a run of arrow keys
    or of clicks on + sends once. The wait is a GLib timeout, so `send` runs on the
    main loop."""

    def __init__(self, send: Callable[[], None]) -> None:
        self._send = send
        #: The timeout's GLib source id while a send waits.
        self._source: int | None = None

    @property
    def pending(self) -> bool:
        """A send is waiting for its time."""
        return self._source is not None

    def restart(self, ms: int) -> None:
        """Send `ms` from now, in place of a send still waiting."""
        self.cancel()
        self._source = GLib.timeout_add(ms, self._fire)

    def cancel(self) -> None:
        """Drop the send that is waiting, if one is."""
        if self._source is not None:
            GLib.source_remove(self._source)
            self._source = None

    def flush(self) -> bool:
        """Send now what is waiting. True when something was."""
        if self._source is None:
            return False
        self.cancel()
        self._send()
        return True

    def _fire(self) -> bool:
        # Cleared first: GLib drops the source at SOURCE_REMOVE, and `send` may ask
        # whether one is pending.
        self._source = None
        self._send()
        return GLib.SOURCE_REMOVE


class DaemonWorker:
    """One thread that talks to the daemon, so the GTK main loop never waits.

    A preview's round trip is 36 ms, and the daemon can stall over a second repainting
    after a 0x8071 notification, which on the main thread froze the color picker.

    One FIFO queue; each reply reaches its callback on the main thread through
    GLib.idle_add, so callbacks may touch widgets. An unsent preview last in the queue
    is replaced in its queue place by the next one of the same command, so a drag builds
    no backlog and a commit queued after a preview is sent after it.
    """

    def __init__(self, send: Callable[..., dict] = send_command) -> None:
        self._send = send
        self._queue: queue.SimpleQueue[Job | None] = queue.SimpleQueue()
        self._lock = threading.Lock()
        #: The queued preview a later one of the same command may still replace: the
        #: queue's last job, or None once the worker takes it or a call or run queues
        #: behind it, so no preview jumps ahead of that.
        self._open_preview: Job | None = None
        self._stopped = False
        self._thread = threading.Thread(target=self._run, name="gearbench-daemon", daemon=True)
        self._thread.start()

    def call(self, request: dict, callback: ReplyCallback | None = None,
             timeout: float = 3.0) -> None:
        if self._stopped:
            return
        with self._lock:
            self._open_preview = None
            self._queue.put(Job(request, callback, timeout))

    def preview(self, request: dict, callback: ReplyCallback | None = None) -> None:
        if self._stopped:
            return
        with self._lock:
            if (self._open_preview is not None
                    and self._open_preview.request.get("command") == request.get("command")):
                self._open_preview.request = request
                self._open_preview.callback = callback
                return
            self._open_preview = Job(request, callback, 3.0)
            self._queue.put(self._open_preview)

    def run[T](self, work: Callable[[], T],
               callback: Callable[[T | Exception], None] | None = None) -> None:
        """Run `work` on the worker thread and hand the result to `callback`."""
        if self._stopped:
            return
        with self._lock:
            self._open_preview = None
            self._queue.put(Job({}, callback, 0.0, work))

    def stop(self) -> None:
        """Drop replies from here on and let the thread exit after its current call."""
        self._stopped = True
        self._queue.put(None)

    def _run(self) -> None:
        while True:
            job = self._queue.get()
            if job is None or self._stopped:
                return
            with self._lock:
                if job is self._open_preview:
                    self._open_preview = None
                request, callback, timeout = job.request, job.callback, job.timeout
                work = job.work
            if work is not None:
                try:
                    reply: Reply | Result = work()
                except Exception as exc:  # noqa: BLE001 -- the thread must outlive one bad file
                    reply = exc
            else:
                try:
                    reply = self._send(request, timeout=timeout)
                except (OSError, ValueError) as exc:
                    # ValueError: a reply cut short mid-send does not parse, and anything
                    # raised here ends the thread, after which nothing is answered.
                    reply = exc
            if callback is not None:
                GLib.idle_add(self._deliver, callback, reply)

    def _deliver[T](self, callback: Callable[[T], None], reply: T) -> bool:
        if not self._stopped:
            callback(reply)
        return GLib.SOURCE_REMOVE
