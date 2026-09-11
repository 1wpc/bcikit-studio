#!/usr/bin/env python3
"""Hardware smoke test for the BCIKit/OpenBCI WiFi Direct firmware.

Connect the computer to OpenBCI-XXXX before running this script.
Only the Python standard library is required.
"""

from __future__ import annotations

import argparse
import http.client
import json
import socket
import time

from bcikit_protocol import (
    CAP_AUTO_PROTOCOL_SESSION,
    CAP_WIFI_BCIKIT_CONTROL,
    CMD_GET_INFO,
    CMD_GET_STATUS,
    CMD_PING,
    CMD_SET_BOARD_COUNT,
    CMD_SET_CHANNEL_MASK,
    CMD_SET_OUTPUT_FORMAT,
    CMD_SET_PROFILE,
    CMD_SET_SAMPLE_RATE,
    CMD_START,
    CMD_STOP,
    ControlFrame,
    OpenBCIStreamDecoder,
    SampleFrame,
    StreamDecoder,
    encode_command,
    parse_device_info,
    parse_device_status,
)


def request(device_ip: str, method: str, path: str, body: dict | None = None):
    conn = http.client.HTTPConnection(device_ip, 80, timeout=5)
    payload = None if body is None else json.dumps(body)
    headers = {} if payload is None else {"Content-Type": "application/json"}
    conn.request(method, path, body=payload, headers=headers)
    response = conn.getresponse()
    data = response.read()
    conn.close()
    if response.status >= 400:
        raise RuntimeError(f"{method} {path}: HTTP {response.status}: {data!r}")
    return data


def expect_http_status(
    device_ip: str,
    expected_status: int,
    method: str,
    path: str,
    body: dict | None = None,
) -> bytes:
    conn = http.client.HTTPConnection(device_ip, 80, timeout=5)
    payload = None if body is None else json.dumps(body)
    headers = {} if payload is None else {"Content-Type": "application/json"}
    conn.request(method, path, body=payload, headers=headers)
    response = conn.getresponse()
    data = response.read()
    conn.close()
    if response.status != expected_status:
        raise RuntimeError(
            f"{method} {path}: expected HTTP {expected_status}, "
            f"got {response.status}: {data!r}"
        )
    return data


def local_ip_for(device_ip: str) -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((device_ip, 80))
        return probe.getsockname()[0]
    finally:
        probe.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="192.168.4.1")
    parser.add_argument(
        "--output", choices=("raw", "bcikit", "auto"), default="auto"
    )
    parser.add_argument("--seconds", type=float, default=5.0)
    args = parser.parse_args()

    print("board:", request(args.device, "GET", "/board").decode())
    print("all:", request(args.device, "GET", "/all").decode())

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("0.0.0.0", 0))
    listener.listen(1)
    listener.settimeout(5)
    host_ip = local_ip_for(args.device)
    port = listener.getsockname()[1]
    request(
        args.device,
        "POST",
        "/tcp",
        {
            "ip": host_ip,
            "port": port,
            "output": args.output,
            "delimiter": False,
            "latency": 10000,
        },
    )
    stream, peer = listener.accept()
    print(f"data socket connected from {peer[0]}:{peer[1]}")
    stream.settimeout(1)

    native_output = args.output != "raw"
    decoder = OpenBCIStreamDecoder() if not native_output else StreamDecoder()
    packet_count = 0
    byte_count = 0
    next_request_id = 1

    def native_command(command: int, payload: bytes = b"", timeout: float = 3.0):
        nonlocal byte_count, next_request_id, packet_count
        request_id = next_request_id
        next_request_id += 1
        stream.sendall(encode_command(request_id, command, payload))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                chunk = stream.recv(4096)
            except TimeoutError:
                continue
            if not chunk:
                raise ConnectionError("device closed the BCIKit TCP connection")
            byte_count += len(chunk)
            for frame in decoder.feed(chunk):
                if isinstance(frame, SampleFrame):
                    packet_count += 1
                elif isinstance(frame, ControlFrame) and frame.request_id == request_id:
                    if frame.status != 0:
                        raise RuntimeError(
                            f"command 0x{command:02X} failed with status {frame.status}"
                        )
                    return frame
        raise TimeoutError(f"timeout waiting for command 0x{command:02X}")

    try:
        if native_output:
            ping = native_command(CMD_PING, b"wifi-control")
            if ping.payload != b"wifi-control":
                raise RuntimeError("PING payload was not echoed")

            info = parse_device_info(native_command(CMD_GET_INFO))
            if not info.capabilities & CAP_WIFI_BCIKIT_CONTROL:
                raise RuntimeError(
                    "firmware does not advertise native WiFi control; flash v0.5.0+"
                )
            if args.output == "auto" and not (
                info.capabilities & CAP_AUTO_PROTOCOL_SESSION
            ):
                raise RuntimeError(
                    "firmware does not advertise automatic protocol sessions; "
                    "flash v0.6.0+"
                )
            if args.output == "auto":
                conflicting_port = port - 1 if port == 65535 else port + 1
                expect_http_status(
                    args.device,
                    409,
                    "POST",
                    "/tcp",
                    {
                        "ip": host_ip,
                        "port": conflicting_port,
                        "output": "raw",
                        "delimiter": False,
                    },
                )
                print("session lock: conflicting OpenBCI target rejected")
            print(
                f"native control: firmware={'.'.join(map(str, info.firmware_version))} "
                f"device_id={info.device_id:016X}"
            )

            native_command(CMD_STOP)
            native_command(CMD_SET_OUTPUT_FORMAT, b"\x00")
            status = parse_device_status(native_command(CMD_GET_STATUS))
            rate = status.sample_rate_hz if status.sample_rate_hz in (250, 500) else 250
            native_command(CMD_SET_SAMPLE_RATE, rate.to_bytes(2, "little"))
            native_command(CMD_SET_BOARD_COUNT, bytes((status.board_count,)))
            native_command(CMD_SET_PROFILE, bytes((status.profile,)))
            native_command(CMD_SET_CHANNEL_MASK, bytes((status.channel_enabled_mask,)))
            status = parse_device_status(native_command(CMD_GET_STATUS))
            print(
                f"configured: rate={status.sample_rate_hz} boards={status.board_count} "
                f"profile={status.profile} mask=0x{status.channel_enabled_mask:02X}"
            )
            native_command(CMD_START)
        else:
            request(args.device, "GET", "/stream/start")

        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            try:
                chunk = stream.recv(4096)
            except TimeoutError:
                continue
            if not chunk:
                break
            byte_count += len(chunk)
            frames = decoder.feed(chunk)
            if native_output:
                packet_count += sum(isinstance(frame, SampleFrame) for frame in frames)
            else:
                packet_count += len(frames)
    finally:
        if native_output:
            try:
                native_command(CMD_STOP)
            except (ConnectionError, OSError, RuntimeError, TimeoutError) as error:
                print(f"warning: native STOP failed: {error}")
        else:
            request(args.device, "GET", "/stream/stop")
        request(args.device, "DELETE", "/tcp")
        stream.close()
        listener.close()

    print(f"received {packet_count} valid frames, {byte_count} bytes")
    if packet_count == 0:
        raise RuntimeError("no valid frames received")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
