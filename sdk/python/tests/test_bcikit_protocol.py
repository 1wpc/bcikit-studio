import struct
import unittest

from bcikit_protocol import (
    CAP_AUTO_PROTOCOL_SESSION,
    CAP_WIFI_BCIKIT_CONTROL,
    CMD_PING,
    TYPE_COMMAND,
    TYPE_DEVICE_INFO,
    TYPE_DEVICE_STATUS,
    ControlFrame,
    OpenBCIStreamDecoder,
    SampleFrame,
    StreamDecoder,
    crc16_ccitt_false,
    encode_command,
    parse_device_info,
    parse_device_status,
)


def make_sample(sequence: int = 7) -> bytes:
    channels = b"".join(
        value.to_bytes(3, "big", signed=True)
        for value in (1, -2, 3, -4, 5, -6, 7, -8)
    )
    payload = b"\xC0\x00\x00" + channels
    header = (
        b"\x01\x01"
        + struct.pack("<HIQH", 0x0005, sequence, 123456789, 250)
        + bytes((1, 8, 6, 0))
        + struct.pack("<H", len(payload))
    )
    body = header + payload
    return b"\xA5\x5A" + body + struct.pack("<H", crc16_ccitt_false(body))


def make_control(frame_type: int, request_id: int, payload: bytes) -> bytes:
    body = struct.pack("<BBIBBH", 1, frame_type, request_id, 0, 0, len(payload)) + payload
    return b"\xA5\x5A" + body + struct.pack("<H", crc16_ccitt_false(body))


class BCIKitProtocolTests(unittest.TestCase):
    def test_crc_standard_vector(self):
        self.assertEqual(crc16_ccitt_false(b"123456789"), 0x29B1)

    def test_sample_chunking_and_signed_channels(self):
        frame = make_sample()
        self.assertEqual(len(frame), 55)
        self.assertEqual(frame[-2:], b"\x96\x9d")
        decoder = StreamDecoder()
        output = []
        for chunk in (b"startup text\r\n" + frame[:5], frame[5:19], frame[19:]):
            output.extend(decoder.feed(chunk))
        self.assertEqual(len(output), 1)
        sample = output[0]
        self.assertIsInstance(sample, SampleFrame)
        self.assertEqual(sample.sequence, 7)
        self.assertEqual(sample.channel_raw(0, 1), -2)
        self.assertEqual(sample.channel_raw(0, 7), -8)

    def test_command_round_trip(self):
        wire = encode_command(0x12345678, CMD_PING, b"hello")
        self.assertEqual(
            wire.hex(" "),
            "a5 5a 01 80 78 56 34 12 0b 00 05 00 68 65 6c 6c 6f 5b 66",
        )
        frames = StreamDecoder().feed(wire)
        self.assertEqual(len(frames), 1)
        command = frames[0]
        self.assertIsInstance(command, ControlFrame)
        self.assertEqual(command.frame_type, TYPE_COMMAND)
        self.assertEqual(command.request_id, 0x12345678)
        self.assertEqual(command.payload, b"hello")

    def test_crc_error_resynchronizes(self):
        bad = bytearray(make_sample(1))
        bad[30] ^= 0x80
        decoder = StreamDecoder()
        output = decoder.feed(bytes(bad) + make_sample(2))
        self.assertEqual(decoder.crc_errors, 1)
        self.assertEqual(len(output), 1)
        self.assertEqual(output[0].sequence, 2)

    def test_device_info_and_status_payloads(self):
        info_payload = (
            bytes((0, 6, 0, 1, 1))
            + struct.pack("<I", 0x1FFF)
            + bytes((4, 3, 3))
            + struct.pack("<I", 1_000_000)
            + struct.pack("<Q", 0x1122334455667788)
        )
        info_frame = StreamDecoder().feed(
            make_control(TYPE_DEVICE_INFO, 4, info_payload)
        )[0]
        info = parse_device_info(info_frame)
        self.assertEqual(info.firmware_version, (0, 6, 0))
        self.assertEqual(info.device_id, 0x1122334455667788)
        self.assertTrue(info.capabilities & CAP_WIFI_BCIKIT_CONTROL)
        self.assertTrue(info.capabilities & CAP_AUTO_PROTOCOL_SESSION)

        status_payload = (
            bytes((1, 0, 2, 1))
            + struct.pack("<H", 250)
            + bytes((6, 0xFF, 1, 0x3E))
            + struct.pack("<IQ8I", 99, 123456, *range(8))
        )
        status_frame = StreamDecoder().feed(
            make_control(TYPE_DEVICE_STATUS, 5, status_payload)
        )[0]
        status = parse_device_status(status_frame)
        self.assertEqual(status.next_sequence, 99)
        self.assertEqual(status.counters, tuple(range(8)))

    def test_openbci_packet(self):
        channels = b"".join(
            value.to_bytes(3, "big", signed=True)
            for value in (100, -100, 200, -200, 300, -300, 400, -400)
        )
        packet = b"\xA0\xFE" + channels + b"\x00" * 6 + b"\xC0"
        decoder = OpenBCIStreamDecoder()
        result = decoder.feed(b"noise" + packet[:9])
        self.assertEqual(result, [])
        result = decoder.feed(packet[9:])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].sample_number, 0xFE)
        self.assertEqual(result[0].channels[1], -100)
        self.assertEqual(result[0].auxiliary, b"\x00" * 6)


if __name__ == "__main__":
    unittest.main()
