"""Strict durable provenance for canonical Owner Backfill physical peak facts.

Persisted rows prove projection integrity only.  Alignment, physical, and final
cell claims are lifted only by replay through their original live owners.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import suppress
from dataclasses import InitVar, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from xic_extractor.alignment.matrix import AlignedCell, AlignmentMatrix
from xic_extractor.alignment.production_decisions import (
    OWNER_BACKFILL_AUTHORITY_CELL_ID_PREFIX,
    PRODUCTION_CELL_DECISION_ID_PREFIX,
    ProductionCellDecisionProjectionV2,
    ProductionDecisionSet,
    build_production_decisions,
    production_cell_decision_projection,
)
from xic_extractor.peak_detection.evidence_facts import (
    PhysicalPeakEvaluation,
    PhysicalPeakState,
    evaluate_physical_peak,
    prepare_physical_signal,
    rehydrate_physical_peak_evaluation,
    validate_bounded_physical_peak_evaluation,
)
from xic_extractor.peak_detection.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
    SignalProcessingProfile,
)

if TYPE_CHECKING:
    from xic_extractor.alignment.config import AlignmentConfig
    from xic_extractor.alignment.owner_backfill import (
        OwnerBackfillExtractionResult,
    )
    from xic_extractor.alignment.owner_backfill import (
        OwnerBackfillPeakFactObservation as LiveOwnerBackfillPeakFactObservation,
    )
    from xic_extractor.peak_detection.integration_result import IntegrationResult


OWNER_BACKFILL_PEAK_FACT_PROVENANCE_SCHEMA_VERSION = (
    "alignment_owner_backfill_peak_fact_provenance_v2"
)
OWNER_BACKFILL_PROVENANCE_WRITER_SCHEMA_VERSION = (
    "alignment-owner-backfill-peak-fact-provenance-writer-v2"
)
OWNER_BACKFILL_AUTHORITY_CELL_SCHEMA_VERSION = (
    "owner_backfill_authority_cell_projection_v2"
)
SELECTED_INTEGRATION_AUTHORITY_SCHEMA_VERSION = (
    "selected_integration_authority_projection_v1"
)
OWNER_BACKFILL_CELL_PROJECTION_SCHEMA_VERSION = "owner_backfill_cell_projection_v2"

SELECTED_INTEGRATION_AUTHORITY_ID_PREFIX = "selected-integration-authority.v1:"
OWNER_BACKFILL_CELL_PROJECTION_ID_PREFIX = "owner-backfill-cell-projection.v2:"
OWNER_BACKFILL_PROVENANCE_ROW_ID_PREFIX = "owner-backfill-provenance-row.v2:"

RowKind = Literal["run_profile", "physical_question", "cell_projection"]
ClaimState = Literal["verified", "unavailable_without_live_owner"]

_STAGE_PROJECTION_MINT_TOKEN = object()

OWNER_BACKFILL_PEAK_FACT_PROVENANCE_COLUMNS = (
    "row_kind",
    "schema_version",
    "work_unit_key",
    "group_hypothesis_id",
    "public_family_id",
    "sample_stem",
    "candidate_index",
    "candidate_phase",
    "request_identity_id",
    "acquisition_source_id",
    "trace_content_id",
    "trace_point_count",
    "trace_rt_start_min",
    "trace_rt_end_min",
    "profile_schema_version",
    "profile_id",
    "profile_hash",
    "signal_processing_method",
    "signal_processing_window_points",
    "physical_signal_formula",
    "prepared_signal_id",
    "segment_universe_id",
    "segment_candidate_count",
    "segment_candidate_ids",
    "physical_question_schema_version",
    "physical_question_id",
    "selection_subject_id",
    "query_rt_min",
    "context_rt_start_min",
    "context_rt_end_min",
    "admissible_segment_count",
    "admissible_segment_ids",
    "selection_policy_version",
    "state",
    "reason",
    "physical_answer_id",
    "physical_segment_id",
    "apex_rt_min",
    "left_rt_min",
    "right_rt_min",
    "height",
    "scan_support_count",
    "area_counts_seconds",
    "area_source",
    "relative_shape_schema_version",
    "relative_shape_point_count",
    "relative_shape_content_hash",
    "fact_content_id",
    "selected_work_unit_key",
    "selected_fact_content_id",
    "owner_backfill_cell_content_id",
    "production_cell_decision_id",
    "cell_status",
    "consolidation_state",
    "consolidation_winner_group_hypothesis_id",
    "consolidation_source_group_hypothesis_id",
    "matrix_value_counts_seconds",
    "matrix_value_source",
    "writer_schema_version",
    "cell_projection_id",
    "projection_content_id",
)

SELECTED_INTEGRATION_AUTHORITY_FIELDS = (
    "rt_left_min",
    "rt_apex_min",
    "rt_right_min",
    "raw_apex_rt_min",
    "rt_width_min",
    "height_raw",
    "height_smoothed",
    "area_raw_counts_seconds",
    "integration_method",
    "boundary_sources",
    "area_baseline_corrected",
    "baseline_type",
    "raw_scan_indices",
    "area_ms1_morphology",
    "ms1_morphology_area_source",
    "ms1_morphology_trace_method",
    "ms1_morphology_trace_window_points",
    "ms1_morphology_trace_effective_points",
)

OWNER_BACKFILL_AUTHORITY_CELL_FIELDS = (
    "work_unit_key",
    "acquisition_source_id",
    "profile_id",
    "selected_fact_content_id",
    "physical_answer_id",
    "physical_segment_id",
    "sample_stem",
    "cluster_id",
    "status",
    "area",
    "apex_rt",
    "height",
    "peak_start_rt",
    "peak_end_rt",
    "rt_delta_sec",
    "trace_quality",
    "scan_support_score",
    "source_candidate_id",
    "reason",
    "selected_integration_authority_content_id",
    "backfill_seed_mz",
    "backfill_seed_rt",
    "backfill_request_rt_min",
    "backfill_request_rt_max",
    "backfill_request_ppm",
    "backfill_ms1_pattern_status",
    "backfill_ms1_pattern_evidence_level",
    "backfill_ms1_product_authority_status",
    "backfill_ms1_product_authority_scope",
    "backfill_ms1_product_authority_source",
    "backfill_ms1_product_authority_reason",
    "backfill_ms1_product_authority_evidence_sha256",
    "backfill_qc_reference_status",
    "backfill_qc_reference_evidence_level",
    "backfill_matrix_rt_drift_status",
    "backfill_drift_evidence_level",
    "backfill_drift_compatible_status",
    "backfill_drift_corrected_delta_sec",
    "backfill_candidate_ms2_pattern_status",
    "backfill_candidate_ms2_evidence_level",
    "backfill_candidate_ms2_product_authority_status",
    "backfill_candidate_ms2_product_authority_scope",
    "backfill_candidate_ms2_product_authority_source",
    "backfill_candidate_ms2_product_authority_reason",
    "backfill_candidate_ms2_product_authority_evidence_sha256",
    "backfill_ms2_trigger_scan_count",
    "backfill_strict_nl_scan_count",
    "backfill_ms2_trace_strength",
    "backfill_dda_missing_nl_policy_status",
    "backfill_family_ms2_required_tag_status",
    "backfill_evidence_reason",
    "group_hypothesis_id",
    "public_family_id",
    "group_construction_role",
    "group_delivery_role",
    "group_membership_source",
    "gap_fill_state",
    "gap_fill_reason",
    "missing_observation_state",
    "peak_hypothesis_status",
    "product_selection_blocker",
    "rt_mode_status",
    "group_claim_state",
    "claim_winner_group_hypothesis_id",
    "claim_source_group_hypothesis_id",
    "consolidation_state",
    "consolidation_winner_group_hypothesis_id",
    "consolidation_source_group_hypothesis_id",
)

_REGION_AUDIT_FIELDS = frozenset(
    {
        "region_candidate_count",
        "region_selected_proposal_sources",
        "region_selected_merge_note",
        "region_shadow_status",
        "region_shadow_verdict",
        "region_merge_suggestion_source",
        "region_area_ratio",
        "region_selected_interval_count",
        "region_selected_interval_gap_max_min",
        "region_local_mixture_diagnostic",
        "region_local_mixture_reason",
        "region_review_reason",
        "region_decision_status",
        "region_decision_class",
        "region_product_action",
        "region_promotion_reason",
        "region_baseline_method",
    }
)


@dataclass(frozen=True, slots=True)
class OwnerBackfillPeakFactStageProjection:
    feature_family_id: str
    selected_integration_authority_content_id: str | None
    work_unit_key: str
    group_hypothesis_id: str
    public_family_id: str
    sample_stem: str
    candidate_index: int
    candidate_phase: str
    request_identity_id: str
    acquisition_source_id: str
    selection_subject_id: str
    query_rt_min: float
    trace_rt_start_min: float | None
    trace_rt_end_min: float | None
    feature_order_index: int = 0
    sample_order_index: int = 0
    _mint_token: InitVar[object | None] = None

    def __post_init__(self, _mint_token: object | None) -> None:
        if _mint_token is not _STAGE_PROJECTION_MINT_TOKEN:
            raise TypeError(
                "stage projection must be minted from a live Owner Backfill observation"
            )
        for name in (
            "feature_family_id",
            "work_unit_key",
            "group_hypothesis_id",
            "public_family_id",
            "sample_stem",
            "candidate_phase",
            "request_identity_id",
            "acquisition_source_id",
            "selection_subject_id",
        ):
            _required_text(getattr(self, name), field=name)
        for name in ("candidate_index", "feature_order_index", "sample_order_index"):
            _nonnegative_int(getattr(self, name), field=name)
        _validate_optional_range(
            self.trace_rt_start_min,
            self.trace_rt_end_min,
            label="trace",
        )
        _required_finite_float(self.query_rt_min, field="query_rt_min")


def owner_backfill_peak_fact_stage_projection_from_observation(
    observation: LiveOwnerBackfillPeakFactObservation,
    *,
    feature_order_index: int = 0,
    sample_order_index: int = 0,
) -> OwnerBackfillPeakFactStageProjection:
    """Project a complete owner-minted Alignment observation for provenance."""

    from xic_extractor.alignment.owner_backfill import (
        OwnerBackfillPeakFactObservation as LiveOwnerBackfillPeakFactObservation,
    )
    from xic_extractor.alignment.owner_backfill import (
        validate_owner_backfill_peak_fact_observation,
    )
    from xic_extractor.alignment.owner_backfill_request_plan import (
        OwnerBackfillLogicalRequestMember,
        OwnerBackfillRequestIdentity,
    )

    if not isinstance(observation, LiveOwnerBackfillPeakFactObservation):
        raise TypeError("observation must be owner-minted by Alignment")
    validate_owner_backfill_peak_fact_observation(observation)
    logical_request = observation.logical_request
    request_identity = observation.request_identity
    if not isinstance(logical_request, OwnerBackfillLogicalRequestMember):
        raise TypeError("observation logical_request must be owner-minted")
    if not isinstance(request_identity, OwnerBackfillRequestIdentity):
        raise TypeError("observation request_identity must be owner-minted")
    if not isinstance(observation.cell, AlignedCell):
        raise TypeError("observation cell must be an AlignedCell")
    evaluation = observation.physical_evaluation
    if not isinstance(evaluation, PhysicalPeakEvaluation):
        raise ValueError("complete provenance observation requires physical evaluation")
    validate_bounded_physical_peak_evaluation(evaluation)

    item = logical_request.item
    feature = item.feature
    cell = observation.cell
    question = evaluation.question
    observed_binding = (
        logical_request.group_hypothesis_id,
        getattr(feature, "group_hypothesis_id", None),
        getattr(feature, "public_family_id", None),
        getattr(feature, "feature_family_id", None),
        item.sample_stem,
        item.request,
        request_identity.sample_stem,
        request_identity.request,
        cell.group_hypothesis_id,
        cell.public_family_id,
        cell.cluster_id,
        cell.sample_stem,
    )
    expected_binding = (
        cell.group_hypothesis_id,
        cell.group_hypothesis_id,
        cell.public_family_id,
        cell.cluster_id,
        cell.sample_stem,
        request_identity.request,
        cell.sample_stem,
        request_identity.request,
        cell.group_hypothesis_id,
        cell.public_family_id,
        cell.cluster_id,
        cell.sample_stem,
    )
    if observed_binding != expected_binding:
        raise ValueError("live observation crosses Alignment owner bindings")

    expected_trace_range = (
        (question.context_rt_start_min, question.context_rt_end_min)
        if evaluation.prepared.trace_point_count
        else (None, None)
    )
    if (
        observation.trace_rt_start_min,
        observation.trace_rt_end_min,
    ) != expected_trace_range:
        raise ValueError("live observation crosses physical question trace context")

    selected_integration_id = None
    if observation.selected_for_output and evaluation.facts is not None:
        if observation.facts is None:
            raise ValueError("selected physical fact lacks bounded owner receipt")
        selected_integration_id = selected_integration_authority_projection(
            observation.facts.integration
        ).selected_integration_authority_content_id
    return OwnerBackfillPeakFactStageProjection(
        feature_family_id=cell.cluster_id,
        selected_integration_authority_content_id=selected_integration_id,
        work_unit_key=observation.work_unit_key,
        group_hypothesis_id=cell.group_hypothesis_id,
        public_family_id=cell.public_family_id,
        sample_stem=cell.sample_stem,
        candidate_index=observation.candidate_index,
        candidate_phase=observation.candidate_phase,
        request_identity_id=request_identity.request_identity_id,
        acquisition_source_id=(
            request_identity.acquisition_source.acquisition_source_id
        ),
        selection_subject_id=question.selection_subject_id,
        query_rt_min=question.query_rt_min,
        trace_rt_start_min=observation.trace_rt_start_min,
        trace_rt_end_min=observation.trace_rt_end_min,
        feature_order_index=feature_order_index,
        sample_order_index=sample_order_index,
        _mint_token=_STAGE_PROJECTION_MINT_TOKEN,
    )


@dataclass(frozen=True, slots=True)
class OwnerBackfillPeakFactObservation:
    stage: OwnerBackfillPeakFactStageProjection
    physical_evaluation: PhysicalPeakEvaluation

    def __post_init__(self) -> None:
        if not isinstance(self.stage, OwnerBackfillPeakFactStageProjection):
            raise TypeError("stage must be owner-minted")
        validate_bounded_physical_peak_evaluation(self.physical_evaluation)


@dataclass(frozen=True, slots=True)
class SelectedIntegrationAuthorityProjectionV1:
    rt_left_min: float
    rt_apex_min: float
    rt_right_min: float
    raw_apex_rt_min: float
    rt_width_min: float
    height_raw: float
    height_smoothed: float
    area_raw_counts_seconds: float
    integration_method: str
    boundary_sources: tuple[str, ...]
    area_baseline_corrected: float | None
    baseline_type: str
    raw_scan_indices: tuple[int, ...]
    area_ms1_morphology: float | None
    ms1_morphology_area_source: str
    ms1_morphology_trace_method: str
    ms1_morphology_trace_window_points: int | None
    ms1_morphology_trace_effective_points: int | None

    @property
    def selected_integration_authority_content_id(self) -> str:
        return _authority_id(
            SELECTED_INTEGRATION_AUTHORITY_ID_PREFIX,
            SELECTED_INTEGRATION_AUTHORITY_SCHEMA_VERSION,
            tuple(
                (name, getattr(self, name))
                for name in SELECTED_INTEGRATION_AUTHORITY_FIELDS
            ),
        )


@dataclass(frozen=True, slots=True)
class OwnerBackfillAuthorityCellProjectionV2:
    work_unit_key: str
    acquisition_source_id: str
    profile_id: str
    selected_fact_content_id: str
    physical_answer_id: str
    physical_segment_id: str
    sample_stem: str
    cluster_id: str
    status: str
    area: float | None
    apex_rt: float | None
    height: float | None
    peak_start_rt: float | None
    peak_end_rt: float | None
    rt_delta_sec: float | None
    trace_quality: str
    scan_support_score: float | None
    source_candidate_id: str | None
    reason: str
    selected_integration_authority_content_id: str
    backfill_seed_mz: float | None
    backfill_seed_rt: float | None
    backfill_request_rt_min: float | None
    backfill_request_rt_max: float | None
    backfill_request_ppm: float | None
    backfill_ms1_pattern_status: str
    backfill_ms1_pattern_evidence_level: str
    backfill_ms1_product_authority_status: str
    backfill_ms1_product_authority_scope: str
    backfill_ms1_product_authority_source: str
    backfill_ms1_product_authority_reason: str
    backfill_ms1_product_authority_evidence_sha256: str
    backfill_qc_reference_status: str
    backfill_qc_reference_evidence_level: str
    backfill_matrix_rt_drift_status: str
    backfill_drift_evidence_level: str
    backfill_drift_compatible_status: str
    backfill_drift_corrected_delta_sec: float | None
    backfill_candidate_ms2_pattern_status: str
    backfill_candidate_ms2_evidence_level: str
    backfill_candidate_ms2_product_authority_status: str
    backfill_candidate_ms2_product_authority_scope: str
    backfill_candidate_ms2_product_authority_source: str
    backfill_candidate_ms2_product_authority_reason: str
    backfill_candidate_ms2_product_authority_evidence_sha256: str
    backfill_ms2_trigger_scan_count: int | None
    backfill_strict_nl_scan_count: int | None
    backfill_ms2_trace_strength: str
    backfill_dda_missing_nl_policy_status: str
    backfill_family_ms2_required_tag_status: str
    backfill_evidence_reason: str
    group_hypothesis_id: str
    public_family_id: str
    group_construction_role: str
    group_delivery_role: str
    group_membership_source: str
    gap_fill_state: str
    gap_fill_reason: str
    missing_observation_state: str
    peak_hypothesis_status: str
    product_selection_blocker: str
    rt_mode_status: str
    group_claim_state: str
    claim_winner_group_hypothesis_id: str
    claim_source_group_hypothesis_id: str
    consolidation_state: str
    consolidation_winner_group_hypothesis_id: str
    consolidation_source_group_hypothesis_id: str

    @property
    def owner_backfill_cell_content_id(self) -> str:
        return _authority_id(
            OWNER_BACKFILL_AUTHORITY_CELL_ID_PREFIX,
            OWNER_BACKFILL_AUTHORITY_CELL_SCHEMA_VERSION,
            tuple(
                (name, getattr(self, name))
                for name in OWNER_BACKFILL_AUTHORITY_CELL_FIELDS
            ),
        )


@dataclass(frozen=True, slots=True)
class OwnerBackfillCellProjectionV2:
    group_hypothesis_id: str
    public_family_id: str
    sample_stem: str
    profile_id: str
    selected_work_unit_key: str
    selected_fact_content_id: str
    owner_backfill_cell_content_id: str
    production_cell_decision_id: str
    cell_status: str
    consolidation_state: str
    consolidation_winner_group_hypothesis_id: str
    consolidation_source_group_hypothesis_id: str
    matrix_value_counts_seconds: float
    matrix_value_source: str
    writer_schema_version: str = OWNER_BACKFILL_PROVENANCE_WRITER_SCHEMA_VERSION

    @property
    def cell_projection_id(self) -> str:
        return _authority_id(
            OWNER_BACKFILL_CELL_PROJECTION_ID_PREFIX,
            OWNER_BACKFILL_CELL_PROJECTION_SCHEMA_VERSION,
            (
                ("group_hypothesis_id", self.group_hypothesis_id),
                ("public_family_id", self.public_family_id),
                ("sample_stem", self.sample_stem),
                ("profile_id", self.profile_id),
                ("selected_work_unit_key", self.selected_work_unit_key),
                ("selected_fact_content_id", self.selected_fact_content_id),
                ("owner_backfill_cell_content_id", self.owner_backfill_cell_content_id),
                ("production_cell_decision_id", self.production_cell_decision_id),
                ("cell_status", self.cell_status),
                ("consolidation_state", self.consolidation_state),
                (
                    "consolidation_winner_group_hypothesis_id",
                    self.consolidation_winner_group_hypothesis_id,
                ),
                (
                    "consolidation_source_group_hypothesis_id",
                    self.consolidation_source_group_hypothesis_id,
                ),
                ("matrix_value_counts_seconds", self.matrix_value_counts_seconds),
                ("matrix_value_source", self.matrix_value_source),
                ("writer_schema_version", self.writer_schema_version),
            ),
        )


@dataclass(frozen=True, slots=True)
class OwnerBackfillPeakFactProvenanceRow:
    row_kind: RowKind
    schema_version: str = OWNER_BACKFILL_PEAK_FACT_PROVENANCE_SCHEMA_VERSION
    work_unit_key: str = ""
    group_hypothesis_id: str = ""
    public_family_id: str = ""
    sample_stem: str = ""
    candidate_index: int | None = None
    candidate_phase: str = ""
    request_identity_id: str = ""
    acquisition_source_id: str = ""
    trace_content_id: str = ""
    trace_point_count: int | None = None
    trace_rt_start_min: float | None = None
    trace_rt_end_min: float | None = None
    profile_schema_version: str = ""
    profile_id: str = ""
    profile_hash: str = ""
    signal_processing_method: str = ""
    signal_processing_window_points: int | None = None
    physical_signal_formula: str = ""
    prepared_signal_id: str = ""
    segment_universe_id: str = ""
    segment_candidate_ids: tuple[str, ...] = ()
    physical_question_schema_version: str = ""
    physical_question_id: str = ""
    selection_subject_id: str = ""
    query_rt_min: float | None = None
    context_rt_start_min: float | None = None
    context_rt_end_min: float | None = None
    admissible_segment_ids: tuple[str, ...] = ()
    selection_policy_version: str = ""
    state: PhysicalPeakState | Literal[""] = ""
    reason: str = ""
    physical_answer_id: str = ""
    physical_segment_id: str = ""
    apex_rt_min: float | None = None
    left_rt_min: float | None = None
    right_rt_min: float | None = None
    height: float | None = None
    scan_support_count: int | None = None
    area_counts_seconds: float | None = None
    area_source: str = ""
    relative_shape_schema_version: str = ""
    relative_shape_point_count: int | None = None
    relative_shape_content_hash: str = ""
    fact_content_id: str = ""
    selected_work_unit_key: str = ""
    selected_fact_content_id: str = ""
    owner_backfill_cell_content_id: str = ""
    production_cell_decision_id: str = ""
    cell_status: str = ""
    consolidation_state: str = ""
    consolidation_winner_group_hypothesis_id: str = ""
    consolidation_source_group_hypothesis_id: str = ""
    matrix_value_counts_seconds: float | None = None
    matrix_value_source: str = ""
    writer_schema_version: str = ""
    cell_projection_id: str = ""

    @property
    def segment_candidate_count(self) -> int | None:
        return (
            len(self.segment_candidate_ids)
            if self.row_kind == "physical_question"
            else None
        )

    @property
    def admissible_segment_count(self) -> int | None:
        return (
            len(self.admissible_segment_ids)
            if self.row_kind == "physical_question"
            else None
        )

    @property
    def projection_content_id(self) -> str:
        payload = [
            _semantic_field_text(self, name)
            for name in OWNER_BACKFILL_PEAK_FACT_PROVENANCE_COLUMNS[:-1]
        ]
        digest = hashlib.sha256(_compact_json_bytes(payload)).hexdigest()
        return f"{OWNER_BACKFILL_PROVENANCE_ROW_ID_PREFIX}{digest}"

    def __post_init__(self) -> None:
        _validate_row(self)


@dataclass(frozen=True, slots=True)
class PreparedOwnerBackfillPeakFact:
    feature_family_id: str
    stage: OwnerBackfillPeakFactStageProjection
    physical_row: OwnerBackfillPeakFactProvenanceRow
    selected_integration_authority: SelectedIntegrationAuthorityProjectionV1 | None
    selected_integration_authority_content_id: str | None

    def __post_init__(self) -> None:
        self.validate_binding()

    def validate_binding(self) -> None:
        if not self.feature_family_id:
            raise ValueError("prepared Backfill peak fact requires feature identity")
        if self.feature_family_id != self.stage.feature_family_id:
            raise ValueError("prepared Backfill peak fact crosses feature identity")
        if self.physical_row.row_kind != "physical_question":
            raise ValueError("prepared Backfill peak fact requires a physical row")
        try:
            _compare_stage_to_row(self.stage, self.physical_row)
        except ValueError as exc:
            raise ValueError(
                "prepared Backfill peak fact crosses stage identity"
            ) from exc
        integration = self.selected_integration_authority
        if integration is None:
            if (
                self.selected_integration_authority_content_id is not None
                or self.stage.selected_integration_authority_content_id is not None
            ):
                raise ValueError(
                    "prepared Backfill peak fact crosses selected integration"
                )
            return
        if (
            self.selected_integration_authority_content_id
            != integration.selected_integration_authority_content_id
        ):
            raise ValueError("prepared Backfill peak fact crosses selected integration")
        if (
            self.stage.selected_integration_authority_content_id
            != integration.selected_integration_authority_content_id
        ):
            raise ValueError("prepared Backfill peak fact crosses selected integration")
        if (
            integration.rt_left_min,
            integration.rt_apex_min,
            integration.rt_right_min,
            integration.area_ms1_morphology,
        ) != (
            self.physical_row.left_rt_min,
            self.physical_row.apex_rt_min,
            self.physical_row.right_rt_min,
            self.physical_row.area_counts_seconds,
        ):
            raise ValueError(
                "prepared Backfill peak fact crosses selected integration facts"
            )


@dataclass(frozen=True, slots=True)
class OwnerBackfillPeakFactProvenanceBatch:
    rows: tuple[OwnerBackfillPeakFactProvenanceRow, ...]

    def __post_init__(self) -> None:
        _validate_batch(self.rows)


@dataclass(frozen=True, slots=True)
class OwnerBackfillPeakFactProvenanceReadResult:
    rows: tuple[OwnerBackfillPeakFactProvenanceRow, ...]
    projection_integrity: Literal["verified"] = "verified"
    stage_claim: Literal["unavailable_without_live_owner"] = (
        "unavailable_without_live_owner"
    )
    physical_claim: Literal["unavailable_without_live_owner"] = (
        "unavailable_without_live_owner"
    )
    cell_projection_claim: Literal["unavailable_without_live_owner"] = (
        "unavailable_without_live_owner"
    )


@dataclass(frozen=True, slots=True)
class OwnerBackfillPhysicalClaim:
    work_unit_key: str
    stage_claim: ClaimState
    physical_claim: ClaimState


@dataclass(frozen=True, slots=True)
class OwnerBackfillCellClaim:
    group_hypothesis_id: str
    sample_stem: str
    cell_projection_claim: ClaimState


@dataclass(frozen=True, slots=True)
class OwnerBackfillPeakFactCompositionResult:
    physical_claims: tuple[OwnerBackfillPhysicalClaim, ...]
    cell_claims: tuple[OwnerBackfillCellClaim, ...]


def selected_integration_authority_projection(
    integration: IntegrationResult | None,
) -> SelectedIntegrationAuthorityProjectionV1:
    if integration is None:
        raise ValueError("selected integration is required")
    # Scan indexes bind the owner-local physical trace only.  This bounded
    # value-authority projection intentionally retains the historical empty
    # public field while the physical owner keeps its exact selected span.
    raw_scan_indices: tuple[int, ...] = ()
    return SelectedIntegrationAuthorityProjectionV1(
        rt_left_min=integration.rt_left_min,
        rt_apex_min=integration.rt_apex_min,
        rt_right_min=integration.rt_right_min,
        raw_apex_rt_min=integration.raw_apex_rt_min,
        rt_width_min=integration.rt_width_min,
        height_raw=integration.height_raw,
        height_smoothed=integration.height_smoothed,
        area_raw_counts_seconds=integration.area_raw_counts_seconds,
        integration_method=integration.integration_method,
        boundary_sources=tuple(integration.boundary_sources),
        area_baseline_corrected=integration.area_baseline_corrected,
        baseline_type=integration.baseline_type,
        raw_scan_indices=raw_scan_indices,
        area_ms1_morphology=integration.area_ms1_morphology,
        ms1_morphology_area_source=integration.ms1_morphology_area_source,
        ms1_morphology_trace_method=integration.ms1_morphology_trace_method,
        ms1_morphology_trace_window_points=(
            integration.ms1_morphology_trace_window_points
        ),
        ms1_morphology_trace_effective_points=(
            integration.ms1_morphology_trace_effective_points
        ),
    )


def owner_backfill_cell_lineage_matches(
    stage: OwnerBackfillPeakFactStageProjection,
    cell: AlignedCell,
) -> bool:
    """Return whether a live final cell legally represents one owner stage.

    This is identity-only validation.  It deliberately never compares areas,
    RTs, or other selected values.  The source public-family label is an output
    alias and is therefore only required for an un-consolidated exact match.
    """

    if not isinstance(stage, OwnerBackfillPeakFactStageProjection):
        raise TypeError("stage must be owner-minted")
    if not isinstance(cell, AlignedCell):
        raise TypeError("cell must be an AlignedCell")
    if stage.sample_stem != cell.sample_stem:
        return False
    state = cell.consolidation_state or "not_consolidated"
    if state == "moved_to_primary_winner":
        return (
            cell.consolidation_source_group_hypothesis_id == stage.group_hypothesis_id
            and cell.consolidation_winner_group_hypothesis_id
            == cell.group_hypothesis_id
        )
    if stage.group_hypothesis_id != cell.group_hypothesis_id:
        return False
    if stage.public_family_id != cell.public_family_id:
        return False
    if state in {"not_consolidated", ""}:
        return not (
            cell.consolidation_winner_group_hypothesis_id
            or cell.consolidation_source_group_hypothesis_id
        )
    if state == "primary_winner":
        return (
            cell.consolidation_winner_group_hypothesis_id == cell.group_hypothesis_id
            and stage.group_hypothesis_id
            in cell.consolidation_source_group_hypothesis_id.split(";")
        )
    return False


def _require_owner_backfill_cell_lineage(
    stage: OwnerBackfillPeakFactStageProjection,
    cell: AlignedCell,
) -> None:
    if not owner_backfill_cell_lineage_matches(stage, cell):
        raise ValueError("selected stage crosses its live Alignment cell lineage")


def owner_backfill_authority_cell_projection(
    *,
    stage: OwnerBackfillPeakFactStageProjection,
    cell: AlignedCell,
    profile_id: str,
    selected_fact_content_id: str,
    physical_answer_id: str,
    physical_segment_id: str,
) -> OwnerBackfillAuthorityCellProjectionV2:
    if not isinstance(stage, OwnerBackfillPeakFactStageProjection):
        raise TypeError("stage must be owner-minted")
    if not isinstance(cell, AlignedCell):
        raise TypeError("cell must be an AlignedCell")
    _require_owner_backfill_cell_lineage(stage, cell)
    integration = selected_integration_authority_projection(cell.selected_integration)
    missing = tuple(
        name
        for name in OWNER_BACKFILL_AUTHORITY_CELL_FIELDS
        if not hasattr(cell, name)
        and name
        not in {
            "work_unit_key",
            "acquisition_source_id",
            "profile_id",
            "selected_fact_content_id",
            "physical_answer_id",
            "physical_segment_id",
            "selected_integration_authority_content_id",
        }
    )
    if missing:
        raise ValueError(f"live Alignment cell lacks authority fields: {missing!r}")
    return OwnerBackfillAuthorityCellProjectionV2(
        work_unit_key=stage.work_unit_key,
        acquisition_source_id=stage.acquisition_source_id,
        profile_id=profile_id,
        selected_fact_content_id=selected_fact_content_id,
        physical_answer_id=physical_answer_id,
        physical_segment_id=physical_segment_id,
        sample_stem=cell.sample_stem,
        cluster_id=cell.cluster_id,
        status=cell.status,
        area=cell.area,
        apex_rt=cell.apex_rt,
        height=cell.height,
        peak_start_rt=cell.peak_start_rt,
        peak_end_rt=cell.peak_end_rt,
        rt_delta_sec=cell.rt_delta_sec,
        trace_quality=cell.trace_quality,
        scan_support_score=cell.scan_support_score,
        source_candidate_id=cell.source_candidate_id,
        reason=cell.reason,
        selected_integration_authority_content_id=(
            integration.selected_integration_authority_content_id
        ),
        backfill_seed_mz=cell.backfill_seed_mz,
        backfill_seed_rt=cell.backfill_seed_rt,
        backfill_request_rt_min=cell.backfill_request_rt_min,
        backfill_request_rt_max=cell.backfill_request_rt_max,
        backfill_request_ppm=cell.backfill_request_ppm,
        backfill_ms1_pattern_status=cell.backfill_ms1_pattern_status,
        backfill_ms1_pattern_evidence_level=(cell.backfill_ms1_pattern_evidence_level),
        backfill_ms1_product_authority_status=(
            cell.backfill_ms1_product_authority_status
        ),
        backfill_ms1_product_authority_scope=(
            cell.backfill_ms1_product_authority_scope
        ),
        backfill_ms1_product_authority_source=(
            cell.backfill_ms1_product_authority_source
        ),
        backfill_ms1_product_authority_reason=(
            cell.backfill_ms1_product_authority_reason
        ),
        backfill_ms1_product_authority_evidence_sha256=(
            cell.backfill_ms1_product_authority_evidence_sha256
        ),
        backfill_qc_reference_status=cell.backfill_qc_reference_status,
        backfill_qc_reference_evidence_level=(
            cell.backfill_qc_reference_evidence_level
        ),
        backfill_matrix_rt_drift_status=cell.backfill_matrix_rt_drift_status,
        backfill_drift_evidence_level=cell.backfill_drift_evidence_level,
        backfill_drift_compatible_status=cell.backfill_drift_compatible_status,
        backfill_drift_corrected_delta_sec=(cell.backfill_drift_corrected_delta_sec),
        backfill_candidate_ms2_pattern_status=(
            cell.backfill_candidate_ms2_pattern_status
        ),
        backfill_candidate_ms2_evidence_level=(
            cell.backfill_candidate_ms2_evidence_level
        ),
        backfill_candidate_ms2_product_authority_status=(
            cell.backfill_candidate_ms2_product_authority_status
        ),
        backfill_candidate_ms2_product_authority_scope=(
            cell.backfill_candidate_ms2_product_authority_scope
        ),
        backfill_candidate_ms2_product_authority_source=(
            cell.backfill_candidate_ms2_product_authority_source
        ),
        backfill_candidate_ms2_product_authority_reason=(
            cell.backfill_candidate_ms2_product_authority_reason
        ),
        backfill_candidate_ms2_product_authority_evidence_sha256=(
            cell.backfill_candidate_ms2_product_authority_evidence_sha256
        ),
        backfill_ms2_trigger_scan_count=cell.backfill_ms2_trigger_scan_count,
        backfill_strict_nl_scan_count=cell.backfill_strict_nl_scan_count,
        backfill_ms2_trace_strength=cell.backfill_ms2_trace_strength,
        backfill_dda_missing_nl_policy_status=(
            cell.backfill_dda_missing_nl_policy_status
        ),
        backfill_family_ms2_required_tag_status=(
            cell.backfill_family_ms2_required_tag_status
        ),
        backfill_evidence_reason=cell.backfill_evidence_reason,
        group_hypothesis_id=cell.group_hypothesis_id,
        public_family_id=cell.public_family_id,
        group_construction_role=cell.group_construction_role,
        group_delivery_role=cell.group_delivery_role,
        group_membership_source=cell.group_membership_source,
        gap_fill_state=cell.gap_fill_state,
        gap_fill_reason=cell.gap_fill_reason,
        missing_observation_state=cell.missing_observation_state,
        peak_hypothesis_status=cell.peak_hypothesis_status,
        product_selection_blocker=cell.product_selection_blocker,
        rt_mode_status=cell.rt_mode_status,
        group_claim_state=cell.group_claim_state,
        claim_winner_group_hypothesis_id=(cell.claim_winner_group_hypothesis_id),
        claim_source_group_hypothesis_id=cell.claim_source_group_hypothesis_id,
        consolidation_state=cell.consolidation_state,
        consolidation_winner_group_hypothesis_id=(
            cell.consolidation_winner_group_hypothesis_id
        ),
        consolidation_source_group_hypothesis_id=(
            cell.consolidation_source_group_hypothesis_id
        ),
    )


def owner_backfill_cell_projection(
    *,
    authority_cell: OwnerBackfillAuthorityCellProjectionV2,
    production_decision: ProductionCellDecisionProjectionV2,
) -> OwnerBackfillCellProjectionV2:
    if (
        production_decision.owner_backfill_cell_content_id
        != authority_cell.owner_backfill_cell_content_id
    ):
        raise ValueError("production decision crosses the selected Alignment cell")
    if (
        not production_decision.write_matrix_value
        or production_decision.matrix_value is None
    ):
        raise ValueError("blank/nonwritten cells do not have provenance cell rows")
    if (
        production_decision.feature_family_id != authority_cell.cluster_id
        or production_decision.sample_stem != authority_cell.sample_stem
        or production_decision.raw_status != authority_cell.status
    ):
        raise ValueError("production decision crosses its authority cell")
    return OwnerBackfillCellProjectionV2(
        group_hypothesis_id=authority_cell.group_hypothesis_id,
        public_family_id=authority_cell.public_family_id,
        sample_stem=authority_cell.sample_stem,
        profile_id=authority_cell.profile_id,
        selected_work_unit_key=authority_cell.work_unit_key,
        selected_fact_content_id=authority_cell.selected_fact_content_id,
        owner_backfill_cell_content_id=authority_cell.owner_backfill_cell_content_id,
        production_cell_decision_id=production_decision.production_cell_decision_id,
        cell_status=authority_cell.status,
        consolidation_state=authority_cell.consolidation_state,
        consolidation_winner_group_hypothesis_id=(
            authority_cell.consolidation_winner_group_hypothesis_id
        ),
        consolidation_source_group_hypothesis_id=(
            authority_cell.consolidation_source_group_hypothesis_id
        ),
        matrix_value_counts_seconds=production_decision.matrix_value,
        matrix_value_source=production_decision.matrix_value_source,
    )


def row_from_terminal_observation(
    stage: OwnerBackfillPeakFactStageProjection,
    physical_evaluation: PhysicalPeakEvaluation,
    *,
    profile: SignalProcessingProfile,
) -> OwnerBackfillPeakFactProvenanceRow:
    validate_bounded_physical_peak_evaluation(physical_evaluation)
    prepared = physical_evaluation.prepared
    universe = physical_evaluation.segment_universe
    question = physical_evaluation.question
    answer = physical_evaluation.answer
    facts_value = physical_evaluation.facts
    if prepared.profile_id != profile.profile_id:
        raise ValueError("physical evaluation crosses signal-processing profile")
    if (stage.selection_subject_id, stage.query_rt_min) != (
        question.selection_subject_id,
        question.query_rt_min,
    ):
        raise ValueError("stage crosses the owner physical question")
    if prepared.trace_point_count:
        expected_range = (question.context_rt_start_min, question.context_rt_end_min)
        if (stage.trace_rt_start_min, stage.trace_rt_end_min) != expected_range:
            raise ValueError("stage trace range differs from owner-derived context")
    elif stage.trace_rt_start_min is not None or stage.trace_rt_end_min is not None:
        raise ValueError("zero-point trace must not assert observed RT endpoints")
    return OwnerBackfillPeakFactProvenanceRow(
        row_kind="physical_question",
        work_unit_key=stage.work_unit_key,
        group_hypothesis_id=stage.group_hypothesis_id,
        public_family_id=stage.public_family_id,
        sample_stem=stage.sample_stem,
        candidate_index=stage.candidate_index,
        candidate_phase=stage.candidate_phase,
        request_identity_id=stage.request_identity_id,
        acquisition_source_id=stage.acquisition_source_id,
        trace_content_id=prepared.trace_content_id,
        trace_point_count=prepared.trace_point_count,
        trace_rt_start_min=stage.trace_rt_start_min,
        trace_rt_end_min=stage.trace_rt_end_min,
        profile_schema_version=profile.schema_version,
        profile_id=profile.profile_id,
        profile_hash=profile.profile_hash,
        signal_processing_method=profile.method,
        signal_processing_window_points=profile.window_points,
        physical_signal_formula=profile.physical_signal_formula,
        prepared_signal_id=prepared.prepared_signal_id,
        segment_universe_id=universe.segment_universe_id,
        segment_candidate_ids=universe.segment_ids,
        physical_question_schema_version=question.schema_version,
        physical_question_id=question.physical_question_id,
        selection_subject_id=question.selection_subject_id,
        query_rt_min=question.query_rt_min,
        context_rt_start_min=question.context_rt_start_min,
        context_rt_end_min=question.context_rt_end_min,
        admissible_segment_ids=question.admissible_segment_ids,
        selection_policy_version=question.selection_policy_version,
        state=physical_evaluation.state,
        reason=physical_evaluation.reason,
        physical_answer_id=answer.physical_answer_id,
        physical_segment_id=facts_value.physical_segment_id if facts_value else "",
        apex_rt_min=facts_value.apex_rt_min if facts_value else None,
        left_rt_min=facts_value.left_rt_min if facts_value else None,
        right_rt_min=facts_value.right_rt_min if facts_value else None,
        height=facts_value.height if facts_value else None,
        scan_support_count=facts_value.scan_support_count if facts_value else None,
        area_counts_seconds=facts_value.area_counts_seconds if facts_value else None,
        area_source=facts_value.area_source if facts_value else "",
        relative_shape_schema_version=(
            facts_value.relative_shape_schema_version if facts_value else ""
        ),
        relative_shape_point_count=(
            facts_value.relative_shape_point_count if facts_value else None
        ),
        relative_shape_content_hash=(
            facts_value.relative_shape_content_hash if facts_value else ""
        ),
        fact_content_id=facts_value.fact_content_id if facts_value else "",
    )


def prepare_owner_backfill_peak_fact(
    observation: LiveOwnerBackfillPeakFactObservation,
    *,
    feature_order_index: int,
    sample_order_index: int,
    profile: SignalProcessingProfile,
) -> PreparedOwnerBackfillPeakFact:
    stage = owner_backfill_peak_fact_stage_projection_from_observation(
        observation,
        feature_order_index=feature_order_index,
        sample_order_index=sample_order_index,
    )
    evaluation = observation.physical_evaluation
    if evaluation is None:
        raise ValueError(
            "covered Untargeted provenance requires canonical physical evaluation"
        )
    selected_integration = None
    if observation.selected_for_output and evaluation.facts is not None:
        if observation.facts is None:
            raise ValueError("selected physical fact lacks bounded owner receipt")
        selected_integration = selected_integration_authority_projection(
            observation.facts.integration
        )
    return PreparedOwnerBackfillPeakFact(
        feature_family_id=observation.cell.cluster_id,
        stage=stage,
        physical_row=row_from_terminal_observation(
            stage,
            evaluation,
            profile=profile,
        ),
        selected_integration_authority=selected_integration,
        selected_integration_authority_content_id=(
            selected_integration.selected_integration_authority_content_id
            if selected_integration is not None
            else None
        ),
    )


def _run_profile_row(
    profile: SignalProcessingProfile,
) -> OwnerBackfillPeakFactProvenanceRow:
    return OwnerBackfillPeakFactProvenanceRow(
        row_kind="run_profile",
        profile_schema_version=profile.schema_version,
        profile_id=profile.profile_id,
        profile_hash=profile.profile_hash,
        signal_processing_method=profile.method,
        signal_processing_window_points=profile.window_points,
        physical_signal_formula=profile.physical_signal_formula,
    )


def _cell_projection_row(
    projection: OwnerBackfillCellProjectionV2,
    profile: SignalProcessingProfile,
) -> OwnerBackfillPeakFactProvenanceRow:
    if projection.profile_id != profile.profile_id:
        raise ValueError("cell projection crosses signal-processing profile")
    return OwnerBackfillPeakFactProvenanceRow(
        row_kind="cell_projection",
        group_hypothesis_id=projection.group_hypothesis_id,
        public_family_id=projection.public_family_id,
        sample_stem=projection.sample_stem,
        profile_schema_version=profile.schema_version,
        profile_id=profile.profile_id,
        profile_hash=profile.profile_hash,
        signal_processing_method=profile.method,
        signal_processing_window_points=profile.window_points,
        physical_signal_formula=profile.physical_signal_formula,
        selected_work_unit_key=projection.selected_work_unit_key,
        selected_fact_content_id=projection.selected_fact_content_id,
        owner_backfill_cell_content_id=projection.owner_backfill_cell_content_id,
        production_cell_decision_id=projection.production_cell_decision_id,
        cell_status=projection.cell_status,
        consolidation_state=projection.consolidation_state,
        consolidation_winner_group_hypothesis_id=(
            projection.consolidation_winner_group_hypothesis_id
        ),
        consolidation_source_group_hypothesis_id=(
            projection.consolidation_source_group_hypothesis_id
        ),
        matrix_value_counts_seconds=projection.matrix_value_counts_seconds,
        matrix_value_source=projection.matrix_value_source,
        writer_schema_version=projection.writer_schema_version,
        cell_projection_id=projection.cell_projection_id,
    )


def build_owner_backfill_peak_fact_provenance_batch(
    observations: Sequence[OwnerBackfillPeakFactObservation],
    *,
    profile: SignalProcessingProfile,
    cell_projections: Sequence[OwnerBackfillCellProjectionV2] = (),
) -> OwnerBackfillPeakFactProvenanceBatch:
    ordered_physical = sorted(
        observations,
        key=lambda item: (
            item.stage.feature_order_index,
            item.stage.sample_order_index,
            item.stage.candidate_index,
            item.stage.candidate_phase,
        ),
    )
    return OwnerBackfillPeakFactProvenanceBatch(
        rows=(
            _run_profile_row(profile),
            *(
                row_from_terminal_observation(
                    item.stage,
                    item.physical_evaluation,
                    profile=profile,
                )
                for item in ordered_physical
            ),
            *(_cell_projection_row(item, profile) for item in cell_projections),
        )
    )


def build_owner_backfill_peak_fact_provenance_batch_from_rows(
    physical_rows: Sequence[OwnerBackfillPeakFactProvenanceRow],
    *,
    profile: SignalProcessingProfile,
    cell_projections: Sequence[OwnerBackfillCellProjectionV2] = (),
) -> OwnerBackfillPeakFactProvenanceBatch:
    """Freeze already owner-projected physical rows with final cell authority."""

    if any(row.row_kind != "physical_question" for row in physical_rows):
        raise ValueError("prepared Backfill rows must be physical-question rows")
    return OwnerBackfillPeakFactProvenanceBatch(
        rows=(
            _run_profile_row(profile),
            *physical_rows,
            *(_cell_projection_row(item, profile) for item in cell_projections),
        )
    )


_PROVENANCE_ROWS_PER_CHUNK = 2048
_PROVENANCE_VERIFY_BLOCK_BYTES = 1 << 20


def write_owner_backfill_peak_fact_provenance_tsv(
    path: Path,
    batch: OwnerBackfillPeakFactProvenanceBatch,
) -> Path:
    # One provenance row per Backfill request, so this artifact scales with the
    # request population: an 85-sample full-audit batch renders to several GB.
    # Rendering it into one payload and then reading the whole temporary file
    # back to compare held three copies of the artifact in the Alignment parent
    # at once.  Stream the same bytes instead and verify the landed file by
    # digest, so peak memory is one chunk rather than one artifact.
    if not isinstance(batch, OwnerBackfillPeakFactProvenanceBatch):
        raise TypeError("batch must be a typed provenance batch")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        expected = hashlib.sha256()
        expected_size = 0
        with temporary.open("wb") as handle:
            for chunk in _iter_owner_backfill_peak_fact_provenance_chunks(batch):
                handle.write(chunk)
                expected.update(chunk)
                expected_size += len(chunk)
        landed = hashlib.sha256()
        landed_size = 0
        with temporary.open("rb") as handle:
            for block in iter(lambda: handle.read(_PROVENANCE_VERIFY_BLOCK_BYTES), b""):
                landed.update(block)
                landed_size += len(block)
        if landed_size != expected_size or landed.digest() != expected.digest():
            raise OSError("provenance temporary write verification failed")
        temporary.replace(path)
        return path
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)


def _iter_owner_backfill_peak_fact_provenance_chunks(
    batch: OwnerBackfillPeakFactProvenanceBatch,
) -> Iterator[bytes]:
    """Yield exactly the bytes render_...() returns, in row-aligned chunks."""

    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=OWNER_BACKFILL_PEAK_FACT_PROVENANCE_COLUMNS,
        delimiter="\t",
        lineterminator="\n",
    )
    writer.writeheader()
    pending = 0
    for row in batch.rows:
        writer.writerow(_row_projection(row))
        pending += 1
        if pending >= _PROVENANCE_ROWS_PER_CHUNK:
            yield buffer.getvalue().encode("utf-8")
            buffer.seek(0)
            buffer.truncate(0)
            pending = 0
    tail = buffer.getvalue()
    if tail:
        yield tail.encode("utf-8")


def render_owner_backfill_peak_fact_provenance_tsv(
    batch: OwnerBackfillPeakFactProvenanceBatch,
) -> bytes:
    if not isinstance(batch, OwnerBackfillPeakFactProvenanceBatch):
        raise TypeError("batch must be a typed provenance batch")
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=OWNER_BACKFILL_PEAK_FACT_PROVENANCE_COLUMNS,
        delimiter="\t",
        lineterminator="\n",
    )
    writer.writeheader()
    for row in batch.rows:
        writer.writerow(_row_projection(row))
    return buffer.getvalue().encode("utf-8")


def read_owner_backfill_peak_fact_provenance_tsv(
    path: Path,
) -> OwnerBackfillPeakFactProvenanceReadResult:
    payload = path.read_bytes()
    if payload.startswith(b"\xef\xbb\xbf"):
        raise ValueError(f"{path}: UTF-8 BOM is forbidden")
    if not payload.endswith(b"\n"):
        raise ValueError(f"{path}: provenance must end with one LF-terminated row")
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{path}: provenance must be strict UTF-8") from exc
    if "\r" in text:
        raise ValueError(f"{path}: provenance must use LF line endings")
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter="\t")
    _validate_exact_header(path, reader.fieldnames)
    rows: list[OwnerBackfillPeakFactProvenanceRow] = []
    for row_number, raw in enumerate(reader, start=2):
        try:
            projection = _strict_projection(raw)
            expected_id = projection.pop("projection_content_id")
            row = _row_from_projection(projection)
            if expected_id != row.projection_content_id:
                raise ValueError("projection_content_id does not match row content")
            rows.append(row)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{path}: row {row_number}: {exc}") from exc
    batch = OwnerBackfillPeakFactProvenanceBatch(tuple(rows))
    if render_owner_backfill_peak_fact_provenance_tsv(batch) != payload:
        raise ValueError(f"{path}: provenance bytes are not canonical writer output")
    return OwnerBackfillPeakFactProvenanceReadResult(rows=batch.rows)


_PROFILE_FIELDS = frozenset(
    {
        "profile_schema_version",
        "profile_id",
        "profile_hash",
        "signal_processing_method",
        "signal_processing_window_points",
        "physical_signal_formula",
    }
)
_STAGE_FIELDS = frozenset(
    {
        "work_unit_key",
        "group_hypothesis_id",
        "public_family_id",
        "sample_stem",
        "candidate_index",
        "candidate_phase",
        "request_identity_id",
        "acquisition_source_id",
    }
)
_TRACE_QUESTION_FIELDS = frozenset(
    {
        "trace_content_id",
        "trace_point_count",
        "trace_rt_start_min",
        "trace_rt_end_min",
        "prepared_signal_id",
        "segment_universe_id",
        "segment_candidate_count",
        "segment_candidate_ids",
        "physical_question_schema_version",
        "physical_question_id",
        "selection_subject_id",
        "query_rt_min",
        "context_rt_start_min",
        "context_rt_end_min",
        "admissible_segment_count",
        "admissible_segment_ids",
        "selection_policy_version",
        "state",
        "reason",
        "physical_answer_id",
        "physical_segment_id",
        "apex_rt_min",
        "left_rt_min",
        "right_rt_min",
        "height",
        "scan_support_count",
        "area_counts_seconds",
        "area_source",
        "relative_shape_schema_version",
        "relative_shape_point_count",
        "relative_shape_content_hash",
        "fact_content_id",
    }
)
_CELL_FIELDS = frozenset(
    {
        "selected_work_unit_key",
        "selected_fact_content_id",
        "owner_backfill_cell_content_id",
        "production_cell_decision_id",
        "cell_status",
        "consolidation_state",
        "consolidation_winner_group_hypothesis_id",
        "consolidation_source_group_hypothesis_id",
        "matrix_value_counts_seconds",
        "matrix_value_source",
        "writer_schema_version",
        "cell_projection_id",
    }
)
_ALWAYS_FIELDS = frozenset({"row_kind", "schema_version", "projection_content_id"})


def _validate_row(row: OwnerBackfillPeakFactProvenanceRow) -> None:
    if row.row_kind not in {"run_profile", "physical_question", "cell_projection"}:
        raise ValueError("unknown provenance row_kind")
    if row.schema_version != OWNER_BACKFILL_PEAK_FACT_PROVENANCE_SCHEMA_VERSION:
        raise ValueError("unsupported provenance schema_version")
    populated = _populated_fields(row)
    if row.row_kind == "run_profile":
        allowed = _ALWAYS_FIELDS | _PROFILE_FIELDS
    elif row.row_kind == "physical_question":
        allowed = (
            _ALWAYS_FIELDS | _PROFILE_FIELDS | _STAGE_FIELDS | _TRACE_QUESTION_FIELDS
        )
    else:
        allowed = (
            _ALWAYS_FIELDS
            | _PROFILE_FIELDS
            | {
                "group_hypothesis_id",
                "public_family_id",
                "sample_stem",
            }
            | _CELL_FIELDS
        )
    outside = populated - allowed
    if outside:
        raise ValueError(
            f"{row.row_kind} populates forbidden fields: {tuple(sorted(outside))!r}"
        )
    _validate_profile_fields(row)
    if row.row_kind == "physical_question":
        _validate_physical_row(row)
    elif row.row_kind == "cell_projection":
        _validate_cell_row(row)


def _validate_profile_fields(row: OwnerBackfillPeakFactProvenanceRow) -> None:
    for name in (
        "profile_schema_version",
        "profile_id",
        "profile_hash",
        "signal_processing_method",
        "physical_signal_formula",
    ):
        _required_text(getattr(row, name), field=name)
    canonical_profile = SignalProcessingProfile(
        schema_version=row.profile_schema_version,
        method=row.signal_processing_method,
        window_points=cast(int, row.signal_processing_window_points),
        physical_signal_formula=row.physical_signal_formula,
    )
    if row.profile_hash != canonical_profile.profile_hash:
        raise ValueError("profile_hash does not match canonical profile content")
    if row.profile_id != canonical_profile.profile_id:
        raise ValueError("profile_id does not match canonical profile content")


def _validate_physical_row(row: OwnerBackfillPeakFactProvenanceRow) -> None:
    for name in (
        "work_unit_key",
        "group_hypothesis_id",
        "public_family_id",
        "sample_stem",
        "candidate_phase",
        "request_identity_id",
        "acquisition_source_id",
        "trace_content_id",
        "prepared_signal_id",
        "segment_universe_id",
        "physical_question_schema_version",
        "physical_question_id",
        "selection_subject_id",
        "selection_policy_version",
        "state",
        "physical_answer_id",
    ):
        _required_text(cast(str, getattr(row, name)), field=name)
    _nonnegative_int(row.candidate_index, field="candidate_index")
    _nonnegative_int(row.trace_point_count, field="trace_point_count")
    _required_finite_float(row.query_rt_min, field="query_rt_min")
    _required_finite_float(row.context_rt_start_min, field="context_rt_start_min")
    _required_finite_float(row.context_rt_end_min, field="context_rt_end_min")
    _validate_optional_range(
        row.trace_rt_start_min, row.trace_rt_end_min, label="trace"
    )
    _validate_optional_range(
        row.context_rt_start_min, row.context_rt_end_min, label="context"
    )
    _unique_string_tuple(row.segment_candidate_ids, field="segment_candidate_ids")
    _unique_string_tuple(row.admissible_segment_ids, field="admissible_segment_ids")
    if not set(row.admissible_segment_ids).issubset(row.segment_candidate_ids):
        raise ValueError("admissible segments must be a subset of the segment universe")
    _require_typed_id(
        row.trace_content_id, prefix="trace-content.v1:", field="trace_content_id"
    )
    _require_typed_id(
        row.prepared_signal_id, prefix="prepared-signal.v1:", field="prepared_signal_id"
    )
    _require_typed_id(
        row.segment_universe_id,
        prefix="segment-universe.v1:",
        field="segment_universe_id",
    )
    _require_typed_id(
        row.physical_question_id,
        prefix="physical-question.v1:",
        field="physical_question_id",
    )
    _require_typed_id(
        row.physical_answer_id, prefix="physical-answer.v1:", field="physical_answer_id"
    )
    if row.state == "available":
        for name in (
            "physical_segment_id",
            "area_source",
            "relative_shape_schema_version",
            "relative_shape_content_hash",
            "fact_content_id",
        ):
            _required_text(cast(str, getattr(row, name)), field=name)
        for name in (
            "apex_rt_min",
            "left_rt_min",
            "right_rt_min",
            "height",
            "area_counts_seconds",
        ):
            _required_finite_float(cast(float | None, getattr(row, name)), field=name)
        _positive_int(row.scan_support_count, field="scan_support_count")
        _positive_int(
            row.relative_shape_point_count, field="relative_shape_point_count"
        )
        _require_typed_id(
            row.physical_segment_id,
            prefix="physical-segment.v1:",
            field="physical_segment_id",
        )
        _require_typed_id(
            row.fact_content_id, prefix="physical-facts.v1:", field="fact_content_id"
        )
        _require_lower_sha256(
            row.relative_shape_content_hash, field="relative_shape_content_hash"
        )
    elif row.state in {"unavailable", "failed"}:
        _required_text(row.reason, field="reason")
        terminal_fields = (
            row.physical_segment_id,
            row.apex_rt_min,
            row.left_rt_min,
            row.right_rt_min,
            row.height,
            row.scan_support_count,
            row.area_counts_seconds,
            row.area_source,
            row.relative_shape_schema_version,
            row.relative_shape_point_count,
            row.relative_shape_content_hash,
            row.fact_content_id,
        )
        if any(value not in {"", None} for value in terminal_fields):
            raise ValueError("non-available physical row must not assert facts")
    else:
        raise ValueError("unsupported physical state")


def _validate_cell_row(row: OwnerBackfillPeakFactProvenanceRow) -> None:
    for name in (
        "group_hypothesis_id",
        "public_family_id",
        "sample_stem",
        "selected_work_unit_key",
        "selected_fact_content_id",
        "owner_backfill_cell_content_id",
        "production_cell_decision_id",
        "cell_status",
        "consolidation_state",
        "matrix_value_source",
        "writer_schema_version",
        "cell_projection_id",
    ):
        _required_text(cast(str, getattr(row, name)), field=name)
    _required_finite_float(
        row.matrix_value_counts_seconds, field="matrix_value_counts_seconds"
    )
    for name in (
        "consolidation_winner_group_hypothesis_id",
        "consolidation_source_group_hypothesis_id",
    ):
        value = getattr(row, name)
        if value:
            _required_text(value, field=name)
    if row.writer_schema_version != OWNER_BACKFILL_PROVENANCE_WRITER_SCHEMA_VERSION:
        raise ValueError("unsupported provenance writer_schema_version")
    _require_typed_id(
        row.selected_fact_content_id,
        prefix="physical-facts.v1:",
        field="selected_fact_content_id",
    )
    _require_typed_id(
        row.owner_backfill_cell_content_id,
        prefix=OWNER_BACKFILL_AUTHORITY_CELL_ID_PREFIX,
        field="owner_backfill_cell_content_id",
    )
    _require_typed_id(
        row.production_cell_decision_id,
        prefix=PRODUCTION_CELL_DECISION_ID_PREFIX,
        field="production_cell_decision_id",
    )
    _require_typed_id(
        row.cell_projection_id,
        prefix=OWNER_BACKFILL_CELL_PROJECTION_ID_PREFIX,
        field="cell_projection_id",
    )
    expected = OwnerBackfillCellProjectionV2(
        group_hypothesis_id=row.group_hypothesis_id,
        public_family_id=row.public_family_id,
        sample_stem=row.sample_stem,
        profile_id=row.profile_id,
        selected_work_unit_key=row.selected_work_unit_key,
        selected_fact_content_id=row.selected_fact_content_id,
        owner_backfill_cell_content_id=row.owner_backfill_cell_content_id,
        production_cell_decision_id=row.production_cell_decision_id,
        cell_status=row.cell_status,
        consolidation_state=row.consolidation_state,
        consolidation_winner_group_hypothesis_id=(
            row.consolidation_winner_group_hypothesis_id
        ),
        consolidation_source_group_hypothesis_id=(
            row.consolidation_source_group_hypothesis_id
        ),
        matrix_value_counts_seconds=cast(float, row.matrix_value_counts_seconds),
        matrix_value_source=row.matrix_value_source,
        writer_schema_version=row.writer_schema_version,
    )
    if row.cell_projection_id != expected.cell_projection_id:
        raise ValueError("cell_projection_id does not match row content")


def _validate_batch(rows: Sequence[OwnerBackfillPeakFactProvenanceRow]) -> None:
    if not rows or rows[0].row_kind != "run_profile":
        raise ValueError("provenance requires one first run_profile row")
    if sum(row.row_kind == "run_profile" for row in rows) != 1:
        raise ValueError("provenance requires exactly one run_profile row")
    kinds = tuple(row.row_kind for row in rows)
    row_kind_order: dict[RowKind, int] = {
        "run_profile": 0,
        "physical_question": 1,
        "cell_projection": 2,
    }
    if kinds != tuple(sorted(kinds, key=row_kind_order.__getitem__)):
        raise ValueError("provenance row kinds are out of order")
    envelope = rows[0]
    work_rows: dict[str, OwnerBackfillPeakFactProvenanceRow] = {}
    cell_keys: set[tuple[str, str]] = set()
    for row in rows[1:]:
        if _profile_binding(row) != _profile_binding(envelope):
            raise ValueError("provenance row crosses the run profile")
        if row.row_kind == "physical_question":
            if row.work_unit_key in work_rows:
                raise ValueError("duplicate work_unit_key")
            work_rows[row.work_unit_key] = row
        else:
            key = (row.group_hypothesis_id, row.sample_stem)
            if key in cell_keys:
                raise ValueError("duplicate final cell key")
            cell_keys.add(key)
            selected = work_rows.get(row.selected_work_unit_key)
            if (
                selected is None
                or selected.fact_content_id != row.selected_fact_content_id
            ):
                raise ValueError("cell projection lacks its selected physical fact")
            if selected.state != "available":
                raise ValueError("cell projection selects a non-available physical row")
            if selected.sample_stem != row.sample_stem:
                raise ValueError("cell projection crosses selected work sample")
            if row.consolidation_state == "moved_to_primary_winner":
                if (
                    row.consolidation_source_group_hypothesis_id
                    != selected.group_hypothesis_id
                    or row.consolidation_winner_group_hypothesis_id
                    != row.group_hypothesis_id
                ):
                    raise ValueError("cell projection has invalid moved lineage")
            else:
                if (
                    selected.group_hypothesis_id,
                    selected.public_family_id,
                ) != (row.group_hypothesis_id, row.public_family_id):
                    raise ValueError("cell projection crosses selected work identity")
                if row.consolidation_state == "not_consolidated":
                    if (
                        row.consolidation_winner_group_hypothesis_id
                        or row.consolidation_source_group_hypothesis_id
                    ):
                        raise ValueError("unconsolidated cell must not assert lineage")
                elif row.consolidation_state == "primary_winner":
                    if (
                        row.consolidation_winner_group_hypothesis_id
                        != row.group_hypothesis_id
                        or selected.group_hypothesis_id
                        not in row.consolidation_source_group_hypothesis_id.split(";")
                    ):
                        raise ValueError(
                            "cell projection has invalid primary lineage"
                        )
                else:
                    raise ValueError("unsupported cell consolidation state")


def _row_projection(row: OwnerBackfillPeakFactProvenanceRow) -> dict[str, str]:
    return {
        name: row.projection_content_id
        if name == "projection_content_id"
        else _semantic_field_text(row, name)
        for name in OWNER_BACKFILL_PEAK_FACT_PROVENANCE_COLUMNS
    }


def _semantic_field_text(row: OwnerBackfillPeakFactProvenanceRow, name: str) -> str:
    if name == "segment_candidate_count":
        value: object = row.segment_candidate_count
    elif name == "admissible_segment_count":
        value = row.admissible_segment_count
    else:
        value = getattr(row, name)
    if value is None:
        return ""
    if isinstance(value, str):
        _safe_text(value, field=name)
        return value
    if type(value) is int:
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
        return _float_text(value)
    if isinstance(value, tuple):
        if row.row_kind != "physical_question":
            return ""
        _unique_string_tuple(value, field=name)
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    raise TypeError(f"{name} has no TSV scalar codec")


def _row_from_projection(raw: Mapping[str, str]) -> OwnerBackfillPeakFactProvenanceRow:
    kind = raw["row_kind"]
    if kind not in {"run_profile", "physical_question", "cell_projection"}:
        raise ValueError("unknown provenance row_kind")
    return OwnerBackfillPeakFactProvenanceRow(
        row_kind=cast(RowKind, kind),
        schema_version=_required_projection_text(raw, "schema_version"),
        work_unit_key=raw["work_unit_key"],
        group_hypothesis_id=raw["group_hypothesis_id"],
        public_family_id=raw["public_family_id"],
        sample_stem=raw["sample_stem"],
        candidate_index=_parse_optional_int(raw, "candidate_index"),
        candidate_phase=raw["candidate_phase"],
        request_identity_id=raw["request_identity_id"],
        acquisition_source_id=raw["acquisition_source_id"],
        trace_content_id=raw["trace_content_id"],
        trace_point_count=_parse_optional_int(raw, "trace_point_count"),
        trace_rt_start_min=_parse_optional_float(raw, "trace_rt_start_min"),
        trace_rt_end_min=_parse_optional_float(raw, "trace_rt_end_min"),
        profile_schema_version=raw["profile_schema_version"],
        profile_id=raw["profile_id"],
        profile_hash=raw["profile_hash"],
        signal_processing_method=raw["signal_processing_method"],
        signal_processing_window_points=_parse_optional_int(
            raw, "signal_processing_window_points"
        ),
        physical_signal_formula=raw["physical_signal_formula"],
        prepared_signal_id=raw["prepared_signal_id"],
        segment_universe_id=raw["segment_universe_id"],
        segment_candidate_ids=_parse_json_string_tuple(raw, "segment_candidate_ids"),
        physical_question_schema_version=raw["physical_question_schema_version"],
        physical_question_id=raw["physical_question_id"],
        selection_subject_id=raw["selection_subject_id"],
        query_rt_min=_parse_optional_float(raw, "query_rt_min"),
        context_rt_start_min=_parse_optional_float(raw, "context_rt_start_min"),
        context_rt_end_min=_parse_optional_float(raw, "context_rt_end_min"),
        admissible_segment_ids=_parse_json_string_tuple(raw, "admissible_segment_ids"),
        selection_policy_version=raw["selection_policy_version"],
        state=cast(PhysicalPeakState | Literal[""], raw["state"]),
        reason=raw["reason"],
        physical_answer_id=raw["physical_answer_id"],
        physical_segment_id=raw["physical_segment_id"],
        apex_rt_min=_parse_optional_float(raw, "apex_rt_min"),
        left_rt_min=_parse_optional_float(raw, "left_rt_min"),
        right_rt_min=_parse_optional_float(raw, "right_rt_min"),
        height=_parse_optional_float(raw, "height"),
        scan_support_count=_parse_optional_int(raw, "scan_support_count"),
        area_counts_seconds=_parse_optional_float(raw, "area_counts_seconds"),
        area_source=raw["area_source"],
        relative_shape_schema_version=raw["relative_shape_schema_version"],
        relative_shape_point_count=_parse_optional_int(
            raw, "relative_shape_point_count"
        ),
        relative_shape_content_hash=raw["relative_shape_content_hash"],
        fact_content_id=raw["fact_content_id"],
        selected_work_unit_key=raw["selected_work_unit_key"],
        selected_fact_content_id=raw["selected_fact_content_id"],
        owner_backfill_cell_content_id=raw["owner_backfill_cell_content_id"],
        production_cell_decision_id=raw["production_cell_decision_id"],
        cell_status=raw["cell_status"],
        consolidation_state=raw["consolidation_state"],
        consolidation_winner_group_hypothesis_id=(
            raw["consolidation_winner_group_hypothesis_id"]
        ),
        consolidation_source_group_hypothesis_id=(
            raw["consolidation_source_group_hypothesis_id"]
        ),
        matrix_value_counts_seconds=_parse_optional_float(
            raw, "matrix_value_counts_seconds"
        ),
        matrix_value_source=raw["matrix_value_source"],
        writer_schema_version=raw["writer_schema_version"],
        cell_projection_id=raw["cell_projection_id"],
    )


def _strict_projection(
    raw: Mapping[str | None, str | list[str] | None],
) -> dict[str, str]:
    if tuple(raw) != OWNER_BACKFILL_PEAK_FACT_PROVENANCE_COLUMNS:
        raise ValueError("row does not match the exact provenance schema")
    projection: dict[str, str] = {}
    for name in OWNER_BACKFILL_PEAK_FACT_PROVENANCE_COLUMNS:
        value = raw[name]
        if not isinstance(value, str):
            raise ValueError("row has missing or excess fields")
        _safe_text(value, field=name)
        projection[name] = value
    _require_typed_id(
        projection["projection_content_id"],
        prefix=OWNER_BACKFILL_PROVENANCE_ROW_ID_PREFIX,
        field="projection_content_id",
    )
    for count_name, list_name in (
        ("segment_candidate_count", "segment_candidate_ids"),
        ("admissible_segment_count", "admissible_segment_ids"),
    ):
        count_text = projection[count_name]
        list_values = _parse_json_string_tuple(projection, list_name)
        if count_text:
            if _parse_nonnegative_int_text(count_text, field=count_name) != len(
                list_values
            ):
                raise ValueError(f"{count_name} does not match {list_name}")
        elif list_values:
            raise ValueError(f"{list_name} requires {count_name}")
    return projection


def _validate_exact_header(path: Path, header: Sequence[str] | None) -> None:
    if len(tuple(header or ())) == 58:
        raise ValueError(
            f"{path}: v1 provenance is not readable; rerun alignment to emit v2"
        )
    if tuple(header or ()) != OWNER_BACKFILL_PEAK_FACT_PROVENANCE_COLUMNS:
        raise ValueError(f"{path}: expected exact provenance schema")


def _parse_json_string_tuple(row: Mapping[str, str], field: str) -> tuple[str, ...]:
    text = row[field]
    if not text:
        return ()
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field} must be compact JSON") from exc
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field} must be a JSON array of strings")
    result = tuple(value)
    _unique_string_tuple(result, field=field)
    if json.dumps(result, ensure_ascii=False, separators=(",", ":")) != text:
        raise ValueError(f"{field} must use the canonical compact JSON codec")
    return result


def _parse_optional_int(row: Mapping[str, str], field: str) -> int | None:
    text = row[field]
    return None if not text else _parse_nonnegative_int_text(text, field=field)


def _parse_nonnegative_int_text(text: str, *, field: str) -> int:
    if _NONNEGATIVE_INTEGER.fullmatch(text) is None:
        raise ValueError(f"{field} must be a canonical non-negative integer")
    return int(text)


def _parse_optional_float(row: Mapping[str, str], field: str) -> float | None:
    text = row[field]
    if not text:
        return None
    try:
        value = float.fromhex(text)
    except ValueError as exc:
        raise ValueError(
            f"{field} must be a canonical finite hexadecimal float"
        ) from exc
    if not math.isfinite(value) or _float_text(value) != text:
        raise ValueError(f"{field} must be a canonical finite hexadecimal float")
    return value


def _required_projection_text(row: Mapping[str, str], field: str) -> str:
    return _required_text(row[field], field=field)


def _populated_fields(row: OwnerBackfillPeakFactProvenanceRow) -> set[str]:
    projection = _row_projection(row)
    return {name for name, value in projection.items() if value != ""}


def _profile_binding(row: OwnerBackfillPeakFactProvenanceRow) -> tuple[object, ...]:
    return tuple(getattr(row, name) for name in _PROFILE_FIELDS)


_LOWER_SHA256 = re.compile(r"[0-9a-f]{64}")
_NONNEGATIVE_INTEGER = re.compile(r"0|[1-9][0-9]*")


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be non-empty text")
    _safe_text(value, field=field)
    return value


def _safe_text(value: str, *, field: str) -> None:
    if any(character in value for character in ("\t", "\r", "\n", "\x00")):
        raise ValueError(f"{field} contains a forbidden control character")


def _nonnegative_int(value: object, *, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _positive_int(value: object, *, field: str) -> int:
    result = _nonnegative_int(value, field=field)
    if result == 0:
        raise ValueError(f"{field} must be positive")
    return result


def _required_finite_float(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be finite numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite numeric")
    return 0.0 if result == 0.0 else result


def _validate_optional_range(start: object, end: object, *, label: str) -> None:
    if (start is None) != (end is None):
        raise ValueError(f"{label} range endpoints must be populated together")
    if start is None:
        return
    start_value = _required_finite_float(start, field=f"{label}_rt_start_min")
    end_value = _required_finite_float(end, field=f"{label}_rt_end_min")
    if start_value > end_value:
        raise ValueError(f"{label} RT range is reversed")


def _unique_string_tuple(value: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, tuple) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ValueError(f"{field} must be a tuple of non-empty strings")
    if len(value) != len(set(value)):
        raise ValueError(f"{field} must not contain duplicates")
    for item in value:
        _safe_text(item, field=field)
    return value


def _require_lower_sha256(value: str, *, field: str) -> None:
    if _LOWER_SHA256.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")


def _require_typed_id(value: str, *, prefix: str, field: str) -> None:
    if not isinstance(value, str) or not value.startswith(prefix):
        raise ValueError(f"{field} must use the {prefix!r} prefix")
    _require_lower_sha256(value[len(prefix) :], field=field)


def _float_text(value: float) -> str:
    return (0.0).hex() if value == 0.0 else value.hex()


def _compact_json_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")


def _authority_id(
    prefix: str,
    schema_version: str,
    field_values: tuple[tuple[str, object], ...],
) -> str:
    payload: dict[str, object] = {"schema_version": schema_version}
    for name, value in field_values:
        payload[name] = _authority_value(value, field=name)
    return f"{prefix}{hashlib.sha256(_compact_json_bytes(payload)).hexdigest()}"


def _authority_value(value: object, *, field: str) -> object:
    if value is None or isinstance(value, (str, bool)):
        if isinstance(value, str):
            _safe_text(value, field=field)
        return value
    if type(value) is int:
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field} must be finite")
        return _float_text(value)
    if isinstance(value, tuple):
        if any(type(item) not in {str, int} for item in value):
            raise TypeError(f"{field} tuple has no canonical authority codec")
        return list(value)
    if isinstance(value, Mapping):
        raise TypeError(f"{field} mappings require a frozen ordered schema")
    raise TypeError(f"{field} has no canonical authority codec")


def rehydrate_owner_backfill_peak_fact_provenance_row(
    row: OwnerBackfillPeakFactProvenanceRow,
    extraction: OwnerBackfillExtractionResult,
    *,
    stage: OwnerBackfillPeakFactStageProjection,
    profile: SignalProcessingProfile,
) -> PhysicalPeakEvaluation:
    """Replay one physical row from live Alignment and acquisition-owner inputs."""

    if row.row_kind != "physical_question":
        raise ValueError("only physical_question rows can be rehydrated")
    _compare_stage_to_row(stage, row)
    _validate_live_extraction(stage, extraction)
    trace = extraction.trace
    prepared = prepare_physical_signal(
        trace.rt,
        trace.intensity,
        trace_content_id=row.trace_content_id,
        profile=profile,
    )
    # The physical owner deliberately receives live stage/query and live request
    # bounds.  Persisted context/admissible IDs never become replay inputs.
    request = extraction.request_identity.request
    actual = evaluate_physical_peak(
        prepared,
        selection_subject_id=stage.selection_subject_id,
        query_rt_min=stage.query_rt_min,
        request_context_rt_start_min=request.rt_min,
        request_context_rt_end_min=request.rt_max,
    )
    actual = rehydrate_physical_peak_evaluation(
        trace.rt,
        trace.intensity,
        profile=profile,
        expected=actual,
        request_context_rt_start_min=request.rt_min,
        request_context_rt_end_min=request.rt_max,
    )
    live_row = row_from_terminal_observation(stage, actual, profile=profile)
    if live_row != row:
        raise ValueError("persisted physical row differs from live owner replay")
    return actual


def compose_owner_backfill_peak_fact_provenance(
    read_result: OwnerBackfillPeakFactProvenanceReadResult,
    *,
    live_observation_by_work: Mapping[str, LiveOwnerBackfillPeakFactObservation]
    | None = None,
    live_extraction_by_work: Mapping[str, OwnerBackfillExtractionResult] | None = None,
    profile: SignalProcessingProfile | None = None,
    live_matrix: AlignmentMatrix | None = None,
    alignment_config: AlignmentConfig | None = None,
    production_decisions: ProductionDecisionSet | None = None,
) -> OwnerBackfillPeakFactCompositionResult:
    if not isinstance(read_result, OwnerBackfillPeakFactProvenanceReadResult):
        raise TypeError("read_result must come from the strict provenance reader")
    observations = live_observation_by_work or {}
    extractions = live_extraction_by_work or {}
    run_profile = read_result.rows[0]
    if profile is not None:
        live_profile_row = _run_profile_row(profile)
        if live_profile_row != run_profile:
            raise ValueError("live profile crosses persisted run profile")
    physical_rows = tuple(
        row for row in read_result.rows if row.row_kind == "physical_question"
    )
    verified_physical: dict[str, OwnerBackfillPeakFactProvenanceRow] = {}
    verified_stages: dict[str, OwnerBackfillPeakFactStageProjection] = {}
    physical_claims: list[OwnerBackfillPhysicalClaim] = []
    for row in physical_rows:
        observation = observations.get(row.work_unit_key)
        stage = (
            owner_backfill_peak_fact_stage_projection_from_observation(observation)
            if observation is not None
            else None
        )
        stage_claim: ClaimState = "unavailable_without_live_owner"
        physical_claim: ClaimState = "unavailable_without_live_owner"
        if stage is not None:
            _compare_stage_to_row(stage, row)
            stage_claim = "verified"
            verified_stages[row.work_unit_key] = stage
        extraction = extractions.get(row.work_unit_key)
        if stage is not None and extraction is not None and profile is not None:
            rehydrate_owner_backfill_peak_fact_provenance_row(
                row,
                extraction,
                stage=stage,
                profile=profile,
            )
            physical_claim = "verified"
            verified_physical[row.work_unit_key] = row
        physical_claims.append(
            OwnerBackfillPhysicalClaim(
                work_unit_key=row.work_unit_key,
                stage_claim=stage_claim,
                physical_claim=physical_claim,
            )
        )

    final_inputs_available = all(
        value is not None
        for value in (profile, live_matrix, alignment_config, production_decisions)
    )
    recomputed_decisions: ProductionDecisionSet | None = None
    if final_inputs_available:
        assert live_matrix is not None
        assert alignment_config is not None
        assert production_decisions is not None
        recomputed_decisions = build_production_decisions(live_matrix, alignment_config)
        if recomputed_decisions != production_decisions:
            raise ValueError(
                "supplied production decisions differ from live owner replay"
            )

    cell_claims: list[OwnerBackfillCellClaim] = []
    for row in (
        item for item in read_result.rows if item.row_kind == "cell_projection"
    ):
        claim: ClaimState = "unavailable_without_live_owner"
        selected = verified_physical.get(row.selected_work_unit_key)
        stage = verified_stages.get(row.selected_work_unit_key)
        if (
            selected is not None
            and stage is not None
            and recomputed_decisions is not None
        ):
            assert profile is not None
            assert live_matrix is not None
            assert alignment_config is not None
            cell = _live_cell_for_projection(live_matrix, row)
            authority = owner_backfill_authority_cell_projection(
                stage=stage,
                cell=cell,
                profile_id=profile.profile_id,
                selected_fact_content_id=row.selected_fact_content_id,
                physical_answer_id=selected.physical_answer_id,
                physical_segment_id=selected.physical_segment_id,
            )
            decision = recomputed_decisions.cell(cell.cluster_id, cell.sample_stem)
            production_projection = production_cell_decision_projection(
                decision,
                cell=cell,
                config=alignment_config,
                owner_backfill_cell_content_id=authority.owner_backfill_cell_content_id,
            )
            expected = _cell_projection_row(
                owner_backfill_cell_projection(
                    authority_cell=authority,
                    production_decision=production_projection,
                ),
                profile,
            )
            if expected != row:
                raise ValueError(
                    "persisted cell projection differs from live owner replay"
                )
            claim = "verified"
        cell_claims.append(
            OwnerBackfillCellClaim(
                group_hypothesis_id=row.group_hypothesis_id,
                sample_stem=row.sample_stem,
                cell_projection_claim=claim,
            )
        )
    return OwnerBackfillPeakFactCompositionResult(
        physical_claims=tuple(physical_claims),
        cell_claims=tuple(cell_claims),
    )


def _compare_stage_to_row(
    stage: OwnerBackfillPeakFactStageProjection,
    row: OwnerBackfillPeakFactProvenanceRow,
) -> None:
    observed = (
        stage.work_unit_key,
        stage.group_hypothesis_id,
        stage.public_family_id,
        stage.sample_stem,
        stage.candidate_index,
        stage.candidate_phase,
        stage.request_identity_id,
        stage.acquisition_source_id,
        stage.selection_subject_id,
        stage.query_rt_min,
        stage.trace_rt_start_min,
        stage.trace_rt_end_min,
    )
    expected = (
        row.work_unit_key,
        row.group_hypothesis_id,
        row.public_family_id,
        row.sample_stem,
        row.candidate_index,
        row.candidate_phase,
        row.request_identity_id,
        row.acquisition_source_id,
        row.selection_subject_id,
        row.query_rt_min,
        row.trace_rt_start_min,
        row.trace_rt_end_min,
    )
    if observed != expected:
        raise ValueError("live Alignment stage projection crosses persisted work")


def _validate_live_extraction(
    stage: OwnerBackfillPeakFactStageProjection,
    extraction: OwnerBackfillExtractionResult,
) -> None:
    from xic_extractor.alignment.owner_backfill import OwnerBackfillExtractionResult

    if not isinstance(extraction, OwnerBackfillExtractionResult):
        raise TypeError("extraction must be owner-minted")
    identity = extraction.request_identity
    if (
        identity.request_identity_id,
        identity.acquisition_source.acquisition_source_id,
        identity.sample_stem,
    ) != (
        stage.request_identity_id,
        stage.acquisition_source_id,
        stage.sample_stem,
    ):
        raise ValueError("live extraction crosses Alignment stage identity")
    trace = extraction.trace
    if len(trace.rt) != len(trace.intensity):
        raise ValueError("live extraction trace cardinality mismatch")
    actual_range = (
        (float(trace.rt[0]), float(trace.rt[-1])) if len(trace.rt) else (None, None)
    )
    if actual_range != (stage.trace_rt_start_min, stage.trace_rt_end_min):
        raise ValueError("live extraction trace range crosses Alignment stage")


def _live_cell_for_projection(
    matrix: AlignmentMatrix,
    row: OwnerBackfillPeakFactProvenanceRow,
) -> AlignedCell:
    matches = tuple(
        cell
        for cell in matrix.cells
        if cell.sample_stem == row.sample_stem
        and cell.group_hypothesis_id == row.group_hypothesis_id
        and cell.public_family_id == row.public_family_id
    )
    if len(matches) != 1:
        raise ValueError("final cell replay requires one exact live Matrix cell")
    return matches[0]


EMPTY_OWNER_BACKFILL_PEAK_FACT_PROVENANCE_BATCH = OwnerBackfillPeakFactProvenanceBatch(
    (_run_profile_row(DEFAULT_SIGNAL_PROCESSING_PROFILE),)
)


__all__ = [
    "EMPTY_OWNER_BACKFILL_PEAK_FACT_PROVENANCE_BATCH",
    "OWNER_BACKFILL_AUTHORITY_CELL_FIELDS",
    "OWNER_BACKFILL_PEAK_FACT_PROVENANCE_COLUMNS",
    "OWNER_BACKFILL_PEAK_FACT_PROVENANCE_SCHEMA_VERSION",
    "OWNER_BACKFILL_PROVENANCE_WRITER_SCHEMA_VERSION",
    "SELECTED_INTEGRATION_AUTHORITY_FIELDS",
    "OwnerBackfillAuthorityCellProjectionV2",
    "OwnerBackfillCellProjectionV2",
    "OwnerBackfillPeakFactCompositionResult",
    "OwnerBackfillPeakFactObservation",
    "OwnerBackfillPeakFactProvenanceBatch",
    "OwnerBackfillPeakFactProvenanceReadResult",
    "OwnerBackfillPeakFactProvenanceRow",
    "OwnerBackfillPeakFactStageProjection",
    "PreparedOwnerBackfillPeakFact",
    "SelectedIntegrationAuthorityProjectionV1",
    "build_owner_backfill_peak_fact_provenance_batch",
    "compose_owner_backfill_peak_fact_provenance",
    "owner_backfill_authority_cell_projection",
    "owner_backfill_cell_lineage_matches",
    "owner_backfill_cell_projection",
    "prepare_owner_backfill_peak_fact",
    "build_owner_backfill_peak_fact_provenance_batch_from_rows",
    "read_owner_backfill_peak_fact_provenance_tsv",
    "render_owner_backfill_peak_fact_provenance_tsv",
    "rehydrate_owner_backfill_peak_fact_provenance_row",
    "row_from_terminal_observation",
    "selected_integration_authority_projection",
    "write_owner_backfill_peak_fact_provenance_tsv",
]
