#!/usr/bin/env python3
"""Generate a short dry binaural room tail as a 2-channel float WAV (stdlib only).

Usage: python3 make_room.py [OUT.wav] [--seed N]

The defaults rebuild room-150ms.wav, the room Gearbench Spatial convolves.
Decaying Gaussian noise in three bands split by Linkwitz-Riley crossovers; the
band below 500 Hz is shared by both ears (a diffuse field is coherent there),
the upper bands are independent per ear, at the same energy in both.
"""
from __future__ import annotations

import argparse
import math
import os
import random
import struct
import sys
import tempfile
from pathlib import Path

RATE = 48000
LENGTH_S = 0.150
START_S = 0.004
FADE_IN_S = 0.006
FADE_OUT_S = 0.010
XOVER_HZ = (500.0, 7000.0)
T60_S = (0.120, 0.100, 0.035)      # low, mid, high band
BAND_GAIN = (1.0, 1.0, 0.5)  # high band 6 dB down
#: The seed room-150ms.wav was made with.
SEED = 20260927


def biquad(x: list[float], kind: str, f0: float, q: float = 1 / math.sqrt(2)) -> list[float]:
    w = 2 * math.pi * f0 / RATE
    cw, alpha = math.cos(w), math.sin(w) / (2 * q)
    if kind == "lp":
        b = ((1 - cw) / 2, 1 - cw, (1 - cw) / 2)
    else:
        b = ((1 + cw) / 2, -(1 + cw), (1 + cw) / 2)
    a0, a1, a2 = 1 + alpha, -2 * cw, 1 - alpha
    b0, b1, b2 = (v / a0 for v in b)
    a1, a2 = a1 / a0, a2 / a0
    y, x1, x2, y1, y2 = [], 0.0, 0.0, 0.0, 0.0
    for s in x:
        o = b0 * s + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1, y2, y1 = x1, s, y1, o
        y.append(o)
    return y


def lr4(x: list[float], kind: str, f0: float) -> list[float]:
    return biquad(biquad(x, kind, f0), kind, f0)


def band(noise: list[float], i: int) -> list[float]:
    if i == 0:
        return lr4(noise, "lp", XOVER_HZ[0])
    if i == 1:
        return lr4(lr4(noise, "hp", XOVER_HZ[0]), "lp", XOVER_HZ[1])
    return lr4(noise, "hp", XOVER_HZ[1])


def gauss(rng: random.Random, n: int) -> list[float]:
    # Box-Muller on random(): stable per seed across Python versions; gauss() is not.
    out: list[float] = []
    while len(out) < n:
        u1, u2 = 1.0 - rng.random(), rng.random()
        r = math.sqrt(-2.0 * math.log(u1))
        out += [r * math.cos(2 * math.pi * u2), r * math.sin(2 * math.pi * u2)]
    return out[:n]


def envelope(n: int, t60: float) -> list[float]:
    start, fin, fout = (round(v * RATE) for v in (START_S, FADE_IN_S, FADE_OUT_S))
    env = [0.0] * start
    for k in range(start, n):
        t = (k - start) / RATE
        e = 10 ** (-3 * t / t60)
        if k - start < fin:
            e *= 0.5 - 0.5 * math.cos(math.pi * (k - start) / fin)
        if k >= n - fout:
            e *= 0.5 - 0.5 * math.cos(math.pi * (n - 1 - k) / fout)
        env.append(e)
    return env


def room(seed: int) -> list[list[float]]:
    """The left and right ears' samples for `seed`, at a mean energy of 1 per channel."""
    rng = random.Random(seed)  # noqa: S311 -- noise for a sound file, the same for a seed
    n = round(LENGTH_S * RATE)

    def shaped(i: int) -> list[float]:
        return [BAND_GAIN[i] * v * e
                for v, e in zip(band(gauss(rng, n), i), envelope(n, T60_S[i]), strict=True)]

    low = shaped(0)
    ears = [[low, shaped(1), shaped(2)] for _ear in range(2)]
    # Each ear's draw of a band takes the mean energy of the two draws, and one norm scales
    # both channels: a norm per channel would set the shared low band louder in one ear.
    for i in (1, 2):
        energy = [sum(v * v for v in ear[i]) for ear in ears]
        for ear, own in zip(ears, energy, strict=True):
            scale = math.sqrt(sum(energy) / 2 / own)
            ear[i] = [v * scale for v in ear[i]]
    chans = [[sum(s) for s in zip(*ear, strict=True)] for ear in ears]
    norm = 1 / math.sqrt(sum(v * v for chan in chans for v in chan) / 2)
    return [[v * norm for v in chan] for chan in chans]


def wav(chans: list[list[float]]) -> bytes:
    """A 2-channel 32-bit float WAV file of `chans`, the left ear and the right."""
    data = b"".join(struct.pack("<2f", *frame) for frame in zip(*chans, strict=True))
    fmt = struct.pack("<HHIIHH", 3, 2, RATE, RATE * 8, 8, 32)
    body = (b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt
            + b"fact" + struct.pack("<II", 4, len(chans[0]))
            + b"data" + struct.pack("<I", len(data)) + data)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def write(out: Path, blob: bytes) -> None:
    """Replace `out` with `blob`, or raise OSError and leave it as it was."""
    # A failed write must not leave a short room where the old one was: render() takes it.
    # mkstemp's random name, opened O_EXCL, keeps a link planted beside OUT from taking
    # the write; its prefix is fixed, as OUT's name in it would pass NAME_MAX for a name
    # OUT may have. It makes the file 0600, so the open file gets 0666 less the umask,
    # the mode a plain open would give.
    fd, name = tempfile.mkstemp(dir=out.parent, prefix=".make_room-", suffix=".tmp")
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as f:
            umask = os.umask(0)
            os.umask(umask)
            os.fchmod(f.fileno(), 0o666 & ~umask)
            f.write(blob)
            # On disk before the replace: a power loss leaves the old room or the new.
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(out)
    finally:
        tmp.unlink(missing_ok=True)


def main() -> None:
    # python3 -OO strips the docstring.
    parser = argparse.ArgumentParser(description=(__doc__ or "").partition("\n")[0])
    parser.add_argument("out", nargs="?", type=Path, metavar="OUT.wav",
                        default=Path(__file__).with_name("room-150ms.wav"),
                        help="the file to write (default: room-150ms.wav beside this script)")
    parser.add_argument("--seed", type=int, metavar="N", default=SEED,
                        help="the noise's seed (default: %(default)s)")
    args = parser.parse_args()
    out, seed = args.out, args.seed
    chans = room(seed)
    try:
        write(out, wav(chans))
    except OSError as err:
        sys.exit(f"{parser.prog}: cannot write {out}: {err.strerror}")
    print(out, len(chans[0]), "frames,", out.stat().st_size, "bytes, seed", seed)


if __name__ == "__main__":
    main()
