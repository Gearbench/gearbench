"""What the parts of the command line share: the request to the daemon, how its reply and
any --json output are printed, the words and the label column that more than one report
uses, the words that clear a binding, and the root parser's subcommands, which each part
adds its own to.
"""

from __future__ import annotations

import argparse
import json
import sys

from ..battery import reading_words
from ..config import escaped as _escaped
from ..daemon import send_command

#: Reply fields only the window reads: lighting digests (KeyboardCommands.lighting_digest).
WINDOW_DIGESTS = frozenset({"lighting", "was"})

#: The narrowest label column of a reply printed whole, in characters, a label and its colon.
REPLY_LABELS = 20


def send(request: dict, timeout: float | None = None) -> dict:
    """One request to the daemon and its reply, after `timeout` seconds at most, or
    send_command's own wait. Every subcommand sends through here: the one place the
    command line looks send_command up."""
    return send_command(request) if timeout is None else send_command(request, timeout=timeout)


def print_json(value: object) -> None:
    """`value` as indented JSON. ensure_ascii, json.dumps's default, is what escapes a C1
    control or a bidi override in a name; a C0 control is escaped either way."""
    print(json.dumps(value, indent=2))


def print_reply(reply: dict, as_json: bool) -> int:
    if as_json:
        print_json(reply)
    elif reply.get("ok"):
        body = {k: v for k, v in reply.items()
                if k != "ok" and not (k in WINDOW_DIGESTS and isinstance(v, str))}
        if body.get("detail") and str(body["detail"]) in str(body.get("note") or ""):
            # A refused write's note holds the device's words already.
            del body["detail"]
        # Wide enough for the longest key, so the values stand in one column.
        width = max([REPLY_LABELS, *(len(key) + 1 for key in body)])
        for key, value in body.items():
            if key == "mice" and isinstance(value, list):
                # From mouse-host; an error says why host mode did not take.
                value = "; ".join(f"{m.get('name')}: host {'on' if m.get('host') else 'off'}"
                                  + (f", {m['error']}" if m.get("error") else "")
                                  for m in value if isinstance(m, dict)) or "-"
            elif isinstance(value, list):
                value = ", ".join(str(v) for v in value) or "-"
            elif key == "tray" and isinstance(value, dict):
                value = tray_words(value)
            elif isinstance(value, dict):
                value = ", ".join(f"{k}={v}" for k, v in value.items()) or "-"
            elif isinstance(value, bool):
                # In words: Python writes True and False.
                value = "yes" if value else "no"
            elif value is None:
                value = "none"
            print(_escaped(f"{key + ':':<{width}} {value}"))
        if not body:
            print("ok")
    else:
        print(f"error: {_escaped(reply.get('error', 'unknown error'))}", file=sys.stderr)
        if reply.get("detail"):
            print(f"       ({_escaped(reply['detail'])})", file=sys.stderr)
    return 0 if reply.get("ok") else 1


#: What clears a G key or a mouse button, in any case. The Clear key is
#: bound by no name (uinput.CLEAR_KEY).
CLEAR_WORDS = ("none", "clear", "off")


def battery_words(battery: dict) -> str:
    """A battery reading in words (reading_words), a dash for none."""
    return reading_words(battery) or "-"


def tray_words(tray: dict) -> str:
    """The tray line of `status`: the switch, and why the icon does not show."""
    if not tray.get("on"):
        return "no icon (tray off)"
    if tray.get("shown"):
        return "icon shown"
    return f"icon not shown: {tray.get('why') or 'the daemon has not started it'}"


def labeled(width: int, label: str, value: object) -> None:
    print(f"{label + ':':<{width}}{_escaped(value)}")


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
                                     help="print raw JSON replies")
        # keys, gui and daemon take --json and ignore it, so their help does not offer it.
        self._ignored_json = argparse.ArgumentParser(add_help=False)
        self._ignored_json.add_argument("--json", action="store_true",
                                        default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        self._subparsers = parser.add_subparsers(dest="cmd", required=True)

    def add(self, name: str, *, help: str, offers_json: bool = True) -> argparse.ArgumentParser:
        # description too: argparse shows help only in the root's list, not in `NAME --help`.
        return self._subparsers.add_parser(
            name, parents=[self._json_flag if offers_json else self._ignored_json],
            help=help, description=help)

    def switch(self, name: str, *, command: str, help: str) -> None:
        # command by keyword: CommandTableTests finds the name as command="...".
        switch = self.add(name, help=help)
        switch.add_argument("state", choices=["on", "off"])
        switch.set_defaults(func=lambda a: print_reply(send(
            {"command": command, "value": a.state == "on"}), a.json))
