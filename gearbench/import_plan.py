"""The plan of an import, which the G HUB import and the pack share.

`Existing` is what Gearbench has now, read from the libraries and config.json. A `Plan`
holds what an import would write, its clashes with what is there and the sentences that say
what it leaves out. `ghub_plan.make_plan` and `pack.make_plan` each build one, with
`programs_for` and `keepers_last` for a program another game profile already loads.
`requests` turns a plan into control socket commands, the one place they are built, so the
daemon writes every file and its hand-edit protection covers an import; `held_back` and
`reply_lines` are for the front end that sends them. Nothing here reads G HUB's settings or
the pack, and nothing here writes a profile, macro or setting.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field
from functools import cached_property
from itertools import groupby
from typing import cast

from .apps import wanted_forms
from .config import MOUSE_ACTIONS, Config, ConfigError, macro_name
from .keyboard import own_entry, own_named_keys
from .library_files import slug
from .macros import KEY_STEPS, MacroLibrary, macro_summary, step_summary
from .mouse import MODEL_IDS, MODEL_OF, bindings_of
from .profiles import MAX_PROCESSES, GameProfile, Library
from .uinput import MODIFIER_KINDS, parse_binding
from .wording import counted, without_controls, word_list

#: What to do with an imported name Gearbench already has.
REPLACE, KEEP, SKIP = "replace", "keep", "skip"
CHOICES = (REPLACE, KEEP, SKIP)


# -- what the user has already ---------------------------------------------

@dataclass
class Existing:
    """The game profiles, macros and M profile names Gearbench has now."""

    profiles: list[str] = field(default_factory=list)
    #: Macro name to (repeat, steps).
    macros: dict[str, tuple[str, list[dict]]] = field(default_factory=dict)
    m_names: dict[int, str] = field(default_factory=dict)
    #: The game profiles themselves by name, as the library reads their
    #: files, for telling an import from one already there. A name in
    #: `profiles` with none here is a clash whatever it holds.
    games: dict[str, GameProfile] = field(default_factory=dict)
    #: By name, each macro of yours as `form_of` worked it out.
    _forms: dict[str, tuple[str, list[dict]]] = field(default_factory=dict, init=False,
                                                       repr=False, compare=False)

    def profile(self, name: str) -> str | None:
        return self._profile_slugs.get(slug(name))

    def macro(self, name: str) -> str | None:
        return self._macro_slugs.get(slug(name))

    def same_macro(self, have: str, repeat: str, steps: list[dict]) -> bool:
        """Whether your macro `have` does what `repeat` and `steps` do."""
        return self.form_of(have) == macro_form(repeat, steps)

    def form_of(self, have: str) -> tuple[str, list[dict]]:
        """Your macro `have` as `macro_form` has it, worked out once: a file can bind
        thousands of cards of one name, each compared with yours."""
        if have not in self._forms:
            self._forms[have] = macro_form(*self.macros[have])
        return self._forms[have]

    # By slug, the first name of each, as a scan in order finds it: make_plan looks up every
    # imported name on the window's main thread. Cached: nothing changes an Existing once built.

    @cached_property
    def _profile_slugs(self) -> dict[str, str]:
        return _first_by_slug(self.profiles)

    @cached_property
    def _macro_slugs(self) -> dict[str, str]:
        return _first_by_slug(self.macros)


def _first_by_slug(names: Iterable[str]) -> dict[str, str]:
    first: dict[str, str] = {}
    for name in names:
        first.setdefault(slug(name), name)
    return first


def existing_from_disk() -> Existing:
    """Read the libraries and config.json, never write them. The daemon
    reads the same files through the same code, so a clash seen here is the
    one it would refuse."""
    try:
        config = Config.load()
    except (ConfigError, OSError):
        config = Config()
    m_names = {i: p.name for i, p in config.profiles.items()}
    # One per name: Library.all keeps one file of a name and lists any
    # other as skipped, as the daemon's scan reads it.
    games = {game.name: game for game in Library().all()}
    # MacroLibrary.all keeps one file per name, the one get plays.
    macros = {macro.name: (macro.repeat, macro.steps) for macro in MacroLibrary().all()}
    return Existing(
        profiles=list(games),
        macros=macros,
        m_names=m_names,
        games=games,
    )


# -- the same as what is there ---------------------------------------------

def _keys(spec: str) -> tuple[tuple[int, ...], int]:
    """A key spec as the keys it sends, so `Shift+Q`, `shift+q` and `SHIFT + q` are one
    binding, and so are `ctrl+shift+q` and `shift+ctrl+q`. The keys go down in the spec's
    order (uinput.VirtualKeyboard.tap), so only modifiers side by side ahead of the last key
    are sorted: `a+ctrl+b` sends A and then Ctrl+B, `ctrl+a+b` Ctrl+A and Ctrl+B, and
    `shift+ctrl` is not `ctrl+shift`, as the last key is left where it is. Every spec that
    comes here was checked the same way when it was read or converted."""
    before, key = parse_binding(spec)
    return tuple(code for modifier, run in groupby(before, MODIFIER_KINDS.__contains__)
                 for code in (sorted(run) if modifier else run)), key


def macro_form(repeat: str, steps: list[dict]) -> tuple[str, list[dict]]:
    """A macro as what it does: its repeat mode and its steps, a key step's
    keys as `_keys` has them."""
    return repeat, [dict(step, value=_keys(step["value"])) if step.get("type") in KEY_STEPS
                    else step for step in steps]


def game_form(game: GameProfile, macro: Callable[[str], object],
              models: Collection[str]) -> tuple:
    """What a game profile does, for telling an import from one already
    there: its G keys, MR layer and each mouse model's buttons as they are
    in force, a macro binding by what `macro` says the macro it names does,
    and the lighting as it is saved for each keyboard model of `models`,
    those the import has lighting for, which a replace writes. Not its name,
    and not its programs."""
    def does(spec: str | None, actions: tuple[str, ...] = ()) -> object:
        if spec is None or spec in actions:
            return spec
        name = macro_name(spec)
        return ("macro", macro(name)) if name is not None else _keys(spec)

    # A model with no entry, as Restore defaults leaves it, runs its defaults, and the import
    # writes each model it has in full; an id no model has keeps its entry.
    mice = {model: bindings_of(game.mouse, model) for model in MODEL_IDS}
    mice |= {key: entry for key, entry in game.mouse.items() if int(key, 16) not in MODEL_OF}
    return (
        {k: does(v) for k, v in game.bindings.items()},
        {k: does(v) for k, v in game.shift.items()},
        {model: {k: does(v, MOUSE_ACTIONS) for k, v in entry.items()}
         for model, entry in mice.items()},
        [own_entry(game.lighting, model) for model in sorted(models)],
    )


# -- the plan --------------------------------------------------------------

#: A clash's kind, and how the review page, the CLI, a plan's Skip and Already
#: sentences and the faults the pack finds in its notes name it. `ghub.MACRO` is
#: an Action's kind.
CLASH_PROFILE, CLASH_MACRO = "profile", "macro"
CLASH_WORDS = {CLASH_PROFILE: "Game profile", CLASH_MACRO: "Macro"}


class PlanError(ValueError):
    """A plan cannot be sent as it is: a clash in it has no answer.

    Control characters are stripped, as GhubError strips them: the text names what a file
    held and goes to terminals."""

    def __init__(self, text: str) -> None:
        super().__init__(without_controls(text))


@dataclass(frozen=True)
class Clash:
    kind: str           # CLASH_PROFILE or CLASH_MACRO
    #: The imported name, which the user's choice is keyed by.
    name: str
    #: The name Gearbench has it under.
    existing: str


@dataclass
class Plan:
    #: macro_save bodies: name, repeat, steps, and replace: whether it writes
    #: over one of yours (without it the save is new-only).
    macros: list[dict] = field(default_factory=list)
    #: (profiles/*.json body, whether it writes over one of the same name).
    profiles: list[tuple[dict, bool]] = field(default_factory=list)
    #: (M profile, body, whether its lighting is imported too, the keys it writes: the G915's
    #: and those of another keyboard the file has slots of, so the M profile keeps the others).
    m_profiles: list[tuple[int, dict, bool, list[str]]] = field(default_factory=list)
    clashes: list[Clash] = field(default_factory=list)
    #: Clashes nobody has chosen for yet. Nothing may be written while any is.
    unresolved: list[Clash] = field(default_factory=list)
    #: The sentences on what the import renames, leaves out or finds already there.
    said: list[str] = field(default_factory=list)
    #: The sentences of `said` for the game profiles and macros already
    #: there, which hold before any clash is answered.
    already: list[str] = field(default_factory=list)
    #: The game profiles that replace one of yours and keep its programs,
    #: none included: the daemon keeps what it has when it writes.
    kept: list[str] = field(default_factory=list)
    #: The pack's: a game profile replacing yours writes its name,
    #: programs and lighting only, and leaves your bindings as they are.
    lighting_only: bool = False

    @property
    def empty(self) -> bool:
        return not (self.macros or self.profiles or self.m_profiles)

    # What both imports' planners write the same way.

    def say_already(self, kind: str, name: str, have: str) -> None:
        """Say the game profile or macro named `name`, of clash `kind`, is already there, as
        your `have`."""
        self.already.append(f'{CLASH_WORDS[kind]} "{name}" is already in Gearbench'
                            + ("." if have == name else f', as "{have}".'))
        self.said.append(self.already[-1])

    def say_skipped(self, kind: str, name: str) -> None:
        """Say the clash of `kind` over the imported `name` was answered Skip."""
        self.said.append(f'{CLASH_WORDS[kind]} "{name}" is not imported; yours is kept.')

    def keep_programs(self, body: dict, yours: GameProfile) -> None:
        """The profile `body` replaces `yours` and takes no programs: it keeps yours."""
        body["processes"] = list(yours.processes)
        self.kept.append(body["name"])

    def add_macro(self, name: str, repeat: str, steps: list[dict], replace: bool) -> None:
        self.macros.append({"name": name, "repeat": repeat,
                            "steps": [dict(s) for s in steps], "replace": replace})


def keepers_last(plan: Plan, keeping: Mapping[str, list[str]]) -> None:
    """Sort the game profiles that keep a program of yours (`programs_for`)
    after the rest, and each after the keepers `keeping` names for it, sent in
    that order: the daemon writes the program only once no other profile loads
    it, and a profile of yours the import replaces without it may load it
    until then. Keepers that wait for each other cannot all keep theirs."""
    order: dict[str, int] = {}
    seen: set[str] = set()
    for body, _replace in plan.profiles:
        if body["name"] not in keeping or body["name"] in seen:
            continue
        # Depth first without recursion: a chain of keepers can be longer than the stack.
        seen.add(body["name"])
        stack = [(body["name"], iter(keeping[body["name"]]))]
        while stack:
            name, waits_for = stack[-1]
            first = next((other for other in waits_for if other not in seen), None)
            if first is None:
                stack.pop()
                order[name] = len(order)
            else:
                seen.add(first)
                stack.append((first, iter(keeping[first])))
    plan.profiles.sort(key=lambda entry: order.get(entry[0]["name"], -1))


def programs_for(taking: list[tuple[list[str], str, str | None]], existing: Existing,
                 limit: int | None = None
                 ) -> tuple[dict[str, tuple[list[str], list[str]]], dict[str, list[str]]]:
    """By the name it comes in under, the programs of each game profile in `taking` that it
    loads for, and the sentences for those it goes without (`_programs_left`). Each of
    `taking` is (its programs, that name, the profile of yours it replaces or None), in the
    order they take programs.

    A program the import goes without because one of your profiles loads it stays with that
    profile, one the import leaves alone before one it replaces, even when the import
    replaces that profile with programs of its own: the replace keeps each such program
    after the import's, and its name is a key of the mapping returned, with the other such
    replaces it waits for: those that replace a profile of yours loading a program it keeps
    (`keepers_last`). The import's programs past MAX_PROCESSES with those are left out (the
    owner's decision of 2026-10-02), and said unless one it takes or keeps loads for them too
    (Claude Code's call of 2026-10-04, confirmed by the owner's decision of 2026-10-06).

    Past `limit` characters of those sentences, each profile from the one that passes it
    has one in place of its own (`_said_within`)."""
    owners, given = _Programs(), _Programs()
    if taking:
        # Those the import leaves alone first: a program one of them loads stays with it, as
        # the daemon writes a replace's program only while no other profile loads it.
        replaced = {replacing for _programs, _name, replacing in taking}
        for other, game in sorted(existing.games.items(), key=lambda item: item[0] in replaced):
            for program in game.processes:
                owners.add(program, other)
    # By the profile of yours each stays with, the forms of the programs
    # the import goes without for it.
    held: dict[str, set[str]] = {}
    for programs, _name, replacing in taking:
        for program in programs:
            owner = owners.first(program, but=replacing)
            if owner is not None:
                held.setdefault(owner, set()).update(wanted_forms(program))
    out: dict[str, tuple[list[str], list[str]]] = {}
    #: By each replace that keeps a program of yours, the wanted forms of those it keeps.
    keeping: dict[str, set[str]] = {}
    # By wanted form, the keepers whose profile of yours loads it: until such a replace is
    # written, the daemon leaves that program out of any other profile.
    loaded_by: dict[str, list[str]] = {}
    left_to_say = limit or 0
    for programs, name, replacing in taking:
        keep = [p for p in existing.games[replacing].processes
                if not held[replacing].isdisjoint(wanted_forms(p))] if replacing in held else []
        room = MAX_PROCESSES
        while True:
            left, cut, said = _programs_left(programs, name, owners, replacing, given, room)
            kept = _not_loaded(keep, left)
            if len(left) + len(kept) <= MAX_PROCESSES:
                break
            # Cut to MAX_PROCESSES less the number kept: fewer of the import's can leave
            # more of yours to keep, so again until they fit.
            room = MAX_PROCESSES - len(kept)
        # A cut program the profile still loads for, through one it takes or keeps, is not said.
        loads = set().union(*map(wanted_forms, [*left, *kept]))
        said += [f'Game profile "{name}" is imported without {program}: with the programs '
                 f'of yours it keeps, it would load for more than {MAX_PROCESSES}.'
                 for program in cut if not wanted_forms(program) <= loads]
        if limit is not None:
            said, left_to_say = _said_within(said, name, left_to_say, limit)
        for program in left:
            given.add(program, name)
        if kept:
            keeping[name] = set().union(*map(wanted_forms, kept))
            # A name: `kept` is of `keep`, which is empty unless `replacing` is in `held`.
            yours = existing.games[cast(str, replacing)]
            for form in set().union(*map(wanted_forms, yours.processes)):
                loaded_by.setdefault(form, []).append(name)
        out[name] = [*left, *kept], said
    # Sorted: a set of text iterates in an order PYTHONHASHSEED picks, and the keepers' order
    # is the plan's.
    return out, {name: list(dict.fromkeys(other for form in sorted(forms)
                                          for other in loaded_by.get(form, ()) if other != name))
                 for name, forms in keeping.items()}


def _said_within(said: list[str], name: str, left: int, limit: int) -> tuple[list[str], int]:
    """The sentences on the programs game profile `name` goes without, `said`, or one saying
    how many once they take `left`, what is left of `limit` characters of such sentences,
    below nothing; and what is left after them."""
    left -= sum(map(len, said))
    if left >= 0 or not said:
        return said, left
    # Every profile of one application names its programs again, so a crafted file can make
    # many times its size in these, as in ghub's Not imported text.
    return [(f'Game profile "{name}" is imported without {counted(len(said), "program")}, '
             f"not listed: the import's lines about programs are over "
             f"{limit // 1_000_000} million characters.")], left


def _not_loaded(programs: list[str], loads: list[str]) -> list[str]:
    """Each of `programs` with a wanted form that neither `loads` nor the programs taken
    before it here have (names_match's forms)."""
    have = set().union(*map(wanted_forms, loads))
    out = []
    for program in programs:
        if not wanted_forms(program) <= have:
            out.append(program)
            have |= wanted_forms(program)
    return out


class _Programs:
    """Programs, each with the game profile that has it, found by the forms
    names_match compares (apps.wanted_forms), so a lookup costs the
    program's forms rather than every program there: make_plan runs on the
    window's main thread."""

    def __init__(self) -> None:
        #: Each of a program's wanted forms to (when added, profile), in the
        #: order added.
        self._wanted: dict[str, list[tuple[int, str]]] = {}
        self._added = 0

    def add(self, program: str, profile: str) -> None:
        entry = (self._added, profile)
        self._added += 1
        for form in wanted_forms(program):
            self._wanted.setdefault(form, []).append(entry)

    def first(self, program: str, but: str | None = None) -> str | None:
        """The profile, other than `but`, added first with a program that some
        running name would load both it and `program` for: their wanted forms
        meet. Either way round (names_match) is not enough: two names over 15
        bytes that share their first 15 match neither way, and the 15-byte
        comm Wine gives either one loads both."""
        found = [self._wanted.get(form, []) for form in wanted_forms(program)]
        firsts = [next((e for e in entries if e[1] != but), None) for entries in found]
        return min((e for e in firsts if e is not None), default=(0, None))[1]


def _programs_left(programs: list[str], name: str, owners: _Programs,
                   replacing: str | None, given: _Programs,
                   room: int) -> tuple[list[str], list[str], list[str]]:
    """`programs` less each that a game profile other than `replacing` already loads
    (`owners`), or that `given` gave another profile of the import, with a sentence for
    each: the scan takes the first match by filename, so the import would take the
    program from the user's profile or load two of its own for it. Each past the first
    `room` is cut, returned apart for `programs_for` to say: the replaced profile's
    programs that it keeps take the rest."""
    left: list[str] = []
    cut: list[str] = []
    said: list[str] = []
    for program in programs:
        owner = owners.first(program, but=replacing)
        taker = given.first(program)
        if owner is not None:
            said.append(f'Game profile "{name}" is imported without {program}, which stays '
                        f'with "{owner}".')
        elif taker is not None:
            said.append(f'Game profile "{name}" is imported without {program}, which goes '
                        f'to "{taker}".')
        elif len(left) == room:
            cut.append(program)
        else:
            left.append(program)
    return left, cut, said


def requests(plan: Plan) -> list[dict]:
    """The control socket commands that carry out `plan`, in order: macros
    before the profiles that bind them. Raises PlanError while a clash is
    unresolved."""
    if plan.unresolved:
        raise PlanError("choose what to do with " + ", ".join(
            f'{c.kind} "{c.name}"' for c in plan.unresolved) + " first")
    # One that replaces nothing is new-only: a macro of its name saved since
    # the plan was made stays.
    out: list[dict] = [{"command": "macro_save", "value": m["name"], "repeat": m["repeat"],
                        "steps": m["steps"], **({} if m.get("replace") else {"new": True})}
                       for m in plan.macros]
    # The pack's under a name of its own, which a daemon from before the pack
    # refuses rather than replace the whole profile.
    command = ({"command": "import_profile_lighting"} if plan.lighting_only
               else {"command": "import_profile"})
    # One that keeps your programs keeps those the profile has when written,
    # not the list read here. The body's list is what the daemon writes when
    # no profile of that name is left, and what one without the flag writes,
    # less any program another profile loads by then.
    out += [{**command, "profile": body, "replace": replace,
             **({"keep_processes": True} if body["name"] in plan.kept else {})}
            for body, replace in plan.profiles]
    out += [{"command": "import_m_profile", "value": bank, "profile": body,
             "lighting": lighting, "keys": keys} for bank, body, lighting, keys in plan.m_profiles]
    return out


# -- saying it -------------------------------------------------------------

def macro_text(repeat: str, steps: list[dict]) -> str:
    """A macro in a few words, as the window's macro rows have it (macro_summary)."""
    return macro_summary(len(steps), repeat)


def step_lines(steps: list[dict]) -> list[str]:
    """A macro's steps, one indented line each, as the macro editor names
    them: an import shows what a macro types before any key can run it."""
    return [f"    {title}: {value}" for title, value in map(step_summary, steps)]


def plan_lines(plan: Plan) -> list[str]:
    """What a plan writes, one line each and a macro's steps under it, for a
    dry run and the review."""
    lines = []
    for macro in plan.macros:
        lines.append(f"macro \"{macro['name']}\", {counted(len(macro['steps']), 'step')}"
                     + (", replacing yours" if macro.get("replace") else ""))
        lines += step_lines(macro["steps"])
    over = ", replacing your lighting" if plan.lighting_only else ", replacing yours"
    lines += [f"game profile \"{body['name']}\"" + (over if replace else "")
              + (", loads by hand" if not body["processes"]
                 else ", keeps your programs" if body["name"] in plan.kept
                 else f", loads for {word_list(body['processes'], 'and')}")
              for body, replace in plan.profiles]
    lines += [f"M{bank}: " + word_list(m_profile_parts(keys, bool(body["mouse"]), light), "and")
              for bank, body, light, keys in plan.m_profiles]
    return lines


def m_profile_parts(keys: Collection[str], mouse: bool, lighting: bool) -> list[str]:
    """What an import writes on an M profile, for a line. The G915's G keys are always written,
    and a keyboard that binds under their names comes in with them; a keyboard with names of its
    own (own_named_keys), or a mouse model, the import has nothing for keeps the M profile's.
    G HUB's lighting is read for the G915 alone (ghub_plan)."""
    return ["G keys", *own_named_keys(keys), "MR layer", *(["mouse buttons"] if mouse else []),
            *(["G915's lighting"] if lighting else [])]


def plan_summary(plan: Plan, verb: str = "Writes") -> str:
    """A sentence for the review page, and with "Imported" for after."""
    parts = [counted(n, noun) for n, noun in ((len(plan.profiles), "game profile"),
                                              (len(plan.macros), "macro")) if n]
    if plan.m_profiles:
        parts.append("M1, M2 and M3")
    return f"{verb} {word_list(parts, 'and')}." if parts else "Nothing to write."


def held_back(request: Mapping, sent: list[tuple[dict, dict]]) -> dict | None:
    """The reply to take instead of sending `request`, a profile that binds a macro whose
    macro_save in `sent` failed, or None to send it. Sent, it would play the user's own
    macro of that name, or none.

    A failed reply marked `unanswered` (a save the window timed out on or lost the
    connection for) may still have been saved, since the daemon reads a request it was too
    busy to take in time; the error says so. The CLI stops at a save with no answer and
    never hands one here."""
    # By slug: whether the save went unanswered, as against refused.
    failed = {slug(r["value"]): bool(reply.get("unanswered")) for r, reply in sent
              if r["command"] == "macro_save" and not reply.get("ok")}
    if not failed or request["command"] == "macro_save":
        return None
    body = request["profile"]
    for binding in (*body["bindings"].values(), *body["shift"].values(),
                    *(spec for entry in body["mouse"].values() for spec in entry.values())):
        name = macro_name(binding)
        if name is not None and slug(name) in failed:
            said = "may not have been saved" if failed[slug(name)] else "was not saved"
            return {"ok": False, "error": f'not sent, its macro "{name}" {said}'}
    return None


#: A failed line's reason when the daemon did not know the command, and the
#: line after them all, worded as the other hints to start or restart a
#: service are.
OLDER_DAEMON = "the daemon running is older than this Gearbench and wrote nothing"
RESTART_DAEMON = "restart the Gearbench service: systemctl --user restart gearbench"


def reply_lines(sent: list[tuple[dict, dict]]) -> tuple[list[str], list[str]]:
    """What each reply of an import said, as (done, failed) lines. A command
    the daemon does not know is one from after it started, the pack's
    `import_profile_lighting`: the failed lines end on RESTART_DAEMON."""
    done, failed = [], []
    older = False
    for request, reply in sent:
        what = _what(request)
        if reply.get("ok"):
            done.append(what)
        elif reply.get("error") == f"unknown command {request['command']!r}":
            failed.append(f"{what}: {OLDER_DAEMON}")
            older = True
        else:
            failed.append(f"{what}: {reply.get('error', 'unknown error')}")
    if older:
        failed.append(RESTART_DAEMON)
    return done, failed


def _what(request: Mapping) -> str:
    if request["command"] == "macro_save":
        return f"macro \"{request['value']}\""
    if request["command"] in ("import_profile", "import_profile_lighting"):
        return f"game profile \"{request['profile']['name']}\""
    return f"M{request['value']}"
