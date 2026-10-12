import gc
import json
import os
import sqlite3
import stat
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from instrument_reader.core import data_safety
from instrument_reader.core.journal import Journal, rebuild_database
from instrument_reader.gui.db_writer import DatabaseWriter


def _fill(writer: DatabaseWriter, n=5):
    exp = writer.create_experiment("Exp", "VOC", "desc")
    run = writer.create_run(exp, notes="n", parameters={"Temperatur": 40})
    for i in range(n):
        writer.insert_readings([{"roi_name": "Waage", "parsed_value": 100 + i, "unit": "g"}], timestamp=1_700_000_000 + i)
    writer.save_roi_preset("preset", '{"rois": []}')
    writer.end_run()
    return exp, run


def test_default_data_dir_is_outside_the_repo(monkeypatch, tmp_path):
    monkeypatch.delenv(data_safety.ENV_DATA_DIR)
    assert data_safety.data_dir() == Path.home() / "InstrumentReader-Daten"
    monkeypatch.setenv(data_safety.ENV_DATA_DIR, str(tmp_path / "d"))
    d = data_safety.ensure_data_dir()
    assert all((d / s).is_dir() for s in ("backups", "journal", "recordings"))


def test_refuses_data_dir_inside_git_repo(monkeypatch, tmp_path):
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    monkeypatch.setenv(data_safety.ENV_DATA_DIR, str(tmp_path / "repo" / "data"))
    with pytest.raises(data_safety.DataDirInRepoError):
        data_safety.ensure_data_dir()


def test_writer_uses_data_dir_by_default():
    w = DatabaseWriter()
    assert Path(w.db.db_path) == data_safety.db_path()


def test_legacy_db_is_copied_never_moved(tmp_path):
    legacy_dir = tmp_path / "repo"
    legacy_dir.mkdir()
    old = DatabaseWriter(str(legacy_dir / "instrument_reader.db"))
    _fill(old)
    target = tmp_path / "data" / "instrument_reader.db"
    target.parent.mkdir()
    assert data_safety.migrate_legacy_db(legacy_dir, target) == legacy_dir / "instrument_reader.db"
    assert (legacy_dir / "instrument_reader.db").exists()
    assert data_safety.db_stats(target)["readings"] == 5
    # Never overwrites an existing target.
    assert data_safety.migrate_legacy_db(legacy_dir, target) is None


def test_journal_rebuilds_the_database(tmp_path):
    w = DatabaseWriter(str(tmp_path / "instrument_reader.db"))
    exp, run = _fill(w, n=7)
    exp2, run2 = _fill(w, n=2)
    w.db.delete_run(run2)
    rebuilt = tmp_path / "rebuilt.db"
    counts = rebuild_database(tmp_path / "journal", rebuilt)
    assert counts["readings"] == 9 and counts["delete_run"] == 1

    def dump(path):
        with sqlite3.connect(path) as c:
            return (
                c.execute("SELECT id, name, voc_type, description, created_at FROM experiments ORDER BY id").fetchall(),
                c.execute("SELECT id, experiment_id, target_temperature, notes, parameters_json, started_at, ended_at"
                          " FROM runs ORDER BY id").fetchall(),
                c.execute("SELECT run_id, timestamp, phase_status, roi_name, parsed_value, unit FROM readings ORDER BY id").fetchall(),
                c.execute("SELECT preset_name, config_json FROM roi_presets ORDER BY id").fetchall(),
            )

    assert dump(rebuilt) == dump(w.db.db_path)
    with pytest.raises(FileExistsError):
        rebuild_database(tmp_path / "journal", rebuilt)


def test_journal_seals_past_days_and_skips_torn_lines(tmp_path):
    folder = tmp_path / "journal"
    folder.mkdir()
    old = folder / "2020-01-01.jsonl"
    old.write_text('{"ev": "experiment", "id": 1, "name": "a"}\n{"ev": "experi', encoding="utf-8")
    Journal(folder).append("experiment", id=2, name="b")
    assert not os.stat(old).st_mode & stat.S_IWRITE
    counts = rebuild_database(folder, tmp_path / "r.db")
    assert counts == {"experiment": 2}
    os.chmod(old, stat.S_IWRITE | stat.S_IREAD)


def test_backup_mirror(tmp_path, monkeypatch):
    w = DatabaseWriter(str(tmp_path / "instrument_reader.db"))
    _fill(w)
    mirror = tmp_path / "mirror"
    monkeypatch.setenv(data_safety.ENV_BACKUP_MIRROR, str(mirror))
    b = data_safety.backup_database(w.db.db_path, "run")
    assert (mirror / b.name).exists()
    # An unreachable mirror never breaks the backup itself.
    monkeypatch.setenv(data_safety.ENV_BACKUP_MIRROR, str(b))  # a file, not a folder
    assert data_safety.backup_database(w.db.db_path, "run").exists()


def test_backup_rotation(tmp_path):
    folder = tmp_path / "backups"
    folder.mkdir()
    start = datetime(2026, 1, 1, 8, 0)
    for i in range(48 * 40):  # 40 days, every 30 min
        t = start + timedelta(minutes=30 * i)
        (folder / f"instrument_reader_{t:%Y-%m-%d_%H%M%S}_auto.db").write_bytes(b"x")
    now = start + timedelta(days=40)
    data_safety.rotate_backups(folder, now)
    kept = sorted(data_safety._backup_time(p) for p in folder.glob("*.db"))
    newest = now - timedelta(minutes=30)
    assert kept[-1] == newest
    assert sum(1 for t in kept if newest - t < timedelta(hours=24)) == 48   # last day complete
    days = {t.date() for t in kept if now - t <= timedelta(days=30)}
    assert len(days) >= 30                                                  # one per day for 30 days
    older = [t for t in kept if now - t > timedelta(days=30)]
    assert len(older) == len({t.isocalendar()[:2] for t in older})          # then one per week
    assert len(kept) < 48 + 31 + 3

def test_backup_is_a_consistent_copy(tmp_path):
    w = DatabaseWriter(str(tmp_path / "instrument_reader.db"))
    _fill(w, n=3)
    b = data_safety.backup_database(w.db.db_path, "test")
    assert data_safety.db_stats(b)["readings"] == 3
    assert data_safety.backup_database(tmp_path / "missing.db") is None


def test_state_detects_missing_or_shrunken_db(tmp_path):
    db = tmp_path / "instrument_reader.db"
    w = DatabaseWriter(str(db))
    _fill(w, n=4)
    assert data_safety.check_state(db) is None  # no state yet
    data_safety.save_state(db)
    assert data_safety.check_state(db) is None
    w.db.delete_run(1)
    assert "readings: 4 → 0" in data_safety.check_state(db)
    del w
    gc.collect()  # the app's own sqlite connections close on garbage collection
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db) + suffix)
        if p.exists():
            p.unlink()
    assert "fehlt" in data_safety.check_state(db)


def test_restore_backup_keeps_the_replaced_db(tmp_path):
    db = tmp_path / "instrument_reader.db"
    w = DatabaseWriter(str(db))
    _fill(w, n=6)
    backup = data_safety.backup_database(db, "test")
    w.db.delete_run(1)
    del w
    gc.collect()  # restore runs at start-up, before the app opens the database
    kept = data_safety.restore_backup(backup, db)
    assert data_safety.db_stats(db)["readings"] == 6
    assert kept is not None and data_safety.db_stats(kept)["readings"] == 0


def test_settings_file_sets_mirror(tmp_path):
    db = tmp_path / "instrument_reader.db"
    (tmp_path / "settings.json").write_text(json.dumps({"backup_mirror": str(tmp_path / "m")}), encoding="utf-8")
    assert data_safety.backup_mirror(db) == tmp_path / "m"
