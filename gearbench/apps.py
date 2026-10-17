"""Programs the user is running interactively, for a game profile's picker.

An app the user opened runs in a systemd scope under the user's app.slice: GNOME names it
after the desktop entry, and a snap gets snap.<snap>.<app>-<uuid>.scope. A background
service gets a .service unit instead. A game launched from Steam runs in Steam's scope, so
each process in a scope is listed, helpers left out. That scope is whatever started Steam:
with Steam closed, a game's shortcut or a browser's steam:// link starts it in theirs.

The daemon's reads of /proc are here too: the names a game profile matches a process by,
and the walk of every process (walk_processes), which also finds the other programs that
drive these devices (OTHER_TOOLS).
"""

from __future__ import annotations

import functools
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from .config import config_home, first_of, printable

CGROUP_ROOT = Path("/sys/fs/cgroup")

#: Where every process is read, the daemon's walk and each name read alike; a test points
#: it at a tree of its own.
PROC = Path("/proc")

#: Helper processes no profile would bind to.
IGNORED_COMMS = frozenset({
    "bash", "sh", "dash", "zsh", "env", "sleep", "cat", "which",
    "bwrap", "flatpak", "xdg-dbus-proxy", "dbus-proxy",
    "steamwebhelper", "reaper",
    # Steam runs some binaries through a path ending in /exe.
    "exe",
    # Run beside a game in a launcher's scope: Steam's shader cache, overlay, crash
    # reporter and driver checks, Proton's script, Heroic's store runners, umu, itch's butler.
    "fossilize_replay", "gameoverlayui", "steamerrorreporter", "gldriverquery",
    "vulkandriverquery", "proton", "legendary", "gogdl", "nile", "umu-run", "butler",
})

#: Wine itself and the Windows programs it runs in every prefix beside the game.
#: `steam.exe` is Proton's stand-in for Steam inside the prefix.
WINE_LOADERS = frozenset({"wine", "wine64", "wine-preloader", "wine64-preloader"})
WINE_OWN = frozenset({
    "wineserver", "services.exe", "winedevice.exe", "plugplay.exe", "explorer.exe",
    "rpcss.exe", "svchost.exe", "tabtip.exe", "conhost.exe", "start.exe", "cmd.exe",
    "wineboot.exe", "winemenubuilder.exe", "rundll32.exe", "steam.exe",
})

#: Steam's container runtime and the usual sidecar naming, by prefix and suffix: the names
#: carry version and architecture fragments that change. `glycin-` is GNOME's sandboxed
#: image loaders, which libglycin starts in the scope of any program that loads an image.
IGNORED_PREFIXES = ("srt-", "pv-", "steam-runtime", "pressure-vessel", "gsd-", "ibus-",
                    "at-spi", "glycin-")
IGNORED_SUFFIXES = ("-helper", "-host", "_handler", "-handler", "-launcher-service",
                    "-daemon", "-agent", "-indicator", "-wrapper")

#: Launchers that run all the time: process name to the name each goes by.
LAUNCHERS = {"steam": "Steam", "lutris": "Lutris", "heroic": "Heroic",
             "bottles": "Bottles", "itch": "itch"}
#: LAUNCHERS by _app_stem of a desktop id: Heroic's Flatpak is com.heroicgameslauncher.hgl.
LAUNCHER_APPS = {**LAUNCHERS, "hgl": "Heroic"}
LAUNCHER_WARNING = ("{0} is a launcher: this profile would load while {0} has focus, or runs "
                    "where focus is not followed.")

#: A reverse-DNS desktop id, com.valvesoftware.Steam, whose last part names the app. Any
#: other id names it whole, dots and all: Wine's entry for Battle.net is
#: wine-Programs-Battle.net-Battle.net, from Programs/Battle.net/Battle.net.desktop.
_REVERSE_DNS = re.compile(r"[a-z][a-z0-9]*(?:\.[A-Za-z0-9_-]+){2,}")


def _app_stem(app_id: str) -> str:
    """The part of a desktop id that names the app."""
    return app_id.rsplit(".", 1)[-1] if _REVERSE_DNS.fullmatch(app_id) else app_id


#: An interpreter as exe resolves it: python3.14, its free-threaded and debug builds
#: (python3.14t, python3.14d), and GNOME's gjs-console.
_INTERPRETER = re.compile(r"(python\d*(\.\d+)?[dt]?|gjs(-console)?)$")


def is_interpreter(name: str) -> bool:
    return _INTERPRETER.match(name.lower()) is not None


def is_helper(comm: str) -> bool:
    """A name the picker does not offer. The scan skips only the interpreters
    (match_names), so the rest can still be typed."""
    low = comm.lower()
    return (
        is_interpreter(low)
        or low in IGNORED_COMMS
        or low in WINE_LOADERS
        or low in _WINE_OWN
        or low.startswith(IGNORED_PREFIXES)
        or low.endswith(IGNORED_SUFFIXES)
        # steam-runtime-tools names a program per architecture <tuple>-srt-<program>:
        # x86_64-linux-gnu-srt-launcher-service.
        or "-srt-" in low
    )


def launcher(name: str) -> str | None:
    """The launcher `name` would match, by the name it goes by, or None, under
    names_match's rule: "Steam" is Steam, "team" is nothing."""
    return next((shown for process, shown in LAUNCHERS.items()
                 if names_match(name.strip(), process)), None)


def launcher_warning(name: str) -> str | None:
    """What to say when `name` is picked or typed, when it is a launcher."""
    found = launcher(name)
    return LAUNCHER_WARNING.format(found) if found else None


_SCOPE = re.compile(r"^app-(?:gnome-|flatpak-)?(?P<app>.+?)-(?P<pid>\d+)\.scope$")
#: snap run moves a snap into its own scope: snap.discord.discord-<uuid>.scope. An app
#: name can hold hyphens (snap-store), so the UUID's shape ends it; a hook's scope,
#: snap.<snap>.hook.<hook>-<uuid>, has a dot there and does not match.
_SNAP_SCOPE = re.compile(r"^snap\.(?P<app>[^.]+)\.[^.]+"
                         r"-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.scope$")


#: One escaped byte in a unit name: systemd's \xNN, or \xffffffNN for a byte from 0x80
#: up where char is signed (x86): systemd_unit_name_escape in libgnome-desktop's
#: gnome-systemd.c prints a gchar with "\x%02x", sign-extended.
_ESCAPED_BYTE = re.compile(r"\\x(?:ffffff)?([0-9a-fA-F]{2})")
_ESCAPED_RUN = re.compile(f"(?:{_ESCAPED_BYTE.pattern})+")


def _unescape(name: str) -> str:
    """A unit name with each run of escaped bytes decoded as UTF-8, as read_comm does."""
    return printable(_ESCAPED_RUN.sub(
        lambda m: bytes.fromhex("".join(_ESCAPED_BYTE.findall(m.group()))).decode(
            errors="replace"),
        name))


def autostart_dirs() -> tuple[Path, ...]:
    """Where autostart entries are: the system's, and the user's under config_home() as it
    is now, the rule config_dir() follows."""
    return (Path("/etc/xdg/autostart"), config_home() / "autostart")


def _is_autostart(app_id: str) -> bool:
    """Whether `app_id` has an autostart entry, disabled or not, whoever started this run."""
    return any((d / f"{app_id}.desktop").exists() for d in autostart_dirs())


#: The kernel's cap on /proc/<pid>/comm in bytes; the cut can fall inside a UTF-8 character.
COMM_MAX = 15

#: Linux's PATH_MAX. process_name looks for a flag as the path's end only within it: each
#: try copies the string up to the flag, and any process can make its argv[0] 128 KiB of
#: " -a" at exec: uncapped, 2.5 to 18 s of the daemon's only thread at every scan
#: (measured 2026-10-01).
PATH_MAX = 4096

#: The kernel's cap on one exec argument, NUL included (MAX_ARG_STRLEN, 32 pages of 4 KiB),
#: so argv[0] as exec set it is read whole. /proc/<pid>/cmdline itself has no cap: any
#: process can point its argument bounds at memory of its own with prctl(PR_SET_MM_MAP),
#: no privilege needed, and a 200 MiB one read whole took the reader past the service's
#: MemoryMax (measured 2026-10-12).
ARGV0_MAX = 128 * 1024

#: The longest program name in UTF-8 bytes: NAME_MAX, the longest file name on Linux, Wine
#: and Proton programs included. A scan's cost grows with name length as well as count.
MAX_PROGRAM_BYTES = 255


def _fits(name: str) -> bool:
    """Whether `name` is no longer than a file's name may be. Any process can make its argv[0]
    128 KiB long, and each name taken is made printable and lowercased at every scan."""
    return len(name.encode(errors="replace")) <= MAX_PROGRAM_BYTES


def _cut(name: str) -> str:
    """`name` as comm keeps it: its first COMM_MAX bytes, decoded as read_comm does."""
    return printable(name.encode(errors="replace")[:COMM_MAX].decode(errors="replace").strip())


#: WINE_OWN plus each name as comm cuts it: winemenubuilder.exe is `winemenubuilder`.
_WINE_OWN = WINE_OWN | {_cut(name) for name in WINE_OWN}


def read_comm(proc: Path) -> str:
    """/proc/<pid>/comm as text, bytes that do not decode replaced: the kernel can cut
    inside a character, and any process can set any bytes. Raises OSError once it has gone."""
    return printable((proc / "comm").read_bytes().decode(errors="replace").strip())


def read_argv0(proc: Path) -> str:
    """argv[0] from /proc/<pid>/cmdline, within its first ARGV0_MAX bytes, bytes that do not
    decode replaced. Raises OSError once the process has gone."""
    with (proc / "cmdline").open("rb") as file:
        return file.read(ARGV0_MAX).split(b"\x00", 1)[0].decode(errors="replace")


def process_name(pid: int) -> str | None:
    """Best available name for a process: exe, then argv[0], then comm.

    - /proc/<pid>/exe is untruncated, but denied for another user's processes, kernel
      threads, and every process when read from the service (PrivateUsers=yes); Yama's
      ptrace_scope=1 does not block it.
    - Chrome and Electron rewrite argv into one space-joined string, so a plain basename
      of argv[0] takes a path from a flag: "renderD128" from
      --render-node-override=/dev/dri/renderD128.
    - comm is always readable, but cut at 15 bytes, and any process can set it.

    An interpreter's name from exe or argv[0] is passed over for comm, the script's name,
    and so is one from argv[0] longer than a file's name may be (_fits).
    """
    proc = PROC / str(pid)
    try:
        target = os.readlink(proc / "exe")
        # A binary whose file was replaced or removed gets this suffix.
        name: str | None = Path(target.removesuffix(" (deleted)")).name
        if name and not is_interpreter(name):
            return printable(name)
    except OSError:
        pass
    try:
        argv0 = read_argv0(proc)
    except OSError:
        argv0 = ""
    # A Windows program under Wine whose exe could not be read, as in the service: its
    # argv[0] is its Windows path, and Path splits only at slashes, so below it would go
    # by comm, cut at 15 bytes.
    if "\\" in argv0:
        try:
            name = windows_name(argv0, read_comm(proc))
        except OSError:
            return None         # gone since its cmdline was read
        if name:
            return name
    # A path with spaces, a Steam game's, is taken whole where its last part is comm: cut
    # at the first space, cs2 would be "Counter-Strike". A rewritten argv, "<path> --flags",
    # is cut before the flag within PATH_MAX, and "<path> <link>" at the first space, where
    # the path's last part is comm.
    if " " in argv0:
        try:
            comm = read_comm(proc)
        except OSError:
            return None         # gone since its cmdline was read
        # A flag that starts within PATH_MAX ends by PATH_MAX + 2.
        ends = [flag.start() for flag in re.finditer(r" -\S", argv0[:PATH_MAX + 2])]
        for end in [*ends, len(argv0), argv0.index(" ")]:
            name = Path(argv0[:end]).name
            if name and not is_interpreter(name) and _cut(name) == comm and _fits(name):
                return printable(name)
        # No cut's last part is comm, so the first space may be in a folder's name, "My" for
        # "/opt/My Game/game": a program that renamed its main thread goes by comm.
        return comm or None
    name = Path(argv0).name
    # A backslash left in it is a Windows path windows_name did not believe, which goes by
    # comm, as own_name has it where exe can be read.
    if name and not is_interpreter(name) and "\\" not in name and _fits(name):
        return printable(name)
    try:
        return read_comm(proc) or None
    except OSError:
        return None


def windows_name(argv0: str, comm: str) -> str | None:
    """A Windows program's own name: the last part of the Windows path in its argv[0].

    Wine drops its own argv[0] and sets comm from that last part, which the kernel cuts at
    15 bytes (rebuild_argv and set_process_name in Wine's dlls/ntdll/unix/env.c). Believed,
    where no longer than a file's name may be (_fits), when comm matches, or for an .exe:
    an Unreal Engine game renames its main thread, and so comm, to GameThread (Arc Raiders'
    PioneerGame.exe, 2026-09-25).
    """
    name = argv0.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
    if not name or not _fits(name):
        return None
    return printable(name) if _cut(name) == comm or name.lower().endswith(".exe") else None


def own_name(pid: int) -> str | None:
    """What the picker calls a process: `process_name`, except a Windows program under
    Wine, whose exe is Wine's loader, goes by its own name.

    match_names matches by process_name too, so a profile bound to Wine's loader, as the
    picker offered it before or as typed, matches a Windows program under Wine where exe
    can be read: by a daemon run by hand, not the service, which is denied every exe.
    """
    return _own_name(pid, process_name(pid))


def _own_name(pid: int, name: str | None) -> str | None:
    """own_name, given the process_name already read."""
    if name is None or name.lower() not in WINE_LOADERS:
        return name
    proc = PROC / str(pid)
    try:
        comm = read_comm(proc)
        argv0 = read_argv0(proc)
    except OSError:
        return None         # gone since its exe was read
    return windows_name(argv0, comm) or comm or name


def match_names(pid: int, comm: str) -> tuple[str | None, set[str]]:
    """Process `pid`'s own_name, and the lowercased names a profile can match it by: `comm`,
    process_name and own_name, interpreters left out, or a profile bound to python3.14 would
    match the daemon itself."""
    name = process_name(pid)
    shown = _own_name(pid, name)
    return shown, {n.lower() for n in (comm, name, shown) if n and not is_interpreter(n)}


def names_of(pid: int) -> tuple[str | None, set[str]]:
    """The focused window's process, as match_names names it with its comm read now.
    (None, set()) for a process that has gone, before its comm was read or while the rest
    was: no game profile loads for a program that is gone (the owner's decision of
    2026-10-07)."""
    try:
        comm = read_comm(PROC / str(pid))
    except OSError:
        return None, set()
    shown, names = match_names(pid, comm)
    return (shown, names) if shown is not None else (None, set())


class Process(NamedTuple):
    pid: int
    uid: int
    comm: str


class OtherTool(NamedTuple):
    """What an OTHER_TOOLS entry's problem names: the tool, what it does and the fix."""
    name: str
    does: str
    fix: str


#: Other programs that talk to Logitech devices, by comm, checked against Ubuntu 26.04's
#: packages: solaar 1.1.19-1's /usr/bin/solaar (`#!/usr/bin/python3`) is comm `solaar`
#: (measured); ratbagd 0.18-1build1's /usr/sbin/ratbagd and openrgb 0.9+git20251009+ds-1's
#: /usr/bin/openrgb are binaries that keep their names. A Solaar run from its source tree
#: (`#!/usr/bin/env python3`) is comm python3 and is not seen.
OTHER_TOOLS = {
    "solaar": OtherTool(
        "Solaar", "talks HID++ to Logitech devices too",
        "quit it and uninstall it: its package starts it at every login"),
    "ratbagd": OtherTool(
        "ratbagd", "talks HID++ to Logitech devices too",
        "stop it (sudo systemctl stop ratbagd) and uninstall it, or Piper starts it again"),
    "openrgb": OtherTool(
        "OpenRGB", "may drive Logitech devices too: which of its drivers are on cannot be "
                   "seen from here",
        "quit it, and stop the server its package starts at every login "
        "(systemctl --user mask --now openrgb)"),
}


def walk_processes() -> list[Process]:
    """Every process's pid, owner and comm, from one walk of /proc. A /proc that cannot be
    listed raises OSError."""
    found = []
    for entry in PROC.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            uid = entry.stat().st_uid
            comm = read_comm(entry)
        except OSError:
            continue        # exited between listing and reading
        found.append(Process(int(entry.name), uid, comm))
    return found


def running_processes(procs: list[Process]) -> set[str]:
    """Names of this user's processes in `procs`, each by the names the focus matches it by
    (match_names): the comm, cut at 15 bytes, the full program name, which misses a
    program that set its own comm, and a Windows program's own name under Wine."""
    uid = os.getuid()
    return set().union(*(match_names(proc.pid, proc.comm)[1] for proc in procs if proc.uid == uid))


def find_other_tools(procs: list[Process]) -> dict[str, list[int]]:
    """The OTHER_TOOLS running, whoever runs them, and their pids."""
    found = {comm: sorted(p.pid for p in procs if p.comm.lower() == comm) for comm in OTHER_TOOLS}
    return {comm: pids for comm, pids in found.items() if pids}


#: The most pids an other-tool problem names before it counts the rest: any account can run
#: any number of processes under a tool's name, which would otherwise set the problem's length.
SHOWN_PIDS = 5


def other_tool_problem(comm: str, pids: list[int]) -> str:
    """The problem an OTHER_TOOLS entry running as `pids` is."""
    tool, shown = OTHER_TOOLS[comm], first_of([str(pid) for pid in pids], SHOWN_PIDS)
    which = f"pid {pids[0]}" if len(pids) == 1 else f"pids {shown}"
    return f"{tool.name} is running ({which}) and {tool.does}; {tool.fix}"


def names_match(wanted: str, running: str) -> bool:
    """Whether a profile's stored program name is a running program's name.

    The whole name, in any case. ".exe" is optional on either side: a Windows
    program under Wine goes by its name with it, and may be typed without.

    Allows for comm's 15-byte cut. The kernel cut the raw name, so the wanted name is cut
    in bytes before `printable` or lowercasing (a few characters change length when
    lowercased), then decoded and stripped as read_comm does (_cut). `replace` covers a
    lone surrogate from a JSON escape, which would otherwise raise out of the daemon's
    scan. Both names are compared as `printable` leaves them: a stored name may hold a
    format character, as a zero-width non-joiner in Persian spelling.
    """
    return running_form(running) in wanted_forms(wanted)


def running_form(name: str) -> str:
    """A running program's name as names_match compares it."""
    return printable(name).lower()


#: The most programs wanted_forms keeps worked out. A scan asks for the library's programs
#: in the same order each time, so an LRU cache smaller than the library keeps none of them
#: from one scan to the next. 8192 is 128 profiles of 64 programs (profiles.MAX_PROCESSES),
#: 7.4 MiB with 255-byte names (measured 2026-10-15).
WANTED_FORMS_KEPT = 8192


@functools.lru_cache(maxsize=WANTED_FORMS_KEPT)
def wanted_forms(wanted: str) -> frozenset[str]:
    """Each running_form that `wanted` matches. Cached: the scan compares every profile's
    programs with every running name every five seconds, on the daemon's only thread."""
    base = wanted[:-4] if wanted.lower().endswith(".exe") else wanted
    forms = set()
    for name in (base, f"{base}.exe") if base else (wanted,):
        forms.add(running_form(name))
        if len(name.encode(errors="replace")) > COMM_MAX:
            forms.add(_cut(name).lower())
    return frozenset(forms)


@dataclass(frozen=True)
class RunningApp:
    #: Desktop entry id from the scope name ("google-chrome"), or a snap's name ("discord").
    app_id: str
    #: The process's name as `own_name` gives it, which a profile stores.
    comm: str

    @property
    def is_main(self) -> bool:
        """Whether this process looks like the app itself, not a child. A launcher's own
        process is, under any desktop id LAUNCHER_APPS maps to that launcher: Heroic's
        Flatpak ends in "hgl" and runs as `heroic`."""
        stem = _app_stem(self.app_id).lower()
        comm = self.comm.lower()
        if stem in LAUNCHER_APPS and LAUNCHER_APPS[stem] == LAUNCHERS.get(comm):
            return True
        return stem.startswith(comm[:6]) or comm.startswith(stem[:6])


def running_apps() -> list[RunningApp]:
    """Distinct (app, process) pairs the user is running interactively.

    An app with an autostart entry is left out, except a launcher, which the session can
    start too (Steam's "Run Steam when my computer starts"): its games run in its scope.
    """
    uid = os.getuid()
    root = CGROUP_ROOT / "user.slice" / f"user-{uid}.slice" / f"user@{uid}.service" / "app.slice"
    found: dict[RunningApp, None] = {}
    for scope in sorted(root.glob("*.scope")):
        match = _SCOPE.match(scope.name) or _SNAP_SCOPE.match(scope.name)
        if match is None:
            continue
        app_id = _unescape(match.group("app"))
        if _is_autostart(app_id) and _app_stem(app_id).lower() not in LAUNCHER_APPS:
            continue
        try:
            pids = [int(line) for line in (scope / "cgroup.procs").read_text().split()]
        except (OSError, ValueError):
            continue
        for pid in pids:
            comm = own_name(pid)
            if not comm or is_helper(comm):
                continue
            found[RunningApp(app_id=app_id, comm=comm)] = None
    # The app's own process first, so a game is listed below its launcher.
    return sorted(found, key=lambda a: (a.app_id.lower(), not a.is_main, a.comm.lower()))


def candidates() -> list[tuple[str, str]]:
    """(process name, label) pairs for the picker, one per name: Chrome runs under both
    "google-chrome" and "com.google.Chrome". The app whose own process it is wins the label."""
    running = running_apps()
    # A launcher in another app's scope names the programs there other than that app's own.
    launcher_in = {app.app_id: LAUNCHERS[app.comm.lower()] for app in running
                   if app.comm.lower() in LAUNCHERS}
    best: dict[str, RunningApp] = {}
    for app in running:
        current = best.get(app.comm)
        if current is None or (app.is_main and not current.is_main):
            best[app.comm] = app
    out = []
    for comm, app in best.items():
        # A game in a launcher's scope takes the launcher's name.
        stem = _app_stem(app.app_id)
        shown = LAUNCHER_APPS.get(stem.lower())
        if shown is None and not app.is_main:
            shown = launcher_in.get(app.app_id)
        if shown is None:
            label = stem.replace("-", " ").strip()
            shown = label.title() if label.islower() else label
        out.append((comm, shown))
    out.sort(key=lambda pair: pair[1].lower())
    return out
