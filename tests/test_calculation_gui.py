import json
import sqlite3
import pytest
from PySide6.QtCore import Qt, QMimeData, QByteArray
from PySide6.QtWidgets import QDialogButtonBox

from instrument_reader.core.calculation import (
    CalculationChannel,
    CalculationEngine,
    get_standard_presets,
)
from instrument_reader.core.database import Database
from instrument_reader.gui.control_panel import (
    ControlPanel,
    CalculationChannelDialog,
    DroppableLineEdit,
    FormulaLineEdit,
)
from instrument_reader.gui.export_dialog import ExportDialog
from instrument_reader.gui.main_window import MainWindow
from instrument_reader.core.roi import ROIConfig, ROIShape


def test_calculation_channel_dialog_presets(qtbot):
    dialog = CalculationChannelDialog(
        channel=None,
        available_rois=["Waage", "Thermo_1", "Rotameter"],
        current_readings={"Waage": 850.0, "Thermo_1": 180.0},
    )
    qtbot.addWidget(dialog)

    # Preset 1: Massenstrom Waage (g/s)
    dialog.preset_combo.setCurrentIndex(1)
    assert dialog.unit_edit.text() == "g/s"
    assert "rate(" in dialog.formula_edit.text()

    # Preset 3: Abgaskonzentration Betriebszustand
    # Find index of concentration_operating
    for idx in range(dialog.preset_combo.count()):
        p = dialog.preset_combo.itemData(idx)
        if p and p.id == "concentration_operating":
            dialog.preset_combo.setCurrentIndex(idx)
            break

    assert dialog.unit_edit.text() == "g/m³"
    assert "273.15" in dialog.formula_edit.text()
    assert "m_dot" in dialog.var_slot_widgets
    assert "T" in dialog.var_slot_widgets
    assert "V_norm" in dialog.const_slot_widgets

    # Switch T from ROI to Fixed value
    slot_t = dialog.var_slot_widgets["T"]
    slot_t["rb_fix"].setChecked(True)
    slot_t["fix_spin"].setValue(200.0)

    ch = dialog.get_channel_data()
    assert ch.constants.get("T") == 200.0
    assert "T" not in ch.variables


def test_droppable_inputs(qtbot):
    drop_edit = DroppableLineEdit()
    formula_edit = FormulaLineEdit()
    qtbot.addWidget(drop_edit)
    qtbot.addWidget(formula_edit)

    # Simulate dropped mime data
    mime = QMimeData()
    mime.setText("Waage")
    mime.setData("application/x-instrument-reader-roi", QByteArray(b"Waage"))

    # Test DroppableLineEdit drop
    from PySide6.QtGui import QDropEvent
    from PySide6.QtCore import QPointF

    event = QDropEvent(
        QPointF(10, 10),
        Qt.CopyAction,
        mime,
        Qt.LeftButton,
        Qt.NoModifier,
    )
    drop_edit.dropEvent(event)
    assert drop_edit.text() == "Waage"

    # Test FormulaLineEdit drop with special characters (spaces)
    mime2 = QMimeData()
    mime2.setText("Waage Abzug")
    event2 = QDropEvent(
        QPointF(10, 10),
        Qt.CopyAction,
        mime2,
        Qt.LeftButton,
        Qt.NoModifier,
    )
    formula_edit.dropEvent(event2)
    assert formula_edit.text() == "{Waage Abzug}"


def test_control_panel_calc_table(qtbot):
    panel = ControlPanel()
    qtbot.addWidget(panel)

    ch1 = CalculationChannel(
        id="ch1",
        name="Massenstrom",
        unit="g/h",
        formula="rate(m, 10) * 3600",
    )
    panel.set_calculated_channels([ch1])
    assert panel.calc_table.rowCount() == 1
    assert panel.calc_table.item(0, 0).text() == "Massenstrom"

    # Update with calculated result
    from instrument_reader.core.calculation import CalculationResult
    res = CalculationResult(
        channel_id="ch1",
        name="Massenstrom",
        value=150.25,
        unit="g/h",
        formula="rate(m, 10) * 3600",
        is_valid=True,
        formatted_value="150.25",
    )
    panel.update_calculated_readings([res])
    assert panel.calc_table.item(0, 1).text() == "150.25"
    assert panel.calc_table.item(0, 4).text() == "✓"


def test_export_dialog_opt_out_calculated_channels(tmp_path, qtbot):
    db_path = tmp_path / "test_export.db"
    db = Database(str(db_path))

    # Insert test run and readings: 2 raw ROIs and 1 calculated channel
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("INSERT INTO runs (experiment_id, target_temperature) VALUES (1, 20.0)")
        run_id = 1
        now = "2026-10-01T12:00:00"
        conn.execute("""
            INSERT INTO readings (run_id, timestamp, phase_status, roi_name, parsed_value, unit, is_calculated)
            VALUES (?, ?, 'TEST', 'Waage', 500.0, 'g', 0)
        """, (run_id, now))
        conn.execute("""
            INSERT INTO readings (run_id, timestamp, phase_status, roi_name, parsed_value, unit, is_calculated)
            VALUES (?, ?, 'TEST', 'Thermo_1', 180.0, '°C', 0)
        """, (run_id, now))
        conn.execute("""
            INSERT INTO readings (run_id, timestamp, phase_status, roi_name, parsed_value, unit, is_calculated)
            VALUES (?, ?, 'TEST', 'Massenstrom (g/h)', 45.0, 'g/h', 1)
        """, (run_id, now))

    dlg = ExportDialog(db=db, current_run_id=1)
    qtbot.addWidget(dlg)

    # Check that channels were loaded
    assert len(dlg.channel_checkboxes) == 3
    # Raw ROIs must be checked by default
    assert dlg.channel_checkboxes["Waage"].isChecked() is True
    assert dlg.channel_checkboxes["Thermo_1"].isChecked() is True
    # Calculated channels must be UNCHECKED by default (opt-out requirement)
    assert dlg.channel_checkboxes["Massenstrom (g/h)"].isChecked() is False

    # Verify get_data() returns only checked channels
    _, _, selected = dlg.get_data()
    assert "Waage" in selected
    assert "Thermo_1" in selected
    assert "Massenstrom (g/h)" not in selected

    # Perform CSV export with filtered channels
    csv_file = tmp_path / "filtered_export.csv"
    db.export_csv(str(csv_file), run_id, selected)

    with open(csv_file, "r") as f:
        content = f.read()
    assert "Waage" in content
    assert "Thermo_1" in content
    assert "Massenstrom (g/h)" not in content


def test_main_window_calculated_channels_live_and_presets(tmp_path, qtbot, monkeypatch):
    # Set up temporary database for MainWindow
    db_file = tmp_path / "main_test.db"

    window = MainWindow()
    qtbot.addWidget(window)
    window.db_writer.db.db_path = str(db_file)
    window.db_writer.db._init_db()

    # Create run
    exp_id = window.db_writer.create_experiment("Exp", "VOC", "Notes")
    run_id = window.db_writer.create_run(exp_id, 25.0, "")

    # Add calculated channel
    ch = CalculationChannel(
        id="c_test",
        name="Test Massenstrom",
        unit="g/h",
        formula="rate(m, 10) * 3600",
        variables={"m": "Waage"},
        decimal_places=2,
    )
    window.calc_engine.add_channel(ch)
    window.control_panel.set_calculated_channels([ch])

    # Send scale readings across 15 seconds
    for t_step in range(15):
        readings = [{
            "roi_id": "roi-waage",
            "roi_name": "Waage",
            "raw_text": f"{1000.0 - 0.1 * t_step}",
            "parsed_value": 1000.0 - 0.1 * t_step,
            "unit": "g",
            "confidence": 0.99,
            "is_valid": True,
            "reason": "",
            "used_fallback": False,
            "is_child": False,
            "timestamp": 100.0 + float(t_step),
        }]
        window.on_readings_ready(readings)

    # Verify live calculated table updated
    assert window.control_panel.calc_table.rowCount() == 1
    val_str = window.control_panel.calc_table.item(0, 1).text()
    assert float(val_str) == pytest.approx(360.0, rel=1e-1)
    status_str = window.control_panel.calc_table.item(0, 4).text()
    assert status_str == "✓"

    # Verify readings table in database has is_calculated = 1 rows
    with sqlite3.connect(str(db_file)) as conn:
        cur = conn.cursor()
        cur.execute("SELECT roi_name, is_calculated FROM readings WHERE run_id = ?", (run_id,))
        rows = cur.fetchall()
        calc_rows = [r for r in rows if r[1] == 1]
        raw_rows = [r for r in rows if r[1] == 0]
        assert len(calc_rows) > 0
        assert len(raw_rows) > 0
        assert calc_rows[-1][0] == "Test Massenstrom"

    # Test Preset Saving and Loading with Calculated Channels
    # Mock QInputDialog for saving preset
    from PySide6.QtWidgets import QInputDialog, QMessageBox
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("TestPreset", True))
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)

    window.save_roi_preset()

    # Clear calculation engine channels
    window.calc_engine.clear_channels()
    window.control_panel.set_calculated_channels([])
    assert len(window.calc_engine.channels) == 0

    # Mock QInputDialog for loading preset
    monkeypatch.setattr(QInputDialog, "getItem", lambda *args, **kwargs: ("TestPreset", True))
    window.load_roi_preset()

    # Verify channel restored
    assert len(window.calc_engine.channels) == 1
    assert "c_test" in window.calc_engine.channels
    restored_ch = window.calc_engine.channels["c_test"]
    assert restored_ch.name == "Test Massenstrom"
    assert restored_ch.formula == "rate(m, 10) * 3600"


def test_dialog_edit_preserves_custom_values(qtbot):
    """Verify that editing a channel does not overwrite user's custom properties with preset defaults."""
    ch = CalculationChannel(
        id="custom_1",
        name="Custom Massenstrom Reaktor 1",
        unit="kg/h",
        formula="rate(m, window_s)",
        variables={"m": "Waage"},
        constants={"window_s": 25.0},
        window_seconds=25.0,
        decimal_places=4,
    )
    dialog = CalculationChannelDialog(channel=ch)
    qtbot.addWidget(dialog)

    assert dialog.name_edit.text() == "Custom Massenstrom Reaktor 1"
    assert dialog.unit_edit.text() == "kg/h"
    assert dialog.window_spin.value() == 25.0
    assert dialog.decimal_spin.value() == 4


def test_dialog_switch_to_custom_clears_preset_slots(qtbot):
    """Verify switching to Custom clears preset slots."""
    dialog = CalculationChannelDialog(channel=None)
    qtbot.addWidget(dialog)

    # Starts at preset 1 (mass_flow_gs)
    assert "m" in dialog.var_slot_widgets
    assert "window_s" in dialog.const_slot_widgets

    # Switch to Custom (index 0)
    dialog.preset_combo.setCurrentIndex(0)
    assert len(dialog.var_slot_widgets) == 0
    assert len(dialog.const_slot_widgets) == 0


def test_dialog_duplicate_name_and_syntax_validation(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    engine = CalculationEngine()
    existing_ch = CalculationChannel(id="ex1", name="Kanal 1", formula="10")
    engine.add_channel(existing_ch)

    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text: warnings.append((title, text)))

    dialog = CalculationChannelDialog(
        channel=None,
        available_rois=["Waage"],
        calc_engine=engine,
    )
    qtbot.addWidget(dialog)

    # 1. Collision with ROI name
    dialog.name_edit.setText("Waage")
    dialog.formula_edit.setText("10 * 2")
    dialog.validate_and_accept()
    assert any("ROI mit dem Namen" in w[1] for w in warnings)
    warnings.clear()

    # 2. Collision with existing calc channel name
    dialog.name_edit.setText("Kanal 1")
    dialog.formula_edit.setText("10 * 2")
    dialog.validate_and_accept()
    assert any("Berechnungs-Kanal mit dem Namen" in w[1] for w in warnings)
    warnings.clear()

    # 3. Invalid syntax
    dialog.name_edit.setText("Neuer Kanal")
    dialog.formula_edit.setText("10 + * 2")
    dialog.validate_and_accept()
    assert any("Syntaxfehler" in w[0] for w in warnings)


def test_main_window_roi_rename_and_delete_synchronization(tmp_path, qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox, QDialog

    window = MainWindow()
    qtbot.addWidget(window)
    window.db_writer.db.db_path = str(tmp_path / "sync_test.db")
    window.db_writer.db._init_db()

    # Create ROI
    roi = ROIConfig(id="r1", name="Waage_Alt", shape=ROIShape.RECTANGLE, coordinates=[0, 0, 50, 50])
    window.video_widget.rois.append(roi)
    window.control_panel.add_roi(roi)

    # Add calculated channel referencing Waage_Alt
    ch = CalculationChannel(
        id="c1",
        name="Rate",
        formula="{Waage_Alt} * 2",
        variables={"m": "Waage_Alt"},
    )
    window.calc_engine.add_channel(ch)
    window.calc_engine.add_reading("Waage_Alt", 100.0, timestamp=10.0)

    # Rename ROI
    updated_roi = ROIConfig(id="r1", name="Waage_Neu", shape=ROIShape.RECTANGLE, coordinates=[0, 0, 50, 50])
    class MockConfigDlg:
        def __init__(self, *args, **kwargs): pass
        def exec(self): return True
        def get_data(self): return updated_roi

    from instrument_reader.gui import main_window as mw_mod
    monkeypatch.setattr(mw_mod, "ROIConfigDialog", MockConfigDlg)

    item = window.control_panel.roi_list.item(0)
    window.on_roi_double_clicked(item)

    # Check that calc_engine was synchronized
    assert "Waage_Neu" in window.calc_engine.history
    assert "Waage_Alt" not in window.calc_engine.history
    assert ch.variables["m"] == "Waage_Neu"
    assert "{Waage_Neu}" in ch.formula

    # Delete ROI
    window.control_panel.roi_list.setCurrentRow(0)
    window.delete_roi()
    assert "Waage_Neu" not in window.calc_engine.history


def test_main_window_invalid_ocr_not_in_calculations(tmp_path, qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.db_writer.db.db_path = str(tmp_path / "invalid_test.db")
    window.db_writer.db._init_db()

    ch = CalculationChannel(id="c1", name="Doubled", formula="v * 2", variables={"v": "Sensor"})
    window.calc_engine.add_channel(ch)

    # Send invalid reading (e.g. out of bounds)
    readings = [{
        "roi_id": "r_s",
        "roi_name": "Sensor",
        "raw_text": "-999",
        "parsed_value": -999.0,
        "is_valid": False,
        "reason": "Value out of range",
    }]
    window.on_readings_ready(readings)

    # Calculation should not evaluate with the invalid reading
    res = window.calc_engine.latest_results.get("c1")
    assert res is not None
    assert not res.is_valid


def test_control_panel_stage_badge_and_reset(qtbot):
    cp = ControlPanel()
    qtbot.addWidget(cp)

    # Initial state
    assert cp.stage_badge.text() == "Stufe 1 (Stationär)"
    assert "#2e7d32" in cp.stage_badge.styleSheet()

    # Transition state
    cp.set_stage_status(1, "Stabilisierung...", is_transition=True, target_stage=2)
    assert cp.stage_badge.text() == "Stufenwechsel 1 → 2 (Stabilisierung...)"
    assert "#ef6c00" in cp.stage_badge.styleSheet()

    # Steady stage 2
    cp.set_stage_status(2, "Stationär", is_transition=False)
    assert cp.stage_badge.text() == "Stufe 2 (Stationär)"
    assert "#2e7d32" in cp.stage_badge.styleSheet()

    # Reset button click
    reset_emitted = False

    def on_reset():
        nonlocal reset_emitted
        reset_emitted = True

    cp.stage_reset_requested.connect(on_reset)
    cp.reset_stage_btn.click()
    assert reset_emitted
    assert cp.stage_badge.text() == "Stufe 1 (Stationär)"


def test_main_window_step_detection_and_db_phase(tmp_path, qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    db_file = str(tmp_path / "step_phase_test.db")
    window.db_writer.db.db_path = db_file
    window.db_writer.db._init_db()

    exp_id = window.db_writer.create_experiment("Exp", "VOC", "Desc")
    run_id = window.db_writer.create_run(exp_id, 20.0, "Notes")

    # Initial phase
    assert window.db_writer.current_phase == "STAGE_1"
    assert window.control_panel.stage_badge.text() == "Stufe 1 (Stationär)"

    # Feed steady readings for 10s
    for i in range(11):
        t = float(i)
        window.on_readings_ready([{
            "roi_id": "r_w",
            "roi_name": "Waage",
            "raw_text": f"{500.0 - 0.01 * t:.2f}",
            "parsed_value": 500.0 - 0.01 * t,
            "unit": "g",
            "is_valid": True,
            "timestamp": t,
        }], timestamp=t)

    assert window.db_writer.current_phase == "STAGE_1"
    assert window.control_panel.stage_badge.text() == "Stufe 1 (Stationär)"

    # Upward jump at t=11
    window.on_readings_ready([{
        "roi_id": "r_w",
        "roi_name": "Waage",
        "raw_text": "500.39",
        "parsed_value": 500.39,
        "unit": "g",
        "is_valid": True,
        "timestamp": 11.0,
    }], timestamp=11.0)

    assert window.db_writer.current_phase == "STAGE_TRANSITION"
    assert "Stufenwechsel" in window.control_panel.stage_badge.text()

    # Settle at t=12 and t=13
    window.on_readings_ready([{
        "roi_id": "r_w",
        "roi_name": "Waage",
        "raw_text": "500.38",
        "parsed_value": 500.38,
        "unit": "g",
        "is_valid": True,
        "timestamp": 12.0,
    }], timestamp=12.0)
    window.on_readings_ready([{
        "roi_id": "r_w",
        "roi_name": "Waage",
        "raw_text": "500.37",
        "parsed_value": 500.37,
        "unit": "g",
        "is_valid": True,
        "timestamp": 13.0,
    }], timestamp=13.0)

    # Now stage 2 steady
    assert window.db_writer.current_phase == "STAGE_2"
    assert window.control_panel.stage_badge.text() == "Stufe 2 (Stationär)"

    # Verify database contents contain phase statuses and virtual channels
    import sqlite3
    with sqlite3.connect(db_file) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT phase_status FROM readings WHERE run_id = ?", (run_id,))
        phases = {row[0] for row in cursor.fetchall()}
        assert "STAGE_1" in phases
        assert "STAGE_TRANSITION" in phases
        assert "STAGE_2" in phases

        cursor.execute("SELECT DISTINCT roi_name FROM readings WHERE run_id = ?", (run_id,))
        logged_rois = {row[0] for row in cursor.fetchall()}
        assert "Waage" in logged_rois

    # Click reset stage button
    window.control_panel.reset_stage_btn.click()
    assert window.db_writer.current_phase == "STAGE_1"
    assert window.control_panel.stage_badge.text() == "Stufe 1 (Stationär)"
    assert window.calc_engine.step_detector.stage == 1


def test_main_window_new_experiment_resets_detector(tmp_path, qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    db_file = str(tmp_path / "new_exp_test.db")
    window.db_writer.db.db_path = db_file
    window.db_writer.db._init_db()

    # Move detector to stage 2
    window.calc_engine.step_detector.stage = 2
    window.calc_engine.step_detector.total_offset = 0.5
    window.control_panel.set_stage_status(2, "Stationär")

    # Start new experiment via create_experiment
    from unittest.mock import patch
    with patch("instrument_reader.gui.main_window.ExperimentDialog") as mock_dlg_cls:
        mock_dlg = mock_dlg_cls.return_value
        mock_dlg.exec.return_value = True
        mock_dlg.get_data.return_value = {
            "exp_name": "Exp2",
            "voc_type": "Toluene",
            "exp_desc": "Clean run",
            "target_temp": 25.0,
            "run_notes": "",
        }
        window.create_experiment()

    # Must be reset to stage 1, tare offset 0, and phase STAGE_1
    assert window.calc_engine.step_detector.stage == 1
    assert window.calc_engine.step_detector.total_offset == 0.0
    assert window.control_panel.stage_badge.text() == "Stufe 1 (Stationär)"
    assert window.db_writer.current_phase == "STAGE_1"


def test_main_window_virtual_channels_in_evaluation_and_dialog(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)

    # Feed a scale reading
    window.on_readings_ready([{
        "roi_id": "r_w",
        "roi_name": "Waage",
        "raw_text": "500.00",
        "parsed_value": 500.00,
        "unit": "g",
        "is_valid": True,
        "timestamp": 10.0,
    }], timestamp=10.0)

    vals = window._get_current_evaluation_values()
    assert "Waage_korrigiert" in vals
    assert "VOC_verdampft" in vals
    assert "Stufe" in vals
    assert vals["Waage_korrigiert"] == 500.00
    assert vals["Stufe"] == 1.0


