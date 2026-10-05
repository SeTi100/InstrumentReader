import os
import csv
import json
import sqlite3
import pytest
from PySide6.QtWidgets import QMessageBox, QTableWidgetItem, QComboBox
from PySide6.QtCore import Qt

from instrument_reader.core.database import Database
from instrument_reader.gui.db_writer import DatabaseWriter
from instrument_reader.gui.experiment_dialog import (
    ParametersTableWidget, ExperimentDialog, NewRunDialog
)
from instrument_reader.gui.runs_dashboard import RunsDashboardDialog
from instrument_reader.gui.main_window import MainWindow


def test_parameters_table_widget_get_set(qtbot):
    widget = ParametersTableWidget()
    qtbot.addWidget(widget)

    assert widget.table.rowCount() == 0
    assert widget.get_parameters() == {}

    # Set parameters
    widget.set_parameters({
        "Konzentration": "500 ppm",
        "Temperatur": "50 °C",
        "CustomParam": "42 bar"
    })
    assert widget.table.rowCount() == 3

    params = widget.get_parameters()
    assert params["Konzentration"] == "500 ppm"
    assert params["Temperatur"] == "50 °C"
    assert params["CustomParam"] == "42 bar"

    # Add row
    widget.add_parameter("Volumenstrom", "24 Nl/h")
    assert widget.table.rowCount() == 4
    assert widget.get_parameters()["Volumenstrom"] == "24 Nl/h"

    # Remove row (last row when none selected)
    widget.table.clearSelection()
    widget.remove_parameter()
    assert widget.table.rowCount() == 3
    assert "Volumenstrom" not in widget.get_parameters()

    # Select specific row and remove
    widget.table.setCurrentCell(0, 0)
    widget.remove_parameter()
    assert widget.table.rowCount() == 2
    assert "Konzentration" not in widget.get_parameters()


def test_parameters_table_widget_suggestions(qtbot):
    widget = ParametersTableWidget()
    qtbot.addWidget(widget)

    widget.add_parameter()
    assert widget.table.rowCount() == 1

    combo = widget.table.cellWidget(0, 0)
    assert isinstance(combo, QComboBox)
    assert combo.isEditable()
    for s in ["Konzentration", "Temperatur", "Volumenstrom", "Druck", "GHSV", "Katalysator"]:
        assert combo.findText(s) >= 0


def test_experiment_dialog_and_new_run_dialog_get_data(qtbot):
    exp_dlg = ExperimentDialog()
    qtbot.addWidget(exp_dlg)
    exp_dlg.exp_name.setText("Test Catalysis")
    exp_dlg.voc_type.setText("Ethanol")
    exp_dlg.params_widget.set_parameters({
        "Konzentration": "200 ppm",
        "Temperatur": "75.5 °C"
    })
    exp_dlg.run_notes.setPlainText("Initial run notes")

    data = exp_dlg.get_data()
    assert data["exp_name"] == "Test Catalysis"
    assert data["voc_type"] == "Ethanol"
    assert data["parameters"]["Konzentration"] == "200 ppm"
    assert data["target_temp"] == 75.5
    assert data["run_notes"] == "Initial run notes"

    # NewRunDialog with initial parameters
    new_dlg = NewRunDialog(
        exp_name="Test Catalysis",
        exp_id=1,
        initial_parameters=data["parameters"]
    )
    qtbot.addWidget(new_dlg)
    assert new_dlg.params_widget.get_parameters()["Konzentration"] == "200 ppm"
    assert new_dlg.params_widget.get_parameters()["Temperatur"] == "75.5 °C"

    # Operator modifies only concentration for the new run
    new_dlg.params_widget.set_parameters({
        "Konzentration": "400 ppm",
        "Temperatur": "75.5 °C"
    })
    new_dlg.run_notes.setPlainText("Step 2: 400 ppm")
    run2_data = new_dlg.get_data()
    assert run2_data["parameters"]["Konzentration"] == "400 ppm"
    assert run2_data["parameters"]["Temperatur"] == "75.5 °C"
    assert run2_data["target_temp"] == 75.5
    assert run2_data["run_notes"] == "Step 2: 400 ppm"


def test_database_schema_migration_and_writer_create_run(tmp_path):
    db_file = str(tmp_path / "migration_test.db")
    # Simulate legacy database schema without parameters_json column
    with sqlite3.connect(db_file) as conn:
        conn.execute("""
            CREATE TABLE experiments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                voc_type TEXT,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.execute("""
            CREATE TABLE runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                experiment_id INTEGER NOT NULL,
                target_temperature REAL,
                notes TEXT,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ended_at TIMESTAMP
            );
        """)

    # Database initialization should automatically migrate schema
    db = Database(db_file)
    writer = DatabaseWriter(db_file)

    exp_id = writer.create_experiment("Migration Exp", "Acetone", "Desc")
    run_id = writer.create_run(
        exp_id=exp_id,
        notes="Flexible Run",
        parameters={"Konzentration": "500 ppm", "Temperatur": "80 °C", "GHSV": "5000 1/h"}
    )

    # Verify column exists and data stored
    tree = db.get_experiments_tree()
    assert len(tree) == 1
    run_record = tree[0]["runs"][0]
    assert run_record["id"] == run_id
    assert run_record["parameters"]["Konzentration"] == "500 ppm"
    assert run_record["parameters"]["Temperatur"] == "80 °C"
    assert run_record["parameters"]["GHSV"] == "5000 1/h"
    assert run_record["target_temperature"] == 80.0


def test_multi_run_csv_export_dynamic_parameters_raw_and_pivot(tmp_path):
    db_file = str(tmp_path / "csv_param_test.db")
    db = Database(db_file)
    writer = DatabaseWriter(db_file)

    exp_id = writer.create_experiment("Exp Catalysis", "Toluene", "")
    run1 = writer.create_run(
        exp_id=exp_id,
        notes="Run 1 - 500ppm",
        parameters={"Konzentration": "500 ppm", "Temperatur": "50 °C"}
    )
    writer.set_phase("STAGE_1")
    writer.insert_readings([
        {"roi_name": "Sensor_VOC", "parsed_value": 498.2, "unit": "ppm", "is_valid": True},
        {"roi_name": "Waage", "parsed_value": 10.02, "unit": "g", "is_valid": True},
    ])

    run2 = writer.create_run(
        exp_id=exp_id,
        notes="Run 2 - 1000ppm",
        parameters={"Konzentration": "1000 ppm", "Temperatur": "50 °C", "Volumenstrom": "24 Nl/h"}
    )
    writer.set_phase("STAGE_2")
    writer.insert_readings([
        {"roi_name": "Sensor_VOC", "parsed_value": 995.1, "unit": "ppm", "is_valid": True},
        {"roi_name": "Waage", "parsed_value": 10.01, "unit": "g", "is_valid": True},
    ])

    # 1. Export Raw format
    raw_csv = str(tmp_path / "export_raw_params.csv")
    db.export_multi_run_csv(raw_csv, [run1, run2], format="raw")
    assert os.path.exists(raw_csv)

    with open(raw_csv, "r", encoding="utf-8") as f:
        raw_rows = list(csv.reader(f))

    raw_header = raw_rows[0]
    assert "param_Konzentration" in raw_header
    assert "param_Temperatur" in raw_header
    assert "param_Volumenstrom" in raw_header

    idx_conc = raw_header.index("param_Konzentration")
    idx_temp = raw_header.index("param_Temperatur")
    idx_flow = raw_header.index("param_Volumenstrom")
    idx_run = raw_header.index("run_id")

    # Data rows: 2 readings for run1, 2 readings for run2
    assert len(raw_rows) == 5  # header + 4 rows
    for row in raw_rows[1:]:
        if int(row[idx_run]) == run1:
            assert row[idx_conc] == "500 ppm"
            assert row[idx_temp] == "50 °C"
            assert row[idx_flow] == ""  # Volumenstrom was not defined in run 1
        elif int(row[idx_run]) == run2:
            assert row[idx_conc] == "1000 ppm"
            assert row[idx_temp] == "50 °C"
            assert row[idx_flow] == "24 Nl/h"

    # 2. Export Pivot format
    pivot_csv = str(tmp_path / "export_pivot_params.csv")
    db.export_multi_run_csv(pivot_csv, [run1, run2], channels=["Sensor_VOC", "Waage"], format="pivot")
    assert os.path.exists(pivot_csv)

    with open(pivot_csv, "r", encoding="utf-8") as f:
        pivot_rows = list(csv.reader(f))

    pivot_header = pivot_rows[0]
    assert "Konzentration" in pivot_header
    assert "Temperatur" in pivot_header
    assert "Volumenstrom" in pivot_header
    assert "Sensor_VOC" in pivot_header
    assert "Waage" in pivot_header

    p_idx_conc = pivot_header.index("Konzentration")
    p_idx_temp = pivot_header.index("Temperatur")
    p_idx_flow = pivot_header.index("Volumenstrom")
    p_idx_run = pivot_header.index("run_id")

    # 1 pivot row for run1, 1 pivot row for run2
    assert len(pivot_rows) == 3
    r1_row = pivot_rows[1]
    assert int(r1_row[p_idx_run]) == run1
    assert r1_row[p_idx_conc] == "500 ppm"
    assert r1_row[p_idx_temp] == "50 °C"
    assert r1_row[p_idx_flow] == ""

    r2_row = pivot_rows[2]
    assert int(r2_row[p_idx_run]) == run2
    assert r2_row[p_idx_conc] == "1000 ppm"
    assert r2_row[p_idx_temp] == "50 °C"
    assert r2_row[p_idx_flow] == "24 Nl/h"


def test_runs_dashboard_parameters_display(tmp_path, qtbot):
    db_file = str(tmp_path / "dashboard_params.db")
    db = Database(db_file)
    writer = DatabaseWriter(db_file)

    exp = writer.create_experiment("Exp Dashboard", "Ethanol", "")
    run1 = writer.create_run(
        exp_id=exp,
        notes="Run A",
        parameters={"Konzentration": "500 ppm", "Temperatur": "50 °C"}
    )
    run2 = writer.create_run(
        exp_id=exp,
        notes="Run B",
        parameters={"Konzentration": "1000 ppm", "Volumenstrom": "10 Nl/h"}
    )

    dlg = RunsDashboardDialog(db, current_run_id=run1)
    qtbot.addWidget(dlg)

    # Check tree column 3 contains parameter summary
    item_exp = dlg.tree.topLevelItem(0)
    run1_item = item_exp.child(0)
    run2_item = item_exp.child(1)

    assert "Konzentration: 500 ppm" in run1_item.text(3)
    assert "Temperatur: 50 °C" in run1_item.text(3)
    assert "Konzentration: 1000 ppm" in run2_item.text(3)
    assert "Volumenstrom: 10 Nl/h" in run2_item.text(3)

    # Click run 1 -> inspect right pane details
    dlg.tree.setCurrentItem(run1_item)
    assert dlg.params_table.rowCount() == 2
    row0_k = dlg.params_table.item(0, 0).text()
    row0_v = dlg.params_table.item(0, 1).text()
    row1_k = dlg.params_table.item(1, 0).text()
    row1_v = dlg.params_table.item(1, 1).text()
    params_dict = {row0_k: row0_v, row1_k: row1_v}
    assert params_dict["Konzentration"] == "500 ppm"
    assert params_dict["Temperatur"] == "50 °C"

    # Click experiment -> params_table should clear
    dlg.tree.setCurrentItem(item_exp)
    assert dlg.params_table.rowCount() == 0


def test_main_window_parameters_workflow(tmp_path, qtbot, monkeypatch):
    win = MainWindow()
    qtbot.addWidget(win)
    db_file = str(tmp_path / "mw_params.db")
    win.db_writer.db.db_path = db_file
    win.db_writer.db._init_db()

    # 1. Create Experiment with initial Run 1 parameters
    exp_data = {
        "exp_name": "Benzene Run",
        "voc_type": "Benzene",
        "exp_desc": "Batch 1",
        "parameters": {"Konzentration": "300 ppm", "Temperatur": "40 °C"},
        "target_temp": 40.0,
        "run_notes": "Step 1"
    }
    monkeypatch.setattr(ExperimentDialog, "exec", lambda self: True)
    monkeypatch.setattr(ExperimentDialog, "get_data", lambda self: exp_data)

    win.control_panel.new_exp_btn.click()

    assert win.current_experiment_id == 1
    assert win.last_run_parameters == {"Konzentration": "300 ppm", "Temperatur": "40 °C"}
    assert win.db_writer.current_run_id == 1

    # 2. Operator opens New Run dialog: verify parameters are prefilled
    passed_initial_params = None

    def mock_new_run_init(self, exp_name="", exp_id=None, initial_parameters=None, parent=None, **kwargs):
        nonlocal passed_initial_params
        passed_initial_params = initial_parameters
        super(NewRunDialog, self).__init__(parent)

    monkeypatch.setattr(NewRunDialog, "__init__", mock_new_run_init)
    monkeypatch.setattr(NewRunDialog, "exec", lambda self: True)

    run2_data = {
        "parameters": {"Konzentration": "600 ppm", "Temperatur": "40 °C"},
        "target_temp": 40.0,
        "run_notes": "Step 2: double concentration"
    }
    monkeypatch.setattr(NewRunDialog, "get_data", lambda self: run2_data)

    win.control_panel.new_run_btn.click()

    # Verify that the previous run parameters were passed for prefilling!
    assert passed_initial_params == {"Konzentration": "300 ppm", "Temperatur": "40 °C"}
    # Verify that last_run_parameters is updated with the new run's parameters
    assert win.last_run_parameters == {"Konzentration": "600 ppm", "Temperatur": "40 °C"}
    assert win.db_writer.current_run_id == 2


def test_comma_decimal_temperature_extraction(qtbot):
    from instrument_reader.gui.experiment_dialog import extract_temperature

    assert extract_temperature({"Temperatur": "75,5 °C"}) == 75.5
    assert extract_temperature({"Temperature": "-12,3 °C"}) == -12.3
    assert extract_temperature({"target temp": "150,0 °C"}) == 150.0
    assert extract_temperature({"Konzentration": "500 ppm"}) is None

    exp_dlg = ExperimentDialog()
    qtbot.addWidget(exp_dlg)
    exp_dlg.params_widget.set_parameters({"Temperatur": "24,8 °C"})
    assert exp_dlg.get_data()["target_temp"] == 24.8

    new_dlg = NewRunDialog(initial_parameters={"Temperatur": "99,9 °C"})
    qtbot.addWidget(new_dlg)
    assert new_dlg.get_data()["target_temp"] == 99.9


def test_db_writer_create_run_argument_variants(tmp_path):
    db_file = str(tmp_path / "writer_variants.db")
    writer = DatabaseWriter(db_file)
    exp = writer.create_experiment("Exp", "VOC", "Desc")

    # 1. Single notes argument
    r1 = writer.create_run(exp, "Clean observations")
    tree = writer.db.get_experiments_tree()
    r1_rec = next(r for r in tree[0]["runs"] if r["id"] == r1)
    assert r1_rec["notes"] == "Clean observations"
    assert r1_rec["target_temperature"] is None

    # 2. Legacy positional: (exp, 30.5, "Legacy notes")
    r2 = writer.create_run(exp, 30.5, "Legacy notes")
    tree = writer.db.get_experiments_tree()
    r2_rec = next(r for r in tree[0]["runs"] if r["id"] == r2)
    assert r2_rec["target_temperature"] == 30.5
    assert r2_rec["notes"] == "Legacy notes"

    # 3. Parameters with German comma decimal temperature
    r3 = writer.create_run(
        exp,
        notes="Step 3",
        parameters={"Temperatur": "65,5 °C", "Volumenstrom": "10 Nl/h"}
    )
    tree = writer.db.get_experiments_tree()
    r3_rec = next(r for r in tree[0]["runs"] if r["id"] == r3)
    assert r3_rec["notes"] == "Step 3"
    assert r3_rec["target_temperature"] == 65.5
    assert r3_rec["parameters"]["Volumenstrom"] == "10 Nl/h"


def test_new_run_dialog_empty_initial_parameters_and_parent_widget(qtbot):
    from PySide6.QtWidgets import QWidget

    # Empty dict should NOT resurrect default 20 °C
    dlg_empty = NewRunDialog(initial_parameters={})
    qtbot.addWidget(dlg_empty)
    assert dlg_empty.params_widget.table.rowCount() == 0
    assert dlg_empty.params_widget.get_parameters() == {}

    # Passing parent widget as first argument
    parent_widget = QWidget()
    qtbot.addWidget(parent_widget)
    dlg_parent = NewRunDialog(parent_widget)
    qtbot.addWidget(dlg_parent)
    assert dlg_parent.parent() == parent_widget


def test_runs_dashboard_clear_details_clears_params_table(tmp_path, qtbot):
    db_file = str(tmp_path / "dashboard_clear.db")
    writer = DatabaseWriter(db_file)
    exp = writer.create_experiment("Exp Clear", "VOC", "")
    run = writer.create_run(exp, parameters={"Konzentration": "500 ppm", "Temperatur": "40 °C"})

    dlg = RunsDashboardDialog(writer.db, current_run_id=run)
    qtbot.addWidget(dlg)

    # Initially populated
    assert dlg.params_table.rowCount() == 2

    # Clear details explicitly
    dlg._clear_details()
    assert dlg.params_table.rowCount() == 0


def test_multi_run_csv_export_standard_column_collision(tmp_path):
    db_file = str(tmp_path / "collision_test.db")
    writer = DatabaseWriter(db_file)
    exp = writer.create_experiment("Exp Collide", "VOC", "")
    run1 = writer.create_run(exp, parameters={"timestamp": "T1", "run_id": "R1"})

    writer.set_phase("STAGE_1")
    writer.insert_readings([{"roi_name": "Sensor", "parsed_value": 1.0, "unit": "g"}])

    out_csv = str(tmp_path / "collision_pivot.csv")
    writer.db.export_multi_run_csv(out_csv, [run1], format="pivot")

    with open(out_csv, "r", encoding="utf-8") as f:
        header = csv.reader(f).__next__()

    # Parameters colliding with standard pivot columns should have param_ prefix
    assert "param_timestamp" in header
    assert "param_run_id" in header
    # Ensure no duplicate "timestamp" or "run_id" in header
    assert header.count("timestamp") == 1
    assert header.count("run_id") == 1


def test_parameters_table_widget_zero_values(qtbot):
    widget = ParametersTableWidget()
    qtbot.addWidget(widget)

    widget.add_parameter(0, 0)
    assert widget.table.rowCount() == 1
    params = widget.get_parameters()
    assert params.get("0") == "0"

