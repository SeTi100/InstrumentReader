import os
import queue
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple
import cv2
import numpy as np

# Frame times are flushed to disk this often, so a crash loses at most this many.
TIMESTAMP_FLUSH_EVERY = 30


def timestamps_path(video_path: str | Path) -> Path:
    """Sidecar file with the wall-clock time of every frame of a recording."""
    p = Path(video_path)
    return p.with_name(p.stem + ".timestamps.csv")


def load_frame_times(video_path: str | Path) -> Optional[np.ndarray]:
    """Epoch time of each frame (index = frame number), or None without a sidecar file."""
    path = timestamps_path(video_path)
    if not path.exists():
        return None
    times = []
    try:
        with open(path, encoding="utf-8") as f:
            next(f, None)  # header
            for line in f:
                parts = line.strip().split(",")
                if len(parts) == 2:
                    try:
                        times.append(float(parts[1]))
                    except ValueError:
                        break  # half-written last line after a crash
    except OSError:
        return None
    return np.asarray(times, dtype=float) if times else None


def _fourcc(fmt: str) -> int:
    return cv2.VideoWriter_fourcc(*("mp4v" if fmt in ("mp4", "mkv") else "XVID"))


class VideoRecorder:
    """
    Lightweight video recorder decoupled from the GUI via an in-memory queue.
    Saves incoming frames into specified directory (default: recordings/)
    using OpenCV VideoWriter.

    Default container is MKV: it is written progressively, so a crash or power loss
    keeps everything up to the last moment. An MP4 only becomes readable when the
    recording is stopped cleanly. Next to the video, <name>.timestamps.csv logs the
    wall-clock time of every written frame, so frames lost while the camera was gone
    (or dropped when the disk was too slow) do not shift the time axis on playback.
    """

    def __init__(
        self,
        output_dir: str | Path = "recordings",
        fps: float = 30.0,
        format: str = "mkv",
    ):
        self.output_dir = Path(output_dir)
        self.fps = fps
        self.format = format.lower().lstrip(".")
        self.is_recording = False
        self._queue: queue.Queue[Optional[tuple[np.ndarray, float]]] = queue.Queue(maxsize=300)
        self._thread: Optional[threading.Thread] = None
        self._writer: Optional[cv2.VideoWriter] = None
        self._size: Optional[Tuple[int, int]] = None
        self._times = None
        self.current_file: Optional[str] = None
        self._frames_written = 0
        self.frames_dropped = 0

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
        self.frames_dropped = 0
        fmt = Path(filepath).suffix.lower().lstrip(".") or self.format

        if frame_size:
            w, h = frame_size
            self._size = (w, h)
            self._writer = cv2.VideoWriter(filepath, _fourcc(fmt), self.fps, (w, h))
        else:
            self._size = None
            self._writer = None  # Will be lazily initialized on first frame
        self._times = open(timestamps_path(filepath), "w", encoding="utf-8", newline="\n")
        self._times.write("frame,epoch_s\n")

        self.is_recording = True

        # Clear any stale frames from previous runs
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

        self._thread = threading.Thread(target=self._worker, args=(fmt,), daemon=True)
        self._thread.start()
        return filepath

    def write_frame(self, frame: Optional[np.ndarray], timestamp: Optional[float] = None) -> bool:
        """
        Pushes a copy of the frame (with its capture time, default: now) into the write queue.
        Non-blocking: if the queue is full, frame is dropped rather than blocking GUI.
        """
        if not self.is_recording or frame is None:
            return False
        try:
            self._queue.put_nowait((frame.copy(), time.time() if timestamp is None else timestamp))
            return True
        except queue.Full:
            self.frames_dropped += 1
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

        if not self._thread or not self._thread.is_alive():
            if self._writer:
                self._writer.release()
                self._writer = None
            self._close_times()

        saved = self.current_file
        self.current_file = None
        return saved

    def _close_times(self):
        times, self._times = self._times, None
        if times is not None:
            try:
                times.close()
            except OSError:
                pass

    def _worker(self, fmt: str):
        fourcc = _fourcc(fmt)
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

                frame, ts = item
                try:
                    if len(frame.shape) == 2:
                        frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)

                    if self._writer is None and self.current_file:
                        h, w = frame.shape[:2]
                        self._size = (w, h)
                        self._writer = cv2.VideoWriter(
                            self.current_file, fourcc, self.fps, (w, h)
                        )

                    if self._size and frame.shape[1::-1] != self._size:
                        # VideoWriter silently skips frames of another size, which would
                        # put the timestamp log out of step with the video.
                        frame = cv2.resize(frame, self._size)

                    if self._writer and self._writer.isOpened():
                        self._writer.write(frame)
                        if self._times is not None:
                            self._times.write(f"{self._frames_written},{ts:.3f}\n")
                            if self._frames_written % TIMESTAMP_FLUSH_EVERY == 0:
                                self._times.flush()
                        self._frames_written += 1
                finally:
                    self._queue.task_done()
        finally:
            if self._writer:
                self._writer.release()
                self._writer = None
            self._close_times()
