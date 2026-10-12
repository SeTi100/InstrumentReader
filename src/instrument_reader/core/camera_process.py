"""Reads a camera in a separate helper process.

When the Windows camera service (Frame Server) crashes, the Media Foundation state of
the running process can stay broken: every reconnect fails until the app is restarted,
even after unplugging the camera. A fresh process connects normally, so as a last
resort the camera is read in one. ProcessCapture has the parts of cv2.VideoCapture's
interface that OpenCVCamera uses. Ending the helper process also frees the device even
when the driver hangs inside a read.
"""
import multiprocessing as mp

import numpy as np

# The helper imports OpenCV and starts the device; that can take several seconds.
START_TIMEOUT_S = 15.0
READ_TIMEOUT_S = 2.0
MAX_READ_FAILURES = 3


def _capture_loop(source, api, conn, stop):
    try:
        import cv2

        cap = cv2.VideoCapture(source) if api is None else cv2.VideoCapture(source, api)
        if not cap.isOpened():
            conn.send(("failed", None))
            return
        try:
            backend = cap.getBackendName()
        except cv2.error:
            backend = ""
        conn.send(("opened", (float(cap.get(cv2.CAP_PROP_FPS) or 0.0), backend)))
        failures = 0
        while not stop.is_set():
            ok, frame = cap.read()
            if not ok or frame is None:
                failures += 1
                if failures >= MAX_READ_FAILURES:
                    break
                continue
            failures = 0
            conn.send(("frame", (frame.shape, frame.dtype.str, frame.tobytes())))
        cap.release()
    except (EOFError, OSError, BrokenPipeError):
        pass
    finally:
        try:
            conn.close()
        except OSError:
            pass


class ProcessCapture:
    def __init__(self, source: int, api: int | None = None):
        ctx = mp.get_context("spawn")
        self._conn, child_conn = ctx.Pipe(duplex=False)
        self._stop = ctx.Event()
        self._proc = ctx.Process(
            target=_capture_loop, args=(source, api, child_conn, self._stop),
            name="camera-helper", daemon=True,
        )
        self._fps = 0.0
        self._backend = ""
        self._opened = False
        try:
            self._proc.start()
        except Exception:
            child_conn.close()
            self.release()
            return
        child_conn.close()
        kind, payload = self._recv(START_TIMEOUT_S)
        if kind == "opened":
            self._fps, self._backend = payload
            self._opened = True
        else:
            self.release()

    def _recv(self, timeout: float):
        try:
            if self._conn.poll(timeout):
                return self._conn.recv()
        except (EOFError, OSError):
            pass
        return None, None

    def isOpened(self) -> bool:
        return self._opened

    def read(self):
        if not self._opened:
            return False, None
        kind, payload = self._recv(READ_TIMEOUT_S)
        if kind != "frame":
            self._opened = False
            return False, None
        shape, dtype, data = payload
        return True, np.frombuffer(data, dtype=np.dtype(dtype)).reshape(shape)

    def get(self, prop) -> float:
        import cv2

        return self._fps if prop == cv2.CAP_PROP_FPS else 0.0

    def getBackendName(self) -> str:
        return self._backend

    def release(self) -> None:
        self._opened = False
        self._stop.set()
        try:
            self._conn.close()
        except OSError:
            pass
        if self._proc.pid is not None:
            self._proc.join(1.0)
            if self._proc.is_alive():
                self._proc.kill()
                self._proc.join(1.0)
