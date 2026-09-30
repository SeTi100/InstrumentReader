import pytest
import numpy as np
from instrument_reader.core.roi import ROIConfig, ROIShape, DisplayType
from instrument_reader.core.ocr_engine import OCRResult
from instrument_reader.gui.ocr_worker import OCRWorker, is_point_in_roi, get_roi_center, get_roi_x

def test_point_in_roi_and_center():
    roi_rect = ROIConfig(name="Box", coordinates=[100, 100, 200, 100])
    assert is_point_in_roi(150, 150, roi_rect)
    assert not is_point_in_roi(50, 50, roi_rect)
    assert not is_point_in_roi(350, 150, roi_rect)
    
    cx, cy = get_roi_center(roi_rect)
    assert cx == 200.0
    assert cy == 150.0
    assert get_roi_x(roi_rect) == 100.0

def test_hierarchy_resolution():
    worker = OCRWorker()
    
    container = ROIConfig(
        name="Container_1",
        is_container=True,
        coordinates=[100, 100, 400, 150]
    )
    
    slot_1 = ROIConfig(name="Slot_1", coordinates=[110, 120, 60, 100])
    slot_2 = ROIConfig(name="Slot_2", coordinates=[180, 120, 60, 100])
    slot_3 = ROIConfig(name="Slot_3", coordinates=[250, 120, 60, 100])
    slot_4 = ROIConfig(name="Slot_4", coordinates=[320, 120, 60, 100])
    
    standalone = ROIConfig(name="Standalone", coordinates=[600, 600, 100, 100])
    
    rois = [container, slot_1, slot_2, slot_3, slot_4, standalone]
    container_children, child_names = worker.resolve_hierarchy(rois)
    
    assert "Container_1" in container_children
    children = container_children["Container_1"]
    assert len(children) == 4
    child_roi_names = {c.name for c in children}
    assert child_roi_names == {"Slot_1", "Slot_2", "Slot_3", "Slot_4"}
    
    assert "Slot_1" in child_names
    assert "Slot_2" in child_names
    assert "Slot_3" in child_names
    assert "Slot_4" in child_names
    assert "Standalone" not in child_names

def test_composite_assembly_and_decimal_position():
    container = ROIConfig(
        name="Scale",
        is_container=True,
        decimal_position=3,  # Dot after 3rd digit: "154.3"
        unit="g"
    )
    
    slots = ["1", "5", "4", "3"]
    # Decimal position logic
    dec_pos = container.decimal_position
    assert dec_pos == 3
    num_str = "".join(slots[:dec_pos]) + "." + "".join(slots[dec_pos:])
    assert num_str == "154.3"
    assert float(num_str) == 154.3

def test_composite_assembly_decimal_places():
    container = ROIConfig(
        name="Scale",
        is_container=True,
        decimal_places=2,  # 2 decimal places: "15.43"
        unit="g"
    )
    slots = ["1", "5", "4", "3"]
    pos = len(slots) - container.decimal_places
    num_str = "".join(slots[:pos]) + "." + "".join(slots[pos:])
    assert num_str == "15.43"
    assert float(num_str) == 15.43

def test_digit_slot_cache_against_flicker():
    worker = OCRWorker()
    
    container = ROIConfig(
        name="Waage",
        is_container=True,
        decimal_position=3,
        coordinates=[100, 100, 400, 150]
    )
    slot1 = ROIConfig(name="S1", coordinates=[110, 120, 60, 100])
    slot2 = ROIConfig(name="S2", coordinates=[180, 120, 60, 100])
    slot3 = ROIConfig(name="S3", coordinates=[250, 120, 60, 100])
    slot4 = ROIConfig(name="S4", coordinates=[320, 120, 60, 100])
    
    # Frame 1: All slots recognized
    worker.digit_slots_cache["S1"] = "1"
    worker.digit_slots_cache["S2"] = "5"
    worker.digit_slots_cache["S3"] = "4"
    worker.digit_slots_cache["S4"] = "3"
    
    slots_sorted = [slot1, slot2, slot3, slot4]
    slot_chars = [worker.digit_slots_cache.get(c.name, "") for c in slots_sorted]
    num_str = "".join(slot_chars[:3]) + "." + "".join(slot_chars[3:])
    assert float(num_str) == 154.3
    
    # Frame 2: Shutter flicker causes S2 OCR to fail (empty raw_text)
    # The cache maintains the previous digit '5'
    new_ocr_raw_text = {"S1": "1", "S2": "", "S3": "4", "S4": "3"}
    for slot_name, text in new_ocr_raw_text.items():
        found = [ch for ch in text if ch.isdigit()]
        if found:
            worker.digit_slots_cache[slot_name] = found[0]
            
    # Cache for S2 should still be '5'
    assert worker.digit_slots_cache["S2"] == "5"
    slot_chars = [worker.digit_slots_cache.get(c.name, "") for c in slots_sorted]
    num_str = "".join(slot_chars[:3]) + "." + "".join(slot_chars[3:])
    assert float(num_str) == 154.3
    
    # Frame 3: Measurement changes: S4 becomes 4
    new_ocr_raw_text = {"S1": "1", "S2": "5", "S3": "4", "S4": "4"}
    for slot_name, text in new_ocr_raw_text.items():
        found = [ch for ch in text if ch.isdigit()]
        if found:
            worker.digit_slots_cache[slot_name] = found[0]
            
    slot_chars = [worker.digit_slots_cache.get(c.name, "") for c in slots_sorted]
    num_str = "".join(slot_chars[:3]) + "." + "".join(slot_chars[3:])
    assert float(num_str) == 154.4

class DummyEngine:
    def __init__(self, mapping):
        self.mapping = mapping  # roi_crop shape or coords -> digit string
        self.call_count = 0

    def recognize(self, image, config=None):
        self.call_count += 1
        # Extract from mapping based on image width/shape or tag
        tag = self.mapping.get(image.shape[:2], "")
        return OCRResult(raw_text=tag, parsed_value=float(tag) if tag.isdigit() else None, confidence=0.95, success=bool(tag))

def test_process_frame_digital_composite_end_to_end():
    worker = OCRWorker()
    
    frame = np.zeros((400, 600, 3), dtype=np.uint8)
    
    container = ROIConfig(
        name="Scale",
        is_container=True,
        decimal_position=3,
        coordinates=[50, 50, 400, 100],
        unit="g"
    )
    # Distinct shapes for slots so dummy engine can return distinct digits
    s1 = ROIConfig(name="S1", coordinates=[60, 60, 41, 70])
    s2 = ROIConfig(name="S2", coordinates=[120, 60, 42, 70])
    s3 = ROIConfig(name="S3", coordinates=[180, 60, 43, 70])
    s4 = ROIConfig(name="S4", coordinates=[240, 60, 44, 70])
    rois = [container, s1, s2, s3, s4]
    
    # Frame 1: All digits detected: 1, 5, 4, 3
    worker.engine = DummyEngine({
        (70, 41): "1",
        (70, 42): "5",
        (70, 43): "4",
        (70, 44): "3",
    })
    
    readings = worker.process_frame(frame, rois)
    container_reading = next(r for r in readings if r["roi_name"] == "Scale")
    assert container_reading["is_child"] is False
    assert container_reading["parsed_value"] == 154.3
    assert container_reading["is_valid"] is True
    
    # Check that children have is_child=True
    child_readings = [r for r in readings if r["is_child"]]
    assert len(child_readings) == 4
    assert [r["raw_text"] for r in child_readings] == ["1", "5", "4", "3"]
    
    # Frame 2: Slot S2 drops out completely (returns empty string due to shutter flicker)
    worker.engine = DummyEngine({
        (70, 41): "1",
        (70, 42): "",   # S2 drops out!
        (70, 43): "4",
        (70, 44): "3",
    })
    readings2 = worker.process_frame(frame, rois)
    container_reading2 = next(r for r in readings2 if r["roi_name"] == "Scale")
    # Composite value MUST remain 154.3 due to slot caching!
    assert container_reading2["parsed_value"] == 154.3
    assert container_reading2["is_valid"] is True

def test_process_frame_analog_rotameter_end_to_end():
    worker = OCRWorker()
    
    # 300 high x 100 wide tube image
    frame = np.ones((300, 100, 3), dtype=np.uint8) * 200
    # Float from y=100 to y=150
    frame[100:150, 20:80] = 30
    
    rotameter = ROIConfig(
        name="Flowmeter",
        is_container=True,
        display_type=DisplayType.ANALOG,
        coordinates=[0, 0, 100, 300],
        unit="Nl/h",
        decimal_places=1
    )
    # Calibration mark 1 at y=50 center (top, val=500)
    mark1 = ROIConfig(name="Mark_500", coordinates=[10, 45, 80, 10], analog_value=500.0)
    # Calibration mark 2 at y=150 center (bottom, val=100)
    mark2 = ROIConfig(name="Mark_100", coordinates=[10, 145, 80, 10], analog_value=100.0)
    
    rois = [rotameter, mark1, mark2]
    readings = worker.process_frame(frame, rois)
    
    flow_reading = next(r for r in readings if r["roi_name"] == "Flowmeter")
    assert flow_reading["is_child"] is False
    assert flow_reading["is_valid"] is True
    # Float top edge is near y=100, exactly halfway between 50 and 150 -> value should be approx 300
    assert abs(flow_reading["parsed_value"] - 300.0) <= 20.0

def test_process_frame_analog_rotameter_upscaled():
    worker = OCRWorker()
    frame = np.ones((300, 100, 3), dtype=np.uint8) * 200
    frame[100:150, 20:80] = 30
    
    rotameter = ROIConfig(
        name="Flowmeter",
        is_container=True,
        display_type=DisplayType.ANALOG,
        coordinates=[0, 0, 100, 300],
        unit="Nl/h",
        decimal_places=1,
        preprocessing_params={"upscale_factor": 2}
    )
    mark1 = ROIConfig(name="Mark_500", coordinates=[10, 45, 80, 10], analog_value=500.0)
    mark2 = ROIConfig(name="Mark_100", coordinates=[10, 145, 80, 10], analog_value=100.0)
    
    readings = worker.process_frame(frame, [rotameter, mark1, mark2])
    flow = next(r for r in readings if r["roi_name"] == "Flowmeter")
    assert flow["is_valid"] is True
    # Float at y=100 must still yield approx 300.0 despite 2x upscaling!
    assert abs(flow["parsed_value"] - 300.0) <= 20.0

def test_composite_assembly_leading_blanks():
    worker = OCRWorker()
    frame = np.zeros((400, 600, 3), dtype=np.uint8)
    
    container = ROIConfig(
        name="Scale",
        is_container=True,
        decimal_position=3,
        coordinates=[50, 50, 400, 100],
        unit="g",
        allow_leading_blank=True
    )
    s1 = ROIConfig(name="S1", coordinates=[60, 60, 41, 70])
    s2 = ROIConfig(name="S2", coordinates=[120, 60, 42, 70])
    s3 = ROIConfig(name="S3", coordinates=[180, 60, 43, 70])
    s4 = ROIConfig(name="S4", coordinates=[240, 60, 44, 70])
    rois = [container, s1, s2, s3, s4]
    
    # S1 and S2 unlit/dark, display shows "  5.2"
    worker.engine = DummyEngine({
        (70, 41): "",
        (70, 42): "",
        (70, 43): "5",
        (70, 44): "2",
    })
    
    readings = worker.process_frame(frame, rois)
    comp = next(r for r in readings if r["roi_name"] == "Scale")
    assert comp["is_valid"] is True
    assert comp["parsed_value"] == 5.2
    assert comp["raw_text"] == "5.2"

def test_composite_assembly_negative_numbers():
    worker = OCRWorker()
    frame = np.zeros((400, 600, 3), dtype=np.uint8)
    
    container = ROIConfig(
        name="Thermometer",
        is_container=True,
        decimal_position=3,
        coordinates=[50, 50, 400, 100],
        unit="°C",
        allow_negative=True
    )
    s1 = ROIConfig(name="S1", coordinates=[60, 60, 41, 70])
    s2 = ROIConfig(name="S2", coordinates=[120, 60, 42, 70])
    s3 = ROIConfig(name="S3", coordinates=[180, 60, 43, 70])
    s4 = ROIConfig(name="S4", coordinates=[240, 60, 44, 70])
    rois = [container, s1, s2, s3, s4]
    
    # Shows "-15.4"
    worker.engine = DummyEngine({
        (70, 41): "-",
        (70, 42): "1",
        (70, 43): "5",
        (70, 44): "4",
    })
    
    readings = worker.process_frame(frame, rois)
    comp = next(r for r in readings if r["roi_name"] == "Thermometer")
    assert comp["is_valid"] is True
    assert comp["parsed_value"] == -15.4

def test_composite_assembly_internal_missing_waits():
    worker = OCRWorker()
    frame = np.zeros((400, 600, 3), dtype=np.uint8)
    
    container = ROIConfig(
        name="Scale",
        is_container=True,
        decimal_position=3,
        coordinates=[50, 50, 400, 100],
        unit="g"
    )
    s1 = ROIConfig(name="S1", coordinates=[60, 60, 41, 70])
    s2 = ROIConfig(name="S2", coordinates=[120, 60, 42, 70])
    s3 = ROIConfig(name="S3", coordinates=[180, 60, 43, 70])
    s4 = ROIConfig(name="S4", coordinates=[240, 60, 44, 70])
    rois = [container, s1, s2, s3, s4]
    
    # S2 is internally missing: "1 _ 4 3"
    worker.engine = DummyEngine({
        (70, 41): "1",
        (70, 42): "",
        (70, 43): "4",
        (70, 44): "3",
    })
    
    readings = worker.process_frame(frame, rois)
    comp = next(r for r in readings if r["roi_name"] == "Scale")
    assert comp["is_valid"] is False
    assert "Waiting for slot(s): 2" in comp["reason"]

def test_child_does_not_inherit_container_masks():
    worker = OCRWorker()
    frame = np.ones((400, 600, 3), dtype=np.uint8) * 255
    
    container = ROIConfig(
        name="Scale",
        is_container=True,
        coordinates=[50, 50, 400, 100],
        preprocessing_params={"masks": [{"type": "rect", "coords": [10, 10, 20, 20], "color": 0}]}
    )
    s1 = ROIConfig(name="S1", coordinates=[60, 60, 40, 70])
    
    worker.engine = DummyEngine({(70, 40): "7"})
    readings = worker.process_frame(frame, [container, s1])
    s1_reading = next(r for r in readings if r["roi_name"] == "S1")
    assert s1_reading["raw_text"] == "7"

def test_explicit_sub_roi_ids_order():
    worker = OCRWorker()
    frame = np.zeros((400, 600, 3), dtype=np.uint8)
    
    container = ROIConfig(
        name="ReverseOrderContainer",
        is_container=True,
        sub_roi_ids=["S3", "S1", "S2"],  # Explicit order
        coordinates=[50, 50, 400, 100],
    )
    s1 = ROIConfig(name="S1", coordinates=[60, 60, 41, 70])
    s2 = ROIConfig(name="S2", coordinates=[120, 60, 42, 70])
    s3 = ROIConfig(name="S3", coordinates=[180, 60, 43, 70])
    rois = [container, s1, s2, s3]
    
    worker.engine = DummyEngine({
        (70, 43): "9",  # S3
        (70, 41): "8",  # S1
        (70, 42): "7",  # S2
    })
    
    readings = worker.process_frame(frame, rois)
    comp = next(r for r in readings if r["roi_name"] == "ReverseOrderContainer")
    assert comp["is_valid"] is True
    # In order S3, S1, S2 -> "987" -> 987.0
    assert comp["parsed_value"] == 987.0

