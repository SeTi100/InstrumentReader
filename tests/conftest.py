import pytest


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    """Tests never touch the real data folder (database, journal, backups, recordings)."""
    monkeypatch.setenv("INSTRUMENT_READER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("INSTRUMENT_READER_BACKUP_MIRROR", raising=False)
    # Never look at or write into an Excel the user has open, and never grab F9/F10.
    monkeypatch.setenv("INSTRUMENT_READER_EXCEL", "0")
