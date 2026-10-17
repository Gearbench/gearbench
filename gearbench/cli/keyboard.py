"""The keyboard's subcommands: the M profile, the brightness, the color, its preview and
the effect, `bind` for a G key, `keys`, which lists the names a binding may use, and
`probe`, the one subcommand whose handler opens a device itself (`daemon` runs the daemon,
which opens them): it reads the keyboard's node, writes nothing, and refuses beside a
running daemon unless forced.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import G_KEYS, PROFILE_IDS, ConfigError, counted, reason, validate_binding, word_list
from ..daemon import BUSY, DOWN, UNKNOWN, failure, socket_path, use_invoking_users_runtime_dir
from ..hidpp import NO_RULE, HidppError, HidppFailure, open_device
from ..keyboard import (
    MODELS as KEYBOARD_MODELS,
    WITNESS as KEYBOARD_WITNESS,
    capabilities as keyboard_capabilities,
    find_attached_keyboard,
    name as keyboard_name,
)
from ..lighting import (
    ANIMATIONS,
    F_RGB,
    KEYBOARD_EFFECTS,
    SW_STATES,
    WAVE_DIRECTIONS,
    Lighting,
    LightingError,
    speed_range,
)
from ..onboard import F_ONBOARD, MODE_NAMES, read_mode
from ..uinput import KEY_NAMES, MODIFIERS
from .output import CLEAR_WORDS, Subcommands, print_json, print_reply, send

if TYPE_CHECKING:
    from ..hidpp import Hidpp


def _daemon_running(state: str) -> int:
    """What probe says when a daemon holds the node it would read."""
    print(f"error: the daemon {state}, and probe would compete with it for "
          f"replies. Stop it first:\n"
          f"    systemctl --user stop gearbench\n"
          f"or pass --force to risk a misread.",
          file=sys.stderr)
    return 1


def _print_probe(found: dict) -> None:
    """Probe's dump as text; --json prints `found` whole."""
    print(f"device:       {found['device']}  {found['name']} "
          f"({found['product']}, device index {found['device_index']})")
    print(f"capabilities: {', '.join(found['capabilities']) or 'none this build drives'}")
    if found["onboard_mode"] is not None:
        print(f"onboard mode: {found['onboard_mode']} "
              f"(the daemon runs the keyboard in host mode)")
    for cluster in found["clusters"]:
        print(f"cluster {cluster['index']} ({cluster['name']}, "
              f"location 0x{cluster['location']:04x}):")
        for effect in cluster["effects"]:
            print(f"  index {effect['index']}  id 0x{effect['id']:04x}  {effect['name']}")
    if found["brightness"] is not None:
        levels = ", ".join(str(level) for level in found["brightness_levels"])
        print(f"brightness:   {found['brightness']} of {levels}")
    if found["sw_control"] is not None:
        state = SW_STATES.get((found["sw_host"], found["sw_control"]),
                              "a pair this build does not name")
        print(f"sw control:   control 0x{found['sw_host']:02x}, notify "
              f"0x{found['sw_control']:02x} ({state})")


def _daemon_beside() -> int | None:
    """probe's exit status where a daemon is running or may be, with the refusal said; None
    where none is."""
    use_invoking_users_runtime_dir()
    # Only DOWN means no daemon. Any answer shows one, an unreadable one included, and so
    # does BUSY: a daemon is slowest when busy on the device. UNKNOWN says nothing either
    # way.
    try:
        send({"command": "status"}, timeout=0.5)
    except (OSError, json.JSONDecodeError) as exc:
        kind = failure(exc)
        if kind == DOWN:
            return None
        if kind == BUSY:
            return _daemon_running("is running and did not answer in time")
        if kind == UNKNOWN:
            print(f"error: cannot tell whether the daemon is running: {socket_path()}: "
                  f"{reason(exc)}. If one is, stop it first:\n"
                  f"    systemctl --user stop gearbench\n"
                  f"otherwise pass --force.", file=sys.stderr)
            return 1
    return _daemon_running("is running")


def _open_keyboard() -> tuple[Path, int, Hidpp, int] | None:
    """The attached keyboard as (node, product, the node opened, device index), or None
    with the reason said: none found, no permission, or no keyboard answering on the
    node."""
    found_device = find_attached_keyboard()
    known = ", ".join(sorted(set(KEYBOARD_MODELS.values())))
    if found_device is None:
        print(f"error: no keyboard this build knows ({known}) found, on a cable or "
              f"through a receiver. Check that one is plugged in.",
              file=sys.stderr)
        return None
    path, product, derived = found_device
    try:
        opened = open_device(path, KEYBOARD_WITNESS, prefer=derived)
    except PermissionError:
        print(f"error: cannot open {path}: {NO_RULE}", file=sys.stderr)
        return None
    if opened is None:
        print(f"error: a Logitech device is on {path} but no keyboard answered. If the "
              f"keyboard is on a receiver, check that it is switched on.",
              file=sys.stderr)
        return None
    return path, product, *opened


def _read_keyboard(dev: Hidpp, path: Path, product: int, index: int) -> dict:
    """probe's dump, read from the open keyboard `dev`; `--json` prints it whole. Reads
    only: the feature table, the onboard mode, and of the lighting its clusters, its
    brightness and who controls it."""
    # A keyboard without 0x8071 is probed too, with no lighting.
    light = Lighting(dev) if dev.feature(F_RGB) is not None else None
    onboard = dev.feature(F_ONBOARD)
    mode = read_mode(dev, onboard) if onboard is not None else None
    host, notify = light.sw_state() if light else (None, None)
    return {
        "ok": True,
        "device": str(path),
        "name": keyboard_name(product),
        "product": f"{product:04x}",
        "device_index": f"0x{index:02x}",
        "capabilities": keyboard_capabilities(dev),
        # A keyboard without onboard profiles has no mode, and None names nothing:
        # dict.get takes a None key, and typeshed's does not.
        "onboard_mode": MODE_NAMES.get(mode, mode),  # type: ignore[arg-type]
        # get_brightness raises on a keyboard without 0x8040.
        "brightness": (light.get_brightness()
                       if light and light.brightness_levels else None),
        "brightness_levels": list(light.brightness_levels) if light else [],
        "sw_host": host,
        "sw_control": notify,
        "clusters": [] if light is None else [
            {
                "index": cluster.index,
                "name": cluster.name,
                "location": cluster.location,
                "effects": [
                    {"index": e.index, "id": e.id, "name": e.name}
                    for e in cluster.effects
                ],
            }
            for cluster in light.clusters
        ],
    }


def cmd_probe(args: argparse.Namespace) -> int:
    """Read-only dump of what this firmware exposes. Needs no daemon.

    Refuses while the daemon runs: both processes see every input report on the
    node, and a reply is matched on feature, function and a four-bit software id
    each process numbers on its own, so the daemon can take probe's reply and act on it.
    """
    if not args.force:
        beside = _daemon_beside()
        if beside is not None:
            return beside
    opened = _open_keyboard()
    if opened is None:
        return 1
    path, product, node, index = opened
    try:
        with node as dev:
            found = _read_keyboard(dev, path, product, index)
    except OSError as exc:
        print(f"error: cannot read {path}: {reason(exc)}", file=sys.stderr)
        return 1
    except HidppFailure as exc:
        # An error reply is an answer; silence, a lost node, or a receiver that cannot reach
        # the keyboard is not. A failure of the node itself is said in the system's words:
        # its own text carries "[Errno 19]".
        refused = isinstance(exc, HidppError) and not exc.unreachable
        print(f"error: the keyboard on {path} "
              f"{'refused a request' if refused else 'stopped answering'}: "
              f"{reason(exc.__cause__ or exc)}",
              file=sys.stderr)
        return 1
    except LightingError as exc:
        # A reply too short to decode, such as a short report's 3 bytes.
        print(f"error: the keyboard on {path} gave a lighting reply too short to read: {exc}",
              file=sys.stderr)
        return 1

    if args.json:
        print_json(found)
        return 0
    _print_probe(found)
    return 0


def cmd_keys(args: argparse.Namespace) -> int:
    """The names a binding may use (KEY_NAMES), or with --modifiers the modifiers."""
    if args.modifiers:
        print("modifiers: " + ", ".join(sorted(MODIFIERS)))
        print("\nPut one before a key, `gearbench-ctl bind G1 ctrl+f15`, or bind it alone, "
              "`gearbench-ctl bind G1 shift`, to hold it while the G key is held.",
              file=sys.stderr)
        return 0
    names = sorted(KEY_NAMES)
    if args.match:
        needle = args.match.upper()
        names = [n for n in names if needle in n]
    for name in names:
        print(f"{name:<28} {KEY_NAMES[name]}")
    print(f"\n{counted(len(names), 'key')}. Bind with e.g. "
          "`gearbench-ctl bind G1 ctrl+f15`.", file=sys.stderr)
    return 0


def cmd_bind(args: argparse.Namespace) -> int:
    """Bind a G key to a key spec or to `macro:<name>`.

    The spec is checked here, so a typo fails without a round trip. A macro name is
    checked for its form only: the daemon owns the library, and a binding may name a
    macro not yet written.
    """
    spec = None if args.binding.strip().lower() in CLEAR_WORDS else args.binding
    if spec is not None:
        try:
            validate_binding(spec, "binding")
        except ConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    return print_reply(send({
        "command": "bind", "key": args.key, "value": spec, "shift": args.shift,
    }), args.json)


def speed_ranges() -> str:
    """The speeds each animation takes, in words: "breathe, cycle and wave
    1000 to 20000, ripple 100 to 1000"."""
    groups: dict[tuple[int, int], list[str]] = {}
    for name in ANIMATIONS:
        groups.setdefault(speed_range(name), []).append(name)
    return ", ".join(f"{word_list(names, 'and')} {fastest} to {slowest}"
                     for (fastest, slowest), names in groups.items())


def cmd_effect(args: argparse.Namespace) -> int:
    """Send the effect; a speed or direction left out keeps what the profile saved.
    The daemon checks the range."""
    request: dict = {"command": "effect", "value": args.name}
    if args.speed is not None:
        request["speed"] = args.speed
    if args.direction is not None:
        request["direction"] = args.direction
    return print_reply(send(request), args.json)


def add_lighting_and_bind(sub: Subcommands) -> None:
    p = sub.add("profile", help="switch the M profile, as M1-M3 do")
    p.add_argument("index", type=int, choices=PROFILE_IDS)
    p.set_defaults(func=lambda a: print_reply(send({"command": "profile", "value": a.index}), a.json))

    b = sub.add("brightness", help="set the backlight level")
    b.add_argument("level", type=int,
                   help="0-100, floored to a level the device keeps; the G915's are 0, 12, 25, "
                        "50 and 100")
    b.set_defaults(func=lambda a: print_reply(send({"command": "brightness", "value": a.level}), a.json))

    c = sub.add("color", help="set the current profile's color")
    c.add_argument("value", help="six hex digits like #0080ff")
    c.set_defaults(func=lambda a: print_reply(send({"command": "color", "value": a.value}), a.json))

    pv = sub.add("preview", help="repaint without saving, as a color drag does")
    pv.add_argument("value", help="six hex digits like #0080ff")
    pv.set_defaults(func=lambda a: print_reply(send({"command": "preview", "value": a.value}), a.json))

    e = sub.add("effect", help="set the lighting effect, saved to the loaded game profile or "
                               "else the M profile")
    e.add_argument("name", choices=sorted(KEYBOARD_EFFECTS))
    e.add_argument("--speed", type=int, metavar="MS",
                   help=f"how long one cycle takes, in milliseconds; lower is faster: "
                        f"{speed_ranges()}")
    e.add_argument("--direction", choices=list(WAVE_DIRECTIONS),
                   help="wave only; horizontal until one is chosen")
    e.set_defaults(func=cmd_effect)

    bind = sub.add("bind", help="bind a G key, e.g. `bind G1 ctrl+f15`")
    bind.add_argument("key", type=str.upper, choices=G_KEYS)
    bind.add_argument("binding",
                      help="a key spec like ctrl+shift+f5, a modifier alone like shift, "
                           "keys held together like a+b, 'macro:<name>' to run a macro, "
                           f"or {word_list(CLEAR_WORDS)} to clear")
    bind.add_argument("--shift", action="store_true", help="bind on the MR shift layer")
    bind.set_defaults(func=cmd_bind)


def add_probe_and_keys(sub: Subcommands) -> None:
    pr = sub.add("probe", help="read-only dump of the keyboard, with the daemon stopped")
    pr.add_argument("--force", action="store_true",
                    help="probe even while the daemon is running")
    pr.set_defaults(func=cmd_probe)

    k = sub.add("keys", help="list bindable key names", offers_json=False)
    k.add_argument("match", nargs="?", help="filter, e.g. `keys volume`")
    k.add_argument("--modifiers", action="store_true",
                   help="list the modifiers, which go before a key or are bound alone")
    k.set_defaults(func=cmd_keys)
