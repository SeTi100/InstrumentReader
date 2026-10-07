"""Misst die OCR-Zeit pro Frame für eine typische ROI-Konfiguration.

Erzeugt ein synthetisches Frame mit einer 5-stelligen Waagenanzeige
(Container + 5 Ziffern-Slots) und einer Standalone-ROI und ruft
``OCRWorker.process_frame`` mehrfach auf.

    python scripts/benchmark_ocr.py [--runs 10] [--blank-slots 1]

``--blank-slots`` lässt die ersten N Slots leer (dunkle, führende
Stellen), damit die Fallback-Kette pro Slot mitgemessen wird.
"""
import argparse
import os
import statistics
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from instrument_reader.core.roi import ROIConfig  # noqa: E402
from instrument_reader.gui.ocr_worker import OCRWorker  # noqa: E402

SLOT_W, SLOT_H = 60, 90
X0, Y0 = 40, 40


def make_frame(digits: str, standalone: str) -> np.ndarray:
    frame = np.full((300, 600, 3), 255, dtype=np.uint8)
    for i, ch in enumerate(digits):
        if ch == " ":
            continue
        x = X0 + i * SLOT_W + 8
        cv2.putText(frame, ch, (x, Y0 + SLOT_H - 15), cv2.FONT_HERSHEY_SIMPLEX, 2.6, (0, 0, 0), 6)
    cv2.putText(frame, standalone, (X0 + 10, 250), cv2.FONT_HERSHEY_SIMPLEX, 2.0, (0, 0, 0), 5)
    return frame


def make_rois(n_slots: int) -> list[ROIConfig]:
    slots = [ROIConfig(name=f"S{i + 1}", coordinates=[X0 + i * SLOT_W, Y0, SLOT_W, SLOT_H]) for i in range(n_slots)]
    container = ROIConfig(
        name="Waage", is_container=True, decimal_position=3,
        coordinates=[X0 - 5, Y0 - 5, n_slots * SLOT_W + 10, SLOT_H + 10],
        sub_roi_ids=[s.name for s in slots], unit="g",
    )
    standalone = ROIConfig(name="Temperatur", coordinates=[X0, 180, 300, 90], unit="°C")
    return [container, *slots, standalone]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--blank-slots", type=int, default=1)
    args = parser.parse_args()

    digits = " " * args.blank_slots + "15432"[args.blank_slots:]
    frame = make_frame(digits, "2345")
    rois = make_rois(len(digits))
    worker = OCRWorker()

    worker.process_frame(frame, rois)  # Warm-up
    times = []
    for _ in range(args.runs):
        t0 = time.perf_counter()
        readings = worker.process_frame(frame, rois)
        times.append((time.perf_counter() - t0) * 1000)

    by_name = {r["roi_name"]: r for r in readings}
    print(f"Anzeige: '{digits}'  ->  Waage={by_name['Waage']['raw_text']!r}, "
          f"Temperatur={by_name['Temperatur']['raw_text']!r}")
    print(f"process_frame: Median {statistics.median(times):.0f} ms, "
          f"min {min(times):.0f} ms, max {max(times):.0f} ms über {args.runs} Läufe")


if __name__ == "__main__":
    main()
