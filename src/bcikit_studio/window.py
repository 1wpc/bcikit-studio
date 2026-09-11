"""Main application window and navigation shell."""

from __future__ import annotations

import logging
from contextlib import suppress
from datetime import datetime
from html import escape
from importlib.resources import files

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent, QIcon
from PySide6.QtWidgets import (
    QDockWidget,
    QMainWindow,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .pages import DiagnosticsPage, HistoryPage, HomePage, LivePage, ReplayPage


class _DockLogHandler(logging.Handler):
    """Forward application logs to Qt safely, including async worker output."""

    def __init__(self, window: MainWindow) -> None:
        super().__init__()
        self._window = window

    def emit(self, record: logging.LogRecord) -> None:
        # Qt may already be tearing down while a final async task logs.
        with suppress(RuntimeError):
            self._window.log_received.emit(record.levelname.lower(), self.format(record))


class MainWindow(QMainWindow):
    log_received = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("BCIKit Studio")
        self.setMinimumSize(1060, 720)
        self.resize(1440, 900)
        icon_path = files("bcikit_studio.resources").joinpath("BCIKitStudio.icns")
        self.setWindowIcon(QIcon(str(icon_path)))
        self.controller = None
        root = QWidget()
        root.setObjectName("AppRoot")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.pages = QStackedWidget()
        self.home = HomePage()
        self.hardware_diagnostics = DiagnosticsPage()
        self.live = LivePage()
        self.history = HistoryPage()
        self.replay = ReplayPage()
        self.pages.addWidget(self.home)
        self.pages.addWidget(self.hardware_diagnostics)
        self.pages.addWidget(self.live)
        self.pages.addWidget(self.history)
        self.pages.addWidget(self.replay)
        root_layout.addWidget(self.pages, 1)
        self.setCentralWidget(root)

        self.diagnostics = QTextEdit()
        self.diagnostics.setReadOnly(True)
        self.diagnostics.document().setMaximumBlockCount(5_000)
        self.diagnostics.setPlaceholderText("应用启动、连接、协议、采集和异常日志会显示在这里。")
        self.diagnostic_dock = QDockWidget("运行日志", self)
        self.diagnostic_dock.setAllowedAreas(
            Qt.DockWidgetArea.BottomDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.diagnostic_dock.setWidget(self.diagnostics)
        self.diagnostic_dock.hide()
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.diagnostic_dock)
        view_menu = self.menuBar().addMenu("视图")
        view_menu.addAction("显示运行日志", self._toggle_diagnostics)
        self.log_received.connect(self.append_log)
        self._log_handler = _DockLogHandler(self)
        self._log_handler.setFormatter(logging.Formatter("%(name)s · %(message)s"))
        for name in ("bcikit", "bcikit_studio"):
            logger = logging.getLogger(name)
            logger.setLevel(logging.DEBUG)
            logger.addHandler(self._log_handler)
        self.append_log("info", "BCIKit Studio 已启动；可通过“视图 → 显示运行日志”查看全局事件。")

    def set_controller(self, controller) -> None:
        self.controller = controller

    def show_home(self) -> None:
        self.pages.setCurrentWidget(self.home)

    def show_live(self) -> None:
        self.pages.setCurrentWidget(self.live)

    def show_hardware_diagnostics(self) -> None:
        self.pages.setCurrentWidget(self.hardware_diagnostics)

    def show_history(self) -> None:
        self.pages.setCurrentWidget(self.history)

    def show_replay(self) -> None:
        self.pages.setCurrentWidget(self.replay)

    def append_diagnostic(self, level: str, message: str) -> None:
        self.append_log(level, message)

    def append_log(self, level: str, message: str) -> None:
        colors = {"error": "#E65A68", "warning": "#D99B26", "info": "#2B8CFF"}
        color = colors.get(level, "#7B8798")
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.diagnostics.append(
            f'<span style="color:{color}">[{timestamp}] [{escape(level.upper())}]</span> '
            f"{escape(message)}"
        )
        scrollbar = self.diagnostics.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _toggle_diagnostics(self) -> None:
        self.diagnostic_dock.setVisible(not self.diagnostic_dock.isVisible())

    def stop_log_capture(self) -> None:
        for name in ("bcikit", "bcikit_studio"):
            logger = logging.getLogger(name)
            logger.removeHandler(self._log_handler)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.controller is not None and not self.controller.shutdown_complete:
            event.ignore()
            # Give immediate visual feedback while recordings and transports close safely.
            self.hide()
            self.controller.request_shutdown()
            return
        event.accept()
