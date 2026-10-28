"""Read profiles, bindings, lighting and macros from Logitech G HUB's settings.

G HUB keeps them in one JSON document in one row of `%LOCALAPPDATA%\\LGHUB\\settings.db`,
a SQLite database in WAL mode whose newest changes can sit in `settings.db-wal`. This reads
the database, or that document pulled out into a JSON file, into a `Found`; what cannot be
carried over is listed by name. Nothing here writes a profile, macro or setting:
`ghub_plan.make_plan` works out what would be written, as an `import_plan.Plan`, and
`import_plan.requests` turns it into control socket commands, so the daemon writes every
file and its hand-edit protection covers an import.
"""

from __future__ import annotations

import contextlib
import sqlite3
import string
import tempfile
import time
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import cast

from .apps import MAX_PROGRAM_BYTES, wanted_forms
from .config import G_KEYS, parse_color
from .json_text import TOO_DEEP, JsonRefused, json_value
from .keyboard import G815, G915, G915_TKL, PRO_X_TKL
from .keycodes import USAGE_NAMES
from .library_files import Taken, by_name
from .macros import (
    CLICK,
    CLICK_DOWN,
    CLICK_UP,
    DELAY,
    KEY,
    KEY_DOWN,
    KEY_UP,
    MAX_DELAY_MS,
    MAX_STEPS,
    ONCE,
    TOGGLE,
    WHILE_HELD,
    validate_steps,
)
from .mouse import MODEL_OF, ButtonTable, MouseModel, key_of
from .profiles import MAX_PROCESSES
from .settings_files import ConfigError, read_capped
from .uinput import UinputError, parse_binding
from .wording import counted, kind_of, reason, whole, without_controls, written

# -- G HUB's vocabulary -----------------------------------------------------

SQLITE_MAGIC = b"SQLite format 3\x00"
#: The largest settings.db or -wal read. G HUB's own are a few megabytes.
MAX_FILE_BYTES = 64 << 20
#: The largest settings document, JSON file or database row: it bounds what json.loads
#: builds from a crafted file. The owner's decision of 2026-09-28.
MAX_DOCUMENT_BYTES = 16 << 20
#: The most profiles, and the most cards, a file may hold: a one-line record costs a
#: profile object and a row in the window's list. The owner's decision of 2026-09-28.
MAX_RECORDS = 10_000
#: The longest application, profile or card name taken, in characters: a file can use one
#: name thousands of times, and each use costs its length. The owner's decision of 2026-09-28.
MAX_NAME_CHARS = 256
#: The most other devices the window and `--list` name before saying how many more: the
#: window shows them in one label, and a file can name hundreds of thousands.
SHOWN_DEVICES = 20
READ_SECONDS = 10.0
#: SQLite's memory cap while it reads, room for a MAX_DOCUMENT_BYTES value: the first query
#: parses the whole schema, unselected views included. The limit is process-wide and can only
#: be lowered; nothing else here uses SQLite.
HEAP_BYTES = 256 << 20
#: G HUB's placeholder names, which its own UI translates.
DESKTOP_APP = "APPLICATION_NAME_DESKTOP"
DEFAULT_PROFILE = "PROFILE_NAME_DEFAULT"
#: Gearbench's names for the desktop and for an application with no name or record.
DESKTOP_NAME = "Desktop"
UNKNOWN_APP = "Unknown application"

#: The card G HUB calls Disabled.
DISABLED = "ffffffff-ffff-ffff-ffff-ffffffffffff"
#: Every built-in assignment is `0f82f693-5b78-4cf5-867e-TTVV00000000`: TT an
#: action category, VV the value in it, decoded from G HUB's settings.db
#: against the action enums in its protobuf schemas.
BUILTIN_PREFIX = "0f82f693-5b78-4cf5-867e-"
BUILTIN_SUFFIX = "00000000"

#: Each keyboard whose G keys this reads, G HUB's `g1` on, under the names its model binds
#: them under (KeyboardModel.g_key_names), by its slotPrefix in G HUB's device database,
#: "g815" the G813's too; no settings file with a keyboard but the G915 has been read.
KEYBOARDS = {"g915": G915, "g915tkl": G915_TKL, "g815": G815, "proxtkl": PRO_X_TKL}
#: Each mouse model with buttons to bind, by its slotPrefix in G HUB's device database (read
#: 2026-10-18), from one of its product ids. Only the G502 X PLUS's slots have been read from
#: a settings file.
MICE = {device: MODEL_OF[product] for device, product in (
    ("g502x-plus", 0x4099), ("g502hero", 0xC08B), ("g502x-lightspeed", 0x409F),
    ("proxwirelessmouse", 0x4093), ("g502x", 0xC099), ("g502x-wl", 0x40B2),
    ("g502wireless", 0x407F), ("prowirelessmouse", 0x4079), ("g903hero", 0x4087),
    ("prox2wirelessmouse", 0x40A9), ("proxrwirelessmouse", 0x40B8),
    ("prox2compactwirelessmouse", 0x40B7),
    ("prox2superstrikewirelessmouse", 0x40BD),
    ("g102g203", 0xC092))}
#: The keyboard whose G keys every profile has, as the preview lists them: another's are
#: read where a profile has a slot of it.
KEYBOARD = "g915"

#: Built-in mouse actions (category 04) by value, and the Gearbench action each is.
MOUSE_ACTION_NAMES = {
    0x01: "DPI up", 0x02: "DPI down", 0x03: "DPI shift", 0x04: "DPI cycle",
    0x05: "default DPI", 0x09: "wheel up", 0x0A: "wheel down", 0x0B: "wheel tilt left",
    0x0C: "wheel tilt right", 0x0E: "go to the shift DPI", 0x0F: "wheel mode toggle",
}
MOUSE_ACTIONS = {0x01: "dpi-up", 0x02: "dpi-down", 0x03: "dpi-shift", 0x04: "dpi-cycle"}
#: Built-in mouse buttons (category 02), and the two Gearbench can send.
MOUSE_BUTTON_NAMES = {0x01: "left click", 0x02: "right click", 0x03: "middle click",
                      0x04: "back", 0x05: "forward"}
MOUSE_BUTTON_ACTIONS = {0x04: "back", 0x05: "forward"}
#: Built-in device actions (category 09).
DEVICE_ACTION_NAMES = {0x01: "onboard profile cycle", 0x02: "next profile",
                       0x03: "previous profile", 0x05: "G-Shift", 0x06: "battery level"}
#: 0907: "this G-Shift slot is at its default", which G HUB puts on every
#: shifted slot nobody set.
SHIFT_DEFAULT = "0907"

#: A HID keyboard usage (page 7) to the kernel's key name, as the kernel's own
#: hid_keyboard table in drivers/hid/hid-input.c maps it: each usage that table
#: maps up to 0x94, and the modifiers.
HID_KEYS: dict[int, str] = {
    **{0x04 + i: chr(ord("a") + i) for i in range(26)},
    **{0x1E + i: str((i + 1) % 10) for i in range(10)},
    **{0x3A + i: f"f{i + 1}" for i in range(12)},
    **{0x68 + i: f"f{i + 13}" for i in range(12)},
    **{0x59 + i: f"kp{i + 1}" for i in range(9)},
    0x28: "enter", 0x29: "esc", 0x2A: "backspace", 0x2B: "tab", 0x2C: "space",
    0x2D: "minus", 0x2E: "equal", 0x2F: "leftbrace", 0x30: "rightbrace",
    0x31: "backslash", 0x32: "backslash", 0x33: "semicolon", 0x34: "apostrophe",
    0x35: "grave", 0x36: "comma", 0x37: "dot", 0x38: "slash", 0x39: "capslock",
    0x46: "sysrq", 0x47: "scrolllock", 0x48: "pause", 0x49: "insert", 0x4A: "home",
    0x4B: "pageup", 0x4C: "delete", 0x4D: "end", 0x4E: "pagedown", 0x4F: "right",
    0x50: "left", 0x51: "down", 0x52: "up", 0x53: "numlock", 0x54: "kpslash",
    0x55: "kpasterisk", 0x56: "kpminus", 0x57: "kpplus", 0x58: "kpenter",
    0x62: "kp0", 0x63: "kpdot", 0x64: "102nd", 0x65: "compose", 0x66: "power",
    0x67: "kpequal", 0x74: "open", 0x75: "help", 0x76: "props", 0x77: "front",
    0x78: "stop", 0x79: "again", 0x7A: "undo", 0x7B: "cut", 0x7C: "copy",
    0x7D: "paste", 0x7E: "find", 0x7F: "mute", 0x80: "volumeup", 0x81: "volumedown",
    0x85: "kpcomma", 0x87: "ro", 0x88: "katakanahiragana", 0x89: "yen",
    0x8A: "henkan", 0x8B: "muhenkan", 0x8C: "kpjpcomma", 0x90: "hangeul",
    0x91: "hanja", 0x92: "katakana", 0x93: "hiragana", 0x94: "zenkakuhankaku",
    0xE0: "leftctrl", 0xE1: "leftshift", 0xE2: "leftalt", 0xE3: "leftmeta",
    0xE4: "rightctrl", 0xE5: "rightshift", 0xE6: "rightalt", 0xE7: "rightmeta",
}
#: A keystroke card's modifiers are HID usages too, 224 to 231, each side its own.
HID_MODIFIERS = {0xE0: "ctrl", 0xE1: "shift", 0xE2: "alt", 0xE3: "super",
                 0xE4: "rctrl", 0xE5: "rshift", 0xE6: "ralt", 0xE7: "rsuper"}
#: A mouse button in a macro, by G HUB's usage, to a click step's button.
HID_BUTTONS = {1: "left", 2: "right", 3: "middle", 4: "side", 5: "extra"}

#: Per-key lighting, by G HUB's group, to Gearbench's zone names. The keyboard
#: group is keyed by HID usage, the order the zone ids follow.
LIGHT_GROUPS: dict[str, dict[int, str]] = {
    "PERKEY_KEYBOARD": USAGE_NAMES,
    # G HUB's number for each of the G915's G keys as a light's id.
    "PERKEY_GKEY": dict(enumerate(G_KEYS, 1)),
    "PERKEY_BRANDING": {1: "LOGO"},
    # The brightness button, the group's one light in G HUB's layout. Unchecked:
    # that an effect record keys it by that LED id, as the other groups do.
    "PERKEY_INDICATOR": {1: "BRIGHTNESS"},
    # HID consumer page usages.
    "PERKEY_CONSUMER": {0xB5: "NEXT", 0xB6: "PREVIOUS", 0xCD: "PLAY/PAUSE", 0xE2: "MUTE"},
}
PER_KEY_DEVICE = "KEYBOARD_RGB_PER_KEY"

#: The refusal of a -wal owned by a different account from the entry it sits beside.
OTHER_OWNER = "{wal}: owned by a different account from {db}; copy both into a folder of your own"


class GhubError(ValueError):
    """The file could not be read as G HUB's settings, or an import cannot be done as asked.

    Control characters are stripped: the text can quote the file and goes to terminals."""

    def __init__(self, text: str) -> None:
        super().__init__(without_controls(text))


# -- reading the file ------------------------------------------------------

def read_settings(path: Path) -> dict:
    """The settings document, from settings.db or a JSON file, told apart by the first
    bytes: a database renamed on the way from Windows still reads as one."""
    try:
        raw = read_capped(path, MAX_FILE_BYTES)
        if raw.startswith(SQLITE_MAGIC):
            raw = _read_database(path, raw)
        elif len(raw) > MAX_DOCUMENT_BYTES:
            raise GhubError(f"{path}: larger than {MAX_DOCUMENT_BYTES} bytes")
    except ConfigError as exc:
        raise GhubError(str(exc)) from None
    except OSError as exc:
        # The file that failed, which can be a -wal (_read_database).
        raise GhubError(f"{exc.filename or path} could not be read ({reason(exc)})") from None
    try:
        data = json_value(raw, one_name=False)
    except JsonRefused as exc:
        # Where the stack runs out depends on the machine: here, or in parse's walk, which
        # words it the same.
        raise GhubError(f"{path.name}: {exc}") from None
    if not isinstance(data, dict) or not _records(data, "profiles"):
        raise GhubError(f"{path.name} holds no G HUB profiles")
    for kind in ("profiles", "cards"):
        if (count := len(_records(data, kind))) > MAX_RECORDS:
            raise GhubError(f"{path.name} holds {count:,} G HUB {kind}; an import reads "
                            f"{MAX_RECORDS:,} at most")
    return data


def _read_database(path: Path, raw: bytes) -> bytes:
    """The newest settings row, read from a private copy of the database (`raw`) and its -wal,
    which must belong to the account that owns the entry it sits beside, the path given or
    the file a link names (OTHER_OWNER): SQLite applies any -wal, and in a folder others may
    write in, such as /tmp, another account could put its own there first and decide what is
    read.

    Opening the user's own file is not read-only in effect: SQLite creates a -shm beside it
    even read-only, cannot open it in a directory it may not write to, and a read-write
    connection checkpoints the -wal and deletes it on close. SQLite rebuilds the -shm from
    the -wal.
    """
    # Beside the path given, else beside the file a link names, where SQLite looks. A link's
    # own owner, not its file's, holds the -wal beside it: a user's link to a file another
    # account owns, on a Windows partition mounted as root, takes the user's own -wal.
    wal = None
    for beside, owner in ((path, path.lstat().st_uid), (path.resolve(), path.stat().st_uid)):
        candidate = beside.with_name(beside.name + "-wal")
        try:
            # The entry's own owner, a link's not its target's: in a folder with the sticky
            # bit only the folder's owner and the entry's can replace the entry checked.
            if candidate.stat(follow_symlinks=False).st_uid != owner:
                raise GhubError(OTHER_OWNER.format(wal=candidate, db=path.name))
            wal = read_capped(candidate, MAX_FILE_BYTES)
            break
        except FileNotFoundError:
            pass
        except OSError as exc:
            # os.fstat's and os.read's errors name no file, and read_settings would name `path`.
            exc.filename = exc.filename or str(candidate)
            raise
    try:
        made = tempfile.TemporaryDirectory(prefix="gearbench-ghub-")
    except OSError as exc:
        # Not the user's file: mkdtemp's error names the folder it could not make, and
        # gettempdir's, which found no folder it can write to, lists each one it tried.
        if exc.filename is None:
            raise GhubError(f"could not copy {path.name}: no temporary folder can be "
                            f"written to") from None
        raise GhubError(f"could not copy {path.name} into {Path(exc.filename).parent}: "
                        f"{reason(exc)}") from None
    with made as tmp:
        copy = Path(tmp) / "settings.db"
        try:
            copy.write_bytes(raw)
            if wal is not None:
                (Path(tmp) / "settings.db-wal").write_bytes(wal)
        except OSError as exc:
            # A full temp folder, not the user's file: its write carries no file name.
            raise GhubError(f"could not copy {path.name} into {Path(tmp).parent}: "
                            f"{reason(exc)}") from None
        try:
            with contextlib.closing(sqlite3.connect(f"{copy.as_uri()}?mode=ro",
                                                    uri=True)) as connection:
                row = _newest_row(connection, path.name)
        except (sqlite3.Error, UnicodeDecodeError) as exc:
            # Only SQLite's own errors carry a code; the module's (a text value that is not
            # UTF-8) do not.
            if getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_TOOBIG:
                raise GhubError(f"{path}: its settings are larger than {MAX_DOCUMENT_BYTES} "
                                f"bytes") from None
            # The sqlite3 module raises UnicodeDecodeError for SQLite's error text when it
            # quotes bytes of the file that are not UTF-8, as from a damaged schema.
            text = (exc.object.decode("utf-8", "replace")
                    if isinstance(exc, UnicodeDecodeError) else str(exc))
            # Cut: SQLite's text can quote a schema name from the file, up to 16 MiB long,
            # and the refusal goes to the window's dialog.
            raise GhubError(f"{path.name} is not G HUB's settings database "
                            f"({text[:MAX_NAME_CHARS]})") from None
        except MemoryError:
            # What the sqlite3 module raises when SQLite reaches HEAP_BYTES.
            raise GhubError(f"{path.name} needs more than {HEAP_BYTES >> 20} MiB to read; "
                            f"G HUB's settings are a few megabytes") from None
    if row is None or row[0] is None:
        raise GhubError(f"{path.name} holds no settings")
    return row[0] if isinstance(row[0], bytes) else str(row[0]).encode()


def _newest_row(connection: sqlite3.Connection, name: str) -> tuple | None:
    """The settings table's newest row, from a database anyone may have written: its
    schema's SQL runs under the limits set here, and `_check_schema` refuses a table
    that is not plain."""
    # Before the first query, which parses the schema; this pragma does not.
    connection.execute(f"PRAGMA hard_heap_limit={HEAP_BYTES}")
    connection.setconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE, True)
    connection.setconfig(sqlite3.SQLITE_DBCONFIG_TRUSTED_SCHEMA, False)
    connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_DOCUMENT_BYTES)
    deadline = time.monotonic() + READ_SECONDS
    connection.set_progress_handler(lambda: time.monotonic() > deadline, 1000)
    try:
        _check_schema(connection, name)
        return connection.execute("SELECT file FROM data ORDER BY _id DESC LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        if time.monotonic() > deadline:
            raise GhubError(f"{name} took longer than {READ_SECONDS:g} s to "
                            f"read") from None
        raise


def _check_schema(connection: sqlite3.Connection, name: str) -> None:
    """Raise GhubError unless `data` is a plain table with no generated or
    hidden column, and for a schema entry over MAX_DOCUMENT_BYTES. A view, a
    virtual table or a generated column is code the file's author wrote,
    which the SELECT would run."""
    try:
        found = connection.execute(
            "SELECT type, sql FROM sqlite_schema WHERE name = 'data'").fetchall()
    except sqlite3.DataError as exc:
        # This first query parses every schema entry under SQLITE_LIMIT_LENGTH,
        # so a TOOBIG here is the schema's, not the settings row's.
        if getattr(exc, "sqlite_errorcode", None) != sqlite3.SQLITE_TOOBIG:
            raise
        raise GhubError(f"{name} is not G HUB's settings database: a schema entry is "
                        f"larger than {MAX_DOCUMENT_BYTES} bytes") from None
    if (len(found) != 1 or found[0][0] != "table"
            or not str(found[0][1]).startswith("CREATE TABLE")):
        raise GhubError(f"{name} is not G HUB's settings database: it has no plain data table")
    if any(column[6] for column in connection.execute("PRAGMA table_xinfo(data)")):
        raise GhubError(f"{name} is not G HUB's settings database: its data table has a "
                        f"generated or hidden column")


def _records(data: Mapping, name: str) -> list[dict]:
    """A list of objects out of the document: `{"name": {"name": [...]}}`,
    which is how G HUB nests them, or a bare list."""
    found = data.get(name)
    if isinstance(found, dict):
        found = found.get(name)
    if not isinstance(found, list):
        return []
    return [item for item in found if isinstance(item, dict)]


def _name(raw: object) -> tuple[str, bool]:
    """A name from the file less its control characters and outer spaces,
    cut at MAX_NAME_CHARS, and whether it was cut."""
    name = without_controls(raw).strip() if isinstance(raw, str) else ""
    return name[:MAX_NAME_CHARS].rstrip(), len(name) > MAX_NAME_CHARS


def _said_cut(what: str, cut: bool) -> list[str]:
    """The preview's Not imported line for a name `_name` cut, if it was."""
    return ([f"{what} after {MAX_NAME_CHARS} characters: an imported name is cut there"]
            if cut else [])


# -- cards -----------------------------------------------------------------

@dataclass(frozen=True)
class MacroRef:
    """A binding to a macro that is named only once the plan is made: a
    clash with a macro the user has can give it a new name."""

    card: str


#: What an assignment is, for a G key or a mouse button to take or refuse.
KEYS, ACTION, MACRO, NOTHING, DEFAULT, CLICK_BUTTON, OTHER = (
    "key", "action", "macro", "nothing", "default", "click", "other")


@dataclass(frozen=True)
class Action:
    kind: str
    #: The key spec, the Gearbench mouse action, the macro card's id, the
    #: clicked button's name, or for OTHER the reason it is not imported.
    value: str
    #: What G HUB calls it, for the preview: for a key, the card's name, or
    #: the key spec for a built-in.
    label: str


def keystroke_spec(code: object, modifiers: object) -> str:
    """A Gearbench key spec from a keystroke's HID usages. Raises GhubError,
    quoting at most MAX_NAME_CHARS of the file: the reason is copied to
    every key the card is on."""
    # Refused, not dropped: modifiers that are no list, and a code or a
    # modifier that is not a number, would otherwise import a different key,
    # Ctrl+W as Ctrl alone or as W.
    mods = [] if modifiers is None else modifiers
    if not isinstance(mods, list):
        raise GhubError(f"a keystroke whose modifiers are {kind_of(mods)}, not a list")
    for mod in mods:
        if not isinstance(mod, int) or mod not in HID_MODIFIERS:
            raise GhubError(f"an unknown modifier ({written(mod)[:MAX_NAME_CHARS]})")
    if code is None or (whole(code) and code == 0):
        # A card that is a modifier on its own ("Alt") carries no code: the
        # last modifier is the key.
        if not mods:
            raise GhubError("a keystroke with no key")
        code, mods = mods[-1], mods[:-1]
    names = dict.fromkeys(HID_MODIFIERS[mod] for mod in mods)
    key = HID_KEYS.get(code) if isinstance(code, int) else None
    if key is None:
        usage = (f"0x{code:02x}" if whole(code) else written(code))[:MAX_NAME_CHARS]
        raise GhubError(f"a key with HID usage {usage}, which this importer does not read")
    spec = "+".join([*names, key])
    try:
        parse_binding(spec)
    except UinputError as exc:
        # Not the spec again: parse_binding's text quotes it.
        raise GhubError(f"a keystroke Gearbench cannot send ({exc})") from None
    return spec


def builtin_id(code: str) -> str:
    """The card id of a built-in, from its category and value: "013a" is F1."""
    return f"{BUILTIN_PREFIX}{code}{BUILTIN_SUFFIX}"


def builtin_action(card_id: str) -> Action | None:
    """What a built-in card id means, or None if it is not one."""
    # Four hex digits: int() also takes a sign, spaces and non-ASCII digits.
    if not (card_id.startswith(BUILTIN_PREFIX) and card_id.endswith(BUILTIN_SUFFIX)
            and len(card_id) == 36 and all(c in string.hexdigits for c in card_id[24:28])):
        return None
    category, value = int(card_id[24:26], 16), int(card_id[26:28], 16)
    if category == 0x01:
        try:
            spec = keystroke_spec(value, [])
        except GhubError as exc:
            return Action(OTHER, str(exc), f"G HUB built-in {card_id[24:28]}")
        return Action(KEYS, spec, spec)
    if category == 0x02:
        if value not in MOUSE_BUTTON_NAMES:
            # Not a CLICK_BUTTON: that reason offers a macro's click step, which sends
            # none but HID_BUTTONS' five.
            return Action(OTHER, "which Gearbench has no equivalent for",
                          f"mouse button {value}")
        label = MOUSE_BUTTON_NAMES[value]
        if value in MOUSE_BUTTON_ACTIONS:
            return Action(ACTION, MOUSE_BUTTON_ACTIONS[value], label)
        return Action(CLICK_BUTTON, label, label)
    if category == 0x04:
        label = MOUSE_ACTION_NAMES.get(value, f"mouse action {value}")
        if value in MOUSE_ACTIONS:
            return Action(ACTION, MOUSE_ACTIONS[value], label)
        return Action(OTHER, "Gearbench has no such action", label)
    if card_id[24:28] == SHIFT_DEFAULT:
        return Action(DEFAULT, "", "G-Shift default")
    if category == 0x09:
        label = DEVICE_ACTION_NAMES.get(value, f"device action {value}")
        return Action(OTHER, "Gearbench has no such action", label)
    return Action(OTHER, "which Gearbench has no equivalent for",
                  f"G HUB built-in {card_id[24:28]}")


def card_action(card_id: object, cards: Mapping[str, dict]) -> Action:
    """What the card an assignment points at does."""
    if not isinstance(card_id, str):
        return Action(OTHER, "an assignment with no card", "nothing")
    if card_id == DISABLED:
        return Action(NOTHING, "", "disabled")
    builtin = builtin_action(card_id)
    if builtin is not None:
        return builtin
    card = cards.get(card_id)
    if card is None:
        return Action(OTHER, "its card is not in the file", "a missing card")
    macro = _dig(card, "macro")
    # Not str(): it spells null "None" and a list in brackets.
    kind = macro.get("type", card.get("attribute", ""))
    if card.get("attribute") == "MACRO_PLAYBACK" and kind == "SEQUENCE":
        return Action(MACRO, card_id, f'macro "{card_name(card)}"')
    name = card_name(card, "card")
    if card.get("attribute") == "MACRO_PLAYBACK" and kind == "KEYSTROKE":
        keystroke = _dig(macro, "keystroke")
        try:
            spec = keystroke_spec(keystroke.get("code"), keystroke.get("modifiers"))
        except GhubError as exc:
            return Action(OTHER, f"it is {exc}", f'"{name}"')
        return Action(KEYS, spec, name)
    # Cut too: the reason is copied into the preview at every key the card is on.
    what = (without_controls(kind)[:MAX_NAME_CHARS].replace("_", " ").lower()
            if isinstance(kind, str) else "") or "unknown"
    return Action(OTHER, f"a G HUB {what} card, which Gearbench has no equivalent for",
                  f'"{name}"')


def card_name(card: Mapping, noun: str = "macro") -> str:
    """The card's name, else G HUB `noun` and the start of its id."""
    name = _name(card.get("name"))[0]
    return name or f"G HUB {noun} {without_controls(card['id'])[:8]}"


# -- macros ----------------------------------------------------------------

@dataclass
class GhubMacro:
    name: str
    repeat: str = ONCE
    #: Gearbench macro steps, or None when it cannot be imported.
    steps: list[dict] | None = None
    #: Why it cannot be, in words that follow its name.
    reason: str | None = None


def _components(part: object) -> list:
    found = _dig(part).get("components")
    return found if isinstance(found, list) else []


def convert_macro(card: Mapping) -> GhubMacro:
    """A G HUB sequence card as a Gearbench macro, or the reason it is not one.

    G HUB's mode flag picks the part that plays, else which parts hold anything. A release
    part runs on key up, and a press part with a held part repeats only the held one; a
    Gearbench macro starts at the press and repeats all of its steps or none.
    """
    name = card_name(card)
    out = GhubMacro(name=name)
    sequence = _dig(card.get("macro"), "sequence")
    parts = {part: _components(sequence.get(f"{part}Sequence"))
             for part in ("simple", "press", "held", "release", "toggle")}
    if sequence.get("useToggleActions"):
        chosen, out.repeat = parts["toggle"], TOGGLE
    elif sequence.get("useRepeatActions"):
        chosen, out.repeat = parts["held"], WHILE_HELD
    elif sequence.get("useSimpleActions"):
        chosen = parts["simple"]
    elif parts["release"]:
        out.reason = "runs steps when the key is released, which Gearbench macros cannot do"
        return out
    elif parts["press"] and parts["held"]:
        out.reason = "repeats only part of itself while held, which Gearbench macros cannot do"
        return out
    elif parts["held"]:
        chosen, out.repeat = parts["held"], WHILE_HELD
    elif parts["toggle"]:
        chosen, out.repeat = parts["toggle"], TOGGLE
    else:
        chosen = parts["press"] or parts["simple"]
    if not chosen:
        out.reason = "has no steps"
        return out
    try:
        steps = _joined([_step(component) for component in chosen])
        if len(steps) > MAX_STEPS:
            raise GhubError(f"has {len(steps)} steps, more than the {MAX_STEPS} a Gearbench "
                            f"macro holds")
        out.steps = validate_steps(steps, name)
    except GhubError as exc:
        out.reason = str(exc)
    except ConfigError as exc:
        out.reason = f"does not make a Gearbench macro ({exc})"
    return out


def _step(component: object) -> dict:
    """One G HUB macro component as a Gearbench step. Raises GhubError,
    quoting at most MAX_NAME_CHARS of the file: the reason is copied to
    every key the macro is on."""
    if not isinstance(component, dict) or len(component) != 1:
        raise GhubError("has a step this importer does not read")
    (kind, body), = component.items()
    body = _dig(body)
    if kind == "delay":
        ms = body.get("durationMs", 0)
        if not whole(ms):
            raise GhubError(f"has a delay of {written(ms)[:MAX_NAME_CHARS]}, not a whole number "
                            f"of milliseconds")
        if not 0 <= ms <= MAX_DELAY_MS:
            raise GhubError(f"has a delay of {repr(ms)[:MAX_NAME_CHARS]} ms, outside 0 to "
                            f"{MAX_DELAY_MS}")
        return {"type": DELAY, "ms": ms}
    if kind == "keyboard":
        key = HID_KEYS.get(_usage(body.get("hidUsage")))
        if key is None:
            shown = body.get("displayName")
            if shown is None:
                shown = body.get("hidUsage")
            if shown is None:
                raise GhubError("has a key step that names no key")
            raise GhubError(f"presses {written(shown)[:MAX_NAME_CHARS]}, "
                            f"which this importer does not read")
        return {"type": KEY_DOWN if body.get("isDown") else KEY_UP, "value": key}
    if kind == "mouse" and isinstance(body.get("button"), dict):
        button = HID_BUTTONS.get(_usage(body["button"].get("hidUsage")))
        if button is None:
            raise GhubError("clicks a mouse button Gearbench macros cannot send")
        return {"type": CLICK_DOWN if body["button"].get("isDown") else CLICK_UP,
                "button": button}
    raise GhubError(f"has a {without_controls(kind)[:MAX_NAME_CHARS]} step, which this "
                    f"importer does not read")


def _usage(raw: object) -> int:
    """A HID usage from the file, a whole number or its ASCII decimal digits as G HUB
    writes it, else -1: int() alone takes "1_0" as 10, and a sign, outer spaces and
    non-ASCII digits."""
    if whole(raw):
        return raw
    if isinstance(raw, str) and raw.isascii() and raw.isdigit():
        with contextlib.suppress(ValueError):   # more digits than int() converts
            return int(raw)
    return -1


def _joined(steps: list[dict]) -> list[dict]:
    """A press straight followed by its own release is one step, a tap."""
    out: list[dict] = []
    for step in steps:
        last = out[-1] if out else None
        if (last is not None and last["type"] == KEY_DOWN and step["type"] == KEY_UP
                and last["value"] == step["value"]):
            out[-1] = {"type": KEY, "value": step["value"]}
        elif (last is not None and last["type"] == CLICK_DOWN and step["type"] == CLICK_UP
                and last["button"] == step["button"]):
            out[-1] = {"type": CLICK, "button": step["button"]}
        else:
            out.append(step)
    return out


# -- lighting --------------------------------------------------------------

@dataclass
class GhubLighting:
    """A per-key layout: Gearbench zone names to colors, over a dark board."""

    keys: dict[str, int]
    #: Colors G HUB sets on something with no Gearbench zone, or that `_color` cannot read.
    unmatched: int = 0

    @property
    def base(self) -> int:
        """The Static color: the color most keys have."""
        return Counter(self.keys.values()).most_common(1)[0][0]


def convert_lighting(settings: dict, card: object, read: dict[str, GhubLighting | None],
                     ) -> tuple[GhubLighting | None, str]:
    """The keyboard's lighting from a profile's lighting card, or None and why, as the
    preview's Lighting section says it (ghub_plan.details). `read` holds the effect records
    already converted: a file can name one record from thousands of effects and cards."""
    effects = _dig(card, "softwareLightingSettings").get("effects")
    if not effects or not isinstance(effects, list):
        return None, "none set in G HUB"
    for effect in effects:
        key = f"lighting_effects/{_dig(effect).get('id')}"
        if key not in read:
            read[key] = _per_key(_dig(settings, key))
        lighting = read[key]
        if lighting is not None:
            return lighting, ""
    return None, "a G HUB effect this importer does not read"


def _per_key(record: dict) -> GhubLighting | None:
    """An effect record's colors, or None if it is for another device or sets
    no color Gearbench can read on a key it has."""
    support = record.get("deviceSupport")
    if not isinstance(support, list) or PER_KEY_DEVICE not in support:
        return None
    lighting = GhubLighting(keys={})
    for group, info in _dig(record, "fixedInfo", "lightingSlots", "infoMap").items():
        names = LIGHT_GROUPS.get(group, {})
        for code, value in _dig(info, "perKeyMap", "colorCodeMap").items():
            zone = names.get(_usage(code))
            color = _color(_dig(value).get("hex"))
            if zone is None or color is None:
                lighting.unmatched += 1
            else:
                lighting.keys[zone] = color
    return lighting if lighting.keys else None


def _dig(value: object, *keys: str) -> dict:
    """value[k1][k2]..., or {} wherever the document holds something else."""
    for key in keys:
        value = value.get(key) if isinstance(value, dict) else None
    return value if isinstance(value, dict) else {}


def _color(text: object) -> int | None:
    """A color from `#rrggbb` text, the "#" optional, as config reads one, or None. G HUB
    writes a color as text, so a number, which config also takes, is None here."""
    if not isinstance(text, str):
        return None
    try:
        return parse_color(text, "")
    except ConfigError:
        return None


# -- profiles --------------------------------------------------------------

Binding = str | MacroRef | None
#: A profile's slot as `_slot` reads its id: (device, control, bank, shifted).
Slot = tuple[str, str, int, bool]


@dataclass
class GhubProfile:
    """One G HUB profile, as Gearbench would take it."""

    #: The Gearbench name: the application's, as G HUB shows it, or "Application: Profile"
    #: for a profile with its own name in an application with more than one profile, with
    #: " (2)" and on added to keep it unique.
    name: str
    application: str
    #: G HUB's own name for the profile, "" for an application's default.
    ghub_name: str = ""
    #: The desktop's default profile, which goes onto the M profiles.
    desktop: bool = False
    #: The one G HUB loads for its application (`activeForApplication`).
    active: bool = False
    processes: list[str] = field(default_factory=list)
    #: M bank to (G keys, shift layer). A game profile takes bank 1.
    banks: dict[int, tuple[dict[str, Binding], dict[str, Binding]]] = field(default_factory=dict)
    #: By mouse model (mouse.model_key), each mouse the import reads (mice_in): its buttons,
    #: by the names Gearbench binds them under, to what each is bound to. A button missing
    #: keeps its model's default.
    mouse: dict[str, dict[str, Binding]] = field(default_factory=dict)
    lighting: GhubLighting | None = None
    #: Why `lighting` is None, for the preview's Lighting section; "" when it is not.
    lighting_note: str = ""
    #: The macro cards it uses, in the order it uses them.
    macros: list[str] = field(default_factory=list)
    #: What G HUB calls a key binding, by where it is bound (`key_where` for a G key,
    #: `mouse_where` for a button), when it is a card with a name of its own: "Use Med
    #: Kit" for its key.
    labels: dict[str, str] = field(default_factory=dict)
    #: What is not imported, one sentence each.
    skipped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def search_text(self) -> str:
        return " ".join((self.name, self.application, self.ghub_name, *self.processes)).casefold()

    def key_where(self, key: str, shifted: bool, bank: int) -> str:
        """How the preview names the G key `key` on `bank`, and what `labels` holds its label
        under: "G1", "G1 with MR", "G1 on M2". A game profile has one bank, so its keys name
        none."""
        return key + (" with MR" if shifted else "") + (f" on M{bank}" if self.desktop else "")


@dataclass
class Found:
    """Everything the file holds that Gearbench can take."""

    profiles: list[GhubProfile]
    #: Each macro card on a G key or button the import takes (`_Reader.use_macro`), by
    #: card id, those that cannot be imported (steps None) included.
    macros: dict[str, GhubMacro]
    #: Devices with buttons in the file that this does not import.
    other_devices: list[str]

    def get(self, name: str) -> GhubProfile | None:
        return by_name(self.profiles, name)

    def steps(self, card: str) -> list[dict]:
        """The steps of a macro card a profile lists in its `macros`: a profile lists a
        card only once its macro has them (`_Reader.use_macro`)."""
        return cast(list[dict], self.macros[card].steps)

    @property
    def desktop(self) -> GhubProfile | None:
        return next((p for p in self.profiles if p.desktop), None)

    @property
    def games(self) -> list[GhubProfile]:
        return [p for p in self.profiles if not p.desktop]


def _slot(slot_id: str) -> Slot | None:
    """`g915_g1_m2_shifted` as (device, control, bank, shifted)."""
    device, _, rest = slot_id.partition("_")
    shifted = rest.endswith("_shifted")
    rest = rest.removesuffix("_shifted")
    control, _, bank = rest.rpartition("_")
    if not (device and bank.startswith("m") and bank[1:].isascii() and bank[1:].isdigit()):
        return None
    if not (control.startswith(("g", "scroll")) and control.lstrip("gscrol").isdigit()):
        return None
    try:
        number = int(bank[1:])
    except ValueError:
        # More than 4300 digits, which int() refuses.
        return None
    return device, control, number, shifted


def parse(settings: dict, name: str = "the file") -> Found:
    """What Gearbench can take from G HUB's settings document, read from the
    file `name`, which a refusal names."""
    try:
        return _Reader(settings).found()
    except RecursionError:
        # json.loads builds nesting deeper than str(), repr() and == can
        # walk, and parsing turns the file's values into text and compares them.
        raise GhubError(f"{name}: {TOO_DEEP}") from None


class _Reader:
    """The state of one parse: the document's records, what is worked out once
    for each application, card and lighting record, and the names, macros and
    other devices found so far."""

    def __init__(self, settings: dict) -> None:
        self.settings = settings
        self.cards = {c["id"]: c for c in _records(settings, "cards")
                      if isinstance(c.get("id"), str)}
        self.apps = {_app_id(a): a for a in _records(settings, "applications") if _app_id(a)}
        self.records = _records(settings, "profiles")
        self.per_app = Counter(_app_id(p) for p in self.records)
        # Once per application and per card: a file can give one application thousands of
        # profiles and one card thousands of keys.
        self.names = {app_id: _app_name(app) for app_id, app in self.apps.items()}
        self.programs = {app_id: _processes(app) for app_id, app in self.apps.items()}
        self.actions: dict[str, tuple[Action, bool]] = {}
        self.lightings: dict[str, tuple[GhubLighting | None, str]] = {}
        self.effects: dict[str, GhubLighting | None] = {}
        self.macros: dict[str, GhubMacro] = {}
        self.others: set[str] = set()
        self.taken = Taken()

    def found(self) -> Found:
        profiles = []
        # Not imported text left for the file: each use of a card repeats its name and
        # reason, so a crafted file can make many times its own size in lines.
        budget = MAX_DOCUMENT_BYTES
        for record in self.records:
            profile = self.profile(record)
            budget -= sum(map(len, profile.skipped))
            if budget < 0 and profile.skipped:
                profile.skipped = [(f"{counted(len(profile.skipped), 'line')} not shown: the "
                                    f"file's Not imported text is over "
                                    f"{MAX_DOCUMENT_BYTES // 1_000_000} million characters")]
            profiles.append(profile)
        return Found(profiles=profiles, macros=self.macros, other_devices=sorted(self.others))

    def card_action_at(self, card_id: object, where: str, skipped: list[str]) -> Action:
        """What the card does (card_action); a name it cut is said in
        `skipped`, at `where`."""
        if not isinstance(card_id, str):
            return card_action(card_id, self.cards)
        if card_id not in self.actions:
            cut = card_id in self.cards and _name(self.cards[card_id].get("name"))[1]
            self.actions[card_id] = card_action(card_id, self.cards), cut
        act, cut = self.actions[card_id]
        if cut:
            skipped.append(f"{where}: its card's name after {MAX_NAME_CHARS} characters, a "
                           f"name is cut there")
        return act

    def lighting(self, card_id: object) -> tuple[GhubLighting | None, str]:
        """The keyboard's lighting from a lighting card, or None and why (convert_lighting)."""
        if not isinstance(card_id, str):
            return convert_lighting(self.settings, None, self.effects)
        if (card := self.cards.get(card_id)) is None:
            return None, "its lighting card is not in the file"
        if card_id not in self.lightings:
            self.lightings[card_id] = convert_lighting(self.settings, card, self.effects)
        return self.lightings[card_id]

    def use_macro(self, profile: GhubProfile, act: Action, where: str) -> Binding:
        """The binding to the macro card `act` names, for the control at
        `where`; one that cannot be imported is said in the profile's
        `skipped`."""
        if act.value not in self.macros:
            self.macros[act.value] = convert_macro(self.cards[act.value])
        macro = self.macros[act.value]
        if macro.steps is None:
            profile.skipped.append(f'{where}: macro "{macro.name}" {macro.reason}')
            return None
        if act.value not in profile.macros:
            profile.macros.append(act.value)
        return MacroRef(act.value)

    def profile(self, record: Mapping) -> GhubProfile:
        """What Gearbench can take from one profile record, under a name none
        before it has."""
        app_id = _app_id(record)
        app = self.apps.get(app_id) or {}
        application, long_application = self.names.get(app_id, (UNKNOWN_APP, False))
        programs = self.programs.get(app_id, ([], []))
        own, long_own = (("", False) if record.get("name") == DEFAULT_PROFILE
                         else _name(record.get("name")))
        desktop = app.get("name") == DESKTOP_APP and not own
        name = application
        if own and self.per_app[app_id] > 1:
            name = f"{application}: {own}"
        name = self.taken.unique(name)
        self.taken.add(name)
        processes, cut = ([], []) if desktop else (list(programs[0]), programs[1])
        profile = GhubProfile(name=name, application=application, ghub_name=own, desktop=desktop,
                              active=record.get("activeForApplication") is True,
                              processes=processes,
                              skipped=[*cut, *_said_cut("Application name", long_application),
                                       *_said_cut("G HUB's profile name", long_own)])

        slots = _slots(record, self.others)
        self.g_keys(profile, slots)
        self.mouse_buttons(profile, slots)
        self.mouse_not_rebound(profile, slots)

        profile.lighting, profile.lighting_note = self.lighting(record.get("lightingCard"))
        if profile.lighting is not None and profile.lighting.unmatched:
            n = profile.lighting.unmatched
            profile.skipped.append(f"Lighting: {counted(n, 'color')} Gearbench cannot read, or "
                                   f"on lights it has no key for")

        if not (desktop or profile.processes):
            # With no program taken, `cut` can only be the names over MAX_PROGRAM_BYTES.
            profile.notes.append(
                "None of G HUB's programs for it can be imported, so it loads by hand; add a "
                "program after importing." if cut else
                "G HUB records no program for it, so it loads by hand; add a program after "
                "importing.")
        return profile

    def slot_action(self, profile: GhubProfile, slots: Mapping[Slot, object], slot: Slot,
                    where: str) -> Action | None:
        """What the card on `slot` does (card_action_at, its cut name said in the profile's
        `skipped`), None for a slot the profile has no assignment on."""
        return self.card_action_at(slots[slot], where, profile.skipped) if slot in slots else None

    def g_keys(self, profile: GhubProfile, slots: Mapping[Slot, object]) -> None:
        """The profile's `banks`: each keyboard's G keys under the names its model binds them
        under (KEYBOARDS), the G915's always and another's where the profile has a slot of
        it, on M1 to M3 for the desktop and on M1 for a game, whose notes say so when its M2
        or M3 holds something of the user's. A key two keyboards bind under one name, as the
        G915 and the G815 do, takes one keyboard's card (g_key_from)."""
        keys = [(device, n, key) for device, model in KEYBOARDS.items()
                if device == KEYBOARD or any(slot[0] == device for slot in slots)
                for n, key in enumerate(model.g_key_names, 1)]
        # Each name, to the keys of each keyboard that binds one under it, (device, number).
        named = {name: [(device, n) for device, n, key in keys if key == name]
                 for _device, _n, name in keys}
        desktop = profile.desktop
        for bank in (1, 2, 3) if desktop else (1,):
            layers: tuple[dict[str, Binding], dict[str, Binding]] = ({}, {})
            for shifted, layer in ((False, layers[0]), (True, layers[1])):
                for key, holders in named.items():
                    where = profile.key_where(key, shifted, bank)
                    device, n = self.g_key_from(profile, slots, key, holders, (bank, shifted),
                                                where)
                    act = self.slot_action(profile, slots, (device, f"g{n}", bank, shifted),
                                           where)
                    # The base layer is filled first, so the MR layer's key has its
                    # binding to fall through to.
                    layer[key] = self.g_binding(profile, act, where,
                                                layers[0][key] if shifted else None)
            profile.banks[bank] = layers

        def users(device: str, n: int, bank: int, shifted: bool) -> bool:
            """Whether a G key on M2 or M3 holds something of the user's: not
            what M1 has there, and not its factory_card."""
            card = slots.get((device, f"g{n}", bank, shifted))
            return card is not None and card not in (factory_card(n, shifted),
                                                     slots.get((device, f"g{n}", 1, shifted)))

        if not desktop and any(users(device, n, bank, shifted) for device, n, _key in keys
                               for bank in (2, 3) for shifted in (False, True)):
            profile.notes.append("Its M2 and M3 G keys are not imported: a game profile has one "
                                 "set of G keys, and M1's are used.")

    def g_key_from(self, profile: GhubProfile, slots: Mapping[Slot, object],
                   key: str, holders: list[tuple[str, int]], layer: tuple[int, bool],
                   where: str) -> tuple[str, int]:
        """Which of `holders`, the keys (device, number) bound under the name `key`, the G key
        at `where` takes its card from on `layer` (bank, shifted): of those whose slot of it
        holds a card id, the first whose card is the user's, not its factory_card, else the
        first; else the first with a slot of it; else the first of all. Another with a card
        of the user's, not the same, is said in `skipped`."""
        on = {(device, n): slots[device, f"g{n}", *layer] for device, n in holders
              if (device, f"g{n}", *layer) in slots}
        # A slot with no card id holds no card (card_action) to take over another keyboard's.
        cards = {held: card for held, card in on.items() if isinstance(card, str)}
        own = [held for held, card in cards.items() if card != factory_card(held[1], layer[1])]
        for other in own[1:]:
            if cards[other] != cards[own[0]]:
                # Not the key's card, so a name it cut is not said.
                act = self.card_action_at(cards[other], where, [])
                kept, lost = KEYBOARDS[own[0][0]].name, KEYBOARDS[other[0]].name
                profile.skipped.append(f"{lost} {where}: {act.label}, Gearbench takes the "
                                       f"{kept}'s {key} for both keyboards")
        return (own or [*cards, *on, *holders])[0]

    def g_binding(self, profile: GhubProfile, act: Action | None, where: str,
                  under: Binding) -> Binding:
        """What the G key at `where` is bound to; what it cannot take is said in
        the profile's `skipped`. `under` is the key's base binding when `where` is
        on the MR layer, which an unbound key there falls through to."""
        if act is not None and act.kind == NOTHING and under is not None:
            profile.skipped.append(f"{where}: {act.label}, Gearbench cannot disable a key on the "
                                   f"MR layer, so it sends its binding without MR")
            return None
        if act is None or act.kind in (NOTHING, DEFAULT):
            return None
        if act.kind == KEYS:
            if act.label != act.value:
                profile.labels[where] = act.label
            return act.value
        if act.kind == MACRO:
            return self.use_macro(profile, act, where)
        why = act.value if act.kind == OTHER else "a G key takes only a key or a macro"
        profile.skipped.append(f"{where}: {act.label}, {why}")
        return None

    def mouse_buttons(self, profile: GhubProfile, slots: Mapping[Slot, object]) -> None:
        """The profile's `mouse`: for each mouse it reads (mice_in), each of the model's
        buttons G HUB binds; what one cannot take is said in `skipped`."""
        for device, model in mice_in(slots):
            mouse = profile.mouse[key_of(model)] = {}
            for control, button in mouse_slots(model).items():
                where = mouse_where(model, button)
                act = self.slot_action(profile, slots, (device, control, 1, False), where)
                self.mouse_button(profile, mouse, button, act, where)

    def mouse_button(self, profile: GhubProfile, mouse: dict[str, Binding], button: str,
                     act: Action | None, where: str) -> None:
        """What one button at `where` is bound to, into `mouse`, its model's bindings, or what
        it cannot take into the profile's `skipped`."""
        if act is None or act.kind == DEFAULT:
            return
        if act.kind == NOTHING:
            mouse[button] = "none"
        elif act.kind in (KEYS, ACTION):
            mouse[button] = act.value
            if act.kind == KEYS and act.label != act.value:
                profile.labels[where] = act.label
        elif act.kind == MACRO:
            bound = self.use_macro(profile, act, where)
            if bound is not None:
                mouse[button] = bound
        else:
            why = (act.value if act.kind == OTHER else
                   "a button takes no click; a macro's click step can send left, right "
                   "or middle")
            profile.skipped.append(f"{where}: {act.label}, {why}")

    def mouse_not_rebound(self, profile: GhubProfile, slots: Mapping[Slot, object]) -> None:
        """Said in the profile's `skipped`, for each mouse it reads (mice_in): a fixed control
        G HUB moved off its default card, and any card but the default on the mouse's G-Shift
        layer."""
        # Sorted once, and each model's labels made once: a file can hold hundreds of
        # thousands of slots, and a profile can read every mouse of MICE.
        ordered = sorted(slots.items(), key=str)
        for mouse, model in mice_in(slots):
            labels = mouse_labels(model)
            for control, (_label, default) in fixed_slots(model).items():
                card = slots.get((mouse, control, 1, False))
                if card is not None and card != builtin_id(default):
                    named = labels[control]
                    act = self.card_action_at(card, named, profile.skipped)
                    profile.skipped.append(f"{named}: {act.label}, Gearbench does not rebind it")
            for (device, control, bank, shifted), card in ordered:
                # The mouse's own controls on bank 1, as the rest reads the mouse: a slot id can
                # name any control and bank, and each would be a line.
                where = labels.get(control)
                if (device, bank) != (mouse, 1) or not shifted or where is None:
                    continue
                act = self.card_action_at(card, f"{where} with G-Shift", profile.skipped)
                if act.kind != DEFAULT:
                    profile.skipped.append(f"{where} with G-Shift: {act.label}, Gearbench has no "
                                           f"G-Shift layer on the mouse")


def _app_id(record: Mapping) -> str | None:
    found = record.get("applicationId")
    return found if isinstance(found, str) else None


def _app_name(app: Mapping) -> tuple[str, bool]:
    """The application's name, and whether it was cut."""
    if app.get("name") == DESKTOP_APP:
        return DESKTOP_NAME, False
    name, cut = _name(app.get("name"))
    return name or UNKNOWN_APP, cut


def _processes(app: Mapping) -> tuple[list[str], list[str]]:
    """The Windows program names G HUB records for an application, the first MAX_PROCESSES,
    and preview sentences for those left out: past that count, or over MAX_PROGRAM_BYTES,
    which the game profile refuses. Only a custom application has any: G HUB matches a game
    it knows by its own database, which is not in the file."""
    user = app.get("userPaths")
    paths = [app.get("applicationPath"), *(user if isinstance(user, list) else [])]
    found: list[str] = []
    # By the forms names_match compares: casefold() takes "STRASSE" for "Straße".
    seen: set[frozenset[str]] = set()
    said: list[str] = []
    for path in paths:
        if isinstance(path, str) and path.lower().endswith(".exe"):
            # Stripped as the game profile strips it.
            name = without_controls(PureWindowsPath(path).name).strip()
            # No name before .exe: a UNC share root (\\server\share.exe) has
            # none at all, and the game profile refuses an empty name.
            if not name[:-len(".exe")]:
                continue
            if len(name.encode()) > MAX_PROGRAM_BYTES:
                said = [(f"Program names over {MAX_PROGRAM_BYTES} bytes: no file name is "
                         f"that long")]
                continue
            if wanted_forms(name) in seen:
                continue
            if len(found) == MAX_PROCESSES:
                return found, [*said, (f"Programs after the first {MAX_PROCESSES}: a game "
                                       f"profile loads for {MAX_PROCESSES} at most")]
            seen.add(wanted_forms(name))
            found.append(name)
    return found, said


def factory_card(n: int, shifted: bool) -> str:
    """The card G HUB puts on a keyboard's G key `n` out of the box: the F key of its number, or
    on the G-Shift layer, the G-Shift default."""
    return builtin_id(SHIFT_DEFAULT if shifted else f"01{0x39 + n:02x}")


def mice_in(slots: Mapping[Slot, object]) -> list[tuple[str, MouseModel]]:
    """The mice of MICE whose buttons a profile's import reads, by G HUB's slot prefix: each
    the profile has a slot of, so a mouse G HUB has nothing for keeps an M profile's own."""
    return [(device, model) for device, model in MICE.items()
            if any(slot[0] == device for slot in slots)]


def mouse_slots(model: MouseModel) -> dict[str, str]:
    """G HUB's slots of a mouse model's buttons, to the names Gearbench binds them under. G
    HUB's slot gN is the button entry N - 1 of the onboard profile (ButtonSlot.index): so the
    G502 X PLUS's settings file has it, its paddle at index 4 being G HUB's g5 and Gearbench's
    G6, and so the G502 HERO's device package draws it. Taken for every model's table (Claude
    Code's call of 2026-10-18, pending the owner)."""
    return {f"g{slot.index + 1}": slot.name
            for slot in cast(ButtonTable, model.button_table).buttons}


def fixed_slots(model: MouseModel) -> dict[str, tuple[str, str]]:
    """A mouse model's other controls by G HUB's slot, which Gearbench does not rebind, with
    what each is and the card it holds out of the box: the three main buttons, the wheel
    tilts and a profile button at any entry past them no button or tilt holds, each at its
    entry's slot (mouse_slots' rule), and the wheel. Holding that card loses nothing;
    anything else is listed as not imported."""
    table = cast(ButtonTable, model.button_table)
    tilts = {f"g{index + 1}": ("wheel tilt left", "040b") if way < 0
             else ("wheel tilt right", "040c")
             for index, way in table.tilts}
    held = {slot.index for slot in table.buttons} | {index for index, _way in table.tilts}
    # Such an entry is the G903 LIGHTSPEED HERO's profile cycle at 11. Its card out of the box
    # is taken as the one the G502 X PLUS's G9 holds for that job: no settings file with a
    # G903 has been read.
    rest = {f"g{index + 1}": ("profile button", "0901")
            for index in range(3, table.count) if index not in held}
    return {"g1": ("left button", "0201"), "g2": ("right button", "0202"),
            "g3": ("middle button", "0203"), **tilts, **rest, "scroll1": ("wheel up", "0409"),
            "scroll2": ("wheel down", "040a")}


def button_label(model: MouseModel, button: str) -> str:
    """What the mouse model calls its button `button`: "G4 (side, rear)"."""
    return next(slot.label for slot in cast(ButtonTable, model.button_table).buttons
                if slot.name == button)


def mouse_where(model: MouseModel, button: str) -> str:
    """How the preview names a mouse button: "G502 X PLUS G4 (side, rear)"."""
    return f"{model.name} {button_label(model, button)}"


def mouse_labels(model: MouseModel) -> dict[str, str]:
    """Each G HUB slot of `model` by what Gearbench or the mouse calls it: a button's
    (mouse_slots) over another control's (fixed_slots)."""
    return ({control: f"{model.name} {label}"
             for control, (label, _card) in fixed_slots(model).items()}
            | {control: mouse_where(model, button)
               for control, button in mouse_slots(model).items()})


def _slots(record: Mapping, others: set[str]) -> dict[Slot, object]:
    """The card on each keyboard and mouse slot of a profile's assignments,
    by `_slot`; another device's name goes into `others`."""
    slots: dict[Slot, object] = {}
    assignments = record.get("assignments")
    for assignment in assignments if isinstance(assignments, list) else []:
        if not isinstance(assignment, dict) or not isinstance(assignment.get("slotId"), str):
            continue
        parsed = _slot(assignment["slotId"])
        if parsed is None:
            continue
        if parsed[0] not in (*KEYBOARDS, *MICE):
            # Cut as the file's other names are: the window names up to SHOWN_DEVICES of
            # them in one label.
            if device := _name(parsed[0])[0]:
                others.add(device)
            continue
        slots[parsed] = assignment.get("cardId")
    return slots
