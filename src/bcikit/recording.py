"""Crash-aware raw session recording, replay, listing, and CSV export."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np
from platformdirs import user_data_path
from pyedflib import FILETYPE_BDFPLUS, EdfWriter

from .models import IntegrityStats, Marker, SampleBatch
from .protocol import SampleFrame, StreamDecoder, microvolts_per_count

APP_NAME = "BCIKit Studio"
APP_AUTHOR = "BCIKit"
BDF_DIGITAL_MIN = -(1 << 23)
BDF_DIGITAL_MAX = (1 << 23) - 1


class BDFExportError(ValueError):
    """A session cannot be represented as a trustworthy fixed-rate BDF file."""


@dataclass(frozen=True)
class BDFExportResult:
    path: Path
    sample_count: int
    channel_count: int
    marker_count: int
    padded_samples: int


def default_sessions_root() -> Path:
    return user_data_path(APP_NAME, APP_AUTHOR, ensure_exists=True) / "sessions"


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def safe_session_name(value: str) -> str:
    cleaned = re.sub(r"[^\w\-\u4e00-\u9fff]+", "_", value.strip(), flags=re.UNICODE)
    return cleaned.strip("_")[:48] or "记录"


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class SessionRecorder:
    """Append-only recorder whose metadata remains useful after abnormal exit."""

    def __init__(self, sessions_root: Path | None = None) -> None:
        self.sessions_root = sessions_root or default_sessions_root()
        self.session_dir: Path | None = None
        self._raw_file: BinaryIO | None = None
        self._events_file: Any | None = None
        self._events_writer: Any | None = None
        self._hash = hashlib.sha256()
        self._metadata: dict[str, Any] = {}
        self._marker_count = 0

    @property
    def active(self) -> bool:
        return self._raw_file is not None

    def start(self, name: str, metadata: dict[str, Any]) -> Path:
        if self.active:
            raise RuntimeError("a recording is already active")
        self.sessions_root.mkdir(parents=True, exist_ok=True)
        now = datetime.now()
        base = f"{now:%Y-%m-%d_%H-%M-%S}_{safe_session_name(name)}"
        session_dir = self.sessions_root / base
        suffix = 2
        while session_dir.exists():
            session_dir = self.sessions_root / f"{base}_{suffix}"
            suffix += 1
        session_dir.mkdir(parents=False)
        self.session_dir = session_dir
        self._raw_file = (session_dir / "raw.bciraw").open("wb", buffering=1024 * 1024)
        self._events_file = (session_dir / "events.csv").open("w", encoding="utf-8", newline="")
        self._events_writer = csv.writer(self._events_file)
        self._events_writer.writerow(
            ["marker_id", "device_timestamp_us", "host_timestamp_utc", "label", "note"]
        )
        (session_dir / "logs.txt").write_text("BCIKit Studio recording started\n", encoding="utf-8")
        self._metadata = {
            **metadata,
            "format_version": 1,
            "status": "recording",
            "started_at_utc": utc_now_iso(),
            "ended_at_utc": None,
            "end_reason": None,
            "marker_count": 0,
            "raw_sha256": None,
            "integrity": {},
        }
        _write_json_atomic(session_dir / "session.json", self._metadata)
        return session_dir

    def append_frame(self, frame: SampleFrame) -> None:
        if self._raw_file is None:
            return
        if not frame.wire:
            raise ValueError("cannot record a sample without its original wire bytes")
        self._raw_file.write(frame.wire)
        self._hash.update(frame.wire)

    def append_marker(self, marker: Marker) -> None:
        if self._events_writer is None or self._events_file is None:
            raise RuntimeError("recording is not active")
        self._events_writer.writerow(
            [
                marker.marker_id,
                "" if marker.device_timestamp_us is None else marker.device_timestamp_us,
                marker.host_timestamp_utc,
                marker.label,
                marker.note,
            ]
        )
        self._events_file.flush()
        self._marker_count += 1

    def log(self, message: str) -> None:
        if self.session_dir is not None:
            with (self.session_dir / "logs.txt").open("a", encoding="utf-8") as stream:
                stream.write(f"{utc_now_iso()} {message}\n")

    def finish(self, stats: IntegrityStats, reason: str = "user_stop") -> Path | None:
        if self.session_dir is None:
            return None
        if self._raw_file is not None:
            self._raw_file.flush()
            os.fsync(self._raw_file.fileno())
            self._raw_file.close()
        if self._events_file is not None:
            self._events_file.flush()
            self._events_file.close()
        self._raw_file = None
        self._events_file = None
        self._events_writer = None
        self._metadata.update(
            {
                "status": "complete" if reason == "user_stop" else "interrupted",
                "ended_at_utc": utc_now_iso(),
                "end_reason": reason,
                "marker_count": self._marker_count,
                "raw_sha256": self._hash.hexdigest(),
                "integrity": stats.public_dict(),
            }
        )
        _write_json_atomic(self.session_dir / "session.json", self._metadata)
        finished = self.session_dir
        self.session_dir = None
        return finished


@dataclass(frozen=True)
class SessionSummary:
    path: Path
    name: str
    started_at_utc: str
    duration_seconds: float
    sample_rate_hz: int
    channel_count: int
    marker_count: int
    status: str
    sequence_gaps: int
    crc_errors: int


def recover_incomplete_sessions(root: Path | None = None) -> list[Path]:
    sessions_root = root or default_sessions_root()
    if not sessions_root.exists():
        return []
    recovered: list[Path] = []
    for metadata_path in sessions_root.glob("*/session.json"):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if metadata.get("status") != "recording":
            continue
        raw_path = metadata_path.parent / "raw.bciraw"
        digest = hashlib.sha256(raw_path.read_bytes()).hexdigest() if raw_path.exists() else None
        metadata.update(
            {
                "status": "interrupted",
                "ended_at_utc": utc_now_iso(),
                "end_reason": "application_exit",
                "raw_sha256": digest,
            }
        )
        _write_json_atomic(metadata_path, metadata)
        recovered.append(metadata_path.parent)
    return recovered


def list_sessions(root: Path | None = None) -> list[SessionSummary]:
    sessions_root = root or default_sessions_root()
    if not sessions_root.exists():
        return []
    summaries: list[SessionSummary] = []
    for metadata_path in sessions_root.glob("*/session.json"):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            config = metadata.get("config", {})
            integrity = metadata.get("integrity", {})
            summaries.append(
                SessionSummary(
                    path=metadata_path.parent,
                    name=metadata_path.parent.name,
                    started_at_utc=str(metadata.get("started_at_utc") or ""),
                    duration_seconds=float(integrity.get("duration_seconds") or 0.0),
                    sample_rate_hz=int(config.get("sample_rate_hz") or 0),
                    channel_count=int(config.get("board_count") or 1) * 8,
                    marker_count=int(metadata.get("marker_count") or 0),
                    status=str(metadata.get("status") or "unknown"),
                    sequence_gaps=int(integrity.get("sequence_gaps") or 0),
                    crc_errors=int(integrity.get("crc_errors") or 0),
                )
            )
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return sorted(summaries, key=lambda item: item.started_at_utc, reverse=True)


@dataclass(frozen=True)
class LoadedSession:
    metadata: dict[str, Any]
    batch: SampleBatch
    markers: tuple[Marker, ...]


def _iter_sample_frames(raw_path: Path):
    decoder = StreamDecoder()
    with raw_path.open("rb") as stream:
        while chunk := stream.read(64 * 1024):
            yield from (frame for frame in decoder.feed(chunk) if isinstance(frame, SampleFrame))


def load_session(session_dir: Path) -> LoadedSession:
    metadata = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
    frames = list(_iter_sample_frames(session_dir / "raw.bciraw"))
    if not frames:
        empty = np.empty((0, 0), dtype=np.int32)
        batch = SampleBatch(
            np.empty(0, dtype=np.uint64),
            np.empty(0, dtype=np.uint32),
            empty,
            empty.astype(np.float64),
            np.empty(0, dtype=np.uint16),
            int(metadata.get("config", {}).get("sample_rate_hz", 250)),
        )
    else:
        raw = np.asarray([frame.channels_physical_order() for frame in frames], dtype=np.int32)
        scale = microvolts_per_count(frames[0].gain_code)
        batch = SampleBatch(
            np.asarray([frame.timestamp_us for frame in frames], dtype=np.uint64),
            np.asarray([frame.sequence for frame in frames], dtype=np.uint32),
            raw,
            raw.astype(np.float64) * scale,
            np.asarray([frame.flags for frame in frames], dtype=np.uint16),
            frames[0].sample_rate_hz,
        )
    markers: list[Marker] = []
    events_path = session_dir / "events.csv"
    if events_path.exists():
        with events_path.open(encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                device_value = row.get("device_timestamp_us") or ""
                markers.append(
                    Marker(
                        int(row["marker_id"]),
                        int(device_value) if device_value else None,
                        row["host_timestamp_utc"],
                        row["label"],
                        row.get("note", ""),
                    )
                )
    return LoadedSession(metadata, batch, tuple(markers))


def export_csv(session_dir: Path, destination: Path, unit: str = "uv") -> int:
    if unit not in {"uv", "raw"}:
        raise ValueError("unit must be 'uv' or 'raw'")
    rows = 0
    with destination.open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output)
        first_frame: SampleFrame | None = None
        for frame in _iter_sample_frames(session_dir / "raw.bciraw"):
            if first_frame is None:
                first_frame = frame
                writer.writerow(
                    ["sequence", "timestamp_us"]
                    + [f"CH{channel + 1}_{unit}" for channel in range(frame.channel_count)]
                )
            values = frame.channels_physical_order()
            if unit == "uv":
                scale = microvolts_per_count(frame.gain_code)
                channel_values = [f"{value * scale:.9f}" for value in values]
            else:
                channel_values = list(values)
            writer.writerow([frame.sequence, frame.timestamp_us, *channel_values])
            rows += 1
    return rows


def _bdf_start_datetime(metadata: dict[str, Any]) -> datetime:
    value = str(metadata.get("started_at_utc") or "")
    try:
        started = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(UTC).replace(tzinfo=None)
    if started.tzinfo is not None:
        return started.astimezone(UTC).replace(tzinfo=None)
    return started


def _validate_bdf_frames(frames: list[SampleFrame]) -> None:
    if not frames:
        raise BDFExportError("记录中没有有效 SAMPLE 帧，无法导出 BDF。")
    first = frames[0]
    expected_period_us = round(1_000_000 / first.sample_rate_hz)
    for index, frame in enumerate(frames):
        if frame.channel_count != first.channel_count:
            raise BDFExportError("会话中的通道数发生变化，无法导出固定通道 BDF。")
        if frame.sample_rate_hz != first.sample_rate_hz:
            raise BDFExportError("会话中的采样率发生变化，无法导出固定采样率 BDF。")
        if frame.gain_code != first.gain_code:
            raise BDFExportError("会话中的通道增益发生变化，无法保持 BDF 物理单位一致。")
        counts = frame.channels_physical_order()
        if any(value < BDF_DIGITAL_MIN or value > BDF_DIGITAL_MAX for value in counts):
            raise BDFExportError("会话包含超出 24 位 BDF 数字量程的样本。")
        if index == 0:
            continue
        previous = frames[index - 1]
        expected_sequence = (previous.sequence + 1) & 0xFFFFFFFF
        if frame.sequence != expected_sequence:
            raise BDFExportError(
                "检测到 sequence gap；BDF 的固定时间轴无法无损表达缺失样本。"
                "请保留 raw.bciraw，或选择 CSV 导出。"
            )
        if frame.timestamp_us - previous.timestamp_us != expected_period_us:
            raise BDFExportError("设备时间戳不是连续固定采样间隔，无法导出可信的固定速率 BDF。")


def _bdf_signal_headers(first_frame: SampleFrame) -> list[dict[str, str | float | int | None]]:
    scale_uv_per_count = microvolts_per_count(first_frame.gain_code)
    # BDF headers allow only eight ASCII characters for a physical bound.  An
    # integer full-scale uV range is both representable and keeps digital
    # ADS1299 counts exact when callers request digital samples.
    physical_limit_uv = int(round(BDF_DIGITAL_MAX * scale_uv_per_count))
    return [
        {
            "label": f"EEG CH{channel + 1}",
            "dimension": "uV",
            "sample_frequency": first_frame.sample_rate_hz,
            "physical_min": -physical_limit_uv,
            "physical_max": physical_limit_uv,
            "digital_min": BDF_DIGITAL_MIN,
            "digital_max": BDF_DIGITAL_MAX,
            "transducer": "ADS1299",
            "prefilter": "raw",
        }
        for channel in range(first_frame.channel_count)
    ]


def _marker_onset_seconds(marker: Marker, first_timestamp_us: int) -> float | None:
    if marker.device_timestamp_us is None:
        return None
    return max(0.0, (marker.device_timestamp_us - first_timestamp_us) / 1_000_000.0)


def export_bdf(session_dir: Path, destination: Path) -> BDFExportResult:
    """Export a regular, gap-free BCIKit session as BDF+ without touching its source.

    BDF stores fixed-rate records. Sessions with a sequence or timestamp gap are
    deliberately rejected instead of filling, interpolating, or time-compressing
    missing samples. ``raw.bciraw`` remains the canonical acquisition record.
    """
    metadata = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
    frames = list(_iter_sample_frames(session_dir / "raw.bciraw"))
    _validate_bdf_frames(frames)
    first = frames[0]
    raw_counts = np.asarray([frame.channels_physical_order() for frame in frames], dtype=np.int32)
    sample_count = int(raw_counts.shape[0])
    padded_samples = (-sample_count) % first.sample_rate_hz
    markers = load_session(session_dir).markers
    marker_annotations = [
        (marker, onset)
        for marker in markers
        if (onset := _marker_onset_seconds(marker, first.timestamp_us)) is not None
    ]
    record_count = (sample_count + padded_samples) // first.sample_rate_hz
    system_annotation_count = 1 if padded_samples else 0
    annotation_signals = max(
        1,
        min(
            64,
            (len(marker_annotations) + system_annotation_count + record_count - 1) // record_count,
        ),
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    writer = EdfWriter(str(destination), first.channel_count, file_type=FILETYPE_BDFPLUS)
    try:
        writer.setSignalHeaders(_bdf_signal_headers(first))
        writer.setStartdatetime(_bdf_start_datetime(metadata))
        writer.setEquipment("BCIKitStudio")
        writer.setRecordingAdditional(f"raw.bciraw;samples={sample_count};padded={padded_samples}")
        writer.set_number_of_annotation_signals(annotation_signals)
        writer.writeSamples(np.ascontiguousarray(raw_counts.T), digital=True)
        for marker, onset in marker_annotations:
            writer.writeAnnotation(onset, 0.0, f"Marker {marker.marker_id}: {marker.label}")
        if padded_samples:
            writer.writeAnnotation(
                sample_count / first.sample_rate_hz,
                0.0,
                f"BCIKit end: final record has {padded_samples} zero padding samples",
            )
    except Exception:
        writer.close()
        destination.unlink(missing_ok=True)
        raise
    else:
        writer.close()
    return BDFExportResult(
        path=destination,
        sample_count=sample_count,
        channel_count=first.channel_count,
        marker_count=len(marker_annotations),
        padded_samples=padded_samples,
    )
