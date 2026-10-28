"""The window's banner for the Gearbench focus extension while it is not running: what
game profiles follow without it, and its Turn on.

This part owns the banner, the window's one use of GNOME Shell's extension interface on
the session bus (desktop.SHELL_EXTENSIONS, the interface `gnome-extensions enable` calls)
and its one read of GNOME Shell's settings, for a copy in the home folder the Shell has not
loaded.
Its window builds it, places the banner, hands it each `status` reply and closes it. It asks
the Shell for the extension's state when status's focus source changes to one that is not
the extension, and again each time the Shell says that state changed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from gi.repository import Adw, Gio, GLib

from ..dbusloop import said
from ..desktop import EXTENSION_NAME, EXTENSION_UUID, SHELL_EXTENSIONS, SHELL_EXTENSIONS_PATH
from ..focus import PROCESSES, XWAYLAND
from ..sni import SHELL
from ..wording import NO_REASON

log = logging.getLogger("gearbench")

#: GNOME Shell's ExtensionState as GetExtensionInfo gives it, a double
#: (js/misc/extensionUtils.js). Checked 2026-10-16 on GNOME Shell 50.1: a running extension
#: reports state 1, and a uuid the Shell has not loaded an empty dict.
ACTIVE, ERROR, OUT_OF_DATE = 1, 3, 4
#: How long a call to the Shell may take, in ms.
SHELL_CALL_MS = 5000
#: Where GNOME Shell looks for an extension's folder, under a data folder.
EXTENSIONS_FOLDER = "gnome-shell/extensions"
#: Where installed() finds the extension's folder: a system data folder, which GNOME Shell
#: reads whatever its settings say, or the user's data folder alone.
SYSTEM, HOME = "system", "home"
#: GNOME Shell's settings, and the key without which it reads no extension from the user's
#: data folder (ui/extensionSystem.js, _loadExtensions, in GNOME Shell 50.1).
SHELL_SCHEMA, HOME_EXTENSIONS = "org.gnome.shell", "allow-extension-installation"

#: What game profiles follow without the extension, by status's focus source. A source not
#: here is the extension's own, or none while the daemon is down.
WITHOUT = {XWAYLAND: "Game profiles follow only X11 windows",
           PROCESSES: "Game profiles load for running programs, not the focused window"}
#: The banner's titles, each after WITHOUT's sentence for the source. All but USER_OFF name
#: the extension as the Extensions app lists it, and none says switch: the button says Turn on.
OFF = f"{{without}}: the {EXTENSION_NAME} extension is off"
NOT_LOADED = f"{{without}}: log out and back in to load the {EXTENSION_NAME} extension"
HOME_SKIPPED = (f"{{without}}: GNOME's settings keep the {EXTENSION_NAME} extension in your "
                "home folder from loading")
FAILED = f"{{without}}: the {EXTENSION_NAME} extension failed: {{error}}"
TOO_OLD = (f"{{without}}: the {EXTENSION_NAME} extension does not support this version of "
           "GNOME Shell")
USER_OFF = ("{without}: GNOME's extensions are turned off; turn them on in the "
            "Extensions app")
REFUSED = f"{{without}}: GNOME Shell did not turn the {EXTENSION_NAME} extension on: {{error}}"
#: The banner's button while the extension is off, its accessible name too.
TURN_ON = "Turn on"


class ShellExtensions:
    """GNOME Shell's extension interface for EXTENSION_UUID, over the application's own
    session bus connection. Every call is asynchronous, so the window never waits on the
    Shell, and none starts a Shell."""

    def __init__(self, bus: Gio.DBusConnection, changed: Callable[[], None]) -> None:
        self._bus = bus
        self._subscription = bus.signal_subscribe(
            SHELL, SHELL_EXTENSIONS, "ExtensionStateChanged", SHELL_EXTENSIONS_PATH,
            EXTENSION_UUID, Gio.DBusSignalFlags.NONE, lambda *_signal: changed())

    def _call(self, method: str, finished: Callable[[GLib.Variant | None, str | None], None]
              ) -> None:
        def answered(bus: Gio.DBusConnection, result: Gio.AsyncResult) -> None:
            try:
                reply = bus.call_finish(result)
            except GLib.Error as exc:
                finished(None, said(exc))
                return
            finished(reply, None)

        self._bus.call(SHELL, SHELL_EXTENSIONS_PATH, SHELL_EXTENSIONS, method,
                       GLib.Variant("(s)", (EXTENSION_UUID,)), None,
                       Gio.DBusCallFlags.NO_AUTO_START, SHELL_CALL_MS, None, answered)

    def info(self, done: Callable[[dict | None], None]) -> None:
        """Calls `done` with GetExtensionInfo's dict, or None when no Shell answers."""
        def finished(reply: GLib.Variant | None, error: str | None) -> None:
            if reply is None:
                # Not GNOME Shell, or one too old for the interface: nothing to turn on.
                log.debug("GNOME Shell's extension interface did not answer: %s", error)
            done(None if reply is None else dict(reply.unpack()[0]))

        self._call("GetExtensionInfo", finished)

    def enable(self, done: Callable[[str | None], None]) -> None:
        """Calls `done` with None once the Shell turned the extension on, else why not."""
        def finished(reply: GLib.Variant | None, error: str | None) -> None:
            if reply is None:
                done(error)
            else:
                done(None if reply.unpack()[0] else NO_REASON)

        self._call("EnableExtension", finished)

    def close(self) -> None:
        """Stops hearing the Shell's ExtensionStateChanged."""
        self._bus.signal_unsubscribe(self._subscription)


def shell_for(app: Gio.Application | None
              ) -> Callable[[Callable[[], None]], ShellExtensions | None]:
    """ExtensionBanner's `connect` for the window's application: its session bus
    connection, which it has once registered, or no Shell without one."""
    def connect(changed: Callable[[], None]) -> ShellExtensions | None:
        bus = app.get_dbus_connection() if app is not None else None
        return None if bus is None else ShellExtensions(bus, changed)
    return connect


def installed() -> str | None:
    """Where the extension's folder is among the data folders GNOME Shell looks in: SYSTEM,
    HOME, or None in neither."""
    for there, folders in ((SYSTEM, GLib.get_system_data_dirs()),
                           (HOME, [GLib.get_user_data_dir()])):
        if any((Path(folder) / EXTENSIONS_FOLDER / EXTENSION_UUID).is_dir()
               for folder in folders):
            return there
    return None


def loads_home_extensions() -> bool:
    """Whether GNOME Shell's settings let it load extensions from the user's data folder,
    as it does where they lack the schema or the key."""
    source = Gio.SettingsSchemaSource.get_default()
    schema = None if source is None else source.lookup(SHELL_SCHEMA, True)
    if schema is None or not schema.has_key(HOME_EXTENSIONS):
        return True
    return Gio.Settings.new_full(schema, None, None).get_boolean(HOME_EXTENSIONS)


def banner_text(source: str | None, info: dict | None, refused: str | None,
                there: str | None, loads_home: Callable[[], bool]
                ) -> tuple[str | None, str | None]:
    """The banner's title and button, (None, None) for no banner. `source` is status's
    focus source, `info` the Shell's last answer, `refused` why the last Turn on failed,
    `there` where the extension's files are (installed()), and `loads_home` whether the
    Shell loads extensions from the home folder, asked only when it has not loaded this one."""
    without = WITHOUT.get(source or "")
    if without is None or info is None or info.get("state") == ACTIVE:
        return None, None
    if not info:
        # The Shell reads its extensions at login, those in the home folder only while its
        # settings allow them: installed after this login, or in a folder it skips. That it
        # answers {} for a skipped copy is read from its source, not yet seen in a session.
        if there is None:
            return None, None
        if there == HOME and not loads_home():
            return HOME_SKIPPED.format(without=without), None
        return NOT_LOADED.format(without=without), None
    state = info.get("state")
    if state == ERROR:
        return FAILED.format(without=without, error=info.get("error") or NO_REASON), None
    if state == OUT_OF_DATE:
        return TOO_OLD.format(without=without), None
    if info.get("canChange") is False:
        # GNOME Shell 50.1 clears canChange and enabled while GNOME's extensions are off, and
        # EnableExtension then changes nothing (ui/extensionSystem.js, _updateCanChange; read
        # from its source, not yet seen in a session). It clears canChange too where the
        # machine's settings lock enabled-extensions: the title names the likelier cause.
        return USER_OFF.format(without=without), None
    if refused:
        return REFUSED.format(without=without, error=refused), TURN_ON
    return OFF.format(without=without), TURN_ON


class ExtensionBanner:
    """The banner, up while game profiles cannot follow every window because the extension
    is installed and not running, with Turn on while it is off.

    `connect` makes the Shell, given the call to make when its state changes, or gives
    None where no session bus is, which keeps the banner down.
    """

    def __init__(self, connect: Callable[[Callable[[], None]], ShellExtensions | None]
                 ) -> None:
        self.banner = Adw.Banner(use_markup=False)
        self.banner.connect("button-clicked", self._turn_on)
        self._shell = connect(self._changed)
        #: Status's focus source, None while there is no daemon.
        self._source: str | None = None
        #: The Shell's last GetExtensionInfo, None before one or without a Shell.
        self._info: dict | None = None
        #: Why the last Turn on failed, until the next or a change of the extension's state.
        self._refused: str | None = None

    def take(self, status: dict) -> None:
        """A status reply, {} while the daemon is down."""
        focus = status.get("focus")
        source = focus.get("source") if isinstance(focus, dict) else None
        if source != self._source:
            self._source = source
            self.ask()
        self._show()

    def ask(self) -> None:
        """Ask the Shell for the extension's state, where one is followed without it."""
        if self._shell is not None and self._source in WITHOUT:
            self._shell.info(self._answered)

    def _changed(self) -> None:
        """The Shell's ExtensionStateChanged for the extension: a refusal answered the Turn on
        before it."""
        self._refused = None
        self.ask()

    def _answered(self, info: dict | None) -> None:
        self._info = info
        self._show()

    def _turn_on(self, _banner: Adw.Banner) -> None:
        if self._shell is not None:
            self._refused = None
            self._shell.enable(self._enabled)

    def _enabled(self, error: str | None) -> None:
        self._refused = error
        self.ask()
        self._show()

    def _show(self) -> None:
        title, button = banner_text(self._source, self._info, self._refused, installed(),
                                    loads_home_extensions)
        self.banner.set_title(title or "")
        self.banner.set_button_label(button)
        self.banner.set_revealed(title is not None)

    def close(self) -> None:
        """Stops hearing the Shell, at the window's close."""
        if self._shell is not None:
            self._shell.close()
            self._shell = None
