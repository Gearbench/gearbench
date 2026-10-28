"""Custom assignments on the G515 generation (0x1B05), the layer files that carry them, written
through 0x8101, and Game Mode's state (0x4523): from G HUB 39.1.2's USB captures of a wired
G515 TKL, firmware U1 67.01.B0008, on 2026-10-21. No published specification covers these
features, so each function, byte and frame here is what those captures show, or is marked
assumed.

0x1B05: fn 1 reads whether custom assignments are on (01) or off (00), fn 2 reads the eight
shortcuts that can turn them on and off, as pairs of key ids, and fn 3 reads the one chosen,
`01 NN`, NN counting from 1 in fn 2's list. Gearbench writes none of them: the keyboard keeps
the shortcut and the on/off state across a replug, and the G915 X LIGHTSPEED kept both, on and
FN+F1, across a power loss (on Linux and in Windows capture G5c, 2026-10-23). The keyboard
sends event 0 for its keys set to report to the host, naming each one held by its trigger
number (TriggeredKeys), and event 6 when its shortcut turns custom assignments on (01) or off
(00).

0x8101 carries the assignments as a file per layer, which the keyboard applies at once and
keeps until it loses power (write_layer). A file is `00 NN`, NN entries, then 11 bytes an
entry, the key id first: a remap, which the keyboard then types itself (remap_entry), or a key
that reports to the host (report_entry). 0x8101 fn 6 switches the keyboard between its on-board
mode, which it powers up in, as the G915 X LIGHTSPEED did (Windows capture G5c, 2026-10-23),
and host mode (read_host_mode, write_mode, FN_MODE). The BASE layer's file took effect with no
fn 6 in the mode the owner's wired G515 TKL powered up in, on 2026-10-22.

0x4523 sends event 0 as Game Mode goes on (01) or off (00). Its function 0 write, the keys
Game Mode blocks, is not sent: whether the keyboard keeps that list is not known.

A key id is the keyboard's own number for a key, neither a HID usage nor a lighting zone, and
models differ in them: F1 is 0x10 on the G515 TKL and 0x09 on the G915 X LIGHTSPEED. 0x1B10,
the control list (read_controls), gives the control id at each key id, and a control id is
Logitech's number for a key, the same on both: F1's is 0x00F6 and FN's 0x0034 on each. So a
key is found and named by its control id (key_ids_at, CONTROL_NAMES). KeyboardModel.key_ids
gives a model's G keys' key ids where they were measured, and KeyboardModel.control_ids their
control ids. A key's trigger number, which event 0 names it by, is another:
KeyboardModel.trigger_numbers gives a model's.
"""

from __future__ import annotations

import zlib
from collections.abc import Callable, Iterable, Iterator, Sequence

from .hidpp import WRITE_ONCE_TIMEOUT, Event, Feature, Hidpp
from .keys import KeyEvent

F_KEY_CONTROL = 0x1B05
F_FILES = 0x8101
F_GAME_MODE = 0x4523

FN_ENABLED = 1          # reads 01 on, 00 off
FN_SHORTCUTS = 2        # reads eight pairs of key ids
FN_SHORTCUT = 3         # reads 01 NN
#: The events' address bytes: the event's number over software id 0.
TRIGGER_EVENT = 0x00
TOGGLE_EVENT = 0x60
GAME_MODE_EVENT = 0x00
#: Game Mode in `status` on a keyboard that has it, until an event since its connect or its
#: last power loss gives it, and the words for that: the keyboard reports it only as an event.
GAME_MODE_UNKNOWN = "unknown"
GAME_MODE_NOT_KNOWN = "not known until the keyboard's Game Mode button is pressed"
#: The words for the shortcut that turns custom assignments "on" or "off": "FN+F1 turns them on".
SHORTCUT_TURNS = "{} turns them {}"

FN_BEGIN = 2            # 00 LL: a file of LL bytes follows
FN_DATA = 3             # the file, DATA_FRAME bytes a frame
FN_COMMIT = 9           # 1b 05, the layer, 02 00, the length, the CRC-32
#: The bytes of the file each fn 3 frame carries: a long report's sixteen.
DATA_FRAME = 16
#: fn 6, the mode: with no parameter it reads it, and HOST_MODE or ONBOARD_MODE sets it, each
#: as G HUB 39.1.2 sent it to the owner's G515 TKL (A2, A5, A5c and A5d) and the owner's script
#: to the G915 X LIGHTSPEED (2026-10-23). Its reply, read or set, starts IN_HOST_MODE in host
#: mode, `00 00 01` on the G515 TKL, and on the G915 X LIGHTSPEED `00 01 01` read from Linux and
#: `00 00 01` from Windows (capture G5c), what its second byte says not known, and `03` in the
#: on-board mode.
FN_MODE = 6
HOST_MODE, ONBOARD_MODE = 0x05, 0x0F
IN_HOST_MODE = 0x00
#: The layers a file is for, as fn 9 names them: G HUB wrote 02 for FN and 03 for G-SHIFT too.
LAYER_BASE = 0x01

#: A remap entry's bytes between its key id and its usage page, which is KEYBOARD_PAGE or
#: CONSUMER_PAGE, before the usage, big-endian: F1 to Q was `10 01 00 80 00 00 00 80 02 00 14`.
REMAP = bytes.fromhex("01 00 80 00 00 00 80")
KEYBOARD_PAGE = 0x02
CONSUMER_PAGE = 0x03
#: A reporting entry is its key id, 01, a tag, then REPORT_TAIL. The tag is 00 in a remap and
#: was 01 in G HUB's one reporting entry, for F1. Event 0 gives no bit of it: on 2026-10-22 F1
#: to F4 tagged 01 to 04 came as 02 to 05, not 02, 04, 08 and 10. The number is one past the
#: tag: on the G915 X LIGHTSPEED G1 tagged 05 came as 06 on 2026-10-23, where tagged 01 it came
#: as 02. Each key is tagged with its G key number, its F number on the G515s.
REPORT_TAIL = bytes.fromhex("00 00 80 00 80 00 00 00")

#: The name of each key the eight shortcuts are made of, by its control id: the control id
#: at each key id of fn 2's pairs in the G515 TKL's control list, both as G HUB read them off
#: the owner's on 2026-10-21, beside the names G HUB listed in fn 2's order, FN+F1,
#: L.CTRL+CAPS, L.ALT+CAPS, R.CTRL+ENTER, FN+ENTER, R.CTRL+CAPS, L.ALT+ENTER and
#: L.CTRL+ENTER. The G915 X LIGHTSPEED's shortcut, FN+F1 on the owner's on 2026-10-23, was
#: key ids 0x69 and 0x09, which hold 0x0034 and 0x00F6 in its own list.
CONTROL_NAMES = {0x00F6: "F1", 0x010D: "Caps Lock", 0x0155: "Enter", 0x010F: "Left Ctrl",
                 0x018B: "Left Alt", 0x0034: "FN", 0x0114: "Right Ctrl"}

#: 0x1B10, the control list (G HUB's ControlList): fn 0 the count of its entries, fn 1
#: CONTROLS_A_READ of them, two bytes each, from the entry its one parameter names, the
#: entry at a key id being the control id of that key. G HUB read the owner's G515 TKL so at
#: its start and again after a key assignment, a count of 0x80 and fn 1 at 0x00 to 0x78, the
#: same each time (2026-10-21); the G915 X LIGHTSPEED answered the device report the same way
#: on its cable and through its receiver (2026-10-23). No other source sends it.
F_CONTROL_LIST = 0x1B10
FN_CONTROL_COUNT = 0
FN_CONTROLS = 1
CONTROLS_A_READ = 8


def remap_entry(key_id: int, page: int, usage: int) -> bytes:
    """The entry that makes key `key_id` type `usage` of usage page `page` (KEYBOARD_PAGE or
    CONSUMER_PAGE) by itself."""
    return bytes([key_id]) + REMAP + bytes([page]) + usage.to_bytes(2, "big")


def report_entry(key_id: int, tag: int) -> bytes:
    """The entry, its third byte `tag` (REPORT_TAIL), that makes key `key_id` report to the
    host through event 0, typing nothing itself."""
    return bytes([key_id, 0x01, tag]) + REPORT_TAIL


def layer_file(entries: Sequence[bytes]) -> bytes:
    """A layer's file: the count of `entries`, two bytes big-endian, then the entries."""
    return len(entries).to_bytes(2, "big") + b"".join(entries)


def commit_params(layer: int, data: bytes) -> bytes:
    """fn 9's parameters for file `data` of `layer`: the feature it belongs to, the layer, 02 00,
    the length and the CRC-32 of `data`, each big-endian. The length is two bytes, as fn 2's
    is; every file captured was under 256 bytes."""
    return (F_KEY_CONTROL.to_bytes(2, "big") + bytes([layer, 0x02, 0x00])
            + len(data).to_bytes(2, "big") + zlib.crc32(data).to_bytes(4, "big"))


def write_layer(dev: Hidpp, files: Feature, layer: int, data: bytes) -> None:
    """Write file `data` for `layer` through 0x8101 at `files`: begin, the data, the commit,
    each frame of the data and the commit sent once.

    Every file captured fitted one frame; files of three, sent in order, took effect on the
    owner's wired G515 TKL on 2026-10-22. fn 3's answer, `00 01` to each one-frame file
    captured, is not read, since what it counts is not known. The transport picks each
    request's report by its length, as G HUB's did: the two bytes of an empty file went short,
    a frame of 13 long."""
    dev.request(files.index, FN_BEGIN, len(data).to_bytes(2, "big"))
    # A resend of a frame or of the commit the keyboard took would stage that frame twice or
    # commit again, and nothing read here would catch it, so a lost answer fails the file.
    for start in range(0, len(data), DATA_FRAME):
        dev.request(files.index, FN_DATA, data[start:start + DATA_FRAME],
                    timeout=WRITE_ONCE_TIMEOUT, retries=0)
    dev.request(files.index, FN_COMMIT, commit_params(layer, data),
                timeout=WRITE_ONCE_TIMEOUT, retries=0)


def read_host_mode(dev: Hidpp, files: Feature) -> bool:
    """Whether the keyboard is in host mode, from 0x8101 fn 6 at `files`."""
    return dev.request(files.index, FN_MODE)[0] == IN_HOST_MODE


def write_mode(dev: Hidpp, files: Feature, host: bool) -> bool:
    """Set host mode, or with `host` False the on-board mode, through 0x8101 fn 6 at `files`:
    whether the reply then says host mode."""
    return dev.request(files.index, FN_MODE,
                       bytes([HOST_MODE if host else ONBOARD_MODE]))[0] == IN_HOST_MODE


def read_enabled(dev: Hidpp, control: Feature) -> bool:
    """Whether the keyboard's custom assignments are on, from 0x1B05 at `control`."""
    return dev.request(control.index, FN_ENABLED)[0] == 0x01


def read_shortcut(dev: Hidpp, control: Feature) -> tuple[int, int] | None:
    """The key ids of the shortcut that turns the keyboard's custom assignments on and off, or
    None where fn 3 names no pair of fn 2's list."""
    pairs = dev.request(control.index, FN_SHORTCUTS)
    chosen = dev.request(control.index, FN_SHORTCUT)[1]
    if not 1 <= chosen <= len(pairs) // 2:
        return None
    return pairs[2 * chosen - 2], pairs[2 * chosen - 1]


def read_controls(read: Callable[[int, bytes], bytes]) -> tuple[int, ...]:
    """0x1B10's list (F_CONTROL_LIST), the control id at each key id from 0, through `read`,
    which sends one of its functions with its parameters and answers the reply. The count is
    a byte, so 32 reads at most. A reply cut short raises IndexError."""
    count = read(FN_CONTROL_COUNT, b"")[0]
    controls: list[int] = []
    for first in range(0, count, CONTROLS_A_READ):
        reply = read(FN_CONTROLS, bytes([first]))
        controls += [reply[at] << 8 | reply[at + 1]
                     for at in range(0, 2 * min(CONTROLS_A_READ, count - first), 2)]
    return tuple(controls)


def key_ids_at(controls: Sequence[int], wanted: Iterable[int]) -> tuple[int | None, ...]:
    """The key id that holds each control id of `wanted` in `controls`, a control list
    (read_controls), or None for one it does not hold."""
    at = {control: key_id for key_id, control in enumerate(controls) if control}
    return tuple(at.get(control) for control in wanted)


def shortcut_words(keys: Iterable[int], controls: Sequence[int]) -> str:
    """A shortcut's keys as a line names them, by the control id `controls`, the keyboard's
    control list, holds at each (CONTROL_NAMES): "FN+F1", or "key 0x4c" for a key id whose
    control id it names no key for, or that the list does not reach, as with no list read."""
    return "+".join(CONTROL_NAMES.get(controls[key] if key < len(controls) else 0,
                                      f"key {key:#04x}") for key in keys)


class TriggeredKeys:
    """A keyboard's keys that custom assignments set to report to the host, by their G key
    numbers: G1 is the first of the model's `key_ids` and of its `trigger_numbers`, the
    numbers event 0 gives them by. The BASE layer's file sets the keys `assign` is given to
    report and leaves the others typing their own keys. It keeps the G keys held last to diff
    against, as DivertedKeys keeps the G keys' mask."""

    def __init__(self, dev: Hidpp, key_ids: tuple[int, ...],
                 trigger_numbers: tuple[int, ...]) -> None:
        self.dev = dev
        self._control = dev.require(F_KEY_CONTROL, "custom assignments")
        self._files = dev.require(F_FILES, "file store")
        self.key_ids = key_ids
        #: The G key number of each trigger number.
        self._g_key_of = {number: g_key for g_key, number in enumerate(trigger_numbers, 1)}
        #: The G key numbers the file written last sets to report, or None while what the
        #: keyboard holds is not known.
        self.assigned: tuple[int, ...] | None = None
        self._held: frozenset[int] = frozenset()

    def assign(self, numbers: Iterable[int]) -> None:
        """Write the BASE layer's file setting the keys of G key numbers `numbers` to report,
        and no other key, forgetting which keys were held: a key down across the write would
        make its next press no change, and its release none, so what it held is for the caller
        to let go of."""
        assigned = tuple(sorted(set(numbers)))
        data = layer_file([report_entry(self.key_ids[number - 1], number)
                           for number in assigned])
        self.assigned = None
        write_layer(self.dev, self._files, LAYER_BASE, data)
        self.assigned, self._held = assigned, frozenset()

    def handles(self, event: Event) -> bool:
        return event.feature_index == self._control.index and event.address == TRIGGER_EVENT

    def decode(self, event: Event) -> Iterator[KeyEvent]:
        """Key events, lowest G key first, from an event 0 that `handles` claimed: none from
        one that changes nothing. Its first byte is the trigger number of the key held, 00 for
        none, as the owner's wired G515 TKL sent it one key at a time on 2026-10-22. Each byte
        after it is one more key held: the G915 X LIGHTSPEED sent G1 and G2 held as `02 03`
        on 2026-10-23. A number of no G key is no key."""
        held = frozenset(self._g_key_of[number] for number in event.data
                         if number in self._g_key_of)
        changed = self._held ^ held
        self._held = held
        for number in sorted(changed):
            yield KeyEvent(f"G{number}", number in held)
