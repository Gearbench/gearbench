"""`headset-spatial`: Gearbench Spatial, the 7.1 output of every headset in
pipewire_headsets.HEADSETS, run in this process through systemctl, pw-dump and pw-cli; no daemon.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace

from .. import outputs, pipewire_client, pipewire_headsets, spatial
from .output import Subcommands, print_unit_state, save_failed, unit_failed

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
    off, they are only saved after it stops."""
    restarts = bool(args.head or args.headset)
    try:
        if args.state == "off":
            # The next start renders what is saved; a restart or a live set of a chain
            # about to stop, or a save that fails, could leave it on, so it stops first.
            spatial.switch_off()
            if args.room or restarts:
                spatial.save_choices(_picked(args, spatial.load_choices()))
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
        return unit_failed(exc, spatial.UNIT)
    except OSError as exc:
        # Only saving spatial.json raises one; systemctl's and PipeWire's arrive as StepError.
        return save_failed(exc, spatial.choices_path())
    state = pipewire_client.settled(spatial.read_state, spatial.summary,
                                    restarts or args.state == "on")
    # Spatial's steps give no notes: --json's `notes` is empty, kept so each unit's command
    # gives one shape.
    print_unit_state(asdict(state), spatial.summary(state), spatial.describe(state), (),
                     args.json)
    return 0


def add_headset_spatial(sub: Subcommands) -> None:
    hsp = sub.add("headset-spatial",
                  help=f"show or set {spatial.DESCRIPTION}, the 7.1 output for the headsets: "
                       "on or off, the headset it plays to, head and room; no daemon needed")
    hsp.add_argument("state", nargs="?", choices=["on", "off"],
                     help="on: it runs from each login on, playing while a headset is plugged "
                          "in; off: it stops and stays off, and a --head, --room or "
                          "--headset given with it is only saved")
    hsp.add_argument("--headset", choices=[*pipewire_headsets.HEADSET_KEYED, FIRST],
                     help=f"the headset it and {outputs.DESCRIPTION} play to while that "
                          "headset is plugged in, or first (the default): the one connected "
                          "first, which they also play to while the one picked is unplugged; "
                          f"it restarts a running {spatial.DESCRIPTION}")
    hsp.add_argument("--head", choices=spatial.HEADS,
                     help="the dummy head it is rendered for: ku100, SADIE II's Neumann "
                          "KU100 (the default), or kemar; it restarts a running "
                          f"{spatial.DESCRIPTION}, even for the head it has")
    hsp.add_argument("--room", choices=spatial.ROOMS,
                     help="competitive (the default): direction alone; immersive: a "
                          "short, dry room about 11 dB under the sound; the room changes "
                          "while it plays, with no restart")
    hsp.set_defaults(func=cmd_headset_spatial)
