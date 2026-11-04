"""`headset-outputs`: Gearbench Game, Chat and Media, for every headset in
pipewire_headsets.HEADSETS, switched and read in this process through systemctl, pw-dump
and pw-metadata
(gearbench.outputs_unit, gearbench.outputs_state), and the balance's words sent to the
unit's socket (gearbench.outputs); no daemon.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict, replace

from .. import astro, outputs, outputs_state, outputs_unit, pipewire_client
from ..settings_files import ConfigError
from ..wording import escaped as _escaped, word_list
from .output import Subcommands, print_json, print_unit_state, save_failed, unit_failed

#: The actions beside showing it.
ON, OFF, BALANCE, APP = "on", "off", "balance", "app"
#: The usage errors of balance's and app's words.
BALANCE_USAGE = (f"balance takes one word: {outputs.GAME_WORD}, {outputs.CHAT_WORD}, or a "
                 "balance from 0 (all chat) to 255 (all game)")
APP_USAGE = ("app takes a key `headset-outputs` lists and one of "
             f"{word_list(outputs_state.CHOICES)}")


def balance_word(words: list[str]) -> outputs.Word:
    """The word balance's argument gives: a step, or a balance as `set N`. ValueError, in
    BALANCE_USAGE's words, for anything else."""
    if len(words) != 1:
        raise ValueError(BALANCE_USAGE)
    word = words[0]
    data = word if word in (outputs.GAME_WORD, outputs.CHAT_WORD) else f"{outputs.SET_WORD} {word}"
    try:
        return outputs.parse(data.encode())
    except (outputs.WordRefused, UnicodeEncodeError):
        raise ValueError(BALANCE_USAGE) from None


def _send(word: outputs.Word, as_json: bool) -> int:
    """`word` to the unit's process and its answer printed: the balance after it, as
    outputs_state.balance_words gives it, or {"balance": N} with `as_json`; or why it was
    refused, which is 1, as a word not sent is, an error line on standard error with `as_json`
    too."""
    try:
        answer = outputs.ask(word)
    except (outputs.SocketRefused, outputs.NotTaken) as exc:
        print(f"error: {_escaped(exc)}", file=sys.stderr)
        return 1
    refused = outputs.refusal(answer)
    if refused is not None:
        print(f"error: {_escaped(refused)}", file=sys.stderr)
        return 1
    balance = answer.removeprefix(outputs.BALANCE_ANSWER.format(""))
    # The answer comes from another process, which may run another version: one that is no
    # number, or for the words none on the scale, is printed as it came.
    found = pipewire_client.as_whole(balance)
    if as_json:
        print_json({"balance": balance if found is None else found})
        return 0
    low, high = astro.BALANCE_RANGE
    print("balance: " + (outputs_state.balance_words(found)
                         if found is not None and low <= found <= high else _escaped(balance)))
    return 0


def cmd_headset_outputs(args: argparse.Namespace) -> int:
    """Outputs' state, after on, off or an app's move, the off's with no program's line; a
    balance word sent and its answer."""
    try:
        word = balance_word(args.words) if args.action == BALANCE else None
        if args.action == APP and (len(args.words) != 2
                                   or args.words[1] not in outputs_state.CHOICES):
            raise ValueError(APP_USAGE)
        if args.action not in (BALANCE, APP) and args.words:
            raise ValueError(f"{args.action} takes no word")
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if word is not None:
        return _send(word, args.json)
    notes: list[str] = []
    try:
        if args.action == ON:
            notes = outputs_state.switch_on()
        elif args.action == OFF:
            notes = outputs_state.switch_off()
        elif args.action == APP:
            notes = [note] if (note := outputs_state.move_app(*args.words)) else []
    except pipewire_client.StepError as exc:
        return unit_failed(exc, outputs.UNIT)
    except OSError as exc:
        # Only saving outputs.json raises one; systemctl's and PipeWire's arrive as StepError.
        return save_failed(exc, outputs_unit.settings_path())
    except ConfigError as exc:
        # write_json's refusal of an outputs.json over settings_files.MAX_JSON_BYTES, which a
        # default output's name can make: its words name the file.
        print(f"error: {_escaped(exc)}", file=sys.stderr)
        return 1
    state = pipewire_client.settled(outputs_state.read_state, outputs_state.summary,
                                    args.action == ON)
    if args.action == OFF:
        # WirePlumber moves the programs off the three once they are gone, seconds after the
        # stop (measured 2026-10-28): read now, each plays "nowhere yet". Once off, Outputs
        # moves none of them.
        state = replace(state, apps=())
    print_unit_state(asdict(state), outputs_state.summary(state),
                     outputs_state.describe(state), notes, args.json)
    return 0


def add_headset_outputs(sub: Subcommands) -> None:
    hop = sub.add("headset-outputs",
                  help=f"show or set {outputs.DESCRIPTION} ({outputs.GAME.description}, "
                       f"{outputs.CHAT.description} and {outputs.MEDIA.description}, outputs "
                       "for games, voice chat and music on the headset Gearbench Spatial plays "
                       "to): on or off, the balance, and the output each program plays on; no "
                       "daemon needed")
    hop.add_argument("action", nargs="?", choices=[ON, OFF, BALANCE, APP],
                     help="on: they run from each login on; if they were stopped, they start "
                          "and Gearbench Game becomes the default output; off: the default "
                          "output you had comes back, unless you picked another since, and "
                          "they stop; balance WORD: "
                          f"{outputs.GAME_WORD} or {outputs.CHAT_WORD} moves the balance one of "
                          f"its {len(outputs.STOPS)} stops, or a number from 0 (all chat) to 255 "
                          "(all game) takes the stop nearest it; app KEY OUTPUT: the program "
                          "`headset-outputs` lists under KEY plays on "
                          f"{word_list(outputs_state.CHOICES)}")
    hop.add_argument("words", nargs="*", metavar="WORD",
                     help="balance's word, or app's key and output")
    hop.set_defaults(func=cmd_headset_outputs)
