"""The commands that edit the game profiles, and an M profile from one (GameCommands.commands).
Each reads the loaded profile and the layer in force on Games, its controller."""

from __future__ import annotations

import copy
import logging
from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING

from ... import profiles
from ...apps import wanted_forms
from ...config import (
    BINDABLE_KEYS,
    PROFILE_IDS,
    STATIC,
    Lighting,
    expect,
    merge_under,
    not_bindable,
    refuse_name,
)
from ...keyboard import keep_entry, model_ids as keyboard_ids
from ...library_files import not_deleted
from ...lighting import ANIMATIONS, SPEEDS, WAVE_DIRECTIONS
from ...mouse import model_ids as mouse_ids
from ...profiles import GameProfile
from ...settings_files import ConfigError
from ...wording import GAME_PROFILE_NEEDS_A_NAME, escaped, quoted, reason
from ..shared import (
    NO_GAME_PROFILE_NAMED,
    NOT_AN_M_PROFILE,
    name_text,
    refusal,
    same_profile,
    whole_number,
    with_note,
)

if TYPE_CHECKING:
    from .. import Daemon
    from ..keyboard import Keyboard
    from . import Games

log = logging.getLogger("gearbench")


def _keys_named(value: object) -> tuple[str, ...]:
    """The keys an `import_m_profile` request's `keys` names, each one of BINDABLE_KEYS, or
    every one of them for a null or a request without it, as a front end older than the field
    sends."""
    if value is None:
        return BINDABLE_KEYS
    keys = tuple(expect(value, list, "import_m_profile.keys", "a list of key names"))
    for key in keys:
        if key not in BINDABLE_KEYS:
            raise ConfigError(f"import_m_profile.keys: {not_bindable(key)}")
    return keys


class GameCommands:
    """The handlers of the commands that edit the game profiles and, from one, the M
    profiles."""

    def __init__(self, daemon: Daemon, games: Games) -> None:
        self.d = daemon
        self.games = games

    def commands(self) -> dict[str, Callable[[dict], dict]]:
        return {
            "save_profile": self._cmd_save_profile,
            "profile_processes": self._cmd_profile_processes,
            "promote": self._cmd_promote,
            "delete_profile": self._cmd_delete_profile,
            "rename_profile": self._cmd_rename_profile,
            "import_profile": self._cmd_import_profile,
            "import_profile_lighting": self._cmd_import_profile_lighting,
            "import_m_profile": self._cmd_import_m_profile,
        }

    def _cmd_save_profile(self, request: dict) -> dict:
        """Save what shows, overlay or M profile, as a game profile (_saved_lighting), with the
        layer's bindings. The lighting is saved for the model of the keyboard `keyboard` names,
        else the leader's (Keyboards.aimed), alone: what it shows, the leader's while it
        follows; a profile saved over keeps its others. The mouse color is
        MouseLighting.color_to_save's."""
        # Text, as a profile's file reads its name: str() would name it "None" for a null.
        name = expect(request["value"], str, "save_profile", "text").strip()
        if not name:
            return {"ok": False, "error": GAME_PROFILE_NEEDS_A_NAME}
        # Here, so a refusal names the request: the save's own check names the file.
        refuse_name(name, "save_profile")
        wanted = request.get("processes")
        if wanted is not None:
            wanted = profiles.check_programs(wanted, "save_profile.processes")
        mouse_color = self.d.mice.color.color_to_save(request)
        keyboard = self.d.keyboards.aimed(request)
        saved = self._saved_lighting(keyboard)
        # The loaded profile's own copy, which every overlay edit writes back
        # whole; a library copy would be undone by the next color.
        loaded, existing = self.games.loaded_copy(name)
        profile = existing or GameProfile(name=name)
        before = copy.deepcopy(profile)
        profile.name = name
        # Before any keyboard, no lighting shows: the profile keeps what it had, if anything.
        if (model := keyboard.model()) is not None:
            keep_entry(profile.lighting, model, saved)
        profile.bindings = dict(self.games.key_layer(False))
        profile.shift = dict(self.games.key_layer(True))
        profile.mouse = copy.deepcopy(self.games.mouse_entries())
        # An M profile has none: profile 1's are the ones in force under it.
        profile.mouse_tuning = dict(self.games.overlay.mouse_tuning) if self.games.overlay is not None else {}
        profile.mouse_color = mouse_color
        if wanted is not None:
            profile.processes = wanted
        try:
            path = profile.save()
        except BaseException:
            if loaded:
                self.games.overlay = before
            raise
        if existing is None:
            # A new profile: DPIs chosen under a gone one of its name are not its own.
            self.d.mice.dpi.profile_gone(profile.name)
        if loaded:
            # A mouse color the request named; the mice skip one shown already.
            self.d.sync_to_leader()
        return {"ok": True, "saved": profile.name, "path": str(path),
                "mouse_color": profiles.mouse_color_json(mouse_color)}

    def _saved_lighting(self, keyboard: Keyboard) -> Lighting:
        """What shows on `keyboard`, as a game profile keeps it: an animation as it is, a
        disabled board static and black, any other effect as its color, and no params. The
        stored layout and background go with it, hidden where the source hides them."""
        layer = self.d.per_keyboard.shown(keyboard=keyboard)
        effect_name, settings = layer.effect, layer.settings()
        static, background = layer.color, layer.background
        effect = effect_name if effect_name in ANIMATIONS else "static"
        if effect_name == "disabled":
            static = background = 0x000000
        if effect in ANIMATIONS or keyboard.paint.shows_static(effect_name):
            mode = layer.mode
        else:
            # An effect a game profile does not keep: its color, the layout
            # kept and not shown.
            mode, static = STATIC, keyboard.shown_color()
        # The fields as the layer has them, as copies, then those the game profile keeps
        # otherwise; no params.
        saved = replace(layer, effect=effect, color=static, params={}, speeds=dict(layer.speeds),
                        keys=dict(layer.keys), mode=mode, background=background)
        # A game profile holds only speeds and direction: an M profile's hand-set period, for
        # an animation that takes a speed, and direction go there.
        if effect in SPEEDS and "period" in settings:
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
        profile = self.games.loaded_copy(name)[1]
        if profile is None:
            return {"ok": False, "error": NO_GAME_PROFILE_NAMED.format(quoted(name))}
        previous = list(profile.processes)
        profile.processes = wanted
        try:
            profile.save()
        except BaseException:
            profile.processes = previous
            raise
        return {"ok": True, "profile": profile.name, "processes": list(profile.processes)}

    def _cmd_promote(self, request: dict) -> dict:
        """Make what shows the active M profile's own, with or without a game
        profile loaded, and unload it."""
        active = self.d.config.active
        before = copy.deepcopy(self.d.config.profiles[active])
        was = self.d.config.active_game_profile
        target = self.d.config.profile
        source = self.games.overlay.name if self.games.overlay else "the current settings"
        # A failed write puts the M profile and the remembered overlay back,
        # or a refused promote would show one thing and save another.
        try:
            # Without a game profile, the M profile's lighting already shows
            # and is saved.
            if self.games.overlay is not None:
                # Every keyboard model's: the M profile then shows on each what the game
                # profile showed.
                target.lighting = copy.deepcopy(self.games.overlay.lighting)
                target.keys = dict(self.games.overlay.bindings)
                target.shift = dict(self.games.overlay.shift)
                target.mouse = copy.deepcopy(self.games.overlay.mouse)
                # Only with a profile loaded: with none, the record may name a hand choice
                # whose file will not load yet, which a reload or a later start loads
                # (Games.remembered).
                self.d.config.active_game_profile = None
            # Saved before anything live is dropped.
            self.d.save_config()
        except BaseException:
            self.d.config.profiles[active] = before
            self.d.config.active_game_profile = was
            raise
        shown = self.games.unload_by_hand()
        log.info("promoted %s to the default for profile %d", escaped(source),
                 self.d.config.active)
        return with_note({"ok": True, "promoted_to": self.d.config.active, "from": source}, *shown)

    def _cmd_delete_profile(self, request: dict) -> dict:
        name = name_text(request["value"], "delete_profile")
        found = self.games.library.get(name)
        if found is None:
            return {"ok": False, "error": NO_GAME_PROFILE_NAMED.format(quoted(name))}
        # The loaded profile's own file under a name edited there: the delete unloads it.
        self.games.follow_edited_name(found)
        gone_name = found.name
        try:
            deleted = self.games.library.delete(gone_name)
        except OSError as exc:
            # Answered here: handle_command's OSError says a save failed.
            return refusal(f"could not delete {quoted(gone_name)}: {reason(exc)}", exc)
        if not deleted:
            return {"ok": False, "error": not_deleted(gone_name)}
        log.info("deleted game profile %r", gone_name)
        # Nothing may go on naming the deleted file. Before the give-back: the hand choice it
        # gives back may hold the deleted name in its own file now (found).
        self.games.forget(gone_name)
        if self.games.overlay is not None and same_profile(self.games.overlay.name, gone_name):
            self.games.unload_gone()
            self.d.keyboards.wake_each()
            shown = self.games.apply_layer()
        else:
            shown = None, None
        self.d.persist()
        return with_note({"ok": True, "deleted": gone_name}, *shown)

    def _cmd_rename_profile(self, request: dict) -> dict:
        """Rename a game profile: move its file and point everything that names it at the new
        name, so its loaded, held-off or kept-out state stays; nothing loads or unloads.
        Refused for a name another profile holds, while a hand-edited config.json names it,
        or, for the loaded one, while the library leaves its file out (check_read_file)."""
        old = name_text(request["value"], "rename_profile")
        # Text, as a profile's file reads its name: str() would take a list as its repr.
        new = expect("" if request.get("to") is None else request["to"], str,
                     "rename_profile.to", "text").strip()
        if not new:
            return {"ok": False, "error": GAME_PROFILE_NEEDS_A_NAME}
        # Here, so a refusal names the request: the rename's own check names the file.
        refuse_name(new, "rename_profile.to")
        profile = self.games.loaded_copy(old)[1]
        if profile is None:
            return {"ok": False, "error": NO_GAME_PROFILE_NAMED.format(quoted(old))}
        was = profile.name
        taken = self.games.library.get(new)
        if taken is not None and not same_profile(taken.name, was):
            return {"ok": False, "error": f"a game profile named {quoted(taken.name)} already "
                                          f"exists; rename or delete that one first"}
        recorded = (self.d.config.active_game_profile is not None
                    and same_profile(self.d.config.active_game_profile, was))
        if recorded:
            self.d.check_config_writable()
        path = profile.rename(new)
        if taken is None:
            self.d.mice.dpi.profile_gone(new)               # as _cmd_save_profile
        self.games.follow_rename(was, new)
        log.info("game profile %r renamed to %r", was, new)
        return {"ok": True, "renamed": was, "name": new, "path": str(path)}

    def _cmd_import_profile(self, request: dict) -> dict:
        """Write a whole game profile a front end built (the G HUB import), checked as the
        library reads it. A taken name is written over only with `replace`, the loaded one
        through the daemon's own copy. `keep_processes` true makes a replace keep the profile's
        programs: the body's date from the plan."""
        return self._import_profile(request, lighting_only=False)

    def _cmd_import_profile_lighting(self, request: dict) -> dict:
        """The pack's `import_profile`: a replace writes the name, programs and lighting, and
        the mouse color where the import has one, and leaves the bindings, MR layer and mouse
        buttons. Its own command, so a daemon older
        than the pack answers "unknown command" instead of replacing the whole profile (the
        owner's decision of 2026-09-29)."""
        return self._import_profile(request, lighting_only=True)

    def _import_profile(self, request: dict, lighting_only: bool) -> dict:
        # Not request["command"]: handle_command lowercases it only for dispatch.
        command = "import_profile_lighting" if lighting_only else "import_profile"
        imported = GameProfile.from_json(request.get("profile"), f"{command}.profile")
        loaded, existing = self.games.loaded_copy(imported.name)
        if existing is not None and request.get("replace") is not True:
            return {"ok": False,
                    "error": f"a game profile named {quoted(existing.name)} already exists"}
        # Only JSON true keeps them, as only true replaces: "false" asks for no keeping.
        keep_processes = request.get("keep_processes") is True
        left: list[str] = []
        if imported.processes and (existing is None or not keep_processes):
            imported.processes, left = self._programs_free(imported)
        profile = existing or imported
        before = copy.deepcopy(profile)
        if existing is not None:
            fields = ["name"]
            if not keep_processes:
                fields.append("processes")
            if not lighting_only:
                fields += ["bindings", "shift", "mouse"]
            for name in fields:
                setattr(profile, name, getattr(imported, name))
            # The keyboard models the import has lighting for; the profile's others stay. So
            # does the profile's mouse color, where the import has none.
            merge_under(profile.lighting, imported.lighting, keyboard_ids)
            if imported.mouse_color is not None:
                profile.mouse_color = imported.mouse_color
        try:
            path = profile.save()
        except BaseException:
            if loaded:
                self.games.overlay = before
            raise
        if existing is None:
            self.d.mice.dpi.profile_gone(profile.name)      # as _cmd_save_profile
        # Logged once saved: a refused save left nothing out. Escaped: a program name in the
        # request may hold a format character, such as a right-to-left mark.
        for program in left:
            log.info("game profile %r imported without %s, which another game profile loads",
                     imported.name, escaped(program))
        shown: tuple[str | None, str | None] = None, None
        if loaded:
            self.d.keyboards.wake_each()
            shown = self.games.show_on_each()
            self.d.bindings.stop_orphaned_macro()
        log.info("imported game profile %r", profile.name)
        return with_note({"ok": True, "saved": profile.name, "path": str(path),
                          "replaced": existing is not None}, *shown)

    def _programs_free(self, imported: GameProfile) -> tuple[list[str], list[str]]:
        """`imported`'s programs that are free, and those left out: each that a game profile
        other than the one it replaces loads, by meeting apps.wanted_forms, as the plan has it
        (import_plan._programs_left). Checked again because the library may have changed since the
        plan read it, and of two profiles that load one program the first by filename wins.
        The profiles are the scan's (scan_library): the loaded one whose file will not load
        counts."""
        taken = set().union(*(wanted_forms(program) for other in self.games.scan_library()
                              if not same_profile(other.name, imported.name)
                              for program in other.processes))
        free: list[str] = []
        left: list[str] = []
        for program in imported.processes:
            (free if taken.isdisjoint(wanted_forms(program)) else left).append(program)
        return free, left

    def _cmd_import_m_profile(self, request: dict) -> dict:
        """Put a front end's G keys, MR layer, mouse buttons and, with `lighting`, lighting on
        one M profile (the G HUB import's desktop default); a failed save puts it back. Only
        the keys `keys` names are written, every one for a null or a request without it
        (_keys_named); the other keys, and the mouse models and keyboard models the import has
        nothing for, keep the M profile's."""
        index = whole_number(request["value"], "import_m_profile")
        if index not in PROFILE_IDS:
            return {"ok": False, "error": NOT_AN_M_PROFILE.format(index)}
        imported = GameProfile.from_json(request.get("profile"), "import_m_profile.profile")
        keys = _keys_named(request.get("keys"))
        target = self.d.config.profiles[index]
        before = copy.deepcopy(target)
        for key in keys:
            target.keys[key], target.shift[key] = imported.bindings[key], imported.shift[key]
        merge_under(target.mouse, imported.mouse, mouse_ids)
        if request.get("lighting") is True:
            # The keyboard models the import has lighting for; the M profile's others stay.
            merge_under(target.lighting, imported.lighting, keyboard_ids)
        try:
            self.d.save_config()
        except BaseException:
            self.d.config.profiles[index] = before
            raise
        shown: tuple[str | None, str | None] = None, None
        if index == self.d.config.active and self.games.overlay is None:
            self.d.keyboards.wake_each()
            shown = self.games.show_on_each()
        self.d.bindings.stop_orphaned_macro()
        log.info("imported M%d", index)
        return with_note({"ok": True, "profile": index, "name": target.name}, *shown)
