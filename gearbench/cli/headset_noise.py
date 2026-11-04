"""`headset-noise`, `g432-noise` in 1.2.0: a headset's noise suppression (headset_mic), on,
off or its state, run in this process through systemctl, systemd-run, pw-metadata and
pw-dump, on the headset `headset_of` picks, and its test, through pw-record and pw-play; no
daemon needed, one running asked only which headset of the G432's family it drives.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import asdict
from typing import TextIO

from .. import a50x, headset_mic, pipewire_client, pipewire_headsets, pipewire_unit
from ..a50x import find_each_a50x
from ..astro import find_base
from .output import Subcommands, driven_g432, failed, print_json, print_unit_state

#: The refusal of a headset whose noise suppression is in the headset, by its name.
OWN = ("the {}'s noise suppression is in the headset, not on the computer: "
       "`gearbench-ctl audio` shows it")
#: The test's lines, as the window counts them: before it records, by the input's name and
#: the number counted (headset_mic.COUNT_IN); while it records, by the input's name and the
#: seconds left; as it plays back; and its report, by the seconds it recorded.
COUNTING = "recording {} in {}"
RECORDING = "recording {}: {} s left"
PLAYING = "playing back on the default output"
TESTED = "recorded {:.1f} s and played them back"
#: --headset's choices: every headset Gearbench Spatial plays to, those with noise suppression
#: of their own too, which OWN refuses.
HEADSET_WORDS = list(pipewire_headsets.HEADSET_KEYED)


def headset_of(args: argparse.Namespace) -> str:
    """The key of the headset `--headset` names, else of one plugged in: the one of the
    G432's family the daemon drives, or would drive at its next look (driven_g432), else one
    found in sysfs as the daemon finds it, which opens nothing: the Astro A50 Gen 4
    (find_base), else the first Centurion one (find_each_a50x) whose noise suppression is on
    the computer, else the first; else the G432's. The two are bound here by name, where the
    CLI's tests stub them."""
    if args.headset is not None:
        return args.headset
    if (driven := driven_g432()) is not None:
        return driven.key
    if find_base() is not None:
        return pipewire_headsets.A50.key
    centurion = [a50x.MODELS[product].key for _path, product in find_each_a50x()]
    # One whose noise suppression is in the headset (OWN) is taken only with no other plugged in.
    served = [key for key in centurion if key not in headset_mic.OWN_SUPPRESSION]
    return (served or centurion or [headset_mic.G432_KEY])[0]


def cmd_headset_noise(args: argparse.Namespace) -> int:
    """The headset's noise suppression (headset_of), after on or off, then its state,
    waiting up to pipewire_client.SETTLE while it starts after on: switch_on's wait for a
    unit it started, else the report's own; then the notes the switch gave, of the default
    input. A headset with noise suppression of its own is refused (OWN), with nothing run;
    `test` is cmd_test's."""
    key = headset_of(args)
    if key in headset_mic.OWN_SUPPRESSION:
        print(f"error: {OWN.format(headset_mic.OWN_SUPPRESSION[key])}", file=sys.stderr)
        return 1
    if args.state == "test":
        return cmd_test(args, key)
    state, notes = None, []
    try:
        if args.state == "on":
            if (switched := headset_mic.switch_on(key=key)) is not None:
                state, notes = switched.state, list(switched.notes)
        elif args.state == "off":
            notes = headset_mic.switch_off(key=key)
    except (pipewire_client.StepError, OSError) as exc:
        return failed(exc, pipewire_unit.switch_folder)
    if state is None:
        state = pipewire_client.settled(lambda: headset_mic.read_state(key=key),
                                        lambda read: headset_mic.summary(read, key=key),
                                        args.state == "on")
    print_unit_state(asdict(state), headset_mic.summary(state, key=key),
                     headset_mic.describe(state, key=key), notes, args.json)
    return 0


class Lines:
    """The test's lines as they come: on a terminal each takes the place of the one before on
    a line of its own, padded over the rest of a longer one; else, as into a pipe or a file,
    each on a line of its own. `out` is None where Python started with stdout closed, and
    print, which says each line, then writes none."""

    def __init__(self, out: TextIO | None) -> None:
        self._out = out
        self._in_place = out is not None and out.isatty()
        #: The length of the line said in place, 0 while none is open.
        self._shown = 0

    def say(self, line: str) -> None:
        if self._in_place:
            # A carriage return takes a terminal's cursor back to the start of its line.
            print(f"\r{line.ljust(self._shown)}", end="", file=self._out, flush=True)
            self._shown = len(line)
        else:
            print(line, file=self._out, flush=True)

    def end(self) -> None:
        """The line said in place ended, so what comes next starts a line of its own."""
        if self._shown:
            print(file=self._out, flush=True)
            self._shown = 0


def cmd_test(args: argparse.Namespace, key: str) -> int:
    """The test, on a cleaned input that is ready (headset_mic.test_refusal): a count of
    COUNT_IN, then TEST_SECONDS of it recorded into memory, counting down, then played back to
    the default output, each line said as it comes (Lines); with --json, the count waited
    for silently, and the seconds recorded alone."""
    lines = Lines(sys.stdout)
    say = (lambda _line: None) if args.json else lines.say
    name = headset_mic.WORDS[key].description
    try:
        refusal = headset_mic.test_refusal(headset_mic.read_state(key=key), key=key)
        if refusal is not None:
            raise headset_mic.MicError(refusal)
        for count in range(headset_mic.COUNT_IN, 0, -1):
            say(COUNTING.format(name, count))
            time.sleep(headset_mic.COUNT_STEP)
        samples = headset_mic.record(lambda left: say(RECORDING.format(name, left)), key=key)
        say(PLAYING)
        headset_mic.play_back(samples)
    except pipewire_client.StepError as exc:
        lines.end()
        return failed(exc, pipewire_unit.switch_folder)
    except KeyboardInterrupt:
        # Ctrl+C ends the line said in place too, so the shell's prompt starts a line of its own.
        lines.end()
        raise
    seconds = len(samples) / headset_mic.BYTES_A_SECOND
    if args.json:
        print_json({"recorded": seconds})
    else:
        lines.say(TESTED.format(seconds))
        lines.end()
    return 0


def add_headset_noise(sub: Subcommands) -> None:
    noise = sub.add("headset-noise", aliases=("g432-noise",),
                    help="show or set a headset's noise suppression: a cleaned-up input made "
                         "from its microphone, the default input while it is on; no daemon "
                         "needed")
    noise.add_argument("--headset", choices=HEADSET_WORDS,
                       help="the headset whose microphone it cleans, by its key; left out, "
                            "a headset plugged in, picked in this order: the one of the "
                            "G432's family the daemon drives or would drive, the Astro A50 "
                            "Gen 4, then the others, found by their USB ids, one with its "
                            "noise suppression in the headset last; with none plugged in, "
                            "the G432")
    noise.add_argument("state", nargs="?", choices=["on", "off", "test"],
                       help="on: it runs until off or the end of the login session, and its "
                            "input becomes the default input; off: the default input you had "
                            "comes back, unless you picked another since, and it stops, which "
                            "removes the input; test: once it is on and linked, count "
                            f"{headset_mic.COUNT_IN}, record {headset_mic.TEST_SECONDS} s of "
                            "the input into memory and play them back on the default output")
    noise.set_defaults(func=cmd_headset_noise)
