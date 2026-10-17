"""Game profiles: a library of named layouts, one JSON file each.

A game profile holds a lighting layout, G-key bindings, the mouse's G4-G9 bindings and, per
mouse model, its own DPI stages and report rate. It is an overlay on the active M profile,
which keeps its own defaults. One file per profile, so a game's layout can be copied or
shared on its own, and a bad file costs one profile.
"""

from __future__ import annotations

import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from .apps import MAX_PROGRAM_BYTES
from .config import (
    DEFAULT_BACKGROUND,
    DEFAULT_COLOR,
    G_KEYS,
    MOUSE_DEFAULTS,
    PER_KEY,
    STATIC,
    ConfigError,
    FileUnreadable,
    SkipWarnings,
    changed_on_disk,
    config_dir,
    effect_settings,
    expect,
    file_stamp,
    format_color,
    from_file,
    g_key_layer,
    layer_binding,
    layer_color,
    mouse_layer,
    parse_color,
    read_direction,
    read_mode,
    read_speeds,
    refuse_controls,
    sync_removal,
    whole,
    write_json,
    written,
)
from .library_files import (
    by_name,
    check_version,
    file_name,
    list_folder,
    read_folder,
    read_name,
    refuse_taken,
)
from .onboard import DPI_HIGHEST, DPI_LOWEST, DPI_STAGES, REPORT_RATES, dpi_fits

# STATIC and PER_KEY are config.py's (an M profile has a mode too), re-exported; so
# is onboard.py's REPORT_RATES, the rates a game profile can hold, those onboard profile 1
# can store, and apps.py's MAX_PROGRAM_BYTES, the longest program name a profile holds.
__all__ = ["FOLDER_UNLISTED", "LIGHTING_FIELDS", "MAX_PROCESSES", "MAX_PROGRAM_BYTES",
           "PER_KEY", "REPORT_RATES", "STATIC", "VERSION", "GameProfile", "Library",
           "MouseTuning", "check_programs", "profiles_dir"]

VERSION = 1

#: The most programs a game profile may load for: the daemon's scan compares each with every
#: running program every five seconds, on the thread that carries the G keys and macros.
MAX_PROCESSES = 64

#: A game profile's lighting fields: what a pack profile holds besides name and programs.
LIGHTING_FIELDS = ("mode", "effect", "speeds", "direction", "base", "background", "keys")

#: Why a file in the profiles folder was not read while the folder could not be listed, by
#: the OSError's reason in words (Library.folder_why).
FOLDER_UNLISTED = "its folder cannot be listed: {}"


def profiles_dir() -> Path:
    return config_dir() / "profiles"


TUNING_FIELDS = ("dpi_stages", "default", "shift", "report_rate")


@dataclass(frozen=True)
class MouseTuning:
    """A game profile's DPI stages and report rate for one mouse model, in force while the
    profile is loaded and the mouse is in HOST mode. Either part left None falls to onboard
    profile 1; `default` and `shift` need `dpi_stages`."""

    dpi_stages: tuple[int, ...] | None = None
    #: Each one of `dpi_stages`: the DPI the profile loads at, and the one the paddle drops to.
    default: int | None = None
    shift: int | None = None
    report_rate: int | None = None

    @property
    def empty(self) -> bool:
        return self.dpi_stages is None and self.report_rate is None

    def to_json(self) -> dict:
        out: dict = {}
        if self.dpi_stages is not None:
            out.update(dpi_stages=list(self.dpi_stages), default=self.default, shift=self.shift)
        if self.report_rate is not None:
            out["report_rate"] = self.report_rate
        return out

    @classmethod
    def from_json(cls, data: object, where: str) -> MouseTuning:
        expect(data, dict, where, "an object")
        data = cast(dict, data)     # expect raised for anything else
        unknown = [k for k in data if k not in TUNING_FIELDS]
        if unknown:
            raise ConfigError(f"{where}: {unknown[0]!r} is not one of "
                              f"{', '.join(TUNING_FIELDS)}")
        rate = data.get("report_rate")
        if rate is not None and not (whole(rate) and rate in REPORT_RATES):
            raise ConfigError(f"{where}.report_rate: must be one of "
                              f"{', '.join(map(str, REPORT_RATES))} Hz, not {written(rate)}")
        stages = data.get("dpi_stages")
        if stages is None:
            for name in ("default", "shift"):
                if data.get(name) is not None:
                    raise ConfigError(f"{where}.{name}: a stage needs dpi_stages")
            return cls(report_rate=rate)
        expect(stages, list, f"{where}.dpi_stages", "a list of DPI values")
        if not 1 <= len(stages) <= DPI_STAGES:
            raise ConfigError(f"{where}.dpi_stages: between 1 and {DPI_STAGES} stages, "
                              f"not {len(stages)}")
        for value in stages:
            if not whole(value) or not dpi_fits(value):
                raise ConfigError(f"{where}.dpi_stages: each must be a whole number from "
                                  f"{DPI_LOWEST} to {DPI_HIGHEST}, not {written(value)}")
        listed = ", ".join(map(str, stages))
        if len(set(stages)) != len(stages):
            raise ConfigError(f"{where}.dpi_stages: the stages must be distinct, not {listed}")
        among = "the one stage" if len(stages) == 1 else "one of the stages"
        for name in ("default", "shift"):
            value = data.get(name)
            if not whole(value) or value not in stages:
                raise ConfigError(f"{where}.{name}: must be {among} {listed}, "
                                  f"not {written(value)}")
        return cls(dpi_stages=tuple(stages), default=data["default"], shift=data["shift"],
                   report_rate=rate)


def read_tuning(raw: object, where: str) -> dict[str, MouseTuning]:
    """Read `mouse_tuning`, keyed by mouse product id in four lowercase hex digits
    (config.mouse_dpi's keys); absent or null is none, and an empty entry is dropped."""
    if raw is None:
        return {}
    expect(raw, dict, where, "an object of mouse product ids")
    raw = cast(dict, raw)       # expect raised for anything else
    out: dict[str, MouseTuning] = {}
    # Not `out`, which drops empty entries, so a duplicate is refused in any key order.
    seen: set[str] = set()
    for key, value in raw.items():
        product = key.strip().lower()
        if len(product) != 4 or not all(c in string.hexdigits for c in product):
            raise ConfigError(f"{where}: {key!r} is not a mouse product id like '4099'")
        if product in seen:
            raise ConfigError(f"{where}: {key!r} names a mouse another key names")
        seen.add(product)
        tuning = MouseTuning.from_json(value, f"{where}.{key}")
        if not tuning.empty:
            out[product] = tuning
    return out


def check_programs(processes: object, where: str) -> list[str]:
    """`processes` as the programs a game profile loads for. A refusal names `where` they came
    from: not a list, more than MAX_PROCESSES, or one that is not a name, is longer than a
    file name or holds a control character."""
    expect(processes, list, where, "a list of program names")
    programs = cast(list, processes)       # expect raised for anything else
    if len(programs) > MAX_PROCESSES:
        raise ConfigError(f"{where}: {len(programs)} programs is more than the "
                          f"{MAX_PROCESSES} a game profile may load for")
    for i, process in enumerate(programs):
        # An empty name is contained in every name, so it would load
        # this profile for whatever happened to be running.
        if not isinstance(process, str) or not process.strip():
            raise ConfigError(f"{where}[{i}]: must be a program name, not {written(process)}")
        size = len(process.strip().encode(errors="replace"))
        if size > MAX_PROGRAM_BYTES:
            raise ConfigError(f"{where}[{i}]: a program name of {size} bytes is "
                              f"longer than the {MAX_PROGRAM_BYTES} a file name may be")
        refuse_controls(process, f"{where}[{i}]")
    return programs


@dataclass
class GameProfile:
    name: str
    #: Process names that auto-load this profile. Empty means manual only.
    processes: list[str] = field(default_factory=list)
    mode: str = STATIC
    #: The zone effect, as an M profile's: an animation (lighting.ANIMATIONS) shows in place of
    #: `mode` and leaves it alone; "static" shows the mode (the owner's decision of 2026-09-24).
    effect: str = "static"
    #: Each animation's speed in milliseconds and the wave's direction, as config.Lighting's.
    speeds: dict[str, int] = field(default_factory=dict)
    direction: str | None = None
    #: The Static color: the whole keyboard in static mode.
    base: int = DEFAULT_COLOR
    #: Per-key colors by key name, used in per-key mode.
    keys: dict[str, int] = field(default_factory=dict)
    #: What an unpainted key shows in per-key mode, kept apart from `base` (the owner's
    #: decision of 2026-09-23).
    background: int = DEFAULT_BACKGROUND
    #: G-key bindings, None meaning unbound.
    bindings: dict[str, str | None] = field(default_factory=lambda: dict.fromkeys(G_KEYS))
    shift: dict[str, str | None] = field(default_factory=lambda: dict.fromkeys(G_KEYS))
    #: The mouse's G4-G9: a built-in action, a key spec or a macro.
    mouse: dict[str, str] = field(default_factory=lambda: dict(MOUSE_DEFAULTS))
    #: Its own DPI stages and report rate by mouse product id; a mouse with none runs profile 1's.
    mouse_tuning: dict[str, MouseTuning] = field(default_factory=dict)
    #: Where it was loaded from, so save() can overwrite in place.
    path: Path | None = field(default=None, compare=False)
    #: file_stamp of `path` at the last read or write. A save refuses when the file changed
    #: since, so a hand edit is not overwritten by the next color or binding.
    disk_stamp: tuple[int, int, int, int] | None = field(default=None, compare=False,
                                                         repr=False)
    #: Why its file would not load at the last `reload` or scan, which kept this copy as it
    #: was; None once it is written.
    unreadable: str | None = field(default=None, compare=False, repr=False)

    @property
    def color(self) -> int:
        """`base` by the name config.Lighting gives its Static color, so that code which edits
        the lighting layer in force (daemon.games.Games.lighting_layer) reads and writes it by
        one name. Not a field: the file's key stays `base`."""
        return self.base

    @color.setter
    def color(self, value: int) -> None:
        self.base = value

    def mode_color(self) -> int:
        """`base` under an animation or in static mode, `background` in per-key mode: the color
        that shows, except that the disabled effect shows black."""
        return layer_color(self.effect, self.mode, self.base, self.background)

    def binding(self, key: str, shifted: bool) -> str | None:
        """A G key's binding, the MR layer's when `shifted` and it has one, as an M profile's
        (config.layer_binding)."""
        return layer_binding(self.bindings, self.shift, key, shifted)

    def settings(self, effect: str | None = None) -> dict[str, int]:
        """What this profile saved for `effect`, its own by default, as
        set_effect parameters (config.effect_settings)."""
        return effect_settings(effect or self.effect, self.speeds, self.direction)

    def tuning(self, keys: list[str]) -> MouseTuning | None:
        """Its own stages and rate for the mouse `keys` name, its own product
        id first and then the same model's on its other route
        (daemon.mice.attached.dpi_keys), or None."""
        return next((self.mouse_tuning[key] for key in keys if key in self.mouse_tuning), None)

    def set_tuning(self, keys: list[str], tuning: MouseTuning) -> None:
        """Keep `tuning` under the first of `keys` and drop the others, the
        same mouse on its other route; an empty one drops them all."""
        for key in keys:
            self.mouse_tuning.pop(key, None)
        if not tuning.empty:
            self.mouse_tuning[keys[0]] = tuning

    def to_json(self) -> dict:
        return {
            "version": VERSION,
            "name": self.name,
            "processes": list(self.processes),
            "mode": self.mode,
            "effect": self.effect,
            "speeds": dict(self.speeds),
            "direction": self.direction,
            "base": format_color(self.base),
            "background": format_color(self.background),
            "keys": {k: format_color(v) for k, v in sorted(self.keys.items())},
            "bindings": dict(self.bindings),
            "shift": dict(self.shift),
            "mouse": dict(self.mouse),
            "mouse_tuning": {k: t.to_json() for k, t in sorted(self.mouse_tuning.items())},
        }

    @classmethod
    def from_json(cls, data: object, where: str) -> GameProfile:
        if not isinstance(data, dict):
            raise ConfigError(f"{where}: profile must be a JSON object")
        check_version(data, where, VERSION)
        mode = read_mode(data, where)

        # Only null is the default, as in config.json: false, 0 or "" is a wrong type, which
        # read as the default would be saved over the hand edit.
        processes = data.get("processes")
        if processes is None:
            processes = []
        check_programs(processes, f"{where}.processes")
        keys = data.get("keys")
        if keys is None:
            keys = {}
        expect(keys, dict, f"{where}.keys", "an object of key names and colors")
        name = read_name(data, where, "game profile")
        base = parse_color(data.get("base", DEFAULT_COLOR), f"{where}.base")
        effect = data.get("effect", "static")
        expect(effect, str, f"{where}.effect", "an effect name")
        return cls(
            name=name,
            processes=[p.strip() for p in processes],
            mode=mode,
            effect=effect,
            speeds=read_speeds(data.get("speeds"), f"{where}.speeds"),
            direction=read_direction(data.get("direction"), f"{where}.direction"),
            base=base,
            background=parse_color(data.get("background", DEFAULT_BACKGROUND),
                                   f"{where}.background"),
            keys={k: parse_color(v, f"{where}.keys.{k}") for k, v in keys.items()},
            bindings=g_key_layer(data.get("bindings"), f"{where}.bindings"),
            shift=g_key_layer(data.get("shift"), f"{where}.shift"),
            mouse=mouse_layer(data.get("mouse"), f"{where}.mouse"),
            mouse_tuning=read_tuning(data.get("mouse_tuning"), f"{where}.mouse_tuning"),
        )

    # -- persistence -----------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> GameProfile:
        stamp = file_stamp(path)
        profile = from_file(path, lambda raw: cls.from_json(raw, path.name))
        profile.path, profile.disk_stamp = path, stamp
        return profile

    def check_unchanged(self) -> None:
        """Raise ConfigError if its file would not load when last read (`unreadable`) or has
        changed on disk since. A reload does not take a file that still will not load, so that
        refusal names `gearbench-ctl save` too."""
        if self.unreadable is not None:
            raise FileUnreadable(
                f"{self.path} could not be read ({self.unreadable}); nothing is saved "
                "over it. Fix it and run `gearbench-ctl reload`; `gearbench-ctl save` "
                "writes the daemon's copy over it.")
        if self.changed():
            # A path: `changed` is False without one.
            raise changed_on_disk(cast(Path, self.path))

    def changed(self) -> bool:
        """Whether its file changed on disk since its last read or write here."""
        return (self.path is not None and self.disk_stamp is not None
                and file_stamp(self.path) != self.disk_stamp)

    def save(self, directory: Path | None = None, over_an_edit: bool = False) -> Path:
        """Write atomically, after checking what is about to be written.

        Checked as `load` will read it, so a save cannot leave a file that will not load. A
        profile not loaded from a file does not overwrite one at its path: that file is one the
        library could not read or another profile's, and the user's to sort out. `over_an_edit`
        writes over a file changed on disk since or marked unreadable, which only
        `gearbench-ctl save` asks.
        """
        target = self.path or (directory or profiles_dir()) / file_name(self.name)
        GameProfile.from_json(self.to_json(), target.name)
        if not over_an_edit:
            self.check_unchanged()
        # Not following a link: one to nothing or a loop is a file the library
        # lists as unreadable.
        if self.path is None and target.exists(follow_symlinks=False):
            refuse_taken(target, GameProfile.load, "game profile")
        target.parent.mkdir(parents=True, exist_ok=True)
        write_json(target, self.to_json(), ".profile-")
        self.path, self.disk_stamp, self.unreadable = target, file_stamp(target), None
        return target

    def rename(self, name: str) -> Path:
        """Take the name `name` and move to the file it calls for, as `Macro.save` does.

        A file whose name and contents disagree blocks the next profile saved under the old
        name: its save is refused. A refusal changes nothing. A new name with the same stem is
        written in place; the old file goes once the new one is written.
        """
        # A path: only a profile the library read is renamed, and `load` set it.
        old = cast(Path, self.path)
        target = old.parent / file_name(name)
        self.check_unchanged()
        if target != old and target.exists(follow_symlinks=False):   # as save's
            refuse_taken(target, GameProfile.load, "game profile")
        was = self.name
        self.name = name
        try:
            GameProfile.from_json(self.to_json(), target.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            write_json(target, self.to_json(), ".profile-")
        except BaseException:
            self.name = was
            raise
        self.path, self.disk_stamp = target, file_stamp(target)
        if old != target:
            old.unlink(missing_ok=True)
            sync_removal(old)
        return target


class Library:
    """The set of game profiles on disk."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or profiles_dir()
        self._skipped = SkipWarnings("game profile")
        #: The files the last `all()` left out, as (file name, why), for the window to show:
        #: those it could not read and those in `shadowed`. A directory it could not list is one
        #: entry, under its own name.
        self.skipped: list[tuple[str, str]] = []
        #: Why the last `all()` could not list the directory, or None, as Folder has it.
        self.unlisted_reason: str | None = None
        #: The files in `skipped` that loaded, left out for another file of
        #: the same name. Such a file holds no profile a lookup by name misses.
        self.shadowed: set[str] = set()

    def names(self) -> list[str]:
        return [p.name for p in self.all()]

    def unread(self) -> list[tuple[str, str]]:
        """The entries of `skipped` the last `all()` could not read: a file
        that will not load, or the directory. Not those in `shadowed`."""
        return [(file, why) for file, why in self.skipped if file not in self.shadowed]

    def files(self) -> list[Path]:
        """The files `all()` reads, for a caller that stamps them to see a change. Raises
        OSError for a directory it cannot list, one not made yet included."""
        return list_folder(self.directory)

    def all(self) -> list[GameProfile]:
        folder = read_folder(self.directory, GameProfile.load, self._skipped)
        self.skipped, self.shadowed = folder.skipped, folder.shadowed
        self.unlisted_reason = folder.unlisted_reason
        return folder.kept

    def get(self, name: str) -> GameProfile | None:
        return by_name(self.all(), name)

    def folder_why(self) -> str | None:
        """Why a file in the folder was not read, in words, when the last `all()` could not
        list the folder; else None. A message that names the file holds the folder's path, so
        this gives only the reason."""
        why = self.unlisted_reason
        return None if why is None else FOLDER_UNLISTED.format(why)

    def delete(self, name: str) -> bool:
        profile = self.get(name)
        if profile is None:
            return False
        # A path: `get` returns a profile GameProfile.load read, which set it.
        path = cast(Path, profile.path)
        path.unlink(missing_ok=True)
        sync_removal(path)
        return True
