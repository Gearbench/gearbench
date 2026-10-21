"""A virtual keyboard and a virtual mouse on /dev/uinput, standard library only.

udev/70-gearbench-uinput.rules gives the seat user an ACL on /dev/uinput (systemd's rules
give none), so nothing here needs root. Two devices, since libinput classifies a device by
its capabilities: a keyboard that sends BTN_SIDE is not routed as a pointer button, so
pointer buttons go through `VirtualMouse`. Key names are the kernel's (KEY_F13): a copy of
its table ships here, and /usr/include/linux/input-event-codes.h, where installed, adds a
newer kernel's names. A code never changes once defined, so the copy can only fall behind.
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import os
import platform
import re
import struct
import time
from collections.abc import Sequence
from pathlib import Path

from .hidpp import BUS_USB
from .wording import reason

UINPUT = "/dev/uinput"
#: The kernel's BUS_VIRTUAL.
BUS_VIRTUAL = 0x06

EV_SYN, EV_KEY, EV_REL = 0x00, 0x01, 0x02
SYN_REPORT = 0x00
REL_X, REL_Y, REL_HWHEEL, REL_WHEEL = 0x00, 0x01, 0x06, 0x08
#: The high-resolution horizontal wheel: 120 to a notch, beside REL_HWHEEL's 1.
REL_HWHEEL_HI_RES = 0x0C
#: What one horizontal notch is in REL_HWHEEL_HI_RES units, as the kernel
#: sends it for the G502 X PLUS's tilt (measured 2026-09-25).
HI_RES_NOTCH = 120

#: The eight ordinary mouse buttons, BTN_LEFT through BTN_TASK.
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE, BTN_SIDE, BTN_EXTRA = 0x110, 0x111, 0x112, 0x113, 0x114
BTN_TASK = 0x117
POINTER_BUTTONS = range(BTN_LEFT, BTN_TASK + 1)
#: The buttons the virtual mouse is ever asked to send, BTN_LEFT through BTN_EXTRA: it
#: registers BTN_FORWARD, BTN_BACK and BTN_TASK as well, which nothing sends.
SENT_BUTTONS = range(BTN_LEFT, BTN_EXTRA + 1)
#: The virtual mouse button each pointer action of a mouse button holds.
POINTER_ACTIONS = {"back": BTN_SIDE, "forward": BTN_EXTRA}

# _IOC(dir, type, nr, size), from each arch's uapi asm/ioctl.h: asm-generic puts dir at
# bit 30 with none 0, write 1 and read 2; powerpc, mips, sparc and alpha put it at bit 29
# with none 1, read 2 and write 4; parisc swaps asm-generic's read and write.
_MACHINE = platform.machine()
if _MACHINE.startswith(("ppc", "mips", "sparc", "alpha")):
    IOC_NONE, IOC_WRITE, IOC_READ, _IOC_DIRSHIFT = 1, 4, 2, 29
elif _MACHINE.startswith("parisc"):
    IOC_NONE, IOC_WRITE, IOC_READ, _IOC_DIRSHIFT = 0, 2, 1, 30
else:
    IOC_NONE, IOC_WRITE, IOC_READ, _IOC_DIRSHIFT = 0, 1, 2, 30


def ioc(direction: int, kind: int, nr: int, size: int) -> int:
    return (direction << _IOC_DIRSHIFT) | (size << 16) | (kind << 8) | nr


# struct input_event: two time fields + u16 type + u16 code + s32 value. The time fields
# are unsigned, as the kernel reads them (__kernel_ulong_t in linux/input.h): a signed
# 32-bit field cannot hold a time after 2038-01-19.
EVENT = struct.Struct("@LLHHi")
# struct uinput_setup: input_id (4 x u16) + char name[80] + u32 ff_effects_max
_SETUP = struct.Struct("@HHHH80sI")

UI_DEV_CREATE = ioc(IOC_NONE, 0x55, 1, 0)      # _IO('U', 1); 'U' is 0x55
UI_DEV_DESTROY = ioc(IOC_NONE, 0x55, 2, 0)
UI_DEV_SETUP = ioc(IOC_WRITE, 0x55, 3, _SETUP.size)
UI_SET_EVBIT = ioc(IOC_WRITE, 0x55, 100, 4)
UI_SET_KEYBIT = ioc(IOC_WRITE, 0x55, 101, 4)
UI_SET_RELBIT = ioc(IOC_WRITE, 0x55, 102, 4)

_HEADER = Path("/usr/include/linux/input-event-codes.h")
#: One `#define KEY_X 123` line, with or without a comment after the value (KEY_CALC's),
#: even one that runs on to the next line (KEY_SWITCHVIDEOMODE's). The gaps are white space
#: other than a line's end and the digits are ASCII, as C reads a define: `\s` spans lines,
#: and `\d` and int() take any script's digits.
_DEFINE = re.compile(
    r"^#define[^\S\n]+((?:KEY|BTN)_[A-Z0-9_]+)[^\S\n]+(0x[0-9a-fA-F]+|[0-9]+)[^\S\n]*"
    r"(?:/\*.*)?$",
    re.MULTILINE)

#: Every numbered KEY_ and BTN_ define in Linux 7.0's input-event-codes.h, less the bound and
#: the alias `_load_keycodes` drops. The header comes with linux-libc-dev, which a desktop
#: may lack or autoremove, and a binding saved while it was there must still load.
_SHIPPED = {
    "KEY_RESERVED": 0, "KEY_ESC": 1, "KEY_1": 2, "KEY_2": 3, "KEY_3": 4, "KEY_4": 5, "KEY_5": 6,
    "KEY_6": 7, "KEY_7": 8, "KEY_8": 9, "KEY_9": 10, "KEY_0": 11, "KEY_MINUS": 12, "KEY_EQUAL": 13,
    "KEY_BACKSPACE": 14, "KEY_TAB": 15, "KEY_Q": 16, "KEY_W": 17, "KEY_E": 18, "KEY_R": 19,
    "KEY_T": 20, "KEY_Y": 21, "KEY_U": 22, "KEY_I": 23, "KEY_O": 24, "KEY_P": 25,
    "KEY_LEFTBRACE": 26, "KEY_RIGHTBRACE": 27, "KEY_ENTER": 28, "KEY_LEFTCTRL": 29, "KEY_A": 30,
    "KEY_S": 31, "KEY_D": 32, "KEY_F": 33, "KEY_G": 34, "KEY_H": 35, "KEY_J": 36, "KEY_K": 37,
    "KEY_L": 38, "KEY_SEMICOLON": 39, "KEY_APOSTROPHE": 40, "KEY_GRAVE": 41, "KEY_LEFTSHIFT": 42,
    "KEY_BACKSLASH": 43, "KEY_Z": 44, "KEY_X": 45, "KEY_C": 46, "KEY_V": 47, "KEY_B": 48,
    "KEY_N": 49, "KEY_M": 50, "KEY_COMMA": 51, "KEY_DOT": 52, "KEY_SLASH": 53, "KEY_RIGHTSHIFT": 54,
    "KEY_KPASTERISK": 55, "KEY_LEFTALT": 56, "KEY_SPACE": 57, "KEY_CAPSLOCK": 58, "KEY_F1": 59,
    "KEY_F2": 60, "KEY_F3": 61, "KEY_F4": 62, "KEY_F5": 63, "KEY_F6": 64, "KEY_F7": 65,
    "KEY_F8": 66, "KEY_F9": 67, "KEY_F10": 68, "KEY_NUMLOCK": 69, "KEY_SCROLLLOCK": 70,
    "KEY_KP7": 71, "KEY_KP8": 72, "KEY_KP9": 73, "KEY_KPMINUS": 74, "KEY_KP4": 75, "KEY_KP5": 76,
    "KEY_KP6": 77, "KEY_KPPLUS": 78, "KEY_KP1": 79, "KEY_KP2": 80, "KEY_KP3": 81, "KEY_KP0": 82,
    "KEY_KPDOT": 83, "KEY_ZENKAKUHANKAKU": 85, "KEY_102ND": 86, "KEY_F11": 87, "KEY_F12": 88,
    "KEY_RO": 89, "KEY_KATAKANA": 90, "KEY_HIRAGANA": 91, "KEY_HENKAN": 92,
    "KEY_KATAKANAHIRAGANA": 93, "KEY_MUHENKAN": 94, "KEY_KPJPCOMMA": 95, "KEY_KPENTER": 96,
    "KEY_RIGHTCTRL": 97, "KEY_KPSLASH": 98, "KEY_SYSRQ": 99, "KEY_RIGHTALT": 100,
    "KEY_LINEFEED": 101, "KEY_HOME": 102, "KEY_UP": 103, "KEY_PAGEUP": 104, "KEY_LEFT": 105,
    "KEY_RIGHT": 106, "KEY_END": 107, "KEY_DOWN": 108, "KEY_PAGEDOWN": 109, "KEY_INSERT": 110,
    "KEY_DELETE": 111, "KEY_MACRO": 112, "KEY_MUTE": 113, "KEY_VOLUMEDOWN": 114,
    "KEY_VOLUMEUP": 115, "KEY_POWER": 116, "KEY_KPEQUAL": 117, "KEY_KPPLUSMINUS": 118,
    "KEY_PAUSE": 119, "KEY_SCALE": 120, "KEY_KPCOMMA": 121, "KEY_HANGEUL": 122, "KEY_HANJA": 123,
    "KEY_YEN": 124, "KEY_LEFTMETA": 125, "KEY_RIGHTMETA": 126, "KEY_COMPOSE": 127, "KEY_STOP": 128,
    "KEY_AGAIN": 129, "KEY_PROPS": 130, "KEY_UNDO": 131, "KEY_FRONT": 132, "KEY_COPY": 133,
    "KEY_OPEN": 134, "KEY_PASTE": 135, "KEY_FIND": 136, "KEY_CUT": 137, "KEY_HELP": 138,
    "KEY_MENU": 139, "KEY_CALC": 140, "KEY_SETUP": 141, "KEY_SLEEP": 142, "KEY_WAKEUP": 143,
    "KEY_FILE": 144, "KEY_SENDFILE": 145, "KEY_DELETEFILE": 146, "KEY_XFER": 147, "KEY_PROG1": 148,
    "KEY_PROG2": 149, "KEY_WWW": 150, "KEY_MSDOS": 151, "KEY_COFFEE": 152,
    "KEY_ROTATE_DISPLAY": 153, "KEY_CYCLEWINDOWS": 154, "KEY_MAIL": 155, "KEY_BOOKMARKS": 156,
    "KEY_COMPUTER": 157, "KEY_BACK": 158, "KEY_FORWARD": 159, "KEY_CLOSECD": 160,
    "KEY_EJECTCD": 161, "KEY_EJECTCLOSECD": 162, "KEY_NEXTSONG": 163, "KEY_PLAYPAUSE": 164,
    "KEY_PREVIOUSSONG": 165, "KEY_STOPCD": 166, "KEY_RECORD": 167, "KEY_REWIND": 168,
    "KEY_PHONE": 169, "KEY_ISO": 170, "KEY_CONFIG": 171, "KEY_HOMEPAGE": 172, "KEY_REFRESH": 173,
    "KEY_EXIT": 174, "KEY_MOVE": 175, "KEY_EDIT": 176, "KEY_SCROLLUP": 177, "KEY_SCROLLDOWN": 178,
    "KEY_KPLEFTPAREN": 179, "KEY_KPRIGHTPAREN": 180, "KEY_NEW": 181, "KEY_REDO": 182,
    "KEY_F13": 183, "KEY_F14": 184, "KEY_F15": 185, "KEY_F16": 186, "KEY_F17": 187, "KEY_F18": 188,
    "KEY_F19": 189, "KEY_F20": 190, "KEY_F21": 191, "KEY_F22": 192, "KEY_F23": 193, "KEY_F24": 194,
    "KEY_PLAYCD": 200, "KEY_PAUSECD": 201, "KEY_PROG3": 202, "KEY_PROG4": 203,
    "KEY_ALL_APPLICATIONS": 204, "KEY_SUSPEND": 205, "KEY_CLOSE": 206, "KEY_PLAY": 207,
    "KEY_FASTFORWARD": 208, "KEY_BASSBOOST": 209, "KEY_PRINT": 210, "KEY_HP": 211,
    "KEY_CAMERA": 212, "KEY_SOUND": 213, "KEY_QUESTION": 214, "KEY_EMAIL": 215, "KEY_CHAT": 216,
    "KEY_SEARCH": 217, "KEY_CONNECT": 218, "KEY_FINANCE": 219, "KEY_SPORT": 220, "KEY_SHOP": 221,
    "KEY_ALTERASE": 222, "KEY_CANCEL": 223, "KEY_BRIGHTNESSDOWN": 224, "KEY_BRIGHTNESSUP": 225,
    "KEY_MEDIA": 226, "KEY_SWITCHVIDEOMODE": 227, "KEY_KBDILLUMTOGGLE": 228,
    "KEY_KBDILLUMDOWN": 229, "KEY_KBDILLUMUP": 230, "KEY_SEND": 231, "KEY_REPLY": 232,
    "KEY_FORWARDMAIL": 233, "KEY_SAVE": 234, "KEY_DOCUMENTS": 235, "KEY_BATTERY": 236,
    "KEY_BLUETOOTH": 237, "KEY_WLAN": 238, "KEY_UWB": 239, "KEY_UNKNOWN": 240,
    "KEY_VIDEO_NEXT": 241, "KEY_VIDEO_PREV": 242, "KEY_BRIGHTNESS_CYCLE": 243,
    "KEY_BRIGHTNESS_AUTO": 244, "KEY_DISPLAY_OFF": 245, "KEY_WWAN": 246, "KEY_RFKILL": 247,
    "KEY_MICMUTE": 248, "BTN_0": 256, "BTN_1": 257, "BTN_2": 258, "BTN_3": 259, "BTN_4": 260,
    "BTN_5": 261, "BTN_6": 262, "BTN_7": 263, "BTN_8": 264, "BTN_9": 265, "BTN_LEFT": 272,
    "BTN_MOUSE": 272, "BTN_RIGHT": 273, "BTN_MIDDLE": 274, "BTN_SIDE": 275, "BTN_EXTRA": 276,
    "BTN_FORWARD": 277, "BTN_BACK": 278, "BTN_TASK": 279, "BTN_JOYSTICK": 288, "BTN_TRIGGER": 288,
    "BTN_THUMB": 289, "BTN_THUMB2": 290, "BTN_TOP": 291, "BTN_TOP2": 292, "BTN_PINKIE": 293,
    "BTN_BASE": 294, "BTN_BASE2": 295, "BTN_BASE3": 296, "BTN_BASE4": 297, "BTN_BASE5": 298,
    "BTN_BASE6": 299, "BTN_DEAD": 303, "BTN_GAMEPAD": 304, "BTN_SOUTH": 304, "BTN_EAST": 305,
    "BTN_C": 306, "BTN_NORTH": 307, "BTN_WEST": 308, "BTN_Z": 309, "BTN_TL": 310, "BTN_TR": 311,
    "BTN_TL2": 312, "BTN_TR2": 313, "BTN_SELECT": 314, "BTN_START": 315, "BTN_MODE": 316,
    "BTN_THUMBL": 317, "BTN_THUMBR": 318, "BTN_DIGI": 320, "BTN_TOOL_PEN": 320,
    "BTN_TOOL_RUBBER": 321, "BTN_TOOL_BRUSH": 322, "BTN_TOOL_PENCIL": 323, "BTN_TOOL_AIRBRUSH": 324,
    "BTN_TOOL_FINGER": 325, "BTN_TOOL_MOUSE": 326, "BTN_TOOL_LENS": 327, "BTN_TOOL_QUINTTAP": 328,
    "BTN_STYLUS3": 329, "BTN_TOUCH": 330, "BTN_STYLUS": 331, "BTN_STYLUS2": 332,
    "BTN_TOOL_DOUBLETAP": 333, "BTN_TOOL_TRIPLETAP": 334, "BTN_TOOL_QUADTAP": 335,
    "BTN_GEAR_DOWN": 336, "BTN_WHEEL": 336, "BTN_GEAR_UP": 337, "KEY_OK": 352, "KEY_SELECT": 353,
    "KEY_GOTO": 354, "KEY_CLEAR": 355, "KEY_POWER2": 356, "KEY_OPTION": 357, "KEY_INFO": 358,
    "KEY_TIME": 359, "KEY_VENDOR": 360, "KEY_ARCHIVE": 361, "KEY_PROGRAM": 362, "KEY_CHANNEL": 363,
    "KEY_FAVORITES": 364, "KEY_EPG": 365, "KEY_PVR": 366, "KEY_MHP": 367, "KEY_LANGUAGE": 368,
    "KEY_TITLE": 369, "KEY_SUBTITLE": 370, "KEY_ANGLE": 371, "KEY_FULL_SCREEN": 372,
    "KEY_MODE": 373, "KEY_KEYBOARD": 374, "KEY_ASPECT_RATIO": 375, "KEY_PC": 376, "KEY_TV": 377,
    "KEY_TV2": 378, "KEY_VCR": 379, "KEY_VCR2": 380, "KEY_SAT": 381, "KEY_SAT2": 382, "KEY_CD": 383,
    "KEY_TAPE": 384, "KEY_RADIO": 385, "KEY_TUNER": 386, "KEY_PLAYER": 387, "KEY_TEXT": 388,
    "KEY_DVD": 389, "KEY_AUX": 390, "KEY_MP3": 391, "KEY_AUDIO": 392, "KEY_VIDEO": 393,
    "KEY_DIRECTORY": 394, "KEY_LIST": 395, "KEY_MEMO": 396, "KEY_CALENDAR": 397, "KEY_RED": 398,
    "KEY_GREEN": 399, "KEY_YELLOW": 400, "KEY_BLUE": 401, "KEY_CHANNELUP": 402,
    "KEY_CHANNELDOWN": 403, "KEY_FIRST": 404, "KEY_LAST": 405, "KEY_AB": 406, "KEY_NEXT": 407,
    "KEY_RESTART": 408, "KEY_SLOW": 409, "KEY_SHUFFLE": 410, "KEY_BREAK": 411, "KEY_PREVIOUS": 412,
    "KEY_DIGITS": 413, "KEY_TEEN": 414, "KEY_TWEN": 415, "KEY_VIDEOPHONE": 416, "KEY_GAMES": 417,
    "KEY_ZOOMIN": 418, "KEY_ZOOMOUT": 419, "KEY_ZOOMRESET": 420, "KEY_WORDPROCESSOR": 421,
    "KEY_EDITOR": 422, "KEY_SPREADSHEET": 423, "KEY_GRAPHICSEDITOR": 424, "KEY_PRESENTATION": 425,
    "KEY_DATABASE": 426, "KEY_NEWS": 427, "KEY_VOICEMAIL": 428, "KEY_ADDRESSBOOK": 429,
    "KEY_MESSENGER": 430, "KEY_DISPLAYTOGGLE": 431, "KEY_SPELLCHECK": 432, "KEY_LOGOFF": 433,
    "KEY_DOLLAR": 434, "KEY_EURO": 435, "KEY_FRAMEBACK": 436, "KEY_FRAMEFORWARD": 437,
    "KEY_CONTEXT_MENU": 438, "KEY_MEDIA_REPEAT": 439, "KEY_10CHANNELSUP": 440,
    "KEY_10CHANNELSDOWN": 441, "KEY_IMAGES": 442, "KEY_NOTIFICATION_CENTER": 444,
    "KEY_PICKUP_PHONE": 445, "KEY_HANGUP_PHONE": 446, "KEY_LINK_PHONE": 447, "KEY_DEL_EOL": 448,
    "KEY_DEL_EOS": 449, "KEY_INS_LINE": 450, "KEY_DEL_LINE": 451, "KEY_FN": 464, "KEY_FN_ESC": 465,
    "KEY_FN_F1": 466, "KEY_FN_F2": 467, "KEY_FN_F3": 468, "KEY_FN_F4": 469, "KEY_FN_F5": 470,
    "KEY_FN_F6": 471, "KEY_FN_F7": 472, "KEY_FN_F8": 473, "KEY_FN_F9": 474, "KEY_FN_F10": 475,
    "KEY_FN_F11": 476, "KEY_FN_F12": 477, "KEY_FN_1": 478, "KEY_FN_2": 479, "KEY_FN_D": 480,
    "KEY_FN_E": 481, "KEY_FN_F": 482, "KEY_FN_S": 483, "KEY_FN_B": 484, "KEY_FN_RIGHT_SHIFT": 485,
    "KEY_BRL_DOT1": 497, "KEY_BRL_DOT2": 498, "KEY_BRL_DOT3": 499, "KEY_BRL_DOT4": 500,
    "KEY_BRL_DOT5": 501, "KEY_BRL_DOT6": 502, "KEY_BRL_DOT7": 503, "KEY_BRL_DOT8": 504,
    "KEY_BRL_DOT9": 505, "KEY_BRL_DOT10": 506, "KEY_NUMERIC_0": 512, "KEY_NUMERIC_1": 513,
    "KEY_NUMERIC_2": 514, "KEY_NUMERIC_3": 515, "KEY_NUMERIC_4": 516, "KEY_NUMERIC_5": 517,
    "KEY_NUMERIC_6": 518, "KEY_NUMERIC_7": 519, "KEY_NUMERIC_8": 520, "KEY_NUMERIC_9": 521,
    "KEY_NUMERIC_STAR": 522, "KEY_NUMERIC_POUND": 523, "KEY_NUMERIC_A": 524, "KEY_NUMERIC_B": 525,
    "KEY_NUMERIC_C": 526, "KEY_NUMERIC_D": 527, "KEY_CAMERA_FOCUS": 528, "KEY_WPS_BUTTON": 529,
    "KEY_TOUCHPAD_TOGGLE": 530, "KEY_TOUCHPAD_ON": 531, "KEY_TOUCHPAD_OFF": 532,
    "KEY_CAMERA_ZOOMIN": 533, "KEY_CAMERA_ZOOMOUT": 534, "KEY_CAMERA_UP": 535,
    "KEY_CAMERA_DOWN": 536, "KEY_CAMERA_LEFT": 537, "KEY_CAMERA_RIGHT": 538,
    "KEY_ATTENDANT_ON": 539, "KEY_ATTENDANT_OFF": 540, "KEY_ATTENDANT_TOGGLE": 541,
    "KEY_LIGHTS_TOGGLE": 542, "BTN_DPAD_UP": 544, "BTN_DPAD_DOWN": 545, "BTN_DPAD_LEFT": 546,
    "BTN_DPAD_RIGHT": 547, "BTN_GRIPL": 548, "BTN_GRIPR": 549, "BTN_GRIPL2": 550, "BTN_GRIPR2": 551,
    "KEY_ALS_TOGGLE": 560, "KEY_ROTATE_LOCK_TOGGLE": 561, "KEY_REFRESH_RATE_TOGGLE": 562,
    "KEY_BUTTONCONFIG": 576, "KEY_TASKMANAGER": 577, "KEY_JOURNAL": 578, "KEY_CONTROLPANEL": 579,
    "KEY_APPSELECT": 580, "KEY_SCREENSAVER": 581, "KEY_VOICECOMMAND": 582, "KEY_ASSISTANT": 583,
    "KEY_KBD_LAYOUT_NEXT": 584, "KEY_EMOJI_PICKER": 585, "KEY_DICTATE": 586,
    "KEY_CAMERA_ACCESS_ENABLE": 587, "KEY_CAMERA_ACCESS_DISABLE": 588,
    "KEY_CAMERA_ACCESS_TOGGLE": 589, "KEY_ACCESSIBILITY": 590, "KEY_DO_NOT_DISTURB": 591,
    "KEY_BRIGHTNESS_MIN": 592, "KEY_BRIGHTNESS_MAX": 593, "KEY_EPRIVACY_SCREEN_ON": 594,
    "KEY_EPRIVACY_SCREEN_OFF": 595, "KEY_ACTION_ON_SELECTION": 596, "KEY_CONTEXTUAL_INSERT": 597,
    "KEY_CONTEXTUAL_QUERY": 598, "KEY_KBDINPUTASSIST_PREV": 608, "KEY_KBDINPUTASSIST_NEXT": 609,
    "KEY_KBDINPUTASSIST_PREVGROUP": 610, "KEY_KBDINPUTASSIST_NEXTGROUP": 611,
    "KEY_KBDINPUTASSIST_ACCEPT": 612, "KEY_KBDINPUTASSIST_CANCEL": 613, "KEY_RIGHT_UP": 614,
    "KEY_RIGHT_DOWN": 615, "KEY_LEFT_UP": 616, "KEY_LEFT_DOWN": 617, "KEY_ROOT_MENU": 618,
    "KEY_MEDIA_TOP_MENU": 619, "KEY_NUMERIC_11": 620, "KEY_NUMERIC_12": 621, "KEY_AUDIO_DESC": 622,
    "KEY_3D_MODE": 623, "KEY_NEXT_FAVORITE": 624, "KEY_STOP_RECORD": 625, "KEY_PAUSE_RECORD": 626,
    "KEY_VOD": 627, "KEY_UNMUTE": 628, "KEY_FASTREVERSE": 629, "KEY_SLOWREVERSE": 630,
    "KEY_DATA": 631, "KEY_ONSCREEN_KEYBOARD": 632, "KEY_PRIVACY_SCREEN_TOGGLE": 633,
    "KEY_SELECTIVE_SCREENSHOT": 634, "KEY_NEXT_ELEMENT": 635, "KEY_PREVIOUS_ELEMENT": 636,
    "KEY_AUTOPILOT_ENGAGE_TOGGLE": 637, "KEY_MARK_WAYPOINT": 638, "KEY_SOS": 639,
    "KEY_NAV_CHART": 640, "KEY_FISHING_CHART": 641, "KEY_SINGLE_RANGE_RADAR": 642,
    "KEY_DUAL_RANGE_RADAR": 643, "KEY_RADAR_OVERLAY": 644, "KEY_TRADITIONAL_SONAR": 645,
    "KEY_CLEARVU_SONAR": 646, "KEY_SIDEVU_SONAR": 647, "KEY_NAV_INFO": 648,
    "KEY_BRIGHTNESS_MENU": 649, "KEY_MACRO1": 656, "KEY_MACRO2": 657, "KEY_MACRO3": 658,
    "KEY_MACRO4": 659, "KEY_MACRO5": 660, "KEY_MACRO6": 661, "KEY_MACRO7": 662, "KEY_MACRO8": 663,
    "KEY_MACRO9": 664, "KEY_MACRO10": 665, "KEY_MACRO11": 666, "KEY_MACRO12": 667,
    "KEY_MACRO13": 668, "KEY_MACRO14": 669, "KEY_MACRO15": 670, "KEY_MACRO16": 671,
    "KEY_MACRO17": 672, "KEY_MACRO18": 673, "KEY_MACRO19": 674, "KEY_MACRO20": 675,
    "KEY_MACRO21": 676, "KEY_MACRO22": 677, "KEY_MACRO23": 678, "KEY_MACRO24": 679,
    "KEY_MACRO25": 680, "KEY_MACRO26": 681, "KEY_MACRO27": 682, "KEY_MACRO28": 683,
    "KEY_MACRO29": 684, "KEY_MACRO30": 685, "KEY_MACRO_RECORD_START": 688,
    "KEY_MACRO_RECORD_STOP": 689, "KEY_MACRO_PRESET_CYCLE": 690, "KEY_MACRO_PRESET1": 691,
    "KEY_MACRO_PRESET2": 692, "KEY_MACRO_PRESET3": 693, "KEY_KBD_LCD_MENU1": 696,
    "KEY_KBD_LCD_MENU2": 697, "KEY_KBD_LCD_MENU3": 698, "KEY_KBD_LCD_MENU4": 699,
    "KEY_KBD_LCD_MENU5": 700, "KEY_PERFORMANCE": 701, "BTN_TRIGGER_HAPPY": 704,
    "BTN_TRIGGER_HAPPY1": 704, "BTN_TRIGGER_HAPPY2": 705, "BTN_TRIGGER_HAPPY3": 706,
    "BTN_TRIGGER_HAPPY4": 707, "BTN_TRIGGER_HAPPY5": 708, "BTN_TRIGGER_HAPPY6": 709,
    "BTN_TRIGGER_HAPPY7": 710, "BTN_TRIGGER_HAPPY8": 711, "BTN_TRIGGER_HAPPY9": 712,
    "BTN_TRIGGER_HAPPY10": 713, "BTN_TRIGGER_HAPPY11": 714, "BTN_TRIGGER_HAPPY12": 715,
    "BTN_TRIGGER_HAPPY13": 716, "BTN_TRIGGER_HAPPY14": 717, "BTN_TRIGGER_HAPPY15": 718,
    "BTN_TRIGGER_HAPPY16": 719, "BTN_TRIGGER_HAPPY17": 720, "BTN_TRIGGER_HAPPY18": 721,
    "BTN_TRIGGER_HAPPY19": 722, "BTN_TRIGGER_HAPPY20": 723, "BTN_TRIGGER_HAPPY21": 724,
    "BTN_TRIGGER_HAPPY22": 725, "BTN_TRIGGER_HAPPY23": 726, "BTN_TRIGGER_HAPPY24": 727,
    "BTN_TRIGGER_HAPPY25": 728, "BTN_TRIGGER_HAPPY26": 729, "BTN_TRIGGER_HAPPY27": 730,
    "BTN_TRIGGER_HAPPY28": 731, "BTN_TRIGGER_HAPPY29": 732, "BTN_TRIGGER_HAPPY30": 733,
    "BTN_TRIGGER_HAPPY31": 734, "BTN_TRIGGER_HAPPY32": 735, "BTN_TRIGGER_HAPPY33": 736,
    "BTN_TRIGGER_HAPPY34": 737, "BTN_TRIGGER_HAPPY35": 738, "BTN_TRIGGER_HAPPY36": 739,
    "BTN_TRIGGER_HAPPY37": 740, "BTN_TRIGGER_HAPPY38": 741, "BTN_TRIGGER_HAPPY39": 742,
    "BTN_TRIGGER_HAPPY40": 743,
}


def _load_keycodes() -> dict[str, int]:
    table = dict(_SHIPPED)
    try:
        text = _HEADER.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return table
    for name, value in _DEFINE.findall(text):
        # Not a value int() refuses, 010 (octal in C) or more digits than it reads: every
        # program imports this module, and the ValueError would end each at its start.
        with contextlib.suppress(ValueError):
            table[name] = int(value, 0)
    # Bounds and aliases, not keys: KEY_MAX, BTN_MISC for BTN_0, and KEY_CNT and
    # KEY_MIN_INTERESTING, which the kernel's header defines as expressions and another
    # file in its place may define as numbers.
    for bound in ("KEY_MAX", "KEY_CNT", "BTN_MISC", "KEY_MIN_INTERESTING"):
        table.pop(bound, None)
    return table


KEYCODES: dict[str, int] = _load_keycodes()

#: The Clear key, which nothing binds: `clear` unbinds a G key or a mouse
#: button, and a binding of the key is too easily mistaken for that (the
#: owner's decision of 2026-09-26).
CLEAR_KEY = "KEY_CLEAR"

#: The next step after an unknown key name: the full list, since the capture's is curated.
KEYS_LISTED = "`gearbench-ctl keys` lists the keys"

#: The kernel's KEY_MAX, the highest key code: uinput refuses a higher one.
KEY_MAX = 0x2FF

#: The KEY_ half of the table, by name: what a binding may name and the virtual keyboard
#: registers. BTN_ codes go out through VirtualMouse.button.
KEY_NAMES: dict[str, int] = {
    name: code for name, code in KEYCODES.items()
    if name.startswith("KEY_") and 0 < code <= KEY_MAX and name != CLEAR_KEY
}

#: Reverse map for turning a captured keypress back into a name. Where two
#: names share a code the shorter wins, so BTN_LEFT beats BTN_MOUSE.
CODE_NAMES: dict[int, str] = {}
for _name, _code in sorted(KEYCODES.items(), key=lambda kv: (len(kv[0]), kv[0])):
    CODE_NAMES.setdefault(_code, _name)

#: Each modifier key's code and which modifier it is, in the order a spec
#: names them: Ctrl, Shift, Alt, Super, the left key of each before the right.
MODIFIER_KINDS: dict[int, str] = {
    KEYCODES[name]: kind for name, kind in (
        ("KEY_LEFTCTRL", "ctrl"), ("KEY_RIGHTCTRL", "ctrl"),
        ("KEY_LEFTSHIFT", "shift"), ("KEY_RIGHTSHIFT", "shift"),
        ("KEY_LEFTALT", "alt"), ("KEY_RIGHTALT", "alt"),
        ("KEY_LEFTMETA", "super"), ("KEY_RIGHTMETA", "super"),
    )
}
#: The four modifiers as `spec_for_keys` writes them in front of a key, in order.
_KINDS = ("ctrl", "shift", "alt", "super")


def spec_for_keys(codes: Sequence[int], ctrl: bool = False, shift: bool = False,
                  alt: bool = False, meta: bool = False) -> str:
    """A binding spec from keys held down together, `codes` in the order they went down,
    and the modifiers the flags say are held besides.

    With a key other than a modifier among them, the modifiers go in front, spelled without
    side: `ctrl+a`, then the keys in order, `ctrl+a+b`. With modifiers alone, each keeps its
    side, `ctrl+shift` or `rctrl+rshift`, left key of a pair first (the owner's decision of
    2026-09-25 that a chord of modifiers is a binding). A modifier only the flags name, held
    before the capture saw it go down, has the plain name.

    A key no binding may name (the Clear key, a pointer or gamepad button, KEY_RESERVED)
    raises UinputError with `resolve`'s reason, so the capture never offers a spec every
    bind refuses.
    """
    held = dict(zip(_KINDS, (ctrl, shift, alt, meta), strict=True))
    modifiers = [code for code in MODIFIER_KINDS if code in codes]
    keys = [code for code in codes if code not in MODIFIER_KINDS]
    if not keys:
        parts: list[str] = []
        for kind in _KINDS:
            sides = [MODIFIER_SPELLING[c] for c in modifiers if MODIFIER_KINDS[c] == kind]
            parts.extend(sides or ([kind] if held[kind] else []))
        return "+".join(parts)
    for code in modifiers:
        held[MODIFIER_KINDS[code]] = True
    names = []
    for code in keys:
        name = CODE_NAMES.get(code)
        if name is None:
            raise UinputError(f"key code {code} has no name Gearbench knows")
        spelled = name.removeprefix("KEY_").lower()
        if name not in KEY_NAMES:
            resolve(spelled)    # raises: the Clear key, a button, or no key to bind
        names.append(spelled)
    return "+".join([kind for kind in _KINDS if held[kind]] + names)

#: Modifier aliases in a binding spec like "ctrl+shift+f5", and on their own as the key:
#: "shift" is KEY_LEFTSHIFT. Each side has a short form (`rsuper`: the owner's decision of
#: 2026-09-25).
MODIFIERS = {
    "ctrl": "KEY_LEFTCTRL", "control": "KEY_LEFTCTRL",
    "shift": "KEY_LEFTSHIFT",
    "alt": "KEY_LEFTALT",
    "super": "KEY_LEFTMETA", "meta": "KEY_LEFTMETA", "win": "KEY_LEFTMETA",
    "rctrl": "KEY_RIGHTCTRL", "ralt": "KEY_RIGHTALT", "rshift": "KEY_RIGHTSHIFT",
    "rsuper": "KEY_RIGHTMETA",
}

#: A modifier's code to the first alias above that names it: how `spec_for_keys` spells
#: each key of a spec of modifiers alone: ctrl, shift, alt, super, rctrl, ralt, rshift and
#: rsuper.
MODIFIER_SPELLING: dict[int, str] = {}
for _alias, _name in MODIFIERS.items():
    MODIFIER_SPELLING.setdefault(KEYCODES[_name], _alias)


class UinputError(RuntimeError):
    pass


class PointerNameError(UinputError):
    """A pointer or gamepad button named where a key belongs."""

    def __init__(self, name: str, message: str) -> None:
        super().__init__(message)
        self.name = name


def resolve(name: str) -> int:
    """Resolve a key name to its kernel code.

    Accepts "KEY_F13", "f13" or "F13", and a modifier alias, "shift" or "rctrl", for the
    modifier alone (the owner's decision of 2026-09-24, for a G key held as Shift). A BTN_
    name is refused, by that spelling or bare: from the virtual keyboard it never reaches
    the desktop as a pointer button. So is the Clear key (CLEAR_KEY).
    """
    code = _lookup(name)
    if code is not None:
        return code
    if _clear_name(name):
        raise UinputError(
            f"{name!r} is the Clear key, which cannot be bound: clear means unbind"
        )
    if (button := _pointer_code(name)) is not None:
        # A click step sends SENT_BUTTONS, and a mouse button's actions POINTER_ACTIONS: no
        # other button gets either.
        if button in POINTER_ACTIONS.values():
            advice = f"bind {' or '.join(POINTER_ACTIONS)} to a mouse button"
            if button in SENT_BUTTONS:
                advice += ", or use a macro's click step"
        elif button in SENT_BUTTONS:
            advice = "use a macro's click step"
        else:
            advice = "Gearbench sends no such button"
        raise PointerNameError(
            name, f"{name!r} is a pointer or gamepad button, not a key; {advice}")
    raise UinputError(f"unknown key name {name!r}; {KEYS_LISTED}")


def _lookup(name: str) -> int | None:
    """A key name's code, as `resolve` takes the name, or None."""
    upper = name.upper()
    for candidate in (upper, f"KEY_{upper}", MODIFIERS.get(name.lower())):
        if candidate in KEY_NAMES:
            return KEY_NAMES[candidate]
    return None


def _pointer_code(name: str) -> int | None:
    """A pointer or gamepad button's code, by either spelling, or None."""
    upper = name.upper()
    return KEYCODES.get(upper if upper.startswith("BTN_") else f"BTN_{upper}")


def _clear_name(name: str) -> bool:
    """Whether a name is the Clear key's, by either spelling."""
    return name.upper() in (CLEAR_KEY, CLEAR_KEY.removeprefix("KEY_"))


def binding_parts(spec: str) -> list[str]:
    """A spec's key names in order, "ctrl + shift+f5" as ctrl, shift, f5."""
    return re.findall(r"[^+\s]+", spec)


def shown_spec(spec: str) -> str:
    """A key spec on one line, each run of white space as one space: keys may be parted by
    any white space (binding_parts), and shown raw, a line break between them starts a line
    of its own. str.split parts at the characters `\\s` matches."""
    return " ".join(spec.split())


def parse_binding(spec: str) -> tuple[list[int], int]:
    """Parse "ctrl+shift+f5" into the codes that go down before the last key and the last
    key's code. Every key goes down in the spec's order and comes up in reverse, as a G key
    holds a binding and a macro's `key` step taps one.

    Each part is a key name or a modifier alias, so a binding may hold several keys that are
    not modifiers, `a+b+c` or `ctrl+a+b` (the owner's decision of 2026-09-25). A key named
    twice, `a+a` or `ctrl+control+a`, is refused, and so is a + with no key beside it,
    `ctrl++` or `ctrl+`, which would otherwise hold Ctrl alone.
    """
    parts = binding_parts(spec)
    if not parts:
        raise UinputError("empty key binding")
    if any(not part.strip() for part in spec.split("+")):
        raise UinputError(f"{spec!r} has a + with no key beside it; the plus key is "
                          "'shift+equal' on a US layout, or 'kpplus' on the keypad")
    named: dict[int, str] = {}
    for part in parts:
        code = _lookup(part)
        if code is None:
            if len(parts) == 1 or _pointer_code(part) is not None or _clear_name(part):
                resolve(part)       # raises, saying which it is
            raise UinputError(
                f"unknown key name {part!r} in {spec!r}; {KEYS_LISTED}, and a "
                f"modifier is one of {', '.join(sorted(MODIFIERS))}"
            )
        if code in named:
            twice = (f"names {part!r} twice" if named[code].lower() == part.lower()
                     else f"names one key twice, as {named[code]!r} and {part!r}")
            raise UinputError(f"{spec!r} {twice}; a binding holds each key once")
        named[code] = part
    *before, last = named
    return before, last


#: What the key capture's list offers, group by group, in a spec's spelling: a curated list
#: (the owner's decision of 2026-09-25), since the full one buries the keys a binding wants
#: among names like alterase. Every other name still binds with `gearbench-ctl bind` and is
#: listed by `gearbench-ctl keys`.
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
KEY_LIST = tuple(KEY_GROUPS.values())

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


def key_matches(name: str, query: str) -> bool:
    """Whether the list's search for `query` shows the key `name`.

    Part of the name or of its label, in any case, with spaces and
    underscores ignored and a leading KEY_ dropped, so "KEY_F13", "play
    pause" and "caps_lock" all find their key. An empty search shows
    everything. The query comes stripped: a KEY_ after leading white space
    is searched as text, not dropped.
    """
    wanted = _squash(query.lower().removeprefix("key_"))
    return wanted in _squash(name) or wanted in _squash(KEY_LABELS[name])


#: The kernel's magic SysRq (Documentation/admin-guide/sysrq.rst, drivers/tty/sysrq.c). Its
#: input filter keeps a state for each device with Left Alt, the virtual keyboard included
#: (its Handlers line in /proc/bus/input/devices names sysrq, 2026-10-13). SysRq going down
#: while an Alt is down arms it until that Alt comes up, whether or not SysRq is still down,
#: and a key other than Alt, Shift or SysRq pressed meanwhile runs a kernel command: under
#: kernel.sysrq 176, Ubuntu's default, B reboots and O powers off at once, unsynced. Alt and
#: SysRq alone are still Alt+Print Screen, a screenshot, and Shift picks a command's case.
_ALT_CODES = frozenset({KEYCODES["KEY_LEFTALT"], KEYCODES["KEY_RIGHTALT"]})
_SYSRQ_CODE = KEYCODES["KEY_SYSRQ"]
_NO_SYSRQ_COMMAND = _ALT_CODES | {
    _SYSRQ_CODE, KEYCODES["KEY_LEFTSHIFT"], KEYCODES["KEY_RIGHTSHIFT"]}


class _UinputDevice:
    """One uinput device. Subclasses say what it advertises and what it is called.

    The capabilities are fixed at creation: uinput refuses a code the device never
    registered, so they are declared up front.

    A code held down is counted per holder that pressed it with `key`: a G key, a mouse
    button and a macro can all hold Shift at once, and the kernel keeps one state per code.
    The first press goes out and only the last release does, so no holder lets a key up
    under another, and a tap leaves alone whatever is held. Every holder must let go of what
    it pressed, once, or the code stays down until the device is destroyed.

    A press the kernel would take as a SysRq command (_NO_SYSRQ_COMMAND) is refused, whoever
    holds Alt and SysRq: a G key bound to Alt and a macro tapping Print Screen arm it too.
    """

    name = "Gearbench virtual device"
    product = 0x0900
    #: struct input_id's bus type.
    bus = BUS_USB

    def __init__(self) -> None:
        self._fd = -1
        #: Code -> how many holders have it down. A code at none is absent.
        self._down: dict[int, int] = {}
        #: Whether the kernel's SysRq filter is armed: SysRq went down while an Alt was down,
        #: and an Alt still is.
        self._sysrq_armed = False
        self._open()

    def _capabilities(self) -> dict[int, list[int]]:
        """Event type -> the codes to register for it."""
        raise NotImplementedError

    def _open(self) -> None:
        try:
            self._fd = os.open(UINPUT, os.O_WRONLY | os.O_NONBLOCK)
        except FileNotFoundError as exc:
            raise UinputError(f"{UINPUT} does not exist") from exc
        except OSError as exc:
            # The node and the reason only: the daemon's warning adds the rule, getfacl and
            # the uinput module.
            raise UinputError(f"cannot open {UINPUT}: {reason(exc)}") from exc

        set_bit = {EV_KEY: UI_SET_KEYBIT, EV_REL: UI_SET_RELBIT}
        try:
            fcntl.ioctl(self._fd, UI_SET_EVBIT, EV_SYN)
            for type_, codes in self._capabilities().items():
                fcntl.ioctl(self._fd, UI_SET_EVBIT, type_)
                for code in codes:
                    fcntl.ioctl(self._fd, set_bit[type_], code)
            setup = _SETUP.pack(
                self.bus,
                0x1D6B, self.product, 0x0001,      # a plausible virtual id
                self.name.encode("utf-8"),
                0,
            )
            fcntl.ioctl(self._fd, UI_DEV_SETUP, setup)
            fcntl.ioctl(self._fd, UI_DEV_CREATE)
        except OSError as exc:
            os.close(self._fd)
            raise UinputError(
                f"cannot set up {self.name} on {UINPUT}: {reason(exc)}") from exc
        # The kernel needs a moment to publish the node before anything can
        # read from it; events written immediately are otherwise lost.
        time.sleep(0.05)

    # -- emitting --------------------------------------------------------

    def _write(self, type_: int, code: int, value: int) -> None:
        if self._fd < 0:
            raise UinputError(f"{self.name} is closed")
        now = time.time()
        packet = EVENT.pack(int(now), int((now % 1) * 1_000_000), type_, code, value)
        try:
            os.write(self._fd, packet)
        except OSError as exc:
            if exc.errno == errno.ENODEV:
                raise UinputError(f"{self.name} vanished") from exc
            raise UinputError(f"cannot write to {self.name}: {reason(exc)}") from exc

    def sync(self) -> None:
        self._write(EV_SYN, SYN_REPORT, 0)

    def key(self, code: int, pressed: bool) -> None:
        """Press or let go of one code for one holder.

        Only the first press and the last holder's release are written. A press whose write
        fails is not counted; a release is counted before it is written, so a failed one
        leaves nothing counted down. A release of a code nobody holds writes nothing. A
        failed write raises UinputError, and so does a first press the kernel would take as a
        SysRq command, which writes nothing.
        """
        held = self._down.get(code, 0)
        if pressed:
            if not held:
                self._refuse_sysrq_command([code])
                self._write(EV_KEY, code, 1)
                self.sync()
            self._down[code] = held + 1
        elif held > 1:
            self._down[code] = held - 1
        elif held:
            del self._down[code]
            self._write(EV_KEY, code, 0)
            self.sync()
        self._follow_sysrq(pressed and not held and code == _SYSRQ_CODE)

    def _refuse_sysrq_command(self, presses: Sequence[int]) -> None:
        """Raise UinputError where one of `presses`, going down in order over what is down
        now, would run a kernel SysRq command (_NO_SYSRQ_COMMAND)."""
        armed = self._sysrq_armed
        alt = not _ALT_CODES.isdisjoint(self._down)
        for code in presses:
            if armed and code not in _NO_SYSRQ_COMMAND:
                name = CODE_NAMES[code].removeprefix("KEY_").lower()
                raise UinputError(
                    f"{self.name} did not press {name}: once Print Screen (sysrq) goes down "
                    f"with Alt held, and until Alt comes up, the kernel can take {name} as a "
                    f"SysRq command, such as an instant reboot")
            alt = alt or code in _ALT_CODES
            armed = armed or (code == _SYSRQ_CODE and alt)

    def _follow_sysrq(self, sysrq_went_down: bool) -> None:
        """Arm the SysRq filter as the kernel does once SysRq went down with an Alt down, and
        disarm it once no Alt is. The kernel disarms it when the Alt that armed it comes up,
        so with both Alts down this refuses a key the kernel would let through."""
        self._sysrq_armed = ((self._sysrq_armed or sysrq_went_down)
                             and not _ALT_CODES.isdisjoint(self._down))

    @property
    def holding(self) -> dict[int, int]:
        """What is down, each code with how many holders have it down."""
        return dict(self._down)

    # -- lifecycle -------------------------------------------------------

    def close(self) -> None:
        # The kernel lets go of every key a destroyed device had down.
        self._down.clear()
        self._sysrq_armed = False
        if self._fd >= 0:
            with contextlib.suppress(OSError):
                fcntl.ioctl(self._fd, UI_DEV_DESTROY)
            try:
                os.close(self._fd)
            finally:
                self._fd = -1


class VirtualKeyboard(_UinputDevice):
    """A uinput keyboard that types the key bindings of the G keys and the mouse's buttons,
    and a macro's key and text steps."""

    name = "Gearbench virtual keyboard"
    product = 0x0915

    def _capabilities(self) -> dict[int, list[int]]:
        # KEY_NAMES stops at KEY_MAX, the most uinput takes.
        return {EV_KEY: sorted(KEY_NAMES.values())}

    def tap(self, codes: Sequence[int], final: int) -> None:
        """Press every key of a binding in order, the last one `final`, and
        let go of them in reverse: `ctrl+a` and `a+b+c` alike.

        A code a holder has down (`key`) is neither pressed nor let go of
        here: a shift held by a G key or a macro's `key_down` would come up
        at the end of the tap, and the rest would go out unshifted. A key
        that is held itself stays down, and only the modifiers not held go
        down and up around it.

        A tap with a press the kernel would take as a SysRq command raises
        UinputError and writes nothing.
        """
        free = [code for code in (*codes, final) if code not in self._down]
        if not free:
            return
        self._refuse_sysrq_command(free)
        # A tap leaves `_down` as it was: SysRq tapped under an Alt a holder has down arms
        # the filter until that Alt comes up.
        self._follow_sysrq(_SYSRQ_CODE in free)
        for code in free:
            self._write(EV_KEY, code, 1)
        self.sync()
        for code in reversed(free):
            self._write(EV_KEY, code, 0)
        self.sync()

    def send(self, spec: str) -> None:
        self.tap(*parse_binding(spec))


class VirtualMouse(_UinputDevice):
    """A uinput pointer for back and forward, macro clicks and, in the G502 X PLUS's HOST
    mode, the wheel tilt's sideways scroll.

    Declares what libinput needs to call it a mouse, REL_X, REL_Y and BTN_LEFT, with the
    other ordinary buttons and both wheels, though it never moves the pointer.
    """

    name = "Gearbench virtual mouse"
    product = 0x0916
    #: libinput counts a mouse on USB or Bluetooth as external, and a touchpad set to turn
    #: off for one would stay off while the daemon runs.
    bus = BUS_VIRTUAL

    def _capabilities(self) -> dict[int, list[int]]:
        return {EV_KEY: list(POINTER_BUTTONS),
                EV_REL: [REL_X, REL_Y, REL_WHEEL, REL_HWHEEL, REL_HWHEEL_HI_RES]}

    def hwheel(self, steps: int) -> None:
        """Scroll sideways `steps` notches, negative to the left: both horizontal axes in one
        report, as the G502 X PLUS's node sends a held tilt's step in ONBOARD mode (measured
        2026-09-25); libinput takes the high-resolution axis where a device has one."""
        self._write(EV_REL, REL_HWHEEL, steps)
        self._write(EV_REL, REL_HWHEEL_HI_RES, steps * HI_RES_NOTCH)
        self.sync()

    def button(self, code: int, pressed: bool) -> None:
        if code not in POINTER_BUTTONS:
            raise UinputError(f"0x{code:x} is not one of the virtual mouse's buttons")
        self.key(code, pressed)
