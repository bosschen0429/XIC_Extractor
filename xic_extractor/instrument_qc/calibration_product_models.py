from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

ARTIFACT_SCHEMA_VERSION = "instrument_qc_calibration"


class ProductSupportStatus(StrEnum):
    SUPPORTED = "supported"
    PARTIAL = "partial"
    NOT_TRIGGERED = "not_triggered"
    PRODUCT_MISSING = "product_missing"
    UNMAPPED = "unmapped"
    PARSE_ERROR = "parse_error"
    NOT_APPLICABLE = "not_applicable"


NeutralLossSupportStatus = ProductSupportStatus


class CoverageStatus(StrEnum):
    COVERED = "covered"
    SPARSE = "sparse"
    EXTRAPOLATED = "extrapolated"
    UNSUPPORTED = "unsupported"
    NOT_COVERED = "not_covered"
    INCOMPLETE = "incomplete"
    NOT_ASSESSABLE = "not_assessable"


class CorrectionStatus(StrEnum):
    APPLIED_PREVIEW = "applied_preview"
    SHADOW_ONLY = "shadow_only"
    REVIEW = "review"
    BLOCKED_NOT_COVERED = "blocked_not_covered"
    BLOCKED_MISSING_VALUE = "blocked_missing_value"
    BLOCKED_NONPOSITIVE_VALUE = "blocked_nonpositive_value"
    NOT_APPLICABLE = "not_applicable"


class ResponseTransferStatus(StrEnum):
    CLEAN_ONLY = "clean_only"
    BIOLOGICAL_VALIDATED = "biological_validated"
    TRANSFER_BLOCKED = "transfer_blocked"
    NOT_ASSESSED = "not_assessed"


@dataclass(frozen=True)
class ArtifactInventoryItem:
    artifact_id: str
    path: Path
    role: str
    required: bool
    schema_version: str
    status: str


@dataclass(frozen=True)
class CalibrationBundleManifest:
    schema_version: str
    bundle_id: str
    run_id: str
    product_maturity_level: str
    overall_verdict: str
    artifact_inventory: tuple[ArtifactInventoryItem, ...]
    source_artifacts: dict[str, str]
    source_contracts: dict[str, str]
    generation_command: str
    created_at_utc: str
    created_by: str
    status_counts: dict[str, dict[str, int]]
    first_human_file: str
    first_machine_file: str


@dataclass(frozen=True)
class CalibrationEvidenceRow:
    schema_version: str
    bundle_id: str
    evidence_row_id: str
    source_artifact_id: str
    source_artifact_hash: str
    source_type: str
    matrix_context: str
    sample_name: str
    raw_stem: str
    source_raw_file: str
    raw_path_kind: str
    injection_order: int | None
    compound: str
    compound_group: str
    precursor_mz: float | None
    observed_mz: float | None
    mz_ppm_error: float | None
    reference_rt_min: float | None
    observed_rt_min: float | None
    rt_delta_min: float | None
    rt_region: str
    area: float | None
    height: float | None
    log2_area_delta: float | None
    log2_height_delta: float | None
    peak_width_min: float | None
    activation_method: str
    product_support_status: ProductSupportStatus
    neutral_loss_support_status: NeutralLossSupportStatus
    evidence_confidence: str
    calibration_eligible: bool
    coverage_status: CoverageStatus
    exclusion_reason: str


@dataclass(frozen=True)
class CalibrationEvidenceSummary:
    schema_version: str
    bundle_id: str
    total_rows: int
    counts_by_source_type: dict[str, int]
    counts_by_matrix_context: dict[str, int]
    counts_by_coverage_status: dict[str, int]
    counts_by_product_support_status: dict[str, int]
    counts_by_calibration_eligible: dict[str, int]
    missing_artifacts: tuple[str, ...]


@dataclass(frozen=True)
class MatrixRTPreviewRow:
    schema_version: str
    bundle_id: str
    matrix_source: str
    matrix_source_hash: str
    matrix_schema_version: str
    source_row_id: str
    source_cell_key: str
    feature_id: str
    matrix_column_name: str
    sample_name: str
    sample_stem: str
    raw_file_stem: str
    feature_mz: float | None
    raw_feature_rt_min: float | None
    injection_order: int | None
    model_id: str
    predicted_rt_delta_min: float | None
    rt_uncertainty_min: float | None
    rt_if_standard_corrected_min: float | None
    coverage_status: CoverageStatus
    rt_alignment_support_status: str
    local_anchor_count: int
    local_clean_anchor_count: int
    local_biological_istd_anchor_count: int
    local_residual_p95_min: float | None
    irt_anchor_scope: str
    irt_position: float | None
    correction_status: CorrectionStatus
    correction_block_reason: str
    review_reason: str


@dataclass(frozen=True)
class MatrixResponsePreviewRow:
    schema_version: str
    bundle_id: str
    matrix_source: str
    matrix_source_hash: str
    matrix_schema_version: str
    source_row_id: str
    source_cell_key: str
    feature_id: str
    matrix_column_name: str
    sample_name: str
    sample_stem: str
    raw_file_stem: str
    feature_mz: float | None
    raw_feature_rt_min: float | None
    injection_order: int | None
    raw_area: float | None
    raw_area_status: str
    raw_height: float | None
    raw_height_status: str
    model_id: str
    predicted_log2_response_delta: float | None
    area_if_response_corrected: float | None
    height_if_response_corrected: float | None
    preview_area_status: str
    preview_height_status: str
    response_uncertainty_log2: float | None
    transfer_status: ResponseTransferStatus
    correction_status: CorrectionStatus
    correction_block_reason: str
    review_reason: str


@dataclass(frozen=True)
class RtDriftModelRow:
    schema_version: str
    bundle_id: str
    model_id: str
    model_scope: str
    compound: str
    compound_group: str
    source_type: str
    matrix_context: str
    injection_order: int | None
    rt_region: str
    source_mix: str
    anchor_ids: str
    anchor_count: int
    clean_anchor_count: int
    biological_istd_anchor_count: int
    predicted_rt_delta_min: float | None
    rt_uncertainty_min: float | None
    coverage_status: CoverageStatus
    conflict_status: str
    model_status: str
    review_reason: str


@dataclass(frozen=True)
class RtLeaveOneAnchorOutRow:
    schema_version: str
    bundle_id: str
    evidence_row_id: str
    compound: str
    source_type: str
    matrix_context: str
    injection_order: int | None
    reference_rt_min: float | None
    observed_rt_delta_min: float | None
    predicted_rt_delta_min: float | None
    prediction_error_min: float | None
    abs_prediction_error_min: float | None
    local_anchor_count: int
    coverage_status: CoverageStatus
    status: str
    review_reason: str
