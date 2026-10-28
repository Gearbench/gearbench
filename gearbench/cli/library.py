"""The subcommands for what Gearbench keeps in files: the game profiles, the macros, the
running programs a profile can name, and the two imports, from G HUB's settings and from
the pack that comes with Gearbench.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .. import ghub, ghub_plan, import_plan, pack
from ..apps import launcher_warning
from ..config import MACRO_PREFIX, MODE_WORDS
from ..ghub import MAX_DOCUMENT_BYTES
from ..lighting import ANIMATIONS
from ..macros import (
    MACRO_RUNNING,
    MACRO_STARTS,
    MAX_PLAY_DELAY,
    PLAY_DELAY,
    REPEAT_SUMMARIES,
    TOGGLE,
    countdown_seconds,
    macros_dir,
)
from ..profiles import profiles_dir
from ..wording import counted, escaped as _escaped
from .output import GAME_PROFILE_LABELS, Subcommands, print_json, print_reply, send

#: import-ghub --all on a file whose profiles are all desktop defaults, which --all leaves out.
ALL_FINDS_NONE = ("The file holds no game profile for --all, which leaves out the desktop "
                  "default; --profile NAME, as --list names it, imports the desktop default "
                  "onto M1, M2 and M3.")
#: A mouse color given on the command line as this, in any case, is no mouse color of the
#: profile's own: a synced mouse then shows the keyboard's color around its painted keys.
NO_MOUSE_COLOR = "none"


def cmd_apps(args: argparse.Namespace) -> int:
    reply = send({"command": "apps"})
    if args.json or not reply.get("ok"):
        return print_reply(reply, args.json)
    apps = reply.get("apps") or []
    if not apps:
        print("nothing running to pick")
        return 0
    for app in apps:
        print(f"  {_escaped(app['label']):<24} {_escaped(app['process'])}")
    print("\nSet a profile's programs with `gearbench-ctl profile-programs <name> "
          "<process>...`", file=sys.stderr)
    return 0


def _warn_launchers(programs: list[str]) -> None:
    """A launcher among the programs that load a game profile gets a warning on stderr and
    is sent anyway: it is the user's call."""
    for program in programs:
        warning = launcher_warning(program)
        if warning:
            print(warning, file=sys.stderr)


def cmd_profile_programs(args: argparse.Namespace) -> int:
    """Set which programs load a game profile."""
    _warn_launchers(args.programs)
    return print_reply(send({"command": "profile_processes", "value": args.name,
                              "processes": args.programs}), args.json,
                       labels=GAME_PROFILE_LABELS)


def mouse_color_sent(text: str) -> str | None:
    """A mouse color as given on the command line, as a request carries it: NO_MOUSE_COLOR,
    in any case, as null, for none; anything else as it is, for the daemon to read."""
    return None if text.strip().lower() == NO_MOUSE_COLOR else text


def cmd_save_profile(args: argparse.Namespace) -> int:
    """Save what shows as a game profile; --process names the programs that load it, and
    --mouse-color the color a synced mouse shows while it is loaded, NO_MOUSE_COLOR for none."""
    _warn_launchers(args.process or [])
    request = {"command": "save_profile", "value": args.name, "processes": args.process}
    if args.mouse_color is not None:
        request["mouse_color"] = mouse_color_sent(args.mouse_color)
    return print_reply(send(request), args.json)


def cmd_profile_mouse_color(args: argparse.Namespace) -> int:
    """Set a game profile's mouse color alone, as the window's Mouse color row does."""
    return print_reply(send({"command": "profile_mouse_color", "value": args.name,
                             "color": mouse_color_sent(args.color)}), args.json,
                       labels=GAME_PROFILE_LABELS)


def _mode_words(mode: object) -> str | None:
    """A lighting mode of a reply in the CLI's words (MODE_WORDS); None for a value no mode
    has, which prints as it came. Looked up by str, since a reply's value can be any JSON."""
    return MODE_WORDS.get(str(mode))


def cmd_profiles(args: argparse.Namespace) -> int:
    reply = send({"command": "profiles"})
    if args.json or not reply.get("ok"):
        return print_reply(reply, args.json)
    overlay = reply.get("overlay")
    print(f"loaded: {_escaped(overlay or 'none')}"
          + ("  [auto]" if reply.get("auto") else ""))
    entries = reply.get("profiles") or []
    if not entries:
        # HOME or XDG_CONFIG_HOME can hold a byte that is not UTF-8, which a UTF-8 stdout
        # refuses.
        print(f"\nno game profiles yet in {_escaped(profiles_dir())}")
        return 0
    print()
    for entry in entries:
        marker = "*" if entry["name"] == overlay else " "
        procs = ", ".join(entry["processes"]) or "loads by hand"
        # An animation, or disabled, shows in place of the mode, which it keeps.
        effect = entry.get("effect")
        shows = (effect if effect in ANIMATIONS or effect == "disabled"
                 else _mode_words(entry["mode"]) or entry["mode"])
        keys = "key " if entry["keys"] == 1 else "keys"    # padded: the programs line up
        print(f"{marker} {_escaped(entry['name']):<20} {_escaped(shows):<8} "
              f"{_escaped(entry['keys']):>3} {keys}   {_escaped(procs)}")
    return 0


def cmd_macros(args: argparse.Namespace) -> int:
    reply = send({"command": "macros"})
    if args.json or not reply.get("ok"):
        return print_reply(reply, args.json)
    macros = reply.get("macros") or []
    if not macros:
        print(f"no macros yet in {_escaped(macros_dir())}")
        return 0
    running, starts_in = reply.get("running"), reply.get("macro_starts_in")
    for macro in macros:
        marker = "*" if macro["name"] == running and not starts_in else " "
        steps = "step " if macro["steps"] == 1 else "steps"    # padded: the repeat lines up
        print(f"{marker} {_escaped(macro['name']):<24} {_escaped(macro['steps']):>3} {steps}   "
              f"{REPEAT_SUMMARIES[macro['repeat']]}")
    if running and starts_in:
        print("\n" + MACRO_STARTS.format(name=_escaped(running),
                                         seconds=countdown_seconds(starts_in)))
    elif running:
        # A toggled macro runs with nothing held, so say it is running.
        print(f"\n* {MACRO_RUNNING.format(name=_escaped(running))}.")
    print(f"\nBind one with `gearbench-ctl bind G1 '{MACRO_PREFIX}<name>'`", file=sys.stderr)
    return 0


def cmd_macro_run(args: argparse.Namespace) -> int:
    """Play a macro from its file, --delay seconds from now.

    The daemon counts down, not this process, so the prompt comes back at once and
    the user can move to the macro's window; a macro played into the terminal types
    at the prompt. `status` and the window's banner show the countdown, and
    `macro-stop` or the banner's Stop cancels it.
    """
    if not 0 <= args.delay <= MAX_PLAY_DELAY:
        print(f"error: --delay is 0 to {MAX_PLAY_DELAY} s, not {args.delay:g}",
              file=sys.stderr)
        return 1
    reply = send({"command": "macro_run", "value": args.name, "delay": args.delay})
    if args.json or not reply.get("ok"):
        return print_reply(reply, args.json)
    name, repeat = _escaped(reply.get("played", args.name)), reply.get("repeat")
    delay = reply.get("delay") or 0
    print(MACRO_STARTS.format(name=name, seconds=countdown_seconds(delay)) if delay
          else f"{MACRO_RUNNING.format(name=name)}.")
    print("It repeats until `gearbench-ctl macro-stop`." if repeat == TOGGLE
          else "It runs through once.")
    return 0


def cmd_macro_stop(args: argparse.Namespace) -> int:
    reply = send({"command": "macro_stop"})
    if args.json or not reply.get("ok"):
        return print_reply(reply, args.json)
    print(f"stopped {_escaped(reply['stopped'])}" if reply.get("stopped")
          else "no macro was running")
    return 0


def cmd_let_load(args: argparse.Namespace) -> int:
    reply = send({"command": "let_load"})
    if args.json or not reply.get("ok"):
        return print_reply(reply, args.json)
    released = reply.get("released_all") or []
    print(f"no longer held off: {_escaped(', '.join(released))}" if released
          else "nothing was held off")
    return 0


def _print_ghub_found(found: ghub.Found, with_programs: bool) -> None:
    """import-ghub's listing of what the file holds; --json prints its own.

    Each profile lists again the names and steps of the macros it binds, so a crafted file
    can make many times its own size of them: once the sections' lines pass
    MAX_DOCUMENT_BYTES characters as they print, as ghub's Not imported text does, the
    profile that passed it and each after it are listed by name alone, and the sections of
    each after it are not made."""
    m_names = import_plan.existing_from_disk().m_names
    left, by_name = MAX_DOCUMENT_BYTES, 0
    for profile in found.profiles:
        where = " (the desktop default: onto M1, M2 and M3)" if profile.desktop else ""
        print(f"{_escaped(profile.name)}{where}")
        if profile.ghub_name:
            print(f'    G HUB calls it "{_escaped(profile.ghub_name)}"')
        # Counted escaped: a character escaped() writes out takes up to 10.
        sections = [] if left < 0 else [
            (_escaped(heading), [_escaped(line) for line in lines])
            for heading, lines in ghub_plan.details(profile, found, m_names, with_programs)]
        left -= sum(len(line) for _heading, lines in sections for line in lines)
        if left < 0:
            by_name += 1
            continue
        for heading, lines in sections:
            print(f"    {heading}:")
            for line in lines:
                print(f"        {line}")
    if by_name:
        print(f"{counted(by_name, 'profile')} listed by name alone: the listing is over "
              f"{MAX_DOCUMENT_BYTES // 1_000_000} million characters. --profile NAME "
              "--dry-run says what importing one would write.")
    if found.other_devices:
        print("Other devices in the file, not imported: "
              + _escaped(ghub_plan.other_devices_text(found)))


def cmd_import_ghub(args: argparse.Namespace) -> int:
    """Show what G HUB's settings hold, or import the profiles asked for.

    The file is read here; the writes go through the daemon, one command
    each, macros before the profiles that bind them.
    """
    try:
        path = Path(args.file)
        found = ghub.parse(ghub.read_settings(path), path.name)
        chosen = ghub_plan.chosen_names(found, args.profile, args.all)
    except ghub.GhubError as exc:
        # SQLite's error text can hold a schema name from the file.
        print(f"error: {_escaped(exc)}", file=sys.stderr)
        return 1
    if args.list or not (chosen or args.all):
        if args.json:
            print_json(ghub_plan.found_as_json(found, args.with_programs))
            return 0
        _print_ghub_found(found, args.with_programs)
        if not args.list:
            print("\nImport with --profile NAME (repeatable) or --all, which leaves out the "
                  "desktop default: its bindings replace those of your M1, M2 and M3, and "
                  "the G915's lighting if it has any. Each loads by hand unless "
                  "--with-programs; one that replaces yours keeps your programs.",
                  file=sys.stderr)
        return 0
    if not chosen:
        print(ALL_FINDS_NONE, file=sys.stderr)
    plan = ghub_plan.make_plan(found, chosen, import_plan.existing_from_disk(),
                               default=import_plan.ON_CLASH.get(args.on_clash),
                               with_programs=chosen if args.with_programs else ())
    if plan.unresolved:
        if not args.json:  # stdout holds JSON or nothing
            for line in plan.already:
                print(_escaped(line))
        print(f"error: Gearbench already has {'this' if len(plan.unresolved) == 1 else 'these'}:",
              file=sys.stderr)
        for clash in plan.unresolved:
            print(f'    {import_plan.CLASH_WORDS[clash.kind].lower()} "{_escaped(clash.existing)}"',
                  file=sys.stderr)
        print("Pass --on-clash replace, keep-both (the import under a new name) or keep-mine.",
              file=sys.stderr)
        return 1
    return _import_plan(plan, args)


def _import_plan(plan: import_plan.Plan, args: argparse.Namespace) -> int:
    """import-ghub's and import-pack's last step: with --dry-run say what `plan` would
    write, else send it a request at a time and say how it went."""
    requests = import_plan.requests(plan)
    if args.dry_run:
        if args.json:
            print_json(requests)
            return 0
        print("Would write:" if requests else "Nothing to write.")
        for line in import_plan.plan_lines(plan):
            print(f"    {_escaped(line)}")
        for line in plan.said:
            print(_escaped(line))
        return 0
    sent: list[tuple[dict, dict]] = []
    try:
        for request in requests:
            sent.append((request, import_plan.held_back(request, sent) or send(request)))
    finally:
        # Said even when the daemon goes away part way: what it wrote stays.
        done, failed = import_plan.reply_lines(sent)
        if args.json:
            print_json([reply for _request, reply in sent])
        else:
            for line in done:
                print(f"imported {_escaped(line)}")
            for _request, reply in sent:
                if reply.get("ok") and reply.get("note"):
                    # Saved, but not shown in full; the note can hold the device's error text.
                    print(f"note: {_escaped(reply['note'])}")
            # The plan's sentences hold only once every request was written.
            for line in plan.said if len(sent) == len(requests) and not failed else ():
                print(_escaped(line))
            for line in failed:
                print(f"error: {_escaped(line)}", file=sys.stderr)
    return 1 if failed else 0


def cmd_import_pack(args: argparse.Namespace) -> int:
    """Show the pack, or import from it: every game profile and macro, or those named.
    A name Gearbench already has keeps yours, said, unless --replace names it."""
    try:
        found = pack.read_pack()
        # --list uses neither the names nor --replace, so neither can stop it.
        if args.list:
            if args.json:
                print_json(pack.as_json(found))
            else:
                for line in pack.listing(found):
                    print(_escaped(line))
            return 0
        profiles, macros = pack.chosen_names(found, args.names)
        choices = pack.replace_choices(found, args.replace, args.names)
    except pack.PackError as exc:
        print(f"error: {_escaped(exc)}", file=sys.stderr)
        return 1
    plan = pack.make_plan(found, profiles, macros, import_plan.existing_from_disk(), choices,
                          default=import_plan.SKIP,
                          with_programs=() if args.without_programs else profiles)
    code = _import_plan(plan, args)
    # After the "yours is kept" lines, which a failed import leaves out.
    if code == 0 and not args.json and any(choices.get((c.kind, c.name)) != import_plan.REPLACE
                                           for c in plan.clashes):
        print("To take the pack's in place of yours, pass --replace NAME.", file=sys.stderr)
    return code


def add_lists_and_macros(sub: Subcommands) -> None:
    sub.add("profiles", help="list game profiles").set_defaults(func=cmd_profiles)
    sub.add("macros", help="list macros; edit them in the window's Macros tab").set_defaults(
        func=cmd_macros)
    mrun = sub.add("macro-run", help="run a macro after a countdown to click into its window; "
                                     "Toggle repeats until macro-stop; Once and While held "
                                     "run once")
    mrun.add_argument("name")
    mrun.add_argument("--delay", type=float, default=PLAY_DELAY, metavar="SECONDS",
                      help=f"seconds before the first step, 0 to {MAX_PLAY_DELAY} "
                           f"(default {PLAY_DELAY})")
    mrun.set_defaults(func=cmd_macro_run)
    sub.add("macro-stop", help="stop the macro that is running or counting down, "
                               "however it started").set_defaults(func=cmd_macro_stop)
    sub.add("apps", help="list the running programs a game profile can load for").set_defaults(
        func=cmd_apps)


def add_game_profiles_and_imports(sub: Subcommands) -> None:
    ld = sub.add("load", help="load a game profile over the active M profile")
    ld.add_argument("name")
    ld.set_defaults(func=lambda a: print_reply(send({"command": "load", "value": a.name}), a.json,
                                               {"mode": _mode_words}))

    sub.add("unload", help="unload the game profile, back to the M profile").set_defaults(
        func=lambda a: print_reply(send({"command": "unload"}), a.json))

    sub.add("let-load", help="end every hold-off and check again at once, as the window's "
                             "Load it now does").set_defaults(func=cmd_let_load)

    sub.switch("keep-loaded", command="keep_loaded",
               help="on: a game profile loaded by hand stays when another game gets focus "
                    "or starts; off (at every daemon start): it is replaced")

    sp = sub.add("save-profile", help="save the live lighting and bindings as a game profile")
    sp.add_argument("name")
    sp.add_argument("--process", action="append", default=None,
                    help="a program that loads it, as `apps` lists it; repeatable")
    sp.add_argument("--mouse-color", metavar="#RRGGBB",
                    help="the color a mouse that follows the keyboard shows while it is "
                         f"loaded, six hex digits like #0080ff, or {NO_MOUSE_COLOR} for the "
                         "keyboard's; without it, the loaded game profile's")
    sp.set_defaults(func=cmd_save_profile)

    pp = sub.add("profile-programs",
                 help="set the programs that load a game profile; with no program, it loads "
                      "by hand only")
    pp.add_argument("name")
    pp.add_argument("programs", nargs="*", metavar="program",
                    help="a process name, as `apps` lists it; the whole name, in any "
                         "case, .exe optional")
    pp.set_defaults(func=cmd_profile_programs)

    pm = sub.add("profile-mouse-color",
                 help="set the color a mouse that follows the keyboard shows while a game "
                      "profile is loaded, and nothing else of it")
    pm.add_argument("name")
    pm.add_argument("color", metavar="#RRGGBB",
                    help=f"six hex digits like #0080ff, or {NO_MOUSE_COLOR} for the keyboard's")
    pm.set_defaults(func=cmd_profile_mouse_color)

    sub.add("promote", help="save what shows to the active M profile and unload the game "
                            "profile").set_defaults(
        func=lambda a: print_reply(send({"command": "promote"}), a.json))

    rp = sub.add("rename-profile", help="rename a game profile; loaded or held off, it "
                                       "stays so")
    rp.add_argument("old")
    rp.add_argument("new")
    rp.set_defaults(func=lambda a: print_reply(send(
        {"command": "rename_profile", "value": a.old, "to": a.new}), a.json))

    dp = sub.add("delete-profile", help="delete a game profile")
    dp.add_argument("name")
    dp.set_defaults(func=lambda a: print_reply(send({"command": "delete_profile", "value": a.name}), a.json))

    ig = sub.add("import-ghub",
                 help="import profiles, bindings, lighting and macros from G HUB's settings.db")
    ig.add_argument("file",
                    help="settings.db, with its -wal beside it if it has one, or the JSON pulled "
                         "out of it")
    ig.add_argument("--list", action="store_true", help="show what the file holds; write nothing")
    ig.add_argument("--profile", action="append", default=[], metavar="NAME",
                    help="import this profile, as --list names it; repeatable. The desktop "
                         "default's bindings replace those of M1, M2 and M3, and the "
                         "G915's lighting if it has any")
    ig.add_argument("--all", action="store_true",
                    help="import every game profile; not the desktop default")
    ig.add_argument("--dry-run", action="store_true",
                    help="say what would be written, and write nothing")
    ig.add_argument("--on-clash", choices=import_plan.ON_CLASH,
                    metavar="{replace,keep-both,keep-mine}",
                    help="for a name Gearbench already has: replace it, keep both, or keep "
                         "yours; keep and skip, its words in 1.1.0, mean keep-both and "
                         "keep-mine")
    ig.add_argument("--with-programs", action="store_true",
                    help="load each game profile for the programs G HUB names, with or "
                         "without .exe; without it, each loads by hand, and one that replaces "
                         "yours keeps your programs")
    ig.set_defaults(func=cmd_import_ghub)

    ip = sub.add("import-pack",
                 help="import the game profiles and macros that come with Gearbench")
    ip.add_argument("names", nargs="*", metavar="NAME",
                    help="a game profile or macro of the pack, as --list names it; with "
                         "none, all of them")
    ip.add_argument("--list", action="store_true", help="show what the pack holds; write nothing")
    ip.add_argument("--dry-run", action="store_true",
                    help="say what would be written, and write nothing")
    ip.add_argument("--replace", action="append", default=[], metavar="NAME",
                    help="take the pack's game profile or macro of that name over yours, "
                         "which is kept otherwise; with names given, it must be one of "
                         "them; repeatable")
    ip.add_argument("--without-programs", action="store_true",
                    help="take none of the pack's programs: a new game profile loads by "
                         "hand, and one --replace names keeps your programs")
    ip.set_defaults(func=cmd_import_pack)
