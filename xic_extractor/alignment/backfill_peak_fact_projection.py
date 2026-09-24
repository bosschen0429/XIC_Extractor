"""Strict in-memory projections and authority bindings for Backfill facts.

This module houses stage, selected integration, authority cell, and cell
projections as pure in-memory domain models and deterministic identity bindings.
It deliberately contains no TSV, file I/O, or persistent row codecs.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import InitVar, dataclass
from typing import TYPE_CHECKING

from xic_extractor.alignment.matrix import AlignedCell
from xic_extractor.alignment.production_decisions import (
    BACKFILL_AUTHORITY_CELL_ID_PREFIX,
    ProductionCellDecisionProjection,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    validate_bounded_physical_peak_evaluation,
)
from xic_extractor.peak_detection.physical_peak_models import (
    PhysicalPeakEvaluation,
)

if TYPE_CHECKING:
    from xic_extractor.alignment.backfill_peak_fact import (
        BackfillPeakFactObservation as LiveBackfillPeakFactObservation,
    )
    from xic_extractor.peak_detection.integration_result import IntegrationResult


SELECTED_INTEGRATION_AUTHORITY_SCHEMA_VERSION = (
    "selected_integration_authority_projection"
)
BACKFILL_AUTHORITY_CELL_SCHEMA_VERSION = (
    "backfill_authority_cell_projection"
)
BACKFILL_CELL_PROJECTION_SCHEMA_VERSION = "owner_backfill_cell_projection"
BACKFILL_PROVENANCE_WRITER_SCHEMA_VERSION = (
    "alignment-owner-backfill-peak-fact-provenance-writer"
)

SELECTED_INTEGRATION_AUTHORITY_ID_PREFIX = "selected-integration-authority:"
BACKFILL_CELL_PROJECTION_ID_PREFIX = "owner-backfill-cell-projection:"

_STAGE_PROJECTION_MINT_TOKEN = object()

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

BACKFILL_AUTHORITY_CELL_FIELDS = (
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

_LOWER_SHA256 = re.compile(r"[0-9a-f]{64}")


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


@dataclass(frozen=True, slots=True)
class BackfillPeakFactStageProjection:
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


def backfill_peak_fact_stage_projection_from_observation(
    observation: LiveBackfillPeakFactObservation,
    *,
    feature_order_index: int = 0,
    sample_order_index: int = 0,
) -> BackfillPeakFactStageProjection:
    """Project a complete owner-minted Alignment observation for provenance."""

    from xic_extractor.alignment.backfill_peak_fact import (
        BackfillPeakFactObservation as LiveBackfillPeakFactObservation,
    )
    from xic_extractor.alignment.backfill_peak_fact import (
        validate_backfill_peak_fact_observation,
    )
    from xic_extractor.alignment.backfill_request_plan import (
        BackfillLogicalRequestMember,
        BackfillRequestIdentity,
    )

    if not isinstance(observation, LiveBackfillPeakFactObservation):
        raise TypeError("observation must be owner-minted by Alignment")
    validate_backfill_peak_fact_observation(observation)
    logical_request = observation.logical_request
    request_identity = observation.request_identity
    if not isinstance(logical_request, BackfillLogicalRequestMember):
        raise TypeError("observation logical_request must be owner-minted")
    if not isinstance(request_identity, BackfillRequestIdentity):
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
    return BackfillPeakFactStageProjection(
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
class BackfillPeakFactObservation:
    stage: BackfillPeakFactStageProjection
    physical_evaluation: PhysicalPeakEvaluation

    def __post_init__(self) -> None:
        if not isinstance(self.stage, BackfillPeakFactStageProjection):
            raise TypeError("stage must be owner-minted")
        validate_bounded_physical_peak_evaluation(self.physical_evaluation)


@dataclass(frozen=True, slots=True)
class SelectedIntegrationAuthorityProjection:
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
class BackfillAuthorityCellProjection:
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
            BACKFILL_AUTHORITY_CELL_ID_PREFIX,
            BACKFILL_AUTHORITY_CELL_SCHEMA_VERSION,
            tuple(
                (name, getattr(self, name))
                for name in BACKFILL_AUTHORITY_CELL_FIELDS
            ),
        )


@dataclass(frozen=True, slots=True)
class BackfillCellProjection:
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
    writer_schema_version: str = BACKFILL_PROVENANCE_WRITER_SCHEMA_VERSION

    @property
    def cell_projection_id(self) -> str:
        return _authority_id(
            BACKFILL_CELL_PROJECTION_ID_PREFIX,
            BACKFILL_CELL_PROJECTION_SCHEMA_VERSION,
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


def selected_integration_authority_projection(
    integration: IntegrationResult | None,
) -> SelectedIntegrationAuthorityProjection:
    if integration is None:
        raise ValueError("selected integration is required")
    raw_scan_indices = tuple(getattr(integration, "raw_scan_indices", ()))
    if raw_scan_indices:
        raise ValueError("bounded selected integration must omit raw scan indices")
    return SelectedIntegrationAuthorityProjection(
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


def backfill_cell_lineage_matches(
    stage: BackfillPeakFactStageProjection,
    cell: AlignedCell,
) -> bool:
    """Return whether a live final cell legally represents one owner stage.

    This is identity-only validation.  It deliberately never compares areas,
    RTs, or other selected values.  The source public-family label is an output
    alias and is therefore only required for an un-consolidated exact match.
    """

    if not isinstance(stage, BackfillPeakFactStageProjection):
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


def _require_backfill_cell_lineage(
    stage: BackfillPeakFactStageProjection,
    cell: AlignedCell,
) -> None:
    if not backfill_cell_lineage_matches(stage, cell):
        raise ValueError("selected stage crosses its live Alignment cell lineage")


def backfill_authority_cell_projection(
    *,
    stage: BackfillPeakFactStageProjection,
    cell: AlignedCell,
    profile_id: str,
    selected_fact_content_id: str,
    physical_answer_id: str,
    physical_segment_id: str,
) -> BackfillAuthorityCellProjection:
    if not isinstance(stage, BackfillPeakFactStageProjection):
        raise TypeError("stage must be owner-minted")
    if not isinstance(cell, AlignedCell):
        raise TypeError("cell must be an AlignedCell")
    _require_backfill_cell_lineage(stage, cell)
    integration = selected_integration_authority_projection(cell.selected_integration)
    missing = tuple(
        name
        for name in BACKFILL_AUTHORITY_CELL_FIELDS
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
    return BackfillAuthorityCellProjection(
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


def backfill_cell_projection(
    *,
    authority_cell: BackfillAuthorityCellProjection,
    production_decision: ProductionCellDecisionProjection,
) -> BackfillCellProjection:
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
    return BackfillCellProjection(
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


__all__ = [
    "BACKFILL_AUTHORITY_CELL_FIELDS",
    "BACKFILL_AUTHORITY_CELL_SCHEMA_VERSION",
    "BACKFILL_CELL_PROJECTION_ID_PREFIX",
    "BACKFILL_CELL_PROJECTION_SCHEMA_VERSION",
    "BACKFILL_PROVENANCE_WRITER_SCHEMA_VERSION",
    "SELECTED_INTEGRATION_AUTHORITY_FIELDS",
    "SELECTED_INTEGRATION_AUTHORITY_ID_PREFIX",
    "SELECTED_INTEGRATION_AUTHORITY_SCHEMA_VERSION",
    "BackfillAuthorityCellProjection",
    "BackfillCellProjection",
    "BackfillPeakFactObservation",
    "BackfillPeakFactStageProjection",
    "SelectedIntegrationAuthorityProjection",
    "_STAGE_PROJECTION_MINT_TOKEN",
    "_authority_id",
    "_authority_value",
    "_compact_json_bytes",
    "_float_text",
    "_nonnegative_int",
    "_positive_int",
    "_require_lower_sha256",
    "_require_backfill_cell_lineage",
    "_require_typed_id",
    "_required_finite_float",
    "_required_text",
    "_safe_text",
    "_unique_string_tuple",
    "_validate_optional_range",
    "backfill_authority_cell_projection",
    "backfill_cell_lineage_matches",
    "backfill_cell_projection",
    "backfill_peak_fact_stage_projection_from_observation",
    "selected_integration_authority_projection",
]
