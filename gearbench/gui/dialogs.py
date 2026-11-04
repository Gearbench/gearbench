"""The modal dialogs: `CaptureDialog` captures a key, `RecordDialog` records a macro.

`CaptureDialog` is the window's one place to bind a keyboard's G key, so a
macro, the actions a G key takes and the keys the capture cannot take (Escape,
and Super alone under GNOME Shell) must be reachable there.

Both are `Adw.Dialog`s, drawn inside the window that opens them. Their keys
pass through that window, but an Adw.Dialog sets limit-events: the window's
own controllers see no key while the focus is in the dialog, except those
set to propagation-limit none (`track_keys_down`, `swallow_held_keys`), so
F10 is captured or recorded and does not open the main menu.
"""

from __future__ import annotations

import time
import weakref
from collections.abc import Callable
from typing import cast

from gi.repository import Adw, Gdk, GObject, Gtk

from ..config import MACRO_PREFIX
from ..key_list import KEY_GROUPS, KEY_LABELS, key_matches
from ..macros import (
    MAX_STEPS,
    MacroRecorder,
    macro_summary,
    step_summary,
)
from ..uinput import MODIFIER_KINDS, MODIFIER_ORDER, UinputError, resolve, spec_for_keys
from ..wording import capitalized, counted, service_command
from .binding_labels import (
    FALLS_THROUGH,
    NOTHING_ACTION,
    NOTHING_TIP,
    SAME_AS_BASE,
    SHARED_ACTIONS,
    SHARED_TIPS,
    outputs_line,
    shift_layer_actions,
)
from .style import held_while_shown, in_place, pointer_button, say_in_place, weakly
from .worker import (
    BUSY,
    DOWN,
    UNREAD,
    DaemonWorker,
    Reply,
    failure,
    unreachable,
)

#: The key list's modifier boxes, (spec name, label), in the order a spec names them.
MODIFIER_BOXES = tuple((kind, kind.capitalize()) for kind in MODIFIER_ORDER)
#: Each box's description to a screen reader, which does not tie the "With" before the
#: boxes to them.
MODIFIER_BOX_HELP = "With the key you pick"
#: The capture's height in pixels: its pane asks 234 and the header 46 (measured
#: 2026-10-15 at the dialog's 380 px width). The key list's needs room for a
#: search, boxes and a list.
CAPTURE_HEIGHT = 280
KEY_LIST_HEIGHT = 560
#: The Super keys, the one modifier whose release may never arrive: GNOME
#: Shell keeps a lone Super's for its Activities overview.
SUPER_CODES = frozenset(code for code, kind in MODIFIER_KINDS.items() if kind == "super")
#: GDK's hardware keycode is the evdev code plus this: X keycodes start at 8, and xkb's evdev
#: keymap adds 8 to every key.
EVDEV_OFFSET = 8
#: The capture dialog's buttons that open a list, each saying what its page does, as that
#: page's title: Choose a key and Choose a macro.
CHOOSE_A_KEY = "Choose a key…"
CHOOSE_A_MACRO = "Choose a macro…"
#: The key list's group of binding_labels.SHARED_ACTIONS, for a keyboard's G key, its tips
#: by binding word, NOTHING_ACTION's tip among them for the G-Shift layer, and the
#: capture's hint's last line, which names what only the key list picks: Super alone, and
#: with that group an action.
ACTIONS = "Actions"
ACTION_TIPS = SHARED_TIPS | {NOTHING_ACTION[0]: NOTHING_TIP}
FOR_SUPER = f"For Super alone, use {CHOOSE_A_KEY}"
FOR_SUPER_OR_ACTION = f"For Super alone or an action, use {CHOOSE_A_KEY}"
#: A read of the macros that failed, in the macro list and the Macros tab's toast.
MACROS_UNREAD = "The macros could not be read"
#: An empty library, in the macro list and the Macros tab's list, each with its own next step.
NO_MACROS = "No macros yet"
#: The recording dialog's list before the first step.
NOTHING_RECORDED = "Nothing recorded yet"
#: The next step after unreachable's words under a list the daemon fills, the macro list
#: and the program picker, by `failure`'s kind. A socket that cannot be used has none: its
#: words name the path and the cause.
LIST_NEXT_STEP = {UNREAD: "Press Back and try again", BUSY: "Press Back and try again",
                  DOWN: f"Start it with `{service_command('start')}`"}
#: The keys each window's one guard keeps from it (swallow_held_keys).
_GUARDED: weakref.WeakKeyDictionary[Gtk.Window, set[int]] = weakref.WeakKeyDictionary()
#: The keys down in each window that counts them (track_keys_down), for keys_held.
_DOWN: weakref.WeakKeyDictionary[Gtk.Window, set[int]] = weakref.WeakKeyDictionary()


def held_modifiers(state: Gdk.ModifierType) -> dict[str, bool]:
    """The four modifiers `state` holds down, as `spec_for_keys` takes them; locks are none."""
    return {"ctrl": bool(state & Gdk.ModifierType.CONTROL_MASK),
            "shift": bool(state & Gdk.ModifierType.SHIFT_MASK),
            "alt": bool(state & Gdk.ModifierType.ALT_MASK),
            "meta": bool(state & Gdk.ModifierType.SUPER_MASK)}


def unlisted(error: BaseException) -> str:
    """A failed round trip as the line under a list's heading: unreachable's words,
    capitalized, then LIST_NEXT_STEP's step for its kind where there is one."""
    words = capitalized(unreachable(error))
    step = LIST_NEXT_STEP.get(failure(error))
    return f"{words}. {step}" if step else words


def track_keys_down(window: Gtk.Window) -> None:
    """Count the keys down in `window` (keys_held), by GDK hardware keycode, each from its
    press there to its release, all forgotten as the keyboard comes or goes: what a dialog
    opening over the window keeps from it (swallow_held_at_open)."""
    down: set[int] = set()
    _DOWN[window] = down

    def pressed(_c: Gtk.EventControllerKey, _keyval: int, keycode: int,
                _state: Gdk.ModifierType) -> bool:
        down.add(keycode)
        return False

    def released(_c: Gtk.EventControllerKey, _keyval: int, keycode: int,
                 _state: Gdk.ModifierType) -> None:
        down.discard(keycode)

    # No propagation limit: a dialog is an Adw.Dialog, which sets limit-events.
    keys = Gtk.EventControllerKey(name="keys-down",
                                  propagation_phase=Gtk.PropagationPhase.CAPTURE,
                                  propagation_limit=Gtk.PropagationLimit.NONE)
    keys.connect("key-pressed", pressed)
    keys.connect("key-released", released)
    window.add_controller(keys)
    # The releases go where the keyboard goes.
    window.connect("notify::is-active", lambda *_args: down.clear())


def keys_held(window: Gtk.Window) -> set[int]:
    """The keys down in `window` (track_keys_down), none in a window that does not count
    them."""
    return _DOWN.get(window, set())


def swallow_held_at_open(window: Gtk.Window) -> None:
    """Keep the keys still held in `window` as a dialog opens over it from the dialog until
    each comes up (swallow_held_keys)."""
    swallow_held_keys(window, keys_held(window))


def swallow_held_keys(window: Gtk.Window, keycodes: set[int]) -> None:
    """Keep the presses of `keycodes`, still held as a dialog over `window` opens or closes,
    from the window until each comes up there or the keyboard leaves the window, taking the
    releases with it. The desktop repeats a held key's press, and a repeated Space or Enter
    presses what has the focus next: what opened the dialog, a response of a confirmation
    the dialog's callback put up, or the dialog's own capture or recording.

    One guard to a window, which a later call adds its keys to: a newer guard that took a
    key's press takes its release from the older, which would then eat the key's next press.
    No keys, no guard."""
    if not keycodes:
        return
    if (guarded := _GUARDED.get(window)) is not None:
        guarded |= keycodes
        return
    held = _GUARDED[window] = set(keycodes)

    def let_go(owner: Gtk.Window, controller: Gtk.EventControllerKey) -> None:
        owner.disconnect(watch)
        owner.remove_controller(controller)
        _GUARDED.pop(owner, None)

    def pressed(_c: Gtk.EventControllerKey, _keyval: int, keycode: int,
                _state: Gdk.ModifierType) -> bool:
        return keycode in held

    def released(controller: Gtk.EventControllerKey, _keyval: int, keycode: int,
                 _state: Gdk.ModifierType) -> None:
        held.discard(keycode)
        owner = cast(Gtk.Window, controller.get_widget())
        # GTK runs a widget's controllers newest first, and a key controller that took a
        # press takes its release from those after it, the window's count among them
        # (GTK 4.22.4, measured 2026-10-24).
        keys_held(owner).discard(keycode)
        if not held:
            # Kept to the last release: off the window, it would see none.
            let_go(owner, controller)

    def inactive(owner: Gtk.Window, _pspec: GObject.ParamSpec) -> None:
        # The keyboard went to another window or the overview, the releases with it: kept
        # on, the guard would eat each key's next press here.
        if not owner.is_active():
            let_go(owner, guard)

    # No propagation limit: the dialog and the confirmation are Adw.Dialogs, which set
    # limit-events.
    guard = Gtk.EventControllerKey(name="held-keys",
                                   propagation_phase=Gtk.PropagationPhase.CAPTURE,
                                   propagation_limit=Gtk.PropagationLimit.NONE)
    guard.connect("key-pressed", pressed)
    guard.connect("key-released", released)
    window.add_controller(guard)
    # The window's is-active handler, which goes with the guard.
    watch = window.connect("notify::is-active", inactive)


def close_keeping_held(dialog: Adw.Dialog, window: Gtk.Window, keys_down: set[int]) -> None:
    """Close `dialog`, and keep `keys_down`, the keys still held in it, from `window` until
    they come up (`swallow_held_keys`)."""
    # Copied first: the keyboard leaving the closed dialog empties the set.
    held = set(keys_down)
    dialog.close()
    swallow_held_keys(window, held)


class CaptureDialog(Adw.Dialog):
    """Asks the user to press a key, and reports it as a binding spec.

    Reads the hardware keycode, not the keysym: GDK's keycode is the evdev code
    plus 8, so a remap follows the physical key whatever the layout.

    Keys held down together are the binding together (the owner's decisions of
    2026-09-25). A modifier held while a key goes down prefixes it, `ctrl+f13`;
    other keys go in the order they went down, `a+b+c`. Modifiers pressed with
    no other key are the binding once all are up: `shift`, `rsuper`, or a chord
    such as `rctrl+rshift`, each keeping its side, in `spec_for_keys`'s order.
    Every capture waits for its confirming button, named by `verb`.

    GNOME Shell keeps a lone Super's release for its Activities overview
    (mutter's `process_special_modifier_key`), so Super tapped alone never
    completes; a chord with Super arrives as any other. A Super that GDK's
    state no longer holds is dropped at the next press, and every key is
    forgotten when the keyboard leaves the dialog, so no key stays counted as
    down. Opening, closing and Back keep a key still held from the dialog and
    the window until it comes up (`swallow_held_at_open`, `close_keeping_held`,
    `_back`): Enter held on a G key, Change… or Back would repeat into the
    capture as its binding, and Enter held at a pick from a list into what
    gets the focus back, opening the dialog again.

    **Choose a key…** is the second way to pick (the owner's decisions of
    2026-09-24): a search, Ctrl, Shift, Alt and Super boxes sent with the key
    picked, and a curated list of keys (the owner's decision of 2026-09-25). It
    binds Escape, which closes the capture, and Super alone. It takes one key;
    keys held together are captured, or typed with `gearbench-ctl bind`.

    With `actions`, for a keyboard's G key, which has no dropdown of them as a
    mouse button's row has (the owner's decision of 2026-10-28), the key list
    opens on the Actions group, binding_labels.SHARED_ACTIONS in its order, each
    by its label over its binding word with its tip, a balance word with
    binding_labels.NEEDS_OUTPUTS there instead while `outputs_off`, as that row
    says it, `OutputsGroup.balance_unheard` from the caller. A pick reports the
    word alone: the boxes are a key's. The capture's hint names the group.

    Given a `worker`, it also offers the daemon's macros and reports a pick as
    `macro:<name>`. Without one it captures keys only, for a macro's own key
    step: a macro cannot run a macro.

    `can_unbind=False` drops the Unbind button, for a caller with nothing to
    unbind, such as a macro step, or one whose row unbinds with Nothing on Base and
    clears with Same as Base on G-Shift, as a mouse button's and a headset G key's do.
    `verb` is the confirming button's label and the act its hint names, as the title's
    first word: "Bind", or "Change" for a macro step. `title` replaces "`verb` `target`" on
    the capture page.

    With `shift`, for a keyboard's G key on the G-Shift layer, Unbind reads SAME_AS_BASE,
    which a clear there gives the key, and the Actions group opens with NOTHING_ACTION (the
    owner's decisions of 2026-10-28) and leaves out G_SHIFT, which the daemon refuses there,
    as a binding row's list does on that layer.
    """

    def __init__(self, parent: Gtk.Window, target: str,
                 worker: DaemonWorker | None = None, can_unbind: bool = True,
                 title: str | None = None, actions: bool = False,
                 outputs_off: bool = False, shift: bool = False, verb: str = "Bind") -> None:
        #: The capture's title, which Back puts back.
        self._capture_title = title or f"{verb} {target}"
        #: The confirming button's label, which its hint names.
        self._verb = verb
        super().__init__(title=self._capture_title, content_width=380,
                         content_height=CAPTURE_HEIGHT)
        self._parent = parent
        self.spec: str | None = None
        self._on_done: Callable[[str | None], None] | None = None
        self._worker = worker
        #: Set at close: the macro list may answer after, when its rows are disposed.
        self._closed = False
        self.connect("closed", self._on_closed)
        #: The keys down, in the order this dialog saw them go down.
        self._down: list[int] = []
        #: The keys down in the dialog on any page, by GDK hardware keycode.
        self._keys_down: set[int] = set()
        #: The spec of the modifiers down at the last press, while no other
        #: key has gone down since nothing was: the binding once all are up.
        self._chord: str | None = None
        #: A non-modifier went down since all keys were up, so the modifiers are its
        #: combination, no binding of their own.
        self._keyed = False
        #: (group, list) per group of the key list (_group_names), empty until _fill_keys.
        self.key_lists: list[tuple[Adw.PreferencesGroup, Gtk.ListBox]] = []
        #: The search the rows are filtered by now.
        self._key_query = ""
        #: The key list has the Actions group, and its balance words say NEEDS_OUTPUTS.
        self._actions, self._outputs_off = actions, outputs_off
        #: The Actions group's labels by binding word, which give its rows: on the G-Shift
        #: layer NOTHING_ACTION first and no G_SHIFT, which the daemon refuses there.
        self._action_labels = (dict((NOTHING_ACTION, *shift_layer_actions(SHARED_ACTIONS)))
                               if shift else dict(SHARED_ACTIONS))

        self.label = Gtk.Label(label="Press a key", css_classes=["title-1"])
        self.hint = Gtk.Label(
            label=f"{target} will send it.\nHold keys together, or tap a modifier alone.\n"
                  f"{FOR_SUPER_OR_ACTION if actions else FOR_SUPER}",
            justify=Gtk.Justification.CENTER, css_classes=["dimmed"])
        self._build_header()
        self._build_capture(can_unbind, shift)
        self._build_macro_list()
        self._build_key_list()

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(self.capture_box)
        content.append(self.macro_box)
        content.append(self.key_box)
        view = Adw.ToolbarView(content=content)
        view.add_top_bar(self.header)
        self.set_child(view)
        self._build_controllers()

    def _build_header(self) -> None:
        """The header bar, with Back and Cancel."""
        # Cancel and Back are in the header: either list hides the body, and a modal
        # window with no button reads as frozen.
        self.header = Adw.HeaderBar(show_end_title_buttons=False)
        self.back = Gtk.Button(label="Back", visible=False)
        self.back.connect("clicked", weakly(self._back))
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", weakly(self._close))
        self.header.pack_start(self.back)
        self.header.pack_start(cancel)

    def _build_capture(self, can_unbind: bool, shift: bool) -> None:
        """The capture pane: the prompt and its hint over the buttons, Unbind as SAME_AS_BASE
        on the G-Shift layer, `shift`."""
        self.accept = Gtk.Button(label=self._verb, css_classes=["suggested-action"],
                                 sensitive=False)
        self.accept.connect("clicked", weakly(self._confirm))

        # The other ways to pick get their own row: four buttons on one row do not fit.
        others = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
                         halign=Gtk.Align.CENTER)
        choose = Gtk.Button(label=CHOOSE_A_KEY)
        choose.connect("clicked", weakly(self._ask_keys))
        others.append(choose)
        if self._worker is not None:
            macro = Gtk.Button(label=CHOOSE_A_MACRO)
            macro.connect("clicked", weakly(self._ask_macros))
            others.append(macro)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
                          halign=Gtk.Align.CENTER)
        if can_unbind:
            unbind = Gtk.Button(label=SAME_AS_BASE if shift else "Unbind",
                                tooltip_text=FALLS_THROUGH if shift else None)
            unbind.connect("clicked", weakly(self._unbind))
            buttons.append(unbind)
        buttons.append(self.accept)

        self.capture_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                                   margin_top=24, margin_bottom=18,
                                   margin_start=18, margin_end=18,
                                   valign=Gtk.Align.CENTER)
        self.capture_box.append(self.label)
        self.capture_box.append(self.hint)
        self.capture_box.append(others)
        self.capture_box.append(buttons)

    def _build_macro_list(self) -> None:
        """The macro list, on a scrolling page of its own."""
        # Built empty and kept hidden; filled from the macros command.
        self.macro_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE,
                                      css_classes=["boxed-list"])
        # Keep the box: a ScrolledWindow wraps a non-scrollable child in a
        # Viewport, so get_child() gives back the viewport, not this box.
        macro_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                              margin_top=12, margin_bottom=12,
                              margin_start=12, margin_end=12)
        macro_inner.append(self.macro_list)
        self.macro_box = Gtk.ScrolledWindow(child=macro_inner, vexpand=True, visible=False)

    def _build_key_list(self) -> None:
        """The key list's page, without its rows."""
        # The key list: the search and boxes stay put over a scrolling list. Its rows
        # are built at first open, since most bindings are made by pressing the key.
        self.key_search = Gtk.SearchEntry(placeholder_text="Search the keys",
                                          hexpand=True)
        # The placeholder is no name to a screen reader.
        self.key_search.update_property([Gtk.AccessibleProperty.LABEL], ["Search the keys"])
        self.key_search.connect("search-changed", weakly(self._on_search))
        self.modifier_checks: dict[str, Gtk.CheckButton] = {}
        checks = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12,
                         halign=Gtk.Align.CENTER)
        checks.append(Gtk.Label(label="With", css_classes=["dimmed"]))
        for name, label in MODIFIER_BOXES:
            self.modifier_checks[name] = Gtk.CheckButton(label=label)
            self.modifier_checks[name].update_property([Gtk.AccessibleProperty.DESCRIPTION],
                                                       [MODIFIER_BOX_HELP])
            checks.append(self.modifier_checks[name])
        self.key_empty = Gtk.Label(css_classes=["dimmed"], wrap=True, visible=False,
                                   margin_top=12)
        self._key_rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                                 margin_top=6, margin_bottom=12,
                                 margin_start=12, margin_end=12)
        self._key_rows.append(self.key_empty)
        # The list is curated; a last line points to the CLI for the rest.
        self.key_more = Gtk.Label(label="Other keys: `gearbench-ctl keys`",
                                  css_classes=["dimmed"], wrap=True)
        self._key_rows.append(self.key_more)
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=12, margin_start=12, margin_end=12, margin_bottom=6)
        top.append(self.key_search)
        top.append(checks)
        self.key_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True,
                               visible=False)
        self.key_box.append(top)
        self.key_box.append(Gtk.ScrolledWindow(child=self._key_rows, vexpand=True,
                                               hscrollbar_policy=Gtk.PolicyType.NEVER))
        # Typing anywhere on the page goes into the search.
        self.key_search.set_key_capture_widget(self.key_box)

    def _build_controllers(self) -> None:
        """The key controller that captures, and the focus controller that forgets the keys."""
        # Capture phase: the dialog opens with the focus on Cancel, and in the bubble
        # phase Space and Enter would click it before reaching this handler. Named for
        # GTK_DEBUG=keybindings.
        keys = Gtk.EventControllerKey(name="capture-keys",
                                      propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", weakly(self._on_key))
        keys.connect("key-released", weakly(self._on_key_up))
        self.add_controller(keys)
        # The keyboard leaving (another window, the overview) takes its releases with
        # it: forget the keys, or one counted as down joins every later capture. GTK
        # also sends leave when the window goes inactive.
        focus = Gtk.EventControllerFocus(name="capture-focus")
        focus.connect("leave", weakly(self._keyboard_left))
        self.add_controller(focus)

    def _on_closed(self, _dialog: Adw.Dialog) -> None:
        self._closed = True

    def _close(self, *_args: object) -> None:
        close_keeping_held(self, self._parent, self._keys_down)

    # -- the pages ---------------------------------------------------------

    def _show_page(self, page: Gtk.Widget, title: str,
                   height: int = CAPTURE_HEIGHT) -> None:
        """Show one of the capture, the macro list and the key list."""
        self._forget_keys()
        self.set_title(title)
        for box in (self.capture_box, self.macro_box, self.key_box):
            box.set_visible(box is page)
        self.back.set_visible(page is not self.capture_box)
        self.set_content_height(height)

    def _back(self, _b: Gtk.Button | None) -> None:
        """Back to key capture, from either list, under its own title, with the keys still
        held kept from it until they come up (swallow_held_keys): Back clicks at its
        activate timeout with Space or Enter still down."""
        # Copied first: hiding Back, which has the focus once pressed, moves the focus out of
        # the dialog, which empties the set (_keyboard_left).
        held = set(self._keys_down)
        self._show_page(self.capture_box, self._capture_title)
        swallow_held_keys(self._parent, held)

    # -- the macro list ----------------------------------------------------

    def _ask_macros(self, _b: Gtk.Button | None) -> None:
        self._show_page(self.macro_box, "Choose a macro")
        say_in_place(self.macro_list, "Reading the macros…", waiting=True)
        # A worker: the button that leads here exists only with one, and `macros` comes only
        # with one.
        cast(DaemonWorker, self._worker).call({"command": "macros"}, self._show_macros)

    def _show_macros(self, reply: Reply) -> None:
        if self._closed:
            return
        # An empty library and an unreachable daemon get different placeholders; the Macros
        # tab lists the macros either way, since it reads the files itself.
        if isinstance(reply, Exception):
            say_in_place(self.macro_list, MACROS_UNREAD, unlisted(reply))
            return
        if not reply.get("ok"):
            error = str(reply.get("error") or "")
            say_in_place(self.macro_list, MACROS_UNREAD, capitalized(error))
            return
        macros = reply.get("macros") or []
        if not macros:
            say_in_place(self.macro_list, NO_MACROS,
                         "Make one on the Macros tab, then come back")
            return
        self.macro_list.remove_all()
        for macro in macros:
            # Not markup: a name with "&" would leave the row empty.
            row = Adw.ActionRow(title=macro["name"], use_markup=False,
                                subtitle=macro_summary(macro["steps"], macro["repeat"]),
                                activatable=True)
            row.connect("activated", weakly(self._pick_macro), macro["name"])
            self.macro_list.append(row)

    def _pick_macro(self, _row: Adw.ActionRow, name: str) -> None:
        self._done(f"{MACRO_PREFIX}{name}")

    # -- the key list ------------------------------------------------------

    def _ask_keys(self, _b: Gtk.Button | None) -> None:
        if not self.key_lists:
            self._fill_keys()
        self._show_page(self.key_box, "Choose a key", KEY_LIST_HEIGHT)
        self.key_search.grab_focus()

    def _group_names(self) -> list[tuple[str, tuple[str, ...]]]:
        """The key list's groups down the page, (title, the names of its rows): ACTIONS first
        where the dialog has it, its binding words, then each `KEY_GROUPS` group."""
        actions = [(ACTIONS, tuple(self._action_labels))] if self._actions else []
        return [*actions, *KEY_GROUPS.items()]

    def _fill_keys(self) -> None:
        """A group per _group_names group, a row per name (_entry_row)."""
        for title, names in self._group_names():
            listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE,
                                  css_classes=["boxed-list"])
            for name in names:
                listbox.append(self._entry_row(name))
            listbox.set_filter_func(weakly(self._key_shown))
            group = Adw.PreferencesGroup(title=title)
            group.add(listbox)
            self._key_rows.insert_child_after(group, self.key_lists[-1][0]
                                              if self.key_lists else None)
            self.key_lists.append((group, listbox))

    def _entry_row(self, name: str) -> Adw.ActionRow:
        """The key list's row for `name`: an action's label over its binding word, or for a
        balance word over binding_labels.NEEDS_OUTPUTS while `outputs_off` (outputs_line), with
        its tip; a key's label over its spec name."""
        label = self._action_labels.get(name)
        if label is None:
            row = Adw.ActionRow(title=KEY_LABELS[name], subtitle=name,
                                name=name, activatable=True, use_markup=False)
            # The row's own signal: the list's row-activated misses ActionRow.activate().
            row.connect("activated", weakly(self._pick_key), name)
            return row
        row = Adw.ActionRow(title=label, subtitle=outputs_line(name, self._outputs_off) or name,
                            tooltip_text=ACTION_TIPS[name], name=name, activatable=True,
                            use_markup=False)
        row.connect("activated", weakly(self._pick_action), name)
        return row

    def _on_search(self, entry: Gtk.SearchEntry) -> None:
        """Keep the rows that match, and the groups that still have one."""
        self._key_query = entry.get_text().strip()
        shown = 0
        for (group, listbox), (_title, names) in zip(self.key_lists, self._group_names(),
                                                      strict=True):
            listbox.invalidate_filter()
            matches = sum(map(self._matches, names))
            group.set_visible(matches > 0)
            shown += matches
        self.key_empty.set_label(f'No key matches "{self._key_query}"')
        self.key_empty.set_visible(shown == 0)

    def _matches(self, name: str) -> bool:
        """Whether the search shows the row for `name`: a key, or an action by its label."""
        return key_matches(name, self._key_query, self._action_labels.get(name))

    def _key_shown(self, row: Adw.ActionRow) -> bool:
        return self._matches(row.get_name())

    def _pick_action(self, _row: Adw.ActionRow, word: str) -> None:
        """Bind the action picked, whatever boxes are checked, and close."""
        self._done(word)

    def _pick_key(self, _row: Adw.ActionRow, name: str) -> None:
        """Bind the key picked, prefixed by the checked boxes in a capture's order, and close."""
        # Shift with Left Shift is one key, and a binding names a key once.
        ticked = [mod for mod, box in self.modifier_checks.items()
                  if box.get_active() and resolve(mod) != resolve(name)]
        self._done("+".join([*ticked, name]))

    # -- the capture -------------------------------------------------------

    def _on_key(self, _c: Gtk.EventControllerKey, keyval: int, keycode: int,
                state: Gdk.ModifierType) -> bool:
        self._keys_down.add(keycode)
        if keyval == Gdk.KEY_Escape:
            # This controller takes every key before the Adw.Dialog does, so it closes on
            # Escape itself. Escape exits from every page and is bound from the key list
            # (the owner's answer of 2026-09-24).
            self._close()
            return True
        if not self.capture_box.get_visible():
            return False        # a list has the keyboard while it is up
        code = keycode - EVDEV_OFFSET
        # A Super that GDK's state says is up was released out of sight, in GNOME Shell's
        # overview (SUPER_CODES); counted as down, it would join the next capture.
        if not state & Gdk.ModifierType.SUPER_MASK:
            self._down = [c for c in self._down if c not in SUPER_CODES]
        if code in self._down:
            return True  # the key repeating
        if not self._down:
            self._forget_keys()
        self._down.append(code)
        if any(c not in MODIFIER_KINDS for c in self._down):
            self._keyed = True
            self._take(state)
        elif not self._keyed:
            # Modifiers alone: the binding once all are up, since a key may still come.
            self._chord = spec_for_keys(self._down, **held_modifiers(state))
        return True

    def _on_key_up(self, _c: Gtk.EventControllerKey, _keyval: int, keycode: int,
                   _state: Gdk.ModifierType) -> None:
        """The last modifier of a chord coming up makes the chord the binding. A
        non-modifier released without its press seen was down with them, so they
        were its combination."""
        self._keys_down.discard(keycode)
        code = keycode - EVDEV_OFFSET
        if code not in self._down:
            if code not in MODIFIER_KINDS:
                self._keyed = True
            return
        self._down.remove(code)
        if not self._down and self._chord and not self._keyed:
            self._show(self._chord)

    def _forget_keys(self) -> None:
        """Count nothing of the capture as down: the next key starts a new binding."""
        self._down, self._chord, self._keyed = [], None, False

    def _keyboard_left(self, *_args: object) -> None:
        self._keys_down.clear()         # their releases go where the keyboard went
        self._forget_keys()

    def _take(self, state: Gdk.ModifierType) -> None:
        """Take the keys down, with the modifiers `state` holds, as the binding."""
        try:
            spec = spec_for_keys(self._down, **held_modifiers(state))
        except UinputError as exc:
            # Drop the last good key too, or the confirming button would bind it under
            # "Cannot use this key".
            self.spec = None
            self.accept.set_sensitive(False)
            self.label.set_text("Cannot use this key")
            self.hint.set_text(f"{capitalized(str(exc))}. Press another key.")
            return
        self._show(spec)

    def _show(self, spec: str) -> None:
        """Show `spec` as the binding, for the confirming button to confirm."""
        self.spec = spec
        self.label.set_text(spec)
        self.hint.set_text(f"Press another key to change it, or {self._verb} to confirm.")
        self.accept.set_sensitive(True)

    def _done(self, result: str | None) -> None:
        """Hand `result` to `run`'s callback, and close."""
        # `run` set it before it showed the dialog, whose buttons, keys and lists lead here.
        cast(Callable[[str | None], None], self._on_done)(result)
        self._close()

    def _confirm(self, _b: Gtk.Button | None) -> None:
        self._done(self.spec)

    def _unbind(self, _b: Gtk.Button | None) -> None:
        self._done(None)

    def run(self, on_done: Callable[[str | None], None], macros: bool = False) -> None:
        """Show the dialog; `macros` opens it on the macro list, for a caller's Macro… choice.
        Held until it closes, since its callers keep no reference and its buttons, keys and
        lists are weakly's (held_while_shown)."""
        self._on_done = on_done
        if macros:
            self._ask_macros(None)
        held_while_shown(self)
        swallow_held_at_open(self._parent)
        self.present(self._parent)


class RecordDialog(Adw.Dialog):
    """Records keys and clicks made in this dialog, as macro steps.

    Only this dialog: reading the keyboard elsewhere means grabbing the real
    devices, which is a keylogger. The dialog's text says so, since a recorder
    that looked global and was not would be worse than one plainly local.

    Keys are read by GDK hardware keycode, as `CaptureDialog` reads them, so a
    recording follows the physical keys whatever the layout. Escape, the one
    way out from the keyboard, is never recorded: the capture-phase key
    controller swallows every other key. A modifier held while a key goes down
    is part of that key's step, `ctrl+a`; Right Alt is `alt` even as AltGr, so
    AltGr+Q is `alt+q`. One tapped or held alone is a step of its own, `shift`
    or `rctrl` (the owner's decision of 2026-09-25, `MacroRecorder.modifier`).

    Presses and releases are both watched, so a held key records as a hold, and
    the recorder drops the auto-repeats. Stop adds a release for anything still
    held, so no recording presses and never lets go. Opening and closing keep a
    key still held from the dialog and the window until it comes up
    (`swallow_held_keys`): Space held on Record… would repeat into the
    recording, and a key held at the close would press what gets the focus
    back.

    Clicks are watched under the header bar alone, so Cancel and Stop are in
    it: a button that is both a control and a recordable click is neither
    reliably.

    Stop hands the steps to `run`'s callback; closing any other way discards
    them.
    """

    def __init__(self, parent: Gtk.Window) -> None:
        super().__init__(title="Record a macro", content_width=460, content_height=520)
        self._parent = parent
        self.recorder = MacroRecorder()
        self._on_done: Callable[[list[dict]], None] | None = None
        self._recording = True
        self._rows: list[Adw.ActionRow] = []
        #: The keys down in the dialog, by GDK hardware keycode, for `_close`.
        self._keys_down: set[int] = set()

        header = Adw.HeaderBar(show_end_title_buttons=False)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", weakly(self._close))
        stop = Gtk.Button(label="Stop", css_classes=["suggested-action"])
        stop.connect("clicked", weakly(self._stop))
        header.pack_start(cancel)
        header.pack_end(stop)

        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE,
                                   css_classes=["boxed-list"])
        self.listbox.set_placeholder(in_place(NOTHING_RECORDED))
        self.scroller = Gtk.ScrolledWindow(child=self.listbox, vexpand=True,
                                           hscrollbar_policy=Gtk.PolicyType.NEVER)
        # Follow the end once a new row has its size: an idle can run before the frame
        # lays the row out, and scroll to the old bottom.
        self.scroller.get_vadjustment().connect("notify::upper", weakly(self._scroll_to_end))

        self.status = Gtk.Label(label="Recording", css_classes=["title-2"])
        self.hint = Gtk.Label(
            label="Keys and clicks in this dialog become steps; nothing outside it is "
                  "recorded, and only GNOME's own shortcuts, such as Super, reach the "
                  "desktop.\nStop hands the steps to the editor. Escape discards them.",
            wrap=True, justify=Gtk.Justification.CENTER, css_classes=["dimmed"])
        self.count = Gtk.Label(label=self._count_text(), css_classes=["dimmed"])

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                       margin_top=18, margin_bottom=18,
                       margin_start=18, margin_end=18, vexpand=True)
        body.append(self.status)
        body.append(self.hint)
        body.append(self.scroller)
        body.append(self.count)

        # Clicks are watched on all under the header: a click in the body's margin is one in
        # the dialog too, and a margin is outside the body's own allocation.
        area = Adw.Bin(child=body)
        view = Adw.ToolbarView(content=area)
        view.add_top_bar(header)
        self.set_child(view)

        # Raw events, not a click gesture: a gesture resets when another button goes
        # down while its own is held, dropping that press and the releases, even with
        # one gesture per button. Capture phase, so a step row's click is seen first.
        buttons = Gtk.EventControllerLegacy(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        buttons.connect("event", weakly(self._on_button))
        area.add_controller(buttons)

        keys = Gtk.EventControllerKey(name="record-keys",
                                      propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", weakly(self._on_key))
        keys.connect("key-released", weakly(self._on_key_up))
        self.add_controller(keys)
        # The keyboard going elsewhere, another window or GNOME Shell's
        # overview after a lone Super, takes the releases with it.
        focus = Gtk.EventControllerFocus(name="record-focus")
        focus.connect("leave", weakly(self._keyboard_left))
        self.add_controller(focus)

    # -- what the user did -------------------------------------------------

    def _on_key(self, _c: Gtk.EventControllerKey, keyval: int, keycode: int,
                state: Gdk.ModifierType) -> bool:
        self._keys_down.add(keycode)
        if keyval == Gdk.KEY_Escape:
            self._close()
            return True
        self._key(keycode, state, True)
        return True

    def _on_key_up(self, _c: Gtk.EventControllerKey, _keyval: int, keycode: int,
                   state: Gdk.ModifierType) -> None:
        self._keys_down.discard(keycode)
        # Whatever its keyval: under xkb's caps:escape_shifted_capslock, Shift+Caps Lock with
        # Shift let go first comes up as Escape. Escape's own press closes the dialog before
        # the recorder sees it, and the recorder drops a release whose press it never saw.
        self._key(keycode, state, False)

    def _key(self, keycode: int, state: Gdk.ModifierType, pressed: bool) -> None:
        """One key going down or coming up, by GDK hardware keycode."""
        if not self._recording:
            return
        self._lost_super(state)
        if not self._recording:
            return                  # Super's release filled it: as `_click`, the key is no step
        code = keycode - EVDEV_OFFSET
        if code in MODIFIER_KINDS:
            # Alone, as bound alone; the recorder holds its press until a key takes it
            # into a combination or it becomes a step.
            self._after(self.recorder.modifier(time.monotonic(), spec_for_keys([code]), pressed))
            return
        try:
            # The modifiers the recorder saw going down as well as the state's: the
            # state names none for Right Alt as AltGr.
            spec = spec_for_keys([*self.recorder.modifiers_down, code], **held_modifiers(state))
        except UinputError as exc:
            if pressed:
                self.count.set_text(f"Not recorded: {exc}")
            return
        self._after(self.recorder.key(time.monotonic(), spec, pressed))

    def _lost_super(self, state: Gdk.ModifierType) -> None:
        """Let go of a Super that GDK's state says is up: its release went to GNOME Shell's
        overview unseen. Counted as down, it would go into the next key, modifier or click."""
        if not state & Gdk.ModifierType.SUPER_MASK and self.recorder.released_unseen(
                time.monotonic(), SUPER_CODES):
            self._after(True)

    def _keyboard_left(self, *_args: object) -> None:
        self._keys_down.clear()         # their releases go where the keyboard went
        if self._recording:
            self._after(self.recorder.keyboard_left(time.monotonic()))

    def _on_button(self, controller: Gtk.EventControllerLegacy, *_args: object) -> bool:
        # Only while recording: a click's state can find up the Super press that filled the
        # recording, and letting go of it would make room again under a status of Full.
        if self._recording and (change := pointer_button(controller)) is not None:
            self._lost_super(change.event.get_modifier_state())
            self._click(change.button, change.pressed)
        return False

    def _click(self, button: int, pressed: bool = True) -> None:
        """One mouse button going down or coming up, by its toolkit number. Split from
        the event handler so a test can drive it without a pointer."""
        if not self._recording:
            return
        self._after(self.recorder.click(time.monotonic(), button, pressed))

    def _after(self, accepted: bool) -> None:
        """Show what the recorder did with the last action."""
        # A refused action can still have let in a modifier that was waiting.
        if accepted or len(self._rows) != len(self.recorder):
            self._add_rows()
        if self.recorder.full:
            self._recording = False
            self.count.set_text(
                f"Stopped: a macro holds at most {MAX_STEPS} steps. "
                f"Press Stop to keep them.")
            self.status.set_text("Full")
            return
        self.count.set_text(self._count_text())

    def _count_text(self) -> str:
        return f"{counted(len(self.recorder), 'step')} so far"

    def _add_rows(self) -> None:
        """Show the steps the last action produced, refreshing the rows already there:
        a press is rewritten in place as a tap when its release lands in the tap window."""
        for index, step in enumerate(self.recorder.steps):
            title, subtitle = step_summary(step)
            if index == len(self._rows):
                # Not markup: the subtitle is what was typed.
                row = Adw.ActionRow(use_markup=False)
                self.listbox.append(row)
                self._rows.append(row)
            self._rows[index].set_title(f"{index + 1}. {title}")
            self._rows[index].set_subtitle(subtitle)

    def _scroll_to_end(self, adjustment: Gtk.Adjustment, _pspec: GObject.ParamSpec) -> None:
        adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())

    # -- leaving -----------------------------------------------------------

    def _close(self, *_args: object) -> None:
        close_keeping_held(self, self._parent, self._keys_down)

    def _stop(self, _b: Gtk.Button | None) -> None:
        """Hand the steps over after closing: the editor's callback may put up a
        confirmation, which should not stack over this dialog."""
        # Stop's click carries no event, so the keyboard's own state says whether Super is
        # up. The click came from a seat, and GDK gives each seat a keyboard. Untried on a
        # desktop as of 2026-10-11: that GNOME Shell keeps this state in step with a Super
        # release it took for its overview.
        seat = cast(Gdk.Seat, self.get_display().get_default_seat())
        self._lost_super(cast(Gdk.Device, seat.get_keyboard()).get_modifier_state())
        steps = self.recorder.finish()
        self._recording = False
        self._close()
        # `run` set it before it showed the dialog, whose Stop button leads here.
        cast(Callable[[list[dict]], None], self._on_done)(steps)

    def run(self, on_done: Callable[[list[dict]], None]) -> None:
        """Show the dialog; `on_done` gets the steps on Stop only. Held until it closes,
        since its caller keeps no reference and its handlers are weakly's (held_while_shown)."""
        self._on_done = on_done
        held_while_shown(self)
        swallow_held_at_open(self._parent)
        self.present(self._parent)
