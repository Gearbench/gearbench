"""The worker thread, the loading guard, the settle timer, and the helpers that read a
round trip's reply.

`DaemonWorker` is the one thread that talks to the daemon and does the window's
file reads. `LoadingGuard` keeps a row the program sets from being read back as
the user's change, and sets a row's value only when that changes it. `Settle`
sends once after a run of changes stops, and `Pace` sends during one, at most
once an interval; SETTLE_MS and LIVE_MS are the A50 X's page's and the volume
rows' waits. `take_typed` takes a number typed in a spin row that the cursor
may still be in. `named_layer` is the `layer` a request names. `daemon_down` and `unreachable` read a
failed round trip through the daemon client's `failure`, as the CLI does, and
`preview_reply` a preview's reply, the same way for every tab and dialog.
`daemon_listening` says whether a daemon accepts on the socket, and `unusable_socket`
names one that could not be used, and why. Nothing here builds a widget, so all of it is
testable without a display.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Never, Protocol, Self, cast

from gi.repository import GLib

from ..daemon import (
    BUSY,
    DOWN,
    NOT_REACHED,
    NOT_RUNNING,
    REPLY_UNREAD,
    UNREAD,
    failure,
    listening,
    send_command,
    socket_path,
)
from ..wording import NO_REASON, escaped, reason

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
#: How long a round trip waits for the daemon's reply, in seconds; a request that can wait
#: out a device's own timeouts passes longer.
REPLY_WAIT = 3.0


class Call(Protocol):
    """The window's `call`: `unanswered(error)`, when given, runs instead of
    `callback(None)` for a round trip that raised. `timeout` is the worker's, longer for
    a request that can wait out the mouse's own timeouts."""

    def __call__(self, request: dict, callback: Done | None = None,
                 unanswered: Callable[[Exception], None] | None = None, /, *,
                 timeout: float = REPLY_WAIT) -> None: ...


class SetStatus(Protocol):
    """The window's `set_status`. `poll` True for a read, which carries no change: one
    that finds no daemon, or a socket it cannot use, does not keep a closing window up."""

    def __call__(self, reachable: bool, error: Exception | None = None, /, *,
                 poll: bool = False) -> None: ...


#: The waits of the A50 X's sliders, which the volume rows share: a slider moved by its keys
#: sends SETTLE_MS after the last change, so a run of arrow keys is one send (Settle); one the
#: pointer drags sends at most once each LIVE_MS (Pace), five a second, about the pace the
#: A50 X's own volume wheel sends at, six steps in about 2 s in its test (the owner's
#: decision of 2026-10-22). The headset keeps each value it is sent.
SETTLE_MS = 300
LIVE_MS = 200
#: What `unreachable` says for BUSY; for DOWN it says the client's NOT_RUNNING.
NOT_IN_TIME = "the daemon did not answer in time; it may be busy"


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
        return REPLY_UNREAD
    if kind == BUSY:
        return NOT_IN_TIME
    if kind == DOWN:
        return NOT_RUNNING
    return f"{NOT_REACHED} at {unusable_socket(error)}"


def unusable_socket(error: BaseException) -> str:
    """The socket's path and `error`'s words, for a socket `failure` calls UNKNOWN. Escaped:
    a runtime folder whose name is not UTF-8 gives a lone surrogate, on which PyGObject
    raises."""
    return f"{escaped(socket_path())}: {reason(error)}"


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
    message = str(reply.get("error", NO_REASON))
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
    callback: Callable[[Never], None]
    timeout: float
    work: Callable[[], object] | None = None
    #: The daemon's reply, or what `work` returned or raised, set on the worker thread.
    reply: Reply | Result = None


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
    def put_active(self, row: Adw.SwitchRow | Gtk.ToggleButton, value: bool) -> None:
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


class Pace:
    """Sends during a run of changes at most once an interval, `ms` long: the first change
    at once, and a change in an interval at its end, so a slider follows a drag live at a
    rate the device takes. The interval is a GLib timeout, so `send` runs on the main
    loop."""

    def __init__(self, send: Callable[[], None], ms: int) -> None:
        self._send = send
        self._ms = ms
        #: The interval's GLib source id while one runs, and whether a change came in it.
        self._source: int | None = None
        self._owed = False

    @property
    def pending(self) -> bool:
        """A change waits for the interval's end."""
        return self._owed

    def changed(self) -> None:
        """A change: sent now and an interval started, or, in one, sent at its end."""
        if self._source is not None:
            self._owed = True
            return
        self._send()
        self._source = GLib.timeout_add(self._ms, self._tick)

    def cancel(self) -> None:
        """Drop the change that waits and end the interval."""
        if self._source is not None:
            GLib.source_remove(self._source)
            self._source = None
        self._owed = False

    def flush(self) -> bool:
        """Send now the change that waits, and end the interval. True when one did."""
        owed = self._owed
        self.cancel()
        if owed:
            self._send()
        return owed

    def _tick(self) -> bool:
        # An interval with no change ends the run; one with a change sends it and starts
        # the next.
        if not self._owed:
            self._source = None
            return GLib.SOURCE_REMOVE
        self._owed = False
        self._send()
        return GLib.SOURCE_CONTINUE


def named_layer(layer: tuple[int | None, str | None]) -> dict:
    """The window's (M profile, game profile) `layer` as a request names it, which the daemon
    refuses once another is in force (Daemon.check_layer): the M profile's number, and the
    loaded game profile's name or None."""
    profile, overlay = layer
    return {"profile": profile, "overlay": overlay}


def take_typed(*rows: Adw.SpinRow) -> None:
    """Take the number typed in each spin row and not yet taken, as Enter does, for a read
    of the rows with the cursor maybe still in one. A spin row takes typed text itself on
    Enter and when the focus leaves it, and the window's close button moves no focus: at
    the close GTK takes the text only as the window is destroyed, after each page's close
    (measured with GTK 4.22.4)."""
    for row in rows:
        row.update()


class DaemonWorker:
    """One thread that talks to the daemon, so the GTK main loop never waits.

    A preview's round trip is 36 ms, and the daemon can stall over a second repainting
    after a 0x8071 notification, which on the main thread froze the color picker.

    One FIFO queue; each job goes back to the main thread through GLib.idle_add with its
    reply, which its callback gets there, so callbacks may touch widgets, and the job is
    let go of there, so a widget its callback reaches is freed there. An unsent preview
    last in the queue is replaced in its queue place by the next one of the same command,
    so a drag builds no backlog and a commit queued after a preview is sent after it.
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

    def call(self, request: dict, callback: ReplyCallback, timeout: float = REPLY_WAIT) -> None:
        if self._stopped:
            return
        with self._lock:
            self._open_preview = None
            self._queue.put(Job(request, callback, timeout))

    def preview(self, request: dict, callback: ReplyCallback) -> None:
        if self._stopped:
            return
        with self._lock:
            if (self._open_preview is not None
                    and self._open_preview.request.get("command") == request.get("command")):
                self._open_preview.request = request
                self._open_preview.callback = callback
                return
            self._open_preview = Job(request, callback, REPLY_WAIT)
            self._queue.put(self._open_preview)

    def run[T](self, work: Callable[[], T],
               callback: Callable[[T | Exception], None]) -> None:
        """Run `work` on the worker thread and hand the result to `callback`."""
        if self._stopped:
            return
        with self._lock:
            self._open_preview = None
            self._queue.put(Job({}, callback, 0.0, work))

    def stop(self) -> None:
        """Drop replies from here on and, here on the main thread, the jobs still queued, and
        let the thread exit after its current call."""
        self._stopped = True
        with self._lock:
            self._open_preview = None
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._queue.put(None)

    def _run(self) -> None:
        # A job's callback, its work and an exception's traceback can reach widgets, which
        # GTK frees only on the main thread. So each job goes back to the main loop in a
        # list the main loop empties (_deliver), and no frame of this thread holds the job
        # once it is handed over: the main thread lets go of it last, whichever finishes
        # first.
        while (job := self._queue.get()) is not None:
            handed = [job]
            del job
            handed[0].reply = self._reply(handed[0])
            GLib.idle_add(self._deliver, handed)

    def _reply(self, job: Job) -> Reply | Result:
        with self._lock:
            if job is self._open_preview:
                self._open_preview = None
            request, timeout, work = job.request, job.timeout, job.work
        # Let go of before anything can raise: a caught error's traceback keeps this frame,
        # and the job keeps the error as its reply, a cycle only the collector frees, on
        # whichever thread runs it.
        del job
        if work is not None:
            try:
                return work()
            except Exception as exc:  # noqa: BLE001 -- the thread must outlive one failed job
                return exc
        try:
            return self._send(request, timeout=timeout)
        except (OSError, ValueError) as exc:
            # ValueError: a reply cut short mid-send does not parse, and anything
            # raised here ends the thread, after which nothing is answered.
            return exc

    def _deliver(self, handed: list[Job]) -> bool:
        job = handed.pop()
        if not self._stopped:
            # The queue holds both kinds of job; `call`, `preview` and `run` each check
            # their callback against what it gets (Job.callback).
            cast("Callable[[Reply | Result], None]", job.callback)(job.reply)
        return GLib.SOURCE_REMOVE
