"""Asks Excel where T0/T1 would write, in a background thread.

Every Excel access is a COM call into another process. Doing that in the GUI thread every
few hundred milliseconds froze the whole window (video, dragging) for as long as Excel took
to answer, which is long while Excel is busy. Here the polling runs in its own thread with
its own COM apartment, and the GUI only gets a signal when the answer changed.
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal

from instrument_reader.core.excel_link import ExcelError


class ExcelPoller(QObject):
    # (Target or None, error text); only emitted when it differs from the last one
    target_changed = Signal(object, str)

    def __init__(self, link_getter: Callable[[], object], interval_ms: int, parent=None):
        super().__init__(parent)
        self._link_getter = link_getter
        self._interval = interval_ms / 1000.0
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        # daemon: a COM call stuck in a busy Excel must never keep the app from closing
        self._thread = threading.Thread(target=self._run, name="ExcelPoller", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 1.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def is_active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def refresh_now(self) -> None:
        """Ask again right away (after a write or a new configuration)."""
        self._last = None
        self._wake.set()

    def _run(self) -> None:
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except ImportError:
            pythoncom = None
        try:
            while not self._stop.is_set():
                self._wake.clear()
                try:
                    result = (self._link_getter().target(), "")
                except ExcelError as e:
                    result = (None, str(e))
                except Exception as e:  # never let the thread die silently
                    result = (None, f"Excel-Fehler: {e}")
                if self._stop.is_set():
                    break
                if result != self._last:
                    self._last = result
                    try:
                        self.target_changed.emit(*result)
                    except RuntimeError:  # window already gone
                        break
                self._wake.wait(self._interval)
        finally:
            if pythoncom is not None:
                pythoncom.CoUninitialize()
