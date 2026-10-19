"""Backfill extraction result and peak fact contracts."""

from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from typing import Protocol, TypeAlias

import numpy as np
from numpy.typing import NDArray

from xic_extractor.alignment.backfill.evaluation_coverage import (
    BackfillAttemptTerminal,
    BackfillCandidatePhase,
    BackfillEvaluationCoverageBatch,
    BackfillTerminalReason,
)
from xic_extractor.alignment.backfill.request_plan import (
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


@dataclass(frozen=True, slots=True)
class BackfillRequestReuseObservation:
    sample_stem: str
    validation_mode: BackfillValidationMode
    logical_request_count: int
    canonical_request_class_count: int


@dataclass(frozen=True, slots=True)
class BackfillExtractionResult:
    request_identity: BackfillRequestIdentity
    trace: XICTrace


@dataclass(frozen=True, slots=True)
class BackfillLogicalExtractionResult:
    logical_request: BackfillLogicalRequestMember
    canonical_result: BackfillExtractionResult

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
