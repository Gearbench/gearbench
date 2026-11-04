"""What the parts of the command line share: the request to the daemon, how its reply,
--json and a report's labeled lines are printed, the words and refusals more than one command
uses, a failed step's error and the root parser's subcommands.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path

from ..battery import reading_words
from ..config import NOTHING, profile_label
from ..daemon import send_command
from ..g432 import MODEL_OF, HeadsetModel, find_g432, reply_model
from ..spatial import INSTALL_HINT
from ..wording import (
    ASLEEP_OR_SWITCHED_OFF,
    NO_REASON,
    NOT_ANSWERING_WORDS,
    NOT_READ,
    READ_AT,
    SETTINGS_NOT_SAVED,
    clock_shown,
    escaped as _escaped,
    number,
    reason,
    service_command,
    whole,
    word_list,
)

#: Reply fields only the window reads: lighting digests (KeyboardCommands.lighting_digest).
WINDOW_DIGESTS = frozenset({"lighting", "was"})

#: --json's help, on the root and on each subcommand that offers it: what it prints is the
#: daemon's reply, or what a command that asks no daemon read itself.
JSON_HELP = "print JSON instead of the formatted output"
#: The commands that stop and start the daemon.
STOP, START = service_command("stop"), service_command("start")
#: How long a look at the daemon waits for each answer, in seconds, short of send_command's
#: own 3 s: a daemon that has not answered by then (daemon.BUSY) counts as running, or the
#: look goes on without its answer.
PROBE_WAIT = 0.5
#: A reply field's own words by its name, for print_reply: None for a value of another
#: shape, which prints as any other.
FieldWords = Mapping[str, Callable[[object], str | None]]
#: A reply field's label in the text output where its key is not the window's word: the
#: daemon's `overlay` is a game profile, its `processes` the programs that load it (the
#: owner's decision of 2026-10-22). --json keeps the keys. The three follow switches take
#: the window's switch title, Follow the leader.
FIELD_LABELS = {"overlay": "game profile", "processes": "programs",
                "lights_off_when_locked": "lights off when locked",
                "mouse_color": "mouse color", "second_color": "second color",
                "follow_leader": "follow the leader", "mouse_sync_color": "follow the leader",
                "headset_sync_color": "follow the leader", "follows": "following now",
                "sync_keyboard": "leader", "sync_leader": "leading now",
                "mouse_lights": "mouse lighting", "tray": "tray icon",
                "dpi_notices": "DPI notices", "mouse_host_mode": "host mode",
                "keep_loaded": "keep loaded", "dpi": "DPI", "dpi_stages": "DPI stages",
                "report_rate": "report rate", "buttons_enabled": "all buttons enabled",
                "enabled_buttons": "enabled buttons", "buttons_fallback": "fallback in profile 1",
                "restored_from": "restored from", "promoted_to": "saved to",
                "profile_path": "game profile file",
                "overlay_error": "game profile file not read"}


def with_unit(unit: str) -> Callable[[object], str | None]:
    """A FieldWords entry for a whole number and its unit, "1000 Hz", as the window gives
    one."""
    return lambda value: f"{value} {unit}" if whole(value) else None


def percent_words(value: object) -> str | None:
    """A FieldWords entry for a whole percent, "25%", as the window and status give a level."""
    return f"{value}%" if whole(value) else None


#: The label of a reply's `profile` where it is an M profile's number and where it is a game
#: profile's name, as status gives each: FIELD_LABELS maps a key to one label, so each
#: command passes its own.
M_PROFILE_LABELS = {"profile": "M profile"}
GAME_PROFILE_LABELS = {"profile": "game profile"}


def percent_list_words(value: object) -> str | None:
    """Levels with the percent sign once, at the end: "0, 12, 25, 50 and 100%"."""
    if isinstance(value, list) and value and all(whole(v) for v in value):
        return f"{listed_words(value)}%"
    return None


def dpi_list_words(value: object) -> str | None:
    """DPI stages with their unit once, "800, 1600, 3200 DPI", as `mouse` gives them."""
    if isinstance(value, list) and value and all(whole(v) for v in value):
        return f"{', '.join(str(v) for v in value)} DPI"
    return None


#: The value words every reply's field takes, for print_reply; a command's own words win.
FIELD_WORDS: FieldWords = {
    "dpi_stages": dpi_list_words, "report_rate": with_unit("Hz")}


def send(request: dict, timeout: float | None = None) -> dict:
    """One request to the daemon and its reply, after `timeout` seconds at most, or
    send_command's own wait. Every subcommand sends through here: the one place the
    command line looks send_command up."""
    return send_command(request) if timeout is None else send_command(request, timeout=timeout)


def driven_g432() -> HeadsetModel | None:
    """The model of the G432's family the daemon drives, from its `g432` reply, which reads
    nothing from the headset; where no daemon answers within PROBE_WAIT, or it drives none,
    the one it attaches at its next look, find_g432's, read from sysfs, which opens nothing:
    the daemon's own rule (daemon/g432's connect), so with two of the family plugged in a
    command acts on the one driven. None for none."""
    try:
        reply = send({"command": "g432"}, timeout=PROBE_WAIT)
    except (OSError, json.JSONDecodeError):
        reply = {}
    # A refusal, with none attached, names no model.
    if (model := reply_model(reply)) is not None:
        return model
    found = find_g432()
    return None if found is None else MODEL_OF[found[1]]


def print_json(value: object) -> None:
    """`value` as indented JSON. ensure_ascii, json.dumps's default, is what escapes a C1
    control or a bidi override in a name; a C0 control is escaped either way."""
    print(json.dumps(value, indent=2))


#: The refusal of `--release` with an effect or a color, before anything is sent.
RELEASE_ALONE = "--release takes no effect or --color"
#: A reply value that holds nothing, an empty list or object or null, in the text output.
NONE_WORD = "none"


def listed_words(values: Sequence[object]) -> str:
    """`values` as a list in prose, "a, b and c", as status gives the effects; NONE_WORD for
    none. A sequence of values, DPI stages say, takes commas alone (dpi_list_words)."""
    return word_list([str(value) for value in values], "and") if values else NONE_WORD


def print_reply(reply: dict, as_json: bool, words: FieldWords | None = None,
                labels: Mapping[str, str] | None = None,
                hidden: frozenset[str] = frozenset()) -> int:
    """`reply` as --json or as labeled lines: `labels` are a command's own labels over
    FIELD_LABELS, `words` its own value words over FIELD_WORDS, and `hidden` the fields only
    --json gives."""
    if as_json:
        print_json(reply)
    elif reply.get("ok"):
        body = {k: v for k, v in reply.items()
                if k != "ok" and k not in hidden
                and not (k in WINDOW_DIGESTS and isinstance(v, str))}
        if body.get("detail") and str(body["detail"]) in str(body.get("note") or ""):
            # A refused write's note holds the device's words already.
            del body["detail"]
        named, worded = {**FIELD_LABELS, **(labels or {})}, {**FIELD_WORDS, **(words or {})}
        lines = []
        for key, value in body.items():
            said = worded[key](value) if key in worded else None
            if said is not None:
                value = said
            elif isinstance(value, list):
                value = listed_words(value)
            elif isinstance(value, dict):
                value = ", ".join(f"{k}={v}" for k, v in value.items()) or NONE_WORD
            elif isinstance(value, bool):
                # In words: Python writes True and False.
                value = "yes" if value else "no"
            elif value is None:
                value = NONE_WORD
            lines.append((named.get(key, key), value))
        if lines:
            print_block(lines)
        else:
            print("ok")
    else:
        print(f"error: {_escaped(reply.get('error', NO_REASON))}", file=sys.stderr)
        if reply.get("detail"):
            print(f"       ({_escaped(reply['detail'])})", file=sys.stderr)
    return 0 if reply.get("ok") else 1


def print_m_profile_reply(reply: dict, as_json: bool, key: str = "profile") -> int:
    """print_reply for a reply whose `key` is an M profile's number: the text names that
    profile as status does, "M profile: M2 · Desk", from the reply's `profile_name`, which
    only --json gives; "M2" alone from a reply without one, a 1.2.1 daemon's to m-profile and
    promote."""
    if as_json or not reply.get("ok") or key not in reply:
        return print_reply(reply, as_json)
    name = reply.get("profile_name")
    shown = {**reply, key: profile_label(reply[key], str(name)) if name else f"M{reply[key]}"}
    return print_reply(shown, False, labels=M_PROFILE_LABELS,
                       hidden=frozenset({"profile_name"}))


#: The note on a device the daemon marks as not answering.
ASLEEP_OR_OFF = f"{NOT_ANSWERING_WORDS}: {ASLEEP_OR_SWITCHED_OFF}"
#: What a silent device's note ends with, {} the clock_shown of the reply's read_at.
READINGS_READ_AT = "the readings below were read at {}"
#: What a silent device's note ends with where its reply gives no read_at.
ITS_LAST = "the readings below are its last"
#: The note on a device the daemon marks as not answering whose reply gives no read_at.
LAST_READINGS = f"{ASLEEP_OR_OFF}; {ITS_LAST}"


def silent_note(reply: Mapping, cabled: bool = False) -> str:
    """The unlabeled note under a device line, `status`'s included, while the device does
    not answer: NOT_ANSWERING_WORDS for a keyboard on its cable (`cabled`), else ASLEEP_OR_OFF,
    then when its readings were taken, where the reply has `read_at`, else that they are its
    last."""
    silent = NOT_ANSWERING_WORDS if cabled else ASLEEP_OR_OFF
    read = reply.get("read_at")
    if number(read):
        return f"{silent}; {READINGS_READ_AT.format(clock_shown(read))}"
    return f"{silent}; {ITS_LAST}"

#: What clears a G key or a mouse button, in any case; what each sends on each layer is
#: cleared_binding's. The Clear key is bound by no name (uinput.CLEAR_KEY).
CLEAR_WORDS = (NOTHING, "clear", "off")
#: The help's words for CLEAR_WORDS on bind and mouse-bind: NOTHING apart, since on the
#: G-Shift layer it binds nothing where the others give back the base binding.
CLEAR_HELP = (f"{NOTHING} to make it do nothing, "
              f"{word_list([word for word in CLEAR_WORDS if word != NOTHING])} to clear it")


def cleared_binding(typed: str, *, shift: bool, to_nothing: bool) -> str | None:
    """The binding to send for `typed`: as typed, or for one of CLEAR_WORDS, NOTHING or None
    (unbound). On the G-Shift layer NOTHING binds the key to nothing there and the others
    unbind it, so it sends its base binding (the owner's decisions of 2026-10-28); on the base
    layer each binds it to NOTHING where `to_nothing`, for a key that does something of its
    own unbound, and unbinds it elsewhere."""
    word = typed.strip().lower()
    if word not in CLEAR_WORDS:
        return typed
    return NOTHING if (word == NOTHING if shift else to_nothing) else None


#: The word that writes back what the daemon first read of a headset, as the window's Put
#: back does: `audio-set`'s settings, `g432-sidetone`'s level. Reset is a preset's default.
PUT_BACK = "put-back"


def on_off(value: object) -> str | None:
    """A switch's state as its command takes it, on or off; None for a value that is not one,
    which prints as any other. yes and no stay for other facts."""
    return ("on" if value else "off") if isinstance(value, bool) else None


def device_words(device: object, name: object, way: object) -> str:
    """A device in the form of every device line, `status`'s included: its node where it
    has one, then its name and its route where that is known."""
    named = f"{name}, {way}" if way else str(name)
    return named if device is None else f"{device}  {named}"


def battery_words(battery: dict | None, at: float | None) -> str:
    """A battery reading in the words of every view (reading_words), NOT_READ for none or for
    one with no level or state, undated; `at` is when a reading kept while the device does
    not answer was taken (battery.kept_at), as the window dates it, or None. Then its
    voltage, where it has one (volts_words), the level's or not."""
    words = reading_words(battery or {})
    if not words:
        words = NOT_READ
    elif at is not None:
        words += READ_AT.format(clock_shown(at))
    return words + volts_words(battery)


def volts_words(reading: Mapping | None) -> str:
    """A reading's voltage after its level, " (3.9 V)", where it has one: a 0x1001 or 0x1F20
    cell's, whose percentage comes off a curve or, with no curve, is not known; "" for none."""
    volts = (reading or {}).get("volts")
    return f" ({volts} V)" if number(volts) else ""


def writes_lines(*refusals: object) -> list[tuple[str | None, str]]:
    """A device report's writes: field: one line for each refusal given, the later ones under
    the first; none where none is."""
    said = [f"refused: {why}" for why in refusals if why]
    return [("writes" if index == 0 else None, line) for index, line in enumerate(said)]


def failed(exc: Exception, written: Callable[[], Path]) -> int:
    """A step that failed, said on stderr: status 1. An OSError names `written`, where the
    step writes: the error's own filename can be none, or the temporary beside the file,
    removed by then."""
    if isinstance(exc, OSError):
        print(f"error: {_escaped(written())}: {reason(exc)}", file=sys.stderr)
    else:
        print(f"error: {_escaped(exc)}", file=sys.stderr)
    return 1


def save_failed(exc: OSError, saved: Path) -> int:
    """A save of the settings file `saved` that failed, said on stderr: status 1. It names the
    file or folder the error names, behind a link the one the link leads to
    (settings_files.write_json), else `saved`."""
    print(f"error: {SETTINGS_NOT_SAVED.format(_escaped(exc.filename or saved),
                                              _escaped(reason(exc)))}", file=sys.stderr)
    return 1


def unit_failed(exc: Exception, unit: str) -> int:
    """A step of a switch of `unit`, a user unit Gearbench installs, that failed, said on
    stderr: status 1. Where systemctl says the unit's own file is not there, the hint to
    install Gearbench again follows: "Unit X does not exist" from enable and disable in
    systemd 259 ("Unit file X does not exist." in older ones), "Unit X not found." from a
    start or restart. A start names a missing BindsTo= unit the same way, after "Failed to
    start X:", which installing Gearbench again does not mend."""
    print(f"error: {_escaped(exc)}", file=sys.stderr)
    words = str(exc)
    if f"{unit} does not exist" in words or f"Unit {unit} not found" in words:
        print(INSTALL_HINT, file=sys.stderr)
    return 1


def labeled(width: int, label: str, value: object) -> None:
    """`label` and `value` on one line, `value` at column `width`, each escaped: a label can
    come from the daemon's reply too."""
    print(f"{_escaped(label) + ':':<{width}}{_escaped(value)}")


def label_column(labels: Iterable[str]) -> int:
    """A block's label column: its longest label, the colon and one space, so each value
    starts one space past the longest label's colon."""
    return max(len(label) for label in labels) + 2


def print_block(lines: Iterable[tuple[str | None, object]]) -> None:
    """A block of a report, each (label, value) in label_column's column, a label of None
    for a value under the line above it."""
    shown = list(lines)
    # As labeled prints them: escaped.
    print_lines(label_column(_escaped(label) for label, _value in shown if label is not None),
                shown)


def print_lines(width: int, lines: Iterable[tuple[str | None, object]]) -> None:
    """Each (label, value) of `lines` with its value at column `width`, a label of None for a
    value under the line above it."""
    for label, value in lines:
        if label is None:
            continued(width, value)
        else:
            labeled(width, label, value)


def print_unit_state(fields: dict, summed: tuple[str, str],
                     rows: Iterable[tuple[str | None, object]], notes: Sequence[str],
                     as_json: bool) -> None:
    """A unit's state, as a command that runs one in this process prints it: with `as_json`
    its `fields` (dataclasses.asdict), its summary's kind and words (`summed`) and the
    switch's `notes`; else its `rows` (print_block) and a note: line for each note."""
    if as_json:
        kind, words = summed
        print_json({**fields, "kind": kind, "summary": words, "notes": list(notes)})
        return
    print_block(rows)
    for note in notes:
        print(f"note: {_escaped(note)}")


def continued(width: int, value: object) -> None:
    print(" " * width + _escaped(value))


class Subcommands:
    """The subcommands of `gearbench-ctl`, which each part adds its own to: `add` makes
    one, and `switch` one that takes on or off and sends it."""

    def __init__(self, parser: argparse.ArgumentParser) -> None:
        # On every subparser too, so `status --json` works as well as `--json status`.
        # SUPPRESS keeps the subparser's default False from overwriting the root's True.
        self._json_flag = argparse.ArgumentParser(add_help=False)
        self._json_flag.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                                     help=JSON_HELP)
        # A subcommand that ignores --json still takes it, and its help does not offer it.
        self._ignored_json = argparse.ArgumentParser(add_help=False)
        self._ignored_json.add_argument("--json", action="store_true",
                                        default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        self._subparsers = parser.add_subparsers(dest="cmd", required=True)

    def add(self, name: str, *, help: str, offers_json: bool = True,
            aliases: tuple[str, ...] = ()) -> argparse.ArgumentParser:
        """A subcommand; `aliases` are its other names, a name a released one had."""
        # description too: argparse shows help only in the root's list, not in `NAME --help`.
        # It %-formats a help but not a description, so a help's %% is one % there.
        return self._subparsers.add_parser(
            name, parents=[self._json_flag if offers_json else self._ignored_json],
            help=help, description=help.replace("%%", "%"), aliases=list(aliases))

    def switch(self, name: str, *, command: str, help: str,
               words: FieldWords | None = None, state: str | None = None,
               hidden: frozenset[str] = frozenset(), timeout: float | None = None) -> None:
        """A switch: `state` is the reply's field that holds it, `command` by default, which
        prints as on or off, the words the switch takes; `hidden` as print_reply's, `timeout`
        as send's."""
        # command by keyword: CommandTableTests finds the name as command="...".
        switch = self.add(name, help=help)
        switch.add_argument("state", choices=["on", "off"])
        said = {state or command: on_off, **(words or {})}
        switch.set_defaults(func=lambda a: print_reply(send(
            {"command": command, "value": a.state == "on"}, timeout), a.json, said,
            hidden=hidden))
