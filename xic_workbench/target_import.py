"""Explicit editable target-table exchange; semantic admission belongs to Targets."""

from pathlib import Path

from xic_extractor.configuration.csv_io import read_target_draft_rows
from xic_extractor.configuration.models import ConfigError
from xic_extractor.configuration.targeted_method_files import load_target_table


def import_targets(path: Path) -> list[dict[str, str]]:
    source = Path(path)
    if source.suffix.lower() in {".csv", ".tsv"}:
        return read_target_draft_rows(source)
    if source.suffix.lower() == ".xlsx":
        return load_target_table(source)
    raise ConfigError(f"{source}: 請選擇 XLSX、CSV 或 TSV 目標表；不支援 .xls。")
