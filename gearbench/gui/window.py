"""The window: the state its four tabs read, and the wiring to the daemon.

The daemon holds the 0x8071 claim, so the window never writes to the keyboard. It reads live
state, which the M keys and the CLI also change, from the control socket, and the files for
what status does not carry, a game profile's per-key map and the M profile's own lighting, on
the worker thread: a library scan parses every profile file.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple, cast

from gi.repository import Adw, Gtk

from ..battery import kept_at
from ..config import PER_KEY, PROFILE_IDS, Config, parse_color, unusable
from ..daemon import KEYBOARD_REQUESTS, LAYER_EDITS, UNKNOWN, LayerChanged
from ..desktop import APP_NAME, DESKTOP_ENTRY as APP_ID
from ..keyboard import shown_brightness
from ..library_files import by_name, slug
from ..lighting import ANIMATIONS
from ..profiles import GameProfile, Library, mouse_color_json, profiles_dir
from ..settings_files import ConfigError, FileChanged, FileUnreadable
from ..wording import NO_REASON, REFUSED_HEADING, capitalized
from .a50x_page import (
    NAMES as A50X_NAMES,
    PAGE as A50X_PAGE,
    TITLE as A50X_TITLE,
    A50XPage,
)
from .daemon_banner import DaemonBanner
from .dialogs import track_keys_down
from .extension_banner import ExtensionBanner, shell_for
from .g432_page import (
    NAME as G432_NAME,
    NAMES as G432_NAMES,
    PAGE as G432_PAGE,
    G432Page,
)
from .headset_page import HeadsetPage
from .headset_stack import HeadsetStack
from .keyboard_page import KeyboardPage, keyboard_problem
from .lighting_state import BEFORE_STATUS, LightingState, ShownOn, adopt_lighting
from .macro_banner import MacroBanner
from .macro_page import MacrosPage
from .main_menu import MainMenu
from .mouse_page import MousePage
from .polls import Polls
from .profile_manager import ProfileManager
from .spatial_group import SpatialGroup
from .style import (
    BASE_SHEET,
    HEADSET_TAB,
    KEYBOARD_TAB,
    MOUSE_TAB,
    Styler,
    displayable,
    editing_text,
    refusal_line,
    take_typing_on_clicks,
)
from .worker import (
    DOWN,
    REPLY_WAIT,
    DaemonWorker,
    Reply,
    Result,
    daemon_down,
    failure,
    named_layer,
    unreachable,
)

#: What the uinput banner adds after the daemon's no_virtual_keyboard or no_virtual_mouse
#: problem, after "; ": a one-line text the window shows ends with no period (the user's
#: decision of 2026-10-27, D22).
SEE_GUIDE = "see the user guide"
#: The uinput banner's problems: with both in a status, it says the first.
UINPUT_PROBLEMS = ("no_virtual_keyboard", "no_virtual_mouse")
#: The refusals that are a problem with a file (ConfigError.kind), for the config banner.
CONFIG_KINDS = (FileChanged.kind, FileUnreadable.kind)
#: Layer changes sent while held: built from the mouse's own reading, not the window's copy.
SENT_WHILE_HELD = frozenset(("mouse_dpi_stages", "mouse_rate"))
#: The window's status read, its poll's and resync's, while the Keyboard tab shows the leader's
#: keyboard; Window.status_read names another the tab shows.
STATUS_READ = {"command": "status"}
#: The config banner's title while the window itself could not read the file.
NOT_READ_TITLE = ("config.json could not be read ({}), so no binding, keyboard color or "
                  "keyboard effect change is sent; fix it, then reload it")
#: The toast for a change held while config.json is unreadable (held), from the Keyboard tab
#: or the Mouse tab; short, since the config banner says the rest and stays up.
ON_DEFAULTS = "Not sent: config.json could not be read; fix it, then reload it"
#: The same while the loaded game profile's file cannot be read. It names no profile:
#: held_back always puts it in a toast, and a profile's name has no length limit (TOAST_MAX).
ON_UNREAD_PROFILE = "Not sent: the game profile's file could not be read; fix it, then reload it"
#: The config banner's button, the Keyboard tab's Reload from disk.
RELOAD_LABEL = "Reload…"
#: The button on a toast whose act can be taken back (toast's `undo`).
UNDO = "Undo"
#: What a close says while it waits for the changes on their way (_on_close).
CLOSING = "Closing once the last changes are sent"


class Seen(NamedTuple):
    """What the last good status gave that, once it moves, reads the files again (_moved)."""

    pid: object
    lighting: object
    bindings: object
    animations: tuple[str, ...]


@functools.cache
def _library(directory: Path) -> Library:
    """One Library per folder, so its SkipWarnings says a bad file once. Worker thread only."""
    return Library(directory)


def read_files() -> tuple[Config | Exception, list[GameProfile], list[tuple[str, str]]]:
    """config.json, or why it could not be read, the game profiles, and the profile files
    left out, unreadable or shadowed by another of the same name, as (file name, why). For
    the worker thread."""
    library = _library(profiles_dir())
    profiles = library.all()
    config, error = first_config()
    return config if error is None else error, profiles, library.skipped


def first_config() -> tuple[Config, Exception | None]:
    """config.json to open on, or the defaults and why it could not be read, as the daemon does."""
    try:
        return Config.load(), None
    except (ConfigError, OSError) as exc:
        return Config(), exc


def saved_colors_from(raw: object) -> list[int] | None:
    """The saved colors in a reply, less any that do not parse, or None if it carried none."""
    if not isinstance(raw, list):
        return None
    colors = []
    for value in raw:
        try:
            colors.append(parse_color(value, "saved_colors"))
        except ConfigError:
            continue
    return colors


def takes_presses(widget: Gtk.Widget) -> bool:
    """Whether `widget` has a GestureSingle of its own that is not touch-only: a button, an
    entry's text, a switch, a row, a scrollbar, the board's keys, the picker. GtkScrolledWindow's
    pan and swipe are touch-only.

    GTK says to use observe_controllers sparingly, since its list model is kept up to date
    while it lives; this one lives for one check, once a press on a tab.
    """
    return any(isinstance(gesture, Gtk.GestureSingle) and not gesture.get_touch_only()
               for gesture in widget.observe_controllers())


def blank_at(area: Gtk.Widget, x: float, y: float) -> bool:
    """Whether a press at (x, y) in `area` lands on nothing that takes it.

    It does not if the widget under the pointer, or one it is inside short of `area`, takes
    presses (takes_presses) or is a list row that activates. A list box's or flow box's own
    gesture is left out, since what it does is its children's (the gaps between the Groups
    buttons take nothing), and so is a row with no gesture of its own that does not activate,
    such as a plain GtkListBoxRow: its list box moves the focus to it at the release unless
    the focus is inside it already. An AdwActionRow has a click gesture of its own, activatable
    or not (libadwaita 1.9.1), so a press on one is never blank. Being focusable says nothing,
    since GtkScrolledWindow and GtkListBox are and hold every row; insensitive widgets count,
    so a click on a disabled button does nothing.
    """
    widget = area.pick(x, y, Gtk.PickFlags.INSENSITIVE)
    if widget is None:
        return False
    while widget is not area:
        if isinstance(widget, Gtk.ListBoxRow) and widget.get_activatable():
            return False
        if not isinstance(widget, (Gtk.ListBox, Gtk.FlowBox)) and takes_presses(widget):
            return False
        # `pick` gave `area` or a widget inside it, so the way up leads to `area`.
        widget = cast(Gtk.Widget, widget.get_parent())
    return True


class Window(Adw.ApplicationWindow):
    """The state the Keyboard, Mouse, Headset and Macros tabs read, the
    status poll, and the daemon wiring."""

    def __init__(self, app: Adw.Application | None) -> None:
        super().__init__(application=app, title=APP_NAME,
                         default_width=940, default_height=820)
        #: The keys held in the window, which a dialog opening over it keeps from the dialog
        #: until they come up (dialogs.swallow_held_at_open).
        self.keys_down = track_keys_down(self)
        #: The binding mirror and the M profiles' own lighting, read on the worker after this
        #: first load. While `config_error`, why the last read failed, is set, `config` is the
        #: defaults or the last good read, maybe of a layer no longer in force, and no change
        #: to the layer is sent (call, KeyboardPage.on_defaults): each is built from this copy
        #: and saved at once.
        self.config, self.config_error = first_config()
        #: Game profiles as they were last read, rebuilt on the worker.
        self.library: list[GameProfile] = []
        #: The profile files that read left out, as (file name, why).
        self.skipped_profiles: list[tuple[str, str]] = []
        self.worker = DaemonWorker()
        self._tick_pending = False
        #: A round trip failed since the last good status, so the next one re-reads the files:
        #: a restarted daemon comes up on them under the same profile and overlay names.
        self._lost_contact = False
        #: The daemon's pid, the lighting and bindings digests and the keyboard's animations from
        #: the last good status. A change in any re-reads the files: a restart between polls, the
        #: CLI or a `reload` changed them under the window, whose next key click would save its
        #: stale copy as `paint`. The window's own `color`, `effect` and `paint` put their reply's
        #: digest here when made on the one held (send_lighting), so its own change is not read
        #: back.
        self._seen: Seen | None = None
        #: The `macros` digest the last good status gave (take_macros).
        self._macros_seen: str | None = None
        #: The daemon's config_changed and config_unreadable messages in the last good status
        #: (_moved): a fix it reloaded from outside moves no digest.
        self._files_seen: list[str] | None = None
        #: `color`, `effect` and `paint` requests not answered yet: a file read done before one
        #: must not replace the copy it was built from (reload_files).
        self._lighting_unanswered = 0
        #: The lighting the daemon holds as far as the window knows: the last read (adopt),
        #: then each change it took. A refused change is taken back to it (send_lighting).
        self._known: LightingState | None = None
        #: `profile_processes` lists not answered yet, as (profile name, processes), oldest
        #: first (send_processes). A file read lands with them put back (reload_files), or the
        #: Game profiles dialog's next edit would save the old list whole.
        self._processes_unanswered: list[tuple[str, list[str]]] = []
        #: The Game profiles dialog's mouse colors not answered yet, as (profile name, color),
        #: oldest first (send_mouse_color), put back as the lists are: a mouse color moves no
        #: digest, so no poll would read the files again.
        self._mouse_colors_unanswered: list[tuple[str, int | None]] = []
        #: A read is owed: one landed while a lighting change was unanswered, or was due
        #: mid-drag (resync, absorb, reload_files). It runs once neither holds.
        self._read_again = False

        #: Live state as the daemon reports it.
        self.active = self.config.active
        self.brightness = shown_brightness(self.config.brightness, None, None)[0]
        self.levels: list[int] = [0, 12, 25, 50, 100]
        self.profile_names = {i: p.name for i, p in self.config.profiles.items()}
        self.saved_colors = list(self.config.saved_colors)
        #: The lighting the Keyboard tab shows, and changes as its rows and board are used:
        #: one object for as long as the window is open, filled from each read
        #: (LightingState.take). A placeholder until the first read.
        self.lighting = LightingState.placeholder()
        self.overlay: str | None = None
        #: The game profile loaded, from `status`, for the focus row: `overlay` has it only
        #: once the files are read.
        self.status_overlay: str | None = None
        #: A running program loaded the game profile, rather than a hand.
        self.overlay_auto = False
        #: The program that loaded it, from status's auto_process, for status_overlay.
        self.auto_process: str | None = None
        #: The daemon's Keep loaded setting, or None until a status gives it.
        self.keep_loaded: bool | None = None
        #: A game profile a choice made by hand holds off, and the program it
        #: waits on (None when the daemon did not say), or None.
        self.held_off: tuple[str, str | None] | None = None
        #: How many hold-offs besides held_off; Load it now releases all.
        self.held_off_more = 0
        #: What game profiles follow, from `status`'s `focus`: (source,
        #: focused program, the profile it binds), or None until a status gives
        #: it. Source "gnome-shell" or "xwayland" is the focused window.
        self.focus: tuple[str, str | None, str | None] | None = None
        #: The daemon's Lights off when locked setting, and what the desktop lacks for it,
        #: in the daemon's words.
        self.lock_lights = False
        self.lock_unavailable: str | None = None

        self.styler = Styler()
        # The rules every tab's widgets take, loaded before any page is built.
        self.styler.update(BASE_SHEET)
        self.toasts = Adw.ToastOverlay()
        header = Adw.HeaderBar()
        self._build_banners()
        #: One toast per distinct message, while it is up (toast).
        self._toasts: dict[str, Adw.Toast] = {}
        #: The Game profiles dialog while it is open (manage_profiles).
        self.profiles_dialog: ProfileManager | None = None
        #: The messages a dialog is open for (explain), so none is opened twice.
        self._explaining: set[str] = set()

        self._build_pages()
        #: The status, mouse and headset polls, run only while the window is on screen.
        self.polls = Polls(self, self._tick)
        header.set_title_widget(Adw.ViewSwitcher(stack=self.stack,
                                                 policy=Adw.ViewSwitcherPolicy.WIDE))
        self.menu = MainMenu(self)
        header.pack_end(self.menu.build())
        self.stack.connect("notify::visible-child-name", self.polls.on_tab)
        self.headset_tab.stack.connect("notify::visible-child-name", self.polls.on_page)

        self._lay_out(header)
        self.take_config({})

        # Shown from the file first; the daemon's reply corrects it.
        self.refresh()
        self.resync()
        self.mouse.refresh(ask=False)
        self.headset.refresh(ask=False)
        self._watch_presence()

    #: The close waits for the changes on their way (_on_close).
    _flushing = False
    #: A change was refused or held while it waited, or found no daemon or a socket it could
    #: not use: the window stays up to say so.
    _flush_refused = False
    #: The toast that says the close waits, taken down if the window stays.
    _closing_toast: Adw.Toast | None = None
    #: Requests sent through `call` and not answered yet, which a close waits for (writing).
    _calls_out = 0
    #: The layer the window's copy of the lighting is of, as (M profile, game profile or None),
    #: which every change to the layer names (on_layer). Set in adopt; none before the first
    #: read, so the daemon refuses a change built from the placeholder state.
    layer: tuple[int | None, str | None] = (None, None)
    #: The animations the attached keyboard has, ANIMATIONS' order, from the status's
    #: `effects` (_take_devices); every one until a status names them.
    animations: tuple[str, ...] = ANIMATIONS
    #: The keyboard the lighting shows on, from the last status (_take_devices).
    shown_on = BEFORE_STATUS
    #: The color the daemon gives a synced mouse, from the last status's `mouse_sync_base`
    #: (_take_devices); None before a status gives it.
    sync_base: int | None = None
    #: The keyboard model the Keyboard tab shows, picked on its switcher, by key; None for the
    #: leader's (KeyboardPage.on_page). The status read names it (status_read); the daemon's
    #: own pick of the leader is not moved by it.
    keyboard_shown: str | None = None
    #: The keyboard model the window's copy of the lighting is of, by key, as the status it
    #: was adopted with named it (adopt); None before any. Each change built from the copy
    #: names it (aimed), as each change to the layer names `layer`: a pick, an unplug or a
    #: new leader moves the tab before the copy is read again.
    lighting_of: str | None = None
    #: A G HUB file or the pack is being read (import_dialog.read_then_show).
    import_reading = False

    def _build_banners(self) -> None:
        """The banners over the tabs, each down when built."""
        self.daemon_banner = DaemonBanner(self.worker, self.resync, self.set_status)
        # Up while the daemon has no virtual keyboard or mouse. No button: the daemon retries
        # every 30 s (VIRTUAL_RETRY) and at the next bind, and the fix (the udev rule or the
        # uinput module) is outside the window.
        self.uinput_banner = Adw.Banner(use_markup=False)
        # Up while another program that drives these devices runs (apps.OTHER_TOOLS), a line
        # each in the daemon's words: no markup. No button: each is quit outside the window.
        self.tools_banner = Adw.Banner(use_markup=False)
        # Up while config.json or the loaded game profile's file cannot be read, or one of them
        # or a macro's was edited on disk: nothing is saved over it until a reload. Not
        # markup: the title carries the file's path and the error's words.
        self.config_banner = Adw.Banner(button_label=RELOAD_LABEL, use_markup=False)
        self.config_banner.connect("button-clicked", lambda _b: self.keyboard.on_reload(None))
        self.extension_banner = ExtensionBanner(shell_for(self.get_application()))
        self.macro_banner = MacroBanner(self.call, self._tick, self.is_suspended)

    def _build_pages(self) -> None:
        """The four tabs and the stack that shows one of them."""
        self.mouse = MousePage(self, self.worker, self.call, self.toast,
                               self.set_status, self.kb_base, self.refused)
        self.spatial = SpatialGroup(self.worker, self.toast)
        self.headset = HeadsetPage(self, self.worker, self.call, self.toast, self.set_status)
        self.g432 = G432Page(self, self.worker, self.call, self.toast, self.kb_base)
        self.headset_tab = HeadsetStack(self.headset.widget, self.headset.page,
                                        self.spatial.widget)
        self.headset_tab.add(G432_PAGE, G432_NAME, self.g432.widget, self.g432.page,
                             G432_NAMES, self.g432.refresh, self.g432.tick)
        self.a50x = A50XPage(self.worker, self.call, self.toast)
        self.headset_tab.add(A50X_PAGE, A50X_TITLE, self.a50x.widget, self.a50x.page,
                             A50X_NAMES, self.a50x.refresh, self.a50x.refresh)
        self.keyboard = KeyboardPage(self)
        self.macros = MacrosPage(self)
        # After the Keyboard tab, whose stylesheet holds the swatches' rules.
        self.rebuild_saved()

        self.stack = Adw.ViewStack()
        # Bare pages: Adw.PreferencesPage clamps content to about 600px, narrower than the
        # keyboard, and each device tab scrolls itself under its pinned banner, so a scroller
        # here would nest.
        self.stack.add_titled_with_icon(self.keyboard.widget, "keyboard", KEYBOARD_TAB,
                                        "input-keyboard-symbolic")
        self.stack.add_titled_with_icon(self.mouse.widget, "mouse", MOUSE_TAB,
                                        "input-mouse-symbolic")
        self.stack.add_titled_with_icon(self.headset_tab.widget, "headset", HEADSET_TAB,
                                        "audio-headset-symbolic")
        # Bare as well: its library and steps scroll in their own lists, Save in a bar under them.
        # An icon of its own, a list of steps in order: none shared with a row.
        self.stack.add_titled_with_icon(self.macros.widget, "macros", "Macros",
                                        "view-list-ordered-symbolic")
        # Always the Keyboard tab first, whatever is attached, and no memory of the last tab
        # (the owner's decision of 2026-09-24). A device not seen yet has a status page on its
        # tab (DeviceView).
        self.stack.set_visible_child_name("keyboard")

    def _lay_out(self, header: Adw.HeaderBar) -> None:
        """The header, the banners and the tabs, top to bottom, under the toasts."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(header)
        box.append(self.daemon_banner.banner)
        box.append(self.uinput_banner)
        box.append(self.tools_banner)
        box.append(self.config_banner)
        box.append(self.extension_banner.banner)
        box.append(self.macro_banner.banner)
        box.append(self.stack)
        self.toasts.set_child(box)
        self.set_content(self.toasts)

    def _watch_presence(self) -> None:
        """The signals that start and stop the polls, the close, the press on a blank part
        of a tab, and a press outside the spin row being typed in."""
        self.connect("notify::is-active", self.polls.on_presence)
        self.connect("notify::suspended", self.polls.on_presence)
        self.connect("map", self.polls.on_presence)
        self.connect("unmap", self.polls.on_presence)
        self.connect("close-request", self._on_close)
        blank = Gtk.GestureClick(propagation_phase=Gtk.PropagationPhase.BUBBLE)
        blank.connect("pressed", self._on_blank_press)
        self.stack.add_controller(blank)
        take_typing_on_clicks(self)

    def _on_close(self, _w: Gtk.Window) -> bool:
        """Let go of everything that outlives the widget tree.

        A timeout source holds a reference to the window and worker.call is a no-op once
        stopped, so a tick left running would latch the polls' pending flags and keep the
        window alive; the display holds the CSS providers likewise.

        Unsaved macro edits are asked about first (MacrosPage.asks_before_close). Settling
        values (a speed, idle times, a mouse's DPI, a picker key run, a headset setting) are
        sent now, and the close waits for changes on their way (writing), not for polls,
        since stopping the
        worker drops what is queued. A change refused or held meanwhile (held_back) keeps the
        window up to say so, and a second close closes at once (the owner's decisions of
        2026-09-28). So does a change that found no daemon or a socket it could not use
        (set_status), under its banner: it never got there. A read that finds either does not.
        A dialog opened while the close waits keeps the window up with it (_flushed).
        """
        if self.macros.asks_before_close(self.close):
            return True
        if not self._flushing:
            # Set before the flushes, so a change one of them holds counts as refused (held_back).
            self._flushing, self._flush_refused = True, False
            if any([self.keyboard.flush(), self.mouse.flush(),
                    *(page.flush() for page in self.headset_pages)]) or self.writing:
                # Up until the close ends or the window stays (_flushed): a mouse write
                # can take up to 30 s.
                self._closing_toast = self.toast(CLOSING, timeout=0)
                # The worker answers in order, so this lands after their replies.
                self.worker.run(lambda: None, self._flushed)
                return True
        self.worker.stop()
        self.polls.stop()
        self.macro_banner.close()
        self.extension_banner.close()
        self.styler.destroy()
        self.keyboard.close()
        self.mouse.close()
        for part in self.headset_parts:
            part.close()
        return False

    def _flushed(self, _result: Result) -> None:
        if not self._flush_refused and self.writing:
            # Sent after the marker: a mouse write's next step, or the request
            # and the list read that settle a macro save or delete that timed out.
            self.worker.run(lambda: None, self._flushed)
            return
        self._flushing = False
        # Down first: a refusal queued behind it shows at once, and a discard
        # prompt the close raises keeps the window.
        if self._closing_toast is not None:
            self._closing_toast.dismiss()
            self._closing_toast = None
        if self._flush_refused:
            # Said on a window that stays up; the read after it puts the row
            # back, and the next close asks about unsaved macro edits again.
            self.macros.close_kept()
            return
        if self.get_visible_dialog() is not None:
            # A dialog opened while the close waited: libadwaita answers close() by closing
            # that dialog alone. Both stay, and the next close closes the window.
            return
        self.close()

    @property
    def writing(self) -> bool:
        """A change is still out, which the close waits for: a request sent through `call`,
        the Mouse and Headset tabs' writes included, or one a page runs itself or has not
        yet settled (its `busy` or `writing`). Polls are not."""
        return bool(self._calls_out or any(part.busy for part in self.headset_parts)
                    or self.macros.writing)

    @property
    def headset_pages(self) -> tuple[HeadsetPage | G432Page | A50XPage, ...]:
        """The Headset tab's pages, each built in _build_pages: the close sends what each
        still settles (flush)."""
        return (self.headset, self.g432, self.a50x)

    @property
    def headset_parts(self) -> tuple[HeadsetPage | SpatialGroup | G432Page | A50XPage, ...]:
        """The Headset tab's pages and its Spatial sound group: the close waits for each
        one's `busy` (writing) and lets go of each."""
        return (*self.headset_pages, self.spatial)

    def manage_profiles(self, expand: str | None = None) -> None:
        """The Game profiles dialog, opened on the profile `expand` names when
        it names one, and the files read again for it. One at a time."""
        if self.profiles_dialog is None:
            manager = ProfileManager(self)
            manager.dialog.connect("closed", self._profiles_closed)
            self.profiles_dialog = manager
            manager.dialog.present(self)
        if expand:
            self.profiles_dialog.expand(expand)
        self.resync()

    def _profiles_closed(self, _dialog: Adw.Dialog) -> None:
        self.profiles_dialog = None

    # -- shared services --------------------------------------------------

    def toast(self, message: str, detail: str | None = None, *,
              full: str | None = None, timeout: int = 4,
              heading: str | None = None,
              undo: Callable[[], object] | None = None) -> Adw.Toast:
        """Say `message` for `timeout` seconds (0: until dismissed), once however often it
        is said.

        A toast already up is added again, which restarts its timeout. Plain text, since a
        title parses markup and would hide a macro named "Copy & paste". `detail`, the raw text
        behind a refusal or a text to copy, such as an address, puts a Details button on it
        that opens a dialog, which shows `detail` in a label that can be selected; so does
        `full`, a note or a refusal too long for a toast, shown under `heading`, or under
        `message` when none is given, with `detail` below it. `undo` puts an Undo button on
        it, in place of Details, that runs it once; a toast of the same words already up is
        then another act's, so it goes and this one takes its place. A lone surrogate shows
        as U+FFFD (displayable).
        """
        overlay = self.toast_overlay()
        key = f"{id(overlay)}\0{message}\0{full or ''}\0{detail or ''}"
        shown = self._toasts.get(key)
        if shown is not None and undo is not None:
            shown.dismiss()
            shown = None
        if shown is None:
            label = UNDO if undo is not None else "Details" if detail or full else None
            shown = Adw.Toast(title=displayable(message), timeout=timeout,
                              use_markup=False, button_label=label)
            # Only its own: one dismissed late must not take its successor's place.
            shown.connect("dismissed", lambda _t: self._toasts.pop(key, None)
                          if self._toasts.get(key) is shown else None)
            if undo is not None:
                shown.connect("button-clicked", lambda _t: undo())
            elif full:
                shown.connect("button-clicked", lambda _t: self.explain(
                    full, detail, heading=heading or message.rstrip(".")))
            elif detail:
                shown.connect("button-clicked", lambda _t: self.explain(message, detail))
            self._toasts[key] = shown
        overlay.add_toast(shown)
        return shown

    def toast_overlay(self) -> Adw.ToastOverlay:
        """Where a message goes: the Game profiles dialog's own overlay while it is open, or
        the A50's Load preset to headset dialog's, since the window's is under the dialog."""
        if self.profiles_dialog is not None:
            return self.profiles_dialog.toasts
        presets = self.headset.library.toast_overlay()
        return presets if presets is not None else self.toasts

    def explain(self, message: str, detail: str | None = None,
                heading: str = REFUSED_HEADING) -> None:
        """A refusal too long for a toast, or its raw text, in a dialog; with
        `heading`, a note too long for one or a failure under its own heading
        (an import's). A lone surrogate shows as U+FFFD (displayable)."""
        if message in self._explaining:
            return
        dialog = Adw.AlertDialog(heading=displayable(heading),
                                 body=displayable(capitalized(message)))
        if detail:
            dialog.set_extra_child(Gtk.Label(label=displayable(detail), wrap=True,
                                             selectable=True, xalign=0,
                                             css_classes=["dimmed", "monospace"]))
        dialog.add_response("close", "OK")
        dialog.connect("closed", lambda _d: self._explaining.discard(message))
        self._explaining.add(message)
        dialog.present(self)

    def held_back(self, why: str) -> None:
        """Say why a change was not sent (held). One held while the close
        waits counts as refused: the window stays up to say so."""
        if self._flushing:
            self._flush_refused = True
        self.toast(why)

    def refused(self, reply: dict) -> None:
        """Say a refused command where it can be read in full: a file the daemon will not write
        over on the config banner, and over the Game profiles dialog as well while it is open,
        since the dialog sits over the banner; anything else in a toast, whole up to
        TOAST_MAX, else its first clause, or REFUSED_HEADING when that is too long too, with
        the whole behind Details, the daemon's raw text under it (the owner's decision of
        2026-10-22). A layer_changed refusal also reads status and the files again, to take
        the layer its sentence names."""
        message = capitalized(str(reply.get("error") or NO_REASON))
        detail = str(reply["detail"]) if reply.get("detail") else None
        if self._flushing:
            self._flush_refused = True
        kind = reply.get("kind")
        if kind in CONFIG_KINDS:
            self.show_config_problem(message)
        if kind not in CONFIG_KINDS or self.profiles_dialog is not None:
            # A layer_changed sentence too: the profile names in it have no length limit.
            line = refusal_line(message, REFUSED_HEADING)
            if line is None:
                self.toast(message, detail)
            else:
                self.toast(line, detail, full=message, heading=REFUSED_HEADING)
        if kind == LayerChanged.kind:
            self.resync()

    def show_config_problem(self, text: str) -> None:
        """The config banner up with `text`, capitalized, lone surrogates made displayable: a
        key or a link's target in an error can hold one."""
        self.config_banner.set_title(displayable(capitalized(text)))
        self.config_banner.set_revealed(True)

    def take_config(self, status: dict) -> None:
        """The config banner: the window's own failed read of config.json
        first, then what the daemon's `problems` say of config.json, the
        loaded game profile's file or a macro's, and down when nothing does."""
        if self.config_error is not None:
            self.show_config_problem(NOT_READ_TITLE.format(unusable(self.config_error)))
            return
        problem = next((p for p in status.get("problems") or () if p["code"] in CONFIG_KINDS), None)
        if problem is None:
            self.config_banner.set_revealed(False)
        else:
            self.show_config_problem(problem["message"])

    def set_status(self, reachable: bool, error: Exception | None = None, *,
                   poll: bool = False) -> None:
        """The daemon banner: shown at a failure, titled for what `failure` makes of `error`,
        and hidden at a good status; each tab says whether its device is there. Every failure
        also marks the files to be read again at the next good status.

        `poll` marks a read, which carries no change: one that finds no daemon or a socket it
        cannot use does not keep a closing window up, since a Spatial sound action, which does
        not go through the daemon, may be all the close waits for.
        """
        if not reachable:
            self._lost_contact = True
            if self._flushing and not poll and failure(error) in (DOWN, UNKNOWN):
                # A change that found no daemon, or a socket the connect could not use: it
                # never got there, and the window stays to say so.
                self._flush_refused = True
            self.daemon_banner.show(error)
        else:
            self.daemon_banner.hide()

    def call(self, request: dict,
             callback: Callable[[dict | None], None] | None = None,
             unanswered: Callable[[Exception], None] | None = None, *,
             timeout: float = REPLY_WAIT) -> None:
        """Send `request` off the main thread. `callback(reply)` runs on the main thread with
        the reply, or None once the failure is shown: the banner for no answer, `refused` for a
        refusal. `timeout` is how long the worker waits for the reply, longer for a request
        that can wait out the mouse's own timeouts.

        `unanswered(error)`, when given, runs instead of `callback(None)` for a round trip that
        raised: a refused command was not carried out, but one that timed out may have been,
        so a caller that must know reads the result back. A change to the layer names its
        layer (on_layer), and is held, as refused, while the window has no copy of that layer
        (held), unless it is the mouse's stages or report rate (SENT_WHILE_HELD).
        """
        command = request.get("command")
        if (command in LAYER_EDITS and command not in SENT_WHILE_HELD
                and (why := self.held(command)) is not None):
            self.held_back(why)
            if callback is not None:
                callback(None)
            return
        self._calls_out += 1

        def done(reply: Reply) -> None:
            self._calls_out -= 1
            taken: dict | None = None
            if isinstance(reply, Exception):
                self.set_status(False, reply)
                if unanswered is not None:
                    unanswered(reply)
                    return
            elif not reply.get("ok"):
                self.refused(reply)
            else:
                taken = reply
            if callback is not None:
                callback(taken)
        self.worker.call(self.aimed(request), done, timeout=timeout)

    def preview(self, request: dict, callback: Callable[[Reply], None], *,
                while_held: bool = False) -> None:
        """Send a `preview`, which the worker replaces with the next `preview`
        while it is the last one queued, naming the layer as `call` does.
        Nothing while a change is held (held), and the release says why, unless
        `while_held`: a preview that puts back what the keyboard showed before a
        held change saves nothing."""
        if while_held or self.held() is None:
            self.worker.preview(self.aimed(request), callback)

    def held(self, command: str | None = None) -> str | None:
        """Why a change to the layer, or `command`, is not sent, or None.

        While config.json could not be read, the window's copy is the defaults or a read of a
        layer the daemon may have left; while the loaded game profile's file cannot be read
        (profile_unread), it has no copy of that profile. `rename` names the M profile alone,
        so an unread game profile does not hold it.
        """
        if self.config_error is not None:
            return ON_DEFAULTS
        if command != "rename" and self.profile_unread():
            return ON_UNREAD_PROFILE
        return None

    def profile_unread(self) -> bool:
        """The daemon keeps a game profile loaded whose file the window's
        last read could not use, so its library lacks it."""
        return bool(self.overlay) and self.game_profile(self.overlay) is None

    def status_read(self) -> dict:
        """The window's status read: STATUS_READ, with the keyboard the Keyboard tab shows
        (keyboard_shown), whose entries the reply then gives, or the leader's where none of
        its model's keyboards has been set up since the daemon started (Keyboards.shown_in),
        which KeyboardPage.take_keyboards then gives way to."""
        shown = self.keyboard_shown
        return dict(STATUS_READ) if shown is None else {**STATUS_READ, "keyboard": shown}

    def aimed(self, request: dict) -> dict:
        """`request` as `call` and `preview` send it: with the layer the window's copy is of
        (on_layer), and, for a request that takes one (the daemon's KEYBOARD_REQUESTS), the
        keyboard its row shows, unless it names one already: for `brightness`, whose row each
        status fills, the one the last status was of (shown_on); for the rest, the keyboard
        the window's copy of the lighting is of (lighting_of)."""
        request = self.on_layer(request)
        command = request.get("command")
        if command in KEYBOARD_REQUESTS and "keyboard" not in request:
            keyboard = self.shown_on.model if command == "brightness" else self.lighting_of
            if keyboard is not None:
                request = {**request, "keyboard": keyboard}
        return request

    def on_layer(self, request: dict) -> dict:
        """`request` with the layer the window's copy is of, when it changes the layer in
        force (the daemon's LAYER_EDITS), which the daemon refuses (LayerChanged) once that
        layer is no longer in force: an M key, a program, the tray's menu or the CLI can
        switch it between polls. A `layer` the request carries already, the one an input or
        a dialog was opened on, is laid over the window's."""
        if request.get("command") not in LAYER_EDITS:
            return request
        return {**request, "layer": {**named_layer(self.layer), **(request.get("layer") or {})}}

    def send_lighting(self, request: dict) -> None:
        """Send `color`, `effect` or `paint`, built from the window's copy of the lighting and
        saved by the daemon as they arrive.

        The reply's `lighting`, the digest the change left, is taken as seen, so the window's
        own change does not read the files again and drop a key clicked meanwhile. It is taken
        only when the reply's `was`, the digest the change was made on, is the one held: a CLI
        change since the last status is in it too and was never read.

        A change refused or held (call) is taken back to `_known` and drawn once no other is
        out, since the read after it takes nothing while a file does not parse. One that timed
        out may have been made, and is left to that read. Either way it is read back (resync),
        or what the daemon refused would stay drawn as if saved.
        """
        self._lighting_unanswered += 1
        sent = self.lighting.copy()

        def done(reply: dict | None, refused: bool = True) -> None:
            self._lighting_unanswered -= 1
            if reply is not None:
                self._known = sent
                digest = reply.get("lighting")
                if (isinstance(digest, str) and self._seen is not None
                        and reply.get("was") == self._seen.lighting):
                    self._seen = self._seen._replace(lighting=digest)
            elif refused and not self._lighting_unanswered and self._known is not None:
                self.lighting.take(self._known)
                self.refresh()
            if reply is None or (self._read_again and not self._lighting_unanswered):
                self.resync()
        self.call(request, done, lambda _error: done(None, refused=False))

    def send_processes(self, profile: GameProfile, processes: list[str],
                       said: str | None) -> None:
        """Show `profile`'s new program list at once, and send it: the daemon writes the file,
        on its own copy when the profile is the loaded one. A refusal reads the files again,
        which puts the list the daemon kept back. `said` is toasted once the daemon has taken
        the list.

        The reply goes on the copy as it is then, and only when no later list for the profile
        is still out: a file read landing first replaces the profile's object, and an earlier
        reply is older than what shows.
        """
        profile.processes = list(processes)
        sent = (profile.name, list(processes))
        self._processes_unanswered.append(sent)
        self.refresh()

        def done(reply: dict | None) -> None:
            self._processes_unanswered.remove(sent)
            if reply is None:
                self.resync()
                return
            kept = reply.get("processes")
            current = self.game_profile(sent[0])
            later = any(slug(name) == slug(sent[0]) for name, _ in self._processes_unanswered)
            if (isinstance(kept, list) and current is not None and not later
                    and kept != current.processes):
                current.processes = [str(p) for p in kept]
                self.refresh()
            if said:
                self.toast(said)
        self.call({"command": "profile_processes", "value": profile.name,
                   "processes": list(processes)}, done)

    def send_mouse_color(self, profile: GameProfile, color: int | None) -> None:
        """Show `profile`'s mouse color `color`, or with None none of its own, at once, here
        and through refresh on each picker that follows the keyboard where the profile is
        loaded (follow_keyboard), and send it: the daemon writes the file, on its own copy
        when the profile is the loaded one. A refusal, or no answer, reads the files again,
        which puts back the color it kept; a file read done before the reply keeps it
        (reload_files)."""
        profile.mouse_color = color
        sent = (profile.name, color)
        self._mouse_colors_unanswered.append(sent)
        self.refresh()

        def done(reply: dict | None) -> None:
            self._mouse_colors_unanswered.remove(sent)
            if reply is None:
                self.resync()
        self.call({"command": "profile_mouse_color", "value": profile.name,
                   "color": mouse_color_json(color)}, done)

    def kb_base(self) -> int:
        """The color the daemon gives a synced mouse and a synced headset's lights
        (Daemon.synced_color): the loaded game profile's mouse color, in the window's copy of
        the library, where it has one. Else on the leader's own page it is the window's copy,
        which a change there moves at once: the whole-board color in static mode and under an
        animation, the background under the keys in per-key. While the copy is of another keyboard (lighting_of), it is
        the last status's (sync_base), since the daemon cuts the leader's lighting to the
        leader; the copy's again with a daemon that gives none."""
        if (own := self.loaded_mouse_color()) is not None:
            return own
        if self.sync_base is not None and not self.copy_is_leaders():
            return self.sync_base
        light = self.lighting
        return light.background if light.mode == PER_KEY else light.color

    def follow_keyboard(self) -> None:
        """Put the keyboard's color (kb_base) on each picker that follows it: the Mouse tab's
        while the mouse syncs, the headset Lighting group's while its lights follow."""
        self.mouse.follow_keyboard()
        self.g432.follow_keyboard()

    def copy_is_leaders(self) -> bool:
        """Whether the window's copy of the lighting is of the keyboard that leads, the one a
        synced mouse follows (Keyboard.sync_devices): true too before a status names either."""
        leader = self.shown_on.leader
        return leader is None or self.lighting_of in (None, leader)

    def game_profile(self, name: str | None) -> GameProfile | None:
        """A game profile from the cached library, by name."""
        return by_name(self.library, name) if name else None

    def loaded_mouse_color(self) -> int | None:
        """The loaded game profile's mouse color in the cached library, which a synced mouse
        shows in place of the keyboard's; None with none loaded or none of its own."""
        loaded = self.game_profile(self.overlay)
        return None if loaded is None else loaded.mouse_color

    def took_rename(self, was: str, now: str) -> None:
        """Put the daemon's new name for the game profile `was` on the copy as it is now, not on
        the object the rename was sent from: a file read that landed first replaced it, still
        under `was`. The loaded one's name moves with it."""
        current = self.game_profile(was)
        if current is not None:
            current.name = now
        if self.overlay is not None and slug(self.overlay) == slug(was):
            self.overlay = now

    @property
    def dragging(self) -> bool:
        """A drag on the board or on a Keyboard or Mouse tab picker is between press and
        release, or a run of such a picker's keys or a Speed move still settles."""
        return self.keyboard.dragging or self.mouse.dragging

    # -- the status poll --------------------------------------------------

    def resync(self) -> None:
        """Read the daemon and the files again, then redraw.

        Mid-drag the read is owed instead (`_read_again`), or it would change the copy and the
        layer the release is built on. A refused release reads it at once, or the next tick does.
        """
        if self.dragging:
            self._read_again = True
            return
        self._read_again = False        # a read landing mid-reply owes one again
        request = self.status_read()
        self.worker.call(request, lambda status: self.absorb(status, force=True,
                                                             asked=request.get("keyboard")))

    def _on_blank_press(self, _gesture: Gtk.GestureClick, _presses: int,
                        x: float, y: float) -> None:
        """A press on a blank part of a tab drops the focus, as on a web page (the owner's
        decision of 2026-09-25); GTK leaves it in place.

        One gesture on the tab stack, in the bubble phase, sees a press after the widget under
        the pointer. A press claimed at once, as an entry's text does, never reaches it; most
        are claimed at the release or never, so it asks `blank_at` rather than take an
        unclaimed press for a blank one. It never claims the press and acts on the press
        alone, so what the widget does with it, drags included, is unchanged.
        """
        if blank_at(self.stack, x, y):
            self.set_focus(None)

    def _tick(self) -> None:
        # Never mid-drag: adopting then moves the marker out from under the
        # pointer, and the release commits the adopted color instead of the
        # one the user was on.
        if self.dragging or self._tick_pending:
            return
        if self._read_again and not self._lighting_unanswered:
            self.resync()
            return
        self._tick_pending = True
        request = self.status_read()
        self.worker.call(request, lambda status: self._on_tick(status, request.get("keyboard")))

    def _on_tick(self, status: Reply, asked: str | None = None) -> None:
        self._tick_pending = False
        self.absorb(status, force=False, asked=asked)

    def absorb(self, status: Reply, force: bool, asked: str | None = None) -> None:
        """Take what a status reply carries.

        The files are read again when the daemon's state moved under the window (_moved),
        when `force` asks for it after a command, and on the first good status after any
        failure reported to set_status; the macro files then too, and when the `macros`
        digest moved (take_macros). `asked` is the keyboard the read named (status_read),
        None for the leader's: the Keyboard tab judges the keyboard it shows by a read that
        named it (KeyboardPage.take_keyboards).
        """
        if isinstance(status, Exception):
            self._take_silence(status)
            return
        if not status.get("ok"):
            return
        if self._lost_contact:
            # A restarted daemon came up on the files; the macro files are read too, since a
            # window opened before then has no digest to compare.
            self._lost_contact = False
            force = True
            self._macros_seen = None        # read here, not again in take_macros
            self.macros.reread()
        self.set_status(True)
        self.polls.take_away(status)
        self.take_virtual(status)
        self.take_tools(status)
        self.take_config(status)
        self.macro_banner.take(status)
        self.extension_banner.take(status)
        self.take_macros(status)
        self._take_devices(status, asked)
        changed = self._moved(status)
        if status.get("profile") in PROFILE_IDS:
            self.active = status["profile"]
        self.take_names(status)
        self.take_saved(status)
        self.take_brightness(status)
        self.take_layer(status)
        self.take_lock(status)
        self.keyboard.light.take_idle(status)
        self.menu.take_tray(status)
        if force or changed:
            if self.dragging:
                # Asked for before the press and delivered after it: owed,
                # as one asked for mid-drag is (resync).
                self._read_again = True
            else:
                self.reload_files(status)

    def _take_silence(self, error: Exception) -> None:
        """A status round trip that raised. The Mouse tab hears it from its own read."""
        self.set_status(False, error, poll=True)
        if daemon_down(error):
            self.macro_banner.take({})         # no daemon, so no macro either
            self.extension_banner.take({})     # nor a focus followed
            # Nor a lock it saw: the device polls read again, and tell
            # their tabs the daemon is gone.
            self.polls.take_away({})
        # The tabs own the device rows: each says it is the daemon, as the device may be
        # plugged in and fine.
        self.keyboard.daemon_gone(unreachable(error))
        self.mouse.show_problems([])
        self.headset.show_problem(None)
        self.headset.daemon_gone(error)

    def _take_devices(self, status: dict, asked: str | None) -> None:
        """What a status says of each device, for its tab. `asked` is the keyboard the read
        named (status_read), None for the leader's."""
        # First: the Keyboard tab's rows read it as they take the status.
        self.shown_on = ShownOn.of(status)
        problems = status.get("problems") or ()
        # Every mouse problem to the Mouse tab, which picks what its banner says
        # (MousePage.show_problems).
        self.mouse.show_problems([p for p in problems if p["code"].startswith("mouse_")])
        # The A50 Gen 4's first, then another family's while no headset is attached, when the
        # A50's page shows for all (HeadsetStack.take): an attached A50's Status row says the
        # problem it is given (HeadsetPage.show_state).
        self.headset.show_problem(
            next((p for p in problems if p["code"].startswith("headset_")), None)
            or (None if status.get("headsets") else next(
                (p for p in problems if p["code"].startswith(("a50x_", "g432_"))), None)))
        self.headset_tab.take(status)
        problem = keyboard_problem(status, self.shown_on.model)
        self.keyboard.take_status(bool(status.get("connected")),
                                  status.get("device"), status.get("battery"),
                                  answering=bool(status.get("answering")),
                                  problem=problem, name=status.get("name"),
                                  product=status.get("product"),
                                  battery_at=kept_at(status))
        if status.get("connected"):
            # Only while one is connected, answering or not: a daemon that has not found the
            # keyboard (asleep when the daemon started, say) sends no capabilities, and taking
            # that literally would empty the tab.
            effects = status.get("effects")
            if isinstance(effects, list):
                self.animations = tuple(name for name in ANIMATIONS if name in effects)
            self.keyboard.show_capabilities(status)
        self.keyboard.take_keyboards(status, asked)
        try:
            self.sync_base = parse_color(status.get("mouse_sync_base"), "mouse_sync_base")
        except ConfigError:
            self.sync_base = None
        self.mouse.take_sync(bool(status.get("mouse_sync_color")), self.polls.asks_mouse)
        self.mouse.take_leader(self.shown_on.leader)
        self.g432.take_leader(self.shown_on.leader)
        self.follow_keyboard()
        self.mouse.show_dpi_notices(status.get("dpi_notices"))

    def _moved(self, status: dict) -> bool:
        """Whether the daemon's state moved under the window since the last status, which
        this one replaces as the one seen. Before `active` takes the status's profile."""
        # Not the M profile's `effect` and `color`: the digest covers them whenever the window
        # draws them, and they move with each Static color the window sends. The animations
        # change what adopt_lighting makes of the same files.
        seen = Seen(status.get("pid"), status.get("lighting"), status.get("bindings"),
                    self.animations)
        files = [p["message"] for p in status.get("problems") or () if p["code"] in CONFIG_KINDS]
        changed = (status.get("profile") != self.active
                   or status.get("overlay") != self.overlay
                   or (self._seen is not None and seen != self._seen)
                   # A fix reloaded with `gearbench-ctl reload` moves no digest when it leaves
                   # the lighting and bindings as they were.
                   or ((self.config_error is not None or self.profile_unread())
                       and files != self._files_seen))
        self._seen, self._files_seen = seen, files
        return changed

    def take_virtual(self, status: dict) -> None:
        """Raise the uinput banner while the daemon lacks a virtual device, in its problem's
        words: the virtual keyboard's while both are missing."""
        found = {p["code"]: p["message"] for p in status.get("problems") or ()
                 if p["code"] in UINPUT_PROBLEMS}
        message = next((found[code] for code in UINPUT_PROBLEMS if code in found), None)
        if message is not None:
            self.uinput_banner.set_title(f"{capitalized(message)}; {SEE_GUIDE}")
        self.uinput_banner.set_revealed(message is not None)

    def take_tools(self, status: dict) -> None:
        """The other-tools banner, from the `other_hid_tool` problems: a
        line each, and down when there are none."""
        found = [p["message"] for p in status.get("problems") or ()
                 if p["code"] == "other_hid_tool"]
        if found:
            self.tools_banner.set_title("\n".join(found))
        self.tools_banner.set_revealed(bool(found))

    def take_macros(self, status: dict) -> None:
        """Reread the Macros tab when the `macros` digest moved, so a hand edit the
        daemon reloaded is not saved over with the tab's old steps."""
        digest = status.get("macros")
        if self._macros_seen is not None and digest != self._macros_seen:
            self.macros.reread()
        self._macros_seen = digest

    def take_layer(self, status: dict) -> None:
        """Take the game profile layer and focus state, then show which layer the tabs edit."""
        overlay = status.get("overlay")
        self.status_overlay = str(overlay) if overlay else None
        self.overlay_auto = status.get("overlay_auto") is True
        process = status.get("auto_process")
        self.auto_process = str(process) if self.overlay_auto and process else None
        self.keep_loaded = bool(status.get("keep_loaded"))
        held = status.get("held_off")
        self.held_off = None
        if isinstance(held, dict) and held.get("profile"):
            process = held.get("process")
            self.held_off = (str(held["profile"]), str(process) if process else None)
        self.held_off_more = max(0, len(status.get("held_off_all") or ()) - 1)
        focus = status.get("focus")
        self.focus = None
        if focus:
            program, profile = focus.get("program"), focus.get("profile")
            self.focus = (focus["source"], str(program) if program else None,
                          str(profile) if profile else None)
        self.keyboard.show_layer()
        self.mouse.set_editing(self.editing())
        self.g432.set_editing(self.editing())

    def take_lock(self, status: dict) -> None:
        """Lights off when locked and why it is unavailable, for the Keyboard tab's switch."""
        self.lock_lights = bool(status.get("lights_off_when_locked"))
        problem = next((p for p in status.get("problems") or ()
                        if p["code"] == "desktop_unavailable"), None)
        self.lock_unavailable = problem["message"] if problem else None
        self.keyboard.light.show_lock()

    def editing(self) -> str:
        """The line the Keyboard and Mouse tabs show, and the Headset tab's G keys group:
        which layer an edit lands in."""
        # The program loaded status_overlay; overlay catches up once the files are read.
        program = self.auto_process if self.overlay == self.status_overlay else None
        profile = self.game_profile(self.overlay)
        return editing_text(self.overlay, self.overlay_auto,
                            profile.processes if profile is not None else [], self.active,
                            self.profile_names.get(self.active, ""), program)

    def take_names(self, status: dict) -> None:
        """The M profiles' names. The daemon owns them; this window mirrors."""
        if raw := status.get("profile_names"):
            self.profile_names = {i: raw[str(i)] for i in PROFILE_IDS}
        self.keyboard.refresh_profile_row()

    def take_saved(self, status: dict) -> None:
        colors = saved_colors_from(status.get("saved_colors"))
        if colors is None or colors == self.saved_colors:
            return
        self.saved_colors = colors
        self.rebuild_saved()

    def rebuild_saved(self) -> None:
        """Every picker's Saved colors row, the Keyboard tab's first: it loads the swatches'
        rules the others draw with. The Game profiles dialog's among them while it is open."""
        self.keyboard.light.show_saved()
        for saved in (self.mouse.saved, self.g432.saved):
            saved.rebuild()
        if self.profiles_dialog is not None:
            self.profiles_dialog.rebuild_saved()

    def took_saved(self, reply: dict | None) -> None:
        """Take the saved list from a save_color or forget_color reply. The daemon owns
        config.json: a write from the window's copy drops what changed since it was read."""
        if reply is None:
            return
        self.take_saved(reply)

    def take_brightness(self, status: dict) -> None:
        if status.get("brightness_levels"):
            self.levels = status["brightness_levels"]
        if isinstance(status.get("brightness"), int):
            self.brightness = status["brightness"]
        self.keyboard.light.refresh_brightness()

    def reload_files(self, status: dict) -> None:
        """Read config.json and the game profile library on the worker: parsing every
        file on the main thread stalls the window, at every M key press."""
        def loaded(result: tuple[Config | Exception, list[GameProfile],
                                 list[tuple[str, str]]] | Exception) -> None:
            if isinstance(result, Exception):
                # The kept copy may be of a layer the daemon has left: changes built on
                # it are held until a read works.
                self.config_error = result
                self.take_config(status)
                return
            config, library, skipped = result
            if isinstance(config, Exception):
                # Held from here, as above, a release mid-drag included.
                self.config_error = config
                self.take_config(status)
            if self.dragging:
                # Started before the press: taking it changes the copy and layer the
                # release is built on. Owed, as resync's is.
                self._read_again = True
                return
            self.library, self.skipped_profiles = library, skipped
            for name, processes in self._processes_unanswered:
                profile = self.game_profile(name)
                if profile is not None:
                    profile.processes = list(processes)
            for name, color in self._mouse_colors_unanswered:
                if (profile := self.game_profile(name)) is not None:
                    profile.mouse_color = color
            if isinstance(config, Exception):
                # The game profiles and the loaded one's name are taken all the same, or
                # every poll finds the overlay moved and reads every file again, and the
                # tabs name a layer the daemon has left. The layer and the lighting stay.
                self.overlay = status.get("overlay")
                self.refresh()
                return
            self.config = config
            self.config_error = None
            self.take_config(status)
            # A color or paint still unanswered is not in this read: taking the read's
            # lighting drops that key from the copy the next click saves whole. Read again
            # once they are answered.
            held = self._lighting_unanswered > 0
            if held:
                self._read_again = True
            self.adopt(status, lighting=not held)
            self.refresh()
        self.worker.run(read_files, loaded)

    def adopt(self, status: dict, lighting: bool) -> None:
        """Take the window's lighting from a status reply and the files.

        With `lighting` False only the overlay and the M profile are taken. While the
        game profile's file cannot be read, the lighting on screen stays and changes are
        held, or the next click paints the M profile's lighting over it. The layer is
        taken even then, since a rename, which is not held, names it.
        """
        self.overlay = status.get("overlay")
        self.config.active = self.active
        if not lighting:
            return
        if not self.profile_unread():
            self._known = adopt_lighting(self.game_profile(self.overlay), self.config.profile,
                                         self.animations, self.shown_on)
            self.lighting.take(self._known)
            self.lighting_of = self.shown_on.model
        self.layer = (self.config.active, self.overlay)

    def refresh(self) -> None:
        """Put the window's state on the rows. Memory only: no disk, no daemon."""
        self.keyboard.sync()
        self.follow_keyboard()
        self.mouse.set_editing(self.editing())
        self.g432.set_editing(self.editing())
        self.macros.refresh_uses()
        if self.profiles_dialog is not None:
            self.profiles_dialog.refresh()


class Application(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID)

    def do_activate(self) -> None:
        (self.props.active_window or Window(self)).present()


def main(argv: list[str]) -> int:
    return Application().run(argv)
