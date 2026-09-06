"""Closed Target/Untarget composition over existing alignment owners."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import TypeAlias

from xic_extractor.alignment.backfill.evaluation_coverage import (
    OwnerBackfillEvaluationCoverageBatch,
)
from xic_extractor.alignment.backfill.observation_repair import (
    ObservationRepairCandidate,
    ObservationRepairReceipt,
    execute_observation_repair,
)
from xic_extractor.alignment.backfill_transaction import OwnerBackfillSampleResult
from xic_extractor.alignment.cell_observations import CellObservationRef
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.association import (
    build_cross_sample_association_lineage_handoff,
)
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleNeighborhoodEdge,
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.cross_sample_reconciliation import (
    CrossSampleFinalReceipt,
    CrossSamplePostRepairModeTrackInput,
    apply_mode_track_refinement_run_for_workflow,
    close_cross_sample_without_observation_repair,
    consume_mode_track_final_attempt,
    consume_observation_repair_terminal,
)
from xic_extractor.alignment.edge_scoring import DriftLookupProtocol
from xic_extractor.alignment.mode_track.assignment import ModeTrackTrajectoryPolicy
from xic_extractor.alignment.mode_track.refinement import (
    bootstrap_mode_track_refinement_from_cid_nl_support,
    bootstrap_mode_track_refinement_from_ms1_hypotheses,
    run_post_repair_mode_track_final_attempt,
)
from xic_extractor.alignment.owner_backfill import (
    OwnerBackfillPeakFactObservation,
)
from xic_extractor.alignment.projection.target.post_cell import (
    project_target_post_cell,
)
from xic_extractor.alignment.projection.target.pre_cell import (
    project_target_pre_cell,
)
from xic_extractor.alignment.projection.untarget.post_cell import (
    project_untarget_post_cell,
)
from xic_extractor.alignment.projection.untarget.pre_cell import (
    project_untarget_pre_cell,
)
from xic_extractor.alignment.result.application_result import (
    FinalizedTargetApplicationResult,
    FinalizedUntargetApplicationResult,
    FinalizedUntargetBatchApplicationResult,
    TargetApplicationResultRequest,
    UntargetApplicationResultRequest,
    UntargetBatchApplicationResultRequest,
    UntargetedCandidateExclusion,
    finalize_alignment_result,
)
from xic_extractor.alignment.result.scientific_alignment import (
    FinalizedScientificAlignment,
    finalize_scientific_alignment,
)
from xic_extractor.execution_runtime import AttemptRuntime, AttemptTerminalOutcome
from xic_extractor.execution_runtime_finalization_models import (
    RuntimeResourceReservation,
)
from xic_extractor.execution_runtime_owner import (
    RuntimeFinalizationPersister,
    RuntimeFinalizationReceipt,
    RuntimeTelemetryProvider,
    RuntimeWorkerBatchProvider,
    execute_runtime_attempt,
)
from xic_extractor.peak_detection.capture import (
    SampleLocalCapture,
    bind_owner_backed_capture_hypothesis,
    capture_peak_hypotheses,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CidNlMethodSubjectContractError,
    cid_nl_method_subject_batch_from_request,
)
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.observations import PeakObservationRef
from xic_extractor.peak_detection.search_scope import (
    TargetSearchScope,
    UntargetedSearchScope,
    validate_scope_owner_binding,
)
from xic_extractor.peak_detection.traces import Trace
from xic_extractor.processing_identity import (
    ProcessingAttemptIdentity,
    ProcessingOwnerReferences,
    ProcessingRequestIdentity,
    compose_processing_owner_references,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity


class AlignmentWorkflowContractError(ValueError):
    """Raised when the shared workflow cannot preserve exact owner truth."""


class _WorkflowStageFailure(Exception):
    def __init__(self, stage: WorkflowStage, error: Exception) -> None:
        super().__init__(str(error))
        self.stage = stage
        self.error = error


class WorkflowStage(str, Enum):
    RUNTIME = "runtime"
    CAPTURE = "capture"
    MODE_TRACK_BOOTSTRAP = "mode_track_bootstrap"
    CROSS_SAMPLE_MT0 = "cross_sample_mt0"
    OBSERVATION_REPAIR = "observation_repair"
    CROSS_SAMPLE_REPAIR = "cross_sample_repair"
    MODE_TRACK_MT1 = "mode_track_mt1"
    CROSS_SAMPLE_FINAL = "cross_sample_final"
    SCIENTIFIC_FINAL = "scientific_final"
    PRE_CELL_PROJECTION = "pre_cell_projection"
    CELL_DECISION = "cell_decision"
    POST_CELL_PROJECTION = "post_cell_projection"
    APPLICATION_RESULT = "application_result"


@dataclass(frozen=True, slots=True)
class WorkflowRuntimeInvocation:
    runtime: AttemptRuntime
    reservations: tuple[RuntimeResourceReservation, ...]
    worker_batch_provider: RuntimeWorkerBatchProvider
    telemetry_provider: RuntimeTelemetryProvider
    persister: RuntimeFinalizationPersister

    def __post_init__(self) -> None:
        runtime_type = type(self.runtime)
        if (
            runtime_type.__module__ != "xic_extractor.execution_runtime"
            or runtime_type.__qualname__ != "AttemptRuntime"
        ):
            raise AlignmentWorkflowContractError("runtime must be an exact attempt")
        if type(self.reservations) is not tuple or any(
            type(item).__module__
            != "xic_extractor.execution_runtime_finalization_models"
            or type(item).__qualname__ != "RuntimeResourceReservation"
            for item in self.reservations
        ):
            raise AlignmentWorkflowContractError(
                "runtime reservations must be an immutable owner tuple"
            )
        if not all(
            callable(item)
            for item in (
                self.worker_batch_provider,
                self.telemetry_provider,
                self.persister,
            )
        ):
            raise AlignmentWorkflowContractError("runtime providers must be callable")


@dataclass(frozen=True, slots=True)
class CaptureWorkflowArguments:
    candidate_id: str
    trace: Trace
    owner_observation: PeakObservationRef | None = None
    owner_hypothesis: PeakHypothesis | None = None

    def __post_init__(self) -> None:
        if type(self.candidate_id) is not str or not self.candidate_id.strip():
            raise AlignmentWorkflowContractError(
                "capture candidate_id must be exact text"
            )
        if type(self.trace) is not Trace:
            raise AlignmentWorkflowContractError("capture trace must be exact")
        if (self.owner_observation is None) != (self.owner_hypothesis is None):
            raise AlignmentWorkflowContractError(
                "capture owner observation and hypothesis must propagate together"
            )
        if self.owner_observation is not None and (
            type(self.owner_observation) is not PeakObservationRef
            or type(self.owner_hypothesis) is not PeakHypothesis
        ):
            raise AlignmentWorkflowContractError(
                "capture owner binding must use exact immutable values"
            )


@dataclass(frozen=True, slots=True)
class ModeTrackWorkflowArguments:
    sample_assessments: tuple[
        tuple[ResolvedCanonicalSampleIdentity, CidNlMethodAssessmentRef], ...
    ]
    alignment_config: AlignmentConfig
    trajectory_policy: ModeTrackTrajectoryPolicy
    retain_observation_only_candidates: bool = False

    def __post_init__(self) -> None:
        if type(self.sample_assessments) is not tuple:
            raise AlignmentWorkflowContractError(
                "ModeTrack sample assessments must be an exact tuple"
            )
        if any(
            type(item) is not tuple
            or len(item) != 2
            or type(item[0]) is not ResolvedCanonicalSampleIdentity
            or type(item[1]) is not CidNlMethodAssessmentRef
            for item in self.sample_assessments
        ):
            raise AlignmentWorkflowContractError(
                "ModeTrack assessments must be exact sample/assessment pairs"
            )
        if type(self.alignment_config) is not AlignmentConfig:
            raise AlignmentWorkflowContractError("alignment_config must be exact")
        if type(self.trajectory_policy) is not ModeTrackTrajectoryPolicy:
            raise AlignmentWorkflowContractError("trajectory_policy must be exact")
        if not isinstance(self.retain_observation_only_candidates, bool):
            raise AlignmentWorkflowContractError(
                "retain_observation_only_candidates must be boolean"
            )


@dataclass(frozen=True, slots=True)
class ObservationRepairWorkflowArguments:
    coverage: OwnerBackfillEvaluationCoverageBatch
    cell_observations: tuple[CellObservationRef, ...]
    execute: Callable[[ObservationRepairCandidate], OwnerBackfillSampleResult]

    def __post_init__(self) -> None:
        if type(self.coverage) is not OwnerBackfillEvaluationCoverageBatch:
            raise AlignmentWorkflowContractError("repair coverage must be exact")
        if type(self.cell_observations) is not tuple or any(
            type(item) is not CellObservationRef for item in self.cell_observations
        ):
            raise AlignmentWorkflowContractError(
                "repair cells must be exact immutable references"
            )
        if not callable(self.execute):
            raise AlignmentWorkflowContractError("repair execute must be callable")


@dataclass(frozen=True, slots=True)
class TargetWorkflowRequest:
    scope: TargetSearchScope
    processing_request: ProcessingRequestIdentity
    processing_attempt: ProcessingAttemptIdentity
    runtime: WorkflowRuntimeInvocation

    def __post_init__(self) -> None:
        if type(self.scope) is not TargetSearchScope:
            raise AlignmentWorkflowContractError("Target request scope must be exact")
        _validate_request_fields(self)


@dataclass(frozen=True, slots=True)
class UntargetWorkflowRequest:
    scope: UntargetedSearchScope
    processing_request: ProcessingRequestIdentity
    processing_attempt: ProcessingAttemptIdentity
    runtime: WorkflowRuntimeInvocation

    def __post_init__(self) -> None:
        if type(self.scope) is not UntargetedSearchScope:
            raise AlignmentWorkflowContractError("Untarget request scope must be exact")
        _validate_request_fields(self)


@dataclass(frozen=True, slots=True)
class UntargetWorkflowCompositionRequest:
    """One Untarget request whose owner runtime has already finalized."""

    scope: UntargetedSearchScope
    processing_request: ProcessingRequestIdentity
    processing_attempt: ProcessingAttemptIdentity
    excluded_candidates: tuple[UntargetedCandidateExclusion, ...] = ()

    def __post_init__(self) -> None:
        if type(self.scope) is not UntargetedSearchScope:
            raise AlignmentWorkflowContractError("Untarget request scope must be exact")
        _validate_processing_fields(self)


def _validate_request_fields(
    request: TargetWorkflowRequest | UntargetWorkflowRequest,
) -> None:
    _validate_processing_fields(request)
    if type(request.runtime) is not WorkflowRuntimeInvocation:
        raise AlignmentWorkflowContractError("runtime invocation must be exact")
    request.runtime.__post_init__()


def _validate_processing_fields(
    request: (
        TargetWorkflowRequest
        | UntargetWorkflowRequest
        | UntargetWorkflowCompositionRequest
    ),
) -> None:
    if type(request.processing_request) is not ProcessingRequestIdentity:
        raise AlignmentWorkflowContractError("processing request must be exact")
    if type(request.processing_attempt) is not ProcessingAttemptIdentity:
        raise AlignmentWorkflowContractError("processing attempt must be exact")


WorkflowRequest: TypeAlias = TargetWorkflowRequest | UntargetWorkflowRequest


@dataclass(frozen=True, slots=True)
class _WorkflowAdapterFields:
    capture_inputs: tuple[CaptureWorkflowArguments, ...]
    mode_track: ModeTrackWorkflowArguments
    repair: ObservationRepairWorkflowArguments | None
    predecessor_cell_observations: tuple[CellObservationRef, ...]
    drift_lookup: DriftLookupProtocol | None = None

    def __post_init__(self) -> None:
        if type(self.capture_inputs) is not tuple or any(
            type(item) is not CaptureWorkflowArguments for item in self.capture_inputs
        ):
            raise AlignmentWorkflowContractError(
                "adapter capture inputs must be an immutable typed tuple"
            )
        for item in self.capture_inputs:
            item.__post_init__()
        if type(self.mode_track) is not ModeTrackWorkflowArguments:
            raise AlignmentWorkflowContractError(
                "adapter requires exact ModeTrack arguments"
            )
        self.mode_track.__post_init__()
        if (
            self.repair is not None
            and type(self.repair) is not ObservationRepairWorkflowArguments
        ):
            raise AlignmentWorkflowContractError(
                "adapter requires exact ObservationRepair arguments"
            )
        if self.repair is not None:
            self.repair.__post_init__()
        if type(self.predecessor_cell_observations) is not tuple or any(
            type(item) is not CellObservationRef
            for item in self.predecessor_cell_observations
        ):
            raise AlignmentWorkflowContractError(
                "predecessor cells must be exact immutable references"
            )


@dataclass(frozen=True, slots=True)
class TargetWorkflowAdapter(_WorkflowAdapterFields):
    pass


@dataclass(frozen=True, slots=True)
class UntargetWorkflowAdapter(_WorkflowAdapterFields):
    def __post_init__(self) -> None:
        super(UntargetWorkflowAdapter, self).__post_init__()
        if self.repair is None:
            raise AlignmentWorkflowContractError(
                "Untarget adapter requires exact ObservationRepair arguments"
            )


WorkflowAdapter: TypeAlias = TargetWorkflowAdapter | UntargetWorkflowAdapter


@dataclass(frozen=True, slots=True)
class UntargetWorkflowBundle:
    """One subject-local request/adapter pair minted after runtime success."""

    request: UntargetWorkflowRequest | UntargetWorkflowCompositionRequest
    adapter: UntargetWorkflowAdapter

    def __post_init__(self) -> None:
        if type(self.request) not in (
            UntargetWorkflowRequest,
            UntargetWorkflowCompositionRequest,
        ):
            raise AlignmentWorkflowContractError(
                "Untarget workflow bundle requires an exact request"
            )
        if type(self.adapter) is not UntargetWorkflowAdapter:
            raise AlignmentWorkflowContractError(
                "Untarget workflow bundle requires an exact adapter"
            )
        self.request.__post_init__()
        self.adapter.__post_init__()


UntargetWorkflowBatchProvider: TypeAlias = Callable[
    [], tuple[UntargetWorkflowBundle, ...]
]


@dataclass(frozen=True, slots=True)
class CompletedTargetWorkflowResult:
    runtime_receipt: RuntimeFinalizationReceipt
    application_result: FinalizedTargetApplicationResult

    def __post_init__(self) -> None:
        _require_runtime_outcome(self.runtime_receipt, AttemptTerminalOutcome.SUCCEEDED)
        if type(self.application_result) is not FinalizedTargetApplicationResult:
            raise AlignmentWorkflowContractError(
                "completed Target workflow requires an exact application result"
            )


@dataclass(frozen=True, slots=True)
class CompletedUntargetWorkflowResult:
    runtime_receipt: RuntimeFinalizationReceipt
    application_result: FinalizedUntargetApplicationResult

    def __post_init__(self) -> None:
        _require_runtime_outcome(self.runtime_receipt, AttemptTerminalOutcome.SUCCEEDED)
        if type(self.application_result) is not FinalizedUntargetApplicationResult:
            raise AlignmentWorkflowContractError(
                "completed Untarget workflow requires an exact application result"
            )


@dataclass(frozen=True, slots=True)
class CompletedUntargetBatchWorkflowResult:
    runtime_receipt: RuntimeFinalizationReceipt
    application_result: FinalizedUntargetBatchApplicationResult
    # Diagnostic handoff only; these retained owner facts grant no completion
    # or Matrix authority and do not change scientific result equality.
    method_assessments: tuple[CidNlMethodAssessmentRef, ...] = field(
        default=(), repr=False, compare=False
    )
    initial_groups: tuple[CrossSamplePeakGroupHypothesis, ...] = field(
        default=(), repr=False, compare=False
    )
    initial_edges: tuple[CrossSampleNeighborhoodEdge, ...] = field(
        default=(), repr=False, compare=False
    )

    def __post_init__(self) -> None:
        _require_runtime_outcome(self.runtime_receipt, AttemptTerminalOutcome.SUCCEEDED)
        if type(self.application_result) is not FinalizedUntargetBatchApplicationResult:
            raise AlignmentWorkflowContractError(
                "completed Untarget batch requires an exact batch application result"
            )
        if (
            self.runtime_receipt.supervisor.attempt
            is not self.application_result.processing_attempt
        ):
            raise AlignmentWorkflowContractError(
                "completed Untarget batch crossed its runtime attempt"
            )


@dataclass(frozen=True, slots=True)
class CancelledWorkflowResult:
    runtime_receipt: RuntimeFinalizationReceipt

    def __post_init__(self) -> None:
        _require_runtime_outcome(self.runtime_receipt, AttemptTerminalOutcome.CANCELLED)


@dataclass(frozen=True, slots=True)
class FailedWorkflowResult:
    runtime_receipt: RuntimeFinalizationReceipt
    stage: WorkflowStage
    failure_type: str | None
    failure_message: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.runtime_receipt, RuntimeFinalizationReceipt):
            raise AlignmentWorkflowContractError(
                "failed workflow requires an owner-minted runtime receipt"
            )
        if type(self.stage) is not WorkflowStage:
            raise AlignmentWorkflowContractError("failed workflow stage must be closed")
        outcome = self.runtime_receipt.supervisor.terminal_outcome
        if outcome is AttemptTerminalOutcome.FAILED:
            if (
                self.stage is not WorkflowStage.RUNTIME
                or self.failure_type is not None
                or self.failure_message is not None
            ):
                raise AlignmentWorkflowContractError(
                    "runtime failure cannot carry a composition failure"
                )
            return
        if outcome is not AttemptTerminalOutcome.SUCCEEDED:
            raise AlignmentWorkflowContractError(
                "cancelled runtime belongs to the cancelled terminal variant"
            )
        if (
            self.stage is WorkflowStage.RUNTIME
            or type(self.failure_type) is not str
            or type(self.failure_message) is not str
        ):
            raise AlignmentWorkflowContractError(
                "post-runtime composition failure requires exact details"
            )


def _require_runtime_outcome(
    receipt: RuntimeFinalizationReceipt,
    expected: AttemptTerminalOutcome,
) -> None:
    if not isinstance(receipt, RuntimeFinalizationReceipt):
        raise AlignmentWorkflowContractError(
            "workflow terminal result requires an owner-minted runtime receipt"
        )
    if receipt.supervisor.terminal_outcome is not expected:
        raise AlignmentWorkflowContractError(
            f"workflow terminal result requires runtime outcome {expected.value}"
        )


WorkflowTerminalResult: TypeAlias = (
    CompletedTargetWorkflowResult
    | CompletedUntargetWorkflowResult
    | CompletedUntargetBatchWorkflowResult
    | CancelledWorkflowResult
    | FailedWorkflowResult
)


def execute_alignment_workflow(
    request: WorkflowRequest,
    adapter: WorkflowAdapter,
) -> WorkflowTerminalResult:
    """Forward one exact request through the frozen semantic owners."""

    owners = _validate_request_adapter(request, adapter)
    runtime_receipt = _execute_runtime(request.runtime)
    outcome = runtime_receipt.supervisor.terminal_outcome
    if outcome is AttemptTerminalOutcome.CANCELLED:
        return CancelledWorkflowResult(runtime_receipt=runtime_receipt)
    if outcome is AttemptTerminalOutcome.FAILED:
        return FailedWorkflowResult(
            runtime_receipt=runtime_receipt,
            stage=WorkflowStage.RUNTIME,
            failure_type=None,
            failure_message=None,
        )
    return _execute_composition_after_runtime(
        request,
        adapter,
        runtime_receipt,
        owners=owners,
    )


def execute_untarget_batch_workflow(
    request: UntargetWorkflowRequest,
    provider: UntargetWorkflowBatchProvider,
) -> WorkflowTerminalResult:
    """Run one Untarget batch runtime, then compose subject-local children."""

    _validate_untarget_batch_request(request)
    runtime_receipt = _execute_runtime(request.runtime)
    return compose_untarget_batch_after_runtime(request, provider, runtime_receipt)


def compose_untarget_batch_after_runtime(
    request: UntargetWorkflowRequest | UntargetWorkflowCompositionRequest,
    provider: UntargetWorkflowBatchProvider,
    runtime_receipt: RuntimeFinalizationReceipt,
) -> WorkflowTerminalResult:
    """Compose one batch from its exact already-completed owner runtime."""

    _validate_untarget_batch_composition_request(request)
    if not isinstance(runtime_receipt, RuntimeFinalizationReceipt):
        raise AlignmentWorkflowContractError(
            "Untarget batch composition requires an owner runtime receipt"
        )
    if runtime_receipt.supervisor.attempt is not request.processing_attempt:
        raise AlignmentWorkflowContractError(
            "Untarget batch composition crossed its processing attempt"
        )
    outcome = runtime_receipt.supervisor.terminal_outcome
    if outcome is AttemptTerminalOutcome.CANCELLED:
        return CancelledWorkflowResult(runtime_receipt=runtime_receipt)
    if outcome is AttemptTerminalOutcome.FAILED:
        return FailedWorkflowResult(
            runtime_receipt=runtime_receipt,
            stage=WorkflowStage.RUNTIME,
            failure_type=None,
            failure_message=None,
        )

    try:
        if not callable(provider):
            raise AlignmentWorkflowContractError(
                "Untarget batch provider must be callable"
            )
        bundles = provider()
        _validate_untarget_batch_bundles(request, bundles)
        children: list[FinalizedUntargetApplicationResult] = []
        for bundle in bundles:
            owners = _validate_request_adapter(
                bundle.request,
                bundle.adapter,
            )
            child = _execute_composition_after_runtime(
                bundle.request,
                bundle.adapter,
                runtime_receipt,
                owners=owners,
            )
            if type(child) is FailedWorkflowResult:
                return child
            if type(child) is not CompletedUntargetWorkflowResult:
                raise AlignmentWorkflowContractError(
                    "Untarget batch child returned an unexpected terminal variant"
                )
            children.append(child.application_result)
        excluded_candidates = (
            getattr(request, "excluded_candidates", ())
            if type(request) is UntargetWorkflowCompositionRequest
            else ()
        )
        application_result = finalize_alignment_result(
            UntargetBatchApplicationResultRequest(
                scope=request.scope,
                processing_request=request.processing_request,
                processing_attempt=request.processing_attempt,
                children=tuple(children),
                excluded_candidates=excluded_candidates,
            )
        )
        if type(application_result) is not FinalizedUntargetBatchApplicationResult:
            raise AlignmentWorkflowContractError(
                "application_result returned a crossed Untarget batch result"
            )
        return CompletedUntargetBatchWorkflowResult(
            runtime_receipt=runtime_receipt,
            application_result=application_result,
        )
    except Exception as exc:
        return FailedWorkflowResult(
            runtime_receipt=runtime_receipt,
            stage=WorkflowStage.APPLICATION_RESULT,
            failure_type=f"{type(exc).__module__}.{type(exc).__qualname__}",
            failure_message=str(exc),
        )


def _execute_runtime(
    invocation: WorkflowRuntimeInvocation,
) -> RuntimeFinalizationReceipt:
    runtime_receipt = execute_runtime_attempt(
        invocation.runtime,
        invocation.reservations,
        worker_batch_provider=invocation.worker_batch_provider,
        telemetry_provider=invocation.telemetry_provider,
        persister=invocation.persister,
    )
    return runtime_receipt


def _execute_composition_after_runtime(
    request: WorkflowRequest | UntargetWorkflowCompositionRequest,
    adapter: WorkflowAdapter,
    runtime_receipt: RuntimeFinalizationReceipt,
    *,
    owners: ProcessingOwnerReferences | None = None,
) -> WorkflowTerminalResult:
    if owners is None:
        owners = _validate_request_adapter(request, adapter)
    stage = WorkflowStage.CAPTURE
    try:
        captures = tuple(
            _capture_workflow_input(request.scope, item)
            for item in adapter.capture_inputs
        )
        stage = WorkflowStage.MODE_TRACK_BOOTSTRAP
        ms1_only_target = (
            type(request) is TargetWorkflowRequest
            and type(request.scope) is TargetSearchScope
            and request.scope.target.definition.target.neutral_loss_da is None
        )
        if not ms1_only_target:
            run = bootstrap_mode_track_refinement_from_cid_nl_support(
                processing_owners=owners,
                sample_assessments=adapter.mode_track.sample_assessments,
                alignment_config=adapter.mode_track.alignment_config,
                trajectory_policy=adapter.mode_track.trajectory_policy,
                drift_lookup=adapter.drift_lookup,
                retain_observation_only_candidates=(
                    getattr(
                        adapter.mode_track,
                        "retain_observation_only_candidates",
                        False,
                    )
                ),
            )
        else:
            assert type(request) is TargetWorkflowRequest
            target = request.scope.target.definition.target
            mz_delta = target.mz * target.ppm_tol / 1_000_000.0
            sample_by_id = {
                sample.identity.sample_identity_id: sample
                for sample in request.scope.samples
            }
            sample_hypotheses = tuple(
                (
                    sample_by_id[
                        item.owner_observation.content.canonical_sample_identity_id
                    ],
                    item.owner_hypothesis,
                )
                for item in adapter.capture_inputs
                if item.owner_observation is not None
                and item.owner_hypothesis is not None
            )
            run = bootstrap_mode_track_refinement_from_ms1_hypotheses(
                processing_owners=owners,
                samples=request.scope.samples,
                sample_hypotheses=sample_hypotheses,
                family_mz_interval=(target.mz - mz_delta, target.mz + mz_delta),
                family_rt_interval=(target.rt_min, target.rt_max),
                alignment_config=adapter.mode_track.alignment_config,
                trajectory_policy=adapter.mode_track.trajectory_policy,
                drift_lookup=adapter.drift_lookup,
            )
        stage = WorkflowStage.CROSS_SAMPLE_MT0
        context = apply_mode_track_refinement_run_for_workflow(
            processing_owners=owners,
            captures=captures,
            run=run,
        )
        if adapter.repair is None:
            if type(request) is not TargetWorkflowRequest:
                raise AlignmentWorkflowContractError(
                    "only Targeted may omit optional ObservationRepair"
                )
            final_receipt = close_cross_sample_without_observation_repair(context)
            scientific = finalize_scientific_alignment(
                scope=request.scope,
                captures=captures,
                final_cross_sample_receipt=final_receipt,
                cell_observation_handoff=None,
                predecessor_cell_observations=(),
            )
            return _finish_variant(
                request=request,
                scientific=scientific,
                runtime_receipt=runtime_receipt,
            )

        repair = adapter.repair
        captured_results: list[OwnerBackfillSampleResult] = []

        def execute_repair(
            candidate: ObservationRepairCandidate,
        ) -> OwnerBackfillSampleResult:
            if captured_results:
                raise AlignmentWorkflowContractError(
                    "repair callback may be invoked at most once"
                )
            result = repair.execute(candidate)
            if type(result) is not OwnerBackfillSampleResult:
                raise AlignmentWorkflowContractError(
                    "repair callback must return an exact owner result"
                )
            captured_results.append(result)
            return result

        stage = WorkflowStage.OBSERVATION_REPAIR
        terminal = execute_observation_repair(
            processing_owners=owners,
            coverage=repair.coverage,
            cell_observations=repair.cell_observations,
            execute=execute_repair,
        )
        repair_result = captured_results[0] if captured_results else None
        stage = WorkflowStage.CROSS_SAMPLE_REPAIR
        post_repair = consume_observation_repair_terminal(
            context=context,
            terminal=terminal,
            repair_result=repair_result,
            alignment_config=adapter.mode_track.alignment_config,
            drift_lookup=adapter.drift_lookup,
        )
        if type(post_repair) is CrossSamplePostRepairModeTrackInput:
            receipt = terminal.receipt
            if type(receipt) is not ObservationRepairReceipt:
                raise AlignmentWorkflowContractError(
                    "changed repair handoff lost its exact receipt"
                )
            stage = WorkflowStage.MODE_TRACK_MT1
            final_attempt = run_post_repair_mode_track_final_attempt(
                source=run.fit_once_source,
                repair_receipt=receipt,
                post_repair=post_repair,
            )
            stage = WorkflowStage.CROSS_SAMPLE_FINAL
            final_receipt = consume_mode_track_final_attempt(
                context=context,
                post_repair=post_repair,
                terminal=terminal,
                final_attempt=final_attempt,
            )
        elif type(post_repair) is CrossSampleFinalReceipt:
            final_receipt = post_repair
        else:
            raise AlignmentWorkflowContractError(
                "CrossSample repair consumer returned an unknown variant"
            )
        repair_receipts = () if terminal.receipt is None else (terminal.receipt,)
        repaired_observations = _repaired_observations(
            terminal.receipt,
            repair_result,
        )
        stage = WorkflowStage.SCIENTIFIC_FINAL
        handoff = build_cross_sample_association_lineage_handoff(
            coverage=adapter.repair.coverage,
            final_receipt=final_receipt,
        )
        scientific = finalize_scientific_alignment(
            scope=request.scope,
            captures=captures,
            final_cross_sample_receipt=final_receipt,
            cell_observation_handoff=handoff,
            predecessor_cell_observations=adapter.predecessor_cell_observations,
            repair_receipts=repair_receipts,
            repaired_observations=repaired_observations,
        )
        return _finish_variant(
            request=request,
            scientific=scientific,
            runtime_receipt=runtime_receipt,
        )
    except _WorkflowStageFailure as failure:
        exc = failure.error
        return FailedWorkflowResult(
            runtime_receipt=runtime_receipt,
            stage=failure.stage,
            failure_type=f"{type(exc).__module__}.{type(exc).__qualname__}",
            failure_message=str(exc),
        )
    except Exception as exc:
        return FailedWorkflowResult(
            runtime_receipt=runtime_receipt,
            stage=stage,
            failure_type=f"{type(exc).__module__}.{type(exc).__qualname__}",
            failure_message=str(exc),
        )


def _capture_workflow_input(
    scope: TargetSearchScope | UntargetedSearchScope,
    item: CaptureWorkflowArguments,
) -> SampleLocalCapture:
    capture = capture_peak_hypotheses(
        scope=scope,
        candidate_id=item.candidate_id,
        trace=item.trace,
    )
    owner_observation = getattr(item, "owner_observation", None)
    owner_hypothesis = getattr(item, "owner_hypothesis", None)
    if owner_observation is None or capture.selected_hypothesis is None:
        return capture
    assert owner_hypothesis is not None
    return bind_owner_backed_capture_hypothesis(
        capture,
        observation=owner_observation,
        hypothesis=owner_hypothesis,
    )


def _validate_untarget_batch_request(request: UntargetWorkflowRequest) -> None:
    if type(request) is not UntargetWorkflowRequest:
        raise AlignmentWorkflowContractError(
            "Untarget batch requires an exact Untarget workflow request"
        )
    request.__post_init__()
    if request.processing_request is not request.scope.request:
        raise AlignmentWorkflowContractError(
            "Untarget batch request crossed its processing request"
        )
    _validate_runtime_attempt_binding(request.runtime, request.processing_attempt)
    _validate_untarget_batch_fields(request)


def _validate_untarget_batch_composition_request(
    request: UntargetWorkflowRequest | UntargetWorkflowCompositionRequest,
) -> None:
    if type(request) is UntargetWorkflowRequest:
        _validate_untarget_batch_request(request)
        return
    if type(request) is not UntargetWorkflowCompositionRequest:
        raise AlignmentWorkflowContractError(
            "Untarget batch composition requires an exact completed request"
        )
    request.__post_init__()
    if request.processing_request is not request.scope.request:
        raise AlignmentWorkflowContractError(
            "Untarget batch request crossed its processing request"
        )
    _validate_untarget_batch_fields(request)


def _validate_untarget_batch_fields(
    request: UntargetWorkflowRequest | UntargetWorkflowCompositionRequest,
) -> None:
    candidate_ids = tuple(request.scope.declared_candidate_ids)
    if not candidate_ids or len(candidate_ids) != len(set(candidate_ids)):
        raise AlignmentWorkflowContractError(
            "Untarget batch request requires one complete candidate universe"
        )
    subject_ids = tuple(
        provider.method_subject_id for provider in request.scope.evidence_providers
    )
    if not subject_ids or len(subject_ids) != len(set(subject_ids)):
        raise AlignmentWorkflowContractError(
            "Untarget batch request requires unique method subjects"
        )
    try:
        subject_batch = cid_nl_method_subject_batch_from_request(
            request.processing_request
        )
    except CidNlMethodSubjectContractError as exc:
        raise AlignmentWorkflowContractError(
            "Untarget batch request is not pinned to a CID-NL subject batch"
        ) from exc
    if candidate_ids != tuple(
        candidate_id
        for candidate_id, _method_subject_id in subject_batch.candidate_memberships
    ) or subject_ids != tuple(
        subject.method_subject_id for subject in subject_batch.subjects
    ):
        raise AlignmentWorkflowContractError(
            "Untarget batch request scope is not the complete request universe"
        )
    if type(request) is UntargetWorkflowCompositionRequest:
        exclusions = request.excluded_candidates
        if type(exclusions) is not tuple or any(
            type(item) is not UntargetedCandidateExclusion for item in exclusions
        ):
            raise AlignmentWorkflowContractError(
                "Untarget batch exclusions must be an immutable typed tuple"
            )
        excluded_ids = tuple(item.candidate_id for item in exclusions)
        capture_ids = tuple(request.scope.capture_bindings)
        capture_candidate_ids = tuple(item.candidate_id for item in capture_ids)
        if sorted((*capture_candidate_ids, *excluded_ids)) != sorted(candidate_ids):
            raise AlignmentWorkflowContractError(
                "Untarget batch capture/exclusion universe is incomplete"
            )
    elif (
        tuple(binding.candidate_id for binding in request.scope.capture_bindings)
        != candidate_ids
    ):
        raise AlignmentWorkflowContractError(
            "Untarget batch runtime scope must capture the complete candidate universe"
        )


def _validate_untarget_batch_bundles(
    request: UntargetWorkflowRequest | UntargetWorkflowCompositionRequest,
    bundles: object,
) -> None:
    if type(bundles) is not tuple:
        raise AlignmentWorkflowContractError(
            "Untarget batch provider must return an immutable typed tuple"
        )
    parent_candidates = tuple(request.scope.declared_candidate_ids)
    subject_batch = cid_nl_method_subject_batch_from_request(request.processing_request)
    parent_memberships = dict(subject_batch.candidate_memberships)
    parent_samples = tuple(request.scope.samples)
    parent_sample_ids = tuple(
        sample.identity.sample_identity_id for sample in parent_samples
    )
    if len(parent_sample_ids) != len(set(parent_sample_ids)):
        raise AlignmentWorkflowContractError(
            "Untarget batch request sample universe contains duplicates"
        )
    parent_subject_ids = {
        provider.method_subject_id for provider in request.scope.evidence_providers
    }
    child_candidates: list[str] = []
    child_subject_ids: list[str] = []
    for bundle in bundles:
        if type(bundle) is not UntargetWorkflowBundle:
            raise AlignmentWorkflowContractError(
                "Untarget batch provider returned a non-owner bundle"
            )
        bundle.__post_init__()
        child_request = bundle.request
        child_scope = child_request.scope
        if (
            type(child_request) is not type(request)
            or child_request.processing_request is not request.processing_request
            or child_request.processing_attempt is not request.processing_attempt
        ):
            raise AlignmentWorkflowContractError(
                "Untarget batch child crossed request, attempt, or runtime"
            )
        if (
            type(request) is UntargetWorkflowRequest
            and type(child_request) is UntargetWorkflowRequest
            and child_request.runtime is not request.runtime
        ):
            raise AlignmentWorkflowContractError(
                "Untarget batch child crossed request, attempt, or runtime"
            )
        child_sample_ids = tuple(
            sample.identity.sample_identity_id for sample in child_scope.samples
        )
        if child_sample_ids != parent_sample_ids or any(
            sample is not parent
            for sample, parent in zip(
                child_scope.samples,
                parent_samples,
                strict=True,
            )
        ):
            raise AlignmentWorkflowContractError(
                "Untarget batch children must share the exact sample universe"
            )
        memberships = _untarget_scope_memberships(child_scope)
        candidate_ids = tuple(child_scope.declared_candidate_ids)
        if set(memberships) != set(candidate_ids):
            raise AlignmentWorkflowContractError(
                "Untarget batch child has incomplete subject membership"
            )
        if any(
            parent_memberships.get(candidate_id) != method_subject_id
            for candidate_id, method_subject_id in memberships.items()
        ):
            raise AlignmentWorkflowContractError(
                "Untarget batch child subject membership crossed its parent"
            )
        child_provider_ids = tuple(
            provider.method_subject_id for provider in child_scope.evidence_providers
        )
        if len(child_provider_ids) != 1 or set(memberships.values()) != set(
            child_provider_ids
        ):
            raise AlignmentWorkflowContractError(
                "Untarget batch child must be one subject-local provider"
            )
        subject_id = child_provider_ids[0]
        if subject_id not in parent_subject_ids:
            raise AlignmentWorkflowContractError(
                "Untarget batch child references an undeclared method subject"
            )
        if subject_id in child_subject_ids:
            raise AlignmentWorkflowContractError(
                "Untarget batch provider duplicated a method subject"
            )
        child_candidates.extend(candidate_ids)
        child_subject_ids.append(subject_id)
        _validate_request_adapter(child_request, bundle.adapter)

    excluded_ids = (
        tuple(item.candidate_id for item in request.excluded_candidates)
        if type(request) is UntargetWorkflowCompositionRequest
        else ()
    )
    if len(child_candidates) != len(set(child_candidates)) or sorted(
        (*child_candidates, *excluded_ids)
    ) != sorted(parent_candidates):
        raise AlignmentWorkflowContractError(
            "Untarget batch child/exclusion candidate union is not exactly once"
        )
    excluded_id_set = set(excluded_ids)
    expected_child_subject_ids = tuple(
        provider.method_subject_id
        for provider in request.scope.evidence_providers
        if any(
            method_subject_id == provider.method_subject_id
            and candidate_id not in excluded_id_set
            for candidate_id, method_subject_id in parent_memberships.items()
        )
    )
    if tuple(child_subject_ids) != expected_child_subject_ids:
        raise AlignmentWorkflowContractError(
            "Untarget batch children are not in canonical subject order"
        )


def _untarget_scope_memberships(scope: UntargetedSearchScope) -> dict[str, str]:
    memberships: dict[str, str] = {}
    for binding in scope.capture_bindings:
        candidate_id = getattr(binding, "candidate_id", None)
        subject_id = getattr(binding, "method_subject_id", None)
        if not isinstance(candidate_id, str) or not isinstance(subject_id, str):
            raise AlignmentWorkflowContractError(
                "Untarget batch capture membership is incomplete"
            )
        if candidate_id in memberships:
            raise AlignmentWorkflowContractError(
                "Untarget batch capture membership contains duplicate candidates"
            )
        memberships[candidate_id] = subject_id
    return memberships


def _validate_request_adapter(
    request: WorkflowRequest | UntargetWorkflowCompositionRequest,
    adapter: WorkflowAdapter,
) -> ProcessingOwnerReferences:
    variants = {
        TargetWorkflowRequest: (TargetWorkflowAdapter, TargetSearchScope),
        UntargetWorkflowRequest: (UntargetWorkflowAdapter, UntargetedSearchScope),
        UntargetWorkflowCompositionRequest: (
            UntargetWorkflowAdapter,
            UntargetedSearchScope,
        ),
    }
    expected = variants.get(type(request))
    if (
        expected is None
        or type(adapter) is not expected[0]
        or type(request.scope) is not expected[1]
    ):
        raise AlignmentWorkflowContractError(
            "workflow request, adapter, and scope variants must match exactly"
        )
    request.__post_init__()
    adapter.__post_init__()
    if (
        request.processing_request is not request.scope.request
        or request.processing_attempt.processing_request_id
        != request.processing_request.processing_request_id
    ):
        raise AlignmentWorkflowContractError(
            "workflow request crossed processing request or attempt"
        )
    if type(request) is not UntargetWorkflowCompositionRequest:
        _validate_runtime_attempt_binding(request.runtime, request.processing_attempt)
    if adapter.repair is not None:
        owners = adapter.repair.coverage.epoch.processing_owners
    elif type(request) is TargetWorkflowRequest:
        owners = compose_processing_owner_references(
            request=request.processing_request,
            attempt=request.processing_attempt,
            sample_snapshot=request.scope.samples[0].snapshot,
            sample_resolutions=request.scope.samples,
            target_snapshot=request.scope.target_snapshot,
        )
    else:
        raise AlignmentWorkflowContractError(
            "workflow adapter lost its processing owners"
        )
    if (
        type(owners) is not ProcessingOwnerReferences
        or owners.request is not request.processing_request
        or owners.attempt is not request.processing_attempt
    ):
        raise AlignmentWorkflowContractError(
            "workflow repair population crossed exact processing owners"
        )
    for sample in request.scope.samples:
        validate_scope_owner_binding(
            request.scope,
            processing_owners=owners,
            sample=sample,
        )
    candidate_ids = tuple(item.candidate_id for item in adapter.capture_inputs)
    declared_candidate_ids = request.scope.declared_candidate_ids
    if candidate_ids != declared_candidate_ids or len(candidate_ids) != len(
        set(candidate_ids)
    ):
        raise AlignmentWorkflowContractError(
            "capture inputs must exactly preserve declared candidate order"
        )
    assessment_samples = tuple(
        sample for sample, _ in adapter.mode_track.sample_assessments
    )
    expected_samples = tuple(
        sorted(request.scope.samples, key=lambda item: item.identity.sample_identity_id)
    )
    if assessment_samples:
        ordered_ids = tuple(
            sample.identity.sample_identity_id for sample in assessment_samples
        )
        expected_by_id = {
            sample.identity.sample_identity_id: sample for sample in expected_samples
        }
        if (
            ordered_ids != tuple(sorted(ordered_ids))
            or tuple(dict.fromkeys(ordered_ids)) != tuple(expected_by_id)
            or any(
                sample is not expected_by_id.get(sample.identity.sample_identity_id)
                for sample in assessment_samples
            )
        ):
            raise AlignmentWorkflowContractError(
                "ModeTrack assessments do not close the exact scope samples"
            )
    elif (
        type(request) is not TargetWorkflowRequest
        or request.scope.target.definition.target.neutral_loss_da is not None
    ):
        raise AlignmentWorkflowContractError(
            "only an MS1-only Target may use captured owner facts without assessments"
        )
    if type(adapter.predecessor_cell_observations) is not tuple or any(
        type(item) is not CellObservationRef
        for item in adapter.predecessor_cell_observations
    ):
        raise AlignmentWorkflowContractError(
            "predecessor cells must be exact immutable references"
        )
    return owners


def _validate_runtime_attempt_binding(
    invocation: WorkflowRuntimeInvocation,
    attempt: ProcessingAttemptIdentity,
) -> None:
    runtime = invocation.runtime
    if (
        runtime.manifest.attempt is not attempt
        or runtime.history.attempt is not attempt
        or any(
            reservation.attempt is not attempt
            for reservation in invocation.reservations
        )
    ):
        raise AlignmentWorkflowContractError(
            "workflow runtime crossed the exact processing attempt"
        )


def _repaired_observations(
    receipt: ObservationRepairReceipt | None,
    result: OwnerBackfillSampleResult | None,
) -> tuple[OwnerBackfillPeakFactObservation, ...]:
    if receipt is None or receipt.evidence is None:
        return ()
    if result is None:
        raise AlignmentWorkflowContractError(
            "repaired receipt lost its exact callback result"
        )
    matches = tuple(
        item
        for item in result.peak_fact_observations
        if item.work_unit_key == receipt.evidence.work_unit_key
    )
    if len(matches) != 1:
        raise AlignmentWorkflowContractError(
            "repaired receipt does not bind one exact live observation"
        )
    return matches


def _finish_variant(
    *,
    request: WorkflowRequest | UntargetWorkflowCompositionRequest,
    scientific: FinalizedScientificAlignment,
    runtime_receipt: RuntimeFinalizationReceipt,
) -> WorkflowTerminalResult:
    stage = WorkflowStage.PRE_CELL_PROJECTION
    try:
        if type(request) is TargetWorkflowRequest:
            target_pre_cell = project_target_pre_cell(scientific)
            stage = WorkflowStage.CELL_DECISION
            from xic_extractor.alignment.cell_decision.owner import (
                decide_target_cell_population,
            )

            target_decisions = decide_target_cell_population(target_pre_cell)
            stage = WorkflowStage.POST_CELL_PROJECTION
            target_projection = project_target_post_cell(
                target_pre_cell,
                target_decisions,
            )
            stage = WorkflowStage.APPLICATION_RESULT
            result = finalize_alignment_result(
                TargetApplicationResultRequest(
                    scientific_core=scientific,
                    completed_projection=target_projection,
                )
            )
            if (
                type(result) is not FinalizedTargetApplicationResult
                or result.scientific_core is not scientific
                or result.completed_projection is not target_projection
            ):
                raise AlignmentWorkflowContractError(
                    "application_result returned a crossed Target result"
                )
            return CompletedTargetWorkflowResult(runtime_receipt, result)
        untarget_pre_cell = project_untarget_pre_cell(scientific)
        stage = WorkflowStage.CELL_DECISION
        from xic_extractor.alignment.cell_decision.owner import (
            decide_group_cell_population,
        )

        untarget_decisions = decide_group_cell_population(
            untarget_pre_cell,
            scientific,
        )
        stage = WorkflowStage.POST_CELL_PROJECTION
        untarget_projection = project_untarget_post_cell(
            untarget_pre_cell,
            untarget_decisions,
        )
        stage = WorkflowStage.APPLICATION_RESULT
        result = finalize_alignment_result(
            UntargetApplicationResultRequest(
                scientific_core=scientific,
                completed_projection=untarget_projection,
            )
        )
        if (
            type(result) is not FinalizedUntargetApplicationResult
            or result.scientific_core is not scientific
            or result.completed_projection is not untarget_projection
        ):
            raise AlignmentWorkflowContractError(
                "application_result returned a crossed Untarget result"
            )
        return CompletedUntargetWorkflowResult(runtime_receipt, result)
    except Exception as exc:
        raise _WorkflowStageFailure(stage, exc) from exc


__all__ = (
    "AlignmentWorkflowContractError",
    "CancelledWorkflowResult",
    "CaptureWorkflowArguments",
    "CompletedTargetWorkflowResult",
    "CompletedUntargetBatchWorkflowResult",
    "CompletedUntargetWorkflowResult",
    "FailedWorkflowResult",
    "ModeTrackWorkflowArguments",
    "ObservationRepairWorkflowArguments",
    "TargetWorkflowAdapter",
    "TargetWorkflowRequest",
    "UntargetWorkflowAdapter",
    "UntargetWorkflowBatchProvider",
    "UntargetWorkflowBundle",
    "UntargetWorkflowCompositionRequest",
    "UntargetWorkflowRequest",
    "UntargetedCandidateExclusion",
    "WorkflowAdapter",
    "WorkflowRequest",
    "WorkflowRuntimeInvocation",
    "WorkflowStage",
    "WorkflowTerminalResult",
    "compose_untarget_batch_after_runtime",
    "execute_untarget_batch_workflow",
    "execute_alignment_workflow",
)
