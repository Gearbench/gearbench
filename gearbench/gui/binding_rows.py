"""A row that binds a key or a button, as the Mouse tab's Buttons group and the headset
page's G keys group draw it: a dropdown of the built-in actions, then Keystroke… and
Macro…, each of which opens the capture dialog, and Change… beside a row bound to a key or
a macro. Each page keeps its rows and sends their binds; this part draws a row, puts it on
a binding and reads its pick. Building one runs nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from gi.repository import Adw, Gtk

from ..config import macro_name
from .board import shown_binding
from .tips import explain_choices
from .worker import LoadingGuard

KEY_CHOICE = "Keystroke…"
MACRO_CHOICE = "Macro…"
#: The entries after the built-in actions, in the row's order; each opens the capture dialog.
#: Last, so a built-in action's row index is its index in the actions.
CHOICES = (KEY_CHOICE, MACRO_CHOICE)
CHANGE = "Change…"


def binding_row(title: str, actions: Sequence[tuple[str, str]], tips: Sequence[str],
                change_tip: str, change: Callable[[], object]) -> tuple[Adw.ComboRow, Gtk.Button]:
    """A row titled `title` of `actions`, (binding, label) pairs, then CHOICES, a tooltip of
    `tips` each; and its Change… button, hidden, whose click runs `change`."""
    row = Adw.ComboRow(title=title, model=Gtk.StringList.new(
        [label for _value, label in actions] + list(CHOICES)))
    explain_choices(row, tips)
    button = Gtk.Button(label=CHANGE, valign=Gtk.Align.CENTER, visible=False,
                        tooltip_text=change_tip)
    button.connect("clicked", lambda _button: change())
    row.add_suffix(button)
    return row, button


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


def show_binding(loading: LoadingGuard, row: Adw.ComboRow, button: Gtk.Button, spec: str,
                 actions: Sequence[tuple[str, str]]) -> None:
    """Put `row` on `spec`: a built-in action selects its own entry; anything else selects
    Keystroke… or Macro… with the binding underneath in the window's words (shown_binding),
    and shows Change…, so the row says what the key does."""
    values = [value for value, _label in actions]
    button.set_visible(spec not in values)
    if spec in values:
        loading.put_selected(row, values.index(spec))
        row.set_subtitle("")
        return
    choice = MACRO_CHOICE if macro_name(spec) is not None else KEY_CHOICE
    loading.put_selected(row, len(values) + CHOICES.index(choice))
    row.set_subtitle(shown_binding(spec))
