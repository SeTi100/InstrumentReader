import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

class ROIShape(Enum):
    RECTANGLE = "rectangle"
    POLYGON = "polygon"
    RULER = "ruler"

class DisplayType(Enum):
    DIGITAL = "digital"      # 7-Segment, LCD – OCR
    ANALOG = "analog"        # Skala/Schwimmer – Bilderkennung
    MANUAL = "manual"        # Manuelle Eingabe

@dataclass
class ROIConfig:
    """Konfiguration eines Messbereichs (Region of Interest)."""
    name: str                              # z.B. "Waage", "Thermometer"
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    shape: ROIShape = ROIShape.RECTANGLE
    display_type: DisplayType = DisplayType.DIGITAL
    # Koordinaten: Rechteck = [x, y, w, h], Polygon = [[x1,y1], [x2,y2], ...], Ruler = [[x1,y1], [x2,y2]]
    coordinates: list = field(default_factory=list)
    unit: str = ""                         # z.B. "g", "°C", "Nl/h"
    # Validierungsgrenzen
    value_min: Optional[float] = None
    value_max: Optional[float] = None
    max_delta_per_sec: Optional[float] = None
    # Vorverarbeitungsparameter
    preprocessing_params: dict = field(default_factory=dict)
    # Dezimalstellen für OCR-Parsing
    decimal_places: Optional[int] = None
    # Composite / Container ROI Konfiguration
    is_container: bool = False
    sub_roi_ids: list[str] = field(default_factory=list)
    sort_direction: str = "ltr"            # "ltr" (Left-to-Right) oder "rtl" (Right-to-Left)
    decimal_position: Optional[int] = None # Nach welcher Ziffer das Komma sitzt (1-basiert)
    allow_leading_blank: bool = True       # Unlit / blank leading digits allowed (e.g. '  15.4')
    allow_negative: bool = True            # Minus sign / negative numbers allowed
    # Analoge Rotameter / Skalen-Kalibrierung: [[y_rel_or_px, value], ...]
    analog_calibration: list[list[float]] = field(default_factory=list)
    analog_value: Optional[float] = None   # Wenn diese ROI als Kalibriermarke dient
    # Lineal / Ruler (Winkelverstellbare Messachse)
    strip_width: float = 30.0
    calibration_marks: list[dict] = field(default_factory=list)
    ruler_edge: str = "top"                # "top" (Oberkante), "bottom" (Unterkante), "center" (Mitte)
    # Rotameter Skalen-Filterung (Togglebar)
    suppress_scale_marks: bool = True       # 2D-Objektfilter zur Skalenstrich-Unterdrückung
    core_width_pct: float = 0.6             # Zentrierter Kernstreifen (0.1 bis 1.0)
    min_float_height: int = 8               # Mindesthöhe des Schwimmers in Pixeln


