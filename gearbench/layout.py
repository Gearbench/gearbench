"""Physical key positions for the G915, for drawing an editable keyboard.

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
"""

from __future__ import annotations

from dataclasses import dataclass

from .keycodes import ZONES


@dataclass(frozen=True)
class Key:
    label: str
    x: float
    y: float
    w: float = 1.0
    h: float = 1.0
    zone: int | None = None
    #: G keys are bindable; ordinary keys are lighting-only.
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
    *(Key(f"F{n}", start + i, 1.24, zone=ZONES[f"F{n}"])
      for start, first in ((_F1, 1), (_F5, 5), (_F9, 9))
      for i, n in enumerate(range(first, first + 4))),
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

ORIGIN_X = min(k.x for k in KEYS)
ORIGIN_Y = min(k.y for k in KEYS)
WIDTH = max(k.x + k.w for k in KEYS) - ORIGIN_X
HEIGHT = max(k.y + k.h for k in KEYS) - ORIGIN_Y
