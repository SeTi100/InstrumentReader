"""Reads a live camera in a separate helper process.

When a camera driver or the Windows camera service (Frame Server) crashes, the process
reading the camera can be left with broken Media Foundation state (every reconnect
fails until restart) or even corrupted memory (Windows then kills the process, 0xc0000374).
Reading the camera in a helper process confines both to the helper: it dies or fails,
and the next reconnect simply starts a fresh one. Ending the helper also frees the
device when the driver hangs inside a read.

ProcessCapture has the parts of cv2.VideoCapture's interface that OpenCVCamera uses.
"""
import logging
import multiprocessing as mp

import numpy as np

log = logging.getLogger("instrument_reader.camera")

# The helper starts Python, imports OpenCV and starts the device.
START_TIMEOUT_S = 15.0
READ_TIMEOUT_S = 2.0
MAX_READ_FAILURES = 3


# Why a camera could not be opened (OpenCVCamera.fail_reason).
FAIL_BUSY = "busy"        # the device opened but delivered no frame: usually in use elsewhere
FAIL_ABSENT = "absent"    # no route could open the device at all


def open_live_capture(source, api, name, other_first):
    """Opens a camera on the first route that delivers a frame.

    Tries the chosen route, then (Windows, camera index) the same camera by name through
    the other backend. A device that opens but fails its first reads - typical when another
    program (or a second InstrumentReader window) holds the camera: Media Foundation then
    reports MF_E_HW_MFT_FAILED_START_STREAMING - counts as failed, so the fallback takes
    milliseconds instead of a reconnect cycle.

    Returns (cap, first_frame, used_other_backend, fail_reason); cap is None on failure.
    """
    import cv2

    from instrument_reader.core.camera_devices import resolve_capture_target

    routes = [other_first, not other_first] if isinstance(source, int) else [False]
    reason = FAIL_ABSENT
    for other in routes:
        if other:
            target = resolve_capture_target(source, api, name, True)
            if target is None:
                continue
            cap = cv2.VideoCapture(*target)
        else:
            cap = cv2.VideoCapture(source) if api is None else cv2.VideoCapture(source, api)
        if not cap.isOpened():
            cap.release()
            continue
        for _ in range(MAX_READ_FAILURES):
            ok, frame = cap.read()
            if ok and frame is not None:
                return cap, frame, other, None
        if isinstance(source, str):
            # A video file that opens but has no frames is simply empty.
            return cap, None, other, None
        log.info("Kamera %s (%s) öffnet, liefert aber keine Bilder", source,
                 "anderer Weg" if other else "normaler Weg")
        cap.release()
        reason = FAIL_BUSY
    return None, None, False, reason


def _exit_with_parent():
    """Ends the helper as soon as the app is gone, even while a read hangs in the driver;
    an orphaned helper would keep the camera locked for every later start."""
    import os
    import threading

    parent = mp.parent_process()
    if parent is None:
        return

    def watch():
        parent.join()
        os._exit(0)

    threading.Thread(target=watch, name="parent-watch", daemon=True).start()


def _capture_loop(source, api, name, other_backend, conn, stop):
    try:
        _exit_with_parent()
        import cv2

        from instrument_reader.core.camera_devices import device_name

        cap, frame, used_other, reason = open_live_capture(source, api, name, other_backend)
        if cap is None:
            conn.send(("failed", reason))
            return
        try:
            backend = cap.getBackendName()
        except cv2.error:
            backend = ""
        if name is None and isinstance(source, int) and not used_other:
            try:
                name = device_name(source, api if api is not None else getattr(cv2, f"CAP_{backend}", None))
            except Exception:
                name = None
        conn.send(("opened", (float(cap.get(cv2.CAP_PROP_FPS) or 0.0), backend, name, used_other)))
        failures = 0
        while not stop.is_set():
            if frame is None:
                ok, frame = cap.read()
                if not ok or frame is None:
                    frame = None
                    failures += 1
                    if failures >= MAX_READ_FAILURES:
                        break
                    continue
            failures = 0
            frame = np.ascontiguousarray(frame)
            conn.send(("frame", (frame.shape, frame.dtype.str)))
            # Flat byte view: for a 3-D array len() counts rows, not bytes, which breaks
            # send_bytes on Windows pipes for frames larger than one write.
            conn.send_bytes(memoryview(frame).cast("B"))
            frame = None
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
        self.used_other_backend = False
        self.fail_reason = None
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
            self._fps, self._backend, self.device_name, self.used_other_backend = payload
            self._opened = True
        else:
            self.fail_reason = payload if kind == "failed" else None
            log.warning("Hilfsprozess: Kamera %s (Backend %s%s) %s", source, api,
                        ", anderer Weg" if other_backend else "",
                        "meldet sich nicht" if kind is None
                        else "ist belegt (öffnet, liefert keine Bilder)" if payload == FAIL_BUSY
                        else "lässt sich nicht öffnen")
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
