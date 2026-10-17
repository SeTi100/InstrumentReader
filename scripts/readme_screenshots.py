"""Erzeugt die Bilder für die README (docs/images/).

Baut eine künstliche Laborszene (Waage mit 7-Segment-Anzeige, Rotameter,
Temperaturregler), startet die echte GUI ohne Bildschirm mit Beispieldaten und
speichert Screenshots. Daten landen in einem temporären Ordner, die echte
Datenbank wird nicht angefasst.

    QT_QPA_PLATFORM=offscreen uv run python scripts/readme_screenshots.py
"""
import os
import sys
import tempfile
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["INSTRUMENT_READER_DATA_DIR"] = tempfile.mkdtemp(prefix="ir-screens-")
os.environ["INSTRUMENT_READER_EXCEL"] = "0"

import cv2
import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QSplitter

from instrument_reader.core.calculation import CalculationChannel
from instrument_reader.core.preprocessing import PreprocessingConfig
from instrument_reader.core.roi import DisplayType, ROIConfig, ROIShape

OUT = Path(__file__).resolve().parent.parent / "docs" / "images"

# --- Künstliche Laborszene --------------------------------------------------

# Segmente a..g als (x0, y0, x1, y1) in einer 1x2-Zelle
_SEG = {
    "a": (0.1, 0.0, 0.9, 0.1), "b": (0.9, 0.05, 1.0, 0.95), "c": (0.9, 1.05, 1.0, 1.95),
    "d": (0.1, 1.9, 0.9, 2.0), "e": (0.0, 1.05, 0.1, 1.95), "f": (0.0, 0.05, 0.1, 0.95),
    "g": (0.1, 0.95, 0.9, 1.05),
}
_DIGITS = {
    "0": "abcdef", "1": "bc", "2": "abged", "3": "abgcd", "4": "fgbc", "5": "afgcd",
    "6": "afgedc", "7": "abc", "8": "abcdefg", "9": "abcdfg", "-": "g", " ": "",
}


def seven_seg(img, text, x, y, h, on, off):
    """Zeichnet text als 7-Segment-Ziffern; ein Punkt hängt am vorigen Zeichen."""
    w = h * 0.5
    gap = w * 0.45
    for ch in text:
        if ch == ".":
            cv2.circle(img, (int(x - gap * 0.5), int(y + h * 0.98)), max(2, int(h * 0.06)), on, -1)
            continue
        lit = _DIGITS[ch]
        for name, (x0, y0, x1, y1) in _SEG.items():
            p0 = (int(x + x0 * w), int(y + y0 * h / 2))
            p1 = (int(x + x1 * w), int(y + y1 * h / 2))
            cv2.rectangle(img, p0, p1, on if name in lit else off, -1)
        x += w + gap


def lab_scene(mass: float, flow: float, temp: float) -> np.ndarray:
    W, H = 1280, 720
    img = np.zeros((H, W, 3), np.uint8)
    # Wand und Labortisch
    for yy in range(H):
        t = yy / H
        img[yy] = (int(205 - 40 * t), int(200 - 40 * t), int(190 - 40 * t))
    cv2.rectangle(img, (0, 560), (W, H), (70, 80, 92), -1)
    cv2.line(img, (0, 560), (W, 560), (110, 120, 130), 3)

    # Waage
    cv2.rectangle(img, (90, 330), (560, 570), (225, 225, 222), -1)
    cv2.rectangle(img, (90, 330), (560, 570), (160, 160, 160), 2)
    cv2.rectangle(img, (130, 300), (520, 335), (190, 190, 190), -1)  # Wägeplatte
    # Becherglas mit Flüssigkeit auf der Platte
    cv2.rectangle(img, (250, 170), (400, 300), (235, 238, 240), -1)
    cv2.rectangle(img, (252, 215), (398, 298), (205, 170, 120), -1)
    cv2.rectangle(img, (250, 170), (400, 300), (150, 150, 150), 2)
    for k in range(4):
        cv2.line(img, (380, 200 + 22 * k), (398, 200 + 22 * k), (120, 120, 120), 1)
    # LCD
    cv2.rectangle(img, (150, 380), (500, 480), (40, 45, 40), -1)
    cv2.rectangle(img, (160, 390), (490, 470), (120, 160, 120), -1)
    seven_seg(img, f"{mass:7.3f}", 175, 402, 56, (25, 40, 25), (110, 148, 110))
    cv2.putText(img, "g", (455, 458), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (25, 40, 25), 3, cv2.LINE_AA)
    for i, lbl in enumerate(("ON/OFF", "TARE", "MODE")):
        cx = 190 + i * 130
        cv2.ellipse(img, (cx, 525), (40, 16), 0, 0, 360, (90, 90, 95), -1)
        cv2.putText(img, lbl, (cx - 30, 530), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (235, 235, 235), 1, cv2.LINE_AA)

    # Rotameter
    tx0, tx1, ty0, ty1 = 760, 820, 90, 520
    cv2.rectangle(img, (tx0 - 30, ty0 - 30), (tx1 + 90, ty1 + 40), (60, 60, 65), -1)
    cv2.rectangle(img, (tx0, ty0), (tx1, ty1), (118, 126, 130), -1)
    cv2.rectangle(img, (tx1 - 14, ty0), (tx1 - 9, ty1), (190, 196, 198), -1)  # Glanz
    for v in range(0, 101, 5):
        yy = int(ty1 - (ty1 - ty0) * v / 100)
        ln = 22 if v % 10 == 0 else 12
        cv2.line(img, (tx1, yy), (tx1 + ln, yy), (240, 240, 240), 2)
        if v % 20 == 0:
            cv2.putText(img, str(v), (tx1 + 28, yy + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (240, 240, 240), 1, cv2.LINE_AA)
    cv2.putText(img, "Nl/h", (tx1 + 24, ty1 + 28), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (240, 240, 240), 1, cv2.LINE_AA)
    fy = int(ty1 - (ty1 - ty0) * flow / 100)
    pts = np.array([[tx0 + 8, fy], [tx1 - 8, fy], [tx1 - 18, fy + 26], [tx0 + 18, fy + 26]], np.int32)
    cv2.fillPoly(img, [pts], (40, 40, 190))

    # Temperaturregler
    cv2.rectangle(img, (960, 360), (1200, 520), (45, 45, 50), -1)
    cv2.rectangle(img, (980, 380), (1180, 450), (15, 15, 15), -1)
    seven_seg(img, f"{temp:4.1f}", 995, 390, 50, (40, 40, 240), (25, 25, 45))
    cv2.putText(img, "C", (1150, 440), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (40, 40, 240), 2, cv2.LINE_AA)
    cv2.putText(img, "TEMP CONTROL", (1000, 490), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1, cv2.LINE_AA)

    # Mehr Wand und Tisch, damit das Bild das Videofeld ohne schwarze Balken füllt
    top = np.repeat(img[:1], PAD, axis=0)
    bottom = np.repeat(img[-1:], PAD, axis=0)
    img = np.vstack([top, img, bottom])
    return cv2.GaussianBlur(img, (3, 3), 0)


PAD = 170  # Zusätzliche Bildhöhe oben und unten
ROT_TOP, ROT_BOTTOM = 90 + PAD, 520 + PAD  # Röhrenenden im Bild


def make_rois():
    waage = ROIConfig(name="Waage", coordinates=[160, 390 + PAD, 330, 80], unit="g", decimal_places=3)
    rot = ROIConfig(
        name="Rotameter", shape=ROIShape.RULER, display_type=DisplayType.ANALOG, unit="Nl/h",
        coordinates=[[790, ROT_TOP], [790, ROT_BOTTOM]], strip_width=56,
        calibration_marks=[{"pos": 1 - v / 100, "value": v} for v in (0, 20, 40, 60, 80, 100)],
    )
    temp = ROIConfig(name="Temperatur", coordinates=[980, 380 + PAD, 200, 70], unit="°C", decimal_places=1)
    return waage, rot, temp


def readings_for(rois, mass, flow, temp, ts):
    waage, rot, tmp = rois
    rel = 1 - flow / 100
    y = ROT_TOP + rel * (ROT_BOTTOM - ROT_TOP)
    return [
        dict(roi_id=waage.id, roi_name="Waage", raw_text=f"{mass:.3f}", parsed_value=round(mass, 3), unit="g",
             confidence=0.97, is_valid=True, timestamp=ts),
        dict(roi_id=rot.id, roi_name="Rotameter", raw_text="", parsed_value=round(flow, 1), unit="Nl/h",
             confidence=0.92, is_valid=True, rel_pos=rel, edge_pts=[[762, y], [818, y]], timestamp=ts),
        dict(roi_id=tmp.id, roi_name="Temperatur", raw_text=f"{temp:.1f}", parsed_value=round(temp, 1), unit="°C",
             confidence=0.95, is_valid=True, timestamp=ts),
    ]


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def save(widget, name):
    QApplication.processEvents()
    widget.grab().save(str(OUT / name))
    print("geschrieben:", OUT / name)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv)
    from instrument_reader.gui.main_window import MainWindow
    from instrument_reader.gui.preprocessing_dialog import PreprocessingDialog
    from instrument_reader.gui.runs_dashboard import RunsDashboardDialog

    win = MainWindow()
    win.resize(1600, 960)
    win.show()
    win.control_panel.camera_source.setText("0  –  USB Kamera (Labor)")

    rois = make_rois()
    win.video_widget.rois.extend(rois)
    for r in rois:
        win.control_panel.add_roi(r)
    win.ocr_worker.update_rois(win.video_widget.rois)

    ch = CalculationChannel(name="Massenstrom", unit="g/h", formula="rate(m, 30) * 3600",
                            variables={"m": "Waage"}, window_seconds=30, decimal_places=2)
    win.calc_engine.add_channel(ch)
    win.control_panel.set_calculated_channels([ch])

    # Beispielexperiment: verdampfende Flüssigkeit, drei Runs bei verschiedenen Temperaturen
    db = win.db_writer
    t_start = time.time() - 3 * 3600
    exp_id = db.create_experiment("Verdampfung Ethanol", "Ethanol", "Massenverlust bei drei Temperaturen")
    for i, (temp, rate_gh) in enumerate(((25.0, 4.2), (30.0, 6.1), (35.0, 8.9))):
        db.create_run(exp_id, parameters={"Temperatur": f"{temp:g} °C", "Volumenstrom": "40 Nl/h"},
                      notes=f"Stufe {i + 1}")
        t0 = t_start + i * 3600
        for k in range(0, 1800, 10):
            m = 92.0 - i * 6 - rate_gh * k / 3600
            db.insert_readings(readings_for(rois, m, 40 + 0.4 * np.sin(k / 60), temp, t0 + k), timestamp=t0 + k)
        # Start-/Endzeiten wie bei einer echten Messung
        with sqlite3.connect(db.db.db_path) as conn:
            conn.execute("UPDATE runs SET started_at = ? WHERE id = ?", (_iso(t0), db.current_run_id))
            if i < 2:
                conn.execute("UPDATE runs SET ended_at = ? WHERE id = ?", (_iso(t0 + 1800), db.current_run_id))
        if i < 2:
            run_id = db.current_run_id
            db.end_run()
            with sqlite3.connect(db.db.db_path) as conn:
                conn.execute("UPDATE runs SET ended_at = ? WHERE id = ?", (_iso(t0 + 1800), run_id))
    with sqlite3.connect(db.db.db_path) as conn:
        conn.execute("UPDATE experiments SET created_at = ? WHERE id = ?", (_iso(t_start - 60), exp_id))
    win.set_active_run(db.current_run_id, exp_id, "Verdampfung Ethanol")

    # Live-Werte einspeisen, damit Rechenkanal und Stufenerkennung echte Werte zeigen
    now = time.time()
    mass = flow = temp = 0.0
    for k in range(60):
        mass, flow, temp = 84.397 + 0.0025 * (59 - k), 42.0 + 0.3 * np.sin(k / 7), 35.0
        win.on_readings_ready(readings_for(rois, mass, flow, temp, now - 60 + k), timestamp=now - 60 + k)
    frame = lab_scene(mass, flow, temp)
    win.video_widget.update_frame(frame)

    # So sieht das Ablesung-Panel aus, wenn Excel mit dem Labjournal offen ist
    clock = lambda h, m, s: (h * 3600 + m * 60 + s) / 86400
    win.control_panel.show_excel_target(SimpleNamespace(
        workbook="Labjournal_2026.xlsx", sheet="Verdampfung", row=14, context="Stufe 3",
        values={"Masse_0": 84.512, "Zeit_0": clock(17, 41, 5), "Masse_1": None, "Zeit_1": None}))
    for text in ("✓ T0 17:41:05  Zeile 14: Masse_0 = 84.512, Zeit_0 = 17:41:05",
                 "✓ T1 17:12:48  Zeile 13: Masse_1 = 86.630, Zeit_1 = 17:12:48"):
        win.control_panel.add_ablesung_history(text)

    def shoot():
        save(win, "hauptfenster.png")
        # Rechte Seite nach unten gescrollt: Live-Werte und Rechenkanäle
        from PySide6.QtWidgets import QScrollArea
        scroll = win.control_panel.findChild(QScrollArea)
        scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
        save(win.control_panel, "messwerte.png")

        dash = RunsDashboardDialog(win.db_writer.db, current_run_id=db.current_run_id, parent=win)
        dash.resize(1500, 560)
        dash.show()
        dash.findChild(QSplitter).setSizes([900, 600])
        it = dash.tree.topLevelItem(0)
        if it is not None:
            it.setExpanded(True)
            if it.childCount():
                dash.tree.setCurrentItem(it.child(1))
        save(dash, "runs-dashboard.png")
        dash.close()

        x, y, w, h = rois[0].coordinates
        pre = PreprocessingDialog(frame[y:y + h, x:x + w].copy(), PreprocessingConfig(upscale_factor=2), parent=win)
        pre.show()
        QApplication.processEvents()
        save(pre, "vorverarbeitung.png")
        pre.close()
        app.quit()

    QTimer.singleShot(500, shoot)
    app.exec()


if __name__ == "__main__":
    main()
