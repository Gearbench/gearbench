"""The Keyboard tab's Game profile group: which profile is loaded and why, and saving one.

The window holds the library and the layer these rows show. This card owns the rows and
their handlers; its page builds it, places its group and says when to show the rows again.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from gi.repository import Adw, GLib, GObject, Gtk

from ..config import unread_reason
from ..focus import FOLLOWED, SHELL, held_until
from ..profiles import GameProfile
from ..wording import word_list
from .profile_manager import programs_text
from .style import LeftOutRows, confirm

if TYPE_CHECKING:                       # pragma: no cover
    from .keyboard_page import SendSwitch
    from .window import Window
    from .worker import LoadingGuard

#: The Loaded row's first entry: the M profile alone.
NO_OVERLAY = "No game profile"


def held_off_text(profile: str, process: str | None,
                  followed: bool = False, more: int = 0) -> tuple[str, str]:
    """The held-off row's title and subtitle, from `status`'s `held_off`, the
    latest of `held_off_all`. `followed`: game profiles follow the focused
    window, so the hold-off ends when focus leaves the program, not when it
    exits. `more`: the other hold-offs, which Load it now releases too."""
    program = process or "its program"
    until = held_until(program, followed)
    said = "Another choice was made by hand"
    if more:
        said += (f". {more} more {'is' if more == 1 else 'are'} held off; Load it now "
                 f"lets them all load")
    return (f"{profile} is held off until {until}", said)


#: The Keep loaded switch's subtitle, by what a game profile follows.
KEEP_LOADED_HELP = "A profile loaded by hand stays when another game starts"
KEEP_LOADED_FOCUS = "A profile loaded by hand stays when another game gets focus"
#: The focus row's subtitle: what is followed, and what is not seen.
FOCUS_HELP = "A game profile is active while its program's window has focus"
#: Its subtitle while focus comes from Xwayland, without Gearbench's GNOME Shell extension.
FOCUS_HELP_X11 = FOCUS_HELP + ". A native Wayland window counts as no program"


def focus_text(focus: tuple[str, str | None, str | None], overlay: str | None,
               auto: bool, keep_loaded: bool | None) -> str:
    """The focus row's title while game profiles follow the focused window
    (the owner's decision of 2026-09-24): what is active, and why.

    `focus` is `status`'s (source, program, profile): the focused program
    and the game profile it binds. Keep loaded and a hold-off are named, as
    they are why a focused game's profile is not the active one.
    """
    _source, program, profile = focus
    active = overlay or "M profile"
    if auto and overlay:
        return f"{overlay} active: {program or 'its program'} has focus"
    if not profile or not program:
        return f"{active} active: no game profile's program has focus"
    if profile == overlay:
        return f"{overlay} active: {program} has focus"
    if keep_loaded and overlay:
        return f"{overlay} active: kept loaded while {program} has focus"
    return f"{active} active: {program} has focus, and {profile} is held off"


def _first_under[W: Gtk.Widget](parent: Gtk.Widget, kind: type[W]) -> W:
    """The first `kind` under `parent`: an Adw.ComboRow has its popover and
    the list in it from the start, and hands out neither."""
    stack = [parent.get_first_child()]
    while stack:
        widget = stack.pop()
        if widget is None:
            continue
        if isinstance(widget, kind):
            return widget
        stack += [widget.get_next_sibling(), widget.get_first_child()]
    raise LookupError(f"no {kind.__name__} under {type(parent).__name__}")


class GameCard:
    """The Game profile group, and the commands its rows send.

    `loading` is the page's guard, held while a reply sets a row. `unanswered` is the page's
    count of writes not answered yet, by what they set, and `send_switch` its way to send a
    switch and read it back: Keep loaded is one. `flush_speed` sends a Speed slider move still
    settling, which goes before a change of layer.
    """

    def __init__(self, window: Window, loading: LoadingGuard, unanswered: Counter[str],
                 send_switch: SendSwitch, flush_speed: Callable[[], None]) -> None:
        self.win = window
        self._loading = loading
        self._unanswered = unanswered
        self._send_switch = send_switch
        self._flush_speed = flush_speed
        #: What the Loaded row lists, so its model is rebuilt only when that changes.
        self._game_names: list[str] = []
        #: A change the Loaded row waits to show until its popover closes.
        self._game_row_owed = False
        #: The Loaded row's `selected` when its popover last closed.
        self._selected_at_close = Gtk.INVALID_LIST_POSITION

    def build(self) -> Adw.PreferencesGroup:
        """The group and its rows, for the page to place."""
        group = self.group = Adw.PreferencesGroup(
            title="Game profile",
            description="Replaces the M profile's lighting, G-key and mouse button bindings "
                        "until unloaded.")
        # The Game profiles dialog edits every profile and loads nothing; the Loaded
        # row below chooses the one in force.
        manage = Gtk.Button(label="Manage…", valign=Gtk.Align.CENTER,
                            tooltip_text="Rename or delete game profiles and choose their "
                                         "programs")
        manage.connect("clicked", self.on_manage)
        group.set_header_suffix(manage)
        # A search in the list, for a long library (the owner's request of 2026-09-28),
        # by substring: libadwaita's default matches only a name's start. A pick
        # through the search still sets `selected` in the full list.
        self.game_row = Adw.ComboRow(title="Loaded", model=Gtk.StringList.new([NO_OVERLAY]),
                                     enable_search=True,
                                     search_match_mode=Gtk.StringFilterMatchMode.SUBSTRING)
        self.game_row.set_expression(
            Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
        self.game_row.connect("notify::selected", self.on_game_profile)
        # Its popover, part of the row from the start: the row waits while it
        # is open (sync_game_row).
        self._game_popover = _first_under(self.game_row, Gtk.Popover)
        self._game_popover.connect("closed", self._on_game_popover_closed)
        # After libadwaita's own handler, which sets `selected`.
        _first_under(self._game_popover, Gtk.ListView).connect_after(
            "activate", self._on_game_picked)
        group.add(self.game_row)
        # G HUB's persistent profile (the owner's decision of 2026-09-23): it holds a
        # profile loaded by hand. Shown while one is loaded.
        self.keep_row = Adw.SwitchRow(title="Keep loaded", visible=False)
        self.keep_row.connect("notify::active", self.on_keep_loaded)
        group.add(self.keep_row)
        # What is active and why, while game profiles follow the focused window and a
        # profile names a program (the owner's decision of 2026-09-24). Not markup:
        # profiles' and programs' names.
        self.focus_row = Adw.ActionRow(use_markup=False, visible=False)
        group.add(self.focus_row)

        # The programs that auto-load the loaded profile, shown while one is loaded.
        # They are edited in the Game profiles dialog, for any profile: edited here,
        # binding one would need the profile loaded by hand, and going back to the M
        # profile while its program ran would hold it off. Not markup: program names.
        self.programs_row = Adw.ActionRow(title="Auto-load", use_markup=False, visible=False)
        edit = Gtk.Button(label="Edit…", valign=Gtk.Align.CENTER,
                          tooltip_text="Choose the programs that auto-load this profile")
        edit.connect("clicked", self.on_edit_programs)
        self.programs_row.add_suffix(edit)
        group.add(self.programs_row)
        # A profile a program loaded, then replaced by hand, stays off until the
        # program exits (under focus, until focus leaves it), which can be all day.
        # This row says so, with a way to let it load (the owner's decision of
        # 2026-09-24). Not markup: a profile's and a program's names.
        self.held_row = Adw.ActionRow(use_markup=False, visible=False)
        let_load = Gtk.Button(label="Load it now", valign=Gtk.Align.CENTER)
        let_load.connect("clicked", self.on_let_load)
        self.held_row.add_suffix(let_load)
        group.add(self.held_row)

        actions = Adw.ActionRow(
            title="Save",
            subtitle="Save what is on screen as a game profile, or as this M profile's "
                     "default")
        save_as = Gtk.Button(label="Save as…", valign=Gtk.Align.CENTER)
        save_as.add_css_class("suggested-action")
        save_as.connect("clicked", self.on_save_as)
        actions.add_suffix(save_as)
        promote = Gtk.Button(label="Set as M default", valign=Gtk.Align.CENTER)
        promote.connect("clicked", self.on_promote)
        actions.add_suffix(promote)
        group.add(actions)
        #: The dimmed rows of the profile files left out (show_unreadable).
        self.unreadable = LeftOutRows(
            group, lambda name, why: f"Left out: {unread_reason(name, why)}")
        return group

    def show_unreadable(self, skipped: list[tuple[str, str]]) -> None:
        """A dimmed row for each game profile file left out: unreadable, or shadowed by
        another of the same name.

        The Loaded list leaves such a file out and the library warns only on the
        window's stderr, so without a row it looks like a profile that was deleted.
        """
        self.unreadable.show(skipped)

    def sync_game_row(self) -> None:
        """Put the library and the loaded profile on the Loaded row, under the
        caller's loading guard. Not while its popover is open, where
        libadwaita empties the search typed at any change of `selected`: the
        row catches up when the popover closes (the owner's decision of
        2026-09-29)."""
        win = self.win
        if self._game_popover.get_visible():
            self._game_row_owed = True
            return
        self._game_row_owed = False
        names = [NO_OVERLAY] + [p.name for p in win.library]
        # The daemon keeps a loaded profile whose file is now unreadable or gone: the tray
        # lists it too.
        if win.overlay and win.overlay not in names[1:]:
            names.append(win.overlay)
        if names != self._game_names:
            self._game_names = names
            # build made the row on a Gtk.StringList.
            model = cast(Gtk.StringList, self.game_row.get_model())
            model.splice(0, model.get_n_items(), names)
        self._loading.put_selected(self.game_row,
                                   names.index(win.overlay, 1) if win.overlay else 0)

    def _on_game_popover_closed(self, _popover: Gtk.Popover) -> None:
        """Catch the Loaded row up at the main loop's next turn: a pick closes
        the popover before libadwaita sets `selected`, by the place in the
        list shown. At the default priority, ahead of redraws, so a window
        that keeps drawing does not hold it back."""
        self._selected_at_close = self.game_row.get_selected()

        def catch_up() -> bool:
            if self._game_row_owed:
                with self._loading:
                    self.sync_game_row()
            return GLib.SOURCE_REMOVE

        GLib.idle_add(catch_up, priority=GLib.PRIORITY_DEFAULT)

    def _on_game_picked(self, _list: Gtk.ListView, _position: int) -> None:
        """A pick of the entry the held row still shows checked: `selected`
        does not change, so no notify::selected loads it."""
        if self.game_row.get_selected() == self._selected_at_close:
            self.on_game_profile(self.game_row, None)

    def show_layer(self) -> None:
        """Show Keep loaded while it applies, what focus makes active, and a game
        profile held off while there is one."""
        win = self.win
        self.keep_row.set_visible(bool(win.overlay))
        if win.keep_loaded is not None and not self._unanswered["keep_loaded"]:
            self._loading.put_active(self.keep_row, win.keep_loaded)
        self.keep_row.set_subtitle(KEEP_LOADED_FOCUS if self.follows_focus()
                                   else KEEP_LOADED_HELP)
        self.show_focus()
        self.show_held_off()

    def follows_focus(self) -> bool:
        """Whether the daemon says game profiles follow the focused window."""
        return self.win.focus is not None and self.win.focus[0] in FOLLOWED

    def show_focus(self) -> None:
        """The focus row, while game profiles follow the focused window and
        some game profile names a program."""
        win = self.win
        shown = self.follows_focus() and any(p.processes for p in win.library)
        self.focus_row.set_visible(shown)
        if shown:
            # A focus: follows_focus saw one.
            focus = cast(tuple[str, str | None, str | None], win.focus)
            # The status's profile: `overlay` changes only once the files
            # are read, and a profile just loaded would read as held off.
            self.focus_row.set_title(focus_text(focus, win.status_overlay,
                                                win.overlay_auto, win.keep_loaded))
            self.focus_row.set_subtitle(FOCUS_HELP if focus[0] == SHELL
                                        else FOCUS_HELP_X11)

    def show_held_off(self) -> None:
        """The held-off row, while the daemon holds a game profile off."""
        held = self.win.held_off
        self.held_row.set_visible(held is not None)
        if held is not None:
            title, subtitle = held_off_text(*held, followed=self.follows_focus(),
                                            more=self.win.held_off_more)
            self.held_row.set_title(title)
            self.held_row.set_subtitle(subtitle)

    def on_let_load(self, _b: Gtk.Button | None) -> None:
        """Send `let_load`, then read the daemon again. The command loads
        nothing; the daemon's next scan, due at once, loads the profile while
        its program runs."""
        # A speed still settling goes first, on the layer it was set on.
        self._flush_speed()
        self.win.call({"command": "let_load"}, lambda _reply: self.win.resync())

    def on_keep_loaded(self, row: Adw.SwitchRow, _p: GObject.ParamSpec) -> None:
        self._send_switch(row, command="keep_loaded", key="keep_loaded")

    def show_programs(self) -> None:
        """Say which programs load the loaded game profile. Hidden while none
        is loaded: the dialog has every profile's."""
        profile = self.win.game_profile(self.win.overlay)
        self.programs_row.set_visible(profile is not None)
        if profile is not None:
            self.programs_row.set_subtitle(programs_text(profile.processes))

    def on_manage(self, _b: Gtk.Button | None) -> None:
        self.win.manage_profiles()

    def on_edit_programs(self, _b: Gtk.Button | None) -> None:
        """The Game profiles dialog, open on the loaded profile."""
        self.win.manage_profiles(self.win.overlay)

    def on_game_profile(self, row: Adw.ComboRow, _p: GObject.ParamSpec | None) -> None:
        if self._loading.active:
            # A pick in the turn after a poll's put is dropped, and no poll draws
            # this row again: the catch-up its popover's close queued puts it back.
            self._game_row_owed = True
            return
        # build made the row on a Gtk.StringList.
        model = cast(Gtk.StringList, row.get_model())
        index = row.get_selected()
        if index >= model.get_n_items():
            return
        name = model.get_string(index)
        # By position: a game profile may be named as the first entry is.
        wanted = None if index == 0 else name
        # While a load or unload is unanswered, `overlay` is the read from before it,
        # and a pick back to it is a change too.
        if wanted == self.win.overlay and not self._unanswered["game_profile"]:
            return
        # A speed still settling goes first, on the layer it was set on.
        self._flush_speed()
        # The reply's resync redraws the row: catching it up from the popover
        # would show the old profile until then.
        self._game_row_owed = False
        self._unanswered["game_profile"] += 1

        def done(_reply: dict | None) -> None:
            self._unanswered["game_profile"] -= 1
            self.win.resync()
        self.win.call({"command": "unload"} if wanted is None
                      else {"command": "load", "value": name}, done)

    def on_save_as(self, _b: Gtk.Button | None) -> None:
        """Ask for a name. Enter saves, and Save waits for a name.

        The files are read again first, or a game profile written since the window last
        read them (import-pack, import-ghub, save-profile, a copied file) would be
        written over without the question _do_save_as asks: such a write need not move
        anything a status poll compares (Window.absorb). One written while the dialog
        is up need not be seen.
        """
        self.win.resync()
        dialog = Adw.AlertDialog(heading="Save game profile",
                                 body="It will appear in the Loaded list.")
        entry = Gtk.Entry(text=self.win.overlay or "", placeholder_text="Doom Eternal")
        # Its only text is the placeholder, an example name.
        entry.update_property([Gtk.AccessibleProperty.LABEL], ["Game profile name"])
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("save", "Save")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")
        dialog.set_response_enabled("save", bool(entry.get_text().strip()))
        # The entry's handlers find the dialog as its ancestor: holding it
        # would make a cycle through GTK that Python's collector cannot see,
        # and the closed dialog would never be freed (style.weakly).
        entry.connect("changed", self._save_name_changed)
        # Enter in the name is Save. Not through `activates-default`: the
        # entry's default action is the window's, not the dialog's, so it did
        # nothing, measured on libadwaita 1.9.
        entry.connect("activate", self._save_name_entered)
        dialog.connect("response", self._do_save_as, entry)
        dialog.present(self.win)

    @staticmethod
    def _save_name_changed(entry: Gtk.Entry) -> None:
        dialog = entry.get_ancestor(Adw.AlertDialog)
        if dialog is not None:
            dialog.set_response_enabled("save", bool(entry.get_text().strip()))

    @staticmethod
    def _save_name_entered(entry: Gtk.Entry) -> None:
        dialog = entry.get_ancestor(Adw.AlertDialog)
        if dialog is not None and dialog.get_response_enabled("save"):
            dialog.emit("response", "save")
            dialog.force_close()

    def _do_save_as(self, dialog: Adw.AlertDialog, response: str, entry: Gtk.Entry) -> None:
        """Save what is on screen as a game profile, asking first when the
        name is another game profile's.

        The daemon writes over a profile with the same name, by slug, and
        keeps its programs, so what is on screen would start loading for
        another game. The loaded profile's own name is not asked about: the
        daemon holds what shows, and saves it as it changes.
        """
        if response != "save":
            return
        name = entry.get_text().strip()
        existing = self.win.game_profile(name)
        loaded = self.win.game_profile(self.win.overlay)
        if existing is not None and existing is not loaded:
            self._confirm_replace(existing, name)
            return
        self._save_as(name)

    def _confirm_replace(self, existing: GameProfile, name: str) -> None:
        """Replace another game profile? Names the programs that load it."""
        programs = existing.processes
        confirm(self.win, f"Replace {existing.name}?",
                f"Saving as {name} writes what is on screen over {existing.name}."
                + (f" It still auto-loads for {word_list(programs, 'and')}." if programs else ""),
                "Replace", lambda: self._save_as(name))

    def _save_as(self, name: str) -> None:
        """Send `save_profile`, then load what was saved."""
        def saved(reply: dict | None) -> None:
            if reply is None:
                return
            self.win.toast(f"Saved {name}")
            self.win.call({"command": "load", "value": name},
                          lambda _reply: self.win.resync())
        self.win.call({"command": "save_profile", "value": name}, saved)

    def on_promote(self, _b: Gtk.Button | None) -> None:
        """Make what is on screen this M profile's default. The daemon reads the
        lighting itself, as it saves every change when it is made."""
        # A speed still settling goes first, so the default takes it.
        self._flush_speed()
        overlay = self.win.overlay

        def done(reply: dict | None) -> None:
            # Resync either way: it re-reads config.json on the worker, which
            # is what promote just rewrote, and puts a refused one back.
            self.win.resync()
            if reply:
                self.win.toast(f"{overlay} is now the M{self.win.active} default" if overlay
                               else f"Saved as the M{self.win.active} default")
        self.win.call({"command": "promote"}, done)
