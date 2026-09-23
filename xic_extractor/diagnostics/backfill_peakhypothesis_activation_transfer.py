"""Transfer normal-peak FULL COHORT backfill decisions into promotion rows.

This module is intentionally a bridge artifact. It does not apply activation or
write matrices; it rewrites the activation key from the reviewed source
PeakHypothesis id to the matched FULL COHORT PeakHypothesis id, then emits rows that
the existing activation bridge can consume.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xic_extractor.diagnostics import (
    backfill_peakhypothesis_activation_trial,
    backfill_peakhypothesis_normal_peak_decision,
    backfill_peakhypothesis_promotion,
)
from xic_extractor.diagnostics.diagnostic_io import (
    bool_value,
    format_diagnostic_value,
    read_tsv_required,
    text_value,
    write_tsv,
)

SCHEMA_VERSION = "backfill_peakhypothesis_activation_transfer"
_GAUSSIAN15_AREA_SOURCE = "gaussian15_positive_asls_residual"
_TRANSFER_REASON = "normal_peak_same_peak_transfer_activation"
_SOURCE_ID_AUTHORITY = "audit_only_not_activation_key"
_ACTIVATION_KEY_AUTHORITY = "full_cohort_matched_peak_hypothesis_id"

TRANSFER_COLUMNS = (
    "schema_version",
    "source_run_id",
    "source_peak_hypothesis_id",
    "activation_peak_hypothesis_id",
    "sample_stem",
    "source_id_authority",
    "activation_key_authority",
    "full_cohort_cell_status",
    "full_cohort_consolidation_state",
    "manual_same_peak_verdict",
    "same_peak_verdict",
    "same_peak_verdict_source",
    "normal_peak_decision",
    "normal_peak_backfill_required",
    "trial_action",
    "current_matrix_written",
    "current_matrix_value",
    "projected_matrix_value",
    "projected_matrix_value_source",
    "projected_matrix_value_source_field",
    "source_artifact_schema_version",
    "source_artifact_sha256",
    "source_row_sha256",
    "source_provenance_detail",
    "transfer_action",
    "transfer_blockers",
)


@dataclass(frozen=True)
class ActivationTransferIndex:
    promotion_rows: tuple[dict[str, str], ...]
    transfer_rows: tuple[dict[str, str], ...]
    summary: dict[str, Any]


@dataclass(frozen=True)
class ActivationTransferOutputs:
    promotion_cells_tsv: Path
    transfer_tsv: Path
    summary_json: Path


def read_normal_peak_decision_rows(path: Path) -> tuple[dict[str, str], ...]:
    return read_tsv_required(
        path,
        backfill_peakhypothesis_normal_peak_decision.DECISION_COLUMNS,
    )


def read_activation_trial_rows(path: Path) -> tuple[dict[str, str], ...]:
    return read_tsv_required(
        path,
        backfill_peakhypothesis_activation_trial.TRIAL_COLUMNS,
    )


def build_activation_transfer_index(
    *,
    normal_peak_decision_rows: Sequence[Mapping[str, Any]],
    activation_trial_rows: Sequence[Mapping[str, Any]],
    source_artifact_sha256: str,
    source_run_id: str = "",
) -> ActivationTransferIndex:
    _validate_sha256_hex("source_artifact_sha256", source_artifact_sha256)
    trial_by_key = _trial_by_key(activation_trial_rows)
    promotion_rows: list[dict[str, str]] = []
    transfer_rows: list[dict[str, str]] = []
    for normal_row in normal_peak_decision_rows:
        trial_row = trial_by_key.get(_transfer_key(normal_row), {})
        blockers = _transfer_blockers(normal_row, trial_row)
        transfer_row = _transfer_row(
            normal_row,
            trial_row=trial_row,
            blockers=blockers,
            source_artifact_sha256=source_artifact_sha256,
            source_run_id=source_run_id,
        )
        transfer_rows.append(transfer_row)
        if blockers:
            continue
        promotion_rows.append(
            _promotion_row(
                normal_row,
                trial_row=trial_row,
                source_artifact_sha256=source_artifact_sha256,
                source_run_id=source_run_id,
            ),
        )
    return ActivationTransferIndex(
        promotion_rows=tuple(promotion_rows),
        transfer_rows=tuple(transfer_rows),
        summary=_summary(
            normal_peak_decision_rows=normal_peak_decision_rows,
            activation_trial_rows=activation_trial_rows,
            promotion_rows=promotion_rows,
            transfer_rows=transfer_rows,
            source_artifact_sha256=source_artifact_sha256,
            source_run_id=source_run_id,
        ),
    )


def write_activation_transfer_outputs(
    output_dir: Path,
    index: ActivationTransferIndex,
) -> ActivationTransferOutputs:
    output_dir.mkdir(parents=True, exist_ok=True)
    promotion_tsv = (
        output_dir / "backfill_peakhypothesis_full_cohort_transfer_promotion_cells.tsv"
    )
    transfer_tsv = output_dir / "backfill_peakhypothesis_activation_transfer.tsv"
    summary_json = (
        output_dir / "backfill_peakhypothesis_activation_transfer_summary.json"
    )
    write_tsv(
        promotion_tsv,
        index.promotion_rows,
        backfill_peakhypothesis_promotion.PROMOTION_COLUMNS,
        formatter=format_diagnostic_value,
        lineterminator="\n",
    )
    write_tsv(
        transfer_tsv,
        index.transfer_rows,
        TRANSFER_COLUMNS,
        formatter=format_diagnostic_value,
        lineterminator="\n",
    )
    summary_json.write_text(
        json.dumps(index.summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return ActivationTransferOutputs(
        promotion_cells_tsv=promotion_tsv,
        transfer_tsv=transfer_tsv,
        summary_json=summary_json,
    )


def _trial_by_key(
    rows: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str, str], Mapping[str, Any]]:
    keyed: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    for row in rows:
        key = (
            _value(row, "source_peak_hypothesis_id"),
            _value(row, "sample_stem"),
            _value(row, "full_cohort_matched_peak_hypothesis_id"),
        )
        if key in keyed:
            raise ValueError(f"duplicate activation trial key: {key}")
        keyed[key] = row
    return keyed


def _transfer_key(row: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        _value(row, "peak_hypothesis_id"),
        _value(row, "sample_stem"),
        _value(row, "full_cohort_matched_peak_hypothesis_id"),
    )


def _transfer_blockers(
    normal_row: Mapping[str, Any],
    trial_row: Mapping[str, Any],
) -> tuple[str, ...]:
    blockers: list[str] = []
    if _value(normal_row, "normal_peak_decision") != "require_backfill":
        blockers.append("normal_peak_decision_not_required")
    if bool_value(normal_row.get("normal_peak_backfill_required")) is not True:
        blockers.append("normal_peak_backfill_not_required")
    if _value(normal_row, "normal_peak_decision_blockers"):
        blockers.append("normal_peak_decision_blockers_present")
    if _same_peak_verdict(normal_row) != "same_peak_supported":
        blockers.append("same_peak_conflict")
    if not _value(normal_row, "full_cohort_matched_peak_hypothesis_id"):
        blockers.append("missing_full_cohort_peak_hypothesis_id")
    if _value(normal_row, "full_cohort_cell_status") not in {"detected", "backfilled"}:
        blockers.append("full_cohort_cell_status_not_detected_or_rescued")
    area_source = _projected_matrix_value_source(normal_row)
    if area_source != _GAUSSIAN15_AREA_SOURCE:
        blockers.append("normal_peak_quantitation_area_source_not_gaussian15")
    if not _positive_number(_projected_matrix_value(normal_row)):
        blockers.append("normal_peak_quantitation_area_not_positive")
    if not trial_row:
        blockers.append("activation_trial_missing")
        return tuple(_dedupe(blockers))
    trial_blockers = _split_semicolon(_value(trial_row, "trial_blockers"))
    if trial_blockers:
        blockers.extend(trial_blockers)
    if _value(trial_row, "trial_action") == "blocked" and not trial_blockers:
        blockers.append("activation_trial_blocked")
    if _same_peak_verdict(trial_row) != "same_peak_supported":
        blockers.append("same_peak_conflict")
    current_written = bool_value(trial_row.get("current_public_matrix_written")) is True
    current_value = _value(trial_row, "current_public_matrix_value")
    projected_value = _projected_matrix_value(normal_row)
    if (
        current_written
        and current_value
        and not _numeric_equal(current_value, projected_value)
    ):
        blockers.append("current_public_matrix_value_conflict")
    return tuple(_dedupe(blockers))


def _promotion_row(
    normal_row: Mapping[str, Any],
    *,
    trial_row: Mapping[str, Any],
    source_artifact_sha256: str,
    source_run_id: str,
) -> dict[str, str]:
    source_peak_id = _value(normal_row, "peak_hypothesis_id")
    activation_peak_id = _value(normal_row, "full_cohort_matched_peak_hypothesis_id")
    sample = _value(normal_row, "sample_stem")
    current_written = _bool_text(trial_row.get("current_public_matrix_written"))
    row_hash = _row_sha256(
        source_peak_id,
        activation_peak_id,
        sample,
        _projected_matrix_value(normal_row),
        _projected_matrix_value_source_field(normal_row),
    )
    return {
        "schema_version": backfill_peakhypothesis_promotion.SCHEMA_VERSION,
        "peak_hypothesis_id": activation_peak_id,
        "activation_unit_scope": "peak_hypothesis",
        "feature_family_id": activation_peak_id,
        "seed_group_id": f"source_peak_hypothesis_id:{source_peak_id}",
        "sample_stem": sample,
        "promotion_decision": "promote_matrix_write",
        "promotion_reasons": ";".join(
            (
                _TRANSFER_REASON,
                f"source_peak_hypothesis_id:{source_peak_id}",
                f"trial_action:{_value(trial_row, 'trial_action')}",
            ),
        ),
        "promotion_blockers": "",
        "current_production_status": _value(normal_row, "full_cohort_cell_status"),
        "current_raw_status": _value(normal_row, "full_cohort_cell_status"),
        "current_matrix_written": current_written,
        "shadow_reasons": _shadow_reasons(normal_row),
        "projected_matrix_written": "TRUE",
        "projected_matrix_value": _projected_matrix_value(normal_row),
        "projected_matrix_value_source": _projected_matrix_value_source(normal_row),
        "area_policy": _value(normal_row, "area_policy"),
        "area_uncertainty_state": "standard_assessable",
        "area_uncertainty_reason": _value(
            normal_row,
            "normal_peak_shape_definition",
        ),
        "area_uncertainty_fraction": "",
        "area_uncertainty_fraction_status": "not_applicable",
        "matrix_quantitative_use": _value(normal_row, "matrix_quantitative_use"),
        "product_authority_chain": (
            "normal_peak_same_peak_manual_overlay;"
            f"{_projected_matrix_value_source(normal_row)}"
        ),
        "authority_source": "full_cohort_normal_peak_transfer_activation",
        "source_artifact_schema_version": SCHEMA_VERSION,
        "source_artifact_sha256": source_artifact_sha256,
        "source_row_sha256": row_hash,
        "source_provenance_detail": (
            "full_cohort_normal_peak_transfer_activation;"
            "source_bundle=normal_peak_decisions_tsv+activation_trial_tsv;"
            "normal_peak_quantitation_area_source_field:"
            f"{_projected_matrix_value_source_field(normal_row)}"
        ),
        "shadow_projection_sha256": source_artifact_sha256,
        "shadow_projection_row_sha256": row_hash,
    }


def _transfer_row(
    normal_row: Mapping[str, Any],
    *,
    trial_row: Mapping[str, Any],
    blockers: Sequence[str],
    source_artifact_sha256: str,
    source_run_id: str,
) -> dict[str, str]:
    return {
        "schema_version": SCHEMA_VERSION,
        "source_run_id": source_run_id,
        "source_peak_hypothesis_id": _value(normal_row, "peak_hypothesis_id"),
        "activation_peak_hypothesis_id": _value(
            normal_row,
            "full_cohort_matched_peak_hypothesis_id",
        ),
        "sample_stem": _value(normal_row, "sample_stem"),
        "source_id_authority": _SOURCE_ID_AUTHORITY,
        "activation_key_authority": _ACTIVATION_KEY_AUTHORITY,
        "full_cohort_cell_status": _value(normal_row, "full_cohort_cell_status"),
        "full_cohort_consolidation_state": _value(
            normal_row,
            "full_cohort_consolidation_state",
        ),
        "manual_same_peak_verdict": _value(normal_row, "manual_same_peak_verdict"),
        "same_peak_verdict": _same_peak_verdict(normal_row),
        "same_peak_verdict_source": _same_peak_verdict_source(normal_row),
        "normal_peak_decision": _value(normal_row, "normal_peak_decision"),
        "normal_peak_backfill_required": _bool_text(
            normal_row.get("normal_peak_backfill_required"),
        ),
        "trial_action": _value(trial_row, "trial_action"),
        "current_matrix_written": _bool_text(
            trial_row.get("current_public_matrix_written"),
        ),
        "current_matrix_value": _value(trial_row, "current_public_matrix_value"),
        "projected_matrix_value": _projected_matrix_value(normal_row),
        "projected_matrix_value_source": _projected_matrix_value_source(normal_row),
        "projected_matrix_value_source_field": _projected_matrix_value_source_field(
            normal_row,
        ),
        "source_artifact_schema_version": SCHEMA_VERSION,
        "source_artifact_sha256": source_artifact_sha256,
        "source_row_sha256": _row_sha256(
            _value(normal_row, "peak_hypothesis_id"),
            _value(normal_row, "full_cohort_matched_peak_hypothesis_id"),
            _value(normal_row, "sample_stem"),
            _projected_matrix_value(normal_row),
            _projected_matrix_value_source_field(normal_row),
        ),
        "source_provenance_detail": (
            "full_cohort_normal_peak_transfer_activation;"
            "source_bundle=normal_peak_decisions_tsv+activation_trial_tsv"
        ),
        "transfer_action": "blocked" if blockers else "emit_promotion_row",
        "transfer_blockers": ";".join(blockers),
    }


def _summary(
    *,
    normal_peak_decision_rows: Sequence[Mapping[str, Any]],
    activation_trial_rows: Sequence[Mapping[str, Any]],
    promotion_rows: Sequence[Mapping[str, str]],
    transfer_rows: Sequence[Mapping[str, str]],
    source_artifact_sha256: str,
    source_run_id: str,
) -> dict[str, Any]:
    blocker_counts: Counter[str] = Counter()
    for row in transfer_rows:
        blocker_counts.update(_split_semicolon(_value(row, "transfer_blockers")))
    hard_fail_reasons = tuple(sorted(blocker_counts))
    expected_activation = sum(
        1 for row in promotion_rows if _value(row, "current_matrix_written") != "TRUE"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "source_run_id": source_run_id,
        "source_artifact_sha256": source_artifact_sha256,
        "source_artifact_detail": (
            "normal_peak_decisions_tsv+activation_trial_tsv"
        ),
        "transfer_status": "pass" if not hard_fail_reasons else "fail",
        "normal_peak_decision_row_count": len(normal_peak_decision_rows),
        "activation_trial_row_count": len(activation_trial_rows),
        "transfer_row_count": len(transfer_rows),
        "promotion_row_count": len(promotion_rows),
        "blocked_transfer_row_count": sum(
            1 for row in transfer_rows if _value(row, "transfer_blockers")
        ),
        "expected_activation_decision_count": expected_activation,
        "already_public_matrix_written_count": len(promotion_rows)
        - expected_activation,
        "source_peak_hypothesis_id_authority": _SOURCE_ID_AUTHORITY,
        "activation_key_authority": _ACTIVATION_KEY_AUTHORITY,
        "hard_fail_reasons": ";".join(hard_fail_reasons),
        "hard_fail_reason_counts": dict(blocker_counts),
        "matrix_contract_changed": False,
        "product_behavior_changed": False,
        "next_action": (
            "bridge_transfer_promotions_into_activation_sidecar"
            if not hard_fail_reasons
            else "review_full_cohort_activation_transfer_blockers"
        ),
    }


def _shadow_reasons(row: Mapping[str, Any]) -> str:
    reasons = _split_semicolon(_value(row, "normal_peak_decision_reasons"))
    reasons.append("normal_peak_same_peak_override")
    return ";".join(_dedupe(reasons))


def _projected_matrix_value(row: Mapping[str, Any]) -> str:
    return _value(row, "normal_peak_quantitation_area") or _value(
        row,
        "full_cohort_primary_matrix_area",
    )


def _projected_matrix_value_source(row: Mapping[str, Any]) -> str:
    return _value(row, "normal_peak_quantitation_area_source") or _value(
        row,
        "full_cohort_primary_matrix_area_source",
    )


def _projected_matrix_value_source_field(row: Mapping[str, Any]) -> str:
    return _value(row, "normal_peak_quantitation_area_source_field") or (
        "full_cohort_primary_matrix_area"
    )


def _same_peak_verdict(row: Mapping[str, Any]) -> str:
    return _value(row, "same_peak_verdict") or _value(
        row,
        "manual_same_peak_verdict",
    )


def _same_peak_verdict_source(row: Mapping[str, Any]) -> str:
    return _value(row, "same_peak_verdict_source") or (
        "legacy_manual_same_peak_verdict"
        if _value(row, "manual_same_peak_verdict")
        else ""
    )


def _row_sha256(*parts: str) -> str:
    payload = "\t".join(parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def input_bundle_sha256(*paths: Path) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _validate_sha256_hex(label: str, value: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{label} must be a lowercase sha256 hex digest")


def _split_semicolon(value: str) -> list[str]:
    return [part.strip() for part in value.split(";") if part.strip()]


def _dedupe(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _positive_number(value: str) -> bool:
    try:
        parsed = float(value)
    except ValueError:
        return False
    return math.isfinite(parsed) and parsed > 0


def _numeric_equal(left: str, right: str) -> bool:
    try:
        left_value = float(left)
        right_value = float(right)
    except ValueError:
        return left == right
    return math.isclose(left_value, right_value, rel_tol=1e-6, abs_tol=1e-9)


def _bool_text(value: object) -> str:
    parsed = bool_value(value)
    if parsed is None:
        return ""
    return "TRUE" if parsed else "FALSE"


def _value(row: Mapping[str, Any], column: str) -> str:
    return text_value(row.get(column, ""))
