import sqlite3
import csv
import json
from datetime import datetime
from typing import List, Optional, Tuple, Dict, Any

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
        parameters_json TEXT DEFAULT '{}',
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
        is_calculated INTEGER DEFAULT 0,
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

    def __init__(self, db_path: str | None = None, journal: bool = True):
        if db_path is None:
            from instrument_reader.core.data_safety import db_path as default_db_path

            default = default_db_path()
            default.parent.mkdir(parents=True, exist_ok=True)
            db_path = str(default)
        self.db_path = db_path
        self._journal_enabled = journal
        self._journal = None
        self._init_db()

    @property
    def journal(self):
        """Append-only journal next to the database (follows db_path); None if disabled."""
        if not self._journal_enabled:
            return None
        from instrument_reader.core.data_safety import journal_dir_for
        from instrument_reader.core.journal import Journal

        folder = journal_dir_for(self.db_path)
        if self._journal is None or self._journal.folder != folder:
            self._journal = Journal(folder)
        return self._journal

    def _log(self, event: str, **data) -> None:
        if self.journal is not None:
            self.journal.append(event, **data)

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.executescript(self.SCHEMA)
            try:
                conn.execute("ALTER TABLE readings ADD COLUMN is_calculated INTEGER DEFAULT 0")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE runs ADD COLUMN parameters_json TEXT DEFAULT '{}'")
            except sqlite3.OperationalError:
                pass
            # Run / experiment life cycle (dedicated stop, resume, crash detection)
            for ddl in (
                "ALTER TABLE runs ADD COLUMN status TEXT",          # running | completed | aborted
                "ALTER TABLE runs ADD COLUMN end_reason TEXT",      # stop | new_run | switched | app_closed | experiment_end | crash
                "ALTER TABLE runs ADD COLUMN data_started_at TIMESTAMP",  # time of the first / last reading
                "ALTER TABLE runs ADD COLUMN data_ended_at TIMESTAMP",    # (video time for files)
                "ALTER TABLE experiments ADD COLUMN status TEXT DEFAULT 'open'",  # open | closed
                "ALTER TABLE experiments ADD COLUMN ended_at TIMESTAMP",
            ):
                try:
                    conn.execute(ddl)
                except sqlite3.OperationalError:
                    pass
            conn.executescript(self.RUN_EVENTS_SCHEMA)
            self.upgrade_rows(conn)

    # Start, stop, resume and abort of each run, so gaps in a resumed run stay visible.
    RUN_EVENTS_SCHEMA = """
    CREATE TABLE IF NOT EXISTS run_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL,
        time TIMESTAMP NOT NULL,
        event TEXT NOT NULL,          -- start | stop | resume | abort
        reason TEXT,
        FOREIGN KEY (run_id) REFERENCES runs(id)
    );
    CREATE INDEX IF NOT EXISTS idx_run_events_run ON run_events(run_id);
    """

    @staticmethod
    def upgrade_rows(conn) -> None:
        """Brings rows written by older versions up to date; safe to run repeatedly.

        - Timestamps from SQLite's CURRENT_TIMESTAMP are UTC ("YYYY-MM-DD HH:MM:SS"); all
          other times in the app are local ISO ("YYYY-MM-DDTHH:MM:SS"), so they become local ISO.
        - Runs without a status: with an end time they were completed, without one they
          were cut off (crash), ending at their last reading.
        """
        legacy = "____-__-__ __:__:__"
        for table, col in (("runs", "started_at"), ("experiments", "created_at"), ("roi_presets", "created_at")):
            conn.execute(
                f"UPDATE {table} SET {col} = replace(datetime({col}, 'localtime'), ' ', 'T') WHERE {col} LIKE ?",
                (legacy,))
        conn.execute("""
            UPDATE runs SET
                data_started_at = COALESCE(data_started_at, (SELECT MIN(timestamp) FROM readings WHERE run_id = runs.id)),
                data_ended_at = COALESCE(data_ended_at, (SELECT MAX(timestamp) FROM readings WHERE run_id = runs.id))
            WHERE status IS NULL
        """)
        conn.execute("UPDATE runs SET status = 'completed', end_reason = COALESCE(end_reason, 'legacy') "
                     "WHERE status IS NULL AND ended_at IS NOT NULL")
        conn.execute("UPDATE runs SET status = 'aborted', end_reason = COALESCE(end_reason, 'crash'), "
                     "ended_at = COALESCE(data_ended_at, started_at) WHERE status IS NULL")
        conn.execute("UPDATE experiments SET status = 'open' WHERE status IS NULL")

    def get_run(self, run_id: int) -> Optional[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            return dict(row) if row else None

    def get_experiment(self, exp_id: int) -> Optional[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM experiments WHERE id = ?", (exp_id,)).fetchone()
            return dict(row) if row else None

    def get_run_events(self, run_id: int) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(
                "SELECT time, event, reason FROM run_events WHERE run_id = ? ORDER BY id", (run_id,))]

    def abort_open_runs(self) -> List[int]:
        """Marks runs still 'running' as aborted (the app ended without stopping them,
        e.g. a crash); their end is their last reading. Call once at start-up."""
        with sqlite3.connect(self.db_path) as conn:
            ids = [r[0] for r in conn.execute("SELECT id FROM runs WHERE status = 'running'")]
            for run_id in ids:
                ended = conn.execute(
                    "SELECT COALESCE(data_ended_at, started_at) FROM runs WHERE id = ?", (run_id,)).fetchone()[0]
                conn.execute("UPDATE runs SET status = 'aborted', end_reason = 'crash', ended_at = ? WHERE id = ?",
                             (ended, run_id))
                conn.execute("INSERT INTO run_events (run_id, time, event, reason) VALUES (?, ?, 'abort', 'crash')",
                             (run_id, ended))
        for run_id in ids:
            row = self.get_run(run_id)
            self._log("run_end", id=run_id, ended_at=row["ended_at"], status="aborted", end_reason="crash",
                      data_ended_at=row["data_ended_at"])
        return ids

    def get_run_channels(self, run_id: int):
        """Returns list of (roi_name, is_calculated) for the given run_id."""
        return self.get_multi_run_channels([run_id])

    def get_multi_run_channels(self, run_ids: List[int]) -> List[Tuple[str, bool]]:
        """Returns distinct list of (roi_name, is_calculated) for the given run_ids."""
        if not run_ids:
            return []
        placeholders = ",".join("?" for _ in run_ids)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT roi_name, MAX(is_calculated) 
                FROM readings 
                WHERE run_id IN ({placeholders})
                GROUP BY roi_name
                ORDER BY MAX(is_calculated) ASC, roi_name ASC
            """, list(run_ids))
            return [(row[0], bool(row[1])) for row in cursor.fetchall()]

    def get_experiments_tree(self) -> List[Dict[str, Any]]:
        """Returns list of experiments with nested runs and aggregate statistics."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, name, voc_type, description, created_at, status, ended_at
                FROM experiments
                ORDER BY id DESC
            """)
            experiments = [dict(row) for row in cursor.fetchall()]

            for exp in experiments:
                cursor.execute("""
                    SELECT r.id, r.experiment_id, r.target_temperature, r.notes, 
                           r.parameters_json,
                           r.started_at, r.ended_at, r.status, r.end_reason,
                           COUNT(rd.id) AS reading_count,
                           MIN(rd.timestamp) AS first_reading,
                           MAX(rd.timestamp) AS last_reading
                    FROM runs r
                    LEFT JOIN readings rd ON rd.run_id = r.id
                    WHERE r.experiment_id = ?
                    GROUP BY r.id
                    ORDER BY r.id ASC
                """, (exp["id"],))
                runs = []
                for r in cursor.fetchall():
                    r_dict = dict(r)
                    params = {}
                    p_json = r_dict.get("parameters_json")
                    if p_json:
                        try:
                            parsed = json.loads(p_json)
                            if isinstance(parsed, dict):
                                params = parsed
                        except Exception:
                            params = {}
                    r_dict["parameters"] = params
                    runs.append(r_dict)
                exp["runs"] = runs

            return experiments

    def delete_run(self, run_id: int):
        """Deletes a run and all associated readings."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM readings WHERE run_id = ?", (run_id,))
            cursor.execute("DELETE FROM run_events WHERE run_id = ?", (run_id,))
            cursor.execute("DELETE FROM runs WHERE id = ?", (run_id,))
            conn.commit()
        self._log("delete_run", id=run_id)

    def delete_experiment(self, experiment_id: int):
        """Deletes an experiment, all its runs, and all associated readings."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                DELETE FROM readings WHERE run_id IN (
                    SELECT id FROM runs WHERE experiment_id = ?
                )
            """, (experiment_id,))
            cursor.execute(
                "DELETE FROM run_events WHERE run_id IN (SELECT id FROM runs WHERE experiment_id = ?)", (experiment_id,))
            cursor.execute("DELETE FROM runs WHERE experiment_id = ?", (experiment_id,))
            cursor.execute("DELETE FROM experiments WHERE id = ?", (experiment_id,))
            conn.commit()
        self._log("delete_experiment", id=experiment_id)

    def get_run_readings_preview(self, run_id: int, limit: int = 100) -> List[Dict[str, Any]]:
        """Returns latest readings preview for a run."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT timestamp, phase_status, roi_name, parsed_value, unit
                FROM readings
                WHERE run_id = ?
                ORDER BY id DESC
                LIMIT ?
            """, (run_id, limit))
            return [dict(r) for r in cursor.fetchall()]

    def export_csv(self, file_path: str, run_id: int, channels=None):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            if channels is not None:
                if len(channels) == 0:
                    cursor.execute("SELECT * FROM readings WHERE run_id = ? AND 0", (run_id,))
                else:
                    placeholders = ",".join("?" for _ in channels)
                    cursor.execute(
                        f"SELECT * FROM readings WHERE run_id = ? AND roi_name IN ({placeholders})",
                        [run_id] + list(channels)
                    )
            else:
                cursor.execute("SELECT * FROM readings WHERE run_id = ?", (run_id,))
            rows = cursor.fetchall()
            col_names = [description[0] for description in cursor.description]

            with open(file_path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(col_names)
                writer.writerows(rows)

    def export_multi_run_csv(
        self,
        file_path: str,
        run_ids: List[int],
        channels: Optional[List[str]] = None,
        format: str = "raw",
    ):
        """
        Exports readings from multiple runs to CSV.
        format="raw":
            Long format with columns:
            [experiment_id, experiment_name, voc_type, run_id, target_temperature,
             timestamp, phase_status, roi_name, parsed_value, unit, is_calculated]
        format="pivot":
            Wide format with columns:
            [timestamp, run_id, experiment_name, phase_status, <channel1>, <channel2>, ...]
            Groups readings occurring within 0.2s of each other in the same run into one row.
        """
        if not run_ids:
            with open(file_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if format == "raw":
                    writer.writerow([
                        "experiment_id", "experiment_name", "voc_type",
                        "run_id", "target_temperature", "timestamp",
                        "phase_status", "roi_name", "parsed_value", "unit", "is_calculated"
                    ])
                else:
                    header = ["timestamp", "run_id", "experiment_name", "phase_status"]
                    if channels:
                        header.extend(list(dict.fromkeys(channels)))
                    writer.writerow(header)
            return

        placeholders = ",".join("?" for _ in run_ids)

        # Retrieve parameters for the selected runs
        run_params_map: Dict[int, Dict[str, Any]] = {}
        unique_param_names: List[str] = []
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT id, parameters_json FROM runs WHERE id IN ({placeholders}) ORDER BY id ASC",
                list(run_ids)
            )
            for r_id, p_json in cursor.fetchall():
                p_dict = {}
                if p_json:
                    try:
                        parsed = json.loads(p_json)
                        if isinstance(parsed, dict):
                            p_dict = parsed
                    except Exception:
                        p_dict = {}
                run_params_map[r_id] = p_dict
                for k in p_dict.keys():
                    if k not in unique_param_names:
                        unique_param_names.append(k)

        params: List[Any] = list(run_ids)
        chan_filter = ""
        if channels is not None:
            if len(channels) == 0:
                chan_filter = "AND 0"
            else:
                c_placeholders = ",".join("?" for _ in channels)
                chan_filter = f"AND rd.roi_name IN ({c_placeholders})"
                params.extend(channels)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            if format == "raw":
                query = f"""
                    SELECT e.id AS experiment_id, e.name AS experiment_name, e.voc_type,
                           r.id AS run_id, r.target_temperature,
                           rd.timestamp, rd.phase_status, rd.roi_name, rd.parsed_value, rd.unit, rd.is_calculated
                    FROM readings rd
                    JOIN runs r ON rd.run_id = r.id
                    JOIN experiments e ON r.experiment_id = e.id
                    WHERE rd.run_id IN ({placeholders}) {chan_filter}
                    ORDER BY rd.run_id ASC, rd.timestamp ASC, rd.id ASC
                """
                cursor.execute(query, params)
                rows = cursor.fetchall()
                param_headers = [f"param_{k}" for k in unique_param_names]
                header = [
                    "experiment_id", "experiment_name", "voc_type",
                    "run_id", "target_temperature"
                ] + param_headers + [
                    "timestamp", "phase_status", "roi_name",
                    "parsed_value", "unit", "is_calculated"
                ]
                with open(file_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow(header)
                    for row in rows:
                        r_id = row[3]
                        r_params = run_params_map.get(r_id, {})
                        param_vals = [r_params.get(k, "") for k in unique_param_names]
                        full_row = list(row[:5]) + param_vals + list(row[5:])
                        writer.writerow(full_row)
            else:
                # Pivot format
                if channels is not None:
                    channel_names = list(dict.fromkeys(channels))
                else:
                    channel_pairs = self.get_multi_run_channels(run_ids)
                    channel_names = list(dict.fromkeys(ch[0] for ch in channel_pairs))

                query = f"""
                    SELECT rd.timestamp, r.id AS run_id, e.name AS experiment_name,
                           rd.phase_status, rd.roi_name, rd.parsed_value
                    FROM readings rd
                    JOIN runs r ON rd.run_id = r.id
                    JOIN experiments e ON r.experiment_id = e.id
                    WHERE rd.run_id IN ({placeholders}) {chan_filter}
                    ORDER BY rd.run_id ASC, rd.timestamp ASC, rd.id ASC
                """
                cursor.execute(query, params)
                rows = cursor.fetchall()

                def _parse_ts(ts_val) -> float:
                    if isinstance(ts_val, (int, float)):
                        return float(ts_val)
                    if not ts_val:
                        return 0.0
                    try:
                        return float(ts_val)
                    except ValueError:
                        pass
                    try:
                        return datetime.fromisoformat(str(ts_val)).timestamp()
                    except Exception:
                        return 0.0

                pivot_rows: List[Dict[str, Any]] = []
                current_row: Optional[Dict[str, Any]] = None

                for ts_str, r_id, exp_name, phase_stat, roi_nm, val in rows:
                    t_num = _parse_ts(ts_str)
                    can_merge = (
                        current_row is not None
                        and current_row["run_id"] == r_id
                        and current_row["phase_status"] == phase_stat
                        and abs(t_num - current_row["_t_num"]) <= 0.2
                        and current_row.get(roi_nm) is None
                    )

                    if can_merge:
                        if roi_nm in channel_names:
                            current_row[roi_nm] = val
                    else:
                        if current_row is not None:
                            pivot_rows.append(current_row)
                        current_row = {
                            "_t_num": t_num,
                            "timestamp": ts_str,
                            "run_id": r_id,
                            "experiment_name": exp_name,
                            "phase_status": phase_stat,
                        }
                        for ch in channel_names:
                            current_row[ch] = None
                        if roi_nm in channel_names:
                            current_row[roi_nm] = val

                if current_row is not None:
                    pivot_rows.append(current_row)

                standard_pivot_cols = {"timestamp", "run_id", "experiment_name", "phase_status"}
                pivot_param_headers = [
                    f"param_{k}" if (k in channel_names or k in standard_pivot_cols) else k
                    for k in unique_param_names
                ]
                header = ["timestamp", "run_id", "experiment_name", "phase_status"] + pivot_param_headers + channel_names
                with open(file_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow(header)
                    for prow in pivot_rows:
                        r_id = prow["run_id"]
                        r_params = run_params_map.get(r_id, {})
                        param_vals = [r_params.get(k, "") for k in unique_param_names]
                        row_vals = [
                            prow["timestamp"],
                            prow["run_id"],
                            prow["experiment_name"],
                            prow["phase_status"],
                        ] + param_vals + ["" if prow.get(ch) is None else prow[ch] for ch in channel_names]
                        writer.writerow(row_vals)

