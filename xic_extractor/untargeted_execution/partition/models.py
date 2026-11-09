"""Bind Untargeted partition jobs and their immutable sample result evidence."""

from __future__ import annotations

import copyreg
import json
from collections.abc import Callable, Mapping
from copy import copy
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, cast

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
from xic_extractor.configuration.scientific_settings import (
    scientific_settings_payload,
)
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.discovery.batch_contract import (
    ALIGNMENT_FEATURE_INPUT_KEY,
    CANDIDATE_UNIVERSE_INPUT_KEY,
    DiscoveryPeakUniverseReceipt,
    validate_alignment_feature_selection,
    validate_candidate_universe_payload,
)
from xic_extractor.discovery.models import DiscoveryPeak
from xic_extractor.discovery.physical_fact_record import DiscoveryPhysicalFactRecord
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CidNlMethodSubject,
    CidNlMethodSubjectContractError,
    UntargetedCidNlMethodSubjectBatch,
    cid_nl_method_subjects_from_request,
)
from xic_extractor.peak_detection.search_scope import (
    PHYSICAL_CAPTURE_INPUT_KEY,
    physical_capture_input_payload,
)
from xic_extractor.processing_identity import (
    AcquisitionSourceExpectation,
    IdentityContractError,
    ProcessingOwnerReferences,
    ResolvedCanonicalSampleIdentity,
    resolve_request_acquisition_source,
)
from xic_extractor.untargeted_execution.partition.cid_nl_backfill_acquisition import (
    UntargetedCidNlBackfillAttemptResult,
)
from xic_extractor.untargeted_execution.partition.cid_nl_sample_acquisition import (
    CidNlAcquisitionResult,
    _bounded_subject_candidates,
)
from xic_extractor.untargeted_execution.partition.request import (
    DISCOVERY_SETTINGS_INPUT_KEY,
    PEAK_SETTINGS_INPUT_KEY,
    UNTARGETED_CID_NL_PROCESSING_ALGORITHM_VERSION,
    UNTARGETED_CID_NL_PROCESSING_CONTRACT_VERSION,
    UNTARGETED_CID_NL_PROCESSING_INPUT_SCHEMA_VERSION,
    candidate_universe_payload,
    discovery_settings_payload,
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
    processing_owners: ProcessingOwnerReferences
    sample: ResolvedCanonicalSampleIdentity
    method_subject: CidNlMethodSubject
    candidates: tuple[DiscoveryPeak, ...]
    discovery_receipt: DiscoveryPeakUniverseReceipt
    settings: DiscoverySettings
    peak_config: ExtractionConfig
    raw_path: Path
    dll_dir: Path
    work_unit_key: str
    signal_processing_profile: SignalProcessingProfile
    subject_partition: UntargetedCidNlMethodSubjectBatch | None = None
    support_candidates: tuple[DiscoveryPeak, ...] = ()
    physical_records: tuple[DiscoveryPhysicalFactRecord, ...] = ()
    acquisition_source_expectation: AcquisitionSourceExpectation | None = field(
        default=None, compare=False
    )
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
    _request_physical_records_by_subject: Mapping[
        str, tuple[DiscoveryPhysicalFactRecord, ...]
    ] = field(init=False, repr=False, compare=False)
    _candidate_order: Mapping[str, int] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        expected = (
            ("processing_owners", ProcessingOwnerReferences),
            ("sample", ResolvedCanonicalSampleIdentity),
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
            self, "_request_subjects", _validate_request_bound_inputs(self)
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
        if self.acquisition_source_expectation is not None:
            if not isinstance(
                self.acquisition_source_expectation, AcquisitionSourceExpectation
            ) or self.acquisition_source_expectation.source != (
                resolve_request_acquisition_source(
                    processing_owners=self.processing_owners, sample=self.sample
                )
            ):
                raise UntargetedPartitionError(
                    "acquisition source expectation must match the request sample"
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
        object.__setattr__(
            self,
            "_request_physical_records_by_subject",
            _index_request_subject_physical_records(
                self._request_subjects, self.physical_records
            ),
        )


def build_sample_jobs(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    subject_work_units: tuple[tuple[CidNlMethodSubject, str], ...],
    candidates: tuple[DiscoveryPeak, ...],
    discovery_receipt: DiscoveryPeakUniverseReceipt,
    settings: DiscoverySettings,
    peak_config: ExtractionConfig,
    raw_path: Path,
    dll_dir: Path,
    signal_processing_profile: SignalProcessingProfile,
    physical_records: tuple[DiscoveryPhysicalFactRecord, ...] = (),
    acquisition_source_expectation: AcquisitionSourceExpectation | None = None,
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
        processing_owners=processing_owners,
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
        physical_records=physical_records,
        acquisition_source_expectation=acquisition_source_expectation,
        acquisition_cache_dir=acquisition_cache_dir,
        prepared_mass_recalibration=prepared_mass_recalibration,
    )
    pinned = first._request_subjects
    subjects_by_id = (
        {subject.method_subject_id: subject for subject in pinned.subjects}
        if isinstance(pinned, UntargetedCidNlMethodSubjectBatch)
        else None
    )
    jobs = [first]
    for subject, work_unit_key in subject_work_units[1:]:
        if not isinstance(subject, CidNlMethodSubject):
            raise UntargetedPartitionError("method_subject has the wrong owner type")
        _required_text(work_unit_key, field="work_unit_key")
        if subjects_by_id is not None:
            if subjects_by_id.get(subject.method_subject_id) != subject:
                raise UntargetedPartitionError(
                    "method subject is not an exact member of the processing request"
                )
        elif pinned != subject:
            raise UntargetedPartitionError(
                "method subject does not match the processing request"
            )
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
    def effective_assessments(self) -> tuple[CidNlMethodAssessmentRef, ...]:
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
    def effective_assessments(self) -> tuple[CidNlMethodAssessmentRef, ...]:
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
                if attempt.assessment.content.evaluation_receipt.query_rt_min
                == max(0.0, center - half_window)
                and attempt.assessment.content.evaluation_receipt.query_rt_max
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
    group = group_context.group.membership
    assert group is not None
    center = group_context.group.group_center_rt
    if (
        group_context.processing_attempt is not job.processing_owners.attempt
        or group_context.method_subject != job.method_subject
        or center is None
    ):
        raise UntargetedPartitionError("backfill crossed its owner group")
    backfill = result.backfill_for_group(
        center, group_hypothesis_id=group_context.group.group_hypothesis_id
    )
    if backfill is None:
        raise UntargetedPartitionError("initial assessment is not a backfill")
    assessment = backfill.assessment
    receipt = assessment.content.evaluation_receipt
    if (
        assessment.content.technical_state != "evaluated"
        or assessment.content.method_result != "HOLD"
        or assessment.candidate_peaks
        or assessment.content.detector_invocation_id != "untargeted-cid-nl-backfill"
        or not receipt.search_completed
        or not receipt.acquisition_supported
        or backfill.backfill_input is not None
    ):
        raise UntargetedPartitionError("backfill is not a completed empty epoch")
    query_rt = center if backfill.query_rt is None else backfill.query_rt
    expected_request = XICRequest(
        mz=group_context.group.family_center_mz,
        rt_min=max(0.0, query_rt - job.peak_config.nl_rt_anchor_half_window_min),
        rt_max=query_rt + job.peak_config.nl_rt_anchor_half_window_min,
        ppm_tol=job.method_subject.precursor_tolerance_ppm,
    )
    exact = exact_traces(
        backfill.acquisition, expected_request, assessment.content.acquisition_source_id
    )
    if len(exact) != 1 or (
        receipt.query_rt_min != expected_request.rt_min
        or receipt.query_rt_max != expected_request.rt_max
    ):
        raise UntargetedPartitionError("backfill crossed its exact request coordinates")
    (acquired,) = exact
    return BackfillCompletionProjection(
        processing_owners=job.processing_owners,
        sample=job.sample,
        group_identity=group,
        group_center_mz=group_context.group.family_center_mz,
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


def _validate_request_bound_inputs(
    job: UntargetedPartitionJob,
) -> CidNlMethodSubject | UntargetedCidNlMethodSubjectBatch:
    request = job.processing_owners.request
    if (
        request.scientific_input_schema_version
        != UNTARGETED_CID_NL_PROCESSING_INPUT_SCHEMA_VERSION
        or request.processing_algorithm_version
        != UNTARGETED_CID_NL_PROCESSING_ALGORITHM_VERSION
        or request.processing_contract_version
        != UNTARGETED_CID_NL_PROCESSING_CONTRACT_VERSION
    ):
        raise UntargetedPartitionError(
            "processing request does not use the supported real-data runtime contract"
        )
    expected_stems = tuple(
        binding.adapter_input_key for binding in request.acquisition_bindings
    )
    try:
        request_inputs, universe_by_sample, feature_ids = _request_input_index(
            request.normalized_scientific_inputs_json,
            expected_stems,
        )
    except (IdentityContractError, TypeError, ValueError) as exc:
        raise UntargetedPartitionError(
            "processing request scientific inputs are malformed"
        ) from exc
    physical_payload = request_inputs.get(PHYSICAL_CAPTURE_INPUT_KEY)
    records = {record.peak_id: record for record in job.physical_records}
    if (
        type(job.physical_records) is not tuple
        or len(records) != len(job.physical_records)
        or set(records) != {candidate.peak_id for candidate in job.candidates}
    ):
        raise UntargetedPartitionError("physical replay candidate coverage changed")
    expected_physical = physical_capture_input_payload(
        job.signal_processing_profile,
    )
    if (
        not isinstance(physical_payload, dict)
        or physical_payload.get("profile") != expected_physical["profile"]
    ):
        raise UntargetedPartitionError("physical replay profile is not request-bound")
    try:
        pinned = cid_nl_method_subjects_from_request(request)
    except CidNlMethodSubjectContractError as exc:
        raise UntargetedPartitionError(
            "processing request has an invalid CID-NL method subject configuration"
        ) from exc
    if isinstance(pinned, UntargetedCidNlMethodSubjectBatch):
        matching_subjects = tuple(
            subject
            for subject in pinned.subjects
            if subject.method_subject_id == job.method_subject.method_subject_id
        )
        if matching_subjects != (job.method_subject,):
            raise UntargetedPartitionError(
                "method subject is not an exact member of the processing request"
            )
    elif pinned != job.method_subject:
        raise UntargetedPartitionError(
            "method subject does not match the processing request"
        )
    if request_inputs.get(DISCOVERY_SETTINGS_INPUT_KEY) != (
        discovery_settings_payload(job.settings)
    ):
        raise UntargetedPartitionError(
            "Discovery settings do not match the processing request"
        )
    expected_peak_settings = scientific_settings_payload(job.peak_config)
    if request_inputs.get(PEAK_SETTINGS_INPUT_KEY) != expected_peak_settings:
        raise UntargetedPartitionError(
            "peak settings do not match the processing request"
        )
    universe = request_inputs.get(CANDIDATE_UNIVERSE_INPUT_KEY)
    sample_entry = universe_by_sample.get(job.sample.metadata.raw_stem)
    if sample_entry is None:
        raise UntargetedPartitionError(
            "candidate universe does not contain the request sample"
        )
    active_subjects = _validate_subject_partition(job, pinned)
    if job.subject_partition is not None or feature_ids is not None:
        _validate_partition_candidates(
            job,
            pinned=pinned,
            active_subjects=active_subjects,
            sample_entry=sample_entry,
            feature_ids=feature_ids,
        )
        return active_subjects
    try:
        expected_payload = candidate_universe_payload(
            {job.sample.metadata.raw_stem: job.candidates},
            discovery_receipts_by_sample={
                job.sample.metadata.raw_stem: job.discovery_receipt
            },
            expected_sample_stems=(job.sample.metadata.raw_stem,),
        )
    except IdentityContractError as exc:
        raise UntargetedPartitionError(
            "candidate universe does not match the processing request"
        ) from exc
    assert isinstance(universe, dict)
    entries = universe["samples"]
    assert isinstance(entries, list)
    expected_entries = expected_payload["samples"]
    assert isinstance(expected_entries, list)
    matches = [
        entry
        for entry in entries
        if isinstance(entry, dict)
        and entry.get("sample_stem") == job.sample.metadata.raw_stem
    ]
    if len(matches) != 1 or matches != expected_entries:
        raise UntargetedPartitionError(
            "candidate universe does not match the processing request"
        )
    if job.support_candidates:
        raise UntargetedPartitionError(
            "support candidates require an alignment feature selection"
        )
    return active_subjects


@lru_cache(maxsize=1)
def _request_input_index(
    normalized_inputs_json: str,
    expected_sample_stems: tuple[str, ...],
) -> tuple[
    dict[str, object],
    Mapping[str, dict[str, object]],
    frozenset[str] | None,
]:
    values = json.loads(normalized_inputs_json)
    if not isinstance(values, dict):
        raise UntargetedPartitionError(
            "processing request scientific inputs must be a mapping"
        )
    entries = validate_candidate_universe_payload(
        values.get(CANDIDATE_UNIVERSE_INPUT_KEY),
        expected_sample_stems=expected_sample_stems,
    )
    by_sample = MappingProxyType(
        {str(entry["sample_stem"]): entry for entry in entries}
    )
    selected_ids = validate_alignment_feature_selection(
        values.get(ALIGNMENT_FEATURE_INPUT_KEY),
        candidate_ids=(
            cid for entry in entries for cid in cast(list[str], entry["candidate_ids"])
        ),
    )
    feature_ids = None if selected_ids is None else frozenset(selected_ids)
    return values, by_sample, feature_ids


def _validate_subject_partition(
    job: UntargetedPartitionJob,
    pinned: CidNlMethodSubject | UntargetedCidNlMethodSubjectBatch,
) -> CidNlMethodSubject | UntargetedCidNlMethodSubjectBatch:
    partition = job.subject_partition
    if partition is None:
        return pinned
    if not isinstance(pinned, UntargetedCidNlMethodSubjectBatch):
        raise UntargetedPartitionError(
            "subject partition requires a batched processing request"
        )
    pinned_subjects = {
        subject.method_subject_id: subject for subject in pinned.subjects
    }
    if any(
        pinned_subjects.get(subject.method_subject_id) != subject
        for subject in partition.subjects
    ):
        raise UntargetedPartitionError(
            "subject partition contains a foreign method subject"
        )
    if not set(partition.candidate_memberships).issubset(pinned.candidate_memberships):
        raise UntargetedPartitionError(
            "subject partition contains a foreign candidate membership"
        )
    if job.method_subject not in partition.subjects:
        raise UntargetedPartitionError(
            "method subject is outside the declared subject partition"
        )
    return partition


def _validate_partition_candidates(
    job: UntargetedPartitionJob,
    *,
    pinned: CidNlMethodSubject | UntargetedCidNlMethodSubjectBatch,
    active_subjects: CidNlMethodSubject | UntargetedCidNlMethodSubjectBatch,
    sample_entry: dict[str, object],
    feature_ids: frozenset[str] | None,
) -> None:
    raw_source_ids = sample_entry["candidate_ids"]
    assert isinstance(raw_source_ids, list)
    source_ids = frozenset(raw_source_ids)
    selected_ids = source_ids if feature_ids is None else source_ids & feature_ids
    if isinstance(active_subjects, UntargetedCidNlMethodSubjectBatch):
        selected_ids &= {
            candidate_id
            for candidate_id, _subject_id in active_subjects.candidate_memberships
        }
    actual_ids = tuple(candidate.peak_id for candidate in job.candidates)
    if len(actual_ids) != len(set(actual_ids)) or set(actual_ids) != selected_ids:
        raise UntargetedPartitionError(
            "partition candidates do not exactly cover its selected feature IDs"
        )
    _validate_sample_candidates(job.candidates, job=job, label="partition")
    support_ids = tuple(candidate.peak_id for candidate in job.support_candidates)
    membership_ids = (
        {candidate_id for candidate_id, _subject_id in pinned.candidate_memberships}
        if isinstance(pinned, UntargetedCidNlMethodSubjectBatch)
        else selected_ids
    )
    if (
        len(support_ids) != len(set(support_ids))
        or not set(support_ids).issubset(source_ids)
        or set(support_ids) & (set(actual_ids) | membership_ids)
    ):
        raise UntargetedPartitionError(
            "support candidates must be distinct non-member rows from this "
            "source universe"
        )
    _validate_sample_candidates(job.support_candidates, job=job, label="support")
    receipt = job.discovery_receipt
    expected_receipt_values = (
        (
            "candidate_artifact_schema_version",
            receipt.peak_artifact_schema_version,
        ),
        ("candidate_artifact_sha256", receipt.peak_artifact_sha256),
        ("candidate_artifact_byte_size", receipt.peak_artifact_byte_size),
        (
            "acquisition_source_id",
            receipt.acquisition_source.acquisition_source_id,
        ),
    )
    if receipt.sample_stem != job.sample.metadata.raw_stem or any(
        sample_entry.get(key) != value for key, value in expected_receipt_values
    ):
        raise UntargetedPartitionError(
            "Discovery receipt metadata does not match the source universe"
        )


def _validate_sample_candidates(
    candidates: tuple[DiscoveryPeak, ...],
    *,
    job: UntargetedPartitionJob,
    label: str,
) -> None:
    if any(
        candidate.sample_stem != job.sample.metadata.raw_stem
        or candidate.raw_file.stem != job.sample.metadata.raw_stem
        for candidate in candidates
    ):
        raise UntargetedPartitionError(
            f"{label} candidate references a different sample"
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


def _index_request_subject_physical_records(
    pinned: CidNlMethodSubject | UntargetedCidNlMethodSubjectBatch,
    records: tuple[DiscoveryPhysicalFactRecord, ...],
) -> Mapping[str, tuple[DiscoveryPhysicalFactRecord, ...]]:
    grouped: dict[str, list[DiscoveryPhysicalFactRecord]] = {}
    if isinstance(pinned, UntargetedCidNlMethodSubjectBatch):
        subject_by_candidate = dict(pinned.candidate_memberships)
        for record in records:
            subject_id = subject_by_candidate.get(record.peak_id)
            if subject_id is not None:
                grouped.setdefault(subject_id, []).append(record)
    return MappingProxyType(
        {subject_id: tuple(values) for subject_id, values in grouped.items()}
    )


def _request_subject_physical_records(
    job: UntargetedPartitionJob,
) -> tuple[DiscoveryPhysicalFactRecord, ...]:
    if not isinstance(job._request_subjects, UntargetedCidNlMethodSubjectBatch):
        return job.physical_records
    return job._request_physical_records_by_subject.get(
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
