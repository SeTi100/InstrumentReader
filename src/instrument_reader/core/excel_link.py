"""Writes scale readings straight into the lab journal that is open in Excel (Windows, COM).

The row is always the row of the active cell in Excel, so the user decides where a value
goes. Columns are found by their header (``Masse_0``, ``Zeit_0``, ...), preferably in the
Excel table (ListObject) that contains the active cell. Nothing is ever overwritten: if a
target cell already holds something, the write is refused and Excel stays untouched.

Which headers are used is configurable (``ExcelColumns``): each of the four roles takes a
header name or a column letter ("D"). A header with that exact name wins over the letter.
Optionally a sheet name restricts writing to that sheet.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

# slot -> (mass role, time role). Roles are the keys of Target.cols/values; the user decides
# which header or column stands behind each role (ExcelColumns).
SLOTS: Dict[str, tuple] = {"T0": ("Masse_0", "Zeit_0"), "T1": ("Masse_1", "Zeit_1")}
REQUIRED = ("Masse_0", "Masse_1", "Zeit_0", "Zeit_1")
TIME_ROLES = ("Zeit_0", "Zeit_1")
MAX_COLUMN = 16384  # XFD
SETTINGS_KEY = "excel_ziel"
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


def column_index(spec: str) -> Optional[int]:
    """1-based column number if spec is a column letter ("A", "ab", "XFD"), else None."""
    s = str(spec or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{1,3}", s):
        return None
    n = 0
    for ch in s:
        n = n * 26 + ord(ch) - 64
    return n if n <= MAX_COLUMN else None


@dataclass
class ExcelColumns:
    """Where T0/T1 write: an optional sheet name (empty = the active sheet) and, per role,
    a header name or a column letter. ``roi`` is the live reading whose value is written
    (empty = pick one automatically)."""
    roi: str = ""
    sheet: str = ""
    mass0: str = "Masse_0"
    time0: str = "Zeit_0"
    mass1: str = "Masse_1"
    time1: str = "Zeit_1"

    def names(self) -> Dict[str, str]:
        """role -> what the user entered (header name or column letter)."""
        return {"Masse_0": self.mass0.strip(), "Zeit_0": self.time0.strip(),
                "Masse_1": self.mass1.strip(), "Zeit_1": self.time1.strip()}

    def label(self, role: str) -> str:
        return self.names()[role] or role

    def problem(self) -> str:
        """Why this configuration can't be used, or "" if it can."""
        names = self.names()
        empty = [role for role, v in names.items() if not v]
        if empty:
            return f"Spalte für {', '.join(empty)} ist leer – bitte einen Namen oder Buchstaben eintragen"
        seen: Dict[str, str] = {}
        for v in names.values():
            key = _norm(v)
            if key in seen:
                return f"„{v}“ ist mehrfach eingetragen – jede Spalte nur einmal verwenden"
            seen[key] = v
        return ""

    @classmethod
    def from_dict(cls, data) -> "ExcelColumns":
        default = cls()
        if not isinstance(data, dict):
            return default
        vals = {k: str(data[k]) for k in asdict(default) if isinstance(data.get(k), str)}
        return cls(**{**asdict(default), **vals})


def load_columns(settings_file: Path) -> ExcelColumns:
    try:
        data = json.loads(Path(settings_file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ExcelColumns()
    return ExcelColumns.from_dict(data.get(SETTINGS_KEY) if isinstance(data, dict) else None)


def save_columns(settings_file: Path, columns: ExcelColumns) -> None:
    """Stores the configuration in settings.json, keeping every other setting there."""
    path = Path(settings_file)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data[SETTINGS_KEY] = asdict(columns)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def find_columns(headers: List[object], first_col: int = 1,
                 names: Optional[Dict[str, str]] = None) -> Dict[str, int]:
    """Maps the known headers to 1-based column numbers. ``names`` is role -> header
    (default: the role itself). Context headers match by prefix ("V_MFC [Nl/min]" counts
    as V_MFC)."""
    names = names or {r: r for r in REQUIRED}
    cols: Dict[str, int] = {}
    for i, h in enumerate(headers):
        n = _norm(h)
        for role, name in names.items():
            if n and n == _norm(name) and role not in cols:
                cols[role] = first_col + i
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
    def __init__(self, app_provider: Optional[Callable[[], object]] = None,
                 columns: Optional[ExcelColumns] = None):
        self._provider = app_provider or _default_app
        self.columns = columns or ExcelColumns()

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
        problem = self.columns.problem()
        if problem:
            raise ExcelError(problem)
        ws = app.ActiveSheet
        sheet = self.columns.sheet.strip()
        if sheet and _norm(ws.Name) != _norm(sheet):
            raise ExcelError(f"Cursor steht auf Blatt „{ws.Name}“, Ziel ist Blatt „{sheet}“ – "
                             "in Excel auf das Blatt wechseln oder das Blatt-Feld leeren")
        row = app.ActiveCell.Row
        header_row, cols = self._locate(ws, row)
        if not all(name in cols for name in REQUIRED):
            missing = ", ".join(self.columns.label(n) for n in REQUIRED if n not in cols)
            raise ExcelError(f"Blatt „{ws.Name}“: Spalten {missing} nicht gefunden")
        if row <= header_row:
            raise ExcelError(f"Blatt „{ws.Name}“: Cursor steht in Zeile {row}, über bzw. auf der Kopfzeile")
        values = {name: ws.Cells(row, c).Value for name, c in cols.items()}
        return Target(workbook=wb.Name, sheet=ws.Name, row=row, cols=cols, values=values)

    def _resolve(self, headers: List[object], first_col: int):
        """Columns for one candidate header row, and whether that row is a header row:
        every role given by name must be found there. Roles given as a column letter use
        that column unless a header of the same name exists."""
        names = self.columns.names()
        cols = find_columns(headers, first_col, names)
        by_name = [r for r, v in names.items() if column_index(v) is None]
        is_header = all(r in cols for r in by_name)
        if not by_name:  # only letters: a row counts if it holds a context header
            is_header = any(n in cols for n, _u in CONTEXT)
        for role, v in names.items():
            if role not in cols and column_index(v) is not None:
                cols[role] = column_index(v)
        return cols, is_header

    def _locate(self, ws, row: int):
        """(header row, columns) from the Excel table that contains the row, else from the
        first rows of the sheet that hold the headers. With only column letters and no
        header row found, any row is allowed (header row 0)."""
        letters_only = all(column_index(v) is not None for v in self.columns.names().values())
        for lo in ws.ListObjects:
            hdr = lo.HeaderRowRange
            if hdr is None:
                continue
            rng = lo.Range
            if not (rng.Row <= row < rng.Row + rng.Rows.Count):
                continue
            cols, is_header = self._resolve(list(hdr.Value[0]), hdr.Column)
            if is_header or letters_only:
                return hdr.Row, cols
        used = ws.UsedRange
        first_col = used.Column
        last_row = min(used.Row + used.Rows.Count - 1, HEADER_SCAN_ROWS, row - 1)
        best = (0, self._resolve([], first_col)[0])
        found = letters_only
        for r in range(used.Row, last_row + 1):
            vals = ws.Range(ws.Cells(r, first_col), ws.Cells(r, first_col + used.Columns.Count - 1)).Value
            cols, is_header = self._resolve(list(vals[0]) if isinstance(vals, tuple) else [vals], first_col)
            if is_header:
                best, found = (r, cols), True  # the last header row above the cursor wins
            elif not found and len(cols) > len(best[1]):
                best = (r, cols)  # only to name what is missing
        return best

    def write(self, slot: str, value: float, when: datetime) -> Target:
        """Writes mass and time of ``slot`` ("T0"/"T1") into the active row. Refuses (ExcelError)
        if either cell is filled already, so a double press changes nothing."""
        return self._com(lambda: self._write(slot, value, when))

    def _write(self, slot: str, value: float, when: datetime) -> Target:
        t = self._target()
        filled = [self.columns.label(h) for h in t.filled(slot)]
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
