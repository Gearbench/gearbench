"""The daemon's client, for the CLI and the window: the socket's path, whether a daemon
listens there (listening), the one round trip both make (send_command), and what a failed
one means (failure). The daemon package's `__init__` gives the front ends the names they
take from here, so they import the daemon alone.
"""

from __future__ import annotations

import json
import os
import socket
import struct
from pathlib import Path

from ..json_text import NUMBER_TOO_LONG, TOO_DEEP
from ..runtime_folder import OWN_FOLDER, runtime_home
from ..wording import SERVICE_JOURNAL, service_command

#: send_command's error for a connection closed with no reply.
NO_ANSWER = ("the daemon closed the connection without answering, so it stopped or crashed; "
             f"`{SERVICE_JOURNAL}`, or the terminal it ran in, says why")

#: send_command's next step where no daemon listens, by whether it runs as root, as which the
#: daemon never runs, so neither way to start one works there.
NO_DAEMON_STEP = {
    False: f"start it with `{service_command('start')}` or `gearbench-ctl daemon`",
    True: "run the command as yourself, not as root: the daemon runs as your own account"}

#: send_command's next step where another account listens and root has no invoking user, which
#: has no daemon of its own to point at, since the daemon never runs as root.
FROM_THE_ACCOUNT = "run the command with sudo, run0 or pkexec from the account whose daemon it is"


def logind_runtime(uid: int) -> Path:
    """The runtime folder logind makes for the user `uid`, which the client takes for
    XDG_RUNTIME_DIR while that is not set to a full path (runtime_home)."""
    return Path(f"/run/user/{uid}")


def socket_path() -> Path:
    return (runtime_home() or logind_runtime(os.getuid())) / "gearbench.sock"


def listening(path: Path) -> bool:
    """Whether a daemon listens on `path`, one whose listen backlog is full included; a file
    left by a killed daemon refuses."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
        # A blocking connect() to a full backlog waits, with no timeout, until the daemon
        # accepts, which its start_up or a device job holds off; Linux refuses a non-blocking
        # one with EAGAIN at once (measured 2026-11-01).
        probe.setblocking(False)
        try:
            probe.connect(str(path))
        except BlockingIOError:
            return True
        except OSError:
            return False
    return True


#: The most digits a user id has, a uid_t being 32 bits. A longer run of digits names no
#: user, and int() refuses one past sys.get_int_max_str_digits (4300 by default).
UID_DIGITS = len(str(2**32 - 1))


def invoking_uid() -> int | None:
    """As root gained through sudo, run0 or pkexec, the id of the user who ran it, as SUDO_UID
    or PKEXEC_UID names it; None for any other run, and for an id that is not a number of
    UID_DIGITS digits at most."""
    invoker = os.environ.get("SUDO_UID") or os.environ.get("PKEXEC_UID") or ""
    if os.geteuid() != 0 or not invoker.isdecimal() or len(invoker) > UID_DIGITS:
        return None
    return int(invoker)


def root_alone() -> bool:
    """Whether this runs as root with no invoking user named (invoking_uid), as after su - or
    doas: no daemon can be asked then, since the daemon runs as a user's own account, never
    in root's runtime folder."""
    return os.geteuid() == 0 and invoking_uid() is None


def use_invoking_users_runtime_dir() -> None:
    """As root gained through sudo, run0 or pkexec, point XDG_RUNTIME_DIR at the invoking
    user's runtime folder, so that socket_path() and send_command() reach that user's daemon.
    sudo starts a command without the variable unless its policy keeps it, pkexec in a
    minimal environment that names the user in PKEXEC_UID, and run0 in a session of root's
    own, whose folder is root's. No daemon listens in root's folder. A relative one counts as
    unset, since socket_path() ignores it.

    Taken from sudoers(5), pkexec(1) and run0(1). On Fedora 44 on 2026-11-03 the report run
    under each reached the account's PipeWire, and under sudo and pkexec it named the device
    the account's daemon holds."""
    invoker = invoking_uid()
    if invoker is not None and runtime_home() in (None, logind_runtime(os.getuid())):
        os.environ["XDG_RUNTIME_DIR"] = str(logind_runtime(invoker))


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
    there (as root under sudo, run0 or pkexec, any account's but the invoking user's, root's
    own included), and json.JSONDecodeError for a reply that is not a UTF-8 JSON object or
    that json.loads cannot read for its depth or a number's digits."""
    path = socket_path()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(timeout)
        try:
            conn.connect(str(path))
        except (FileNotFoundError, ConnectionRefusedError):
            raise ConnectionError(f"{NOT_RUNNING}: nothing listens on {path}; "
                                  f"{NO_DAEMON_STEP[os.geteuid() == 0]}") from None
        peer = peer_uid(conn)
        # Root under sudo, run0 or pkexec reaches the invoking user's listener alone, since
        # it looks for that user's daemon. Root's own is refused there too: the daemon never
        # runs as root, and that user's programs can put a link to any socket root may open
        # where root looks. A third account's is refused, since a folder sudo kept may be one
        # another account can listen in.
        invoker = invoking_uid()
        own = os.geteuid() if invoker is None else invoker
        if peer != own:
            # A PermissionError, as connect()'s to another account's socket of mode 0600: a
            # ConnectionError would read as no daemon running.
            step = FROM_THE_ACCOUNT if invoker is None and own == 0 else OWN_FOLDER.format(uid=own)
            raise PermissionError(
                f"another account (uid {peer}) listens on the socket, not your Gearbench daemon, "
                f"so nothing was sent; {step}")
        # A daemon that cuts a request off past request_reader.MAX_REQUEST answers, then
        # closes with the rest unread: Linux fails the send, or resets the connection once
        # the answer is read (measured 2026-10-31). Its answer is read either way; with none,
        # the send's or the reset's error stands.
        cut_off: OSError | None = None
        try:
            conn.sendall(json.dumps(request).encode())
            conn.shutdown(socket.SHUT_WR)
        except (BrokenPipeError, ConnectionResetError) as exc:
            cut_off = exc
        chunks: list[bytes] = []
        try:
            while chunk := conn.recv(65536):
                chunks.append(chunk)
        except ConnectionResetError:
            if not chunks:
                raise
        if not chunks and cut_off is not None:
            raise cut_off
        data = b"".join(chunks)
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
            else f"the reply is {TOO_DEEP}" if isinstance(exc, RecursionError)
            else f"the reply {NUMBER_TOO_LONG}", "", 0) from None
    if not isinstance(reply, dict):
        raise UnreadableReply("the reply is not a JSON object", "", 0)
    return reply


#: What a failed round trip means (`failure`). Each front end puts its own words to them,
#: with these four in common: a daemon it could not reach, a reply it could not read, no
#: daemon running (DOWN), and one that did not answer in time (BUSY).
DOWN, BUSY, UNREAD, UNKNOWN = "down", "busy", "unread", "unknown"
NOT_REACHED = "the daemon could not be reached"
REPLY_UNREAD = "the daemon's reply could not be read"
NOT_RUNNING = "the daemon is not running"
NOT_IN_TIME = "the daemon did not answer in time; it may be busy"


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
