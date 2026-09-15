from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal

import numpy as np

from xic_extractor.evidence_semantics import (
    EvidenceDecisionSemantics,
    EvidenceSignalSet,
    decision_semantics_from_signal_set,
)
from xic_extractor.peak_detection.baseline import (
    asls_baseline,
    bounded_trace_interval,
    integrate_with_baseline,
)
from xic_extractor.peak_detection.chrom_peak_segments import (
    ChromPeakSegmentPolicy,
    enumerate_chrom_peak_segments,
)
from xic_extractor.peak_detection.integration_result import IntegrationResult
from xic_extractor.peak_detection.ms1_morphology import (
    DEFAULT_GAUSSIAN15_WINDOW_POINTS,
    MS1_MORPHOLOGY_AREA_SOURCE,
    MS1_MORPHOLOGY_TRACE_METHOD,
    canonical_gaussian_signal_traces,
    gaussian15_positive_asls_residual_trace,
    morphology_trace_effective_points,
    positive_residual_area,
)
from xic_extractor.peak_detection.ms1_shape_identity import (
    DEFAULT_LOCAL_HALF_WINDOW_MIN,
    DEFAULT_OWN_MAX_SMOOTH_POINTS,
    OwnMaxTrace,
    own_max_normalized_trace,
)
from xic_extractor.peak_detection.ms1_trace_detection import (
    validate_ms1_trace_arrays,
)
from xic_extractor.peak_detection.scoring_metrics import (
    compute_local_sn_cache,
    noise_shape_severity,
    peak_width_severity,
    rt_centrality_severity,
    rt_prior_severity,
    symmetry_severity,
)
from xic_extractor.peak_detection.scoring_quality import (
    candidate_quality_penalty,
    candidate_selection_quality_penalty,
    hard_quality_flags,
)
from xic_extractor.peak_detection.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
    SignalProcessingProfile,
    canonical_signal_processing_profile_bytes,
    deserialize_signal_processing_profile,
)

EvidenceQuality = Literal["strong", "clean", "borderline", "poor", "unknown"]
RtEvidenceStatus = Literal["missing", "close", "borderline", "far"]
RtWindowStatus = Literal["inside", "borderline", "outside", "unknown"]
AcquisitionOpportunity = Literal["observed", "not_observed", "not_required"]

FACTS_VERSION = "candidate_evidence_facts_v1"
PAIRED_ISTD_MAX_DELTA_MIN = 1.0

PhysicalPeakState = Literal["available", "unavailable", "failed"]
LEGACY_PHYSICAL_PEAK_SELECTION_POLICY = "closed_segment_contains_query_v1"
LEGACY_PHYSICAL_PEAK_POINT_POLICY = "baseline_peak_contains_query_v2"
LEGACY_PHYSICAL_PEAK_WINDOW_POLICY = "unique_baseline_peak_in_context_v1"
LEGACY_ORIGINAL_SUPPORT_POINT_POLICY = "baseline_peak_contains_query_v3"
LEGACY_ORIGINAL_SUPPORT_WINDOW_POLICY = "unique_baseline_peak_in_context_v2"
LEGACY_ORIGINAL_CONTINUITY_POINT_POLICY = "baseline_peak_contains_query_v4"
LEGACY_ORIGINAL_CONTINUITY_WINDOW_POLICY = "unique_baseline_peak_in_context_v3"
LEGACY_ORIGINAL_EDGE_RETURN_POINT_POLICY = "baseline_peak_contains_query_v5"
LEGACY_ORIGINAL_EDGE_RETURN_WINDOW_POLICY = "unique_baseline_peak_in_context_v4"
LEGACY_ORIGINAL_FRAGMENTATION_POINT_POLICY = "baseline_peak_contains_query_v6"
LEGACY_ORIGINAL_FRAGMENTATION_WINDOW_POLICY = "unique_baseline_peak_in_context_v5"
PHYSICAL_PEAK_POINT_POLICY = "baseline_peak_contains_query_v7"
PHYSICAL_PEAK_WINDOW_POLICY = "unique_baseline_peak_in_context_v6"
_PHYSICAL_PEAK_SELECTION_POLICIES = frozenset(
    {
        LEGACY_PHYSICAL_PEAK_SELECTION_POLICY,
        LEGACY_PHYSICAL_PEAK_POINT_POLICY,
        LEGACY_PHYSICAL_PEAK_WINDOW_POLICY,
        LEGACY_ORIGINAL_SUPPORT_POINT_POLICY,
        LEGACY_ORIGINAL_SUPPORT_WINDOW_POLICY,
        LEGACY_ORIGINAL_CONTINUITY_POINT_POLICY,
        LEGACY_ORIGINAL_CONTINUITY_WINDOW_POLICY,
        LEGACY_ORIGINAL_EDGE_RETURN_POINT_POLICY,
        LEGACY_ORIGINAL_EDGE_RETURN_WINDOW_POLICY,
        LEGACY_ORIGINAL_FRAGMENTATION_POINT_POLICY,
        LEGACY_ORIGINAL_FRAGMENTATION_WINDOW_POLICY,
        PHYSICAL_PEAK_POINT_POLICY,
        PHYSICAL_PEAK_WINDOW_POLICY,
    }
)
_PHYSICAL_EDGE_RETURN_FRACTION = 0.001
_PHYSICAL_LOCAL_CONTRAST_MIN_MAD = 2.0
PhysicalPeakOperation = Literal[
    "trace_evaluation",
    "asls_baseline",
    "gaussian_smoothing",
    "segment_enumeration",
    "integration",
    "normalization",
    "raw_descriptor_projection",
    "region_audit_projection",
]
_PHYSICAL_PEAK_OPERATION_ORDER: tuple[PhysicalPeakOperation, ...] = (
    "trace_evaluation",
    "asls_baseline",
    "gaussian_smoothing",
    "segment_enumeration",
    "integration",
    "normalization",
    "raw_descriptor_projection",
    "region_audit_projection",
)

_PERSISTED_PHYSICAL_EVALUATION_SCHEMA = "physical_peak_evaluation_persistence_v1"
_PERSISTED_PHYSICAL_KEYS = (
    "schema_version",
    "profile",
    "trace",
    "evaluation",
)
_PERSISTED_PROFILE_KEYS = ("profile_hash", "profile_id", "content")
_PERSISTED_TRACE_KEYS = ("trace_content_id", "rt", "raw")
_PERSISTED_EVALUATION_KEYS = (
    "state",
    "reason",
    "prepared",
    "segment_universe",
    "question",
    "answer",
    "facts",
    "integration",
    "operations",
)
_PREPARED_PROJECTION_KEYS = (
    "schema_version",
    "trace_content_id",
    "profile_id",
    "prepared_signal_id",
    "trace_point_count",
    "state",
    "reason",
)
_SEGMENT_KEYS = (
    "physical_segment_id",
    "schema_version",
    "start_index",
    "end_index",
    "apex_index",
    "left_rt_min",
    "right_rt_min",
    "apex_rt_min",
    "height",
    "scan_support_count",
    "area_counts_seconds",
    "segment_class",
    "boundary_stop_reason",
    "evidence_sources",
)
_SEGMENT_UNIVERSE_KEYS = (
    "schema_version",
    "prepared_signal_id",
    "context_rt_start_min",
    "context_rt_end_min",
    "state",
    "reason",
    "segments",
    "segment_universe_id",
)
_QUESTION_KEYS = (
    "schema_version",
    "trace_content_id",
    "prepared_signal_id",
    "segment_universe_id",
    "segment_ids",
    "selection_subject_id",
    "query_rt_min",
    "context_rt_start_min",
    "context_rt_end_min",
    "admissible_segment_ids",
    "selection_policy_version",
    "physical_question_id",
)
_ANSWER_KEYS = (
    "schema_version",
    "state",
    "reason",
    "prepared_signal_id",
    "physical_question_id",
    "physical_segment_id",
    "physical_answer_id",
)
_FACT_KEYS = (
    "schema_version",
    "prepared_signal_id",
    "physical_question_id",
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
)
_INTEGRATION_KEYS = (
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
    "area_uncertainty",
    "area_uncertainty_formula_version",
    "baseline_residual_mad",
    "area_uncertainty_noise_source",
    "baseline_type",
    "baseline_score",
    "raw_scan_indices",
    "area_ms1_morphology",
    "ms1_morphology_area_source",
    "ms1_morphology_trace_method",
    "ms1_morphology_trace_window_points",
    "ms1_morphology_trace_effective_points",
)


@dataclass(frozen=True, slots=True)
class PreparedSignalProjection:
    schema_version: str
    trace_content_id: str
    profile_id: str
    prepared_signal_id: str
    trace_point_count: int
    state: PhysicalPeakState
    reason: str


@dataclass(frozen=True, slots=True)
class PreparedSignal:
    projection: PreparedSignalProjection
    profile: SignalProcessingProfile
    rt: tuple[float, ...]
    raw: tuple[float, ...]
    baseline: tuple[float, ...]
    smoothed: tuple[float, ...]
    positive_signal: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class PreparedSignalResult:
    projection: PreparedSignalProjection
    prepared: PreparedSignal | None
    operations: tuple[PhysicalPeakOperation, ...]
    trace_context_rt_start_min: float | None = None
    trace_context_rt_end_min: float | None = None


@dataclass(frozen=True, slots=True)
class PhysicalSegment:
    physical_segment_id: str
    start_index: int
    end_index: int
    apex_index: int
    left_rt_min: float
    right_rt_min: float
    apex_rt_min: float
    height: float
    scan_support_count: int
    area_counts_seconds: float
    segment_class: str
    boundary_stop_reason: str
    evidence_sources: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PhysicalSegmentUniverse:
    schema_version: str
    prepared_signal_id: str
    context_rt_start_min: float
    context_rt_end_min: float
    state: PhysicalPeakState
    reason: str
    segments: tuple[PhysicalSegment, ...]
    segment_universe_id: str

    @property
    def segment_ids(self) -> tuple[str, ...]:
        return tuple(segment.physical_segment_id for segment in self.segments)


@dataclass(frozen=True, slots=True)
class PhysicalPeakQuestion:
    schema_version: str
    trace_content_id: str
    prepared_signal_id: str
    segment_universe_id: str
    segment_ids: tuple[str, ...]
    selection_subject_id: str
    query_rt_min: float
    context_rt_start_min: float
    context_rt_end_min: float
    admissible_segment_ids: tuple[str, ...]
    selection_policy_version: str
    physical_question_id: str


@dataclass(frozen=True, slots=True)
class PhysicalPeakAnswer:
    schema_version: str
    state: PhysicalPeakState
    reason: str
    prepared_signal_id: str
    physical_question_id: str
    physical_segment_id: str | None
    physical_answer_id: str


@dataclass(frozen=True, slots=True)
class PhysicalPeakFacts:
    schema_version: str
    prepared_signal_id: str
    physical_question_id: str
    physical_answer_id: str
    physical_segment_id: str
    apex_rt_min: float
    left_rt_min: float
    right_rt_min: float
    height: float
    scan_support_count: int
    area_counts_seconds: float
    area_source: str
    relative_shape_schema_version: str
    relative_shape_point_count: int
    relative_shape_content_hash: str
    fact_content_id: str


@dataclass(frozen=True, slots=True)
class PhysicalPeakEvaluation:
    state: PhysicalPeakState
    reason: str
    prepared: PreparedSignalProjection
    segment_universe: PhysicalSegmentUniverse
    question: PhysicalPeakQuestion
    answer: PhysicalPeakAnswer
    facts: PhysicalPeakFacts | None
    integration: IntegrationResult | None
    operations: tuple[PhysicalPeakOperation, ...]
    # Diagnostic evidence only: never part of scientific equality or identity.
    retained_prepared_signal: PreparedSignal | None = field(default=None, compare=False)


def canonical_physical_trace_content_bytes(
    rt_values: object,
    intensity_values: object,
) -> bytes:
    rt = _physical_vector(rt_values, field="rt_values")
    raw = _physical_vector(intensity_values, field="intensity_values")
    return _canonical_physical_json_bytes(
        {
            "schema_version": "physical_trace_content_v1",
            "rt": [_physical_float_text(value) for value in rt],
            "raw": [_physical_float_text(value) for value in raw],
        }
    )


def mint_physical_trace_content_id(
    rt_values: object,
    intensity_values: object,
) -> str:
    return _typed_physical_id(
        "trace-content.v1:",
        canonical_physical_trace_content_bytes(rt_values, intensity_values),
    )


def validate_physical_trace_content_id(
    expected_trace_content_id: str,
    rt_values: object,
    intensity_values: object,
) -> None:
    if expected_trace_content_id != mint_physical_trace_content_id(
        rt_values, intensity_values
    ):
        raise ValueError("trace_content_id does not match exact RT/raw content")


def prepare_physical_signal(
    rt_values: object,
    intensity_values: object,
    *,
    trace_content_id: str | None = None,
    profile: SignalProcessingProfile = DEFAULT_SIGNAL_PROCESSING_PROFILE,
) -> PreparedSignalResult:
    if not isinstance(profile, SignalProcessingProfile):
        raise TypeError("profile must be a SignalProcessingProfile")
    rt = _physical_vector(rt_values, field="rt_values")
    raw = _physical_vector(intensity_values, field="intensity_values")
    actual_trace_id = mint_physical_trace_content_id(rt, raw)
    if trace_content_id is not None:
        if not isinstance(trace_content_id, str) or not trace_content_id:
            raise TypeError("trace_content_id must be non-empty text")
        if trace_content_id != actual_trace_id:
            raise ValueError("trace_content_id does not match exact RT/raw content")
    prepared_id = _typed_physical_id(
        "prepared-signal.v1:",
        canonical_prepared_signal_identity_bytes(actual_trace_id, profile.profile_id),
    )
    projection_profile_id = profile.profile_id
    projection_trace_point_count = len(rt)

    def projection_for(
        *, state: PhysicalPeakState, reason: str
    ) -> PreparedSignalProjection:
        return PreparedSignalProjection(
            schema_version="prepared_signal_projection_v1",
            trace_content_id=actual_trace_id,
            profile_id=projection_profile_id,
            prepared_signal_id=prepared_id,
            trace_point_count=projection_trace_point_count,
            state=state,
            reason=reason,
        )

    trace_context = _derivable_physical_trace_context(rt)
    trace_operation: tuple[PhysicalPeakOperation, ...] = ("trace_evaluation",)
    malformed_reason = _physical_trace_failure_reason(rt, raw)
    if malformed_reason:
        projection = projection_for(
            state="failed",
            reason=malformed_reason,
        )
        return PreparedSignalResult(
            projection,
            None,
            trace_operation,
            *(trace_context or (None, None)),
        )
    if not rt:
        projection = projection_for(
            state="unavailable",
            reason="no_signal_above_baseline",
        )
        return PreparedSignalResult(projection, None, trace_operation)
    if len(rt) < profile.window_points:
        projection = projection_for(
            state="unavailable",
            reason="insufficient_points_for_profile",
        )
        return PreparedSignalResult(
            projection,
            None,
            trace_operation,
            *(trace_context or (None, None)),
        )
    if max(raw, default=0.0) <= 0.0:
        projection = projection_for(
            state="unavailable",
            reason="no_signal_above_baseline",
        )
        return PreparedSignalResult(
            projection,
            None,
            trace_operation,
            *(trace_context or (None, None)),
        )
    try:
        baseline = asls_baseline(
            np.asarray(raw, dtype=float),
            lam=profile.baseline_lam,
            p=profile.baseline_p,
            n_iter=profile.baseline_n_iter,
        )
        baseline = np.maximum(baseline, 0.0)
    except (ValueError, FloatingPointError, ArithmeticError) as exc:
        projection = projection_for(
            state="failed",
            reason=f"numerical_owner_failure:{type(exc).__name__}",
        )
        return PreparedSignalResult(
            projection,
            None,
            (*trace_operation, "asls_baseline"),
            *(trace_context or (None, None)),
        )
    try:
        smoothed, positive = canonical_gaussian_signal_traces(
            np.asarray(raw, dtype=float),
            baseline,
            profile=profile,
        )
    except (ValueError, FloatingPointError, ArithmeticError) as exc:
        projection = projection_for(
            state="failed",
            reason=f"numerical_owner_failure:{type(exc).__name__}",
        )
        return PreparedSignalResult(
            projection,
            None,
            (*trace_operation, "asls_baseline", "gaussian_smoothing"),
            *(trace_context or (None, None)),
        )
    operations: tuple[PhysicalPeakOperation, ...] = (
        "trace_evaluation",
        "asls_baseline",
        "gaussian_smoothing",
    )
    state: PhysicalPeakState = (
        "available" if float(np.max(positive)) > 0.0 else "unavailable"
    )
    reason = "" if state == "available" else "no_signal_above_baseline"
    projection = projection_for(state=state, reason=reason)
    prepared = PreparedSignal(
        projection=projection,
        profile=profile,
        rt=rt,
        raw=raw,
        baseline=tuple(float(value) for value in baseline),
        smoothed=tuple(float(value) for value in smoothed),
        positive_signal=tuple(float(value) for value in positive),
    )
    return PreparedSignalResult(
        projection,
        prepared,
        operations,
        *(trace_context or (None, None)),
    )


def canonical_prepared_signal_identity_bytes(
    trace_content_id: str,
    profile_id: str,
) -> bytes:
    return _canonical_physical_json_bytes(
        {
            "schema_version": "prepared_signal_identity_v1",
            "trace_content_id": _required_physical_text(
                trace_content_id, field="trace_content_id"
            ),
            "profile_id": _required_physical_text(profile_id, field="profile_id"),
        }
    )


def validate_prepared_signal_identity(projection: PreparedSignalProjection) -> None:
    if projection.schema_version != "prepared_signal_projection_v1":
        raise ValueError("unsupported prepared signal projection schema")
    if projection.state not in {"available", "unavailable", "failed"}:
        raise ValueError("unsupported prepared signal state")
    if (projection.state == "available") != (projection.reason == ""):
        raise ValueError("prepared signal state/reason is not canonical")
    if (
        isinstance(projection.trace_point_count, bool)
        or not isinstance(projection.trace_point_count, int)
        or projection.trace_point_count < 0
    ):
        raise ValueError("trace_point_count must be a nonnegative integer")
    expected = _typed_physical_id(
        "prepared-signal.v1:",
        canonical_prepared_signal_identity_bytes(
            projection.trace_content_id, projection.profile_id
        ),
    )
    if projection.prepared_signal_id != expected:
        raise ValueError("prepared_signal_id does not match owner content")


def _physical_boundary_universe_schema(selection_policy_version: str) -> str:
    if selection_policy_version == LEGACY_PHYSICAL_PEAK_SELECTION_POLICY:
        return "physical_segment_universe_v1"
    if selection_policy_version in {
        LEGACY_ORIGINAL_EDGE_RETURN_POINT_POLICY,
        LEGACY_ORIGINAL_EDGE_RETURN_WINDOW_POLICY,
        LEGACY_ORIGINAL_FRAGMENTATION_POINT_POLICY,
        LEGACY_ORIGINAL_FRAGMENTATION_WINDOW_POLICY,
        PHYSICAL_PEAK_POINT_POLICY,
        PHYSICAL_PEAK_WINDOW_POLICY,
    }:
        return "physical_segment_universe_v3"
    return "physical_segment_universe_v2"


def enumerate_physical_segment_universe(
    prepared_result: PreparedSignalResult,
    *,
    request_context_rt_start_min: float | None = None,
    request_context_rt_end_min: float | None = None,
    selection_policy_version: str = PHYSICAL_PEAK_POINT_POLICY,
) -> PhysicalSegmentUniverse:
    if not isinstance(prepared_result, PreparedSignalResult):
        raise TypeError("prepared_result must be a PreparedSignalResult")
    validate_prepared_signal_identity(prepared_result.projection)
    if selection_policy_version not in _PHYSICAL_PEAK_SELECTION_POLICIES:
        raise ValueError("unsupported physical peak selection policy")
    legacy = selection_policy_version == LEGACY_PHYSICAL_PEAK_SELECTION_POLICY
    universe_schema = _physical_boundary_universe_schema(selection_policy_version)
    context_start, context_end = _physical_owner_context(
        prepared_result,
        request_context_rt_start_min=request_context_rt_start_min,
        request_context_rt_end_min=request_context_rt_end_min,
    )
    state = prepared_result.projection.state
    reason = prepared_result.projection.reason
    physical_segments: tuple[PhysicalSegment, ...] = ()
    prepared = prepared_result.prepared
    if state == "available" and prepared is not None:
        rt = np.asarray(prepared.rt, dtype=float)
        signal = np.asarray(prepared.positive_signal, dtype=float)
        zero_baseline = np.zeros_like(signal)
        segment_policy = ChromPeakSegmentPolicy(
            morphology_trace_method="canonical_signal",
            morphology_trace_window_points=prepared.profile.window_points,
            baseline_return_fraction=0.02 if legacy else 0.0,
            baseline_return_min_residual=1.0 if legacy else 0.0,
            include_baseline_return_samples=not legacy,
        )
        enumeration = enumerate_chrom_peak_segments(
            rt,
            signal,
            zero_baseline,
            quantitation_context_rt_start=context_start,
            quantitation_context_rt_end=context_end,
            policy=segment_policy,
        )
        if enumeration.status == "OK":
            segments = enumeration.segments
            if universe_schema == "physical_segment_universe_v3" and any(
                s.interval.start_index == 0 or s.interval.end_index == len(rt)
                for s in segments
            ):
                # Context-dependent baseline estimates can bridge a nearly
                # returned peak to an acquired edge. Only recover a fully
                # observed return; complete segments keep their exact values.
                near_baseline = enumerate_chrom_peak_segments(
                    rt,
                    signal,
                    zero_baseline,
                    quantitation_context_rt_start=context_start,
                    quantitation_context_rt_end=context_end,
                    policy=replace(
                        segment_policy,
                        baseline_return_fraction=_PHYSICAL_EDGE_RETURN_FRACTION,
                    ),
                )
                alternatives = {s.apex_index: s for s in near_baseline.segments}
                recovered = []
                for segment in segments:
                    alternative = alternatives.get(segment.apex_index)
                    interval = segment.interval
                    if (
                        (interval.start_index == 0 or interval.end_index == len(rt))
                        and alternative is not None
                        and 0 < alternative.interval.start_index
                        and alternative.interval.end_index < len(rt)
                        and interval.start_index <= alternative.interval.start_index
                        and alternative.interval.end_index <= interval.end_index
                    ):
                        segment = alternative
                    recovered.append(segment)
                segments = tuple(recovered)
            physical_segments = tuple(
                _physical_segment_from_chrom(segment)
                for segment in segments
            )
            if len(physical_segments) > prepared.profile.segment_universe_limit:
                state = "unavailable"
                reason = "segment_universe_too_large"
                physical_segments = ()
        elif enumeration.status == "NO_SIGNAL":
            state, reason = "unavailable", "no_signal_above_baseline"
        elif enumeration.status == "PEAK_NOT_FOUND":
            state, reason = "unavailable", "peak_not_found"
        elif enumeration.status == "WINDOW_TOO_SHORT":
            state, reason = "unavailable", "peak_not_found"
        else:
            state, reason = "failed", "malformed_trace"
    basis = _physical_segment_universe_basis(
        prepared_result.projection.prepared_signal_id,
        context_start,
        context_end,
        state,
        reason,
        physical_segments,
        schema_version=universe_schema,
    )
    return PhysicalSegmentUniverse(
        schema_version=universe_schema,
        prepared_signal_id=prepared_result.projection.prepared_signal_id,
        context_rt_start_min=context_start,
        context_rt_end_min=context_end,
        state=state,
        reason=reason,
        segments=physical_segments,
        segment_universe_id=_typed_physical_id(
            "segment-universe.v1:", _canonical_physical_json_bytes(basis)
        ),
    )


def canonical_physical_segment_bytes(segment: PhysicalSegment) -> bytes:
    if not isinstance(segment, PhysicalSegment):
        raise TypeError("segment must be a PhysicalSegment")
    return _canonical_physical_json_bytes(_physical_segment_basis(segment))


def canonical_physical_segment_universe_bytes(
    universe: PhysicalSegmentUniverse,
) -> bytes:
    if not isinstance(universe, PhysicalSegmentUniverse):
        raise TypeError("universe must be a PhysicalSegmentUniverse")
    return _canonical_physical_json_bytes(
        _physical_segment_universe_basis(
            universe.prepared_signal_id,
            universe.context_rt_start_min,
            universe.context_rt_end_min,
            universe.state,
            universe.reason,
            universe.segments,
            schema_version=universe.schema_version,
        )
    )


def validate_physical_segment_universe_identity(
    universe: PhysicalSegmentUniverse,
) -> None:
    if universe.schema_version not in {
        "physical_segment_universe_v1",
        "physical_segment_universe_v2",
        "physical_segment_universe_v3",
    }:
        raise ValueError("unsupported physical segment universe schema")
    if universe.state not in {"available", "unavailable", "failed"}:
        raise ValueError("unsupported physical segment universe state")
    if (universe.state == "available") != (universe.reason == ""):
        raise ValueError("physical segment universe state/reason is not canonical")
    if universe.state == "available" and not universe.segments:
        raise ValueError("available physical segment universe must not be empty")
    context_start = _finite_physical_float(
        universe.context_rt_start_min,
        field="context_rt_start_min",
    )
    context_end = _finite_physical_float(
        universe.context_rt_end_min,
        field="context_rt_end_min",
    )
    if context_start > context_end:
        raise ValueError("physical segment universe context is reversed")
    if len(universe.segment_ids) != len(set(universe.segment_ids)):
        raise ValueError("physical segment IDs must be unique")
    for segment in universe.segments:
        expected_segment_id = _typed_physical_id(
            "physical-segment.v1:", canonical_physical_segment_bytes(segment)
        )
        if segment.physical_segment_id != expected_segment_id:
            raise ValueError("physical_segment_id does not match owner content")
    expected_universe_id = _typed_physical_id(
        "segment-universe.v1:",
        canonical_physical_segment_universe_bytes(universe),
    )
    if universe.segment_universe_id != expected_universe_id:
        raise ValueError("segment_universe_id does not match owner content")


def mint_physical_peak_question(
    prepared: PreparedSignalProjection,
    universe: PhysicalSegmentUniverse,
    *,
    selection_subject_id: str,
    query_rt_min: float,
    selection_policy_version: str = PHYSICAL_PEAK_POINT_POLICY,
) -> PhysicalPeakQuestion:
    if not isinstance(prepared, PreparedSignalProjection):
        raise TypeError("prepared must be a PreparedSignalProjection")
    if not isinstance(universe, PhysicalSegmentUniverse):
        raise TypeError("universe must be a PhysicalSegmentUniverse")
    validate_prepared_signal_identity(prepared)
    validate_physical_segment_universe_identity(universe)
    if universe.prepared_signal_id != prepared.prepared_signal_id:
        raise ValueError("segment universe crosses prepared signal identity")
    subject_id = _required_physical_text(
        selection_subject_id, field="selection_subject_id"
    )
    query_rt = _finite_physical_float(query_rt_min, field="query_rt_min")
    context_start = universe.context_rt_start_min
    context_end = universe.context_rt_end_min
    admissible = _admissible_physical_segments(
        universe, query_rt, selection_policy_version
    )
    values = {
        "schema_version": "physical_peak_question_v1",
        "trace_content_id": prepared.trace_content_id,
        "prepared_signal_id": prepared.prepared_signal_id,
        "segment_universe_id": universe.segment_universe_id,
        "segment_ids": list(universe.segment_ids),
        "selection_subject_id": subject_id,
        "query_rt_min": _physical_float_text(query_rt),
        "context_rt_start_min": _physical_float_text(context_start),
        "context_rt_end_min": _physical_float_text(context_end),
        "admissible_segment_ids": list(admissible),
        "selection_policy_version": selection_policy_version,
    }
    question_id = _typed_physical_id(
        "physical-question.v1:", _canonical_physical_json_bytes(values)
    )
    return PhysicalPeakQuestion(
        schema_version="physical_peak_question_v1",
        trace_content_id=prepared.trace_content_id,
        prepared_signal_id=prepared.prepared_signal_id,
        segment_universe_id=universe.segment_universe_id,
        segment_ids=universe.segment_ids,
        selection_subject_id=subject_id,
        query_rt_min=query_rt,
        context_rt_start_min=context_start,
        context_rt_end_min=context_end,
        admissible_segment_ids=admissible,
        selection_policy_version=selection_policy_version,
        physical_question_id=question_id,
    )


def _admissible_physical_segments(
    universe: PhysicalSegmentUniverse,
    query_rt: float,
    selection_policy_version: str,
) -> tuple[str, ...]:
    if selection_policy_version not in _PHYSICAL_PEAK_SELECTION_POLICIES:
        raise ValueError("unsupported physical peak selection policy")
    if universe.schema_version != _physical_boundary_universe_schema(
        selection_policy_version
    ):
        raise ValueError("physical question crosses boundary policy")
    if selection_policy_version in {
        LEGACY_PHYSICAL_PEAK_WINDOW_POLICY,
        LEGACY_ORIGINAL_SUPPORT_WINDOW_POLICY,
        LEGACY_ORIGINAL_CONTINUITY_WINDOW_POLICY,
        LEGACY_ORIGINAL_EDGE_RETURN_WINDOW_POLICY,
        LEGACY_ORIGINAL_FRAGMENTATION_WINDOW_POLICY,
        PHYSICAL_PEAK_WINDOW_POLICY,
    }:
        if not universe.context_rt_start_min <= query_rt <= universe.context_rt_end_min:
            return ()
        # A group center is a prior for this already acquired bounded context.
        # Every local candidate participates; ambiguity never selects a winner.
        return universe.segment_ids
    return tuple(
        segment.physical_segment_id
        for segment in universe.segments
        if segment.left_rt_min <= query_rt <= segment.right_rt_min
    )


def canonical_physical_peak_question_bytes(question: PhysicalPeakQuestion) -> bytes:
    if not isinstance(question, PhysicalPeakQuestion):
        raise TypeError("question must be a PhysicalPeakQuestion")
    return _canonical_physical_json_bytes(_physical_question_basis(question))


def validate_physical_peak_question_identity(
    question: PhysicalPeakQuestion,
    *,
    universe: PhysicalSegmentUniverse | None = None,
) -> None:
    if question.schema_version != "physical_peak_question_v1":
        raise ValueError("unsupported physical peak question schema")
    if question.selection_policy_version not in _PHYSICAL_PEAK_SELECTION_POLICIES:
        raise ValueError("unsupported physical peak selection policy")
    _finite_physical_float(question.query_rt_min, field="query_rt_min")
    _finite_physical_float(question.context_rt_start_min, field="context_rt_start_min")
    _finite_physical_float(question.context_rt_end_min, field="context_rt_end_min")
    if len(question.segment_ids) != len(set(question.segment_ids)):
        raise ValueError("physical question segment IDs must be unique")
    if len(question.admissible_segment_ids) != len(
        set(question.admissible_segment_ids)
    ):
        raise ValueError("physical question admissible IDs must be unique")
    if any(
        segment_id not in set(question.segment_ids)
        for segment_id in question.admissible_segment_ids
    ):
        raise ValueError("physical question admissible ID is outside its universe")
    expected = _typed_physical_id(
        "physical-question.v1:", canonical_physical_peak_question_bytes(question)
    )
    if question.physical_question_id != expected:
        raise ValueError("physical_question_id does not match owner content")
    if universe is not None:
        validate_physical_segment_universe_identity(universe)
        if (
            question.segment_universe_id != universe.segment_universe_id
            or question.segment_ids != universe.segment_ids
        ):
            raise ValueError("physical question crosses segment universe")
        expected_admissible = _admissible_physical_segments(
            universe, question.query_rt_min, question.selection_policy_version
        )
        if question.admissible_segment_ids != expected_admissible:
            raise ValueError("physical question omits owner-admissible segments")
        if (
            question.context_rt_start_min != universe.context_rt_start_min
            or question.context_rt_end_min != universe.context_rt_end_min
        ):
            raise ValueError("physical question crosses universe context")


def answer_physical_peak_question(
    prepared_result: PreparedSignalResult,
    universe: PhysicalSegmentUniverse,
    question: PhysicalPeakQuestion,
) -> PhysicalPeakEvaluation:
    if not isinstance(prepared_result, PreparedSignalResult):
        raise TypeError("prepared_result must be a PreparedSignalResult")
    validate_prepared_signal_identity(prepared_result.projection)
    validate_physical_peak_question_identity(question, universe=universe)
    if question.prepared_signal_id != prepared_result.projection.prepared_signal_id:
        raise ValueError("physical question crosses prepared signal")
    state = universe.state
    reason = universe.reason
    selected: PhysicalSegment | None = None
    if state == "available":
        if not question.admissible_segment_ids:
            state, reason = "unavailable", "no_selection"
        elif len(question.admissible_segment_ids) > 1:
            state, reason = "unavailable", "ambiguous_segment_candidates"
        else:
            selected_id = question.admissible_segment_ids[0]
            selected = next(
                segment
                for segment in universe.segments
                if segment.physical_segment_id == selected_id
            )
            prepared = prepared_result.prepared
            if prepared is None:
                raise ValueError("available physical answer lacks worker-local signal")
            if selected.start_index == 0 or selected.end_index == len(prepared.rt):
                state, reason = "unavailable", "incomplete_peak_context"
                selected = None
            elif question.selection_policy_version in {
                LEGACY_ORIGINAL_SUPPORT_POINT_POLICY,
                LEGACY_ORIGINAL_SUPPORT_WINDOW_POLICY,
                LEGACY_ORIGINAL_CONTINUITY_POINT_POLICY,
                LEGACY_ORIGINAL_CONTINUITY_WINDOW_POLICY,
                LEGACY_ORIGINAL_EDGE_RETURN_POINT_POLICY,
                LEGACY_ORIGINAL_EDGE_RETURN_WINDOW_POLICY,
                LEGACY_ORIGINAL_FRAGMENTATION_POINT_POLICY,
                LEGACY_ORIGINAL_FRAGMENTATION_WINDOW_POLICY,
                PHYSICAL_PEAK_POINT_POLICY,
                PHYSICAL_PEAK_WINDOW_POLICY,
            }:
                # Smoothing may spread one acquired signal point into a peak.
                # Support belongs to the selected interval's original XIC;
                # saved policies retain their original answer semantics.
                acquired = prepared.raw[selected.start_index : selected.end_index]
                original = acquired
                if question.selection_policy_version in {
                    PHYSICAL_PEAK_POINT_POLICY,
                    PHYSICAL_PEAK_WINDOW_POLICY,
                }:
                    # Acquired background is not peak support. Reuse the owner
                    # baseline so adding an offset cannot rescue an impulse.
                    original = np.maximum(
                        np.asarray(original, dtype=float)
                        - np.asarray(
                            prepared.baseline[
                                selected.start_index : selected.end_index
                            ],
                            dtype=float,
                        ),
                        0.0,
                    )
                if sum(value > 0.0 for value in original) < 2:
                    state, reason = "unavailable", "insufficient_original_xic_support"
                    selected = None
                elif question.selection_policy_version in {
                    LEGACY_ORIGINAL_CONTINUITY_POINT_POLICY,
                    LEGACY_ORIGINAL_CONTINUITY_WINDOW_POLICY,
                    LEGACY_ORIGINAL_EDGE_RETURN_POINT_POLICY,
                    LEGACY_ORIGINAL_EDGE_RETURN_WINDOW_POLICY,
                    LEGACY_ORIGINAL_FRAGMENTATION_POINT_POLICY,
                    LEGACY_ORIGINAL_FRAGMENTATION_WINDOW_POLICY,
                    PHYSICAL_PEAK_POINT_POLICY,
                    PHYSICAL_PEAK_WINDOW_POLICY,
                } and not any(
                    left > 0.0 and right > 0.0
                    for left, right in zip(original, original[1:])
                ):
                    # Gaussian can bridge separate acquired impulses into one
                    # segment. A fresh answer needs an original adjacent pair.
                    state, reason = (
                        "unavailable",
                        "insufficient_original_xic_continuity",
                    )
                    selected = None
                elif question.selection_policy_version in {
                    LEGACY_ORIGINAL_FRAGMENTATION_POINT_POLICY,
                    LEGACY_ORIGINAL_FRAGMENTATION_WINDOW_POLICY,
                    PHYSICAL_PEAK_POINT_POLICY,
                    PHYSICAL_PEAK_WINDOW_POLICY,
                }:
                    original_runs = sum(
                        value > 0.0 and (index == 0 or acquired[index - 1] <= 0.0)
                        for index, value in enumerate(acquired)
                    )
                    # Repeated raw interruptions plus severe jaggedness cannot
                    # acquire peak support from a smooth envelope. Keep this
                    # conjunction in the versioned physical policy: neither
                    # isolated gaps nor jaggedness alone rejects a peak.
                    # Baseline crossings are not missing acquired samples.
                    if original_runs >= 3 and noise_shape_severity(
                        np.asarray(acquired, dtype=float)
                    )[0] == 2:
                        state, reason = (
                            "unavailable",
                            "fragmented_original_xic_support",
                        )
                        selected = None
                if selected is not None and question.selection_policy_version in {
                    PHYSICAL_PEAK_POINT_POLICY,
                    PHYSICAL_PEAK_WINDOW_POLICY,
                }:
                    # A shallow local maximum on a background plateau must not
                    # borrow its entire residual height as peak evidence. Use
                    # relief above both local valleys and short-scale original
                    # fluctuations; smoothing bias is not measurement noise.
                    left, right, apex = (
                        selected.start_index, selected.end_index, selected.apex_index
                    )
                    signal = np.asarray(prepared.positive_signal, dtype=float)
                    prominence = float(signal[apex]) - max(
                        float(np.min(signal[left : apex + 1])),
                        float(np.min(signal[apex : right])),
                    )
                    raw = np.asarray(prepared.raw[left:right], dtype=float)
                    rt = np.asarray(prepared.rt[left:right], dtype=float)
                    if len(raw) >= 3:
                        # Deviation from the line through each point's two
                        # neighbors removes constant/linear background, also
                        # on uneven RT grids. Normalize the coefficient norm
                        # (sqrt(6) for a uniform-grid second difference).
                        weight = (rt[2:] - rt[1:-1]) / (rt[2:] - rt[:-2])
                        residual = (
                            weight * raw[:-2] + (1.0 - weight) * raw[2:] - raw[1:-1]
                        ) / np.sqrt(1.0 + weight**2 + (1.0 - weight)**2)
                        noise_mad = float(
                            np.median(np.abs(residual - np.median(residual)))
                        )
                    else:
                        noise_mad = 0.0
                    if prominence < _PHYSICAL_LOCAL_CONTRAST_MIN_MAD * noise_mad:
                        state, reason = (
                            "unavailable", "insufficient_local_peak_contrast"
                        )
                        selected = None
    answer_basis = {
        "schema_version": "physical_peak_answer_v1",
        "state": state,
        "reason": reason,
        "prepared_signal_id": prepared_result.projection.prepared_signal_id,
        "physical_question_id": question.physical_question_id,
        "physical_segment_id": (
            selected.physical_segment_id if selected is not None else None
        ),
    }
    answer = PhysicalPeakAnswer(
        schema_version="physical_peak_answer_v1",
        state=state,
        reason=reason,
        prepared_signal_id=prepared_result.projection.prepared_signal_id,
        physical_question_id=question.physical_question_id,
        physical_segment_id=(
            selected.physical_segment_id if selected is not None else None
        ),
        physical_answer_id=_typed_physical_id(
            "physical-answer.v1:", _canonical_physical_json_bytes(answer_basis)
        ),
    )
    facts: PhysicalPeakFacts | None = None
    integration: IntegrationResult | None = None
    operations = prepared_result.operations
    if prepared_result.projection.state == "available":
        operations = (*operations, "segment_enumeration")
    if selected is not None:
        prepared = prepared_result.prepared
        if prepared is None:
            raise ValueError("available physical answer lacks worker-local signal")
        selected_values = prepared.positive_signal[
            selected.start_index : selected.end_index
        ]
        maximum = max(selected_values, default=0.0)
        if maximum <= 0.0:
            raise ValueError("available physical segment has no positive signal")
        normalized = tuple(float(value / maximum) for value in selected_values)
        normalized_bytes = _canonical_physical_json_bytes(
            {
                "schema_version": "physical_relative_shape_v1",
                "values": [_physical_float_text(value) for value in normalized],
            }
        )
        shape_hash = hashlib.sha256(normalized_bytes).hexdigest()
        provisional = PhysicalPeakFacts(
            schema_version="physical_peak_facts_v1",
            prepared_signal_id=prepared_result.projection.prepared_signal_id,
            physical_question_id=question.physical_question_id,
            physical_answer_id=answer.physical_answer_id,
            physical_segment_id=selected.physical_segment_id,
            apex_rt_min=selected.apex_rt_min,
            left_rt_min=selected.left_rt_min,
            right_rt_min=selected.right_rt_min,
            height=selected.height,
            scan_support_count=selected.scan_support_count,
            area_counts_seconds=selected.area_counts_seconds,
            area_source="trapezoid_counts_seconds_on_canonical_signal_v1",
            relative_shape_schema_version="physical_relative_shape_v1",
            relative_shape_point_count=len(normalized),
            relative_shape_content_hash=shape_hash,
            fact_content_id="",
        )
        fact_id = _typed_physical_id(
            "physical-facts.v1:",
            canonical_physical_peak_facts_bytes(
                provisional,
                relative_shape_values=normalized,
            ),
        )
        facts = replace(provisional, fact_content_id=fact_id)
        integration = _physical_raw_descriptor_projection(prepared, selected, facts)
        operations = (
            *operations,
            "integration",
            "normalization",
            "raw_descriptor_projection",
        )
    return PhysicalPeakEvaluation(
        state=state,
        reason=reason,
        prepared=prepared_result.projection,
        segment_universe=universe,
        question=question,
        answer=answer,
        facts=facts,
        integration=integration,
        operations=operations,
        retained_prepared_signal=prepared_result.prepared,
    )


def evaluate_physical_peak(
    prepared_result: PreparedSignalResult,
    *,
    selection_subject_id: str,
    query_rt_min: float,
    request_context_rt_start_min: float | None = None,
    request_context_rt_end_min: float | None = None,
    selection_policy_version: str = PHYSICAL_PEAK_POINT_POLICY,
) -> PhysicalPeakEvaluation:
    """Answer one workflow-neutral physical question from owner-derived context."""

    if not isinstance(prepared_result, PreparedSignalResult):
        raise TypeError("prepared_result must be a PreparedSignalResult")
    universe = enumerate_physical_segment_universe(
        prepared_result,
        request_context_rt_start_min=request_context_rt_start_min,
        request_context_rt_end_min=request_context_rt_end_min,
        selection_policy_version=selection_policy_version,
    )
    question = mint_physical_peak_question(
        prepared_result.projection,
        universe,
        selection_subject_id=selection_subject_id,
        query_rt_min=query_rt_min,
        selection_policy_version=selection_policy_version,
    )
    return answer_physical_peak_question(prepared_result, universe, question)


def _physical_raw_descriptor_projection(
    prepared: PreparedSignal,
    selected: PhysicalSegment,
    facts: PhysicalPeakFacts,
) -> IntegrationResult:
    rt = np.asarray(
        prepared.rt[selected.start_index : selected.end_index],
        dtype=float,
    )
    raw = np.asarray(
        prepared.raw[selected.start_index : selected.end_index],
        dtype=float,
    )
    if len(rt) < 2 or len(raw) != len(rt):
        raise ValueError("selected physical segment cannot mint a raw descriptor")
    raw_apex_offset = int(np.argmax(raw))
    raw_area_counts_seconds = float(np.trapezoid(raw, rt)) * 60.0
    return IntegrationResult(
        rt_left_min=facts.left_rt_min,
        rt_apex_min=facts.apex_rt_min,
        rt_right_min=facts.right_rt_min,
        raw_apex_rt_min=float(rt[raw_apex_offset]),
        rt_width_min=facts.right_rt_min - facts.left_rt_min,
        height_raw=float(raw[raw_apex_offset]),
        height_smoothed=float(prepared.smoothed[selected.apex_index]),
        area_raw_counts_seconds=raw_area_counts_seconds,
        integration_method="canonical_physical_signal_trapezoid_v1",
        boundary_sources=("canonical_physical_segment_v1",),
        area_baseline_corrected=facts.area_counts_seconds,
        baseline_type=prepared.profile.baseline_method,
        raw_scan_indices=(),
        area_ms1_morphology=facts.area_counts_seconds,
        ms1_morphology_area_source=facts.area_source,
        ms1_morphology_trace_method=prepared.profile.method,
        ms1_morphology_trace_window_points=prepared.profile.window_points,
        ms1_morphology_trace_effective_points=prepared.profile.window_points,
    )


def canonical_physical_peak_answer_bytes(answer: PhysicalPeakAnswer) -> bytes:
    if not isinstance(answer, PhysicalPeakAnswer):
        raise TypeError("answer must be a PhysicalPeakAnswer")
    return _canonical_physical_json_bytes(
        {
            "schema_version": answer.schema_version,
            "state": answer.state,
            "reason": answer.reason,
            "prepared_signal_id": answer.prepared_signal_id,
            "physical_question_id": answer.physical_question_id,
            "physical_segment_id": answer.physical_segment_id,
        }
    )


def validate_physical_peak_answer_identity(answer: PhysicalPeakAnswer) -> None:
    if answer.schema_version != "physical_peak_answer_v1":
        raise ValueError("unsupported physical peak answer schema")
    if answer.state not in {"available", "unavailable", "failed"}:
        raise ValueError("unsupported physical peak answer state")
    if (answer.state == "available") != (answer.reason == ""):
        raise ValueError("physical peak answer state/reason is not canonical")
    if (answer.state == "available") != (answer.physical_segment_id is not None):
        raise ValueError("physical peak answer selection is not canonical")
    expected = _typed_physical_id(
        "physical-answer.v1:", canonical_physical_peak_answer_bytes(answer)
    )
    if answer.physical_answer_id != expected:
        raise ValueError("physical_answer_id does not match owner content")


def canonical_physical_peak_facts_bytes(
    facts: PhysicalPeakFacts,
    *,
    relative_shape_values: Sequence[float],
) -> bytes:
    if not isinstance(facts, PhysicalPeakFacts):
        raise TypeError("facts must be PhysicalPeakFacts")
    normalized = tuple(
        _finite_physical_float(value, field="relative_shape_values")
        for value in relative_shape_values
    )
    if len(normalized) != facts.relative_shape_point_count:
        raise ValueError("relative shape cardinality does not match facts")
    normalized_bytes = _canonical_physical_json_bytes(
        {
            "schema_version": facts.relative_shape_schema_version,
            "values": [_physical_float_text(value) for value in normalized],
        }
    )
    if (
        hashlib.sha256(normalized_bytes).hexdigest()
        != facts.relative_shape_content_hash
    ):
        raise ValueError("relative_shape_content_hash does not match live shape")
    return _canonical_physical_json_bytes(
        {
            "schema_version": facts.schema_version,
            "prepared_signal_id": facts.prepared_signal_id,
            "physical_question_id": facts.physical_question_id,
            "physical_answer_id": facts.physical_answer_id,
            "physical_segment_id": facts.physical_segment_id,
            "apex_rt_min": _physical_float_text(facts.apex_rt_min),
            "left_rt_min": _physical_float_text(facts.left_rt_min),
            "right_rt_min": _physical_float_text(facts.right_rt_min),
            "height": _physical_float_text(facts.height),
            "scan_support_count": facts.scan_support_count,
            "area_counts_seconds": _physical_float_text(facts.area_counts_seconds),
            "area_source": facts.area_source,
            "relative_shape_schema_version": facts.relative_shape_schema_version,
            "relative_shape_point_count": facts.relative_shape_point_count,
            "relative_shape_content_hash": facts.relative_shape_content_hash,
            "relative_shape_values": [
                _physical_float_text(value) for value in normalized
            ],
        }
    )


def validate_physical_peak_facts_identity(
    facts: PhysicalPeakFacts,
    *,
    relative_shape_values: Sequence[float],
) -> None:
    expected = _typed_physical_id(
        "physical-facts.v1:",
        canonical_physical_peak_facts_bytes(
            facts,
            relative_shape_values=relative_shape_values,
        ),
    )
    if facts.fact_content_id != expected:
        raise ValueError("fact_content_id does not match complete live fact")


def validate_bounded_physical_peak_evaluation(
    evaluation: PhysicalPeakEvaluation,
) -> None:
    if not isinstance(evaluation, PhysicalPeakEvaluation):
        raise TypeError("evaluation must be a PhysicalPeakEvaluation")
    allowed_operation_sequences = tuple(
        _PHYSICAL_PEAK_OPERATION_ORDER[:count] for count in (1, 2, 3, 4, 7, 8)
    )
    if evaluation.operations not in allowed_operation_sequences:
        raise ValueError("physical evaluation operations are not canonical")
    validate_prepared_signal_identity(evaluation.prepared)
    validate_physical_segment_universe_identity(evaluation.segment_universe)
    validate_physical_peak_question_identity(
        evaluation.question,
        universe=evaluation.segment_universe,
    )
    validate_physical_peak_answer_identity(evaluation.answer)
    if (
        evaluation.prepared.prepared_signal_id
        != evaluation.segment_universe.prepared_signal_id
        or evaluation.prepared.prepared_signal_id
        != evaluation.question.prepared_signal_id
        or evaluation.prepared.prepared_signal_id
        != evaluation.answer.prepared_signal_id
    ):
        raise ValueError("bounded physical evaluation crosses prepared identities")
    if evaluation.question.trace_content_id != evaluation.prepared.trace_content_id:
        raise ValueError("bounded physical evaluation crosses trace identities")
    if (
        evaluation.answer.physical_question_id
        != evaluation.question.physical_question_id
    ):
        raise ValueError("bounded physical evaluation crosses question identities")
    if (
        evaluation.state != evaluation.answer.state
        or evaluation.reason != evaluation.answer.reason
    ):
        raise ValueError("bounded physical evaluation crosses terminal state")
    if evaluation.state == "available":
        if (
            evaluation.facts is None
            or evaluation.integration is None
            or evaluation.answer.physical_segment_id is None
        ):
            raise ValueError("available physical evaluation requires complete facts")
        facts = evaluation.facts
        integration = evaluation.integration
        if not isinstance(integration, IntegrationResult):
            raise ValueError("available physical evaluation has invalid integration")
        if evaluation.operations not in {
            _PHYSICAL_PEAK_OPERATION_ORDER[:7],
            _PHYSICAL_PEAK_OPERATION_ORDER,
        }:
            raise ValueError("available physical evaluation lacks completed operations")
        if facts.schema_version != "physical_peak_facts_v1":
            raise ValueError("unsupported physical peak facts schema")
        if facts.relative_shape_schema_version != "physical_relative_shape_v1":
            raise ValueError("unsupported physical relative-shape schema")
        if facts.relative_shape_point_count < 1:
            raise ValueError("physical relative shape must contain at least one point")
        if (
            facts.prepared_signal_id != evaluation.prepared.prepared_signal_id
            or facts.physical_question_id != evaluation.question.physical_question_id
            or facts.physical_answer_id != evaluation.answer.physical_answer_id
            or facts.physical_segment_id != evaluation.answer.physical_segment_id
        ):
            raise ValueError("bounded physical facts cross owner identities")
        _require_typed_physical_hash(
            facts.relative_shape_content_hash,
            prefix="",
            field="relative_shape_content_hash",
        )
        _require_typed_physical_hash(
            facts.fact_content_id,
            prefix="physical-facts.v1:",
            field="fact_content_id",
        )
        if (
            integration.rt_left_min != facts.left_rt_min
            or integration.rt_apex_min != facts.apex_rt_min
            or integration.rt_right_min != facts.right_rt_min
            or integration.area_baseline_corrected != facts.area_counts_seconds
            or integration.area_ms1_morphology != facts.area_counts_seconds
            or integration.raw_scan_indices
        ):
            raise ValueError("bounded raw descriptor crosses physical facts")
    elif (
        evaluation.facts is not None
        or evaluation.integration is not None
        or evaluation.answer.physical_segment_id is not None
    ):
        raise ValueError("non-available physical evaluation cannot carry facts")
    elif len(evaluation.operations) > 4:
        raise ValueError("non-available physical evaluation claims fact operations")


def rehydrate_physical_peak_evaluation(
    rt_values: object,
    intensity_values: object,
    *,
    profile: SignalProcessingProfile,
    expected: PhysicalPeakEvaluation,
    request_context_rt_start_min: float | None = None,
    request_context_rt_end_min: float | None = None,
) -> PhysicalPeakEvaluation:
    if not isinstance(expected, PhysicalPeakEvaluation):
        raise TypeError("expected must be a PhysicalPeakEvaluation")
    prepared_result = prepare_physical_signal(
        rt_values,
        intensity_values,
        trace_content_id=expected.prepared.trace_content_id,
        profile=profile,
    )
    actual = evaluate_physical_peak(
        prepared_result,
        selection_subject_id=expected.question.selection_subject_id,
        query_rt_min=expected.question.query_rt_min,
        request_context_rt_start_min=request_context_rt_start_min,
        request_context_rt_end_min=request_context_rt_end_min,
        selection_policy_version=expected.question.selection_policy_version,
    )
    if actual != expected:
        raise ValueError(
            "persisted physical evaluation does not match live owner result"
        )
    return actual


def serialize_physical_peak_evaluation(
    rt_values: object,
    intensity_values: object,
    *,
    profile: SignalProcessingProfile,
    evaluation: PhysicalPeakEvaluation,
) -> bytes:
    """Purely encode one complete owner-rehydratable physical evaluation."""

    if not isinstance(profile, SignalProcessingProfile):
        raise TypeError("profile must be a SignalProcessingProfile")
    if not isinstance(evaluation, PhysicalPeakEvaluation):
        raise TypeError("evaluation must be a PhysicalPeakEvaluation")
    rt = _physical_vector(rt_values, field="rt_values")
    raw = _physical_vector(intensity_values, field="intensity_values")
    validate_bounded_physical_peak_evaluation(evaluation)
    validate_physical_trace_content_id(
        evaluation.prepared.trace_content_id,
        rt,
        raw,
    )
    if evaluation.prepared.profile_id != profile.profile_id:
        raise ValueError("physical evaluation crosses signal-processing profile")
    return _canonical_physical_json_bytes(
        _persisted_physical_payload(rt, raw, profile, evaluation)
    )


def deserialize_physical_peak_evaluation(
    payload: bytes | str,
    *,
    expected_profile: SignalProcessingProfile | None = None,
    expected_trace_content_id: str | None = None,
) -> tuple[
    tuple[float, ...],
    tuple[float, ...],
    SignalProcessingProfile,
    PhysicalPeakEvaluation,
]:
    """Strictly parse and rehydrate one persisted physical evaluation."""

    raw_payload = payload.encode("utf-8") if isinstance(payload, str) else payload
    if not isinstance(raw_payload, bytes):
        raise TypeError("physical evaluation payload must be bytes or str")
    try:
        decoded = raw_payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("physical evaluation payload must be UTF-8") from exc
    try:
        value = json.loads(decoded, object_pairs_hook=_unique_physical_mapping)
    except json.JSONDecodeError as exc:
        raise ValueError("physical evaluation payload must be valid JSON") from exc
    root = _physical_mapping(value, label="physical evaluation payload")
    _require_physical_keys(root, _PERSISTED_PHYSICAL_KEYS, label="physical payload")
    if root["schema_version"] != _PERSISTED_PHYSICAL_EVALUATION_SCHEMA:
        raise ValueError("unsupported physical evaluation persistence schema")

    profile_envelope = _physical_mapping(root["profile"], label="profile")
    _require_physical_keys(profile_envelope, _PERSISTED_PROFILE_KEYS, label="profile")
    profile_content = _physical_mapping(
        profile_envelope["content"], label="profile content"
    )
    profile = deserialize_signal_processing_profile(
        _canonical_physical_json_bytes(profile_content),
        expected_profile_hash=_physical_text(
            profile_envelope["profile_hash"], field="profile_hash"
        ),
        expected_profile_id=_physical_text(
            profile_envelope["profile_id"], field="profile_id"
        ),
    )
    if expected_profile is not None and profile != expected_profile:
        raise ValueError("persisted physical profile crosses expected run profile")

    trace = _physical_mapping(root["trace"], label="trace")
    _require_physical_keys(trace, _PERSISTED_TRACE_KEYS, label="trace")
    rt = tuple(
        _physical_float_from_text(item, field="trace.rt")
        for item in _physical_list(trace["rt"], label="trace.rt")
    )
    raw = tuple(
        _physical_float_from_text(item, field="trace.raw")
        for item in _physical_list(trace["raw"], label="trace.raw")
    )
    trace_content_id = _physical_text(
        trace["trace_content_id"], field="trace_content_id"
    )
    validate_physical_trace_content_id(trace_content_id, rt, raw)
    if (
        expected_trace_content_id is not None
        and trace_content_id != expected_trace_content_id
    ):
        raise ValueError("persisted trace crosses expected trace identity")

    evaluation_value = _physical_mapping(root["evaluation"], label="evaluation")
    evaluation = _physical_evaluation_from_payload(evaluation_value)
    if evaluation.prepared.trace_content_id != trace_content_id:
        raise ValueError("persisted physical evaluation crosses trace identity")
    if evaluation.prepared.profile_id != profile.profile_id:
        raise ValueError("persisted physical evaluation crosses profile identity")
    rehydrated = rehydrate_physical_peak_evaluation(
        rt,
        raw,
        profile=profile,
        expected=evaluation,
        request_context_rt_start_min=evaluation.question.context_rt_start_min,
        request_context_rt_end_min=evaluation.question.context_rt_end_min,
    )
    # Profile and trace tokens are already canonical and identity-checked.
    # Keep the live-owner evaluation check above and rebuild its payload, but
    # reuse the checked inputs instead of converting and hashing them again.
    canonical = _canonical_physical_json_bytes(
        {
            "schema_version": _PERSISTED_PHYSICAL_EVALUATION_SCHEMA,
            "profile": profile_envelope,
            "trace": trace,
            "evaluation": _physical_evaluation_payload(rehydrated),
        }
    )
    if canonical != raw_payload:
        raise ValueError("physical evaluation payload is not canonical")
    return rt, raw, profile, rehydrated


def _persisted_physical_payload(
    rt: tuple[float, ...],
    raw: tuple[float, ...],
    profile: SignalProcessingProfile,
    evaluation: PhysicalPeakEvaluation,
) -> dict[str, object]:
    return {
        "schema_version": _PERSISTED_PHYSICAL_EVALUATION_SCHEMA,
        "profile": {
            "profile_hash": profile.profile_hash,
            "profile_id": profile.profile_id,
            "content": json.loads(
                canonical_signal_processing_profile_bytes(profile).decode("utf-8")
            ),
        },
        "trace": {
            "trace_content_id": evaluation.prepared.trace_content_id,
            "rt": [_physical_float_text(item) for item in rt],
            "raw": [_physical_float_text(item) for item in raw],
        },
        "evaluation": _physical_evaluation_payload(evaluation),
    }


def _physical_evaluation_payload(
    evaluation: PhysicalPeakEvaluation,
) -> dict[str, object]:
    prepared = evaluation.prepared
    universe = evaluation.segment_universe
    question = evaluation.question
    answer = evaluation.answer
    facts = evaluation.facts
    integration = evaluation.integration
    return {
        "state": evaluation.state,
        "reason": evaluation.reason,
        "prepared": {
            "schema_version": prepared.schema_version,
            "trace_content_id": prepared.trace_content_id,
            "profile_id": prepared.profile_id,
            "prepared_signal_id": prepared.prepared_signal_id,
            "trace_point_count": prepared.trace_point_count,
            "state": prepared.state,
            "reason": prepared.reason,
        },
        "segment_universe": {
            "schema_version": universe.schema_version,
            "prepared_signal_id": universe.prepared_signal_id,
            "context_rt_start_min": _physical_float_text(universe.context_rt_start_min),
            "context_rt_end_min": _physical_float_text(universe.context_rt_end_min),
            "state": universe.state,
            "reason": universe.reason,
            "segments": [
                {
                    "physical_segment_id": segment.physical_segment_id,
                    **_physical_segment_basis(segment),
                }
                for segment in universe.segments
            ],
            "segment_universe_id": universe.segment_universe_id,
        },
        "question": {
            **_physical_question_basis(question),
            "physical_question_id": question.physical_question_id,
        },
        "answer": {
            "schema_version": answer.schema_version,
            "state": answer.state,
            "reason": answer.reason,
            "prepared_signal_id": answer.prepared_signal_id,
            "physical_question_id": answer.physical_question_id,
            "physical_segment_id": answer.physical_segment_id,
            "physical_answer_id": answer.physical_answer_id,
        },
        "facts": (
            None
            if facts is None
            else {
                "schema_version": facts.schema_version,
                "prepared_signal_id": facts.prepared_signal_id,
                "physical_question_id": facts.physical_question_id,
                "physical_answer_id": facts.physical_answer_id,
                "physical_segment_id": facts.physical_segment_id,
                "apex_rt_min": _physical_float_text(facts.apex_rt_min),
                "left_rt_min": _physical_float_text(facts.left_rt_min),
                "right_rt_min": _physical_float_text(facts.right_rt_min),
                "height": _physical_float_text(facts.height),
                "scan_support_count": facts.scan_support_count,
                "area_counts_seconds": _physical_float_text(facts.area_counts_seconds),
                "area_source": facts.area_source,
                "relative_shape_schema_version": (facts.relative_shape_schema_version),
                "relative_shape_point_count": facts.relative_shape_point_count,
                "relative_shape_content_hash": facts.relative_shape_content_hash,
                "fact_content_id": facts.fact_content_id,
            }
        ),
        "integration": (
            None if integration is None else _integration_payload(integration)
        ),
        "operations": list(evaluation.operations),
    }


def _integration_payload(integration: IntegrationResult) -> dict[str, object]:
    float_fields = {
        "rt_left_min",
        "rt_apex_min",
        "rt_right_min",
        "raw_apex_rt_min",
        "rt_width_min",
        "height_raw",
        "height_smoothed",
        "area_raw_counts_seconds",
        "area_baseline_corrected",
        "area_uncertainty",
        "baseline_residual_mad",
        "baseline_score",
        "area_ms1_morphology",
    }
    sequence_fields = {"boundary_sources", "raw_scan_indices"}
    payload: dict[str, object] = {}
    for key in _INTEGRATION_KEYS:
        value = getattr(integration, key)
        if key in float_fields:
            payload[key] = None if value is None else _physical_float_text(value)
        elif key in sequence_fields:
            payload[key] = list(value)
        else:
            payload[key] = value
    return payload


def _physical_evaluation_from_payload(
    value: Mapping[str, object],
) -> PhysicalPeakEvaluation:
    _require_physical_keys(value, _PERSISTED_EVALUATION_KEYS, label="evaluation")
    prepared_value = _physical_mapping(value["prepared"], label="prepared")
    _require_physical_keys(prepared_value, _PREPARED_PROJECTION_KEYS, label="prepared")
    prepared = PreparedSignalProjection(
        schema_version=_physical_text(
            prepared_value["schema_version"], field="prepared.schema_version"
        ),
        trace_content_id=_physical_text(
            prepared_value["trace_content_id"], field="prepared.trace_content_id"
        ),
        profile_id=_physical_text(
            prepared_value["profile_id"], field="prepared.profile_id"
        ),
        prepared_signal_id=_physical_text(
            prepared_value["prepared_signal_id"],
            field="prepared.prepared_signal_id",
        ),
        trace_point_count=_physical_integer(
            prepared_value["trace_point_count"], field="prepared.trace_point_count"
        ),
        state=_physical_state(prepared_value["state"], field="prepared.state"),
        reason=_physical_text(prepared_value["reason"], field="prepared.reason"),
    )
    universe_value = _physical_mapping(
        value["segment_universe"], label="segment_universe"
    )
    _require_physical_keys(
        universe_value, _SEGMENT_UNIVERSE_KEYS, label="segment_universe"
    )
    segments: list[PhysicalSegment] = []
    for raw_segment in _physical_list(universe_value["segments"], label="segments"):
        segment = _physical_mapping(raw_segment, label="segment")
        _require_physical_keys(segment, _SEGMENT_KEYS, label="segment")
        segment_schema = _physical_text(
            segment["schema_version"], field="segment.schema_version"
        )
        if segment_schema != "physical_segment_v1":
            raise ValueError("unsupported physical segment schema")
        segments.append(
            PhysicalSegment(
                physical_segment_id=_physical_text(
                    segment["physical_segment_id"], field="physical_segment_id"
                ),
                start_index=_physical_integer(
                    segment["start_index"], field="start_index"
                ),
                end_index=_physical_integer(segment["end_index"], field="end_index"),
                apex_index=_physical_integer(segment["apex_index"], field="apex_index"),
                left_rt_min=_physical_float_from_text(
                    segment["left_rt_min"], field="left_rt_min"
                ),
                right_rt_min=_physical_float_from_text(
                    segment["right_rt_min"], field="right_rt_min"
                ),
                apex_rt_min=_physical_float_from_text(
                    segment["apex_rt_min"], field="apex_rt_min"
                ),
                height=_physical_float_from_text(segment["height"], field="height"),
                scan_support_count=_physical_integer(
                    segment["scan_support_count"], field="scan_support_count"
                ),
                area_counts_seconds=_physical_float_from_text(
                    segment["area_counts_seconds"], field="area_counts_seconds"
                ),
                segment_class=_physical_text(
                    segment["segment_class"], field="segment_class"
                ),
                boundary_stop_reason=_physical_text(
                    segment["boundary_stop_reason"], field="boundary_stop_reason"
                ),
                evidence_sources=_physical_text_tuple(
                    segment["evidence_sources"], field="evidence_sources"
                ),
            )
        )
    universe = PhysicalSegmentUniverse(
        schema_version=_physical_text(
            universe_value["schema_version"], field="universe.schema_version"
        ),
        prepared_signal_id=_physical_text(
            universe_value["prepared_signal_id"], field="universe.prepared_signal_id"
        ),
        context_rt_start_min=_physical_float_from_text(
            universe_value["context_rt_start_min"], field="context_rt_start_min"
        ),
        context_rt_end_min=_physical_float_from_text(
            universe_value["context_rt_end_min"], field="context_rt_end_min"
        ),
        state=_physical_state(universe_value["state"], field="universe.state"),
        reason=_physical_text(universe_value["reason"], field="universe.reason"),
        segments=tuple(segments),
        segment_universe_id=_physical_text(
            universe_value["segment_universe_id"], field="segment_universe_id"
        ),
    )
    question_value = _physical_mapping(value["question"], label="question")
    _require_physical_keys(question_value, _QUESTION_KEYS, label="question")
    question = PhysicalPeakQuestion(
        schema_version=_physical_text(
            question_value["schema_version"], field="question.schema_version"
        ),
        trace_content_id=_physical_text(
            question_value["trace_content_id"], field="question.trace_content_id"
        ),
        prepared_signal_id=_physical_text(
            question_value["prepared_signal_id"],
            field="question.prepared_signal_id",
        ),
        segment_universe_id=_physical_text(
            question_value["segment_universe_id"], field="segment_universe_id"
        ),
        segment_ids=_physical_text_tuple(
            question_value["segment_ids"], field="segment_ids"
        ),
        selection_subject_id=_physical_text(
            question_value["selection_subject_id"], field="selection_subject_id"
        ),
        query_rt_min=_physical_float_from_text(
            question_value["query_rt_min"], field="query_rt_min"
        ),
        context_rt_start_min=_physical_float_from_text(
            question_value["context_rt_start_min"], field="context_rt_start_min"
        ),
        context_rt_end_min=_physical_float_from_text(
            question_value["context_rt_end_min"], field="context_rt_end_min"
        ),
        admissible_segment_ids=_physical_text_tuple(
            question_value["admissible_segment_ids"],
            field="admissible_segment_ids",
        ),
        selection_policy_version=_physical_text(
            question_value["selection_policy_version"],
            field="selection_policy_version",
        ),
        physical_question_id=_physical_text(
            question_value["physical_question_id"], field="physical_question_id"
        ),
    )
    answer_value = _physical_mapping(value["answer"], label="answer")
    _require_physical_keys(answer_value, _ANSWER_KEYS, label="answer")
    answer = PhysicalPeakAnswer(
        schema_version=_physical_text(
            answer_value["schema_version"], field="answer.schema_version"
        ),
        state=_physical_state(answer_value["state"], field="answer.state"),
        reason=_physical_text(answer_value["reason"], field="answer.reason"),
        prepared_signal_id=_physical_text(
            answer_value["prepared_signal_id"], field="answer.prepared_signal_id"
        ),
        physical_question_id=_physical_text(
            answer_value["physical_question_id"], field="answer.physical_question_id"
        ),
        physical_segment_id=_optional_physical_text(
            answer_value["physical_segment_id"], field="physical_segment_id"
        ),
        physical_answer_id=_physical_text(
            answer_value["physical_answer_id"], field="physical_answer_id"
        ),
    )
    facts = _physical_facts_from_payload(value["facts"])
    integration = _physical_integration_from_payload(value["integration"])
    evaluation = PhysicalPeakEvaluation(
        state=_physical_state(value["state"], field="evaluation.state"),
        reason=_physical_text(value["reason"], field="evaluation.reason"),
        prepared=prepared,
        segment_universe=universe,
        question=question,
        answer=answer,
        facts=facts,
        integration=integration,
        operations=tuple(
            _physical_operation(item)
            for item in _physical_list(value["operations"], label="operations")
        ),
    )
    validate_bounded_physical_peak_evaluation(evaluation)
    return evaluation


def _physical_facts_from_payload(value: object) -> PhysicalPeakFacts | None:
    if value is None:
        return None
    facts = _physical_mapping(value, label="facts")
    _require_physical_keys(facts, _FACT_KEYS, label="facts")
    return PhysicalPeakFacts(
        schema_version=_physical_text(facts["schema_version"], field="facts.schema"),
        prepared_signal_id=_physical_text(
            facts["prepared_signal_id"], field="facts.prepared_signal_id"
        ),
        physical_question_id=_physical_text(
            facts["physical_question_id"], field="facts.physical_question_id"
        ),
        physical_answer_id=_physical_text(
            facts["physical_answer_id"], field="facts.physical_answer_id"
        ),
        physical_segment_id=_physical_text(
            facts["physical_segment_id"], field="facts.physical_segment_id"
        ),
        apex_rt_min=_physical_float_from_text(
            facts["apex_rt_min"], field="apex_rt_min"
        ),
        left_rt_min=_physical_float_from_text(
            facts["left_rt_min"], field="left_rt_min"
        ),
        right_rt_min=_physical_float_from_text(
            facts["right_rt_min"], field="right_rt_min"
        ),
        height=_physical_float_from_text(facts["height"], field="height"),
        scan_support_count=_physical_integer(
            facts["scan_support_count"], field="scan_support_count"
        ),
        area_counts_seconds=_physical_float_from_text(
            facts["area_counts_seconds"], field="area_counts_seconds"
        ),
        area_source=_physical_text(facts["area_source"], field="area_source"),
        relative_shape_schema_version=_physical_text(
            facts["relative_shape_schema_version"],
            field="relative_shape_schema_version",
        ),
        relative_shape_point_count=_physical_integer(
            facts["relative_shape_point_count"], field="relative_shape_point_count"
        ),
        relative_shape_content_hash=_physical_text(
            facts["relative_shape_content_hash"], field="relative_shape_content_hash"
        ),
        fact_content_id=_physical_text(
            facts["fact_content_id"], field="fact_content_id"
        ),
    )


def _physical_integration_from_payload(value: object) -> IntegrationResult | None:
    if value is None:
        return None
    integration = _physical_mapping(value, label="integration")
    _require_physical_keys(integration, _INTEGRATION_KEYS, label="integration")
    required_float_fields = (
        "rt_left_min",
        "rt_apex_min",
        "rt_right_min",
        "raw_apex_rt_min",
        "rt_width_min",
        "height_raw",
        "height_smoothed",
        "area_raw_counts_seconds",
    )
    optional_float_fields = (
        "area_baseline_corrected",
        "area_uncertainty",
        "baseline_residual_mad",
        "baseline_score",
        "area_ms1_morphology",
    )
    required = {
        key: _physical_float_from_text(integration[key], field=key)
        for key in required_float_fields
    }
    optional = {
        key: _optional_physical_float_from_text(integration[key], field=key)
        for key in optional_float_fields
    }
    return IntegrationResult(
        rt_left_min=required["rt_left_min"],
        rt_apex_min=required["rt_apex_min"],
        rt_right_min=required["rt_right_min"],
        raw_apex_rt_min=required["raw_apex_rt_min"],
        rt_width_min=required["rt_width_min"],
        height_raw=required["height_raw"],
        height_smoothed=required["height_smoothed"],
        area_raw_counts_seconds=required["area_raw_counts_seconds"],
        integration_method=_physical_text(
            integration["integration_method"], field="integration_method"
        ),
        boundary_sources=_physical_text_tuple(
            integration["boundary_sources"], field="boundary_sources"
        ),
        area_baseline_corrected=optional["area_baseline_corrected"],
        area_uncertainty=optional["area_uncertainty"],
        area_uncertainty_formula_version=_physical_text(
            integration["area_uncertainty_formula_version"],
            field="area_uncertainty_formula_version",
        ),
        baseline_residual_mad=optional["baseline_residual_mad"],
        area_uncertainty_noise_source=_physical_text(
            integration["area_uncertainty_noise_source"],
            field="area_uncertainty_noise_source",
        ),
        baseline_type=_physical_text(
            integration["baseline_type"], field="baseline_type"
        ),
        baseline_score=optional["baseline_score"],
        raw_scan_indices=tuple(
            _physical_integer(item, field="raw_scan_indices")
            for item in _physical_list(
                integration["raw_scan_indices"], label="raw_scan_indices"
            )
        ),
        area_ms1_morphology=optional["area_ms1_morphology"],
        ms1_morphology_area_source=_physical_text(
            integration["ms1_morphology_area_source"],
            field="ms1_morphology_area_source",
        ),
        ms1_morphology_trace_method=_physical_text(
            integration["ms1_morphology_trace_method"],
            field="ms1_morphology_trace_method",
        ),
        ms1_morphology_trace_window_points=_optional_physical_integer(
            integration["ms1_morphology_trace_window_points"],
            field="ms1_morphology_trace_window_points",
        ),
        ms1_morphology_trace_effective_points=_optional_physical_integer(
            integration["ms1_morphology_trace_effective_points"],
            field="ms1_morphology_trace_effective_points",
        ),
    )


def _physical_segment_from_chrom(segment: Any) -> PhysicalSegment:
    provisional = PhysicalSegment(
        physical_segment_id="",
        start_index=segment.interval.start_index,
        end_index=segment.interval.end_index,
        apex_index=segment.apex_index,
        left_rt_min=segment.interval.rt_start_min,
        right_rt_min=segment.interval.rt_end_min,
        apex_rt_min=segment.apex_rt_min,
        height=segment.morphology_apex_residual,
        scan_support_count=segment.interval.scan_count,
        area_counts_seconds=segment.area_baseline_corrected,
        segment_class=segment.segment_class,
        boundary_stop_reason=segment.boundary_stop_reason,
        evidence_sources=segment.evidence_sources,
    )
    return replace(
        provisional,
        physical_segment_id=_typed_physical_id(
            "physical-segment.v1:", canonical_physical_segment_bytes(provisional)
        ),
    )


def _physical_segment_basis(segment: PhysicalSegment) -> dict[str, object]:
    return {
        "schema_version": "physical_segment_v1",
        "start_index": segment.start_index,
        "end_index": segment.end_index,
        "apex_index": segment.apex_index,
        "left_rt_min": _physical_float_text(segment.left_rt_min),
        "right_rt_min": _physical_float_text(segment.right_rt_min),
        "apex_rt_min": _physical_float_text(segment.apex_rt_min),
        "height": _physical_float_text(segment.height),
        "scan_support_count": segment.scan_support_count,
        "area_counts_seconds": _physical_float_text(segment.area_counts_seconds),
        "segment_class": segment.segment_class,
        "boundary_stop_reason": segment.boundary_stop_reason,
        "evidence_sources": list(segment.evidence_sources),
    }


def _physical_segment_universe_basis(
    prepared_signal_id: str,
    context_rt_start_min: float,
    context_rt_end_min: float,
    state: PhysicalPeakState,
    reason: str,
    segments: tuple[PhysicalSegment, ...],
    *,
    schema_version: str,
) -> dict[str, object]:
    return {
        "schema_version": schema_version,
        "prepared_signal_id": prepared_signal_id,
        "context_rt_start_min": _physical_float_text(context_rt_start_min),
        "context_rt_end_min": _physical_float_text(context_rt_end_min),
        "state": state,
        "reason": reason,
        "segments": [
            {
                "physical_segment_id": segment.physical_segment_id,
                **_physical_segment_basis(segment),
            }
            for segment in segments
        ],
    }


def _physical_question_basis(question: PhysicalPeakQuestion) -> dict[str, object]:
    return {
        "schema_version": question.schema_version,
        "trace_content_id": question.trace_content_id,
        "prepared_signal_id": question.prepared_signal_id,
        "segment_universe_id": question.segment_universe_id,
        "segment_ids": list(question.segment_ids),
        "selection_subject_id": question.selection_subject_id,
        "query_rt_min": _physical_float_text(question.query_rt_min),
        "context_rt_start_min": _physical_float_text(question.context_rt_start_min),
        "context_rt_end_min": _physical_float_text(question.context_rt_end_min),
        "admissible_segment_ids": list(question.admissible_segment_ids),
        "selection_policy_version": question.selection_policy_version,
    }


def _physical_vector(values: object, *, field: str) -> tuple[float, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{field} must be a one-dimensional numeric sequence")
    try:
        array = np.asarray(values, dtype=float)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{field} must be a one-dimensional numeric sequence") from exc
    if array.ndim != 1:
        raise ValueError(f"{field} must be one-dimensional")
    return tuple(float(value) for value in array)


def _physical_trace_failure_reason(
    rt: tuple[float, ...], raw: tuple[float, ...]
) -> str:
    if len(rt) != len(raw):
        return "length_mismatch"
    if not all(math.isfinite(value) for value in (*rt, *raw)):
        return "nonfinite_trace"
    if any(right <= left for left, right in zip(rt, rt[1:])):
        return "non_monotonic_rt"
    return ""


def _derivable_physical_trace_context(
    rt: tuple[float, ...],
) -> tuple[float, float] | None:
    if not rt:
        return None
    start = float(rt[0])
    end = float(rt[-1])
    if not math.isfinite(start) or not math.isfinite(end) or start > end:
        return None
    return start, end


def _physical_float_text(value: float) -> str:
    number = float(value)
    if number == 0.0:
        return (0.0).hex()
    if math.isnan(number):
        return "nan"
    if math.isinf(number):
        return "inf" if number > 0 else "-inf"
    return number.hex()


def _finite_physical_float(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be a finite number")
    return result


def _validated_request_context(
    start_value: float | None,
    end_value: float | None,
) -> tuple[float, float] | None:
    if start_value is None and end_value is None:
        return None
    if start_value is None or end_value is None:
        raise ValueError("request context bounds must be supplied together")
    start = _finite_physical_float(
        start_value,
        field="request_context_rt_start_min",
    )
    end = _finite_physical_float(
        end_value,
        field="request_context_rt_end_min",
    )
    if start > end:
        raise ValueError("request context start must not exceed end")
    return start, end


def _physical_owner_context(
    prepared_result: PreparedSignalResult,
    *,
    request_context_rt_start_min: float | None,
    request_context_rt_end_min: float | None,
) -> tuple[float, float]:
    request_context = _validated_request_context(
        request_context_rt_start_min,
        request_context_rt_end_min,
    )
    if (
        prepared_result.trace_context_rt_start_min is not None
        and prepared_result.trace_context_rt_end_min is not None
    ):
        return (
            prepared_result.trace_context_rt_start_min,
            prepared_result.trace_context_rt_end_min,
        )
    if request_context is None:
        raise ValueError(
            "request context is required when trace context cannot be derived"
        )
    return request_context


def _required_physical_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise TypeError(f"{field} must be non-empty text")
    return value


def _canonical_physical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _typed_physical_id(prefix: str, payload: bytes) -> str:
    return f"{prefix}{hashlib.sha256(payload).hexdigest()}"


def _require_typed_physical_hash(value: str, *, prefix: str, field: str) -> None:
    if not isinstance(value, str) or not value.startswith(prefix):
        raise ValueError(f"{field} has an invalid typed-hash prefix")
    digest = value[len(prefix) :]
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError(f"{field} must carry one lowercase SHA-256 digest")


def _unique_physical_mapping(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"physical payload has duplicate field: {key}")
        result[key] = value
    return result


def _physical_mapping(value: object, *, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be one JSON object")
    return value


def _physical_list(value: object, *, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be one JSON array")
    return value


def _require_physical_keys(
    value: Mapping[str, object],
    keys: tuple[str, ...],
    *,
    label: str,
) -> None:
    actual = tuple(value)
    if actual != keys:
        raise ValueError(f"{label} fields must be exactly {keys}; got {actual}")


def _physical_text(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text")
    return value


def _optional_physical_text(value: object, *, field: str) -> str | None:
    if value is None:
        return None
    return _physical_text(value, field=field)


def _physical_integer(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be an integer")
    return value


def _optional_physical_integer(value: object, *, field: str) -> int | None:
    if value is None:
        return None
    return _physical_integer(value, field=field)


def _physical_float_from_text(value: object, *, field: str) -> float:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be canonical hexadecimal text")
    if value == "nan":
        result = float("nan")
    elif value == "inf":
        result = float("inf")
    elif value == "-inf":
        result = float("-inf")
    else:
        try:
            result = float.fromhex(value)
        except ValueError as exc:
            raise ValueError(f"{field} must be canonical hexadecimal text") from exc
    if _physical_float_text(result) != value:
        raise ValueError(f"{field} must be canonical hexadecimal text")
    return result


def _optional_physical_float_from_text(
    value: object,
    *,
    field: str,
) -> float | None:
    if value is None:
        return None
    return _physical_float_from_text(value, field=field)


def _physical_text_tuple(value: object, *, field: str) -> tuple[str, ...]:
    return tuple(
        _physical_text(item, field=field) for item in _physical_list(value, label=field)
    )


def _physical_state(value: object, *, field: str) -> PhysicalPeakState:
    text = _physical_text(value, field=field)
    if text not in {"available", "unavailable", "failed"}:
        raise ValueError(f"{field} is not a physical terminal state")
    return text  # type: ignore[return-value]


def _physical_operation(value: object) -> PhysicalPeakOperation:
    text = _physical_text(value, field="physical operation")
    if text not in _PHYSICAL_PEAK_OPERATION_ORDER:
        raise ValueError("unsupported physical operation")
    return text  # type: ignore[return-value]


@dataclass(frozen=True)
class TraceEvidenceFacts:
    local_sn_ratio: float | None = None
    local_sn_quality: EvidenceQuality = "unknown"
    baseline_method: str = ""
    residual_mad: float | None = None
    noise_source: str = ""
    active_trace_source: str = ""
    morphology_trace_method: str = ""
    morphology_trace_window_points: int | None = None
    symmetry_quality: EvidenceQuality = "unknown"
    width_quality: EvidenceQuality = "unknown"
    noise_shape_quality: EvidenceQuality = "unknown"
    scan_count: int | None = None
    duration_min: float | None = None
    edge_ratio: float | None = None
    continuity: float | None = None
    quality_flags: tuple[str, ...] = ()
    hard_quality_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class TraceWindowEvidenceFacts:
    """Hypothesis-neutral physical facts around one explicit trace coordinate."""

    status: Literal["ok", "inconclusive"]
    reason: str
    query_apex_rt_min: float | None
    query_apex_in_trace_window: bool
    trace_apex_rt_min: float | None = None
    global_trace_apex_delta_min: float | None = None
    local_apex_delta_min: float | None = None
    local_max_intensity: float | None = None
    local_to_global_max_ratio: float | None = None


@dataclass(frozen=True)
class PeakTraceEvidenceFacts:
    """One selected integration and its reused Gaussian15 physical evidence."""

    integration: IntegrationResult
    normalized_shape_trace: OwnMaxTrace
    window: TraceWindowEvidenceFacts
    smoothing_count: int
    integration_count: int


@dataclass(frozen=True)
class ChemicalEvidenceFacts:
    neutral_loss_required: bool = True
    ms2_present: bool | None = None
    nl_match: bool | None = None
    nl_status: str = ""
    ms2_trace_strength: str = ""
    loss_ppm: float | None = None
    ms2_rt_delta_min: float | None = None
    trigger_scan_count: int | None = None
    strict_nl_scan_count: int | None = None
    ms1_peak_group_source: str = ""
    ms1_peak_group_rt_min: float | None = None
    ms1_peak_group_rt_max: float | None = None
    ms1_peak_group_trigger_scan_count: int | None = None
    ms1_peak_group_strict_nl_scan_count: int | None = None
    ms1_peak_group_strict_nl_event_count: int | None = None
    outside_ms1_peak_group_trigger_scan_count: int | None = None
    outside_ms1_peak_group_strict_nl_scan_count: int | None = None
    alignment_source: str = ""
    product_absence_reason: str = ""
    acquisition_opportunity: AcquisitionOpportunity = "not_observed"


@dataclass(frozen=True)
class RtEvidenceFacts:
    selected_apex_rt_min: float
    rt_min: float | None = None
    rt_max: float | None = None
    rt_prior_min: float | None = None
    rt_prior_sigma_min: float | None = None
    rt_prior_delta_min: float | None = None
    rt_prior_status: RtEvidenceStatus = "missing"
    window_status: RtWindowStatus = "unknown"
    paired_istd_anchor_rt_min: float | None = None
    paired_istd_delta_min: float | None = None
    paired_istd_status: RtEvidenceStatus = "missing"
    role: str = ""
    istd_pair: str = ""
    prefer_rt_prior_tiebreak: bool = False


@dataclass(frozen=True)
class BoundaryEvidenceFacts:
    proposal_sources: tuple[str, ...] = ()
    cwt_same_apex_observed: bool = False
    cwt_best_scale: float | None = None
    cwt_ridge_persistence: float | None = None
    chrom_peak_segment_present: bool = False
    boundary_sources: tuple[str, ...] = ("candidate_interval",)


@dataclass(frozen=True)
class CandidateEvidenceFacts:
    facts_version: str
    candidate_id: str
    abundance: float
    area: float
    height: float
    trace: TraceEvidenceFacts
    chemical: ChemicalEvidenceFacts
    rt: RtEvidenceFacts
    boundary: BoundaryEvidenceFacts
    quality_penalty: int = 0
    selection_quality_penalty: float | None = None


def gaussian15_trace_window_evidence(
    rt_values: Sequence[float | int | None],
    intensity_values: Sequence[float | int | None],
    *,
    query_apex_rt_min: float | int | None,
    half_window_min: float = DEFAULT_LOCAL_HALF_WINDOW_MIN,
) -> tuple[OwnMaxTrace, TraceWindowEvidenceFacts]:
    """Mint one Gaussian15 trace and its bound hypothesis-neutral window facts."""

    rt = tuple(rt_values)
    intensity = tuple(intensity_values)
    normalized_trace = own_max_normalized_trace(
        rt,
        intensity,
        smooth_points=DEFAULT_OWN_MAX_SMOOTH_POINTS,
    )
    bound_rt = rt if len(rt) == len(intensity) else ()
    requested_query_apex = _optional_float(query_apex_rt_min)
    query_apex = requested_query_apex
    if query_apex is None and normalized_trace.is_usable:
        trace_rt = np.asarray(normalized_trace.rt, dtype=float)
        trace_intensity = np.asarray(normalized_trace.intensity, dtype=float)
        if trace_rt.size and trace_rt.size == trace_intensity.size:
            query_apex = float(trace_rt[int(np.argmax(trace_intensity))])
    return normalized_trace, _trace_window_evidence_facts(
        bound_rt,
        normalized_trace,
        query_apex_rt_min=query_apex,
        half_window_min=half_window_min,
        report_global_trace_apex_delta=requested_query_apex is not None,
    )


def _trace_window_evidence_facts(
    bound_rt_values: Sequence[float | int | None],
    normalized_trace: OwnMaxTrace,
    *,
    query_apex_rt_min: float | int | None,
    half_window_min: float,
    report_global_trace_apex_delta: bool,
) -> TraceWindowEvidenceFacts:
    """Describe facts from coordinates and Gaussian15 trace minted together above."""

    query_apex = _optional_float(query_apex_rt_min)
    try:
        raw_rt = np.asarray(tuple(bound_rt_values), dtype=float)
    except (TypeError, ValueError):
        raw_rt = np.asarray((), dtype=float)
    finite_rt = raw_rt[np.isfinite(raw_rt)] if raw_rt.ndim == 1 else np.asarray(())
    query_in_window = bool(
        query_apex is not None
        and finite_rt.size
        and float(np.min(finite_rt)) - 1e-9
        <= query_apex
        <= float(np.max(finite_rt)) + 1e-9
    )
    if query_apex is None:
        return TraceWindowEvidenceFacts(
            status="inconclusive",
            reason="query_apex_unavailable",
            query_apex_rt_min=None,
            query_apex_in_trace_window=False,
        )
    if not normalized_trace.is_usable or normalized_trace.max_intensity is None:
        return TraceWindowEvidenceFacts(
            status="inconclusive",
            reason=normalized_trace.reason,
            query_apex_rt_min=query_apex,
            query_apex_in_trace_window=query_in_window,
        )

    trace_rt = np.asarray(normalized_trace.rt, dtype=float)
    trace_intensity = np.asarray(normalized_trace.intensity, dtype=float)
    if trace_rt.size == 0 or trace_intensity.size != trace_rt.size:
        return TraceWindowEvidenceFacts(
            status="inconclusive",
            reason="normalized_trace_unavailable",
            query_apex_rt_min=query_apex,
            query_apex_in_trace_window=query_in_window,
        )
    trace_apex_index = int(np.argmax(trace_intensity))
    trace_apex_rt = float(trace_rt[trace_apex_index])
    global_trace_apex_delta = (
        trace_apex_rt - query_apex if report_global_trace_apex_delta else None
    )
    local_mask = (trace_rt >= query_apex - half_window_min) & (
        trace_rt <= query_apex + half_window_min
    )
    if not np.any(local_mask):
        return TraceWindowEvidenceFacts(
            status="inconclusive",
            reason="local_window_unavailable",
            query_apex_rt_min=query_apex,
            query_apex_in_trace_window=query_in_window,
            trace_apex_rt_min=trace_apex_rt,
            global_trace_apex_delta_min=global_trace_apex_delta,
        )
    local_rt = trace_rt[local_mask]
    local_intensity = trace_intensity[local_mask]
    local_index = int(np.argmax(local_intensity))
    local_ratio = float(local_intensity[local_index])
    if local_ratio <= 0:
        return TraceWindowEvidenceFacts(
            status="inconclusive",
            reason="non_positive_local_signal",
            query_apex_rt_min=query_apex,
            query_apex_in_trace_window=query_in_window,
            trace_apex_rt_min=trace_apex_rt,
            global_trace_apex_delta_min=global_trace_apex_delta,
        )
    return TraceWindowEvidenceFacts(
        status="ok",
        reason="gaussian15_query_window_evidence",
        query_apex_rt_min=query_apex,
        query_apex_in_trace_window=query_in_window,
        trace_apex_rt_min=trace_apex_rt,
        global_trace_apex_delta_min=global_trace_apex_delta,
        local_apex_delta_min=float(local_rt[local_index] - query_apex),
        local_max_intensity=local_ratio * normalized_trace.max_intensity,
        local_to_global_max_ratio=local_ratio,
    )


def integration_with_trace_evidence(
    integration: IntegrationResult,
    rt_values: object,
    intensity_values: object,
    *,
    baseline_integration_method: str = "asls",
) -> IntegrationResult:
    """Enrich an existing integration with owner-computed baseline/morphology facts."""

    enriched, _rt, _morphology_trace = _integration_and_morphology_trace(
        integration,
        rt_values,
        intensity_values,
        baseline_integration_method=baseline_integration_method,
    )
    return enriched


def peak_trace_evidence(
    integration: IntegrationResult,
    rt_values: object,
    intensity_values: object,
    *,
    baseline_integration_method: str = "asls",
) -> PeakTraceEvidenceFacts:
    """Mint selected-peak area, shape, and window facts from one acquired trace.

    The Gaussian15 positive-AsLS residual trace is computed once and reused for
    morphology area and own-max shape/window projections.  The selected peak
    and its compatibility area remain caller-owned inputs.
    """

    enriched, rt, morphology_trace = _integration_and_morphology_trace(
        integration,
        rt_values,
        intensity_values,
        baseline_integration_method=baseline_integration_method,
    )
    normalized_shape = _normalized_precomputed_trace(rt, morphology_trace)
    window = _trace_window_evidence_facts(
        tuple(float(value) for value in rt),
        normalized_shape,
        query_apex_rt_min=integration.rt_apex_min,
        half_window_min=DEFAULT_LOCAL_HALF_WINDOW_MIN,
        report_global_trace_apex_delta=True,
    )
    return PeakTraceEvidenceFacts(
        integration=enriched,
        normalized_shape_trace=normalized_shape,
        window=window,
        smoothing_count=1 if morphology_trace is not None else 0,
        integration_count=1,
    )


def _integration_and_morphology_trace(
    integration: IntegrationResult,
    rt_values: object,
    intensity_values: object,
    *,
    baseline_integration_method: str,
) -> tuple[IntegrationResult, np.ndarray, np.ndarray | None]:
    if not isinstance(integration, IntegrationResult):
        raise TypeError("integration must be an IntegrationResult")
    rt, intensity = validate_ms1_trace_arrays(rt_values, intensity_values)
    if len(rt) < 2:
        raise ValueError("trace arrays must have matching length >= 2")
    left_index, right_index = _bounded_indices_for_rt_window(
        rt,
        integration.rt_left_min,
        integration.rt_right_min,
    )
    baseline_values = (
        asls_baseline(intensity) if baseline_integration_method == "asls" else None
    )
    baseline = integrate_with_baseline(
        intensity,
        rt,
        left_index,
        right_index,
        baseline_method=baseline_integration_method,
        baseline_values=baseline_values,
    )
    morphology_trace = (
        gaussian15_positive_asls_residual_trace(intensity, baseline_values)
        if baseline_values is not None
        else None
    )
    morphology_area = (
        positive_residual_area(rt, morphology_trace, left_index, right_index)
        if morphology_trace is not None
        else None
    )
    enriched = replace(
        integration,
        raw_scan_indices=tuple(range(left_index, right_index)),
        area_baseline_corrected=baseline.area_baseline_corrected,
        area_uncertainty=baseline.area_uncertainty,
        area_uncertainty_formula_version=baseline.area_uncertainty_formula_version,
        baseline_residual_mad=baseline.baseline_residual_mad,
        area_uncertainty_noise_source=baseline.area_uncertainty_noise_source,
        baseline_type=baseline.baseline_type,
        baseline_score=baseline.baseline_score,
        area_ms1_morphology=morphology_area,
        ms1_morphology_area_source=(
            MS1_MORPHOLOGY_AREA_SOURCE if morphology_trace is not None else ""
        ),
        ms1_morphology_trace_method=(
            MS1_MORPHOLOGY_TRACE_METHOD if morphology_trace is not None else ""
        ),
        ms1_morphology_trace_window_points=(
            DEFAULT_GAUSSIAN15_WINDOW_POINTS if morphology_trace is not None else None
        ),
        ms1_morphology_trace_effective_points=(
            morphology_trace_effective_points(
                len(morphology_trace),
                method=MS1_MORPHOLOGY_TRACE_METHOD,
                window_points=DEFAULT_GAUSSIAN15_WINDOW_POINTS,
            )
            if morphology_trace is not None
            else None
        ),
    )
    return enriched, rt, morphology_trace


def _normalized_precomputed_trace(
    rt_values: np.ndarray,
    intensity_values: np.ndarray | None,
) -> OwnMaxTrace:
    if intensity_values is None:
        return OwnMaxTrace("inconclusive", "gaussian15_trace_unavailable")
    rt = np.asarray(rt_values, dtype=float)
    intensity = np.asarray(intensity_values, dtype=float)
    if rt.ndim != 1 or intensity.ndim != 1 or len(rt) != len(intensity):
        return OwnMaxTrace("inconclusive", "length_mismatch")
    mask = np.isfinite(rt) & np.isfinite(intensity)
    if int(np.sum(mask)) < 3:
        return OwnMaxTrace("inconclusive", "too_few_points")
    rt = rt[mask]
    intensity = intensity[mask]
    order = np.argsort(rt)
    rt = rt[order]
    intensity = intensity[order]
    maximum = float(np.max(intensity)) if intensity.size else 0.0
    if maximum <= 0:
        return OwnMaxTrace("inconclusive", "non_positive_signal")
    return OwnMaxTrace(
        "ok",
        "gaussian15_positive_asls_residual_own_max",
        tuple(float(value) for value in rt),
        tuple(float(value) for value in intensity / maximum),
        maximum,
    )


def build_candidate_evidence_facts(
    candidate: Any,
    ctx: Any,
    *,
    role: str = "",
    istd_pair: str = "",
    paired_istd_anchor_rt_min: float | None = None,
) -> CandidateEvidenceFacts:
    quality_penalty, _ = candidate_quality_penalty(candidate)
    return CandidateEvidenceFacts(
        facts_version=FACTS_VERSION,
        candidate_id=_candidate_id(candidate),
        abundance=_candidate_abundance(candidate),
        area=_candidate_area(candidate),
        height=_candidate_height(candidate),
        trace=_trace_facts(candidate, ctx),
        chemical=_chemical_facts(candidate, ctx),
        rt=_rt_facts(
            candidate,
            ctx,
            role=role,
            istd_pair=istd_pair,
            paired_istd_anchor_rt_min=paired_istd_anchor_rt_min,
        ),
        boundary=_boundary_facts(candidate),
        quality_penalty=quality_penalty,
        selection_quality_penalty=candidate_selection_quality_penalty(candidate),
    )


def decision_semantics_from_candidate_facts(
    facts: CandidateEvidenceFacts,
    *,
    count_no_ms2_as_detected: bool = False,
) -> EvidenceDecisionSemantics:
    signals = _signal_set_from_facts(
        facts,
        count_no_ms2_as_detected=count_no_ms2_as_detected,
    )
    return decision_semantics_from_signal_set(signals)


def projected_confidence_from_candidate_facts(
    facts: CandidateEvidenceFacts,
    semantics: EvidenceDecisionSemantics | None = None,
) -> str:
    if semantics is None:
        semantics = decision_semantics_from_candidate_facts(facts)
    if semantics.decision_class in {"excluded", "not_counted", "ambiguous"}:
        return "VERY_LOW"
    if semantics.conflict_reasons:
        return "LOW"
    if semantics.review_reasons:
        return "MEDIUM"
    if semantics.decision_class == "accepted":
        return "HIGH"
    return "LOW"


def projected_reason_from_candidate_facts(
    facts: CandidateEvidenceFacts,
    semantics: EvidenceDecisionSemantics | None = None,
) -> str:
    if semantics is None:
        semantics = decision_semantics_from_candidate_facts(facts)
    details = (
        *semantics.support_reasons,
        *semantics.conflict_reasons,
        *semantics.review_reasons,
        *semantics.not_counted_reasons,
        *semantics.exclusion_reasons,
        *semantics.ambiguity_reasons,
    )
    if not details:
        return f"decision: {semantics.decision_class}"
    return f"decision: {semantics.decision_class}; evidence: {'; '.join(details)}"


def _signal_set_from_facts(
    facts: CandidateEvidenceFacts,
    *,
    count_no_ms2_as_detected: bool,
) -> EvidenceSignalSet:
    support: list[str] = []
    concerns: list[str] = []
    caps: list[str] = []

    chemical = facts.chemical
    if not chemical.neutral_loss_required:
        support.append("no_nl_required")
    elif chemical.ms2_present and chemical.nl_match:
        support.append("strict_nl_ok")
        if chemical.ms2_trace_strength == "strong":
            support.append("ms2_trace_strong")
        elif chemical.ms2_trace_strength == "moderate":
            support.append("ms2_trace_moderate")
        elif chemical.ms2_trace_strength == "weak":
            concerns.append("ms2_trace_weak")
    elif chemical.ms2_present and chemical.nl_match is False:
        concerns.append("nl_fail")
        caps.append("nl_fail_cap")
    else:
        concerns.append("no_ms2")
        caps.append("no_ms2_cap")

    rt_support_emitted = False
    if facts.rt.rt_prior_status == "close":
        support.append("rt_prior_close")
        rt_support_emitted = True
    elif facts.rt.rt_prior_status == "borderline":
        concerns.append("rt_prior_borderline")
    elif facts.rt.rt_prior_status == "far":
        concerns.append("rt_prior_far")

    if facts.rt.paired_istd_status == "close":
        if not rt_support_emitted:
            support.append("paired_istd_rt_close")
    elif facts.rt.paired_istd_status == "far":
        concerns.append("anchor_mismatch")
        caps.append("anchor_mismatch_cap")

    if facts.rt.window_status == "borderline":
        concerns.append("rt_centrality_borderline")
    elif facts.rt.window_status == "outside":
        concerns.append("rt_centrality_poor")
        if facts.rt.rt_prior_status != "close":
            caps.append("rt_window_cap")

    if facts.trace.local_sn_quality == "strong":
        support.append("local_sn_strong")
    elif facts.trace.local_sn_quality == "borderline":
        concerns.append("local_sn_borderline")
    elif facts.trace.local_sn_quality == "poor":
        concerns.append("local_sn_poor")

    shape_quality = _worst_quality(
        facts.trace.symmetry_quality,
        facts.trace.width_quality,
    )
    if shape_quality == "clean":
        support.append("shape_clean")
    elif shape_quality == "borderline":
        concerns.append("shape_borderline")
    elif shape_quality == "poor":
        concerns.append("shape_poor")

    if facts.trace.noise_shape_quality == "borderline":
        concerns.append("noise_shape_borderline")
    elif facts.trace.noise_shape_quality == "poor":
        concerns.append("noise_shape_poor")

    if not facts.trace.hard_quality_flags:
        support.append("trace_clean")
    for flag in facts.trace.quality_flags:
        if flag in {"low_scan_support", "low_trace_continuity", "poor_edge_recovery"}:
            concerns.append(flag)
    if facts.trace.hard_quality_flags:
        concerns.append("hard_quality_flag")
        caps.append("hard_quality_flag_cap")

    if facts.boundary.cwt_same_apex_observed and (
        not chemical.neutral_loss_required or bool(chemical.nl_match)
    ):
        support.append("cwt_same_apex_support")

    ms2_present_for_semantics = (
        facts.chemical.ms2_present if facts.chemical.neutral_loss_required else None
    )
    nl_match_for_semantics = (
        facts.chemical.nl_match
        if facts.chemical.neutral_loss_required and facts.chemical.ms2_present
        else None
    )
    return EvidenceSignalSet(
        support_labels=tuple(dict.fromkeys(support)),
        concern_labels=tuple(dict.fromkeys(concerns)),
        proposal_sources=facts.boundary.proposal_sources,
        quality_flags=facts.trace.hard_quality_flags,
        ms2_present=ms2_present_for_semantics,
        nl_match=nl_match_for_semantics,
        cap_labels=tuple(dict.fromkeys(caps)),
        count_no_ms2_as_detected=count_no_ms2_as_detected,
    )


def _trace_facts(candidate: Any, ctx: Any) -> TraceEvidenceFacts:
    local_ratio, local_quality, baseline_method, noise_source = _local_sn_facts(ctx)
    flags = tuple(
        dict.fromkeys(str(flag) for flag in getattr(candidate, "quality_flags", ()))
    )
    return TraceEvidenceFacts(
        local_sn_ratio=local_ratio,
        local_sn_quality=local_quality,
        baseline_method=baseline_method,
        residual_mad=_optional_float(getattr(ctx, "residual_mad", None)),
        noise_source=noise_source,
        active_trace_source=str(getattr(ctx, "active_trace_source", "") or ""),
        morphology_trace_method=str(getattr(ctx, "morphology_trace_method", "") or ""),
        morphology_trace_window_points=getattr(
            ctx,
            "morphology_trace_window_points",
            None,
        ),
        symmetry_quality=_severity_quality(
            symmetry_severity(float(getattr(ctx, "half_width_ratio", math.nan)))[0],
            clean_label="clean",
        ),
        width_quality=_severity_quality(
            peak_width_severity(getattr(ctx, "fwhm_ratio", None))[0],
            clean_label="clean",
        ),
        noise_shape_quality=_severity_quality(
            noise_shape_severity(np.asarray(getattr(ctx, "intensity_array", [])))[0],
            clean_label="clean",
        ),
        scan_count=getattr(candidate, "region_scan_count", None),
        duration_min=getattr(candidate, "region_duration_min", None),
        edge_ratio=getattr(candidate, "region_edge_ratio", None),
        continuity=getattr(candidate, "region_trace_continuity", None),
        quality_flags=flags,
        hard_quality_flags=hard_quality_flags(flags),
    )


def _local_sn_facts(ctx: Any) -> tuple[float | None, EvidenceQuality, str, str]:
    intensity = np.asarray(getattr(ctx, "intensity_array", []), dtype=float)
    apex_index = int(getattr(ctx, "apex_index", -1))
    baseline = getattr(ctx, "baseline_array", None)
    residual_mad = getattr(ctx, "residual_mad", None)
    active_source = str(getattr(ctx, "active_trace_source", "") or "")
    baseline_method = ""
    noise_source = ""
    if baseline is not None:
        baseline = np.asarray(baseline, dtype=float)
        baseline_method = "asls"
        if active_source and active_source != "raw":
            baseline_method = active_source
    if residual_mad is not None:
        noise_source = "residual_mad"
    if (
        intensity.ndim == 1
        and baseline is not None
        and len(intensity) == len(baseline)
        and 0 <= apex_index < len(intensity)
        and residual_mad is not None
    ):
        mad = float(residual_mad)
        if mad <= 0:
            return None, "strong", baseline_method, noise_source
        ratio = float((intensity[apex_index] - baseline[apex_index]) / mad)
        quality = _sn_quality(ratio, bool(getattr(ctx, "dirty_matrix", False)))
        return ratio, quality, baseline_method, noise_source
    if intensity.ndim == 1 and 0 <= apex_index < len(intensity):
        computed_baseline, computed_mad = compute_local_sn_cache(intensity)
        if computed_baseline is not None and computed_mad is not None:
            baseline_method = "asls"
            noise_source = "residual_mad"
            if computed_mad <= 0:
                return None, "strong", baseline_method, noise_source
            ratio = float(
                (intensity[apex_index] - computed_baseline[apex_index]) / computed_mad
            )
            quality = _sn_quality(ratio, bool(getattr(ctx, "dirty_matrix", False)))
            return ratio, quality, baseline_method, noise_source
    return None, "poor", baseline_method, noise_source


def _chemical_facts(candidate: Any, ctx: Any) -> ChemicalEvidenceFacts:
    ms2_present = bool(getattr(ctx, "ms2_present", False))
    nl_match = bool(getattr(ctx, "nl_match", False))
    neutral_loss_required = bool(getattr(ctx, "neutral_loss_required", True))
    best_scan_rt = getattr(ctx, "best_ms2_scan_rt_min", None)
    selected_rt = float(getattr(candidate, "selection_apex_rt", math.nan))
    return ChemicalEvidenceFacts(
        neutral_loss_required=neutral_loss_required,
        ms2_present=ms2_present,
        nl_match=nl_match,
        nl_status=_nl_status(neutral_loss_required, ms2_present, nl_match),
        ms2_trace_strength=str(getattr(ctx, "ms2_trace_strength", "") or ""),
        loss_ppm=_optional_float(getattr(ctx, "best_loss_ppm", None)),
        ms2_rt_delta_min=(
            abs(selected_rt - float(best_scan_rt))
            if best_scan_rt is not None and _is_finite(selected_rt)
            else None
        ),
        trigger_scan_count=getattr(ctx, "trigger_scan_count", None),
        strict_nl_scan_count=getattr(ctx, "strict_nl_scan_count", None),
        ms1_peak_group_source=str(getattr(ctx, "ms1_peak_group_source", "") or ""),
        ms1_peak_group_rt_min=_optional_float(
            getattr(ctx, "ms1_peak_group_rt_min", None)
        ),
        ms1_peak_group_rt_max=_optional_float(
            getattr(ctx, "ms1_peak_group_rt_max", None)
        ),
        ms1_peak_group_trigger_scan_count=getattr(
            ctx,
            "ms1_peak_group_trigger_scan_count",
            None,
        ),
        ms1_peak_group_strict_nl_scan_count=getattr(
            ctx,
            "ms1_peak_group_strict_nl_scan_count",
            None,
        ),
        ms1_peak_group_strict_nl_event_count=getattr(
            ctx,
            "ms1_peak_group_strict_nl_event_count",
            None,
        ),
        outside_ms1_peak_group_trigger_scan_count=getattr(
            ctx,
            "outside_ms1_peak_group_trigger_scan_count",
            None,
        ),
        outside_ms1_peak_group_strict_nl_scan_count=getattr(
            ctx,
            "outside_ms1_peak_group_strict_nl_scan_count",
            None,
        ),
        alignment_source=str(getattr(ctx, "ms2_alignment_source", "") or ""),
        product_absence_reason=str(
            getattr(ctx, "diagnostic_product_absence_reason", "") or ""
        ),
        acquisition_opportunity=(
            "not_required"
            if not neutral_loss_required
            else "observed"
            if ms2_present
            else "not_observed"
        ),
    )


def _rt_facts(
    candidate: Any,
    ctx: Any,
    *,
    role: str,
    istd_pair: str,
    paired_istd_anchor_rt_min: float | None,
) -> RtEvidenceFacts:
    selected_rt = float(getattr(candidate, "selection_apex_rt"))
    rt_prior = getattr(ctx, "rt_prior", None)
    rt_prior_delta = (
        round(abs(selected_rt - float(rt_prior)), 10)
        if rt_prior is not None and _is_finite(float(rt_prior))
        else None
    )
    paired_istd_delta = (
        round(abs(selected_rt - float(paired_istd_anchor_rt_min)), 10)
        if paired_istd_anchor_rt_min is not None
        and _is_finite(float(paired_istd_anchor_rt_min))
        else None
    )
    rt_severity = rt_prior_severity(
        selected_rt,
        rt_prior,
        getattr(ctx, "rt_prior_sigma", None),
    )[0]
    centrality_severity = rt_centrality_severity(
        selected_rt,
        float(getattr(ctx, "rt_min", math.nan)),
        float(getattr(ctx, "rt_max", math.nan)),
    )[0]
    return RtEvidenceFacts(
        selected_apex_rt_min=selected_rt,
        rt_min=_optional_float(getattr(ctx, "rt_min", None)),
        rt_max=_optional_float(getattr(ctx, "rt_max", None)),
        rt_prior_min=_optional_float(rt_prior),
        rt_prior_sigma_min=_optional_float(getattr(ctx, "rt_prior_sigma", None)),
        rt_prior_delta_min=rt_prior_delta,
        rt_prior_status=_rt_prior_status(rt_prior, rt_severity),
        window_status=_window_status(centrality_severity),
        paired_istd_anchor_rt_min=paired_istd_anchor_rt_min,
        paired_istd_delta_min=paired_istd_delta,
        paired_istd_status=_paired_istd_status(
            role=role,
            delta_min=paired_istd_delta,
        ),
        role=role,
        istd_pair=istd_pair,
        prefer_rt_prior_tiebreak=bool(getattr(ctx, "prefer_rt_prior_tiebreak", False)),
    )


def _boundary_facts(candidate: Any) -> BoundaryEvidenceFacts:
    proposal_sources = tuple(
        dict.fromkeys(
            str(source) for source in getattr(candidate, "proposal_sources", ())
        )
    )
    cwt_best_scale = getattr(candidate, "cwt_best_scale", None)
    cwt_ridge_persistence = getattr(candidate, "cwt_ridge_persistence", None)
    return BoundaryEvidenceFacts(
        proposal_sources=proposal_sources,
        cwt_same_apex_observed=(
            cwt_best_scale is not None
            or cwt_ridge_persistence is not None
            or "centwave_cwt" in proposal_sources
        ),
        cwt_best_scale=cwt_best_scale,
        cwt_ridge_persistence=cwt_ridge_persistence,
        chrom_peak_segment_present="chrom_peak_segment" in proposal_sources,
    )


def _bounded_indices_for_rt_window(
    rt_values: np.ndarray,
    peak_start_rt: float,
    peak_end_rt: float,
) -> tuple[int, int]:
    if not np.isfinite(peak_start_rt) or not np.isfinite(peak_end_rt):
        raise ValueError("peak RT bounds must be finite")
    left_rt = min(float(peak_start_rt), float(peak_end_rt))
    right_rt = max(float(peak_start_rt), float(peak_end_rt))
    if right_rt <= left_rt:
        raise ValueError("peak RT window must have positive width")
    left_index = int(np.searchsorted(rt_values, left_rt, side="left"))
    right_index = int(np.searchsorted(rt_values, right_rt, side="right"))
    if right_index - left_index < 2:
        left_index = int(np.argmin(np.abs(rt_values - left_rt)))
        right_index = int(np.argmin(np.abs(rt_values - right_rt))) + 1
    return bounded_trace_interval(left_index, right_index, len(rt_values))


def _candidate_id(candidate: Any) -> str:
    sources = ";".join(
        str(source) for source in getattr(candidate, "proposal_sources", ())
    )
    selected_rt = float(getattr(candidate, "selection_apex_rt"))
    peak = getattr(candidate, "peak", None)
    peak_start = getattr(peak, "peak_start", selected_rt)
    peak_end = getattr(peak, "peak_end", selected_rt)
    return "|".join(
        (
            sources,
            f"{selected_rt:.5f}",
            f"{float(peak_start):.5f}",
            f"{float(peak_end):.5f}",
        )
    )


def _candidate_abundance(candidate: Any) -> float:
    area = _candidate_area(candidate)
    if area > 0:
        return area
    return _candidate_height(candidate)


def _candidate_area(candidate: Any) -> float:
    peak = getattr(candidate, "peak", None)
    value_source = getattr(peak, "area", None)
    if value_source is None:
        value_source = getattr(candidate, "peak_area", None)
    if value_source is None:
        value_source = getattr(candidate, "area", None)
    if value_source is None:
        return 0.0
    try:
        value = float(value_source)
    except (TypeError, ValueError):
        return 0.0
    return value if _is_finite(value) else 0.0


def _candidate_height(candidate: Any) -> float:
    try:
        value = float(getattr(candidate, "selection_apex_intensity"))
    except (TypeError, ValueError):
        return 0.0
    return value if _is_finite(value) else 0.0


def _sn_quality(ratio: float, dirty_matrix: bool) -> EvidenceQuality:
    hard = 1.3 if dirty_matrix else 2.0
    soft = 2.0 if dirty_matrix else 3.0
    if ratio < hard:
        return "poor"
    if ratio < soft:
        return "borderline"
    return "strong"


def _severity_quality(
    severity: int,
    *,
    clean_label: EvidenceQuality,
) -> EvidenceQuality:
    if severity <= 0:
        return clean_label
    if severity == 1:
        return "borderline"
    return "poor"


def _rt_prior_status(rt_prior: float | None, severity: int) -> RtEvidenceStatus:
    if rt_prior is None:
        return "missing"
    if severity <= 0:
        return "close"
    if severity == 1:
        return "borderline"
    return "far"


def _window_status(severity: int) -> RtWindowStatus:
    if severity <= 0:
        return "inside"
    if severity == 1:
        return "borderline"
    return "outside"


def _paired_istd_status(
    *,
    role: str,
    delta_min: float | None,
) -> RtEvidenceStatus:
    if role != "Analyte" or delta_min is None:
        return "missing"
    if delta_min <= PAIRED_ISTD_MAX_DELTA_MIN:
        return "close"
    return "far"


def _nl_status(
    neutral_loss_required: bool,
    ms2_present: bool,
    nl_match: bool,
) -> str:
    if not neutral_loss_required:
        return "NOT_REQUIRED"
    if ms2_present and nl_match:
        return "OK"
    if ms2_present and not nl_match:
        return "NL_FAIL"
    return "NO_MS2"


def _worst_quality(*values: EvidenceQuality) -> EvidenceQuality:
    rank = {"strong": 0, "clean": 0, "unknown": 1, "borderline": 2, "poor": 3}
    return max(values, key=lambda value: rank[value])


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if _is_finite(number) else None


def _is_finite(value: float) -> bool:
    return math.isfinite(float(value))
