from dataclasses import dataclass, field
import cv2
import numpy as np

@dataclass
class PreprocessingConfig:
    grayscale: bool = True
    invert: bool = False
    threshold_method: str = "otsu"
    threshold_value: int = 128
    blur_kernel: int = 0
    contrast_alpha: float = 1.0
    contrast_beta: int = 0
    morphology_op: str = "none"
    morphology_kernel: int = 3
    rotation_angle: float = 0.0
    upscale_factor: int = 1               # 1, 2, 4
    morphology_shape: str = "rect"        # "rect", "cross", "vertical", "horizontal"
    masks: list[dict] = field(default_factory=list)  # [{"type": "rect", "coords": [x, y, w, h], "color": 0}, ...]
    # Rotameter Ruler inspection fields
    ruler_edge: str = "top"
    suppress_scale_marks: bool = True
    core_width_pct: float = 0.6
    min_float_height: int = 8

    @classmethod
    def from_dict(cls, data: dict):
        if not data:
            return cls()
        from dataclasses import fields
        valid_names = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in data.items() if k in valid_names}
        return cls(**filtered)

class PreprocessingPipeline:
    def process(self, image: np.ndarray, config: PreprocessingConfig) -> np.ndarray:
        if image is None or image.size == 0:
            return image

        result = image.copy()
        
        # 1. Grayscale
        if config.grayscale and len(result.shape) == 3:
            result = cv2.cvtColor(result, cv2.COLOR_BGR2GRAY)
            
        # 2. Upscaling (before morphology/thresholding so operations are gentler)
        scale = max(1, getattr(config, "upscale_factor", 1))
        if scale > 1:
            result = cv2.resize(result, (0, 0), fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            
        # 3. Contrast adjustment
        if config.contrast_alpha != 1.0 or config.contrast_beta != 0:
            result = cv2.convertScaleAbs(result, alpha=config.contrast_alpha, beta=config.contrast_beta)
            
        # 4. Blur
        if config.blur_kernel > 0:
            k = config.blur_kernel | 1
            result = cv2.GaussianBlur(result, (k, k), 0)
            
        # 5. Invert
        if config.invert:
            result = cv2.bitwise_not(result)
        
        # 6. Thresholding
        if config.threshold_method == "otsu":
            if len(result.shape) == 3:
                gray_thresh = cv2.cvtColor(result, cv2.COLOR_BGR2GRAY)
            else:
                gray_thresh = result
            _, result = cv2.threshold(gray_thresh, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        elif config.threshold_method == "adaptive":
            if len(result.shape) == 3:
                gray_thresh = cv2.cvtColor(result, cv2.COLOR_BGR2GRAY)
            else:
                gray_thresh = result
            result = cv2.adaptiveThreshold(gray_thresh, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                           cv2.THRESH_BINARY, 11, 2)
        elif config.threshold_method == "fixed":
            _, result = cv2.threshold(result, config.threshold_value, 255, cv2.THRESH_BINARY)
            
        # 7. Morphology
        if config.morphology_op != "none" and config.morphology_kernel > 0:
            k_size = config.morphology_kernel
            shape = getattr(config, "morphology_shape", "rect")
            if shape == "cross":
                k_odd = k_size if k_size % 2 == 1 else k_size + 1
                kernel = cv2.getStructuringElement(cv2.MORPH_CROSS, (k_odd, k_odd))
            elif shape == "vertical":
                kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, k_size))
            elif shape == "horizontal":
                kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (k_size, 1))
            else:
                kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (k_size, k_size))
                
            if config.morphology_op == "dilate":
                result = cv2.dilate(result, kernel, iterations=1)
            elif config.morphology_op == "erode":
                result = cv2.erode(result, kernel, iterations=1)
            elif config.morphology_op == "open":
                result = cv2.morphologyEx(result, cv2.MORPH_OPEN, kernel)
            elif config.morphology_op == "close":
                result = cv2.morphologyEx(result, cv2.MORPH_CLOSE, kernel)
                
        # 8. Rotation
        if config.rotation_angle != 0.0:
            h, w = result.shape[:2]
            M = cv2.getRotationMatrix2D((w/2, h/2), config.rotation_angle, 1.0)
            result = cv2.warpAffine(result, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
            
        # 9. Manual masks (coords in unscaled crop coordinates: [x, y, w, h])
        masks = getattr(config, "masks", None)
        if masks:
            h_res, w_res = result.shape[:2]
            for m in masks:
                if m.get("type", "rect") == "rect":
                    coords = m.get("coords", [])
                    if len(coords) == 4:
                        mx, my, mw, mh = coords
                        x1 = max(0, int(round(mx * scale)))
                        y1 = max(0, int(round(my * scale)))
                        x2 = min(w_res, int(round((mx + mw) * scale)))
                        y2 = min(h_res, int(round((my + mh) * scale)))
                        if x2 > x1 and y2 > y1:
                            col = int(m.get("color", 0))
                            if len(result.shape) == 3:
                                result[y1:y2, x1:x2] = (col, col, col)
                            else:
                                result[y1:y2, x1:x2] = col
        
        return result
