"""Shared state and data models used by the core and the desktop UI."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import StrEnum

import numpy as np
from numpy.typing import NDArray

from .protocol import FLAG_DROPPED_BEFORE, SampleFrame


class SessionState(StrEnum):
    DISCONNECTED = "disconnected"
    DISCOVERING = "discovering"
    LISTENING = "listening"
    CONFIGURING = "configuring"
    READY = "ready"
    STREAMING = "streaming"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass(frozen=True)
class SessionConfig:
    board_count: int = 1
    sample_rate_hz: int = 250
    profile: int = 0
    channel_mask: int = 0xFF
    output_format: int = 0

    def __post_init__(self) -> None:
        if not 1 <= self.board_count <= 4:
            raise ValueError("board_count must be in 1..4")
        if self.sample_rate_hz not in (250, 500):
            raise ValueError("sample_rate_hz must be 250 or 500")
        if self.profile not in range(4):
            raise ValueError("profile must be in 0..3")
        if not 0 <= self.channel_mask <= 0xFF:
            raise ValueError("channel_mask must be a uint8")


@dataclass
class IntegrityStats:
    valid_frames: int = 0
    sequence_gaps: int = 0
    duplicate_sequences: int = 0
    crc_errors: int = 0
    format_errors: int = 0
    discarded_bytes: int = 0
    device_drop_flags: int = 0
    local_queue_drops: int = 0
    reconnects: int = 0
    max_arrival_gap_ms: float = 0.0
    first_timestamp_us: int | None = None
    last_timestamp_us: int | None = None
    last_sequence: int | None = None
    _last_arrival_monotonic: float | None = field(default=None, repr=False)

    def observe(self, frame: SampleFrame, arrival_monotonic: float | None = None) -> None:
        arrival = arrival_monotonic if arrival_monotonic is not None else time.monotonic()
        if self._last_arrival_monotonic is not None:
            gap_ms = (arrival - self._last_arrival_monotonic) * 1000.0
            self.max_arrival_gap_ms = max(self.max_arrival_gap_ms, gap_ms)
        self._last_arrival_monotonic = arrival

        if self.last_sequence is not None:
            delta = (frame.sequence - self.last_sequence) & 0xFFFFFFFF
            if delta == 0:
                self.duplicate_sequences += 1
            elif delta > 1:
                self.sequence_gaps += delta - 1
        self.last_sequence = frame.sequence
        self.valid_frames += 1
        self.first_timestamp_us = (
            frame.timestamp_us if self.first_timestamp_us is None else self.first_timestamp_us
        )
        self.last_timestamp_us = frame.timestamp_us
        if frame.flags & FLAG_DROPPED_BEFORE:
            self.device_drop_flags += 1

    @property
    def duration_seconds(self) -> float:
        if self.first_timestamp_us is None or self.last_timestamp_us is None:
            return 0.0
        return max(0.0, (self.last_timestamp_us - self.first_timestamp_us) / 1_000_000.0)

    @property
    def actual_sample_rate_hz(self) -> float:
        duration = self.duration_seconds
        return (self.valid_frames - 1) / duration if duration > 0 and self.valid_frames > 1 else 0.0

    def public_dict(self) -> dict[str, int | float | None]:
        result = asdict(self)
        result.pop("_last_arrival_monotonic", None)
        result["duration_seconds"] = self.duration_seconds
        result["actual_sample_rate_hz"] = self.actual_sample_rate_hz
        return result


@dataclass(frozen=True)
class SampleBatch:
    timestamps_us: NDArray[np.uint64]
    sequences: NDArray[np.uint32]
    raw_counts: NDArray[np.int32]
    microvolts: NDArray[np.float64]
    flags: NDArray[np.uint16]
    sample_rate_hz: int

    @property
    def sample_count(self) -> int:
        return int(self.raw_counts.shape[0])

    @property
    def channel_count(self) -> int:
        return int(self.raw_counts.shape[1])


@dataclass(frozen=True)
class Marker:
    marker_id: int
    device_timestamp_us: int | None
    host_timestamp_utc: str
    label: str
    note: str = ""


@dataclass(frozen=True)
class DiagnosticEvent:
    level: str
    message: str
    timestamp_monotonic: float = field(default_factory=time.monotonic)
