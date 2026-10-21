"""The Logitech G432's subcommands: `g432-sidetone`, which asks the daemon, and `g432-eq`
and `g432-noise`, which run systemd's and PipeWire's programs in this process and need no
daemon.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import asdict, replace
from functools import partial

from .. import astro, eq_presets, g432, g432_eq, g432_mic, spatial
from ..config import ConfigError
from ..wording import escaped as _escaped, word_list
from .headset import ALONE, HEADSET_LABELS, failed, typed_name
from .output import Subcommands, labeled, print_json, print_reply, send

#: The width of `g432-noise`'s label column: "noise suppression: " is the longest.
NOISE_LABELS = 19


def _sidetone_level(text: str) -> int:
    """`g432-sidetone`'s level, or a printable ValueError (g432.sidetone_level's)."""
    try:
        value = int(text)
    except ValueError:
        value = None
    return g432.sidetone_level(value)


def cmd_g432_sidetone(args: argparse.Namespace) -> int:
    """The G432's sidetone, read from the headset, beside the firmware version read at its
    attach; or the sidetone set to a level, or reset to the level the daemon first read
    before any write (SIDETONE_UNREAD_RESET where none), then read back."""
    if args.level is None:
        request: dict = {"command": "g432", "read": True}
    elif args.level == "reset":
        request = {"command": "g432_sidetone", "reset": True}
    else:
        try:
            request = {"command": "g432_sidetone", "level": _sidetone_level(args.level)}
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    reply = send(request)
    if args.json or not reply.get("ok"):
        return print_reply(reply, args.json)
    line = partial(labeled, HEADSET_LABELS)
    line("device", f"{reply['device']}  {reply['name']}")
    line("firmware", reply.get("firmware") or "not read")
    first = reply["sidetone_first"]
    line("sidetone", f"{reply['sidetone']}%" if reply["sidetone"] is not None else "not read")
    line("first read", f"{first}%, which reset writes back" if first is not None
         else f"none before a write, so reset writes {g432.SIDETONE_UNREAD_RESET}%")
    return 0


def _eq_band(text: str) -> tuple[int, int]:
    """`--band`'s FREQ:GAIN, a band of g432_eq.BAND_FREQS and a whole dB, or a printable
    ValueError; the gain's range is g432_eq.checked's to refuse."""
    freq, colon, gain = text.partition(":")
    bands = word_list([str(b) for b in g432_eq.BAND_FREQS])
    try:
        pair = (int(freq), int(gain)) if colon else None
    except ValueError:
        pair = None
    if pair is None or pair[0] not in g432_eq.BAND_FREQS:
        raise ValueError(f"--band is FREQ:GAIN, FREQ one of {bands} Hz and GAIN whole dB, "
                         f"not {text!r}")
    return pair


def _eq_gains(args: argparse.Namespace, gains: g432_eq.Gains) -> g432_eq.Gains:
    """`gains` as `g432-eq`'s options change them: flat first with --flat, then each band,
    the bass and the treble given. A printable ValueError for one the equalizer cannot
    take."""
    gains = g432_eq.Gains() if args.flat else gains
    bands = list(gains.bands)
    for text in args.band:
        freq, gain = _eq_band(text)
        bands[g432_eq.BAND_FREQS.index(freq)] = gain
    return g432_eq.checked(replace(
        gains, bands=tuple(bands),
        bass=gains.bass if args.bass is None else args.bass,
        treble=gains.treble if args.treble is None else args.treble))


def _eq_change(args: argparse.Namespace,
               saved: g432_eq.Presets) -> Callable[[g432_eq.Presets], g432_eq.Presets]:
    """What `g432-eq`'s options make of the saved presets: --select first, then the others
    on --preset's preset, or the active one when left out. A printable ValueError for an
    option it cannot take, raised here on `saved`, the presets saved now, before anything
    runs."""
    name = None if args.name is None else typed_name(args.name)
    new = None if args.save_as is None else typed_name(args.save_as)

    def change(presets: g432_eq.Presets) -> g432_eq.Presets:
        if args.select is not None:
            presets = replace(presets, active=presets.number(args.select))
        number = presets.active if args.preset is None else presets.number(args.preset)
        if args.delete:
            return presets.deleted(number)
        if args.reset:
            presets = presets.reset(number)
        presets = presets.with_gains(number, _eq_gains(args, presets.get(number).gains))
        if name is not None:
            presets = presets.named(number, name)
        return presets if new is None else replace(presets, active=number).saved_as(new)
    change(saved)
    return change


def cmd_g432_eq(args: argparse.Namespace) -> int:
    """Logitech G432 Equalizer's state, after its presets' changes and then on or off, and
    the notes the switch gave. With off, or with a name alone, the changes are only
    saved; --preset alone only picks the preset the report shows."""
    gains = args.flat or args.band or args.bass is not None or args.treble is not None
    changes = args.select is not None or args.reset or gains or args.save_as is not None
    # What the chain plays can change; a name alone leaves it as it is.
    played = changes or args.delete
    try:
        presets = g432_eq.load_presets()
        if played or args.name is not None:
            # PresetsRefused, a ValueError, for a file that does not read or read in part.
            presets.whole()
        if args.preset is not None:
            presets.number(args.preset)
        if args.delete and (changes or args.name is not None):
            raise ValueError(ALONE)
        change = _eq_change(args, presets) if played or args.name is not None else None
    except ValueError as exc:
        print(f"error: {_escaped(exc)}", file=sys.stderr)
        return 1
    try:
        if change is not None and played and args.state != "off":
            g432_eq.update(change)
        elif change is not None:
            g432_eq.edit(change)
    # ConfigError: a version 1 g432-eq.json whose bytes could not go to the backups.
    except (spatial.SpatialError, ConfigError, OSError) as exc:
        return failed(exc, g432_eq.gains_path)
    notes: list[str] = []
    try:
        if args.state == "on":
            notes = g432_eq.switch_on()
        elif args.state == "off":
            notes = g432_eq.switch_off()
    except (spatial.SpatialError, OSError) as exc:
        return failed(exc, g432_eq.switch_folder)
    # A preset saved as new is the active one, which the report shows; a deleted one is gone.
    shown = args.preset if args.save_as is None and not args.delete else None
    return _eq_report(args.json, shown, notes, starting=args.state == "on")


def cmd_g432_noise(args: argparse.Namespace) -> int:
    """The G432's noise suppression, after on or off, then its state, waiting up to
    spatial.SETTLE while it starts after on: switch_on's wait for a unit it started, else
    the report's own."""
    state = None
    try:
        if args.state == "on":
            state = g432_mic.switch_on()
        elif args.state == "off":
            g432_mic.switch_off()
    except (spatial.SpatialError, OSError) as exc:
        return failed(exc, g432_eq.switch_folder)
    if state is None:
        state = spatial.settled(g432_mic.read_state, g432_mic.summary, args.state == "on")
    if args.json:
        kind, words = g432_mic.summary(state)
        print_json({**asdict(state), "kind": kind, "summary": words})
        return 0
    for label, value in g432_mic.describe(state):
        labeled(NOISE_LABELS, label, value)
    return 0


def _eq_report(as_json: bool, shown: int | None, notes: list[str], starting: bool) -> int:
    """The equalizer's report, after waiting up to spatial.SETTLE while it is `starting`:
    the state and preset `shown`, or the active one, then the notes; with `as_json` the
    state whole."""
    state = spatial.settled(g432_eq.read_state, g432_eq.summary, starting)
    if as_json:
        kind, words = g432_eq.summary(state)
        print_json({**asdict(state), "kind": kind, "summary": words, "notes": notes})
        return 0
    for label, value in g432_eq.describe(state, shown):
        labeled(HEADSET_LABELS, label, value)
    for note in notes:
        print(f"note: {_escaped(note)}")
    if state.presets.refused:
        print(f"note: {_escaped(state.presets.refused)}", file=sys.stderr)
    return 0


def add_g432(sub: Subcommands) -> None:
    """The G432's subcommands, in the order `gearbench-ctl --help` lists them."""
    low, high = g432.SIDETONE_RANGE
    gst = sub.add("g432-sidetone",
                  help=f"show or set the Logitech G432's sidetone, {g432.SIDETONE_MEANS}")
    gst.add_argument("level", nargs="?",
                     help=f"{low} to {high}, or reset: the level the daemon first read before "
                          f"any write, else {g432.SIDETONE_UNREAD_RESET}; left out, the level is "
                          "read and shown")
    gst.set_defaults(func=cmd_g432_sidetone)

    low, high = g432_eq.DB_RANGE
    geq = sub.add("g432-eq",
                  help=f"show or set {g432_eq.DESCRIPTION}, a separate output for the "
                       "Logitech G432: on or off, its presets and their gains; no daemon "
                       "needed")
    geq.add_argument("state", nargs="?", choices=["on", "off"],
                     help="on: it runs until off or the end of the login session; off: it "
                          "stops, which removes the output, and changes given with it are "
                          "only saved")
    shipped = word_list([str(number) for number in g432_eq.SHIPPED], "and")
    geq.add_argument("--select", type=int, metavar="N",
                     help=f"the preset it plays: {shipped}, the shipped ones, or one saved "
                          "as new after them")
    geq.add_argument("--preset", type=int, metavar="N",
                     help="the preset the other options change and the report shows; the "
                          "active one when left out")
    geq.add_argument("--name", help=f"the preset's name, {astro.NAME_BYTES[0]} to "
                                    f"{astro.NAME_BYTES[1]} bytes")
    geq.add_argument("--reset", action="store_true",
                     help=f"a shipped preset, {shipped}, back to its default, its name and "
                          "its gains, before the other options")
    geq.add_argument("--delete", action="store_true",
                     help="delete the preset, one saved as new, alone; those after it move up "
                          "a number, and with the active one deleted the one before it plays")
    geq.add_argument("--save-as", metavar="NAME",
                     help="after the other options, save the preset as a new one named "
                          f"NAME, which plays; at most {eq_presets.PRESETS_MAX} presets in all")
    geq.add_argument("--band", action="append", default=[], metavar="FREQ:GAIN",
                     help=f"a band's gain, FREQ one of "
                          f"{word_list([str(b) for b in g432_eq.BAND_FREQS], 'and')} Hz, GAIN "
                          f"whole dB from {low} to {high}; repeatable")
    geq.add_argument("--bass", type=int, metavar="GAIN",
                     help=f"the bass shelf's gain at {g432_eq.hertz(g432_eq.BASS_FREQ)}, "
                          f"{low} to {high} dB")
    geq.add_argument("--treble", type=int, metavar="GAIN",
                     help=f"the treble shelf's gain at "
                          f"{g432_eq.hertz(g432_eq.TREBLE_FREQ)}, {low} to {high} dB")
    geq.add_argument("--flat", action="store_true",
                     help="every gain of the preset to 0 dB first, before any other gain "
                          "given")
    geq.set_defaults(func=cmd_g432_eq)

    gns = sub.add("g432-noise",
                  help=f"show or set the Logitech G432's noise suppression: "
                       f"{g432_mic.DESCRIPTION}, a cleaned-up input made from its microphone, "
                       "to pick in a game or chat app; no daemon needed")
    gns.add_argument("state", nargs="?", choices=["on", "off"],
                     help="on: it runs until off or the end of the login session; off: it "
                          "stops, which removes the input")
    gns.set_defaults(func=cmd_g432_noise)
