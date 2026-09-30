"""Group and compose acquired Untargeted partitions into scientific results."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import cast

from xic_extractor.alignment.backfill.evaluation_coverage import (
    BackfillPlanningPosition,
    ConservativeNoRequestProofBuilder,
    build_backfill_evaluation_coverage,
    mint_backfill_planning_epoch,
    mint_backfill_position_id,
)
from xic_extractor.alignment.backfill_request_plan import (
    BACKFILL_TRACE_RESULT_SEMANTICS,
)
from xic_extractor.alignment.cell_observations import (
    CellObservationRef,
    compose_cid_nl_predecessor_cells,
)
from xic_extractor.alignment.completion import (
    CaptureWorkflowArguments,
    UntargetWorkflowAdapter,
    UntargetWorkflowBundle,
    UntargetWorkflowCompositionRequest,
    compose_untarget_subject,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.conservative_backfill import (
    ConservativeBackfillCandidateUniverse,
    assess_conservative_backfill_group,
    map_conservative_backfill_candidates,
    mint_conservative_backfill_candidate_universe,
)
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleNeighborhoodEdge,
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.drift_evidence import KnownIstdReference
from xic_extractor.alignment.feature_membership_inference import (
    FeatureInferenceOperationCounts,
    estimate_batch_rt_warp,
)
from xic_extractor.alignment.grouping_workflow import (
    admit_cid_nl_group_backfills,
    build_initial_cid_nl_cross_sample_groups,
    contested_backfill_peaks,
    regroup_cid_nl_cross_sample_groups,
)
from xic_extractor.alignment.hypothesis_owner_adapter import (
    compose_peak_hypotheses_from_assessment_universe,
)
from xic_extractor.alignment.result.application_result import (
    FinalizedUntargetApplicationResult,
    UntargetedCandidateExclusion,
)
from xic_extractor.configuration.execution_profile import DEFAULT_ALIGNMENT_WORKERS
from xic_extractor.discovery.models import (
    MS1_FEATURE_BACKED_STATES,
)
from xic_extractor.execution.result_store import (
    SealedPartition,
    StreamedResultStore,
)
from xic_extractor.extraction.untargeted_cid_nl_real_data_inputs import (
    UntargetedCidNlRealDataContext,
)
from xic_extractor.extraction.untargeted_input_planning import (
    alignment_subject_worksets,
)
from xic_extractor.extraction.untargeted_real_data_models import (
    UntargetedCidNlInitialSampleResult,
    UntargetedCidNlPreparedGrouping,
    UntargetedCidNlWorksetGrouping,
)
from xic_extractor.extraction.untargeted_real_data_runtime import (
    UntargetedCidNlRealDataRuntimeJob,
    UntargetedCidNlRuntimeSampleResult,
    _RuntimeResultContext,
)
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
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.observations import RetainedCaptureEvidence
from xic_extractor.peak_detection.search_scope import (
    SEARCH_SCOPE_SCHEMA_VERSION,
    CaptureCandidateBinding,
    CaptureMethodConstraints,
    EvidenceProviderBinding,
    UntargetedSearchScope,
    require_capture_binding,
)


@dataclass(frozen=True, slots=True)
class UntargetedPartitionInputs:
    """Partition inputs, executable jobs, and optional sparse backfill templates."""

    subject_batch: UntargetedCidNlMethodSubjectBatch
    context: UntargetedCidNlRealDataContext
    jobs: tuple[UntargetedCidNlRealDataRuntimeJob, ...]
    sample_job_templates: tuple[UntargetedCidNlRealDataRuntimeJob, ...] = ()
    prepared_grouping: UntargetedCidNlPreparedGrouping | None = None


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


def compose_and_persist_untargeted_partition(
    batch: UntargetedPartitionInputs,
    sample_results: tuple[UntargetedCidNlRuntimeSampleResult, ...],
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
        for bundle, captures in zip(
            inputs.bundles, inputs.completed_captures, strict=True
        ):
            check_cancellation()
            child = compose_untarget_subject(
                cast(UntargetWorkflowCompositionRequest, bundle.request),
                bundle.adapter,
                completed_captures=captures,
            )
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
    bundles: tuple[UntargetWorkflowBundle, ...]
    method_assessments: tuple[CidNlMethodAssessmentRef, ...]
    initial_groups: tuple[CrossSamplePeakGroupHypothesis, ...]
    initial_edges: tuple[CrossSampleNeighborhoodEdge, ...]
    completed_captures: tuple[tuple[SampleLocalCapture, ...], ...]
    capture_evidence: tuple[RetainedCaptureEvidence, ...] = ()


def compose_untargeted_partition(
    batch: UntargetedPartitionInputs,
    sample_results: tuple[UntargetedCidNlRuntimeSampleResult, ...],
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
            compose_untarget_subject(
                cast(UntargetWorkflowCompositionRequest, bundle.request),
                bundle.adapter,
                completed_captures=captures,
            )
            for bundle, captures in zip(
                inputs.bundles, inputs.completed_captures, strict=True
            )
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
    sample_results: tuple[UntargetedCidNlInitialSampleResult, ...],
    *,
    alignment_config: AlignmentConfig,
    known_istd_reference: KnownIstdReference | None = None,
    progress_callback: Callable[[Mapping[str, object]], None] | None = None,
) -> UntargetedCidNlPreparedGrouping:
    """Freeze compatible-workset membership once, before any backfill search."""
    requested_reference = known_istd_reference
    results_by_subject: dict[str, list[UntargetedCidNlInitialSampleResult]] = {}
    for result in sample_results:
        results_by_subject.setdefault(
            result.job.method_subject.method_subject_id, []
        ).append(result)
    for results in results_by_subject.values():
        results.sort(key=lambda result: result.job.sample.identity.sample_identity_id)
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
            canonical_sample_identity_id=batch.context.sample(
                candidate_by_id[candidate_id].sample_stem
            ).identity.sample_identity_id,
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
        sample.identity.sample_identity_id: sample for sample in batch.context.samples
    }
    sample_universe = frozenset(
        sample.metadata.raw_stem
        for sample in batch.context.processing_owners.sample_resolutions
    )
    injection_order = {
        sample.metadata.raw_stem: sample.metadata.injection_order
        for sample in batch.context.processing_owners.sample_resolutions
        if sample.metadata.injection_order is not None
    }
    if known_istd_reference is not None:
        known_istd_reference = known_istd_reference.prepare_batch(
            sample_universe=sample_universe,
            injection_order=injection_order,
        )
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
            sorted(assessments, key=lambda pair: pair[0].identity.sample_identity_id)
        )
        capture_inputs_list = []
        captures_list = []
        assessment_by_sample_subject = {
            (
                result.job.sample.identity.sample_identity_id,
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
                (binding.sample_identity_id, binding.method_subject_id)
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
            sample = sample_by_id[binding.sample_identity_id]
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
                (binding.sample_identity_id, binding.method_subject_id)
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
            known_istd_reference=known_istd_reference,
            retained_hypotheses_by_assessment=(retained_hypotheses_by_assessment),
            operation_counts_sink=operation_counts,
        )
        prepared_worksets.append(
            UntargetedCidNlWorksetGrouping(
                subjects=subject_workset,
                scope=child_scope,
                captures=captures,
                capture_inputs=capture_inputs,
                grouping_scope=grouping_scope,
                grouping_result=grouping_result,
                retained_candidates=retained_candidates,
            )
        )
    bootstrap_groups = tuple(
        group
        for workset in prepared_worksets
        for group in workset.grouping_scope.groups
        if group.inference_basis is not None
        and group.inference_basis.basis
        in {"bidirectional_membership_agreement", "known_identity_constraint"}
    )
    bootstrap_owners = {
        owner.owner_id: owner
        for workset in prepared_worksets
        for group in workset.grouping_scope.groups
        for owner in group.owners
    }
    rt_warp = estimate_batch_rt_warp(
        tuple(group.owners for group in bootstrap_groups),
        owners_by_id=bootstrap_owners,
        injection_order=injection_order,
    )
    final_worksets = []
    for workset in prepared_worksets:
        operation_counts = []
        grouping_scope, grouping_result = regroup_cid_nl_cross_sample_groups(
            processing_owners=batch.context.processing_owners,
            grouping_scope=workset.grouping_scope,
            grouping_result=workset.grouping_result,
            alignment_config=alignment_config,
            injection_order=injection_order,
            rt_warp=rt_warp,
            backfill_half_window_min=(
                batch.context.peak_config.nl_rt_anchor_half_window_min
            ),
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
    return UntargetedCidNlPreparedGrouping(
        request=parent_request,
        worksets=tuple(final_worksets),
        alignment_config=alignment_config,
        known_istd_reference=requested_reference,
    )


def _build_production_composition_inputs(
    batch: UntargetedPartitionInputs,
    sample_results: tuple[UntargetedCidNlRuntimeSampleResult, ...],
    *,
    alignment_config: AlignmentConfig,
    known_istd_reference: KnownIstdReference | None,
) -> _ProductionCompositionInputs:
    if type(sample_results) is not tuple or any(
        type(result) is not UntargetedCidNlRuntimeSampleResult
        for result in sample_results
    ):
        raise TypeError("sample_results must be an immutable typed runtime population")
    if not isinstance(alignment_config, AlignmentConfig):
        raise TypeError("alignment_config must be an AlignmentConfig")
    result_context = _RuntimeResultContext(
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

    results_by_subject: dict[str, list[UntargetedCidNlRuntimeSampleResult]] = {}
    for result in sample_results:
        results_by_subject.setdefault(
            result.job.method_subject.method_subject_id, []
        ).append(result)
    for results in results_by_subject.values():
        results.sort(key=lambda result: result.job.sample.identity.sample_identity_id)
    final_method_assessments = [
        assessment
        for result in sample_results
        for assessment in result.effective_assessments
        if not is_unsearched_cid_nl_discovery_assessment(assessment)
    ]

    prepared = batch.prepared_grouping or prepare_untargeted_partition_grouping(
        batch,
        tuple(
            UntargetedCidNlInitialSampleResult(item.job, item.adapter_result)
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
    parent_request = prepared.request
    bundles = []
    completed_captures = []
    initial_groups: list[CrossSamplePeakGroupHypothesis] = []
    initial_edges: list[CrossSampleNeighborhoodEdge] = []
    ordered_samples = tuple(
        sorted(
            batch.context.samples, key=lambda sample: sample.identity.sample_identity_id
        )
    )
    sample_universe = frozenset(sample.metadata.raw_stem for sample in ordered_samples)
    injection_order = {
        sample.metadata.raw_stem: sample.metadata.injection_order
        for sample in ordered_samples
        if sample.metadata.injection_order is not None
    }
    raw_sample_stems = frozenset(batch.context.raw_paths_by_sample)
    empty_family_ids: frozenset[str] = frozenset()
    for workset in prepared.worksets:
        subject_workset = workset.subjects
        child_scope = workset.scope
        captures = workset.captures
        capture_inputs = workset.capture_inputs
        grouping_scope = workset.grouping_scope
        grouping_result = workset.grouping_result
        retained_candidates = workset.retained_candidates
        assessments = tuple(
            (result.job.sample, assessment)
            for subject in subject_workset
            for result in results_by_subject.get(subject.method_subject_id, ())
            for assessment in result.effective_assessments
            if not is_unsearched_cid_nl_discovery_assessment(assessment)
        )
        initial_groups.extend(grouping_scope.groups)
        subject_order = {
            subject.method_subject_id: index
            for index, subject in enumerate(subject_workset)
        }
        assessments = tuple(
            sorted(
                assessments,
                key=lambda pair: (
                    pair[0].identity.sample_identity_id,
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
        retained_cell_hypotheses: dict[tuple[str, str], list[PeakHypothesis]] = {}
        for candidate in retained_candidates:
            assessment = candidate.direct_support_assessment
            if assessment is None:
                continue
            retained_cell_hypotheses.setdefault(
                (
                    candidate.canonical_sample_identity_id,
                    assessment.artifact.content_identity.artifact_content_id,
                ),
                [],
            ).append(candidate.peak_hypothesis)
        group_backfills = {
            (
                group.group_hypothesis_id,
                sample_result.job.sample.identity.sample_identity_id,
                sample_result.job.method_subject.method_subject_id,
            ): (sample_result.job.sample, backfill.assessment)
            for subject in subject_workset
            for sample_result in results_by_subject.get(subject.method_subject_id, ())
            if sample_result.backfill_attempts
            for group in grouping_scope.groups
            if group.group_center_rt is not None
            and not any(
                member.canonical_sample_identity_id
                == sample_result.job.sample.identity.sample_identity_id
                for member in group.member_subjects
            )
            and (
                backfill := sample_result.backfill_for_group(
                    group.group_center_rt, group_hypothesis_id=group.group_hypothesis_id
                )
            )
            is not None
        }
        grouping_result, backfilled_groups, successor_ids, backfilled_hypotheses = (
            admit_cid_nl_group_backfills(
                result=grouping_result,
                groups=grouping_scope.groups,
                backfills=group_backfills,
                alignment_config=alignment_config,
            )
        )
        backfilled_positions = frozenset(
            (group_id, sample_id)
            for (group_id, sample_id, _), (_, assessment) in group_backfills.items()
            if assessment.content.method_result == "SUPPORT"
        )
        predecessor_cells = compose_cid_nl_predecessor_cells(
            processing_owners=batch.context.processing_owners,
            groups=grouping_scope.groups,
            sample_assessments=initial_assessments,
            alignment_config=alignment_config,
            drift_lookup=grouping_result.drift_lookup,
            sample_universe=sample_universe,
            injection_order=injection_order,
            backfill_assessments_by_group={
                key: pair
                for key, pair in group_backfills.items()
                if pair[1].content.method_result != "SUPPORT"
            },
            retained_hypotheses_by_assessment={
                key: tuple(hypotheses)
                for key, hypotheses in retained_cell_hypotheses.items()
            },
            contested_backfill_peaks=contested_backfill_peaks(
                groups=backfilled_groups,
                backfills=group_backfills,
                alignment_config=alignment_config,
            ),
        )
        backfill_cells = (
            compose_cid_nl_predecessor_cells(
                processing_owners=batch.context.processing_owners,
                groups=backfilled_groups,
                sample_assessments=initial_assessments,
                alignment_config=alignment_config,
                drift_lookup=grouping_result.drift_lookup,
                sample_universe=sample_universe,
                injection_order=injection_order,
                backfill_assessments_by_group={
                    (successor_ids[group_id], sample_id, subject_id): pair
                    for (
                        group_id,
                        sample_id,
                        subject_id,
                    ), pair in group_backfills.items()
                    if pair[1].content.method_result == "SUPPORT"
                },
                retained_hypotheses_by_assessment={
                    **{
                        key: tuple(hypotheses)
                        for key, hypotheses in retained_cell_hypotheses.items()
                    },
                    **backfilled_hypotheses,
                },
                selected_cells=frozenset(
                    (successor_ids[group_id], sample_id)
                    for group_id, sample_id in backfilled_positions
                ),
            )
            if backfilled_positions
            else ()
        )
        cell_by_key = {}
        cells_by_group: dict[str, list[CellObservationRef]] = {}
        for predecessor_cell in predecessor_cells:
            group_id = predecessor_cell.content.group_hypothesis_id
            sample_id = predecessor_cell.content.canonical_sample_identity_id
            cell_by_key[(group_id, sample_id)] = predecessor_cell
            cells_by_group.setdefault(group_id, []).append(predecessor_cell)
        repair_groups = grouping_result.initial_population
        no_request_proofs = {}
        for group in repair_groups:
            group_cells = tuple(cells_by_group[group.group_hypothesis_id])
            searched_sample_ids = {
                cell.content.canonical_sample_identity_id for cell in group_cells
            }
            facts = assess_conservative_backfill_group(
                group_cells,
                sample_population=batch.context.samples,
                unsearched_sample_ids=tuple(
                    sample.identity.sample_identity_id
                    for sample in ordered_samples
                    if sample.identity.sample_identity_id not in searched_sample_ids
                ),
            )
            proof_builder = None
            for sample in batch.context.samples:
                key = (group.group_hypothesis_id, sample.identity.sample_identity_id)
                cell = cell_by_key.get(key)
                if cell is None or key in backfilled_positions:
                    continue
                if (
                    cell.content.method_result != "HOLD"
                    or cell.peak_observation is not None
                ):
                    continue
                completion = None
                universes: tuple[ConservativeBackfillCandidateUniverse, ...] = ()
                if facts.may_attempt_backfill:
                    if cell.method_assessment.candidate_peaks:
                        continue
                    method_subject_id = (
                        cell.method_assessment.content.method_subject.method_subject_id
                    )
                    sample_result = result_by_key.get(
                        (sample.metadata.raw_stem, method_subject_id)
                    )
                    if (
                        sample_result is None
                        or facts.group_context.group.group_center_rt is None
                        or sample_result.backfill_for_group(
                            facts.group_context.group.group_center_rt,
                            group_hypothesis_id=group.group_hypothesis_id,
                        )
                        is None
                    ):
                        continue
                    completion = result_context.project_completed_backfill(
                        sample_result, group_context=facts.group_context
                    )
                    universes = (
                        mint_conservative_backfill_candidate_universe(
                            processing_owners=batch.context.processing_owners,
                            sample=sample,
                            group_facts=(facts,),
                            candidate_assessment=completion.backfill_assessment,
                        ),
                    )
                (decision,) = map_conservative_backfill_candidates(
                    (facts,), sample=sample, candidate_universes=universes
                )
                if proof_builder is None:
                    proof_builder = ConservativeNoRequestProofBuilder(
                        processing_owners=batch.context.processing_owners,
                        group_identity=group,
                        group_facts=facts,
                    )
                no_request_proofs[key] = proof_builder.mint(
                    sample=sample,
                    decision=decision,
                    completion=completion,
                )
        positions = tuple(
            BackfillPlanningPosition(
                position_id=mint_backfill_position_id(
                    processing_request_id=group.processing_request_id,
                    group_hypothesis_id=group.group_hypothesis_id,
                    sample_identity_id=sample.identity.sample_identity_id,
                ),
                group_identity=group,
                sample=sample,
                planning_state="not_planned",
                planning_reason=(
                    "no_discovery_input"
                    if cell is None
                    else no_request_proofs[
                        (group.group_hypothesis_id, sample.identity.sample_identity_id)
                    ].planning_reason
                    if (group.group_hypothesis_id, sample.identity.sample_identity_id)
                    in no_request_proofs
                    else "local_owner_not_supersedable"
                    if cell.content.outside_group is not None
                    else "retained_unresolved_candidates"
                    if (
                        cell.content.technical_state == "evaluated"
                        and cell.content.method_result == "HOLD"
                        and cell.peak_observation is None
                        and len(cell.method_assessment.candidate_peaks) >= 2
                    )
                    else "retained_local_owner"
                ),
                primary_logical_requests=(),
                conservative_no_request_proof=no_request_proofs.get(
                    (group.group_hypothesis_id, sample.identity.sample_identity_id)
                ),
            )
            for group in repair_groups
            for sample in batch.context.samples
            for cell in (
                cell_by_key.get(
                    (group.group_hypothesis_id, sample.identity.sample_identity_id)
                ),
            )
        )
        epoch = mint_backfill_planning_epoch(
            processing_owners=batch.context.processing_owners,
            group_identities=repair_groups,
            samples=batch.context.samples,
            raw_sample_stems=raw_sample_stems,
            alignment_config=alignment_config,
            backend="raw",
            window_strategy="exact",
            superwindow_span_factor=2,
            validation_mode="primary",
            result_semantics=BACKFILL_TRACE_RESULT_SEMANTICS,
            scope="full-audit",
            selected_family_ids=empty_family_ids,
            positions=positions,
        )
        coverage = build_backfill_evaluation_coverage(epoch, ())
        bundles.append(
            UntargetWorkflowBundle(
                request=UntargetWorkflowCompositionRequest(
                    scope=child_scope,
                    processing_request=parent_request.processing_request,
                    processing_attempt=parent_request.processing_attempt,
                ),
                adapter=UntargetWorkflowAdapter(
                    capture_inputs=capture_inputs,
                    completed_grouping=grouping_result,
                    sample_assessments=assessments,
                    closed_cell_coverage=coverage,
                    predecessor_cell_observations=predecessor_cells,
                    backfill_cell_observations=backfill_cells,
                ),
            )
        )
        completed_captures.append(captures)
    return _ProductionCompositionInputs(
        request=parent_request,
        bundles=tuple(bundles),
        method_assessments=tuple(final_method_assessments),
        initial_groups=tuple(initial_groups),
        initial_edges=tuple(initial_edges),
        completed_captures=tuple(completed_captures),
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
    sample_results: tuple[UntargetedCidNlInitialSampleResult, ...],
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
