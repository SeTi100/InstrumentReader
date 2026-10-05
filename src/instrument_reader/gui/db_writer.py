import sqlite3
import time
import json
import re
from datetime import datetime
from typing import Optional, Dict, Any
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

    @staticmethod
    def _extract_temp_from_params(params: Dict[str, Any]) -> Optional[float]:
        if not isinstance(params, dict):
            return None
        for k, v in params.items():
            k_clean = str(k).strip().lower()
            if k_clean in ("temperatur", "temperature", "target temp", "target_temp", "temp", "target temperature"):
                if isinstance(v, (int, float)):
                    return float(v)
                if isinstance(v, str):
                    v_clean = v.replace(",", ".")
                    m = re.search(r"[-+]?(?:\d+\.?\d*|\.\d+)", v_clean)
                    if m:
                        try:
                            return float(m.group(0))
                        except ValueError:
                            pass
        return None
            
    def create_run(
        self,
        exp_id: int,
        notes: Any = "",
        parameters: Optional[Dict[str, Any]] = None,
        target_temp: Optional[float] = None,
        **kwargs
    ):
        if self.current_run_id is not None:
            self.end_run()

        if "notes" in kwargs:
            notes = kwargs["notes"]
        if "parameters" in kwargs:
            parameters = kwargs["parameters"]
        if "target_temp" in kwargs:
            target_temp = kwargs["target_temp"]

        # Support legacy positional call: create_run(exp_id, target_temp, notes)
        if isinstance(notes, (int, float)):
            target_temp = float(notes)
            notes = str(parameters) if (parameters is not None and not isinstance(parameters, dict)) else ""
            parameters = kwargs.get("parameters")
        elif isinstance(notes, dict) and parameters is None:
            parameters = notes
            notes = ""
        elif isinstance(notes, str) and parameters is not None and not isinstance(parameters, dict):
            # Check if notes was a numeric temperature string passed positionally
            v_clean = str(notes).strip().replace(",", ".")
            m = re.match(r"^[-+]?(?:\d+\.?\d*|\.\d+)$", v_clean)
            if m:
                target_temp = float(m.group(0))
                notes = str(parameters)
                parameters = kwargs.get("parameters")

        if parameters is None:
            parameters = {}
        else:
            if target_temp is None:
                target_temp = self._extract_temp_from_params(parameters)

        params_json = json.dumps(parameters, ensure_ascii=False)

        with sqlite3.connect(self.db.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO runs (experiment_id, target_temperature, notes, parameters_json) VALUES (?, ?, ?, ?)",
                (exp_id, target_temp, notes, params_json)
            )
            self.current_run_id = cursor.lastrowid
            return self.current_run_id
            
    def end_run(self):
        if self.current_run_id:
            with sqlite3.connect(self.db.db_path) as conn:
                conn.execute("UPDATE runs SET ended_at = ? WHERE id = ?",
                             (datetime.now().isoformat(), self.current_run_id))
            self.current_run_id = None

    def set_active_run(self, run_id: int):
        self.current_run_id = run_id
            
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
                                          is_valid, validation_reason, used_fallback, is_calculated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    self.current_run_id, now_str, self.current_phase, r["roi_name"],
                    r.get("raw_text", ""), r["parsed_value"], r.get("unit", ""), r.get("confidence", 1.0),
                    1 if r.get("is_valid", True) else 0, r.get("reason", ""),
                    1 if r.get("used_fallback") else 0,
                    1 if r.get("is_calculated") else 0
                ))
                
    def save_roi_preset(self, name, config_json):
        with sqlite3.connect(self.db.db_path) as conn:
            conn.execute("INSERT INTO roi_presets (preset_name, config_json) VALUES (?, ?)", (name, config_json))
            
    def load_roi_presets(self):
        with sqlite3.connect(self.db.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, preset_name, config_json FROM roi_presets ORDER BY created_at DESC")
            return cursor.fetchall()
