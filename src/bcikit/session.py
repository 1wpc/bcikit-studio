"""Device session state machine and real-time sample batching."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from contextlib import suppress
from dataclasses import asdict, replace

import numpy as np

from .models import (
    DiagnosticEvent,
    IntegrityStats,
    Marker,
    SampleBatch,
    SessionConfig,
    SessionState,
)
from .protocol import (
    CAP_AUTO_PROTOCOL_SESSION,
    CAP_BLE_BCIKIT_CONTROL,
    CAP_BLE_BCIKIT_STREAM,
    CAP_INPUT_SHORT,
    CAP_INTERNAL_TEST,
    CAP_MULTI_BOARD,
    CAP_WIFI_BCIKIT_CONTROL,
    CMD_GET_INFO,
    CMD_GET_STATUS,
    CMD_PING,
    CMD_RESET_STATS,
    CMD_SET_BOARD_COUNT,
    CMD_SET_CHANNEL_MASK,
    CMD_SET_OUTPUT_FORMAT,
    CMD_SET_PROFILE,
    CMD_SET_SAMPLE_RATE,
    CMD_START,
    CMD_STOP,
    PROFILE_INPUT_SHORT,
    PROFILE_INTERNAL_TEST,
    PROTOCOL_VERSION,
    STATUS_OK,
    ControlFrame,
    DeviceInfo,
    DeviceStatus,
    SampleFrame,
    StreamDecoder,
    encode_command,
    microvolts_per_count,
    parse_device_info,
    parse_device_status,
)
from .recording import SessionRecorder, utc_now_iso
from .transport import BleTransport, Transport, WiFiTransport

logger = logging.getLogger(__name__)


class CommandError(RuntimeError):
    def __init__(self, command: int, status: int) -> None:
        super().__init__(f"命令 0x{command:02X} 失败，设备状态码 {status}")
        self.command = command
        self.status = status


class DeviceSession:
    """Single-reader session with asynchronous command matching and batching."""

    def __init__(self, transport: Transport, config: SessionConfig | None = None) -> None:
        self.transport = transport
        self.config = config or SessionConfig()
        self.state = SessionState.DISCONNECTED
        self.state_queue: asyncio.Queue[SessionState] = asyncio.Queue()
        self.sample_queue: asyncio.Queue[SampleBatch] = asyncio.Queue(maxsize=8)
        self.diagnostic_queue: asyncio.Queue[DiagnosticEvent] = asyncio.Queue(maxsize=128)
        self.stats = IntegrityStats()
        self.device_info: DeviceInfo | None = None
        self.device_status: DeviceStatus | None = None
        self.last_error: str | None = None
        self.recorder = SessionRecorder()
        self._decoder = StreamDecoder()
        self._receiver: asyncio.Task[None] | None = None
        self._watchdog: asyncio.Task[None] | None = None
        self._status_poller: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[ControlFrame]] = {}
        self._next_request_id = 1
        self._closing = False
        self._last_sample_monotonic = 0.0
        self._last_device_timestamp_us: int | None = None
        self._marker_id = 0
        self._frames_for_batch: list[SampleFrame] = []
        self._last_decoder_counts = (0, 0, 0)
        self._had_streamed = False
        self._first_sample_reported = False

    @property
    def recording(self) -> bool:
        return self.recorder.active

    async def connect(self) -> None:
        if self.state != SessionState.DISCONNECTED:
            return
        self._closing = False
        self._set_state(SessionState.DISCOVERING)
        try:
            await self.transport.open()
            self._set_state(SessionState.LISTENING)
            self._receiver = asyncio.create_task(self._receive_loop(), name="bcikit-receiver")
            self._set_state(SessionState.CONFIGURING)
            await self._handshake()
            self._set_state(SessionState.READY)
        except Exception as error:
            await self._fail(error)
            raise

    async def start(self) -> None:
        if self.state not in {SessionState.READY, SessionState.STREAMING}:
            raise RuntimeError("设备尚未完成配置")
        if self.state == SessionState.STREAMING:
            return
        await self.send_command(CMD_START)
        self._first_sample_reported = False
        self._last_sample_monotonic = time.monotonic()
        self._had_streamed = True
        timeout = getattr(self.transport, "sample_timeout_seconds", 1.0)
        self._diagnostic(
            "info", f"START 已确认，正在等待第一帧 SAMPLE（超时阈值 {timeout:g} 秒）"
        )
        self._set_state(SessionState.STREAMING)
        self._watchdog = asyncio.create_task(self._watchdog_loop(), name="bcikit-watchdog")
        self._status_poller = asyncio.create_task(
            self._status_poll_loop(), name="bcikit-status-poller"
        )

    async def stop(self) -> None:
        if self.state not in {SessionState.READY, SessionState.STREAMING, SessionState.ERROR}:
            return
        previous = self.state
        self._set_state(SessionState.STOPPING)
        if self._watchdog is not None:
            self._watchdog.cancel()
            with suppress(asyncio.CancelledError):
                await self._watchdog
            self._watchdog = None
        if self._status_poller is not None:
            self._status_poller.cancel()
            with suppress(asyncio.CancelledError):
                await self._status_poller
            self._status_poller = None
        if previous != SessionState.ERROR:
            with suppress(Exception):
                await self.send_command(CMD_STOP, timeout=2.0)
        self._flush_batch()
        if self.recording:
            reason = "user_stop" if previous != SessionState.ERROR else "connection_error"
            self.stop_recording(reason)
        if previous != SessionState.ERROR:
            self._set_state(SessionState.READY)

    async def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        with suppress(Exception):
            await self.stop()
        if self.recording:
            self.stop_recording("application_exit")
        for pending in self._pending.values():
            if not pending.done():
                pending.cancel()
        self._pending.clear()
        if self._receiver is not None:
            self._receiver.cancel()
            with suppress(asyncio.CancelledError):
                await self._receiver
            self._receiver = None
        await self.transport.close()
        self._set_state(SessionState.DISCONNECTED)

    async def force_close(self) -> None:
        """Release local tasks and handles after an unresponsive transport shutdown.

        This deliberately does not wait for device-side STOP/HTTP acknowledgements.
        It is the last-resort path used by the UI so an unreachable board cannot
        leave the application connection controls permanently disabled.
        """
        self._closing = True
        if self.recording:
            self.stop_recording("forced_disconnect")
        for pending in self._pending.values():
            if not pending.done():
                pending.cancel()
        self._pending.clear()
        current = asyncio.current_task()
        tasks = [
            task
            for task in (self._watchdog, self._status_poller, self._receiver)
            if task is not None and task is not current
        ]
        self._watchdog = None
        self._status_poller = None
        self._receiver = None
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError, Exception):
                await task
        try:
            await asyncio.wait_for(self.transport.close(), timeout=1.0)
        except TimeoutError:
            logger.warning("强制断开时传输层关闭超时；已放弃等待设备端确认")
        except Exception as error:
            logger.warning("强制断开时传输层清理异常：%s", error)
        self._set_state(SessionState.DISCONNECTED)

    async def reconnect(self, attempts: tuple[float, ...] = (0.5, 1.0, 2.0)) -> bool:
        """Rebuild a failed Wi-Fi connection with bounded exponential backoff."""
        if not self._had_streamed or not isinstance(self.transport, WiFiTransport):
            return False
        if self._watchdog is not None:
            self._watchdog.cancel()
            with suppress(asyncio.CancelledError):
                await self._watchdog
            self._watchdog = None
        if self._status_poller is not None:
            self._status_poller.cancel()
            with suppress(asyncio.CancelledError):
                await self._status_poller
            self._status_poller = None
        current = asyncio.current_task()
        if self._receiver is not None and self._receiver is not current:
            self._receiver.cancel()
            with suppress(asyncio.CancelledError):
                await self._receiver
        self._receiver = None
        for number, delay in enumerate(attempts, start=1):
            self.stats.reconnects += 1
            self._diagnostic("warning", f"将在 {delay:.1f} 秒后进行第 {number} 次重连")
            await asyncio.sleep(delay)
            try:
                await self.transport.close()
                self._decoder = StreamDecoder()
                self._last_decoder_counts = (0, 0, 0)
                self._set_state(SessionState.DISCOVERING)
                await self.transport.open()
                self._set_state(SessionState.LISTENING)
                self._receiver = asyncio.create_task(
                    self._receive_loop(), name="bcikit-receiver-reconnected"
                )
                self._set_state(SessionState.CONFIGURING)
                await self._handshake()
                self._set_state(SessionState.READY)
                await self.start()
                self._diagnostic("info", "设备重连成功")
                return True
            except Exception as error:
                self._diagnostic("warning", f"第 {number} 次重连失败：{error}")
        self.last_error = "自动重连已达到 3 次上限，请检查热点和设备电源后手动重连"
        self._set_state(SessionState.ERROR)
        if self.recording:
            self.stop_recording("reconnect_exhausted")
        return False

    async def send_command(
        self, command: int, payload: bytes = b"", timeout: float = 3.0
    ) -> ControlFrame:
        request_id = self._next_request_id
        self._next_request_id = 1 if request_id == 0xFFFFFFFF else request_id + 1
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ControlFrame] = loop.create_future()
        self._pending[request_id] = future
        try:
            logger.debug(
                "发送命令 0x%02X，request_id=%d，payload=%d B",
                command,
                request_id,
                len(payload),
            )
            await self.transport.write(encode_command(request_id, command, payload))
            response = await asyncio.wait_for(future, timeout=timeout)
            logger.debug(
                "收到命令响应 0x%02X，request_id=%d，status=%d，payload=%d B",
                command,
                request_id,
                response.status,
                len(response.payload),
            )
            if response.status != STATUS_OK:
                raise CommandError(command, response.status)
            return response
        finally:
            self._pending.pop(request_id, None)

    async def read_status(self) -> DeviceStatus:
        """Read and retain the device's current configuration and counters."""
        status = parse_device_status(await self.send_command(CMD_GET_STATUS))
        self.device_status = status
        self._diagnostic(
            "info",
            "设备状态已读回："
            f"profile={status.profile}，"
            f"寄存器校验={'通过' if status.register_verified else '失败'}，"
            f"帧计数={status.counters[1]}",
        )
        return status

    async def reset_device_stats(self) -> DeviceStatus:
        """Clear firmware counters without changing the host's session history."""
        await self.send_command(CMD_RESET_STATS)
        self._diagnostic("info", "已请求设备清零采集与错误计数")
        return await self.read_status()

    async def set_profile(self, profile: int) -> DeviceStatus:
        """Safely switch the ADS1299 input profile and resume an active stream.

        Profile changes are accepted by firmware only while stopped. This helper
        preserves that transaction boundary and refuses to alter an active
        recording, preventing one session file from silently mixing test data
        and EEG data.
        """
        if profile not in range(4):
            raise ValueError("profile must be in 0..3")
        if self.state not in {SessionState.READY, SessionState.STREAMING}:
            raise RuntimeError("设备尚未就绪，不能切换输入模式")
        if self.recording:
            raise RuntimeError("请先停止记录，再切换芯片输入模式")
        if self.device_info is not None:
            if (
                profile == PROFILE_INTERNAL_TEST
                and not self.device_info.capabilities & CAP_INTERNAL_TEST
            ):
                raise RuntimeError("设备固件未声明支持 ADS1299 内部测试信号")
            if (
                profile == PROFILE_INPUT_SHORT
                and not self.device_info.capabilities & CAP_INPUT_SHORT
            ):
                raise RuntimeError("设备固件未声明支持 ADS1299 输入短接")

        resume_stream = self.state == SessionState.STREAMING
        if resume_stream:
            await self.stop()
        try:
            await self.send_command(CMD_SET_PROFILE, bytes((profile,)))
            status = await self.read_status()
            if status.profile != profile:
                raise RuntimeError(
                    f"芯片输入模式读回不一致：期望 {profile}，实际 {status.profile}"
                )
            if not status.register_verified:
                raise RuntimeError("ADS1299 寄存器校验失败，已拒绝使用该输入模式")
            self.config = replace(self.config, profile=profile)
            self._diagnostic("info", f"芯片输入模式已切换为 profile={profile}")
        except Exception as error:
            if resume_stream and self.state == SessionState.READY:
                try:
                    await self.start()
                except Exception as recovery_error:
                    # The profile command is the root failure. Keep it visible
                    # to the caller instead of replacing it with a secondary
                    # START error from a firmware rollback that also failed.
                    self._diagnostic(
                        "error",
                        "切换输入模式失败后，尝试恢复原采集也失败："
                        f"{recovery_error}",
                    )
            raise error
        if resume_stream:
            await self.start()
        return status

    def start_recording(self, name: str = "EEG记录") -> None:
        if self.state != SessionState.STREAMING:
            raise RuntimeError("开始采集后才能记录")
        if self.recording:
            return
        info = self.device_info
        metadata = {
            "software_version": "0.1.0",
            "protocol_version": PROTOCOL_VERSION,
            "firmware_version": (
                ".".join(map(str, info.firmware_version)) if info is not None else None
            ),
            "device_id": f"{info.device_id:016X}" if info is not None else None,
            "transport": self.transport.description,
            "config": asdict(self.config),
            "host": {"platform": "macOS"},
        }
        path = self.recorder.start(name, metadata)
        self._diagnostic("info", f"开始记录：{path.name}")

    def stop_recording(self, reason: str = "user_stop") -> None:
        path = self.recorder.finish(self.stats, reason)
        if path is not None:
            self._diagnostic("info", f"记录已保存：{path.name}")

    def add_marker(self, label: str, note: str = "") -> Marker:
        if not self.recording:
            raise RuntimeError("请先开始记录，再添加 Marker")
        self._marker_id += 1
        marker = Marker(
            self._marker_id,
            self._last_device_timestamp_us,
            utc_now_iso(),
            label.strip() or f"Marker {self._marker_id}",
            note.strip(),
        )
        self.recorder.append_marker(marker)
        self._diagnostic("info", f"Marker：{marker.label}")
        return marker

    async def _handshake(self) -> None:
        ping_payload = b"BCIKit Studio 0.1.0"
        ping = await self.send_command(CMD_PING, ping_payload)
        if ping.payload != ping_payload:
            raise RuntimeError("设备 PING 回显不一致")
        info = parse_device_info(await self.send_command(CMD_GET_INFO))
        if info.protocol_version != PROTOCOL_VERSION:
            raise RuntimeError(
                f"设备协议版本为 {info.protocol_version}，软件仅支持版本 {PROTOCOL_VERSION}"
            )
        if (
            isinstance(self.transport, WiFiTransport)
            and not info.capabilities & CAP_WIFI_BCIKIT_CONTROL
        ):
            raise RuntimeError("设备固件不支持 Wi-Fi 原生控制，请升级到 v0.5.0 或更高")
        if isinstance(self.transport, BleTransport):
            required_ble = CAP_BLE_BCIKIT_STREAM | CAP_BLE_BCIKIT_CONTROL
            if info.capabilities & required_ble != required_ble:
                raise RuntimeError("设备固件不支持 BCIKit BLE 双向采集，请升级到 v0.7.0 或更高")
            if self.config.board_count > 2 or (
                self.config.board_count == 2 and self.config.sample_rate_hz > 250
            ):
                raise RuntimeError(
                    "当前 BLE 仅支持单板 250/500 SPS 或双板 250 SPS；请改用 Wi-Fi TCP。"
                )
        if (
            isinstance(self.transport, WiFiTransport)
            and self.transport.output_mode == "auto"
            and not info.capabilities & CAP_AUTO_PROTOCOL_SESSION
        ):
            raise RuntimeError(
                "设备接受了 output=auto，但未声明自动会话能力位；请确认已烧录 BCIKit v0.6.0 或更高"
            )
        if self.config.board_count > 1 and not info.capabilities & CAP_MULTI_BOARD:
            raise RuntimeError("设备固件不支持多板采集；请改为单板配置或升级支持多板的固件")
        self.device_info = info
        self._diagnostic(
            "info",
            "设备信息：固件 "
            f"{info.firmware_version[0]}.{info.firmware_version[1]}.{info.firmware_version[2]}，"
            f"最大 {info.max_board_count} 块板，能力位 0x{info.capabilities:X}",
        )
        await self.send_command(CMD_STOP)
        await self.send_command(CMD_SET_OUTPUT_FORMAT, bytes((self.config.output_format,)))
        # A daisy chain must be configured before its shared CONFIG1 sample-rate
        # register is written.  Firmware v0.6 verifies this order while stopped.
        await self.send_command(CMD_SET_BOARD_COUNT, bytes((self.config.board_count,)))
        await self.send_command(
            CMD_SET_SAMPLE_RATE, self.config.sample_rate_hz.to_bytes(2, "little")
        )
        await self.send_command(CMD_SET_PROFILE, bytes((self.config.profile,)))
        await self.send_command(CMD_SET_CHANNEL_MASK, bytes((self.config.channel_mask,)))
        status = parse_device_status(await self.send_command(CMD_GET_STATUS))
        expected = (
            self.config.output_format,
            self.config.profile,
            self.config.board_count,
            self.config.sample_rate_hz,
            self.config.channel_mask,
        )
        actual = (
            status.output_format,
            status.profile,
            status.board_count,
            status.sample_rate_hz,
            status.channel_enabled_mask,
        )
        if actual != expected:
            raise RuntimeError(f"设备配置读回不一致：期望 {expected}，实际 {actual}")
        if not status.register_verified:
            verification = status.register_verified
            raise RuntimeError(
                f"ADS1299 状态异常：ID=0x{status.ads1299_id:02X}，寄存器校验={verification}"
            )
        if self.config.board_count == 1 and status.ads1299_id != 0x3E:
            raise RuntimeError(
                f"ADS1299 状态异常：单板模式要求 ID=0x3E，实际为 0x{status.ads1299_id:02X}"
            )
        if self.config.board_count > 1 and status.ads1299_id != 0x3E:
            self._diagnostic(
                "info",
                "多板菊花链不支持逐板寄存器 ID 读回；将通过每块板的采样状态头验证链路",
            )
        self.device_status = status
        self._diagnostic(
            "info",
            "设备握手和配置读回完成："
            f"{status.board_count} 块板、{status.board_count * 8} 通道、"
            f"{status.sample_rate_hz} SPS",
        )

    async def _receive_loop(self) -> None:
        try:
            while True:
                chunk = await self.transport.read()
                frames = self._decoder.feed(chunk)
                self._sync_decoder_stats()
                arrival = time.monotonic()
                for frame in frames:
                    if isinstance(frame, ControlFrame):
                        future = self._pending.get(frame.request_id)
                        if future is not None and not future.done():
                            future.set_result(frame)
                    else:
                        self._handle_sample(frame, arrival)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if not self._closing:
                await self._fail(error)

    def _handle_sample(self, frame: SampleFrame, arrival: float) -> None:
        if (
            frame.board_count != self.config.board_count
            or frame.sample_rate_hz != self.config.sample_rate_hz
        ):
            raise RuntimeError(
                "SAMPLE 配置与读回不一致："
                f"期望 {self.config.board_count} 块板/{self.config.sample_rate_hz} SPS，"
                f"收到 {frame.board_count} 块板/{frame.sample_rate_hz} SPS"
            )
        if not self._first_sample_reported:
            self._first_sample_reported = True
            self._diagnostic(
                "info",
                "收到第一帧 SAMPLE："
                f"sequence={frame.sequence}，{frame.board_count} 块板、"
                f"{frame.channel_count} 通道、时间戳={frame.timestamp_us} µs",
            )
        self._last_sample_monotonic = arrival
        self._last_device_timestamp_us = frame.timestamp_us
        self.stats.observe(frame, arrival)
        if self.recording:
            self.recorder.append_frame(frame)
        self._frames_for_batch.append(frame)
        target = max(1, math.ceil(frame.sample_rate_hz / 30))
        if len(self._frames_for_batch) >= target:
            self._flush_batch()

    def _flush_batch(self) -> None:
        if not self._frames_for_batch:
            return
        frames = self._frames_for_batch
        self._frames_for_batch = []
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
        if self.sample_queue.full():
            with suppress(asyncio.QueueEmpty):
                dropped = self.sample_queue.get_nowait()
                self.stats.local_queue_drops += dropped.sample_count
            self._diagnostic("warning", "界面处理队列已满，已丢弃最旧显示批次；原始记录未受影响")
        self.sample_queue.put_nowait(batch)

    def _sync_decoder_stats(self) -> None:
        now = (self._decoder.crc_errors, self._decoder.format_errors, self._decoder.discarded_bytes)
        previous = self._last_decoder_counts
        self.stats.crc_errors += now[0] - previous[0]
        self.stats.format_errors += now[1] - previous[1]
        self.stats.discarded_bytes += now[2] - previous[2]
        self._last_decoder_counts = now

    async def _watchdog_loop(self) -> None:
        while self.state == SessionState.STREAMING:
            await asyncio.sleep(max(0.25, 3.0 / self.config.sample_rate_hz))
            timeout = getattr(self.transport, "sample_timeout_seconds", 1.0)
            if time.monotonic() - self._last_sample_monotonic > timeout:
                await self._fail(
                    RuntimeError(f"超过 {timeout:g} 秒未收到采样帧，请检查设备和 ADS1299 状态")
                )
                return

    async def _status_poll_loop(self) -> None:
        previous_counters = self.device_status.counters if self.device_status is not None else None
        while self.state == SessionState.STREAMING:
            await asyncio.sleep(5.0)
            if self.state != SessionState.STREAMING:
                return
            try:
                status = parse_device_status(await self.send_command(CMD_GET_STATUS, timeout=2.0))
            except Exception as error:
                await self._fail(error)
                return
            if previous_counters is not None and status.counters != previous_counters:
                increases = [
                    current - previous
                    for current, previous in zip(status.counters, previous_counters, strict=True)
                ]
                if any(value > 0 for value in increases[2:]):
                    self._diagnostic("warning", f"设备错误计数发生变化：{increases}")
            self.device_status = status
            previous_counters = status.counters

    async def _fail(self, error: Exception) -> None:
        self.last_error = str(error)
        logger.error("DeviceSession 失败：%s", self.last_error)
        self._diagnostic("error", self.last_error)
        self._set_state(SessionState.ERROR)
        if self.recording and not isinstance(self.transport, WiFiTransport):
            self.stop_recording("connection_error")
        if isinstance(self.transport, WiFiTransport):
            await self.transport.emergency_stop()

    def _set_state(self, state: SessionState) -> None:
        self.state = state
        self.state_queue.put_nowait(state)

    def _diagnostic(self, level: str, message: str) -> None:
        event = DiagnosticEvent(level, message)
        if self.diagnostic_queue.full():
            with suppress(asyncio.QueueEmpty):
                self.diagnostic_queue.get_nowait()
        self.diagnostic_queue.put_nowait(event)
        if self.recording:
            self.recorder.log(f"[{level}] {message}")
