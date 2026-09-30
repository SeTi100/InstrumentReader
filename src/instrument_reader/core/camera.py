import cv2
import numpy as np
from abc import ABC, abstractmethod

class CameraSource(ABC):
    @abstractmethod
    def open(self) -> bool: ...

    @abstractmethod
    def read(self) -> tuple[bool, np.ndarray | None]: ...

    @abstractmethod
    def release(self) -> None: ...

    @property
    @abstractmethod
    def fps(self) -> float: ...

class OpenCVCamera(CameraSource):
    def __init__(self, source: int | str = 0):
        self._source = source
        self._cap: cv2.VideoCapture | None = None
    
    def open(self) -> bool:
        self._cap = cv2.VideoCapture(self._source)
        return self._cap.isOpened()
    
    def read(self) -> tuple[bool, np.ndarray | None]:
        if self._cap is None:
            return False, None
        return self._cap.read()
    
    def release(self) -> None:
        if self._cap:
            self._cap.release()
    
    @property
    def fps(self) -> float:
        if self._cap:
            val = self._cap.get(cv2.CAP_PROP_FPS)
            return val if val > 0 else 30.0
        return 30.0
