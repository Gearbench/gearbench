"""The mouse's subcommands: `mouse` and its report, the choice of mouse, the DPI, the
report rate and the DPI stages, the writes to onboard profile 1, the bindings of the selected
mouse's buttons, the color and the effect, and the switches for color sync, DPI notices and
host mode. Each that acts on one mouse takes `--mouse` for another than the selected one.
"""

from __future__ import annotations

import argparse
import sys
from functools import partial

from ..battery import kept_at
from ..config import (
    DEFAULT_COLOR,
    MOUSE_ACTIONS,
    MOUSE_BUTTONS,
    REPORT_RATES,
    format_color,
    mouse_button_name,
)
from ..lighting import EFFECT_IDS, has_color
from ..mouse import (
    CABLED_IDS,
    DPI_WRITE_WAIT,
    EFFECT_NAMES,
    OWN_LIGHTING,
    PROFILE_SECTOR,
    PROFILE_WRITE_WAIT,
    WIDEST_DPI_RANGE,
    HostState,
    host_state,
    host_why,
    partly_enabled,
    profile_jobs,
    profile_words,
    stuck_words,
    switch_refusal_words,
)
from ..onboard import DPI_STAGES, crc_words, stage_dpi, stages_words
from ..report import untested_note
from ..sensor import range_words, reply_range
from ..wording import NOT_READ, escaped as _escaped, route_of, word_list
from .output import (
    CLEAR_WORDS,
    RELEASE_ALONE,
    Subcommands,
    battery_words,
    continued,
    device_words,
    labeled,
    print_reply,
    send,
    silent_note,
    with_unit,
)

#: The width of `mouse`'s label column: "programmable buttons: " is the longest.
MOUSE_LABELS = 22
#: A line of `mouse`'s report in that column, and one under the line before it.
_line, _more = partial(labeled, MOUSE_LABELS), partial(continued, MOUSE_LABELS)
#: `--mouse`'s help on a command that acts on one attached mouse.
MOUSE_HELP = ("the mouse to act on, by model name or product id, as `mouse` lists them; "
              "the selected one when left out")
#: Its help on mouse-bind and mouse-factory-bindings, which take a model attached or not.
MODEL_HELP = ("the mouse model, by name or product id, attached or not; the selected "
              "mouse's when left out")


def mouse_option(parser: argparse.ArgumentParser,
                 help: str = MOUSE_HELP) -> argparse.ArgumentParser:
    """`parser` with `--mouse`, which `for_mouse` sends."""
    parser.add_argument("--mouse", metavar="MODEL|ID", help=help)
    return parser


def for_mouse(args: argparse.Namespace, request: dict) -> dict:
    """`request` for the mouse `--mouse` names, as `device`; without one, for the selected
    mouse, as it is."""
    return request if args.mouse is None else {**request, "device": args.mouse}


def cmd_mouse(args: argparse.Namespace) -> int:
    reply = send(for_mouse(args, {"command": "mouse"}))
    if args.json or not reply.get("ok"):
        return print_reply(reply, args.json)
    # Only what this model can do: another line is noise or names a write the daemon
    # refuses for it (ProfileWrites.no_button_table).
    able = set(reply.get("capabilities") or ())
    host = reply.get("host") or {}
    onboard = reply.get("onboard") or {}
    _print_readings(reply, able, host)
    if onboard:
        # The daemon's rule (switch_refusal_words); `flash_refused` is a sentence where the
        # model's writes are not tested (Mouse.flash_refusal).
        _print_onboard(onboard, host, reply.get("answering") is not False,
                       switched=switch_refusal_words(
                           not isinstance(reply.get("flash_refused"), str),
                           onboard.get("crc_ok") is not False) is None)
    game = reply.get("game_profile")
    if isinstance(game, dict) and (game.get("edits") or game.get("dpi_stages")
                                   or game.get("report_rate")):
        _line("game profile", _game_stages_words(game))
    if "buttons" in able:
        _print_buttons(reply, onboard, host)
    return 0


def _print_readings(reply: dict, able: set[str], host: dict) -> None:
    """The head of `gearbench-ctl mouse`: the device, the note on a model not yet run on
    (report.untested_note), whether it answers, the mice to select from, the battery, the
    mode, and what `able` holds of DPI, report rate and lighting."""
    battery = reply.get("battery") or {}
    rng = reply_range(reply.get("dpi_range"))
    # The route in the window's words, as on every device line.
    _line("device", device_words(reply["device"], reply.get("name", ""),
                                 route_of(reply.get("product"), CABLED_IDS)))
    if reply.get("untested"):
        _more(untested_note(reply.get("name", "")))
    # Behind its receiver a switched-off mouse stays attached, and so does a sleeping
    # one that answered late (an untouched G502 X PLUS can take 1.6 s). The readings
    # below are the daemon's last.
    if reply.get("answering") is False:
        _more(silent_note(reply))
    # The mice `mouse-select` chooses from.
    devices = reply.get("devices") or []
    if len(devices) > 1:
        _line("mice", ", ".join(
            f"{d.get('name')} ({d.get('product')}"
            f"{', not answering' if d.get('answering') is False else ''})"
            f"{' *' if d.get('selected') else ''}"
            for d in devices))
    if battery:
        # A mouse that reports its battery by itself was last heard at battery_at, not
        # read_at (MouseBatteries.take_report).
        _line("battery", battery_words(battery, kept_at(reply)))
    _line("mode", _host_words(host, reply.get("answering") is not False))
    # A reading not taken yet, or one the mouse refused, is None (Mouse.describe).
    if "dpi" in able:
        dpi = reply.get("dpi")
        _line("DPI", f"{NOT_READ if dpi is None else dpi}"
                     + (f"  ({range_words(rng)})" if rng else ""))
    if "report_rate" in able:
        rate, rates = reply.get("report_rate"), reply.get("report_rates") or []
        _line("report rate", f"{NOT_READ if rate is None else f'{rate} Hz'}"
                             + (f"  of {', '.join(str(r) for r in rates)}" if rates else ""))
    if "lighting" in able:
        _line("lighting", f"{', '.join(reply.get('clusters', []))}  "
                          f"effects: {', '.join(reply.get('effects', []))}")
        _line("color", _color_words(reply))


def _color_words(reply: dict) -> str:
    """The `color:` line of `gearbench-ctl mouse`. An effect with no color in its layout leaves
    `color` as it was, so none is named for it."""
    color, effect = reply.get("color"), reply.get("effect")
    if reply.get("sync_color"):
        # A loaded game profile's own mouse color stands in for the keyboard's
        # (Daemon.synced_color).
        shown, game = reply.get("profile_mouse_color"), reply.get("game_profile") or {}
        return ("follows the keyboard that leads" if not isinstance(shown, str)
                else f"follows the keyboard that leads, in {game.get('name')}'s mouse color "
                     f"{shown}")
    if reply.get("lighting_shown") == OWN_LIGHTING and color is None and effect is None:
        # A mouse that waits to claim shows its own lighting while no color or effect is set
        # for it (claim_waits, release_lighting). One owed a color it has not been claimed
        # for keeps that color.
        return "its own lighting, until Gearbench sends a color or effect"
    # The model's own effect names, where it has them, by the reply's product id.
    own = EFFECT_NAMES.get(int(reply["product"], 16), {}) if reply.get("product") else {}
    if (isinstance(effect, str) and (effect in EFFECT_IDS or effect in own.values())
            and not has_color(effect, own)):
        return f"its own, {effect}"
    shown = (f"its own, last set to {color}" if color
             else "its own, not set since the daemon started")
    return shown + (f" ({effect})" if effect and effect != "static" else "")


def _print_onboard(onboard: dict, host: dict, answering: bool, *, switched: bool) -> None:
    """The lines of `gearbench-ctl mouse` on onboard profile 1: what its flash holds, the
    profile the mouse runs, and its DPI stages; `answering` False for a mouse the daemon marks
    silent, `switched` False for one the daemon never switches to profile 1
    (Mouse.switch_refusal), which goes back to the profile it ran before host mode."""
    mode = onboard.get("mode")
    mode = NOT_READ if mode is None else mode
    stages = onboard.get("dpi_stages") or []
    default = stage_dpi(stages, onboard.get("default_dpi_index", 0))
    shift = stage_dpi(stages, onboard.get("shift_dpi_index", 0))
    if onboard.get("flash_unknown"):
        # What is shown below was read before the failed write.
        # The Stages and rate row's words (onboard_card.PROFILE_UNKNOWN).
        _line("onboard", f"mode {mode}, profile 1 not known: a write failed and it could not "
                         "be read back. Leave the mouse on; see RECOVERY in gearbench-ctl(1)")
    else:
        # None for a rate byte other than 1, 2, 4 or 8 (ProfileImage.report_rate).
        rate = onboard.get("report_rate")
        _line("onboard", f"mode {mode}, profile 1 stored at "
                         f"{f'{rate} Hz' if rate else 'an unknown report rate'}, "
                         f"{crc_words(bool(onboard.get('crc_ok')))}")
    # Under another profile, profile 1 shown here is not in effect; the daemon switches back
    # at attach and power-on where `switched`. A mouse left in host mode (_host_words'
    # "stuck there") is in status's mouse_left_in_host; one not answering is not, and its
    # switch waits for it.
    running = onboard.get("running_profile")
    _line("running", (
        NOT_READ if not isinstance(running, int)
        else "onboard profile 1" if running == PROFILE_SECTOR
        else f"{profile_words(running)}; "
             + ("profile 1 runs" if switched else "the profile it ran before host mode runs again")
             + " when Gearbench stops"
        if host_state(host, answering) in (HostState.DRIVEN, HostState.UNTIL_ANSWERED,
                                           HostState.NOT_ARMED)
        else f"{profile_words(running)}, not profile 1: profile 1 as shown is not "
             f"in effect; `gearbench-ctl status` says why"))
    # stage_dpi gives None only where there is no stage.
    _line("DPI stages", stages_words(stages, default, shift) if stages else "none")


def _print_buttons(reply: dict, onboard: dict, host: dict) -> None:
    """The lines of `gearbench-ctl mouse` on the buttons: what profile 1 holds for the model's
    buttons and every entry of its button table, the event nodes G4 to G9 are read from,
    where the model's are read, and the bindings of the model's buttons, under its labels.
    Where those bindings do nothing now (the reply's `button_table.idle`), why, on the
    bindings' line, and each button's job in profile 1 in place of its binding."""
    table = reply.get("button_table") or {}
    idle = table.get("idle")
    # The Mouse tab's Programmable buttons row: "buttons" alone would name the bindings too.
    _line("programmable buttons",
          _buttons_words(reply, host_state(host, True) is HostState.DRIVEN))
    for i, entry in enumerate(onboard.get("buttons") or []):
        print(f"  [{i:2}] {_escaped(entry)}")
    if table.get("event_node"):
        # Only a mouse whose buttons are read has its event node opened.
        inputs, denied = reply.get("input") or [], reply.get("input_denied")
        _line("evdev", ", ".join(inputs) if inputs
              else f"cannot read {denied}" if denied
              else "not found (mouse off?)")
    bindings = reply.get("bindings") or {}
    rows = [(str(b.get("name")), str(b.get("label")), b.get("onboard"))
            for b in table.get("buttons") or []]
    if idle:
        _line("bindings", idle)
        print("in profile 1:")
        for _name, label, job in rows:
            print(f"  {_escaped(label):<22} {_escaped(job or NOT_READ)}")
    elif bindings:
        print("bindings:")
        for name, label, _job in rows:
            print(f"  {_escaped(label):<22} {_escaped(bindings.get(name, 'none'))}")


def _buttons_words(reply: dict, spy: bool) -> str:
    """The `buttons:` line of `gearbench-ctl mouse`: what profile 1 holds for G4 to G9, and the
    write to run. With `spy` (HostState.DRIVEN) the spy brings all six whatever profile 1
    holds, and profile 1 is what the mouse does without Gearbench. On a model whose writes to
    profile 1 the daemon refuses (the reply's `flash_refused`), which keeps the profile it ran
    before in place of profile 1 (hand_back), no write is offered: each of the model's buttons
    and its job in profile 1, and why. Where the daemon refuses the enable write (the
    reply's `enable_refused`), no hint names it."""
    refused = reply.get("flash_refused")
    if isinstance(refused, str):
        # The daemon's sentence (mouse.untested_words), with the model's name in it. The words
        # below name the G502 X PLUS's G4 to G9, which another model's table need not have.
        jobs = profile_jobs(reply.get("button_table"))
        holds = f"profile 1: {jobs}" if jobs else "profile 1 not read"
        return ("through the button spy (host mode); " if spy else "") + f"{holds}. {refused}"
    enabled = reply.get("enabled_buttons") or []
    fallback = bool(reply.get("buttons_fallback"))
    if fallback:
        state = "fallback (G4 to G8 as from the factory, G9 nothing)"
        hint = "; their bindings need `gearbench-ctl mouse-enable-buttons`"
    elif reply.get("buttons_enabled"):
        state, hint = "enabled (G4 to G9 are mouse buttons 11 to 16 in profile 1)", ""
    elif enabled:
        state = partly_enabled(enabled)
        hint = "; run `gearbench-ctl mouse-enable-buttons` again"
    else:
        state, hint = "not enabled", "; run `gearbench-ctl mouse-enable-buttons`"
    if isinstance(reply.get("enable_refused"), str):
        hint = ""
    if spy:
        return (f"through the button spy (host mode); profile 1: {state}"
                + ("" if fallback else "; `gearbench-ctl mouse-fallback-buttons` for use without "
                                       "Gearbench"))
    return state + hint


def _game_stages_words(game: dict) -> str:
    """The `game profile:` line of `gearbench-ctl mouse`: the loaded game profile's own DPI
    stages and report rate, whether they are in effect, and in host mode what is in effect in
    place of those it lacks: profile 1's, or the rate kept for the model (the reply's
    `kept_rate`)."""
    name, stages, rate = game.get("name"), game.get("dpi_stages"), game.get("report_rate")
    kept = game.get("kept_rate") is not None
    own = []
    if stages:
        own.append(stages_words(stages, game.get("default"), game.get("shift")))
    if rate:
        own.append(f"{rate} Hz")
    if not own:
        if not game.get("applies"):
            # Outside host mode the mouse runs its onboard profile, and no kept rate is sent
            # (Dpi.tune).
            return f"{name} has none of its own; mouse-dpi-stages and mouse-rate give it its own"
        return (f"{name} has none of its own, so "
                + ("profile 1's stages and the rate kept for the model are" if kept
                   else "profile 1's are")
                + " in effect; mouse-dpi-stages and mouse-rate give it its own")
    said = f"{name}'s own {'; '.join(own)}"
    if not game.get("applies"):
        return f"{said}; not in effect: Gearbench is not driving the mouse in host mode"
    rest = "the rate kept for the model" if kept and not rate else "profile 1's"
    return f"{said}; in effect" + ("" if stages and rate else f", {rest} for the rest")


#: The `mode:` line of `gearbench-ctl mouse` for each HostState that takes nothing more from
#: the reply.
HOST_WORDS = {
    HostState.DRIVEN: ("host (Gearbench drives the buttons through the button spy; the onboard "
                       "profile takes over when it stops)"),
    HostState.NO_HOST: "onboard (Gearbench has no host mode for this mouse)",
    HostState.SWITCHED_OFF: "onboard (host mode is off: `gearbench-ctl mouse-host on`)",
    HostState.NOT_YET: "onboard until the mouse answers, then host",
    HostState.NOT_ARMED: "host until the mouse answers, then host mode is set up",
}


def _host_words(host: dict, answering: bool) -> str:
    """The `mode:` line of `gearbench-ctl mouse`, from the reply's `host`; `answering` False
    for a mouse the daemon marks silent."""
    state = host_state(host, answering)
    why = host_why(bool(host.get("wanted")), host.get("error"))
    if state is HostState.UNTIL_ANSWERED:
        # Its switch went unanswered, not refused: the daemon makes it at the mouse's
        # next setup, and its mouse_left_in_host leaves out a mouse not answering.
        return (f"host until the mouse answers ({why}), then "
                + ("host mode is tried again" if host.get("wanted") else "onboard"))
    if state is HostState.LEFT_THERE:
        # The daemon's mouse_left_in_host: its switch back to ONBOARD mode was refused, or
        # answered and not taken.
        return f"host, stuck there ({why}): {stuck_words()}"
    if state is HostState.REFUSED:
        return f"onboard: {why}"
    return HOST_WORDS[state]


def _written_words(value: object) -> str | None:
    """Where a write of the stages or the rate went, as the reply's `written` gives it, in
    words: "game profile" for game_profile."""
    return value.replace("_", " ") if isinstance(value, str) else None


#: The labels of a stages write's default and shift, which are DPI values.
STAGE_LABELS = {"default": "default stage", "shift": "shift stage"}


def _button_words(value: object) -> str | None:
    """A mouse button as config, the refusals and the Mouse tab name it, "G4"; --json keeps
    the reply's "g4"."""
    return value.upper() if isinstance(value, str) else None


def _button_list_words(value: object) -> str | None:
    """A reply's list of mouse buttons, "G4, G5", or "-" for none."""
    if not isinstance(value, list):
        return None
    return ", ".join(str(button).upper() for button in value) or "-"


def _bindings_words(value: object) -> str | None:
    """A reply's mouse buttons and their bindings, "G4=back, G5=forward", or "-" for none."""
    if not isinstance(value, dict):
        return None
    return ", ".join(f"{str(button).upper()}={binding}"
                     for button, binding in value.items()) or "-"

def cmd_mouse_write(args: argparse.Namespace, request: dict) -> int:
    """A command that can write onboard profile 1 to the mouse's flash, which takes longer than
    send_command's default wait."""
    dpi = with_unit("DPI")
    return print_reply(send(for_mouse(args, request), timeout=PROFILE_WRITE_WAIT), args.json,
                       {"written": _written_words, "default": dpi, "shift": dpi,
                        "enabled_buttons": _button_list_words}, STAGE_LABELS)


def cmd_mouse_select(args: argparse.Namespace) -> int:
    """Select a mouse, named in the text output as a mouse write's reply names the mouse it
    went to (`mouse`): "G502 X PLUS (4099)"."""
    reply = send({"command": "mouse_select", "value": args.value})
    if args.json or not reply.get("ok") or "name" not in reply or "selected" not in reply:
        return print_reply(reply, args.json)
    return print_reply({"ok": True, "mouse": f"{reply['name']} ({reply['selected']})"}, False)


def cmd_mouse_dpi_stages(args: argparse.Namespace) -> int:
    listed = args.stages is not None or args.default is not None or args.shift is not None
    if args.factory:
        if args.reset or listed:
            print("error: --factory takes no stages, --default, --shift or --remove-own",
                  file=sys.stderr)
            return 1
        return cmd_mouse_write(args, {"command": "mouse_dpi_stages", "factory": True})
    if args.reset:
        if listed:
            print("error: --remove-own takes no stages, --default or --shift", file=sys.stderr)
            return 1
        return cmd_mouse_write(args, {"command": "mouse_dpi_stages", "reset": True})
    if args.stages is None:
        print("error: give the stages, like 800,1600,3200, --remove-own or --factory",
              file=sys.stderr)
        return 1
    try:
        # int() allows space around a stage, not inside: "800 1600" is an error, not 8001600.
        stages = [int(s) for s in args.stages.split(",") if s.strip()]
    except ValueError:
        print(f"error: {args.stages!r} is not a list like 800,1600,3200", file=sys.stderr)
        return 1
    if not 1 <= len(stages) <= DPI_STAGES:
        print(f"error: give 1 to {DPI_STAGES} stages, not {len(stages)}",
              file=sys.stderr)
        return 1
    for option, value in (("--default", args.default), ("--shift", args.shift)):
        if value is not None and value < WIDEST_DPI_RANGE[0]:
            print(f"error: {option} {value}: that is a DPI, not a stage number, and no "
                  f"sensor goes under {WIDEST_DPI_RANGE[0]}", file=sys.stderr)
            return 1
    return cmd_mouse_write(args, {"command": "mouse_dpi_stages", "value": stages,
                                  "default": args.default, "shift": args.shift})


def cmd_mouse_rate(args: argparse.Namespace) -> int:
    if args.reset and args.value is not None:
        print("error: --remove-own takes no rate", file=sys.stderr)
        return 1
    if not args.reset and args.value is None:
        print("error: give a rate, or --remove-own", file=sys.stderr)
        return 1
    request = ({"command": "mouse_rate", "reset": True} if args.reset
               else {"command": "mouse_rate", "value": args.value})
    return cmd_mouse_write(args, request)


def cmd_mouse_effect(args: argparse.Namespace) -> int:
    """An effect with its color on the mouse, or with `--release` its own lighting back."""
    if args.release and (args.name is not None or args.color is not None):
        print(f"error: {RELEASE_ALONE}", file=sys.stderr)
        return 1
    if not args.release and args.name is None:
        print("error: give an effect, or --release", file=sys.stderr)
        return 1
    request = ({"command": "mouse_effect", "release": True} if args.release
               else {"command": "mouse_effect", "value": args.name,
                     **({} if args.color is None else {"color": args.color})})
    return print_reply(send(for_mouse(args, request)), args.json)


def _mouse_button(text: str) -> str:
    name = mouse_button_name(text)
    if name is None:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a mouse button; they are "
            + word_list([b.upper() for b in MOUSE_BUTTONS], "and")
        )
    return name


def cmd_mouse_bind(args: argparse.Namespace) -> int:
    binding = "none" if args.binding.strip().lower() in CLEAR_WORDS else args.binding
    return print_reply(send(for_mouse(args, {"command": "mouse_bind", "key": args.button,
                                             "value": binding})), args.json,
                       {"key": _button_words})


def _host_mice_words(mice: object) -> str | None:
    """mouse-host's `mice`: each mouse host mode drives, on or off. Why one did not take is the
    reply's note alone: an answering mouse's `error` is in it, and a silent one's can be an
    earlier arm's, which the note leaves out (Mice._cmd_mouse_host_mode)."""
    if not isinstance(mice, list):
        return None
    return "; ".join(f"{m.get('name')}: host {'on' if m.get('host') else 'off'}"
                     for m in mice if isinstance(m, dict)) or "-"


def add_mouse(sub: Subcommands) -> None:
    mouse_option(sub.add("mouse", help="show the selected mouse's state")).set_defaults(func=cmd_mouse)

    # "the selected mouse" as its siblings say it, and live as the GUIDE does.
    md = mouse_option(sub.add("mouse-dpi", help="set the selected mouse's DPI live"))
    md.add_argument("value", type=int)
    md.set_defaults(func=lambda a: print_reply(send(for_mouse(
        a, {"command": "mouse_dpi", "value": a.value}), timeout=DPI_WRITE_WAIT), a.json))

    mr = mouse_option(sub.add("mouse-rate",
                       help="set the report rate in Hz: written to onboard profile 1, or "
                            "with a game profile loaded in host mode, saved to that profile, or in "
                            "host mode on a mouse whose onboard profile Gearbench does not "
                            "write, live and kept for its model, sent again at each setup in "
                            "host mode"))
    mr.add_argument("value", type=int, nargs="?", choices=REPORT_RATES)
    # --reset, its name in 1.1.0, still works.
    mr.add_argument("--remove-own", "--reset", dest="reset", action="store_true",
                    help="the loaded game profile's own rate goes, and profile 1's is used, "
                         "or, on a mouse whose onboard profile Gearbench does not write, "
                         "the rate kept for the model where one is kept")
    mr.set_defaults(func=cmd_mouse_rate)

    ms = mouse_option(sub.add("mouse-dpi-stages",
                       help="set the DPI stages, e.g. 800,1600,3200: written to onboard "
                            "profile 1, or with a game profile loaded in host mode, saved to "
                            "that profile"))
    ms.add_argument("stages", nargs="?",
                    help="up to five DPI values, comma separated, written lowest first")
    ms.add_argument("--default", type=int, metavar="DPI",
                    help="the default stage's DPI, one of the stages: where the mouse "
                         "starts on profile 1, or, with a game profile loaded in host mode, "
                         "at that profile's load until a DPI is chosen under it")
    ms.add_argument("--shift", type=int, metavar="DPI",
                    help="the DPI of the stage the DPI shift button drops to while held, "
                         "one of the stages")
    # --reset, its name in 1.1.0, still works.
    ms.add_argument("--remove-own", "--reset", dest="reset", action="store_true",
                    help="the loaded game profile's own stages go, and profile 1's are used")
    ms.add_argument("--factory", action="store_true",
                    help="the stages, default and shift in the mouse's ROM, in place of a "
                         "list")
    ms.set_defaults(func=cmd_mouse_dpi_stages)

    mouse_option(sub.add("mouse-enable-buttons",
                  help="rewrite onboard profile 1 so G4 to G9 send mouse buttons 11 to 16, "
                       "which the daemon binds (backs up first)")).set_defaults(
        func=lambda a: cmd_mouse_write(a, {"command": "mouse_enable_buttons"}))
    mouse_option(sub.add("mouse-fallback-buttons",
                  help="rewrite onboard profile 1 for use without Gearbench: G4 to G8 as from the "
                       "factory, G9 nothing (backs up first)")).set_defaults(
        func=lambda a: cmd_mouse_write(a, {"command": "mouse_fallback_buttons"}))
    mouse_option(sub.add("mouse-restore",
                  help="write the newest backup of onboard profile 1 back")).set_defaults(
        func=lambda a: cmd_mouse_write(a, {"command": "mouse_restore"}))

    mb = mouse_option(sub.add("mouse-bind",
                       help="bind a button of the selected mouse, for its model alone, e.g. "
                            "`mouse-bind G9 ctrl+f15`, `mouse-bind g6 dpi-shift` or "
                            "`mouse-bind G9 'macro:Reload'`"), MODEL_HELP)
    mb.add_argument("button", type=_mouse_button,
                    metavar="{" + ",".join(b.upper() for b in MOUSE_BUTTONS) + "}",
                    help="one of the selected mouse's buttons, as `mouse` lists them")
    mb.add_argument("binding",
                    help=f"one of {', '.join(a for a in MOUSE_ACTIONS if a != 'none')}, "
                         f"a key spec, macro:<name>, or {word_list(CLEAR_WORDS)} to clear it")
    mb.set_defaults(func=cmd_mouse_bind)

    # mouse-defaults, its name in 1.1.0, still works.
    mouse_option(sub.add("mouse-factory-bindings",
                  help="put the selected mouse's buttons back to their factory bindings",
                  aliases=("mouse-defaults",)),
                 MODEL_HELP).set_defaults(
        func=lambda a: print_reply(send(for_mouse(a, {"command": "mouse_bind_defaults"})),
                                   a.json, {"bindings": _bindings_words}))

    ms = sub.add("mouse-select",
                 help="choose the attached mouse the window shows and a mouse command "
                      "without --mouse acts on, kept across a restart")
    ms.add_argument("value", metavar="MODEL|ID",
                    help="a model name or product id, as `mouse` lists them")
    ms.set_defaults(func=cmd_mouse_select)

    mc = mouse_option(sub.add("mouse-color", help="set the mouse color"))
    mc.add_argument("value", metavar="#RRGGBB", help="six hex digits like #0080ff")
    mc.set_defaults(func=lambda a: print_reply(send(for_mouse(
        a, {"command": "mouse_color", "value": a.value})), a.json))

    me = mouse_option(sub.add("mouse-effect",
                              help="set the mouse zone effect, or with --release give the "
                                   "mouse its own lighting back"))
    me.add_argument("name", nargs="?", help="an effect name, as `mouse` lists them")
    me.add_argument("--color", metavar="#RRGGBB",
                    help="the effect's color, six hex digits; when left out, the color the "
                         f"mouse was last sent, or {format_color(DEFAULT_COLOR)} where it was "
                         "sent none")
    me.add_argument("--release", action="store_true",
                    help="on a mouse that shows its own lighting until it is sent a color, "
                         "its own lighting back and its color and effect forgotten")
    me.set_defaults(func=cmd_mouse_effect)

    # `mouse` is `mouse_lights` under its 1.1.0 name, kept for a script reading --json.
    sub.switch("mouse-sync", command="mouse_sync", state="mouse_sync_color",
               hidden=frozenset({"mouse"}),
               help="on: the mouse takes the keyboard's color (under Per-key, the "
                    "background), or a loaded game profile's mouse color, and dims with the "
                    "keyboard, and mouse-color and mouse-effect are refused; off: it goes back "
                    "to its own color or effect")


def add_mouse_switches(sub: Subcommands) -> None:
    sub.switch("dpi-notices", command="dpi_notices",
               help="on (the default): show the new DPI when a DPI button changes it; off: "
                    "none")

    sub.switch("mouse-host", command="mouse_host_mode", words={"mice": _host_mice_words},
               help="on (the default): Gearbench drives the buttons, stages and rate of each "
                    "mouse it has host mode for, and the onboard profile takes over when it "
                    "stops; off: each mouse runs its onboard profile, and a G502 X PLUS's G4 "
                    "to G9 need mouse-enable-buttons")
