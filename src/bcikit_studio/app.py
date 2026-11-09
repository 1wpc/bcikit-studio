"""Application entry point."""

from __future__ import annotations

import argparse
import asyncio
import sys
from importlib.resources import files
from typing import cast

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from qasync import QEventLoop

from . import __version__
from .controller import StudioController
from .styles import configure_app
from .window import MainWindow


def build_application() -> tuple[QApplication, MainWindow, StudioController]:
    app = cast(QApplication | None, QApplication.instance()) or QApplication(sys.argv)
    app.setApplicationName("BCIKit Studio")
    app.setOrganizationName("BCIKit")
    app.setApplicationVersion(__version__)
    app.setWindowIcon(QIcon(str(files("bcikit_studio.resources").joinpath("BCIKitStudio.icns"))))
    configure_app(app)
    window = MainWindow()
    controller = StudioController(window)
    return app, window, controller


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--screenshot")
    # Deliberately undocumented: retained only for automated UI screenshots and tests.
    parser.add_argument("--studio-dev-virtual-demo", action="store_true")
    options, _ = parser.parse_known_args()
    app, window, controller = build_application()
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)
    window.show()
    if options.studio_dev_virtual_demo:
        QTimer.singleShot(
            100,
            lambda: asyncio.create_task(controller.connect_development_virtual()),
        )
    if options.screenshot:

        def capture() -> None:
            window.grab().save(options.screenshot)
            # Exercise the same graceful shutdown path as the macOS close button.
            window.close()

        # Development simulation needs time for the two-second PSD window to fill.
        QTimer.singleShot(3500 if options.studio_dev_virtual_demo else 800, capture)
    app.aboutToQuit.connect(loop.stop)
    with loop:
        loop.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
