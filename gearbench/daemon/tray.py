"""The tray icon: its switch, what it shows and what its menu does. tray.py
is the model and sni.TrayExport exports it, through desktop.Desktop."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from ..config import PROFILE_IDS, profile_label
from ..desktop import APP_NAME
from ..keyboard import followed, m_profiles_in_menu
from ..settings_files import file_stamp
from ..tray import NOT_AVAILABLE_ICON, Model, Snapshot, Stages, TrayMenu, build
from ..wording import NO_REASON, capitalized, escaped, home_shortened
from .shared import look_due, switch_value

if TYPE_CHECKING:
    from . import Daemon

log = logging.getLogger("gearbench")

#: Seconds between comparing the tray's model with what it shows (Tray.tend); a command
#: compares at once (Tray.changed).
TRAY_REFRESH = 2.0

#: The title of a refused menu choice's notice: the choice's label first, as the DPI notice's
#: title puts its mouse's name first.
REFUSED_TITLE = "{}: refused"
REFUSED_ICON = NOT_AVAILABLE_ICON


class Tray:
    """The tray icon, shown while `config.tray` is on."""

    def __init__(self, daemon: Daemon) -> None:
        self.d = daemon
        self.menu = TrayMenu(source=self.model, act=self.act,
                             open_app=daemon.session.desktop.open_app, opening=self.opening)
        #: Set by start(), from Daemon.run, so a Daemon built and not run shows nothing.
        self._started = False
        #: When tend next compares the menu's model with what the tray shows, and whether a
        #: pass came before then, whose change the compare then owes (deadlines).
        self._refresh_at = 0.0
        self._owed = False
        #: The game profiles' names, and the directory's files and times they
        #: were read at: read again only when a file changes.
        self._profiles: tuple[str, ...] = ()
        self._profiles_read: object = None

    def commands(self) -> dict[str, Callable[[dict], dict]]:
        return {"tray": self._cmd_tray}

    def start(self) -> None:
        self._started = True
        self.apply()

    def apply(self) -> None:
        if not self._started:
            return
        if self.d.config.tray:
            self.d.session.desktop.show_tray(self.menu)
        else:
            self.d.session.desktop.hide_tray()

    def changed(self) -> None:
        """A command ran: show what it changed now, not at the next refresh."""
        self.d.session.desktop.refresh_tray()

    def tend(self) -> None:
        """Once a pass: while the icon is wanted, compare the menu's model with what the tray
        shows once TRAY_REFRESH has passed since the last time, or in the pass that called
        due_now, after its looks. What the tray shows changes in the loop's passes, so a pass
        before the compare is due leaves it owed, a deadline (deadlines): a change shows within
        TRAY_REFRESH, and an idle loop, whose passes come further apart, wakes for no compare
        of its own."""
        now = time.monotonic()
        if not (self._started and self.d.config.tray):
            # A compare owed from before the switch went off would be a deadline already
            # passed, so the loop would never wait.
            self._owed = False
            return
        if not look_due(now, self._refresh_at):
            self._owed = True
            return
        self._refresh_at, self._owed = now + TRAY_REFRESH, False
        self.d.session.desktop.refresh_tray()

    def deadlines(self) -> list[float]:
        """The compare a pass left owed (tend)."""
        return [self._refresh_at] if self._owed else []

    def due_now(self) -> None:
        """Compare at this pass's end (tend): a device node came or went (Plugs), which the
        pass's looks act on."""
        self._refresh_at = 0.0

    def status(self) -> dict:
        """`status`'s `tray`: the switch, whether the icon shows, and why not."""
        shown, why = False, None
        if self.d.config.tray:
            shown, why = self.d.session.desktop.tray_state()
        return {"on": self.d.config.tray, "shown": shown, "why": why}

    # -- what it shows -------------------------------------------------------

    def model(self) -> Model:
        return build(self.snapshot(), APP_NAME)

    def snapshot(self) -> Snapshot:
        """What the daemon knows, from its own state, each device's battery line as its
        controller gives it. Only a keyboard's battery may be asked for, as `status` asks
        (Keyboard.tray_battery); each mouse's and each headset's is its last reading
        (MouseBatteries.tray_batteries, tray_battery)."""
        mice, config = self.d.mice, self.d.config
        # Each keyboard driven, the leader's first.
        batteries = [battery for keyboard in self.d.keyboards.driven()
                     if (battery := keyboard.tray_battery()) is not None]
        batteries += mice.battery.tray_batteries()
        batteries += [battery for headset in self.d.headsets
                      if (battery := headset.tray_battery()) is not None]
        current = mice.current
        stages = None
        if current is not None and not current.silent and "dpi" in current.mouse.capabilities:
            in_force, _shift = mice.dpi.stages_in_force(current)
            if in_force:
                stages = Stages(f"{current.product:04x}", tuple(in_force),
                                mice.dpi.known_dpi(current))
        # Any keyboard driven, the leader or not: the M profile is every keyboard's. Asked
        # after the battery reads, which may drop one.
        pickable = any(m_profiles_in_menu(keyboard.keyboard_able)
                       for keyboard in self.d.keyboards.driven())
        lit = any(keyboard.lighting is not None for keyboard in self.d.keyboards.driven())
        # As the Mouse tab's DPI row: a mouse whose DPI the daemon sets, asleep or not.
        steps = any("dpi" in attached.mouse.capabilities for attached in mice.attached)
        overlay = self.d.games.overlay
        problems = self.d.problems()
        return Snapshot(
            batteries=tuple(batteries),
            problems=tuple(p["message"] for p in problems),
            problem_codes=tuple(p["code"] for p in problems),
            stages=stages,
            profiles=self.profile_names(),
            loaded=overlay.name if overlay is not None else None,
            m_profiles=(tuple((number, profile_label(number, config.profiles[number].name))
                              for number in PROFILE_IDS) if pickable else ()),
            active=config.active,
            keep_loaded=self.d.games.keep_loaded,
            lock_lights=config.lights_off_when_locked if lit else None,
            dpi_notices=config.dpi_notices if steps else None,
            mouse_sync=config.mouse_sync_color if mice.color.mouse_with_lights() else None,
            # As the window's Follow switch: an attached headset that lists lights
            # (HeadsetLighting.listed).
            headset_sync=(config.headset_sync_color if self.d.g432.lights.listed() else None),
            leader=followed(self.d.per_keyboard.leader()),
        )

    def profile_names(self) -> tuple[str, ...]:
        """The game profiles, parsed again only when a file in their directory
        changed: the model is built every TRAY_REFRESH."""
        library = self.d.games.library
        # file_stamp holds the change time, which a chmod or chown moves: they decide whether
        # the library can read a file. A file it cannot stat, gone or a link to nothing, is None.
        try:
            files = tuple((p.name, file_stamp(p)) for p in library.files())
        except OSError:
            # No folder yet, or one it cannot list, which the library's own read says once.
            files = ()
        if files != self._profiles_read:
            self._profiles = tuple(library.names())
            self._profiles_read = files
        return self._profiles

    # -- what its menu does --------------------------------------------------

    def act(self, request: dict, label: str) -> None:
        """A chosen item's request, run as the control socket runs it. A refusal is posted
        as a notice titled with the item's `label`, since the menu has nowhere to show it."""
        reply = self.run(request)
        if reply is not None and not reply.get("ok"):
            error = str(reply.get("error", NO_REASON))
            detail = reply.get("detail")
            # A refusal can quote a file's error, which quotes the file. The notice keeps the
            # sentence alone, the home folder written ~ as the window and the menu write it;
            # the journal, the menu's only record, has the device's words and the whole path.
            log.warning("the tray's %s was refused: %s%s", request["command"], escaped(error),
                        f" ({escaped(detail)})" if detail else "")
            self.d.session.desktop.notify(REFUSED_TITLE.format(label),
                                          capitalized(home_shortened(error)),
                                          REFUSED_ICON, transient=True)

    def opening(self) -> None:
        """The menu opens: the selected mouse read as the Mouse tab reads it (`mouse`), so its
        DPI and battery are current; a mouse not answering or with a fresh reading is not asked."""
        if self.d.mice.current is not None:
            self.run({"command": "mouse"})

    def run(self, request: dict) -> dict | None:
        """One request through the socket's handler; None when it failed, logged."""
        try:
            return self.d.handle_command(request)
        except Exception:                   # as serve_one: nothing a click does ends the loop
            log.exception("the tray's %s failed", request.get("command"))
            return None

    def _cmd_tray(self, request: dict) -> dict:
        """The tray icon on or off, saved."""
        value = switch_value(request.get("value"), "tray")
        self.d.save_setting(tray=value)
        self.apply()
        return {"ok": True, "tray": self.status()}
