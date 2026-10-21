"""The Macros tab: the library on the left, one macro's editor on the right.

The worker thread reads `~/.config/gearbench/macros/` itself, since the `macros`
command carries only a count; saves and deletes go through `macro_save` and
`macro_delete`, because the daemon owns that directory. Text, delay and click rows
write into their step in place, without a rebuild, so typing keeps the focus.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, cast

from gi.repository import Adw, Gtk

from ..config import Config, macro_name, unread_reason
from ..library_files import file_name, slug
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
    STANDARD_DELAY_MS,
    STEP_TITLES,
    TEXT,
    TOGGLE,
    WHILE_HELD,
    MacroLibrary,
    macro_summary,
    macros_dir,
    step_summary,
    text_problem,
    unbalanced,
    with_step,
)
from ..mouse import MODEL_IDS, bindings_of, key_name as mouse_shown
from ..profiles import GameProfile
from ..wording import counted, word_list
from .dialogs import CaptureDialog, RecordDialog
from .style import confirm, left_out_row, plain_row
from .tips import explain_choices
from .worker import LoadingGuard

if TYPE_CHECKING:                       # pragma: no cover
    from .window import Window

#: The repeat dropdown. The labels are short because a ComboRow ellipsizes the
#: chosen one on the row.
REPEAT_CHOICES = (ONCE, WHILE_HELD, TOGGLE)
REPEAT_LABELS = ("Once", "While held", "Toggle")
#: Each repeat mode's tooltip in the dropdown's list, in REPEAT_CHOICES order.
REPEAT_TIPS = ("Runs the steps once.",
               "Repeats until the key is released.",
               "Starts on one press, stops on the next.")
#: The click dropdown, its buttons named as the recording dialog names them.
CLICK_CHOICES = tuple(CLICK_BUTTONS)
CLICK_LABELS = tuple(BUTTON_LABELS[button] for button in CLICK_CHOICES)
#: The step-type dropdown beside Add, each down/up pair after the step it splits.
#: The labels are short because a Gtk.DropDown ellipsizes the chosen one.
STEP_CHOICES = (KEY, KEY_DOWN, KEY_UP, TEXT, DELAY, CLICK, CLICK_DOWN, CLICK_UP)
STEP_LABELS = tuple(STEP_TITLES[kind] for kind in STEP_CHOICES)
#: Each step type's tooltip in the dropdown's list, in STEP_CHOICES order.
STEP_TIPS = ("Press and release a key or combination.",
             "Press a key and leave it down.",
             "Release a key an earlier step left down.",
             "Type a line of text, one key at a time.",
             "Wait before the next step.",
             "Press and release a mouse button.",
             "Press a mouse button and leave it down.",
             "Release a mouse button an earlier step left down.")
#: The warning under the steps of a repeating macro that ends without a delay.
ENDS_WITHOUT_DELAY = ("Ends without a delay, so the next pass can start at once: a game may "
                      "not register the input.")


def ends_without_delay(steps: list[dict], repeat: str) -> list[str]:
    """The warning for a repeating macro whose trailing Delays, if any, add up to 0 ms.

    Its next pass can start as its last step goes, so a release there is followed at once
    by the next press, which a game may not see. The wording is the owner's but for
    "can": the runner starts a pass no sooner than MIN_CYCLE_MS after the last one
    started. Nothing is added at playback (the owner's decision of 2026-09-23).
    """
    tail = 0
    for step in reversed(steps):
        if step.get("type") != DELAY:
            break
        tail += step.get("ms", 0)
    if repeat in (WHILE_HELD, TOGGLE) and steps and tail == 0:
        return [ENDS_WITHOUT_DELAY]
    return []


def macro_uses(name: str, config: Config, library: list[GameProfile]) -> list[str]:
    """Every G key and mouse button that runs the macro `name`, as "G1 on M1",
    "G2 with MR on M2" or "G502 X PLUS G4 in Doom": M profiles first, then the game profiles.

    Matched by slug, as the daemon finds a macro's file. A mouse button by its model, as the
    daemon reads the model's bindings (mouse.bindings_of), since each model's buttons bind on
    their own and the keyboard's G4 and G5 share their names.
    """
    wanted = slug(name)

    def bound(spec: object) -> bool:
        target = macro_name(spec)
        return target is not None and slug(target) == wanted

    found = []
    layers = [(f"on M{i}", profile.keys, profile.shift, profile.mouse)
              for i, profile in sorted(config.profiles.items())]
    layers += [(f"in {profile.name}", profile.bindings, profile.shift, profile.mouse)
               for profile in library]
    for where, keys, shift, mouse in layers:
        found += [f"{key} {where}" for key, spec in keys.items() if bound(spec)]
        found += [f"{key} with MR {where}" for key, spec in shift.items() if bound(spec)]
        found += [f"{mouse_shown(model)} {button.upper()} {where}"
                  for model in sorted(MODEL_IDS)
                  for button, spec in bindings_of(mouse, model).items() if bound(spec)]
    return found


def uses_summary(uses: list[str]) -> str:
    """What a macro's row says of where it runs."""
    return f"Runs on {word_list(uses, 'and')}" if uses else "Not bound to a key or button"


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
#: The status beside Save while no write is in flight.
SAVE_HELP = "Writes the macro to disk"
#: What a read of the macro files says when the open macro's file changed under unsaved edits.
CHANGED_UNDER_EDITS = "{} changed on disk. Your edits stay unsaved; Save writes over it."
#: What a save says once the daemon answered it or the list read after it holds it.
SAVED = "Saved {}"
#: What the list read after a save the daemon did not answer says when it lacks the save,
#: and after such a delete when it still holds the macro.
NOT_SAVED = "Your edits to {} are not saved: the daemon did not answer."
NOT_DELETED = "{} is not deleted: the daemon did not answer."

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
        #: by hand need not be the one Macro.target names.
        self._files: dict[str, str] = {}
        #: The macro in the editor. `saved_name` is None for a new one.
        self.saved_name: str | None = None
        self.steps: list[dict] = []
        self._rows: list[Gtk.Widget] = []
        #: The editor's snapshot when last loaded or saved; anything else is unsaved work.
        self._clean: tuple = ()
        #: The editor's snapshot when the close's discard prompt was answered, so the closes
        #: that follow do not ask again until it is edited. Window._flushed clears it when a
        #: refusal keeps the window up.
        self.discarded: tuple | None = None
        #: A save or a delete is on its way to the daemon. One at a time, and nothing
        #: moves the editor on meanwhile: the worker's queue is shared with the polls
        #: and the mouse's flash writes, so a reply can land seconds later, on whatever
        #: the editor then holds.
        self.writing = False
        #: A save whose reply was lost and which the list read after it did not hold, as
        #: (what it sent, in `_form`, whether it renamed). That read misses a save the
        #: daemon still writes when the request sent before it timed out too, or when the
        #: read failed; it is taken up once its file is listed (`_take_up`), by `reread`
        #: or by a later save's refusal or settling.
        self._late: tuple[tuple, bool] | None = None

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
        self.delete_button = Gtk.Button(label="Delete", css_classes=["destructive-action"])
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
            description="Bind it to a G key (Keyboard tab, Map G keys, Use a macro…) or a "
                        "mouse button (Mouse tab, Macro…). Saving under a new name renames it.")
        self.name_row = Adw.EntryRow(title="Name")
        group.add(self.name_row)
        # Read at Save and Play; a change sends nothing to the daemon.
        self.repeat_row = Adw.ComboRow(
            title="Repeat", model=Gtk.StringList.new(list(REPEAT_LABELS)))
        explain_choices(self.repeat_row, REPEAT_TIPS)
        group.add(self.repeat_row)
        box.append(group)

        steps_group = Adw.PreferencesGroup(
            title="Steps", description=f"Run in order, up to {MAX_STEPS} of them")
        adders = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
                         valign=Gtk.Align.CENTER)
        # One dropdown and Add: eight buttons, one per type, would not fit beside Record.
        self.step_kind = Gtk.DropDown(model=Gtk.StringList.new(list(STEP_LABELS)))
        self.step_kind.set_tooltip_text("The kind of step Add appends")
        explain_choices(self.step_kind, STEP_TIPS)
        adders.append(self.step_kind)
        add = Gtk.Button(label="Add", tooltip_text="Append a step of that kind")
        add.connect("clicked", lambda _b: self.add_step(
            STEP_CHOICES[self.step_kind.get_selected()]))
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

        self.save_status = Gtk.Label(label=SAVE_HELP, xalign=0, hexpand=True,
                                     css_classes=["dimmed"])
        play = Gtk.Button(
            label="Play", tooltip_text=f"Run these steps in {PLAY_DELAY} s, unsaved changes "
                                       f"included: click into the window they are for")
        play.connect("clicked", self.on_play)
        self.save_button = Gtk.Button(label="Save", css_classes=["suggested-action"])
        self.save_button.connect("clicked", self.on_save)
        bar = Gtk.Box(spacing=12, css_classes=["toolbar"])
        bar.append(self.save_status)
        bar.append(play)
        bar.append(self.save_button)
        editor = Adw.ToolbarView(content=box)
        editor.add_bottom_bar(bar)
        return editor

    # -- the library ------------------------------------------------------

    def reload(self, then: Callable[[bool], None] | None = None) -> None:
        """Read the macros on the worker and rebuild the list; `then` gets whether it worked."""
        def loaded(result: tuple[list[dict], bool, list[tuple[str, str]]] | Exception) -> None:
            if isinstance(result, Exception):
                self.win.toast(f"Could not read the macros: {result}")
            else:
                self.macros, self.unlisted, self.skipped = result
                # Added to, never replaced: a macro that left the list has its file
                # only in an earlier one.
                self._files.update((slug(m["name"]), m["file"]) for m in self.macros
                                   if "file" in m)
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
            if self._take_up(self._late):
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
                self.win.toast(f"{name}'s file {self._gone(name)}. Your edits stay unsaved.")
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
        key = slug(name)
        files = {file_name(name), self._files.get(key)}
        if self.unlisted or any(file in files for file, _why in self.skipped):
            return "could not be read"
        return "was deleted"

    def rebuild_list(self) -> None:
        with self._loading:
            self.listbox.remove_all()
            for macro in self.macros:
                # Not markup: an "&" in a name would leave the row empty.
                self.listbox.append(plain_row(Adw.ActionRow, macro["name"],
                                              self._row_subtitle(macro)))
            # After the macros, so a row's index is still its macro's.
            for name, why in self.skipped:
                self.listbox.append(left_out_row(
                    name, f"Left out: {unread_reason(name, why)}",
                    selectable=False, activatable=False))
        self.reselect_current()

    def uses(self, name: str) -> list[str]:
        """Where the window's copy of the files binds the macro `name`."""
        return macro_uses(name, self.win.config, self.win.library)

    def _row_subtitle(self, macro: dict) -> str:
        return (f"{macro_summary(len(macro['steps']), macro['repeat'])}. "
                f"{uses_summary(self.uses(macro['name']))}")

    def refresh_uses(self) -> None:
        """Say again where each macro runs, after the window read the files."""
        for index, macro in enumerate(self.macros):
            row = self.listbox.get_row_at_index(index)
            if isinstance(row, Adw.ActionRow):
                row.set_subtitle(self._row_subtitle(macro))

    def on_select(self, _box: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if self._loading.active or row is None:
            return
        index = row.get_index()
        if not 0 <= index < len(self.macros):
            return
        chosen = self.macros[index]
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
        uses = self.uses(name)
        confirm(self.win, f"Delete {name}?",
                (f"The file is removed. {word_list(uses, 'and')} then "
                 f"{'does' if len(uses) == 1 else 'do'} nothing until rebound." if uses
                 else "The file is removed. No key or button runs it."),
                "Delete", lambda: self._do_delete(name))

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
            self.win.toast(f"Deleted {name}. Later edits stay unsaved.")

        def done(reply: dict | None) -> None:
            if reply is not None:
                deleted()
                self.reload()

        def settle(read: bool) -> None:
            # A timeout can come after the file went; the list read again, once the daemon
            # answers a later request, says whether it did. One that could not be read has
            # said so instead.
            if read and self._listed(name) is None:
                deleted()
            elif read:
                self.win.toast(NOT_DELETED.format(name))
        self._send_write({"command": "macro_delete", "value": name}, done, settle)

    # -- the editor -------------------------------------------------------

    def edit(self, macro: dict, saved: bool = True) -> None:
        self._late = None
        self.saved_name = macro["name"] if saved else None
        self.steps = [dict(step) for step in macro["steps"]]
        self.name_row.set_text(macro["name"])
        self.repeat_row.set_selected(REPEAT_CHOICES.index(macro["repeat"]))
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
        for row in self._rows:
            if isinstance(row, Adw.SpinRow):
                row.update()
        return self._form({"name": self._open_name(),
                           "repeat": REPEAT_CHOICES[self.repeat_row.get_selected()],
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
        dialog.add_response("cancel", "Keep editing")
        dialog.add_response("discard", "Discard")
        dialog.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def answered(_d: Adw.AlertDialog, response: str) -> None:
            if response == "discard":
                go_on()
            else:
                self.reselect_current()
        dialog.connect("response", answered)
        dialog.present(self.win)

    def asks_before_close(self, close: Callable[[], None]) -> bool:
        """Ask before the window's close discards unsaved edits, and whether it asked.

        Not while a save or a delete is on its way, which the close waits for, nor again
        for the state a Discard was given for. Discard calls `close`: the window's close(),
        not destroy(), which emits no close-request, so the window's teardown would not run.
        """
        if self.writing:
            return False
        state = self.snapshot()
        if state in (self._clean, self.discarded):
            return False

        def discard() -> None:
            self.discarded = self.snapshot()
            close()
        self.confirm_discard("Closing the window", discard)
        return True

    def add_step(self, kind: str) -> None:
        """Append a step of `kind`, with the standard delay between two events (`with_step`)."""
        if len(self.steps) >= MAX_STEPS:
            self.win.toast(f"A macro holds at most {MAX_STEPS} steps")
            return
        self.steps = with_step(self.steps, NEW_STEP[kind])
        self.rebuild_steps()

    def remove_step(self, index: int) -> None:
        """Remove one step; the last may go too, since only Save and Play require a step."""
        del self.steps[index]
        self.rebuild_steps()

    def move_step(self, index: int, by: int) -> None:
        target = index + by
        if not 0 <= target < len(self.steps):
            return
        self.steps[index], self.steps[target] = self.steps[target], self.steps[index]
        self.rebuild_steps()

    def rebuild_steps(self) -> None:
        self.step_list.remove_all()
        self.check_holds()
        if self.steps:
            self._rows = [self._step_row(index, step) for index, step in enumerate(self.steps)]
        else:
            # A heading over nothing reads as a tab that failed to load.
            self._rows = [Adw.ActionRow(title="No steps yet", sensitive=False,
                                        subtitle="Pick a type and press Add, or record one")]
        for row in self._rows:
            self.step_list.append(row)

    def check_holds(self) -> None:
        """Show or hide the warnings under the steps."""
        repeat = REPEAT_CHOICES[self.repeat_row.get_selected()]
        found = unbalanced(self.steps, repeat) + ends_without_delay(self.steps, repeat)
        self.hold_label.set_text("\n".join(found))
        self.hold_warning.set_visible(bool(found))

    def _step_row(self, index: int, step: dict) -> Gtk.Widget:
        """One editable step. Each value is set before its handler is connected, so
        setting it up is not read back as an edit."""
        kind = step["type"]
        title, subtitle = step_summary(step)
        row: Adw.EntryRow | Adw.ActionRow
        if kind == TEXT:
            row = Adw.EntryRow(title=f"{index + 1}. {title}", use_markup=False)
            row.set_text(step["value"])
            row.set_max_length(MAX_TEXT_LENGTH)
            row.connect("changed", lambda r, s=step: s.__setitem__("value", r.get_text()))
        elif kind == DELAY:
            row = Adw.SpinRow(
                title=f"{index + 1}. {title}",
                subtitle="Milliseconds to wait before the next step",
                adjustment=Gtk.Adjustment(value=step["ms"], lower=0,
                                          upper=MAX_DELAY_MS, step_increment=RECORD_ROUND_MS,
                                          page_increment=100))
            row.connect("notify::value",
                        lambda r, _p, s=step: self._set_field(s, "ms", round(r.get_value())))
        elif kind in CLICK_STEPS:
            row = Adw.ComboRow(title=f"{index + 1}. {title}",
                               model=Gtk.StringList.new(list(CLICK_LABELS)))
            row.set_selected(CLICK_CHOICES.index(step["button"]))
            row.connect("notify::selected", lambda r, _p, s=step: self._set_field(
                s, "button", CLICK_CHOICES[r.get_selected()]))
        else:
            # Not markup: the subtitle is what the step sends.
            row = Adw.ActionRow(title=f"{index + 1}. {title}", subtitle=subtitle,
                                use_markup=False)
            change = Gtk.Button(label="Change…", valign=Gtk.Align.CENTER)
            change.connect("clicked", lambda _b, i=index: self._capture(i))
            row.add_suffix(change)

        for icon, by, tip in (("go-up-symbolic", -1, "Move up"),
                              ("go-down-symbolic", 1, "Move down")):
            button = Gtk.Button(icon_name=icon, valign=Gtk.Align.CENTER,
                                css_classes=["flat"], tooltip_text=tip)
            button.connect("clicked", lambda _b, i=index, d=by: self.move_step(i, d))
            row.add_suffix(button)
        remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                            css_classes=["flat"], tooltip_text="Remove this step")
        remove.connect("clicked", lambda _b, i=index: self.remove_step(i))
        row.add_suffix(remove)
        return row

    def _set_field(self, step: dict, field: str, value: int | str) -> None:
        """A delay's milliseconds or a click's button, edited in place: the warnings under
        the steps may change."""
        step[field] = value
        self.check_holds()

    def _capture(self, index: int) -> None:
        """Ask for a key for one step: keys only, as a macro cannot run a macro, and no Unbind."""
        dialog = CaptureDialog(self.win, f"Step {index + 1}", can_unbind=False,
                               title=f"Change step {index + 1}")
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
        dialog = Adw.AlertDialog(
            heading="Replace the steps?",
            body=f"The recording ({counted(len(steps), 'step')}) replaces the "
                 f"{counted(len(self.steps), 'step')} in the editor, unsaved.")
        dialog.add_response("cancel", "Keep these")
        dialog.add_response("replace", "Replace")
        dialog.set_response_appearance("replace", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response",
                       lambda _d, r: self._replace_steps(steps) if r == "replace" else None)
        dialog.present(self.win)

    def _replace_steps(self, steps: list[dict]) -> None:
        self.steps = [dict(step) for step in steps]
        self.rebuild_steps()
        self.win.toast(f"Recorded {counted(len(self.steps), 'step')}. Save when the macro "
                       f"looks right.")

    # -- playing ----------------------------------------------------------

    def on_play(self, _b: Gtk.Button | None) -> None:
        """Play what the editor holds, saved or not, PLAY_DELAY seconds from now.

        The steps go whole, as `macro_save` would send them, and nothing is written. The
        reply puts the countdown and Stop on the window's macro banner. With no key held,
        `once` and `while_held` run one pass and `toggle` runs until Stop.
        """
        if (name := self._sendable("playing")) is None:
            return
        request = {"command": "macro_run", "value": name,
                   "repeat": REPEAT_CHOICES[self.repeat_row.get_selected()],
                   "steps": [dict(step) for step in self.steps], "delay": PLAY_DELAY}
        self.win.call(request,
                      lambda reply: self.win.macro_banner.take(reply) if reply is not None else None)

    def _sendable(self, doing: str) -> str | None:
        """The editor's name, or None once a toast or a dialog said why the daemon would
        refuse to run or save the macro; `doing` ends the sentence for no steps."""
        name = self._open_name()
        if not name:
            self.win.toast("A macro needs a name")
        elif not self.steps:
            self.win.toast(f"Add at least one step before {doing}")
        elif not self._untypable():
            return name
        return None

    def _untypable(self) -> bool:
        """Say which text step the daemon would refuse, by its row's number (the
        daemon's refusal numbers steps from 0, as the file does), and whether one would be."""
        for index, step in enumerate(self.steps):
            if step["type"] == TEXT and (problem := text_problem(step["value"])) is not None:
                # As the imports do: one over TOAST_MAX opens a dialog.
                self.win.refused({"error": f"Step {index + 1}: {problem}"})
                return True
        return False

    # -- saving -----------------------------------------------------------

    def on_save(self, _b: Gtk.Button | None) -> None:
        if (name := self._sendable("saving")) is None:
            return
        repeat = REPEAT_CHOICES[self.repeat_row.get_selected()]
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
        earlier save, whose reply was lost: that is taken up (`_late`)."""
        if (self.writing or self.saved_name is not None or not self.steps
                or self._open_name() != name):
            return      # the editor has moved on since
        if self._take_up(self._late):
            return
        taken = self._taken(name)
        if taken is not None:
            self.confirm_overwrite(taken, name, REPEAT_CHOICES[self.repeat_row.get_selected()])

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
        """Send the macro, counted as saved from the moment it is sent.

        The steps are copied and snapshotted here, not when the reply lands: the editor
        stays live across the round trip, and an edit made meanwhile must leave it dirty.
        A refusal puts the old snapshot back and leaves an earlier unanswered save to be
        taken up (`_late`), and an unanswered save is settled by the list read again once
        the daemon answers a later request, which also takes up an earlier one it lists.
        The library, New, Delete and Save wait for the reply, so it lands on the macro it
        was sent for.
        """
        steps = [dict(step) for step in self.steps]
        late, self._late = self._late, None
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
                if self._take_up(late):
                    return
                if request.get("new"):
                    # Most likely a macro of that name the list did not hold.
                    self.reload(
                        lambda read: self._ask_after_refusal(name) if read else None)
            else:
                self._saved(reply.get("saved", name), renamed)
                self.reload()

        def settle(read: bool) -> None:
            # A timeout can come after the write: settle the save by the list read again,
            # with the editor held until then. Taken as refused, a rename the daemon did
            # would send the old name as `previous` at every later Save, and be refused.
            # A failed read leaves the old list, which holds these steps only if saved.
            if self._on_disk(sent):
                self._saved(name, renamed)
                self.reselect_current()
                return
            self._clean = before
            # An earlier save whose reply was lost can have landed before this one; had
            # it renamed, the daemon refused this one, sent with the old name as
            # `previous`.
            self._take_up(late)
            self._late = (sent, renamed)
            if read:
                # A list that could not be read has said so instead.
                self.win.toast(NOT_SAVED.format(name))
        if replace:
            request["replace"] = True
        elif self.saved_name is None:
            # The list may predate a file added by hand, which the daemon
            # would write over without a word.
            request["new"] = True
        self._send_write(request, done, settle)

    def _send_write(self, request: dict, answered: Callable[[dict | None], None],
                    settle: Callable[[bool], None]) -> None:
        """Send a save or a delete with the editor held (`_set_writing`) until `answered`
        has the reply, or None for a refusal; unanswered, until `settle` has the list
        read again after it (`_after_the_daemon`)."""
        def done(reply: dict | None) -> None:
            answered(reply)
            self._set_writing(False)

        def unanswered(_error: Exception) -> None:
            def settled(read: bool) -> None:
                settle(read)
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

    def _take_up(self, late: tuple[tuple, bool] | None) -> bool:
        """Take `late`, a save whose reply was lost, as saved, and say so, if the list last
        read holds it: what it sent is the saved state, and edits since stay unsaved.
        Whether it did."""
        if late is None or not self._on_disk(late[0]):
            return False
        self._late = None
        self._clean = late[0]
        self._saved(late[0][0], late[1])
        self.reselect_current()
        return True

    def _saved(self, name: str, renamed: bool) -> None:
        """Make `name` the open macro's saved name and say so."""
        self.saved_name = name
        self.win.toast(SAVED.format(name))
        if renamed:
            # The daemon repointed the old name's bindings in config.json and the game
            # profiles, and the Keyboard tab draws from the window's copy.
            self.win.resync()

    def _on_disk(self, form: tuple) -> bool:
        """Whether the list last read holds a macro whose `_form` is `form`."""
        return any(self._form(m) == form for m in self.macros)

    def _set_writing(self, busy: bool) -> None:
        """Disable what moves the editor on or sends a second write while one is in flight."""
        self.writing = busy
        for widget in (self.listbox, self.new_button, self.delete_button, self.save_button):
            widget.set_sensitive(not busy)
        self.save_status.set_text("Waiting for the daemon…" if busy else SAVE_HELP)
