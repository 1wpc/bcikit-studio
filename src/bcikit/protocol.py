"""BCIKit Raw Stream v1 and Device Control Protocol v1 codec.

This is the canonical protocol implementation. The historical
``sdk/python/bcikit_protocol.py`` module re-exports this API so existing scripts
keep working.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Union

SYNC = b"\xa5\x5a"
PROTOCOL_VERSION = 1

TYPE_SAMPLE = 0x01
TYPE_DEVICE_INFO = 0x02
TYPE_DEVICE_STATUS = 0x03
TYPE_ACK = 0x04
TYPE_EVENT = 0x05
TYPE_COMMAND = 0x80

CMD_GET_INFO = 0x01
CMD_GET_STATUS = 0x02
CMD_START = 0x03
CMD_STOP = 0x04
CMD_SET_OUTPUT_FORMAT = 0x05
CMD_SET_SAMPLE_RATE = 0x06
CMD_SET_BOARD_COUNT = 0x07
CMD_SET_PROFILE = 0x08
CMD_SET_CHANNEL_MASK = 0x09
CMD_RESET_STATS = 0x0A
CMD_PING = 0x0B

OUTPUT_BCIKIT_RAW_V1 = 0
OUTPUT_OPENBCI_CYTON = 1

PROFILE_EEG_SRB2 = 0
PROFILE_DIFFERENTIAL = 1
PROFILE_INTERNAL_TEST = 2
PROFILE_INPUT_SHORT = 3

STATUS_OK = 0
STATUS_INVALID_COMMAND = 1
STATUS_INVALID_LENGTH = 2
STATUS_INVALID_VALUE = 3
STATUS_BUSY = 4
STATUS_APPLY_FAILED = 5
STATUS_UNSUPPORTED = 6
STATUS_CRC_ERROR = 7

CAP_BCIKIT_RAW_V1 = 1 << 0
CAP_OPENBCI_CYTON = 1 << 1
CAP_MULTI_BOARD = 1 << 2
CAP_DRDY_TIMESTAMP = 1 << 3
CAP_CRC16 = 1 << 4
CAP_INTERNAL_TEST = 1 << 5
CAP_INPUT_SHORT = 1 << 6
CAP_CHANNEL_MASK = 1 << 7
CAP_BINARY_CONTROL = 1 << 8
CAP_WIFI_OPENBCI_API = 1 << 9
CAP_WIFI_BCIKIT_STREAM = 1 << 10
CAP_WIFI_BCIKIT_CONTROL = 1 << 11
CAP_AUTO_PROTOCOL_SESSION = 1 << 12
CAP_BLE_BCIKIT_STREAM = 1 << 13
CAP_BLE_BCIKIT_CONTROL = 1 << 14

FLAG_TIME_VALID = 1 << 0
FLAG_DROPPED_BEFORE = 1 << 1
FLAG_REGISTER_VERIFIED = 1 << 2
FLAG_TEST_SIGNAL = 1 << 3
FLAG_INPUT_SHORT = 1 << 4

GAIN_BY_CODE = {0: 1, 1: 2, 2: 4, 3: 6, 4: 8, 5: 12, 6: 24}


def crc16_ccitt_false(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def decode_s24(data: bytes) -> int:
    if len(data) != 3:
        raise ValueError("ADS1299 values must contain exactly 3 bytes")
    value = int.from_bytes(data, "big", signed=False)
    return value - (1 << 24) if value & 0x800000 else value


def encode_s24(value: int) -> bytes:
    if not -(1 << 23) <= value < (1 << 23):
        raise ValueError("ADS1299 value is outside signed 24-bit range")
    return value.to_bytes(3, "big", signed=True)


def microvolts_per_count(gain_code: int, reference_volts: float = 4.5) -> float:
    gain = GAIN_BY_CODE.get(gain_code)
    if gain is None:
        raise ValueError(f"unknown ADS1299 gain code: {gain_code}")
    return reference_volts / gain / ((1 << 23) - 1) * 1_000_000.0


@dataclass(frozen=True)
class SampleFrame:
    flags: int
    sequence: int
    timestamp_us: int
    sample_rate_hz: int
    board_count: int
    channel_count: int
    gain_code: int
    payload: bytes
    wire: bytes = b""

    def board_block(self, wire_board_index: int) -> bytes:
        if not 0 <= wire_board_index < self.board_count:
            raise IndexError("wire board index out of range")
        offset = wire_board_index * 27
        return self.payload[offset : offset + 27]

    def channel_raw(self, wire_board_index: int, channel_index: int) -> int:
        if not 0 <= channel_index < 8:
            raise IndexError("channel index out of range")
        block = self.board_block(wire_board_index)
        offset = 3 + channel_index * 3
        return decode_s24(block[offset : offset + 3])

    def channels_physical_order(self) -> tuple[int, ...]:
        values: list[int] = []
        for physical_board in range(self.board_count):
            wire_board = self.board_count - 1 - physical_board
            values.extend(self.channel_raw(wire_board, channel) for channel in range(8))
        return tuple(values)


@dataclass(frozen=True)
class ControlFrame:
    frame_type: int
    request_id: int
    code: int
    status: int
    payload: bytes
    wire: bytes = b""


@dataclass(frozen=True)
class DeviceInfo:
    firmware_version: tuple[int, int, int]
    protocol_version: int
    hardware_type: int
    capabilities: int
    max_board_count: int
    sample_rate_mask: int
    output_format_mask: int
    serial_baud: int
    device_id: int


@dataclass(frozen=True)
class DeviceStatus:
    acquisition_state: int
    output_format: int
    profile: int
    board_count: int
    sample_rate_hz: int
    gain_code: int
    channel_enabled_mask: int
    register_verified: bool
    ads1299_id: int
    next_sequence: int
    uptime_us: int
    counters: tuple[int, ...]


@dataclass(frozen=True)
class OpenBCIPacket:
    sample_number: int
    channels: tuple[int, ...]
    auxiliary: bytes
    footer: int


DecodedFrame = Union[SampleFrame, ControlFrame]  # noqa: UP007,UP040


def encode_command(request_id: int, command: int, payload: bytes = b"") -> bytes:
    if not 0 < request_id <= 0xFFFFFFFF:
        raise ValueError("request_id must be a non-zero uint32")
    if len(payload) > 32:
        raise ValueError("command payload exceeds firmware limit of 32 bytes")
    return encode_control_frame(TYPE_COMMAND, request_id, command, STATUS_OK, payload)


def encode_control_frame(
    frame_type: int,
    request_id: int,
    code: int,
    status: int,
    payload: bytes = b"",
) -> bytes:
    body = (
        struct.pack("<BBIBBH", PROTOCOL_VERSION, frame_type, request_id, code, status, len(payload))
        + payload
    )
    return SYNC + body + struct.pack("<H", crc16_ccitt_false(body))


def encode_sample_frame(
    *,
    flags: int,
    sequence: int,
    timestamp_us: int,
    sample_rate_hz: int,
    gain_code: int,
    board_channels_wire_order: list[list[int]],
) -> bytes:
    if not 1 <= len(board_channels_wire_order) <= 4:
        raise ValueError("board count must be between 1 and 4")
    blocks: list[bytes] = []
    for channels in board_channels_wire_order:
        if len(channels) != 8:
            raise ValueError("each board must provide 8 channels")
        blocks.append(b"\xc0\x00\x00" + b"".join(encode_s24(value) for value in channels))
    payload = b"".join(blocks)
    board_count = len(blocks)
    body = (
        struct.pack(
            "<BBHIQH", PROTOCOL_VERSION, TYPE_SAMPLE, flags, sequence, timestamp_us, sample_rate_hz
        )
        + bytes((board_count, board_count * 8, gain_code, 0))
        + struct.pack("<H", len(payload))
        + payload
    )
    return SYNC + body + struct.pack("<H", crc16_ccitt_false(body))


def parse_device_info(frame: ControlFrame) -> DeviceInfo:
    if frame.frame_type != TYPE_DEVICE_INFO or len(frame.payload) != 24:
        raise ValueError("not a valid DEVICE_INFO frame")
    payload = frame.payload
    return DeviceInfo(
        firmware_version=(payload[0], payload[1], payload[2]),
        protocol_version=payload[3],
        hardware_type=payload[4],
        capabilities=struct.unpack_from("<I", payload, 5)[0],
        max_board_count=payload[9],
        sample_rate_mask=payload[10],
        output_format_mask=payload[11],
        serial_baud=struct.unpack_from("<I", payload, 12)[0],
        device_id=struct.unpack_from("<Q", payload, 16)[0],
    )


def parse_device_status(frame: ControlFrame) -> DeviceStatus:
    if frame.frame_type != TYPE_DEVICE_STATUS or len(frame.payload) != 54:
        raise ValueError("not a valid DEVICE_STATUS frame")
    payload = frame.payload
    return DeviceStatus(
        acquisition_state=payload[0],
        output_format=payload[1],
        profile=payload[2],
        board_count=payload[3],
        sample_rate_hz=struct.unpack_from("<H", payload, 4)[0],
        gain_code=payload[6],
        channel_enabled_mask=payload[7],
        register_verified=bool(payload[8]),
        ads1299_id=payload[9],
        next_sequence=struct.unpack_from("<I", payload, 10)[0],
        uptime_us=struct.unpack_from("<Q", payload, 14)[0],
        counters=struct.unpack_from("<8I", payload, 22),
    )


class StreamDecoder:
    """Incremental decoder tolerant of arbitrary chunking and corrupt bytes."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self.crc_errors = 0
        self.format_errors = 0
        self.discarded_bytes = 0

    def reset(self) -> None:
        self._buffer.clear()

    def feed(self, data: bytes) -> list[DecodedFrame]:
        self._buffer.extend(data)
        frames: list[DecodedFrame] = []
        while True:
            sync_index = self._buffer.find(SYNC)
            if sync_index < 0:
                keep = 1 if self._buffer.endswith(SYNC[:1]) else 0
                self.discarded_bytes += len(self._buffer) - keep
                self._buffer[:] = self._buffer[-1:] if keep else b""
                break
            if sync_index:
                self.discarded_bytes += sync_index
                del self._buffer[:sync_index]
            if len(self._buffer) < 4:
                break
            if self._buffer[2] != PROTOCOL_VERSION:
                self._drop_one_format_error()
                continue
            frame_type = self._buffer[3]
            if frame_type == TYPE_SAMPLE:
                if len(self._buffer) < 26:
                    break
                payload_length = struct.unpack_from("<H", self._buffer, 24)[0]
                total_length = 28 + payload_length
            elif frame_type in {
                TYPE_DEVICE_INFO,
                TYPE_DEVICE_STATUS,
                TYPE_ACK,
                TYPE_EVENT,
                TYPE_COMMAND,
            }:
                if len(self._buffer) < 12:
                    break
                payload_length = struct.unpack_from("<H", self._buffer, 10)[0]
                total_length = 14 + payload_length
            else:
                self._drop_one_format_error()
                continue
            if total_length > 4096:
                self._drop_one_format_error()
                continue
            if len(self._buffer) < total_length:
                break
            candidate = bytes(self._buffer[:total_length])
            expected_crc = struct.unpack_from("<H", candidate, total_length - 2)[0]
            actual_crc = crc16_ccitt_false(candidate[2:-2])
            if expected_crc != actual_crc:
                self.crc_errors += 1
                del self._buffer[0]
                continue
            frame = self._decode_valid(candidate)
            if frame is None:
                self._drop_one_format_error()
                continue
            frames.append(frame)
            del self._buffer[:total_length]
        return frames

    def _drop_one_format_error(self) -> None:
        self.format_errors += 1
        del self._buffer[0]

    @staticmethod
    def _decode_valid(frame: bytes) -> DecodedFrame | None:
        frame_type = frame[3]
        if frame_type == TYPE_SAMPLE:
            flags, sequence, timestamp_us, rate = struct.unpack_from("<HIQH", frame, 4)
            board_count = frame[20]
            channel_count = frame[21]
            gain_code = frame[22]
            payload_length = struct.unpack_from("<H", frame, 24)[0]
            if not 1 <= board_count <= 4:
                return None
            if payload_length != board_count * 27 or channel_count != board_count * 8:
                return None
            payload = frame[26 : 26 + payload_length]
            if any((payload[i * 27] & 0xF0) != 0xC0 for i in range(board_count)):
                return None
            return SampleFrame(
                flags,
                sequence,
                timestamp_us,
                rate,
                board_count,
                channel_count,
                gain_code,
                payload,
                frame,
            )
        request_id = struct.unpack_from("<I", frame, 4)[0]
        payload_length = struct.unpack_from("<H", frame, 10)[0]
        return ControlFrame(
            frame_type,
            request_id,
            frame[8],
            frame[9],
            frame[12 : 12 + payload_length],
            frame,
        )


class OpenBCIStreamDecoder:
    """Incremental decoder for standard 33-byte Cyton packets."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self.discarded_bytes = 0

    def feed(self, data: bytes) -> list[OpenBCIPacket]:
        self._buffer.extend(data)
        packets: list[OpenBCIPacket] = []
        while True:
            try:
                start = self._buffer.index(0xA0)
            except ValueError:
                self.discarded_bytes += len(self._buffer)
                self._buffer.clear()
                break
            if start:
                self.discarded_bytes += start
                del self._buffer[:start]
            if len(self._buffer) < 33:
                break
            if self._buffer[32] & 0xF0 != 0xC0:
                self.discarded_bytes += 1
                del self._buffer[0]
                continue
            packet = bytes(self._buffer[:33])
            channels = tuple(decode_s24(packet[2 + i * 3 : 5 + i * 3]) for i in range(8))
            packets.append(OpenBCIPacket(packet[1], channels, packet[26:32], packet[32]))
            del self._buffer[:33]
        return packets
