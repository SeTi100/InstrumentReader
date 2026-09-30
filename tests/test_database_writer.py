import os
import sqlite3
import pytest
from instrument_reader.core.database import Database
from instrument_reader.gui.db_writer import DatabaseWriter
from datetime import datetime

@pytest.fixture
def db_writer(tmp_path):
    db_path = tmp_path / "test.db"
    writer = DatabaseWriter(str(db_path))
    return writer

def test_create_experiment_and_run(db_writer):
    exp_id = db_writer.create_experiment("Exp 1", "VOC A", "Test desc")
    assert exp_id > 0
    
    run_id = db_writer.create_run(exp_id, 25.5, "Test notes")
    assert run_id > 0
    assert db_writer.current_run_id == run_id

def test_insert_readings(db_writer):
    exp_id = db_writer.create_experiment("Exp 1", "VOC A", "Test")
    run_id = db_writer.create_run(exp_id, 25.5, "Notes")
    
    readings = [{
        "roi_name": "ROI_1",
        "raw_text": "12.3",
        "parsed_value": 12.3,
        "unit": "g",
        "confidence": 0.95,
        "is_valid": True,
        "reason": "",
        "used_fallback": False
    }]
    
    db_writer.insert_readings(readings)
    
    with sqlite3.connect(db_writer.db.db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT roi_name, parsed_value, unit FROM readings WHERE run_id = ?", (run_id,))
        rows = cursor.fetchall()
        
    assert len(rows) == 1
    assert rows[0] == ("ROI_1", 12.3, "g")

def test_export_csv(db_writer, tmp_path):
    exp_id = db_writer.create_experiment("Exp 1", "VOC A", "Test")
    run_id = db_writer.create_run(exp_id, 25.5, "Notes")
    
    readings = [{
        "roi_name": "ROI_1",
        "raw_text": "12.3",
        "parsed_value": 12.3,
        "unit": "g",
        "confidence": 0.95,
        "is_valid": True,
        "reason": "",
        "used_fallback": False
    }]
    db_writer.insert_readings(readings)
    
    csv_path = tmp_path / "export.csv"
    db_writer.db.export_csv(str(csv_path), run_id)
    
    assert os.path.exists(csv_path)
    with open(csv_path, 'r') as f:
        lines = f.readlines()
        assert len(lines) == 2  # Header + 1 row
        assert "ROI_1" in lines[1]
        assert "12.3" in lines[1]
