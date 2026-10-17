"""What a G HUB import writes, and the words that show it.

`make_plan` works out the `import_plan.Plan` for the profiles chosen from a `ghub.Found`:
which of them come and under what name, the macros they bind and the programs each loads
for. A game profile that already holds what the import would write is left as it is
(`_already_there`). `details`, `summary`, `notes` and `found_as_json` say what the file
holds, for the window's list and `import-ghub --list`. Nothing here reads a file or writes
one.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import NamedTuple

from .config import (
    G_KEYS,
    MACRO_PREFIX,
    MOUSE_BUTTONS,
    MOUSE_DEFAULTS,
    MOUSE_LABELS,
    PER_KEY,
    PROFILE_IDS,
    counted,
    first_of,
    word_list,
)
from .ghub import (
    MAX_DOCUMENT_BYTES,
    SHOWN_DEVICES,
    Binding,
    Found,
    GhubError,
    GhubProfile,
    MacroRef,
    Taken,
    g_where,
    mouse_where,
)
from .import_plan import (
    CHOICES,
    CLASH_MACRO,
    CLASH_PROFILE,
    KEEP,
    REPLACE,
    SKIP,
    Clash,
    Existing,
    Plan,
    game_form,
    keepers_last,
    macro_form,
    macro_text,
    programs_for,
    step_lines,
)
from .library_files import slug
from .profiles import GameProfile

# -- the same as what is there ---------------------------------------------

def _already_there(profile: GhubProfile, have: str, found: Found, existing: Existing) -> bool:
    """Whether the game profile `have` holds what importing `profile` would write: the same
    G keys, MR layer, mouse buttons, lighting and macros (the owner's decision of 2026-09-24).

    Both sides are compared as the daemon keeps them: the body through
    `GameProfile.from_json`, as `import_profile` takes it, and `have` as the library read its
    file. A macro binding is compared by the macro's repeat mode and steps, not its name,
    which a clash can change.
    """
    game = existing.games.get(have)
    if game is None:
        return False
    def by_card(binding: Binding) -> str | None:
        # The card's place in profile.macros stands in for the macro's name,
        # which a clash can change; a card id can hold what a binding may not
        # (outer spaces, a control character, nothing).
        return (f"{MACRO_PREFIX}{profile.macros.index(binding.card)}"
                if isinstance(binding, MacroRef) else binding)

    imported = GameProfile.from_json(_body(profile, 1, by_card), profile.name)

    def ours(index: str) -> object:
        card = profile.macros[int(index)]
        return macro_form(found.macros[card].repeat, found.steps(card))

    def theirs(name: str) -> object:
        mine = existing.macro(name)
        return None if mine is None else existing.form_of(mine)

    return game_form(imported, ours) == game_form(game, theirs)


# -- the plan --------------------------------------------------------------

class _Picked(NamedTuple):
    """A profile that comes (`_pick_profiles`): the name it comes in under, whether it
    replaces yours, whether it loads by hand whatever `make_plan`'s `with_programs` says (a
    Keep both copy), and whether its clash is unanswered."""

    profile: GhubProfile
    name: str
    replace: bool
    by_hand: bool
    unanswered: bool


def chosen_names(found: Found, names: Iterable[str], everything: bool) -> list[str]:
    """The profiles a request names, as `found` names them. `everything` is
    every game profile, never the desktop's default, which replaces the M
    profiles and is only taken when named. Raises GhubError for a name the
    file does not have, or a second desktop default."""
    chosen = [p.name for p in found.games] if everything else []
    desktop: str | None = None
    for name in names:
        profile = found.get(name)
        if profile is None:
            raise GhubError(f"the file has no G HUB profile named {name!r}; "
                            f"--list shows what it has")
        if profile.desktop:
            if desktop not in (None, profile.name):
                raise GhubError(f"{desktop!r} and {profile.name!r} are both desktop defaults, "
                                f"which go onto the same M1, M2 and M3; name one")
            desktop = profile.name
        if profile.name not in chosen:
            chosen.append(profile.name)
    return chosen


def make_plan(found: Found, chosen: Iterable[str], existing: Existing,
              choices: Mapping[tuple[str, str], str] | None = None,
              default: str | None = None, with_programs: Iterable[str] = ()) -> Plan:
    """What importing the `chosen` profiles writes.

    `choices` answers each clash by (kind, imported name) and `default` the rest; with
    neither, a clash stays unresolved. A macro already there step for step is no clash and
    is used as it is; nor is a game profile that holds what the import would write
    (`_already_there`), which is left as it is, programs and all, and said. Only the macros
    the imported profiles use are imported.

    A game profile loads for G HUB's programs only when `with_programs` names it, the
    owner's decision of 2026-09-28: a name from someone else's file also matches the Linux
    program of that name. Any other is saved with none and loads by hand, and one replacing
    yours keeps your programs. A program another game profile already loads stays with it,
    a replace that takes G HUB's programs included (`programs_for`), and one that two
    imported profiles name goes to G HUB's active one, else the first
    (`import_plan._programs_left`).
    """
    choices = choices or {}
    plan = Plan()
    wanted = set(chosen)
    loaded = set(with_programs)
    # plan.clashes as a set: the window plans again at each answer, on its main thread.
    asked: set[Clash] = set()

    def choose(kind: str, name: str, have: str) -> str | None:
        # Asked once a name: two macros of one name share the answer. The choice is keyed
        # by the clash's kind and name, so its first sight says whether it is unresolved.
        clash = Clash(kind, name, have)
        choice = choices.get((kind, name), default)
        if clash not in asked:
            asked.add(clash)
            plan.clashes.append(clash)
            if choice not in CHOICES:
                plan.unresolved.append(clash)
        return choice if choice in CHOICES else None

    # The profiles first, for which of them come at all. One whose clash is
    # unanswered still counts, so its macros' clashes are asked in the same
    # pass; nothing is written until every one is answered.
    picked = _pick_profiles(found, wanted, existing, plan, choose)
    # Then the macros: a clash, or two of one name in the file, renames one,
    # and the bindings follow.
    names = _macro_names(found, picked, existing, plan, choose)

    def resolved(binding: Binding) -> str | None:
        if isinstance(binding, MacroRef):
            return f"{MACRO_PREFIX}{names[binding.card]}"
        return binding

    programs, keeping = _programs_by_profile(picked, loaded, existing)
    for profile, name, replace, _by_hand, unanswered in picked:
        if profile.desktop:
            for bank in PROFILE_IDS:
                # An M profile's name may be only spaces, and a game profile's
                # may not; the daemon keeps the M profile's own name anyway.
                body = _body(profile, bank, resolved,
                             name=existing.m_names.get(bank, "").strip())
                plan.m_profiles.append((bank, body, profile.lighting is not None))
        elif not unanswered:
            body = _body(profile, 1, resolved, name=name)
            body["processes"], said = programs.get(name, ([], []))
            yours = existing.games.get(existing.profile(profile.name) or "") if replace else None
            if name not in programs and yours is not None:
                plan.keep_programs(body, yours)
            plan.said += said
            plan.profiles.append((body, replace))
    keepers_last(plan, keeping)
    return plan


def _pick_profiles(found: Found, wanted: set[str], existing: Existing, plan: Plan,
                   choose: Callable[[str, str, str], str | None]) -> list[_Picked]:
    """The `wanted` profiles that come; one left out or already there is said in `plan`."""
    picked: list[_Picked] = []
    # The chosen names too, so a rename takes none a later profile comes in under.
    taken = Taken([*existing.profiles, *wanted])
    for profile in (p for p in found.profiles if p.name in wanted):
        name, replace, by_hand, unanswered = profile.name, False, False, False
        have = None if profile.desktop else existing.profile(profile.name)
        if have is not None and _already_there(profile, have, found, existing):
            plan.say_already(CLASH_PROFILE, profile.name, have)
            continue
        if have is not None:
            choice = choose(CLASH_PROFILE, profile.name, have)
            # None: no answer yet, and the clash is in plan.unresolved.
            unanswered = choice is None
            if choice == SKIP:
                plan.say_skipped(CLASH_PROFILE, profile.name)
                continue
            if choice == REPLACE:
                replace = True
            elif choice == KEEP:
                # Two profiles for one program: the scan takes the first
                # by filename, and the copy would take over from the one the
                # user already has. So the copy loads by hand.
                name, by_hand = taken.unique(profile.name), True
                plan.said.append(f'Game profile "{profile.name}" is imported as '
                                 f'"{name}", loaded by hand.')
        taken.add(name)
        picked.append(_Picked(profile, name, replace, by_hand, unanswered))
    return picked


def _macro_names(found: Found, picked: list[_Picked], existing: Existing, plan: Plan,
                 choose: Callable[[str, str, str], str | None]) -> dict[str, str]:
    """The name each macro card of the `picked` profiles is bound by; one
    written goes into `plan.macros`, and one renamed or left out is said."""
    names: dict[str, str] = {}
    #: The user's macros and those the import writes, whose names a new one may not take.
    macro_names = Taken(existing.macros)
    #: The names an earlier macro of the import is written or already there
    #: under, which a later one of the same name may not write over.
    given: set[str] = set()
    #: Of `given`, those of yours an earlier macro of the import writes over.
    replaced: set[str] = set()
    #: By slug, the spelling a clash was first asked under: two names the library takes for
    #: one share the answer, or Replace for one would write over yours after Skip for the
    #: other kept it.
    asked: dict[str, str] = {}
    for card in dict.fromkeys(c for p in picked for c in p.profile.macros):
        macro, steps = found.macros[card], found.steps(card)
        have = existing.macro(macro.name)
        # Not when an earlier macro of this import wrote over yours: a binding to it would
        # play the earlier one's steps.
        if (have is not None and slug(have) not in replaced
                and existing.same_macro(have, macro.repeat, steps)):
            names[card] = have
            given.add(slug(have))
            continue
        name = macro.name
        if slug(name) in given:
            name = macro_names.unique(macro.name)
        elif have is not None:
            choice = choose(CLASH_MACRO, asked.setdefault(slug(have), macro.name), have)
            if choice in (SKIP, None):
                names[card] = have
                skipped = (f'Macro "{macro.name}" is not imported; the bindings use your '
                           f'own "{have}".')
                if choice == SKIP and skipped not in plan.said:
                    plan.said.append(skipped)
                continue
            if choice == KEEP:
                name = macro_names.unique(macro.name)
        if name != macro.name:
            plan.said.append(f'Macro "{macro.name}" is imported as "{name}".')
        names[card] = name
        macro_names.add(name)
        given.add(slug(name))
        replace = have is not None and name == macro.name
        if replace:
            replaced.add(slug(name))
        plan.add_macro(name, macro.repeat, steps, replace)
    return names


def _programs_by_profile(picked: list[_Picked], loaded: set[str], existing: Existing
                         ) -> tuple[dict[str, tuple[list[str], list[str]]], dict[str, list[str]]]:
    """By the name it comes in under, each `loaded` game profile's programs and the sentences
    for those it goes without, a count in their place past MAX_DOCUMENT_BYTES characters of
    them, and the names that keep a program of yours, each with the keepers it waits for
    (`programs_for`). A Keep both copy, which loads by hand, and one whose clash is
    unanswered take none.
    One G HUB names no programs for is not loaded, as the window's Load for has it, so one
    that replaces yours keeps your programs (the owner's decision of 2026-09-29)."""
    # G HUB's active profile of an application first: the library would
    # load whichever sorts first by filename.
    return programs_for([
        (p.profile.processes, p.name, existing.profile(p.profile.name) if p.replace else None)
        for p in sorted(picked, key=lambda p: not p.profile.active)
        if (p.profile.name in loaded and p.profile.processes
            and not (p.by_hand or p.unanswered))], existing, MAX_DOCUMENT_BYTES)


def _body(profile: GhubProfile, bank: int, resolved: Callable[[Binding], str | None],
          name: str = "") -> dict:
    """A profiles/*.json body for one bank of a G HUB profile."""
    keys, shift = profile.banks.get(bank, ({}, {}))
    game = GameProfile(
        name=name or profile.name,
        bindings={k: resolved(keys.get(k)) for k in G_KEYS},
        shift={k: resolved(shift.get(k)) for k in G_KEYS},
        mouse=MOUSE_DEFAULTS | {button: resolved(binding) or "none"
                                for button, binding in profile.mouse.items()},
    )
    if profile.lighting is not None:
        game.mode, game.keys = PER_KEY, dict(profile.lighting.keys)
        game.base, game.background = profile.lighting.base, 0x000000
    return game.to_json()


# -- saying it -------------------------------------------------------------

def binding_text(binding: Binding, found: Found, label: str | None) -> str:
    if isinstance(binding, MacroRef):
        return f'macro "{found.macros[binding.card].name}"'
    if binding and label:
        return f'{binding} ("{label}")'
    return binding or "unbound"


def summary(profile: GhubProfile) -> str:
    """One line: what the profile carries."""
    bound = sum(1 for layer in profile.banks.values() for side in layer
                for value in side.values() if value is not None)
    return ", ".join([
        counted(bound, "G-key binding"),
        counted(len(profile.mouse), "mouse button"),
        "per-key lighting" if profile.lighting is not None else "no lighting",
        counted(len(profile.macros), "macro"),
    ])


def other_devices_text(found: Found) -> str:
    """"g305, g403", the file's other devices: at most SHOWN_DEVICES, then
    "and 380 more"."""
    return first_of(found.other_devices, SHOWN_DEVICES)


#: The programs the Loads note names before saying how many more: each of a file's
#: MAX_RECORDS profiles can carry one application's MAX_PROCESSES names, each up to
#: MAX_PROGRAM_BYTES, and the note names each twice.
SHOWN_PROGRAMS = 8


def _named(names: list[str]) -> str:
    """"a, b and c", or the first SHOWN_PROGRAMS of `names` and how many more."""
    return (first_of(names, SHOWN_PROGRAMS) if len(names) > SHOWN_PROGRAMS
            else word_list(names, "and"))


def _loads_for(programs: list[str], with_programs: bool) -> str:
    """The preview's note on what loads a profile: each .exe name, and the
    Linux program of that name without .exe, which matches too
    (apps.names_match), or by hand when the programs do not come over; at
    most SHOWN_PROGRAMS of each are named."""
    stems = [p[:-len(".exe")] for p in programs]
    one = len(programs) == 1
    linux = f"{'a Linux program' if one else 'Linux programs'} named {_named(stems)}"
    windows = f"{_named(programs)}, G HUB's Windows name{'' if one else 's'}"
    if with_programs:
        return (f"Loads for {windows}, and for {linux}. Under Proton the process name may "
                "differ; if the profile does not load, add the game's program while it runs.")
    return (f"Loads by hand: {windows}, {'is' if one else 'are'} not imported. Imported, "
            f"{'it' if one else 'they'} would also load the profile for {linux}.")


def notes(profile: GhubProfile, with_programs: bool = False) -> list[str]:
    """The profile's notes, the last saying what loads it: G HUB's programs
    when they come over (`make_plan`'s `with_programs`), else by hand."""
    if not profile.processes:
        return list(profile.notes)
    return [*profile.notes, _loads_for(profile.processes, with_programs)]


#: The heading of a profile's notes, whose last line says what loads it (notes).
NOTES = "Notes"


def details(profile: GhubProfile, found: Found, m_names: Mapping[int, str] | None = None,
            with_programs: bool = False) -> list[tuple[str, list[str]]]:
    """The preview of one profile, as (heading, lines) sections.
    `with_programs` is whether G HUB's programs for it come over."""
    sections: list[tuple[str, list[str]]] = []
    labels = profile.labels
    for bank, (keys, shift) in sorted(profile.banks.items()):
        at = bank if profile.desktop else None
        where = f"M{bank} " if profile.desktop else ""
        sections.append((f"{where}G keys", [
            f"{k}: {binding_text(keys.get(k), found, labels.get(g_where(k, False, at)))}"
            for k in G_KEYS]))
        if held := [f"{k}: {binding_text(shift[k], found, labels.get(g_where(k, True, at)))}"
                    for k in G_KEYS if shift.get(k)]:
            sections.append((f"{where}G keys with MR held", held))
    sections.append(("Mouse buttons", [
        f"{MOUSE_LABELS[b]}: "
        + (binding_text(profile.mouse[b], found, labels.get(mouse_where(b)))
           if b in profile.mouse else f"{MOUSE_DEFAULTS[b]} (Gearbench's default)")
        for b in MOUSE_BUTTONS]))
    if profile.lighting is not None:
        n = len(profile.lighting.keys)
        sections.append(("Lighting", [f"Per-key colors on {counted(n, 'key')}, the rest dark"]))
    else:
        light = f"Not imported: {profile.lighting_note}"
        sections.append(("Lighting", [light + (", the M profiles keep theirs" if profile.desktop
                                               else ", Gearbench's default color")]))
    if profile.macros:
        lines = []
        for card in profile.macros:
            macro, steps = found.macros[card], found.steps(card)
            lines.append(f"{macro.name}: {macro_text(macro.repeat, steps)}")
            lines += step_lines(steps)
        sections.append(("Macros", lines))
    if profile.desktop:
        names = m_names or {}
        which = word_list([f"M{i} \"{names.get(i, f'Profile {i}')}\"" for i in PROFILE_IDS], "and")
        what = ["The G keys", "the MR layer", "the mouse buttons"]
        if profile.lighting is not None:
            what.append("the lighting")
        sections.append(("Replaces", [f"{word_list(what, 'and')} of {which}"]))
    if profile.skipped:
        sections.append(("Not imported", list(profile.skipped)))
    if said := notes(profile, with_programs):
        sections.append((NOTES, said))
    return sections


def found_as_json(found: Found, with_programs: bool = False) -> list[dict]:
    """The profiles found, for `--json`."""
    return [{"name": p.name, "application": p.application, "ghub_name": p.ghub_name,
             "desktop": p.desktop, "processes": p.processes, "summary": summary(p),
             "macros": [found.macros[c].name for c in p.macros],
             "skipped": p.skipped, "notes": notes(p, with_programs)} for p in found.profiles]
