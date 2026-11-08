"""UI orchestration; all device I/O remains in the core session layer."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Slot
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMessageBox

from bcikit.analysis import analyze_eeg
from bcikit.filtering import EEGDisplayFilter
from bcikit.models import SessionConfig, SessionState
from bcikit.protocol import PROFILE_EEG_SRB2, PROFILE_INPUT_SHORT, PROFILE_INTERNAL_TEST
from bcikit.recording import (
    BDFExportError,
    export_bdf,
    export_csv,
    list_sessions,
    load_session,
    recover_incomplete_sessions,
)
from bcikit.session import DeviceSession
from bcikit.transport import (
    BleDevice,
    BleTransport,
    TransportError,
    VirtualTransport,
    WiFiTransport,
)

from .window import MainWindow

logger = logging.getLogger(__name__)
DISCONNECT_TIMEOUT_SECONDS = 4.0


class StudioController(QObject):
    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.window = window
        self.window.set_controller(self)
        self.session: DeviceSession | None = None
        self.filter: EEGDisplayFilter | None = None
        self.shutdown_complete = False
        self._shutdown_started = False
        self._reconnecting = False
        self._disconnecting = False
        self._disconnect_finished = asyncio.Event()
        self._disconnect_finished.set()
        self._tasks: set[asyncio.Task] = set()
        self._connect_signals()
        logger.info("StudioController 已初始化")
        recovered = recover_incomplete_sessions()
        for path in recovered:
            self.window.append_diagnostic("warning", f"已恢复中断记录：{path.name}")
        self.refresh_sessions()
        self._plot_timer = QTimer(self)
        self._plot_timer.setInterval(33)
        self._plot_timer.timeout.connect(self.window.live.waveform.refresh)
        self._plot_timer.start()
        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(250)
        self._stats_timer.timeout.connect(self._update_stats)
        self._stats_timer.start()
        self._analysis_timer = QTimer(self)
        self._analysis_timer.setInterval(1000)
        self._analysis_timer.timeout.connect(self._update_analysis)
        self._analysis_timer.start()

    def _connect_signals(self) -> None:
        home = self.window.home
        home.connect_real.connect(
            lambda board_count, sample_rate, gain_code: self._spawn(
                self.connect_real(board_count, sample_rate, gain_code)
            )
        )
        home.connect_bluetooth.connect(
            lambda board_count, sample_rate, gain_code: self._spawn(
                self.connect_bluetooth(board_count, sample_rate, gain_code)
            )
        )
        home.open_history.connect(self.window.show_history)
        home.open_diagnostics.connect(self.window.show_hardware_diagnostics)
        home.open_session.connect(self.open_session)
        diagnostics = self.window.hardware_diagnostics
        diagnostics.back_requested.connect(
            lambda: self._spawn(self.return_home_from_diagnostics())
        )
        diagnostics.connect_real_requested.connect(
            lambda: self._spawn(self.connect_diagnostic_real(*home.connection_options()))
        )
        diagnostics.connect_bluetooth_requested.connect(
            lambda: self._spawn(self.connect_diagnostic_bluetooth(*home.connection_options()))
        )
        diagnostics.operation_requested.connect(
            lambda operation: self._spawn(self.run_hardware_diagnostic(operation))
        )
        diagnostics.open_live_requested.connect(self.window.show_live)
        diagnostics.disconnect_requested.connect(lambda: self._spawn(self.disconnect_session()))
        live = self.window.live
        live.disconnect_requested.connect(lambda: self._spawn(self.disconnect_session()))
        live.recording_requested.connect(self._recording_toggled)
        live.marker_requested.connect(self.add_marker)
        history = self.window.history
        history.back_requested.connect(self.window.show_home)
        history.refresh_button.clicked.connect(self.refresh_sessions)
        history.open_session.connect(self.open_session)
        replay = self.window.replay
        replay.back_requested.connect(self.window.show_history)
        replay.export_requested.connect(self.export_session)

    def _spawn(self, coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def connect_development_virtual(
        self, board_count: int = 1, sample_rate_hz: int = 250
    ) -> None:
        """Internal visual-test hook; deliberately not exposed by the shipped UI."""
        await self._connect(
            VirtualTransport("normal"), SessionConfig(board_count, sample_rate_hz)
        )

    async def connect_bluetooth(
        self, board_count: int = 1, sample_rate_hz: int = 250,
        gain_code: int = 6, destination: str = "live"
    ) -> None:
        if board_count > 2 or (board_count == 2 and sample_rate_hz > 250):
            self._show_error(
                "蓝牙配置不支持",
                "当前 BLE 仅支持单板 250/500 SPS 或双板 250 SPS；请使用 Wi‑Fi TCP。",
            )
            return
        self._set_connection_progress(destination, True, "正在扫描附近的 BCIKit 蓝牙设备…")
        try:
            devices = await BleTransport.discover()
        except TransportError as error:
            self._show_error("蓝牙扫描失败", str(error))
            return
        finally:
            self._set_connection_progress(destination, False)
        if not devices:
            self._show_error(
                "未发现 BCIKit 蓝牙设备",
                "请确认设备已上电、固件为 v0.7.0 或更高，并在 macOS 设置中允许 "
                "BCIKit Studio 使用蓝牙。",
            )
            return
        device = self._choose_bluetooth_device(devices)
        if device is None:
            return
        await self._connect(
            BleTransport(device),
            SessionConfig(board_count, sample_rate_hz, gain_code=gain_code),
            destination=destination,
        )

    def _choose_bluetooth_device(self, devices: list[BleDevice]) -> BleDevice | None:
        labels = [device.label for device in devices]
        selected, accepted = QInputDialog.getItem(
            self.window,
            "选择 BCIKit 蓝牙设备",
            "附近设备：",
            labels,
            0,
            False,
        )
        if not accepted:
            return None
        return devices[labels.index(selected)]

    async def connect_real(
        self, board_count: int = 1, sample_rate_hz: int = 250,
        gain_code: int = 6, destination: str = "live"
    ) -> None:
        answer = QMessageBox.information(
            self.window,
            "连接 BCIKit 设备",
            "请先在 macOS Wi-Fi 菜单中连接 OpenBCI-XXXX 热点。\n\n"
            "连接人体前必须使用电池或合规隔离供电，并断开 USB、示波器地等非隔离路径。",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Ok:
            return
        await self._connect(
            WiFiTransport(), SessionConfig(board_count, sample_rate_hz, gain_code=gain_code),
            destination=destination
        )

    async def connect_diagnostic_real(
        self, board_count: int = 1, sample_rate_hz: int = 250, gain_code: int = 6
    ) -> None:
        await self.connect_real(board_count, sample_rate_hz, gain_code, destination="diagnostics")

    async def connect_diagnostic_bluetooth(
        self, board_count: int = 1, sample_rate_hz: int = 250, gain_code: int = 6
    ) -> None:
        await self.connect_bluetooth(
            board_count, sample_rate_hz, gain_code, destination="diagnostics"
        )

    async def return_home_from_diagnostics(self) -> None:
        """The connection page must never be shown with a hidden live session."""
        if self.session is None:
            self.window.show_home()
            return
        await self.disconnect_session("离开诊断页面，正在断开设备")

    def _set_connection_progress(
        self, destination: str, connecting: bool, detail: str = ""
    ) -> None:
        if destination == "diagnostics":
            self.window.hardware_diagnostics.set_connecting(connecting, detail)
        else:
            self.window.home.set_connecting(connecting, detail)

    async def _connect(
        self, transport, config: SessionConfig | None = None, destination: str = "live"
    ) -> None:
        # A new /tcp request must never overlap the previous session's STOP and
        # DELETE /tcp cleanup. Otherwise that stale cleanup can tear down the
        # newly established reverse TCP stream after START succeeds.
        if self._disconnecting:
            logger.info("等待上一会话的连接资源释放")
            await self._disconnect_finished.wait()
        if self.session is not None:
            await self.disconnect_session()
        session = DeviceSession(transport, config or SessionConfig())
        logger.info(
            "开始连接：%s，%d 块板、%d SPS、PGA %d×",
            transport.description,
            session.config.board_count,
            session.config.sample_rate_hz,
            (1, 2, 4, 6, 8, 12, 24)[session.config.gain_code],
        )
        self.session = session
        self.window.live.clear_session_data()
        self.window.live.set_channel_count(session.config.board_count * 8)
        self.window.live.waveform.reset(
            session.config.sample_rate_hz, session.config.board_count * 8
        )
        self._set_connection_progress(destination, True, "正在连接并校验设备配置…")
        consumers = [
            asyncio.create_task(self._consume_states(session)),
            asyncio.create_task(self._consume_samples(session)),
            asyncio.create_task(self._consume_diagnostics(session)),
        ]
        self._tasks.update(consumers)
        for task in consumers:
            task.add_done_callback(self._tasks.discard)
        try:
            await session.connect()
            self.filter = EEGDisplayFilter(
                session.config.sample_rate_hz, session.config.board_count * 8
            )
            await session.start()
            if destination == "diagnostics":
                status = await session.read_status()
                self.window.hardware_diagnostics.set_connected(True)
                self.window.hardware_diagnostics.show_status(session.device_info, status)
                logger.info("诊断会话已开始")
                self.window.show_hardware_diagnostics()
            else:
                logger.info("采集已开始，进入实时工作区")
                self.window.show_live()
        except Exception as error:
            logger.error(
                "连接或启动采集失败：%s",
                error,
                exc_info=not isinstance(error, TransportError),
            )
            if self.session is session:
                self.session = None
            self.filter = None
            with suppress(Exception):
                await session.close()
            self.window.live.clear_session_data()
            self.window.hardware_diagnostics.set_connected(False)
            self._show_error("连接失败", str(error))
        finally:
            self._set_connection_progress(destination, False)

    async def disconnect_session(self, reason: str = "用户请求断开设备") -> None:
        if self._disconnecting:
            return
        self._disconnecting = True
        self._disconnect_finished.clear()
        session = self.session
        self.session = None
        self.filter = None
        # Return to the connection flow immediately. Closing a TCP connection can
        # take a moment, and leaving the stale Live page visible during that time
        # makes it look as though the disconnect action did nothing.
        self.window.live.clear_session_data()
        self.window.hardware_diagnostics.set_connected(False)
        self.window.show_home()
        self.window.home.set_connecting(True, "正在断开并释放设备连接…")
        try:
            logger.info(reason)
            if session is not None:
                try:
                    await asyncio.wait_for(session.close(), timeout=DISCONNECT_TIMEOUT_SECONDS)
                except TimeoutError:
                    warning = (
                        f"{reason}超过 {DISCONNECT_TIMEOUT_SECONDS:g} 秒未完成；"
                        "已强制释放本机连接。设备端可能仍需断电或等待固件超时。"
                    )
                    logger.warning(warning)
                    self.window.append_diagnostic("warning", warning)
                    await session.force_close()
        except Exception as error:
            self.window.append_diagnostic("warning", f"断开设备时清理异常：{error}")
        finally:
            self.refresh_sessions()
            self._disconnecting = False
            self._disconnect_finished.set()
            self.window.home.set_connecting(False)

    async def run_hardware_diagnostic(self, operation: str) -> None:
        """Run one explicit, capability-gated ADS1299 diagnostic operation."""
        session = self.session
        panel = self.window.hardware_diagnostics
        if session is None:
            self._show_error("诊断失败", "请先连接设备。")
            return
        names = {
            "read_status": "读取设备配置与计数",
            "reset_stats": "清零设备计数",
            "internal_test": "启用 ADS1299 内部测试信号",
            "input_short": "启用 ADS1299 输入短接",
            "restore_eeg": "恢复 EEG SRB2",
        }
        if operation not in names:
            self._show_error("诊断失败", f"未知诊断操作：{operation}")
            return
        panel.set_busy(True, f"正在{names[operation]}…")
        try:
            if operation == "read_status":
                status = await session.read_status()
            elif operation == "reset_stats":
                status = await session.reset_device_stats()
            elif operation == "internal_test":
                status = await session.set_profile(PROFILE_INTERNAL_TEST)
            elif operation == "input_short":
                status = await session.set_profile(PROFILE_INPUT_SHORT)
            else:
                status = await session.set_profile(PROFILE_EEG_SRB2)
            panel.show_status(session.device_info, status, f"已完成：{names[operation]}")
        except Exception as error:
            logger.exception("诊断操作失败：%s", operation)
            self._show_error("诊断失败", str(error))
            panel.set_busy(False, f"诊断失败：{error}")
        else:
            panel.set_busy(False)

    async def _consume_states(self, session: DeviceSession) -> None:
        while self.session is session:
            state = await session.state_queue.get()
            logger.info("设备状态：%s", state.value)
            detail = session.transport.description
            self.window.live.set_state(state, detail)
            if state == SessionState.ERROR and session.last_error:
                if isinstance(session.transport, WiFiTransport) and session.stats.valid_frames > 0:
                    if not self._reconnecting:
                        self._spawn(self._attempt_reconnect(session))
                else:
                    self.window.append_diagnostic(
                        "error", f"采集异常，正在强制断开：{session.last_error}"
                    )
                    self._spawn(self.disconnect_session("设备异常断开"))

    async def _attempt_reconnect(self, session: DeviceSession) -> None:
        self._reconnecting = True
        try:
            recovered = await session.reconnect()
            if not recovered and self.session is session:
                self.window.append_diagnostic(
                    "error", f"自动重连失败，正在强制断开：{session.last_error or '未知错误'}"
                )
                await self.disconnect_session("自动重连失败")
        finally:
            self._reconnecting = False

    async def _consume_samples(self, session: DeviceSession) -> None:
        while self.session is session:
            batch = await session.sample_queue.get()
            logger.debug(
                "界面收到采样批次：%d 样本、%d 通道",
                batch.sample_count,
                batch.channel_count,
            )
            filtered = self.filter.process(batch.microvolts) if self.filter else batch.microvolts
            self.window.live.waveform.append(batch, filtered)

    async def _consume_diagnostics(self, session: DeviceSession) -> None:
        while self.session is session:
            event = await session.diagnostic_queue.get()
            self.window.append_diagnostic(event.level, event.message)

    @Slot(bool)
    def _recording_toggled(self, enabled: bool) -> None:
        if self.session is None:
            self.window.live.set_recording(False)
            return
        try:
            if enabled:
                name, accepted = QInputDialog.getText(
                    self.window, "开始记录", "记录名称：", text="EEG记录"
                )
                if not accepted:
                    self.window.live.set_recording(False)
                    return
                self.session.start_recording(name)
            else:
                self.session.stop_recording("user_stop")
                self.refresh_sessions()
            self.window.live.set_recording(self.session.recording)
        except Exception as error:
            self.window.live.set_recording(self.session.recording)
            self._show_error("记录操作失败", str(error))

    @Slot()
    def add_marker(self) -> None:
        if self.session is None:
            return
        label, accepted = QInputDialog.getText(self.window, "添加 Marker", "标签：", text="事件")
        if accepted:
            try:
                self.session.add_marker(label)
            except Exception as error:
                self._show_error("无法添加 Marker", str(error))

    @Slot(Path)
    def open_session(self, path: Path) -> None:
        try:
            loaded = load_session(path)
            self.window.replay.load(path, loaded)
            self.window.show_replay()
        except Exception as error:
            self._show_error("无法打开记录", str(error))

    @Slot(Path, str)
    def export_session(self, path: Path, unit: str) -> None:
        if unit == "bdf":
            default = str(Path.home() / "Desktop" / f"{path.name}.bdf")
            file_filter = "BDF+ 文件 (*.bdf)"
        else:
            suffix = "microvolts" if unit == "uv" else "raw_counts"
            default = str(Path.home() / "Desktop" / f"{path.name}_{suffix}.csv")
            file_filter = "CSV 文件 (*.csv)"
        destination, _ = QFileDialog.getSaveFileName(
            self.window, "导出 BDF+" if unit == "bdf" else "导出 CSV", default, file_filter
        )
        if not destination:
            return
        try:
            if unit == "bdf":
                result = export_bdf(path, Path(destination))
                detail = (
                    f"已导出 {result.sample_count:,} 个样本、{result.channel_count} 个通道"
                    f"和 {result.marker_count} 个 Marker。"
                )
                if result.padded_samples:
                    detail += (
                        f"\n末个 BDF 记录补齐了 {result.padded_samples} 个零值样本，并已写入注释。"
                    )
                QMessageBox.information(self.window, "BDF+ 导出完成", detail)
            else:
                rows = export_csv(path, Path(destination), unit)
                QMessageBox.information(self.window, "导出完成", f"已导出 {rows:,} 个样本。")
        except BDFExportError as error:
            QMessageBox.warning(self.window, "无法导出 BDF+", str(error))
        except Exception as error:
            self._show_error("导出失败", str(error))

    @Slot()
    def refresh_sessions(self) -> None:
        sessions = list_sessions()
        self.window.home.set_recent(sessions)
        self.window.history.set_sessions(sessions)

    @Slot()
    def _update_stats(self) -> None:
        if self.session is not None:
            self.window.live.set_stats(self.session.stats)

    @Slot()
    def _update_analysis(self) -> None:
        session = self.session
        panel = self.window.live.analysis_panel
        if (
            session is None
            or session.state != SessionState.STREAMING
            or not self.window.live.analysis_toggle.isChecked()
        ):
            return
        sample_rate = session.config.sample_rate_hz
        samples = self.window.live.waveform.buffer.snapshot(seconds=4.0, filtered=False)
        available_seconds = samples.shape[0] / sample_rate
        if samples.shape[0] < sample_rate * 2:
            panel.set_waiting(available_seconds)
            return
        panel.set_result(analyze_eeg(samples, sample_rate))

    def request_shutdown(self) -> None:
        if self._shutdown_started:
            return
        self._shutdown_started = True
        self._spawn(self.shutdown())

    async def shutdown(self) -> None:
        if self.shutdown_complete:
            return
        self._shutdown_started = True
        self._plot_timer.stop()
        self._stats_timer.stop()
        self._analysis_timer.stop()
        session = self.session
        self.session = None
        try:
            if session is not None:
                try:
                    await asyncio.wait_for(session.close(), timeout=5.0)
                except TimeoutError:
                    self.window.append_diagnostic(
                        "warning", "设备断开超过 5 秒，应用将结束；当前记录按异常退出保存。"
                    )
                    if session.recording:
                        session.stop_recording("application_exit_timeout")
                except Exception as error:
                    self.window.append_diagnostic("warning", f"退出清理异常：{error}")
                    if session.recording:
                        session.stop_recording("application_exit_error")
        finally:
            self.filter = None
            current = asyncio.current_task()
            tasks = [task for task in self._tasks if task is not current]
            for task in tasks:
                task.cancel()
            for task in tasks:
                with suppress(asyncio.CancelledError, Exception):
                    await task
            self.window.stop_log_capture()
            self.shutdown_complete = True
            self.window.close()
            app = QApplication.instance()
            if app is not None:
                app.quit()

    def _show_error(self, title: str, detail: str) -> None:
        self.window.append_diagnostic("error", detail)
        QMessageBox.critical(self.window, title, detail)
