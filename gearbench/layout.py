"""Physical key positions for the G915, the G515 TKL, the G915 TKL and the G815, for drawing an
editable keyboard: a `Board` for each, from `KEYS`, `G515_KEYS`, `G915_TKL_KEYS` and
`G815_KEYS`.

Positions are in key units: 1.0 is one keycap width, x grows right, y down. HID++ gives zone
ids but no geometry, so they come from G HUB's g915_us depot, whose metadata.json has a
rectangle per LED in front.png's pixels: x = (px - 269.3) / 154.5, y = (py - 20.8) / 154.5,
which puts ` at x 0 and the number row at y 2.5. The pitch, 154.5 px, is fitted over the main
block's 46 one-unit keys; at the image's scale (475 mm over 3850 px) it is 19.06 mm, one
standard key.

Widths are set by hand. The G column and the logo are 1.2, centered where the 1.0 keys are.
The bottom row has the G915's own 1.5, 1.25, 1.25, 5.75, 1.25, 1.25, 1.25, 1.5, since ANSI's
widths overlap at the measured positions. The round buttons are 0.9 square; they stand about
0.94 apart. Game is the metadata's gameMode hot area. M1 to M3, MR, Host and BT have no
rectangle: they are measured off front.png, scaled to 3850 px wide and shifted by the 3 to 5 px
its Game and Brightness buttons sit from the metadata's.

0x4540 KBLayout function 0 reports the board's layout, 01 for this US board in G HUB's
capture. Nothing reads it, and another layout's positions would still have to be drawn.

`zone` is the 0x8081 per-key lighting id, or None for a key 0x8081 does not light. M1-M3 and MR
light through 0x8020 and 0x8030, so they are drawn but not colorable here.

The G515 TKL's positions come from the key drawing on page 4 of Logitech's setup guide for the
wired G515 TKL (logitech.com/assets/70164/g515_web_qsg_amr.pdf), read at 600 dpi: 87 key boxes
at a pitch of 134.6 px, with ` at x 0 and the number row at y 2.5 as on the G915. Its keys take
the G915's zone ids, which Solaar's listing of the G515 LIGHTSPEED TKL's 0x8081 zone bitmap
gives once the ids no key has are dropped. On a wired G515 TKL the keys clicked lit at their ids,
FN at 111 (2026-10-17).

The G915 TKL's come from G HUB's g915_tkl_us package, which the G913 TKL shares: its
metadata.json's LED rectangles, in front.png's pixels at 116.45 px a key across and 116.95
down, fitted over the main block's 46 one-unit keys with ` at x 0 and the number row at y 2.5.
They put Esc, the main block, its bottom row and the navigation block within 0.03 of the G915's,
so those are the G915's keys; its function row, logo, round buttons and media keys are its own.
The key printed FN lights at G HUB's id for the right Super, zone 111, as on the G515 TKL. Its
LIGHTSPEED and Bluetooth buttons have no rectangle: they stand one round-button pitch apart left
of Game Mode, where front.png draws them (read 2026-10-18).

The G815's come from the key drawing on page 4 of Logitech's quick-start guide for it
(logitech.com/assets/65841/), read at 1200 dpi on 2026-10-18: a pitch of 138.46 px, with ` at
x 0 and the number row at y 2.5. From the function row down its keys stand within 0.02 of the
G915's, its bottom row of the G915's widths, so those are the G915's keys. Its top row has the
logo, M1 to M3 and MR at the G915's places, then Game Mode and Brightness where the G915 has
LIGHTSPEED and Bluetooth: it has no wireless buttons.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .keycodes import ZONES


@dataclass(frozen=True)
class Key:
    label: str
    x: float
    y: float
    w: float = 1.0
    h: float = 1.0
    zone: int | None = None
    #: G keys are bindable, the G915 TKL's F row among them; ordinary keys are lighting-only.
    bindable: bool = False
    #: M1-M3, MR, Host, BT and Game do the keyboard's own jobs and are
    #: neither bindable nor colorable.
    reserved: bool = False


#: Where the blocks start, in key units, from G HUB's rectangles: the function row's three
#: groups, the navigation block (Print Screen down to Left), the keypad and the G column.
_F1, _F5, _F9 = 1.67, 6.34, 11.0
_NAV = 15.35
_PAD = 18.7
_G = -1.35

#: The top row's round buttons and the media keys, drawn this size square.
_ROUND = 0.9


def _function_row(starts: tuple[float, float, float], y: float) -> tuple[Key, ...]:
    """F1 to F12 at `y`, in their three groups of four, each from its start in `starts`."""
    return tuple(Key(f"F{n}", start + i, y, zone=ZONES[f"F{n}"])
                 for start, first in zip(starts, (1, 5, 9), strict=True)
                 for i, n in enumerate(range(first, first + 4)))


#: Full-size ANSI plus the G915's own extras: the logo and the buttons above the function row,
#: the media keys at its right, and G1-G5 at the left.
KEYS: tuple[Key, ...] = (
    # Top row, in the quick-start guide's order: logo, M keys, LIGHTSPEED (Host), Bluetooth,
    # Game Mode and brightness. The volume roller is not a key and is not drawn.
    Key("Logo", _G, 0.08, w=1.2, zone=ZONES["LOGO"]),
    *(Key(label, x, 0.1, w=_ROUND, h=_ROUND, reserved=True) for label, x in (
        ("M1", 1.79), ("M2", 2.73), ("M3", 3.68), ("MR", 4.62),
        ("Host", 6.45), ("BT", 7.4), ("Game", 8.35))),
    Key("Bright", 9.28, 0.1, w=_ROUND, h=_ROUND, zone=ZONES["BRIGHTNESS"]),

    # Function row.
    Key("Esc", 0, 1.24, zone=ZONES["ESC"]),
    *_function_row((_F1, _F5, _F9), 1.24),
    Key("PrtSc", _NAV, 1.24, zone=ZONES["PRINT"]),
    Key("ScrLk", _NAV + 1, 1.24, zone=ZONES["SCROLL LOCK"]),
    Key("Pause", _NAV + 2, 1.24, zone=ZONES["PAUSE"]),
    *(Key(label, x, 1.3, w=_ROUND, h=_ROUND, zone=ZONES[name]) for label, name, x in (
        ("Prev", "PREVIOUS", 18.83), ("Play", "PLAY/PAUSE", 19.77),
        ("Next", "NEXT", 20.72), ("Mute", "MUTE", 21.66))),

    # Number row.
    Key("G1", _G, 2.5, w=1.2, zone=ZONES["G1"], bindable=True),
    Key("`", 0, 2.5, zone=ZONES["`"]),
    *(Key(d, 1 + i, 2.5, zone=ZONES[d]) for i, d in enumerate("1234567890-=")),
    Key("Backspace", 13, 2.5, w=2, zone=ZONES["BACKSPACE"]),
    Key("Ins", _NAV, 2.5, zone=ZONES["INSERT"]),
    Key("Home", _NAV + 1, 2.5, zone=ZONES["HOME"]),
    Key("PgUp", _NAV + 2, 2.5, zone=ZONES["PAGE UP"]),
    Key("Num", _PAD, 2.5, zone=ZONES["NUMLOCK"]),
    *(Key(c, _PAD + i, 2.5, zone=ZONES[f"KEYPAD {c}"]) for i, c in enumerate("/*-", 1)),

    # Tab row.
    Key("G2", _G, 3.5, w=1.2, zone=ZONES["G2"], bindable=True),
    Key("Tab", 0, 3.5, w=1.5, zone=ZONES["TAB"]),
    *(Key(c, 1.5 + i, 3.5, zone=ZONES[c]) for i, c in enumerate("QWERTYUIOP[]")),
    Key("\\", 13.5, 3.5, w=1.5, zone=ZONES["\\"]),
    Key("Del", _NAV, 3.5, zone=ZONES["DELETE"]),
    Key("End", _NAV + 1, 3.5, zone=ZONES["END"]),
    Key("PgDn", _NAV + 2, 3.5, zone=ZONES["PAGE DOWN"]),
    *(Key(d, _PAD + i, 3.5, zone=ZONES[f"KEYPAD {d}"]) for i, d in enumerate("789")),
    Key("+", _PAD + 3, 3.5, h=2, zone=ZONES["KEYPAD +"]),

    # Home row.
    Key("G3", _G, 4.5, w=1.2, zone=ZONES["G3"], bindable=True),
    Key("Caps", 0, 4.5, w=1.75, zone=ZONES["CAPS LOCK"]),
    *(Key(c, 1.75 + i, 4.5, zone=ZONES[c]) for i, c in enumerate("ASDFGHJKL;'")),
    Key("Enter", 12.75, 4.5, w=2.25, zone=ZONES["ENTER"]),
    *(Key(d, _PAD + i, 4.5, zone=ZONES[f"KEYPAD {d}"]) for i, d in enumerate("456")),

    # Shift row.
    Key("G4", _G, 5.5, w=1.2, zone=ZONES["G4"], bindable=True),
    Key("Shift", 0, 5.5, w=2.25, zone=ZONES["LEFT SHIFT"]),
    *(Key(c, 2.25 + i, 5.5, zone=ZONES[c]) for i, c in enumerate("ZXCVBNM,./")),
    Key("Shift", 12.25, 5.5, w=2.75, zone=ZONES["RIGHT SHIFT"]),
    Key("Up", _NAV + 1, 5.5, zone=ZONES["UP"]),
    *(Key(d, _PAD + i, 5.5, zone=ZONES[f"KEYPAD {d}"]) for i, d in enumerate("123")),
    Key("Enter", _PAD + 3, 5.5, h=2, zone=ZONES["KEYPAD ENTER"]),

    # Bottom row.
    Key("G5", _G, 6.5, w=1.2, zone=ZONES["G5"], bindable=True),
    Key("Ctrl", 0, 6.5, w=1.5, zone=ZONES["LEFT CTRL"]),
    Key("Super", 1.5, 6.5, w=1.25, zone=ZONES["LEFT WINDOWS"]),
    Key("Alt", 2.75, 6.5, w=1.25, zone=ZONES["LEFT ALT"]),
    Key("Space", 4, 6.5, w=5.75, zone=ZONES["SPACE"]),
    Key("Alt", 9.75, 6.5, w=1.25, zone=ZONES["RIGHT ALTGR"]),
    Key("Super", 11, 6.5, w=1.25, zone=ZONES["RIGHT WINDOWS"]),
    Key("Menu", 12.25, 6.5, w=1.25, zone=ZONES["COMPOSE"]),
    Key("Ctrl", 13.5, 6.5, w=1.5, zone=ZONES["RIGHT CTRL"]),
    Key("Left", _NAV, 6.5, zone=ZONES["LEFT"]),
    Key("Down", _NAV + 1, 6.5, zone=ZONES["DOWN"]),
    Key("Right", _NAV + 2, 6.5, zone=ZONES["RIGHT"]),
    Key("0", _PAD, 6.5, w=2, zone=ZONES["KEYPAD 0"]),
    Key(".", _PAD + 2, 6.5, zone=ZONES["KEYPAD ."]),
)

#: The G515 TKL's blocks: where the function row's three groups and the navigation block start,
#: and the function row's top, measured off the guide's drawing.
_G515_F1, _G515_F5, _G515_F9 = 2.0, 6.5, 11.0
_G515_NAV = 15.2
_G515_TOP = 1.32

#: The G515 TKL, US ANSI: its 87 lit keys, and the Game Mode button above Esc, drawn the round
#: buttons' size around the guide's 0.44 square. Shared by the wired-only model and the G515
#: LIGHTSPEED TKL, whose guide was not read.
G515_KEYS: tuple[Key, ...] = (
    Key("Game", 0.5, 0.14, w=_ROUND, h=_ROUND, reserved=True),

    Key("Esc", 0, _G515_TOP, zone=ZONES["ESC"]),
    *_function_row((_G515_F1, _G515_F5, _G515_F9), _G515_TOP),
    *(Key(label, _G515_NAV + i, _G515_TOP, zone=ZONES[name]) for i, (label, name) in
      enumerate((("PrtSc", "PRINT"), ("ScrLk", "SCROLL LOCK"), ("Pause", "PAUSE")))),

    Key("`", 0, 2.5, zone=ZONES["`"]),
    *(Key(d, 1 + i, 2.5, zone=ZONES[d]) for i, d in enumerate("1234567890-=")),
    Key("Backspace", 13, 2.5, w=2, zone=ZONES["BACKSPACE"]),
    *(Key(label, _G515_NAV + i, 2.5, zone=ZONES[name]) for i, (label, name) in
      enumerate((("Ins", "INSERT"), ("Home", "HOME"), ("PgUp", "PAGE UP")))),

    Key("Tab", 0, 3.5, w=1.5, zone=ZONES["TAB"]),
    *(Key(c, 1.5 + i, 3.5, zone=ZONES[c]) for i, c in enumerate("QWERTYUIOP[]")),
    Key("\\", 13.5, 3.5, w=1.5, zone=ZONES["\\"]),
    *(Key(label, _G515_NAV + i, 3.5, zone=ZONES[name]) for i, (label, name) in
      enumerate((("Del", "DELETE"), ("End", "END"), ("PgDn", "PAGE DOWN")))),

    Key("Caps", 0, 4.5, w=1.75, zone=ZONES["CAPS LOCK"]),
    *(Key(c, 1.75 + i, 4.5, zone=ZONES[c]) for i, c in enumerate("ASDFGHJKL;'")),
    Key("Enter", 12.75, 4.5, w=2.25, zone=ZONES["ENTER"]),

    Key("Shift", 0, 5.5, w=2.25, zone=ZONES["LEFT SHIFT"]),
    *(Key(c, 2.25 + i, 5.5, zone=ZONES[c]) for i, c in enumerate("ZXCVBNM,./")),
    Key("Shift", 12.25, 5.5, w=2.75, zone=ZONES["RIGHT SHIFT"]),
    Key("Up", _G515_NAV + 1, 5.5, zone=ZONES["UP"]),

    # ANSI's bottom row. FN is 111, the G915's right Super, where a wired G515 TKL's FN lit.
    *(Key(label, x, 6.5, w=w, zone=ZONES[name]) for label, x, w, name in (
        ("Ctrl", 0, 1.25, "LEFT CTRL"), ("Super", 1.25, 1.25, "LEFT WINDOWS"),
        ("Alt", 2.5, 1.25, "LEFT ALT"), ("Space", 3.75, 6.25, "SPACE"),
        ("Alt", 10, 1.25, "RIGHT ALTGR"), ("FN", 11.25, 1.25, "RIGHT WINDOWS"),
        ("Menu", 12.5, 1.25, "COMPOSE"), ("Ctrl", 13.75, 1.25, "RIGHT CTRL"))),
    *(Key(label, _G515_NAV + i, 6.5, zone=ZONES[name]) for i, (label, name) in
      enumerate((("Left", "LEFT"), ("Down", "DOWN"), ("Right", "RIGHT")))),
)

#: The G915 TKL's own blocks, from G HUB's rectangles: where its function row's three groups
#: start, and the top of its round buttons and media keys.
_TKL_F1, _TKL_F5, _TKL_F9 = 1.65, 6.33, 11.0
_TKL_ROUND_TOP = 0.09
_F_ROW_ZONES = frozenset(ZONES[f"F{n}"] for n in range(1, 13))

#: The G915 TKL, US ANSI: the G915's keys from Esc to the arrows, its function row aside and its
#: right Super printed FN; and its own function row, logo, LIGHTSPEED, Bluetooth and Game Mode
#: buttons, brightness and media keys. Its F1 to F12 are its G1 to G12, bindable
#: (keyboard.G915_TKL).
G915_TKL_KEYS: tuple[Key, ...] = tuple(sorted((
    Key("Logo", 0.05, 0.11, zone=ZONES["LOGO"]),
    *(Key(label, x, _TKL_ROUND_TOP, w=_ROUND, h=_ROUND, reserved=True)
      for label, x in (("Host", 1.83), ("BT", 2.77), ("Game", 3.71))),
    Key("Bright", 4.64, _TKL_ROUND_TOP, w=_ROUND, h=_ROUND, zone=ZONES["BRIGHTNESS"]),
    *(Key(label, x, _TKL_ROUND_TOP, w=_ROUND, h=_ROUND, zone=ZONES[name]) for label, name, x in (
        ("Prev", "PREVIOUS", 11.12), ("Play", "PLAY/PAUSE", 12.06),
        ("Next", "NEXT", 13.0), ("Mute", "MUTE", 13.92))),
    *(replace(key, bindable=True) for key in _function_row((_TKL_F1, _TKL_F5, _TKL_F9), 1.24)),
    *(replace(key, label="FN") if key.zone == ZONES["RIGHT WINDOWS"] else key
      for key in KEYS if key.y >= 1 and 0 <= key.x < _PAD and key.zone not in _F_ROW_ZONES),
), key=lambda key: (int(key.y), key.x)))

#: The G915's top-row buttons by label, whose places the G815's take.
_G915_TOP = {key.label: key for key in KEYS if key.y < 1}

#: The G815, US ANSI: the G915's keys from the function row down, G1 to G5 and the keypad
#: among them, and its top row: the G915's logo, M1 to M3 and MR, with Game Mode and
#: Brightness where the G915 has LIGHTSPEED and Bluetooth.
G815_KEYS: tuple[Key, ...] = (
    *(_G915_TOP[label] for label in ("Logo", "M1", "M2", "M3", "MR")),
    replace(_G915_TOP["Host"], label="Game"),
    replace(_G915_TOP["BT"], label="Bright", zone=ZONES["BRIGHTNESS"], reserved=False),
    *(key for key in KEYS if key.y >= 1),
)


@dataclass(frozen=True)
class Board:
    """One model's drawn keyboard: its keys, the corner the drawing starts at and its size,
    in key units, and the per-key zones its keys light."""

    keys: tuple[Key, ...]
    origin_x: float
    origin_y: float
    width: float
    height: float
    zones: frozenset[int]


def board(keys: tuple[Key, ...]) -> Board:
    """The Board `keys` make."""
    origin_x, origin_y = min(k.x for k in keys), min(k.y for k in keys)
    return Board(keys, origin_x, origin_y, max(k.x + k.w for k in keys) - origin_x,
                 max(k.y + k.h for k in keys) - origin_y,
                 frozenset(k.zone for k in keys if k.zone is not None))


G915_BOARD = board(KEYS)
G515_BOARD = board(G515_KEYS)
G915_TKL_BOARD = board(G915_TKL_KEYS)
G815_BOARD = board(G815_KEYS)
