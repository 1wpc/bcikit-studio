"""Exercise the Raspberry Pi local-connection UI with electrodes disconnected."""

from __future__ import annotations

import argparse
import asyncio

from PySide6.QtCore import QTimer
from qasync import QEventLoop

from bcikit_studio.app import build_application


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--electrodes-disconnected", action="store_true", required=True)
    parser.add_argument("--screenshot", default="/tmp/bcikit-studio-pi5-live.png")
    args = parser.parse_args()
    if not args.electrodes_disconnected:
        parser.error("先断开人体和所有电极，再传入 --electrodes-disconnected")

    app, window, controller = build_application()
    controller._show_error = lambda title, message: print(f"{title}: {message}")
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)
    failed = False

    async def exercise() -> None:
        nonlocal failed
        try:
            await controller.connect_local()
            await asyncio.sleep(1.0)
            session = controller.session
            if session is None or session.stats.valid_frames == 0:
                raise RuntimeError("本机界面未收到采样帧")
            if window.pages.currentWidget() is not window.live:
                raise RuntimeError("连接后未进入实时采集页面")
            window.grab().save(args.screenshot)
            print(f"UI received {session.stats.valid_frames} frames; screenshot: {args.screenshot}")
            await controller.disconnect_session()
        except Exception as error:
            failed = True
            print(f"UI smoke failed: {error}")
        finally:
            app.quit()

    window.show()
    QTimer.singleShot(100, lambda: asyncio.create_task(exercise()))
    app.aboutToQuit.connect(loop.stop)
    with loop:
        loop.run_forever()
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
