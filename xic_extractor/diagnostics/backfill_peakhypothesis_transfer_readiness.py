"""Readiness summary for a reviewed PeakHypothesis backfill transfer slice."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xic_extractor.tabular_io import (
    bool_value,
    format_diagnostic_value,
    optional_int,
    text_value,
    write_tsv,
)

SCHEMA_VERSION = "backfill_peakhypothesis_transfer_readiness"

READINESS_COLUMNS = (
    "schema_version",
    "source_run_id",
    "promotion_row_count",
    "promotion_matrix_write_count",
    "activation_acceptance_status",
    "activation_validation_scope",
    "activation_changed_matrix_cell_count",
    "activation_unexpected_matrix_diff_count",
    "activation_missing_matrix_diff_count",
    "activation_value_mismatch_count",
    "eight_raw_gate_status",
    "full_cohort_artifact_status",
    "full_cohort_metadata_contract_status",
    "full_cohort_matrix_row_count",
    "full_cohort_sample_column_count",
    "full_cohort_review_row_count",
    "full_cohort_cell_row_count",
    "full_cohort_skipped_evidence_row_count",
    "full_cohort_acceptance_gate_status",
    "full_cohort_acceptance_gate_candidate_no_regression_count",
    "full_cohort_acceptance_gate_hypothesis_candidate_review_count",
    "full_cohort_acceptance_gate_blocked_count",
    "full_cohort_acceptance_gate_primary_loser_count",
    "full_cohort_acceptance_gate_duplicate_assigned_count",
    "full_cohort_acceptance_gate_absent_count",
    "full_cohort_winner_remap_status",
    "full_cohort_winner_remap_candidate_count",
    "full_cohort_winner_remap_blocked_count",
    "full_cohort_winner_remap_winner_detected_count",
    "full_cohort_winner_remap_winner_backfilled_count",
    "full_cohort_winner_remap_missing_winner_count",
    "manual_review_scope",
    "full_cohort_peak_shape_review_status",
    "area_generalization_status",
    "readiness_label",
    "production_ready",
    "hard_fail_reasons",
    "remaining_blockers",
    "next_action",
)

_CANONICAL_FULL_COHORT_BASE_METADATA = {
    "output_level": "validation-minimal",
    "backfill_scope": "production-equivalent",
    "audit_evidence_mode": "none",
    "owner_backfill_xic_backend": "raw",
}
_ALIGNMENT_METADATA_SCHEMA_VERSION = "alignment-results"
_CURRENT_MATRIX_VALUE_POLICY = "canonical_signal_profile_selected_area"

_ZERO_MISMATCH_FIELDS = (
    "unexpected_matrix_diff_count",
    "missing_matrix_diff_count",
    "value_mismatch_count",
    "decision_mismatch_count",
    "preflight_mismatch_count",
    "value_delta_mismatch_count",
    "application_summary_mismatch_count",
)


@dataclass(frozen=True)
class TransferReadinessIndex:
    readiness_row: dict[str, str]
    summary: dict[str, Any]


@dataclass(frozen=True)
class TransferReadinessOutputs:
    readiness_tsv: Path
    summary_json: Path


def build_transfer_readiness(
    *,
    promotion_summary: dict[str, Any],
    activation_acceptance_rows: list[dict[str, str]] | tuple[dict[str, str], ...],
    full_cohort_metadata: dict[str, Any],
    full_cohort_counts: dict[str, int],
    full_cohort_acceptance_gate_summary: dict[str, Any] | None = None,
    full_cohort_winner_remap_summary: dict[str, Any] | None = None,
    full_cohort_hypothesis_review_summary: dict[str, Any] | None = None,
    source_run_id: str = "",
    expected_full_cohort_sample_columns: int = 85,
) -> TransferReadinessIndex:
    if len(activation_acceptance_rows) != 1:
        raise ValueError("activation acceptance must contain exactly one row")
    acceptance = activation_acceptance_rows[0]
    promotion_count = _promotion_row_count(promotion_summary)
    matrix_write_count = _promotion_matrix_write_count(promotion_summary)
    eight_raw_failures = _eight_raw_failures(acceptance, matrix_write_count)
    full_cohort_failures = _full_cohort_failures(
        full_cohort_metadata,
        full_cohort_counts,
        expected_sample_columns=expected_full_cohort_sample_columns,
    )
    slice_gate_failures = _full_cohort_acceptance_gate_failures(
        full_cohort_acceptance_gate_summary,
        matrix_write_count=matrix_write_count,
        manual_same_peak_covers_review=_manual_same_peak_covers_review(
            full_cohort_hypothesis_review_summary,
            full_cohort_acceptance_gate_summary,
        ),
    )
    hard_fail_reasons = (
        *eight_raw_failures,
        *full_cohort_failures,
        *slice_gate_failures,
    )
    slice_gate_status = _full_cohort_acceptance_gate_status(
        full_cohort_acceptance_gate_summary
    )
    peak_shape_review_status = _full_cohort_peak_shape_review_status(
        full_cohort_hypothesis_review_summary,
        full_cohort_acceptance_gate_summary,
    )

    readiness_label = "blocked" if hard_fail_reasons else "production_candidate"
    production_ready = "FALSE"
    remaining_blockers = _remaining_blockers(
        hard_fail_reasons,
        slice_gate_status=slice_gate_status,
        peak_shape_review_status=peak_shape_review_status,
    )
    next_action = (
        _hard_fail_next_action(
            full_cohort_acceptance_gate_summary,
            full_cohort_winner_remap_summary,
        )
        if hard_fail_reasons
        else _next_action(
            slice_gate_status,
            peak_shape_review_status=peak_shape_review_status,
        )
    )

    row = {
        "schema_version": SCHEMA_VERSION,
        "source_run_id": source_run_id,
        "promotion_row_count": str(promotion_count),
        "promotion_matrix_write_count": str(matrix_write_count),
        "activation_acceptance_status": _value(acceptance, "acceptance_status"),
        "activation_validation_scope": _value(acceptance, "validation_scope"),
        "activation_changed_matrix_cell_count": _value(
            acceptance,
            "changed_matrix_cell_count",
        ),
        "activation_unexpected_matrix_diff_count": _value(
            acceptance,
            "unexpected_matrix_diff_count",
        ),
        "activation_missing_matrix_diff_count": _value(
            acceptance,
            "missing_matrix_diff_count",
        ),
        "activation_value_mismatch_count": _value(
            acceptance,
            "value_mismatch_count",
        ),
        "eight_raw_gate_status": "fail" if eight_raw_failures else "pass",
        "full_cohort_artifact_status": "fail" if full_cohort_failures else "pass",
        "full_cohort_metadata_contract_status": (
            "fail"
            if "full_cohort_metadata_contract_not_canonical" in full_cohort_failures
            else "pass"
        ),
        "full_cohort_matrix_row_count": str(
            full_cohort_counts.get("matrix_row_count", 0)
        ),
        "full_cohort_sample_column_count": str(
            full_cohort_counts.get("sample_column_count", 0),
        ),
        "full_cohort_review_row_count": str(
            full_cohort_counts.get("review_row_count", 0)
        ),
        "full_cohort_cell_row_count": str(full_cohort_counts.get("cell_row_count", 0)),
        "full_cohort_skipped_evidence_row_count": str(
            full_cohort_counts.get("skipped_evidence_row_count", 0),
        ),
        "full_cohort_acceptance_gate_status": slice_gate_status,
        "full_cohort_acceptance_gate_candidate_no_regression_count": str(
            _slice_gate_count(
                full_cohort_acceptance_gate_summary,
                "candidate_no_regression_count",
            ),
        ),
        "full_cohort_acceptance_gate_hypothesis_candidate_review_count": str(
            _slice_gate_count(
                full_cohort_acceptance_gate_summary,
                "hypothesis_candidate_review_count",
            ),
        ),
        "full_cohort_acceptance_gate_blocked_count": str(
            _slice_gate_count(full_cohort_acceptance_gate_summary, "blocked_count"),
        ),
        "full_cohort_acceptance_gate_primary_loser_count": str(
            _slice_gate_count(
                full_cohort_acceptance_gate_summary, "primary_loser_count"
            ),
        ),
        "full_cohort_acceptance_gate_duplicate_assigned_count": str(
            _slice_gate_count(
                full_cohort_acceptance_gate_summary, "duplicate_assigned_count"
            ),
        ),
        "full_cohort_acceptance_gate_absent_count": str(
            _slice_gate_count(full_cohort_acceptance_gate_summary, "absent_count"),
        ),
        "full_cohort_winner_remap_status": _winner_remap_status(
            full_cohort_winner_remap_summary,
        ),
        "full_cohort_winner_remap_candidate_count": str(
            _winner_remap_count(
                full_cohort_winner_remap_summary,
                "remap_candidate_review_count",
            ),
        ),
        "full_cohort_winner_remap_blocked_count": str(
            _winner_remap_count(full_cohort_winner_remap_summary, "blocked_count"),
        ),
        "full_cohort_winner_remap_winner_detected_count": str(
            _winner_remap_count(
                full_cohort_winner_remap_summary, "winner_detected_count"
            ),
        ),
        "full_cohort_winner_remap_winner_backfilled_count": str(
            _winner_remap_count(
                full_cohort_winner_remap_summary, "winner_backfilled_count"
            ),
        ),
        "full_cohort_winner_remap_missing_winner_count": str(
            _winner_remap_count(
                full_cohort_winner_remap_summary, "missing_winner_count"
            ),
        ),
        "manual_review_scope": "observed_fixture_cohort_top14_standard_cells",
        "full_cohort_peak_shape_review_status": peak_shape_review_status,
        "area_generalization_status": _area_generalization_status(
            peak_shape_review_status,
        ),
        "readiness_label": readiness_label,
        "production_ready": production_ready,
        "hard_fail_reasons": ";".join(hard_fail_reasons),
        "remaining_blockers": remaining_blockers,
        "next_action": next_action,
    }
    return TransferReadinessIndex(
        readiness_row=row,
        summary={key: _json_value(value) for key, value in row.items()},
    )


def write_transfer_readiness_outputs(
    output_dir: Path,
    index: TransferReadinessIndex,
) -> TransferReadinessOutputs:
    output_dir.mkdir(parents=True, exist_ok=True)
    readiness_tsv = output_dir / "backfill_peakhypothesis_transfer_readiness.tsv"
    summary_json = (
        output_dir / "backfill_peakhypothesis_transfer_readiness_summary.json"
    )
    write_tsv(
        readiness_tsv,
        [index.readiness_row],
        READINESS_COLUMNS,
        formatter=format_diagnostic_value,
        lineterminator="\n",
    )
    summary_json.write_text(
        json.dumps(index.summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return TransferReadinessOutputs(
        readiness_tsv=readiness_tsv,
        summary_json=summary_json,
    )


def _promotion_row_count(summary: dict[str, Any]) -> int:
    return _int_value(summary.get("allowlist_row_count"))


def _promotion_matrix_write_count(summary: dict[str, Any]) -> int:
    decision_counts = summary.get("decision_counts")
    if not isinstance(decision_counts, dict):
        return 0
    return _int_value(decision_counts.get("promote_matrix_write"))


def _eight_raw_failures(
    acceptance: dict[str, str],
    matrix_write_count: int,
) -> tuple[str, ...]:
    failures: list[str] = []
    if _value(acceptance, "acceptance_status") != "pass":
        failures.append("fixture_cohort_activation_acceptance_not_passed")
    if (
        _value(acceptance, "validation_scope")
        != "fixture_cohort_current_writer_matrix_diff"
    ):
        failures.append(
            "fixture_cohort_activation_scope_not_current_writer_matrix_diff"
        )
    if bool_value(acceptance.get("canonical_row_identity_ready")) is not True:
        failures.append("fixture_cohort_canonical_row_identity_not_ready")
    activation_decision_count = _int_value(
        acceptance.get("activation_decision_row_count"),
    )
    if activation_decision_count != matrix_write_count:
        failures.append("fixture_cohort_activation_decision_count_mismatch")
    if _int_value(acceptance.get("changed_matrix_cell_count")) != matrix_write_count:
        failures.append("fixture_cohort_changed_matrix_cell_count_mismatch")
    for field in _ZERO_MISMATCH_FIELDS:
        if _int_value(acceptance.get(field)) != 0:
            failures.append(f"fixture_cohort_{field}_not_zero")
    return tuple(failures)


def _full_cohort_failures(
    metadata: dict[str, Any],
    counts: dict[str, int],
    *,
    expected_sample_columns: int,
) -> tuple[str, ...]:
    failures: list[str] = []
    metadata_contract_invalid = any(
        text_value(metadata.get(key)) != expected
        for key, expected in _CANONICAL_FULL_COHORT_BASE_METADATA.items()
    )
    schema_version = text_value(metadata.get("schema_version"))
    has_targeted_istd_anchor = bool(
        text_value(metadata.get("targeted_istd_anchor_identity"))
    )
    has_targeted_istd_snapshot = bool(
        text_value(metadata.get("targeted_istd_target_snapshot_id"))
    )
    matrix_value_policy = text_value(metadata.get("matrix_value_policy"))
    if (
        schema_version != _ALIGNMENT_METADATA_SCHEMA_VERSION
        or matrix_value_policy != _CURRENT_MATRIX_VALUE_POLICY
        or "targeted_istd_anchor_identity" not in metadata
        or "targeted_istd_target_snapshot_id" not in metadata
        or has_targeted_istd_anchor != has_targeted_istd_snapshot
    ):
        metadata_contract_invalid = True
    if metadata_contract_invalid:
        failures.append("full_cohort_metadata_contract_not_canonical")
    if counts.get("sample_column_count", 0) != expected_sample_columns:
        failures.append("full_cohort_sample_column_count_unexpected")
    for key in ("matrix_row_count", "review_row_count", "cell_row_count"):
        if counts.get(key, 0) <= 0:
            failures.append(f"full_cohort_{key}_missing")
    return tuple(failures)


def _full_cohort_acceptance_gate_failures(
    summary: dict[str, Any] | None,
    *,
    matrix_write_count: int,
    manual_same_peak_covers_review: bool = False,
) -> tuple[str, ...]:
    if summary is None:
        return ()
    failures: list[str] = []
    if _text(summary.get("schema_version")) != (
        "backfill_peakhypothesis_acceptance_gate"
    ):
        failures.append("full_cohort_acceptance_gate_schema_mismatch")
    if _int_value(summary.get("promotion_row_count")) != matrix_write_count:
        failures.append("full_cohort_acceptance_gate_promotion_count_mismatch")
    gate_status = _text(summary.get("gate_status"))
    review_count = _int_value(summary.get("hypothesis_candidate_review_count"))
    candidate_count = _int_value(summary.get("candidate_no_regression_count"))
    manual_review_closes_partial = (
        gate_status == "partial"
        and manual_same_peak_covers_review
        and _int_value(summary.get("blocked_count")) == 0
        and candidate_count + review_count == matrix_write_count
    )
    if gate_status != "pass" and not manual_review_closes_partial:
        failures.append("full_cohort_slice_specific_no_regression_failed")
    if _int_value(summary.get("blocked_count")) != 0:
        failures.append("full_cohort_acceptance_gate_blocked_rows_present")
    if gate_status == "pass" and (
        _int_value(summary.get("candidate_no_regression_count")) != matrix_write_count
    ):
        failures.append("full_cohort_acceptance_gate_candidate_count_mismatch")
    return tuple(failures)


def _full_cohort_acceptance_gate_status(summary: dict[str, Any] | None) -> str:
    if summary is None:
        return "not_assessed"
    return _text(summary.get("gate_status")) or "unknown"


def _remaining_blockers(
    hard_fail_reasons: tuple[str, ...],
    *,
    slice_gate_status: str,
    peak_shape_review_status: str,
) -> str:
    if hard_fail_reasons:
        return ""
    blockers = ["explicit_product_transfer_decision_required"]
    if slice_gate_status == "not_assessed":
        blockers.append("full_cohort_slice_specific_no_regression_not_assessed")
    if peak_shape_review_status == "manual_same_peak_supported_all_review_candidates":
        if slice_gate_status == "partial":
            blockers.append("full_cohort_consolidation_policy_not_productized")
    else:
        blockers.append("full_cohort_peak_shape_not_manually_confirmed")
    return ";".join(blockers)


def _next_action(slice_gate_status: str, *, peak_shape_review_status: str) -> str:
    if (
        slice_gate_status == "partial"
        and peak_shape_review_status
        == "manual_same_peak_supported_all_review_candidates"
    ):
        return (
            "define_full_cohort_consolidation_policy_for_same_peak_"
            "non_primary_candidates"
        )
    if slice_gate_status == "not_assessed":
        return (
            "request_explicit_product_transfer_decision_or_build_"
            "full_cohort_acceptance_gate"
        )
    return (
        "request_explicit_product_transfer_decision_or_manual_full_cohort_shape_review"
    )


def _full_cohort_peak_shape_review_status(
    manual_review_summary: dict[str, Any] | None,
    slice_gate_summary: dict[str, Any] | None,
) -> str:
    if _manual_same_peak_covers_review(manual_review_summary, slice_gate_summary):
        return "manual_same_peak_supported_all_review_candidates"
    if manual_review_summary is not None:
        return "manual_review_incomplete_or_conflicting"
    return "not_assessed"


def _area_generalization_status(peak_shape_review_status: str) -> str:
    if peak_shape_review_status == "manual_same_peak_supported_all_review_candidates":
        return "manual_same_peak_reviewed_area_policy_pending"
    return "not_generalized_to_full_cohort"


def _manual_same_peak_covers_review(
    manual_review_summary: dict[str, Any] | None,
    slice_gate_summary: dict[str, Any] | None,
) -> bool:
    if manual_review_summary is None or slice_gate_summary is None:
        return False
    if _text(manual_review_summary.get("schema_version")) != (
        "backfill_peakhypothesis_full_cohort_manual_verdict"
    ):
        return False
    review_count = _int_value(
        slice_gate_summary.get("hypothesis_candidate_review_count"),
    )
    return (
        review_count > 0
        and _int_value(manual_review_summary.get("reviewed_candidate_count"))
        == review_count
        and _int_value(manual_review_summary.get("same_peak_supported_count"))
        == review_count
        and _int_value(manual_review_summary.get("same_peak_conflict_count")) == 0
        and _int_value(manual_review_summary.get("unreviewed_candidate_count")) == 0
    )


def _hard_fail_next_action(
    full_cohort_acceptance_gate_summary: dict[str, Any] | None,
    winner_remap_summary: dict[str, Any] | None,
) -> str:
    if (
        _slice_gate_count(
            full_cohort_acceptance_gate_summary,
            "hypothesis_candidate_review_count",
        )
        > 0
    ):
        return "review_full_cohort_hypothesis_candidates_before_product_transfer"
    if _winner_remap_count(winner_remap_summary, "remap_candidate_review_count") > 0:
        return "review_full_cohort_winner_remap_candidates"
    return "review_transfer_readiness_failures"


def _slice_gate_count(summary: dict[str, Any] | None, key: str) -> int:
    if summary is None:
        return 0
    return _int_value(summary.get(key))


def _winner_remap_status(summary: dict[str, Any] | None) -> str:
    if summary is None:
        return "not_assessed"
    return _text(summary.get("remap_gate_status")) or "unknown"


def _winner_remap_count(summary: dict[str, Any] | None, key: str) -> int:
    if summary is None:
        return 0
    return _int_value(summary.get(key))


def _int_value(value: object) -> int:
    parsed = optional_int(value)
    return parsed if parsed is not None else 0


def _text(value: object) -> str:
    return text_value(value)


def _value(row: dict[str, str], key: str) -> str:
    return text_value(row.get(key, ""))


def _json_value(value: str) -> str | int | bool:
    if value in {"TRUE", "FALSE"}:
        return value == "TRUE"
    parsed = optional_int(value)
    if parsed is not None and str(parsed) == value:
        return parsed
    return value
