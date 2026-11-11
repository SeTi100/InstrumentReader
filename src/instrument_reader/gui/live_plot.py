import re
import time
from collections import deque
from typing import Dict, Iterable, Optional, Tuple

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QComboBox, QToolButton

from instrument_reader.core.calculation import CalculationResult

# Calculated channels whose formula uses rate()/slope() are shown in the rate plot
RATE_FORMULA_RE = re.compile(r"\b(rate|slope)\s*\(")

# (label, seconds) for the visible time window
WINDOW_CHOICES = [("1 min", 60), ("5 min", 300), ("15 min", 900), ("1 h", 3600)]
DEFAULT_WINDOW_INDEX = 1

MASS_COLORS = {"raw": "#9aa4b1", "corrected": "#2f80ed"}
RATE_COLORS = ["#eb5757", "#27ae60", "#f2994a", "#9b51e0", "#56ccf2", "#bb6bd9"]

MAX_POINTS_PER_SERIES = 20000
PLOT_HEIGHT = 150

CORRECTED_KEY = "Waage_korrigiert"


def is_rate_result(res: CalculationResult) -> bool:
    return bool(res.formula) and RATE_FORMULA_RE.search(res.formula) is not None


def _fmt(value: float) -> str:
    return f"{value:.3f}" if abs(value) < 100 else f"{value:.1f}"


class LivePlotWidget(QWidget):
    """Compact live strip: scale mass (raw + corrected) next to rate-type calculated channels."""

    def __init__(self, parent=None):
        super().__init__(parent)
        # series key -> deque of (timestamp, value)
        self.mass_data: Dict[str, deque] = {}
        self.rate_data: Dict[str, deque] = {}
        self.rate_units: Dict[str, str] = {}
        self._curves: Dict[Tuple[str, str], pg.PlotDataItem] = {}
        self._scale_name: Optional[str] = None
        self._t0: Optional[float] = None
        self._last_ts: Optional[float] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(0)

        header = QHBoxLayout()
        header.setSpacing(12)
        self.mass_label = QLabel("Masse  –")
        self.rate_label = QLabel("Rate  –")
        for lbl in (self.mass_label, self.rate_label):
            lbl.setTextFormat(Qt.RichText)
            lbl.setStyleSheet("color: #5f6670; font-size: 11px;")
        header.addWidget(self.mass_label, 1)
        header.addWidget(self.rate_label, 1)

        self.window_combo = QComboBox()
        for label, seconds in WINDOW_CHOICES:
            self.window_combo.addItem(label, seconds)
        self.window_combo.setCurrentIndex(DEFAULT_WINDOW_INDEX)
        self.window_combo.setToolTip("Sichtbares Zeitfenster")
        self.window_combo.setStyleSheet("font-size: 11px;")
        self.window_combo.currentIndexChanged.connect(self.refresh)
        header.addWidget(self.window_combo)

        self.clear_btn = QToolButton()
        self.clear_btn.setText("⟲")
        self.clear_btn.setToolTip("Diagramm leeren")
        self.clear_btn.setAutoRaise(True)
        self.clear_btn.clicked.connect(self.clear)
        header.addWidget(self.clear_btn)
        layout.addLayout(header)

        self.graphics = pg.GraphicsLayoutWidget()
        self.graphics.setBackground(None)
        self.graphics.ci.setContentsMargins(0, 0, 0, 0)
        self.graphics.ci.layout.setHorizontalSpacing(16)
        self.graphics.setFixedHeight(PLOT_HEIGHT)
        layout.addWidget(self.graphics)

        self.mass_plot = self.graphics.addPlot(row=0, col=0)
        self.rate_plot = self.graphics.addPlot(row=0, col=1)
        tick_font = QFont()
        tick_font.setPointSize(7)
        for plot in (self.mass_plot, self.rate_plot):
            plot.setMenuEnabled(False)
            plot.setMouseEnabled(x=False, y=False)
            plot.hideButtons()
            plot.showGrid(x=True, y=True, alpha=0.12)
            for name in ("left", "bottom"):
                axis = plot.getAxis(name)
                axis.setStyle(tickFont=tick_font, tickLength=-4)
                axis.setPen(pg.mkPen("#8a8f98"))
                axis.setTextPen(pg.mkPen("#8a8f98"))
                axis.enableAutoSIPrefix(False)
            plot.getAxis("left").setWidth(38)
            plot.getAxis("bottom").setHeight(18)
        self.rate_plot.setXLink(self.mass_plot)

        self.setMaximumHeight(PLOT_HEIGHT + 30)

    @property
    def window_seconds(self) -> float:
        return float(self.window_combo.currentData())

    def clear(self):
        self.mass_data.clear()
        self.rate_data.clear()
        self.rate_units.clear()
        self.mass_plot.clear()
        self.rate_plot.clear()
        self._curves.clear()
        self._t0 = None
        self._last_ts = None
        self.mass_label.setText("Masse  –")
        self.rate_label.setText("Rate  –")

    def add_point(self, data: Dict[str, deque], key: str, ts: float, value: Optional[float]):
        if value is None:
            return
        try:
            v = float(value)
        except (TypeError, ValueError):
            return
        if not np.isfinite(v):
            return
        q = data.get(key)
        if q is None:
            q = data[key] = deque(maxlen=MAX_POINTS_PER_SERIES)
        q.append((ts, v))

    def update_data(
        self,
        timestamp: Optional[float],
        scale_name: Optional[str],
        raw_mass: Optional[float],
        corrected_mass: Optional[float],
        calc_results: Iterable[CalculationResult],
    ):
        ts = float(timestamp) if timestamp is not None else time.time()
        if self._last_ts is not None and ts < self._last_ts:
            # Time went backwards (new video source or restart): start a fresh plot
            self.clear()
        if self._t0 is None:
            self._t0 = ts
        self._last_ts = ts
        self._scale_name = scale_name

        if scale_name:
            self.add_point(self.mass_data, scale_name, ts, raw_mass)
        if corrected_mass is not None:
            self.add_point(self.mass_data, CORRECTED_KEY, ts, corrected_mass)

        for res in calc_results:
            if not is_rate_result(res) or not res.is_valid:
                continue
            self.add_point(self.rate_data, res.name, ts, res.value)
            self.rate_units[res.name] = res.unit or ""

        self._prune(ts)
        self.refresh()

    def _prune(self, now: float):
        # Keep history up to the largest selectable window so enlarging it shows past data
        min_ts = now - max(seconds for _, seconds in WINDOW_CHOICES)
        for data in (self.mass_data, self.rate_data):
            for q in data.values():
                while q and q[0][0] < min_ts:
                    q.popleft()

    def _curve(self, plot_name: str, key: str) -> pg.PlotDataItem:
        curve = self._curves.get((plot_name, key))
        if curve is None:
            if plot_name == "mass":
                corrected = key == CORRECTED_KEY
                pen = pg.mkPen(MASS_COLORS["corrected" if corrected else "raw"], width=1.6 if corrected else 1.0)
                curve = self.mass_plot.plot([], [], pen=pen)
                # Corrected mass stays on top of the raw trace
                curve.setZValue(1 if corrected else 0)
            else:
                n = sum(1 for p, _ in self._curves if p == "rate")
                curve = self.rate_plot.plot([], [], pen=pg.mkPen(RATE_COLORS[n % len(RATE_COLORS)], width=1.6))
            curve.setClipToView(True)
            curve.setDownsampling(auto=True, method="peak")
            self._curves[(plot_name, key)] = curve
        return curve

    def _series_color(self, plot_name: str, key: str) -> str:
        return self._curve(plot_name, key).opts["pen"].color().name()

    def _update_labels(self):
        parts = []
        for key in (self._scale_name, CORRECTED_KEY):
            q = self.mass_data.get(key) if key else None
            if q:
                name = "korr." if key == CORRECTED_KEY else key
                parts.append(
                    f"<span style='color:{self._series_color('mass', key)}'>●</span> {name} <b>{_fmt(q[-1][1])}</b> g"
                )
        self.mass_label.setText("  ".join(parts) or "Masse  –")

        parts = []
        for key, q in self.rate_data.items():
            if q:
                unit = self.rate_units.get(key, "")
                parts.append(
                    f"<span style='color:{self._series_color('rate', key)}'>●</span> {key} <b>{_fmt(q[-1][1])}</b> {unit}"
                )
        self.rate_label.setText("  ".join(parts) or "Rate  –")

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def refresh(self):
        # Data keeps being collected while hidden; redraw happens when shown again
        if self._t0 is None or self._last_ts is None or not self.isVisible():
            return
        x_max = self._last_ts - self._t0
        x_min = x_max - self.window_seconds
        for plot_name, data in (("mass", self.mass_data), ("rate", self.rate_data)):
            for key, q in data.items():
                arr = np.array(q, dtype=float).reshape(-1, 2)
                xs = arr[:, 0] - self._t0
                mask = xs >= x_min
                self._curve(plot_name, key).setData(xs[mask], arr[mask, 1])

        self._update_labels()
        self.mass_plot.setXRange(max(0.0, x_min), max(x_max, 1.0), padding=0.01)
        self.mass_plot.enableAutoRange(axis="y")
        self.rate_plot.enableAutoRange(axis="y")
