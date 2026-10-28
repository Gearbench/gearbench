"""Color helpers and the picker widget.

The conversions are free functions, testable without a display. Color text
goes through config's parser and formatter, so a color this window writes
reads back through the same range-checked code as one the CLI writes.
"""

from __future__ import annotations

import colorsys
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from gi.repository import Adw, Gdk, Gtk

from ..config import format_color, parse_color
from ..settings_files import ConfigError
from ..wording import counted
from .style import EntryTexts, Styler, confirm, swatch_class, take_color_class, weakly
from .worker import Settle

if TYPE_CHECKING:                       # pragma: no cover
    from .window import Window

#: The Saved colors row's title and line, and its + button's tooltip.
SAVED_TITLE = "Saved colors"
SAVED_HELP = "Click to apply, right-click to remove; + saves the current color"
SAVE_TIP = "Save the current color"
#: The keys that do what a right click does on a focused widget, as Shift+F10 and Menu, the
#: context menu keys, do on GTK's own color plane.
RIGHT_CLICK_KEYS = "Delete|KP_Delete|BackSpace|<Shift>F10|Menu"


#: An arrow key's step, plain and with Alt, as in GTK's color plane (gtkcolorplane.c).
#: Page Up and Page Down move the strip the big step, as on a Gtk.Scale.
KEY_STEP, KEY_BIG_STEP = 0.01, 0.1
#: Milliseconds after the last of a run of keys before the run commits as one change.
KEY_SETTLE_MS = 300


def from_hsv(hsv: Sequence[float]) -> int:
    """One 0xrrggbb from hue, saturation and value, each 0..1."""
    r, g, b = colorsys.hsv_to_rgb(*hsv)
    return (round(r * 255) << 16) | (round(g * 255) << 8) | round(b * 255)


def to_hsv(color: int) -> list[float]:
    """Hue, saturation and value for `color`. A gray comes back as hue 0 and
    saturation 0, so a change of value keeps it gray."""
    r, g, b = ((color >> 16) & 0xFF) / 255, ((color >> 8) & 0xFF) / 255, (color & 0xFF) / 255
    return list(colorsys.rgb_to_hsv(r, g, b))


def pure_hue(hue: float) -> int:
    """The fully saturated color at `hue`, at full value."""
    return from_hsv((hue, 1.0, 1.0))


def to_rgba(color: int) -> Gdk.RGBA:
    """`color` as an opaque Gdk.RGBA, for what the drawn keyboard draws under its keys."""
    rgba = Gdk.RGBA()
    rgba.parse(format_color(color))
    return rgba


class _Slider(Gtk.DrawingArea, Gtk.AccessibleRange):
    """A drawing area that a screen reader follows as a slider.

    GTK 4.22 gives a widget the AT-SPI Value interface only when it is a
    Gtk.AccessibleRange (gtk_atspi_get_value_vtable). Without it Orca 50 reads each
    value as 0, so it speaks no step after the first (is_presentable_value_change).
    Read from GTK's and Orca's source, not measured with a screen reader.
    """


class ColorPicker:
    """A saturation/value square over a hue strip, with a swatch and a hex row.

    CSS gradients, not Gtk.ColorDialog: that dialog hands back only the color
    chosen when it closes, so the device could not show the color while it is
    open. One drag gesture also covers a click: drag-begin fires on the press
    and drag-end on the release. Each pick that changes the color calls
    `on_preview(color)`; the release and `apply` (an applied hex entry, a saved color
    clicked) call `on_commit(color)`; `set_color` calls neither.
    `on_error` gets the message for a hex value that did not parse.

    The square and the strip take the keyboard focus as GTK's color plane and
    hue scale do; a click does not. Each key that changes the color previews,
    and a run of keys commits once: KEY_SETTLE_MS after the last, at focus
    loss, or at `flush`.

    Each instance owns the two CSS rules for its own color, under class names
    with a per-instance tag, so two pickers on screen do not share a rule. The
    shared rules (borders, gradients, markers) are the window's.

    Its handlers on its own widgets hold it weakly (style.weakly), so a picker in a dialog
    goes with the dialog; whoever builds one keeps it.
    """

    _instances = 0

    def __init__(self, on_preview: Callable[[int], object], on_commit: Callable[[int], object],
                 on_error: Callable[[str], object], subtitle: str,
                 color: int = 0x0080FF) -> None:
        ColorPicker._instances += 1
        tag = f"g915-p{ColorPicker._instances}"
        self.on_preview = on_preview
        self.on_commit = on_commit
        self.on_error = on_error
        self.color = color
        self._hsv = [0.0, 1.0, 1.0]
        #: True from press to release, or until a run of keys settles: the
        #: window's periodic reads wait for it so they do not fight the drag.
        self.dragging = False
        #: The commit waiting for a run of keys to settle.
        self._settle = Settle(self._drag_ended)
        #: The Hex row's text as the picker last put it there (typing).
        self._shown = EntryTexts()

        self._css = Styler()
        #: Class-name prefixes; each class names its color (_refresh_css).
        self._sv_prefix, self._swatch_prefix = f"{tag}-sv-", f"{tag}-swatch-"

        # An image to assistive technology, named for its color in _refresh_css: the CSS fill
        # is not in the accessibility tree, and the Hex row is hidden while the row is closed.
        self.swatch = Gtk.Box(valign=Gtk.Align.CENTER, css_classes=["g915-swatch"],
                              accessible_role=Gtk.AccessibleRole.IMG)
        self.swatch.set_size_request(32, 32)
        self.row = Adw.ExpanderRow(title="Color", subtitle=subtitle)
        self.row.add_suffix(self.swatch)

        self.sv_area = _Slider(css_classes=["g915-sv"], hexpand=True, focusable=True,
                               accessible_role=Gtk.AccessibleRole.SLIDER)
        self.sv_area.update_property([Gtk.AccessibleProperty.LABEL], ["Saturation and value"])
        self.sv_area.set_size_request(-1, 150)
        self.sv_marker = Gtk.Box(css_classes=["g915-marker"], halign=Gtk.Align.START,
                                 valign=Gtk.Align.START, can_target=False)
        self.sv_marker.set_size_request(14, 14)
        # The gradients and the picks run left to right in any locale; in a right-to-left
        # one, halign START and margin-start would measure from the right edge.
        self.sv_marker.set_direction(Gtk.TextDirection.LTR)
        sv_overlay = Gtk.Overlay()
        sv_overlay.set_child(self.sv_area)
        sv_overlay.add_overlay(self.sv_marker)
        self._attach(self.sv_area, "sv")
        self.sv_area.connect("resize", weakly(self._place_markers))

        self.hue_area = _Slider(css_classes=["g915-hue"], hexpand=True, focusable=True,
                                accessible_role=Gtk.AccessibleRole.SLIDER)
        self.hue_area.update_property(
            [Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.VALUE_MIN,
             Gtk.AccessibleProperty.VALUE_MAX], ["Hue", 0.0, 360.0])
        self.hue_area.set_size_request(-1, 26)
        self.hue_marker = Gtk.Box(css_classes=["g915-marker"], halign=Gtk.Align.START,
                                  valign=Gtk.Align.CENTER, can_target=False)
        self.hue_marker.set_size_request(10, 22)
        self.hue_marker.set_direction(Gtk.TextDirection.LTR)
        hue_overlay = Gtk.Overlay()
        hue_overlay.set_child(self.hue_area)
        hue_overlay.add_overlay(self.hue_marker)
        self._attach(self.hue_area, "hue")
        self.hue_area.connect("resize", weakly(self._place_markers))

        picker = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                         margin_top=8, margin_bottom=8, margin_start=12, margin_end=12)
        picker.append(sv_overlay)
        picker.append(hue_overlay)
        # Not a focus stop of its own: Tab goes on to the square.
        self.row.add_row(Gtk.ListBoxRow(child=picker, activatable=False, selectable=False,
                                        focusable=False))

        self.hex_row = Adw.EntryRow(title="Hex", show_apply_button=True)
        self.hex_row.connect("apply", weakly(self._on_hex))
        self.row.add_row(self.hex_row)
        self.set_color(color)

    def set_color(self, color: int) -> None:
        """Show `color` without previewing or committing it; a run of keys still
        settling is dropped. A hue picked on a gray or black survives a poll that
        reads the same color back."""
        if self._settle.pending:
            self._settle.cancel()
            self.dragging = False
        self.color = color
        if from_hsv(self._hsv) != color:
            self._hsv = to_hsv(color)
        self._place_markers()
        self._show_text()
        self._refresh_css()

    def _show_text(self) -> None:
        """Put the color in the Hex row through EntryTexts.show, since the polls repeat it."""
        self._shown.show(self.hex_row, format_color(self.color))

    def typing(self, parent: Gtk.Window) -> bool:
        """True while the cursor is in the Hex row over text the picker did not put
        there (EntryTexts.typing), so a poll leaves a color being typed alone."""
        return self._shown.typing(parent, self.hex_row)

    def destroy(self) -> None:
        """Drop this picker's CSS rules: the display holds them past the widget."""
        self._settle.cancel()
        self._css.destroy()

    def _attach(self, widget: Gtk.Widget, area: str) -> None:
        """The drag, the keys and the focus of `area`, "sv" for the square or "hue" for the
        strip, on `widget`. Each handler is told the area by name: a bound method handed as
        a handler's data would hold the picker as strongly as a lambda does."""
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", weakly(self._on_drag_begin), area)
        drag.connect("drag-update", weakly(self._on_drag_update), area)
        drag.connect("drag-end", weakly(self._drag_ended))
        widget.add_controller(drag)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", weakly(self._on_key_pressed), area)
        widget.add_controller(keys)
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", weakly(self.flush))
        widget.add_controller(focus)

    def _pick(self, area: str) -> Callable[[float, float], None]:
        return self._pick_sv if area == "sv" else self._pick_hue

    def _on_drag_begin(self, _gesture: Gtk.GestureDrag, x: float, y: float, area: str) -> None:
        self._settle.cancel()           # the release commits keys still settling
        self.dragging = True
        self._pick(area)(x, y)

    def _on_drag_update(self, gesture: Gtk.GestureDrag, dx: float, dy: float,
                        area: str) -> None:
        _ok, x, y = gesture.get_start_point()
        self._pick(area)(x + dx, y + dy)

    def _on_key_pressed(self, _controller: Gtk.EventControllerKey, keyval: int, _code: int,
                        state: Gdk.ModifierType, area: str) -> bool:
        step = KEY_BIG_STEP if state & Gdk.ModifierType.ALT_MASK else KEY_STEP
        return (self._on_sv_key if area == "sv" else self._on_hue_key)(keyval, step)

    def _on_sv_key(self, keyval: int, step: float) -> bool:
        """An arrow on the square: Left and Right step saturation, Up and Down value."""
        move = {Gdk.KEY_Left: (-step, 0.0), Gdk.KEY_KP_Left: (-step, 0.0),
                Gdk.KEY_Right: (step, 0.0), Gdk.KEY_KP_Right: (step, 0.0),
                Gdk.KEY_Up: (0.0, step), Gdk.KEY_KP_Up: (0.0, step),
                Gdk.KEY_Down: (0.0, -step), Gdk.KEY_KP_Down: (0.0, -step)}.get(keyval)
        if move is None:
            return False
        self._step(1, self._hsv[1] + move[0])
        self._step(2, self._hsv[2] + move[1])
        self._stepped()
        return True

    def _on_hue_key(self, keyval: int, step: float) -> bool:
        """A key on the strip, mapped as a Gtk.Scale maps it."""
        hue = self._hsv[0]
        wanted = {Gdk.KEY_Right: hue + step, Gdk.KEY_KP_Right: hue + step,
                  Gdk.KEY_Up: hue + step, Gdk.KEY_KP_Up: hue + step,
                  Gdk.KEY_Left: hue - step, Gdk.KEY_KP_Left: hue - step,
                  Gdk.KEY_Down: hue - step, Gdk.KEY_KP_Down: hue - step,
                  Gdk.KEY_Page_Up: hue + KEY_BIG_STEP, Gdk.KEY_KP_Page_Up: hue + KEY_BIG_STEP,
                  Gdk.KEY_Page_Down: hue - KEY_BIG_STEP, Gdk.KEY_KP_Page_Down: hue - KEY_BIG_STEP,
                  Gdk.KEY_Home: 0.0, Gdk.KEY_KP_Home: 0.0,
                  Gdk.KEY_End: 1.0, Gdk.KEY_KP_End: 1.0}.get(keyval)
        if wanted is None:
            return False
        self._step(0, wanted)
        self._stepped()
        return True

    def _step(self, index: int, value: float) -> None:
        """Set one HSV component, clamped to 0..1; rounded so steps up and back do not drift."""
        self._hsv[index] = round(min(max(value, 0.0), 1.0), 6)

    def _stepped(self) -> None:
        """Preview a key's step now and commit once the keys settle; `dragging` holds
        the window's reads meanwhile. A step that leaves the color unchanged (at a
        range's end, or a gray's hue) sends nothing, and one during a pointer drag is
        committed by its release."""
        if from_hsv(self._hsv) == self.color:
            self._refresh_css()
            self._place_markers()
            return
        pointer = self.dragging and not self._settle.pending
        self.dragging = True
        self._picked()
        if pointer:
            return
        self._settle.restart(KEY_SETTLE_MS)

    def flush(self, *_args: object) -> bool:
        """Commit a run of keys still settling, at focus loss or window close; True if
        there was one."""
        return self._settle.flush()

    def _drag_ended(self, *_args: object) -> None:
        self.dragging = False
        self.on_commit(self.color)

    def _pick_sv(self, x: float, y: float) -> None:
        w, h = max(self.sv_area.get_width(), 1), max(self.sv_area.get_height(), 1)
        self._hsv[1] = min(max(x / w, 0.0), 1.0)
        self._hsv[2] = 1.0 - min(max(y / h, 0.0), 1.0)
        self._picked()

    def _pick_hue(self, x: float, _y: float) -> None:
        self._hsv[0] = min(max(x / max(self.hue_area.get_width(), 1), 0.0), 1.0)
        self._picked()

    def _place_markers(self, *_args: object) -> None:
        h, sat, val = self._hsv
        w, ht = max(self.sv_area.get_width(), 1), max(self.sv_area.get_height(), 1)
        self.sv_marker.set_margin_start(int(sat * w - 7))
        self.sv_marker.set_margin_top(int((1 - val) * ht - 7))
        self.hue_marker.set_margin_start(int(h * max(self.hue_area.get_width(), 1) - 5))
        # Where the markers are, in words, for a screen reader. GTK tells it that a slider
        # moved only when VALUE_NOW changes (gtk_at_spi_context_state_change), and Orca then
        # says VALUE_TEXT, so the square's number changes with either of its two values.
        # It has no VALUE_MIN or VALUE_MAX: Orca would say the number as a percent too.
        saturation, value = round(sat * 100), round(val * 100)
        self.sv_area.update_property(
            [Gtk.AccessibleProperty.VALUE_NOW, Gtk.AccessibleProperty.VALUE_TEXT],
            [float(saturation * 1000 + value), f"saturation {saturation}%, value {value}%"])
        degrees = round(h * 360)
        self.hue_area.update_property(
            [Gtk.AccessibleProperty.VALUE_NOW, Gtk.AccessibleProperty.VALUE_TEXT],
            [float(degrees), counted(degrees, "degree")])

    def _picked(self) -> None:
        color = from_hsv(self._hsv)
        changed = color != self.color
        self.color = color
        self._show_text()
        self._refresh_css()
        self._place_markers()
        # A pick that leaves the color as it was (a hue on a gray, a drag past an edge)
        # sends nothing, as a key's step does (_stepped).
        if changed:
            self.on_preview(color)

    def _on_hex(self, row: Adw.EntryRow) -> None:
        try:
            color = parse_color(row.get_text(), "Color")
        except ConfigError as exc:
            self.on_error(str(exc))
            self._show_text()
            return
        self.apply(color)

    def apply(self, color: int) -> None:
        """Show `color` and commit it."""
        self.set_color(color)
        self.on_commit(color)

    def _refresh_css(self) -> None:
        """Load this picker's two rules unless loaded already (Styler.update), and after a
        load give the widgets their classes, each named for its color: a rule reloaded
        under an unchanged class left the widget drawn in its old color until restyled
        (GTK 4.22.4 on Wayland, measured 2026-10-02); a class change restyles it. The
        swatch's color goes to a screen reader as its name."""
        hue, swatch = format_color(pure_hue(self._hsv[0])), format_color(self.color)
        sv_class, swatch_class = self._sv_prefix + hue[1:], self._swatch_prefix + swatch[1:]
        sheet = (f".{sv_class} {{ background-color: {hue}; }}"
                 f".{swatch_class} {{ background-color: {swatch}; }}")
        if not self._css.update(sheet):
            return
        take_color_class(self.sv_area, self._sv_prefix, sv_class)
        take_color_class(self.swatch, self._swatch_prefix, swatch_class)
        self.swatch.update_property([Gtk.AccessibleProperty.LABEL], [f"Color {swatch}"])


class SavedColors:
    """The Saved colors row in a picker's expander: a button per color the daemon keeps
    (`window.saved_colors`), a click showing it on the picker and committing it, as the Hex
    row does, a right-click or one of RIGHT_CLICK_KEYS removing it after a question, and +
    saving the color `current` gives, the picker's by default. Every picker has one, so each
    offers the same list (the owner's rule of 2026-10-18 on feature parity).
    `refresh_css` loads the swatches' rules: the Keyboard tab's, which every page shares."""

    def __init__(self, picker: ColorPicker, window: Window,
                 current: Callable[[], int] | None = None,
                 refresh_css: Callable[[], None] = lambda: None) -> None:
        self._window = window
        self._picker = picker
        self._current = current or (lambda: picker.color)
        self._refresh_css = refresh_css
        #: The colors the buttons show, which a rebuild finds the focus's color in.
        self._shown: tuple[int, ...] = ()
        row = Adw.ActionRow(title=SAVED_TITLE, subtitle=SAVED_HELP)
        self.box = Gtk.Box(spacing=4, valign=Gtk.Align.CENTER)
        self.add_button = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER,
                                     tooltip_text=SAVE_TIP)
        # An icon alone: its name for a screen reader, as the mute buttons set theirs.
        self.add_button.update_property([Gtk.AccessibleProperty.LABEL], [SAVE_TIP])
        # Every handler through weakly, as the picker's: the row is its picker's.
        self.add_button.connect("clicked", weakly(self.on_save))
        # In a scroller: the window is no narrower than its widest row, and
        # SAVED_COLORS_MAX buttons in one line run past 1,100 px.
        row.add_suffix(Gtk.ScrolledWindow(child=self.box, propagate_natural_width=True,
                                          vscrollbar_policy=Gtk.PolicyType.NEVER,
                                          valign=Gtk.Align.CENTER))
        row.add_suffix(self.add_button)
        picker.row.add_row(row)

    def buttons(self) -> list[Gtk.Widget]:
        return list(self.box)

    def rebuild(self) -> None:
        """A button per saved color, each made anew. The keyboard focus stays on the color
        it was on; once that color is gone, on the button now at its place, or the last if
        the row now ends before it, or on + with none left."""
        buttons = self.buttons()
        focus = self._window.get_focus()
        was = next((i for i, b in enumerate(buttons) if b is focus), None)
        focused = self._shown[was] if was is not None else None
        for button in buttons:
            self.box.remove(button)
        self._shown = tuple(self._window.saved_colors)
        for color in self._shown:
            self.box.append(self._button(color))
        if was is not None:
            buttons = self.buttons()
            if focused in self._shown:
                buttons[self._shown.index(focused)].grab_focus()
            elif buttons:
                buttons[min(was, len(buttons) - 1)].grab_focus()
            else:
                self.add_button.grab_focus()
        self._refresh_css()

    def _button(self, color: int) -> Gtk.Button:
        area = Gtk.Box(css_classes=["g915-saved", swatch_class(color)])
        area.set_size_request(22, 22)
        text = format_color(color)
        button = Gtk.Button(child=area, valign=Gtk.Align.CENTER,
                            tooltip_text=f"{text}: right-click or Delete to remove")
        button.update_property([Gtk.AccessibleProperty.LABEL], [f"Saved color {text}"])
        button.connect("clicked", weakly(self._on_use), color)
        remove = Gtk.GestureClick(button=3)
        remove.connect("pressed", weakly(self._on_right_click), color)
        button.add_controller(remove)
        keys = Gtk.ShortcutController()
        keys.add_shortcut(Gtk.Shortcut.new(
            Gtk.ShortcutTrigger.parse_string(RIGHT_CLICK_KEYS),
            Gtk.CallbackAction.new(weakly(self._on_forget_key), color)))
        button.add_controller(keys)
        return button

    def _on_use(self, _button: Gtk.Button, color: int) -> None:
        self._picker.apply(color)

    def _on_right_click(self, _gesture: Gtk.GestureClick, _n_press: int, _x: float, _y: float,
                        color: int) -> None:
        self._forget(color)

    def _on_forget_key(self, _widget: Gtk.Widget, _args: object, color: int) -> bool:
        self._forget(color)
        return True

    def on_save(self, _b: Gtk.Button | None) -> None:
        color = self._current()
        if color in self._window.saved_colors:
            self._window.toast("Already saved")
            return
        self._window.call({"command": "save_color", "value": format_color(color)},
                          lambda reply: self._took_save(reply, color))

    def _took_save(self, reply: dict | None, color: int) -> None:
        """Take the daemon's list from a save_color reply and name what the save dropped, read
        off the reply: the daemon decides which color goes to keep its limit."""
        before = self._window.saved_colors
        self._window.took_saved(reply)
        if reply is None:
            return
        gone = [old for old in before if old not in self._window.saved_colors]
        self._window.toast(
            f"Saved {format_color(color)} and removed the oldest, {format_color(gone[0])}"
            if gone else f"Saved {format_color(color)}")

    def _forget(self, color: int) -> None:
        """Ask before the daemon forgets a saved color: a tuned color lost to a mis-click is
        annoying to redo."""
        text = format_color(color)
        confirm(self._window, f"Remove {text}?",
                "It leaves the saved list; the lighting does not change.", "Remove",
                lambda: self._window.call({"command": "forget_color", "value": text},
                                          self._window.took_saved))
