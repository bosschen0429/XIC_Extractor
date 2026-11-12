"""Bind Untargeted partition jobs and their immutable sample result evidence."""

from __future__ import annotations

import copyreg
from collections.abc import Callable, Mapping
from copy import copy
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xic_extractor.raw_mass_recalibration import PreparedRawMassRecalibration

from xic_extractor.acquisition_contract import AcquiredTrace, TraceAcquisitionBatch
from xic_extractor.alignment.backfill.completion_evidence import (
    BackfillCompletionProjection,
)
from xic_extractor.alignment.cell_observation.observations import CidNlCellGroupContext
from xic_extractor.alignment.cid_nl_completion import CidNlWorksetGrouping
from xic_extractor.alignment.cid_nl_inputs import (
    UntargetedCidNlBackfillInput,
)
from xic_extractor.alignment.completion import (
    UntargetWorkflowCompositionRequest,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.drift_evidence import KnownIstdReference
from xic_extractor.alignment.grouping_bootstrap import (
    measured_calibration_anchor_groups,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.discovery.batch_contract import DiscoveryPeakUniverseReceipt
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
from xic_extractor.validation_primitives import (
    require_owner_normalized_text,
)
from xic_extractor.xic_models import XICRequest


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


class UntargetedPartitionError(ValueError):
    """Raised when real-data runtime work cannot close atomically."""


def _required_text(value: object, *, field: str) -> str:
    return require_owner_normalized_text(
        value,
        field=field,
        error_factory=UntargetedPartitionError,
    )


@dataclass(frozen=True, slots=True)
class UntargetedPartitionJob:
    processing_attempt_id: str
    method_subjects: CidNlMethodSubject | UntargetedCidNlMethodSubjectBatch
    sample: SampleMetadata
    method_subject: CidNlMethodSubject
    candidates: tuple[DiscoveryPeak, ...]
    discovery_receipt: DiscoveryPeakUniverseReceipt
    settings: DiscoverySettings
    peak_config: ExtractionConfig
    raw_path: Path
    dll_dir: Path
    work_unit_key: str
    signal_processing_profile: SignalProcessingProfile
    acquisition_source_expectation: AcquisitionSourceExpectation = field(compare=False)
    subject_partition: UntargetedCidNlMethodSubjectBatch | None = None
    support_candidates: tuple[DiscoveryPeak, ...] = ()
    acquisition_cache_dir: Path | None = field(default=None, compare=False)
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
        expected = (
            ("method_subject", CidNlMethodSubject),
            ("discovery_receipt", DiscoveryPeakUniverseReceipt),
            ("settings", DiscoverySettings),
            ("peak_config", ExtractionConfig),
            ("raw_path", Path),
            ("dll_dir", Path),
        )
        for field_name, kind in expected:
            if not isinstance(getattr(self, field_name), kind):
                raise UntargetedPartitionError(f"{field_name} has the wrong owner type")
        if not isinstance(self.candidates, tuple) or any(
            not isinstance(item, DiscoveryPeak) for item in self.candidates
        ):
            raise UntargetedPartitionError(
                "candidates must contain current DiscoveryPeak values"
            )
        if self.subject_partition is not None and not isinstance(
            self.subject_partition, UntargetedCidNlMethodSubjectBatch
        ):
            raise UntargetedPartitionError(
                "subject_partition must be an owner-normalized subject batch or None"
            )
        if not isinstance(self.support_candidates, tuple) or any(
            not isinstance(item, DiscoveryPeak) for item in self.support_candidates
        ):
            raise UntargetedPartitionError(
                "support_candidates must contain current DiscoveryPeak values"
            )
        _required_text(self.work_unit_key, field="work_unit_key")
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
        if self.acquisition_cache_dir is not None and not isinstance(
            self.acquisition_cache_dir, Path
        ):
            raise UntargetedPartitionError(
                "acquisition_cache_dir must be a Path or None"
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
    discovery_receipt: DiscoveryPeakUniverseReceipt,
    settings: DiscoverySettings,
    peak_config: ExtractionConfig,
    raw_path: Path,
    dll_dir: Path,
    signal_processing_profile: SignalProcessingProfile,
    acquisition_source_expectation: AcquisitionSourceExpectation,
    subject_partition: UntargetedCidNlMethodSubjectBatch | None = None,
    support_candidates: tuple[DiscoveryPeak, ...] = (),
    acquisition_cache_dir: Path | None = None,
    prepared_mass_recalibration: PreparedRawMassRecalibration | None = None,
) -> tuple[UntargetedPartitionJob, ...]:
    """Validate a sample once, then bind each subject to those same inputs."""

    if not subject_work_units:
        return ()
    if subject_partition is not None:
        if not isinstance(subject_partition, UntargetedCidNlMethodSubjectBatch):
            raise UntargetedPartitionError(
                "subject_partition must be an owner-normalized subject batch or None"
            )
        partition_subjects = {
            subject.method_subject_id: subject for subject in subject_partition.subjects
        }
        if any(
            partition_subjects.get(subject.method_subject_id) != subject
            for subject, _work_unit_key in subject_work_units
        ):
            raise UntargetedPartitionError(
                "subject work units must belong to the declared subject partition"
            )
    first_subject, first_key = subject_work_units[0]
    first = UntargetedPartitionJob(
        processing_attempt_id=processing_attempt_id,
        method_subjects=method_subjects,
        sample=sample,
        method_subject=first_subject,
        candidates=candidates,
        discovery_receipt=discovery_receipt,
        settings=settings,
        peak_config=peak_config,
        raw_path=raw_path,
        dll_dir=dll_dir,
        work_unit_key=first_key,
        subject_partition=subject_partition,
        support_candidates=support_candidates,
        signal_processing_profile=signal_processing_profile,
        acquisition_source_expectation=acquisition_source_expectation,
        acquisition_cache_dir=acquisition_cache_dir,
        prepared_mass_recalibration=prepared_mass_recalibration,
    )
    jobs = [first]
    for subject, work_unit_key in subject_work_units[1:]:
        if not isinstance(subject, CidNlMethodSubject):
            raise UntargetedPartitionError("method_subject has the wrong owner type")
        _required_text(work_unit_key, field="work_unit_key")
        # Only these two fields vary; every shared field was validated above.
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
class UntargetedPreparedGrouping:
    request: UntargetWorkflowCompositionRequest
    worksets: tuple[CidNlWorksetGrouping, ...]
    alignment_config: AlignmentConfig
    known_istd_reference: KnownIstdReference | None
    rt_frame: SampleRtFrame

    @property
    def rt_anchor_groups(self) -> tuple[tuple[SampleLocalMS1Owner, ...], ...]:
        return tuple(
            owners
            for workset in self.worksets
            for owners in measured_calibration_anchor_groups(
                workset.grouping_scope, workset.retained_candidates
            )
        )


@dataclass(frozen=True, slots=True)
class UntargetedSampleResult:
    job: UntargetedPartitionJob
    adapter_result: CidNlAcquisitionResult
    backfill_input: UntargetedCidNlBackfillInput | None = None
    backfill_attempts: tuple[UntargetedCidNlBackfillAttemptResult, ...] = ()

    @property
    def backfill_attempt(self) -> UntargetedCidNlBackfillAttemptResult | None:
        if len(self.backfill_attempts) > 1:
            raise UntargetedPartitionError(
                "multiple backfill attempts require group selection"
            )
        return self.backfill_attempts[0] if self.backfill_attempts else None

    @property
    def effective_assessments(self) -> tuple[CidNlMethodAssessment, ...]:
        return (self.adapter_result.assessment_input.assessment,) + tuple(
            attempt.assessment for attempt in self.backfill_attempts
        )

    def backfill_for_group(
        self, center: float, *, group_hypothesis_id: str | None = None
    ) -> UntargetedCidNlBackfillAttemptResult | None:
        bound = any(a.group_hypothesis_id is not None for a in self.backfill_attempts)
        if bound:
            matches = tuple(
                attempt
                for attempt in self.backfill_attempts
                if group_hypothesis_id is not None
                and attempt.group_hypothesis_id == group_hypothesis_id
            )
        else:
            # The standalone single-subject runtime predates workset grouping.
            half_window = self.job.peak_config.nl_rt_anchor_half_window_min
            matches = tuple(
                attempt
                for attempt in self.backfill_attempts
                if attempt.assessment.evaluation_receipt.query_rt_min
                == max(0.0, center - half_window)
                and attempt.assessment.evaluation_receipt.query_rt_max
                == center + half_window
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
    if type(result) is not UntargetedSampleResult:
        raise UntargetedPartitionError("backfill requires a runtime result")
    replace(result.job)
    replace(group_context)
    projection = _project_completed_backfill(
        result,
        group_context=group_context,
        exact_traces=_exact_acquisition_traces,
    )
    return projection


def _project_completed_backfill(
    result: UntargetedSampleResult,
    *,
    group_context: CidNlCellGroupContext,
    exact_traces: Callable[
        [TraceAcquisitionBatch, XICRequest, str], tuple[AcquiredTrace, ...]
    ],
) -> BackfillCompletionProjection:
    """Project the completed group query from its actual assessment and trace."""
    job = result.job
    group = group_context.group
    center = group.group_center_rt
    if group_context.method_subject != job.method_subject:
        raise UntargetedPartitionError("backfill crossed its owner group")
    backfill = result.backfill_for_group(
        center, group_hypothesis_id=group_context.group.group_hypothesis_id
    )
    if backfill is None:
        raise UntargetedPartitionError("initial assessment is not a backfill")
    assessment = backfill.assessment
    receipt = assessment.evaluation_receipt
    if (
        assessment.technical_state != "evaluated"
        or assessment.method_result != "HOLD"
        or assessment.candidate_peaks
        or assessment.detector_invocation_id != "untargeted-cid-nl-backfill"
        or not receipt.search_completed
        or not receipt.acquisition_supported
        or backfill.backfill_input is not None
    ):
        raise UntargetedPartitionError("backfill is not a completed empty epoch")
    query_rt = center if backfill.query_rt is None else backfill.query_rt
    expected_request = XICRequest(
        mz=group.group_center_mz,
        rt_min=max(0.0, query_rt - job.peak_config.nl_rt_anchor_half_window_min),
        rt_max=query_rt + job.peak_config.nl_rt_anchor_half_window_min,
        ppm_tol=job.method_subject.precursor_tolerance_ppm,
    )
    exact = exact_traces(
        backfill.acquisition, expected_request, assessment.acquisition_source_id
    )
    if len(exact) != 1 or (
        receipt.query_rt_min != expected_request.rt_min
        or receipt.query_rt_max != expected_request.rt_max
    ):
        raise UntargetedPartitionError("backfill crossed its exact request coordinates")
    (acquired,) = exact
    return BackfillCompletionProjection(
        sample=job.sample,
        group=group,
        group_center_mz=group.group_center_mz,
        group_center_rt=center,
        query_rt=query_rt,
        query_rt_basis=backfill.query_rt_basis,
        backfill_assessment=assessment,
        trace_coordinate=acquired.coordinate,
    )


@dataclass(frozen=True, slots=True)
class UntargetedPartitionResult:
    """Successful sample results, or the first sample failure that stops them.

    An ``InterruptedError`` failure means the work observed cancellation.
    """

    sample_results: tuple[UntargetedSampleResult, ...]
    failure: Exception | None = None
    prepared_grouping: UntargetedPreparedGrouping | None = None

    @property
    def succeeded(self) -> bool:
        return self.failure is None


def _exact_acquisition_traces(
    acquisition: TraceAcquisitionBatch, request: XICRequest, source_id: str
) -> tuple[AcquiredTrace, ...]:
    return tuple(
        replace(acquired)
        for acquired in acquisition.traces
        if acquired.coordinate.request == request
        and acquired.coordinate.capability.acquisition_source.acquisition_source_id
        == source_id
    )


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
            sample=job.sample,
            method_subject=job.method_subject,
            settings=job.settings,
        )
    return job._request_candidates_by_subject.get(
        job.method_subject.method_subject_id, ()
    )


class _SampleResultContext:
    """Reuse completed adapter snapshots within one synchronous owner phase."""

    def __init__(self, jobs: tuple[UntargetedPartitionJob, ...]) -> None:
        self._jobs = {id(job): job for job in jobs}
        self._traces: dict[
            int,
            tuple[
                TraceAcquisitionBatch,
                dict[tuple[XICRequest, str], tuple[AcquiredTrace, ...]],
            ],
        ] = {}

    def exact_traces(
        self, acquisition: TraceAcquisitionBatch, request: XICRequest, source_id: str
    ) -> tuple[AcquiredTrace, ...]:
        cached = self._traces.get(id(acquisition))
        if cached is None:
            grouped: dict[tuple[XICRequest, str], list[AcquiredTrace]] = {}
            for acquired in acquisition.traces:
                key = (
                    acquired.coordinate.request,
                    acquired.coordinate.capability.acquisition_source.acquisition_source_id,
                )
                grouped.setdefault(key, []).append(acquired)
            cached = (
                acquisition,
                {key: tuple(values) for key, values in grouped.items()},
            )
            self._traces[id(acquisition)] = cached
        return cached[1].get((request, source_id), ())

    def project_completed_backfill(
        self,
        result: UntargetedSampleResult,
        *,
        group_context: CidNlCellGroupContext,
    ) -> BackfillCompletionProjection:
        if type(result) is not UntargetedSampleResult:
            raise UntargetedPartitionError("backfill requires a runtime result")
        if self._jobs.get(id(result.job)) is not result.job:
            raise UntargetedPartitionError(
                "backfill result does not reuse an exact runtime batch job"
            )
        return _project_completed_backfill(
            result,
            group_context=group_context,
            exact_traces=self.exact_traces,
        )
