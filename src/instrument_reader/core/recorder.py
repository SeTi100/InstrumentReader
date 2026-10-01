import os
import queue
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple
import cv2
import numpy as np


class VideoRecorder:
    """
    Lightweight video recorder decoupled from the GUI via an in-memory queue.
    Saves incoming frames into specified directory (default: recordings/)
    using OpenCV VideoWriter.
    """

    def __init__(
        self,
        output_dir: str | Path = "recordings",
        fps: float = 30.0,
        format: str = "mp4",
    ):
        self.output_dir = Path(output_dir)
        self.fps = fps
        self.format = format.lower().lstrip(".")
        self.is_recording = False
        self._queue: queue.Queue[Optional[np.ndarray]] = queue.Queue(maxsize=300)
        self._thread: Optional[threading.Thread] = None
        self._writer: Optional[cv2.VideoWriter] = None
        self.current_file: Optional[str] = None
        self._frames_written = 0

    def start_recording(
        self,
        fps: Optional[float] = None,
        frame_size: Optional[Tuple[int, int]] = None,
        filename: Optional[str] = None,
    ) -> str:
        """
        Starts video recording in a background thread.
        Returns the path to the recorded video file.
        """
        if self.is_recording:
            return self.current_file or ""

        self.output_dir.mkdir(parents=True, exist_ok=True)
        if fps and fps > 0:
            self.fps = fps

        if not filename:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            candidate = f"recording_{ts}.{self.format}"
            idx = 1
            while (self.output_dir / candidate).exists():
                candidate = f"recording_{ts}_{idx}.{self.format}"
                idx += 1
            filename = candidate

        filepath = str(self.output_dir / filename)
        self.current_file = filepath
        self._frames_written = 0

        fourcc = (
            cv2.VideoWriter_fourcc(*"mp4v")
            if self.format == "mp4"
            else cv2.VideoWriter_fourcc(*"XVID")
        )

        if frame_size:
            w, h = frame_size
            self._writer = cv2.VideoWriter(filepath, fourcc, self.fps, (w, h))
        else:
            self._writer = None  # Will be lazily initialized on first frame

        self.is_recording = True

        # Clear any stale frames from previous runs
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()
        return filepath

    def write_frame(self, frame: Optional[np.ndarray]) -> bool:
        """
        Pushes a copy of the frame into the write queue.
        Non-blocking: if the queue is full, frame is dropped rather than blocking GUI.
        """
        if not self.is_recording or frame is None:
            return False
        try:
            self._queue.put_nowait(frame.copy())
            return True
        except queue.Full:
            return False

    def stop_recording(self) -> Optional[str]:
        """
        Stops the recording worker thread and flushes remaining frames.
        Returns the saved file path.
        """
        if not self.is_recording:
            return None

        self.is_recording = False
        self._queue.put(None)  # Sentinel to stop worker
        if self._thread:
            self._thread.join(timeout=5.0)
            if not self._thread.is_alive():
                self._thread = None

        if self._writer and (not self._thread or not self._thread.is_alive()):
            self._writer.release()
            self._writer = None

        saved = self.current_file
        self.current_file = None
        return saved

    def _worker(self):
        fourcc = (
            cv2.VideoWriter_fourcc(*"mp4v")
            if self.format == "mp4"
            else cv2.VideoWriter_fourcc(*"XVID")
        )
        try:
            while True:
                try:
                    item = self._queue.get(timeout=0.1)
                except queue.Empty:
                    if not self.is_recording:
                        break
                    continue

                if item is None:
                    self._queue.task_done()
                    break

                frame = item
                try:
                    if len(frame.shape) == 2:
                        frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)

                    if self._writer is None and self.current_file:
                        h, w = frame.shape[:2]
                        self._writer = cv2.VideoWriter(
                            self.current_file, fourcc, self.fps, (w, h)
                        )

                    if self._writer and self._writer.isOpened():
                        self._writer.write(frame)
                        self._frames_written += 1
                finally:
                    self._queue.task_done()
        finally:
            if self._writer:
                self._writer.release()
                self._writer = None
