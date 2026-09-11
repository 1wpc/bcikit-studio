"""UART control smoke test for BCIKit ESP32 firmware v0.6.0.

Requires pyserial: python3 -m pip install pyserial
Usage: python3 bcikit_serial_smoke.py /dev/cu.usbserial-XXXX
"""

from __future__ import annotations

import argparse
import struct
import sys
import time

from bcikit_protocol import (
    CMD_GET_INFO,
    CMD_GET_STATUS,
    CMD_STOP,
    ControlFrame,
    StreamDecoder,
    TYPE_ACK,
    TYPE_DEVICE_INFO,
    TYPE_DEVICE_STATUS,
    encode_command,
    parse_device_info,
    parse_device_status,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("port")
    args = parser.parse_args()

    try:
        import serial
    except ImportError:
        print("pyserial is required: python3 -m pip install pyserial", file=sys.stderr)
        return 2

    decoder = StreamDecoder()
    next_request_id = 1

    with serial.Serial(args.port, 115_200, timeout=0.05) as device:
        time.sleep(1.5)  # DevKitC may reset when the port opens.
        device.reset_input_buffer()

        def request(command: int, payload: bytes = b"", timeout: float = 2.0):
            nonlocal next_request_id
            request_id = next_request_id
            next_request_id += 1
            device.write(encode_command(request_id, command, payload))
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                for frame in decoder.feed(device.read(512)):
                    if isinstance(frame, ControlFrame) and frame.request_id == request_id:
                        return frame
            raise TimeoutError(f"timeout waiting for command 0x{command:02X}")

        info_frame = request(CMD_GET_INFO)
        if info_frame.frame_type != TYPE_DEVICE_INFO:
            raise RuntimeError(f"unexpected GET_INFO response: {info_frame}")
        info = parse_device_info(info_frame)
        print(f"firmware={'.'.join(map(str, info.firmware_version))} "
              f"device_id={info.device_id:016X} capabilities=0x{info.capabilities:08X}")

        stop = request(CMD_STOP)
        if stop.frame_type != TYPE_ACK or stop.status != 0:
            raise RuntimeError(f"STOP failed: {stop}")
        status_frame = request(CMD_GET_STATUS)
        if status_frame.frame_type != TYPE_DEVICE_STATUS:
            raise RuntimeError(f"unexpected GET_STATUS response: {status_frame}")
        status = parse_device_status(status_frame)
        print(f"ads_id=0x{status.ads1299_id:02X} rate={status.sample_rate_hz} "
              f"boards={status.board_count} verified={status.register_verified}")

        print("UART control path passed; use bcikit_wifi_smoke.py for native sample data")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
