"""Bindings and macros: what a G key, an M key, MR, a mouse button or a
headset's G key does, the keys they hold, the balance's words they send Outputs, and the
macro runner."""

from __future__ import annotations

import copy
import logging
import socket
import time
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, NamedTuple, cast

from .. import outputs
from ..config import (
    BALANCE_ACTIONS,
    G_SHIFT,
    HEADSET_ACTIONS,
    HEADSET_DEFAULTS,
    MACRO_PREFIX,
    NOTHING,
    entry_under,
    expect,
    g_shift_refusal,
    keep_under,
    key_binding,
    macro_name,
    mouse_button_name,
    not_bindable,
    parse_mouse_binding,
)
from ..hidpp import DeviceGone, HidppFailure
from ..keyboard import (
    BASE_BINDING_TOO,
    MODEL_OF as KEYBOARDS,
    assignments_untested,
    bound_as,
    keystroke,
    needs_base_binding,
    untested_assignment_words,
)
from ..library_files import not_deleted, slug
from ..macros import (
    MAX_PLAY_DELAY,
    ONCE,
    PLAY_KEY,
    TOGGLE,
    WHILE_HELD,
    Macro,
    MacroLibrary,
    MacroRunner,
    describe_macros,
)
from ..mouse import key_name, model_ids, model_key
from ..profiles import layers_of
from ..sensor import MouseError
from ..settings_files import ConfigError
from ..uinput import POINTER_ACTIONS, UinputError, parse_binding
from ..wording import (
    MACRO_NEEDS_A_NAME,
    counted,
    escaped,
    number,
    quoted,
    reason,
    word_list,
    written,
)
from .shared import digest, mouse_model_asked, name_text, refusal, with_note

if TYPE_CHECKING:
    from . import Daemon
    from .keyboard import Keyboard
    from .mice.attached import AttachedMouse

log = logging.getLogger("gearbench")

#: Each DPI step action: its way through the stages, +1 up and -1 down, and whether it goes
#: from the last stage round to the first (Mouse.step_dpi's `wrap`).
DPI_STEPS = {"dpi-up": (1, False), "dpi-down": (-1, False), "dpi-cycle": (1, True)}
#: The word each balance action sends Outputs: one stop toward the game, one toward chat.
BALANCE_WORDS = dict(zip(BALANCE_ACTIONS, (outputs.Step(outputs.GAME_WORD),
                                           outputs.Step(outputs.CHAT_WORD)), strict=True))
#: The line for a balance word not sent for any cause but a full queue, by the key or
#: button, its binding and why (outputs.unsent, or the runtime folder's refusal,
#: outputs.SocketRefused): said once until a word goes through.
BALANCE_NOT_SENT = "%s bound to %s: %s; said once until a word is sent"
#: The refusal of a mouse bind with no `device` named while no mouse is attached and none is
#: selected (Bindings.mouse_model).
NO_MOUSE_YET = ("no mouse is attached and none is selected, and each mouse model keeps its "
                "own bindings: connect the mouse, or name its model with `--mouse`")


def mouse_holder(model: str, button: str) -> str:
    """What a mouse button's held keys and the macro it started are filed under: `mouse:`, the
    mouse model (mouse.model_key) and the button, so a button of one name on two models is
    held apart, as each is bound."""
    return f"mouse:{model}:{button}"


class ShiftHolder(NamedTuple):
    """One key or button holding G-Shift (Bindings._shifted_by): its name, MR, a G key's, a
    headset's included, or a mouse button's by mouse_holder, and the keyboard it is a key of,
    None for a mouse button or a headset's key."""

    name: str
    keyboard: Keyboard | None


def shifts(holder: Keyboard | None, device: Keyboard | None) -> bool:
    """Whether G-Shift held on `holder`, the keyboard whose MR or G key holds it, or None for a
    mouse button or a headset's key, shifts `device`: a keyboard's G keys, or None for every
    mouse's buttons and every headset's keys. A keyboard's holder shifts that keyboard, every
    mouse and every headset, never another keyboard, and any other holder every device (the
    owner's decisions 4 and 14 of 2026-10-28, and a headset as a mouse the owner's of that
    day)."""
    return holder is None or device is None or holder is device


class Bindings:
    """Turns key and button presses into keystrokes, mouse actions, macros and the balance's
    words."""

    def __init__(self, daemon: Daemon) -> None:
        self.d = daemon
        #: A mouse button by mouse_holder -> the binding its press dispatched to; the release
        #: follows this, not what is bound by then.
        self._mouse_held: dict[str, str] = {}
        #: The same -> the mouse whose press is held (on_mouse_button's `source`): a mouse
        #: going away lets go of its own holds alone (release_mouse_holds). Two mice of one
        #: model holding a button of one name at once share one hold, which the first release
        #: lets go of.
        self._mouse_owner: dict[str, AttachedMouse] = {}
        #: What each held binding put down on the virtual keyboard, by holder
        #: (a G key, or a mouse button by mouse_holder), let go of at its release.
        self._keys_held: dict[str, tuple[int, ...]] = {}
        #: Each key holding G-Shift, in the order pressed: what it shifts (shifts) resolves on
        #: the shift layer (shifted_on).
        self._shifted_by: list[ShiftHolder] = []
        #: The keyboard each G key held was pressed on, by the name its binding is under: a
        #: keyboard going lets go of its own alone (keyboard_gone), as a mouse does
        #: (_mouse_owner).
        self._key_owner: dict[str, Keyboard] = {}
        #: A balance word was not sent and that was said, until one is sent (send_balance).
        self._balance_unsent_said = False
        self.macros = MacroLibrary()
        #: The one macro that can be running. It looks the virtual devices up
        #: at every step, so it never holds one the daemon replaced.
        self.runner = MacroRunner(devices=lambda: (self.d.kbd, self.d.ptr))

    def commands(self) -> dict[str, Callable[[dict], dict]]:
        return {
            "bind": self._cmd_bind,
            "mouse_bind": self._cmd_mouse_bind,
            "mouse_bind_defaults": self._cmd_mouse_bind_defaults,
            "macros": self._cmd_macros,
            "macro_save": self._cmd_macro_save,
            "macro_delete": self._cmd_macro_delete,
            "macro_run": self._cmd_macro_run,
            "macro_stop": self._cmd_macro_stop,
        }

    def deadlines(self) -> list[float]:
        """The running macro's next step."""
        due = self.runner.deadline()
        return [] if due is None else [due]

    def stop_mouse_macro(self, source: AttachedMouse) -> None:
        """Stop a macro a button of `source` started, where a press or release of that button
        could be lost (Buttons.drop_spy_state)."""
        macro = self.runner.macro
        if macro is not None and self.runner.source is source:
            log.info("macro %r stopped: a press or release of %s may be lost",
                     macro.name, self.runner.key)
            self.runner.cancel()

    def release_mouse_holds(self, source: AttachedMouse) -> None:
        """Let go of what the buttons of `source` hold, where their releases could be lost
        (Buttons.drop_spy_state): a back or forward left down would stay pressed on the
        virtual mouse, and a G-Shift held would stay held. A write that fails is logged and
        the rest go on."""
        for holder in [h for h, owner in self._mouse_owner.items() if owner is source]:
            del self._mouse_owner[holder]
            binding = self._mouse_held.pop(holder, None)
            if binding == G_SHIFT:
                self._hold_shift(ShiftHolder(holder, None), False)
            if binding in POINTER_ACTIONS:
                log.debug("%s was held; releasing it", holder)
                try:
                    self._pointer_button(POINTER_ACTIONS[binding], False)
                except UinputError as exc:
                    log.debug("could not let go of %s: %s", holder, exc)
            try:
                self.let_go_binding(holder)
            except UinputError as exc:
                log.debug("could not let go of %s: %s", holder, exc)

    def keyboard_gone(self, source: Keyboard, held_only: bool = False) -> None:
        """Let go of what the keyboard `source` holds, whose releases will not come: the
        G-Shift its MR or G keys hold, a macro a G key of its started (with `held_only`, only a
        while_held one) and the keys the G keys hold. Its MR LED is not written: it went, or
        is set up again."""
        self._shifted_by = [held for held in self._shifted_by if held.keyboard is not source]
        macro = self.runner.macro
        if (macro is not None and self.runner.source is source
                and (not held_only or macro.repeat == WHILE_HELD)):
            self.runner.cancel()
        self.let_go_of_keys(source)

    @property
    def shifted(self) -> bool:
        """Whether anything holds G-Shift."""
        return bool(self._shifted_by)

    def shifted_on(self, device: Keyboard | None) -> bool:
        """Whether the shift layer is held for the G keys of the keyboard `device`, or with None
        for each mouse's buttons and each headset's keys, by any key holding G-Shift
        (shifts). A keyboard's MR LED shows it for that keyboard, whatever holds it: MR or a G
        key of its own, a mouse button or a headset's key (the owner's decision 4 of
        2026-10-28, and "The MR light for every G-Shift holder" of that day)."""
        return any(shifts(held.keyboard, device) for held in self._shifted_by)

    def _lit_mr(self) -> list[Keyboard]:
        """The keyboards whose MR LED shows G-Shift held (shifted_on): each a keyboard's holder
        is a key of, then, while a mouse button or a headset's key holds it, each driven."""
        lit = [held.keyboard for held in self._shifted_by]
        if None in lit:
            lit += self.d.keyboards.driven()
        return [keyboard for keyboard in dict.fromkeys(lit) if keyboard is not None]

    def _hold_shift(self, holder: ShiftHolder, held: bool) -> None:
        """`holder`, a G key or a mouse button bound to G_SHIFT, holding G-Shift or letting it
        go, recorded before any MR LED it changes (shifted_on) is written, so a write that
        tears a keyboard down finds the holder in place."""
        lit = self._lit_mr()
        if held and holder not in self._shifted_by:
            self._shifted_by.append(holder)
        elif not held and holder in self._shifted_by:
            self._shifted_by.remove(holder)
        else:
            return
        log.debug("%s %s G-Shift", holder.name, "holds" if held else "lets go of")
        now = self._lit_mr()
        for keyboard in lit:
            if keyboard not in now:
                keyboard.show_mr(False)
        for keyboard in now:
            if keyboard not in lit:
                keyboard.show_mr(True)

    def let_go_away(self, why: str) -> None:
        """Let go of what the G keys, MR and mouse buttons hold, G-Shift and the MR LEDs that
        show it included, and forget it,
        so the later release lets go of nothing and a key still down when the
        session is back needs a new press. A `dpi-shift` is left to its release,
        which puts the DPI back. A write that fails is logged and the rest go on."""
        pointer = [(holder, binding) for holder, binding in self._mouse_held.items()
                   if binding in POINTER_ACTIONS]
        held = [*dict.fromkeys(holder.name for holder in self._shifted_by), *self._keys_held,
                *(holder for holder, _ in pointer)]
        if not held:
            return
        log.info("let go of what %s held: %s", word_list(held, "and"), why)
        shown = self._lit_mr()
        self._shifted_by = []
        for keyboard in shown:
            keyboard.show_mr(False, dark_too=True)
        for holder, binding in pointer:
            # Its release does nothing now, as a button bound to nothing does.
            self._mouse_held[holder] = NOTHING
            try:
                self._pointer_button(POINTER_ACTIONS[binding], False)
            except UinputError as exc:
                log.debug("could not let go of %s: %s", holder, exc)
        self.let_go_of_keys()

    def stop_macro_away(self, why: str) -> None:
        """Stop a macro running or counting down, whatever started it: a toggle macro
        would type on into the lock screen's password field."""
        macro = self.runner.macro
        if macro is None:
            return
        log.info("macro %r stopped: %s", macro.name, why)
        self.runner.cancel()


    def on_key(self, key: str, pressed: bool, source: Keyboard | None = None) -> None:
        """A G, M or MR key of the keyboard `source`, the leader's by default, going down or up.
        An M key switches every keyboard's M profile; MR holds G-Shift (_take_mr)."""
        keyboard = self.d.keyboard if source is None else source
        if key == "MR":
            self._take_mr(keyboard, pressed)
            return

        if key.startswith("M"):
            if pressed:
                index = int(key[1:])
                # Lit again while locked: each keyboard once, by the switch's own apply
                # when it switches, or here when it does not and the relight moved any
                # keyboard's level.
                relit = self.d.session.note_key_input(paint=False)
                if relit and not self.d.config.switches_to(index):
                    self.d.keyboards.apply_each(keyboard)
                else:
                    keyboard.switch_profile(index)
            return

        # Under its model's name for it from here on: the G915 TKL's G1 is its F1.
        product = keyboard.keyboard_product
        key = bound_as(product, key)
        if not pressed:
            # Before any lookup: the layer may have changed since the press.
            self._key_up(ShiftHolder(key, keyboard))
            return

        board = keyboard.dev
        self.d.session.note_key_input()
        if board is not None and keyboard.dev is None:
            # The relight tore the keyboard down, letting go of what the G keys hold:
            # this key's release will not come.
            return

        # A G915 TKL's F key with nothing bound types its own key, which HOST mode stops it
        # typing by itself.
        binding = (self.d.games.active_binding(key, self.shifted_on(keyboard))
                   or keystroke(product, key))
        # NOTHING, the G-Shift layer's binding to nothing, sends neither its base binding nor
        # its own key.
        if binding in (None, NOTHING):
            log.debug("%s down (%s)", key, binding or "unbound")
            return
        # Escaped: a key spec splits at any white space, so one from a file can hold a
        # newline, where the journal would begin a second entry.
        log.debug("%s down -> %s", key, escaped(binding))
        if self._took_alike(ShiftHolder(key, keyboard), binding, True, keyboard):
            return
        try:
            self.hold_binding(key, binding)
        except UinputError as exc:
            log.warning("%s bound to %r: %s", key, binding, exc)
        if key in self._keys_held:
            # A hold alone: an owner left by a press that held nothing would keep another
            # keyboard's later hold of the key from that keyboard's keyboard_gone.
            self._key_owner.setdefault(key, keyboard)

    def _took_alike(self, holder: ShiftHolder, binding: str, pressed: bool,
                    source: Keyboard | AttachedMouse | None) -> bool:
        """What a G key, a headset's G key and a mouse button do alike with G_SHIFT, a balance
        word (sent at the press alone) and a macro, at the press or release of `holder` that
        `source` made; whether `binding` was one of them."""
        if binding == G_SHIFT:
            self._hold_shift(holder, pressed)
        elif binding in BALANCE_WORDS:
            if pressed:
                self.send_balance(holder.name, binding)
        elif (name := macro_name(binding)) is not None:
            self.run_macro(name, holder.name, pressed, source)
        else:
            return False
        return True

    def _key_up(self, holder: ShiftHolder) -> None:
        """A G key's release, a keyboard's or a headset's: what its press held let go."""
        self._hold_shift(holder, False)
        self.runner.release(holder.name)
        self._key_owner.pop(holder.name, None)
        try:
            self.let_go_binding(holder.name)
        except UinputError as exc:
            log.warning("could not let go of %s: %s", holder.name, exc)

    def _take_mr(self, keyboard: Keyboard, pressed: bool) -> None:
        """MR going down or up on `keyboard`: G-Shift held while its MR is down, for what it
        shifts (shifts), and its MR LED (shifted_on)."""
        holder = ShiftHolder("MR", keyboard)
        if pressed and holder not in self._shifted_by:
            self._shifted_by.append(holder)
        elif not pressed and holder in self._shifted_by:
            self._shifted_by.remove(holder)
        if not (pressed and keyboard in self.d.session.note_key_input()):
            # A press that lit this board again leaves the LED to that apply.
            keyboard.show_mr(self.shifted_on(keyboard))

    def on_headset_key(self, key: str, pressed: bool) -> None:
        """A headset's G key going down or up, by the name it binds under
        (g432.HeadsetModel.g_keys), which names its model too, so what a key holds is held
        apart from another model's. The release lets go of what the press held, G-Shift
        included, and ends a while-held macro it started, as a keyboard's G key's does."""
        if not pressed:
            self._key_up(ShiftHolder(key, None))
            return
        binding = self.headset_binding(key, self.shifted_on(None))
        log.debug("%s down -> %s", key, escaped(binding))     # as on_key's
        if binding == NOTHING or self._took_alike(ShiftHolder(key, None), binding, True, None):
            return
        if binding in HEADSET_ACTIONS:
            self.d.g432.key_action(binding, key)
            return
        try:
            self.hold_binding(key, binding)
        except UinputError as exc:
            log.warning("%s bound to %r: %s", key, binding, exc)

    def headset_binding(self, key: str, shifted: bool = False) -> str:
        """What a headset's G key does: its binding on the layer in force, on the shift layer
        with `shifted`, which falls through to the base layer as a keyboard's does
        (config.layer_binding), or while it is unbound its default (HEADSET_DEFAULTS), else
        NOTHING."""
        return self.d.games.active_binding(key, shifted) or HEADSET_DEFAULTS.get(key, NOTHING)

    def let_go_headset(self, keys: tuple[str, ...], gone: bool = False) -> None:
        """Let go of what the headset's G keys `keys` hold, G-Shift included, whose releases
        will not come. With `gone`, a macro one of them started stops too, toggle or not, as a
        keyboard's does (keyboard_gone)."""
        if gone and self.runner.key in keys:
            self.runner.cancel()
        for key in keys:
            self.on_headset_key(key, False)

    def hold_binding(self, source: str, binding: str) -> None:
        """Put a key binding down, every key of the spec in order, until `source` (a G
        key, or a mouse button by mouse_holder) is let go of. Recorded against the source, so a
        layer change mid-hold still lets go of the right keys. Nothing while the session
        is not active. Raises UinputError for a bad spec, a write the virtual keyboard
        refused or a key it will not press under an Alt and SysRq chord."""
        if self.d.kbd is None or source in self._keys_held or self.refused_inactive(source):
            return
        before, last = parse_binding(binding)
        down: list[int] = []
        try:
            for code in (*before, last):
                self.d.kbd.key(code, True)
                down.append(code)
        finally:
            if down:
                self._keys_held[source] = tuple(down)

    def refused_inactive(self, source: str) -> bool:
        """Whether a press of `source` types nothing: the session is not active, and the
        virtual devices would type into the one that is. Said at debug only, since the
        presses are then the typing of whoever has the seat; Session.on_active said why at
        info. A lock alone refuses nothing here (Session.why_inactive)."""
        why = self.d.session.why_inactive()
        if why is not None:
            log.debug("%s not typed: %s", source, why)
        return why is not None

    def send_balance(self, source: str, binding: str) -> None:
        """A press of `source` (a G key, a headset's, or a mouse button by mouse_holder) bound to
        one of BALANCE_ACTIONS: its word to Outputs' socket, from a socket with no address of
        its own, so no answer comes, and never waiting (outputs.send), so the loop goes on
        whatever the unit's process does. Nothing while the session is not active
        (refused_inactive). A press to a full queue is dropped with one line; any other word
        not sent, Outputs off among them, is said once until one is sent
        (BALANCE_NOT_SENT)."""
        if self.refused_inactive(source):
            return
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
                outputs.send(sock, BALANCE_WORDS[binding], outputs.socket_path())
        except BlockingIOError as err:
            log.info("%s bound to %s: %s", source, binding, outputs.unsent(err))
        except (OSError, outputs.SocketRefused) as err:
            if not self._balance_unsent_said:
                self._balance_unsent_said = True
                words = outputs.unsent(err) if isinstance(err, OSError) else reason(err)
                log.info(BALANCE_NOT_SENT, source, binding, words)
        else:
            self._balance_unsent_said = False

    def let_go_binding(self, source: str) -> None:
        """Let go of what `source` holds, in reverse. A failed write does not
        stop the rest, whose counts would stay up for good; the first failure
        is raised after."""
        codes = self._keys_held.pop(source, None)
        if codes is None or self.d.kbd is None:
            return
        failed: UinputError | None = None
        for code in reversed(codes):
            try:
                self.d.kbd.key(code, False)
            except UinputError as exc:
                failed = failed or exc
        if failed is not None:
            raise failed

    def let_go_of_keys(self, source: Keyboard | None = None) -> None:
        """Let go of every key a G key, a headset's G key or a mouse button holds, or with
        `source` those the G keys of that keyboard hold, whose releases will not come."""
        for holder in [h for h in self._keys_held
                       if source is None or self._key_owner.get(h) is source]:
            self._key_owner.pop(holder, None)
            try:
                self.let_go_binding(holder)
            except UinputError as exc:
                log.debug("could not let go of %s: %s", holder, exc)

    def stop_orphaned_macro(self) -> None:
        """Stop a running macro whose key no longer starts it, or a toggle one would type
        on with nothing to stop it. Called wherever a binding or the layer changes; both
        layers count, and a played macro has no key to lose."""
        macro = self.runner.macro
        if macro is None or self.runner.key == PLAY_KEY:
            return
        # A running macro has its key: macros.MacroRunner sets and clears the two together.
        key = cast("str", self.runner.key)
        live: list[str | None]
        if key.startswith("mouse:"):
            _mouse, model, button = key.split(":")
            live = [self.d.games.active_mouse_binding(button, model, shifted)
                    for shifted in (False, True)]
        else:
            live = [self.d.games.active_binding(key, False), self.d.games.active_binding(key, True)]
        running = slug(macro.name)
        if any(slug(name) == running for name in map(macro_name, live) if name):
            return
        log.info("macro %r is no longer bound to %s; stopping it", macro.name, key)
        self.runner.cancel()

    def run_macro(self, name: str, key: str, pressed: bool,
                  source: Keyboard | AttachedMouse | None = None) -> None:
        """Start or stop the macro a key is bound to, read from the library at each press
        that neither stops a toggle nor is refused as inactive, so an edit needs no reload;
        a binding may outlive its macro. The first step runs at once, the run kept as
        `source`'s, the keyboard or mouse pressed (None for a headset's key). Nothing starts
        while locked or blank, the session inactive, or going to sleep."""
        if not pressed:
            self.runner.release(key)
            return
        runner = self.runner
        current = runner.macro
        if (current is not None and runner.key == key and current.repeat == TOGGLE
                and slug(current.name) == slug(name)):
            # Before the library is read: a toggle whose file has since gone,
            # broken or been renamed still stops on its key.
            log.debug("macro %r toggled off by %s", current.name, key)
            runner.cancel()
            return
        if self.refused_inactive(key):
            return
        macro = self.macros.get(name)
        if macro is None:
            log.warning("%s is bound to macro %r, which is not in %s or will not load",
                        key, name, self.macros.directory)
            return
        why = self.d.session.why_away()
        if why is not None:
            log.info("macro %r not started: %s", macro.name, why)
            return
        log.debug("%s -> macro %r (%s)", key, macro.name, macro.repeat)
        now = time.monotonic()
        self.runner.start(macro, key, now, source)
        self.runner.tick(now)

    def on_mouse_button(self, button: str, pressed: bool, source: AttachedMouse) -> None:
        """One button of `source`, dispatched to its binding on the mouse's model, on the
        G-Shift layer while that is held for the mice (shifted_on). A release goes to what the
        press dispatched to, not what is bound now. A DPI action changes `source`."""
        model = model_key(source.product)
        holder = mouse_holder(model, button)
        if pressed:
            if (self._mouse_held.get(holder) in POINTER_ACTIONS or holder in self._keys_held
                    or ShiftHolder(holder, None) in self._shifted_by):
                # Another mouse of this model holds it: the one hold its release lets go of.
                log.debug("%s down on a second mouse: held already", holder)
                return
            # NOTHING too: a rebind under the held button must not give its release a
            # binding the press never acted on.
            binding = self._mouse_held[holder] = self.d.games.active_mouse_binding(
                button, model, self.shifted_on(None))
            # Not setdefault: past the guard, an owner left is a press's that held nothing,
            # which would keep this mouse's hold from its release_mouse_holds.
            self._mouse_owner[holder] = source
        else:
            held = self._mouse_held.pop(holder, None)
            self._mouse_owner.pop(holder, None)
            binding = held or self.d.games.active_mouse_binding(button, model)
        log.debug("%s %s -> %s", holder, "down" if pressed else "up",
                  escaped(binding))     # as on_key's
        if binding == NOTHING or self._took_alike(ShiftHolder(holder, None), binding, pressed,
                                                  source):
            return
        if pressed and binding in POINTER_ACTIONS and self.refused_inactive(holder):
            # Its release lets go of nothing: a back pressed since keeps its count.
            self._mouse_held[holder] = NOTHING
            return
        try:
            if binding in POINTER_ACTIONS:
                self._pointer_button(POINTER_ACTIONS[binding], pressed)
            elif binding == "dpi-shift":
                self.d.mice.dpi.dpi_shift(pressed, source)
            elif binding in DPI_STEPS:
                if pressed:
                    way, wrap = DPI_STEPS[binding]
                    self.d.mice.dpi.dpi_step(way, source, wrap)
            elif not pressed:
                self.let_go_binding(holder)
            else:
                self.hold_binding(holder, binding)
        # HidppFailure: mouse_call words each as a MouseError, so no action raises one today. It
        # stays so an action that sends outside mouse_call cannot end the daemon's one thread.
        except (HidppFailure, MouseError, UinputError) as exc:
            # A cause of DeviceGone is logged at debug: mouse_call said it went away, at info,
            # and a node gone is not warned of. Any other is a warning.
            log.log(logging.DEBUG if isinstance(exc.__cause__, DeviceGone) else logging.WARNING,
                    "%s bound to %r: %s", holder, binding, exc)

    def _pointer_button(self, code: int, pressed: bool) -> None:
        """Mirror a held button on the virtual mouse, so a long press stays a press."""
        if self.d.ptr is None:
            log.debug("pointer button 0x%x ignored: no virtual mouse", code)
            return
        self.d.ptr.button(code, pressed)

    def bindings_digest(self) -> str:
        """A short digest of the bindings in force, both G key layers and each mouse model's on
        both layers: a front end rereads the files when it changes, so a CLI bind shows in an
        open window."""
        games = self.d.games
        shown = [games.overlay.name if games.overlay is not None else None,
                 sorted(games.key_layer(False).items()), sorted(games.key_layer(True).items()),
                 *(sorted((model, sorted(entry.items())) for model, entry in entries.items())
                   for entries in (games.mouse_entries(), games.mouse_shift_entries()))]
        return digest(shown)

    def macros_digest(self) -> str:
        """A short digest of the macro files as the daemon last took them
        (MacroLibrary.stamps). A reload that reads a hand edit moves it, and
        an edit not reloaded does not, so a front end reads the macros again
        once the daemon would save over that edit."""
        shown = sorted((str(path), stamp) for path, stamp in self.macros.stamps.items())
        return digest(shown)


    def _cmd_bind(self, request: dict) -> dict:
        self.d.retry_virtual_devices(now=True)
        key, spec = name_text(request["key"], "bind.key").upper(), request.get("value")
        shift = request.get("shift") is True
        layer = self.d.games.key_layer(shift)
        if key not in layer:
            return {"ok": False, "error": not_bindable(key)}
        spec = key_binding(key, spec, f"bind.{key}", shift)
        other = self.d.games.key_layer(not shift)[key]
        base, shifted = (other, spec) if shift else (spec, other)
        refused = g_shift_refusal(key, base, shifted, shift)
        if refused is not None:
            return {"ok": False, "error": refused}
        if (unsaved := self.save_layer(layer, lambda: layer.update({key: spec}))) is not None:
            return unsaved
        said =[words for words in (self.untested_words(key, layer[key]),
                                    BASE_BINDING_TOO.format(key)
                                    if needs_base_binding(key, base, shifted) else None)
                if words is not None]
        return with_note(self.layer_reply({"ok": True, "key": key, "binding": layer[key]}),
                         f"saved; {'; '.join(said)}" if said else None)

    def untested_words(self, key: str, binding: str | None) -> str | None:
        """What a bind of `key` to `binding` says where the leader's keyboard, or another
        driven, binds `key` and its G keys take no binding until their write is tested on this
        hardware (keyboard.assignments_untested); None otherwise."""
        if binding is None:
            return None
        for keyboard in (self.d.keyboard, *self.d.keyboards.driven()):
            product = keyboard.keyboard_product
            if (assignments_untested(product)
                    and key in KEYBOARDS[cast("int", product)].g_key_names):
                return untested_assignment_words(cast("int", product))
        return None

    def save_layer(self, layer: dict, change: Callable[[], object]) -> dict | None:
        """Make `change` to `layer`, a layer of bindings or the mouse's by model, and save it,
        then stop a macro whose key no longer starts it; None once saved. Refused before
        anything changes when its file cannot be saved to. A failed save puts `layer` back,
        since left changed the next unrelated save would write it, and raises; an OSError of
        the loaded game profile's file is answered here, naming the profile, where
        handle_command's OSError gives the path of its file."""
        self.d.check_layer_writable()
        before = copy.deepcopy(layer)
        change()
        try:
            self.d.save_layer_file()
        except BaseException as exc:
            layer.clear()
            layer.update(before)
            overlay = self.d.games.overlay
            if isinstance(exc, OSError) and overlay is not None:
                return refusal(f"could not save {quoted(overlay.name)}: {reason(exc)}", exc)
            raise
        self.stop_orphaned_macro()
        return None

    def layer_reply(self, reply: dict) -> dict:
        """A bind's reply, naming the game profile it was saved to when one is loaded."""
        if self.d.games.overlay is not None:
            reply["overlay"] = self.d.games.overlay.name
        return reply

    def macro_state(self) -> dict:
        """The running macro's name and repeat mode, and the seconds left while
        a played one counts down, to a tenth and at least 0.1: the CLI and the
        banner read 0 as begun."""
        macro = self.runner.macro
        if macro is None:
            return {"macro": None, "macro_repeat": None, "macro_starts_in": None}
        left = self.runner.starts_in(time.monotonic())
        return {"macro": macro.name, "macro_repeat": macro.repeat,
                "macro_starts_in": None if left is None else max(round(left, 1), 0.1)}

    def _cmd_macros(self, request: dict) -> dict:
        """The library and the running macro (`gearbench-ctl macros` stars it once
        it has begun)."""
        macros = self.macros.all()
        state = self.macro_state()
        return {
            "ok": True,
            "running": state["macro"],
            "macro_starts_in": state["macro_starts_in"],
            "macros": describe_macros(macros),
        }

    def _cmd_macro_save(self, request: dict) -> dict:
        """Write one macro. Macro.save checks the steps and names the one at fault; the
        library refuses a file changed on disk since the daemon read it. Saving over the
        running macro stops it once written: the runner holds a copy of the old steps."""
        # Text, as a macro's file reads its name: str() would name it "None" for a null.
        expect(request["value"], str, "macro_save", "text")
        name = request["value"].strip()
        if not name:
            return {"ok": False, "error": MACRO_NEEDS_A_NAME}
        # The name it was saved under, sent when the window renamed it.
        previous = name_text(request.get("previous") or "", "macro_save.previous").strip()
        existing = self.macros.get(previous or name)
        if existing is not None and request.get("new"):
            return {"ok": False, "error": f"a macro named {quoted(existing.name)} already exists"}
        # A rename onto another macro's name needs `replace`, and that macro
        # goes only once the write has succeeded.
        taken = self.macros.get(name) if previous else None
        if taken is not None and existing is not None and taken.path == existing.path:
            taken = None        # the same macro, renamed in case or spacing only
        if taken is not None and request.get("replace") is not True:
            return {"ok": False, "error": f"a macro named {quoted(taken.name)} already exists; "
                                          f"rename or delete that one first"}
        macro = existing or Macro(name=name)
        was = macro.name
        macro.name = name
        # As sent: Macro.save checks them, and a coerced value would be refused in its place.
        # The steps are the list Macro.steps says only once the save below has taken them.
        macro.repeat = request.get("repeat", macro.repeat)
        macro.steps = cast("list[dict]", request.get("steps"))
        try:
            path = self.macros.save(macro, replace=taken)
        except OSError as exc:
            # Answered here by the macro's name, where handle_command's gives its file's path.
            return refusal(f"could not save {quoted(name)}: {reason(exc)}", exc)
        # By slug, as the library matches names: a rename onto "spam"
        # replaces a running "Spam".
        running = self.runner.macro
        if running is not None and slug(running.name) in (slug(name), slug(was)):
            log.info("macro %r was saved while running; stopping it", running.name)
            self.runner.cancel()
        if was != macro.name:
            self.repoint_macro(was, macro.name)
        count = len(macro.steps)
        log.info("saved macro %r: %s, %s", macro.name, counted(count, "step"), macro.repeat)
        return {"ok": True, "saved": macro.name, "repeat": macro.repeat,
                "steps": count, "path": str(path)}

    def repoint_macro(self, old: str, new: str) -> None:
        """Point every binding that names macro `old` at `new`, in every M profile and each
        game profile the library reads, the loaded one included, and save. A file the daemon
        may not save over, or fails to save, keeps the old name, in memory as on disk, and the
        log says so."""
        spec, wanted = f"{MACRO_PREFIX}{new}", slug(old)

        def repoint(layers: Iterable[dict]) -> bool:
            changed = False
            for layer in layers:
                for key, value in layer.items():
                    named = macro_name(value)
                    if named is not None and slug(named) == wanted and value != spec:
                        layer[key] = spec
                        changed = True
            return changed

        before = {i: copy.deepcopy(p.layers) for i, p in self.d.config.profiles.items()}
        try:
            self.d.check_config_writable()
            if repoint(layer for p in self.d.config.profiles.values()
                       for layer in layers_of(*p.layers)):
                self.d.save_config()
        except (ConfigError, OSError) as exc:
            for i, layers in before.items():
                self.d.config.profiles[i].layers = layers
            # Escaped, as Daemon.persist, here and below: a refusal can quote load_config's
            # error or a file's name as they are, and the journal splits a record at a newline.
            log.warning("macro %r renamed to %r; config.json keeps the old name: %s",
                        old, new, escaped(reason(exc)))
        overlay = self.d.games.overlay
        profiles = [overlay] if overlay is not None else []
        # By file, not name: the file the library reads for a loaded profile left out for it
        # (Library.shadowed) is the one the scan follows next.
        profiles += [g for g in self.d.games.library.all()
                     if overlay is None or g.path != overlay.path]
        for game in profiles:
            layers = copy.deepcopy(game.layers)
            try:
                game.check_unchanged()
                if repoint(layers_of(*game.layers)):
                    game.save()
            except (ConfigError, OSError) as exc:
                game.layers = layers
                log.warning("macro %r renamed to %r; game profile %r keeps the old "
                            "name: %s", old, new, game.name, escaped(reason(exc)))

    def _cmd_macro_delete(self, request: dict) -> dict:
        """Delete a macro, unless its file changed on disk since the daemon
        read it; bindings that name it stay, and only log."""
        name = name_text(request["value"], "macro_delete")
        found = self.macros.get(name)
        if found is None:
            return {"ok": False, "error": self.macros.not_found(name)}
        gone = found.name
        try:
            deleted = self.macros.delete(gone)
        except OSError as exc:
            # Answered here: handle_command's OSError says a save failed.
            return refusal(f"could not delete {quoted(gone)}: {reason(exc)}", exc)
        if not deleted:
            return {"ok": False, "error": not_deleted(gone)}
        log.info("deleted macro %r", gone)
        running = self.runner.macro
        if running is not None and slug(running.name) == slug(gone):
            self.runner.cancel()
        return {"ok": True, "deleted": gone}

    def _cmd_macro_run(self, request: dict) -> dict:
        """Play a macro no key started: the window's Run or `macro-run`. By name it is
        the file; with `steps`, the editor's unsaved macro, never written. The daemon
        counts `delay` down, so every front end sees it and a Stop cancels it. Refused
        while locked or blank, the session inactive, or going to sleep."""
        name = name_text(request["value"], "macro_run").strip()
        if not name:
            return {"ok": False, "error": MACRO_NEEDS_A_NAME}
        delay = request.get("delay", 0)
        if not number(delay) or not 0 <= delay <= MAX_PLAY_DELAY:
            return {"ok": False,
                    "error": f"delay is 0 to {MAX_PLAY_DELAY} s, "
                             f"not {written(delay)}"}
        macro: Macro | None
        if "steps" in request:
            macro = Macro.from_json({"name": name, "repeat": request.get("repeat", ONCE),
                                     "steps": request["steps"]}, slug(name))
        else:
            macro = self.macros.get(name)
            if macro is None:
                return {"ok": False, "error": self.macros.not_found(name)}
        why = self.d.session.why_away()
        if why is not None:
            return {"ok": False, "error": f"macro {quoted(macro.name)} not started: {why}"}
        now = time.monotonic()
        self.runner.play(macro, now, delay)
        self.runner.tick(now)
        log.info("macro %r played (%s)%s", macro.name, macro.repeat,
                 f", starting in {delay:g} s" if delay else "")
        return {"ok": True, "played": macro.name, "repeat": macro.repeat,
                "delay": delay, **self.macro_state()}

    def _cmd_macro_stop(self, request: dict) -> dict:
        """Stop the macro running or counting down, however it started;
        nothing running is no refusal, since it may just have ended."""
        macro = self.runner.macro
        stopped = macro.name if macro is not None else None
        if stopped is not None:
            log.info("macro %r stopped by macro_stop", stopped)
            self.runner.cancel()
        return {"ok": True, "stopped": stopped}

    def mouse_model(self, request: dict) -> str:
        """The key (mouse.model_key) of the mouse model a bind is for: the one `device` names,
        attached or not, since a binding is saved and nothing is sent to the mouse; else
        Mice.current's, or with none attached the selected one's. ConfigError with neither
        (NO_MOUSE_YET), or for a `device` that names no mouse model; one that is neither text
        nor a number is refused as name_text refuses it (shared.mouse_model_asked)."""
        wanted = request.get("device")
        if wanted is None:
            current = self.d.mice.current
            product = current.product if current is not None else self.d.mice.selected
            if product is None:
                raise ConfigError(NO_MOUSE_YET)
            return model_key(product)
        return mouse_model_asked(wanted, (m.label for m in self.d.mice.attached))

    def _cmd_mouse_bind(self, request: dict) -> dict:
        """Bind one button of a mouse model (mouse_model) in the layer in force, on its G-Shift
        layer with `shift` true (_bind_shifted). Its first bind on the base layer keeps the
        model's defaults with it, so the file holds each of its buttons."""
        self.d.retry_virtual_devices(now=True)
        model = self.mouse_model(request)
        bindings = self.d.games.mouse_bindings(model)
        button = mouse_button_name(request["key"])
        if button not in bindings:
            return {"ok": False, "error": not_a_button(model, request["key"], list(bindings))}
        if request.get("shift") is True:
            return self._bind_shifted(model, button, request.get("value"))
        spec = parse_mouse_binding(request.get("value"), button)
        shifted = self.d.games.mouse_shift_bindings(model).get(button)
        refused = g_shift_refusal(button.upper(), spec, shifted, False)
        if refused is not None:
            return {"ok": False, "error": refused}
        entries = self.d.games.mouse_entries()
        unsaved = self.save_layer(entries, lambda: keep_under(entries, model_ids(model),
                                                              {**bindings, button: spec}))
        if unsaved is not None:
            return unsaved
        return self.layer_reply({"ok": True, "key": button, "binding": spec,
                                 "mouse": key_name(model)})

    def _bind_shifted(self, model: str, button: str, value: object) -> dict:
        """mouse_bind on the G-Shift layer: `button` of the mouse model `model` bound to
        `value` there, or with None or "" left out of it, so it sends its base binding. The
        model's entry keeps its other buttons, and goes once it holds none. The reply's
        `binding` is what the button sends while G-Shift is held."""
        spec = None if value is None or value == "" else parse_mouse_binding(value, button)
        refused = g_shift_refusal(button.upper(), self.d.games.mouse_bindings(model)[button],
                                  spec, True)
        if refused is not None:
            return {"ok": False, "error": refused}
        entries = self.d.games.mouse_shift_entries()
        held = entry_under(entries, model_ids(model)) or {}
        own = {name: bound for name, bound in held.items() if name != button}
        if spec is not None:
            own[button] = spec

        def change() -> None:
            keep_under(entries, model_ids(model), own)
            if not own:
                entries.pop(model, None)

        if (unsaved := self.save_layer(entries, change)) is not None:
            return unsaved
        return self.layer_reply({"ok": True, "key": button,
                                 "binding": self.d.games.active_mouse_binding(button, model, True),
                                 "mouse": key_name(model)})

    def _cmd_mouse_bind_defaults(self, request: dict) -> dict:
        """Put a mouse model's buttons (mouse_model) back to its defaults on the base layer:
        its own bindings there go, under each of its ids. Its G-Shift layer stays."""
        model = self.mouse_model(request)
        entries = self.d.games.mouse_entries()

        def drop() -> None:
            for name in model_ids(model):
                entries.pop(name, None)

        if (unsaved := self.save_layer(entries, drop)) is not None:
            return unsaved
        return self.layer_reply({"ok": True, "bindings": self.d.games.mouse_bindings(model),
                                 "mouse": key_name(model)})


def not_a_button(model: str, key: object, buttons: list[str]) -> str:
    """The refusal of a bind of `key` on the mouse model `model`, whose `buttons` are these."""
    name = key_name(model)
    if not buttons:
        return f"the {name} has no buttons to bind"
    return (f"the {name} has no button {written(key)}; its buttons are "
            f"{word_list([b.upper() for b in buttons], 'and')}")
