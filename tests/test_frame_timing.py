import sqlite3
import time
from datetime import datetime

import numpy as np
import pytest
from PySide6.QtCore import Qt

from instrument_reader.core.camera import OpenCVCamera
from instrument_reader.gui.db_writer import DatabaseWriter
from instrument_reader.gui.camera_thread import CameraThread
from instrument_reader.gui.ocr_worker import OCRWorker
from tests.test_recorder_and_playback import create_dummy_video


def _run_until_eof(thread, qtbot, timeout=10000):
    thread.start()
    qtbot.waitUntil(lambda: thread.paused, timeout=timeout)
    thread.stop()


def test_video_playback_runs_in_real_time(tmp_path, qtbot):
    """1x playback of a 1 s video takes ~1 s (no extra sleep per frame)."""
    vid = create_dummy_video(tmp_path / "rt.mp4", num_frames=20, fps=20.0)
    thread = CameraThread(OpenCVCamera(vid))
    frames = []
    thread.frame_ready.connect(lambda f: frames.append(time.monotonic()))

    _run_until_eof(thread, qtbot)

    assert len(frames) == 20
    elapsed = frames[-1] - frames[0]
    assert 0.75 < elapsed < 1.3


def test_unpaced_playback_emits_video_timestamps(tmp_path, qtbot):
    vid = create_dummy_video(tmp_path / "recording_20260101_120000.mp4", num_frames=60, fps=10.0)
    thread = CameraThread(OpenCVCamera(vid))
    thread.set_playback_speed(0)
    stamps = []
    thread.frame_captured.connect(lambda f, ts: stamps.append(ts))

    t0 = time.monotonic()
    _run_until_eof(thread, qtbot)
    assert time.monotonic() - t0 < 3.0  # 6 s of video

    start = datetime(2026, 1, 1, 12, 0, 0).timestamp()
    assert len(stamps) == 60
    assert stamps[0] == pytest.approx(start)
    assert stamps[-1] == pytest.approx(start + 5.9)


def test_start_time_falls_back_to_mtime_minus_duration(tmp_path):
    vid = create_dummy_video(tmp_path / "clip.mp4", num_frames=30, fps=10.0)
    cam = OpenCVCamera(vid)
    assert cam.open()
    cam.read()
    mtime = (tmp_path / "clip.mp4").stat().st_mtime
    assert cam.frame_timestamp() == pytest.approx(mtime - 3.0)
    cam.release()


def _stub_worker(interval_ms=1000):
    worker = OCRWorker(interval_ms=interval_ms)
    worker.rois = [object()]
    worker.process_frame = lambda frame, rois, now=None: [
        {"roi_name": "w", "parsed_value": 1.0, "is_valid": True}
    ]
    return worker


def test_ocr_samples_on_video_time_in_unpaced_mode(tmp_path, qtbot):
    """At max speed, OCR still reads exactly one frame per interval of video time."""
    vid = create_dummy_video(tmp_path / "recording_20260101_120000.mp4", num_frames=60, fps=10.0)
    worker = _stub_worker()
    got = []
    worker.readings_ready.connect(lambda rs: got.append(rs[0]["timestamp"]))
    worker.start()
    qtbot.waitUntil(lambda: worker.running, timeout=2000)

    thread = CameraThread(OpenCVCamera(vid))
    thread.set_playback_speed(0)
    thread.frame_captured.connect(worker.update_frame, Qt.DirectConnection)
    thread.backpressure = worker.wait_while_pending
    _run_until_eof(thread, qtbot)
    qtbot.waitUntil(lambda: len(got) >= 6, timeout=2000)
    worker.stop()

    start = datetime(2026, 1, 1, 12, 0, 0).timestamp()
    assert [round(t - start, 3) for t in got] == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]


def test_ocr_does_not_reread_paused_frame(qtbot):
    worker = _stub_worker(interval_ms=100)
    got = []
    worker.readings_ready.connect(lambda rs: got.append(rs[0]["timestamp"]))
    worker.start()
    frame = np.zeros((4, 4, 3), dtype=np.uint8)
    worker.update_frame(frame, 100.0)
    qtbot.waitUntil(lambda: len(got) == 1, timeout=2000)
    qtbot.wait(300)  # same frame stays current, as when a video is paused
    assert got == [100.0]

    # Seeking backwards restarts the schedule
    worker.update_frame(frame, 50.0)
    qtbot.waitUntil(lambda: len(got) == 2, timeout=2000)
    worker.stop()
    assert got == [100.0, 50.0]


def test_db_writer_uses_reading_timestamp(tmp_path):
    writer = DatabaseWriter(db_path=str(tmp_path / "ts.db"))
    exp = writer.create_experiment("e", "voc", "")
    writer.create_run(exp)
    ts = datetime(2026, 1, 1, 12, 0, 5).timestamp()
    writer.insert_readings([{"roi_name": "w", "parsed_value": 1.0, "timestamp": ts}])
    writer.insert_readings([{"roi_name": "w", "parsed_value": 2.0}], timestamp=ts + 1.5)

    with sqlite3.connect(writer.db.db_path) as conn:
        rows = [r[0] for r in conn.execute("SELECT timestamp FROM readings ORDER BY id")]
    assert rows == ["2026-01-01T12:00:05", "2026-01-01T12:00:06.500000"]


class _FakeLiveCamera:
    """Live camera whose read() blocks until the next frame, like a real device."""

    fps = 50.0
    is_video_file = False
    is_opened = True

    def read(self):
        time.sleep(1.0 / self.fps)
        return True, np.zeros((4, 4, 3), dtype=np.uint8)

    def frame_timestamp(self):
        return time.time()

    def release(self):
        pass


def test_live_camera_is_not_slowed_by_extra_sleep(qtbot):
    thread = CameraThread(_FakeLiveCamera())
    frames = []
    thread.frame_ready.connect(lambda f: frames.append(f))
    thread.start()
    qtbot.wait(1000)
    thread.stop()
    # 50 fps camera: ~50 frames per second; the old extra 1/fps sleep gave ~25
    assert len(frames) >= 38
