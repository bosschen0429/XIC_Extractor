"""
Compatibility wrapper for converting XIC CSV outputs into a formatted workbook.

Workbook implementation lives under :mod:`xic_extractor.output`.
"""

from __future__ import annotations

from pathlib import Path
from typing import overload

from xic_extractor.configuration.loader import load_config
from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.output.review_queue_model import (
    _review_queue_rows as _review_queue_rows,
)
from xic_extractor.output.review_report import write_review_report
from xic_extractor.output.sheet_diagnostics import (
    _build_diagnostics_sheet as _build_diagnostics_sheet,
)
from xic_extractor.output.sheet_metadata import (
    _build_metadata_sheet as _build_metadata_sheet,
)
from xic_extractor.output.sheet_overview import (
    _build_overview_sheet as _build_overview_sheet,
)
from xic_extractor.output.sheet_results import _build_data_sheet as _build_data_sheet
from xic_extractor.output.sheet_review_queue import (
    _build_review_queue_sheet as _build_review_queue_sheet,
)
from xic_extractor.output.sheet_score_breakdown import (
    _build_score_breakdown_sheet as _build_score_breakdown_sheet,
)
from xic_extractor.output.sheet_summary import (
    _build_summary_sheet as _build_summary_sheet,
)
from xic_extractor.output.sheet_targets import (
    _build_targets_sheet as _build_targets_sheet,
)
from xic_extractor.output.workbook_builder import run_from_config
from xic_extractor.output.workbook_inputs import (
    _read_diagnostics as _read_diagnostics,
)
from xic_extractor.output.workbook_inputs import (
    _read_long_results as _read_long_results,
)
from xic_extractor.output.workbook_inputs import (
    _read_results as _read_results,
)
from xic_extractor.output.workbook_inputs import (
    _read_score_breakdown as _read_score_breakdown,
)
from xic_extractor.output.workbook_inputs import (
    _wide_to_long_rows as _wide_to_long_rows,
)
from xic_extractor.output.workbook_styles import (
    _apply_sheet_role_styles as _apply_sheet_role_styles,
)

__all__ = [
    "_apply_sheet_role_styles",
    "_build_data_sheet",
    "_build_diagnostics_sheet",
    "_build_metadata_sheet",
    "_build_overview_sheet",
    "_build_review_queue_sheet",
    "_build_score_breakdown_sheet",
    "_build_summary_sheet",
    "_build_targets_sheet",
    "_read_diagnostics",
    "_read_long_results",
    "_read_results",
    "_read_score_breakdown",
    "_review_queue_rows",
    "_wide_to_long_rows",
    "main",
    "run",
]


@overload
def run(base_or_config: Path) -> Path: ...


@overload
def run(base_or_config: ExtractionConfig, targets: list[Target]) -> Path: ...


def run(
    base_or_config: Path | ExtractionConfig,
    targets: list[Target] | None = None,
) -> Path:
    if isinstance(base_or_config, Path):
        config, loaded_targets = load_config(base_or_config / "config")
        return run(config, loaded_targets)
    if targets is None:
        raise TypeError("targets are required when run() receives ExtractionConfig")
    return _run_with_config(base_or_config, targets)


def _run_with_config(config: ExtractionConfig, targets: list[Target]) -> Path:
    return run_from_config(config, targets, report_writer=write_review_report)


def main() -> None:
    base_dir = Path(__file__).parent.parent
    run(base_dir)


if __name__ == "__main__":
    main()
