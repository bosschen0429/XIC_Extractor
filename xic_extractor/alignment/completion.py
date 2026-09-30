"""Pure Target/Untarget completion over acquired evidence and scientific owners."""

from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from enum import Enum
from typing import TypeAlias, cast

from xic_extractor.alignment.backfill.evaluation_coverage import (
    BackfillEvaluationCoverageBatch,
)
from xic_extractor.alignment.cell_observations import CellObservationRef
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.association import (
    build_cross_sample_association_lineage_handoff,
)
from xic_extractor.alignment.edge_scoring import DriftLookupProtocol
from xic_extractor.alignment.grouping_result import (
    CrossSampleGroupingResult,
)
from xic_extractor.alignment.grouping_workflow import (
    build_targeted_cross_sample_groups,
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
    _finalize_fresh_untarget_result,
    finalize_alignment_result,
)
from xic_extractor.alignment.result.scientific_alignment import (
    FinalizedScientificAlignment,
    finalize_scientific_alignment,
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
from xic_extractor.peak_detection.observations import (
    PeakObservationRef,
)
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
    CROSS_SAMPLE_GROUPING = "cross_sample_grouping"
    SCIENTIFIC_FINAL = "scientific_final"
    PRE_CELL_PROJECTION = "pre_cell_projection"
    CELL_DECISION = "cell_decision"
    POST_CELL_PROJECTION = "post_cell_projection"
    APPLICATION_RESULT = "application_result"


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
class TargetWorkflowRequest:
    scope: TargetSearchScope
    processing_request: ProcessingRequestIdentity
    processing_attempt: ProcessingAttemptIdentity

    def __post_init__(self) -> None:
        if type(self.scope) is not TargetSearchScope:
            raise AlignmentWorkflowContractError("Target request scope must be exact")
        _validate_processing_fields(self)


@dataclass(frozen=True, slots=True)
class UntargetWorkflowCompositionRequest:
    """Untarget scientific composition inputs independent of runtime closure."""

    scope: UntargetedSearchScope
    processing_request: ProcessingRequestIdentity
    processing_attempt: ProcessingAttemptIdentity
    excluded_candidates: tuple[UntargetedCandidateExclusion, ...] = ()

    def __post_init__(self) -> None:
        if type(self.scope) is not UntargetedSearchScope:
            raise AlignmentWorkflowContractError("Untarget request scope must be exact")
        _validate_processing_fields(self)


def _validate_processing_fields(
    request: (TargetWorkflowRequest | UntargetWorkflowCompositionRequest),
) -> None:
    if type(request.processing_request) is not ProcessingRequestIdentity:
        raise AlignmentWorkflowContractError("processing request must be exact")
    if type(request.processing_attempt) is not ProcessingAttemptIdentity:
        raise AlignmentWorkflowContractError("processing attempt must be exact")


WorkflowRequest: TypeAlias = TargetWorkflowRequest | UntargetWorkflowCompositionRequest


@dataclass(frozen=True, slots=True)
class _WorkflowAdapterFields:
    capture_inputs: tuple[CaptureWorkflowArguments, ...]
    predecessor_cell_observations: tuple[CellObservationRef, ...]
    drift_lookup: DriftLookupProtocol | None = None
    backfill_cell_observations: tuple[CellObservationRef, ...] = ()

    def __post_init__(self) -> None:
        if type(self.capture_inputs) is not tuple or any(
            type(item) is not CaptureWorkflowArguments for item in self.capture_inputs
        ):
            raise AlignmentWorkflowContractError(
                "adapter capture inputs must be an immutable typed tuple"
            )
        for item in self.capture_inputs:
            item.__post_init__()
        if type(self.predecessor_cell_observations) is not tuple or any(
            type(item) is not CellObservationRef
            for item in self.predecessor_cell_observations
        ):
            raise AlignmentWorkflowContractError(
                "predecessor cells must be exact immutable references"
            )


@dataclass(frozen=True)
class TargetWorkflowAdapter(_WorkflowAdapterFields):
    sample_assessments: tuple[
        tuple[ResolvedCanonicalSampleIdentity, CidNlMethodAssessmentRef], ...
    ] = field(kw_only=True)
    alignment_config: AlignmentConfig = field(kw_only=True)
    completed_capture_handoff: InitVar[tuple[SampleLocalCapture, ...] | None] = None

    def __post_init__(
        self,
        completed_capture_handoff: tuple[SampleLocalCapture, ...] | None = None,
    ) -> None:
        super(TargetWorkflowAdapter, self).__post_init__()
        if type(self.sample_assessments) is not tuple or any(
            type(item) is not tuple
            or len(item) != 2
            or type(item[0]) is not ResolvedCanonicalSampleIdentity
            or type(item[1]) is not CidNlMethodAssessmentRef
            for item in self.sample_assessments
        ):
            raise AlignmentWorkflowContractError(
                "Target assessments must be exact sample/assessment pairs"
            )
        if type(self.alignment_config) is not AlignmentConfig:
            raise AlignmentWorkflowContractError("alignment_config must be exact")
        completed_captures = getattr(
            self,
            "_completed_captures",
            completed_capture_handoff,
        )
        if completed_captures is not None and type(completed_captures) is not tuple:
            raise AlignmentWorkflowContractError(
                "completed captures require an immutable owner population"
            )
        if not hasattr(self, "_completed_captures"):
            object.__setattr__(self, "_completed_captures", completed_captures)

    @property
    def completed_captures(self) -> tuple[SampleLocalCapture, ...] | None:
        return getattr(self, "_completed_captures", None)


@dataclass(frozen=True, slots=True)
class UntargetWorkflowAdapter(_WorkflowAdapterFields):
    sample_assessments: tuple[
        tuple[ResolvedCanonicalSampleIdentity, CidNlMethodAssessmentRef], ...
    ] = field(kw_only=True)
    completed_grouping: CrossSampleGroupingResult | None = None
    closed_cell_coverage: BackfillEvaluationCoverageBatch | None = None

    def __post_init__(self) -> None:
        super(UntargetWorkflowAdapter, self).__post_init__()
        if type(self.sample_assessments) is not tuple or any(
            type(item) is not tuple
            or len(item) != 2
            or type(item[0]) is not ResolvedCanonicalSampleIdentity
            or type(item[1]) is not CidNlMethodAssessmentRef
            for item in self.sample_assessments
        ):
            raise AlignmentWorkflowContractError(
                "Untarget assessments must be exact sample/assessment pairs"
            )
        if self.closed_cell_coverage is None:
            raise AlignmentWorkflowContractError(
                "Untarget adapter requires closed cell coverage"
            )
        if self.closed_cell_coverage is not None:
            coverage = self.closed_cell_coverage
            if type(coverage) is not BackfillEvaluationCoverageBatch:
                raise AlignmentWorkflowContractError(
                    "closed cell coverage must be exact"
                )
            if self.completed_grouping is None:
                raise AlignmentWorkflowContractError(
                    "closed cell coverage requires completed CrossSample"
                )
            if coverage.terminals or any(
                position.planning_state != "not_planned"
                for position in coverage.epoch.positions
            ):
                raise AlignmentWorkflowContractError(
                    "closed cell coverage must contain only unplanned positions"
                )
        if self.completed_grouping is not None:
            if type(self.completed_grouping) is not CrossSampleGroupingResult:
                raise AlignmentWorkflowContractError(
                    "completed CrossSample requires its exact owner context"
                )
            if self.closed_cell_coverage is None:
                raise AlignmentWorkflowContractError(
                    "completed CrossSample requires closed cell coverage"
                )
            if (
                self.completed_grouping.processing_owners
                is not self.closed_cell_coverage.epoch.processing_owners
            ):
                raise AlignmentWorkflowContractError(
                    "completed CrossSample crossed its closed cell coverage owners"
                )


WorkflowAdapter: TypeAlias = TargetWorkflowAdapter | UntargetWorkflowAdapter


@dataclass(frozen=True, slots=True)
class UntargetWorkflowBundle:
    """One bounded subject workset and its acquired scientific inputs."""

    request: UntargetWorkflowCompositionRequest
    adapter: UntargetWorkflowAdapter

    def __post_init__(self) -> None:
        if type(self.request) is not UntargetWorkflowCompositionRequest:
            raise AlignmentWorkflowContractError(
                "Untarget workflow bundle requires an exact request"
            )
        if type(self.adapter) is not UntargetWorkflowAdapter:
            raise AlignmentWorkflowContractError(
                "Untarget workflow bundle requires an exact adapter"
            )
        self.request.__post_init__()
        self.adapter.__post_init__()


def compose_untarget_subject(
    request: UntargetWorkflowCompositionRequest,
    adapter: UntargetWorkflowAdapter,
    *,
    completed_captures: tuple[SampleLocalCapture, ...] | None = None,
) -> FinalizedUntargetApplicationResult:
    """Close one bounded subject workset without asserting runtime completion."""
    if type(request) is not UntargetWorkflowCompositionRequest:
        raise AlignmentWorkflowContractError(
            "Subject composition requires exact inputs"
        )
    owners = validate_completion_inputs(request, adapter)
    subjects = set(_untarget_scope_memberships(request.scope).values())
    provider_subjects = {
        provider.method_subject_id for provider in request.scope.evidence_providers
    }
    if subjects != provider_subjects or request.excluded_candidates:
        raise AlignmentWorkflowContractError(
            "Subject composition requires one exact included subject workset"
        )
    try:
        result = complete_analysis(
            request,
            adapter,
            processing_owners=owners,
            completed_captures=completed_captures,
        )
    except _WorkflowStageFailure as failure:
        raise AlignmentWorkflowContractError(
            f"Untarget subject composition failed at {failure.stage.value}: "
            f"{failure.error}"
        ) from failure.error
    if type(result) is not FinalizedUntargetApplicationResult:
        raise AlignmentWorkflowContractError(
            "Subject composition returned crossed result"
        )
    return result


def _compose_application_result(
    request: WorkflowRequest,
    adapter: WorkflowAdapter,
    *,
    owners: ProcessingOwnerReferences,
    completed_captures: tuple[SampleLocalCapture, ...] | None = None,
) -> FinalizedTargetApplicationResult | FinalizedUntargetApplicationResult:
    stage = WorkflowStage.CAPTURE
    try:
        if completed_captures is None:
            captures = tuple(
                _capture_workflow_input(request.scope, item)
                for item in adapter.capture_inputs
            )
        else:
            if type(completed_captures) is not tuple:
                raise AlignmentWorkflowContractError(
                    "completed captures require an immutable owner population"
                )
            for capture, item in zip(
                completed_captures, adapter.capture_inputs, strict=True
            ):
                if (
                    type(capture) is not SampleLocalCapture
                    or capture.scope is not request.scope
                    or capture.candidate_id != item.candidate_id
                    or capture.trace is not item.trace
                ):
                    raise AlignmentWorkflowContractError(
                        "completed capture crossed its workflow scope or input"
                    )
                capture.__post_init__()
                if item.owner_hypothesis is not None and (
                    capture.selected_hypothesis is not item.owner_hypothesis
                    or item.owner_observation
                    not in item.owner_hypothesis.peak_observation_refs
                ):
                    raise AlignmentWorkflowContractError(
                        "completed capture crossed its owner binding"
                    )
                if (
                    item.owner_hypothesis is None
                    and capture.selected_hypothesis is not None
                ):
                    raise AlignmentWorkflowContractError(
                        "completed capture crossed its sealed selection"
                    )
            captures = completed_captures
        if type(adapter) is TargetWorkflowAdapter:
            stage = WorkflowStage.CROSS_SAMPLE_GROUPING
            context = build_targeted_cross_sample_groups(
                processing_owners=owners,
                captures=captures,
                sample_assessments=adapter.sample_assessments,
                alignment_config=adapter.alignment_config,
                drift_lookup=adapter.drift_lookup,
            )
        else:
            untarget_adapter = cast(UntargetWorkflowAdapter, adapter)
            stage = WorkflowStage.CROSS_SAMPLE_GROUPING
            # The adapter contract admits Untarget only with completed grouping.
            assert untarget_adapter.completed_grouping is not None
            context = untarget_adapter.completed_grouping
        closed_coverage = (
            adapter.closed_cell_coverage
            if type(adapter) is UntargetWorkflowAdapter
            else None
        )
        if type(request) is not TargetWorkflowRequest and closed_coverage is None:
            raise AlignmentWorkflowContractError(
                "Untarget requires closed cell coverage"
            )
        if (
            closed_coverage is not None
            and closed_coverage.epoch.processing_owners is not owners
        ):
            raise AlignmentWorkflowContractError(
                "closed cell coverage crossed exact workflow processing owners"
            )
        final_receipt = context
        handoff = None
        if closed_coverage is not None:
            stage = WorkflowStage.SCIENTIFIC_FINAL
            handoff = build_cross_sample_association_lineage_handoff(
                coverage=closed_coverage,
                final_receipt=final_receipt,
            )
        scientific = finalize_scientific_alignment(
            scope=request.scope,
            captures=captures,
            grouping_result=final_receipt,
            cell_observation_handoff=handoff,
            predecessor_cell_observations=(
                adapter.predecessor_cell_observations
                if closed_coverage is not None
                else ()
            ),
            backfill_cell_observations=adapter.backfill_cell_observations,
        )
        return _finish_variant(
            request=request,
            scientific=scientific,
        )

    except _WorkflowStageFailure:
        raise
    except Exception as exc:
        raise _WorkflowStageFailure(stage, exc) from exc


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


def _validate_untarget_batch_composition_request(
    request: UntargetWorkflowCompositionRequest,
) -> None:
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
    request: UntargetWorkflowCompositionRequest,
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


def _validate_untarget_batch_bundles(
    request: UntargetWorkflowCompositionRequest,
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
        if (
            not child_provider_ids
            or len(child_provider_ids) != len(set(child_provider_ids))
            or set(memberships.values()) != set(child_provider_ids)
        ):
            raise AlignmentWorkflowContractError(
                "Untarget batch child must close one exact subject workset"
            )
        if any(
            subject_id not in parent_subject_ids for subject_id in child_provider_ids
        ):
            raise AlignmentWorkflowContractError(
                "Untarget batch child references an undeclared method subject"
            )
        if any(subject_id in child_subject_ids for subject_id in child_provider_ids):
            raise AlignmentWorkflowContractError(
                "Untarget batch provider duplicated a method subject"
            )
        child_candidates.extend(candidate_ids)
        child_subject_ids.extend(child_provider_ids)
        validate_completion_inputs(child_request, bundle.adapter)

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
    included_subject_ids = {
        method_subject_id
        for candidate_id, method_subject_id in parent_memberships.items()
        if candidate_id not in excluded_id_set
    }
    expected_child_subject_ids = tuple(
        provider.method_subject_id
        for provider in request.scope.evidence_providers
        if provider.method_subject_id in included_subject_ids
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


def validate_completion_inputs(
    request: WorkflowRequest,
    adapter: WorkflowAdapter,
) -> ProcessingOwnerReferences:
    variants = {
        TargetWorkflowRequest: (TargetWorkflowAdapter, TargetSearchScope),
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
    if (
        type(adapter) is UntargetWorkflowAdapter
        and adapter.closed_cell_coverage is not None
    ):
        owners = adapter.closed_cell_coverage.epoch.processing_owners
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
            "workflow population crossed exact processing owners"
        )
    if (
        type(adapter) is UntargetWorkflowAdapter
        and adapter.completed_grouping is not None
        and adapter.completed_grouping.processing_owners is not owners
    ):
        raise AlignmentWorkflowContractError(
            "completed CrossSample crossed the workflow processing owners"
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
    assessments = adapter.sample_assessments
    assessment_samples = tuple(sample for sample, _ in assessments)
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
        # Completed Untarget grouping already carries the cohort and coverage.
        # Its assessments contain real searches, not placeholders for no input.
        completed_grouping = (
            type(adapter) is UntargetWorkflowAdapter
            and adapter.completed_grouping is not None
        )
        if (
            ordered_ids != tuple(sorted(ordered_ids))
            or (
                not completed_grouping
                and tuple(dict.fromkeys(ordered_ids)) != tuple(expected_by_id)
            )
            or any(
                sample is not expected_by_id.get(sample.identity.sample_identity_id)
                for sample in assessment_samples
            )
        ):
            raise AlignmentWorkflowContractError(
                "method assessments do not close the exact scope samples"
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


def _finish_variant(
    *,
    request: WorkflowRequest,
    scientific: FinalizedScientificAlignment,
) -> FinalizedTargetApplicationResult | FinalizedUntargetApplicationResult:
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
            return result
        untarget_pre_cell = project_untarget_pre_cell(scientific)
        stage = WorkflowStage.CELL_DECISION
        from xic_extractor.alignment.cell_decision.owner import (
            _decide_fresh_group_cell_population,
        )

        untarget_decisions = _decide_fresh_group_cell_population(
            untarget_pre_cell,
            scientific,
        )
        stage = WorkflowStage.POST_CELL_PROJECTION
        untarget_projection = project_untarget_post_cell(
            untarget_pre_cell,
            untarget_decisions,
        )
        stage = WorkflowStage.APPLICATION_RESULT
        # No adapter callback intervenes between these owner completions.
        result = _finalize_fresh_untarget_result(
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
        return result
    except Exception as exc:
        raise _WorkflowStageFailure(stage, exc) from exc


def complete_analysis(
    request: WorkflowRequest,
    adapter: WorkflowAdapter,
    *,
    processing_owners: ProcessingOwnerReferences | None = None,
    completed_captures: tuple[SampleLocalCapture, ...] | None = None,
) -> FinalizedTargetApplicationResult | FinalizedUntargetApplicationResult:
    """Complete acquired evidence without invoking runtime or persistence.

    Execution may validate immutable inputs before runtime and forward those
    exact processing owners here after success. Other callers validate here.
    """
    owners = (
        validate_completion_inputs(request, adapter)
        if processing_owners is None
        else processing_owners
    )
    return _compose_application_result(
        request,
        adapter,
        owners=owners,
        completed_captures=(
            adapter.completed_captures
            if completed_captures is None and type(adapter) is TargetWorkflowAdapter
            else completed_captures
        ),
    )


def complete_untarget_batch(
    request: UntargetWorkflowCompositionRequest,
    bundles: tuple[UntargetWorkflowBundle, ...],
) -> FinalizedUntargetBatchApplicationResult:
    """Close the exact subject population without a runtime terminal wrapper."""
    _validate_untarget_batch_composition_request(request)
    try:
        _validate_untarget_batch_bundles(request, bundles)
        children: list[FinalizedUntargetApplicationResult] = []
        for bundle in bundles:
            child = complete_analysis(bundle.request, bundle.adapter)
            if type(child) is not FinalizedUntargetApplicationResult:
                raise AlignmentWorkflowContractError(
                    "Untarget batch child returned a crossed application result"
                )
            children.append(child)
        application_result = finalize_alignment_result(
            UntargetBatchApplicationResultRequest(
                scope=request.scope,
                processing_request=request.processing_request,
                processing_attempt=request.processing_attempt,
                children=tuple(children),
                excluded_candidates=request.excluded_candidates,
            )
        )
        if type(application_result) is not FinalizedUntargetBatchApplicationResult:
            raise AlignmentWorkflowContractError(
                "application_result returned a crossed Untarget batch result"
            )
        return application_result
    except _WorkflowStageFailure:
        raise
    except Exception as exc:
        raise _WorkflowStageFailure(WorkflowStage.APPLICATION_RESULT, exc) from exc


__all__ = (
    "AlignmentWorkflowContractError",
    "CaptureWorkflowArguments",
    "TargetWorkflowAdapter",
    "TargetWorkflowRequest",
    "UntargetWorkflowAdapter",
    "UntargetWorkflowBundle",
    "UntargetWorkflowCompositionRequest",
    "WorkflowAdapter",
    "WorkflowRequest",
    "WorkflowStage",
    "complete_analysis",
    "complete_untarget_batch",
    "compose_untarget_subject",
    "validate_completion_inputs",
)
