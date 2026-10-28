"""Command line for Gearbench.

Most commands go through the daemon's control socket: the daemon holds the 0x8071
claim, and a second process writing lighting would fight it. The man page's DESCRIPTION
names the commands that need no daemon. `headset-spatial` runs systemctl, pw-dump and
pw-cli in this process, and `headset-volume` pw-dump and pw-cli, so the daemon's sandbox
never has to allow them.

This module has `build_parser` and `main`. A group of subcommands is a module with its
handlers and the functions that add its subcommands to the parser:

- `output`: what the groups share: the request to the daemon, the printing of a reply,
  and `Subcommands`, which each group adds its own to.
- `presets`: what the headsets' groups share of their presets: a preset library's
  command (`run_library`) and a preset's name as typed.
- `a50x`: `audio` and the commands after it, for the Astro A50 X and A50 Gen 5 and the
  PRO X 2, G522 and G325 LIGHTSPEED headsets.
- `headset`: the A50 Gen 4.
- `headset_spatial`: `headset-spatial`, for every headset in spatial.HEADSETS.
- `headset_volume`: `headset-volume`, the volume of each headset's microphone and of the
  Game and Chat outputs of the headsets in system_volume.OUTPUTS.
- `g432`: the Logitech G432 and the other headsets of its family.
- `keyboard`: the M profile, the lighting, the leader (`sync-keyboard`), `bind`, `keys` and
  `probe`.
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
from ..daemon import BUSY, DOWN, NOT_REACHED, REPLY_UNREAD, UNREAD, failure, socket_path
from ..desktop import APP_SUMMARY
from ..keyboard import MODEL_OF as KEYBOARDS
from ..wording import escaped, reason, word_list
from . import (
    a50x,
    g432,
    headset,
    headset_spatial,
    headset_volume,
    keyboard,
    library,
    mouse,
    output,
    report,
    service,
    status,
)
from .output import JSON_HELP, Subcommands


class _Parser(argparse.ArgumentParser):
    """A parser whose refusals go through `escaped`: argparse quotes a refused value with
    repr, but writes an unrecognized argument and an ambiguous option as typed, and a glob
    passes on a file name whoever shared the file chose. add_subparsers makes each
    subcommand's parser of the root's class."""

    def error(self, message: str) -> NoReturn:
        super().error(escaped(message))


def _keeping_brightness(tested: bool) -> list[str]:
    """The keyboards whose records keep the brightness they are sent through a power-off,
    those tested on their hardware or those taken to, each name once and in MODEL_OF's
    order."""
    return list(dict.fromkeys(model.name for model in KEYBOARDS.values()
                              if model.keeps_brightness and model.tested == tested))


def build_parser() -> argparse.ArgumentParser:
    # The epilog from the records, so a model added or a model's write test changes what
    # --help says; the description is what Gearbench is, the one sentence of every view.
    unwritten = a50x.model_names(lambda model: not model.writes_tested)
    taken = _keeping_brightness(tested=False)
    parser = _Parser(
        prog="gearbench-ctl",
        description=f"{APP_SUMMARY}.",
        epilog="The keyboard's onboard profile is never written, but a "
               f"{word_list(_keeping_brightness(tested=True))} keeps the brightness it is "
               "sent through a power-off"
               + (f", as a {word_list(taken)} is taken to" if taken else "") + ". "
               "The mouse-* commands that say so rewrite onboard profile 1, backed up first "
               "to ~/.config/gearbench/backup/. The G432's adapter kept the level "
               "g432-sidetone wrote through an unplug when tested; g432-auto-off and "
               "g432-lights --startup and --store write what a headset of its family keeps, "
               "refused on a model not tested on its hardware. Only headset-save writes the "
               "Astro A50 Gen 4 base station's flash; the Astro A50 X keeps each setting "
               "written to it through a power cycle." + (
                   f" No write is sent to the {word_list(unwritten)} until it is tested on "
                   "that model." if unwritten else ""),
    )
    parser.add_argument("--json", action="store_true", help=JSON_HELP)
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
    keyboard.add_leader(sub)
    mouse.add_mouse_switches(sub)
    headset.add_headset(sub)
    headset_spatial.add_headset_spatial(sub)
    headset_volume.add_headset_volume(sub)
    g432.add_g432(sub)
    a50x.add_audio(sub)
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
        # The socket's path comes from XDG_RUNTIME_DIR, as written there.
        print(f"error: {escaped(_daemon_error(exc))}", file=sys.stderr)
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
        return f"{REPLY_UNREAD}: {exc}"
    # Each kind but UNREAD is an OSError (failure).
    error = cast(OSError, exc)
    if kind == DOWN:
        # send_command's own are sentences; the kernel's (a reset, a broken pipe) carry an
        # errno and name nothing.
        return (reason(error) if error.errno is None
                else f"{NOT_REACHED}: {reason(error)}")
    if kind == BUSY:
        # connect() to a full backlog, so unlike a timeout the request never left.
        return ("the daemon is busy, so the request was not sent; try again in a moment"
                if isinstance(error, BlockingIOError)
                else "the daemon did not answer in time; it may be busy")
    # connect()'s error names no path.
    return f"{NOT_REACHED} at {socket_path()}: {reason(error)}"
