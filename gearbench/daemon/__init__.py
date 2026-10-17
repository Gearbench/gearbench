"""The Gearbench daemon: holds the keyboard's lighting claim, diverts its keys, drives the mice
and the headset's base station, and answers the CLI and the window on a control socket.

This module is the core: the select loop, the control socket, `status`, saving config.json,
the virtual keyboard and mouse, and the shutdown. The controllers it builds do the rest, and
shared.py holds what they share. client.py is the front ends' side of the socket; this module
gives its names again, so that the front ends and the probes in tools/ import the daemon
alone. The claim must be held, or the first keypress hands the lighting back to the firmware;
while it is held, the keyboard's idle and wake notices need an answer and a paint. A bad
shutdown leaves the G and M keys emitting nothing.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import selectors
import signal
import socket
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import cast

from ..apps import candidates
from ..config import (
    PROFILE_IDS,
    Config,
    ConfigError,
    FileUnreadable,
    changed_on_disk,
    config_path,
    escaped,
    file_stamp,
    format_color,
    reason,
    unusable,
)
from ..hidpp import DeviceGone, HidppFailure
from ..lighting import LightingError
from ..mouse import MouseError
from ..uinput import UinputError, VirtualKeyboard, VirtualMouse
from .bindings import Bindings
from .client import (
    BUSY as BUSY,
    DOWN as DOWN,
    NO_ANSWER as NO_ANSWER,
    UCRED as UCRED,
    UNKNOWN as UNKNOWN,
    UNREAD as UNREAD,
    UnreadableReply as UnreadableReply,
    failure as failure,
    peer_uid as peer_uid,
    send_command as send_command,
    socket_path,
    use_invoking_users_runtime_dir as use_invoking_users_runtime_dir,
)
from .games import Games
from .headset import Headset, headset_refusal
from .keyboard import Keyboard
from .mice import Mice
from .session import Session
from .shared import (
    RECONNECT_DELAY,
    HeadsetError,
    checked_layer,
    hidpp_refusal,
    mouse_refusal,
    nested_past,
    not_a_request,
    refusal,
    with_note,
)
from .tray import Tray

log = logging.getLogger("gearbench")

#: Longest select wait: a backstop for a mislaid deadline, not a polling rate.
MAX_POLL_INTERVAL = 1.0

#: A dispatch or connect holding the loop longer logs at info, to match the window's "busy" banner.
SLOW_STEP = 1.0

#: Retry interval for a missing virtual device, so a udev rule applied later is picked up.
VIRTUAL_RETRY = 30.0
UINPUT_HINT = ("/dev/uinput needs Gearbench's udev rule, 70-gearbench-uinput.rules. Check "
               "`getfacl /dev/uinput` and that the uinput module is loaded; tried again "
               f"every {VIRTUAL_RETRY:g} s and at the next bind")

#: What stops working without each virtual device, for its warning and its `status` problem.
VIRTUAL_KEYBOARD_MISSING = "no virtual keyboard, so key bindings and macros type nothing"
VIRTUAL_MOUSE_MISSING = ("no virtual mouse, so macro clicks, wheel tilt and Back or Forward "
                         "bindings fail")

#: handle_command's answer to an OSError, with its reason in words.
CONFIG_NOT_SAVED = "Could not save the configuration ({})."

#: Longest pause between a request's segments (the timeout is per recv): a longer gap is a
#: client that stopped talking, and waiting on it stalls key handling.
CLIENT_TIMEOUT = 0.5

#: Time from the accept to send the whole request: CLIENT_TIMEOUT alone lets a client that
#: sends a byte every 0.4 s hold the loop indefinitely.
REQUEST_DEADLINE = 2.0

#: The most a request may hold, since the loop buffers a whole request before it parses it.
#: The largest a front end sends, a macro of 64 text steps at their longest, is about 35 KB.
MAX_REQUEST = 1 << 20

#: How deep a request may nest lists and objects, itself counted: json.loads reads nesting
#: deeper than repr() of a value in a refusal can walk. A front end sends at most 5, a game
#: profile with its own DPI stages in import_profile.
MAX_DEPTH = 32

#: A bad request's words for what json.loads raises on a body, by the error's own type, where
#: the error's text would not do: the stack's, and int()'s past 4300 digits, whose text asks
#: for sys.set_int_max_str_digits(), which no client can call. JSONDecodeError gives its own
#: text, with its position; serve_one words a UnicodeDecodeError by its codec.
UNREADABLE = {RecursionError: "nested too deeply to read",
              ValueError: "holds a number too long to read"}

#: The reply to a request that met a bug: a handler raised what nothing expects, or its
#: reply could not be written as JSON. log.exception puts the traceback in the daemon's log.
INTERNAL_ERROR = "internal error, a bug in Gearbench: the daemon's log has the traceback"

#: The next step for a daemon refused beside another, which is the service or one in a
#: terminal: either may be the one refused.
STOP_THE_OTHER = "Stop it first with `systemctl --user stop gearbench`, or Ctrl+C in its terminal."

def loggable(request: dict) -> dict:
    """The request as the debug log shows it: macro steps show only their types, since a step's
    value can be a password and the journal keeps it, and "zones" is left out for its length."""
    shown = {k: v for k, v in request.items() if k != "zones"}
    if "steps" in shown:
        steps = shown["steps"]
        shown["steps"] = ([s.get("type") if isinstance(s, dict) else "?" for s in steps]
                          if isinstance(steps, list) else "?")
    return shown


def load_config() -> tuple[Config, str | None]:
    """The configuration and None, or the defaults and why the file was not usable.

    A bad field must not stop the daemon, or the G keys emit nothing. The reason is returned so
    that nothing is saved over the file (Daemon.save_config).
    """
    try:
        return Config.load(), None
    except (ConfigError, OSError) as exc:
        why = unusable(exc)
        # Escaped: the error quotes the file's keys and a link's target as they are, and the
        # journal splits a record at each newline (cli.service.JournalFormatter).
        log.error("%s is not usable (%s); starting with the defaults instead. "
                  "Nothing will be written over it: fix the file and run "
                  "`gearbench-ctl reload`.", escaped(config_path()), escaped(why))
        return Config(), why


class LayerChanged(Exception):
    """A change made on a layer no longer in force (Daemon.check_layer)."""

    kind = "layer_changed"


#: check_layer's refusal, with the layer now in force.
LAYER_NOW = "The keyboard is now on {}, so the change was not made."


class BadRequest(Exception):
    """A request serve_one refuses before any handler sees it."""


# -- the layer each change edits, for check_layer ----------------------------
#: What shows: the loaded game profile, or the M profile with none loaded.
SHOWN = "shown"
#: The M profile in force, whatever is loaded.
M_PROFILE = "M profile"
#: Both: promote copies the loaded game profile into the M profile.
BOTH = "both"
#: Which game profile is loaded, if any: the stages and the rate edit its own, or else onboard
#: profile 1's, which no M profile has.
GAME_PROFILE = "game profile"
LAYER_EDITS = {
    **dict.fromkeys(("paint", "color", "effect", "preview", "bind", "mouse_bind",
                     "mouse_bind_defaults", "save_profile"), SHOWN),
    "rename": M_PROFILE,
    "promote": BOTH,
    "mouse_dpi_stages": GAME_PROFILE,
    "mouse_rate": GAME_PROFILE,
}


class Daemon:
    def __init__(self) -> None:
        #: config.json's file_stamp at its last read or write here: a save refuses once it
        #: differs. Taken before the read, so an edit between the two is not taken as read.
        self._config_stamp = file_stamp(config_path())
        #: config.json's file_stamp when the last reload could not use it, and why. While the
        #: file keeps that stamp, no reload takes it, so a refusal gives that error instead.
        #: A save drops it.
        self._reload_refused: tuple[tuple[int, int, int, int] | None, str] | None = None
        #: config_error: why config.json could not be used at startup, or at a reload refused
        #: since, or None. While it is set, nothing is written over the file until a reload
        #: reads it.
        self.config, self.config_error = load_config()
        self._unsaved_warned = False
        self.keyboard = Keyboard(self)
        self.kbd: VirtualKeyboard | None = None
        #: Back and forward for the mouse buttons, the wheel tilt and a macro's clicks.
        #: Optional: without it each is logged at debug and dropped.
        self.ptr: VirtualMouse | None = None
        self.server: socket.socket | None = None
        self.mice = Mice(self)
        self.headset = Headset(self)
        self.bindings = Bindings(self)
        #: When to retry a missing virtual device, and the failures already warned of (a repeat
        #: logs at debug).
        self._virtual_retry = 0.0
        self._uinput_said: set[str] = set()
        #: Why the last accept on the control socket failed: a cause is warned of once, until
        #: another or an accept that works.
        self._accept_said: str | None = None
        #: The last slow connect line logged at info, per device ("keyboard", "headset" or a
        #: mouse's node).
        self._slow_said: dict[str, str] = {}
        self.selector = selectors.DefaultSelector()
        self.running = False
        self.session = Session(self)
        self.games = Games(self)
        self.tray = Tray(self)
        self._sock_path = socket_path()
        #: The single-instance lock's fd while listen() holds it, and (st_dev, st_ino) of the
        #: socket file this daemon bound: shutdown removes that file only.
        self._lock: int | None = None
        self._sock_id: tuple[int, int] | None = None
        #: Built once, so the command names can be read without running a command.
        self.commands = self.command_table()

    # -- slow steps ------------------------------------------------------

    def note_slow(self, started: float, what: str, why: str = "",
                  repeats: str | None = None) -> None:
        """Log at info that `what`, begun at `started`, held the loop past SLOW_STEP, with `why`.

        `repeats` names a connect attempt's device, which the loop retries on a timer: the same
        line again for it logs at debug, since a keyboard off behind its receiver is slow every
        time.
        """
        took = time.monotonic() - started
        if took <= SLOW_STEP:
            return
        said = f"{what}: {why}"
        level = logging.INFO
        if repeats is not None:
            if self._slow_said.get(repeats) == said:
                level = logging.DEBUG
            self._slow_said[repeats] = said
        log.log(level, "%s took %.1f s%s", what, took, f": {why}" if why else "")

    def forget_slow(self, repeats: str) -> None:
        """Log the next slow step of `repeats` at info again: its device's attempt changed."""
        self._slow_said.pop(repeats, None)

    # -- saving config.json ----------------------------------------------

    def persist(self) -> None:
        """Save config.json, logging a failure rather than raising.

        For a selection or a remembered value, which stands either way; a refusal is warned of
        once until a save succeeds. A setting uses save_setting, so one not saved is not made.
        """
        try:
            self.save_config()
        except ConfigError as exc:
            if not self._unsaved_warned:
                log.warning("%s", escaped(exc))     # it can quote load_config's error
                self._unsaved_warned = True
        except OSError as exc:
            log.warning("could not save %s: %s", escaped(config_path()), reason(exc))

    def save_setting(self, **changes: object) -> None:
        """Set config fields and save config.json. A refused or failed save puts them back and
        raises: a setting made and not saved would be lost at the next start."""
        before = {key: getattr(self.config, key) for key in changes}
        for key, value in changes.items():
            setattr(self.config, key, value)
        try:
            self.save_config()
        except BaseException:
            for key, value in before.items():
                setattr(self.config, key, value)
            raise

    def check_config_writable(self) -> None:
        """Raise ConfigError if config.json was unreadable at load or has changed on disk since
        its last read or write here.

        A command that edits config.json calls this before it changes anything, or puts back
        what it changed when the save is refused, as save_setting does, so a refusal leaves the
        live state as it was.
        """
        if self.config_error is not None:
            raise FileUnreadable(
                f"{config_path()} could not be read ({self.config_error}); nothing is "
                f"saved over it. Fix it and run `gearbench-ctl reload`."
            )
        if (changed := self._disk_refusal()) is not None:
            raise changed

    def _disk_refusal(self) -> ConfigError | None:
        """The refusal for config.json changed on disk since its last read or write here, or
        None. While the file is as a refused reload found it, that is the reload's error, as
        another reload would refuse it the same way."""
        stamp = file_stamp(config_path())
        if stamp == self._config_stamp:
            return None
        refused = self._reload_refused
        if refused is not None and refused[0] == stamp:
            return FileUnreadable(
                f"{config_path()} could not be read at the last reload ({refused[1]}); "
                f"nothing is saved over it. Fix it and run `gearbench-ctl reload`; "
                f"`gearbench-ctl save` writes the daemon's copy over it.")
        return changed_on_disk(config_path())

    def check_layer(self, command: str, request: dict) -> None:
        """Raise LayerChanged if `request` names a layer other than the one in force for what
        `command` edits (LAYER_EDITS).

        The window names the layer its change was built from in `layer`: {"profile": the M
        profile, "overlay": the game profile's name or null}. An M key, a program's profile,
        the tray's menu or the CLI can switch layers before the change arrives, and saving it
        there would overwrite that layer's own. A request without `layer`, the CLI's or the
        tray's, is for the layer in force. Runs before the handler, so before anything is saved
        or written. A `layer` of another shape is a TypeError (checked_layer).
        """
        edits, named = LAYER_EDITS.get(command), request.get("layer")
        if edits is None or named is None:
            return
        named = checked_layer(named)
        if edits == GAME_PROFILE and not self.mice.dpi.tunes_game_profile(request):
            if named.get("overlay") is None:
                return      # onboard profile 1's stages and rate, whatever is loaded
            # Built on a game profile's stages and rate, out of force since host mode went off:
            # made, it would go to profile 1 in flash.
            raise LayerChanged(f"The mouse now takes onboard profile 1's stages and rate, not "
                               f"{named['overlay']}'s own, so the change was not made.")
        overlay = self.games.overlay.name if self.games.overlay is not None else None
        if edits == SHOWN:
            edits = GAME_PROFILE if overlay is not None else BOTH
        if ((edits == GAME_PROFILE or named.get("profile") == self.config.active)
                and (edits == M_PROFILE or named.get("overlay") == overlay)):
            return
        where = f"M{self.config.active} · {self.config.profile.name}"
        if overlay is not None:
            where += f" with {overlay} loaded"
        raise LayerChanged(LAYER_NOW.format(where))

    def check_layer_writable(self) -> None:
        """check_config_writable for the M profile, or for the loaded game profile: its file
        is unchanged and the library still reads it (check_read_file)."""
        if self.games.overlay is not None:
            self.games.overlay.check_unchanged()
            self.games.check_read_file()
        else:
            self.check_config_writable()

    def save_layer_file(self) -> None:
        """Save the layer in force to the file check_layer_writable checks: the loaded game
        profile's, or config.json."""
        if self.games.overlay is not None:
            self.games.overlay.save()
        else:
            self.save_config()

    def save_config(self, over_an_edit: bool = False) -> Path:
        """Write config.json and answer its path, or raise ConfigError if it could not be read
        at load or has changed on disk since, or if its folder is or sits behind a link to
        nothing (Config.save).

        The first two are not written over until `reload` reads it: the first holds the user's
        profiles behind one bad field, the second is a hand edit. `over_an_edit` is
        `gearbench-ctl save`, which writes over a hand edit, one the last reload could not read
        included, but never over a file unreadable at load, while the daemon holds only the
        defaults.
        """
        if not over_an_edit or self.config_error is not None:
            self.check_config_writable()
        path = self.config.save()
        # The refused reload's record is of a version this save replaced, and its stamp can
        # come back: None, a file that cannot be statted, is every such file's.
        self._config_stamp, self._reload_refused = file_stamp(path), None
        # Saved, so the next refusal is news and is warned of again.
        self._unsaved_warned = False
        return path

    # -- control socket --------------------------------------------------

    def listen(self) -> None:
        """Open the control socket for this user only, or exit if another daemon is running,
        a file sits where the socket's folder should be, or the folder is another account's or
        other accounts' to write in.

        The lock beside the socket is held until shutdown: without it, a second daemon's probe
        could land between the first's bind and listen, be refused, and remove the new socket
        as stale.
        """
        folder = self._sock_path.parent
        try:
            # 0700, as logind makes /run/user/<uid>, and so is each missing folder above it:
            # under umask 002 the group would write there, and could put its own folder here.
            for parent in reversed(folder.parents):
                with contextlib.suppress(FileExistsError):
                    parent.mkdir(mode=0o700)
            folder.mkdir(mode=0o700, exist_ok=True)
        except FileExistsError:
            # mkdir's own words, "File exists", name neither which path nor what to do.
            raise SystemExit(
                f"Gearbench daemon cannot listen: a file is at {folder}, where its runtime "
                f"folder should be. Remove the file, or set XDG_RUNTIME_DIR to a folder, then "
                f"start the daemon again.") from None
        # Another account that may write here, through the folder's group or as any account,
        # could bind the socket first, swap its name for a link between bind and chmod, or
        # plant the lock as a link to a file this process may create.
        info = os.stat(folder)
        if info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise SystemExit(
                f"Gearbench daemon cannot listen in {folder}: another account owns it or other "
                f"accounts may write in it. Set XDG_RUNTIME_DIR to a folder only you may write "
                f"in, as /run/user/{os.geteuid()} is, then start the daemon again.")
        lock = self._sock_path.with_name("gearbench.lock")
        fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            if isinstance(exc, BlockingIOError):
                raise SystemExit(f"another Gearbench daemon is already running: it holds "
                                 f"{lock}. {STOP_THE_OTHER}") from None
            raise
        self._lock = fd
        if self._sock_path.exists():
            # A killed daemon's, or a live one's whose lock file was removed: only a refused
            # connect removes it.
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                try:
                    probe.connect(str(self._sock_path))
                except OSError:
                    self._sock_path.unlink()
                else:
                    raise SystemExit(f"another Gearbench daemon is already listening on "
                                     f"{self._sock_path}. {STOP_THE_OTHER}")
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(self._sock_path))
        info = os.stat(self._sock_path)
        self._sock_id = (info.st_dev, info.st_ino)
        # Before listen: until then a connect is refused, whatever the umask's mode lets
        # others do.
        os.chmod(self._sock_path, 0o600)
        self.server.listen(4)
        self.server.setblocking(False)
        self.selector.register(self.server, selectors.EVENT_READ, "control")

    def command_table(self) -> dict[str, Callable[[dict], dict]]:
        """Each control-socket command name and its handler, the core's and each controller's."""
        return {
            "status": self._cmd_status,
            "save": self._cmd_save,
            "reload": self._cmd_reload,
            "quit": self._cmd_quit,
            "apps": self._cmd_apps,
            **self.keyboard.commands(),
            **self.mice.commands(),
            **self.headset.commands(),
            **self.bindings.commands(),
            **self.games.commands(),
            **self.session.commands(),
            **self.tray.commands(),
        }

    def handle_command(self, request: object) -> dict:
        """Run one request from the control socket and return its reply.

        Every reply carries "ok"; a false one carries "error", a sentence for a person, and may
        carry "detail", the failure's own text (refusal), or "kind": a file problem the window
        shows in a banner (ConfigError.kind) or a layer no longer in force (LayerChanged), which
        it reads again. Nothing a client sends may raise out of here, a body that is not a JSON
        object included; serve_one has already refused one nested past MAX_DEPTH.
        """
        if not isinstance(request, dict) or not isinstance(request.get("command", ""), str):
            return {"ok": False, "error": not_a_request(request)}
        command = request.get("command", "").lower()
        if command not in ("status", "mouse", "headset"):
            log.debug("command %s", loggable(request))
        handler = self.commands.get(command)
        if handler is None:
            return {"ok": False, "error": f"unknown command {command!r}"}
        try:
            self.check_layer(command, request)
            return handler(request)
        except (ConfigError, LayerChanged) as exc:
            # Before ValueError, ConfigError's base: "bad request" would blame the client for a
            # file on disk or a refused binding.
            reply = {"ok": False, "error": str(exc)}
            if exc.kind is not None:
                reply["kind"] = exc.kind
            return reply
        except HeadsetError as exc:
            # Every failure of the base's, as headset_call raises it: none reaches the
            # keyboard's branches below.
            return headset_refusal(exc)
        except KeyError as exc:
            # str() of a KeyError is the bare quoted key: a field the request left out.
            return {"ok": False, "error": f"bad request: no {exc}"}
        except (ValueError, TypeError) as exc:
            return {"ok": False, "error": f"bad request: {exc}"}
        except DeviceGone as exc:
            # The keyboard's: a mouse's failure comes back as MouseError from mouse_call,
            # which has torn that mouse down already.
            self.keyboard.teardown_device()
            return hidpp_refusal("keyboard", exc)
        except HidppFailure as exc:
            return hidpp_refusal("keyboard", exc)
        except MouseError as exc:
            return mouse_refusal(exc)
        except (LightingError, UinputError) as exc:
            return {"ok": False, "error": str(exc)}
        except OSError as exc:
            return refusal(CONFIG_NOT_SAVED.format(reason(exc)), exc)

    # -- the core's commands ---------------------------------------------

    def _cmd_status(self, request: dict) -> dict:
        return {
            "ok": True,
            # Before problems(): its reads can tear the keyboard down, which problems() must see.
            **self.keyboard.handlers.status(),
            "problems": self.problems(),
            "profile": self.config.active,
            "profile_name": self.config.profile.name,
            "profile_names": {str(i): self.config.profiles[i].name for i in PROFILE_IDS},
            **self.games.status(),
            # The M profile's color, whatever is loaded: its background in per-key mode, its
            # Static color in static and under an animation.
            "color": format_color(self.config.profile.lighting.mode_color()),
            "effect": self.config.profile.lighting.effect,
            # Static or per-key, which `effect` static leaves open.
            "mode": self.config.profile.lighting.mode,
            "shifted": self.bindings.shifted,
            "brightness": self.config.brightness,
            # Which macro runs and the seconds before a played one starts: a repeating macro
            # shows nowhere else.
            **self.bindings.macro_state(),
            "mouse": self.mice.current is not None,
            # A base station attached; its detail is the `headset` request.
            "headset": self.headset.a50 is not None,
            "mouse_sync_color": self.config.mouse_sync_color,
            # Whether any attached mouse has lighting for the sync to color.
            "mouse_lights": self.mice.color.mouse_with_lights(),
            "dpi_notices": self.config.dpi_notices,
            "mouse_host_mode": self.config.mouse_host_mode,
            "lights_off_when_locked": self.config.lights_off_when_locked,
            "idle_dim_after": self.config.idle_dim_after,
            "idle_off_after": self.config.idle_off_after,
            "never_dim": self.config.never_dim,
            "session_locked": self.session.session_locked,
            "screen_blanked": self.session.screen_blanked,
            "desktop": self.session.desktop.status(),
            "tray": self.tray.status(),
            # The lowest low-battery threshold the keyboard was warned of since it was last on
            # external power or read at BATTERY_REARM or more, or None.
            "battery_warned": self.session.battery_warned,
            "virtual_keyboard": self.kbd is not None,
            "virtual_mouse": self.ptr is not None,
            # A restarted daemon comes up under the same profile and overlay names, so the
            # window reads the files again when this changes.
            "pid": os.getpid(),
            # The bindings in force and the macro files, each in eight hex digits as the
            # keyboard's `lighting` is: a front end rereads the files when one changes.
            "bindings": self.bindings.bindings_digest(),
            "macros": self.bindings.macros_digest(),
        }

    def problems(self) -> list[dict]:
        """What is wrong, as {"code", "message"}, for `status` to carry. Only sysfs and the
        files are read: nothing is sent to a device."""
        found: list[dict] = []

        def add(code: str, message: str) -> None:
            found.append({"code": code, "message": message})

        for code, message in (*self.keyboard.handlers.problems(), *self.mice.problems(),
                              *self.headset.problems()):
            add(code, message)
        for message in self.games.other_tool_problems():
            add("other_hid_tool", message)
        try:
            self.check_config_writable()
        except ConfigError as refused:
            # A FileChanged or a FileUnreadable, each of which sets its kind.
            add(cast("str", refused.kind), str(refused))
        overlay = self.games.overlay
        # In check_unchanged's order, which refuses the loaded profile's next edit. Whatever
        # the file's stamp: a folder that cannot be listed leaves the file as it was.
        if (unreadable := self.games.overlay_error()) is not None:
            add("config_unreadable", unreadable)
        elif overlay is not None and overlay.changed():
            # GameProfile.changed is False for a profile with no file.
            add("config_changed", str(changed_on_disk(cast("Path", overlay.path))))
        for path in self.bindings.macros.changed():
            add("config_changed", str(changed_on_disk(path, save=False)))
        # What stops working goes before the colon, where the tray cuts a long line.
        if self.kbd is None:
            add("no_virtual_keyboard",
                f"{VIRTUAL_KEYBOARD_MISSING}: /dev/uinput could not be opened")
        if self.ptr is None:
            add("no_virtual_mouse", f"{VIRTUAL_MOUSE_MISSING}: /dev/uinput could not be opened")
        missing = self.session.desktop_missing() if self.config.lights_off_when_locked else None
        if missing:
            add("desktop_unavailable", missing)
        return found

    def _cmd_save(self, request: dict) -> dict:
        """Write the daemon's copy over hand edits of config.json and the loaded game profile."""
        reply = {"ok": True, "path": str(self.save_config(over_an_edit=True))}
        if (overlay := self.games.overlay) is not None:
            try:
                reply["profile_path"] = str(overlay.save(over_an_edit=True))
            except OSError as exc:
                # config.json is written by now, which handle_command's own answer leaves out.
                return refusal(f"Saved {reply['path']}, but not game profile {overlay.name!r} "
                               f"({reason(exc)}).", exc)
        return reply

    def _cmd_reload(self, request: dict) -> dict:
        """Discard live changes and go back to what is on disk, on both layers.

        Config.load() runs before anything is assigned, so a broken config.json leaves the
        daemon as it was, and its error is kept for a refusal (_disk_refusal) until the file
        changes; after a start that could not use the file, it replaces the start's
        (config_error). The game profile loaded by hand follows active_game_profile as at a
        start (Games.follow_record), and the loaded one is read again (Games.reread); the macro
        files are read again, so one edited by hand can be saved over. A switch edited by hand
        is acted on as its command would. Device state that is not configuration, the mouse's
        onboard profile and the lighting claim, is left alone.
        """
        stamp = file_stamp(config_path())
        try:
            config = Config.load()
        except (ConfigError, OSError) as exc:
            self._reload_refused = (stamp, unusable(exc))
            if self.config_error is not None:
                self.config_error = self._reload_refused[1]
            if isinstance(exc, ConfigError):
                raise
            # A read failure, not the failed save that handle_command's OSError answer says.
            return {"ok": False,
                    "error": f"could not read {config_path()}: {self._reload_refused[1]}"}
        before = self.keyboard.idle_settings()
        old = self.config
        # The color a synced mouse shows now, which sync turned off leaves it on.
        synced = self.keyboard.shown_color()
        self.config = config
        self.config_error, self._unsaved_warned = None, False
        self._config_stamp, self._reload_refused = stamp, None
        # Switches edited by hand; paint=False because apply_profile below paints.
        times = self.keyboard.idle_changed(before, paint=False)
        if config.lights_off_when_locked != old.lights_off_when_locked:
            self.session.lock_lights_changed(config.lights_off_when_locked, paint=False)
        # First, so reread reads what the record leaves loaded.
        self.games.follow_record()
        overlay_news = self.games.reread()
        self.bindings.macros.reread()
        self.keyboard.wake_for_change()
        failed = self.keyboard.apply_profile()
        # After the keyboard's paint, so a slow mouse never delays it.
        if config.mouse_sync_color != old.mouse_sync_color:
            self.mice.color.sync_changed(config.mouse_sync_color, synced, paint=False)
        if config.mouse_host_mode != old.mouse_host_mode:
            self.mice.host_mode_changed(config.mouse_host_mode)
        self.bindings.stop_orphaned_macro()
        self.mice.dpi.follow_tuning()
        if config.tray != old.tray:
            self.tray.apply()
        overlay = self.games.overlay.name if self.games.overlay else None
        log.info("reloaded from disk: profile %d, game profile %s", self.config.active,
                 repr(overlay) if overlay is not None else "none")
        return with_note({"ok": True, "profile": self.config.active,
                          "profile_name": self.config.profile.name,
                          "overlay": overlay, **overlay_news},
                         # New times the keyboard did not take, as `idle` says them, after a
                         # paint it did not show and before the lights-down note.
                         self.keyboard.last_apply_error or times or failed,
                         self.keyboard.last_apply_detail)

    def _cmd_quit(self, request: dict) -> dict:
        self.running = False
        return {"ok": True}

    def _cmd_apps(self, request: dict) -> dict:
        """The programs running now that a game profile can be bound to."""
        return {"ok": True, "apps": [{"process": c, "label": label} for c, label in candidates()]}

    # -- answering a client ----------------------------------------------

    def serve_one(self) -> str | None:
        """Accept one client and answer it, whatever it sent; return the command's name, when
        it is text, else None, for dispatch's slow-step log.

        Nothing a client does may end the process: the unit's start limit is five starts in a
        minute, so a crash anyone with the socket can trigger leaves the unit dead and the
        keyboard without its claim. The request is read to end of file, which the client sends
        by shutting its write side, since a paint's zone map can arrive in several segments.
        """
        assert self.server is not None  # noqa: S101 -- for the type checker: listen() bound it
        try:
            conn, _ = self.server.accept()
        except BlockingIOError:
            return None     # a wake with no client waiting
        except OSError as exc:
            # EMFILE leaves the client queued and the socket ready, so the next select comes
            # back at once: said once a cause.
            if self._accept_said != (said := reason(exc)):
                log.warning("cannot take a client on the control socket: %s", said)
            self._accept_said = said
            return None
        self._accept_said = None
        deadline = time.monotonic() + REQUEST_DEADLINE
        command = None
        with conn:
            try:
                received = bytearray()
                while (chunk := read_before(conn, deadline)):
                    received += chunk
                    if len(received) > MAX_REQUEST:
                        raise BadRequest(f"longer than the {MAX_REQUEST} bytes a request may be")
                try:
                    request = json.loads(received or b"{}")
                except UnicodeDecodeError as exc:
                    # The codec json.loads chose by the first bytes, as config.read_json says it.
                    raise BadRequest(f"not {exc.encoding.upper()} text ({exc.reason})") from None
                except (RecursionError, ValueError) as exc:
                    raise BadRequest(UNREADABLE.get(type(exc), str(exc))) from None
                # The same words: which of the two refuses a deep body depends on the stack.
                if nested_past(request, MAX_DEPTH):
                    raise BadRequest(UNREADABLE[RecursionError])
                if isinstance(request, dict) and isinstance(request.get("command"), str):
                    command = request["command"]
                body = json.dumps(self.handle_command(request))
            # Not ValueError or RecursionError: out of handle_command, or json.dumps of a reply
            # it cannot write, either is a bug to log.
            except (OSError, BadRequest) as exc:
                body = json.dumps({"ok": False, "error": f"bad request: {reason(exc)}"})
            except Exception:                       # see the docstring
                log.exception("command failed")
                body = json.dumps({"ok": False, "error": INTERNAL_ERROR})
            with contextlib.suppress(OSError):
                # Not what the deadline left the last read.
                conn.settimeout(CLIENT_TIMEOUT)
                conn.sendall(body.encode() + b"\n")
        self.tray.changed()
        return command

    # -- main loop -------------------------------------------------------

    def run(self) -> int:
        """Run until a stop signal or `quit`, then shut down; 1 if the loop failed, else 0."""
        self.running = True
        self.stop_on_signals()
        self.start_up()
        # So a stop signal wakes select at once: select, retried after a handler (PEP 475),
        # would wait out its timeout. The interpreter writes the signal's number here.
        wake_read, wake_write = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
        self.selector.register(wake_read, selectors.EVENT_READ, "signal")
        previous_wakeup = signal.set_wakeup_fd(wake_write)
        next_attempt = 0.0
        try:
            while self.running:
                next_attempt = self.one_pass(next_attempt)
        except Exception as exc:
            # Before the shutdown, whose "shutting down" line would read as a clean stop, and
            # at critical, for `journalctl -p warning`. The failure exit makes systemd restart
            # the daemon, up to the unit's start limit.
            log.critical("the loop failed and the daemon is stopping: %r", exc, exc_info=True)
            return 1
        finally:
            signal.set_wakeup_fd(previous_wakeup)
            self.shutdown()
            os.close(wake_read)
            os.close(wake_write)
        return 0

    def stop_on_signals(self) -> None:
        """Have SIGINT and SIGTERM, and SIGHUP where it was not ignored, end the loop."""
        stops = [signal.SIGINT, signal.SIGTERM]
        # SIGHUP is a foreground daemon's terminal closing, whose default action skips the
        # shutdown and leaves the keys diverted. Left ignored where it was, as nohup does,
        # to outlive the terminal. Not yet checked with a real terminal.
        if signal.getsignal(signal.SIGHUP) != signal.SIG_IGN:
            stops.append(signal.SIGHUP)
        for sig in stops:
            signal.signal(sig, lambda *_: setattr(self, "running", False))

    def start_up(self) -> None:
        """What `run` does once before its loop. A socket that cannot be listened on ends the
        process, nothing shut down."""
        try:
            self.listen()
        except OSError as exc:
            # Only around listen: a later OSError from the loop is no socket problem.
            raise SystemExit(f"Gearbench daemon cannot listen on {self._sock_path}: "
                             f"{reason(exc)}") from exc
        have = self.make_virtual_devices()
        log.info("virtual %s ready; control socket at %s",
                 " and ".join(have) or "devices: none", self._sock_path)
        # Once listening: a second daemon, refused there, writes nothing.
        self.games.load_remembered()
        # Stamped from here, so a macro file edited by hand is not saved over.
        self.bindings.macros.all()
        self.session.start_desktop()
        self.tray.start()
        self.games.focus.start(self.selector, "focus")

    def one_pass(self, next_attempt: float) -> float:
        """One pass of the loop: the work that is due, the wait in select, then the reports
        queued meanwhile. `next_attempt` is when the keyboard's connect is next tried, and
        the answer is that time for the next pass."""
        if self.keyboard.keyboard_moved_to_cable() or self.keyboard.look_for_cable_again():
            next_attempt = 0.0
        if (self.keyboard.dev is None and time.monotonic() >= next_attempt
                and not self.keyboard.connect()):
            next_attempt = time.monotonic() + RECONNECT_DELAY
            log.debug("keyboard not present; retrying")
        self.bindings.runner.tick(time.monotonic())
        self.games.focus.tick()
        self.games.scan_processes()
        self.mice.maybe_connect_mouse()
        self.mice.tend_mice()
        self.mice.buttons.tend_tilts()
        self.mice.buttons.open_mouse_inputs()
        self.headset.maybe_connect()
        self.headset.tend()
        self.retry_virtual_devices()
        self.session.tend_desktop()
        self.keyboard.tend_idle()
        self.keyboard.write_due_leds()
        wake_at = time.monotonic() + self.next_wakeup(next_attempt)
        # Motion alone, up to 1000 reports a second on a mouse's receiver node, changes
        # nothing the steps above or next_wakeup read: select again with the time left.
        while (self.dispatch(self.selector.select(
                timeout=max(0.0, wake_at - time.monotonic())))
               and self.running and time.monotonic() < wake_at):
            pass
        # Reports queued while a request awaited its reply do not wake select: read them.
        for attached in list(self.mice.attached):
            self.mice.mouse_events(attached)
        self.mice.pump_waiting_queued()
        # The keyboard's too: otherwise a G key pressed during a status read or a
        # command waits for the next report, and a quick tap comes out with its release.
        if self.keyboard.dev is not None and self.keyboard.dev.pending:
            self.keyboard.pump()
        return next_attempt

    def make_virtual_devices(self) -> list[str]:
        """Create whichever of the virtual keyboard and mouse is missing, and answer which
        were made now.

        Both are optional: the lighting and the M/MR LEDs need no uinput, whose ACL comes from
        70-gearbench-uinput.rules. A failure is warned of once, `status` carries it, and
        retry_virtual_devices tries again.
        """
        made = []
        for attr, name, make, lost in (
                ("kbd", "keyboard", VirtualKeyboard, VIRTUAL_KEYBOARD_MISSING),
                ("ptr", "mouse", VirtualMouse, VIRTUAL_MOUSE_MISSING)):
            if getattr(self, attr) is not None:
                continue
            try:
                setattr(self, attr, make())
            except UinputError as exc:
                said = f"{name}: {reason(exc)}"
                if said in self._uinput_said:
                    log.debug("still no virtual %s: %s", name, exc)
                else:
                    self._uinput_said.add(said)
                    log.warning("%s: %s. %s", lost, reason(exc), UINPUT_HINT)
            else:
                made.append(name)
        self._virtual_retry = time.monotonic() + VIRTUAL_RETRY
        return made

    def retry_virtual_devices(self, now: bool = False) -> None:
        """Retry a missing virtual device once VIRTUAL_RETRY has passed, or `now`, for a bind."""
        if self.kbd is not None and self.ptr is not None:
            return
        if not now and time.monotonic() < self._virtual_retry:
            return
        if made := self.make_virtual_devices():
            log.info("virtual %s ready", " and ".join(made))

    def dispatch(self, batch: list[tuple[selectors.SelectorKey, int]]) -> bool:
        """Hand each ready fd of one select batch to its handler, timed (note_slow), and answer
        whether the batch was motion alone: mouse HID++ nodes only, each pump_mouse True.

        A handler can close an fd later in the batch, so one no longer registered is skipped.
        """
        motion = bool(batch)
        for key, mask in batch:
            kind = key.data[0] if isinstance(key.data, tuple) else key.data
            if kind != "mouse-hid":
                motion = False
            try:
                self.selector.get_key(key.fileobj)
            except KeyError:
                continue
            started = time.monotonic()
            if kind == "control":
                command = self.serve_one()
                what = f"command {command!r}" if command else "a client's request"
            elif kind == "hid":
                self.keyboard.pump()
                what = "the keyboard's reports"
            elif kind == "signal":
                # The stop signal's byte; its handler has already cleared `running`.
                os.read(key.fd, 512)
                continue
            elif kind == "mouse-input":
                self.mice.buttons.pump_mouse_input(key.data[1], key.data[2])
                what = f"{key.data[2].name}'s buttons"
            elif kind == "mouse-hid":
                if not self.mice.pump_mouse(key.data[1]):
                    motion = False
                what = f"{key.data[1].name}'s reports"
            elif kind == "mouse-wait":
                self.mice.pump_waiting(key.data[1])
                # An attach from here logs its own time (connect_mouse).
                continue
            elif kind == "headset":
                self.headset.pump()
                what = "the headset's reports"
            elif kind == "desktop":
                # The session buses' replies and signals; Session's on_* callbacks run in here.
                self.session.desktop.dispatch()
                what = "the desktop's messages"
            elif kind == "focus":
                # X server replies and focus events; a settled change comes back through on_focus.
                self.games.focus.dispatch(mask)
                what = "the X server's messages"
            else:
                continue
            self.note_slow(started, what)
        return motion

    def next_wakeup(self, next_attempt: float) -> float:
        """Seconds to wait in select: the nearest deadline of any part of the daemon, at most
        MAX_POLL_INTERVAL."""
        # A queued report does not wake select: handle it first.
        if (self.keyboard.dev is not None and self.keyboard.dev.pending) or any(m.dev.pending for m in self.mice.attached):
            return 0.0
        now = time.monotonic()
        deadlines = [*self.keyboard.deadlines(), *self.mice.deadlines(),
                     *self.headset.deadlines(), *self.bindings.deadlines(),
                     *self.games.deadlines(), *self.session.deadlines()]
        if self.keyboard.dev is None:
            deadlines.append(next_attempt)
        if (self.kbd is None or self.ptr is None) and self._virtual_retry:
            # Once run() has tried the virtual devices the first time.
            deadlines.append(self._virtual_retry)
        return max(0.0, min(min(deadlines) - now, MAX_POLL_INTERVAL))

    # -- shutdown --------------------------------------------------------

    def system_is_stopping(self) -> bool:
        """Whether the machine is powering off rather than just stopping the daemon.

        systemd answers "stopping" for the whole of a shutdown or reboot. Any other answer, or
        none, counts as not shutting down: a keyboard wrongly lit is better than one wrongly dark.
        GEARBENCH_ASSUME_SHUTDOWN=1 forces it, the only way to run the path without a reboot.
        """
        if os.environ.get("GEARBENCH_ASSUME_SHUTDOWN") == "1":
            return True
        try:
            done = subprocess.run(
                ["systemctl", "is-system-running"],  # noqa: S607 -- the PATH is the user's own
                check=False, capture_output=True, text=True, timeout=2.0)
        except (OSError, subprocess.SubprocessError) as exc:
            # A timeout's own text quotes the command as a Python list.
            why = (f"took over {exc.timeout:g} s" if isinstance(exc, subprocess.TimeoutExpired)
                   else f"did not run ({reason(exc)})")
            log.info("could not ask systemd whether the system is stopping: systemctl %s", why)
            return False
        state, error = done.stdout.strip(), done.stderr.strip()
        # Any other answer, or an error, may be a power-off taken for a plain stop.
        expected = state in ("running", "stopping") and not error
        log.log(logging.DEBUG if expected else logging.INFO,
                "systemctl is-system-running answered %r, exit %d%s", state, done.returncode,
                f": {error}" if error else "")
        return state == "stopping"

    def shutdown(self) -> None:
        """Let go of everything, and say in one line what was let go."""
        going_down = self.system_is_stopping()
        log.info("shutting down%s", ", machine is powering off" if going_down else "")
        self.bindings.runner.cancel()
        keep_dark = going_down and self.config.mouse_sync_color
        if keep_dark:
            # Before the keyboard's darken, which writes a synced mouse black.
            self.mice.ready_for_dark()
        said = [self.keyboard.teardown_device(lights_off=going_down, stopping=True)]
        # Before the virtual keyboard closes: it types the release of a key a mouse button
        # holds.
        self.bindings.let_go_of_keys(mouse=True)
        if self.kbd is not None:
            self.kbd.close()
            self.kbd = None
        if self.ptr is not None:
            self.ptr.close()
            self.ptr = None
        if self.server is not None:
            with contextlib.suppress(KeyError, ValueError):
                self.selector.unregister(self.server)
            self.server.close()
            self.server = None
        said.append(self.mice.teardown_all(keep_dark))
        said.append(self.headset.teardown())
        # After the devices: closing lets a suspend waiting on the inhibitor go, once the
        # keyboard has been handed back or darkened.
        self.session.stop_desktop()
        self.games.focus.close()
        if self._sock_id is not None:
            # A file another daemon bound there since is its socket, not this one's.
            with contextlib.suppress(OSError):
                info = os.stat(self._sock_path)
                if (info.st_dev, info.st_ino) == self._sock_id:
                    self._sock_path.unlink()
            self._sock_id = None
        self.selector.close()
        if self._lock is not None:
            os.close(self._lock)
            self._lock = None
        log.info("stopped; %s", "; ".join(said))


def read_before(conn: socket.socket, deadline: float) -> bytes:
    """One read of a client's request, waiting no longer than CLIENT_TIMEOUT or what is left
    before `deadline`, a time.monotonic(); BadRequest after CLIENT_TIMEOUT with nothing sent
    or once that has passed."""
    left = deadline - time.monotonic()
    if left > 0:
        conn.settimeout(min(CLIENT_TIMEOUT, left))
        try:
            return conn.recv(65536)
        except TimeoutError:
            if time.monotonic() < deadline:
                raise BadRequest(f"nothing sent for {CLIENT_TIMEOUT:g} s") from None
    raise BadRequest(f"not sent whole within the {REQUEST_DEADLINE:g} s a request may take")
