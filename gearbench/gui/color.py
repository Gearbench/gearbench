"""Color helpers and the picker widget.

The conversions are free functions, testable without a display. Color text
goes through config's parser and formatter, so a color this window writes
reads back through the same range-checked code as one the CLI writes.
"""

from __future__ import annotations

import colorsys
from collections.abc import Callable, Sequence

from gi.repository import Adw, Gdk, Gtk

from ..config import ConfigError, format_color, parse_color
from ..wording import counted
from .style import EntryTexts, Styler, take_color_class
from .worker import Settle

__all__ = [
    "ColorPicker",
    "from_hsv",
    "pure_hue",
    "to_hsv",
]


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
    """The fully saturated color at `hue`, for the picker's square."""
    return from_hsv((hue, 1.0, 1.0))


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
    `on_preview(color)`; the release and an applied hex entry call
    `on_commit(color)`; `set_color` calls neither.
    `on_error` gets the message for a hex value that did not parse.

    The square and the strip take the keyboard focus as GTK's color plane and
    hue scale do; a click does not. Each key that changes the color previews,
    and a run of keys commits once: KEY_SETTLE_MS after the last, at focus
    loss, or at `flush`.

    Each instance owns the two CSS rules for its own color, under class names
    with a per-instance tag, so two pickers on screen do not share a rule. The
    shared rules (borders, gradients, markers) are the window's.
    """

    _instances = 0

    def __init__(self, on_preview: Callable[[int], None], on_commit: Callable[[int], None],
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

        self.swatch = Gtk.Box(valign=Gtk.Align.CENTER, css_classes=["g915-swatch"])
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
        self._attach(self.sv_area, self._pick_sv, self._on_sv_key)
        self.sv_area.connect("resize", lambda *_a: self._place_markers())

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
        self._attach(self.hue_area, self._pick_hue, self._on_hue_key)
        self.hue_area.connect("resize", lambda *_a: self._place_markers())

        picker = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                         margin_top=8, margin_bottom=8, margin_start=12, margin_end=12)
        picker.append(sv_overlay)
        picker.append(hue_overlay)
        # Not a focus stop of its own: Tab goes on to the square.
        self.row.add_row(Gtk.ListBoxRow(child=picker, activatable=False, selectable=False,
                                        focusable=False))

        self.hex_row = Adw.EntryRow(title="Hex", show_apply_button=True)
        self.hex_row.connect("apply", self._on_hex)
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
        """Put the color in the Hex row through EntryTexts.show, which leaves a row that
        holds it already alone, its cursor and selection too, since the Mouse tab's poll
        repeats the color every 5 s, and does not arm the row's apply button."""
        self._shown.show(self.hex_row, format_color(self.color))

    def typing(self, parent: Gtk.Window) -> bool:
        """True while the cursor is in the Hex row over text the picker did not put
        there (EntryTexts.typing), so a poll leaves a color being typed alone."""
        return self._shown.typing(parent, self.hex_row)

    def destroy(self) -> None:
        """Drop this picker's CSS rules: the display holds them past the widget."""
        self._settle.cancel()
        self._css.destroy()

    def _attach(self, widget: Gtk.Widget, handler: Callable[[float, float], None],
                on_key: Callable[[int, float], bool]) -> None:
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", lambda _g, x, y: self._drag_started(handler, x, y))
        drag.connect("drag-update", lambda g, dx, dy: handler(
            g.get_start_point()[1] + dx, g.get_start_point()[2] + dy))
        drag.connect("drag-end", lambda *_a: self._drag_ended())
        widget.add_controller(drag)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", lambda _c, keyval, _code, state: on_key(
            keyval, KEY_BIG_STEP if state & Gdk.ModifierType.ALT_MASK else KEY_STEP))
        widget.add_controller(keys)
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda _c: self.flush())
        widget.add_controller(focus)

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

    def flush(self) -> bool:
        """Commit a run of keys still settling, at focus loss or window close; True if
        there was one."""
        return self._settle.flush()

    def _drag_started(self, handler: Callable[[float, float], None], x: float, y: float) -> None:
        self._settle.cancel()           # the release commits keys still settling
        self.dragging = True
        handler(x, y)

    def _drag_ended(self) -> None:
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

    def _place_markers(self) -> None:
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
        self.set_color(color)
        self.on_commit(color)

    def _refresh_css(self) -> None:
        """Load this picker's two rules unless they are already loaded: a load restyles
        every widget on the display, and the Mouse tab's 5 s poll repeats the color.

        Each rule's class names its color, and the widgets take theirs after the load.
        A rule reloaded under an unchanged class left the widget drawn in its old color
        until restyled (GTK 4.22.4 on Wayland, measured 2026-10-02); a class change
        restyles it."""
        hue, swatch = format_color(pure_hue(self._hsv[0])), format_color(self.color)
        sv_class, swatch_class = self._sv_prefix + hue[1:], self._swatch_prefix + swatch[1:]
        sheet = (f".{sv_class} {{ background-color: {hue}; }}"
                 f".{swatch_class} {{ background-color: {swatch}; }}")
        if not self._css.update(sheet):
            return
        take_color_class(self.sv_area, self._sv_prefix, sv_class)
        take_color_class(self.swatch, self._swatch_prefix, swatch_class)
