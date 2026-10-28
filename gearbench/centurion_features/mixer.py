"""0x0800, G HUB's FeatureC0800MixerAudio: on the A50 X the stream port's mix, the sound
its Stream Out sends, and the state of the microphone's boom (G HUB's captures of
2026-10-21, B3, B4-12, B5 and B6b).

The mix, read with function 5 and the byte 00, is the stream port's level, a byte G HUB
sends back as it read it (00), a count, then for each source its id, its level and 01 for
muted: `1a 00 04 00 10 01 01 10 00 02 10 01 03 10 00` as G HUB found it, the stream port at
80 %, each source at 50 %, the microphone's output and Bluetooth muted. A level is 0 to 32,
32 G HUB's 100 %. G HUB writes the whole mix with function 6, 00 then the read's bytes, and
the base echoes it as the function 2 event with that 00 before it. Source 01 is Game and 03
Voice, as G HUB's sliders moved them; 00, the microphone's output, and 02, Bluetooth, are
the two G HUB showed muted and disconnected, taken by elimination.

The boom: the function 0 event, `00 00 00 0b` with the boom down and the microphone live,
`00 00 00 0a` with it up and muted, bit 0 of its last byte (B5). Function 2, sent with no
parameter, answered `00 00 00 0a` at each of G HUB's starts, the boom up both times, the
event's layout, which is how it is read here: assumed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from ..hidpp import UnreadableAnswer
from ..wording import counted
from .setting import unreadable

ID = 0x0800
WORDS = "stream mix"
FN_BOOM = 2
FN_READ, FN_WRITE = 5, 6
#: The event that echoes a write of the mix.
EVENT_MIX = 2
#: The byte the read of the mix is sent with and its write starts with.
OUTPUT = 0x00
#: The boom's byte, and its bit that says the microphone is live.
BOOM_AT, LIVE = 3, 0x01
LEVELS = 32
#: The sources by their ids, by the words the socket gives them.
SOURCES = {"mic_out": 0x00, "game": 0x01, "bluetooth": 0x02, "voice": 0x03}
#: The sources in the order every view lists them, the window's rows' (a50x_page.MIX_TITLES),
#: the GUIDE's and the man page's: Game, Voice, Mic, Bluetooth, after the stream port.
ORDER = ("game", "voice", "mic_out", "bluetooth")
#: The stream port's level, which is no source, by its socket word.
STREAM = "stream"
#: The bytes before the sources, the stream port's level, the byte after it and the count,
#: and each source's.
HEAD, SOURCE_BYTES = 3, 3
#: A word of the mix where it is said otherwise than by its socket word: the microphone's
#: level is Mic, as on the A50 Gen 4's stream mix, since every source of the mix goes out.
SHOWN = {STREAM: "stream port", "mic_out": "mic"}


@dataclass(frozen=True)
class Source:
    """One source of the mix: its id, its level, 0 to LEVELS, and whether it is muted."""

    id: int
    level: int
    muted: bool


@dataclass(frozen=True)
class Mix:
    """The stream port's mix: its level, the byte after it as read, and the sources."""

    stream: int
    after: int
    sources: tuple[Source, ...]

    def with_level(self, source: str, level: int) -> Mix:
        """The mix with `source`'s level, or the stream port's, at `level`."""
        if source == STREAM:
            return replace(self, stream=level)
        return replace(self, sources=tuple(replace(each, level=level)
                                           if each.id == SOURCES[source] else each
                                           for each in self.sources))

    def with_muted(self, source: str, muted: bool) -> Mix:
        return replace(self, sources=tuple(replace(each, muted=muted)
                                           if each.id == SOURCES[source] else each
                                           for each in self.sources))

    def lists(self, source: str) -> bool:
        """Whether the mix has `source`: the stream port, or one of SOURCES' words it lists."""
        return source == STREAM or any(each.id == SOURCES[source] for each in self.sources)


def reply(mix: Mix) -> dict:
    """The stream mix as the `a50x` reply gives it, the shape `words` reads: the stream
    port's level, and each source the mix lists that SOURCES has a word for, by that word,
    with its level and whether it is muted."""
    named = {source: word for word, source in SOURCES.items()}
    return {"stream": mix.stream,
            "sources": {named[source.id]: {"level": source.level, "muted": source.muted}
                        for source in mix.sources if source.id in named}}


def words(mix: Mapping) -> str:
    """The stream mix in words, from the shape `reply` gives it, its `stream` level and each
    source's `level` and `muted` by its socket word, in ORDER: "stream port 26, game 16, mic
    16 muted (of 32)"."""
    sources = mix["sources"]
    parts = [f"{SHOWN[STREAM]} {mix['stream']}",
             *(f"{SHOWN.get(word, word)} {sources[word]['level']}"
               f"{' muted' if sources[word]['muted'] else ''}"
               for word in ORDER if word in sources)]
    return f"{', '.join(parts)} (of {LEVELS})"


def mix_of(reply: bytes) -> Mix:
    """Function 5's reply as a Mix; UnreadableAnswer for one too short for its count, a level
    past LEVELS or a mute byte other than 00 and 01."""
    if len(reply) < HEAD or len(reply) < HEAD + reply[2] * SOURCE_BYTES:
        raise unreadable(WORDS, reply)
    entries = [reply[at:at + SOURCE_BYTES]
               for at in range(HEAD, HEAD + reply[2] * SOURCE_BYTES, SOURCE_BYTES)]
    if reply[0] > LEVELS or any(level > LEVELS or muted > 1 for _id, level, muted in entries):
        raise unreadable(WORDS, reply)
    return Mix(reply[0], reply[1],
               tuple(Source(source, level, muted == 1) for source, level, muted in entries))


def echoed(params: bytes) -> Mix:
    """The function 2 event's Mix, which echoes a write: the byte `written` puts first, then
    the read's layout (mix_of)."""
    return mix_of(params[1:])


def written(mix: Mix) -> bytes:
    """Function 6's parameters for `mix`: OUTPUT, then the read's layout."""
    return bytes([OUTPUT, mix.stream, mix.after, len(mix.sources),
                  *(byte for source in mix.sources
                    for byte in (source.id, source.level, int(source.muted)))])


def live(reply: bytes) -> bool:
    """Whether the boom's state, its event's or function 2's reply, says the microphone is
    live; UnreadableAnswer for one too short."""
    if len(reply) <= BOOM_AT:
        raise UnreadableAnswer(f"the microphone's boom answered {counted(len(reply), 'byte')}, "
                               f"not {BOOM_AT + 1}")
    return bool(reply[BOOM_AT] & LIVE)
