"""The Keyboard tab's lighting rows: the keyboard that leads and the follow switch, the mode
with its speed and direction, the color or brush and the second color, the saved colors, the
background, the brightness, and the lock and idle settings.

The window holds the lighting these rows show (`Window.lighting`) and sends what they change.
This card owns the rows, their handlers and the idle dimming a status gave (`take_idle`); its
page builds it, places its two groups with the board between them, and draws the board.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from gi.repository import Adw, GObject, Gtk

from ..config import IDLE_DIM_AFTER, IDLE_OFF_AFTER, IDLE_RANGE, PER_KEY, STATIC, format_color
from ..keyboard import key_name, model_named, shown_brightness, wave_directions
from ..keycodes import by_zone
from ..lighting import (
    ANIMATIONS,
    COLORLESS,
    DEFAULT_PERIODS,
    DIRECTION_WORDS,
    OFF,
    SIGNATURES,
    SPEEDS,
    TWO_COLORED,
    nearest_speed,
)
from ..wording import capitalized
from .animation import logo_steady
from .color import RELEASE_HELP, ColorPicker, SavedColors
from .style import (
    COLORLESS_PICKER_HELP,
    EFFECT,
    EFFECT_TIPS,
    FOLLOW,
    LIGHTING_HOLDS,
    OFF_PICKER_HELP,
    SWATCH_PREFIX,
    SWITCHED_OFF,
    effect_label,
    say_not_shown,
    swatch_class,
    take_color_class,
)
from .tips import explain_choices, gray_out_choices
from .worker import (
    SETTLE_MS,
    SPIN_SETTLE_MS,
    Reply,
    Settle,
    preview_reply,
    send_switch,
    take_typed,
)

if TYPE_CHECKING:                       # pragma: no cover
    from .window import Window
    from .worker import LoadingGuard

#: The Lighting group's first row while two keyboards or more are plugged in: which leads, the
#: one each other keyboard with its follow switch on and the mice follow (the owner's decision
#: of 2026-10-21, "B: separate pages, follow switch": "the leader is a row").
LEADER = "Leader"
#: The line under the follow switch, with the leader's name, in `keyboard-follow`'s words:
#: following takes the leader's effect, colors and brightness (show_following).
FOLLOW_HELP = "Shows the {}'s lighting at its brightness"
#: Said at a pick in the Leader row that the daemon saves without making it the leader, since
#: it has not set that keyboard up, as a G915 TKL on its own profile (one switched off is out
#: of reach in the row): it leads once the daemon sets it up (PerKeyboard.leader), and the row
#: stays on it meanwhile (show_leader). The sentence names who sets it up, so it asks nothing of
#: the user.
LEADS_ONCE_SET_UP = "Saved: the {} leads once Gearbench sets it up"
#: The Effect row's list: one choice of what the board shows, the owner's
#: decision of 2026-09-24. An animation keeps the painted keys, hidden, as
#: Static does, and Per-key brings them back. The row lists the animations
#: the attached keyboard has (LightingCard.modes). Off, the disabled effect, comes last where
#: the keyboard reports it, an entry the owner's decision of 2026-10-27 added: it puts the
#: layer on Static too, so the painted keys go dark with the rest (LightingCard.on_mode).
LIGHTING_MODES = (STATIC, PER_KEY, *ANIMATIONS, OFF)
#: In LIGHTING_MODES' order: Per-key's name is the mode's own, and the others are the
#: effects' names (effect_label).
MODE_LABELS = tuple("Per-key" if mode == PER_KEY else effect_label(mode)
                    for mode in LIGHTING_MODES)
#: The tip of each of lighting.SIGNATURES, whose look no source describes, and the picker's
#: subtitle under one: "Remind me of's color" would not read.
SIGNATURE_TIP = "One of the keyboard's own animations, in one color over the whole board."
SIGNATURE_PICKER_HELP = "The animation's color, sent when you let go"
#: Ripple's picker subtitle where the board has a logo that stays steady under it
#: (animation.logo_steady).
RIPPLE_STEADY_HELP = "Ripple's color, sent when you let go. The logo shows it steady"
#: Each mode's tip by its name, so a reorder of lighting.ANIMATIONS keeps each on its mode
#: and an animation added there with none here fails at import.
_TIPS = {STATIC: "One color across the whole board.",
         PER_KEY: "Paint individual keys on the board below.",
         "breathe": "The whole board fades in and out in one color.",
         "cycle": "The whole board runs through every color together.",
         "wave": "Colors roll across the board.",
         "ripple": "A ripple of one color spreads from each key you press.",
         "pulsar-point": "The keyboard's own animation in two colors over the whole board.",
         **dict.fromkeys(SIGNATURES, SIGNATURE_TIP),
         OFF: EFFECT_TIPS[OFF]}
#: In LIGHTING_MODES' order.
MODE_TIPS = tuple(_TIPS[mode] for mode in LIGHTING_MODES)
#: Wave's directions in the Direction row's order, and their labels (lighting.DIRECTION_WORDS);
#: the row lists those the keyboard takes (LightingCard.directions).
DIRECTIONS = tuple(DIRECTION_WORDS)
DIRECTION_LABELS = tuple(DIRECTION_WORDS.values())
#: Per-key's name and tip on a board lit by areas, in MODE_LABELS' and MODE_TIPS' place: a
#: click paints a whole area.
AREA_LABEL = "Per-area"
AREA_TIP = "Paint the areas of the board below."
#: One period of each animation, for the Speed row's subtitle.
SPEED_UNITS = {"breathe": "breath", "cycle": "cycle", "wave": "wave", "ripple": "ripple"}
#: The picker of an animation's second color, Pulsar Point's, and its subtitle, worded as the
#: First color's (picker_labels).
SECOND_COLOR = "Second color"
SECOND_PICKER_HELP = "{effect}'s second color, sent when you let go"
#: The Speed slider's step, in milliseconds.
SPEED_STEP = 100
#: The picker's subtitle in Static mode, the one the window opens in; RELEASE_HELP on a
#: keyboard that takes no per-key frame, as 0x8070's zones do, where a drag shows at the
#: release (KeyboardCommands.preview_board).
STATIC_PICKER_HELP = "Live on the keyboard as you drag"
#: The picker's subtitle in Per-key mode, on a board lit by areas, and while Sample waits
#: for its key; the board row's title starts with the last then.
BRUSH_HELP = "Click keys on the board to paint them"
AREA_BRUSH_HELP = "Click keys on the board to paint their areas"
SAMPLE_HELP = "Click a key to take its color"
#: Under the Lights off when locked switch, unless the daemon says what it
#: lacks for it. The blank is GNOME's alone (desktop.SCREENSAVER).
LOCK_HELP = "Also while GNOME blanks the screen and before a suspend"
#: Under the idle dimming rows, the owner's decision of 2026-09-23.
NEVER_DIM_HELP = "Stay lit at the Brightness level however long no key is pressed"
DIM_AFTER_HELP = "Seconds without a keypress before the keyboard dims to half"
#: The switch under Never dim, never_dim_with_game_profile (the owner's decision 13 of
#: 2026-10-28): the title alone, as the G915's Keyboard tab words a row.
NEVER_DIM_WITH_GAME_PROFILE = "Never dim with a game profile"
OFF_AFTER_HELP = "Seconds after dimming before the lights go off"
#: The toast's line for the idle times' note when the note is too long for one: the times are
#: saved, and the keyboard did not take them, is away or has none (Idle.write_idle_times).
IDLE_TIMES_LINE = "The keyboard did not get the new times"
#: The idle time rows' steps; the seconds they take are config.IDLE_RANGE.
IDLE_STEP, IDLE_PAGE = 10, 60


def speed_text(mode: str, ms: int) -> str:
    """The Speed row's subtitle: one period, in the unit the CLI's --speed takes."""
    return f"{ms} ms per {SPEED_UNITS[mode]}"


def mode_label(mode: str) -> str:
    """A mode's name as the Effect row lists it on a board lit key by key; on one lit by
    areas Per-key's is AREA_LABEL (LightingCard._list_modes)."""
    return MODE_LABELS[LIGHTING_MODES.index(mode)]


def snap_speed(mode: str, value: float) -> int:
    """A slider value as a speed `mode` takes: to the step, within its range."""
    # A half rounds up, not to even as round() does: from a speed off the step
    # (2150, which --speed takes) every arrow key lands on a half, and every
    # other one would snap where the last did and send nothing. int() is floor
    # here, as the slider never goes below 100.
    return nearest_speed(mode, int(value / SPEED_STEP + 0.5) * SPEED_STEP)


def picker_labels(mode: str, sampling: bool, areas: bool, frames: bool,
                  steady_logo: bool) -> tuple[str, str]:
    """The picker's title and subtitle. In Per-key mode it is the brush,
    which sends nothing as it moves (LightingCard._preview), and paints a key's whole area
    with `areas`; while Sample is on, the next key clicked gives it its color. In Static mode
    it is live on a keyboard that takes a per-key frame (`frames`) and sent at the release on
    one that does not.

    Under an animation it is the Static color that breathe, ripple and the
    G512 X's three more (lighting.SIGNATURES) run in, and Pulsar Point as
    the first of its two, sent at the release: a
    preview is a per-key frame, which an animation would not show. Cycle and
    wave have no color of their own, and the grayed-out picker says so, as it does under Off.
    Under ripple the subtitle says the logo stays steady where `steady_logo` is set
    (animation.logo_steady).
    """
    if mode == PER_KEY:
        if sampling:
            return "Brush", SAMPLE_HELP
        return "Brush", AREA_BRUSH_HELP if areas else BRUSH_HELP
    if mode == OFF:
        return "Color", OFF_PICKER_HELP
    if mode in COLORLESS:
        return "Color", COLORLESS_PICKER_HELP.format(mode_label(mode))
    if mode in TWO_COLORED:
        return "First color", f"{mode_label(mode)}'s first color, sent when you let go"
    if steady_logo:
        return "Color", RIPPLE_STEADY_HELP
    if mode in ("breathe", "ripple"):
        return "Color", f"{mode_label(mode)}'s color, sent when you let go"
    if mode in SIGNATURES:
        return "Color", SIGNATURE_PICKER_HELP
    return "Color", STATIC_PICKER_HELP if frames else RELEASE_HELP


def background_hint(color: int, mouse_elsewhere: bool, areas: bool = False) -> str:
    """The Background row's subtitle. In Per-key mode a mouse that follows the keyboard takes
    the background, so black leaves it dark, unless it shows another color
    (`mouse_elsewhere`): the loaded game profile's own, or the leader's on the page of a
    keyboard that neither leads nor follows. On a board lit by areas (`areas`) the background
    shows in an area with no painted key (layout.Board.area_colors)."""
    unpainted = "unpainted areas" if areas else "unpainted keys"
    shown_by = unpainted if mouse_elsewhere else f"{unpainted} and a mouse that follows"
    if color == 0x000000:
        return f"Black: {shown_by} are dark. Use brush color to light them"
    return f"Shown by {shown_by}. Not the brush"


def paint_request(background: int, per_key: dict[str, int]) -> dict:
    """The paint command: the background as its base, then the painted keys over it, by
    decimal zone id as a string; a name with no zone is dropped (by_zone)."""
    return {"command": "paint", "base": format_color(background),
            "zones": {str(zone): format_color(color)
                      for zone, color in by_zone(per_key).items()}}


class LightingCard:
    """The Lighting group and the color rows under the board, and the commands they send.

    `loading` is the page's guard, held while a reply sets a row. `unanswered` is the page's
    count of writes not answered yet, by what they set, which Lights off when locked's switch
    counts in too (worker.send_switch).
    The other two are the page's too, which holds the board: `on_defaults` refuses a
    change while the window holds changes back, and says so; `update_board` draws the board
    and has the card draw the picker's labels again (show_picker).
    """

    #: The color a drag on the First color's picker started from, kept from its first preview
    #: to its release (_commit).
    _drag_from: int | None = None

    def __init__(self, window: Window, loading: LoadingGuard, unanswered: Counter[str],
                 on_defaults: Callable[[], bool], update_board: Callable[[], None]) -> None:
        self.win = window
        self._loading = loading
        self._unanswered = unanswered
        self._on_defaults = on_defaults
        self._update_board = update_board
        #: The Speed slider's send once it stops moving (SETTLE_MS), and the
        #: animation the move still waiting was made on.
        self._speed = Settle(self._send_speed)
        self._speed_mode: str | None = None
        #: The daemon's idle dimming as (dim after, lights off after, never dim), the times in
        #: seconds, or None until a status gives them (take_idle); and its
        #: never_dim_with_game_profile, None until a status gives it.
        self.idle: tuple[int, int, bool] | None = None
        self.never_dim_with_game_profile: bool | None = None
        #: The idle time rows' send once they stop changing (SPIN_SETTLE_MS).
        self._idle = Settle(self._send_idle_times)
        #: The window's close ran (close): the idle time rows send nothing from then on.
        self._closed = False
        #: The Brightness row's levels, the keyboard's ascending percents, and the level in
        #: force, from a status (take_brightness) or a reply (on_brightness).
        self.levels: list[int] = [0, 12, 25, 50, 100]
        self.brightness = shown_brightness(window.config.brightness, None, None)[0]
        #: What the Brightness row lists, so its model is rebuilt only when it changes.
        self._brightness_labels: list[str] = []
        #: The last refused preview, so a drag reports it once.
        self._preview_error: str | None = None
        #: Until a keyboard answers, every section is on screen. A keyboard
        #: that cannot paint individual keys clears this, and the Groups row goes with it,
        #: and Per-key and the Background row unless it paints areas (`areas`), and the Effect
        #: row and the rows under it where it has no animation and no Off either (has_modes).
        self.per_key = True
        #: The keyboard shows painted keys as the colors of its board's areas, the G213
        #: Prodigy's: "areas" in its status's `capabilities` (show_capabilities).
        self.areas = False
        #: The keyboard dims while idle, its status's `dims` (show_capabilities).
        self.dims = True
        #: What the Effect row lists, in LIGHTING_MODES' order, and each entry's tip (show_mode).
        self.modes: tuple[str, ...] = LIGHTING_MODES
        self._mode_tips = list(MODE_TIPS)
        #: What the Direction row lists, in DIRECTIONS' order (show_effect_rows).
        self.directions: tuple[str, ...] = DIRECTIONS
        #: The keyboard models the Leader row lists, by key, and (`_status_pick`) where the last
        #: status put it: the one chosen to lead while plugged in, else the leader in force.
        self._keyboards: list[str] = []
        self._status_pick: str | None = None
        #: The positions in that list of those switched off, which the row grays out: changed
        #: in place, since the row's entries read it as they are drawn (gray_out_choices).
        self._keyboards_off: set[int] = set()
        #: The layer each picker last showed the color of (_show_color).
        self._color_for: dict[ColorPicker, tuple[int | None, str | None]] = {}

    def build_lighting(self) -> Adw.PreferencesGroup:
        """The Leader row and the follow switch (show_leader), the Effect row, and under it the
        Speed and Direction rows, each hidden until show_effect_rows shows it. The board
        follows, then the color rows (`build_colors`)."""
        group = Adw.PreferencesGroup(
            title="Lighting",
            description=LIGHTING_HOLDS)
        # Over the Effect row, as a mouse's follow switch is over its own.
        self.leader_row = Adw.ComboRow(title=LEADER, model=Gtk.StringList.new([]),
                                       visible=False)
        self._redraw_leader = gray_out_choices(self.leader_row, self._keyboards_off,
                                               SWITCHED_OFF)
        self.leader_row.connect("notify::selected", self.on_leader)
        group.add(self.leader_row)
        self.follow_row = Adw.SwitchRow(title=FOLLOW.format("leader"),
                                        subtitle=FOLLOW_HELP.format("leader"), visible=False)
        self.follow_row.connect("notify::active", self.on_follow)
        group.add(self.follow_row)
        self.mode_row = Adw.ComboRow(title=EFFECT, model=Gtk.StringList.new(MODE_LABELS))
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
        """The picker, the Second color picker right under it, hidden until show_effect_rows
        shows it, the saved colors, the background, the brightness, Lights off when locked
        and the idle dimming, under the board. No title: they are the Lighting group's, and
        the board sits between. `sample` and `groups` are the board's two tools, which the
        page builds: the Sample button goes on the picker's row and the Groups row under the
        two pickers."""
        group = Adw.PreferencesGroup()
        self.picker = ColorPicker(self._preview, self._commit, self.win.toast,
                                  subtitle=STATIC_PICKER_HELP,
                                  color=self.win.lighting.color)
        color_row = self.picker.row
        color_row.add_suffix(sample)

        # + saves the layer's color, the brush in Per-key mode.
        self.saved = SavedColors(self.picker, self.win, lambda: self.win.lighting.color)
        group.add(color_row)

        # The First color's picker for the other color, with the same Saved colors row (the
        # owner's decision of 2026-10-24), right under it: the two colors of one effect. A
        # drag sends nothing: the daemon's preview is a per-key frame, which an animation does
        # not show, so the color goes at the release, as the First color reaches the keyboard
        # then (picker_labels). show_effect_rows gives the row its subtitle.
        self.second_picker = ColorPicker(lambda _color: None, self._commit_second,
                                         self.win.toast, subtitle="",
                                         color=self.win.lighting.second_color)
        self.second_picker.row.set_title(SECOND_COLOR)
        self.second_picker.row.set_visible(False)
        self.second_saved = SavedColors(self.second_picker, self.win)
        group.add(self.second_picker.row)
        group.add(groups)

        # Only meaningful in per-key mode, where unpainted keys show it, or unpainted areas on a
        # board lit by areas (background_hint). An image to assistive technology, named for
        # its color in show_background: the CSS fill is not in the accessibility tree.
        self.bg_swatch = Gtk.Box(valign=Gtk.Align.CENTER, css_classes=["g915-swatch"],
                                 accessible_role=Gtk.AccessibleRole.IMG)
        self.bg_swatch.set_size_request(28, 28)
        self.bg_row = Adw.ActionRow(title="Background")
        use = Gtk.Button(label="Use brush color", valign=Gtk.Align.CENTER)
        use.connect("clicked", lambda _b: self._set_background(self.win.lighting.color))
        black = Gtk.Button(label="Use black", valign=Gtk.Align.CENTER)
        black.connect("clicked", lambda _b: self._set_background(0x000000))
        self.bg_row.add_suffix(self.bg_swatch)
        self.bg_row.add_suffix(black)
        self.bg_row.add_suffix(use)
        group.add(self.bg_row)

        self.brightness_row = Adw.ComboRow(
            title="Brightness", subtitle="The levels the keyboard lists")
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
        # decision of 2026-09-23). Shown on a keyboard that dims, once the daemon has the
        # settings (_show_idle_rows).
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
        # Under the times, which keep Never dim's order (the owner's decision of 2026-09-23).
        self.game_dim_row = Adw.SwitchRow(title=NEVER_DIM_WITH_GAME_PROFILE, visible=False)
        self.game_dim_row.connect("notify::active", self.on_never_dim_with_game_profile)
        group.add(self.game_dim_row)
        return group

    def take_brightness(self, status: dict) -> None:
        """The Brightness row's levels and level from a status, then the row."""
        if status.get("brightness_levels"):
            self.levels = status["brightness_levels"]
        if isinstance(status.get("brightness"), int):
            self.brightness = status["brightness"]
        self.refresh_brightness()

    def refresh_brightness(self) -> None:
        labels = [f"{p}%" for p in self.levels]
        # A level between the firmware's ascending ones, saved while the
        # keyboard was away, runs as the one below it (Lighting.set_brightness);
        # the 13 a G515's own key sets, which it keeps, shows as 12 %.
        wanted = max((i for i, p in enumerate(self.levels) if p <= self.brightness),
                     default=0)
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
            self._loading.put_active(self.lock_row, win.lock_lights)
        self.lock_row.set_subtitle(win.lock_unavailable or LOCK_HELP)

    def on_lock_lights(self, row: Adw.SwitchRow, _p: GObject.ParamSpec) -> None:
        send_switch(self.win, self._loading, self._unanswered, row, command="lock_lights",
                    key="lights_off_when_locked")

    def take_idle(self, status: dict) -> None:
        """Idle dimming's times and never dim, from a status, on the idle rows."""
        dim_after = status.get("idle_dim_after")
        # The daemon's `status` gives both times together (Daemon._cmd_status).
        self.idle = None if dim_after is None else cast(tuple[int, int, bool], (
            dim_after, status.get("idle_off_after"), bool(status.get("never_dim"))))
        game = status.get("never_dim_with_game_profile")
        self.never_dim_with_game_profile = game if isinstance(game, bool) else None
        self.show_idle()

    def show_idle(self) -> None:
        """The idle rows as the daemon has them, shown as _show_idle_rows says,
        the times and the game profile's switch grayed out while the Never dim
        switch is on. A time waiting to be sent or unanswered, and a switch
        while its write is unanswered, are left as they are: a status read in
        between would put the old one back under the cursor."""
        self._show_idle_rows()
        if self.idle is None:
            return
        dim_after, off_after, never = self.idle
        if not self._idle.pending and not self._unanswered["idle_times"]:
            self._loading.put_value(self.dim_after_row, dim_after)
            self._loading.put_value(self.off_after_row, off_after)
        if not self._unanswered["never_dim"]:
            self._loading.put_active(self.never_dim_row, never)
        game = self.never_dim_with_game_profile
        if game is not None and not self._unanswered["never_dim_with_game_profile"]:
            self._loading.put_active(self.game_dim_row, game)
        for row in (self.dim_after_row, self.off_after_row, self.game_dim_row):
            row.set_sensitive(not self.never_dim_row.get_active())

    def _show_idle_rows(self) -> None:
        """The idle rows while the daemon gives their times, on a keyboard that dims, the
        game profile's switch while it gives that too."""
        for row in (self.never_dim_row, self.dim_after_row, self.off_after_row):
            row.set_visible(self.idle is not None and self.dims)
        self.game_dim_row.set_visible(self.idle is not None and self.dims
                                      and self.never_dim_with_game_profile is not None)

    def on_never_dim(self, row: Adw.SwitchRow, _p: GObject.ParamSpec) -> None:
        """Send the switch, and read it back either way: a refusal puts it back."""
        if self._loading.active or self.idle is None:
            return
        wanted = row.get_active()
        if wanted == self.idle[2] and not self._unanswered["never_dim"]:
            return
        self._unanswered["never_dim"] += 1

        def done(reply: dict | None) -> None:
            self._unanswered["never_dim"] -= 1
            if reply is None:
                self.show_idle()
            elif self.idle is not None and isinstance(reply.get("never_dim"), bool):
                self.idle = (*self.idle[:2], reply["never_dim"])
            self.win.resync()
        self.win.call({"command": "idle", "never_dim": wanted}, done)

    def on_never_dim_with_game_profile(self, row: Adw.SwitchRow,
                                       _p: GObject.ParamSpec) -> None:
        """Send the switch, and read it back either way: a refusal puts it back. The page's
        own set of the row (show_idle) is the daemon's value with no write out, which sends
        nothing."""
        wanted = row.get_active()
        field = "never_dim_with_game_profile"
        if wanted == self.never_dim_with_game_profile and not self._unanswered[field]:
            return
        self._unanswered[field] += 1

        def done(reply: dict | None) -> None:
            self._unanswered[field] -= 1
            if reply is None:
                self.show_idle()
            elif isinstance(reply.get(field), bool):
                self.never_dim_with_game_profile = reply[field]
            self.win.resync()
        self.win.call({"command": "idle", field: wanted}, done)

    def on_idle_time(self, _row: Adw.SpinRow, _p: GObject.ParamSpec) -> None:
        """A time changed: send both once they settle (SPIN_SETTLE_MS); not a time typed that
        GTK takes as the window is destroyed, after the close (worker.take_typed)."""
        if self._loading.active or self._closed or self.idle is None:
            return
        self._idle.restart(SPIN_SETTLE_MS)

    def _send_idle_times(self) -> None:
        """Send the two times, unless they are what the daemon has and no
        send of them is unanswered. A refusal puts the rows back; a note, that the keyboard did
        not get them, is toasted."""
        if self.idle is None:
            return
        # round(), not int(): the row shows a typed 15.7 as 16, rounding half to even
        # as round() does.
        times = round(self.dim_after_row.get_value()), round(self.off_after_row.get_value())
        if times == self.idle[:2] and not self._unanswered["idle_times"]:
            return
        self._unanswered["idle_times"] += 1

        def done(reply: dict | None) -> None:
            self._unanswered["idle_times"] -= 1
            if reply is None:
                self.show_idle()
            elif self.idle is not None:
                self.idle = (reply["idle_dim_after"], reply["idle_off_after"], self.idle[2])
            say_not_shown(self.win.toast, reply, IDLE_TIMES_LINE)
            self.win.resync()
        self.win.call({"command": "idle", "dim_after": times[0], "off_after": times[1]}, done)

    def flush_speed(self) -> None:
        """Send a Speed move still settling, now: it goes before a change that switches the
        lighting or the layer, on the layer it was made on."""
        self._speed.flush()

    def close(self) -> None:
        """Stop the waits, the pickers' too, and the idle time rows' sends, and take the
        pickers' rules off the display."""
        self._closed = True
        self._speed.cancel()
        self._idle.cancel()
        self.picker.destroy()
        self.second_picker.destroy()

    def flush(self) -> bool:
        """Send the speed, the idle times, a time typed and not yet taken among them, and a
        run of keys still settling on either picker, now, for the window's close. True when
        one was."""
        take_typed(self.dim_after_row, self.off_after_row)
        return any([self._speed.flush(), self._idle.flush(), self.picker.flush(),
                    self.second_picker.flush()])

    @property
    def dragging(self) -> bool:
        """A drag on either picker is between press and release, or a run of its keys
        settles."""
        return self.picker.dragging or self.second_picker.dragging

    def on_mode(self, row: Adw.ComboRow, _p: GObject.ParamSpec) -> None:
        if self._loading.active:
            return
        if self._on_defaults():
            self._loading.put_selected(row, self.modes.index(self.win.lighting.mode))
            return
        win = self.win
        mode = self.modes[row.get_selected()]
        # A speed still settling goes first, for the animation it was set on.
        self._speed.flush()
        if win.lighting.choose_mode(mode):
            # The picker goes to the color that came back, the Static color or Off's black:
            # Per-key starts from what the picker shows. set_color calls no handler.
            self.picker.set_color(win.lighting.color)
        if mode in ANIMATIONS:
            # The effect alone: the daemon keeps the speed and the direction
            # the profile saved, and the painted keys, hidden, for Per-key.
            win.send_lighting({"command": "effect", "value": mode})
        elif mode == OFF:
            # Static too: under disabled a Per-key layout's keys still show. The keys are
            # kept for Per-key.
            win.send_lighting({"command": "effect", "value": OFF, "mode": STATIC})
        else:
            self.push_lighting()
        self.show_effect_rows()
        self._draw_lighting()

    def show_leader(self, attached: list[str], switched_off: set[int], status: dict) -> None:
        """The Leader row and the follow switch from a status, `attached` by key and
        `switched_off` by position in it. The row sits on the keyboard chosen to lead while it
        is plugged in, so a pick not in force yet can be taken back there (the owner's decision
        of 2026-10-27), else on the leader, with those switched off grayed out and out of reach,
        as on the switcher (the owner's decision of 2026-10-24). A pick or a flip not answered
        yet holds its row."""
        leader, shown = self.win.shown_on.leader, self.win.shown_on.model
        chosen = status.get("sync_keyboard")
        self._status_pick = chosen if chosen in attached else leader
        off_changed = switched_off != self._keyboards_off
        self._keyboards_off.clear()
        self._keyboards_off.update(switched_off)
        if attached != self._keyboards:
            self._keyboards = list(attached)
            self._loading.put_model(self.leader_row,
                                    Gtk.StringList.new([capitalized(key_name(key))
                                                        for key in attached]))
        elif off_changed:
            self._redraw_leader()
        several = len(attached) >= 2
        self.leader_row.set_visible(several and self._status_pick in attached)
        if self._status_pick in attached and not self._unanswered["sync_keyboard"]:
            self._loading.put_selected(self.leader_row, attached.index(self._status_pick))
        self.follow_row.set_visible(several and None not in (leader, shown) and shown != leader)
        if leader is not None:
            self.follow_row.set_title(FOLLOW.format(key_name(leader)))
            self.follow_row.set_subtitle(FOLLOW_HELP.format(key_name(leader)))
        if not self._unanswered["follow_leader"]:
            self._loading.put_active(self.follow_row, status.get("follow_leader") is not False)
        self.show_following()

    def show_following(self) -> None:
        """Gray out the rows that change the lighting of the keyboard the tab shows while it
        follows the leader, as a mouse's own color rows are while it syncs with the keyboard:
        they show the leader's lighting, which a change here would not reach. The First color
        picker's row is show_picker's, which the page's update_board runs next."""
        for row in (self.mode_row, self.speed_row, self.direction_row, self.second_picker.row,
                    self.bg_row, self.brightness_row):
            row.set_sensitive(not self.win.shown_on.follows)

    def on_leader(self, row: Adw.ComboRow, _p: GObject.ParamSpec) -> None:
        """Send the keyboard picked to lead, as `gearbench-ctl sync-keyboard` does, then read
        the status again; a refusal puts the row back where the last status put it
        (show_leader). A pick the daemon saves but whose keyboard it has not set up, so that
        the reply's leader is another, says so (LEADS_ONCE_SET_UP). With a later pick out,
        neither: the last pick is the user's."""
        if self._loading.active or not self._keyboards:
            return
        picked = self._keyboards[row.get_selected()]
        self._unanswered["sync_keyboard"] += 1

        def done(reply: dict | None) -> None:
            self._unanswered["sync_keyboard"] -= 1
            later = self._unanswered["sync_keyboard"] > 0
            if reply is None and not later and self._status_pick in self._keyboards:
                self._loading.put_selected(row, self._keyboards.index(self._status_pick))
            elif reply is not None and not later and reply.get("sync_leader") != picked:
                self.win.toast(LEADS_ONCE_SET_UP.format(key_name(picked)))
            self.win.resync()
        self.win.call({"command": "sync_keyboard", "value": picked}, done)

    def on_follow(self, row: Adw.SwitchRow, _p: GObject.ParamSpec) -> None:
        """Send the follow switch of the keyboard the tab shows, then read the status again;
        a refusal puts the switch back."""
        shown = self.win.shown_on.model
        if self._loading.active or shown is None:
            return
        wanted = row.get_active()
        self._unanswered["follow_leader"] += 1

        def done(reply: dict | None) -> None:
            self._unanswered["follow_leader"] -= 1
            if reply is None and not self._unanswered["follow_leader"]:
                self._loading.put_active(row, not wanted)
            self.win.resync()
        self.win.call({"command": "follow_leader", "keyboard": shown, "value": wanted}, done)

    def show_capabilities(self, able: set[str], dims: bool) -> None:
        """The rows this keyboard has, from its status's `capabilities` and the animations
        it has (Window.animations): the Effect row and the rows under it on a keyboard with a
        mode to choose (has_modes), Brightness only on one that has it."""
        self.per_key, self.areas = "per_key" in able, "areas" in able
        # The idle rows on a keyboard that dims while idle, hidden on one lit through 0x8070,
        # which has no idle times (the G213): status's `dims`.
        self.dims = dims
        self.show_effect_rows()
        self.mode_row.set_visible(self.has_modes)
        self.brightness_row.set_visible("brightness" in able)
        self._show_idle_rows()

    def show_mode(self) -> None:
        """The Effect row, the rows under it and the picker's color as the window's lighting
        has them. It runs under the window's loading guard (KeyboardPage.sync), so the
        rows' handlers send nothing."""
        win = self.win
        self._list_modes(win.lighting.mode)
        self.mode_row.set_selected(self.modes.index(win.lighting.mode))
        self.show_effect_rows()
        self._show_color(self.picker, win.lighting.color)

    def _show_color(self, picker: ColorPicker, color: int) -> None:
        """`color` on `picker`, unless a drag on it is under way: its release commits the color
        it shows, as on the Mouse tab. A color being typed in its Hex row stays, as there, but
        a layer switch drops it, as it drops a name being typed
        (KeyboardPage.refresh_profile_row). set_color calls no handler."""
        win = self.win
        if not picker.dragging and (win.layer != self._color_for.get(picker)
                                    or not picker.typing(win)):
            picker.set_color(color)
            self._color_for[picker] = win.layer

    @property
    def paints(self) -> bool:
        """Whether the keyboard shows painted keys, key by key or by its board's areas, so that
        Per-key is a mode it has."""
        return self.per_key or self.areas

    @property
    def has_modes(self) -> bool:
        """Whether the keyboard has a mode to choose: Per-key, an animation (Window.animations)
        or Off, which its status's effects list. Zone lighting with none has one color alone,
        the G213's 0x8070 zones with no animation listed, no area found and no disabled
        effect."""
        return (self.paints or bool(self.win.animations)
                or OFF in (self.win.shown_on.effects or ()))

    def _list_modes(self, mode: str) -> None:
        """The Effect row's list: Static, Per-key on a keyboard that paints single keys or areas
        (`paints`), the animations the attached keyboard has (Window.animations), Off where it
        reports it (ShownOn.reports), and `mode`, the one shown, if it is none of those. That
        is the lighting the window read before a status changed the keyboard's animations,
        until the files are read again (Window._moved). Per-key's tip on a board lit by areas
        is AREA_TIP."""
        offered = (STATIC, *((PER_KEY,) if self.paints else ()), *self.win.animations,
                   *((OFF,) if self.win.shown_on.reports(OFF) else ()), mode)
        modes = tuple(m for m in LIGHTING_MODES if m in offered)
        tips = [AREA_TIP if m == PER_KEY and self.areas else _TIPS[m] for m in modes]
        if modes == self.modes and tips == self._mode_tips:
            return
        self.modes = modes
        # explain_choices reads its tips when an entry is drawn.
        self._mode_tips[:] = tips
        self.mode_row.set_model(Gtk.StringList.new(
            [AREA_LABEL if m == PER_KEY and self.areas else mode_label(m) for m in modes]))

    def show_picker(self, sampling: bool) -> None:
        """The picker's title and subtitle for the mode, with Sample on or off
        (picker_labels), on the keyboard model shown (Window.shown_on): under ripple the
        subtitle says the logo stays steady where the model shown has it so
        (animation.logo_steady)."""
        mode = self.win.lighting.mode
        steady = logo_steady(mode, model_named(self.win.shown_on.model))
        title, subtitle = picker_labels(mode, sampling, areas=self.areas, frames=self.per_key,
                                        steady_logo=steady)
        self.picker.row.set_title(title)
        self.picker.row.set_subtitle(subtitle)
        # Grayed out under an animation with no color of its own (lighting.COLORLESS) and under
        # Off, which the subtitle says, and while the keyboard follows the leader
        # (show_following).
        self.picker.row.set_sensitive(mode not in (*COLORLESS, OFF)
                                      and not self.win.shown_on.follows)

    def show_brush(self) -> None:
        """The picker in the window's color, which the board's Sample gave the brush. It
        sends nothing: set_color calls no handler."""
        self.picker.set_color(self.win.lighting.color)

    def show_effect_rows(self) -> None:
        """The Background row in Per-key mode, the Speed row under an animation that takes a
        speed (SPEEDS), the Direction row under wave and the Second color picker under Pulsar
        Point, showing what the profile saved; each hidden otherwise, and on a keyboard whose
        Effect row is hidden. A speed waiting to be sent is left as it is, as show_idle
        leaves a time, and a second color as _show_color leaves one."""
        win = self.win
        mode = win.lighting.mode
        self.bg_row.set_visible(self.paints and mode == PER_KEY)
        animated = self.has_modes and mode in ANIMATIONS
        self.speed_row.set_visible(animated and mode in SPEEDS)
        self.direction_row.set_visible(animated and mode == "wave")
        self.second_picker.row.set_visible(animated and mode in TWO_COLORED)
        if not animated:
            return
        if mode in TWO_COLORED:
            self.second_picker.row.set_subtitle(SECOND_PICKER_HELP.format(
                effect=mode_label(mode)))
            self._show_color(self.second_picker, win.lighting.second_color)
        if mode == "wave":
            self._list_directions(win.lighting.direction)
            self._loading.put_selected(self.direction_row,
                                       self.directions.index(win.lighting.direction))
        if mode not in SPEEDS:
            return
        ms = win.lighting.speeds.get(mode, DEFAULT_PERIODS[mode])
        settling = self._speed.pending and self._speed_mode == mode
        if not settling:
            low, high = SPEEDS[mode]
            held = nearest_speed(mode, ms)
            shown = self.speed_scale.get_adjustment()
            # A new range can move the value, which calls on_speed. The slider holds a period
            # outside the range, which an M profile's params can give, at the nearer end
            # (nearest_speed), where the board draws it.
            if (shown.get_lower(), shown.get_upper(), self.speed_scale.get_value()) \
                    != (low, high, held):
                with self._loading:
                    self.speed_scale.set_range(low, high)
                    # A Page key or a wheel notch moves a page: 1000 ms would
                    # cross ripple's whole range in one, so there it is a step.
                    shown.set_page_increment(1000 if high - low > 1000 else SPEED_STEP)
                    self.speed_scale.set_value(held)
            self._say_speed(speed_text(mode, ms))

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
            [DIRECTION_WORDS[d] for d in directions]))

    def on_speed(self, scale: Gtk.Scale) -> None:
        """The slider moved: say the speed now, and send it once it settles."""
        mode = self.win.lighting.mode
        if self._loading.active or mode not in SPEEDS:
            return
        self._say_speed(speed_text(mode, snap_speed(mode, scale.get_value())))
        self._speed_mode = mode
        self._speed.restart(SETTLE_MS)

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
        """Send the slider's speed, saved for its animation, which the board draws at it."""
        mode = self.win.lighting.mode
        if mode not in SPEEDS:
            return
        # Before the hold: a move that snaps to the saved speed changes nothing to hold.
        ms = snap_speed(mode, self.speed_scale.get_value())
        if ms == self.win.lighting.speeds.get(mode):
            return
        if self._on_defaults():
            self.show_effect_rows()             # put the slider back
            return
        self.win.lighting.speeds[mode] = ms
        self.win.send_lighting({"command": "effect", "value": mode, "speed": ms})
        self._update_board()

    def on_direction(self, row: Adw.ComboRow, _p: GObject.ParamSpec) -> None:
        """A direction for wave, sent and saved at once, and drawn on the board."""
        if self._loading.active or self.win.lighting.mode != "wave":
            return
        index = row.get_selected()
        if not 0 <= index < len(self.directions) \
                or self.directions[index] == self.win.lighting.direction:
            return
        if self._on_defaults():
            self._loading.put_selected(row, self.directions.index(self.win.lighting.direction))
            return
        self.win.lighting.direction = self.directions[index]
        self.win.send_lighting({"command": "effect", "value": "wave",
                                "direction": self.win.lighting.direction})
        self._update_board()

    def _commit_second(self, color: int) -> None:
        """The Second color picker's release, or a saved color or a hex applied there: sent
        and saved for Pulsar Point, and drawn on the board. While the window holds changes
        back, the picker goes back to the color saved. Not under another effect, where a run of
        the picker's keys can settle after a status changed the effect."""
        mode = self.win.lighting.mode
        if mode not in TWO_COLORED or color == self.win.lighting.second_color:
            return
        if self._on_defaults():
            self.second_picker.set_color(self.win.lighting.second_color)
            return
        self.win.lighting.second_color = color
        self.win.send_lighting({"command": "effect", "value": mode,
                                "second_color": format_color(color)})
        self._update_board()

    def show_background(self) -> None:
        """The Background row in the window's background: the swatch's color rule and its
        name for a screen reader, and the subtitle. The rule loads with the page's style
        sheet (refresh_css), which holds every color in play."""
        win = self.win
        background = win.lighting.background
        take_color_class(self.bg_swatch, SWATCH_PREFIX, swatch_class(background))
        self.bg_swatch.update_property([Gtk.AccessibleProperty.LABEL],
                                       [f"Background color {format_color(background)}"])
        # A keyboard that follows shows the leader's lighting, which the mouse shows too.
        leaders = win.copy_is_leaders() or win.shown_on.follows
        elsewhere = win.loaded_mouse_color() is not None or not leaders
        self.bg_row.set_subtitle(background_hint(background, elsewhere, areas=self.areas))

    def _set_background(self, color: int) -> None:
        if self._on_defaults():
            return
        self.win.lighting.background = color
        self.push_lighting()
        self._draw_lighting()

    def on_brightness(self, row: Adw.ComboRow, _p: GObject.ParamSpec) -> None:
        if self._loading.active:
            return
        level = self.levels[row.get_selected()]
        self._unanswered["brightness"] += 1

        def done(reply: dict | None) -> None:
            self._unanswered["brightness"] -= 1
            if reply is not None:
                self.brightness = reply["applied"]
            # After a refusal, this puts the row back once no other level is out.
            self.refresh_brightness()
        self.win.call({"command": "brightness", "value": level}, done)

    def show_saved(self) -> None:
        """The two pickers' Saved colors rows, as the window's list has it now
        (SavedColors.rebuild)."""
        self.saved.rebuild()
        self.second_saved.rebuild()

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
            self.win.lighting.commit(color)
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
        # The Static color from now on (LightingState.commit); a drag's previews are not, as
        # they are not saved. Each animation with a color runs in it.
        self.win.lighting.commit(color)
        self.push_lighting()
        self._draw_lighting()

    def _draw_lighting(self) -> None:
        """Draw the board, and the color of each device that follows the keyboard, in the
        window's lighting."""
        self._update_board()
        self.win.follow_keyboard()

    def on_preview(self, reply: Reply) -> None:
        self._preview_error = preview_reply(reply, self._preview_error,
                                            self.win.set_status, self.win.refused)

    def push_lighting(self) -> None:
        """Send what the window shows; a refusal is taken back and read back
        (Window.send_lighting)."""
        state = self.win.lighting
        if state.mode == PER_KEY:
            # The daemon saves the paint's base as the background, black from Off included.
            state.stored_background = state.background
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
