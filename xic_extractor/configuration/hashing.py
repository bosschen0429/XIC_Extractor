import csv
import hashlib
from pathlib import Path

from xic_extractor.tabular_io import render_delimited_rows


def compute_config_hash(targets_csv: Path, settings_csv: Path) -> str:
    """SHA-256[:8] hex of targets.csv followed by settings.csv byte content."""
    digest = hashlib.sha256()
    digest.update(targets_csv.read_bytes())
    digest.update(b"\x00")
    digest.update(settings_csv.read_bytes())
    return digest.hexdigest()[:8]


def compute_target_config_hash(targets_csv: Path) -> str:
    """SHA-256[:8] hex of target CSV byte content only."""
    return hashlib.sha256(targets_csv.read_bytes()).hexdigest()[:8]


def _compute_config_hash(
    targets_path: Path,
    settings_path: Path,
    *,
    settings_overrides: dict[str, str] | None,
) -> str:
    if not targets_path.exists():
        return ""
    if not settings_overrides:
        return compute_config_hash(targets_path, settings_path)

    digest = hashlib.sha256()
    digest.update(targets_path.read_bytes())
    digest.update(b"\x00")
    digest.update(_settings_csv_bytes_with_overrides(settings_path, settings_overrides))
    return digest.hexdigest()[:8]


def _settings_csv_bytes_with_overrides(
    settings_path: Path, settings_overrides: dict[str, str]
) -> bytes:
    with settings_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    overridden_keys: set[str] = set()
    for row in rows:
        key = str(row.get("key", "")).strip()
        if key in settings_overrides:
            row["value"] = settings_overrides[key]
            overridden_keys.add(key)

    for key, value in settings_overrides.items():
        if key in overridden_keys:
            continue
        row = {field: "" for field in fieldnames}
        row["key"] = key
        row["value"] = value
        if "description" in row:
            row["description"] = key
        rows.append(row)

    return render_delimited_rows(
        rows,
        fieldnames,
        formatter=_format_hash_csv_value,
    ).encode("utf-8-sig")


def _format_hash_csv_value(value: object) -> str:
    if value is None:
        return ""
    return str(value)
