"""The subcommands of the Logitech G432 and the other headsets of its family
(gearbench.g432.MODELS): `g432-sidetone`, `g432-auto-off`, `g432-headset-eq` and
`g432-lights` ask the daemon about the headset it has attached, `headset-sync` switches
whether every headset's lights follow the keyboard, attached or not, and
`g432-headset-presets` keeps the library of the attached headset's equalizer on this
computer (presets.run_library); `g432-eq` and `g432-noise` run systemd's and PipeWire's
programs in this process, with no daemon, on the headset `headset_of` picks.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path
from typing import cast

from .. import (
    astro,
    eq_presets,
    g432,
    g432_eq,
    g432_library,
    g432_mic,
    g432_presets,
    pipewire_client,
    pipewire_unit,
)
from ..battery import kept_at
from ..config import DEFAULT_COLOR, format_color
from ..g432 import G432_MODEL, MODEL_KEYED, MODEL_OF, HeadsetModel, find_g432
from ..g432_eq_words import EQ_MODELS
from ..headset_lights import FULL_BRIGHTNESS
from ..lighting import EFFECT_IDS, has_color
from ..report import untested_note
from ..wording import NOT_READ, escaped as _escaped, gain_range, hertz, route, word_list
from .output import (
    PUT_BACK,
    RELEASE_ALONE,
    Subcommands,
    bands_words,
    battery_words,
    device_words,
    failed,
    print_block,
    print_json,
    print_reply,
    send,
    silent_note,
)
from .presets import (
    ALONE,
    LIBRARY_EMPTY,
    LibraryKind,
    library_arguments,
    library_reply,
    run_library,
    typed_name,
)

#: g432-eq's refusal of a headset with an equalizer of its own, found plugged in: its name.
OWN_EQ = ("the {} has an equalizer of its own, which `gearbench-ctl g432-headset-eq` shows "
          "and sets; --headset picks a headset whose equalizer runs on the computer")
#: `g432-headset-presets`' refusal where the daemon read no equalizer in the headset, the
#: model's short name; and its list with none in the library yet.
EQ_NOT_READ = ("the {}'s equalizer has not been read, so there are no gains to save: read it "
               "with `gearbench-ctl g432-headset-eq`")
NONE_SAVED = LIBRARY_EMPTY.format("g432-headset-presets --save-as NAME saves the gains the "
                                  "headset plays")
#: g432-sidetone's words for writing back the level first read: PUT_BACK, and reset, its
#: name in 1.1.0, which still works.
PUT_BACK_WORDS = (PUT_BACK, "reset")
#: `g432-headset-eq`'s line for the preset picked, its name put in.
PICKED = "{}, sent again each time the headset connects"
#: `g432-lights`' refusal of --startup or --store with another change, before anything is
#: sent.
KEPT_ALONE = "--startup and --store take no effect, --color, --release or each other"
#: --headset's words, the command's own after "the headset".
HEADSET_HELP = ("the headset of the G432's family {}, by its key; left out, the one plugged "
                "in, found by its USB ids as the daemon finds it, else the G432")


def headset_of(args: argparse.Namespace) -> HeadsetModel:
    """The model `--headset` names, else the headset of the family found plugged in, read
    from sysfs (find_g432, which opens nothing), else the G432."""
    if args.headset is not None:
        return MODEL_KEYED[args.headset]
    found = find_g432()
    return G432_MODEL if found is None else MODEL_OF[found[1]]


def _checked(text: str, check: Callable[[object], int]) -> int:
    """`text` through `check`, one of g432's: as a whole number where it reads as one, else
    as typed, which check's printable ValueError quotes."""
    try:
        value: object = int(text)
    except ValueError:
        value = text
    return check(value)


def g432_device(reply: dict) -> str:
    """The `g432` reply's device line: the node, the model, and its route in the window's
    words, for a model this install knows."""
    model = MODEL_KEYED.get(str(reply.get("model")))
    return device_words(reply["device"], reply["name"],
                        None if model is None else route(model.plugged_by))


def cmd_g432_sidetone(args: argparse.Namespace) -> int:
    """The headset's sidetone, read from it, and its battery for one that has one, beside
    the firmware version read at its attach and the power-off time, or, where the read fails on
    one the daemon marks not answering, the last readings under its note (silent_note); or the
    sidetone set to a level, or put back to the level the daemon first read before any write
    (SIDETONE_UNREAD_RESET where none), the request's `reset`, then read back."""
    if args.level is None:
        request: dict = {"command": "g432", "read": True}
    elif args.level in PUT_BACK_WORDS:
        request = {"command": "g432_sidetone", "reset": True}
    else:
        try:
            request = {"command": "g432_sidetone",
                       "level": _checked(args.level, g432.sidetone_level)}
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    reply = send(request)
    asleep = False
    if args.level is None and not args.json and not reply.get("ok"):
        # A read that failed: the last readings of a headset the daemon marks not answering
        # (tend); one that lists no sidetone keeps the read's refusal.
        last = send({"command": "g432"})
        if last.get("ok") and last.get("answering") is False and last.get("sidetone_found"):
            reply, asleep = last, True
    if args.json or not reply.get("ok"):
        return print_reply(reply, args.json)
    lines: list[tuple[str | None, object]] = [("device", g432_device(reply))]
    if reply.get("untested"):
        lines.append((None, untested_note(reply["name"])))
    if asleep:
        # Under the device line.
        lines.append((None, silent_note(reply)))
    lines.append(("firmware", reply.get("firmware") or NOT_READ))
    if reply.get("battery_found"):
        battery = reply.get("battery")
        volts = f" ({battery['volts']} V)" if battery and battery.get("volts") else ""
        # Dated after a write too: only a battery reading marks the headset answering again.
        lines.append(("battery", battery_words(battery, kept_at(reply)) + volts))
        lines.append((g432.AUTO_OFF_LABEL, g432.auto_off_words(reply.get("power_off"))))
    first = reply["sidetone_first"]
    lines.append(("sidetone", f"{reply['sidetone']}%" if reply["sidetone"] is not None
                  else NOT_READ))
    lines.append(("first read", f"{first}%, which {PUT_BACK} writes back" if first is not None
                  else f"none before a write, so {PUT_BACK} writes "
                       f"{g432.SIDETONE_UNREAD_RESET}%"))
    # What each G key does now, on a model with G keys: its binding or its default.
    lines += (reply.get("g_keys") or {}).items()
    print_block(lines)
    return 0


def _auto_off_minutes(text: str) -> int:
    """`g432-auto-off`'s time: never, or a number of minutes g432.POWER_OFF_CHOICES offers;
    else a printable ValueError (g432.power_off_minutes's)."""
    if text.strip().lower() == "never":
        return 0
    return _checked(text, g432.power_off_minutes)


def cmd_g432_auto_off(args: argparse.Namespace) -> int:
    """The minutes of silence the headset powers off after: shown, or set and read back.
    Where the daemon refuses the writes the headset keeps (`flash_refused`), its reason is
    the writes: field (_writes_lines)."""
    if args.minutes is None:
        request: dict = {"command": "g432"}
    else:
        try:
            request = {"command": "g432_auto_off", "minutes": _auto_off_minutes(args.minutes)}
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    reply = send(request)
    if args.json or not reply.get("ok"):
        return print_reply(reply, args.json)
    print_block([("device", g432_device(reply)),
                 (g432.AUTO_OFF_LABEL, g432.auto_off_words(reply.get("power_off"))
                  if reply.get("battery_found") else "none: it lists no battery"),
                 *_writes_lines(reply.get("flash_refused"))])
    return 0


def _writes_lines(*refusals: object) -> list[tuple[str | None, object]]:
    """A report's writes: field, as `audio` gives it, one line for each refusal given, the
    later ones under the first; none where none is."""
    said = [f"refused: {why}" for why in refusals if why]
    return [("writes" if index == 0 else None, line) for index, line in enumerate(said)]


def _headset_gains(text: str) -> list[int]:
    """`--gains`' G1,G2,...: whole dB, or a printable ValueError; their count and range are
    the daemon's to refuse, which reads them off the headset."""
    try:
        return [int(part) for part in text.split(",")]
    except ValueError:
        raise ValueError(f"--gains takes whole dB split by commas, not {text!r}") from None


def cmd_g432_headset_eq(args: argparse.Namespace) -> int:
    """The equalizer in the headset of a model with one of its own: read again, or set to
    `--gains`, or with `--flat` to 0 dB on every band, as it plays once it switches on."""
    if args.flat:
        request: dict = {"command": "g432_headset_eq", "flat": True}
    elif args.gains is not None:
        try:
            request = {"command": "g432_headset_eq", "gains": _headset_gains(args.gains)}
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    else:
        request = {"command": "g432_headset_eq"}
    reply = send(request)
    if args.json or not reply.get("ok"):
        return print_reply(reply, args.json)
    _headset_eq_report(reply)
    return 0


def _headset_eq_report(reply: dict) -> None:
    """`g432-headset-eq`'s report from the daemon's `g432` reply: the device, each band
    with the gain the headset plays, the preset picked, and why a write is refused."""
    lines: list[tuple[str | None, object]] = [("device", g432_device(reply))]
    eq = reply["equalizer"]
    if not eq["frequencies"]:
        # A getEqInfo that counts no band: there are no gains to list either.
        lines.append(("bands", "none listed"))
    else:
        # A band and its gain together, as `audio` gives them; the daemon gives a gain for
        # each band it lists.
        lines.append(("playing", bands_words(zip(eq["frequencies"], eq["active"],
                                                 strict=True))))
    if eq.get("picked") is not None:
        lines.append(("picked", PICKED.format(eq["picked"])))
    print_block([*lines, *_writes_lines(eq["refused"])])


class HeadsetPresets:
    """`g432-headset-presets`' library, that of the equalizer in a headset with one of its
    own (presets.LibraryKind)."""

    command = "g432-headset-presets"
    none_saved = NONE_SAVED

    @staticmethod
    def attached(args: argparse.Namespace) -> tuple[dict, HeadsetModel] | None:
        """The daemon's `g432` reply and the model it names, one with an equalizer of its
        own; None once why there is none is said (presets.library_reply)."""
        attached = library_reply(args, lambda reply: MODEL_KEYED.get(str(reply.get("model"))),
                                 command="g432")
        if attached is not None and not attached[1].onboard_eq:
            print(f"error: {g432.NO_HEADSET_EQ.format(attached[1].short)}", file=sys.stderr)
            return None
        return attached

    @staticmethod
    def load(model: HeadsetModel) -> eq_presets.Saved[g432_library.Preset]:
        return g432_library.load_library(model)

    @staticmethod
    def edit(change: Callable[[eq_presets.Saved[g432_library.Preset]],
                              eq_presets.Saved[g432_library.Preset]],
             model: HeadsetModel) -> eq_presets.Saved[g432_library.Preset]:
        # Saved's changes give the library of the kind they change: a Library's, a Library.
        return g432_library.edit(
            cast("Callable[[g432_library.Library], g432_library.Library]", change), model)

    @staticmethod
    def path(model: HeadsetModel) -> Path:
        return g432_library.library_path(model)

    @staticmethod
    def saved_preset(_args: argparse.Namespace, reply: dict, model: HeadsetModel,
                     name: str) -> g432_library.Preset | None:
        """A preset named `name` of the gains the headset plays, as `reply` holds them; None
        once why it cannot be is said."""
        eq = reply.get("equalizer")
        if eq is None:
            print(f"error: {EQ_NOT_READ.format(model.short)}", file=sys.stderr)
            return None
        try:
            return g432_library.preset_of({"name": name, "bands": [
                {"freq": freq, "gain": gain}
                for freq, gain in zip(eq["frequencies"], eq["active"], strict=False)]})
        except ValueError as exc:
            print(f"error: {_escaped(exc)}", file=sys.stderr)
            return None

    @staticmethod
    def words(preset: g432_library.Preset) -> str:
        """The preset's name and each band's frequency and gain."""
        return f"{preset.name}: " + bands_words((band.freq, band.gain)
                                                for band in preset.bands)

    @staticmethod
    def entry(preset: g432_library.Preset) -> dict:
        return g432_library.entry(preset)

    @staticmethod
    def report(reply: dict) -> None:
        _headset_eq_report(reply)


HEADSET_PRESETS: LibraryKind[HeadsetModel, g432_library.Preset] = HeadsetPresets()


def cmd_g432_headset_presets(args: argparse.Namespace) -> int:
    """The library of the equalizer in the headset the daemon names (presets.run_library):
    with --pick, NUMBER's gains sent to the headset, kept by the daemon to send again
    (g432_library), and `g432-headset-eq`'s report in the list's place."""
    return run_library(args, HEADSET_PRESETS)


def cmd_g432_lights(args: argparse.Namespace) -> int:
    """The lights of a headset that lists them: shown, or why the attach could not read them
    (`lights_unread`), or an effect with its color sent to every zone, or a color alone for
    the effect shown, or with `--release`
    given back to the headset's own lighting, or with `--startup` their startup effect on or
    off, or with `--store` the lighting shown kept in the headset."""
    try:
        request = _lights_request(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    reply = send(request)
    if args.json or not reply.get("ok"):
        return print_reply(reply, args.json)
    _lights_report(reply)
    return 0


def _lights_request(args: argparse.Namespace) -> dict:
    """`g432-lights`' request: the change its options ask for, else `g432`, whose reply
    shows the lights; a ValueError for options that do not go together, before anything is
    sent."""
    kept = (args.startup is not None) + args.store
    if kept and kept + (args.effect is not None) + (args.color is not None) + args.release > 1:
        raise ValueError(KEPT_ALONE)
    if args.release and (args.effect is not None or args.color is not None):
        raise ValueError(RELEASE_ALONE)
    if args.startup is not None:
        return {"command": "g432_lights", "startup": args.startup == "on"}
    if args.store:
        return {"command": "g432_lights", "store": True}
    if args.release:
        return {"command": "g432_lights", "release": True}
    # Either alone, the daemon takes the other from the lighting shown.
    given = {key: value for key, value in (("effect", args.effect), ("color", args.color))
             if value is not None}
    return {"command": "g432_lights", **given} if given else {"command": "g432"}


def _lights_report(reply: dict) -> None:
    """`g432-lights`' report on an ok reply, or _no_lights_report's where it has no lights."""
    lights = reply.get("lights")
    if lights is None:
        _no_lights_report(reply)
        return
    lines: list[tuple[str | None, object]] = [
        ("device", g432_device(reply)),
        ("zones", word_list(lights["zones"], "and")),
        # Empty where the zones list no effect they all take.
        ("effects", word_list(lights["effects"]) if lights["effects"]
         else "none every zone takes"),
        ("showing", _showing_words(lights))]
    # Left out where the lights list no startup effect, and where the daemon stored none.
    startup, stored = lights.get("startup"), lights.get("stored")
    if startup is not None:
        lines.append(("startup", "on" if startup else "off"))
    if stored:
        lines.append(("stored", _shown_words(stored)))
    print_block([*lines, *_writes_lines(
        lights["refused"], reply.get("flash_refused") if startup is not None else None)])


def _no_lights_report(reply: dict) -> None:
    """`g432-lights`' report on a reply with no lights: none listed, or not read, with why
    under note: where the attach could not read them (`lights_unread`)."""
    unread = reply.get("lights_unread")
    print_block([("device", g432_device(reply)),
                 ("lights", "none listed" if unread is None else NOT_READ)])
    if unread is not None:
        print(f"note: {_escaped(unread)}")


def _showing_words(lights: dict) -> str:
    """What the lights show: the keyboard's color while they follow it (`headset-sync`) on
    lights whose writes are not refused, since sync sends refused ones nothing; else
    _shown_words, off where a G key turned them off, and the brightness a G key set where it
    is not full."""
    if lights.get("synced") and not lights.get("refused"):
        return "follows the keyboard that leads"
    brightness = lights.get("brightness")
    if brightness == 0:
        return "off"
    words = _shown_words(lights["shown"])
    return words if brightness in (None, FULL_BRIGHTNESS) else f"{words}, at {brightness}%"


def _shown_words(shown: dict | None) -> str:
    """What the lights show, or what the daemon stored in them: the headset's own lighting,
    or an effect with its color, none named for an effect whose layout has none
    (lighting.has_color), as `mouse`'s color line leaves it out."""
    if not shown:
        return "its own lighting"
    effect = shown["effect"]
    if effect in EFFECT_IDS and not has_color(effect):
        return effect
    return f"{effect}, {shown['color']}"


def _eq_band(text: str) -> tuple[int, int]:
    """`--band`'s FREQ:GAIN, a band of g432_presets.BAND_FREQS and a whole dB, or a printable
    ValueError; the gain's range is g432_presets.checked's to refuse."""
    freq, _, gain = text.partition(":")
    bands = word_list([str(b) for b in g432_presets.BAND_FREQS])
    try:
        pair = (int(freq), int(gain))
    except ValueError:
        pair = None
    if pair is None or pair[0] not in g432_presets.BAND_FREQS:
        raise ValueError(f"--band is FREQ:GAIN, FREQ one of {bands} Hz and GAIN whole dB, "
                         f"not {text!r}")
    return pair


def _eq_gains(args: argparse.Namespace, gains: g432_presets.Gains) -> g432_presets.Gains:
    """`gains` as `g432-eq`'s options change them: flat first with --flat, then each band,
    the bass and the treble given. A printable ValueError for one the equalizer cannot
    take."""
    gains = g432_presets.Gains() if args.flat else gains
    bands = list(gains.bands)
    for text in args.band:
        freq, gain = _eq_band(text)
        bands[g432_presets.BAND_FREQS.index(freq)] = gain
    return g432_presets.checked(replace(
        gains, bands=tuple(bands),
        bass=gains.bass if args.bass is None else args.bass,
        treble=gains.treble if args.treble is None else args.treble))


def _eq_change(args: argparse.Namespace) -> Callable[[g432_presets.Presets], g432_presets.Presets]:
    """What `g432-eq`'s options make of the saved presets: --pick first, then the others
    on --preset's preset, or the active one when left out. A printable ValueError for a name
    it cannot take, raised here; the change raises one for another option."""
    name = None if args.name is None else typed_name(args.name)
    new = None if args.save_as is None else typed_name(args.save_as)

    def change(presets: g432_presets.Presets) -> g432_presets.Presets:
        if args.pick is not None:
            presets = replace(presets, active=presets.number(args.pick))
        number = presets.active if args.preset is None else presets.number(args.preset)
        if args.delete:
            return presets.deleted(number)
        if args.reset:
            presets = presets.reset(number)
        presets = presets.with_gains(number, _eq_gains(args, presets.get(number).gains))
        if name is not None:
            presets = presets.named(number, name)
        return presets if new is None else replace(presets, active=number).saved_as(new)
    return change


def cmd_g432_eq(args: argparse.Namespace) -> int:
    """The headset's equalizer's state (headset_of), Logitech G432 Equalizer's on the G432,
    after its presets' changes and then on or off, and the notes the switch gave. With off,
    or with a name alone, the changes are only saved; --preset alone only chooses the preset
    the report shows. A headset found with an equalizer of its own is refused (OWN_EQ)."""
    model = headset_of(args)
    if model.onboard_eq:
        print(f"error: {OWN_EQ.format(model.short)}", file=sys.stderr)
        return 1
    gains = args.flat or args.band or args.bass is not None or args.treble is not None
    changes = args.pick is not None or args.reset or gains or args.save_as is not None
    # What the chain plays can change; a name alone leaves it as it is.
    played = changes or args.delete
    try:
        presets = g432_presets.load_presets(model=model)
        if played or args.name is not None:
            # PresetsRefused, a ValueError, for a file that does not read or read in part.
            presets.whole()
        if args.preset is not None:
            presets.number(args.preset)
        if args.delete and (changes or args.name is not None):
            raise ValueError(ALONE)
        change = _eq_change(args) if played or args.name is not None else None
    except ValueError as exc:
        print(f"error: {_escaped(exc)}", file=sys.stderr)
        return 1
    try:
        if change is not None and played and args.state != "off":
            g432_eq.update(change, model=model)
        elif change is not None:
            g432_presets.edit(change, model=model)
    # ValueError: the presets edit reads refuse the change or the save, a version 1
    # g432-eq.json whose bytes could not go to the backups among them (a ConfigError).
    # Nothing is saved then.
    except (pipewire_client.StepError, ValueError, OSError) as exc:
        return failed(exc, lambda: g432_presets.gains_path(model=model))
    notes: list[str] = []
    try:
        if args.state == "on":
            notes = g432_eq.switch_on(model=model)
        elif args.state == "off":
            notes = g432_eq.switch_off(model=model)
    except (pipewire_client.StepError, OSError) as exc:
        return failed(exc, pipewire_unit.switch_folder)
    # A preset saved as new is the active one, which the report shows; a deleted one is gone.
    shown = args.preset if args.save_as is None and not args.delete else None
    return _eq_report(args.json, shown, notes, starting=args.state == "on", model=model)


def cmd_g432_noise(args: argparse.Namespace) -> int:
    """The headset's noise suppression (headset_of), after on or off, then its state,
    waiting up to pipewire_client.SETTLE while it starts after on: switch_on's wait for a
    unit it started, else the report's own."""
    model = headset_of(args)
    state = None
    try:
        if args.state == "on":
            state = g432_mic.switch_on(model=model)
        elif args.state == "off":
            g432_mic.switch_off(model=model)
    except (pipewire_client.StepError, OSError) as exc:
        return failed(exc, pipewire_unit.switch_folder)
    if state is None:
        state = pipewire_client.settled(lambda: g432_mic.read_state(model=model),
                                        lambda read: g432_mic.summary(read, model=model),
                                        args.state == "on")
    if args.json:
        kind, words = g432_mic.summary(state, model=model)
        print_json({**asdict(state), "kind": kind, "summary": words})
        return 0
    print_block(g432_mic.describe(state, model=model))
    return 0


def _eq_report(as_json: bool, shown: int | None, notes: list[str], starting: bool, *,
               model: HeadsetModel) -> int:
    """The equalizer's report, after waiting up to pipewire_client.SETTLE while it is
    `starting`: the state and preset `shown`, or the active one, then the notes; with
    `as_json` the state whole."""
    state = pipewire_client.settled(lambda: g432_eq.read_state(model=model),
                                    lambda read: g432_eq.summary(read, model=model), starting)
    if as_json:
        kind, words = g432_eq.summary(state, model=model)
        print_json({**asdict(state), "kind": kind, "summary": words, "notes": notes})
        return 0
    print_block(g432_eq.describe(state, shown, model=model))
    for note in notes:
        print(f"note: {_escaped(note)}")
    if state.presets.refused:
        # A problem on stderr is a warning, as report's; note: is stdout's label.
        print(f"warning: {_escaped(state.presets.refused)}", file=sys.stderr)
    return 0


def add_g432(sub: Subcommands) -> None:
    """The G432's subcommands, in the order `gearbench-ctl --help` lists them: those that
    ask the daemon, then those that run on the computer."""
    _add_daemon_commands(sub)
    _add_computer_commands(sub)


def _add_daemon_commands(sub: Subcommands) -> None:
    low, high = g432.SIDETONE_RANGE
    gst = sub.add("g432-sidetone",
                  help=f"show or set the sidetone of the Logitech G432, or of another HID++ "
                       f"headset of its family, {g432.SIDETONE_MEANS}, and show the battery "
                       "of one that has one")
    gst.add_argument("level", nargs="?",
                     help=f"{low} to {high}, or {PUT_BACK} (reset, its name in 1.1.0): the "
                          "level the daemon first read before any write, else "
                          f"{g432.SIDETONE_UNREAD_RESET}; left out, the level is read and "
                          "shown")
    gst.set_defaults(func=cmd_g432_sidetone)

    gao = sub.add("g432-auto-off",
                  help="show or set the minutes of silence a wireless headset of the G432's "
                       "family powers off after, which it keeps")
    gao.add_argument("minutes", nargs="?",
                     help=f"never, or {g432.POWER_OFF_OFFERED}; left out, the time is shown")
    gao.set_defaults(func=cmd_g432_auto_off)

    own = word_list([model.short for model in g432.MODELS if model.onboard_eq])
    ghe = sub.add("g432-headset-eq",
                  help=f"show or set the equalizer in the headset of a {own}, which plays "
                       "until the headset powers off")
    change = ghe.add_mutually_exclusive_group()
    change.add_argument("--gains", metavar="G1,G2,...",
                        help="a whole dB for each of the headset's bands, low to high, in "
                             "its range")
    change.add_argument("--flat", action="store_true",
                        help="every band to 0 dB, as the headset plays once it switches on, "
                             "and again each time it connects, in place of a preset")
    ghe.set_defaults(func=cmd_g432_headset_eq)

    ghp = sub.add("g432-headset-presets",
                  help=f"show or change the preset library Gearbench keeps on this computer "
                       f"for the equalizer in a {own}, or pick one to send to the headset")
    ghp.add_argument("--save-as", metavar="NAME",
                     help="save the gains the headset plays now as a new preset named NAME; "
                          f"at most {eq_presets.PRESETS_MAX} presets in all")
    library_arguments(ghp, picks="send its gains to the headset, and again each time it "
                                 "connects until g432-headset-eq --flat")
    ghp.set_defaults(func=cmd_g432_headset_presets)

    gli = sub.add("g432-lights",
                  help="show or set the lights of a headset of the G432's family that has "
                       "them: one effect and color on every zone, held until the headset "
                       "powers off or --release gives them back")
    gli.add_argument("effect", nargs="?",
                     help="one of the effects g432-lights alone lists, such as static or "
                          "breathe")
    gli.add_argument("--color", metavar="#RRGGBB",
                     help="the color for an effect that takes one, or alone a new color for "
                          "the effect shown; when left out, the color last shown, or "
                          f"{format_color(DEFAULT_COLOR)} where none was")
    gli.add_argument("--release", action="store_true",
                     help="give the lights back to the headset's own lighting")
    gli.add_argument("--startup", choices=["on", "off"],
                     help="the effect the lights show as the headset switches on, which it "
                          "keeps")
    gli.add_argument("--store", action="store_true",
                     help="keep the effect and color shown now in the headset, which shows "
                          "them without Gearbench, after a power-off too")
    gli.set_defaults(func=cmd_g432_lights)

    sub.switch("headset-sync", command="headset_sync", state="headset_sync_color",
               help="on: a headset's lights take the keyboard's color (under Per-key, the "
                    "background) and dim with it, and g432-lights refuses an effect, "
                    "--release and --store; off: they show what they showed before")


def _add_computer_commands(sub: Subcommands) -> None:
    low, high = g432_presets.DB_RANGE
    geq = sub.add("g432-eq",
                  help="show or set the equalizer on this computer for the Logitech G432, or "
                       "for another headset of its family with none of its own: a separate "
                       "output, on or off, its presets and their gains; no daemon needed")
    geq.add_argument("--headset", choices=[model.key for model in EQ_MODELS],
                     help=HEADSET_HELP.format("whose equalizer it shows or sets"))
    geq.add_argument("state", nargs="?", choices=["on", "off"],
                     help="on: it runs until off or the end of the login session; off: it "
                          "stops, which removes the output, and changes given with it are "
                          "only saved")
    shipped = word_list([str(number) for number in g432_presets.SHIPPED], "and")
    # --select, its name in 1.1.0, still works.
    geq.add_argument("--pick", "--select", dest="pick", type=int, metavar="N",
                     help=f"the active preset: {shipped}, the shipped ones, or one saved "
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
                          f"{word_list([str(b) for b in g432_presets.BAND_FREQS], 'and')} Hz, GAIN "
                          f"a whole number of dB, {gain_range(low, high)}; repeatable")
    geq.add_argument("--bass", type=int, metavar="GAIN",
                     help=f"the bass shelf's gain at {hertz(g432_presets.BASS_FREQ)}, "
                          f"{gain_range(low, high)}")
    geq.add_argument("--treble", type=int, metavar="GAIN",
                     help=f"the treble shelf's gain at "
                          f"{hertz(g432_presets.TREBLE_FREQ)}, {gain_range(low, high)}")
    geq.add_argument("--flat", action="store_true",
                     help="every gain of the preset to 0 dB first, before any other gain "
                          "given")
    geq.set_defaults(func=cmd_g432_eq)

    gns = sub.add("g432-noise",
                  help="show or set the noise suppression of the Logitech G432, or of another "
                       "headset of its family: a cleaned-up input made from its microphone, to "
                       "pick in a game or chat app; no daemon needed")
    gns.add_argument("--headset", choices=[model.key for model in g432.MODELS],
                     help=HEADSET_HELP.format("whose microphone it cleans"))
    gns.add_argument("state", nargs="?", choices=["on", "off"],
                     help="on: it runs until off or the end of the login session; off: it "
                          "stops, which removes the input")
    gns.set_defaults(func=cmd_g432_noise)
