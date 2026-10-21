"""Per-item tooltips for a dropdown's popover.

A dropdown's popover rows are reachable only by setting its factory, which
then has to draw what the default one drew: the label and the check mark on
the selected entry. Losing that check mark is the trap this helper is for.

An explanation of each entry in a list goes here, on its entry, instead of
under the row as a subtitle.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from gi.repository import Adw, Gtk

#: What libadwaita's own factory marks the selected entry with.
CHECK_ICON = "object-select-symbolic"


def explain_choices(dropdown: Gtk.DropDown | Adw.ComboRow, tips: Sequence[str]) -> None:
    """Give each entry in `dropdown`'s popover its own tooltip, which is also the
    entry's accessible description.

    `dropdown` is a `Gtk.DropDown` or an `Adw.ComboRow` on a `Gtk.StringList`.
    `tips` holds one tooltip for each entry, in the model's order. It is read as each entry
    is drawn, not copied: a caller that gives `dropdown` a new model changes `tips` in place
    to match.
    """
    factory = Gtk.SignalListItemFactory()
    #: The bound entries and their checks. GTK keeps them bound between openings
    #: of the popover, so a check set only at bind would go stale.
    bound: dict[Gtk.ListItem, Gtk.Image] = {}

    def setup(_factory: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
        box = Gtk.Box(spacing=6)
        box.append(Gtk.Label(xalign=0, hexpand=True))
        # Presentation, as GTK's and libadwaita's own checks are: the entry's
        # selected state is what assistive technology reads.
        box.append(Gtk.Image(icon_name=CHECK_ICON,
                             accessible_role=Gtk.AccessibleRole.PRESENTATION))
        item.set_child(box)

    def bind(_factory: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
        # `setup` put the box in the item, with its label first and its check last, and the
        # model is the Gtk.StringList the docstring asks for.
        box = cast(Gtk.Box, item.get_child())
        label = cast(Gtk.Label, box.get_first_child())
        check = cast(Gtk.Image, box.get_last_child())
        position = item.get_position()
        label.set_text(cast(Gtk.StringObject, item.get_item()).get_string())
        # Opacity rather than visibility, so every row keeps the same width.
        check.set_opacity(1.0 if position == dropdown.get_selected() else 0.0)
        box.set_tooltip_text(tips[position])
        # Keyboard focus and assistive technology land on the entry, not on the box,
        # whose generic role GTK gives neither a name nor a description.
        item.set_accessible_description(tips[position])
        bound[item] = check

    def unbind(_factory: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
        bound.pop(item, None)

    def selected(*_args: object) -> None:
        chosen = dropdown.get_selected()
        for item, check in bound.items():
            check.set_opacity(1.0 if item.get_position() == chosen else 0.0)

    factory.connect("setup", setup)
    factory.connect("bind", bind)
    factory.connect("unbind", unbind)
    dropdown.set_list_factory(factory)
    dropdown.connect("notify::selected", selected)
