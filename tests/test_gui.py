import pytest
import numpy as np
from PySide6.QtCore import Qt, QPoint, QPointF
from PySide6.QtGui import QWheelEvent
from instrument_reader.core.roi import ROIConfig, ROIShape, DisplayType
from instrument_reader.core.preprocessing import PreprocessingConfig
from instrument_reader.gui.roi_config_dialog import ROIConfigDialog
from instrument_reader.gui.preprocessing_dialog import PreprocessingDialog
from instrument_reader.gui.video_widget import VideoWidget

def test_roi_config_dialog(qtbot):
    roi = ROIConfig(name="Thermometer", coordinates=[10, 20, 100, 50], unit="°C")
    dlg = ROIConfigDialog(roi)
    qtbot.addWidget(dlg)
    
    dlg.name_edit.setText("Thermo_1")
    dlg.is_container_cb.setChecked(True)
    dlg.sort_combo.setCurrentIndex(1)  # rtl
    dlg.decimal_pos_spin.setValue(2)
    dlg.cal_mark_cb.setChecked(True)
    dlg.analog_val_spin.setValue(45.5)
    
    updated = dlg.get_data()
    assert updated.name == "Thermo_1"
    assert updated.is_container is True
    assert updated.sort_direction == "rtl"
    assert updated.decimal_position == 2
    assert updated.analog_value == 45.5

def test_preprocessing_dialog(qtbot):
    crop = np.ones((100, 100, 3), dtype=np.uint8) * 150
    config = PreprocessingConfig(grayscale=True)
    
    dlg = PreprocessingDialog(crop, config)
    qtbot.addWidget(dlg)
    
    # Test mask drawing
    dlg.mask_color_combo.setCurrentIndex(0)  # Black
    dlg.on_mask_drawn(10, 10, 30, 30)
    assert len(dlg.config.masks) == 1
    assert dlg.config.masks[0]["color"] == 0
    assert dlg.config.masks[0]["coords"] == [10, 10, 30, 30]
    
    # Test mask list selection and deletion
    dlg.mask_list.setCurrentRow(0)
    dlg.delete_selected_mask()
    assert len(dlg.config.masks) == 0
    
    # Test rotameter float display
    dlg.show_float_cb.setChecked(True)
    dlg.update_preview()
    
    # Test zoom wheel event on view
    wheel_event = QWheelEvent(
        QPointF(50, 50), QPointF(50, 50),
        QPoint(0, 0), QPoint(0, 120),
        Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False
    )
    dlg.view.wheelEvent(wheel_event)
    
    cfg = dlg.get_config()
    assert isinstance(cfg, PreprocessingConfig)

def test_video_widget_rendering(qtbot):
    widget = VideoWidget()
    qtbot.addWidget(widget)
    
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    container = ROIConfig(name="Scale", is_container=True, coordinates=[50, 50, 200, 100])
    slot = ROIConfig(name="S1", coordinates=[60, 60, 40, 60])
    widget.rois = [container, slot]
    
    readings = [
        {"roi_name": "Scale", "parsed_value": 154.3, "unit": "g", "is_valid": True, "is_child": False},
        {"roi_name": "S1", "parsed_value": 1.0, "unit": "", "is_valid": True, "is_child": True, "slot_index": 1, "raw_text": "1"}
    ]
    
    widget.update_frame(frame)
    widget.update_readings(readings)
    # Ensure display rendered pixmap
    assert widget.pixmap() is not None
    assert not widget.pixmap().isNull()

def test_roi_config_dialog_with_sub_rois_and_options(qtbot):
    container = ROIConfig(name="Box", is_container=True)
    r1 = ROIConfig(name="Digit_1")
    r2 = ROIConfig(name="Digit_2")
    all_rois = [container, r1, r2]
    
    dlg = ROIConfigDialog(container, all_rois=all_rois)
    qtbot.addWidget(dlg)
    
    # Check Digit_1 in sub_roi_list
    assert dlg.sub_roi_list is not None
    assert dlg.sub_roi_list.count() == 2
    item1 = dlg.sub_roi_list.item(0)
    assert item1.text() == "Digit_1"
    item1.setCheckState(Qt.Checked)
    
    dlg.allow_blank_cb.setChecked(True)
    dlg.allow_neg_cb.setChecked(False)
    
    updated = dlg.get_data()
    assert updated.sub_roi_ids == ["Digit_1"]
    assert updated.allow_leading_blank is True
    assert updated.allow_negative is False

def test_preprocessing_dialog_mask_selection_and_clamping(qtbot):
    crop = np.ones((100, 100, 3), dtype=np.uint8) * 150
    config = PreprocessingConfig(grayscale=True)
    dlg = PreprocessingDialog(crop, config)
    qtbot.addWidget(dlg)
    
    # Test clamped mask coordinates starting negative
    dlg.on_mask_drawn(-10, -5, 40, 30)
    assert len(dlg.config.masks) == 1
    # Clamped x1=0, y1=0, x2=30, y2=25 -> cw=30, ch=25
    assert dlg.config.masks[0]["coords"] == [0, 0, 30, 25]
    
    # Add second mask and test selection
    dlg.on_mask_drawn(50, 50, 20, 20)
    assert len(dlg.config.masks) == 2
    dlg.mask_list.setCurrentRow(1)
    
    # Empty crop safety
    empty_dlg = PreprocessingDialog(np.zeros((0, 0), dtype=np.uint8), config)
    qtbot.addWidget(empty_dlg)
    empty_dlg.update_preview()  # Should not raise exception

def test_video_widget_rotameter_float_line(qtbot):
    widget = VideoWidget()
    qtbot.addWidget(widget)
    
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    rotameter = ROIConfig(
        name="Flow",
        is_container=True,
        display_type=DisplayType.ANALOG,
        coordinates=[100, 50, 60, 300]
    )
    widget.rois = [rotameter]
    readings = [{
        "roi_name": "Flow",
        "parsed_value": 250.0,
        "unit": "Nl/h",
        "is_valid": True,
        "is_child": False,
        "y_float": 150.0
    }]
    widget.update_frame(frame)
    widget.update_readings(readings)
    assert widget.pixmap() is not None
