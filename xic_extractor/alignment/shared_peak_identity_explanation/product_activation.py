from __future__ import annotations

import csv
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.alignment.backfill_evidence_projection import (
    BACKFILL_PROJECTION_COLUMNS,
    project_backfill_evidence_to_cells,
)
from xic_extractor.alignment.matrix_output_contract import (
    serialize_public_matrix_table,
)
from xic_extractor.alignment.primary_matrix_area import (
    MISSING_ASLS_PRIMARY_AREA,
    MS1_MORPHOLOGY_PRIMARY_MATRIX_AREA_SOURCE,
)
from xic_extractor.alignment.product_matrix import (
    ALIGNMENT_MATRIX_IDENTITY_COLUMNS,
)
from xic_extractor.tabular_io import write_tsv

from .activation_matrix_projection import (
    FormalMatrixStats,
    LegacyRtRowReference,
    _accepted_samples,
    _accepted_samples_for_source_family,
    _alignment_matrix_identity_rows,
    _append_unique_cell,
    _apply_rt_mode_centers,
    _decision_peak_hypothesis_id,
    _decision_row_identity_basis,
    _evidence_status,
    _formal_matrix_header,
    _format_float,
    _has_any_sample_value,
    _identity_metadata_row,
    _legacy_oracle_match_is_ambiguous,
    _legacy_projection_hypothesis_id,
    _load_legacy_rt_row_oracle,
    _match_legacy_rt_row_reference,
    _matrix_center_rt,
    _matrix_center_rt_basis,
    _matrix_contains_source_family,
    _matrix_input_by_family,
    _matrix_row_key_for_decision,
    _matrix_row_keys_for_source_family,
    _matrix_source_family_id,
    _matrix_source_family_ids,
    _new_matrix_row,
    _new_peak_hypothesis_matrix_row,
    _parent_peak_hypothesis_id,
    _peak_hypothesis_matrix_rows,
    _pop_matrix_rows_for_source_family,
    _positive_row_index,
    _product_matrix_header,
    _product_matrix_rows_from_hypotheses,
    _public_matrix_row_key,
    _public_matrix_rows_by_family,
    _reject_multi_family_hypothesis_collapse,
    _rt_mode_by_key,
    _rt_mode_is_product_coordinate_authority,
    _rt_mode_matches_hypothesis,
    _RtCenterAccumulator,
    _select_conflicting_matrix_value,
    _single_source_family_id,
    _split_evaluation_status,
    _to_float,
    _validate_identity_coordinate,
    _validate_public_identity_basis,
)
from .schema import (
    ACTIVATION_APPLICATION_SCHEMA_VERSION,
    ACTIVATION_APPLICATION_SUMMARY_COLUMNS,
    ACTIVATION_CELL_AUDIT_COLUMNS,
    ACTIVATION_REVIEW_AUDIT_COLUMNS,
    ACTIVATION_VALUE_DELTA_COLUMNS,
    ACTIVATION_VALUE_DELTA_SCHEMA_VERSION,
    ACTIVATION_VALUE_INPUT_COLUMNS,
)

ACTIVE_PRIMARY_MATRIX_AREA_SOURCES = frozenset(
    (
        MS1_MORPHOLOGY_PRIMARY_MATRIX_AREA_SOURCE,
    )
)


@dataclass(frozen=True)
class ActivationApplicationOutputs:
    matrix_tsv: Path
    review_tsv: Path
    cells_tsv: Path
    summary_tsv: Path
    value_delta_tsv: Path
    hypothesis_identity_tsv: Path | None = None
    matrix_identity_tsv: Path | None = None


@dataclass(frozen=True)
class MatrixActivationApplicationOutputs:
    matrix_tsv: Path
    summary_tsv: Path
    value_delta_tsv: Path
    hypothesis_identity_tsv: Path
    matrix_identity_tsv: Path


__all__ = [
    "ACTIVE_PRIMARY_MATRIX_AREA_SOURCES",
    "ActivationApplicationOutputs",
    "FormalMatrixStats",
    "LegacyRtRowReference",
    "MatrixActivationApplicationOutputs",
    "_RtCenterAccumulator",
    "_accepted_samples",
    "_accepted_samples_for_source_family",
    "_alignment_matrix_identity_rows",
    "_append_unique_cell",
    "_apply_rt_mode_centers",
    "_decision_peak_hypothesis_id",
    "_decision_row_identity_basis",
    "_evidence_status",
    "_format_float",
    "_formal_matrix_header",
    "_has_any_sample_value",
    "_identity_metadata_row",
    "_legacy_oracle_match_is_ambiguous",
    "_legacy_projection_hypothesis_id",
    "_load_legacy_rt_row_oracle",
    "_match_legacy_rt_row_reference",
    "_matrix_center_rt",
    "_matrix_center_rt_basis",
    "_matrix_contains_source_family",
    "_matrix_input_by_family",
    "_matrix_row_key_for_decision",
    "_matrix_row_keys_for_source_family",
    "_matrix_source_family_id",
    "_matrix_source_family_ids",
    "_new_matrix_row",
    "_new_peak_hypothesis_matrix_row",
    "_parent_peak_hypothesis_id",
    "_peak_hypothesis_matrix_rows",
    "_pop_matrix_rows_for_source_family",
    "_positive_row_index",
    "_product_matrix_header",
    "_product_matrix_rows_from_hypotheses",
    "_public_matrix_row_key",
    "_public_matrix_rows_by_family",
    "_reject_multi_family_hypothesis_collapse",
    "_rt_mode_by_key",
    "_rt_mode_is_product_coordinate_authority",
    "_rt_mode_matches_hypothesis",
    "_select_conflicting_matrix_value",
    "_single_source_family_id",
    "_split_evaluation_status",
    "_to_float",
    "_validate_identity_coordinate",
    "_validate_public_identity_basis",
    "apply_activation_to_alignment_matrix_outputs",
    "apply_activation_to_alignment_outputs",
]


def apply_activation_to_alignment_outputs(
    *,
    activation_decisions_tsv: Path,
    activation_acceptance_tsv: Path,
    alignment_matrix_tsv: Path,
    alignment_matrix_identity_tsv: Path | None = None,
    alignment_review_tsv: Path,
    alignment_cells_tsv: Path,
    output_dir: Path,
    require_acceptance_pass: bool = True,
    output_mode: str = "activated-copy",
    allow_overwrite_source: bool = False,
    legacy_rt_row_oracle_xlsx: Path | None = None,
    legacy_rt_row_oracle_mz_ppm: float = 20.0,
    legacy_rt_row_oracle_rt_tolerance_min: float = 1.0,
    require_complete_peak_hypothesis_identity: bool = False,
    exclude_family_projections: bool = True,
    candidate_ms2_pattern_rows: Sequence[Mapping[str, str]] = (),
    ms1_pattern_coherence_rows: Sequence[Mapping[str, str]] = (),
    qc_ms1_pattern_reference_rows: Sequence[Mapping[str, str]] = (),
    matrix_rt_drift_policy_rows: Sequence[Mapping[str, str]] = (),
    rt_mode_evidence_rows: Sequence[Mapping[str, str]] = (),
) -> ActivationApplicationOutputs:
    _validate_output_mode(output_mode)
    if output_mode != "formal":
        exclude_family_projections = False
    decisions = _read_tsv(activation_decisions_tsv)
    _validate_decisions_for_product_application(decisions)
    acceptance_rows = _read_tsv(activation_acceptance_tsv)
    if len(acceptance_rows) != 1:
        raise ValueError("activation acceptance TSV must contain exactly one row")
    acceptance = acceptance_rows[0]
    if require_acceptance_pass and acceptance.get("acceptance_status") != "pass":
        raise ValueError("activation acceptance must pass before product application")

    matrix_header, matrix_rows = _read_tsv_with_header(alignment_matrix_tsv)
    review_header, review_rows = _read_tsv_with_header(alignment_review_tsv)
    cells_header, cell_rows = _read_tsv_with_header(alignment_cells_tsv)
    _require_columns(
        review_header,
        (
            "feature_family_id",
            "neutral_loss_tag",
            "family_center_mz",
            "family_center_rt",
            "include_in_primary_matrix",
        ),
    )
    _require_columns(cells_header, ("feature_family_id", "sample_stem"))
    review_by_family = {row["feature_family_id"]: row for row in review_rows}
    sample_columns, matrix_by_family = _matrix_input_by_family(
        matrix_header=matrix_header,
        matrix_rows=matrix_rows,
        alignment_matrix_identity_tsv=alignment_matrix_identity_tsv,
        review_by_family=review_by_family,
    )
    if _should_project_backfill_evidence(
        cells_header,
        candidate_ms2_pattern_rows=candidate_ms2_pattern_rows,
        ms1_pattern_coherence_rows=ms1_pattern_coherence_rows,
        qc_ms1_pattern_reference_rows=qc_ms1_pattern_reference_rows,
        matrix_rt_drift_policy_rows=matrix_rt_drift_policy_rows,
    ):
        cell_rows = project_backfill_evidence_to_cells(
            cell_rows=cell_rows,
            candidate_ms2_pattern_rows=candidate_ms2_pattern_rows,
            ms1_pattern_coherence_rows=ms1_pattern_coherence_rows,
            qc_ms1_pattern_reference_rows=qc_ms1_pattern_reference_rows,
            matrix_rt_drift_policy_rows=matrix_rt_drift_policy_rows,
        )
        cells_header = _header_with_backfill_projection(cells_header)

    cells_by_key = {
        (row["feature_family_id"], row["sample_stem"]): row for row in cell_rows
    }
    cell_status_by_key = {
        key: row.get("status", "") for key, row in cells_by_key.items()
    }
    original_matrix_by_family = {
        family_id: dict(row) for family_id, row in matrix_by_family.items()
    }
    original_matrix_families = _matrix_source_family_ids(matrix_by_family)

    decision_by_key = {
        (row.get("feature_family_id", ""), row.get("sample_id", "")): row
        for row in decisions
        if row.get("sample_id") and row.get("sample_id") != "__family_context__"
    }
    matrix_effects: dict[tuple[str, str], str] = {}
    family_notes: dict[str, list[str]] = {}
    families_added: set[str] = set()
    family_blocked: set[str] = set()
    legacy_rt_row_oracle = _load_legacy_rt_row_oracle(legacy_rt_row_oracle_xlsx)

    for decision in decisions:
        family_id = decision.get("feature_family_id", "")
        sample_id = decision.get("sample_id", "")
        status = decision.get("activation_status", "")
        effect = decision.get("product_effect", "")
        if status == "auto_block" and effect == "block_family_promotion":
            family_blocked.add(family_id)
            _pop_matrix_rows_for_source_family(matrix_by_family, family_id)
            family_notes.setdefault(family_id, []).append("family_promotion_blocked")
            continue
        if sample_id == "__family_context__" or not sample_id:
            continue
        key = (family_id, sample_id)
        if status == "auto_block" and effect == "block_rescue_cell":
            row_key = _matrix_row_key_for_decision(
                matrix_by_family,
                family_id=family_id,
                sample_id=sample_id,
                decision=decision,
            )
            row = matrix_by_family.get(row_key) if row_key is not None else None
            if row is not None and sample_id in row and row.get(sample_id):
                row[sample_id] = ""
                matrix_effects[key] = "blanked"
            else:
                matrix_effects[key] = "block_no_existing_matrix_value"
            family_notes.setdefault(family_id, []).append("rescue_cell_blocked")
        elif status == "auto_activate":
            row_key = _matrix_row_key_for_decision(
                matrix_by_family,
                family_id=family_id,
                sample_id=sample_id,
                decision=decision,
            )
            row = matrix_by_family.get(row_key) if row_key is not None else None
            if row is None:
                row = _new_matrix_row(
                    family_id,
                    sample_columns=sample_columns,
                    review_row=review_by_family.get(family_id, {}),
                )
                matrix_by_family[family_id] = row
                families_added.add(family_id)
            source_cell = cells_by_key.get(key)
            value = _matrix_value_for_activation(source_cell)
            previous = row.get(sample_id, "")
            if previous:
                matrix_effects[key] = "unchanged"
                family_notes.setdefault(family_id, []).append("cell_auto_activated")
            elif value:
                row[sample_id] = value
                matrix_effects[key] = "written"
                family_notes.setdefault(family_id, []).append("cell_auto_activated")
            elif _has_untrusted_primary_or_raw_area(source_cell):
                matrix_effects[key] = MISSING_ASLS_PRIMARY_AREA
            else:
                matrix_effects[key] = "no_cell_area_available"

    matrix_by_family = {
        family_id: row
        for family_id, row in matrix_by_family.items()
        if _has_any_sample_value(row, sample_columns)
    }
    output_matrix_header: Sequence[str]
    formal_matrix_stats = FormalMatrixStats()
    hypothesis_identity_rows: list[dict[str, str]] = []
    if output_mode == "formal":
        hypothesis_identity_rows, formal_matrix_stats = (
            _peak_hypothesis_matrix_rows(
                matrix_by_family=matrix_by_family,
                review_by_family=review_by_family,
                decisions_by_key=decision_by_key,
                sample_columns=sample_columns,
                legacy_rt_row_oracle=legacy_rt_row_oracle,
                legacy_rt_row_oracle_mz_ppm=legacy_rt_row_oracle_mz_ppm,
                legacy_rt_row_oracle_rt_tolerance_min=(
                    legacy_rt_row_oracle_rt_tolerance_min
                ),
                include_family_projections=not exclude_family_projections,
                rt_mode_evidence_rows=rt_mode_evidence_rows,
            )
        )
        output_matrix_header = _product_matrix_header(sample_columns)
        output_matrix_rows = _product_matrix_rows_from_hypotheses(
            hypothesis_identity_rows,
            sample_columns,
        )
    else:
        output_matrix_header = matrix_header
        output_matrix_rows = [
            matrix_by_family[family_id] for family_id in sorted(matrix_by_family)
        ]
    output_review_rows = _activated_review_rows(
        review_rows=review_rows,
        review_header=review_header,
        decisions=decisions,
        matrix_by_family=matrix_by_family,
        sample_columns=sample_columns,
        cell_status_by_key=cell_status_by_key,
        matrix_effects=matrix_effects,
        family_notes=family_notes,
        family_blocked=family_blocked,
    )
    output_cell_rows = _activated_cell_rows(
        cell_rows=cell_rows,
        decisions_by_key=decision_by_key,
        matrix_effects=matrix_effects,
    )
    value_delta_rows = _value_delta_rows(
        decisions=decisions,
        original_matrix_by_family=original_matrix_by_family,
        matrix_by_family=matrix_by_family,
        cells_by_key=cells_by_key,
        sample_columns=sample_columns,
        matrix_effects=matrix_effects,
    )
    summary_row = _summary_row(
        acceptance=acceptance,
        decisions=decisions,
        output_mode=output_mode,
        input_matrix_rows=len(matrix_rows),
        output_matrix_rows=len(output_matrix_rows),
        formal_matrix_stats=formal_matrix_stats,
        matrix_effects=matrix_effects,
        families_added=families_added,
        families_removed=original_matrix_families
        - _matrix_source_family_ids(matrix_by_family),
    )
    if (
        require_complete_peak_hypothesis_identity
        and output_mode != "formal"
    ):
        raise ValueError(
            "--require-complete-peak-hypothesis-identity requires "
            "--output-mode formal"
        )
    if (
        require_complete_peak_hypothesis_identity
        and summary_row["canonical_row_identity_ready"] != "TRUE"
    ):
        raise ValueError(
            "complete PeakHypothesis identity requires canonical row identity "
            f"readiness; blockers={summary_row['canonical_row_identity_blockers']}"
        )

    if tuple(output_matrix_header[:2]) == ("Mz", "RT"):
        public_table = serialize_public_matrix_table(
            output_matrix_rows,
            sample_columns=sample_columns,
        )
        output_matrix_header = public_table.header
        output_matrix_rows = list(public_table.rows)

    output_dir.mkdir(parents=True, exist_ok=True)
    matrix_path, review_path, cells_path = _activated_output_paths(
        output_dir,
        output_mode=output_mode,
    )
    summary_path = output_dir / "activation_application_summary.tsv"
    value_delta_path = output_dir / "activation_value_delta.tsv"
    hypothesis_identity_path = (
        output_dir / "activation_hypothesis_identity.tsv"
        if output_mode == "formal"
        else None
    )
    matrix_identity_path = (
        output_dir / "alignment_matrix_identity.tsv"
        if output_mode == "formal"
        else None
    )
    if not allow_overwrite_source:
        output_paths = [matrix_path, review_path, cells_path]
        source_paths = [alignment_matrix_tsv, alignment_review_tsv, alignment_cells_tsv]
        if hypothesis_identity_path is not None:
            output_paths.append(hypothesis_identity_path)
        if matrix_identity_path is not None:
            output_paths.append(matrix_identity_path)
        if alignment_matrix_identity_tsv is not None:
            source_paths.append(alignment_matrix_identity_tsv)
        _reject_source_overwrite(
            output_paths=output_paths,
            source_paths=source_paths,
        )
    _write_tsv(matrix_path, output_matrix_header, output_matrix_rows)
    if hypothesis_identity_path is not None:
        _write_tsv(
            hypothesis_identity_path,
            _formal_matrix_header(sample_columns),
            hypothesis_identity_rows,
        )
    if matrix_identity_path is not None:
        _write_tsv(
            matrix_identity_path,
            ALIGNMENT_MATRIX_IDENTITY_COLUMNS,
            _alignment_matrix_identity_rows(
                hypothesis_identity_rows,
                sample_columns,
                output_matrix_header,
                output_matrix_rows,
            ),
        )
    review_output_header = (
        tuple(review_header)
        if output_mode == "formal"
        else (*review_header, *ACTIVATION_REVIEW_AUDIT_COLUMNS)
    )
    cells_output_header = (
        tuple(cells_header)
        if output_mode == "formal"
        else (*cells_header, *ACTIVATION_CELL_AUDIT_COLUMNS)
    )
    _write_tsv(review_path, review_output_header, output_review_rows)
    _write_tsv(cells_path, cells_output_header, output_cell_rows)
    _write_tsv(summary_path, ACTIVATION_APPLICATION_SUMMARY_COLUMNS, [summary_row])
    _write_tsv(value_delta_path, ACTIVATION_VALUE_DELTA_COLUMNS, value_delta_rows)
    return ActivationApplicationOutputs(
        matrix_tsv=matrix_path,
        review_tsv=review_path,
        cells_tsv=cells_path,
        summary_tsv=summary_path,
        value_delta_tsv=value_delta_path,
        hypothesis_identity_tsv=hypothesis_identity_path,
        matrix_identity_tsv=matrix_identity_path,
    )


def apply_activation_to_alignment_matrix_outputs(
    *,
    activation_decisions_tsv: Path,
    activation_acceptance_tsv: Path,
    activation_values_tsv: Path,
    alignment_matrix_tsv: Path,
    alignment_matrix_identity_tsv: Path,
    alignment_review_tsv: Path,
    output_dir: Path,
    require_acceptance_pass: bool = True,
    allow_overwrite_source: bool = False,
    rt_mode_evidence_rows: Sequence[Mapping[str, str]] = (),
) -> MatrixActivationApplicationOutputs:
    decisions = _read_tsv(activation_decisions_tsv)
    _validate_decisions_for_product_application(decisions)
    _validate_matrix_only_decisions(decisions)
    acceptance_rows = _read_tsv(activation_acceptance_tsv)
    if len(acceptance_rows) != 1:
        raise ValueError("activation acceptance TSV must contain exactly one row")
    acceptance = acceptance_rows[0]
    if require_acceptance_pass and acceptance.get("acceptance_status") != "pass":
        raise ValueError("activation acceptance must pass before product application")

    matrix_header, matrix_rows = _read_tsv_with_header(alignment_matrix_tsv)
    review_header, review_rows = _read_tsv_with_header(alignment_review_tsv)
    _require_columns(
        review_header,
        (
            "feature_family_id",
            "neutral_loss_tag",
            "family_center_mz",
            "family_center_rt",
            "include_in_primary_matrix",
        ),
    )
    review_by_family = {row["feature_family_id"]: row for row in review_rows}
    sample_columns, matrix_by_family = _matrix_input_by_family(
        matrix_header=matrix_header,
        matrix_rows=matrix_rows,
        alignment_matrix_identity_tsv=alignment_matrix_identity_tsv,
        review_by_family=review_by_family,
    )
    original_matrix_by_family = {
        row_key: dict(row) for row_key, row in matrix_by_family.items()
    }
    original_matrix_families = _matrix_source_family_ids(matrix_by_family)
    decision_by_key = {
        (row.get("feature_family_id", ""), row.get("sample_id", "")): row
        for row in decisions
        if row.get("sample_id") and row.get("sample_id") != "__family_context__"
    }
    activation_values_by_key = _activation_values_by_key(activation_values_tsv)

    matrix_effects: dict[tuple[str, str], str] = {}
    synthetic_cells_by_key: dict[tuple[str, str], dict[str, str]] = {}
    families_added: set[str] = set()
    for decision in decisions:
        family_id = decision.get("feature_family_id", "")
        sample_id = decision.get("sample_id", "")
        if not sample_id or sample_id == "__family_context__":
            continue
        if sample_id not in sample_columns:
            raise ValueError(
                f"{family_id}/{sample_id}: activation sample is not present in "
                "alignment_matrix.tsv sample columns"
            )
        peak_hypothesis_id = decision.get("peak_hypothesis_id", "")
        value_row = activation_values_by_key.get((peak_hypothesis_id, sample_id))
        if value_row is None:
            raise ValueError(
                f"{family_id}/{sample_id}: matrix-only activation requires a "
                "matching activation value row"
            )
        value_family_id = value_row.get("feature_family_id", "")
        if value_family_id and value_family_id != family_id:
            raise ValueError(
                f"{family_id}/{sample_id}: activation value source family "
                f"{value_family_id} does not match decision family"
            )
        value = value_row.get("projected_matrix_value", "")
        if not value:
            raise ValueError(
                f"{family_id}/{sample_id}: matrix-only activation value is blank"
            )

        row_key = _matrix_row_key_for_decision(
            matrix_by_family,
            family_id=family_id,
            sample_id=sample_id,
            decision=decision,
        )
        row = matrix_by_family.get(row_key) if row_key is not None else None
        if row is None:
            row = _new_matrix_row(
                family_id,
                sample_columns=sample_columns,
                review_row=review_by_family.get(family_id, {}),
            )
            matrix_by_family[family_id] = row
            families_added.add(family_id)
        key = (family_id, sample_id)
        if row.get(sample_id):
            matrix_effects[key] = "unchanged"
        else:
            row[sample_id] = value
            matrix_effects[key] = "written"
        synthetic_cells_by_key[key] = {
            "status": (
                value_row.get("current_raw_status", "")
                or value_row.get("current_production_status", "")
            ),
            "area": value,
            "_matrix_value_source": "activation_values_tsv",
            "_matrix_value_source_field": "projected_matrix_value",
            "_matrix_value_source_detail": (
                value_row.get("projected_matrix_value_source", "")
                or "matrix_only_activation_value"
            ),
            "_matrix_value_source_artifact_schema_version": value_row.get(
                "source_artifact_schema_version",
                "",
            ),
            "_matrix_value_source_artifact_sha256": value_row.get(
                "source_artifact_sha256",
                "",
            ),
            "_matrix_value_source_row_sha256": value_row.get(
                "source_row_sha256",
                "",
            ),
        }

    matrix_by_family = {
        row_key: row
        for row_key, row in matrix_by_family.items()
        if _has_any_sample_value(row, sample_columns)
    }
    hypothesis_identity_rows, formal_matrix_stats = _peak_hypothesis_matrix_rows(
        matrix_by_family=matrix_by_family,
        review_by_family=review_by_family,
        decisions_by_key=decision_by_key,
        sample_columns=sample_columns,
        legacy_rt_row_oracle=(),
        legacy_rt_row_oracle_mz_ppm=20.0,
        legacy_rt_row_oracle_rt_tolerance_min=1.0,
        include_family_projections=False,
        rt_mode_evidence_rows=rt_mode_evidence_rows,
    )
    output_matrix_rows = _product_matrix_rows_from_hypotheses(
        hypothesis_identity_rows,
        sample_columns,
    )
    value_delta_rows = _value_delta_rows(
        decisions=decisions,
        original_matrix_by_family=original_matrix_by_family,
        matrix_by_family=matrix_by_family,
        cells_by_key=synthetic_cells_by_key,
        sample_columns=sample_columns,
        matrix_effects=matrix_effects,
    )
    summary_row = _summary_row(
        acceptance=acceptance,
        decisions=decisions,
        output_mode="matrix-only",
        input_matrix_rows=len(matrix_rows),
        output_matrix_rows=len(output_matrix_rows),
        formal_matrix_stats=formal_matrix_stats,
        matrix_effects=matrix_effects,
        families_added=families_added,
        families_removed=original_matrix_families
        - _matrix_source_family_ids(matrix_by_family),
    )
    public_table = serialize_public_matrix_table(
        output_matrix_rows,
        sample_columns=sample_columns,
    )
    output_matrix_rows = list(public_table.rows)

    output_dir.mkdir(parents=True, exist_ok=True)
    matrix_path = output_dir / "alignment_matrix.tsv"
    summary_path = output_dir / "activation_application_summary.tsv"
    value_delta_path = output_dir / "activation_value_delta.tsv"
    hypothesis_identity_path = output_dir / "activation_hypothesis_identity.tsv"
    matrix_identity_path = output_dir / "alignment_matrix_identity.tsv"
    if not allow_overwrite_source:
        _reject_source_overwrite(
            output_paths=(
                matrix_path,
                summary_path,
                value_delta_path,
                hypothesis_identity_path,
                matrix_identity_path,
            ),
            source_paths=(
                alignment_matrix_tsv,
                alignment_matrix_identity_tsv,
                alignment_review_tsv,
                activation_values_tsv,
            ),
        )
    _write_tsv(matrix_path, public_table.header, output_matrix_rows)
    _write_tsv(
        hypothesis_identity_path,
        _formal_matrix_header(sample_columns),
        hypothesis_identity_rows,
    )
    _write_tsv(
        matrix_identity_path,
        ALIGNMENT_MATRIX_IDENTITY_COLUMNS,
        _alignment_matrix_identity_rows(
            hypothesis_identity_rows,
            sample_columns,
            public_table.header,
            output_matrix_rows,
        ),
    )
    _write_tsv(summary_path, ACTIVATION_APPLICATION_SUMMARY_COLUMNS, [summary_row])
    _write_tsv(value_delta_path, ACTIVATION_VALUE_DELTA_COLUMNS, value_delta_rows)
    return MatrixActivationApplicationOutputs(
        matrix_tsv=matrix_path,
        summary_tsv=summary_path,
        value_delta_tsv=value_delta_path,
        hypothesis_identity_tsv=hypothesis_identity_path,
        matrix_identity_tsv=matrix_identity_path,
    )


_OUTPUT_MODES = frozenset({"activated-copy", "formal"})


def _validate_output_mode(output_mode: str) -> None:
    if output_mode not in _OUTPUT_MODES:
        choices = ", ".join(sorted(_OUTPUT_MODES))
        raise ValueError(f"activation output mode must be one of: {choices}")


def _should_project_backfill_evidence(
    cells_header: Sequence[str],
    *,
    candidate_ms2_pattern_rows: Sequence[Mapping[str, str]],
    ms1_pattern_coherence_rows: Sequence[Mapping[str, str]],
    qc_ms1_pattern_reference_rows: Sequence[Mapping[str, str]],
    matrix_rt_drift_policy_rows: Sequence[Mapping[str, str]],
) -> bool:
    if any(column in cells_header for column in BACKFILL_PROJECTION_COLUMNS):
        return True
    return any(
        (
            candidate_ms2_pattern_rows,
            ms1_pattern_coherence_rows,
            qc_ms1_pattern_reference_rows,
            matrix_rt_drift_policy_rows,
        )
    )


def _header_with_backfill_projection(header: Sequence[str]) -> tuple[str, ...]:
    columns = list(header)
    for column in BACKFILL_PROJECTION_COLUMNS:
        if column not in columns:
            columns.append(column)
    return tuple(columns)


def _activated_output_paths(
    output_dir: Path,
    *,
    output_mode: str,
) -> tuple[Path, Path, Path]:
    if output_mode == "formal":
        return (
            output_dir / "alignment_matrix.tsv",
            output_dir / "alignment_review.tsv",
            output_dir / "alignment_cells.tsv",
        )
    return (
        output_dir / "alignment_matrix_activated.tsv",
        output_dir / "alignment_review_activated.tsv",
        output_dir / "alignment_cells_activated.tsv",
    )


def _validate_decisions_for_product_application(
    decisions: Sequence[Mapping[str, str]],
) -> None:
    for decision in decisions:
        if decision.get("activation_status") != "auto_activate":
            continue
        if (
            not decision.get("peak_hypothesis_id")
            or decision.get("activation_unit_scope") != "peak_hypothesis"
        ):
            family_id = decision.get("feature_family_id", "")
            sample_id = decision.get("sample_id", "")
            raise ValueError(
                "auto_activate decisions require peak_hypothesis_id and "
                "activation_unit_scope=peak_hypothesis before product "
                f"application: {family_id}/{sample_id}"
            )


def _validate_matrix_only_decisions(
    decisions: Sequence[Mapping[str, str]],
) -> None:
    for decision in decisions:
        sample_id = decision.get("sample_id", "")
        if not sample_id or sample_id == "__family_context__":
            continue
        if decision.get("activation_status") == "auto_activate":
            continue
        family_id = decision.get("feature_family_id", "")
        status = decision.get("activation_status", "")
        raise ValueError(
            "matrix-only activation only applies auto_activate rows; "
            f"got {status or '<blank>'} for {family_id}/{sample_id}"
        )


def _activation_values_by_key(
    activation_values_tsv: Path,
) -> dict[tuple[str, str], Mapping[str, str]]:
    header, rows = _read_tsv_with_header(activation_values_tsv)
    missing = [
        column for column in ACTIVATION_VALUE_INPUT_COLUMNS if column not in header
    ]
    if missing:
        raise ValueError(
            "activation_values.tsv missing columns: " + ",".join(missing)
        )
    values_by_key: dict[tuple[str, str], Mapping[str, str]] = {}
    for row_number, row in enumerate(rows, start=2):
        key = (row.get("peak_hypothesis_id", ""), row.get("sample_stem", ""))
        if not key[0] or not key[1]:
            raise ValueError(
                "activation value rows require peak_hypothesis_id and sample_stem"
            )
        _validate_activation_value_provenance(
            activation_values_tsv,
            row_number=row_number,
            row=row,
        )
        if key in values_by_key:
            raise ValueError(
                "activation value rows must be unique by "
                f"peak_hypothesis_id/sample_stem: {key[0]}/{key[1]}"
            )
        values_by_key[key] = row
    return values_by_key


def _validate_activation_value_provenance(
    path: Path,
    *,
    row_number: int,
    row: Mapping[str, str],
) -> None:
    for column in (
        "projected_matrix_value_source",
        "source_artifact_schema_version",
        "source_artifact_sha256",
        "source_row_sha256",
        "source_provenance_detail",
    ):
        if not row.get(column, ""):
            raise ValueError(
                f"{path}: row {row_number} activation value provenance "
                f"column {column} must not be blank"
            )
    for column in ("source_artifact_sha256", "source_row_sha256"):
        value = row.get(column, "")
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError(
                f"{path}: row {row_number} {column} must be a lowercase "
                "sha256 hex digest"
            )

def _reject_source_overwrite(
    *,
    output_paths: Sequence[Path],
    source_paths: Sequence[Path],
) -> None:
    output_resolved = {path.resolve() for path in output_paths}
    source_resolved = {path.resolve() for path in source_paths}
    overlap = sorted(str(path) for path in output_resolved & source_resolved)
    if overlap:
        raise ValueError(
            "activation product output would overwrite source alignment artifacts; "
            "write formal outputs to a separate directory or pass "
            "--allow-overwrite-source. Overlap: "
            + ", ".join(overlap)
        )


def _activated_review_rows(
    *,
    review_rows: Sequence[Mapping[str, str]],
    review_header: Sequence[str],
    decisions: Sequence[Mapping[str, str]],
    matrix_by_family: Mapping[str, Mapping[str, str]],
    sample_columns: Sequence[str],
    cell_status_by_key: Mapping[tuple[str, str], str],
    matrix_effects: Mapping[tuple[str, str], str],
    family_notes: Mapping[str, Sequence[str]],
    family_blocked: set[str],
) -> list[dict[str, str]]:
    counts_by_family: dict[str, Counter[str]] = {}
    rules_by_family: dict[str, list[str]] = {}
    for decision in decisions:
        family_id = decision.get("feature_family_id", "")
        counts_by_family.setdefault(family_id, Counter())[
            decision.get("activation_status", "")
        ] += 1
        rule = decision.get("contract_rule_id", "")
        if rule:
            rules_by_family.setdefault(family_id, []).append(rule)

    rows: list[dict[str, str]] = []
    for review_row in review_rows:
        family_id = review_row["feature_family_id"]
        row = {column: review_row.get(column, "") for column in review_header}
        family_in_matrix = _matrix_contains_source_family(
            matrix_by_family,
            family_id,
        )
        accepted_samples = _accepted_samples_for_source_family(
            matrix_by_family,
            family_id,
            sample_columns,
        )
        accepted_rescue_count = sum(
            1
            for sample in accepted_samples
            if cell_status_by_key.get((family_id, sample)) == "rescued"
        )
        family_counts = counts_by_family.get(family_id, Counter())
        if family_id in family_blocked:
            row["identity_decision"] = "audit_family"
            row["identity_confidence"] = "review"
            row["identity_reason"] = "activation_family_required_tag_gate"
        elif family_in_matrix and family_counts["auto_activate"]:
            if row.get("include_in_primary_matrix") != "TRUE":
                row["identity_decision"] = (
                    row.get("identity_decision") or "provisional_discovery"
                )
                row["identity_confidence"] = "medium"
                row["identity_reason"] = "activation_peak_hypothesis_candidate"
        row["include_in_primary_matrix"] = (
            "TRUE" if family_in_matrix else "FALSE"
        )
        row["accepted_cell_count"] = str(len(accepted_samples))
        row["accepted_rescue_count"] = str(accepted_rescue_count)
        row["activation_auto_activate_count"] = str(
            family_counts["auto_activate"]
        )
        row["activation_auto_block_count"] = str(
            family_counts["auto_block"]
        )
        row["activation_review_required_count"] = str(
            family_counts["review_required"]
        )
        row["activation_blocked_cell_count"] = str(
            sum(
                1
                for (effect_family, _sample), effect in matrix_effects.items()
                if effect_family == family_id and effect == "blanked"
            )
        )
        row["activation_written_cell_count"] = str(
            sum(
                1
                for (effect_family, _sample), effect in matrix_effects.items()
                if effect_family == family_id and effect == "written"
            )
        )
        row["activation_rules"] = ";".join(
            dict.fromkeys(rules_by_family.get(family_id, ()))
        )
        row["activation_application_note"] = ";".join(
            dict.fromkeys(family_notes.get(family_id, ()))
        )
        rows.append(row)
    return rows


def _activated_cell_rows(
    *,
    cell_rows: Sequence[Mapping[str, str]],
    decisions_by_key: Mapping[tuple[str, str], Mapping[str, str]],
    matrix_effects: Mapping[tuple[str, str], str],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for cell in cell_rows:
        key = (cell["feature_family_id"], cell["sample_stem"])
        decision = decisions_by_key.get(key, {})
        row = dict(cell)
        row.update(
            {
                "activation_status": decision.get("activation_status", ""),
                "activation_action": decision.get("activation_action", ""),
                "activation_product_effect": decision.get("product_effect", ""),
                "activation_contract_rule_id": decision.get("contract_rule_id", ""),
                "activation_peak_hypothesis_id": decision.get(
                    "peak_hypothesis_id",
                    "",
                ),
                "activation_unit_scope": decision.get("activation_unit_scope", ""),
                "activation_matrix_value_effect": matrix_effects.get(key, "unchanged"),
                "activation_reason": decision.get("activation_reason", ""),
            }
        )
        rows.append(row)
    return rows


def _value_delta_rows(
    *,
    decisions: Sequence[Mapping[str, str]],
    original_matrix_by_family: Mapping[str, Mapping[str, str]],
    matrix_by_family: Mapping[str, Mapping[str, str]],
    cells_by_key: Mapping[tuple[str, str], Mapping[str, str]],
    sample_columns: Sequence[str],
    matrix_effects: Mapping[tuple[str, str], str],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    sample_column_set = set(sample_columns)
    for decision in decisions:
        family_id = decision.get("feature_family_id", "")
        sample_id = decision.get("sample_id", "")
        if not sample_id or sample_id == "__family_context__":
            continue
        key = (family_id, sample_id)
        original_row_key = _matrix_row_key_for_decision(
            original_matrix_by_family,
            family_id=family_id,
            sample_id=sample_id,
            decision=decision,
        )
        activated_row_key = _matrix_row_key_for_decision(
            matrix_by_family,
            family_id=family_id,
            sample_id=sample_id,
            decision=decision,
        )
        original_row = (
            original_matrix_by_family.get(original_row_key)
            if original_row_key is not None
            else None
        )
        activated_row = (
            matrix_by_family.get(activated_row_key)
            if activated_row_key is not None
            else None
        )
        original_value = (
            original_row.get(sample_id, "")
            if original_row is not None and sample_id in sample_column_set
            else ""
        )
        activated_value = (
            activated_row.get(sample_id, "")
            if activated_row is not None and sample_id in sample_column_set
            else ""
        )
        cell = cells_by_key.get(key, {})
        matrix_value_effect = matrix_effects.get(key, "unchanged")
        rows.append(
            {
                "activation_value_delta_schema_version": (
                    ACTIVATION_VALUE_DELTA_SCHEMA_VERSION
                ),
                "feature_family_id": family_id,
                "candidate_container_id": decision.get(
                    "candidate_container_id",
                    family_id,
                ),
                "sample_id": sample_id,
                "peak_hypothesis_id": decision.get("peak_hypothesis_id", ""),
                "activation_unit_scope": decision.get("activation_unit_scope", ""),
                "activation_status": decision.get("activation_status", ""),
                "product_effect": decision.get("product_effect", ""),
                "contract_rule_id": decision.get("contract_rule_id", ""),
                "original_matrix_value": original_value,
                "activated_matrix_value": activated_value,
                "matrix_value_kind": _matrix_value_kind(
                    matrix_value_effect,
                    activation_status=decision.get("activation_status", ""),
                ),
                "matrix_value_source": _matrix_value_source(
                    matrix_value_effect,
                    cell,
                    activation_status=decision.get("activation_status", ""),
                ),
                "matrix_value_source_field": _matrix_value_source_field(
                    matrix_value_effect,
                    cell,
                ),
                "matrix_value_source_detail": _matrix_value_source_detail(
                    matrix_value_effect,
                    cell,
                ),
                "matrix_value_source_artifact_schema_version": (
                    _matrix_value_source_artifact_schema_version(cell)
                ),
                "matrix_value_source_artifact_sha256": (
                    _matrix_value_source_artifact_sha256(cell)
                ),
                "matrix_value_source_row_sha256": _matrix_value_source_row_sha256(
                    cell
                ),
                "source_cell_status": cell.get("status", ""),
                "source_cell_area": cell.get("area", ""),
                "matrix_value_effect": matrix_value_effect,
                "value_changed": (
                    "TRUE" if original_value != activated_value else "FALSE"
                ),
                "activation_reason": decision.get("activation_reason", ""),
            }
        )
    return rows


def _matrix_value_kind(
    matrix_value_effect: str,
    *,
    activation_status: str,
) -> str:
    if matrix_value_effect == "written" and activation_status == "auto_activate":
        return "backfill_activation"
    if matrix_value_effect == "unchanged":
        return "existing_matrix_value"
    if matrix_value_effect == "blanked":
        return "blanked_by_activation"
    if matrix_value_effect:
        return "not_written"
    return "not_applicable"


def _matrix_value_source(
    matrix_value_effect: str,
    cell: Mapping[str, str],
    *,
    activation_status: str,
) -> str:
    kind = _matrix_value_kind(
        matrix_value_effect,
        activation_status=activation_status,
    )
    if kind == "existing_matrix_value":
        return "existing_alignment_matrix"
    if kind == "backfill_activation":
        return cell.get("_matrix_value_source", "") or "alignment_cells_tsv"
    if cell and kind == "not_written":
        return cell.get("_matrix_value_source", "") or "alignment_cells_tsv"
    return "none"


def _matrix_value_source_field(
    matrix_value_effect: str,
    cell: Mapping[str, str],
) -> str:
    if matrix_value_effect == "unchanged":
        return "existing_matrix_value"
    explicit = cell.get("_matrix_value_source_field", "")
    if explicit:
        return explicit
    if cell.get("primary_matrix_area", ""):
        return "primary_matrix_area"
    if cell.get("area", ""):
        return "area"
    return "none"


def _matrix_value_source_detail(
    matrix_value_effect: str,
    cell: Mapping[str, str],
) -> str:
    if matrix_value_effect == "unchanged":
        return "matrix_value_preexisted"
    explicit = cell.get("_matrix_value_source_detail", "")
    if explicit:
        return explicit
    return cell.get("primary_matrix_area_source", "")


def _matrix_value_source_artifact_schema_version(cell: Mapping[str, str]) -> str:
    return cell.get("_matrix_value_source_artifact_schema_version", "")


def _matrix_value_source_artifact_sha256(cell: Mapping[str, str]) -> str:
    return cell.get("_matrix_value_source_artifact_sha256", "")


def _matrix_value_source_row_sha256(cell: Mapping[str, str]) -> str:
    return cell.get("_matrix_value_source_row_sha256", "")


def _summary_row(
    *,
    acceptance: Mapping[str, str],
    decisions: Sequence[Mapping[str, str]],
    output_mode: str,
    input_matrix_rows: int,
    output_matrix_rows: int,
    formal_matrix_stats: FormalMatrixStats,
    matrix_effects: Mapping[tuple[str, str], str],
    families_added: set[str],
    families_removed: set[str],
) -> dict[str, str]:
    decision_counts = Counter(row.get("activation_status", "") for row in decisions)
    blanked = sum(1 for effect in matrix_effects.values() if effect == "blanked")
    written = sum(1 for effect in matrix_effects.values() if effect == "written")
    return {
        "activation_application_schema_version": (
            ACTIVATION_APPLICATION_SCHEMA_VERSION
        ),
        "application_status": "applied",
        "activation_output_mode": output_mode,
        "acceptance_status": acceptance.get("acceptance_status", ""),
        "blast_radius_current": acceptance.get("blast_radius_current", ""),
        "decision_rows_total": str(len(decisions)),
        "input_matrix_rows": str(input_matrix_rows),
        "output_matrix_rows": str(output_matrix_rows),
        "matrix_row_identity": (
            "mz_rt_sample_columns"
            if _uses_formal_matrix_identity(output_mode)
            else "feature_family_id"
        ),
        "canonical_row_identity_ready": _canonical_row_identity_ready(
            output_mode,
            formal_matrix_stats,
        ),
        "canonical_row_identity_blockers": _canonical_row_identity_blocker(
            output_mode,
            formal_matrix_stats,
        ),
        "canonical_row_identity_scope": _canonical_row_identity_scope(
            output_mode,
            formal_matrix_stats,
        ),
        "family_projection_semantics": _family_projection_semantics(
            output_mode,
            formal_matrix_stats,
        ),
        "legacy_rt_row_context_authority": (
            "context_only_not_identity_authority"
            if formal_matrix_stats.legacy_rt_row_context_rows
            else "not_applicable"
        ),
        "all_family_split_science_ready": "FALSE",
        "legacy_rt_row_context_rows": str(
            formal_matrix_stats.legacy_rt_row_context_rows
        ),
        "family_projection_rows": str(formal_matrix_stats.family_projection_rows),
        "family_projection_rows_excluded": str(
            formal_matrix_stats.family_projection_rows_excluded
        ),
        "family_projection_cells_excluded": str(
            formal_matrix_stats.family_projection_cells_excluded
        ),
        "matrix_value_conflict_cells": str(
            formal_matrix_stats.matrix_value_conflict_cells
        ),
        "matrix_value_conflict_policy": (
            "max_area_pending_baseline"
            if formal_matrix_stats.matrix_value_conflict_cells
            else "not_applicable"
        ),
        "auto_activate_count": str(decision_counts["auto_activate"]),
        "auto_block_count": str(decision_counts["auto_block"]),
        "matrix_cells_written": str(written),
        "matrix_cells_blanked": str(blanked),
        "families_added_to_matrix": str(len(families_added)),
        "families_removed_from_matrix": str(len(families_removed)),
        "summary_reason": "explicit_activation_sidecar_applied",
    }


def _family_projection_semantics(
    output_mode: str,
    formal_matrix_stats: FormalMatrixStats,
) -> str:
    if not _uses_formal_matrix_identity(output_mode):
        return "not_applicable"
    if formal_matrix_stats.family_projection_rows:
        return "projection_not_split_proof"
    if formal_matrix_stats.family_projection_rows_excluded:
        return "excluded_from_canonical_output"
    return "explicit_hypothesis_only"


def _canonical_row_identity_ready(
    output_mode: str,
    formal_matrix_stats: FormalMatrixStats,
) -> str:
    if not _uses_formal_matrix_identity(output_mode):
        return "FALSE"
    if (
        formal_matrix_stats.family_projection_rows
        or formal_matrix_stats.family_projection_rows_excluded
    ):
        return "FALSE"
    return "TRUE"


def _canonical_row_identity_blocker(
    output_mode: str,
    formal_matrix_stats: FormalMatrixStats,
) -> str:
    if not _uses_formal_matrix_identity(output_mode):
        return "formal_output_not_requested"
    if formal_matrix_stats.family_projection_rows:
        return "family_projection_present"
    if formal_matrix_stats.family_projection_rows_excluded:
        return "family_projection_excluded_incomplete_scope"
    return "none"


def _canonical_row_identity_scope(
    output_mode: str,
    formal_matrix_stats: FormalMatrixStats,
) -> str:
    if not _uses_formal_matrix_identity(output_mode):
        return "legacy_feature_family_row"
    if formal_matrix_stats.family_projection_rows:
        return "partial_peak_hypothesis_sidecar_with_family_projections"
    if formal_matrix_stats.family_projection_rows_excluded:
        return "partial_canonical_peak_hypothesis_rows_only"
    return "formal_peak_hypothesis_identity_sidecar"


def _uses_formal_matrix_identity(output_mode: str) -> bool:
    return output_mode in {"formal", "matrix-only"}


def _matrix_value_for_activation(cell: Mapping[str, str] | None) -> str:
    if cell is None:
        return ""
    if (
        cell.get("primary_matrix_area_source", "")
        not in ACTIVE_PRIMARY_MATRIX_AREA_SOURCES
    ):
        return ""
    return cell.get("primary_matrix_area", "")


def _has_untrusted_primary_or_raw_area(cell: Mapping[str, str] | None) -> bool:
    return cell is not None and bool(
        cell.get("primary_matrix_area", "") or cell.get("area", "")
    )


def _require_columns(header: Sequence[str], columns: Sequence[str]) -> None:
    missing = [column for column in columns if column not in header]
    if missing:
        raise ValueError(f"missing required columns: {','.join(missing)}")


def _read_tsv(path: Path) -> tuple[dict[str, str], ...]:
    _header, rows = _read_tsv_with_header(path)
    return rows


def _read_tsv_with_header(
    path: Path,
) -> tuple[tuple[str, ...], tuple[dict[str, str], ...]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        header = tuple(reader.fieldnames or ())
        return header, tuple(dict(row) for row in reader)


def _write_tsv(
    path: Path,
    fieldnames: Sequence[str],
    rows: Sequence[Mapping[str, str]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_tsv(path, rows, fieldnames, lineterminator="\n")
