"""Which X11 program has focus, read from Xwayland without blocking the loop.

A game profile bound to programs is active only while one of its programs has focus (the
owner's decision of 2026-09-24). GNOME tells only its own portal which window has focus
(org.gnome.Shell.Introspect is allowlisted) and gives Wayland clients no foreign-toplevel
protocol, but its Xwayland publishes the focused X11 window as _NET_ACTIVE_WINDOW, and a
Proton game draws into an X11 window unless it runs Wine's Wayland driver (GE-Proton's
PROTON_ENABLE_WAYLAND=1). This client speaks the X11 wire protocol on the standard library
alone: python-xlib is not installed, and nothing is to be. Nothing here may block the
daemon's select loop: a setup or request not answered in TIMEOUT drops the connection.
Where GNOME starts Xwayland on demand, this connection keeps it running.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import selectors
import socket
import struct
import time
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path

from .config import ConfigError, read_capped
from .dbusloop import RETRY, RETRY_MAX
from .wording import escaped, reason

log = logging.getLogger("gearbench")

#: The X server's socket folder; the abstract sockets take the same names. Read at each
#: connect, so a test can move it.
X11_DIR = "/tmp/.X11-unix"  # noqa: S108 -- the X server's own folder: nothing is made in it

#: The one authorization protocol this speaks, and the Xauthority families it takes.
MIT_COOKIE = b"MIT-MAGIC-COOKIE-1"
FAMILY_LOCAL = 256
FAMILY_WILD = 65535
#: Far above any real Xauthority file: an entry is 44 bytes and the host's name, and Mutter
#: writes two.
MAX_XAUTHORITY = 1 << 20

# Core protocol requests, events and predefined atoms.
CHANGE_WINDOW_ATTRIBUTES = 2
INTERN_ATOM = 16
GET_PROPERTY = 20
QUERY_EXTENSION = 98
CW_EVENT_MASK = 0x0800
PROPERTY_CHANGE_MASK = 0x0040_0000
PROPERTY_NOTIFY = 28
GENERIC_EVENT = 35
ATOM_CARDINAL = 6
ATOM_WINDOW = 33

# X-Resource: its minor requests, the version QueryClientIds came in, and the mask that
# asks for a client's pid. That pid is the server's view, from the client socket's
# credentials, in the host's PID namespace; _NET_WM_PID is what a program says of itself, in
# its own namespace. Steam's pressure-vessel shares the host's (checked 2026-09-24), but a
# Flatpak's sandbox does not, so X-Resource goes first.
XRES = b"X-Resource"
XRES_QUERY_VERSION = 0
XRES_QUERY_CLIENT_IDS = 4
XRES_VERSION = (1, 2)
XRES_LOCAL_CLIENT_PID = 1 << 1

ACTIVE = "_NET_ACTIVE_WINDOW"
WM_PID = "_NET_WM_PID"
WM_CHECK = "_NET_SUPPORTING_WM_CHECK"
ATOMS = (ACTIVE, WM_PID, WM_CHECK)

#: How long focus must stay put before it is acted on, and the longest a burst of changes
#: can hold it back. Alt-Tab passes through windows, and Mutter hands focus through its
#: placeholder to a window that takes focus itself (WM_TAKE_FOCUS, as Wine's do): each would
#: repaint the keyboard. 0.3 s is past both and a quick double Alt-Tab, and short beside the
#: repaint (0.25 to 0.5 s of writes). SETTLE_MAX keeps two windows that trade focus without
#: end from holding the profile back.
SETTLE = 0.3
SETTLE_MAX = 1.0
#: How long the setup or a request has to be answered before the server is taken as gone.
#: It answers in well under a millisecond; this is for one that is stopped or wedged.
TIMEOUT = 5.0
#: The longest message taken from the server. The setup reply, the longest asked for, is a
#: few kilobytes; a longer one is another protocol, which would be buffered forever.
MAX_MESSAGE = 1 << 20
#: parse_setup's words for a refusal with no reason, and for an accepted reply it cannot read.
NO_REASON = "no reason given"
TOO_SHORT = "a setup reply too short to read"
NO_SCREEN = "a setup reply with no screen"
#: What game profiles do while neither focus source is in force.
BY_RUNNING_PROGRAMS = "game profiles load for running programs"


class Settle:
    """A focus change waiting to settle: `at` is when it settles, None for none waiting. A
    burst of changes settles SETTLE_MAX after its first at the latest."""

    def __init__(self) -> None:
        self.at: float | None = None
        self._burst_at: float | None = None

    def change(self, now: float) -> None:
        """Focus changed at `now`."""
        if self._burst_at is None:
            self._burst_at = now
        self.at = min(now + SETTLE, self._burst_at + SETTLE_MAX)

    def due(self, now: float) -> bool:
        """Whether the change has settled by `now`; one that has is forgotten."""
        if self.at is None or now < self.at:
            return False
        self.clear()
        return True

    def clear(self) -> None:
        self.at = self._burst_at = None


def display_number(display: str | None) -> int | None:
    """The display number a local DISPLAY names, or None.

    `:0`, `:0.0`, `unix:0` and `unix:0.1` are display 0; the screen is ignored, since
    Xwayland has one. A host name makes it a TCP display: None.
    """
    # [0-9], as libxcb reads them: \d takes "٣", which int reads as 3. The screen too, as
    # libxcb refuses ":0." and ":0.x", and the log lines write DISPLAY as it is: ":0.\n<3>x"
    # would split each into two journal records. At most nine digits, which fit libxcb's int:
    # int() raises ValueError past 4300 digits, out of start() and the daemon's run.
    found = re.fullmatch(r"(?:unix)?:([0-9]{1,9})(?:\.[0-9]+)?", display or "")
    return int(found[1]) if found else None


def xauth_entries(data: bytes) -> Iterator[tuple[int, bytes, bytes, bytes, bytes]]:
    """An Xauthority file's entries as (family, address, number, name, data): a big-endian
    CARD16 family, then four strings of a CARD16 length and its bytes. One cut short ends
    the list."""
    at = 0
    while at + 2 <= len(data):
        (family,) = struct.unpack_from(">H", data, at)
        at += 2
        fields = []
        for _ in range(4):
            if at + 2 > len(data):
                return
            (size,) = struct.unpack_from(">H", data, at)
            at += 2
            if at + size > len(data):
                return
            fields.append(data[at:at + size])
            at += size
        yield family, fields[0], fields[1], fields[2], fields[3]


def read_cookie(number: int, environ: Mapping[str, str],
                hostname: str | None = None) -> bytes | None:
    """The MIT-MAGIC-COOKIE-1 for display `number` from $XAUTHORITY or ~/.Xauthority, or None.

    libXau's rule: family wild, or family local under this host's name, with this display's
    number or none. Mutter writes both, with no number. A FIFO, a device or a file over
    MAX_XAUTHORITY bytes gives None, since the daemon's one thread reads it.
    """
    path = Path(environ.get("XAUTHORITY")
                or Path(environ.get("HOME") or Path.home()) / ".Xauthority")
    try:
        data = read_capped(path, MAX_XAUTHORITY)
    except (ConfigError, OSError) as exc:
        log.debug("no X cookie: %s", exc)
        return None
    # gethostname decodes the kernel's bytes with surrogateescape, and libxcb compares those
    # bytes: a strict encode raises on a name that is not UTF-8.
    host = os.fsencode(hostname if hostname is not None else socket.gethostname())
    for family, address, display, name, cookie in xauth_entries(data):
        if name != MIT_COOKIE:
            continue
        if family != FAMILY_WILD and not (family == FAMILY_LOCAL and address == host):
            continue
        if display and display != str(number).encode():
            continue
        return cookie
    return None


def open_socket(number: int) -> socket.socket:
    """A connected, non-blocking socket to display `number`, abstract first, then path.

    Raises the last OSError when neither connects. Abstract first: the unit's PrivateTmp hides
    /tmp/.X11-unix, and an abstract socket belongs to the network namespace, which the unit
    shares with the session. A Unix socket's connect never waits: it connects or fails.
    """
    path = f"{X11_DIR}/X{number}"
    failure: OSError = OSError(f"no socket for display :{number}")
    for address in ("\0" + path, path):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_NONBLOCK)
        try:
            sock.connect(address)
        except OSError as exc:
            sock.close()
            failure = exc
            continue
        return sock
    raise failure


def pad(data: bytes) -> bytes:
    """`data` padded with zeros to a multiple of four bytes."""
    return data + bytes(-len(data) % 4)


def setup_request(name: bytes, data: bytes) -> bytes:
    """The connection setup: little-endian, protocol 11.0, and the
    authorization protocol's name and data, empty for none."""
    return (struct.pack("<BxHHHHxx", ord("l"), 11, 0, len(name), len(data))
            + pad(name) + pad(data))


def parse_setup(reply: bytes) -> tuple[int, int] | str:
    """The first screen's root window and the resource-id mask from a setup reply, or the
    server's reason for refusing, or why an accepted reply cannot be read. Xwayland has
    one screen."""
    status = reply[0]
    if status != 1:     # Failed (0) gives the reason's length in byte 1; Authenticate pads it
        said = reply[8:8 + reply[1]] if status == 0 else reply[8:].rstrip(b"\0")
        return said.decode(errors="replace").strip() or NO_REASON
    if len(reply) < 40:
        return TOO_SHORT
    (mask,) = struct.unpack_from("<I", reply, 16)
    (vendor,) = struct.unpack_from("<H", reply, 24)
    screens, formats = reply[28], reply[29]
    root_at = 40 + vendor + (-vendor % 4) + 8 * formats
    if screens < 1 or len(reply) < root_at + 4:
        return NO_SCREEN
    (root,) = struct.unpack_from("<I", reply, root_at)
    return root, mask


def client_pid(reply: bytes) -> int | None:
    """The pid in an X-Resource QueryClientIds reply, or None."""
    (count,) = struct.unpack_from("<I", reply, 8)
    at = 32
    for _ in range(count):
        if at + 12 > len(reply):
            break
        _client, mask, size = struct.unpack_from("<III", reply, at)
        at += 12
        if mask & XRES_LOCAL_CLIENT_PID and size == 4 and at + 4 <= len(reply):
            (pid,) = struct.unpack_from("<I", reply, at)
            return pid or None
        at += size
    return None


def property_value(reply: bytes) -> int | None:
    """The first 32-bit item of a GetProperty reply, or None when the
    property is absent, empty or of another type or format."""
    (items,) = struct.unpack_from("<I", reply, 16)
    if reply[1] != 32 or items < 1 or len(reply) < 36:
        return None
    (value,) = struct.unpack_from("<I", reply, 32)
    return value


#: What a request's reply goes to: the reply, or None with the error code.
Answer = Callable[[bytes | None, int], None]


class XFocus:
    """The process whose X11 window has focus on Xwayland.

    `ready`: focus is in force (set up, and the first focus read). `pid`: the settled focus,
    None for no program (no active window, the window manager's own, or no pid). `pending`:
    the first connection is being made, so the daemon can wait for it; the running-process
    scan would load a profile and drop it a moment later. `changed()` is called on the
    daemon's loop when focus comes into or goes out of force and when the settled pid changes.
    """

    def __init__(self, changed: Callable[[], None], *,
                 environ: Mapping[str, str] = os.environ,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._changed = changed
        self._environ = environ
        self._clock = clock
        self.ready = False
        self.pending = False
        self.pid: int | None = None
        #: Whether Gearbench's GNOME Shell extension is followed, so this source stands by:
        #: focus.Focus sets it, and only the log lines ask it.
        self.standby: Callable[[], bool] = lambda: False
        #: DISPLAY as the environment gave it, and its number.
        self.display: str | None = None
        self._number: int | None = None
        #: X-Resource's major opcode while it gives pids, else None.
        self.xres: int | None = None
        self._selector: selectors.BaseSelector | None = None
        self._tag: object = "focus"
        self._sock: socket.socket | None = None
        self._in = bytearray()
        self._out = bytearray()
        #: The last request's sequence number, and the requests waiting for
        #: a reply by number: when each was sent and what takes its answer.
        self._seq = 0
        self._waiting: dict[int, tuple[float, Answer]] = {}
        #: When the setup went out, while its reply is awaited.
        self._setup_at: float | None = None
        self._root = 0
        self._mask = 0
        self._atoms: dict[str, int] = {}
        #: The window manager's check window, for its client's ids.
        self._wm_window: int | None = None
        #: Whether this connection's setup has sent its own focus read (_begin).
        self._begun = False
        #: The next connect attempt, and the wait after one that fails.
        self._retry_at: float | None = None
        self._backoff = RETRY
        #: The focus change waiting to settle.
        self._settle = Settle()
        #: Bumped at every change, so a focus read overtaken by one is dropped
        #: when its answer comes.
        self._generation = 0
        #: The last failure said at warning, so a retry failing the same way
        #: is said at debug.
        self._said: str | None = None

    # ------------------------------------------------------------ the loop

    def start(self, selector: selectors.BaseSelector, tag: object = "focus") -> bool:
        """Start connecting, the socket registered on `selector` under `tag`. False, logged
        once, when DISPLAY names no local display."""
        self._selector, self._tag = selector, tag
        self.display = self._environ.get("DISPLAY") or None
        self._number = display_number(self.display)
        if self._number is None:
            why = ("DISPLAY is not set" if self.display is None
                   else f"DISPLAY {self.display!r} is not a local display")
            log.info("no X server to follow window focus on (%s); %s unless Gearbench's "
                     "GNOME Shell extension reports focus", why, BY_RUNNING_PROGRAMS)
            return False
        self.pending = True
        self._connect()
        return True

    def dispatch(self, mask: int) -> None:
        """Flush output and read input, from the daemon's loop when the socket is ready."""
        if mask & selectors.EVENT_WRITE:
            self._flush()
        if self._sock is None or not mask & selectors.EVENT_READ:
            return
        try:
            chunk = self._sock.recv(65536)
        except BlockingIOError:
            return
        except OSError as exc:
            self._fail(f"the connection to the X server failed ({reason(exc)})")
            return
        if not chunk:
            self._fail("the X server closed the connection")
            return
        self._in += chunk
        self._take()

    def deadline(self) -> float | None:
        """When tick() next has something to do, on the clock given, or None."""
        due = [self._retry_at, self._settle.at]
        if self._setup_at is not None:
            due.append(self._setup_at + TIMEOUT)
        if self._waiting:
            due.append(min(sent for sent, _answer in self._waiting.values()) + TIMEOUT)
        return min((when for when in due if when is not None), default=None)

    def tick(self) -> None:
        """Do what falls due: a connect attempt, a settled focus, a server silent for TIMEOUT."""
        # A loop held past TIMEOUT by another handler leaves an answer unread: read it before
        # judging, on the time taken before the read, since a focus change the read passes
        # on can hold the loop past TIMEOUT while a request it sent waits unread.
        now = self._clock()
        if self._setup_at is not None or self._waiting:
            self.dispatch(selectors.EVENT_READ)
        if self._setup_at is not None and now >= self._setup_at + TIMEOUT:
            self._fail(f"the X server did not answer the connection setup in {TIMEOUT:g} s")
        elif self._waiting and now >= min(s for s, _a in self._waiting.values()) + TIMEOUT:
            self._fail(f"the X server did not answer in {TIMEOUT:g} s")
        if self._retry_at is not None and now >= self._retry_at:
            self._connect()
        if self._settle.due(now):
            self._read_focus()

    def close(self) -> None:
        """Hang up, and try no more."""
        self._drop()
        self._retry_at = None

    # ------------------------------------------------------ the connection

    def _connect(self) -> None:
        self._retry_at = None
        assert self._number is not None  # noqa: S101 -- for the type checker: start() checked it
        try:
            sock = open_socket(self._number)
        except OSError as exc:
            self._fail(f"cannot reach the X server {self.display} "
                       f"({reason(exc)})")
            return
        self._sock = sock
        assert self._selector is not None  # noqa: S101 -- for the type checker: start() set it
        self._selector.register(sock, selectors.EVENT_READ, self._tag)
        cookie = read_cookie(self._number, self._environ)
        if cookie is None:
            log.debug("no MIT-MAGIC-COOKIE-1 for %s; asking with no authorization",
                      self.display)
        self._setup_at = self._clock()
        self._write(setup_request(MIT_COOKIE if cookie else b"", cookie or b""))

    def _drop(self) -> None:
        """Forget the connection and everything that went with it."""
        sock, self._sock = self._sock, None
        if sock is not None:
            if self._selector is not None:
                with contextlib.suppress(KeyError, ValueError):
                    self._selector.unregister(sock)
            sock.close()
        self._in.clear()
        self._out.clear()
        self._waiting.clear()
        self._setup_at = None
        self._settle.clear()
        self._atoms.clear()
        self._wm_window = None
        self._begun = False
        self.xres = None
        self.ready = self.pending = False
        self.pid = None

    def _fail(self, why: str) -> None:
        """Drop the connection and retry after the backoff; the daemon is told if focus was
        in force."""
        was = self.ready
        self._drop()
        wait = self._backoff
        self._backoff = min(wait * 2, RETRY_MAX)
        self._retry_at = self._clock() + wait
        meanwhile = ("Gearbench's GNOME Shell extension is followed meanwhile" if self.standby()
                     else f"{BY_RUNNING_PROGRAMS} meanwhile")
        level = logging.DEBUG if why == self._said else logging.WARNING
        self._said = why
        log.log(level, "%s; %s, trying again in %g s", why, meanwhile, wait)
        if was:
            self._changed()

    def _write(self, data: bytes) -> None:
        self._out += data
        self._flush()

    def _flush(self) -> None:
        """Send what the socket takes now; the rest waits for EVENT_WRITE."""
        if self._sock is None:
            return
        try:
            sent = self._sock.send(self._out) if self._out else 0
        except BlockingIOError:
            sent = 0
        except OSError as exc:
            self._fail(f"the connection to the X server failed ({reason(exc)})")
            return
        del self._out[:sent]
        wanted = selectors.EVENT_READ | (selectors.EVENT_WRITE if self._out else 0)
        if self._selector is not None and wanted != self._selector.get_key(self._sock).events:
            self._selector.modify(self._sock, wanted, self._tag)

    def _request(self, opcode: int, data: int, body: bytes,
                 answer: Answer | None = None) -> None:
        """Send one request; `answer` takes its reply or its error. Omit it only for a
        request with no reply: an error to that ends the connection."""
        if self._sock is None:
            return          # dropped by a failed write earlier in this pass
        body = pad(body)
        self._seq = (self._seq + 1) & 0xFFFF
        if answer is not None:
            self._waiting[self._seq] = (self._clock(), answer)
        self._write(struct.pack("<BBH", opcode, data, 1 + len(body) // 4) + body)

    # --------------------------------------------------- what comes back

    def _take(self) -> None:
        """Handle every whole message in the buffer, in order."""
        while self._sock is not None:
            if len(self._in) < 8:
                return
            if self._setup_at is not None:
                (length,) = struct.unpack_from("<H", self._in, 6)
                size = 8 + 4 * length
            elif len(self._in) < 32:
                return
            elif self._in[0] in (1, GENERIC_EVENT):
                (length,) = struct.unpack_from("<I", self._in, 4)
                size = 32 + 4 * length
            else:
                size = 32
            if size > MAX_MESSAGE:
                self._fail(f"the X server sent a message of {size} bytes, "
                           "which is not the protocol this speaks")
                return
            if len(self._in) < size:
                return
            message = bytes(self._in[:size])
            del self._in[:size]
            if self._setup_at is not None:
                self._set_up(message)
            elif message[0] == 0:
                self._error(message)
            elif message[0] == 1:
                self._reply(message)
            elif message[0] & 0x7F == PROPERTY_NOTIFY:
                self._property_changed(message)

    def _set_up(self, reply: bytes) -> None:
        self._setup_at = None
        found = parse_setup(reply)
        if isinstance(found, str):
            # The server's words can hold a newline, and the journal takes each line as a
            # record of its own (cli.service.JournalFormatter).
            what = ("set up a connection this cannot use" if reply[0] == 1
                    else "refused the connection")
            self._fail(f"the X server {self.display} {what}: {escaped(found)}")
            return
        self._root, self._mask = found
        self._seq = 0
        # The events first: a change after any read below comes as one.
        self._request(CHANGE_WINDOW_ATTRIBUTES, 0,
                      struct.pack("<III", self._root, CW_EVENT_MASK, PROPERTY_CHANGE_MASK))
        for name in ATOMS:
            def took(reply: bytes | None, _code: int, name: str = name) -> None:
                self._took_atom(name, reply)
            self._request(INTERN_ATOM, 0,
                          struct.pack("<HH", len(name), 0) + name.encode(), took)
        self._request(QUERY_EXTENSION, 0, struct.pack("<HH", len(XRES), 0) + XRES,
                      lambda reply, _code: self._took_extension(reply))

    def _reply(self, message: bytes) -> None:
        (seq,) = struct.unpack_from("<H", message, 2)
        waiting = self._waiting.pop(seq, None)
        if waiting is not None:
            waiting[1](message, 0)

    def _error(self, message: bytes) -> None:
        code = message[1]
        (seq,) = struct.unpack_from("<H", message, 2)
        waiting = self._waiting.pop(seq, None)
        if waiting is None:
            # The one request without a reply is the event selection; with
            # it refused, no change would ever come.
            self._fail(f"the X server refused a request (error {code})")
            return
        waiting[1](None, code)

    def _took_atom(self, name: str, reply: bytes | None) -> None:
        atom = struct.unpack_from("<I", reply, 8)[0] if reply is not None else 0
        if not atom:
            self._fail(f"the X server has no atom {name}")
            return
        self._atoms[name] = atom

    def _took_extension(self, reply: bytes | None) -> None:
        if reply is None or not reply[8]:
            self._begin()
            return
        opcode = reply[9]
        self._request(opcode, XRES_QUERY_VERSION, struct.pack("<BBxx", *XRES_VERSION),
                      lambda version, _code: self._took_version(opcode, version))

    def _took_version(self, opcode: int, reply: bytes | None) -> None:
        if reply is not None and struct.unpack_from("<HH", reply, 8) >= XRES_VERSION:
            self.xres = opcode
        self._begin()

    def _begin(self) -> None:
        """Set up: read the window manager's check window, then the focus."""
        # The focus read covers every change so far, so _property_changed starts no settle
        # before it: a settle's read sent during the setup could be answered before the check
        # window's and X-Resource's version, and an answer of no window would put focus in
        # force without them. Set before the reads, so a drop by either clears it.
        self._begun = True
        self._get_property(self._root, WM_CHECK, ATOM_WINDOW, self._took_wm_window)
        self._read_focus()

    def _took_wm_window(self, window: int | None) -> None:
        self._wm_window = window or None

    def _property_changed(self, event: bytes) -> None:
        window, atom = struct.unpack_from("<II", event, 4)
        if window != self._root:
            return
        if atom == self._atoms.get(WM_CHECK):
            self._get_property(self._root, WM_CHECK, ATOM_WINDOW, self._took_wm_window)
        elif atom == self._atoms.get(ACTIVE) and self._begun:
            self._generation += 1       # a read on its way is out of date
            self._settle.change(self._clock())

    def _get_property(self, window: int, name: str, kind: int,
                      then: Callable[[int | None], None]) -> None:
        """GetProperty of one 32-bit item; `then` gets it, or None for an
        absent property or a window that has gone (BadWindow)."""
        if self._sock is None:
            return          # dropped, atoms and all, by a failed write earlier in this pass
        atom = self._atoms.get(name)
        if atom is None:
            # The atoms are asked for first, and a server answers in the order asked: one
            # that answers a later request before them is not speaking the protocol.
            self._fail(f"the X server answered before it gave the atom {name}")
            return
        self._request(GET_PROPERTY, 0,
                      struct.pack("<IIIII", window, atom, kind, 0, 1),
                      lambda reply, _code: then(property_value(reply)
                                                if reply is not None else None))

    # ------------------------------------------------------------ the focus

    def _read_focus(self) -> None:
        generation = self._generation
        self._get_property(self._root, ACTIVE, ATOM_WINDOW,
                           lambda window: self._took_window(generation, window))

    def _made_by_wm(self, window: int) -> bool:
        """Whether `window` is the window manager's own, by the client bits of its id and the
        check window's. While a Wayland window or the Shell has focus, Mutter points
        _NET_ACTIVE_WINDOW at its 1x1 placeholder, and X-Resource would name gnome-shell."""
        return (self._wm_window is not None
                and window & ~self._mask == self._wm_window & ~self._mask)

    def _took_window(self, generation: int, window: int | None) -> None:
        if generation != self._generation:
            return
        if not window or self._made_by_wm(window):
            self._settled(generation, None)
        elif self.xres is not None:
            self._request(self.xres, XRES_QUERY_CLIENT_IDS,
                          struct.pack("<III", 1, window, XRES_LOCAL_CLIENT_PID),
                          lambda reply, code: self._took_client(generation, window,
                                                                reply, code))
        else:
            self._read_wm_pid(generation, window)

    def _took_client(self, generation: int, window: int, reply: bytes | None,
                     code: int) -> None:
        pid = client_pid(reply) if reply is not None else None
        if reply is None:
            log.debug("X-Resource refused QueryClientIds (error %d); reading _NET_WM_PID "
                      "for the rest of this connection", code)
            self.xres = None
        if pid is not None:
            self._settled(generation, pid)
        else:
            self._read_wm_pid(generation, window)

    def _read_wm_pid(self, generation: int, window: int) -> None:
        self._get_property(window, WM_PID, ATOM_CARDINAL,
                           lambda pid: self._settled(generation, pid or None))

    def _settled(self, generation: int, pid: int | None) -> None:
        """A focus read has its answer: pass it on if it is still the
        latest and says something new."""
        if generation != self._generation:
            return
        first = not self.ready
        if first:
            self.ready, self.pending = True, False
            self._backoff = RETRY
            self._said = None
            how = "" if self.xres is not None else "; pids by _NET_WM_PID (none from X-Resource)"
            if self.standby():
                log.info("Xwayland %s connected, standing by while Gearbench's GNOME Shell "
                         "extension is followed%s", self.display, how)
            else:
                log.info("following window focus on Xwayland %s%s", self.display, how)
        if first or pid != self.pid:
            self.pid = pid
            self._changed()
