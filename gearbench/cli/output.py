"""What the parts of the command line share: the request to the daemon, how its reply,
--json and a report's labeled lines are printed, the words and refusals more than one command
uses, a failed step's error and the root parser's subcommands.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from ..battery import reading_words
from ..daemon import send_command
from ..wording import (
    ASLEEP_OR_SWITCHED_OFF,
    NO_REASON,
    NOT_READ,
    READ_AT,
    clock_shown,
    escaped as _escaped,
    hertz,
    reason,
    service_command,
    signed,
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
#: How long a look for a running daemon waits for each answer, in seconds: a daemon that
#: has not answered by then counts as running (daemon.BUSY), so the look need not wait
#: send_command's own 3 s.
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
                "report_rate": "report rate", "buttons_enabled": "buttons enabled",
                "enabled_buttons": "enabled buttons", "buttons_fallback": "fallback held",
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


def m_profile_words(value: object) -> str | None:
    """An M profile's number as status and config give it: "M2"."""
    return f"M{value}" if whole(value) else None


#: The label of a reply's `profile` where it is an M profile's number (m-profile, reload) and
#: where it is a game profile's name (profile-programs, profile-mouse-color), as status gives
#: each: FIELD_LABELS maps a key to one label, so each command passes its own.
M_PROFILE_LABELS = {"profile": "M profile"}
GAME_PROFILE_LABELS = {"profile": "game profile"}


def percent_list_words(value: object) -> str | None:
    """Levels with the percent sign once, at the end: "0, 12, 25, 50 and 100%"."""
    if isinstance(value, list) and value and all(whole(v) for v in value):
        return f"{word_list([str(v) for v in value], 'and')}%"
    return None


def dpi_list_words(value: object) -> str | None:
    """DPI stages with their unit once, "800, 1600, 3200 DPI", as `mouse` gives them."""
    if isinstance(value, list) and value and all(whole(v) for v in value):
        return f"{', '.join(str(v) for v in value)} DPI"
    return None


#: The value words every reply's field takes, for print_reply; a command's own words win.
FIELD_WORDS: FieldWords = {
    "dpi_stages": dpi_list_words, "report_rate": with_unit("Hz"),
    "promoted_to": m_profile_words}


def send(request: dict, timeout: float | None = None) -> dict:
    """One request to the daemon and its reply, after `timeout` seconds at most, or
    send_command's own wait. Every subcommand sends through here: the one place the
    command line looks send_command up."""
    return send_command(request) if timeout is None else send_command(request, timeout=timeout)


def print_json(value: object) -> None:
    """`value` as indented JSON. ensure_ascii, json.dumps's default, is what escapes a C1
    control or a bidi override in a name; a C0 control is escaped either way."""
    print(json.dumps(value, indent=2))


#: The refusal of `--release` with an effect or a color, before anything is sent.
RELEASE_ALONE = "--release takes no effect or --color"


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
        width = label_column(named.get(key, key) for key in body) if body else 0
        for key, value in body.items():
            said = worded[key](value) if key in worded else None
            if said is not None:
                value = said
            elif isinstance(value, list):
                value = ", ".join(str(v) for v in value) or "-"
            elif isinstance(value, dict):
                value = ", ".join(f"{k}={v}" for k, v in value.items()) or "-"
            elif isinstance(value, bool):
                # In words: Python writes True and False.
                value = "yes" if value else "no"
            elif value is None:
                value = "none"
            print(_escaped(f"{named.get(key, key) + ':':<{width}}{value}"))
        if not body:
            print("ok")
    else:
        print(f"error: {_escaped(reply.get('error', NO_REASON))}", file=sys.stderr)
        if reply.get("detail"):
            print(f"       ({_escaped(reply['detail'])})", file=sys.stderr)
    return 0 if reply.get("ok") else 1


#: The note on a device the daemon marks as not answering.
ASLEEP_OR_OFF = f"not answering: {ASLEEP_OR_SWITCHED_OFF}"
#: What a silent device's note ends with, {} the clock_shown of the reply's read_at.
READINGS_FROM = "the readings below are from {}"
#: The note on a device the daemon marks as not answering whose reply gives no read_at.
LAST_READINGS = f"{ASLEEP_OR_OFF}; the readings below are its last"


def silent_note(reply: Mapping) -> str:
    """The unlabeled note under a device line, `status`'s included, while the device does
    not answer: when its readings were taken, where the reply has `read_at`, else that they
    are its last."""
    read = reply.get("read_at")
    if isinstance(read, int | float) and not isinstance(read, bool):
        return f"{ASLEEP_OR_OFF}; {READINGS_FROM.format(clock_shown(read))}"
    return LAST_READINGS

#: What clears a G key or a mouse button, in any case. The Clear key is
#: bound by no name (uinput.CLEAR_KEY).
CLEAR_WORDS = ("none", "clear", "off")
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
    not answer was taken (battery.kept_at), as the window dates it, or None."""
    words = reading_words(battery or {})
    if not words:
        return NOT_READ
    if at is not None:
        return words + READ_AT.format(clock_shown(at))
    return words


def bands_words(bands: Iterable[tuple[int, float]]) -> str:
    """An equalizer's bands, each (frequency, gain), as the window gives them: "32 Hz +0 dB,
    64 Hz +3 dB"."""
    return ", ".join(f"{hertz(freq)} {signed(gain)}" for freq, gain in bands)


def failed(exc: Exception, written: Callable[[], Path]) -> int:
    """A step that failed, said on stderr: status 1. An OSError names `written`, where the
    step writes: the error's own filename can be none, or the temporary beside the file,
    removed by then."""
    if isinstance(exc, OSError):
        print(f"error: {_escaped(written())}: {reason(exc)}", file=sys.stderr)
    else:
        print(f"error: {_escaped(exc)}", file=sys.stderr)
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
    width = label_column(_escaped(label) for label, _value in shown if label is not None)
    for label, value in shown:
        if label is None:
            continued(width, value)
        else:
            labeled(width, label, value)


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
        return self._subparsers.add_parser(
            name, parents=[self._json_flag if offers_json else self._ignored_json],
            help=help, description=help, aliases=list(aliases))

    def switch(self, name: str, *, command: str, help: str,
               words: FieldWords | None = None, state: str | None = None,
               hidden: frozenset[str] = frozenset()) -> None:
        """A switch: `state` is the reply's field that holds it, `command` by default, which
        prints as on or off, the words the switch takes; `hidden` as print_reply's."""
        # command by keyword: CommandTableTests finds the name as command="...".
        switch = self.add(name, help=help)
        switch.add_argument("state", choices=["on", "off"])
        said = {state or command: on_off, **(words or {})}
        switch.set_defaults(func=lambda a: print_reply(send(
            {"command": command, "value": a.state == "on"}), a.json, said, hidden=hidden))
