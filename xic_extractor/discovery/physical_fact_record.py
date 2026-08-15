"""Dependency-light Discovery binding for canonical physical facts."""

from __future__ import annotations

from dataclasses import dataclass

from xic_extractor.acquisition_contract import (
    AcquiredTrace,
    acquired_trace_content_id,
)
from xic_extractor.discovery.models import DiscoveryPhysicalWorkItem
from xic_extractor.peak_detection.evidence_facts import PhysicalPeakEvaluation


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


__all__ = ["DiscoveryPhysicalFactRecord"]
