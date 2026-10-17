"""The profiles and macros pack: game lighting profiles and macros that come with
Gearbench, which nothing imports until asked.

The pack is its files, `profiles/<slug>.json` and `macros/<slug>.json` under `data_dir()`,
read as the user's own are (`profiles.Library`, `macros.MacroLibrary`). A file that fails
that read, a profile that is more than lighting, and a second file of one name are broken,
listed and never imported. `notes.json` holds a line to show under an item; MACRO_RISK,
one line above all the macros, takes the place of a note on each naming the games that
forbid them (the owner's decision of 2026-10-02). The plan is a lighting-only
`import_plan.Plan`, which the daemon writes through `import_profile_lighting` and
`macro_save`. A pack profile loads for the programs it names when the front end names it
in `make_plan`'s `with_programs`, as the window and the CLI do unless told otherwise, since
the pack chose them for its games (the owner's decision of 2026-09-28).
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from .config import (
    DIRECTION_TURNS,
    PER_KEY,
    ConfigError,
    both_ends,
    counted,
    format_color,
    kind_of,
    read_json,
    reason,
    unprefixed,
    unread_reason,
    word_list,
)
from .import_plan import (
    CLASH_MACRO,
    CLASH_PROFILE,
    CLASH_WORDS,
    REPLACE,
    SKIP,
    Clash,
    Existing,
    Plan,
    keepers_last,
    macro_text,
    programs_for,
    step_lines,
)
from .library_files import by_name, slug
from .lighting import ANIMATIONS
from .macros import Macro, MacroLibrary
from .profiles import LIGHTING_FIELDS, GameProfile, Library

#: Where the package installs the pack.
PACKAGED = Path("/usr/share/gearbench/pack")
PROFILES, MACROS = "profiles", "macros"
NOTES = "notes.json"
#: Shown above the pack's macros, in the window and in `import-pack --list`.
MACRO_RISK = "Use at your own risk: some games forbid macros."
#: A note is one line under a row.
MAX_NOTE = 240
#: A pack item's kind, which is its clash's.
PROFILE, MACRO = CLASH_PROFILE, CLASH_MACRO
#: The answers to a clash: Use the pack's, Keep mine.
CHOICES = (REPLACE, SKIP)


class PackError(ValueError):
    """The pack is not there, a name asked for is not in it, or a --replace name
    is not among the names given."""


@dataclass
class Pack:
    directory: Path
    profiles: list[GameProfile] = field(default_factory=list)
    macros: list[Macro] = field(default_factory=list)
    #: (file or folder under the pack, why) for each thing refused; notes.json can have several.
    broken: list[tuple[str, str]] = field(default_factory=list)
    #: (PROFILE or MACRO, slug of the name): the line shown under it.
    notes: dict[tuple[str, str], str] = field(default_factory=dict)

    def note(self, kind: str, name: str) -> str | None:
        return self.notes.get((kind, slug(name)))


def data_dir() -> Path:
    """The checkout's pack/ when running from one, else the package's."""
    here = Path(__file__).resolve().parents[1] / "pack"
    return here if (here / PROFILES).is_dir() else PACKAGED


def read_pack(directory: Path | None = None) -> Pack:
    """Every file in the pack, read as the libraries read the user's own.
    Raises PackError when there is no pack."""
    directory = data_dir() if directory is None else directory
    if not directory.is_dir():
        raise PackError(f"there is no pack at {directory}")
    pack = Pack(directory)
    profiles, macros = Library(directory / PROFILES), MacroLibrary(directory / MACROS)
    for profile in profiles.all():
        if why := _more_than_lighting(profile):
            # A path: `Library.all` returns profiles GameProfile.load read, which set it.
            pack.broken.append((f"{PROFILES}/{cast(Path, profile.path).name}", why))
        else:
            pack.profiles.append(profile)
    pack.macros = macros.all()
    # A folder a library could not list is one entry, under its own path in the pack. An
    # entry names its file or folder, so its reason leaves out the name and the path.
    for folder, library in ((PROFILES, profiles), (MACROS, macros)):
        pack.broken += ([(folder, f"cannot be listed: {library.unlisted_reason}")]
                        if library.unlisted_reason is not None
                        else [(f"{folder}/{name}", unread_reason(name, why))
                              for name, why in library.skipped])
    _read_notes(pack)
    # A note's name is quoted whole, and can be most of notes.json.
    pack.broken = sorted((file, both_ends(why)) for file, why in pack.broken)
    return pack


def _read_notes(pack: Pack) -> None:
    """Read notes.json, {"profiles": {name: line}, "macros": {name: line}}, into
    `pack.notes`, and the reason for each part of it refused into `broken`. A note may
    not hold a direction turn, which shows its text in another order than it is written:
    a pack's notes come from whoever made the pack (the owner's decision of 2026-10-07)."""
    path = pack.directory / NOTES
    if not path.is_file():
        return
    try:
        raw = read_json(path)
    except (ConfigError, OSError) as exc:
        pack.broken.append((NOTES, f"cannot be read: {unprefixed(reason(exc), path)}"))
        return
    if not isinstance(raw, dict) or set(raw) - {PROFILES, MACROS}:
        pack.broken.append((NOTES, f"must be an object of {PROFILES!r} and {MACROS!r}"))
        return
    for group, kind, items in ((PROFILES, PROFILE, pack.profiles), (MACROS, MACRO, pack.macros)):
        notes = raw.get(group, {})
        if not isinstance(notes, dict):
            pack.broken.append((NOTES, f"{group!r} must map names to lines"))
            continue
        names = {slug(item.name) for item in items}
        for name, line in notes.items():
            if (fault := _note_fault(line)) is not None:
                pack.broken.append((NOTES, f"the note on {name!r} {fault}"))
            elif slug(name) not in names:
                why = f"names no {CLASH_WORDS[kind].lower()} {name!r} the pack can import"
                pack.broken.append((NOTES, why))
            elif (kind, slug(name)) in pack.notes:
                why = f"a second note on the {CLASH_WORDS[kind].lower()} {name!r}"
                pack.broken.append((NOTES, why))
            else:
                pack.notes[kind, slug(name)] = line.strip()


#: What a note may not hold, by Unicode category: a note is one line, drawn as it is written.
_NOT_IN_A_NOTE = {"Cc": "a control character", "Cs": "a lone surrogate",
                  "Zl": "a line separator", "Zp": "a paragraph separator"}


def _note_fault(line: object) -> str | None:
    """Why a note of notes.json is refused, or None. A refused character is named by its
    code point: a direction turn and a lone surrogate draw nothing a reader could find."""
    if not isinstance(line, str):
        return f"must be text, not {kind_of(line)}"
    if not line.strip():
        return "is blank"
    if len(line) > MAX_NOTE:
        return f"is longer than {MAX_NOTE} characters"
    for ch in line:
        if ch in DIRECTION_TURNS:
            return f"holds U+{ord(ch):04X}, which turns the direction of the text after it"
        if (what := _NOT_IN_A_NOTE.get(unicodedata.category(ch))) is not None:
            return f"holds U+{ord(ch):04X}, {what}"
    return None


#: What a pack profile may not hold, by GameProfile field, and why it is refused: every
#: field but its name, its programs and LIGHTING_FIELDS.
REFUSED_FIELDS = {"bindings": "binds a G key", "shift": "binds a G key",
                  "mouse": "binds a mouse button",
                  "mouse_tuning": "holds DPI stages or a report rate"}


def _more_than_lighting(profile: GameProfile) -> str | None:
    """Why a pack profile is refused for holding more than its lighting."""
    plain = GameProfile(name=profile.name)
    return next((f"{why}; a pack profile is lighting only"
                 for name, why in REFUSED_FIELDS.items()
                 if getattr(profile, name) != getattr(plain, name)), None)


# -- saying what it holds --------------------------------------------------

def lighting_text(profile: GameProfile) -> str:
    """A profile's lighting in a few words."""
    if profile.effect in ANIMATIONS:
        return f"{profile.effect.capitalize()} effect"
    # Disabled darkens the board, and a per-key layout's keys still show over
    # it (keyboard.shown_color).
    dark = profile.effect == "disabled"
    if profile.mode == PER_KEY and (profile.keys or not dark):
        rest = "dark" if dark else format_color(profile.background)
        return f"Per-key colors on {counted(len(profile.keys), 'key')}, the rest {rest}"
    if dark:
        return "Lights off"
    return f"Static color {format_color(profile.base)}"


def programs_text(profile: GameProfile) -> str:
    """What loads a pack profile imported with its programs."""
    return (f"loads for {word_list(profile.processes, 'and')}" if profile.processes
            else "loads by hand")


def as_json(pack: Pack) -> dict:
    """The pack, for `import-pack --list --json`."""
    return {
        "directory": str(pack.directory),
        "profiles": [{"name": p.name, "processes": p.processes,
                      "lighting": lighting_text(p), "note": pack.note(PROFILE, p.name)}
                     for p in pack.profiles],
        "macros": [{"name": m.name, "repeat": m.repeat, "steps": m.steps,
                    "note": pack.note(MACRO, m.name)} for m in pack.macros],
        "macros_note": MACRO_RISK,
        "broken": [{"file": file, "error": why} for file, why in pack.broken],
    }


def listing(pack: Pack) -> list[str]:
    """`import-pack --list`: each profile and macro, a macro's steps under it,
    and the broken files."""
    lines = [f"Game profiles in {pack.directory / PROFILES}:"]
    for p in pack.profiles:
        lines.append(f"    {p.name}: {lighting_text(p)[:1].lower()}{lighting_text(p)[1:]}; "
                     f"{programs_text(p)}")
        if note := pack.note(PROFILE, p.name):
            lines.append(f"        {note}")
    if not pack.profiles:
        lines.append("    none")
    if pack.macros:
        lines.append(MACRO_RISK)
    lines.append(f"Macros in {pack.directory / MACROS}:")
    for macro in pack.macros:
        lines.append(f"    {macro.name}: {macro_text(macro.repeat, macro.steps)}")
        if note := pack.note(MACRO, macro.name):
            lines.append(f"        {note}")
        lines += ["    " + line for line in step_lines(macro.steps)]
    if not pack.macros:
        lines.append("    none")
    if pack.broken:
        lines.append("Broken, never imported:")
        lines += [f"    {file}: {why}" for file, why in pack.broken]
    return lines


# -- what the command line names -------------------------------------------

def _find(items: Iterable[GameProfile | Macro], name: str) -> str | None:
    found = by_name(items, name)
    return None if found is None else found.name


def chosen_names(pack: Pack, names: Iterable[str]) -> tuple[list[str], list[str]]:
    """The game profiles and macros `names` names, by the pack's own names,
    or all of each with none named. Raises PackError for a name the pack
    does not have."""
    names = list(names)
    if not names:
        return [p.name for p in pack.profiles], [m.name for m in pack.macros]
    profiles: set[str | None] = set()
    macros: set[str | None] = set()
    for name in names:
        profile, macro = _find(pack.profiles, name), _find(pack.macros, name)
        if profile is None and macro is None:
            raise PackError(f"the pack has no game profile or macro named {name!r}; "
                            f"--list shows what it has")
        profiles.add(profile)
        macros.add(macro)
    return ([p.name for p in pack.profiles if p.name in profiles],
            [m.name for m in pack.macros if m.name in macros])


def replace_choices(pack: Pack, names: Iterable[str],
                    given: Iterable[str]) -> dict[tuple[str, str], str]:
    """Use the pack's for each game profile and macro `names` names, as
    make_plan's `choices`. Raises PackError for a name the pack does not have,
    or one the names `given` leave out when any are given: only those are
    imported, so its choice would never be asked."""
    given_slugs = {slug(name) for name in given}
    choices: dict[tuple[str, str], str] = {}
    for name in names:
        profile, macro = _find(pack.profiles, name), _find(pack.macros, name)
        if profile is None and macro is None:
            raise PackError(f"--replace {name!r}: the pack has no game profile or macro "
                            f"of that name; --list shows what it has")
        # _find matches by slug, so a name finds what a given one finds exactly
        # when the two slugs are equal.
        if given_slugs and slug(name) not in given_slugs:
            raise PackError(f"--replace {name!r}: not among the names given; name it too")
        for kind, found in ((PROFILE, profile), (MACRO, macro)):
            if found is not None:
                choices[kind, found] = REPLACE
    return choices


# -- the plan --------------------------------------------------------------

def _same_lighting(profile: GameProfile, yours: GameProfile | None) -> bool:
    return yours is not None and all(getattr(profile, name) == getattr(yours, name)
                                     for name in LIGHTING_FIELDS)


def make_plan(pack: Pack, profiles: Iterable[str], macros: Iterable[str], existing: Existing,
              choices: Mapping[tuple[str, str], str] | None = None,
              default: str | None = None, *, with_programs: Iterable[str]) -> Plan:
    """What importing the named game profiles and macros writes.

    `choices` answers each clash by (kind, name), REPLACE or SKIP; `default` answers the
    rest, and with neither a clash stays unresolved. A profile `with_programs` names loads
    for the programs the pack gives it; one it leaves out, or one the pack gives none, keeps
    yours on a replace and loads by hand when new. `with_programs` has no default, so each
    front end says which. A profile of a name you have with the same lighting, whatever its
    bindings and programs, or a macro with the same steps and repeat, is already there: no
    clash. A program another of your profiles loads stays with it, as in the G HUB import
    (`import_plan.programs_for`).
    """
    choices = choices or {}
    plan = Plan(lighting_only=True)
    loaded = set(with_programs)

    def choose(kind: str, name: str, have: str) -> str | None:
        clash = Clash(kind, name, have)
        plan.clashes.append(clash)
        choice = choices.get((kind, name), default)
        if choice not in CHOICES:
            plan.unresolved.append(clash)
            return None
        if choice == SKIP:
            plan.say_skipped(kind, name)
        return choice

    #: (programs, name, the profile of yours it replaces) of each that loads for its own.
    taking: list[tuple[list[str], str, str | None]] = []
    wanted = set(profiles)
    for profile in (p for p in pack.profiles if p.name in wanted):
        have = existing.profile(profile.name)
        yours = existing.games.get(have) if have is not None else None
        if have is not None and _same_lighting(profile, yours):
            plan.say_already(PROFILE, profile.name, have)
            continue
        if have is not None and choose(PROFILE, profile.name, have) != REPLACE:
            continue
        body = profile.to_json()
        # One the pack names no programs for is not loaded, as the window's
        # Load for and the G HUB import have it: a replace keeps your programs.
        if profile.name in loaded and profile.processes:
            taking.append((profile.processes, profile.name, have))
        elif yours is not None:
            plan.keep_programs(body, yours)
        else:
            body["processes"] = []
        plan.profiles.append((body, have is not None))
    # Once every profile is chosen: a replace keeps a program of yours that
    # another profile of the pack goes without for it.
    programs, keeping = programs_for(taking, existing)
    for body, _replace in plan.profiles:
        if body["name"] in programs:
            body["processes"], said = programs[body["name"]]
            plan.said += said
    keepers_last(plan, keeping)
    wanted = set(macros)
    for macro in (m for m in pack.macros if m.name in wanted):
        have = existing.macro(macro.name)
        if have is not None and existing.same_macro(have, macro.repeat, macro.steps):
            plan.say_already(MACRO, macro.name, have)
            continue
        if have is not None and choose(MACRO, macro.name, have) != REPLACE:
            continue
        plan.add_macro(macro.name, macro.repeat, macro.steps, have is not None)
    return plan
