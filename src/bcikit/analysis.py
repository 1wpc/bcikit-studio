"""Display-only EEG spectrum, band-power, and signal-quality analysis."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

BAND_DEFINITIONS = (
    ("Delta", 1.0, 4.0),
    ("Theta", 4.0, 8.0),
    ("Alpha", 8.0, 13.0),
    ("Beta", 13.0, 30.0),
    ("Gamma", 30.0, 45.0),
)


@dataclass(frozen=True)
class EEGAnalysis:
    frequencies_hz: NDArray[np.float64]
    psd_uv2_per_hz: NDArray[np.float64]
    band_power_uv2: NDArray[np.float64]
    relative_band_power: NDArray[np.float64]
    quality_scores: NDArray[np.float64]
    data_seconds: float

    @property
    def channel_count(self) -> int:
        return int(self.psd_uv2_per_hz.shape[0])

    @property
    def average_psd_uv2_per_hz(self) -> NDArray[np.float64]:
        return np.mean(self.psd_uv2_per_hz, axis=0)

    @property
    def average_relative_band_power(self) -> NDArray[np.float64]:
        return np.mean(self.relative_band_power, axis=0)


def quality_label(score: float) -> str:
    if score >= 75.0:
        return "良好"
    if score >= 45.0:
        return "注意"
    return "较差"


def _welch_psd(
    samples_uv: NDArray[np.float64], sample_rate_hz: int
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    sample_count = samples_uv.shape[0]
    segment_length = min(sample_count, sample_rate_hz * 2)
    step = max(1, segment_length // 2)
    starts = list(range(0, sample_count - segment_length + 1, step))
    if not starts:
        starts = [0]
    window = np.hanning(segment_length).astype(np.float64)
    window_energy = float(np.sum(window**2))
    spectra: list[NDArray[np.float64]] = []
    for start in starts:
        segment = samples_uv[start : start + segment_length]
        segment = segment - np.mean(segment, axis=0, keepdims=True)
        transformed = np.fft.rfft(segment * window[:, None], axis=0)
        density = np.abs(transformed) ** 2 / (sample_rate_hz * window_energy)
        if segment_length % 2 == 0:
            density[1:-1] *= 2.0
        else:
            density[1:] *= 2.0
        spectra.append(density.T)
    frequencies = np.fft.rfftfreq(segment_length, d=1.0 / sample_rate_hz)
    return frequencies.astype(np.float64), np.mean(spectra, axis=0)


def _integrated_power(
    frequencies_hz: NDArray[np.float64],
    psd: NDArray[np.float64],
    low_hz: float,
    high_hz: float,
) -> NDArray[np.float64]:
    mask = (frequencies_hz >= low_hz) & (frequencies_hz <= high_hz)
    if np.count_nonzero(mask) < 2:
        return np.zeros(psd.shape[0], dtype=np.float64)
    return np.asarray(np.trapezoid(psd[:, mask], frequencies_hz[mask], axis=1), dtype=np.float64)


def analyze_eeg(samples_uv: NDArray[np.float64], sample_rate_hz: int) -> EEGAnalysis:
    """Analyze at least two seconds of channel-major EEG display data.

    The quality score is a transparent display heuristic based on flat-line,
    excessive amplitude, slow drift, and 50 Hz interference. It is not an
    impedance measurement or a medical assessment.
    """
    values = np.asarray(samples_uv, dtype=np.float64)
    if values.ndim != 2 or values.shape[1] < 1:
        raise ValueError("samples must have shape (n, channel_count)")
    if sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be positive")
    required = sample_rate_hz * 2
    if values.shape[0] < required:
        raise ValueError("at least two seconds of samples are required")
    if not np.isfinite(values).all():
        raise ValueError("samples must be finite")

    frequencies, psd = _welch_psd(values, sample_rate_hz)
    band_power = np.column_stack(
        [
            _integrated_power(frequencies, psd, low_hz, high_hz)
            for _, low_hz, high_hz in BAND_DEFINITIONS
        ]
    )
    total_power = _integrated_power(frequencies, psd, 1.0, 45.0)
    relative = np.divide(
        band_power,
        total_power[:, None],
        out=np.zeros_like(band_power),
        where=total_power[:, None] > np.finfo(np.float64).eps,
    )

    standard_deviation = np.std(values, axis=0)
    robust_range = np.percentile(values, 99.0, axis=0) - np.percentile(values, 1.0, axis=0)
    broad_power = _integrated_power(frequencies, psd, 0.5, min(55.0, sample_rate_hz / 2))
    line_power = _integrated_power(frequencies, psd, 48.0, 52.0)
    drift_power = _integrated_power(frequencies, psd, 0.5, 1.0)
    line_ratio = np.divide(
        line_power, broad_power, out=np.zeros_like(line_power), where=broad_power > 0
    )
    drift_ratio = np.divide(
        drift_power, broad_power, out=np.zeros_like(drift_power), where=broad_power > 0
    )

    score = np.full(values.shape[1], 100.0, dtype=np.float64)
    score -= np.clip((standard_deviation - 30.0) * 1.25, 0.0, 45.0)
    score -= np.clip((robust_range - 120.0) * 0.25, 0.0, 30.0)
    score -= np.clip((line_ratio - 0.03) * 250.0, 0.0, 40.0)
    score -= np.clip((drift_ratio - 0.30) * 60.0, 0.0, 20.0)
    flatline_score = np.clip(standard_deviation * 30.0, 5.0, 35.0)
    score = np.where(standard_deviation < 1.0, np.minimum(score, flatline_score), score)
    score = np.clip(score, 0.0, 100.0)

    return EEGAnalysis(
        frequencies_hz=frequencies,
        psd_uv2_per_hz=psd,
        band_power_uv2=band_power,
        relative_band_power=relative,
        quality_scores=score,
        data_seconds=values.shape[0] / sample_rate_hz,
    )
