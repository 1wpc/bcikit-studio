"""Compact live EEG analysis panel for the Studio workspace."""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QVBoxLayout

from bcikit.analysis import BAND_DEFINITIONS, EEGAnalysis

from .styles import colors


class AnalysisPanel(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("AnalysisPanel")
        self.setMinimumWidth(286)
        self.setMaximumWidth(380)
        c = colors()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        title_row = QHBoxLayout()
        title = QLabel("实时分析")
        title.setStyleSheet("font-size: 16px; font-weight: 700;")
        self.summary = QLabel("等待数据")
        self.summary.setObjectName("Muted")
        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(self.summary)
        layout.addLayout(title_row)

        self.psd_plot = pg.PlotWidget(background=c["panel_alt"])
        self.psd_plot.setMinimumHeight(160)
        self.psd_plot.setMaximumHeight(210)
        self.psd_plot.setMenuEnabled(False)
        self.psd_plot.setMouseEnabled(x=False, y=False)
        self.psd_plot.showGrid(x=True, y=True, alpha=0.12)
        self.psd_plot.setLabel("bottom", "频率", units="Hz", color=c["muted"])
        self.psd_plot.setLabel("left", "PSD", units="dB", color=c["muted"])
        self.psd_plot.setXRange(1.0, 45.0, padding=0.0)
        self._psd_curve = self.psd_plot.plot(pen=pg.mkPen(c["cyan"], width=1.6))
        layout.addWidget(self.psd_plot)

        self.peak_label = QLabel("主峰 —")
        self.peak_label.setObjectName("Muted")
        layout.addWidget(self.peak_label)

        band_title = QLabel("频段能量占比")
        band_title.setStyleSheet("font-weight: 700;")
        layout.addWidget(band_title)
        self.band_bars: list[QProgressBar] = []
        self.band_values: list[QLabel] = []
        for name, low_hz, high_hz in BAND_DEFINITIONS:
            row = QHBoxLayout()
            label = QLabel(f"{name}  {low_hz:g}–{high_hz:g}")
            label.setMinimumWidth(92)
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setTextVisible(False)
            bar.setFixedHeight(7)
            value = QLabel("—")
            value.setMinimumWidth(38)
            value.setAlignment(value.alignment() | value.alignment().AlignRight)
            row.addWidget(label)
            row.addWidget(bar, 1)
            row.addWidget(value)
            layout.addLayout(row)
            self.band_bars.append(bar)
            self.band_values.append(value)

        layout.addStretch()
        self.clear()

    def set_waiting(self, available_seconds: float) -> None:
        self.summary.setText(f"积累中 {min(available_seconds, 2.0):.1f}/2.0 s")

    def set_result(self, result: EEGAnalysis) -> None:
        average_psd = result.average_psd_uv2_per_hz
        visible = (result.frequencies_hz >= 1.0) & (result.frequencies_hz <= 45.0)
        frequencies = result.frequencies_hz[visible]
        psd_db = 10.0 * np.log10(np.maximum(average_psd[visible], 1e-12))
        self._psd_curve.setData(frequencies, psd_db, skipFiniteCheck=True)
        if len(frequencies):
            peak_index = int(np.argmax(psd_db))
            self.peak_label.setText(f"主峰 {frequencies[peak_index]:.1f} Hz")

        for bar, label, relative in zip(
            self.band_bars,
            self.band_values,
            result.average_relative_band_power,
            strict=True,
        ):
            percent = float(np.clip(relative * 100.0, 0.0, 100.0))
            bar.setValue(round(percent))
            label.setText(f"{percent:.0f}%")

        self.summary.setText("已分析")

    def clear(self) -> None:
        self._psd_curve.setData([], [])
        self.summary.setText("等待数据")
        self.peak_label.setText("主峰 —")
        for bar, label in zip(self.band_bars, self.band_values, strict=True):
            bar.setValue(0)
            label.setText("—")
