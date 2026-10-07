import threading
import time
import cv2
import numpy as np
from typing import Optional, List, Dict, Set, Tuple
from PySide6.QtCore import QThread, Signal
from instrument_reader.core.ocr_engine import TesseractEngine, OCRResult
from instrument_reader.core.preprocessing import PreprocessingPipeline, PreprocessingConfig
from instrument_reader.core.validator import ValueValidator
from instrument_reader.core.roi import ROIShape, ROIConfig, DisplayType
from instrument_reader.core.analog_reader import RotameterReader, RotameterRulerReader

def is_point_in_roi(px: float, py: float, roi: ROIConfig) -> bool:
    if roi.shape == ROIShape.RECTANGLE:
        if len(roi.coordinates) != 4:
            return False
        rx, ry, rw, rh = roi.coordinates
        return rx <= px <= (rx + rw) and ry <= py <= (ry + rh)
    elif roi.shape == ROIShape.POLYGON:
        pts = np.array(roi.coordinates, np.int32)
        if len(pts) < 3:
            return False
        return cv2.pointPolygonTest(pts, (float(px), float(py)), False) >= 0
    return False

def get_roi_center(roi: ROIConfig) -> Tuple[float, float]:
    if roi.shape == ROIShape.RECTANGLE and len(roi.coordinates) == 4:
        return roi.coordinates[0] + roi.coordinates[2] / 2.0, roi.coordinates[1] + roi.coordinates[3] / 2.0
    elif roi.shape == ROIShape.POLYGON and len(roi.coordinates) >= 3:
        pts = np.array(roi.coordinates, np.float32)
        return float(np.mean(pts[:, 0])), float(np.mean(pts[:, 1]))
    return 0.0, 0.0

def get_roi_x(roi: ROIConfig) -> float:
    if roi.shape == ROIShape.RECTANGLE and len(roi.coordinates) == 4:
        return float(roi.coordinates[0])
    elif roi.shape == ROIShape.POLYGON and len(roi.coordinates) >= 1:
        return float(min(p[0] for p in roi.coordinates))
    return 0.0

def extract_roi_crop(frame: np.ndarray, roi: ROIConfig) -> Optional[np.ndarray]:
    img_h, img_w = frame.shape[:2]
    if roi.shape == ROIShape.RECTANGLE:
        if len(roi.coordinates) != 4:
            return None
        x, y, w, h = roi.coordinates
        x1, y1 = max(0, x), max(0, y)
        x2, y2 = min(img_w, x + w), min(img_h, y + h)
        if x2 <= x1 or y2 <= y1:
            return None
        return frame[y1:y2, x1:x2].copy()
    else:
        pts = np.array(roi.coordinates, np.int32)
        if len(pts) < 3:
            return None
        rect = cv2.boundingRect(pts)
        x, y, w, h = rect
        x1, y1 = max(0, x), max(0, y)
        x2, y2 = min(img_w, x + w), min(img_h, y + h)
        if x2 <= x1 or y2 <= y1:
            return None
        crop = frame[y1:y2, x1:x2].copy()
        mask = np.zeros(crop.shape[:2], dtype=np.uint8)
        pts_shifted = pts - [x1, y1]
        cv2.fillPoly(mask, [pts_shifted], 255)
        return cv2.bitwise_and(crop, crop, mask=mask)

class OCRWorker(QThread):
    readings_ready = Signal(list)
    
    def __init__(self, interval_ms=1000):
        super().__init__()
        self.interval_ms = interval_ms
        self.running = False
        self.rois: List[ROIConfig] = []
        self.current_frame = None
        # Epoch timestamp of current_frame (video time for files, wall clock for live)
        self.current_ts: Optional[float] = None
        self._cond = threading.Condition()
        self._last_taken_ts: Optional[float] = None
        self._next_due_ts: Optional[float] = None
        self.engine = TesseractEngine()
        self.pipeline = PreprocessingPipeline()
        self.validator = ValueValidator()
        self.rotameter_reader = RotameterReader()
        self.ruler_reader = RotameterRulerReader()
        self.last_valid_values: Dict[str, float] = {}
        self.last_timestamps: Dict[str, float] = {}
        self.digit_slots_cache: Dict[str, str] = {}
        
    def update_frame(self, frame, timestamp: Optional[float] = None):
        ts = time.time() if timestamp is None else float(timestamp)
        with self._cond:
            if self._last_taken_ts is not None and ts < self._last_taken_ts:
                # Jumped backwards (seek / video restarted): restart the sampling
                # schedule and forget per-ROI history so dt stays positive.
                self._reset_schedule_locked()
            self.current_frame = frame
            self.current_ts = ts
            self._cond.notify_all()

    def update_rois(self, rois):
        with self._cond:
            self.rois = list(rois)
            self.last_valid_values.clear()
            self.last_timestamps.clear()
            self.digit_slots_cache.clear()
            self._cond.notify_all()

    def set_interval(self, interval_ms):
        with self._cond:
            self.interval_ms = interval_ms
            self._next_due_ts = None
            self._cond.notify_all()

    def _reset_schedule_locked(self):
        self._last_taken_ts = None
        self._next_due_ts = None
        self.last_valid_values.clear()
        self.last_timestamps.clear()
        self.digit_slots_cache.clear()

    def _frame_due_locked(self) -> bool:
        """True if the current frame should be read: new, and at least one
        logging interval (in frame time) after the previously read frame."""
        if self.current_frame is None or not self.rois or self.current_ts is None:
            return False
        if self._last_taken_ts is not None and self.current_ts <= self._last_taken_ts:
            return False
        return self._next_due_ts is None or self.current_ts >= self._next_due_ts

    def wait_while_pending(self, keep_going=lambda: True):
        """
        Blocks while the current frame is due but not yet picked up by the worker.
        Called by the camera thread in unpaced playback so no sample is skipped.
        """
        with self._cond:
            while self.running and keep_going() and self._frame_due_locked():
                self._cond.wait(0.05)

    def _recognize_many(self, images: List[np.ndarray], config: Optional[str]) -> List[OCRResult]:
        recognize_many = getattr(self.engine, "recognize_many", None)
        if recognize_many is not None:
            return recognize_many(images, config)
        return [self.engine.recognize(img, config=config) for img in images]

    def resolve_hierarchy(self, rois: List[ROIConfig]) -> Tuple[Dict[str, List[ROIConfig]], Set[str]]:
        """
        Determines which ROIs are containers and maps each container to its children.
        Returns:
            (container_to_children_dict, set_of_all_child_roi_names)
        """
        containers = [r for r in rois if getattr(r, "is_container", False)]
        non_containers = [r for r in rois if not getattr(r, "is_container", False)]
        
        container_children: Dict[str, List[ROIConfig]] = {c.name: [] for c in containers}
        child_names: Set[str] = set()

        for c in containers:
            sub_ids = getattr(c, "sub_roi_ids", [])
            if sub_ids:
                nc_by_name = {nc.name: nc for nc in non_containers}
                for sid in sub_ids:
                    if sid in nc_by_name:
                        container_children[c.name].append(nc_by_name[sid])
                        child_names.add(sid)
            else:
                for nc in non_containers:
                    cx, cy = get_roi_center(nc)
                    if is_point_in_roi(cx, cy, c):
                        container_children[c.name].append(nc)
                        child_names.add(nc.name)

        return container_children, child_names

    def run(self):
        self.running = True
        while self.running:
            with self._cond:
                if not self._frame_due_locked():
                    self._cond.wait(0.05)
                    continue
                frame = self.current_frame.copy()
                ts = self.current_ts
                rois = list(self.rois)
                # Schedule on a fixed grid in frame time so the logging interval
                # means the same thing for live cameras and for (sped up) videos.
                interval = self.interval_ms / 1000.0
                if self._next_due_ts is None or ts - self._next_due_ts >= interval:
                    self._next_due_ts = ts + interval
                else:
                    self._next_due_ts += interval
                self._last_taken_ts = ts
                self._cond.notify_all()

            readings = self.process_frame(frame, rois, ts)
            if readings:
                for r in readings:
                    r["timestamp"] = ts
                self.readings_ready.emit(readings)

    def process_frame(self, frame: np.ndarray, rois: List[ROIConfig], now: Optional[float] = None) -> List[dict]:
        if now is None:
            now = time.time()
        readings = []
        
        # 0. Process Ruler ROIs (rotameter angled ruler)
        ruler_rois = [r for r in rois if r.shape == ROIShape.RULER]
        other_rois = [r for r in rois if r.shape != ROIShape.RULER]

        for ruler in ruler_rois:
            try:
                rel_pos, interp_val, edge_pts = self.ruler_reader.process_ruler_roi(
                    frame, ruler, pipeline=self.pipeline
                )
                roi_key = getattr(ruler, "id", ruler.name)

                if interp_val is not None:
                    last_val = self.last_valid_values.get(roi_key)
                    last_time = self.last_timestamps.get(roi_key, now - 1)
                    dt = now - last_time
                    ocr_res = OCRResult(
                        raw_text=f"s={rel_pos:.3f}" if rel_pos is not None else "",
                        parsed_value=interp_val,
                        confidence=0.95,
                        success=True
                    )
                    val_res = self.validator.validate(ocr_res, ruler, last_val, dt)

                    if val_res.is_valid and val_res.value is not None:
                        self.last_valid_values[roi_key] = val_res.value
                        self.last_timestamps[roi_key] = now

                    readings.append({
                        "roi_id": roi_key,
                        "roi_name": ruler.name,
                        "raw_text": f"s={rel_pos:.3f}" if rel_pos is not None else "",
                        "parsed_value": val_res.value,
                        "unit": ruler.unit,
                        "confidence": 0.95,
                        "is_valid": val_res.is_valid,
                        "reason": val_res.reason,
                        "used_fallback": val_res.used_fallback,
                        "is_child": False,
                        "rel_pos": rel_pos,
                        "edge_pts": edge_pts,
                        "shape": ROIShape.RULER.value
                    })
                else:
                    reason = "Float edge not detected" if rel_pos is None else "Need at least 2 calibration marks"
                    readings.append({
                        "roi_id": roi_key,
                        "roi_name": ruler.name,
                        "raw_text": f"s={rel_pos:.3f}" if rel_pos is not None else "",
                        "parsed_value": None,
                        "unit": ruler.unit,
                        "confidence": 0.0,
                        "is_valid": False,
                        "reason": reason,
                        "used_fallback": False,
                        "is_child": False,
                        "rel_pos": rel_pos,
                        "edge_pts": None,
                        "shape": ROIShape.RULER.value
                    })
            except Exception as e:
                print(f"Error processing ruler ROI {ruler.name}: {e}")

        # 1. Resolve containers and children for other ROIs
        container_children, child_names = self.resolve_hierarchy(other_rois)

        # 2. Process Containers
        containers = [r for r in other_rois if getattr(r, "is_container", False)]
        for container in containers:
            try:
                c_key = getattr(container, "id", container.name)
                children = container_children.get(container.name, [])
                
                if container.display_type == DisplayType.ANALOG:
                    # Analog Rotameter container
                    container_crop = extract_roi_crop(frame, container)
                    if container_crop is None:
                        continue
                    config = PreprocessingConfig.from_dict(container.preprocessing_params)
                    processed = self.pipeline.process(container_crop, config)
                    
                    edge_mode = getattr(container, "ruler_edge", "top")
                    suppress = getattr(container, "suppress_scale_marks", True)
                    core_pct = getattr(container, "core_width_pct", 0.6)
                    min_h = getattr(container, "min_float_height", 8)
                    scale = max(1, getattr(config, "upscale_factor", 1))
                    y_float_scaled = self.rotameter_reader.detect_float_y(
                        processed,
                        edge_mode=edge_mode,
                        suppress_scale_marks=suppress,
                        core_width_pct=core_pct,
                        min_float_height=int(min_h * scale)
                    )
                    y_float = (y_float_scaled / scale) if y_float_scaled is not None else None
                    
                    # Calibration points
                    cal_pts = [list(pt) for pt in getattr(container, "analog_calibration", [])]
                    c_top = max(0, container.coordinates[1]) if container.shape == ROIShape.RECTANGLE else max(0, min(p[1] for p in container.coordinates))
                    for child in children:
                        c_val = getattr(child, "analog_value", None)
                        if c_val is None:
                            c_val = child.value_min
                        if c_val is None:
                            try:
                                c_val = float(child.name)
                            except ValueError:
                                pass
                        if c_val is not None:
                            center_x, center_y = get_roi_center(child)
                            rel_y = center_y - c_top
                            cal_pts.append([rel_y, c_val])

                    if y_float is not None and len(cal_pts) >= 2:
                        interp_val = self.rotameter_reader.interpolate_value(y_float, cal_pts)
                        if interp_val is not None and container.decimal_places is not None:
                            interp_val = round(interp_val, container.decimal_places)
                            
                        last_val = self.last_valid_values.get(c_key)
                        last_time = self.last_timestamps.get(c_key, now - 1)
                        dt = now - last_time
                        ocr_res = OCRResult(raw_text=f"y={y_float:.1f}", parsed_value=interp_val, confidence=0.95, success=True)
                        val_res = self.validator.validate(ocr_res, container, last_val, dt)
                        
                        if val_res.is_valid and val_res.value is not None:
                            self.last_valid_values[c_key] = val_res.value
                            self.last_timestamps[c_key] = now
                            
                        readings.append({
                            "roi_id": c_key,
                            "roi_name": container.name,
                            "raw_text": f"y={y_float:.1f}",
                            "parsed_value": val_res.value,
                            "unit": container.unit,
                            "confidence": 0.95,
                            "is_valid": val_res.is_valid,
                            "reason": val_res.reason,
                            "used_fallback": val_res.used_fallback,
                            "is_child": False,
                            "y_float": y_float
                        })
                    else:
                        reason = "Float edge not detected" if y_float is None else "Need at least 2 calibration points"
                        readings.append({
                            "roi_id": c_key,
                            "roi_name": container.name,
                            "raw_text": f"y={y_float:.1f}" if y_float is not None else "",
                            "parsed_value": None,
                            "unit": container.unit,
                            "confidence": 0.0,
                            "is_valid": False,
                            "reason": reason,
                            "used_fallback": False,
                            "is_child": False,
                            "y_float": y_float
                        })
                        
                    for idx, child in enumerate(children):
                        child_key = getattr(child, "id", child.name)
                        cal_val = getattr(child, "analog_value", child.value_min)
                        readings.append({
                            "roi_id": child_key,
                            "roi_name": child.name,
                            "raw_text": f"Cal: {cal_val}" if cal_val is not None else "CAL_MARK",
                            "parsed_value": cal_val,
                            "unit": container.unit,
                            "confidence": 1.0,
                            "is_valid": True,
                            "reason": "",
                            "used_fallback": False,
                            "is_child": True,
                            "parent_roi": container.name,
                            "slot_index": idx + 1
                        })

                else:
                    # Digital composite container
                    sub_ids = getattr(container, "sub_roi_ids", [])
                    is_rtl = getattr(container, "sort_direction", "ltr") == "rtl"
                    if sub_ids and all(c.name in sub_ids for c in children):
                        id_order = {name: i for i, name in enumerate(sub_ids)}
                        children_sorted = sorted(children, key=lambda c: id_order.get(c.name, 0), reverse=is_rtl)
                    else:
                        children_sorted = sorted(children, key=lambda c: get_roi_x(c), reverse=is_rtl)
                    
                    allow_neg = getattr(container, "allow_negative", True)
                    whitelist = "0123456789-" if allow_neg else "0123456789"
                    
                    # Preprocess all child digit slots first, then OCR them together
                    slots = []
                    for idx, child in enumerate(children_sorted):
                        child_crop = extract_roi_crop(frame, child)
                        if child_crop is None:
                            continue
                        if child.preprocessing_params:
                            c_params = child.preprocessing_params
                        else:
                            # Inherit container filters, but strip coordinate-specific masks
                            c_params = {k: v for k, v in container.preprocessing_params.items() if k != "masks"}
                        config = PreprocessingConfig.from_dict(c_params)
                        slots.append((idx, child, self.pipeline.process(child_crop, config)))

                    def has_valid_char(res: OCRResult) -> bool:
                        return any(ch.isdigit() or (allow_neg and ch == '-') for ch in res.raw_text)

                    # Fallback chain: each pass only re-runs the slots that found nothing yet
                    results: Dict[int, OCRResult] = {}
                    pending = list(range(len(slots)))
                    for ocr_cfg in (f"--psm 10 -l lets -c tessedit_char_whitelist={whitelist}",
                                    f"--psm 10 -c tessedit_char_whitelist={whitelist}",
                                    None):
                        if not pending:
                            break
                        batch = self._recognize_many([slots[i][2] for i in pending], ocr_cfg)
                        for i, res in zip(pending, batch):
                            results[i] = res
                        pending = [i for i in pending if not has_valid_char(results[i])]

                    for i, (idx, child, _) in enumerate(slots):
                        child_key = getattr(child, "id", child.name)
                        ocr_res = results[i]
                        valid_chars = [ch for ch in ocr_res.raw_text if ch.isdigit() or (allow_neg and ch == '-')]
                        if valid_chars:
                            self.digit_slots_cache[child_key] = valid_chars[0]
                            
                        cached_digit = self.digit_slots_cache.get(child_key, "")
                        readings.append({
                            "roi_id": child_key,
                            "roi_name": child.name,
                            "raw_text": cached_digit,
                            "parsed_value": float(cached_digit) if (cached_digit and cached_digit.isdigit()) else None,
                            "unit": "",
                            "confidence": ocr_res.confidence if cached_digit else 0.0,
                            "is_valid": bool(cached_digit),
                            "reason": "" if cached_digit else "No digit in cache",
                            "used_fallback": False,
                            "is_child": True,
                            "parent_roi": container.name,
                            "slot_index": idx + 1
                        })

                    slot_chars = [self.digit_slots_cache.get(getattr(c, "id", c.name), "") for c in children_sorted]
                    dec_pos = getattr(container, "decimal_position", None)
                    dec_places = container.decimal_places
                    allow_leading_blank = getattr(container, "allow_leading_blank", True)

                    first_digit_idx = next((i for i, ch in enumerate(slot_chars) if ch and ch.isdigit()), None)

                    if first_digit_idx is None:
                        missing_all = [str(i + 1) for i, ch in enumerate(slot_chars) if not ch]
                        readings.append({
                            "roi_id": c_key,
                            "roi_name": container.name,
                            "raw_text": "".join(slot_chars),
                            "parsed_value": None,
                            "unit": container.unit,
                            "confidence": 0.0,
                            "is_valid": False,
                            "reason": f"Waiting for slot(s): {', '.join(missing_all)}" if missing_all else "No digits found",
                            "used_fallback": False,
                            "is_child": False
                        })
                    else:
                        has_minus = False
                        pre_digit_invalid = False
                        for i in range(first_digit_idx):
                            if slot_chars[i] == '-' and allow_neg:
                                has_minus = True
                            elif slot_chars[i] != '':
                                pre_digit_invalid = True

                        expected_start = 1 if has_minus else 0
                        missing_leading = [str(i + 1) for i in range(expected_start, first_digit_idx)] if not allow_leading_blank else []
                        internal_missing = [str(i + 1) for i in range(first_digit_idx, len(slot_chars)) if not slot_chars[i]]
                        all_missing = missing_leading + internal_missing

                        if pre_digit_invalid or all_missing:
                            readings.append({
                                "roi_id": c_key,
                                "roi_name": container.name,
                                "raw_text": "".join(slot_chars),
                                "parsed_value": None,
                                "unit": container.unit,
                                "confidence": 0.0,
                                "is_valid": False,
                                "reason": f"Waiting for slot(s): {', '.join(all_missing)}" if all_missing else "Invalid character before digit",
                                "used_fallback": False,
                                "is_child": False
                            })
                        else:
                            digits = [ch for ch in slot_chars[first_digit_idx:] if ch.isdigit()]
                            sign = "-" if has_minus else ""

                            if dec_pos is not None and 0 < dec_pos < len(slot_chars):
                                p1 = "".join(c for c in slot_chars[:dec_pos] if c.isdigit())
                                p2 = "".join(c for c in slot_chars[dec_pos:] if c.isdigit())
                                num_str = sign + (p1 if p1 else "0") + "." + p2
                            elif dec_places is not None and dec_places > 0:
                                pos = len(digits) - dec_places
                                if 0 < pos < len(digits):
                                    num_str = sign + "".join(digits[:pos]) + "." + "".join(digits[pos:])
                                elif pos <= 0:
                                    num_str = sign + "0." + ("0" * abs(pos)) + "".join(digits)
                                else:
                                    num_str = sign + "".join(digits)
                            else:
                                num_str = sign + "".join(digits)

                            try:
                                val = float(num_str)
                                last_val = self.last_valid_values.get(c_key)
                                last_time = self.last_timestamps.get(c_key, now - 1)
                                dt = now - last_time
                                ocr_comp = OCRResult(raw_text=num_str, parsed_value=val, confidence=0.98, success=True)
                                val_res = self.validator.validate(ocr_comp, container, last_val, dt)

                                if val_res.is_valid and val_res.value is not None:
                                    self.last_valid_values[c_key] = val_res.value
                                    self.last_timestamps[c_key] = now

                                readings.append({
                                    "roi_id": c_key,
                                    "roi_name": container.name,
                                    "raw_text": num_str,
                                    "parsed_value": val_res.value,
                                    "unit": container.unit,
                                    "confidence": 0.98,
                                    "is_valid": val_res.is_valid,
                                    "reason": val_res.reason,
                                    "used_fallback": val_res.used_fallback,
                                    "is_child": False
                                })
                            except ValueError:
                                readings.append({
                                    "roi_id": c_key,
                                    "roi_name": container.name,
                                    "raw_text": "".join(slot_chars),
                                    "parsed_value": None,
                                    "unit": container.unit,
                                    "confidence": 0.0,
                                    "is_valid": False,
                                    "reason": "Failed to parse composite number",
                                    "used_fallback": False,
                                    "is_child": False
                                })
            except Exception as e:
                print(f"Error processing container {container.name}: {e}")

        # 3. Process Standalone ROIs (not containers, not ruler, and not children of any container)
        standalone_rois = [r for r in other_rois if not getattr(r, "is_container", False) and r.name not in child_names]
        prepared = []
        for roi in standalone_rois:
            try:
                crop = extract_roi_crop(frame, roi)
                if crop is None:
                    continue
                config = PreprocessingConfig.from_dict(roi.preprocessing_params)
                prepared.append((roi, self.pipeline.process(crop, config)))
            except Exception as e:
                print(f"OCR Error for {roi.name}: {e}")

        ocr_results = self._recognize_many([img for _, img in prepared], None) if prepared else []
        for (roi, _), ocr_res in zip(prepared, ocr_results):
            try:
                roi_key = getattr(roi, "id", roi.name)
                if ocr_res.success and ocr_res.parsed_value is not None and roi.decimal_places is not None:
                    ocr_res.parsed_value = ocr_res.parsed_value / (10 ** roi.decimal_places)
                
                last_val = self.last_valid_values.get(roi_key)
                last_time = self.last_timestamps.get(roi_key, now - 1)
                dt = now - last_time
                val_res = self.validator.validate(ocr_res, roi, last_val, dt)
                
                if val_res.is_valid and val_res.value is not None:
                    self.last_valid_values[roi_key] = val_res.value
                    self.last_timestamps[roi_key] = now
                    
                readings.append({
                    "roi_id": roi_key,
                    "roi_name": roi.name,
                    "raw_text": ocr_res.raw_text,
                    "parsed_value": val_res.value,
                    "unit": roi.unit,
                    "confidence": ocr_res.confidence,
                    "is_valid": val_res.is_valid,
                    "reason": val_res.reason,
                    "used_fallback": val_res.used_fallback,
                    "is_child": False
                })
            except Exception as e:
                print(f"OCR Error for {roi.name}: {e}")
                
        return readings

            
    def stop(self):
        with self._cond:
            self.running = False
            self._cond.notify_all()
        self.wait()

