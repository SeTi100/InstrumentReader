import sqlite3
import os
import pytest
from instrument_reader.core.database import Database

def test_database_init(tmp_path):
    db_file = tmp_path / "test.db"
    db = Database(str(db_file))
    assert os.path.exists(db_file)
    
    with sqlite3.connect(db_file) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {row[0] for row in cursor.fetchall()}
        assert "experiments" in tables
        assert "runs" in tables
        assert "readings" in tables
        assert "roi_presets" in tables
