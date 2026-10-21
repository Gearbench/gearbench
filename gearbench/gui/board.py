"""The drawn keyboard: a Gtk.Fixed of one button per key of a model's `Board`, scaled to the
width a DrawingArea under it reports. Each key with a light carries a CSS class named after the
color it shows.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

from gi.repository import Gdk, Gtk

from ..config import format_color, macro_name
from ..keycodes import ZONE_NAMES
from ..layout import Board, Key
from ..uinput import KEY_LABELS, shown_spec
from .style import KEY_PREFIX, key_class

#: Bounds of the pixels per key unit, which relayout picks to fit the window.
UNIT_MIN, UNIT_MAX = 13, 34
GAP = 2

#: The modifier bit of the board's other button, held at a press of this one. Only that bit:
#: a press's state holds the buttons already down, and broadway's its own as well. On Wayland
#: this rests on GDK's source (gdkseat-wayland.c), not on a run.
OTHER_BUTTON = {Gdk.BUTTON_PRIMARY: Gdk.ModifierType.BUTTON3_MASK,
                Gdk.BUTTON_SECONDARY: Gdk.ModifierType.BUTTON1_MASK}

#: The smallest key unit that shows a G key's binding under its name in map mode: at 24 px a
#: G key is 26 px wide, room for three or four characters at a 7 px font. Below it the
#: binding is in the tooltip only.
BINDING_MIN_UNIT = 24

#: What each reserved key does, for its tooltip in map mode.
RESERVED_TIPS = {
    "M1": "switches to M profile 1",
    "M2": "switches to M profile 2",
    "M3": "switches to M profile 3",
    "MR": "hold it and G1 to G5 send their MR bindings (gearbench-ctl bind --shift)",
    "Game": "Game Mode: turns off Super and Menu until pressed again",
    "BT": "switches the keyboard to Bluetooth; the keyboard does this itself",
    "Host": "switches which connection the keyboard uses; the keyboard does this itself",
}

#: The whole names of the keys whose labels are cut short to fit a keycap, from the key list's
#: names where it has the key.
FULL_NAMES = {
    "Esc": KEY_LABELS["esc"], "PrtSc": KEY_LABELS["sysrq"], "ScrLk": KEY_LABELS["scrolllock"],
    "Ins": KEY_LABELS["insert"], "Del": KEY_LABELS["delete"], "PgUp": KEY_LABELS["pageup"],
    "PgDn": KEY_LABELS["pagedown"], "Num": KEY_LABELS["numlock"],
    "Caps": KEY_LABELS["capslock"], "Prev": KEY_LABELS["previoussong"],
    "Play": KEY_LABELS["playpause"], "Next": KEY_LABELS["nextsong"],
    "Bright": "Brightness", "BT": "Bluetooth",
}

def navigable(board: Board) -> tuple[Key, ...]:
    """The keys of `board` the keyboard focus reaches: all but the reserved, which are
    insensitive and take no focus."""
    return tuple(key for key in board.keys if not key.reserved)


def first_key(keys: tuple[Key, ...]) -> Key:
    """Where Tab first enters a board of `keys`, navigable ones: the top left of the main
    block."""
    return next(key for key in keys if key.label == "Esc")


def neighbor(key: Key, direction: str, keys: tuple[Key, ...]) -> Key | None:
    """The key of `keys` an arrow moves the focus to from `key`, or None at the edge.

    `direction` is "left", "right", "up" or "down". A key beside `key` shares some of its
    height, one above or below some of its width, so the keypad's tall + and Enter sit
    beside both rows they span, and Up from the Up arrow reaches End over the gap. Of
    those, the nearest wins, then the one sharing most, then the one centered closest: Up
    from Space is B.
    """
    across = direction in ("up", "down")
    ahead = direction in ("right", "down")

    def spans(k: Key) -> tuple[tuple[float, float], tuple[float, float]]:
        """(along the move, across it), each as (start, end)."""
        width, height = (k.x, k.x + k.w), (k.y, k.y + k.h)
        return (height, width) if across else (width, height)

    (start, end), (low, high) = spans(key)
    ranked = []
    for index, other in enumerate(keys):
        (o_start, o_end), (o_low, o_high) = spans(other)
        shared = min(high, o_high) - max(low, o_low)
        gap = o_start - end if ahead else start - o_end
        if shared <= 1e-6 or gap < -1e-6:
            continue
        # `index` keeps min() from comparing two Keys, which have no order.
        ranked.append((round(gap, 3), -round(shared, 3),
                       abs((o_low + o_high) - (low + high)), index, other))
    return min(ranked)[-1] if ranked else None


def key_rect(key: Key, unit: float, board: Board) -> tuple[float, float, int, int]:
    """Where relayout puts a key of `board` at `unit` pixels a key unit: left, top, width,
    height."""
    return ((key.x - board.origin_x) * unit, (key.y - board.origin_y) * unit,
            max(int(key.w * unit) - GAP, 1), max(int(key.h * unit) - GAP, 1))


def shown_binding(bound: str) -> str:
    """A binding in the window's words: `Macro: name` for a macro, the key spec on one line
    otherwise."""
    macro = macro_name(bound)
    return f"Macro: {macro}" if macro is not None else shown_spec(bound)


def binding_label(label: str, bound: str | None, unit: float) -> str:
    """A G key's text in map mode: its name, and under it what it is bound
    to while the keys are big enough to show it (BINDING_MIN_UNIT)."""
    if not bound or unit < BINDING_MIN_UNIT:
        return label
    return f"{label}\n{shown_binding(bound)}"


def bindable_span(board: Board) -> str:
    """The bindable keys of `board`, first to last, as a toast names them: "G1-G5", "F1-F12"
    on the G915 TKL."""
    names = [key.label for key in board.keys if key.bindable]
    return f"{names[0]}-{names[-1]}"


def key_tooltip(key: Key, bound: str | None = None) -> str:
    """What a key says on hover in map mode and to a screen reader as its description, with
    a G key's binding, which the key may be too small to show. Color mode has none
    (show_colors).
    """
    if key.reserved:
        return f"{key.label} · {RESERVED_TIPS[key.label]}; not remappable"
    bits = [key.label]
    if key.bindable and bound:
        bits.append(shown_binding(bound))
    bits.append("click to bind it" if key.bindable else "not remappable")
    return " · ".join(bits)


def key_name(key: Key, board: Board) -> str:
    """What a screen reader calls a key: its label, after "Keypad" where its zone has it or
    after its side where `board` has a second key of that label, so the two Shift keys and
    the two 7s are told apart, and before its whole name where the label is cut short. The
    label stays in the name for speech input."""
    # dict.get is typed for the key's type alone; the None of a key with no light misses.
    first, _, rest = ZONE_NAMES.get(key.zone, "").partition(" ")  # type: ignore[arg-type]
    twinned = sum(other.label == key.label for other in board.keys) > 1
    if rest and (first == "KEYPAD" or first in ("LEFT", "RIGHT") and twinned):
        return f"{first.title()} {key.label}"
    if full := FULL_NAMES.get(key.label):
        return f"{key.label}, {full}"
    return key.label


def fitted_unit(width: float, board: Board) -> float:
    """The pixels per key unit that fit `board` in `width`, within UNIT_MIN and UNIT_MAX."""
    return max(UNIT_MIN, min(UNIT_MAX, (width - 4) / board.width))


def board_height(width: int, board: Board) -> int:
    """The height `board`'s keys take at the unit `width` fits, and the margin under them."""
    return int(board.height * fitted_unit(width, board)) + 4


class _Probe(Gtk.DrawingArea):
    """The size probe under the keys of `board`, as tall as they are at its width."""

    def __init__(self, board: Board) -> None:
        super().__init__(hexpand=True)
        self.board = board

    # Height for width, which GTK lays out in the pass that gives the width: a size request
    # set at "resize" comes during that pass, and GTK lays it out only when it lays out
    # something else. At a width not yet given (-1), its board's height at UNIT_MIN.
    def do_get_request_mode(self) -> Gtk.SizeRequestMode:
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation: Gtk.Orientation,
                   for_size: int) -> tuple[int, int, int, int]:
        if orientation == Gtk.Orientation.HORIZONTAL:
            return 0, 0, -1, -1
        height = board_height(for_size, self.board)
        return height, height, -1, -1


@dataclass
class Stroke:
    """One press on the board, from the press to the release.

    `x` and `y` are the pointer's, in the Gtk.Fixed's coordinates. It is a click on `first`
    until the pointer reaches another key, then a drag (`live`), or refused (`dead`) and
    ignored to its release. One from the keyboard (`keys`), Shift with the arrows, is a drag
    or refused from its first arrow, and ends at KeyBoard.end_key_stroke.
    """

    clear: bool
    x: float
    y: float
    first: Key | None
    live: bool = False
    dead: bool = False
    #: The keys the drag has reached, so none is passed on twice.
    seen: set[Key] = field(default_factory=set)
    #: From the keyboard rather than the pointer.
    keys: bool = False


class _Keys(Gtk.Fixed):
    """The board's Gtk.Fixed, one Tab stop however many keys it holds.

    Tab enters on the key that last had the focus, the first time on the board's Esc
    (first_key), and the next Tab leaves the board; the arrows move between the keys
    (KeyBoard.step). GTK's default would make each focusable key a Tab stop of its own.
    """

    #: The key Tab enters on: set once the keys are built, then the last
    #: key to have the focus, by the arrows, Tab or a click.
    last: Gtk.Widget

    def do_focus(self, direction: Gtk.DirectionType) -> bool:
        if direction not in (Gtk.DirectionType.TAB_FORWARD, Gtk.DirectionType.TAB_BACKWARD):
            return Gtk.Fixed.do_focus(self, direction)
        if self.get_focus_child() is not None:
            return False
        return self.last.grab_focus()

    # The stubs give the child a default, as for a call; GTK always passes it to the override.
    def do_set_focus_child(self, child: Gtk.Widget | None) -> None:  # type: ignore[override]
        Gtk.Fixed.do_set_focus_child(self, child)
        if child is not None:
            self.last = child


class KeyBoard:
    """The board as a widget, plus what each key currently shows.

    `on_click` is the left click on a key that is not reserved, from the key's own button.
    `on_clear` is the right click, at its release. `on_resize` comes after the keys move, so
    the caller can reload the CSS that carries the key font size.

    A press that moves onto a second key is a drag (the owner's decision of 2026-09-24):
    `on_stroke_start(clear)` says whether it may paint, `on_stroke_key(key, clear)` gets each
    lit key it reaches once, the first included, and `on_stroke_end(clear)` comes at the
    release, or at the other button's press, which ends it. `clear` is True for the right
    button. Without them a drag does nothing.

    From the keyboard the board is one Tab stop (`_Keys`), and the arrows move the focus
    (`neighbor`); Space and Enter are a click. Delete, BackSpace, Shift+F10 and Menu are the
    right click, as Shift+F10 and Menu are on GTK's own color plane. Shift with an arrow is a
    drag through the same three handlers, released at `end_key_stroke`. `description` is what
    the board says about its keys to a screen reader. `board` is the model's, whose keys it
    draws.
    """

    def __init__(self, on_click: Callable[[Key], None], on_clear: Callable[[Key], None],
                 on_resize: Callable[[], None], *,
                 on_stroke_start: Callable[[bool], bool] = lambda _clear: False,
                 on_stroke_key: Callable[[Key, bool], None] = lambda _key, _clear: None,
                 on_stroke_end: Callable[[bool], None] = lambda _clear: None,
                 description: str = "", board: Board) -> None:
        self.board = board
        self._navigable = navigable(board)
        self._on_resize = on_resize
        self._on_clear = on_clear
        self._on_stroke_start = on_stroke_start
        self._on_stroke_key = on_stroke_key
        self._on_stroke_end = on_stroke_end
        #: The press in progress, or None, and the gesture it came from.
        self.stroke: Stroke | None = None
        self._driver: Gtk.GestureDrag | None = None
        # Not a real unit: relayout skips a growth under half a pixel per key unit, so a real one
        # would make the first resize look like no change and the keys would never be placed.
        # A shrink always applies: the Overlay does not measure the Fixed, and a board wider
        # than its row runs past both edges.
        self.unit = 0.0
        # Centered: past UNIT_MAX the board stops growing, and at START it
        # would sit flush left in its section with the spare width on the right.
        self.fixed = _Keys(halign=Gtk.Align.CENTER, valign=Gtk.Align.START,
                           accessible_role=Gtk.AccessibleRole.GROUP)
        self.fixed.update_property(
            [Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
            ["Keyboard", description])
        self.keys: dict[Key, Gtk.Button] = {}
        self._key_of: dict[Gtk.Widget, Key] = {}
        self._build_keys(on_click)
        self._build_drags()
        self._build_shortcuts()
        self._build_probe()

    def _build_keys(self, on_click: Callable[[Key], None]) -> None:
        """A button per key, put on the Gtk.Fixed."""
        for key in self.board.keys:
            # can_shrink ellipsizes the label to fit: GtkFixed gives a child at least its
            # minimum size, so a wider label would spill over the keys beside it. Set on the
            # label itself, the ellipsis is undone by the next set_label.
            button = Gtk.Button(label=key.label, css_classes=["g915-key"], can_shrink=True)
            if (name := key_name(key, self.board)) != key.label:
                # A button made with a label is named by it, through a relation GTK takes
                # before LABEL: without the reset both Shift keys are "Shift". set_label
                # keeps the label child it finds, and adds no relation back.
                button.reset_relation(Gtk.AccessibleRelation.LABELLED_BY)
                button.update_property([Gtk.AccessibleProperty.LABEL], [name])
            if key.reserved:
                button.set_sensitive(False)
                # libadwaita's dim treatment, not an opacity set here: high contrast cannot
                # undo an app-set opacity.
                button.add_css_class("dimmed")
            else:
                button.connect("clicked", lambda _b, k=key: on_click(k))
            self.fixed.put(button, 0, 0)
            self.keys[key] = button
            self._key_of[button] = key
        self.fixed.last = self.keys[first_key(self._navigable)]

    def _build_drags(self) -> None:
        """A drag gesture per mouse button, on the Gtk.Fixed."""
        # Both buttons' presses, on the Gtk.Fixed in the capture phase, before the key's own
        # button sees them. A left press stays the button's click until it reaches a second
        # key; the drag then claims it, which cancels the click. The right button has only
        # this: clearing at the press would make a right-drag save at the press and again at
        # the release.
        for number in (Gdk.BUTTON_PRIMARY, Gdk.BUTTON_SECONDARY):
            drag = Gtk.GestureDrag(button=number,
                                   propagation_phase=Gtk.PropagationPhase.CAPTURE)
            drag.connect("drag-begin", self._on_drag_begin)
            drag.connect("drag-update", self._on_drag_update)
            drag.connect("drag-end", self._on_drag_end)
            self.fixed.add_controller(drag)

    def _build_shortcuts(self) -> None:
        """The shortcuts on a focused key, and what ends a drag from the keyboard."""
        # Keys on a focused key, in the bubble phase: the key's button takes only Space and
        # Enter, and the arrows would go on to the list box around the board, whose Up and
        # Down leave it.
        shortcuts = Gtk.ShortcutController(name="board-keys")
        arrows = (("Left", "left"), ("Right", "right"), ("Up", "up"), ("Down", "down"))
        for trigger, action in (
            *((f"{name}|KP_{name}", lambda d=d: self.step(d)) for name, d in arrows),
            *((f"<Shift>{name}", lambda d=d: self.step(d, paint=True)) for name, d in arrows),
            # Shift+F10 and Menu, the context menu keys: here, as on GTK's
            # own color plane, whatever a right click does.
            ("Delete|KP_Delete|BackSpace|<Shift>F10|Menu", self.clear_focused),
        ):
            shortcuts.add_shortcut(Gtk.Shortcut.new(
                Gtk.ShortcutTrigger.parse_string(trigger),
                Gtk.CallbackAction.new(lambda _w, _a, act=action: act())))
        self.fixed.add_controller(shortcuts)
        released = Gtk.EventControllerKey()
        released.connect("key-released", self._on_key_released)
        self.fixed.add_controller(released)
        left = Gtk.EventControllerFocus()
        left.connect("leave", lambda _c: self.end_key_stroke())
        self.fixed.add_controller(left)

    def _build_probe(self) -> None:
        """The size probe, and the overlay that puts the keys over it."""
        # A DrawingArea with no draw func, as a size probe: its "resize" gives the board its
        # width, and its measure the height the keys take at that width.
        self._probe = _Probe(self.board)
        self._probe.connect("resize", lambda _a, w, _h: self.relayout(w))
        self.widget = Gtk.Overlay(child=self._probe)
        self.widget.add_overlay(self.fixed)

    def place(self) -> None:
        """Lay the keys out at full size before any resize arrives: an Overlay child with no
        size is not drawn. Not in the constructor: it calls back into the caller, which is
        still building itself."""
        self.relayout(math.ceil(self.board.width * UNIT_MAX) + 4)

    def relayout(self, width: int) -> None:
        """Scale the drawn keyboard to the width available."""
        if width <= 1:
            return
        unit = fitted_unit(width, self.board)
        if self.unit <= unit < self.unit + 0.5:
            return
        self.unit = unit
        for key, button in self.keys.items():
            left, top, width, height = key_rect(key, unit, self.board)
            button.set_size_request(width, height)
            self.fixed.move(button, left, top)
        self._on_resize()

    def show_bindings(self, bindings: dict[str, str | None]) -> None:
        """Map mode: the bindable keys lit, everything else dimmed."""
        for key, button in self.keys.items():
            self._strip(button)
            bound = bindings.get(key.label)
            button.set_tooltip_text(key_tooltip(key, bound=bound))
            # The color description show_colors wrote would outrank the tooltip,
            # which GTK uses as the description only when none is set.
            button.reset_property(Gtk.AccessibleProperty.DESCRIPTION)
            if key.reserved:
                continue
            if key.bindable:
                button.add_css_class("g915-mappable")
                button.set_label(binding_label(key.label, bound, self.unit))
            else:
                button.add_css_class("g915-dim")

    def show_colors(self, per_key: dict[str, int], base: int) -> None:
        """Color mode: each key's own color, or `base` where it has none."""
        for key, button in self.keys.items():
            self._strip(button)
            # No tooltip in color mode, the owner's decision of 2026-09-25.
            button.set_tooltip_text(None)
            if key.reserved:
                continue
            button.set_label(key.label)
            # dict.get is typed for the key's type alone; a key with no light misses in both.
            self._show(button,
                       per_key.get(ZONE_NAMES.get(key.zone), base))  # type: ignore[arg-type]

    def show_key(self, key: Key, color: int) -> None:
        """One key in `color`, the rest left as they are: a drag repaints
        the keys it reaches, not the board."""
        button = self.keys[key]
        self._strip(button)
        self._show(button, color)

    @staticmethod
    def _show(button: Gtk.Button, color: int) -> None:
        button.add_css_class(key_class(color))
        # The color as text, for anything that cannot see the key: the CSS class is not in the
        # accessibility tree, and two shades can look the same. The key's name is its own.
        button.update_property([Gtk.AccessibleProperty.DESCRIPTION], [format_color(color)])

    def key_at(self, x: float, y: float) -> Key | None:
        """The key under a point of the Gtk.Fixed, as `relayout` placed it,
        or None over the gap between two keys and off the keys."""
        for key in self.keys:
            left, top, width, height = key_rect(key, self.unit, self.board)
            if left <= x < left + width and top <= y < top + height:
                return key
        return None

    # -- a press, and a drag ----------------------------------------------

    def begin_stroke(self, x: float, y: float, clear: bool) -> None:
        """A press at (x, y): the left button's, or the right's for `clear`."""
        self.stroke = Stroke(clear, x, y, self.key_at(x, y))

    def move_stroke(self, x: float, y: float) -> bool:
        """The pointer moved to (x, y) with the button held. True when this move made the
        press a drag, which the caller then claims.

        The line from the last point is walked a third of a key at a time, so a quick swipe
        reaches every key on its row, where the pointer's events may land two keys apart.
        """
        stroke = self.stroke
        if stroke is None or stroke.dead:
            return False
        started = False
        dx, dy = x - stroke.x, y - stroke.y
        steps = max(1, math.ceil(math.hypot(dx, dy) / max(self.unit / 3, 1.0)))
        for step in range(1, steps + 1):
            key = self.key_at(stroke.x + dx * step / steps, stroke.y + dy * step / steps)
            if key is None:
                continue
            if not stroke.live:
                if key is stroke.first:
                    continue
                if not self._on_stroke_start(stroke.clear):
                    stroke.dead = True
                    return False
                stroke.live = started = True
                if stroke.first is not None:
                    self._reach(stroke.first)
            self._reach(key)
        stroke.x, stroke.y = x, y
        return started

    def _reach(self, key: Key) -> None:
        """Pass on a key the drag reached, once, and none without a light."""
        stroke = self.stroke
        if stroke is None or key in stroke.seen:
            return
        stroke.seen.add(key)
        if key.zone is not None:
            self._on_stroke_key(key, stroke.clear)

    def end_stroke(self, x: float, y: float) -> None:
        """The release at (x, y). A drag ends; a right click on the key it
        was pressed on clears that key. A left click is the key button's.

        A press becomes a drag on a move, never here: a release that made
        one would leave the key's button its click as well."""
        stroke = self.stroke
        if stroke is None:
            return
        if stroke.live:
            self.move_stroke(x, y)          # the keys between the last move and here
        self._drop_stroke()
        if (stroke.clear and not stroke.live and not stroke.dead and stroke.first is not None
                and not stroke.first.reserved and self.key_at(x, y) is stroke.first):
            self._on_clear(stroke.first)

    def _drop_stroke(self) -> None:
        """No stroke from here on; a drag's release goes to `on_stroke_end`."""
        stroke, self.stroke = self.stroke, None
        if stroke is not None and stroke.live:
            self._on_stroke_end(stroke.clear)

    def _on_drag_begin(self, gesture: Gtk.GestureDrag, x: float, y: float) -> None:
        # The other button pressed during a stroke ends it: GTK resets a GestureSingle when
        # another button goes down while its own is held, and _on_drag_end ends a canceled
        # stroke. That press begins nothing while the stroke's button is still down.
        if (self.stroke is not None
                or gesture.get_current_event_state() & OTHER_BUTTON[gesture.get_button()]):
            return
        self._driver = gesture
        self.begin_stroke(x, y, gesture.get_button() == Gdk.BUTTON_SECONDARY)

    def _on_drag_update(self, gesture: Gtk.GestureDrag, dx: float, dy: float) -> None:
        ok, x, y = gesture.get_start_point()
        if gesture is self._driver and ok and self.move_stroke(x + dx, y + dy):
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _on_drag_end(self, gesture: Gtk.GestureDrag, dx: float, dy: float) -> None:
        if gesture is not self._driver:
            return
        self._driver = None
        ok, x, y = gesture.get_start_point()
        if ok:
            self.end_stroke(x + dx, y + dy)
            return
        # GTK ends a canceled sequence (a touch cancel, a broken grab) with no start point.
        # A live stroke still ends: its keys show their previews, and an open stroke on the
        # page holds the window's polls off.
        self._drop_stroke()

    # -- the keyboard -----------------------------------------------------

    def focused(self) -> Key | None:
        """The key with the keyboard focus, or None."""
        # dict.get is typed for the key's type alone; the None of no focus child misses.
        return self._key_of.get(self.fixed.get_focus_child())  # type: ignore[arg-type]

    def step(self, direction: str, paint: bool = False) -> bool:
        """An arrow on a focused key: the focus to the next key that way
        (`neighbor`), and nowhere at the edge. With `paint`, Shift held, it
        is a drag, from the keyboard: its first arrow asks
        `on_stroke_start` and passes on the key it started on, and every
        arrow passes on the key it moves to. A plain arrow ends one whose
        Shift was let go unseen."""
        key = self.focused()
        if key is None:
            return False
        if not paint:
            self.end_key_stroke()
        elif self.stroke is None:
            self.stroke = Stroke(False, 0.0, 0.0, key, keys=True)
            if self._on_stroke_start(False):
                self.stroke.live = True
                self._reach(key)
            else:
                self.stroke.dead = True
        target = neighbor(key, direction, self._navigable)
        if target is None:
            return True
        self.keys[target].grab_focus()
        stroke = self.stroke
        if stroke is not None and stroke.keys and stroke.live:
            self._reach(target)
        return True

    def end_key_stroke(self) -> None:
        """The release of a drag from the keyboard, if one is on; a drag from
        the pointer goes on."""
        if self.stroke is not None and self.stroke.keys:
            self._drop_stroke()

    def _on_key_released(self, _controller: Gtk.EventControllerKey, keyval: int, _code: int,
                         state: Gdk.ModifierType) -> None:
        # Shift's own release ends a drag from the keyboard, and so does any
        # key let go with no Shift in effect: a latched Shift (Sticky Keys)
        # came and went before its arrow, and the compositor clears the latch
        # before that arrow's release (mutter handle_stickykeys_release,
        # read 2026-09-30; not yet run on a real GNOME session).
        if (keyval in (Gdk.KEY_Shift_L, Gdk.KEY_Shift_R)
                or not state & Gdk.ModifierType.SHIFT_MASK):
            self.end_key_stroke()

    def clear_focused(self) -> bool:
        """What a right click on the focused key does, `on_clear`. A drag
        from the keyboard ends first, as at a plain arrow, and at Shift+F10
        with its Shift still down."""
        key = self.focused()
        if key is None:
            return False
        self.end_key_stroke()
        self._on_clear(key)
        return True

    def set_sampling(self, on: bool) -> None:
        """A crosshair over the keys while the Sampler waits for a key, and
        the usual pointer otherwise. Each key inherits the cursor of the
        Gtk.Fixed it sits in."""
        self.fixed.set_cursor(Gdk.Cursor.new_from_name("crosshair") if on else None)

    @staticmethod
    def _strip(button: Gtk.Button) -> None:
        for cls in button.get_css_classes():
            if cls.startswith(KEY_PREFIX) or cls in ("g915-mappable", "g915-dim"):
                button.remove_css_class(cls)
