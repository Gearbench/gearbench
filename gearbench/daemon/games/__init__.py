"""Game profiles: the overlay over the M profiles, loaded by hand, by the focused
window or by a running program. The /proc walk (apps.walk_processes) also finds other
tools that talk to Logitech devices. The commands that edit the game profiles, and an M
profile from one, are a part of their own (commands.py)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Collection
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ...apps import (
    OTHER_TOOLS,
    Process,
    find_other_tools,
    names_match,
    names_of,
    other_tool_problem,
    running_form,
    running_processes,
    walk_processes,
    wanted_forms,
)
from ...config import layer_binding
from ...focus import Focus, held_until
from ...library_files import by_name, file_name, may_hold_it, unread_reason
from ...mouse import bindings_of
from ...profiles import GameProfile, Library, layer_in_force
from ...settings_files import FIX_AND_RELOAD, ConfigError
from ...wording import escaped, quoted
from ...xfocus import XFocus
from ..shared import (
    NO_GAME_PROFILE_NAMED,
    look_due,
    name_text,
    same_profile,
    switch_value,
    with_note,
)
from .commands import GameCommands

if TYPE_CHECKING:
    from .. import Daemon

log = logging.getLogger("gearbench")

#: Seconds between /proc walks (running programs, other tools) and, while focus is
#: followed, between matches of the settled focus; a focus change acts at once.
PROCESS_SCAN_INTERVAL = 5.0
#: Seconds between /proc walks while Gearbench's GNOME Shell extension gives focus, which the
#: scan matches with no walk, so the walk serves the other-tools check alone: the owner's
#: decision of 2026-10-24, "Every 30 s".
SHELL_WALK_INTERVAL = 30.0

#: Why a profile let go of the program that loaded or held it (Games.let_go): its file has
#: gone, a held-off profile took the program in its place, its programs no longer include
#: the program (Games.unbound), focus moved away from it, or it exited.
GONE, SHARED, EDITED, MOVED, EXITED = "gone", "shared", "edited", "moved", "exited"
#: What the journal says as a hold-off ends, by why, with the profile's name and the program.
HOLD_OFF_ENDS = {
    GONE: "game profile %(name)r is no longer in the library; its hold-off ends",
    SHARED: "game profile %(name)r can load again: another held-off game profile that names "
            "%(program)s is first by file name",
    EDITED: "game profile %(name)r can load again: its programs no longer include %(program)s",
    MOVED: "focus moved away from %(program)s; game profile %(name)r can load again",
    EXITED: "%(program)s is no longer running; game profile %(name)r can auto-load again",
}
#: What the journal says as an auto-loaded profile is dropped, by why, with what it gives back.
AUTO_UNLOADS = {
    GONE: "game profile %(name)r auto-unloaded; it is no longer in the library, returning to "
          "%(back)s",
    SHARED: "game profile %(name)r auto-unloaded; a held-off game profile names %(program)s "
            "too, returning to %(back)s",
    EDITED: "game profile %(name)r auto-unloaded; its programs no longer include %(program)s, "
            "returning to %(back)s",
    MOVED: "game profile %(name)r unloaded; focus moved away from %(program)s, returning to "
           "%(back)s",
    EXITED: "game profile %(name)r auto-unloaded; %(program)s exited, returning to %(back)s",
}


def matched_by(profile: GameProfile, running: set[str]) -> str | None:
    """The name in `running` that `profile`'s programs match, for the journal and `status`, or
    None: no placeholder name, as a program may have any name, "?" included."""
    return next((name for want in profile.processes for name in sorted(running)
                 if names_match(want, name)), None)


def matching_profile(running: set[str], profiles: list[GameProfile]) -> GameProfile | None:
    """First profile whose process list matches something running.

    `profiles` is the library as the caller already read it, so the five-second scan does
    not parse every file twice. Compared as apps.names_match does, each running name and
    each program worked out once a call (apps.wanted_forms).
    """
    have = {running_form(r) for r in running}
    for profile in profiles:
        if any(not have.isdisjoint(wanted_forms(want)) for want in profile.processes):
            return profile
    return None


class Games:
    """The loaded game profile and what decides it."""

    def __init__(self, daemon: Daemon) -> None:
        self.d = daemon
        self.library = Library()
        #: The game profile over the M profile, and whether a program loaded it: the scan
        #: drops only those.
        self.overlay: GameProfile | None = None
        self.overlay_auto = False
        #: The profile loaded by hand under an auto-load, or None, given back when its program
        #: goes: an auto-load over another keeps the first one's.
        self._before_auto: str | None = None
        #: Profiles held off by a hand choice made while each was loaded and its program had
        #: focus or ran, with the program holding each now (held_by), the latest last. Each
        #: lasts while one of its programs has focus or runs.
        self._held: dict[str, str] = {}
        #: The program that auto-loaded the overlay, for the journal and `status`.
        self._auto_process: str | None = None
        #: The names the last scan matched profiles by, the focused program's or the running
        #: programs': a hand choice holds off a profile loaded by hand that one of them loads,
        #: and let_go tells a program a held-off profile took from one gone.
        self._seen: set[str] = set()
        #: Keep loaded: off at every start.
        self.keep_loaded = False
        #: Bumped at each change of the loaded game profile, so the mice tell a new load from
        #: the same profile edited or read again (Dpi.retune).
        self.loads = 0
        #: What keep loaded last kept out, so the journal says it once.
        self._kept_out: tuple[str, str] | None = None
        self._last_scan = 0.0
        #: When /proc was last walked, for the scan or the other-tools check (None before the
        #: loop's first pass), and the OTHER_TOOLS it found, with their pids.
        self._last_walk: float | None = None
        self.other_tools: dict[str, list[int]] = {}
        #: The program with focus, from Gearbench's GNOME Shell extension or
        #: Xwayland; started in run(). While `ready`, a profile follows focus
        #: instead of the running programs.
        self.focus = Focus(self.on_focus, lambda changed: XFocus(changed, clock=time.monotonic),
                           clock=time.monotonic)
        #: The focused program and the profile it binds, for `status`.
        self._focus_program: str | None = None
        self._focus_profile: str | None = None
        self.handlers = GameCommands(daemon, self)

    def commands(self) -> dict[str, Callable[[dict], dict]]:
        return {
            "profiles": self._cmd_profiles,
            "load": self._cmd_load,
            "unload": self._cmd_unload,
            "let_load": self._cmd_let_load,
            "keep_loaded": self._cmd_keep_loaded,
            **self.handlers.commands(),
        }

    def status(self) -> dict:
        """The game profile's part of `status`; `held_off_all` lists the latest last."""
        held = [{"profile": name, "process": process} for name, process in self._held.items()]
        return {
            "overlay": self.overlay.name if self.overlay else None,
            "overlay_auto": self.overlay_auto,
            "keep_loaded": self.keep_loaded,
            "auto_process": self._auto_process if self.overlay_auto else None,
            # The latest alone, which the window's held-off row and the CLI's line name.
            "held_off": held[-1] if held else None,
            "held_off_all": held,
            "focus": self.focus_status(),
        }

    def is_held(self, name: str) -> bool:
        return any(same_profile(name, held) for held in self._held)

    def reread(self) -> dict:
        """`reload`: read the loaded profile again from its file; returns what the reply
        adds. A name edited in the file is followed as a rename; a file that will not load, or
        one in a folder that cannot be listed, keeps the profile as it was (`overlay_error`); a
        file gone, or now holding another profile's name that another file is read for
        (`Library.shadowed`), unloads and forgets it (`unloaded`)."""
        if self.overlay is None:
            return {}
        name, path = self.overlay.name, self.overlay.path
        fresh, why = self.read_for(self.overlay)
        if fresh is not None:
            self.follow_edited_name(fresh)
            self.overlay = fresh
            return {}
        if why is not None:
            # A profile the library read has its file (GameProfile.load).
            said = self.overlay.unreadable = unread_reason(cast("Path", path).name, why)
            # Escaped: the error can quote the file's keys as they are, and the journal splits
            # a record at each newline (cli.service.JournalFormatter).
            log.info("game profile %r keeps what it held: its file was not read (%s)", name,
                     escaped(said))
            return {"overlay_error": self.overlay_error()}
        log.info("game profile %r is no longer in the library; unloading it", name)
        self.d.mice.dpi.profile_gone(name)
        self.unload_gone()
        self.d.persist()
        return {"unloaded": name}

    def read_for(self, overlay: GameProfile) -> tuple[GameProfile | None, str | None]:
        """What the library holds for the loaded `overlay`: the profile read for its name, or
        from its file under a name edited there; else why its file was not read, which may
        still hold it; else (None, None), in no file. A directory that cannot be listed may hold
        the file as it was. A shadowed file has no why_unread(): it loaded, and as no profile of
        this name was read, holds another's."""
        # A profile the library read has its file (GameProfile.load), the overlay included.
        path = cast("Path", overlay.path)
        profiles = self.library.all()
        fresh = by_name(profiles, overlay.name)
        if fresh is None:
            fresh = next((p for p in profiles if p.path == path), None)
        if fresh is not None:
            return fresh, None
        return None, self.library.why_unread(path.name)

    def unload_gone(self) -> None:
        """Unload the profile whose file or program has gone: one loaded by hand is no longer
        recorded; an auto-loaded one gives back the hand choice under it (found), which stays
        recorded, loaded or not."""
        restore = self.found(self._before_auto) if self._before_auto else None
        if not self.overlay_auto:
            self.d.config.active_game_profile = None
        self.overlay, self.overlay_auto = restore, False
        self.loads += 1
        self._before_auto = None

    def overlay_error(self) -> str | None:
        """What `reload` and `status` say while the loaded profile's file
        was not read at the last reload or at a scan (scan_library), until a
        reload reads it or `save` writes over it, which fails where the folder could not be
        listed: the write goes through that folder."""
        overlay = self.overlay
        if overlay is None or overlay.unreadable is None:
            return None
        return (f"{overlay.path} could not be read ({overlay.unreadable}); game profile "
                f"{quoted(overlay.name)} keeps what it held; {FIX_AND_RELOAD}")

    def load_remembered(self) -> None:
        """At the start, the game profile loaded by hand before the stop:
        its bindings apply at once, its lighting when the keyboard comes."""
        self.overlay = self.remembered()

    def remembered(self) -> GameProfile | None:
        """The hand choice config.json's `active_game_profile` names, or None. A name edited in
        its file is followed, one whose file will not load or whose folder cannot be listed
        stays recorded, and one gone is forgotten."""
        remembered = self.d.config.active_game_profile
        found = self.found(remembered) if remembered else None
        if not remembered or found is not None:
            return found
        # Any file that will not load may be its file: its name is not known, and a hand-made
        # file's stem need not be the name's slug. A shadowed file is not: found() would have
        # taken the one read in its place.
        unread = [escaped(file) for file, _why in self.library.unread()]
        if unread:
            # A folder that cannot be listed is the one entry, under its own name.
            why = (f"the {unread[0]} folder cannot be listed" if self.library.unlisted_reason
                   else may_hold_it(unread))
            log.info("game profile %r stays remembered: %s; it loads at a reload or a later "
                     "start once its file can be read", remembered, why)
            return None
        # In no file: forgotten, or looked for again at every start.
        log.info("game profile %r is no longer in the library; forgetting it", remembered)
        self.d.config.active_game_profile = None
        self.d.persist()
        return None

    def found(self, chosen: str, profiles: list[GameProfile] | None = None) -> GameProfile | None:
        """The hand choice or held-off profile `chosen`: the profile of that name, else the one
        read from the file saved under it (`doom.json` for Doom) with a name edited there, which
        is followed as a rename, by the loaded copy too where that file is its own
        (follow_edited_name); else None. In `profiles`, a scan's read of the library, so the
        scan works on one read; else in the library as read here."""
        profiles = self.library.all() if profiles is None else profiles
        if (found := by_name(profiles, chosen)) is not None:
            return found
        saved = self.library.directory / file_name(chosen)
        if (found := next((p for p in profiles if p.path == saved), None)) is not None:
            overlay = self.overlay
            if overlay is not None and same_profile(overlay.name, chosen) and overlay.path == saved:
                self.follow_edited_name(found)
            else:
                self.follow_file_name(chosen, found.name)
        return found

    def reload(self) -> dict:
        """`reload`: follow config.json's record (follow_record), then read the loaded profile
        again (reread), whose answer is what the reply adds. In that order: follow_record leaves
        to reread what the loaded profile's file holds now."""
        self.follow_record()
        return self.reread()

    def follow_record(self) -> None:
        """`reload`: follow config.json's `active_game_profile` (remembered), as at a start. A
        profile loaded by hand that it does not name is replaced, or unloaded for a name whose
        file will not load or whose folder cannot be listed; a gone file's DPIs are forgotten,
        as reread does. Under an auto-loaded profile the record is what its program gives
        back."""
        recorded = self.d.config.active_game_profile
        if (not self.overlay_auto and self.overlay is not None and recorded
                and same_profile(recorded, self.overlay.name)):
            return          # reread reads it again, or keeps it while its file cannot be read
        wanted = self.remembered()
        if self.overlay_auto:
            self._before_auto = wanted.name if wanted else None
            return
        if self.overlay is None and wanted is None:
            return
        if self.overlay is not None:
            if wanted is not None and (same_profile(self.overlay.name, wanted.name)
                                       or wanted.path == self.overlay.path):
                return      # the record followed a name edited in the file; reread follows it
            # Gone as reread has it (read_for), a file that holds another profile's name now
            # included: one moved to another file name is still in the library, and reread
            # would keep it loaded. So would a file it cannot read, a dangling link or an
            # unlistable directory.
            if self.read_for(self.overlay) == (None, None):
                if not self.d.config.active_game_profile:
                    return  # reread unloads it, forgets its DPIs and says so
                self.d.mice.dpi.profile_gone(self.overlay.name)
            log.info("game profile %r unloaded: config.json does not name it", self.overlay.name)
        if wanted is not None:
            log.info("game profile %r loaded: config.json names it", wanted.name)
        self.overlay = wanted
        self.loads += 1

    # -- the layer in force --------------------------------------------------

    def active_binding(self, key: str, shifted: bool) -> str | None:
        return layer_binding(self.key_layer(False), self.key_layer(True), key, shifted)

    def key_layer(self, shift: bool) -> dict[str, str | None]:
        """The G key bindings in force, MR's layer with `shift`: the loaded game profile's, or
        the M profile's."""
        if self.overlay is not None:
            return self.overlay.shift if shift else self.overlay.bindings
        return self.d.config.profile.shift if shift else self.d.config.profile.keys

    def mouse_entries(self) -> dict[str, dict[str, str]]:
        """The mouse bindings in force by mouse model: the loaded game profile's, or the M
        profile's."""
        return layer_in_force(self.overlay, self.d.config.profile).mouse

    def mouse_bindings(self, model: str) -> dict[str, str]:
        """What the buttons of the mouse model named `model` (mouse.model_key) are bound to in
        the layer in force (mouse.bindings_of)."""
        return bindings_of(self.mouse_entries(), model)

    def active_mouse_binding(self, button: str, model: str) -> str:
        return self.mouse_bindings(model).get(button, "none")

    def apply_layer(self) -> tuple[str | None, str | None]:
        """Show the layer in force on every keyboard (show_on_each) and the mice; its note and
        detail."""
        shown = self.show_on_each()
        self.d.bindings.stop_orphaned_macro()
        self.d.mice.dpi.follow_tuning()
        return shown

    def show_on_each(self) -> tuple[str | None, str | None]:
        """The layer in force on every keyboard, the leader's first (Keyboards.apply_each): the
        leader's apply_profile's answer and the device's words behind it, both from the
        controller that led as it began, since one torn down in it hands the lead on."""
        leading = self.d.keyboard
        return self.d.keyboards.apply_each(leading), leading.last_apply_detail

    # -- commands ------------------------------------------------------------

    def _cmd_profiles(self, request: dict) -> dict:
        """The game profiles, each with the lighting it shows on the keyboard `keyboard` names,
        else the leader's (Keyboards.shown_in)."""
        keyboard = self.d.keyboards.shown_in(request)
        return {
            "ok": True,
            "overlay": self.overlay.name if self.overlay else None,
            "auto": self.overlay_auto,
            "profiles": [
                {"name": p.name, "mode": shown.mode, "effect": shown.effect,
                 "processes": p.processes, "keys": len(shown.keys)}
                for p in self.library.all()
                for shown in (self.d.per_keyboard.shown(p.lighting, keyboard),)
            ],
        }

    def _cmd_load(self, request: dict) -> dict:
        name = name_text(request["value"], "load")
        profile = self.library.get(name)
        if profile is None:
            return {"ok": False, "error": NO_GAME_PROFILE_NAMED.format(quoted(name))}
        same = self.overlay is not None and same_profile(self.overlay.name, profile.name)
        if not same:
            # Loading the loaded one again is no other choice to hold it off for.
            self.suppress_auto()
            self.loads += 1
        self.overlay = profile
        # The window's Save as loads what it saved: one loaded for its program stays so, and
        # goes when the program does (the owner's decision of 2026-09-28).
        if not (same and self.overlay_auto):
            self.overlay_auto = False
            self._before_auto = None
            self.d.config.active_game_profile = profile.name
        self.d.keyboards.wake_each()
        shown = self.apply_layer()
        self.d.persist()
        log.info("loaded game profile %r over profile %d", profile.name, self.d.config.active)
        return with_note({"ok": True, "overlay": profile.name,
                          "mode": self.d.per_keyboard.shown(profile.lighting).mode}, *shown)

    def suppress_auto(self) -> None:
        """Before a hand choice replaces the overlay, hold it off while its program has focus
        or runs, however it was loaded, or the next scan would put it straight back. Only adds
        a hold-off, beside any other."""
        if self.overlay is None:
            return
        if self.overlay_auto:
            # Set with the flag, by a match (_take_match).
            process = cast("str", self._auto_process)
        elif (seen := matched_by(self.overlay, self._seen)) is not None:
            process = self._focus_program or seen   # under focus, as shown
        else:
            return
        for name in [name for name in self._held if same_profile(name, self.overlay.name)]:
            del self._held[name]        # the latest last, under its latest spelling
        self._held[self.overlay.name] = process
        log.info("game profile %r held off until %s: another choice was made by hand",
                 self.overlay.name, held_until(escaped(process), self.focus.ready))

    def held_by(self, name: str, program: str, present: Collection[str]) -> None:
        """A held-off profile stays held while any of its programs runs or has
        focus; name the one that holds it now once its stored one is gone. `present` holds the
        names of its programs running, or the focused program's."""
        key = next(held for held in self._held if same_profile(held, name))
        was = self._held[key]
        if any(names_match(was, now) for now in present):
            return
        self._held[key] = program
        log.info("game profile %r stays held off until %s, another of its programs", key,
                 held_until(escaped(program), self.focus.ready))

    def _cmd_unload(self, request: dict) -> dict:
        previous = self.overlay.name if self.overlay else None
        if previous is not None:
            log.info("unloaded game profile %r by hand", previous)
        self.d.config.active_game_profile = None
        shown = self.unload_by_hand()
        self.d.persist()
        return with_note({"ok": True, "unloaded": previous}, *shown)

    def unload_by_hand(self) -> tuple[str | None, str | None]:
        """Unload the game profile for a choice made by hand, held off while its program
        has focus or runs (suppress_auto), and show what is under it; apply_layer's answer."""
        self.suppress_auto()
        if self.overlay is not None:
            self.loads += 1
        self.overlay, self.overlay_auto = None, False
        self._before_auto = None
        self.d.keyboards.wake_each()
        return self.apply_layer()

    def _cmd_let_load(self, request: dict) -> dict:
        """End every hold-off and make the next scan due at once, to decide what loads.
        `released` is the latest alone, as `status`'s `held_off` is."""
        released = list(self._held)
        for name in released:
            log.info("game profile %r is no longer held off: let load by hand", name)
        if released:
            self._held.clear()
            self._last_scan = 0.0       # due at once, as at start
        return {"ok": True, "released": released[-1] if released else None,
                "released_all": released}

    def _cmd_keep_loaded(self, request: dict) -> dict:
        """Keep a profile loaded by hand over auto-loads (G HUB's persistent
        profile). Not saved; the next scan acts on it."""
        value = switch_value(request.get("value"), "keep_loaded")
        if value != self.keep_loaded:
            log.info("keep loaded %s", "on" if value else "off")
        self.keep_loaded = value
        self._kept_out = None
        return {"ok": True, "keep_loaded": value}

    def loaded_copy(self, name: str) -> tuple[bool, GameProfile | None]:
        """Whether game profile `name` is the loaded one, and the copy an edit to it goes
        through: the daemon's own when loaded, else the library's, or None. A `name` the loaded
        profile's own file was edited to hold is followed first, as a reload follows it
        (follow_edited_name), and the loaded copy is refused while the library leaves its file
        out (check_read_file): either way the edit would go to a file behind the daemon's copy."""
        if self.overlay is None or not same_profile(self.overlay.name, name):
            self.follow_edited_name(found := self.library.get(name))
            if self.overlay is None or not same_profile(self.overlay.name, name):
                return False, found
        self.check_read_file()
        return True, self.overlay

    def follow_rename(self, was: str, new: str) -> None:
        """Point everything that names game profile `was` at `new`,
        config.json's `active_game_profile` included."""
        self.d.mice.dpi.profile_renamed(was, new)

        def follow[N: str | None](name: N) -> N | str:
            return new if name is not None and same_profile(name, was) else name

        self._before_auto = follow(self._before_auto)
        self._held = {follow(name): process for name, process in self._held.items()}
        self._focus_profile = follow(self._focus_profile)
        if self._kept_out is not None:
            self._kept_out = (follow(self._kept_out[0]), follow(self._kept_out[1]))
        recorded = self.d.config.active_game_profile
        if recorded is not None and same_profile(recorded, was):
            self.d.config.active_game_profile = new
            self.d.persist()

    def follow_file_name(self, was: str, new: str) -> None:
        """Follow game profile `was` under `new`, the name its own file holds now."""
        log.info("game profile %r is named %r in its file now; following it", was, new)
        self.follow_rename(was, new)

    def forget(self, gone: str) -> None:
        """Let go of everything follow_rename points at a new name for game profile `gone`,
        whose file has gone: its DPIs and config.json's `active_game_profile` included."""
        self.d.mice.dpi.profile_gone(gone)
        if self._before_auto and same_profile(self._before_auto, gone):
            self._before_auto = None
        for name in [name for name in self._held if same_profile(name, gone)]:
            del self._held[name]
        if self._focus_profile and same_profile(self._focus_profile, gone):
            self._focus_profile = None
        if self._kept_out is not None and any(same_profile(name, gone) for name in self._kept_out):
            self._kept_out = None
        recorded = self.d.config.active_game_profile
        if recorded and same_profile(recorded, gone):
            self.d.config.active_game_profile = None

    # -- following focus or the running programs -----------------------------

    def deadlines(self) -> list[float]:
        """The next scan and walk, and focus's own: a focus change settling, an X reply
        overdue, an X connect attempt."""
        # A scan waiting for the first X connection is due at once, which
        # would spin the loop; the connection's own deadline wakes it.
        due = [] if self.focus.pending else [self._last_scan + PROCESS_SCAN_INTERVAL]
        if self._last_walk is not None:
            due.append(self._last_walk + self.walk_interval())
        focus_due = self.focus.deadline()
        if focus_due is not None:
            due.append(focus_due)
        return due

    def walk_interval(self) -> float:
        """Seconds between walks of /proc."""
        return SHELL_WALK_INTERVAL if self.focus.shell else PROCESS_SCAN_INTERVAL

    def on_focus(self) -> None:
        """Focus came into force or went out of it, moved to another source,
        or settled on another process. Acted on at once, not at the next scan."""
        self._last_scan = 0.0
        self.scan_processes()

    def focus_status(self) -> dict:
        """`status`'s `focus`: which source a game profile follows, and
        under focus the focused program and the profile it binds."""
        if not self.focus.ready:
            return {"source": self.focus.source, "program": None, "profile": None}
        return {"source": self.focus.source, "program": self._focus_program,
                "profile": self._focus_profile}

    def scan_processes(self) -> None:
        """Match the focused program, or where focus is not followed the running programs,
        against the profiles' programs and act on it (follow_match). While the first X
        connection is pending and the extension is not in force (Focus.pending), wait for it
        instead of loading a profile and taking it back a moment later. One /proc walk every
        walk_interval serves this and the other-tools check."""
        now = time.monotonic()
        walked = self._last_walk is None or look_due(now, self._last_walk + self.walk_interval())
        procs = self.walk(now) if walked else None
        if self.focus.pending or not look_due(now, self._last_scan + PROCESS_SCAN_INTERVAL):
            return
        self._last_scan = now
        profiles = self.scan_library()
        self.follow_read_file(profiles)
        # A held-off profile's name edited in its own file is followed: else the hold-off reads
        # as its profile gone, and the file loads under its new name while its program runs.
        for name in list(self._held):
            self.found(name, profiles)
        bound = [p for p in profiles if p.processes]
        held = [p for p in bound if self.is_held(p.name)]
        # A held-off profile missing while a file will not load may be in it, which says nothing
        # about its program. A shadowed file cannot hide one: the file read in its place has the
        # same name.
        unread = ({name for name in self._held
                   if not any(same_profile(p.name, name) for p in profiles)}
                  if self.library.unread() else set())
        if self.focus.ready:
            shown, names = names_of(self.focus.pid) if self.focus.pid else (None, set())
            # A held-off profile first: its hold-off lasts while any of its programs has
            # focus, whichever other profile binds that program too.
            match = matching_profile(names, held) or matching_profile(names, bound)
            self._focus_program, self._seen = shown, names
            self._focus_profile = match.name if match else None
            if match in held:
                # A match names its program: names_of gives a program gone no names.
                self.held_by(match.name, cast("str", shown), names)
            self.follow_match(match, shown, keep=unread, profiles=profiles)
            return
        self._focus_program = self._focus_profile = None
        if not bound and not self.overlay_auto and not self._held:
            self._seen = set()
            return          # nothing binds a process
        if not walked:
            procs = self.walk(now)
        if procs is None:
            return          # /proc could not be listed, which says nothing exited
        running = self._seen = running_processes(procs)
        # A held-off profile's programs hold it off until none of them runs,
        # whatever else starts, and must not hide another profile's program,
        # nor load another profile that binds one of them too.
        own = {p.name: {name for name in running
                        if any(names_match(want, name) for want in p.processes)} for p in held}
        waiting = {name for name, names in own.items() if names}
        for p in held:
            if own[p.name]:
                # Its own programs alone hold it: one edited out of its list holds it no more.
                # own names what they match, so matched_by finds one.
                self.held_by(p.name, cast("str", matched_by(p, running)), own[p.name])
        others = running.difference(*own.values())
        match = matching_profile(others, [p for p in bound if p not in held])
        self.follow_match(match, matched_by(match, others) if match else None,
                          keep=waiting | unread, profiles=profiles)

    def follow_read_file(self, profiles: list[GameProfile]) -> None:
        """Take the loaded profile from the file the library reads for its name once its own
        file was left out for that one (Library.shadowed), or an edit saved to its own would
        be lost at the next load. A file that will not load is not shadowed; the profile keeps
        what it held."""
        overlay = self.overlay
        # A profile the library read has its file (GameProfile.load), the overlay included.
        if overlay is None or cast("Path", overlay.path).name not in self.library.shadowed:
            return
        fresh = by_name(profiles, overlay.name)
        if fresh is None:
            return
        log.info("game profile %r is read from %s now; following it", fresh.name,
                 escaped(cast("Path", fresh.path).name))
        self.overlay = fresh
        self.loads += 1
        self.apply_layer()

    def check_read_file(self) -> None:
        """Refuse an edit to the loaded profile while another file is read for its name and the
        scan has not followed it yet (follow_read_file), or while its file holds another
        profile's name that another file is read for, which the scan never follows and a reload
        unloads (reread): the edit would go to a file the library leaves out. A `<slug>.json`
        is left out only once edited on disk, which check_unchanged refuses before any write,
        so only another file needs the library read."""
        # Asked only with a profile loaded: each caller has tested the overlay, or was handed it.
        overlay = cast("GameProfile", self.overlay)
        # A profile the library read has its file (GameProfile.load), the overlay included.
        path = cast("Path", overlay.path)
        if path.name == file_name(overlay.name):
            return
        # First: the read sets Library.shadowed, which the next line tests.
        read = self.library.get(overlay.name)
        if path.name not in self.library.shadowed:
            return
        if read is None:
            raise ConfigError(f"game profile {quoted(overlay.name)} is in no file now: "
                              f"{escaped(path.name)} holds another profile's name; "
                              "reload to unload it")
        # A profile the library read has its file (GameProfile.load).
        read_from = cast("Path", read.path).name
        raise ConfigError(f"game profile {quoted(overlay.name)} is read from "
                          f"{escaped(read_from)} now, not {escaped(path.name)};"
                          f" Gearbench follows it within {PROCESS_SCAN_INTERVAL:g} s, or "
                          "reload now")

    def scan_library(self) -> list[GameProfile]:
        """The library for a scan, with the loaded profile's own copy in place of its file when
        that will not load: a bad edit must not read as its program gone, nor hand its program
        to another profile. A shadowed file did load, so it gets no stand-in."""
        profiles = self.library.all()
        if (overlay := self.overlay) is not None:
            # A profile the library read has its file (GameProfile.load).
            file = cast("Path", overlay.path).name
            why = self.library.why_unread(file)
            if why is not None:
                overlay.unreadable = unread_reason(file, why)
                profiles = sorted([*profiles, overlay], key=lambda p: str(p.path))
        return profiles

    def walk(self, now: float) -> list[Process] | None:
        """One walk of /proc, and the other tools in it; None when /proc
        cannot be listed, which leaves what was found as it was."""
        self._last_walk = now
        try:
            procs = walk_processes()
        except OSError:
            return None
        found = find_other_tools(procs)
        for comm, tool in OTHER_TOOLS.items():
            if comm in found and comm not in self.other_tools:
                log.warning("%s", other_tool_problem(comm, found[comm]))
            elif comm in self.other_tools and comm not in found:
                log.info("%s is no longer running", tool.name)
        self.other_tools = found
        return procs

    def other_tool_problems(self) -> list[str]:
        """A sentence for each other tool the last walk found, for `problems`."""
        return [other_tool_problem(comm, pids) for comm, pids in self.other_tools.items()]

    def unbound(self, profile: GameProfile, was: str, program: str | None) -> bool:
        """Whether `profile`, no longer matched and not SHARED (let_go), lets go of `was`, the
        program that loaded or holds it, because its programs were edited: under focus, `was`
        still has focus (`program`); otherwise none of its programs is `was`."""
        if self.focus.ready:
            # `program` is the whole name as shown, `was` may be the running programs' form of
            # it, lowercased and cut as comm is: only the whole name's forms include the cut.
            return program is not None and names_match(program, was)
        return not any(names_match(want, was) for want in profile.processes)

    def end_hold_offs(self, match: GameProfile | None, program: str | None,
                      keep: Collection[str], profiles: list[GameProfile]) -> None:
        """End each hold-off whose profile is not `match` and that `keep` does not name, and
        say why (let_go)."""
        for name in [name for name in self._held
                     if not any(same_profile(name, kept) for kept in keep)
                     and (match is None or not same_profile(match.name, name))]:
            was = self._held.pop(name)
            why = self.let_go(name, was, program, profiles)
            log.info(HOLD_OFF_ENDS[why], {"name": name, "program": escaped(was)})

    def let_go(self, name: str, was: str, program: str | None,
               profiles: list[GameProfile]) -> str:
        """Why profile `name`, no longer matched, lets go of `was`, the program that loaded or
        holds it: GONE when `profiles`, the scan's library, lacks it; SHARED when `was` has
        focus or runs and it still binds `was`, or under focus the focused program, which a
        held-off profile then took in its place; EDITED when its programs no longer include
        `was` (unbound); else MOVED under focus and EXITED without."""
        own = [p for p in profiles if same_profile(p.name, name)]
        if not own:
            return GONE
        if any(names_match(was, seen) for seen in self._seen) and matching_profile(
                self._seen if self.focus.ready else {was}, own):
            return SHARED
        if self.unbound(own[0], was, program):
            return EDITED
        return MOVED if self.focus.ready else EXITED

    def follow_edited_name(self, read: GameProfile | None) -> None:
        """Follow a name edited in the loaded profile's own file, `read` being what the library
        read from it, as a rename. Else a scan would auto-load that file as another profile over
        it and give a hand choice back by a name no file holds now, and a command naming it so
        would edit the file behind the daemon's own copy. Only the name is taken from the file:
        the rest is taken at a reload, as any other edit made on disk."""
        overlay = self.overlay
        if read is not None and overlay is not None and read.path == overlay.path \
                and not same_profile(read.name, overlay.name):
            self.follow_file_name(overlay.name, read.name)
            overlay.name = read.name

    def follow_match(self, match: GameProfile | None, program: str | None,
                     keep: Collection[str], profiles: list[GameProfile]) -> None:
        """Load, keep or drop the overlay for `match`, the profile a program binds, or None;
        `program` names it for the journal, `status` and a later hold-off. A profile loaded by
        hand is replaced and given back after, unless keep loaded is on; only an auto-loaded
        one is dropped here. A hold-off ends when the match is not its profile and `keep` does
        not name it (one of its programs runs, or its file will not load). let_go says why a
        hold-off ends or an auto-loaded profile is dropped, given `profiles`, the scan's library;
        a dropped profile gone from it has its DPIs forgotten.
        A match from the loaded profile's own file under a name edited there is followed as a
        reload follows it (follow_edited_name); `active_game_profile`, the hand choice, is
        written here only so, or for a name edited in the hand choice's own file as a drop
        gives it back (found)."""
        self.follow_edited_name(match)
        self.end_hold_offs(match, program, keep, profiles)

        if match is None or self.overlay_auto or self.overlay is None:
            self._kept_out = None
        if match is not None:
            self._take_match(match, program)
        elif self.overlay_auto:
            self._drop_auto(program, profiles)

    def _take_match(self, match: GameProfile, program: str | None) -> None:
        """Load `match` over the overlay, giving back later a hand choice it replaces, unless it
        is held off or loaded, or keep loaded keeps a hand choice."""
        if self.is_held(match.name):
            return
        if self.overlay is not None and same_profile(self.overlay.name, match.name):
            if self.overlay_auto:
                self._auto_process = program    # another of its programs
            return
        focus = self.focus.ready
        if self.keep_loaded and self.overlay is not None and not self.overlay_auto:
            if self._kept_out != (match.name, self.overlay.name):
                log.info("game profile %r: %s %s; keeping %r loaded (keep loaded is on)",
                         match.name, escaped(program), "has focus" if focus else "is running",
                         self.overlay.name)
                self._kept_out = (match.name, self.overlay.name)
            return
        if not self.overlay_auto:
            self._before_auto = self.overlay.name if self.overlay else None
        self.overlay, self.overlay_auto = match, True
        self.loads += 1
        self._auto_process = program
        if focus:
            log.info("game profile %r loaded: %s has focus", match.name, escaped(program))
        else:
            log.info("game profile %r auto-loaded: %s is running", match.name, escaped(program))
        self.apply_layer()

    def _drop_auto(self, program: str | None, profiles: list[GameProfile]) -> None:
        """Drop the auto-loaded overlay, which no program binds now, give back the hand choice
        under it, and say why (let_go)."""
        # An auto-loaded overlay and its program are there: both are set with the flag
        # (_take_match).
        name, was = cast("GameProfile", self.overlay).name, cast("str", self._auto_process)
        self.unload_gone()
        self._auto_process = None
        back = escaped(self.overlay.name) if self.overlay else "the M profile default"
        why = self.let_go(name, was, program, profiles)
        if why == GONE:
            self.d.mice.dpi.profile_gone(name)      # as reread and delete_profile do
        log.info(AUTO_UNLOADS[why], {"name": name, "program": escaped(was), "back": back})
        self.apply_layer()
