"""The headset's subcommands: `headset` and its report, `headset-set`, `headset-eq`,
`headset-import`, `headset-save` and `headset-revert`, which ask the daemon, and
`headset-spatial`, which runs systemctl, pw-dump and pw-cli in this process and needs
no daemon.
"""

from __future__ import annotations

import argparse
import sys
import time
import unicodedata
from collections.abc import Callable
from dataclasses import asdict, replace
from functools import partial

from .. import astro, spatial
from ..config import clock_shown, escaped as _escaped, reason, word_list
from .output import Subcommands, battery_words, continued, labeled, print_json, print_reply, send

#: The width of `headset`'s label column: "default-balance: " is the longest.
HEADSET_LABELS = 17
#: What a slow headset request may take: tens of round trips to the base
#: station, one at a time.
HEADSET_SLOW = 10.0


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
                   else "; gearbench-ctl headset --read reads it"))
    return said or "none"


def _save_hint(reply: dict, key: str | None = None) -> None:
    """Point at headset-save while `key`, or anything, is not saved to flash, unless the
    base station would refuse a save: while it saves or is not answering."""
    unsaved = reply["unsaved"]
    if not reply["saving"] and reply["answering"] and (key in unsaved if key else unsaved):
        print("Save to the base station with: gearbench-ctl headset-save", file=sys.stderr)


def cmd_headset(args: argparse.Namespace) -> int:
    """The headset's state as a report; `--json` prints the reply whole."""
    request = {"command": "headset", "read": args.read}
    reply = send(request, timeout=HEADSET_SLOW) if args.read else send(request)
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


def _preset_name(name: str) -> str:
    # argv holds a byte that is not UTF-8 as a lone surrogate, which encode() raises on.
    if any(unicodedata.category(c) == "Cs" for c in name):
        raise ValueError("a preset name cannot hold a byte that is not UTF-8")
    # The daemon's rule, so a name it would refuse is refused before it is sent.
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


def _send_eq(args: argparse.Namespace, request: dict) -> int:
    """Send a headset_eq, on --preset's preset when given, and show the changed preset as the
    base station read it back."""
    if args.preset is not None:
        request["preset"] = args.preset
    reply = send(request, timeout=HEADSET_SLOW)
    if args.json or not reply.get("ok"):
        return print_reply(reply, args.json)
    number = request.get("preset", reply["preset"])
    preset = next((p for p in reply["presets"] if p["preset"] == number), None)
    if preset is None:
        print("ok")
        return 0
    _print_preset(preset)
    _save_hint(reply, f"preset_{number}")
    return 0


def cmd_headset_eq(args: argparse.Namespace) -> int:
    if args.name is None and args.gains is None and not args.band:
        print("error: give --name, --gains or --band, or more than one", file=sys.stderr)
        return 1
    request: dict = {"command": "headset_eq"}
    try:
        if args.name is not None:
            request["name"] = _preset_name(args.name)
        if args.gains is not None:
            request["gains"] = _gains(args.gains)
        if args.band:
            request["bands"] = [_band(text) for text in args.band]
            numbers = [band["band"] for band in request["bands"]]
            repeated = next((n for n in numbers if numbers.count(n) > 1), None)
            if repeated is not None:
                raise ValueError(f"band {repeated} is given more than once")
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return _send_eq(args, request)


def cmd_headset_import(args: argparse.Namespace) -> int:
    """Load an Astro Command Center .astroeq file into a preset. Nothing is sent
    unless the file parses."""
    # The errors escape the name: whoever shared the file chose it, and a glob passes it on.
    try:
        # As typed: Path("") is ".", the folder the command runs in.
        eq = astro.load_astroeq(args.file)
    except OSError as exc:
        print(f"error: cannot read {_escaped(args.file)}: {reason(exc)}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {_escaped(args.file)}: {exc}", file=sys.stderr)
        return 1
    return _send_eq(args, eq.request())


def cmd_headset_save(args: argparse.Namespace) -> int:
    reply = send({"command": "headset_save"})
    if args.json or not reply.get("ok"):
        return print_reply(reply, args.json)
    print("saving to the base station's flash; `gearbench-ctl headset` shows when it is done")
    return 0


def cmd_headset_revert(args: argparse.Namespace) -> int:
    reply = send({"command": "headset_revert"}, timeout=HEADSET_SLOW)
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


def cmd_headset_spatial(args: argparse.Namespace) -> int:
    """Astro A50 Spatial's state, after the room, the head, then on; with off, the room
    and the head are only saved before it stops."""
    try:
        if args.state == "off":
            # The next start renders what is saved; a restart or a live set of a chain
            # about to stop could fail and leave it on.
            if args.room or args.head:
                saved = spatial.load_choices()
                spatial.save_choices(replace(saved, head=args.head or saved.head,
                                             room=args.room or saved.room))
            spatial.switch_off()
        else:
            # The room first: the head's restart renders the conf from what is saved, so
            # with a head the room is only saved, and a failed live set cannot cost the
            # head.
            if args.room and args.head:
                spatial.save_choices(replace(spatial.load_choices(), room=args.room))
            elif args.room:
                spatial.set_room(args.room)
            if args.head:
                spatial.set_head(args.head, block=True)
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
    state = spatial.read_state()
    deadline = time.monotonic() + spatial.SETTLE
    while ((args.head or args.state == "on") and spatial.summary(state)[0] == "starting"
           and time.monotonic() < deadline):
        time.sleep(spatial.SETTLE_STEP)
        state = spatial.read_state()
    if args.json:
        kind, words = spatial.summary(state)
        print_json({**asdict(state), "kind": kind, "summary": words})
        return 0
    for label, value in spatial.describe(state):
        labeled(HEADSET_LABELS, label, value)
    return 0


def add_headset(sub: Subcommands) -> None:
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
    heq.set_defaults(func=cmd_headset_eq)

    him = sub.add("headset-import", help="load an Astro Command Center .astroeq file into an "
                                         "EQ preset")
    him.add_argument("file", help="the .astroeq file")
    him.add_argument("--preset", type=int, choices=astro.PRESETS,
                     help="the preset to load it into; the active one when left out")
    him.set_defaults(func=cmd_headset_import)

    sub.add("headset-save", help="save the headset's settings and EQ presets to the base "
                                 "station's flash").set_defaults(
        func=cmd_headset_save)
    sub.add("headset-revert", help="put the headset's settings and EQ presets back to what "
                                   "the base station's flash holds").set_defaults(
        func=cmd_headset_revert)

    hsp = sub.add("headset-spatial",
                  help="show or set Astro A50 Spatial, the 7.1 output: on or off, head and "
                       "room; no daemon needed")
    hsp.add_argument("state", nargs="?", choices=["on", "off"],
                     help="on: it runs whenever the base station is plugged in; off: it stops "
                          "and stays off, and a --head or --room given with it is only saved")
    hsp.add_argument("--head", choices=spatial.HEADS,
                     help="the dummy head it is rendered for: ku100, SADIE II's Neumann "
                          "KU100 (the default), or kemar; Astro A50 Spatial restarts if "
                          "running, even for the head it has")
    hsp.add_argument("--room", choices=spatial.ROOMS,
                     help="competitive (the default): direction alone; immersive: a "
                          "short, dry room about 11 dB under the sound; changed while it plays")
    hsp.set_defaults(func=cmd_headset_spatial)
