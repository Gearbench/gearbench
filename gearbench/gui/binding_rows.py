"""A row that binds a key or a button, as the Mouse tab's Buttons group and the headset
page's G keys group draw it: a dropdown of the built-in actions, then Keystroke… and
Macro…, each of which opens the capture dialog, and Change… beside a row bound to a key or
a macro. This part draws a row, puts it on a binding and reads its pick; LayerRows holds
such a group's rows under its Layer and Editing rows and opens the capture dialog for them,
and each page sends their binds. Building one runs nothing.

Also the Layer row those groups and the Keyboard tab's Keys group share, Base or G-Shift,
which layer the rows show and bind, with its line, and the G-Shift layer's own first entry,
Same as Base, for a key or button with no binding there, in a list without G-Shift (hold).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from functools import partial
from typing import cast

from gi.repository import Adw, GObject, Gtk

from ..config import G_SHIFT, NOTHING, macro_name
from .binding_labels import (
    FALLS_THROUGH,
    FALLS_THROUGH_TIP,
    NOTHING_TIP,
    SAME_AS_BASE,
    outputs_line,
    shift_layer_actions,
    shown_binding,
)
from .dialogs import CaptureDialog
from .tips import explain_choices
from .worker import DaemonWorker, LoadingGuard

KEY_CHOICE = "Keystroke…"
MACRO_CHOICE = "Macro…"
#: The entries after the built-in actions, in the row's order; each opens the capture dialog.
#: Last, so a built-in action's row index is its index in the actions.
CHOICES = (KEY_CHOICE, MACRO_CHOICE)
CHANGE = "Change…"
#: A Change… button's accessible name, by its row's title on the layer shown (layer_named), so
#: a screen reader tells a group's Change… buttons apart, and Base's from G-Shift's.
CHANGE_NAME = "Change {}"
#: The Layer row: its title, its two choices, by the name each goes by (layer_row), and its
#: line (layer_line), by whether MR is there to hold and whether one of the keyboards' keys
#: it reads, the headsets' keys or the mice's buttons is bound to G_SHIFT.
LAYER = "Layer"
BASE_LAYER, SHIFT_LAYER = "Base", "G-Shift"
HOLD_MR = "Hold MR"
HOLD_MR_OR_HOLDER = "Hold MR or a G-Shift key or button"
BIND_A_HOLDER = "Bind G-Shift to a key or button first"
HOLD_A_HOLDER = "Hold a G-Shift key or button"
#: The window's name for a key or button on the G-Shift layer, and the toast of one whose
#: binding there is cleared, in binding_labels.FALLS_THROUGH's words.
WITH_G_SHIFT = "{} with G-Shift"
SHIFT_CLEARED = "{} with G-Shift does the same as on Base"
#: A bind's toast: the key or button on its layer (layer_named), then what it does now.
BOUND = "{} → {}"
#: The value of binding_labels.SAME_AS_BASE among the G-Shift layer's actions
#: (layer_actions), which no binding can be: picked, it clears the binding there.
NO_SHIFT_BINDING = ""


def layer_row(picked: Callable[[bool], object]) -> tuple[Adw.ActionRow, Adw.ToggleGroup]:
    """The Layer row, on Base, and its two-button suffix, which a screen reader names by the
    row's title; `picked` gets True for G-Shift at each change of the pick, a
    set_active_name's included."""
    row = Adw.ActionRow(title=LAYER, use_markup=False)
    toggles = Adw.ToggleGroup(valign=Gtk.Align.CENTER)
    for name, label in (("base", BASE_LAYER), ("shift", SHIFT_LAYER)):
        toggles.add(Adw.Toggle(name=name, label=label))
    toggles.set_active_name("base")
    toggles.update_property([Gtk.AccessibleProperty.LABEL], [LAYER])
    toggles.connect("notify::active-name",
                    lambda group, _pspec: picked(group.get_active_name() == "shift"))
    row.add_suffix(toggles)
    return row, toggles


def layer_named(name: str, shift: bool) -> str:
    """`name`, a key's or a button's, as the window names it on the G-Shift layer, `shift`
    (WITH_G_SHIFT), or as it is on Base."""
    return WITH_G_SHIFT.format(name) if shift else name


def holds_g_shift(keys: Mapping[str, str | None], mouse: Mapping[str, Mapping[str, str]]) -> bool:
    """Whether a key of `keys`, a layer's G keys and headset keys, or a button of a model in
    `mouse`, that layer's mouse bindings by model, is bound to G_SHIFT."""
    return G_SHIFT in keys.values() or any(G_SHIFT in buttons.values()
                                           for buttons in mouse.values())


def layer_line(mr: bool, held: bool) -> str:
    """The Layer row's line, by whether MR is there to hold and whether a key or button holds
    G-Shift when held (holds_g_shift)."""
    if mr:
        return HOLD_MR_OR_HOLDER if held else HOLD_MR
    return HOLD_A_HOLDER if held else BIND_A_HOLDER


def layer_actions(actions: Sequence[tuple[str, str]], shift: bool) -> list[tuple[str, str]]:
    """A binding row's built-in actions on its layer, `actions`; on the G-Shift layer,
    `shift`, after SAME_AS_BASE and without G_SHIFT, which the daemon refuses there
    (shift_layer_actions)."""
    if not shift:
        return list(actions)
    return [(NO_SHIFT_BINDING, SAME_AS_BASE), *shift_layer_actions(actions)]


def _entries(actions: Sequence[tuple[str, str]]) -> Gtk.StringList:
    """A binding row's list: the labels of `actions`, then CHOICES."""
    return Gtk.StringList.new([label for _value, label in actions] + list(CHOICES))


def put_layer(loading: LoadingGuard, rows: Iterable[Adw.ComboRow], tips: list[str],
              actions: Sequence[tuple[str, str]], action_tips: Sequence[str],
              shift: bool) -> None:
    """Give `rows` the list of their layer, the G-Shift layer's with `shift` (layer_actions),
    and change `tips`, the list their entries' tooltips are read from (binding_row), in place
    to match it entry by entry: each listed action's of `action_tips`, then CHOICES', with
    FALLS_THROUGH_TIP for SAME_AS_BASE and NOTHING_TIP for NOTHING on that layer, where it binds
    nothing only while G-Shift is held. Each row then needs putting on its binding again
    (show_binding)."""
    listed = layer_actions(actions, shift)
    tip_of = dict(zip((value for value, _label in actions), action_tips[:len(actions)],
                      strict=True))
    if shift:
        tip_of |= {NO_SHIFT_BINDING: FALLS_THROUGH_TIP, NOTHING: NOTHING_TIP}
    tips[:] = [*(tip_of[value] for value, _label in listed), *action_tips[len(actions):]]
    for row in rows:
        loading.put_model(row, _entries(listed))


def binding_row(title: str, actions: Sequence[tuple[str, str]], tips: Sequence[str],
                change_tip: str, change: Callable[[], object],
                shift: bool) -> tuple[Adw.ComboRow, Gtk.Button]:
    """A row titled `title` of `actions`, (binding, label) pairs, then CHOICES, a tooltip of
    `tips` each, read as each entry is drawn (put_layer changes them in place); and its
    Change… button, hidden, whose click runs `change`, named for the G-Shift layer with
    `shift` (title_binding_row)."""
    row = Adw.ComboRow(model=_entries(actions))
    explain_choices(row, tips)
    button = Gtk.Button(label=CHANGE, valign=Gtk.Align.CENTER, visible=False,
                        tooltip_text=change_tip)
    button.connect("clicked", lambda _button: change())
    row.add_suffix(button)
    title_binding_row(row, button, title, shift)
    return row, button


def title_binding_row(row: Adw.ComboRow, button: Gtk.Button, title: str, shift: bool) -> None:
    """Title `row` `title`, and name its Change… button, `button`, after it on the layer it
    binds, the G-Shift layer with `shift` (CHANGE_NAME, layer_named)."""
    row.set_title(title)
    button.update_property([Gtk.AccessibleProperty.LABEL],
                           [CHANGE_NAME.format(layer_named(title, shift))])


def binding_words(spec: str, actions: Sequence[tuple[str, str]]) -> str:
    """A binding in its row's words: an action's label, or as the board shows it."""
    return dict(actions).get(spec) or shown_binding(spec)


def read_pick(row: Adw.ComboRow, actions: Sequence[tuple[str, str]]) -> tuple[str | None, bool]:
    """What `row`'s entry asks for: the binding of a built-in action among `actions`, or None
    for the capture dialog, with whether it opens on the macro list (MACRO_CHOICE); False
    opens it for a keystroke (KEY_CHOICE)."""
    index = row.get_selected()
    if index < len(actions):
        return actions[index][0], False
    return None, index == len(actions) + CHOICES.index(MACRO_CHOICE)


def show_binding(loading: LoadingGuard, row: Adw.ComboRow, button: Gtk.Button,
                 spec: str | None, actions: Sequence[tuple[str, str]],
                 outputs_off: bool) -> None:
    """Put `row` on `spec`: a built-in action selects its own entry, a balance word with
    binding_labels.NEEDS_OUTPUTS under it while `outputs_off` (outputs_line); anything else
    selects Keystroke… or Macro… with the binding underneath in the window's words
    (shown_binding), and shows Change…, so the row says what the key does. None, a key or
    button with no binding on the G-Shift layer, selects SAME_AS_BASE, with FALLS_THROUGH as
    the row's tooltip."""
    row.set_tooltip_text(FALLS_THROUGH if spec is None else None)
    spec = NO_SHIFT_BINDING if spec is None else spec
    values = [value for value, _label in actions]
    button.set_visible(spec not in values)
    if spec in values:
        loading.put_selected(row, values.index(spec))
        row.set_subtitle(outputs_line(spec, outputs_off) or "")
        return
    choice = MACRO_CHOICE if macro_name(spec) is not None else KEY_CHOICE
    loading.put_selected(row, len(values) + CHOICES.index(choice))
    row.set_subtitle(shown_binding(spec))


def bound_toast(label: str, shift: bool, now: str | None,
                actions: Sequence[tuple[str, str]]) -> str:
    """A bind's toast for the key or button `label` on its layer, the G-Shift layer with
    `shift`: SHIFT_CLEARED where it left none there (`now` None), else BOUND with `now` in its
    row's words (binding_words)."""
    return (SHIFT_CLEARED.format(label) if now is None
            else BOUND.format(layer_named(label, shift), binding_words(now, actions)))


def undo_offered(was: str | None, now: str | None, shift: bool) -> bool:
    """Whether a bind's toast offers Undo: it replaced or cleared `was`, the binding on its
    layer before it, and `now` is what the layer holds after it, None for none there. On
    Base, `shift` False, NOTHING is the key or button unbound, so a bind over it offers
    none; on G-Shift Nothing is a binding of its own."""
    return was is not None and (shift or was != NOTHING) and was != now


class LayerRows:
    """A group's binding rows, one a key or button, and the Layer and Editing rows over them:
    the layer the rows show and bind, `shift` True for G-Shift as the Layer row is, and each
    row's binding as the page last took it, on Base (`base`) and on G-Shift (`shifted`,
    None or missing where it has none there, which shows Same as Base).

    A built-in action picked in a row goes to `bind(name, spec)`, spec None for Same as
    Base; Keystroke…, Macro… and Change… go to the page's `capture(name, macros)`, macros
    True for the macro list, which opens the dialog with LayerRows.capture and binds what it
    picks. `outputs_off` is read at each show (OutputsGroup.balance_unheard), and a line
    `subtitle(name)` gives replaces the binding's under the row. The page builds the Layer
    and Editing rows (layer_rows) and each binding row (add) and places them. Building one
    runs nothing and draws nothing.
    """

    def __init__(self, loading: LoadingGuard, actions: Sequence[tuple[str, str]],
                 action_tips: Sequence[str], change_tip: str,
                 bind: Callable[[str, str | None], object],
                 capture: Callable[[str, bool], object], outputs_off: Callable[[], bool],
                 subtitle: Callable[[str], str | None] = lambda _name: None) -> None:
        self._loading = loading
        self._actions, self._action_tips, self._change_tip = actions, action_tips, change_tip
        self._bind, self._capture = bind, capture
        self._outputs_off, self._subtitle = outputs_off, subtitle
        self.shift = False
        self.base: dict[str, str] = {}
        self.shifted: dict[str, str | None] = {}
        #: The tooltips of the rows' entries on the layer they show, changed in place with
        #: their lists (put_layer).
        self._tips = list(action_tips)
        self.rows: dict[str, Adw.ComboRow] = {}
        #: Each row's Change… button, shown while the row is on Keystroke… or Macro…: an
        #: Adw.ComboRow only notifies a change, so picking the entry again does nothing.
        self.change_buttons: dict[str, Gtk.Button] = {}

    def layer_rows(self) -> tuple[Adw.ActionRow, Adw.ActionRow]:
        """The Layer row, first in the group, and under it the Editing row, which says which
        layer a binding is written to (set_editing)."""
        self.layer_row, self.layer_toggles = layer_row(self.on_layer)
        # Not markup: it names a game profile.
        self.editing_row = Adw.ActionRow(title="Editing", use_markup=False)
        return self.layer_row, self.editing_row

    def set_editing(self, text: str, line: str) -> None:
        """Say which layer the bindings are written to, and how G-Shift is held, the Layer
        row's `line` (KeyboardPage.layer_line)."""
        self.editing_row.set_title(text)
        self.layer_row.set_subtitle(line)

    def add(self, name: str, title: str) -> Adw.ComboRow:
        """The row of `name`, titled `title`, with the list of the layer shown, for the page
        to place."""
        row, self.change_buttons[name] = binding_row(
            title, layer_actions(self._actions, self.shift), self._tips, self._change_tip,
            partial(self.on_change, name), self.shift)
        row.connect("notify::selected", self.on_binding, name)
        self.rows[name] = row
        return row

    def on_layer(self, shift: bool) -> None:
        """The Layer row picked G-Shift, or Base: each row lists that layer's entries
        (put_layer), its Change… is named for that layer, and it shows its binding there."""
        self.shift = shift
        put_layer(self._loading, self.rows.values(), self._tips, self._actions,
                  self._action_tips, shift)
        for name, row in self.rows.items():
            title_binding_row(row, self.change_buttons[name], row.get_title(), shift)
        self.show_all()

    def show_all(self) -> None:
        """Put each row on its binding (show_held)."""
        for name in self.rows:
            self.show_held(name)

    def show_held(self, name: str) -> None:
        """Put `name`'s row on its binding on the layer shown (show_binding), where one with
        none on G-Shift shows Same as Base, and give it `subtitle`'s line where there is
        one."""
        row = self.rows[name]
        own = self.shifted.get(name) if self.shift else self.base.get(name, NOTHING)
        show_binding(self._loading, row, self.change_buttons[name], own,
                     layer_actions(self._actions, self.shift), self._outputs_off())
        if (line := self._subtitle(name)) is not None:
            row.set_subtitle(line)

    def take(self, name: str, shift: bool, now: str | None) -> str | None:
        """Keep `now`, a bind's reply, as `name`'s binding on its layer, the G-Shift layer with
        `shift`, None for none there; put its row on it (show_held); and return what the layer
        held before, None for none, which the toast's Undo binds again (undo_offered)."""
        if shift:
            was = self.shifted.get(name)
            self.shifted[name] = now
        else:
            was = self.base.get(name)
            # Base always holds a binding: Nothing is one, and Same as Base is G-Shift's alone.
            self.base[name] = cast("str", now)
        self.show_held(name)
        return was

    def capture(self, parent: Gtk.Window, worker: DaemonWorker, name: str, label: str,
                macros: bool, bind: Callable[[str, bool], object],
                closed: Callable[[], object]) -> None:
        """Open the capture dialog over `parent` for `name`'s row, titled `label` on the layer
        shown, on the macro list with `macros`; a key or macro picked goes to
        `bind(spec, shift)`, `shift` as the Layer row was at the open: the polls go on while
        it is open, and an M key, the tray or a program can move the window's layer
        meanwhile. The dialog has no Unbind: a row's Nothing binds to nothing on Base, and
        its Same as Base clears the binding on G-Shift. Closed with nothing picked, the row
        goes back to its binding (show_held); `closed` runs at each close, after that."""
        shift = self.shift
        # The worker fills the dialog's macro list.
        dialog = CaptureDialog(parent, layer_named(label, shift), worker=worker,
                               can_unbind=False)
        picked = False

        def done(spec: str | None) -> None:
            nonlocal picked
            picked = True
            # With no Unbind, the dialog hands on no None.
            bind(cast("str", spec), shift)

        def on_closed(_dialog: Adw.Dialog) -> None:
            if not picked:
                self.show_held(name)
            closed()
        dialog.run(done, macros=macros)
        dialog.connect("closed", on_closed)

    def on_binding(self, row: Adw.ComboRow, _pspec: GObject.ParamSpec, name: str) -> None:
        """A row's entry picked by the user: a built-in action is bound, any other opens the
        capture dialog."""
        if self._loading.active:
            return
        action, macros = read_pick(row, layer_actions(self._actions, self.shift))
        if action is not None:
            self._bind(name, None if action == NO_SHIFT_BINDING else action)
            return
        self._capture(name, macros)

    def on_change(self, name: str) -> None:
        """Change… on a row bound to a key or a macro, opened on the macro list for a macro:
        picking the row's entry again does nothing (change_buttons)."""
        self._capture(name, read_pick(self.rows[name],
                                      layer_actions(self._actions, self.shift))[1])
