"""The daemon's client, for the CLI and the window: the socket's path, the one round trip
both make (send_command), and what a failed one means (failure). The daemon package's
`__init__` gives the front ends each name here, so they import the daemon alone.
"""

from __future__ import annotations

import json
import os
import socket
import struct
from pathlib import Path

from ..config import runtime_home

#: send_command's error for a connection closed with no reply.
NO_ANSWER = ("the daemon closed the connection without answering, so it stopped or crashed; "
             "`journalctl --user -u gearbench`, or the terminal it ran in, says why")


def socket_path() -> Path:
    return (runtime_home() or Path(f"/run/user/{os.getuid()}")) / "gearbench.sock"


def use_invoking_users_runtime_dir() -> None:
    """As root gained through sudo, run0 or pkexec, point XDG_RUNTIME_DIR at the invoking
    user's runtime folder, so that socket_path() and send_command() reach that user's daemon.
    sudo starts a command without the variable unless its policy keeps it, pkexec in a
    minimal environment that names the user in PKEXEC_UID, and run0 in a session of root's
    own, whose folder is root's. No daemon listens in root's folder. A relative one counts as
    unset, since socket_path() ignores it. For a probe that refuses beside a daemon.

    Taken from sudoers(5), pkexec(1) and run0(1); not checked on a run under any of them."""
    invoker = os.environ.get("SUDO_UID") or os.environ.get("PKEXEC_UID")
    if os.geteuid() != 0 or not invoker:
        return
    kept = runtime_home()
    if kept is None or kept == Path(f"/run/user/{os.getuid()}"):
        os.environ["XDG_RUNTIME_DIR"] = f"/run/user/{invoker}"


class UnreadableReply(json.JSONDecodeError):
    """A reply send_command cannot read for a cause other than JSON's own: a JSONDecodeError,
    for the callers that catch one, whose text gives no position, since such a cause has none."""

    def __str__(self) -> str:
        return self.msg


#: struct ucred, which SO_PEERCRED fills: a pid_t, a uid_t and a gid_t (unix(7)).
UCRED = struct.Struct("iII")


def peer_uid(conn: socket.socket) -> int:
    """The user id of the process at the other end of `conn`, as the kernel took it when that
    process called listen() (unix(7), SO_PEERCRED)."""
    _pid, uid, _gid = UCRED.unpack(conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                                   UCRED.size))
    return uid


def send_command(request: dict, timeout: float = 3.0) -> dict:
    """Send one request to the running daemon and return its reply, for the CLI and the
    window. Raises ConnectionError when no daemon is listening, or when it closes the
    connection without answering, PermissionError when another account's process listens
    there, and json.JSONDecodeError for a reply that is not a UTF-8 JSON object or that
    json.loads cannot read for its depth or a number's digits."""
    path = socket_path()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(timeout)
        try:
            conn.connect(str(path))
        except (FileNotFoundError, ConnectionRefusedError):
            raise ConnectionError(
                f"no Gearbench daemon listening on {path}. "
                f"Start it with `systemctl --user start gearbench` or `gearbench-ctl daemon`."
            ) from None
        peer = peer_uid(conn)
        # Root is let through, for a probe under sudo that looks for the user's daemon.
        if os.geteuid() not in (0, peer):
            # A PermissionError, as connect()'s to another account's socket of mode 0600: a
            # ConnectionError would read as no daemon running.
            raise PermissionError(
                f"another account (uid {peer}) listens on the socket, not your Gearbench daemon, "
                f"so nothing was sent. Set XDG_RUNTIME_DIR to a folder of your own, as "
                f"/run/user/{os.geteuid()} is")
        conn.sendall(json.dumps(request).encode())
        conn.shutdown(socket.SHUT_WR)
        data = b"".join(iter(lambda: conn.recv(65536), b""))
    if not data:
        # serve_one answers every request it reads, so no reply is a daemon that died after
        # reading it: a ConnectionError, like the kernel's reset when it died before.
        raise ConnectionResetError(NO_ANSWER)
    try:
        reply = json.loads(data.decode())
    except (RecursionError, ValueError) as exc:
        # JSON's own refusal goes as it is, with its position. The others are UTF-8's, the
        # stack's and int()'s, past sys.get_int_max_str_digits (4300 digits by default).
        raise exc if isinstance(exc, json.JSONDecodeError) else UnreadableReply(
            f"the reply is not UTF-8 ({exc.reason})" if isinstance(exc, UnicodeDecodeError)
            else "the reply is nested too deeply to read" if isinstance(exc, RecursionError)
            else "the reply holds a number too long to read", "", 0) from None
    if not isinstance(reply, dict):
        raise UnreadableReply("the reply is not a JSON object", "", 0)
    return reply


#: What a failed round trip means (`failure`). Each front end puts its own words to them.
DOWN, BUSY, UNREAD, UNKNOWN = "down", "busy", "unread", "unknown"


def failure(error: BaseException | None) -> str:
    """What a failed round trip means, `error` being what send_command raised: DOWN,
    BUSY, UNREAD or UNKNOWN, for the CLI and the window alike.

    DOWN, no daemon running: ConnectionError and its subclasses, which are no socket, a
    refused connection, or a daemon that left before reading the request or answering.

    BUSY, a daemon running that has not got to the request: a timeout, or a listen backlog
    too full to connect. Its loop is single-threaded, and a mouse profile write holds it
    for about a second, far longer if the mouse stops answering part way.

    UNREAD, a reply that does not parse: something answered, so a daemon is running, and
    there is no timeout to name. A daemon that left mid-reply leaves one.

    UNKNOWN, a socket that could not be used: one owned by another user, or a path through
    a file or too long for AF_UNIX. Whether a daemon runs is unknown.
    """
    if isinstance(error, ValueError):
        return UNREAD
    if isinstance(error, (TimeoutError, BlockingIOError)):
        return BUSY
    if isinstance(error, ConnectionError):
        return DOWN
    return UNKNOWN
