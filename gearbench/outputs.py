"""Outputs: Gearbench Game, Chat and Media, three stereo outputs that gearbench-outputs.service
plays on the headset Gearbench Spatial plays to (gearbench.outputs_unit), and the words that
move the balance between voice and game, which the unit's process takes on a datagram socket
in the runtime folder: the outputs' names (OUTPUTS), the socket's path (socket_path), the
words' grammar (parse), the balance's stops (STOPS, moved), the gains a balance gives Game
and Chat (gains), and the send and its answer (send, ask, refusal).

Here, before the daemon, so that the daemon, which runs no PipeWire program, can send a word
for a key bound to the balance. Standard library only.
"""

from __future__ import annotations

import contextlib
import socket
from dataclasses import dataclass
from pathlib import Path

from . import pipewire_client, runtime_folder
from .astro import BALANCE_RANGE
from .wording import reason

UNIT = "gearbench-outputs.service"
#: What the window and the CLI call the three outputs together.
DESCRIPTION = "Gearbench Outputs"


@dataclass(frozen=True)
class Output:
    """One of the three: its key, the CLI's word for it; its sink and the sink's playback
    stream by node name; and its description, which GNOME's Sound settings and the process's
    journal name it by. WirePlumber keeps a sink's volume, and a stream's choice of output,
    by these names, so they never change."""

    key: str
    sink: str
    output: str
    description: str


GAME = Output("game", "gearbench-game", "gearbench-game.output", "Gearbench Game")
CHAT = Output("chat", "gearbench-chat", "gearbench-chat.output", "Gearbench Chat")
MEDIA = Output("media", "gearbench-media", "gearbench-media.output", "Gearbench Media")
#: In the order the conf holds them and the CLI lists them.
OUTPUTS = (GAME, CHAT, MEDIA)
OUTPUT_KEYED = {output.key: output for output in OUTPUTS}

#: The socket's name in the runtime folder.
SOCKET_NAME = "gearbench-outputs.sock"
#: The longest word, in bytes, leading zeros included: the longest one sent is "set 255", 7.
WORD_MAX = 16
#: The words: move one stop toward the game, one toward the chat, and set a balance.
GAME_WORD, CHAT_WORD, SET_WORD = "game", "chat", "set"
#: How a word is answered to a sender that has an address of its own (ask): the balance
#: after it, or why it was refused.
BALANCE_ANSWER = "balance {}"
REFUSED = "refused"
REFUSED_ANSWER = f"{REFUSED} {{}}"
#: The longest answer read: a refusal's words with the word itself, escaped.
ANSWER_MAX = 1024
#: The balance's stops on the A50's scale, 0 all voice and 255 all game (astro.BALANCE_RANGE),
#: so that one scale serves both: 13, as the A50 X's dial has 0 to 12, each pair mirrored
#: about the middle summing to 255. Written out: round(k * 255 / 12) gives 212 at k = 10.
STOPS = (0, 21, 42, 64, 85, 106, 128, 149, 170, 191, 213, 234, 255)
#: The balance before any word: the middle stop, where game and voice are each at full.
CENTER = STOPS[len(STOPS) // 2]
#: How far a side's gain is from the other side's end when it reaches full, 1.0: Game at 127
#: and Chat at 128, so both are at full on either middle value and neither is ever above it.
FULL_AT = 127
#: The words of a refused word, each after REFUSED_ANSWER; NOT_A_WORD's by the word, as
#: repr gives it.
OVER_MAX = f"a word is at most {WORD_MAX} bytes"
NOT_ASCII = "a word is ASCII"
NOT_A_WORD = (f"{{}} is not a word of {DESCRIPTION}: {GAME_WORD}, {CHAT_WORD} or "
              f"{SET_WORD} {BALANCE_RANGE[0]} to {BALANCE_RANGE[1]}")
#: Why a word was not taken: no answer, by the seconds waited; a queue that is full; no
#: process to take it; and a word otherwise not sent (a socket not made, or a connect, bind or
#: send failed), by why.
NO_ANSWER = f"{DESCRIPTION} did not answer within {{:g}} s"
BUSY = f"{DESCRIPTION} is busy; try again in a moment"
NOT_RUNNING = (f"{DESCRIPTION} is off: switch it on with "
               "`gearbench-ctl headset-outputs on`")
NOT_SENT = f"could not reach {DESCRIPTION}: {{}}"


class SocketRefused(Exception):
    """The runtime folder is refused for the socket, in words: private_runtime's, a failed
    stat on the way included."""


class NotTaken(Exception):
    """ask's word not sent or not answered, in words."""


class WordRefused(ValueError):
    """A datagram that is not a word of the grammar, in words: OVER_MAX, NOT_ASCII or
    NOT_A_WORD's."""


@dataclass(frozen=True)
class Step:
    """A word that moves the balance one stop toward the game (GAME_WORD) or the voice
    (CHAT_WORD)."""

    toward: str

    def __str__(self) -> str:
        return self.toward


@dataclass(frozen=True)
class SetTo:
    """SET_WORD's word: a balance from 0 to 255, which takes its nearest stop."""

    balance: int

    def __str__(self) -> str:
        return f"{SET_WORD} {self.balance}"


Word = Step | SetTo


@dataclass(frozen=True)
class Gains:
    """The gain of Gearbench Game's chain and of Gearbench Chat's, each from 0 to 1.0, as a
    balance gives them (gains). Media's is always 1.0."""

    game: float
    chat: float


def gains(balance: int) -> Gains:
    """Game's and Chat's gains at `balance`, 0 all voice to 255 all game: each side's share,
    its distance from the other side's end over FULL_AT and at most 1.0, cubed, so that one
    stop from the middle puts the side it moves from 4.7 dB down (the owner's decision of
    2026-10-28, by ear on a G933; the share itself gave 1.6 dB, too little to hear)."""
    return Gains(min(1.0, balance / FULL_AT) ** 3,
                 min(1.0, (BALANCE_RANGE[1] - balance) / FULL_AT) ** 3)


def socket_path() -> Path:
    """The socket in the runtime folder, which runtime_folder.private_runtime checks first:
    another account that could put a folder of its own there could take the words.
    SocketRefused otherwise, a folder on the way that stat cannot look at included."""
    return runtime_folder.private_runtime(
        SocketRefused, runtime_folder.not_set(f"{DESCRIPTION}' socket has"),
        DESCRIPTION) / SOCKET_NAME


def parse(data: bytes) -> Word:
    """The word a datagram holds: GAME_WORD, CHAT_WORD or SET_WORD and a whole number from 0 to
    255 in decimal, one space between, and nothing else. WordRefused otherwise."""
    if len(data) > WORD_MAX:
        raise WordRefused(OVER_MAX)
    if not data.isascii():
        raise WordRefused(NOT_ASCII)
    text = data.decode("ascii")
    if text in (GAME_WORD, CHAT_WORD):
        return Step(text)
    verb, _space, number = text.partition(" ")
    # ASCII by now, so isdigit takes 0 to 9 alone; a sign or a space is no digit.
    if (verb == SET_WORD and number.isdigit()
            and BALANCE_RANGE[0] <= (balance := int(number)) <= BALANCE_RANGE[1]):
        return SetTo(balance)
    raise WordRefused(NOT_A_WORD.format(repr(text)))


def moved(balance: int, word: Word) -> int:
    """The balance after `word`: SET_WORD's balance at its nearest stop, the lower of two as
    near; else one stop past `balance` toward the game or the voice, an end stop staying
    put."""
    if isinstance(word, SetTo):
        return min(STOPS, key=lambda stop: (abs(stop - word.balance), stop))
    if word.toward == GAME_WORD:
        return next((stop for stop in STOPS if stop > balance), STOPS[-1])
    return next((stop for stop in reversed(STOPS) if stop < balance), STOPS[0])


def send(sock: socket.socket, word: Word, path: Path) -> None:
    """`word` from `sock` to the socket at `path`, never waiting: a queue that is full is
    BlockingIOError, a socket not there FileNotFoundError and one no process holds
    ConnectionRefusedError, each an OSError the caller says (unsent)."""
    sock.sendto(str(word).encode("ascii"), socket.MSG_DONTWAIT, str(path))


def unsent(err: OSError) -> str:
    """Why a word was not sent, in words: BUSY for a full queue, NOT_RUNNING for no socket or
    one no process holds, the unit not running, else NOT_SENT's."""
    if isinstance(err, BlockingIOError):
        return BUSY
    if isinstance(err, (FileNotFoundError, ConnectionRefusedError)):
        return NOT_RUNNING
    return NOT_SENT.format(reason(err))


def ask(word: Word, timeout: float = pipewire_client.SETTLE) -> str:
    """`word` sent to the unit's process, and its answer (BALANCE_ANSWER or REFUSED_ANSWER),
    waited for up to `timeout`. SocketRefused for the runtime folder, and NotTaken, in words,
    for a word not sent or not answered in time."""
    path = socket_path()
    with contextlib.ExitStack() as stack:
        try:
            sock = stack.enter_context(socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM))
            # Connected before it has an address, so that the kernel queues a datagram from
            # the process's socket alone (connect(2)): any account may send to an abstract
            # address, and a sender's name proves nothing, since another account can bind
            # this path in a mount namespace of its own. Then bound to an abstract address the
            # kernel picks (unix(7), "Autobind feature"), which the process answers to.
            sock.connect(str(path))
            sock.bind("")
            send(sock, word, path)
        except OSError as err:
            raise NotTaken(unsent(err)) from None
        sock.settimeout(timeout)
        try:
            return sock.recv(ANSWER_MAX).decode("ascii", "replace")
        except TimeoutError:
            raise NotTaken(NO_ANSWER.format(timeout)) from None


def refusal(answer: str) -> str | None:
    """Why the unit's process refused a word, from ask's answer (REFUSED_ANSWER); None for
    the balance it answers once it took the word (BALANCE_ANSWER)."""
    verb, _space, rest = answer.partition(" ")
    return rest if verb == REFUSED else None
