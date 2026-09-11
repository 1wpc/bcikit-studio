"""BCIKit Studio pages and reusable product UI widgets."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QMenu,
    QPushButton,
    QSlider,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from bcikit.models import IntegrityStats, SampleBatch, SessionState
from bcikit.recording import LoadedSession, SessionSummary

from .analysis_view import AnalysisPanel
from .waveform import WaveformView


def muted(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Muted")
    label.setWordWrap(True)
    return label


class ActionCard(QFrame):
    clicked = Signal()

    def __init__(self, icon: str, title: str, description: str, button: str) -> None:
        super().__init__()
        self.setObjectName("Card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(10)
        symbol = QLabel(icon)
        symbol.setStyleSheet("font-size: 30px;")
        heading = QLabel(title)
        heading.setStyleSheet("font-size: 17px; font-weight: 700;")
        detail = muted(description)
        detail.setMinimumHeight(48)
        self.action = QPushButton(button)
        self.action.setObjectName("")
        self.action.clicked.connect(self.clicked)
        layout.addWidget(symbol)
        layout.addWidget(heading)
        layout.addWidget(detail)
        layout.addStretch()
        layout.addWidget(self.action)


class HomePage(QWidget):
    connect_real = Signal(int, int)
    connect_bluetooth = Signal(int, int)
    open_history = Signal()
    open_diagnostics = Signal()
    open_session = Signal(Path)

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(18)
        title = QLabel("连接设备，开始采集")
        title.setObjectName("Title")
        intro = muted(
            "先选择与实际装配一致的采集配置，再连接设备。连接完成后将进入独立的实时采集工作区。"
        )
        config = QFrame()
        config.setObjectName("Card")
        config_layout = QHBoxLayout(config)
        config_layout.setContentsMargins(18, 14, 18, 14)
        config_title = QLabel("采集配置")
        config_title.setStyleSheet("font-weight: 700;")
        self.board_combo = QComboBox()
        for board_count in range(1, 5):
            channels = board_count * 8
            self.board_combo.addItem(f"{board_count} 块板 · {channels} 通道", board_count)
        self.sample_rate_combo = QComboBox()
        self.sample_rate_combo.addItem("250 SPS", 250)
        self.sample_rate_combo.addItem("500 SPS", 500)
        config_note = muted(
            "请按实际串联板卡数选择。当前固件不会自动探测板卡数量，连接后会校验设备读回值。"
        )
        config_layout.addWidget(config_title)
        config_layout.addSpacing(12)
        config_layout.addWidget(QLabel("板卡"))
        config_layout.addWidget(self.board_combo)
        config_layout.addWidget(QLabel("采样率"))
        config_layout.addWidget(self.sample_rate_combo)
        config_layout.addSpacing(12)
        config_layout.addWidget(config_note, 1)
        cards = QHBoxLayout()
        self.real_card = ActionCard(
            "⌁",
            "Wi‑Fi 连接",
            "先在 macOS Wi-Fi 中连接 OpenBCI-XXXX，再由软件完成配置。",
            "开始连接",
        )
        self.bluetooth_card = ActionCard(
            "◌",
            "蓝牙连接",
            "扫描附近的 BCIKit-XXXX；适用于单板或双板 250 SPS 的近距无线采集。",
            "扫描蓝牙设备",
        )
        history = ActionCard(
            "▤", "打开历史记录", "回放已保存会话，查看 Marker 和数据完整性并导出 CSV。", "查看记录"
        )
        self.real_card.clicked.connect(
            lambda: self.connect_real.emit(*self.connection_options())
        )
        self.bluetooth_card.clicked.connect(
            lambda: self.connect_bluetooth.emit(*self.connection_options())
        )
        history.clicked.connect(self.open_history)
        cards.addWidget(self.real_card)
        cards.addWidget(self.bluetooth_card)
        cards.addWidget(history)
        diagnostics = ActionCard(
            "⌁",
            "芯片诊断",
            "读取 ADS1299 状态、运行内部测试信号或输入短接检查，并查看设备错误计数。",
            "进入诊断",
        )
        diagnostics.clicked.connect(self.open_diagnostics)
        diagnostic_row = QHBoxLayout()
        diagnostic_row.addWidget(diagnostics)
        diagnostic_row.addStretch(2)
        recent_title = QLabel("最近记录")
        recent_title.setStyleSheet("font-size: 17px; font-weight: 700;")
        self.recent = QListWidget()
        self.recent.setMaximumHeight(150)
        self.recent.itemDoubleClicked.connect(
            lambda item: self.open_session.emit(Path(item.data(Qt.ItemDataRole.UserRole)))
        )
        self.connection_status = muted("")
        self.connection_status.setVisible(False)
        layout.addWidget(title)
        layout.addWidget(intro)
        layout.addWidget(config)
        layout.addWidget(self.connection_status)
        layout.addLayout(cards, 1)
        layout.addLayout(diagnostic_row)
        layout.addWidget(recent_title)
        layout.addWidget(self.recent)

    def set_recent(self, sessions: list[SessionSummary]) -> None:
        self.recent.clear()
        for summary in sessions[:5]:
            item_text = (
                f"{summary.name}    {summary.duration_seconds:.1f} 秒    "
                f"{summary.channel_count} 通道"
            )
            self.recent.addItem(item_text)
            self.recent.item(self.recent.count() - 1).setData(
                Qt.ItemDataRole.UserRole, str(summary.path)
            )
        if not sessions:
            self.recent.addItem("还没有记录。连接设备后即可开始第一次采集。")
            self.recent.item(0).setFlags(Qt.ItemFlag.NoItemFlags)

    def connection_options(self) -> tuple[int, int]:
        return (
            int(self.board_combo.currentData()),
            int(self.sample_rate_combo.currentData()),
        )

    def set_connecting(self, connecting: bool, detail: str = "") -> None:
        """Keep connection progress on the connection page until configuration succeeds."""
        self.board_combo.setEnabled(not connecting)
        self.sample_rate_combo.setEnabled(not connecting)
        self.real_card.action.setEnabled(not connecting)
        self.bluetooth_card.action.setEnabled(not connecting)
        self.connection_status.setVisible(connecting)
        self.connection_status.setText(detail or "正在连接并校验设备配置…")


class DiagnosticsPage(QWidget):
    """Hardware diagnostics backed only by negotiated BCIKit control commands."""

    back_requested = Signal()
    connect_real_requested = Signal()
    connect_bluetooth_requested = Signal()
    operation_requested = Signal(str)
    open_live_requested = Signal()
    disconnect_requested = Signal()

    _profile_names = {
        0: "EEG SRB2",
        1: "差分输入",
        2: "内部测试信号",
        3: "输入短接",
    }

    def __init__(self) -> None:
        super().__init__()
        self._connected = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(16)

        header = QHBoxLayout()
        back = QPushButton("‹ 返回连接页")
        back.clicked.connect(self.back_requested)
        title_box = QVBoxLayout()
        title = QLabel("芯片诊断")
        title.setObjectName("PageTitle")
        self.state_label = muted("未连接设备。诊断操作会停止当前数据流、切换芯片配置并读回校验。")
        title_box.addWidget(title)
        title_box.addWidget(self.state_label)
        self.live_button = QPushButton("进入实时采集")
        self.live_button.clicked.connect(self.open_live_requested)
        self.disconnect_button = QPushButton("断开")
        self.disconnect_button.setObjectName("Danger")
        self.disconnect_button.clicked.connect(self.disconnect_requested)
        header.addWidget(back)
        header.addLayout(title_box, 1)
        header.addWidget(self.live_button)
        header.addWidget(self.disconnect_button)

        connection = QFrame()
        connection.setObjectName("Card")
        connection_layout = QHBoxLayout(connection)
        connection_layout.setContentsMargins(18, 14, 18, 14)
        connection_layout.addWidget(QLabel("设备连接"))
        self.real_connect_button = QPushButton("连接真实设备")
        self.real_connect_button.setObjectName("Primary")
        self.real_connect_button.clicked.connect(self.connect_real_requested)
        self.bluetooth_connect_button = QPushButton("蓝牙连接")
        self.bluetooth_connect_button.clicked.connect(self.connect_bluetooth_requested)
        connection_layout.addWidget(self.real_connect_button)
        connection_layout.addWidget(self.bluetooth_connect_button)
        connection_layout.addStretch()

        actions = QFrame()
        actions.setObjectName("Card")
        action_layout = QVBoxLayout(actions)
        action_layout.setContentsMargins(18, 16, 18, 16)
        action_layout.addWidget(QLabel("检查与控制"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        self.action_buttons: dict[str, QPushButton] = {}
        for index, (operation, label) in enumerate(
            (
                ("read_status", "读取配置与计数"),
                ("reset_stats", "清零设备计数"),
                ("internal_test", "启用内部测试信号"),
                ("input_short", "启用输入短接"),
                ("restore_eeg", "恢复 EEG SRB2"),
            )
        ):
            button = QPushButton(label)
            button.clicked.connect(
                lambda _checked=False, name=operation: self.operation_requested.emit(name)
            )
            grid.addWidget(button, index // 3, index % 3)
            self.action_buttons[operation] = button
        action_layout.addLayout(grid)
        note = muted(
            "切换模式时会自动 STOP → SET_PROFILE → GET_STATUS → START；记录进行中不可切换。"
        )
        action_layout.addWidget(note)

        self.result = QTextEdit()
        self.result.setReadOnly(True)
        self.result.setMinimumHeight(250)
        self.result.setPlaceholderText("连接后将在这里显示固件、ADS1299 校验与设备计数。")

        layout.addLayout(header)
        layout.addWidget(connection)
        layout.addWidget(actions)
        layout.addWidget(self.result, 1)
        self.set_connected(False)

    def set_connected(self, connected: bool, detail: str = "") -> None:
        self._connected = connected
        self.real_connect_button.setVisible(not connected)
        self.bluetooth_connect_button.setVisible(not connected)
        self.live_button.setVisible(connected)
        self.disconnect_button.setVisible(connected)
        for button in self.action_buttons.values():
            button.setEnabled(connected)
        if detail:
            self.state_label.setText(detail)
        elif not connected:
            self.state_label.setText("未连接设备。诊断操作会停止当前数据流、切换芯片配置并读回校验。")

    def set_busy(self, busy: bool, detail: str = "") -> None:
        for button in self.action_buttons.values():
            button.setEnabled(self._connected and not busy)
        self.live_button.setEnabled(self._connected and not busy)
        self.disconnect_button.setEnabled(self._connected and not busy)
        if detail:
            self.state_label.setText(detail)

    def set_connecting(self, connecting: bool, detail: str = "") -> None:
        """Disable both connection choices while discovery or setup is in progress."""
        self.real_connect_button.setEnabled(not connecting)
        self.bluetooth_connect_button.setEnabled(not connecting)
        if detail:
            self.state_label.setText(detail)

    def show_status(self, info, status, message: str = "") -> None:
        profile = self._profile_names.get(status.profile, f"未知 ({status.profile})")
        counters = (
            "DRDY=" + str(status.counters[0]),
            "帧=" + str(status.counters[1]),
            "帧同步错误=" + str(status.counters[2]),
            "SPI 错误=" + str(status.counters[3]),
            "DRDY 超时=" + str(status.counters[4]),
            "队列溢出=" + str(status.counters[5]),
            "传输丢弃=" + str(status.counters[6]),
            "寄存器校验错误=" + str(status.counters[7]),
        )
        firmware = ".".join(map(str, info.firmware_version)) if info is not None else "—"
        self.state_label.setText(message or "诊断已完成，设备仍在采集。")
        self.result.setPlainText(
            "设备信息\n"
            f"固件：{firmware}\n"
            f"设备 ID：{info.device_id:016X}\n\n"
            "ADS1299 配置读回\n"
            f"模式：{profile}\n板卡：{status.board_count}\n采样率：{status.sample_rate_hz} SPS\n"
            f"通道掩码：0x{status.channel_enabled_mask:02X}\n"
            f"寄存器校验：{'通过' if status.register_verified else '失败'}\n"
            f"ADS1299 ID：0x{status.ads1299_id:02X}\n\n"
            "设备计数\n" + "\n".join(counters)
        )


class MetricCard(QFrame):
    def __init__(self, label: str, value: str = "—") -> None:
        super().__init__()
        self.setObjectName("MetricCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(13, 10, 13, 10)
        self.value = QLabel(value)
        self.value.setObjectName("MetricValue")
        caption = QLabel(label)
        caption.setObjectName("MetricLabel")
        layout.addWidget(self.value)
        layout.addWidget(caption)


class LivePage(QWidget):
    disconnect_requested = Signal()
    recording_requested = Signal(bool)
    marker_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._disconnect_pending = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        header = QHBoxLayout()
        heading_box = QVBoxLayout()
        title = QLabel("BCIKit Studio · 实时采集")
        title.setObjectName("PageTitle")
        self.status = QLabel("● 未连接")
        self.status.setObjectName("Muted")
        heading_box.addWidget(title)
        heading_box.addWidget(self.status)
        self.pause_button = QPushButton("暂停画面")
        self.pause_button.setCheckable(True)
        self.pause_button.toggled.connect(self._pause_toggled)
        self.record_button = QPushButton("开始记录")
        self.record_button.setObjectName("Record")
        self.record_button.setCheckable(True)
        self.record_button.toggled.connect(self.recording_requested)
        self.marker_button = QPushButton("添加 Marker")
        self.marker_button.setEnabled(False)
        self.marker_button.clicked.connect(self.marker_requested)
        self.disconnect_button = QPushButton("断开")
        self.disconnect_button.setObjectName("Danger")
        self.disconnect_button.clicked.connect(self._disconnect_clicked)
        header.addLayout(heading_box)
        header.addStretch()
        header.addWidget(self.pause_button)
        header.addWidget(self.record_button)
        header.addWidget(self.marker_button)
        header.addWidget(self.disconnect_button)

        metrics = QHBoxLayout()
        self.metric_rate = MetricCard("实际采样率")
        self.metric_frames = MetricCard("有效帧")
        self.metric_gap = MetricCard("sequence 丢帧")
        self.metric_crc = MetricCard("CRC 错误")
        self.metric_queue = MetricCard("界面队列丢帧")
        for card in (
            self.metric_rate,
            self.metric_frames,
            self.metric_gap,
            self.metric_crc,
            self.metric_queue,
        ):
            metrics.addWidget(card)

        toolbar = QHBoxLayout()
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["原始波形", "EEG 清晰 · 1–45 Hz + 50 Hz Notch"])
        self.window_combo = QComboBox()
        self.window_combo.addItems(["2 秒", "5 秒", "10 秒", "30 秒"])
        self.window_combo.setCurrentText("10 秒")
        self.scale_combo = QComboBox()
        self.scale_combo.addItems(["统一 ±100 µV", "统一 ±50 µV", "自动独立缩放"])
        self.board_view_combo = QComboBox()
        self.channel_button = QPushButton("通道 · 8/8")
        self.channel_menu = QMenu(self.channel_button)
        self.channel_button.setMenu(self.channel_menu)
        self.waveform = WaveformView()
        self.analysis_panel = AnalysisPanel()
        self.analysis_toggle = QPushButton("隐藏分析")
        self.analysis_toggle.setCheckable(True)
        self.analysis_toggle.setChecked(True)
        self.analysis_toggle.toggled.connect(self._analysis_toggled)
        self.filter_combo.currentIndexChanged.connect(
            lambda index: setattr(self.waveform, "filtered", index == 1)
        )
        self.window_combo.currentTextChanged.connect(
            lambda text: self.waveform.set_window(int(text.split()[0]))
        )
        self.scale_combo.currentTextChanged.connect(self._scale_changed)
        toolbar.addWidget(QLabel("显示"))
        toolbar.addWidget(self.filter_combo)
        toolbar.addWidget(QLabel("窗口"))
        toolbar.addWidget(self.window_combo)
        toolbar.addWidget(QLabel("幅度"))
        toolbar.addWidget(self.scale_combo)
        toolbar.addWidget(QLabel("查看"))
        toolbar.addWidget(self.board_view_combo)
        toolbar.addWidget(self.channel_button)
        toolbar.addWidget(self.analysis_toggle)
        toolbar.addStretch()

        self.content_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.content_splitter.setChildrenCollapsible(False)
        self.content_splitter.addWidget(self.waveform)
        self.content_splitter.addWidget(self.analysis_panel)
        self.content_splitter.setStretchFactor(0, 1)
        self.content_splitter.setStretchFactor(1, 0)
        self.content_splitter.setSizes([900, 320])

        layout.addLayout(header)
        layout.addLayout(metrics)
        layout.addLayout(toolbar)
        layout.addWidget(self.content_splitter, 1)
        self.board_view_combo.currentIndexChanged.connect(self._board_view_changed)
        self.set_channel_count(8)
        self.clear_session_data()

    def set_state(self, state: SessionState, detail: str = "") -> None:
        names = {
            SessionState.DISCONNECTED: "未连接",
            SessionState.DISCOVERING: "正在发现设备",
            SessionState.LISTENING: "等待设备反向连接",
            SessionState.CONFIGURING: "正在配置并读回",
            SessionState.READY: "设备就绪",
            SessionState.STREAMING: "正在采集",
            SessionState.STOPPING: "正在停止",
            SessionState.ERROR: "连接异常",
        }
        self.status.setText(f"● {names[state]}" + (f" · {detail}" if detail else ""))
        connected = state not in {SessionState.DISCONNECTED, SessionState.STOPPING}
        self.disconnect_button.setVisible(connected and not self._disconnect_pending)
        streaming = state == SessionState.STREAMING
        self.pause_button.setEnabled(streaming)
        self.record_button.setEnabled(streaming)

    def set_stats(self, stats: IntegrityStats) -> None:
        self.metric_rate.value.setText(f"{stats.actual_sample_rate_hz:.1f} Hz")
        self.metric_frames.value.setText(f"{stats.valid_frames:,}")
        self.metric_gap.value.setText(f"{stats.sequence_gaps:,}")
        self.metric_crc.value.setText(f"{stats.crc_errors:,}")
        self.metric_queue.value.setText(f"{stats.local_queue_drops:,}")

    def set_recording(self, recording: bool) -> None:
        self.record_button.blockSignals(True)
        self.record_button.setChecked(recording)
        self.record_button.setProperty("recording", recording)
        self.record_button.setText("停止记录" if recording else "开始记录")
        self.record_button.style().unpolish(self.record_button)
        self.record_button.style().polish(self.record_button)
        self.marker_button.setEnabled(recording)
        self.record_button.blockSignals(False)

    def clear_session_data(self) -> None:
        """Restore the Live page to a clean state after a session ends."""
        self._disconnect_pending = False
        self.set_recording(False)
        self.pause_button.blockSignals(True)
        self.pause_button.setChecked(False)
        self.pause_button.blockSignals(False)
        self.pause_button.setText("暂停画面")
        self.waveform.paused = False
        self.filter_combo.setCurrentIndex(0)
        self.waveform.filtered = False
        self.waveform.clear()
        self.analysis_panel.clear()
        for card in (
            self.metric_rate,
            self.metric_frames,
            self.metric_gap,
            self.metric_crc,
            self.metric_queue,
        ):
            card.value.setText("—")
        self.set_state(SessionState.DISCONNECTED)

    def set_channel_count(self, channel_count: int) -> None:
        """Rebuild channel controls from the negotiated device configuration."""
        channel_count = max(1, min(channel_count, 32))
        self.channel_menu.clear()
        for channel in range(channel_count):
            action = self.channel_menu.addAction(f"CH{channel + 1}")
            action.setCheckable(True)
            # A newly negotiated device always enters Live with every channel
            # visible. Individual channel choices can then be made from the menu.
            action.setChecked(True)
            action.toggled.connect(
                lambda checked, index=channel: self._channel_toggled(index, checked)
            )
        self.board_view_combo.blockSignals(True)
        self.board_view_combo.clear()
        self.board_view_combo.addItem("全部通道", None)
        for board_index, first_channel in enumerate(range(0, channel_count, 8), start=1):
            last_channel = min(first_channel + 8, channel_count)
            self.board_view_combo.addItem(
                f"板 {board_index} · CH{first_channel + 1}–{last_channel}",
                (first_channel, last_channel),
            )
        # Keep "全部通道" selected for multi-board acquisitions as well.
        self.board_view_combo.setCurrentIndex(0)
        self.board_view_combo.blockSignals(False)
        self._apply_board_view()
        self.waveform.reset(self.waveform.buffer.sample_rate_hz, channel_count)

    def _board_view_changed(self) -> None:
        self._apply_board_view()

    def _apply_board_view(self) -> None:
        selected_range = self.board_view_combo.currentData()
        for channel, action in enumerate(self.channel_menu.actions()):
            action.setVisible(
                selected_range is None
                or (selected_range[0] <= channel < selected_range[1])
            )
            self.waveform.channel_visible[channel] = (
                action.isChecked()
                and (
                    selected_range is None
                    or selected_range[0] <= channel < selected_range[1]
                )
            )
        self._update_channel_button()
        self.waveform.refresh()

    def _disconnect_clicked(self) -> None:
        if self._disconnect_pending:
            return
        self._disconnect_pending = True
        self.disconnect_button.hide()
        self.pause_button.setEnabled(False)
        self.record_button.setEnabled(False)
        self.disconnect_requested.emit()

    def _analysis_toggled(self, visible: bool) -> None:
        self.analysis_panel.setVisible(visible)
        self.analysis_toggle.setText("隐藏分析" if visible else "显示分析")

    def _pause_toggled(self, paused: bool) -> None:
        self.waveform.paused = paused
        self.pause_button.setText("继续画面" if paused else "暂停画面")
        if not paused:
            self.waveform.refresh()

    def _scale_changed(self, text: str) -> None:
        if text.startswith("自动"):
            self.waveform.scale_mode = "auto"
        else:
            self.waveform.scale_mode = "uniform"
            self.waveform.uniform_scale_uv = 50.0 if "50" in text else 100.0

    def _channel_toggled(self, channel: int, visible: bool) -> None:
        self.waveform.channel_visible[channel] = visible
        self._apply_board_view()

    def _update_channel_button(self) -> None:
        actions = self.channel_menu.actions()
        selected = sum(1 for action in actions if action.isChecked())
        self.channel_button.setText(f"通道 · {selected}/{len(actions)}")


class HistoryPage(QWidget):
    back_requested = Signal()
    open_session = Signal(Path)

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        top = QHBoxLayout()
        back = QPushButton("‹ 返回连接页面")
        back.clicked.connect(self.back_requested)
        title = QLabel("历史记录")
        title.setObjectName("PageTitle")
        self.refresh_button = QPushButton("刷新")
        top.addWidget(back)
        top.addWidget(title)
        top.addStretch()
        top.addWidget(self.refresh_button)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["会话", "时长", "采样率", "通道", "Marker", "状态", "Gap", "CRC"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.doubleClicked.connect(self._open_selected)
        layout.addLayout(top)
        layout.addWidget(muted("双击记录即可回放。中断记录会保留已有原始帧并明确标记。"))
        layout.addWidget(self.table)

    def set_sessions(self, sessions: list[SessionSummary]) -> None:
        self.table.setRowCount(len(sessions))
        for row, summary in enumerate(sessions):
            values = (
                summary.name,
                f"{summary.duration_seconds:.1f} s",
                f"{summary.sample_rate_hz} Hz",
                str(summary.channel_count),
                str(summary.marker_count),
                "完整" if summary.status == "complete" else "中断",
                str(summary.sequence_gaps),
                str(summary.crc_errors),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, str(summary.path))
                self.table.setItem(row, column, item)

    def _open_selected(self) -> None:
        row = self.table.currentRow()
        if row >= 0 and (item := self.table.item(row, 0)) is not None:
            self.open_session.emit(Path(item.data(Qt.ItemDataRole.UserRole)))


class ReplayPage(QWidget):
    back_requested = Signal()
    export_requested = Signal(Path, str)

    def __init__(self) -> None:
        super().__init__()
        self.session: LoadedSession | None = None
        self.session_path: Path | None = None
        self._position = 0
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._advance)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        header = QHBoxLayout()
        back = QPushButton("‹ 返回历史记录")
        back.clicked.connect(self.back_requested)
        self.title = QLabel("回放")
        self.title.setObjectName("PageTitle")
        self.play = QPushButton("播放")
        self.play.setCheckable(True)
        self.play.toggled.connect(self._toggle_play)
        export_uv = QPushButton("导出微伏 CSV")
        export_raw = QPushButton("导出原始计数 CSV")
        export_bdf = QPushButton("导出 BDF+")
        export_uv.clicked.connect(lambda: self._emit_export("uv"))
        export_raw.clicked.connect(lambda: self._emit_export("raw"))
        export_bdf.clicked.connect(lambda: self._emit_export("bdf"))
        self.marker_combo = QComboBox()
        self.marker_combo.addItem("Marker")
        self.marker_combo.currentIndexChanged.connect(self._jump_to_marker)
        self.window_combo = QComboBox()
        self.window_combo.addItems(["2 秒", "5 秒", "10 秒", "30 秒"])
        self.window_combo.setCurrentText("10 秒")
        self.waveform = WaveformView()
        self.window_combo.currentTextChanged.connect(
            lambda text: self.waveform.set_window(int(text.split()[0]))
        )
        header.addWidget(back)
        header.addWidget(self.title)
        header.addStretch()
        header.addWidget(self.play)
        header.addWidget(self.marker_combo)
        header.addWidget(self.window_combo)
        header.addWidget(export_uv)
        header.addWidget(export_raw)
        header.addWidget(export_bdf)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.valueChanged.connect(self._seek)
        self.time_label = muted("00:00 / 00:00")
        bottom = QHBoxLayout()
        bottom.addWidget(self.slider, 1)
        bottom.addWidget(self.time_label)
        layout.addLayout(header)
        layout.addWidget(self.waveform, 1)
        layout.addLayout(bottom)

    def load(self, path: Path, session: LoadedSession) -> None:
        self.session_path = path
        self.session = session
        self.title.setText(path.name)
        self._position = 0
        self.slider.setRange(0, max(0, session.batch.sample_count - 1))
        self.slider.setValue(0)
        self.marker_combo.blockSignals(True)
        self.marker_combo.clear()
        self.marker_combo.addItem("跳转 Marker", None)
        for marker in session.markers:
            self.marker_combo.addItem(
                f"{marker.marker_id} · {marker.label}", marker.device_timestamp_us
            )
        self.marker_combo.blockSignals(False)
        self._render_position()

    def _toggle_play(self, playing: bool) -> None:
        self.play.setText("暂停" if playing else "播放")
        if playing:
            self._timer.start()
        else:
            self._timer.stop()

    def _advance(self) -> None:
        if self.session is None:
            return
        step = max(1, self.session.batch.sample_rate_hz // 30)
        self._position = min(self.session.batch.sample_count - 1, self._position + step)
        self.slider.blockSignals(True)
        self.slider.setValue(self._position)
        self.slider.blockSignals(False)
        self._render_position()
        if self._position >= self.session.batch.sample_count - 1:
            self.play.setChecked(False)

    def _seek(self, value: int) -> None:
        self._position = value
        self._render_position()

    def _jump_to_marker(self, index: int) -> None:
        if index <= 0 or self.session is None:
            return
        timestamp = self.marker_combo.itemData(index)
        if timestamp is None or not self.session.batch.sample_count:
            return
        distances = abs(self.session.batch.timestamps_us.astype("int64") - int(timestamp))
        self.slider.setValue(int(distances.argmin()))

    def _render_position(self) -> None:
        if self.session is None or not self.session.batch.sample_count:
            return
        batch = self.session.batch
        window = batch.sample_rate_hz * self.waveform.buffer.seconds
        start = max(0, self._position - window + 1)
        end = self._position + 1
        subset = SampleBatch(
            batch.timestamps_us[start:end],
            batch.sequences[start:end],
            batch.raw_counts[start:end],
            batch.microvolts[start:end],
            batch.flags[start:end],
            batch.sample_rate_hz,
        )
        self.waveform.buffer.load(subset)
        self.waveform.refresh()
        elapsed = self._position / batch.sample_rate_hz
        total = max(0.0, (batch.sample_count - 1) / batch.sample_rate_hz)
        self.time_label.setText(f"{elapsed / 60:05.2f} / {total / 60:05.2f} 分钟")

    def _emit_export(self, unit: str) -> None:
        if self.session_path is not None:
            self.export_requested.emit(self.session_path, unit)
