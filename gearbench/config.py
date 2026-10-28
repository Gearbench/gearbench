"""Configuration for Gearbench: ~/.config/gearbench/config.json.

Three profiles, one per M key, each with its G-key bindings, a shift layer held on
MR, its lighting for each keyboard model, and its bindings for each mouse model's buttons. A
binding is a key spec like "ctrl+f13" or `macro:<name>`; a mouse button also takes a
built-in action. G keys ship unbound, so nothing sends keystrokes the user did not ask
for; G keys that are a model's F row, as the G915 TKL's are, type their own keys while
unbound (KeyboardModel.g_key_defaults and .reports_unbound in keyboard.py). JSON, since
the daemon writes this file and the standard library only reads TOML.

Version 2 keeps the lighting and the brightness by keyboard model, under the model's
first product id (keyboard.model_key). Version 1 kept one of each, which are read as the
G915's (V1_KEYBOARD). Version 3 keeps the mouse bindings by mouse model in the same way
(mouse.model_key); versions 1 and 2 kept one set, read as the G502 X PLUS's (V2_MOUSE).
"""

from __future__ import annotations

import logging
import string
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from .lighting import (
    ANIMATIONS,
    DEFAULT_PERIODS,
    DEFAULT_SECOND_COLOR,
    SPEEDS,
    TWO_COLORED,
    WAVE_DIRECTIONS,
)
from .settings_files import (
    FIRST_VERSION,
    ConfigError,
    backup_dir,
    config_dir,
    from_file,
    keep_old_version,
    link_to_nothing,
    make_folder,
    missing_behind,
    unprefixed,
    version_problem,
    write_json,
)
from .uinput import UinputError, parse_binding
from .wording import (
    both_ends,
    escaped,
    kind_of,
    printable,
    reason,
    whole,
    word_list,
    written,
)

log = logging.getLogger("gearbench")

VERSION = 3
#: The first format to keep the mouse bindings by mouse model; an older one's are V2_MOUSE's.
MOUSE_BY_MODEL = 3
#: The model a version 1 file's lighting and brightness are read as: the G915, which
#: keyboard.model_key names "c33e", the one keyboard Gearbench drove before version 2.
V1_KEYBOARD = "c33e"
#: The model a version 1 or 2 file's mouse bindings are read as: the G502 X PLUS, which
#: mouse.model_key names "4099", the one mouse with bindings before version 3.
V2_MOUSE = "4099"
#: How a refusal names a keyboard model where a file keys one: keyboard.model_key's form.
KEYBOARD_MODEL = "a keyboard model like 'c33e'"
#: The same for a mouse model: mouse.model_key's form.
MOUSE_MODEL = "a mouse model like '4099'"
#: How a refusal names one mouse's product id, on the route it was picked on.
MOUSE_PRODUCT = "a mouse's product id like 'c095'"
#: The brightness of a keyboard model with none saved and none to copy.
DEFAULT_BRIGHTNESS = 50
#: The G915's and the G815's G keys, by the names a layer of bindings keeps them under.
G_KEYS = ("G1", "G2", "G3", "G4", "G5")
#: The G915 TKL's G keys, its F row, by the names its keycaps and the window give them, so a
#: binding made for the G915's G1 is not the TKL's F1 (the owner's decision of 2026-10-18,
#: "Their own"). keyboard.KeyboardModel.g_key_names gives each model its own.
F_ROW_KEYS = tuple(f"F{n}" for n in range(1, 13))
#: The G515 TKL's and the G515 LIGHTSPEED TKL's F1 to F12, under names of their own, so a
#: binding made for the G915 TKL's F1 is not theirs (the owner's decisions of 2026-10-18,
#: "Their own", and of 2026-10-21). The names are the model and the keycap, with no space, as
#: `gearbench-ctl bind` takes a name.
G515_F_KEYS = tuple(f"G515-F{n}" for n in range(1, 13))
#: The PRO X TKL LIGHTSPEED's F1 to F12, its G1 to G12, under names of their own (the owner's
#: decision of 2026-10-18, "Their own"). The names are in the G515's form, the model and the
#: keycap with no space.
PRO_X_TKL_F_KEYS = tuple(f"PROXTKL-F{n}" for n in range(1, 13))
#: The G515 generation's other keyboards' keys of their own, which their custom assignments
#: bind, under names of their own (the owner's decisions of 2026-10-18, "Their own", and of
#: 2026-10-21) in the G515s' form: the G915 X's G1 to G9, shared with the G915 X LIGHTSPEED, which has its keys, and
#: each other board's F1 to F12, shared by the two models on one board.
G915X_G_KEYS = tuple(f"G915X-G{n}" for n in range(1, 10))
G915XTKL_F_KEYS = tuple(f"G915XTKL-F{n}" for n in range(1, 13))
G316X_F_KEYS = tuple(f"G316X-F{n}" for n in range(1, 13))
G512X75_F_KEYS = tuple(f"G512X75-F{n}" for n in range(1, 13))
G512X98_F_KEYS = tuple(f"G512X98-F{n}" for n in range(1, 13))
#: The G933's G1 to G3, on its left earcup, under names of their own (the owner's answer of
#: 2026-10-25, "named like G933-G1", in each M profile and game profile): g432's
#: HeadsetModel.g_keys. The G935's, G633's and G635's, taken to be the G933's (the owner's
#: decision of 2026-10-25), under each model's own in the same form. HEADSET_KEYS are every
#: headset's, which take HEADSET_ACTIONS too.
G933_G_KEYS = tuple(f"G933-G{n}" for n in range(1, 4))
G935_G_KEYS = tuple(f"G935-G{n}" for n in range(1, 4))
G633_G_KEYS = tuple(f"G633-G{n}" for n in range(1, 4))
G635_G_KEYS = tuple(f"G635-G{n}" for n in range(1, 4))
HEADSET_G_KEYS = (G933_G_KEYS, G935_G_KEYS, G633_G_KEYS, G635_G_KEYS)
HEADSET_KEYS = tuple(key for keys in HEADSET_G_KEYS for key in keys)
#: What a headset's G key can do but hold a key spec or run a macro, the owner's answers of
#: 2026-10-25: nothing; the lighting's brightness cycle, G HUB's G1 on a G933; the lighting
#: off or on; the next preset of the headset's equalizer library; sidetone off or on.
HEADSET_ACTIONS = ("none", "brightness-cycle", "lights-toggle", "eq-next", "sidetone-toggle")
#: What a headset's G key does while unbound: G1 the brightness cycle, as G HUB has it on a
#: G933 (the owner's answer of 2026-10-25), and as the G933's on the models whose G keys are
#: taken to be its (their setup guides give G1 a lighting cycle); the others nothing.
HEADSET_DEFAULTS = {keys[0]: "brightness-cycle" for keys in HEADSET_G_KEYS}
#: Each model's keys bound under names of their own, beside the models whose keys they are,
#: as a refusal and `gearbench-ctl bind`'s help name them: keyboard.MODEL_OF's names and
#: g432.PRODUCTS', which this module may not import.
OWN_KEYS = (
    (F_ROW_KEYS, "the G915 TKL"),
    (G515_F_KEYS, "the G515 TKL and G515 LIGHTSPEED TKL"),
    (PRO_X_TKL_F_KEYS, "the PRO X TKL LIGHTSPEED"),
    (G915X_G_KEYS, "the G915 X and G915 X LIGHTSPEED"),
    (G915XTKL_F_KEYS, "the G915 X LIGHTSPEED TKL"),
    (G316X_F_KEYS, "the G316 X and G316 X 98"),
    (G512X75_F_KEYS, "the G512 X 75"),
    (G512X98_F_KEYS, "the G512 X 98"),
    (G933_G_KEYS, "the G933"),
    (G935_G_KEYS, "the G935"),
    (G633_G_KEYS, "the G633"),
    (G635_G_KEYS, "the G635"),
)
#: Every name a layer of bindings keeps, in the order its files write them.
BINDABLE_KEYS = G_KEYS + tuple(key for keys, _models in OWN_KEYS for key in keys)
PROFILE_IDS = (1, 2, 3)          # one per M key


def profile_label(index: int, name: str) -> str:
    """M profile `index` named `name` as the window, the tray and a refusal name it:
    "M2 · Shooter"."""
    return f"M{index} · {name}"


#: The two lighting modes, of M and game profiles alike: one color for the whole
#: board, or the per-key layout over a background.
STATIC, PER_KEY = "static", "perkey"
MODES = (STATIC, PER_KEY)
#: Each mode in the CLI's text, as the Effect row names it: the file's "perkey" with its
#: hyphen. --json and the file keep the mode's own word.
MODE_WORDS = {STATIC: "static", PER_KEY: "per-key"}
#: A lighting layer's Static color and per-key background where its file has none.
DEFAULT_COLOR, DEFAULT_BACKGROUND = 0x0080FF, 0x000000

#: The names a mouse model's programmable buttons are bound under, in display order: each
#: model has some of them (mouse.ButtonTable).
MOUSE_BUTTONS = ("g4", "g5", "g6", "g7", "g8", "g9")
#: What each one is on the G502 X PLUS, for the model tables that share its labels (mouse.py)
#: and the window's rows until a reply gives the shown model's.
MOUSE_LABELS = {
    "g4": "G4 (side, rear)", "g5": "G5 (side, front)", "g6": "G6 (paddle)",
    "g7": "G7 (DPI down)", "g8": "G8 (DPI up)", "g9": "G9 (behind the wheel)",
}
#: Built-in actions a mouse button can be bound to instead of a key spec.
#: back and forward hold the virtual mouse's buttons uinput.POINTER_ACTIONS names;
#: dpi-shift drops to the profile's shift stage while held; dpi-cycle steps up and from the
#: last stage round to the first, as the PRO WIRELESS's DPI button does.
MOUSE_ACTIONS = ("none", "back", "forward", "dpi-shift", "dpi-up", "dpi-down", "dpi-cycle")
#: What versions 1 and 2 gave a button their file left out: the G502 X PLUS's factory jobs
#: for G4 to G8 and nothing on G9, a profile cycle there (mouse.G502X_BUTTONS.defaults).
MOUSE_DEFAULTS = {"g4": "back", "g5": "forward", "g6": "dpi-shift",
                  "g7": "dpi-down", "g8": "dpi-up", "g9": "none"}

#: The report rates a mouse's onboard profile and its live write take, in Hz, the fastest
#: first, as a message lists them: an interval of 1, 2, 4 or 8 ms (onboard.RATE_BYTES).
REPORT_RATES = (1000, 500, 250, 125)

#: Idle dimming, the owner's decision of 2026-09-23, G HUB's values: the
#: keyboard dims to half this many seconds after its last keypress and goes
#: dark IDLE_OFF_AFTER seconds after that; a keypress brings it back.
IDLE_DIM_AFTER, IDLE_OFF_AFTER = 60, 300
#: The seconds either time may be set to. The firmware keeps up to 65535
#: units of 1000/1024 s; an hour is plenty for a setting in the window.
IDLE_RANGE = (10, 3600)

#: Prefix of a binding that names a macro, on a G key or a mouse button (macros.py).
MACRO_PREFIX = "macro:"


def config_path() -> Path:
    return config_dir() / "config.json"


def config_backup(version: int) -> Path:
    """Where config.json's bytes in format `version` are kept (keep_old_version)."""
    return backup_dir() / f"config.v{version}.json"


def unusable(exc: BaseException) -> str:
    """Why config.json would not load, without its path, for a message that names the file:
    Config.load starts each of its errors with the path, and an OSError from the open quotes
    it after the reason."""
    return unprefixed(reason(exc), config_path())


def idle_problem(value: object) -> str | None:
    """Why `value` is not a whole number of seconds in IDLE_RANGE, or None."""
    low, high = IDLE_RANGE
    if not whole(value) or not low <= value <= high:
        return f"is a whole number of seconds from {low} to {high}, not {written(value)}"
    return None


def idle_seconds(value: object, where: str) -> int:
    """An idle time from the file, or ConfigError naming the field."""
    if (problem := idle_problem(value)) is not None:
        raise ConfigError(f"{where} {problem}")
    # A whole number: idle_problem found nothing wrong with it.
    return cast(int, value)


def flag(data: dict, name: str, default: bool) -> bool:
    """A true-or-false field of the file, or ConfigError naming it."""
    return read_switch(data.get(name, default), name)


def read_switch(value: object, where: str) -> bool:
    """A switch, true or false, or ConfigError naming `where`."""
    if not isinstance(value, bool):
        raise ConfigError(f"{where} is true or false, not {written(value)}")
    return value


def expect[T](value: object, kind: type[T], where: str, what: str) -> T:
    """`value` as a `kind`, or ConfigError naming what was found (kind_of)."""
    if not isinstance(value, kind):
        raise ConfigError(f"{where} is {what}, not {kind_of(value)}")
    return value


def refuse_controls(text: str, where: str) -> None:
    """Raise ConfigError if `text` holds a control character (Cc) or a lone surrogate (Cs).

    Names and bindings are printed to terminals, where a control character can start
    an escape sequence that hides or rewrites output. A lone surrogate, from a JSON
    escape, is not UTF-8: PyGObject raises on it, and the window could not open. A refusal quotes
    `text` cut to its two ends (both_ends): a caller can measure a name without the white
    space around it, which can be most of a request."""
    for c in text:
        category = unicodedata.category(c)
        if category == "Cc":
            raise ConfigError(f"{where}: {both_ends(repr(text))} holds a control character")
        if category == "Cs":
            raise ConfigError(f"{where}: {both_ends(repr(text))} is not valid text")


#: The most characters a game profile, macro or M profile name may hold. A row wraps a
#: name with no space in time that grows with its square (40,000 characters: 29 s,
#: measured 2026-10-12). Room above the G HUB import's 256-character cut
#: (ghub.MAX_NAME_CHARS) for the " (N)" an import adds.
MAX_NAME_LENGTH = 1000


def refuse_name(text: str, where: str) -> None:
    """refuse_controls for a name, length first, so a refusal never quotes a long name."""
    if len(text) > MAX_NAME_LENGTH:
        raise ConfigError(f"{where}: {len(text)} characters is more than the "
                          f"{MAX_NAME_LENGTH} a name may hold")
    refuse_controls(text, where)


def parse_color(value: object, where: str) -> int:
    """A color from a file or a command: '#rrggbb' text, the '#' optional, or a whole
    number from 0 to 0xffffff. Anything else is a ConfigError naming `where`."""
    if isinstance(value, str):
        # Exactly six hex digits, the owner's decision of 2026-09-26: int() alone takes
        # "fff" as 0x000fff, and "0x", "_" and fullwidth digits.
        text = value.strip().removeprefix("#")
        if len(text) == 6 and all(c in string.hexdigits for c in text):
            return int(text, 16)
    elif whole(value):
        if not 0 <= value <= 0xFFFFFF:
            raise ConfigError(f"{where}: {written(value)} is not a color like '#0080ff' "
                              "or a number from 0 to 16777215")
        return value
    raise ConfigError(f"{where}: {written(value)} is not a color like '#0080ff'")


def format_color(color: int) -> str:
    """A color as the files and the replies write it: '#rrggbb', lowercase."""
    return f"#{color:06x}"


#: The M profiles as a refusal of any other number names them: "1, 2 or 3".
PROFILE_CHOICES = word_list([str(i) for i in PROFILE_IDS])
#: REPORT_RATES as a refusal of any other rate names them: "1000, 500, 250 or 125".
RATE_CHOICES = word_list([str(rate) for rate in REPORT_RATES])


#: Why an animation outside lighting.SPEEDS has no speed, for its refusal and the journal.
OWN_PACE = "the keyboard runs it at its own pace"


def speed_problem(effect: str, value: object) -> str | None:
    """Why `value` is not a speed `effect` takes, in words, or None when it is."""
    if effect not in SPEEDS:
        why = f": {OWN_PACE};" if effect in ANIMATIONS else ";"
        return f"{effect} has no speed{why} only {word_list(list(SPEEDS), 'and')} have one"
    fastest, slowest = SPEEDS[effect]
    if not whole(value) or not fastest <= value <= slowest:
        return (f"{effect}'s speed is {fastest} to {slowest} ms, how long one cycle "
                f"takes, lower faster; not {written(value)}")
    return None


def direction_problem(value: object) -> str | None:
    """Why `value` is not one of wave's directions, in words, or None."""
    if isinstance(value, str) and value in WAVE_DIRECTIONS:
        return None
    return f"wave's direction is {word_list(list(WAVE_DIRECTIONS))}, not {written(value)}"


#: (where, effect, ms) of each clamp or drop read_speeds has said at info.
_CLAMPS_SAID: set[tuple[str, str, int]] = set()


def _say_once(said: tuple[str, str, int], message: str, *args: object) -> None:
    """`message` at info the first time read_speeds meets `said`, (where, effect, ms), and at
    debug after: the daemon reads every game profile every 5 s."""
    log.log(logging.DEBUG if said in _CLAMPS_SAID else logging.INFO, message, *args)
    _CLAMPS_SAID.add(said)


def read_speeds(raw: object, where: str) -> dict[str, int]:
    """A profile's saved animation speeds from JSON, {effect: ms}, each in its range.
    Absent or null is none saved: every animation at its default.

    A whole number outside its range is clamped to the nearer end and said once at
    info, so a narrowed range (ripple's, to 1000 ms on 2026-09-24) does not leave the
    daemon without its configuration, and one for an animation that takes no speed, which
    an earlier build saved for Pulsar Point and the G512 X's three, is left out the same
    way. The file keeps the old number until the next save. Anything else wrong is refused,
    naming the field."""
    if raw is None:
        return {}
    raw = expect(raw, dict, where, "an object of effect names and milliseconds")
    speeds = {}
    for name, value in raw.items():
        if name in ANIMATIONS and whole(value):
            said = (where, name, value)
            if name not in SPEEDS:
                _say_once(said, "%s.%s: %s has no speed: %s; its %d ms is left out",
                          escaped(where), name, name, OWN_PACE, value)
                continue
            fastest, slowest = SPEEDS[name]
            if not fastest <= value <= slowest:
                kept = min(max(value, fastest), slowest)
                _say_once(said, "%s.%s: %d ms is outside %s's %d to %d; using %d",
                          escaped(where), name, value, name, fastest, slowest, kept)
                value = kept
        if (problem := speed_problem(name, value)) is not None:
            raise ConfigError(f"{where}.{name}: {problem}")
        speeds[name] = value
    return speeds


def read_direction(raw: object, where: str) -> str | None:
    """Wave's saved direction from JSON, by name; absent or null for none."""
    if raw is None:
        return None
    if (problem := direction_problem(raw)) is not None:
        raise ConfigError(f"{where}: {problem}")
    return str(raw)


def speed_shown(effect: str, settings: dict[str, int]) -> int:
    """The period `effect` runs at, from Lighting.settings: the saved one, or the default."""
    return settings.get("period", DEFAULT_PERIODS[effect])


def direction_shown(settings: dict[str, int]) -> str:
    """Wave's direction by name from its settings; unsaved or unnamed, horizontal."""
    wanted = settings.get("direction")
    return next((name for name, code in WAVE_DIRECTIONS.items() if code == wanted),
                "horizontal")


def layer_color(effect: str, mode: str, static: int, background: int) -> int:
    """The color a layer shows, an M profile's or a game profile's: `static` under an
    animation or in static mode, `background` in per-key mode. The disabled effect's
    black is the caller's."""
    return static if effect in ANIMATIONS or mode != PER_KEY else background


def layer_binding(keys: dict[str, str | None], shift: dict[str, str | None], key: str,
                  shifted: bool) -> str | None:
    """A G key's binding on a layer, an M profile's or a game profile's. An unbound key
    on the shift layer falls through to the base layer, so holding MR does not silently
    disable keys the user did bind."""
    value = shift.get(key) if shifted else None
    return keys.get(key) if value is None else value


def read_mode(data: dict, where: str) -> str:
    """A layer's lighting mode from JSON, STATIC when absent."""
    mode = data.get("mode", STATIC)
    expect(mode, str, f"{where}.mode", word_list(MODES))
    if mode not in MODES:
        raise ConfigError(f"{where}.mode is {word_list(MODES)}, not {mode!r}")
    return mode


def macro_name(spec: object) -> str | None:
    """The macro a binding names, or None if it names a key instead."""
    text = str(spec)
    if not text.startswith(MACRO_PREFIX):
        return None
    return text[len(MACRO_PREFIX):].strip()


def validate_binding(spec: str, where: str) -> str:
    """One G-key binding: a key spec, or `macro:<name>`. Answers it as it is stored, without
    the white space around it, which a key spec is split at (uinput.binding_parts) and a
    macro's name drops (the owner's decision of 2026-10-07).

    A macro name is not checked against the library: a macro file can be added after
    the profile is read, and the order the two were written in must not matter. An
    unknown name is logged when the key is pressed.
    """
    spec = spec.strip()     # before the prefix is read, so " macro:Foo" is a macro
    name = macro_name(spec)
    if name is None:
        try:
            parse_binding(spec)
        except UinputError as exc:
            raise ConfigError(f"{where}: {exc}") from None
    elif not name:
        raise ConfigError(f"{where}: {spec!r} names no macro")
    else:
        refuse_name(name, where)
        refuse_controls(spec, where)    # also the white space macro_name strips
    return spec


def key_binding(key: str, spec: object, where: str) -> str | None:
    """`key`'s binding from a file or a request, as validate_binding answers it, or None for
    None and "". Anything else must be text: str() would bind a JSON 5 to the 5 key, and 0 or
    false would unbind. A headset's key, one of HEADSET_KEYS, takes one of HEADSET_ACTIONS
    too, in any case and with white space around it, as a mouse button takes its actions."""
    if spec is None or spec == "":
        return None
    spec = expect(spec, str, where, "a key spec or macro:<name>")
    if key in HEADSET_KEYS and spec.strip().lower() in HEADSET_ACTIONS:
        return spec.strip().lower()
    return validate_binding(spec, where)


def parse_mouse_binding(spec: object, where: str) -> str:
    """Validate one mouse binding: a built-in action, a key spec or a macro.

    None and "" mean "none"; anything else must be text, so a JSON 5 is not the 5 key.
    An action matches in any case and with spaces around it, since " Back" is held as
    "Back" otherwise, which parse_binding reads as KEY_BACK. The rest takes a G key's check,
    `macro:<name>` included, and is stored as a G key's is.
    """
    if spec is None or spec == "":
        return "none"
    spec = expect(spec, str, where, "an action, a key spec or macro:<name>")
    word = spec.strip().lower()
    if word in MOUSE_ACTIONS:
        return word
    return validate_binding(spec, where)


def mouse_button_name(name: object) -> str | None:
    """The config key for a mouse button name, in any case, or None."""
    text = str(name).lower()
    return text if text in MOUSE_BUTTONS else None


def mouse_entry(raw: object, where: str) -> dict[str, str]:
    """The mouse bindings an object of JSON holds, by button name, in the file's order. A
    button a model lacks is kept and does nothing on it; one it has and the object leaves out
    takes the model's default (mouse.bindings_of)."""
    raw = expect(raw, dict, where, "an object of mouse buttons and bindings")
    found: dict[str, str] = {}
    for key, spec in raw.items():
        if (name := mouse_button_name(key)) is None:
            raise ConfigError(
                f"{where}: {key!r} is not a mouse button; they are {', '.join(MOUSE_BUTTONS)}"
            )
        # Refused in any key order, or the later of "G4" and "g4" wins unsaid.
        if name in found:
            raise ConfigError(f"{where}: {key!r} names a button another key names")
        found[name] = parse_mouse_binding(spec, f"{where}.{key}")
    return found


def read_mouse_bindings(raw: object, where: str, version: int) -> dict[str, dict[str, str]]:
    """A profile's mouse bindings by mouse model (mouse.model_key), from a file of format
    `version`: an older format's one set, MOUSE_DEFAULTS filled in, is V2_MOUSE's. Absent or
    null is none."""
    if version >= MOUSE_BY_MODEL:
        return by_model(raw, where, mouse_entry, MOUSE_MODEL, "mouse")
    return {} if raw is None else {V2_MOUSE: MOUSE_DEFAULTS | mouse_entry(raw, where)}


def mouse_json(entries: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    """A profile's mouse bindings as its file writes them: by model, sorted."""
    return {model: dict(entry) for model, entry in sorted(entries.items())}


def product_key(key: str, where: str, what: str) -> str:
    """A device's product id where a file keys one, in four lowercase hex digits, or a
    ConfigError naming `where` and saying it is not `what`, the kind of id it should be."""
    product = key.strip().lower()
    if len(product) != 4 or not all(c in string.hexdigits for c in product):
        raise ConfigError(f"{where}: {key!r} is not {what}")
    return product


def product_or_null(data: dict, name: str, what: str) -> str | None:
    """config.json's field `name`, a product id in product_key's form, `what` the kind it
    should be, or null."""
    if (value := data.get(name)) is None:
        return None
    expect(value, str, name, f"{what} or null")
    return product_key(value, name, what)


def by_model[T](raw: object, where: str, read: Callable[[object, str], T], what: str,
                kind: str) -> dict[str, T]:
    """What a file keeps by device model, each value read by `read(value, where)`: absent or
    null is nothing. Each key is read by product_key, `what` the kind of id it should be, and
    one named twice is refused, in any key order, as naming a `kind` another key names."""
    if raw is None:
        return {}
    raw = expect(raw, dict, where, f"an object keyed by {what}")
    out: dict[str, T] = {}
    for key, value in raw.items():
        model = product_key(key, where, what)
        if model in out:
            raise ConfigError(f"{where}: {key!r} names a {kind} another key names")
        out[model] = read(value, f"{where}.{key}")
    return out


def key_under(entries: Mapping[str, object], ids: Sequence[str]) -> str | None:
    """The first of `ids` that `entries`, kept by device model, has: the one entry_under reads.
    `ids` are one model's ids in the order they are read: its key first (keyboard.model_ids,
    mouse.model_ids), or a mouse's own route first (daemon.mice.attached.dpi_keys). None for
    none."""
    return next((name for name in ids if name in entries), None)


def entry_under[T](entries: Mapping[str, T], ids: Sequence[str]) -> T | None:
    """What `entries`, kept by device model, holds under the first of `ids` it has
    (key_under). None for nothing."""
    key = key_under(entries, ids)
    return None if key is None else entries[key]


def keep_under[T](entries: dict[str, T], ids: Sequence[str], entry: T) -> None:
    """Keep `entry` in `entries` under `ids[0]` (key_under) and drop one under its other ids:
    beside a model's key the others would be left unread, and beside a mouse's own route the
    entry under its other route would be read there in its place. A key kept already keeps its
    place in `entries`."""
    for name in ids[1:]:
        entries.pop(name, None)
    entries[ids[0]] = entry


def merge_under[T](entries: dict[str, T], imported: Mapping[str, T],
                   ids: Callable[[str], tuple[str, ...]]) -> None:
    """Keep in `entries` each model's entry of `imported`, as keep_under does: where `imported`
    holds a model under two of its ids, the one entry_under reads there. `ids` gives a model's
    ids from any of them (keyboard.model_ids, mouse.model_ids). A model `imported` lacks keeps
    its entry."""
    for name, entry in imported.items():
        model = ids(name)
        if name == key_under(imported, model):
            keep_under(entries, model, entry)


class ModelKeys[M]:
    """The names the files keep a kind of device's models under (keyboard.KEYING, mouse.KEYING):
    each model's product ids, in four lowercase hex digits in `model_of`'s order, by the
    first, its key."""

    def __init__(self, model_of: Mapping[int, M]) -> None:
        self.model_of = model_of
        #: Each model's ids, by its key, in `model_of`'s order.
        self.ids = {ids[0]: ids for ids in (
            tuple(f"{product:04x}" for product, known in model_of.items() if known is model)
            for model in model_of.values())}

    def key_of(self, model: M) -> str:
        """The name `model` is kept under in the files."""
        return next(key for key in self.ids if self.model_of[int(key, 16)] is model)

    def model_key(self, product: int) -> str:
        """The name the model of `product` is kept under in the files, on whichever route it
        answers."""
        return self.key_of(self.model_of[product])

    def model_ids(self, key: str) -> tuple[str, ...]:
        """The ids the files may keep the model of id `key` under, its key first, for `key`
        any of its ids: one under another of its ids was written by hand, and is the
        model's. An id no model has is its own alone."""
        return next((ids for ids in self.ids.values() if key in ids), (key,))

    def model_named(self, key: str | None) -> M | None:
        """The model whose key is `key`; None for any other id, a model's other ids
        included, and for None."""
        return self.model_of[int(key, 16)] if key in self.ids else None


def read_brightness(value: object, where: str) -> int:
    """A keyboard's saved brightness, a whole number from 0 to 100, or ConfigError naming
    `where`."""
    if not whole(value) or not 0 <= value <= 100:
        raise ConfigError(f"{where} is a whole number from 0 to 100, not {written(value)}")
    return value


def per_mouse(raw: object, name: str, fits: Callable[[object], bool],
              what: str) -> dict[str, int]:
    """A whole number kept per mouse model, the field `name` of config.json, read by by_model
    as every map kept by model is (the owner's decision of 2026-10-20): each value one that
    `fits`, else a ConfigError saying it is not `what`."""
    def read(value: object, where: str) -> int:
        if not fits(value):
            raise ConfigError(f"{printable(where)}: {written(value)} is not {what}")
        return cast(int, value)     # `fits` takes whole numbers alone
    return by_model(raw, name, read, MOUSE_MODEL, "mouse")


def mouse_dpi_map(raw: object) -> dict[str, int]:
    """The remembered DPI per mouse model, by product id."""
    return per_mouse(raw, "mouse_dpi", lambda value: whole(value) and value > 0,
                     "a positive whole number")


def mouse_rate_map(raw: object) -> dict[str, int]:
    """The report rate set live per mouse model, by product id, each one of REPORT_RATES."""
    return per_mouse(raw, "mouse_rate", lambda value: whole(value) and value in REPORT_RATES,
                     f"one of {RATE_CHOICES} Hz")


def not_bindable(key: object) -> str:
    """The refusal of a key no binding takes. It names the bindable keys, because M1 to M3
    and MR look bindable and are not."""
    return (f"{written(key)} is not bindable; bindable keys are {G_KEYS[0]} to {G_KEYS[-1]}, "
            f"{own_keys_words('and')}; M1 to M3 switch profiles, and holding MR switches to "
            f"the MR layer")


def own_keys_words(last: str) -> str:
    """Each model's keys of its own (OWN_KEYS) as a line names them, `last` before the last
    group: "F1 to F12 on the G915 TKL, ... and G512X98-F1 to G512X98-F12 on the G512 X 98"."""
    return word_list([f"{keys[0]} to {keys[-1]} on {models}" for keys, models in OWN_KEYS], last)


#: The refusal of a headset's G key bound on the MR layer, the key's name put in.
NO_HEADSET_SHIFT = "{} has no MR layer: MR shifts a keyboard's G keys alone"


def g_key_layer(raw: object, where: str, shift: bool = False) -> dict[str, str | None]:
    """A layer of G-key bindings from JSON, every one of BINDABLE_KEYS, unbound ones None;
    game profiles share it. On the MR layer (`shift`) a headset's key that is bound is
    refused, as `bind` refuses it (NO_HEADSET_SHIFT), since nothing reads it there."""
    if raw is None:
        raw = {}
    raw = expect(raw, dict, where, "an object of G keys and bindings")
    out: dict[str, str | None] = dict.fromkeys(BINDABLE_KEYS)
    for key, spec in raw.items():
        if key not in BINDABLE_KEYS:
            raise ConfigError(f"{where}: {not_bindable(key)}")
        out[key] = key_binding(key, spec, f"{where}.{key}")
        if shift and key in HEADSET_KEYS and out[key] is not None:
            raise ConfigError(f"{where}: {NO_HEADSET_SHIFT.format(key)}")
    return out


def layer_json(layer: dict[str, str | None]) -> dict[str, str | None]:
    """A layer of bindings as its file writes it: each of G_KEYS, bound or not, and any other
    key, a model's own (OWN_KEYS), only while it is bound, so a file that binds none of them
    is written as before they were bindable."""
    return {key: spec for key, spec in layer.items() if key in G_KEYS or spec is not None}


@dataclass
class Lighting:
    """A profile's lighting for one keyboard model: one zone effect, plus optional per-key
    overrides. An M profile and a game profile keep one by model (Profile.lighting).

    The board shows one choice, as the window lists it: an animation
    (lighting.ANIMATIONS) when `effect` is one, else `mode`, Static or Per-key. An
    animation hides the painted keys as Static does and leaves `mode` alone, so
    `effect static` brings the earlier choice back.
    """

    effect: str = "static"
    #: The Static color: the whole board in static mode, and the color an
    #: animation with a color takes, Pulsar Point's first, whatever the mode.
    color: int = DEFAULT_COLOR
    #: The second color of an animation with two, Pulsar Point's (lighting.TWO_COLORED).
    second_color: int = DEFAULT_SECOND_COLOR
    #: Extra effect parameters, e.g. {"period": 5000} for cycle, applied to
    #: whatever effect runs. Only a hand edit fills them; a game profile's
    #: lighting has none, and copy first drops them (keyboard.cut_lighting).
    #: `speeds` and `direction` win over them.
    params: dict[str, int] = field(default_factory=dict)
    #: Each animation's speed, its period in ms, by effect name, within lighting.SPEEDS;
    #: one missing runs at lighting.DEFAULT_PERIODS. Kept per effect, so another choice
    #: and back finds it (the owner's decision of 2026-09-24).
    speeds: dict[str, int] = field(default_factory=dict)
    #: Wave's direction, a lighting.WAVE_DIRECTIONS name, or None for
    #: horizontal (or a `direction` in `params`).
    direction: str | None = None
    #: Per-key overrides by key name, e.g. {"G1": 0xff0000}.
    keys: dict[str, int] = field(default_factory=dict)
    #: STATIC shows `color` on every key and keeps `keys` without showing
    #: them; PER_KEY shows `keys` over `background`.
    mode: str = STATIC
    #: What an unpainted key shows in per-key mode, apart from `color` (the owner's
    #: decision of 2026-09-23).
    background: int = DEFAULT_BACKGROUND

    def mode_color(self) -> int:
        """The color of the choice it shows, but for the disabled effect's black."""
        return layer_color(self.effect, self.mode, self.color, self.background)

    def settings(self, effect: str | None = None) -> dict[str, int]:
        """What this profile saved for `effect`, its own by default, as set_effect parameters:
        `params` (an M profile's raw ones), overridden by its speed as `period`, for wave its
        direction's byte, and for an effect with two colors its `second_color`. Nothing saved
        runs at lighting's defaults."""
        effect = effect or self.effect
        settings = dict(self.params)
        if effect in self.speeds:
            settings["period"] = self.speeds[effect]
        if effect == "wave" and self.direction is not None:
            settings["direction"] = WAVE_DIRECTIONS[self.direction]
        if effect in TWO_COLORED:
            settings["second_color"] = self.second_color
        return settings

    def to_json(self) -> dict:
        return {
            "effect": self.effect,
            "color": format_color(self.color),
            "second_color": format_color(self.second_color),
            "params": dict(self.params),
            "speeds": dict(self.speeds),
            "direction": self.direction,
            "mode": self.mode,
            "background": format_color(self.background),
            "keys": {k: format_color(v) for k, v in self.keys.items()},
        }

    @classmethod
    def from_json(cls, data: object, where: str, *, color_field: str = "color",
                  with_params: bool = True) -> Lighting:
        """One keyboard model's lighting from a file's JSON: config.json's, and a game
        profile's (profiles.game_lighting), whose Static color is its `color_field` and
        whose `params`, which only an M profile has, are left unread (`with_params`)."""
        data = expect(data, dict, where, "an object")
        # Only null is the default, as in Config.from_json.
        params = data.get("params") if with_params else None
        if params is None:
            params = {}
        expect(params, dict, f"{where}.params", "an object")
        # A bool is an int and would go out as 1; a negative number fits no byte.
        for name, value in params.items():
            if not whole(value) or value < 0:
                raise ConfigError(f"{where}.params.{name}: {written(value)} is not a whole "
                                  "number, 0 or more")
        keys = data.get("keys")
        if keys is None:
            keys = {}
        expect(keys, dict, f"{where}.keys", "an object of key names and colors")
        mode = read_mode(data, where)
        color = parse_color(data.get(color_field, DEFAULT_COLOR), f"{where}.{color_field}")
        effect = data.get("effect", "static")
        expect(effect, str, f"{where}.effect", "an effect name")
        return cls(
            effect=effect,
            color=color,
            second_color=parse_color(data.get("second_color", DEFAULT_SECOND_COLOR),
                                     f"{where}.second_color"),
            params=dict(params),
            speeds=read_speeds(data.get("speeds"), f"{where}.speeds"),
            direction=read_direction(data.get("direction"), f"{where}.direction"),
            keys={k: parse_color(v, f"{where}.keys.{k}") for k, v in keys.items()},
            mode=mode,
            background=parse_color(data.get("background", DEFAULT_BACKGROUND),
                                   f"{where}.background"),
        )


@dataclass
class Profile:
    name: str = "Profile"
    #: G-key bindings by BINDABLE_KEYS' names, None meaning unbound. Values are specs like
    #: "ctrl+f5".
    keys: dict[str, str | None] = field(default_factory=lambda: dict.fromkeys(BINDABLE_KEYS))
    #: Bindings used while MR is held.
    shift: dict[str, str | None] = field(default_factory=lambda: dict.fromkeys(BINDABLE_KEYS))
    #: Its lighting by keyboard model (keyboard.model_key). A model with none shows another
    #: model's, cut to what it can show (keyboard.shown_lighting), until its first change.
    lighting: dict[str, Lighting] = field(default_factory=dict)
    #: Its mouse bindings by mouse model (mouse.model_key), each by button name: a built-in
    #: action, a key spec or a macro, never None. A model with none, or a button its entry
    #: leaves out, takes the model's defaults (mouse.bindings_of).
    mouse: dict[str, dict[str, str]] = field(default_factory=dict)

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "keys": layer_json(self.keys),
            "shift": layer_json(self.shift),
            "lighting": {model: entry.to_json() for model, entry in sorted(self.lighting.items())},
            "mouse": mouse_json(self.mouse),
        }

    @classmethod
    def from_json(cls, data: object, where: str, default_name: str, version: int) -> Profile:
        """An M profile from a file of format `version`: version 1's one lighting, its defaults
        for any field it lacks, is V1_KEYBOARD's, and an older format's mouse bindings are
        read_mouse_bindings'."""
        data = expect(data, dict, where, "an object")

        name = data.get("name", default_name)
        expect(name, str, f"{where}.name", "text")
        refuse_name(name, f"{where}.name")
        # Only null is the default; false or [] is a wrong type, refused.
        lighting = data.get("lighting")
        if version == FIRST_VERSION:
            entries = {V1_KEYBOARD: Lighting.from_json({} if lighting is None else lighting,
                                                       f"{where}.lighting")}
        else:
            entries = by_model(lighting, f"{where}.lighting", Lighting.from_json,
                               KEYBOARD_MODEL, "keyboard")
        return cls(
            name=name,
            keys=g_key_layer(data.get("keys"), f"{where}.keys"),
            shift=g_key_layer(data.get("shift"), f"{where}.shift", shift=True),
            lighting=entries,
            mouse=read_mouse_bindings(data.get("mouse"), f"{where}.mouse", version),
        )


#: The most saved colors config.json holds: one swatch each in every picker's scrolling row.
SAVED_COLORS_MAX = 24


@dataclass
class Config:
    active: int = 1
    #: Each keyboard model's brightness, by keyboard.model_key, one for every layer. A model with
    #: none starts from the one copy first picks (keyboard.source_entry), or DEFAULT_BRIGHTNESS.
    brightness: dict[str, int] = field(default_factory=dict)
    #: Colors the user chose to keep; nothing is added just for being tried.
    saved_colors: list[int] = field(default_factory=list)
    #: Game profile overlaying the M profile, by name, or None.
    active_game_profile: str | None = None
    #: The mouse follows the keyboard's lighting: its base color, live previews included,
    #: its brightness, the lock, and its idle dimming once the whole desktop is idle. A
    #: device preference, not a profile's: the mouse is one device whatever profile is active.
    mouse_sync_color: bool = False
    #: A headset's lights follow the keyboard's as a synced mouse does, one setting for every
    #: headset whose lights Gearbench drives and every profile (the owner's decision of
    #: 2026-10-25).
    headset_sync_color: bool = False
    #: The keyboard model chosen to lead, by keyboard.model_key or, written by hand, another
    #: of the model's ids (keyboard.model_ids), or None for none chosen: it leads while it is
    #: driven or none is (PerKeyboard.leader).
    sync_keyboard: str | None = None
    #: Each keyboard model's follow switch, by keyboard.model_key as `brightness` is: on, while
    #: another keyboard leads it shows the leader's lighting at the leader's brightness in place
    #: of its own (the owner's decision of 2026-10-21, "B: separate pages, follow switch"). On
    #: for a model not named. Gearbench 1.1.0 reads a file with the field and leaves it out of
    #: its next save.
    follow_leader: dict[str, bool] = field(default_factory=dict)
    #: The keyboard's lights, and a synced mouse's, go off while the screen is locked
    #: or blanked and before a suspend (the owner's decision of 2026-09-23).
    lights_off_when_locked: bool = True
    #: Idle dimming (IDLE_DIM_AFTER): seconds from the last keypress to the dim, and from
    #: the dim to lights off. never_dim turns both off; the keyboard's idle and wake
    #: notices are answered either way.
    idle_dim_after: int = IDLE_DIM_AFTER
    idle_off_after: int = IDLE_OFF_AFTER
    never_dim: bool = False
    #: A desktop notice with the new DPI when a DPI button steps it; a step within
    #: session.DPI_NOTICE_REPLACE of its post replaces it in place, a later one closes it and
    #: posts a new one (the owner's decisions of 2026-09-24 and 2026-10-18). Its own switch
    #: because GNOME's switch for the app would silence the battery notices too.
    dpi_notices: bool = True
    #: HOST mode for each mouse model in spy.SPY_MODELS (MouseModel.host_mode): while the
    #: daemon runs, the mouse is in HOST mode with 0x8110 armed and its buttons reach the
    #: daemon as the spy's masks; at a stop it goes back to ONBOARD mode, on profile 1 where
    #: its writes are tested (the owner's decision of 2026-09-24). Off, every mouse runs its
    #: onboard profile.
    mouse_host_mode: bool = True
    #: The tray icon (the owner's scope of 2026-09-26): the batteries and problems, and a
    #: menu of DPI stages, game profiles and these switches.
    tray: bool = True
    #: Tell the keyboard's receiver to send its link notices (register 0x00's wireless
    #: bit) when the keyboard connects through it. On by the owner's decision of
    #: 2026-09-26; set by hand only.
    keyboard_link_notices: bool = True
    #: The DPI last chosen for each mouse model, by product id, re-applied when that mouse
    #: reappears: after a power cycle it is on its last stage in ONBOARD mode and on
    #: profile 1's default stage in HOST mode, neither of which need be the DPI chosen
    #: over 0x2201. After a write that changes the stage list it is the DPI the mouse
    #: reads then. A mouse absent from the map keeps its own stage. Keyed by product id, so
    #: a mouse of another model is not set to the first one's DPI.
    mouse_dpi: dict[str, int] = field(default_factory=dict)
    #: The report rate last set live on each mouse model whose onboard profile Gearbench does
    #: not write (Dpi.live_rate), by product id as mouse_dpi is: sent at each arm of
    #: HOST mode in place of profile 1's, which the mouse keeps for when Gearbench stops.
    mouse_rate: dict[str, int] = field(default_factory=dict)
    #: The mouse last picked on the Mouse tab's switcher or with `gearbench-ctl mouse-select`,
    #: by the product id it was picked on: the one the window, the tray and a command without
    #: `device` start on after a restart once it is attached (the owner's decision of
    #: 2026-10-21). None for the first attached since the daemon started.
    mouse_selected: str | None = None
    profiles: dict[int, Profile] = field(
        default_factory=lambda: {
            i: Profile(name=f"Profile {i}") for i in PROFILE_IDS
        }
    )

    @property
    def profile(self) -> Profile:
        return self.profiles[self.active]

    def switches_to(self, index: int) -> bool:
        """Whether `index` names an M profile other than the active one, so that a switch to
        it makes it active."""
        return index in PROFILE_IDS and index != self.active

    def to_json(self) -> dict:
        return {
            "version": VERSION,
            "active_profile": self.active,
            "brightness": dict(sorted(self.brightness.items())),
            "saved_colors": [format_color(c) for c in self.saved_colors],
            "active_game_profile": self.active_game_profile,
            "mouse_sync_color": self.mouse_sync_color,
            "headset_sync_color": self.headset_sync_color,
            "sync_keyboard": self.sync_keyboard,
            "follow_leader": dict(sorted(self.follow_leader.items())),
            "lights_off_when_locked": self.lights_off_when_locked,
            "idle_dim_after": self.idle_dim_after,
            "idle_off_after": self.idle_off_after,
            "never_dim": self.never_dim,
            "dpi_notices": self.dpi_notices,
            "mouse_host_mode": self.mouse_host_mode,
            "tray": self.tray,
            "keyboard_link_notices": self.keyboard_link_notices,
            "mouse_dpi": self.mouse_dpi,
            "mouse_rate": self.mouse_rate,
            "mouse_selected": self.mouse_selected,
            "profiles": {str(i): p.to_json() for i, p in sorted(self.profiles.items())},
        }

    @classmethod
    def from_json(cls, data: object) -> Config:
        if not isinstance(data, dict):
            raise ConfigError(f"config is a JSON object, not {kind_of(data)}")
        if (problem := version_problem(data, VERSION, FIRST_VERSION)) is not None:
            raise ConfigError(f"config {problem}")
        version = data.get("version", FIRST_VERSION)
        # A missing field takes the default. Null does too where a field may be empty, and
        # is refused elsewhere. A wrong type is refused even when falsy (false, 0, []): read
        # as the default, it would be saved over the hand edit.
        stored = data.get("profiles")
        if stored is None:
            stored = {}
        expect(stored, dict, "profiles", 'an object keyed "1", "2" and "3"')
        # Read only by these keys, so any other would be dropped at the next save.
        for key in stored:
            if key not in {str(i) for i in PROFILE_IDS}:
                raise ConfigError(f'profiles: {key!r} is not an M profile; they are '
                                  f'"1", "2" and "3", one per M key')
        profiles = {}
        for i in PROFILE_IDS:
            raw = stored.get(str(i))
            profiles[i] = Profile.from_json({} if raw is None else raw, f"profiles.{i}",
                                            f"Profile {i}", version)
        active = data.get("active_profile", 1)
        # An int check first: 2.0 is `in` PROFILE_IDS and would reach `1 << (which - 1)`.
        if not whole(active) or active not in PROFILE_IDS:
            raise ConfigError(f"active_profile is {PROFILE_CHOICES}, "
                              f"not {written(active)}")
        if version == FIRST_VERSION:
            brightness = {V1_KEYBOARD: read_brightness(
                data.get("brightness", DEFAULT_BRIGHTNESS), "brightness")}
        else:
            brightness = by_model(data.get("brightness"), "brightness", read_brightness,
                                  KEYBOARD_MODEL, "keyboard")
        colors = data.get("saved_colors")
        if colors is None:
            colors = []
        expect(colors, list, "saved_colors", "a list of colors")
        # Refused, not cut to the newest: the next save would write the cut over the hand edit.
        if len(colors) > SAVED_COLORS_MAX:
            raise ConfigError(f"saved_colors: {len(colors)} colors is more than the "
                              f"{SAVED_COLORS_MAX} the Saved colors row may hold")
        saved = [parse_color(c, f"saved_colors[{i}]") for i, c in enumerate(colors)]
        overlay = data.get("active_game_profile")
        if overlay is not None:
            expect(overlay, str, "active_game_profile", "a game profile's name or null")
        return cls(
            active=active, brightness=brightness, profiles=profiles, saved_colors=saved,
            active_game_profile=overlay or None,
            mouse_sync_color=flag(data, "mouse_sync_color", False),
            headset_sync_color=flag(data, "headset_sync_color", False),
            sync_keyboard=product_or_null(data, "sync_keyboard", KEYBOARD_MODEL),
            follow_leader=by_model(data.get("follow_leader"), "follow_leader", read_switch,
                                   KEYBOARD_MODEL, "keyboard"),
            lights_off_when_locked=flag(data, "lights_off_when_locked", True),
            never_dim=flag(data, "never_dim", False),
            dpi_notices=flag(data, "dpi_notices", True),
            mouse_host_mode=flag(data, "mouse_host_mode", True),
            tray=flag(data, "tray", True),
            keyboard_link_notices=flag(data, "keyboard_link_notices", True),
            mouse_dpi=mouse_dpi_map(data.get("mouse_dpi")),
            mouse_rate=mouse_rate_map(data.get("mouse_rate")),
            mouse_selected=product_or_null(data, "mouse_selected", MOUSE_PRODUCT),
            idle_dim_after=idle_seconds(data.get("idle_dim_after", IDLE_DIM_AFTER),
                                        "idle_dim_after"),
            idle_off_after=idle_seconds(data.get("idle_off_after", IDLE_OFF_AFTER),
                                        "idle_off_after"),
        )


    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        """Read config.json; defaults when there is no file, and a ConfigError for a
        link to nothing, the file's or a folder's above it, so nothing is saved over it.
        Every ConfigError names the file."""
        target = path or config_path()

        def parse(raw: object) -> Config:
            try:
                return cls.from_json(raw)
            except ConfigError as exc:
                raise ConfigError(f"{target}: {exc}") from None

        try:
            return from_file(target, parse)
        except FileNotFoundError:
            place = link_to_nothing(target)
            if place is None:
                return cls()
            raise ConfigError(missing_behind(target, place)) from None

    def save(self, path: Path | None = None) -> Path:
        """Write atomically, so a crash mid-write cannot leave a truncated config, its folder
        made and synced first where it is not there (make_folder). A folder behind a link to
        nothing is a ConfigError, as at load."""
        # The rename command takes any text, and a name that load refuses
        # would hold every profile back at the next start.
        for index, profile in self.profiles.items():
            refuse_name(profile.name, f"profiles.{index}.name")
        target = path or config_path()
        # config.json's own link is written through.
        make_folder(target)
        keep_old_version(target, config_backup, VERSION)
        write_json(target, self.to_json(), ".config-")
        return target
