"""The Astro A50's subcommands: `headset` and its report, `headset-set`, `headset-eq`,
`headset-save` and `headset-revert`, which ask the daemon, `headset-import`, which asks it
unless given `--library`, `headset-library`, the preset library Gearbench keeps, which asks
it only to read a slot or write one, and `headset-spatial`, the spatial output of both
headsets, which runs systemctl, pw-dump and pw-cli in this process and needs no daemon. The
G432's are in `g432`.
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from collections.abc import Callable
from dataclasses import asdict, replace
from functools import partial
from pathlib import Path

from .. import a50_library, astro, astroeq, eq_presets, spatial
from ..wording import clock_shown, escaped as _escaped, reason, word_list
from .output import Subcommands, battery_words, continued, labeled, print_json, print_reply, send

#: The width of `headset`'s label column: "default-balance: " is the longest.
HEADSET_LABELS = 17
#: What a change to the base station's memory says while it is not saved.
SAVE_HINT = "Save to the base station with: gearbench-ctl headset-save"
#: Why --delete is refused with another change.
ALONE = "--delete takes no other change of the preset"
#: Why a command that needs the active slot cannot go on, with the option that names a slot.
UNKNOWN_ACTIVE = "the base station has not said which slot is active; give {}"
NO_ACTIVE = UNKNOWN_ACTIVE.format("--preset")
#: Why a slot cannot be taken: the daemon holds only part of it.
NOT_READ = "{} has not been read whole from the base station"
#: What reads the rest, where --read can: it asks nothing while the base station is not
#: answering or saving.
READ_HINT = "gearbench-ctl headset --read reads it"
#: Why a slot the daemon holds whole cannot be taken, with what of it a library preset
#: checks: NAMED_PARTS, or SOUND_PARTS where the preset takes a name of its own.
NOT_TAKEN = "{} holds a {} a library preset cannot take; gearbench-ctl headset shows it"
NAMED_PARTS = "name, gain or band"
SOUND_PARTS = "gain or band"


def _setting_word(setting: str) -> str:
    """A socket setting as the command line spells it: mic_level is mic-level."""
    return setting.replace("_", "-")


#: What `headset-set` takes: every setting, and the active EQ preset.
HEADSET_SETTINGS = (*map(_setting_word, astro.SETTINGS), "preset")
#: The bands that take a width, as a list in words.
PEAK_BANDS = word_list([str(b) for b in astro.BANDS if b not in astro.SHELVES], "and")


def _headset_ranges() -> str:
    """What each of `headset-set`'s settings takes, for its help."""
    levels = [_setting_word(s) for s, span in astro.SETTING_RANGES.items()
              if span == astro.LEVEL_RANGE]
    level, balance = astro.LEVEL_RANGE, astro.SETTING_RANGES["default_balance"]
    return "; ".join((
        f"{word_list(levels, 'and')} {level[0]} to {level[1]}",
        *(f"{_setting_word(name)} {word_list(words)}"
          for name, words in astro.SETTING_WORDS.items()),
        f"default-balance {balance[0]} (all Voice) to {balance[1]} (all Game)",
        f"auto-off {word_list([str(m) for m in astro.AUTO_OFF_MINUTES])} minutes",
        f"preset {word_list([str(p) for p in astro.PRESETS])}",
    ))


def _headset_value(setting: str, text: str) -> int | str:
    """`text` as `setting`'s value, or a printable ValueError; the base station clamps nothing."""
    shown = _setting_word(setting)
    words = astro.SETTING_WORDS.get(setting)
    if words is not None:
        if text.lower() not in words:
            raise ValueError(f"{shown} is {word_list(words)}, not {text!r}")
        return text.lower()
    try:
        value = int(text)
    except ValueError:
        value = None
    choices = astro.PRESETS if setting == "preset" else astro.SETTING_CHOICES.get(setting)
    if choices is not None:
        if value is None or value not in choices:
            unit = " minutes" if setting == "auto_off" else ""
            raise ValueError(f"{shown} is {word_list([str(c) for c in choices])}{unit}, "
                             f"not {text!r}")
        return value
    low, high = astro.SETTING_RANGES[setting]
    if value is None or not low <= value <= high:
        raise ValueError(f"{shown} is a whole number from {low} to {high}, not {text!r}")
    return value


def _balance_words(value: int) -> str:
    """A Game/Voice mix, 0 all Voice to 255 all Game, as the two shares."""
    voice, game = astro.balance_shares(value)
    return f"Voice {voice}% / Game {game}%"


def _headset_value_words(setting: str, value: object) -> str:
    if setting == "auto_off":
        return f"{value} minutes"
    if setting == "default_balance" and isinstance(value, int):
        return f"{value}, {_balance_words(value)}"
    return str(value)


def _saved_words(entry: dict, words: Callable[[object], str] = str) -> str:
    """What the base station runs, and what its flash holds where that differs."""
    active = words(entry["active"])
    if "saved" in entry and entry["saved"] != entry["active"]:
        return f"{active} (saved: {words(entry['saved'])})"
    return active


def _band_words(gain: int, band: dict) -> str:
    """One EQ band: gain, frequency, and width or "shelf" for a band with none."""
    width = band["width"]
    return (f"{f'{gain:+d}' if gain else '0'} dB".rjust(5) + f"  {band['freq']:>5} Hz  "
            + ("shelf" if width is None else f"width {width:.1f}"))


def _preset_lines(preset: dict) -> list[str]:
    """One EQ preset: its name, then a line for each band, with flash's value where it
    differs."""
    lines = [_saved_words(preset["name"])]
    gains = preset["gains"]
    for gain, saved_gain, band in zip(gains["active"], gains["saved"], preset["bands"],
                                      strict=True):
        said = _band_words(gain, band["active"])
        if (saved_gain, band["saved"]) != (gain, band["active"]):
            said += f"  (saved: {_band_words(saved_gain, band['saved']).strip()})"
        lines.append(f"band {band['band']}  {said}")
    return lines


def _print_preset(preset: dict) -> None:
    head, *bands = _preset_lines(preset)
    labeled(HEADSET_LABELS, f"preset {preset['preset']}", head)
    for band in bands:
        continued(HEADSET_LABELS, band)


def _active_preset_words(reply: dict) -> str | None:
    """The active preset's number, and its name when it was read."""
    active = reply["preset"]
    if active is None:
        return None
    for preset in reply["presets"]:
        if preset["preset"] == active and preset["name"]["active"]:
            return f"{active}, {preset['name']['active']}"
    return str(active)


def _headset_state_words(reply: dict) -> str:
    """The `headset:` line: on, off or out of range, or docked and on or switched off;
    or the base not answering."""
    if not reply["answering"]:
        read = reply["read_at"]
        return "the base station is not answering; " + (
            "the settings have not all been read yet" if read is None
            else f"the readings below are from {clock_shown(read)}")
    docked = reply["docked"]
    # Off its base station, a headset with no link may be on and out of range.
    return ("on" if reply["linked"] else "switched off" if docked
            else "off or out of range") + (", docked" if docked else "")


def _unsaved_words(keys: list) -> str:
    """The reply's `unsaved` keys as the command line names them."""
    return ", ".join(key.replace("_", " ") if key.startswith("preset_") else _setting_word(key)
                     for key in keys)


def _headset_unsaved_words(reply: dict) -> str:
    """The `unsaved:` line: the settings flash does not hold, or "none", and before the
    first full read that the rest is not read yet."""
    said = _unsaved_words(reply["unsaved"])
    if reply["read_at"] is None:
        # Before the first full read, an empty list says nothing about flash. --read
        # asks nothing while the base is not answering or saving.
        said = ((f"{said}, and the rest " if said else "") + "not read yet"
                + ("" if not reply["answering"] or reply["saving"]
                   else f"; {READ_HINT}"))
    return said or "none"


def _save_hint(reply: dict, key: str | None = None) -> None:
    """Point at headset-save while `key`, or anything, is not saved to flash, unless the
    base station would refuse a save: while it saves or is not answering."""
    unsaved = reply["unsaved"]
    if not reply["saving"] and reply["answering"] and (key in unsaved if key else unsaved):
        print(SAVE_HINT, file=sys.stderr)


def cmd_headset(args: argparse.Namespace) -> int:
    """The headset's state as a report; `--json` prints the reply whole."""
    request = {"command": "headset", "read": args.read}
    reply = send(request, timeout=astro.SLOW_REQUEST_WAIT) if args.read else send(request)
    if args.json or not reply.get("ok"):
        return print_reply(reply, args.json)
    _print_state(reply)
    _print_settings(reply)
    _print_unsaved(reply)
    return 0


def _print_state(reply: dict) -> None:
    """The head of `gearbench-ctl headset`: the device, the headset on or off, the
    versions, the battery and the Game/Voice balance."""
    line = partial(labeled, HEADSET_LABELS)
    line("device", f"{reply['device']}  {reply['name']}")
    line("headset", _headset_state_words(reply))
    versions = reply["versions"]
    line("versions", ", ".join(f"{part} {versions[part]}" for part in ("base", "headset")
                               if part in versions))
    battery = reply["battery"]
    if battery is not None:
        # While the headset is off, 7C gives the last reading the base heard; a silent
        # base's was read at battery_at, not read_at. The Headset tab dates it the same way.
        stale = (f", as of {clock_shown(reply['battery_at'])}"
                 if not reply["linked"] or not reply["answering"] else "")
        line("battery", battery_words(battery) + stale)
    if reply["balance"] is not None:
        line("balance", _balance_words(reply["balance"]))


def _print_settings(reply: dict) -> None:
    """The settings of `gearbench-ctl headset`, each with flash's value where that
    differs, then the active EQ preset and every preset."""
    line = partial(labeled, HEADSET_LABELS)
    settings = reply["settings"]
    for setting in astro.SETTINGS:
        if setting in settings:
            line(_setting_word(setting),
                 _saved_words(settings[setting], partial(_headset_value_words, setting)))
    active = _active_preset_words(reply)
    if active:
        line("preset", active)
    for preset in reply["presets"]:
        _print_preset(preset)


def _print_unsaved(reply: dict) -> None:
    """The end of `gearbench-ctl headset`: what flash does not hold, a save running or
    failed, and the hint to save."""
    line = partial(labeled, HEADSET_LABELS)
    line("unsaved", _headset_unsaved_words(reply))
    if reply["saving"]:
        line("save", "saving to the base station's flash now")
    elif reply["save_error"]:
        line("save", f"failed: {reply['save_error']}")
    _save_hint(reply)


def cmd_headset_set(args: argparse.Namespace) -> int:
    setting = args.setting.replace("-", "_")
    try:
        value = _headset_value(setting, args.value)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    reply = send({"command": "headset_set", "setting": setting, "value": value})
    if args.json or not reply.get("ok"):
        return print_reply(reply, args.json)
    if setting == "preset":
        print(f"preset: {_escaped(_active_preset_words(reply) or value)}")
        return 0
    entry = reply["settings"].get(setting)
    print(f"{args.setting}: " + (_saved_words(entry, partial(_headset_value_words, setting))
                                  if entry is not None
                                  else _headset_value_words(setting, value)))
    _save_hint(reply, setting)
    return 0


def typed_name(name: str) -> str:
    # argv holds a byte that is not UTF-8 as a lone surrogate, which encode() raises on.
    if any(unicodedata.category(c) == "Cs" for c in name):
        raise ValueError("a preset name cannot hold a byte that is not UTF-8")
    # astro.preset_name: the daemon's rule and both preset files', so a name is refused
    # before anything is sent or saved.
    astro.preset_name(name, "A preset name")
    return name


def _gains(text: str) -> list[int]:
    try:
        gains = [int(g) for g in text.replace(" ", "").split(",")]
    except ValueError:
        raise ValueError(f"--gains is {len(astro.BANDS)} whole numbers, comma separated, "
                         f"like 0,2,0,-1,3; not {text!r}") from None
    astro.preset_gains(gains)
    return gains


def _band(text: str) -> dict:
    """--band's BAND:FREQ or BAND:FREQ:WIDTH as one of headset_eq's bands."""
    parts = text.split(":")
    try:
        number, freq = int(parts[0]), int(parts[1])
        width = float(parts[2]) if len(parts) == 3 else None
    except (ValueError, IndexError):
        parts = []
    if not 2 <= len(parts) <= 3:
        raise ValueError(f"--band is BAND:FREQ, and BAND:FREQ:WIDTH for bands "
                         f"{PEAK_BANDS}, like 3:2750:1.5; not {text!r}")
    narrow, wide = astro.WIDTH_RANGE
    # What to type for a peak band given none, which the daemon's sentence cannot say.
    if number in astro.BANDS and number not in astro.SHELVES and width is None:
        raise ValueError(f"band {number} needs a width, {narrow:.1f} to {wide:.1f}: "
                         f"{number}:{freq}:WIDTH")
    # The daemon's rule for the band, its frequency and its width.
    astro.band_width(number, freq, width)
    return {"band": number, "freq": freq, "width": width}


def _eq_reply(request: dict, slot: int | None) -> dict:
    """The daemon's reply to a headset_eq on `slot`, the active one for None."""
    if slot is not None:
        request["preset"] = slot
    return send(request, timeout=astro.SLOW_REQUEST_WAIT)


def _send_eq(args: argparse.Namespace, request: dict, slot: int | None) -> int:
    """Send a headset_eq (_eq_reply) and show the changed preset as the base station read it
    back."""
    reply = _eq_reply(request, slot)
    if args.json or not reply.get("ok"):
        return print_reply(reply, args.json)
    _print_slot(reply, request.get("preset", reply["preset"]))
    return 0


def _print_slot(reply: dict, number: int | None) -> None:
    """Slot `number` of the `headset` reply as the base station read it back, and the hint
    to save it."""
    preset = next((p for p in reply["presets"] if p["preset"] == number), None)
    if preset is None:
        print("ok")
        return
    _print_preset(preset)
    _save_hint(reply, f"preset_{number}")


def _eq_request(args: argparse.Namespace) -> dict:
    """`headset-eq`'s headset_eq: its name, gains and bands as given. A printable ValueError
    for one the base station would refuse."""
    request: dict = {"command": "headset_eq"}
    if args.name is not None:
        request["name"] = typed_name(args.name)
    if args.gains is not None:
        request["gains"] = _gains(args.gains)
    if args.band:
        request["bands"] = _band_list(args.band)
    return request


def _band_list(texts: list[str]) -> list[dict]:
    """`--band`'s bands as headset_eq takes them, each band once; a printable ValueError."""
    bands = [_band(text) for text in texts]
    numbers = [band["band"] for band in bands]
    repeated = next((n for n in numbers if numbers.count(n) > 1), None)
    if repeated is not None:
        raise ValueError(f"band {repeated} is given more than once")
    return bands


def cmd_headset_eq(args: argparse.Namespace) -> int:
    """A slot's changes, sent to the base station, then with --save-as the slot saved into
    the library. Everything given is checked first, the library's file read among it; with
    --reset and no --preset the daemon is asked which slot is active, for its default."""
    if (args.name is None and args.gains is None and not args.band and args.save_as is None
            and not args.reset):
        print("error: give --name, --gains, --band, --reset or --save-as, or more than one",
              file=sys.stderr)
        return 1
    try:
        request = _eq_request(args)
        # --save-as with no name takes the slot's own.
        name = typed_name(args.save_as) if args.save_as else None
        if args.save_as is not None:
            a50_library.load_library().whole().has_room()
    except ValueError as exc:
        print(f"error: {_escaped(exc)}", file=sys.stderr)
        return 1
    slot = args.preset
    if args.reset:
        if slot is None:
            reply = send({"command": "headset"})
            if not reply.get("ok"):
                return print_reply(reply, args.json)
            if (slot := reply["preset"]) is None:
                print(f"error: {NO_ACTIVE}", file=sys.stderr)
                return 1
        request = _over_default(a50_library.default_slot(slot), request)
    if args.save_as is None:
        return _send_eq(args, request, slot)
    changed = len(request) > 1
    reply = _eq_reply(request, slot) if changed else send({"command": "headset"})
    if not reply.get("ok"):
        return print_reply(reply, args.json)
    return _save_slot_as(args, reply, name, changed, slot)


def _over_default(default: astro.EqPreset, request: dict) -> dict:
    """`--reset`'s headset_eq: the slot's default, its name, gains and all five bands, with
    the name, the gains and each band `request` gives over it."""
    reset = default.request()
    given = {band["band"]: band for band in request.get("bands", [])}
    return {**reset, **request,
            "bands": [given.get(band["band"], band) for band in reset["bands"]]}


def _save_slot_as(args: argparse.Namespace, reply: dict, name: str | None,
                  changed: bool, slot: int | None) -> int:
    """`headset-eq --save-as`: the slot, `slot` or the active one, as the `headset` reply
    gives it, shown when `changed`, then saved into the library under `name`, or its own
    name for None, and the library preset it made shown."""
    number = reply["preset"] if slot is None else slot
    # The slot first: it is changed whether or not the library's save goes through. --json
    # prints it with the library, or alone when the save does not go through.
    if changed and not args.json:
        _print_slot(reply, number)
    library = _saved_as(reply, number, name)
    if library is None:
        if changed and args.json:
            print_json(reply)
        return 1
    if args.json:
        print_json({**reply, "library": _library_entries(library)})
    else:
        _print_library(library, len(library.presets))
    return 0


def _saved_as(reply: dict, number: int | None, name: str | None) -> a50_library.Library | None:
    """The library with slot `number` of `reply` added under `name`, or its own name for
    None; None for a slot it cannot take or a save that failed, each said on stderr."""
    # A name given replaces the slot's, so the slot's own is checked only when none is given.
    if (preset := _reply_slot(reply, number, name is None, NO_ACTIVE)) is None:
        return None
    if name is not None:
        preset = replace(preset, name=name)
    try:
        return a50_library.edit(lambda saved: saved.added(preset))
    except (ValueError, OSError) as exc:
        failed(exc, a50_library.library_path)
        return None


def _library_entries(library: a50_library.Library) -> list[dict]:
    """The library as `--json` prints it: each preset's number, then a50-eq.json's entry."""
    return [{"number": number, **a50_library.entry(library.get(number))}
            for number in library.numbers]


def _library_lines(preset: astro.EqPreset) -> list[str]:
    """A library preset: its name, then a line for each band, as `headset` gives a slot's."""
    return [preset.name, *(f"band {number}  {_band_words(gain, asdict(band))}"
                           for number, gain, band in zip(astro.BANDS, preset.gains,
                                                         preset.bands(), strict=True))]


def _print_library(library: a50_library.Library, shown: int | None) -> None:
    """The library's presets, or preset `shown` alone, each as `headset` prints a slot."""
    if not library.presets:
        labeled(HEADSET_LABELS, "library", "no presets yet; headset-eq --save-as saves the "
                                           "active one into it")
        return
    for number in library.numbers if shown is None else (shown,):
        head, *bands = _library_lines(library.get(number))
        labeled(HEADSET_LABELS, f"library {number}", head)
        for band in bands:
            continued(HEADSET_LABELS, band)


def failed(exc: Exception, written: Callable[[], Path]) -> int:
    """A step that failed, said on stderr: status 1. An OSError names `written`, where the
    step writes: the error's own filename can be none, or the temporary beside the file,
    removed by then."""
    if isinstance(exc, OSError):
        print(f"error: {_escaped(written())}: {reason(exc)}", file=sys.stderr)
    else:
        print(f"error: {_escaped(exc)}", file=sys.stderr)
    return 1


def loaded_astroeq(file: str) -> astro.EqPreset | None:
    """The .astroeq file `file` as typed; None once why it does not read is said on stderr."""
    # The errors escape the name: whoever shared the file chose it, and a glob passes it on.
    try:
        # As typed: Path("") is ".", the folder the command runs in.
        return astroeq.load_astroeq(file)
    except OSError as exc:
        print(f"error: cannot read {_escaped(file)}: {reason(exc)}", file=sys.stderr)
    except ValueError as exc:
        print(f"error: {_escaped(file)}: {exc}", file=sys.stderr)
    return None


def cmd_headset_import(args: argparse.Namespace) -> int:
    """Load an Astro Command Center .astroeq file into a slot, or with --library into the
    library. Nothing is sent or saved unless the file parses."""
    if (eq := loaded_astroeq(args.file)) is None:
        return 1
    if not args.library:
        return _send_eq(args, eq.request(), args.preset)
    try:
        library = a50_library.edit(lambda saved: saved.added(eq))
    except (ValueError, OSError) as exc:
        return failed(exc, a50_library.library_path)
    return _library_report(args.json, library, len(library.presets))


def _library_report(as_json: bool, library: a50_library.Library, shown: int | None) -> int:
    """The library, or preset `shown` alone, printed; with `as_json` every preset. What the
    file held that did not read is noted on stderr, which keeps --json's output whole."""
    if as_json:
        print_json({"library": _library_entries(library)})
    else:
        _print_library(library, shown)
    if library.left_out:
        print(f"note: {_escaped(library.left_out)}", file=sys.stderr)
    return 0


def _library_change(args: argparse.Namespace
                    ) -> Callable[[a50_library.Library, astro.EqPreset | None],
                                  a50_library.Library]:
    """What `headset-library`'s options make of the library, on its preset NUMBER: the
    gains and bands of the slot --from-slot took first, then --gains, each --band and
    --name. A printable ValueError for one the base station would refuse, raised here
    before anything runs."""
    name = None if args.name is None else typed_name(args.name)
    gains = None if args.gains is None else _gains(args.gains)
    bands = _band_list(args.band)

    def change(library: a50_library.Library, slot: astro.EqPreset | None
               ) -> a50_library.Library:
        preset = library.get(library.number(args.number))
        if slot is not None:
            preset = replace(slot, name=preset.name)
        shown = preset.bands()
        # Five bands and five gains, as they were: a band changes one.
        for band in bands:
            shown[band["band"] - 1] = astro.Band(band["freq"], band["width"])
        return library.put(args.number, astro.EqPreset.of_bands(
            preset.name if name is None else name,
            preset.gains if gains is None else gains, shown))
    return change


def _not_taken(reply: dict, number: int, named: bool) -> str:
    """Why slot `number` cannot be taken, its name checked when `named`: NOT_TAKEN for one the
    daemon holds, else NOT_READ, then READ_HINT or why --read would ask nothing now."""
    slot = f"slot {number}"
    if any(p["preset"] == number for p in reply["presets"]):
        return NOT_TAKEN.format(slot, NAMED_PARTS if named else SOUND_PARTS)
    why = ("the base station is not answering" if not reply["answering"]
           else "the base station is saving; try again once it is done" if reply["saving"]
           else READ_HINT)
    return f"{NOT_READ.format(slot)}; {why}"


def _reply_slot(reply: dict, number: int | None, named: bool, unknown: str
                ) -> astro.EqPreset | None:
    """Slot `number` of a `headset` reply, from what the base station runs; None once why it
    cannot be taken is said on stderr: a slot not read whole, one a preset refuses, its name
    only when `named`, or None, no active one known, said as `unknown`."""
    preset = a50_library.a50_preset(next((p for p in reply["presets"]
                                          if p["preset"] == number), None), named)
    if preset is None:
        print("error: " + (unknown if number is None else _not_taken(reply, number, named)),
              file=sys.stderr)
    return preset


def _from_slot(args: argparse.Namespace) -> astro.EqPreset | None:
    """The slot --from-slot names, the active one for none, as the base station runs it;
    None once the daemon's refusal, or why the slot cannot be taken, is said."""
    reply = send({"command": "headset"})
    if not reply.get("ok"):
        print_reply(reply, args.json)
        return None
    # The library preset keeps its own name, so the slot's is not checked.
    return _reply_slot(reply, args.from_slot or reply["preset"], named=False,
                       unknown=UNKNOWN_ACTIVE.format("--from-slot SLOT"))


def cmd_headset_library(args: argparse.Namespace) -> int:
    """The A50's preset library: preset NUMBER changed by the options and shown alone, or
    every preset, with no NUMBER, after --delete or with --json; with --write, NUMBER written
    to a slot of the base station after the changes, and the slot shown in its place."""
    changes = (args.name is not None or args.gains is not None or bool(args.band)
               or args.from_slot is not None)
    if args.number is None and (changes or args.write is not None or args.delete):
        print("error: give the number of a preset in the library, as headset-library lists "
              "them", file=sys.stderr)
        return 1
    if args.delete and (changes or args.write is not None):
        print(f"error: {ALONE}", file=sys.stderr)
        return 1
    try:
        library = a50_library.load_library()
        if changes or args.delete:
            library.whole()
        if args.number is not None:
            library.number(args.number)
        change = _library_change(args)
    except ValueError as exc:
        print(f"error: {_escaped(exc)}", file=sys.stderr)
        return 1
    slot: astro.EqPreset | None = None
    if args.from_slot is not None and (slot := _from_slot(args)) is None:
        return 1
    if changes or args.delete:
        try:
            library = a50_library.edit((lambda saved: saved.deleted(args.number))
                                       if args.delete else lambda saved: change(saved, slot))
        except (ValueError, OSError) as exc:
            return failed(exc, a50_library.library_path)
    if args.write is not None:
        return _send_eq(args, library.get(args.number).request(), args.write)
    return _library_report(args.json, library, None if args.delete else args.number)


def cmd_headset_save(args: argparse.Namespace) -> int:
    reply = send({"command": "headset_save"})
    if args.json or not reply.get("ok"):
        return print_reply(reply, args.json)
    print("saving to the base station's flash; `gearbench-ctl headset` shows when it is done")
    return 0


def cmd_headset_revert(args: argparse.Namespace) -> int:
    reply = send({"command": "headset_revert"}, timeout=astro.SLOW_REQUEST_WAIT)
    if not reply.get("ok"):
        return print_reply(reply, args.json)
    # Auto shut-off has no flash value; the note says so.
    no_flash = set(astro.SETTINGS) - astro.READS_SAVED
    left = [key for key in reply["unsaved"] if key not in no_flash]
    if args.json:
        print_reply(reply, True)
        return 1 if left else 0
    if not left:
        print("back to what the base station's flash holds")
    if reply.get("note"):
        print(f"note: {_escaped(reply['note'])}")
    if left:
        print(f"error: still differs from the base station's flash: {_unsaved_words(left)}",
              file=sys.stderr)
        return 1
    return 0


def _unit_missing(words: str) -> bool:
    """systemctl's words for the unit's own file not being there: "Unit X does not
    exist" from enable and disable in systemd 259 ("Unit file X does not
    exist." in older ones), "Unit X not found." from a start or restart. A start
    names a missing BindsTo= unit the same way, after "Failed to start X:", which
    installing Gearbench again does not mend."""
    return (f"{spatial.UNIT} does not exist" in words
            or f"Unit {spatial.UNIT} not found" in words)


#: --headset's word for none picked, the one connected first.
FIRST = "first"


def _picked(args: argparse.Namespace, saved: spatial.Choices) -> spatial.Choices:
    """`saved` with the room, the head and the headset `args` give."""
    headset = saved.headset if args.headset is None else (
        None if args.headset == FIRST else args.headset)
    return replace(saved, head=args.head or saved.head, room=args.room or saved.room,
                   headset=headset)


def cmd_headset_spatial(args: argparse.Namespace) -> int:
    """Gearbench Spatial's state, after the room, the head and the headset, then on; with
    off, they are only saved before it stops."""
    restarts = bool(args.head or args.headset)
    try:
        if args.state == "off":
            # The next start renders what is saved; a restart or a live set of a chain
            # about to stop could fail and leave it on.
            if args.room or restarts:
                spatial.save_choices(_picked(args, spatial.load_choices()))
            spatial.switch_off()
        else:
            # The room first: a restart renders the conf from what is saved, so with a head
            # or a headset the room is only saved, and a failed live set cannot cost them.
            if args.room and not restarts:
                spatial.set_room(args.room)
            if restarts:
                spatial.save_choices(_picked(args, spatial.load_choices()))
                spatial.restart(block=True)
            if args.state == "on":
                spatial.switch_on(block=True)
    except spatial.SpatialError as exc:
        print(f"error: {_escaped(exc)}", file=sys.stderr)
        if _unit_missing(str(exc)):
            print(spatial.INSTALL_HINT, file=sys.stderr)
        return 1
    except OSError as exc:
        # Only saving spatial.json raises one; systemctl's and PipeWire's
        # arrive as SpatialError.
        print(f"error: cannot save {spatial.choices_path()}: {reason(exc)}",
              file=sys.stderr)
        return 1
    state = spatial.settled(spatial.read_state, spatial.summary, restarts or args.state == "on")
    if args.json:
        kind, words = spatial.summary(state)
        print_json({**asdict(state), "kind": kind, "summary": words})
        return 0
    for label, value in spatial.describe(state):
        labeled(HEADSET_LABELS, label, value)
    return 0


def add_headset(sub: Subcommands) -> None:
    """The A50's subcommands and headset-spatial, in the order `gearbench-ctl --help` lists
    them."""
    hs = sub.add("headset", help="show the Astro A50: on or off, battery, settings, EQ presets "
                                 "and what is unsaved")
    hs.add_argument("--read", action="store_true",
                    help="read every setting and preset from the base station again first")
    hs.set_defaults(func=cmd_headset)

    hst = sub.add("headset-set", help="change a headset setting or the EQ preset; "
                                      "headset-save keeps it")
    hst.add_argument("setting", choices=HEADSET_SETTINGS)
    hst.add_argument("value", help=_headset_ranges())
    hst.set_defaults(func=cmd_headset_set)

    gain, freq, width = astro.GAIN_RANGE, astro.FREQ_RANGE, astro.WIDTH_RANGE
    shelves = [str(b) for b in astro.SHELVES]
    heq = sub.add("headset-eq", help="change an EQ preset's name, gains or bands; "
                                     "headset-save keeps it")
    heq.add_argument("--preset", type=int, choices=astro.PRESETS,
                     help="the preset to change; the active one when left out")
    heq.add_argument("--name", help=f"{astro.NAME_BYTES[0]} to {astro.NAME_BYTES[1]} bytes")
    heq.add_argument("--gains", metavar="G1,G2,G3,G4,G5",
                     help=f"the five bands' gains in dB, {gain[0]} to {gain[1]}")
    heq.add_argument("--band", action="append", default=[], metavar="BAND:FREQ[:WIDTH]",
                     help=f"a band's frequency in Hz, {freq[0]} to {freq[1]}, and for bands "
                          f"{PEAK_BANDS} its width, {width[0]:.1f} to "
                          f"{width[1]:.1f}; bands {word_list(shelves, 'and')} are shelves with "
                          f"no width; repeatable")
    heq.add_argument("--reset", action="store_true",
                     help="first put the preset's default back, its name, gains and all five "
                          "bands: " + "; ".join(f"{number}, {preset.name}" for number, preset in
                                      zip(astro.PRESETS, a50_library.DEFAULT_SLOTS,
                                          strict=True))
                          + "; headset-save keeps it")
    heq.add_argument("--save-as", nargs="?", const="", metavar="NAME",
                     help="after the other options, save the preset into the library "
                          "Gearbench keeps, as NAME, or under its own name when left out; "
                          f"at most {eq_presets.PRESETS_MAX} presets in it")
    heq.set_defaults(func=cmd_headset_eq)

    him = sub.add("headset-import", help="load an Astro Command Center .astroeq file into an "
                                         "EQ preset, or into the library")
    him.add_argument("file", help="the .astroeq file")
    into = him.add_mutually_exclusive_group()
    into.add_argument("--preset", type=int, choices=astro.PRESETS,
                      help="the preset to load it into; the active one when left out")
    into.add_argument("--library", action="store_true",
                      help="add it to the library Gearbench keeps, sending the base station "
                           "nothing")
    him.set_defaults(func=cmd_headset_import)

    hlb = sub.add("headset-library",
                  help="show or change the A50's preset library, which Gearbench keeps apart "
                       "from the base station's three, or write one of its presets to a slot")
    hlb.add_argument("number", nargs="?", type=int,
                     help="the library preset to show or change, as the list numbers them; "
                          "every preset is listed when left out")
    hlb.add_argument("--from-slot", nargs="?", type=int, const=0, choices=astro.PRESETS,
                     metavar="SLOT",
                     help="first take the gains and bands of slot SLOT, 1, 2 or 3, as the "
                          "base station runs it, the active one when left out; the name stays")
    hlb.add_argument("--gains", metavar="G1,G2,G3,G4,G5",
                     help=f"the five bands' gains in dB, {gain[0]} to {gain[1]}")
    hlb.add_argument("--band", action="append", default=[], metavar="BAND:FREQ[:WIDTH]",
                     help="a band's frequency and width, as headset-eq takes them; repeatable")
    hlb.add_argument("--name", help=f"rename it, {astro.NAME_BYTES[0]} to "
                                    f"{astro.NAME_BYTES[1]} bytes")
    hlb.add_argument("--delete", action="store_true",
                     help="delete it from the library, alone; those after it move up a number")
    hlb.add_argument("--write", type=int, choices=astro.PRESETS, metavar="SLOT",
                     help="after the other options, write it to slot SLOT, 1, 2 or 3, of the "
                          "base station's memory; headset-save keeps it")
    hlb.set_defaults(func=cmd_headset_library)

    sub.add("headset-save", help="save the headset's settings and EQ presets to the base "
                                 "station's flash").set_defaults(
        func=cmd_headset_save)
    sub.add("headset-revert", help="put the headset's settings and EQ presets back to what "
                                   "the base station's flash holds").set_defaults(
        func=cmd_headset_revert)

    hsp = sub.add("headset-spatial",
                  help=f"show or set {spatial.DESCRIPTION}, the 7.1 output for the headsets: "
                       "on or off, the headset it plays to, head and room; no daemon needed")
    hsp.add_argument("state", nargs="?", choices=["on", "off"],
                     help="on: it runs from each login on, playing while a headset is plugged "
                          "in; off: it stops and stays off, and a --head, --room or "
                          "--headset given with it is only saved")
    hsp.add_argument("--headset", choices=[*spatial.HEADSET_NAMES, FIRST],
                     help="the headset it plays to while both are plugged in: a50, g432, or "
                          "first (the default), the one connected first; with the one picked "
                          "unplugged it plays to the other; it restarts a running "
                          f"{spatial.DESCRIPTION}")
    hsp.add_argument("--head", choices=spatial.HEADS,
                     help="the dummy head it is rendered for: ku100, SADIE II's Neumann "
                          "KU100 (the default), or kemar; it restarts a running "
                          f"{spatial.DESCRIPTION}, even for the head it has")
    hsp.add_argument("--room", choices=spatial.ROOMS,
                     help="competitive (the default): direction alone; immersive: a "
                          "short, dry room about 11 dB under the sound; changed while it plays")
    hsp.set_defaults(func=cmd_headset_spatial)
