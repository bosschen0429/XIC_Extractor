"""Dependency-light Discovery binding for canonical physical facts."""

from __future__ import annotations

from dataclasses import dataclass, replace

from xic_extractor.acquisition_contract import (
    AcquiredTrace,
    acquired_trace_content_id,
)
from xic_extractor.discovery.models import (
    DiscoveryCandidate,
    DiscoveryPhysicalWorkItem,
    build_discovery_physical_selection_subject_id,
    discovery_candidate_output_sort_key,
)
from xic_extractor.peak_detection.evidence_facts import (
    PhysicalPeakEvaluation,
    validate_bounded_physical_peak_evaluation,
)


@dataclass(frozen=True, slots=True)
class DiscoveryPhysicalFactRecord:
    work_item: DiscoveryPhysicalWorkItem
    acquired_trace: AcquiredTrace
    evaluation: PhysicalPeakEvaluation

    def __post_init__(self) -> None:
        if not isinstance(self.work_item, DiscoveryPhysicalWorkItem):
            raise TypeError("work_item must be a DiscoveryPhysicalWorkItem")
        if not isinstance(self.acquired_trace, AcquiredTrace):
            raise TypeError("acquired_trace must be an AcquiredTrace")
        if not isinstance(self.evaluation, PhysicalPeakEvaluation):
            raise TypeError("evaluation must be a PhysicalPeakEvaluation")
        _validate_evaluation_work_identity(self.evaluation, self.work_item)
        _validate_acquired_trace_work_identity(self.acquired_trace, self.work_item)

    @property
    def rt(self) -> tuple[float, ...]:
        return tuple(float(value) for value in self.acquired_trace.trace.rt)

    @property
    def raw(self) -> tuple[float, ...]:
        return tuple(float(value) for value in self.acquired_trace.trace.intensity)

    @property
    def candidate_id(self) -> str:
        return self.work_item.candidate_id

    @property
    def selection_subject_id(self) -> str:
        return self.work_item.selection_subject_id


@dataclass(frozen=True, slots=True)
class DiscoveryCandidatePhysicalBinding:
    """Discovery-owned in-memory binding from a final candidate to peak facts."""

    final_ordinal: int
    candidate: DiscoveryCandidate
    representative_source_ordinal: int
    member_source_ordinals: tuple[int, ...]
    representative_work_ordinal: int
    selection_subject_id: str
    physical_evaluation: PhysicalPeakEvaluation

    def __post_init__(self) -> None:
        if self.final_ordinal < 0:
            raise ValueError("final_ordinal must be non-negative")
        if not isinstance(self.candidate, DiscoveryCandidate):
            raise TypeError("candidate must be a DiscoveryCandidate")
        if (
            not self.member_source_ordinals
            or tuple(sorted(set(self.member_source_ordinals)))
            != self.member_source_ordinals
            or self.member_source_ordinals[0] < 0
        ):
            raise ValueError(
                "member_source_ordinals must be unique, ordered, and non-negative"
            )
        if self.representative_source_ordinal not in self.member_source_ordinals:
            raise ValueError("representative source must belong to the merged members")
        if self.representative_work_ordinal < 0:
            raise ValueError("representative_work_ordinal must be non-negative")
        if not isinstance(self.physical_evaluation, PhysicalPeakEvaluation):
            raise TypeError("physical_evaluation must be a PhysicalPeakEvaluation")
        validate_bounded_physical_peak_evaluation(self.physical_evaluation)
        expected_subject_id = build_discovery_physical_selection_subject_id(
            sample_stem=self.candidate.sample_stem,
            best_ms2_scan_id=self.candidate.best_ms2_scan_id,
            precursor_mz=self.candidate.precursor_mz,
            product_mz=self.candidate.product_mz,
        )
        if expected_subject_id != self.selection_subject_id:
            raise ValueError("Discovery physical binding crosses candidate identity")
        if self.physical_evaluation.question.selection_subject_id != (
            self.selection_subject_id
        ):
            raise ValueError("Discovery physical binding crosses selection subject")


def bind_physical_facts_to_final_candidates(
    bindings: tuple[DiscoveryCandidatePhysicalBinding, ...],
    candidates: tuple[DiscoveryCandidate, ...],
) -> tuple[DiscoveryCandidatePhysicalBinding, ...]:
    """Attach post-anchor/scoring candidates without changing physical authority."""

    if len(bindings) != len(candidates):
        raise ValueError("Discovery final candidate/binding cardinality mismatch")
    rebound: list[DiscoveryCandidatePhysicalBinding] = []
    for final_ordinal, (binding, candidate) in enumerate(
        zip(bindings, candidates, strict=True)
    ):
        if binding.final_ordinal != final_ordinal:
            raise ValueError("Discovery physical binding final order is not contiguous")
        if (
            binding.candidate.candidate_id != candidate.candidate_id
            or binding.candidate.sample_stem != candidate.sample_stem
        ):
            raise ValueError("Discovery final candidate crosses physical binding")
        rebound.append(replace(binding, candidate=candidate))
    return tuple(rebound)


def order_physical_bindings_for_candidate_csv(
    bindings: tuple[DiscoveryCandidatePhysicalBinding, ...],
) -> tuple[DiscoveryCandidatePhysicalBinding, ...]:
    ordered = sorted(
        bindings,
        key=lambda binding: discovery_candidate_output_sort_key(binding.candidate),
    )
    return tuple(
        replace(binding, final_ordinal=final_ordinal)
        for final_ordinal, binding in enumerate(ordered)
    )


def _validate_evaluation_work_identity(
    evaluation: PhysicalPeakEvaluation,
    work_item: DiscoveryPhysicalWorkItem,
) -> None:
    question = evaluation.question
    if question.selection_subject_id != work_item.selection_subject_id:
        raise ValueError("physical evaluation crosses Discovery selection subject")
    if question.query_rt_min != work_item.query_rt_min:
        raise ValueError("physical evaluation crosses Discovery query RT")
    if (
        question.context_rt_start_min != work_item.context_rt_start_min
        or question.context_rt_end_min != work_item.context_rt_end_min
    ):
        raise ValueError("physical evaluation crosses Discovery request context")


def _validate_acquired_trace_work_identity(
    acquired_trace: AcquiredTrace,
    work_item: DiscoveryPhysicalWorkItem,
) -> None:
    coordinate = acquired_trace.coordinate
    request = coordinate.request
    if coordinate.acquisition_source_id != work_item.acquisition_source_id:
        raise ValueError("acquired trace crosses Discovery acquisition source")
    if coordinate.trace_coordinate_id != work_item.trace_coordinate_id:
        raise ValueError("acquired trace crosses Discovery trace coordinate")
    if acquired_trace_content_id(acquired_trace) != work_item.acquired_trace_content_id:
        raise ValueError("acquired trace crosses Discovery trace content")
    if (
        request.mz != work_item.xic_mz
        or request.rt_min != work_item.xic_rt_min
        or request.rt_max != work_item.xic_rt_max
        or request.ppm_tol != work_item.xic_ppm_tol
    ):
        raise ValueError("acquired trace crosses Discovery XIC request")


__all__ = [
    "DiscoveryCandidatePhysicalBinding",
    "DiscoveryPhysicalFactRecord",
    "bind_physical_facts_to_final_candidates",
    "order_physical_bindings_for_candidate_csv",
]
