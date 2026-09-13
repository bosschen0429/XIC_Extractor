"""Dependency-light Discovery binding for canonical physical facts."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

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
    validate_physical_trace_content_id,
)
from xic_extractor.processing_identity import AcquisitionSourceIdentity


@dataclass(frozen=True, slots=True)
class DiscoveryPhysicalCandidateLink:
    """Exact Discovery merge membership, retained only for evidence browsing."""

    candidate_id: str
    final_ordinal: int
    representative_source_ordinal: int
    member_source_ordinals: tuple[int, ...]
    representative_work_ordinal: int
    member_work_ordinals: tuple[int, ...]


@dataclass(frozen=True, slots=True, eq=False)
class DiscoveryPhysicalFactRecord:
    work_item: DiscoveryPhysicalWorkItem
    acquired_trace: AcquiredTrace
    evaluation: PhysicalPeakEvaluation
    candidate_links: tuple[DiscoveryPhysicalCandidateLink, ...] = field(
        default=(), compare=False
    )

    def __post_init__(self) -> None:
        validate_discovery_physical_fact_record(self)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DiscoveryPhysicalFactRecord):
            return NotImplemented
        return (
            self.work_item == other.work_item
            and acquired_trace_content_id(self.acquired_trace)
            == acquired_trace_content_id(other.acquired_trace)
            and self.evaluation == other.evaluation
        )

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

    run_spec_id: str
    acquisition_source: AcquisitionSourceIdentity
    final_ordinal: int
    candidate: DiscoveryCandidate
    representative_source_ordinal: int
    member_source_ordinals: tuple[int, ...]
    representative_work_ordinal: int
    selection_subject_id: str
    physical_fact_record: DiscoveryPhysicalFactRecord
    member_work_ordinals: tuple[int, ...] = field(default=(), compare=False)

    def __post_init__(self) -> None:
        validate_discovery_candidate_physical_binding(self)

    @property
    def physical_evaluation(self) -> PhysicalPeakEvaluation:
        return self.physical_fact_record.evaluation


def validate_discovery_physical_fact_record(
    record: DiscoveryPhysicalFactRecord,
) -> None:
    """Re-run the complete Discovery owner invariant for one physical fact."""

    if not isinstance(record, DiscoveryPhysicalFactRecord):
        raise TypeError("record must be a DiscoveryPhysicalFactRecord")
    if not isinstance(record.work_item, DiscoveryPhysicalWorkItem):
        raise TypeError("work_item must be a DiscoveryPhysicalWorkItem")
    if not isinstance(record.acquired_trace, AcquiredTrace):
        raise TypeError("acquired_trace must be an AcquiredTrace")
    if not isinstance(record.evaluation, PhysicalPeakEvaluation):
        raise TypeError("evaluation must be a PhysicalPeakEvaluation")
    validate_bounded_physical_peak_evaluation(record.evaluation)
    _validate_evaluation_work_identity(record.evaluation, record.work_item)
    _validate_acquired_trace_work_identity(record.acquired_trace, record.work_item)
    try:
        validate_physical_trace_content_id(
            record.evaluation.prepared.trace_content_id,
            record.acquired_trace.trace.rt,
            record.acquired_trace.trace.intensity,
        )
    except ValueError as exc:
        raise ValueError("physical evaluation crosses acquired trace content") from exc


def validate_discovery_candidate_physical_binding(
    binding: DiscoveryCandidatePhysicalBinding,
) -> None:
    """Re-run the final-candidate projection against its complete owner record."""

    if not isinstance(binding, DiscoveryCandidatePhysicalBinding):
        raise TypeError("binding must be a DiscoveryCandidatePhysicalBinding")
    if not isinstance(binding.run_spec_id, str) or not binding.run_spec_id.strip():
        raise ValueError("run_spec_id must be non-empty text")
    if not isinstance(binding.acquisition_source, AcquisitionSourceIdentity):
        raise TypeError("acquisition_source must be an AcquisitionSourceIdentity")
    if binding.final_ordinal < 0:
        raise ValueError("final_ordinal must be non-negative")
    if not isinstance(binding.candidate, DiscoveryCandidate):
        raise TypeError("candidate must be a DiscoveryCandidate")
    if (
        not binding.member_source_ordinals
        or tuple(sorted(set(binding.member_source_ordinals)))
        != binding.member_source_ordinals
        or binding.member_source_ordinals[0] < 0
    ):
        raise ValueError(
            "member_source_ordinals must be unique, ordered, and non-negative"
        )
    if binding.representative_source_ordinal not in binding.member_source_ordinals:
        raise ValueError("representative source must belong to the merged members")
    if binding.representative_work_ordinal < 0:
        raise ValueError("representative_work_ordinal must be non-negative")
    validate_discovery_physical_fact_record(binding.physical_fact_record)
    work_item = binding.physical_fact_record.work_item
    if (
        work_item.acquisition_source_id
        != binding.acquisition_source.acquisition_source_id
    ):
        raise ValueError("Discovery physical binding crosses acquisition record")
    if work_item.work_ordinal != binding.representative_work_ordinal:
        raise ValueError(
            "Discovery physical binding crosses representative work record"
        )
    if work_item.sample_stem != binding.candidate.sample_stem:
        raise ValueError("Discovery physical binding crosses record sample")
    if (
        binding.candidate.ms1_search_rt_min != work_item.xic_rt_min
        or binding.candidate.ms1_search_rt_max != work_item.xic_rt_max
    ):
        raise ValueError(
            "Discovery physical binding crosses representative search window"
        )
    if work_item.selection_subject_id != binding.selection_subject_id:
        raise ValueError("Discovery physical binding crosses record selection subject")
    expected_subject_id = build_discovery_physical_selection_subject_id(
        sample_stem=binding.candidate.sample_stem,
        best_ms2_scan_id=binding.candidate.best_ms2_scan_id,
        precursor_mz=binding.candidate.precursor_mz,
        product_mz=binding.candidate.product_mz,
    )
    if expected_subject_id != binding.selection_subject_id:
        raise ValueError("Discovery physical binding crosses candidate identity")
    if binding.physical_evaluation.question.selection_subject_id != (
        binding.selection_subject_id
    ):
        raise ValueError("Discovery physical binding crosses selection subject")


def bind_discovery_physical_record_evidence(
    records: tuple[DiscoveryPhysicalFactRecord, ...],
    bindings: tuple[DiscoveryCandidatePhysicalBinding, ...],
) -> tuple[DiscoveryPhysicalFactRecord, ...]:
    """Copy actual merge refs before source/member provenance disappears."""
    links_by_work: dict[int, list[DiscoveryPhysicalCandidateLink]] = {}
    for binding in bindings:
        link = DiscoveryPhysicalCandidateLink(
            candidate_id=binding.candidate.candidate_id,
            final_ordinal=binding.final_ordinal,
            representative_source_ordinal=binding.representative_source_ordinal,
            member_source_ordinals=binding.member_source_ordinals,
            representative_work_ordinal=binding.representative_work_ordinal,
            member_work_ordinals=binding.member_work_ordinals,
        )
        for ordinal in set(binding.member_work_ordinals):
            links_by_work.setdefault(ordinal, []).append(link)
    return tuple(
        replace(
            record,
            candidate_links=tuple(links_by_work.get(record.work_item.work_ordinal, ())),
        )
        for record in records
    )


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
    "validate_discovery_candidate_physical_binding",
    "validate_discovery_physical_fact_record",
]
