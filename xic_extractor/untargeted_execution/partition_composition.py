"""Compose acquired Untargeted partitions and close their canonical batch result."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import cast

from xic_extractor.alignment.backfill.completion_evidence import (
    BackfillCompletionProjection,
)
from xic_extractor.alignment.cid_nl_completion import (
    CidNlWorksetCompletion,
    CidNlWorksetEvidence,
    CidNlWorksetGrouping,
    prepare_cid_nl_workset,
)
from xic_extractor.alignment.completion import (
    CaptureWorkflowArguments,
    UntargetWorkflowCompositionRequest,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSampleNeighborhoodEdge,
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.drift_evidence import KnownIstdReference
from xic_extractor.alignment.feature_membership_inference import (
    FeatureInferenceOperationCounts,
)
from xic_extractor.alignment.grouping_workflow import (
    build_initial_cid_nl_cross_sample_groups,
    regroup_cid_nl_cross_sample_groups,
)
from xic_extractor.alignment.hypothesis_owner_adapter import (
    compose_peak_hypotheses_from_assessment_universe,
)
from xic_extractor.alignment.result.application_result import (
    FinalizedUntargetApplicationResult,
    FinalizedUntargetBatchApplicationResult,
    UntargetBatchApplicationResultRequest,
    UntargetedCandidateExclusion,
    finalize_alignment_result,
)
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.alignment.sample_rt_calibration import BatchRtCalibrationAccumulator
from xic_extractor.configuration.execution_profile import DEFAULT_ALIGNMENT_WORKERS
from xic_extractor.discovery.models import (
    MS1_FEATURE_BACKED_STATES,
)
from xic_extractor.execution.result_store import (
    SealedPartition,
    StreamedResultStore,
)
from xic_extractor.immutable_sequence import ImmutableSequence
from xic_extractor.peak_detection.capture import (
    SampleLocalCapture,
    bind_owner_backed_capture_hypothesis,
    mint_captured_peak_observation,
    rebind_untargeted_capture_scope,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
    is_unsearched_cid_nl_discovery_assessment,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CID_NL_PROVIDER_ID,
    UntargetedCidNlMethodSubjectBatch,
)
from xic_extractor.peak_detection.observations import RetainedCaptureEvidence
from xic_extractor.peak_detection.search_scope import (
    SEARCH_SCOPE_SCHEMA_VERSION,
    CaptureCandidateBinding,
    CaptureMethodConstraints,
    EvidenceProviderBinding,
    UntargetedSearchScope,
    require_capture_binding,
)
from xic_extractor.untargeted_execution.input_planning import (
    UntargetedInputPlan,
    alignment_subject_worksets,
)
from xic_extractor.untargeted_execution.partition.models import (
    UntargetedInitialSampleResult,
    UntargetedPartitionJob,
    UntargetedPreparedGrouping,
    UntargetedSampleResult,
    _SampleResultContext,
)
from xic_extractor.untargeted_execution.partition.request import (
    UntargetedPartitionContext,
)
from xic_extractor.untargeted_execution.physical_comparison_context import (
    comparison_views_from_acquisitions,
)


@dataclass(frozen=True, slots=True)
class UntargetedPartitionInputs:
    """Partition inputs, executable jobs, and optional sparse backfill templates."""

    subject_batch: UntargetedCidNlMethodSubjectBatch
    context: UntargetedPartitionContext
    jobs: tuple[UntargetedPartitionJob, ...]
    sample_job_templates: tuple[UntargetedPartitionJob, ...] = ()
    prepared_grouping: UntargetedPreparedGrouping | None = None


class UntargetedBatchGroupingPreparation:
    """Shared in-memory preparation for direct and spooled batch execution.

    Callers supply initial groupings, freeze calibration, then finalize each
    grouping against that frozen calibration. Only compact anchor projections
    are retained; RAW access, partition admission and spooling remain with
    runtime.
    """

    def __init__(self, context: UntargetedPartitionContext) -> None:
        self._bootstrap_frame = context.rt_frame
        self._calibration = BatchRtCalibrationAccumulator(
            sample_stems=tuple(
                self._bootstrap_frame.sample_universe
                or self._bootstrap_frame.injection_order
            ),
            sample_cohorts=self._bootstrap_frame.sample_cohorts,
        )
        self._rt_frame: SampleRtFrame | None = None

    def add_initial(self, prepared: UntargetedPreparedGrouping) -> None:
        self._calibration.add_groups(prepared.rt_anchor_groups)
        self._bootstrap_frame = self._bootstrap_frame.with_sources(
            known_istd_reference=prepared.known_istd_reference
        )

    def freeze_calibration(self) -> None:
        self._rt_frame = self._bootstrap_frame.with_sample_calibration(
            self._calibration.freeze(
                measured_istds=self._bootstrap_frame.measured_istd_coordinates
            )
        )

    def finalize_grouping(
        self,
        batch: UntargetedPartitionInputs,
        prepared: UntargetedPreparedGrouping,
        *,
        progress_callback: Callable[[Mapping[str, object]], None] | None = None,
    ) -> UntargetedPreparedGrouping:
        assert self._rt_frame is not None
        return finalize_untargeted_partition_grouping(
            batch,
            prepared,
            rt_frame=self._rt_frame,
            progress_callback=progress_callback,
        )


@dataclass(frozen=True, slots=True)
class UntargetedPartitionComposition:
    """Subject-local scientific closure without a whole-runtime completion claim."""

    children: tuple[FinalizedUntargetApplicationResult, ...]
    excluded_candidates: tuple[UntargetedCandidateExclusion, ...]
    capture_bindings: tuple[CaptureCandidateBinding, ...]
    method_assessments: tuple[CidNlMethodAssessmentRef, ...]
    initial_groups: tuple[CrossSamplePeakGroupHypothesis, ...]
    initial_edges: tuple[CrossSampleNeighborhoodEdge, ...]
    capture_evidence: tuple[RetainedCaptureEvidence, ...] = ()


@dataclass(frozen=True, slots=True)
class PersistedUntargetedPartition:
    """Lightweight positions for one persisted scientific partition."""

    sealed: SealedPartition
    child_count: int
    excluded_candidate_ids: tuple[str, ...]
    child_order_keys: tuple[str, ...] = ()


def compose_untargeted_batch(
    plan: UntargetedInputPlan,
    *,
    store: StreamedResultStore,
    partitions: Sequence[PersistedUntargetedPartition],
) -> FinalizedUntargetBatchApplicationResult:
    """Close the planned batch with canonical, lazily loaded partition results.

    Partition composition owns scientific ordering and the complete batch scope;
    the runtime adapter supplies sealed partitions without reconstructing either.
    Alignment remains the authority for final scientific coverage and validation.
    """

    context = plan.context
    owners = context.processing_owners
    exclusion_positions: list[tuple[str, int, int]] = []
    child_positions: list[tuple[str, int, int]] = []
    for persisted in partitions:
        child_positions.extend(
            (key, persisted.sealed.index, offset)
            for offset, key in enumerate(persisted.child_order_keys)
        )
        exclusion_positions.extend(
            (candidate_id, persisted.sealed.index, offset)
            for offset, candidate_id in enumerate(persisted.excluded_candidate_ids)
        )
    scope = UntargetedSearchScope(
        schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
        request=owners.request,
        samples=context.samples,
        method_constraints=CaptureMethodConstraints(
            boundary_policy="resolver_interval",
            peak_config=context.peak_config,
            signal_processing_profile=context.signal_processing_profile,
        ),
        evidence_providers=tuple(
            EvidenceProviderBinding(CID_NL_PROVIDER_ID, subject.method_subject_id)
            for subject in plan.subject_batch.subjects
        ),
        declared_candidate_ids=tuple(
            candidate_id for candidate_id, _ in plan.subject_batch.candidate_memberships
        ),
        # Batch closure checks request membership and exact child owners.
        # Capture authorization and physical evidence belong to each child.
        capture_bindings=(),
    )
    children = store.sequence("children")
    if child_positions:
        ordered_children = tuple(sorted(child_positions))
        if len(ordered_children) != len(children):
            raise ValueError("partition child order lost a scientific result")
        children = ImmutableSequence(
            (1,) * len(ordered_children),
            lambda index: store.read_selected(
                ordered_children[index][1],
                "children",
                (ordered_children[index][2],),
            ),
        )
    return cast(
        FinalizedUntargetBatchApplicationResult,
        finalize_alignment_result(
            UntargetBatchApplicationResultRequest(
                scope=scope,
                processing_request=owners.request,
                processing_attempt=owners.attempt,
                children=children,
                excluded_candidates=_ordered_exclusions(
                    store, exclusion_positions, plan.excluded_candidates
                ),
            )
        ),
    )


def _ordered_exclusions(
    store: StreamedResultStore,
    positions: list[tuple[str, int, int]],
    planning_exclusions: tuple[UntargetedCandidateExclusion, ...],
) -> ImmutableSequence:
    """Restore canonical candidate order with finite result windows."""
    all_positions = list(positions)
    # Planning exclusions have no acquisition partition or assessment payload.
    all_positions.extend(
        (item.candidate_id, -1, offset)
        for offset, item in enumerate(planning_exclusions)
    )
    ordered = tuple(sorted(all_positions))
    windows = tuple(
        ordered[start : start + 2048] for start in range(0, len(ordered), 2048)
    )

    def load_window(index: int) -> tuple:
        window = windows[index]
        by_partition: dict[int, list[tuple[int, int]]] = {}
        for position, (_, partition, offset) in enumerate(window):
            by_partition.setdefault(partition, []).append((position, offset))
        values: dict[int, UntargetedCandidateExclusion] = {}
        for partition, selected in by_partition.items():
            exclusions = (
                tuple(planning_exclusions[offset] for _, offset in selected)
                if partition == -1
                else store.read_selected(
                    partition,
                    "excluded_candidates",
                    tuple(offset for _, offset in selected),
                )
            )
            for (position, _), exclusion in zip(selected, exclusions, strict=True):
                values[position] = exclusion
        return tuple(values[position] for position in range(len(window)))

    return ImmutableSequence(tuple(map(len, windows)), load_window)


def compose_and_persist_untargeted_partition(
    batch: UntargetedPartitionInputs,
    sample_results: tuple[UntargetedSampleResult, ...],
    *,
    store: StreamedResultStore,
    partition_key: str,
    alignment_config: AlignmentConfig,
    known_istd_reference: KnownIstdReference | None = None,
    should_cancel: Callable[[], object] | None = None,
    retain_memory: bool = False,
    raw_workers: int = DEFAULT_ALIGNMENT_WORKERS,
) -> PersistedUntargetedPartition:
    """Compose one child at a time and publish only after the full partition.

    Composition inputs are prepared once.  The result store owns the trusted
    owner references and atomic file publication while this owner controls
    scientific child order and cancellation boundaries.
    """

    inputs = _build_production_composition_inputs(
        batch,
        sample_results,
        alignment_config=alignment_config,
        known_istd_reference=known_istd_reference,
    )
    child_count = 0
    child_order_keys: list[str] = []

    def check_cancellation() -> None:
        if should_cancel is not None and should_cancel():
            raise InterruptedError("CID-NL partition cancelled")

    def children():
        nonlocal child_count
        for completion, receipts in inputs.completions:
            check_cancellation()
            child = completion.complete(receipts)
            child_count += 1
            child_order_keys.append(
                min(
                    provider.method_subject_id
                    for provider in child.scientific_core.scope.evidence_providers
                )
            )
            yield child
            del child

    metadata_payload = UntargetedPartitionComposition(
        children=(),
        excluded_candidates=inputs.request.excluded_candidates,
        # Child scopes and capture_evidence retain the complete physical facts.
        # The partition index does not need a second copy of their graphs.
        capture_bindings=(),
        method_assessments=inputs.method_assessments,
        initial_groups=inputs.initial_groups,
        initial_edges=inputs.initial_edges,
        capture_evidence=inputs.capture_evidence,
    )
    sealed = store.append_children(
        partition_key,
        children=children(),
        before_publish=check_cancellation,
        metadata_payload=metadata_payload,
        retain_memory=retain_memory,
    )
    return PersistedUntargetedPartition(
        sealed=sealed,
        child_count=child_count,
        excluded_candidate_ids=tuple(
            item.candidate_id for item in inputs.request.excluded_candidates
        ),
        child_order_keys=tuple(child_order_keys),
    )


@dataclass(frozen=True, slots=True)
class _ProductionCompositionInputs:
    request: UntargetWorkflowCompositionRequest
    completions: tuple[
        tuple[CidNlWorksetCompletion, tuple[BackfillCompletionProjection, ...]], ...
    ]
    method_assessments: tuple[CidNlMethodAssessmentRef, ...]
    initial_groups: tuple[CrossSamplePeakGroupHypothesis, ...]
    initial_edges: tuple[CrossSampleNeighborhoodEdge, ...]
    capture_evidence: tuple[RetainedCaptureEvidence, ...] = ()


def compose_untargeted_partition(
    batch: UntargetedPartitionInputs,
    sample_results: tuple[UntargetedSampleResult, ...],
    *,
    alignment_config: AlignmentConfig,
    known_istd_reference: KnownIstdReference | None = None,
) -> UntargetedPartitionComposition:
    """Compose complete subject partitions before global runtime finalization."""
    inputs = _build_production_composition_inputs(
        batch,
        sample_results,
        alignment_config=alignment_config,
        known_istd_reference=known_istd_reference,
    )
    return UntargetedPartitionComposition(
        children=tuple(
            completion.complete(receipts) for completion, receipts in inputs.completions
        ),
        excluded_candidates=inputs.request.excluded_candidates,
        capture_bindings=inputs.request.scope.capture_bindings,
        method_assessments=inputs.method_assessments,
        initial_groups=inputs.initial_groups,
        initial_edges=inputs.initial_edges,
        capture_evidence=inputs.capture_evidence,
    )


def prepare_untargeted_partition_grouping(
    batch: UntargetedPartitionInputs,
    sample_results: tuple[UntargetedInitialSampleResult, ...],
    *,
    alignment_config: AlignmentConfig,
    bootstrap_only: bool = False,
    known_istd_reference: KnownIstdReference | None = None,
    progress_callback: Callable[[Mapping[str, object]], None] | None = None,
) -> UntargetedPreparedGrouping:
    """Freeze compatible-workset membership once, before any backfill search."""
    requested_reference = known_istd_reference
    # A sample acquisition is shared by all of its method-subject results.
    # Flatten each retained context batch once, outside the workset loop.
    context_batches = {
        id(
            result.adapter_result.elution_contexts
        ): result.adapter_result.elution_contexts
        for result in sample_results
    }
    elution_contexts = tuple(
        context for contexts in context_batches.values() for context in contexts
    )
    results_by_subject: dict[str, list[UntargetedInitialSampleResult]] = {}
    for result in sample_results:
        results_by_subject.setdefault(
            result.job.method_subject.method_subject_id, []
        ).append(result)
    for results in results_by_subject.values():
        results.sort(key=lambda result: result.job.sample.identity.sample_key)
    capture_by_candidate = _runtime_captures_by_candidate(batch, sample_results)
    candidate_by_id = {
        candidate.candidate_id: candidate
        for candidates in batch.context.candidates_by_sample.values()
        for candidate in candidates
    }
    unsupported_subjects = {}
    for subject in batch.subject_batch.subjects:
        subject_assessments = tuple(
            assessment
            for result in results_by_subject.get(subject.method_subject_id, ())
            for assessment in result.effective_assessments
        )
        evaluated_unsupported = tuple(
            ref.content.technical_state == "evaluated"
            and ref.content.method_result in {"HOLD", "REJECT"}
            for ref in subject_assessments
        )
        if any(evaluated_unsupported) and all(
            is_unsupported or is_unsearched_cid_nl_discovery_assessment(ref)
            for ref, is_unsupported in zip(
                subject_assessments, evaluated_unsupported, strict=True
            )
        ):
            unsupported_subjects[subject.method_subject_id] = subject_assessments
    bindings = tuple(
        require_capture_binding(
            capture_by_candidate[candidate_id].scope,
            candidate_id,
        )
        for candidate_id, method_subject_id in batch.subject_batch.candidate_memberships
        if candidate_by_id[candidate_id].discovery_candidate_state
        in MS1_FEATURE_BACKED_STATES
        and method_subject_id not in unsupported_subjects
    )
    exclusions = tuple(
        UntargetedCandidateExclusion(
            candidate_id=candidate_id,
            method_subject_id=method_subject_id,
            sample_key=batch.context.sample(
                candidate_by_id[candidate_id].sample_stem
            ).identity.sample_key,
            discovery_candidate_state=(
                candidate_by_id[candidate_id].discovery_candidate_state
            ),
            reason_code=(
                "composite_requirement_not_supported"
                if method_subject_id in unsupported_subjects
                and unsupported_subjects[method_subject_id][
                    0
                ].content.method_subject.composition
                is not None
                else "cid_nl_requirement_not_supported"
                if method_subject_id in unsupported_subjects
                else "discovery_candidate_not_search_eligible"
            ),
            composite_assessments=tuple(
                ref
                for ref in unsupported_subjects.get(method_subject_id, ())
                if ref.content.method_subject.composition is not None
            ),
            method_assessments=tuple(
                ref
                for ref in unsupported_subjects.get(method_subject_id, ())
                if ref.content.method_subject.composition is None
            ),
        )
        for candidate_id, method_subject_id in (
            batch.subject_batch.candidate_memberships
        )
        if candidate_by_id[candidate_id].discovery_candidate_state
        not in MS1_FEATURE_BACKED_STATES
        or method_subject_id in unsupported_subjects
    )
    # Partitioning changes execution ownership. Scientific composition keeps
    # the global request scope and closes the exact subject views below.
    request_batch = cast(
        UntargetedCidNlMethodSubjectBatch, batch.context.method_subject
    )
    scope = UntargetedSearchScope(
        schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
        request=batch.context.processing_owners.request,
        samples=batch.context.samples,
        method_constraints=CaptureMethodConstraints(
            boundary_policy="resolver_interval",
            peak_config=batch.context.peak_config,
            signal_processing_profile=batch.context.signal_processing_profile,
        ),
        evidence_providers=tuple(
            EvidenceProviderBinding(
                provider_id=CID_NL_PROVIDER_ID,
                method_subject_id=subject.method_subject_id,
            )
            for subject in request_batch.subjects
        ),
        declared_candidate_ids=tuple(
            candidate_id
            for candidate_id, _method_subject_id in (
                request_batch.candidate_memberships
            )
        ),
        capture_bindings=bindings,
    )
    parent_request = UntargetWorkflowCompositionRequest(
        scope=scope,
        processing_request=batch.context.processing_owners.request,
        processing_attempt=batch.context.processing_owners.attempt,
        excluded_candidates=exclusions,
    )

    prepared_worksets = []
    bindings_by_subject: dict[str, list[CaptureCandidateBinding]] = {}
    for binding in bindings:
        bindings_by_subject.setdefault(binding.method_subject_id, []).append(binding)
    sample_by_id = {
        sample.identity.sample_key: sample for sample in batch.context.samples
    }
    sample_universe = frozenset(
        sample.metadata.raw_stem
        for sample in batch.context.processing_owners.sample_resolutions
    )
    rt_frame = batch.context.rt_frame
    if known_istd_reference is not None:
        known_istd_reference = known_istd_reference.prepare_batch(
            sample_universe=sample_universe,
            injection_order=rt_frame.injection_order,
        )
    rt_frame = rt_frame.with_sources(known_istd_reference=known_istd_reference)
    active_subjects = tuple(
        subject
        for subject in batch.subject_batch.subjects
        if bindings_by_subject.get(subject.method_subject_id)
    )
    for subject_workset in alignment_subject_worksets(
        active_subjects,
        known_istd_reference=known_istd_reference,
    ):
        child_bindings = tuple(
            sorted(
                (
                    binding
                    for subject in subject_workset
                    for binding in bindings_by_subject[subject.method_subject_id]
                ),
                key=lambda binding: binding.candidate_id,
            )
        )
        child_scope = UntargetedSearchScope(
            schema_version=scope.schema_version,
            request=scope.request,
            samples=scope.samples,
            method_constraints=scope.method_constraints,
            evidence_providers=tuple(
                EvidenceProviderBinding(
                    provider_id=CID_NL_PROVIDER_ID,
                    method_subject_id=subject.method_subject_id,
                )
                for subject in subject_workset
            ),
            declared_candidate_ids=tuple(
                binding.candidate_id for binding in child_bindings
            ),
            capture_bindings=child_bindings,
        )
        assessments = tuple(
            (
                result.job.sample,
                assessment,
            )
            for subject in subject_workset
            for result in results_by_subject.get(subject.method_subject_id, ())
            for assessment in result.effective_assessments
        )
        assessments = tuple(
            sorted(assessments, key=lambda pair: pair[0].identity.sample_key)
        )
        capture_inputs_list = []
        captures_list = []
        assessment_by_sample_subject = {
            (
                result.job.sample.identity.sample_key,
                subject.method_subject_id,
            ): result.adapter_result.assessment_input.assessment
            for subject in subject_workset
            for result in results_by_subject.get(subject.method_subject_id, ())
        }
        hypotheses_by_sample_subject = {
            key: compose_peak_hypotheses_from_assessment_universe(
                processing_owners=batch.context.processing_owners,
                sample=sample_by_id[key[0]],
                assessment=assessment_by_sample_subject[key],
            )
            for key in dict.fromkeys(
                (binding.sample_key, binding.method_subject_id)
                for binding in child_bindings
            )
        }
        retained_hypotheses_by_assessment = {
            (
                key[0],
                assessment_by_sample_subject[
                    key
                ].artifact.content_identity.artifact_content_id,
            ): hypotheses
            for key, hypotheses in hypotheses_by_sample_subject.items()
        }
        for binding in child_bindings:
            sample = sample_by_id[binding.sample_key]
            trace = capture_by_candidate[binding.candidate_id].trace
            captured = rebind_untargeted_capture_scope(
                capture_by_candidate[binding.candidate_id],
                scope=child_scope,
            )
            if captured.selected_hypothesis is None:
                captures_list.append(captured)
                capture_inputs_list.append(
                    CaptureWorkflowArguments(
                        candidate_id=binding.candidate_id,
                        trace=trace,
                    )
                )
                continue
            observation = mint_captured_peak_observation(
                captured,
                processing_owners=batch.context.processing_owners,
                sample=sample,
            )
            hypotheses = hypotheses_by_sample_subject[
                (binding.sample_key, binding.method_subject_id)
            ]
            matches = tuple(
                hypothesis
                for hypothesis in hypotheses
                if observation in hypothesis.peak_observation_refs
            )
            if len(matches) != 1:
                raise RuntimeError(
                    "CID-NL runtime assessment lost its exact captured observation"
                )
            owner_capture = bind_owner_backed_capture_hypothesis(
                captured,
                observation=observation,
                hypothesis=matches[0],
            )
            captures_list.append(owner_capture)
            capture_inputs_list.append(
                CaptureWorkflowArguments(
                    candidate_id=binding.candidate_id,
                    trace=trace,
                    owner_observation=observation,
                    owner_hypothesis=matches[0],
                )
            )
        captures = tuple(captures_list)
        capture_inputs = tuple(capture_inputs_list)
        operation_counts: list[FeatureInferenceOperationCounts] = []
        (
            grouping_scope,
            grouping_result,
            retained_candidates,
        ) = build_initial_cid_nl_cross_sample_groups(
            processing_owners=batch.context.processing_owners,
            captures=captures,
            sample_assessments=assessments,
            alignment_config=alignment_config,
            elution_contexts=elution_contexts,
            rt_frame=rt_frame,
            retained_hypotheses_by_assessment=(retained_hypotheses_by_assessment),
            operation_counts_sink=operation_counts,
        )
        prepared_worksets.append(
            CidNlWorksetGrouping(
                subjects=subject_workset,
                scope=child_scope,
                captures=captures,
                capture_inputs=capture_inputs,
                grouping_scope=grouping_scope,
                grouping_result=grouping_result,
                retained_candidates=retained_candidates,
            )
        )
    prepared = UntargetedPreparedGrouping(
        request=parent_request,
        worksets=tuple(prepared_worksets),
        alignment_config=alignment_config,
        known_istd_reference=requested_reference,
        rt_frame=rt_frame,
    )
    if bootstrap_only:
        return prepared
    preparation = UntargetedBatchGroupingPreparation(batch.context)
    preparation.add_initial(prepared)
    preparation.freeze_calibration()
    return preparation.finalize_grouping(
        batch,
        prepared,
        progress_callback=progress_callback,
    )


def finalize_untargeted_partition_grouping(
    batch: UntargetedPartitionInputs,
    prepared: UntargetedPreparedGrouping,
    *,
    rt_frame: SampleRtFrame,
    progress_callback: Callable[[Mapping[str, object]], None] | None = None,
) -> UntargetedPreparedGrouping:
    """Regroup one bounded workset collection using the frozen batch calibration."""
    alignment_config = prepared.alignment_config
    final_worksets = []
    for workset in prepared.worksets:
        operation_counts: list[FeatureInferenceOperationCounts] = []
        grouping_scope, grouping_result = regroup_cid_nl_cross_sample_groups(
            processing_owners=batch.context.processing_owners,
            grouping_scope=workset.grouping_scope,
            grouping_result=workset.grouping_result,
            alignment_config=alignment_config,
            rt_frame=workset.grouping_result.rt_frame.with_calibration(rt_frame),
            operation_counts_sink=operation_counts,
        )
        final_worksets.append(
            replace(
                workset,
                grouping_scope=grouping_scope,
                grouping_result=grouping_result,
            )
        )
        if progress_callback is not None:
            progress_callback(
                {
                    "event": "feature_membership_inference",
                    "stage": "feature_membership_inference",
                    "subject_count": len(workset.subjects),
                    "group_count": len(grouping_scope.groups),
                    "operation_counts": {
                        key: (
                            max(
                                (getattr(item, key) for item in operation_counts),
                                default=0,
                            )
                            if key.startswith("largest_")
                            else sum(getattr(item, key) for item in operation_counts)
                        )
                        for key in FeatureInferenceOperationCounts.__dataclass_fields__
                    },
                }
            )
    return UntargetedPreparedGrouping(
        request=prepared.request,
        worksets=tuple(final_worksets),
        alignment_config=alignment_config,
        known_istd_reference=prepared.known_istd_reference,
        rt_frame=prepared.rt_frame.with_calibration(rt_frame),
    )


def _build_production_composition_inputs(
    batch: UntargetedPartitionInputs,
    sample_results: tuple[UntargetedSampleResult, ...],
    *,
    alignment_config: AlignmentConfig,
    known_istd_reference: KnownIstdReference | None,
) -> _ProductionCompositionInputs:
    if type(sample_results) is not tuple or any(
        type(result) is not UntargetedSampleResult for result in sample_results
    ):
        raise TypeError("sample_results must be an immutable typed runtime population")
    if not isinstance(alignment_config, AlignmentConfig):
        raise TypeError("alignment_config must be an AlignmentConfig")
    result_context = _SampleResultContext(
        tuple(result.job for result in sample_results)
    )
    result_by_key = {
        (
            result.job.sample.metadata.raw_stem,
            result.job.method_subject.method_subject_id,
        ): result
        for result in sample_results
    }
    jobs_by_key = {
        (job.sample.metadata.raw_stem, job.method_subject.method_subject_id): job
        for job in batch.jobs
    }
    subject_ids = {
        subject.method_subject_id for subject in batch.subject_batch.subjects
    }
    templates_by_stem = {
        job.sample.metadata.raw_stem: job for job in batch.sample_job_templates
    }
    extra_keys = set(result_by_key) - set(jobs_by_key)
    dense_keys = (
        {
            (sample.metadata.raw_stem, subject.method_subject_id)
            for sample in batch.context.samples
            for subject in batch.subject_batch.subjects
        }
        if not batch.sample_job_templates
        else set(jobs_by_key)
    )
    if (
        len(result_by_key) != len(sample_results)
        or len(jobs_by_key) != len(batch.jobs)
        or set(jobs_by_key) != dense_keys
        or not set(jobs_by_key) <= set(result_by_key)
        or any(result_by_key[key].job is not job for key, job in jobs_by_key.items())
        or any(
            key[0] not in templates_by_stem
            or key[1] not in subject_ids
            or result_by_key[key].job.sample is not templates_by_stem[key[0]].sample
            or result_by_key[key].job.processing_owners
            is not batch.context.processing_owners
            for key in extra_keys
        )
    ):
        raise RuntimeError(
            "CID-NL production runtime lost exact sample/subject results"
        )

    results_by_subject: dict[str, list[UntargetedSampleResult]] = {}
    for result in sample_results:
        results_by_subject.setdefault(
            result.job.method_subject.method_subject_id, []
        ).append(result)
    for results in results_by_subject.values():
        results.sort(key=lambda result: result.job.sample.identity.sample_key)
    final_method_assessments = [
        assessment
        for result in sample_results
        for assessment in result.effective_assessments
        if not is_unsearched_cid_nl_discovery_assessment(assessment)
    ]

    prepared = batch.prepared_grouping or prepare_untargeted_partition_grouping(
        batch,
        tuple(
            UntargetedInitialSampleResult(item.job, item.adapter_result)
            for item in sample_results
        ),
        alignment_config=alignment_config,
        known_istd_reference=known_istd_reference,
    )
    if prepared.alignment_config != alignment_config or (
        known_istd_reference is not None
        and prepared.known_istd_reference != known_istd_reference
    ):
        raise RuntimeError("grouping context must be supplied before backfill")
    completions = []
    for workset in prepared.worksets:
        subject_workset = workset.subjects
        assessments = tuple(
            (result.job.sample, assessment)
            for subject in subject_workset
            for result in results_by_subject.get(subject.method_subject_id, ())
            for assessment in result.effective_assessments
            if not is_unsearched_cid_nl_discovery_assessment(assessment)
        )
        subject_order = {
            subject.method_subject_id: index
            for index, subject in enumerate(subject_workset)
        }
        assessments = tuple(
            sorted(
                assessments,
                key=lambda pair: (
                    pair[0].identity.sample_key,
                    subject_order[pair[1].content.method_subject.method_subject_id],
                ),
            )
        )
        initial_assessments = tuple(
            (result.job.sample, assessment)
            for subject in subject_workset
            for result in results_by_subject.get(subject.method_subject_id, ())
            for assessment in (result.adapter_result.assessment_input.assessment,)
            if not is_unsearched_cid_nl_discovery_assessment(assessment)
        )
        group_backfills = {
            (
                group.group_hypothesis_id,
                sample_result.job.sample.identity.sample_key,
                sample_result.job.method_subject.method_subject_id,
            ): (sample_result.job.sample, backfill.assessment)
            for subject in subject_workset
            for sample_result in results_by_subject.get(subject.method_subject_id, ())
            if sample_result.backfill_attempts
            for group in workset.grouping_scope.groups
            if group.group_center_rt is not None
            and (
                backfill := sample_result.backfill_for_group(
                    group.group_center_rt, group_hypothesis_id=group.group_hypothesis_id
                )
            )
            is not None
        }
        physical_peak_views = comparison_views_from_acquisitions(
            (assessment.candidate_peaks, acquisition)
            for subject in subject_workset
            for result in results_by_subject.get(subject.method_subject_id, ())
            for assessment, acquisition in (
                (
                    result.adapter_result.assessment_input.assessment,
                    result.adapter_result.acquisition,
                ),
                *(
                    (attempt.assessment, attempt.acquisition)
                    for attempt in result.backfill_attempts
                ),
            )
        )
        completion = prepare_cid_nl_workset(
            workset,
            CidNlWorksetEvidence(
                sample_assessments=assessments,
                initial_assessments=initial_assessments,
                group_backfills=group_backfills,
                physical_peak_views=physical_peak_views,
            ),
            samples=batch.context.samples,
            alignment_config=alignment_config,
        )
        receipts = tuple(
            result_context.project_completed_backfill(
                result_by_key[(sample.metadata.raw_stem, context.method_subject_id)],
                group_context=context,
            )
            for sample, context in completion.completion_requests
        )
        completions.append((completion, receipts))
    return _ProductionCompositionInputs(
        request=prepared.request,
        completions=tuple(completions),
        method_assessments=tuple(final_method_assessments),
        initial_groups=tuple(
            group
            for workset in prepared.worksets
            for group in workset.grouping_scope.groups
        ),
        initial_edges=(),
        capture_evidence=tuple(
            evidence
            for result in sample_results
            for evidence in (
                *result.adapter_result.capture_evidence,
                *(
                    item
                    for attempt in result.backfill_attempts
                    for item in attempt.capture_evidence
                ),
            )
        ),
    )


def _runtime_captures_by_candidate(
    batch: UntargetedPartitionInputs,
    sample_results: tuple[UntargetedInitialSampleResult, ...],
) -> dict[str, SampleLocalCapture]:
    captures: dict[str, SampleLocalCapture] = {}
    for result in sample_results:
        for capture in result.adapter_result.captures:
            if capture.candidate_id in captures:
                raise RuntimeError("runtime capture duplicated a candidate")
            captures[capture.candidate_id] = capture
    partition_ids = {key for key, _ in batch.subject_batch.candidate_memberships}
    expected = {
        candidate.candidate_id
        for candidates in batch.context.candidates_by_sample.values()
        for candidate in candidates
        if candidate.candidate_id in partition_ids
        and candidate.discovery_candidate_state in MS1_FEATURE_BACKED_STATES
    }
    if set(captures) != expected:
        raise RuntimeError("runtime capture does not close capture-eligible candidates")
    return captures
