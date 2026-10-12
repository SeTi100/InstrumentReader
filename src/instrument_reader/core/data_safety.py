"""Where measurement data lives and how it is protected.

Background: on 2026-10-12 the measurement database lived in the git working folder,
was untracked and ignored, and a later ``git switch`` + ``git pull`` silently overwrote
and then deleted it. Therefore:

- All data (database, append-only journal, backups, recordings) lives in a data folder
  outside any git repository: ``%USERPROFILE%\\InstrumentReader-Daten`` by default,
  ``INSTRUMENT_READER_DATA_DIR`` to change it. The app refuses a data folder inside a repo.
- A database found in the start folder (the old location) is *copied* over once, never moved.
- The database is backed up with SQLite's online backup (consistent while it is written)
  into ``<data>/backups``, rotated, and optionally copied to a mirror folder
  (``INSTRUMENT_READER_BACKUP_MIRROR`` or ``backup_mirror`` in ``<data>/settings.json``).
- ``<data>/state.json`` remembers how much data there was; a database that is missing or
  smaller at start is reported instead of silently starting empty.
"""
import json
import logging
import os
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

ENV_DATA_DIR = "INSTRUMENT_READER_DATA_DIR"
ENV_BACKUP_MIRROR = "INSTRUMENT_READER_BACKUP_MIRROR"
DB_NAME = "instrument_reader.db"
BACKUP_INTERVAL_S = 30 * 60
KEEP_RECENT_BACKUPS = 48     # all of the newest 48 (one day at 30 min)
KEEP_DAILY_DAYS = 30         # then the newest per day for 30 days, then the newest per week


class DataDirInRepoError(RuntimeError):
    pass


def data_dir() -> Path:
    env = os.environ.get(ENV_DATA_DIR, "").strip()
    return Path(env).expanduser() if env else Path.home() / "InstrumentReader-Daten"


def find_git_root(path: Path) -> Path | None:
    """The enclosing git working folder of path, or None."""
    p = Path(path).resolve()
    for d in (p, *p.parents):
        if (d / ".git").exists():
            return d
    return None


def ensure_data_dir() -> Path:
    d = data_dir()
    repo = find_git_root(d)
    if repo is not None:
        raise DataDirInRepoError(
            f"Der Datenordner {d} liegt im Git-Ordner {repo}. Git-Befehle können dort "
            f"Messdaten überschreiben oder löschen. Bitte {ENV_DATA_DIR} auf einen Ordner "
            f"außerhalb von Git setzen."
        )
    for sub in ("", "backups", "journal", "recordings"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return data_dir() / DB_NAME


def recordings_dir() -> Path:
    return data_dir() / "recordings"


def journal_dir_for(db: str | Path) -> Path:
    return Path(db).resolve().parent / "journal"


def backups_dir_for(db: str | Path) -> Path:
    return Path(db).resolve().parent / "backups"


# --- copying -------------------------------------------------------------------------

def copy_database(src: str | Path, dst: str | Path) -> None:
    """Consistent copy of a (possibly open, WAL-mode) SQLite database."""
    tmp = Path(str(dst) + ".part")
    if tmp.exists():
        tmp.unlink()
    s, d = sqlite3.connect(str(src)), sqlite3.connect(str(tmp))
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()
    os.replace(tmp, dst)


def migrate_legacy_db(legacy_dir: str | Path, target: str | Path) -> Path | None:
    """Copies <legacy_dir>/instrument_reader.db to target if target does not exist yet.

    Never moves or deletes the old file. Returns the copied source, or None.
    """
    legacy = Path(legacy_dir) / DB_NAME
    target = Path(target)
    if not legacy.exists() or target.exists() or legacy.resolve() == target.resolve():
        return None
    copy_database(legacy, target)
    log.warning("Datenbank aus %s nach %s kopiert (Original bleibt liegen).", legacy, target)
    return legacy


# --- backups -------------------------------------------------------------------------

def _settings(d: Path) -> dict:
    try:
        return json.loads((d / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def backup_mirror(db: str | Path) -> Path | None:
    env = os.environ.get(ENV_BACKUP_MIRROR, "").strip()
    value = env or _settings(Path(db).resolve().parent).get("backup_mirror", "")
    return Path(value).expanduser() if value else None


def backup_database(db: str | Path, reason: str = "auto", now: datetime | None = None) -> Path | None:
    """Writes a timestamped backup next to the database, rotates old ones and copies
    the new one to the mirror folder if configured. Returns the backup, None if the
    database does not exist. Mirror problems are logged, never raised."""
    db = Path(db)
    if not db.exists():
        return None
    now = now or datetime.now()
    folder = backups_dir_for(db)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{db.stem}_{now:%Y-%m-%d_%H%M%S}_{reason}.db"
    copy_database(db, target)
    rotate_backups(folder, now)
    mirror = backup_mirror(db)
    if mirror is not None:
        try:
            mirror.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, mirror / target.name)
            rotate_backups(mirror, now)
        except OSError as e:
            log.warning("Backup-Spiegel %s nicht erreichbar: %s", mirror, e)
    return target


def _backup_time(p: Path) -> datetime | None:
    parts = p.stem.split("_")
    for i in range(len(parts) - 1):
        try:
            return datetime.strptime(f"{parts[i]}_{parts[i + 1]}", "%Y-%m-%d_%H%M%S")
        except ValueError:
            continue
    return None


def rotate_backups(folder: Path, now: datetime | None = None) -> list[Path]:
    """Keeps the newest KEEP_RECENT_BACKUPS, then the newest per day for KEEP_DAILY_DAYS,
    then the newest per ISO week. Returns the deleted files."""
    now = now or datetime.now()
    dated = sorted(((t, p) for p in Path(folder).glob("*.db") if (t := _backup_time(p))), reverse=True)
    keep, days, weeks = set(), set(), set()
    for i, (t, p) in enumerate(dated):
        if i < KEEP_RECENT_BACKUPS:
            keep.add(p)
        elif now - t <= timedelta(days=KEEP_DAILY_DAYS):
            if t.date() not in days:
                keep.add(p)
        elif t.isocalendar()[:2] not in weeks:
            keep.add(p)
        days.add(t.date())
        weeks.add(t.isocalendar()[:2])
    removed = []
    for _, p in dated:
        if p not in keep:
            try:
                p.unlink()
                removed.append(p)
            except OSError:
                pass
    return removed


def latest_backup(db: str | Path) -> Path | None:
    dated = sorted(((t, p) for p in backups_dir_for(db).glob("*.db") if (t := _backup_time(p))), reverse=True)
    return dated[0][1] if dated else None


# --- noticing loss -------------------------------------------------------------------

def db_stats(db: str | Path) -> dict:
    stats = {"experiments": 0, "runs": 0, "readings": 0, "size": 0}
    db = Path(db)
    if not db.exists():
        return stats
    stats["size"] = db.stat().st_size
    try:
        conn = sqlite3.connect(str(db))
    except sqlite3.DatabaseError:
        return stats
    try:
        for table in ("experiments", "runs", "readings"):
            try:
                stats[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.DatabaseError:
                pass
    finally:
        conn.close()  # an open handle would block restoring or deleting the file on Windows
    return stats


def _state_file(db: str | Path) -> Path:
    return Path(db).resolve().parent / "state.json"


def save_state(db: str | Path) -> None:
    state = db_stats(db)
    state["saved_at"] = datetime.now().isoformat(timespec="seconds")
    try:
        _state_file(db).write_text(json.dumps(state, indent=1), encoding="utf-8")
    except OSError as e:
        log.warning("state.json nicht schreibbar: %s", e)


def check_state(db: str | Path) -> str | None:
    """A description of the problem if the database is missing or holds less data than
    when the app last saw it, else None."""
    try:
        last = json.loads(_state_file(db).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    now = db_stats(db)
    if not Path(db).exists():
        return (f"Die Datenbank {db} fehlt. Beim letzten Mal ({last.get('saved_at', '?')}) "
                f"enthielt sie {last.get('runs', 0)} Runs und {last.get('readings', 0)} Messwerte.")
    lost = [k for k in ("experiments", "runs", "readings") if now[k] < last.get(k, 0)]
    if lost:
        details = ", ".join(f"{k}: {last.get(k, 0)} → {now[k]}" for k in lost)
        return (f"Die Datenbank {db} enthält weniger Daten als beim letzten Mal "
                f"({last.get('saved_at', '?')}): {details}.")
    return None


def restore_backup(backup: str | Path, db: str | Path) -> Path | None:
    """Puts a backup in place of the database. The current database (with its WAL files)
    is kept as <name>.replaced_<time>, never deleted. Returns that file, if any."""
    db = Path(db)
    kept = None
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    if db.exists():
        kept = db.with_name(f"{db.name}.replaced_{stamp}")
        copy_database(db, kept)
    for suffix in ("-wal", "-shm"):
        side = Path(str(db) + suffix)
        if side.exists():
            side.replace(Path(str(kept or db) + suffix + f".{stamp}"))
    copy_database(backup, db)
    return kept
