import sqlite3
import time
from datetime import datetime
from PySide6.QtCore import QObject, Signal, Slot
from instrument_reader.core.database import Database

class DatabaseWriter(QObject):
    def __init__(self, db_path="instrument_reader.db"):
        super().__init__()
        self.db = Database(db_path)
        self.current_run_id = None
        self.current_phase = "IDLE"
        
    def create_experiment(self, name, voc_type, desc):
        with sqlite3.connect(self.db.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO experiments (name, voc_type, description) VALUES (?, ?, ?)",
                           (name, voc_type, desc))
            return cursor.lastrowid
            
    def create_run(self, exp_id, target_temp, notes):
        with sqlite3.connect(self.db.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO runs (experiment_id, target_temperature, notes) VALUES (?, ?, ?)",
                           (exp_id, target_temp, notes))
            self.current_run_id = cursor.lastrowid
            return self.current_run_id
            
    def end_run(self):
        if self.current_run_id:
            with sqlite3.connect(self.db.db_path) as conn:
                conn.execute("UPDATE runs SET ended_at = ? WHERE id = ?",
                             (datetime.now().isoformat(), self.current_run_id))
            self.current_run_id = None
            
    def set_phase(self, phase):
        self.current_phase = phase

    @Slot(list)
    def insert_readings(self, readings):
        if not self.current_run_id:
            return
            
        now_str = datetime.now().isoformat()
        with sqlite3.connect(self.db.db_path) as conn:
            cursor = conn.cursor()
            for r in readings:
                if r.get("is_child", False):
                    continue
                cursor.execute("""
                    INSERT INTO readings (run_id, timestamp, phase_status, roi_name, 
                                          raw_ocr_text, parsed_value, unit, confidence, 
                                          is_valid, validation_reason, used_fallback)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    self.current_run_id, now_str, self.current_phase, r["roi_name"],
                    r["raw_text"], r["parsed_value"], r.get("unit", ""), r["confidence"],
                    1 if r["is_valid"] else 0, r.get("reason", ""), 1 if r.get("used_fallback") else 0
                ))
                
    def save_roi_preset(self, name, config_json):
        with sqlite3.connect(self.db.db_path) as conn:
            conn.execute("INSERT INTO roi_presets (preset_name, config_json) VALUES (?, ?)", (name, config_json))
            
    def load_roi_presets(self):
        with sqlite3.connect(self.db.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, preset_name, config_json FROM roi_presets ORDER BY created_at DESC")
            return cursor.fetchall()
