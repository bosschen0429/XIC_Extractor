"""Decide CID-NL participation and freeze batch-calibrated initial membership."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace

from xic_extractor.alignment.cid_nl_completion import CidNlWorksetGrouping
from xic_extractor.alignment.cid_nl_grouping import alignment_subject_worksets
from xic_extractor.alignment.completion import UntargetWorkflowCompositionRequest
from xic_extractor.alignment.config import AlignmentConfig
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
    UntargetedCandidateExclusion,
)
from xic_extractor.alignment.result.scientific_alignment import ObservedSearchSubject
from xic_extractor.alignment.rt_anchors import (
    BatchRtModelInputs,
    RtAnchorObservation,
    fit_batch_rt_model,
    rt_anchor_observations,
)
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.discovery.models import DiscoveryPeak
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessment,
    is_unsearched_cid_nl_discovery_assessment,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CID_NL_PROVIDER_ID,
    UntargetedCidNlMethodSubjectBatch,
)
from xic_extractor.peak_detection.elution_context import SampleElutionContext
from xic_extractor.peak_detection.observations import PeakObservation
from xic_extractor.peak_detection.search_scope import (
    SEARCH_SCOPE_SCHEMA_VERSION,
    CaptureCandidateBinding,
    EvidenceProviderBinding,
    UntargetedSearchScope,
)
from xic_extractor.sample_metadata import SampleMetadata


@dataclass(frozen=True, slots=True)
class CidNlGrouping:
    """Initial membership, exclusions and RT facts shared through completion."""

    request: UntargetWorkflowCompositionRequest
    worksets: tuple[CidNlWorksetGrouping, ...]
    alignment_config: AlignmentConfig
    known_istd_reference: KnownIstdReference | None
    rt_frame: SampleRtFrame

    @property
    def rt_anchor_observations(self) -> tuple[RtAnchorObservation, ...]:
        return tuple(
            observation
            for workset in self.worksets
            for observation in rt_anchor_observations(
                workset.grouping_scope, workset.retained_candidates
            )
        )


class CidNlBatchGrouping:
    """Shared in-memory preparation for direct and spooled batch execution.

    Callers supply initial groupings, freeze calibration, then finalize each
    grouping against that frozen calibration. Only compact anchor projections
    are retained; RAW access, partition admission and spooling remain with
    runtime.
    """

    def __init__(self, rt_frame: SampleRtFrame) -> None:
        self._bootstrap_frame = rt_frame
        self._anchor_batches: list[tuple[RtAnchorObservation, ...]] = []
        self._rt_model_inputs: BatchRtModelInputs | None = None
        self._rt_frame: SampleRtFrame | None = None

    def add_initial(self, prepared: CidNlGrouping) -> tuple[RtAnchorObservation, ...]:
        observations = prepared.rt_anchor_observations
        self.add_anchor_observations(observations)
        self._bootstrap_frame = self._bootstrap_frame.with_sources(
            known_istd_reference=prepared.known_istd_reference
        )
        return observations

    def add_anchor_observations(
        self, observations: Sequence[RtAnchorObservation]
    ) -> None:
        self._anchor_batches.append(tuple(observations))

    def freeze_calibration(
        self, *, reference: CidNlBatchGrouping | None = None
    ) -> None:
        """Fit the published map and the drift model on one bootstrap population.

        ``reference`` holds that population when it differs from this one,
        i.e. without peaks measured on an unresolved shoulder or overlap.
        """
        source = reference if reference is not None else self
        frame = source._bootstrap_frame
        self._rt_model_inputs = BatchRtModelInputs(
            sample_stems=tuple(frame.sample_universe or frame.injection_order),
            sample_cohorts=frame.sample_cohorts,
            injection_order=frame.injection_order,
            qc_samples=frame.qc_samples,
            measured_istds=self._bootstrap_frame.measured_istd_coordinates,
            anchor_batches=tuple(source._anchor_batches),
        )
        self._rt_frame = self._bootstrap_frame.with_sample_calibration(
            *fit_batch_rt_model(self._rt_model_inputs)
        )

    @property
    def frozen_rt_frame(self) -> SampleRtFrame | None:
        """The final batch map retained for persisted review consumers."""
        return self._rt_frame

    @property
    def rt_model_inputs(self) -> BatchRtModelInputs | None:
        """What the frozen map and drift model were fitted from."""
        return self._rt_model_inputs

    def finalize_grouping(
        self,
        prepared: CidNlGrouping,
        *,
        progress_callback: Callable[[Mapping[str, object]], None] | None = None,
    ) -> CidNlGrouping:
        assert self._rt_frame is not None
        return regroup_cid_nl_grouping(
            prepared,
            rt_frame=self._rt_frame,
            progress_callback=progress_callback,
        )


def prepare_cid_nl_grouping(
    *,
    subject_batch: UntargetedCidNlMethodSubjectBatch,
    request_subjects: UntargetedCidNlMethodSubjectBatch,
    samples: tuple[SampleMetadata, ...],
    candidates: Sequence[DiscoveryPeak],
    observation_by_candidate: Mapping[str, PeakObservation],
    sample_assessments: tuple[tuple[SampleMetadata, CidNlMethodAssessment], ...],
    alignment_config: AlignmentConfig,
    rt_frame: SampleRtFrame,
    elution_contexts: Sequence[SampleElutionContext] = (),
    known_istd_reference: KnownIstdReference | None = None,
    anchors_calibrate: bool = True,
) -> CidNlGrouping:
    """Decide candidate participation and initial membership from acquired science.

    A subject is unsupported only after a completed negative assessment and no
    supported or otherwise unevaluated answer. An unsearched sample alone cannot
    reject it. Unmeasured candidates are excluded independently of method support.
    The returned exclusions and bindings are the answer consumed by completion;
    neither acquisition mode nor later Backfill reopens this initial decision.
    """
    requested_reference = known_istd_reference
    results_by_subject: dict[
        str, list[tuple[SampleMetadata, CidNlMethodAssessment]]
    ] = {}
    for sample, assessment in sample_assessments:
        results_by_subject.setdefault(
            assessment.method_subject.method_subject_id, []
        ).append((sample, assessment))
    for results in results_by_subject.values():
        results.sort(key=lambda pair: pair[0].sample_name)
    peak_by_id = {peak.peak_id: peak for peak in candidates}
    sample_by_stem = {sample.raw_stem: sample for sample in samples}
    unsupported_subjects = {}
    for subject in subject_batch.subjects:
        subject_assessments = tuple(
            assessment
            for _sample, assessment in results_by_subject.get(
                subject.method_subject_id, ()
            )
        )
        evaluated_unsupported = tuple(
            ref.technical_state == "evaluated"
            and ref.method_result in {"HOLD", "REJECT"}
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
        CaptureCandidateBinding(
            candidate_id=candidate_id,
            method_subject_id=method_subject_id,
            sample_key=observation_by_candidate[candidate_id].sample_key,
        )
        for candidate_id, method_subject_id in subject_batch.candidate_memberships
        if candidate_id in observation_by_candidate
        and method_subject_id not in unsupported_subjects
    )
    exclusions = tuple(
        UntargetedCandidateExclusion(
            candidate_id=candidate_id,
            method_subject_id=method_subject_id,
            sample_key=sample_by_stem[peak_by_id[candidate_id].sample_stem].sample_name,
            peak_status=peak_by_id[candidate_id].status,
            reason_code=(
                "composite_requirement_not_supported"
                if method_subject_id in unsupported_subjects
                and unsupported_subjects[method_subject_id][
                    0
                ].method_subject.composition
                is not None
                else "cid_nl_requirement_not_supported"
                if method_subject_id in unsupported_subjects
                else "discovery_candidate_not_search_eligible"
            ),
            composite_assessments=tuple(
                ref
                for ref in unsupported_subjects.get(method_subject_id, ())
                if ref.method_subject.composition is not None
            ),
            method_assessments=tuple(
                ref
                for ref in unsupported_subjects.get(method_subject_id, ())
                if ref.method_subject.composition is None
            ),
        )
        for candidate_id, method_subject_id in (subject_batch.candidate_memberships)
        if candidate_id not in observation_by_candidate
        or method_subject_id in unsupported_subjects
    )
    scope = UntargetedSearchScope(
        schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
        method_subjects=request_subjects,
        samples=samples,
        evidence_providers=tuple(
            EvidenceProviderBinding(
                provider_id=CID_NL_PROVIDER_ID,
                method_subject_id=subject.method_subject_id,
            )
            for subject in request_subjects.subjects
        ),
        declared_candidate_ids=tuple(
            candidate_id
            for candidate_id, _method_subject_id in (
                request_subjects.candidate_memberships
            )
        ),
        capture_bindings=bindings,
    )
    parent_request = UntargetWorkflowCompositionRequest(
        scope=scope,
        excluded_candidates=exclusions,
    )

    prepared_worksets = []
    bindings_by_subject: dict[str, list[CaptureCandidateBinding]] = {}
    for binding in bindings:
        bindings_by_subject.setdefault(binding.method_subject_id, []).append(binding)
    sample_by_id = {sample.sample_name: sample for sample in samples}
    sample_universe = frozenset(sample.raw_stem for sample in samples)
    if known_istd_reference is not None:
        known_istd_reference = known_istd_reference.prepare_batch(
            sample_universe=sample_universe,
            injection_order=rt_frame.injection_order,
        )
    rt_frame = rt_frame.with_sources(known_istd_reference=known_istd_reference)
    active_subjects = tuple(
        subject
        for subject in subject_batch.subjects
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
            method_subjects=scope.method_subjects,
            samples=scope.samples,
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
                sample,
                assessment,
            )
            for subject in subject_workset
            for sample, assessment in results_by_subject.get(
                subject.method_subject_id, ()
            )
        )
        assessments = tuple(sorted(assessments, key=lambda pair: pair[0].sample_name))
        assessment_by_sample_subject = {
            (
                sample.sample_name,
                subject.method_subject_id,
            ): assessment
            for subject in subject_workset
            for sample, assessment in results_by_subject.get(
                subject.method_subject_id, ()
            )
        }
        hypotheses_by_sample_subject = {
            key: compose_peak_hypotheses_from_assessment_universe(
                sample=sample_by_id[key[0]],
                assessment=assessment_by_sample_subject[key],
            )
            for key in dict.fromkeys(
                (binding.sample_key, binding.method_subject_id)
                for binding in child_bindings
            )
        }
        retained_hypotheses_by_assessment = {
            assessment_by_sample_subject[key]: hypotheses
            for key, hypotheses in hypotheses_by_sample_subject.items()
        }
        terminal_subjects = []
        for binding in child_bindings:
            observation = observation_by_candidate[binding.candidate_id]
            hypotheses = hypotheses_by_sample_subject[
                (binding.sample_key, binding.method_subject_id)
            ]
            matches = tuple(
                hypothesis
                for hypothesis in hypotheses
                if hypothesis.peak == observation
            )
            if len(matches) != 1:
                raise RuntimeError("CID-NL assessment lost its measured observation")
            terminal_subjects.append(
                ObservedSearchSubject(
                    candidate_id=binding.candidate_id,
                    sample_key=binding.sample_key,
                    hypothesis=matches[0],
                )
            )
        operation_counts: list[FeatureInferenceOperationCounts] = []
        (
            grouping_scope,
            grouping_result,
            retained_candidates,
        ) = build_initial_cid_nl_cross_sample_groups(
            samples=samples,
            sample_assessments=assessments,
            alignment_config=alignment_config,
            elution_contexts=elution_contexts,
            rt_frame=rt_frame,
            retained_hypotheses_by_assessment=(retained_hypotheses_by_assessment),
            operation_counts_sink=operation_counts,
            anchors_calibrate=anchors_calibrate,
        )
        prepared_worksets.append(
            CidNlWorksetGrouping(
                subjects=subject_workset,
                scope=child_scope,
                terminal_subjects=tuple(terminal_subjects),
                grouping_scope=grouping_scope,
                grouping_result=grouping_result,
                retained_candidates=retained_candidates,
            )
        )
    return CidNlGrouping(
        request=parent_request,
        worksets=tuple(prepared_worksets),
        alignment_config=alignment_config,
        known_istd_reference=requested_reference,
        rt_frame=rt_frame,
    )


def regroup_cid_nl_grouping(
    prepared: CidNlGrouping,
    *,
    rt_frame: SampleRtFrame,
    progress_callback: Callable[[Mapping[str, object]], None] | None = None,
) -> CidNlGrouping:
    """Regroup one bounded workset collection using the frozen batch calibration."""
    alignment_config = prepared.alignment_config
    final_worksets = []
    for workset in prepared.worksets:
        operation_counts: list[FeatureInferenceOperationCounts] = []
        grouping_scope, grouping_result = regroup_cid_nl_cross_sample_groups(
            samples=prepared.request.scope.samples,
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
    return CidNlGrouping(
        request=prepared.request,
        worksets=tuple(final_worksets),
        alignment_config=alignment_config,
        known_istd_reference=prepared.known_istd_reference,
        rt_frame=prepared.rt_frame.with_calibration(rt_frame),
    )
