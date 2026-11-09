"""Loud alarm when a running live camera stops delivering pictures.

On 09.11.2026 the camera stack hung and no picture came for 16 minutes; the app only
showed a red status line and kept reconnecting, so nobody noticed and the measurement
was lost. The alarm runs on a timer in the GUI thread of the main process (no new
processes or files, so it still works when the system is already sluggish): it beeps
until acknowledged and stays visible until pictures come back.
"""
import time

from PySide6.QtCore import QObject, QTimer, Signal


def _system_beep():
    try:
        import winsound

        winsound.MessageBeep(winsound.MB_ICONHAND)
    except Exception:
        from PySide6.QtWidgets import QApplication

        QApplication.beep()


class CameraAlarm(QObject):
    # Epoch time of the last picture, when the silence passed the threshold
    raised = Signal(float)
    # Seconds without a picture so far, once per check while the alarm is on
    ticked = Signal(float)
    # (epoch time of the last picture before the gap, seconds without a picture)
    cleared = Signal(float, float)

    DEFAULT_THRESHOLD_S = 30.0
    CHECK_INTERVAL_MS = 1000
    BEEP_EVERY_S = 2.0

    def __init__(self, threshold_s: float = DEFAULT_THRESHOLD_S, beep=_system_beep, parent=None):
        super().__init__(parent)
        self.threshold_s = float(threshold_s)
        self._beep = beep
        self.watching = False
        self.alarming = False
        self.acknowledged = False
        self._last_frame = 0.0       # monotonic
        self._last_frame_wall = 0.0  # epoch
        self._last_beep = 0.0
        self._gap_start = 0.0        # epoch time of the last picture before the alarm
        self._timer = QTimer(self)
        self._timer.setInterval(self.CHECK_INTERVAL_MS)
        self._timer.timeout.connect(self.check)

    def frame_arrived(self, *_args):
        """Called for every picture, directly from the camera thread (two plain stores)."""
        self._last_frame_wall = time.time()
        self._last_frame = time.monotonic()

    def watch(self):
        """Starts watching a live camera; the silence counts from now."""
        self.frame_arrived()
        self.watching = True
        self._timer.start()

    def unwatch(self):
        """The camera was stopped on purpose: no alarm, and an alarm that is on ends."""
        self.watching = False
        self._timer.stop()
        self._end()

    def acknowledge(self):
        """Stops the beeping; the alarm stays visible until pictures come back."""
        self.acknowledged = True

    def silence_s(self) -> float:
        return time.monotonic() - self._last_frame

    def check(self):
        if not self.watching:
            return
        silent = self.silence_s()
        if silent < self.threshold_s:
            self._end()
            return
        now = time.monotonic()
        if not self.alarming:
            self.alarming = True
            self.acknowledged = False
            self._last_beep = 0.0
            self._gap_start = self._last_frame_wall
            self.raised.emit(self._gap_start)
        self.ticked.emit(silent)
        if not self.acknowledged and now - self._last_beep >= self.BEEP_EVERY_S:
            self._last_beep = now
            self._beep()

    def _end(self):
        if self.alarming:
            self.alarming = False
            # Pictures back: the gap ends with the first new one; stopped: it ends now.
            end = self._last_frame_wall if self._last_frame_wall > self._gap_start else time.time()
            self.cleared.emit(self._gap_start, end - self._gap_start)
