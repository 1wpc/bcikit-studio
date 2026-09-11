"""Stateful display-only EEG filtering without a heavyweight runtime backend."""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import NDArray


class _Biquad:
    def __init__(
        self,
        coefficients: tuple[float, float, float, float, float],
        channel_count: int,
    ) -> None:
        self.b0, self.b1, self.b2, self.a1, self.a2 = coefficients
        self.x1 = np.zeros(channel_count, dtype=np.float64)
        self.x2 = np.zeros(channel_count, dtype=np.float64)
        self.y1 = np.zeros(channel_count, dtype=np.float64)
        self.y2 = np.zeros(channel_count, dtype=np.float64)

    def reset(self) -> None:
        for state in (self.x1, self.x2, self.y1, self.y2):
            state.fill(0.0)

    def process(self, samples: NDArray[np.float64]) -> NDArray[np.float64]:
        output = np.empty_like(samples, dtype=np.float64)
        for index, current in enumerate(samples):
            value = (
                self.b0 * current
                + self.b1 * self.x1
                + self.b2 * self.x2
                - self.a1 * self.y1
                - self.a2 * self.y2
            )
            output[index] = value
            self.x2[:] = self.x1
            self.x1[:] = current
            self.y2[:] = self.y1
            self.y1[:] = value
        return output


def _normalized(
    b0: float, b1: float, b2: float, a0: float, a1: float, a2: float
) -> tuple[float, float, float, float, float]:
    return b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0


def _lowpass(sample_rate: int, frequency: float, quality: float):
    omega = 2.0 * math.pi * frequency / sample_rate
    cosine = math.cos(omega)
    alpha = math.sin(omega) / (2.0 * quality)
    return _normalized(
        (1.0 - cosine) / 2.0,
        1.0 - cosine,
        (1.0 - cosine) / 2.0,
        1.0 + alpha,
        -2.0 * cosine,
        1.0 - alpha,
    )


def _highpass(sample_rate: int, frequency: float, quality: float):
    omega = 2.0 * math.pi * frequency / sample_rate
    cosine = math.cos(omega)
    alpha = math.sin(omega) / (2.0 * quality)
    return _normalized(
        (1.0 + cosine) / 2.0,
        -(1.0 + cosine),
        (1.0 + cosine) / 2.0,
        1.0 + alpha,
        -2.0 * cosine,
        1.0 - alpha,
    )


def _notch(sample_rate: int, frequency: float, quality: float):
    omega = 2.0 * math.pi * frequency / sample_rate
    cosine = math.cos(omega)
    alpha = math.sin(omega) / (2.0 * quality)
    return _normalized(
        1.0,
        -2.0 * cosine,
        1.0,
        1.0 + alpha,
        -2.0 * cosine,
        1.0 - alpha,
    )


class EEGDisplayFilter:
    """Cascaded fourth-order 1-45 Hz band-pass and 50 Hz notch."""

    def __init__(self, sample_rate_hz: int, channel_count: int) -> None:
        self.sample_rate_hz = sample_rate_hz
        self.channel_count = channel_count
        high = min(45.0, sample_rate_hz / 2.0 - 1.0)
        butterworth_q = (0.5411961, 1.3065630)
        coefficients = [
            *(_highpass(sample_rate_hz, 1.0, quality) for quality in butterworth_q),
            *(_lowpass(sample_rate_hz, high, quality) for quality in butterworth_q),
            _notch(sample_rate_hz, 50.0, 30.0),
        ]
        self._sections = [_Biquad(value, channel_count) for value in coefficients]

    def reset(self) -> None:
        for section in self._sections:
            section.reset()

    def process(self, samples_uv: NDArray[np.float64]) -> NDArray[np.float64]:
        if samples_uv.ndim != 2 or samples_uv.shape[1] != self.channel_count:
            raise ValueError("samples must have shape (n, channel_count)")
        filtered = np.asarray(samples_uv, dtype=np.float64)
        for section in self._sections:
            filtered = section.process(filtered)
        return filtered
