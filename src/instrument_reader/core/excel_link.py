"""Writes live readings straight into the lab journal that is open in Excel (Windows, COM).

The row is always the row of the active cell in Excel, so the user decides where a value
goes. Columns are found by their header, preferably in the Excel table (ListObject) that
contains the active cell. Nothing is ever overwritten: if a target cell already holds
something, the write is refused and Excel stays untouched.

What a key press writes is configurable (``AblesungConfig``): per key (T0 = F9, T1 = F10)
any number of "live reading -> column" pairs plus one column for the time of the press.
A column is a header name or a column letter ("D"); a header with exactly that name wins
over the letter. Optionally a sheet name restricts writing to that sheet.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

SLOT_KEYS = {"T0": "F9", "T1": "F10"}
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
class ValueColumn:
    """One live reading and the column it goes into. roi "" = the scale (picked automatically)."""
    roi: str = ""
    column: str = ""


@dataclass
class SlotConfig:
    values: List[ValueColumn] = field(default_factory=list)
    time: str = ""  # column for the time of the press; "" = don't write a time

    def columns(self) -> List[str]:
        cols = [v.column.strip() for v in self.values]
        return cols + [self.time.strip()] if self.time.strip() else cols


def _default_slots() -> Dict[str, SlotConfig]:
    return {"T0": SlotConfig([ValueColumn("", "Masse_0")], "Zeit_0"),
            "T1": SlotConfig([ValueColumn("", "Masse_1")], "Zeit_1")}


@dataclass
class AblesungConfig:
    """Where T0/T1 write: an optional sheet name (empty = the active sheet) and per key
    the value columns and the time column."""
    sheet: str = ""
    slots: Dict[str, SlotConfig] = field(default_factory=_default_slots)

    def columns(self, slot: Optional[str] = None) -> List[str]:
        """Every configured column (header name or letter), of one key or of all."""
        keys = [slot] if slot else list(self.slots)
        return [c for k in keys for c in self.slots[k].columns()]

    def problem(self) -> str:
        """Why this configuration can't be used, or "" if it can."""
        for key, sc in self.slots.items():
            if any(not v.column.strip() for v in sc.values):
                return f"{key}: eine Spalte ist leer – bitte einen Namen oder Buchstaben eintragen"
        seen = set()
        for c in self.columns():
            if _norm(c) in seen:
                return f"Spalte „{c}“ ist mehrfach eingetragen – jede Spalte nur einmal verwenden"
            seen.add(_norm(c))
        if not seen:
            return "Keine Zielspalten eingetragen"
        return ""

    def to_dict(self) -> dict:
        return {"sheet": self.sheet,
                "slots": {k: {"time": sc.time, "values": [{"roi": v.roi, "column": v.column} for v in sc.values]}
                          for k, sc in self.slots.items()}}

    @classmethod
    def from_dict(cls, data) -> "AblesungConfig":
        cfg = cls()
        if not isinstance(data, dict):
            return cfg
        if isinstance(data.get("sheet"), str):
            cfg.sheet = data["sheet"]
        if "mass0" in data:  # first version: one value per key, one shared reading
            roi = data.get("roi") if isinstance(data.get("roi"), str) else ""
            for key, m, t in (("T0", "mass0", "time0"), ("T1", "mass1", "time1")):
                sc = cfg.slots[key]
                if isinstance(data.get(m), str):
                    sc.values = [ValueColumn(roi, data[m])]
                if isinstance(data.get(t), str):
                    sc.time = data[t]
            return cfg
        slots = data.get("slots")
        if isinstance(slots, dict):
            for key in cfg.slots:
                d = slots.get(key)
                if not isinstance(d, dict):
                    continue
                sc = SlotConfig(time=d["time"] if isinstance(d.get("time"), str) else "")
                for v in d.get("values") or []:
                    if isinstance(v, dict) and isinstance(v.get("column"), str):
                        sc.values.append(ValueColumn(v["roi"] if isinstance(v.get("roi"), str) else "", v["column"]))
                cfg.slots[key] = sc
        return cfg


def load_config(settings_file: Path) -> AblesungConfig:
    try:
        data = json.loads(Path(settings_file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return AblesungConfig()
    return AblesungConfig.from_dict(data.get(SETTINGS_KEY) if isinstance(data, dict) else None)


def save_config(settings_file: Path, config: AblesungConfig) -> None:
    """Stores the configuration in settings.json, keeping every other setting there."""
    path = Path(settings_file)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data[SETTINGS_KEY] = config.to_dict()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def find_columns(headers: List[object], first_col: int = 1, names=()) -> Dict[str, int]:
    """Maps each of ``names`` to the 1-based column whose header equals it (ignoring case)."""
    cols: Dict[str, int] = {}
    for i, h in enumerate(headers):
        n = _norm(h)
        for name in names:
            if n and n == _norm(name) and name not in cols:
                cols[name] = first_col + i
    return cols


def find_context(headers: List[object], first_col: int = 1) -> Dict[str, int]:
    """Context headers match by prefix ("V_MFC [Nl/min]" counts as V_MFC)."""
    cols: Dict[str, int] = {}
    for i, h in enumerate(headers):
        n = _norm(h)
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
    cols: Dict[str, int]  # configured column (as entered) -> column number
    values: Dict[str, object] = field(default_factory=dict)
    context_values: Dict[str, object] = field(default_factory=dict)

    @property
    def context(self) -> str:
        return format_context(self.context_values)

    def filled(self, columns: List[str]) -> List[str]:
        return [c for c in columns if not is_empty(self.values.get(c))]


def _default_app():
    import win32com.client
    return win32com.client.GetActiveObject("Excel.Application")


class ExcelLink:
    def __init__(self, app_provider: Optional[Callable[[], object]] = None,
                 config: Optional[AblesungConfig] = None):
        self._provider = app_provider or _default_app
        self.config = config or AblesungConfig()

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
        problem = self.config.problem()
        if problem:
            raise ExcelError(problem)
        ws = app.ActiveSheet
        sheet = self.config.sheet.strip()
        if sheet and _norm(ws.Name) != _norm(sheet):
            raise ExcelError(f"Cursor steht auf Blatt „{ws.Name}“, Ziel ist Blatt „{sheet}“ – "
                             "in Excel auf das Blatt wechseln oder das Blatt-Feld leeren")
        row = app.ActiveCell.Row
        header_row, cols, ctx = self._locate(ws, row)
        missing = [c for c in self.config.columns() if c not in cols]
        if missing:
            raise ExcelError(f"Blatt „{ws.Name}“: Spalten {', '.join(missing)} nicht gefunden")
        if row <= header_row:
            raise ExcelError(f"Blatt „{ws.Name}“: Cursor steht in Zeile {row}, über bzw. auf der Kopfzeile")
        values = {name: ws.Cells(row, c).Value for name, c in cols.items()}
        context_values = {name: ws.Cells(row, c).Value for name, c in ctx.items()}
        return Target(workbook=wb.Name, sheet=ws.Name, row=row, cols=cols, values=values,
                      context_values=context_values)

    def _resolve(self, headers: List[object], first_col: int) -> Tuple[Dict[str, int], Dict[str, int], bool]:
        """Columns and context columns for one candidate header row, and whether that row
        is a header row: every column given by name must be found there. Columns given as
        a letter use that column unless a header of the same name exists."""
        names = self.config.columns()
        cols = find_columns(headers, first_col, names)
        ctx = find_context(headers, first_col)
        by_name = [n for n in names if column_index(n) is None]
        is_header = all(n in cols for n in by_name)
        if not by_name:  # only letters: a row counts if it holds a context header
            is_header = bool(ctx)
        for n in names:
            if n not in cols and column_index(n) is not None:
                cols[n] = column_index(n)
        return cols, ctx, is_header

    def _locate(self, ws, row: int):
        """(header row, columns, context columns) from the Excel table that contains the
        row, else from the first rows of the sheet that hold the headers. With only column
        letters and no header row found, any row is allowed (header row 0)."""
        letters_only = all(column_index(n) is not None for n in self.config.columns())
        for lo in ws.ListObjects:
            hdr = lo.HeaderRowRange
            if hdr is None:
                continue
            rng = lo.Range
            if not (rng.Row <= row < rng.Row + rng.Rows.Count):
                continue
            cols, ctx, is_header = self._resolve(list(hdr.Value[0]), hdr.Column)
            if is_header or letters_only:
                return hdr.Row, cols, ctx
        used = ws.UsedRange
        first_col = used.Column
        last_row = min(used.Row + used.Rows.Count - 1, HEADER_SCAN_ROWS, row - 1)
        cols, ctx, _ = self._resolve([], first_col)
        best = (0, cols, ctx)
        found = letters_only
        # One COM call for all candidate rows: every call is a round trip to the Excel process.
        rows = ()
        if last_row >= used.Row:
            rows = ws.Range(ws.Cells(used.Row, first_col),
                            ws.Cells(last_row, first_col + used.Columns.Count - 1)).Value
            rows = rows if isinstance(rows, tuple) else ((rows,),)
        for r, vals in enumerate(rows, start=used.Row):
            cols, ctx, is_header = self._resolve(list(vals) if isinstance(vals, tuple) else [vals], first_col)
            if is_header:
                best, found = (r, cols, ctx), True  # the last header row above the cursor wins
            elif not found and len(cols) > len(best[1]):
                best = (r, cols, ctx)  # only to name what is missing
        return best

    def write(self, slot: str, values: List[Tuple[str, float]], when: datetime) -> Target:
        """Writes ``values`` ([(column, value)]) and the time of ``slot`` ("T0"/"T1") into the
        active row. Refuses (ExcelError) if any target cell is filled already, so a double
        press changes nothing."""
        return self._com(lambda: self._write(slot, values, when))

    def _write(self, slot: str, values: List[Tuple[str, float]], when: datetime) -> Target:
        t = self._target()
        sc = self.config.slots[slot]
        filled = t.filled(sc.columns())
        if filled:
            raise ExcelError(
                f"Zeile {t.row}: {', '.join(filled)} schon belegt – nichts geschrieben. "
                "Zum Ändern in Excel löschen und nochmal drücken.")
        ws = self._app().ActiveSheet
        for column, value in values:
            ws.Cells(t.row, t.cols[column]).Value2 = float(value)
            t.values[column] = float(value)
        time_col = sc.time.strip()
        if time_col:
            time_cell = ws.Cells(t.row, t.cols[time_col])
            if _norm(time_cell.NumberFormat) in ("general", "standard"):
                time_cell.NumberFormat = "hh:mm:ss"
            time_cell.Value2 = time_fraction(when)
            t.values[time_col] = time_fraction(when)
        return t

    def flash(self, t: Target, slot: str) -> list:
        """Colours the written cells light green if they had no fill; returns what to undo."""
        def run():
            ws = self._app().ActiveSheet
            done = []
            for h in self.config.slots[slot].columns():
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
