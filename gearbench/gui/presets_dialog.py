"""The Astro A50's page's Load preset to headset dialog, over the presets Gearbench keeps
apart from the base station's three slots (gearbench.a50_library), read and saved on the
worker thread, as the G432's page saves its presets. Each preset is a row with its gains,
Load, which the page sends to the slot picked under Load into through the daemon as any edit,
and a menu of Rename…, Replace with the active preset…, which takes the sound of the slot
playing, and Delete…, the last two asked first. The library is read at the page's polls
until one is taken (show), for the page's Save as new preset, which grays out with the
library full (saved), then when the dialog opens and at the page's polls while it is open;
Save as new preset saves into it with the dialog closed too. Building it runs nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import cast

from gi.repository import Adw, Gio, GLib, Gtk

from .. import a50_library, astro, astroeq, eq_presets
from ..wording import capitalized, printable, reason
from .style import (
    DELETE_HEADING,
    DELETE_OPENER,
    DELETE_RESPONSE,
    LIBRARY_UNREAD,
    LIST_DIALOG_HEIGHT,
    LIST_DIALOG_WIDTH,
    NOT_DELETED,
    NOT_RENAMED,
    NOT_SAVED_NEW,
    SAVE_NEW_PRESET,
    ask_name,
    astroeq_dialog,
    confirm,
    displayable,
    name_button,
    picked_path,
    plain_row,
    preset_label,
    toast_not_saved,
    toast_note,
)
from .worker import DaemonWorker, Result

TITLE = "Load preset to headset"
HELP = "Stored on this computer; one loaded into a slot stays there after Save"
#: The row that stands in for the list while it is empty.
EMPTY = "No presets in the library yet"
EMPTY_HELP = f"{SAVE_NEW_PRESET} in the Equalizer group adds one"
#: The row that adds a Command Center file to the library, and the title of the chooser it
#: opens, in its words: the A50's Import row writes a file into a slot, another job.
ADD_TITLE = "Add from Astro Command Center"
IMPORT = f"{ADD_TITLE}…"
#: A preset's Load button, and its name for a screen reader, which tells the rows apart.
LOAD = "Load"
LOAD_NAME = "Load {}"
#: A preset's menu button, its name for a screen reader, its actions' group, and its items.
MORE = "More"
MORE_NAME = "More for {}"
ACTIONS = "preset"
RENAME = "Rename…"
REPLACE = "Replace with the active preset…"
DELETE = DELETE_OPENER
#: The slot choice's group, and each slot's row and radio button.
LOAD_INTO = "Load into"
SLOT = "Slot {}"
#: The Rename dialog.
RENAME_HEADING = "Rename preset"
RENAME_BUTTON = "Rename"
#: Replace's confirmation, by the preset's name.
REPLACE_HEADING = "Replace {}?"
REPLACE_BODY = ("It takes the active slot's gains and bands. A slot it was loaded into keeps "
                "the old ones.")
REPLACE_RESPONSE = "Replace"
#: Delete's confirmation.
DELETE_BODY = "A slot it was loaded into keeps it. This cannot be undone."
#: The Save as new preset dialog.
SAVE_BODY = f"{TITLE}… puts it in a slot."
#: Each action's toast line when its failure is too long for a toast, the whole failure
#: behind Details.
NOT_REPLACED = "The preset was not replaced"
NOT_IMPORTED = "The file was not added"
NOT_LOADED = LIBRARY_UNREAD
#: An import's refusal of the file, with its name and why.
IMPORT_REFUSED = "{} could not be added: {}"


def gains_text(preset: astro.EqPreset) -> str:
    """A preset's five gains, as a row's subtitle gives them: each signed, a zero too, as
    signed writes one, and dB once at the end."""
    return " ".join(f"{gain:+d}" for gain in preset.gains) + " dB"


class PresetsDialog:
    """Load preset to headset: `write(slot, preset)` sends a preset to a slot through the
    page, `active()` gives the active slot's preset as the page shows it, None while it is
    not read whole, and `changed()` runs after each result _take takes (saved); `parent` is
    the window, which the dialog and the import's file dialog open over. The dialog is built
    once and presented again at each open."""

    def __init__(self, parent: Gtk.Window, worker: DaemonWorker, toast: Callable[..., object],
                 write: Callable[[int, astro.EqPreset], None],
                 active: Callable[[], astro.EqPreset | None],
                 changed: Callable[[], None]) -> None:
        self._parent = parent
        self._worker = worker
        self._toast = toast
        self._write = write
        self._active = active
        self._changed = changed
        #: The library the last read or action gave, None before one or after one found the
        #: file unread, and whether either has been taken (show).
        self.library: a50_library.Library | None = None
        self._taken = False
        #: The slots the page gave, by number, and the active one; the slot Load writes to.
        self._slots: tuple[int, ...] = astro.PRESETS
        self._active_slot: int | None = None
        self.slot: int | None = None
        #: The dialog is up; the base station takes a write; a read is out.
        self.is_open = False
        self._writable = False
        self._pending = False
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
        # Dimmed: a state, not a preset of that name (style.say_in_place).
        self.empty_row = plain_row(Adw.ActionRow, EMPTY, EMPTY_HELP, css_classes=["dimmed"],
                                   visible=False)
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
        """Read the library on the worker while the dialog is up, and with it down until a
        library is taken (show), unless a read is out or an action runs."""
        if (self._taken and not self.is_open) or self._pending or self.busy:
            return
        self._pending = True
        self._worker.run(a50_library.load_library, self._loaded)

    def _loaded(self, result: Result) -> None:
        self._pending = False
        if not self.busy:
            self._take(result, NOT_LOADED)

    def _take(self, result: Result, failed: str) -> None:
        """A read's or an action's result: the library shown, or the failure said, in the
        list's description for a library that does not read, which no action may then
        change, else in a toast. The description also says what a library read in part left
        out, while its rows load and each change is refused (a50_library.Library.whole)."""
        said: str | None = None
        if isinstance(result, a50_library.Library):
            self.show(result)
            said = result.left_out
        elif isinstance(result, eq_presets.LibraryUnread):
            self.show(None)
            said = str(result)
        elif isinstance(result, OSError):
            # Only a50-eq.json's save raises one.
            toast_not_saved(self._toast, a50_library.library_path(), result)
        else:
            # A refusal in words: the work gives a Library or raises.
            toast_note(self._toast, capitalized(str(result)), failed, one_sentence=True)
        if said is not None:
            # A group's description is markup; each sentence quotes the file's path, a lone
            # surrogate in it as U+FFFD (displayable), and its cause, a wrong version's value
            # or a preset's name among them.
            self.group.set_description(GLib.markup_escape_text(displayable(said)) if said else HELP)
        self._set_sensitive()
        self._changed()

    def saved(self) -> tuple[astro.EqPreset, ...] | None:
        """The library's presets as the last read or action gave them; None before one and
        while the file does not read."""
        return None if self.library is None else self.library.presets

    def show(self, library: a50_library.Library | None) -> None:
        """A row for each preset of `library`, none for None, built again only when the
        presets differ from those shown. Either is a library taken, which ends the reads
        with the dialog down (refresh)."""
        shown = () if self.library is None else self.library.presets
        self.library, self._taken = library, True
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
        """One preset's row: its number and name, its gains, Load, and its menu."""
        row = plain_row(Adw.ActionRow, preset_label(number, preset.name), gains_text(preset))
        load = Gtk.Button(label=LOAD, valign=Gtk.Align.CENTER)
        name_button(load, LOAD_NAME.format(preset.name))
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
        takes = self._active() is not None
        for swap in self.replace_actions:
            swap.set_enabled(takes)

    # -- the actions --------------------------------------------------------

    def save_new(self, preset: astro.EqPreset) -> None:
        """Save `preset` after the last, the page's Save as new preset."""
        self._act(lambda: a50_library.edit(lambda library: library.added(preset)), NOT_SAVED_NEW)

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
        """The active slot's gains and bands as the page showed them at the click, under this
        preset's name, once asked, as Delete is: it writes over the preset. Read again here:
        a reply with no active preset gives the dialog no slots (HeadsetPage.show_presets),
        so the action's enabled state can be a reply old."""
        if (active := self._active()) is None:
            return
        shown = self._preset(number)

        def change(library: a50_library.Library) -> a50_library.Library:
            return library.put(eq_presets.number_named(library.presets, number, shown.name),
                               replace(active, name=shown.name))
        confirm(self.dialog, REPLACE_HEADING.format(shown.name), REPLACE_BODY, REPLACE_RESPONSE,
                lambda: self._act(lambda: a50_library.edit(change), NOT_REPLACED))

    def on_delete(self, _action: Gio.SimpleAction, _value: object, number: int) -> None:
        """The preset row `number` shows deleted once asked, where the file holds it then
        (eq_presets.number_named)."""
        shown = self._preset(number)
        confirm(self.dialog, DELETE_HEADING.format(shown.name), DELETE_BODY, DELETE_RESPONSE,
                lambda: self._act(lambda: a50_library.edit(lambda library: library.deleted(
                    eq_presets.number_named(library.presets, number, shown.name))),
                    NOT_DELETED))

    def on_import(self, _row: Adw.ButtonRow) -> None:
        astroeq_dialog(ADD_TITLE).open(self._parent, None, self._picked)

    def _picked(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
        if (path := picked_path(dialog, result, self._toast)) is not None:
            self.import_path(path)

    def import_path(self, path: Path) -> None:
        """Read the .astroeq file at `path` on the worker, then add it after the last: an
        action from the read on, which the window's close waits for (busy)."""
        def parsed(result: astro.EqPreset | Exception) -> None:
            self._acting -= 1
            if isinstance(result, astro.EqPreset):
                self.save_new(result)
                return
            self._set_sensitive()
            # Whoever shared the file named it: a direction turn or a separator in the
            # name would draw the rest of the sentence in another order or on a new line.
            toast_note(self._toast, IMPORT_REFUSED.format(printable(path.name), reason(result)),
                       NOT_IMPORTED, one_sentence=True)
        self._acting += 1
        self._set_sensitive()
        self._worker.run(lambda: astroeq.load_astroeq(path), parsed)

    def _act(self, work: Callable[[], a50_library.Library], failed: str) -> None:
        """Run `work` on the worker; `failed` is the toast line for a failure too long for
        a toast."""
        self._acting += 1
        self._set_sensitive()
        self._worker.run(work, lambda result: self._acted(result, failed))

    def _acted(self, result: Result, failed: str) -> None:
        """An action's result (_take). A library that does not read is toasted too while
        the dialog is down, where the list's description that says so is not seen: the
        page's Save as new preset saves with it down."""
        self._acting -= 1
        if isinstance(result, eq_presets.LibraryUnread) and not self.is_open:
            toast_note(self._toast, capitalized(str(result)), failed, one_sentence=True)
        self._take(result, failed)
