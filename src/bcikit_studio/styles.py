"""Small macOS-oriented visual system shared by all pages."""

from __future__ import annotations

from typing import cast

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

LIGHT = {
    "window": "#F4F6F9",
    "panel": "#FFFFFF",
    "panel_alt": "#F8FAFC",
    "text": "#142033",
    "muted": "#66758A",
    "border": "#DCE3EC",
    "accent": "#247CFF",
    "accent_hover": "#136DEB",
    "cyan": "#0AAFC8",
    "danger": "#D94452",
    "warning": "#D48806",
    "success": "#159A67",
}

DARK = {
    "window": "#10141B",
    "panel": "#171D27",
    "panel_alt": "#1C2430",
    "text": "#EEF4FF",
    "muted": "#9DACBF",
    "border": "#2A3544",
    "accent": "#4C91FF",
    "accent_hover": "#69A4FF",
    "cyan": "#24C3D8",
    "danger": "#FF6472",
    "warning": "#F1B84B",
    "success": "#37C58B",
}


def is_dark(app: QApplication) -> bool:
    return app.palette().color(QPalette.ColorRole.Window).lightness() < 128


def colors(app: QApplication | None = None) -> dict[str, str]:
    current = app or cast(QApplication | None, QApplication.instance())
    return DARK if current is not None and is_dark(current) else LIGHT


def stylesheet(app: QApplication) -> str:
    c = colors(app)
    return f"""
    * {{ font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Text', 'PingFang SC'; }}
    QMainWindow, QWidget#AppRoot {{ background: {c["window"]}; color: {c["text"]}; }}
    QLabel {{ color: {c["text"]}; }}
    QLabel#Muted {{ color: {c["muted"]}; }}
    QLabel#Title {{ font-size: 28px; font-weight: 700; }}
    QLabel#PageTitle {{ font-size: 22px; font-weight: 700; }}
    QLabel#MetricValue {{ font-size: 19px; font-weight: 700; }}
    QLabel#MetricLabel {{ color: {c["muted"]}; font-size: 12px; }}
    QFrame#Card, QFrame#MetricCard {{
      background: {c["panel"]}; border: 1px solid {c["border"]}; border-radius: 14px;
    }}
    QFrame#AnalysisPanel {{
      background: {c["panel"]}; border: 1px solid {c["border"]}; border-radius: 12px;
    }}
    QFrame#Sidebar {{ background: {c["panel"]}; border-right: 1px solid {c["border"]}; }}
    QListWidget#NavList {{ background: transparent; border: none; outline: none; }}
    QListWidget#NavList::item {{ padding: 11px 14px; margin: 3px 8px; border-radius: 9px; }}
    QListWidget#NavList::item:selected {{ background: {c["accent"]}; color: white; }}
    QPushButton {{
      background: {c["panel_alt"]}; color: {c["text"]}; border: 1px solid {c["border"]};
      border-radius: 9px; padding: 8px 14px; font-weight: 600;
    }}
    QPushButton:hover {{ border-color: {c["accent"]}; }}
    QPushButton:disabled {{ color: {c["muted"]}; opacity: .55; }}
    QPushButton#Primary {{ background: {c["accent"]}; color: white; border-color: {c["accent"]}; }}
    QPushButton#Primary:hover {{ background: {c["accent_hover"]}; }}
    QPushButton#Danger {{ color: {c["danger"]}; }}
    QPushButton#Record[recording="true"] {{
      background: {c["danger"]}; color: white; border-color: {c["danger"]};
    }}
    QComboBox, QLineEdit {{
      background: {c["panel"]}; color: {c["text"]}; border: 1px solid {c["border"]};
      border-radius: 8px; padding: 7px 10px;
    }}
    QTableWidget {{
      background: {c["panel"]}; alternate-background-color: {c["panel_alt"]}; color: {c["text"]};
      border: 1px solid {c["border"]}; border-radius: 12px; gridline-color: {c["border"]};
    }}
    QHeaderView::section {{
      background: {c["panel_alt"]}; color: {c["muted"]}; border: none; padding: 8px;
    }}
    QTextEdit {{ background: {c["panel"]}; color: {c["text"]}; border: none; }}
    QDockWidget {{ color: {c["text"]}; }}
    QSlider::groove:horizontal {{ height: 4px; background: {c["border"]}; border-radius: 2px; }}
    QSlider::handle:horizontal {{
      background: {c["accent"]}; width: 14px; margin: -5px 0; border-radius: 7px;
    }}
    QProgressBar {{ background: {c["border"]}; border: none; border-radius: 3px; }}
    QProgressBar::chunk {{ background: {c["cyan"]}; border-radius: 3px; }}
    """


def configure_app(app: QApplication) -> None:
    app.setStyle("Fusion")
    app.setStyleSheet(stylesheet(app))
