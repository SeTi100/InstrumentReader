import pytest
import math
import numpy as np
import cv2
from unittest.mock import patch
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QMessageBox
from instrument_reader.core.roi import ROIConfig, ROIShape, DisplayType
from instrument_reader.core.analog_reader import RotameterRulerReader
from instrument_reader.gui.ocr_worker import OCRWorker
from instrument_reader.gui.roi_config_dialog import ROIConfigDialog
from instrument_reader.gui.video_widget import VideoWidget
from instrument_reader.gui.preprocessing_dialog import PreprocessingDialog
from instrument_reader.core.preprocessing import PreprocessingConfig

def create_synthetic_tilted_rotameter(
    angle_deg: float,
    length: float = 160.0,
    width: float = 30.0,
    float_rel_pos: float = 0.4,
    float_thickness: float = 30.0,
    img_size: int = 400
):
    """
    Creates an image with a rotameter tube tilted by angle_deg from the vertical down direction (+y).
    Returns (image, p1, p2).
    """
    image = np.ones((img_size, img_size, 3), dtype=np.uint8) * 220
    
    # Angle theta measured from vertical down (+y axis):
    # theta=0 -> straight down: u = (0, 1)
    # theta=90 -> straight right: u = (1, 0)
    theta = math.radians(angle_deg)
    ux = math.sin(theta)
    uy = math.cos(theta)
    
    # Center of ruler at (img_size/2, img_size/2)
    cx, cy = img_size / 2.0, img_size / 2.0
    p1 = (cx - (length / 2.0) * ux, cy - (length / 2.0) * uy)
    p2 = (cx + (length / 2.0) * ux, cy + (length / 2.0) * uy)
    
    # Draw tube and float by creating a straight strip and warping it into the image
    H = int(round(length))
    W = int(round(width))
    strip = np.ones((H, W, 3), dtype=np.uint8) * 220
    
    # Place float inside strip
    fy_start = int(round(float_rel_pos * (H - 1)))
    fy_end = min(H, fy_start + int(float_thickness))
    strip[fy_start:fy_end, :] = 35
    
    # Map strip into image
    n = np.array([-uy, ux], dtype=np.float32)
    u = np.array([ux, uy], dtype=np.float32)
    p1_arr = np.array(p1, dtype=np.float32)
    p2_arr = np.array(p2, dtype=np.float32)
    cx_rect = (W - 1) / 2.0
    
    pt0_frame = p1_arr - cx_rect * n
    pt1_frame = p1_arr + (float(W - 1) - cx_rect) * n
    pt2_frame = p2_arr - cx_rect * n
    
    src_tri = np.float32([[0.0, 0.0], [float(W - 1), 0.0], [0.0, float(H - 1)]])
    dst_tri = np.float32([pt0_frame, pt1_frame, pt2_frame])
    
    M = cv2.getAffineTransform(src_tri, dst_tri)
    cv2.warpAffine(strip, M, (img_size, img_size), dst=image, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_TRANSPARENT)
    
    return image, p1, p2

@pytest.mark.parametrize("angle", [0.0, 15.0, 45.0, -30.0])
def test_affine_rectification_and_float_detection_tilted_angles(angle):
    target_pos = 0.40
    frame, p1, p2 = create_synthetic_tilted_rotameter(
        angle_deg=angle,
        length=160.0,
        width=30.0,
        float_rel_pos=target_pos,
        img_size=400
    )
    
    reader = RotameterRulerReader(float_color="dark")
    rectified = reader.extract_rectified_strip(frame, p1, p2, strip_width=30.0)
    assert rectified is not None
    assert rectified.shape[0] == 160
    assert rectified.shape[1] == 30
    
    y_float = reader.detect_float_y(rectified)
    assert y_float is not None
    
    detected_pos = y_float / (rectified.shape[0] - 1)
    # Verify detection within 5% tolerance across all rotation angles
    assert abs(detected_pos - target_pos) <= 0.05

def test_ruler_interpolation_two_marks():
    marks = [
        {"pos": 0.0, "value": 100.0},
        {"pos": 1.0, "value": 800.0}
    ]
    # Exact boundary points
    assert RotameterRulerReader.interpolate_ruler_value(0.0, marks) == 100.0
    assert RotameterRulerReader.interpolate_ruler_value(1.0, marks) == 800.0
    # Midpoint
    assert RotameterRulerReader.interpolate_ruler_value(0.5, marks) == 450.0
    # Extrapolation below
    assert RotameterRulerReader.interpolate_ruler_value(-0.1, marks) == pytest.approx(30.0)
    # Extrapolation above
    assert RotameterRulerReader.interpolate_ruler_value(1.1, marks) == pytest.approx(870.0)

def test_ruler_interpolation_inverted_scale():
    # Scale decreases from top to bottom
    marks = [
        {"pos": 0.0, "value": 800.0},
        {"pos": 1.0, "value": 100.0}
    ]
    assert RotameterRulerReader.interpolate_ruler_value(0.0, marks) == 800.0
    assert RotameterRulerReader.interpolate_ruler_value(0.5, marks) == 450.0
    assert RotameterRulerReader.interpolate_ruler_value(1.0, marks) == 100.0

def test_ruler_interpolation_multiple_marks():
    # Non-linear 5-point calibration
    marks = [
        {"pos": 0.0, "value": 100.0},
        {"pos": 0.25, "value": 200.0},
        {"pos": 0.50, "value": 350.0},
        {"pos": 0.75, "value": 550.0},
        {"pos": 1.0, "value": 800.0}
    ]
    # Intermediate interpolation within segment [0.25, 0.50]
    val_mid = RotameterRulerReader.interpolate_ruler_value(0.375, marks)
    assert val_mid == pytest.approx(275.0)

    # Edge cases: None if rel_pos is None, empty marks, or fewer than 2 distinct marks
    assert RotameterRulerReader.interpolate_ruler_value(None, marks) is None
    assert RotameterRulerReader.interpolate_ruler_value(0.5, []) is None
    assert RotameterRulerReader.interpolate_ruler_value(0.5, [{"pos": 0.5, "value": 42.0}]) is None
    assert RotameterRulerReader.interpolate_ruler_value(0.5, [{"pos": 0.5, "value": 42.0}, {"pos": 0.5, "value": 50.0}]) is None

def test_compute_edge_points_perpendicularity():
    p1 = (100.0, 100.0)
    p2 = (200.0, 200.0)  # 45 deg tilt
    strip_width = 40.0
    rel_pos = 0.5

    e1, e2 = RotameterRulerReader.compute_edge_points(p1, p2, strip_width, rel_pos)
    
    # 1. Edge segment vector:
    ev = np.array([e2[0] - e1[0], e2[1] - e1[1]])
    edge_len = float(np.linalg.norm(ev))
    assert edge_len == pytest.approx(strip_width, abs=1e-3)
    
    # 2. Axis vector:
    av = np.array([p2[0] - p1[0], p2[1] - p1[1]])
    
    # 3. Orthogonality (dot product == 0):
    dot_prod = float(np.dot(ev, av))
    assert abs(dot_prod) < 1e-4

    # 4. Midpoint matches p1 + rel_pos * (p2 - p1):
    mid_x = (e1[0] + e2[0]) / 2.0
    mid_y = (e1[1] + e2[1]) / 2.0
    expected_center = (p1[0] + rel_pos * av[0], p1[1] + rel_pos * av[1])
    assert mid_x == pytest.approx(expected_center[0], abs=1e-3)
    assert mid_y == pytest.approx(expected_center[1], abs=1e-3)

def test_ocr_worker_process_frame_ruler_end_to_end():
    worker = OCRWorker()
    target_pos = 0.40
    frame, p1, p2 = create_synthetic_tilted_rotameter(
        angle_deg=20.0,
        length=200.0,
        width=30.0,
        float_rel_pos=target_pos,
        img_size=500
    )

    ruler_roi = ROIConfig(
        name="Rotameter_Ruler",
        shape=ROIShape.RULER,
        display_type=DisplayType.ANALOG,
        coordinates=[list(p1), list(p2)],
        strip_width=30.0,
        calibration_marks=[
            {"pos": 0.0, "value": 100.0},
            {"pos": 1.0, "value": 900.0}
        ],
        unit="Nl/h",
        decimal_places=1
    )

    readings = worker.process_frame(frame, [ruler_roi])
    assert len(readings) == 1
    r = readings[0]
    assert r["roi_id"] == ruler_roi.id
    assert r["roi_name"] == "Rotameter_Ruler"
    assert r["is_valid"] is True
    assert r["unit"] == "Nl/h"
    assert r["edge_pts"] is not None
    # Upper edge (top) is at rel_pos ~0.55 -> value ~ 100 + 0.55 * 800 = 540 Nl/h
    assert abs(r["parsed_value"] - 540.0) <= 40.0

    # Lower edge (bottom) is at rel_pos ~0.40 -> value ~ 100 + 0.40 * 800 = 420 Nl/h
    ruler_roi.ruler_edge = "bottom"
    readings_bottom = worker.process_frame(frame, [ruler_roi])
    assert abs(readings_bottom[0]["parsed_value"] - 420.0) <= 40.0

    # Center is at rel_pos ~0.475 -> value ~ 100 + 0.475 * 800 = 480 Nl/h
    ruler_roi.ruler_edge = "center"
    readings_center = worker.process_frame(frame, [ruler_roi])
    assert abs(readings_center[0]["parsed_value"] - 480.0) <= 40.0

def test_video_widget_ruler_rendering(qtbot):
    widget = VideoWidget()
    qtbot.addWidget(widget)

    frame = np.ones((480, 640, 3), dtype=np.uint8) * 100
    ruler = ROIConfig(
        name="Flow_Ruler",
        shape=ROIShape.RULER,
        coordinates=[[100, 100], [200, 350]],
        strip_width=35.0,
        calibration_marks=[
            {"pos": 0.0, "value": 50.0},
            {"pos": 0.5, "value": 250.0},
            {"pos": 1.0, "value": 500.0}
        ],
        unit="Nl/h"
    )
    widget.rois = [ruler]
    e1, e2 = RotameterRulerReader.compute_edge_points([100, 100], [200, 350], 35.0, 0.45)
    readings = [{
        "roi_id": ruler.id,
        "roi_name": "Flow_Ruler",
        "parsed_value": 225.0,
        "unit": "Nl/h",
        "is_valid": True,
        "is_child": False,
        "rel_pos": 0.45,
        "edge_pts": (e1, e2),
        "confidence": 0.95
    }]
    widget.update_frame(frame)
    widget.update_readings(readings)
    assert widget.pixmap() is not None
    assert not widget.pixmap().isNull()

def test_roi_config_dialog_ruler_ui(qtbot):
    ruler = ROIConfig(
        name="Ruler_Scale",
        shape=ROIShape.RULER,
        coordinates=[[50, 50], [50, 250]],
        strip_width=40.0,
        calibration_marks=[
            {"pos": 0.0, "value": 100.0},
            {"pos": 1.0, "value": 800.0}
        ]
    )
    dlg = ROIConfigDialog(ruler)
    qtbot.addWidget(dlg)

    assert dlg.ruler_group is not None
    assert dlg.strip_width_spin.value() == 40.0
    assert dlg.marks_table.rowCount() == 2

    # Add mark
    dlg._add_mark()
    assert dlg.marks_table.rowCount() == 3

    # Edit mark values
    dlg.strip_width_spin.setValue(55.0)
    dlg.marks_table.item(2, 0).setText("0.5")
    dlg.marks_table.item(2, 1).setText("450.0")

    updated = dlg.get_data()
    assert updated.strip_width == 55.0
    assert len(updated.calibration_marks) == 3
    assert any(m["pos"] == 0.5 and m["value"] == 450.0 for m in updated.calibration_marks)

def test_roi_config_dialog_ruler_minimum_two_marks(qtbot):
    ruler = ROIConfig(
        name="Ruler_Scale",
        shape=ROIShape.RULER,
        coordinates=[[50, 50], [50, 250]],
        calibration_marks=[{"pos": 0.0, "value": 100.0}]  # Only 1 mark
    )
    dlg = ROIConfigDialog(ruler)
    qtbot.addWidget(dlg)

    warning_called = False
    def mock_warn(*args, **kwargs):
        nonlocal warning_called
        warning_called = True

    with patch.object(QMessageBox, "warning", side_effect=mock_warn):
        dlg.accept()
        assert warning_called is True
        assert not dlg.result()

def test_roi_config_dialog_ruler_duplicate_positions_rejected(qtbot):
    ruler = ROIConfig(
        name="Ruler_Scale",
        shape=ROIShape.RULER,
        coordinates=[[50, 50], [50, 250]],
        calibration_marks=[
            {"pos": 0.0, "value": 100.0},
            {"pos": 0.0, "value": 200.0}  # Both marks at pos 0.0
        ]
    )
    dlg = ROIConfigDialog(ruler)
    qtbot.addWidget(dlg)

    warning_called = False
    def mock_warn(*args, **kwargs):
        nonlocal warning_called
        warning_called = True

    with patch.object(QMessageBox, "warning", side_effect=mock_warn):
        dlg.accept()
        assert warning_called is True
        assert not dlg.result()

def test_ruler_drawing_direction_symmetry():
    # Verify that drawing bottom-to-top and top-to-bottom yields identical float edge position
    # and reading value
    frame = np.ones((400, 400, 3), dtype=np.uint8) * 220
    # Tube from y=100 (top) to y=300 (bottom) at x=200
    # Float top edge is at y=220, bottom edge at y=250 (30px high)
    frame[220:250, 185:215] = 35

    reader = RotameterRulerReader(float_color="dark")

    # Scenario A: Top to Bottom (P1=top:800, P2=bottom:100)
    roi_top_down = ROIConfig(
        name="Ruler_TD",
        shape=ROIShape.RULER,
        coordinates=[[200, 100], [200, 300]],
        calibration_marks=[{"pos": 0.0, "value": 800.0}, {"pos": 1.0, "value": 100.0}]
    )
    rel_a, val_a, edge_a = reader.process_ruler_roi(frame, roi_top_down)
    assert val_a is not None
    assert edge_a is not None

    # Scenario B: Bottom to Top (P1=bottom:100, P2=top:800) - user standard
    roi_bottom_up = ROIConfig(
        name="Ruler_BU",
        shape=ROIShape.RULER,
        coordinates=[[200, 300], [200, 100]],
        calibration_marks=[{"pos": 0.0, "value": 100.0}, {"pos": 1.0, "value": 800.0}]
    )
    rel_b, val_b, edge_b = reader.process_ruler_roi(frame, roi_bottom_up)
    assert val_b is not None
    assert edge_b is not None

    # Both must detect the reading edge (y=220) and identical value
    assert val_a == pytest.approx(val_b, abs=1.0)
    assert edge_a[0][1] == pytest.approx(220.0, abs=2.0)
    assert edge_b[0][1] == pytest.approx(220.0, abs=2.0)

def test_ruler_float_resting_at_bottom():
    frame = np.ones((400, 400, 3), dtype=np.uint8) * 220
    # Float resting at bottom of tube: tube is [100, 300], float is [260, 300]
    frame[260:300, 185:215] = 35

    reader = RotameterRulerReader(float_color="dark")
    roi_bottom_up = ROIConfig(
        name="Ruler_Resting",
        shape=ROIShape.RULER,
        coordinates=[[200, 300], [200, 100]],
        calibration_marks=[{"pos": 0.0, "value": 100.0}, {"pos": 1.0, "value": 800.0}]
    )
    rel, val, edge = reader.process_ruler_roi(frame, roi_bottom_up)
    assert rel is not None
    assert val is not None
    assert edge is not None
    # Float top edge is at y=260:
    assert edge[0][1] == pytest.approx(260.0, abs=2.0)
    # Expected value: 100 + (40/200)*700 = 240
    assert val == pytest.approx(240.0, abs=5.0)

def test_video_widget_identical_scale_values_rejected(qtbot):
    from PySide6.QtWidgets import QInputDialog
    widget = VideoWidget()
    qtbot.addWidget(widget)

    frame = np.ones((400, 400, 3), dtype=np.uint8) * 200
    widget.update_frame(frame)
    widget.set_drawing_mode(ROIShape.RULER)
    widget.start_point = QPoint(50, 50)
    widget.end_point = QPoint(50, 250)

    # User enters 100.0 for start and 100.0 for end (zero span)
    warning_called = False
    def mock_warn(*args, **kwargs):
        nonlocal warning_called
        warning_called = True

    double_returns = [(100.0, True), (100.0, True)]
    with patch.object(QInputDialog, "getDouble", side_effect=lambda *args, **kwargs: double_returns.pop(0)), \
         patch.object(QMessageBox, "warning", side_effect=mock_warn):
        widget._finish_ruler()
        assert warning_called is True
        assert len(widget.rois) == 0

def test_ruler_edge_mode_selection():
    # Synthetic tube 200px high, background=200
    # Float is between y=80 and y=140 (thickness 60px)
    img = np.ones((200, 60), dtype=np.uint8) * 200
    img[80:140, 10:50] = 30

    reader = RotameterRulerReader(float_color="auto")
    
    y_top = reader.detect_float_y(img, edge_mode="top")
    y_bottom = reader.detect_float_y(img, edge_mode="bottom")
    y_center = reader.detect_float_y(img, edge_mode="center")

    assert y_top is not None
    assert y_bottom is not None
    assert y_center is not None

    # Top edge ~ 80
    assert abs(y_top - 80.0) <= 3.0
    # Bottom edge ~ 140
    assert abs(y_bottom - 140.0) <= 3.0
    # Center ~ 110
    assert abs(y_center - 110.0) <= 3.0

def test_roi_config_dialog_ruler_edge_selection(qtbot):
    ruler = ROIConfig(
        name="Ruler_Edge_Test",
        shape=ROIShape.RULER,
        coordinates=[[50, 50], [50, 250]],
        calibration_marks=[
            {"pos": 0.0, "value": 100.0},
            {"pos": 1.0, "value": 500.0}
        ],
        ruler_edge="top"
    )
    dlg = ROIConfigDialog(ruler)
    qtbot.addWidget(dlg)

    assert hasattr(dlg, "ruler_edge_combo")
    assert dlg.ruler_edge_combo.currentData() == "top"

    # Change to bottom
    idx_bottom = dlg.ruler_edge_combo.findData("bottom")
    dlg.ruler_edge_combo.setCurrentIndex(idx_bottom)

    data = dlg.get_data()
    assert data.ruler_edge == "bottom"

def test_ruler_rotameter_scale_suppression_toggle():
    # Tube 200px high x 60px wide, light fluid background 220
    img = np.ones((200, 60), dtype=np.uint8) * 220
    
    # Printed black scale ticks at y=25, 45, 65, 85, 105, 125, 145, 165
    # Ticks are 2px high and 20px wide on the left glass margin
    for y_tick in [25, 45, 65, 85, 105, 125, 145, 165]:
        img[y_tick:y_tick+2, 5:25] = 20

    # Physical float body: dark, solid mass from y=120 to y=170 (height 50px)
    img[120:170, 15:45] = 25

    reader = RotameterRulerReader(float_color="auto")

    # Case A: suppress_scale_marks = True (2D object filter)
    # Ignores all 2px scale tick marks and finds the true float top edge at y=120
    y_suppressed = reader.detect_float_y(img, edge_mode="top", suppress_scale_marks=True, min_float_height=8)
    assert y_suppressed is not None
    assert abs(y_suppressed - 120.0) <= 2.0

    # Case B: suppress_scale_marks = False (1D gradient mode)
    # Row profile collapses the ticks and snaps to the uppermost scale tick near y=25
    y_classic = reader.detect_float_y(img, edge_mode="top", suppress_scale_marks=False)
    assert y_classic is not None
    assert abs(y_classic - 25.0) <= 3.0
    assert y_classic != y_suppressed

def test_roi_config_dialog_suppress_scale_toggle(qtbot):
    ruler = ROIConfig(
        name="Ruler_Suppress_Test",
        shape=ROIShape.RULER,
        coordinates=[[50, 50], [50, 250]],
        calibration_marks=[{"pos": 0.0, "value": 100.0}, {"pos": 1.0, "value": 800.0}],
        suppress_scale_marks=True,
        core_width_pct=0.6,
        min_float_height=8
    )
    dlg = ROIConfigDialog(ruler)
    qtbot.addWidget(dlg)

    assert hasattr(dlg, "suppress_scale_cb")
    assert dlg.suppress_scale_cb.isChecked() is True
    assert dlg.core_width_spin.value() == 60
    assert dlg.min_height_spin.value() == 8

    # Toggle off, change parameters
    dlg.suppress_scale_cb.setChecked(False)
    dlg.core_width_spin.setValue(80)
    dlg.min_height_spin.setValue(15)

    data = dlg.get_data()
    assert data.suppress_scale_marks is False
    assert data.core_width_pct == 0.8
    assert data.min_float_height == 15

def test_preprocessing_dialog_suppress_scale_toggle(qtbot):
    img = np.ones((200, 60, 3), dtype=np.uint8) * 220
    # Scale tick at y=30
    img[30:32, 5:25] = 20
    # Float at y=120..170
    img[120:170, 15:45] = 25

    config = PreprocessingConfig(
        grayscale=True,
        suppress_scale_marks=True,
        core_width_pct=0.6,
        min_float_height=8
    )
    dlg = PreprocessingDialog(img, config)
    qtbot.addWidget(dlg)

    dlg.show_float_cb.setChecked(True)
    assert hasattr(dlg, "suppress_scale_cb")
    assert dlg.suppress_scale_cb.isChecked() is True
    # In 2D filter mode, float line is at y=120
    assert dlg.float_line_item is not None
    assert abs(dlg.float_line_item.line().y1() - 120.0) <= 2.0
    assert dlg.float_box_item is not None  # Green float box is rendered

    # Toggle off: switch to 1D gradient mode
    dlg.suppress_scale_cb.setChecked(False)
    assert abs(dlg.float_line_item.line().y1() - 30.0) <= 3.0
    assert dlg.float_box_item is None  # Green box removed in 1D mode

    updated_config = dlg.get_config()
    assert updated_config.suppress_scale_marks is False


def test_preprocessing_dialog_debug_toggle(qtbot):
    img = np.ones((200, 60, 3), dtype=np.uint8) * 220
    # Scale tick at y=30
    img[30:32, 5:25] = 20
    # Float at y=120..170
    img[120:170, 15:45] = 25

    config = PreprocessingConfig(
        grayscale=True,
        suppress_scale_marks=True,
        core_width_pct=0.6,
        min_float_height=8
    )
    dlg = PreprocessingDialog(img, config)
    qtbot.addWidget(dlg)
    dlg.show()

    assert hasattr(dlg, "show_debug_cb")
    assert dlg.show_debug_cb.isChecked() is False
    assert hasattr(dlg, "profile_plot")
    assert dlg.profile_plot.isHidden() is True
    assert len(dlg.debug_overlay_items) == 0

    # 1. Activate debug visualization (2D mode)
    dlg.show_debug_cb.setChecked(True)
    assert dlg.profile_plot.isHidden() is False
    assert dlg.float_line_item is not None
    assert abs(dlg.float_line_item.line().y1() - 120.0) <= 2.0
    assert dlg.float_box_item is not None
    assert len(dlg.debug_overlay_items) > 0  # Core bounds, rejected ticks, curves
    assert "2D-Objektfilter" in dlg.float_status_label.text()
    assert "Blobs:" in dlg.float_status_label.text()
    # Scene bounds expand to include side-plot
    assert dlg.scene.sceneRect().width() > 100

    # 2. Switch to 1D gradient debug mode
    dlg.suppress_scale_cb.setChecked(False)
    assert dlg.profile_plot.isHidden() is False
    assert dlg.float_line_item is not None
    assert abs(dlg.float_line_item.line().y1() - 30.0) <= 3.0
    assert dlg.float_box_item is None
    assert len(dlg.debug_overlay_items) > 0  # 1D margin lines, candidate lines, labels
    assert "1D-Gradient" in dlg.float_status_label.text()
    assert "1D-Peaks:" in dlg.float_status_label.text()

    # Verify horizontal candidate lines include both red selected line and orange dashed lines
    from PySide6.QtWidgets import QGraphicsLineItem
    from PySide6.QtCore import Qt
    line_overlays = [it for it in dlg.debug_overlay_items if isinstance(it, QGraphicsLineItem) and it.line().y1() == it.line().y2()]
    assert len(line_overlays) >= 2
    # At least one bold solid red line for selected peak
    red_selected = [l for l in line_overlays if l.pen().color().red() > 200 and l.pen().color().green() < 100 and l.pen().style() == Qt.SolidLine]
    assert len(red_selected) >= 1

    # 3. Deactivate debug visualization and float line
    dlg.show_debug_cb.setChecked(False)
    dlg.show_float_cb.setChecked(False)
    assert dlg.profile_plot.isHidden() is True
    assert len(dlg.debug_overlay_items) == 0
    assert dlg.float_line_item is None
    assert dlg.float_status_label.text() == "Float Position: -"
    # Scene bounds contract back to crop rect (width=60, height=200)
    assert dlg.scene.sceneRect().width() == 60.0
    assert dlg.scene.sceneRect().height() == 200.0


def test_process_ruler_roi_debug_output():
    from instrument_reader.core.analog_reader import RotameterRulerReader
    from instrument_reader.core.roi import ROIConfig, ROIShape

    frame = np.ones((300, 300, 3), dtype=np.uint8) * 200
    # Axis from (150, 250) to (150, 50)
    # Put float body at row 150
    frame[140:170, 135:165] = 20

    ruler = ROIConfig(
        name="Ruler_Debug_Test",
        shape=ROIShape.RULER,
        coordinates=[[150, 50], [150, 250]],
        strip_width=40.0,
        calibration_marks=[{"pos": 0.0, "value": 0.0}, {"pos": 1.0, "value": 100.0}],
        suppress_scale_marks=True
    )

    reader = RotameterRulerReader()
    rel_pos, val, edge_pts, debug = reader.process_ruler_roi(frame, ruler, return_debug=True)

    assert rel_pos is not None
    assert val is not None
    assert edge_pts is not None
    assert debug is not None
    assert isinstance(debug, dict)
    assert "mode" in debug
    assert "candidates_1d" in debug
    assert "profile" in debug

