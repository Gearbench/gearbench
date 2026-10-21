"""Bindings and macros: what a G key, an M key, the MR layer or a mouse
button does, the keys they hold, and the macro runner."""

from __future__ import annotations

import copy
import logging
import time
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, cast

from ..config import (
    BINDABLE_KEYS,
    MACRO_PREFIX,
    MOUSE_MODEL,
    ConfigError,
    expect,
    keep_under,
    macro_name,
    mouse_button_name,
    not_bindable,
    parse_mouse_binding,
    product_key,
    validate_binding,
)
from ..hidpp import DeviceGone, HidppFailure
from ..keyboard import bound_as, keystroke
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
from ..mouse import MODEL_OF, MouseError, key_name, model_ids, model_key
from ..uinput import POINTER_ACTIONS, UinputError, parse_binding
from ..wording import escaped, number, reason, word_list, written
from .shared import digest, name_text, refusal

if TYPE_CHECKING:
    from . import Daemon
    from .mice.attached import AttachedMouse

log = logging.getLogger("gearbench")

#: Each DPI step action: its way through the stages, +1 up and -1 down, and whether it goes
#: from the last stage round to the first (Mouse.step_dpi's `wrap`).
DPI_STEPS = {"dpi-up": (1, False), "dpi-down": (-1, False), "dpi-cycle": (1, True)}
#: The refusal of a mouse bind made before any mouse was attached, with no `device` named.
NO_MOUSE_YET = ("No mouse has been connected since the Gearbench daemon started, and each "
                "mouse model keeps its own bindings: connect the mouse first.")


def mouse_holder(model: str, button: str) -> str:
    """What a mouse button's held keys and the macro it started are filed under: `mouse:`, the
    mouse model (mouse.model_key) and the button, so a button of one name on two models is
    held apart, as each is bound."""
    return f"mouse:{model}:{button}"


class Bindings:
    """Turns key and button presses into keystrokes, mouse actions and macros."""

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
        #: The mouse whose button started the running macro, kept after the macro ends: a
        #: mouse going away stops a macro of its own alone (stop_mouse_macro).
        self._macro_owner: AttachedMouse | None = None
        #: What each held binding put down on the virtual keyboard, by holder
        #: (a G key, or a mouse button by mouse_holder), let go of at its release.
        self._keys_held: dict[str, tuple[int, ...]] = {}
        #: MR held: the G keys resolve on the shift layer.
        self.shifted = False
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
        # A running macro has its key: macros.MacroRunner sets and clears the two together.
        if (macro is not None and cast("str", self.runner.key).startswith("mouse:")
                and self._macro_owner is source):
            log.info("macro %r stopped: a press or release of %s may be lost",
                     macro.name, self.runner.key)
            self.runner.cancel()

    def release_mouse_holds(self, source: AttachedMouse) -> None:
        """Let go of what the buttons of `source` hold, where their releases could be lost
        (Buttons.drop_spy_state): a back or forward left down would stay pressed on the
        virtual mouse. A write that fails is logged and the rest go on."""
        for holder in [h for h, owner in self._mouse_owner.items() if owner is source]:
            del self._mouse_owner[holder]
            binding = self._mouse_held.pop(holder, None)
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

    def keyboard_gone(self, held_only: bool = False) -> None:
        """Let go of what the keyboard holds, whose releases will not come: MR's shift layer,
        a macro a G key started (with `held_only`, only a while_held one) and the keys the G
        keys hold."""
        self.shifted = False
        macro = self.runner.macro
        if macro is not None and self.runner.key in BINDABLE_KEYS and (
                not held_only or macro.repeat == WHILE_HELD):
            self.runner.cancel()
        self.let_go_of_keys(mouse=False)

    def let_go_away(self, why: str) -> None:
        """Let go of what the G keys, MR and mouse buttons hold, and forget it,
        so the later release lets go of nothing and a key still down when the
        session is back needs a new press. A `dpi-shift` is left to its release,
        which puts the DPI back. A write that fails is logged and the rest go on."""
        pointer = [(holder, binding) for holder, binding in self._mouse_held.items()
                   if binding in POINTER_ACTIONS]
        held = [*(["MR"] if self.shifted else []), *self._keys_held,
                *(holder for holder, _ in pointer)]
        if not held:
            return
        log.info("let go of what %s held: %s", ", ".join(held), why)
        if self.shifted:
            self.shifted = False
            self.d.keyboard.show_mr(False, dark_too=True)
        for holder, binding in pointer:
            # Its release does nothing now, as a button bound to nothing does.
            self._mouse_held[holder] = "none"
            try:
                self._pointer_button(POINTER_ACTIONS[binding], False)
            except UinputError as exc:
                log.debug("could not let go of %s: %s", holder, exc)
        self.let_go_of_keys(mouse=False)
        self.let_go_of_keys(mouse=True)

    def stop_macro_away(self, why: str) -> None:
        """Stop a macro running or counting down, whatever started it: a toggle macro
        would type on into the lock screen's password field."""
        macro = self.runner.macro
        if macro is None:
            return
        log.info("macro %r stopped: %s", macro.name, why)
        self.runner.cancel()


    def on_key(self, key: str, pressed: bool) -> None:
        if key == "MR":
            self.shifted = pressed
            if not (pressed and self.d.session.note_key_input()):
                # A press that lit the board again leaves the LED to that apply.
                self.d.keyboard.show_mr(pressed)
            return

        if key.startswith("M"):
            if pressed:
                index = int(key[1:])
                # Lit again while locked: once, by the switch's own apply
                # when it switches, or here when it does not.
                woke = self.d.session.note_key_input(paint=False)
                if woke and not self.d.config.switches_to(index):
                    self.d.keyboard.apply_profile()
                else:
                    self.d.keyboard.switch_profile(index)
            return

        # Under its model's name for it from here on: the G915 TKL's G1 is its F1.
        product = self.d.keyboard.keyboard_product
        key = bound_as(product, key)
        if not pressed:
            # Before any lookup: the layer may have changed since the press.
            self.runner.release(key)
            try:
                self.let_go_binding(key)
            except UinputError as exc:
                log.warning("%s released: %s", key, exc)
            return

        board = self.d.keyboard.dev
        self.d.session.note_key_input()
        if board is not None and self.d.keyboard.dev is None:
            # The relight tore the keyboard down, letting go of what the G keys hold:
            # this key's release will not come.
            return

        # A G915 TKL's F key with nothing bound types its own key, which HOST mode stops it
        # typing by itself.
        binding = self.d.games.active_binding(key, self.shifted) or keystroke(product, key)
        if binding is None:
            log.debug("%s down (unbound)", key)
            return
        name = macro_name(binding)
        if name is not None:
            self.run_macro(name, key, pressed)
            return
        try:
            self.hold_binding(key, binding)
            # Escaped: a key spec splits at any white space, so one from a file can hold a
            # newline, where the journal would begin a second entry.
            log.debug("%s down -> %s", key, escaped(binding))
        except UinputError as exc:
            log.warning("%s bound to %r: %s", key, binding, exc)

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

    def let_go_of_keys(self, mouse: bool) -> None:
        """Let go of every key the G keys hold, or with `mouse` the mouse
        buttons, whose releases will not come."""
        for source in [s for s in self._keys_held if s.startswith("mouse:") == mouse]:
            try:
                self.let_go_binding(source)
            except UinputError as exc:
                log.debug("could not let go of %s: %s", source, exc)

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
            live = [self.d.games.active_mouse_binding(button, model)]
        else:
            live = [self.d.games.active_binding(key, False), self.d.games.active_binding(key, True)]
        running = slug(macro.name)
        if any(slug(name) == running for name in map(macro_name, live) if name):
            return
        log.info("macro %r is no longer bound to %s; stopping it", macro.name, key)
        self.runner.cancel()

    def run_macro(self, name: str, key: str, pressed: bool) -> None:
        """Start or stop the macro a key is bound to, read from the library at each press
        that neither stops a toggle nor is refused as inactive, so an edit needs no reload;
        a binding may outlive its macro. The first step runs at once. Nothing starts while
        locked or blank, the session inactive, or going to sleep."""
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
        self.runner.start(macro, key, now)
        self.runner.tick(now)

    def on_mouse_button(self, button: str, pressed: bool, source: AttachedMouse) -> None:
        """One button of `source`, dispatched to its binding on the mouse's model. A release
        goes to what the press dispatched to, not what is bound now. A DPI action changes
        `source`."""
        model = model_key(source.product)
        holder = mouse_holder(model, button)
        if pressed:
            # "none" too: a rebind under the held button must not give its release a
            # binding the press never acted on.
            binding = self._mouse_held[holder] = self.d.games.active_mouse_binding(button, model)
            self._mouse_owner.setdefault(holder, source)
        else:
            held = self._mouse_held.pop(holder, None)
            self._mouse_owner.pop(holder, None)
            binding = held or self.d.games.active_mouse_binding(button, model)
        log.debug("%s %s -> %s", holder, "down" if pressed else "up",
                  escaped(binding))     # as on_key's
        if binding == "none":
            return
        name = macro_name(binding)
        if name is not None:
            self.run_macro(name, holder, pressed)
            if pressed and self.runner.key == holder:
                self._macro_owner = source
            return
        if pressed and binding in POINTER_ACTIONS and self.refused_inactive(holder):
            # Its release lets go of nothing: a back pressed since keeps its count.
            self._mouse_held[holder] = "none"
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
            elif pressed:
                self.hold_binding(holder, binding)
            else:
                self.let_go_binding(holder)
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
        """A short digest of the bindings in force, both G key layers and each mouse model's:
        a front end rereads the files when it changes, so a CLI bind shows in an open window."""
        games = self.d.games
        shown = [games.overlay.name if games.overlay is not None else None,
                 sorted(games.key_layer(False).items()), sorted(games.key_layer(True).items()),
                 sorted((model, sorted(entry.items()))
                        for model, entry in games.mouse_entries().items())]
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
        layer = self.d.games.key_layer(bool(request.get("shift")))
        if key not in layer:
            return {"ok": False, "error": not_bindable(key)}
        if spec is not None:
            # Text or null, as the files take it: str() binds a JSON 5 to the 5 key, and 0 or
            # false would unbind.
            expect(spec, str, f"bind.{key}", "a key spec or macro:<name>")
        if spec:
            spec = validate_binding(spec, f"bind.{key}")
        self.save_layer(layer, lambda: layer.update({key: spec or None}))
        return self.layer_reply({"ok": True, "key": key, "binding": layer[key]})

    def save_layer(self, layer: dict, change: Callable[[], object]) -> None:
        """Make `change` to `layer`, a layer of bindings or the mouse's by model, and save it,
        then stop a macro whose key no longer starts it. Refused before anything changes when
        its file cannot be saved to; a failed save puts `layer` back and raises: left changed,
        the next unrelated save would write it."""
        self.d.check_layer_writable()
        before = copy.deepcopy(layer)
        change()
        try:
            self.d.save_layer_file()
        except BaseException:
            layer.clear()
            layer.update(before)
            raise
        self.stop_orphaned_macro()

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
            return {"ok": False, "error": "a macro needs a name"}
        # The name it was saved under, sent when the window renamed it.
        previous = name_text(request.get("previous") or "", "macro_save.previous").strip()
        existing = self.macros.get(previous or name)
        if existing is not None and request.get("new"):
            return {"ok": False, "error": f"a macro named {existing.name!r} already exists"}
        # A rename onto another macro's name needs `replace`, and that macro
        # goes only once the write has succeeded.
        taken = self.macros.get(name) if previous else None
        if taken is not None and existing is not None and taken.path == existing.path:
            taken = None        # the same macro, renamed in case or spacing only
        if taken is not None and not request.get("replace"):
            return {"ok": False, "error": f"a macro named {taken.name!r} already exists; "
                                          f"rename or delete that one first"}
        macro = existing or Macro(name=name)
        was = macro.name
        macro.name = name
        # As sent: Macro.save checks them, and a coerced value would be refused in its place.
        # The steps are the list Macro.steps says only once the save below has taken them.
        macro.repeat = request.get("repeat", macro.repeat)
        macro.steps = cast("list[dict]", request.get("steps"))
        path = self.macros.save(macro, replace=taken)
        # By slug, as the library matches names: a rename onto "spam"
        # replaces a running "Spam".
        running = self.runner.macro
        if running is not None and slug(running.name) in (slug(name), slug(was)):
            log.info("macro %r was saved while running; stopping it", running.name)
            self.runner.cancel()
        if was != macro.name:
            self.repoint_macro(was, macro.name)
        count = len(macro.steps)
        log.info("saved macro %r: %d step%s, %s", macro.name, count, "" if count == 1 else "s",
                 macro.repeat)
        return {"ok": True, "saved": macro.name, "repeat": macro.repeat,
                "steps": count, "path": str(path)}

    def repoint_macro(self, old: str, new: str) -> None:
        """Point every binding that names macro `old` at `new`, in every M and game
        profile, and save. A file the daemon may not save over, or fails to save, keeps the old
        name, in memory as on disk, and the log says so."""
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

        try:
            self.d.check_config_writable()
        except ConfigError as exc:
            # Escaped, as Daemon.persist, here and below: a refusal can quote load_config's
            # error or a file's name as they are, and the journal splits a record at a newline.
            log.warning("macro %r renamed to %r; config.json keeps the old name: %s",
                        old, new, escaped(exc))
        else:
            before = {i: (dict(p.keys), dict(p.shift), copy.deepcopy(p.mouse))
                      for i, p in self.d.config.profiles.items()}
            if repoint(layer for p in self.d.config.profiles.values()
                       for layer in layers_of(p.keys, p.shift, p.mouse)):
                try:
                    self.d.save_config()
                except (ConfigError, OSError) as exc:
                    for i, (keys, shift, mouse) in before.items():
                        profile = self.d.config.profiles[i]
                        profile.keys, profile.shift, profile.mouse = keys, shift, mouse
                    log.warning("macro %r renamed to %r; config.json keeps the old "
                                "name: %s", old, new, escaped(reason(exc)))
        overlay = self.d.games.overlay
        profiles = [overlay] if overlay is not None else []
        # By file, not name: the file the library reads for a loaded profile left out for it
        # (Library.shadowed) is the one the scan follows next.
        profiles += [g for g in self.d.games.library.all()
                     if overlay is None or g.path != overlay.path]
        for game in profiles:
            try:
                game.check_unchanged()
            except ConfigError as exc:
                log.warning("macro %r renamed to %r; game profile %r keeps the old "
                            "name: %s", old, new, game.name, escaped(exc))
                continue
            layers = (dict(game.bindings), dict(game.shift), copy.deepcopy(game.mouse))
            if repoint(layers_of(game.bindings, game.shift, game.mouse)):
                try:
                    game.save()
                except (ConfigError, OSError) as exc:
                    game.bindings, game.shift, game.mouse = layers
                    log.warning("macro %r renamed to %r; game profile %r keeps the old "
                                "name: %s", old, new, game.name, escaped(reason(exc)))

    def _cmd_macro_delete(self, request: dict) -> dict:
        """Delete a macro, unless its file changed on disk since the daemon
        read it; bindings that name it stay, and only log."""
        name = name_text(request["value"], "macro_delete")
        found = self.macros.get(name)
        if found is None:
            return {"ok": False, "error": f"no macro named {name!r}"}
        gone = found.name
        try:
            deleted = self.macros.delete(gone)
        except OSError as exc:
            # Answered here: handle_command's OSError says a save failed.
            return refusal(f"Could not delete {gone!r} ({reason(exc)}).", exc)
        if not deleted:
            return {"ok": False, "error": not_deleted(gone)}
        log.info("deleted macro %r", gone)
        running = self.runner.macro
        if running is not None and slug(running.name) == slug(gone):
            self.runner.cancel()
        return {"ok": True, "deleted": gone}

    def _cmd_macro_run(self, request: dict) -> dict:
        """Play a macro no key started: the window's Play or `macro-run`. By name it is
        the file; with `steps`, the editor's unsaved macro, never written. The daemon
        counts `delay` down, so every front end sees it and a Stop cancels it. Refused
        while locked or blank, the session inactive, or going to sleep."""
        name = name_text(request["value"], "macro_run").strip()
        if not name:
            return {"ok": False, "error": "a macro needs a name"}
        delay = request.get("delay", 0)
        if not number(delay) or not 0 <= delay <= MAX_PLAY_DELAY:
            return {"ok": False,
                    "error": f"delay must be 0 to {MAX_PLAY_DELAY} seconds, "
                             f"not {written(delay)}"}
        macro: Macro | None
        if "steps" in request:
            macro = Macro.from_json({"name": name, "repeat": request.get("repeat", ONCE),
                                     "steps": request["steps"]}, slug(name))
        else:
            macro = self.macros.get(name)
            if macro is None:
                return {"ok": False, "error": f"no macro named {name!r}"}
        why = self.d.session.why_away()
        if why is not None:
            return {"ok": False, "error": f"macro {macro.name!r} not started: {why}"}
        now = time.monotonic()
        self.runner.play(macro, now, float(delay))
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
        """The mouse model (mouse.model_key) a bind is for: `device`, a product id of any mouse
        model, attached or not, since a binding is saved and nothing is sent to the mouse; else
        that of the mouse each mouse command acts on (Mice.current), or with none attached the
        selected one, kept while it is away (Mice.selected). Refused before any mouse was
        attached (NO_MOUSE_YET) and for an id no mouse model has."""
        wanted = request.get("device")
        if wanted is None:
            current = self.d.mice.current
            product = current.product if current is not None else self.d.mice.selected
            if product is None:
                raise ConfigError(NO_MOUSE_YET)
            return model_key(product)
        key = product_key(name_text(wanted, "device"), "device", MOUSE_MODEL)
        if int(key, 16) not in MODEL_OF:
            raise ConfigError(f"device: {key!r} names no mouse model Gearbench drives")
        return model_key(int(key, 16))

    def _cmd_mouse_bind(self, request: dict) -> dict:
        """Bind one button of a mouse model (mouse_model) on the layer in force. Its first bind
        there keeps the model's defaults with it, so the file holds each of its buttons."""
        self.d.retry_virtual_devices(now=True)
        model = self.mouse_model(request)
        bindings = self.d.games.mouse_bindings(model)
        button = mouse_button_name(request["key"])
        if button not in bindings:
            return {"ok": False, "error": not_a_button(model, request["key"], list(bindings))}
        spec = parse_mouse_binding(request.get("value"), button)
        entries = self.d.games.mouse_entries()
        self.save_layer(entries, lambda: keep_under(entries, model_ids(model),
                                                    {**bindings, button: spec}))
        return self.layer_reply({"ok": True, "key": button, "binding": spec,
                                 "mouse": key_name(model)})

    def _cmd_mouse_bind_defaults(self, request: dict) -> dict:
        """Put a mouse model's buttons (mouse_model) back to its defaults on the layer
        mouse_bind writes: its own bindings there go, under each of its ids."""
        model = self.mouse_model(request)
        entries = self.d.games.mouse_entries()

        def drop() -> None:
            for name in model_ids(model):
                entries.pop(name, None)

        self.save_layer(entries, drop)
        return self.layer_reply({"ok": True, "bindings": self.d.games.mouse_bindings(model),
                                 "mouse": key_name(model)})


def layers_of(keys: dict, shift: dict, mouse: dict[str, dict[str, str]]) -> list[dict]:
    """A profile's layers of bindings: its G keys', its MR layer's and each mouse model's."""
    return [keys, shift, *mouse.values()]


def not_a_button(model: str, key: object, buttons: list[str]) -> str:
    """The refusal of a bind of `key` on the mouse model `model`, whose `buttons` are these."""
    name = key_name(model)
    if not buttons:
        return f"the {name} has no buttons to bind"
    return (f"the {name} has no button {written(key)}; its buttons are "
            f"{word_list([b.upper() for b in buttons], 'and')}")
