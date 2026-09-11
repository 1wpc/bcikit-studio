"""Reusable live and replay waveform visualization."""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtWidgets import QVBoxLayout, QWidget

from bcikit.models import SampleBatch

from .styles import colors

CHANNEL_COLORS = (
    "#2F80ED",
    "#00A9C0",
    "#27AE60",
    "#F2A93B",
    "#EB5757",
    "#9B51E0",
    "#2D9CDB",
    "#56CCF2",
)

# Each channel occupies one vertical unit centered on its channel index. Keeping
# a small gutter on both sides makes neighbouring tracks readable even when an
# electrode saturates or an artefact is many times larger than the selected scale.
TRACK_HALF_HEIGHT = 0.42


class WaveformBuffer:
    def __init__(
        self, sample_rate_hz: int = 250, channel_count: int = 8, seconds: int = 10
    ) -> None:
        self.sample_rate_hz = sample_rate_hz
        self.channel_count = channel_count
        self.seconds = seconds
        self._raw = np.empty((0, channel_count), dtype=np.float64)
        self._filtered = np.empty((0, channel_count), dtype=np.float64)
        self._timestamps = np.empty(0, dtype=np.uint64)

    @property
    def capacity(self) -> int:
        return self.sample_rate_hz * self.seconds

    @property
    def sample_count(self) -> int:
        return int(self._raw.shape[0])

    def reset(self, sample_rate_hz: int, channel_count: int) -> None:
        self.sample_rate_hz = sample_rate_hz
        self.channel_count = channel_count
        self._raw = np.empty((0, channel_count), dtype=np.float64)
        self._filtered = np.empty((0, channel_count), dtype=np.float64)
        self._timestamps = np.empty(0, dtype=np.uint64)

    def set_seconds(self, seconds: int) -> None:
        self.seconds = seconds
        self._trim()

    def append(self, batch: SampleBatch, filtered: np.ndarray) -> None:
        if batch.channel_count != self.channel_count or batch.sample_rate_hz != self.sample_rate_hz:
            self.reset(batch.sample_rate_hz, batch.channel_count)
        self._raw = np.concatenate((self._raw, batch.microvolts), axis=0)
        self._filtered = np.concatenate((self._filtered, filtered), axis=0)
        self._timestamps = np.concatenate((self._timestamps, batch.timestamps_us))
        self._trim()

    def load(self, batch: SampleBatch) -> None:
        self.reset(batch.sample_rate_hz, batch.channel_count)
        self._raw = batch.microvolts[-self.capacity :].copy()
        self._filtered = self._raw.copy()
        self._timestamps = batch.timestamps_us[-self.capacity :].copy()

    def _trim(self) -> None:
        excess = max(0, self._raw.shape[0] - self.capacity)
        if excess:
            self._raw = self._raw[excess:]
            self._filtered = self._filtered[excess:]
            self._timestamps = self._timestamps[excess:]

    def values(self, filtered: bool) -> tuple[np.ndarray, np.ndarray]:
        values = self._filtered if filtered else self._raw
        if not len(self._timestamps):
            return np.empty(0), values
        x = (self._timestamps.astype(np.float64) - float(self._timestamps[-1])) / 1_000_000.0
        return x, values

    def snapshot(self, seconds: float = 4.0, filtered: bool = False) -> np.ndarray:
        values = self._filtered if filtered else self._raw
        sample_count = max(1, int(round(self.sample_rate_hz * seconds)))
        return values[-sample_count:].copy()


class WaveformView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        pg.setConfigOptions(antialias=False, useOpenGL=False)
        self.buffer = WaveformBuffer()
        self.filtered = False
        self.paused = False
        self.scale_mode = "uniform"
        self.uniform_scale_uv = 100.0
        self.channel_visible = [True] * 32
        self._curves: list[pg.PlotDataItem] = []
        self._separators: list[pg.InfiniteLine] = []
        c = colors()
        self.plot = pg.PlotWidget(background=c["panel"])
        self.plot.setMenuEnabled(False)
        self.plot.showGrid(x=True, y=False, alpha=0.12)
        self.plot.setMouseEnabled(x=False, y=False)
        self.channel_axis = self.plot.getAxis("left")
        self.channel_axis.setWidth(54)
        self.channel_axis.setStyle(tickLength=0, showValues=True)
        self.channel_axis.setPen(c["border"])
        self.channel_axis.setTextPen(c["muted"])
        self.plot.setLabel("bottom", "时间", units="s", color=c["muted"])
        self.plot.getPlotItem().setClipToView(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.plot)
        self._ensure_curves(8)
        self.plot.setYRange(-0.8, 7.8, padding=0.02)

    def reset(self, sample_rate_hz: int = 250, channel_count: int = 8) -> None:
        self.buffer.reset(sample_rate_hz, channel_count)
        self._ensure_curves(channel_count)
        self.refresh()

    def clear(self) -> None:
        """Remove all session samples even when the display is paused."""
        self.buffer.reset(self.buffer.sample_rate_hz, self.buffer.channel_count)
        for curve in self._curves:
            curve.setData([], [])
        self._update_channel_axis(self.buffer.channel_count)
        self.plot.setXRange(-self.buffer.seconds, 0.0, padding=0.0)

    def append(self, batch: SampleBatch, filtered: np.ndarray) -> None:
        self.buffer.append(batch, filtered)

    def set_window(self, seconds: int) -> None:
        self.buffer.set_seconds(seconds)
        self.refresh()

    def _ensure_curves(self, count: int) -> None:
        while len(self._curves) < count:
            index = len(self._curves)
            color = CHANNEL_COLORS[index % len(CHANNEL_COLORS)]
            curve = self.plot.plot(pen=pg.mkPen(color, width=1.15), connect="finite")
            curve.setDownsampling(auto=True, method="peak")
            self._curves.append(curve)
        while len(self._separators) < count:
            index = len(self._separators)
            separator = pg.InfiniteLine(
                pos=index + 0.5,
                angle=0,
                movable=False,
                pen=pg.mkPen(colors()["border"], width=1),
            )
            separator.setZValue(-10)
            self.plot.addItem(separator)
            self._separators.append(separator)
        for index, curve in enumerate(self._curves):
            visible = index < count and self.channel_visible[index]
            curve.setVisible(visible)
        for index, separator in enumerate(self._separators):
            separator.setVisible(index < count - 1)
        self._update_channel_axis(count)
        self.plot.setYRange(-0.8, max(0.8, count - 0.2), padding=0.02)

    def _update_channel_axis(self, count: int) -> None:
        ticks = [(index, f"CH{index + 1}") for index in range(count) if self.channel_visible[index]]
        self.channel_axis.setTicks([ticks])

    def refresh(self) -> None:
        if self.paused:
            return
        x, values = self.buffer.values(self.filtered)
        count = values.shape[1] if values.ndim == 2 else 0
        self._ensure_curves(count)
        if not len(x):
            for curve in self._curves:
                curve.setData([], [])
            return
        for channel in range(count):
            visible = self.channel_visible[channel]
            self._curves[channel].setVisible(visible)
            if not visible:
                continue
            # EEG hardware can carry a comparatively large DC offset.  Center
            # every visible trace around its own robust baseline before scaling,
            # so the waveform remains in the middle of its labelled track.
            # This is deliberately a display-only transform: the buffer, raw
            # recorder, and every export continue to receive the untouched data.
            centered = values[:, channel] - np.median(values[:, channel])
            if self.scale_mode == "auto":
                percentile = float(np.percentile(np.abs(centered), 95))
                scale = max(5.0, percentile * 1.15)
            else:
                scale = self.uniform_scale_uv
            # The selected ± scale reaches the edge of this channel's track.
            # Clipping stays display-only and prevents a large artefact from
            # crossing into a neighbouring channel.
            normalized = np.clip(
                centered / scale * TRACK_HALF_HEIGHT,
                -TRACK_HALF_HEIGHT,
                TRACK_HALF_HEIGHT,
            ) + channel
            self._curves[channel].setData(x, normalized, skipFiniteCheck=True)
        self._update_channel_axis(count)
        self.plot.setXRange(-self.buffer.seconds, 0.0, padding=0.0)
