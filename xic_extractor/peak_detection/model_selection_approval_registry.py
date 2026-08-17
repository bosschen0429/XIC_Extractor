from __future__ import annotations

import csv
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from xic_extractor.peak_detection.model_selection import (
    ExpectedDiffApprovalRecord,
    ExpectedDiffFinalLabel,
    ExpectedDiffReviewerVerdict,
    ExpectedDiffValidationTier,
    MatrixValueImpact,
)

EXPECTED_DIFF_APPROVAL_REGISTRY_HEADERS = (
    "stable_row_id",
    "sample_name",
    "target_label",
    "legacy_selected_candidate_id",
    "successor_selected_candidate_id",
    "final_label",
    "reviewer_verdict",
    "validation_tier",
    "public_outputs_touched",
    "matrix_value_impact",
    "evidence_sources",
    "evidence_summary",
    "reviewer_role",
)

_FINAL_LABELS = frozenset({"expected_diff", "blocked_diff", "inconclusive"})
_REVIEWER_VERDICTS = frozenset({"approved", "blocked", "inconclusive"})
_VALIDATION_TIERS = frozenset(
    {
        "synthetic_fixture",
        "targeted_benchmark",
        "8raw",
        "manual_eic_ms2_review",
        "not_validated",
    }
)
_MATRIX_VALUE_IMPACTS = frozenset(
    {"none", "area_value_changed", "presence_changed", "not_assessed"}
)
_MATRIX_AFFECTING_OUTPUTS = frozenset(
    {
        "area",
        "boundary",
        "final_matrix",
        "final matrix",
        "final matrix value",
        "integration",
        "selected area",
        "selected boundary",
        "selected rt",
        "workbook",
        "xlsx",
    }
)


def load_expected_diff_approval_registry(
    path: Path,
) -> dict[str, ExpectedDiffApprovalRecord]:
    if not path.is_file():
        raise ValueError(f"{path}: approval registry file not found")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        _validate_headers(path, reader.fieldnames)
        approvals: dict[str, ExpectedDiffApprovalRecord] = {}
        for row_number, row in enumerate(reader, start=2):
            approval = _approval_from_row(path, row_number, row)
            if approval.stable_row_id in approvals:
                raise ValueError(
                    f"{path}:{row_number}: duplicate stable_row_id "
                    f"{approval.stable_row_id!r}"
                )
            approvals[approval.stable_row_id] = approval
    return approvals


def _validate_headers(path: Path, fieldnames: Sequence[str] | None) -> None:
    if fieldnames is None:
        raise ValueError(f"{path}: expected TSV header row")
    fieldname_set = set(fieldnames)
    missing = [
        header
        for header in EXPECTED_DIFF_APPROVAL_REGISTRY_HEADERS
        if header not in fieldname_set
    ]
    if missing:
        raise ValueError(
            f"{path}: missing approval registry columns: {', '.join(missing)}"
        )


def _approval_from_row(
    path: Path,
    row_number: int,
    row: dict[str, str],
) -> ExpectedDiffApprovalRecord:
    final_label = _required_enum(
        path,
        row_number,
        row,
        "final_label",
        _FINAL_LABELS,
    )
    reviewer_verdict = _required_enum(
        path,
        row_number,
        row,
        "reviewer_verdict",
        _REVIEWER_VERDICTS,
    )
    validation_tier = _required_enum(
        path,
        row_number,
        row,
        "validation_tier",
        _VALIDATION_TIERS,
    )
    matrix_value_impact = _required_enum(
        path,
        row_number,
        row,
        "matrix_value_impact",
        _MATRIX_VALUE_IMPACTS,
    )
    public_outputs_touched = _split_multi(
        _required(path, row_number, row, "public_outputs_touched")
    )
    evidence_sources = _split_multi(
        _required(path, row_number, row, "evidence_sources")
    )
    _validate_approved_registry_row(
        path,
        row_number,
        final_label=final_label,
        reviewer_verdict=reviewer_verdict,
        validation_tier=validation_tier,
        matrix_value_impact=matrix_value_impact,
        public_outputs_touched=public_outputs_touched,
        evidence_sources=evidence_sources,
        evidence_summary=_required(path, row_number, row, "evidence_summary"),
    )
    return ExpectedDiffApprovalRecord(
        stable_row_id=_required(path, row_number, row, "stable_row_id"),
        sample_name=_required(path, row_number, row, "sample_name"),
        target_label=_required(path, row_number, row, "target_label"),
        legacy_selected_candidate_id=_required(
            path,
            row_number,
            row,
            "legacy_selected_candidate_id",
        ),
        successor_selected_candidate_id=_required(
            path,
            row_number,
            row,
            "successor_selected_candidate_id",
        ),
        public_outputs_touched=public_outputs_touched,
        matrix_value_impact=cast(MatrixValueImpact, matrix_value_impact),
        evidence_sources=evidence_sources,
        evidence_summary=_required(path, row_number, row, "evidence_summary"),
        validation_tier=cast(ExpectedDiffValidationTier, validation_tier),
        reviewer_role=_required(path, row_number, row, "reviewer_role"),
        reviewer_verdict=cast(ExpectedDiffReviewerVerdict, reviewer_verdict),
        final_label=cast(ExpectedDiffFinalLabel, final_label),
    )


def _validate_approved_registry_row(
    path: Path,
    row_number: int,
    *,
    final_label: str,
    reviewer_verdict: str,
    validation_tier: str,
    matrix_value_impact: str,
    public_outputs_touched: tuple[str, ...],
    evidence_sources: tuple[str, ...],
    evidence_summary: str,
) -> None:
    if final_label != "expected_diff" or reviewer_verdict != "approved":
        raise ValueError(
            f"{path}:{row_number}: durable registry rows must be approved "
            "expected_diff records"
        )
    if validation_tier == "not_validated":
        raise ValueError(f"{path}:{row_number}: approval registry row is not validated")
    if not evidence_sources or not evidence_summary.strip():
        raise ValueError(f"{path}:{row_number}: approval registry row lacks evidence")
    if _touches_matrix_output(public_outputs_touched):
        if matrix_value_impact == "not_assessed":
            raise ValueError(
                f"{path}:{row_number}: matrix-affecting approval must assess "
                "matrix impact"
            )
        if validation_tier == "synthetic_fixture":
            raise ValueError(
                f"{path}:{row_number}: matrix-affecting approval requires real-data "
                "validation, not synthetic_fixture"
            )


def _required(
    path: Path,
    row_number: int,
    row: dict[str, str],
    column: str,
) -> str:
    value = (row.get(column) or "").strip()
    if not value:
        raise ValueError(f"{path}:{row_number}: {column} is required")
    return value


def _required_enum(
    path: Path,
    row_number: int,
    row: dict[str, str],
    column: str,
    allowed: frozenset[str],
) -> str:
    value = _required(path, row_number, row, column)
    if value not in allowed:
        raise ValueError(
            f"{path}:{row_number}: {column} must be one of {', '.join(sorted(allowed))}"
        )
    return value


def _split_multi(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(";") if part.strip())


def _touches_matrix_output(public_outputs_touched: tuple[str, ...]) -> bool:
    normalized = {
        value.strip().lower().replace("-", "_")
        for value in public_outputs_touched
    }
    return any(value in _MATRIX_AFFECTING_OUTPUTS for value in normalized)
