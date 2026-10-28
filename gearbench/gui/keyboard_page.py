"""The Keyboard tab: the switcher that picks the keyboard the tab shows, its status, the M
profile and the board, around two cards it builds, the Game profile group (game_card) and the
lighting rows (lighting_card), the keyboard that leads and the follow switch among them.

The window holds the state these rows show, read from the daemon and the files;
this page and its cards own the widgets and the handlers.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol, cast

from gi.repository import Adw, Gdk, GLib, GObject, Gtk

from ..config import PER_KEY, PROFILE_IDS, Profile, format_color, profile_label
from ..keyboard import (
    BOUND_KEY_CAPABILITIES,
    CABLED_IDS,
    MODEL_OF as KEYBOARDS,
    followed,
    key_name,
    m_profiles_in_menu,
    model_named,
)
from ..keycodes import ZONE_NAMES, by_zone
from ..keycontrol import GAME_MODE_NOT_KNOWN, GAME_MODE_UNKNOWN, SHORTCUT_TURNS
from ..layout import G915_BOARD, Board, Key
from ..lighting import (
    ANIMATIONS,
    COLORLESS,
    DEFAULT_PERIODS,
    DIRECTION_WORDS,
    SPEEDS,
    TWO_COLORED,
)
from ..profiles import GameProfile
from ..settings_files import config_dir, shown_path
from ..wording import M_PROFILE_NEEDS_A_NAME, capitalized
from .animation import MEDIA_LABELS, Animation, logo_steady
from .board import NOT_BINDABLE, KeyBoard, bindable_span, shown_binding
from .dialogs import CaptureDialog
from .game_card import GameCard
from .lighting_card import (
    AREA_LABEL,
    SAMPLE_HELP,
    LightingCard,
    mode_label,
    paint_request,
    speed_text,
)
from .style import (
    ASLEEP,
    AWAY_BANNER,
    DAEMON_WAIT,
    EFFECT,
    FOLLOW,
    KEYBOARD_ASLEEP,
    KEYBOARD_TAB,
    ON_CABLE,
    TEST_WAIT,
    DeviceView,
    EntryTexts,
    Styler,
    Switcher,
    confirm,
    name_button,
    route_text,
    say_not_shown,
    set_dot,
    show_battery,
    show_untested,
    status_rows,
    stylesheet,
    untested_row,
    weakly,
)
from .worker import LoadingGuard, named_layer

if TYPE_CHECKING:                       # pragma: no cover
    from .window import Window

    class SendSwitch(Protocol):
        """`KeyboardPage._send_switch`, as the page hands it to a card."""

        def __call__(self, row: Adw.SwitchRow, *, command: str, key: str) -> None: ...

#: The board row's title while the loaded game profile's file cannot be read
#: (Window.profile_unread), when the window holds its changes to the layer (Window.held):
#: short, as ON_UNREAD_PROFILE, since the config banner says what to do and stays up.
UNREAD_PROFILE_BOARD = "Not sent: the file of game profile {} could not be read"
#: The status page's titles until a keyboard is first seen (DeviceView): before
#: the first reply, with the spinner, and over the reason once one says it is missing.
LOOKING = "Looking for the keyboard…"
NO_KEYBOARD = "Keyboard not available"
#: What a screen reader calls the switcher at the top, a button for each keyboard plugged in,
#: shown while two or more are (the owner's decision of 2026-10-20, "Only attached ones"). Its
#: pick is the keyboard the tab shows and edits (the owner's decision of 2026-10-21, "B:
#: separate pages, follow switch"); the Leader row picks the one that leads.
PICK_KEYBOARD = "Keyboard"
#: The M profile group's description: the keys that switch the profiles, or, on a keyboard
#: with lighting and no M keys, where they are switched instead (the owner's decision of
#: 2026-10-18). The tray icon's menu offers them while any such keyboard is connected, the
#: leader or not, so the group names the tray while this one is and the icon shows; while it
#: is away, while the daemon is gone or with no tray icon, it names only this tab.
M_KEYS_SWITCH = "M1, M2 and M3 switch these on the keyboard"
NO_M_KEYS_SWITCH = "Switch these here or from the tray icon's menu"
NO_M_KEYS_AWAY = "Switch these here"
#: The board row's title in Per-key mode, and on a board lit by areas, where a key's paint
#: colors its whole area (layout.Board.painted_with).
PAINT_HELP = "Click or drag to paint keys; right-click or right-drag to clear"
AREA_PAINT_HELP = "Click or drag to paint areas; right-click or right-drag to clear"
#: What a click or a drag on the board says outside Per-key, and outside Per-area on a board
#: lit by areas.
NOT_PER_KEY = f"Switch {EFFECT} to {mode_label(PER_KEY)} to paint keys"
NOT_PER_AREA = f"Switch {EFFECT} to {AREA_LABEL} to paint areas"
#: The board row's title outside map mode while the keyboard shown follows the leader and the
#: loaded profile is read: the board shows the leader's lighting and takes no paint
#: (LightingCard.show_following). The leader by its model, and the switch by its title
#: (FOLLOW), as SYNC_ON names its switch; on a board lit by areas, its areas.
FOLLOWING_BOARD = "Shows the {0}'s lighting; turn off " + FOLLOW.format("{0}") + " to paint keys"
AREA_FOLLOWING_BOARD = ("Shows the {0}'s lighting; turn off " + FOLLOW.format("{0}")
                        + " to paint areas")
#: The G-key toggle's label.
BIND_G_KEYS = "Bind G keys"
#: The status group's rows for Game Mode, shown on a keyboard that has it, not known until
#: an event gives it, and for custom assignments, shown on or off on a keyboard whose own keys
#: bind through them (`assigned_keys`), with the shortcut that turns them the other way.
GAME_MODE = "Game Mode"
GAME_MODE_SHOWN = {"on": "On", "off": "Off",
                   GAME_MODE_UNKNOWN: capitalized(GAME_MODE_NOT_KNOWN)}
CUSTOM_ASSIGNMENTS = "Custom assignments"
#: Its line on a keyboard whose bindings wait for a test (the reply's `assignments_untested`),
#: in the Read only row's words: shown, as a write held back is, where it was left out.
ASSIGNMENTS_UNTESTED = f"Bindings wait {TEST_WAIT}"
#: Its line by the reply's `custom_assignments`, and with the shortcut: "On · FN+F1 turns
#: them off".
ASSIGNMENTS_SHOWN = {True: "On", False: "Off"}
ASSIGNMENTS_SHORTCUT = "{} · " + SHORTCUT_TURNS
#: How both boards' help begins, the same on each.
MOVE_HELP = "Arrow keys move between the keys. Space or Enter does what a click does."
#: What the board says to a screen reader about its keys, which the board row says only for
#: the pointer. Not shown. Set once, whatever the mode, so it names by their labels the mode
#: and the toggle state in which Shift paints and Delete clears.
BOARD_HELP = (f"{MOVE_HELP} Under {mode_label(PER_KEY)}, with {BIND_G_KEYS} off, "
              "Shift with an arrow paints each key it reaches, and Delete or the Menu key "
              "clears a key.")
#: The same for a board lit by areas, which has no G keys to map.
AREA_BOARD_HELP = (f"{MOVE_HELP} Under {AREA_LABEL}, Shift with an arrow paints the area "
                   "of each key it reaches, and Delete or the Menu key clears a key's area.")


def _numbered(name: str, prefix: str) -> bool:
    """`name` is `prefix` and a number: F12, G5."""
    return name.startswith(prefix) and name[len(prefix):].isdigit()


def quick_groups(board: Board) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    """The Groups row's buttons as (label, what it paints, zone names), over the
    keys `board` draws with a light, each group in the board's order. The media
    keys go by their labels: the PRO X TKL LIGHTSPEED's Previous and Mute light
    at ids the G915 gives other keys (keycodes.ZONES).

    G HUB's eight quick color groups (picked by HID usage in its app.min.js)
    and the keypad and media keys, the owner's decision of 2026-09-24; the
    Interface and Combat groups are the owner's of 2026-09-25.
    """
    names = lit_names(board)
    media = {ZONE_NAMES[key.zone] for key in board.keys
             if key.label in MEDIA_LABELS.values() and key.zone is not None}

    def pick(test: Callable[[str], bool]) -> tuple[str, ...]:
        return tuple(name for name in names if test(name))

    return (
        ("WASD", "W, A, S and D", pick(lambda n: n in ("W", "A", "S", "D"))),
        # Short labels, with the full names in the tooltips, keep the row's
        # columns narrow (the owner's choice of 2026-09-25).
        ("Interface", "Tab, M, Esc and Enter, the interface and management keys,",
         pick(lambda n: n in ("TAB", "M", "ESC", "ENTER"))),
        ("Combat", "E, F, R, V, Q and G, the interaction and combat keys,",
         pick(lambda n: n in ("E", "F", "R", "V", "Q", "G"))),
        ("Numbers", "the number row, 1 to 0,", pick(lambda n: len(n) == 1 and n.isdigit())),
        ("F keys", "F1 to F12", pick(lambda n: _numbered(n, "F"))),
        # The G915's G1 to G5, a G915 X's G1 to G9: the tooltips are built once, for every board.
        ("G keys", "the G keys", pick(lambda n: _numbered(n, "G"))),
        # The modifier keys differ by board: a TKL's right Super is FN.
        ("Modifiers", "the modifier keys",
         pick(lambda n: n.startswith(("LEFT ", "RIGHT ")))),
        ("Arrows", "the four arrow keys", pick(lambda n: n in ("UP", "DOWN", "LEFT", "RIGHT"))),
        ("Keypad", "the keypad and Num Lock",
         pick(lambda n: n == "NUMLOCK" or n.startswith("KEYPAD "))),
        ("Media", "Previous, Play, Next and Mute",
         pick(lambda n: n in media)),
        ("Logo", "the logo", pick(lambda n: n == "LOGO")),
        ("All keys", "every lit key but the logo", pick(lambda n: n != "LOGO")),
    )


def lit_names(board: Board) -> list[str]:
    """The zone names of the keys `board` draws with a light, in its order."""
    return [ZONE_NAMES[key.zone] for key in board.keys if key.zone is not None]


#: The Groups row's buttons, over the keys the G915's board draws with a light. Another
#: board's groups are its own (KeyboardPage.show_board).
QUICK_GROUPS = quick_groups(G915_BOARD)


def board_of(product: object) -> Board | None:
    """The board of the keyboard a status's `product`, its id in hex, names; None for none, or
    for an id this build does not know, from a daemon of another version."""
    try:
        return KEYBOARDS[int(str(product), 16)].board
    except (ValueError, KeyError):
        return None


def gives_way(picked: str | None, asked: str | None, shown: str | None,
              attached: list[str]) -> bool:
    """Whether the keyboard the tab showed, `picked`, gives way to the leader's: it is no longer
    among those `attached`, or the status read that named it (`asked`) gave another's entries
    (`shown`). A read sent before a pick named another keyboard, so its reply judges none."""
    return picked is not None and (picked not in attached
                                   or (asked == picked and shown != picked))


def plugged_in(status: dict) -> list[dict]:
    """A status's `keyboards_attached`: each entry that names a model by its key."""
    attached = status.get("keyboards_attached")
    return [item for item in (attached if isinstance(attached, list) else ())
            if isinstance(item, dict) and isinstance(item.get("model"), str)]


def keyboard_problem(status: dict, shown: str | None) -> dict | None:
    """The status's keyboard problem for the tab on model `shown`: the first entry about
    `shown`, as the daemon lists the leader's first; else, while `shown` is not plugged in, the
    first of any keyboard, since with the leader's unplugged its look finds another model's
    node."""
    said = [p for p in status.get("problems") or () if p["code"].startswith("keyboard_")]
    plugged = [item["model"] for item in plugged_in(status)]
    return next((p for p in said if p.get("keyboard") == shown),
                None if shown in plugged else next(iter(said), None))


def binding_layer(overlay: GameProfile | None,
                  profile: Profile) -> dict[str, str | None]:
    """The G-key bindings in force: the loaded game profile's when one is
    loaded, the M profile's own otherwise.

    The daemon resolves a G key press and binds the same way, so the board
    shows what a key sends and an edit lands where the daemon put it. The
    board reads and writes through this one function so the two agree.
    """
    return profile.keys if overlay is None else overlay.bindings


def with_stroke(per_key: dict[str, int], stroke: dict[str, int | None]) -> dict[str, int]:
    """The painted keys after a drag: each key it painted in its color, and
    each it cleared (None) without one, back to the background."""
    return {name: color for name, color in {**per_key, **stroke}.items() if color is not None}


class KeyboardPage:
    """The Keyboard tab: its status, the M profile and the board, and the commands those
    send. The lighting rows and the Game profile group are cards of their own (`light`,
    `game`), built here with what they need of the page."""

    #: A drag on the board, from its second key to its release: each key it
    #: reached by name, with the brush, or None for one it cleared. Sent at
    #: the release as one paint.
    _stroke: dict[str, int | None] | None = None
    #: The M profile refresh_profile_row last put a name in the Name row for.
    _name_for: int | None = None
    #: The last status with capabilities named a keyboard that switches its M profiles from
    #: the tray's menu (m_profiles_in_menu).
    _in_menu = False
    #: The last status with capabilities said the tray icon shows (its `tray`'s `shown`).
    _tray_shown = False

    def __init__(self, window: Window) -> None:
        self.win = window
        #: The key rule and the colors in play (refresh_css); before the board, whose
        #: first layout loads it.
        self._css = Styler()
        #: The text the page last put in the Name row, by refresh_profile_row or by an apply
        #: (on_name).
        self._texts = EntryTexts()
        #: Unanswered writes, by what they set: a poll sent before one would put the
        #: old value back, and a value put back while one is out is still sent,
        #: since that write may land after it (Holds.unchanged, the A50's page's).
        self._unanswered: Counter[str] = Counter()
        #: What the Active row lists, so its model is rebuilt only when it changes.
        self._profile_labels: list[str] = []
        self.map_mode = False
        #: The tab's LoadingGuard, handed to its cards.
        self.loading = LoadingGuard()
        #: The keyboard models plugged in, by key, a button each on the switcher, and the
        #: indexes of those switched off, which cannot be picked (take_keyboards).
        self._attached: list[str] = []
        self._switched_off: set[int] = set()
        #: The keys each quick group paints on the board drawn, by its label, and its button.
        self._groups = {label: names for label, _what, names in QUICK_GROUPS}
        self._group_buttons: dict[str, Gtk.Button] = {}

        #: The lighting rows over and under the board, and the Game profile group. A Speed
        #: move still settling goes out before the Game profile group changes the layer.
        self.light = LightingCard(window, self.loading, self._unanswered, self._send_switch,
                                  self.on_defaults, self.update_board, self.refresh_css)
        self.game = GameCard(window, self.loading, self._unanswered, self._send_switch,
                             self.light.flush_speed)
        self._build()

    # -- construction -----------------------------------------------------

    def _build(self) -> None:
        """The tab: the groups, in a window that scrolls, under the banner."""
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                          margin_top=18, margin_bottom=18, margin_start=18, margin_end=18)
        self.status_group = self._status_group()
        self.profile_group = self._profile_group()
        self.game_group = self.game.build()
        self.light_group = self.light.build_lighting()
        # Built before the board, whose first layout reads the background
        # swatch, and shown after it.
        self.color_group = self.light.build_colors(self._sample_button(), self._groups_row())
        self.board_group = self._board_group()
        # The board right under the Effect row: after the color rows it would start
        # about 180 px further down the tab.
        for group in (self.status_group, self.profile_group, self.game_group,
                      self.light_group, self.board_group, self.color_group):
            content.append(group)
        # Up while the keyboard is away or not answering, saying once that changes
        # are saved and applied when it is back. A top bar, as on the Mouse tab, so
        # it stays on screen however far the tab is scrolled; this tab therefore
        # scrolls itself.
        self.away_banner = Adw.Banner(use_markup=False)
        # A status page in place of the rows until a keyboard is seen.
        self.view = DeviceView(content, "input-keyboard-symbolic", LOOKING)
        self.widget = Adw.ToolbarView(content=self.view.stack)
        # Over the banner, as the Mouse tab's switcher is.
        self.switcher = Switcher(PICK_KEYBOARD, self.on_page)
        self.widget.add_top_bar(self.switcher.widget)
        self.widget.add_top_bar(self.away_banner)
        # Escape turns Sample off wherever the focus is on the tab (on_escape).
        escape = Gtk.ShortcutController()
        escape.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"),
                                             Gtk.CallbackAction.new(self.on_escape)))
        self.widget.add_controller(escape)
        # Each key typed in the window, before the widget it is typed in (on_typed).
        typed = Gtk.EventControllerKey(name="typed-keys",
                                       propagation_phase=Gtk.PropagationPhase.CAPTURE)
        typed.connect("key-pressed", weakly(self.on_typed), True)
        typed.connect("key-released", weakly(self.on_typed), False)
        self.win.add_controller(typed)
        # The keyboard going to another window takes the releases of the keys held with it.
        away = Gtk.EventControllerFocus(name="typed-keys-away")
        away.connect("leave", weakly(self.on_keyboard_left))
        self.win.add_controller(away)

    def _status_group(self) -> Adw.PreferencesGroup:
        """The same shape as the Mouse tab's: what it is, whether it is there, and for a
        model not yet run on, the row that asks for a device report from it."""
        # The tab's noun until a status names the model.
        group = Adw.PreferencesGroup(title=KEYBOARD_TAB)
        self.state_row, self.state_dot, self.battery_row, self.battery_icon = status_rows(group)
        self.untested_row = untested_row(group, self.win.toast)
        self.game_mode_row = Adw.ActionRow(title=GAME_MODE, use_markup=False, visible=False)
        self.assignments_row = Adw.ActionRow(title=CUSTOM_ASSIGNMENTS, use_markup=False,
                                             visible=False)
        group.add(self.game_mode_row)
        group.add(self.assignments_row)
        return group

    def take_status(self, connected: bool, device: str | None, battery: dict | None, *,
                    answering: bool = True, problem: dict | None = None,
                    name: str | None = None, product: str | None = None,
                    battery_at: float | None = None) -> None:
        """What the status poll saw of the keyboard itself.

        `answering` is the daemon's word on an attached keyboard: switched
        off behind its receiver it stays attached. `problem` is the status's
        keyboard problem for the shown keyboard (keyboard_problem); a node found that does
        not answer (keyboard_no_answer), through a receiver or on a cable, is
        amber, as an attached keyboard not answering is. `name` is the model,
        for the banner. `battery_at` is when a kept reading was taken, None
        while the reading is live (battery.kept_at).

        Until a status says a keyboard is connected, the tab is the status
        page (DeviceView), and the banner stays down: there is nothing on it
        to change.
        """
        here = connected and answering
        problem = problem or {}
        message = str(problem.get("message", ""))
        if connected:
            self.view.found()
        elif not self.view.seen:
            self.view.missing(NO_KEYBOARD, message)
            return
        self.away_banner.set_title(AWAY_BANNER.format(name or "keyboard"))
        self.away_banner.set_revealed(not here)
        self._show_switch(connected)
        # The route, not /dev/hidrawN, whose number floats; the node is in the tooltip.
        route = route_text(product, CABLED_IDS) if connected else None
        if connected and not answering:
            set_dot(self.state_dot, ASLEEP)
            self.state_row.set_subtitle(KEYBOARD_ASLEEP)
        elif connected:
            set_dot(self.state_dot, True)
            self.state_row.set_subtitle(route or str(device or ""))
        else:
            set_dot(self.state_dot,
                    ASLEEP if problem.get("code") == "keyboard_no_answer" else False)
            self.state_row.set_subtitle(capitalized(message))
        self.state_row.set_tooltip_text(str(device) if connected and device else None)
        # A keyboard that does not answer keeps its last reading, with when it was taken.
        show_battery(self.battery_row, self.battery_icon, battery if connected else None,
                     battery_at, cabled=route == ON_CABLE,
                     gone=not connected)

    def daemon_gone(self, why: str) -> None:
        """The daemon did not answer, for the reason `why`. The row says so instead of
        blaming the cable and the udev rule, since the keyboard may be plugged in and fine,
        and the banner goes down: with no daemon, no change is saved. Until a keyboard is
        seen, the status page says it."""
        if not self.view.seen:
            self.view.missing(DAEMON_WAIT, why)
            return
        self.away_banner.set_revealed(False)
        self._show_switch(False)
        set_dot(self.state_dot, False)
        self.state_row.set_subtitle(capitalized(why))
        self.state_row.set_tooltip_text(None)
        show_battery(self.battery_row, self.battery_icon, None, gone=True)

    def take_keyboards(self, status: dict, asked: str | None) -> None:
        """A status's keyboard models plugged in, for the switcher: a button for each by name,
        one switched off insensitive, with SWITCHED_OFF under its name, on the one the status
        gives the entries of, the tab's, as the window read it (Window.shown_on); shown while
        two or more are plugged in. `asked` is the keyboard the status read named, None for the
        leader's (Window.status_read). The one the tab showed gives way to the leader's, read
        at once (Window.keyboard_shown), once it is no longer plugged in, or once a read that
        named it gives another's entries (gives_way; the owner's decision of 2026-10-22). Then
        the Leader row and the follow switch (LightingCard.show_leader), and the board as they
        leave it."""
        listed = plugged_in(status)
        self._attached = [item["model"] for item in listed]
        self._switched_off = {index for index, item in enumerate(listed)
                              if item.get("switched_off") is True}
        shown = self.win.shown_on.model
        if gives_way(self.win.keyboard_shown, asked, shown, self._attached):
            # A status asked for the one gone gives its entries, and one asked for a model none
            # of whose keyboards has been set up since the daemon started the leader's
            # (Keyboards.shown_in): the leader's are read now, not at the next tick.
            self.win.keyboard_shown = None
            self.win.resync()
        self.switcher.show([key_name(key) for key in self._attached],
                           self._attached.index(shown) if shown in self._attached else None,
                           len(self._attached) >= 2, self._switched_off)
        self.light.show_leader(self._attached, self._switched_off, status)
        self.update_board()

    def on_page(self, index: int) -> None:
        """Show the keyboard picked: the window reads its status and its lighting from the
        files again (Window.keyboard_shown), and each change the tab sends names it once that
        read lands (Window.aimed). Nothing is sent to the daemon for the pick itself, so a
        command naming no keyboard still acts on the leader."""
        self.win.keyboard_shown = self._attached[index]
        self.win.resync()

    def show_capabilities(self, status: dict) -> None:
        """Draw the sections this keyboard has, and none of the others.

        The daemon probes the keyboard and names what it found: a G915 and a
        G213 share a protocol and little else (per-key and G keys, against five
        lighting areas). A section the keyboard lacks is hidden, not grayed out,
        which would say the feature is unavailable when it does not exist, and so
        is the Battery row of a keyboard with no battery. The M profile group shows
        for a keyboard with M keys or lighting, and says where its profiles switch
        (_show_switch).
        The board drawn is the model's, once the status names its product (show_board).
        """
        able = set(status.get("capabilities") or ())
        name = str(status.get("name") or KEYBOARD_TAB)
        # A group's title is markup, and the name is the daemon's.
        self.status_group.set_title(GLib.markup_escape_text(name))
        show_untested(self.untested_row, status.get("untested"))
        self.light.show_capabilities(able, status.get("dims") is not False)
        self.battery_row.set_visible("battery" in able)
        self._in_menu = m_profiles_in_menu(able)
        self._tray_shown = (status.get("tray") or {}).get("shown") is True
        self.profile_group.set_visible("m_keys" in able or self._in_menu)
        self._show_switch(True)
        self.show_key_control(status, able)
        # A game profile holds lighting and G-key bindings; with neither it holds nothing.
        self.game_group.set_visible(bool(able & {"lighting", *BOUND_KEY_CAPABILITIES}))
        self.light_group.set_visible("lighting" in able)
        self.color_group.set_visible("lighting" in able)
        board = board_of(status.get("product"))
        # Whether G keys map is the keyboard's to say, on a board with a key to bind: the
        # G213's has none, whatever features a keyboard of its id lists.
        drawn = board if board is not None else self.board.board
        mappable = bool(able & BOUND_KEY_CAPABILITIES) and bool(drawn.bindable_keys)
        # The board is for painting keys or areas and binding G keys; with none it is hidden.
        self.board_group.set_visible(bool(able & {"per_key", "areas"}) or mappable)
        self.map_toggle.set_visible(mappable)
        if not mappable:
            # Hidden and left on, it would hold the board in map mode with no G key to map.
            self.map_toggle.set_active(False)
        if board is not None and board is not self.board.board:
            self.show_board(board)

    def show_key_control(self, status: dict, able: set[str]) -> None:
        """The Game Mode row, on a keyboard that has it, and the Custom assignments row, on or
        off as the status gives them on a keyboard able to do `able`, which binds its own keys
        through them, or while its bindings wait for a test."""
        game = status.get("game_mode")
        shown = GAME_MODE_SHOWN.get(game) if isinstance(game, str) else None
        self.game_mode_row.set_visible(shown is not None)
        self.game_mode_row.set_subtitle(shown or "")
        shortcut = status.get("assignment_shortcut")
        untested = status.get("assignments_untested") is True
        state = status.get("custom_assignments")
        self.assignments_row.set_visible(untested or ("assigned_keys" in able
                                                      and isinstance(state, bool)))
        on = state is True
        self.assignments_row.set_subtitle(
            ASSIGNMENTS_UNTESTED if untested
            else ASSIGNMENTS_SHORTCUT.format(ASSIGNMENTS_SHOWN[on], shortcut,
                                             "off" if on else "on")
            if isinstance(shortcut, str)
            else ASSIGNMENTS_SHOWN[on])

    def _show_switch(self, connected: bool) -> None:
        """The M profile group's description (M_KEYS_SWITCH)."""
        self.profile_group.set_description(
            M_KEYS_SWITCH if not self._in_menu
            else NO_M_KEYS_SWITCH if connected and self._tray_shown else NO_M_KEYS_AWAY)

    def show_board(self, board: Board) -> None:
        """Draw `board`, the attached model's, in place of the board drawn, with the quick
        groups that have keys on it. A drag on the board it replaces ends unsaved."""
        self._stroke = None
        self._put_board(board)
        self.board_holder.set_child(self.board.widget)
        self._groups = {label: names for label, _what, names in quick_groups(board)}
        for label, names in self._groups.items():
            # The flow box's wrapper, or a hidden button would leave its gap in the row.
            cast(Gtk.FlowBoxChild, self._group_buttons[label].get_parent()).set_visible(
                bool(names))
        self.update_board()

    def _profile_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="M profile", description=M_KEYS_SWITCH)
        # The selected value as the subtitle: libadwaita's inline value label is
        # capped at 20 characters at any window width. The popover list stays capped.
        self.profile_row = Adw.ComboRow(title="Active", use_subtitle=True,
                                        model=Gtk.StringList.new([]))
        self.profile_row.connect("notify::selected", self.on_profile)
        group.add(self.profile_row)
        # On apply, not per keystroke: each is a rename sent to the daemon, which owns the name.
        self.name_row = Adw.EntryRow(title="Name", show_apply_button=True)
        self.name_row.connect("apply", self.on_name)
        group.add(self.name_row)

        # Plain, not destructive: every change made here is saved as it is
        # made, so this is for a file edited by hand.
        reload_row = Adw.ActionRow(
            title="Reload from disk",
            subtitle="Read the files again after editing one by hand")
        reload = Gtk.Button(label="Reload…", valign=Gtk.Align.CENTER)
        reload.connect("clicked", self.on_reload)
        reload_row.add_suffix(reload)
        group.add(reload_row)
        return group

    def _sample_button(self) -> Gtk.ToggleButton:
        """Sample, on the picker's row (the owner's request of 2026-09-24): the next key
        clicked gives the brush its color. Per-key only (update_board), one key at a time
        as an eyedropper is; Escape or a second click turns it off."""
        # A label alone, as Bind G keys, the board's other toggle: outside a header bar an
        # icon or a label, not both (the HIG's buttons).
        self.sample_button = Gtk.ToggleButton(
            label="Sample", valign=Gtk.Align.CENTER, visible=False,
            tooltip_text="Take the color of the next key you click")
        name_button(self.sample_button, "Sample a key's color into the brush")
        self.sample_button.connect("toggled", self.on_sample)
        return self.sample_button

    def _groups_row(self) -> Gtk.ListBoxRow:
        """The quick groups under the Brush row (the owner's decision of
        2026-09-24): a button per group paints its keys with the brush. Per-key
        only, as Sample is (update_board). A flow box wraps them on a narrow
        window, at most five to a line."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=10,
                      margin_bottom=10, margin_start=12, margin_end=12)
        heading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        heading.append(Gtk.Label(label="Groups", xalign=0))
        heading.append(Gtk.Label(label="Paint a group of keys with the brush", xalign=0,
                                 css_classes=["dimmed", "caption"]))
        box.append(heading)
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=6,
                           row_spacing=6, max_children_per_line=5, halign=Gtk.Align.START)
        for label, what, _names in QUICK_GROUPS:
            button = Gtk.Button(label=label, tooltip_text=f"Paint {what} with the brush")
            button.connect("clicked", lambda _b, g=label: self.on_group(self._groups[g]))
            self._group_buttons[label] = button
            flow.append(button)
            # Tab goes to the button, not to the flow box's wrapper around it, which
            # `append` just made its parent.
            cast(Gtk.FlowBoxChild, button.get_parent()).set_focusable(False)
        box.append(flow)
        # Tab goes on to the first button, as it does into the board.
        self.groups_row = Gtk.ListBoxRow(child=box, activatable=False, selectable=False,
                                         focusable=False, visible=False)
        return self.groups_row

    def _board_group(self) -> Adw.PreferencesGroup:
        # Keys, as the Mouse tab's group for the same job is Buttons: Keyboard names the tab.
        group = Adw.PreferencesGroup(title="Keys")
        self.map_toggle = Gtk.ToggleButton(
            label=BIND_G_KEYS, valign=Gtk.Align.CENTER,
            tooltip_text="Click a G key to choose what it sends")
        self.map_toggle.connect("toggled", self.on_map_mode)
        # Which layer every edit on the board lands in (show_layer), a row of its own as
        # on the Mouse tab's Buttons. Not markup: it names profiles.
        self.editing_row = Adw.ActionRow(title="Editing", use_markup=False)
        group.add(self.editing_row)
        # One sentence in the title, not a title and a suffix at the far end of a
        # 900px row: update_board writes the one for the mode, this is the startup
        # one. Not markup: the title names profiles.
        self.board_row = Adw.ActionRow(title=NOT_PER_KEY, use_markup=False)
        self.board_row.add_suffix(self.map_toggle)
        group.add(self.board_row)

        self._put_board(G915_BOARD)
        # Not a focus stop: the row does nothing and has no name, so Tab goes
        # straight to the board's keys.
        self.board_holder = Gtk.ListBoxRow(child=self.board.widget, activatable=False,
                                           selectable=False, focusable=False)
        group.add(self.board_holder)
        return group

    def _put_board(self, board: Board) -> None:
        """The drawn keyboard of `board` as the page's `board`, laid out once the page holds
        it, since its first layout calls back into the page."""
        self.board = KeyBoard(self.on_key_clicked, self.on_key_cleared, self.on_board_resize,
                              on_stroke_start=self.on_stroke_start,
                              on_stroke_key=self.on_stroke_key,
                              on_stroke_end=self.on_stroke_end,
                              description=AREA_BOARD_HELP if board.areas else BOARD_HELP,
                              board=board)
        self.board.place()

    # -- putting state on the rows ----------------------------------------

    def sync(self) -> None:
        """Show the window's state. Memory only: no disk, no daemon."""
        win = self.win
        self.refresh_profile_row()
        self.light.refresh_brightness()
        with self.loading:
            self.game.sync_game_row()
            self.game.show_programs()
            self.show_layer()
            self.game.show_unreadable(win.skipped_profiles)
            self.light.show_mode()
            self.update_board()

    def refresh_profile_row(self, force: bool = False) -> None:
        win = self.win
        labels = [profile_label(i, win.profile_names.get(i, "")) for i in PROFILE_IDS]
        name = win.profile_names.get(win.active, "")
        # A switch or a rename not answered yet holds its row (_unanswered). Read
        # before a new model, which moves the selection.
        shown = self.profile_row.get_selected()
        if labels != self._profile_labels:
            self._profile_labels = labels
            self.loading.put_model(self.profile_row, Gtk.StringList.new(labels))
        self.loading.put_selected(self.profile_row, shown if self._unanswered["profile"]
                                  else PROFILE_IDS.index(win.active))
        # A name being typed was for the profile shown: a switch drops it,
        # or a rename would give the new profile a name meant for the old.
        if (force or win.active != self._name_for
                or not (self._texts.typing(win, self.name_row) or self._unanswered["rename"])):
            # No loading guard: set_text never emits `apply`, the row's only handler (on_name).
            self._texts.show(self.name_row, name)
            self._name_for = win.active

    def show_layer(self) -> None:
        """Say which layer is edited, on the Editing row over the board, and have the Game
        profile group show what holds that layer."""
        self.editing_row.set_title(self.win.editing())
        self.game.show_layer()

    def _send_switch(self, row: Adw.SwitchRow, *, command: str, key: str) -> None:
        """Send the switch as `command` and read it back either way: a refusal
        puts it back once no other flip of it is out. `command` also names the
        window's field and its count in `_unanswered`, which holds the row
        while a flip is out; the reply carries the value under `key`. By
        keyword, so CommandTableTests finds the name."""
        if self.loading.active:
            return
        wanted = row.get_active()
        if wanted == getattr(self.win, command) and not self._unanswered[command]:
            return
        self._unanswered[command] += 1

        def done(reply: dict | None) -> None:
            self._unanswered[command] -= 1
            if reply is None:
                # A later flip still out is the user's last: its answer puts the row right.
                if not self._unanswered[command]:
                    with self.loading:
                        row.set_active(bool(getattr(self.win, command)))
            elif isinstance(reply.get(key), bool):
                # Taken now: a change back before the read-back lands is
                # compared with it.
                setattr(self.win, command, reply[key])
            self.win.resync()
        self.win.call({"command": command, "value": wanted}, done)

    def update_board(self) -> None:
        win = self.win
        # Sample is for a board of colors: Static has one, and map mode shows
        # the bindings. A keyboard that follows the leader paints nothing of its own, so its
        # board takes neither a click nor a drag outside map mode (LightingCard.show_following).
        following = win.shown_on.follows
        self.board_holder.set_sensitive(self.map_mode or not following)
        can_sample = win.lighting.mode == PER_KEY and not self.map_mode and not following
        self.sample_button.set_visible(can_sample)
        # The quick groups likewise, on a keyboard that paints single keys.
        self.groups_row.set_visible(can_sample and self.light.per_key)
        if self.sampling and not can_sample:
            self.sample_button.set_active(False)    # on_sample draws the rest
            return
        unread = win.profile_unread()
        if self.map_mode:
            # Nothing shown bound while the loaded profile is unread: the M
            # profile's bindings in its place read as that profile's.
            self.board.show_bindings({} if unread else
                                     binding_layer(win.game_profile(win.overlay),
                                                   win.config.profile))
        elif win.lighting.mode == PER_KEY:
            # A drag's keys too, or a redraw during one would take them off the
            # board until its release.
            self.board.show_colors(with_stroke(win.lighting.per_key, self._stroke or {}),
                                   win.lighting.background)
        elif win.lighting.mode in ANIMATIONS:
            self.board.show_animation(*self.shown_animation())
        else:
            self.board.show_colors({}, win.lighting.color)
        if unread and not self.sampling:
            title = UNREAD_PROFILE_BOARD.format(win.overlay)
        elif self.map_mode:
            # Named because a game profile's layer and the M profile's look the same on
            # screen, and binding into the wrong one is the easy mistake here. The M profile as
            # the Editing row names it: "Profile 1" alone reads as the mouse's onboard profile 1.
            overlay = win.game_profile(win.overlay)
            where = (overlay.name if overlay is not None
                     else profile_label(win.active, win.profile_names.get(win.active, "")))
            title = f"Click a G key to bind it in {where}"
        elif following:
            title = (AREA_FOLLOWING_BOARD if self.board.board.areas
                     else FOLLOWING_BOARD).format(followed(win.shown_on.leader))
        elif self.sampling:
            title = f"{SAMPLE_HELP}, or press Escape"
        elif win.lighting.mode == PER_KEY:
            title = AREA_PAINT_HELP if self.board.board.areas else PAINT_HELP
        elif win.lighting.mode in ANIMATIONS:
            # The board draws the animation, a likeness of it (show_animation): this says the
            # keyboard runs it, and that a click paints nothing.
            title = f"{mode_label(win.lighting.mode)} runs on the keyboard. {self.not_painted()}"
        else:
            title = self.not_painted()
        self.board_row.set_title(title)
        self.light.show_picker(self.sampling)
        self.board.set_sampling(self.sampling)
        self.refresh_css()

    def not_painted(self) -> str:
        """What the board says of a click outside Per-key: Per-area's words on a board lit by
        areas, as its Effect row names the choice."""
        return NOT_PER_AREA if self.board.board.areas else NOT_PER_KEY

    def shown_animation(self) -> tuple[Animation, str]:
        """The animation the window's lighting shows, for the drawn board (the owner's decision
        of 2026-10-24), and what the board says it shows to a screen reader: the effect, its
        colors, wave's direction and its speed where it takes one (SPEEDS), as the rows over
        and under it give them. One that takes none runs at its DEFAULT_PERIODS' period. A
        hand-set period outside SPEEDS' range is drawn at the nearest end of it. The logo's look
        is the model's shown (logo_steady)."""
        state = self.win.lighting
        mode = state.mode
        period = state.speeds.get(mode, DEFAULT_PERIODS[mode])
        colors = ("every color" if mode in COLORLESS
                  else f"{format_color(state.color)} and {format_color(state.second_color)}"
                  if mode in TWO_COLORED else format_color(state.color))
        way = f", {DIRECTION_WORDS[state.direction].lower()}" if mode == "wave" else ""
        pace = f", {speed_text(mode, period)}" if mode in SPEEDS else ""
        # An M profile's params can give a period outside SPEEDS' range, 0 among them, which a
        # frame divides by: the board draws at the nearest end, where the Speed slider sits.
        drawn = min(max(period, SPEEDS[mode][0]), SPEEDS[mode][1]) if mode in SPEEDS else period
        return (Animation(mode, state.color, state.second_color, drawn, state.direction,
                          logo_steady(mode, model_named(self.win.shown_on.model))),
                f"Shows {mode_label(mode)} in {colors}{way}{pace}.")

    def on_typed(self, _controller: Gtk.EventControllerKey, _keyval: int, keycode: int,
                 _state: Gdk.ModifierType, down: bool) -> bool:
        """A key pressed or let go anywhere in the window, `down` for a press: under ripple the
        drawn board ripples from it, as the keyboard does (KeyBoard.typed). Never taken: the key
        goes on to the widget it was typed in."""
        self.board.typed(keycode - 8, down)     # GDK's hardware keycode is evdev's + 8
        return False

    def on_keyboard_left(self, _controller: Gtk.EventControllerFocus) -> None:
        """The keyboard went to another window: the board holds no key typed here as down."""
        self.board.keyboard_left()

    def on_board_resize(self) -> None:
        """The board was scaled: in map mode the G keys' labels depend on the key size
        (BINDING_MIN_UNIT), so they are drawn again; update_board ends with refresh_css."""
        if self.map_mode:
            self.update_board()
        else:
            self.refresh_css()

    def refresh_css(self) -> None:
        state = self.win.lighting
        colors = {state.color, state.background, *self.win.saved_colors, *state.per_key.values()}
        self._css.update(stylesheet(self.board.unit, colors))
        self.light.show_background()

    def flush(self) -> bool:
        """Send what still settles on the tab, now, for the window's close. True when
        something was sent."""
        return self.light.flush()

    def close(self) -> None:
        self.light.close()
        self._css.destroy()

    # -- the M profile -----------------------------------------------------

    def on_profile(self, row: Adw.ComboRow, _p: GObject.ParamSpec) -> None:
        if self.loading.active:
            return
        index = PROFILE_IDS[row.get_selected()]
        if index == self.win.active and not self._unanswered["profile"]:
            return
        # A speed still settling goes first, on the layer it was set on.
        self.light.flush_speed()
        self._unanswered["profile"] += 1

        def done(reply: dict | None) -> None:
            # Resync either way: the read shows the profile switched to, or
            # puts the row back after a refusal, without waiting for a poll.
            self._unanswered["profile"] -= 1
            if reply is not None and reply.get("profile") in PROFILE_IDS:
                # Taken now: a pick back before the read-back lands is
                # compared with it.
                self.win.active = reply["profile"]
            say_not_shown(self.win.toast, reply)
            self.win.resync()
        self.win.call({"command": "profile", "value": index}, done)

    def on_name(self, row: Adw.EntryRow) -> None:
        """No loading guard: apply comes only from Enter or the apply button,
        never from set_text, so a guard could only drop a real one."""
        name = row.get_text().strip()
        if not name:
            self.win.toast(capitalized(M_PROFILE_NEEDS_A_NAME))
            self.refresh_profile_row(force=True)
            return
        self._texts.took(row)
        if (name == self.win.profile_names.get(self.win.active)
                and not self._unanswered["rename"]):
            return
        self._unanswered["rename"] += 1

        def done(reply: dict | None) -> None:
            self._unanswered["rename"] -= 1
            if (reply is not None and reply.get("profile") in PROFILE_IDS
                    and isinstance(reply.get("name"), str)):
                # Taken now: a name put back before the read-back lands is
                # compared with it.
                self.win.profile_names[reply["profile"]] = reply["name"]
            self.win.resync()
        self.win.call({"command": "rename", "value": name}, done)

    def on_reload(self, _b: Gtk.Button | None) -> None:
        """Ask first, and say what comes back.

        What it restores is not on screen, so the dialog names the profile:
        the loaded game profile when there is one, since that is the layer
        the editing commands write, and the M profile otherwise. Also the
        config banner's button and the main menu's Reload from disk….
        """
        # The daemon's _cmd_reload reads config.json in both cases, and the
        # game profile's own stages and rate follow its file (follow_tuning).
        folder = shown_path(config_dir())
        if self.win.overlay:
            source = f"the game profile {self.win.overlay}"
            where = (f"its file in {folder}/profiles/, the settings from "
                     f"{folder}/config.json,")
            mouse = " In host mode the mouse takes the DPI stages and rate in its file."
        else:
            source = profile_label(self.win.active,
                                   self.win.profile_names.get(self.win.active, ""))
            where = f"{folder}/config.json"
            mouse = ""
        confirm(self.win, "Reload from disk?",
                f"Reads the lighting and bindings of {source} again from {where} and "
                f"the macros from {folder}/macros/. "
                f"A setting such as mouse_host_mode takes effect.{mouse}",
                "Reload", self._do_reload, destructive=False)

    def _do_reload(self) -> None:
        def done(reply: dict | None) -> None:
            # Resync either way: it re-reads config.json and the library on
            # the worker, which is what the daemon has just gone back to, and
            # the Macros tab when the reload took a macro file anew.
            self.win.resync()
            if not reply:
                return
            if reply.get("overlay_error"):
                # The banner gives the file's error.
                self.win.toast(f"Kept {reply['overlay']}: its file could not be read")
            elif reply.get("overlay"):
                self.win.toast(f"Back to {reply['overlay']}")
            else:
                # As the profile row names it: "Profile 1" alone reads as the mouse's
                # onboard profile 1.
                self.win.toast(f"Back to {profile_label(reply['profile'], reply['profile_name'])}")
            say_not_shown(self.win.toast, reply)
        self.win.call({"command": "reload"}, done)

    # -- the board --------------------------------------------------------

    def on_defaults(self) -> bool:
        """Refuse a change while config.json, or the loaded game profile's
        file, could not be read (Window.held).

        The mode, colors and painted keys the window holds are then the
        defaults, or the last good read, of a layer the daemon may have left,
        and every lighting command is built from them and saved at once: one
        mode switch or paint stroke would replace the real per-key layout. This
        repeats the config banner's advice at the moment it matters. Held at
        the window's close, it keeps the window up (Window.held_back).
        """
        why = self.win.held()
        if why is None:
            return False
        self.win.held_back(why)
        return True

    def on_map_mode(self, button: Gtk.ToggleButton) -> None:
        self.map_mode = button.get_active()
        self.update_board()

    @property
    def dragging(self) -> bool:
        """A drag on a picker or on the board is between press and release, or a run of a
        picker's keys or a Speed move still settles: the window's reads wait for it
        (Window.dragging)."""
        return self.light.dragging or self._stroke is not None or self.light.speed_settling

    @property
    def sampling(self) -> bool:
        """Sample is on: the next key clicked gives the brush its color."""
        return self.sample_button.get_active()

    def on_sample(self, _button: Gtk.ToggleButton) -> None:
        """Sample turned on or off: the picker and the board say which."""
        self.update_board()

    def on_escape(self, *_args: object) -> bool:
        """Escape turns Sample off. While it is off, Escape is left to the rest."""
        if not self.sampling:
            return False
        self.sample_button.set_active(False)
        return True

    def _sample(self, zone: int) -> None:
        """Give the brush the color of the key at `zone`, and turn Sample off.

        The color is what the board shows on the key (Board.shown_colors): its own on the
        layer being edited, which is the loaded game profile's when there is one
        (`Window.adopt`), or the background where it has none, or on a board lit by areas
        its area's. Nothing is painted and nothing is sent: in Per-key mode the brush goes
        out only with a key it paints, as a drag on the picker's does
        (`LightingCard._preview`).
        """
        state = self.win.lighting
        state.color = self.board.board.shown_colors(by_zone(state.per_key),
                                                    state.background)[zone]
        self.light.show_brush()
        self.sample_button.set_active(False)        # on_sample redraws

    def _reached(self, zone: int) -> tuple[str, ...]:
        """The names of the keys a paint of the key at `zone` colors: its own, or on a board
        lit by areas each of its area's (Board.painted_with)."""
        return tuple(ZONE_NAMES[reached] for reached in self.board.board.painted_with(zone))

    def on_key_clicked(self, key: Key) -> None:
        if self.map_mode:
            if not key.bindable:
                self.win.toast(f"{key.label} {NOT_BINDABLE}; only "
                               f"{bindable_span(self.board.board)} can")
                return
            if self.on_defaults():
                return
            # The worker lets the dialog list the macros as well as capture a key.
            dialog = CaptureDialog(self.win, key.label, worker=self.win.worker)
            # The layer the board named at the click: the polls go on while the dialog is
            # open, and an M key, the tray or a program can move the window's meanwhile.
            layer = named_layer(self.win.layer)
            dialog.run(lambda spec: self._bind(key, spec, layer))
            return
        if key.zone is None:                # reserved: the board passes on no such key
            return
        if self.win.lighting.mode != PER_KEY:
            # Under ripple a click is for the ripple it starts (KeyBoard.begin_stroke, or
            # KeyBoard.typed from the keyboard); the board row says a click paints nothing.
            if self.win.lighting.mode != "ripple":
                self.win.toast(self.not_painted())
            return
        if self.sampling:
            self._sample(key.zone)
            return
        self.on_group(self._reached(key.zone))

    def on_key_cleared(self, key: Key) -> None:
        """A right click on a key, or the same from the keyboard on a focused key
        (`KeyBoard.clear_focused`): drop this key's own color, or on a board lit by
        areas the color of each key of its area, back to the background. Not while Sample
        waits for its key, as a right-drag is not (on_stroke_start): the click would save
        that key's color away."""
        if self.map_mode or self.sampling or key.zone is None or self.win.lighting.mode != PER_KEY:
            return
        painted = self.win.lighting.per_key
        names = [name for name in self._reached(key.zone) if name in painted]
        if not names or self.on_defaults():
            return
        for name in names:
            del painted[name]
        self.light.push_lighting()
        self.update_board()

    def on_stroke_start(self, clear: bool) -> bool:
        """A press on the board reached a second key, or Shift and an arrow
        began a drag from the keyboard: whether the drag may paint, or with
        `clear` clear."""
        if self.map_mode or self.sampling:
            return False
        if self.win.lighting.mode != PER_KEY:
            # A drag says what its click says (on_key_clicked): nothing for a clear, nor under
            # ripple, where the press ripples from its first key (KeyBoard.begin_stroke).
            if not clear and self.win.lighting.mode != "ripple":
                self.win.toast(self.not_painted())
            return False
        if self.on_defaults():
            return False
        self._stroke = {}
        self.refresh_css()                  # a class for the brush's color
        return True

    def on_stroke_key(self, key: Key, clear: bool) -> None:
        """A key the drag reached, or on a board lit by areas each key of its area: drawn in
        the brush, or the background for a clear, kept for the release, and shown on the
        keyboard.

        The keyboard gets a `preview` of the whole board as the drag has it so
        far, in the paint's shape, which the daemon shows and saves nowhere.
        The whole board, not the key: the worker replaces a `preview` still
        waiting with the next and drops the one before it. A board lit by areas gets none,
        since its areas show only as zone effects, which a preview never writes: the
        release's paint shows them.
        """
        if self._stroke is None or key.zone is None:
            return
        state = self.win.lighting
        color = None if clear else state.color
        self._stroke.update(dict.fromkeys(self._reached(key.zone), color))
        self.board.show_key(key, state.background if color is None else color)
        if not self.board.board.areas:
            request = paint_request(state.background, with_stroke(state.per_key, self._stroke))
            self.win.preview({**request, "command": "preview"}, self.light.on_preview)

    def on_stroke_end(self) -> None:
        """The drag's release: every key it reached, as one paint, saved
        once. Nothing is sent when no key changed: a right-drag over keys
        with no color of their own, a drag over keys in the brush already.
        While a change is held, by a read that failed mid-drag, nothing is
        saved: the stroke is dropped, the board drawn again without it, and
        the keyboard sent a preview of the board without it, as the picker's
        release sends the color it began on (LightingCard._commit), but for a board lit by
        areas, which the drag sent no preview (on_stroke_key)."""
        stroke, self._stroke = self._stroke, None
        if not stroke:
            return
        state = self.win.lighting
        painted = with_stroke(state.per_key, stroke)
        if painted != state.per_key:
            if not self.on_defaults():
                state.per_key = painted
                self.light.push_lighting()
            elif not self.board.board.areas:
                self.win.preview({**paint_request(state.background, state.per_key),
                                  "command": "preview"}, self.light.on_preview, while_held=True)
        self.update_board()

    def on_group(self, names: tuple[str, ...]) -> None:
        """Paint `names` in the brush: one paint, saved once."""
        if self.map_mode or self.win.lighting.mode != PER_KEY or self.on_defaults():
            return
        for name in names:
            self.win.lighting.per_key[name] = self.win.lighting.color
        self.light.push_lighting()
        self.update_board()

    def _bind(self, key: Key, spec: str | None, layer: dict) -> None:
        """Bind G key `key` under its `binds_as`, G515-F1 for a G515's F1, and mirror what
        the daemon says it wrote. The toast names the keycap, as the capture dialog does.

        `layer` is the one the click was made on, laid over the window's
        (Window.on_layer), so the daemon refuses the bind once the keyboard
        has left it (layer_changed). The mirror's layer comes from the reply
        rather than from `win.overlay`, which a file read can move between
        the click and the reply: the daemon names the profile it bound in.
        """
        binds_as = key.binds_as

        def done(reply: dict | None) -> None:
            if reply is None:
                return
            written = reply["binding"]
            name = reply.get("overlay")
            overlay = self.win.game_profile(name)
            mirrored = not name or overlay is not None
            # What the key did, which Undo binds again on the same layer whenever the bind
            # replaced or cleared it, as on the Mouse tab and the G keys.
            was = (binding_layer(overlay, self.win.config.profile).get(binds_as)
                   if mirrored else None)
            self.win.toast(f"{key.label} → {shown_binding(written)}" if written
                           else f"{key.label} unbound",
                           undo=(lambda: self._bind(key, was, layer))
                           if was and was != written else None)
            if not mirrored:
                # The daemon bound a profile this window has not read yet,
                # so read the files again rather than guess at the mirror.
                self.win.resync()
                return
            binding_layer(overlay, self.win.config.profile)[binds_as] = written
            self.update_board()
        self.win.call({"command": "bind", "key": binds_as, "value": spec, "shift": False,
                       "layer": layer}, done)
