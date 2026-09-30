import pytest
from unittest.mock import patch
from PySide6.QtWidgets import QMessageBox, QInputDialog
from instrument_reader.core.roi import ROIConfig, ROIShape, DisplayType
from instrument_reader.gui.main_window import MainWindow
from instrument_reader.gui.roi_config_dialog import ROIConfigDialog
from instrument_reader.gui.ocr_worker import OCRWorker
import numpy as np

def test_roi_config_unique_ids():
    roi1 = ROIConfig(name="Sensor_A")
    roi2 = ROIConfig(name="Sensor_B")
    roi3 = ROIConfig(name="Sensor_A")  # same name, different instance
    assert roi1.id != roi2.id
    assert roi1.id != roi3.id
    assert len(roi1.id) > 10

def test_main_window_monotonic_naming(qtbot):
    win = MainWindow()
    qtbot.addWidget(win)

    # Initial state
    assert win._roi_counter == 1

    # Simulate creating ROI with default name
    roi1 = ROIConfig(name="", coordinates=[10, 10, 50, 50])
    win.video_widget.rois.append(roi1)
    with patch.object(QInputDialog, "getText", return_value=("ROI_1", True)):
        win.on_roi_created(roi1)

    assert roi1.name == "ROI_1"
    assert win._roi_counter == 2
    assert len(win.video_widget.rois) == 1

    # Simulate creating second ROI with default name
    roi2 = ROIConfig(name="", coordinates=[70, 10, 50, 50])
    win.video_widget.rois.append(roi2)
    with patch.object(QInputDialog, "getText", return_value=("ROI_2", True)):
        win.on_roi_created(roi2)

    assert roi2.name == "ROI_2"
    assert win._roi_counter == 3

    # Delete ROI_1
    win.control_panel.roi_list.setCurrentRow(0)
    win.delete_roi()
    assert len(win.video_widget.rois) == 1
    # Counter must NOT decrement (stays monotonic)
    assert win._roi_counter == 3

    # Create third ROI: default suggested name should be ROI_3, never colliding with ROI_2
    roi3 = ROIConfig(name="", coordinates=[150, 10, 50, 50])
    win.video_widget.rois.append(roi3)
    with patch.object(QInputDialog, "getText", return_value=("ROI_3", True)):
        win.on_roi_created(roi3)

    assert roi3.name == "ROI_3"
    assert win._roi_counter == 4

def test_main_window_duplicate_name_rejection(qtbot):
    win = MainWindow()
    qtbot.addWidget(win)

    roi1 = ROIConfig(name="Temperature", coordinates=[10, 10, 50, 50])
    win.video_widget.rois.append(roi1)
    win.control_panel.add_roi(roi1)

    # Attempt to create roi2 with duplicate name "temperature" (case-insensitive)
    # First attempt: "TEMPERATURE" (rejected), Second attempt: "Pressure" (accepted)
    roi2 = ROIConfig(name="", coordinates=[70, 10, 50, 50])
    win.video_widget.rois.append(roi2)

    input_calls = [("TEMPERATURE", True), ("Pressure", True)]
    warning_calls = []

    def mock_get_text(*args, **kwargs):
        return input_calls.pop(0)

    def mock_warning(*args, **kwargs):
        warning_calls.append(args)

    with patch.object(QInputDialog, "getText", side_effect=mock_get_text), \
         patch.object(QMessageBox, "warning", side_effect=mock_warning):
        win.on_roi_created(roi2)

    assert len(warning_calls) == 1
    assert roi2.name == "Pressure"
    assert len(win.video_widget.rois) == 2

def test_main_window_cancel_roi_creation(qtbot):
    win = MainWindow()
    qtbot.addWidget(win)

    roi1 = ROIConfig(name="", coordinates=[10, 10, 50, 50])
    win.video_widget.rois.append(roi1)

    # User clicks Cancel
    with patch.object(QInputDialog, "getText", return_value=("", False)):
        win.on_roi_created(roi1)

    # ROI must be removed from video_widget.rois
    assert roi1 not in win.video_widget.rois
    assert len(win.video_widget.rois) == 0

def test_roi_config_dialog_duplicate_name_prevention(qtbot):
    r1 = ROIConfig(name="Flow_1", coordinates=[0, 0, 50, 50])
    r2 = ROIConfig(name="Flow_2", coordinates=[60, 0, 50, 50])
    all_rois = [r1, r2]

    dlg = ROIConfigDialog(r2, all_rois=all_rois)
    qtbot.addWidget(dlg)

    # 1. Renaming to existing ROI "flow_1" must be rejected
    dlg.name_edit.setText("flow_1")
    warning_called = False
    def mock_warn(*args, **kwargs):
        nonlocal warning_called
        warning_called = True

    with patch.object(QMessageBox, "warning", side_effect=mock_warn):
        dlg.accept()
        assert warning_called is True
        assert not dlg.result()  # Dialog must not accept

    # 2. Renaming to its own name "Flow_2" must be accepted
    dlg.name_edit.setText("Flow_2")
    dlg.accept()
    assert dlg.result() == 1  # Accepted

def test_ocr_worker_caches_isolated_by_id():
    worker = OCRWorker()
    frame = np.ones((100, 100, 3), dtype=np.uint8) * 128

    # Create two ROIs with different IDs
    roi_a = ROIConfig(name="Meter_A", coordinates=[10, 10, 30, 30])
    roi_b = ROIConfig(name="Meter_B", coordinates=[50, 10, 30, 30])

    worker.last_valid_values[roi_a.id] = 123.45
    worker.last_valid_values[roi_b.id] = 678.90

    # Ensure their caches do not overwrite each other
    assert worker.last_valid_values[roi_a.id] == 123.45
    assert worker.last_valid_values[roi_b.id] == 678.90
    assert roi_a.id != roi_b.id

def test_delete_roi_clears_live_readings_immediately(qtbot):
    win = MainWindow()
    qtbot.addWidget(win)

    roi1 = ROIConfig(name="Sensor_1", coordinates=[0, 0, 50, 50])
    roi2 = ROIConfig(name="Sensor_2", coordinates=[60, 0, 50, 50])
    win.video_widget.rois = [roi1, roi2]
    win.control_panel.add_roi(roi1)
    win.control_panel.add_roi(roi2)

    # Populate live readings
    readings = [
        {"roi_id": roi1.id, "roi_name": "Sensor_1", "parsed_value": 42.0, "unit": "g", "confidence": 0.9, "is_valid": True},
        {"roi_id": roi2.id, "roi_name": "Sensor_2", "parsed_value": 84.0, "unit": "g", "confidence": 0.9, "is_valid": True},
    ]
    win.video_widget.update_readings(readings)
    win.control_panel.update_readings(readings)
    assert win.control_panel.readings_table.rowCount() == 2
    assert len(win.video_widget.latest_readings) == 2

    # Delete Sensor_1
    win.control_panel.roi_list.setCurrentRow(0)
    win.delete_roi()

    # Sensor_1 must be immediately removed from both video_widget and control_panel readings table
    assert win.control_panel.readings_table.rowCount() == 1
    assert win.control_panel.readings_table.item(0, 0).text() == "Sensor_2"
    assert len(win.video_widget.latest_readings) == 1
    assert win.video_widget.latest_readings[0]["roi_name"] == "Sensor_2"

def test_rename_roi_updates_container_sub_ids_and_readings(qtbot):
    win = MainWindow()
    qtbot.addWidget(win)

    child = ROIConfig(name="Digit_1", coordinates=[10, 10, 20, 20])
    container = ROIConfig(name="Display", is_container=True, sub_roi_ids=["Digit_1"])
    win.video_widget.rois = [child, container]
    win.control_panel.add_roi(child)
    win.control_panel.add_roi(container)

    readings = [
        {"roi_id": child.id, "roi_name": "Digit_1", "parsed_value": 1.0, "unit": "", "confidence": 0.9, "is_valid": True},
        {"roi_id": container.id, "roi_name": "Display", "parsed_value": 1.0, "unit": "", "confidence": 0.9, "is_valid": True},
    ]
    win.video_widget.update_readings(readings)
    win.control_panel.update_readings(readings)

    # Double-click child to rename Digit_1 -> Slot_1
    with patch.object(ROIConfigDialog, "exec", return_value=True), \
         patch.object(ROIConfigDialog, "get_data", return_value=ROIConfig(
             id=child.id, name="Slot_1", coordinates=child.coordinates
         )):
        item = win.control_panel.roi_list.item(0)
        win.on_roi_double_clicked(item)

    # 1. Container sub_roi_ids must be updated to Slot_1
    assert "Slot_1" in container.sub_roi_ids
    assert "Digit_1" not in container.sub_roi_ids

    # 2. Control panel readings table must reflect Slot_1
    assert win.control_panel.readings_table.item(0, 0).text() == "Slot_1"

def test_video_widget_monotonic_naming(qtbot):
    from instrument_reader.gui.video_widget import VideoWidget
    widget = VideoWidget()
    qtbot.addWidget(widget)

    # Draw rectangle 1
    name1 = widget._get_next_default_name()
    assert name1 == "ROI_1"
    roi1 = ROIConfig(name=name1)
    widget.rois.append(roi1)

    # Draw rectangle 2
    name2 = widget._get_next_default_name()
    assert name2 == "ROI_2"
    roi2 = ROIConfig(name=name2)
    widget.rois.append(roi2)

    # Delete ROI_1
    widget.rois.remove(roi1)

    # Next name must be ROI_3, never reusing ROI_2
    name3 = widget._get_next_default_name()
    assert name3 == "ROI_3"
