# Astro A50 Spatial: data

Astro A50 Spatial is Gearbench's 7.1 output for headphones on the Astro A50's
Game output. Its user unit writes a PipeWire filter chain from the
template here at every start and runs it. This directory is read from a
checkout, or from `/usr/share/gearbench/spatial` when Gearbench is installed
as a package. The package's copy leaves out `gearbench-spatial.service.in`
and `gearbench-spatial-login.service`, which the package installs as units
in `/usr/lib/systemd/user`, and `make_room.py` and `SHA256SUMS`, which are in
a checkout only.

| File | What it is |
|---|---|
| `gearbench-spatial.conf.in` | The filter chain's template: 16 convolvers, the mixers and the sink's names |
| `gearbench-spatial.service.in` | The user unit's template, from which `install.sh` or the package's build writes the unit |
| `gearbench-spatial-login.service` | The user unit that starts `gearbench-spatial.service` at login when the base station is already plugged in; installed unchanged |
| `room-150ms.wav` | The room tail, 2 channels, 32-bit float, 48 kHz, 7,200 frames |
| `make_room.py` | The program that made `room-150ms.wav` |
| `sadie-ii/D1/`, `sadie-ii/D2/` | SADIE II's head-related impulse responses at the seven 7.1 angles, one WAV per angle |
| `sadie-ii/LICENSE.txt` | SADIE II's license, the Apache License 2.0 |
| `SHA256SUMS` | The SHA-256 of every WAV here, for `sha256sum -c` |

## SADIE II

SADIE II Database v2-2, subjects D1 (Neumann KU100) and D2 (KEMAR), The Audio
Lab, University of York. Copyright 2018 University of York, Apache License 2.0.
https://doi.org/10.5281/zenodo.12092466. Paper: Armstrong, Thresh, Murphy and
Kearney, "A Perceptual Evaluation of Individual and Non-Individual HRTFs: A
Case Study of the SADIE II Database", Applied Sciences 8(11), 2029 (2018),
https://doi.org/10.3390/app8112029.

The files in `sadie-ii/` are SADIE II's own, unmodified: the 48 kHz 24-bit
HRIR WAVs from `D1.zip` and `D2.zip` (`<subject>/<subject>_HRIR_WAV/48K_24bit/`)
at azimuths 30 (front left), 330 (front right), 0 (center), 90 (side left),
270 (side right), 150 (rear left) and 210 (rear right), elevation 0. Each is
2 channels, left ear first, 256 frames. On 2026-09-27 their samples were
checked equal to the same directions in the record's V2-2 SOFA files, both
ears, both subjects. `LICENSE.txt` is the record's file byte for byte, CRLF
line ends included (MD5 `0ba5044c64ef53cb0189c9546081e228`). SADIE II ships
no NOTICE file.

The head is chosen in the Headset tab or with
`gearbench-ctl headset-spatial --head ku100` or `--head kemar`: the KU100 is
D1, the KEMAR is D2.

`cd spatial && sha256sum -c SHA256SUMS` checks every WAV here. The license is
at `https://zenodo.org/records/12092466/files/LICENSE.txt?download=1`.

## The room

`room-150ms.wav` is Gearbench's own work, under the project's license,
GPL-3.0-or-later. `make_room.py` makes it with the standard library alone:

```
python3 spatial/make_room.py              # writes spatial/room-150ms.wav
python3 spatial/make_room.py OUT.wav --seed 20260927
```

It is Gaussian noise from seed 20260927, split by Linkwitz-Riley crossovers
at 500 Hz and 7 kHz into three bands that decay on their own: T60 120 ms
below 500 Hz, 100 ms to 7 kHz, and 35 ms above, the top band 6 dB down. The
band below 500 Hz is the same in both ears; the other two differ per ear and
have the same energy in each. It starts 4 ms in, about 2 ms after the HRIRs'
direct sound, with a 6 ms fade-in and a 10 ms fade-out, and is 150 ms long.
One gain scales both channels to a mean energy of 1: 0.990 left, 1.010
right. The noise comes from the `random()` method of one
`random.Random(seed)` through Box-Muller, since Python keeps that sequence
the same across versions for a given seed and does not promise it for
`random.gauss()`.

Measured on 2026-10-03, in bands cut from each channel's FFT (Schroeder
integration, T20, the two ears averaged): T60 128 ms at 125-500 Hz, 106 ms
at 500 Hz-2 kHz, 107 ms at 2-8 kHz, 110 ms at 8-12 kHz and 39 ms at
12-20 kHz. In the same bands the left and right channels correlate at +0.95
at 125-500 Hz and between -0.14 and +0.12 above.

With the room on (Immersive, a room gain of 0.1 against 0.5 for the direct
sound), a center source's direct sound through the KU100 is 10.8 dB above
the room at the left ear and 11.8 dB at the right.

## The filter chain template's placeholders

`render()` in `gearbench/spatial.py` fills three in one pass, so a folder
named like one stays in its path, and refuses a template with any other
`@NAME@`:

| Placeholder | Filled with |
|---|---|
| `@HRIR@` | The absolute path of `sadie-ii/D1` or `sadie-ii/D2`, by the head |
| `@ROOM@` | The absolute path of `room-150ms.wav` |
| `@ROOM_GAIN@` | The out mixers' "Gain 2": `0.0` for Competitive, `0.1` for Immersive |

It also refuses a path holding `"`, `\` or a character below U+0020 (a tab,
say), a missing WAV, a file that is not a WAV or has no fmt or data chunk, and
a WAV that is not 2 channels at 48 kHz.
