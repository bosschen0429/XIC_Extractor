from __future__ import annotations

import csv
from pathlib import Path

from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.output.detection import MissingTargetedProductProjectionError


def _read_diagnostics(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _read_long_results(
    config: ExtractionConfig, targets: list[Target]
) -> list[dict[str, str]]:
    long_path = config.output_csv.with_name("xic_results_long.csv")
    if long_path.exists():
        return _read_results(long_path)
    raise MissingTargetedProductProjectionError(
        "xic_results_long.csv is required for targeted product workbook "
        f"generation; missing {long_path}"
    )


def _read_results(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))
