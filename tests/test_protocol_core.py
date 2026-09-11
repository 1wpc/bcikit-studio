import struct

from hypothesis import given
from hypothesis import strategies as st

from bcikit.models import IntegrityStats
from bcikit.protocol import (
    FLAG_TIME_VALID,
    SampleFrame,
    StreamDecoder,
    encode_sample_frame,
)


@given(
    board_count=st.integers(min_value=1, max_value=4),
    sequence=st.integers(min_value=0, max_value=0xFFFFFFFF),
    values=st.lists(
        st.integers(min_value=-(1 << 23), max_value=(1 << 23) - 1),
        min_size=32,
        max_size=32,
    ),
)
def test_sample_round_trip_preserves_physical_order(board_count, sequence, values):
    physical = [values[index * 8 : index * 8 + 8] for index in range(board_count)]
    wire = encode_sample_frame(
        flags=FLAG_TIME_VALID,
        sequence=sequence,
        timestamp_us=123_000,
        sample_rate_hz=500,
        gain_code=6,
        board_channels_wire_order=list(reversed(physical)),
    )
    chunks = [wire[:3], wire[3:17], wire[17:]]
    decoder = StreamDecoder()
    frames = [frame for chunk in chunks for frame in decoder.feed(chunk)]
    assert len(frames) == 1
    frame = frames[0]
    assert isinstance(frame, SampleFrame)
    assert frame.sequence == sequence
    assert frame.wire == wire
    assert frame.channels_physical_order() == tuple(values[: board_count * 8])


def test_integrity_sequence_wrap_is_continuous():
    payload = b"\xc0\x00\x00" + b"\x00\x00\x00" * 8
    stats = IntegrityStats()
    for sequence in (0xFFFFFFFE, 0xFFFFFFFF, 0, 1, 4):
        stats.observe(SampleFrame(1, sequence, sequence & 0xFFFF, 250, 1, 8, 6, payload))
    assert stats.sequence_gaps == 2
    assert stats.duplicate_sequences == 0


def test_sample_wire_crc_is_little_endian():
    wire = encode_sample_frame(
        flags=1,
        sequence=7,
        timestamp_us=123,
        sample_rate_hz=250,
        gain_code=6,
        board_channels_wire_order=[[0] * 8],
    )
    assert StreamDecoder().feed(wire)
    assert struct.unpack_from("<H", wire, len(wire) - 2)[0] != 0
