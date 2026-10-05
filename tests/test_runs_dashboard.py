import os
import csv
import sqlite3
import pytest
from datetime import datetime
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from instrument_reader.core.database import Database
from instrument_reader.gui.db_writer import DatabaseWriter
from instrument_reader.gui.control_panel import ControlPanel, LEDIndicator
from instrument_reader.gui.experiment_dialog import ExperimentDialog, NewRunDialog
from instrument_reader.gui.runs_dashboard import RunsDashboardDialog, MultiRunExportDialog
from instrument_reader.gui.main_window import MainWindow


def test_led_indicator(qtbot):
    led = LEDIndicator()
    qtbot.addWidget(led)

    # Default / Steady state
    assert led._color == "#2e7d32"
    assert led.text() == "Stufe 1 (Stationär)"
    assert led.toolTip() == "Stufe 1 (Stationär)"
    assert "#2e7d32" in led.styleSheet()

    # Transition state
    led.set_status(1, "Stabilisierung...", is_transition=True, target_stage=2)
    assert led._color == "#ef6c00"
    assert led.text() == "Stufenwechsel 1 → 2 (Stabilisierung...)"
    assert led.toolTip() == "Stufenwechsel 1 → 2 (Stabilisierung...)"
    assert "#ef6c00" in led.styleSheet()

    # Steady stage 2
    led.set_status(2, "Stationär", is_transition=False)
    assert led._color == "#2e7d32"
    assert led.text() == "Stufe 2 (Stationär)"
    assert led.toolTip() == "Stufe 2 (Stationär)"

    # Inactive state
    led.set_status(0, "Gestoppt", inactive=True)
    assert led._color == "#9e9e9e"
    assert led.text() == "Inaktiv (Gestoppt)"
    assert led.toolTip() == "Inaktiv (Gestoppt)"
    assert "#9e9e9e" in led.styleSheet()


def test_control_panel_buttons_and_led(qtbot):
    cp = ControlPanel()
    qtbot.addWidget(cp)

    assert isinstance(cp.stage_led, LEDIndicator)
    assert cp.stage_badge is cp.stage_led
    assert cp.new_exp_btn.text() == "New Experiment"
    assert cp.new_run_btn.text() == "New Run"
    assert cp.dashboard_btn.text() == "Runs Dashboard"
    assert cp.reset_stage_btn.text() == "Reset Stage"

    # Reset stage button
    reset_called = False

    def on_reset():
        nonlocal reset_called
        reset_called = True

    cp.stage_reset_requested.connect(on_reset)
    cp.reset_stage_btn.click()
    assert reset_called
    assert cp.stage_led.text() == "Stufe 1 (Stationär)"


def test_experiment_and_run_decoupling(tmp_path, qtbot, monkeypatch):
    db_file = str(tmp_path / "test_decoupling.db")
    win = MainWindow()
    qtbot.addWidget(win)
    win.db_writer.db.db_path = db_file
    win.db_writer.db._init_db()

    # 1. Create first experiment + Run 1
    exp_data = {
        "exp_name": "Ethanol 50C",
        "voc_type": "Ethanol",
        "exp_desc": "Batch A",
        "target_temp": 50.0,
        "run_notes": "Baseline run"
    }
    monkeypatch.setattr(ExperimentDialog, "exec", lambda self: True)
    monkeypatch.setattr(ExperimentDialog, "get_data", lambda self: exp_data)

    win.control_panel.new_exp_btn.click()

    assert win.current_experiment_id == 1
    assert win.current_experiment_name == "Ethanol 50C"
    assert win.db_writer.current_run_id == 1
    assert "Experiment: Ethanol 50C (ID: 1) | Run: 1" in win.control_panel.exp_label.text()

    # 2. Create Run 2 under the same experiment using New Run button
    run2_data = {
        "target_temp": 50.0,
        "run_notes": "Step 2: 100ppm injection"
    }
    monkeypatch.setattr(NewRunDialog, "exec", lambda self: True)
    monkeypatch.setattr(NewRunDialog, "get_data", lambda self: run2_data)

    win.control_panel.new_run_btn.click()

    assert win.current_experiment_id == 1  # Still the same experiment!
    assert win.db_writer.current_run_id == 2  # New run ID
    assert "Experiment: Ethanol 50C (ID: 1) | Run: 2" in win.control_panel.exp_label.text()


def test_new_run_without_active_experiment_warns(tmp_path, qtbot, monkeypatch):
    win = MainWindow()
    qtbot.addWidget(win)
    win.current_experiment_id = None

    info_shown = False

    def mock_info(parent, title, text):
        nonlocal info_shown
        info_shown = True

    monkeypatch.setattr(QMessageBox, "information", mock_info)
    win.create_new_run()
    assert info_shown
    assert win.db_writer.current_run_id is None


def test_database_tree_and_aggregates(tmp_path):
    db_file = str(tmp_path / "test_tree.db")
    db = Database(db_file)
    writer = DatabaseWriter(db_file)

    exp1 = writer.create_experiment("Exp Alpha", "Ethanol", "Desc Alpha")
    run1 = writer.create_run(exp1, 25.0, "Run 1 notes")
    writer.set_phase("STAGE_1")
    writer.insert_readings([
        {"roi_name": "Waage", "parsed_value": 100.1, "unit": "g", "is_valid": True},
        {"roi_name": "Thermo_1", "parsed_value": 25.4, "unit": "°C", "is_valid": True},
    ])

    run2 = writer.create_run(exp1, 50.0, "Run 2 notes")
    writer.set_phase("STAGE_2")
    writer.insert_readings([
        {"roi_name": "Waage", "parsed_value": 99.8, "unit": "g", "is_valid": True},
    ])

    tree = db.get_experiments_tree()
    assert len(tree) == 1
    exp_entry = tree[0]
    assert exp_entry["id"] == exp1
    assert exp_entry["name"] == "Exp Alpha"
    assert exp_entry["voc_type"] == "Ethanol"
    assert len(exp_entry["runs"]) == 2

    # Verify run aggregates
    r1 = next(r for r in exp_entry["runs"] if r["id"] == run1)
    r2 = next(r for r in exp_entry["runs"] if r["id"] == run2)
    assert r1["reading_count"] == 2
    assert r1["target_temperature"] == 25.0
    assert r2["reading_count"] == 1
    assert r2["target_temperature"] == 50.0


def test_database_delete_run_and_experiment(tmp_path):
    db_file = str(tmp_path / "test_delete.db")
    db = Database(db_file)
    writer = DatabaseWriter(db_file)

    exp1 = writer.create_experiment("Exp 1", "Acetone", "")
    run1 = writer.create_run(exp1, 20.0, "")
    writer.insert_readings([{"roi_name": "Ch1", "parsed_value": 1.0, "unit": ""}])
    run2 = writer.create_run(exp1, 30.0, "")
    writer.insert_readings([{"roi_name": "Ch1", "parsed_value": 2.0, "unit": ""}])

    # Delete single run
    db.delete_run(run1)
    tree = db.get_experiments_tree()
    assert len(tree) == 1
    assert len(tree[0]["runs"]) == 1
    assert tree[0]["runs"][0]["id"] == run2

    # Delete entire experiment (cascading)
    db.delete_experiment(exp1)
    tree = db.get_experiments_tree()
    assert len(tree) == 0

    with sqlite3.connect(db_file) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM readings")
        assert cursor.fetchone()[0] == 0
        cursor.execute("SELECT COUNT(*) FROM runs")
        assert cursor.fetchone()[0] == 0
        cursor.execute("SELECT COUNT(*) FROM experiments")
        assert cursor.fetchone()[0] == 0


def test_multi_run_csv_export_raw(tmp_path):
    db_file = str(tmp_path / "test_export_raw.db")
    db = Database(db_file)
    writer = DatabaseWriter(db_file)

    exp = writer.create_experiment("Exp Multi", "Toluene", "Notes")
    run1 = writer.create_run(exp, 25.0, "Run 1")
    writer.set_phase("STAGE_1")
    writer.insert_readings([
        {"roi_name": "Waage", "parsed_value": 100.0, "unit": "g", "is_valid": True},
        {"roi_name": "Temp", "parsed_value": 25.1, "unit": "°C", "is_valid": True},
    ])

    run2 = writer.create_run(exp, 30.0, "Run 2")
    writer.set_phase("STAGE_2")
    writer.insert_readings([
        {"roi_name": "Waage", "parsed_value": 98.5, "unit": "g", "is_valid": True},
    ])

    out_csv = str(tmp_path / "raw_export.csv")
    db.export_multi_run_csv(out_csv, [run1, run2], format="raw")

    assert os.path.exists(out_csv)
    with open(out_csv, "r", encoding="utf-8") as f:
        reader = list(csv.reader(f))

    # Header check
    expected_header = [
        "experiment_id", "experiment_name", "voc_type", "run_id",
        "target_temperature", "timestamp", "phase_status", "roi_name",
        "parsed_value", "unit", "is_calculated"
    ]
    assert reader[0] == expected_header
    assert len(reader) == 4  # Header + 3 readings

    # Test with channel filtering
    out_filtered = str(tmp_path / "raw_filtered.csv")
    db.export_multi_run_csv(out_filtered, [run1, run2], channels=["Waage"], format="raw")
    with open(out_filtered, "r", encoding="utf-8") as f:
        reader_filt = list(csv.reader(f))
    assert len(reader_filt) == 3  # Header + 2 Waage readings


def test_multi_run_csv_export_pivot(tmp_path):
    db_file = str(tmp_path / "test_export_pivot.db")
    db = Database(db_file)
    writer = DatabaseWriter(db_file)

    exp = writer.create_experiment("Exp Pivot", "Isopropanol", "")
    run1 = writer.create_run(exp, 25.0, "")
    writer.set_phase("STAGE_1")

    # Insert batch 1: two channels with same timestamp
    writer.insert_readings([
        {"roi_name": "Sensor_A", "parsed_value": 10.5, "unit": "ppm", "is_valid": True},
        {"roi_name": "Sensor_B", "parsed_value": 20.2, "unit": "ppm", "is_valid": True},
    ])

    out_pivot = str(tmp_path / "pivot_export.csv")
    db.export_multi_run_csv(out_pivot, [run1], channels=["Sensor_A", "Sensor_B"], format="pivot")

    assert os.path.exists(out_pivot)
    with open(out_pivot, "r", encoding="utf-8") as f:
        reader = list(csv.reader(f))

    assert reader[0] == ["timestamp", "run_id", "experiment_name", "phase_status", "Sensor_A", "Sensor_B"]
    assert len(reader) == 2  # Header + 1 pivoted row
    data_row = reader[1]
    assert data_row[1] == str(run1)
    assert data_row[2] == "Exp Pivot"
    assert data_row[3] == "STAGE_1"
    assert float(data_row[4]) == 10.5
    assert float(data_row[5]) == 20.2


def test_runs_dashboard_gui_lifecycle(tmp_path, qtbot, monkeypatch):
    db_file = str(tmp_path / "dashboard_gui.db")
    db = Database(db_file)
    writer = DatabaseWriter(db_file)

    exp1 = writer.create_experiment("Exp 1", "Ethanol", "Description 1")
    run1 = writer.create_run(exp1, 20.0, "Notes 1")
    writer.set_phase("STAGE_1")
    writer.insert_readings([
        {"roi_name": "Sensor1", "parsed_value": 12.3, "unit": "g", "is_valid": True},
        {"roi_name": "Sensor2", "parsed_value": 45.6, "unit": "°C", "is_valid": True},
    ])

    exp2 = writer.create_experiment("Exp 2", "Methanol", "Description 2")
    run2 = writer.create_run(exp2, 30.0, "Notes 2")
    writer.insert_readings([
        {"roi_name": "Sensor1", "parsed_value": 99.0, "unit": "g", "is_valid": True},
    ])

    dlg = RunsDashboardDialog(db, current_run_id=run1)
    qtbot.addWidget(dlg)

    # Check tree structure
    assert dlg.tree.topLevelItemCount() == 2
    item_exp1 = dlg.tree.topLevelItem(1)  # Descending order by id: Exp 2 is index 0, Exp 1 is index 1
    item_exp2 = dlg.tree.topLevelItem(0)
    assert "Exp 2" in item_exp2.text(0)
    assert "Exp 1" in item_exp1.text(0)

    # Find run 1 item
    run1_item = item_exp1.child(0)
    assert "[ACTIVE]" in run1_item.text(0)

    # Test selection and preview table update
    dlg.tree.setCurrentItem(run1_item)
    assert dlg.detail_run_lbl.text() == str(run1)
    assert dlg.preview_table.rowCount() == 2

    # Test select all / deselect all
    dlg.select_all_btn.click()
    assert dlg.get_selected_run_ids() == [run1, run2]

    dlg.deselect_all_btn.click()
    # With none checked, get_selected_run_ids falls back to currently selected item
    assert dlg.get_selected_run_ids() == [run1]

    # Test Set as Active Run
    run2_item = item_exp2.child(0)
    dlg.tree.setCurrentItem(run2_item)

    active_emitted = []

    def on_active_changed(r_id, e_id, e_name):
        active_emitted.append((r_id, e_id, e_name))

    dlg.active_run_changed.connect(on_active_changed)

    # Accept info dialog
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)
    dlg.set_active_btn.click()

    assert len(active_emitted) == 1
    assert active_emitted[0][0] == run2
    assert dlg.current_run_id == run2

    # Test Delete Selected (re-fetch items after tree refresh)
    item_exp2_refreshed = dlg.tree.topLevelItem(0)
    run2_item_refreshed = item_exp2_refreshed.child(0)
    run2_item_refreshed.setCheckState(0, Qt.Checked)

    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    dlg.delete_btn.click()

    # Verify run 2 is deleted
    tree = db.get_experiments_tree()
    exp2_tree = next(e for e in tree if e["id"] == exp2)
    assert len(exp2_tree["runs"]) == 0

    # Test MultiRunExportDialog directly
    export_csv_file = str(tmp_path / "gui_export_test.csv")
    exp_dlg = MultiRunExportDialog(db, [run1])
    qtbot.addWidget(exp_dlg)
    exp_dlg.path_edit.setText(export_csv_file)
    exp_dlg.export_btn.click()
    assert os.path.exists(export_csv_file)


def test_main_window_dashboard_action_and_set_active_run(tmp_path, qtbot):
    win = MainWindow()
    qtbot.addWidget(win)
    db_file = str(tmp_path / "mw_dashboard_test.db")
    win.db_writer.db.db_path = db_file
    win.db_writer.db._init_db()

    # Call set_active_run on MainWindow
    win.set_active_run(run_id=42, exp_id=7, exp_name="Bench Run")
    assert win.db_writer.current_run_id == 42
    assert win.current_experiment_id == 7
    assert win.current_experiment_name == "Bench Run"
    assert "Experiment: Bench Run (ID: 7) | Run: 42" in win.control_panel.exp_label.text()
    assert win.db_writer.current_phase == "STAGE_1"
    assert win.control_panel.stage_led.text() == "Stufe 1 (Stationär)"


def test_export_multi_run_csv_edge_cases(tmp_path):
    db_file = str(tmp_path / "edge_cases.db")
    db = Database(db_file)

    # 1. Empty run_ids
    empty_raw = str(tmp_path / "empty_raw.csv")
    db.export_multi_run_csv(empty_raw, [], format="raw")
    assert os.path.exists(empty_raw)
    with open(empty_raw, "r", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert len(rows) == 1
    assert "experiment_id" in rows[0]

    empty_pivot = str(tmp_path / "empty_pivot.csv")
    db.export_multi_run_csv(empty_pivot, [], format="pivot")
    assert os.path.exists(empty_pivot)
    with open(empty_pivot, "r", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert len(rows) == 1
    assert rows[0] == ["timestamp", "run_id", "experiment_name", "phase_status"]

    # 2. Empty channels filter on existing run
    writer = DatabaseWriter(db_file)
    exp = writer.create_experiment("Exp 1", "", "")
    run = writer.create_run(exp, 20.0, "")
    writer.insert_readings([{"roi_name": "A", "parsed_value": 1.0, "unit": ""}])

    chan_empty_raw = str(tmp_path / "chan_empty_raw.csv")
    db.export_multi_run_csv(chan_empty_raw, [run], channels=[], format="raw")
    with open(chan_empty_raw, "r", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert len(rows) == 1  # Header only, no data matching channel 0

    # 3. get_multi_run_channels with empty list
    assert db.get_multi_run_channels([]) == []

    # 4. delete non-existent run / experiment
    db.delete_run(9999)
    db.delete_experiment(9999)


def test_runs_dashboard_delete_entire_experiment(tmp_path, qtbot, monkeypatch):
    db_file = str(tmp_path / "delete_exp_test.db")
    db = Database(db_file)
    writer = DatabaseWriter(db_file)

    exp1 = writer.create_experiment("Exp To Delete", "VOC", "")
    run1 = writer.create_run(exp1, 20.0, "")
    run2 = writer.create_run(exp1, 30.0, "")

    dlg = RunsDashboardDialog(db)
    qtbot.addWidget(dlg)

    assert dlg.tree.topLevelItemCount() == 1
    exp_item = dlg.tree.topLevelItem(0)

    # Check the experiment itself
    exp_item.setCheckState(0, Qt.Checked)
    # Check that children were automatically checked too
    assert exp_item.child(0).checkState(0) == Qt.Checked
    assert exp_item.child(1).checkState(0) == Qt.Checked

    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    dlg.delete_btn.click()

    assert dlg.tree.topLevelItemCount() == 0
    assert len(db.get_experiments_tree()) == 0


def test_runs_dashboard_delete_active_run_clears_main_window(tmp_path, qtbot, monkeypatch):
    win = MainWindow()
    qtbot.addWidget(win)
    db_file = str(tmp_path / "mw_del_active.db")
    win.db_writer.db.db_path = db_file
    win.db_writer.db._init_db()

    exp_id = win.db_writer.create_experiment("Exp Active", "Ethanol", "")
    run_id = win.db_writer.create_run(exp_id, 25.0, "Run to be deleted")
    win.set_active_run(run_id=run_id, exp_id=exp_id, exp_name="Exp Active")

    assert win.db_writer.current_run_id == run_id
    assert win.current_experiment_id == exp_id

    # Open dashboard dialog and delete the active run
    dlg = RunsDashboardDialog(win.db_writer.db, current_run_id=run_id, parent=win)
    qtbot.addWidget(dlg)
    dlg.active_run_changed.connect(win.set_active_run)
    dlg.active_run_cleared.connect(win.clear_active_run)

    # Find run item and check it
    exp_item = dlg.tree.topLevelItem(0)
    run_item = exp_item.child(0)
    run_item.setCheckState(0, Qt.Checked)

    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    dlg.delete_btn.click()

    # Active run must now be cleared on MainWindow
    assert win.db_writer.current_run_id is None
    assert win.current_experiment_id is None
    assert win.current_experiment_name == ""
    assert win.control_panel.exp_label.text() == "Experiment: None | Run: None"
    assert win.control_panel.stage_led._color == "#9e9e9e"
    assert "Inaktiv" in win.control_panel.stage_led.text()


def test_runs_dashboard_delete_experiment_with_active_run_clears_active(tmp_path, qtbot, monkeypatch):
    db_file = str(tmp_path / "del_exp_active.db")
    db = Database(db_file)
    writer = DatabaseWriter(db_file)

    exp_id = writer.create_experiment("Batch Exp", "VOC", "")
    run1 = writer.create_run(exp_id, 20.0, "")
    run2 = writer.create_run(exp_id, 30.0, "")

    dlg = RunsDashboardDialog(db, current_run_id=run2)
    qtbot.addWidget(dlg)

    cleared_emitted = False

    def on_cleared():
        nonlocal cleared_emitted
        cleared_emitted = True

    dlg.active_run_cleared.connect(on_cleared)

    # Delete the entire experiment
    exp_item = dlg.tree.topLevelItem(0)
    exp_item.setCheckState(0, Qt.Checked)

    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    dlg.delete_btn.click()

    assert cleared_emitted
    assert dlg.current_run_id is None


def test_runs_dashboard_preview_table_zero_values(tmp_path, qtbot):
    db_file = str(tmp_path / "preview_zero.db")
    db = Database(db_file)
    writer = DatabaseWriter(db_file)

    exp = writer.create_experiment("Exp Zero", "VOC", "")
    run = writer.create_run(exp, 20.0, "")
    writer.set_phase("STAGE_1")
    writer.insert_readings([
        {"roi_name": "ZeroFloat", "parsed_value": 0.0, "unit": "g", "is_valid": True},
        {"roi_name": "ZeroInt", "parsed_value": 0, "unit": "ppm", "is_valid": True},
    ])

    dlg = RunsDashboardDialog(db, current_run_id=run)
    qtbot.addWidget(dlg)

    # Preview table must show 0.0000 and 0, NOT blank empty string
    assert dlg.preview_table.rowCount() == 2
    row0_val = dlg.preview_table.item(0, 3).text()
    row1_val = dlg.preview_table.item(1, 3).text()
    assert row0_val in ("0.0000", "0")
    assert row1_val in ("0.0000", "0")


def test_create_run_records_ended_at_on_previous_run(tmp_path):
    db_file = str(tmp_path / "ended_at_test.db")
    writer = DatabaseWriter(db_file)
    db = Database(db_file)

    exp_id = writer.create_experiment("Multi Run Exp", "VOC", "")
    run1 = writer.create_run(exp_id, 20.0, "Run 1")
    assert writer.current_run_id == run1

    # Creating run 2 must set ended_at on run 1
    run2 = writer.create_run(exp_id, 30.0, "Run 2")
    assert writer.current_run_id == run2

    tree = db.get_experiments_tree()
    runs = tree[0]["runs"]
    r1 = next(r for r in runs if r["id"] == run1)
    r2 = next(r for r in runs if r["id"] == run2)
    assert r1["ended_at"] is not None
    assert r2["ended_at"] is None


def test_export_multi_run_csv_duplicate_channels_deduped(tmp_path):
    db_file = str(tmp_path / "dedup_channels.db")
    writer = DatabaseWriter(db_file)
    db = Database(db_file)

    exp = writer.create_experiment("Exp Dedup", "VOC", "")
    run1 = writer.create_run(exp, 20.0, "")
    writer.insert_readings([
        {"roi_name": "Sensor_1", "parsed_value": 10.0, "unit": "ppm", "is_calculated": 0}
    ])

    run2 = writer.create_run(exp, 25.0, "")
    writer.insert_readings([
        {"roi_name": "Sensor_1", "parsed_value": 20.0, "unit": "ppm", "is_calculated": 1}
    ])

    # get_multi_run_channels must return Sensor_1 exactly once
    channels = db.get_multi_run_channels([run1, run2])
    roi_names = [ch[0] for ch in channels]
    assert roi_names.count("Sensor_1") == 1

    # Pivot CSV export must not have duplicate column
    pivot_csv = str(tmp_path / "dedup_pivot.csv")
    db.export_multi_run_csv(pivot_csv, [run1, run2], format="pivot")
    with open(pivot_csv, "r", encoding="utf-8") as f:
        reader = list(csv.reader(f))
    header = reader[0]
    assert header.count("Sensor_1") == 1


def test_no_emojis_in_src():
    import os
    src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
    emojis_found = []
    for root, dirs, files in os.walk(src_dir):
        for f in files:
            if f.endswith(".py"):
                p = os.path.join(root, f)
                with open(p, "r", encoding="utf-8") as fh:
                    for line_idx, line in enumerate(fh, 1):
                        found = [
                            c for c in line
                            if (0x1F000 <= ord(c) <= 0x1FAFF)
                            or (0x2600 <= ord(c) <= 0x27BF and c not in ("✓", "⚠"))
                            or c in ("📦", "📏", "★", "☆")
                        ]
                        if found:
                            emojis_found.append((f, line_idx, found, line.strip()))
    assert emojis_found == [], f"Found emojis in source files: {emojis_found}"


