"""Per-key lighting zone ids for 0x8081 PER_KEY_LIGHTING.

The keyboard block is a 1-based index into the sorted HID usages the board reports; the
ids are not usages, and sending usages paints the wrong keys and drops every modifier.
Found from a G HUB capture and checked on the board id by id. The device's zone bitmap
(0x8081 fn 0) does not enumerate the keys: page 0 claims every id up to 111.

Seven ids have no key on a US ANSI board and light nothing, so they stay unnamed: 47
(Non-US #), 97 (Non-US backslash), 99-103 (International 1-5). The media ids, 155 to
158, do not follow the board's key order. M1-M3 and MR are not per-key zones: they are
driven through 0x8020 fn 1 and 0x8030 fn 0.
"""

from __future__ import annotations

#: HID usages the keyboard block addresses, in wire order. zone = index + 1.
SORTED_USAGES: list[int] = list(range(4, 102)) + [135, 136, 137, 138, 139] + list(range(224, 232))

#: Key name by HID usage, for the usages that have a key on a US ANSI board.
USAGE_NAMES: dict[int, str] = {
    **{0x04 + i: chr(ord("A") + i) for i in range(26)},
    **{0x1E + i: d for i, d in enumerate("1234567890")},
    0x28: "ENTER",
    0x29: "ESC",
    0x2A: "BACKSPACE",
    0x2B: "TAB",
    0x2C: "SPACE",
    0x2D: "-",
    0x2E: "=",
    0x2F: "[",
    0x30: "]",
    0x31: "\\",
    0x33: ";",
    0x34: "'",
    0x35: "`",
    0x36: ",",
    0x37: ".",
    0x38: "/",
    0x39: "CAPS LOCK",
    **{0x3A + i: f"F{i + 1}" for i in range(12)},
    0x46: "PRINT",
    0x47: "SCROLL LOCK",
    0x48: "PAUSE",
    0x49: "INSERT",
    0x4A: "HOME",
    0x4B: "PAGE UP",
    0x4C: "DELETE",
    0x4D: "END",
    0x4E: "PAGE DOWN",
    0x4F: "RIGHT",
    0x50: "LEFT",
    0x51: "DOWN",
    0x52: "UP",
    0x53: "NUMLOCK",
    0x54: "KEYPAD /",
    0x55: "KEYPAD *",
    0x56: "KEYPAD -",
    0x57: "KEYPAD +",
    0x58: "KEYPAD ENTER",
    **{0x59 + i: f"KEYPAD {i + 1}" for i in range(9)},
    0x62: "KEYPAD 0",
    0x63: "KEYPAD .",
    0x65: "COMPOSE",
    0xE0: "LEFT CTRL",
    0xE1: "LEFT SHIFT",
    0xE2: "LEFT ALT",
    0xE3: "LEFT WINDOWS",
    0xE4: "RIGHT CTRL",
    0xE5: "RIGHT SHIFT",
    0xE6: "RIGHT ALTGR",
    0xE7: "RIGHT WINDOWS",
}

ZONES: dict[str, int] = {
    USAGE_NAMES[usage]: index + 1
    for index, usage in enumerate(SORTED_USAGES)
    if usage in USAGE_NAMES
}
ZONES.update({
    "BRIGHTNESS": 153,
    "PLAY/PAUSE": 155,
    "MUTE": 156,
    "NEXT": 157,
    "PREVIOUS": 158,
    "G1": 180,
    "G2": 181,
    "G3": 182,
    "G4": 183,
    "G5": 184,
    "LOGO": 210,
})

LOGO = ZONES["LOGO"]

#: Zone id to key name, the reverse of ZONES, which has one name per id.
ZONE_NAMES: dict[int, str] = {zone: name for name, zone in ZONES.items()}
