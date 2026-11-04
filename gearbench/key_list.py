"""The key capture's list: its groups, each key's title, and the search over them."""

from __future__ import annotations

#: What the key capture's list offers, group by group, in a spec's spelling: a curated list
#: (the owner's decision of 2026-09-25), since the full one buries the keys a binding wants
#: among names like alterase. Every other name `gearbench-ctl keys` lists still binds with
#: `gearbench-ctl bind`.
#:
#: F13 to F24 first (the decision of 2026-09-24): the G915 has no key for them. Under the
#: default keymap (xkb's inet(evdev)) GNOME takes some: F13 is XF86Tools, which opens
#: Settings (checked 2026-09-26), F20 is mic mute and F21 to F23 the touchpad keys, as
#: KEY_LABELS says. Then the media keys, and the modifiers on their own, both sides.
FIRST_KEYS = tuple(f"f{n}" for n in range(13, 25))
MEDIA_KEYS = ("playpause", "stopcd", "previoussong", "nextsong", "mute",
              "volumedown", "volumeup", "micmute")
MODIFIER_KEYS = ("leftctrl", "rightctrl", "leftshift", "rightshift",
                 "leftalt", "rightalt", "leftmeta", "rightmeta")
#: Then the rest of the G915's full-size US board (layout.KEYS), block by block, row by row:
#: the main block less the modifiers, Print Screen to the arrows, then the keypad.
MAIN_KEYS = (
    "esc", *(f"f{n}" for n in range(1, 13)),
    "grave", *"1234567890", "minus", "equal", "backspace",
    "tab", *"qwertyuiop", "leftbrace", "rightbrace", "backslash",
    "capslock", *"asdfghjkl", "semicolon", "apostrophe", "enter",
    *"zxcvbnm", "comma", "dot", "slash",
    "space", "compose",
)
NAVIGATION_KEYS = ("sysrq", "scrolllock", "pause", "insert", "home", "pageup",
                   "delete", "end", "pagedown", "up", "left", "down", "right")
KEYPAD_KEYS = ("numlock", "kpslash", "kpasterisk", "kpminus", "kp7", "kp8", "kp9",
               "kpplus", "kp4", "kp5", "kp6", "kp1", "kp2", "kp3", "kpenter",
               "kp0", "kpdot")
#: The list's groups by their titles, in the order it shows them.
KEY_GROUPS = {"F13 to F24": FIRST_KEYS, "Media": MEDIA_KEYS, "Modifiers": MODIFIER_KEYS,
              "Main keys": MAIN_KEYS, "Navigation": NAVIGATION_KEYS, "Keypad": KEYPAD_KEYS}

#: What each key in the list is, the title of its row: searched with the
#: name, so "escape" finds esc, "super" leftmeta, "print screen" sysrq and
#: "backtick" grave.
KEY_LABELS = {
    **{f"f{n}": f"F{n}" for n in range(1, 25)},
    # What GNOME does with these under the default keymap (FIRST_KEYS).
    "f13": "F13 (opens GNOME Settings)", "f20": "F20 (GNOME mic mute)",
    "f21": "F21 (GNOME touchpad toggle)", "f22": "F22 (GNOME touchpad on)",
    "f23": "F23 (GNOME touchpad off)",
    "playpause": "Play/Pause", "stopcd": "Stop", "previoussong": "Previous track",
    "nextsong": "Next track", "mute": "Mute", "volumedown": "Volume down",
    "volumeup": "Volume up", "micmute": "Mic mute",
    "leftctrl": "Left Ctrl", "rightctrl": "Right Ctrl", "leftshift": "Left Shift",
    "rightshift": "Right Shift", "leftalt": "Left Alt", "rightalt": "Right Alt, AltGr",
    "leftmeta": "Left Super", "rightmeta": "Right Super",
    "esc": "Escape", "grave": "Backtick (`)",
    **{digit: digit for digit in "1234567890"},
    "minus": "Minus (-)", "equal": "Equals (=)", "backspace": "Backspace",
    "tab": "Tab", **{letter: letter.upper() for letter in "abcdefghijklmnopqrstuvwxyz"},
    "leftbrace": "Left bracket ([)", "rightbrace": "Right bracket (])",
    "backslash": "Backslash (\\)", "capslock": "Caps Lock",
    "semicolon": "Semicolon (;)", "apostrophe": "Quote (')", "enter": "Enter",
    "comma": "Comma (,)", "dot": "Period (.)", "slash": "Slash (/)",
    "space": "Space", "compose": "Menu",
    "sysrq": "Print Screen", "scrolllock": "Scroll Lock", "pause": "Pause",
    "insert": "Insert", "home": "Home", "pageup": "Page Up", "delete": "Delete",
    "end": "End", "pagedown": "Page Down", "up": "Up arrow", "left": "Left arrow",
    "down": "Down arrow", "right": "Right arrow",
    "numlock": "Num Lock", "kpslash": "Keypad /", "kpasterisk": "Keypad *",
    "kpminus": "Keypad -", "kpplus": "Keypad +", "kpenter": "Keypad Enter",
    "kpdot": "Keypad .", **{f"kp{digit}": f"Keypad {digit}" for digit in "0123456789"},
}


def _squash(text: str) -> str:
    """Lower case, with the spaces and underscores taken out."""
    return "".join(text.lower().replace("_", " ").split())


def key_matches(name: str, query: str, label: str | None = None) -> bool:
    """Whether the list's search for `query` shows the key `name`, or with `label` the
    entry of the list named `name` that is no key, such as an action, titled `label`.

    Part of the name or of its label, in any case, with spaces and
    underscores ignored and a leading KEY_ dropped, so "KEY_F13", "play
    pause" and "caps_lock" all find their key. A hyphen in the name or label
    may be left out, so "g shift" finds g-shift; one typed is searched for,
    so "-" finds Minus. An empty search shows everything. The query comes
    stripped: a KEY_ after leading white space is searched as text, not dropped.
    """
    wanted = _squash(query.lower().removeprefix("key_"))
    texts = (_squash(text) for text in (name, KEY_LABELS[name] if label is None else label))
    return any(wanted in text or wanted in text.replace("-", "") for text in texts)
