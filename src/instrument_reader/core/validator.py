from dataclasses import dataclass
from .roi import ROIConfig
from .ocr_engine import OCRResult

@dataclass
class ValidationResult:
    is_valid: bool
    value: float | None
    reason: str = ""
    used_fallback: bool = False

class ValueValidator:
    def validate(self, ocr_result: OCRResult, roi_config: ROIConfig,
                 last_valid: float | None, time_delta: float) -> ValidationResult:
        if not ocr_result.success or ocr_result.parsed_value is None:
            return ValidationResult(False, last_valid, "OCR_FAILED", used_fallback=True)
        
        value = ocr_result.parsed_value
        
        if roi_config.value_min is not None and value < roi_config.value_min:
            return ValidationResult(False, last_valid, "BELOW_MIN", used_fallback=True)
        if roi_config.value_max is not None and value > roi_config.value_max:
            return ValidationResult(False, last_valid, "ABOVE_MAX", used_fallback=True)
        
        if (last_valid is not None and roi_config.max_delta_per_sec is not None 
            and time_delta > 0):
            delta = abs(value - last_valid) / time_delta
            if delta > roi_config.max_delta_per_sec:
                return ValidationResult(False, last_valid, "DELTA_TOO_LARGE", used_fallback=True)
        
        return ValidationResult(True, value)
