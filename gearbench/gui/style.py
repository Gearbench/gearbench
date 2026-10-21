"""The window's shared CSS and widget helpers.

Each color in play gets two rules named after it, a swatch fill and a key
fill: that is how a Gtk.Button shows an arbitrary color without a custom
widget. A picker owns its hue and swatch rules, so two pickers on screen do
not fight over one rule.
"""

from __future__ import annotations

import weakref
from collections.abc import Callable, Collection, Iterable, Sequence
from pathlib import Path
from typing import Literal, NamedTuple, cast

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

from ..astro import NAME_BYTES
from ..battery import (
    CHARGING,
    CRITICAL_PERCENT,
    FAULT,
    FULL,
    LOW_PERCENT,
    NEARLY_FULL,
    icon_name,
    reading_words,
)
from ..config import format_color, profile_label, shown_path
from ..eq_presets import is_name
from ..report import REPORT_FORM
from ..wording import clock_shown, reason, word_list

#: The fixed rules, which the window loads once for every tab: the swatches, the
#: Mouse tab's stage dots, the pickers' square, strip and markers, and the board's
#: dimmed and mappable keys. The picker's square and strip copy the focus ring
#: libadwaita draws on GTK's color plane: libadwaita has none for a Gtk.DrawingArea.
BASE_SHEET = """
.g915-swatch, .g915-saved { border-radius: 5px;
  border: 1px solid alpha(currentColor, 0.35); }
.g915-stage-dot { border-radius: 50%; border: 1px solid alpha(currentColor, 0.35); }
.g915-sv { border-radius: 6px;
  background-image: linear-gradient(to top, #000000, rgba(0,0,0,0)),
    linear-gradient(to right, #ffffff, rgba(255,255,255,0)); }
.g915-hue { border-radius: 5px;
  background-image: linear-gradient(to right, #ff0000 0%, #ffff00 17%,
    #00ff00 33%, #00ffff 50%, #0000ff 67%, #ff00ff 83%, #ff0000 100%); }
.g915-sv:focus:focus-visible, .g915-hue:focus:focus-visible {
  outline: 2px solid alpha(@accent_color, 0.5); outline-offset: 2px; }
@media (prefers-contrast: more) { .g915-sv:focus:focus-visible,
  .g915-hue:focus:focus-visible { outline-color: alpha(@accent_color, 0.8); } }
.g915-marker { border: 2px solid #ffffff; border-radius: 7px;
  box-shadow: 0 0 0 1px rgba(0,0,0,0.6); }
.g915-dim { opacity: 0.55; }
.g915-mappable { border: 2px solid @accent_color; font-weight: bold; }
@media (prefers-contrast: more) { .g915-dim { opacity: 1; } }
"""
#: The board's key rule, with a placeholder for the font size, which scales with
#: the drawn keyboard.
KEY_TEMPLATE = """
.g915-key {{ font-size: {font}px;
  padding: 0; min-height: 0; min-width: 0; border-radius: 4px; }}
"""

#: A device indicator's third state: the device or its receiver is attached
#: and the device does not answer, switched off or asleep.
ASLEEP = "asleep"
#: What each tab's status row says in that state.
MOUSE_ASLEEP = "Asleep or switched off. Move it or switch it on."
KEYBOARD_ASLEEP = "Asleep or switched off. Press a key or switch it on."

#: Each device indicator state's icon, style class and label. A shape and words,
#: so the state reaches a screen reader and anyone who cannot tell the colors
#: apart; libadwaita's classes, so the color follows the theme. Not
#: emblem-ok-symbolic: Adwaita's icon theme (50) lacks it. False's label is the
#: status pages' "not available" and blames no device: a tab also sets False
#: while the daemon does not answer, when the device may be plugged in and fine.
DOT_STATES = {True: ("object-select-symbolic", "success", "Connected"),
              False: ("dialog-error-symbolic", "error", "Not available"),
              ASLEEP: ("dialog-warning-symbolic", "warning", "Asleep or switched off")}


def status_dot() -> Gtk.Image:
    """The indicator beside a device's status. `set_dot(dot, state)` sets it."""
    dot = Gtk.Image(valign=Gtk.Align.CENTER)
    set_dot(dot, False)
    return dot


def set_dot(dot: Gtk.Image, state: bool | str) -> None:
    """Show a device as found, not available or not answering (ASLEEP)."""
    icon, style, label = DOT_STATES[state]
    dot.set_from_icon_name(icon)
    for other in {s for _i, s, _l in DOT_STATES.values()} - {style}:
        dot.remove_css_class(other)
    dot.add_css_class(style)
    dot.update_property([Gtk.AccessibleProperty.LABEL], [label])


def put_dot(row: Adw.ActionRow, dot: Gtk.Image, state: bool | str | None, label: str) -> None:
    """Set a switch row's dot, `label` its name for a screen reader, or for None take it out
    of the row: hidden, it still indents the title by the row's spacing."""
    attached = dot.get_parent() is not None
    if state is None:
        if attached:
            row.remove(dot)
        return
    set_dot(dot, state)
    # set_dot's words are a device's: "Not available" for a unit that failed.
    dot.update_property([Gtk.AccessibleProperty.LABEL], [label])
    if not attached:
        row.add_prefix(dot)


def unit_words(words: str) -> str:
    """A unit's summary as its switch row's subtitle: capitalized, but a command keeps its
    case, "systemctl --user did not answer"."""
    return words if words.startswith("systemctl ") else words[:1].upper() + words[1:]


def displayable(text: str) -> str:
    """`text` with U+FFFD for each lone surrogate, on which PyGObject raises
    UnicodeEncodeError. surrogateescape file names and json.loads of an
    escape such as "\\udce9" both make them."""
    return "".join("\ufffd" if "\ud800" <= c <= "\udfff" else c for c in text)


def plain_row[R: Adw.ActionRow | Adw.ExpanderRow](kind: type[R], title: str, subtitle: str,
                                                  **props: object) -> R:
    """A row showing `title` and `subtitle` as plain text, lone surrogates as U+FFFD.

    Both are set after construction: given to the constructor, a subtitle and
    a combo row's title are parsed as markup before use_markup=False applies,
    and GTK logs a warning.
    """
    # The stubs spell out each row class's properties, and `props` are whichever `kind` takes.
    row = kind(use_markup=False, **props)  # type: ignore[arg-type]
    row.set_title(displayable(title))
    row.set_subtitle(displayable(subtitle))
    return row


def left_out_row(title: str, subtitle: str, **props: object) -> Adw.ActionRow:
    """A dimmed plain_row for a file left out. Its warning icon is a decoration: the
    words say what is wrong, and without the role a screen reader meets an image
    with no name."""
    row = plain_row(Adw.ActionRow, title, subtitle, css_classes=["dimmed"], **props)
    row.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic",
                             accessible_role=Gtk.AccessibleRole.PRESENTATION))
    return row


class LeftOutRows:
    """A left_out_row per file left out, in `group`, rebuilt only when the (file, reason)
    list changes, so the keyboard focus on one stays in place. `said(file, reason)` is a
    row's subtitle."""

    def __init__(self, group: Adw.PreferencesGroup, said: Callable[[str, str], str]) -> None:
        self._group = group
        self._said = said
        self.rows: list[Adw.ActionRow] = []
        self._shown: list[tuple[str, str]] = []

    def show(self, skipped: list[tuple[str, str]]) -> None:
        """Put a row per (file, reason) in `skipped` in the group, in place of the last."""
        if skipped == self._shown:
            return
        self._shown = list(skipped)
        for row in self.rows:
            self._group.remove(row)
        self.rows = [left_out_row(name, self._said(name, why)) for name, why in skipped]
        for row in self.rows:
            self._group.add(row)


def holds_focus(focus: Gtk.Widget | None, widget: Gtk.Widget) -> bool:
    """True when `focus`, the widget with the cursor, is `widget` or inside it."""
    return focus is not None and (focus is widget or focus.is_ancestor(widget))


class EntryTexts:
    """The text a page last put in each of its entry rows, so a poll leaves a row being
    typed in alone and fills the others."""

    def __init__(self) -> None:
        self._texts: dict[Adw.EntryRow, str] = {}

    def last(self, row: Adw.EntryRow) -> str:
        """The text the page last put in `row`, or "" before it put any."""
        return self._texts.get(row, "")

    def typed(self, row: Adw.EntryRow) -> bool:
        """True while `row` holds text the page did not put there."""
        return row.get_text() != self._texts.get(row)

    def typing(self, parent: Gtk.Window, row: Adw.EntryRow) -> bool:
        """True while the cursor is in `row` and its text is not what the page
        last put there, so a poll does not overwrite typing. The cursor
        alone is not enough: Enter and the apply button leave it in the row, which
        would then miss every later change until the cursor moved."""
        return holds_focus(parent.get_focus(), row) and self.typed(row)

    def took(self, row: Adw.EntryRow) -> None:
        """The row's text was sent and is the page's own now, so the polls fill the row
        again."""
        self._texts[row] = row.get_text()

    def show(self, row: Adw.EntryRow, text: str) -> None:
        """Put `text` in an entry row as the page's own. The text a row holds is left alone:
        set_text moves the cursor to the start and drops a selection. With the cursor in the
        row, libadwaita 1.9.1 arms the apply button at any set_text, so Enter would send the
        value again; turning the button off and on clears that, which its docs do not say
        (measured on 1.9.1, 2026-09-30)."""
        if row.get_text() != text:
            row.set_show_apply_button(False)
            row.set_text(text)
            row.set_show_apply_button(True)
        self._texts[row] = text


#: The Name rows' words for what astro.preset_name refuses, the A50's and the G432's.
NAME_BLANK = "A preset name cannot be blank"
NAME_LONG = "A preset name is at most {} bytes, not {}: an accented letter takes 2 or more"
NAME_CONTROL = "A preset name cannot hold a control character, such as a tab or a line break"


def name_refusal(name: str) -> str | None:
    """Why a Name row cannot take `name`, already stripped, in words, or None. Whether it
    can is astro.preset_name's rule (eq_presets.is_name), which past a blank name and a long
    one refuses a control character."""
    if is_name(name):
        return None
    # NAME_BYTES starts at 1, so the only name too short is the empty one.
    if not name:
        return NAME_BLANK
    if (size := len(name.encode())) > NAME_BYTES[1]:
        return NAME_LONG.format(NAME_BYTES[1], size)
    return NAME_CONTROL


def preset_label(number: int, name: str | None) -> str:
    """An EQ preset as a dropdown lists it: its number, and its name when there is one."""
    return f"{number} · {name}" if name else f"Preset {number}"


#: The A50's Import row's title, and astroeq_dialog's.
ASTROEQ_IMPORT = "Import from Astro Command Center…"
ASTROEQ_TITLE = "Import from Astro Command Center"
#: The A50's Import row's refusal of the file, with its name and why.
ASTROEQ_REFUSED = "{} could not be imported: {}"
#: The Reset to default rows' title, the A50's and the G432's.
PRESET_RESET = "Reset to default"


def astroeq_dialog() -> Gtk.FileDialog:
    """The file dialog the A50's Import row and Load preset to headset's Add row open:
    Command Center's presets, *.astroeq."""
    dialog = Gtk.FileDialog(title=ASTROEQ_TITLE, modal=True)
    filters = Gio.ListStore.new(Gtk.FileFilter)
    presets = Gtk.FileFilter(name="Command Center presets")
    presets.add_pattern("*.astroeq")
    filters.append(presets)
    dialog.set_filters(filters)
    return dialog


#: Every Headset tab slider's width, fixed so the ends line up down the tab.
SLIDER_WIDTH = 280


def percent(level: int) -> str:
    """A slider's level in words, as its row's subtitle shows it and a screen reader says
    it (spoken_scale): "40%"."""
    return f"{level}%"


def spoken_scale(low: float, high: float, label: str, page: float = 10, origin: bool = True,
                 spoken: Callable[[int], str] = percent) -> Gtk.Scale:
    """A Headset tab slider, named `label` for a screen reader; `origin` False for a
    balance or a gain, where a fill from the left end would read as a level. `spoken` is
    its value as a screen reader says it, the words its row shows: a percent by default;
    a balance, 0 to 255, and a gain pass their own."""
    scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, draw_value=False, hexpand=False,
                      valign=Gtk.Align.CENTER, has_origin=origin,
                      adjustment=Gtk.Adjustment(lower=low, upper=high, value=low,
                                                step_increment=1, page_increment=page))
    scale.set_round_digits(0)
    scale.set_size_request(SLIDER_WIDTH, -1)
    scale.update_property([Gtk.AccessibleProperty.LABEL], [label])

    def say(scale: Gtk.Scale) -> None:
        scale.update_property([Gtk.AccessibleProperty.VALUE_TEXT],
                              [spoken(round(scale.get_value()))])
    scale.connect("value-changed", say)
    say(scale)
    return scale


class PointerChange(NamedTuple):
    """A pointer button going down or coming up: the event, the button's toolkit number
    and whether it went down."""

    event: Gdk.Event
    button: int
    pressed: bool


def pointer_button(controller: Gtk.EventControllerLegacy) -> PointerChange | None:
    """The button press or release the event `controller` is handling, or None for any other
    event and when it holds none. A touchscreen sends no button events, so its
    pointer-emulating finger is the primary button, as a click gesture takes it, and a
    cancel lets go, or the button stays down."""
    # PyGObject hands the handler no event; the controller holds it while a handler runs,
    # and only then (gtk_event_controller_get_current_event).
    event = controller.get_current_event()
    if event is None:
        return None
    kind = event.get_event_type()
    if kind in (Gdk.EventType.BUTTON_PRESS, Gdk.EventType.BUTTON_RELEASE):
        return PointerChange(event, event.get_button(), kind == Gdk.EventType.BUTTON_PRESS)
    if kind in (Gdk.EventType.TOUCH_BEGIN, Gdk.EventType.TOUCH_END,
                Gdk.EventType.TOUCH_CANCEL) and event.get_pointer_emulated():
        return PointerChange(event, Gdk.BUTTON_PRIMARY, kind == Gdk.EventType.TOUCH_BEGIN)
    return None


def weakly[R](method: Callable[..., R]) -> Callable[..., R | Literal[False]]:
    """`method` holding its object weakly, for a handler on a dialog's child
    widget, a controller or a filter func: a strong one makes a cycle through
    GTK that Python's collector cannot see, and the closed dialog is never
    freed. A handler on the dialog itself needs none. Once the object is gone
    it does nothing and answers False, so a dialog whose handlers all come
    through here needs held_while_shown."""
    ref = weakref.WeakMethod(method)

    def call(*args: object) -> R | Literal[False]:
        bound = ref()
        return False if bound is None else bound(*args)
    return call


#: The dialogs held_while_shown keeps, each until it closes.
_SHOWN: set[Adw.Dialog] = set()


def held_while_shown(dialog: Adw.Dialog) -> None:
    """Keep `dialog`'s Python object until the dialog closes. GTK holds a
    presented dialog but not its Python object, which the collector frees,
    and every handler through `weakly` then does nothing. A caller that keeps
    the dialog needs none of this. `force_close()` on a dialog shown as its own
    window emits no "closed" (libadwaita 1.9's adw_dialog_force_close), so the
    object stays until the process ends."""
    _SHOWN.add(dialog)
    dialog.connect("closed", _SHOWN.discard)


#: picked_path's toast for a file with no local path; the note behind Details names it.
NOT_LOCAL = "The file is not on a local disk: copy it to this computer first"


def picked_path(dialog: Gtk.FileDialog, result: Gio.AsyncResult,
                toast: Callable[..., object]) -> Path | None:
    """The local file picked in `dialog`, or None, with a toast for a failure."""
    try:
        file = dialog.open_finish(result)
    except GLib.Error as exc:
        # Cancel is an error here too, and says nothing that needs saying.
        if not exc.matches(Gtk.dialog_error_quark(), Gtk.DialogError.DISMISSED):
            toast(f"Could not open the file: {exc.message}")
        return None
    if (path := file.get_path()) is None:
        # GVfs gives a network share or a phone a path only through its FUSE
        # mount, and the reads need a path. A phone's location runs past a toast's
        # width, so it goes behind Details.
        toast(NOT_LOCAL, full=f"Could not open {file.get_parse_name()}: it is not on a "
                              "local disk. Copy it to this computer first.")
        return None
    return Path(path)


def confirm(parent: Gtk.Widget, heading: str, body: str, response: str,
            then: Callable[[], None], destructive: bool = True) -> None:
    """A confirmation over `parent`; `then` runs on `response`."""
    dialog = Adw.AlertDialog(heading=heading, body=body)
    key = response.lower()
    dialog.add_response("cancel", "Cancel")
    dialog.add_response(key, response)
    if destructive:
        dialog.set_response_appearance(key, Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.set_default_response("cancel")
    dialog.set_close_response("cancel")
    dialog.connect("response", lambda _d, r: then() if r == key else None)
    dialog.present(parent)


#: Width of a confirmation longer than a sentence: an Adw.AlertDialog's own, about 356px,
#: wraps a list into a narrow column.
WIDE_DIALOG = 560


def explain(dialog: Adw.AlertDialog, paragraphs: tuple[str, ...],
            bullets: tuple[str, ...] = ()) -> None:
    """Put the dialog's text, left-aligned, in its extra child, with hanging bullets.

    The built-in body is centered, and centered bullets have no common left edge.
    """
    # set_content_width has no effect while the dialog follows its content size.
    dialog.set_follows_content_size(False)
    dialog.set_content_width(WIDE_DIALOG)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                  margin_top=4, margin_bottom=4)
    for text in paragraphs:
        box.append(Gtk.Label(label=text, xalign=0.0, wrap=True, max_width_chars=64))
    for text in bullets:
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        line.append(Gtk.Label(label="•", xalign=0.0, valign=Gtk.Align.START))
        line.append(Gtk.Label(label=text, xalign=0.0, wrap=True,
                              max_width_chars=60, hexpand=True))
        box.append(line)
    dialog.set_extra_child(box)


#: A device tab's status page title while the daemon does not answer and the
#: device has not been seen. The reason under it is `unreachable`'s; the
#: window's banner carries Start it.
DAEMON_WAIT = "Waiting for the daemon"


class DeviceView:
    """A device tab's content: an Adw.StatusPage until the device is seen,
    then the editor for as long as the window is open (the owner's decision
    of 2026-09-24).

    The page spins until the first read comes back, answered or failed, then
    says why the device is not there: the daemon's reason, or `unreachable`'s
    while the daemon does not answer. A device seen once keeps its editor when
    it goes; its status row says it is away. The editor scrolls, under the
    tab's top bars.
    """

    def __init__(self, editor: Gtk.Widget, icon: str, looking: str) -> None:
        #: The device's icon, shown once the spinner stops.
        self.icon = icon
        #: The device has been seen since the window opened.
        self.seen = False
        self.status = Adw.StatusPage(title=looking)
        # A status page takes a paintable, not a widget; set_icon_name later replaces it.
        self.status.set_paintable(Adw.SpinnerPaintable(widget=self.status))
        self.stack = Gtk.Stack()
        self.stack.add_named(self.status, "status")
        self.stack.add_named(Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, child=editor), "editor")
        self.stack.set_visible_child_name("status")

    def found(self) -> None:
        """The device is there: the editor, from now on."""
        self.seen = True
        self.stack.set_visible_child_name("editor")

    def missing(self, title: str, reason: str) -> None:
        """Stop the spinner and say why the device is not there. The description
        is markup, so the reason, which can hold a node or socket path, is escaped."""
        self.status.set_icon_name(self.icon)
        self.status.set_title(title)
        self.status.set_description(GLib.markup_escape_text(reason[:1].upper() + reason[1:]))


def top_switcher(stack: Gtk.Stack) -> Gtk.StackSwitcher:
    """The row of buttons at the top of a tab that picks the device it shows, hidden until
    there are two to pick from."""
    return Gtk.StackSwitcher(stack=stack, halign=Gtk.Align.CENTER, margin_top=12,
                             visible=False)


class Switcher:
    """A top_switcher for a tab that shows each device it picks in the same rows: a button
    per choice over a stack whose pages are empty, there to name the choices. `picked` gets
    the index of the choice clicked; `show` moves the switcher with no call, so a poll's
    choice is never sent back."""

    def __init__(self, label: str, picked: Callable[[int], None]) -> None:
        self._picked = picked
        #: The choices' labels, in the buttons' order.
        self.labels: list[str] = []
        #: show is putting the switcher on a choice, which is not a pick.
        self._showing = False
        self.stack = Gtk.Stack()
        self.stack.connect("notify::visible-child-name", self._on_pick)
        self.widget = top_switcher(self.stack)
        # Each button is named by its label; the whole by what it picks.
        self.widget.update_property([Gtk.AccessibleProperty.LABEL], [label])

    @property
    def selected(self) -> int | None:
        """The index of the choice the switcher is on, None with none."""
        name = self.stack.get_visible_child_name()
        return None if name is None else int(name)

    def show(self, labels: Sequence[str], at: int | None, shown: bool,
             unpicked: Collection[int] = ()) -> None:
        """Offer `labels`, with the switcher on the one at `at`, and shown while `shown`; the
        button of each choice whose index is in `unpicked` is insensitive, so it shows and
        cannot be picked. None for `at` leaves it where it is, which for new labels is on the
        first."""
        self._showing = True
        try:
            if list(labels) != self.labels:
                while (page := self.stack.get_first_child()) is not None:
                    self.stack.remove(page)
                for index, words in enumerate(labels):
                    self.stack.add_titled(Gtk.Box(), str(index), words)
                self.labels = list(labels)
            if at is not None:
                self.stack.set_visible_child_name(str(at))
        finally:
            self._showing = False
        # The StackSwitcher's children are its buttons, one for each page in the pages' order,
        # each a label of the page's title, and it leaves their sensitivity alone: read off
        # GTK 4.22.4 on 2026-10-20, not promised by its documentation. A label of two lines
        # is centered, as one line is. Each button's own drop motion controller switches the
        # page under a drag held over it for half a second, which here would be a pick no one
        # clicked, so it goes.
        button, index = self.widget.get_first_child(), 0
        while button is not None:
            for controller in list(button.observe_controllers()):
                if isinstance(controller, Gtk.DropControllerMotion):
                    button.remove_controller(controller)
            button.set_sensitive(index not in unpicked)
            cast("Gtk.Label", button.get_first_child()).set_justify(Gtk.Justification.CENTER)
            button, index = button.get_next_sibling(), index + 1
        self.widget.set_visible(shown)

    def _on_pick(self, _stack: Gtk.Stack, _pspec: GObject.ParamSpec) -> None:
        # A page goes only in show, so outside it the stack is on one.
        if not self._showing:
            self._picked(cast("int", self.selected))


#: The size of a dialog that lists a library.
LIST_DIALOG_WIDTH = 560
LIST_DIALOG_HEIGHT = 640


#: The longest text a toast carries whole; about 105 characters of its one line
#: fit in the window. Longer refusals open a dialog, longer notes use short_note.
TOAST_MAX = 80


def short_note(note: str, fallback: str, one_sentence: bool = False) -> str | None:
    """A note's toast line when it is longer than TOAST_MAX: its first sentence,
    or `fallback` when that is too long too; None when the note fits.
    `one_sentence` gives `fallback` for a one-sentence note, as a game profile's
    name in it can hold ". " (R.E.P.O.)."""
    if len(note) <= TOAST_MAX:
        return None
    first = note if one_sentence else note.split(". ", 1)[0].rstrip(".") + "."
    line = first if len(first) <= TOAST_MAX else fallback
    return line[:1].upper() + line[1:]


def toast_note(toast: Callable[..., object], note: str, fallback: str,
               one_sentence: bool = False) -> None:
    """Toast `note` whole, or short_note's line with the note behind Details."""
    short = short_note(note, fallback, one_sentence)
    if short is None:
        toast(note)
    else:
        toast(short, full=note)


#: A save that failed: where it went and its cause in words, and the toast's line when that
#: is too long, with where it went said shorter.
SAVE_FAILED = "Could not save {}: {}"
SAVE_FAILED_LINE = "Could not save {}"


def toast_not_saved(toast: Callable[..., object], path: Path, error: OSError) -> None:
    """A save of `path` that failed, its cause in words: a toast too short for the path names
    the file, the whole sentence behind Details."""
    toast_note(toast, SAVE_FAILED.format(shown_path(path), reason(error)),
               SAVE_FAILED_LINE.format(path.name), one_sentence=True)


def editing_text(overlay: str | None, auto: bool, processes: list[str], active: int,
                 name: str, program: str | None) -> str:
    """Which layer the window edits, for the line on the Keyboard and Mouse tabs.

    `program` is the one that loaded `overlay`, from the status's
    `auto_process`. Without it, `processes`, the loaded profile's programs as
    the window last read them, are all named.
    """
    if not overlay:
        return f"Editing {profile_label(active, name)}"
    if not auto:
        return f"Editing {overlay}"
    if program:
        return f"Editing {overlay}, loaded for {program}"
    if not processes:
        return f"Editing {overlay}, loaded for its program"
    return f"Editing {overlay}, loaded for {word_list(processes)}"


def battery_icon(battery: dict | None) -> tuple[str, str | None]:
    """battery.icon_name's icon for a reading, and its style class: `error` for a
    fault, and unless the reading is charging or full, `warning` at LOW_PERCENT and
    `error` at CRITICAL_PERCENT, both for the whole percent battery_text shows."""
    reading = battery or {}
    name, state, percent = icon_name(reading), reading.get("state"), reading.get("percent")
    if state == FAULT:
        return name, "error"
    if state in (FULL, CHARGING, NEARLY_FULL) or not isinstance(percent, int | float):
        return name, None
    # The whole percent battery_text shows (`:.0f` rounds as round() does): the
    # keyboard's readings come in tenths, and a row reading "15%" is red at 15.3 too.
    shown = round(percent)
    return name, ("error" if shown <= CRITICAL_PERCENT
                  else "warning" if shown <= LOW_PERCENT else None)


def set_battery(icon: Gtk.Image, battery: dict | None) -> None:
    """Put `battery_icon`'s icon and style on a battery row's image."""
    name, style = battery_icon(battery)
    icon.set_from_icon_name(name)
    for other in {"warning", "error"} - {style}:
        icon.remove_css_class(other)
    if style:
        icon.add_css_class(style)


def show_battery(row: Adw.ActionRow, icon: Gtk.Image, battery: dict | None,
                 at: float | None = None) -> None:
    """A Battery row and its icon from one reading: battery_text's words, given `at` as it
    takes it, and set_battery's icon, whose color goes by the whole percent the words show."""
    row.set_subtitle(battery_text(battery, at))
    set_battery(icon, battery)


def status_rows(group: Adw.PreferencesGroup, title: str = "Status"
                ) -> tuple[Adw.ActionRow, Gtk.Image, Adw.ActionRow, Gtk.Image]:
    """A device tab's state row with its dot (set_dot) and its Battery row with its icon
    (show_battery), added to `group` and returned in that order. Not markup: the subtitles
    are the daemon's words and a node path. The Battery row starts without a subtitle or an
    icon, since each tab fills them in the call that shows its editor (DeviceView.found)."""
    state_row = Adw.ActionRow(title=title, use_markup=False)
    dot = status_dot()
    state_row.add_prefix(dot)
    group.add(state_row)
    battery_row = Adw.ActionRow(title="Battery", use_markup=False)
    # Presentation: the row's subtitle says the reading in words.
    icon = Gtk.Image(valign=Gtk.Align.CENTER, accessible_role=Gtk.AccessibleRole.PRESENTATION)
    battery_row.add_prefix(icon)
    group.add(battery_row)
    return state_row, dot, battery_row, icon


#: The Keyboard and Mouse tabs' row for a model Gearbench has not yet run on
#: (report.untested): its title and subtitle, its button's label and tooltip, and the toast
#: when the browser does not open, with the form's address behind Details.
UNTESTED_TITLE = "Not yet checked on this model"
UNTESTED_SUBTITLE = "Run gearbench-ctl report in a terminal and paste its block into the form"
REPORT_BUTTON = "Device report"
REPORT_BUTTON_HELP = "Opens the Device report form in your browser"
NO_BROWSER = "The browser did not open"


def untested_row(group: Adw.PreferencesGroup, toast: Callable[..., object]) -> Adw.ActionRow:
    """The row, hidden until show_untested shows it, added to `group`, with its Device report
    button; `toast` says why the browser did not open."""
    row = Adw.ActionRow(title=UNTESTED_TITLE, subtitle=UNTESTED_SUBTITLE, use_markup=False,
                        visible=False)
    button = Gtk.Button(label=REPORT_BUTTON, valign=Gtk.Align.CENTER,
                        tooltip_text=REPORT_BUTTON_HELP)
    button.connect("clicked", lambda clicked: open_report_form(clicked, toast))
    row.add_suffix(button)
    group.add(row)
    return row


def show_untested(row: Adw.ActionRow, untested: object) -> None:
    """Show the row where a reply's `untested` is true, and hide it for any other model."""
    row.set_visible(untested is True)


def open_report_form(widget: Gtk.Widget, toast: Callable[..., object]) -> None:
    """Open REPORT_FORM in the browser, over the window `widget` is on. A failure is toasted
    with the address; a launch the person dismissed is not."""
    def launched(launcher: Gtk.UriLauncher, result: Gio.AsyncResult) -> None:
        try:
            launcher.launch_finish(result)
        except GLib.Error as exc:
            # As Gtk.FileDialog's: a portal's chooser closed unpicked is no failure.
            if not exc.matches(Gtk.dialog_error_quark(), Gtk.DialogError.DISMISSED):
                toast(NO_BROWSER, full=f"{NO_BROWSER}: {exc.message}. The Device report "
                                       f"form is at {REPORT_FORM}")

    # A tab's widget is on the window, its root.
    Gtk.UriLauncher(uri=REPORT_FORM).launch(cast("Gtk.Window", widget.get_root()), None,
                                            launched)


#: A status row's route words.
ON_CABLE = "On its cable"
ON_RECEIVER = "Through its LIGHTSPEED receiver"


def route_text(product: str | None, cabled: frozenset[int]) -> str | None:
    """Which way a device is connected, from its reply's product id; None without one."""
    try:
        pid = int(str(product), 16)
    except ValueError:
        return None
    return ON_CABLE if pid in cabled else ON_RECEIVER


def battery_text(battery: dict | None, at: float | None = None) -> str:
    """A battery reading in the device tabs' words, a dash for none.

    "about" marks a coarse reading (the G305's levels), as the CLI says it.
    `at`, in seconds since the epoch, is when a device that no longer answers
    was last read.
    """
    if not battery:
        return "—"
    words = reading_words(battery) or "—"
    if at is not None:
        words += f", read at {clock_shown(at)}"
    return words


def readable_on(color: int) -> str:
    """Black or white label text, whichever is legible on `color`."""
    r, g, b = (color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF
    # Rec. 709 luma; a mid gray needs white text but a yellow needs black.
    return "#000000" if (0.2126 * r + 0.7152 * g + 0.0722 * b) > 140 else "#ffffff"


#: The prefixes of swatch_class's and key_class's names, by which a widget's old color
#: class is found.
SWATCH_PREFIX, KEY_PREFIX = "sw-", "kb-"


def swatch_class(color: int) -> str:
    """CSS class that fills a swatch with `color`."""
    return f"{SWATCH_PREFIX}{color:06x}"


def key_class(color: int) -> str:
    """CSS class that fills a key button with `color` and picks its text."""
    return f"{KEY_PREFIX}{color:06x}"


def take_color_class(widget: Gtk.Widget, prefix: str, wanted: str) -> None:
    """Give `widget` the class `wanted` and drop its others that start with `prefix`.
    A class change restyles the widget (ColorPicker._refresh_css says why)."""
    for name in widget.get_css_classes():
        if name.startswith(prefix) and name != wanted:
            widget.remove_css_class(name)
    widget.add_css_class(wanted)


def color_rules(colors: Iterable[int]) -> str:
    # Sorted: the Keyboard tab passes a set, whose order follows how it was built,
    # and Styler.update skips a sheet only when its text is the same.
    return "\n".join(
        f".{swatch_class(c)} {{ background-color: {format_color(c)}; }}"
        f".{key_class(c)} {{ background-image: none;"
        f" background-color: {format_color(c)}; color: {readable_on(c)}; }}"
        for c in sorted(colors))


def stylesheet(unit: float, colors: Iterable[int]) -> str:
    """The Keyboard tab's sheet: the key rule at this key size, then the colors."""
    return KEY_TEMPLATE.format(font=max(6, int(unit * 0.30))) + color_rules(colors)


class Styler:
    """A CSS provider on the display holding one sheet while its owner is open."""

    def __init__(self) -> None:
        self.provider = Gtk.CssProvider()
        #: The sheet loaded now, so update skips an unchanged one.
        self._sheet = ""
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, self.provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def update(self, sheet: str) -> bool:
        """Load `sheet` and answer True, unless it is the one already loaded: a load
        restyles every widget in the window, and the owners often repeat a sheet."""
        if sheet == self._sheet:
            return False
        self._sheet = sheet
        self.provider.load_from_string(sheet)
        return True

    def destroy(self) -> None:
        """Take the sheet off the display. It is held there, not by its owner."""
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.remove_provider_for_display(display, self.provider)
