"""Logitech's Centurion transport over a hidraw node: its frames, a message in fragments, the
root feature's lookup, the events a device sends unasked, and the route to a headset behind
a receiver's bridge. Nothing here imports the daemon or knows a headset.

A frame is REPORT_SIZE bytes: a prefix (Framing), a length that counts the bytes from the
flags byte to the last parameter, the flags, a feature index, the function and software id
in one byte, then the parameters, zero-padded (Solaar's base.py; HeadsetControl's
logitech_centurion_protocol.hpp and its A50 Gen 5 driver, PR 531, whose `02 0c 06 00 09 1c 01
ff <level>` is a sidetone write in this form). Above the frame it is HID++ 2.0's: the root
feature at index 0 looks a feature up, an error reply has ERROR_REPLY for its index and
the request's index, function byte and an error code after it, and a frame with software id
0 is an event, an error reply apart: its software id is the low four bits of the request's
index, 0 for the root's (Solaar reads a Centurion frame as a HID++ long message). So the
failures are hidpp's.

A message too long for one frame goes in fragments: the flags give each its number and say
whether more follow, the first carries the index and the function byte, and the rest carry
the message's bytes on (Solaar's write_centurion_cpl; the A50 X's 0x0103 reply came in two
frames flagged 01 and 02, the second its bytes alone, in G HUB's captures of 2026-10-21).

A receiver reaches its headset through its CentPPBridge (Bridged): a request to the headset
is a message inside a request to the bridge, which the receiver acknowledges at once and
answers when the headset does, with an event of the bridge; the headset's own events come
the same way, and the bridge's function 0 event says whether a headset is connected (Link)
(Solaar's centurion_bridge_request and its listener's _handle_centurion_notification;
HeadsetControl's sendCenturionBridgeRequest).
"""

from __future__ import annotations

import logging
import os
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from itertools import cycle

from .hidpp import (
    ERROR_REPLY,
    REQUEST_RETRIES,
    REQUEST_TIMEOUT,
    ROOT_FEATURE,
    SOFTWARE_IDS,
    Feature,
    HidppError,
    HidppFailure,
    HidppTimeout,
    HidrawNode,
    feature_of,
    no_reply,
    request_name,
)
from .wording import counted

log = logging.getLogger("gearbench")

#: A frame with its report id, each way (Solaar's CENTURION_FRAME_SIZE; HeadsetControl's
#: A50 Gen 5 driver writes and reads 64).
REPORT_SIZE = 64
#: The flags of a message in one frame: no fragment index, nothing more to come (Solaar's
#: write_centurion_cpl, `(fragment_index << 1) | more`).
SINGLE = 0x00
#: The flags' bit that says another fragment follows; the fragment's number is above it.
MORE = 0x01
#: The bytes a fragment of a request leaves spare at the end of its frame, as Solaar's and
#: HeadsetControl's bridged requests do, which the receivers they drive took: a fragment
#: never fills its frame (Solaar's _BRIDGE_FIRST_CHUNK, "one byte of conservative margin").
SPARE = 1
#: What the length counts besides the parameters: the flags, the index and the function byte.
HEADER = 3
#: The most events kept between two reads of them: an event says the state now, so the oldest
#: go first.
EVENTS_MAX = 256
#: The receiver's CentPPBridge (Solaar's CENT_PP_BRIDGE, HeadsetControl's CenturionBridge;
#: the PRO X 2 LIGHTSPEED's, the G522's and the G325's receivers list it at index 3).
BRIDGE = 0x0003
#: The bridge's functions: its connection event, function 0, and sendFragment, function 1,
#: whose reply is an empty acknowledgment with the request's software id, and whose answer,
#: the headset's, is an event of function 1, its MessageEvent (Solaar; HeadsetControl).
FN_LINK, FN_SEND = 0, 1
#: The headset's id in the top four bits of the bridge's header, whose other twelve bits
#: count the message: 0 (Solaar's `device_id=0 for the headset`, HeadsetControl's).
HEADSET = 0
BRIDGED_MAX = 0x0FFF
#: The bytes of the bridge's header, before the headset's message.
BRIDGE_HEADER = 2


@dataclass(frozen=True)
class Framing:
    """The bytes before a frame's length: its report id, and the byte after it on some, None
    on others: 0x0C after report 0x02 (HeadsetControl PR 531), a device's address after
    report 0x50 (Solaar's 0x50 variant), none after report 0x51 (Solaar's)."""

    report_id: int
    address: int | None = None

    @property
    def prefix(self) -> bytes:
        return bytes([self.report_id] if self.address is None else [self.report_id, self.address])

    @property
    def room(self) -> int:
        """The message bytes one frame carries: all but the prefix, the length and the flags."""
        return REPORT_SIZE - len(self.prefix) - 2

    def words(self) -> str:
        """The prefix as the journal and the CLI give it: "02 0c"."""
        return self.prefix.hex(" ")


@dataclass(frozen=True)
class Message:
    """A message, from one frame or its fragments put together: the feature index, the
    function, the software id (0 for an event) and the parameters."""

    index: int
    function: int
    software_id: int
    params: bytes


@dataclass(frozen=True)
class Link:
    """The bridge's connection event: whether a headset is connected behind the receiver."""

    connected: bool


def _framed(framing: Framing, flags: int, chunk: bytes) -> bytes:
    return (framing.prefix + bytes([len(chunk) + 1, flags]) + chunk).ljust(REPORT_SIZE, b"\x00")


def frames(framing: Framing, index: int, function: int, software_id: int,
           params: bytes = b"") -> list[bytes]:
    """The frames of one request: one while it fits, else its fragments, each with
    SPARE bytes left at its end. The flags number 128 fragments, more than the longest
    message, a bridged one of BRIDGED_MAX bytes, takes."""
    body = bytes([index, function << 4 | software_id]) + params
    if len(body) <= framing.room:
        return [_framed(framing, SINGLE, body)]
    size = framing.room - SPARE
    chunks = [body[at:at + size] for at in range(0, len(body), size)]
    last = len(chunks) - 1
    return [_framed(framing, number << 1 | (MORE if number < last else 0), chunk)
            for number, chunk in enumerate(chunks)]


def unframe(framing: Framing, report: bytes) -> tuple[int, bytes] | None:
    """A report of `framing` as (its flags, the bytes its length counts after them), or None
    for one of another prefix, or with a length past its end or no flags."""
    start = len(framing.prefix)
    if len(report) < start + 2 or report[:start] != framing.prefix:
        return None
    length = report[start]
    end = start + 1 + length
    if length < 1 or end > len(report):
        return None
    return report[start + 1], report[start + 2:end]


def message_of(body: bytes) -> Message | None:
    """A message's bytes, the index and the function byte first, as a Message; None for
    fewer than those two."""
    if len(body) < HEADER - 1:
        return None
    return Message(body[0], body[1] >> 4, body[1] & 0x0F, body[2:])


def error_of(message: Message) -> tuple[int, int, int, HidppError] | None:
    """An error reply's request, as (feature index, function, software id), and the
    HidppError it carries; None for any other message. The reply puts ERROR_REPLY where the
    index goes, the request's index where the function byte goes, then the request's
    function byte and the code, as a HID++ 2.0 error does."""
    if message.index != ERROR_REPLY or len(message.params) < 2:
        return None
    index, function_byte = message.function << 4 | message.software_id, message.params[0]
    return (index, function_byte >> 4, function_byte & 0x0F,
            HidppError(index, function_byte >> 4, message.params[1]))


def _reply_source(message: Message) -> str:
    """Where a reply dropped comes from, as a debug line names it: "index 0x09 fn 0", or for
    an error reply, whose own index is ERROR_REPLY, its request's with the code: "index 0x09
    fn 1 (error 0x02)"."""
    if (error := error_of(message)) is None:
        return f"index 0x{message.index:02x} fn {message.function}"
    index, function, _software_id, raised = error
    return f"index 0x{index:02x} fn {function} (error 0x{raised.code:02x})"


def _is_event(message: Message) -> bool:
    """Whether `message` is an event: software id 0, an error reply apart, whose software id
    is its request's index's low four bits, 0 for the root's."""
    return message.software_id == 0 and message.index != ERROR_REPLY


def looked_up(feature_id: int, reply: bytes) -> Feature | None:
    """The root feature's getFeature answer for `feature_id`: its index, type and version,
    or None for one the device lacks; HidppFailure for an answer too short."""
    if len(reply) < HEADER:
        raise HidppFailure(f"the lookup of 0x{feature_id:04x} answered "
                           f"{counted(len(reply), 'byte')}, not {HEADER}")
    return feature_of(reply)


class Centurion(HidrawNode):
    """A request/reply channel on one node, with the events read while it waits kept for
    `next_event`. One request in flight: a reply is its answer when it echoes the request's
    index, function and one of the software ids it went out with, so a late reply to an
    earlier request is dropped. A message that comes in fragments is put back together."""

    _read_size = REPORT_SIZE

    def __init__(self, path: str | os.PathLike[str], framing: Framing) -> None:
        self._setup(self._opened(path), framing)

    @classmethod
    def from_fd(cls, fd: int, framing: Framing) -> Centurion:
        """A channel over a descriptor somebody else opened, and now owns."""
        channel = cls.__new__(cls)
        channel._setup(fd, framing)
        return channel

    def _setup(self, fd: int, framing: Framing) -> None:
        self._fd = fd
        self.framing = framing
        self._events: deque[Message] = deque(maxlen=EVENTS_MAX)
        self._software_ids = cycle(SOFTWARE_IDS)
        #: The fragments of a message so far, and the number the next one has.
        self._fragments: bytearray | None = None
        self._next_fragment = 0

    # -- wire ------------------------------------------------------------

    def _write(self, index: int, function: int, params: bytes) -> int:
        """Send one request, in fragments if it needs them, and return the software id it
        went out with; ValueError, from frames, before anything is written."""
        software_id = next(self._software_ids)
        for packet in frames(self.framing, index, function, software_id, params):
            self._send_packet(packet)
        return software_id

    def _assembled(self, report: bytes) -> Message | None:
        """A report's message: a whole frame's, or the last fragment's with the ones before
        it; None for a fragment with more to come, or a report that is no message of this
        framing, which is dropped, as is a fragment out of its order with those before it; a
        whole frame between fragments drops those before it."""
        unframed = unframe(self.framing, report)
        if unframed is None:
            log.debug("centurion: dropped %s",
                      report.rstrip(b"\x00").hex(" ") or "a report of zeros")
            return None
        flags, body = unframed
        number = flags >> 1
        if flags == SINGLE:
            self._fragments = None
            return message_of(body)
        if number == 0:
            self._fragments, self._next_fragment = bytearray(body), 1
            return None
        if self._fragments is None or number != self._next_fragment:
            log.debug("centurion: dropped fragment %d, %s", number,
                      "with none before it" if self._fragments is None
                      else f"where {self._next_fragment} was next")
            self._fragments = None
            return None
        self._fragments += body
        self._next_fragment += 1
        if flags & MORE:
            return None
        whole, self._fragments = bytes(self._fragments), None
        return message_of(whole)

    def _file(self, report: bytes) -> Message | None:
        """A report read that is no event: an event is queued, anything not a message of
        this framing dropped, and a message returned for the caller to match."""
        message = self._assembled(report)
        if message is None:
            return None
        if _is_event(message):
            self._events.append(message)
            return None
        return message

    def pump(self) -> None:
        """Read what is queued without sending or waiting: events are kept, and a reply,
        nobody's now, is dropped."""
        while (report := self._raw_read()) is not None:
            if (message := self._file(report)) is not None:
                log.debug("centurion: dropped a late reply from %s", _reply_source(message))

    def next_event(self) -> Message | None:
        return self._events.popleft() if self._events else None

    @property
    def pending(self) -> bool:
        """Whether events are queued: a request keeps those it reads while it waits, which
        leaves the node with nothing to wake select for."""
        return bool(self._events)

    def take_event[Taken](self, pick: Callable[[Message], Taken | None],
                         deadline: float) -> Taken | None:
        """What `pick` makes of the first event it takes, out of those queued or read before
        `deadline`, the others kept; None for none by then."""
        for queued_event in self._events:
            if (taken := pick(queued_event)) is not None:
                self._events.remove(queued_event)
                return taken
        while (report := self._read(deadline)) is not None:
            if (message := self._assembled(report)) is None:
                continue
            if not _is_event(message):
                log.debug("centurion: dropped a reply from %s while an event was waited for",
                          _reply_source(message))
            elif (taken := pick(message)) is not None:
                return taken
            else:
                self._events.append(message)
        return None

    # -- requests --------------------------------------------------------

    def request(self, index: int, function: int, params: bytes = b"", *,
                timeout: float = REQUEST_TIMEOUT, retries: int = REQUEST_RETRIES) -> bytes:
        """Call one function and return its reply's parameters. Sent again after each
        `timeout` without an answer, `retries` times; an answer to any of the tries is the
        answer. Raises HidppError for an error reply, HidppTimeout for none, DeviceGone for a
        node gone, and ValueError for parameters too long, before anything is written."""
        self.pump()
        sent: list[int] = []
        for _attempt in range(retries + 1):
            sent.append(self._write(index, function, params))
            deadline = time.monotonic() + timeout
            while (report := self._read(deadline)) is not None:
                message = self._file(report)
                if message is None:
                    continue
                error = error_of(message)
                if error is not None and error[:2] == (index, function) and error[2] in sent:
                    raise error[3]
                if (message.index, message.function) == (index, function) \
                        and message.software_id in sent:
                    return message.params
                log.debug("centurion: dropped a reply from %s while index 0x%02x fn %d waited",
                          _reply_source(message), index, function)
        raise no_reply(request_name(index, function), retries)

    def lookup(self, feature_id: int, *, timeout: float = REQUEST_TIMEOUT,
               retries: int = REQUEST_RETRIES) -> Feature | None:
        """A feature id's index, type and version from the root feature's getFeature
        (function 0), or None for one the device lacks."""
        return looked_up(feature_id, self.request(ROOT_FEATURE, 0, feature_id.to_bytes(2, "big"),
                                                  timeout=timeout, retries=retries))


def bridged(index: int, function: int, software_id: int, params: bytes = b"") -> bytes:
    """sendFragment's parameters for one message to the headset: the bridge's header, the
    headset's id and the message's length, then the message, its flags byte first (Solaar's
    `[sub_cpl=0x00, sub_feat_idx, sub_func|swid, params...]`); ValueError for one past
    BRIDGED_MAX, before anything is written."""
    message = bytes([SINGLE, index, function << 4 | software_id]) + params
    if len(message) > BRIDGED_MAX:
        raise ValueError(f"{counted(len(params), 'byte')} of parameters exceed the "
                         f"{BRIDGED_MAX - HEADER:,} a bridged message carries")
    return bytes([HEADSET << 4 | len(message) >> 8, len(message) & 0xFF]) + message


class HeadsetAway(HidppTimeout):
    """The receiver took a request for its headset and the headset did not answer: it is
    off, or out of the receiver's reach."""


def _header_count(params: bytes) -> int | None:
    """The bytes the bridge's header counts after it; None for params shorter than it."""
    return (params[0] & 0x0F) << 8 | params[1] if len(params) >= BRIDGE_HEADER else None


def inner(message: Message) -> Message | None:
    """The headset's message a MessageEvent of the bridge carries, after the bridge's header
    and the message's own flags byte; None for one too short for the length its header
    gives (Solaar's _parse_bridge_response)."""
    params = message.params
    if (length := _header_count(params)) is None:
        return None
    carried = params[BRIDGE_HEADER:BRIDGE_HEADER + length]
    return message_of(carried[1:]) if len(carried) == length else None


class Bridged:
    """A headset's channel through its receiver's bridge, at `index` on `receiver`'s
    channel: a Centurion's requests, lookup and events, on the receiver's node."""

    def __init__(self, receiver: Centurion, index: int) -> None:
        self.receiver = receiver
        self.index = index
        self._software_ids = cycle(SOFTWARE_IDS)

    @property
    def framing(self) -> Framing:
        return self.receiver.framing

    @property
    def fileno(self) -> int:
        return self.receiver.fileno

    def close(self) -> None:
        self.receiver.close()

    def pump(self) -> None:
        self.receiver.pump()

    @property
    def pending(self) -> bool:
        return self.receiver.pending

    def next_event(self) -> Message | Link | None:
        """The next event of the headset's, or of the bridge's connection, the receiver's
        own events and late answers dropped."""
        while (raw := self.receiver.next_event()) is not None:
            if (said := self._said(raw)) is not None:
                return said
        return None

    def _said(self, raw: Message) -> Message | Link | None:
        if raw.index == self.index and raw.function == FN_LINK:
            # The connected headsets' descriptors, as many bytes as the header counts: none
            # for none (Solaar's listener; HeadsetControl reads `00 00` as off, `00 01` on).
            return Link(connected=bool(_header_count(raw.params)))
        said = inner(raw) if (raw.index, raw.function) == (self.index, FN_SEND) else None
        if said is not None and _is_event(said):
            return said
        log.debug("centurion: dropped the receiver's event from index 0x%02x fn %d",
                  raw.index, raw.function)
        return None

    def request(self, index: int, function: int, params: bytes = b"", *,
                timeout: float = REQUEST_TIMEOUT, retries: int = REQUEST_RETRIES) -> bytes:
        """Call one function of the headset's and return its answer's parameters. The
        receiver's acknowledgment is waited for as a request is, `retries` times; then the
        headset's answer, `timeout` long. Raises HidppError for an error, the headset's or
        the receiver's, HeadsetAway for no answer from the headset after the receiver took
        it, the rest as Centurion.request, and ValueError for parameters past BRIDGED_MAX,
        before anything is written."""
        software_id = next(self._software_ids)
        self.receiver.request(self.index, FN_SEND,
                              bridged(index, function, software_id, params),
                              timeout=timeout, retries=retries)

        def answer(raw: Message) -> Message | None:
            said = inner(raw) if (raw.index, raw.function) == (self.index, FN_SEND) else None
            if said is None:
                return None
            error = error_of(said)
            echoed = error[:3] if error is not None else (said.index, said.function,
                                                          said.software_id)
            return said if echoed == (index, function, software_id) else None

        said = self.receiver.take_event(answer, time.monotonic() + timeout)
        if said is None:
            raise HeadsetAway(f"{request_name(index, function)}: the receiver took "
                              "it and the headset did not answer")
        if (error := error_of(said)) is not None:
            raise error[3]
        return said.params

    def lookup(self, feature_id: int, *, timeout: float = REQUEST_TIMEOUT,
               retries: int = REQUEST_RETRIES) -> Feature | None:
        """The headset's root lookup of `feature_id`, through the bridge: Solaar's and
        HeadsetControl's G522 send getFeature to the headset's index 0 this way."""
        return looked_up(feature_id, self.request(ROOT_FEATURE, 0, feature_id.to_bytes(2, "big"),
                                                  timeout=timeout, retries=retries))
