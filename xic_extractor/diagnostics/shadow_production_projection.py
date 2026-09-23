"""Cell-level shadow production projection for backfill review.

This module is deliberately a projection layer: it consumes the production
decision snapshot and existing diagnostic evidence, then writes a review TSV.
It does not mutate the alignment matrix or workbook outputs.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

from xic_extractor.alignment.backfill_evidence_projection import (
    load_ms1_pattern_coherence_rows,
    project_backfill_evidence_to_cells,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.matrix import AlignedCell, AlignmentMatrix
from xic_extractor.alignment.primary_matrix_area import (
    MS1_MORPHOLOGY_PRIMARY_MATRIX_AREA_SOURCE,
)
from xic_extractor.alignment.production_decisions import (
    ProductionCellDecision,
    ProductionDecisionSet,
    build_production_decisions,
)
from xic_extractor.alignment.promotion_policy import (
    ANCHOR_OWN_MAX_MS1_SUPPORT_REASON,
    BACKFILL_HYPOTHESIS_BLOCKED_REASON,
    STANDARD_PEAK_GATE_MS1_SUPPORT_REASON,
)
from xic_extractor.diagnostics.backfill_decision_explanation import (
    BackfillDecisionExplanation,
    decision_explanation,
)
from xic_extractor.diagnostics.backfill_overlay import (
    selected_overlay_row as select_backfill_overlay_row,
)
from xic_extractor.diagnostics.matrix_identity_projection import matrix_values_from_tsv
from xic_extractor.peak_detection.hypotheses import IntegrationResult
from xic_extractor.tabular_io import (
    file_sha256,
    format_diagnostic_value,
    optional_float,
    read_tsv_required,
    rows_by_text_field,
    split_semicolon_labels,
    text_value,
    write_tsv,
)

SCHEMA_VERSION = "shadow_production_projection"

ShadowDecision = Literal["accept", "block", "context"]


@dataclass(frozen=True)
class _ProjectionValueGateResult:
    explanation: BackfillDecisionExplanation
    projected_value: float | None
    projected_written: bool


@dataclass(frozen=True)
class _CurrentProjectionState:
    production_status: str
    raw_status: str
    matrix_written: bool
    matrix_value: float | None
    blank_reason: str
    matrix_source: str
    review_backfilled: bool


_PRODUCT_PROVENANCE_HASH_EXCLUDED_COLUMNS = frozenset(
    {
        "shadow_projection_row_sha256",
        "overlay_png_path",
    },
)

SHADOW_PRODUCTION_PROJECTION_COLUMNS = (
    "schema_version",
    "peak_hypothesis_id",
    "activation_unit_scope",
    "feature_family_id",
    "seed_group_id",
    "sample_stem",
    "current_raw_status",
    "current_production_status",
    "current_backfill_tier",
    "current_matrix_written",
    "current_matrix_value",
    "current_blank_reason",
    "current_matrix_source",
    "review_backfilled_cell",
    "shadow_decision",
    "shadow_reasons",
    "shadow_warnings",
    "projected_matrix_written",
    "projected_matrix_value",
    "projection_authority",
    "product_authority_chain",
    "seed_mz",
    "seed_rt",
    "seed_rt_window",
    "detected_anchor_count",
    "rescued_cell_count",
    "request_window_overlap",
    "local_global_ratio",
    "cell_status",
    "gap_fill_state",
    "gap_fill_reason",
    "evidence_gate_status",
    "support_components",
    "hard_blockers",
    "missing_evidence",
    "shadow_projection_row_sha256",
    "overlay_verdict",
    "overlay_png_path",
)

RETAINED_GATE_REQUIRED_COLUMNS = (
    "feature_family_id",
    "seed_group_id",
    "seed_group_basis",
    "seed_mz",
    "seed_rt",
    "suggested_rt_min",
    "suggested_rt_max",
    "detected_cell_count",
    "rescued_cell_count",
    "seed_source_samples",
    "evidence_gate_status",
    "support_components",
    "challenge_blockers",
    "missing_evidence",
)
REVIEW_REQUIRED_COLUMNS = (
    "feature_family_id",
    "neutral_loss_tag",
    "detected_count",
)
CELL_REQUIRED_COLUMNS = (
    "feature_family_id",
    "sample_stem",
    "status",
    "area",
    "apex_rt",
    "height",
    "peak_start_rt",
    "peak_end_rt",
    "rt_delta_sec",
)
GATE_REQUIRED_COLUMNS = RETAINED_GATE_REQUIRED_COLUMNS
OVERLAY_REQUIRED_COLUMNS = (
    "feature_family_id",
    "family_verdict",
    "png_path",
)

HARD_BLOCKER_TOKENS = {
    "not_same_gaussian15_peak_segment",
    "outside_selected_segment",
    "neighboring_interference_hard_block",
    "missing_ms1_peak_segment",
}
CURRENT_PRODUCTION_HARD_BLOCKERS = {BACKFILL_HYPOTHESIS_BLOCKED_REASON}

PROJECTION_ACCEPT_GATE_STATUSES = {
    "visual_support",
    "product_grade_support",
}
_PRODUCT_AUTHORIZED_STATUS = "product_authorized"
_PRODUCT_AUTHORIZED_SCOPE = "feature_family_sample"
_SUPPORT_STATUSES = {"supportive", "partial_support"}
_MS1_SAME_PEAK_LEVEL = "trace_constellation"
_MS1_SAME_PEAK_SUPPORT_REASONS = frozenset(
    {
        ANCHOR_OWN_MAX_MS1_SUPPORT_REASON,
        STANDARD_PEAK_GATE_MS1_SUPPORT_REASON,
    }
)
_IDENTITY_SUPPORTED_REVIEW_REASON = "identity_supported_review"
_SEED_REQUEST_COMPONENT = "seed_request_provenance"
_MS1_SHAPE_SUPPORT_COMPONENT = "ms1_shape_supports_family_backfill"


@dataclass(frozen=True)
class ShadowProductionProjectionIndex:
    rows: tuple[dict[str, str], ...]
    summary: dict[str, object]


@dataclass(frozen=True)
class ShadowProductionProjectionOutputs:
    tsv: Path
    json: Path


def run_shadow_production_projection(
    *,
    alignment_review_tsv: Path,
    alignment_cells_tsv: Path,
    retained_gate_tsv: Path,
    output_dir: Path,
    alignment_matrix_tsv: Path | None = None,
    alignment_matrix_identity_tsv: Path | None = None,
    overlay_batch_summary_tsvs: Sequence[Path] = (),
    ms1_pattern_coherence_tsvs: Sequence[Path] = (),
    source_run_id: str = "",
) -> ShadowProductionProjectionOutputs:
    if alignment_matrix_tsv is not None and not alignment_matrix_tsv.exists():
        raise FileNotFoundError(str(alignment_matrix_tsv))
    if (
        alignment_matrix_identity_tsv is not None
        and not alignment_matrix_identity_tsv.exists()
    ):
        raise FileNotFoundError(str(alignment_matrix_identity_tsv))
    review_rows = read_tsv_required(alignment_review_tsv, REVIEW_REQUIRED_COLUMNS)
    cell_rows = read_tsv_required(alignment_cells_tsv, CELL_REQUIRED_COLUMNS)
    projection_cell_rows: Sequence[Mapping[str, str]] = cell_rows
    gate_rows = read_tsv_required(retained_gate_tsv, GATE_REQUIRED_COLUMNS)
    ms1_pattern_rows: list[dict[str, str]] = []
    for path in ms1_pattern_coherence_tsvs:
        ms1_pattern_rows.extend(load_ms1_pattern_coherence_rows(path))
    if ms1_pattern_rows:
        projection_cell_rows = project_backfill_evidence_to_cells(
            cell_rows=cell_rows,
            ms1_pattern_coherence_rows=ms1_pattern_rows,
        )
    overlay_rows: list[dict[str, str]] = []
    for path in overlay_batch_summary_tsvs:
        overlay_rows.extend(read_tsv_required(path, OVERLAY_REQUIRED_COLUMNS))

    matrix = _alignment_matrix_from_tsv(review_rows, cell_rows)
    decisions = build_production_decisions(matrix, AlignmentConfig())
    index = build_shadow_production_projection_index(
        production_decisions=decisions,
        cell_rows=projection_cell_rows,
        retained_gate_rows=gate_rows,
        overlay_rows=overlay_rows,
        current_matrix_values=_current_matrix_values_by_family_sample(
            alignment_matrix_tsv=alignment_matrix_tsv,
            alignment_matrix_identity_tsv=alignment_matrix_identity_tsv,
            requested_keys=_requested_matrix_keys(gate_rows),
        ),
        source_run_id=source_run_id,
        source_review_sha256=file_sha256(alignment_review_tsv),
        source_cell_sha256=file_sha256(alignment_cells_tsv),
        source_gate_sha256=file_sha256(retained_gate_tsv),
        source_matrix_sha256=(
            file_sha256(alignment_matrix_tsv)
            if alignment_matrix_tsv is not None and alignment_matrix_tsv.exists()
            else ""
        ),
        source_overlay_artifacts=tuple(
            str(path) for path in overlay_batch_summary_tsvs
        ),
        source_overlay_sha256s=tuple(
            file_sha256(path) for path in overlay_batch_summary_tsvs
        ),
        source_ms1_pattern_coherence_artifacts=tuple(
            str(path) for path in ms1_pattern_coherence_tsvs
        ),
        source_ms1_pattern_coherence_sha256s=tuple(
            file_sha256(path) for path in ms1_pattern_coherence_tsvs
        ),
    )
    return write_shadow_production_projection_outputs(output_dir, index)


def build_shadow_production_projection_index(
    *,
    production_decisions: ProductionDecisionSet,
    cell_rows: Iterable[Mapping[str, str]],
    retained_gate_rows: Iterable[Mapping[str, str]],
    overlay_rows: Iterable[Mapping[str, str]] = (),
    current_matrix_values: Mapping[tuple[str, str], str] | None = None,
    source_run_id: str = "",
    source_review_sha256: str = "",
    source_cell_sha256: str = "",
    source_gate_sha256: str = "",
    source_matrix_sha256: str = "",
    source_overlay_artifacts: Sequence[str] = (),
    source_overlay_sha256s: Sequence[str] = (),
    source_ms1_pattern_coherence_artifacts: Sequence[str] = (),
    source_ms1_pattern_coherence_sha256s: Sequence[str] = (),
) -> ShadowProductionProjectionIndex:
    gate_rows = tuple(dict(row) for row in retained_gate_rows)
    _validate_gate_rows(gate_rows)
    gate_projection_summary = _gate_projection_summary(gate_rows)
    current_matrix_values = current_matrix_values or {}
    cells_by_key = {
        (
            text_value(row.get("feature_family_id")),
            text_value(row.get("sample_stem")),
        ): dict(row)
        for row in cell_rows
        if text_value(row.get("feature_family_id"))
        and text_value(row.get("sample_stem"))
    }
    overlays_by_family = _group_by_family(overlay_rows)
    rows: list[dict[str, str]] = []
    for gate_row in gate_rows:
        family_id = text_value(gate_row.get("feature_family_id"))
        seed_group_id = text_value(gate_row.get("seed_group_id"))
        if not family_id or not seed_group_id:
            continue
        overlay_row = _selected_overlay_row(
            overlays_by_family.get(family_id, ()),
            seed_group_id=seed_group_id,
        )
        for sample in split_semicolon_labels(gate_row.get("seed_source_samples")):
            decision = production_decisions.cells.get((family_id, sample))
            cell = cells_by_key.get((family_id, sample), {})
            matrix_key = (family_id, sample)
            rows.append(
                _projection_row(
                    gate_row=gate_row,
                    overlay_row=overlay_row,
                    cell=cell,
                    sample_stem=sample,
                    decision=decision,
                    current_matrix_cell_value=(
                        current_matrix_values.get(matrix_key)
                        if matrix_key in current_matrix_values
                        else None
                    ),
                    current_matrix_cell_known=matrix_key in current_matrix_values,
                ),
            )

    sorted_rows = tuple(sorted(rows, key=_row_sort_key))
    return ShadowProductionProjectionIndex(
        rows=sorted_rows,
        summary=_summary(
            sorted_rows,
            gate_projection_summary=gate_projection_summary,
            source_run_id=source_run_id,
            source_review_sha256=source_review_sha256,
            source_cell_sha256=source_cell_sha256,
            source_gate_sha256=source_gate_sha256,
            source_matrix_sha256=source_matrix_sha256,
            source_overlay_artifacts=source_overlay_artifacts,
            source_overlay_sha256s=source_overlay_sha256s,
            source_ms1_pattern_coherence_artifacts=(
                source_ms1_pattern_coherence_artifacts
            ),
            source_ms1_pattern_coherence_sha256s=(
                source_ms1_pattern_coherence_sha256s
            ),
        ),
    )


def write_shadow_production_projection_outputs(
    output_dir: Path,
    index: ShadowProductionProjectionIndex,
) -> ShadowProductionProjectionOutputs:
    output_dir.mkdir(parents=True, exist_ok=True)
    tsv_path = output_dir / "shadow_production_projection_cells.tsv"
    json_path = output_dir / "shadow_production_projection_summary.json"
    write_tsv(
        tsv_path,
        index.rows,
        SHADOW_PRODUCTION_PROJECTION_COLUMNS,
        formatter=format_diagnostic_value,
        lineterminator="\n",
    )
    json_path.write_text(
        json.dumps(index.summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return ShadowProductionProjectionOutputs(tsv=tsv_path, json=json_path)


def _current_matrix_values_by_family_sample(
    *,
    alignment_matrix_tsv: Path | None,
    alignment_matrix_identity_tsv: Path | None,
    requested_keys: set[tuple[str, str]] | None = None,
) -> dict[tuple[str, str], str]:
    if alignment_matrix_tsv is None or alignment_matrix_identity_tsv is None:
        return {}
    return matrix_values_from_tsv(
        alignment_matrix_tsv=alignment_matrix_tsv,
        alignment_matrix_identity_tsv=alignment_matrix_identity_tsv,
        key_mode="family_aliases",
        include_blank=True,
        requested_keys=requested_keys,
        duplicate_policy="last",
    )


def _requested_matrix_keys(
    retained_gate_rows: Sequence[Mapping[str, str]],
) -> set[tuple[str, str]]:
    return {
        (family_id, sample)
        for row in retained_gate_rows
        if (family_id := text_value(row.get("feature_family_id")))
        for sample in split_semicolon_labels(row.get("seed_source_samples"))
    }


def _alignment_matrix_from_tsv(
    review_rows: Sequence[Mapping[str, str]],
    cell_rows: Sequence[Mapping[str, str]],
) -> AlignmentMatrix:
    clusters = tuple(_cluster_from_review(row) for row in review_rows)
    cells = tuple(_cell_from_row(row) for row in cell_rows)
    sample_order = tuple(
        sorted(
            {
                row.get("sample_stem", "")
                for row in cell_rows
                if row.get("sample_stem")
            },
        ),
    )
    return AlignmentMatrix(clusters=clusters, cells=cells, sample_order=sample_order)


def _cluster_from_review(row: Mapping[str, str]) -> SimpleNamespace:
    evidence = row.get("family_evidence") or row.get("evidence") or ""
    return SimpleNamespace(
        feature_family_id=row.get("feature_family_id", ""),
        neutral_loss_tag=row.get("neutral_loss_tag", ""),
        family_center_mz=_float(row.get("family_center_mz")) or 0.0,
        family_center_rt=_float(row.get("family_center_rt")) or 0.0,
        family_product_mz=_float(row.get("family_product_mz")) or 0.0,
        family_observed_neutral_loss_da=(
            _float(row.get("family_observed_neutral_loss_da")) or 0.0
        ),
        has_anchor=_is_trueish(row.get("has_anchor")) or _float(
            row.get("detected_count"),
        )
        not in (None, 0.0),
        event_cluster_ids=tuple(
            part for part in row.get("event_cluster_ids", "").split(";") if part
        ),
        event_member_count=int(_float(row.get("event_member_count")) or 0),
        evidence=evidence,
        review_only=_is_review_only_row(row, evidence),
    )


def _cell_from_row(row: Mapping[str, str]) -> AlignedCell:
    start = _float(row.get("peak_start_rt"))
    end = _float(row.get("peak_end_rt"))
    apex = _float(row.get("apex_rt"))
    raw_area = _float(row.get("area"))
    if apex is None and start is not None and end is not None:
        apex = (start + end) / 2.0
    return AlignedCell(
        sample_stem=row.get("sample_stem", ""),
        cluster_id=row.get("feature_family_id", ""),
        status=row.get("status", ""),  # type: ignore[arg-type]
        area=raw_area,
        apex_rt=apex,
        height=_float(row.get("height")) or 1.0,
        peak_start_rt=start,
        peak_end_rt=end,
        rt_delta_sec=_float(row.get("rt_delta_sec")),
        trace_quality=row.get("trace_quality", ""),
        scan_support_score=_float(row.get("scan_support_score")),
        source_candidate_id=row.get("source_candidate_id") or None,
        source_raw_file=None,
        reason=row.get("reason", ""),
        selected_integration=_integration_from_cell_row(
            row,
            raw_area=raw_area,
            apex=apex,
            start=start,
            end=end,
        ),
        backfill_ms1_pattern_status=row.get("backfill_ms1_pattern_status", ""),
        backfill_ms1_pattern_evidence_level=row.get(
            "backfill_ms1_pattern_evidence_level",
            "",
        ),
        backfill_ms1_product_authority_status=row.get(
            "backfill_ms1_product_authority_status",
            "",
        ),
        backfill_ms1_product_authority_scope=row.get(
            "backfill_ms1_product_authority_scope",
            "",
        ),
        backfill_ms1_product_authority_source=row.get(
            "backfill_ms1_product_authority_source",
            "",
        ),
        backfill_ms1_product_authority_reason=row.get(
            "backfill_ms1_product_authority_reason",
            "",
        ),
        backfill_ms1_product_authority_evidence_sha256=row.get(
            "backfill_ms1_product_authority_evidence_sha256",
            "",
        ),
        backfill_qc_reference_status=row.get("backfill_qc_reference_status", ""),
        backfill_qc_reference_evidence_level=row.get(
            "backfill_qc_reference_evidence_level",
            "",
        ),
        backfill_matrix_rt_drift_status=row.get(
            "backfill_matrix_rt_drift_status",
            "",
        ),
        backfill_drift_evidence_level=row.get("backfill_drift_evidence_level", ""),
        backfill_drift_compatible_status=row.get(
            "backfill_drift_compatible_status",
            "",
        ),
        backfill_drift_corrected_delta_sec=_float(
            row.get("backfill_drift_corrected_delta_sec"),
        ),
        backfill_candidate_ms2_pattern_status=row.get(
            "backfill_candidate_ms2_pattern_status",
            "",
        ),
        backfill_candidate_ms2_evidence_level=row.get(
            "backfill_candidate_ms2_evidence_level",
            "",
        ),
        backfill_candidate_ms2_product_authority_status=row.get(
            "backfill_candidate_ms2_product_authority_status",
            "",
        ),
        backfill_candidate_ms2_product_authority_scope=row.get(
            "backfill_candidate_ms2_product_authority_scope",
            "",
        ),
        backfill_candidate_ms2_product_authority_source=row.get(
            "backfill_candidate_ms2_product_authority_source",
            "",
        ),
        backfill_candidate_ms2_product_authority_reason=row.get(
            "backfill_candidate_ms2_product_authority_reason",
            "",
        ),
        backfill_candidate_ms2_product_authority_evidence_sha256=row.get(
            "backfill_candidate_ms2_product_authority_evidence_sha256",
            "",
        ),
        backfill_evidence_reason=row.get("backfill_evidence_reason", ""),
        group_hypothesis_id=row.get("group_hypothesis_id", ""),
        public_family_id=row.get("public_family_id", ""),
        group_construction_role=row.get("group_construction_role", ""),
        group_delivery_role=row.get("group_delivery_role", ""),
        group_membership_source=row.get("group_membership_source", ""),
        gap_fill_state=row.get("gap_fill_state", ""),
        gap_fill_reason=row.get("gap_fill_reason", ""),
        peak_hypothesis_status=row.get("peak_hypothesis_status", ""),
        product_selection_blocker=row.get("product_selection_blocker", ""),
        rt_mode_status=row.get("rt_mode_status", ""),
        group_claim_state=row.get("group_claim_state", ""),
        consolidation_state=row.get("consolidation_state", ""),
    )


def _integration_from_cell_row(
    row: Mapping[str, str],
    *,
    raw_area: float | None,
    apex: float | None,
    start: float | None,
    end: float | None,
) -> IntegrationResult | None:
    primary_area = _float(row.get("primary_matrix_area")) or raw_area
    if (
        primary_area is None
        or raw_area is None
        or apex is None
        or start is None
        or end is None
    ):
        return None
    return IntegrationResult(
        rt_left_min=start,
        rt_apex_min=apex,
        rt_right_min=end,
        raw_apex_rt_min=apex,
        rt_width_min=max(end - start, 0.0),
        height_raw=_float(row.get("height")) or 1.0,
        height_smoothed=_float(row.get("height")) or 1.0,
        area_raw_counts_seconds=raw_area,
        area_ms1_morphology=primary_area,
        ms1_morphology_area_source=MS1_MORPHOLOGY_PRIMARY_MATRIX_AREA_SOURCE,
        boundary_sources=("alignment_cells_tsv",),
    )


def _is_review_only_row(row: Mapping[str, str], evidence: str) -> bool:
    row_flags = _split_tokens(row.get("row_flags", ""))
    evidence_tokens = _split_tokens(evidence)
    return (
        row.get("identity_reason", "") == "review_only"
        or "review_only" in row_flags
        or "review_only" in evidence_tokens
    )


def _split_tokens(value: object) -> set[str]:
    return {part.strip() for part in str(value or "").split(";") if part.strip()}


def _is_trueish(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _float(value: object) -> float | None:
    return optional_float(value)


def _current_projection_state(
    *,
    cell: Mapping[str, str],
    decision: ProductionCellDecision | None,
    current_matrix_cell_value: str | None,
    current_matrix_cell_known: bool,
) -> _CurrentProjectionState:
    production_status = decision.production_status if decision else "blank"
    if current_matrix_cell_known:
        matrix_value = optional_float(current_matrix_cell_value)
        matrix_written = bool(text_value(current_matrix_cell_value))
        blank_reason = "" if matrix_written else "source_matrix_blank"
        matrix_source = "alignment_matrix_tsv"
    else:
        matrix_written = bool(decision and decision.write_matrix_value)
        matrix_value = decision.matrix_value if decision else None
        blank_reason = (
            decision.blank_reason if decision else "missing_production_decision"
        )
        matrix_source = "production_decision_snapshot"
    raw_status = decision.raw_status if decision else text_value(cell.get("status"))
    review_backfilled = (
        decision is not None
        and decision.production_status in {"review_backfill", "accepted_backfill"}
        and raw_status == "backfilled"
        and not matrix_written
    )
    return _CurrentProjectionState(
        production_status=production_status,
        raw_status=raw_status,
        matrix_written=matrix_written,
        matrix_value=matrix_value,
        blank_reason=blank_reason,
        matrix_source=matrix_source,
        review_backfilled=review_backfilled,
    )


def _projection_row(
    *,
    gate_row: Mapping[str, str],
    overlay_row: Mapping[str, str],
    cell: Mapping[str, str],
    sample_stem: str,
    decision: ProductionCellDecision | None,
    current_matrix_cell_value: str | None = None,
    current_matrix_cell_known: bool = False,
) -> dict[str, str]:
    current_state = _current_projection_state(
        cell=cell,
        decision=decision,
        current_matrix_cell_value=current_matrix_cell_value,
        current_matrix_cell_known=current_matrix_cell_known,
    )
    explanation = _shadow_projection_decision(
        gate_row=gate_row,
        cell=cell,
        decision=decision,
        review_backfilled=current_state.review_backfilled,
        current_written=current_state.matrix_written,
    )
    value_gate = _apply_projection_value_gate(
        explanation=explanation,
        cell=cell,
        current_written=current_state.matrix_written,
        current_value=current_state.matrix_value,
    )
    explanation = value_gate.explanation
    rt_start = text_value(gate_row.get("suggested_rt_min"))
    rt_end = text_value(gate_row.get("suggested_rt_max"))
    peak_hypothesis_id = text_value(cell.get("peak_hypothesis_id")) or text_value(
        cell.get("group_hypothesis_id"),
    )
    hard_blockers = tuple(
        token
        for token in _hard_blocker_tokens(gate_row)
        if token in HARD_BLOCKER_TOKENS
    )
    row = {
        "schema_version": SCHEMA_VERSION,
        "peak_hypothesis_id": peak_hypothesis_id,
        "activation_unit_scope": (
            "peak_hypothesis" if peak_hypothesis_id else ""
        ),
        "feature_family_id": text_value(gate_row.get("feature_family_id")),
        "seed_group_id": text_value(gate_row.get("seed_group_id")),
        "sample_stem": sample_stem,
        "current_raw_status": current_state.raw_status,
        "current_production_status": current_state.production_status,
        "current_backfill_tier": decision.backfill_tier if decision else "",
        "current_matrix_written": _bool_text(current_state.matrix_written),
        "current_matrix_value": _number_text(current_state.matrix_value),
        "current_blank_reason": current_state.blank_reason,
        "current_matrix_source": current_state.matrix_source,
        "review_backfilled_cell": _bool_text(current_state.review_backfilled),
        "shadow_decision": explanation.decision,
        "shadow_reasons": explanation.reason_text,
        "shadow_warnings": explanation.warning_text,
        "projected_matrix_written": _bool_text(value_gate.projected_written),
        "projected_matrix_value": _number_text(value_gate.projected_value),
        "projection_authority": "shadow_projection_only",
        "product_authority_chain": _product_authority_chain_text(cell),
        "seed_mz": text_value(gate_row.get("seed_mz")),
        "seed_rt": text_value(gate_row.get("seed_rt")),
        "seed_rt_window": f"{rt_start}-{rt_end}" if rt_start or rt_end else "",
        "detected_anchor_count": text_value(gate_row.get("detected_cell_count")),
        "rescued_cell_count": text_value(gate_row.get("rescued_cell_count")),
        "request_window_overlap": _bool_text(_request_window_overlap(cell, gate_row)),
        "local_global_ratio": _local_global_ratio_text(cell, overlay_row),
        "cell_status": text_value(cell.get("status")),
        "gap_fill_state": text_value(cell.get("gap_fill_state")),
        "gap_fill_reason": text_value(cell.get("gap_fill_reason")),
        "evidence_gate_status": text_value(gate_row.get("evidence_gate_status")),
        "support_components": text_value(gate_row.get("support_components")),
        "hard_blockers": ";".join(hard_blockers),
        "missing_evidence": text_value(gate_row.get("missing_evidence")),
        "shadow_projection_row_sha256": "",
        "overlay_verdict": text_value(gate_row.get("overlay_family_verdict")),
        "overlay_png_path": (
            text_value(overlay_row.get("png_path"))
            or text_value(gate_row.get("overlay_png_path"))
        ),
    }
    row["shadow_projection_row_sha256"] = _shadow_projection_row_sha256(row)
    return row


def _apply_projection_value_gate(
    *,
    explanation: BackfillDecisionExplanation,
    cell: Mapping[str, str],
    current_written: bool,
    current_value: float | None,
) -> _ProjectionValueGateResult:
    if current_written:
        return _ProjectionValueGateResult(
            explanation=explanation,
            projected_value=current_value,
            projected_written=True,
        )
    if explanation.decision != "accept":
        projected_value = (
            _projected_cell_value(cell)
            if _IDENTITY_SUPPORTED_REVIEW_REASON in explanation.reasons
            else None
        )
        return _ProjectionValueGateResult(
            explanation=explanation,
            projected_value=projected_value,
            projected_written=False,
        )
    projected_value = _projected_cell_value(cell)
    if projected_value is None:
        explanation = BackfillDecisionExplanation(
            decision="context",
            reasons=(*explanation.reasons, "missing_projected_matrix_value"),
            warnings=(
                *explanation.warnings,
                "projection_accept_without_positive_area",
            ),
            production_gap=explanation.production_gap,
        )
        return _ProjectionValueGateResult(
            explanation=explanation,
            projected_value=None,
            projected_written=False,
        )
    return _ProjectionValueGateResult(
        explanation=explanation,
        projected_value=projected_value,
        projected_written=True,
    )


def _shadow_projection_row_sha256(row: Mapping[str, str]) -> str:
    payload = {
        column: text_value(row.get(column))
        for column in SHADOW_PRODUCTION_PROJECTION_COLUMNS
        if column not in _PRODUCT_PROVENANCE_HASH_EXCLUDED_COLUMNS
    }
    return _sha256_json_payload(payload)


def canonical_shadow_projection_sha256(rows: Iterable[Mapping[str, str]]) -> str:
    payload = [
        {
            column: text_value(row.get(column))
            for column in SHADOW_PRODUCTION_PROJECTION_COLUMNS
            if column not in _PRODUCT_PROVENANCE_HASH_EXCLUDED_COLUMNS
        }
        for row in rows
    ]
    return _sha256_json_payload(payload)


def _sha256_json_payload(payload: object) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _shadow_projection_decision(
    *,
    gate_row: Mapping[str, str],
    cell: Mapping[str, str],
    decision: ProductionCellDecision | None,
    review_backfilled: bool,
    current_written: bool,
) -> BackfillDecisionExplanation:
    warnings = _warnings(cell, gate_row)
    if current_written:
        return decision_explanation("context", "already_written_current_matrix")
    if not review_backfilled:
        reason = (
            "missing_production_decision"
            if decision is None
            else "not_review_backfilled_cell"
        )
        return decision_explanation("context", reason, warnings=warnings)

    if _int_text(gate_row.get("detected_cell_count")) <= 0:
        return decision_explanation("block", "no_detected_anchor", warnings=warnings)
    if not _has_ms1_peak_segment(cell):
        return decision_explanation(
            "block",
            "missing_ms1_peak_segment",
            warnings=warnings,
        )
    if not _request_window_overlap(cell, gate_row):
        return decision_explanation(
            "block",
            "outside_request_window",
            warnings=warnings,
        )

    product_authorized = _product_authorized_same_peak_backfill(cell)
    current_blocker = _current_production_hard_blocker(decision)
    if current_blocker:
        return decision_explanation("block", current_blocker, warnings=warnings)
    cell_blocker = _cell_hypothesis_blocker(
        cell,
        product_authorized=product_authorized,
    )
    if cell_blocker:
        return decision_explanation("block", cell_blocker, warnings=warnings)

    blockers = _hard_blocker_tokens(gate_row)
    if blockers:
        return decision_explanation("block", blockers, warnings=warnings)
    gate_status = text_value(gate_row.get("evidence_gate_status"))
    if (
        not product_authorized
        and gate_status
        and gate_status not in PROJECTION_ACCEPT_GATE_STATUSES
    ):
        return decision_explanation(
            "context",
            "evidence_gate_requires_review",
            warnings=warnings,
        )
    soft_blockers = _soft_blocker_tokens(gate_row)
    if soft_blockers:
        return decision_explanation(
            "context",
            "challenge_blockers_require_review",
            warnings=warnings,
        )
    if not product_authorized:
        if _identity_supported_by_review(gate_row):
            return decision_explanation(
                "context",
                _IDENTITY_SUPPORTED_REVIEW_REASON,
                warnings=warnings,
            )
        return decision_explanation(
            "context",
            "missing_product_authorized_evidence_chain",
            warnings=warnings,
        )
    if _same_peak_multi_claim(cell):
        return decision_explanation(
            "accept",
            "product_authorized_same_peak_backfill",
            warnings=warnings,
        )
    return decision_explanation(
        "accept",
        "product_authorized_same_peak_backfill",
        warnings=warnings,
    )


def _product_authorized_same_peak_backfill(cell: Mapping[str, str]) -> bool:
    if not _product_authority_present(
        status=cell.get("backfill_ms1_product_authority_status"),
        scope=cell.get("backfill_ms1_product_authority_scope"),
        source=cell.get("backfill_ms1_product_authority_source"),
    ):
        return False
    if _normalize(cell.get("backfill_ms1_pattern_status")) not in _SUPPORT_STATUSES:
        return False
    if (
        _normalize(cell.get("backfill_ms1_pattern_evidence_level"))
        != _MS1_SAME_PEAK_LEVEL
    ):
        return False
    if not (
        _MS1_SAME_PEAK_SUPPORT_REASONS
        & set(split_semicolon_labels(cell.get("backfill_evidence_reason")))
    ):
        return False
    return True


def _identity_supported_by_review(gate_row: Mapping[str, str]) -> bool:
    gate_status = text_value(gate_row.get("evidence_gate_status"))
    components = set(split_semicolon_labels(gate_row.get("support_components")))
    return (
        gate_status in PROJECTION_ACCEPT_GATE_STATUSES
        and _SEED_REQUEST_COMPONENT in components
        and _MS1_SHAPE_SUPPORT_COMPONENT in components
    )


def _product_authority_chain_text(cell: Mapping[str, str]) -> str:
    return " | ".join(
        part
        for part in (
            _authority_component_text(
                cell,
                label="MS1",
                prefix="backfill_ms1",
                status_key="backfill_ms1_pattern_status",
                level_key="backfill_ms1_pattern_evidence_level",
            ),
            _authority_component_text(
                cell,
                label="candidateMS2(optional)",
                prefix="backfill_candidate_ms2",
                status_key="backfill_candidate_ms2_pattern_status",
                level_key="backfill_candidate_ms2_evidence_level",
            ),
            _same_peak_reason_text(cell),
        )
        if part
    )


def _authority_component_text(
    cell: Mapping[str, str],
    *,
    label: str,
    prefix: str,
    status_key: str,
    level_key: str,
) -> str:
    authority_status = text_value(cell.get(f"{prefix}_product_authority_status"))
    if not authority_status:
        return ""
    pattern_status = text_value(cell.get(status_key)) or "no_pattern_status"
    evidence_level = text_value(cell.get(level_key)) or "no_evidence_level"
    scope = text_value(cell.get(f"{prefix}_product_authority_scope"))
    source = text_value(cell.get(f"{prefix}_product_authority_source"))
    reason = text_value(cell.get(f"{prefix}_product_authority_reason"))
    parts = [
        label,
        authority_status,
        pattern_status,
        evidence_level,
    ]
    if scope:
        parts.append(scope)
    if reason:
        parts.append(reason)
    elif source:
        parts.append(source)
    return ":".join(parts)


def _same_peak_reason_text(cell: Mapping[str, str]) -> str:
    reasons = set(split_semicolon_labels(cell.get("backfill_evidence_reason")))
    matched = tuple(sorted(_MS1_SAME_PEAK_SUPPORT_REASONS & reasons))
    if not matched:
        return ""
    return "same_peak_reason:" + ";".join(matched)


def _product_authority_present(
    *,
    status: object,
    scope: object,
    source: object,
) -> bool:
    return (
        _normalize(status) == _PRODUCT_AUTHORIZED_STATUS
        and _normalize(scope) == _PRODUCT_AUTHORIZED_SCOPE
        and bool(_normalize(source))
    )


def _normalize(value: object) -> str:
    return text_value(value).strip().lower().replace(" ", "_")


def _warnings(
    cell: Mapping[str, str],
    gate_row: Mapping[str, str],
) -> tuple[str, ...]:
    warnings: list[str] = []
    if _same_peak_multi_claim(cell):
        warnings.append("same_peak_multi_claim")
    ratio = optional_float(cell.get("local_window_to_global_max_ratio"))
    if ratio is not None and ratio <= 0.5:
        warnings.append("low_dominance")
    for token in split_semicolon_labels(gate_row.get("challenge_blockers")):
        if token and token not in HARD_BLOCKER_TOKENS:
            warnings.append(token)
    return tuple(dict.fromkeys(warnings))


def _current_production_hard_blocker(
    decision: ProductionCellDecision | None,
) -> str:
    if decision is None:
        return ""
    return (
        decision.blank_reason
        if decision.blank_reason in CURRENT_PRODUCTION_HARD_BLOCKERS
        else ""
    )


def _cell_hypothesis_blocker(
    cell: Mapping[str, str],
    *,
    product_authorized: bool = False,
) -> str:
    tokens = " ".join(
        text_value(cell.get(key)).lower().replace(" ", "_")
        for key in (
            "group_claim_state",
            "consolidation_state",
            "peak_hypothesis_status",
            "product_selection_blocker",
            "rt_mode_status",
            "activation_product_effect",
            "activation_contract_rule_id",
            "activation_reason",
            "reason",
            "backfill_evidence_reason",
        )
    )
    hard_markers: tuple[str, ...] = (
        "primary_loser",
        "wrong_peak",
        "cross_mode_rescue_blocked",
        "mode_split_required",
        "consolidation_no_go",
    )
    if not product_authorized:
        hard_markers = ("duplicate_loser", *hard_markers)
    if any(marker in tokens for marker in hard_markers):
        return BACKFILL_HYPOTHESIS_BLOCKED_REASON
    return ""


def _hard_blocker_tokens(gate_row: Mapping[str, str]) -> tuple[str, ...]:
    tokens = split_semicolon_labels(gate_row.get("challenge_blockers"))
    return tuple(token for token in tokens if token in HARD_BLOCKER_TOKENS)


def _soft_blocker_tokens(gate_row: Mapping[str, str]) -> tuple[str, ...]:
    tokens = split_semicolon_labels(gate_row.get("challenge_blockers"))
    return tuple(token for token in tokens if token not in HARD_BLOCKER_TOKENS)


def _same_peak_multi_claim(cell: Mapping[str, str]) -> bool:
    text = " ".join(
        text_value(cell.get(key)).lower()
        for key in (
            "gap_fill_state",
            "gap_fill_reason",
            "reason",
            "group_claim_state",
            "consolidation_state",
        )
    )
    return "duplicate" in text or "same_peak_multi_claim" in text


def _request_window_overlap(
    cell: Mapping[str, str],
    gate_row: Mapping[str, str],
) -> bool:
    if not _has_ms1_peak_segment(cell):
        return False
    start = optional_float(cell.get("peak_start_rt"))
    end = optional_float(cell.get("peak_end_rt"))
    apex = optional_float(cell.get("apex_rt"))
    gate_start = optional_float(gate_row.get("suggested_rt_min"))
    gate_end = optional_float(gate_row.get("suggested_rt_max"))
    if None in (start, end, gate_start, gate_end):
        return False
    assert start is not None
    assert end is not None
    assert gate_start is not None
    assert gate_end is not None
    overlaps = end >= gate_start and start <= gate_end
    if apex is None:
        return overlaps
    return overlaps and gate_start <= apex <= gate_end


def _validate_gate_rows(rows: Sequence[Mapping[str, str]]) -> None:
    for index, row in enumerate(rows, start=1):
        missing = [
            column
            for column in RETAINED_GATE_REQUIRED_COLUMNS
            if column not in row
        ]
        if missing:
            raise ValueError(
                "retained gate row "
                f"{index} missing required shadow projection columns: "
                + ", ".join(missing),
            )


def _has_ms1_peak_segment(cell: Mapping[str, str]) -> bool:
    return (
        optional_float(cell.get("peak_start_rt")) is not None
        and optional_float(cell.get("peak_end_rt")) is not None
    )


def _projected_cell_value(cell: Mapping[str, str]) -> float | None:
    for key in ("primary_matrix_area", "area"):
        value = optional_float(cell.get(key))
        if value is not None and value > 0:
            return value
    return None


def _local_global_ratio_text(
    cell: Mapping[str, str],
    overlay_row: Mapping[str, str],
) -> str:
    for key in (
        "local_window_to_global_max_ratio",
        "absolute_own_max_shape_supported_fraction",
    ):
        value = optional_float(cell.get(key))
        if value is None:
            value = optional_float(overlay_row.get(key))
        if value is not None:
            return _number_text(value)
    return ""


def _selected_overlay_row(
    rows: Sequence[Mapping[str, str]],
    *,
    seed_group_id: str,
) -> Mapping[str, str]:
    return select_backfill_overlay_row(
        rows,
        seed_group_id=seed_group_id,
        support_verdict=_MS1_SHAPE_SUPPORT_COMPONENT,
    )


def _group_by_family(
    rows: Iterable[Mapping[str, str]],
) -> dict[str, tuple[Mapping[str, str], ...]]:
    return rows_by_text_field(rows, "feature_family_id")


def _summary(
    rows: Sequence[Mapping[str, str]],
    *,
    gate_projection_summary: Mapping[str, object],
    source_run_id: str,
    source_review_sha256: str,
    source_cell_sha256: str,
    source_gate_sha256: str,
    source_matrix_sha256: str,
    source_overlay_artifacts: Sequence[str],
    source_overlay_sha256s: Sequence[str],
    source_ms1_pattern_coherence_artifacts: Sequence[str],
    source_ms1_pattern_coherence_sha256s: Sequence[str],
) -> dict[str, object]:
    decisions = Counter(row["shadow_decision"] for row in rows)
    current_written = sum(row["current_matrix_written"] == "TRUE" for row in rows)
    projected_written = sum(row["projected_matrix_written"] == "TRUE" for row in rows)
    projected_new = sum(
        row["current_matrix_written"] == "FALSE"
        and row["projected_matrix_written"] == "TRUE"
        for row in rows
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "validation_label": "shadow_projection_only",
        "source_run_id": source_run_id,
        **gate_projection_summary,
        "row_count": len(rows),
        "decision_counts": dict(sorted(decisions.items())),
        "current_matrix_written_count": current_written,
        "projected_matrix_written_count": projected_written,
        "projected_new_write_count": projected_new,
        "review_backfilled_target_count": sum(
            row["review_backfilled_cell"] == "TRUE" for row in rows
        ),
        "source_review_sha256": source_review_sha256,
        "source_cell_sha256": source_cell_sha256,
        "source_gate_sha256": source_gate_sha256,
        "source_matrix_sha256": source_matrix_sha256,
        "source_overlay_artifacts": tuple(source_overlay_artifacts),
        "source_overlay_sha256s": tuple(source_overlay_sha256s),
        "source_ms1_pattern_coherence_artifacts": tuple(
            source_ms1_pattern_coherence_artifacts
        ),
        "source_ms1_pattern_coherence_sha256s": tuple(
            source_ms1_pattern_coherence_sha256s
        ),
        "current_matrix_source": "production_decision_snapshot",
        "alignment_matrix_cross_checked": False,
        "matrix_contract_changed": False,
        "product_behavior_changed": False,
    }


def _gate_projection_summary(
    gate_rows: Sequence[Mapping[str, str]],
) -> dict[str, object]:
    reasons = Counter(
        reason
        for row in gate_rows
        if (reason := _unprojectable_gate_reason(row))
    )
    unprojectable = sum(reasons.values())
    return {
        "gate_row_count": len(gate_rows),
        "projectable_gate_row_count": len(gate_rows) - unprojectable,
        "unprojectable_gate_row_count": unprojectable,
        "unprojectable_gate_reasons": dict(sorted(reasons.items())),
    }


def _unprojectable_gate_reason(row: Mapping[str, str]) -> str:
    if not text_value(row.get("feature_family_id")):
        return "missing_feature_family_id"
    if not text_value(row.get("seed_group_id")):
        return "missing_seed_group_id"
    if not split_semicolon_labels(row.get("seed_source_samples")):
        seed_basis = text_value(row.get("seed_group_basis"))
        if seed_basis == "missing_seed_audit":
            return "missing_seed_audit"
        return "missing_seed_source_samples"
    return ""


def _int_text(value: object) -> int:
    parsed = optional_float(value)
    if parsed is None:
        return 0
    return int(parsed)


def _number_text(value: object) -> str:
    parsed = optional_float(value)
    if parsed is None:
        return ""
    return f"{parsed:.6g}"


def _bool_text(value: bool) -> str:
    return "TRUE" if value else "FALSE"


def _row_sort_key(row: Mapping[str, str]) -> tuple[str, str, str]:
    return (
        text_value(row.get("feature_family_id")),
        text_value(row.get("seed_group_id")),
        text_value(row.get("sample_stem")),
    )
