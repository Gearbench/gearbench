"""The input meter of a headset's microphone: its level, read live from the microphone's node
in PipeWire by pw-record, PipeWire's own recorder (pw-cat(1)), for the Input level row of
each headset page's Microphone group (gui/input_meter_row.py).

The level is the one GNOME's Sound settings show for an input (gnome-control-center's
panels/sound/cc-level-bar.c, read 2026-10-24): the loudest sample of each 1/25 s as a
fraction of full scale, the bar moving 0.3 of the way to each, on a linear bar from 0 to 1.
`Meter` takes it from the samples pw-record writes. The meter reads levels only: the samples
come through a pipe into memory, each window's peak is taken and its samples are dropped, and
nothing is written or sent anywhere.

Standard library only. pw-dump goes through a `run` argument (pipewire_client.dumped) and
pw-record through a `spawn` one; `Recording` holds all of pw-record: its start, its reads and
its stop. A failure is a pipewire_client.StepError in words.
"""

from __future__ import annotations

import array
import json
import os
import subprocess
from collections.abc import Callable
from typing import IO, cast

from . import pipewire_client, pipewire_unit, spatial

#: PipeWire's recorder, pw-cat in record mode, from the package that has pw-dump.
PROGRAM = "pw-record"
#: The samples pw-record writes: signed 16-bit, in the machine's byte order as array's "h"
#: reads them, one channel, at 48000 Hz. pw-cat writes raw samples to stdout with stdio's
#: fwrite (pw-cat.c, PipeWire 1.6.2), whose buffer glibc sizes at a pipe's st_blksize
#: (libio/filedoalloc.c), 4096 bytes (measured 2026-10-24), so they come in blocks of 2048
#: samples, 42.7 ms at 48000 Hz, about 23 a second, where 16000 Hz would give 8. 48000 Hz
#: is PipeWire's default clock rate, so pw-record resamples nothing there.
SAMPLE_FORMAT, TYPECODE, SAMPLE_BYTES = "s16", "h", 2
RATE = 48000
FULL_SCALE = 2 ** 15
#: GNOME's level bar: 25 peaks a second, its stream's rate, and how far each peak moves the
#: bar, its SMOOTHING (cc-level-bar.c).
UPDATES = 25
SMOOTHING = 0.3
#: One window's samples, 40 ms, and its bytes.
WINDOW = RATE // UPDATES
WINDOW_BYTES = WINDOW * SAMPLE_BYTES
#: The most taken from the pipe at a time: a pipe holds 64 KiB on Linux (pipe(7)).
READ_MAX = 2 ** 16
#: The most kept of what pw-record said on stderr, read once it has ended.
SAID_MAX = 4096
#: How long a stop waits for pw-record to exit after SIGTERM, which ends its loop (pw-cat.c's
#: do_quit), before it kills it: a stop runs on the window's thread.
STOP_WAIT = 0.5
#: The stream's props (pw-record --properties). It records the headset's microphone or none:
#: no other input in its place where the node is not there, and no move to another that a
#: program asks for; WirePlumber destroys the stream where its node is gone, and pw-record
#: ends (WirePlumber 0.5.13's linking/find-defined-target.lua, the docs' Linking Policy).
#: The names say whose stream it is where the streams are listed, in pw-dump or a mixer.
PROPERTIES = {"node.dont-fallback": True, "node.dont-move": True,
              "application.name": "Gearbench", "node.name": "gearbench-input-meter",
              "media.name": "Gearbench input meter"}
Spawn = Callable[..., subprocess.Popen]


def argv(node: str) -> list[str]:
    """pw-record's command line for the node named `node`: raw samples on its stdout, "-",
    never a file. --target takes a node name (pw-cat(1)), which WirePlumber matches by
    node.name."""
    return [PROGRAM, f"--target={node}", "--raw", f"--format={SAMPLE_FORMAT}", f"--rate={RATE}",
            "--channels=1", f"--properties={json.dumps(PROPERTIES)}", "-"]


def find(run: pipewire_client.Run, key: str) -> str | None:
    """The node name of the microphone of the headset of spatial.HEADSET_KEYED's `key` in a
    fresh pw-dump (pipewire_unit.mic_of); None while there is none. StepError when pw-dump
    fails or its objects are of a shape pipewire_client.shaped refuses."""
    headset = spatial.HEADSET_KEYED[key]
    return pipewire_client.dumped(run, lambda objects: pipewire_unit.mic_of(objects, headset))


def peak(window: bytes | bytearray) -> float:
    """The loudest sample of `window`, s16 samples, as a fraction of full scale."""
    samples = array.array(TYPECODE, window)
    return max(max(samples), -min(samples)) / FULL_SCALE


class Meter:
    """GNOME's level of the samples fed in, in any cut: `level`, 0 to 1."""

    def __init__(self) -> None:
        self.level = 0.0
        #: The samples of the window not yet whole.
        self._held = bytearray()

    def feed(self, data: bytes) -> float | None:
        """The level once `data` completes one window or more, each moving it in turn; None
        while no window is whole."""
        self._held += data
        whole = len(self._held) // WINDOW_BYTES * WINDOW_BYTES
        if not whole:
            return None
        for start in range(0, whole, WINDOW_BYTES):
            loudest = peak(self._held[start:start + WINDOW_BYTES])
            self.level = loudest * SMOOTHING + self.level * (1 - SMOOTHING)
        del self._held[:whole]
        return self.level


class Recording:
    """pw-record reading one node into a pipe, and the Meter of what came through it."""

    def __init__(self, process: subprocess.Popen) -> None:
        self._process = process
        # Pipes, as `start` asks for both.
        self._samples = cast("IO[bytes]", process.stdout)
        self._said = cast("IO[bytes]", process.stderr)
        self.meter = Meter()

    @classmethod
    def start(cls, node: str, spawn: Spawn) -> Recording:
        """pw-record started on the node named `node`, its pipe read without blocking.
        pipewire_client.NotInstalled without pw-record, a StepError when it does not start."""
        try:
            process = spawn(argv(node), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
        except OSError as err:
            raise pipewire_client.not_run(PROGRAM, err) from None
        recording = cls(process)
        os.set_blocking(recording.fileno(), False)
        return recording

    def fileno(self) -> int:
        """The pipe's end the samples are read from."""
        return self._samples.fileno()

    def read(self) -> tuple[float | None, bool]:
        """What the pipe holds now through the meter: its level once a window is whole, else
        None; and whether pw-record has closed the pipe, as it does as it ends."""
        try:
            data = os.read(self.fileno(), READ_MAX)
        except BlockingIOError:
            return None, False
        if not data:
            return None, True
        return self.meter.feed(data), False

    def stop(self) -> str:
        """End pw-record, if it still runs, and wait for it: SIGTERM, then SIGKILL past
        STOP_WAIT; the pipe is closed before the wait, so a pw-record stuck in a write ends too.
        How it ended, its stderr on one line, else its exit (pipewire_client.program_words)."""
        process = self._process
        process.terminate()
        self._samples.close()
        try:
            process.wait(STOP_WAIT)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        said = self._said.read(SAID_MAX).decode("utf-8", "replace")
        self._said.close()
        return pipewire_client.program_words(
            subprocess.CompletedProcess(process.args, process.returncode, "", said), PROGRAM)
