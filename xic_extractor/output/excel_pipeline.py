from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.output import csv_writers
from xic_extractor.output.messages import DiagnosticRecord
from xic_extractor.output.review_report import write_review_report
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
    score_breakdown = (
        _run_output_to_score_breakdown_rows(config, run_output)
        if config.emit_score_breakdown
        else []
    )
    return write_workbook_from_rows(
        config,
        targets,
        rows,
        diagnostics=diagnostics,
        score_breakdown=score_breakdown,
        output_path=output_path,
        report_writer=write_review_report,
        require_projection=True,
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


def _run_output_to_score_breakdown_rows(
    config: ExtractionConfig,
    run_output: RunOutputLike,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for file_result in run_output.file_results:
        rows.extend(
            csv_writers._score_breakdown_rows(
                file_result,
                count_no_ms2_as_detected=config.count_no_ms2_as_detected,
            )
        )
    return rows
