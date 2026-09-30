import sqlite3
import csv
from datetime import datetime

class Database:
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS experiments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        voc_type TEXT,
        description TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        experiment_id INTEGER NOT NULL,
        target_temperature REAL,
        notes TEXT,
        started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        ended_at TIMESTAMP,
        FOREIGN KEY (experiment_id) REFERENCES experiments(id)
    );

    CREATE TABLE IF NOT EXISTS readings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL,
        timestamp TIMESTAMP NOT NULL,
        phase_status TEXT NOT NULL,
        roi_name TEXT NOT NULL,
        raw_ocr_text TEXT,
        parsed_value REAL,
        unit TEXT,
        confidence REAL,
        is_valid INTEGER DEFAULT 1,
        validation_reason TEXT,
        used_fallback INTEGER DEFAULT 0,
        FOREIGN KEY (run_id) REFERENCES runs(id)
    );

    CREATE TABLE IF NOT EXISTS roi_presets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        preset_name TEXT NOT NULL,
        config_json TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_readings_run_phase 
        ON readings(run_id, phase_status);
    CREATE INDEX IF NOT EXISTS idx_readings_timestamp 
        ON readings(timestamp);
    """

    def __init__(self, db_path: str = "instrument_reader.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(self.SCHEMA)

    def export_csv(self, file_path: str, run_id: int):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM readings WHERE run_id = ?", (run_id,))
            rows = cursor.fetchall()
            col_names = [description[0] for description in cursor.description]

            with open(file_path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(col_names)
                writer.writerows(rows)
