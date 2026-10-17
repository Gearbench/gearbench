"""The window's primary menu: Game profiles, Import from G HUB, the Tray icon check and About.

This part owns the menu, the button that opens it, the Tray icon action with the daemon's
switch behind it, and the About dialog. Its window builds it, puts the button in its header
and hands it each status reply's `tray`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from gi.repository import Gio, GLib, Gtk

from ..desktop import APP_NAME
from . import import_dialog
from .about import about_dialog, gather

if TYPE_CHECKING:                       # pragma: no cover
    from .window import Window
    from .worker import Result


class MainMenu:
    """The header's menu and what its items do.

    `window` takes the menu's actions, and its Game profiles dialog, its worker, its `call`
    and its `resync` are the ones the items use; the import's file chooser and the About
    dialog open over it.
    """

    def __init__(self, window: Window) -> None:
        self.win = window
        #: The daemon's tray icon switch, or None until a status gives it.
        self.tray: bool | None = None
        #: Clicks on Tray icon not answered yet: a status read before one lands has the old
        #: value, so it leaves the check as clicked.
        self._tray_unanswered = 0
        #: The tray icon's switch, a check item hidden until the first status (show_tray; the
        #: owner's decision of 2026-09-26).
        self.tray_action = Gio.SimpleAction.new_stateful("tray", None,
                                                         GLib.Variant.new_boolean(False))
        self.tray_action.set_enabled(False)
        self.tray_action.connect("change-state", self.on_tray)

    def build(self) -> Gtk.MenuButton:
        """The primary menu and its actions, and the button that opens it."""
        # The primary menu, laid out as the HIG has it.
        self.win.add_action_entries([
            ("game-profiles", lambda *_a: self.win.manage_profiles()),
            ("import-ghub", lambda *_a: import_dialog.choose_file(self.win)),
            ("about", lambda *_a: self.about()),
        ])
        self.win.add_action(self.tray_action)
        self.model = Gio.Menu()
        self.model.append("Game profiles…", "win.game-profiles")
        self.model.append("Import from G HUB…", "win.import-ghub")
        tray_item = Gio.MenuItem.new("Tray icon", "win.tray")
        tray_item.set_attribute_value("hidden-when", GLib.Variant.new_string("action-disabled"))
        tray_section = Gio.Menu()
        tray_section.append_item(tray_item)
        self.model.append_section(None, tray_section)
        about_section = Gio.Menu()
        about_section.append(f"About {APP_NAME}", "win.about")
        self.model.append_section(None, about_section)
        return Gtk.MenuButton(icon_name="open-menu-symbolic", primary=True,
                              menu_model=self.model, tooltip_text="Main menu")

    def about(self) -> None:
        """The About dialog, its debug information gathered on the worker (`about.gather`)."""
        dialog = about_dialog()

        def gathered(result: Result) -> None:
            dialog.set_debug_info(result if isinstance(result, str)
                                  else f"The debug information could not be gathered: {result}")
        self.win.worker.run(gather, gathered)
        dialog.present(self.win)

    def take_tray(self, status: dict) -> None:
        """The daemon's switch, put on the check unless a click is unanswered (on_tray)."""
        self.tray = bool((status.get("tray") or {}).get("on"))
        if not self._tray_unanswered:
            self.show_tray()

    def show_tray(self) -> None:
        """The Tray icon item as the daemon has it, shown from the first status (the action
        starts disabled, which hides it). Setting the state sends nothing."""
        self.tray_action.set_enabled(True)
        # A bool: take_tray set it, and on_tray, the other way here, sends nothing before
        # that.
        self.tray_action.set_state(GLib.Variant.new_boolean(cast(bool, self.tray)))

    def on_tray(self, action: Gio.SimpleAction, value: GLib.Variant) -> None:
        """Send the check and read it back, so a refusal puts it back. A click is
        compared with the check as shown, not the daemon's last word, so a second click
        before the readback is sent too; the worker's order makes the last one win. A status
        read before a click lands, and the refusal of one with a later click unanswered, leave
        the check as clicked: the readback after the last click sets it."""
        wanted = value.get_boolean()
        # A state: __init__ made the action with one.
        if self.tray is None or wanted == cast(GLib.Variant, action.get_state()).get_boolean():
            return
        action.set_state(value)
        self._tray_unanswered += 1

        def done(reply: dict | None) -> None:
            self._tray_unanswered -= 1
            if reply is None and not self._tray_unanswered:
                self.show_tray()
            self.win.resync()
        self.win.call({"command": "tray", "value": wanted}, done)
