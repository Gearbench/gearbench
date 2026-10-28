"""A preset library's command, which the headsets' groups share: its arguments, its refusals,
its list and its run (run_library), the daemon's reply with the model a library is of
(library_reply), and the words of a preset's name as typed (typed_name) and of a library in
a report.
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .. import astro, eq_presets
from ..wording import RESTART_DAEMON, escaped as _escaped
from .output import failed, print_block, print_json, print_reply, send

#: A preset library's label in a report, and its line while it holds none, with how to save
#: one into it.
LIBRARY_LABEL = "library"
LIBRARY_EMPTY = "no presets yet; {}"
#: Why --delete is refused with another change.
ALONE = "--delete takes no other change of the preset"


def typed_name(name: str) -> str:
    # argv holds a byte that is not UTF-8 as a lone surrogate, which encode() raises on.
    if any(unicodedata.category(c) == "Cs" for c in name):
        raise ValueError("a preset name cannot hold a byte that is not UTF-8")
    # astro.preset_name: the daemon's rule and the preset files', so a name is refused
    # before anything is sent or saved.
    astro.preset_name(name)
    return name


#: The refusals of a change with no preset's number, the command's name put in, and of a save
#: given a number or another change.
NO_NUMBER = "give the number of a preset in the library, as {} lists them"
SAVE_NEW = "--save-as saves a new preset alone: no number, --name, --delete or --pick"
#: The refusal of a model the daemon names and this install does not know, so has no library
#: for: the daemon runs another version.
NO_LIBRARY = ("the daemon names the {}, a model this gearbench-ctl does not know, so it keeps "
              f"no library for it: {RESTART_DAEMON}")


def library_reply[M](args: argparse.Namespace, model_of: Callable[[dict], M | None], *,
                     command: str) -> tuple[dict, M] | None:
    """The daemon's reply to `command` and the model `model_of` finds in it; None once why
    there is none is said: the reply's refusal, or NO_LIBRARY. `command` is keyword-only so
    that each caller's literal is one the daemon's command table is checked against."""
    reply = send({"command": command})
    if not reply.get("ok"):
        print_reply(reply, args.json)
        return None
    if (model := model_of(reply)) is None:
        print(f"error: {NO_LIBRARY.format(_escaped(reply['name']))}", file=sys.stderr)
        return None
    return reply, model


class LibraryKind[M, P: eq_presets.Record](Protocol):
    """A kind of preset library a command keeps, for models M, of presets P: the command's
    name, its list's line with no preset in it, the daemon's reply with the model it names
    (None once why there is none is said), the library's read, change and path, a new preset
    of the bands the reply holds (None once why it cannot be is said), a preset's words and its
    entry, and the daemon's reply after a pick, said."""

    @property
    def command(self) -> str: ...

    @property
    def none_saved(self) -> str: ...

    def attached(self, args: argparse.Namespace) -> tuple[dict, M] | None: ...

    def load(self, model: M) -> eq_presets.Saved[P]: ...

    def edit(self, change: Callable[[eq_presets.Saved[P]], eq_presets.Saved[P]],
             model: M) -> eq_presets.Saved[P]: ...

    def path(self, model: M) -> Path: ...

    def saved_preset(self, args: argparse.Namespace, reply: dict, model: M,
                     name: str) -> P | None: ...

    def words(self, preset: P) -> str: ...

    def entry(self, preset: P) -> dict: ...

    def report(self, reply: dict) -> None: ...


def library_number(parser: argparse.ArgumentParser) -> None:
    """NUMBER, the preset a library's command shows or changes."""
    parser.add_argument("number", nargs="?", type=int,
                        help="the library preset to show or change, as the list numbers them; "
                             "every preset is listed when left out")


def library_arguments(parser: argparse.ArgumentParser, *, picks: str) -> None:
    """NUMBER (library_number), --name, --delete and --pick, which run_library reads, after
    the command's own --save-as; `picks` is what --pick does."""
    library_number(parser)
    parser.add_argument("--name", help="rename it")
    parser.add_argument("--delete", action="store_true",
                        help="delete it, alone; those after it move up a number")
    parser.add_argument("--pick", action="store_true", help=f"after --name, {picks}")


def number_refusal(command: str, number: int | None, *, changes: bool, delete: bool,
                   sends: bool) -> str | None:
    """Why a library command's options go together no way: a change, a delete or a send of
    a preset to the device with no preset's number (NO_NUMBER, `command` put in), or a delete
    with anything else (ALONE); None where they do."""
    if number is None and (changes or delete or sends):
        return NO_NUMBER.format(command)
    if delete and (changes or sends):
        return ALONE
    return None


def library_refusal(args: argparse.Namespace, command: str) -> str | None:
    """Why the options of the library's command `command` go together no way, or None:
    --save-as with a number, --name, --delete or --pick (SAVE_NEW), then number_refusal's,
    --pick the send."""
    if args.save_as is not None and (args.number is not None or args.name is not None
                                     or args.delete or args.pick):
        return SAVE_NEW
    return number_refusal(command, args.number, changes=args.name is not None,
                          delete=args.delete, sends=args.pick)


def checked_library(library: eq_presets.Saved, number: int | None, changes: bool) -> None:
    """ValueError in words where `library`, as read, does not take what a command asks, before
    anything is written: a change, where `changes`, to a library read in part (Saved.whole),
    or a `number` that is none of its presets (Saved.number)."""
    if changes:
        library.whole()
    if number is not None:
        library.number(number)


def library_name(args: argparse.Namespace, library: eq_presets.Saved, changes: bool) -> str:
    """The new preset's name or the new name, "" for none, once `library` takes what `args`
    asks, a change where `changes` (checked_library): ValueError in words where it does not,
    before anything is written."""
    checked_library(library, args.number, changes)
    typed = args.name if args.save_as is None else args.save_as
    name = "" if typed is None else typed_name(typed)
    if args.save_as is not None:
        library.has_room()
    return name


def library_listed[M, P: eq_presets.Record](kind: LibraryKind[M, P], as_json: bool,
                                            library: eq_presets.Saved[P],
                                            shown: int | None) -> int:
    """The library's presets, or preset `shown` alone, each its number and words; with
    `as_json` every preset, its number and entry. What the file held that did not read is
    noted on stderr, which keeps --json's output whole."""
    if as_json:
        print_json({"presets": [{"number": number, **kind.entry(library.get(number))}
                                for number in library.numbers]})
    elif not library.presets:
        print_block([(LIBRARY_LABEL, kind.none_saved)])
    else:
        numbers = library.numbers if shown is None else (shown,)
        print_block((f"{LIBRARY_LABEL} {number}", kind.words(library.get(number)))
                    for number in numbers)
    note_left_out(library)
    return 0


def note_left_out(library: eq_presets.Saved) -> None:
    """What the file `library` was read from held that did not read, a warning on stderr, as
    report's problem lines are, escaped, which keeps --json's output whole."""
    if library.left_out:
        print(f"warning: {_escaped(library.left_out)}", file=sys.stderr)


def run_library[M, P: eq_presets.Record](args: argparse.Namespace,
                                         kind: LibraryKind[M, P]) -> int:
    """The library of the model the daemon names, after --save-as, --name or --delete: the
    preset NUMBER or --save-as names alone, every preset with neither, after --delete or with
    --json; with --pick, NUMBER's request to the daemon, and the daemon's reply said in the
    list's place."""
    if (refusal := library_refusal(args, kind.command)) is not None:
        print(f"error: {refusal}", file=sys.stderr)
        return 1
    if (attached := kind.attached(args)) is None:
        return 1
    reply, model = attached
    changes = args.name is not None or args.delete or args.save_as is not None
    try:
        library = kind.load(model)
        name = library_name(args, library, changes)
    except ValueError as exc:
        print(f"error: {_escaped(exc)}", file=sys.stderr)
        return 1
    preset = None
    if args.save_as is not None and (
            preset := kind.saved_preset(args, reply, model, name)) is None:
        return 1

    def change(saved: eq_presets.Saved[P]) -> eq_presets.Saved[P]:
        if preset is not None:
            return saved.added(preset)
        if args.delete:
            return saved.deleted(args.number)
        return saved.named(args.number, name)

    if changes:
        try:
            library = kind.edit(change, model)
        except (ValueError, OSError) as exc:
            return failed(exc, lambda: kind.path(model))
    shown = (len(library.presets) if preset is not None
             else None if args.delete else args.number)
    if args.pick:
        reply = send(library.get(args.number).request())
        if args.json or not reply.get("ok"):
            return print_reply(reply, args.json)
        kind.report(reply)
        return 0
    return library_listed(kind, args.json, library, shown)
