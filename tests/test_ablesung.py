from datetime import datetime

import pytest

from instrument_reader.core.ablesung import AblesungLog
from instrument_reader.core.excel_link import (
    FLASH_COLOR, XL_NONE, AblesungConfig, ExcelError, ExcelLink, SlotConfig, ValueColumn,
    column_index, find_columns, find_context, format_context, load_config, save_config, time_fraction,
)

WHEN = datetime(2026, 10, 15, 10, 8, 56)
HEADERS = ["Temperatur", "V_MFC [Nl/min]", "Masse_0", "Masse_1", "Zeit_0", "Zeit_1", "mg_C FID [RF1]"]


def test_find_columns_by_header_with_offset():
    cols = find_columns(HEADERS, 2, AblesungConfig().columns())  # table starts in column B like the journal
    assert cols == {"Masse_0": 4, "Masse_1": 5, "Zeit_0": 6, "Zeit_1": 7}
    assert find_context(HEADERS, 2) == {"Temperatur": 2, "V_MFC": 3}


def test_context_and_time():
    assert format_context({"Temperatur": 250.0, "V_MFC": 20}) == "250 °C · 20 Nl/min"
    assert format_context({"Temperatur": None}) == ""
    assert round(time_fraction(WHEN) * 86400) == 10 * 3600 + 8 * 60 + 56


def test_log_keeps_every_press(tmp_path):
    log = AblesungLog(tmp_path)
    log.record("T0", 9657.45, True, WHEN, "OK Zeile 18", run_id=3, workbook="J.xlsx", sheet="S", row=18)
    path = log.record("T0", 9657.40, True, WHEN, "NICHT GESCHRIEBEN: schon belegt")
    rows = AblesungLog.read(path)
    assert path.name == "Ablesungen_2026-10-15.csv"
    assert [(r["Taste"], r["Wert"], r["Zeile"]) for r in rows] == [("T0", "9657.45", "18"), ("T0", "9657.4", "")]
    assert rows[1]["Ergebnis"].startswith("NICHT GESCHRIEBEN")


# -- real Excel (Windows with Excel only); a separate hidden instance, never the user's ----------

@pytest.fixture
def excel():
    win32 = pytest.importorskip("win32com.client")
    try:
        app = win32.DispatchEx("Excel.Application")
    except Exception:
        pytest.skip("Excel nicht installiert")
    app.Visible = False
    app.DisplayAlerts = False
    wb = app.Workbooks.Add()
    ws = wb.Worksheets(1)
    ws.Name = "15.10.2026"
    for i, h in enumerate(HEADERS):
        ws.Cells(10, 2 + i).Value = h
    ws.Cells(11, 2).Value = 250
    ws.Cells(11, 3).Value = 20
    ws.ListObjects.Add(1, ws.Range("B10:H30"), None, 1)  # xlSrcRange, with headers
    ws.Range("F11:G30").NumberFormat = "hh:mm:ss"
    ws.Cells(11, 1).Select()  # column A is outside the table, like the step labels in the journal
    try:
        yield app, ws
    finally:
        wb.Close(False)
        app.Quit()


def test_writes_into_active_row_and_never_overwrites(excel):
    app, ws = excel
    link = ExcelLink(lambda: app)
    t = link.target()
    assert (t.sheet, t.row, t.context) == ("15.10.2026", 11, "250 °C · 20 Nl/min")
    assert t.filled("T0") == []

    link.write("T0", [("Masse_0", 9657.45)], WHEN)
    assert ws.Cells(11, 4).Value == 9657.45
    assert round(ws.Cells(11, 6).Value2 * 86400) == 10 * 3600 + 8 * 60 + 56
    assert ws.Cells(11, 6).Text == "10:08:56"

    with pytest.raises(ExcelError, match="schon belegt"):
        link.write("T0", [("Masse_0", 1.0)], WHEN)  # double press
    assert ws.Cells(11, 4).Value == 9657.45

    link.write("T1", [("Masse_1", 9655.86)], datetime(2026, 10, 15, 10, 14, 40))
    assert ws.Cells(11, 5).Value == 9655.86 and ws.Cells(11, 7).Text == "10:14:40"


def test_refuses_if_only_the_time_is_filled(excel):
    app, ws = excel
    ws.Cells(11, 6).Value2 = 0.5
    with pytest.raises(ExcelError, match="Zeit_0"):
        ExcelLink(lambda: app).write("T0", [("Masse_0", 1.0)], WHEN)
    assert ws.Cells(11, 4).Value is None


def test_refuses_header_row_and_unknown_sheet(excel):
    app, ws = excel
    ws.Cells(10, 4).Select()
    with pytest.raises(ExcelError, match="Kopfzeile"):
        ExcelLink(lambda: app).target()
    other = ws.Parent.Worksheets.Add()
    other.Cells(5, 1).Select()
    with pytest.raises(ExcelError, match="nicht gefunden"):
        ExcelLink(lambda: app).target()


def test_flash_restores_fill(excel):
    app, ws = excel
    link = ExcelLink(lambda: app)
    t = link.write("T0", [("Masse_0", 1.0)], WHEN)
    cells = link.flash(t, "T0")
    assert len(cells) == 2 and ws.Cells(11, 4).Interior.Color == FLASH_COLOR
    assert link.unflash(cells) == []
    assert ws.Cells(11, 4).Interior.ColorIndex == XL_NONE


def test_no_excel_is_a_message():
    def gone():
        raise OSError("not running")
    with pytest.raises(ExcelError, match="nicht geöffnet"):
        ExcelLink(gone).target()


# -- GUI ---------------------------------------------------------------------------------------

class FakeLink:
    def __init__(self):
        self.cells = {}
        self.config = AblesungConfig()

    def target(self):
        raise ExcelError("Excel ist nicht geöffnet")

    def write(self, slot, values, when):
        if slot in self.cells:
            raise ExcelError("Zeile 18: Masse_0 schon belegt – nichts geschrieben.")
        self.cells[slot] = (values, when)
        from instrument_reader.core.excel_link import Target
        return Target("J.xlsx", "S", 18, {})

    def flash(self, t, slot):
        return []

    def unflash(self, cells):
        return []


def test_buttons_write_value_and_log_refusals(qtbot):
    from instrument_reader.gui.main_window import MainWindow

    w = MainWindow()
    qtbot.addWidget(w)
    w.excel_link = FakeLink()
    w.video_widget.latest_readings = [
        {"roi_name": "Waage", "parsed_value": 9657.8, "is_valid": True, "raw_text": "9657.8", "timestamp": 1_760_000_000.0},
        {"roi_name": "Rotameter", "parsed_value": 3.0, "is_valid": True},
    ]
    w.control_panel.set_scale_roi_names(["Rotameter", "Waage"])
    assert w.control_panel.resolve_roi("") == "Waage"

    w.control_panel.t0_btn.click()
    w.control_panel.t0_btn.click()  # double press: refused, but logged
    assert w.excel_link.cells["T0"][0] == [("Masse_0", 9657.8)]
    assert w.control_panel.ablesung_history.count() == 2
    assert w.control_panel.ablesung_history.item(0).text().startswith("⚠ T0")

    w.video_widget.latest_readings[0]["is_valid"] = False
    w.control_panel.t1_btn.click()
    assert "T1" not in w.excel_link.cells

    rows = AblesungLog.read(w.ablesung_log.path_for(datetime.now()))
    assert [r["Ergebnis"][:2] for r in rows] == ["OK", "NI", "NI"]


# -- configurable target (sheet, any number of readings, header names or column letters) -------


class FakeCell:
    def __init__(self, sheet, r, c):
        self.sheet, self.Row, self.Column = sheet, r, c
        self.NumberFormat = "General"

    @property
    def Value(self):
        return self.sheet.data.get((self.Row, self.Column))

    @property
    def Value2(self):
        return self.Value

    @Value2.setter
    def Value2(self, v):
        self.sheet.data[(self.Row, self.Column)] = v


class FakeRange:
    def __init__(self, sheet, a, b):
        self.sheet, self.a, self.b = sheet, a, b

    @property
    def Value(self):
        return (tuple(self.sheet.data.get((self.a.Row, c)) for c in range(self.a.Column, self.b.Column + 1)),)


class FakeUsed:
    Row, Column = 1, 1

    def __init__(self, sheet):
        rows = [r for r, _c in sheet.data] or [1]
        cols = [c for _r, c in sheet.data] or [1]
        self.Rows = type("N", (), {"Count": max(rows)})()
        self.Columns = type("N", (), {"Count": max(cols)})()


class FakeSheet:
    ListObjects = ()

    def __init__(self, name, data):
        self.Name, self.data = name, dict(data)

    def Cells(self, r, c):
        return FakeCell(self, r, c)

    def Range(self, a, b):
        return FakeRange(self, a, b)

    @property
    def UsedRange(self):
        return FakeUsed(self)


class FakeApp:
    def __init__(self, sheet, row):
        self.ActiveSheet = sheet
        self.ActiveWorkbook = type("WB", (), {"Name": "J.xlsx"})()
        self.ActiveCell = FakeCell(sheet, row, 1)


def _sheet(headers, name="Tabelle1", header_row=1):
    return FakeSheet(name, {(header_row, 1 + i): h for i, h in enumerate(headers)})


def _cfg(t0, t0_time="", t1=(), t1_time="", sheet=""):
    return AblesungConfig(sheet=sheet, slots={
        "T0": SlotConfig([ValueColumn(r, c) for r, c in t0], t0_time),
        "T1": SlotConfig([ValueColumn(r, c) for r, c in t1], t1_time)})


def test_column_letters():
    assert [column_index(x) for x in ("A", "d", "AA", "XFD", "Masse", "A1", "", "XFE")] == \
        [1, 4, 27, 16384, None, None, None, None]


def test_several_readings_with_one_press():
    ws = _sheet(["Temperatur", "m0", "T_0", "t0", "m1", "t1"])
    cfg = _cfg([("Waage", "m0"), ("Temperatur", "T_0")], "t0", [("Waage", "M1")], "t1")
    link = ExcelLink(lambda: FakeApp(ws, 5), cfg)
    t = link.target()
    assert t.cols == {"m0": 2, "T_0": 3, "t0": 4, "M1": 5, "t1": 6}
    link.write("T0", [("m0", 84.5), ("T_0", 251.0)], WHEN)
    assert (ws.data[(5, 2)], ws.data[(5, 3)]) == (84.5, 251.0)
    assert round(ws.data[(5, 4)] * 86400) == 10 * 3600 + 8 * 60 + 56
    with pytest.raises(ExcelError, match="m0, T_0, t0 schon belegt"):
        link.write("T0", [("m0", 1.0), ("T_0", 1.0)], WHEN)


def test_plain_column_letters_need_no_headers_and_time_is_optional():
    ws = FakeSheet("Leer", {})
    link = ExcelLink(lambda: FakeApp(ws, 1), _cfg([("", "A"), ("", "B"), ("", "C")], "", [("", "D")]))
    assert link.target().cols == {"A": 1, "B": 2, "C": 3, "D": 4}
    link.write("T0", [("A", 1.0), ("B", 2.0), ("C", 3.0)], WHEN)
    assert ws.data == {(1, 1): 1.0, (1, 2): 2.0, (1, 3): 3.0}


def test_letters_and_names_mixed_header_wins():
    ws = _sheet(["Masse_0", "x", "Zeit_0", "A"])
    cfg = _cfg([("", "Masse_0")], "Zeit_0", [("", "A")], "F")
    t = ExcelLink(lambda: FakeApp(ws, 3), cfg).target()
    assert t.cols == {"Masse_0": 1, "Zeit_0": 3, "A": 4, "F": 6}
    with pytest.raises(ExcelError, match="nicht gefunden"):  # cursor on the header row itself
        ExcelLink(lambda: FakeApp(ws, 1), cfg).target()


def test_missing_names_are_reported_as_entered():
    ws = _sheet(["Masse_0"])
    with pytest.raises(ExcelError, match="Spalten Gewicht, Zeit_0 nicht gefunden"):
        ExcelLink(lambda: FakeApp(ws, 3), _cfg([("", "Gewicht")], "Zeit_0", [("", "Masse_0")], "C")).target()


def test_sheet_restriction_and_bad_config():
    ws = _sheet(["Masse_0", "Zeit_0", "Masse_1", "Zeit_1"], name="Tabelle1")
    assert ExcelLink(lambda: FakeApp(ws, 2), AblesungConfig(sheet="tabelle1")).target().row == 2
    with pytest.raises(ExcelError, match="Ziel ist Blatt „Messung“"):
        ExcelLink(lambda: FakeApp(ws, 2), AblesungConfig(sheet="Messung")).target()
    with pytest.raises(ExcelError, match="T0: eine Spalte ist leer"):
        ExcelLink(lambda: FakeApp(ws, 2), _cfg([("", " ")])).target()
    with pytest.raises(ExcelError, match="mehrfach"):
        ExcelLink(lambda: FakeApp(ws, 2), _cfg([("", "A")], "a")).target()


def test_settings_roundtrip_keeps_other_keys_and_reads_first_version(tmp_path):
    import json
    f = tmp_path / "settings.json"
    assert load_config(f) == AblesungConfig()
    f.write_text('{"backup_mirror": "Z:/x", "excel_ziel": "kaputt"}', encoding="utf-8")
    assert load_config(f) == AblesungConfig()
    cfg = _cfg([("Waage", "A"), ("Temperatur", "B")], "C", [], "", sheet="Tabelle1")
    save_config(f, cfg)
    assert load_config(f) == cfg
    assert json.loads(f.read_text(encoding="utf-8"))["backup_mirror"] == "Z:/x"
    # format of the first version of this branch: one value per key
    f.write_text(json.dumps({"excel_ziel": {"roi": "Temperatur", "sheet": "", "mass0": "B",
                                            "time0": "Zeit_0", "mass1": "Masse_1", "time1": ""}}))
    assert load_config(f) == _cfg([("Temperatur", "B")], "Zeit_0", [("Temperatur", "Masse_1")], "")


def test_panel_rows_are_editable_and_saved(qtbot):
    from instrument_reader.core import data_safety
    from instrument_reader.gui.main_window import MainWindow

    w = MainWindow()
    qtbot.addWidget(w)
    cp = w.control_panel
    cp.set_scale_roi_names(["Temperatur", "Waage"])
    combo, edit, _lbl = cp._abl_rows["T0"][0]
    assert (combo.currentText(), edit.text(), cp.excel_time_edits["T0"].text()) == ("Waage", "Masse_0", "Zeit_0")
    assert cp.t0_btn.text().startswith("T0 → Masse_0, Zeit_0")

    cp._add_ablesung_row("T0")  # "+ Wert": the next unused reading
    combo2, edit2, _ = cp._abl_rows["T0"][1]
    assert combo2.currentText() == "Temperatur"
    edit2.setText("Temp_0")
    edit2.editingFinished.emit()
    assert w.excel_link.config.slots["T0"].values == [ValueColumn("", "Masse_0"), ValueColumn("Temperatur", "Temp_0")]
    assert cp.t0_btn.text().startswith("T0 → Masse_0, Temp_0, Zeit_0")
    assert load_config(data_safety.data_dir() / "settings.json") == w.excel_link.config

    cp.update_ablesung_values({"Waage": (84.5, True), "Temperatur": (251.0, False)})
    assert [r[2].text() for r in cp._abl_rows["T0"]] == ["84.5", "251"]

    w.excel_link = FakeLink()
    w.excel_link.config = cp.ablesung_config()
    w.video_widget.latest_readings = [
        {"roi_name": "Waage", "parsed_value": 84.5, "is_valid": True},
        {"roi_name": "Temperatur", "parsed_value": 251.0, "is_valid": True},
    ]
    cp.t0_btn.click()
    assert w.excel_link.cells["T0"][0] == [("Masse_0", 84.5), ("Temp_0", 251.0)]
    w.video_widget.latest_readings[1]["is_valid"] = False
    cp.t1_btn.click()  # T1 only uses the scale: still fine
    assert w.excel_link.cells["T1"][0] == [("Masse_1", 84.5)]

    w2 = MainWindow()  # next start
    qtbot.addWidget(w2)
    w2.control_panel.set_scale_roi_names(["Waage", "Temperatur"])
    rows = w2.control_panel._abl_rows["T0"]
    assert [(c.currentText(), e.text()) for c, e, _ in rows] == [("Waage", "Masse_0"), ("Temperatur", "Temp_0")]
    w2.control_panel._remove_ablesung_row("T0", rows[0][0])
    assert w2.excel_link.config.slots["T0"].values == [ValueColumn("Temperatur", "Temp_0")]
    w2.control_panel.excel_defaults_btn.click()
    assert w2.excel_link.config == AblesungConfig()
