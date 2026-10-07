import threading

import numpy as np
import pytest

from instrument_reader.core.camera_devices import CameraDevice, parse_source
from instrument_reader.gui.camera_thread import CameraThread
from instrument_reader.gui.control_panel import ControlPanel


class FakeDevice:
    """A webcam with a loose cable: can be unplugged, or hang inside read()."""

    def __init__(self):
        self.connected = True
        self.hang = False
        self.unhang = threading.Event()
        self.open_handles = 0
        self.opens = 0
        self.lock = threading.Lock()


class FakeCamera:
    def __init__(self, device: FakeDevice):
        self.device = device
        self._opened = False
        self._cached_fps = None

    def clone(self):
        return FakeCamera(self.device)

    def open(self):
        if not self.device.connected:
            return False
        with self.device.lock:
            self.device.open_handles += 1
            self.device.opens += 1
        self._opened = True
        return True

    def read(self):
        if self.device.hang:
            self.device.unhang.wait(5)
            return False, None
        if not self.device.connected:
            return False, None
        threading.Event().wait(0.005)
        return True, np.zeros((4, 4, 3), dtype=np.uint8)

    def release(self):
        if self._opened:
            with self.device.lock:
                self.device.open_handles -= 1
            self._opened = False

    @property
    def fps(self):
        return 30.0

    @property
    def is_video_file(self):
        return False


@pytest.fixture
def fast_thread(monkeypatch):
    monkeypatch.setattr(CameraThread, "STALL_TIMEOUT_S", 0.3)
    monkeypatch.setattr(CameraThread, "OPEN_TIMEOUT_S", 0.5)


def _start(device, qtbot):
    thread = CameraThread(FakeCamera(device))
    frames, states = [], []
    thread.frame_ready.connect(lambda f: frames.append(f))
    thread.status_changed.connect(lambda st, _msg: states.append(st))
    thread.start()
    qtbot.waitUntil(lambda: "connected" in states, timeout=2000)
    return thread, frames, states


def test_reconnects_after_unplug_and_releases_handle(qtbot, fast_thread):
    device = FakeDevice()
    thread, frames, states = _start(device, qtbot)

    device.connected = False
    qtbot.waitUntil(lambda: states[-1] == "reconnecting", timeout=2000)
    # The dead handle must be released so other apps (OBS) can use the camera.
    qtbot.waitUntil(lambda: device.open_handles == 0, timeout=2000)

    device.connected = True
    qtbot.waitUntil(lambda: states[-1] == "connected", timeout=3000)
    n = len(frames)
    qtbot.waitUntil(lambda: len(frames) > n, timeout=2000)

    thread.stop()
    assert device.open_handles == 0
    assert thread.isRunning() is False


def test_recovers_from_read_that_hangs_in_driver(qtbot, fast_thread):
    device = FakeDevice()
    thread, frames, states = _start(device, qtbot)

    device.hang = True
    qtbot.waitUntil(lambda: states[-1] == "reconnecting", timeout=2000)
    # A new connection is opened while the old read is still stuck.
    opens = device.opens
    device.hang = False
    qtbot.waitUntil(lambda: device.opens > opens and states[-1] == "connected", timeout=3000)

    device.unhang.set()  # the stuck read finally returns; its handle gets released
    thread.stop()
    qtbot.waitUntil(lambda: device.open_handles == 0, timeout=2000)


def test_keeps_retrying_when_camera_absent_at_start(qtbot, fast_thread):
    device = FakeDevice()
    device.connected = False
    thread = CameraThread(FakeCamera(device))
    states = []
    thread.status_changed.connect(lambda st, _msg: states.append(st))
    thread.start()
    qtbot.waitUntil(lambda: thread.running and states and states[-1] == "connecting", timeout=2000)

    device.connected = True
    qtbot.waitUntil(lambda: states[-1] == "connected", timeout=3000)
    thread.stop()
    assert device.open_handles == 0


def test_stop_is_quick_while_read_hangs(qtbot, fast_thread):
    import time

    device = FakeDevice()
    thread, _frames, _states = _start(device, qtbot)
    device.hang = True
    t0 = time.monotonic()
    thread.stop()
    assert time.monotonic() - t0 < 2.0
    device.unhang.set()


@pytest.mark.parametrize("text,expected", [
    ("0", 0),
    (" 2 ", 2),
    ("1: Logitech C920", 1),
    ("/dev/video2", "/dev/video2"),
    ("C:/videos/run 1.mp4", "C:/videos/run 1.mp4"),
    ("rtsp://cam/stream", "rtsp://cam/stream"),
])
def test_parse_source(text, expected):
    assert parse_source(text) == expected


def test_control_panel_camera_dropdown(qtbot, monkeypatch):
    devices = [CameraDevice(0, "Integrated Webcam"), CameraDevice(1, "USB Camera")]
    monkeypatch.setattr("instrument_reader.gui.control_panel.list_cameras", lambda: list(devices))
    panel = ControlPanel()
    qtbot.addWidget(panel)

    assert panel.camera_combo.count() == 2
    assert panel.camera_source.text() == "0: Integrated Webcam"
    assert panel.selected_source() == 0
    assert panel.is_video_source() is False

    panel.camera_combo.setCurrentIndex(1)
    assert panel.selected_source() == 1

    # Selection survives a refresh, even when the camera list order changes.
    devices.reverse()
    panel.refresh_cameras()
    assert panel.selected_source() == 1
    assert panel.camera_source.text() == "1: USB Camera"

    # Typed video paths still work and enable the video controls.
    panel.camera_source.setText("run.mp4")
    assert panel.selected_source() == "run.mp4"
    assert panel.is_video_source() is True
    panel.refresh_cameras()
    assert panel.camera_source.text() == "run.mp4"
