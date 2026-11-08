"""Transport abstraction plus virtual and Wi-Fi Direct implementations."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import socket
import struct
import time
from abc import ABC, abstractmethod
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp
import numpy as np
from bleak import BleakClient, BleakScanner

from .protocol import (
    CAP_AUTO_PROTOCOL_SESSION,
    CAP_BCIKIT_RAW_V1,
    CAP_BINARY_CONTROL,
    CAP_CHANNEL_MASK,
    CAP_CRC16,
    CAP_DRDY_TIMESTAMP,
    CAP_INPUT_SHORT,
    CAP_INTERNAL_TEST,
    CAP_MULTI_BOARD,
    CAP_UNIFORM_PGA_GAIN,
    CAP_WIFI_BCIKIT_CONTROL,
    CAP_WIFI_BCIKIT_STREAM,
    CMD_GET_INFO,
    CMD_GET_STATUS,
    CMD_PING,
    CMD_RESET_STATS,
    CMD_SET_BOARD_COUNT,
    CMD_SET_CHANNEL_MASK,
    CMD_SET_GAIN,
    CMD_SET_OUTPUT_FORMAT,
    CMD_SET_PROFILE,
    CMD_SET_SAMPLE_RATE,
    CMD_START,
    CMD_STOP,
    FLAG_DROPPED_BEFORE,
    FLAG_REGISTER_VERIFIED,
    FLAG_TEST_SIGNAL,
    FLAG_TIME_VALID,
    STATUS_BUSY,
    STATUS_INVALID_COMMAND,
    STATUS_INVALID_LENGTH,
    STATUS_INVALID_VALUE,
    STATUS_OK,
    TYPE_ACK,
    TYPE_COMMAND,
    TYPE_DEVICE_INFO,
    TYPE_DEVICE_STATUS,
    ControlFrame,
    SampleFrame,
    StreamDecoder,
    encode_control_frame,
    encode_sample_frame,
    microvolts_per_count,
)

logger = logging.getLogger(__name__)

BLE_SERVICE_UUID = "7d2ea28a-f7bd-485a-bd9d-92ad6ecfe93e"
BLE_CONTROL_RX_UUID = "7d2ea28b-f7bd-485a-bd9d-92ad6ecfe93e"
BLE_CONTROL_TX_UUID = "7d2ea28c-f7bd-485a-bd9d-92ad6ecfe93e"
BLE_SAMPLE_TX_UUID = "7d2ea28d-f7bd-485a-bd9d-92ad6ecfe93e"


@dataclass(frozen=True)
class BleDevice:
    """A verified-name BCIKit BLE advertising candidate for the connection UI."""

    identifier: str
    name: str
    rssi: int | None = None

    @property
    def label(self) -> str:
        signal = f" · {self.rssi} dBm" if self.rssi is not None else ""
        return f"{self.name}{signal}"


class TransportError(RuntimeError):
    """A user-actionable transport failure."""


class Transport(ABC):
    description = "数据源"

    @abstractmethod
    async def open(self) -> None: ...

    @abstractmethod
    async def read(self, size: int = 4096) -> bytes: ...

    @abstractmethod
    async def write(self, data: bytes) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...


class VirtualTransport(Transport):
    """Deterministic BCIKit device simulator speaking the real binary protocol."""

    description = "虚拟 EEG 设备"
    SCENARIOS = ("normal", "alpha", "blink", "line_noise", "poor_contact")

    def __init__(
        self,
        scenario: str = "normal",
        seed: int = 42,
        drop_every: int = 0,
        ads1299_id: int = 0x3E,
        supports_uniform_gain: bool = True,
    ) -> None:
        if scenario not in self.SCENARIOS:
            raise ValueError(f"unknown virtual scenario: {scenario}")
        self.scenario = scenario
        self.drop_every = max(0, drop_every)
        self._rng = np.random.default_rng(seed)
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=256)
        self._decoder = StreamDecoder()
        self._open = False
        self._streaming = False
        self._producer: asyncio.Task[None] | None = None
        self._started_monotonic = 0.0
        self._sequence = 0
        self._timestamp_us = 0
        self._sample_rate = 250
        self._board_count = 1
        self._profile = 0
        self._channel_mask = 0xFF
        self._gain_code = 6
        self._ads1299_id = ads1299_id & 0xFF
        self._supports_uniform_gain = supports_uniform_gain
        self._drop_pending = False
        self._counters = [0] * 8

    async def open(self) -> None:
        if self._open:
            return
        self._open = True
        self._started_monotonic = time.monotonic()
        self._producer = asyncio.create_task(self._produce(), name="bcikit-virtual-device")

    async def read(self, size: int = 4096) -> bytes:
        del size
        if not self._open:
            raise TransportError("虚拟设备尚未打开")
        return await self._queue.get()

    async def write(self, data: bytes) -> None:
        if not self._open:
            raise TransportError("虚拟设备尚未打开")
        for frame in self._decoder.feed(data):
            if isinstance(frame, ControlFrame) and frame.frame_type == TYPE_COMMAND:
                await self._handle_command(frame)

    async def close(self) -> None:
        self._open = False
        self._streaming = False
        if self._producer is not None:
            self._producer.cancel()
            with suppress(asyncio.CancelledError):
                await self._producer
        self._producer = None

    def set_scenario(self, scenario: str) -> None:
        if scenario not in self.SCENARIOS:
            raise ValueError(f"unknown virtual scenario: {scenario}")
        self.scenario = scenario

    async def _put(self, data: bytes) -> None:
        if self._queue.full():
            with suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
            self._drop_pending = True
            self._counters[6] += 1
        self._queue.put_nowait(data)

    async def _handle_command(self, frame: ControlFrame) -> None:
        command = frame.code
        status = STATUS_OK
        payload = b""
        response_type = TYPE_ACK
        if command == CMD_PING:
            payload = frame.payload
        elif command == CMD_GET_INFO:
            response_type = TYPE_DEVICE_INFO
            capabilities = (
                CAP_BCIKIT_RAW_V1
                | CAP_MULTI_BOARD
                | CAP_DRDY_TIMESTAMP
                | CAP_CRC16
                | CAP_INTERNAL_TEST
                | CAP_INPUT_SHORT
                | CAP_CHANNEL_MASK
                | CAP_BINARY_CONTROL
                | CAP_WIFI_BCIKIT_STREAM
                | CAP_WIFI_BCIKIT_CONTROL
                | CAP_AUTO_PROTOCOL_SESSION
                | (CAP_UNIFORM_PGA_GAIN if self._supports_uniform_gain else 0)
            )
            payload = (
                bytes((0, 6, 0, 1, 0xFE))
                + struct.pack("<I", capabilities)
                + bytes((4, 0b11, 0b01))
                + struct.pack("<I", 115_200)
                + struct.pack("<Q", 0x5649525455414C01)
            )
        elif command == CMD_GET_STATUS:
            response_type = TYPE_DEVICE_STATUS
            payload = self._status_payload()
        elif command == CMD_STOP:
            self._streaming = False
        elif command == CMD_START:
            self._streaming = True
        elif command in {
            CMD_SET_OUTPUT_FORMAT,
            CMD_SET_SAMPLE_RATE,
            CMD_SET_BOARD_COUNT,
            CMD_SET_GAIN,
            CMD_SET_PROFILE,
            CMD_SET_CHANNEL_MASK,
        }:
            if command == CMD_SET_GAIN and not self._supports_uniform_gain:
                status = STATUS_INVALID_COMMAND
            else:
                status = (
                    STATUS_BUSY if self._streaming else self._apply_config(command, frame.payload)
                )
        elif command == CMD_RESET_STATS:
            self._counters = [0] * 8
        else:
            status = STATUS_INVALID_COMMAND
        await self._put(
            encode_control_frame(response_type, frame.request_id, command, status, payload)
        )

    def _apply_config(self, command: int, payload: bytes) -> int:
        expected = 2 if command == CMD_SET_SAMPLE_RATE else 1
        if len(payload) != expected:
            return STATUS_INVALID_LENGTH
        if command == CMD_SET_OUTPUT_FORMAT:
            return STATUS_OK if payload[0] == 0 else STATUS_INVALID_VALUE
        if command == CMD_SET_SAMPLE_RATE:
            rate = int.from_bytes(payload, "little")
            if rate not in (250, 500):
                return STATUS_INVALID_VALUE
            self._sample_rate = rate
        elif command == CMD_SET_BOARD_COUNT:
            if payload[0] not in range(1, 5):
                return STATUS_INVALID_VALUE
            self._board_count = payload[0]
        elif command == CMD_SET_PROFILE:
            if payload[0] not in range(4):
                return STATUS_INVALID_VALUE
            self._profile = payload[0]
        elif command == CMD_SET_CHANNEL_MASK:
            self._channel_mask = payload[0]
        elif command == CMD_SET_GAIN:
            if payload[0] not in range(7):
                return STATUS_INVALID_VALUE
            self._gain_code = payload[0]
        return STATUS_OK

    def _status_payload(self) -> bytes:
        uptime_us = int((time.monotonic() - self._started_monotonic) * 1_000_000)
        return (
            bytes((int(self._streaming), 0, self._profile, self._board_count))
            + struct.pack("<H", self._sample_rate)
            + bytes((self._gain_code, self._channel_mask, 1, self._ads1299_id))
            + struct.pack("<IQ8I", self._sequence, uptime_us, *self._counters)
        )

    async def _produce(self) -> None:
        next_tick = asyncio.get_running_loop().time()
        while self._open:
            if not self._streaming:
                await asyncio.sleep(0.01)
                next_tick = asyncio.get_running_loop().time()
                continue
            period = 1.0 / self._sample_rate
            next_tick += period
            self._sequence = (self._sequence + 1) & 0xFFFFFFFF
            self._timestamp_us += int(round(1_000_000 / self._sample_rate))
            self._counters[0] += 1
            if self.drop_every and self._sequence % self.drop_every == 0:
                self._drop_pending = True
                self._counters[6] += 1
            else:
                flags = FLAG_TIME_VALID | FLAG_REGISTER_VERIFIED
                if self._profile == 2:
                    flags |= FLAG_TEST_SIGNAL
                if self._drop_pending:
                    flags |= FLAG_DROPPED_BEFORE
                    self._drop_pending = False
                physical_boards = [self._channels_for_board(i) for i in range(self._board_count)]
                wire_order = list(reversed(physical_boards))
                await self._put(
                    encode_sample_frame(
                        flags=flags,
                        sequence=self._sequence,
                        timestamp_us=self._timestamp_us,
                        sample_rate_hz=self._sample_rate,
                        gain_code=self._gain_code,
                        board_channels_wire_order=wire_order,
                    )
                )
                self._counters[1] += 1
            await asyncio.sleep(max(0.0, next_tick - asyncio.get_running_loop().time()))

    def _channels_for_board(self, board: int) -> list[int]:
        t = self._timestamp_us / 1_000_000.0
        uv_per_count = microvolts_per_count(self._gain_code)
        values: list[int] = []
        for channel in range(8):
            amplitude = 6.0 + channel * 0.45 + board
            signal_uv = amplitude * math.sin(2 * math.pi * (8.0 + channel * 0.35) * t)
            signal_uv += float(self._rng.normal(0.0, 1.8))
            if self.scenario == "alpha" and channel in (6, 7):
                signal_uv += 28.0 * math.sin(2 * math.pi * 10.0 * t)
            elif self.scenario == "blink" and channel in (0, 1):
                phase = t % 3.0
                if phase < 0.28:
                    signal_uv += 120.0 * math.sin(math.pi * phase / 0.28)
            elif self.scenario == "line_noise":
                signal_uv += 22.0 * math.sin(2 * math.pi * 50.0 * t)
            elif self.scenario == "poor_contact" and channel == 3:
                signal_uv += 55.0 * math.sin(2 * math.pi * 0.35 * t)
                signal_uv += float(self._rng.normal(0.0, 20.0))
            if self._profile == 2:
                signal_uv = 35.0 * math.sin(2 * math.pi * 0.9765625 * t)
            elif self._profile == 3:
                signal_uv = float(self._rng.normal(0.0, 0.7))
            values.append(int(np.clip(round(signal_uv / uv_per_count), -(1 << 23), (1 << 23) - 1)))
        return values


class BleTransport(Transport):
    """BCIKit BLE v1 transport with separate control/sample notification decoders."""

    sample_timeout_seconds = 3.0

    def __init__(self, device: BleDevice, timeout: float = 12.0) -> None:
        self.device = device
        self.description = f"蓝牙设备 {device.name}"
        self.timeout = timeout
        self._client: BleakClient | None = None
        self._frames: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=512)
        self._control_decoder = StreamDecoder()
        self._sample_decoder = StreamDecoder()
        self._control_subscribed = False
        self._sample_subscribed = False
        self._closed = False

    @classmethod
    async def discover(cls, timeout: float = 5.0) -> list[BleDevice]:
        """Scan for BCIKit-named peripherals; GATT UUID is verified after connect."""
        try:
            discovered = await BleakScanner.discover(timeout=timeout)
        except Exception as error:
            raise TransportError(
                "无法扫描蓝牙设备。请确认 macOS 蓝牙已开启，并允许 BCIKit Studio 使用蓝牙。"
            ) from error
        devices: list[BleDevice] = []
        for device in discovered:
            name = device.name or ""
            if not name.startswith("BCIKit-"):
                continue
            rssi = getattr(device, "rssi", None)
            devices.append(BleDevice(str(device.address), name, rssi))
        return sorted(devices, key=lambda device: device.name)

    async def open(self) -> None:
        if self._client is not None and self._client.is_connected:
            return
        self._closed = False
        self._control_decoder = StreamDecoder()
        self._sample_decoder = StreamDecoder()
        try:
            client = BleakClient(self.device.identifier, timeout=self.timeout)
            await client.connect()
            services = client.services
            service = services.get_service(BLE_SERVICE_UUID)
            if service is None:
                raise TransportError(
                    f"{self.device.name} 未提供 BCIKit BLE v1 服务，可能不是目标设备或固件过旧。"
                )
            for uuid in (BLE_CONTROL_RX_UUID, BLE_CONTROL_TX_UUID, BLE_SAMPLE_TX_UUID):
                if services.get_characteristic(uuid) is None:
                    raise TransportError(f"{self.device.name} 的 BCIKit BLE v1 特征不完整。")
            self._client = client
            await client.start_notify(
                BLE_CONTROL_TX_UUID,
                lambda _sender, data: self._accept_notification(self._control_decoder, data),
            )
            self._control_subscribed = True
            await client.start_notify(
                BLE_SAMPLE_TX_UUID,
                lambda _sender, data: self._accept_notification(self._sample_decoder, data),
            )
            self._sample_subscribed = True
            logger.info("蓝牙建链：已连接 %s 并订阅控制与采样通知", self.device.name)
        except Exception as error:
            await self.close()
            if isinstance(error, TransportError):
                raise
            raise TransportError(f"无法连接蓝牙设备 {self.device.name}：{error}") from error

    async def read(self, size: int = 4096) -> bytes:
        del size
        frame = await self._frames.get()
        if frame is None:
            raise TransportError("蓝牙连接已断开")
        return frame

    async def write(self, data: bytes) -> None:
        client = self._client
        if client is None or not client.is_connected:
            raise TransportError("蓝牙 GATT 连接尚未建立")
        try:
            await client.write_gatt_char(BLE_CONTROL_RX_UUID, data, response=True)
        except Exception as error:
            raise TransportError(f"蓝牙控制写入失败：{error}") from error

    async def close(self) -> None:
        self._closed = True
        client = self._client
        self._client = None
        if client is not None:
            if self._control_subscribed:
                with suppress(Exception):
                    await client.stop_notify(BLE_CONTROL_TX_UUID)
            if self._sample_subscribed:
                with suppress(Exception):
                    await client.stop_notify(BLE_SAMPLE_TX_UUID)
            with suppress(Exception):
                await client.disconnect()
        self._control_subscribed = False
        self._sample_subscribed = False
        self._put_frame(None)

    def _accept_notification(self, decoder: StreamDecoder, data: bytearray) -> None:
        # Each GATT characteristic is an independent fragmented byte stream.
        # Reassemble it here, then forward only complete wire frames so control
        # and sample fragments can never interleave in DeviceSession's decoder.
        for frame in decoder.feed(bytes(data)):
            self._put_frame(frame.wire)

    def _put_frame(self, frame: bytes | None) -> None:
        if self._frames.full():
            with suppress(asyncio.QueueEmpty):
                self._frames.get_nowait()
            logger.warning("蓝牙接收队列已满，丢弃最旧完整帧")
        self._frames.put_nowait(frame)


class WiFiTransport(Transport):
    """BCIKit Wi-Fi Direct HTTP bootstrap and reverse TCP byte stream."""

    description = "BCIKit Wi-Fi 设备"

    def __init__(
        self,
        device_ip: str = "192.168.4.1",
        device_port: int = 80,
        timeout: float = 5.0,
        require_softap_subnet: bool = True,
    ) -> None:
        self.device_ip = device_ip
        self.device_port = device_port
        self.timeout = timeout
        self.require_softap_subnet = require_softap_subnet
        self.board_info: dict[str, Any] = {}
        self.all_info: dict[str, Any] = {}
        self._http: aiohttp.ClientSession | None = None
        self._server: asyncio.AbstractServer | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._accepted: asyncio.Future[None] | None = None
        self._configured = False
        self.output_mode = "auto"

    async def open(self) -> None:
        if self._reader is not None:
            return
        # Validate the active route before opening HTTP. This turns a confusing
        # system-level "No route to host" into an actionable hotspot diagnosis.
        host_ip = self._local_ip_for_device()
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        self._http = aiohttp.ClientSession(timeout=timeout)
        try:
            logger.info("Wi-Fi 建链：查询 /board 和 /all")
            self.board_info = await self._json_request("GET", "/board")
            self.all_info = await self._json_request("GET", "/all")
            loop = asyncio.get_running_loop()
            self._accepted = loop.create_future()
            self._server = await asyncio.start_server(self._on_connection, "0.0.0.0", 0)
            socket_info = self._server.sockets[0].getsockname()
            request = {
                "ip": host_ip,
                "port": int(socket_info[1]),
                "output": "auto",
                "delimiter": False,
                "latency": 10_000,
            }
            try:
                await self._json_request("POST", "/tcp", request)
                self.output_mode = "auto"
                logger.info("Wi-Fi 建链：已请求 output=auto，等待 PING 认领原生会话")
            except TransportError as error:
                if not str(error).startswith("HTTP 506:"):
                    raise
                request["output"] = "bcikit"
                await self._json_request("POST", "/tcp", request)
                self.output_mode = "bcikit"
                logger.warning("设备不支持 output=auto，已回退到旧固件兼容模式 output=bcikit")
            self._configured = True
            await asyncio.wait_for(self._accepted, timeout=self.timeout)
            logger.info("Wi-Fi 建链：设备已反向连接本机 TCP listener")
        except TransportError:
            logger.warning("Wi-Fi 建链失败：设备热点路由不可用")
            await self.close()
            raise
        except Exception:
            logger.exception("Wi-Fi 建链失败")
            await self.close()
            raise

    async def read(self, size: int = 4096) -> bytes:
        if self._reader is None:
            raise TransportError("设备 TCP 数据连接尚未建立")
        data = await self._reader.read(size)
        if not data:
            raise TransportError("设备关闭了 TCP 数据连接")
        return data

    async def write(self, data: bytes) -> None:
        if self._writer is None:
            raise TransportError("设备 TCP 数据连接尚未建立")
        self._writer.write(data)
        await self._writer.drain()

    async def close(self) -> None:
        logger.info("Wi-Fi 建链：关闭 TCP 和 HTTP 资源")
        if self._configured and self._http is not None:
            with suppress(Exception):
                await self._json_request("DELETE", "/tcp")
        self._configured = False
        if self._writer is not None:
            self._writer.close()
            with suppress(Exception):
                await self._writer.wait_closed()
        self._writer = None
        self._reader = None
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        self._server = None
        if self._http is not None:
            await self._http.close()
        self._http = None

    async def emergency_stop(self) -> None:
        if self._http is None:
            return
        with suppress(Exception):
            await self._json_request("GET", "/stream/stop")

    async def _on_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        if self._reader is not None:
            writer.close()
            await writer.wait_closed()
            return
        self._reader = reader
        self._writer = writer
        if self._accepted is not None and not self._accepted.done():
            self._accepted.set_result(None)

    def _local_ip_for_device(self) -> str:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect((self.device_ip, self.device_port))
            ip = probe.getsockname()[0]
            if self.require_softap_subnet and not ip.startswith("192.168.4."):
                raise TransportError(
                    "当前 Mac 未通过 OpenBCI-XXXX 热点访问设备"
                    f"（当前路由本机地址为 {ip}）。请在 macOS Wi-Fi 中连接设备热点，"
                    "保留“无互联网连接”的网络，并暂时关闭会接管 192.168.x.x 路由的 VPN。"
                )
            return ip
        finally:
            probe.close()

    async def _json_request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if self._http is None:
            raise TransportError("HTTP 客户端尚未初始化")
        url = f"http://{self.device_ip}:{self.device_port}{path}"
        try:
            async with self._http.request(method, url, json=body) as response:
                raw = await response.read()
                if response.status >= 400:
                    detail = raw.decode("utf-8", errors="replace")
                    advice = {
                        503: "设备正忙，请稍后重试。",
                        409: (
                            "设备正被另一协议会话占用。请先停止 OpenBCI 软件"
                            "或等待旧 TCP 会话释放。"
                        ),
                        504: "设备无法反向连接本机，请允许 BCIKit Studio 接收入站连接。",
                        506: "设备不支持所请求的输出模式；Studio 会自动尝试兼容模式。",
                    }.get(response.status, "请检查设备状态和连接参数。")
                    raise TransportError(f"HTTP {response.status}: {detail}。{advice}")
                if not raw:
                    return {}
                try:
                    value = json.loads(raw)
                except json.JSONDecodeError as error:
                    raise TransportError(f"设备返回了无效 JSON：{error}") from error
                return value if isinstance(value, dict) else {"value": value}
        except (aiohttp.ClientError, TimeoutError) as error:
            raise TransportError(
                "无法访问 192.168.4.1。请先连接 OpenBCI-XXXX 热点，并确认设备已上电。"
            ) from error


class ReplayTransport(VirtualTransport):
    """Recorded BCIKit frames exposed through the same byte-stream interface."""

    description = "历史记录回放"

    def __init__(self, session_dir: Path, speed: float = 1.0) -> None:
        super().__init__(scenario="normal")
        if speed <= 0:
            raise ValueError("replay speed must be positive")
        self.session_dir = session_dir
        self.speed = speed
        self._replay_frames: list[SampleFrame] = []
        self._replay_index = 0

    async def open(self) -> None:
        raw_path = self.session_dir / "raw.bciraw"
        if not raw_path.exists():
            raise TransportError(f"找不到原始记录：{raw_path}")
        decoder = StreamDecoder()
        frames: list[SampleFrame] = []
        with raw_path.open("rb") as stream:
            while chunk := stream.read(64 * 1024):
                frames.extend(
                    frame for frame in decoder.feed(chunk) if isinstance(frame, SampleFrame)
                )
        if not frames:
            raise TransportError("记录中没有有效的 BCIKit SAMPLE 帧")
        self._replay_frames = frames
        self._sample_rate = frames[0].sample_rate_hz
        self._board_count = frames[0].board_count
        self._replay_index = 0
        await super().open()

    async def _produce(self) -> None:
        previous_timestamp: int | None = None
        while self._open:
            if not self._streaming:
                if self._replay_index >= len(self._replay_frames):
                    self._replay_index = 0
                previous_timestamp = None
                await asyncio.sleep(0.01)
                continue
            if self._replay_index >= len(self._replay_frames):
                self._streaming = False
                continue
            frame = self._replay_frames[self._replay_index]
            if previous_timestamp is not None:
                delay = max(
                    0.0,
                    (frame.timestamp_us - previous_timestamp) / 1_000_000 / self.speed,
                )
                await asyncio.sleep(delay)
            previous_timestamp = frame.timestamp_us
            self._sequence = frame.sequence
            self._timestamp_us = frame.timestamp_us
            await self._put(frame.wire)
            self._replay_index += 1
