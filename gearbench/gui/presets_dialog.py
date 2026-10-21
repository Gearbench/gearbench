"""The Astro A50's page's Load preset to headset dialog, over the presets Gearbench keeps
apart from the base station's three slots (gearbench.a50_library), read and saved on the
worker thread, as the G432's page saves its presets. Each preset is a row with its gains,
Load, which the page sends to the slot picked under Load into through the daemon as any edit,
and a menu of Rename…, Replace with the active preset, which takes the sound of the slot
playing, and Delete. The library is read when the dialog opens and at the page's polls while
it is open; Save as new preset saves into it with the dialog closed too. Building it runs
nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import cast

from gi.repository import Adw, Gio, GLib, Gtk

from .. import a50_library, astro, astroeq, eq_presets
from ..wording import reason
from .style import (
    LIST_DIALOG_HEIGHT,
    LIST_DIALOG_WIDTH,
    astroeq_dialog,
    confirm,
    displayable,
    name_refusal,
    picked_path,
    plain_row,
    toast_not_saved,
    toast_note,
)
from .worker import DaemonWorker, Result

TITLE = "Load preset to headset"
HELP = "Stored on this computer; one loaded into a slot stays there after Save."
#: The row that stands in for the list while it is empty.
EMPTY = "No saved presets yet"
EMPTY_HELP = "Save as new preset… in the Equalizer group adds one"
IMPORT = "Add from Astro Command Center…"
#: A preset's Load button, and its name for a screen reader, which tells the rows apart.
LOAD = "Load"
LOAD_NAME = "Load {}"
#: A preset's menu button, its name for a screen reader, its actions' group, and its items.
MORE = "More"
MORE_NAME = "More for {}"
ACTIONS = "preset"
RENAME = "Rename…"
REPLACE = "Replace with the active preset"
DELETE = "Delete"
#: The slot choice's group, and each slot's row and radio button.
LOAD_INTO = "Load into"
SLOT = "Slot {}"
#: The Rename dialog.
RENAME_HEADING = "Rename preset"
RENAME_BUTTON = "Rename"
#: Delete's confirmation.
DELETE_HEADING = "Delete {}?"
DELETE_BODY = "A slot it was loaded into keeps it. This cannot be undone."
#: The Save as new preset dialog.
SAVE_HEADING = "Save as new preset"
SAVE_BODY = "Load preset to headset… puts it in a slot."
SAVE_BUTTON = "Save"
NAME = "Preset name"
#: Each action's toast line when its failure is too long for a toast, the whole failure
#: behind Details.
NOT_SAVED = "The preset was not saved"
NOT_RENAMED = "The name did not change"
NOT_REPLACED = "The preset was not replaced"
NOT_DELETED = "The preset was not deleted"
NOT_IMPORTED = "The file was not added"
NOT_LOADED = "The saved presets could not be read"
#: An import's refusal of the file, with its name and why.
IMPORT_REFUSED = "{} could not be added: {}"


def gains_text(preset: astro.EqPreset) -> str:
    """A preset's five gains, as a row's subtitle gives them."""
    return " ".join(f"{gain:+d}" if gain else "0" for gain in preset.gains) + " dB"


def ask_name(parent: Gtk.Widget, name: str, then: Callable[[str], None],
             heading: str = SAVE_HEADING, body: str = SAVE_BODY,
             response: str = SAVE_BUTTON) -> Adw.AlertDialog:
    """Ask for a preset's name, `name` to start, under `heading`; `then` takes the name on
    `response`, which waits for one name_refusal takes."""
    dialog = Adw.AlertDialog(heading=heading, body=body)
    key = response.lower()
    entry = Gtk.Entry(text=name)
    entry.update_property([Gtk.AccessibleProperty.LABEL], [NAME])
    dialog.set_extra_child(entry)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response(key, response)
    dialog.set_response_appearance(key, Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response(key)
    dialog.set_close_response("cancel")
    dialog.set_response_enabled(key, name_refusal(entry.get_text().strip()) is None)

    # The entry's handlers find the dialog as its ancestor: holding it would make a cycle
    # through GTK that Python's collector cannot see (style.weakly).
    def allowed(entry: Gtk.Entry) -> None:
        if (found := entry.get_ancestor(Adw.AlertDialog)) is not None:
            found.set_response_enabled(key, name_refusal(entry.get_text().strip()) is None)

    def entered(entry: Gtk.Entry) -> None:
        # Enter in the name is the response, as the game profile's name dialog has it.
        found = entry.get_ancestor(Adw.AlertDialog)
        if found is not None and found.get_response_enabled(key):
            found.emit("response", key)
            found.force_close()

    entry.connect("changed", allowed)
    entry.connect("activate", entered)
    dialog.connect("response", lambda _d, answer: then(entry.get_text().strip())
                   if answer == key else None)
    dialog.present(parent)
    return dialog


class PresetsDialog:
    """Load preset to headset: `write(slot, preset)` sends a preset to a slot through the
    page, `active()` gives the active slot's preset as the page shows it, None while it is
    not read whole; `parent` is the window, which the dialog and the import's file dialog
    open over. The dialog is built once and presented again at each open."""

    def __init__(self, parent: Gtk.Window, worker: DaemonWorker, toast: Callable[..., object],
                 write: Callable[[int, astro.EqPreset], None],
                 active: Callable[[], astro.EqPreset | None]) -> None:
        self._parent = parent
        self._worker = worker
        self._toast = toast
        self._write = write
        self._active = active
        #: The library the last read or action gave, None before one or after one found the
        #: file unread, and the sentence that says why.
        self.library: a50_library.Library | None = None
        self._unread: str | None = None
        #: The slots the page gave, by number, and the active one; the slot Load writes to.
        self._slots: tuple[int, ...] = astro.PRESETS
        self._active_slot: int | None = None
        self.slot: int | None = None
        #: The dialog is up; the base station takes a write; a read is out; the window
        #: closed.
        self.is_open = False
        self._writable = False
        self._pending = False
        self._closed = False
        #: How many actions are out: one can start while another runs.
        self._acting = 0
        #: Each preset's row, its Load button and its Replace action, by number from 1.
        self.rows: list[Adw.ActionRow] = []
        self._loads: list[Gtk.Button] = []
        self.replace_actions: list[Gio.SimpleAction] = []
        self.dialog = self._build()

    @property
    def busy(self) -> bool:
        """An action is out, until the last of those out ends."""
        return self._acting > 0

    def _build(self) -> Adw.Dialog:
        self.group = Adw.PreferencesGroup(description=HELP)
        self.empty_row = plain_row(Adw.ActionRow, EMPTY, EMPTY_HELP, visible=False)
        self.group.add(self.empty_row)
        slots = Adw.PreferencesGroup(title=LOAD_INTO)
        self.slot_rows: list[Adw.ActionRow] = []
        self.slot_checks: list[Gtk.CheckButton] = []
        for slot in astro.PRESETS:
            check = Gtk.CheckButton(valign=Gtk.Align.CENTER)
            if self.slot_checks:
                check.set_group(self.slot_checks[0])
            check.update_property([Gtk.AccessibleProperty.LABEL], [SLOT.format(slot)])
            check.connect("toggled", self.on_slot, slot)
            row = plain_row(Adw.ActionRow, SLOT.format(slot), "", activatable_widget=check)
            row.add_prefix(check)
            slots.add(row)
            self.slot_rows.append(row)
            self.slot_checks.append(check)
        adding = Adw.PreferencesGroup()
        self.import_row = Adw.ButtonRow(title=IMPORT, start_icon_name="document-open-symbolic")
        self.import_row.connect("activated", self.on_import)
        adding.add(self.import_row)
        page = Adw.PreferencesPage()
        for group in (self.group, slots, adding):
            page.add(group)
        view = Adw.ToolbarView(content=page)
        view.add_top_bar(Adw.HeaderBar())
        # The window's toasts are under the dialog (Window.toast_overlay).
        self.toasts = Adw.ToastOverlay(child=view)
        dialog = Adw.Dialog(title=TITLE, content_width=LIST_DIALOG_WIDTH,
                            content_height=LIST_DIALOG_HEIGHT, child=self.toasts)
        dialog.connect("closed", self._on_closed)
        self._set_sensitive()
        return dialog

    # -- opening ------------------------------------------------------------

    def open(self) -> None:
        """Present the dialog over the window, Load into on the active slot, and read the
        library."""
        if self.is_open:
            return
        self.is_open = True
        self._pick(self._active_slot)
        self.dialog.present(self._parent)
        self.refresh()

    def _on_closed(self, _dialog: Adw.Dialog) -> None:
        self.is_open = False

    def toast_overlay(self) -> Adw.ToastOverlay | None:
        """Where the window's messages go while the dialog is up; None while it is not."""
        return self.toasts if self.is_open else None

    # -- reading ------------------------------------------------------------

    def refresh(self) -> None:
        """Read the library on the worker while the dialog is up, unless a read is out or an
        action runs."""
        if not self.is_open or self._pending or self.busy or self._closed:
            return
        self._pending = True
        self._worker.run(a50_library.load_library, self._loaded)

    def _loaded(self, result: Result) -> None:
        self._pending = False
        if not self._closed and not self.busy:
            self._take(result, NOT_LOADED)

    def _take(self, result: Result, failed: str) -> None:
        """A read's or an action's result: the library shown, or the failure said, in the
        list's description for a library that does not read, which no action may then
        change, else in a toast. The description also says what a library read in part left
        out, while its rows load and each change is refused (a50_library.Library.whole)."""
        if isinstance(result, a50_library.Library):
            self._unread = None
            self.show(result)
        elif isinstance(result, a50_library.LibraryUnread):
            self._unread = str(result)
            self.show(None)
        elif isinstance(result, OSError):
            # Only a50-eq.json's save raises one.
            toast_not_saved(self._toast, a50_library.library_path(), result)
        else:
            # A refusal in words: the work gives a Library or raises.
            toast_note(self._toast, f"Saved presets: {result}", failed, one_sentence=True)
        # A group's description is markup; each sentence quotes the file's path, a lone
        # surrogate in it as U+FFFD (displayable), and its cause, a wrong version's value or
        # a preset's name among them.
        said = self._unread or (self.library.left_out if self.library else "")
        self.group.set_description(GLib.markup_escape_text(displayable(said)) if said else HELP)
        self._set_sensitive()

    def show(self, library: a50_library.Library | None) -> None:
        """A row for each preset of `library`, none for None, built again only when the
        presets differ from those shown."""
        shown = () if self.library is None else self.library.presets
        self.library = library
        presets = () if library is None else library.presets
        self.empty_row.set_visible(library is not None and not presets)
        if presets == shown:
            return
        for row in self.rows:
            self.group.remove(row)
        self.rows, self._loads, self.replace_actions = [], [], []
        for number, preset in enumerate(presets, 1):
            row = self._row(number, preset)
            self.rows.append(row)
            self.group.add(row)

    def _row(self, number: int, preset: astro.EqPreset) -> Adw.ActionRow:
        """One preset's row: its name and gains, Load, and its menu."""
        row = plain_row(Adw.ActionRow, preset.name, gains_text(preset))
        load = Gtk.Button(label=LOAD, valign=Gtk.Align.CENTER)
        load.update_property([Gtk.AccessibleProperty.LABEL], [LOAD_NAME.format(preset.name)])
        load.connect("clicked", self.on_load, number)
        row.add_suffix(load)
        self._loads.append(load)
        actions = Gio.SimpleActionGroup()
        for name, handler in (("rename", self.on_rename), ("replace", self.on_replace),
                              ("delete", self.on_delete)):
            action = Gio.SimpleAction(name=name)
            action.connect("activate", handler, number)
            actions.add_action(action)
        self.replace_actions.append(cast("Gio.SimpleAction", actions.lookup_action("replace")))
        row.insert_action_group(ACTIONS, actions)
        menu = Gio.Menu()
        menu.append(RENAME, f"{ACTIONS}.rename")
        menu.append(REPLACE, f"{ACTIONS}.replace")
        # The destructive item in a section of its own, at the end, as the HIG has it.
        last = Gio.Menu()
        last.append(DELETE, f"{ACTIONS}.delete")
        menu.append_section(None, last)
        more = Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu, tooltip_text=MORE,
                              valign=Gtk.Align.CENTER, css_classes=["flat"])
        more.update_property([Gtk.AccessibleProperty.LABEL], [MORE_NAME.format(preset.name)])
        row.add_suffix(more)
        return row

    def show_slots(self, slots: list[int], names: list[str | None], active: int) -> None:
        """The base station's slots the page read, by number, with their names, and the
        active one, one of them, which Load into starts on when the dialog opens."""
        self._slots = tuple(slots)
        for slot, row in zip(astro.PRESETS, self.slot_rows, strict=True):
            row.set_visible(slot in slots)
            name = names[slots.index(slot)] if slot in slots else None
            row.set_subtitle(displayable(name) if name else "")
        self._active_slot = active
        self._set_sensitive()

    def _pick(self, slot: int | None) -> None:
        self.slot = slot
        if slot is not None:
            self.slot_checks[astro.PRESETS.index(slot)].set_active(True)

    def on_slot(self, check: Gtk.CheckButton, slot: int) -> None:
        if check.get_active():
            self.slot = slot
            self._set_sensitive()

    def set_writable(self, writable: bool) -> None:
        """Whether the base station takes a write now: attached, answering and not saving."""
        self._writable = writable
        self._set_sensitive()

    def _set_sensitive(self) -> None:
        usable = self.library is not None and not self.busy
        self.import_row.set_sensitive(usable)
        for row in self.rows:
            row.set_sensitive(usable)
        for load in self._loads:
            load.set_sensitive(self._writable and self.slot in self._slots)
        for swap in self.replace_actions:
            swap.set_enabled(self._active() is not None)

    # -- the actions --------------------------------------------------------

    def save_new(self, preset: astro.EqPreset) -> None:
        """Save `preset` after the last, the page's Save as new preset."""
        self._act(lambda: a50_library.edit(lambda library: library.added(preset)), NOT_SAVED)

    def _preset(self, number: int) -> astro.EqPreset:
        """Preset `number` of the library the rows show: a row is there only while one read
        (show)."""
        return cast("a50_library.Library", self.library).get(number)

    def on_load(self, _button: Gtk.Button, number: int) -> None:
        """Load is sensitive only while a slot the page gave is picked (_set_sensitive)."""
        self._write(cast(int, self.slot), self._preset(number))

    def on_rename(self, _action: Gio.SimpleAction, _value: object, number: int) -> None:
        """Ask the new name over the dialog, then save it."""
        shown = self._preset(number)

        def rename(name: str) -> None:
            self._act(lambda: a50_library.edit(lambda library: library.named(
                eq_presets.number_named(library.presets, number, shown.name), name)),
                NOT_RENAMED)
        ask_name(self.dialog, shown.name, rename, RENAME_HEADING, "", RENAME_BUTTON)

    def on_replace(self, _action: Gio.SimpleAction, _value: object, number: int) -> None:
        """The active slot's gains and bands as the page shows them, under this preset's
        name. Asked again here: a reply with no active preset gives the dialog no slots
        (HeadsetPage.show_presets), so the action's enabled state can be a reply old."""
        if (active := self._active()) is None:
            return
        shown = self._preset(number)

        def change(library: a50_library.Library) -> a50_library.Library:
            return library.put(eq_presets.number_named(library.presets, number, shown.name),
                               replace(active, name=shown.name))
        self._act(lambda: a50_library.edit(change), NOT_REPLACED)

    def on_delete(self, _action: Gio.SimpleAction, _value: object, number: int) -> None:
        """The preset row `number` shows deleted once asked, where the file holds it then
        (eq_presets.number_named)."""
        shown = self._preset(number)
        confirm(self.dialog, DELETE_HEADING.format(shown.name), DELETE_BODY, DELETE,
                lambda: self._act(lambda: a50_library.edit(lambda library: library.deleted(
                    eq_presets.number_named(library.presets, number, shown.name))),
                    NOT_DELETED))

    def on_import(self, _row: Adw.ButtonRow) -> None:
        astroeq_dialog().open(self._parent, None, self._picked)

    def _picked(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
        if (path := picked_path(dialog, result, self._toast)) is not None:
            self.import_path(path)

    def import_path(self, path: Path) -> None:
        """Read the .astroeq file at `path` on the worker, then add it after the last."""
        def parsed(result: Result) -> None:
            if self._closed:
                return
            if isinstance(result, astro.EqPreset):
                self.save_new(result)
            else:
                # The work gives a preset or raises.
                why = reason(cast(Exception, result))
                toast_note(self._toast, IMPORT_REFUSED.format(path.name, why), NOT_IMPORTED,
                           one_sentence=True)
        self._worker.run(lambda: astroeq.load_astroeq(path), parsed)

    def _act(self, work: Callable[[], a50_library.Library], failed: str) -> None:
        """Run `work` on the worker; `failed` is the toast line for a failure too long for
        a toast."""
        self._acting += 1
        self._set_sensitive()
        self._worker.run(work, lambda result: self._acted(result, failed))

    def _acted(self, result: Result, failed: str) -> None:
        if self._closed:
            return
        self._acting -= 1
        self._take(result, failed)

    def close(self) -> None:
        self._closed = True
