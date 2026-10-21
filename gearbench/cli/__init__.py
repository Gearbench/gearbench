"""Command line for Gearbench.

Most commands go through the daemon's control socket: the daemon holds the 0x8071
claim, and a second process writing lighting would fight it. `probe`, `report`, `keys`,
`config`, `gui`, `daemon`, `headset-spatial`, `g432-eq`, `g432-noise`,
`headset-import --library`, and `import-ghub` and `import-pack` with `--list` or
`--dry-run` need no daemon, and `headset-library` needs it only to read or write a slot.
`probe` is read-only and reads the keyboard directly; `report` is read-only too, and sends
nothing to a node the daemon holds, to one of a model this build drives while a daemon runs
or may, or to one on a held node's USB device; `headset-spatial` runs systemctl, pw-dump and
pw-cli in this process, so the daemon's sandbox never has to allow them.

This module has `build_parser` and `main`. A group of subcommands is a module with its
handlers and the functions that add its subcommands to the parser:

- `output`: what the groups share: the request to the daemon, the printing of a reply,
  and `Subcommands`, which each group adds its own to.
- `headset`: the A50, and `headset-spatial`.
- `g432`: the Logitech G432.
- `keyboard`: the M profile, the lighting, `bind`, `keys` and `probe`.
- `library`: the game profiles, the macros, the running programs and the two imports.
- `mouse`: the mouse.
- `report`: `report`, the device report.
- `service`: `daemon` and `gui`.
- `status`: `status`, `save`, `reload`, the lock and tray switches, `idle` and `config`.
"""

from __future__ import annotations

import argparse
import json
import os
import select
import sys
import traceback
from typing import NoReturn, cast

from .. import __version__
from ..daemon import BUSY, DOWN, UNREAD, failure, socket_path
from ..wording import escaped, reason
from . import g432, headset, keyboard, library, mouse, output, report, service, status
from .output import Subcommands


class _Parser(argparse.ArgumentParser):
    """A parser whose refusals go through `escaped`: argparse quotes a refused value with
    repr, but writes an unrecognized argument and an ambiguous option as typed, and a glob
    passes on a file name whoever shared the file chose. add_subparsers makes each
    subcommand's parser of the root's class."""

    def error(self, message: str) -> NoReturn:
        super().error(escaped(message))


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="gearbench-ctl",
        description="Control Logitech G keyboards and mice, the Logitech G432 headset and "
                    "the Astro A50 Gen 4 headset: G key remapping, lighting, DPI, macros "
                    "and headset settings.",
        epilog="The keyboard's onboard profile is never written. The mouse-* commands that "
               "say so rewrite onboard profile 1, backed up first to "
               "~/.config/gearbench/backup/. Only headset-save writes the base station's "
               "flash.",
    )
    parser.add_argument("--json", action="store_true", help="print raw JSON replies")
    parser.add_argument("--version", action="version", version=f"gearbench-ctl {__version__}")

    sub = Subcommands(parser)
    # In the order `gearbench-ctl --help` lists the subcommands, which is why some groups
    # add theirs in more than one place.
    service.add_daemon(sub)
    status.add_status(sub)
    keyboard.add_lighting_and_bind(sub)
    status.add_save_and_reload(sub)
    library.add_lists_and_macros(sub)
    mouse.add_mouse(sub)
    headset.add_headset(sub)
    g432.add_g432(sub)
    status.add_switches_and_idle(sub)
    library.add_game_profiles_and_imports(sub)
    status.add_config(sub)
    service.add_gui(sub)
    keyboard.add_probe_and_keys(sub)
    report.add_report(sub)
    return parser


#: The exit status when stdout's reader closed it before reading everything, as `head`
#: does: what the shell reports for a program SIGPIPE ended, 128 + 13.
READER_LEFT = 141


def _stdout_reader_left() -> bool:
    """Whether stdout is a pipe or socket its reader closed, which tells such a
    BrokenPipeError from the daemon socket's. Relies on Linux's poll(2) setting POLLERR on
    a pipe's write end once the read end is closed, and POLLHUP on a socket whose peer
    closed."""
    try:
        poller = select.poll()
        poller.register(sys.stdout.fileno(), select.POLLOUT)
        return any(events & (select.POLLERR | select.POLLHUP)
                   for _fd, events in poller.poll(0))
    except (AttributeError, OSError, ValueError):
        # No stdout, or one with no descriptor, such as a test's StringIO.
        return False


def _reader_left() -> int:
    """Drop what stdout still holds: /dev/null in its place leaves Python's flush at exit
    nothing to fail on (the signal module's docs, "Note on SIGPIPE")."""
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, sys.stdout.fileno())
    os.close(devnull)
    return READER_LEFT


def _flushed() -> bool:
    """Flush stdout here, not in Python's own flush at exit, which takes a reader that left
    for an error and exits 120. False when its reader closed it. sys.stdout is None when
    started with stdout closed, and print then writes nothing."""
    try:
        if sys.stdout is not None:
            sys.stdout.flush()
    except BrokenPipeError:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    try:
        code = _run(build_parser().parse_args(argv))
    except SystemExit:
        # --help and --version print, then exit through here.
        if _flushed():
            raise
        raise SystemExit(_reader_left()) from None
    return code if _flushed() else _reader_left()


def _run(args: argparse.Namespace) -> int:
    try:
        return args.func(args)
    except (OSError, json.JSONDecodeError) as exc:
        # A BrokenPipeError from a print whose reader left, not from the daemon.
        if isinstance(exc, BrokenPipeError) and _stdout_reader_left():
            return _reader_left()
        if not _from_daemon(exc):
            # Not the daemon's: a print's, or a file's or a device's in a command that asks
            # no daemon. Let through.
            raise
        print(f"error: {_daemon_error(exc)}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


def _from_daemon(exc: BaseException) -> bool:
    """Whether `exc` was raised under output.send, the command line's one request to the
    daemon. A print raises the same errors, ENOSPC from stdout on a full disk and EAGAIN
    from a terminal left non-blocking, and those are not the daemon's."""
    return any(frame.f_code is output.send.__code__
               for frame, _line in traceback.walk_tb(exc.__traceback__))


def _daemon_error(exc: OSError | json.JSONDecodeError) -> str:
    """What went wrong with a request to the daemon, as `_run` says it."""
    kind = failure(exc)
    if kind == UNREAD:
        # Something answered, and may have done the request.
        return f"the daemon's reply could not be read: {exc}"
    # Each kind but UNREAD is an OSError (failure).
    error = cast(OSError, exc)
    if kind == DOWN:
        # send_command's own are sentences; the kernel's (a reset, a broken pipe) carry an
        # errno and name nothing.
        return (reason(error) if error.errno is None
                else f"cannot talk to the daemon: {reason(error)}")
    if kind == BUSY:
        # connect() to a full backlog, so unlike a timeout the request never left.
        return ("the daemon is busy; the request was not sent. Try again in a moment."
                if isinstance(error, BlockingIOError)
                else "the daemon did not answer in time; it may be busy")
    # connect()'s error names no path.
    return f"cannot talk to the daemon on {socket_path()}: {reason(error)}"
