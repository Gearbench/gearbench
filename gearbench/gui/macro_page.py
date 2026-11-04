"""The Macros tab: the library on the left, one macro's editor on the right.

The worker thread reads `~/.config/gearbench/macros/` itself, since the `macros`
command gives each macro's step count, not its steps; saves and deletes go through
`macro_save` and `macro_delete`, because the daemon owns that directory. Text, delay and
click rows write into their step in place, without a rebuild, so typing keeps the focus.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, cast

from gi.repository import Adw, Gdk, Gio, GObject, Gtk

from ..config import Config, layer_binding, macro_name
from ..library_files import file_name, slug, unread_reason
from ..macros import (
    CLICK,
    CLICK_BUTTONS,
    CLICK_DOWN,
    CLICK_LABELS as BUTTON_LABELS,
    CLICK_STEPS,
    CLICK_UP,
    DELAY,
    KEY,
    KEY_DOWN,
    KEY_UP,
    MAX_DELAY_MS,
    MAX_STEPS,
    MAX_TEXT_LENGTH,
    ONCE,
    PLAY_DELAY,
    RECORD_ROUND_MS,
    REPEAT_MODES,
    REPEAT_NAMES,
    STANDARD_DELAY_MS,
    STEP_TITLES,
    STEP_TYPES,
    TEXT,
    TOGGLE,
    WHILE_HELD,
    MacroLibrary,
    ends_without_delay,
    macro_summary,
    macros_dir,
    step_summary,
    text_problem,
    unbalanced,
    with_step,
)
from ..mouse import MODEL_IDS, bindings_of, key_name as mouse_shown, shift_bindings_of
from ..profiles import GameProfile
from ..wording import LEFT_OUT, MACRO_NEEDS_A_NAME, capitalized, counted, reason, word_list
from .binding_rows import CHANGE, WITH_G_SHIFT
from .dialogs import MACROS_UNREAD, NO_MACROS, CaptureDialog, RecordDialog
from .style import (
    DELETE_HEADING,
    DELETE_OPENER,
    DELETE_RESPONSE,
    HEADSET_TAB,
    KEYBOARD_TAB,
    MOUSE_TAB,
    blank,
    confirm,
    left_out_row,
    name_button,
    plain_row,
    respond,
    say_in_place,
    watch_refused,
)
from .tips import explain_choices
from .worker import BUSY, LoadingGuard, daemon_down, failure, take_typed, unreachable

if TYPE_CHECKING:                       # pragma: no cover
    from .window import Window

#: The button that runs the editor's steps after a countdown, as `macro-run` does.
RUN = "Run"
#: The Macro group's one line, the owner's window-text standard, and the status beside Save
#: while no write is in flight: the rule a rename follows, which no one guesses.
MACRO_HELP = f"Bind it on the {KEYBOARD_TAB}, {MOUSE_TAB} or {HEADSET_TAB} tab"
RENAME_HELP = "Saving under a new name renames it"
#: The repeat dropdown. The labels are short because a ComboRow ellipsizes the
#: chosen one on the row.
REPEAT_LABELS = tuple(REPEAT_NAMES[mode] for mode in REPEAT_MODES)
#: Each repeat mode's tooltip in the dropdown's list, looked up by mode, in REPEAT_MODES order.
REPEAT_TIPS = tuple({ONCE: "Runs the steps once.",
                     WHILE_HELD: "Repeats until the key or button is released.",
                     TOGGLE: "Starts on one press, stops on the next."}[mode]
                    for mode in REPEAT_MODES)
#: The click dropdown, its buttons named as the recording dialog names them.
CLICK_CHOICES = tuple(CLICK_BUTTONS)
CLICK_LABELS = tuple(BUTTON_LABELS[button] for button in CLICK_CHOICES)
#: The step-type dropdown beside Add, in STEP_TYPES order: each down/up pair after the
#: step it splits. The labels are short because a Gtk.DropDown ellipsizes the chosen one.
STEP_LABELS = tuple(STEP_TITLES[kind] for kind in STEP_TYPES)
#: Each step type's tooltip in the dropdown's list, looked up by type, in STEP_TYPES order.
STEP_TIPS = tuple({KEY: "Press and release a key or combination.",
                   KEY_DOWN: "Press a key and leave it down.",
                   KEY_UP: "Release a key an earlier step left down.",
                   TEXT: "Type a line of text, one key at a time.",
                   DELAY: "Wait before the next step.",
                   CLICK: "Press and release a mouse button.",
                   CLICK_DOWN: "Press a mouse button and leave it down.",
                   CLICK_UP: "Release a mouse button an earlier step left down."}[kind]
                  for kind in STEP_TYPES)
#: The toast for a step its row's menu removed, by its number, with Undo.
REMOVED_STEP = "Removed step {}"
#: The toast for a recording put into the editor, by its count of steps (wording.counted).
RECORDED = "Recorded {}; not saved yet"
#: The placeholders of the macro list and the step list while each is empty, with the next
#: step: Record… by name, since a recording is many steps and replaces the editor's.
NEW_MACRO_HINT = "Press New to make one"
NO_STEPS = "No steps yet"
NO_STEPS_HINT = "Pick a step type and press Add, or press Record…"
#: A step row's menu, apart from its value's control: the HIG's boxed lists set at most two
#: controls a row. Its actions' group, its items, its tooltip and its name by the step's
#: number, and the handle at the row's start that drags the step to another place.
STEP_ACTIONS = "step"
MOVE_UP = "Move up"
MOVE_DOWN = "Move down"
REMOVE = "Remove"
STEP_MENU = "More"
STEP_MENU_NAME = "More for step {}"
#: A key step's Change… button's name by the step's number, and the title of the dialog it
#: opens: each row has one, so a screen reader told only "Change…" could not say which.
CHANGE_NAME = "Change step {}"
#: Its tooltip, in the binding rows' form: keys only, as a macro cannot run a macro.
CHANGE_HELP = "Pick another key for this step"
DRAG_HELP = "Drag to move this step"


def uses_by_macro(config: Config, library: list[GameProfile]) -> dict[str, list[str]]:
    """Every G key and mouse button that runs a macro, by the slug of the macro's name, as
    "G1 on M1", "G2 with G-Shift on M2", "G502 X PLUS G4 in Doom" or "G502 X PLUS G4 with
    G-Shift on M1": M profiles first, then the game profiles. One walk of the bindings serves
    every macro's row.

    By slug, as the daemon finds a macro's file. A mouse button by its model, as the daemon
    reads the model's bindings on each layer (mouse.bindings_of, mouse.shift_bindings_of),
    since each model's buttons bind on their own and the keyboard's G4 and G5 share their
    names. A G-Shift binding counts as the daemon reads it (config.layer_binding): one that a
    hand edit left on a key or button holding G-Shift never runs, and is not counted.
    """
    found: dict[str, list[str]] = {}

    def add(spec: object, said: str) -> None:
        target = macro_name(spec)
        if target is not None:
            found.setdefault(slug(target), []).append(said)

    layers = [(f"on M{i}", *profile.layers) for i, profile in sorted(config.profiles.items())]
    layers += [(f"in {profile.name}", *profile.layers) for profile in library]
    for where, keys, shift, mouse, mouse_shift in layers:
        for key, spec in keys.items():
            add(spec, f"{key} {where}")
        for key, spec in shift.items():
            if spec is not None:
                add(layer_binding(keys, shift, key, True), f"{WITH_G_SHIFT.format(key)} {where}")
        bases = {model: bindings_of(mouse, model) for model in sorted(MODEL_IDS)}
        for model, base in bases.items():
            for button, spec in base.items():
                add(spec, f"{mouse_shown(model)} {button.upper()} {where}")
        for model, base in bases.items():
            shifted = shift_bindings_of(mouse_shift, model)
            for button in shifted:
                named = f"{mouse_shown(model)} {button.upper()}"
                add(layer_binding(base, shifted, button, True),
                    f"{WITH_G_SHIFT.format(named)} {where}")
    return found


def uses_summary(uses: list[str]) -> str:
    """What a macro's row says of the keys and buttons bound to it, the clause after "; " in
    its line."""
    return f"bound to {word_list(uses, 'and')}" if uses else "not bound to a key or button"


@functools.cache
def _library(directory: Path) -> MacroLibrary:
    """One MacroLibrary per folder, so its SkipWarnings says a bad file once. Worker thread only."""
    return MacroLibrary(directory)


def read_macros() -> tuple[list[dict], bool, list[tuple[str, str]]]:
    """The macros on disk as the editor holds them, each with the file it was read from;
    whether the folder could not be listed; and the macro files left out (unreadable or
    shadowed, or the folder's own entry) as (file, why)."""
    library = _library(macros_dir())
    # MacroLibrary.all reads each with Macro.load, which gives it the file it came from.
    macros = [{"name": m.name, "repeat": m.repeat, "steps": m.steps,
               "file": cast(Path, m.path).name} for m in library.all()]
    return macros, library.unlisted_reason is not None, library.skipped


#: What New puts in the editor, and what the editor opens on.
NEW_MACRO = {"name": "New macro", "repeat": ONCE, "steps": []}
#: Save's tooltip.
SAVE_HELP = "Write the macro to disk"
#: The status beside Save while a save or a delete is on its way.
SAVE_WAIT = "Waiting for the daemon…"
#: What a read of the macro files says when the open macro's file changed under unsaved edits.
#: "After a reload": a digest moved by another file's save also rereads a hand edit the
#: daemon has not taken, and it refuses a save over that.
CHANGED_UNDER_EDITS = ("{} changed on disk; your edits stay unsaved; after a reload, Save "
                       "writes over it")
#: What a save says once the daemon answered it or the list read after it holds it.
SAVED = "Saved {}"
#: What the list read after a save the daemon did not answer says when it lacks the save, and
#: after such a delete when it still holds the macro, with the round trip's cause
#: (_send_write), and START_FIRST or nothing: one sentence, so no period. The edits stay
#: unsaved and the macro listed, so the same button does it again.
NOT_SAVED = "Your edits to {} were not saved: {}; {}press Save again"
NOT_DELETED = "{} was not deleted: {}; {}delete it again"
#: Before NOT_SAVED's and NOT_DELETED's next step when no daemon runs: the same button fails
#: the same way until the banner's Start daemon starts one.
START_FIRST = "start the daemon, then "
#: A busy daemon's cause (_send_write). Not `unreachable`'s words for it, which add that the daemon may be busy.
TIMED_OUT = "the daemon did not answer in time"

NEW_STEP: dict[str, dict] = {
    KEY: {"type": KEY, "value": "f13"},
    KEY_DOWN: {"type": KEY_DOWN, "value": "f13"},
    KEY_UP: {"type": KEY_UP, "value": "f13"},
    TEXT: {"type": TEXT, "value": "gg"},
    DELAY: {"type": DELAY, "ms": STANDARD_DELAY_MS},
    CLICK: {"type": CLICK, "button": "left"},
    CLICK_DOWN: {"type": CLICK_DOWN, "button": "left"},
    CLICK_UP: {"type": CLICK_UP, "button": "left"},
}


class MacrosPage:
    """The Macros tab and the commands its buttons send."""

    def __init__(self, window: Window) -> None:
        self.win = window
        self._loading = LoadingGuard()
        #: The library as last read.
        self.macros: list[dict] = []
        #: The macro files left out (unreadable or shadowed), or the folder's own entry
        #: when it could not be listed, as (file name, why), listed dimmed under the macros.
        self.skipped: list[tuple[str, str]] = []
        #: The folder could not be listed at the last read, so no file in it was read.
        self.unlisted = False
        #: The file each macro was last listed from, by slug, for `_gone`: a file named
        #: by hand need not be the one its name calls for (library_files.file_name).
        self._files: dict[str, str] = {}
        #: The macro in the editor. `saved_name` is None for a new one.
        self.saved_name: str | None = None
        self.steps: list[dict] = []
        #: How many times a step left its place: the editor took steps whole, a macro's or a
        #: recording's, or one was removed, moved or dropped. An Add appends, so it moves
        #: none. A removed step's Undo puts it back by its index, which is its place only
        #: while this count is the one its removal left.
        self._moved = 0
        self._rows: list[Gtk.Widget] = []
        #: The editor's snapshot when last loaded or saved; anything else is unsaved work.
        self._clean: tuple = ()
        #: The editor's snapshot when the close's discard prompt was answered, so the closes
        #: that follow do not ask again until it is edited or a close keeps the window up
        #: (close_kept).
        self._discarded: tuple | None = None
        #: How many discard prompts are up (confirm_discard). A Replace asked over one
        #: (_ask_after_refusal) would start a save that its Discard then moves the editor
        #: away from.
        self._asking = 0
        #: A save or a delete is on its way to the daemon. One at a time, and nothing
        #: moves the editor on meanwhile: the worker's queue is shared with the polls
        #: and the mouse's flash writes, so a reply can land seconds later, on whatever
        #: the editor then holds.
        self.writing = False
        #: The saves whose reply was lost and which the list read after each did not hold,
        #: oldest first, each as (what it sent, in `_form`, whether it renamed). That read
        #: misses a save the daemon still writes when the request sent before it timed out
        #: too, or when the read failed, and a later save can be lost the same way before an
        #: earlier one lands; `_take_up` takes the newest a later read lists.
        self._late: list[tuple[tuple, bool]] = []

        self.widget = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18,
                              margin_top=18, margin_start=18, margin_end=18)
        library = self._library_side()
        library.set_margin_bottom(18)
        self.widget.append(library)
        self.widget.append(self._editor_side())
        # Opens as New leaves it, so what is typed before any New is unsaved work too.
        self.edit(NEW_MACRO, saved=False)
        self.reload()

    # -- construction -----------------------------------------------------

    def _library_side(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, width_request=260)
        box.append(Gtk.Label(label="Macros", xalign=0, css_classes=["heading"]))
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE,
                                   css_classes=["boxed-list"])
        self.listbox.connect("row-selected", self.on_select)
        box.append(Gtk.ScrolledWindow(child=self.listbox, vexpand=True,
                                      hscrollbar_policy=Gtk.PolicyType.NEVER))
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
                          homogeneous=True)
        self.new_button = Gtk.Button(label="New")
        self.new_button.connect("clicked", self.on_new)
        # Plain: the red is on the confirmation's Delete, which does it.
        self.delete_button = Gtk.Button(label=DELETE_OPENER)
        self.delete_button.connect("clicked", self.on_delete)
        buttons.append(self.new_button)
        buttons.append(self.delete_button)
        box.append(buttons)
        return box

    def _editor_side(self) -> Gtk.Widget:
        """The name, the repeat mode and the steps over a bottom bar with Save."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, hexpand=True)

        group = Adw.PreferencesGroup(
            title="Macro",
            description=MACRO_HELP)
        self.name_row = Adw.EntryRow(title="Name")
        watch_refused(self.name_row, blank)
        group.add(self.name_row)
        # Read at Save and Run; a change sends nothing to the daemon.
        self.repeat_row = Adw.ComboRow(title="Repeat", model=Gtk.StringList.new(REPEAT_LABELS))
        explain_choices(self.repeat_row, REPEAT_TIPS)
        group.add(self.repeat_row)
        box.append(group)

        steps_group = Adw.PreferencesGroup(
            title="Steps", description=f"Run in order, up to {MAX_STEPS} of them")
        adders = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
                         valign=Gtk.Align.CENTER)
        # One dropdown and Add: a button per type would not fit beside Record.
        self.step_kind = Gtk.DropDown(model=Gtk.StringList.new(STEP_LABELS))
        self.step_kind.set_tooltip_text("The type of step Add appends")
        self.step_kind.update_property([Gtk.AccessibleProperty.LABEL], ["Step type"])
        explain_choices(self.step_kind, STEP_TIPS)
        adders.append(self.step_kind)
        add = Gtk.Button(label="Add", tooltip_text="Append a step of that type")
        add.connect("clicked", lambda _b: self.add_step(
            STEP_TYPES[self.step_kind.get_selected()]))
        adders.append(add)
        adders.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL,
                                    margin_top=4, margin_bottom=4))
        record = Gtk.Button(label="Record…",
                            tooltip_text="Record keys and clicks into steps")
        record.connect("clicked", self.on_record)
        adders.append(record)
        steps_group.set_header_suffix(adders)
        box.append(steps_group)
        # Only the rows scroll: the heading, Add and Record stay put above them.
        self.step_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE,
                                     css_classes=["boxed-list"], valign=Gtk.Align.START)
        box.append(Gtk.ScrolledWindow(child=self.step_list, vexpand=True,
                                      hscrollbar_policy=Gtk.PolicyType.NEVER))
        self.hold_label = Gtk.Label(xalign=0, wrap=True, hexpand=True)
        self.hold_warning = Gtk.Box(spacing=8, visible=False, css_classes=["warning"])
        # Decoration: the label beside it says what is wrong, and without the role a
        # screen reader meets an image with no name.
        self.hold_warning.append(Gtk.Image(icon_name="dialog-warning-symbolic",
                                           valign=Gtk.Align.START,
                                           accessible_role=Gtk.AccessibleRole.PRESENTATION))
        self.hold_warning.append(self.hold_label)
        box.append(self.hold_warning)
        # Both warnings depend on the mode as well as the steps.
        self.repeat_row.connect("notify::selected", lambda *_: self.check_holds())

        self.save_status = Gtk.Label(label=RENAME_HELP, xalign=0, hexpand=True,
                                     css_classes=["dimmed"])
        self.run_button = Gtk.Button(
            label=RUN, tooltip_text=f"Run these steps in {PLAY_DELAY} s, unsaved changes "
                                    f"included: click into the window they are for")
        self.run_button.connect("clicked", self.on_play)
        self.save_button = Gtk.Button(label="Save", css_classes=["suggested-action"],
                                      tooltip_text=SAVE_HELP)
        self.save_button.connect("clicked", self.on_save)
        bar = Gtk.Box(spacing=12, css_classes=["toolbar"])
        bar.append(self.save_status)
        bar.append(self.run_button)
        bar.append(self.save_button)
        editor = Adw.ToolbarView(content=box)
        editor.add_bottom_bar(bar)
        return editor

    # -- the library ------------------------------------------------------

    def reload(self, then: Callable[[bool], None] | None = None) -> None:
        """Read the macros on the worker and rebuild the list; `then` gets whether it worked."""
        def loaded(result: tuple[list[dict], bool, list[tuple[str, str]]] | Exception) -> None:
            if isinstance(result, Exception):
                self.win.toast(f"{MACROS_UNREAD} ({reason(result)})")
            else:
                self.macros, self.unlisted, self.skipped = result
                # Added to, never replaced: a macro that left the list has its file
                # only in an earlier one.
                self._files.update((slug(m["name"]), m["file"]) for m in self.macros)
                self.rebuild_list()
            if then is not None:
                then(not isinstance(result, Exception))

        self.win.worker.run(read_macros, loaded)

    def reread(self) -> None:
        """Read the list again when the daemon's macro files changed (Window.take_macros).

        A clean editor shows the open macro as its file now holds it, or a new macro once
        the file is gone. An editor holding unsaved work keeps it and says the file
        changed, and from then on is compared with the new file. A save whose reply was
        lost and which landed after the read that settled it is taken as saved (`_late`).
        """
        def then(read: bool) -> None:
            if not read or self.writing:
                return
            if self._take_up():
                return
            name = self.saved_name
            if name is None:
                return
            # By slug, as the daemon finds the file: a save that renames in case or
            # punctuation only, as an import's Replace does, keeps it.
            fresh = self._listed(name)
            if fresh is not None and self._form(fresh) == self._clean:
                return
            if not self.dirty:
                self.edit(fresh or NEW_MACRO, saved=fresh is not None)
            elif fresh is None:
                self.saved_name, self._clean = None, ()
                self.reselect_current()
                self.win.toast(f"{name}'s file {self._gone(name)}; your edits stay unsaved")
            else:
                self.saved_name, self._clean = fresh["name"], self._form(fresh)
                self.reselect_current()
                self.win.toast(CHANGED_UNDER_EDITS.format(name))

        self.reload(then)

    def _listed(self, name: str) -> dict | None:
        """The listed macro `name` matches by slug, as the daemon finds a macro's file."""
        return next((m for m in self.macros if slug(m["name"]) == slug(name)), None)

    def _gone(self, name: str) -> str:
        """What became of the file of `name`, which the list does not hold: a file
        that could not be read is among the skipped ones, under the file it was last
        listed from or its slug, or in a folder that could not be listed."""
        files = {file_name(name), self._files.get(slug(name))}
        if self.unlisted or any(file in files for file, _why in self.skipped):
            return "could not be read"
        return "was deleted"

    def rebuild_list(self) -> None:
        uses = self.uses()
        with self._loading:
            # Set after each read, so the list says it is empty only once it was read.
            say_in_place(self.listbox, NO_MACROS, NEW_MACRO_HINT)
            for macro in self.macros:
                # Not markup: an "&" in a name would leave the row empty.
                self.listbox.append(plain_row(Adw.ActionRow, macro["name"],
                                              self._row_subtitle(macro, uses)))
            # After the macros, so a row's index is still its macro's.
            for name, why in self.skipped:
                self.listbox.append(left_out_row(
                    name, f"{LEFT_OUT}: {unread_reason(name, why)}",
                    selectable=False, activatable=False))
        self.reselect_current()

    def uses(self) -> dict[str, list[str]]:
        """Where the window's copy of the files binds each macro, by slug (uses_by_macro)."""
        return uses_by_macro(self.win.config, self.win.library)

    def _row_subtitle(self, macro: dict, uses: dict[str, list[str]]) -> str:
        """A macro's row's line, `uses` as uses_by_macro gives it."""
        summary = macro_summary(len(macro["steps"]), macro["repeat"])
        bound = uses.get(slug(macro["name"]), [])
        if not bound and self._uses_unknown():
            return summary
        return f"{summary}; {uses_summary(bound)}"

    def _uses_unknown(self) -> bool:
        """Whether a key or button the window's copy lacks may run a macro: with config.json
        unread, the M profiles are the defaults or an earlier read (Window.config); before
        the game profiles' first read the window has none (Window.library_read); and the
        game profile loaded from a file the window could not read keeps its keys in the
        daemon (Window.profile_unread)."""
        return (self.win.config_error is not None or not self.win.library_read
                or self.win.profile_unread())

    def refresh_uses(self) -> None:
        """Say again where each macro runs, after the window read the files."""
        uses = self.uses()
        for index, macro in enumerate(self.macros):
            row = self.listbox.get_row_at_index(index)
            if isinstance(row, Adw.ActionRow):
                row.set_subtitle(self._row_subtitle(macro, uses))

    def on_select(self, _box: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if self._loading.active or row is None:
            return
        chosen = self.macros[row.get_index()]
        if self.dirty:
            name = chosen["name"]
            self.confirm_discard(f"Opening {name}", lambda: self._open_listed(name))
            return
        self.edit(chosen)

    def _open_listed(self, name: str) -> None:
        """Open `name` as the list holds it now. The list can be reread while the discard
        prompt is up, and a stale copy opened as clean would let Save write over the file."""
        found = self._listed(name)
        if found is None:
            self.reselect_current()
            self.win.toast(f"{name}'s file {self._gone(name)}")
            return
        self.edit(found)

    def on_new(self, _b: Gtk.Button | None) -> None:
        if self.dirty:
            self.confirm_discard("Starting a new macro",
                                 lambda: self.edit(NEW_MACRO, saved=False))
            return
        self.edit(NEW_MACRO, saved=False)

    def on_delete(self, _b: Gtk.Button | None) -> None:
        name = self.saved_name
        if not name:
            self.win.toast("Select a saved macro first")
            return
        uses = self.uses().get(slug(name), [])
        body = "The file is removed."
        if uses:
            body += (f" {word_list(uses, 'and')} then "
                     f"{'does' if len(uses) == 1 else 'do'} nothing until rebound.")
        elif not self._uses_unknown():
            body += " No key or button runs it."
        confirm(self.win, DELETE_HEADING.format(name), body, DELETE_RESPONSE,
                lambda: self._do_delete(name))

    def _do_delete(self, name: str) -> None:
        """Delete `name`, and clear the editor if it is unchanged since the delete was sent:
        edits typed meanwhile stay, and a close waiting on the delete still asks about them."""
        sent = self.snapshot()

        def deleted() -> None:
            if self.snapshot() == sent:
                self.win.toast(f"Deleted {name}")
                self.edit(NEW_MACRO, saved=False)
                return
            # Kept as a new macro: its file is gone, so Save writes a new one.
            self.saved_name = None
            self._clean = ()
            self.reselect_current()
            self.win.toast(f"Deleted {name}; later edits stay unsaved")

        def done(reply: dict | None) -> None:
            if reply is not None:
                deleted()
                self.reload()
            else:
                # Refused, and the toast said why. A save whose reply was lost can have
                # landed meanwhile, listed by a `reread` held off while this was out.
                self._take_up()

        def settle(read: bool, why: str, first: str) -> None:
            # A timeout can come after the file went; the list read again, once the daemon
            # answers a later request, says whether it did. A read that failed says so in
            # its toast, and a folder that could not be listed in its row: neither says
            # whether the file went.
            if not read or self.unlisted:
                return
            # A lost save the list now holds landed before the delete, which the daemon
            # answers in order: had it renamed the macro, the delete found no macro of
            # this name.
            if self._take_up() or self._listed(name) is not None:
                self.win.toast(NOT_DELETED.format(name, why, first))
            else:
                deleted()
        self._send_write({"command": "macro_delete", "value": name}, done, settle)

    # -- the editor -------------------------------------------------------

    def edit(self, macro: dict, saved: bool = True) -> None:
        self._late = []
        self.saved_name = macro["name"] if saved else None
        self.steps = [dict(step) for step in macro["steps"]]
        self._moved += 1
        self.name_row.set_text(macro["name"])
        self.repeat_row.set_selected(REPEAT_MODES.index(macro["repeat"]))
        self.rebuild_steps()
        self._clean = self.snapshot()
        self.reselect_current()

    # -- unsaved work -----------------------------------------------------

    def snapshot(self) -> tuple:
        """What the editor holds, in a form two states can be compared by.

        A Delay row's typed number goes into its step first: a spin row takes it only on
        Enter or when the focus leaves, and neither a click on a list row nor the
        window's close button moves the focus before the editor is compared.
        """
        take_typed(*(row for row in self._rows if isinstance(row, Adw.SpinRow)))
        return self._form({"name": self._open_name(),
                           "repeat": REPEAT_MODES[self.repeat_row.get_selected()],
                           "steps": self.steps})

    @staticmethod
    def _form(macro: dict) -> tuple:
        """A macro as `snapshot` has one."""
        return macro["name"], macro["repeat"], json.dumps(macro["steps"], sort_keys=True)

    def _open_name(self) -> str:
        """The name in the editor, which is not always a saved macro's."""
        return self.name_row.get_text().strip()

    @property
    def dirty(self) -> bool:
        return self.snapshot() != self._clean

    def reselect_current(self) -> None:
        """Put the list selection back on the macro the editor is showing.

        row-selected has already moved when its handler runs, so a canceled switch is
        undone here. An unsaved macro has no row, so the selection is cleared: GtkListBox
        does not re-select a selected row, so one left highlighted after New could not be
        clicked to go back to it.
        """
        names = [m["name"] for m in self.macros]
        with self._loading:
            if self.saved_name in names:
                self.listbox.select_row(self.listbox.get_row_at_index(
                    names.index(self.saved_name)))
            else:
                self.listbox.unselect_all()

    def confirm_discard(self, what: str, go_on: Callable[[], None]) -> None:
        """Ask before discarding unsaved edits; `what` starts the sentence, as "Opening Two"."""
        name = self._open_name() or "this macro"
        dialog = Adw.AlertDialog(
            heading="Discard the unsaved changes?",
            body=f"{what} discards the unsaved changes to {name}. "
                 f"Nothing on disk changes.")
        respond(dialog, "discard", "Discard", Adw.ResponseAppearance.DESTRUCTIVE)

        def answered(_d: Adw.AlertDialog, response: str) -> None:
            self._asking -= 1
            if response == "discard":
                go_on()
            else:
                self.reselect_current()
        dialog.connect("response", answered)
        self._asking += 1
        dialog.present(self.win)

    def asks_before_close(self, close: Callable[[], None]) -> bool:
        """Ask before the window's close discards unsaved edits, and whether it asked.

        Not while a save or a delete is on its way, which the close waits for, nor again
        for the state a Discard was given for. Discard calls `close`: the window's close(),
        not destroy(), which emits no close-request, so the window's teardown would not run.
        """
        if self.writing or self.snapshot() in (self._clean, self._discarded):
            return False

        def discard() -> None:
            self._discarded = self.snapshot()
            close()
        self.confirm_discard("Closing the window", discard)
        return True

    def close_kept(self) -> None:
        """A close kept the window up to say a change did not go through: the next close
        asks again about unsaved edits, even ones a Discard answered."""
        self._discarded = None

    def add_step(self, kind: str) -> None:
        """Append a step of `kind`, with the standard delay between two events (`with_step`)."""
        if len(self.steps) >= MAX_STEPS:
            self.win.toast(f"A macro holds at most {MAX_STEPS} steps")
            return
        self.steps = with_step(self.steps, NEW_STEP[kind])
        self.rebuild_steps()

    def remove_step(self, index: int) -> None:
        """Remove one step; the last may go too, since only Save and Run require a step.
        Its toast's Undo puts it back at its place while no step has left its place since
        (`_moved`) and there is room for it: one click took it, and the edit is not saved yet
        (the owner's decision of 2026-10-22)."""
        step = self.steps.pop(index)
        self._moved += 1
        moved = self._moved
        self.rebuild_steps()

        def undo() -> None:
            if self._moved == moved and len(self.steps) < MAX_STEPS:
                self.steps.insert(index, step)
                self.rebuild_steps()
        self.win.toast(REMOVED_STEP.format(index + 1), undo=undo)

    def drop_step(self, index: int, target: int) -> bool:
        """The step at `index` dropped on the row at `target`, or moved up or down by its
        menu, taking its place: the ones between move a place toward where it was. A drop
        on its own row moves nothing."""
        if index == target or not 0 <= index < len(self.steps):
            return False
        self.steps.insert(target, self.steps.pop(index))
        self._moved += 1
        self.rebuild_steps()
        return True

    def rebuild_steps(self) -> None:
        # A number typed in a Delay row and not yet taken goes into its step first: a drag
        # moves no focus, and the old row takes it only once GTK moves the focus on, with
        # the new row showing the old number.
        take_typed(*(row for row in self._rows if isinstance(row, Adw.SpinRow)))
        # A heading over nothing reads as a tab that failed to load. remove_all takes the
        # placeholder too, so it is set again with the rows.
        say_in_place(self.step_list, NO_STEPS, NO_STEPS_HINT)
        self.check_holds()
        self._rows = [self._step_row(index, step) for index, step in enumerate(self.steps)]
        for row in self._rows:
            self.step_list.append(row)
        self._show_sendable()

    def _show_sendable(self) -> None:
        """Run and Save on while the editor holds a step, No steps yet in the list saying
        why they are off before a click, and Save off too while a write is in flight."""
        self.run_button.set_sensitive(bool(self.steps))
        self.save_button.set_sensitive(bool(self.steps) and not self.writing)

    def check_holds(self) -> None:
        """Show or hide the warnings under the steps."""
        repeat = REPEAT_MODES[self.repeat_row.get_selected()]
        found = unbalanced(self.steps, repeat) + ends_without_delay(self.steps, repeat)
        self.hold_label.set_text("\n".join(found))
        self.hold_warning.set_visible(bool(found))

    def _step_row(self, index: int, step: dict) -> Gtk.Widget:
        """One editable step. Each value is set before its handler is connected, so
        setting it up is not read back as an edit."""
        kind = step["type"]
        title, subtitle = step_summary(step)
        numbered = f"{index + 1}. {title}"
        row: Adw.EntryRow | Adw.ActionRow
        if kind == TEXT:
            row = Adw.EntryRow(title=numbered, use_markup=False)
            row.set_text(step["value"])
            row.set_max_length(MAX_TEXT_LENGTH)
            row.connect("changed", lambda r, s=step: s.__setitem__("value", r.get_text()))
        elif kind == DELAY:
            row = Adw.SpinRow(
                title=numbered,
                subtitle="Milliseconds to wait before the next step",
                adjustment=Gtk.Adjustment(value=step["ms"], lower=0,
                                          upper=MAX_DELAY_MS, step_increment=RECORD_ROUND_MS,
                                          page_increment=100))
            row.connect("notify::value",
                        lambda r, _p, s=step: self._set_field(s, "ms", round(r.get_value())))
        elif kind in CLICK_STEPS:
            row = Adw.ComboRow(title=numbered, model=Gtk.StringList.new(CLICK_LABELS))
            row.set_selected(CLICK_CHOICES.index(step["button"]))
            row.connect("notify::selected", lambda r, _p, s=step: self._set_field(
                s, "button", CLICK_CHOICES[r.get_selected()]))
        else:
            # Not markup: the subtitle is what the step sends.
            row = Adw.ActionRow(title=numbered, subtitle=subtitle, use_markup=False)
            change = Gtk.Button(label=CHANGE, valign=Gtk.Align.CENTER, tooltip_text=CHANGE_HELP)
            name_button(change, CHANGE_NAME.format(index + 1))
            change.connect("clicked", lambda _b, i=index: self._capture(i))
            row.add_suffix(change)

        row.add_suffix(self._step_menu(row, index))
        self._drag_handle(row, index)
        return row

    def _step_menu(self, row: Adw.EntryRow | Adw.ActionRow, index: int) -> Gtk.MenuButton:
        """The row's menu, Move up, Move down and Remove, each a move past either end grayed
        out; Remove, which takes the step, in a section of its own at the end, as the HIG has
        it."""
        actions = Gio.SimpleActionGroup()
        for name, act, able in (
                ("up", lambda: self.drop_step(index, index - 1), index > 0),
                ("down", lambda: self.drop_step(index, index + 1), index < len(self.steps) - 1),
                ("remove", lambda: self.remove_step(index), True)):
            action = Gio.SimpleAction(name=name, enabled=able)
            action.connect("activate", lambda _a, _p, act=act: act())
            actions.add_action(action)
        row.insert_action_group(STEP_ACTIONS, actions)
        menu = Gio.Menu()
        menu.append(MOVE_UP, f"{STEP_ACTIONS}.up")
        menu.append(MOVE_DOWN, f"{STEP_ACTIONS}.down")
        last = Gio.Menu()
        last.append(REMOVE, f"{STEP_ACTIONS}.remove")
        menu.append_section(None, last)
        more = Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu,
                              tooltip_text=STEP_MENU, valign=Gtk.Align.CENTER,
                              css_classes=["flat"])
        more.update_property([Gtk.AccessibleProperty.LABEL], [STEP_MENU_NAME.format(index + 1)])
        return more

    def _drag_handle(self, row: Adw.EntryRow | Adw.ActionRow, index: int) -> None:
        """A handle at the row's start that drags the step onto another row, which takes it
        in its place (drop_step). The menu does the same from the keyboard, so the handle is
        no focus stop, and to a screen reader it is presentation."""
        handle = Gtk.Image(icon_name="list-drag-handle-symbolic", tooltip_text=DRAG_HELP,
                           accessible_role=Gtk.AccessibleRole.PRESENTATION)
        source = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        source.connect("prepare", lambda _s, _x, _y: Gdk.ContentProvider.new_for_value(
            GObject.Value(GObject.TYPE_INT, index)))
        handle.add_controller(source)
        row.add_prefix(handle)
        # Python's int is GObject.TYPE_INT here (measured 2026-10-23, PyGObject with GTK 4).
        target = Gtk.DropTarget.new(int, Gdk.DragAction.MOVE)
        target.connect("drop", lambda _t, value, _x, _y: self.drop_step(value, index))
        row.add_controller(target)

    def _set_field(self, step: dict, field: str, value: int | str) -> None:
        """A delay's milliseconds or a click's button, edited in place: the warnings under
        the steps may change."""
        step[field] = value
        self.check_holds()

    def _capture(self, index: int) -> None:
        """Ask for a key for one step: keys only, as a macro cannot run a macro, and no Unbind."""
        dialog = CaptureDialog(self.win, f"Step {index + 1}", can_unbind=False,
                               title=CHANGE_NAME.format(index + 1), verb="Change")
        step = self.steps[index]
        dialog.run(lambda spec: self._captured(step, spec))

    def _captured(self, step: dict, spec: str | None) -> None:
        # A reread can replace the steps while the dialog is up; such a capture is dropped.
        if all(s is not step for s in self.steps):
            return
        step["value"] = spec
        self.rebuild_steps()

    # -- recording --------------------------------------------------------

    def on_record(self, _b: Gtk.Button | None) -> None:
        RecordDialog(self.win).run(self.on_recorded)

    def on_recorded(self, steps: list[dict]) -> None:
        """Put what the dialog recorded into the editor, unsaved: a recording is a draft."""
        if not steps:
            self.win.toast("Nothing was recorded")
            return
        # No steps to replace, so nothing to ask.
        if not self.steps:
            self._replace_steps(steps)
            return
        confirm(self.win, "Replace the steps?",
                f"The recording ({counted(len(steps), 'step')}) replaces the "
                f"{counted(len(self.steps), 'step')} in the editor. Nothing on disk changes "
                "until Save.",
                "Replace", lambda: self._replace_steps(steps))

    def _replace_steps(self, steps: list[dict]) -> None:
        self.steps = [dict(step) for step in steps]
        self._moved += 1
        self.rebuild_steps()
        self.win.toast(RECORDED.format(counted(len(self.steps), 'step')))

    # -- playing ----------------------------------------------------------

    def on_play(self, _b: Gtk.Button | None) -> None:
        """Run what the editor holds, saved or not, PLAY_DELAY seconds from now.

        The steps go whole, as `macro_save` would send them, and nothing is written. The
        reply puts the countdown and Stop on the window's macro banner. With no key held,
        `once` and `while_held` run one pass and `toggle` runs until Stop.
        """
        if (name := self._sendable()) is None:
            return
        request = {"command": "macro_run", "value": name,
                   "repeat": REPEAT_MODES[self.repeat_row.get_selected()],
                   "steps": [dict(step) for step in self.steps], "delay": PLAY_DELAY}
        self.win.call(request,
                      lambda reply: self.win.macro_banner.take(reply) if reply is not None else None)

    def _sendable(self) -> str | None:
        """The editor's name, or None once a toast or a dialog said why the daemon would
        refuse to run or save the macro. Run and Save are off with no step (_show_sendable),
        which the list says before a click."""
        name = self._open_name()
        if not name:
            self.win.toast(capitalized(MACRO_NEEDS_A_NAME))
            return None
        for index, step in enumerate(self.steps):
            if step["type"] == TEXT and (problem := text_problem(step["value"])) is not None:
                # Said as the daemon's refusals are (Window.refused), by the row's number:
                # the daemon's refusal numbers steps from 0, as the file does.
                self.win.refused({"error": f"Step {index + 1}: {problem}"})
                return None
        return name

    # -- saving -----------------------------------------------------------

    def on_save(self, _b: Gtk.Button | None) -> None:
        if (name := self._sendable()) is None:
            return
        repeat = REPEAT_MODES[self.repeat_row.get_selected()]
        taken = self._taken(name)
        if taken is not None:
            self.confirm_overwrite(taken, name, repeat)
            return
        self.write(name, repeat)

    def _taken(self, name: str) -> dict | None:
        """The listed macro, other than the open one, that saving as `name` would write over.

        Matched by slug, as the daemon matches a name to a file: "my macro" is saved into
        "My Macro"'s file.
        """
        # Not slug("") for a new macro: that is profile-da39a3ee, the empty name's
        # hash, so saving one as "Profile DA39A3EE" would never ask.
        if self.saved_name is not None and slug(name) == slug(self.saved_name):
            return None
        return self._listed(name)

    def _ask_after_refusal(self, name: str) -> None:
        """Ask Replace, as Save does, once the list read again holds the
        name a new macro's save was refused for, unless it holds the editor's own
        earlier save, whose reply was lost: that is taken up (`_late`). Not over a discard
        prompt: Save asks again."""
        if (self.writing or self.saved_name is not None or not self.steps
                or self._open_name() != name):
            return      # the editor has moved on since
        # A take-up sends nothing, so it goes ahead under a discard prompt, as `reread`'s does.
        if self._take_up() or self._asking:
            return
        taken = self._taken(name)
        if taken is not None:
            self.confirm_overwrite(taken, name, REPEAT_MODES[self.repeat_row.get_selected()])

    def confirm_overwrite(self, taken: dict, name: str, repeat: str) -> None:
        """Ask before saving `name` over `taken`, the listed macro it matches; Replace writes it."""
        other = taken["name"]
        body = (f"Saving as {name} writes over {other} ({counted(len(taken['steps']), 'step')}). "
                f"Anything bound to {other} then runs what is in the editor.")
        if self.saved_name is not None:
            # A rename: the daemon moves the open macro's file onto the other's.
            body += (f" {self.saved_name} is renamed, not kept: its file goes, and its "
                     f"bindings run {name}.")
        confirm(self.win, f"Replace {other}?", body, "Replace",
                lambda: self.write(name, repeat, replace=True))

    def write(self, name: str, repeat: str, replace: bool = False) -> None:
        """Send the macro, counted as saved from the moment it is sent; nothing while a save
        or a delete is out (`writing`).

        The steps are copied and snapshotted here, not when the reply lands: the editor
        stays live across the round trip, and an edit made meanwhile must leave it dirty.
        """
        if self.writing:
            # A second Replace prompt, a refusal's read put up over Save's own
            # (_ask_after_refusal), answered with that one's save still out: one at a time.
            return
        steps = [dict(step) for step in self.steps]
        late, self._late = self._late, []
        sent = self._form({"name": name, "repeat": repeat, "steps": steps})
        before, self._clean = self._clean, sent
        request: dict[str, object] = {"command": "macro_save", "value": name,
                                      "repeat": repeat, "steps": steps}
        renamed = self.saved_name is not None and self.saved_name != name
        if renamed:
            # A rename: the daemon moves the file rather than writing a copy.
            request["previous"] = self.saved_name

        def done(reply: dict | None) -> None:
            if reply is None:
                # Refused, and the toast or banner said why: the edits are unsaved again.
                # A refused save wrote nothing. An earlier one whose reply was lost can
                # have landed meanwhile, listed by a `reread` held off while this one was
                # on its way; one still to land is taken up by the read that lists it.
                self._clean, self._late = before, late
                if self._take_up():
                    return
                if request.get("new"):
                    # Most likely a macro of that name the list did not hold.
                    self.reload(
                        lambda read: self._ask_after_refusal(name) if read else None)
            else:
                self._saved(reply["saved"], renamed)
                self.reload()

        def settle(read: bool, why: str, first: str) -> None:
            # A timeout can come after the write: settle the save by the list read again,
            # with the editor held until then. Taken as refused, a rename the daemon did
            # would send the old name as `previous` at every later Save, and be refused.
            # A failed read leaves the old list, which holds these steps only if saved.
            self._clean = before
            # An earlier save whose reply was lost can have landed before this one; had
            # it renamed, the daemon refused this one, sent with the old name as
            # `previous`. One not listed yet can still land, before this one. This save
            # joins them, and the newest the list holds is taken up.
            self._late = [*late, (sent, renamed)]
            self._take_up()
            if read and self._late and not self.unlisted:
                # Still in `_late`: this save is not listed. A read that failed says so in
                # its toast, and a folder that could not be listed in its row: neither says
                # whether the save landed.
                self.win.toast(NOT_SAVED.format(name, why, first))
        if replace:
            request["replace"] = True
        elif self.saved_name is None:
            # The list may predate a file added by hand, which the daemon
            # would write over without a word.
            request["new"] = True
        self._send_write(request, done, settle)

    def _send_write(self, request: dict, answered: Callable[[dict | None], None],
                    settle: Callable[[bool, str, str], None]) -> None:
        """Send a save or a delete with the editor held (`_set_writing`) until `answered`
        has the reply, or None for a refusal; unanswered, until `settle` has the list
        read again after it (`_after_the_daemon`), the round trip's cause in words and what
        goes before the next step. The cause is TIMED_OUT where `failure` says BUSY (a
        timeout, or a backlog too full to connect), else `unreachable`'s, as for a daemon not
        running, which never got the write; before the next step goes START_FIRST where no
        daemon runs (daemon_down), else nothing."""
        def done(reply: dict | None) -> None:
            answered(reply)
            self._set_writing(False)

        def unanswered(error: Exception) -> None:
            why = TIMED_OUT if failure(error) == BUSY else unreachable(error)
            first = START_FIRST if daemon_down(error) else ""

            def settled(read: bool) -> None:
                settle(read, why, first)
                self._set_writing(False)
            self._after_the_daemon(settled)
        self._set_writing(True)
        self.win.call(request, done, unanswered)

    def _after_the_daemon(self, settle: Callable[[bool], None]) -> None:
        """Read the list for `settle` once the daemon answers a request sent after a save
        or a delete it did not answer, or that request goes unanswered in turn.

        Not the files at once: a daemon too busy to answer in time has likely not read the
        write yet, and carries it out once free, as it reads every request to its end
        whether or not the client still waits. It answers in the order it accepts, so the
        write is done once a later request is answered. A second timeout can still settle
        before the write lands.
        """
        self.win.worker.call({"command": "macros"}, lambda _reply: self.reload(settle))

    def _take_up(self) -> bool:
        """Take the newest of `_late`, saves whose reply was lost, that the list last read
        holds as saved, and say so: what it sent is the saved state, and edits since stay
        unsaved. Those sent after it are kept, since they can still land. Whether it took
        one."""
        for index in reversed(range(len(self._late))):
            form, renamed = self._late[index]
            if any(self._form(m) == form for m in self.macros):
                self._late = self._late[index + 1:]
                self._clean = form
                self._saved(form[0], renamed)
                self.reselect_current()
                return True
        return False

    def _saved(self, name: str, renamed: bool) -> None:
        """Make `name` the open macro's saved name and say so."""
        self.saved_name = name
        self.win.toast(SAVED.format(name))
        if renamed:
            # The daemon repointed the old name's bindings in config.json and the game
            # profiles, and the Keyboard tab draws from the window's copy.
            self.win.resync()

    def _set_writing(self, busy: bool) -> None:
        """Disable what moves the editor on or sends a second write while one is in flight."""
        self.writing = busy
        for widget in (self.listbox, self.new_button, self.delete_button):
            widget.set_sensitive(not busy)
        self._show_sendable()
        self.save_status.set_text(SAVE_WAIT if busy else RENAME_HELP)
