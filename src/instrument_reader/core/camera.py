import os
import re
import time
from datetime import datetime

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

    def frame_timestamp(self) -> float:
        """Epoch timestamp (seconds) of the frame returned by the last read()."""
        return time.time()


class OpenCVCamera(CameraSource):
    def __init__(self, source: int | str = 0):
        self._source = source
        self._cap: cv2.VideoCapture | None = None
        self._cached_fps: float | None = None
        self._start_epoch: float | None = None

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
        if self.is_video_file:
            self._start_epoch = self._guess_start_epoch()
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


    def get_position_seconds(self) -> float:
        """Video time (seconds) of the frame returned by the last read()."""
        if self._cap is None or not self.is_video_file:
            return 0.0
        # POS_FRAMES points at the next frame to decode
        return max(0.0, self._cap.get(cv2.CAP_PROP_POS_FRAMES) - 1) / self.fps

    def frame_timestamp(self) -> float:
        """
        Epoch timestamp (seconds) of the frame returned by the last read().
        Live cameras use wall-clock time; video files use the recording start
        plus the frame's position in the video, so offline analysis yields the
        same time axis as the original recording, independent of playback speed.
        """
        if not self.is_video_file:
            return time.time()
        if self._start_epoch is None:
            self._start_epoch = self._guess_start_epoch()
        return self._start_epoch + self.get_position_seconds()

    def _guess_start_epoch(self) -> float:
        """
        Best guess for the wall-clock time at which the video was recorded:
        a YYYYMMDD_HHMMSS stamp in the file name (as written by VideoRecorder),
        otherwise the file modification time minus the video duration.
        """
        name = os.path.basename(str(self._source))
        m = re.search(r"(\d{8}_\d{6})", name)
        if m:
            try:
                return datetime.strptime(m.group(1), "%Y%m%d_%H%M%S").timestamp()
            except ValueError:
                pass
        try:
            duration = self.get_frame_count() / self.fps if self._cap else 0.0
            return os.path.getmtime(str(self._source)) - max(0.0, duration)
        except OSError:
            return time.time()
