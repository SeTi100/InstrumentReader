import sqlite3
from datetime import datetime

import pytest
from PySide6.QtWidgets import QMessageBox

from instrument_reader.core.database import Database
from instrument_reader.core.journal import rebuild_database
from instrument_reader.gui.db_writer import DatabaseWriter


def _reading(v=1.0):
    return [{"roi_name": "Waage", "parsed_value": v, "unit": "g"}]


def test_end_run_stops_logging_and_records_status(tmp_path):
    w = DatabaseWriter(str(tmp_path / "db.db"))
    exp = w.create_experiment("E", "", "")
    run = w.create_run(exp)
    w.insert_readings(_reading(1), timestamp=1_700_000_000)
    w.insert_readings(_reading(2), timestamp=1_700_000_060)
    w.end_run("stop")
    w.insert_readings(_reading(3), timestamp=1_700_000_120)  # after the stop: not saved

    r = w.db.get_run(run)
    assert r["status"] == "completed" and r["end_reason"] == "stop" and r["ended_at"]
    assert r["data_started_at"] == datetime.fromtimestamp(1_700_000_000).isoformat()
    assert r["data_ended_at"] == datetime.fromtimestamp(1_700_000_060).isoformat()
    with sqlite3.connect(w.db.db_path) as c:
        assert c.execute("SELECT COUNT(*) FROM readings").fetchone()[0] == 2
    assert [e["event"] for e in w.db.get_run_events(run)] == ["start", "stop"]


def test_times_are_local_iso(tmp_path):
    w = DatabaseWriter(str(tmp_path / "db.db"))
    exp = w.create_experiment("E", "", "")
    run = w.create_run(exp)
    w.end_run()
    r = w.db.get_run(run)
    started, ended = datetime.fromisoformat(r["started_at"]), datetime.fromisoformat(r["ended_at"])
    assert "T" in r["started_at"]
    assert abs((datetime.now() - started).total_seconds()) < 60  # local, not UTC
    assert 0 <= (ended - started).total_seconds() < 60


def test_new_run_ends_previous_with_reason(tmp_path):
    w = DatabaseWriter(str(tmp_path / "db.db"))
    exp = w.create_experiment("E", "", "")
    r1 = w.create_run(exp)
    r2 = w.create_run(exp)
    assert w.db.get_run(r1)["end_reason"] == "new_run"
    assert w.db.get_run(r2)["status"] == "running"


def test_end_experiment_closes_it_and_resume_reopens_run(tmp_path):
    w = DatabaseWriter(str(tmp_path / "db.db"))
    exp = w.create_experiment("E", "", "")
    run = w.create_run(exp)
    w.end_experiment(exp)
    assert w.current_run_id is None
    assert w.db.get_experiment(exp)["status"] == "closed"
    assert w.db.get_run(run)["end_reason"] == "experiment_end"

    w.reopen_experiment(exp)
    w.set_active_run(run)
    r = w.db.get_run(run)
    assert r["status"] == "running" and r["ended_at"] is None
    assert w.db.get_experiment(exp)["status"] == "open"
    assert [e["event"] for e in w.db.get_run_events(run)] == ["start", "stop", "resume"]


def test_switching_runs_ends_the_current_one(tmp_path):
    w = DatabaseWriter(str(tmp_path / "db.db"))
    exp = w.create_experiment("E", "", "")
    r1 = w.create_run(exp)
    r2 = w.create_run(exp)
    w.set_active_run(r1)
    assert w.db.get_run(r2)["end_reason"] == "switched"
    assert w.db.get_run(r1)["status"] == "running"


def test_crash_leaves_running_runs_that_start_up_aborts(tmp_path):
    db = str(tmp_path / "db.db")
    w = DatabaseWriter(db)
    exp = w.create_experiment("E", "", "")
    run = w.create_run(exp)
    w.insert_readings(_reading(), timestamp=1_700_000_000)
    del w  # "crash": no end_run
    aborted = Database(db).abort_open_runs()
    r = Database(db).get_run(run)
    assert aborted == [run]
    assert r["status"] == "aborted" and r["end_reason"] == "crash"
    assert r["ended_at"] == datetime.fromtimestamp(1_700_000_000).isoformat()  # last reading
    assert Database(db).abort_open_runs() == []


def test_legacy_rows_are_upgraded(tmp_path):
    """Databases from before this change: UTC CURRENT_TIMESTAMP times, no status."""
    db = tmp_path / "old.db"
    with sqlite3.connect(db) as c:
        c.executescript(Database.SCHEMA)
        c.execute("INSERT INTO experiments (id, name, created_at) VALUES (1, 'E', '2026-10-12 08:00:00')")
        c.execute("INSERT INTO runs (id, experiment_id, started_at, ended_at) VALUES (1, 1, '2026-10-12 08:00:00', '2026-10-12T10:30:00')")
        c.execute("INSERT INTO runs (id, experiment_id, started_at) VALUES (2, 1, '2026-10-12 09:00:00')")
        c.execute("INSERT INTO readings (run_id, timestamp, phase_status, roi_name) VALUES (2, '2026-10-12T11:15:00', 'S', 'W')")
    d = Database(str(db))
    r1, r2 = d.get_run(1), d.get_run(2)
    assert r1["status"] == "completed"
    assert r2["status"] == "aborted" and r2["ended_at"] == "2026-10-12T11:15:00"
    local = datetime.fromisoformat("2026-10-12T08:00:00+00:00").astimezone().replace(tzinfo=None).isoformat()
    assert r1["started_at"] == local
    assert d.get_experiment(1)["status"] == "open"
    Database(str(db))  # idempotent
    assert Database(str(db)).get_run(1)["started_at"] == local


def test_journal_rebuild_keeps_life_cycle(tmp_path):
    w = DatabaseWriter(str(tmp_path / "db.db"))
    exp = w.create_experiment("E", "", "")
    r1 = w.create_run(exp)
    w.insert_readings(_reading(1), timestamp=1_700_000_000)
    w.end_run("stop")
    w.set_active_run(r1)
    w.insert_readings(_reading(2), timestamp=1_700_000_500)
    w.end_experiment(exp)
    rebuild_database(tmp_path / "journal", tmp_path / "rebuilt.db")

    def dump(path):
        with sqlite3.connect(path) as c:
            return (c.execute("SELECT id, status, end_reason, started_at, ended_at, data_started_at, data_ended_at FROM runs").fetchall(),
                    c.execute("SELECT run_id, time, event, reason FROM run_events ORDER BY id").fetchall(),
                    c.execute("SELECT id, status, ended_at FROM experiments").fetchall())

    assert dump(tmp_path / "rebuilt.db") == dump(w.db.db_path)


def test_main_window_end_run_button(qtbot, monkeypatch):
    from instrument_reader.gui.main_window import MainWindow

    monkeypatch.setattr("instrument_reader.gui.control_panel.list_cameras", lambda: [])
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    win = MainWindow()
    qtbot.addWidget(win)
    cp = win.control_panel
    assert not cp.end_run_btn.isEnabled() and not cp.end_exp_btn.isEnabled()
    assert "NICHT" in cp.logging_hint.text()

    exp = win.db_writer.create_experiment("E", "", "")
    win.current_experiment_id, win.current_experiment_name = exp, "E"
    win.db_writer.create_run(exp)
    win._update_run_ui()
    assert cp.end_run_btn.isEnabled() and "gespeichert" in cp.logging_hint.text()

    cp.end_run_btn.click()
    assert win.db_writer.current_run_id is None
    assert win.current_experiment_id == exp  # experiment stays open for a new run
    assert not cp.end_run_btn.isEnabled() and cp.end_exp_btn.isEnabled()
    assert win.db_writer.current_phase == "IDLE"

    cp.end_exp_btn.click()
    assert win.current_experiment_id is None
    assert win.db_writer.db.get_experiment(exp)["status"] == "closed"


def test_end_run_can_be_cancelled(qtbot, monkeypatch):
    from instrument_reader.gui.main_window import MainWindow

    monkeypatch.setattr("instrument_reader.gui.control_panel.list_cameras", lambda: [])
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    win = MainWindow()
    qtbot.addWidget(win)
    exp = win.db_writer.create_experiment("E", "", "")
    win.current_experiment_id = exp
    run = win.db_writer.create_run(exp)
    win.end_current_run()
    assert win.db_writer.current_run_id == run
