"""The Headset tab's page for the Logitech G432 and the other headsets of its family
(gearbench.g432.MODELS).

The daemon reads the headset and writes it (`g432`, the page's own writes and the cards',
headset_cards). The window itself runs the equalizer (gearbench.g432_eq) of each model with
none of its own, its programs and the files it saves on the worker thread, as the Spatial
sound group's are; the Microphone group ends with the rows the window runs on this computer
(computer_rows). The equalizer is a card of its own (g432_eq_card). Building it runs
nothing.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from typing import TYPE_CHECKING

from gi.repository import Adw, GObject, Gtk

from .. import astro, pipewire_client
from ..battery import kept_at
from ..g432 import (
    G432_MODEL,
    POWER_OFF_CHOICES,
    PRODUCTS,
    SIDETONE_RANGE,
    SIDETONE_UNREAD_RESET,
    HeadsetModel,
    auto_off_words,
    reply_model,
)
from ..system_volume import MIC
from ..wording import capitalized, whole, whole_within
from .computer_rows import ComputerRows
from .device_rows import show_gone, status_rows
from .g432_eq_card import G432EqCard
from .headset_cards import HeadsetEqCard, HeadsetKeysCard, LightsCard, Writes
from .style import (
    ASLEEP,
    HEADSET_ASLEEP,
    HEADSET_TAB,
    NOT_FOUND,
    NOT_READ,
    SIDETONE,
    WAITS_FOR_TEST,
    NoticesRow,
    firmware_row,
    percent,
    plain_row,
    set_dot,
    show_battery,
    show_firmware,
    show_looking,
    show_untested,
    spoken_scale,
    through,
    untested_row,
)
from .worker import SETTLE_MS, Call, DaemonWorker, LoadingGuard, Reply, Settle, unreachable

if TYPE_CHECKING:                       # pragma: no cover
    from .window import Window

#: The page's name in the Headset tab's stack, the G432's name, as `status` gives it among
#: its headsets, which titles the page before `status` names a model, and every name of its
#: family.
PAGE = "g432"
NAME = G432_MODEL.short
NAMES = frozenset(PRODUCTS.values())
SIDETONE_HELP = "You may hear it only while a program has the microphone open"
PUT_BACK = "Put back {}%"
PUT_BACK_HELP = "The level Gearbench first read from the headset"
#: With no level read before any write, the level a reset writes, in PUT_BACK's form.
PUT_BACK_NONE = PUT_BACK.format(SIDETONE_UNREAD_RESET)
PUT_BACK_NONE_HELP = "Gearbench read no level before any write"


def auto_off_label(minutes: int | None) -> str:
    """A time as the Auto shut-off row lists it, as the A50 Gen 4's row words its own:
    `auto_off_words`, capitalized, Not read for none."""
    return capitalized(auto_off_words(minutes))


#: The Auto shut-off row's title: the A50 Gen 4's name for the same job.
AUTO_OFF = astro.SETTING_NAMES["auto_off"]
#: The Auto shut-off row's choices, in POWER_OFF_CHOICES' order.
AUTO_OFF_LABELS = tuple(auto_off_label(minutes) for minutes in POWER_OFF_CHOICES)


class G432Page:
    """The page of the G432 or the headset of its family the daemon names: `call` is the
    window's, for the writes to the headset, whose refusals it shows; reads go to the worker
    straight, so a poll that finds no headset toasts nothing. `run` runs the equalizer's
    programs and those of the rows on this computer (ComputerRows), for `model`, the model
    the last reply named; `parent` is the window, which the page's dialogs open over and
    which the page and its cards use (headset_cards); `kb_base` gives the color a synced
    device takes from the keyboard, as the window holds it (Window.kb_base). HeadsetStack
    puts the groups every headset page shares last in `page` while this page shows."""

    def __init__(self, parent: Window, worker: DaemonWorker, call: Call,
                 toast: Callable[..., object], kb_base: Callable[[], int],
                 run: pipewire_client.Run = subprocess.run) -> None:
        self._parent = parent
        self._worker = worker
        self._call = call
        self._toast = toast
        self._run = run
        self._loading = LoadingGuard()
        #: A read of the G432 is out; the window has closed.
        self._pending = False
        self._closed = False
        #: The level the last reply gave, None while there is none.
        self._level: int | None = None
        #: The model the last reply named, the G432 before one: its equalizer runs here
        #: but on a model with one of its own, and its noise suppression (noise_rows) on
        #: each.
        self.model: HeadsetModel = G432_MODEL
        #: The sidetone's writes, `out` of them not answered yet, any of which may still move
        #: the level.
        self._sidetone_writes = Writes(call, self._eq_answered)
        self._sidetone_send = Settle(self._send_sidetone)
        #: The power-off time's writes, the time the last reply gave, None while unknown, and
        #: the Auto shut-off row's labels.
        self._power_writes = Writes(call, self._eq_answered)
        self._power_off: int | None = None
        self._power_labels = list(AUTO_OFF_LABELS)
        #: The equalizer the window runs on this computer, on a model with none of its own.
        self.eq = G432EqCard(parent, worker, toast, self._loading, run, SETTLE_MS)
        #: What the headset holds itself, each a card of its own (gui/headset_cards.py).
        self.headset_eq = HeadsetEqCard(worker, toast, parent.kept_up, self._loading, call,
                                        self._eq_answered, SETTLE_MS)
        self.lights = LightsCard(parent, self._loading, call, self._eq_answered, toast,
                                 self.refresh, kb_base)
        self.keys = HeadsetKeysCard(parent, worker, self._loading, call, toast)
        #: The Lights card's Saved colors row, which Window.rebuild_saved fills.
        self.saved = self.lights.saved
        self.computer = ComputerRows(worker, toast, parent.kept_up, run, words=(MIC,))
        # The noise suppression of the model shown, the G432 before a reply names one; it
        # stays while a reply names none, since its unit runs without the headset.
        self.computer.noise.show_headset(G432_MODEL.key)
        self.widget = self._build()

    # -- construction -----------------------------------------------------

    def _build(self) -> Gtk.Widget:
        self.page = page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                                   margin_top=18, margin_bottom=18, margin_start=18,
                                   margin_end=18)
        page.append(self._status_group())
        page.append(self._mic_group())
        page.append(self.eq.group)
        # The equalizer in the headset shows where the one above does not, on a model with
        # an equalizer of its own.
        page.append(self.headset_eq.group)
        page.append(self.lights.group)
        page.append(self.keys.group)
        return Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
                                  child=page)

    def _status_group(self) -> Adw.PreferencesGroup:
        """The group titled by the headset's name, its rows in every device group's order
        (the owner's decision of 2026-10-22)."""
        # The tab's noun until a reply names the model.
        group = self.status_group = Adw.PreferencesGroup(title=HEADSET_TAB)
        self.state_row, self.state_dot, self.battery_row, self.battery_icon = status_rows(
            group)
        show_looking(self.state_row, self.state_dot)
        self.battery_row.set_visible(False)
        self.untested_row = untested_row(group, self._toast)
        # After the untested note and before the readings, as on every device page.
        self.notices = NoticesRow(group, self._call)
        self.firmware_row = firmware_row(group)
        self.power_row = Adw.ComboRow(title=AUTO_OFF, visible=False,
                                      model=Gtk.StringList.new(AUTO_OFF_LABELS))
        self.power_row.connect("notify::selected", self.on_auto_off)
        group.add(self.power_row)
        return group

    def _sidetone_rows(self, group: Adw.PreferencesGroup) -> None:
        """The sidetone's slider and its Put back, the Microphone group's first rows, as the
        A50 Gen 4's: one job, one place (the owner's decision of 2026-10-22)."""
        self.sidetone_row = plain_row(Adw.ActionRow, SIDETONE, "", tooltip_text=SIDETONE_HELP)
        self.sidetone_scale = spoken_scale(*SIDETONE_RANGE, SIDETONE)
        self.sidetone_scale.connect("value-changed", self.on_sidetone)
        self.sidetone_row.add_suffix(self.sidetone_scale)
        group.add(self.sidetone_row)
        self.put_back_row = Adw.ButtonRow(title=PUT_BACK_NONE,
                                          start_icon_name="edit-undo-symbolic")
        self.put_back_row.connect("activated", self.on_put_back)
        group.add(self.put_back_row)
        # Grayed until a reply (show): before one the slider sits at 0 and Put back names
        # 0%, whatever the daemon would put back.
        self._sidetone_usable(False)

    def _sidetone_usable(self, usable: bool) -> None:
        """The sidetone's rows sensitive while a headset that lists a sidetone answers."""
        for row in (self.sidetone_row, self.put_back_row):
            row.set_sensitive(usable)

    def _eq_answered(self, reply: dict | None) -> None:
        """A write's reply, the whole `g432` reply; None, a failure the window showed, reads
        the headset again, which puts the rows back."""
        if reply is None:
            self.refresh()
        elif not self._closed:
            self.show(reply)

    def _mic_group(self) -> Adw.PreferencesGroup:
        group = self.mic_group = Adw.PreferencesGroup(title="Microphone")
        self._sidetone_rows(group)
        for row in self.computer.mic_rows:
            group.add(row)
        return group

    # -- the headset and its sidetone ---------------------------------------

    @property
    def busy(self) -> bool:
        """An action the page runs itself, which the window's close waits for: one of the
        equalizer's on the computer, a switch of the noise suppression or Mic input's volume
        set (ComputerRows.busy), or a change of the library of the equalizer in the
        headset."""
        return self.eq.busy or self.computer.busy or self.headset_eq.busy

    def refresh(self) -> None:
        """Ask the daemon for the G432, unless a read is out. It answers from what it last
        read from the headset or wrote to it, sending the headset nothing. The library of
        the equalizer in the headset is read again too."""
        self.headset_eq.refresh()
        if self._pending or self._closed:
            return
        self._pending = True
        self._worker.call({"command": "g432"}, self._on_info)

    def _on_info(self, info: Reply) -> None:
        self._pending = False
        if self._closed:
            return
        if isinstance(info, Exception):
            self._missing(unreachable(info))
        elif not info.get("ok"):
            self._missing(str(info.get("error") or NOT_FOUND))
        else:
            self.show(info)

    def _missing(self, why: str) -> None:
        # A model last shown with a battery keeps the row, with a dash: show_gone leaves
        # whether it shows alone.
        show_gone(self.state_row, self.state_dot, self.battery_row, self.battery_icon,
                  self.notices, why)
        # The untested row stays while the model is known, as on every device page.
        for row in (self.firmware_row, self.power_row):
            row.set_visible(False)
        self.headset_eq.show(None, None)
        self.lights.show(None)
        self.keys.show(None)
        self.computer.gone()
        self._level = None
        self._show_level()
        self._sidetone_usable(False)

    def show(self, info: dict) -> None:
        """The headset's name, the state row, the firmware version, the battery and its
        power-off time where it has them, the slider and Put back from the `g432` reply. The
        slider stays where it is while a send settles or a write is out."""
        model = reply_model(info) or G432_MODEL
        if model is not self.model:
            self._take_model(model)
        self.computer.show_headset(model.key)
        self.status_group.set_title(model.short)
        # A wireless one switched off behind its receiver, as a keyboard or a mouse asleep:
        # amber, what to do, its last battery reading, and the headset's own rows off.
        answering = info.get("answering") is not False
        set_dot(self.state_dot, True if answering else ASLEEP)
        # The route, as the Keyboard tab's row; the node is in the tooltip and the description.
        self.state_row.set_subtitle(through(model.plugged_by) if answering
                                    else HEADSET_ASLEEP)
        self.state_row.show_node(str(info.get("device") or "") or None)
        show_firmware(self.firmware_row, info.get("firmware"))
        self._show_power(info)
        show_untested(self.untested_row, info.get("untested"))
        self.headset_eq.show(info.get("equalizer"), model)
        # Its follow switch stays live while the headset is away, as the Mouse tab's does.
        self.lights.show(info.get("lights"), info.get("flash_refused"), answering)
        self.keys.show(info.get("g_keys"), info.get("g_keys_shift"))
        # A control the headset lacks is hidden, as on every page; the CLI keeps its refusal.
        listed = info.get("sidetone_found") is True
        for row in (self.sidetone_row, self.put_back_row):
            row.set_visible(listed)
        self._sidetone_usable(listed and answering)
        self.headset_eq.set_usable(answering)
        self._level = whole_within(info.get("sidetone"), *SIDETONE_RANGE)
        if (self._level is not None and not self._sidetone_send.pending
                and not self._sidetone_writes.out):
            self._loading.put_value(self.sidetone_scale, self._level)
        self._show_level()
        first = whole_within(info.get("sidetone_first"), *SIDETONE_RANGE)
        known = first is not None
        self.put_back_row.set_title(PUT_BACK.format(first) if known else PUT_BACK_NONE)
        self.put_back_row.set_tooltip_text(PUT_BACK_HELP if known else PUT_BACK_NONE_HELP)

    def take_leader(self, leader: str | None) -> None:
        """The keyboard that leads, by its key, for the Lighting group's follow switch
        (LightsCard.take_leader)."""
        self.lights.take_leader(leader)

    def follow_keyboard(self) -> None:
        """While the lights follow the keyboard, its color on their picker
        (LightsCard.follow_keyboard)."""
        self.lights.follow_keyboard()

    def set_editing(self, text: str, line: str) -> None:
        """Say which layer the G keys card's bindings are written to, and its Layer row's
        `line` (HeadsetKeysCard.set_editing)."""
        self.keys.set_editing(text, line)

    def _take_model(self, model: HeadsetModel) -> None:
        """The page on `model`: its equalizer card on it (G432EqCard.show_model), and the
        last model's gains still settling in the headset's equalizer dropped."""
        self.model = model
        self.eq.show_model(model)
        self.headset_eq.cancel()

    def _show_power(self, info: dict) -> None:
        """The Battery and Auto shut-off rows of a headset that lists a battery, hidden on
        another. Auto shut-off picks the time read, unless a write of it is out, from its
        choices and, where it is none of them or not read, one more entry that says it; it is
        grayed out until a time is read, while the headset does not answer, and where the
        daemon refuses the writes the headset keeps, saying WAITS_FOR_TEST beneath."""
        found = info.get("battery_found") is True
        self.battery_row.set_visible(found)
        self.power_row.set_visible(found)
        self.notices.show({"headset": info.get("model")} if found else None, info)
        if not found:
            return
        battery = info.get("battery")
        # One that does not answer keeps its last reading, with when it was taken.
        show_battery(self.battery_row, self.battery_icon,
                     battery if isinstance(battery, dict) else None, kept_at(info),
                     preset=info.get("battery_notices"))
        answering = info.get("answering") is not False
        minutes = info.get("power_off")
        self._power_off = minutes if whole(minutes) else None
        if not self._power_writes.out:
            self._show_power_off(self._power_off)
        refused = info.get("flash_refused")
        self.power_row.set_subtitle(WAITS_FOR_TEST if refused else "")
        self.power_row.set_sensitive(answering and not refused and self._power_off is not None)

    def _show_power_off(self, minutes: int | None) -> None:
        """Auto shut-off's choices, with an entry for a time that is none of them or not
        read, and the time picked."""
        offered = minutes in POWER_OFF_CHOICES
        labels = list(AUTO_OFF_LABELS)
        if not offered:
            labels.append(auto_off_label(minutes))
        if labels != self._power_labels:
            self._power_labels = labels
            self._loading.put_model(self.power_row, Gtk.StringList.new(labels))
        self._loading.put_selected(self.power_row, POWER_OFF_CHOICES.index(minutes) if offered
                                   else len(POWER_OFF_CHOICES))

    def on_auto_off(self, row: Adw.ComboRow, _pspec: GObject.ParamSpec) -> None:
        """A time picked by hand goes to the headset, unless it is the last reply's and no
        write is out that may yet move the headset away from it."""
        index = row.get_selected()
        if (not self._loading.active and 0 <= index < len(POWER_OFF_CHOICES)
                and (self._power_writes.out or POWER_OFF_CHOICES[index] != self._power_off)):
            self._power_writes.send({"command": "g432_auto_off",
                                     "minutes": POWER_OFF_CHOICES[index]})

    def _show_level(self) -> None:
        """The sidetone row's subtitle: NOT_READ while the daemon holds no level and no write
        is settling or out, since the slider then sits where the page left it, else the
        slider's level."""
        if (self._level is None and not self._sidetone_send.pending
                and not self._sidetone_writes.out):
            words = NOT_READ
        else:
            words = percent(round(self.sidetone_scale.get_value()))
        self.sidetone_row.set_subtitle(words)

    def on_sidetone(self, _scale: Gtk.Scale) -> None:
        # The send first: the subtitle gives the level of a move that settles.
        if not self._loading.active:
            self._sidetone_send.restart(SETTLE_MS)
        self._show_level()

    def _send_sidetone(self) -> None:
        """Write the slider's level, unless it is the last reply's and no write is out that
        may yet move the headset away from it."""
        level = round(self.sidetone_scale.get_value())
        if level == self._level and not self._sidetone_writes.out:
            return
        self._sidetone_writes.send({"command": "g432_sidetone", "level": level})

    def on_put_back(self, _row: Adw.ButtonRow) -> None:
        self._sidetone_send.cancel()
        self._sidetone_writes.send({"command": "g432_sidetone", "reset": True})

    # -- the window's ------------------------------------------------------

    def tick(self) -> None:
        """The window's poll: the headset from the daemon, the noise suppression's state and
        Mic input's volume (ComputerRows.refresh), and on a model whose equalizer the window
        runs the A50's presets and the equalizer's state, but while an action runs, whose end
        reads it."""
        self.refresh()
        self.computer.refresh()
        self.eq.tick()

    def flush(self) -> bool:
        """Send what is still settling now, for the window's close; True if any."""
        sent = [settle.flush() for settle in (self._sidetone_send, self.eq, self.headset_eq,
                                               self.lights, self.computer)]
        return any(sent)

    def close(self) -> None:
        self._closed = True
        for settle in (self._sidetone_send, self.headset_eq):
            settle.cancel()
        self.lights.close()
        self.eq.close()
        self.computer.close()
