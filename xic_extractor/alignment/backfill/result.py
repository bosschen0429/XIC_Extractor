"""Backfill extraction result and peak fact contracts."""

from __future__ import annotations

import math
from dataclasses import InitVar, dataclass, field
from typing import Protocol, TypeAlias

import numpy as np
from numpy.typing import NDArray

from xic_extractor.alignment.backfill.evaluation_coverage import (
    BackfillAttemptTerminal,
    BackfillCandidatePhase,
    BackfillEvaluationCoverageBatch,
    BackfillTerminalReason,
    mint_backfill_selection_subject_id,
)
from xic_extractor.alignment.backfill.request_plan import (
    BACKFILL_VALIDATION_MODES,
    BackfillLogicalRequestMember,
    BackfillRequestIdentity,
    BackfillRequestItem,
    BackfillValidationMode,
)
from xic_extractor.alignment.matrix import AlignedCell
from xic_extractor.peak_detection.evidence_facts import (
    PeakTraceEvidenceFacts,
    TraceWindowEvidenceFacts,
)
from xic_extractor.peak_detection.integration_result import IntegrationResult
from xic_extractor.peak_detection.physical_peak_evaluation import (
    validate_bounded_physical_peak_evaluation,
)
from xic_extractor.peak_detection.physical_peak_models import (
    PhysicalPeakEvaluation,
    PreparedSignalResult,
)
from xic_extractor.xic_models import XICTrace

_RequestItem = BackfillRequestItem
# One extraction chunk's prepared signals, keyed by the identity of the trace
# object request equivalence fanned out.  The cache holds the trace itself so a
# recycled id() can never alias a different trace.
_ChunkPreparedSignalCache: TypeAlias = dict[int, tuple[XICTrace, PreparedSignalResult]]
_RequestGroupKey = tuple[str, int | float, int | float]
_BACKFILL_PEAK_FACT_OBSERVATION_MINT_TOKEN = object()


@dataclass(frozen=True, slots=True)

class _ExtractionRequestItem:
    item: _RequestItem
    logical_request: BackfillLogicalRequestMember | None = None
    request_identity: BackfillRequestIdentity | None = None

    def __post_init__(self) -> None:
        if (self.logical_request is None) != (self.request_identity is None):
            raise ValueError(
                "logical request and canonical identity must be supplied together"
            )
        if self.logical_request is not None and self.logical_request.item != self.item:
            raise ValueError("logical request is not bound to its extraction item")


_WindowedRequestItem = tuple[_ExtractionRequestItem, tuple[int, int]]


class BackfillSource(Protocol):
    def extract_xic(
        self,
        mz: float,
        rt_min: float,
        rt_max: float,
        ppm_tol: float,
    ) -> tuple[NDArray[np.float64], NDArray[np.float64]]: ...


@dataclass(frozen=True)
class BackfillCandidateAuditRow:
    feature_family_id: str
    group_hypothesis_id: str
    public_family_id: str
    sample_stem: str
    candidate_index: int
    candidate_phase: str
    selected_for_output: bool
    candidate_status: str
    candidate_outcome: str
    trace_quality: str
    area: float | None
    apex_rt: float | None
    peak_start_rt: float | None
    peak_end_rt: float | None
    rt_delta_sec: float | None
    backfill_seed_mz: float | None
    backfill_seed_rt: float | None
    backfill_request_rt_min: float | None
    backfill_request_rt_max: float | None
    backfill_request_ppm: float | None
    reason: str
    selection_note: str


@dataclass(frozen=True)
class BackfillResult:
    cells: tuple[AlignedCell, ...]
    candidate_audit_rows: tuple[BackfillCandidateAuditRow, ...]
    request_reuse_observations: tuple[
        BackfillRequestReuseObservation,
        ...,
    ] = ()
    peak_fact_observations: tuple[BackfillPeakFactObservation, ...] = ()
    peak_fact_unsealed_evaluation_keys: tuple[str, ...] = ()
    evaluation_coverage: BackfillEvaluationCoverageBatch | None = None
    evaluation_coverage_terminals: tuple[BackfillAttemptTerminal, ...] = ()

    def __post_init__(self) -> None:
        if self.evaluation_coverage is not None and self.evaluation_coverage_terminals:
            raise ValueError(
                "Backfill result cannot carry a batch and loose coverage terminals"
            )


@dataclass(frozen=True, slots=True)
class BackfillPeakFactReceipt:
    """Bounded post-decision projection of shared peak evidence."""

    integration: IntegrationResult
    physical_evaluation: PhysicalPeakEvaluation | None
    normalized_shape_status: str
    normalized_shape_reason: str
    normalized_shape_point_count: int
    normalized_shape_max_intensity: float | None
    window: TraceWindowEvidenceFacts
    smoothing_count: int
    integration_count: int

    def __post_init__(self) -> None:
        if self.integration.raw_scan_indices:
            raise ValueError("peak fact receipt must not retain raw scan indices")
        counts = (
            self.normalized_shape_point_count,
            self.smoothing_count,
            self.integration_count,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in counts
        ):
            raise ValueError("peak fact receipt counts must be non-negative integers")
        if self.physical_evaluation is not None:
            if self.physical_evaluation.facts is None:
                raise ValueError("available peak fact receipt requires physical facts")
            if (
                self.physical_evaluation.facts.area_counts_seconds
                != self.integration.area_ms1_morphology
            ):
                raise ValueError("peak fact receipt crosses canonical area")


@dataclass(frozen=True, slots=True)
class BackfillPeakFactObservation:
    logical_request: BackfillLogicalRequestMember
    request_identity: BackfillRequestIdentity
    candidate_index: int
    candidate_phase: str
    selected_for_output: bool
    cell: AlignedCell
    facts: BackfillPeakFactReceipt | None
    physical_evaluation: PhysicalPeakEvaluation | None
    trace_rt_start_min: float | None
    trace_rt_end_min: float | None
    fact_elapsed_seconds: float | None
    trace_evaluated: bool
    _mint_token: InitVar[object] = field(kw_only=True)

    @property
    def evidence_authority(self) -> str:
        return "evidence_only_no_write_authority"

    @property
    def work_unit_key(self) -> str:
        return (
            f"{self.request_identity.request_identity_id}:"
            f"{self.candidate_index}:{self.candidate_phase}"
        )

    @property
    def prepared_signal_id(self) -> str | None:
        return (
            self.physical_evaluation.prepared.prepared_signal_id
            if self.physical_evaluation is not None
            else None
        )

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _BACKFILL_PEAK_FACT_OBSERVATION_MINT_TOKEN:
            raise ValueError("peak fact observation must be minted by Owner Backfill")
        _validate_backfill_peak_fact_observation(self)


def validate_backfill_peak_fact_observation(
    observation: BackfillPeakFactObservation,
) -> None:
    """Re-run every bounded live-observation invariant without minting it."""

    if type(observation) is not BackfillPeakFactObservation:
        raise TypeError("observation must be an Owner Backfill-minted value")
    _validate_backfill_peak_fact_observation(observation)


def _validate_backfill_peak_fact_observation(
    observation: BackfillPeakFactObservation,
) -> None:
    if not isinstance(
        observation.logical_request,
        BackfillLogicalRequestMember,
    ):
        raise TypeError("logical_request must be owner-minted")
    if not isinstance(observation.request_identity, BackfillRequestIdentity):
        raise TypeError("request_identity must be owner-minted")
    if not isinstance(observation.cell, AlignedCell):
        raise TypeError("cell must be an AlignedCell")
    if observation.facts is not None and not isinstance(
        observation.facts,
        BackfillPeakFactReceipt,
    ):
        raise TypeError("facts must be an Owner Backfill peak fact receipt")
    observed_binding = (
        observation.logical_request.item.sample_stem,
        observation.logical_request.group_hypothesis_id,
        observation.request_identity.sample_stem,
        observation.request_identity.request,
    )
    expected_binding = (
        observation.cell.sample_stem,
        observation.cell.group_hypothesis_id,
        observation.cell.sample_stem,
        observation.logical_request.item.request,
    )
    if observed_binding != expected_binding:
        raise ValueError("peak fact observation crosses sealed identity")
    if (
        isinstance(observation.candidate_index, bool)
        or not isinstance(observation.candidate_index, int)
        or observation.candidate_index < 0
    ):
        raise ValueError("candidate_index must be a non-negative integer")
    if (
        not isinstance(observation.candidate_phase, str)
        or not observation.candidate_phase
        or observation.candidate_phase != observation.candidate_phase.strip()
    ):
        raise ValueError("candidate_phase must be owner-normalized text")
    if not isinstance(observation.selected_for_output, bool):
        raise ValueError("selected_for_output must be boolean")
    if not isinstance(observation.trace_evaluated, bool):
        raise ValueError("trace_evaluated must be boolean")
    physical_evaluation = observation.physical_evaluation
    if physical_evaluation is not None and not isinstance(
        physical_evaluation,
        PhysicalPeakEvaluation,
    ):
        raise TypeError("physical_evaluation must be owner-minted when available")
    if physical_evaluation is not None:
        expected_selection_subject_id = mint_backfill_selection_subject_id(
            group_hypothesis_id=(
                observation.logical_request.item.feature.group_hypothesis_id
            ),
            sample_stem=observation.logical_request.item.sample_stem,
            request=observation.logical_request.item.request,
            candidate_index=observation.candidate_index,
            candidate_phase=observation.candidate_phase,
            request_identity=observation.request_identity,
        )
        question = physical_evaluation.question
        if question.selection_subject_id != expected_selection_subject_id:
            raise ValueError("peak fact observation crosses physical selection subject")
        if not _physical_query_matches_owner_request(
            physical_evaluation,
            preferred_rt=observation.logical_request.item.preferred_rt,
        ):
            raise ValueError("peak fact observation crosses physical query RT")
        validate_bounded_physical_peak_evaluation(physical_evaluation)
    if observation.facts is not None and observation.facts.physical_evaluation != (
        physical_evaluation
    ):
        raise ValueError("peak fact observation crosses physical evaluation")
    if observation.fact_elapsed_seconds is not None and (
        isinstance(observation.fact_elapsed_seconds, bool)
        or not isinstance(observation.fact_elapsed_seconds, (int, float))
        or not math.isfinite(observation.fact_elapsed_seconds)
        or observation.fact_elapsed_seconds <= 0
    ):
        raise ValueError("fact elapsed observation must be finite and positive")


def _physical_query_matches_owner_request(
    evaluation: PhysicalPeakEvaluation,
    *,
    preferred_rt: float,
) -> bool:
    question = evaluation.question
    if question.query_rt_min == preferred_rt:
        return True
    segments = evaluation.segment_universe.segments
    if len(segments) != 1:
        return False
    segment = segments[0]
    return (
        not segment.left_rt_min <= preferred_rt <= segment.right_rt_min
        and question.query_rt_min == segment.apex_rt_min
        and evaluation.answer.physical_segment_id == segment.physical_segment_id
        and evaluation.state == "available"
    )


@dataclass(frozen=True, slots=True)
class BackfillRequestReuseObservation:
    sample_stem: str
    validation_mode: BackfillValidationMode
    logical_request_count: int
    canonical_request_class_count: int

    def __post_init__(self) -> None:
        if not self.sample_stem or self.sample_stem != self.sample_stem.strip():
            raise ValueError("sample_stem must be owner-normalized text")
        if self.validation_mode not in BACKFILL_VALIDATION_MODES:
            raise ValueError("unsupported owner Backfill validation mode")
        for field_name in ("logical_request_count", "canonical_request_class_count"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        if self.canonical_request_class_count > self.logical_request_count:
            raise ValueError("canonical classes cannot exceed logical requests")


@dataclass(frozen=True, slots=True)
class BackfillExtractionResult:
    request_identity: BackfillRequestIdentity
    trace: XICTrace

    def __post_init__(self) -> None:
        if not isinstance(self.request_identity, BackfillRequestIdentity):
            raise TypeError("request_identity must be owner-minted")
        if not isinstance(self.trace, XICTrace):
            raise TypeError("trace must be an XICTrace")


@dataclass(frozen=True, slots=True)
class BackfillLogicalExtractionResult:
    logical_request: BackfillLogicalRequestMember
    canonical_result: BackfillExtractionResult

    def __post_init__(self) -> None:
        if not isinstance(
            self.logical_request,
            BackfillLogicalRequestMember,
        ):
            raise TypeError("logical_request must be owner-minted")
        if not isinstance(self.canonical_result, BackfillExtractionResult):
            raise TypeError("canonical_result must be an owner extraction result")
        identity = self.canonical_result.request_identity
        if self.logical_request.item.sample_stem != identity.sample_stem:
            raise ValueError("logical result crosses canonical samples")
        if self.logical_request.item.request != identity.request:
            raise ValueError("logical result crosses canonical request semantics")

    @property
    def trace(self) -> XICTrace:
        return self.canonical_result.trace


@dataclass(frozen=True)
class _BackfillCandidateRecord:
    cell: AlignedCell
    candidate_index: int
    candidate_phase: str
    trace_evaluated: bool = False
    peak_facts: BackfillPeakFactReceipt | None = None
    peak_fact_elapsed_seconds: float | None = None
    physical_evaluation: PhysicalPeakEvaluation | None = None
    trace_rt_start_min: float | None = None
    trace_rt_end_min: float | None = None
    logical_request: BackfillLogicalRequestMember | None = None
    request_identity: BackfillRequestIdentity | None = None


@dataclass(frozen=True, slots=True)
class _BackfillCoverageAttempt:
    position_id: str
    logical_request: BackfillLogicalRequestMember
    request_identity: BackfillRequestIdentity
    candidate_phase: BackfillCandidatePhase
    candidate_index: int
    trace_evaluated: bool
    terminal_reason: BackfillTerminalReason | None
    physical_evaluation: PhysicalPeakEvaluation | None
    cell: AlignedCell


@dataclass(frozen=True, slots=True)
class _BackfillTraceEvaluation:
    cell: AlignedCell
    peak_facts: PeakTraceEvidenceFacts | BackfillPeakFactReceipt | None
    peak_fact_elapsed_seconds: float | None
    physical_evaluation: PhysicalPeakEvaluation | None = None
    trace_rt_start_min: float | None = None
    trace_rt_end_min: float | None = None
