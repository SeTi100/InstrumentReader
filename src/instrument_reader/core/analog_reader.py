import numpy as np
import cv2
from typing import Optional, List, Tuple, Union, Any, Dict

class RotameterReader:
    """
    Reader for analog variable-area flowmeters (rotameters) and similar scale instruments.
    Detects the float's position inside the vertical tube and performs piecewise linear
    interpolation between calibration marks to determine the measurement value.
    """

    def __init__(
        self,
        float_color: str = "auto",
        margin_x_pct: float = 0.15,
        min_gradient: float = 1.5,
        suppress_scale_marks: bool = True,
        core_width_pct: float = 0.6,
        min_float_height: int = 8
    ):
        """
        Args:
            float_color: "dark" (dark float on light background), 
                         "bright" (bright float on dark background),
                         or "auto" (strongest gradient transition).
            margin_x_pct: Fraction of width to exclude from left and right margins
                          to avoid glass wall reflections and scale markings (for 1D gradient mode).
            min_gradient: Minimum gradient magnitude required to declare a float edge detected.
            suppress_scale_marks: When True, uses 2D connected-component analysis and core-width
                                  filtering to completely eliminate thin scale tick marks and glass markings.
                                  When False, runs classic 1D gradient row-profiling.
            core_width_pct: Fraction of strip width (centered) to analyze in 2D mode (0.1 to 1.0).
            min_float_height: Minimum vertical pixel height of the float body in 2D mode.
        """
        self.float_color = float_color
        self.margin_x_pct = margin_x_pct
        self.min_gradient = min_gradient
        self.suppress_scale_marks = suppress_scale_marks
        self.core_width_pct = core_width_pct
        self.min_float_height = min_float_height

    def _compute_1d_gradient(
        self,
        gray: np.ndarray,
        mode: str = "top"
    ) -> Tuple[
        np.ndarray,
        Optional[np.ndarray],
        np.ndarray,
        int,
        List[Dict[str, Any]],
        Optional[float],
        str
    ]:
        """
        Computes 1D vertical intensity profile, smoothed first derivative (gradient),
        and candidate peak transitions.
        """
        h, w = gray.shape
        mx = int(w * self.margin_x_pct)
        if w - 2 * mx >= 3:
            strip = gray[:, mx:w-mx]
        else:
            strip = gray

        # Compute row-wise average profile
        profile = np.mean(strip.astype(np.float32), axis=1)

        # Smooth profile using Gaussian kernel to eliminate noise
        k_size = max(3, (int(h * 0.03) | 1))
        kernel = cv2.getGaussianKernel(k_size, -1).flatten()
        smoothed = np.convolve(profile, kernel, mode='same')

        # First derivative along vertical axis (y)
        grad = np.gradient(smoothed)

        # Ignore top and bottom 2% to avoid border artifacts
        pad = max(2, int(h * 0.02))
        if h - pad <= pad:
            pad = 0
            
        valid_range = slice(pad, h - pad if pad > 0 else h)
        grad_valid = grad[valid_range]

        if len(grad_valid) == 0:
            return profile, grad_valid, grad, pad, [], None, "Gradientenbereich nach Randabzug leer"

        abs_grad = np.abs(grad_valid)
        max_mag = float(np.max(abs_grad))
        if max_mag < self.min_gradient:
            return profile, grad_valid, grad, pad, [], None, f"Maximaler Gradient ({max_mag:.1f}) unter Schwellenwert ({self.min_gradient:.1f})"

        thresh = max(self.min_gradient, max_mag * 0.30)

        # Find significant local extrema (candidate transitions) in abs_grad
        peak_indices = []
        for i in range(len(grad_valid)):
            if abs_grad[i] >= thresh:
                is_local_max = True
                if i > 0 and abs_grad[i] < abs_grad[i - 1]:
                    is_local_max = False
                if i < len(grad_valid) - 1 and abs_grad[i] < abs_grad[i + 1]:
                    is_local_max = False
                if is_local_max:
                    peak_indices.append(i)

        if not peak_indices:
            peak_indices = [int(np.argmax(abs_grad))]

        # Filter candidates by float_color if explicitly defined
        top_candidates = []
        bottom_candidates = []

        for idx in peak_indices:
            val = grad_valid[idx]
            if self.float_color == "dark":
                # Dark float in bright fluid: enters from top with negative gradient drop
                if val <= -self.min_gradient:
                    top_candidates.append(idx)
                # Exits at bottom with positive gradient rise
                if val >= self.min_gradient:
                    bottom_candidates.append(idx)
            elif self.float_color == "bright":
                # Bright float in dark fluid: enters from top with positive gradient rise
                if val >= self.min_gradient:
                    top_candidates.append(idx)
                # Exits at bottom with negative gradient drop
                if val <= -self.min_gradient:
                    bottom_candidates.append(idx)
            else:
                top_candidates.append(idx)
                bottom_candidates.append(idx)

        # Fallbacks if directional filtering yielded no candidates
        if not top_candidates:
            top_candidates = [peak_indices[0]]
        if not bottom_candidates:
            bottom_candidates = [peak_indices[-1]]

        idx_top = min(top_candidates)
        idx_bottom = max(bottom_candidates)

        if mode == "bottom":
            best_idx = idx_bottom
        elif mode == "center":
            best_idx = (idx_top + idx_bottom) / 2.0
        else:  # "top" default
            best_idx = idx_top

        y_res = float(best_idx + pad)

        candidates_1d = []
        for idx in peak_indices:
            val = float(grad_valid[idx])
            y_pos = int(round(idx + pad))
            if mode == "center":
                is_sel = bool(idx == idx_top or idx == idx_bottom)
            else:
                is_sel = bool(abs(idx - best_idx) < 0.5)
            candidates_1d.append({
                "y": y_pos,
                "idx": int(idx),
                "grad": val,
                "selected": is_sel
            })

        cand_str = ", ".join([f"y={c['y']}(g={c['grad']:+.1f})" for c in candidates_1d[:5]])
        reason = f"1D-Gradient: Peak bei y={y_res:.1f} gewählt ({len(candidates_1d)} Peaks >= {thresh:.1f}: {cand_str})"
        return profile, grad_valid, grad, pad, candidates_1d, y_res, reason

    def detect_float_y(
        self,
        image: np.ndarray,
        edge_mode: str = "top",
        suppress_scale_marks: Optional[bool] = None,
        core_width_pct: Optional[float] = None,
        min_float_height: Optional[int] = None,
        return_box: bool = False,
        return_debug: bool = False
    ) -> Union[
        Optional[float],
        Tuple[Optional[float], Optional[Tuple[int, int, int, int]]],
        Dict[str, Any]
    ]:
        """
        Detects the float's position inside the vertical tube.
        
        Args:
            image: Rectified vertical image of the tube (oriented from top to bottom).
            edge_mode: "top" (Oberkante), "bottom" (Unterkante), or "center" (Mitte).
            suppress_scale_marks: Whether to suppress printed scale marks using 2D object filtering.
            core_width_pct: Fraction of tube width (centered) to analyze in 2D mode.
            min_float_height: Minimum pixel height of the float body in 2D mode.
            return_box: If True, returns (y_float, (x, y, w, h)) bounding box of detected float blob.
            return_debug: If True, returns a structured diagnostic dict containing:
                          mode, y_float, box, profile, gradient, grad_full, pad,
                          candidates_1d, blobs, core_bounds, and reason.
            
        Returns:
            y float coordinate in pixels (or (y, box) if return_box=True), or debug dict if return_debug=True,
            or None if no float detected.
        """
        suppress = self.suppress_scale_marks if suppress_scale_marks is None else suppress_scale_marks
        core_pct = self.core_width_pct if core_width_pct is None else core_width_pct
        min_h = self.min_float_height if min_float_height is None else min_float_height
        mode = (edge_mode or "top").lower().strip()

        if image is None or image.size == 0:
            if return_debug:
                return {
                    "mode": "2d" if suppress else "1d",
                    "y_float": None,
                    "box": None,
                    "profile": None,
                    "gradient": None,
                    "grad_valid": None,
                    "grad_full": None,
                    "pad": 0,
                    "pad_slice": slice(0, 0),
                    "candidates_1d": [],
                    "blobs": [],
                    "core_bounds": None,
                    "reason": "Image is empty"
                }
            return (None, None) if return_box else None

        # Convert to grayscale if needed
        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image.copy()

        h, w = gray.shape
        if h < 5 or w < 3:
            if return_debug:
                return {
                    "mode": "2d" if suppress else "1d",
                    "y_float": None,
                    "box": None,
                    "profile": None,
                    "gradient": None,
                    "grad_valid": None,
                    "grad_full": None,
                    "pad": 0,
                    "pad_slice": slice(0, 0),
                    "candidates_1d": [],
                    "blobs": [],
                    "core_bounds": None,
                    "reason": "Image dimensions too small (< 5x3)"
                }
            return (None, None) if return_box else None

        # -------------------------------------------------------------
        # 1. 2D Object Analysis / Scale Mark Suppression (when active)
        # -------------------------------------------------------------
        all_blobs: List[Dict[str, Any]] = []
        candidates_2d: List[Dict[str, Any]] = []
        core_bounds: Optional[Tuple[int, int]] = None

        if suppress:
            cw = max(3, int(round(w * max(0.1, min(1.0, core_pct)))))
            cx = (w - 1) / 2.0
            x_start = max(0, int(round(cx - (cw - 1) / 2.0)))
            x_end = min(w, x_start + cw)
            core_bounds = (x_start, x_end)
            core_strip = gray[:, x_start:x_end]
            ch_h, ch_w = core_strip.shape

            std_dev = float(np.std(core_strip))
            if std_dev >= 2.0 and ch_h >= 5 and ch_w >= 2:
                unique_vals = np.unique(core_strip)
                is_binary = len(unique_vals) <= 2 and (0 in unique_vals or 255 in unique_vals)

                bin_masks = []
                if is_binary:
                    if self.float_color == "bright":
                        bin_masks = [(core_strip == 255).astype(np.uint8) * 255]
                    elif self.float_color == "dark":
                        bin_masks = [(core_strip == 0).astype(np.uint8) * 255]
                    else:
                        m_dark = (core_strip == 0).astype(np.uint8) * 255
                        m_bright = (core_strip == 255).astype(np.uint8) * 255
                        p_dark = np.count_nonzero(m_dark) / float(ch_h * ch_w)
                        p_bright = np.count_nonzero(m_bright) / float(ch_h * ch_w)
                        if 0.02 <= p_dark <= 0.60 and p_bright > p_dark:
                            bin_masks = [m_dark]
                        elif 0.02 <= p_bright <= 0.60 and p_dark > p_bright:
                            bin_masks = [m_bright]
                        else:
                            bin_masks = [m_dark, m_bright]
                else:
                    k_blur = 3
                    blurred = cv2.GaussianBlur(core_strip, (k_blur, k_blur), 0)
                    otsu_val, _ = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

                    if self.float_color == "dark":
                        bin_masks = [((blurred < otsu_val).astype(np.uint8) * 255)]
                    elif self.float_color == "bright":
                        bin_masks = [((blurred >= otsu_val).astype(np.uint8) * 255)]
                    else:
                        mask_dark = ((blurred < otsu_val).astype(np.uint8) * 255)
                        mask_bright = ((blurred >= otsu_val).astype(np.uint8) * 255)
                        p_dark = np.count_nonzero(mask_dark) / float(ch_h * ch_w)
                        p_bright = np.count_nonzero(mask_bright) / float(ch_h * ch_w)
                        if 0.02 <= p_dark <= 0.60 and p_bright > p_dark:
                            bin_masks = [mask_dark]
                        elif 0.02 <= p_bright <= 0.60 and p_dark > p_bright:
                            bin_masks = [mask_bright]
                        else:
                            bin_masks = [mask_dark, mask_bright]

                close_k = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 3))

                for m in bin_masks:
                    closed = cv2.morphologyEx(m, cv2.MORPH_CLOSE, close_k)
                    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(closed, connectivity=8)

                    for label_idx in range(1, num_labels):
                        bx, by, bw, bh, barea = stats[label_idx]

                        # Ignore border-spanning strips (spans nearly entire height of strip)
                        pad_2d = max(1, int(ch_h * 0.02))
                        is_border = (by <= pad_2d and (by + bh) >= ch_h - pad_2d)

                        # Height filter: thin scale ticks (1-3 px) are suppressed
                        passes_height = (bh >= min_h)

                        # Area filter: must be large enough to be a physical float body
                        passes_area = (barea >= min_h * 2)

                        is_float = (not is_border) and passes_height and passes_area

                        blob_dict = {
                            "box": (int(bx + x_start), int(by), int(bw), int(bh)),
                            "area": int(barea),
                            "is_float": bool(is_float),
                            "selected": False,
                        }
                        all_blobs.append(blob_dict)

                        if is_float:
                            candidates_2d.append({
                                "x": bx + x_start,
                                "y": by,
                                "w": bw,
                                "h": bh,
                                "area": barea,
                                "centroid_y": float(centroids[label_idx][1]),
                                "blob_dict": blob_dict,
                            })

                if candidates_2d:
                    # Pick largest candidate by area
                    best = max(candidates_2d, key=lambda c: c["area"])
                    best["blob_dict"]["selected"] = True
                    by = best["y"]
                    bh = best["h"]
                    box = (int(best["x"]), int(best["y"]), int(best["w"]), int(best["h"]))

                    if mode == "bottom":
                        y_res = float(by + bh - 1)
                    elif mode == "center":
                        y_res = float(by + (bh - 1) / 2.0)
                    else:  # "top"
                        y_res = float(by)

                    if return_debug:
                        profile, grad_valid, grad_full, pad_1d, candidates_1d, _, _ = self._compute_1d_gradient(gray, mode)
                        suppressed_count = len(all_blobs) - len(candidates_2d)
                        # Ensure 1D candidates do not falsely claim to be selected float in 2D mode
                        for c in candidates_1d:
                            c["selected"] = bool(abs(c["y"] - y_res) < 1.0)
                        return {
                            "mode": "2d",
                            "y_float": y_res,
                            "box": box,
                            "profile": profile,
                            "gradient": grad_full,
                            "grad_valid": grad_valid,
                            "grad_full": grad_full,
                            "pad": pad_1d,
                            "pad_slice": slice(pad_1d, h - pad_1d if pad_1d > 0 else h),
                            "candidates_1d": candidates_1d,
                            "blobs": all_blobs,
                            "core_bounds": core_bounds,
                            "reason": f"2D-Filter: Schwimmerkörper erkannt (Box={box}, Fläche={best['area']} px², {suppressed_count} Striche/Störungen unterdrückt)"
                        }

                    return (y_res, box) if return_box else y_res

        # -------------------------------------------------------------
        # 2. 1D Gradient Profile Method (Classic mode / Fallback)
        # -------------------------------------------------------------
        profile, grad_valid, grad_full, pad_1d, candidates_1d, y_res_1d, reason_1d = self._compute_1d_gradient(gray, mode)

        if return_debug:
            if suppress:
                reason = f"2D-Filter fand keinen Schwimmerkörper ({len(all_blobs)} Komponenten unterdrückt) -> Fallback auf 1D: {reason_1d}"
            else:
                reason = reason_1d

            return {
                "mode": "1d",
                "y_float": y_res_1d,
                "box": None,
                "profile": profile,
                "gradient": grad_full,
                "grad_valid": grad_valid,
                "grad_full": grad_full,
                "pad": pad_1d,
                "pad_slice": slice(pad_1d, h - pad_1d if pad_1d > 0 else h),
                "candidates_1d": candidates_1d,
                "blobs": all_blobs if suppress else [],
                "core_bounds": core_bounds if suppress else None,
                "reason": reason
            }

        return (y_res_1d, None) if return_box else y_res_1d


    @staticmethod
    def interpolate_value(y_float: Optional[float], calibration_points: Union[List[Tuple[float, float]], List[List[float]]]) -> Optional[float]:
        """
        Performs piecewise linear interpolation given calibration points [(y0, val0), (y1, val1), ...].
        Works correctly whether the scale increases upwards or downwards.
        
        Args:
            y_float: The detected vertical float position in pixels.
            calibration_points: List of (y_coordinate, measurement_value) pairs.
            
        Returns:
            Interpolated measurement value, or None if invalid.
        """
        if y_float is None or not calibration_points:
            return None

        # Clean and convert points to (float, float)
        pts: List[Tuple[float, float]] = []
        for p in calibration_points:
            try:
                y, v = float(p[0]), float(p[1])
                pts.append((y, v))
            except (ValueError, IndexError, TypeError):
                continue

        if not pts:
            return None
        if len(pts) == 1:
            return pts[0][1]

        # Sort strictly by y-coordinate
        pts.sort(key=lambda pt: pt[0])

        # Deduplicate points with identical or near-identical y-coordinates (average their values)
        dedup_pts: List[Tuple[float, float]] = []
        for y, v in pts:
            if dedup_pts and abs(dedup_pts[-1][0] - y) < 1e-4:
                prev_y, prev_v = dedup_pts[-1]
                dedup_pts[-1] = (prev_y, (prev_v + v) / 2.0)
            else:
                dedup_pts.append((y, v))
        pts = dedup_pts

        if len(pts) == 1:
            return pts[0][1]

        # Above/below outer bounds: linearly extrapolate from nearest boundary segment
        if y_float <= pts[0][0]:
            y0, v0 = pts[0]
            y1, v1 = pts[1]
            if y1 == y0:
                return v0
            return v0 + (y_float - y0) * (v1 - v0) / (y1 - y0)

        if y_float >= pts[-1][0]:
            y0, v0 = pts[-2]
            y1, v1 = pts[-1]
            if y1 == y0:
                return v1
            return v0 + (y_float - y0) * (v1 - v0) / (y1 - y0)

        # In-between: find bounding interval [pts[i], pts[i+1]]
        for i in range(len(pts) - 1):
            y0, v0 = pts[i]
            y1, v1 = pts[i + 1]
            if y0 <= y_float <= y1:
                if y1 == y0:
                    return v0
                fraction = (y_float - y0) / (y1 - y0)
                return v0 + fraction * (v1 - v0)

        return pts[-1][1]

    def read(
        self,
        image: np.ndarray,
        calibration_points: list,
        edge_mode: str = "top",
        suppress_scale_marks: Optional[bool] = None,
        core_width_pct: Optional[float] = None,
        min_float_height: Optional[int] = None,
        return_debug: bool = False
    ) -> Union[
        Tuple[Optional[float], Optional[float]],
        Tuple[Optional[float], Optional[float], Optional[Dict[str, Any]]]
    ]:
        """
        Detects float position and interpolates value.
        Returns: (y_float, value) or (y_float, value, debug_dict) if return_debug=True.
        """
        if return_debug:
            debug_info = self.detect_float_y(
                image,
                edge_mode=edge_mode,
                suppress_scale_marks=suppress_scale_marks,
                core_width_pct=core_width_pct,
                min_float_height=min_float_height,
                return_debug=True
            )
            y_float = debug_info.get("y_float") if isinstance(debug_info, dict) else None
            if y_float is None:
                return None, None, debug_info
            val = self.interpolate_value(y_float, calibration_points)
            return y_float, val, debug_info

        y_float = self.detect_float_y(
            image,
            edge_mode=edge_mode,
            suppress_scale_marks=suppress_scale_marks,
            core_width_pct=core_width_pct,
            min_float_height=min_float_height
        )
        if y_float is None:
            return None, None
        val = self.interpolate_value(y_float, calibration_points)
        return y_float, val


class RotameterRulerReader(RotameterReader):
    """
    Reader for variable-area flowmeters (rotameters) aligned along an arbitrary axis (ruler).
    Handles arbitrary tilt angles theta by performing affine rectification on the tube strip,
    detects the float edge in the rectified coordinates, and performs piecewise linear
    interpolation between calibration marks along the axis.
    """

    @staticmethod
    def get_endpoints_from_roi(roi: Any) -> Optional[Tuple[Tuple[float, float], Tuple[float, float]]]:
        coords = getattr(roi, "coordinates", [])
        if not coords:
            return None
        if len(coords) == 2 and isinstance(coords[0], (list, tuple)):
            return (float(coords[0][0]), float(coords[0][1])), (float(coords[1][0]), float(coords[1][1]))
        elif len(coords) == 4 and not isinstance(coords[0], (list, tuple)):
            return (float(coords[0]), float(coords[1])), (float(coords[2]), float(coords[3]))
        return None

    @staticmethod
    def extract_rectified_strip(
        frame: np.ndarray,
        p1: Union[Tuple[float, float], List[float]],
        p2: Union[Tuple[float, float], List[float]],
        strip_width: float = 30.0
    ) -> Optional[np.ndarray]:
        """
        Extracts an affine-rectified vertical image strip of the tube between p1 and p2.
        p1 maps to the top row (y'=0), p2 maps to the bottom row (y'=H-1).
        The width of the rectified strip corresponds to strip_width pixels.
        """
        if frame is None or frame.size == 0:
            return None

        p1_arr = np.array(p1, dtype=np.float32)
        p2_arr = np.array(p2, dtype=np.float32)
        v = p2_arr - p1_arr
        L = float(np.linalg.norm(v))
        if L < 2.0:
            return None

        u = v / L  # Unit vector along axis: (ux, uy)
        n = np.array([-u[1], u[0]], dtype=np.float32)  # Unit normal: (-uy, ux)

        H = max(2, int(round(L)))
        W = max(2, int(round(strip_width)))
        cx_rect = (W - 1) / 2.0

        # Corresponding 3 points in source frame
        pt0_frame = p1_arr - cx_rect * n
        pt1_frame = p1_arr + (float(W - 1) - cx_rect) * n
        pt2_frame = p2_arr - cx_rect * n

        src_tri = np.float32([pt0_frame, pt1_frame, pt2_frame])
        dst_tri = np.float32([[0.0, 0.0], [float(W - 1), 0.0], [0.0, float(H - 1)]])

        M = cv2.getAffineTransform(src_tri, dst_tri)
        rectified = cv2.warpAffine(frame, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        return rectified

    @staticmethod
    def compute_edge_points(
        p1: Union[Tuple[float, float], List[float]],
        p2: Union[Tuple[float, float], List[float]],
        strip_width: float,
        rel_pos: float
    ) -> Tuple[Tuple[float, float], Tuple[float, float]]:
        """
        Computes the line segment (in original frame coordinates) representing the float edge
        perpendicular to the axis at relative position rel_pos in [0.0, 1.0].
        """
        p1_arr = np.array(p1, dtype=np.float32)
        p2_arr = np.array(p2, dtype=np.float32)
        v = p2_arr - p1_arr
        L = float(np.linalg.norm(v))
        if L < 1e-4:
            return ((float(p1_arr[0]), float(p1_arr[1])), (float(p2_arr[0]), float(p2_arr[1])))

        u = v / L
        n = np.array([-u[1], u[0]], dtype=np.float32)
        p_center = p1_arr + rel_pos * v
        half_w = float(strip_width) / 2.0

        e1 = p_center - half_w * n
        e2 = p_center + half_w * n
        return ((float(e1[0]), float(e1[1])), (float(e2[0]), float(e2[1])))

    @staticmethod
    def is_p2_top(
        p1: Union[Tuple[float, float], List[float]],
        p2: Union[Tuple[float, float], List[float]],
        calibration_marks: Optional[List[dict]] = None
    ) -> bool:
        """
        Determines whether p2 is the top of the tube (direction of rising flow/higher scale value).
        In rotameters, scale values increase towards the top of the tube.
        If calibration marks are not informative, physical vertical direction in the image
        (smaller y coordinate = physically higher) is used.
        """
        if calibration_marks and len(calibration_marks) >= 2:
            try:
                sorted_m = sorted(calibration_marks, key=lambda m: float(m.get("pos", 0.0)))
                val_start = float(sorted_m[0].get("value", 0.0))
                val_end = float(sorted_m[-1].get("value", 0.0))
                if abs(val_end - val_start) > 1e-4:
                    return val_end > val_start
            except (ValueError, TypeError):
                pass
        return float(p2[1]) < float(p1[1])

    @staticmethod
    def interpolate_ruler_value(
        rel_pos: Optional[float],
        calibration_marks: List[dict]
    ) -> Optional[float]:
        """
        Performs piecewise linear interpolation/extrapolation along the axis given calibration marks
        in the format [{'pos': float, 'value': float}, ...].
        Works for any orientation and direction of increasing scale values.
        Requires at least 2 distinct calibration positions.
        """
        if rel_pos is None or not calibration_marks:
            return None

        cleaned_marks: List[Tuple[float, float]] = []
        for m in calibration_marks:
            try:
                pos = float(m["pos"])
                val = float(m["value"])
                cleaned_marks.append((pos, val))
            except (KeyError, TypeError, ValueError):
                continue

        if len(cleaned_marks) < 2:
            return None

        cleaned_marks.sort(key=lambda pt: pt[0])

        # Deduplicate points with identical pos
        dedup: List[Tuple[float, float]] = []
        for p, v in cleaned_marks:
            if dedup and abs(dedup[-1][0] - p) < 1e-5:
                prev_p, prev_v = dedup[-1]
                dedup[-1] = (prev_p, (prev_v + v) / 2.0)
            else:
                dedup.append((p, v))
        marks = dedup

        if len(marks) < 2:
            return None

        # Extrapolate below minimum calibration mark
        if rel_pos <= marks[0][0]:
            p0, v0 = marks[0]
            p1, v1 = marks[1]
            if p1 == p0:
                return v0
            return v0 + (rel_pos - p0) * (v1 - v0) / (p1 - p0)

        # Extrapolate above maximum calibration mark
        if rel_pos >= marks[-1][0]:
            p0, v0 = marks[-2]
            p1, v1 = marks[-1]
            if p1 == p0:
                return v1
            return v0 + (rel_pos - p0) * (v1 - v0) / (p1 - p0)

        # In-between: find segment [marks[i], marks[i+1]]
        for i in range(len(marks) - 1):
            p0, v0 = marks[i]
            p1, v1 = marks[i + 1]
            if p0 <= rel_pos <= p1:
                if p1 == p0:
                    return v0
                fraction = (rel_pos - p0) / (p1 - p0)
                return v0 + fraction * (v1 - v0)

        return marks[-1][1]

    def process_ruler_roi(
        self,
        frame: np.ndarray,
        roi: Any,
        pipeline: Any = None,
        return_debug: bool = False
    ) -> Union[
        Tuple[Optional[float], Optional[float], Optional[Tuple[Tuple[float, float], Tuple[float, float]]]],
        Tuple[Optional[float], Optional[float], Optional[Tuple[Tuple[float, float], Tuple[float, float]]], Optional[Dict[str, Any]]]
    ]:
        """
        Processes a ruler ROI on a given frame:
        1. Rectifies the tube strip oriented from top to bottom (fluid to float reading edge).
        2. Applies preprocessing pipeline if configured.
        3. Detects float position.
        4. Interpolates value from calibration marks.
        5. Computes edge points in original coordinates.

        Returns:
            (rel_pos, interpolated_value, edge_points) if not return_debug,
            or (rel_pos, interpolated_value, edge_points, debug_info) if return_debug=True.
        """
        endpoints = self.get_endpoints_from_roi(roi)
        if endpoints is None:
            return (None, None, None, None) if return_debug else (None, None, None)

        p1, p2 = endpoints
        strip_width = getattr(roi, "strip_width", 30.0)
        calibration_marks = getattr(roi, "calibration_marks", [])

        is_p2_top = self.is_p2_top(p1, p2, calibration_marks)
        p_top, p_bottom = (p2, p1) if is_p2_top else (p1, p2)

        rectified = self.extract_rectified_strip(frame, p_top, p_bottom, strip_width)
        if rectified is None:
            return (None, None, None, None) if return_debug else (None, None, None)

        edge_mode = getattr(roi, "ruler_edge", "top")
        suppress_scale = getattr(roi, "suppress_scale_marks", True)
        core_pct = getattr(roi, "core_width_pct", 0.6)
        min_h = getattr(roi, "min_float_height", 8)

        debug_info: Optional[Dict[str, Any]] = None

        if getattr(roi, "preprocessing_params", None) and pipeline is not None:
            from instrument_reader.core.preprocessing import PreprocessingConfig
            config = PreprocessingConfig.from_dict(roi.preprocessing_params)
            processed = pipeline.process(rectified, config)
            scale = max(1, getattr(config, "upscale_factor", 1))
            if return_debug:
                debug_info = self.detect_float_y(
                    processed,
                    edge_mode=edge_mode,
                    suppress_scale_marks=suppress_scale,
                    core_width_pct=core_pct,
                    min_float_height=int(min_h * scale),
                    return_debug=True
                )
                y_scaled = debug_info.get("y_float")
            else:
                y_scaled = self.detect_float_y(
                    processed,
                    edge_mode=edge_mode,
                    suppress_scale_marks=suppress_scale,
                    core_width_pct=core_pct,
                    min_float_height=int(min_h * scale)
                )
            y_float = (y_scaled / scale) if y_scaled is not None else None
        else:
            if return_debug:
                debug_info = self.detect_float_y(
                    rectified,
                    edge_mode=edge_mode,
                    suppress_scale_marks=suppress_scale,
                    core_width_pct=core_pct,
                    min_float_height=min_h,
                    return_debug=True
                )
                y_float = debug_info.get("y_float")
            else:
                y_float = self.detect_float_y(
                    rectified,
                    edge_mode=edge_mode,
                    suppress_scale_marks=suppress_scale,
                    core_width_pct=core_pct,
                    min_float_height=min_h
                )

        if y_float is None:
            return (None, None, None, debug_info) if return_debug else (None, None, None)

        H = rectified.shape[0]
        frac_from_top = y_float / (H - 1) if H > 1 else 0.0
        rel_pos = (1.0 - frac_from_top) if is_p2_top else frac_from_top

        interp_val = self.interpolate_ruler_value(rel_pos, calibration_marks)
        if interp_val is not None and getattr(roi, "decimal_places", None) is not None:
            interp_val = round(interp_val, roi.decimal_places)

        edge_pts = self.compute_edge_points(p1, p2, strip_width, rel_pos)
        return (rel_pos, interp_val, edge_pts, debug_info) if return_debug else (rel_pos, interp_val, edge_pts)

