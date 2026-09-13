"""Input loading and artifact metadata for the reconciliation gallery."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.alignment.shared_peak_identity_explanation.schema import (
    ACTIVATION_APPLICATION_SUMMARY_COLUMNS,
    ACTIVATION_VALUE_DELTA_COLUMNS,
)
from xic_extractor.diagnostics.backfill_shadow_policy import (
    BACKFILL_SHADOW_POLICY_COLUMNS,
)
from xic_extractor.diagnostics.diagnostic_io import (
    file_sha256,
    read_tsv_required,
)
from xic_extractor.diagnostics.shadow_production_projection import (
    SHADOW_PRODUCTION_PROJECTION_COLUMNS,
)

Rows = tuple[dict[str, str], ...]

_REQUIRED_ALIGNMENT_REVIEW_COLUMNS = (
    "feature_family_id",
    "group_construction_role",
    "neutral_loss_tag",
    "detected_count",
    "quantifiable_detected_count",
    "identity_decision",
    "identity_confidence",
    "primary_evidence",
    "identity_reason",
    "quantifiable_rescue_count",
    "accepted_rescue_count",
    "include_in_primary_matrix",
    "row_flags",
    "reason",
)
_REQUIRED_ALIGNMENT_CELLS_COLUMNS = (
    "feature_family_id",
    "sample_stem",
    "status",
    "primary_matrix_area_source",
    "apex_rt",
    "peak_start_rt",
    "peak_end_rt",
    "rt_delta_sec",
    "trace_quality",
    "scan_support_score",
    "gap_fill_state",
    "gap_fill_reason",
)
_REQUIRED_ALIGNMENT_OWNER_BACKFILL_SEED_AUDIT_COLUMNS = (
    "feature_family_id",
    "sample_stem",
    "backfill_seed_mz",
    "backfill_seed_rt",
    "backfill_request_rt_min",
    "backfill_request_rt_max",
    "backfill_request_ppm",
)
_REQUIRED_RETAINED_BACKFILL_GATE_COLUMNS = (
    "feature_family_id",
    "seed_group_id",
    "evidence_gate_status",
    "recommended_action",
    "support_components",
    "challenge_blockers",
    "missing_evidence",
)
_REQUIRED_SHIFT_AWARE_STANDARD_PEAK_GATE_COLUMNS = (
    "feature_family_id",
    "standard_peak_gate_call",
    "standard_peak_gate_reasons",
    "standard_peak_gate_blockers",
    "calibration_outcome",
    "min_shape_r_after_best_shift",
    "max_abs_shift_sec",
)
_REQUIRED_TARGETED_ISTD_BENCHMARK_SUMMARY_COLUMNS = (
    "target_label",
    "role",
    "active_tag",
    "targeted_positive_count",
    "selected_feature_id",
    "untargeted_positive_count",
    "coverage_minimum",
    "status",
    "failure_modes",
)
_INPUT_ARTIFACT_LABEL_BY_KEY = {
    "alignment_review_tsv": "alignment_review.tsv",
    "alignment_cells_tsv": "cell evidence TSV",
    "alignment_matrix_tsv": "alignment_matrix.tsv",
    "backfill_seed_audit_tsv": "alignment_owner_backfill_seed_audit.tsv",
    "overlay_batch_summary_tsvs": "family_ms1_overlay_batch_summary.tsv",
    "shift_aware_same_pattern_tsvs": "source_family_best_shift_summary.tsv",
    "shift_aware_standard_peak_gate_tsvs": (
        "shift_aware_standard_peak_gate_calibration.tsv"
    ),
    "seed_aware_family_tsv": "seed_aware_backfill_review_families.tsv",
    "seed_aware_summary_tsv": "seed_aware_backfill_review_summary.tsv",
    "candidate_gate_tsv": "alignment_production_candidate_gate.tsv",
    "retained_backfill_gate_tsv": "alignment_retained_backfill_evidence_gate.tsv",
    "tier2_trace_evidence_tsv": "alignment_tier2_trace_evidence.tsv",
    "shadow_policy_cells_tsv": "backfill_shadow_policy_cells.tsv",
    "shadow_projection_cells_tsv": "shadow_production_projection_cells.tsv",
    "activation_application_summary_tsv": "activation_application_summary.tsv",
    "activation_value_delta_tsv": "activation_value_delta.tsv",
    "targeted_istd_benchmark_summary_tsv": "targeted_istd_benchmark_summary.tsv",
}


@dataclass(frozen=True)
class ReconciliationInputRows:
    review_rows: Rows
    cell_rows: Rows
    matrix_rows: Rows
    seed_audit_rows: Rows
    overlay_rows: Rows
    shift_aware_rows: Rows
    standard_peak_gate_rows: Rows
    seed_aware_family_rows: Rows
    seed_aware_summary_rows: Rows
    candidate_gate_rows: Rows
    retained_gate_rows: Rows
    tier2_trace_evidence_rows: Rows
    shadow_policy_rows: Rows
    shadow_projection_rows: Rows
    activation_application_summary_rows: Rows
    activation_value_delta_rows: Rows
    target_benchmark_rows: Rows
    input_artifacts: dict[str, object]


def load_reconciliation_input_rows(
    *,
    alignment_review_tsv: Path,
    alignment_cells_tsv: Path,
    alignment_matrix_tsv: Path | None = None,
    backfill_seed_audit_tsv: Path | None = None,
    overlay_batch_summary_tsvs: Sequence[Path] = (),
    shift_aware_same_pattern_tsvs: Sequence[Path] = (),
    shift_aware_standard_peak_gate_tsvs: Sequence[Path] = (),
    seed_aware_family_tsv: Path | None = None,
    seed_aware_summary_tsv: Path | None = None,
    candidate_gate_tsv: Path | None = None,
    retained_backfill_gate_tsv: Path | None = None,
    tier2_trace_evidence_tsv: Path | None = None,
    shadow_policy_cells_tsv: Path | None = None,
    shadow_projection_cells_tsv: Path | None = None,
    activation_application_summary_tsv: Path | None = None,
    activation_value_delta_tsv: Path | None = None,
    targeted_istd_benchmark_summary_tsv: Path | None = None,
    source_run_id: str = "",
) -> ReconciliationInputRows:
    review_rows = _read_required_tsv(
        alignment_review_tsv,
        _REQUIRED_ALIGNMENT_REVIEW_COLUMNS,
    )
    cell_rows = _read_required_tsv(
        alignment_cells_tsv,
        _REQUIRED_ALIGNMENT_CELLS_COLUMNS,
    )
    matrix_rows = (
        _read_required_tsv(alignment_matrix_tsv, ())
        if alignment_matrix_tsv is not None
        else ()
    )
    seed_audit_rows = (
        _read_required_tsv(
            backfill_seed_audit_tsv,
            _REQUIRED_ALIGNMENT_OWNER_BACKFILL_SEED_AUDIT_COLUMNS,
        )
        if backfill_seed_audit_tsv is not None
        else ()
    )
    overlay_rows: list[dict[str, str]] = []
    for path in overlay_batch_summary_tsvs:
        overlay_rows.extend(
            _read_required_tsv(
                path,
                (
                    "feature_family_id",
                    "family_verdict",
                    "png_path",
                ),
            ),
        )
    shift_aware_rows: list[dict[str, str]] = []
    for path in shift_aware_same_pattern_tsvs:
        shift_aware_rows.extend(
            _read_required_tsv(
                path,
                (
                    "feature_family_id",
                    "source_family",
                    "is_reference",
                    "shift_basis",
                    "shift_to_reference_sec",
                    "shape_similarity_to_reference_after_group_shift",
                ),
            ),
        )
    standard_peak_gate_rows: list[dict[str, str]] = []
    for path in shift_aware_standard_peak_gate_tsvs:
        standard_peak_gate_rows.extend(
            _read_required_tsv(
                path,
                _REQUIRED_SHIFT_AWARE_STANDARD_PEAK_GATE_COLUMNS,
            ),
        )
    seed_aware_family_rows = (
        _read_required_tsv(
            seed_aware_family_tsv,
            ("feature_family_id", "review_classification"),
        )
        if seed_aware_family_tsv is not None
        else ()
    )
    seed_aware_summary_rows = (
        _read_required_tsv(seed_aware_summary_tsv, ("feature_family_id",))
        if seed_aware_summary_tsv is not None
        else ()
    )
    candidate_gate_rows = (
        _read_required_tsv(
            candidate_gate_tsv,
            (
                "feature_family_id",
                "candidate_gate_status",
                "support_components",
                "challenge_blockers",
            ),
        )
        if candidate_gate_tsv is not None
        else ()
    )
    retained_gate_rows = (
        _read_required_tsv(
            retained_backfill_gate_tsv,
            _REQUIRED_RETAINED_BACKFILL_GATE_COLUMNS,
        )
        if retained_backfill_gate_tsv is not None
        else ()
    )
    tier2_trace_evidence_rows = (
        _read_required_tsv(tier2_trace_evidence_tsv, ("feature_family_id",))
        if tier2_trace_evidence_tsv is not None
        else ()
    )
    shadow_policy_rows = (
        _read_required_tsv(
            shadow_policy_cells_tsv,
            BACKFILL_SHADOW_POLICY_COLUMNS,
        )
        if shadow_policy_cells_tsv is not None
        else ()
    )
    shadow_projection_rows = (
        _read_required_tsv(
            shadow_projection_cells_tsv,
            SHADOW_PRODUCTION_PROJECTION_COLUMNS,
        )
        if shadow_projection_cells_tsv is not None
        else ()
    )
    activation_application_summary_rows = (
        _read_required_tsv(
            activation_application_summary_tsv,
            ACTIVATION_APPLICATION_SUMMARY_COLUMNS,
        )
        if activation_application_summary_tsv is not None
        else ()
    )
    activation_value_delta_rows = (
        _read_required_tsv(
            activation_value_delta_tsv,
            ACTIVATION_VALUE_DELTA_COLUMNS,
        )
        if activation_value_delta_tsv is not None
        else ()
    )
    target_benchmark_rows = (
        _read_required_tsv(
            targeted_istd_benchmark_summary_tsv,
            _REQUIRED_TARGETED_ISTD_BENCHMARK_SUMMARY_COLUMNS,
        )
        if targeted_istd_benchmark_summary_tsv is not None
        else ()
    )
    input_artifacts = _input_artifact_summary(
        alignment_review_tsv=alignment_review_tsv,
        alignment_cells_tsv=alignment_cells_tsv,
        alignment_matrix_tsv=alignment_matrix_tsv,
        backfill_seed_audit_tsv=backfill_seed_audit_tsv,
        overlay_batch_summary_tsvs=overlay_batch_summary_tsvs,
        shift_aware_same_pattern_tsvs=shift_aware_same_pattern_tsvs,
        shift_aware_standard_peak_gate_tsvs=shift_aware_standard_peak_gate_tsvs,
        seed_aware_family_tsv=seed_aware_family_tsv,
        seed_aware_summary_tsv=seed_aware_summary_tsv,
        candidate_gate_tsv=candidate_gate_tsv,
        retained_backfill_gate_tsv=retained_backfill_gate_tsv,
        tier2_trace_evidence_tsv=tier2_trace_evidence_tsv,
        shadow_policy_cells_tsv=shadow_policy_cells_tsv,
        shadow_projection_cells_tsv=shadow_projection_cells_tsv,
        activation_application_summary_tsv=activation_application_summary_tsv,
        activation_value_delta_tsv=activation_value_delta_tsv,
        targeted_istd_benchmark_summary_tsv=targeted_istd_benchmark_summary_tsv,
        source_run_id=source_run_id,
    )
    input_artifacts.update(
        _input_artifact_hashes(
            alignment_review_tsv=alignment_review_tsv,
            alignment_cells_tsv=alignment_cells_tsv,
            alignment_matrix_tsv=alignment_matrix_tsv,
            backfill_seed_audit_tsv=backfill_seed_audit_tsv,
            overlay_batch_summary_tsvs=overlay_batch_summary_tsvs,
            shift_aware_same_pattern_tsvs=shift_aware_same_pattern_tsvs,
            shift_aware_standard_peak_gate_tsvs=(
                shift_aware_standard_peak_gate_tsvs
            ),
            seed_aware_family_tsv=seed_aware_family_tsv,
            seed_aware_summary_tsv=seed_aware_summary_tsv,
            candidate_gate_tsv=candidate_gate_tsv,
            retained_backfill_gate_tsv=retained_backfill_gate_tsv,
            tier2_trace_evidence_tsv=tier2_trace_evidence_tsv,
            shadow_policy_cells_tsv=shadow_policy_cells_tsv,
            shadow_projection_cells_tsv=shadow_projection_cells_tsv,
            activation_application_summary_tsv=activation_application_summary_tsv,
            activation_value_delta_tsv=activation_value_delta_tsv,
            targeted_istd_benchmark_summary_tsv=targeted_istd_benchmark_summary_tsv,
        ),
    )
    return ReconciliationInputRows(
        review_rows=review_rows,
        cell_rows=cell_rows,
        matrix_rows=matrix_rows,
        seed_audit_rows=seed_audit_rows,
        overlay_rows=tuple(overlay_rows),
        shift_aware_rows=tuple(shift_aware_rows),
        standard_peak_gate_rows=tuple(standard_peak_gate_rows),
        seed_aware_family_rows=seed_aware_family_rows,
        seed_aware_summary_rows=seed_aware_summary_rows,
        candidate_gate_rows=candidate_gate_rows,
        retained_gate_rows=retained_gate_rows,
        tier2_trace_evidence_rows=tier2_trace_evidence_rows,
        shadow_policy_rows=shadow_policy_rows,
        shadow_projection_rows=shadow_projection_rows,
        activation_application_summary_rows=activation_application_summary_rows,
        activation_value_delta_rows=activation_value_delta_rows,
        target_benchmark_rows=target_benchmark_rows,
        input_artifacts=input_artifacts,
    )


def _read_required_tsv(
    path: Path | None,
    required_columns: Sequence[str],
) -> tuple[dict[str, str], ...]:
    if path is None:
        return ()
    try:
        return read_tsv_required(path, required_columns)
    except FileNotFoundError as exc:
        raise ValueError(f"Required TSV not found: {path}") from exc


def _input_artifact_summary(**paths: object) -> dict[str, object]:
    summary: dict[str, object] = {}
    for key, value in paths.items():
        if isinstance(value, Path):
            summary[key] = str(value)
        elif isinstance(value, Sequence) and not isinstance(value, str):
            summary[key] = [str(item) for item in value if isinstance(item, Path)]
        elif value:
            summary[key] = value
    return summary


def _input_artifact_hashes(**paths: object) -> dict[str, object]:
    hashes: dict[str, object] = {}
    for key, value in paths.items():
        if value is None:
            continue
        if isinstance(value, Path):
            hashes[f"{key.removesuffix('_tsv')}_sha256"] = file_sha256(
                value,
                uppercase=False,
            )
            continue
        if isinstance(value, Sequence) and not isinstance(value, str):
            artifact_hashes = [
                {"path": str(path), "sha256": file_sha256(path, uppercase=False)}
                for path in value
                if isinstance(path, Path)
            ]
            if artifact_hashes:
                key_base = key.removesuffix("_tsvs").removesuffix("_tsv")
                hashes[f"{key_base}_hashes"] = artifact_hashes
    return hashes
