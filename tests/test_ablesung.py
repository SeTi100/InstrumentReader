from datetime import datetime

import pytest

from instrument_reader.core.ablesung import AblesungLog
from instrument_reader.core.excel_link import (
    ExcelError, ExcelLink, FLASH_COLOR, XL_NONE, find_columns, format_context, time_fraction,
)

WHEN = datetime(2026, 10, 15, 10, 8, 56)
HEADERS = ["Temperatur", "V_MFC [Nl/min]", "Masse_0", "Masse_1", "Zeit_0", "Zeit_1", "mg_C FID [RF1]"]


def test_find_columns_by_header_with_offset():
    cols = find_columns(HEADERS, first_col=2)  # table starts in column B like the journal
    assert cols == {"Temperatur": 2, "V_MFC": 3, "Masse_0": 4, "Masse_1": 5, "Zeit_0": 6, "Zeit_1": 7}


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

    link.write("T0", 9657.45, WHEN)
    assert ws.Cells(11, 4).Value == 9657.45
    assert round(ws.Cells(11, 6).Value2 * 86400) == 10 * 3600 + 8 * 60 + 56
    assert ws.Cells(11, 6).Text == "10:08:56"

    with pytest.raises(ExcelError, match="schon belegt"):
        link.write("T0", 1.0, WHEN)  # double press
    assert ws.Cells(11, 4).Value == 9657.45

    link.write("T1", 9655.86, datetime(2026, 10, 15, 10, 14, 40))
    assert ws.Cells(11, 5).Value == 9655.86 and ws.Cells(11, 7).Text == "10:14:40"


def test_refuses_if_only_the_time_is_filled(excel):
    app, ws = excel
    ws.Cells(11, 6).Value2 = 0.5
    with pytest.raises(ExcelError, match="Zeit_0"):
        ExcelLink(lambda: app).write("T0", 1.0, WHEN)
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
    t = link.write("T0", 1.0, WHEN)
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

    def target(self):
        raise ExcelError("Excel ist nicht geöffnet")

    def write(self, slot, value, when):
        if slot in self.cells:
            raise ExcelError("Zeile 18: Masse_0 schon belegt – nichts geschrieben.")
        self.cells[slot] = (value, when)
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
    assert w.control_panel.scale_roi_combo.currentText() == "Waage"

    w.control_panel.t0_btn.click()
    w.control_panel.t0_btn.click()  # double press: refused, but logged
    assert w.excel_link.cells["T0"][0] == 9657.8
    assert w.control_panel.ablesung_history.count() == 2
    assert w.control_panel.ablesung_history.item(0).text().startswith("⚠ T0")

    w.video_widget.latest_readings[0]["is_valid"] = False
    w.control_panel.t1_btn.click()
    assert "T1" not in w.excel_link.cells

    rows = AblesungLog.read(w.ablesung_log.path_for(datetime.now()))
    assert [r["Ergebnis"][:2] for r in rows] == ["OK", "NI", "NI"]
