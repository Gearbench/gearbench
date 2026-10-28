"""`headset-spatial`: Gearbench Spatial, the 7.1 output of every headset in
spatial.HEADSETS, run in this process through systemctl, pw-dump and pw-cli; no daemon.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict, replace

from .. import pipewire_client, spatial
from ..wording import escaped as _escaped, reason
from .output import Subcommands, print_block, print_json


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
            # With on, a chain Gearbench did not start is not set: the start renders the room.
            if args.room and not restarts:
                spatial.set_room(args.room, by_hand=args.state != "on")
            if restarts:
                spatial.save_choices(_picked(args, spatial.load_choices()))
                spatial.restart(block=True)
            if args.state == "on":
                spatial.switch_on(block=True)
    except pipewire_client.StepError as exc:
        print(f"error: {_escaped(exc)}", file=sys.stderr)
        if _unit_missing(str(exc)):
            print(spatial.INSTALL_HINT, file=sys.stderr)
        return 1
    except OSError as exc:
        # Only saving spatial.json raises one; systemctl's and PipeWire's
        # arrive as StepError.
        print(f"error: could not save {_escaped(spatial.choices_path())}: "
              f"{_escaped(reason(exc))}", file=sys.stderr)
        return 1
    state = pipewire_client.settled(spatial.read_state, spatial.summary,
                                    restarts or args.state == "on")
    if args.json:
        kind, words = spatial.summary(state)
        print_json({**asdict(state), "kind": kind, "summary": words})
        return 0
    print_block(spatial.describe(state))
    return 0


def add_headset_spatial(sub: Subcommands) -> None:
    hsp = sub.add("headset-spatial",
                  help=f"show or set {spatial.DESCRIPTION}, the 7.1 output for the headsets: "
                       "on or off, the headset it plays to, head and room; no daemon needed")
    hsp.add_argument("state", nargs="?", choices=["on", "off"],
                     help="on: it runs from each login on, playing while a headset is plugged "
                          "in; off: it stops and stays off, and a --head, --room or "
                          "--headset given with it is only saved")
    hsp.add_argument("--headset", choices=[*spatial.HEADSET_KEYED, FIRST],
                     help="the headset it plays to while it is plugged in, or first (the "
                          "default): the one connected first, which it also plays to while "
                          "the one picked is unplugged; it restarts a running "
                          f"{spatial.DESCRIPTION}")
    hsp.add_argument("--head", choices=spatial.HEADS,
                     help="the dummy head it is rendered for: ku100, SADIE II's Neumann "
                          "KU100 (the default), or kemar; it restarts a running "
                          f"{spatial.DESCRIPTION}, even for the head it has")
    hsp.add_argument("--room", choices=spatial.ROOMS,
                     help="competitive (the default): direction alone; immersive: a "
                          "short, dry room about 11 dB under the sound; changed while it plays")
    hsp.set_defaults(func=cmd_headset_spatial)
