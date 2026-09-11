import asyncio
import hashlib
import json

import numpy as np
import pyedflib
import pytest

from bcikit.models import SessionConfig
from bcikit.protocol import (
    CMD_SET_PROFILE,
    CMD_START,
    PROFILE_EEG_SRB2,
    PROFILE_INPUT_SHORT,
    PROFILE_INTERNAL_TEST,
)
from bcikit.recording import (
    BDFExportError,
    SessionRecorder,
    _iter_sample_frames,
    export_bdf,
    export_csv,
    list_sessions,
    load_session,
    recover_incomplete_sessions,
)
from bcikit.session import CommandError, DeviceSession
from bcikit.transport import ReplayTransport, VirtualTransport


@pytest.mark.asyncio
async def test_virtual_session_record_marker_replay_and_export(tmp_path):
    session = DeviceSession(VirtualTransport("alpha"))
    session.recorder = SessionRecorder(tmp_path)
    await session.connect()
    await session.start()
    first_batch = await asyncio.wait_for(session.sample_queue.get(), 1.0)
    assert first_batch.channel_count == 8
    assert first_batch.sample_rate_hz == 250
    session.start_recording("Alpha 测试")
    session_dir = session.recorder.session_dir
    assert session_dir is not None
    await asyncio.sleep(0.12)
    marker = session.add_marker("闭眼")
    assert marker.device_timestamp_us is not None
    await asyncio.sleep(0.08)
    await session.stop()
    await session.close()

    metadata = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
    raw = (session_dir / "raw.bciraw").read_bytes()
    assert metadata["status"] == "complete"
    assert metadata["marker_count"] == 1
    assert metadata["raw_sha256"] == hashlib.sha256(raw).hexdigest()
    loaded = load_session(session_dir)
    assert loaded.batch.sample_count > 20
    assert loaded.batch.channel_count == 8
    assert loaded.markers[0].label == "闭眼"
    destination = tmp_path / "export.csv"
    assert export_csv(session_dir, destination, "uv") == loaded.batch.sample_count
    assert (
        destination.read_text(encoding="utf-8")
        .splitlines()[0]
        .startswith("sequence,timestamp_us,CH1_uv")
    )
    bdf_destination = tmp_path / "alpha.bdf"
    bdf_result = export_bdf(session_dir, bdf_destination)
    assert bdf_result.sample_count == loaded.batch.sample_count
    assert bdf_result.channel_count == 8
    assert bdf_result.marker_count == 1
    assert bdf_result.padded_samples > 0
    reader = pyedflib.EdfReader(str(bdf_destination))
    try:
        assert reader.filetype == pyedflib.FILETYPE_BDFPLUS
        assert reader.signals_in_file == 8
        assert reader.getSignalLabels() == [f"EEG CH{channel}" for channel in range(1, 9)]
        assert reader.getSampleFrequency(0) == 250.0
        np.testing.assert_array_equal(
            reader.readSignal(0, 0, loaded.batch.sample_count, digital=True),
            loaded.batch.raw_counts[:, 0],
        )
        _, _, annotations = reader.readAnnotations()
        assert any("闭眼" in text for text in annotations)
    finally:
        reader.close()
    summaries = list_sessions(tmp_path)
    assert summaries[0].status == "complete"

    replay = DeviceSession(ReplayTransport(session_dir, speed=100.0))
    await replay.connect()
    await replay.start()
    replayed = await asyncio.wait_for(replay.sample_queue.get(), 1.0)
    assert replayed.channel_count == 8
    assert replayed.sample_rate_hz == 250
    await replay.close()


@pytest.mark.asyncio
async def test_multi_board_accepts_unavailable_daisy_chain_id():
    session = DeviceSession(
        VirtualTransport("normal", ads1299_id=0x00),
        config=SessionConfig(board_count=2, sample_rate_hz=250),
    )
    await session.connect()
    assert session.device_status is not None
    assert session.device_status.board_count == 2
    assert session.device_status.register_verified
    assert session.device_status.ads1299_id == 0x00
    await session.start()
    batch = await asyncio.wait_for(session.sample_queue.get(), 1.0)
    assert batch.channel_count == 16
    await session.close()


@pytest.mark.asyncio
async def test_virtual_session_switches_diagnostic_profiles_and_reads_status():
    session = DeviceSession(VirtualTransport("normal"))
    await session.connect()
    await session.start()
    await asyncio.wait_for(session.sample_queue.get(), 1.0)

    internal_test = await session.set_profile(PROFILE_INTERNAL_TEST)
    assert internal_test.profile == PROFILE_INTERNAL_TEST
    assert internal_test.register_verified
    assert session.config.profile == PROFILE_INTERNAL_TEST
    assert session.state.value == "streaming"

    input_short = await session.set_profile(PROFILE_INPUT_SHORT)
    assert input_short.profile == PROFILE_INPUT_SHORT
    restored = await session.set_profile(PROFILE_EEG_SRB2)
    assert restored.profile == PROFILE_EEG_SRB2
    assert (await session.reset_device_stats()).counters[1] == 0
    await session.close()


@pytest.mark.asyncio
async def test_profile_failure_preserves_the_profile_error_if_recovery_also_fails(monkeypatch):
    session = DeviceSession(VirtualTransport("normal"))
    await session.connect()
    await session.start()
    original_send_command = session.send_command

    async def failing_send(command: int, payload: bytes = b"", timeout: float = 3.0):
        if command == CMD_SET_PROFILE:
            raise CommandError(CMD_SET_PROFILE, 5)
        if command == CMD_START:
            raise CommandError(CMD_START, 5)
        return await original_send_command(command, payload, timeout)

    monkeypatch.setattr(session, "send_command", failing_send)
    with pytest.raises(CommandError) as failure:
        await session.set_profile(PROFILE_INTERNAL_TEST)
    assert failure.value.command == CMD_SET_PROFILE
    await session.close()


@pytest.mark.asyncio
async def test_single_board_still_requires_ads1299_id():
    session = DeviceSession(VirtualTransport("normal", ads1299_id=0x00))
    with pytest.raises(RuntimeError, match="单板模式要求 ID=0x3E"):
        await session.connect()
    await session.close()


def test_recover_incomplete_session(tmp_path):
    recorder = SessionRecorder(tmp_path)
    session_dir = recorder.start("中断测试", {"config": {"board_count": 1, "sample_rate_hz": 250}})
    assert recover_incomplete_sessions(tmp_path) == [session_dir]
    metadata = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
    assert metadata["status"] == "interrupted"
    assert metadata["end_reason"] == "application_exit"


@pytest.mark.asyncio
async def test_bdf_export_rejects_sequence_gap(tmp_path):
    session = DeviceSession(VirtualTransport("normal"))
    session.recorder = SessionRecorder(tmp_path)
    await session.connect()
    await session.start()
    session.start_recording("有丢帧")
    session_dir = session.recorder.session_dir
    assert session_dir is not None
    await asyncio.sleep(0.12)
    await session.stop()
    await session.close()

    raw_path = session_dir / "raw.bciraw"
    frames = list(_iter_sample_frames(raw_path))
    assert len(frames) > 4
    raw_path.write_bytes(b"".join(frame.wire for index, frame in enumerate(frames) if index != 3))

    with pytest.raises(BDFExportError, match="sequence gap"):
        export_bdf(session_dir, tmp_path / "gap.bdf")
