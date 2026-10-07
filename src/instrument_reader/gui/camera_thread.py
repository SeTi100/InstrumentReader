import threading
import time

from PySide6.QtCore import QThread, Signal, QMutex


class _LiveSession:
    """One connection to a live camera, read on its own daemon thread.

    The session owns its capture handle: it opens it, reads frames until the device
    stops delivering, and always releases it on the way out. A read that blocks inside
    the driver (common when a USB camera drops out) therefore only stalls this session;
    the supervising CameraThread abandons it and connects a fresh one.
    """

    MAX_READ_FAILURES = 3

    def __init__(self, camera, on_frame):
        self.camera = camera
        self._on_frame = on_frame
        self._stop = threading.Event()
        self.started_at = time.monotonic()
        self.opened = False
        self.last_frame_at: float | None = None
        self.done = False
        self._thread = threading.Thread(target=self._run, name="camera-session", daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    def join(self, timeout: float):
        self._thread.join(timeout)

    def _run(self):
        try:
            if not self.camera.open():
                return
            self.opened = True
            failures = 0
            while not self._stop.is_set():
                ret, frame = self.camera.read()
                if self._stop.is_set():
                    break
                if ret and frame is not None:
                    failures = 0
                    self.last_frame_at = time.monotonic()
                    self._on_frame(frame)
                else:
                    failures += 1
                    if failures >= self.MAX_READ_FAILURES:
                        break
                    time.sleep(0.005)
        except Exception:
            pass
        finally:
            try:
                self.camera.release()
            except Exception:
                pass
            self.done = True


class CameraThread(QThread):
    frame_ready = Signal(object)
    # (state, message); state is one of "connecting", "connected", "reconnecting", "stopped"
    status_changed = Signal(str, str)

    # Live camera: a session that delivers no frame for this long is considered dead.
    STALL_TIMEOUT_S = 1.5
    # Opening a device can legitimately take a few seconds (driver start-up).
    OPEN_TIMEOUT_S = 8.0
    # Delay before each reconnect attempt; the last value repeats.
    RECONNECT_DELAYS_S = (0.0, 0.05, 0.1, 0.25, 0.5, 1.0)
    STOP_JOIN_TIMEOUT_S = 1.0

    def __init__(self, camera):
        super().__init__()
        self.camera = camera
        self.running = False
        self.paused = False
        self._lock = QMutex()
        self.state = "stopped"
        self._status_message = ""

    def run(self):
        self.running = True
        self.paused = False
        if getattr(self.camera, "is_video_file", False) or not hasattr(self.camera, "clone"):
            self._run_file()
        else:
            self._run_live()

    def _run_file(self):
        if not getattr(self.camera, "is_opened", False) and not self.camera.open():
            self.running = False
            return

        while self.running:
            if self.paused:
                self.msleep(50)
                continue

            self._lock.lock()
            ret, frame = self.camera.read()
            self._lock.unlock()

            if ret and frame is not None:
                self.frame_ready.emit(frame)
            else:
                if getattr(self.camera, "is_video_file", False):
                    self.paused = True

            fps = self.camera.fps
            interval = int(1000 / fps) if fps > 0 else 33
            self.msleep(interval)

    def _emit_frame(self, frame):
        if self.running and not self.paused:
            self.frame_ready.emit(frame)

    def _set_state(self, state: str, message: str):
        if state != self.state or message != self._status_message:
            self.state = state
            self._status_message = message
            self.status_changed.emit(state, message)

    def _sleep(self, seconds: float):
        deadline = time.monotonic() + seconds
        while self.running and time.monotonic() < deadline:
            self.msleep(10)

    def _run_live(self):
        """Keeps a live camera connected until stop(), reconnecting as fast as the device allows."""
        session: _LiveSession | None = None
        attempt = 0
        ever_connected = False
        self._set_state("connecting", "Verbinde mit Kamera …")
        try:
            while self.running:
                if session is None:
                    delay = self.RECONNECT_DELAYS_S[min(attempt, len(self.RECONNECT_DELAYS_S) - 1)]
                    self._sleep(delay)
                    if not self.running:
                        break
                    attempt += 1
                    session = _LiveSession(self.camera.clone(), self._emit_frame)
                    session.start()
                    continue

                now = time.monotonic()
                if session.last_frame_at is not None:
                    if attempt:
                        # The device answered: adopt the real frame rate and reset the backoff.
                        if hasattr(self.camera, "_cached_fps"):
                            self.camera._cached_fps = session.camera.fps
                        attempt = 0
                        ever_connected = True
                        self._set_state("connected", "Kamera verbunden")
                    stalled = now - session.last_frame_at > self.STALL_TIMEOUT_S
                else:
                    stalled = now - session.started_at > self.OPEN_TIMEOUT_S

                if session.done or stalled:
                    # Stop the old session (it releases its handle as soon as the driver
                    # lets go) and immediately start over with a fresh connection.
                    session.stop()
                    session = None
                    if ever_connected:
                        self._set_state("reconnecting", f"Verbindung unterbrochen – verbinde neu (Versuch {attempt + 1}) …")
                    else:
                        self._set_state("connecting", f"Kamera nicht erreichbar – neuer Versuch ({attempt + 1}) …")
                    continue

                self.msleep(20)
        finally:
            if session is not None:
                session.stop()
                session.join(self.STOP_JOIN_TIMEOUT_S)
            self._set_state("stopped", "")

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def seek(self, seconds: float):
        self._lock.lock()
        try:
            if self.camera:
                self.camera.seek(seconds)
                if self.paused:
                    ret, frame = self.camera.read()
                    if ret and frame is not None:
                        self.frame_ready.emit(frame)
        finally:
            self._lock.unlock()

    def seek_to_frame(self, frame_num: int):
        self._lock.lock()
        try:
            if self.camera:
                self.camera.seek_to_frame(frame_num)
                if self.paused:
                    ret, frame = self.camera.read()
                    if ret and frame is not None:
                        self.frame_ready.emit(frame)
        finally:
            self._lock.unlock()

    def stop(self):
        self.running = False
        self.wait()
        self._lock.lock()
        try:
            self.camera.release()
        finally:
            self._lock.unlock()
