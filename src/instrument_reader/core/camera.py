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

    @property
    def is_video_file(self) -> bool:
        return False

    @property
    def is_opened(self) -> bool:
        return False

    def seek(self, seconds: float) -> bool:
        return False

    def seek_to_frame(self, frame_num: int) -> bool:
        return False

    def is_eof(self) -> bool:
        return False

    def get_position_frames(self) -> float:
        return 0.0

    def get_frame_count(self) -> float:
        return 0.0


class OpenCVCamera(CameraSource):
    def __init__(self, source: int | str = 0):
        self._source = source
        self._cap: cv2.VideoCapture | None = None
        self._cached_fps: float | None = None

    @property
    def source(self) -> int | str:
        return self._source

    def clone(self) -> "OpenCVCamera":
        """Returns an unopened camera for the same source (used for reconnects)."""
        return type(self)(self._source)

    def open(self) -> bool:
        # Never leave a stale handle behind: a capture that is not released keeps
        # the device locked for other applications (e.g. OBS) until the process exits.
        self.release()
        cap = cv2.VideoCapture(self._source)
        if not cap.isOpened():
            cap.release()
            return False
        self._cap = cap
        val = cap.get(cv2.CAP_PROP_FPS)
        self._cached_fps = float(val) if val and val > 0 else 30.0
        return True

    def read(self) -> tuple[bool, np.ndarray | None]:
        if self._cap is None:
            return False, None
        return self._cap.read()

    def release(self) -> None:
        cap, self._cap = self._cap, None
        if cap is not None:
            cap.release()
        self._cached_fps = None

    @property
    def is_opened(self) -> bool:
        return bool(self._cap and self._cap.isOpened())

    @property
    def fps(self) -> float:
        if self._cached_fps is not None and self._cached_fps > 0:
            return self._cached_fps
        if self._cap:
            val = self._cap.get(cv2.CAP_PROP_FPS)
            if val and val > 0:
                self._cached_fps = float(val)
                return self._cached_fps
        return 30.0

    @property
    def is_video_file(self) -> bool:
        if isinstance(self._source, str):
            s = self._source.strip()
            return not s.isdigit() and len(s) > 0 and not s.startswith("/dev/video")
        return False

    def seek(self, seconds: float) -> bool:
        """
        Seeks relative to current position by seconds (+forward, -backward).
        Returns True if seek was successful.
        """
        if self._cap is None or not self.is_video_file:
            return False

        fps = self.fps
        if fps <= 0:
            fps = 30.0

        current_frame = self._cap.get(cv2.CAP_PROP_POS_FRAMES)
        total_frames = self._cap.get(cv2.CAP_PROP_FRAME_COUNT)
        frame_delta = int(seconds * fps)
        target_frame = current_frame + frame_delta

        if total_frames > 0:
            target_frame = max(0, min(int(total_frames - 1), int(target_frame)))
        else:
            target_frame = max(0, int(target_frame))

        return bool(self._cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame))

    def seek_to_frame(self, frame_num: int) -> bool:
        """Seeks to a specific frame number."""
        if self._cap is None or not self.is_video_file:
            return False
        total_frames = self._cap.get(cv2.CAP_PROP_FRAME_COUNT)
        if total_frames > 0:
            target = max(0, min(int(total_frames - 1), int(frame_num)))
        else:
            target = max(0, int(frame_num))
        return bool(self._cap.set(cv2.CAP_PROP_POS_FRAMES, target))

    def is_eof(self) -> bool:
        """Returns True if video file has reached the end."""
        if self._cap is None or not self.is_video_file:
            return False
        total = self._cap.get(cv2.CAP_PROP_FRAME_COUNT)
        pos = self._cap.get(cv2.CAP_PROP_POS_FRAMES)
        return total > 0 and pos >= total

    def get_position_frames(self) -> float:
        """Returns current frame position."""
        if self._cap is None:
            return 0.0
        return self._cap.get(cv2.CAP_PROP_POS_FRAMES)

    def get_frame_count(self) -> float:
        """Returns total frame count."""
        if self._cap is None:
            return 0.0
        return self._cap.get(cv2.CAP_PROP_FRAME_COUNT)

