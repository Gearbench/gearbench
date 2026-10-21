"""The two subcommands that run a program of their own and ask no daemon: `daemon`, which
sets up the log and runs the daemon in the foreground, and `gui`, which opens the
window.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from pathlib import Path

from ..config import config_dir
from ..daemon import Daemon
from ..desktop import GREETER, session_class
from ..wording import escaped as _escaped
from .output import Subcommands


def cmd_gui(args: argparse.Namespace) -> int:
    """Open the GTK window."""
    try:
        from ..gui import main as gui_main  # noqa: PLC0415 -- optional: the CLI runs without GTK
    except (ImportError, ValueError) as exc:
        print(
            f"error: cannot load the GTK interface: {exc}\n"
            f"The window needs python3-gi, gir1.2-gtk-4.0 and gir1.2-adw-1; the CLI "
            f"works without them.",
            file=sys.stderr,
        )
        return 1
    # GApplication reads its own option: D-Bus starts the window as a service
    # and then asks it to open (desktop/io.github.gearbench.Gearbench.service).
    return gui_main(["gearbench-ctl", "--gapplication-service"] if args.gapplication_service
                    else [])


def sd_priority(levelno: int) -> int:
    """The sd-daemon(3) priority for a logging level."""
    for floor, priority in ((logging.CRITICAL, 2), (logging.ERROR, 3),
                            (logging.WARNING, 4), (logging.INFO, 6)):
        if levelno >= floor:
            return priority
    return 7


#: The most characters of a line one journal record holds after its `<N>`. journald ends a
#: stream record after LineMax bytes with no newline, 48K by default (journald.conf(5)), and
#: reads a `<N>` at the next one's start as its priority (stdout_stream_log in systemd's
#: journald-stream.c), which text a line quotes from a file would then choose. stderr writes
#: a character in 10 bytes at most: backslashreplace's "\U0001f600", where the locale's
#: encoding has no such character.
JOURNAL_LINE_CHARS = (48 * 1024 - len("<7>")) // 10     # 4914
#: No time: the journal keeps its own.
JOURNAL_FORMAT = "%(levelname)-7s %(message)s"


class JournalFormatter(logging.Formatter):
    """A record as journald reads it off stderr.

    journald takes a leading `<N>` as each line's priority, so every line carries
    one, a traceback's too. Lines split at "\n" and NUL, where journald splits
    (_LINE_BREAK in systemd.journal-fields(7)), and after JOURNAL_LINE_CHARS, before
    journald would; str.splitlines also splits at \r and others. No timestamp: the
    journal keeps its own."""

    def format(self, record: logging.LogRecord) -> str:
        prefix = f"<{sd_priority(record.levelno)}>"
        return "\n".join(prefix + line[start:start + JOURNAL_LINE_CHARS]
                         for line in re.split("[\n\0]", super().format(record))
                         for start in range(0, len(line) or 1, JOURNAL_LINE_CHARS))


def journal_stream() -> bool:
    """Whether stderr is the journal: JOURNAL_STREAM's device and inode are stderr's.
    A program started from a service inherits the variable with stderr elsewhere
    (systemd.exec(5)). sys.stderr is None when started with stderr closed."""
    try:
        device, inode = (int(part) for part in os.environ.get("JOURNAL_STREAM", "").split(":"))
        found = os.fstat(sys.stderr.fileno())
    except (AttributeError, ValueError, OSError):
        return False
    return (found.st_dev, found.st_ino) == (device, inode)


def cmd_daemon(args: argparse.Namespace) -> int:
    """Run the daemon, unless this is root or the login screen.

    As root it would drive the devices outside the unit's sandbox, and beside the user's
    own daemon, whose lock is in the user's runtime folder, not root's: exit 1. A package
    enables the unit for every account, GDM's login screen's too, where the daemon would
    claim the lighting and type through its virtual devices. Exit 0 there: the unit
    restarts only on a failure. Without python3-gi, session_class cannot read the class and
    the daemon runs there too.
    """
    handler = logging.StreamHandler()
    if journal_stream():
        handler.setFormatter(JournalFormatter(JOURNAL_FORMAT))
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s",
                                               datefmt="%H:%M:%S"))
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        handlers=[handler], force=True)
    if os.geteuid() == 0:
        logging.getLogger("gearbench").error(
            "the daemon runs as your own account, never as root: exiting without opening a "
            "device. Run `gearbench-ctl daemon` or `systemctl --user start gearbench` as "
            "yourself, not with sudo")
        return 1
    if session_class() == GREETER:
        logging.getLogger("gearbench").info(
            "this is the login screen (logind session class %s): exiting without opening "
            "a device", GREETER)
        return 0
    # The unit's %E/gearbench, from the user manager's environment, which
    # environment.d does not change. ReadWritePaths= resolves links, so this does too.
    granted = os.environ.get("GEARBENCH_SETTINGS_GRANT")
    if granted and Path(granted).resolve() != config_dir().resolve():
        logging.getLogger("gearbench").error(
            "the settings folder is %s, but the service may write only %s, from its user "
            "manager's XDG_CONFIG_HOME: where the service's sandbox applies, nothing is "
            "saved. Set XDG_CONFIG_HOME for the user manager itself, with "
            "ManagerEnvironment= in ~/.config/systemd/user.conf, or not at all",
            _escaped(config_dir()), _escaped(granted))
    return Daemon().run()


def add_daemon(sub: Subcommands) -> None:
    d = sub.add("daemon", help="run the daemon in the foreground", offers_json=False)
    d.add_argument("-v", "--verbose", action="store_true",
                   help="log G key presses and the mouse's G4 to G9")
    d.set_defaults(func=cmd_daemon)


def add_gui(sub: Subcommands) -> None:
    g = sub.add("gui", help="open the GTK window", offers_json=False)
    g.add_argument("--gapplication-service", action="store_true",
                   help="wait to be asked over D-Bus to open, as the desktop starts it")
    g.set_defaults(func=cmd_gui)
