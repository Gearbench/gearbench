"""The rows of a headset page the window runs on this computer, never through the daemon:
Noise suppression and Test the microphone (noise_rows), Mic input (volume_rows) and Input
level (input_meter_row), which end each page's Microphone group in that order, and Game
output and Chat output (volume_rows) where a page shows them. Building it runs nothing."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence

from gi.repository import Gtk

from .. import pipewire_client
from ..system_volume import MIC
from .input_meter_row import InputMeterRow
from .noise_rows import NoiseRows
from .volume_rows import TITLES, VolumeRows
from .worker import DaemonWorker


class ComputerRows:
    """A headset page's Noise suppression (`noise`), Input level (`meter`) and volume rows
    (`volumes`), each told of the headset the page names and of its close, and all but Input
    level of its polls.
    `run` runs their programs; `kept_up` is told of a switch or a set that failed
    (Window.kept_up); `inset` and `words` are the volume rows' (VolumeRows)."""

    def __init__(self, worker: DaemonWorker, toast: Callable[..., object],
                 kept_up: Callable[[], None], run: pipewire_client.Run = subprocess.run,
                 inset: int = 0, words: Sequence[str] = tuple(TITLES)) -> None:
        self.noise = NoiseRows(worker, toast, kept_up, run)
        self.meter = InputMeterRow(worker, run)
        self.volumes = VolumeRows(worker, toast, kept_up, run, inset=inset, words=words)

    @property
    def mic_rows(self) -> tuple[Gtk.Widget, ...]:
        """The rows that end the Microphone group, in their order."""
        return (self.noise.row, self.noise.test_row, self.volumes.rows[MIC], self.meter.row)

    @property
    def output_rows(self) -> tuple[Gtk.Widget, ...]:
        """Game output and Chat output, in their order, which the A50s' pages put under
        Balance."""
        return tuple(row for word, row in self.volumes.rows.items() if word != MIC)

    def show_headset(self, key: str | None) -> None:
        """The headset the page's reply names, None for a model this install does not know:
        each row follows it; for none, Noise suppression and the volumes hide and Input level
        stops."""
        self.noise.show_headset(key)
        self.meter.show_headset(key)
        self.volumes.show_headset(key)

    def gone(self) -> None:
        """The page's headset is gone: Input level and the volumes name none. Noise
        suppression stays on the last headset, since its unit runs without the headset."""
        self.meter.show_headset(None)
        self.volumes.show_headset(None)

    def refresh(self) -> None:
        """The page's poll: the noise suppression's state and the volumes are read again where
        their rows show (NoiseRows.refresh, VolumeRows.refresh)."""
        self.noise.refresh()
        self.volumes.refresh()

    @property
    def busy(self) -> bool:
        """A switch of the noise suppression or a volume set is out, which the window's close
        waits for."""
        return self.noise.busy or self.volumes.busy

    def flush(self) -> bool:
        """Set now a volume that still waits, for the window's close; True if one did."""
        return self.volumes.flush()

    def close(self) -> None:
        """The window's close: each row stops for good."""
        self.noise.close()
        self.meter.close()
        self.volumes.close()
