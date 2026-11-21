"""Closed capture population of one Targeted Target over its sample cohort.

Each sample with a retained physical evaluation becomes one search candidate:
its selected and alternative peaks are observations, and the selected peak
carries the hypothesis that completion reports. A CID-NL Target takes that
hypothesis from its per-sample assessment; an MS1-only Target composes it from
the observation. A Backfill recovery observation replaces a sample's hypothesis.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from xic_extractor.alignment.hypothesis_owner_adapter import (
    compose_peak_hypotheses_from_assessment_universe,
)
from xic_extractor.alignment.result.scientific_alignment import (
    ObservedSearchSubject,
    SearchedNoObservationSubject,
    TerminalSearchObservation,
)
from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
    resolve_signal_processing_profile,
)
from xic_extractor.extraction.result_assembly import TARGETED_ACQUISITION_TRACE_SOURCE
from xic_extractor.extraction.result_models import ExtractionResult, FileResult
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
    """One sample's retained result and, when it has a candidate, its peaks."""

    sample: SampleMetadata
    file_result: FileResult
    result: ExtractionResult
    candidate_id: str | None
    selected_observation: PeakObservation | None
    observation_universe: tuple[PeakObservation, ...]
    owner_hypothesis: MeasuredPeakHypothesis | None = None

    @property
    def terminal_subject(self) -> TerminalSearchObservation | None:
        if self.candidate_id is None:
            return None
        if self.owner_hypothesis is None:
            return SearchedNoObservationSubject(
                candidate_id=self.candidate_id,
                sample_key=self.sample.sample_name,
            )
        return ObservedSearchSubject(
            candidate_id=self.candidate_id,
            sample_key=self.sample.sample_name,
            hypothesis=self.owner_hypothesis,
        )


@dataclass(frozen=True, slots=True)
class TargetedCapturePopulation:
    """Every sample of one Target, with its scope and selected hypotheses."""

    target: Target
    acquisition_sources: Mapping[str, AcquisitionSourceIdentity]
    samples: tuple[SampleMetadata, ...]
    scope: TargetSearchScope
    entries: tuple[TargetedSampleCapture, ...]

    @property
    def ms1_only(self) -> bool:
        return self.target.neutral_loss_da is None

    @property
    def terminal_subjects(self) -> tuple[TerminalSearchObservation, ...]:
        return tuple(
            item
            for item in (entry.terminal_subject for entry in self.entries)
            if item is not None
        )

    def bind_assessments(
        self,
        assessments: Sequence[tuple[SampleMetadata, CidNlMethodAssessment]],
    ) -> TargetedCapturePopulation:
        """Take each selected observation's hypothesis from its assessment."""

        assessment_by_sample = {
            sample.sample_name: assessment for sample, assessment in assessments
        }
        bound_entries = []
        for entry in self.entries:
            selected = entry.selected_observation
            if selected is None or entry.owner_hypothesis is not None:
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
                raise RuntimeError("Targeted assessment lost its selected observation")
            bound_entries.append(replace(entry, owner_hypothesis=matches[0]))
        return replace(self, entries=tuple(bound_entries))

    def with_recovered_peaks(
        self,
        recovery_observations: Mapping[str, PeakObservation],
    ) -> TargetedCapturePopulation:
        """Report the Backfill peak where it replaced a sample's selected peak."""

        entries = []
        for entry in self.entries:
            recovery = recovery_observations.get(entry.sample.sample_name)
            if (
                entry.candidate_id is None
                or recovery is None
                or recovery.physical_evaluation is entry.result.physical_evaluation
            ):
                entries.append(entry)
                continue
            entries.append(
                replace(
                    entry,
                    selected_observation=recovery,
                    owner_hypothesis=compose_ms1_observation_peak_hypothesis(
                        peak_observation=recovery,
                    ),
                )
            )
        return replace(self, entries=tuple(entries))


def build_targeted_capture_population(
    *,
    target: Target,
    acquisition_sources: Mapping[str, AcquisitionSourceIdentity],
    samples: tuple[SampleMetadata, ...],
    results_by_sample: ResultsBySample,
    config: ExtractionConfig,
) -> TargetedCapturePopulation:
    """Observe every sample that retained a physical evaluation for ``target``."""

    profile = targeted_signal_processing_profile(config)
    label = target.label
    ms1_only = target.neutral_loss_da is None
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
        CaptureCandidateBinding(
            candidate_id=candidate_id,
            method_subject_id=method_subject_id,
            sample_key=sample.sample_name,
        )
        for sample, _result, candidate_id in candidates
    )
    scope = TargetSearchScope(
        schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
        samples=samples,
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
            target,
        )
        evaluation = result.physical_evaluation
        selected_observation = (
            mint_candidate_peak_observation(
                sample,
                trace,
                evaluation,
                config=config,
                acquired_trace_provenance=result.acquisition_observation_provenance,
            )
            if evaluation is not None and evaluation.state == "available"
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
            entries.append(
                TargetedSampleCapture(
                    sample=sample,
                    file_result=file_result,
                    result=result,
                    candidate_id=candidate_id,
                    selected_observation=selected_observation,
                    observation_universe=(
                        () if selected_observation is None else (selected_observation,)
                    ),
                    owner_hypothesis=owner_hypothesis,
                )
            )
            continue
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
                selected_observation=selected_observation,
                observation_universe=universe,
            )
        )
    return TargetedCapturePopulation(
        target=target,
        acquisition_sources=acquisition_sources,
        samples=samples,
        scope=scope,
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
    target: Target,
    sample: SampleMetadata,
) -> str:
    return ":".join(
        (
            "targeted-capture",
            target.label,
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


def _target_capture_trace(
    source: AcquisitionSourceIdentity,
    sample: SampleMetadata,
    result: ExtractionResult,
    target: Target,
) -> Trace:
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
