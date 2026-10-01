import numpy as np
import pytest
from instrument_reader.core.analog_reader import RotameterReader

def test_detect_float_y_dark():
    # 200 high x 60 wide image
    # Fluid background = 200 (light gray)
    img = np.ones((200, 60), dtype=np.uint8) * 200
    # Dark float begins at y=120 downwards to y=180
    img[120:180, 10:50] = 30

    reader = RotameterReader(float_color="dark", margin_x_pct=0.1)
    y_float = reader.detect_float_y(img)
    assert y_float is not None
    # Detected top edge should be within 3 pixels of 120
    assert abs(y_float - 120) <= 3

def test_detect_float_y_bright():
    # Fluid background = 20 (dark)
    img = np.ones((200, 60), dtype=np.uint8) * 20
    # Bright float from y=75 to y=140
    img[75:140, 10:50] = 230

    reader = RotameterReader(float_color="bright", margin_x_pct=0.1)
    y_float = reader.detect_float_y(img)
    assert y_float is not None
    assert abs(y_float - 75) <= 3

def test_detect_float_y_auto():
    img = np.ones((200, 60), dtype=np.uint8) * 200
    img[90:150, 15:45] = 40

    reader = RotameterReader(float_color="auto", margin_x_pct=0.1)
    y_float = reader.detect_float_y(img)
    assert y_float is not None
    assert abs(y_float - 90) <= 3

def test_detect_float_y_edge_cases():
    reader = RotameterReader()
    assert reader.detect_float_y(None) is None
    assert reader.detect_float_y(np.zeros((0, 0), dtype=np.uint8)) is None
    assert reader.detect_float_y(np.zeros((2, 2), dtype=np.uint8)) is None

def test_piecewise_linear_interpolation_rotameter():
    # Rotameter tube:
    # y=800 (bottom) -> 100 Nl/h
    # y=500 (middle) -> 200 Nl/h
    # y=200 (top)    -> 500 Nl/h
    cal_points = [
        [800.0, 100.0],
        [500.0, 200.0],
        [200.0, 500.0],
    ]

    # 1. Exact points
    assert RotameterReader.interpolate_value(800.0, cal_points) == 100.0
    assert RotameterReader.interpolate_value(500.0, cal_points) == 200.0
    assert RotameterReader.interpolate_value(200.0, cal_points) == 500.0

    # 2. Between lower segment [800, 500] -> [100, 200]
    # Halfway between 800 and 500 is 650 -> value should be 150
    val_650 = RotameterReader.interpolate_value(650.0, cal_points)
    assert val_650 == pytest.approx(150.0, rel=1e-3)

    # 3. Between upper segment [500, 200] -> [200, 500]
    # Halfway between 500 and 200 is 350 -> value should be 350
    val_350 = RotameterReader.interpolate_value(350.0, cal_points)
    assert val_350 == pytest.approx(350.0, rel=1e-3)

    # 4. Extrapolation below bottom (y > 800)
    # y=950: 150px below 800 -> 100 - (150/300)*100 = 50.0
    val_950 = RotameterReader.interpolate_value(950.0, cal_points)
    assert val_950 == pytest.approx(50.0, rel=1e-3)

    # 5. Extrapolation above top (y < 200)
    # y=100: 100px above 200 -> 500 + (100/300)*300 = 600.0
    val_100 = RotameterReader.interpolate_value(100.0, cal_points)
    assert val_100 == pytest.approx(600.0, rel=1e-3)

def test_interpolate_edge_cases():
    assert RotameterReader.interpolate_value(None, [[100, 10]]) is None
    assert RotameterReader.interpolate_value(50.0, []) is None
    assert RotameterReader.interpolate_value(50.0, [[100, 10]]) == 10.0

def test_reader_full_flow():
    img = np.ones((200, 60), dtype=np.uint8) * 200
    img[100:150, 10:50] = 30
    cal_points = [(180, 0), (20, 100)]
    reader = RotameterReader(float_color="dark")
    y_float, val = reader.read(img, cal_points)
    assert y_float is not None
    assert val is not None
    assert 40.0 < val < 60.0

def test_detect_float_y_uniform_image():
    # A completely uniform image has zero contrast; float edge should NOT be found
    img = np.ones((200, 60), dtype=np.uint8) * 128
    reader = RotameterReader()
    assert reader.detect_float_y(img) is None

def test_interpolate_duplicate_points():
    cal_points = [
        [100.0, 10.0],
        [100.0, 20.0],  # Duplicate y
        [200.0, 50.0],
    ]
    # At y=100, merged value should be (10+20)/2 = 15.0
    val_100 = RotameterReader.interpolate_value(100.0, cal_points)
    assert val_100 == pytest.approx(15.0)

    # In-between y=150: halfway between 100 and 200 -> 15 + 0.5 * (50 - 15) = 32.5
    val_150 = RotameterReader.interpolate_value(150.0, cal_points)
    assert val_150 == pytest.approx(32.5)


def test_detect_float_y_debug_output_2d():
    img = np.ones((200, 60), dtype=np.uint8) * 220
    # Add a thin scale tick at y=30 (height 2px)
    img[30:32, 10:30] = 20
    # Add float body at y=120..170 (height 50px)
    img[120:170, 15:45] = 25

    reader = RotameterReader(float_color="dark", suppress_scale_marks=True, min_float_height=8)
    debug = reader.detect_float_y(img, return_debug=True)

    assert isinstance(debug, dict)
    assert debug["mode"] == "2d"
    assert debug["y_float"] is not None
    assert abs(debug["y_float"] - 120.0) <= 2.0
    assert debug["box"] is not None
    assert isinstance(debug["box"], tuple)
    assert len(debug["box"]) == 4

    assert isinstance(debug["profile"], np.ndarray)
    assert len(debug["profile"]) == 200
    assert isinstance(debug["gradient"], np.ndarray)
    assert len(debug["gradient"]) == 200
    assert isinstance(debug["pad"], int)
    assert isinstance(debug["pad_slice"], slice)
    assert isinstance(debug["grad_valid"], np.ndarray)
    assert isinstance(debug["candidates_1d"], list)
    assert len(debug["candidates_1d"]) > 0
    # In 2D mode, tick candidates must not be selected; only candidate matching the 2D float is selected
    tick_cands = [c for c in debug["candidates_1d"] if c["y"] < 50]
    assert len(tick_cands) > 0
    assert all(not c["selected"] for c in tick_cands)
    float_cands = [c for c in debug["candidates_1d"] if abs(c["y"] - debug["y_float"]) <= 1.0]
    assert any(c["selected"] for c in float_cands)

    assert isinstance(debug["blobs"], list)
    assert len(debug["blobs"]) >= 2  # At least tick and float body
    # Verify selected blob
    selected_blobs = [b for b in debug["blobs"] if b.get("selected")]
    assert len(selected_blobs) == 1
    assert selected_blobs[0]["is_float"] is True

    # Verify rejected tick blob
    rejected_blobs = [b for b in debug["blobs"] if not b.get("is_float")]
    assert len(rejected_blobs) >= 1

    assert debug["core_bounds"] is not None
    assert isinstance(debug["core_bounds"], tuple)
    assert len(debug["core_bounds"]) == 2
    assert "2D" in debug["reason"] or "Schwimmerkörper" in debug["reason"]


def test_detect_float_y_debug_output_1d():
    img = np.ones((200, 60), dtype=np.uint8) * 220
    # Scale tick at y=30
    img[30:32, 10:30] = 20
    # Float body at y=120..170
    img[120:170, 15:45] = 25

    reader = RotameterReader(float_color="dark", suppress_scale_marks=False)
    debug = reader.detect_float_y(img, return_debug=True)

    assert isinstance(debug, dict)
    assert debug["mode"] == "1d"
    assert debug["y_float"] is not None
    # In 1D mode, snaps to the first dark transition (tick mark at y=30)
    assert abs(debug["y_float"] - 30.0) <= 3.0
    assert debug["box"] is None
    assert debug["blobs"] == []
    assert debug["core_bounds"] is None

    assert isinstance(debug["profile"], np.ndarray)
    assert isinstance(debug["gradient"], np.ndarray)
    assert len(debug["gradient"]) == len(debug["profile"])
    assert isinstance(debug["candidates_1d"], list)
    assert len(debug["candidates_1d"]) > 0
    # Verify candidate structure
    for cand in debug["candidates_1d"]:
        assert "y" in cand
        assert "idx" in cand
        assert "grad" in cand
        assert "selected" in cand
        assert isinstance(cand["y"], int)
        assert isinstance(cand["grad"], float)
        assert isinstance(cand["selected"], bool)

    # Exactly one candidate should be selected
    selected_cands = [c for c in debug["candidates_1d"] if c["selected"]]
    assert len(selected_cands) == 1
    assert abs(selected_cands[0]["y"] - 30) <= 3


def test_detect_float_y_debug_empty_and_edge_cases():
    reader = RotameterReader()

    # None
    d_none = reader.detect_float_y(None, return_debug=True)
    assert isinstance(d_none, dict)
    assert d_none["y_float"] is None
    assert d_none["box"] is None
    assert d_none["candidates_1d"] == []
    assert d_none["blobs"] == []
    assert isinstance(d_none["pad_slice"], slice)

    # Empty 0x0
    d_empty = reader.detect_float_y(np.zeros((0, 0), dtype=np.uint8), return_debug=True)
    assert isinstance(d_empty, dict)
    assert d_empty["y_float"] is None

    # Small 2x2
    d_small = reader.detect_float_y(np.zeros((2, 2), dtype=np.uint8), return_debug=True)
    assert isinstance(d_small, dict)
    assert d_small["y_float"] is None

    # Uniform 200x60
    d_uni = reader.detect_float_y(np.ones((200, 60), dtype=np.uint8) * 128, return_debug=True)
    assert isinstance(d_uni, dict)
    assert d_uni["y_float"] is None


def test_detect_float_y_2d_fallback_to_1d():
    # Image with ONLY thin ticks (height 2px) and no physical float
    img = np.ones((200, 60), dtype=np.uint8) * 220
    img[50:52, 10:30] = 20
    img[100:102, 10:30] = 20

    reader = RotameterReader(float_color="dark", suppress_scale_marks=True, min_float_height=10)
    debug = reader.detect_float_y(img, return_debug=True)

    assert isinstance(debug, dict)
    assert debug["mode"] == "1d"  # Fell back to 1D
    assert debug["box"] is None
    assert len(debug["blobs"]) >= 2  # The ticks were found by 2D
    assert all(not b["is_float"] for b in debug["blobs"])  # All rejected
    assert "Fallback" in debug["reason"] or "keinen Schwimmerkörper" in debug["reason"]


def test_detect_float_y_1d_center_mode():
    img = np.ones((200, 60), dtype=np.uint8) * 220
    # Add dark float at 80..120
    img[80:120, 15:45] = 20

    reader = RotameterReader(float_color="dark", suppress_scale_marks=False)
    debug = reader.detect_float_y(img, edge_mode="center", return_debug=True)

    assert debug["mode"] == "1d"
    assert debug["y_float"] is not None
    assert abs(debug["y_float"] - 100.0) <= 3.0
    # In center mode, both top and bottom extrema are marked selected
    sel = [c for c in debug["candidates_1d"] if c["selected"]]
    assert len(sel) == 2


def test_rotameter_reader_read_debug():
    img = np.ones((200, 60), dtype=np.uint8) * 220
    img[80:130, 15:45] = 20

    reader = RotameterReader(float_color="dark", suppress_scale_marks=True, min_float_height=8)
    cal = [(50.0, 0.0), (150.0, 100.0)]
    y_float, val, debug = reader.read(img, cal, return_debug=True)

    assert y_float is not None
    assert abs(y_float - 80.0) <= 2.0
    assert val is not None
    assert 25.0 <= val <= 35.0
    assert isinstance(debug, dict)
    assert debug["mode"] == "2d"

