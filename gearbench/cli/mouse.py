"""The mouse's subcommands: `mouse` and its report, the choice of mouse, the DPI, the
report rate and the DPI stages, the writes to onboard profile 1, the bindings of the selected
mouse's buttons, the color and the effect, and the switches for color sync, DPI notices and
host mode.
"""

from __future__ import annotations

import argparse
import sys

from ..config import MOUSE_ACTIONS, MOUSE_BUTTONS, mouse_button_name
from ..lighting import EFFECT_IDS, has_color
from ..mouse import (
    EFFECT_NAMES,
    PROFILE_SECTOR,
    PROFILE_WRITE_WAIT,
    WIDEST_DPI_RANGE,
    HostState,
    host_state,
    host_why,
    profile_jobs,
    profile_words,
    stuck_words,
    switch_refusal_words,
)
from ..onboard import DPI_STAGES, crc_words, stage_dpi
from ..profiles import REPORT_RATES
from ..report import untested_note
from ..wording import clock_shown, escaped as _escaped, word_list
from .output import CLEAR_WORDS, Subcommands, battery_words, print_reply, send


def cmd_mouse(args: argparse.Namespace) -> int:
    reply = send({"command": "mouse"})
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
        print(f"game stages: {_escaped(_game_stages_words(game))}")
    if "buttons" in able:
        _print_buttons(reply, onboard, host)
    return 0


def _print_readings(reply: dict, able: set[str], host: dict) -> None:
    """The head of `gearbench-ctl mouse`: the device, the note on a model not yet run on
    (report.untested_note), whether it answers, the mice to select from, the battery, the
    mode, and what `able` holds of DPI, report rate and lighting."""
    battery = reply.get("battery") or {}
    rng = reply.get("dpi_range") or []
    print(f"device:      {reply['device']}  {reply.get('name', '')}")
    if reply.get("untested"):
        print(f"             {untested_note(reply.get('name', ''))}")
    # Behind its receiver a switched-off mouse stays attached, and so does a sleeping
    # one that answered late (an untouched G502 X PLUS can take 1.6 s). The readings
    # below are the daemon's last.
    if reply.get("answering") is False:
        read = reply.get("read_at")
        print("             not answering: asleep or switched off"
              + (f"; the readings below are from {clock_shown(read)}"
                 if isinstance(read, int | float) else ""))
    # The mice `mouse-select` chooses from.
    devices = reply.get("devices") or []
    if len(devices) > 1:
        print("mice:        " + ", ".join(
            f"{d.get('name')} ({d.get('product')}){' *' if d.get('selected') else ''}"
            for d in devices))
    if battery:
        # A mouse that reports its battery by itself was last heard at battery_at, not
        # read_at (Mice.take_battery_report); the headset line dates its battery the same way.
        at = reply.get("battery_at")
        dated = (f", as of {clock_shown(at)}"
                 if reply.get("answering") is False and isinstance(at, int | float) else "")
        print(f"battery:     {battery_words(battery)}{dated}")
    print(f"mode:        {_host_words(host, reply.get('answering') is not False)}")
    # A reading not taken yet, or one the mouse refused, is None (Mouse.describe).
    if "dpi" in able:
        dpi = reply.get("dpi")
        print(f"dpi:         {'not read' if dpi is None else dpi}"
              + (f"  (range {rng[0]}-{rng[1]} step {rng[2]})" if len(rng) == 3 else ""))
    if "report_rate" in able:
        rate, rates = reply.get("report_rate"), reply.get("report_rates") or []
        print(f"report rate: {'not read' if rate is None else f'{rate}Hz'}"
              + (f"  of {', '.join(str(r) for r in rates)}" if rates else ""))
    if "lighting" in able:
        print(f"lighting:    {', '.join(reply.get('clusters', []))}  "
              f"effects: {', '.join(reply.get('effects', []))}")
        print(f"color:       {_color_words(reply)}")


def _color_words(reply: dict) -> str:
    """The `color:` line of `gearbench-ctl mouse`: synced, or the mouse's own color and the
    effect last set. An effect with no color in its layout leaves `color` as it was, so
    none is named for it."""
    color, effect = reply.get("color"), reply.get("effect")
    if reply.get("sync_color"):
        return "synced with the keyboard"
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
    mode = "not read" if mode is None else mode
    stages = onboard.get("dpi_stages") or []
    default = stage_dpi(stages, onboard.get("default_dpi_index", 0))
    shift = stage_dpi(stages, onboard.get("shift_dpi_index", 0))
    if onboard.get("flash_unknown"):
        # What is shown below was read before the failed write.
        print(f"onboard:     mode {mode}, profile 1 in flash unknown: a "
              f"write failed and it could not be read back. Leave the mouse on; "
              f"see RECOVERY in gearbench-ctl(1)")
    else:
        # None for a rate byte other than 1, 2, 4 or 8 (ProfileImage.report_rate).
        rate = onboard.get("report_rate")
        print(f"onboard:     mode {mode}, profile 1 stored at "
              f"{f'{rate}Hz' if rate else 'an unknown report rate'}, "
              f"{crc_words(bool(onboard.get('crc_ok')))}")
    # Under another profile, profile 1 shown here is not in effect; the daemon switches back
    # at attach and power-on where `switched`. A mouse left in host mode (_host_words'
    # "stuck there") is in status's mouse_left_in_host; one not answering is not, and its
    # switch waits for it.
    running = onboard.get("running_profile")
    print("running:     " + (
        "not read" if not isinstance(running, int)
        else "onboard profile 1" if running == PROFILE_SECTOR
        else f"{profile_words(running)}; "
             + ("profile 1 runs" if switched else "the profile it ran before host mode runs again")
             + " when Gearbench stops"
        if host_state(host, answering) in (HostState.DRIVEN, HostState.UNTIL_ANSWERED,
                                           HostState.NOT_ARMED)
        else f"{profile_words(running)}, not profile 1: profile 1 as shown is not "
             f"in effect; `gearbench-ctl status` says why"))
    # stage_dpi gives None only where there is no stage.
    listed = ", ".join(str(s) for s in stages)
    print(f"dpi stages:  {listed}  default {default}  shift {shift}" if stages
          else "dpi stages:  none")


def _print_buttons(reply: dict, onboard: dict, host: dict) -> None:
    """The lines of `gearbench-ctl mouse` on the buttons: what profile 1 holds for the model's
    buttons and every entry of its button table, the event nodes G4 to G9 are read from,
    where the model's are read, and the bindings of the model's buttons, under its labels.
    Where those bindings do nothing now (the reply's `button_table.idle`), why, and each
    button's job in profile 1 in place of its binding."""
    table = reply.get("button_table") or {}
    idle = table.get("idle")
    print("buttons:     " + (_escaped(idle) if idle else
                             _buttons_words(reply, host_state(host, True) is HostState.DRIVEN)))
    for i, entry in enumerate(onboard.get("buttons") or []):
        print(f"  [{i:2}] {entry}")
    if table.get("event_node"):
        # Only a mouse whose buttons are read has its event node opened.
        inputs, denied = reply.get("input") or [], reply.get("input_denied")
        print("evdev:       " + (", ".join(inputs) if inputs
                                 else _escaped(f"cannot read {denied}") if denied
                                 else "not found (mouse off?)"))
    bindings = reply.get("bindings") or {}
    rows = [(str(b.get("name")), str(b.get("label")), b.get("onboard"))
            for b in table.get("buttons") or []]
    if idle:
        print("in profile 1:")
        for _name, label, job in rows:
            print(f"  {_escaped(label):<22} {_escaped(job or 'not read')}")
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
        return (("through the button spy (host mode); " if spy else "")
                + _escaped(f"{holds}. {refused}"))
    enabled = reply.get("enabled_buttons") or []
    fallback = bool(reply.get("buttons_fallback"))
    if fallback:
        state = "fallback (G4-G8 as from the factory, G9 nothing)"
        hint = "; their bindings need `gearbench-ctl mouse-enable-buttons`"
    elif reply.get("buttons_enabled"):
        state, hint = "enabled (G4-G9 are mouse buttons 11-16 in profile 1)", ""
    elif enabled:
        state = f"partly enabled ({', '.join(b.upper() for b in enabled)} only)"
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
    """The `game stages:` line of `gearbench-ctl mouse`: the loaded game profile's own DPI
    stages and report rate, whether they are in force, and in host mode what is in force in
    place of those it lacks: profile 1's, or the rate kept for the model (the reply's
    `kept_rate`)."""
    name, stages, rate = game.get("name"), game.get("dpi_stages"), game.get("report_rate")
    kept = game.get("kept_rate") is not None
    own = []
    if stages:
        own.append(f"{', '.join(str(s) for s in stages)}  default {game.get('default')}  "
                   f"shift {game.get('shift')}")
    if rate:
        own.append(f"{rate}Hz")
    if not own:
        if not game.get("applies"):
            # Outside host mode the mouse runs its onboard profile, and no kept rate is sent
            # (Dpi.tune).
            return f"{name} has none of its own; mouse-dpi-stages and mouse-rate give it its own"
        return (f"{name} has none of its own, so "
                + ("profile 1's stages and the rate kept for the model are" if kept
                   else "profile 1's are")
                + " in force; mouse-dpi-stages and mouse-rate give it its own")
    said = f"{name}'s own {'; '.join(own)}"
    if not game.get("applies"):
        return f"{said}; not in force: Gearbench is not driving the mouse in host mode"
    rest = "the rate kept for the model" if kept and not rate else "profile 1's"
    return f"{said}; in force" + ("" if stages and rate else f", {rest} for the rest")


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
        # The daemon's mouse_left_in_host: its switch back to ONBOARD mode was refused.
        return f"host, stuck there ({why}): {stuck_words()}"
    if state is HostState.REFUSED:
        return f"onboard: host mode did not take: {host['error']}"
    return HOST_WORDS[state]


def cmd_mouse_write(args: argparse.Namespace, request: dict) -> int:
    """A command that can write onboard profile 1 to the mouse's flash, which takes longer than
    send_command's default wait."""
    return print_reply(send(request, timeout=PROFILE_WRITE_WAIT), args.json)


def cmd_mouse_dpi_stages(args: argparse.Namespace) -> int:
    listed = args.stages is not None or args.default is not None or args.shift is not None
    if args.factory:
        if args.reset or listed:
            print("error: --factory takes no stages, --default, --shift or --reset",
                  file=sys.stderr)
            return 1
        return cmd_mouse_write(args, {"command": "mouse_dpi_stages", "factory": True})
    if args.reset:
        if listed:
            print("error: --reset takes no stages, --default or --shift", file=sys.stderr)
            return 1
        return cmd_mouse_write(args, {"command": "mouse_dpi_stages", "reset": True})
    if args.stages is None:
        print("error: give the stages, like 800,1600,3200, --reset or --factory",
              file=sys.stderr)
        return 1
    try:
        # int() allows space around a stage, not inside: "800 1600" is an error, not 8001600.
        stages = [int(s) for s in args.stages.split(",") if s.strip()]
    except ValueError:
        print(f"error: {args.stages!r} is not a list like 800,1600,3200", file=sys.stderr)
        return 1
    if not 1 <= len(stages) <= DPI_STAGES:
        print(f"error: give between 1 and {DPI_STAGES} stages, not {len(stages)}",
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
        print("error: --reset takes no rate", file=sys.stderr)
        return 1
    if not args.reset and args.value is None:
        print("error: give a rate, or --reset", file=sys.stderr)
        return 1
    request = ({"command": "mouse_rate", "reset": True} if args.reset
               else {"command": "mouse_rate", "value": args.value})
    return cmd_mouse_write(args, request)


def _mouse_button(text: str) -> str:
    name = mouse_button_name(text)
    if name is None:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a mouse button; they are "
            + ", ".join(b.upper() for b in MOUSE_BUTTONS)
        )
    return name


def cmd_mouse_bind(args: argparse.Namespace) -> int:
    binding = "none" if args.binding.strip().lower() in CLEAR_WORDS else args.binding
    return print_reply(send({"command": "mouse_bind", "key": args.button,
                              "value": binding}), args.json)


def add_mouse(sub: Subcommands) -> None:
    sub.add("mouse", help="show the selected mouse's state").set_defaults(func=cmd_mouse)

    md = sub.add("mouse-dpi", help="set mouse DPI")
    md.add_argument("value", type=int)
    md.set_defaults(func=lambda a: print_reply(send({"command": "mouse_dpi", "value": a.value}), a.json))

    mr = sub.add("mouse-rate",
                 help="set the report rate in Hz: saved to onboard profile 1, or "
                      "with a game profile loaded in host mode, to that profile, or in host "
                      "mode on a mouse whose onboard profile Gearbench does not write, live "
                      "and kept for its model, sent again at each setup in host mode")
    mr.add_argument("value", type=int, nargs="?", choices=REPORT_RATES)
    mr.add_argument("--reset", action="store_true",
                    help="the loaded game profile's own rate goes, and profile 1's is used, "
                         "or, on a mouse whose onboard profile Gearbench does not write, "
                         "the rate kept for the model where one is kept")
    mr.set_defaults(func=cmd_mouse_rate)

    ms = sub.add("mouse-dpi-stages",
                 help="set the DPI stages, e.g. 800,1600,3200: saved to onboard "
                      "profile 1, or with a game profile loaded in host mode, to "
                      "that profile")
    ms.add_argument("stages", nargs="?", help="up to five DPI values, comma separated")
    ms.add_argument("--default", type=int, metavar="DPI",
                    help="the default stage's DPI, one of the stages: where the mouse "
                         "starts on profile 1, or, with a game profile loaded in host mode, "
                         "at that profile's load until a DPI is chosen under it")
    ms.add_argument("--shift", type=int, metavar="DPI",
                    help="the DPI of the stage the paddle drops to, one of the stages")
    ms.add_argument("--reset", action="store_true",
                    help="the loaded game profile's own stages go, and profile 1's are used")
    ms.add_argument("--factory", action="store_true",
                    help="the stages, default and shift in the mouse's ROM, in place of a "
                         "list")
    ms.set_defaults(func=cmd_mouse_dpi_stages)

    sub.add("mouse-enable-buttons",
            help="rewrite onboard profile 1 so G4-G9 send mouse buttons 11-16, "
                 "which the daemon binds (backs up first)").set_defaults(
        func=lambda a: cmd_mouse_write(a, {"command": "mouse_enable_buttons"}))
    sub.add("mouse-fallback-buttons",
            help="rewrite onboard profile 1 for use without Gearbench: G4-G8 as from the "
                 "factory, G9 nothing (backs up first)").set_defaults(
        func=lambda a: cmd_mouse_write(a, {"command": "mouse_fallback_buttons"}))
    sub.add("mouse-restore",
            help="write the newest backup of onboard profile 1 back").set_defaults(
        func=lambda a: cmd_mouse_write(a, {"command": "mouse_restore"}))

    mb = sub.add("mouse-bind",
                 help="bind a button of the selected mouse, for its model alone, e.g. "
                      "`mouse-bind G9 ctrl+f15`, `mouse-bind g6 dpi-shift` or "
                      "`mouse-bind G9 'macro:Reload'`")
    mb.add_argument("button", type=_mouse_button,
                    metavar="{" + ",".join(b.upper() for b in MOUSE_BUTTONS) + "}",
                    help="one of the selected mouse's buttons, as `mouse` lists them")
    mb.add_argument("binding",
                    help=f"one of {', '.join(a for a in MOUSE_ACTIONS if a != 'none')}, "
                         f"a key spec, macro:<name>, or {word_list(CLEAR_WORDS)} to clear it")
    mb.set_defaults(func=cmd_mouse_bind)

    sub.add("mouse-defaults",
            help="put the selected mouse's buttons back to their model's default "
                 "bindings").set_defaults(
        func=lambda a: print_reply(send({"command": "mouse_bind_defaults"}), a.json))

    ms = sub.add("mouse-select",
                 help="choose which attached mouse the mouse commands act on")
    ms.add_argument("value", help="a model name or product id, as `mouse` lists them")
    ms.set_defaults(func=lambda a: print_reply(
        send({"command": "mouse_select", "value": a.value}), a.json))

    mc = sub.add("mouse-color", help="set the mouse color")
    mc.add_argument("value", help="six hex digits like #0080ff")
    mc.set_defaults(func=lambda a: print_reply(send({"command": "mouse_color", "value": a.value}), a.json))

    me = sub.add("mouse-effect", help="set the mouse zone effect")
    me.add_argument("name", help="an effect name, as `mouse` lists them")
    me.add_argument("--color", default="#0080ff",
                    help="the effect's color, six hex digits; #0080ff when left out")
    me.set_defaults(func=lambda a: print_reply(send(
        {"command": "mouse_effect", "value": a.name, "color": a.color}), a.json))

    sub.switch("mouse-sync", command="mouse_sync",
               help="on: the mouse takes the keyboard's color (in Per-key mode, the "
                    "background) and dims with it, and mouse-color and mouse-effect are "
                    "refused; off: it keeps its own color")

    sk = sub.add("sync-keyboard",
                 help="choose the keyboard model the daemon drives with two plugged in, "
                      "switching at once, which the mouse follows and a keyboard with no "
                      "lighting or brightness of its own starts from")
    sk.add_argument("model", help="a keyboard model by name or id, as G915 or c33e, or first "
                                  "for the keyboard connected first since the daemon started")
    sk.set_defaults(func=lambda a: print_reply(send(
        {"command": "sync_keyboard",
         "value": None if a.model.strip().lower() == "first" else a.model}), a.json))

    sub.switch("dpi-notices", command="dpi_notices",
               help="on (the default): show the new DPI when a DPI button changes it; off: "
                    "none")

    sub.switch("mouse-host", command="mouse_host_mode",
               help="on (the default): Gearbench drives the buttons, stages and rate of each "
                    "mouse it has host mode for, and the onboard profile takes over when it "
                    "stops; off: each mouse runs its onboard profile, and a G502 X PLUS's G4 "
                    "to G9 need mouse-enable-buttons")
