"""Post-activation acceptance for backfill PeakHypothesis promotion slices."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xic_extractor.alignment.shared_peak_identity_explanation.schema import (
    ACTIVATION_APPLICATION_SCHEMA_VERSION,
    ACTIVATION_DECISION_SCHEMA_VERSION,
    ACTIVATION_VALUE_DELTA_SCHEMA_VERSION,
)
from xic_extractor.diagnostics import (
    backfill_peakhypothesis_activation_bridge,
    backfill_peakhypothesis_promotion,
)
from xic_extractor.diagnostics.diagnostic_io import (
    format_diagnostic_value,
    numeric_equal,
    text_value,
    write_tsv,
)
from xic_extractor.diagnostics.matrix_identity_projection import (
    matrix_value_diffs,
    matrix_values_by_identity,
)

SCHEMA_VERSION = "backfill_peakhypothesis_activation_acceptance_v1"

ACCEPTANCE_COLUMNS = (
    "schema_version",
    "source_run_id",
    "validation_scope",
    "promotion_row_count",
    "activation_decision_row_count",
    "preflight_row_count",
    "preflight_needs_activation_count",
    "value_delta_row_count",
    "changed_matrix_cell_count",
    "expected_written_count",
    "application_matrix_cells_written",
    "application_matrix_cells_blanked",
    "application_matrix_value_conflict_cells",
    "canonical_row_identity_ready",
    "unexpected_matrix_diff_count",
    "missing_matrix_diff_count",
    "value_mismatch_count",
    "decision_mismatch_count",
    "preflight_mismatch_count",
    "value_delta_mismatch_count",
    "application_summary_mismatch_count",
    "matrix_contract_changed",
    "product_behavior_changed",
    "acceptance_status",
    "hard_fail_reasons",
    "next_action",
)

MATRIX_DIFF_COLUMNS = (
    "schema_version",
    "peak_hypothesis_id",
    "feature_family_id",
    "sample_stem",
    "expected_promotion",
    "before_value",
    "after_value",
    "matrix_diff_effect",
    "value_matches_promotion",
    "activation_delta_present",
    "diff_status",
)


@dataclass(frozen=True)
class ActivationAcceptanceIndex:
    acceptance_row: dict[str, str]
    matrix_diff_rows: tuple[dict[str, str], ...]
    summary: dict[str, Any]


@dataclass(frozen=True)
class ActivationAcceptanceOutputs:
    acceptance_tsv: Path
    matrix_diff_tsv: Path
    summary_json: Path


def build_activation_acceptance(
    *,
    promotion_rows: Sequence[Mapping[str, Any]],
    activation_decision_rows: Sequence[Mapping[str, Any]],
    preflight_rows: Sequence[Mapping[str, Any]],
    application_summary_rows: Sequence[Mapping[str, Any]],
    value_delta_rows: Sequence[Mapping[str, Any]],
    input_matrix_rows: Sequence[Mapping[str, Any]],
    input_identity_rows: Sequence[Mapping[str, Any]],
    output_matrix_rows: Sequence[Mapping[str, Any]],
    output_identity_rows: Sequence[Mapping[str, Any]],
    source_run_id: str = "",
    validation_scope: str = "fixture_cohort_current_writer_matrix_diff",
) -> ActivationAcceptanceIndex:
    _validate_input_schemas(
        promotion_rows=promotion_rows,
        activation_decision_rows=activation_decision_rows,
        preflight_rows=preflight_rows,
        application_summary_rows=application_summary_rows,
        value_delta_rows=value_delta_rows,
    )
    promoted = tuple(
        row
        for row in promotion_rows
        if _value(row, "promotion_decision") == "promote_matrix_write"
    )
    expected = {_promotion_key(row): row for row in promoted}
    if len(expected) != len(promoted):
        raise ValueError("duplicate promoted PeakHypothesis/sample keys")

    decisions = _keyed_rows(
        activation_decision_rows,
        key_func=_decision_key,
        label="activation_decision",
    )
    preflight = _keyed_rows(
        preflight_rows,
        key_func=_preflight_key,
        label="activation_preflight",
    )
    value_delta = _keyed_rows(
        value_delta_rows,
        key_func=_delta_key,
        label="activation_value_delta",
    )
    before_values = matrix_values_by_identity(
        matrix_rows=input_matrix_rows,
        matrix_identity_rows=input_identity_rows,
    )
    after_values = matrix_values_by_identity(
        matrix_rows=output_matrix_rows,
        matrix_identity_rows=output_identity_rows,
    )
    matrix_diffs = matrix_value_diffs(before_values, after_values)
    application_summary = _single_summary_row(application_summary_rows)

    matrix_diff_rows = tuple(
        _matrix_diff_row(
            key,
            before_value=before,
            after_value=after,
            expected=expected,
            value_delta=value_delta,
        )
        for key, before, after in matrix_diffs
    )
    counts = _acceptance_counts(
        expected=expected,
        decisions=decisions,
        preflight=preflight,
        value_delta=value_delta,
        matrix_diff_rows=matrix_diff_rows,
        application_summary=application_summary,
    )
    hard_fail_reasons = _hard_fail_reasons(counts)
    acceptance_row = {
        "schema_version": SCHEMA_VERSION,
        "source_run_id": source_run_id,
        "validation_scope": validation_scope,
        "promotion_row_count": str(len(promoted)),
        "activation_decision_row_count": str(len(activation_decision_rows)),
        "preflight_row_count": str(len(preflight_rows)),
        "preflight_needs_activation_count": str(
            sum(
                1
                for row in preflight_rows
                if _value(row, "preflight_status") == "needs_activation"
            )
        ),
        "value_delta_row_count": str(len(value_delta_rows)),
        "changed_matrix_cell_count": str(len(matrix_diff_rows)),
        "expected_written_count": str(len(expected)),
        "application_matrix_cells_written": _value(
            application_summary,
            "matrix_cells_written",
        ),
        "application_matrix_cells_blanked": _value(
            application_summary,
            "matrix_cells_blanked",
        ),
        "application_matrix_value_conflict_cells": _value(
            application_summary,
            "matrix_value_conflict_cells",
        ),
        "canonical_row_identity_ready": _value(
            application_summary,
            "canonical_row_identity_ready",
        ),
        "unexpected_matrix_diff_count": str(counts["unexpected_matrix_diff_count"]),
        "missing_matrix_diff_count": str(counts["missing_matrix_diff_count"]),
        "value_mismatch_count": str(counts["value_mismatch_count"]),
        "decision_mismatch_count": str(counts["decision_mismatch_count"]),
        "preflight_mismatch_count": str(counts["preflight_mismatch_count"]),
        "value_delta_mismatch_count": str(counts["value_delta_mismatch_count"]),
        "application_summary_mismatch_count": str(
            counts["application_summary_mismatch_count"]
        ),
        "matrix_contract_changed": "FALSE",
        "product_behavior_changed": "FALSE",
        "acceptance_status": "pass" if not hard_fail_reasons else "fail",
        "hard_fail_reasons": ";".join(hard_fail_reasons),
        "next_action": (
            _ready_next_action(validation_scope)
            if not hard_fail_reasons
            else "review_activation_matrix_diff_failures"
        ),
    }
    summary: dict[str, Any] = {
        key: _json_value(value) for key, value in acceptance_row.items()
    }
    return ActivationAcceptanceIndex(
        acceptance_row=acceptance_row,
        matrix_diff_rows=matrix_diff_rows,
        summary=summary,
    )


def write_activation_acceptance_outputs(
    output_dir: Path,
    index: ActivationAcceptanceIndex,
) -> ActivationAcceptanceOutputs:
    output_dir.mkdir(parents=True, exist_ok=True)
    acceptance_tsv = output_dir / "backfill_peakhypothesis_activation_acceptance.tsv"
    matrix_diff_tsv = output_dir / "backfill_peakhypothesis_activation_matrix_diff.tsv"
    summary_json = (
        output_dir / "backfill_peakhypothesis_activation_acceptance_summary.json"
    )
    write_tsv(
        acceptance_tsv,
        [index.acceptance_row],
        ACCEPTANCE_COLUMNS,
        formatter=format_diagnostic_value,
        lineterminator="\n",
    )
    write_tsv(
        matrix_diff_tsv,
        index.matrix_diff_rows,
        MATRIX_DIFF_COLUMNS,
        formatter=format_diagnostic_value,
        lineterminator="\n",
    )
    summary_json.write_text(
        json.dumps(index.summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return ActivationAcceptanceOutputs(
        acceptance_tsv=acceptance_tsv,
        matrix_diff_tsv=matrix_diff_tsv,
        summary_json=summary_json,
    )


def _validate_input_schemas(
    *,
    promotion_rows: Sequence[Mapping[str, Any]],
    activation_decision_rows: Sequence[Mapping[str, Any]],
    preflight_rows: Sequence[Mapping[str, Any]],
    application_summary_rows: Sequence[Mapping[str, Any]],
    value_delta_rows: Sequence[Mapping[str, Any]],
) -> None:
    _require_schema(
        promotion_rows,
        field="schema_version",
        expected=backfill_peakhypothesis_promotion.SCHEMA_VERSION,
        label="promotion row",
    )
    _require_schema(
        activation_decision_rows,
        field="activation_schema_version",
        expected=ACTIVATION_DECISION_SCHEMA_VERSION,
        label="activation decision row",
    )
    _require_schema(
        preflight_rows,
        field="schema_version",
        expected=backfill_peakhypothesis_activation_bridge.SCHEMA_VERSION,
        label="activation preflight row",
    )
    _require_schema(
        application_summary_rows,
        field="activation_application_schema_version",
        expected=ACTIVATION_APPLICATION_SCHEMA_VERSION,
        label="activation application summary row",
    )
    _require_schema(
        value_delta_rows,
        field="activation_value_delta_schema_version",
        expected=ACTIVATION_VALUE_DELTA_SCHEMA_VERSION,
        label="activation value delta row",
    )


def _require_schema(
    rows: Sequence[Mapping[str, Any]],
    *,
    field: str,
    expected: str,
    label: str,
) -> None:
    for index, row in enumerate(rows, start=1):
        actual = _value(row, field)
        if actual != expected:
            raise ValueError(
                f"{label} schema_version mismatch at row {index}: "
                f"expected {expected!r}, got {actual!r}",
            )


def _acceptance_counts(
    *,
    expected: Mapping[tuple[str, str], Mapping[str, Any]],
    decisions: Mapping[tuple[str, str], Mapping[str, Any]],
    preflight: Mapping[tuple[str, str], Mapping[str, Any]],
    value_delta: Mapping[tuple[str, str], Mapping[str, Any]],
    matrix_diff_rows: Sequence[Mapping[str, str]],
    application_summary: Mapping[str, Any],
) -> dict[str, int]:
    expected_keys = set(expected)
    changed_keys = {
        (_value(row, "peak_hypothesis_id"), _value(row, "sample_stem"))
        for row in matrix_diff_rows
    }
    counts = {
        "unexpected_matrix_diff_count": len(changed_keys - expected_keys),
        "missing_matrix_diff_count": len(expected_keys - changed_keys),
        "value_mismatch_count": sum(
            1
            for row in matrix_diff_rows
            if _value(row, "value_matches_promotion") != "TRUE"
        ),
        "decision_mismatch_count": sum(
            1 for key in expected_keys if not _decision_matches(decisions.get(key))
        )
        + len(set(decisions) - expected_keys),
        "preflight_mismatch_count": sum(
            1 for key in expected_keys if not _preflight_matches(preflight.get(key))
        )
        + len(set(preflight) - expected_keys),
        "value_delta_mismatch_count": sum(
            1
            for key, row in expected.items()
            if not _value_delta_matches(
                value_delta.get(key),
                expected_value=_value(row, "projected_matrix_value"),
            )
        )
        + len(set(value_delta) - expected_keys),
        "application_summary_mismatch_count": _application_summary_mismatch_count(
            application_summary,
            expected_count=len(expected_keys),
        ),
    }
    return counts


def _hard_fail_reasons(counts: Mapping[str, int]) -> tuple[str, ...]:
    reasons: list[str] = []
    for key, reason in (
        ("unexpected_matrix_diff_count", "unexpected_matrix_diff"),
        ("missing_matrix_diff_count", "missing_expected_matrix_diff"),
        ("value_mismatch_count", "matrix_value_mismatch"),
        ("decision_mismatch_count", "activation_decision_mismatch"),
        ("preflight_mismatch_count", "activation_preflight_mismatch"),
        ("value_delta_mismatch_count", "activation_value_delta_mismatch"),
        (
            "application_summary_mismatch_count",
            "activation_application_summary_mismatch",
        ),
    ):
        if counts[key]:
            reasons.append(reason)
    return tuple(reasons)


def _matrix_diff_row(
    key: tuple[str, str],
    *,
    before_value: str,
    after_value: str,
    expected: Mapping[tuple[str, str], Mapping[str, Any]],
    value_delta: Mapping[tuple[str, str], Mapping[str, Any]],
) -> dict[str, str]:
    expected_row = expected.get(key)
    expected_value = _value(expected_row or {}, "projected_matrix_value")
    matches = numeric_equal(after_value, expected_value)
    delta_present = key in value_delta
    return {
        "schema_version": SCHEMA_VERSION,
        "peak_hypothesis_id": key[0],
        "feature_family_id": _value(expected_row or {}, "feature_family_id") or key[0],
        "sample_stem": key[1],
        "expected_promotion": "TRUE" if expected_row is not None else "FALSE",
        "before_value": before_value,
        "after_value": after_value,
        "matrix_diff_effect": _diff_effect(before_value, after_value),
        "value_matches_promotion": str(matches).upper(),
        "activation_delta_present": str(delta_present).upper(),
        "diff_status": "expected_written" if expected_row is not None else "unexpected",
    }


def _keyed_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    key_func: Callable[[Mapping[str, Any]], tuple[str, str]],
    label: str,
) -> dict[tuple[str, str], Mapping[str, Any]]:
    keyed: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in rows:
        key = key_func(row)
        if key in keyed:
            raise ValueError(f"duplicate {label} key: {key}")
        keyed[key] = row
    return keyed


def _decision_matches(row: Mapping[str, Any] | None) -> bool:
    if row is None:
        return False
    return all(
        (
            _value(row, "activation_status") == "auto_activate",
            _value(row, "activation_unit_scope") == "peak_hypothesis",
            _value(row, "contract_rule_id")
            == "machine_observed_sufficient_positive_identity",
        )
    )


def _preflight_matches(row: Mapping[str, Any] | None) -> bool:
    if row is None:
        return False
    return all(
        (
            _value(row, "preflight_status") == "needs_activation",
            _value(row, "bridge_action") == "emit_activation_decision",
        )
    )


def _value_delta_matches(
    row: Mapping[str, Any] | None,
    *,
    expected_value: str,
) -> bool:
    if row is None:
        return False
    return all(
        (
            _value(row, "matrix_value_kind") == "backfill_activation",
            _value(row, "matrix_value_effect") == "written",
            _value(row, "value_changed") == "TRUE",
            _value(row, "original_matrix_value") == "",
            numeric_equal(_value(row, "activated_matrix_value"), expected_value),
        )
    )


def _application_summary_mismatch_count(
    row: Mapping[str, Any],
    *,
    expected_count: int,
) -> int:
    mismatches = 0
    checks = (
        _value(row, "application_status") == "applied",
        _value(row, "canonical_row_identity_ready") == "TRUE",
        _int_value(row, "decision_rows_total") == expected_count,
        _int_value(row, "matrix_cells_written") == expected_count,
        _int_value(row, "matrix_cells_blanked") == 0,
        _int_value(row, "matrix_value_conflict_cells") == 0,
        _application_row_count_matches(row),
    )
    for ok in checks:
        if not ok:
            mismatches += 1
    return mismatches


def _application_row_count_matches(row: Mapping[str, Any]) -> bool:
    input_rows = _int_value(row, "input_matrix_rows")
    output_rows = _int_value(row, "output_matrix_rows")
    if input_rows == output_rows:
        return True
    added = max(0, _int_value(row, "families_added_to_matrix"))
    removed = max(0, _int_value(row, "families_removed_from_matrix"))
    return output_rows == input_rows + added - removed


def _ready_next_action(validation_scope: str) -> str:
    if validation_scope.endswith("_current_writer_matrix_diff"):
        prefix = validation_scope[: -len("_current_writer_matrix_diff")]
        if prefix:
            return f"ready_for_{prefix}_reviewed_activation_acceptance"
    return "ready_for_reviewed_activation_acceptance"


def _single_summary_row(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    if len(rows) != 1:
        raise ValueError("activation application summary must contain exactly one row")
    return rows[0]


def _promotion_key(row: Mapping[str, Any]) -> tuple[str, str]:
    return (_value(row, "peak_hypothesis_id"), _value(row, "sample_stem"))


def _decision_key(row: Mapping[str, Any]) -> tuple[str, str]:
    return (_value(row, "peak_hypothesis_id"), _value(row, "sample_id"))


def _preflight_key(row: Mapping[str, Any]) -> tuple[str, str]:
    return (_value(row, "peak_hypothesis_id"), _value(row, "sample_stem"))


def _delta_key(row: Mapping[str, Any]) -> tuple[str, str]:
    return (_value(row, "peak_hypothesis_id"), _value(row, "sample_id"))


def _diff_effect(before_value: str, after_value: str) -> str:
    if before_value and not after_value:
        return "blanked"
    if after_value and not before_value:
        return "written"
    return "changed"


def _int_value(row: Mapping[str, Any], column: str) -> int:
    try:
        return int(_value(row, column))
    except ValueError:
        return -1


def _value(row: Mapping[str, Any], column: str) -> str:
    return text_value(row.get(column, ""))


def _json_value(value: str) -> str | int | bool:
    if value == "TRUE":
        return True
    if value == "FALSE":
        return False
    try:
        return int(value)
    except ValueError:
        return value
