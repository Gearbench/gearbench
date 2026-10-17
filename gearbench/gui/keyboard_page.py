"""The Keyboard tab: its status, the M profile and the board, around two cards it builds,
the Game profile group (game_card) and the lighting rows (lighting_card).

The window holds the state these rows show, read from the daemon and the files;
this page and its cards own the widgets and the handlers.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Protocol, cast

from gi.repository import Adw, GLib, GObject, Gtk

from ..config import PROFILE_IDS, Profile, shown_config_dir
from ..keyboard import CABLED_IDS
from ..keycodes import ZONE_NAMES
from ..layout import KEYS, Key
from ..lighting import ANIMATIONS
from ..profiles import PER_KEY, GameProfile
from .board import KeyBoard, shown_binding
from .dialogs import CaptureDialog
from .game_card import GameCard
from .lighting_card import (
    LIGHTING_MODES,
    MODE_LABELS,
    SAMPLE_HELP,
    LightingCard,
    paint_request,
)
from .style import (
    ASLEEP,
    DAEMON_WAIT,
    KEYBOARD_ASLEEP,
    DeviceView,
    EntryTexts,
    Styler,
    route_text,
    set_dot,
    show_battery,
    status_rows,
    stylesheet,
)
from .worker import LoadingGuard

if TYPE_CHECKING:                       # pragma: no cover
    from .window import Window

    class SendSwitch(Protocol):
        """`KeyboardPage._send_switch`, as the page hands it to a card."""

        def __call__(self, row: Adw.SwitchRow, *, command: str, key: str) -> None: ...

#: The board row's title while the loaded game profile's file cannot be read
#: (Window.profile_unread), when the window holds its changes to the layer (Window.held).
UNREAD_PROFILE_BOARD = ("The file of game profile {} could not be read, so no change is sent "
                        "to it. Fix it, then reload it.")
#: The status page's titles until a keyboard is first seen (DeviceView): before
#: the first reply, with the spinner, and over the reason once one says it is missing.
LOOKING = "Looking for the keyboard…"
NO_KEYBOARD = "Keyboard not available"
#: The banner while a keyboard seen once is away or not answering, with its model or
#: "keyboard".
AWAY_BANNER = "Changes are saved and will show when the {} is back"
#: The board row's title in Per-key mode.
PAINT_HELP = "Click or drag to color keys; right-click or right-drag to clear"
#: What a click or a drag on the board says outside Per-key mode.
NOT_PER_KEY = "Switch Lighting mode to Per-key to color keys"
#: The G-key toggle's label, which the Macros tab's description names.
MAP_G_KEYS = "Map G keys"
#: What the board says to a screen reader about its keys, which the board row says only for
#: the pointer. Not shown. Set once, whatever the mode, so it names by their labels the mode
#: and the toggle state in which Shift paints and Delete clears.
BOARD_HELP = ("Arrow keys move between the keys. Space or Enter does what a click does. In "
              f"{MODE_LABELS[LIGHTING_MODES.index(PER_KEY)]} mode with {MAP_G_KEYS} off, "
              "Shift with an arrow paints each key it reaches, and Delete or the Menu key "
              "clears a key.")


def _numbered(name: str, prefix: str) -> bool:
    """`name` is `prefix` and a number: F12, G5."""
    return name.startswith(prefix) and name[len(prefix):].isdigit()


def quick_groups(names: Sequence[str]) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    """The Groups row's buttons as (label, what it paints, zone names), from
    the lit keys' zone names `names`, each group in the order given.

    G HUB's eight quick color groups (picked by HID usage in its app.min.js)
    and the keypad and media keys, the owner's decision of 2026-09-24; the
    Interface and Combat groups are the owner's of 2026-09-25.
    """
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
        ("F-keys", "F1 to F12", pick(lambda n: _numbered(n, "F"))),
        ("G-keys", "G1 to G5", pick(lambda n: _numbered(n, "G"))),
        ("Modifiers", "Ctrl, Shift, Alt and Super on both sides",
         pick(lambda n: n.startswith(("LEFT ", "RIGHT ")))),
        ("Arrows", "the four arrow keys", pick(lambda n: n in ("UP", "DOWN", "LEFT", "RIGHT"))),
        ("Keypad", "the number pad and Num Lock",
         pick(lambda n: n == "NUMLOCK" or n.startswith("KEYPAD "))),
        ("Media", "Previous, Play, Next and Mute",
         pick(lambda n: n in ("PREVIOUS", "PLAY/PAUSE", "NEXT", "MUTE"))),
        ("Logo", "the logo", pick(lambda n: n == "LOGO")),
        ("All keys", "every lit key but the logo", pick(lambda n: n != "LOGO")),
    )


#: The Groups row's buttons, over the keys the board draws with a light.
QUICK_GROUPS = quick_groups([ZONE_NAMES[key.zone] for key in KEYS if key.zone is not None])


def binding_layer(overlay: GameProfile | None,
                  profile: Profile) -> dict[str, str | None]:
    """The G-key bindings in force: the loaded game profile's when one is
    loaded, the M profile's own otherwise.

    The daemon resolves a G key press and binds the same way, so the board
    shows what a key sends and an edit lands where the daemon put it. The
    board reads and writes through this one function so the two agree.
    """
    return profile.keys if overlay is None else overlay.bindings


def binding_where(overlay: GameProfile | None, profile: Profile) -> str:
    """The name of the layer a bind lands in, as `binding_layer` picks it.

    Shown on the board row: the two layers look the same on screen, and
    binding into the wrong one is the easy mistake here.
    """
    return overlay.name if overlay is not None else profile.name


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
        #: since that write may land after it (Holds.unchanged, the Headset tab's).
        self._unanswered: Counter[str] = Counter()
        #: What the Active row lists, so its model is rebuilt only when it changes.
        self._profile_labels: list[str] = []
        self.map_mode = False
        #: The tab's LoadingGuard, handed to its cards.
        self.loading = LoadingGuard()

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
        # The board right under the Mode row: after the color rows it would start
        # about 180 px further down the tab.
        for group in (self.status_group, self.profile_group, self.game_group,
                      self.light_group, self.board_group, self.color_group):
            content.append(group)
        # Up while the keyboard is away or not answering, saying once that changes
        # are saved and applied when it is back. A top bar, as on the Mouse tab, so
        # it stays on screen however far the tab is scrolled; this tab therefore
        # scrolls itself.
        self.away_banner = Adw.Banner()
        # A status page in place of the rows until a keyboard is seen.
        self.view = DeviceView(content, "input-keyboard-symbolic", LOOKING)
        self.widget = Adw.ToolbarView(content=self.view.stack)
        self.widget.add_top_bar(self.away_banner)
        # Escape turns Sample off wherever the focus is on the tab (on_escape).
        escape = Gtk.ShortcutController()
        escape.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"),
                                             Gtk.CallbackAction.new(self.on_escape)))
        self.widget.add_controller(escape)

    def _status_group(self) -> Adw.PreferencesGroup:
        """The same shape as the Mouse tab's: what it is, and whether it is there."""
        group = Adw.PreferencesGroup(title="G915")
        self.state_row, self.state_dot, self.battery_row, self.battery_icon = status_rows(group)
        return group

    def take_status(self, connected: bool, device: str | None, battery: dict | None, *,
                    answering: bool = True, problem: dict | None = None,
                    name: str | None = None, product: str | None = None) -> None:
        """What the status poll saw of the keyboard itself.

        `answering` is the daemon's word on an attached keyboard: switched
        off behind its receiver it stays attached. `problem` is the first of
        the status's `problems` about the keyboard; a receiver with nothing
        answering is amber, as a keyboard not answering is, since switching it
        on is the likely fix. `name` is the model, for the banner.

        Until a status says a keyboard is connected, the tab is the status
        page (DeviceView), and the banner stays down: there is nothing on it
        to change.

        The percentage is from Logitech's curve for this keyboard, and the
        daemon names the state so the device tabs share one vocabulary
        (battery.py); `gearbench-ctl status` shows the voltage.
        """
        here = connected and answering
        if connected:
            self.view.found()
        elif not self.view.seen:
            self.view.missing(NO_KEYBOARD, str((problem or {}).get("message", "")))
            return
        self.away_banner.set_title(AWAY_BANNER.format(name or "keyboard"))
        self.away_banner.set_revealed(not here)
        code = (problem or {}).get("code")
        if connected and not answering:
            set_dot(self.state_dot, ASLEEP)
            self.state_row.set_subtitle(KEYBOARD_ASLEEP)
        elif connected:
            set_dot(self.state_dot, True)
            # The route, not /dev/hidrawN, whose number floats; the node is in the tooltip.
            self.state_row.set_subtitle(route_text(product, CABLED_IDS) or str(device or ""))
        else:
            set_dot(self.state_dot, ASLEEP if code == "keyboard_no_answer" else False)
            message = str((problem or {}).get("message", ""))
            self.state_row.set_subtitle(message[:1].upper() + message[1:])
        self.state_row.set_tooltip_text(str(device) if connected and device else None)
        show_battery(self.battery_row, self.battery_icon, battery if here else None)

    def daemon_gone(self, why: str) -> None:
        """The daemon did not answer, for the reason `why`. The row says so instead of
        blaming the cable and the udev rule, since the keyboard may be plugged in and fine,
        and the banner goes down: with no daemon, no change is saved. Until a keyboard is
        seen, the status page says it."""
        if not self.view.seen:
            self.view.missing(DAEMON_WAIT, why)
            return
        self.away_banner.set_revealed(False)
        set_dot(self.state_dot, False)
        self.state_row.set_subtitle(why[:1].upper() + why[1:])
        self.state_row.set_tooltip_text(None)
        show_battery(self.battery_row, self.battery_icon, None)

    def show_capabilities(self, status: dict) -> None:
        """Draw the sections this keyboard has, and none of the others.

        The daemon probes the keyboard and names what it found: a G915 and a
        G213 share a protocol and little else (per-key and G keys, against one
        lighting zone). A section the keyboard lacks is hidden, not grayed out,
        which would say the feature is unavailable when it does not exist.
        """
        able = set(status.get("capabilities") or ())
        # A group's title is markup, and the name is the daemon's.
        self.status_group.set_title(
            GLib.markup_escape_text(str(status.get("name") or "Keyboard")))
        self.light.show_capabilities(able)
        self.profile_group.set_visible("m_keys" in able)
        # A game profile holds lighting and G-key bindings; with neither it holds nothing.
        self.game_group.set_visible(bool(able & {"lighting", "g_keys"}))
        self.light_group.set_visible("lighting" in able)
        self.color_group.set_visible("lighting" in able)
        # The board is for painting keys and binding G keys; with neither it is hidden.
        self.board_group.set_visible(bool(able & {"per_key", "g_keys"}))
        self.map_toggle.set_visible("g_keys" in able)

    def _profile_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="M profile", description="M1, M2 and M3 switch these on the keyboard")
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
        self.sample_button = Gtk.ToggleButton(
            child=Adw.ButtonContent(icon_name="color-select-symbolic", label="Sample"),
            valign=Gtk.Align.CENTER, visible=False,
            tooltip_text="Take the color of the next key you click")
        self.sample_button.update_property([Gtk.AccessibleProperty.LABEL],
                                           ["Sample a key's color into the brush"])
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
        for label, what, names in QUICK_GROUPS:
            button = Gtk.Button(label=label, tooltip_text=f"Paint {what} with the brush")
            button.connect("clicked", lambda _b, n=names: self.on_group(n))
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
        group = Adw.PreferencesGroup(title="Keyboard")
        self.map_toggle = Gtk.ToggleButton(
            label=MAP_G_KEYS, valign=Gtk.Align.CENTER,
            tooltip_text="Click a G key to choose what it sends")
        self.map_toggle.connect("toggled", self.on_map_mode)
        # One sentence in the title, not a title and a suffix at the far end of a
        # 900px row: update_board writes the one for the mode, this is the startup
        # one. The subtitle names the layer every edit on the board lands in
        # (show_layer); a row of its own would push the board down the tab. Not
        # markup: the title and the subtitle name profiles.
        self.board_row = Adw.ActionRow(title=NOT_PER_KEY, use_markup=False)
        self.board_row.add_suffix(self.map_toggle)
        group.add(self.board_row)

        self.board = KeyBoard(self.on_key_clicked, self.on_key_cleared, self.on_board_resize,
                              on_stroke_start=self.on_stroke_start,
                              on_stroke_key=self.on_stroke_key,
                              on_stroke_end=self.on_stroke_end, description=BOARD_HELP)
        self.board.place()
        # Not a focus stop: the row does nothing and has no name, so Tab goes
        # straight to the board's keys.
        group.add(Gtk.ListBoxRow(child=self.board.widget, activatable=False,
                                 selectable=False, focusable=False))
        return group

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
            win.mouse.follow_keyboard()
            self.light.rebuild_saved()
            self.update_board()

    def refresh_profile_row(self, force: bool = False) -> None:
        win = self.win
        labels = [f"M{i} · {win.profile_names.get(i, '')}" for i in PROFILE_IDS]
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
        """Say which layer is edited, on the board's row, and have the Game profile group
        show what holds that layer."""
        self.board_row.set_subtitle(self.win.editing())
        self.game.show_layer()

    def _send_switch(self, row: Adw.SwitchRow, *, command: str, key: str) -> None:
        """Send the switch as `command` and read it back either way: a refusal
        puts it back. `command` also names the window's field and the
        unanswered count show_layer and show_lock read; the reply carries the
        value under `key`. By keyword, so CommandTableTests finds the name."""
        if self.loading.active:
            return
        wanted = row.get_active()
        if wanted == getattr(self.win, command) and not self._unanswered[command]:
            return
        self._unanswered[command] += 1

        def done(reply: dict | None) -> None:
            self._unanswered[command] -= 1
            if reply is None:
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
        # the bindings.
        can_sample = win.lighting.mode == PER_KEY and not self.map_mode
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
        else:
            self.board.show_colors({}, win.lighting.color)
        if unread and not self.sampling:
            title = UNREAD_PROFILE_BOARD.format(win.overlay)
        elif self.map_mode:
            where = binding_where(win.game_profile(win.overlay), win.config.profile)
            title = f"Click a G key to bind it in {where}"
        elif self.sampling:
            title = f"{SAMPLE_HELP}, or press Escape"
        elif win.lighting.mode == PER_KEY:
            title = PAINT_HELP
        elif win.lighting.mode in ANIMATIONS:
            # The board is drawn in the Static color, which is breathe's and
            # ripple's and the logo's under wave, and not what cycle or wave
            # shows on the keys: this says the animation runs there.
            label = MODE_LABELS[LIGHTING_MODES.index(win.lighting.mode)]
            title = f"{label} runs on the keyboard. {NOT_PER_KEY}"
        else:
            title = NOT_PER_KEY
        self.board_row.set_title(title)
        self.light.show_picker(self.sampling)
        self.board.set_sampling(self.sampling)
        self.refresh_css()

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
            self.win.resync()
        self.win.call({"command": "profile", "value": index}, done)

    def on_name(self, row: Adw.EntryRow) -> None:
        """No loading guard: apply comes only from Enter or the apply button,
        never from set_text, so a guard could only drop a real one."""
        name = row.get_text().strip()
        if not name:
            self.win.toast("A profile needs a name")
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
        config banner's button.
        """
        # The daemon's _cmd_reload reads config.json in both cases, and the
        # game profile's own stages and rate follow its file (follow_tuning).
        folder = shown_config_dir()
        if self.win.overlay:
            source = f"the game profile {self.win.overlay}"
            where = (f"its file in {folder}/profiles/, the settings from "
                     f"{folder}/config.json,")
            mouse = " In host mode the mouse takes the DPI stages and rate in its file."
        else:
            source = f"M{self.win.active} · {self.win.profile_names.get(self.win.active, '')}"
            where = f"{folder}/config.json"
            mouse = ""
        dialog = Adw.AlertDialog(
            heading="Reload from disk?",
            body=f"Reads the lighting and bindings of {source} again from {where} and "
                 f"the macros from {folder}/macros/, for a file edited by hand. "
                 f"A setting such as mouse_host_mode takes effect as if set here.{mouse}")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("reload", "Reload")
        dialog.set_default_response("reload")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _d, r: self._do_reload(r))
        dialog.present(self.win)

    def _do_reload(self, response: str) -> None:
        if response != "reload":
            return

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
                self.win.toast(f"Back to M{reply['profile']} · {reply['profile_name']}")
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
        """A picker drag or a drag on the board is between press and release, or a Speed move
        still settles: the window's reads wait for it (Window.dragging)."""
        return (self.light.picker.dragging or self._stroke is not None
                or self.light.speed_settling)

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

    def _sample(self, name: str) -> None:
        """Give the brush a key's color, and turn Sample off.

        The color is what the board shows on the key: its own on the layer
        being edited, which is the loaded game profile's when there is one
        (`Window.adopt`), or the background where it has none. Nothing is
        painted and nothing is sent: in Per-key mode the brush goes out only
        with a key it paints, as a drag on the picker's does (`LightingCard._preview`).
        """
        self.win.lighting.color = self.win.lighting.per_key.get(name,
                                                                self.win.lighting.background)
        self.light.show_brush()
        self.sample_button.set_active(False)        # on_sample redraws

    def on_key_clicked(self, key: Key) -> None:
        if self.map_mode:
            if not key.bindable:
                self.win.toast(f"{key.label} is not remappable; only G1-G5 are")
                return
            if self.on_defaults():
                return
            # The worker lets the dialog list the macros as well as capture a key.
            dialog = CaptureDialog(self.win, key.label, worker=self.win.worker)
            # The layer the board named at the click: the polls go on while the dialog is
            # open, and an M key, the tray or a program can move the window's meanwhile.
            profile, overlay = self.win.layer
            layer = {"profile": profile, "overlay": overlay}
            dialog.run(lambda spec: self._bind(key.label, spec, layer))
            return
        if key.zone is None:                # reserved: the board passes on no such key
            return
        if self.win.lighting.mode != PER_KEY:
            self.win.toast(NOT_PER_KEY)
            return
        name = ZONE_NAMES[key.zone]
        if self.sampling:
            self._sample(name)
            return
        if self.on_defaults():
            return
        self.win.lighting.per_key[name] = self.win.lighting.color
        self.light.push_lighting()
        self.update_board()

    def on_key_cleared(self, key: Key) -> None:
        """Right-click, or Delete, BackSpace, Shift+F10 or Menu on a focused
        key (`KeyBoard.clear_focused`): drop this key's own color, back to
        the background. Not while Sample waits for its key, as a right-drag
        is not (on_stroke_start): the click would save that key's color away."""
        if self.map_mode or self.sampling or key.zone is None or self.win.lighting.mode != PER_KEY:
            return
        name = ZONE_NAMES.get(key.zone)
        if name not in self.win.lighting.per_key or self.on_defaults():
            return
        del self.win.lighting.per_key[name]
        self.light.push_lighting()
        self.update_board()

    def on_stroke_start(self, clear: bool) -> bool:
        """A press on the board reached a second key, or Shift and an arrow
        began a drag from the keyboard: whether the drag may paint, or with
        `clear` clear. In Per-key mode only, not in map mode, and not while
        Sample waits for its one key."""
        if self.map_mode or self.sampling:
            return False
        if self.win.lighting.mode != PER_KEY:
            if not clear:
                self.win.toast(NOT_PER_KEY)
            return False
        if self.on_defaults():
            return False
        self._stroke = {}
        self.refresh_css()                  # a class for the brush's color
        return True

    def on_stroke_key(self, key: Key, clear: bool) -> None:
        """A key the drag reached: drawn in the brush, or the background for
        a clear, kept for the release, and shown on the keyboard.

        The keyboard gets a `preview` of the whole board as the drag has it so
        far, in the paint's shape, which the daemon shows and saves nowhere.
        The whole board, not the key: the worker replaces a `preview` still
        waiting with the next and drops the one before it.
        """
        if self._stroke is None or key.zone is None:
            return
        name = ZONE_NAMES[key.zone]
        state = self.win.lighting
        color = None if clear else state.color
        self._stroke[name] = color
        self.board.show_key(key, state.background if color is None else color)
        request = paint_request(state.background, with_stroke(state.per_key, self._stroke))
        self.win.preview({**request, "command": "preview"}, self.light.on_preview)

    def on_stroke_end(self, _clear: bool) -> None:
        """The drag's release: every key it reached, as one paint, saved
        once. Nothing is sent when no key changed: a right-drag over keys
        with no color of their own, a drag over keys in the brush already.
        While a change is held, by a read that failed mid-drag, nothing is
        saved: the stroke is dropped, the board drawn again without it, and
        the keyboard sent a preview of the board without it, as the picker's
        release sends the color it began on (LightingCard._commit)."""
        stroke, self._stroke = self._stroke, None
        if not stroke:
            return
        state = self.win.lighting
        painted = with_stroke(state.per_key, stroke)
        if painted != state.per_key:
            if not self.on_defaults():
                state.per_key = painted
                self.light.push_lighting()
            else:
                self.win.preview({**paint_request(state.background, state.per_key),
                                  "command": "preview"}, self.light.on_preview, while_held=True)
        self.update_board()

    def on_group(self, names: tuple[str, ...]) -> None:
        """A Groups button: its keys in the brush, one paint, saved as a
        click is."""
        if self.map_mode or self.win.lighting.mode != PER_KEY or self.on_defaults():
            return
        for name in names:
            self.win.lighting.per_key[name] = self.win.lighting.color
        self.light.push_lighting()
        self.update_board()

    def _bind(self, key: str, spec: str | None, layer: dict) -> None:
        """Bind a G key, and mirror what the daemon says it wrote.

        `layer` is the one the click was made on, laid over the window's
        (Window.on_layer), so the daemon refuses the bind once the keyboard
        has left it (layer_changed). The mirror's layer comes from the reply
        rather than from `win.overlay`, which a file read can move between
        the click and the reply: the daemon names the profile it bound in.
        """
        def done(reply: dict | None) -> None:
            if reply is None:
                return
            written = reply["binding"]
            self.win.toast(f"{key} → {shown_binding(written)}" if written else f"{key} unbound")
            name = reply.get("overlay")
            overlay = self.win.game_profile(name)
            if name and overlay is None:
                # The daemon bound a profile this window has not read yet,
                # so read the files again rather than guess at the mirror.
                self.win.resync()
                return
            binding_layer(overlay, self.win.config.profile)[key] = written
            self.update_board()
        self.win.call({"command": "bind", "key": key, "value": spec, "shift": False,
                       "layer": layer}, done)
