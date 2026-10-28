"""`headset-volume`: the system volume of each headset's outputs and microphone
(system_volume.VOLUMES), as GNOME's Sound settings show and set it, run in this process
through pw-dump and pw-cli; no daemon.
"""

from __future__ import annotations

import argparse
import sys

from .. import pipewire_client, spatial, system_volume
from ..wording import escaped, word_list
from .output import Subcommands, print_block, print_json

#: A volume's line while it is not in PipeWire.
NOT_THERE = "not in PipeWire"
#: The command alone, while no headset's volume is in PipeWire.
NONE_THERE = "no headset in PipeWire"
#: The refusal of an output the headset named does not have, by its name and the output's.
NO_SUCH = "the {} has no {} output: game and chat are for {}"
#: The refusal of a level outside GNOME's range, the level given put in.
NOT_A_LEVEL = f"the level is 0 to {system_volume.LEVEL_MAX}, GNOME's percent, not {{}}"
#: The headsets by their keys, and the volumes, as the arguments take them.
HEADSET_WORDS = list(system_volume.VOLUMES)
VOLUME_WORDS = [*system_volume.OUTPUT_NAMES, system_volume.MIC]


def _level(text: str) -> int:
    """The level `headset-volume` sets as a whole number; ArgumentTypeError for text that is
    not one. Its range is the command's to refuse, as macro-run refuses its --delay."""
    try:
        return int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"the level is a whole number, not {text!r}") from None


def _place(args: argparse.Namespace) -> None:
    """Put the words `headset-volume` was given in their places. With --headset, the words
    after the command are the volume and the level, so argparse, which fills its positional
    arguments in order, cannot check them: each is checked here, and one that is none of its
    argument's choices is refused as argparse refuses one, usage line and exit 2."""
    words = [args.headset, args.volume, args.level]
    if args.headset_option is not None:
        if words[2] is not None:
            args.usage_error(f"unrecognized arguments: {words[2]}")
        words = [args.headset_option, *words[:2]]
    args.headset, args.volume, level = words
    for name, choices in (("headset", HEADSET_WORDS), ("volume", VOLUME_WORDS)):
        word = getattr(args, name)
        if word is not None and word not in choices:
            args.usage_error(f"argument {name}: invalid choice: {word!r} "
                             f"(choose from {', '.join(choices)})")
    try:
        args.level = None if level is None else _level(level)
    except argparse.ArgumentTypeError as exc:
        args.usage_error(f"argument level: {exc}")


def cmd_headset_volume(args: argparse.Namespace) -> int:
    """Set the volume a level is given for, then show the volumes asked for, read again: that
    one, the headset's, or alone those of each headset with one in PipeWire."""
    _place(args)
    if args.volume is not None and args.volume not in system_volume.VOLUMES[args.headset]:
        refused = NO_SUCH.format(spatial.HEADSET_KEYED[args.headset].name,
                                 system_volume.OUTPUT_NAMES[args.volume],
                                 word_list(list(system_volume.OUTPUTS), "and"))
        print(f"error: {refused}", file=sys.stderr)
        return 1
    if args.level is not None and not 0 <= args.level <= system_volume.LEVEL_MAX:
        print(f"error: {NOT_A_LEVEL.format(args.level)}", file=sys.stderr)
        return 1
    keys = [args.headset] if args.headset else list(system_volume.VOLUMES)
    try:
        if args.level is not None:
            system_volume.set_level(args.headset, args.volume, args.level)
        found = system_volume.read_volumes(keys)
    except pipewire_client.StepError as exc:
        print(f"error: {escaped(exc)}", file=sys.stderr)
        return 1
    shown = {key: {word: volume for word, volume in volumes.items()
                   if args.volume in (None, word)}
             for key, volumes in found.items()
             if args.headset or any(volume is not None for volume in volumes.values())}
    if args.json:
        print_json({key: {word: None if volume is None else volume.level
                          for word, volume in volumes.items()}
                    for key, volumes in shown.items()})
        return 0
    if not shown:
        print(NONE_THERE)
        return 0
    print_block((f"{key} {word}", NOT_THERE if volume is None else f"{volume.level}%")
                for key, volumes in shown.items() for word, volume in volumes.items())
    return 0


def add_headset_volume(sub: Subcommands) -> None:
    outputs = word_list([spatial.HEADSET_KEYED[key].name for key in system_volume.OUTPUTS],
                        "and")
    volume = sub.add("headset-volume",
                     help="show or set the volume of a headset's microphone, and of the Game "
                          f"and Chat outputs of the {outputs}, in percent, as GNOME's Sound "
                          "settings show and set it; no daemon needed")
    volume.add_argument("--headset", dest="headset_option", choices=HEADSET_WORDS,
                        help="the headset, as the other headset commands take it, in place "
                             "of the first word")
    # With --headset each word after the command moves up a place, so _place checks them
    # and the metavars list the choices, as argparse does.
    volume.add_argument("headset", nargs="?", metavar="{" + ",".join(HEADSET_WORDS) + "}",
                        help="the headset; left out, each headset in PipeWire is shown")
    volume.add_argument("volume", nargs="?", metavar="{" + ",".join(VOLUME_WORDS) + "}",
                        help=f"game or chat, an output of the {outputs}, or mic, the "
                             "microphone; left out, each of the headset's is shown")
    volume.add_argument("level", nargs="?",
                        help=f"the volume to set, 0 to {system_volume.LEVEL_MAX}; left out, "
                             "it is shown")
    volume.set_defaults(func=cmd_headset_volume, usage_error=volume.error)
