import asyncio

import numpy as np
import pyqtgraph as pg
import pytest

import bcikit_studio.controller as controller_module
from bcikit.analysis import analyze_eeg
from bcikit.models import IntegrityStats, SampleBatch, SessionConfig, SessionState
from bcikit.session import CommandError, DeviceSession
from bcikit.transport import VirtualTransport
from bcikit_studio.controller import StudioController
from bcikit_studio.styles import configure_app
from bcikit_studio.waveform import TRACK_HALF_HEIGHT, WaveformView
from bcikit_studio.window import MainWindow


def test_main_window_has_first_release_pages(qtbot, qapp):
    configure_app(qapp)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    assert window.minimumWidth() <= 1060
    assert window.pages.count() == 5
    assert not hasattr(window, "nav")
    assert window.home.isVisible()
    window.show_live()
    assert window.live.isVisible()
    assert window.live.marker_button.isEnabled() is False


def test_home_has_a_standalone_hardware_diagnostics_entry(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show_hardware_diagnostics()

    assert window.pages.currentWidget() is window.hardware_diagnostics
    assert not window.hardware_diagnostics.action_buttons["internal_test"].isEnabled()
    assert not window.hardware_diagnostics.real_connect_button.isHidden()
    assert not window.hardware_diagnostics.bluetooth_connect_button.isHidden()


def test_diagnostics_page_exposes_bluetooth_connection_entry(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    requests = []
    window.hardware_diagnostics.connect_bluetooth_requested.connect(
        lambda: requests.append(True)
    )

    window.hardware_diagnostics.bluetooth_connect_button.click()

    assert requests == [True]


def test_connection_page_configures_dynamic_multiboard_live_workspace(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    home = window.home

    home.board_combo.setCurrentIndex(3)
    home.sample_rate_combo.setCurrentIndex(1)
    home.gain_combo.setCurrentIndex(5)
    assert home.connection_options() == (4, 500, 5)

    window.live.set_channel_count(32)
    assert len(window.live.channel_menu.actions()) == 32
    assert window.live.channel_button.text() == "通道 · 32/32"
    assert window.live.board_view_combo.currentText() == "全部通道"
    assert window.live.waveform.buffer.channel_count == 32
    assert all(action.isVisible() for action in window.live.channel_menu.actions())
    assert all(window.live.waveform.channel_visible[:32])


def test_home_exposes_a_separate_bluetooth_connection_entry(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    requests = []
    window.home.connect_bluetooth.connect(
        lambda boards, rate, gain: requests.append((boards, rate, gain))
    )

    window.home.bluetooth_card.action.click()

    assert requests == [(1, 250, 6)]


def test_local_pi_entry_reuses_the_acquisition_configuration(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    home = window.home
    requests = []
    home.board_combo.setCurrentIndex(1)
    home.gain_combo.setCurrentIndex(0)
    home.connect_local.connect(lambda boards, rate, gain: requests.append((boards, rate, gain)))

    home.local_card.action.click()

    assert requests == [(2, 250, 0)]
    assert hasattr(window.hardware_diagnostics, "connect_local_requested")


def test_shipped_connection_and_diagnostics_pages_do_not_expose_virtual_devices(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)

    assert not hasattr(window.home, "virtual_card")
    assert not hasattr(window.home, "connect_virtual")
    assert not hasattr(window.hardware_diagnostics, "virtual_connect_button")
    assert not hasattr(window.hardware_diagnostics, "connect_virtual_requested")


def test_waveform_channel_names_use_fixed_left_axis(qtbot):
    waveform = WaveformView()
    qtbot.addWidget(waveform)

    assert waveform.channel_axis.isVisible()
    assert waveform.channel_axis.width() >= 30
    assert not any(isinstance(item, pg.TextItem) for item in waveform.plot.getPlotItem().items)


def test_global_runtime_log_is_available_in_a_toggleable_dock(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    window.append_log("warning", "测试：第一帧 SAMPLE 尚未到达")
    assert "第一帧 SAMPLE 尚未到达" in window.diagnostics.toPlainText()
    assert window.diagnostic_dock.windowTitle() == "运行日志"
    assert window.diagnostic_dock.isHidden()

    window._toggle_diagnostics()
    assert window.diagnostic_dock.isVisible()


def test_waveform_clips_each_channel_to_its_own_track_and_draws_separators(qtbot):
    waveform = WaveformView()
    qtbot.addWidget(waveform)
    samples = 32
    batch = SampleBatch(
        timestamps_us=np.arange(samples, dtype=np.uint64) * 4_000,
        sequences=np.arange(samples, dtype=np.uint32),
        raw_counts=np.zeros((samples, 8), dtype=np.int32),
        microvolts=np.full((samples, 8), 100_000.0, dtype=np.float64),
        flags=np.zeros(samples, dtype=np.uint16),
        sample_rate_hz=250,
    )

    waveform.append(batch, batch.microvolts)
    waveform.refresh()

    assert len(waveform._separators) >= 8
    assert all(separator.isVisible() for separator in waveform._separators[:7])
    assert not waveform._separators[7].isVisible()
    for channel, curve in enumerate(waveform._curves[:8]):
        _, y_values = curve.getData()
        assert y_values is not None
        assert np.max(y_values) <= channel + TRACK_HALF_HEIGHT
        assert np.min(y_values) >= channel - TRACK_HALF_HEIGHT
        assert np.median(y_values) == pytest.approx(channel)


def test_auto_scale_shows_live_per_channel_microvolt_range(qtbot):
    waveform = WaveformView()
    qtbot.addWidget(waveform)
    waveform.show()
    values = np.zeros((100, 8), dtype=np.float64)
    values[:, 0] = np.linspace(-20.0, 20.0, 100) + 1_000.0
    values[:, 1] = np.linspace(-2_000.0, 2_000.0, 100)
    batch = SampleBatch(
        timestamps_us=np.arange(100, dtype=np.uint64) * 4_000,
        sequences=np.arange(100, dtype=np.uint32),
        raw_counts=np.zeros((100, 8), dtype=np.int32),
        microvolts=values,
        flags=np.zeros(100, dtype=np.uint16),
        sample_rate_hz=250,
    )

    waveform.scale_mode = "auto"
    waveform.append(batch, values)
    waveform.refresh()

    qtbot.waitUntil(lambda: waveform.channel_axis.width() == 120)
    assert waveform._channel_label(0).startswith("CH1  ±")
    assert waveform._channel_label(0).endswith("µV")
    assert waveform._channel_label(1).startswith("CH2  ±")
    assert waveform._channel_label(1).endswith("mV")
    assert waveform._auto_scales_uv[1] > waveform._auto_scales_uv[0]
    np.testing.assert_array_equal(waveform.buffer.values(False)[1], values)

    waveform.scale_mode = "uniform"
    waveform.refresh()
    qtbot.waitUntil(lambda: waveform.channel_axis.width() == 54)
    assert waveform._channel_label(0) == "CH1"

    waveform.scale_mode = "auto"
    waveform.clear()
    assert waveform._channel_label(0) == "CH1"


def test_window_hides_immediately_while_shutdown_is_requested(qtbot):
    class ControllerStub:
        shutdown_complete = False
        requests = 0

        def request_shutdown(self) -> None:
            self.requests += 1

    window = MainWindow()
    controller = ControllerStub()
    window.set_controller(controller)
    qtbot.addWidget(window)
    window.show()

    window.close()

    assert not window.isVisible()
    assert controller.requests == 1
    controller.shutdown_complete = True
    window.close()


def test_disconnect_button_hides_immediately(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show_live()
    window.show()
    window.live.set_state(SessionState.STREAMING)
    requests = []
    window.live.disconnect_requested.connect(lambda: requests.append(True))

    assert window.live.disconnect_button.isVisible()
    window.live.disconnect_button.click()

    assert window.live.disconnect_button.isHidden()
    assert requests == [True]


@pytest.mark.asyncio
async def test_disconnect_returns_to_home_before_tcp_cleanup_completes(qtbot):
    class SlowClosingSession:
        def __init__(self) -> None:
            self.close_started = asyncio.Event()
            self.allow_close = asyncio.Event()

        async def close(self) -> None:
            self.close_started.set()
            await self.allow_close.wait()

    window = MainWindow()
    qtbot.addWidget(window)
    controller = StudioController(window)
    session = SlowClosingSession()
    controller.session = session  # type: ignore[assignment]
    window.show_live()
    window.show()

    disconnecting = asyncio.create_task(controller.disconnect_session())
    await asyncio.wait_for(session.close_started.wait(), timeout=1.0)

    assert window.pages.currentWidget() is window.home
    assert not window.home.real_card.action.isEnabled()
    assert "正在断开" in window.home.connection_status.text()

    session.allow_close.set()
    await disconnecting
    assert window.home.real_card.action.isEnabled()
    controller._plot_timer.stop()
    controller._stats_timer.stop()
    controller._analysis_timer.stop()
    controller.shutdown_complete = True


@pytest.mark.asyncio
async def test_disconnect_forces_local_release_after_close_timeout(qtbot, monkeypatch):
    class HungSession:
        recording = False

        def __init__(self) -> None:
            self.force_closed = False

        async def close(self) -> None:
            await asyncio.Event().wait()

        async def force_close(self) -> None:
            self.force_closed = True

    monkeypatch.setattr(controller_module, "DISCONNECT_TIMEOUT_SECONDS", 0.01)
    window = MainWindow()
    qtbot.addWidget(window)
    controller = StudioController(window)
    session = HungSession()
    controller.session = session  # type: ignore[assignment]

    await controller.disconnect_session()

    assert session.force_closed
    assert not controller._disconnecting
    assert controller._disconnect_finished.is_set()
    assert window.home.real_card.action.isEnabled()
    assert "强制释放本机连接" in window.diagnostics.toPlainText()
    controller._plot_timer.stop()
    controller._stats_timer.stop()
    controller._analysis_timer.stop()
    controller.shutdown_complete = True


@pytest.mark.asyncio
async def test_disconnect_forces_local_release_after_close_error(qtbot):
    class FailingSession:
        def __init__(self) -> None:
            self.force_closed = False

        async def close(self) -> None:
            raise RuntimeError("socket cleanup failed")

        async def force_close(self) -> None:
            self.force_closed = True

    window = MainWindow()
    qtbot.addWidget(window)
    controller = StudioController(window)
    session = FailingSession()
    controller.session = session  # type: ignore[assignment]

    await controller.disconnect_session()

    assert session.force_closed
    assert controller._disconnect_finished.is_set()
    assert window.home.real_card.action.isEnabled()
    controller._plot_timer.stop()
    controller._stats_timer.stop()
    controller._analysis_timer.stop()
    controller.shutdown_complete = True


@pytest.mark.asyncio
async def test_failed_handshake_waits_for_single_cleanup_before_showing_error(qtbot, monkeypatch):
    close_started = asyncio.Event()
    allow_close = asyncio.Event()
    close_calls = []
    errors = []

    async def fail_connect(session):
        session.last_error = "gain apply failed"
        session._set_state(SessionState.ERROR)
        await asyncio.sleep(0)
        raise CommandError(0x0C, 5)

    async def slow_close(session):
        close_calls.append(session)
        if session._closing:
            return
        session._closing = True
        close_started.set()
        await allow_close.wait()
        session._set_state(SessionState.DISCONNECTED)

    monkeypatch.setattr(DeviceSession, "connect", fail_connect)
    monkeypatch.setattr(DeviceSession, "close", slow_close)
    window = MainWindow()
    qtbot.addWidget(window)
    controller = StudioController(window)
    monkeypatch.setattr(
        controller, "_show_error", lambda title, detail: errors.append((title, detail))
    )
    window.show()

    connecting = asyncio.create_task(
        controller._connect(VirtualTransport("normal"), SessionConfig(gain_code=0))
    )
    await asyncio.wait_for(close_started.wait(), timeout=1.0)
    await asyncio.sleep(0)
    assert controller._disconnecting
    assert errors == []

    allow_close.set()
    await asyncio.wait_for(connecting, timeout=1.0)
    assert len(close_calls) == 1
    assert errors == [("连接失败", "命令 0x0C 失败，设备状态码 5")]
    assert controller.session is None
    assert not controller._disconnecting
    assert controller._disconnect_finished.is_set()
    assert window.home.real_card.action.isEnabled()
    controller._plot_timer.stop()
    controller._stats_timer.stop()
    controller._analysis_timer.stop()
    controller.shutdown_complete = True


def test_live_page_clears_current_session_data(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    live = window.live
    batch = SampleBatch(
        timestamps_us=np.array([1_000, 5_000], dtype=np.uint64),
        sequences=np.array([1, 2], dtype=np.uint32),
        raw_counts=np.ones((2, 8), dtype=np.int32),
        microvolts=np.ones((2, 8), dtype=np.float64),
        flags=np.zeros(2, dtype=np.uint16),
        sample_rate_hz=250,
    )
    live.waveform.append(batch, batch.microvolts)
    live.waveform.refresh()
    live.set_stats(
        IntegrityStats(
            valid_frames=2,
            sequence_gaps=3,
            crc_errors=4,
            local_queue_drops=5,
            first_timestamp_us=1_000,
            last_timestamp_us=5_000,
        )
    )
    live.pause_button.setChecked(True)
    live.filter_combo.setCurrentIndex(1)

    live.clear_session_data()

    assert live.waveform.buffer.sample_count == 0
    for curve in live.waveform._curves:
        x_values, _ = curve.getData()
        assert x_values is None or len(x_values) == 0
    assert not live.pause_button.isChecked()
    assert live.pause_button.text() == "暂停画面"
    assert live.filter_combo.currentIndex() == 0
    assert live.disconnect_button.isHidden()
    assert all(
        card.value.text() == "—"
        for card in (
            live.metric_rate,
            live.metric_frames,
            live.metric_gap,
            live.metric_crc,
            live.metric_queue,
        )
    )


def test_live_analysis_panel_updates_and_clears(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    sample_rate = 250
    time = np.arange(sample_rate * 4, dtype=np.float64) / sample_rate
    alpha = 18.0 * np.sin(2.0 * np.pi * 10.0 * time)
    result = analyze_eeg(np.column_stack([alpha] * 8), sample_rate)

    window.live.analysis_panel.set_result(result)

    assert "10.0 Hz" in window.live.analysis_panel.peak_label.text()
    assert window.live.analysis_panel.summary.text() == "已分析"
    assert not hasattr(window.live.analysis_panel, "quality_grid")
    window.live.clear_session_data()
    assert window.live.analysis_panel.summary.text() == "等待数据"
    assert window.live.analysis_panel.peak_label.text() == "主峰 —"
