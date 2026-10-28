"""Physical key positions of each keyboard model Gearbench drives, for drawing an editable
keyboard: a `Board` for each, and the keys the G515 generation's boards light while FN is held.

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
at a pitch of 134.6 px, with ` at x 0 and the number row at y 2.5 as on the G915. Its main
block from the number row to the shift row stands within 0.03 of the G915's, so those are the
G915's keys. Its keys take the G915's zone ids, which Solaar's listing of the G515 LIGHTSPEED
TKL's 0x8081 zone bitmap gives once the ids no key has are dropped. On a wired G515 TKL the keys
clicked lit at their ids, FN at 111 (2026-10-17).

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

The PRO X TKL LIGHTSPEED's come from G HUB's package of its US layout (e58b079f-...,
fetched 2026-10-21, its sha256 the manifest's): its metadata.json's LED rectangles, at 194.09
px a key with ` at x 0 and the number row at y 2.5. From the number row down they stand
within 0.05 of the G515 TKL's, so those are the G515's keys; its function row is the G515's
at its own height, and its top row its own: LIGHTSPEED, Bluetooth, Game Mode and Brightness
one pitch apart, Brightness on G HUB's indicator light and Game Mode on its hot area, and the
media keys. Its light_map.json gives the top row's lights ids of their own (keycodes.ZONES).
Its logo has no light. Logitech's setup guide for it draws the same rows (page 6).

The G213 Prodigy's come from G HUB's g213_us package (fetched 2026-10-21, its sha256 the
manifest's): its metadata.json's rectangles, at 119.75 px a key. From the function row down
they stand within 0.1 of the G915's, its bottom row of the G915's widths, so those are the
G915's keys, less its G keys and media keys. Its own are the logo, Game Mode, Brightness and
Mute above the function row, and Play, Stop, Previous and Next above the keypad. The package
puts every key in one of five lighting zones, which are all the G213 lights by: Esc to F3, the
logo above them and the keys below them to C and Left Alt; F4 to F8, 5 to 9 and the keys below
them to M and Space; F9 to F12 and the rest of the main block; the navigation block and arrows
with Game Mode and Brightness; and the keypad with the media keys. Those are its board's areas
(`G213_AREAS`). Its keys are drawn with the G915's per-key ids all the same, which name the
keys a profile keeps colors for: the G213 has no per-key lighting, so each area shows one
color (`Board.area_colors`).

The G515 generation's other boards come from G HUB's US layout package for each model, fetched
from its update server on 2026-10-21: metadata.json's rectangle or circle for each LED, whose
ids give the zones as the G915's package's do, its nine G keys G1 to G9. Fitted over each
board's lit keys, every key stands within 0.1 of its LED's center. The
G915 X's keys are the G915's from the function row down, ANSI's bottom row with FN at 111 in
its own, and G6 to G9 in the places of the G915's M1 to M3 and MR; the wired one has Game Mode
and Brightness where the G915 has LIGHTSPEED and Bluetooth, the LIGHTSPEED one all four at the
G915's places, as their front pictures show. The G915 X LIGHTSPEED TKL's are the G915 TKL's with
that bottom row. The 98% and 75% boards, the G316 X's and the G512 X's, take the G515 TKL's keys
from the number row to the shift row, its right Shift narrower, and its bottom row to Space,
and the 98% boards the G915's keypad, moved left with a 1-unit 0; their top row, the rest of
their bottom row, arrows and right column are their own, rounded to 0.05. Their light bars,
LED ids 160 to 221 that overlap G1 to G9's zones on the G512 X, are not drawn, nor are their
dials or the G316 X's display.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace

from .config import (
    G316X_F_KEYS,
    G512X75_F_KEYS,
    G512X98_F_KEYS,
    G515_F_KEYS,
    G915X_G_KEYS,
    G915XTKL_F_KEYS,
    PRO_X_TKL_F_KEYS,
)
from .keycodes import ZONES


@dataclass(frozen=True)
class Key:
    label: str
    x: float
    y: float
    w: float = 1.0
    h: float = 1.0
    zone: int | None = None
    #: G keys are bindable, on some boards the F row too; ordinary keys are lighting-only.
    bindable: bool = False
    #: The name a bindable key binds under (config.BINDABLE_KEYS) where it is not its label:
    #: the G515's F1 binds as G515-F1.
    name: str | None = None

    @property
    def binds_as(self) -> str:
        """The name the key's binding is kept under: `name`, else its label."""
        return self.name or self.label

    @property
    def reserved(self) -> bool:
        """A key with no zone, which the window neither colors nor binds: the M keys, Game Mode
        and the like, which do the keyboard's own jobs."""
        return self.zone is None

    @property
    def number(self) -> int:
        """A bindable key's number, from its label: 1 for G1 or F1."""
        return int(self.label[1:])


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
    *(Key(label, x, 0.1, w=_ROUND, h=_ROUND) for label, x in (
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

#: ANSI's bottom row as the G515 TKL has it, which the G915 X family's boards share. FN is
#: 111, the G915's right Super, where a wired G515 TKL's FN lit.
_ANSI_BOTTOM: tuple[Key, ...] = tuple(Key(label, x, 6.5, w=w, zone=ZONES[name])
                                      for label, x, w, name in (
    ("Ctrl", 0, 1.25, "LEFT CTRL"), ("Super", 1.25, 1.25, "LEFT WINDOWS"),
    ("Alt", 2.5, 1.25, "LEFT ALT"), ("Space", 3.75, 6.25, "SPACE"),
    ("Alt", 10, 1.25, "RIGHT ALTGR"), ("FN", 11.25, 1.25, "RIGHT WINDOWS"),
    ("Menu", 12.5, 1.25, "COMPOSE"), ("Ctrl", 13.75, 1.25, "RIGHT CTRL")))

#: The G515 TKL, US ANSI: its 87 lit keys, and the Game Mode button above Esc, drawn the round
#: buttons' size around the guide's 0.44 square. Shared by the wired-only model and the G515
#: LIGHTSPEED TKL, whose guide was not read. Its F1 to F12 are bindable, as G515-F1 to
#: G515-F12 (keyboard.G515_TKL).
G515_KEYS: tuple[Key, ...] = (
    Key("Game", 0.5, 0.14, w=_ROUND, h=_ROUND),

    Key("Esc", 0, _G515_TOP, zone=ZONES["ESC"]),
    *(replace(key, bindable=True, name=name) for key, name in
      zip(_function_row((_G515_F1, _G515_F5, _G515_F9), _G515_TOP), G515_F_KEYS, strict=True)),
    *(Key(label, _G515_NAV + i, _G515_TOP, zone=ZONES[name]) for i, (label, name) in
      enumerate((("PrtSc", "PRINT"), ("ScrLk", "SCROLL LOCK"), ("Pause", "PAUSE")))),

    # Number row to shift row: the G915's main block, which the guide's drawing agrees with.
    *(key for key in KEYS if key.y == 2.5 and 0 <= key.x < _NAV),
    *(Key(label, _G515_NAV + i, 2.5, zone=ZONES[name]) for i, (label, name) in
      enumerate((("Ins", "INSERT"), ("Home", "HOME"), ("PgUp", "PAGE UP")))),

    *(key for key in KEYS if key.y == 3.5 and 0 <= key.x < _NAV),
    *(Key(label, _G515_NAV + i, 3.5, zone=ZONES[name]) for i, (label, name) in
      enumerate((("Del", "DELETE"), ("End", "END"), ("PgDn", "PAGE DOWN")))),

    *(key for key in KEYS if key.y == 4.5 and 0 <= key.x < _NAV),

    *(key for key in KEYS if key.y == 5.5 and 0 <= key.x < _NAV),
    Key("Up", _G515_NAV + 1, 5.5, zone=ZONES["UP"]),

    *_ANSI_BOTTOM,
    *(Key(label, _G515_NAV + i, 6.5, zone=ZONES[name]) for i, (label, name) in
      enumerate((("Left", "LEFT"), ("Down", "DOWN"), ("Right", "RIGHT")))),
)

#: The G915 TKL's own blocks, from G HUB's rectangles: where its function row's three groups
#: start, and the top of its round buttons and media keys.
_TKL_F1, _TKL_F5, _TKL_F9 = 1.65, 6.33, 11.0
_TKL_ROUND_TOP = 0.09
_F_ROW_ZONES = frozenset(ZONES[f"F{n}"] for n in range(1, 13))


def _by_row(keys: tuple[Key, ...]) -> tuple[Key, ...]:
    """`keys` row by row, each row left to right."""
    return tuple(sorted(keys, key=lambda key: (int(key.y), key.x)))


def _own_names(keys: Iterable[Key], names: tuple[str, ...]) -> tuple[Key, ...]:
    """`keys`, each bindable one, G1 or F1 on, bound under the name `names` gives its number."""
    return tuple(replace(key, name=names[key.number - 1])
                 if key.bindable else key for key in keys)


#: The G915 TKL, US ANSI: the G915's keys from Esc to the arrows, its function row aside and its
#: right Super printed FN; and its own function row, logo, LIGHTSPEED, Bluetooth and Game Mode
#: buttons, brightness and media keys. Its F1 to F12 are its G1 to G12, bindable
#: (keyboard.G915_TKL).
G915_TKL_KEYS: tuple[Key, ...] = _by_row((
    Key("Logo", 0.05, 0.11, zone=ZONES["LOGO"]),
    *(Key(label, x, _TKL_ROUND_TOP, w=_ROUND, h=_ROUND)
      for label, x in (("Host", 1.83), ("BT", 2.77), ("Game", 3.71))),
    Key("Bright", 4.64, _TKL_ROUND_TOP, w=_ROUND, h=_ROUND, zone=ZONES["BRIGHTNESS"]),
    *(Key(label, x, _TKL_ROUND_TOP, w=_ROUND, h=_ROUND, zone=ZONES[name]) for label, name, x in (
        ("Prev", "PREVIOUS", 11.12), ("Play", "PLAY/PAUSE", 12.06),
        ("Next", "NEXT", 13.0), ("Mute", "MUTE", 13.92))),
    *(replace(key, bindable=True) for key in _function_row((_TKL_F1, _TKL_F5, _TKL_F9), 1.24)),
    *(replace(key, label="FN") if key.zone == ZONES["RIGHT WINDOWS"] else key
      for key in KEYS if key.y >= 1 and 0 <= key.x < _PAD and key.zone not in _F_ROW_ZONES),
))

#: The G915's top-row keys by label.
_G915_TOP = {key.label: key for key in KEYS if key.y < 1}

#: The G815, US ANSI: the G915's keys from the function row down, G1 to G5 and the keypad
#: among them, and its top row: the G915's logo, M1 to M3 and MR, with Game Mode and
#: Brightness where the G915 has LIGHTSPEED and Bluetooth.
G815_KEYS: tuple[Key, ...] = (
    *(_G915_TOP[label] for label in ("Logo", "M1", "M2", "M3", "MR")),
    replace(_G915_TOP["Host"], label="Game"),
    replace(_G915_TOP["BT"], label="Bright", zone=ZONES["BRIGHTNESS"]),
    *(key for key in KEYS if key.y >= 1),
)

#: The PRO X TKL LIGHTSPEED's function row's top, and its top row's, from G HUB's rectangles,
#: its round buttons and media keys drawn the round buttons' size about G HUB's circles.
_PRO_X_TKL_TOP = 1.23
_PRO_X_TKL_ROUND_TOP = 0.05


#: The PRO X TKL LIGHTSPEED, US ANSI: its LIGHTSPEED, Bluetooth and Game Mode buttons, its
#: brightness button and media keys, each lit at the id its light_map.json gives it, and the
#: G515 TKL's keys below them. Its F1 to F12 are its G1 to G12, bindable as PROXTKL-F1 to
#: PROXTKL-F12 (keyboard.PRO_X_TKL).
PRO_X_TKL_KEYS: tuple[Key, ...] = (
    *(Key(label, x, _PRO_X_TKL_ROUND_TOP, w=_ROUND, h=_ROUND)
      for label, x in (("Host", 2.05), ("BT", 3.04), ("Game", 4.03))),
    *(Key(label, x, _PRO_X_TKL_ROUND_TOP, w=_ROUND, h=_ROUND, zone=zone) for label, zone, x in (
        ("Bright", ZONES["KEY 150"], 5.02), ("Prev", 155, 11.0), ("Play", ZONES["KEY 152"], 11.99),
        ("Next", ZONES["KEY 154"], 12.99), ("Mute", 153, 13.98))),
    *_own_names((replace(key, y=_PRO_X_TKL_TOP) if key.y == _G515_TOP else key
                 for key in G515_KEYS if not key.reserved), PRO_X_TKL_F_KEYS),
)

#: The G915's media keys, which the G213 Prodigy has in places of its own.
_G915_MEDIA = frozenset(ZONES[name] for name in ("PREVIOUS", "PLAY/PAUSE", "NEXT", "MUTE"))

#: The G213 Prodigy, US ANSI: the G915's keys from the function row down, its G keys and media
#: keys aside, and its own logo, Game Mode, Brightness and Mute above them, and Play, Stop,
#: Previous and Next above the keypad. Stop has no per-key id of the G915's to draw it by.
G213_KEYS: tuple[Key, ...] = _by_row((
    Key("Logo", 0, 0.1, w=_ROUND, h=_ROUND, zone=ZONES["LOGO"]),
    Key("Game", 15.31, 0.09, w=_ROUND, h=_ROUND),
    *(Key(label, x, 0.09, w=_ROUND, h=_ROUND, zone=ZONES[name]) for label, name, x in (
        ("Bright", "BRIGHTNESS", 17.32), ("Mute", "MUTE", 19.63))),
    Key("Stop", 19.66, 1.2, w=_ROUND, h=_ROUND),
    *(Key(label, x, 1.2, w=_ROUND, h=_ROUND, zone=ZONES[name]) for label, name, x in (
        ("Play", "PLAY/PAUSE", 18.63), ("Prev", "PREVIOUS", 20.68), ("Next", "NEXT", 21.71))),
    *(key for key in KEYS if key.y >= 1 and key.x >= 0 and key.zone not in _G915_MEDIA),
))
#: The G213 Prodigy's five lighting areas, by the zones of the keys in each, in its package's
#: order, ZONE_ONE to ZONE_FIVE. Game Mode and Stop are in the fourth and the fifth, with no
#: zone to keep a color by.
G213_AREAS = tuple(frozenset(ZONES[name] for name in names) for names in (
    ("LOGO", "ESC", "F1", "F2", "F3", "`", "1", "2", "3", "4", "TAB", "Q", "W", "E", "R",
     "CAPS LOCK", "A", "S", "D", "F", "LEFT SHIFT", "Z", "X", "C", "LEFT CTRL",
     "LEFT WINDOWS", "LEFT ALT"),
    ("F4", "F5", "F6", "F7", "F8", "5", "6", "7", "8", "9", "T", "Y", "U", "I", "G", "H",
     "J", "K", "V", "B", "N", "M", "SPACE"),
    ("F9", "F10", "F11", "F12", "0", "-", "=", "BACKSPACE", "O", "P", "[", "]", "\\", "L",
     ";", "'", "ENTER", ",", ".", "/", "RIGHT SHIFT", "RIGHT ALTGR", "RIGHT WINDOWS",
     "COMPOSE", "RIGHT CTRL"),
    ("BRIGHTNESS", "PRINT", "SCROLL LOCK", "PAUSE", "INSERT", "HOME", "PAGE UP", "DELETE",
     "END", "PAGE DOWN", "UP", "LEFT", "DOWN", "RIGHT"),
    ("NUMLOCK", *(f"KEYPAD {name}" for name in (*"/*-+", "ENTER", *"1234567890", ".")),
     "PLAY/PAUSE", "PREVIOUS", "NEXT", "MUTE"),
))


def _g915x_keys(top: tuple[Key, ...]) -> tuple[Key, ...]:
    """A G915 X's keys under `top`, its logo and round buttons: the G915's from the function row
    down, G1 to G5 under the G915 X's names, and ANSI's bottom row in the G915's."""
    return _by_row((*top, *_own_names(
        (key for key in KEYS if key.y >= 1 and not (key.y == 6.5 and 0 <= key.x < _NAV)),
        G915X_G_KEYS), *_ANSI_BOTTOM))


#: The G915 X's G6 to G9, round buttons in the places of the G915's M1 to M3 and MR, bindable.
_G915X_G6_G9 = tuple(replace(_G915_TOP[m_key], label=f"G{n}", zone=ZONES[f"G{n}"],
                             bindable=True, name=G915X_G_KEYS[n - 1])
                     for n, m_key in zip(range(6, 10), ("M1", "M2", "M3", "MR"), strict=True))
#: The G915 X, wired, US ANSI: the logo, G6 to G9, then Game Mode and Brightness in the places
#: of the G915's LIGHTSPEED and Bluetooth, as on the G815; under them _g915x_keys.
G915X_KEYS = _g915x_keys((_G915_TOP["Logo"], *_G915X_G6_G9,
                          *(key for key in G815_KEYS if key.label in ("Game", "Bright"))))
#: The G915 X LIGHTSPEED, US ANSI: the G915 X's keys, with LIGHTSPEED, Bluetooth, Game Mode
#: and Brightness at the G915's places, as its front picture in G HUB draws them.
G915X_LIGHTSPEED_KEYS = _g915x_keys((_G915_TOP["Logo"], *_G915X_G6_G9,
                                     *(_G915_TOP[label] for label in ("Host", "BT", "Game",
                                                                      "Bright"))))
#: The G915 X LIGHTSPEED TKL, US ANSI: the G915 TKL's keys with ANSI's bottom row in its
#: own, its F1 to F12 bindable under names of their own.
G915X_TKL_KEYS = _by_row((*_own_names(
    (key for key in G915_TKL_KEYS if not (key.y == 6.5 and key.x < _NAV)), G915XTKL_F_KEYS),
    *_ANSI_BOTTOM))

#: The compact boards' main block: the G515 TKL's keys from the number row to the shift row,
#: its right Shift 1.75 wide, then ANSI's bottom row to Space with 1-unit right Alt, FN and
#: Ctrl, and the arrows a quarter row below it.
_COMPACT_MAIN: tuple[Key, ...] = (
    *(replace(key, w=1.75) if key.zone == ZONES["RIGHT SHIFT"] else key
      for key in G515_KEYS if 2.5 <= key.y < 6 and key.x < _G515_NAV),
    *(key for key in _ANSI_BOTTOM if key.x < 10),
    *(Key(label, x, 6.5, zone=ZONES[name]) for label, x, name in (
        ("Alt", 10, "RIGHT ALTGR"), ("FN", 11, "RIGHT WINDOWS"), ("Ctrl", 12, "RIGHT CTRL"))),
    Key("Up", 14.25, 5.75, zone=ZONES["UP"]),
    *(Key(label, 13.25 + i, 6.75, zone=ZONES[name]) for i, (label, name) in
      enumerate((("Left", "LEFT"), ("Down", "DOWN"), ("Right", "RIGHT")))),
)
#: Where the compact boards' function row's three groups start, and the 98% boards' Delete
#: beside it, and where a 98% board's keypad starts, its Print Screen above it on the G512 X 98.
_COMPACT_F_ROW = (1.25, 5.5, 9.8)
_COMPACT_DEL = 14.05
_PAD_98 = 15.5
#: A 98% board's keypad: the G915's, moved left to _PAD_98, with a 1-unit 0 beside the point.
_KEYPAD_98: tuple[Key, ...] = (
    *(replace(key, x=key.x - _PAD + _PAD_98) for key in KEYS
      if key.x >= _PAD and key.y >= 2.5 and key.zone != ZONES["KEYPAD 0"]),
    Key("0", _PAD_98 + 1, 6.5, zone=ZONES["KEYPAD 0"]),
)


def _compact_top(y: float, names: tuple[str, ...]) -> tuple[Key, ...]:
    """A compact board's Esc and F1 to F12 at `y`, the F keys from _COMPACT_F_ROW and bindable
    under `names`."""
    return (Key("Esc", 0, y, zone=ZONES["ESC"]),
            *_own_names((replace(key, bindable=True) for key in _function_row(_COMPACT_F_ROW, y)),
                        names))


#: The G316 X and G316 X 98, US: Esc, F1 to F12, Delete and the Game Mode button, then the
#: main block and a 98% keypad. The dial and the display have no LED of their own and are not
#: drawn, nor is the light bar.
G316X_KEYS = _by_row((
    *_compact_top(1.15, G316X_F_KEYS),
    Key("Del", _COMPACT_DEL, 1.15, zone=ZONES["DELETE"]),
    Key("Game", 16.55, 1.2, w=_ROUND, h=_ROUND),
    *_COMPACT_MAIN, *_KEYPAD_98))
#: The G512 X 98, US: Esc, F1 to F12, Delete and Print Screen, then the main block and a 98%
#: keypad. Its two dials are not drawn, nor its light bar or its Game Mode light.
G512X98_KEYS = _by_row((
    *_compact_top(1.3, G512X98_F_KEYS),
    Key("Del", _COMPACT_DEL, 1.3, zone=ZONES["DELETE"]),
    Key("PrtSc", _PAD_98, 1.3, zone=ZONES["PRINT"]),
    *_COMPACT_MAIN, *_KEYPAD_98))
#: The G512 X 75, US: Esc and F1 to F12, the main block, and Delete, Print Screen, Page Up and
#: Page Down down its right side. Its two dials, its light bar and its Game Mode light are not
#: drawn.
G512X75_KEYS = _by_row((
    *_compact_top(1.3, G512X75_F_KEYS),
    *_COMPACT_MAIN,
    *(Key(label, 15.45, 2.5 + i, zone=ZONES[name]) for i, (label, name) in enumerate((
        ("Del", "DELETE"), ("PrtSc", "PRINT"), ("PgUp", "PAGE UP"), ("PgDn", "PAGE DOWN"))))))


@dataclass(frozen=True)
class Board:
    """One model's drawn keyboard: its keys, the corner the drawing starts at and its size,
    in key units, the per-key zones its keys light, its bindable keys, G1 or F1 on, by the
    number in their labels, and on a board lit by areas the zones of each area."""

    keys: tuple[Key, ...]
    origin_x: float
    origin_y: float
    width: float
    height: float
    zones: frozenset[int]
    bindable_keys: tuple[Key, ...]
    #: The zones of each of its lighting areas, on a board that shows one color an area: the
    #: G213 Prodigy's five. Empty on a board lit key by key.
    areas: tuple[frozenset[int], ...]

    @property
    def lit(self) -> tuple[int, ...]:
        """The zones of its lit keys, in its keys' order."""
        return tuple(key.zone for key in self.keys if key.zone is not None)

    def painted_with(self, zone: int) -> tuple[int, ...]:
        """The zones a paint of the key at `zone` colors, in the board's order: its whole
        area's on a board lit by areas, else `zone` alone."""
        area = next((area for area in self.areas if zone in area), frozenset({zone}))
        return tuple(lit for lit in self.lit if lit in area)

    def area_colors(self, painted: Mapping[int, int], fill: int) -> tuple[int, ...]:
        """The color each of its areas shows of `painted`, a layer's painted keys by zone, over
        `fill`: the color most of the area's painted keys have, or `fill` where none of them
        is painted. A painted key outweighs the unpainted ones, since a profile chose its
        color, and a tie goes to the color met first in its keys' order, row by row on the
        G213's."""
        shown = []
        for area in self.areas:
            # most_common(1) gives the first met of colors with the same count.
            counted = Counter(painted[zone] for zone in self.lit
                              if zone in area and zone in painted)
            shown.append(counted.most_common(1)[0][0] if counted else fill)
        return tuple(shown)

    def shown_colors(self, painted: Mapping[int, int], fill: int) -> dict[int, int]:
        """The color each of its lit keys shows, by zone: its own of `painted`, else `fill`,
        or on a board lit by areas its area's (area_colors)."""
        if not self.areas:
            return {zone: painted.get(zone, fill) for zone in self.zones}
        return {zone: color
                for area, color in zip(self.areas, self.area_colors(painted, fill), strict=True)
                for zone in area}


def board(keys: tuple[Key, ...], areas: tuple[frozenset[int], ...] = ()) -> Board:
    """The Board `keys` make, lit by `areas` where it has them."""
    origin_x, origin_y = min(k.x for k in keys), min(k.y for k in keys)
    return Board(keys, origin_x, origin_y, max(k.x + k.w for k in keys) - origin_x,
                 max(k.y + k.h for k in keys) - origin_y,
                 frozenset(k.zone for k in keys if k.zone is not None),
                 tuple(sorted((k for k in keys if k.bindable), key=lambda k: k.number)),
                 areas)


G915_BOARD = board(KEYS)
G515_BOARD = board(G515_KEYS)
#: The G515 TKL's keys lit while FN is held: F8 to F12, PrtSc, ScrLk and Pause, which carry
#: its FN functions (its setup guide, pages 4 and 5; a G HUB capture of 2026-10-21), and FN
#: itself, by their zones. Not A, whose FN + A types a, nor F1, whose FN + F1 G HUB 39.1.2
#: makes its custom assignments' shortcut (the owner's decision of 2026-10-21, "No, media
#: keys and FN only"). The G515 LIGHTSPEED TKL is taken to have the same.
G515_FN_KEYS = frozenset(ZONES[name] for name in (
    "F8", "F9", "F10", "F11", "F12", "PRINT", "SCROLL LOCK", "PAUSE", "RIGHT WINDOWS"))
G915_TKL_BOARD = board(G915_TKL_KEYS)
G815_BOARD = board(G815_KEYS)
PRO_X_TKL_BOARD = board(PRO_X_TKL_KEYS)
G213_BOARD = board(G213_KEYS, G213_AREAS)
G915X_BOARD = board(G915X_KEYS)
G915X_LIGHTSPEED_BOARD = board(G915X_LIGHTSPEED_KEYS)
G915X_TKL_BOARD = board(G915X_TKL_KEYS)
G316X_BOARD = board(G316X_KEYS)
G512X75_BOARD = board(G512X75_KEYS)
G512X98_BOARD = board(G512X98_KEYS)
#: The keys lit while FN is held on the G515 generation's other boards: the keys whose caps
#: print a second function and FN, by their zones, never F1, as on the G515s (G515_FN_KEYS).
#: The G915 X family's are F2 to F4, its onboard profiles (the G915 X's setup guide; its
#: G HUB pictures print them on the TKL too). The others' are read off the keycaps of each
#: front picture in G HUB, none tried: the G316 X's F7 and F8, brightness, and F9 to F12,
#: Page Up, Page Down, Insert and Print Screen; the G512 X's ` and 1 to 3, F3 to F12, of
#: which its setup guide names F5 to F8 for the lighting and its pictures print media keys
#: on F9 to F12, and on the G512 X 75 Print Screen, Page Up and Page Down, which print
#: Insert, Home and End.
G915X_FN_KEYS = frozenset(ZONES[name] for name in ("F2", "F3", "F4", "RIGHT WINDOWS"))
G316X_FN_KEYS = frozenset(ZONES[name] for name in (
    "F7", "F8", "F9", "F10", "F11", "F12", "RIGHT WINDOWS"))
G512X98_FN_KEYS = frozenset(ZONES[name] for name in (
    "`", "1", "2", "3", *(f"F{n}" for n in range(3, 13)), "RIGHT WINDOWS"))
G512X75_FN_KEYS = G512X98_FN_KEYS | {ZONES[name] for name in ("PRINT", "PAGE UP", "PAGE DOWN")}
