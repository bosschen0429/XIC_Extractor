"""Bind Untargeted partition jobs and their immutable sample result evidence."""

from __future__ import annotations

import copyreg
from collections.abc import Callable, Mapping
from copy import copy
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xic_extractor.raw_mass_recalibration import PreparedRawMassRecalibration

from xic_extractor.alignment.backfill.completion_evidence import (
    BackfillCompletionProjection,
)
from xic_extractor.alignment.cell_observation.observations import CidNlCellGroupContext
from xic_extractor.alignment.cid_nl_initial_grouping import CidNlGrouping
from xic_extractor.alignment.cid_nl_inputs import (
    UntargetedCidNlBackfillInput,
)
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.discovery.models import DiscoveryPeak
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessment,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CidNlMethodSubject,
    UntargetedCidNlMethodSubjectBatch,
)
from xic_extractor.processing_identity import AcquisitionSourceExpectation
from xic_extractor.sample_metadata import SampleMetadata
from xic_extractor.untargeted_execution.partition.cid_nl_backfill_acquisition import (
    UntargetedCidNlBackfillAttemptResult,
)
from xic_extractor.untargeted_execution.partition.cid_nl_sample_acquisition import (
    CidNlAcquisitionResult,
    _bounded_subject_candidates,
)


def _rebuild_mapping_proxy(
    mapping: dict[object, object],
) -> MappingProxyType[object, object]:
    return MappingProxyType(mapping)


def _reduce_mapping_proxy(
    proxy: MappingProxyType[object, object],
) -> tuple[
    Callable[[dict[object, object]], MappingProxyType[object, object]],
    tuple[dict[object, object]],
]:
    return (_rebuild_mapping_proxy, (dict(proxy),))


copyreg.pickle(MappingProxyType, _reduce_mapping_proxy)


SCHEMA_VERSION = "untargeted_cid_nl_real_data_runtime_result"
BACKFILL_HALF_WINDOW_MIN = 1.0


class UntargetedPartitionError(ValueError):
    """Raised when real-data runtime work cannot close atomically."""


@dataclass(frozen=True, slots=True)
class UntargetedPartitionJob:
    processing_attempt_id: str
    method_subjects: CidNlMethodSubject | UntargetedCidNlMethodSubjectBatch
    sample: SampleMetadata
    method_subject: CidNlMethodSubject
    candidates: tuple[DiscoveryPeak, ...]
    settings: DiscoverySettings
    raw_path: Path
    dll_dir: Path
    work_unit_key: str
    signal_processing_profile: SignalProcessingProfile
    acquisition_source_expectation: AcquisitionSourceExpectation = field(compare=False)
    subject_partition: UntargetedCidNlMethodSubjectBatch | None = None
    support_candidates: tuple[DiscoveryPeak, ...] = ()
    prepared_mass_recalibration: PreparedRawMassRecalibration | None = field(
        default=None, compare=False
    )
    _request_subjects: CidNlMethodSubject | UntargetedCidNlMethodSubjectBatch = field(
        init=False, repr=False, compare=False
    )
    _request_candidates_by_subject: Mapping[str, tuple[DiscoveryPeak, ...]] = field(
        init=False, repr=False, compare=False
    )
    _candidate_order: Mapping[str, int] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_request_subjects",
            self.method_subjects
            if self.subject_partition is None
            else self.subject_partition,
        )
        object.__setattr__(
            self,
            "_candidate_order",
            MappingProxyType(
                {
                    candidate.peak_id: index
                    for index, candidate in enumerate(self.candidates)
                }
            ),
        )
        object.__setattr__(
            self,
            "_request_candidates_by_subject",
            _index_request_subject_candidates(self._request_subjects, self.candidates),
        )


def build_sample_jobs(
    *,
    processing_attempt_id: str,
    method_subjects: CidNlMethodSubject | UntargetedCidNlMethodSubjectBatch,
    sample: SampleMetadata,
    subject_work_units: tuple[tuple[CidNlMethodSubject, str], ...],
    candidates: tuple[DiscoveryPeak, ...],
    settings: DiscoverySettings,
    raw_path: Path,
    dll_dir: Path,
    signal_processing_profile: SignalProcessingProfile,
    acquisition_source_expectation: AcquisitionSourceExpectation,
    subject_partition: UntargetedCidNlMethodSubjectBatch | None = None,
    support_candidates: tuple[DiscoveryPeak, ...] = (),
    prepared_mass_recalibration: PreparedRawMassRecalibration | None = None,
) -> tuple[UntargetedPartitionJob, ...]:
    """Bind each subject of one sample to that sample's shared inputs."""

    if not subject_work_units:
        return ()
    first_subject, first_key = subject_work_units[0]
    first = UntargetedPartitionJob(
        processing_attempt_id=processing_attempt_id,
        method_subjects=method_subjects,
        sample=sample,
        method_subject=first_subject,
        candidates=candidates,
        settings=settings,
        raw_path=raw_path,
        dll_dir=dll_dir,
        work_unit_key=first_key,
        subject_partition=subject_partition,
        support_candidates=support_candidates,
        signal_processing_profile=signal_processing_profile,
        acquisition_source_expectation=acquisition_source_expectation,
        prepared_mass_recalibration=prepared_mass_recalibration,
    )
    jobs = [first]
    for subject, work_unit_key in subject_work_units[1:]:
        job = copy(first)
        object.__setattr__(job, "method_subject", subject)
        object.__setattr__(job, "work_unit_key", work_unit_key)
        jobs.append(job)
    return tuple(jobs)


@dataclass(frozen=True, slots=True)
class UntargetedInitialSampleResult:
    """Initial evidence available before backfill completion."""

    job: UntargetedPartitionJob
    adapter_result: CidNlAcquisitionResult

    @property
    def effective_assessments(self) -> tuple[CidNlMethodAssessment, ...]:
        return (self.adapter_result.assessment_input.assessment,)


@dataclass(frozen=True, slots=True)
class UntargetedSampleResult:
    job: UntargetedPartitionJob
    adapter_result: CidNlAcquisitionResult
    backfill_input: UntargetedCidNlBackfillInput | None = None
    backfill_attempts: tuple[UntargetedCidNlBackfillAttemptResult, ...] = ()

    @property
    def effective_assessments(self) -> tuple[CidNlMethodAssessment, ...]:
        return (self.adapter_result.assessment_input.assessment,) + tuple(
            attempt.assessment for attempt in self.backfill_attempts
        )

    def backfill_for_group(
        self, group_hypothesis_id: str
    ) -> UntargetedCidNlBackfillAttemptResult | None:
        matches = tuple(
            attempt
            for attempt in self.backfill_attempts
            if attempt.group_hypothesis_id == group_hypothesis_id
        )
        if len(matches) > 1:
            raise UntargetedPartitionError("group has duplicate backfill attempts")
        return matches[0] if matches else None


def project_completed_backfill(
    result: UntargetedSampleResult,
    *,
    group_context: CidNlCellGroupContext,
) -> BackfillCompletionProjection:
    """Project an existing completed empty backfill without executing it again."""
    job = result.job
    group = group_context.group
    backfill = result.backfill_for_group(group.group_hypothesis_id)
    if backfill is None:
        raise UntargetedPartitionError("initial assessment is not a backfill")
    assessment = backfill.assessment
    receipt = assessment.evaluation_receipt
    if (
        assessment.technical_state != "evaluated"
        or assessment.method_result != "HOLD"
        or assessment.candidate_peaks
        or not receipt.search_completed
        or not receipt.acquisition_supported
        or backfill.backfill_input is not None
        or backfill.acquisition is None
    ):
        raise UntargetedPartitionError("backfill is not a completed empty epoch")
    return BackfillCompletionProjection(
        sample=job.sample,
        group=group,
        backfill_assessment=assessment,
    )


@dataclass(frozen=True, slots=True)
class UntargetedPartitionResult:
    """Successful sample results, or the first sample failure that stops them.

    An ``InterruptedError`` failure means the work observed cancellation.
    """

    sample_results: tuple[UntargetedSampleResult, ...]
    failure: Exception | None = None
    prepared_grouping: CidNlGrouping | None = None

    @property
    def succeeded(self) -> bool:
        return self.failure is None


def _index_request_subject_candidates(
    pinned: CidNlMethodSubject | UntargetedCidNlMethodSubjectBatch,
    candidates: tuple[DiscoveryPeak, ...],
) -> Mapping[str, tuple[DiscoveryPeak, ...]]:
    grouped: dict[str, list[DiscoveryPeak]] = {}
    if isinstance(pinned, UntargetedCidNlMethodSubjectBatch):
        subject_by_candidate = dict(pinned.candidate_memberships)
        for candidate in candidates:
            subject_id = subject_by_candidate.get(candidate.peak_id)
            if subject_id is not None:
                grouped.setdefault(subject_id, []).append(candidate)
    return MappingProxyType(
        {subject_id: tuple(values) for subject_id, values in grouped.items()}
    )


def _request_subject_candidates(
    job: UntargetedPartitionJob,
) -> tuple[DiscoveryPeak, ...]:
    if not isinstance(job._request_subjects, UntargetedCidNlMethodSubjectBatch):
        return _bounded_subject_candidates(
            job.candidates,
            method_subject=job.method_subject,
            settings=job.settings,
        )
    return job._request_candidates_by_subject.get(
        job.method_subject.method_subject_id, ()
    )
