from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.output import csv_writers
from xic_extractor.output.messages import DiagnosticRecord
from xic_extractor.output.sheet_identification import identification_evidence_rows
from xic_extractor.output.workbook_builder import write_workbook_from_rows


class RunOutputLike(Protocol):
    @property
    def file_results(self) -> Sequence[csv_writers.FileResultLike]: ...

    @property
    def diagnostics(self) -> Sequence[DiagnosticRecord]: ...


def write_excel_from_run_output(
    config: ExtractionConfig,
    targets: list[Target],
    run_output: RunOutputLike,
    *,
    output_path: Path,
) -> Path:
    """Write an xlsx workbook directly from in-memory extraction output."""
    rows = _run_output_to_long_rows(run_output, targets)
    diagnostics = _diagnostics_to_rows(run_output.diagnostics)
    return write_workbook_from_rows(
        config,
        targets,
        rows,
        diagnostics=diagnostics,
        output_path=output_path,
        identification_rows=identification_evidence_rows(run_output, targets),
    )


def _run_output_to_long_rows(
    run_output: RunOutputLike, targets: list[Target]
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for file_result in run_output.file_results:
        rows.extend(csv_writers._long_output_rows(file_result, targets))
    return rows


def _diagnostics_to_rows(
    diagnostics: Sequence[DiagnosticRecord],
) -> list[dict[str, str]]:
    return [
        {
            "SampleName": record.sample_name,
            "Target": record.target_label,
            "Issue": record.issue,
            "Reason": record.reason,
        }
        for record in diagnostics
    ]
