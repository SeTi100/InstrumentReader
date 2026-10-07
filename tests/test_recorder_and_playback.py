import os
import time
from pathlib import Path
import cv2
import numpy as np
import pytest
from PySide6.QtWidgets import QMessageBox

from instrument_reader.core.camera import OpenCVCamera
from instrument_reader.core.recorder import VideoRecorder
from instrument_reader.gui.control_panel import ControlPanel
from instrument_reader.gui.main_window import MainWindow


def create_dummy_video(path: Path, num_frames: int = 30, fps: float = 10.0) -> str:
    """Helper to create a temporary test video file."""
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(path), fourcc, fps, (120, 90))
    for i in range(num_frames):
        frame = np.full((90, 120, 3), (i * 5) % 256, dtype=np.uint8)
        writer.write(frame)
    writer.release()
    return str(path)


# ---------------------------------------------------------------------------
# VideoRecorder Unit Tests
# ---------------------------------------------------------------------------

def test_video_recorder_lifecycle(tmp_path):
    rec = VideoRecorder(output_dir=tmp_path, fps=15.0, format="mp4")
    assert not rec.is_recording
    assert rec.current_file is None

    filepath = rec.start_recording()
    assert rec.is_recording
    assert os.path.exists(filepath) or Path(filepath).parent.exists()

    # Write frames
    for i in range(15):
        frame = np.full((100, 120, 3), i * 10, dtype=np.uint8)
        success = rec.write_frame(frame)
        assert success is True

    # Stop recording
    saved = rec.stop_recording()
    assert not rec.is_recording
    assert saved == filepath
    assert os.path.exists(saved)
    assert os.path.getsize(saved) > 0

    # Verify video readable by cv2
    cap = cv2.VideoCapture(saved)
    assert cap.isOpened()
    assert cap.get(cv2.CAP_PROP_FRAME_COUNT) >= 10
    cap.release()


def test_video_recorder_custom_filename_and_fps(tmp_path):
    rec = VideoRecorder(output_dir=tmp_path, fps=20.0, format="mp4")
    custom_name = "test_custom.mp4"
    filepath = rec.start_recording(fps=25.0, frame_size=(80, 60), filename=custom_name)
    assert Path(filepath).name == custom_name
    assert rec.fps == 25.0

    frame = np.zeros((60, 80, 3), dtype=np.uint8)
    assert rec.write_frame(frame) is True

    saved = rec.stop_recording()
    assert saved == filepath
    assert os.path.exists(saved)


def test_video_recorder_edge_cases(tmp_path):
    rec = VideoRecorder(output_dir=tmp_path)
    # Stop before start returns None
    assert rec.stop_recording() is None

    # Write frame when not recording returns False
    dummy = np.zeros((50, 50, 3), dtype=np.uint8)
    assert rec.write_frame(dummy) is False
    assert rec.write_frame(None) is False

    # Start recording
    p1 = rec.start_recording()
    # Calling start_recording again while already recording returns current file
    p2 = rec.start_recording()
    assert p1 == p2

    rec.stop_recording()


# ---------------------------------------------------------------------------
# Camera Seeking Unit Tests
# ---------------------------------------------------------------------------

def test_opencv_camera_video_file_detection_and_seeking(tmp_path):
    vid_path = tmp_path / "seek_test.mp4"
    create_dummy_video(vid_path, num_frames=50, fps=10.0)

    # Webcam indices
    cam_live0 = OpenCVCamera(0)
    assert cam_live0.is_video_file is False
    assert cam_live0.seek(5.0) is False

    cam_live_str = OpenCVCamera("0")
    assert cam_live_str.is_video_file is False
    assert cam_live_str.seek(5.0) is False

    # Video file
    cam_vid = OpenCVCamera(str(vid_path))
    assert cam_vid.is_video_file is True
    assert cam_vid.open() is True
    assert cam_vid.fps == 10.0

    # Initial frame
    ret, frame = cam_vid.read()
    assert ret is True
    assert frame is not None

    # Seek forward +2 seconds (+20 frames)
    current_pos = cam_vid.get_position_frames()
    ok = cam_vid.seek(2.0)
    assert ok is True
    new_pos = cam_vid.get_position_frames()
    assert new_pos >= current_pos + 15

    # Seek backward -1 second (-10 frames)
    ok = cam_vid.seek(-1.0)
    assert ok is True
    assert cam_vid.get_position_frames() < new_pos

    # Seek to specific frame
    assert cam_vid.seek_to_frame(5) is True
    assert cam_vid.get_position_frames() == 5.0

    # Large backward seek clamps to 0
    assert cam_vid.seek(-100.0) is True
    assert cam_vid.get_position_frames() == 0.0

    cam_vid.release()


# ---------------------------------------------------------------------------
# ControlPanel Playback Controls Tests
# ---------------------------------------------------------------------------

def test_control_panel_playback_controls_state(qtbot, monkeypatch):
    monkeypatch.setattr("instrument_reader.gui.control_panel.list_cameras", lambda: [])
    panel = ControlPanel()
    qtbot.addWidget(panel)

    # Initial state with live camera "0"
    assert panel.camera_source.text() == "0"
    assert panel.start_btn.text() == "Start / Play"
    assert panel.stop_btn.text() == "Stop / Pause"
    assert panel.record_btn.text() == "Record"
    assert panel.fwd_btn.isEnabled() is False
    assert panel.back_btn.isEnabled() is False
    assert panel.unlock_back_cb.isEnabled() is False
    assert panel.unlock_back_cb.isChecked() is False

    # Switch source to a video file
    panel.camera_source.setText("lab_experiment.mp4")
    assert panel.is_video_source() is True
    assert panel.fwd_btn.isEnabled() is True
    assert panel.unlock_back_cb.isEnabled() is True
    assert panel.back_btn.isEnabled() is False  # Locked by default

    # Switch source back to webcam index "1"
    panel.camera_source.setText("1")
    assert panel.is_video_source() is False
    assert panel.fwd_btn.isEnabled() is False
    assert panel.back_btn.isEnabled() is False
    assert panel.unlock_back_cb.isEnabled() is False


def test_control_panel_backwards_unlock_dialog(qtbot, monkeypatch):
    panel = ControlPanel()
    qtbot.addWidget(panel)
    panel.camera_source.setText("run.mp4")

    # When user cancels / rejects safety warning
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *args, **kwargs: QMessageBox.StandardButton.No
    )
    panel.unlock_back_cb.click()
    assert panel.unlock_back_cb.isChecked() is False
    assert panel.back_btn.isEnabled() is False

    # When user confirms safety warning
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *args, **kwargs: QMessageBox.StandardButton.Yes
    )
    panel.unlock_back_cb.click()
    assert panel.unlock_back_cb.isChecked() is True
    assert panel.back_btn.isEnabled() is True

    # When user unchecks unlock checkbox
    panel.unlock_back_cb.click()
    assert panel.unlock_back_cb.isChecked() is False
    assert panel.back_btn.isEnabled() is False

    # Programmatic unlock without prompt
    panel.unlock_backwards(prompt=False)
    assert panel.unlock_back_cb.isChecked() is True
    assert panel.back_btn.isEnabled() is True

    panel.lock_backwards()
    assert panel.unlock_back_cb.isChecked() is False
    assert panel.back_btn.isEnabled() is False


def test_control_panel_set_recording_toggle(qtbot):
    panel = ControlPanel()
    qtbot.addWidget(panel)

    assert panel.record_btn.text() == "Record"
    panel.set_recording(True)
    assert panel.record_btn.text() == "Stop Recording"
    panel.set_recording(False)
    assert panel.record_btn.text() == "Record"


# ---------------------------------------------------------------------------
# MainWindow Integration Tests (Seek clears buffer & step detector)
# ---------------------------------------------------------------------------

def test_main_window_seek_clears_calculation_history_and_step_detector(tmp_path, qtbot):
    db_file = tmp_path / "main_seek.db"
    win = MainWindow()
    qtbot.addWidget(win)
    win.db_writer.db.db_path = str(db_file)
    win.db_writer.db._init_db()

    # Populate calculation engine history buffer
    win.calc_engine.add_reading("Waage", 120.5, timestamp=10.0)
    win.calc_engine.add_reading("Waage", 120.4, timestamp=11.0)
    win.calc_engine.add_reading("Temperatur", 45.0, timestamp=11.0)
    assert len(win.calc_engine.history) >= 2
    assert len(win.calc_engine.history["Waage"]) == 2

    # Simulate step detector at stage 3
    win.calc_engine.step_detector.stage = 3
    win.control_panel.set_stage_status(3, "Stationär")
    win.db_writer.set_phase("STAGE_3")

    # Perform seek (forward or backward)
    win.seek_video(5.0)

    # Verify history buffer is completely cleared
    assert len(win.calc_engine.history) == 0

    # Verify scale detector is reset to stage 1
    assert win.calc_engine.step_detector.stage == 1
    assert "Stufe 1" in win.control_panel.stage_badge.text()
    assert win.db_writer.current_phase == "STAGE_1"


def test_main_window_recording_toggle_and_close(tmp_path, qtbot):
    db_file = tmp_path / "main_rec.db"
    win = MainWindow()
    qtbot.addWidget(win)
    win.db_writer.db.db_path = str(db_file)
    win.db_writer.db._init_db()

    win.recorder.output_dir = tmp_path

    assert win.recorder.is_recording is False
    assert win.control_panel.record_btn.text() == "Record"

    # Start recording
    win.toggle_recording()
    assert win.recorder.is_recording is True
    assert win.control_panel.record_btn.text() == "Stop Recording"

    # Push a frame
    win.recorder.write_frame(np.zeros((60, 80, 3), dtype=np.uint8))

    # Stop recording via toggle
    win.toggle_recording()
    assert win.recorder.is_recording is False
    assert win.control_panel.record_btn.text() == "Record"

    # Start recording again and test closeEvent stops it cleanly
    win.toggle_recording()
    assert win.recorder.is_recording is True
    win.close()
    assert win.recorder.is_recording is False


def test_video_recorder_mkv_format(tmp_path):
    rec = VideoRecorder(output_dir=tmp_path, fps=10.0, format="mkv")
    filepath = rec.start_recording()
    assert filepath.endswith(".mkv")
    for _ in range(5):
        rec.write_frame(np.zeros((40, 40, 3), dtype=np.uint8))
    saved = rec.stop_recording()
    assert saved == filepath
    assert os.path.exists(saved)


def test_camera_thread_seek_and_pause(tmp_path, qtbot):
    from instrument_reader.gui.main_window import CameraThread

    vid_path = tmp_path / "thread_seek.mp4"
    create_dummy_video(vid_path, num_frames=30, fps=10.0)

    cam = OpenCVCamera(str(vid_path))
    thread = CameraThread(cam)

    frames_received = []
    thread.frame_ready.connect(lambda f: frames_received.append(f))

    thread.start()
    qtbot.waitUntil(lambda: len(frames_received) > 0, timeout=2000)

    # Pause playback
    thread.pause()
    assert thread.paused is True

    # Seek while paused
    count_before = len(frames_received)
    thread.seek(1.0)
    # Seeking while paused should emit a frame immediately
    qtbot.waitUntil(lambda: len(frames_received) > count_before, timeout=2000)

    # Resume playback
    thread.resume()
    assert thread.paused is False

    thread.stop()
    assert thread.running is False


def test_main_window_full_playback_lifecycle(tmp_path, qtbot, monkeypatch):
    db_file = tmp_path / "lifecycle.db"
    vid_path = tmp_path / "lifecycle_vid.mp4"
    create_dummy_video(vid_path, num_frames=60, fps=15.0)

    win = MainWindow()
    qtbot.addWidget(win)
    win.db_writer.db.db_path = str(db_file)
    win.db_writer.db._init_db()

    win.control_panel.camera_source.setText(str(vid_path))
    assert win.control_panel.is_video_source() is True

    # 1. Start playback
    win.control_panel.start_btn.click()
    assert win.camera_thread is not None
    qtbot.waitUntil(lambda: win.camera_thread.running, timeout=2000)
    qtbot.waitUntil(lambda: win.video_widget.current_frame is not None, timeout=2000)

    # 2. Pause playback
    win.control_panel.stop_btn.click()
    assert win.camera_thread.paused is True

    # 3. Seek forward while paused
    win.calc_engine.add_reading("Waage", 100.0, timestamp=1.0)
    win.control_panel.fwd_btn.click()
    assert len(win.calc_engine.history) == 0

    # 4. Unlock backwards and seek backward
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *args, **kwargs: QMessageBox.StandardButton.Yes
    )
    win.control_panel.unlock_back_cb.click()
    assert win.control_panel.back_btn.isEnabled() is True
    win.control_panel.back_btn.click()
    assert len(win.calc_engine.history) == 0

    # 5. Resume playback
    win.control_panel.start_btn.click()
    assert win.camera_thread.paused is False

    # 6. Stop playback completely (first stop pauses, second stop fully terminates thread)
    win.control_panel.stop_btn.click()
    assert win.camera_thread.paused is True
    win.control_panel.stop_btn.click()
    assert win.camera_thread is None

    # 7. Seek while stopped (reopens video and updates frame)
    win.control_panel.fwd_btn.click()
    assert win.video_widget.current_frame is not None
    assert win.camera is not None
    assert win.camera.is_opened is True

    # Clean close
    win.close()
    assert win.camera_thread is None


def test_main_window_video_replay_at_eof(tmp_path, qtbot):
    db_file = tmp_path / "replay_eof.db"
    vid_path = tmp_path / "eof_vid.mp4"
    create_dummy_video(vid_path, num_frames=10, fps=20.0)

    win = MainWindow()
    qtbot.addWidget(win)
    win.db_writer.db.db_path = str(db_file)
    win.db_writer.db._init_db()

    win.control_panel.camera_source.setText(str(vid_path))
    win.control_panel.start_btn.click()

    # Wait for thread to reach EOF and automatically pause
    qtbot.waitUntil(lambda: win.camera_thread and win.camera_thread.paused, timeout=3000)
    assert win.camera.is_eof() is True

    # Click start / play when at EOF -> rewinds to 0 and resumes playback
    win.control_panel.start_btn.click()
    assert win.camera_thread.paused is False
    assert win.camera.get_position_frames() < 10.0

    win.close()


def test_video_recorder_grayscale_and_collision_and_dots(tmp_path):
    # Test dot stripping in format
    rec = VideoRecorder(output_dir=tmp_path, format=".mp4")
    assert rec.format == "mp4"

    # Test collision avoidance in same second
    p1 = rec.start_recording()
    rec.write_frame(np.zeros((60, 60, 3), dtype=np.uint8))
    rec.stop_recording()
    assert os.path.exists(p1)

    # Re-recording with fresh recorder in same second generates unique file
    rec2 = VideoRecorder(output_dir=tmp_path, format="mp4")
    p2 = rec2.start_recording()
    rec2.stop_recording()
    assert p1 != p2
    assert "_1.mp4" in p2

    # Test grayscale frame writing (2D array converted to BGR internally)
    rec3 = VideoRecorder(output_dir=tmp_path, format="mp4")
    f_path = rec3.start_recording()
    gray_frame = np.ones((80, 80), dtype=np.uint8) * 128
    assert rec3.write_frame(gray_frame) is True
    saved = rec3.stop_recording()
    assert saved == f_path
    assert os.path.exists(saved)


def test_camera_source_polymorphic_defaults():
    class DummyCamera(OpenCVCamera):
        def __init__(self):
            super().__init__(0)

    dummy = DummyCamera()
    assert dummy.is_opened is False
    assert dummy.is_eof() is False
    assert dummy.get_position_frames() == 0.0
    assert dummy.get_frame_count() == 0.0
    assert dummy.seek_to_frame(5) is False


