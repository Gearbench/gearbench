"""The mouse tab. It polls the daemon on its own 5 s timer, with its own re-entry guard."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING, Protocol, cast

from gi.repository import Adw, GLib, GObject, Gtk

from ..battery import kept_at
from ..config import MOUSE_ACTIONS, MOUSE_BUTTONS, MOUSE_LABELS, format_color, parse_color
from ..keyboard import followed
from ..mouse import (
    CABLED_IDS,
    DPI_WRITE_WAIT,
    GEARBENCH_LIGHTING,
    OWN_LIGHTING,
    PROFILE_WRITE_WAIT,
    WIDEST_DPI_RANGE,
    HostState,
    host_state,
    model_key,
    other_profile,
)
from ..sensor import range_words, reply_range
from ..settings_files import ConfigError
from ..wording import capitalized
from .binding_rows import binding_row, binding_words, read_pick, show_binding
from .color import ColorPicker, SavedColors
from .dialogs import CaptureDialog
from .onboard_card import OnboardCard
from .stages_card import StagesCard
from .style import (
    ASLEEP,
    AWAY_BANNER,
    DAEMON_WAIT,
    EFFECT,
    FOLLOW,
    GEARBENCH_LIGHTING_SHOWN,
    LIGHTS_OWN,
    MOUSE_ASLEEP,
    MOUSE_TAB,
    NOT_READ,
    OWN_LIGHTING_SHOWN,
    SAVING_GAME,
    SETTING_RATE,
    SYNC_HELP,
    WRITING_PROFILE,
    DeviceView,
    Switcher,
    effect_label,
    effect_tips,
    explain,
    header_spinner,
    holds_focus,
    offer,
    route_text,
    set_dot,
    show_battery,
    show_header_spinner,
    show_untested,
    status_rows,
    toast_note,
    untested_row,
)
from .tips import explain_choices
from .worker import (
    Call,
    DaemonWorker,
    LoadingGuard,
    Reply,
    SetStatus,
    Settle,
    named_layer,
    preview_reply,
    take_typed,
    unreachable,
)

if TYPE_CHECKING:                       # pragma: no cover
    from .window import Window

    class Write(Protocol):
        """`MousePage._write`, as the page hands it to a card."""

        def __call__(self, request: dict, callback: Callable[[dict], None] | None = None,
                     product: str | None = None, layer: dict | None = None,
                     group: str = "onboard") -> None: ...

    class OfferWrite(Protocol):
        """`MousePage._offer_write`, as the page hands it to a card."""

        def __call__(self, dialog: Adw.AlertDialog, key: str, label: str,
                     appearance: Adw.ResponseAppearance, request: dict,
                     layer: dict | None = None, group: str = "onboard") -> None: ...

#: The binding of a button that does nothing: what the row's Nothing sends.
UNBOUND = "none"
#: A button row's built-in actions, in config.MOUSE_ACTIONS order.
MOUSE_CHOICES = [(UNBOUND, "Nothing"), ("back", "Back"), ("forward", "Forward"),
                 ("dpi-shift", "DPI shift"), ("dpi-up", "DPI up"),
                 ("dpi-down", "DPI down"), ("dpi-cycle", "DPI cycle")]
#: One tooltip line per entry, in the rows' order: MOUSE_CHOICES, then binding_rows.CHOICES.
MOUSE_TIPS = ("The button does nothing.",
              "Browser back, sent through the virtual mouse.",
              "Browser forward, sent through the virtual mouse.",
              "Drops to the shift stage while held.",
              "Steps up through the mouse's DPI stages.",
              "Steps down through the mouse's DPI stages.",
              "Steps up through the mouse's DPI stages, from the last back to the first.",
              "Pick a key or a combination for this button.",
              "Pick one of your macros for this button.")

#: The DPI row's (low, high) while the shown mouse has not reported its sensor's.
DPI_RANGE = WIDEST_DPI_RANGE
#: The DPI row's step while the shown mouse has not reported its sensor's, in DPI: that of
#: the G502 X PLUS's and the G305's sensors, which list one step throughout, and of the G203
#: LIGHTSYNC's in G HUB's description.
DPI_STEP = 50
#: How many steps Page Up and Page Down move the DPI row, as they move the A50's frequency
#: rows ten.
DPI_PAGE_STEPS = 10
#: The DPI row's wait after its last change before it writes, in milliseconds: a run of clicks
#: on + or of arrow keys is one write to the mouse, as on the A50's band rows.
DPI_SETTLE_MS = 600
#: The toast after a DPI write the daemon snapped to the sensor's step: the DPI it took.
DPI_SNAPPED = "DPI snapped to {}"

#: The Buttons group's last row, which puts each button back to its factory job after a
#: question, as Use factory stages… does the stages; its tooltip, and the toasts after it.
FACTORY_BINDINGS = "Use factory bindings…"
FACTORY_BINDINGS_HELP = "Each button's factory job; nothing where Gearbench lacks the action"
FACTORY_BINDINGS_DONE = "The buttons are back to their factory bindings"
FACTORY_BINDINGS_DONE_FOR = "The {}'s buttons are back to their factory bindings"

#: The Host mode row's subtitle, by state. On, the Onboard profile 1 group's description
#: says what the mouse runs once Gearbench stops; on a model whose writes the daemon refuses,
#: that writes to profile 1 wait for a test (style.PROFILE_WRITES_WAIT).
HOST_ON = "Gearbench drives the buttons, stages and rate while it runs"
HOST_OFF = ("The mouse runs onboard profile 1; G4 to G9 reach Gearbench if it has them "
            "enabled, below")
#: In place of HOST_OFF on a mouse whose event node is not read, whose buttons reach Gearbench
#: in host mode alone: the Buttons group says what they do meanwhile.
HOST_OFF_ALONE = "The mouse runs onboard profile 1"
#: Off, before the mouse's switch back to onboard mode lands; the banner's mouse_left_in_host
#: says why.
HOST_NOT_LEFT = "The mouse has not left host mode yet: its programmable buttons do nothing"
#: On and refused, with the daemon's reason where HOST_STUCK and the banner's mouse_not_host
#: put it: after "onboard profile 1" it would read as a word on the profile.
HOST_NOT_TAKEN_WHY = "Host mode did not take ({}), so the mouse runs onboard profile 1"
#: On and refused, and the switch back to onboard mode refused or not taken too, with the
#: reason: the banner's mouse_left_in_host, and `gearbench-ctl mouse`'s "host, stuck there".
HOST_STUCK = ("Host mode did not take ({}), and the mouse is stuck in it: its programmable buttons "
              "do nothing")
#: On and refused, and the switch back unanswered: `gearbench-ctl mouse`'s "host until the
#: mouse answers". The banner leaves out a mouse not answering.
HOST_UNTIL_ANSWERED = "Host mode did not take ({}); it is tried again when the mouse answers"
#: On, before the mouse's setup in host mode: `gearbench-ctl mouse`'s "onboard until the
#: mouse answers, then host", or for a mouse counted in host mode whose last arm was cut
#: short, "host until the mouse answers, then host mode is set up".
HOST_NOT_YET = "Host mode is set up when the mouse answers"
#: In place of HOST_OFF and HOST_NOT_TAKEN_WHY while the reply's onboard.running_profile
#: names another profile; the banner's mouse_other_profile says why.
HOST_OFF_OTHER = "The mouse runs {} in place of profile 1: the banner says why"
HOST_NOT_TAKEN_OTHER = "Host mode did not take ({}), and the mouse runs {} in place of profile 1"
#: The daemon's problems of a mouse found and not attached, which the refused `mouse` reply
#: names the first of (MousePage.show_problems).
NOT_ATTACHED_CODES = ("mouse_no_permission", "mouse_not_set_up")
#: Under the DPI notices switch, the owner's decision of 2026-09-24.
DPI_NOTICE_HELP = "Show the new DPI when a DPI button changes it"
#: The Buttons group's description on a mouse whose event node is read, the G502 X PLUS. The
#: Programmable buttons row under Onboard profile 1 says which of them reach Gearbench.
BUTTONS_HELP = "What G4 to G9 do while Gearbench runs"
#: The same on a mouse whose buttons reach Gearbench in host mode alone.
BUTTONS_HOST_HELP = "The bindings work in host mode only"
#: The Lighting group's description for a mouse that waits to claim its lighting, by whose
#: lighting it shows (`mouse`'s `lighting_shown`); none for another mouse.
LIGHTING_SHOWN_HELP = {OWN_LIGHTING: OWN_LIGHTING_SHOWN,
                       GEARBENCH_LIGHTING: GEARBENCH_LIGHTING_SHOWN}
#: The tooltip of Show its own lighting, which such a mouse has under Lighting: its color
#: and effect go, as color sync turned off gives them back with none set.
LIGHTS_OWN_HELP = "The lighting the mouse shows without Gearbench"
#: The status page's titles until a mouse is seen: before the first reply, and after the
#: daemon refuses `mouse`, over its reason (DeviceView).
LOOKING = "Looking for the mouse…"
NO_MOUSE = "Mouse not available"
#: A write's toast for a game profile's note too long to toast whole (short_note).
PROFILE_SAVED = "Saved to the game profile"
#: The same for a report rate set live alone (Dpi.live_rate), which writes no profile.
SET_LIVE = "Report rate set live; the onboard profile keeps its own"
#: Those lines by the reply's `written`; any other write's went to onboard profile 1.
WRITTEN_LINES = {"game_profile": PROFILE_SAVED, "live": SET_LIVE}
#: The toast of a rate the mouse took live whose reply has no note (Mouse.set_report_rate's
#: "live"), by the rate.
RATE_TAKEN_LIVE = "Report rate set to {} Hz live (the mouse is in host mode)"


class MousePage:
    """The Mouse tab: the switcher between mice, status, tuning, lighting and the
    buttons. The DPI stages with the report rate, and the Onboard profile 1 group, are cards
    of their own (`stages`, `onboard`), built here with what they need.

    `parent` is the window, whose layer the button bindings and Use factory bindings… are for.
    `call` is the window's: it shows a daemon that did not answer on its banner and a
    refused command where it can be read, then hands on the reply or None. `kb_base` is the
    keyboard's base color, which a synced mouse follows. `refused` is the window's handler
    for the reply this page reads itself, a preview's.
    """

    #: The last reply said the mouse answers. False: attached but asleep or off, so the rows
    #: that write to it are insensitive and the rest show its last reading.
    answering = True
    #: A switch is being put on the daemon's value (_adopt), and its handler
    #: sends nothing. Held for the set_active alone: the loading guard lets go
    #: on the next idle, and a click landing before then would be dropped.
    _adopting = False
    #: How many colors and effects the page has sent. A `mouse` reply asked for before the
    #: last one holds the color and effect it replaced, which _show_lighting leaves off the rows.
    _lighting_sent = 0
    #: Whose lighting the shown mouse shows, a mouse that waits to claim its lighting alone
    #: (the reply's `lighting_shown`); None for any other.
    _lighting_shown: str | None = None
    #: The last refused `mouse` reply's reason, which the tab says (_failed); None before one.
    _refusal: str | None = None

    def __init__(self, parent: Window, worker: DaemonWorker, call: Call,
                 toast: Callable[..., object],
                 set_status: SetStatus,
                 kb_base: Callable[[], int],
                 refused: Callable[[dict], None]) -> None:
        self._parent = parent
        self._worker = worker
        self._call = call
        self._toast = toast
        self._refused = refused
        self._set_status = set_status
        self._kb_base = kb_base

        self._loading = LoadingGuard()
        self._pending = False
        self._effects: list[str] = ["static"]
        #: Each effect's tooltip on the Effect row, changed in place with the list
        #: (explain_choices reads it as an entry is drawn).
        self._effect_tips = effect_tips(self._effects)
        self.rows: dict[str, Adw.ComboRow] = {}
        #: Each button row's Change… button, shown while the row is on Keystroke… or Macro…: an
        #: Adw.ComboRow only notifies a change, so picking the entry again does nothing.
        self.change_buttons: dict[str, Gtk.Button] = {}
        #: Each button's binding as the daemon last said it, to put a row back by at once.
        self._bindings: dict[str, str] = {}
        #: Each button's label and default binding on the shown mouse's model, from the reply's
        #: `button_table`, and the model's name.
        self._labels: dict[str, str] = dict(MOUSE_LABELS)
        self._defaults: dict[str, str] = {}
        self._name = "mouse"
        #: Why the shown mouse's bindings do nothing now (the reply's `button_table.idle`), or
        #: None; and what profile 1 has each button do, which a row says meanwhile.
        self._idle: str | None = None
        self._jobs: dict[str, str] = {}
        #: The mouse the last reply was about, by product id.
        self._product: str | None = None
        #: The mouse whose DPI, range and step the DPI row shows, by product id, which its
        #: writes go to: a DPI changed for one mouse must not go to another polled meanwhile.
        self._dpi_for: str | None = None
        #: The DPI the mouse has: the one a reply last put in the row, or a DPI write's answer
        #: that found the row the user's (_send_dpi); None while it is Not read.
        self._dpi_shown: int | None = None
        #: The DPI row's write once its changes settle (DPI_SETTLE_MS), and how many of its
        #: writes are unanswered: either holds the row (_dpi_held).
        self._dpi_settle = Settle(self._send_dpi)
        self._dpi_unanswered = 0
        #: The window's close ran (close): the DPI row writes nothing from then on.
        self._closed = False
        #: A _write is in flight; the poll would otherwise re-enable the rows under it.
        self.writing = False
        #: The group whose row started that write, "tuning", "stages" or "onboard", and what
        #: its header's spinner says meanwhile.
        self._spinning: str | None = None
        self._spinning_label = WRITING_PROFILE
        #: The last refused preview, so a drag reports it once.
        self._preview_error: str | None = None
        #: The daemon's view of the mouse: reachable, and following the keyboard's color.
        self.ok = False
        self.sync = False
        #: The daemon's DPI notices switch, from the last status or reply, and how many clicks
        #: of it are unanswered, which hold the switch (show_dpi_notices).
        self.dpi_notices = False
        self._notices_unanswered = 0
        #: The product ids behind the switcher's buttons, in their order.
        self._devices: list[str] = []
        #: The DPI stages group with the Report rate row, and the Onboard profile 1 group.
        #: Their writes go out through this page's _write.
        self.stages = StagesCard(parent, self._loading, self._write, self._offer_write, toast,
                                 lambda: self._product)
        self.onboard = OnboardCard(parent, self._offer_write)
        self.widget = self._build()

    # -- construction -----------------------------------------------------

    def _build(self) -> Gtk.Widget:
        """The tab: the rows, in a window that scrolls, under the banner."""
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                       margin_top=18, margin_bottom=18, margin_start=18, margin_end=18)
        for group in (self._status_group, self._tuning_group, self.stages.build,
                      self._lighting_group, self._buttons_group, self.onboard.build):
            page.append(group())
        # Up while the daemon's status names a mouse_ problem (show_problems). A top bar,
        # as on the Keyboard tab, so it shows however the tab is scrolled. Not markup: the
        # title is the daemon's words.
        self.profile_banner = Adw.Banner(use_markup=False)
        # Up while the mouse shown does not answer, as the Keyboard tab's (_show_answering).
        self.away_banner = Adw.Banner(use_markup=False)
        # A status page in place of the rows until a mouse is seen.
        self.view = DeviceView(page, "input-mouse-symbolic", LOOKING)
        toolbar = Adw.ToolbarView(content=self.view.stack)
        # Shown for two mice or more, as the Headset tab's for two headsets: with one, the
        # status group's title names it. A screen reader calls it by the tab's noun, as it
        # does the Keyboard and Headset tabs' switchers.
        self.device_switcher = Switcher(MOUSE_TAB, self.on_device)
        toolbar.add_top_bar(self.device_switcher.widget)
        toolbar.add_top_bar(self.away_banner)
        toolbar.add_top_bar(self.profile_banner)
        return toolbar

    def _status_group(self) -> Adw.PreferencesGroup:
        """Which mouse this is and whether it is there, for a model not yet run on the row
        that asks for a device report from it, and the Host mode switch."""
        # Retitled from the reply: which mouse this is is unknown until one answers.
        status = self.status_group = Adw.PreferencesGroup(title=MOUSE_TAB)
        self.state_row, self.state_dot, self.battery_row, self.battery_icon = status_rows(status)
        self.untested_row = untested_row(status, self._toast)
        # The daemon's mouse_host_mode, shown for a mouse whose button spy HOST mode drives
        # (show_host). A daemon setting, so not a device row: it stays usable while the
        # mouse does not answer, which gets the switch once it does. Not markup: the subtitle
        # can carry the daemon's reason.
        self.host_row = Adw.SwitchRow(title="Host mode", subtitle=HOST_ON, use_markup=False,
                                      visible=False)
        self.host_row.connect("notify::active", self.on_host_mode)
        status.add(self.host_row)
        return status

    def _tuning_group(self) -> Adw.PreferencesGroup:
        """The DPI and the report rate, the mouse's, then the DPI notices switch, the
        daemon's, last, as the Keyboard tab puts its lock and idle rows after Brightness."""
        # Stacked rows in one group, as every other group here and on the
        # desktop has them (the owner's request of 2026-09-24).
        tuning = self.tuning_group = Adw.PreferencesGroup(title="Tuning")
        #: Spins while the Report rate's write is in flight (_write).
        self.tuning_spinner = header_spinner(tuning)
        # Written once its changes settle (on_dpi): a run of clicks on + or of arrow keys is
        # one write to the mouse, not one a step. A number typed is taken by Enter, by the
        # window's close (flush), by a click outside the row (style.take_typing_on_clicks),
        # and by the focus leaving the row, which GTK's spin button takes itself (measured
        # with GTK 4.22.4, the owner's decision of 2026-10-23). One typed outside the range,
        # or a word, puts back the DPI shown (IF_VALID), where GTK's default would send the
        # nearer end, and for a word the lowest.
        low, high = DPI_RANGE
        self.dpi_row = Adw.SpinRow(
            title="DPI", update_policy=Gtk.SpinButtonUpdatePolicy.IF_VALID,
            adjustment=Gtk.Adjustment(lower=low, upper=high, value=low, step_increment=DPI_STEP,
                                      page_increment=DPI_STEP * DPI_PAGE_STEPS))
        self.dpi_row.connect("notify::value", self.on_dpi)
        tuning.add(self.dpi_row)
        tuning.add(self.stages.build_rate_row())
        # A switch of its own, since GNOME's for the app would silence the battery notices too
        # (the owner's decision of 2026-09-24). A daemon setting, so not a device row; shown
        # from the first status.
        self.notice_row = Adw.SwitchRow(title="DPI notices", subtitle=DPI_NOTICE_HELP,
                                        visible=False)
        self.notice_row.connect("notify::active", self.on_dpi_notices)
        tuning.add(self.notice_row)
        return tuning

    def _lighting_group(self) -> Adw.PreferencesGroup:
        """The sync switch, the effect and the color."""
        light = self.light_group = Adw.PreferencesGroup(title="Lighting")
        self.sync_row = Adw.SwitchRow(title=FOLLOW.format(followed(None)), subtitle=SYNC_HELP)
        self.sync_row.connect("notify::active", self.on_sync)
        light.add(self.sync_row)
        self.effect_row = Adw.ComboRow(title=EFFECT,
                                       model=Gtk.StringList.new([effect_label("static")]))
        explain_choices(self.effect_row, self._effect_tips)
        self.effect_row.connect("notify::selected", self.on_effect)
        light.add(self.effect_row)
        self.picker = ColorPicker(self._preview, self._commit, self._toast,
                                  subtitle="Live on the mouse as you drag")
        self.saved = SavedColors(self.picker, self._parent)
        light.add(self.picker.row)
        # No undo sign: it hands the lights over and puts no value back, as the headset's row.
        self.own_row = Adw.ButtonRow(title=LIGHTS_OWN, tooltip_text=LIGHTS_OWN_HELP,
                                     visible=False)
        self.own_row.connect("activated", self.on_own)
        light.add(self.own_row)
        return light

    def _buttons_group(self) -> Adw.PreferencesGroup:
        """What G4 to G9 do, a row each."""
        buttons = self.buttons_group = Adw.PreferencesGroup(title="Buttons",
                                                            description=BUTTONS_HELP)
        # Which layer a binding is written to, as the Keyboard tab says it:
        # the loaded game profile's, or the M profile's. Not markup.
        self.editing_row = Adw.ActionRow(title="Editing", use_markup=False)
        buttons.add(self.editing_row)
        for name in MOUSE_BUTTONS:
            row, change = binding_row(MOUSE_LABELS[name], MOUSE_CHOICES, MOUSE_TIPS,
                                      "Pick another key or macro for this button",
                                      partial(self.on_change, name))
            row.connect("notify::selected", self.on_binding, name)
            self.rows[name] = row
            self.change_buttons[name] = change
            buttons.add(row)
        self.defaults_row = Adw.ButtonRow(title=FACTORY_BINDINGS,
                                          start_icon_name="edit-undo-symbolic",
                                          tooltip_text=FACTORY_BINDINGS_HELP)
        self.defaults_row.connect("activated", self.on_bind_defaults)
        buttons.add(self.defaults_row)
        return buttons

    def show_problems(self, problems: list[dict]) -> None:
        """Show the daemon's mouse problems on the banner, a line each, or take it down.

        While the tab has no mouse to show, a mouse found and not attached, its node refused
        (mouse_no_permission) or its setup refused (mouse_not_set_up), is left out when the
        refused `mouse` reply, which the tab says (_failed), names it: that reply names the
        first such mouse alone. Before a reply, each is left out. Beside a mouse shown, the
        reply is about that mouse, and only the banner says one."""
        lines = [capitalized(p["message"]) for p in problems
                 if self.ok or p["code"] not in NOT_ATTACHED_CODES
                 or self._refusal not in (None, p["message"])]
        if lines:
            self.profile_banner.set_title("\n".join(lines))
        self.profile_banner.set_revealed(bool(lines))

    def set_editing(self, text: str) -> None:
        """Say which layer the button bindings are written to."""
        self.editing_row.set_title(text)

    def on_bind_defaults(self, _row: Adw.ButtonRow | None) -> None:
        """Put every button back, behind a confirmation: it discards the lot."""
        # The daemon writes bindings to the loaded game profile when there is
        # one, so the dialog has to say which of the two is about to change.
        win = self._parent
        where = f"the game profile {win.overlay}" if win.overlay else "the active M profile"
        dialog = Adw.AlertDialog(heading="Use the factory bindings?")
        defaults = "; ".join(f"{self._labels[name]}: {binding_words(spec, MOUSE_CHOICES)}"
                             for name, spec in self._defaults.items())
        explain(dialog, paragraphs=(
            f"In {where}, the {self._name}'s buttons return to their factory bindings"
            + (f": {defaults}." if defaults else "."),
            ("Anything bound to them, macros included, is replaced. Another mouse model's "
             "buttons keep theirs."),
        ))
        layer, product = named_layer(win.layer), self._product
        offer(dialog, "factory", "Use factory bindings", Adw.ResponseAppearance.DESTRUCTIVE,
              lambda: self._do_bind_defaults(layer, product))
        dialog.present(self._parent)

    def _do_bind_defaults(self, layer: dict, product: str | None) -> None:
        """The confirmed answer, for `product`, the mouse the dialog named, which the shared
        selection can move off meanwhile, on `layer`, the window's when the dialog opened, laid
        over the window's (Window.on_layer): the daemon refuses it (layer_changed) once the
        layer in force has left the one the dialog named, as it refuses a bind's."""
        target = product or self._product

        def done(reply: dict | None) -> None:
            if reply is None:
                return
            # The rows take the reply's bindings only while they still show that model; a
            # reset of another names it, as _bind's toast does.
            if self._shows_model(target):
                self._refresh_bindings(reply.get("bindings") or {})
                self._toast(FACTORY_BINDINGS_DONE)
            else:
                self._toast(FACTORY_BINDINGS_DONE_FOR.format(reply["mouse"]))
        request = self._for_shown({"command": "mouse_bind_defaults"}, target)
        self._call({**request, "layer": layer}, done)

    def _shows_model(self, product: str | None) -> bool:
        """Whether the rows show the bindings of `product`'s model, which a bind or a reset
        for it changes: a model's bindings are one, whichever of its ids, receiver or cable,
        the mouse shown answers on (mouse.model_key)."""
        if product is None or self._product is None:
            return product == self._product
        return model_key(int(product, 16)) == model_key(int(self._product, 16))

    def _for_shown(self, request: dict, product: str | None = None) -> dict:
        """`request` for the mouse the rows show, or `product`'s, by its `device`, since the
        shared selection can move off it meanwhile: a bind is saved for that mouse's model, and
        any other request is for that mouse."""
        product = product or self._product
        return request if product is None else {**request, "device": product}

    def _set_writing(self, busy: bool) -> None:
        """Show whether a _write is in flight, and hold off a second one: the cards'
        flash_rows are off while one is, and otherwise follow the last reply, since a poll
        can have found the daemon gone or the mouse not answering meanwhile. The DPI row is
        left alone: a live write to the sensor, not to flash."""
        self.writing = busy
        usable = not busy and self.ok and self.answering
        self.stages.set_usable(usable)
        self.onboard.show_writing(usable)
        label = self._spinning_label if busy else None
        show_header_spinner(self.tuning_spinner, label if self._spinning == "tuning" else None)
        self.stages.show_spinner(label if self._spinning == "stages" else None)
        self.onboard.show_spinner(label if self._spinning == "onboard" else None)

    # -- reading the mouse ------------------------------------------------

    def tick(self, dragging: bool, ask: bool = True) -> None:
        """The five second poll. Skipped mid-drag: the read costs 55 ms."""
        if not dragging and not self._pending:
            self.refresh(ask=ask)

    def refresh(self, force: bool = False, ask: bool = True) -> None:
        """Read the mouse. `force` puts the typed-in rows back on the daemon's values even with
        the cursor in one, for a write's own re-read. `ask` False has the daemon answer from its
        last read and send the mouse nothing, for a read made while this tab is hidden or the
        window is not the active one, and the first one at the window's build."""
        self._pending = True
        request = {"command": "mouse"} if ask else {"command": "mouse", "ask": False}
        sent = self._lighting_sent
        self._worker.call(request, lambda info: self._on_info(
            info, force, lighting=sent == self._lighting_sent))

    @property
    def dragging(self) -> bool:
        """The color picker is between press and release, or a run of its keys still
        settles: the window's polls and file reads wait for it."""
        return self.picker.dragging

    def flush(self) -> bool:
        """Send a DPI change or a picker key run still settling, now, for the window's
        close, a DPI typed and not yet taken among them. True when there was one."""
        # A row Not read shows a blank: taking it would show the value the row still holds,
        # another mouse's DPI or the range's low end, and send that.
        if self._dpi_shown is not None:
            take_typed(self.dpi_row)
        return any([self._dpi_settle.flush(), self.picker.flush()])

    def close(self) -> None:
        """Stop the DPI row's wait and its writes, and take the picker's rules and the stage
        dots' sheet off the display, which holds them, not the page or its card."""
        self._closed = True
        self._dpi_settle.cancel()
        self.picker.destroy()
        self.stages.close()

    def _failed(self, detail: str, title: str = NO_MOUSE) -> None:
        """Show that this tab holds nothing usable, and why: until a mouse is seen on the
        status page, under `title`, and after that on the Status row alone, as the Keyboard
        tab says it: a toast would repeat it.
        """
        self.ok = False
        set_dot(self.state_dot, False)
        # A mouse gone has nothing to come back to, as on the Keyboard tab.
        self.away_banner.set_revealed(False)
        self.state_row.set_subtitle(capitalized(detail))
        # The node and the battery reading were the gone mouse's, as the Keyboard tab has it.
        self.state_row.set_tooltip_text(None)
        show_battery(self.battery_row, self.battery_icon, None, gone=True)
        self.dpi_row.set_sensitive(False)
        self._set_writing(self.writing)
        self._apply_sync_state()
        if not self.view.seen:
            self.view.missing(title, detail)

    def _on_info(self, info: Reply, force: bool = False, lighting: bool = True) -> None:
        """Put a `mouse` reply on the rows.

        A reading the reply leaves out (None from Mouse.describe: refused, or still waking) is
        kept for the mouse already shown, and cleared on a switch to another mouse or on `force`.
        `lighting` False: a color or effect was sent after the reply was asked for
        (_show_lighting).
        """
        self._pending = False
        if isinstance(info, Exception):
            self._set_status(False, info, poll=True)
            # In words, and about the daemon: a timeout here is a busy daemon, not the mouse.
            self._failed(unreachable(info), DAEMON_WAIT)
            return
        if not info.get("ok"):
            self._refusal = str(info.get("error", "not connected"))
            self._failed(self._refusal)
            # The mice the last answer listed may be gone, every one when no mouse is attached:
            # the next answer lists them again.
            self.show_devices({})
            # The layer's bindings for the selected mouse's model, which a refusal for want of
            # a mouse carries: they can change with no mouse attached, by an M key or a game
            # profile. Taken for the model the rows show alone.
            shown = model_key(int(self._product, 16)) if self._product else None
            if isinstance(info.get("bindings"), dict) and info.get("bindings_model") == shown:
                self._refresh_bindings(info["bindings"])
            return
        self.view.found()
        previous, self._product = self._product, str(info.get("product") or "") or None
        self.ok = True
        self._show_answering(info)
        self.show_devices(info)
        self.show_capabilities(info)
        self._show_status(info)
        self._show_dpi(info, force)
        self._show_lighting(info, force, lighting)
        self.show_host(info.get("host"), (info.get("onboard") or {}).get("running_profile"),
                       bool((info.get("button_table") or {}).get("event_node", True)))
        self.stages.show(info, force, switched=self._product != previous)
        self.onboard.show(info)
        # The cards' rows once the cards hold the reply, whose refusal or missing profile
        # keeps some off. A poll during a write leaves them off until it ends.
        self._set_writing(self.writing)
        self.show_button_table(info.get("button_table"))
        self._refresh_bindings(info.get("bindings") or {})
        self._apply_sync_state()

    def _show_answering(self, info: dict) -> None:
        """`answering`, the dot and the away banner, set before the rows that read
        `answering`."""
        # Asleep or switched off behind its receiver: attached, and every
        # write to it would wait out its timeouts. Said by the dot rather than
        # a toast, which would come back every poll; the banner says that the rows Gearbench
        # keeps, Host mode and the buttons, stay live and their changes are saved.
        self.answering = bool(info.get("answering"))
        set_dot(self.state_dot, True if self.answering else ASLEEP)
        self.away_banner.set_title(AWAY_BANNER.format(info.get("name") or "mouse"))
        self.away_banner.set_revealed(not self.answering)

    def _show_status(self, info: dict) -> None:
        """The Status row's route and node, and the Battery row."""
        # The route rather than /dev/hidrawN, whose number floats; the node
        # goes in the tooltip.
        device = info.get("device")
        self.state_row.set_subtitle(
            (route_text(info.get("product"), CABLED_IDS) or str(device)) if self.answering
            else MOUSE_ASLEEP)
        self.state_row.set_tooltip_text(str(device) if device else None)
        # Its last reading, and while the mouse does not answer, when it was taken.
        show_battery(self.battery_row, self.battery_icon, info.get("battery"), kept_at(info))

    def _show_dpi(self, info: dict, force: bool) -> None:
        """The DPI row: the reply's DPI over the sensor's range and step, unless the row is
        the user's (_dpi_held), and sensitive while the mouse answers and its DPI was read.
        A reading the reply leaves out is kept for the mouse the row shows, and cleared for
        another mouse or on `force`: the range is DPI_RANGE, and the DPI Not read."""
        if not self._dpi_held(force):
            fresh = force or self._product != self._dpi_for
            shown = reply_range(info.get("dpi_range"))
            if shown is not None or fresh:
                self._show_dpi_range(shown)
            if info.get("dpi") or fresh:
                self._put_dpi(int(info["dpi"]) if info.get("dpi") else None)
                self._dpi_for = self._product
        self.dpi_row.set_sensitive(self.answering and self._dpi_shown is not None)

    def _dpi_held(self, force: bool) -> bool:
        """Whether a reply leaves the DPI row as it is: while a change waits to settle or its
        write is unanswered, which a poll sent before the write would undo, and, unless
        `force`, while the cursor is in the row over a number it has not taken yet, which
        Enter or leaving the row takes."""
        if self._dpi_settle.pending or self._dpi_unanswered:
            return True
        row = self.dpi_row
        # Gtk.SpinButton's text for its value, with no decimals (digits 0). A row Not read
        # holds no number to type over.
        return (not force and self._dpi_shown is not None
                and holds_focus(self._parent.get_focus(), row)
                and row.get_text() != f"{row.get_value():.0f}")

    def _show_dpi_range(self, shown: tuple[int, int, list[int]] | None) -> None:
        """The DPI row's range and step: the sensor's range (sensor.reply_range), or DPI_RANGE
        where the reply gives none, in its coarsest step, or DPI_STEP for no step above 0. On
        a sensor whose step grows with the DPI, a click on + by the finest step would snap back
        to the DPI it left in a coarser band (sensor.DpiRange.clamp); a DPI typed between the
        row's steps is sent as typed, and the daemon snaps it."""
        low, high, steps = shown or (*DPI_RANGE, [])
        step = max(steps, default=0) or DPI_STEP
        # The range and the sensor's steps in a tooltip: the row shows neither.
        self.dpi_row.set_tooltip_text(range_words(shown) if shown else None)
        adjustment = self.dpi_row.get_adjustment()
        if (adjustment.get_lower(), adjustment.get_upper(),
                adjustment.get_step_increment()) != (low, high, step):
            # A new range can move the value, which calls on_dpi.
            with self._loading:
                self.dpi_row.set_range(low, high)
                adjustment.set_step_increment(step)
                adjustment.set_page_increment(step * DPI_PAGE_STEPS)

    def _put_dpi(self, dpi: int | None) -> None:
        """`dpi` in the DPI row as the mouse's, or for None Not read, with no number: one left
        there would be the last mouse's."""
        row = self.dpi_row
        self._dpi_shown = dpi
        row.set_subtitle(NOT_READ if dpi is None else "")
        if dpi is None:
            row.set_text("")
            return
        self._loading.put_value(row, dpi)
        # A value that did not change leaves the text as it was: Not read's blank, or the
        # typing a forced read puts back.
        if row.get_text() != str(dpi):
            row.set_text(str(dpi))

    def _show_lighting(self, info: dict, force: bool, current: bool) -> None:
        """The Effect row, the sync switch, the picker's color and whose lighting the mouse
        shows. `current` False: a color or effect was sent after the reply was asked for, so
        its effect, color and lighting shown are the ones that change replaced, and the rows
        keep the change."""
        effects = list(info.get("effects") or ["static"])
        if effects != self._effects:
            self._effects = effects
            self._effect_tips[:] = effect_tips(effects)
            # In words; _effects keeps the names on_effect sends.
            self._loading.put_model(self.effect_row,
                                    Gtk.StringList.new([effect_label(e) for e in effects]))
        # The one last set, which the daemon does not read from the mouse:
        # a color of its own, or none set, is static.
        effect = info.get("effect") or "static"
        # An effect the list lacks leaves the row as it is: a ComboRow cannot select nothing.
        if current and effect in self._effects:
            self._loading.put_selected(self.effect_row, self._effects.index(effect))
        if isinstance(info.get("sync_color"), bool):
            self.set_sync(info["sync_color"])
        if current:
            shown = info.get("lighting_shown", "")
            self.light_group.set_description(LIGHTING_SHOWN_HELP.get(shown))
            self._lighting_shown = shown if shown in LIGHTING_SHOWN_HELP else None
        # What the daemon last wrote to the mouse, unless a color was sent
        # since the reply was asked for, a drag is under way, a color is
        # being typed, or the picker is showing the keyboard's color instead.
        if (current and info.get("color") and not self.sync and not self.picker.dragging
                and (force or not self.picker.typing(self._parent))):
            try:
                self.picker.set_color(parse_color(info["color"], "mouse color"))
            except ConfigError as exc:
                self._toast(capitalized(str(exc)))

    def show_devices(self, info: dict) -> None:
        """Offer a choice of mouse when more than one is attached.

        The daemon drives them all and names which one its reply is about. A pick (on_device)
        moves the daemon's selection, which the CLI shares: the mouse the tab shows and the one
        a command without `device` acts on.
        """
        devices = info.get("devices") or []
        self._devices = [str(d.get("product")) for d in devices]
        here = str(info.get("product") or "")
        # One that does not answer is shown switched off until it answers, as on the Keyboard
        # tab's switcher (the owner's decision of 2026-10-23, "Gray it out").
        silent = {index for index, d in enumerate(devices) if d.get("answering") is False}
        self.device_switcher.show([str(d.get("name") or d.get("product")) for d in devices],
                                  self._devices.index(here) if here in self._devices else None,
                                  len(devices) > 1, silent)

    def on_device(self, index: int) -> None:
        """Tell the daemon which mouse to answer about, the one at `index` among the
        switcher's, then read it again."""
        self._call({"command": "mouse_select", "value": self._devices[index]},
                   lambda reply: self.refresh(force=True) if reply else None)

    def show_capabilities(self, info: dict) -> None:
        """Show the sections this device has, and hide the others.

        The tab is built with every section, since which mouse is attached is unknown until one
        answers. Hidden, not insensitive: a grayed-out Lighting group on a mouse with no lights
        would say the feature is unavailable when it does not exist.
        """
        able = set(info.get("capabilities") or ())
        name = self._name = str(info.get("name") or MOUSE_TAB)
        # A group's title is markup, and the name is the daemon's.
        self.status_group.set_title(GLib.markup_escape_text(name))
        show_untested(self.untested_row, info.get("untested"))
        self.light_group.set_visible("lighting" in able)
        self.buttons_group.set_visible("buttons" in able)
        self.stages.show_able(able)
        self.onboard.show_able(able)
        self.tuning_group.set_visible("dpi" in able or "report_rate" in able)
        self.dpi_row.set_visible("dpi" in able)
        self.battery_row.set_visible("battery" in able)

    def show_host(self, host: object, running: object = None, event_node: bool = True) -> None:
        """The Host mode row, from the reply's `host` as mouse.host_state reads it: shown for a
        mouse whose button spy HOST mode drives, on while the daemon's switch is, and saying whether it
        took, what waits for a mouse not answering, or, switched off, whether the mouse has
        left host mode. `running`, the reply's onboard.running_profile, names a profile other
        than 1 in place of profile 1, outside host mode. `event_node` False: the mouse's buttons
        reach Gearbench in host mode alone, the reply's button_table.event_node."""
        host = host if isinstance(host, dict) else {}
        capable = bool(host.get("capable"))
        self.host_row.set_visible(capable)
        if not capable:
            return
        wanted, error = bool(host.get("wanted")), host.get("error")
        self._adopt(self.host_row, wanted)
        other = other_profile(running)
        state = host_state(host, self.answering)
        if state is HostState.DRIVEN:
            subtitle = HOST_ON
        elif state in (HostState.LEFT_THERE, HostState.UNTIL_ANSWERED) and not wanted:
            subtitle = HOST_NOT_LEFT
        elif state is HostState.LEFT_THERE:
            subtitle = HOST_STUCK.format(error)
        elif state is HostState.UNTIL_ANSWERED:
            subtitle = HOST_UNTIL_ANSWERED.format(error)
        elif state is HostState.SWITCHED_OFF:
            subtitle = (HOST_OFF_OTHER.format(other) if other
                        else HOST_OFF if event_node else HOST_OFF_ALONE)
        elif state is HostState.REFUSED:
            subtitle = (HOST_NOT_TAKEN_OTHER.format(error, other) if other
                        else HOST_NOT_TAKEN_WHY.format(error))
        else:
            subtitle = HOST_NOT_YET
        self.host_row.set_subtitle(subtitle)

    def on_host_mode(self, row: Adw.SwitchRow, _p: GObject.ParamSpec) -> None:
        """Send the switch, and read the mouse back either way: a refusal puts the row back,
        and the reply's note says a mouse the switch did not take on, or, switched off, one
        stuck in host mode. No loading guard, as on_dpi_notices: _adopting marks the switch
        set from the daemon."""
        if self._adopting:
            return
        wanted = row.get_active()

        def done(reply: dict | None) -> None:
            if reply and reply.get("note"):
                toast_note(self._toast, reply["note"], "Host mode did not take" if wanted
                           else "Host mode did not end")
            self.refresh(force=True)
        self._call({"command": "mouse_host_mode", "value": wanted}, done)

    def show_button_table(self, table: object) -> None:
        """The button rows as the shown mouse's model has them (the reply's `button_table`):
        a row for each of its buttons alone, under its label; and where their bindings do
        nothing now (`idle`), each row grayed out with what profile 1 has the button do, and
        the reason as the group's description. Without a table the rows stay as they
        are: the group is hidden then (show_capabilities)."""
        if not isinstance(table, dict):
            return
        shown = {str(b.get("name")): b for b in table.get("buttons") or []
                 if isinstance(b, dict)}
        idle = table.get("idle")
        self._idle = str(idle) if idle else None
        self._jobs = {name: str(b.get("onboard") or "") for name, b in shown.items()}
        self._defaults = {name: str(b.get("default") or UNBOUND) for name, b in shown.items()}
        for name, row in self.rows.items():
            row.set_visible(name in shown)
            if name in shown:
                self._labels[name] = str(shown[name].get("label") or MOUSE_LABELS[name])
                row.set_title(self._labels[name])
            row.set_sensitive(self._idle is None)
        # A group's description is markup, and the idle reason is the daemon's, naming the
        # mouse.
        self.buttons_group.set_description(
            GLib.markup_escape_text(capitalized(self._idle) + ".")
            if self._idle
            else BUTTONS_HELP if table.get("event_node") else BUTTONS_HOST_HELP)

    def _refresh_bindings(self, bindings: dict[str, str]) -> None:
        """Keep the daemon's bindings, and put each button's row on its binding (_show_held)."""
        self._bindings = dict(bindings)
        for name in self.rows:
            self._show_held(name)

    def _show_held(self, name: str) -> None:
        """Put one button's row on what the daemon last said it holds (show_binding); on a
        mouse whose bindings do nothing now, its subtitle says what profile 1 has it do."""
        row = self.rows[name]
        show_binding(self._loading, row, self.change_buttons[name],
                     self._bindings.get(name, UNBOUND), MOUSE_CHOICES)
        job = self._jobs.get(name)
        if self._idle is not None and job:
            row.set_subtitle(f"In profile 1: {job}")

    # -- writes -----------------------------------------------------------

    def _write(self, request: dict, callback: Callable[[dict], None] | None = None,
               product: str | None = None, layer: dict | None = None,
               group: str = "onboard") -> None:
        """A command that rewrites onboard profile 1, edits a loaded game profile's own stages
        or rate, or sets the report rate live alone, from a row of `group`: "tuning", "stages"
        or "onboard".

        Locks the rows that could start a second write and spins the group's header until the
        reply lands, then toasts its note, or the rate of a live write without one, and
        re-reads the mouse: a stage edit can move its live DPI. `callback` gets the reply if
        the write succeeded.

        The request names the mouse the last reply was about, or `product` for an input built
        earlier (an entry being edited, a dialog left open): the selection is shared, and
        `gearbench-ctl mouse-select` or a mouse going away moves it before the next poll.
        `layer`, {"overlay": name or None}, is the game profile such an input was filled for,
        since a load or an unload moves the window's layer meanwhile.
        """
        request = self._for_shown(request, product)
        if layer is not None:
            # Laid over the window's layer, which the stages and the rate name too: they
            # edit a loaded game profile's own (Window.on_layer).
            request = {**request, "layer": layer}

        def done(reply: dict | None) -> None:
            self._set_writing(False)
            if reply is None:
                # Refused, and said by the window: the rows go back to what the mouse holds.
                self.refresh(force=True)
                return
            if reply.get("note"):
                # A game profile's note is one sentence (Dpi.tuning_note); a rate set live's
                # (live_rate_note) is cut at its first sentence when that fits a toast.
                written = reply.get("written") or ""
                toast_note(self._toast, reply["note"],
                           WRITTEN_LINES.get(written, "Written to onboard profile 1"),
                           one_sentence=written == "game_profile")
            elif reply.get("written") == "live":
                # Flash.set_rate's live write through Mouse.set_report_rate carries no note.
                self._toast(RATE_TAKEN_LIVE.format(reply["report_rate"]))
            self.refresh(force=True)
            if callback is not None:
                callback(reply)
        self._spinning = group
        # A rate on a mouse whose rate the daemon sets live alone writes no profile.
        live = request.get("command") == "mouse_rate" and self.stages.rate_live
        self._spinning_label = (SAVING_GAME if (layer or {}).get("overlay")
                                else SETTING_RATE if live else WRITING_PROFILE)
        self._set_writing(True)
        # A round trip that raised reads nothing back: the daemon did not answer.
        self._call(request, done, lambda _error: self._set_writing(False),
                   timeout=PROFILE_WRITE_WAIT)

    def on_dpi(self, _row: Adw.SpinRow, _p: GObject.ParamSpec) -> None:
        """The DPI changed, by a click, an arrow key or a number typed and taken: written once
        the changes settle (DPI_SETTLE_MS), but not for a DPI a reply put there, or one typed
        that GTK takes as the window is destroyed, after the close (worker.take_typed)."""
        if self._loading.active or self._closed:
            return
        self._dpi_settle.restart(DPI_SETTLE_MS)

    def _send_dpi(self) -> None:
        """Write the DPI the row shows, to the mouse it was filled for (`_dpi_for`); nothing
        when the mouse has it and no write is out, which may land after this one would. The
        daemon snaps it to the sensor's step."""
        wanted = round(self.dpi_row.get_value())
        if wanted == self._dpi_shown and not self._dpi_unanswered:
            return
        self._dpi_unanswered += 1

        def done(reply: dict | None) -> None:
            self._dpi_unanswered -= 1
            if reply is None:
                # Refused, and said by the window: the mouse's DPI goes back in the row.
                self.refresh()
                return
            # Shown at once, snapped or not, unless the row is the user's again: then kept
            # as the mouse's DPI alone, which _send_dpi compares the row with once it settles.
            if self._dpi_held(False):
                self._dpi_shown = reply["dpi"]
            else:
                self._put_dpi(reply["dpi"])
            if reply["dpi"] != reply["requested"]:
                self._toast(DPI_SNAPPED.format(reply["dpi"]))
        self._call(self._for_shown({"command": "mouse_dpi", "value": wanted}, self._dpi_for),
                   done, timeout=DPI_WRITE_WAIT)

    def _offer_write(self, dialog: Adw.AlertDialog, key: str, label: str,
                     appearance: Adw.ResponseAppearance, request: dict,
                     layer: dict | None = None, group: str = "onboard") -> None:
        """Cancel and `label` on `dialog` (style.offer); `label` sends `request` through
        _write, from a row of `group`, to the mouse, and with `layer` the game profile, the
        dialog was opened for, not whichever the page shows when it is answered."""
        product = self._product
        offer(dialog, key, label, appearance,
              lambda: self._write(request, product=product, layer=layer, group=group))

    def on_binding(self, row: Adw.ComboRow, _p: GObject.ParamSpec, name: str) -> None:
        if self._loading.active:
            return
        action, macros = read_pick(row, MOUSE_CHOICES)
        if action is not None:
            self._bind(name, action)
            return
        self._capture(name, macros=macros)

    def on_change(self, name: str) -> None:
        """Change… on a row already bound to a key or a macro, opened on the macro list for a
        macro: picking the row's entry again does nothing (change_buttons)."""
        self._capture(name, macros=read_pick(self.rows[name], MOUSE_CHOICES)[1])

    def _capture(self, name: str, macros: bool) -> None:
        """Ask for a key or a macro for one button, and bind what comes back on the layer
        the window was on when the dialog opened, for the mouse it showed: the polls go on
        while it is open, and an M key, the tray or a program can move the window's
        meanwhile, as another mouse's selection can move the rows. The dialog has no Unbind:
        Nothing in the row clears a button."""
        # The worker fills the dialog's macro list; a mouse button takes a macro as a G key does.
        dialog = CaptureDialog(self._parent, self._labels[name], worker=self._worker,
                               can_unbind=False)
        picked = False
        layer = named_layer(self._parent.layer)
        product = self._product

        def done(picked_spec: str | None) -> None:
            nonlocal picked
            picked = True
            # Set, a key or a macro picked: with no Unbind, the dialog hands on no None.
            spec = cast("str", picked_spec)
            # The keyboard's Back and Forward keys capture as `back` and `forward`, which
            # a mouse binding reads as the mouse's back and forward: `key_back` is the key.
            if spec.lower() in MOUSE_ACTIONS:
                spec = f"key_{spec.lower()}"
            self._bind(name, spec, layer, product)

        def closed(_dialog: Adw.Dialog) -> None:
            # Canceling leaves the dropdown on "Keystroke…" or "Macro…" with
            # nothing bound; put it back to what the daemon has. The bind is
            # queued before this read, so the read sees it.
            if not picked:
                self._show_held(name)
            self.refresh()
        dialog.run(done, macros=macros)
        dialog.connect("closed", closed)

    def _bind(self, name: str, spec: str, layer: dict | None = None,
              product: str | None = None) -> None:
        """Bind one button of the mouse model the rows show, or `product`'s; the daemon
        persists it.

        `layer`, the one a capture dialog was opened on, or else the window's at the pick, is
        laid over the window's (Window.on_layer), so the daemon refuses the bind, and its
        Undo, once the window's layer has left it (layer_changed). The row takes the reply's
        binding while it still shows that mouse's model, or goes back on a refusal, now
        rather than at the next poll.
        """
        target = product or self._product
        if layer is None:
            # Named at the pick: an Undo sent after an M key or a game profile moved the
            # window's layer would put this layer's binding on that one.
            layer = named_layer(self._parent.layer)

        def done(reply: dict | None) -> None:
            if reply is None:
                self._show_held(name)
                self.refresh()
                return
            # A bind for a model the rows no longer show leaves them, and names it.
            moved = not self._shows_model(target)
            # What the button did, which Undo binds again on the same layer whenever the
            # bind replaced or cleared it, as on the Keyboard tab and the G keys.
            bound = reply["binding"]
            was = None if moved else self._bindings.get(name)
            if not moved:
                self._bindings[name] = bound
            self._show_held(name)
            button = f"{reply['mouse']} {name.upper()}" if moved else self._labels[name]
            self._toast(f"{button} unbound" if bound == UNBOUND
                        else f"{button} → {binding_words(bound, MOUSE_CHOICES)}",
                        undo=(lambda: self._bind(name, was, layer, target))
                        if was not in (None, UNBOUND) and was != bound else None)
        request = self._for_shown({"command": "mouse_bind", "key": name, "value": spec},
                                  target)
        self._call({**request, "layer": layer}, done)

    def on_effect(self, row: Adw.ComboRow, _p: GObject.ParamSpec) -> None:
        """Send the effect picked, and read the mouse back if it is refused:
        the row stayed on what was refused."""
        if self._loading.active:
            return
        index = row.get_selected()
        if not 0 <= index < len(self._effects):
            return
        self._lighting_sent += 1
        self._call(self._for_shown({"command": "mouse_effect", "value": self._effects[index],
                                    "color": format_color(self.picker.color)}),
                   lambda reply: self.refresh(force=True) if reply is None else None)

    def _preview(self, color: int) -> None:
        # Coalesced like the keyboard's: a drag never builds a backlog.
        self._worker.preview(self._for_shown({"command": "mouse_color",
                                              "value": format_color(color)}),
                             self._on_preview)

    def _commit(self, color: int) -> None:
        self._lighting_sent += 1
        self._call(self._for_shown({"command": "mouse_color", "value": format_color(color)}))

    def _on_preview(self, reply: Reply) -> None:
        self._preview_error = preview_reply(reply, self._preview_error,
                                            self._set_status, self._refused)

    # -- DPI notices -----------------------------------------------------

    def _adopt(self, row: Adw.SwitchRow, value: bool) -> None:
        """Put a switch on the daemon's value without its handler sending it.
        Adw.SwitchRow notifies within set_active (libadwaita 1.9.1, which the
        widget tests run)."""
        self._adopting = True
        try:
            row.set_active(value)
        finally:
            self._adopting = False

    def show_dpi_notices(self, value: bool | None) -> None:
        """The DPI notices switch as the daemon has it, left as it is while a click of it is
        unanswered: a status read sent before the click would put the old value back."""
        self.dpi_notices = bool(value)
        self.notice_row.set_visible(True)
        if not self._notices_unanswered:
            self._adopt(self.notice_row, self.dpi_notices)

    def on_dpi_notices(self, row: Adw.SwitchRow, _p: GObject.ParamSpec) -> None:
        """Send the switch and take the reply's value; a refusal puts it back.

        Every click is sent, one back to the daemon's value included: while an earlier click is
        unanswered, that value is not what the daemon will hold, and the worker's order makes
        the last click win. No loading guard: _adopting marks the switch set from the
        daemon."""
        if self._adopting:
            return
        self._notices_unanswered += 1

        def done(reply: dict | None) -> None:
            self._notices_unanswered -= 1
            self.show_dpi_notices(self.dpi_notices if reply is None
                                  else reply.get("dpi_notices"))
        self._call({"command": "dpi_notices", "value": row.get_active()}, done)

    # -- color sync ------------------------------------------------------

    def follow_keyboard(self) -> None:
        if self.sync:
            self.picker.set_color(self._kb_base())

    def _apply_sync_state(self) -> None:
        """The mouse's own color controls work only when it is reachable and
        not following the keyboard; synced, its picker shows the keyboard's. Show its own
        lighting appears on a mouse that waits to claim its lighting alone (the reply's
        `lighting_shown`), and works with those controls while the mouse shows Gearbench's."""
        own = self.ok and self.answering and not self.sync
        self.picker.row.set_sensitive(own)
        self.effect_row.set_sensitive(own)
        self.own_row.set_visible(self._lighting_shown is not None)
        self.own_row.set_sensitive(own and self._lighting_shown == GEARBENCH_LIGHTING)
        self.follow_keyboard()

    def on_own(self, _row: Adw.ButtonRow) -> None:
        """Give the mouse its own lighting back, then read it again for whose it shows."""
        self._lighting_sent += 1
        self._call(self._for_shown({"command": "mouse_effect", "release": True}),
                   lambda _reply: self.refresh(force=True))

    def set_sync(self, value: bool) -> None:
        """Adopt the daemon's sync state without asking it to change."""
        self.sync = value
        self._adopt(self.sync_row, value)
        self._apply_sync_state()

    def take_leader(self, leader: str | None) -> None:
        """The follow switch named for the keyboard that leads, by its key, as a keyboard's
        is (LightingCard.show_leader); "Follow the keyboard" before a status names one."""
        self.sync_row.set_title(FOLLOW.format(followed(leader)))

    def take_sync(self, value: bool, ask: Callable[[], bool]) -> None:
        """A status's sync switch, which the tray or the CLI can turn: adopted when it changed,
        and the mouse read again when it went off, since its own color is only in a `mouse`
        reply. `ask` gives refresh's `ask` then."""
        if value == self.sync:
            return
        self.set_sync(value)
        if not value:
            self.refresh(ask=ask())

    def on_sync(self, row: Adw.SwitchRow, _p: GObject.ParamSpec) -> None:
        """Send the switch; a refusal puts it back. Every click is sent, with
        no loading guard, as on_dpi_notices does."""
        if self._adopting:
            return
        wanted = row.get_active()

        def done(reply: dict | None) -> None:
            if reply is None:
                self.set_sync(self.sync)        # refused: put the switch back
                return
            self.set_sync(bool(reply.get("mouse_sync_color")))
            if not self.sync:
                # Synced, the picker showed the keyboard's color; the mouse's
                # own is only in a `mouse` reply.
                self.refresh()
            if wanted and not reply.get("mouse_lights"):
                self._toast("Saved; the mouse follows once it is connected")
        self._call({"command": "mouse_sync", "value": wanted}, done)
