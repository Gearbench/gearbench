"""A dropdown's popover entries: a tooltip on each (explain_choices), and those out of
reach grayed out (gray_out_choices).

A dropdown's popover rows are reachable only by setting its factory, which
then has to draw what the default one drew: the label and the check mark on
the selected entry. Losing that check mark is the trap these helpers are for.

An explanation of each entry in a list goes here, on its entry, instead of
under the row as a subtitle.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from typing import cast

from gi.repository import Adw, Gtk

#: What libadwaita's own factory marks the selected entry with.
CHECK_ICON = "object-select-symbolic"
#: Each noise gate's entry tip on every headset page, by the word the socket names it by:
#: Command Center's own descriptions, shortened, and Off, the Centurion headsets' gate that
#: lets every sound through. That the A50 X's Night, Home and Tournament are Astro's is
#: assumed until a listen to each on one.
NOISE_GATE_TIPS = {
    "off": "Every sound the microphone picks up goes through.",
    "streaming": "The lightest gate, for a quiet room.",
    "night": "Very lightly gated, for quiet speech.",
    "home": "Astro's default: removes most light background noise.",
    "tournament": "The strongest gate, for loud rooms; it gates the sidetone too.",
}


def explain_choices(dropdown: Gtk.DropDown | Adw.ComboRow, tips: Sequence[str]) -> None:
    """Give each entry in `dropdown`'s popover its own tooltip, which is also the
    entry's accessible description.

    `dropdown`'s model is a `Gtk.StringList`. `tips` holds one tooltip for each entry, in the
    model's order. It is read as each entry is drawn, not copied: a caller that gives
    `dropdown` a new model changes `tips` in place to match.
    """

    def describe(item: Gtk.ListItem, box: Gtk.Box, _label: Gtk.Label) -> None:
        position = item.get_position()
        box.set_tooltip_text(tips[position])
        # Keyboard focus and assistive technology land on the entry, not on the box,
        # whose generic role GTK gives neither a name nor a description.
        item.set_accessible_description(tips[position])

    _draw_choices(dropdown, describe)


def gray_out_choices(dropdown: Gtk.DropDown | Adw.ComboRow, out: Collection[int],
                     why: str) -> Callable[[], None]:
    """Draw each entry of `dropdown`'s popover whose position `out` holds grayed out, with
    `why` under its name, and out of reach: a click or Enter on it picks nothing.

    `dropdown`'s model is a `Gtk.StringList`. `out` is read as each entry is drawn, not
    copied; the answer draws the entries drawn already again, for a caller that changed it.
    """

    def describe(item: Gtk.ListItem, box: Gtk.Box, label: Gtk.Label) -> None:
        reachable = item.get_position() not in out
        if not reachable:
            label.set_text(f"{label.get_text()}\n{why}")
        box.set_sensitive(reachable)
        item.set_selectable(reachable)
        item.set_activatable(reachable)

    return _draw_choices(dropdown, describe)


def _draw_choices(dropdown: Gtk.DropDown | Adw.ComboRow,
                  describe: Callable[[Gtk.ListItem, Gtk.Box, Gtk.Label], None]
                  ) -> Callable[[], None]:
    """Draw each entry of `dropdown`'s popover as libadwaita's own factory does, its name
    and the check mark on the selected one, then hand it to `describe`. The answer draws
    the entries drawn already again.

    No handler here holds `dropdown`: GTK holds each one through the dropdown or its factory,
    so one that held it would be a cycle through GTK the collector cannot see, and a dropdown
    a page replaces would never be freed. `selected` takes it as its signal's first argument
    and keeps its choice in `chosen`, which `bind` reads.
    """
    factory = Gtk.SignalListItemFactory()
    #: The bound entries and their checks. GTK keeps them bound between openings
    #: of the popover, so a check set only at bind would go stale.
    bound: dict[Gtk.ListItem, Gtk.Image] = {}
    #: The entry selected, as `selected` saw it last.
    chosen = [dropdown.get_selected()]

    def setup(_factory: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
        box = Gtk.Box(spacing=6)
        box.append(Gtk.Label(xalign=0, hexpand=True))
        # Presentation, as GTK's and libadwaita's own checks are: the entry's
        # selected state is what assistive technology reads.
        box.append(Gtk.Image(icon_name=CHECK_ICON,
                             accessible_role=Gtk.AccessibleRole.PRESENTATION))
        item.set_child(box)

    def bind(_factory: Gtk.SignalListItemFactory | None, item: Gtk.ListItem) -> None:
        # `setup` put the box in the item, with its label first and its check last, and the
        # model is the Gtk.StringList the docstrings ask for.
        box = cast(Gtk.Box, item.get_child())
        label = cast(Gtk.Label, box.get_first_child())
        check = cast(Gtk.Image, box.get_last_child())
        label.set_text(cast(Gtk.StringObject, item.get_item()).get_string())
        # Opacity rather than visibility, so every row keeps the same width.
        check.set_opacity(1.0 if item.get_position() == chosen[0] else 0.0)
        describe(item, box, label)
        bound[item] = check

    def unbind(_factory: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
        bound.pop(item, None)

    def selected(row: Gtk.DropDown | Adw.ComboRow, _pspec: object) -> None:
        chosen[0] = row.get_selected()
        for item, check in bound.items():
            check.set_opacity(1.0 if item.get_position() == chosen[0] else 0.0)

    def redraw() -> None:
        for item in list(bound):
            bind(None, item)

    factory.connect("setup", setup)
    factory.connect("bind", bind)
    factory.connect("unbind", unbind)
    dropdown.set_list_factory(factory)
    dropdown.connect("notify::selected", selected)
    return redraw
