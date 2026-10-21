"""The tray icon: its switch, what it shows and what its menu does. tray.py
is the model and sni.TrayExport exports it, through desktop.Desktop."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from ..config import PROFILE_IDS, file_stamp, profile_label
from ..desktop import APP_NAME
from ..keyboard import CABLED_IDS as KEYBOARD_CABLED, m_profiles_in_menu, name as keyboard_name
from ..tray import Battery, Model, Snapshot, Stages, TrayMenu, build
from ..wording import escaped
from .shared import on_or_off, switch_refusal

if TYPE_CHECKING:
    from . import Daemon

log = logging.getLogger("gearbench")

#: A refused menu choice's notice: the window's heading for one, and the refusal as the body.
REFUSED = "That did not work"
REFUSED_ICON = "dialog-warning-symbolic"


class Tray:
    """The tray icon, shown while `config.tray` is on."""

    def __init__(self, daemon: Daemon) -> None:
        self.d = daemon
        self.menu = TrayMenu(source=self.model, act=self.act, open_app=self.open_app,
                             opening=self.opening)
        #: Set by start(), from Daemon.run, so a Daemon built and not run shows nothing.
        self._started = False
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
        """What the daemon knows, from its own state. Only the keyboard's battery may be asked
        for, as `status` asks (Keyboard.keyboard_battery); the headset's is the loop's last
        reading."""
        kb, mice, config = self.d.keyboard, self.d.mice, self.d.config
        batteries = []
        reading = kb.keyboard_battery()
        # Asked after the read: one that went unanswered marks it, and one that found the
        # keyboard gone, or on its cable, dropped it.
        if kb.dev is not None and "battery" in kb.keyboard_able:
            batteries.append(Battery.of(
                "keyboard", keyboard_name(kb.keyboard_product), reading,
                answering=kb.keyboard_answering(),
                cabled=kb.keyboard_product in KEYBOARD_CABLED))
        batteries += [Battery.of(f"{m.product:04x}", m.name, m.battery, answering=not m.silent)
                      for m in mice.attached if "battery" in m.mouse.capabilities]
        headset = self.d.headset
        # Only while linked: the base's 7C goes stale while the headset is off.
        if headset.linked and headset.battery is not None:
            batteries.append(Battery.of("headset", headset.name, headset.battery,
                                        answering=not headset.silent))
        current = mice.current
        stages = None
        if current is not None and not current.silent and "dpi" in current.mouse.capabilities:
            in_force, _shift = mice.dpi.stages_in_force(current)
            if in_force:
                stages = Stages(f"{current.product:04x}", tuple(in_force),
                                mice.dpi.known_dpi(current))
        pickable = kb.dev is not None and m_profiles_in_menu(kb.keyboard_able)
        overlay = self.d.games.overlay
        return Snapshot(
            batteries=tuple(batteries),
            problems=tuple(p["message"] for p in self.d.problems()),
            stages=stages,
            profiles=self.profile_names(),
            loaded=overlay.name if overlay is not None else None,
            m_profiles=(tuple((number, profile_label(number, config.profiles[number].name))
                              for number in PROFILE_IDS) if pickable else ()),
            active=config.active,
            keep_loaded=self.d.games.keep_loaded,
            lock_lights=config.lights_off_when_locked,
            dpi_notices=config.dpi_notices,
            mouse_sync=config.mouse_sync_color if mice.color.mouse_with_lights() else None,
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

    def act(self, request: dict) -> None:
        """A chosen item's request, run as the control socket runs it. A refusal is posted
        as a notice, since the menu has nowhere to show it."""
        reply = self.run(request)
        if reply is not None and not reply.get("ok"):
            error = str(reply.get("error", "refused"))
            detail = reply.get("detail")
            # A refusal can quote a file's error, which quotes the file. The notice keeps the
            # sentence alone; the journal, the menu's only record, has the device's words too.
            log.warning("the tray's %s was refused: %s%s", request["command"], escaped(error),
                        f" ({escaped(detail)})" if detail else "")
            self.d.session.desktop.notify(REFUSED, error, REFUSED_ICON, transient=True)

    def open_app(self) -> None:
        """The menu's Open item: the window, from the desktop."""
        self.d.session.desktop.open_app()

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
        value = on_or_off(request.get("value"))
        if value is None:
            return {"ok": False, "error": switch_refusal("tray", request.get("value"))}
        self.d.save_setting(tray=value)
        self.apply()
        return {"ok": True, "tray": self.status()}
