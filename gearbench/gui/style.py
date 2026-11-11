"""The window's shared CSS and widget helpers.

Each color in play gets two rules named after it, a swatch fill and a key
fill: that is how a Gtk.Button shows an arbitrary color without a custom
widget. A picker owns its hue and swatch rules, so two pickers on screen do
not fight over one rule.
"""

from __future__ import annotations

import weakref
from collections.abc import Callable, Collection, Iterable, Iterator, Sequence, Sized
from pathlib import Path
from typing import Literal, NamedTuple, cast

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

from ..astro import name_refusal as name_problem
from ..battery import (
    FAULT,
    below,
    icon_name,
    notice_levels,
    on_power,
    reading_words,
)
from ..config import (
    AT_10_AND_5,
    AT_30_AND_15,
    BATTERY_PRESETS,
    NO_NOTICES,
    URGENT,
    format_color,
    profile_label,
)
from ..eq_presets import PRESETS_FULL, PRESETS_MAX
from ..g432 import SIDETONE_MEANS
from ..lighting import OFF
from ..mouse import COLORLESS_EFFECTS
from ..report import REPORT_FORM
from ..settings_files import shown_path
from ..tray import ASLEEP_ICON, NOT_AVAILABLE_ICON
from ..wording import (
    NOT_ANSWERING_WORDS,
    NOT_READ as UNREAD,
    READ_AT,
    SERVICE_JOURNAL,
    capitalized,
    clock_shown,
    numbered,
    printable,
    reason,
    route,
    route_of,
    turns_replaced,
    word_list,
)
from .worker import Call, LoadingGuard, take_typed

#: The window's icons that the freedesktop Icon Naming Specification does not name, copied
#: from Adwaita with its license (COPYING, beside them), as a hicolor tree of the window's
#: own: a theme other than Adwaita may lack them, as KDE's Breeze lacks the battery's and
#: the Macros tab's, which drew as missing images there. Its tray/ holds the tray's own
#: signs (tray.TRAY_ICONS), which the window does not name. A name the specification lists
#: is not copied: a theme that follows it has the name, or its colored drawing, which
#: GThemedIcon looks for after the symbolic one (edit-undo after edit-undo-symbolic, GLib
#: 2.88, measured 2026-10-28). The folder ships inside the package, so none of its generic
#: names lands in the system's hicolor, where another package's icon of that name would
#: clash with it.
ICONS = Path(__file__).resolve().parent / "icons"


def add_icons(theme: Gtk.IconTheme) -> None:
    """Let `theme` find the window's own icons. GTK looks a name up in hicolor after the
    theme in force and those it inherits, so a theme's own drawing of a name comes first and
    these stand in where it has none."""
    theme.add_search_path(str(ICONS))


#: The fixed rules, which the window loads once for every tab: the swatches, the
#: Mouse tab's stage dots, the pickers' square, strip and markers, and the board's
#: dimmed and mappable keys, and its keys over an animation's colors, white letters
#: with a black edge, legible on any of them. The picker's square and strip copy the
#: focus ring libadwaita draws on GTK's color plane: libadwaita has none for a
#: Gtk.DrawingArea.
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
.g915-animated { background-image: none; background-color: transparent; color: #ffffff;
  text-shadow: 0 0 2px #000000, 0 0 1px #000000; }
.g915-animated:hover { background-color: alpha(#ffffff, 0.2); }
"""
#: A key's corner radius on the drawn keyboard, in pixels: the key rule's, and that of
#: the colors an animation draws under the keys, which must fit inside them.
KEY_RADIUS = 4
#: The board's key rule, with placeholders for the font size, which scales with
#: the drawn keyboard, and the corner radius.
KEY_TEMPLATE = """
.g915-key {{ font-size: {font}px;
  padding: 0; min-height: 0; min-width: 0; border-radius: {radius}px; }}
"""

#: A device indicator's third state: the device or its receiver is attached
#: and the device does not answer.
ASLEEP = "asleep"
#: What each tab's status row says in that state.
MOUSE_ASLEEP = "Asleep or switched off; move it or switch it on"
KEYBOARD_ASLEEP = "Asleep or switched off; press a key or switch it on"
#: A keyboard on its cable in that state: switched off, it leaves its cable's node, so it is
#: not asleep or off.
KEYBOARD_SILENT_ON_CABLE = f"Not answering; `{SERVICE_JOURNAL}` says why"
#: A headset's next step off its link, with what it links to: its receiver or base station.
HEADSET_NEAR = "switch it on, or bring it near its {}"
HEADSET_ASLEEP = "Asleep or switched off; " + HEADSET_NEAR.format("receiver")
#: The Keyboard and Mouse tabs' banner while the device seen once is away or not answering,
#: with its model or its noun: a row whose setting lives on the device is off then, and a row
#: Gearbench keeps stays live, so a change is saved and shows when the device is back.
AWAY_BANNER = "Changes are saved and will show when the {} is back"
#: The green dot's name, and the Mouse tab's and a headset page's reason for a refusal that
#: gives none; and a row's subtitle where the daemon gave no value.
NOT_FOUND, CONNECTED = "Not found", "Connected"
NOT_READ = UNREAD.capitalize()
#: A Battery row with no device there.
NO_DEVICE = "—"
#: A device group's row of the firmware versions the device reported.
FIRMWARE = "Firmware"
#: A switcher's line under the name of a device the daemon marks as not answering.
SWITCHED_OFF = "Switched off"
#: The tabs' nouns: each tab's title, and its device group's until a reply names the model.
KEYBOARD_TAB, MOUSE_TAB, HEADSET_TAB = "Keyboard", "Mouse", "Headset"
#: A headset page's Status row before its first reply, and its status page's title.
LOOKING = "Looking for the headset…"
#: A headset page's sidetone row title, and its slider's name for a screen reader.
SIDETONE = f"Sidetone: {SIDETONE_MEANS}"
#: The group of the levels a base station sends out of its stream port, on each page that
#: has one, and its line, which says the mix is not what the headset plays.
STREAM_MIX = "Stream mix"
STREAM_HELP = "What the base station's stream port sends"
#: The Game and Voice mix: on the A50s a reading, which the headset's own buttons or dial
#: set; on another headset the Outputs group's slider.
BALANCE = "Balance"
#: A unit's switch row whose state did not read, with why.
COULD_NOT_READ = "Could not be read ({})"

#: Each device indicator state's icon, style class and label. A shape and words,
#: so the state reaches a screen reader and anyone who cannot tell the colors
#: apart; libadwaita's classes, so the color follows the theme. Not
#: emblem-ok-symbolic: Adwaita's icon theme (50) lacks it. False's label is the
#: status pages' "not available" and blames no device: a tab also sets False
#: while the daemon does not answer, when the device may be plugged in and fine.
#: ASLEEP's gives no cause: the row's line does, and a keyboard on its cable is
#: neither asleep nor off (KEYBOARD_SILENT_ON_CABLE).
DOT_STATES = {True: ("object-select-symbolic", "success", CONNECTED),
              False: (NOT_AVAILABLE_ICON, "error", "Not available"),
              ASLEEP: (ASLEEP_ICON, "warning", capitalized(NOT_ANSWERING_WORDS))}


def status_dot() -> Gtk.Image:
    """The indicator beside a device's status. `set_dot(dot, state)` sets it."""
    dot = Gtk.Image(valign=Gtk.Align.CENTER)
    set_dot(dot, False)
    return dot


def set_dot(dot: Gtk.Image, state: bool | str) -> None:
    """Show a device as found, not available or not answering (ASLEEP), in place of a
    spinner (spin_dot)."""
    icon, style, label = DOT_STATES[state]
    dot.set_from_icon_name(icon)
    for other in {s for _i, s, _l in DOT_STATES.values()} - {style}:
        dot.remove_css_class(other)
    dot.add_css_class(style)
    dot.update_property([Gtk.AccessibleProperty.LABEL], [label])


#: A unit's state while it only starts, which put_dot draws as a spinner: the warning sign
#: is for a state that needs a look.
STARTING = "starting"


def spin_dot(dot: Gtk.Image, label: str) -> None:
    """Draw a dot as a spinner for a state still being found, named `label` for a screen
    reader."""
    # A new spinner starts from its first frame: a read of a state still being found keeps the
    # one that turns. set_dot's icon drops it, so the next one is new.
    if not isinstance(dot.get_paintable(), Adw.SpinnerPaintable):
        dot.set_from_paintable(Adw.SpinnerPaintable(widget=dot))
    for _icon, style, _label in DOT_STATES.values():
        dot.remove_css_class(style)
    dot.update_property([Gtk.AccessibleProperty.LABEL], [label])


def put_dot(row: Adw.ActionRow, dot: Gtk.Image, state: bool | str | None, label: str) -> None:
    """Set a switch row's dot, a spinner for STARTING, `label` its name for a screen reader,
    or for None take it out of the row: hidden, it still indents the title by the row's
    spacing."""
    attached = dot.get_parent() is not None
    if state is None:
        if attached:
            row.remove(dot)
        return
    if state == STARTING:
        spin_dot(dot, label)
    else:
        set_dot(dot, state)
    # set_dot's words are a device's: "Not available" for a unit that failed.
    dot.update_property([Gtk.AccessibleProperty.LABEL], [displayable(label)])
    if not attached:
        row.add_prefix(dot)


def displayable(text: str) -> str:
    """`text` with U+FFFD for each lone surrogate, on which PyGObject raises
    UnicodeEncodeError, for each NUL, where PyGObject's C copy of the text ends, and for each
    direction turn and line or paragraph separator (wording.turns_replaced), which a saved
    name can hold (config.refuse_controls) and which would reorder or break the sentence it
    is in. surrogateescape file names and json.loads of an escape such as "\\udce9" make the
    first; a device's firmware name (hidpp.firmware_entity) and json.loads of "\\u0000"
    the second."""
    return turns_replaced("".join("\ufffd" if c == "\0" or "\ud800" <= c <= "\udfff" else c
                                  for c in text))


def say_in_place(listbox: Gtk.ListBox, title: str, detail: str = "",
                 waiting: bool = False) -> None:
    """Empty `listbox` and say why in its placeholder (in_place)."""
    listbox.remove_all()
    listbox.set_placeholder(in_place(title, detail, waiting))


def in_place(title: str, detail: str = "", waiting: bool = False) -> Gtk.Widget:
    """A list's placeholder, which it shows while no row is shown: `title` dimmed, `detail`
    under it, and a spinner while the list is read. A state drawn as a row reads as an item
    of that name. Plain text, lone surrogates as U+FFFD: `detail` can be an error naming a
    path."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=12,
                  margin_bottom=12, margin_start=12, margin_end=12)
    if waiting:
        box.append(Adw.Spinner())
    box.append(Gtk.Label(label=displayable(title), wrap=True, css_classes=["dimmed"]))
    if detail:
        box.append(Gtk.Label(label=displayable(detail), wrap=True,
                             css_classes=["dimmed", "caption"]))
    return box


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


def take_typing_on_clicks(window: Gtk.Window) -> None:
    """Have a press anywhere in `window` outside the spin row with the cursor take the
    number typed in it (worker.take_typed), as the focus leaving the row does: a click on a
    switch, a scrollbar or a grayed-out row moves no focus, and a click out of the row takes
    it (the owner's decision of 2026-10-23). One gesture on the window, so the header bar
    counts too, in the capture phase, so it sees each press before the widget under it,
    which may claim the press at once; it claims none, so the click does what it did."""
    gesture = Gtk.GestureClick(button=0, propagation_phase=Gtk.PropagationPhase.CAPTURE)
    gesture.connect("pressed", lambda _g, _n, x, y: _take_typing_outside(window, x, y))
    window.add_controller(gesture)


def _take_typing_outside(window: Gtk.Window, x: float, y: float) -> None:
    """A press at (x, y) in `window`: the number typed in the spin row with the cursor is
    taken unless the press is on that row. Insensitive widgets count, as in blank_at, so a
    press on the row's grayed-out + is on the row."""
    focus = window.get_focus()
    row = None if focus is None else focus.get_ancestor(Adw.SpinRow)
    if row is not None and not holds_focus(window.pick(x, y, Gtk.PickFlags.INSENSITIVE), row):
        take_typed(row)


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


def name_refusal(name: str) -> str | None:
    """Why a Name row cannot take `name`, already stripped, in astro.name_refusal's words,
    the daemon's and the command line's, with a capital; or None."""
    refused = name_problem(name)
    return None if refused is None else capitalized(refused)


#: libadwaita's style class that draws an entry red: an entry's while the window would refuse
#: its text (watch_refused).
REFUSED_STYLE = "error"


def mark_refused(entry: Gtk.Entry | Adw.EntryRow, refused: Callable[[str], bool]) -> None:
    """Give `entry` REFUSED_STYLE while `refused` holds for its text, stripped, and take it
    off once it does not."""
    if refused(entry.get_text().strip()):
        entry.add_css_class(REFUSED_STYLE)
    else:
        entry.remove_css_class(REFUSED_STYLE)


def watch_refused(entry: Gtk.Entry | Adw.EntryRow, refused: Callable[[str], bool]) -> None:
    """Mark `entry` (mark_refused) now and at each change of its text: every entry whose text
    the window checks before it sends, beside the toast or the words that say why. Its text
    stays after a refusal, to be fixed."""
    mark_refused(entry, refused)
    entry.connect("changed", mark_refused, refused)


def blank(text: str) -> bool:
    """True for no text: the check watch_refused takes for a name entry that refuses a blank
    name."""
    return not text


def watch_name(entry: Gtk.Entry | Adw.EntryRow) -> None:
    """watch_refused for a preset's name on its Name rows."""
    watch_refused(entry, lambda name: name_refusal(name) is not None)


#: An equalizer's Preset row on the headset pages, and its last choice and what the row says
#: while the equalizer plays none of its presets.
PRESET, CUSTOM = "Preset", "Custom"
#: The name dialog that saves the sound a page's equalizer has as a new preset, the rows
#: that open it, its Save, and its entry's name for a screen reader.
SAVE_HEADING = "Save as new preset"
SAVE_NEW_PRESET = f"{SAVE_HEADING}…"
SAVE_BUTTON = "Save"
PRESET_NAME = "Preset name"


def answer_on_enter(entry: Gtk.Entry, key: str) -> None:
    """Enter in a dialog's name entry answers `key` once, while that response is enabled. The
    dialog is found as the entry's ancestor, not held: a handler that held it would make a
    cycle through GTK that Python's collector cannot see. The response is turned off as it is
    given and the entry takes no more typing, which would turn it back on: over an Adw window
    the entry keeps the cursor while the dialog slides away, and a second Enter then answered
    again (libadwaita 1.9.1, measured 2026-10-31)."""
    found = cast("Adw.AlertDialog", entry.get_ancestor(Adw.AlertDialog))
    if found.get_response_enabled(key):
        found.set_response_enabled(key, False)
        entry.set_editable(False)
        found.emit("response", key)
        found.force_close()


def ask_name(parent: Gtk.Widget, name: str, then: Callable[[str], None],
             heading: str, body: str = "", response: str = SAVE_BUTTON, label: str = PRESET_NAME,
             refusal: Callable[[str], str | None] = name_refusal) -> Adw.AlertDialog:
    """Ask for a name, a preset's unless `refusal` and `label`, the entry's name for a screen
    reader, say otherwise, `name` to start, under `heading`; `then` takes the name on
    `response`, which waits for one `refusal` takes. While it refuses one, its sentence is
    the body in place of `body`, as the Name rows toast it: a refusal said in words, and
    the entry is red (watch_refused), as the Name rows are."""
    dialog = Adw.AlertDialog(heading=displayable(heading),
                             body=displayable(refusal(name.strip()) or body))
    key = response.lower()
    entry = Gtk.Entry(text=name)
    entry.update_property([Gtk.AccessibleProperty.LABEL], [label])
    watch_refused(entry, lambda text: refusal(text) is not None)
    dialog.set_extra_child(entry)
    respond(dialog, key, response, Adw.ResponseAppearance.SUGGESTED)
    dialog.set_response_enabled(key, refusal(entry.get_text().strip()) is None)

    # The entry's handlers find the dialog as its ancestor, which it is from here on:
    # holding it would make a cycle through GTK that Python's collector cannot see.
    def allowed(entry: Gtk.Entry) -> None:
        found = cast("Adw.AlertDialog", entry.get_ancestor(Adw.AlertDialog))
        refused = refusal(entry.get_text().strip())
        found.set_response_enabled(key, refused is None)
        found.set_body(displayable(refused or body))

    entry.connect("changed", allowed)
    # Not through `activates-default`: the entry's default action is the window's, not the
    # dialog's, so it did nothing, measured on libadwaita 1.9.
    entry.connect("activate", answer_on_enter, key)
    dialog.connect("response", lambda _d, answer: then(entry.get_text().strip())
                   if answer == key else None)
    dialog.present(parent)
    return dialog


def presets_full(presets: Sized | None) -> str | None:
    """Why a preset file, `presets` as last read, takes no new preset once it holds
    PRESETS_MAX: PRESETS_FULL, capitalized, for the line of the row that adds one, or of
    its group where that row is a button row with no line of its own. None while it has
    room, or for None, a file not read."""
    full = presets is not None and len(presets) >= PRESETS_MAX
    return capitalized(PRESETS_FULL) if full else None


def offer_save_new(row: Gtk.Widget, usable: bool, presets: Sized | None) -> str | None:
    """A row that adds a preset to a preset file, Save as new or the A50 dialog's Add row:
    sensitive while `usable`, unless `presets`, the file's as last read, leave no room for one
    more. Returns presets_full's line, which the caller shows; None for `presets`, a file
    not read, leaves the row to `usable`."""
    line = presets_full(presets)
    row.set_sensitive(usable and line is None)
    return line


def save_new_row(tooltip: str, activated: Callable[..., object],
                 *data: object) -> Adw.ButtonRow:
    """A page's Save as new preset… row, `activated` with `data` at a click. The page asks
    the name in a dialog (ask_name): an entry row looked like the Name row, which renames."""
    row = Adw.ButtonRow(title=SAVE_NEW_PRESET, tooltip_text=tooltip,
                        start_icon_name="document-save-as-symbolic")
    row.connect("activated", activated, *data)
    return row


def opener_row(title: str, subtitle: str, opener: str, clicked: Callable[..., object],
               *data: object) -> tuple[Adw.ActionRow, Gtk.Button]:
    """A row of `title` and its line `subtitle`, such as a page's Delete preset row, and its
    plain button `opener`, such as Delete…, `clicked` with `data` at a click: the red is on
    the question's own response, which does it. The button is named for a screen reader by
    its row's job, `title` (name_button)."""
    row = plain_row(Adw.ActionRow, title, subtitle)
    button = Gtk.Button(label=opener, valign=Gtk.Align.CENTER)
    name_button(button, title)
    button.connect("clicked", clicked, *data)
    row.add_suffix(button)
    return row, button


def widgets_under(parent: Gtk.Widget) -> Iterator[Gtk.Widget]:
    """Each widget under `parent`, depth first in the order GTK holds them, the private
    children a libadwaita widget hands out no getter for included."""
    stack = [parent.get_first_child()]
    while stack:
        widget = stack.pop()
        if widget is None:
            continue
        yield widget
        stack += [widget.get_next_sibling(), widget.get_first_child()]


def first_under[W: Gtk.Widget](parent: Gtk.Widget, kind: type[W]) -> W:
    """The first `kind` under `parent` (widgets_under): an Adw.ComboRow has its popover and
    the list in it from the start, and hands out neither. LookupError where none is."""
    found = next((widget for widget in widgets_under(parent) if isinstance(widget, kind)), None)
    if found is None:
        raise LookupError(f"no {kind.__name__} under {type(parent).__name__}")
    return found


def name_button(button: Gtk.Widget, name: str) -> None:
    """Name `button` `name` for a screen reader. A button made with a label is named by it,
    through a relation GTK takes before LABEL, so the relation goes; set_label keeps the
    label child it finds, and adds no relation back. Libadwaita names a button row, and an
    entry row's text, by its title through the same relation."""
    button.reset_relation(Gtk.AccessibleRelation.LABELLED_BY)
    button.update_property([Gtk.AccessibleProperty.LABEL], [name])


def preset_label(number: int, name: str | None) -> str:
    """An EQ preset as a dropdown lists it: its number and its name, or, without a name,
    its slot: a library preset always has a name, a base station's slot can read none."""
    return numbered(number, name) if name else f"Slot {number}"


def show_choice(loading: LoadingGuard, row: Adw.ComboRow, labels: Sequence[str],
                index: int | None) -> None:
    """A headset setting's dropdown on entry `index` of `labels`, its own values, or for None,
    not read, on one more entry after them, NOT_READ, as the G432's Auto shut-off is while no
    time is read, with no subtitle to say it again. A row gets the same `labels` at each
    call, so the count of its entries tells whether NOT_READ is among them."""
    shown = [*labels] if index is not None else [*labels, NOT_READ]
    # Each such row is built on its list.
    if cast("Gio.ListModel", row.get_model()).get_n_items() != len(shown):
        loading.put_model(row, Gtk.StringList.new(shown))
    loading.put_selected(row, len(labels) if index is None else index)


#: Why a page or Load preset to headset shows no preset of its library: the file could not
#: be read; also the toast line of a library read that failed, on a headset page or in Load
#: preset to headset, when the failure is too long for a toast, the whole failure behind
#: Details.
LIBRARY_UNREAD = "The library could not be read"
#: The toast line of a Save as new that failed, on every headset page, when the failure is
#: too long for a toast.
NOT_SAVED_NEW = "The new preset was not saved"
#: The Delete preset row of the headset pages. The toast lines of a rename and a delete that
#: failed, when the failure is too long for a toast, are Load preset to headset's as well.
DELETE_PRESET = "Delete preset"
NOT_RENAMED = "The name did not change"
NOT_DELETED = "The preset was not deleted"

#: The title of the chooser the A50's Import row opens (astroeq_dialog), and the row's.
ASTROEQ_TITLE = "Import from Astro Command Center"
ASTROEQ_IMPORT = f"{ASTROEQ_TITLE}…"
#: The A50's Import row's refusal of the file, with its name and why, and the toast's line
#: when it is too long for one.
ASTROEQ_REFUSED = "{} could not be imported: {}"
ASTROEQ_NOT_IMPORTED = "The file was not imported"
#: The Reset to default rows' title: the A50's, a button row that writes at once, and the
#: G432's, whose button opens a question (g432_eq_card.RESET_OPENER).
PRESET_RESET = "Reset to default"
#: The row that writes back what the A50 Gen 4 keeps in its flash.
REVERT_TO_SAVED = "Revert to saved"


def astroeq_dialog(title: str) -> Gtk.FileDialog:
    """The file dialog the A50's Import row and Load preset to headset's Add row open,
    titled in its opener's words: Command Center's presets, *.astroeq in any case."""
    dialog = Gtk.FileDialog(title=title, modal=True)
    filters = Gio.ListStore.new(Gtk.FileFilter)
    presets = Gtk.FileFilter(name="Command Center presets")
    # A suffix matches in any case, a pattern only in its own on Linux, and Command Center
    # writes its files on Windows, where Night.ASTROEQ is Night.astroeq.
    presets.add_suffix("astroeq")
    filters.append(presets)
    dialog.set_filters(filters)
    return dialog


#: Every Headset tab slider's width, fixed so the ends line up down the tab.
SLIDER_WIDTH = 280


#: The Effect row's words for an effect named for something with a name of its own, whose
#: case the row's sentence case would lose: G HUB's names for the Falcon's two. And Off for
#: disabled, which says what the lights do, on every Effect row; the CLI keeps disabled (the
#: owner's decision of 2026-10-27).
EFFECT_LABELS = {"millennium-falcon": "Millennium Falcon",
                 "millennium-falcon-inactive": "Millennium Falcon, inactive",
                 "disabled": "Off"}


#: What each effect a mouse or a headset lists does, the tooltip of its Effect row's entry,
#: as the Keyboard tab's Mode list has one for each (lighting_card.MODE_TIPS), in the words
#: of a device's lights; EFFECT_TIP for a name of the device's own.
EFFECT_TIPS = {"static": "One color on every light.",
               "breathe": "The lights fade in and out in one color.",
               "cycle": "The lights run through every color together.",
               "wave": "Colors roll across the lights.",
               "disabled": "The lights are off."}
EFFECT_TIP = "One of the device's own effects, under the device's name for it."


def effect_tips(names: list[str]) -> list[str]:
    """The Effect row's tooltips for the effects `names`, in their order."""
    return [EFFECT_TIPS.get(name, EFFECT_TIP) for name in names]


def effect_label(name: str) -> str:
    """An effect's name as the Keyboard tab's Effect row and the Mouse tab's and the headsets'
    Effect rows show it: "neural-wave" is "Neural wave"."""
    return EFFECT_LABELS.get(name) or name[:1].upper() + name[1:].replace("-", " ")


#: The steps from which Page Up moves a slider by 10, as on the G432's 0-to-100 sidetone,
#: so a 0-to-100 slider pages alike on every page; a shorter one pages by 1.
PAGE_BY_TEN = 100


def page_of(low: float, high: float) -> int:
    """How far Page Up and Page Down move a slider from `low` to `high`."""
    return 10 if high - low >= PAGE_BY_TEN else 1


def percent(level: int) -> str:
    """A slider's level in words, as its row's subtitle shows it and a screen reader says
    it (spoken_scale): "40%"."""
    return f"{level}%"


def spoken_scale(low: float, high: float, label: str, page: float = 10, origin: bool = True,
                 spoken: Callable[[int], str] = percent) -> Gtk.Scale:
    """A Headset tab slider, named `label` for a screen reader; `origin` False for a
    balance or a gain, where a fill from the left end would read as a level. `spoken` is
    its value for a screen reader: the words its row shows, as a screen reader should say
    them; a percent by default."""
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


def between(scale: Gtk.Scale, start: str, end: str) -> Gtk.Box:
    """A balance slider with its two sides, `start` and `end`, dimmed under its ends: beside
    it, a word would push it out of line with the other sliders."""
    words = Gtk.CenterBox(
        start_widget=Gtk.Label(label=start, css_classes=["dimmed", "caption"]),
        end_widget=Gtk.Label(label=end, css_classes=["dimmed", "caption"]))
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
    box.append(scale)
    box.append(words)
    return box


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


def watch_pointer(scale: Gtk.Scale, press: Callable[[], None],
                  release: Callable[[], None]) -> None:
    """Run `press` as the pointer goes down on a slider and `release` as it lets go
    (pointer_changed), in the capture phase, so it sees each press before the slider does."""
    pointer = Gtk.EventControllerLegacy(propagation_phase=Gtk.PropagationPhase.CAPTURE)
    pointer.connect("event", pointer_changed, press, release)
    scale.add_controller(pointer)


def pointer_changed(controller: Gtk.EventControllerLegacy, _event: object,
                    press: Callable[[], None], release: Callable[[], None]) -> bool:
    """watch_pointer's handler: a canceled touch counts as the release, or the slider stays
    held and never sends. False, so the slider gets the event too."""
    if (change := pointer_button(controller)) is not None:
        (press if change.pressed else release)()
    return False


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
        # width, so it goes behind Details. Whoever shared the file named it: a direction
        # turn or a separator in the name would draw the note in another order or on new
        # lines.
        toast(NOT_LOCAL, full=f"Could not open {printable(file.get_parse_name())}: it is not "
                              "on a local disk. Copy it to this computer first.")
        return None
    return Path(path)


#: What a group's spinner is called while a write one of its rows started is in flight, by
#: where the write goes: a mouse's onboard profile 1, a loaded game profile's file, no
#: profile, for a report rate set live alone, or an A50 Gen 4's base station.
WRITING_PROFILE = "Writing to onboard profile 1…"
SAVING_GAME = "Saving to the game profile…"
SETTING_RATE = "Setting the report rate…"
WRITING_BASE = "Writing to the base station…"


def header_spinner(group: Adw.PreferencesGroup) -> Adw.Spinner:
    """A spinner in `group`'s header, hidden until a write one of its rows started is in
    flight. Named for a write to profile 1 until a write names where it goes
    (show_header_spinner)."""
    spinner = Adw.Spinner(valign=Gtk.Align.CENTER, visible=False)
    spinner.update_property([Gtk.AccessibleProperty.LABEL], [WRITING_PROFILE])
    group.set_header_suffix(spinner)
    return spinner


def show_header_spinner(spinner: Adw.Spinner, label: str | None) -> None:
    """A header_spinner shown, and named `label` for a screen reader, while a write is in
    flight; hidden for None."""
    if label is not None:
        spinner.update_property([Gtk.AccessibleProperty.LABEL], [label])
    spinner.set_visible(label is not None)


#: The button that opens a delete's question, its ellipsis for the question (the HIG's
#: writing style), and the question's own response.
DELETE_OPENER = "Delete…"
DELETE_RESPONSE = "Delete"
#: A delete's question, by the name of what it deletes.
DELETE_HEADING = "Delete {}?"


def respond(dialog: Adw.AlertDialog, key: str, label: str,
            appearance: Adw.ResponseAppearance) -> None:
    """Cancel, answered as "cancel", and `label` on `dialog`. Escape answers Cancel, and
    Enter `label` unless it is destructive, when it answers Cancel (the GNOME HIG's
    dialogs). The one home of that rule."""
    dialog.add_response("cancel", "Cancel")
    dialog.add_response(key, label)
    dialog.set_response_appearance(key, appearance)
    destructive = appearance == Adw.ResponseAppearance.DESTRUCTIVE
    dialog.set_default_response("cancel" if destructive else key)
    dialog.set_close_response("cancel")


def offer(dialog: Adw.AlertDialog, key: str, label: str,
          appearance: Adw.ResponseAppearance, then: Callable[[], object]) -> None:
    """Cancel and `label` on `dialog` (respond); `then` runs on `label`."""
    respond(dialog, key, label, appearance)
    dialog.connect("response", lambda _d, r: then() if r == key else None)


def confirm(parent: Gtk.Widget, heading: str, body: str, response: str,
            then: Callable[[], None], destructive: bool = True) -> None:
    """A confirmation over `parent`; `then` runs on `response` (offer). A lone surrogate in
    `body`, as in a folder's name that is not UTF-8, shows as U+FFFD, and a saved name's
    direction turn in `heading` or `body` as well (displayable)."""
    dialog = Adw.AlertDialog(heading=displayable(heading), body=displayable(body))
    offer(dialog, response.lower(), response,
          Adw.ResponseAppearance.DESTRUCTIVE if destructive else Adw.ResponseAppearance.DEFAULT,
          then)
    dialog.present(parent)


#: Width of a confirmation longer than a sentence: an Adw.AlertDialog's own, about 356px,
#: wraps a list into a narrow column.
WIDE_DIALOG = 560


def explain(dialog: Adw.AlertDialog, paragraphs: tuple[str, ...],
            bullets: tuple[str, ...] = ()) -> None:
    """Put the dialog's text, left-aligned, in its extra child, with hanging bullets.

    The built-in body is centered, and centered bullets have no common left edge. A lone
    surrogate, as in a folder's name that is not UTF-8, shows as U+FFFD (displayable).
    """
    # set_content_width has no effect while the dialog follows its content size.
    dialog.set_follows_content_size(False)
    dialog.set_content_width(WIDE_DIALOG)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                  margin_top=4, margin_bottom=4)
    for text in paragraphs:
        box.append(Gtk.Label(label=displayable(text), xalign=0.0, wrap=True,
                             max_width_chars=64))
    for text in bullets:
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        line.append(Gtk.Label(label="•", xalign=0.0, valign=Gtk.Align.START))
        line.append(Gtk.Label(label=displayable(text), xalign=0.0, wrap=True,
                              max_width_chars=60, hexpand=True))
        box.append(line)
    dialog.set_extra_child(box)


#: A device tab's status page title while the daemon does not answer and the
#: device has not been seen, a wait that lasts, as LOOKING's. The reason under it is
#: `unreachable`'s; the window's banner carries Start daemon.
DAEMON_WAIT = "Waiting for the daemon…"


class DeviceView:
    """A device tab's content: an Adw.StatusPage until the device is seen,
    then the editor for as long as the window is open (the owner's decision
    of 2026-09-24).

    The page spins until the first read comes back, answered or failed, then
    says why the device is not there: the daemon's reason, or `unreachable`'s
    while the daemon does not answer. A device seen once keeps its editor when
    it goes; its status row says it is away. The editor scrolls, under the
    tab's top bars. `under`, where given, shows under the status page's words
    until the device is seen, then last in the editor: groups that work without
    the device.
    """

    def __init__(self, editor: Gtk.Box, icon: str, looking: str,
                 under: Gtk.Widget | None = None) -> None:
        #: The device's icon, shown once the spinner stops.
        self.icon = icon
        #: The device has been seen since the window opened.
        self.seen = False
        self._editor, self._under = editor, under
        self.status = Adw.StatusPage(title=looking, child=under)
        # A status page takes a paintable, not a widget; set_icon_name later replaces it.
        self.status.set_paintable(Adw.SpinnerPaintable(widget=self.status))
        self.stack = Gtk.Stack()
        self.stack.add_named(self.status, "status")
        self.stack.add_named(Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, child=editor), "editor")

    def found(self) -> None:
        """The device is there: the editor, from now on, `under` moved to its end."""
        if not self.seen and self._under is not None:
            self.status.set_child(None)
            self._editor.append(self._under)
        self.seen = True
        self.stack.set_visible_child_name("editor")

    def missing(self, title: str, reason: str) -> None:
        """Stop the spinner and say why the device is not there. The description
        is markup, so the reason, which can hold a node or socket path, is escaped, a lone
        surrogate or a NUL in it shown as U+FFFD (displayable)."""
        self.status.set_icon_name(self.icon)
        self.status.set_title(title)
        self.status.set_description(GLib.markup_escape_text(displayable(capitalized(reason))))


def top_switcher(stack: Gtk.Stack) -> Gtk.StackSwitcher:
    """The row of buttons at the top of a tab that picks the device it shows, hidden until
    there are two to pick from."""
    return Gtk.StackSwitcher(stack=stack, halign=Gtk.Align.CENTER, margin_top=12, visible=False)


def switched_off_title(name: str, off: bool) -> str:
    """A top_switcher button's text: `name`, with SWITCHED_OFF under it for a device `off`."""
    return f"{name}\n{SWITCHED_OFF}" if off else name


def gray_switched_off(switcher: Gtk.StackSwitcher, switched_off: Collection[int]) -> None:
    """Each of `switcher`'s buttons whose index is in `switched_off` insensitive, so it shows
    and cannot be picked (the owner's decisions of 2026-10-20 and 2026-10-23), every other
    sensitive, and each label centered, a label of two lines as one line is."""
    # The StackSwitcher's children are its buttons, one for each page in the pages' order, a
    # hidden page's too, each a label of the page's title, and it leaves their sensitivity
    # alone: read off GTK 4.22.4 on 2026-10-20 and 2026-10-27, not promised by its
    # documentation.
    button, index = switcher.get_first_child(), 0
    while button is not None:
        button.set_sensitive(index not in switched_off)
        cast("Gtk.Label", button.get_first_child()).set_justify(Gtk.Justification.CENTER)
        button, index = button.get_next_sibling(), index + 1


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

    def show(self, names: Sequence[str], at: int | None, shown: bool,
             switched_off: Collection[int] = ()) -> None:
        """Offer `names`, with the switcher on the one at `at`, and shown while `shown`; the
        button of each choice whose index is in `switched_off` reads SWITCHED_OFF under its
        name and is insensitive (gray_switched_off). None for `at` leaves it where it is,
        which for new choices is on the first."""
        labels = [switched_off_title(name, index in switched_off)
                  for index, name in enumerate(names)]
        self._showing = True
        try:
            if labels != self.labels:
                while (page := self.stack.get_first_child()) is not None:
                    self.stack.remove(page)
                for index, words in enumerate(labels):
                    self.stack.add_titled(Gtk.Box(), str(index), words)
                self.labels = labels
            if at is not None:
                self.stack.set_visible_child_name(str(at))
        finally:
            self._showing = False
        # Each button's own drop motion controller switches the page under a drag held over it
        # for half a second, which here would be a pick no one clicked, so it goes.
        button = self.widget.get_first_child()
        while button is not None:
            for controller in list(button.observe_controllers()):
                if isinstance(controller, Gtk.DropControllerMotion):
                    button.remove_controller(controller)
            button = button.get_next_sibling()
        gray_switched_off(self.widget, switched_off)
        self.widget.set_visible(shown)

    def _on_pick(self, _stack: Gtk.Stack, _pspec: GObject.ParamSpec) -> None:
        # A page goes only in show, so outside it the stack is on one.
        if not self._showing:
            self._picked(cast("int", self.selected))


#: The size of a dialog that lists a library.
LIST_DIALOG_WIDTH = 560
LIST_DIALOG_HEIGHT = 640


#: The longest text a toast carries whole; about 105 characters of its one line
#: fit in the window. A longer refusal shows refusal_line, a longer note short_note, or
#: refusal_line where it is worded as a refusal (toast_clause).
TOAST_MAX = 80


def refusal_line(refusal: str, fallback: str) -> str | None:
    """A refusal's toast line when it is longer than TOAST_MAX: its first clause, before
    "; ", or `fallback` when that is too long too, or when a double quote is on each side of
    the cut; None when the refusal fits whole. Not cut at ". ": a refusal is a fragment
    (wording.capitalized), so a period in it is inside a name or a path, as in the shipped
    game profile R.E.P.O. A name is in double quotes (wording.quoted) and can hold "; " and
    a double quote of its own, so a cut with a quote on each side can be inside one, and the
    clause would name something else."""
    if len(refusal) <= TOAST_MAX:
        return None
    first, _cut, rest = refusal.partition("; ")
    return fallback if len(first) > TOAST_MAX or ('"' in first and '"' in rest) else first


def short_note(note: str, fallback: str, one_sentence: bool = False) -> str | None:
    """A note's toast line when it is longer than TOAST_MAX: its first sentence with no final
    period, or `fallback` when that is too long too; None when the note fits. `one_sentence` gives `fallback` for a one-sentence note, as a game profile's
    name in it can hold ". " (R.E.P.O.)."""
    if len(note) <= TOAST_MAX:
        return None
    first = note if one_sentence else note.split(". ", 1)[0].rstrip(".")
    line = first if len(first) <= TOAST_MAX else fallback
    return line[:1].upper() + line[1:]


def toast_note(toast: Callable[..., object], note: str, fallback: str,
               one_sentence: bool = False) -> None:
    """Toast `note` whole with no final period, as the window's one-line texts end, or
    short_note's line with the note behind Details, where it keeps its periods."""
    short = short_note(note, fallback, one_sentence)
    if short is None:
        toast(note.removesuffix("."))
    else:
        toast(short, full=note)


def toast_clause(toast: Callable[..., object], note: str, fallback: str) -> None:
    """Toast `note`, a fragment with "; " before a second clause, whole, or refusal_line's
    line with the note behind Details."""
    line = refusal_line(note, fallback)
    if line is None:
        toast(note)
    else:
        toast(line, full=note)


#: The toast's line for the note of a layer change too long for one: why the keyboard does
#: not show what the daemon saved (Keyboard.apply_profile).
NOT_SHOWN_LINE = "The keyboard does not show the change"


def say_not_shown(toast: Callable[..., object], reply: dict | None,
                  line: str = NOT_SHOWN_LINE) -> None:
    """Toast a reply's note, if it has one: why the keyboard does not show the layer or did not
    get the idle times the daemon saved, or that its lights are down; `line` where it is too
    long. The device's words (the reply's `detail`), where the note does not quote them, go
    under the note behind Details, headed by `line`, as the command line prints them on a
    detail line."""
    if reply is None or not reply.get("note"):
        return
    note = capitalized(reply["note"])
    detail = str(reply.get("detail") or "")
    if not detail or detail in reply["note"]:
        toast_note(toast, note, line)
    else:
        toast(short_note(note, line) or note.removesuffix("."), detail, full=note, heading=line)


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
    """Which layer the window edits, for the Editing rows on the Keyboard and Mouse tabs and
    in the Headset tab's G keys group.

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


def battery_icon(battery: dict | None, cabled: bool = False,
                 preset: object = None) -> tuple[str, str | None]:
    """battery.icon_name's icon for a reading, `cabled` passed on, and its style class:
    `error` for a fault, and unless the reading is full or on external power (on_power),
    `warning` below the first level of the device's `preset` (battery.notice_levels) and
    `error` below its second, both for the whole percent battery_text shows, as the notices
    and the tray compare it."""
    low, critical = notice_levels(preset)
    reading = battery or {}
    name = icon_name(reading, cabled)
    if reading.get("state") == FAULT:
        return name, "error"
    value = reading.get("percent")
    percent = value if isinstance(value, int | float) else None
    state, powered = reading.get("state"), on_power(reading)
    if below(percent, state, powered, critical):
        return name, "error"
    return name, "warning" if below(percent, state, powered, low) else None


def show_battery(row: Adw.ActionRow, icon: Gtk.Image, battery: dict | None,
                 at: float | None = None, cabled: bool = False, gone: bool = False,
                 preset: object = None) -> None:
    """A Battery row and its icon from one reading: battery_text's words, given `at`,
    `cabled` and `gone` as it takes them, and battery_icon's icon and style, whose color goes
    by the whole percent the words show against the levels of the device's `preset`, its
    reply's battery_notices. `cabled` says it is a keyboard on its cable."""
    row.set_subtitle(battery_text(battery, at, cabled, gone))
    name, style = battery_icon(battery, cabled, preset)
    icon.set_from_icon_name(name)
    for other in {"warning", "error"} - {style}:
        icon.remove_css_class(other)
    if style:
        icon.add_css_class(style)


#: The Battery notices row's title, each preset's choice by its word (config.BATTERY_PRESETS),
#: its tooltip where the urgent preset is offered (the owner's decision 10 of 2026-10-28), and
#: for a device whose battery the desktop warns of itself (a reply's desktop_warns), by the
#: desktop's name, its line, where urgent is not the preset already and where it is, and its
#: tooltip (the owner's decision of 2026-10-28, "The 'also warns' rule goes by desktop").
BATTERY_NOTICES = "Battery notices"
NOTICE_CHOICES = {AT_30_AND_15: "At 30% and 15%", AT_10_AND_5: "At 10% and 5%",
                  NO_NOTICES: "Off", URGENT: "At 30% and 15%, urgent"}
URGENT_HELP = "Urgent: GNOME shows the 15% notice over full-screen games"
ALSO_WARNS = "{} also warns, so no urgent choice"
ALSO_WARNS_NOT_URGENT = f"{{}} also warns, so {notice_levels(URGENT)[1]}% is not urgent"
ALSO_WARNS_HELP = "An urgent notice would come on top of {}'s own"


class NoticesRow:
    """A device tab's Battery notices row, added to `group`: the device's preset of
    config.json's battery_notices, and a pick sent as `battery_notices`.
    The urgent preset is offered only where no desktop warns of the device's battery itself,
    or where it is the preset already."""

    def __init__(self, group: Adw.PreferencesGroup, call: Call) -> None:
        self.row = Adw.ComboRow(title=BATTERY_NOTICES, use_markup=False, visible=False)
        self.row.connect("notify::selected", self.on_picked)
        group.add(self.row)
        self._call = call
        self._loading = LoadingGuard()
        #: The presets the row lists, in its order; the request's field and value naming the
        #: device, None while none is shown; the preset its reply gave; and the picks out.
        self._presets: list[str] = []
        self._device: dict | None = None
        self._preset: str | None = None
        self._out = 0

    def show(self, device: dict | None, reply: dict | None) -> None:
        """The row for the device `device` names, as {"keyboard": "c33e"}, from its reply's
        battery_notices and desktop_warns; hidden for none, and for a reply with no preset
        this version knows. A pick out holds the row where it is."""
        preset = None if reply is None else reply.get("battery_notices")
        if device is None or preset not in BATTERY_PRESETS:
            self._device = None
            self.row.set_visible(False)
            return
        desktop = cast("dict", reply).get("desktop_warns")
        warns = desktop if isinstance(desktop, str) else None
        presets = [p for p in BATTERY_PRESETS
                   if p != URGENT or warns is None or preset == URGENT]
        if presets != self._presets:
            self._presets = presets
            self._loading.put_model(self.row, Gtk.StringList.new(
                [NOTICE_CHOICES[p] for p in presets]))
        self._device, self._preset = device, cast("str", preset)
        if not self._out:
            self._loading.put_selected(self.row, presets.index(self._preset))
        if warns is None:
            self.row.set_subtitle("")
            self.row.set_tooltip_text(URGENT_HELP)
        else:
            line = ALSO_WARNS_NOT_URGENT if preset == URGENT else ALSO_WARNS
            self.row.set_subtitle(line.format(warns))
            self.row.set_tooltip_text(ALSO_WARNS_HELP.format(warns))
        self.row.set_visible(True)

    def on_picked(self, row: Adw.ComboRow, _pspec: GObject.ParamSpec | None) -> None:
        """Send a preset picked by hand, unless it is the reply's and no pick is out. A
        refusal, which the window says, puts the reply's back once no pick is out."""
        # Each model set is the program's, under the guard: a pick is one of its entries.
        if self._loading.active:
            return
        picked = self._presets[row.get_selected()]
        if picked == self._preset and not self._out:
            return
        self._out += 1

        def done(reply: dict | None) -> None:
            self._out -= 1
            if reply is not None:
                self._preset = picked
            elif not self._out:
                self._loading.put_selected(self.row,
                                           self._presets.index(cast("str", self._preset)))
        # A pick comes from a row shown, which show() gave a device.
        self._call({"command": "battery_notices", "value": picked, **cast("dict", self._device)},
                   done)


#: The device pages' row for a model not yet tested on the hardware
#: (report.untested): its title and subtitle, its button's label and tooltip, and the toast
#: when the browser does not open, with the form's address behind Details.
UNTESTED_TITLE = "Not yet tested on this model"
#: A write held on a model not yet tested, said once before a click at the narrowest scope that
#: holds every grayed row: a group's line, or a row's own, never a tooltip alone (the owner's
#: decision of 2026-10-27). The cause stays the CLI's, the refusal toast's and Details'.
TEST_WAIT = "for a test on this model"
CHANGES_WAIT = f"Changes wait {TEST_WAIT}"
PROFILE_WRITES_WAIT = f"Writes to onboard profile 1 wait {TEST_WAIT}"
WAITS_FOR_TEST = f"Waits {TEST_WAIT}"
UNTESTED_SUBTITLE = "Run `gearbench-ctl report` in a terminal and paste its block into the form"
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


def firmware_row(group: Adw.PreferencesGroup) -> Adw.ActionRow:
    """A device tab's Firmware row, hidden until its page shows it, added to `group`."""
    row = plain_row(Adw.ActionRow, FIRMWARE, "", visible=False)
    group.add(row)
    return row


def show_firmware(row: Adw.ActionRow, firmware: object) -> None:
    """The row with a reply's `firmware`, the device's own text, under its title where it
    gives a version; hidden where it gives none, as for a model that has none to read or a
    read that failed."""
    version = firmware if isinstance(firmware, str) else ""
    row.set_subtitle(displayable(version))
    row.set_visible(bool(version))


def open_report_form(widget: Gtk.Widget, toast: Callable[..., object]) -> None:
    """Open REPORT_FORM in the browser, over the window `widget` is on. A failure is toasted
    with the address; a launch the person dismissed is not."""
    def launched(launcher: Gtk.UriLauncher, result: Gio.AsyncResult) -> None:
        try:
            launcher.launch_finish(result)
        except GLib.Error as exc:
            # As Gtk.FileDialog's: a portal's chooser closed unpicked is no failure.
            if not exc.matches(Gtk.dialog_error_quark(), Gtk.DialogError.DISMISSED):
                # The address as the detail, the one text under Details that can be selected
                # and copied: an Adw.AlertDialog's body cannot be (libadwaita 1.9.1).
                toast(NO_BROWSER, REPORT_FORM,
                      full=f"{exc.message}. Open the Device report form at:")

    # A tab's widget is on the window, its root.
    Gtk.UriLauncher(uri=REPORT_FORM).launch(cast("Gtk.Window", widget.get_root()), None,
                                            launched)


#: The effect row's title on every page that has one, the Keyboard tab's list of what its
#: board shows included (the owner's decision of 2026-10-22).
EFFECT = "Effect"
#: The follow switch, a keyboard's that does not lead, a mouse's and a headset's lights', with
#: the leader's name (keyboard.followed): the owner's words of 2026-10-21, "Follow the G915".
FOLLOW = "Follow the {}"
#: The line under a mouse's and a headset's follow switch: in Per-key mode a synced device
#: follows the background, black unless one was set, so the row says so. Its brightness and
#: idle dimming: the owner's decision of 2026-09-28.
SYNC_HELP = "Takes the keyboard's color (under Per-key, the background) and dims with it"
#: The row that gives a device's lighting back to it, on the Mouse tab and a headset of the
#: G432's family, and the Lighting group's line on whose lighting the device shows.
LIGHTS_OWN = "Show its own lighting"
OWN_LIGHTING_SHOWN = "Shows its own lighting until Gearbench sends a color or effect"
GEARBENCH_LIGHTING_SHOWN = "Shows the lighting Gearbench sends"
#: The Lighting group's line on a device that shows Gearbench's lighting from its setup: a
#: stop gives it back its own.
LIGHTING_HOLDS = "Holds only while the daemon runs"
#: The grayed-out Color picker's line under Off, and under an effect with no color of its own
#: (lighting.COLORLESS, mouse.COLORLESS_EFFECTS), with the effect's name, on the Keyboard tab,
#: the Mouse tab and a headset of the G432's family.
OFF_PICKER_HELP = "The lights are off; none to pick"
COLORLESS_PICKER_HELP = "{} takes no color; none to pick"


def no_color_line(effect: str | None) -> str | None:
    """The grayed-out Color picker's line under `effect`, by its name, where it takes no
    color: Off, or one of mouse.COLORLESS_EFFECTS. None under one that takes a color, or
    none."""
    if effect in COLORLESS_EFFECTS:
        return COLORLESS_PICKER_HELP.format(effect_label(effect))
    return OFF_PICKER_HELP if effect == OFF else None

#: The title of the import dialogs' checklist page, G HUB's and the pack's: the G HUB one
#: offers the desktop default beside the profiles.
CHOOSE_WHAT = "Choose what to import"

#: A status row's route words on its cable (wording.route).
ON_CABLE = capitalized(route("cable"))


def show_looking(row: Adw.ActionRow, dot: Gtk.Image) -> None:
    """A headset page's Status row before its first reply: LOOKING, with a spinner where the
    dot goes, as the other device pages' status page has while it looks (the owner's decision
    of 2026-10-27); set_dot puts the dot there."""
    row.set_subtitle(LOOKING)
    spin_dot(dot, LOOKING)


def through(dongle: str) -> str:
    """A headset's route, by the words for what plugs into the computer: On its cable, or
    Through its base station, its receiver, its USB adapter (wording.route)."""
    return capitalized(route(dongle))


def route_text(product: str | None, cabled: frozenset[int]) -> str | None:
    """Which way a device is connected, from its reply's product id; None without one."""
    way = route_of(product, cabled)
    return None if way is None else capitalized(way)


def battery_text(battery: dict | None, at: float | None = None, cabled: bool = False,
                 gone: bool = False) -> str:
    """A battery reading in the words of every view (battery.reading_words), with a capital
    as the Battery row's other words have: NOT_READ for none, or for one with no level or
    state, ON_CABLE for that from a keyboard on its cable (`cabled`), and NO_DEVICE with no
    device there (`gone`).

    `at`, in seconds since the epoch, is when a device that no longer answers
    was last read; it dates only a reading with words, since "Not read, read at" says two
    things.
    """
    if gone:
        return NO_DEVICE
    words = reading_words(battery or {})
    if not words:
        return ON_CABLE if cabled else NOT_READ
    if at is not None:
        words += READ_AT.format(clock_shown(at))
    return capitalized(words)


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


def swatch_sheet(colors: Iterable[int]) -> str:
    """A swatch rule for each color, sorted so an unchanged list gives the same sheet."""
    return "".join(f".{swatch_class(c)} {{ background-color: {format_color(c)}; }}"
                   for c in sorted(colors))


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


def stylesheet(unit: float, colors: Collection[int]) -> str:
    """The Keyboard tab's sheet: the key rule at this key size, then a swatch rule for each
    color (swatch_sheet) and a key rule for each."""
    # Sorted: the Keyboard tab passes a set, whose order follows how it was built,
    # and Styler.update skips a sheet only when its text is the same.
    return (KEY_TEMPLATE.format(font=max(6, int(unit * 0.30)), radius=KEY_RADIUS)
            + swatch_sheet(colors) + "\n".join(
                f".{key_class(c)} {{ background-image: none;"
                f" background-color: {format_color(c)}; color: {readable_on(c)}; }}"
                for c in sorted(colors)))


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
