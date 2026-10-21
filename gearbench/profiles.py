"""Game profiles: a library of named layouts, one JSON file each.

A game profile holds its lighting for each keyboard model, G-key bindings and, per mouse
model, the mouse's bindings and its own DPI stages and report rate. It is an overlay on the
active M profile, which keeps its own defaults. One file per profile, so a game's layout can
be copied or shared on its own, and a bad file costs one profile. Version 2 keeps the lighting
under `lighting`, by keyboard model; version 1 kept one at the top of the file, read as the
G915's (config.V1_KEYBOARD). Version 3 keeps the mouse bindings by mouse model; versions 1 and
2 kept one set, read as the G502 X PLUS's (config.V2_MOUSE).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from .apps import MAX_PROGRAM_BYTES
from .config import (
    BINDABLE_KEYS,
    FIRST_VERSION,
    KEYBOARD_MODEL,
    PER_KEY,
    RATE_CHOICES,
    REPORT_RATES,
    STATIC,
    V1_KEYBOARD,
    VERSION,
    ConfigError,
    FileUnreadable,
    Lighting,
    Profile,
    SkipWarnings,
    backup_dir,
    by_model,
    changed_on_disk,
    config_dir,
    entry_under,
    expect,
    file_stamp,
    format_color,
    from_file,
    g_key_layer,
    keep_old_version,
    keep_under,
    layer_binding,
    layer_json,
    mouse_json,
    read_mouse_bindings,
    refuse_controls,
    sync_removal,
    write_json,
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
from .onboard import DPI_HIGHEST, DPI_LOWEST, DPI_STAGES, dpi_fits
from .wording import whole, written

# STATIC and PER_KEY are config.py's (an M profile has a mode too), re-exported; so are its
# REPORT_RATES, the rates a game profile can hold, those onboard profile 1 can store, and
# apps.py's MAX_PROGRAM_BYTES, the longest program name a profile holds.
__all__ = ["FOLDER_UNLISTED", "MAX_PROCESSES", "MAX_PROGRAM_BYTES", "PER_KEY", "REPORT_RATES",
           "STATIC", "GameProfile", "Library", "MouseTuning", "check_programs", "profiles_dir"]

#: The most programs a game profile may load for: the daemon's scan compares each with every
#: running program every five seconds, on the thread that carries the G keys and macros.
MAX_PROCESSES = 64

#: Why a file in the profiles folder was not read while the folder could not be listed, by
#: the OSError's reason in words (Library.why_unread).
FOLDER_UNLISTED = "its folder cannot be listed: {}"


def profiles_dir() -> Path:
    return config_dir() / "profiles"


def profile_backup(path: Path) -> Callable[[int], Path]:
    """Where a game profile's bytes in an older format are kept (config.keep_old_version),
    under its own file name, in a folder for each format."""
    return lambda version: backup_dir() / f"profiles.v{version}" / path.name


TUNING_FIELDS = ("dpi_stages", "default", "shift", "report_rate")


@dataclass(frozen=True)
class MouseTuning:
    """A game profile's DPI stages and report rate for one mouse model, in force while the
    profile is loaded and the mouse is in HOST mode. Stages left None fall to onboard profile
    1's, a rate left None to the rate in force without a game profile (Dpi.rate_for);
    `default` and `shift` need `dpi_stages`."""

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
            raise ConfigError(f"{where}.report_rate: must be one of {RATE_CHOICES} Hz, "
                              f"not {written(rate)}")
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
    if raw is not None:
        expect(raw, dict, where, "an object of mouse product ids")
    tunings = by_model(raw, where, MouseTuning.from_json, "a mouse product id like '4099'",
                       "mouse")
    return {product: tuning for product, tuning in tunings.items() if not tuning.empty}


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


def game_lighting(data: object, where: str) -> Lighting:
    """One keyboard model's lighting as a game profile's file writes it: config.Lighting's
    fields with its Static color as `base`, and no `params`, which only an M profile has."""
    return Lighting.from_json(data, where, color_field="base", with_params=False)


def lighting_json(entry: Lighting) -> dict:
    """`entry` as a game profile's file writes it (game_lighting), its keys sorted."""
    return {
        "mode": entry.mode,
        "effect": entry.effect,
        "speeds": dict(entry.speeds),
        "direction": entry.direction,
        "base": format_color(entry.color),
        "background": format_color(entry.background),
        "keys": {k: format_color(v) for k, v in sorted(entry.keys.items())},
    }


@dataclass
class GameProfile:
    name: str
    #: Process names that auto-load this profile. Empty means manual only.
    processes: list[str] = field(default_factory=list)
    #: Its lighting by keyboard model, as an M profile's (config.Profile.lighting), no `params`.
    lighting: dict[str, Lighting] = field(default_factory=dict)
    #: G-key bindings by config.BINDABLE_KEYS' names, None meaning unbound.
    bindings: dict[str, str | None] = field(default_factory=lambda: dict.fromkeys(BINDABLE_KEYS))
    shift: dict[str, str | None] = field(default_factory=lambda: dict.fromkeys(BINDABLE_KEYS))
    #: Its mouse bindings by mouse model, each by button name, as an M profile's
    #: (config.Profile.mouse).
    mouse: dict[str, dict[str, str]] = field(default_factory=dict)
    #: Its own DPI stages and report rate by mouse product id; a mouse with none runs profile 1's
    #: stages and the rate in force without a game profile (Dpi.rate_for).
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

    def binding(self, key: str, shifted: bool) -> str | None:
        """A G key's binding, the MR layer's when `shifted` and it has one, as an M profile's
        (config.layer_binding)."""
        return layer_binding(self.bindings, self.shift, key, shifted)

    def tuning(self, keys: list[str]) -> MouseTuning | None:
        """Its own stages and rate for the mouse `keys` name, its own product
        id first and then the same model's on its other route
        (daemon.mice.attached.dpi_keys), or None (config.entry_under)."""
        return entry_under(self.mouse_tuning, keys)

    def set_tuning(self, keys: list[str], tuning: MouseTuning) -> None:
        """Keep `tuning` under the first of `keys` and drop the others, the
        same mouse on its other route (config.keep_under); an empty one drops them all."""
        if tuning.empty:
            for key in keys:
                self.mouse_tuning.pop(key, None)
        else:
            keep_under(self.mouse_tuning, keys, tuning)

    def to_json(self) -> dict:
        return {
            "version": VERSION,
            "name": self.name,
            "processes": list(self.processes),
            "lighting": {model: lighting_json(entry)
                         for model, entry in sorted(self.lighting.items())},
            "bindings": layer_json(self.bindings),
            "shift": layer_json(self.shift),
            "mouse": mouse_json(self.mouse),
            "mouse_tuning": {k: t.to_json() for k, t in sorted(self.mouse_tuning.items())},
        }

    @classmethod
    def from_json(cls, data: object, where: str) -> GameProfile:
        """A game profile from its file's JSON: version 1's lighting, at the top of the file
        with its defaults for any field it lacks, is V1_KEYBOARD's, and an older format's mouse
        bindings are config.read_mouse_bindings'."""
        if not isinstance(data, dict):
            raise ConfigError(f"{where}: profile must be a JSON object")
        version = check_version(data, where, VERSION, FIRST_VERSION)
        if version == FIRST_VERSION:
            lighting = {V1_KEYBOARD: game_lighting(data, where)}
        else:
            lighting = by_model(data.get("lighting"), f"{where}.lighting", game_lighting,
                                KEYBOARD_MODEL, "keyboard")
        # Only null is the default, as in config.json: false, 0 or "" is a wrong type, which
        # read as the default would be saved over the hand edit.
        processes = data.get("processes")
        if processes is None:
            processes = []
        check_programs(processes, f"{where}.processes")
        name = read_name(data, where, "game profile")
        return cls(
            name=name,
            processes=[p.strip() for p in processes],
            lighting=lighting,
            bindings=g_key_layer(data.get("bindings"), f"{where}.bindings"),
            shift=g_key_layer(data.get("shift"), f"{where}.shift"),
            mouse=read_mouse_bindings(data.get("mouse"), f"{where}.mouse", version),
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
        keep_old_version(target, profile_backup(target))
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
            # Before the write: a rename in place writes over the file it leaves, and one to
            # another file name deletes it.
            keep_old_version(old, profile_backup(old))
            write_json(target, self.to_json(), ".profile-")
        except BaseException:
            self.name = was
            raise
        self.path, self.disk_stamp = target, file_stamp(target)
        if old != target:
            old.unlink(missing_ok=True)
            sync_removal(old)
        return target


def layer_in_force(overlay: GameProfile | None, profile: Profile) -> GameProfile | Profile:
    """The layer in force: the loaded game profile `overlay`, else the active M profile."""
    return overlay if overlay is not None else profile


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

    def why_unread(self, file: str | None) -> str | None:
        """Why the last `all()` did not read `file`, in words: its entry in `unread()`, else,
        while the folder could not be listed, FOLDER_UNLISTED's; else None, as for a file it
        read, one shadowed or one not there. A message that names the file holds the folder's
        path, so the folder's part gives only the reason."""
        why = None if file is None else dict(self.unread()).get(file)
        if why is None and self.unlisted_reason is not None:
            why = FOLDER_UNLISTED.format(self.unlisted_reason)
        return why

    def delete(self, name: str) -> bool:
        profile = self.get(name)
        if profile is None:
            return False
        # A path: `get` returns a profile GameProfile.load read, which set it.
        path = cast(Path, profile.path)
        path.unlink(missing_ok=True)
        sync_removal(path)
        return True
