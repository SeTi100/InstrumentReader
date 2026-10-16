"""Backup log of every T0/T1 press ("Ablesung").

The lab journal open in Excel is where the values go (see ``excel_link``). Independent of
that, every press is appended here with the OCR value, the time and what happened (written
to which row, or why not), so nothing read at a press is ever lost:
``<data folder>/ablesungen/Ablesungen_JJJJ-MM-TT.csv``.
"""
from __future__ import annotations

import csv
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

CSV_HEADER = ["Datum", "Uhrzeit", "Taste", "Wert", "Gueltig", "Rohtext", "ROI", "Run",
              "Zeit_Messwert", "Ergebnis", "Mappe", "Blatt", "Zeile"]


class AblesungLog:
    def __init__(self, folder: str | Path):
        self.folder = Path(folder)

    def path_for(self, when: datetime) -> Path:
        return self.folder / f"Ablesungen_{when.date().isoformat()}.csv"

    def record(
        self,
        slot: str,
        value: Optional[float],
        valid: bool,
        when: datetime,
        result: str,
        raw_text: str = "",
        roi: str = "",
        run_id: Optional[int] = None,
        reading_time: Optional[datetime] = None,
        workbook: str = "",
        sheet: str = "",
        row: Optional[int] = None,
    ) -> Path:
        """Appends one press and forces it to disk. Raises OSError if that fails."""
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.path_for(when)
        new = not path.exists()
        with open(path, "a", newline="", encoding="utf-8-sig" if new else "utf-8") as f:
            w = csv.writer(f, delimiter=";")
            if new:
                w.writerow(CSV_HEADER)
            w.writerow([
                when.date().isoformat(),
                when.strftime("%H:%M:%S"),
                slot,
                "" if value is None else repr(float(value)),
                1 if valid else 0,
                raw_text,
                roi,
                "" if run_id is None else run_id,
                reading_time.replace(microsecond=0).isoformat(sep=" ") if reading_time else "",
                result,
                workbook,
                sheet,
                "" if row is None else row,
            ])
            f.flush()
            os.fsync(f.fileno())
        return path

    @staticmethod
    def read(path: str | Path) -> list[dict]:
        with open(path, newline="", encoding="utf-8-sig") as f:
            return list(csv.DictReader(f, delimiter=";"))
