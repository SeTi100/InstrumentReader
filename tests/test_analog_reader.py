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
