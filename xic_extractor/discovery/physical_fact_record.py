"""Dependency-light Discovery binding for canonical physical facts."""

from __future__ import annotations

from dataclasses import dataclass

from xic_extractor.acquisition_contract import (
    AcquiredTrace,
    TraceAcquisitionCapability,
    TraceCoordinateKind,
    mint_trace_acquisition_capability,
)
from xic_extractor.discovery.models import (
    DiscoveryPeak,
    DiscoveryPhysicalWorkItem,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    validate_bounded_physical_peak_evaluation,
)
from xic_extractor.peak_detection.physical_peak_models import PhysicalPeakEvaluation
from xic_extractor.processing_identity import AcquisitionSourceIdentity

DISCOVERY_TRACE_ADAPTER_NAMESPACE = "xic_extractor.discovery.ms1_capture"


def discovery_trace_capability(
    acquisition_source: AcquisitionSourceIdentity,
) -> TraceAcquisitionCapability:
    """The capability of every MS1 trace Discovery acquires from one RAW."""
    return mint_trace_acquisition_capability(
        acquisition_source,
        adapter_namespace=DISCOVERY_TRACE_ADAPTER_NAMESPACE,
        coordinate_kind=TraceCoordinateKind.NATIVE_SCAN,
    )


def admits_cid_nl_alignment(
    peak: DiscoveryPeak, record: DiscoveryPhysicalFactRecord | None
) -> bool:
    """Bind method admission to a resolved physical peak before input grouping."""
    return (
        peak.admitted
        and record is not None
        and record.evaluation.state == "available"
        and not record.evaluation.requires_overlap_review
    )


@dataclass(frozen=True, slots=True)
class DiscoveryPhysicalFactRecord:
    work_item: DiscoveryPhysicalWorkItem
    acquired_trace: AcquiredTrace
    evaluation: PhysicalPeakEvaluation

    @property
    def rt(self) -> tuple[float, ...]:
        return tuple(float(value) for value in self.acquired_trace.trace.rt)

    @property
    def raw(self) -> tuple[float, ...]:
        return tuple(float(value) for value in self.acquired_trace.trace.intensity)

    @property
    def peak_id(self) -> str:
        return self.work_item.peak_id

    @property
    def selection_subject_id(self) -> str:
        return self.work_item.selection_subject_id


@dataclass(frozen=True, slots=True)
class DiscoveryPeakPhysicalBinding:
    """Discovery-owned in-memory binding from one peak to its physical facts."""

    acquisition_source: AcquisitionSourceIdentity
    final_ordinal: int
    peak: DiscoveryPeak
    physical_fact_record: DiscoveryPhysicalFactRecord

    def __post_init__(self) -> None:
        validate_discovery_peak_physical_binding(self)

    @property
    def physical_evaluation(self) -> PhysicalPeakEvaluation:
        return self.physical_fact_record.evaluation


def validate_discovery_physical_fact_record(
    record: DiscoveryPhysicalFactRecord,
) -> None:
    """Validate work/trace/evaluation binding at a physical-record boundary."""

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
    if (
        record.evaluation.prepared.rt != record.rt
        or record.evaluation.prepared.raw != record.raw
    ):
        raise ValueError("physical evaluation crosses acquired trace content")


def validate_discovery_peak_physical_binding(
    binding: DiscoveryPeakPhysicalBinding,
) -> None:
    """Bind a final peak to its settled physical record and acquisition."""

    if binding.final_ordinal < 0:
        raise ValueError("final_ordinal must be non-negative")
    work_item = binding.physical_fact_record.work_item
    if (
        work_item.acquisition_source_id
        != binding.acquisition_source.acquisition_source_id
    ):
        raise ValueError("Discovery physical binding crosses acquisition record")
    if work_item.peak_id != binding.peak.peak_id:
        raise ValueError("Discovery physical binding crosses peak identity")


def validate_peak_physical_measurement(
    peak: DiscoveryPeak, record: DiscoveryPhysicalFactRecord
) -> None:
    """Reject a peak row whose measurement differs from its physical record.

    The peak table and the physical sidecar both carry the peak's apex,
    bounds, area and height; a reader that pairs them requires they agree.
    """

    work = record.work_item
    if (
        work.peak_id != peak.peak_id
        or work.sample_stem != peak.sample_stem
        or work.best_ms2_scan_id != peak.best_ms2_scan_id
    ):
        raise ValueError(f"{peak.peak_id}: physical record crosses its peak row")
    facts = record.evaluation.facts
    if facts is None:
        return
    for name, value in (
        ("ms1_apex_rt", facts.apex_rt_min),
        ("ms1_peak_rt_start", facts.left_rt_min),
        ("ms1_peak_rt_end", facts.right_rt_min),
        ("ms1_area", facts.area_counts_seconds),
        ("ms1_height", facts.height),
    ):
        if getattr(peak, name) != value:
            raise ValueError(f"{peak.peak_id}: physical {name} crosses its peak row")


def _validate_evaluation_work_identity(
    evaluation: PhysicalPeakEvaluation,
    work_item: DiscoveryPhysicalWorkItem,
) -> None:
    question = evaluation.question
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
    if (
        coordinate.capability.acquisition_source.acquisition_source_id
        != work_item.acquisition_source_id
    ):
        raise ValueError("acquired trace crosses Discovery acquisition source")
    if (
        request.mz != work_item.xic_mz
        or request.rt_min != work_item.xic_rt_min
        or request.rt_max != work_item.xic_rt_max
        or request.ppm_tol != work_item.xic_ppm_tol
    ):
        raise ValueError("acquired trace crosses Discovery XIC request")


__all__ = [
    "DISCOVERY_TRACE_ADAPTER_NAMESPACE",
    "DiscoveryPeakPhysicalBinding",
    "DiscoveryPhysicalFactRecord",
    "validate_discovery_peak_physical_binding",
    "validate_discovery_physical_fact_record",
    "discovery_trace_capability",
    "validate_peak_physical_measurement",
]
