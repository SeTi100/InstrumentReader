"""Reads a live camera in a separate helper process.

When a camera driver or the Windows camera service (Frame Server) crashes, the process
reading the camera can be left with broken Media Foundation state (every reconnect
fails until restart) or even corrupted memory (Windows then kills the process, 0xc0000374).
Reading the camera in a helper process confines both to the helper: it dies or fails,
and the next reconnect simply starts a fresh one. Ending the helper also frees the
device when the driver hangs inside a read.

ProcessCapture has the parts of cv2.VideoCapture's interface that OpenCVCamera uses.
"""
import multiprocessing as mp

import numpy as np

# The helper starts Python, imports OpenCV and starts the device.
START_TIMEOUT_S = 15.0
READ_TIMEOUT_S = 2.0
MAX_READ_FAILURES = 3


def _capture_loop(source, api, name, other_backend, conn, stop):
    try:
        import cv2

        from instrument_reader.core.camera_devices import device_name, resolve_capture_target

        target = resolve_capture_target(source, api, name, other_backend) if isinstance(source, int) else None
        if other_backend and target is None:
            conn.send(("failed", None))
            return
        if target is not None:
            cap = cv2.VideoCapture(*target)
        else:
            cap = cv2.VideoCapture(source) if api is None else cv2.VideoCapture(source, api)
        if not cap.isOpened():
            conn.send(("failed", None))
            return
        try:
            backend = cap.getBackendName()
        except cv2.error:
            backend = ""
        if name is None and isinstance(source, int) and target is None:
            try:
                name = device_name(source, api if api is not None else getattr(cv2, f"CAP_{backend}", None))
            except Exception:
                name = None
        conn.send(("opened", (float(cap.get(cv2.CAP_PROP_FPS) or 0.0), backend, name)))
        failures = 0
        while not stop.is_set():
            ok, frame = cap.read()
            if not ok or frame is None:
                failures += 1
                if failures >= MAX_READ_FAILURES:
                    break
                continue
            failures = 0
            frame = np.ascontiguousarray(frame)
            conn.send(("frame", (frame.shape, frame.dtype.str)))
            conn.send_bytes(frame)
        cap.release()
    except (EOFError, OSError):
        pass
    finally:
        try:
            conn.close()
        except OSError:
            pass


class ProcessCapture:
    def __init__(self, source: int | str, api: int | None = None, name: str | None = None,
                 other_backend: bool = False):
        ctx = mp.get_context("spawn")
        self._conn, child_conn = ctx.Pipe(duplex=False)
        self._stop = ctx.Event()
        self._proc = ctx.Process(
            target=_capture_loop, args=(source, api, name, other_backend, child_conn, self._stop),
            name="camera-helper", daemon=True,
        )
        self._fps = 0.0
        self._backend = ""
        self.device_name = name
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
            self._fps, self._backend, self.device_name = payload
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
        if kind == "frame":
            shape, dtype = payload
            dtype = np.dtype(dtype)
            buf = bytearray(int(np.prod(shape)) * dtype.itemsize)
            try:
                self._conn.recv_bytes_into(buf)
                return True, np.frombuffer(buf, dtype=dtype).reshape(shape)
            except (EOFError, OSError):
                pass
        self._opened = False
        return False, None

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
