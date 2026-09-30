from abc import ABC, abstractmethod
import numpy as np
from .ocr_engine import OCRResult
from .roi import ROIConfig

class CalculationBlock(ABC):
    @abstractmethod
    def calculate(self, values: list[tuple[float, float]]) -> float: ...

class MarkerTracker(ABC):
    @abstractmethod
    def detect_markers(self, frame: np.ndarray) -> dict: ...
    
    @abstractmethod
    def transform_roi(self, roi: ROIConfig, markers: dict) -> ROIConfig: ...

class AnalogReader(ABC):
    @abstractmethod
    def read(self, image: np.ndarray) -> OCRResult: ...
