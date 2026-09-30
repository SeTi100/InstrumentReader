from abc import ABC, abstractmethod
from dataclasses import dataclass
import numpy as np
import re

@dataclass
class OCRResult:
    """Ergebnis einer OCR-Erkennung."""
    raw_text: str
    parsed_value: float | None
    confidence: float
    success: bool

class OCREngine(ABC):
    @abstractmethod
    def recognize(self, image: np.ndarray, config: str | None = None) -> OCRResult: ...

class TesseractEngine(OCREngine):
    def __init__(self, config: str = "--psm 7 -l lets -c tessedit_char_whitelist=0123456789.-"):
        self._config = config
    
    def recognize(self, image: np.ndarray, config: str | None = None) -> OCRResult:
        import pytesseract
        pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
        cfg = config or self._config
        try:
            data = pytesseract.image_to_data(image, config=cfg, output_type=pytesseract.Output.DICT)
            text = " ".join([t for t in data['text'] if t.strip()])
            text_clean = text.replace(" ", "").replace(".", "").replace(",", "")
            print(f"DEBUG OCR RAW TEXT: '{text}'", flush=True) # DEBUG
            print(f"DEBUG OCR CLEAN TEXT: '{text_clean}'", flush=True) # DEBUG
            # extract numeric value
            nums = re.findall(r'-?\d+', text_clean)

            with open("ocr_debug_log.txt", "a", encoding="utf-8") as log_file:
                log_file.write(f"RAW: '{text}' | NUMS: {nums}\n")
            print(f"DEBUG OCR NUMS FOUND: {nums}", flush=True) # DEBUG



            parsed = float(nums[0]) if nums else None
            
            confs = [int(c) for c in data['conf'] if int(c) != -1]
            conf = sum(confs) / len(confs) / 100.0 if confs else 0.0
            
            return OCRResult(raw_text=text, parsed_value=parsed, confidence=conf, success=True)
        except Exception as e:
            print(f"KRITISCHER OCR FEHLER: {e}", flush=True) # <- Diese Zeile hinzufügen
            return OCRResult(raw_text=str(e), parsed_value=None, confidence=0.0, success=False)
