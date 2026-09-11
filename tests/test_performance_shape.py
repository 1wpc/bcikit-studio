import asyncio

import numpy as np
import pytest

from bcikit.models import SampleBatch, SessionConfig
from bcikit.protocol import TYPE_COMMAND, ControlFrame, StreamDecoder
from bcikit.session import DeviceSession
from bcikit.transport import VirtualTransport
from bcikit_studio.waveform import WaveformBuffer


class CommandRecordingVirtualTransport(VirtualTransport):
    def __init__(self) -> None:
        super().__init__("normal")
        self.commands: list[int] = []
        self._command_decoder = StreamDecoder()

    async def write(self, data: bytes) -> None:
        self.commands.extend(
            frame.code
            for frame in self._command_decoder.feed(data)
            if isinstance(frame, ControlFrame) and frame.frame_type == TYPE_COMMAND
        )
        await super().write(data)


@pytest.mark.asyncio
async def test_virtual_32_channel_500_hz_core_path():
    session = DeviceSession(
        VirtualTransport("normal"), SessionConfig(board_count=4, sample_rate_hz=500)
    )
    await session.connect()
    await session.start()
    batch = await asyncio.wait_for(session.sample_queue.get(), 1.0)
    assert batch.channel_count == 32
    assert batch.sample_rate_hz == 500
    await session.close()


@pytest.mark.asyncio
async def test_multiboard_handshake_sets_board_count_before_sample_rate():
    transport = CommandRecordingVirtualTransport()
    session = DeviceSession(transport, SessionConfig(board_count=2, sample_rate_hz=250))
    try:
        await session.connect()
        assert transport.commands.index(0x07) < transport.commands.index(0x06)
    finally:
        await session.close()


def test_accelerated_30_minute_display_buffer_remains_bounded():
    sample_rate = 500
    channel_count = 8
    buffer = WaveformBuffer(sample_rate, channel_count, seconds=30)
    chunk_size = 1000
    timestamps = np.arange(chunk_size, dtype=np.uint64) * 2000
    raw = np.zeros((chunk_size, channel_count), dtype=np.int32)
    uv = raw.astype(np.float64)
    for chunk in range(900):
        batch = SampleBatch(
            timestamps + chunk * chunk_size * 2000,
            np.arange(chunk_size, dtype=np.uint32) + chunk * chunk_size,
            raw,
            uv,
            np.zeros(chunk_size, dtype=np.uint16),
            sample_rate,
        )
        buffer.append(batch, uv)
    _, values = buffer.values(filtered=False)
    assert values.shape == (sample_rate * 30, channel_count)
