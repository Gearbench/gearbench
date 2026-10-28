"""The subcommands for the daemon's own state: `status` and its report, `save` and
`reload`, the switches `lock-lights` and `tray`, `idle`, and `config`, which reads the
configuration on disk and needs no daemon.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Mapping
from functools import partial

from ..battery import kept_at
from ..config import (
    BINDABLE_KEYS,
    DEFAULT_BRIGHTNESS,
    F_ROW_KEYS,
    G_KEYS,
    HEADSET_KEYS,
    IDLE_DIM_AFTER,
    IDLE_OFF_AFTER,
    IDLE_RANGE,
    MODE_WORDS,
    PER_KEY,
    Config,
    Lighting,
    config_path,
    direction_shown,
    format_color,
    key_under,
    profile_label,
    speed_shown,
)
from ..daemon import BUSY, DOWN, NOT_REACHED, NOT_RUNNING, REPLY_UNREAD, UNREAD, failure
from ..focus import FOLLOWED, SHELL, held_until
from ..keyboard import (
    CABLED_IDS as KEYBOARD_CABLED,
    MODEL_OF as KEYBOARDS,
    followed as leader_shown,
    key_name as model_shown,
    model_ids as keyboard_ids,
    model_named as keyboard_named,
    untested_assignment_words,
)
from ..keycontrol import GAME_MODE_NOT_KNOWN, GAME_MODE_UNKNOWN, SHORTCUT_TURNS
from ..lighting import ANIMATIONS, COLORLESS, DARK, DIM, LEVEL_WORDS, SPEEDS, TWO_COLORED
from ..macros import REPEAT_WORDS, countdown_seconds
from ..mouse import (
    key_name as mouse_shown,
    lighting_shown_words,
    lighting_under_test,
    model_ids as mouse_ids,
)
from ..report import untested_note
from ..settings_files import ConfigError
from ..wording import NO_REASON, counted, escaped as _escaped, reason, route_of, word_list
from ..xfocus import BY_RUNNING_PROGRAMS
from .output import (
    ASLEEP_OR_OFF,
    M_PROFILE_LABELS,
    Subcommands,
    battery_words,
    device_words,
    on_off,
    print_block,
    print_json,
    print_reply,
    send,
    silent_note,
)


def tray_words(tray: dict) -> str:
    """The tray icon line of `status`: the switch, and why the icon does not show."""
    if not tray.get("on"):
        return "off"
    if tray.get("shown"):
        return "shown"
    return f"not shown: {tray.get('why') or 'the daemon has not started it'}"


def _tray_switch_words(tray: object) -> str | None:
    """The `tray` field of the tray switch's reply, worded as status's tray line."""
    return tray_words(tray) if isinstance(tray, dict) else None


def _claim_words(reply: dict) -> str:
    """The claim line of `status`: the keyboard's lighting claimed or not, its software
    control and, on a keyboard whose G keys 0x8010 diverts, whether they are diverted, or
    which keys Gearbench types while unbound on one whose G keys are keys of its own (the
    reply's `typed`)."""
    if not reply.get("claimed"):
        return "not claimed"
    held = [f"sw_control {reply['sw_control']}"] if reply.get("sw_control") is not None else []
    if "g_keys" not in (reply.get("capabilities") or ()):
        # None to divert: the G213 has no G keys, and the G515 generation's go through their
        # custom assignments, which the assignments line says.
        return ", ".join(["claimed", *held])
    keys = "G keys diverted" if reply.get("diverted") else "G keys not diverted"
    if reply.get("diverted") and reply.get("typed"):
        keys = f"{reply['typed']} diverted, typed by Gearbench while unbound"
    return ", ".join(["claimed", *held, keys])


def _assignment_words(reply: dict) -> str:
    """The custom assignments line of `status`: on or off, the shortcut that turns them the
    other way, then why its own keys take no binding yet, or the keys set to report to
    Gearbench and, while they are off, what those do meanwhile: on a model whose every G key
    reports (KeyboardModel.reports_unbound), its G keys in the model's own words."""
    on = bool(reply["custom_assignments"])
    said = "on" if on else "off"
    if reply.get("assignment_shortcut"):
        said += ", " + SHORTCUT_TURNS.format(reply["assignment_shortcut"], "off" if on else "on")
    keys = [str(key) for key in reply.get("reporting_keys") or ()]
    model = KEYBOARDS.get(int(reply["product"], 16)) if keys else None
    if reply.get("assignments_untested"):
        said += f"; {untested_assignment_words(int(reply['product'], 16))}"
    elif model is not None and model.reports_unbound:
        said += (f"; its {model.own_key}s report to Gearbench" if on
                 else f"; until then its {model.own_key}s {model.unbound}")
    elif keys and on:
        said += f"; {word_list(keys, 'and')} {'reports' if len(keys) == 1 else 'report'} to Gearbench"
    elif keys:
        said += (f"; until then {word_list(keys, 'and')} "
                 + ("types its own key" if len(keys) == 1 else "type their own keys"))
    return said


#: The Game Mode line of `status`, by the reply's `game_mode`.
GAME_MODE_WORDS = {"on": "on", "off": "off",
                   GAME_MODE_UNKNOWN: GAME_MODE_NOT_KNOWN}


def _level_words(level: object) -> str | None:
    """A lights level as `status` words it (LEVEL_WORDS)."""
    return LEVEL_WORDS.get(str(level))


def _lock_words(reply: dict) -> str:
    """The screen-lock line of `status`: the switch, and what it has done."""
    if not reply.get("lights_off_when_locked"):
        return "lights stay on (lock-lights off)"
    state = ("locked" if reply.get("session_locked")
             else "blank" if reply.get("screen_blanked")
             else "unlocked" if reply.get("session_locked") is False
             else "not known yet")
    return f"lights off when locked; {state}, lights {LEVEL_WORDS[reply['lights']]}"


def _idle_words(dim_after: object, off_after: object, never_dim: bool,
                level: object = None) -> str:
    """The idle line: the settings, then "idle now" where `level`, a reply's `idle_lights`, is
    dim or dark."""
    if never_dim:
        said = "never dims (never-dim on)"
    else:
        said = f"dims after {dim_after} s without a keypress, lights off {off_after} s after that"
    if level in (DIM, DARK):
        said += f"; idle now, {'dimmed' if level == DIM else 'lights off'}"
    return said


def _reply_idle_words(reply: dict) -> str:
    """The idle line of a daemon reply, `idle`'s or `status`'s: the settings, or on a keyboard
    that never dims (`dims` false, one lit through 0x8070's zones) that alone."""
    if reply.get("dims") is False:
        return f"never dims on the {reply.get('name') or 'keyboard'}"
    return _idle_words(reply.get("idle_dim_after"), reply.get("idle_off_after"),
                       bool(reply.get("never_dim")), reply.get("idle_lights"))


def cmd_idle(args: argparse.Namespace) -> int:
    """Show the idle dimming, or change what the options give."""
    request: dict = {"command": "idle"}
    if args.dim_after is not None:
        request["dim_after"] = args.dim_after
    if args.off_after is not None:
        request["off_after"] = args.off_after
    if args.never_dim is not None:
        request["never_dim"] = args.never_dim == "on"
    reply = send(request)
    if args.json or not reply.get("ok"):
        return print_reply(reply, args.json)
    print(f"idle: {_escaped(_reply_idle_words(reply))}")
    if reply.get("note"):
        print(f"note: {_escaped(reply['note'])}")
    return 0


def _focus_words(focus: dict) -> str:
    """The focus line of `status`: what game profiles follow, and the focused program."""
    source = focus.get("source")
    if source not in FOLLOWED:
        return ("not followed, no X server or Gearbench's GNOME Shell extension: "
                + BY_RUNNING_PROGRAMS)
    program = focus.get("program")
    if not program:
        return ("no program: the desktop or the Shell" if source == SHELL
                else "no X11 program: the desktop or a native Wayland window")
    profile = focus.get("profile")
    return f"{program}, which loads {profile}" if profile else \
        f"{program}, which no game profile names"


def _game_profile_words(reply: dict, followed: bool) -> str:
    """The game profile line of `status`: the one loaded, what loaded it, one held off.
    `followed` is whether game profiles follow focus."""
    overlay = reply.get("overlay")
    # Keep loaded holds only a profile loaded by hand; a program's goes when the
    # program exits or loses focus.
    if not overlay:
        shown = "none"
    elif reply.get("overlay_auto"):
        program = reply.get("auto_process") or "its program"
        shown = (f"{overlay}, {program} has focus" if followed
                 else f"{overlay}, loaded for {program}")
    else:
        shown = overlay + (", kept loaded" if reply.get("keep_loaded") is True else "")
    held = reply.get("held_off")
    if isinstance(held, dict) and held.get("profile"):
        # A program's profile replaced by hand while the program ran or had focus.
        program = held.get("process") or "its program"
        shown += f"; {held['profile']} held off until {held_until(program, followed)}"
    return shown


def _macro_words(reply: dict) -> str:
    """The macro line of `status`: the macro running or about to, and its repeat."""
    macro, starts_in = reply.get("macro"), reply.get("macro_starts_in")
    if not macro:
        return "none running"
    repeat = reply.get("macro_repeat")
    # A value no mode has, as it came; looked up by str, since a reply's value can be any JSON.
    mode = REPEAT_WORDS.get(str(repeat), repeat)
    if starts_in:
        return f"{macro} starts in {countdown_seconds(starts_in)} s ({mode})"
    return f"{macro} is running ({mode})"


def _m_lighting_words(reply: dict) -> str:
    """The M profile's lighting in `status`: disabled, an animation, or else static or
    per-key, each with the color where it shows one, and Pulsar Point's two."""
    effect, color = reply.get("effect"), reply["color"]
    if effect == "disabled":
        return "disabled"
    if effect in TWO_COLORED:
        # A daemon that runs it gives its second color.
        return f"{effect} {color} and {reply['second_color']}"
    if effect in ANIMATIONS:
        return effect if effect in COLORLESS else f"{effect} {color}"
    if reply.get("mode") == PER_KEY:
        return f"{MODE_WORDS[PER_KEY]}, background {color}"
    return f"static {color}"


def _keyboard_notes(reply: dict, name: str) -> list[str]:
    """The lines under `status`'s `keyboard:` line for the keyboard named `name`: the note
    on a model not yet run on, then whether it is not answering (silent_note)."""
    return ([untested_note(name)] if reply.get("untested") else []) + (
        [] if reply.get("answering") else [silent_note(reply)])


def _other_keyboards(reply: dict) -> list[str]:
    """The lines under `status`'s keyboard lines for each keyboard driven beside the leader's
    (`keyboards_driven`, the leader's first): its name, whether it shows the leader's lighting
    or its own, and whether it is not answering."""
    driven = [each for each in reply.get("keyboards_driven") or () if isinstance(each, dict)]
    leader = str(driven[0].get("name")) if driven else ""
    return [f"{each.get('name')} driven too, "
            + (f"showing the {leader}'s lighting" if each.get("follows") else "its own lighting")
            + ("" if each.get("answering") else f"; {ASLEEP_OR_OFF}")
            for each in driven[1:]]


def _mouse_notes(reply: dict, name: str) -> list[str]:
    """The notes under `status`'s line of the mouse named `name`: the note on a model not yet
    run on, that its lighting is not yet tested, then whose lighting it shows while it waits to
    claim, its own or Gearbench's."""
    return ([untested_note(name)] if name in (reply.get("mouse_untested") or ()) else []) + (
        [lighting_under_test(name)] if name in (reply.get("mouse_lighting_untested") or ())
        else []) + [lighting_shown_words(name, str(each["shown"]))
                    for each in reply.get("mouse_lighting_shown") or ()
                    if each.get("name") == name]


#: A line of `status`, (label, value), as print_block takes it: a label of None for a line
#: under the one before.
StatusLine = tuple[str | None, object]


def _add(lines: list[StatusLine], label: str | None, value: object) -> None:
    lines.append((label, value))


def _attached(lines: list[StatusLine], label: str, entries: list | None,
              notes: Callable[[str], list[str]]) -> None:
    """`status`'s lines of each mouse or headset `entries` gives (shared.attached_entry),
    as the keyboard's: its device line under `label`, its `notes` and whether it is not
    answering under it, then its battery where it lists one; not attached for none."""
    listed = [each for each in entries or () if isinstance(each, dict)]
    if not listed:
        _add(lines, label, "not attached")
    for entry in listed:
        name = str(entry.get("name"))
        _add(lines, label, device_words(entry.get("device"), name, entry.get("route")))
        for note in (*notes(name),
                     *([silent_note(entry)] if entry.get("answering") is False else [])):
            _add(lines, None, note)
        if entry.get("battery_found"):
            # A device that does not answer gives its last reading, dated.
            _add(lines, "battery", battery_words(entry.get("battery"), kept_at(entry)))


def _listed(lines: list[StatusLine], label: str, values: list[str], none: str) -> None:
    """A `status` line that lists `values`: the first on the label's line and each other
    under it, or `none` for none."""
    _add(lines, label, values[0] if values else none)
    for value in values[1:]:
        _add(lines, None, value)


def cmd_status(args: argparse.Namespace) -> int:
    """What the daemon and the keyboard are doing, as a report in one label column
    (print_block); `--json` prints the reply."""
    reply = send({"command": "status"})
    if args.json or not reply.get("ok"):
        return print_reply(reply, args.json)
    lines: list[StatusLine] = []
    line, more = partial(_add, lines), partial(_add, lines, None)
    name = reply.get("name") or "keyboard"
    if reply.get("connected"):
        line("keyboard", device_words(reply.get("device"), name,
                                      route_of(reply.get("product"), KEYBOARD_CABLED)))
        for note in (*_keyboard_notes(reply, name), *_other_keyboards(reply)):
            more(note)
    else:
        # Not attached, as the mouse and headset lines say it.
        line("keyboard", "not attached")
    # The problems first after the keyboard, as the tray's menu and the window put them.
    _listed(lines, "problems", [p["message"] for p in reply.get("problems") or ()], "none")
    battery = reply.get("battery")
    if isinstance(battery, dict):
        # The voltage too: the percentage comes off a curve.
        volts = f" ({battery['volts']} V)" if battery.get("volts") is not None else ""
        # A keyboard that does not answer gives its last reading, dated as the window has it.
        line("battery", battery_words(battery, kept_at(reply)) + volts)
    if reply.get("battery_warned"):
        # Not shown once the notices are armed again (BatteryNotices.battery_rearmed).
        more(f"below {reply['battery_warned']}%: a notice was posted")
    if reply.get("profile") is not None:
        shown = f", {_m_lighting_words(reply)}" if reply.get("effect") else ""
        line("M profile", profile_label(reply["profile"], reply.get("profile_name", ""))
             + shown
                          + (", MR held" if reply.get("shifted") else ""))
    focus = reply.get("focus") or {}
    line("game profile", _game_profile_words(reply, focus.get("source") in FOLLOWED))
    line("focus", _focus_words(focus))
    # An attached keyboard without brightness levels, the G213 or one without lighting, refuses
    # `brightness`.
    line("brightness", f"none Gearbench can set on the {name}"
         if reply.get("connected") and not reply.get("brightness_levels")
         else f"{reply.get('brightness')}%")
    line("screen lock", _lock_words(reply))
    line("idle", _reply_idle_words(reply))
    # "tray icon", config's label and the switch's job.
    line("tray icon", tray_words(reply.get("tray") or {}))
    # "claim", the man page's lighting claim: the line says the G keys' diversion too, where
    # 0x8010 diverts G keys, and lighting elsewhere means colors and effects.
    line("claim", _claim_words(reply))
    if reply.get("custom_assignments") is not None:
        # The Keyboard tab's word; "assignments" alone could name the G keys' bindings.
        line("custom assignments", _assignment_words(reply))
    if reply.get("game_mode") in GAME_MODE_WORDS:
        line("game mode", GAME_MODE_WORDS[reply["game_mode"]])
    # What `effect` takes on the keyboard that leads, the one this report is of: its clusters'
    # effects that have a name.
    effects = reply.get("effects")
    if isinstance(effects, list) and effects:
        line("effects", word_list([str(name) for name in effects], "and"))
    if reply.get("last_apply_error"):
        line("last apply", reply["last_apply_error"])
    typing, pointing = reply.get("virtual_keyboard"), reply.get("virtual_mouse")
    line("uinput", "virtual keyboard and mouse ready" if typing and pointing
         else "virtual keyboard ready, no virtual mouse" if typing
         else "virtual mouse ready, no virtual keyboard" if pointing
         else "none ready")
    # Each device as the keyboard is given, in the tray's order.
    _attached(lines, "mouse", reply.get("mice_attached"), partial(_mouse_notes, reply))
    # A mouse without lighting, such as the G305, has no color to say.
    if not reply.get("mouse") or reply.get("mouse_lights"):
        line("mouse color", f"follows the {leader_shown(reply.get('sync_leader'))}"
             if reply.get("mouse_sync_color") else "its own color")
    _attached(lines, "headset", reply.get("headsets_attached"), lambda _name: [])
    line("macro", _macro_words(reply))
    line("daemon", f"pid {reply.get('pid')}")
    print_block(lines)
    return 0


def cmd_reload(args: argparse.Namespace) -> int:
    """Re-read the files; the M profile then in force as status gives it, "M profile: M2 ·
    Desk", in place of the reply's `profile` and `profile_name`, which --json keeps."""
    reply = send({"command": "reload"})
    if args.json or not reply.get("ok") or "profile" not in reply:
        return print_reply(reply, args.json)
    shown = {**reply, "profile": profile_label(reply["profile"],
                                               str(reply.get("profile_name") or ""))}
    return print_reply(shown, False, labels=M_PROFILE_LABELS,
                       hidden=frozenset({"profile_name"}))


#: What _say_daemon_state prints for a daemon it could not ask, by `failure`'s kind; for
#: UNKNOWN it names the cause. A BUSY daemon, mid flash write say, still has live state.
#: DOWN and BUSY in the window's words for each state (NOT_RUNNING, worker.NOT_IN_TIME).
LIVE_STATE = {
    DOWN: f"        ({NOT_RUNNING}, so this is the whole picture)",
    BUSY: "        (the daemon did not answer in time, so its live state may differ from this)",
    UNREAD: f"        ({REPLY_UNREAD}, so its live state may differ from this)",
}


def _say_daemon_state(active: int) -> None:
    """Print, under config's source line, whether a running daemon's state may differ from
    the file's, whose M profile is `active`; nothing where the daemon is on that profile."""
    try:
        live = send({"command": "status"})
    except (OSError, json.JSONDecodeError) as exc:
        print(LIVE_STATE.get(failure(exc))
              or f"        ({NOT_REACHED}: {reason(exc)}; if one is "
                 f"running, its live state may differ from this)")
    else:
        if not live.get("ok"):
            print(f"        (the daemon did not give its state: "
                  f"{_escaped(live.get('error', NO_REASON))}; its live state may "
                  f"differ from this)")
        elif "profile" in live and live["profile"] != active:
            # config.json unreadable at the daemon's start: its save refuses to write over
            # the file (Daemon.save_refused), so only reload is named.
            save = ("" if live.get("save_refused")
                    else "; `gearbench-ctl save` writes the daemon's over it")
            print(
                f"        note: the daemon is on M{_escaped(live['profile'])} and this "
                f"file says M{active}. `gearbench-ctl reload` takes the file's{save}."
            )


def _entry_model(entries: Mapping[str, object], key: str,
                 ids: Callable[[str], tuple[str, ...]], shown: Callable[[str], str]) -> str:
    """The model `config` names an entry kept under `key` for. Under another of the model's
    ids, written by hand, that id follows, with "not read" where the daemon reads another
    id's entry in its place (config.key_under)."""
    model = ids(key)
    if model[0] == key:
        return shown(key)
    read = key_under(entries, model) == key
    return f"{shown(model[0])} ({key}{'' if read else ', not read'})"


def _keyboard_entry(entries: Mapping[str, object], key: str) -> str:
    return _entry_model(entries, key, keyboard_ids, model_shown)


def _mouse_entry(entries: Mapping[str, object], key: str) -> str:
    return _entry_model(entries, key, mouse_ids, mouse_shown)


def lighting_words(light: Lighting) -> str:
    """One keyboard model's lighting as `config` prints it."""
    # The mode and key count, as `profiles` gives them: under static the keys are stored, not
    # shown. The background is stored apart from the Static color.
    background = (f", background {format_color(light.background)}"
                  if light.background != light.color else "")
    # The running animation's speed, where it takes one, and wave's direction: saved, or the
    # default. Other animations' saved speeds stay in the file. Pulsar Point's second color
    # after its first.
    settings = light.settings()
    runs = ""
    if light.effect in SPEEDS:
        runs = f" at {speed_shown(light.effect, settings)} ms"
        if light.effect == "wave":
            runs += f" {direction_shown(settings)}"
    second = (f" and {format_color(light.second_color)}" if light.effect in TWO_COLORED
              else "")
    return (f"{_escaped(light.effect)}{runs} {format_color(light.color)}{second}, "
            f"mode {MODE_WORDS[light.mode]}, {counted(len(light.keys), 'key')}{background}")


def cmd_config(args: argparse.Namespace) -> int:
    try:
        config = Config.load()
    except ConfigError as exc:
        # A key or speed name in the message comes from the file as it is.
        print(f"error: {_escaped(exc)}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: {_escaped(config_path())} could not be read ({reason(exc)})",
              file=sys.stderr)
        return 1
    if args.json:
        print_json(config.to_json())
        return 0

    path = config_path()
    # HOME or XDG_CONFIG_HOME can hold a byte that is not UTF-8, which a UTF-8 stdout refuses.
    shown = _escaped(path)
    print(f"source: {shown}" if path.exists()
          else f"source: built-in defaults ({shown} does not exist yet)")
    # A running daemon's state may be unsaved; the file's, shown alone, reads as live.
    _say_daemon_state(config.active)
    print()
    # Another of a model's ids, written by hand, leads as that model (PerKeyboard.chosen); an
    # id no model has is already in its name.
    leader = config.sync_keyboard
    leader_key = keyboard_ids(leader)[0] if leader else None
    print_block((
        # As the window, the tray and a refusal name it (profile_label).
        ("M profile", profile_label(config.active, config.profile.name)),
        # By keyboard model; with none saved, the level every keyboard starts at.
        ("brightness", ", ".join(f"{level}% on the {_keyboard_entry(config.brightness, model)}"
                                 for model, level in sorted(config.brightness.items()))
         or f"{DEFAULT_BRIGHTNESS}%"),
        ("mouse color",
         "follows the keyboard that leads" if config.mouse_sync_color else "its own"),
        ("headset color",
         "follows the keyboard that leads" if config.headset_sync_color else "its own"),
        # The window's Leader row and the docs' word; the config key stays sync_keyboard.
        ("leader", "the one connected first" if leader_key is None
         else f"{model_shown(leader_key)} ({leader})"
         if keyboard_named(leader_key) is not None else model_shown(leader_key)),
        ("DPI notices", "on, at a DPI button's step" if config.dpi_notices else "off"),
        ("host mode", "on, the buttons through the button spy while the daemon runs"
         if config.mouse_host_mode else "off, each mouse on its onboard profile"),
        ("lights off when locked", on_off(config.lights_off_when_locked)),
        ("tray icon", on_off(config.tray)),
        ("idle", _idle_words(config.idle_dim_after, config.idle_off_after, config.never_dim))))
    for index, profile in sorted(config.profiles.items()):
        marker = "*" if index == config.active else " "
        print(f"\n{marker} {_escaped(profile_label(index, profile.name))}")
        for model, light in sorted(profile.lighting.items()):
            print(f"    lighting on the {_keyboard_entry(profile.lighting, model)}: "
                  f"{lighting_words(light)}")
        if not profile.lighting:
            print("    lighting: none saved")
        # A model's own keys (OWN_KEYS) run to dozens, each a `-` line while unbound: only a
        # bound one is listed.
        listed = [*G_KEYS, *(k for k in BINDABLE_KEYS
                             if k not in G_KEYS and (profile.keys[k] or profile.shift[k]))]
        # At least F12's width, so a file that binds no key with a longer name lines up as
        # it did before those were bindable.
        width = max(len(key) for key in [*listed, F_ROW_KEYS[-1]])
        for key in listed:
            base = _escaped(profile.keys[key] or "-")
            shift = _escaped(profile.shift[key] or "-")
            # MR: the binding on the MR layer, as the Keyboard tab names it; a headset's key
            # has none.
            print(f"    {key:<{width}} {base}" if key in HEADSET_KEYS
                  else f"    {key:<{width}} {base:<18} MR: {shift}")
        # Each mouse model's own; one with none takes its factory bindings.
        for model, entry in sorted(profile.mouse.items()):
            print(f"    mouse on the {_mouse_entry(profile.mouse, model)}: "
                  + "  ".join(f"{b.upper()}={_escaped(spec)}" for b, spec in entry.items()))
        if not profile.mouse:
            print("    mouse: each model's factory bindings")
    return 0


def add_status(sub: Subcommands) -> None:
    sub.add("status", help="show daemon and device state; --json for every field").set_defaults(
        func=cmd_status)


def add_save_and_reload(sub: Subcommands) -> None:
    # Names the files, as the man page does: save sits beside save-profile and headset-save.
    sub.add("save", help="write the daemon's live configuration to config.json and a loaded "
                         "game profile to its file, over a hand edit").set_defaults(
        func=lambda a: print_reply(send({"command": "save"}), a.json))
    sub.add("reload", help="re-read the configuration from disk").set_defaults(
        func=cmd_reload)


def add_switches_and_idle(sub: Subcommands) -> None:
    sub.switch("lock-lights", command="lock_lights", state="lights_off_when_locked",
               words={"lights": _level_words},
               help="on (the default): the lights go off while the screen is locked or "
                    "blank and before a suspend; off: they stay on")

    sub.switch("tray", command="tray", words={"tray": _tray_switch_words},
               help="on (the default): a top-bar icon with batteries, problems, DPI stages, "
                    "game profiles and switches, where there is a tray (on GNOME, Ubuntu's "
                    "AppIndicators extension); off: none")

    low, high = IDLE_RANGE
    idl = sub.add("idle", help="show or set the keyboard's idle dimming: it dims to half after "
                               "--dim-after seconds without a keypress (default "
                               f"{IDLE_DIM_AFTER}), and its lights go off --off-after seconds "
                               f"later (default {IDLE_OFF_AFTER})")
    idl.add_argument("--dim-after", type=int, metavar="SECONDS",
                     help=f"seconds without a keypress before it dims, {low} to {high}")
    idl.add_argument("--off-after", type=int, metavar="SECONDS",
                     help=f"seconds after the dim before the lights go off, {low} to {high}")
    idl.add_argument("--never-dim", choices=["on", "off"],
                     help="on: never dim or turn off")
    idl.set_defaults(func=cmd_idle)


def add_config(sub: Subcommands) -> None:
    sub.add("config", help="show the configuration on disk").set_defaults(
        func=cmd_config)
