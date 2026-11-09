"""Camera outage protection after the system hang of 09.11.2026: alarm, DirectShow, no pile-up."""
import json
import threading
import time

import pytest

from instrument_reader.core.camera_devices import CameraDevice
from instrument_reader.gui.camera_alarm import CameraAlarm
from instrument_reader.gui.camera_thread import CameraThread
from tests.test_camera_reconnect import FakeCamera, FakeDevice, StuckRouteCamera, fast_thread  # noqa: F401


def _alarm(qtbot, threshold=0.2):
    beeps = []
    alarm = CameraAlarm(threshold, beep=lambda: beeps.append(time.monotonic()))
    alarm._timer.setInterval(20)
    events = []
    alarm.raised.connect(lambda t: events.append(("raised", t)))
    alarm.cleared.connect(lambda t, gap: events.append(("cleared", t, gap)))
    return alarm, beeps, events


def test_alarm_beeps_until_acknowledged_and_ends_when_pictures_return(qtbot, monkeypatch):
    monkeypatch.setattr(CameraAlarm, "BEEP_EVERY_S", 0.05)
    alarm, beeps, events = _alarm(qtbot)
    alarm.watch()
    qtbot.wait(100)
    assert events == [] and beeps == []

    qtbot.waitUntil(lambda: len(beeps) >= 3, timeout=2000)
    assert alarm.alarming and events[0][0] == "raised"
    lost_at = events[0][1]

    alarm.acknowledge()
    n = len(beeps)
    qtbot.wait(200)
    assert len(beeps) == n and alarm.alarming  # quiet, but still on

    alarm.frame_arrived()
    qtbot.waitUntil(lambda: not alarm.alarming, timeout=1000)
    kind, start, gap = events[-1]
    assert kind == "cleared" and start == lost_at and gap >= 0.2
    alarm.unwatch()


def test_alarm_stays_off_while_pictures_come_and_after_stop(qtbot):
    alarm, beeps, events = _alarm(qtbot)
    alarm.watch()
    for _ in range(10):
        alarm.frame_arrived()
        qtbot.wait(40)
    alarm.unwatch()
    qtbot.wait(400)
    assert events == [] and beeps == []


def _window(qtbot, monkeypatch, devices=()):
    from instrument_reader.gui.main_window import MainWindow

    monkeypatch.setattr("instrument_reader.gui.control_panel.list_cameras", lambda: list(devices))
    monkeypatch.setattr("instrument_reader.gui.camera_alarm._system_beep", lambda: None)
    win = MainWindow()
    qtbot.addWidget(win)
    win.ocr_worker.start = win.ocr_worker.stop = lambda *a: None
    return win


def test_alarm_shows_banner_and_marks_the_gap_in_the_run(qtbot, monkeypatch, tmp_path):
    from instrument_reader.core import data_safety
    from instrument_reader.core.journal import rebuild_database

    win = _window(qtbot, monkeypatch)
    exp = win.db_writer.create_experiment("Alarm", "VOC", "")
    run = win.db_writer.create_run(exp, "")
    win.camera_alarm.threshold_s = 0.2
    win.camera_alarm._timer.setInterval(20)

    win.camera_alarm.watch()
    qtbot.waitUntil(lambda: win.alarm_banner.isVisibleTo(win), timeout=2000)
    assert "KEIN KAMERABILD" in win.alarm_label.text()
    win.alarm_ack_btn.click()
    assert win.camera_alarm.acknowledged and win.alarm_banner.isVisibleTo(win)

    win.camera_alarm.frame_arrived()
    qtbot.waitUntil(lambda: not win.alarm_banner.isVisibleTo(win), timeout=2000)
    win.camera_alarm.unwatch()

    events = [(e["event"], e["reason"]) for e in win.db_writer.db.get_run_events(run)]
    assert events[0] == ("start", None)
    assert events[1][0] == "camera_lost"
    assert events[2][0] == "camera_back" and events[2][1].endswith("s ohne Bild")

    # The gap survives a rebuild from the journal.
    db = win.db_writer.db.db_path
    rebuild_database(data_safety.journal_dir_for(db), tmp_path / "rebuilt.db")
    import sqlite3

    with sqlite3.connect(tmp_path / "rebuilt.db") as conn:
        rebuilt = [r[0] for r in conn.execute("SELECT event FROM run_events WHERE run_id = ? ORDER BY id", (run,))]
    assert rebuilt == ["start", "camera_lost", "camera_back"]


def test_directshow_setting_is_saved_and_used_on_start(qtbot, monkeypatch):
    from instrument_reader.core import data_safety
    from instrument_reader.core.camera import OpenCVCamera
    from instrument_reader.gui import main_window as mw

    monkeypatch.setattr("sys.platform", "win32")
    started = []

    class NoThread(CameraThread):
        def start(self):
            started.append((self.camera.route, self.camera.via_frame_server))
            self.running = True

        def stop(self):
            self.running = False

    monkeypatch.setattr(mw, "CameraThread", NoThread)
    monkeypatch.setattr(OpenCVCamera, "open", lambda self: False)
    win = _window(qtbot, monkeypatch, [CameraDevice(1, "PC-LM1E Camera")])
    assert not win.control_panel.directshow_cb.isChecked()

    win.control_panel.directshow_cb.setChecked(True)
    settings = json.loads((data_safety.data_dir() / "settings.json").read_text(encoding="utf-8"))
    assert settings["kamera"]["directshow"] is True

    win.control_panel.start_btn.click()
    assert started == [("other", False)]  # straight to DirectShow, not through the camera service
    win.control_panel.stop_btn.click()

    # A new window (app restart) keeps the choice.
    assert _window(qtbot, monkeypatch).control_panel.directshow_cb.isChecked()


def test_via_frame_server(monkeypatch):
    from instrument_reader.core.camera import OpenCVCamera

    monkeypatch.setattr("sys.platform", "win32")
    msmf, typed_dshow = OpenCVCamera(1), OpenCVCamera(701)
    assert msmf.via_frame_server and not typed_dshow.via_frame_server
    msmf.avoid_frame_server()
    typed_dshow.avoid_frame_server()
    assert (msmf.route, msmf.via_frame_server) == ("other", False)
    assert (typed_dshow.route, typed_dshow.via_frame_server) == ("direct", False)
    typed_dshow.next_route()
    assert typed_dshow.via_frame_server  # 701's fallback is Media Foundation
    assert not OpenCVCamera("run.mp4").via_frame_server
    monkeypatch.setattr("sys.platform", "linux")
    assert not OpenCVCamera(0).via_frame_server


class DirectShowCamera(StuckRouteCamera):
    """A typed 701: the direct route is DirectShow; it drops once, briefly."""

    drop = False
    via_frame_server = property(lambda self: self.route != "direct")

    def clone(self):
        twin = DirectShowCamera(self.device, self.source)
        twin.route = self.route
        return twin

    def open(self):
        StuckRouteCamera.routes_tried.append(self.route)
        return FakeCamera.open(self)

    def read(self):
        if DirectShowCamera.drop:
            return False, None
        return FakeCamera.read(self)


def test_dropout_on_directshow_does_not_jump_to_media_foundation(qtbot, fast_thread):
    StuckRouteCamera.routes_tried = []
    DirectShowCamera.drop = False
    thread = CameraThread(DirectShowCamera(FakeDevice()))
    states = []
    thread.status_changed.connect(lambda st, msg: states.append(st))
    thread.start()
    qtbot.waitUntil(lambda: bool(states) and states[-1] == "connected", timeout=3000)
    DirectShowCamera.drop = True
    qtbot.waitUntil(lambda: states[-1] == "reconnecting", timeout=3000)
    DirectShowCamera.drop = False
    qtbot.waitUntil(lambda: states[-1] == "connected", timeout=3000)
    assert set(StuckRouteCamera.routes_tried) == {"direct"}
    thread.stop()


def test_hanging_driver_gets_no_pile_of_connection_attempts(qtbot, fast_thread):
    """09.11.: every route hangs; the app must not keep starting new stuck attempts."""
    device = FakeDevice()
    thread = CameraThread(FakeCamera(device))
    states, messages = [], []
    thread.status_changed.connect(lambda st, msg: (states.append(st), messages.append(msg)))
    thread.start()
    qtbot.waitUntil(lambda: bool(states) and states[-1] == "connected", timeout=3000)

    device.hang = True
    qtbot.wait(3000)  # = 10 stall timeouts
    stuck = [t for t in threading.enumerate() if t.name == "camera-session" and t.is_alive()]
    assert len(stuck) <= CameraThread.MAX_HANGING_CONNECTIONS
    assert device.opens <= 1 + CameraThread.MAX_HANGING_CONNECTIONS
    assert "Kameratreiber hängt" in messages[-1]

    device.hang = False
    device.unhang.set()  # the driver lets go: the stuck reads return
    qtbot.waitUntil(lambda: states[-1] == "connected", timeout=5000)
    thread.stop()
    assert device.open_handles == 0


def test_backoff_grows_to_30_s():
    delays = CameraThread.RECONNECT_DELAYS_S
    assert delays[0] == 0.0 and list(delays) == sorted(delays) and delays[-1] == 30.0


def test_unkillable_helper_is_counted(monkeypatch):
    from instrument_reader.core import camera_process
    from instrument_reader.core.camera import OpenCVCamera

    class Unkillable:
        pid = 4711
        alive = True

        def is_alive(self):
            return self.alive

        def join(self, timeout=None):
            pass

        def kill(self):
            pass

    monkeypatch.setattr(camera_process, "_unfinished", [])
    cap = camera_process.ProcessCapture.__new__(camera_process.ProcessCapture)
    cap._opened, cap._proc = True, Unkillable()
    cap._stop, cap._conn = threading.Event(), type("C", (), {"close": lambda self: None})()
    cap.release()
    assert camera_process.unfinished_helpers() == 1
    monkeypatch.setattr(OpenCVCamera, "USE_HELPER_PROCESS", True)
    assert OpenCVCamera(0).stuck_helpers() == 1
    cap._proc.alive = False
    assert camera_process.unfinished_helpers() == 0
