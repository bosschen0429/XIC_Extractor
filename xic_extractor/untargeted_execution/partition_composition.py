"""Compose acquired Untargeted partitions and close their canonical batch result."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from xic_extractor.alignment.backfill.completion_evidence import (
    BackfillCompletionProjection,
)
from xic_extractor.alignment.cid_nl_completion import (
    CidNlWorksetCompletion,
    CidNlWorksetEvidence,
    prepare_cid_nl_workset,
)
from xic_extractor.alignment.cid_nl_initial_grouping import (
    CidNlBatchGrouping,
    CidNlGrouping,
    prepare_cid_nl_grouping,
)
from xic_extractor.alignment.completion import (
    UntargetWorkflowCompositionRequest,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSamplePeakGroupHypothesis,
    accepted_membership_risks,
)
from xic_extractor.alignment.drift_evidence import KnownIstdReference
from xic_extractor.alignment.membership_selection import AcceptedMembershipRisk
from xic_extractor.alignment.result.application_result import (
    FinalizedUntargetApplicationResult,
    FinalizedUntargetBatchApplicationResult,
    UntargetedCandidateExclusion,
)
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.execution.result_store import (
    SealedPartition,
    StreamedResultStore,
)
from xic_extractor.immutable_sequence import ImmutableSequence
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessment,
    is_unsearched_cid_nl_discovery_assessment,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CID_NL_PROVIDER_ID,
    UntargetedCidNlMethodSubjectBatch,
)
from xic_extractor.peak_detection.observations import (
    PeakObservation,
    RetainedCaptureEvidence,
)
from xic_extractor.peak_detection.search_scope import (
    SEARCH_SCOPE_SCHEMA_VERSION,
    CaptureCandidateBinding,
    EvidenceProviderBinding,
    UntargetedSearchScope,
)
from xic_extractor.sample_metadata import SampleMetadata
from xic_extractor.untargeted_execution.input_planning import (
    UntargetedInputPlan,
)
from xic_extractor.untargeted_execution.partition.models import (
    UntargetedInitialSampleResult,
    UntargetedPartitionJob,
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
    prepared_grouping: CidNlGrouping | None = None


@dataclass(frozen=True, slots=True)
class UntargetedPartitionComposition:
    """Subject-local scientific closure without a whole-runtime completion claim."""

    children: tuple[FinalizedUntargetApplicationResult, ...]
    excluded_candidates: tuple[UntargetedCandidateExclusion, ...]
    capture_bindings: tuple[CaptureCandidateBinding, ...]
    method_assessments: tuple[CidNlMethodAssessment, ...]
    initial_groups: tuple[CrossSamplePeakGroupHypothesis, ...]
    capture_evidence: tuple[RetainedCaptureEvidence, ...] = ()


@dataclass(frozen=True, slots=True)
class ChildProjectionContext:
    """Batch and partition facts a child's output projection reads besides the
    child: the batch's samples, which order every row's sample columns."""

    samples: tuple[SampleMetadata, ...]
    membership_risks_by_owner: Mapping[str, AcceptedMembershipRisk]
    sample_rt_frame: SampleRtFrame | None
    capture_evidence_retained: bool


ChildOutputProjection = Callable[
    [FinalizedUntargetApplicationResult, ChildProjectionContext], object
]


@dataclass(frozen=True, slots=True)
class _PersistedPartitionComposition:
    """Stored partition: projected child outputs beside the retained facts."""

    child_outputs: tuple[object, ...]
    excluded_candidates: tuple[UntargetedCandidateExclusion, ...]
    method_assessments: tuple[CidNlMethodAssessment, ...]
    initial_groups: tuple[CrossSamplePeakGroupHypothesis, ...]
    capture_evidence: tuple[RetainedCaptureEvidence, ...] = ()


@dataclass(frozen=True, slots=True)
class PersistedUntargetedPartition:
    """Lightweight positions for one persisted scientific partition."""

    sealed: SealedPartition
    child_count: int
    excluded_candidate_ids: tuple[str, ...]
    child_order_keys: tuple[str, ...] = ()
    has_flagged_backfill_risk: bool = False


def compose_untargeted_batch(
    plan: UntargetedInputPlan,
    *,
    store: StreamedResultStore,
    partitions: Sequence[PersistedUntargetedPartition],
) -> FinalizedUntargetBatchApplicationResult:
    """Close the planned batch over its partitions' stored child outputs.

    Partition composition owns scientific ordering and the complete batch scope;
    the runtime adapter supplies sealed partitions without reconstructing either.
    Alignment remains the authority for final scientific coverage and validation.
    """

    context = plan.context
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
        method_subjects=context.method_subject,
        samples=context.samples,
        evidence_providers=tuple(
            EvidenceProviderBinding(CID_NL_PROVIDER_ID, subject.method_subject_id)
            for subject in plan.subject_batch.subjects
        ),
        declared_candidate_ids=tuple(
            candidate_id for candidate_id, _ in plan.subject_batch.candidate_memberships
        ),
        capture_bindings=(),
    )
    child_outputs = store.sequence("child_outputs")
    if child_positions:
        ordered_outputs = tuple(sorted(child_positions))
        if len(ordered_outputs) != len(child_outputs):
            raise ValueError("partition child order lost a projected child")
        child_outputs = ImmutableSequence(
            (1,) * len(ordered_outputs),
            lambda index: store.read_selected(
                ordered_outputs[index][1],
                "child_outputs",
                (ordered_outputs[index][2],),
            ),
        )
    return FinalizedUntargetBatchApplicationResult(
        scope=scope,
        processing_attempt_id=context.processing_attempt_id,
        acquisition_sources=tuple(
            context.acquisition_sources_by_sample[sample.raw_stem].source
            for sample in context.samples
        ),
        child_outputs=child_outputs,
        excluded_candidates=_ordered_exclusions(
            store, exclusion_positions, plan.excluded_candidates
        ),
        has_flagged_backfill_risk=any(
            persisted.has_flagged_backfill_risk for persisted in partitions
        ),
    )


def _ordered_exclusions(
    store: StreamedResultStore,
    positions: list[tuple[str, int, int]],
    planning_exclusions: tuple[UntargetedCandidateExclusion, ...],
) -> ImmutableSequence:
    """Restore canonical peak order with finite result windows."""
    all_positions = list(positions)
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
    project_child: ChildOutputProjection,
    sample_rt_frame: SampleRtFrame | None = None,
    known_istd_reference: KnownIstdReference | None = None,
    should_cancel: Callable[[], object] | None = None,
    retain_memory: bool = False,
) -> PersistedUntargetedPartition:
    """Compose one child at a time and publish only after the full partition.

    Composition inputs are prepared once. Each child is projected into its
    outputs as soon as it composes, and only the projection is stored. The
    result store owns the trusted owner references and atomic file
    publication while this owner controls scientific child order and
    cancellation boundaries.
    """

    inputs = _build_production_composition_inputs(
        batch,
        sample_results,
        alignment_config=alignment_config,
        known_istd_reference=known_istd_reference,
    )
    child_count = 0
    child_order_keys: list[str] = []
    has_flagged_backfill_risk = False
    projection_context = ChildProjectionContext(
        samples=batch.context.samples,
        membership_risks_by_owner=accepted_membership_risks(inputs.initial_groups),
        sample_rt_frame=sample_rt_frame,
        capture_evidence_retained=bool(inputs.capture_evidence),
    )

    def check_cancellation() -> None:
        if should_cancel is not None and should_cancel():
            raise InterruptedError("CID-NL partition cancelled")

    def child_outputs():
        nonlocal child_count, has_flagged_backfill_risk
        for completion, receipts in inputs.completions:
            check_cancellation()
            child = completion.complete(receipts)
            child_count += 1
            child_order_keys.append(child.first_method_subject_id)
            has_flagged_backfill_risk = has_flagged_backfill_risk or any(
                risk.risk_flags
                for _, _, risk in (
                    child.scientific_core.grouping_result.backfill_membership_risks
                )
            )
            yield project_child(child, projection_context)
            del child

    metadata_payload = _PersistedPartitionComposition(
        child_outputs=(),
        excluded_candidates=inputs.request.excluded_candidates,
        method_assessments=inputs.method_assessments,
        initial_groups=inputs.initial_groups,
        capture_evidence=inputs.capture_evidence,
    )
    sealed = store.append_child_outputs(
        partition_key,
        child_outputs=child_outputs(),
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
        has_flagged_backfill_risk=has_flagged_backfill_risk,
    )


@dataclass(frozen=True, slots=True)
class _ProductionCompositionInputs:
    request: UntargetWorkflowCompositionRequest
    completions: tuple[
        tuple[CidNlWorksetCompletion, tuple[BackfillCompletionProjection, ...]], ...
    ]
    method_assessments: tuple[CidNlMethodAssessment, ...]
    initial_groups: tuple[CrossSamplePeakGroupHypothesis, ...]
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
        capture_evidence=inputs.capture_evidence,
    )


def group_acquired_partition(
    batch: UntargetedPartitionInputs,
    sample_results: tuple[UntargetedInitialSampleResult, ...],
    *,
    alignment_config: AlignmentConfig,
    bootstrap_only: bool = False,
    known_istd_reference: KnownIstdReference | None = None,
    progress_callback: Callable[[Mapping[str, object]], None] | None = None,
) -> CidNlGrouping:
    """Hand acquired in-memory facts to Alignment, retaining no eligibility policy."""
    context_batches = {
        id(
            result.adapter_result.elution_contexts
        ): result.adapter_result.elution_contexts
        for result in sample_results
    }
    prepared = prepare_cid_nl_grouping(
        subject_batch=batch.subject_batch,
        request_subjects=cast(
            UntargetedCidNlMethodSubjectBatch, batch.context.method_subject
        ),
        samples=batch.context.samples,
        candidates=tuple(
            peak for peaks in batch.context.peaks_by_sample.values() for peak in peaks
        ),
        observation_by_candidate=_runtime_observations_by_candidate(
            batch, sample_results
        ),
        sample_assessments=tuple(
            (result.job.sample, assessment)
            for result in sample_results
            for assessment in result.effective_assessments
        ),
        alignment_config=alignment_config,
        rt_frame=batch.context.rt_frame,
        elution_contexts=tuple(
            context for contexts in context_batches.values() for context in contexts
        ),
        known_istd_reference=known_istd_reference,
    )
    if bootstrap_only:
        return prepared
    preparation = CidNlBatchGrouping(batch.context.rt_frame)
    preparation.add_initial(prepared)
    preparation.freeze_calibration()
    return preparation.finalize_grouping(prepared, progress_callback=progress_callback)


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
            result.job.sample.raw_stem,
            result.job.method_subject.method_subject_id,
        ): result
        for result in sample_results
    }
    jobs_by_key = {
        (job.sample.raw_stem, job.method_subject.method_subject_id): job
        for job in batch.jobs
    }
    subject_ids = {
        subject.method_subject_id for subject in batch.subject_batch.subjects
    }
    templates_by_stem = {job.sample.raw_stem: job for job in batch.sample_job_templates}
    extra_keys = set(result_by_key) - set(jobs_by_key)
    dense_keys = (
        {
            (sample.raw_stem, subject.method_subject_id)
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
        results.sort(key=lambda result: result.job.sample.sample_name)
    final_method_assessments = [
        assessment
        for result in sample_results
        for assessment in result.effective_assessments
        if not is_unsearched_cid_nl_discovery_assessment(assessment)
    ]

    prepared = batch.prepared_grouping or group_acquired_partition(
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
                    pair[0].sample_name,
                    subject_order[pair[1].method_subject.method_subject_id],
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
                sample_result.job.sample.sample_name,
                sample_result.job.method_subject.method_subject_id,
            ): (sample_result.job.sample, backfill.assessment)
            for subject in subject_workset
            for sample_result in results_by_subject.get(subject.method_subject_id, ())
            if sample_result.backfill_attempts
            for group in workset.grouping_scope.groups
            if (
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
                result_by_key[(sample.raw_stem, context.method_subject_id)],
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


def _runtime_observations_by_candidate(
    batch: UntargetedPartitionInputs,
    sample_results: tuple[UntargetedInitialSampleResult, ...],
) -> dict[str, PeakObservation]:
    observations: dict[str, PeakObservation] = {}
    for result in sample_results:
        for candidate_id, observation in result.adapter_result.candidate_observations:
            if candidate_id in observations:
                raise RuntimeError("runtime acquisition duplicated a peak")
            observations[candidate_id] = observation
    partition_ids = {key for key, _ in batch.subject_batch.candidate_memberships}
    expected = {
        peak.peak_id
        for peaks in batch.context.peaks_by_sample.values()
        for peak in peaks
        if peak.peak_id in partition_ids
    }
    if not set(observations) <= expected:
        raise RuntimeError("runtime acquisition crossed partition peaks")
    return observations
