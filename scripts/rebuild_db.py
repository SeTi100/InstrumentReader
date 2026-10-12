"""Builds a fresh database from the append-only journal.

    python scripts/rebuild_db.py                      # Journal im Datenordner -> instrument_reader_rebuilt_<Zeit>.db
    python scripts/rebuild_db.py --journal D:\\journal --out C:\\temp\\neu.db

The existing database is never touched; check the result, then replace it yourself
(app closed) or open it via INSTRUMENT_READER_DATA_DIR.
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from instrument_reader.core import data_safety  # noqa: E402
from instrument_reader.core.journal import journal_files, rebuild_database  # noqa: E402


def main() -> int:
    folder = data_safety.data_dir()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--journal", type=Path, default=folder / "journal")
    ap.add_argument("--out", type=Path,
                    default=folder / f"instrument_reader_rebuilt_{datetime.now():%Y-%m-%d_%H%M%S}.db")
    args = ap.parse_args()

    files = journal_files(args.journal)
    if not files:
        print(f"Kein Journal in {args.journal} gefunden.")
        return 1
    counts = rebuild_database(args.journal, args.out)
    print(f"{len(files)} Journal-Dateien ({files[0].stem} bis {files[-1].stem}) -> {args.out}")
    for ev, n in sorted(counts.items()):
        print(f"  {ev}: {n}")
    stats = data_safety.db_stats(args.out)
    print(f"Ergebnis: {stats['experiments']} Experimente, {stats['runs']} Runs, {stats['readings']} Messwerte")
    return 0


if __name__ == "__main__":
    sys.exit(main())
