"""The mouse tab. It polls the daemon on its own 5 s timer, with its own re-entry guard."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

from gi.repository import Adw, GLib, GObject, Gtk

from ..config import (
    MOUSE_ACTIONS,
    MOUSE_BUTTONS,
    MOUSE_LABELS,
    ConfigError,
    format_color,
    macro_name,
    parse_color,
)
from ..mouse import (
    CABLED_IDS,
    GEARBENCH_LIGHTING,
    OWN_LIGHTING,
    PROFILE_WRITE_WAIT,
    WIDEST_DPI_RANGE,
    HostState,
    host_state,
    model_key,
    profile_words,
    runs_another_profile,
)
from .board import shown_binding
from .color import ColorPicker
from .dialogs import CaptureDialog
from .onboard_card import OnboardCard
from .stages_card import StagesCard
from .style import (
    ASLEEP,
    DAEMON_WAIT,
    MOUSE_ASLEEP,
    DeviceView,
    EntryTexts,
    Switcher,
    explain,
    route_text,
    set_dot,
    show_battery,
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
    preview_reply,
    unreachable,
)

if TYPE_CHECKING:                       # pragma: no cover
    from .window import Window

    class Write(Protocol):
        """`MousePage._write`, as the page hands it to a card."""

        def __call__(self, request: dict, callback: Callable[[dict], None] | None = None,
                     product: str | None = None, layer: dict | None = None) -> None: ...

    class OfferWrite(Protocol):
        """`MousePage._offer_write`, as the page hands it to a card."""

        def __call__(self, dialog: Adw.AlertDialog, key: str, label: str,
                     appearance: Adw.ResponseAppearance, request: dict,
                     layer: dict | None = None) -> None: ...

#: A button row's built-in actions, in config.MOUSE_ACTIONS order.
MOUSE_CHOICES = [("none", "Nothing"), ("back", "Back"), ("forward", "Forward"),
                 ("dpi-shift", "DPI shift"), ("dpi-up", "DPI up"),
                 ("dpi-down", "DPI down"), ("dpi-cycle", "DPI cycle")]
#: The entries after the built-in actions; each opens the capture dialog. Last, so a built-in
#: action's row index is its index in MOUSE_CHOICES.
MOUSE_KEY_CHOICE = "Keystroke…"
MOUSE_MACRO_CHOICE = "Macro…"
#: One tooltip line per entry, in the rows' order: MOUSE_CHOICES, then the key and macro
#: entries.
MOUSE_TIPS = ("The button does nothing.",
              "Browser back, sent through the virtual mouse.",
              "Browser forward, sent through the virtual mouse.",
              "Drops to the shift stage while held.",
              "Steps up through the mouse's DPI stages.",
              "Steps down through the mouse's DPI stages.",
              "Steps up through the mouse's DPI stages, from the last back to the first.",
              "Pick a key or a combination for this button.",
              "Pick one of your macros for this button.")

#: The DPI entry's (low, high) while the shown mouse has not reported its sensor's.
DPI_RANGE = WIDEST_DPI_RANGE
#: How long on_dpi waits for the daemon, in seconds: its set_dpi gives a mouse that does not
#: answer three tries of 1 s (Mouse.set_dpi), which outlast Window.call's 3 s.
DPI_WAIT = 10.0

#: The Host mode row's subtitle, by state.
HOST_ON = ("Gearbench drives the buttons, the stages and the rate; the onboard profile takes "
           "over when it stops")
HOST_OFF = ("The mouse runs onboard profile 1; G4 to G9 reach Gearbench if it has them "
            "enabled, below")
#: In place of HOST_OFF on a mouse whose event node is not read, whose buttons reach Gearbench
#: in host mode alone: the Buttons group says what they do meanwhile.
HOST_OFF_ALONE = "The mouse runs onboard profile 1"
#: Off, before the mouse's switch back to onboard mode lands; the banner's mouse_left_in_host
#: says why.
HOST_NOT_LEFT = ("Host mode is off, but the mouse has not left it yet: its programmable buttons do "
                 "nothing")
#: On and refused, with the daemon's reason where HOST_STUCK and the banner's mouse_not_host
#: put it: after "onboard profile 1" it would read as a word on the profile.
HOST_NOT_TAKEN_WHY = "Host mode did not take ({}), so the mouse runs onboard profile 1"
#: On and refused, and the switch back to onboard mode refused too, with the reason: the
#: banner's mouse_left_in_host, and `gearbench-ctl mouse`'s "host, stuck there".
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
#: Under the DPI notices switch, the owner's decision of 2026-09-24.
DPI_NOTICE_HELP = "Show the new DPI when a DPI button changes it"
#: The Buttons group's description on a mouse whose event node is read, the G502 X PLUS.
BUTTONS_HELP = ("What G4 to G9 do while Gearbench runs: all six in host mode, otherwise after "
                "Enable… under Onboard profile 1.")
#: The same on a mouse whose buttons reach Gearbench in host mode alone.
BUTTONS_HOST_HELP = "The bindings work in host mode alone."
#: In Per-key mode a synced mouse follows the background, black unless one was set, so the
#: row says so. Its brightness and idle dimming: the owner's decision of 2026-09-28.
SYNC_HELP = ("The mouse takes the keyboard's color (in Per-key mode, the background) and dims "
             "with it")
#: The Lighting group's description for a mouse that waits to claim its lighting, by whose
#: lighting it shows (`mouse`'s `lighting_shown`); none for another mouse.
LIGHTING_SHOWN_HELP = {
    OWN_LIGHTING: "Shows its own lighting until Gearbench sends a color or effect",
    GEARBENCH_LIGHTING: "Shows the lighting Gearbench sends",
}
#: What a screen reader calls the switcher at the top, which picks the mouse the tab shows.
DEVICE = "Device"
#: The status page's titles until a mouse is seen: before the first reply, and after the
#: daemon refuses `mouse`, over its reason (DeviceView).
LOOKING = "Looking for the mouse…"
NO_MOUSE = "Mouse not available"
#: A write's toast for a game profile's note too long to toast whole (short_note).
PROFILE_SAVED = "Saved to the game profile"

def binding_text(spec: str) -> str:
    """A mouse binding in its row's words: the dropdown's label, or as the board shows it."""
    return dict(MOUSE_CHOICES).get(spec) or shown_binding(spec)


#: The Effect row's words for an effect named for something with a name of its own, whose
#: case the row's sentence case would lose: G HUB's names for the Falcon's two.
EFFECT_LABELS = {"millennium-falcon": "Millennium Falcon",
                 "millennium-falcon-inactive": "Millennium Falcon, inactive"}


def effect_label(name: str) -> str:
    """An effect's name as the Effect row shows it, in the Keyboard tab's Mode row's case:
    "neural-wave" is "Neural wave"."""
    return EFFECT_LABELS.get(name) or name[:1].upper() + name[1:].replace("-", " ")


def reset_target(layer: tuple[int | None, str | None]) -> tuple[int | None, str | None]:
    """Restore defaults' target on the window's (M profile, game profile) layer: the loaded
    game profile alone, or else the M profile."""
    return (None, layer[1]) if layer[1] is not None else layer


class MousePage:
    """The Mouse tab: the switcher between mice, status, tuning, lighting and the
    buttons. The DPI stages with the report rate, and the Onboard profile 1 group, are cards
    of their own (`stages`, `onboard`), built here with what they need.

    `parent` is the window, whose layer the button bindings and Restore defaults are for.
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
        #: The sensor's (low, high), from the mouse reply, which the DPI entry is checked
        #: against.
        self._dpi_range = DPI_RANGE
        #: The mouse the last reply was about, by product id.
        self._product: str | None = None
        #: The mouse whose DPI the DPI entry shows, which keeps its text across polls while
        #: edited: a DPI typed for one mouse must not go to another polled meanwhile.
        self._dpi_for: str | None = None
        #: That mouse's sensor range, which on_dpi checks against: the polled mouse's could
        #: refuse a DPI the G502 X PLUS takes for being past the G305's.
        self._dpi_for_range = DPI_RANGE
        #: The text the page last put in the DPI entry, by a poll or by an apply.
        self._shown = EntryTexts()
        #: A _write is in flight: to onboard profile 1, about a second, or to a loaded game
        #: profile's own stages or rate. The poll would otherwise re-enable the rows under it.
        self.writing = False
        #: The last failure shown, so a mouse that is off does not toast at every poll; the
        #: preview keeps its own.
        self._error: str | None = None
        self._preview_error: str | None = None
        #: The daemon's view of the mouse: reachable, and following the keyboard's color.
        self.ok = False
        self.sync = False
        #: The daemon's DPI notices switch, None until a status gives it.
        self.dpi_notices: bool | None = None
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
        # A status page in place of the rows until a mouse is seen.
        self.view = DeviceView(page, "input-mouse-symbolic", LOOKING)
        toolbar = Adw.ToolbarView(content=self.view.stack)
        # Shown for two mice or more, as the Headset tab's for two headsets: with one, the
        # status group's title names it.
        self.device_switcher = Switcher(DEVICE, self.on_device)
        toolbar.add_top_bar(self.device_switcher.widget)
        toolbar.add_top_bar(self.profile_banner)
        return toolbar

    def _status_group(self) -> Adw.PreferencesGroup:
        """Which mouse this is and whether it is there, for a model not yet run on the row
        that asks for a device report from it, and the Host mode switch."""
        # Retitled from the reply: which mouse this is is unknown until one answers.
        status = self.status_group = Adw.PreferencesGroup(title="Mouse")
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
        """The DPI, the DPI notices switch and the report rate."""
        # Stacked rows in one group, as every other group here and on the
        # desktop has them (the owner's request of 2026-09-24).
        tuning = self.tuning_group = Adw.PreferencesGroup(title="Tuning")
        # An entry with an apply button, not a spin row, whose every + and - step is a write to
        # the mouse: this sends one, when the value is confirmed.
        self.dpi_row = Adw.EntryRow(title="DPI", show_apply_button=True)
        self.dpi_row.connect("apply", self.on_dpi)
        tuning.add(self.dpi_row)
        # A switch of its own, since GNOME's for the app would silence the battery notices too
        # (the owner's decision of 2026-09-24). A daemon setting, so not a device row; shown
        # from the first status.
        self.notice_row = Adw.SwitchRow(title="DPI notices", subtitle=DPI_NOTICE_HELP,
                                        visible=False)
        self.notice_row.connect("notify::active", self.on_dpi_notices)
        tuning.add(self.notice_row)
        tuning.add(self.stages.build_rate_row())
        return tuning

    def _lighting_group(self) -> Adw.PreferencesGroup:
        """The sync switch, the effect and the color."""
        light = self.light_group = Adw.PreferencesGroup(title="Lighting")
        self.sync_row = Adw.SwitchRow(title="Sync with keyboard", subtitle=SYNC_HELP)
        self.sync_row.connect("notify::active", self.on_sync)
        light.add(self.sync_row)
        self.effect_row = Adw.ComboRow(title="Effect",
                                       model=Gtk.StringList.new([effect_label("static")]))
        self.effect_row.connect("notify::selected", self.on_effect)
        light.add(self.effect_row)
        self.picker = ColorPicker(self._preview, self._commit, self._toast,
                                  subtitle="Live on the mouse as you drag")
        light.add(self.picker.row)
        return light

    def _buttons_group(self) -> Adw.PreferencesGroup:
        """What G4 to G9 do, a row each."""
        buttons = self.buttons_group = Adw.PreferencesGroup(title="Buttons",
                                                            description=BUTTONS_HELP)
        # Which layer a binding is written to, as the Keyboard tab says it:
        # the loaded game profile's, or the M profile's. Not markup.
        self.editing_row = Adw.ActionRow(title="Editing", use_markup=False)
        # Decoration, so presentation: the title says it, and a plain image is read out
        # as one with no name.
        self.editing_row.add_prefix(Gtk.Image(icon_name="document-edit-symbolic",
                                              accessible_role=Gtk.AccessibleRole.PRESENTATION))
        buttons.add(self.editing_row)
        choices = ([label for _v, label in MOUSE_CHOICES]
                   + [MOUSE_KEY_CHOICE, MOUSE_MACRO_CHOICE])
        for name in MOUSE_BUTTONS:
            row = Adw.ComboRow(title=MOUSE_LABELS[name], model=Gtk.StringList.new(choices))
            explain_choices(row, MOUSE_TIPS)
            row.connect("notify::selected", self.on_binding, name)
            change = Gtk.Button(label="Change…", valign=Gtk.Align.CENTER, visible=False,
                                tooltip_text="Pick another key or macro for this button")
            change.connect("clicked", lambda _b, n=name: self.on_change(n))
            row.add_suffix(change)
            self.rows[name] = row
            self.change_buttons[name] = change
            buttons.add(row)
        self.defaults_row = Adw.ActionRow(
            title="Default bindings",
            subtitle="As from the factory; nothing where Gearbench lacks the action")
        defaults = Gtk.Button(label="Restore defaults", valign=Gtk.Align.CENTER)
        defaults.connect("clicked", self.on_bind_defaults)
        self.defaults_row.add_suffix(defaults)
        buttons.add(self.defaults_row)
        return buttons

    def show_problems(self, problems: list[dict]) -> None:
        """Show the daemon's mouse problems on the banner, a line each, or take it down.

        A node refused (mouse_no_permission) is left out while the tab has no mouse to show,
        since the refused `mouse` reply says it then (_failed). Beside a mouse shown, that
        reply is about that mouse, and only the banner says it."""
        shown = (p for p in problems if self.ok or p["code"] != "mouse_no_permission")
        lines = [message[:1].upper() + message[1:]
                 for message in (str(p.get("message") or "") for p in shown) if message]
        if lines:
            self.profile_banner.set_title("\n".join(lines))
        self.profile_banner.set_revealed(bool(lines))

    def set_editing(self, text: str) -> None:
        """Say which layer the button bindings are written to."""
        self.editing_row.set_title(text)

    def on_bind_defaults(self, _b: Gtk.Button | None) -> None:
        """Put every button back, behind a confirmation: it discards the lot."""
        # The daemon writes bindings to the loaded game profile when there is
        # one, so the dialog has to say which of the two is about to change.
        win = self._parent
        where = f"the game profile {win.overlay}" if win.overlay else "the active M profile"
        dialog = Adw.AlertDialog(heading="Restore the default bindings?")
        defaults = "; ".join(f"{self._labels[name]}: {binding_text(spec)}"
                             for name, spec in self._defaults.items())
        explain(dialog, paragraphs=(
            f"In {where}, the {self._name}'s buttons return to their defaults"
            + (f": {defaults}." if defaults else "."),
            ("Anything bound to them, macros included, is replaced. Another mouse model's "
             "buttons keep theirs."),
        ))
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("restore", "Restore")
        dialog.set_response_appearance("restore", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        layer, product = win.layer, self._product
        dialog.connect("response", lambda _d, r: (self._do_bind_defaults(layer, product)
                                                  if r == "restore" else None))
        dialog.present(self._parent)

    def _do_bind_defaults(self, layer: tuple[int | None, str | None],
                          product: str | None) -> None:
        """The confirmed answer, on `layer`, the window's (M profile, game profile) when the
        dialog opened, for `product`, the mouse it named, which the shared selection can move
        off meanwhile. The request names the window's layer as it is now (on_layer), so an
        answer after a load, an unload or an M key would reset one the dialog did not name."""
        if reset_target(self._parent.layer) != reset_target(layer):
            self._toast("Nothing was restored: the profile being edited changed while the "
                        "dialog was open")
            return
        target = product or self._product

        def done(reply: dict | None) -> None:
            if reply is None:
                return
            # The rows take the reply's bindings only while they still show that model; a
            # reset of another names it, as _bind's toast does.
            if self._shows_model(target):
                self._refresh_bindings(reply.get("bindings") or {})
                self._toast("The buttons are back to their defaults")
            else:
                self._toast(f"The {reply['mouse']}'s buttons are back to their defaults")
        self._call(self._for_shown({"command": "mouse_bind_defaults"}, target), done)

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
        """Show whether a _write is in flight, and hold off a second one: the cards' rows,
        whose changes rewrite onboard profile 1 or a loaded game profile's own stages and
        rate, are off while one is, and otherwise follow the last reply (_on_info, _failed).

        The DPI row is left alone: a live write to the sensor, not to flash. At the end the
        rows go back to what the last reply left: a poll can have found the daemon gone or the
        mouse not answering meanwhile.
        """
        self.writing = busy
        usable = not busy and self.ok and self.answering
        self.stages.set_usable(usable)
        self.onboard.show_writing(busy, usable)

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
        """The color picker is between press and release: the window's polls and file
        reads wait for it."""
        return self.picker.dragging

    def flush(self) -> bool:
        """Send a picker key run still settling, now, for the window's
        close. True when there was one."""
        return self.picker.flush()

    def close(self) -> None:
        """Take the picker's rules and the stage dots' sheet off the display,
        which holds them, not the page or its card."""
        self.picker.destroy()
        self.stages.close()

    def _failed(self, detail: str, title: str = NO_MOUSE) -> None:
        """Show that this tab holds nothing usable, and say why once.

        Until a mouse is seen, the status page says it under `title`, with no toast.
        """
        self.ok = False
        set_dot(self.state_dot, False)
        self.state_row.set_subtitle(detail[:1].upper() + detail[1:])
        # The node and the battery reading were the gone mouse's, as the Keyboard tab has it.
        self.state_row.set_tooltip_text(None)
        show_battery(self.battery_row, self.battery_icon, None)
        self.dpi_row.set_sensitive(False)
        self._set_writing(self.writing)
        self._apply_sync_state()
        if not self.view.seen:
            self.view.missing(title, detail)
            return
        if detail != self._error:
            self._error = detail
            self._toast(f"Mouse: {detail}")

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
            self._failed(str(info.get("error", "not connected")))
            # The layer's bindings for the selected mouse's model, which a refusal for want of
            # a mouse carries: they can change with no mouse attached, by an M key or a game
            # profile. Taken for the model the rows show alone.
            shown = model_key(int(self._product, 16)) if self._product else None
            if isinstance(info.get("bindings"), dict) and info.get("bindings_model") == shown:
                self._refresh_bindings(info["bindings"])
            return
        self.view.found()
        previous, self._product = self._product, str(info.get("product") or "") or None
        fresh = force or self._product != previous
        self.ok = True
        self._error = None
        self._show_answering(info)
        self.show_devices(info)
        self.show_capabilities(info)
        self._show_status(info)
        self._show_dpi(info, force, fresh)
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
        """The DPI row, sensitive while the mouse answers, and the dot. The cards' rows
        follow in _on_info, after the cards take the reply."""
        # Asleep or switched off behind its receiver: attached, and every
        # write to it would wait out its timeouts. Said by the dot rather than
        # a toast, which would come back every poll.
        self.answering = bool(info.get("answering"))
        self.dpi_row.set_sensitive(self.answering)
        set_dot(self.state_dot, True if self.answering else ASLEEP)

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
        show_battery(self.battery_row, self.battery_icon, info.get("battery"),
                     None if self.answering else info.get("battery_at"))

    def _show_dpi(self, info: dict, force: bool, fresh: bool) -> None:
        """The DPI entry, and the sensor's range it is checked against. `fresh`: the
        reply is about another mouse than the last, or `force`."""
        rng = info.get("dpi_range") or []
        if len(rng) == 3:
            # In a tooltip: an Adw.EntryRow has a title and no subtitle.
            self._dpi_range = (rng[0], rng[1])
            self.dpi_row.set_tooltip_text(
                f"{rng[0]} to {rng[1]}, in steps of {rng[2]}")
        elif fresh:
            self._dpi_range = DPI_RANGE
            self.dpi_row.set_tooltip_text(None)
        # Cleared on a switch as well as filled: an empty entry is this mouse's too, or a DPI
        # typed into it would go to the last one.
        if (info.get("dpi") or fresh) and (
                force or not self._shown.typing(self._parent, self.dpi_row)):
            self._shown.show(self.dpi_row, str(int(info["dpi"])) if info.get("dpi") else "")
            self._dpi_for, self._dpi_for_range = self._product, self._dpi_range

    def _show_lighting(self, info: dict, force: bool, current: bool) -> None:
        """The Effect row, the sync switch, the picker's color and whose lighting the mouse
        shows. `current` False: a color or effect was sent after the reply was asked for, so
        its effect, color and lighting shown are the ones that change replaced, and the rows
        keep the change."""
        effects = list(info.get("effects") or ["static"])
        if effects != self._effects:
            self._effects = effects
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
        # What the daemon last wrote to the mouse, unless a color was sent
        # since the reply was asked for, a drag is under way, a color is
        # being typed, or the picker is showing the keyboard's color instead.
        if (current and info.get("color") and not self.sync and not self.picker.dragging
                and (force or not self.picker.typing(self._parent))):
            try:
                self.picker.set_color(parse_color(info["color"], "mouse color"))
            except ConfigError as exc:
                self._toast(str(exc))

    def show_devices(self, info: dict) -> None:
        """Offer a choice of mouse when more than one is attached.

        The daemon drives them all and names which one its reply is about. A pick (on_device)
        moves the daemon's selection, which the CLI shares: the mouse the tab shows and the one
        a command without `device` acts on.
        """
        devices = info.get("devices") or []
        self._devices = [str(d.get("product")) for d in devices]
        here = str(info.get("product") or "")
        self.device_switcher.show([str(d.get("name") or d.get("product")) for d in devices],
                                  self._devices.index(here) if here in self._devices else None,
                                  len(devices) > 1)

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
        name = self._name = str(info.get("name") or "Mouse")
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
        other = (profile_words(running) if isinstance(running, int)
                 and runs_another_profile(running) else None)
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
                           else "A mouse is stuck in host mode")
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
        self._defaults = {name: str(b.get("default") or "none") for name, b in shown.items()}
        for name, row in self.rows.items():
            row.set_visible(name in shown)
            if name in shown:
                self._labels[name] = str(shown[name].get("label") or MOUSE_LABELS[name])
                row.set_title(self._labels[name])
            row.set_sensitive(self._idle is None)
        # A group's description is markup, and the idle reason is the daemon's, naming the
        # mouse.
        self.buttons_group.set_description(
            GLib.markup_escape_text(self._idle[:1].upper() + self._idle[1:] + ".")
            if self._idle
            else BUTTONS_HELP if table.get("event_node") else BUTTONS_HOST_HELP)

    def _refresh_bindings(self, bindings: dict[str, str]) -> None:
        """Put each button's dropdown on its binding from the daemon.

        A built-in action selects its own entry; anything else selects Keystroke… or Macro…
        with the binding underneath in the window's words (shown_binding), so a row says what
        the button does.
        """
        self._bindings = dict(bindings)
        for name in self.rows:
            self._show_held(name)

    def _show_held(self, name: str) -> None:
        """Put one button's row on what the daemon last said it holds; on a mouse whose
        bindings do nothing now, its subtitle says what profile 1 has it do."""
        spec = self._bindings.get(name, "none")
        values = [v for v, _label in MOUSE_CHOICES]
        row = self.rows[name]
        self.change_buttons[name].set_visible(spec not in values)
        if spec in values:
            self._loading.put_selected(row, values.index(spec))
            row.set_subtitle("")
        else:
            self._loading.put_selected(
                row, len(values) + 1 if macro_name(spec) is not None else len(values))
            row.set_subtitle(shown_binding(spec))
        job = self._jobs.get(name)
        if self._idle is not None and job:
            row.set_subtitle(f"In profile 1: {job}")

    # -- writes -----------------------------------------------------------

    def _write(self, request: dict, callback: Callable[[dict], None] | None = None,
               product: str | None = None, layer: dict | None = None) -> None:
        """A command that rewrites onboard profile 1, or a loaded game profile's own stages or
        rate.

        Locks the rows that could start a second write and spins until the reply lands, then
        toasts its note and re-reads the mouse: a stage edit can move its live DPI. `callback`
        gets the reply if the write succeeded. Sent through the window's `call`, which names
        the layer and says a refusal or a daemon that did not answer, with the 30 s a flash
        write can take.

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
                # A game profile's note is one sentence (Dpi.tuning_note).
                tuned = reply.get("written") == "game_profile"
                toast_note(self._toast, reply["note"], PROFILE_SAVED if tuned
                           else "Written to onboard profile 1", one_sentence=tuned)
            self.refresh(force=True)
            if callback is not None:
                callback(reply)
        self._set_writing(True)
        # A round trip that raised reads nothing back: the daemon did not answer.
        self._call(request, done, lambda _error: self._set_writing(False),
                   timeout=PROFILE_WRITE_WAIT)

    def on_dpi(self, row: Adw.EntryRow) -> None:
        """The DPI entry was confirmed: one write, with what it asked for.

        The daemon snaps to the sensor's step, so this checks only the range it would refuse.
        No loading guard: apply comes only from Enter or the apply button, never from set_text,
        so a guard could only drop a real one.
        """
        # The range of the mouse the entry was filled for, once a poll is about another.
        if self._dpi_for is not None and self._dpi_for != self._product:
            low, high = self._dpi_for_range
        else:
            low, high = self._dpi_range
        try:
            wanted = int(row.get_text().strip())
        except ValueError:
            self._toast(f"DPI is a number between {low} and {high}")
            return
        if not low <= wanted <= high:
            self._toast(f"{wanted} DPI is outside {low} to {high}")
            return
        self._shown.took(row)
        request = {"command": "mouse_dpi", "value": wanted}
        if self._dpi_for is not None:
            request["device"] = self._dpi_for

        def done(reply: dict | None) -> None:
            if not reply:
                return
            # Shown at once, snapped or not: a poll answered before the write lands after the
            # apply with the old DPI. Not over a row refilled for another mouse, or new typing.
            if self._dpi_for == request.get("device") and not self._shown.typing(
                    self._parent, row):
                self._shown.show(row, str(reply["dpi"]))
            if reply["dpi"] != reply["requested"]:
                self._toast(f"DPI snapped to {reply['dpi']}")
        self._call(request, done, timeout=DPI_WAIT)

    def _offer_write(self, dialog: Adw.AlertDialog, key: str, label: str,
                     appearance: Adw.ResponseAppearance, request: dict,
                     layer: dict | None = None) -> None:
        """Cancel and `label` on `dialog`; `label` sends `request` through _write, to the
        mouse, and with `layer` the game profile, the dialog was opened for, not whichever
        the page shows when it is answered."""
        dialog.add_response("cancel", "Cancel")
        dialog.add_response(key, label)
        dialog.set_response_appearance(key, appearance)
        dialog.connect("response", lambda _d, r, p=self._product: (
            self._write(request, product=p, layer=layer) if r == key else None))

    def on_binding(self, row: Adw.ComboRow, _p: GObject.ParamSpec, name: str) -> None:
        if self._loading.active:
            return
        index = row.get_selected()
        if index < len(MOUSE_CHOICES):
            self._bind(name, MOUSE_CHOICES[index][0])
            return
        self._capture(name, macros=index == len(MOUSE_CHOICES) + 1)

    def on_change(self, name: str) -> None:
        """Change… on a row already bound to a key or a macro, opened on the macro list for a
        macro: picking the row's entry again does nothing (change_buttons)."""
        self._capture(name, macros=self.rows[name].get_selected() == len(MOUSE_CHOICES) + 1)

    def _capture(self, name: str, macros: bool) -> None:
        """Ask for a key or a macro for one button, and bind what comes back on the layer
        the window was on when the dialog opened, for the mouse it showed: the polls go on
        while it is open, and an M key, the tray or a program can move the window's
        meanwhile, as another mouse's selection can move the rows."""
        # The worker fills the dialog's macro list; a mouse button takes a macro as a G key does.
        dialog = CaptureDialog(self._parent, self._labels[name], worker=self._worker)
        picked = False
        profile, overlay = self._parent.layer
        layer = {"profile": profile, "overlay": overlay}
        product = self._product

        def done(spec: str | None) -> None:
            nonlocal picked
            picked = True
            # The keyboard's Back and Forward keys capture as `back` and `forward`, which
            # a mouse binding reads as the mouse's back and forward: `key_back` is the key.
            if spec and spec.lower() in MOUSE_ACTIONS:
                spec = f"key_{spec.lower()}"
            self._bind(name, spec or "none", layer, product)

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

        `layer`, the one a capture dialog was opened on, is laid over the window's
        (Window.on_layer), so the daemon refuses the bind once the window's layer has left
        it (layer_changed). The row takes the reply's binding while it still shows that
        mouse's model, or goes back on a refusal, now rather than at the next poll.
        """
        target = product or self._product

        def done(reply: dict | None) -> None:
            if reply is None:
                self._show_held(name)
                self.refresh()
                return
            # A bind for a model the rows no longer show leaves them, and names it.
            moved = not self._shows_model(target)
            if not moved:
                self._bindings[name] = reply["binding"]
            self._show_held(name)
            button = f"{reply['mouse']} {name.upper()}" if moved else self._labels[name]
            self._toast(f"{button} → {binding_text(reply['binding'])}")
        request = self._for_shown({"command": "mouse_bind", "key": name, "value": spec},
                                  target)
        self._call(request if layer is None else {**request, "layer": layer}, done)

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
        """The DPI notices switch as the daemon has it."""
        self.dpi_notices = bool(value)
        self.notice_row.set_visible(True)
        self._adopt(self.notice_row, self.dpi_notices)

    def on_dpi_notices(self, row: Adw.SwitchRow, _p: GObject.ParamSpec) -> None:
        """Send the switch and take the reply's value; a refusal puts it back.

        Every click is sent, one back to the daemon's value included: while an earlier click is
        unanswered, that value is not what the daemon will hold, and the worker's order makes
        the last click win. No loading guard, as on_dpi; _adopting marks the switch set from
        the daemon."""
        if self._adopting:
            return

        def done(reply: dict | None) -> None:
            self.show_dpi_notices(self.dpi_notices if reply is None
                                  else reply.get("dpi_notices"))
        self._call({"command": "dpi_notices", "value": row.get_active()}, done)

    # -- color sync ------------------------------------------------------

    def follow_keyboard(self) -> None:
        if self.sync:
            self.picker.set_color(self._kb_base())

    def _apply_sync_state(self) -> None:
        """The mouse's own color controls work only when it is reachable and
        not following the keyboard; synced, its picker shows the keyboard's."""
        own = self.ok and self.answering and not self.sync
        self.picker.row.set_sensitive(own)
        self.effect_row.set_sensitive(own)
        self.follow_keyboard()

    def set_sync(self, value: bool) -> None:
        """Adopt the daemon's sync state without asking it to change."""
        self.sync = value
        self._adopt(self.sync_row, value)
        self._apply_sync_state()

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
            if wanted and not reply.get("mouse"):
                self._toast("Saved; the mouse follows once it is connected")
        self._call({"command": "mouse_sync", "value": wanted}, done)
