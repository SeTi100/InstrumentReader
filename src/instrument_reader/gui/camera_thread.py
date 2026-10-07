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
    # (frame, epoch timestamp of the frame) - video time for files, wall clock for live cameras
    frame_captured = Signal(object, float)
    # (state, message); state is one of "connecting", "connected", "reconnecting", "stopped"
    status_changed = Signal(str, str)

    # Live camera: a session that delivers no frame for this long is considered dead.
    STALL_TIMEOUT_S = 1.5
    # Opening a device can legitimately take a few seconds (driver start-up).
    OPEN_TIMEOUT_S = 8.0
    # Delay before each reconnect attempt; the last value repeats.
    RECONNECT_DELAYS_S = (0.0, 0.05, 0.1, 0.25, 0.5, 1.0)
    STOP_JOIN_TIMEOUT_S = 1.0
    # Max. rate of GUI frame updates while a video runs unpaced
    DISPLAY_INTERVAL_S = 1.0 / 30.0

    def __init__(self, camera):
        super().__init__()
        self.camera = camera
        self.running = False
        self.paused = False
        self._lock = QMutex()
        self.state = "stopped"
        self._status_message = ""
        # Playback speed for video files: 1.0 = real time, 0 = as fast as possible
        self.playback_speed = 1.0
        # Optional callable(keep_going) that blocks until the consumer has taken
        # a frame it needs; used in unpaced mode so OCR never misses a sample.
        self.backpressure = None
        self._resync = True

    def set_playback_speed(self, speed: float):
        self.playback_speed = max(0.0, float(speed))
        self._resync = True

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

        is_video = getattr(self.camera, "is_video_file", False)
        anchor_wall = anchor_video = 0.0
        last_display = 0.0

        while self.running:
            if self.paused:
                self._resync = True
                self.msleep(50)
                continue

            self._lock.lock()
            ret, frame = self.camera.read()
            ts = self.camera.frame_timestamp() if ret else 0.0
            self._lock.unlock()

            if not (ret and frame is not None):
                if is_video:
                    self.paused = True
                else:
                    self.msleep(10)
                continue

            if not is_video:
                # Non-reconnecting live source: read() already blocks until the next frame
                self.frame_ready.emit(frame)
                self.frame_captured.emit(frame, ts)
                continue

            speed = self.playback_speed
            now = time.monotonic()
            if speed > 0:
                # Pace against the video clock instead of sleeping 1/fps after
                # every frame, so decoding/processing time is not added on top.
                if self._resync:
                    anchor_wall, anchor_video = now, ts
                    self._resync = False
                delay = anchor_wall + (ts - anchor_video) / speed - now
                if delay < -0.5:
                    # Too far behind (slow decode): re-anchor instead of bursting
                    anchor_wall, anchor_video = now, ts
                # Sleep in small slices so pause/stop/seek stay responsive
                while delay > 0 and self.running and not self.paused and not self._resync:
                    self.msleep(max(1, int(min(delay, 0.05) * 1000)))
                    delay = anchor_wall + (ts - anchor_video) / speed - time.monotonic()
                self.frame_ready.emit(frame)
            else:
                self._resync = True
                if now - last_display >= self.DISPLAY_INTERVAL_S:
                    last_display = now
                    self.frame_ready.emit(frame)

            self.frame_captured.emit(frame, ts)
            if speed <= 0 and self.backpressure is not None:
                self.backpressure(lambda: self.running and not self.paused)

    def _emit_frame(self, frame):
        if self.running and not self.paused:
            self.frame_ready.emit(frame)
            self.frame_captured.emit(frame, time.time())

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
        self._resync = True
        self.paused = False

    def seek(self, seconds: float):
        self._lock.lock()
        try:
            if self.camera:
                self.camera.seek(seconds)
                self._resync = True
                if self.paused:
                    ret, frame = self.camera.read()
                    if ret and frame is not None:
                        self.frame_ready.emit(frame)
                        self.frame_captured.emit(frame, self.camera.frame_timestamp())
        finally:
            self._lock.unlock()

    def seek_to_frame(self, frame_num: int):
        self._lock.lock()
        try:
            if self.camera:
                self.camera.seek_to_frame(frame_num)
                self._resync = True
                if self.paused:
                    ret, frame = self.camera.read()
                    if ret and frame is not None:
                        self.frame_ready.emit(frame)
                        self.frame_captured.emit(frame, self.camera.frame_timestamp())
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
