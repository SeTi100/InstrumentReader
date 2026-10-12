import sqlite3
import time
import json
import re
from datetime import datetime
from typing import Optional, Dict, Any
from PySide6.QtCore import QObject, Signal, Slot
from instrument_reader.core.database import Database

class DatabaseWriter(QObject):
    # Emitted after a run ended (run id), e.g. to back up the database.
    run_ended = Signal(int)

    def __init__(self, db_path=None):
        super().__init__()
        self.db = Database(db_path)
        self.current_run_id = None
        self.current_phase = "IDLE"

    def create_experiment(self, name, voc_type, desc):
        with sqlite3.connect(self.db.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO experiments (name, voc_type, description) VALUES (?, ?, ?)",
                           (name, voc_type, desc))
            exp_id = cursor.lastrowid
            created_at = conn.execute("SELECT created_at FROM experiments WHERE id = ?", (exp_id,)).fetchone()[0]
        self.db._log("experiment", id=exp_id, name=name, voc_type=voc_type, description=desc, created_at=created_at)
        return exp_id

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
            started_at = conn.execute("SELECT started_at FROM runs WHERE id = ?", (self.current_run_id,)).fetchone()[0]
        self.db._log("run", id=self.current_run_id, experiment_id=exp_id, target_temperature=target_temp,
                     notes=notes, parameters_json=params_json, started_at=started_at)
        return self.current_run_id

    def end_run(self):
        if self.current_run_id:
            run_id, ended_at = self.current_run_id, datetime.now().isoformat()
            with sqlite3.connect(self.db.db_path) as conn:
                conn.execute("UPDATE runs SET ended_at = ? WHERE id = ?", (ended_at, run_id))
            self.db._log("run_end", id=run_id, ended_at=ended_at)
            self.current_run_id = None
            self.run_ended.emit(run_id)

    def set_active_run(self, run_id: int):
        self.current_run_id = run_id
            
    def set_phase(self, phase):
        self.current_phase = phase

    @Slot(list)
    def insert_readings(self, readings, timestamp: Optional[float] = None):
        """
        Logs a batch of readings. timestamp (epoch seconds) is the time of the
        frame they were read from - video time for files - and defaults to the
        first reading's "timestamp", then to the current wall-clock time.
        """
        if not self.current_run_id:
            return

        if timestamp is None:
            timestamp = next(
                (r["timestamp"] for r in readings if isinstance(r.get("timestamp"), (int, float))),
                None,
            )
        ts_dt = datetime.fromtimestamp(timestamp) if timestamp is not None else datetime.now()
        now_str = ts_dt.isoformat()
        rows = [
            (
                self.current_run_id, now_str, self.current_phase, r["roi_name"],
                r.get("raw_text", ""), r["parsed_value"], r.get("unit", ""), r.get("confidence", 1.0),
                1 if r.get("is_valid", True) else 0, r.get("reason", ""),
                1 if r.get("used_fallback") else 0,
                1 if r.get("is_calculated") else 0
            )
            for r in readings if not r.get("is_child", False)
        ]
        if not rows:
            return
        with sqlite3.connect(self.db.db_path) as conn:
            conn.executemany("""
                INSERT INTO readings (run_id, timestamp, phase_status, roi_name,
                                      raw_ocr_text, parsed_value, unit, confidence,
                                      is_valid, validation_reason, used_fallback, is_calculated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, rows)
        self.db._log("readings", rows=rows)

    def save_roi_preset(self, name, config_json):
        with sqlite3.connect(self.db.db_path) as conn:
            cursor = conn.execute("INSERT INTO roi_presets (preset_name, config_json) VALUES (?, ?)", (name, config_json))
            created_at = conn.execute("SELECT created_at FROM roi_presets WHERE id = ?", (cursor.lastrowid,)).fetchone()[0]
        self.db._log("roi_preset", name=name, config_json=config_json, created_at=created_at)
            
    def load_roi_presets(self):
        with sqlite3.connect(self.db.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, preset_name, config_json FROM roi_presets ORDER BY created_at DESC")
            return cursor.fetchall()
