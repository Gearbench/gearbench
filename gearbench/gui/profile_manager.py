"""The Game profiles dialog: each game profile with its name, the programs that auto-load it
and Delete, plus Import pack… (`pack_dialog`). Opened from the primary menu and the Keyboard
tab's Game profile group. Nothing here loads or suppresses a profile; only deleting the loaded
one unloads it. The filter matches a game's name or its programs (`import_dialog.Picker`), the
owner's request of 2026-09-28.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from gi.repository import Adw, Gtk

from ..apps import LAUNCHERS, launcher_warning, wanted_forms
from ..config import unread_reason
from ..profiles import GameProfile
from ..wording import word_list
from . import pack_dialog
from .import_dialog import FILTER_HINT, NO_MATCH, Picker, search_text
from .style import (
    LIST_DIALOG_HEIGHT,
    LIST_DIALOG_WIDTH,
    LeftOutRows,
    confirm,
    holds_focus,
    plain_row,
    weakly,
)
from .worker import DaemonWorker, Reply, unreachable

if TYPE_CHECKING:                       # pragma: no cover
    from .window import Window

TITLE = "Game profiles"
DESCRIPTION = ("A profile loads while one of its programs has focus, or runs where focus is not "
               "followed. Load one by hand from the Keyboard tab.")
MANUAL_ONLY = "Manual only"
NONE_YET = "No game profiles yet"
NONE_YET_HINT = ("Save one from the Keyboard tab with Save as…, or import the pack's with "
                 "Import pack…")
IMPORT_PACK = "Import pack…"
IMPORT_PACK_HINT = "Game lighting profiles and macros that come with Gearbench"
PICK_TITLE = "Add a running program"
LOOKING = "Looking at what is running…"
PICKER_HINT = "Programs running now. A game from a launcher such as Steam has its own name."


def programs_text(processes: list[str]) -> str:
    """What loads a profile, in one line: `Loads for a, b or c`."""
    return f"Loads for {word_list(processes)}" if processes else MANUAL_ONLY


def still_matching(processes: list[str], program: str) -> list[str]:
    """The other programs in `processes` whose apps.wanted_forms meet `program`'s: with
    `program` taken off, the profile still loads for it. A list set by `profile-programs` or by
    hand can hold two such, or a cut name beside its full name."""
    forms = wanted_forms(program)
    return [p for p in processes if p != program and not forms.isdisjoint(wanted_forms(p))]


def matches_too(others: list[str]) -> str:
    """`a matches it too`, `a and b match it too`."""
    return f"{word_list(others, 'and')} {'matches' if len(others) == 1 else 'match'} it too"


def picker_row(process: str, label: str) -> tuple[str, str]:
    """A row's title and subtitle in the program picker.

    A game in a launcher's scope carries the launcher's label, so it is titled with its own
    name; the launcher's own process, and any other program, with its app.
    """
    if label in LAUNCHERS.values() and LAUNCHERS.get(process.lower()) != label:
        return process, f"Running in {label}"
    return label, process


class ProfileManager:
    """The dialog, and what its rows send.

    `dialog` is the Adw.Dialog; `toasts` is its own overlay, which the window's messages go to
    while it is open, since the window's are under it. The window calls `refresh` whenever it
    has read the files.
    """

    def __init__(self, window: Window) -> None:
        self.win = window
        #: One row per profile, by name, kept while the profile is there, so
        #: a row that is open stays open when the files are read again.
        self.rows: dict[str, ProfileRow] = {}

        # Above the list, as in the pack's dialog. Every handler on a widget
        # inside the dialog goes through weakly: this object holds the dialog.
        self.search = Gtk.SearchEntry(placeholder_text=FILTER_HINT)
        # The placeholder is no name to a screen reader.
        self.search.update_property([Gtk.AccessibleProperty.LABEL], [FILTER_HINT])
        self.search.connect("search-changed", weakly(self._on_search))
        search_group = Adw.PreferencesGroup()
        search_group.add(self.search)
        self.group = Adw.PreferencesGroup(description=DESCRIPTION)
        self.none_row = Adw.ActionRow(title=NONE_YET, subtitle=NONE_YET_HINT, visible=False)
        self.group.add(self.none_row)
        self.no_match = Adw.ActionRow(title=NO_MATCH, css_classes=["dimmed"], visible=False)
        self.group.add(self.no_match)
        self.unreadable_group = Adw.PreferencesGroup(title="Left out", visible=False)
        # The group's title says Left out.
        self.unreadable = LeftOutRows(self.unreadable_group, unread_reason)
        page = Adw.PreferencesPage()
        page.add(search_group)
        page.add(self.group)
        page.add(self.unreadable_group)
        view = Adw.ToolbarView(content=page)
        header = Adw.HeaderBar()
        self.import_pack = Gtk.Button(label=IMPORT_PACK, tooltip_text=IMPORT_PACK_HINT)
        self.import_pack.connect("clicked", weakly(self._on_import_pack))
        header.pack_start(self.import_pack)
        view.add_top_bar(header)
        self.nav = Adw.NavigationView()
        self.nav.add(Adw.NavigationPage(child=view, title=TITLE, tag="profiles"))
        self.toasts = Adw.ToastOverlay(child=self.nav)
        self.dialog = Adw.Dialog(title=TITLE, content_width=LIST_DIALOG_WIDTH,
                                 content_height=LIST_DIALOG_HEIGHT, child=self.toasts)
        self.refresh()

    def _is_open(self) -> bool:
        # Window.manage_profiles makes this its profiles_dialog, and
        # _profiles_closed lets go of it on "closed".
        return self.win.profiles_dialog is self

    def _on_search(self, _entry: Gtk.SearchEntry) -> None:
        self._filter()

    def _on_import_pack(self, _button: Gtk.Button) -> None:
        pack_dialog.open_pack(self.win)

    # -- the rows -----------------------------------------------------------

    def refresh(self) -> None:
        """Put the window's copy of the library on the rows. Memory only."""
        library = self.win.library
        names = [p.name for p in library]
        if names != list(self.rows):
            for row in self.rows.values():
                self.group.remove(row.expander)
            self.rows = {name: self.rows.get(name) or ProfileRow(self, name)
                         for name in names}
            for row in self.rows.values():
                self.group.add(row.expander)
        self.none_row.set_visible(not library)
        loaded = self.win.game_profile(self.win.overlay)
        for profile in library:
            self.rows[profile.name].show(profile.processes, profile is loaded)
        self._filter()
        self._show_unreadable(self.win.skipped_profiles)

    def _filter(self) -> None:
        """Show the rows the filter matches. On each refresh too, which
        reads what is typed and changes none of it."""
        picker = Picker(self.win.library, search_text)
        picker.query = self.search.get_text().strip()
        shown = set(picker.visible())
        for name, row in self.rows.items():
            row.expander.set_visible(name in shown)
        self.no_match.set_visible(bool(self.rows) and not shown)

    def _show_unreadable(self, skipped: list[tuple[str, str]]) -> None:
        """A dimmed row per profile file left out, as on the tab."""
        self.unreadable.show(skipped)
        self.unreadable_group.set_visible(bool(skipped))

    def expand(self, name: str) -> None:
        """Open the row of the profile `name` finds, by slug as the daemon does."""
        profile = self.win.game_profile(name)
        row = self.rows.get(profile.name) if profile is not None else None
        if row is not None:
            row.expander.set_expanded(True)

    def pick(self, name: str) -> None:
        """Show the running programs, to add one to the profile `name`. Its
        rows hold on_pick, which holds this object weakly: gone with it."""
        picked, is_open = weakly(self._picked), weakly(self._is_open)
        # The pick goes to the profile's name then: a rename answered meanwhile changes it.
        profile = self.win.game_profile(name)
        picker = ProgramPicker(self.win.worker, name,
                               lambda process: picked(profile.name if profile is not None
                                                      else name, process),
                               lambda: not is_open())
        self.nav.push(picker.page)

    def _picked(self, name: str, process: str) -> None:
        self.nav.pop()
        self.add_program(name, process)

    # -- what the rows send -------------------------------------------------

    def _profile(self, name: str) -> GameProfile | None:
        """The profile `name` finds in the window's copy; or None, with a toast and the files
        read again, since the list on screen is out of date."""
        profile = self.win.game_profile(name)
        if profile is None:
            self.win.toast(f"No game profile named {name}")
            self.win.resync()
        return profile

    def add_program(self, name: str, program: str,
                    then: Callable[[], None] | None = None) -> None:
        """Add `program` to the profile `name`, asking first when it is a
        launcher. `then` runs as it is sent."""
        program = program.strip()
        if not program:
            self.win.toast("Type a program name")
            return
        profile = self._profile(name)
        if profile is None:
            return
        # Matching ignores case and ".exe", so a spelling that matches the same running names as
        # one already there adds nothing. Equal forms, not any shared one: two long names sharing
        # their 15-byte cut are two programs.
        there = next((p for p in profile.processes
                      if wanted_forms(p) == wanted_forms(program)), None)
        if there is not None:
            self.win.toast(f"{profile.name} already auto-loads for {there}")
            return

        def add() -> None:
            current = self._profile(profile.name)
            if current is None:
                return
            if then is not None:
                then()
            self.win.send_processes(current, [*current.processes, program],
                                    f"{current.name} auto-loads for {program}")

        warning = launcher_warning(program)
        if warning is None:
            add()
        else:
            self._ask(f"Add {program}?", warning, "Add", add)

    def remove_program(self, name: str, program: str) -> None:
        """Take a program off a profile, once the user confirms it (a delete that saves
        asks first: the owner's decision of 2026-09-27). Another program on the list
        that still matches it is named (still_matching)."""
        profile = self.win.game_profile(name)
        others = still_matching(profile.processes, program) if profile is not None else []
        body = (f"{name} still auto-loads for {program}: {matches_too(others)}." if others
                else f"{name} stops auto-loading for {program}.")
        # The profile's name at the answer, as delete's: a rename answered meanwhile changes it.
        self._ask(f"Remove {program}?", body, "Remove",
                  lambda: self._do_remove_program(profile.name if profile is not None else name,
                                                  program), destructive=True)

    def _do_remove_program(self, name: str, program: str) -> None:
        profile = self._profile(name)
        if profile is not None and program in profile.processes:
            self.win.send_processes(profile, [p for p in profile.processes if p != program], None)

    def rename(self, name: str, new: str, undo: Callable[[], None]) -> None:
        """Rename the profile `name` to `new`. The daemon moves its file and
        points everything that named it at the new name; the window takes
        the name from the reply and reads the files again. `undo()` runs
        for the caller to put the name back where it was typed, when `new`
        is empty or unchanged, or the daemon refuses or does not answer."""
        new = new.strip()
        profile = self._profile(name)
        if profile is None:
            return
        if not new or new == profile.name:
            if not new:
                self.win.toast("A profile needs a name")
            undo()
            return
        was = profile.name

        def done(reply: dict | None) -> None:
            if reply is None:
                undo()
                self.win.resync()
                return
            now = reply["name"]
            self.win.took_rename(was, now)
            row = self.rows.pop(was, None)
            if row is not None:
                row.rename(now)
                self.rows[now] = row
            self.win.refresh()
            self.win.toast(f"Renamed {was} to {now}")
            self.win.resync()
        self.win.call({"command": "rename_profile", "value": was, "to": new}, done)

    def delete(self, name: str) -> None:
        """Delete the profile `name`, once asked. The loaded one is unloaded
        by the daemon as it goes, and the dialog says so."""
        profile = self._profile(name)
        if profile is None:
            return
        body = "The file is removed. This cannot be undone."
        if self.win.game_profile(self.win.overlay) is profile:
            body += " It is loaded now, and deleting it unloads it."
        self._ask(f"Delete {profile.name}?", body, "Delete",
                  lambda: self._do_delete(profile.name), destructive=True)

    def _do_delete(self, name: str) -> None:
        """Say it once the daemon has, with the name it deleted, so a
        refusal shows its error alone."""
        def done(reply: dict | None) -> None:
            self.win.resync()
            if reply is not None:
                self.win.toast(f"Deleted {reply['deleted']}")
        self.win.call({"command": "delete_profile", "value": name}, done)

    def _ask(self, heading: str, body: str, response: str, then: Callable[[], None],
             destructive: bool = False) -> None:
        confirm(self.win, heading, body, response, then, destructive)


class ProfileRow:
    """One profile: its name, its programs, the two ways to add one, and
    Delete. Every row sends `self.name`, which a rename changes."""

    def __init__(self, manager: ProfileManager, name: str) -> None:
        self.name = name
        # Not markup: a profile's name and a program's are anyone's text.
        self.expander = Adw.ExpanderRow(title=name, use_markup=False)
        self.loaded = Gtk.Label(label="Loaded", valign=Gtk.Align.CENTER,
                                css_classes=["dimmed"], visible=False)
        self.expander.add_suffix(self.loaded)
        # On apply rather than on every keystroke, as the M profile's Name
        # row on the Keyboard tab: each one is a rename sent to the daemon.
        # Every handler through weakly: the row holds the manager, which
        # holds the dialog (ProfileManager.__init__).
        self.name_row = Adw.EntryRow(title="Name", text=name, show_apply_button=True)
        self.name_row.connect("apply", weakly(self._on_rename))
        self.pick = Adw.ActionRow(title=PICK_TITLE, activatable=True)
        # The arrow is decoration: without the role a screen reader meets an
        # image with no name.
        self.pick.add_suffix(Gtk.Image(icon_name="go-next-symbolic",
                                       accessible_role=Gtk.AccessibleRole.PRESENTATION))
        self.pick.connect("activated", weakly(self._on_pick))
        self.entry = Adw.EntryRow(title="Add by name", show_apply_button=True)
        self.entry.connect("apply", weakly(self._on_add))
        self.delete = Adw.ActionRow(title="Delete profile", subtitle="Removes its file")
        remove = Gtk.Button(label="Delete", valign=Gtk.Align.CENTER,
                            css_classes=["destructive-action"])
        remove.connect("clicked", weakly(self._on_delete))
        self.delete.add_suffix(remove)
        self._manager = manager
        self.program_rows: list[Adw.ActionRow] = []
        #: The list the rows were last built for.
        self.shown: list[str] | None = None

    def show(self, processes: list[str], loaded: bool) -> None:
        """Put the profile's programs on the row, rebuilding its rows only
        when the list changed. The focus stays where it was across that,
        unless it was on a program's row."""
        self.expander.set_subtitle(programs_text(processes))
        self.loaded.set_visible(loaded)
        if processes == self.shown:
            return
        self.shown = list(processes)
        root = self.expander.get_root()
        focus = root.get_focus() if root is not None else None
        # GTK moves the focus off a row taken out to the list it was in, at a later frame.
        held = next((row for row in (self.name_row, self.pick, self.entry, self.delete)
                     if holds_focus(focus, row)), None)
        for row in [self.name_row, *self.program_rows, self.pick, self.entry, self.delete]:
            if row.get_parent() is not None:
                self.expander.remove(row)
        self.program_rows = [self._program_row(p, processes) for p in processes]
        for row in [self.name_row, *self.program_rows, self.pick, self.entry, self.delete]:
            self.expander.add_row(row)
        if isinstance(held, Adw.EntryRow):
            # grab_focus would select the whole text, and the next
            # keystroke would replace what is being typed.
            held.grab_focus_without_selecting()
        elif held is not None and focus is not None:
            focus.grab_focus()

    def _on_rename(self, entry: Adw.EntryRow) -> None:
        # Back to the row's name as it is then: a rename answered meanwhile has moved it, and
        # the name this one was sent from may be gone.
        self._manager.rename(self.name, entry.get_text(),
                             undo=lambda: entry.set_text(self.name))

    def _on_pick(self, _row: Adw.ActionRow) -> None:
        self._manager.pick(self.name)

    def _on_add(self, entry: Adw.EntryRow) -> None:
        self._manager.add_program(self.name, entry.get_text(),
                                  then=lambda: entry.set_text(""))

    def _on_delete(self, _button: Gtk.Button) -> None:
        self._manager.delete(self.name)

    def _on_remove(self, _button: Gtk.Button, process: str) -> None:
        self._manager.remove_program(self.name, process)

    def rename(self, name: str) -> None:
        """Take the name the daemon renamed the profile to."""
        self.name = name
        self.expander.set_title(name)
        if self.name_row.get_text() != name:
            self.name_row.set_text(name)

    def _program_row(self, process: str, processes: list[str]) -> Adw.ActionRow:
        row = Adw.ActionRow(title=process, use_markup=False)
        others = still_matching(processes, process)
        tip = (f"Remove {process}. The profile still auto-loads for it: {matches_too(others)}"
               if others else f"Stop auto-loading for {process}")
        remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                            css_classes=["flat"], tooltip_text=tip)
        remove.update_property([Gtk.AccessibleProperty.LABEL], [f"Remove {process}"])
        remove.connect("clicked", weakly(self._on_remove), process)
        row.add_suffix(remove)
        return row


class ProgramPicker:
    """The programs running now, as a page pushed over the profile list.

    Lists what is running rather than asking for an executable name nobody can see; Add by
    name covers a program not running yet. `gone()` says the dialog has closed, since the
    `apps` command scans /proc and can answer after that.
    """

    def __init__(self, worker: DaemonWorker, name: str, on_pick: Callable[[str], object],
                 gone: Callable[[], bool]) -> None:
        self._on_pick = on_pick
        self._gone = gone
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE,
                                   css_classes=["boxed-list"])
        self.listbox.append(Adw.ActionRow(title=LOOKING))
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12,
                       margin_bottom=12, margin_start=12, margin_end=12)
        body.append(Gtk.Label(label=PICKER_HINT, wrap=True, xalign=0, css_classes=["dimmed"]))
        body.append(self.listbox)
        view = Adw.ToolbarView(content=Gtk.ScrolledWindow(
            child=Adw.Clamp(child=body), hscrollbar_policy=Gtk.PolicyType.NEVER,
            vexpand=True))
        # Not markup: the subtitle is the profile's name.
        title = Adw.WindowTitle(title=PICK_TITLE, subtitle=name)
        view.add_top_bar(Adw.HeaderBar(title_widget=title))
        self.page = Adw.NavigationPage(child=view, title=PICK_TITLE, tag="picker")
        worker.call({"command": "apps"}, self._fill)

    def _fill(self, reply: Reply) -> None:
        if self._gone():
            return
        self.listbox.remove_all()
        if isinstance(reply, Exception):
            self._say("Could not list what is running", unreachable(reply))
            return
        if not reply.get("ok"):
            self._say("Could not list what is running", str(reply.get("error") or ""))
            return
        apps = reply.get("apps") or []
        if not apps:
            self._say("Nothing running to pick",
                      "Start the program, or type its name in Add by name")
            return
        # Not self: a row holding the picker that holds its rows is a cycle
        # through GTK, and the page would never be freed.
        on_pick = self._on_pick
        for app in apps:
            process = str(app.get("process", ""))
            title, subtitle = picker_row(process, str(app.get("label", "")))
            # Not markup: a program's name and its app's are anyone's.
            row = plain_row(Adw.ActionRow, title, subtitle, activatable=True)
            row.connect("activated", lambda _r, p=process: on_pick(p))
            self.listbox.append(row)

    def _say(self, title: str, subtitle: str) -> None:
        """One row in place of the list. The subtitle is capitalized, as the tabs' status rows
        are: unreachable's words and the daemon's errors start lowercase."""
        self.listbox.append(plain_row(Adw.ActionRow, title, subtitle[:1].upper() + subtitle[1:]))
