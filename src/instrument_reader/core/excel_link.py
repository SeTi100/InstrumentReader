"""Writes scale readings straight into the lab journal that is open in Excel (Windows, COM).

The row is always the row of the active cell in Excel, so the user decides where a value
goes. Columns are found by their header (``Masse_0``, ``Zeit_0``, ...), preferably in the
Excel table (ListObject) that contains the active cell. Nothing is ever overwritten: if a
target cell already holds something, the write is refused and Excel stays untouched.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional

# slot -> (mass header, time header)
SLOTS: Dict[str, tuple] = {"T0": ("Masse_0", "Zeit_0"), "T1": ("Masse_1", "Zeit_1")}
REQUIRED = ("Masse_0", "Masse_1", "Zeit_0", "Zeit_1")
# Shown next to the row so the user sees which step it is
CONTEXT = (("Temperatur", "°C"), ("V_MFC", "Nl/min"))

XL_NONE = -4142
FLASH_COLOR = 206 * 65536 + 239 * 256 + 198  # light green (Excel BGR)
HEADER_SCAN_ROWS = 40
RPC_E_CALL_REJECTED = -2147418111


class ExcelError(Exception):
    """A message for the user; nothing was written."""


def _norm(text) -> str:
    return str(text or "").strip().lower()


def find_columns(headers: List[object], first_col: int = 1) -> Dict[str, int]:
    """Maps the known headers to 1-based column numbers. Context headers match by prefix
    ("V_MFC [Nl/min]" counts as V_MFC)."""
    cols: Dict[str, int] = {}
    for i, h in enumerate(headers):
        n = _norm(h)
        for name in REQUIRED:
            if n == name.lower() and name not in cols:
                cols[name] = first_col + i
        for name, _unit in CONTEXT:
            if n.startswith(name.lower()) and name not in cols:
                cols[name] = first_col + i
    return cols


def is_empty(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def format_context(values: Dict[str, object]) -> str:
    parts = []
    for name, unit in CONTEXT:
        v = values.get(name)
        if not is_empty(v):
            parts.append(f"{v:g} {unit}" if isinstance(v, (int, float)) else f"{v} {unit}")
    return " · ".join(parts)


def time_fraction(when: datetime) -> float:
    """Time of day as Excel stores it (fraction of a day), to the second."""
    return (when.hour * 3600 + when.minute * 60 + when.second) / 86400


@dataclass
class Target:
    workbook: str
    sheet: str
    row: int
    cols: Dict[str, int]
    values: Dict[str, object] = field(default_factory=dict)

    @property
    def context(self) -> str:
        return format_context(self.values)

    def filled(self, slot: str) -> List[str]:
        return [h for h in SLOTS[slot] if not is_empty(self.values.get(h))]


def _default_app():
    import win32com.client
    return win32com.client.GetActiveObject("Excel.Application")


class ExcelLink:
    def __init__(self, app_provider: Optional[Callable[[], object]] = None):
        self._provider = app_provider or _default_app

    def _app(self):
        try:
            return self._provider()
        except ImportError:
            raise ExcelError("Excel-Anbindung nicht verfügbar (pywin32 fehlt)")
        except Exception:
            raise ExcelError("Excel ist nicht geöffnet")

    def _com(self, fn):
        """Runs COM calls and turns COM failures into user messages."""
        try:
            return fn()
        except ExcelError:
            raise
        except Exception as e:
            code = getattr(e, "hresult", None) or (e.args[0] if e.args and isinstance(e.args[0], int) else None)
            if code == RPC_E_CALL_REJECTED:
                raise ExcelError("Excel ist beschäftigt – wird gerade eine Zelle bearbeitet? Enter oder Esc drücken")
            raise ExcelError(f"Excel-Fehler: {e}")

    def target(self) -> Target:
        """Where a press would write right now: the active cell's row."""
        return self._com(self._target)

    def _target(self) -> Target:
        app = self._app()
        wb = app.ActiveWorkbook
        if wb is None:
            raise ExcelError("In Excel ist keine Arbeitsmappe offen")
        ws = app.ActiveSheet
        row = app.ActiveCell.Row
        header_row, cols = self._locate(ws, row)
        if not all(name in cols for name in REQUIRED):
            missing = ", ".join(n for n in REQUIRED if n not in cols)
            raise ExcelError(f"Blatt „{ws.Name}“: Spalten {missing} nicht gefunden")
        if row <= header_row:
            raise ExcelError(f"Blatt „{ws.Name}“: Cursor steht in Zeile {row}, über bzw. auf der Kopfzeile")
        values = {name: ws.Cells(row, c).Value for name, c in cols.items()}
        return Target(workbook=wb.Name, sheet=ws.Name, row=row, cols=cols, values=values)

    def _locate(self, ws, row: int):
        """(header row, columns) from the Excel table that contains the row, else from the
        first rows of the sheet that hold the headers."""
        for lo in ws.ListObjects:
            hdr = lo.HeaderRowRange
            if hdr is None:
                continue
            rng = lo.Range
            if not (rng.Row <= row < rng.Row + rng.Rows.Count):
                continue
            cols = find_columns(list(hdr.Value[0]), hdr.Column)
            if all(name in cols for name in REQUIRED):
                return hdr.Row, cols
        used = ws.UsedRange
        first_col = used.Column
        last_row = min(used.Row + used.Rows.Count - 1, HEADER_SCAN_ROWS, row - 1)
        best = (0, {})
        for r in range(used.Row, last_row + 1):
            vals = ws.Range(ws.Cells(r, first_col), ws.Cells(r, first_col + used.Columns.Count - 1)).Value
            cols = find_columns(list(vals[0]) if isinstance(vals, tuple) else [vals], first_col)
            if all(name in cols for name in REQUIRED):
                best = (r, cols)  # the last header row above the cursor wins
        return best

    def write(self, slot: str, value: float, when: datetime) -> Target:
        """Writes mass and time of ``slot`` ("T0"/"T1") into the active row. Refuses (ExcelError)
        if either cell is filled already, so a double press changes nothing."""
        return self._com(lambda: self._write(slot, value, when))

    def _write(self, slot: str, value: float, when: datetime) -> Target:
        t = self._target()
        filled = t.filled(slot)
        if filled:
            raise ExcelError(
                f"Zeile {t.row}: {' und '.join(filled)} schon belegt – nichts geschrieben. "
                "Zum Ändern in Excel löschen und nochmal drücken.")
        ws = self._app().ActiveSheet
        mass_h, time_h = SLOTS[slot]
        mass_cell = ws.Cells(t.row, t.cols[mass_h])
        time_cell = ws.Cells(t.row, t.cols[time_h])
        mass_cell.Value2 = float(value)
        if _norm(time_cell.NumberFormat) in ("general", "standard"):
            time_cell.NumberFormat = "hh:mm:ss"
        time_cell.Value2 = time_fraction(when)
        t.values[mass_h], t.values[time_h] = float(value), time_fraction(when)
        return t

    def flash(self, t: Target, slot: str) -> list:
        """Colours the written cells light green if they had no fill; returns what to undo."""
        def run():
            ws = self._app().ActiveSheet
            done = []
            for h in SLOTS[slot]:
                cell = ws.Cells(t.row, t.cols[h])
                if cell.Interior.ColorIndex == XL_NONE:
                    cell.Interior.Color = FLASH_COLOR
                    done.append((ws, t.row, t.cols[h]))
            return done
        try:
            return self._com(run)
        except ExcelError:
            return []

    def unflash(self, cells: list) -> list:
        """Removes the green again; returns the cells that could not be reached yet (Excel
        busy), to retry later. Values are never touched."""
        left = []
        for ws, row, col in cells:
            try:
                cell = ws.Cells(row, col)
                if cell.Interior.Color == FLASH_COLOR:
                    cell.Interior.ColorIndex = XL_NONE
            except Exception as e:
                code = getattr(e, "hresult", None) or (e.args[0] if e.args and isinstance(e.args[0], int) else None)
                if code == RPC_E_CALL_REJECTED:
                    left.append((ws, row, col))
        return left
