"""The runtime folder, XDG_RUNTIME_DIR, and its rule: no file is read or written there, the
daemon's socket and the equalizer's and the noise suppression's confs included, where another
account could put a folder or a file of its own in their place, as far as stat can tell that
account from root (open_to_others). The daemon's unit runs the rule before each start outside
its user namespace, where stat can, and makes a missing runtime folder there (main,
`python3 -m gearbench.runtime_folder`). And each
folder Gearbench makes, the runtime folder, the settings folder and the folders in it, is its
owner's alone (make_private). The standard library and wording alone.
"""

from __future__ import annotations

import os
import stat
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from .wording import escaped, reason

#: The mode of each folder Gearbench makes, as logind makes /run/user/<uid>; a umask can
#: narrow it but never widen it. At mkdir's default, 0777 less the login umask, 002 say,
#: the folder's group could write in it, and so rename a file of its own over a macro or a
#: sector backup there, or put a folder of its own in the runtime folder's place.
PRIVATE_FOLDER = 0o700


def missing_folders(folder: Path) -> list[Path]:
    """`folder` and each folder above it that is not there, the deepest first: what a mkdir
    with its parents makes."""
    return [place for place in (folder, *folder.parents) if not place.exists(follow_symlinks=False)]


def make_private(folder: Path, made: Sequence[Path]) -> None:
    """Make `folder` and the folders above it in `made`, missing_folders' list for it, the
    topmost first, each at PRIVATE_FOLDER. A folder already there keeps its mode, and a file
    at `folder` is mkdir's FileExistsError."""
    for place in (*reversed(made), folder):
        place.mkdir(mode=PRIVATE_FOLDER, exist_ok=True)


def runtime_home(environ: Mapping[str, str] = os.environ) -> Path | None:
    """XDG_RUNTIME_DIR, or None when it is unset, empty or relative, which the XDG spec
    says to ignore: a relative one is a folder under the working one, where, in /tmp or a
    shared checkout, another account can listen in the daemon's place."""
    base = Path(environ.get("XDG_RUNTIME_DIR") or "")
    return base if base.is_absolute() else None


def others_may_write(folder: Path) -> bool:
    """Whether another account owns `folder` or may write in it, through its group or as any
    account (mode & 0o022): it could bind a socket there first, or put its own file in place
    of one written there before it is read. OSError passes through."""
    info = os.stat(folder)
    return info.st_uid != os.geteuid() or bool(info.st_mode & 0o022)


def open_to_others(folder: Path) -> Path | None:
    """The first path through which another account could put a folder of its own where
    this process looks for the runtime folder `folder`, a full path; None when there is
    none. That is `folder` itself, links followed, when another account owns it or others
    may write in it (others_may_write), or else the first on the way (on_the_way)."""
    if others_may_write(folder):
        return folder
    return on_the_way(folder)


def on_the_way(folder: Path) -> Path | None:
    """The first folder or link on the way to `folder`, `folder` included, through which
    another account could put a folder of its own in its place; None when there is none.
    That is one, by the path as given and by each link's target in turn, a link given as
    `folder` and one a link leads to included, that an account other than root or this one
    owns; and a folder that others may write in without the sticky bit, since they may
    rename what it holds."""
    uid = os.geteuid()
    # Root as this process sees it: in a user namespace that does not map root, as the
    # unit's PrivateUsers=yes makes one (systemd.exec(5)), stat gives root's files the
    # overflow id, 65534 by default (user_namespaces(7)), and every other unmapped
    # account's files too, which this cannot tell from root's: hence main, which the unit
    # runs outside it.
    root = Path("/").stat().st_uid
    checked: set[Path] = set()
    pending = [folder]
    while pending:
        given = pending.pop(0)
        for path in (*reversed(given.parents), given):
            if path in checked:
                continue
            checked.add(path)
            info = path.stat(follow_symlinks=False)
            if info.st_uid not in (root, uid) or (
                    stat.S_ISDIR(info.st_mode) and info.st_mode & 0o022
                    and not info.st_mode & stat.S_ISVTX):
                return path
            # Then its target's path: a link a link leads to, and the folder that holds it,
            # are on neither the path as given nor the resolved one.
            if stat.S_ISLNK(info.st_mode):
                pending.append(path.parent / path.readlink())
    return None


#: The way out of a runtime folder refused, `uid` the account whose folder it is: one only
#: that account may write in.
OWN_FOLDER = "set XDG_RUNTIME_DIR to a folder only you may write in, as /run/user/{uid} is"
#: The way out where the folders on the way to the runtime folder matter too.
OWN_FOLDER_BELOW = ("set XDG_RUNTIME_DIR to a folder only you may write in, below folders "
                    "only you or root may change, as /run/user/{uid} is")
#: Why a runtime folder another account owns or may write in is refused, and the way out.
SHARED_FOLDER = f"another account owns it or other accounts may write in it; {OWN_FOLDER}"
#: Why a runtime folder is refused that another account could swap for its own through
#: `at_fault`, a folder or link on the way to it, and the way out.
SWAPPABLE_FOLDER = ("{at_fault}, on the way to it, is another account's or lets other "
                    "accounts rename what it holds, so one could put a folder of its own where "
                    f"the runtime folder is; {OWN_FOLDER_BELOW}")


def shared_problem(folder: Path) -> str | None:
    """Why the runtime folder `folder` is refused, with the way out: SHARED_FOLDER where
    open_to_others finds it at fault itself, SWAPPABLE_FOLDER where it finds a path on the
    way; None where it finds none. OSError passes through."""
    return problem_with(folder, open_to_others(folder))


def problem_with(folder: Path, at_fault: Path | None) -> str | None:
    """shared_problem's words for the runtime folder `folder` and the path `at_fault`
    found to let another account put a folder of its own there, or None for no such path."""
    if at_fault is None:
        return None
    words = SHARED_FOLDER if at_fault == folder else SWAPPABLE_FOLDER
    return words.format(at_fault=escaped(at_fault), uid=os.geteuid())


#: private_runtime's refusal, `problem` being shared_problem's.
SHARED_RUNTIME = "{what} uses no file in {folder}: {problem}"


def not_set(files: str) -> str:
    """private_runtime's `unset` words for this account: `files`, subject and verb ("X's
    file has"), have nowhere to go, then OWN_FOLDER's way out."""
    return (f"XDG_RUNTIME_DIR is not set to a full path, so {files} nowhere to go; "
            f"{OWN_FOLDER.format(uid=os.geteuid())}")


def private_runtime(error: type[Exception], unset: str, what: str) -> Path:
    """XDG_RUNTIME_DIR for `what`'s files, or `error`: with `unset` while it is not set to a
    full path (runtime_home), and with shared_problem's words where another account could put a
    folder of its own in its place (open_to_others), as Daemon.listen refuses it, since that
    account could swap a conf before PipeWire reads it, or put its own file, or a link, where
    one is read or written. A stat on the way raises its OSError, as a write in it would."""
    folder = runtime_home()
    if folder is None:
        raise error(unset)
    if (problem := shared_problem(folder)) is not None:
        raise error(SHARED_RUNTIME.format(what=what, folder=escaped(folder), problem=problem))
    return folder


#: The daemon's refusal of its runtime folder, `problem` shared_problem's words, UNSEEN_FOLDER
#: or UNMADE_FOLDER.
LISTEN_REFUSED = ("Gearbench daemon cannot listen in {folder}: {problem}, then start the "
                  "daemon again")
#: main's `problem` where a stat on the way to the runtime folder fails, `reason` its words
#: (wording.reason): what it could not see may be another account's.
UNSEEN_FOLDER = f"{{reason}}, looking at it or a folder on the way to it; {OWN_FOLDER_BELOW}"
#: main's `problem` where making the runtime folder or a missing folder above it fails,
#: `reason` its words (wording.reason).
UNMADE_FOLDER = f"{{reason}}, making it or a folder above it; {OWN_FOLDER_BELOW}"


def unmade_problem(folder: Path) -> str | None:
    """Make the runtime folder `folder` and the missing folders above it, as Daemon.listen
    makes them (make_private): None once they are made, UNMADE_FOLDER's words where a mkdir
    fails."""
    try:
        make_private(folder, missing_folders(folder))
    except OSError as error:
        return UNMADE_FOLDER.format(reason=reason(error), uid=os.geteuid())
    return None


def main() -> int:
    """The runtime folder's rule as Daemon.listen keeps it, which the daemon's unit runs
    before each start outside its user namespace: under PrivateUsers=yes stat shows root's
    files and every other account's as one id, so listen cannot tell another account's
    folder or link on the way from root's, and here stat can. A missing folder is made here,
    once nothing on the way to the deepest folder there is at fault, and then checked as one
    that is there. 1, with listen's words on stderr, for a folder listen would refuse, for a
    missing one below a folder or link that on_the_way finds, for one that cannot be made
    and for one stat cannot look at; 0 otherwise, an unset XDG_RUNTIME_DIR included, whose
    /run/user/<uid> is below root's folders alone."""
    folder = runtime_home()
    if folder is None:
        return 0
    there = next(place for place in (folder, *folder.parents) if os.path.lexists(place))
    problem = None
    try:
        if there != folder:
            # Left to listen, another account could make it first in a folder others may make
            # folders in, sticky or not, as /tmp, and listen under PrivateUsers=yes would take
            # that account's folder for root's. Made here, it is this account's, and the check
            # below finds one another account made before this mkdir.
            problem = problem_with(folder, on_the_way(there)) or unmade_problem(folder)
        problem = problem or shared_problem(folder)
    except OSError as error:
        problem = UNSEEN_FOLDER.format(reason=reason(error), uid=os.geteuid())
    if problem is None:
        return 0
    print(LISTEN_REFUSED.format(folder=escaped(folder), problem=problem), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
