"""The keyboard's subcommands: the M profile; the brightness, the color, its preview and
the effect, each for the keyboard that leads or the one `--keyboard` names; the choice of
the keyboard that leads (`sync-keyboard`), the follow switch (`keyboard-follow`), `bind` for
a G key, `keys`, which lists the names a binding may use, and `probe`, of the first keyboard
that answers or the one `--keyboard` names, whose handler opens the keyboard's node itself:
it writes nothing, and refuses beside a running daemon unless forced.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import (
    BINDABLE_KEYS,
    G_KEYS,
    HEADSET_ACTIONS,
    HEADSET_DEFAULTS,
    HEADSET_KEYS,
    PROFILE_IDS,
    format_color,
    key_binding,
    own_keys_words,
    validate_binding,
)
from ..daemon import BUSY, DOWN, UNKNOWN, failure, socket_path, use_invoking_users_runtime_dir
from ..hidpp import NO_RULE, HidppError, HidppFailure, UnreadableAnswer, open_device
from ..keyboard import (
    MODEL_OF as KEYBOARDS,
    MODELS as KEYBOARD_MODELS,
    attached_keyboards,
    capabilities as keyboard_capabilities,
    key_name,
    key_named,
    model_key,
    name as keyboard_name,
    opening,
)
from ..lighting import (
    DEFAULT_SECOND_COLOR,
    DIRECTION_WORDS,
    F_RGB,
    KEYBOARD_EFFECTS,
    SPEEDS,
    SW_STATES,
    TWO_COLORED,
    Lighting,
)
from ..mouse_lighting import ZoneLighting
from ..onboard import F_ONBOARD, MODE_NAMES, read_mode
from ..report import untested, untested_note
from ..settings_files import ConfigError
from ..uinput import KEY_NAMES, MODIFIERS
from ..wording import counted, escaped, reason, word_list
from .output import (
    CLEAR_WORDS,
    M_PROFILE_LABELS,
    PROBE_WAIT,
    STOP,
    FieldWords,
    Subcommands,
    m_profile_words,
    on_off,
    percent_list_words,
    percent_words,
    print_json,
    print_reply,
    send,
    with_unit,
)

if TYPE_CHECKING:
    from ..hidpp import Hidpp

#: The value words of `brightness` and `effect`: each number with its unit, as status,
#: probe and the window give it; --json keeps the numbers.
BRIGHTNESS_WORDS: FieldWords = {"requested": percent_words, "applied": percent_words,
                                "levels": percent_list_words}
EFFECT_WORDS: FieldWords = {"speed": with_unit("ms")}

#: `--keyboard`'s words: whole where it is required, on keyboard-follow, and before the
#: keyboard taken without it where it is not (keyboard_option).
KEYBOARD_NAMED = "the keyboard it is for, by model name or product id, as G515 TKL or c358"
#: The keyboard a lighting command acts on without it: the owner's decision of 2026-10-21,
#: "The leader, --keyboard for another".
LEADER = "the one that leads"


def keyboard_option(parser: argparse.ArgumentParser,
                    left_out: str = LEADER) -> argparse.ArgumentParser:
    """`parser` with `--keyboard`, which `for_keyboard` sends and probe reads itself; its help
    ends with `left_out`, the keyboard taken without it."""
    parser.add_argument("--keyboard", metavar="MODEL|ID",
                        help=f"{KEYBOARD_NAMED}; {left_out} when left out")
    return parser


def for_keyboard(args: argparse.Namespace, request: dict) -> dict:
    """`request` for the keyboard `--keyboard` names, as `keyboard`, which the daemon resolves
    (Keyboards.aimed); without one, for the leader's, as it is."""
    return request if args.keyboard is None else {**request, "keyboard": args.keyboard}


def _daemon_running(state: str) -> int:
    """What probe says when a daemon holds the node it would read."""
    print(f"error: the daemon {state}, and probe would compete with it for "
          f"replies. Stop it first:\n"
          f"    {STOP}\n"
          f"or pass --force to risk a misread.",
          file=sys.stderr)
    return 1


def _print_probe(found: dict) -> None:
    """Probe's dump as text; --json prints `found` whole."""
    print(f"device:       {found['device']}  {found['name']} "
          f"({found['product']}, device index {found['device_index']})")
    if found["untested"]:
        print(f"              {untested_note(found['name'])}")
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
        # A percent, as the window and status give it; the levels, read with it, give % once
        # at their end.
        print(f"brightness:   {found['brightness']}% of "
              f"{percent_list_words(found['brightness_levels'])}")
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
        send({"command": "status"}, timeout=PROBE_WAIT)
    except (OSError, json.JSONDecodeError) as exc:
        kind = failure(exc)
        if kind == DOWN:
            return None
        if kind == BUSY:
            return _daemon_running("is running and did not answer in time")
        if kind == UNKNOWN:
            print(f"error: cannot tell whether the daemon is running: {escaped(socket_path())}: "
                  f"{reason(exc)}. If one is, stop it first:\n"
                  f"    {STOP}\n"
                  f"otherwise pass --force.", file=sys.stderr)
            return 1
    return _daemon_running("is running")


def _open_keyboard(wanted: str | None) -> tuple[Path, int, Hidpp, int] | None:
    """The first keyboard plugged in that answers, by attached_keyboards' order, or with
    `wanted`, a model's key, that model's, as (node, product, the node opened, device index),
    or None with the reason said. A node with nothing answering, a receiver's with its
    keyboard off, gives way to the next."""
    attached = attached_keyboards()
    if wanted is not None:
        attached = [found for found in attached if model_key(found[1]) == wanted]
        if not attached:
            print(f"error: no {key_name(wanted)} found, on a cable or through a receiver. "
                  f"Check that it is plugged in.", file=sys.stderr)
            return None
    if not attached:
        known = word_list(sorted(set(KEYBOARD_MODELS.values())), "and")
        print(f"error: no keyboard Gearbench drives found, on a cable or through a receiver; "
              f"it drives the {known}. Check that one is plugged in.", file=sys.stderr)
        return None
    for path, product, derived in attached:
        try:
            opened = open_device(path, **opening(product, derived))
        except PermissionError:
            print(f"error: no permission to open {path}: {NO_RULE}", file=sys.stderr)
            return None
        except OSError as exc:
            print(f"error: cannot open {path}: {reason(exc)}", file=sys.stderr)
            return None
        if opened is not None:
            return path, product, *opened
    nodes = word_list([str(found[0]) for found in attached], "and")
    print(f"error: a Logitech device is on {nodes} but no keyboard answered. If the "
          f"keyboard is on a receiver, check that it is switched on.",
          file=sys.stderr)
    return None


def _read_keyboard(dev: Hidpp, path: Path, product: int, index: int) -> dict:
    """probe's dump, read from the open keyboard `dev`; `--json` prints it whole. Reads
    only: the feature table, the onboard mode, and of the lighting its clusters, the whole
    board's after them where its model reads it, its brightness and who controls it, or on
    a model lit by 0x8070's zones those zones."""
    # ZoneLighting's walk sends 0x8070's three reads and no claim.
    light = (Lighting(dev, whole_board=KEYBOARDS[product].whole_board)
             if dev.feature(F_RGB) is not None else None)
    zones = (ZoneLighting.find(dev, claim_each_effect=False)
             if light is None and KEYBOARDS[product].zone_lighting else None)
    clusters = light.effect_clusters if light else zones.clusters if zones else ()
    onboard = dev.feature(F_ONBOARD)
    mode = read_mode(dev, onboard) if onboard is not None else None
    host, notify = light.sw_state() if light else (None, None)
    return {
        "ok": True,
        "device": str(path),
        "name": keyboard_name(product),
        "product": f"{product:04x}",
        # A model not yet run on, whose note asks for a device report (report.untested).
        "untested": untested(product),
        "device_index": f"0x{index:02x}",
        "capabilities": keyboard_capabilities(dev, KEYBOARDS[product]),
        "onboard_mode": None if mode is None else MODE_NAMES.get(mode, mode),
        # get_brightness raises on a keyboard without 0x8040.
        "brightness": (light.get_brightness()
                       if light and light.brightness_levels else None),
        "brightness_levels": list(light.brightness_levels) if light else [],
        "sw_host": host,
        "sw_control": notify,
        "clusters": [
            {
                "index": cluster.index,
                "name": cluster.name,
                "location": cluster.location,
                "effects": [
                    {"index": e.index, "id": e.id, "name": e.name}
                    for e in cluster.effects
                ],
            }
            for cluster in clusters
        ],
    }


def cmd_probe(args: argparse.Namespace) -> int:
    """Read-only dump of what this firmware exposes. Needs no daemon.

    Refuses while the daemon runs: both processes see every input report on the
    node, and a reply is matched on feature, function and a four-bit software id
    each process numbers on its own, so the daemon can take probe's reply and act on it.
    A `--keyboard` that names no model is refused first, before the daemon is asked.
    """
    try:
        wanted = None if args.keyboard is None else key_named(args.keyboard, "--keyboard")
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not args.force:
        beside = _daemon_beside()
        if beside is not None:
            return beside
    opened = _open_keyboard(wanted)
    if opened is None:
        return 1
    path, product, node, index = opened
    try:
        with node as dev:
            found = _read_keyboard(dev, path, product, index)
    except OSError as exc:
        print(f"error: could not read {path}: {reason(exc)}", file=sys.stderr)
        return 1
    except UnreadableAnswer as exc:
        # A reply too short to decode, such as a short report's 3 bytes.
        print(f"error: the keyboard on {path} gave a lighting reply too short to read: {exc}",
              file=sys.stderr)
        return 1
    except HidppFailure as exc:
        # An error reply is an answer; silence, a lost node, or a receiver that cannot reach
        # the keyboard is not. A failure of the node itself is said in the system's words
        # alone, without the transport's "write to the device failed".
        refused = isinstance(exc, HidppError) and not exc.unreachable
        print(f"error: the keyboard on {path} "
              f"{'refused a request' if refused else 'stopped answering'}: "
              f"{reason(exc.__cause__ or exc)}",
              file=sys.stderr)
        return 1

    if args.json:
        print_json(found)
        return 0
    _print_probe(found)
    return 0


def cmd_keys(args: argparse.Namespace) -> int:
    """The names a binding may use (KEY_NAMES), or with --modifiers the modifiers; with
    --json each name with its code, or each modifier with the key it stands for."""
    if args.modifiers:
        if args.json:
            print_json(dict(sorted(MODIFIERS.items())))
            return 0
        print("modifiers: " + ", ".join(sorted(MODIFIERS)))
        print("\nPut one before a key, `gearbench-ctl bind G1 ctrl+f15`, or bind it alone, "
              "`gearbench-ctl bind G1 shift`, to hold it while the G key is held.",
              file=sys.stderr)
        return 0
    names = sorted(KEY_NAMES)
    if args.match:
        needle = args.match.upper()
        names = [n for n in names if needle in n]
    if args.json:
        print_json({name: KEY_NAMES[name] for name in names})
        return 0
    for name in names:
        print(f"{name:<28} {KEY_NAMES[name]}")
    print(f"\n{counted(len(names), 'key')}. Bind with e.g. "
          "`gearbench-ctl bind G1 ctrl+f15`.", file=sys.stderr)
    return 0


def cmd_bind(args: argparse.Namespace) -> int:
    """Bind a G key to a key spec or to `macro:<name>`, or a headset's to one of its actions
    too (config.HEADSET_ACTIONS).

    The spec is checked here, so a typo fails without a round trip. A macro name is
    checked for its form only: the daemon owns the library, and a binding may name a
    macro not yet written. A headset's key cleared does nothing, as a mouse button's does:
    unbound, a headset's G1 would run its default.
    """
    spec = None if args.binding.strip().lower() in CLEAR_WORDS else args.binding
    if spec is None and args.key in HEADSET_KEYS:
        spec = "none"
    if spec is not None:
        try:
            # key_binding reads "" as unbound, as a file holds it; typed, "" is refused,
            # so an empty argument never clears a key.
            if key_binding(args.key, spec, "binding") is None:
                validate_binding(spec, "binding")
        except ConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    return print_reply(send({
        "command": "bind", "key": args.key, "value": spec, "shift": args.shift,
    }), args.json)


def speed_ranges() -> str:
    """The speeds each animation that takes one takes, in words: "breathe, cycle and
    wave 1000 to 20000, ripple 100 to 1000"."""
    groups: dict[tuple[int, int], list[str]] = {}
    for name, speeds in SPEEDS.items():
        groups.setdefault(speeds, []).append(name)
    return ", ".join(f"{word_list(names, 'and')} {fastest} to {slowest}"
                     for (fastest, slowest), names in groups.items())


def cmd_effect(args: argparse.Namespace) -> int:
    """Send the effect; a speed, direction or second color left out keeps what the profile
    saved. The daemon checks each."""
    request: dict = {"command": "effect", "value": args.name}
    if args.speed is not None:
        request["speed"] = args.speed
    if args.direction is not None:
        request["direction"] = args.direction
    if args.second_color is not None:
        request["second_color"] = args.second_color
    return print_reply(send(for_keyboard(args, request)), args.json, EFFECT_WORDS)


def add_lighting_and_bind(sub: Subcommands) -> None:
    # m-profile, where profiles and save-profile mean game profiles; profile, its name in
    # 1.1.0, still works (the owner's decision of 2026-10-22).
    p = sub.add("m-profile", help="switch the M profile, as M1 to M3 do", aliases=("profile",))
    p.add_argument("index", type=int, choices=PROFILE_IDS)
    p.set_defaults(func=lambda a: print_reply(send({"command": "profile", "value": a.index}),
                                              a.json, {"profile": m_profile_words},
                                              M_PROFILE_LABELS))

    b = keyboard_option(sub.add("brightness", help="set the brightness"))
    b.add_argument("level", type=int,
                   help="0 to 100, floored to a level the device keeps; the G915's are 0, 12, 25, "
                        "50 and 100")
    b.set_defaults(func=lambda a: print_reply(send(for_keyboard(
        a, {"command": "brightness", "value": a.level})), a.json, BRIGHTNESS_WORDS))

    # Where it is saved in effect's words: "the current profile" can be either kind.
    c = keyboard_option(sub.add("color", help="set the lighting color, saved to the loaded "
                                              "game profile or else the M profile"))
    # One placeholder for a color in every command that takes one, g432-lights' included.
    c.add_argument("value", metavar="#RRGGBB", help="six hex digits like #0080ff")
    c.set_defaults(func=lambda a: print_reply(send(for_keyboard(
        a, {"command": "color", "value": a.value})), a.json))

    pv = keyboard_option(sub.add("preview", help="repaint without saving, as a color drag does"))
    pv.add_argument("value", metavar="#RRGGBB", help="six hex digits like #0080ff")
    pv.set_defaults(func=lambda a: print_reply(send(for_keyboard(
        a, {"command": "preview", "value": a.value})), a.json))

    kf = sub.add("keyboard-follow",
                 help="on (the default): while another keyboard leads, this one shows its "
                      "lighting at its brightness; off: its own")
    kf.add_argument("state", choices=["on", "off"])
    kf.add_argument("--keyboard", metavar="MODEL|ID", required=True, help=KEYBOARD_NAMED)
    kf.set_defaults(func=lambda a: print_reply(send(
        {"command": "follow_leader", "keyboard": a.keyboard, "value": a.state == "on"}),
        a.json, {"follow_leader": on_off}))

    e = keyboard_option(sub.add("effect", help="set the lighting effect, saved to the loaded "
                                               "game profile or else the M profile"))
    e.add_argument("name", choices=sorted(KEYBOARD_EFFECTS),
                   help="one the keyboard has, which a refusal names")
    e.add_argument("--speed", type=int, metavar="MS",
                   help=f"how long one cycle takes, in milliseconds; lower is faster: "
                        f"{speed_ranges()}")
    # Each with the Direction row's words, so a reader of both sees one choice.
    directions = ", ".join(f"{name} ({words.lower()})" for name, words in DIRECTION_WORDS.items())
    e.add_argument("--direction", choices=list(DIRECTION_WORDS),
                   help=f"wave only, one the keyboard takes, which a refusal names: {directions}; "
                        "horizontal until one is chosen")
    e.add_argument("--second-color", metavar="#RRGGBB",
                   help=f"{word_list(TWO_COLORED, 'and')} only, the color it runs beside the "
                        f"one `color` sets; {format_color(DEFAULT_SECOND_COLOR)} until one is "
                        "chosen")
    e.set_defaults(func=cmd_effect)

    bind = sub.add("bind", help="bind a G key, e.g. `bind G1 ctrl+f15`")
    # One model's name may stand for several ids. Only where it acts: an untested model's
    # writes are refused, so its unbound G keys do what they do without Gearbench.
    silent = sorted({model.name for model in KEYBOARDS.values()
                     if model.reports_unbound and model.assignments_tested})
    # The headsets' keys that run something unbound, by what they run.
    defaults: dict[str, list[str]] = {}
    for key, action in HEADSET_DEFAULTS.items():
        defaults.setdefault(action, []).append(key)
    bind.add_argument("key", type=str.upper, choices=BINDABLE_KEYS, metavar="KEY",
                      help=f"{G_KEYS[0]} to {G_KEYS[-1]}, which do nothing unbound, or "
                           f"{own_keys_words('or')}, where an unbound one does what it does "
                           f"without Gearbench, or nothing on a {word_list(silent)}, but "
                           + word_list([f"{action} on {word_list(keys, 'and')}"
                                        for action, keys in defaults.items()]))
    bind.add_argument("binding",
                      help="a key spec like ctrl+shift+f5, a modifier alone like shift, "
                           "keys held together like a+b, 'macro:<name>' to run a macro, "
                           f"or {word_list(CLEAR_WORDS)} to clear; on a headset's key also "
                           f"{word_list(HEADSET_ACTIONS[1:])}")
    # --shift, its name in 1.1.0, still works.
    bind.add_argument("--mr", "--shift", dest="shift", action="store_true",
                      help="bind on the MR layer")
    bind.set_defaults(func=cmd_bind)


def add_leader(sub: Subcommands) -> None:
    sk = sub.add("sync-keyboard",
                 help="choose the keyboard model that leads; each other keyboard with "
                      "keyboard-follow on follows it, and so do the mice and a headset's "
                      "lights while mouse-sync and headset-sync are on")
    # MODEL|ID, as --keyboard and --mouse show the same kind of argument.
    sk.add_argument("model", metavar="MODEL|ID",
                    help="a keyboard model by name or product id, as G915 or c33e, or first "
                         "for the keyboard connected first since the daemon started")
    sk.set_defaults(func=lambda a: print_reply(send(
        {"command": "sync_keyboard",
         "value": None if a.model.strip().lower() == "first" else a.model}), a.json))


def add_probe_and_keys(sub: Subcommands) -> None:
    pr = keyboard_option(sub.add("probe",
                                 help="read-only dump of the keyboard, with the daemon stopped"),
                         "the first that answers")
    pr.add_argument("--force", action="store_true",
                    help="probe even while the daemon is running")
    pr.set_defaults(func=cmd_probe)

    k = sub.add("keys", help="list bindable key names")
    k.add_argument("match", nargs="?", help="filter, e.g. `keys volume`")
    k.add_argument("--modifiers", action="store_true",
                   help="list the modifiers, which go before a key or are bound alone")
    k.set_defaults(func=cmd_keys)
