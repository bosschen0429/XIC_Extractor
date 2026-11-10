"""Closed capture population of one Targeted Target over its sample cohort.

The population turns retained acquisition results into owner-bound captures:
each sample with a retained physical evaluation is captured once, its selected
and alternative peaks are minted as observations, and the selected peak is bound
to the hypothesis that carries it into completion. A CID-NL Target binds after
its per-sample assessments exist; an MS1-only Target binds while capturing. A
Backfill recovery observation rebinds the affected samples and captures again.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from xic_extractor.alignment.completion import CaptureWorkflowArguments
from xic_extractor.alignment.hypothesis_owner_adapter import (
    compose_peak_hypotheses_from_assessment_universe,
)
from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
    resolve_signal_processing_profile,
)
from xic_extractor.configuration.target_identity import (
    ResolvedTargetDefinition,
)
from xic_extractor.extraction.result_assembly import TARGETED_ACQUISITION_TRACE_SOURCE
from xic_extractor.extraction.result_models import ExtractionResult, FileResult
from xic_extractor.peak_detection.capture import (
    SampleLocalCapture,
    bind_owner_backed_capture_hypothesis,
    capture_peak_hypotheses,
    mint_captured_peak_observation,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessment,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CID_NL_PROVIDER_ID,
    cid_nl_method_subject_from_target,
)
from xic_extractor.peak_detection.observations import (
    PeakObservation,
    PeakObservationGenerationProvenance,
    mint_physical_peak_observations,
)
from xic_extractor.peak_detection.peak_hypothesis_identity import (
    MeasuredPeakHypothesis,
    compose_ms1_observation_peak_hypothesis,
)
from xic_extractor.peak_detection.physical_peak_models import PhysicalPeakEvaluation
from xic_extractor.peak_detection.search_scope import (
    SEARCH_SCOPE_SCHEMA_VERSION,
    CaptureCandidateBinding,
    CaptureMethodConstraints,
    EvidenceProviderBinding,
    TargetSearchScope,
    target_ms1_subject_id,
)
from xic_extractor.peak_detection.traces import Trace
from xic_extractor.processing_identity import AcquisitionSourceIdentity
from xic_extractor.sample_metadata import SampleMetadata

ResultsBySample = tuple[
    tuple[SampleMetadata, FileResult],
    ...,
]


@dataclass(frozen=True, slots=True)
class TargetedSampleCapture:
    """One sample's retained result and, when it has a candidate, its capture."""

    sample: SampleMetadata
    file_result: FileResult
    result: ExtractionResult
    candidate_id: str | None
    captured: SampleLocalCapture | None
    selected_observation: PeakObservation | None
    observation_universe: tuple[PeakObservation, ...]
    owner_hypothesis: MeasuredPeakHypothesis | None = None

    @property
    def capture_input(self) -> CaptureWorkflowArguments | None:
        captured = self.captured
        if captured is None:
            return None
        if self.owner_hypothesis is None:
            return CaptureWorkflowArguments(
                candidate_id=captured.candidate_id,
                trace=captured.trace,
            )
        return CaptureWorkflowArguments(
            candidate_id=captured.candidate_id,
            trace=captured.trace,
            owner_observation=self.selected_observation,
            owner_hypothesis=self.owner_hypothesis,
        )


@dataclass(frozen=True, slots=True)
class TargetedCapturePopulation:
    """Every sample of one Target, with its scope and owner-bound captures."""

    target: ResolvedTargetDefinition
    acquisition_sources: Mapping[str, AcquisitionSourceIdentity]
    samples: tuple[SampleMetadata, ...]
    scope: TargetSearchScope
    method_subject_id: str
    entries: tuple[TargetedSampleCapture, ...]

    @property
    def ms1_only(self) -> bool:
        return self.target.definition.target.neutral_loss_da is None

    @property
    def capture_inputs(self) -> tuple[CaptureWorkflowArguments, ...]:
        return tuple(
            item
            for item in (entry.capture_input for entry in self.entries)
            if item is not None
        )

    @property
    def captures(self) -> tuple[SampleLocalCapture, ...]:
        return tuple(
            entry.captured for entry in self.entries if entry.captured is not None
        )

    def bind_assessments(
        self,
        assessments: Sequence[tuple[SampleMetadata, CidNlMethodAssessment]],
    ) -> TargetedCapturePopulation:
        """Bind each selected observation to its assessment-universe hypothesis."""

        assessment_by_sample = {
            sample.sample_name: assessment for sample, assessment in assessments
        }
        bound_entries = []
        for entry in self.entries:
            selected = entry.selected_observation
            if (
                entry.captured is None
                or selected is None
                or entry.owner_hypothesis is not None
            ):
                bound_entries.append(entry)
                continue
            hypotheses = compose_peak_hypotheses_from_assessment_universe(
                sample=entry.sample,
                assessment=assessment_by_sample[entry.sample.sample_name],
            )
            matches = tuple(
                hypothesis for hypothesis in hypotheses if hypothesis.peak == selected
            )
            if len(matches) != 1:
                raise RuntimeError("Targeted assessment lost its captured observation")
            bound_entries.append(
                replace(
                    entry,
                    captured=bind_owner_backed_capture_hypothesis(
                        entry.captured,
                        observation=selected,
                        hypothesis=matches[0],
                    ),
                    owner_hypothesis=matches[0],
                )
            )
        return replace(self, entries=tuple(bound_entries))

    def rebind_to_recovery(
        self,
        recovery_observations: Mapping[str, PeakObservation],
    ) -> TargetedCapturePopulation:
        """Capture again after Backfill replaced a sample's selected peak."""

        replacements: dict[str, CaptureCandidateBinding] = {}
        for entry in self.entries:
            recovery = recovery_observations.get(entry.sample.sample_name)
            if (
                entry.candidate_id is None
                or recovery is None
                or recovery.physical_evaluation is entry.result.physical_evaluation
            ):
                continue
            index = next(
                i
                for i, evaluation in enumerate(
                    entry.result.candidate_physical_evaluations
                )
                if evaluation is recovery.physical_evaluation
            )
            replacements[entry.candidate_id] = _capture_binding(
                self.acquisition_sources[entry.sample.raw_stem],
                self.target,
                entry.sample,
                replace(entry.result, physical_evaluation=recovery.physical_evaluation),
                f"{entry.candidate_id}:universe:{index}",
                method_subject_id=self.method_subject_id,
            )
        if not replacements:
            return self
        bindings = tuple(
            replacements.get(binding.candidate_id, binding)
            for binding in self.scope.capture_bindings
        )
        scope = replace(
            self.scope,
            capture_bindings=bindings,
            declared_candidate_ids=tuple(binding.candidate_id for binding in bindings),
        )
        rebound_entries = []
        for entry in self.entries:
            if entry.captured is None:
                rebound_entries.append(entry)
                continue
            binding = replacements.get(entry.captured.candidate_id)
            candidate_id = (
                entry.captured.candidate_id if binding is None else binding.candidate_id
            )
            captured = capture_peak_hypotheses(
                scope=scope, candidate_id=candidate_id, trace=entry.captured.trace
            )
            result = entry.result
            observation = entry.selected_observation
            hypothesis = entry.owner_hypothesis
            if binding is not None:
                recovery = recovery_observations[binding.sample_key]
                result = replace(
                    result, physical_evaluation=recovery.physical_evaluation
                )
                observation = recovery
                hypothesis = compose_ms1_observation_peak_hypothesis(
                    peak_observation=observation,
                )
            if observation is not None and hypothesis is not None:
                captured = bind_owner_backed_capture_hypothesis(
                    captured,
                    observation=observation,
                    hypothesis=hypothesis,
                )
            rebound_entries.append(
                replace(
                    entry,
                    result=result,
                    candidate_id=candidate_id,
                    captured=captured,
                    selected_observation=observation,
                    owner_hypothesis=hypothesis,
                )
            )
        return replace(self, scope=scope, entries=tuple(rebound_entries))


def build_targeted_capture_population(
    *,
    target: ResolvedTargetDefinition,
    acquisition_sources: Mapping[str, AcquisitionSourceIdentity],
    samples: tuple[SampleMetadata, ...],
    results_by_sample: ResultsBySample,
    config: ExtractionConfig,
) -> TargetedCapturePopulation:
    """Capture every sample that retained a physical evaluation for ``target``."""

    profile = targeted_signal_processing_profile(config)
    label = target.definition.target.label
    ms1_only = target.definition.target.neutral_loss_da is None
    method_subject = None if ms1_only else cid_nl_method_subject_from_target(target)
    method_subject_id = (
        target_ms1_subject_id(target)
        if method_subject is None
        else method_subject.method_subject_id
    )
    candidates = []
    for sample, file_result in results_by_sample:
        extraction = file_result.results[label]
        if extraction.target_label != label:
            raise RuntimeError(
                "Targeted acquisition result crossed its declared Target identity"
            )
        if extraction.acquisition_trace is None and not ms1_only:
            if extraction.selected_hypothesis is not None:
                raise RuntimeError(
                    "selected Targeted result lost its acquisition trace"
                )
            continue
        if extraction.acquisition_trace is None:
            if extraction.selected_hypothesis is not None:
                raise RuntimeError(
                    "selected Targeted result lost its acquisition trace"
                )
            raise RuntimeError(
                "MS1-only Target is missing retained acquisition evidence"
            )
        if extraction.physical_evaluation is not None:
            candidates.append(
                (sample, extraction, targeted_capture_candidate_id(target, sample))
            )
    bindings = tuple(
        _capture_binding(
            acquisition_sources[sample.raw_stem],
            target,
            sample,
            result,
            candidate_id,
            method_subject_id=method_subject_id,
        )
        for sample, result, candidate_id in candidates
    )
    scope = TargetSearchScope(
        schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
        samples=samples,
        method_constraints=CaptureMethodConstraints(
            boundary_policy="resolver_interval",
            peak_config=config,
            signal_processing_profile=profile,
        ),
        evidence_providers=(
            ()
            if method_subject is None
            else (
                EvidenceProviderBinding(
                    provider_id=CID_NL_PROVIDER_ID,
                    method_subject_id=method_subject.method_subject_id,
                ),
            )
        ),
        declared_candidate_ids=tuple(binding.candidate_id for binding in bindings),
        capture_bindings=bindings,
        target_snapshot=target.snapshot,
        target=target,
    )
    candidate_by_sample = {
        sample.sample_name: (result, candidate_id)
        for sample, result, candidate_id in candidates
    }
    entries = []
    for sample, file_result in results_by_sample:
        result = file_result.results[label]
        candidate = candidate_by_sample.get(sample.sample_name)
        if candidate is None:
            if ms1_only:
                raise RuntimeError(
                    "MS1-only Target lost its retained acquisition candidate"
                )
            entries.append(
                TargetedSampleCapture(
                    sample=sample,
                    file_result=file_result,
                    result=result,
                    candidate_id=None,
                    captured=None,
                    selected_observation=None,
                    observation_universe=(),
                )
            )
            continue
        result, candidate_id = candidate
        trace = _target_capture_trace(
            acquisition_sources[sample.raw_stem],
            sample,
            result,
            target.definition.target,
        )
        captured = capture_peak_hypotheses(
            scope=scope, candidate_id=candidate_id, trace=trace
        )
        selected_observation = (
            mint_captured_peak_observation(
                captured,
                sample=sample,
            )
            if captured.selected_hypothesis is not None
            else None
        )
        if ms1_only:
            owner_hypothesis = (
                None
                if selected_observation is None
                else compose_ms1_observation_peak_hypothesis(
                    peak_observation=selected_observation,
                )
            )
            if owner_hypothesis is not None and selected_observation is not None:
                captured = bind_owner_backed_capture_hypothesis(
                    captured,
                    observation=selected_observation,
                    hypothesis=owner_hypothesis,
                )
            entries.append(
                TargetedSampleCapture(
                    sample=sample,
                    file_result=file_result,
                    result=result,
                    candidate_id=candidate_id,
                    captured=captured,
                    selected_observation=selected_observation,
                    observation_universe=(
                        () if selected_observation is None else (selected_observation,)
                    ),
                    owner_hypothesis=owner_hypothesis,
                )
            )
            continue
        # Unavailable alternatives remain acquisition diagnostics; only an
        # available physical evaluation can supply a numeric observation.
        admissible_evaluations = tuple(
            evaluation
            for index, evaluation in enumerate(result.candidate_physical_evaluations)
            if evaluation.state == "available"
            and (
                result.context_admissible_candidate_indexes is None
                or index in result.context_admissible_candidate_indexes
            )
        )
        alternate_evaluations = tuple(
            evaluation
            for evaluation in admissible_evaluations
            if evaluation is not result.physical_evaluation
            or selected_observation is None
        )
        alternate_observations = iter(
            mint_physical_peak_observations(
                sample=sample,
                trace=trace,
                evaluations=alternate_evaluations,
                signal_processing_profile=profile,
                resolver_mode=config.resolver_mode,
                trace_observation=selected_observation,
                acquired_trace_provenance=result.acquisition_observation_provenance,
            )
        )
        universe = tuple(
            selected_observation
            if evaluation is result.physical_evaluation
            and selected_observation is not None
            else next(alternate_observations)
            for evaluation in admissible_evaluations
        )
        entries.append(
            TargetedSampleCapture(
                sample=sample,
                file_result=file_result,
                result=result,
                candidate_id=candidate_id,
                captured=captured,
                selected_observation=selected_observation,
                observation_universe=universe,
            )
        )
    return TargetedCapturePopulation(
        target=target,
        acquisition_sources=acquisition_sources,
        samples=samples,
        scope=scope,
        method_subject_id=method_subject_id,
        entries=tuple(entries),
    )


def bound_acquisition_trace(
    source: AcquisitionSourceIdentity,
    sample: SampleMetadata,
    result: ExtractionResult,
) -> Trace:
    """Return the retained trace re-bound to the canonical sample and source."""

    trace = result.acquisition_trace
    if trace is None:
        raise RuntimeError("Targeted result has no retained acquisition trace")
    if trace.sample_name != sample.sample_name:
        raise RuntimeError(
            "Targeted acquisition trace crossed the canonical sample identity"
        )
    if trace.acquisition_source_id not in {"", source.acquisition_source_id}:
        raise RuntimeError(
            "Targeted acquisition trace crossed the resolved acquisition source"
        )
    return replace(
        trace,
        sample_name=sample.sample_name,
        source=TARGETED_ACQUISITION_TRACE_SOURCE,
        acquisition_source_id=source.acquisition_source_id,
    )


def targeted_capture_candidate_id(
    target: ResolvedTargetDefinition,
    sample: SampleMetadata,
) -> str:
    return ":".join(
        (
            "targeted-capture",
            target.lookup_label,
            sample.sample_name,
        )
    )


def targeted_signal_processing_profile(
    config: ExtractionConfig,
) -> SignalProcessingProfile:
    return resolve_signal_processing_profile(
        signal_processing_method=config.signal_processing_method,
        signal_processing_window_points=config.signal_processing_window_points,
    )


def mint_candidate_peak_observation(
    sample: SampleMetadata,
    trace: Trace,
    evaluation: PhysicalPeakEvaluation,
    *,
    config: ExtractionConfig,
    acquired_trace_provenance: PeakObservationGenerationProvenance | None,
) -> PeakObservation:
    return mint_physical_peak_observations(
        sample=sample,
        trace=trace,
        evaluations=(evaluation,),
        resolver_mode=config.resolver_mode,
        signal_processing_profile=targeted_signal_processing_profile(config),
        acquired_trace_provenance=acquired_trace_provenance,
    )[0]


def _capture_binding(
    source: AcquisitionSourceIdentity,
    target: ResolvedTargetDefinition,
    sample: SampleMetadata,
    result: ExtractionResult,
    candidate_id: str,
    *,
    method_subject_id: str,
) -> CaptureCandidateBinding:
    trace = _target_capture_trace(
        source,
        sample,
        result,
        target.definition.target,
    )
    physical_evaluation = result.physical_evaluation
    if physical_evaluation is None:
        raise RuntimeError("Targeted capture lost its acquisition physical evaluation")
    preferred_rt = physical_evaluation.question.query_rt_min
    return CaptureCandidateBinding(
        candidate_id=candidate_id,
        method_subject_id=method_subject_id,
        sample_key=sample.sample_name,
        acquisition_source_id=source.acquisition_source_id,
        query_mz=trace.mz,
        query_rt_min=trace.rt_min,
        query_rt_max=trace.rt_max,
        query_ppm_tol=trace.ppm_tol,
        preferred_rt=preferred_rt,
        retained_selected_integration=None,
        selection_policy="capture_default",
        physical_evaluation=physical_evaluation,
        acquired_trace_provenance=result.acquisition_observation_provenance,
    )


def _target_capture_trace(
    source: AcquisitionSourceIdentity,
    sample: SampleMetadata,
    result: ExtractionResult,
    target: Target,
) -> Trace:
    # Expected RT identifies the target; retained acquisition coverage is authoritative.
    return bound_acquisition_trace(source, sample, result)


__all__ = [
    "TargetedCapturePopulation",
    "TargetedSampleCapture",
    "bound_acquisition_trace",
    "build_targeted_capture_population",
    "mint_candidate_peak_observation",
    "targeted_capture_candidate_id",
    "targeted_signal_processing_profile",
]
