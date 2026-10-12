import os
import re
import sys
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


def _backend_id(name: str) -> int | None:
    for api in cv2.videoio_registry.getBackends():
        if cv2.videoio_registry.getBackendName(api) == name:
            return int(api)
    return None


def _is_plain_index(source) -> bool:
    """True for a bare camera index; False for paths and backend-encoded indices like 701."""
    return isinstance(source, int) and 0 <= source < 100


# Ways to reach a live camera, alternated while reconnects keep failing (see
# OpenCVCamera.next_route()).
ROUTE_DIRECT = "direct"            # the selected index and backend
ROUTE_OTHER_BACKEND = "other"      # same camera (by name) via the other Windows backend
ROUTES = (ROUTE_DIRECT, ROUTE_OTHER_BACKEND)
ENV_CAMERA_HELPER = "INSTRUMENT_READER_CAMERA_HELPER"   # "0": read live cameras in-process


class OpenCVCamera(CameraSource):
    # Live cameras are read in a helper process (see camera_process.py): when a camera
    # driver or the Windows camera service crashes, it can corrupt the memory of the
    # process that reads the camera. Only the helper dies then; the app starts a new one.
    USE_HELPER_PROCESS = os.environ.get(ENV_CAMERA_HELPER, "1") != "0"

    def __init__(self, source: int | str = 0, api_preference: int | None = None, name: str | None = None):
        self._source = source
        self._cap: cv2.VideoCapture | None = None
        self._cached_fps: float | None = None
        self._start_epoch: float | None = None
        # None: OpenCV picks the backend on the first open (Media Foundation first on
        # Windows, then DirectShow, so typed indices reach DirectShow-only virtual cameras
        # too). open() then records the backend that worked, and clone() passes it on, so
        # a reconnect can never fall through to another backend's device list (where
        # index 0 may be a virtual placeholder camera such as OBS).
        self._api = api_preference
        # Device name, used to find the same camera in the other backend's list.
        self.name = name
        self.route = ROUTE_DIRECT

    @property
    def source(self) -> int | str:
        return self._source

    def set_source(self, source: int | str) -> None:
        """Points the camera at another device; takes effect on the next open()."""
        self._source = source

    def adopt(self, other: "OpenCVCamera") -> None:
        """Takes over the backend, route and frame rate a reconnected clone ended up with."""
        self._api = other._api
        self.name = other.name
        self.route = other.route
        self._cached_fps = other.fps

    def clone(self) -> "OpenCVCamera":
        """Returns an unopened camera for the same device and backend (used for reconnects)."""
        twin = type(self)(self._source, self._api, self.name)
        twin.route = self.route
        return twin

    def next_route(self) -> str:
        """Switches between the selected backend and the other Windows backend for the
        same camera; takes effect on the next open(). Other sources keep the direct route."""
        if isinstance(self._source, int) and sys.platform.startswith("win"):
            self.route = ROUTE_OTHER_BACKEND if self.route == ROUTE_DIRECT else ROUTE_DIRECT
        return self.route

    @property
    def route_label(self) -> str:
        """Short note for the status line when the camera is not reached the usual way."""
        return "Ersatzweg: anderer Kameratreiber" if self.route == ROUTE_OTHER_BACKEND else ""

    @property
    def uses_helper_process(self) -> bool:
        return self.USE_HELPER_PROCESS and isinstance(self._source, int)

    def _backend(self) -> tuple[int | str, int | None]:
        """(device index, backend) the direct route opens."""
        if isinstance(self._source, int) and not _is_plain_index(self._source):
            # OpenCV's encoded form: 701 = device 1 of backend 700 (DirectShow).
            return self._source % 100, self._source - self._source % 100
        return self._source, self._api

    def open(self) -> bool:
        # Never leave a stale handle behind: a capture that is not released keeps
        # the device locked for other applications (e.g. OBS) until the process exits.
        self.release()
        index, api = self._backend()
        other = self.route == ROUTE_OTHER_BACKEND
        if self.uses_helper_process:
            from instrument_reader.core.camera_process import ProcessCapture

            cap = ProcessCapture(index, api, name=self.name, other_backend=other)
        else:
            from instrument_reader.core.camera_devices import resolve_capture_target

            target = resolve_capture_target(index, api, self.name, other) if other else None
            if target is not None:
                cap = cv2.VideoCapture(target[0], target[1])
            elif api is None:
                cap = cv2.VideoCapture(index)
            else:
                cap = cv2.VideoCapture(index, api)
        if not cap.isOpened():
            cap.release()
            return False
        if self._api is None and _is_plain_index(self._source) and not other:
            # Reconnects must use the same backend, or a different device list applies.
            try:
                self._api = _backend_id(cap.getBackendName())
            except cv2.error:
                pass
        if self.name is None and getattr(cap, "device_name", None):
            self.name = cap.device_name
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
