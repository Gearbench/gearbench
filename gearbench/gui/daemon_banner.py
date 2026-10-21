"""The window's daemon banner: what a failed round trip says of the daemon, and its Start it.

This part owns the banner, its title for each kind of failure, and Start it, which runs the
user service on the worker thread and waits for the socket to accept. Its window builds it,
places the banner, and shows it at a failure and hides it at a good status (set_status).
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from typing import cast

from gi.repository import Adw

from .. import spatial
from ..wording import ended, reason
from .worker import (
    BUSY,
    DOWN,
    UNREAD,
    DaemonWorker,
    SetStatus,
    daemon_listening,
    failure,
    socket_path,
)

#: What the banner's Start it runs, the command the guide's First-time setup gives.
START_COMMAND = ["systemctl", "--user", "start", "gearbench"]
#: Seconds to wait for systemctl, which blocks until the unit is up or has failed.
START_TIMEOUT = 30.0
#: Seconds to wait for the socket after a start: the unit is Type=simple, so systemctl
#: returns once the process forks, before it binds the socket.
SOCKET_WAIT = 5.0

#: The banner's titles. A busy daemon, most likely on a mouse profile write, gets no
#: Start it. Stopped, the daemon leaves the keyboard to its onboard profiles, whose bindings
#: differ by model: a G915's bind G1-G5 to F1-F5 on M1, 1-5 on M2 and 6-0 on M3 (its
#: sectors 1 to 3 at 0x20, in G HUB's backup images of them), a G915 TKL's F1 to F12 type
#: F1 to F12 (keyboard.G915_TKL). The window cannot tell which keyboard is meant while the
#: daemon is down, so the title names no binding. Killed, it hands nothing back: a keyboard
#: it had set up stays in host mode with the G keys diverted, and the socket file it leaves
#: refuses the connect.
DOWN_TITLE = ("The Gearbench daemon is not running, so the G keys type their onboard "
              "bindings, or may type nothing if it was killed.")
BUSY_TITLE = "The Gearbench daemon did not answer in time; it may be busy"
#: A reply that does not parse: something answered, so no Start it either.
UNREAD_TITLE = "The Gearbench daemon's reply could not be read"
#: A socket that could not be used (another user's, say): Start it would not help.
UNREACHABLE_TITLE = "The Gearbench daemon could not be reached"
START_LABEL = "Start it"
#: The banner after Start it failed, with what systemctl said, or that it could not be run
#: or did not finish, or NOT_LISTENING.
START_FAILED = "The Gearbench daemon did not start: {}"
#: Why a start systemctl took failed: the unit is Type=simple, so systemctl exits 0 for a
#: daemon that exits at once, at an import error or a socket it cannot bind, say.
NOT_LISTENING = (f"it exited at once or did not listen within {SOCKET_WAIT:g} s; "
                 "journalctl --user -u gearbench says why")


def start_daemon(run: spatial.Run = subprocess.run) -> tuple[int, str]:
    """Run START_COMMAND with `run` on the worker thread, as it blocks. Returns its exit code
    and stderr, or for a start that succeeded, 0 and "" once the daemon accepts, so the read
    that follows finds it listening, and 0 and NOT_LISTENING if it does not within
    SOCKET_WAIT.
    """
    done = run(START_COMMAND, check=False, capture_output=True, text=True, timeout=START_TIMEOUT)
    if done.returncode != 0:
        return done.returncode, done.stderr.strip()
    deadline = time.monotonic() + SOCKET_WAIT
    while not daemon_listening():
        if time.monotonic() >= deadline:
            return 0, NOT_LISTENING
        time.sleep(0.05)
    return 0, ""


class DaemonBanner:
    """The banner up while the daemon cannot be used, with Start it while it is not running.

    `resync` reads the daemon again once a start is up, and a start that failed is shown
    through `failed`, the window's set_status. Start it runs START_COMMAND with `run`.
    """

    #: What Start it failed with, which the banner says until a start succeeds or a daemon
    #: answers.
    _start_failed: str | None = None
    #: Start it's systemctl run is out (_on_start).
    _starting = False

    def __init__(self, worker: DaemonWorker, resync: Callable[[], None], failed: SetStatus,
                 run: spatial.Run = subprocess.run) -> None:
        self._worker = worker
        self._resync = resync
        self._failed = failed
        self._run = run
        # A banner for its Start it button: a header label could carry the command only in a
        # tooltip, which the keyboard cannot reach and a screen reader is not given.
        self.banner = Adw.Banner()
        self.banner.connect("button-clicked", self._on_start)

    def show(self, error: Exception | None) -> None:
        """Up, titled for what `failure` makes of `error`, a round trip's that failed."""
        kind = failure(error)
        if kind == UNREAD:
            title, button = UNREAD_TITLE, ""
        elif kind == BUSY:
            title, button = BUSY_TITLE, ""
        elif kind == DOWN:
            title = (START_FAILED.format(self._start_failed) if self._start_failed
                     else DOWN_TITLE)
            button = START_LABEL
        else:
            # Shown for a failure, so its caller gave the error.
            why = reason(cast(Exception, error))
            title, button = f"{UNREACHABLE_TITLE} at {socket_path()}: {why}", ""
        self.banner.set_title(title)
        self.banner.set_button_label(button)
        self.banner.set_revealed(True)

    def hide(self) -> None:
        """Down at a good status, which ends what a failed start said."""
        self._start_failed = None
        self.banner.set_revealed(False)

    def _on_start(self, _banner: Adw.Banner) -> None:
        """Start the user service, and read it again once it is up. A click while a start is
        out does nothing: each holds the worker, and every request behind it, for up to
        START_TIMEOUT and SOCKET_WAIT."""
        if self._starting:
            return
        self._starting = True

        def done(result: tuple[int, str] | Exception) -> None:
            self._starting = False
            if isinstance(result, subprocess.TimeoutExpired):
                # It did run, and the exception's text quotes the command as a Python list.
                self._start_failed = f"systemctl did not finish in {START_TIMEOUT:g} s"
            elif isinstance(result, Exception):
                self._start_failed = f"could not run systemctl: {reason(result)}"
            else:
                code, error = result
                if code == 0 and not error:
                    self._start_failed = None
                    self._resync()
                    return
                self._start_failed = " ".join((error or ended("systemctl", code)).split())
            self._failed(False, ConnectionError(self._start_failed))
        self._worker.run(lambda: start_daemon(self._run), done)
