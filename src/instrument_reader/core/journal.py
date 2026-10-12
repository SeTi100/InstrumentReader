"""Append-only journal of everything written to the measurement database.

Every new experiment, run, run end, batch of readings, ROI preset and deletion is also
appended as one JSON line to ``<data>/journal/YYYY-MM-DD.jsonl``. Files are only ever
opened for appending and fsync'ed after each write; files of past days are made
read-only. ``rebuild_database()`` replays the journal into a fresh database, so the
SQLite file can always be recreated from it (``scripts/rebuild_db.py``).
"""
import json
import logging
import os
import sqlite3
import stat
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)


class Journal:
    def __init__(self, folder: str | Path):
        self.folder = Path(folder)
        self._sealed_before: str | None = None

    def append(self, event: str, **data) -> None:
        """Appends one event. Problems are logged, never raised: the database write
        that triggered it has already happened and the measurement must go on."""
        now = datetime.now()
        record = {"t": now.isoformat(timespec="milliseconds"), "ev": event, **data}
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            day = now.strftime("%Y-%m-%d")
            if self._sealed_before != day:
                self._seal_older_than(day)
                self._sealed_before = day
            with open(self.folder / f"{day}.jsonl", "a", encoding="utf-8", newline="\n") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
        except OSError as e:
            log.error("Journal-Eintrag konnte nicht geschrieben werden: %s", e)

    def _seal_older_than(self, day: str) -> None:
        for p in self.folder.glob("*.jsonl"):
            if p.stem < day:
                try:
                    os.chmod(p, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
                except OSError:
                    pass


def journal_files(folder: str | Path) -> list[Path]:
    return sorted(Path(folder).glob("*.jsonl"))


def read_events(folder: str | Path):
    for path in journal_files(folder):
        with open(path, encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except ValueError:
                    log.warning("%s Zeile %d unlesbar (abgebrochener Schreibvorgang?), übersprungen", path.name, n)


def rebuild_database(journal_folder: str | Path, target: str | Path) -> dict:
    """Creates target (must not exist) from the journal. Returns event counts."""
    from instrument_reader.core.database import Database

    target = Path(target)
    if target.exists():
        raise FileExistsError(f"{target} existiert schon; bitte einen neuen Dateinamen wählen.")
    Database(str(target), journal=False)
    counts: dict[str, int] = {}
    conn = sqlite3.connect(str(target))
    try:
        for e in read_events(journal_folder):
            ev = e.get("ev")
            counts[ev] = counts.get(ev, 0) + 1
            if ev == "experiment":
                conn.execute(
                    "INSERT OR REPLACE INTO experiments (id, name, voc_type, description, created_at) VALUES (?, ?, ?, ?, ?)",
                    (e["id"], e.get("name"), e.get("voc_type"), e.get("description"), e.get("created_at")))
            elif ev == "run":
                conn.execute(
                    "INSERT OR REPLACE INTO runs (id, experiment_id, target_temperature, notes, parameters_json, started_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (e["id"], e.get("experiment_id"), e.get("target_temperature"), e.get("notes"),
                     e.get("parameters_json", "{}"), e.get("started_at")))
            elif ev == "run_end":
                conn.execute("UPDATE runs SET ended_at = ? WHERE id = ?", (e.get("ended_at"), e["id"]))
            elif ev == "readings":
                conn.executemany(
                    "INSERT INTO readings (run_id, timestamp, phase_status, roi_name, raw_ocr_text, parsed_value, unit,"
                    " confidence, is_valid, validation_reason, used_fallback, is_calculated)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [tuple(r) for r in e.get("rows", [])])
            elif ev == "roi_preset":
                conn.execute("INSERT INTO roi_presets (preset_name, config_json, created_at) VALUES (?, ?, ?)",
                             (e.get("name"), e.get("config_json"), e.get("created_at")))
            elif ev == "delete_run":
                conn.execute("DELETE FROM readings WHERE run_id = ?", (e["id"],))
                conn.execute("DELETE FROM runs WHERE id = ?", (e["id"],))
            elif ev == "delete_experiment":
                conn.execute("DELETE FROM readings WHERE run_id IN (SELECT id FROM runs WHERE experiment_id = ?)", (e["id"],))
                conn.execute("DELETE FROM runs WHERE experiment_id = ?", (e["id"],))
                conn.execute("DELETE FROM experiments WHERE id = ?", (e["id"],))
        conn.commit()
    finally:
        conn.close()
    return counts
