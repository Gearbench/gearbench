"""Macros: a named list of typed steps, one JSON file each.

A G key bound to `macro:<name>` runs one. Files live in
`~/.config/gearbench/macros/`, as the game profiles do. `MacroRunner` is a
state machine on the daemon's single-threaded loop, which also carries the
devices and the socket: a delay is a deadline the loop sleeps until
(`deadline()`), never a sleep or a thread. The runner releases everything it
pressed whenever the macro stops, since a stuck modifier makes the desktop
unusable. Text steps send key codes for a US ANSI layout; a character with no
key there is refused at save.
"""

from __future__ import annotations

import logging
import string
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from .config import (
    ConfigError,
    SkipWarnings,
    changed_on_disk,
    config_dir,
    counted,
    expect,
    file_stamp,
    from_file,
    kind_of,
    macro_name,
    reason,
    refuse_name,
    sync_removal,
    whole,
    word_list,
    write_json,
    written,
)
from .library_files import (
    by_name,
    check_version,
    file_name,
    read_folder,
    read_name,
    refuse_taken,
    slug,
)
from .uinput import (
    BTN_EXTRA,
    BTN_LEFT,
    BTN_MIDDLE,
    BTN_RIGHT,
    BTN_SIDE,
    KEYCODES,
    MODIFIER_KINDS,
    PointerNameError,
    UinputError,
    VirtualKeyboard,
    VirtualMouse,
    binding_parts,
    parse_binding,
    shown_spec,
)

log = logging.getLogger("gearbench")

VERSION = 1

#: `once` runs the steps once; `while_held` repeats while the key is down;
#: `toggle` repeats until the same key is pressed again, ignoring the release.
ONCE, WHILE_HELD, TOGGLE = "once", "while_held", "toggle"
REPEAT_MODES = (ONCE, WHILE_HELD, TOGGLE)

#: One line per repeat mode (repeat_summary).
REPEAT_SUMMARIES = {
    ONCE: "runs once",
    WHILE_HELD: "repeats while held",
    TOGGLE: "repeats until pressed again",
}

KEY, TEXT, DELAY, CLICK = "key", "text", "delay", "click"
#: A press or a click split in halves, to hold across a delay. Underscores, as
#: in `while_held`, not the hyphens of a binding action.
KEY_DOWN, KEY_UP = "key_down", "key_up"
CLICK_DOWN, CLICK_UP = "click_down", "click_up"
STEP_TYPES = (KEY, KEY_DOWN, KEY_UP, TEXT, DELAY, CLICK, CLICK_DOWN, CLICK_UP)

#: The step types whose `value` is a key spec, and those whose `button` names
#: a button.
KEY_STEPS = (KEY, KEY_DOWN, KEY_UP)
CLICK_STEPS = (CLICK, CLICK_DOWN, CLICK_UP)

#: A click step's button name to the evdev code the virtual mouse sends.
CLICK_BUTTONS: dict[str, int] = {
    "left": BTN_LEFT, "right": BTN_RIGHT, "middle": BTN_MIDDLE,
    "side": BTN_SIDE, "extra": BTN_EXTRA,
}

#: A click step's button as the user reads it: "extra" alone does not say
#: which physical button it is.
CLICK_LABELS: dict[str, str] = {
    "left": "Left", "right": "Right", "middle": "Middle",
    "side": "Side (back)", "extra": "Extra (forward)",
}

# -- the caps --------------------------------------------------------------
#
# MAX_STEPS: the runner walks a cycle on the daemon's only thread, so 64 bounds
# the work per cycle and is more than a hand can usefully trigger.
# MAX_TEXT_LENGTH: a text step goes out in one tick, one tap per character; 256,
# a chat line, is about 1500 writes to /dev/uinput, a millisecond or two.
# MAX_DELAY_MS: a longer delay would hold a released `while_held` macro that
# long.
# MIN_CYCLE_MS: the floor between two cycle starts of a repeating macro, or
# zero-delay steps busy-loop the thread that owns the devices. 50 ms is 20
# cycles a second, faster than a hand taps.
MAX_STEPS = 64
MAX_TEXT_LENGTH = 256
MAX_DELAY_MS = 60_000
MIN_CYCLE_MS = 50
#: The standard delay between two events, never added at playback. G HUB's
#: new-macro default, the owner's decision of 2026-09-23.
STANDARD_DELAY_MS = 50

#: Playing a macro by hand (the window's Play, `gearbench-ctl macro-run`), the
#: owner's decision of 2026-09-24. PLAY_DELAY: the countdown both front ends
#: ask for, in seconds, to click into the target window. MAX_PLAY_DELAY: the
#: most the daemon takes. PLAY_KEY: the key a played macro runs under, which no
#: key event carries and no binding names, so a played `toggle` runs until
#: `cancel()`.
PLAY_DELAY = 3
MAX_PLAY_DELAY = 60
PLAY_KEY = "play"


def macros_dir() -> Path:
    return config_dir() / "macros"


# -- typing text -----------------------------------------------------------

#: Characters that share a key on a US ANSI board, unshifted then shifted.
_PAIRS = (
    ("`", "~", "GRAVE"), ("1", "!", "1"), ("2", "@", "2"), ("3", "#", "3"),
    ("4", "$", "4"), ("5", "%", "5"), ("6", "^", "6"), ("7", "&", "7"),
    ("8", "*", "8"), ("9", "(", "9"), ("0", ")", "0"),
    ("-", "_", "MINUS"), ("=", "+", "EQUAL"),
    ("[", "{", "LEFTBRACE"), ("]", "}", "RIGHTBRACE"), ("\\", "|", "BACKSLASH"),
    (";", ":", "SEMICOLON"), ("'", '"', "APOSTROPHE"),
    (",", "<", "COMMA"), (".", ">", "DOT"), ("/", "?", "SLASH"),
)


def _typing_table() -> dict[str, tuple[bool, int]]:
    """Character to (needs shift, key code), from `uinput.KEYCODES`."""
    table: dict[str, tuple[bool, int]] = {}

    def add(char: str, name: str, shift: bool = False) -> None:
        table[char] = (shift, KEYCODES[f"KEY_{name}"])

    for letter in string.ascii_lowercase:
        add(letter, letter.upper())
        add(letter.upper(), letter.upper(), shift=True)
    for plain, shifted, name in _PAIRS:
        add(plain, name)
        add(shifted, name, shift=True)
    add(" ", "SPACE")
    add("\t", "TAB")
    add("\n", "ENTER")
    return table


TYPING: dict[str, tuple[bool, int]] = _typing_table()
_SHIFT_CODE = KEYCODES["KEY_LEFTSHIFT"]


# -- validation ------------------------------------------------------------

def text_problem(text: str) -> str | None:
    """Why a text step cannot carry `text`, or None."""
    if not text:
        return "a text step needs something to type"
    if len(text) > MAX_TEXT_LENGTH:
        return (f"{len(text)} characters is longer than the {MAX_TEXT_LENGTH} "
                f"a text step may carry")
    bad = next((char for char in text if char not in TYPING), None)
    if bad is not None:
        return f"text steps send the keys of a US ANSI layout, which has no key for {bad!r}"
    return None


def _not(raw: dict, field: str) -> str:
    """What a refusal says of `field`: the file's own value, or that the step has none."""
    return f"not {written(raw[field])}" if field in raw else "and the step has none"


def validate_step(raw: object, index: int, where: str) -> dict:
    """One step, checked and normalized, or a ConfigError naming its position and field."""
    at = f"{where}.steps[{index}]"
    if not isinstance(raw, dict):
        raise ConfigError(f"{at}: a step must be a JSON object, not {kind_of(raw)}")
    # Not str(): str() of a deeply nested list recurses too far.
    kind = raw.get("type", "")
    if kind not in STEP_TYPES:
        raise ConfigError(
            f"{at}.type: must be {word_list(STEP_TYPES)}, {_not(raw, 'type')}")

    if kind in KEY_STEPS or kind == TEXT:
        # Not str(): a null value would type "None".
        value = raw.get("value", "")
        expect(value, str, f"{at}.value", "text")
        if kind == TEXT:
            if (problem := text_problem(value)) is not None:
                raise ConfigError(f"{at}.value: {problem}")
            return {"type": TEXT, "value": value}
        spec = value.strip()
        if not spec:
            raise ConfigError(f"{at}.value: a {kind} step needs a key spec like 'ctrl+f5'")
        if macro_name(spec) is not None:
            raise ConfigError(
                f"{at}.value: a macro cannot run another macro. Copy the steps in "
                f"instead."
            )
        try:
            parse_binding(spec)
        except PointerNameError as exc:
            # Not the resolver's message, whose back and forward advice is for a
            # binding: a step sends a mouse button only from a click step.
            raise ConfigError(
                f"{at}.value: {exc.name!r} is a pointer or gamepad button, not a "
                f"key; a click step sends a mouse button, one of "
                f"{word_list(list(CLICK_BUTTONS))}"
            ) from None
        except UinputError as exc:
            raise ConfigError(f"{at}.value: {exc}") from None
        return {"type": kind, "value": spec}

    if kind == DELAY:
        ms = raw.get("ms")
        if not whole(ms):
            raise ConfigError(
                f"{at}.ms: a delay needs a whole number of milliseconds, {_not(raw, 'ms')}")
        if not 0 <= ms <= MAX_DELAY_MS:
            raise ConfigError(f"{at}.ms: {ms} ms is outside 0 to {MAX_DELAY_MS}")
        return {"type": DELAY, "ms": ms}

    button = raw.get("button", "")
    # A str check, not str().lower(): str() of a deeply nested list recurses too far.
    if not isinstance(button, str) or button.lower() not in CLICK_BUTTONS:
        raise ConfigError(
            f"{at}.button: must be {word_list(list(CLICK_BUTTONS))}, {_not(raw, 'button')}"
        )
    return {"type": kind, "button": button.lower()}


def with_step(steps: list[dict], step: dict) -> list[dict]:
    """A copy of `steps` with `step` appended, the standard delay first when it
    and the last step are both events. With room for one step only, the delay
    is left out. `steps` has room for one: the editor refuses a full macro before
    it asks, to say so."""
    added = [dict(s) for s in steps]
    if (added and step.get("type") != DELAY and added[-1].get("type") != DELAY
            and len(added) + 2 <= MAX_STEPS):
        added.append({"type": DELAY, "ms": STANDARD_DELAY_MS})
    added.append(dict(step))
    return added


def validate_steps(raw: object, where: str) -> list[dict]:
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise ConfigError(f"{where}.steps: must be a list of steps")
    if not raw:
        raise ConfigError(f"{where}.steps: a macro needs at least one step")
    if len(raw) > MAX_STEPS:
        raise ConfigError(
            f"{where}.steps: {len(raw)} steps is more than the {MAX_STEPS} a macro may hold"
        )
    return [validate_step(step, i, where) for i, step in enumerate(raw)]


# -- the macro -------------------------------------------------------------

@dataclass
class Macro:
    name: str
    repeat: str = ONCE
    steps: list[dict] = field(default_factory=list)
    #: The file it was loaded from or last saved to, which save() overwrites or,
    #: after a rename, removes.
    path: Path | None = field(default=None, compare=False)

    def to_json(self) -> dict:
        return {
            "version": VERSION,
            "name": self.name,
            "repeat": self.repeat,
            "steps": [dict(step) for step in self.steps],
        }

    @classmethod
    def from_json(cls, data: object, where: str) -> Macro:
        if not isinstance(data, dict):
            raise ConfigError(f"{where}: a macro must be a JSON object")
        check_version(data, where, VERSION)
        name = read_name(data, where, "macro")
        repeat = data.get("repeat", ONCE)
        if repeat not in REPEAT_MODES:
            raise ConfigError(
                f"{where}.repeat: must be {word_list(REPEAT_MODES)}, not {written(repeat)}"
            )
        return cls(name=name, repeat=repeat, steps=validate_steps(data.get("steps"), where))

    # -- persistence -----------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> Macro:
        macro = from_file(path, lambda raw: cls.from_json(raw, path.name))
        macro.path = path
        return macro

    def target(self, directory: Path | None) -> Path:
        """The file its name calls for."""
        wanted = directory or (self.path.parent if self.path else macros_dir())
        return wanted / file_name(self.name)

    def save(self, directory: Path | None = None, replace: Path | None = None) -> Path:
        """Validate, so a front end cannot write what the loader would refuse,
        then write atomically to the file the name calls for.

        A renamed macro moves there and its old file goes, so no file's name and
        contents disagree. Neither a move nor a new macro writes over an existing
        file (another macro's, or one the library could not read), as in
        `GameProfile.save`, unless it is `replace`, the file of the macro the
        user agreed to lose.
        """
        # Before the steps, or a blank name is reported under its hashed slug.
        if not self.name.strip():
            raise ConfigError("name: a macro needs a name")
        self.steps = validate_steps(self.steps, slug(self.name))
        if self.repeat not in REPEAT_MODES:
            raise ConfigError(f"repeat: must be {word_list(REPEAT_MODES)}, "
                              f"not {written(self.repeat)}")
        refuse_name(self.name, "name")
        target = self.target(directory)
        # Not following a link: one to nothing or a loop is a file the library
        # lists as unreadable.
        if (self.path != target and target.exists(follow_symlinks=False)
                and target != replace):
            refuse_taken(target, Macro.load, "macro")
        target.parent.mkdir(parents=True, exist_ok=True)
        write_json(target, self.to_json(), ".macro-")
        if self.path is not None and self.path != target:
            self.path.unlink(missing_ok=True)
            sync_removal(self.path)
        self.path = target
        return target


class MacroLibrary:
    """The set of macros on disk, read as `profiles.Library` reads the game profiles,
    by `library_files.read_folder`: a folder it cannot list is one skipped entry, and of two
    files of one name it reads the one a save writes, or else the first by file name."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or macros_dir()
        self._skipped = SkipWarnings("macro")
        #: (file name, why) for each file the last `all()` left out, for the window
        #: to show: those it could not read and those another file of the same name
        #: is read in place of. A directory it could not list is one entry, under
        #: its own name.
        self.skipped: list[tuple[str, str]] = []
        #: Why the last `all()` could not list the directory, or None, as library_files.Folder
        #: has it.
        self.unlisted_reason: str | None = None
        #: file_stamp of each file at its first read or last write here; `save`
        #: and `delete` refuse a file changed since. Every use reads the files,
        #: so a later read keeps the stamp: only `reread` takes a hand edit as read.
        self.stamps: dict[Path, tuple[int, int, int, int] | None] = {}

    def names(self) -> list[str]:
        return [m.name for m in self.all()]

    def all(self) -> list[Macro]:
        def load(path: Path) -> Macro:
            # Before the read, so an edit landing between the two is not taken as read.
            stamp = file_stamp(path)
            macro = Macro.load(path)
            self.stamps.setdefault(path, stamp)
            return macro

        folder = read_folder(self.directory, load, self._skipped)
        self.skipped, self.unlisted_reason = folder.skipped, folder.unlisted_reason
        return folder.kept

    def get(self, name: str) -> Macro | None:
        return by_name(self.all(), name)

    def delete(self, name: str) -> bool:
        """Remove a macro's file; raise FileChanged if it changed since read or written here."""
        macro = self.get(name)
        if macro is None:
            return False
        # A path: `get` returns a macro Macro.load read, which set it.
        path = cast(Path, macro.path)
        self.check_unchanged(path)
        path.unlink(missing_ok=True)
        self.stamps.pop(path, None)
        sync_removal(path)
        return True

    def save(self, macro: Macro, replace: Macro | None = None) -> Path:
        """Write `macro` (Macro.save), then remove the file of `replace`, the
        macro the user agreed to lose. Nothing is written when a file it would
        write over or remove changed on disk since read or written here."""
        was, gone = macro.path, replace.path if replace is not None else None
        self.check_unchanged(was, macro.target(self.directory), gone)
        path = macro.save(self.directory, replace=gone)
        if gone is not None and gone != path:
            gone.unlink(missing_ok=True)
            sync_removal(gone)
        for old in (was, gone):
            if old is not None and old != path:
                self.stamps.pop(old, None)
        self.stamps[path] = file_stamp(path)
        return path

    def check_unchanged(self, *paths: Path | None) -> None:
        """Raise FileChanged for the first of `paths` changed since read or written here."""
        changed = self.changed()
        for path in paths:
            if path in changed:
                raise changed_on_disk(path, save=False)

    def changed(self) -> list[Path]:
        """The files read or written here that changed on disk since, for
        `status`. Not a removed one: it holds no edit to keep, so a save writes
        it again (the owner's decision of 2026-09-26)."""
        return [path for path, stamp in self.stamps.items()
                if file_stamp(path) not in (stamp, None)]

    def reread(self) -> None:
        """Read every file again as if for the first time (`reload`), which
        takes a hand edit as read."""
        self.stamps.clear()
        self.all()


# -- running one -----------------------------------------------------------

class MacroRunner:
    """One macro at a time. The last press wins.

    A delay sets `_due`, which `deadline()` hands to `next_wakeup`. Teardown and
    shutdown call `cancel()`, so a macro cannot outlive its devices. The virtual
    devices count each holder of a code (`uinput._UinputDevice.key`), so a key
    the macro lets go of stays down while a G key or a mouse button still holds
    it. A step whose virtual device is missing is logged at debug and skipped.
    A step a device refuses is warned of once a run, and the same refusal on a
    later pass at debug.
    """

    #: Which device a hold belongs to, in the `(device, code)` pairs below.
    KEYBOARD, POINTER = "keyboard", "pointer"

    def __init__(self, devices: Callable[[], tuple[VirtualKeyboard | None, VirtualMouse | None]]
                 ) -> None:
        #: Returns the virtual keyboard and mouse, either possibly None. Called on every
        #: step, so the runner never keeps a device the daemon replaced or dropped.
        self._devices = devices
        self.macro: Macro | None = None
        #: The key that started it, the only one whose release counts: a G key,
        #: `mouse:` and a button name, or PLAY_KEY.
        self.key: str | None = None
        self.held = False
        #: The step to run next.
        self.index = 0
        #: When that step may run.
        self._due = 0.0
        #: When the current cycle started, for the repeat floor.
        self._cycle_start = 0.0
        #: A cycle has ended and the next is waiting on the floor: whether it
        #: runs is decided when it is due, not when the last one ended.
        self._next_cycle = False
        #: (device, code) pressed and not released, oldest first.
        self._held: list[tuple[str, int]] = []
        #: The (step number, error) pairs this run has warned of: a repeating macro
        #: that fails the same way each pass says so once, then at debug.
        self._warned: set[tuple[int, str]] = set()
        #: When the first step is due, for `starts_in`: later than the start
        #: only for a macro played with a delay.
        self._starts_at = 0.0

    @property
    def holding(self) -> tuple[tuple[str, int], ...]:
        """What the macro has pressed and not released, oldest first."""
        return tuple(self._held)

    def start(self, macro: Macro, key: str, now: float) -> None:
        """Begin a macro under the key that was pressed; whatever ran stops first.

        `now` is the caller's clock, the one it passes `tick`. A `toggle`
        macro's second press never gets here: `Bindings.run_macro` stops it
        first, matching by slug, before it reads the library.
        """
        self._begin(macro, key, held=True, at=now)

    def play(self, macro: Macro, now: float, delay: float = 0.0) -> None:
        """Begin a macro no key started (Play, `macro-run`), `delay` seconds from `now`.

        With no key down, a `while_held` macro runs one pass and a `toggle` one
        runs until `cancel()`. A second play restarts it: Stop is what stops it.
        """
        self._begin(macro, PLAY_KEY, held=False, at=now + delay)

    def _begin(self, macro: Macro, key: str, held: bool, at: float) -> None:
        if self.macro is not None:
            log.debug("macro %r canceled by %r", self.macro.name, macro.name)
        self.cancel()
        self.macro, self.key, self.held = macro, key, held
        self._due = self._cycle_start = self._starts_at = at

    def starts_in(self, now: float) -> float | None:
        """Seconds until a played macro's first step while its delay counts
        down, or None once it has begun or when nothing runs."""
        if self.macro is None or now >= self._starts_at:
            return None
        return self._starts_at - now

    def release(self, key: str) -> None:
        """The key came up: a `while_held` macro stops after this cycle. A `toggle` macro
        goes on, since `_repeating` does not ask whether its key is down."""
        # Nothing running has no key, so no key's release matches.
        if key == self.key:
            self.held = False

    def cancel(self) -> None:
        """Stop and release everything the macro holds. Every stop goes through here."""
        self.release_held()
        self.macro = self.key = None
        self.held = False
        self.index = 0
        self._next_cycle = False
        self._warned.clear()

    def release_held(self) -> None:
        """Let go of every outstanding press, newest first.

        A combination goes down modifiers first, and a desktop that sees ctrl
        come up before its key can act on the key alone. A failed write is
        logged and the hold dropped: its device is gone, and keeping it would
        leak it into the next macro.
        """
        keyboard, mouse = self._devices()
        for device, code in reversed(self._held):
            target = mouse if device == self.POINTER else keyboard
            if target is None:
                continue
            try:
                self._write(device, code, False, target)
            except UinputError as exc:
                log.warning("could not release %s code %d: %s", device, code, reason(exc))
        self._held = []

    def deadline(self) -> float | None:
        """When the loop should come back, or None while nothing is running."""
        return None if self.macro is None else self._due

    def tick(self, now: float) -> None:
        """Run whatever is due, never blocking.

        At most one cycle boundary per call, so zero-delay steps still give the
        loop a turn between cycles; the repeat floor runs from one cycle start
        to the next.
        """
        if self.macro is None:
            return
        steps = self.macro.steps
        while now >= self._due:
            ended = self.index >= len(steps)
            # Asked again when the next cycle is due: a key that came up
            # between cycles stops it there, so a 20 ms tap types once.
            if (ended or self._next_cycle) and not self._repeating():
                log.debug("macro %r finished", self.macro.name)
                self.cancel()
                return
            self._next_cycle = False
            if ended:
                self.index = 0
                self._due = max(now, self._cycle_start + MIN_CYCLE_MS / 1000)
                self._next_cycle = True
                return
            if self.index == 0:
                # A pass starts when its first step runs, which a late loop makes
                # later than it was due: the floor counts from there.
                self._cycle_start = now
            step, index = steps[self.index], self.index
            self.index += 1
            if step["type"] == DELAY:
                self._due = now + step["ms"] / 1000
            else:
                self._emit(step, index)

    def _repeating(self) -> bool:
        """Whether another cycle follows the one that just ended."""
        # A macro: `tick` asks this of the one it runs.
        repeat = cast(Macro, self.macro).repeat
        if repeat == TOGGLE:
            return True         # only a second press or a cancel stops it
        return repeat == WHILE_HELD and self.held

    # -- emitting --------------------------------------------------------

    def _emit(self, step: dict, index: int) -> None:
        # A macro: `tick` emits a step of the one it runs.
        macro = cast(Macro, self.macro)
        name, number = macro.name, index + 1     # as the Macros tab numbers its rows
        kind = step["type"]
        keyboard, mouse = self._devices()
        try:
            if kind in CLICK_STEPS:
                if mouse is None:
                    log.debug("macro %r step %d: no virtual mouse", name, number)
                    return
                code = CLICK_BUTTONS[step["button"]]
                if kind == CLICK:
                    mouse.button(code, True)
                    mouse.button(code, False)
                elif kind == CLICK_DOWN:
                    self._press(self.POINTER, code, mouse)
                else:
                    self._let_go(self.POINTER, code, mouse)
                return
            if keyboard is None:
                log.debug("macro %r step %d: no virtual keyboard", name, number)
                return
            if kind == KEY:
                # A tap, which leaves alone what the macro or anyone else holds.
                keyboard.send(step["value"])
            elif kind in (KEY_DOWN, KEY_UP):
                before, last = parse_binding(step["value"])
                codes = [*before, last]
                if kind == KEY_DOWN:
                    for code in codes:
                        self._press(self.KEYBOARD, code, keyboard)
                else:
                    for code in reversed(codes):
                        self._let_go(self.KEYBOARD, code, keyboard)
            else:
                self._type(keyboard, step["value"])
        except UinputError as exc:
            warned = (number, str(exc))
            log.log(logging.DEBUG if warned in self._warned else logging.WARNING,
                    "macro %r step %d: %s", name, number, exc)
            self._warned.add(warned)

    def _press(self, device: str, code: int, target: VirtualKeyboard | VirtualMouse) -> None:
        """Press one code, unless the macro already holds it.

        So a `key_down` in a repeating macro presses once, not once a pass, and
        the macro stays one holder in the device's count, with one release each.
        """
        if (device, code) in self._held:
            return
        self._write(device, code, True, target)
        self._held.append((device, code))

    def _let_go(self, device: str, code: int, target: VirtualKeyboard | VirtualMouse) -> None:
        """Release one code, unless the macro does not hold it: a code another holder
        keeps down is left. Named around `release`."""
        if (device, code) not in self._held:
            return
        self._write(device, code, False, target)
        self._held.remove((device, code))

    @staticmethod
    def _write(device: str, code: int, pressed: bool,
               target: VirtualKeyboard | VirtualMouse) -> None:
        if device == MacroRunner.POINTER:
            # The mouse: `_emit` and `release_held` pair POINTER with it.
            cast(VirtualMouse, target).button(code, pressed)
        else:
            target.key(code, pressed)

    def _type(self, keyboard: VirtualKeyboard, text: str) -> None:
        """One tap per character, through the virtual keyboard's own `tap`,
        which leaves a held shift down rather than letting it up after the
        first capital."""
        for char in text:
            shift, code = TYPING[char]
            keyboard.tap([_SHIFT_CODE] if shift else [], code)


# -- recording one ---------------------------------------------------------

#: Toolkit button numbers to click steps. GDK numbers them as X11 does: 1, 2, 3
#: left, middle, right; 8, 9 back, forward. Other buttons and wheel tilts are
#: dropped.
BUTTON_NUMBERS: dict[int, str] = {
    1: "left", 2: "middle", 3: "right", 8: "side", 9: "extra",
}

# RECORD_ROUND_MS: gaps round to the nearest 10 ms, half up, the editor's delay
# increment. The runner reaches a step when the loop next wakes, so finer
# precision would not replay anyway.
# RECORD_TAP_MS: a press and release under 40 ms apart, rounded, with nothing
# between them, are one tap.
RECORD_ROUND_MS = 10
RECORD_TAP_MS = 40


@dataclass
class _Hold:
    """One press a recording has not seen the release of yet."""

    #: (device, code or button name). What a release is matched against.
    identity: tuple[str, object]
    #: The step the press went in as, still in `_steps` and still editable:
    #: a release inside the tap window rewrites it as a tap.
    step: dict
    #: The modifiers of a key's spec whose own `key_up` went in while it was
    #: held (`_commit`), left out of its release.
    gone: list[str] = field(default_factory=list)


class MacroRecorder:
    """Timed key presses and clicks, turned into steps.

    No toolkit and no device, so a recording is testable without a display:
    the front end feeds it events with monotonic timestamps in seconds, shows
    `steps`, and stops when `full` goes true. The gap before an action becomes
    a delay step; nothing trails the last action.

    A press goes in as `key_down` or `click_down` at once. Its release rewrites
    it as a tap inside the tap window with nothing recorded between, or goes
    in as `key_up` or `click_up`. A press of something already held is
    auto-repeat and is dropped, as is a release of something never pressed
    here.

    A lone modifier (`modifier`) waits until its purpose is known: a key
    pressed meanwhile takes it into its combination, `ctrl+a`; anything else
    recorded first puts its press in at the time it went down. A taken
    modifier that comes up before its key waits too, and one still down when
    the key comes up waits again, so Shift, A, a click records `key shift+a`
    and a shift-click. The owner's decision of 2026-09-25: a recording replays
    what was done, and a harmless extra step is easy to delete.

    An action that does not fit under the cap is refused and sets `full`; room
    for the release of everything held is kept back, so `finish()` can always
    balance the recording. A modifier still waiting at Stop is left out when it
    does not fit, or when `_requeue` put it back (`finish`).
    """

    #: Which device a hold belongs to. Only ever compared, never emitted.
    KEY_DEVICE, BUTTON_DEVICE = "key", "button"

    def __init__(self, max_steps: int = MAX_STEPS) -> None:
        self.max_steps = max_steps
        #: True when no room is left, so the caller stops recording and says why.
        self.full = False
        self._steps: list[dict] = []
        #: When the last accepted action happened, for the next gap.
        self._last: float | None = None
        #: Presses still waiting for their release, oldest first.
        self._held: list[_Hold] = []
        #: Modifiers down on their own and not recorded yet, oldest first,
        #: as (when, spec, code): `modifier`.
        self._waiting: list[tuple[float, str, int]] = []
        #: Modifiers a key held took that came up before it and are not
        #: recorded yet, as (when, kind): `modifier`.
        self._let_go: list[tuple[float, str]] = []
        #: The modifier keys down, as code to spec, from `modifier`: a
        #: Shift let go of with the other Shift still down has not come up.
        self._down: dict[int, str] = {}
        #: The codes `_requeue` put back in `_waiting`, which their own release
        #: drops.
        self._requeued: set[int] = set()

    def __len__(self) -> int:
        return len(self._steps)

    @property
    def steps(self) -> list[dict]:
        """A copy: a release can still rewrite a press's step in place, as a tap."""
        return [dict(step) for step in self._steps]

    def key(self, when: float, spec: str, pressed: bool = True) -> bool:
        """A key going down or coming up, modifiers folded into the spec.

        A spec this kernel cannot send is refused, so a recording always saves.
        The hold is matched on the key's own code, since a release carries only
        the modifiers still down: ctrl+c with ctrl let go first comes up as `c`.
        The step's spec leaves out a modifier held as its own step (`modifier`),
        which its own `key_up` lets go of. The `key_up` carries the pressed
        spec, less a taken modifier whose own `key_up` went in first (`_commit`)
        and one another held key still carries, since the runner holds a code
        once per macro: Shift held over W and then D records `key_up w`, and
        Shift comes up with the last `key_up` that names it.
        """
        try:
            before, code = parse_binding(spec)
        except UinputError:
            return False
        if pressed:
            parts = binding_parts(spec)
            spec = "+".join([part for part, down in zip(parts[:-1], before, strict=True)
                             if self._find((self.KEY_DEVICE, down)) is None] + parts[-1:])
        return self._action(when, (self.KEY_DEVICE, code), pressed, {"value": spec})

    def modifier(self, when: float, spec: str, pressed: bool = True) -> bool:
        """A modifier key on its own going down or coming up, spelled as bound
        alone: `shift`, `rctrl`.

        Its press waits (`_waiting`). A key pressed meanwhile takes it into the
        spec the caller builds from the toolkit's state, and it is not recorded
        alone. Anything recorded first, a click or a release, puts its press in
        at the time it went down, and from then it records as any key does.

        A release records nothing itself when no press of it waits or is held
        (a key took it, or it was down before the recording began), when a
        held key's step presses its code, whose `key_up` lets go of it, or
        when it was put back to wait by `_requeue`. When the last modifier of a kind
        comes up while a held key still presses that kind, the release waits
        (`_let_go`) for that key's release, or for anything recorded first to
        put its `key_up` in at the time it came up. So a left Ctrl a key took
        and let go of while the right Ctrl is down comes up with the right one.
        """
        _, code = parse_binding(spec)
        identity = (self.KEY_DEVICE, code)
        waiting = any(held == code for _, _, held in self._waiting)
        if pressed:
            self._down[code] = spec
            if not waiting and self._find(identity) is None:
                self._waiting.append((when, spec, code))
            return False            # nothing recorded yet, or auto-repeat
        self._down.pop(code, None)
        if code in self._requeued:
            self._requeued.discard(code)
            self._waiting = [entry for entry in self._waiting if entry[2] != code]
            return False
        recorded = False
        if waiting or self._find(identity) is not None:
            if not self._commit():
                return False
            # Held by now: `_commit` pressed it if it was waiting.
            recorded = self._release(when, cast(_Hold, self._find(identity)))
        kind = MODIFIER_KINDS[code]
        if (self._takers(kind)
                and not any(MODIFIER_KINDS.get(down) == kind for down in self._down)):
            self._let_go.append((when, kind))
        return recorded

    def keyboard_left(self, when: float) -> bool:
        """The caller's window lost the keyboard (to another window, or to GNOME
        Shell keeping a lone Super's release for its overview), so releases go
        elsewhere. Waiting modifiers are dropped, and each held key comes up now
        with the modifiers it took, or its next press would read as auto-repeat.
        A held button stays: its release comes to the window that took its
        press. True when a step went in."""
        self._waiting, self._let_go = [], []
        self._requeued.clear()
        self._down.clear()
        added = False
        for hold in [h for h in self._held if h.identity[0] == self.KEY_DEVICE]:
            added = self._release(when, hold) or added
        return added

    @property
    def modifiers_down(self) -> list[int]:
        """The modifier keys down, by code, as `modifier` saw them. The caller
        spells a key's combination with them: the toolkit's state names none
        for Right Alt as AltGr."""
        return list(self._down)

    def released_unseen(self, when: float, codes: Iterable[int]) -> bool:
        """Modifier keys the caller found up without seeing them come up: GNOME
        Shell keeps a lone Super's release for its overview. A press still
        waiting is dropped, as `keyboard_left` drops it. One recorded, as held
        or in a held key's step, comes up now as `modifier` records a release:
        still counted as down, it would be pressed in the replay under what
        was done without it. True when a step went in."""
        gone = {code: self._down.pop(code) for code in codes if code in self._down}
        self._waiting = [entry for entry in self._waiting if entry[2] not in gone]
        added = False
        for spec in gone.values():
            added = self.modifier(when, spec, pressed=False) or added
        return added

    def click(self, when: float, button: int, pressed: bool = True) -> bool:
        """A mouse button, by toolkit number."""
        name = BUTTON_NUMBERS.get(button)
        if name not in CLICK_BUTTONS:
            return False
        return self._action(when, (self.BUTTON_DEVICE, name), pressed, {"button": name})

    def finish(self) -> list[dict]:
        """The steps, with a release for anything still held.

        Left held, a key would stay down through every pass of the replay. The
        releases go in reverse press order with no delay: Stop says nothing
        about when the key would have come up. A modifier still waiting goes in
        first, as held, when its delay, press and release fit under the cap,
        and is left out otherwise; one put back by `_requeue` is dropped, as its
        own release would drop it.
        """
        self._waiting = [entry for entry in self._waiting if entry[2] not in self._requeued]
        self._commit()
        for hold in reversed(self._held):
            kind = KEY_UP if hold.identity[0] == self.KEY_DEVICE else CLICK_UP
            self._steps.append({"type": kind, **self._fields(hold)})
        self._held = []
        self._recount()
        return self.steps

    def _action(self, when: float, identity: tuple[str, object], pressed: bool,
                fields: dict) -> bool:
        if pressed:
            if self._find(identity) is not None:
                return False        # auto-repeat: the key never came up
            if identity[0] == self.KEY_DEVICE:
                # The modifiers waiting are this key's combination.
                self._waiting = []
            if not self._commit():
                return False
            return self._press(when, identity, fields)
        hold = self._find(identity)
        if hold is None:
            return False            # nothing here pressed it, so nothing to record
        # A modifier only this key took comes up with this key's release.
        self._let_go = [(at, kind) for at, kind in self._let_go
                        if any(taker is not hold for taker in self._takers(kind))]
        if not self._commit():
            return False
        return self._release(when, hold)

    def _commit(self) -> bool:
        """Put in, in time order, each waiting modifier's press at the time it
        went down, and each `key_up` of a taken modifier that came up before
        its key, at the time it came up. Every recorded action commits these
        first, so none lands before the last action. False once one does not
        fit."""
        waiting, self._waiting = self._waiting, []
        let_go, self._let_go = self._let_go, []
        self._requeued.clear()
        events = sorted([(when, False, spec, code) for when, spec, code in waiting]
                        + [(when, True, kind, 0) for when, kind in let_go],
                        key=lambda event: event[:2])
        for when, up, name, code in events:
            if not up:
                if not self._press(when, (self.KEY_DEVICE, code), {"value": name}):
                    return False
                continue
            takers = self._takers(name)
            if not takers:
                continue
            if not self._add(when, {"type": KEY_UP, "value": self._part(takers[0], name)}):
                return False
            for hold in takers:
                hold.gone.append(self._part(hold, name))
        return True

    @staticmethod
    def _modifiers(hold: _Hold) -> list[str]:
        """The modifiers a key hold's spec still presses: every part but
        the last, less those whose own `key_up` went in."""
        return [part for part in binding_parts(hold.step["value"])[:-1]
                if part not in hold.gone]

    def _part(self, hold: _Hold, kind: str) -> str:
        """How `hold`'s spec spells its modifier of `kind`."""
        return next(part for part in self._modifiers(hold)
                    if MODIFIER_KINDS.get(parse_binding(part)[1]) == kind)

    def _takers(self, kind: str) -> list[_Hold]:
        """The keys held whose spec still presses a modifier of `kind`."""
        return [hold for hold in self._held if hold.identity[0] == self.KEY_DEVICE
                and any(MODIFIER_KINDS.get(parse_binding(part)[1]) == kind
                        for part in self._modifiers(hold))]

    def _find(self, identity: tuple[str, object]) -> _Hold | None:
        return next((hold for hold in self._held if hold.identity == identity), None)

    @staticmethod
    def _fields(hold: _Hold) -> dict:
        """A hold's step without its type and without the modifiers whose
        own `key_up` went in, to build the release from."""
        fields = {k: v for k, v in hold.step.items() if k != "type"}
        if hold.gone:
            fields["value"] = "+".join(part for part in binding_parts(fields["value"])
                                       if part not in hold.gone)
        return fields

    def _release_fields(self, hold: _Hold) -> dict | None:
        """The fields of the release of `hold`, which is out of `_held`: a
        key's spec less each code another key held still carries, or None
        when that leaves nothing, a modifier alone whose code a key took,
        spelled without side (`ctrl`), for the other key of its kind. The
        runner presses a code once however many steps name it, so that code
        would come up under the other key."""
        fields = self._fields(hold)
        if hold.identity[0] != self.KEY_DEVICE:
            return fields
        carried: set[int] = set()
        for other in self._held:
            if other.identity[0] == self.KEY_DEVICE:
                before, last = parse_binding(self._fields(other)["value"])
                carried.update((*before, last))
        before, last = parse_binding(fields["value"])
        kept = [part for part, code in zip(binding_parts(fields["value"]),
                                           (*before, last), strict=True)
                if code not in carried]
        if not kept:
            return None
        fields["value"] = "+".join(kept)
        return fields

    def _press(self, when: float, identity: tuple[str, object], fields: dict) -> bool:
        down = KEY_DOWN if identity[0] == self.KEY_DEVICE else CLICK_DOWN
        step = {"type": down, **fields}
        if not self._add(when, step, reserve=1):
            return False
        self._held.append(_Hold(identity, step))
        self._recount()
        return True

    def _release(self, when: float, hold: _Hold) -> bool:
        at = self._held.index(hold)
        del self._held[at]
        tap, up = (KEY, KEY_UP) if hold.identity[0] == self.KEY_DEVICE else (CLICK, CLICK_UP)
        if self._rounded(when) < RECORD_TAP_MS and self._steps[-1] is hold.step:
            # Down and up inside the tap window with nothing in between: one tap step.
            hold.step["type"] = tap
            self._last = when
            self._recount()
        elif (fields := self._release_fields(hold)) is None:
            # Its code is a held key's too, whose key_up lets go of it. The release
            # it no longer owes is room again.
            self._recount()
            return False
        elif not self._add(when, {"type": up, **fields}):
            # No room for the delay in front of it: the room kept back is for
            # the key_up alone. Keep it held; finish() releases it in that room.
            self._held.insert(at, hold)
            return False
        self._requeue(when, hold)
        return True

    def _requeue(self, when: float, hold: _Hold) -> None:
        """After a key's release, put each modifier it took that is still down,
        and that no held key presses, back to wait from `when`. The key's step
        lets go of it in the replay, so a click made with it held would go out
        without it. A modifier's own release lets go of its kind the same way:
        the other key of that kind, still down, waits again."""
        if hold.identity[0] != self.KEY_DEVICE:
            return
        kinds = [MODIFIER_KINDS.get(parse_binding(part)[1]) for part in self._modifiers(hold)]
        # A key's hold, by the check above, is known by its code.
        for kind in [*kinds, MODIFIER_KINDS.get(cast(int, hold.identity[1]))]:
            if kind is None or self._takers(kind):
                continue
            for code, spec in self._down.items():
                if (MODIFIER_KINDS.get(code) == kind
                        and self._find((self.KEY_DEVICE, code)) is None
                        and all(waiting != code for _, _, waiting in self._waiting)):
                    self._waiting.append((when, spec, code))
                    self._requeued.add(code)

    def _rounded(self, when: float) -> int:
        """The milliseconds since the last action, to RECORD_ROUND_MS, half up.
        Negative for a time before it, which `_gap` and `_release` read as they
        read 0."""
        # A time: `_gap` checks it, and `_add` set it before `_release` has a hold to release.
        elapsed = (when - cast(float, self._last)) * 1000.0
        return int((elapsed + RECORD_ROUND_MS / 2) // RECORD_ROUND_MS) * RECORD_ROUND_MS

    def _gap(self, when: float) -> dict | None:
        """The delay step in front of an action: none before the first, and
        never less than the standard delay after it."""
        if self._last is None:
            return None
        ms = max(self._rounded(when), STANDARD_DELAY_MS)
        return {"type": DELAY, "ms": min(ms, MAX_DELAY_MS)}

    def _recount(self) -> None:
        """No room left once the steps plus the releases owed fill the cap."""
        self.full = len(self._steps) + len(self._held) >= self.max_steps

    def _add(self, when: float, step: dict, reserve: int = 0) -> bool:
        """Append the gap and the action together, or neither, so a recording
        never ends on a pause. `reserve` is the room this step's own release
        needs: a press goes in only when its release can still follow."""
        if self.full:
            return False
        gap = self._gap(when)
        batch = [gap, step] if gap is not None else [step]
        if len(self._steps) + len(batch) + len(self._held) + reserve > self.max_steps:
            self.full = True
            return False
        self._steps.extend(batch)
        self._last = when
        self._recount()
        return True


#: Row title per step type; short, since a row shows title and value side by side.
STEP_TITLES = {
    KEY: "Key", KEY_DOWN: "Key down", KEY_UP: "Key up",
    TEXT: "Text", DELAY: "Delay",
    CLICK: "Click", CLICK_DOWN: "Click down", CLICK_UP: "Click up",
}


def step_summary(step: dict) -> tuple[str, str]:
    """A step as a row title and subtitle, shared by every list of steps. The step is one
    `validate_step` would return: it has its type and that type's field, a key's or a
    text's `value` as text and a click's `button` one of CLICK_BUTTONS. A key's spec is
    shown on one line (shown_spec)."""
    kind = step["type"]
    if kind in KEY_STEPS:
        return STEP_TITLES[kind], shown_spec(step["value"])
    if kind == TEXT:
        # Enter and Tab as signs: raw, a last Enter is lost from a one-line preview and
        # an inner one reads as the next step. TYPING types neither sign.
        return STEP_TITLES[kind], step["value"].replace("\n", "⏎").replace("\t", "⇥")
    if kind == DELAY:
        return STEP_TITLES[DELAY], f"{step['ms']} ms"
    return STEP_TITLES[kind], CLICK_LABELS[step["button"]]


#: The Macros tab's warnings under the steps, by a step's number and its keys as its row shows them.
RELEASES_NOTHING = "Step {step} releases {keys}, which no earlier step left down."
HELD_TO_THE_END = ("Step {step} holds {keys} down and no later step releases it; it comes "
                   "up when the macro ends.")


def unbalanced(steps: list[dict], repeat: str) -> list[str]:
    """The editor's warnings for each hold that no later step releases, and each release of
    nothing held, as MacroRunner plays the steps.

    Both are legal and the runner lets go of everything when a macro ends, so the editor
    warns and never refuses. Each key is matched alone, as the runner holds them: `ctrl+c` down
    then `c` up leaves ctrl down. A key that does not parse is left to the save to refuse.

    A repeating macro starts each pass with what the last left held, so a hold that some
    step releases comes up in the next pass, and a release lets go of a key the pass ends
    holding if no earlier release in the pass names it: Key up `a`, Delay, Key down `a`,
    Delay lifts `a` once a pass.
    """
    held: dict[tuple[str, object], tuple[int, str]] = {}
    # Every key a release has named so far.
    named: set[tuple[str, object]] = set()
    # Each release that let go of nothing, with its keys no earlier release named.
    orphans: list[tuple[int, str, list[tuple[str, object]]]] = []
    for index, step in enumerate(steps):
        kind = step.get("type")
        keys: list[tuple[tuple[str, object], str]]
        if kind in (KEY_DOWN, KEY_UP):
            spec = str(step.get("value", ""))
            try:
                before, last = parse_binding(spec)
            except UinputError:
                continue
            keys = list(zip([("key", code) for code in (*before, last)], binding_parts(spec)))
        elif kind in (CLICK_DOWN, CLICK_UP):
            keys = [(("button", step["button"]), step_summary(step)[1])]
        else:
            continue
        # As the step's row shows it: the warnings share a label, one a line.
        name = step_summary(step)[1]
        if kind in (KEY_DOWN, CLICK_DOWN):
            for identity, part in keys:
                held.setdefault(identity, (index, part))
            continue
        released = [identity for identity, _part in keys if identity in held]
        for identity in released:
            del held[identity]
        if not released:
            orphans.append((index, name, [identity for identity, _part in keys
                                          if identity not in named]))
        named.update(identity for identity, _part in keys)
    if repeat in (WHILE_HELD, TOGGLE):
        orphans = [orphan for orphan in orphans
                   if not any(identity in held for identity in orphan[2])]
        held = {identity: where for identity, where in held.items() if identity not in named}
    left: dict[int, list[str]] = {}
    for index, part in held.values():
        left.setdefault(index, []).append(part)
    return ([RELEASES_NOTHING.format(step=index + 1, keys=name)
             for index, name, _keys in orphans]
            + [HELD_TO_THE_END.format(step=index + 1, keys="+".join(left[index]))
               for index in sorted(left)])


def repeat_summary(repeat: str) -> str:
    """One line saying what a repeat mode does, for a list of macros."""
    return REPEAT_SUMMARIES[repeat]


def macro_summary(count: int, repeat: str) -> str:
    """A macro of `count` steps in a few words, for a list of macros: "3 steps, repeats
    while held"."""
    return f"{counted(count, 'step')}, {repeat_summary(repeat)}"


def describe_macros(macros: Iterable[Macro]) -> list[dict]:
    """The library as a front end wants to list it."""
    return [{"name": m.name, "repeat": m.repeat, "steps": len(m.steps)} for m in macros]
