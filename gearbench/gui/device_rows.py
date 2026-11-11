"""A device tab's Status and Battery rows (status_rows), and what they show once the device
or the daemon is gone (show_gone). Building them runs nothing."""

from __future__ import annotations

from gi.repository import Adw, GObject, Gtk

from ..wording import capitalized
from .style import NoticesRow, displayable, set_dot, show_battery, status_dot, widgets_under


class StatusRow(Adw.ActionRow):
    """A device tab's Status row, whose node (show_node) is its tooltip for the pointer and,
    since a tooltip alone reaches neither the keyboard nor a screen reader, a hidden label
    that the row's description reads after its subtitle (the owner's decision of
    2026-10-31)."""

    def __init__(self) -> None:
        super().__init__(title="Status", use_markup=False)
        self._node = Gtk.Label(visible=False)
        self.add_suffix(self._node)
        # Libadwaita describes the row by its subtitle label, a private child: the relation
        # is set again with the node's label after it. Taken from the W3C accessible name
        # computation, step 2A, which GTK follows: a hidden label a relation names still
        # counts. Not yet heard with a screen reader.
        subtitle = next(widget for widget in widgets_under(self)
                        if isinstance(widget, Gtk.Label) and widget.has_css_class("subtitle"))
        both = Gtk.AccessibleList.new_from_list([subtitle, self._node])
        self.update_relation([Gtk.AccessibleRelation.DESCRIBED_BY],
                             [GObject.Value(GObject.type_from_name("GtkAccessibleList"), both)])

    def show_node(self, node: str | None) -> None:
        """The device's node, /dev/hidrawN, or None where there is none to show."""
        self.set_tooltip_text(node)
        self._node.set_label(node or "")


def status_rows(group: Adw.PreferencesGroup
                ) -> tuple[StatusRow, Gtk.Image, Adw.ActionRow, Gtk.Image]:
    """A device tab's state row with its dot (set_dot) and its Battery row with its icon
    (show_battery), added to `group` and returned in that order. Not markup: the subtitles
    are the daemon's words and a node path. The Battery row has no subtitle or icon until
    the page's first show_battery."""
    state_row = StatusRow()
    dot = status_dot()
    state_row.add_prefix(dot)
    group.add(state_row)
    battery_row = Adw.ActionRow(title="Battery", use_markup=False)
    # Presentation: the row's subtitle says the reading in words.
    icon = Gtk.Image(valign=Gtk.Align.CENTER, accessible_role=Gtk.AccessibleRole.PRESENTATION)
    battery_row.add_prefix(icon)
    group.add(battery_row)
    return state_row, dot, battery_row, icon


def show_gone(state_row: StatusRow, dot: Gtk.Image, battery_row: Adw.ActionRow,
              battery_icon: Gtk.Image, notices: NoticesRow, why: str,
              state: bool | str = False) -> None:
    """The rows status_rows made, and the Battery notices row, once the device or the daemon
    is gone for the reason `why`: the dot at `state` (set_dot), `why` as the Status row's line,
    no node, no battery reading and no notices row. The Firmware row is the page's, since
    the A50 X has none."""
    set_dot(dot, state)
    state_row.set_subtitle(displayable(capitalized(why)))
    state_row.show_node(None)
    show_battery(battery_row, battery_icon, None, gone=True)
    notices.show(None, None)
