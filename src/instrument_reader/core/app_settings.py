"""Small settings in <data>/settings.json; each feature keeps its own section there."""
import json
from pathlib import Path


def load_section(settings_file: str | Path, key: str) -> dict:
    """The section ``key`` of settings.json ({} if the file or section is missing or broken)."""
    try:
        data = json.loads(Path(settings_file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    section = data.get(key) if isinstance(data, dict) else None
    return section if isinstance(section, dict) else {}


def save_section(settings_file: str | Path, key: str, values: dict) -> None:
    """Replaces the section ``key`` in settings.json, keeping every other setting there."""
    path = Path(settings_file)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data[key] = values
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
