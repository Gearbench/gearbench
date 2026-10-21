"""Game profiles: the overlay over the M profiles, loaded by hand, by the focused
window or by a running program. The /proc walk (apps.walk_processes) also finds other
tools that talk to Logitech devices."""

from __future__ import annotations

import copy
import logging
import time
from collections.abc import Callable, Collection
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

from .. import profiles
from ..apps import (
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
from ..config import (
    BINDABLE_KEYS,
    PROFILE_IDS,
    ConfigError,
    Lighting,
    expect,
    merge_under,
    not_bindable,
    unread_reason,
)
from ..focus import Focus, held_until
from ..keyboard import keep_entry, model_ids as keyboard_ids
from ..library_files import file_name, not_deleted
from ..lighting import ANIMATIONS, SPEEDS, WAVE_DIRECTIONS
from ..mouse import bindings_of, model_ids as mouse_ids
from ..profiles import STATIC, GameProfile, Library, layer_in_force
from ..wording import escaped, reason
from ..xfocus import XFocus
from .shared import (
    NOT_AN_M_PROFILE,
    name_text,
    on_or_off,
    refusal,
    same_profile,
    switch_refusal,
    whole_number,
    with_note,
)

if TYPE_CHECKING:
    from . import Daemon

log = logging.getLogger("gearbench")

#: Seconds between /proc walks (running programs, other tools) and, while focus is
#: followed, between matches of the settled focus; a focus change acts at once.
PROCESS_SCAN_INTERVAL = 5.0

#: Why a profile let go of the program that loaded or held it (Games.let_go): its file has
#: gone, its programs no longer include the program (Games.unbound), focus moved away from
#: it, or it exited.
GONE, EDITED, MOVED, EXITED = "gone", "edited", "moved", "exited"
#: What the journal says as a hold-off ends, by why, with the profile's name and the program.
HOLD_OFF_ENDS = {
    GONE: "game profile %(name)r is no longer in the library; its hold-off ends",
    EDITED: "game profile %(name)r can load again: its programs no longer include %(program)s",
    MOVED: "focus moved away from %(program)s; game profile %(name)r can load again",
    EXITED: "%(program)s is no longer running; game profile %(name)r can auto-load again",
}
#: What the journal says as an auto-loaded profile is dropped, by why, with what it gives back.
AUTO_UNLOADS = {
    GONE: "game profile %(name)r auto-unloaded; it is no longer in the library, returning to "
          "%(back)s",
    EDITED: "game profile %(name)r auto-unloaded; its programs no longer include %(program)s, "
            "returning to %(back)s",
    MOVED: "game profile %(name)r unloaded; focus moved away from %(program)s, returning to "
           "%(back)s",
    EXITED: "game profile %(name)r auto-unloaded; %(program)s exited, returning to %(back)s",
}


def matched_by(profile: GameProfile, running: set[str]) -> str:
    """The name in `running` that `profile`'s programs match, for the journal and `status`."""
    return next((name for want in profile.processes for name in sorted(running)
                 if names_match(want, name)), "?")


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


def _keys_named(value: object) -> tuple[str, ...]:
    """The keys an `import_m_profile` request's `keys` names, each one of BINDABLE_KEYS, or
    every one of them for a null or a request without it, as a front end older than the field
    sends."""
    if value is None:
        return BINDABLE_KEYS
    expect(value, list, "import_m_profile.keys", "a list of key names")
    keys = tuple(cast(list, value))     # expect raised for anything else
    for key in keys:
        if key not in BINDABLE_KEYS:
            raise ConfigError(f"import_m_profile.keys: {not_bindable(key)}")
    return keys


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
        #: The profile the last scan matched and its program, which a hand
        #: choice holds off when it replaces that profile loaded by hand.
        self._matched: tuple[str, str] | None = None
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
        clock = lambda: time.monotonic()       # late-bound: a test swaps time
        self.focus = Focus(self.on_focus, lambda changed: XFocus(changed, clock=clock),
                           clock=clock)
        #: The focused program and the profile it binds, for `status`.
        self._focus_program: str | None = None
        self._focus_profile: str | None = None

    def commands(self) -> dict[str, Callable[[dict], dict]]:
        return {
            "profiles": self._cmd_profiles,
            "load": self._cmd_load,
            "unload": self._cmd_unload,
            "let_load": self._cmd_let_load,
            "keep_loaded": self._cmd_keep_loaded,
            "save_profile": self._cmd_save_profile,
            "profile_processes": self._cmd_profile_processes,
            "promote": self._cmd_promote,
            "delete_profile": self._cmd_delete_profile,
            "rename_profile": self._cmd_rename_profile,
            "import_profile": self._cmd_import_profile,
            "import_profile_lighting": self._cmd_import_profile_lighting,
            "import_m_profile": self._cmd_import_m_profile,
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
        the file as it was. A shadowed file has no why_unread(): it loaded, and as get() found
        no profile of this name, holds another's."""
        path = overlay.path
        fresh = self.library.get(overlay.name)
        if fresh is None and path is not None:
            fresh = next((p for p in self.library.all() if p.path == path), None)
        if fresh is not None:
            return fresh, None
        return None, self.library.why_unread(None if path is None else path.name)

    def unload_gone(self) -> None:
        """Unload the profile whose file or program has gone: one loaded by hand is no longer
        recorded; an auto-loaded one gives back the hand choice under it, which stays
        recorded, loaded or not."""
        restore = self.library.get(self._before_auto) if self._before_auto else None
        if not self.overlay_auto:
            self.d.config.active_game_profile = None
        self.overlay, self.overlay_auto = restore, False
        self.loads += 1
        self._before_auto = None

    def overlay_error(self) -> str | None:
        """What `reload` and `status` say while the loaded profile's file
        was not read at the last reload or, for an auto-loaded one, at a
        scan (scan_library), until a reload reads it or `save` writes over it."""
        overlay = self.overlay
        if overlay is None or overlay.unreadable is None:
            return None
        return (f"{overlay.path} could not be read ({overlay.unreadable}); game profile "
                f"{overlay.name!r} keeps what it held. Fix it and run `gearbench-ctl "
                "reload`")

    def load_remembered(self) -> None:
        """At the start, the game profile loaded by hand before the stop:
        its bindings apply at once, its lighting when the keyboard comes."""
        self.overlay = self.remembered()

    def remembered(self) -> GameProfile | None:
        """The hand choice config.json's `active_game_profile` names, or None. A name edited in
        its file is followed, one whose file will not load or whose folder cannot be listed
        stays recorded, and one gone is forgotten."""
        remembered = self.d.config.active_game_profile
        if not remembered:
            return None
        found = self.library.get(remembered)
        if found is not None:
            return found
        saved = self.library.directory / file_name(remembered)
        found = next((p for p in self.library.all() if p.path == saved), None)
        if found is not None:
            log.info("game profile %r is named %r in its file now; loading it under that name",
                     remembered, found.name)
            self.follow_rename(remembered, found.name)
            return found
        # Any file that will not load may be its file: its name is not known, and a hand-made
        # file's stem need not be the name's slug. A shadowed file is not: get() would have
        # found the one read in its place.
        unread = [escaped(file) for file, _why in self.library.unread()]
        if unread:
            # A folder that cannot be listed is the one entry, under its own name.
            why = (f"the {unread[0]} folder cannot be listed" if self.library.unlisted_reason
                   else f"{', '.join(unread)} will not load, and may hold it")
            log.info("game profile %r stays remembered: %s; it loads at a reload or a later "
                     "start once its file can be read", remembered, why)
            return None
        # In no file: forgotten, or looked for again at every start.
        log.info("game profile %r is no longer in the library; forgetting it", remembered)
        self.d.config.active_game_profile = None
        self.d.persist()
        return None

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
        return layer_in_force(self.overlay, self.d.config.profile).binding(key, shifted)

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

    def apply_layer(self) -> str | None:
        """Show the layer in force on the keyboard and the mice; apply_profile's answer."""
        failed = self.d.keyboard.apply_profile()
        self.d.bindings.stop_orphaned_macro()
        self.d.mice.dpi.follow_tuning()
        return failed

    # -- commands ------------------------------------------------------------

    def _cmd_profiles(self, request: dict) -> dict:
        """The game profiles, each with the lighting it shows on the keyboard model in force."""
        return {
            "ok": True,
            "overlay": self.overlay.name if self.overlay else None,
            "auto": self.overlay_auto,
            "profiles": [
                {"name": p.name, "mode": shown.mode, "effect": shown.effect,
                 "processes": p.processes, "keys": len(shown.keys)}
                for p in self.library.all() for shown in (self.d.per_keyboard.shown(p.lighting),)
            ],
        }

    def _cmd_load(self, request: dict) -> dict:
        name = name_text(request["value"], "load")
        profile = self.library.get(name)
        if profile is None:
            return {"ok": False, "error": f"no game profile named {name!r}"}
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
        self.d.keyboard.wake_for_change()
        failed = self.apply_layer()
        self.d.persist()
        log.info("loaded game profile %r over profile %d", profile.name, self.d.config.active)
        return with_note({"ok": True, "overlay": profile.name,
                          "mode": self.d.per_keyboard.shown(profile.lighting).mode},
                         failed, self.d.keyboard.last_apply_detail)

    def suppress_auto(self) -> None:
        """Before a hand choice replaces the overlay, hold it off while its program has focus
        or runs, however it was loaded, or the next scan would put it straight back. Only adds
        a hold-off, beside any other."""
        if self.overlay is None:
            return
        if self.overlay_auto:
            # Set with the flag, by a match (_take_match).
            process = cast("str", self._auto_process)
        elif self._matched is not None and same_profile(self._matched[0], self.overlay.name):
            process = self._matched[1]
        else:
            return
        for name in [name for name in self._held if same_profile(name, self.overlay.name)]:
            del self._held[name]        # the latest last, under its latest spelling
        self._held[self.overlay.name] = process
        log.info("game profile %r held off until %s: another choice was made by hand",
                 self.overlay.name, held_until(escaped(process), self.focus.ready))

    def held_by(self, name: str, program: str, present: Collection[str]) -> None:
        """A held-off profile stays held while any of its programs runs or has
        focus; name the one that holds it now once its stored one is gone."""
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
        failed = self._unload_by_hand()
        self.d.persist()
        return with_note({"ok": True, "unloaded": previous}, failed, self.d.keyboard.last_apply_detail)

    def _unload_by_hand(self) -> str | None:
        """Unload the game profile for a choice made by hand, held off while its program
        has focus or runs (suppress_auto), and show what is under it; apply_layer's answer."""
        self.suppress_auto()
        if self.overlay is not None:
            self.loads += 1
        self.overlay, self.overlay_auto = None, False
        self._before_auto = None
        self.d.keyboard.wake_for_change()
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
        value = on_or_off(request.get("value"))
        if value is None:
            return {"ok": False, "error": switch_refusal("keep_loaded", request.get("value"))}
        if value != self.keep_loaded:
            log.info("keep loaded %s", "on" if value else "off")
        self.keep_loaded = value
        self._kept_out = None
        return {"ok": True, "keep_loaded": value}

    def _cmd_save_profile(self, request: dict) -> dict:
        """Save what shows, overlay or M profile, as a game profile (_saved_lighting), with the
        layer's bindings. The lighting is saved for the keyboard model in force alone; a profile
        saved over keeps its others."""
        # Text, as a profile's file reads its name: str() would name it "None" for a null.
        expect(request["value"], str, "save_profile", "text")
        name = request["value"].strip()
        if not name:
            return {"ok": False, "error": "a game profile needs a name"}
        wanted = request.get("processes")
        if wanted is not None:
            # Here, so a refusal names the request: the save's own check names the file.
            wanted = profiles.check_programs(wanted, "save_profile.processes")
        saved = self._saved_lighting()
        # The loaded profile's own copy, which every overlay edit writes back
        # whole; a library copy would be undone by the next color.
        loaded = self.overlay is not None and same_profile(self.overlay.name, name)
        if loaded:
            self.check_read_file()
        existing = self.overlay if loaded else self.library.get(name)
        profile = existing or GameProfile(name=name)
        before = copy.deepcopy(profile)
        profile.name = name
        # Before any keyboard, no lighting shows: the profile keeps what it had, if anything.
        if (model := self.d.per_keyboard.model()) is not None:
            keep_entry(profile.lighting, model, saved)
        profile.bindings = dict(self.key_layer(False))
        profile.shift = dict(self.key_layer(True))
        profile.mouse = copy.deepcopy(self.mouse_entries())
        # An M profile has none: profile 1's are the ones in force under it.
        profile.mouse_tuning = dict(self.overlay.mouse_tuning) if self.overlay is not None else {}
        if wanted is not None:
            profile.processes = list(wanted)
        try:
            path = profile.save()
        except BaseException:
            if loaded:
                self.overlay = before
            raise
        if existing is None:
            # A new profile: DPIs chosen under a gone one of its name are not its own.
            self.d.mice.dpi.profile_gone(profile.name)
        # As the file reads back, its control-character check passed (GameProfile.from_json).
        profile.processes = [p.strip() for p in profile.processes]
        return {"ok": True, "saved": profile.name, "path": str(path)}

    def _saved_lighting(self) -> Lighting:
        """What shows on the keyboard model in force, as a game profile keeps it: an animation
        as it is, a disabled board static and black, any other effect as its color, and no
        params. The stored layout and background go with it, hidden where the source hides
        them."""
        layer = self.d.per_keyboard.shown()
        effect_name, settings = layer.effect, layer.settings()
        static, background = layer.color, layer.background
        effect = effect_name if effect_name in ANIMATIONS else "static"
        if effect_name == "disabled":
            static = background = 0x000000
        if effect in ANIMATIONS or self.d.keyboard.paint.shows_static(effect_name):
            mode = layer.mode
        else:
            # An effect a game profile does not keep: its color, the layout
            # kept and not shown.
            mode, static = STATIC, self.d.keyboard.shown_color()
        # The fields as the layer has them, as copies, then those the game profile keeps
        # otherwise; no params.
        saved = replace(layer, effect=effect, color=static, params={}, speeds=dict(layer.speeds),
                        keys=dict(layer.keys), mode=mode, background=background)
        # A game profile holds only speeds and direction: an M profile's hand-set period and
        # direction go there.
        if effect in ANIMATIONS and "period" in settings:
            fastest, slowest = SPEEDS[effect]
            saved.speeds[effect] = min(max(settings["period"], fastest), slowest)
        if effect == "wave" and saved.direction is None:
            saved.direction = next((word for word, code in WAVE_DIRECTIONS.items()
                                    if code == settings.get("direction")), None)
        return saved

    def _cmd_profile_processes(self, request: dict) -> dict:
        """Set which programs load a game profile, on the daemon's own copy
        when it is the loaded one, which every overlay edit writes back."""
        name = name_text(request["value"], "profile_processes")
        wanted = profiles.check_programs(request.get("processes"), "profile_processes.processes")
        loaded = self.overlay is not None and same_profile(self.overlay.name, name)
        profile = self.overlay if loaded else self.library.get(name)
        if profile is None:
            return {"ok": False, "error": f"no game profile named {name!r}"}
        if loaded:
            self.check_read_file()
        previous = list(profile.processes)
        profile.processes = list(wanted)
        try:
            profile.save()
        except BaseException:
            profile.processes = previous
            raise
        # As the file reads back, its control-character check passed (GameProfile.from_json).
        profile.processes = [p.strip() for p in profile.processes]
        return {"ok": True, "profile": profile.name, "processes": list(profile.processes)}

    def _cmd_promote(self, request: dict) -> dict:
        """Make what shows the active M profile's own, with or without a game
        profile loaded, and unload it."""
        self.d.check_config_writable()
        active = self.d.config.active
        before = copy.deepcopy(self.d.config.profiles[active])
        was = self.d.config.active_game_profile
        target = self.d.config.profile
        source = self.overlay.name if self.overlay else "the current settings"
        # A failed write puts the M profile and the remembered overlay back,
        # or a refused promote would show one thing and save another.
        try:
            # Without a game profile, the M profile's lighting already shows
            # and is saved.
            if self.overlay is not None:
                # Every keyboard model's: the M profile then shows on each what the game
                # profile showed.
                target.lighting = copy.deepcopy(self.overlay.lighting)
                target.keys = dict(self.overlay.bindings)
                target.shift = dict(self.overlay.shift)
                target.mouse = copy.deepcopy(self.overlay.mouse)
            # Saved before anything live is dropped.
            self.d.config.active_game_profile = None
            self.d.save_config()
        except BaseException:
            self.d.config.profiles[active] = before
            self.d.config.active_game_profile = was
            raise
        failed = self._unload_by_hand()
        log.info("promoted %s to the default for profile %d", escaped(source),
                 self.d.config.active)
        return with_note({"ok": True, "promoted_to": self.d.config.active, "from": source},
                         failed, self.d.keyboard.last_apply_detail)

    def _cmd_delete_profile(self, request: dict) -> dict:
        name = name_text(request["value"], "delete_profile")
        found = self.library.get(name)
        if found is None:
            return {"ok": False, "error": f"no game profile named {name!r}"}
        gone_name = found.name
        try:
            deleted = self.library.delete(gone_name)
        except OSError as exc:
            # Answered here: handle_command's OSError says a save failed.
            return refusal(f"Could not delete {gone_name!r} ({reason(exc)}).", exc)
        if not deleted:
            return {"ok": False, "error": not_deleted(gone_name)}
        log.info("deleted game profile %r", gone_name)
        # Nothing may go on naming the deleted file.
        if self.overlay is not None and same_profile(self.overlay.name, gone_name):
            self.unload_gone()
            self.d.keyboard.wake_for_change()
            failed = self.apply_layer()
        else:
            failed = None
        self.forget(gone_name)
        self.d.persist()
        return with_note({"ok": True, "deleted": gone_name}, failed, self.d.keyboard.last_apply_detail)

    def _cmd_rename_profile(self, request: dict) -> dict:
        """Rename a game profile: move its file and point everything that names it at the new
        name, so its loaded, held-off or kept-out state stays; nothing loads or unloads.
        Refused for a name another profile holds, while a hand-edited config.json names it,
        or, for the loaded one, while the library leaves its file out (check_read_file)."""
        old = name_text(request["value"], "rename_profile")
        new = request.get("to") or ""
        # Text, as a profile's file reads its name: str() would take a list as its repr.
        expect(new, str, "rename_profile.to", "text")
        new = new.strip()
        if not new:
            return {"ok": False, "error": "a game profile needs a name"}
        loaded = self.overlay is not None and same_profile(self.overlay.name, old)
        profile = self.overlay if loaded else self.library.get(old)
        if profile is None:
            return {"ok": False, "error": f"no game profile named {old!r}"}
        if loaded:
            self.check_read_file()
        was = profile.name
        taken = self.library.get(new)
        if taken is not None and not same_profile(taken.name, was):
            return {"ok": False, "error": f"a game profile named {taken.name!r} already "
                                          f"exists; rename or delete that one first"}
        recorded = (self.d.config.active_game_profile is not None
                    and same_profile(self.d.config.active_game_profile, was))
        if recorded:
            self.d.check_config_writable()
        path = profile.rename(new)
        if taken is None:
            self.d.mice.dpi.profile_gone(new)               # as _cmd_save_profile
        self.follow_rename(was, new)
        log.info("game profile %r renamed to %r", was, new)
        return {"ok": True, "renamed": was, "name": new, "path": str(path)}

    def follow_rename(self, was: str, new: str) -> None:
        """Point everything that names game profile `was` at `new`,
        config.json's `active_game_profile` included."""
        self.d.mice.dpi.profile_renamed(was, new)

        def follow[N: str | None](name: N) -> N | str:
            return new if name is not None and same_profile(name, was) else name

        self._before_auto = follow(self._before_auto)
        self._held = {follow(name): process for name, process in self._held.items()}
        self._focus_profile = follow(self._focus_profile)
        if self._matched is not None:
            self._matched = (follow(self._matched[0]), self._matched[1])
        if self._kept_out is not None:
            self._kept_out = (follow(self._kept_out[0]), follow(self._kept_out[1]))
        recorded = self.d.config.active_game_profile
        if recorded is not None and same_profile(recorded, was):
            self.d.config.active_game_profile = new
            self.d.persist()

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
        if self._matched is not None and same_profile(self._matched[0], gone):
            self._matched = None
        if self._kept_out is not None and any(same_profile(name, gone)
                                              for name in self._kept_out):
            self._kept_out = None
        recorded = self.d.config.active_game_profile
        if recorded and same_profile(recorded, gone):
            self.d.config.active_game_profile = None

    def _cmd_import_profile(self, request: dict) -> dict:
        """Write a whole game profile a front end built (the G HUB import), checked as the
        library reads it. A taken name is written over only with `replace`, the loaded one
        through the daemon's own copy. `keep_processes` makes a replace keep the profile's
        programs: the body's date from the plan."""
        return self._import_profile(request, lighting_only=False)

    def _cmd_import_profile_lighting(self, request: dict) -> dict:
        """The pack's `import_profile`: a replace writes the name, programs and lighting and
        leaves the bindings, MR layer and mouse buttons. Its own command, so a daemon older
        than the pack answers "unknown command" instead of replacing the whole profile (the
        owner's decision of 2026-09-29)."""
        return self._import_profile(request, lighting_only=True)

    def _import_profile(self, request: dict, lighting_only: bool) -> dict:
        # Not request["command"]: handle_command lowercases it only for dispatch.
        command = "import_profile_lighting" if lighting_only else "import_profile"
        imported = GameProfile.from_json(request.get("profile"), f"{command}.profile")
        loaded = self.overlay is not None and same_profile(self.overlay.name, imported.name)
        existing = self.overlay if loaded else self.library.get(imported.name)
        if existing is not None and not request.get("replace"):
            return {"ok": False, "error": f"a game profile named {existing.name!r} already exists"}
        if loaded:
            # As every other edit to the loaded profile: its file may be one the library now
            # leaves out for another of its name.
            self.check_read_file()
        left: list[str] = []
        if imported.processes and (existing is None or not request.get("keep_processes")):
            imported.processes, left = self._programs_free(imported)
        profile = existing or imported
        before = copy.deepcopy(profile)
        if existing is not None:
            fields = ["name"]
            if not request.get("keep_processes"):
                fields.append("processes")
            if not lighting_only:
                fields += ["bindings", "shift", "mouse"]
            for name in fields:
                setattr(profile, name, getattr(imported, name))
            # The keyboard models the import has lighting for; the profile's others stay.
            merge_under(profile.lighting, imported.lighting, keyboard_ids)
        try:
            path = profile.save()
        except BaseException:
            if loaded:
                self.overlay = before
            raise
        if existing is None:
            self.d.mice.dpi.profile_gone(profile.name)      # as _cmd_save_profile
        # Logged once saved: a refused save left nothing out. Escaped: a program name in the
        # request may hold a format character, such as a right-to-left mark.
        for program in left:
            log.info("game profile %r imported without %s, which another game profile loads",
                     imported.name, escaped(program))
        failed = None
        if loaded:
            self.d.keyboard.wake_for_change()
            failed = self.d.keyboard.apply_profile()
            self.d.bindings.stop_orphaned_macro()
        log.info("imported game profile %r", profile.name)
        return with_note({"ok": True, "saved": profile.name, "path": str(path),
                          "replaced": existing is not None}, failed,
                         self.d.keyboard.last_apply_detail)

    def _programs_free(self, imported: GameProfile) -> tuple[list[str], list[str]]:
        """`imported`'s programs that are free, and those left out: each that a game profile
        other than the one it replaces loads, by meeting apps.wanted_forms, as the plan has it
        (import_plan._programs_left). Checked again because the library may have changed since the
        plan read it, and of two profiles that load one program the first by filename wins.
        The profiles are the scan's (scan_library): an auto-loaded one whose file will not load
        counts."""
        taken = set().union(*(wanted_forms(program) for other in self.scan_library()
                              if not same_profile(other.name, imported.name)
                              for program in other.processes))
        free: list[str] = []
        left: list[str] = []
        for program in imported.processes:
            (free if taken.isdisjoint(wanted_forms(program)) else left).append(program)
        return free, left

    def _cmd_import_m_profile(self, request: dict) -> dict:
        """Put a front end's G keys, MR layer, mouse buttons and, with
        `lighting`, lighting on one M profile (the G HUB import's desktop
        default); a failed save puts it back. Only the keys `keys` names are written, every one
        for a null or a request without it (_keys_named); the G HUB import names the G915's and
        those of another keyboard the file has. The other keys, and the mouse models and
        keyboard models the import has nothing for, keep the M profile's."""
        index = whole_number(request["value"], "import_m_profile")
        if index not in PROFILE_IDS:
            return {"ok": False, "error": NOT_AN_M_PROFILE}
        imported = GameProfile.from_json(request.get("profile"), "import_m_profile.profile")
        keys = _keys_named(request.get("keys"))
        self.d.check_config_writable()
        target = self.d.config.profiles[index]
        before = copy.deepcopy(target)
        for key in keys:
            target.keys[key], target.shift[key] = imported.bindings[key], imported.shift[key]
        merge_under(target.mouse, imported.mouse, mouse_ids)
        if request.get("lighting"):
            # The keyboard models the import has lighting for; the M profile's others stay.
            merge_under(target.lighting, imported.lighting, keyboard_ids)
        try:
            self.d.save_config()
        except BaseException:
            self.d.config.profiles[index] = before
            raise
        failed = None
        if index == self.d.config.active and self.overlay is None:
            self.d.keyboard.wake_for_change()
            failed = self.d.keyboard.apply_profile()
        self.d.bindings.stop_orphaned_macro()
        log.info("imported M%d", index)
        return with_note({"ok": True, "profile": index, "name": target.name}, failed,
                         self.d.keyboard.last_apply_detail)

    # -- following focus or the running programs -----------------------------

    def deadlines(self) -> list[float]:
        """The next scan and walk, and focus's own: a focus change settling, an X reply
        overdue, an X connect attempt."""
        # A scan waiting for the first X connection is due at once, which
        # would spin the loop; the connection's own deadline wakes it.
        due = [] if self.focus.pending else [self._last_scan + PROCESS_SCAN_INTERVAL]
        if self._last_walk is not None:
            due.append(self._last_walk + PROCESS_SCAN_INTERVAL)
        focus_due = self.focus.deadline()
        if focus_due is not None:
            due.append(focus_due)
        return due

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
        PROCESS_SCAN_INTERVAL serves this and the other-tools check, whatever focus does."""
        now = time.monotonic()
        walked = self._last_walk is None or now - self._last_walk >= PROCESS_SCAN_INTERVAL
        procs = self.walk(now) if walked else None
        if self.focus.pending or now - self._last_scan < PROCESS_SCAN_INTERVAL:
            return
        self._last_scan = now
        profiles = self.scan_library()
        self.follow_read_file(profiles)
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
            self._focus_program = shown
            self._focus_profile = match.name if match else None
            if match in held:
                # A match names its program: names_of gives a program gone no names.
                self.held_by(match.name, cast("str", shown), names)
            self.follow_match(match, shown, keep=unread, profiles=profiles)
            return
        self._focus_program = self._focus_profile = None
        if not bound and not self.overlay_auto and not self._held:
            self._matched = None
            return          # nothing binds a process
        if not walked:
            procs = self.walk(now)
        if procs is None:
            return          # /proc could not be listed, which says nothing exited
        running = running_processes(procs)
        # A held-off profile's programs hold it off until none of them runs,
        # whatever else starts, and must not hide another profile's program,
        # nor load another profile that binds one of them too.
        waiting = {p.name for p in held
                   if any(names_match(want, name) for want in p.processes for name in running)}
        for p in held:
            if p.name in waiting:
                self.held_by(p.name, matched_by(p, running), running)
        held_names = {name for name in running
                      if any(names_match(want, name) for p in held for want in p.processes)}
        others = running - held_names
        match = matching_profile(others, [p for p in bound if p not in held])
        self.follow_match(match, matched_by(match, others) if match else None,
                          keep=waiting | unread, profiles=profiles)

    def follow_read_file(self, profiles: list[GameProfile]) -> None:
        """Take the loaded profile from the file the library reads for its name once its own
        file was left out for that one (Library.shadowed), or an edit saved to its own would
        be lost at the next load. A file that will not load is not shadowed; the profile keeps
        what it held."""
        overlay = self.overlay
        if overlay is None or overlay.path is None \
                or overlay.path.name not in self.library.shadowed:
            return
        fresh = next((p for p in profiles if same_profile(p.name, overlay.name)), None)
        if fresh is None:
            return
        # A profile the library read has its file (GameProfile.load).
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
        if overlay.path is None or overlay.path.name == file_name(overlay.name):
            return
        # First: the read sets Library.shadowed, which the next line tests.
        read = self.library.get(overlay.name)
        if overlay.path.name not in self.library.shadowed:
            return
        if read is None:
            raise ConfigError(f"game profile {overlay.name!r} is in no file now: "
                              f"{escaped(overlay.path.name)} holds another profile's name; "
                              "reload to unload it")
        # A profile the library read has its file (GameProfile.load).
        read_from = cast("Path", read.path).name
        raise ConfigError(f"game profile {overlay.name!r} is read from "
                          f"{escaped(read_from)} now, not {escaped(overlay.path.name)};"
                          f" Gearbench follows it within {PROCESS_SCAN_INTERVAL:g} s, or "
                          "reload now")

    def scan_library(self) -> list[GameProfile]:
        """The library for a scan, with the auto-loaded profile's own copy in place of its file
        when that will not load: a bad edit must not read as its program gone. A shadowed file
        did load, so it gets no stand-in."""
        profiles = self.library.all()
        if self.overlay_auto:
            # An auto-loaded overlay is there: _take_match sets the two together.
            overlay = cast("GameProfile", self.overlay)
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

    def unbound(self, name: str, was: str, program: str | None,
                profiles: list[GameProfile]) -> bool:
        """Whether profile `name`, no longer matched, lets go of `was`, the program that loaded
        or holds it, because its programs were edited: under focus, `was` still has focus
        (`program`); otherwise none of the programs `profiles` gives it is `was`. `profiles`
        holds `name`: a caller tests that first."""
        if self.focus.ready:
            # `program` is the whole name as shown, `was` may be the running programs' form of
            # it, lowercased and cut as comm is: only the whole name's forms include the cut.
            return program is not None and names_match(program, was)
        profile = next(p for p in profiles if same_profile(p.name, name))
        return not any(names_match(want, was) for want in profile.processes)

    def end_hold_offs(self, match: GameProfile | None, program: str | None,
                      keep: Collection[str], profiles: list[GameProfile]) -> None:
        """End each hold-off whose profile is not `match` and that `keep` does not name, and
        say why: its file has gone, its programs were edited, focus moved away or its program
        exited."""
        for name in [name for name in self._held
                     if not any(same_profile(name, kept) for kept in keep)
                     and (match is None or not same_profile(match.name, name))]:
            was = self._held.pop(name)
            why = self.let_go(name, was, program, profiles)
            log.info(HOLD_OFF_ENDS[why], {"name": name, "program": escaped(was)})

    def let_go(self, name: str, was: str, program: str | None,
               profiles: list[GameProfile]) -> str:
        """Why profile `name`, no longer matched, lets go of `was`, the program that loaded or
        holds it: GONE when `profiles`, the scan's library, lacks it, EDITED when its programs
        no longer include `was` (unbound), else MOVED under focus and EXITED without."""
        if not any(same_profile(p.name, name) for p in profiles):
            return GONE
        if self.unbound(name, was, program, profiles):
            return EDITED
        return MOVED if self.focus.ready else EXITED

    def follow_edited_name(self, read: GameProfile | None) -> None:
        """Follow a name edited in the loaded profile's own file, `read` being what the library
        read from it, as a rename: at a reload (reread), and for a scan's match (follow_match),
        which would else auto-load that file as another profile over it, and give a hand choice
        back by a name no file holds now. A scan takes nothing else from the file: the rest is
        taken at a reload, as any other edit made on disk."""
        overlay = self.overlay
        if read is not None and overlay is not None and read.path == overlay.path \
                and not same_profile(read.name, overlay.name):
            log.info("game profile %r is named %r in its file now; following it",
                     overlay.name, read.name)
            self.follow_rename(overlay.name, read.name)
            overlay.name = read.name

    def follow_match(self, match: GameProfile | None, program: str | None,
                     keep: Collection[str], profiles: list[GameProfile]) -> None:
        """Load, keep or drop the overlay for `match`, the profile a program binds, or None;
        `program` names it for the journal, `status` and a later hold-off. A profile loaded by
        hand is replaced and given back after, unless keep loaded is on; only an auto-loaded
        one is dropped here. A hold-off ends when the match is not its profile and `keep` does
        not name it (one of its programs runs, or its file will not load). let_go says why a
        hold-off ends or an auto-loaded profile is dropped: a profile missing from `profiles`,
        the scan's library, has gone, not its program, and a dropped one's DPIs are forgotten;
        one whose programs no longer include its program was edited (unbound). The journal
        says which. A match from the loaded profile's own file under a name edited there is
        followed as a reload follows it (follow_edited_name); `active_game_profile`, the hand
        choice, is written here only so."""
        self.follow_edited_name(match)
        # A match names its program: names_of gives a program gone no names, and matched_by
        # names one for every match.
        self._matched = (match.name, cast("str", program)) if match is not None else None
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
            log.info("game profile %r auto-loaded: %s is running", match.name,
                     escaped(program))
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
