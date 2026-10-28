"""The window's primary menu: Game profiles, Import from G HUB, Import pack, Reload from disk,
the Tray icon check and About.

This part owns the menu, the button that opens it, the Tray icon action with the daemon's
switch behind it, and the About dialog. Its window builds it, puts the button in its header
and hands it each status reply's `tray`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from gi.repository import Gio, GLib, Gtk

from ..desktop import APP_NAME
from ..sni import REGISTERING
from ..wording import home_shortened, reason
from . import import_dialog, pack_dialog
from .about import about_dialog, gather
from .style import toast_note

if TYPE_CHECKING:                       # pragma: no cover
    from .window import Window

#: The toast after Tray icon is turned on where the icon does not show, the daemon's reason
#: after it, or alone, its reason behind Details, where that is too long for a toast.
TRAY_NOT_SHOWN = "Tray icon not shown"
#: The menu button's tooltip and its name for a screen reader.
MAIN_MENU = "Main menu"


class MainMenu:
    """The header's menu and what its items do.

    `window` takes the menu's actions, and its Game profiles dialog, its worker, its `call`,
    its `resync` and its Keyboard tab's reload are the ones the items use; the import's file
    chooser, the pack and the About dialog open over it.
    """

    def __init__(self, window: Window) -> None:
        self.win = window
        #: The daemon's tray icon switch as the last status gave it, read only after one.
        self.tray = False
        #: Clicks on Tray icon not answered yet: a status read before one lands has the old
        #: value, so it leaves the check as clicked.
        self._tray_unanswered = 0
        #: A click turned the icon on, and no status since has said whether it shows: the
        #: first that says why it does not is toasted (say_why_not).
        self._why_owed = False
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
            ("import-pack", lambda *_a: pack_dialog.open_pack(self.win)),
            # The files serve every device, so the menu has it for a window with no keyboard.
            ("reload", lambda *_a: self.win.keyboard.on_reload(None)),
            ("about", lambda *_a: self.about()),
        ])
        self.win.add_action(self.tray_action)
        self.model = Gio.Menu()
        self.model.append("Game profiles…", "win.game-profiles")
        self.model.append("Import from G HUB…", "win.import-ghub")
        self.model.append("Import pack…", "win.import-pack")
        self.model.append("Reload from disk…", "win.reload")
        tray_item = Gio.MenuItem.new("Tray icon", "win.tray")
        tray_item.set_attribute_value("hidden-when", GLib.Variant.new_string("action-disabled"))
        tray_section = Gio.Menu()
        tray_section.append_item(tray_item)
        self.model.append_section(None, tray_section)
        about_section = Gio.Menu()
        about_section.append(f"About {APP_NAME}", "win.about")
        self.model.append_section(None, about_section)
        button = Gtk.MenuButton(icon_name="open-menu-symbolic", primary=True,
                                menu_model=self.model, tooltip_text=MAIN_MENU)
        # An icon alone: its name for a screen reader, as the mute buttons set theirs.
        button.update_property([Gtk.AccessibleProperty.LABEL], [MAIN_MENU])
        return button

    def about(self) -> None:
        """The About dialog, its debug information gathered on the worker (`about.gather`)."""
        dialog = about_dialog()

        def gathered(result: str | Exception) -> None:
            if isinstance(result, str):
                dialog.set_debug_info(result)
                return
            where = (f"{result.filename}: " if isinstance(result, OSError) and result.filename
                     else "")
            # Pasted into a public issue, as debug_info's text is: no login in a path.
            dialog.set_debug_info(home_shortened(
                f"The debugging information could not be gathered: {where}{reason(result)}."))
        self.win.worker.run(gather, gathered)
        dialog.present(self.win)

    def take_tray(self, status: dict) -> None:
        """The daemon's switch, put on the check unless a click is unanswered (on_tray), and
        why the icon does not show, where a click turned it on."""
        tray = status.get("tray") or {}
        self.tray = bool(tray.get("on"))
        if not self._tray_unanswered:
            self.show_tray()
            if self._why_owed:
                self.say_why_not(tray)

    def say_why_not(self, tray: dict) -> None:
        """Toast why the icon the last click turned on does not show, as `status` and
        `gearbench-ctl tray` give it; nothing once it is off or shows. While it is still
        registering with the tray the next status says."""
        why = tray.get("why")
        if tray.get("on") and not tray.get("shown"):
            if not isinstance(why, str) or why == REGISTERING:
                return
            toast_note(self.win.toast, f"{TRAY_NOT_SHOWN}: {why}", TRAY_NOT_SHOWN,
                       one_sentence=True)
        self._why_owed = False

    def show_tray(self) -> None:
        """The Tray icon item as the daemon has it, shown from the first status (the action
        starts disabled, which hides it). Setting the state sends nothing."""
        self.tray_action.set_enabled(True)
        self.tray_action.set_state(GLib.Variant.new_boolean(self.tray))

    def on_tray(self, action: Gio.SimpleAction, value: GLib.Variant) -> None:
        """Send the check and read it back, so a refusal puts it back. A click is
        compared with the check as shown, not the daemon's last word, so a second click
        before the readback is sent too; the worker's order makes the last one win. A status
        read before a click lands, and the refusal of one with a later click unanswered, leave
        the check as clicked: the readback after the last click sets it."""
        wanted = value.get_boolean()
        # Disabled until the first status (show_tray). A state: __init__ made the action with one.
        if (not action.get_enabled()
                or wanted == cast(GLib.Variant, action.get_state()).get_boolean()):
            return
        action.set_state(value)
        self._tray_unanswered += 1

        def done(reply: dict | None) -> None:
            self._tray_unanswered -= 1
            # The readback says why it does not show; a later click's answer decides anew.
            self._why_owed = wanted and reply is not None
            if reply is None and not self._tray_unanswered:
                self.show_tray()
            self.win.resync()
        self.win.call({"command": "tray", "value": wanted}, done)
