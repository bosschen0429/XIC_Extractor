"""Data models, contracts, and validation helpers for untargeted real-data runtime."""

from __future__ import annotations

import copyreg
import json
from collections.abc import Callable, Mapping
from copy import copy
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType

from xic_extractor.acquisition_contract import AcquiredTrace, TraceAcquisitionBatch
from xic_extractor.alignment.backfill.evaluation_coverage import (
    _COMPLETED_BACKFILL_CONTRACT,
    BackfillCompletionProjection,
    _completed_backfill_projection_bytes,
)
from xic_extractor.alignment.cell_observations import CidNlCellGroupContext
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample_peak_groups import (
    validate_cross_sample_peak_group_identity,
)
from xic_extractor.alignment.drift_evidence import KnownIstdReference
from xic_extractor.alignment.grouping_result import CrossSampleGroupingResult
from xic_extractor.alignment.mode_track.assignment import ModeTrackCandidateObservation
from xic_extractor.alignment.mode_track.assignment_scope import ModeTrackAssignmentScope
from xic_extractor.alignment.untargeted_cid_nl_golden_path import (
    UntargetedCidNlBackfillInput,
)
from xic_extractor.alignment.workflow import (
    CaptureWorkflowArguments,
    UntargetWorkflowCompositionRequest,
)
from xic_extractor.config import ExtractionConfig
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.discovery.batch_contract import (
    ALIGNMENT_FEATURE_INPUT_KEY,
    CANDIDATE_UNIVERSE_INPUT_KEY,
    DiscoveryCandidateUniverseReceipt,
    validate_candidate_universe_payload,
)
from xic_extractor.discovery.models import DiscoveryCandidate, DiscoverySettings
from xic_extractor.discovery.physical_fact_record import DiscoveryPhysicalFactRecord
from xic_extractor.evidence_artifacts import (
    EvidenceArtifactRef,
    _mint_artifact_semantic_contract_ref,
    _mint_evidence_artifact_ref,
    verify_evidence_artifact_payload,
)
from xic_extractor.execution_runtime import WorkUnitTerminalOutcome
from xic_extractor.execution_runtime_owner import (
    RuntimeAcquisitionReceipt,
    RuntimeWorkerBatchReceipt,
)
from xic_extractor.extraction.targeted_inputs import (
    TARGETED_SCIENTIFIC_SETTING_FIELDS,
)
from xic_extractor.extraction.untargeted_cid_nl_real_data_adapter import (
    UntargetedCidNlBackfillAttemptResult,
    UntargetedCidNlRealDataAdapterResult,
    _bounded_subject_candidates,
)
from xic_extractor.extraction.untargeted_cid_nl_real_data_inputs import (
    DISCOVERY_SETTINGS_INPUT_KEY,
    PEAK_SETTINGS_INPUT_KEY,
    UNTARGETED_CID_NL_PROCESSING_ALGORITHM_VERSION,
    UNTARGETED_CID_NL_PROCESSING_CONTRACT_VERSION,
    UNTARGETED_CID_NL_PROCESSING_INPUT_SCHEMA_VERSION,
    candidate_universe_payload,
    discovery_settings_payload,
)
from xic_extractor.peak_detection.capture import SampleLocalCapture
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
    validate_cid_nl_method_assessment_binding,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CidNlMethodSubject,
    CidNlMethodSubjectContractError,
    UntargetedCidNlMethodSubjectBatch,
    cid_nl_method_subjects_from_request,
)
from xic_extractor.peak_detection.search_scope import (
    PHYSICAL_CAPTURE_INPUT_KEY,
    UntargetedSearchScope,
    physical_capture_input_payload,
)
from xic_extractor.processing_identity import (
    AcquisitionSourceExpectation,
    IdentityContractError,
    ProcessingOwnerReferences,
    ResolvedCanonicalSampleIdentity,
    resolve_request_acquisition_source,
)
from xic_extractor.validation_primitives import (
    require_owner_normalized_text,
    sha256_hexdigest,
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
VALIDATION_STATUS = "diagnostic_only"
RUNTIME_CALL_COST_METRIC_NAMES = (
    "raw_open_count",
    "logical_xic_request_count",
    "xic_batch_count",
    "adapter_chromatogram_call_count",
    "trace_point_count",
)
_RUNTIME_COMPATIBILITY_TOKEN = "untargeted-cid-nl-real-data-runtime"
_ARTIFACT_SEMANTIC_CONTRACT = _mint_artifact_semantic_contract_ref(
    owner_namespace="xic_extractor.untargeted_cid_nl_real_data_runtime",
    semantic_role="untargeted_cid_nl_real_data_sample_result",
    schema_version=SCHEMA_VERSION,
    validation_status=VALIDATION_STATUS,
    media_type="application/json",
    serialization_format="canonical-json-utf8-lf",
)


class UntargetedCidNlRealDataRuntimeError(ValueError):
    """Raised when real-data runtime work cannot close atomically."""


def _required_text(value: object, *, field: str) -> str:
    return require_owner_normalized_text(
        value,
        field=field,
        error_factory=UntargetedCidNlRealDataRuntimeError,
    )


@dataclass(frozen=True, slots=True)
class UntargetedCidNlRealDataRuntimeJob:
    processing_owners: ProcessingOwnerReferences
    sample: ResolvedCanonicalSampleIdentity
    method_subject: CidNlMethodSubject
    candidates: tuple[DiscoveryCandidate, ...]
    discovery_receipt: DiscoveryCandidateUniverseReceipt
    settings: DiscoverySettings
    peak_config: ExtractionConfig
    raw_path: Path
    dll_dir: Path
    work_unit_key: str
    subject_partition: UntargetedCidNlMethodSubjectBatch | None = None
    support_candidates: tuple[DiscoveryCandidate, ...] = ()
    signal_processing_profile: SignalProcessingProfile | None = None
    physical_records: tuple[DiscoveryPhysicalFactRecord, ...] = ()
    acquisition_source_expectation: AcquisitionSourceExpectation | None = field(
        default=None, compare=False
    )
    acquisition_cache_dir: Path | None = field(default=None, compare=False)
    _request_subjects: CidNlMethodSubject | UntargetedCidNlMethodSubjectBatch = field(
        init=False, repr=False, compare=False
    )
    _request_candidates_by_subject: Mapping[str, tuple[DiscoveryCandidate, ...]] = (
        field(init=False, repr=False, compare=False)
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
            ("discovery_receipt", DiscoveryCandidateUniverseReceipt),
            ("settings", DiscoverySettings),
            ("peak_config", ExtractionConfig),
            ("raw_path", Path),
            ("dll_dir", Path),
        )
        for field_name, kind in expected:
            if not isinstance(getattr(self, field_name), kind):
                raise UntargetedCidNlRealDataRuntimeError(
                    f"{field_name} has the wrong owner type"
                )
        if not isinstance(self.candidates, tuple) or any(
            not isinstance(item, DiscoveryCandidate) for item in self.candidates
        ):
            raise UntargetedCidNlRealDataRuntimeError(
                "candidates must contain current DiscoveryCandidate values"
            )
        if self.subject_partition is not None and not isinstance(
            self.subject_partition, UntargetedCidNlMethodSubjectBatch
        ):
            raise UntargetedCidNlRealDataRuntimeError(
                "subject_partition must be an owner-normalized subject batch or None"
            )
        if not isinstance(self.support_candidates, tuple) or any(
            not isinstance(item, DiscoveryCandidate) for item in self.support_candidates
        ):
            raise UntargetedCidNlRealDataRuntimeError(
                "support_candidates must contain current DiscoveryCandidate values"
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
                    candidate.candidate_id: index
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
                raise UntargetedCidNlRealDataRuntimeError(
                    "acquisition source expectation must match the request sample"
                )
        if self.acquisition_cache_dir is not None and not isinstance(
            self.acquisition_cache_dir, Path
        ):
            raise UntargetedCidNlRealDataRuntimeError(
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


def build_untargeted_cid_nl_sample_runtime_jobs(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    subject_work_units: tuple[tuple[CidNlMethodSubject, str], ...],
    candidates: tuple[DiscoveryCandidate, ...],
    discovery_receipt: DiscoveryCandidateUniverseReceipt,
    settings: DiscoverySettings,
    peak_config: ExtractionConfig,
    raw_path: Path,
    dll_dir: Path,
    signal_processing_profile: SignalProcessingProfile | None = None,
    physical_records: tuple[DiscoveryPhysicalFactRecord, ...] = (),
    acquisition_source_expectation: AcquisitionSourceExpectation | None = None,
    subject_partition: UntargetedCidNlMethodSubjectBatch | None = None,
    support_candidates: tuple[DiscoveryCandidate, ...] = (),
    acquisition_cache_dir: Path | None = None,
) -> tuple[UntargetedCidNlRealDataRuntimeJob, ...]:
    """Validate a sample once, then bind each subject to those same inputs."""

    if not subject_work_units:
        return ()
    if subject_partition is not None:
        if not isinstance(subject_partition, UntargetedCidNlMethodSubjectBatch):
            raise UntargetedCidNlRealDataRuntimeError(
                "subject_partition must be an owner-normalized subject batch or None"
            )
        partition_subjects = {
            subject.method_subject_id: subject for subject in subject_partition.subjects
        }
        if any(
            partition_subjects.get(subject.method_subject_id) != subject
            for subject, _work_unit_key in subject_work_units
        ):
            raise UntargetedCidNlRealDataRuntimeError(
                "subject work units must belong to the declared subject partition"
            )
    first_subject, first_key = subject_work_units[0]
    first = UntargetedCidNlRealDataRuntimeJob(
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
            raise UntargetedCidNlRealDataRuntimeError(
                "method_subject has the wrong owner type"
            )
        _required_text(work_unit_key, field="work_unit_key")
        if subjects_by_id is not None:
            if subjects_by_id.get(subject.method_subject_id) != subject:
                raise UntargetedCidNlRealDataRuntimeError(
                    "method subject is not an exact member of the processing request"
                )
        elif pinned != subject:
            raise UntargetedCidNlRealDataRuntimeError(
                "method subject does not match the processing request"
            )
        # Only these two fields vary; every shared field was validated above.
        job = copy(first)
        object.__setattr__(job, "method_subject", subject)
        object.__setattr__(job, "work_unit_key", work_unit_key)
        jobs.append(job)
    return tuple(jobs)


def canonical_untargeted_cid_nl_sample_result_bytes(
    reference: UntargetedCidNlSampleResultRef,
) -> bytes:
    return _sample_result_bytes(
        work_unit_key=reference.work_unit_key,
        canonical_sample_identity_id=reference.canonical_sample_identity_id,
        method_subject_id=reference.method_subject_id,
        discovery_receipt_id=reference.discovery_receipt_id,
        assessment_artifact_id=reference.assessment_artifact_id,
        discovery_projection_artifact_ids=(reference.discovery_projection_artifact_ids),
        source_candidate_ids=reference.source_candidate_ids,
        strict_scan_count=reference.strict_scan_count,
        backfill_assessment_artifact_id=(reference.backfill_assessment_artifact_id),
        backfill_candidate_count=reference.backfill_candidate_count,
        acquisition_sha256=reference.acquisition_sha256,
        backfill_assessment_artifact_ids=reference.backfill_assessment_artifact_ids,
        backfill_query_bounds=reference.backfill_query_bounds,
        backfill_group_hypothesis_ids=reference.backfill_group_hypothesis_ids,
    )


def _sample_result_bytes(**values: object) -> bytes:
    return (
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "validation_status": VALIDATION_STATUS,
                **values,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class UntargetedCidNlSampleResultRef:
    work_unit_key: str
    canonical_sample_identity_id: str
    method_subject_id: str
    discovery_receipt_id: str
    assessment_artifact_id: str
    discovery_projection_artifact_ids: tuple[str, ...]
    source_candidate_ids: tuple[str, ...]
    strict_scan_count: int
    backfill_assessment_artifact_id: str | None
    backfill_candidate_count: int
    acquisition_sha256: str | None
    backfill_assessment_artifact_ids: tuple[str, ...]
    backfill_query_bounds: tuple[tuple[float, float], ...]
    backfill_group_hypothesis_ids: tuple[str | None, ...]
    artifact: EvidenceArtifactRef

    def __post_init__(self) -> None:
        if self.artifact.semantic_contract != _ARTIFACT_SEMANTIC_CONTRACT:
            raise UntargetedCidNlRealDataRuntimeError(
                "sample result artifact has the wrong semantic owner"
            )
        try:
            verify_evidence_artifact_payload(
                self.artifact,
                canonical_untargeted_cid_nl_sample_result_bytes(self),
            )
        except ValueError as exc:
            raise UntargetedCidNlRealDataRuntimeError(
                "sample result mismatches its immutable artifact"
            ) from exc


@dataclass(frozen=True, slots=True)
class UntargetedCidNlInitialSampleResult:
    """Initial evidence available before backfill and runtime result sealing."""

    job: UntargetedCidNlRealDataRuntimeJob
    adapter_result: UntargetedCidNlRealDataAdapterResult

    @property
    def effective_assessments(self) -> tuple[CidNlMethodAssessmentRef, ...]:
        return (self.adapter_result.assessment_input.assessment,)


@dataclass(frozen=True, slots=True)
class UntargetedCidNlWorksetGrouping:
    """The existing grouping owner's answer and its exact capture inputs."""

    subjects: tuple[CidNlMethodSubject, ...]
    scope: UntargetedSearchScope
    captures: tuple[SampleLocalCapture, ...]
    capture_inputs: tuple[CaptureWorkflowArguments, ...]
    assignment_scope: ModeTrackAssignmentScope
    grouping_result: CrossSampleGroupingResult
    retained_candidates: tuple[ModeTrackCandidateObservation, ...]


@dataclass(frozen=True, slots=True)
class UntargetedCidNlPreparedGrouping:
    request: UntargetWorkflowCompositionRequest
    worksets: tuple[UntargetedCidNlWorksetGrouping, ...]
    alignment_config: AlignmentConfig
    known_istd_reference: KnownIstdReference | None


@dataclass(frozen=True, slots=True)
class UntargetedCidNlRuntimeSampleResult:
    job: UntargetedCidNlRealDataRuntimeJob
    adapter_result: UntargetedCidNlRealDataAdapterResult
    result_ref: UntargetedCidNlSampleResultRef
    backfill_input: UntargetedCidNlBackfillInput | None = None
    backfill_attempts: tuple[UntargetedCidNlBackfillAttemptResult, ...] = ()

    @property
    def backfill_attempt(self) -> UntargetedCidNlBackfillAttemptResult | None:
        if len(self.backfill_attempts) > 1:
            raise UntargetedCidNlRealDataRuntimeError(
                "multiple backfill attempts require group selection"
            )
        return self.backfill_attempts[0] if self.backfill_attempts else None

    @property
    def effective_assessments(self) -> tuple[CidNlMethodAssessmentRef, ...]:
        return (self.adapter_result.assessment_input.assessment,) + tuple(
            attempt.assessment for attempt in self.backfill_attempts
        )

    @property
    def effective_assessment(self) -> CidNlMethodAssessmentRef:
        backfill = self.backfill_attempt
        return (
            backfill.assessment
            if backfill is not None
            else self.adapter_result.assessment_input.assessment
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
            raise UntargetedCidNlRealDataRuntimeError(
                "group has duplicate backfill attempts"
            )
        return matches[0] if matches else None


def project_untargeted_cid_nl_completed_backfill(
    result: UntargetedCidNlRuntimeSampleResult,
    *,
    group_context: CidNlCellGroupContext,
) -> BackfillCompletionProjection:
    """Project an existing completed empty backfill without executing it again."""
    if type(result) is not UntargetedCidNlRuntimeSampleResult:
        raise UntargetedCidNlRealDataRuntimeError("backfill requires a runtime result")
    replace(result.job)
    replace(group_context)
    projection = _project_completed_backfill(
        result,
        group_context=group_context,
        exact_traces=_exact_acquisition_traces,
    )
    backfill = result.backfill_for_group(
        projection.group_center_rt,
        group_hypothesis_id=group_context.group.group_hypothesis_id,
    )
    assert backfill is not None
    replace(result.result_ref)
    expected_result = _mint_sample_result_ref(
        job=result.job,
        result=result.adapter_result,
        backfill_input=result.backfill_input,
        backfill_attempt=None,
        backfill_attempts=result.backfill_attempts,
        acquisition=backfill.acquisition,
        acquisition_content=_canonical_acquisition_content,
    )
    if result.result_ref != expected_result:
        raise UntargetedCidNlRealDataRuntimeError(
            "completed backfill lost its sealed runtime result"
        )
    return projection


def _project_completed_backfill(
    result: UntargetedCidNlRuntimeSampleResult,
    *,
    group_context: CidNlCellGroupContext,
    exact_traces: Callable[
        [TraceAcquisitionBatch, XICRequest, str], tuple[AcquiredTrace, ...]
    ],
) -> BackfillCompletionProjection:
    """Bind an admitted batch result; standalone replay validates at its entry."""
    job = result.job
    group = validate_cross_sample_peak_group_identity(
        group_context.group, require_canonical=True
    )
    assert group is not None
    center = group_context.group.group_center_rt
    if (
        group_context.processing_attempt is not job.processing_owners.attempt
        or group_context.method_subject != job.method_subject
        or center is None
    ):
        raise UntargetedCidNlRealDataRuntimeError("backfill crossed its owner group")
    backfill = result.backfill_for_group(
        center, group_hypothesis_id=group_context.group.group_hypothesis_id
    )
    if backfill is None:
        raise UntargetedCidNlRealDataRuntimeError(
            "initial assessment is not a backfill"
        )
    assessment = backfill.assessment
    initial = result.adapter_result.assessment_input.assessment
    for reference in (initial, assessment):
        validate_cid_nl_method_assessment_binding(
            reference,
            processing_owners=job.processing_owners,
            sample=job.sample,
            method_subject=job.method_subject,
        )
    receipt = assessment.content.evaluation_receipt
    if (
        assessment.content.technical_state != "evaluated"
        or assessment.content.method_result != "HOLD"
        or assessment.candidate_peaks
        or assessment.content.detector_invocation_id
        != "untargeted-cid-nl-backfill"
        or not receipt.search_completed
        or not receipt.acquisition_supported
        or backfill.backfill_input is not None
    ):
        raise UntargetedCidNlRealDataRuntimeError(
            "backfill is not a completed empty epoch"
        )
    expected_request = XICRequest(
        mz=job.method_subject.precursor_mz,
        rt_min=max(0.0, center - job.peak_config.nl_rt_anchor_half_window_min),
        rt_max=center + job.peak_config.nl_rt_anchor_half_window_min,
        ppm_tol=job.method_subject.precursor_tolerance_ppm,
    )
    exact = exact_traces(
        backfill.acquisition, expected_request, assessment.content.acquisition_source_id
    )
    if len(exact) != 1 or (
        receipt.query_rt_min != expected_request.rt_min
        or receipt.query_rt_max != expected_request.rt_max
    ):
        raise UntargetedCidNlRealDataRuntimeError(
            "backfill crossed its exact request coordinates"
        )
    (acquired,) = exact
    digest = result.result_ref.acquisition_sha256
    assert digest is not None
    artifact = _mint_evidence_artifact_ref(
        producer_processing_request_id=job.processing_owners.request.processing_request_id,
        producer_processing_attempt_id=job.processing_owners.attempt.processing_attempt_id,
        semantic_contract=_COMPLETED_BACKFILL_CONTRACT,
        payload_bytes=_completed_backfill_projection_bytes(
            processing_owners=job.processing_owners,
            sample=job.sample,
            group_identity=group,
            group_center_rt=center,
            source_result_artifact=result.result_ref.artifact,
            initial_assessment_artifact=initial.artifact,
            backfill_assessment=assessment,
            trace_coordinate=acquired.coordinate,
            acquisition_sha256=digest,
        ),
        upstream_artifacts=(result.result_ref.artifact, assessment.artifact),
    )
    return BackfillCompletionProjection(
        processing_owners=job.processing_owners,
        sample=job.sample,
        group_identity=group,
        group_center_rt=center,
        source_result_artifact=result.result_ref.artifact,
        initial_assessment_artifact=initial.artifact,
        backfill_assessment=assessment,
        trace_coordinate=acquired.coordinate,
        acquisition_sha256=digest,
        artifact=artifact,
    )


@dataclass(frozen=True, slots=True)
class UntargetedCidNlRuntimePartitionResult:
    sample_results: tuple[UntargetedCidNlRuntimeSampleResult, ...]
    worker_batch: RuntimeWorkerBatchReceipt
    acquisitions: tuple[RuntimeAcquisitionReceipt, ...]
    prepared_grouping: UntargetedCidNlPreparedGrouping | None = None

    @property
    def succeeded(self) -> bool:
        return bool(self.worker_batch.settlements) and all(
            settlement.outcome is WorkUnitTerminalOutcome.SUCCEEDED
            for settlement in self.worker_batch.settlements
        )


def mint_untargeted_cid_nl_sample_result_ref(
    *,
    job: UntargetedCidNlRealDataRuntimeJob,
    result: UntargetedCidNlRealDataAdapterResult,
    backfill_input: UntargetedCidNlBackfillInput | None,
    backfill_attempt: UntargetedCidNlBackfillAttemptResult | None,
    acquisition: TraceAcquisitionBatch | None,
    backfill_attempts: tuple[UntargetedCidNlBackfillAttemptResult, ...] = (),
) -> UntargetedCidNlSampleResultRef:
    return _mint_sample_result_ref(
        job=job,
        result=result,
        backfill_input=backfill_input,
        backfill_attempt=backfill_attempt,
        acquisition=acquisition,
        backfill_attempts=backfill_attempts,
        acquisition_content=_canonical_acquisition_content,
    )


def _mint_sample_result_ref(
    *,
    job: UntargetedCidNlRealDataRuntimeJob,
    result: UntargetedCidNlRealDataAdapterResult,
    backfill_input: UntargetedCidNlBackfillInput | None,
    backfill_attempt: UntargetedCidNlBackfillAttemptResult | None,
    acquisition: TraceAcquisitionBatch | None,
    acquisition_content: Callable[[TraceAcquisitionBatch], tuple[bytes, str]],
    backfill_attempts: tuple[UntargetedCidNlBackfillAttemptResult, ...] = (),
) -> UntargetedCidNlSampleResultRef:
    if backfill_attempt is not None:
        if backfill_attempts:
            raise UntargetedCidNlRealDataRuntimeError(
                "duplicate backfill input carriers"
            )
        backfill_attempts = (backfill_attempt,)
    if len(backfill_attempts) == 1:
        backfill_attempt = backfill_attempts[0]
    backfill_assessments = tuple(attempt.assessment for attempt in backfill_attempts)
    assessment = result.assessment_input.assessment
    projections = result.assessment_input.discovery_projections
    content = acquisition_content(acquisition) if acquisition is not None else None
    acquisition_sha256 = content[1] if content is not None else None
    if (
        backfill_attempt is not None
        and backfill_input != backfill_attempt.backfill_input
    ):
        raise UntargetedCidNlRealDataRuntimeError(
            "backfill input does not match the observed backfill attempt"
        )
    for observed in backfill_attempts:
        validate_cid_nl_method_assessment_binding(
            observed.assessment,
            processing_owners=job.processing_owners,
            sample=job.sample,
            method_subject=job.method_subject,
        )
        if content is None or (
            observed.acquisition is not acquisition
            and acquisition_content(observed.acquisition)[0] != content[0]
        ):
            raise UntargetedCidNlRealDataRuntimeError(
                "backfill lost its sealed runtime result acquisition"
            )
    if len(backfill_attempts) > 1 and backfill_input is not None:
        raise UntargetedCidNlRealDataRuntimeError(
            "multiple backfills cannot use a scalar backfill input"
        )
    backfill_assessment = (
        backfill_attempt.assessment
        if backfill_attempt is not None
        else (
            backfill_input.candidate_assessment if backfill_input is not None else None
        )
    )
    backfill_candidate_count = len(
        {
            peak.content.peak_observation_content_id
            for ref in backfill_assessments
            or ((backfill_assessment,) if backfill_assessment is not None else ())
            for peak in ref.candidate_peaks
        }
    )
    values = {
        "work_unit_key": job.work_unit_key,
        "canonical_sample_identity_id": job.sample.identity.sample_identity_id,
        "method_subject_id": job.method_subject.method_subject_id,
        "discovery_receipt_id": job.discovery_receipt.discovery_receipt_id,
        "assessment_artifact_id": assessment.artifact.evidence_artifact_id,
        "discovery_projection_artifact_ids": tuple(
            projection.artifact.evidence_artifact_id for projection in projections
        ),
        "source_candidate_ids": result.source_candidate_ids,
        "strict_scan_count": result.strict_scan_count,
        "backfill_assessment_artifact_id": (
            backfill_assessment.artifact.evidence_artifact_id
            if backfill_assessment is not None
            else None
        ),
        "backfill_candidate_count": backfill_candidate_count,
        "backfill_group_hypothesis_ids": tuple(
            attempt.group_hypothesis_id for attempt in backfill_attempts
        ),
        "backfill_assessment_artifact_ids": tuple(
            ref.artifact.evidence_artifact_id for ref in backfill_assessments
        ),
        "backfill_query_bounds": tuple(
            (
                ref.content.evaluation_receipt.query_rt_min,
                ref.content.evaluation_receipt.query_rt_max,
            )
            for ref in backfill_assessments
        ),
        "acquisition_sha256": acquisition_sha256,
    }
    payload = _sample_result_bytes(**values)
    artifact = _mint_evidence_artifact_ref(
        producer_processing_request_id=(
            job.processing_owners.request.processing_request_id
        ),
        producer_processing_attempt_id=(
            job.processing_owners.attempt.processing_attempt_id
        ),
        semantic_contract=_ARTIFACT_SEMANTIC_CONTRACT,
        payload_bytes=payload,
        upstream_artifacts=tuple(
            dict.fromkeys(
                (
                    assessment.artifact,
                    *(projection.artifact for projection in projections),
                    *(ref.artifact for ref in backfill_assessments),
                    *(
                        (backfill_assessment.artifact,)
                        if backfill_assessment is not None
                        else ()
                    ),
                )
            )
        ),
    )
    return UntargetedCidNlSampleResultRef(
        work_unit_key=job.work_unit_key,
        canonical_sample_identity_id=job.sample.identity.sample_identity_id,
        method_subject_id=job.method_subject.method_subject_id,
        discovery_receipt_id=job.discovery_receipt.discovery_receipt_id,
        assessment_artifact_id=assessment.artifact.evidence_artifact_id,
        discovery_projection_artifact_ids=tuple(
            projection.artifact.evidence_artifact_id for projection in projections
        ),
        source_candidate_ids=result.source_candidate_ids,
        strict_scan_count=result.strict_scan_count,
        backfill_assessment_artifact_id=(
            backfill_assessment.artifact.evidence_artifact_id
            if backfill_assessment is not None
            else None
        ),
        backfill_candidate_count=backfill_candidate_count,
        backfill_group_hypothesis_ids=tuple(
            attempt.group_hypothesis_id for attempt in backfill_attempts
        ),
        acquisition_sha256=acquisition_sha256,
        backfill_assessment_artifact_ids=tuple(
            ref.artifact.evidence_artifact_id for ref in backfill_assessments
        ),
        backfill_query_bounds=tuple(
            (
                ref.content.evaluation_receipt.query_rt_min,
                ref.content.evaluation_receipt.query_rt_max,
            )
            for ref in backfill_assessments
        ),
        artifact=artifact,
    )


def _canonical_acquisition_bytes(acquisition: TraceAcquisitionBatch) -> bytes:
    payload = {
        "capability_id": acquisition.capability.capability_id,
        "acquisition_source_id": (
            acquisition.capability.acquisition_source.acquisition_source_id
        ),
        "logical_xic_request_count": acquisition.logical_xic_request_count,
        "xic_batch_count": acquisition.xic_batch_count,
        "adapter_chromatogram_call_count": (
            acquisition.adapter_chromatogram_call_count
        ),
        "trace_point_count": acquisition.trace_point_count,
        "traces": [
            {
                "trace_coordinate_id": item.coordinate.trace_coordinate_id,
                "rt": list(map(float, item.trace.rt)),
                "intensity": list(map(float, item.trace.intensity)),
            }
            for item in acquisition.traces
        ],
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _canonical_acquisition_content(
    acquisition: TraceAcquisitionBatch,
) -> tuple[bytes, str]:
    payload = _canonical_acquisition_bytes(acquisition)
    return payload, sha256_hexdigest(payload)


def _exact_acquisition_traces(
    acquisition: TraceAcquisitionBatch, request: XICRequest, source_id: str
) -> tuple[AcquiredTrace, ...]:
    return tuple(
        replace(acquired)
        for acquired in acquisition.traces
        if acquired.coordinate.request == request
        and acquired.coordinate.acquisition_source_id == source_id
    )


def _validate_request_bound_inputs(
    job: UntargetedCidNlRealDataRuntimeJob,
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
        raise UntargetedCidNlRealDataRuntimeError(
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
        raise UntargetedCidNlRealDataRuntimeError(
            "processing request scientific inputs are malformed"
        ) from exc
    physical_payload = request_inputs.get(PHYSICAL_CAPTURE_INPUT_KEY)
    if job.signal_processing_profile is None:
        if physical_payload is not None or job.physical_records:
            raise UntargetedCidNlRealDataRuntimeError(
                "physical replay lost its profile"
            )
    else:
        records = {record.candidate_id: record for record in job.physical_records}
        if (
            type(job.physical_records) is not tuple
            or len(records) != len(job.physical_records)
            or set(records) != {candidate.candidate_id for candidate in job.candidates}
        ):
            raise UntargetedCidNlRealDataRuntimeError(
                "physical replay candidate coverage changed"
            )
        expected_physical = physical_capture_input_payload(
            job.signal_processing_profile,
            {
                candidate_id: record.evaluation
                for candidate_id, record in records.items()
            },
        )
        if (
            not isinstance(physical_payload, dict)
            or physical_payload.get("profile") != expected_physical["profile"]
            or not isinstance(physical_payload.get("candidates"), dict)
        ):
            raise UntargetedCidNlRealDataRuntimeError(
                "physical replay profile is not request-bound"
            )
        expected_candidates = expected_physical["candidates"]
        assert isinstance(expected_candidates, dict)
        actual_candidates = physical_payload["candidates"]
        if any(
            actual_candidates.get(candidate_id) != value
            for candidate_id, value in expected_candidates.items()
        ):
            raise UntargetedCidNlRealDataRuntimeError(
                "physical replay answer is not request-bound"
            )
    try:
        pinned = cid_nl_method_subjects_from_request(request)
    except CidNlMethodSubjectContractError as exc:
        raise UntargetedCidNlRealDataRuntimeError(
            "processing request has an invalid CID-NL method subject configuration"
        ) from exc
    if isinstance(pinned, UntargetedCidNlMethodSubjectBatch):
        matching_subjects = tuple(
            subject
            for subject in pinned.subjects
            if subject.method_subject_id == job.method_subject.method_subject_id
        )
        if matching_subjects != (job.method_subject,):
            raise UntargetedCidNlRealDataRuntimeError(
                "method subject is not an exact member of the processing request"
            )
    elif pinned != job.method_subject:
        raise UntargetedCidNlRealDataRuntimeError(
            "method subject does not match the processing request"
        )
    if request_inputs.get(DISCOVERY_SETTINGS_INPUT_KEY) != (
        discovery_settings_payload(job.settings)
    ):
        raise UntargetedCidNlRealDataRuntimeError(
            "Discovery settings do not match the processing request"
        )
    expected_peak_settings = {
        field: getattr(job.peak_config, field)
        for field in TARGETED_SCIENTIFIC_SETTING_FIELDS
    }
    if request_inputs.get(PEAK_SETTINGS_INPUT_KEY) != expected_peak_settings:
        raise UntargetedCidNlRealDataRuntimeError(
            "peak settings do not match the processing request"
        )
    universe = request_inputs.get(CANDIDATE_UNIVERSE_INPUT_KEY)
    sample_entry = universe_by_sample.get(job.sample.metadata.raw_stem)
    if sample_entry is None:
        raise UntargetedCidNlRealDataRuntimeError(
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
        raise UntargetedCidNlRealDataRuntimeError(
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
        raise UntargetedCidNlRealDataRuntimeError(
            "candidate universe does not match the processing request"
        )
    if job.support_candidates:
        raise UntargetedCidNlRealDataRuntimeError(
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
        raise UntargetedCidNlRealDataRuntimeError(
            "processing request scientific inputs must be a mapping"
        )
    entries = validate_candidate_universe_payload(
        values.get(CANDIDATE_UNIVERSE_INPUT_KEY),
        expected_sample_stems=expected_sample_stems,
    )
    by_sample = MappingProxyType(
        {str(entry["sample_stem"]): entry for entry in entries}
    )
    raw_feature_ids = values.get(ALIGNMENT_FEATURE_INPUT_KEY)
    feature_ids: frozenset[str] | None = None
    if raw_feature_ids is not None:
        if (
            not isinstance(raw_feature_ids, list)
            or any(not isinstance(item, str) or not item for item in raw_feature_ids)
            or raw_feature_ids != sorted(set(raw_feature_ids))
        ):
            raise UntargetedCidNlRealDataRuntimeError(
                "alignment feature candidate IDs are malformed"
            )
        universe_ids: set[str] = set()
        for entry in entries:
            entry_ids = entry["candidate_ids"]
            assert isinstance(entry_ids, list)
            universe_ids.update(entry_ids)
        if not set(raw_feature_ids).issubset(universe_ids):
            raise UntargetedCidNlRealDataRuntimeError(
                "alignment feature candidates are outside the Discovery universe"
            )
        feature_ids = frozenset(raw_feature_ids)
    return values, by_sample, feature_ids


def _validate_subject_partition(
    job: UntargetedCidNlRealDataRuntimeJob,
    pinned: CidNlMethodSubject | UntargetedCidNlMethodSubjectBatch,
) -> CidNlMethodSubject | UntargetedCidNlMethodSubjectBatch:
    partition = job.subject_partition
    if partition is None:
        return pinned
    if not isinstance(pinned, UntargetedCidNlMethodSubjectBatch):
        raise UntargetedCidNlRealDataRuntimeError(
            "subject partition requires a batched processing request"
        )
    pinned_subjects = {
        subject.method_subject_id: subject for subject in pinned.subjects
    }
    if any(
        pinned_subjects.get(subject.method_subject_id) != subject
        for subject in partition.subjects
    ):
        raise UntargetedCidNlRealDataRuntimeError(
            "subject partition contains a foreign method subject"
        )
    if not set(partition.candidate_memberships).issubset(pinned.candidate_memberships):
        raise UntargetedCidNlRealDataRuntimeError(
            "subject partition contains a foreign candidate membership"
        )
    if job.method_subject not in partition.subjects:
        raise UntargetedCidNlRealDataRuntimeError(
            "method subject is outside the declared subject partition"
        )
    return partition


def _validate_partition_candidates(
    job: UntargetedCidNlRealDataRuntimeJob,
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
    actual_ids = tuple(candidate.candidate_id for candidate in job.candidates)
    if len(actual_ids) != len(set(actual_ids)) or set(actual_ids) != selected_ids:
        raise UntargetedCidNlRealDataRuntimeError(
            "partition candidates do not exactly cover its selected feature IDs"
        )
    _validate_sample_candidates(job.candidates, job=job, label="partition")
    support_ids = tuple(candidate.candidate_id for candidate in job.support_candidates)
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
        raise UntargetedCidNlRealDataRuntimeError(
            "support candidates must be distinct non-member rows from this "
            "source universe"
        )
    _validate_sample_candidates(job.support_candidates, job=job, label="support")
    receipt = job.discovery_receipt
    expected_receipt_values = (
        ("discovery_receipt_id", receipt.discovery_receipt_id),
        ("candidate_payload_sha256", receipt.candidate_payload_sha256),
        (
            "candidate_artifact_schema_version",
            receipt.candidate_artifact_schema_version,
        ),
        ("candidate_artifact_sha256", receipt.candidate_artifact_sha256),
        ("candidate_artifact_byte_size", receipt.candidate_artifact_byte_size),
        (
            "acquisition_source_id",
            receipt.acquisition_source.acquisition_source_id,
        ),
    )
    if receipt.sample_stem != job.sample.metadata.raw_stem or any(
        sample_entry.get(key) != value for key, value in expected_receipt_values
    ):
        raise UntargetedCidNlRealDataRuntimeError(
            "Discovery receipt metadata does not match the source universe"
        )


def _validate_sample_candidates(
    candidates: tuple[DiscoveryCandidate, ...],
    *,
    job: UntargetedCidNlRealDataRuntimeJob,
    label: str,
) -> None:
    if any(
        candidate.sample_stem != job.sample.metadata.raw_stem
        or candidate.raw_file.stem != job.sample.metadata.raw_stem
        for candidate in candidates
    ):
        raise UntargetedCidNlRealDataRuntimeError(
            f"{label} candidate references a different sample"
        )


def _index_request_subject_candidates(
    pinned: CidNlMethodSubject | UntargetedCidNlMethodSubjectBatch,
    candidates: tuple[DiscoveryCandidate, ...],
) -> Mapping[str, tuple[DiscoveryCandidate, ...]]:
    grouped: dict[str, list[DiscoveryCandidate]] = {}
    if isinstance(pinned, UntargetedCidNlMethodSubjectBatch):
        subject_by_candidate = dict(pinned.candidate_memberships)
        for candidate in candidates:
            subject_id = subject_by_candidate.get(candidate.candidate_id)
            if subject_id is not None:
                grouped.setdefault(subject_id, []).append(candidate)
    return MappingProxyType(
        {subject_id: tuple(values) for subject_id, values in grouped.items()}
    )


def _request_subject_candidates(
    job: UntargetedCidNlRealDataRuntimeJob,
) -> tuple[DiscoveryCandidate, ...]:
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
            subject_id = subject_by_candidate.get(record.candidate_id)
            if subject_id is not None:
                grouped.setdefault(subject_id, []).append(record)
    return MappingProxyType(
        {subject_id: tuple(values) for subject_id, values in grouped.items()}
    )


def _request_subject_physical_records(
    job: UntargetedCidNlRealDataRuntimeJob,
) -> tuple[DiscoveryPhysicalFactRecord, ...]:
    if not isinstance(job._request_subjects, UntargetedCidNlMethodSubjectBatch):
        return job.physical_records
    return job._request_physical_records_by_subject.get(
        job.method_subject.method_subject_id, ()
    )


def _request_uses_subject_batch(job: UntargetedCidNlRealDataRuntimeJob) -> bool:
    return isinstance(job._request_subjects, UntargetedCidNlMethodSubjectBatch)


# Capability-named Aliases
UntargetedRealDataRuntimeJob = UntargetedCidNlRealDataRuntimeJob
UntargetedSampleResultRef = UntargetedCidNlSampleResultRef
UntargetedRuntimeSampleResult = UntargetedCidNlRuntimeSampleResult
UntargetedRuntimePartitionResult = UntargetedCidNlRuntimePartitionResult
UntargetedRealDataRuntimeError = UntargetedCidNlRealDataRuntimeError

build_untargeted_sample_runtime_jobs = build_untargeted_cid_nl_sample_runtime_jobs
project_untargeted_completed_backfill = project_untargeted_cid_nl_completed_backfill
mint_untargeted_sample_result_ref = mint_untargeted_cid_nl_sample_result_ref
canonical_untargeted_sample_result_bytes = (
    canonical_untargeted_cid_nl_sample_result_bytes
)


__all__ = [
    "RUNTIME_CALL_COST_METRIC_NAMES",
    "SCHEMA_VERSION",
    "UntargetedCidNlRealDataRuntimeError",
    "UntargetedCidNlRealDataRuntimeJob",
    "UntargetedCidNlRuntimePartitionResult",
    "UntargetedCidNlRuntimeSampleResult",
    "UntargetedCidNlSampleResultRef",
    "UntargetedRealDataRuntimeError",
    "UntargetedRealDataRuntimeJob",
    "UntargetedRuntimePartitionResult",
    "UntargetedRuntimeSampleResult",
    "UntargetedSampleResultRef",
    "VALIDATION_STATUS",
    "build_untargeted_cid_nl_sample_runtime_jobs",
    "build_untargeted_sample_runtime_jobs",
    "canonical_untargeted_cid_nl_sample_result_bytes",
    "canonical_untargeted_sample_result_bytes",
    "mint_untargeted_cid_nl_sample_result_ref",
    "mint_untargeted_sample_result_ref",
    "project_untargeted_cid_nl_completed_backfill",
    "project_untargeted_completed_backfill",
]
