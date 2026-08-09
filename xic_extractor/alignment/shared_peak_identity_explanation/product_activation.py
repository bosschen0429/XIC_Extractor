from __future__ import annotations

import csv
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.alignment.backfill_evidence_projection import (
    BACKFILL_PROJECTION_COLUMNS,
    project_backfill_evidence_to_cells,
)
from xic_extractor.alignment.matrix_output_contract import (
    MATRIX_IDENTITY_SCHEMA_VERSION,
    bind_matrix_identity_rows,
    canonical_mz_text,
    project_public_matrix_rows,
    serialize_public_matrix_table,
    validate_matrix_identity_row,
    validate_matrix_identity_rows,
)
from xic_extractor.alignment.primary_matrix_area import (
    MISSING_ASLS_PRIMARY_AREA,
    MS1_MORPHOLOGY_PRIMARY_MATRIX_AREA_SOURCE,
)
from xic_extractor.alignment.product_matrix import (
    ALIGNMENT_MATRIX_IDENTITY_COLUMNS,
)
from xic_extractor.tabular_io import write_tsv

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


@dataclass(frozen=True)
class LegacyRtRowReference:
    row_number: int
    mz: float
    rt: float

    @property
    def peak_hypothesis_id(self) -> str:
        return (
            f"mzmine_rtrow_{self.row_number}_mz{self.mz:.4f}_rt{self.rt:.2f}min"
        )


@dataclass(frozen=True)
class FormalMatrixStats:
    family_projection_rows: int = 0
    legacy_rt_row_context_rows: int = 0
    matrix_value_conflict_cells: int = 0
    family_projection_rows_excluded: int = 0
    family_projection_cells_excluded: int = 0


@dataclass
class _RtCenterAccumulator:
    weighted_sum: float = 0.0
    weight_sum: float = 0.0
    unweighted_sum: float = 0.0
    unweighted_count: int = 0

    def add(self, rt: float, weight: float | None) -> None:
        if weight is not None and weight > 0:
            self.weighted_sum += rt * weight
            self.weight_sum += weight
        self.unweighted_sum += rt
        self.unweighted_count += 1

    @property
    def center(self) -> float | None:
        if self.weight_sum > 0:
            return self.weighted_sum / self.weight_sum
        if self.unweighted_count > 0:
            return self.unweighted_sum / self.unweighted_count
        return None

    @property
    def basis(self) -> str:
        if self.weight_sum > 0:
            return "activation_rt_mode_area_weighted_raw_selected_rt"
        return "activation_rt_mode_unweighted_raw_selected_rt"


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


_META = frozenset(
    {"feature_family_id", "neutral_loss_tag", "family_center_mz", "family_center_rt"}
)
_PUBLIC_MATRIX_META = frozenset({"Mz", "RT"})
_PUBLIC_PRODUCT_ROW_IDENTITY_BASIS = frozenset(
    {"no_split_peak_hypothesis", "split_peak_hypothesis"}
)
_INPUT_PEAK_HYPOTHESIS_ID = "_input_peak_hypothesis_id"
_INPUT_ROW_IDENTITY_BASIS = "_input_row_identity_basis"
_INPUT_LEGACY_RT_ROW_CONTEXT_ID = "_input_legacy_rt_row_context_id"
_INPUT_SOURCE_FEATURE_FAMILY_ID = "_input_source_feature_family_id"
_HYPOTHESIS_CENTER_RT = "_hypothesis_center_rt"
_CENTER_RT_BASIS = "_center_rt_basis"

_MATRIX_IDENTITY_REQUIRED_COLUMNS = (
    "matrix_row_index",
    "Mz",
    "RT",
    "peak_hypothesis_id",
    "row_identity_basis",
    "source_feature_family_ids",
)

_FORMAL_MATRIX_META = (
    "peak_hypothesis_id",
    "feature_family_id",
    "candidate_container_id",
    "row_identity_basis",
    "legacy_rt_row_context_id",
    "neutral_loss_tag",
    "family_center_mz",
    "family_center_rt",
)
_OUTPUT_MODES = frozenset({"activated-copy", "formal"})


def _validate_output_mode(output_mode: str) -> None:
    if output_mode not in _OUTPUT_MODES:
        choices = ", ".join(sorted(_OUTPUT_MODES))
        raise ValueError(f"activation output mode must be one of: {choices}")


def _matrix_input_by_family(
    *,
    matrix_header: Sequence[str],
    matrix_rows: Sequence[Mapping[str, str]],
    alignment_matrix_identity_tsv: Path | None,
    review_by_family: Mapping[str, Mapping[str, str]],
) -> tuple[tuple[str, ...], dict[str, dict[str, str]]]:
    if "feature_family_id" in matrix_header:
        sample_columns = tuple(
            column for column in matrix_header if column not in _META
        )
        return (
            sample_columns,
            {row["feature_family_id"]: dict(row) for row in matrix_rows},
        )
    _require_columns(matrix_header, ("Mz", "RT"))
    if alignment_matrix_identity_tsv is None:
        raise ValueError(
            "public Mz/RT alignment_matrix.tsv requires "
            "alignment_matrix_identity_tsv"
        )
    sample_columns = tuple(
        column for column in matrix_header if column not in _PUBLIC_MATRIX_META
    )
    return (
        sample_columns,
        _public_matrix_rows_by_family(
            matrix_rows=matrix_rows,
            sample_columns=sample_columns,
            alignment_matrix_identity_tsv=alignment_matrix_identity_tsv,
            review_by_family=review_by_family,
        ),
    )


def _public_matrix_rows_by_family(
    *,
    matrix_rows: Sequence[Mapping[str, str]],
    sample_columns: Sequence[str],
    alignment_matrix_identity_tsv: Path,
    review_by_family: Mapping[str, Mapping[str, str]],
) -> dict[str, dict[str, str]]:
    identity_header, identity_rows = _read_tsv_with_header(
        alignment_matrix_identity_tsv
    )
    _require_columns(identity_header, _MATRIX_IDENTITY_REQUIRED_COLUMNS)
    matrix_header = ("Mz", "RT", *sample_columns)
    validate_matrix_identity_rows(
        matrix_header=matrix_header,
        matrix_rows=matrix_rows,
        identity_columns=identity_header,
        identity_rows=identity_rows,
    )
    rows_by_key: dict[str, dict[str, str]] = {}
    for index, (matrix_row, identity) in enumerate(
        zip(matrix_rows, identity_rows, strict=True),
        start=1,
    ):
        _validate_public_identity_basis(identity, index)
        family_id = _single_source_family_id(identity, index)
        row_key = _public_matrix_row_key(identity, index)
        if row_key in rows_by_key:
            raise ValueError(
                "public Mz/RT matrix identity row peak_hypothesis_id must be "
                f"unique; duplicate {row_key} at matrix_row_index {index}"
            )
        review_row = review_by_family.get(family_id, {})
        row = {
            "feature_family_id": family_id,
            "neutral_loss_tag": review_row.get("neutral_loss_tag", ""),
            "family_center_mz": canonical_mz_text(identity.get("Mz", "")),
            "family_center_rt": matrix_row.get("RT", ""),
            _INPUT_PEAK_HYPOTHESIS_ID: identity.get("peak_hypothesis_id", ""),
            _INPUT_ROW_IDENTITY_BASIS: identity.get("row_identity_basis", ""),
            _INPUT_LEGACY_RT_ROW_CONTEXT_ID: identity.get(
                "legacy_rt_row_context_id",
                "",
            ),
            _INPUT_SOURCE_FEATURE_FAMILY_ID: family_id,
        }
        row.update({sample: matrix_row.get(sample, "") for sample in sample_columns})
        rows_by_key[row_key] = row
    return rows_by_key


def _public_matrix_row_key(identity_row: Mapping[str, str], index: int) -> str:
    peak_hypothesis_id = identity_row.get("peak_hypothesis_id", "").strip()
    if not peak_hypothesis_id:
        raise ValueError(
            "public Mz/RT matrix identity row requires peak_hypothesis_id "
            f"at matrix_row_index {index}"
        )
    return peak_hypothesis_id


def _validate_public_identity_basis(
    identity: Mapping[str, str],
    row_index: int,
) -> None:
    row_identity_basis = identity.get("row_identity_basis", "")
    if row_identity_basis in _PUBLIC_PRODUCT_ROW_IDENTITY_BASIS:
        return
    raise ValueError(
        "public Mz/RT matrix identity row requires product row_identity_basis "
        f"at matrix_row_index {row_index}; got {row_identity_basis or '<blank>'}"
    )


def _positive_row_index(value: str) -> int:
    try:
        index = int(value)
    except ValueError:
        return -1
    return index if index > 0 else -1


def _validate_identity_coordinate(
    index: int,
    matrix_row: Mapping[str, str],
    identity_row: Mapping[str, str],
) -> None:
    validate_matrix_identity_row(
        matrix_header=tuple(matrix_row),
        matrix_row=matrix_row,
        identity_columns=tuple(identity_row),
        identity_row=identity_row,
        expected_index=index,
    )


def _single_source_family_id(identity_row: Mapping[str, str], index: int) -> str:
    source_ids = tuple(
        part.strip()
        for part in identity_row.get("source_feature_family_ids", "").split(";")
        if part.strip()
    )
    if len(source_ids) != 1:
        raise ValueError(
            "public Mz/RT matrix activation requires exactly one "
            "source_feature_family_id per row until multi-source "
            f"PeakHypothesis activation is implemented: matrix_row_index {index}"
    )
    return source_ids[0]


def _matrix_source_family_id(
    matrix_row: Mapping[str, str],
    row_key: str,
) -> str:
    return (
        matrix_row.get(_INPUT_SOURCE_FEATURE_FAMILY_ID, "")
        or matrix_row.get("feature_family_id", "")
        or row_key
    )


def _matrix_source_family_ids(
    matrix_by_family: Mapping[str, Mapping[str, str]],
) -> set[str]:
    return {
        _matrix_source_family_id(row, row_key)
        for row_key, row in matrix_by_family.items()
    }


def _matrix_row_keys_for_source_family(
    matrix_by_family: Mapping[str, Mapping[str, str]],
    family_id: str,
) -> tuple[str, ...]:
    return tuple(
        row_key
        for row_key, row in matrix_by_family.items()
        if _matrix_source_family_id(row, row_key) == family_id
    )


def _pop_matrix_rows_for_source_family(
    matrix_by_family: dict[str, dict[str, str]],
    family_id: str,
) -> None:
    for row_key in _matrix_row_keys_for_source_family(matrix_by_family, family_id):
        matrix_by_family.pop(row_key, None)


def _matrix_row_key_for_decision(
    matrix_by_family: Mapping[str, Mapping[str, str]],
    *,
    family_id: str,
    sample_id: str,
    decision: Mapping[str, str],
) -> str | None:
    peak_hypothesis_id = decision.get("peak_hypothesis_id", "").strip()
    if peak_hypothesis_id and peak_hypothesis_id in matrix_by_family:
        return peak_hypothesis_id
    if family_id in matrix_by_family:
        return family_id
    candidate_keys = _matrix_row_keys_for_source_family(matrix_by_family, family_id)
    if not candidate_keys:
        return None
    if peak_hypothesis_id:
        raise ValueError(
            f"{family_id}/{sample_id}: activation decision peak_hypothesis_id "
            f"{peak_hypothesis_id} does not match a public product matrix row"
        )
    valued_keys = tuple(
        row_key
        for row_key in candidate_keys
        if sample_id and matrix_by_family[row_key].get(sample_id)
    )
    if len(valued_keys) == 1:
        return valued_keys[0]
    if len(valued_keys) > 1:
        raise ValueError(
            f"{family_id}/{sample_id}: activation decision is ambiguous across "
            "public product matrix rows; provide peak_hypothesis_id"
        )
    if len(candidate_keys) == 1:
        return candidate_keys[0]
    return None


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


def _formal_matrix_header(sample_columns: Sequence[str]) -> tuple[str, ...]:
    return (*_FORMAL_MATRIX_META, *sample_columns)


def _product_matrix_header(sample_columns: Sequence[str]) -> tuple[str, ...]:
    return ("Mz", "RT", *sample_columns)


def _product_matrix_rows_from_hypotheses(
    hypothesis_rows: Sequence[Mapping[str, str]],
    sample_columns: Sequence[str],
) -> list[dict[str, str]]:
    machine_rows: list[dict[str, str]] = []
    for hypothesis_row in hypothesis_rows:
        row = {
            "Mz": hypothesis_row.get("family_center_mz", ""),
            "RT": _matrix_center_rt(hypothesis_row),
        }
        row.update(
            {
                sample: hypothesis_row.get(sample, "")
                for sample in sample_columns
            }
        )
        machine_rows.append(row)
    return [
        {column: str(value) for column, value in row.items()}
        for row in project_public_matrix_rows(
            machine_rows,
            sample_columns=sample_columns,
        )
    ]


def _alignment_matrix_identity_rows(
    hypothesis_rows: Sequence[Mapping[str, str]],
    sample_columns: Sequence[str],
    matrix_header: Sequence[str],
    matrix_rows: Sequence[Mapping[str, str]],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for index, hypothesis_row in enumerate(hypothesis_rows, start=1):
        source_family_ids = tuple(
            part
            for part in hypothesis_row.get("feature_family_id", "").split(";")
            if part
        )
        row_identity_basis = hypothesis_row.get("row_identity_basis", "")
        accepted_count = sum(
            1 for sample in sample_columns if hypothesis_row.get(sample)
        )
        if len(source_family_ids) != 1:
            peak_hypothesis_id = hypothesis_row.get("peak_hypothesis_id", "")
            raise ValueError(
                f"{peak_hypothesis_id}: product matrix row requires exactly one "
                "source_feature_family_id, got "
                f"{';'.join(source_family_ids) or '<blank>'}"
            )
        is_projection = row_identity_basis == "family_projection_no_split_evidence"
        split_evaluation_status = _split_evaluation_status(row_identity_basis)
        rows.append(
            {
                "identity_schema_version": MATRIX_IDENTITY_SCHEMA_VERSION,
                "matrix_row_index": str(index),
                "Mz": canonical_mz_text(
                    hypothesis_row.get("family_center_mz", "")
                ),
                "RT": _matrix_center_rt(hypothesis_row),
                "peak_hypothesis_id": hypothesis_row.get("peak_hypothesis_id", ""),
                "row_identity_basis": row_identity_basis,
                "split_evaluation_status": split_evaluation_status,
                "projection_status": (
                    "family_projection" if is_projection else "not_projection"
                ),
                "source_feature_family_ids": ";".join(source_family_ids),
                "source_feature_family_count": str(len(source_family_ids)),
                "center_mz_basis": "activation_hypothesis_family_center_mz",
                "center_rt_basis": _matrix_center_rt_basis(hypothesis_row),
                "center_weight_basis": "activation_formal_matrix_row",
                "accepted_cell_count": str(accepted_count),
                "accepted_sample_count": str(accepted_count),
                "evidence_status": (
                    "family_projection_not_split_proof"
                    if is_projection
                    else _evidence_status(row_identity_basis)
                ),
                "parent_peak_hypothesis_id": (
                    _parent_peak_hypothesis_id(hypothesis_row, source_family_ids)
                ),
                "child_peak_hypothesis_ids": "",
                "matrix_row_binding_sha256": "",
            }
        )
    return list(
        bind_matrix_identity_rows(
            matrix_header=matrix_header,
            matrix_rows=matrix_rows,
            identity_columns=ALIGNMENT_MATRIX_IDENTITY_COLUMNS,
            identity_rows=rows,
        )
    )


def _matrix_center_rt(hypothesis_row: Mapping[str, str]) -> str:
    return (
        hypothesis_row.get(_HYPOTHESIS_CENTER_RT, "")
        or hypothesis_row.get("family_center_rt", "")
    )


def _matrix_center_rt_basis(hypothesis_row: Mapping[str, str]) -> str:
    return (
        hypothesis_row.get(_CENTER_RT_BASIS, "")
        or "activation_hypothesis_family_center_rt"
    )


def _split_evaluation_status(row_identity_basis: str) -> str:
    if row_identity_basis == "split_peak_hypothesis":
        return "complete_product_ready_split"
    if row_identity_basis == "no_split_peak_hypothesis":
        return "complete_no_product_ready_split"
    return "incomplete_scope"


def _evidence_status(row_identity_basis: str) -> str:
    if row_identity_basis == "split_peak_hypothesis":
        return "product_matrix_split_identity_complete"
    if row_identity_basis == "no_split_peak_hypothesis":
        return "product_matrix_identity_complete"
    return "product_matrix_identity_incomplete"


def _parent_peak_hypothesis_id(
    hypothesis_row: Mapping[str, str],
    source_family_ids: Sequence[str],
) -> str:
    if hypothesis_row.get("row_identity_basis", "") != "split_peak_hypothesis":
        return ""
    peak_hypothesis_id = hypothesis_row.get("peak_hypothesis_id", "")
    if "::" in peak_hypothesis_id:
        return peak_hypothesis_id.split("::", 1)[0]
    if len(source_family_ids) == 1:
        return source_family_ids[0]
    return ";".join(source_family_ids)


def _peak_hypothesis_matrix_rows(
    *,
    matrix_by_family: Mapping[str, Mapping[str, str]],
    review_by_family: Mapping[str, Mapping[str, str]],
    decisions_by_key: Mapping[tuple[str, str], Mapping[str, str]],
    sample_columns: Sequence[str],
    legacy_rt_row_oracle: Sequence[LegacyRtRowReference],
    legacy_rt_row_oracle_mz_ppm: float,
    legacy_rt_row_oracle_rt_tolerance_min: float,
    include_family_projections: bool = False,
    rt_mode_evidence_rows: Sequence[Mapping[str, str]] = (),
) -> tuple[list[dict[str, str]], FormalMatrixStats]:
    rows_by_hypothesis: dict[str, dict[str, str]] = {}
    rt_mode_by_key = _rt_mode_by_key(rt_mode_evidence_rows)
    rt_centers_by_hypothesis: dict[str, _RtCenterAccumulator] = {}
    family_projection_ids: set[str] = set()
    excluded_family_projection_ids: set[str] = set()
    excluded_family_projection_cells = 0
    legacy_rt_row_context_row_ids: set[str] = set()
    matrix_value_conflict_cells = 0
    for row_key in sorted(matrix_by_family):
        family_row = matrix_by_family[row_key]
        family_id = _matrix_source_family_id(family_row, row_key)
        review_row = review_by_family.get(family_id, {})
        for sample_id in sample_columns:
            value = family_row.get(sample_id, "")
            if not value:
                continue
            decision = decisions_by_key.get((family_id, sample_id), {})
            peak_hypothesis_id = _decision_peak_hypothesis_id(decision)
            row_identity_basis = _decision_row_identity_basis(
                family_id,
                peak_hypothesis_id,
            )
            candidate_container_id = decision.get("candidate_container_id", family_id)
            if not peak_hypothesis_id:
                input_peak_hypothesis_id = family_row.get(
                    _INPUT_PEAK_HYPOTHESIS_ID,
                    "",
                )
                if input_peak_hypothesis_id:
                    peak_hypothesis_id = input_peak_hypothesis_id
                    row_identity_basis = (
                        family_row.get(_INPUT_ROW_IDENTITY_BASIS, "")
                        or "input_matrix_identity"
                    )
                    legacy_rt_row_context_id = family_row.get(
                        _INPUT_LEGACY_RT_ROW_CONTEXT_ID,
                        "",
                    )
                else:
                    projection_id = _legacy_projection_hypothesis_id(family_id)
                    if not include_family_projections:
                        excluded_family_projection_ids.add(projection_id)
                        excluded_family_projection_cells += 1
                        continue
                    legacy_reference = _match_legacy_rt_row_reference(
                        review_row,
                        legacy_rt_row_oracle,
                        mz_ppm=legacy_rt_row_oracle_mz_ppm,
                        rt_tolerance_min=legacy_rt_row_oracle_rt_tolerance_min,
                    )
                    peak_hypothesis_id = projection_id
                    row_identity_basis = "family_projection_no_split_evidence"
                    family_projection_ids.add(peak_hypothesis_id)
                    legacy_rt_row_context_id = ""
                    if legacy_reference is not None:
                        legacy_rt_row_context_id = legacy_reference.peak_hypothesis_id
                        legacy_rt_row_context_row_ids.add(peak_hypothesis_id)
                candidate_container_id = family_id
            else:
                legacy_rt_row_context_id = ""
            metadata_row = _identity_metadata_row(family_row, review_row)
            _reject_multi_family_hypothesis_collapse(
                rows_by_hypothesis.get(peak_hypothesis_id),
                peak_hypothesis_id=peak_hypothesis_id,
                family_id=family_id,
            )
            row = rows_by_hypothesis.setdefault(
                peak_hypothesis_id,
                _new_peak_hypothesis_matrix_row(
                    peak_hypothesis_id,
                    family_id=family_id,
                    candidate_container_id=candidate_container_id,
                    row_identity_basis=row_identity_basis,
                    legacy_rt_row_context_id=legacy_rt_row_context_id,
                    review_row=metadata_row,
                    sample_columns=sample_columns,
                ),
            )
            _append_unique_cell(row, "feature_family_id", family_id)
            _append_unique_cell(row, "candidate_container_id", candidate_container_id)
            _append_unique_cell(
                row,
                "legacy_rt_row_context_id",
                legacy_rt_row_context_id,
            )
            previous = row.get(sample_id, "")
            if previous and previous != value:
                value = _select_conflicting_matrix_value(previous, value)
                matrix_value_conflict_cells += 1
            row[sample_id] = value
            _add_rt_mode_center(
                rt_centers_by_hypothesis,
                family_id=family_id,
                sample_id=sample_id,
                peak_hypothesis_id=peak_hypothesis_id,
                value=value,
                rt_mode_by_key=rt_mode_by_key,
            )
    _apply_rt_mode_centers(rows_by_hypothesis, rt_centers_by_hypothesis)
    return (
        [rows_by_hypothesis[key] for key in sorted(rows_by_hypothesis)],
        FormalMatrixStats(
            family_projection_rows=len(family_projection_ids),
            legacy_rt_row_context_rows=len(legacy_rt_row_context_row_ids),
            matrix_value_conflict_cells=matrix_value_conflict_cells,
            family_projection_rows_excluded=len(excluded_family_projection_ids),
            family_projection_cells_excluded=excluded_family_projection_cells,
        ),
    )


def _decision_peak_hypothesis_id(decision: Mapping[str, str]) -> str:
    if decision.get("activation_unit_scope") != "peak_hypothesis":
        return ""
    if decision.get("activation_status") != "auto_activate":
        return ""
    return decision.get("peak_hypothesis_id", "")


def _rt_mode_by_key(
    rows: Sequence[Mapping[str, str]],
) -> dict[tuple[str, str], Mapping[str, str]]:
    return {
        (row.get("feature_family_id", ""), row.get("sample_stem", "")): row
        for row in rows
        if row.get("feature_family_id") and row.get("sample_stem")
    }


def _add_rt_mode_center(
    rt_centers_by_hypothesis: dict[str, _RtCenterAccumulator],
    *,
    family_id: str,
    sample_id: str,
    peak_hypothesis_id: str,
    value: str,
    rt_mode_by_key: Mapping[tuple[str, str], Mapping[str, str]],
) -> None:
    row = rt_mode_by_key.get((family_id, sample_id))
    if row is None or not _rt_mode_matches_hypothesis(row, peak_hypothesis_id):
        return
    raw_rt = _to_float(row.get("raw_selected_rt"))
    if raw_rt is None:
        return
    weight = _to_float(value)
    rt_centers_by_hypothesis.setdefault(
        peak_hypothesis_id,
        _RtCenterAccumulator(),
    ).add(raw_rt, weight)


def _rt_mode_matches_hypothesis(
    rt_mode_row: Mapping[str, str],
    peak_hypothesis_id: str,
) -> bool:
    if not _rt_mode_is_product_coordinate_authority(rt_mode_row):
        return False
    mode_id = rt_mode_row.get("selected_mode_id", "")
    if not mode_id or "::" not in peak_hypothesis_id:
        return False
    return peak_hypothesis_id.rsplit("::", 1)[1] == mode_id


def _rt_mode_is_product_coordinate_authority(rt_mode_row: Mapping[str, str]) -> bool:
    if rt_mode_row.get("rt_mode_status", "") != "mode_supported":
        return False
    return rt_mode_row.get("rt_mode_evidence_level", "") in {
        "irt_selected_apex_modes",
        "mode_assignment_summary",
    }


def _apply_rt_mode_centers(
    rows_by_hypothesis: Mapping[str, dict[str, str]],
    rt_centers_by_hypothesis: Mapping[str, _RtCenterAccumulator],
) -> None:
    for peak_hypothesis_id, accumulator in rt_centers_by_hypothesis.items():
        row = rows_by_hypothesis.get(peak_hypothesis_id)
        center = accumulator.center
        if row is None or center is None:
            continue
        row[_HYPOTHESIS_CENTER_RT] = _format_float(center)
        row[_CENTER_RT_BASIS] = accumulator.basis


def _decision_row_identity_basis(family_id: str, peak_hypothesis_id: str) -> str:
    if not peak_hypothesis_id:
        return ""
    if peak_hypothesis_id == family_id:
        return "no_split_peak_hypothesis"
    return "split_peak_hypothesis"


def _reject_multi_family_hypothesis_collapse(
    existing_row: Mapping[str, str] | None,
    *,
    peak_hypothesis_id: str,
    family_id: str,
) -> None:
    if existing_row is None:
        return
    source_ids = tuple(
        part for part in existing_row.get("feature_family_id", "").split(";") if part
    )
    if not source_ids or family_id in source_ids:
        return
    collapsed_ids = ";".join((*source_ids, family_id))
    raise ValueError(
        f"{peak_hypothesis_id}: product matrix row requires exactly one "
        f"source_feature_family_id, got {collapsed_ids}"
    )


def _identity_metadata_row(
    family_row: Mapping[str, str],
    review_row: Mapping[str, str],
) -> dict[str, str]:
    # Public Matrix input is rehydrated from its identity sidecar. Legacy
    # feature-family input has no such binding, so its review row is the most
    # precise available machine coordinate.
    if family_row.get(_INPUT_PEAK_HYPOTHESIS_ID):
        family_center_mz = (
            family_row.get("family_center_mz")
            or review_row.get("family_center_mz", "")
        )
    else:
        family_center_mz = (
            review_row.get("family_center_mz")
            or family_row.get("family_center_mz", "")
        )
    return {
        "neutral_loss_tag": (
            family_row.get("neutral_loss_tag")
            or review_row.get("neutral_loss_tag", "")
        ),
        "family_center_mz": canonical_mz_text(family_center_mz),
        "family_center_rt": (
            family_row.get("family_center_rt")
            or review_row.get("family_center_rt", "")
        ),
    }


def _legacy_projection_hypothesis_id(family_id: str) -> str:
    return f"{family_id}::family_projection"


def _load_legacy_rt_row_oracle(
    path: Path | None,
) -> tuple[LegacyRtRowReference, ...]:
    if path is None:
        return ()
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - dependency is declared.
        raise ValueError(
            "legacy RT-row oracle requires openpyxl; install project dependencies"
        ) from exc
    workbook = load_workbook(path, read_only=True, data_only=True)
    sheet = workbook[workbook.sheetnames[0]]
    header = tuple(cell.value for cell in next(sheet.iter_rows(max_row=1)))
    try:
        mz_index = header.index("Mz")
        rt_index = header.index("RT")
    except ValueError as exc:
        raise ValueError(
            "legacy RT-row oracle workbook must contain Mz and RT"
        ) from exc
    rows: list[LegacyRtRowReference] = []
    for row_number, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), 2):
        mz = _to_float(row[mz_index])
        rt = _to_float(row[rt_index])
        if mz is None or rt is None:
            continue
        rows.append(LegacyRtRowReference(row_number=row_number, mz=mz, rt=rt))
    return tuple(rows)


def _match_legacy_rt_row_reference(
    review_row: Mapping[str, str],
    oracle_rows: Sequence[LegacyRtRowReference],
    *,
    mz_ppm: float,
    rt_tolerance_min: float,
) -> LegacyRtRowReference | None:
    if not oracle_rows:
        return None
    mz = _to_float(review_row.get("family_center_mz"))
    rt = _to_float(review_row.get("family_center_rt"))
    if mz is None or rt is None:
        return None
    matches: list[tuple[float, float, LegacyRtRowReference]] = []
    for oracle_row in oracle_rows:
        ppm = abs(oracle_row.mz - mz) / mz * 1_000_000
        rt_delta = abs(oracle_row.rt - rt)
        if ppm <= mz_ppm and rt_delta <= rt_tolerance_min:
            matches.append((rt_delta, ppm, oracle_row))
    if not matches:
        return None
    matches.sort(key=lambda item: (item[0], item[1], item[2].row_number))
    if len(matches) > 1 and _legacy_oracle_match_is_ambiguous(matches):
        return None
    return matches[0][2]


def _legacy_oracle_match_is_ambiguous(
    matches: Sequence[tuple[float, float, LegacyRtRowReference]],
) -> bool:
    best = matches[0]
    second = matches[1]
    return abs(best[0] - second[0]) <= 0.05 and abs(best[1] - second[1]) <= 5.0


def _to_float(value: object) -> float | None:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _format_float(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _append_unique_cell(row: dict[str, str], column: str, value: str) -> None:
    if not value:
        return
    existing = tuple(part for part in row.get(column, "").split(";") if part)
    if value in existing:
        return
    row[column] = ";".join((*existing, value))


def _select_conflicting_matrix_value(existing: str, incoming: str) -> str:
    existing_value = _to_float(existing)
    incoming_value = _to_float(incoming)
    if existing_value is None or incoming_value is None:
        return existing
    return incoming if incoming_value > existing_value else existing


def _new_peak_hypothesis_matrix_row(
    peak_hypothesis_id: str,
    *,
    family_id: str,
    candidate_container_id: str,
    row_identity_basis: str,
    legacy_rt_row_context_id: str,
    review_row: Mapping[str, str],
    sample_columns: Sequence[str],
) -> dict[str, str]:
    row = {
        "peak_hypothesis_id": peak_hypothesis_id,
        "feature_family_id": family_id,
        "candidate_container_id": candidate_container_id or family_id,
        "row_identity_basis": row_identity_basis,
        "legacy_rt_row_context_id": legacy_rt_row_context_id,
        "neutral_loss_tag": review_row.get("neutral_loss_tag", ""),
        "family_center_mz": canonical_mz_text(
            review_row.get("family_center_mz", "")
        ),
        "family_center_rt": review_row.get("family_center_rt", ""),
    }
    row.update({sample: "" for sample in sample_columns})
    return row


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


def _new_matrix_row(
    family_id: str,
    *,
    sample_columns: Sequence[str],
    review_row: Mapping[str, str],
) -> dict[str, str]:
    row = {
        "feature_family_id": family_id,
        "neutral_loss_tag": review_row.get("neutral_loss_tag", ""),
        "family_center_mz": canonical_mz_text(
            review_row.get("family_center_mz", "")
        ),
        "family_center_rt": review_row.get("family_center_rt", ""),
    }
    row.update({sample: "" for sample in sample_columns})
    return row


def _accepted_samples(
    matrix_row: Mapping[str, str] | None,
    sample_columns: Sequence[str],
) -> tuple[str, ...]:
    if matrix_row is None:
        return ()
    return tuple(sample for sample in sample_columns if matrix_row.get(sample))


def _accepted_samples_for_source_family(
    matrix_by_family: Mapping[str, Mapping[str, str]],
    family_id: str,
    sample_columns: Sequence[str],
) -> tuple[str, ...]:
    accepted: list[str] = []
    for row_key in _matrix_row_keys_for_source_family(matrix_by_family, family_id):
        for sample in _accepted_samples(matrix_by_family[row_key], sample_columns):
            if sample not in accepted:
                accepted.append(sample)
    return tuple(accepted)


def _matrix_contains_source_family(
    matrix_by_family: Mapping[str, Mapping[str, str]],
    family_id: str,
) -> bool:
    return bool(_matrix_row_keys_for_source_family(matrix_by_family, family_id))


def _has_any_sample_value(
    row: Mapping[str, str],
    sample_columns: Sequence[str],
) -> bool:
    return any(row.get(sample) for sample in sample_columns)


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
