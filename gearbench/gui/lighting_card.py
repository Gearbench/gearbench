"""The Keyboard tab's lighting rows: the mode with its speed and direction, the color or
brush, the saved colors, the background, the brightness, and the lock and idle settings.

The window holds the lighting these rows show (`Window.lighting`) and sends what they change.
This card owns the rows and their handlers; its page builds it, places its two groups with
the board between them, and draws the board.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import TYPE_CHECKING

from gi.repository import Adw, GObject, Gtk

from ..config import IDLE_DIM_AFTER, IDLE_OFF_AFTER, IDLE_RANGE, format_color
from ..keyboard import wave_directions
from ..keycodes import ZONES
from ..lighting import ANIMATIONS, COLORLESS, DEFAULT_PERIODS, SPEEDS
from ..profiles import PER_KEY, STATIC
from .color import ColorPicker
from .style import SWATCH_PREFIX, confirm, swatch_class, take_color_class
from .tips import explain_choices
from .worker import Reply, Settle, preview_reply

if TYPE_CHECKING:                       # pragma: no cover
    from .keyboard_page import SendSwitch
    from .window import Window
    from .worker import LoadingGuard

#: The Lighting mode list: one choice of what the board shows, the owner's
#: decision of 2026-09-24. An animation keeps the painted keys, hidden, as
#: Static does, and Per-key brings them back. The Mode row lists the animations
#: the attached keyboard has (LightingCard.modes).
LIGHTING_MODES = (STATIC, PER_KEY, *ANIMATIONS)
MODE_LABELS = ("Static", "Per-key", "Breathe", "Cycle", "Wave", "Ripple")
#: In LIGHTING_MODES' order.
MODE_TIPS = ("One color across the whole board.",
             "Color individual keys on the board below.",
             "The whole board fades in and out in one color.",
             "The whole board runs through every color together.",
             "Colors roll across the board.",
             "A ripple of one color spreads from each key you press.")
#: Wave's directions in the Direction row's order, each of lighting.WAVE_DIRECTIONS; the row
#: lists those the keyboard takes (LightingCard.directions). The labels say which way each
#: runs: Left to right, Right to left, Top to bottom and From the center out as the owner saw
#: them on the G915 on 2026-09-24, and all six as the owner saw them on a wired G515 TKL on
#: 2026-10-18.
DIRECTIONS = ("horizontal", "reverse-horizontal", "vertical", "reverse-vertical", "center-out",
              "center-in")
DIRECTION_LABELS = ("Left to right", "Right to left", "Top to bottom", "Bottom to top",
                    "From the center out", "From the edges in")
#: One period of each animation, for the Speed row's subtitle.
SPEED_UNITS = {"breathe": "breath", "cycle": "cycle", "wave": "wave", "ripple": "ripple"}
#: The Speed slider's step, in milliseconds.
SPEED_STEP = 100
#: The Speed slider's wait after it stops before it sends: a drag or a run of
#: arrow keys saves one change.
SPEED_SETTLE_MS = 300
#: The picker's subtitle in Static mode, the one the window opens in.
STATIC_PICKER_HELP = "Live on the keyboard as you drag"
#: The picker's subtitle in Per-key mode, and while Sample waits for its key; the board
#: row's title starts with the second then.
BRUSH_HELP = "Click keys on the board to paint them"
SAMPLE_HELP = "Click a key to take its color"
#: Under the Lights off when locked switch, unless the daemon says what it
#: lacks for it.
LOCK_HELP = "Also while the screen is blank and before a suspend"
#: Under the idle dimming rows, the owner's decision of 2026-09-23.
NEVER_DIM_HELP = "Stay lit at the Brightness level however long no key is pressed"
DIM_AFTER_HELP = "Seconds without a keypress before the keyboard dims to half"
OFF_AFTER_HELP = "Seconds after dimming before the lights go off"
#: The idle time rows' steps; the seconds they take are config.IDLE_RANGE.
IDLE_STEP, IDLE_PAGE = 10, 60
#: The time rows' wait after the last change before they send: a run of clicks
#: on + saves one change and writes the keyboard once.
IDLE_SETTLE_MS = 600


def speed_text(mode: str, ms: int) -> str:
    """The Speed row's subtitle: one period, in the unit the CLI's --speed takes."""
    return f"{ms} ms per {SPEED_UNITS[mode]}"


def mode_label(mode: str) -> str:
    """A mode's name as the Mode row lists it."""
    return MODE_LABELS[LIGHTING_MODES.index(mode)]


def snap_speed(mode: str, value: float) -> int:
    """A slider value as a speed `mode` takes: to the step, within its range."""
    fastest, slowest = SPEEDS[mode]
    # A half rounds up, not to even as round() does: from a speed off the step
    # (2150, which --speed takes) every arrow key lands on a half, and every
    # other one would snap where the last did and send nothing. int() is floor
    # here, as the slider never goes below 100.
    return min(slowest, max(fastest, int(value / SPEED_STEP + 0.5) * SPEED_STEP))


def picker_labels(mode: str, sampling: bool = False) -> tuple[str, str]:
    """The picker's title and subtitle. In Per-key mode it is the brush,
    which sends nothing as it moves (LightingCard._preview); while Sample is on, the next
    key clicked gives it its color.

    Under an animation it is the Static color that breathe and ripple run
    in, sent at the release: a preview is a per-key frame, which an
    animation would not show. Cycle and wave have no color of their own, and
    the grayed-out picker says so. The logo waves with the keys though its
    cluster has no wave (the owner saw it on 2026-09-24), and stays steady
    under ripple.
    """
    if mode == PER_KEY:
        if sampling:
            return "Brush", SAMPLE_HELP
        return "Brush", BRUSH_HELP
    if mode in COLORLESS:
        return "Color", f"{mode_label(mode)} runs through every color; none to pick"
    if mode == "breathe":
        return "Color", "Breathe's color, sent when you let go"
    if mode == "ripple":
        return "Color", "Ripple's color, sent when you let go. The logo shows it steady"
    return "Color", STATIC_PICKER_HELP


def background_hint(color: int) -> str:
    """The Background row's subtitle. In Per-key mode a mouse synced with
    the keyboard takes the background, so black leaves it dark."""
    if color == 0x000000:
        return "Black: unpainted keys and a synced mouse are dark. Use brush color to light them"
    return "Shown by unpainted keys and a synced mouse. Not the brush"


def zone_map(per_key: dict[str, int]) -> dict[str, str]:
    """The painted keys as the paint command wants them: the decimal zone id
    as a string, the color as #rrggbb. A name with no zone is dropped."""
    return {
        str(ZONES[name]): format_color(color)
        for name, color in per_key.items() if name in ZONES
    }


def paint_request(background: int, per_key: dict[str, int]) -> dict:
    """The paint command: the background as its base, then the painted keys over it."""
    return {"command": "paint", "base": format_color(background),
            "zones": zone_map(per_key)}


class LightingCard:
    """The Lighting group and the color rows under the board, and the commands they send.

    `loading` is the page's guard, held while a reply sets a row. `unanswered` is the page's
    count of writes not answered yet, by what they set, and `send_switch` its way to send a
    switch and read it back: Lights off when locked is one.
    The other three are the page's too, which holds the board: `on_defaults` refuses a
    change while the window holds changes back, and says so; `update_board` draws the board
    and has the card draw the picker's labels again (show_picker); `refresh_css` loads the
    rules for the colors in play and has the card draw the Background row in them
    (show_background).
    """

    #: The color a picker drag started from, kept from its first preview to its release (_commit).
    _drag_from: int | None = None
    #: The layer the picker last showed the color of (show_mode).
    _color_for: tuple[int | None, str | None] | None = None
    #: The saved colors the row shows, in its order.
    _saved_shown: tuple[int, ...] = ()

    def __init__(self, window: Window, loading: LoadingGuard, unanswered: Counter[str],
                 send_switch: SendSwitch, on_defaults: Callable[[], bool],
                 update_board: Callable[[], None], refresh_css: Callable[[], None]) -> None:
        self.win = window
        self._loading = loading
        self._unanswered = unanswered
        self._send_switch = send_switch
        self._on_defaults = on_defaults
        self._update_board = update_board
        self._refresh_css = refresh_css
        #: The Speed slider's send once it stops moving (SPEED_SETTLE_MS), and the
        #: animation the move still waiting was made on.
        self._speed = Settle(self._send_speed)
        self._speed_mode: str | None = None
        #: The idle time rows' send once they stop changing (IDLE_SETTLE_MS).
        self._idle = Settle(self._send_idle_times)
        #: What the Brightness row lists, so its model is rebuilt only when it changes.
        self._brightness_labels: list[str] = []
        #: The last refused preview, so a drag reports it once.
        self._preview_error: str | None = None
        #: Until a keyboard answers, every section is on screen. A keyboard
        #: that cannot paint individual keys clears this, and the Mode, Speed,
        #: Direction, Background and Groups rows go with it.
        self.per_key = True
        #: What the Mode row lists, in LIGHTING_MODES' order, and each entry's tip (show_mode).
        self.modes: tuple[str, ...] = LIGHTING_MODES
        self._mode_tips = list(MODE_TIPS)
        #: What the Direction row lists, in DIRECTIONS' order (show_effect_rows).
        self.directions: tuple[str, ...] = DIRECTIONS

    def build_lighting(self) -> Adw.PreferencesGroup:
        """The Mode row, the Speed row under an animation and the Direction
        row under wave. The board follows, then the color rows (`build_colors`)."""
        group = Adw.PreferencesGroup(
            title="Lighting",
            description="Holds only while the daemon runs.")
        self.mode_row = Adw.ComboRow(title="Mode", model=Gtk.StringList.new(MODE_LABELS))
        explain_choices(self.mode_row, self._mode_tips)
        self.mode_row.connect("notify::selected", self.on_mode)
        group.add(self.mode_row)

        # An animation's speed as its period, which G HUB's slider sets and `--speed`
        # takes, faster to the right as G HUB draws it (the owner's decision of
        # 2026-09-24). No origin: a fill from the fast end reads as a level, not a speed.
        self.speed_scale = Gtk.Scale(
            orientation=Gtk.Orientation.HORIZONTAL, inverted=True, draw_value=False,
            has_origin=False, hexpand=True, valign=Gtk.Align.CENTER,
            adjustment=Gtk.Adjustment(lower=1000, upper=20000, value=3000,
                                      step_increment=SPEED_STEP, page_increment=1000))
        self.speed_scale.set_size_request(220, -1)
        self.speed_scale.update_property([Gtk.AccessibleProperty.LABEL], ["Speed"])
        self.speed_scale.connect("value-changed", self.on_speed)
        speed = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
        speed.append(Gtk.Label(label="Slower", css_classes=["dimmed"]))
        speed.append(self.speed_scale)
        speed.append(Gtk.Label(label="Faster", css_classes=["dimmed"]))
        self.speed_row = Adw.ActionRow(title="Speed", visible=False)
        self.speed_row.add_suffix(speed)
        group.add(self.speed_row)

        self.direction_row = Adw.ComboRow(title="Direction", visible=False,
                                          model=Gtk.StringList.new(DIRECTION_LABELS))
        self.direction_row.connect("notify::selected", self.on_direction)
        group.add(self.direction_row)
        return group

    def build_colors(self, sample: Gtk.Widget, groups: Gtk.Widget) -> Adw.PreferencesGroup:
        """The picker, the saved colors, the background, the brightness,
        Lights off when locked and the idle dimming, under the board. No
        title: they are the Lighting group's, and the board sits between.
        `sample` and `groups` are the board's two tools, which the page builds:
        the Sample button goes on the picker's row and the Groups row under it."""
        group = Adw.PreferencesGroup()
        self.picker = ColorPicker(self._preview, self._commit, self.win.toast,
                                  subtitle=STATIC_PICKER_HELP,
                                  color=self.win.lighting.color)
        color_row = self.picker.row
        color_row.add_suffix(sample)

        saved_row = Adw.ActionRow(
            title="Saved colors",
            subtitle="Click to apply, right-click to remove; + saves the current color")
        self.saved_box = Gtk.Box(spacing=4, valign=Gtk.Align.CENTER)
        self.add_color = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER,
                                    tooltip_text="Save the current color")
        self.add_color.connect("clicked", self.on_save_color)
        saved_row.add_suffix(self.saved_box)
        saved_row.add_suffix(self.add_color)
        color_row.add_row(saved_row)
        group.add(color_row)
        group.add(groups)

        # Only meaningful in per-key mode, where unpainted keys show it. An image to
        # assistive technology, named for its color in show_background: the CSS fill is
        # not in the accessibility tree.
        self.bg_swatch = Gtk.Box(valign=Gtk.Align.CENTER, css_classes=["g915-swatch"],
                                 accessible_role=Gtk.AccessibleRole.IMG)
        self.bg_swatch.set_size_request(28, 28)
        self.bg_row = Adw.ActionRow(title="Background")
        use = Gtk.Button(label="Use brush color", valign=Gtk.Align.CENTER)
        use.connect("clicked", lambda _b: self._set_background(self.win.lighting.color))
        black = Gtk.Button(label="Black", valign=Gtk.Align.CENTER)
        black.connect("clicked", lambda _b: self._set_background(0x000000))
        self.bg_row.add_suffix(self.bg_swatch)
        self.bg_row.add_suffix(black)
        self.bg_row.add_suffix(use)
        group.add(self.bg_row)

        self.brightness_row = Adw.ComboRow(
            title="Brightness", subtitle="The firmware stores only these levels")
        self.brightness_row.connect("notify::selected", self.on_brightness)
        group.add(self.brightness_row)
        # Device-wide like the brightness, so under it (the owner's decision of
        # 2026-09-23). Shown from the first status. Not markup: the subtitle can
        # be the daemon's words.
        self.lock_row = Adw.SwitchRow(title="Lights off when locked", subtitle=LOCK_HELP,
                                      use_markup=False, visible=False)
        self.lock_row.connect("notify::active", self.on_lock_lights)
        group.add(self.lock_row)
        # Idle dimming, device-wide like the lock's switch and under it (the owner's
        # decision of 2026-09-23). Shown once the daemon has the settings.
        self.never_dim_row = Adw.SwitchRow(title="Never dim", subtitle=NEVER_DIM_HELP,
                                           visible=False)
        self.never_dim_row.connect("notify::active", self.on_never_dim)
        group.add(self.never_dim_row)
        low, high = IDLE_RANGE
        self.dim_after_row = Adw.SpinRow(
            title="Dim after", subtitle=DIM_AFTER_HELP, visible=False,
            adjustment=Gtk.Adjustment(lower=low, upper=high, value=IDLE_DIM_AFTER,
                                      step_increment=IDLE_STEP, page_increment=IDLE_PAGE))
        self.off_after_row = Adw.SpinRow(
            title="Lights off after", subtitle=OFF_AFTER_HELP, visible=False,
            adjustment=Gtk.Adjustment(lower=low, upper=high, value=IDLE_OFF_AFTER,
                                      step_increment=IDLE_STEP, page_increment=IDLE_PAGE))
        for row in (self.dim_after_row, self.off_after_row):
            row.connect("notify::value", self.on_idle_time)
            group.add(row)
        return group

    def refresh_brightness(self) -> None:
        win = self.win
        labels = [f"{p}%" for p in win.levels]
        # A level between the firmware's ascending ones, saved while the
        # keyboard was away, runs as the one below it (Lighting.set_brightness).
        wanted = max((i for i, p in enumerate(win.levels) if p <= win.brightness), default=0)
        if labels != self._brightness_labels:
            self._brightness_labels = labels
            self._loading.put_model(self.brightness_row, Gtk.StringList.new(labels))
        elif self._unanswered["brightness"]:
            return              # a level not answered yet holds the row (_unanswered)
        self._loading.put_selected(self.brightness_row, wanted)

    def show_lock(self) -> None:
        """The Lights off when locked switch as the daemon has it, and what the
        desktop lacks for it, if anything."""
        win = self.win
        self.lock_row.set_visible(True)
        if not self._unanswered["lock_lights"]:
            self._loading.put_active(self.lock_row, bool(win.lock_lights))
        self.lock_row.set_subtitle(win.lock_unavailable or LOCK_HELP)

    def on_lock_lights(self, row: Adw.SwitchRow, _p: GObject.ParamSpec) -> None:
        self._send_switch(row, command="lock_lights", key="lights_off_when_locked")

    def show_idle(self) -> None:
        """The idle rows as the daemon has them, hidden until a status gives
        them, the times grayed out while the Never dim switch is on. A time
        waiting to be sent or unanswered, and Never dim while its write is
        unanswered, are left as they are: a status read in between would put
        the old one back under the cursor."""
        win = self.win
        for row in (self.never_dim_row, self.dim_after_row, self.off_after_row):
            row.set_visible(win.idle is not None)
        if win.idle is None:
            return
        dim_after, off_after, never = win.idle
        if not self._idle.pending and not self._unanswered["idle_times"]:
            self._loading.put_value(self.dim_after_row, dim_after)
            self._loading.put_value(self.off_after_row, off_after)
        if not self._unanswered["never_dim"]:
            self._loading.put_active(self.never_dim_row, never)
        for row in (self.dim_after_row, self.off_after_row):
            row.set_sensitive(not self.never_dim_row.get_active())

    def on_never_dim(self, row: Adw.SwitchRow, _p: GObject.ParamSpec) -> None:
        """Send the switch, and read it back either way: a refusal puts it back."""
        if self._loading.active or self.win.idle is None:
            return
        wanted = row.get_active()
        if wanted == self.win.idle[2] and not self._unanswered["never_dim"]:
            return
        self._unanswered["never_dim"] += 1

        def done(reply: dict | None) -> None:
            self._unanswered["never_dim"] -= 1
            if reply is None:
                self.show_idle()
            elif self.win.idle is not None and isinstance(reply.get("never_dim"), bool):
                self.win.idle = (*self.win.idle[:2], reply["never_dim"])
            self.win.resync()
        self.win.call({"command": "idle", "never_dim": wanted}, done)

    def on_idle_time(self, _row: Adw.SpinRow, _p: GObject.ParamSpec) -> None:
        """A time changed: send both once they settle (IDLE_SETTLE_MS)."""
        if self._loading.active or self.win.idle is None:
            return
        self._idle.restart(IDLE_SETTLE_MS)

    def _send_idle_times(self) -> None:
        """Send the two times, unless they are what the daemon has and no
        send of them is unanswered. A refusal puts the rows back."""
        if self.win.idle is None:
            return
        # round(), not int(): the row shows a typed 15.7 as 16, rounding half to even
        # as round() does.
        times = round(self.dim_after_row.get_value()), round(self.off_after_row.get_value())
        if times == self.win.idle[:2] and not self._unanswered["idle_times"]:
            return
        self._unanswered["idle_times"] += 1

        def done(reply: dict | None) -> None:
            self._unanswered["idle_times"] -= 1
            if reply is None:
                self.show_idle()
            elif self.win.idle is not None:
                self.win.idle = (reply["idle_dim_after"], reply["idle_off_after"],
                                 self.win.idle[2])
            self.win.resync()
        self.win.call({"command": "idle", "dim_after": times[0], "off_after": times[1]}, done)

    def rebuild_saved(self) -> None:
        """A button per saved color, made anew at every read of the files. The
        keyboard focus stays on the color it was on, or at its place in the row
        once that color is gone, or goes to + with none left."""
        buttons = self._saved_buttons()
        focus = self.win.get_focus()
        was = next((i for i, b in enumerate(buttons) if b is focus), None)
        focused = self._saved_shown[was] if was is not None else None
        for button in buttons:
            self.saved_box.remove(button)
        self._saved_shown = tuple(self.win.saved_colors)
        for color in self.win.saved_colors:
            area = Gtk.Box(css_classes=["g915-saved", swatch_class(color)])
            area.set_size_request(22, 22)
            text = format_color(color)
            button = Gtk.Button(child=area, valign=Gtk.Align.CENTER,
                                tooltip_text=f"{text}: right-click or Delete to remove")
            button.update_property([Gtk.AccessibleProperty.LABEL], [f"Saved color {text}"])
            button.connect("clicked", lambda _b, c=color: self._use_saved(c))
            remove = Gtk.GestureClick(button=3)
            remove.connect("pressed", lambda *a, c=color: self._forget_color(c))
            button.add_controller(remove)
            # The right click from the keyboard, as on the board's keys.
            def forget(*_args: object, c: int = color) -> bool:
                self._forget_color(c)
                return True

            keys = Gtk.ShortcutController()
            keys.add_shortcut(Gtk.Shortcut.new(
                Gtk.ShortcutTrigger.parse_string("Delete|KP_Delete|BackSpace|<Shift>F10|Menu"),
                Gtk.CallbackAction.new(forget)))
            button.add_controller(keys)
            self.saved_box.append(button)
        if was is not None:
            buttons = self._saved_buttons()
            colors = self.win.saved_colors
            if focused in colors:
                buttons[colors.index(focused)].grab_focus()
            elif buttons:
                buttons[min(was, len(buttons) - 1)].grab_focus()
            else:
                self.add_color.grab_focus()
        self._refresh_css()

    def _saved_buttons(self) -> list[Gtk.Widget]:
        return list(self.saved_box)

    def flush_speed(self) -> None:
        """Send a Speed move still settling, now: it goes before a change that switches the
        lighting or the layer, on the layer it was made on."""
        self._speed.flush()

    def close(self) -> None:
        """Stop the waits, the picker's too, and take its rules off the display."""
        self._speed.cancel()
        self._idle.cancel()
        self.picker.destroy()

    def flush(self) -> bool:
        """Send the speed, the idle times and a picker key run still
        settling, now, for the window's close. True when one was."""
        return any([self._speed.flush(), self._idle.flush(), self.picker.flush()])

    def on_mode(self, row: Adw.ComboRow, _p: GObject.ParamSpec) -> None:
        if self._loading.active:
            return
        if self._on_defaults():
            with self._loading:
                row.set_selected(self.modes.index(self.win.lighting.mode))
            return
        win = self.win
        index = row.get_selected()
        mode = self.modes[index] if 0 <= index < len(self.modes) else STATIC
        # A speed still settling goes first, for the animation it was set on.
        self._speed.flush()
        if mode != PER_KEY and win.lighting.color != win.lighting.static_color:
            # The stored Static color comes back, and the picker goes to it (the
            # owner's decision of 2026-09-23): `color` is the brush, or the black
            # adopt_lighting draws for a disabled layer, and sending it would save
            # it over the Static color. An animation runs in the Static color too.
            # The brush is not kept: Per-key starts from what the picker shows.
            # set_color calls no handler.
            win.lighting.color = win.lighting.static_color
            self.picker.set_color(win.lighting.color)
        win.lighting.mode = mode
        if mode in ANIMATIONS:
            # The effect alone: the daemon keeps the speed and the direction
            # the profile saved, and the painted keys, hidden, for Per-key.
            win.send_lighting({"command": "effect", "value": mode})
        else:
            self.push_lighting()
        self.show_effect_rows()
        self._draw_lighting()

    def show_capabilities(self, able: set[str]) -> None:
        """The rows this keyboard has, from its status's `capabilities`: Mode and the rows
        under it only on a keyboard that paints single keys, Brightness only on one that
        has it."""
        self.per_key = "per_key" in able
        self.show_effect_rows()
        # Zone lighting has one color, so there is no mode to choose.
        self.mode_row.set_visible(self.per_key)
        self.brightness_row.set_visible("brightness" in able)

    def show_mode(self) -> None:
        """The Mode row, the rows under it and the picker's color as the window's lighting
        has them. It runs under the window's loading guard (KeyboardPage.sync), so the
        rows' handlers send nothing."""
        win = self.win
        self._list_modes(win.lighting.mode)
        self.mode_row.set_selected(self.modes.index(win.lighting.mode))
        self.show_effect_rows()
        # Its release commits the color it shows, as on the Mouse tab. A color being
        # typed in the Hex row stays, as there, but a layer switch drops it, as it
        # drops a name being typed (KeyboardPage.refresh_profile_row).
        picker = self.picker
        if not picker.dragging and (win.layer != self._color_for or not picker.typing(win)):
            picker.set_color(win.lighting.color)
            self._color_for = win.layer

    def _list_modes(self, mode: str) -> None:
        """The Mode row's list: Static, Per-key and the animations the attached keyboard has
        (Window.animations), and `mode`, the one shown, if it is none of those. That is the
        lighting the window read before a status changed the keyboard's animations, until the
        files are read again (Window._moved)."""
        offered = (STATIC, PER_KEY, *self.win.animations, mode)
        modes = tuple(m for m in LIGHTING_MODES if m in offered)
        if modes == self.modes:
            return
        self.modes = modes
        # explain_choices reads its tips when an entry is drawn.
        self._mode_tips[:] = [MODE_TIPS[LIGHTING_MODES.index(m)] for m in modes]
        self.mode_row.set_model(Gtk.StringList.new([mode_label(m) for m in modes]))

    def show_picker(self, sampling: bool) -> None:
        """The picker's title and subtitle for the mode, with Sample on or off
        (picker_labels)."""
        mode = self.win.lighting.mode
        title, subtitle = picker_labels(mode, sampling)
        self.picker.row.set_title(title)
        self.picker.row.set_subtitle(subtitle)
        # Grayed out, the reason in the subtitle, while an animation with no
        # color of its own (lighting.COLORLESS: cycle, wave) runs.
        self.picker.row.set_sensitive(mode not in COLORLESS)

    def show_brush(self) -> None:
        """The picker in the window's color, which the board's Sample gave the brush. It
        sends nothing: set_color calls no handler."""
        self.picker.set_color(self.win.lighting.color)

    def show_effect_rows(self) -> None:
        """The Background row in Per-key mode, the Speed row under an animation and the
        Direction row under wave, showing what the profile saved; each hidden otherwise,
        and on a keyboard whose Mode row is hidden. A speed waiting to be sent is left as
        it is, as show_idle leaves a time."""
        win = self.win
        mode = win.lighting.mode
        self.bg_row.set_visible(self.per_key and mode == PER_KEY)
        animated = self.per_key and mode in ANIMATIONS
        self.speed_row.set_visible(animated)
        self.direction_row.set_visible(animated and mode == "wave")
        if not animated:
            return
        ms = win.lighting.speeds.get(mode, DEFAULT_PERIODS[mode])
        settling = self._speed.pending and self._speed_mode == mode
        if not settling:
            low, high = SPEEDS[mode]
            shown = self.speed_scale.get_adjustment()
            # A new range can move the value, which calls on_speed.
            if (shown.get_lower(), shown.get_upper(), self.speed_scale.get_value()) \
                    != (low, high, ms):
                with self._loading:
                    self.speed_scale.set_range(low, high)
                    # A Page key or a wheel notch moves a page: 1000 ms would
                    # cross ripple's whole range in one, so there it is a step.
                    shown.set_page_increment(1000 if high - low > 1000 else SPEED_STEP)
                    self.speed_scale.set_value(ms)
            self._say_speed(speed_text(mode, ms))
        if mode == "wave":
            self._list_directions(win.lighting.direction)
            self._loading.put_selected(self.direction_row,
                                       self.directions.index(win.lighting.direction))

    def _list_directions(self, direction: str) -> None:
        """The Direction row's list: the directions the keyboard model the lighting shows on
        takes (keyboard.wave_directions over Window.shown_on), and `direction`, the one shown,
        if it is none of those: one its own entry in a file holds, which the daemon sends as
        it is."""
        offered = (*wave_directions(self.win.shown_on.model), direction)
        directions = tuple(d for d in DIRECTIONS if d in offered)
        if directions == self.directions:
            return
        self.directions = directions
        self._loading.put_model(self.direction_row, Gtk.StringList.new(
            [DIRECTION_LABELS[DIRECTIONS.index(d)] for d in directions]))

    def on_speed(self, scale: Gtk.Scale) -> None:
        """The slider moved: say the speed now, and send it once it settles."""
        mode = self.win.lighting.mode
        if self._loading.active or mode not in ANIMATIONS:
            return
        self._say_speed(speed_text(mode, snap_speed(mode, scale.get_value())))
        self._speed_mode = mode
        self._speed.restart(SPEED_SETTLE_MS)

    def _say_speed(self, text: str) -> None:
        """The Speed row's subtitle, and the slider's value in the same words for a
        screen reader: the number alone is milliseconds, higher for slower."""
        self.speed_row.set_subtitle(text)
        self.speed_scale.update_property([Gtk.AccessibleProperty.VALUE_TEXT], [text])

    @property
    def speed_settling(self) -> bool:
        """A Speed slider move is waiting to be sent, on the layer shown."""
        return self._speed.pending

    def _send_speed(self) -> None:
        """Send the slider's speed, saved for its animation."""
        mode = self.win.lighting.mode
        if mode not in ANIMATIONS:
            return
        if self._on_defaults():
            self.show_effect_rows()             # put the slider back
            return
        ms = snap_speed(mode, self.speed_scale.get_value())
        if ms == self.win.lighting.speeds.get(mode):
            return
        self.win.lighting.speeds[mode] = ms
        self.win.send_lighting({"command": "effect", "value": mode, "speed": ms})

    def on_direction(self, row: Adw.ComboRow, _p: GObject.ParamSpec) -> None:
        """A direction for wave, sent and saved at once."""
        if self._loading.active or self.win.lighting.mode != "wave":
            return
        index = row.get_selected()
        if not 0 <= index < len(self.directions) \
                or self.directions[index] == self.win.lighting.direction:
            return
        if self._on_defaults():
            with self._loading:
                row.set_selected(self.directions.index(self.win.lighting.direction))
            return
        self.win.lighting.direction = self.directions[index]
        self.win.send_lighting({"command": "effect", "value": "wave",
                                "direction": self.win.lighting.direction})

    def show_background(self) -> None:
        """The Background row in the window's background: the swatch's color rule and its
        name for a screen reader, and the subtitle. The rule loads with the page's style
        sheet (refresh_css), which holds every color in play."""
        background = self.win.lighting.background
        take_color_class(self.bg_swatch, SWATCH_PREFIX, swatch_class(background))
        self.bg_swatch.update_property([Gtk.AccessibleProperty.LABEL],
                                       [f"Background color {format_color(background)}"])
        self.bg_row.set_subtitle(background_hint(background))

    def _set_background(self, color: int) -> None:
        if self._on_defaults():
            return
        self.win.lighting.background = color
        self.push_lighting()
        self._draw_lighting()

    def on_brightness(self, row: Adw.ComboRow, _p: GObject.ParamSpec) -> None:
        if self._loading.active:
            return
        level = self.win.levels[row.get_selected()]
        self._unanswered["brightness"] += 1

        def done(reply: dict | None) -> None:
            self._unanswered["brightness"] -= 1
            if reply is not None:
                self.win.brightness = reply["applied"]
            # After a refusal, this puts the row back once no other level is out.
            self.refresh_brightness()
        self.win.call({"command": "brightness", "value": level}, done)

    def on_save_color(self, _b: Gtk.Button | None) -> None:
        color = self.win.lighting.color
        if color in self.win.saved_colors:
            self.win.toast("Already saved")
            return
        self.win.call({"command": "save_color", "value": format_color(color)},
                      lambda reply: self.win.took_saved(
                          reply, f"Saved {format_color(color)}"))

    def _use_saved(self, color: int) -> None:
        self.picker.set_color(color)
        self._commit(color)

    def _forget_color(self, color: int) -> None:
        """Ask before the daemon forgets a saved color: a tuned color lost to a
        mis-click is annoying to redo."""
        text = format_color(color)
        confirm(self.win, f"Remove {text}?",
                "It leaves the saved list; the keyboard's lighting does not change.", "Remove",
                lambda: self.win.call({"command": "forget_color", "value": text},
                                      self.win.took_saved))

    def _preview(self, color: int) -> None:
        """A color picked but not yet released.

        Per-key mode previews nothing: the brush is not on the board until a
        key is clicked, and a paint request carries only the background and
        the painted keys, so the daemon would repaint the board unchanged at
        the drag's rate.

        Nothing is sent or said while a change is held (Window.held): the
        release says it once, or in Per-key mode takes the brush, which
        nothing holds (_commit).
        """
        if self.win.held() is not None:
            return
        if self._drag_from is None:
            # What a held release puts back outside Per-key: a read that
            # fails mid-drag holds the release after this preview moved the
            # color.
            self._drag_from = self.win.lighting.color
        self.win.lighting.color = color
        if self.win.lighting.mode == PER_KEY:
            return
        self._draw_lighting()
        # Repaint only, no commit; the worker coalesces these during a drag.
        self.win.preview({"command": "preview", "value": format_color(color)},
                         self.on_preview)

    def _commit(self, color: int) -> None:
        """The picker's release, or a saved color or a hex applied. Held, it
        saves nothing and puts back the color a drag started from.

        In Per-key mode it is the brush, which sends nothing, held or not: it
        goes out only with the keys it paints, as a drag's previews do
        (_preview). A paint of the unchanged board would be saved, and over a
        disabled M profile, drawn black, would save the board lit on black in
        place of the stored background.
        """
        start, self._drag_from = self._drag_from, None
        if self.win.lighting.mode == PER_KEY:
            self.win.lighting.color = color
            return
        if self._on_defaults():
            if start is not None and start != self.win.lighting.color:
                self.win.lighting.color = start
                self._draw_lighting()
                # A preview saves nothing: the keyboard and a synced mouse
                # go back to what they showed before the drag.
                self.win.preview({"command": "preview", "value": format_color(start)},
                                 self.on_preview, while_held=True)
            self.picker.set_color(self.win.lighting.color)       # put the picker back
            return
        self.win.lighting.color = color
        # The Static color from now on, for the next switch back from
        # Per-key; a drag's previews are not, as they are not saved. Breathe
        # and ripple run in it.
        self.win.lighting.static_color = color
        self.push_lighting()
        self._draw_lighting()

    def _draw_lighting(self) -> None:
        """Draw the board, and a synced mouse's color, in the window's lighting."""
        self._update_board()
        self.win.mouse.follow_keyboard()

    def on_preview(self, reply: Reply) -> None:
        self._preview_error = preview_reply(reply, self._preview_error,
                                            self.win.set_status, self.win.refused)

    def push_lighting(self) -> None:
        """Send what the window shows; a refusal is taken back and read back
        (Window.send_lighting)."""
        state = self.win.lighting
        if state.mode == PER_KEY:
            self.win.send_lighting(paint_request(state.background, state.per_key))
        elif state.mode in ANIMATIONS:
            # The Static color, which the animation runs in; with no
            # "mode" the daemon keeps the effect.
            self.win.send_lighting({"command": "color", "value": format_color(state.color)})
        else:
            # "mode" says this is the Static choice, one color for the whole
            # board: the daemon stops showing a stored per-key layout, and
            # any animation.
            self.win.send_lighting({"command": "color", "value": format_color(state.color),
                                    "mode": STATIC})
