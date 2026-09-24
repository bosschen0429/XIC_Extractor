"""Diagnostic-only PeakHypothesis backfill promotion projection.

This module consumes shadow projection rows plus a manually reviewed allowlist.
It does not read RAW files, mutate final matrices, or write workbook outputs.
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

from xic_extractor.diagnostics.diagnostic_io import (
    format_diagnostic_value,
    text_value,
    write_tsv,
)

SCHEMA_VERSION = "backfill_peakhypothesis_promotion"
ALLOWLIST_SCHEMA_VERSION = "backfill_peakhypothesis_promotion_allowlist"

PROMOTION_COLUMNS = (
    "schema_version",
    "peak_hypothesis_id",
    "activation_unit_scope",
    "feature_family_id",
    "seed_group_id",
    "sample_stem",
    "promotion_decision",
    "promotion_reasons",
    "promotion_blockers",
    "current_production_status",
    "current_raw_status",
    "current_matrix_written",
    "shadow_reasons",
    "projected_matrix_written",
    "projected_matrix_value",
    "projected_matrix_value_source",
    "area_policy",
    "area_uncertainty_state",
    "area_uncertainty_reason",
    "area_uncertainty_fraction",
    "area_uncertainty_fraction_status",
    "matrix_quantitative_use",
    "product_authority_chain",
    "authority_source",
    "source_artifact_schema_version",
    "source_artifact_sha256",
    "source_row_sha256",
    "source_provenance_detail",
    "shadow_projection_sha256",
    "shadow_projection_row_sha256",
)

AREA_UNCERTAINTY_COLUMNS = (
    "schema_version",
    "peak_hypothesis_id",
    "activation_unit_scope",
    "feature_family_id",
    "seed_group_id",
    "sample_stem",
    "projected_matrix_value",
    "area_uncertainty_state",
    "area_uncertainty_reason",
    "area_uncertainty_fraction",
    "area_uncertainty_fraction_status",
    "area_policy",
    "integration_bounds_source",
    "peak_start_rt",
    "peak_end_rt",
    "matrix_quantitative_use",
    "product_authority_chain",
    "shadow_projection_sha256",
    "shadow_projection_row_sha256",
)

SHADOW_REQUIRED_COLUMNS = (
    "peak_hypothesis_id",
    "activation_unit_scope",
    "feature_family_id",
    "seed_group_id",
    "sample_stem",
    "current_raw_status",
    "current_production_status",
    "current_matrix_written",
    "shadow_decision",
    "shadow_reasons",
    "projected_matrix_written",
    "projected_matrix_value",
    "product_authority_chain",
    "hard_blockers",
    "missing_evidence",
    "shadow_projection_row_sha256",
)

ALLOWLIST_REQUIRED_COLUMNS = (
    "schema_version",
    "peak_hypothesis_id",
    "activation_unit_scope",
    "feature_family_id",
    "seed_group_id",
    "sample_stem",
    "authority_status",
    "authority_source",
    "authority_reason",
    "expected_shadow_projection_sha256",
    "expected_shadow_projection_row_sha256",
    "expected_product_authority_chain",
    "area_policy",
    "area_uncertainty_reason",
    "area_uncertainty_fraction",
    "area_uncertainty_fraction_status",
    "integration_bounds_source",
    "peak_start_rt",
    "peak_end_rt",
    "matrix_quantitative_use",
    "reviewer",
    "reviewed_at",
)

_KEY_COLUMNS = (
    "peak_hypothesis_id",
    "feature_family_id",
    "seed_group_id",
    "sample_stem",
)
_READINESS_LABELS = {"diagnostic_only", "shadow_ready"}
_SUPPORTED_AREA_POLICIES = {
    "standard_assessable_area",
    "nonstandard_assessable_area",
    "unassessable_area",
}
_AREA_UNCERTAINTY_FRACTION_STATUSES = {
    "bounded",
    "estimated",
    "not_available",
    "reviewed",
}
_PROMOTION_REASON = "allowlisted_peakhypothesis_same_peak_backfill"
_IDENTITY_REVIEW_REASON = "identity_supported_review"


@dataclass(frozen=True)
class PromotionRow:
    schema_version: str
    peak_hypothesis_id: str
    activation_unit_scope: str
    feature_family_id: str
    seed_group_id: str
    sample_stem: str
    promotion_decision: str
    promotion_reasons: tuple[str, ...]
    promotion_blockers: tuple[str, ...]
    current_production_status: str
    current_raw_status: str
    current_matrix_written: str
    shadow_reasons: str
    projected_matrix_written: str
    projected_matrix_value: str
    projected_matrix_value_source: str
    area_policy: str
    area_uncertainty_state: str
    area_uncertainty_reason: str
    area_uncertainty_fraction: str
    area_uncertainty_fraction_status: str
    matrix_quantitative_use: str
    product_authority_chain: str
    authority_source: str
    source_artifact_schema_version: str
    source_artifact_sha256: str
    source_row_sha256: str
    source_provenance_detail: str
    shadow_projection_sha256: str
    shadow_projection_row_sha256: str
    integration_bounds_source: str
    peak_start_rt: str
    peak_end_rt: str


@dataclass(frozen=True)
class PromotionIndex:
    rows: tuple[PromotionRow, ...]
    summary: dict[str, Any]


@dataclass(frozen=True)
class PromotionOutputs:
    cells_tsv: Path
    area_uncertainty_tsv: Path
    summary_json: Path


def build_promotion_index(
    shadow_projection_rows: Sequence[Mapping[str, Any]],
    allowlist_rows: Sequence[Mapping[str, Any]],
    shadow_projection_sha256: str,
    source_run_id: str = "",
    readiness_label: str = "diagnostic_only",
) -> PromotionIndex:
    if readiness_label not in _READINESS_LABELS:
        raise ValueError(
            "readiness_label must be one of: "
            f"{', '.join(sorted(_READINESS_LABELS))}",
        )
    shadow_by_key, shadow_by_cell_key = _index_shadow_rows(shadow_projection_rows)
    allowlist_by_key = _index_allowlist_rows(allowlist_rows)
    rows = tuple(
        sorted(
            (
                _build_row(
                    allowlist_row=allowlist_row,
                    shadow_by_key=shadow_by_key,
                    shadow_by_cell_key=shadow_by_cell_key,
                    shadow_projection_sha256=shadow_projection_sha256,
                )
                for allowlist_row in allowlist_by_key.values()
            ),
            key=_row_sort_key,
        ),
    )
    return PromotionIndex(
        rows=rows,
        summary=_summary(
            rows,
            allowlist_row_count=len(allowlist_by_key),
            shadow_projection_sha256=shadow_projection_sha256,
            source_run_id=source_run_id,
            readiness_label=readiness_label,
        ),
    )


def write_promotion_outputs(
    output_dir: Path,
    index: PromotionIndex,
) -> PromotionOutputs:
    output_dir.mkdir(parents=True, exist_ok=True)
    cells_tsv = output_dir / "backfill_peakhypothesis_promotion_cells.tsv"
    area_uncertainty_tsv = output_dir / "backfill_peakhypothesis_area_uncertainty.tsv"
    summary_json = output_dir / "backfill_peakhypothesis_promotion_summary.json"
    promotion_rows = [_promotion_row_dict(row) for row in index.rows]
    uncertainty_rows = [
        _area_uncertainty_row_dict(row)
        for row in index.rows
        if row.promotion_decision == "promote_matrix_write"
    ]
    write_tsv(
        cells_tsv,
        promotion_rows,
        PROMOTION_COLUMNS,
        formatter=format_diagnostic_value,
        lineterminator="\n",
    )
    write_tsv(
        area_uncertainty_tsv,
        uncertainty_rows,
        AREA_UNCERTAINTY_COLUMNS,
        formatter=format_diagnostic_value,
        lineterminator="\n",
    )
    summary_json.write_text(
        json.dumps(index.summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return PromotionOutputs(
        cells_tsv=cells_tsv,
        area_uncertainty_tsv=area_uncertainty_tsv,
        summary_json=summary_json,
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _build_row(
    *,
    allowlist_row: Mapping[str, Any],
    shadow_by_key: Mapping[tuple[str, str, str, str], Mapping[str, Any]],
    shadow_by_cell_key: Mapping[tuple[str, str, str], Mapping[str, Any]],
    shadow_projection_sha256: str,
) -> PromotionRow:
    key = _required_key(allowlist_row, label="allowlist")
    cell_key = key[1:]
    shadow_row = shadow_by_key.get(key)
    peak_id_drift = False
    if shadow_row is None:
        shadow_row = shadow_by_cell_key.get(cell_key)
        peak_id_drift = shadow_row is not None
    shadow = shadow_row or {}
    has_shadow_row = shadow_row is not None

    blockers: list[str] = []
    if not has_shadow_row:
        blockers.append("missing_shadow_projection_row")
    if _value(allowlist_row, "schema_version") != ALLOWLIST_SCHEMA_VERSION:
        blockers.append("allowlist_schema_version_mismatch")
    if _value(allowlist_row, "authority_status") != "product_authorized":
        blockers.append("allowlist_not_product_authorized")
    blockers.extend(_allowlist_review_blockers(allowlist_row))
    if _value(allowlist_row, "activation_unit_scope") != "peak_hypothesis":
        blockers.append("allowlist_activation_scope_not_peakhypothesis")
    if _value(allowlist_row, "expected_shadow_projection_sha256") != (
        shadow_projection_sha256
    ):
        blockers.append("shadow_projection_sha256_mismatch")

    shadow_row_sha = _value(shadow, "shadow_projection_row_sha256")
    if has_shadow_row and (
        _value(allowlist_row, "expected_shadow_projection_row_sha256")
        != shadow_row_sha
    ):
        blockers.append("shadow_projection_row_sha256_mismatch")

    area_policy = _value(allowlist_row, "area_policy")
    if area_policy not in _SUPPORTED_AREA_POLICIES:
        blockers.append("unsupported_area_policy")
    if peak_id_drift or (
        has_shadow_row
        and _value(shadow, "peak_hypothesis_id")
        != _value(allowlist_row, "peak_hypothesis_id")
    ):
        blockers.append("peak_hypothesis_id_drift")
    if has_shadow_row:
        blockers.extend(_shadow_blockers(shadow))
        blockers.extend(_product_authority_blockers(shadow, allowlist_row))
    blockers.extend(_area_blockers(allowlist_row))

    reasons = [_PROMOTION_REASON]
    area_state = _area_uncertainty_state(allowlist_row)
    if area_state == "nonstandard_assessable":
        reasons.append("area_uncertainty_recorded")
    if blockers:
        decision = "blocked"
        reasons = []
    else:
        decision = "promote_matrix_write"

    return PromotionRow(
        schema_version=SCHEMA_VERSION,
        peak_hypothesis_id=_value(allowlist_row, "peak_hypothesis_id"),
        activation_unit_scope=_value(allowlist_row, "activation_unit_scope"),
        feature_family_id=_value(allowlist_row, "feature_family_id"),
        seed_group_id=_value(allowlist_row, "seed_group_id"),
        sample_stem=_value(allowlist_row, "sample_stem"),
        promotion_decision=decision,
        promotion_reasons=tuple(reasons),
        promotion_blockers=tuple(blockers),
        current_production_status=_value(shadow, "current_production_status"),
        current_raw_status=_value(shadow, "current_raw_status"),
        current_matrix_written=_value(shadow, "current_matrix_written"),
        shadow_reasons=_value(shadow, "shadow_reasons"),
        projected_matrix_written=_value(shadow, "projected_matrix_written"),
        projected_matrix_value=_value(shadow, "projected_matrix_value"),
        projected_matrix_value_source="shadow_projection_projected_matrix_value",
        area_policy=area_policy,
        area_uncertainty_state=area_state,
        area_uncertainty_reason=_value(allowlist_row, "area_uncertainty_reason"),
        area_uncertainty_fraction=_value(allowlist_row, "area_uncertainty_fraction"),
        area_uncertainty_fraction_status=_value(
            allowlist_row,
            "area_uncertainty_fraction_status",
        ),
        matrix_quantitative_use=_value(allowlist_row, "matrix_quantitative_use"),
        product_authority_chain=_value(shadow, "product_authority_chain"),
        authority_source=_value(allowlist_row, "authority_source"),
        source_artifact_schema_version=(
            _value(shadow, "schema_version") or SCHEMA_VERSION
        ),
        source_artifact_sha256=shadow_projection_sha256,
        source_row_sha256=shadow_row_sha,
        source_provenance_detail="backfill_peakhypothesis_shadow_projection",
        shadow_projection_sha256=shadow_projection_sha256,
        shadow_projection_row_sha256=shadow_row_sha,
        integration_bounds_source=_value(allowlist_row, "integration_bounds_source"),
        peak_start_rt=_value(allowlist_row, "peak_start_rt"),
        peak_end_rt=_value(allowlist_row, "peak_end_rt"),
    )


def _allowlist_review_blockers(row: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    for column, blocker in (
        ("authority_source", "missing_authority_source"),
        ("authority_reason", "missing_authority_reason"),
        ("integration_bounds_source", "missing_integration_bounds_source"),
        ("peak_start_rt", "missing_peak_start_rt"),
        ("peak_end_rt", "missing_peak_end_rt"),
        ("reviewer", "missing_reviewer"),
        ("reviewed_at", "missing_reviewed_at"),
    ):
        if not _value(row, column):
            blockers.append(blocker)
    return blockers


def _shadow_blockers(row: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    shadow_reasons = set(_semicolon_values(row.get("shadow_reasons")))
    identity_supported = _IDENTITY_REVIEW_REASON in shadow_reasons
    if _value(row, "activation_unit_scope") != "peak_hypothesis":
        blockers.append("shadow_activation_scope_not_peakhypothesis")
    if identity_supported and _value(row, "shadow_decision") != "context":
        blockers.append("identity_review_decision_not_context")
    if not identity_supported:
        blockers.append("missing_identity_supported_review_reason")
    if _value(row, "current_raw_status") != "backfilled":
        blockers.append("current_raw_status_not_rescued")
    if _value(row, "current_production_status") != "review_backfill":
        blockers.append("current_status_not_review_backfill")
    if _value(row, "current_matrix_written") != "FALSE":
        blockers.append("current_matrix_already_written")
    if not _positive_number(_value(row, "projected_matrix_value")):
        blockers.append("projected_matrix_value_not_positive")
    if _value(row, "hard_blockers"):
        blockers.append("hard_blockers_present")
    if _value(row, "missing_evidence"):
        blockers.append("missing_evidence_present")
    return blockers


def _product_authority_blockers(
    shadow_row: Mapping[str, Any],
    allowlist_row: Mapping[str, Any],
) -> list[str]:
    chain = _value(shadow_row, "product_authority_chain")
    expected_chain = _value(allowlist_row, "expected_product_authority_chain")
    if chain != expected_chain:
        return ["product_authority_chain_drift"]
    return []


def _area_blockers(row: Mapping[str, Any]) -> list[str]:
    policy = _value(row, "area_policy")
    quantitative_use = _value(row, "matrix_quantitative_use")
    blockers: list[str] = []
    if policy == "unassessable_area":
        blockers.append("area_unassessable")
    if (
        policy == "standard_assessable_area"
        and quantitative_use != "standard_quantitative_use"
    ):
        blockers.append("standard_area_quantitative_use_mismatch")
    if policy == "nonstandard_assessable_area":
        if quantitative_use != "use_with_uncertainty":
            blockers.append("nonstandard_area_quantitative_use_mismatch")
        if not _value(row, "area_uncertainty_reason"):
            blockers.append("missing_area_uncertainty_reason")
        fraction = _value(row, "area_uncertainty_fraction")
        status = _value(row, "area_uncertainty_fraction_status")
        if not (fraction or status):
            blockers.append("missing_area_uncertainty_fraction_status")
        if fraction and not _valid_uncertainty_fraction(fraction):
            blockers.append("invalid_area_uncertainty_fraction")
        if status and status not in _AREA_UNCERTAINTY_FRACTION_STATUSES:
            blockers.append("unsupported_area_uncertainty_fraction_status")
        blockers.append("nonstandard_area_review_only")
    return blockers


def _area_uncertainty_state(row: Mapping[str, Any]) -> str:
    policy = _value(row, "area_policy")
    if policy == "standard_assessable_area":
        return "standard_assessable"
    if policy == "nonstandard_assessable_area":
        return "nonstandard_assessable"
    if policy == "unassessable_area":
        return "unassessable"
    return ""


def _positive_number(value: str) -> bool:
    try:
        parsed = float(value)
    except ValueError:
        return False
    return math.isfinite(parsed) and parsed > 0


def _valid_uncertainty_fraction(value: str) -> bool:
    try:
        parsed = float(value)
    except ValueError:
        return False
    return math.isfinite(parsed) and 0 <= parsed <= 1


def _index_allowlist_rows(
    rows: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str, str, str], Mapping[str, Any]]:
    by_key: dict[tuple[str, str, str, str], Mapping[str, Any]] = {}
    for row in rows:
        key = _required_key(row, label="allowlist")
        if key in by_key:
            raise ValueError(f"duplicate allowlist key: {_format_key(key)}")
        by_key[key] = row
    return by_key


def _index_shadow_rows(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[
    dict[tuple[str, str, str, str], Mapping[str, Any]],
    dict[tuple[str, str, str], Mapping[str, Any]],
]:
    by_key: dict[tuple[str, str, str, str], Mapping[str, Any]] = {}
    by_cell_key: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    for row in rows:
        key = _optional_key(row)
        if key is None:
            continue
        if key in by_key:
            raise ValueError(f"duplicate shadow projection key: {_format_key(key)}")
        by_key[key] = row
        by_cell_key.setdefault(key[1:], row)
    return by_key, by_cell_key


def _required_key(row: Mapping[str, Any], *, label: str) -> tuple[str, str, str, str]:
    key = _optional_key(row)
    if key is None:
        missing = [column for column in _KEY_COLUMNS if not _value(row, column)]
        raise ValueError(f"{label} row missing key components: {', '.join(missing)}")
    return key


def _optional_key(row: Mapping[str, Any]) -> tuple[str, str, str, str] | None:
    values = tuple(_value(row, column) for column in _KEY_COLUMNS)
    if any(not value for value in values):
        return None
    return (values[0], values[1], values[2], values[3])


def _row_sort_key(row: PromotionRow) -> tuple[str, str, str, str]:
    return (
        row.peak_hypothesis_id,
        row.feature_family_id,
        row.seed_group_id,
        row.sample_stem,
    )


def _summary(
    rows: Sequence[PromotionRow],
    *,
    allowlist_row_count: int,
    shadow_projection_sha256: str,
    source_run_id: str,
    readiness_label: str,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "readiness_label": readiness_label,
        "source_run_id": source_run_id,
        "shadow_projection_sha256": shadow_projection_sha256,
        "allowlist_row_count": allowlist_row_count,
        "row_count": len(rows),
        "decision_counts": dict(Counter(row.promotion_decision for row in rows)),
        "area_uncertainty_counts": dict(
            Counter(row.area_uncertainty_state for row in rows),
        ),
        "matrix_contract_changed": False,
        "product_behavior_changed": False,
    }


def _promotion_row_dict(row: PromotionRow) -> dict[str, Any]:
    return {
        "schema_version": row.schema_version,
        "peak_hypothesis_id": row.peak_hypothesis_id,
        "activation_unit_scope": row.activation_unit_scope,
        "feature_family_id": row.feature_family_id,
        "seed_group_id": row.seed_group_id,
        "sample_stem": row.sample_stem,
        "promotion_decision": row.promotion_decision,
        "promotion_reasons": ";".join(row.promotion_reasons),
        "promotion_blockers": ";".join(row.promotion_blockers),
        "current_production_status": row.current_production_status,
        "current_raw_status": row.current_raw_status,
        "current_matrix_written": row.current_matrix_written,
        "shadow_reasons": row.shadow_reasons,
        "projected_matrix_written": row.projected_matrix_written,
        "projected_matrix_value": row.projected_matrix_value,
        "projected_matrix_value_source": row.projected_matrix_value_source,
        "area_policy": row.area_policy,
        "area_uncertainty_state": row.area_uncertainty_state,
        "area_uncertainty_reason": row.area_uncertainty_reason,
        "area_uncertainty_fraction": row.area_uncertainty_fraction,
        "area_uncertainty_fraction_status": row.area_uncertainty_fraction_status,
        "matrix_quantitative_use": row.matrix_quantitative_use,
        "product_authority_chain": row.product_authority_chain,
        "authority_source": row.authority_source,
        "source_artifact_schema_version": row.source_artifact_schema_version,
        "source_artifact_sha256": row.source_artifact_sha256,
        "source_row_sha256": row.source_row_sha256,
        "source_provenance_detail": row.source_provenance_detail,
        "shadow_projection_sha256": row.shadow_projection_sha256,
        "shadow_projection_row_sha256": row.shadow_projection_row_sha256,
    }


def _area_uncertainty_row_dict(row: PromotionRow) -> dict[str, Any]:
    return {
        "schema_version": row.schema_version,
        "peak_hypothesis_id": row.peak_hypothesis_id,
        "activation_unit_scope": row.activation_unit_scope,
        "feature_family_id": row.feature_family_id,
        "seed_group_id": row.seed_group_id,
        "sample_stem": row.sample_stem,
        "projected_matrix_value": row.projected_matrix_value,
        "area_uncertainty_state": row.area_uncertainty_state,
        "area_uncertainty_reason": row.area_uncertainty_reason,
        "area_uncertainty_fraction": row.area_uncertainty_fraction,
        "area_uncertainty_fraction_status": row.area_uncertainty_fraction_status,
        "area_policy": row.area_policy,
        "integration_bounds_source": row.integration_bounds_source,
        "peak_start_rt": row.peak_start_rt,
        "peak_end_rt": row.peak_end_rt,
        "matrix_quantitative_use": row.matrix_quantitative_use,
        "product_authority_chain": row.product_authority_chain,
        "shadow_projection_sha256": row.shadow_projection_sha256,
        "shadow_projection_row_sha256": row.shadow_projection_row_sha256,
    }


def _semicolon_values(value: Any) -> tuple[str, ...]:
    return tuple(part.strip() for part in text_value(value).split(";") if part.strip())


def _value(row: Mapping[str, Any], column: str) -> str:
    return text_value(row.get(column, ""))


def _format_key(key: tuple[str, str, str, str]) -> str:
    return "|".join(key)
