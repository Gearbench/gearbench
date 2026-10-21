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

from ..astro import PRODUCTS as A50_PRODUCTS
from ..config import (
    DEFAULT_BRIGHTNESS,
    F_ROW_KEYS,
    G_KEYS,
    IDLE_DIM_AFTER,
    IDLE_OFF_AFTER,
    IDLE_RANGE,
    PER_KEY,
    Config,
    ConfigError,
    Lighting,
    config_path,
    direction_shown,
    format_color,
    key_under,
    speed_shown,
)
from ..daemon import BUSY, DOWN, UNREAD, failure
from ..focus import FOLLOWED, SHELL, held_until
from ..keyboard import (
    CABLED_IDS as KEYBOARD_CABLED,
    key_name as model_shown,
    model_ids as keyboard_ids,
    model_named as keyboard_named,
)
from ..lighting import ANIMATIONS, COLORLESS, DARK, DIM, LEVEL_WORDS
from ..mouse import (
    key_name as mouse_shown,
    lighting_shown_words,
    lighting_under_test,
    model_ids as mouse_ids,
)
from ..report import untested_note
from ..wording import counted, escaped as _escaped, reason, word_list
from ..xfocus import BY_RUNNING_PROGRAMS
from .output import (
    Subcommands,
    battery_words,
    continued,
    labeled,
    print_json,
    print_reply,
    send,
    tray_words,
)

#: The width of `status`'s label column: "game profile: " is the longest.
STATUS_LABELS = 14
#: The A50's names in `status`'s headsets, which its own line words as the base station.
A50_NAMES = frozenset(A50_PRODUCTS.values())


def _claim_words(reply: dict) -> str:
    """The lighting line of `status`: the keyboard's lighting claimed or not, its software
    control and its G keys, which Gearbench types while unbound on a keyboard whose G keys
    are keys of its own (the reply's `typed`)."""
    if not reply.get("claimed"):
        return "not claimed"
    held = [f"sw_control {reply['sw_control']}"] if reply.get("sw_control") is not None else []
    keys = "G keys diverted" if reply.get("diverted") else "G keys not diverted"
    if reply.get("diverted") and reply.get("typed"):
        keys = f"{reply['typed']} diverted, typed by Gearbench while unbound"
    return ", ".join(["claimed", *held, keys])


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
        said = f"dims after {dim_after} s without a key, lights off {off_after} s after that"
    if level in (DIM, DARK):
        said += f"; idle now, {'dimmed' if level == DIM else 'lights off'}"
    return said


def _reply_idle_words(reply: dict) -> str:
    """The idle line of a daemon reply: `idle`'s or `status`'s."""
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
    print(f"idle: {_reply_idle_words(reply)}")
    if reply.get("note"):
        print(f"note: {reply['note']}")
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
    return f"{program}, bound to {profile}" if profile else \
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
    if starts_in:
        return f"{macro} starts in {starts_in:g} s ({reply.get('macro_repeat')})"
    return f"{macro} is running ({reply.get('macro_repeat')})"


def _m_lighting_words(reply: dict) -> str:
    """The M profile's lighting in `status`: disabled, an animation, or else static or
    per-key, each with the color where it shows one."""
    effect, color = reply.get("effect"), reply["color"]
    if effect == "disabled":
        return "disabled"
    if effect in ANIMATIONS:
        return effect if effect in COLORLESS else f"{effect} {color}"
    if reply.get("mode") == PER_KEY:
        return f"per-key, background {color}"
    return f"static {color}"


def _keyboard_notes(reply: dict, name: str) -> list[str]:
    """The lines under `status`'s `keyboard:` line for the keyboard named `name`: the note
    on a model not yet run on, then whether it is not answering."""
    return ([untested_note(name)] if reply.get("untested") else []) + (
        [] if reply.get("answering") else ["not answering: asleep or switched off"])


def _mouse_notes(reply: dict) -> list[str]:
    """The lines under `status`'s `mouse:` line: each attached mouse of a model not yet run
    on, each whose lighting is under test, then whose lighting each one that waits to claim
    shows, its own or Gearbench's."""
    return ([untested_note(str(name)) for name in reply.get("mouse_untested") or ()]
            + [lighting_under_test(str(name)) for name in reply.get("mouse_lighting_untested") or ()]
            + [lighting_shown_words(str(each["name"]), str(each["shown"]))
               for each in reply.get("mouse_lighting_shown") or ()])


def _headset_lines(reply: dict) -> list[str]:
    """The headset lines of `status`: the A50 as its base station, then every other headset
    by its name."""
    return (["base station attached"] if reply.get("headset") else []) + [
        f"{name} attached" for name in reply.get("headsets") or () if name not in A50_NAMES]


def _listed(label: str, values: list[str], none: str) -> None:
    """A `status` line that lists `values`: the first on the label's line and each other
    under it, or `none` for none."""
    labeled(STATUS_LABELS, label, values[0] if values else none)
    for value in values[1:]:
        continued(STATUS_LABELS, value)


def cmd_status(args: argparse.Namespace) -> int:
    """What the daemon and the keyboard are doing, as a report; `--json` prints the reply."""
    reply = send({"command": "status"})
    if args.json or not reply.get("ok"):
        return print_reply(reply, args.json)
    line, more = partial(labeled, STATUS_LABELS), partial(continued, STATUS_LABELS)
    name = reply.get("name") or "keyboard"
    if reply.get("connected"):
        product = reply.get("product")
        route = ("" if not product else " on its cable" if int(product, 16) in KEYBOARD_CABLED
                 else " through its receiver")
        line("keyboard", f"{name}{route}, {reply.get('device')}")
        for note in _keyboard_notes(reply, name):
            more(note)
    else:
        line("keyboard", "not found")
    battery = reply.get("battery")
    if isinstance(battery, dict):
        # The voltage too: the percentage comes off a curve.
        volts = f"  ({battery['volts']} V)" if battery.get("volts") is not None else ""
        line("battery", battery_words(battery) + volts)
    if reply.get("battery_warned"):
        # Not shown once the notices are armed again (BatteryNotices.battery_rearmed).
        more(f"below {reply['battery_warned']}%: a notice was posted")
    if reply.get("profile") is not None:
        shown = f", {_m_lighting_words(reply)}" if reply.get("effect") else ""
        line("M profile", f"M{reply['profile']} {reply.get('profile_name', '')}{shown}"
                          + (", MR held" if reply.get("shifted") else ""))
    focus = reply.get("focus") or {}
    line("game profile", _game_profile_words(reply, focus.get("source") in FOLLOWED))
    line("focus", _focus_words(focus))
    line("brightness", f"{reply.get('brightness')}%")
    line("screen lock", _lock_words(reply))
    line("idle", _reply_idle_words(reply))
    line("tray", tray_words(reply.get("tray") or {}))
    line("lighting", _claim_words(reply))
    # What `effect` takes on the attached keyboard: its clusters' effects that have a name.
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
    # A mouse without lighting, such as the G305, has no color to say.
    mouse = reply.get("mouse")
    line("mouse", ("attached" if mouse else "not attached")
         + ("" if mouse and not reply.get("mouse_lights")
            else ", color synced with the keyboard" if reply.get("mouse_sync_color")
            else ", its own color"))
    for note in _mouse_notes(reply):
        more(note)
    _listed("headset", _headset_lines(reply), "not attached")
    line("macro", _macro_words(reply))
    _listed("problems", [p["message"] for p in reply.get("problems") or ()], "none")
    line("daemon", f"pid {reply.get('pid')}")
    return 0


#: What _say_daemon_state prints for a daemon it could not ask, by `failure`'s kind; for
#: UNKNOWN it names the cause. A BUSY daemon, mid flash write say, still has live state.
LIVE_STATE = {
    DOWN: "        (no daemon running, so this is the whole picture)",
    BUSY: "        (the daemon did not answer, so its live state may differ from this)",
    UNREAD: "        (the daemon's reply could not be read, so its live state may differ "
            "from this)",
}


def _say_daemon_state(active: int) -> None:
    """Print, under config's source line, whether a running daemon's state may differ from
    the file's, whose M profile is `active`; nothing where the daemon is on that profile."""
    try:
        live = send({"command": "status"})
    except (OSError, json.JSONDecodeError) as exc:
        print(LIVE_STATE.get(failure(exc))
              or f"        (could not reach the daemon: {reason(exc)}; if one is "
                 f"running, its live state may differ from this)")
    else:
        if not live.get("ok"):
            print(f"        (the daemon did not give its state: "
                  f"{_escaped(live.get('error', 'unknown error'))}; its live state may "
                  f"differ from this)")
        elif "profile" in live and live["profile"] != active:
            print(
                f"        note: the daemon is on profile {live['profile']} and this "
                f"file says {active}. `gearbench-ctl reload` takes the file's; "
                f"`gearbench-ctl save` writes the daemon's over it."
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
    # The running animation's speed, and wave's direction: saved, or the default. Other
    # animations' saved speeds stay in the file.
    settings = light.settings()
    runs = ""
    if light.effect in ANIMATIONS:
        runs = f" at {speed_shown(light.effect, settings)} ms"
        if light.effect == "wave":
            runs += f" {direction_shown(settings)}"
    return (f"{_escaped(light.effect)}{runs} {format_color(light.color)}, "
            f"mode {_escaped(light.mode)}, {counted(len(light.keys), 'key')}{background}")


def cmd_config(args: argparse.Namespace) -> int:
    try:
        config = Config.load()
    except ConfigError as exc:
        # A key or speed name in the message comes from the file as it is.
        print(f"error: {_escaped(exc)}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: cannot read {config_path()}: {reason(exc)}", file=sys.stderr)
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
    print(f"active profile: {config.active} ({_escaped(config.profile.name)})")
    # By keyboard model; with none saved, the level every keyboard starts at.
    print("brightness:     " + (", ".join(f"{level} on the "
                                             f"{_keyboard_entry(config.brightness, model)}"
                                             for model, level in sorted(config.brightness.items()))
                                   or str(DEFAULT_BRIGHTNESS)))
    print(f"mouse color:    {'synced with the keyboard' if config.mouse_sync_color else 'its own'}")
    # Another of a model's ids, written by hand, leads as that model (PerKeyboard.chosen); an
    # id no model has is already in its name.
    leader = config.sync_keyboard
    leader_key = keyboard_ids(leader)[0] if leader else None
    print("sync keyboard:  " + ("the one connected first" if leader_key is None
                                else f"{model_shown(leader_key)} ({leader})"
                                if keyboard_named(leader_key) is not None
                                else model_shown(leader_key)))
    notices = "on, at a DPI button's step" if config.dpi_notices else "off"
    print(f"dpi notices:    {notices}")
    print("mouse host:     " + ("on, the buttons through the button spy while the daemon runs"
                                if config.mouse_host_mode
                                else "off, each mouse on its onboard profile"))
    print(f"lock lights:    {'off while locked' if config.lights_off_when_locked else 'always on'}")
    print(f"tray icon:      {'on' if config.tray else 'off'}")
    print("idle:           " + _idle_words(config.idle_dim_after, config.idle_off_after,
                                            config.never_dim))
    for index, profile in sorted(config.profiles.items()):
        marker = "*" if index == config.active else " "
        print(f"\n{marker} profile {index}: {_escaped(profile.name)}")
        for model, light in sorted(profile.lighting.items()):
            print(f"    lighting on the {_keyboard_entry(profile.lighting, model)}: "
                  f"{lighting_words(light)}")
        if not profile.lighting:
            print("    lighting: none saved")
        # The G915 TKL's F1 to F12 type their own keys while unbound: only a bound one is
        # listed.
        for key in [*G_KEYS, *(k for k in F_ROW_KEYS if profile.keys[k] or profile.shift[k])]:
            base = _escaped(profile.keys[key] or "-")
            shift = _escaped(profile.shift[key] or "-")
            print(f"    {key:<3} {base:<18} shift: {shift}")
        # Each mouse model's own; one with none takes its defaults.
        for model, entry in sorted(profile.mouse.items()):
            print(f"    mouse on the {_mouse_entry(profile.mouse, model)}: "
                  + "  ".join(f"{b.upper()}={_escaped(spec)}" for b, spec in entry.items()))
        if not profile.mouse:
            print("    mouse: each model's defaults")
    return 0


def add_status(sub: Subcommands) -> None:
    sub.add("status", help="show daemon and device state; --json for every field").set_defaults(
        func=cmd_status)


def add_save_and_reload(sub: Subcommands) -> None:
    sub.add("save", help="write the live configuration to disk").set_defaults(
        func=lambda a: print_reply(send({"command": "save"}), a.json))
    sub.add("reload", help="re-read the configuration from disk").set_defaults(
        func=lambda a: print_reply(send({"command": "reload"}), a.json))


def add_switches_and_idle(sub: Subcommands) -> None:
    sub.switch("lock-lights", command="lock_lights",
               help="on (the default): the lights go off while the screen is locked or "
                    "blank and before a suspend; off: they stay on")

    sub.switch("tray", command="tray",
               help="on (the default): a top-bar icon with batteries, problems, DPI stages, "
                    "game profiles and switches, where there is a tray (on GNOME, Ubuntu's "
                    "AppIndicators extension); off: none")

    low, high = IDLE_RANGE
    idl = sub.add("idle", help="show or set the keyboard's idle dimming: it dims to half after "
                               f"--dim-after seconds without a keypress ({IDLE_DIM_AFTER}), and "
                               f"its lights go off --off-after seconds later ({IDLE_OFF_AFTER})")
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
