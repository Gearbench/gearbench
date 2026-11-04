"""Reading one request off the control socket for Daemon.serve_one: how long, how deep and
how slow a request may be, and the refusal of a body that cannot be read."""

from __future__ import annotations

import socket
import time

from ..json_text import TOO_DEEP, JsonRefused, json_value
from ..wording import size_words
from .shared import nested_past

#: Longest pause between a request's segments (the timeout is per recv): a longer gap is a
#: client that stopped talking, and waiting on it stalls key handling.
CLIENT_TIMEOUT = 0.5

#: Time from the accept to send the whole request: CLIENT_TIMEOUT alone lets a client that
#: sends a byte every 0.4 s hold the loop indefinitely.
REQUEST_DEADLINE = 2.0

#: The most a request may hold, since the loop buffers a whole request before it parses it.
#: The largest a front end sends, a macro of 64 text steps at their longest, is about 35 KB.
MAX_REQUEST = 1 << 20

#: How deep a request may nest lists and objects, itself counted: json.loads reads nesting
#: deeper than repr() of a value in a refusal can walk. A front end sends at most 5, a game
#: profile with its own DPI stages in import_profile.
MAX_DEPTH = 32


class BadRequest(Exception):
    """A request serve_one refuses before any handler sees it."""


def read_request(conn: socket.socket) -> object:
    """The JSON value `conn` sends, read to the end of file the client sends by shutting its
    write side, within REQUEST_DEADLINE of the call; BadRequest for one too long, too slow,
    not JSON or nested past MAX_DEPTH."""
    deadline = time.monotonic() + REQUEST_DEADLINE
    received = bytearray()
    while (chunk := read_before(conn, deadline)):
        received += chunk
        if len(received) > MAX_REQUEST:
            raise BadRequest(f"longer than the {size_words(MAX_REQUEST)} a request may be")
    try:
        # A name given twice keeps its last value: a request is no hand edit a save overwrites.
        request = json_value(bytes(received) or b"{}", one_name=False)
    except JsonRefused as exc:
        raise BadRequest(str(exc)) from None
    # The same words: which of the two refuses a deep body depends on the stack.
    if nested_past(request, MAX_DEPTH):
        raise BadRequest(TOO_DEEP)
    return request


def read_before(conn: socket.socket, deadline: float) -> bytes:
    """One read of a client's request, waiting no longer than CLIENT_TIMEOUT or what is left
    before `deadline`, a time.monotonic(); BadRequest after CLIENT_TIMEOUT with nothing sent
    or once that has passed."""
    left = deadline - time.monotonic()
    if left > 0:
        conn.settimeout(min(CLIENT_TIMEOUT, left))
        try:
            return conn.recv(65536)
        except TimeoutError:
            if time.monotonic() < deadline:
                raise BadRequest(f"nothing sent for {CLIENT_TIMEOUT:g} s") from None
    raise BadRequest(f"not sent whole within the {REQUEST_DEADLINE:g} s a request may take")
