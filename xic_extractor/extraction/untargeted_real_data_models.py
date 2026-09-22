"""Data models, contracts, and validation helpers for untargeted real-data runtime."""

from __future__ import annotations

import copyreg
import json
from collections.abc import Callable, Mapping
from copy import copy
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType

from xic_extractor.acquisition_contract import AcquiredTrace, TraceAcquisitionBatch
from xic_extractor.alignment.backfill.evaluation_coverage import (
    _COMPLETED_RECOVERY_CONTRACT,
    OwnerBackfillCompletedRecoveryProjection,
    _completed_recovery_projection_bytes,
)
from xic_extractor.alignment.cell_observations import CidNlCellGroupContext
from xic_extractor.alignment.cross_sample_peak_groups import (
    validate_cross_sample_peak_group_identity,
)
from xic_extractor.alignment.untargeted_cid_nl_golden_path import (
    UntargetedCidNlRecoveryInput,
)
from xic_extractor.config import ExtractionConfig
from xic_extractor.discovery.batch_contract import (
    DiscoveryCandidateUniverseReceipt,
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
    UntargetedCidNlRealDataAdapterResult,
    UntargetedCidNlRecoveryAttemptResult,
    _bounded_subject_candidates,
)
from xic_extractor.extraction.untargeted_cid_nl_real_data_inputs import (
    CANDIDATE_UNIVERSE_INPUT_KEY,
    DISCOVERY_SETTINGS_INPUT_KEY,
    PEAK_SETTINGS_INPUT_KEY,
    UNTARGETED_CID_NL_PROCESSING_ALGORITHM_VERSION,
    UNTARGETED_CID_NL_PROCESSING_CONTRACT_VERSION,
    UNTARGETED_CID_NL_PROCESSING_INPUT_SCHEMA_VERSION,
    candidate_universe_payload,
    discovery_settings_payload,
    validate_candidate_universe_payload,
)
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
    physical_capture_input_payload,
)
from xic_extractor.peak_detection.signal_processing_profile import (
    SignalProcessingProfile,
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
    signal_processing_profile: SignalProcessingProfile | None = None
    physical_records: tuple[DiscoveryPhysicalFactRecord, ...] = ()
    acquisition_source_expectation: AcquisitionSourceExpectation | None = field(
        default=None, compare=False
    )
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
) -> tuple[UntargetedCidNlRealDataRuntimeJob, ...]:
    """Validate a sample once, then bind each subject to those same inputs."""

    if not subject_work_units:
        return ()
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
        signal_processing_profile=signal_processing_profile,
        physical_records=physical_records,
        acquisition_source_expectation=acquisition_source_expectation,
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
        recovery_assessment_artifact_id=(reference.recovery_assessment_artifact_id),
        recovery_candidate_count=reference.recovery_candidate_count,
        acquisition_sha256=reference.acquisition_sha256,
        recovery_assessment_artifact_ids=reference.recovery_assessment_artifact_ids,
        recovery_query_bounds=reference.recovery_query_bounds,
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
    recovery_assessment_artifact_id: str | None
    recovery_candidate_count: int
    acquisition_sha256: str | None
    recovery_assessment_artifact_ids: tuple[str, ...]
    recovery_query_bounds: tuple[tuple[float, float], ...]
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
class UntargetedCidNlRuntimeSampleResult:
    job: UntargetedCidNlRealDataRuntimeJob
    adapter_result: UntargetedCidNlRealDataAdapterResult
    result_ref: UntargetedCidNlSampleResultRef
    recovery_input: UntargetedCidNlRecoveryInput | None = None
    recovery_attempts: tuple[UntargetedCidNlRecoveryAttemptResult, ...] = ()

    @property
    def recovery_attempt(self) -> UntargetedCidNlRecoveryAttemptResult | None:
        if len(self.recovery_attempts) > 1:
            raise UntargetedCidNlRealDataRuntimeError(
                "multiple recovery attempts require group selection"
            )
        return self.recovery_attempts[0] if self.recovery_attempts else None

    @property
    def effective_assessments(self) -> tuple[CidNlMethodAssessmentRef, ...]:
        return (self.adapter_result.assessment_input.assessment,) + tuple(
            attempt.assessment for attempt in self.recovery_attempts
        )

    @property
    def effective_assessment(self) -> CidNlMethodAssessmentRef:
        recovery = self.recovery_attempt
        return (
            recovery.assessment
            if recovery is not None
            else self.adapter_result.assessment_input.assessment
        )

    def recovery_for_group(
        self, center: float
    ) -> UntargetedCidNlRecoveryAttemptResult | None:
        half_window = self.job.peak_config.nl_rt_anchor_half_window_min
        matches = tuple(
            attempt
            for attempt in self.recovery_attempts
            if attempt.assessment.content.evaluation_receipt.query_rt_min
            == max(0.0, center - half_window)
            and attempt.assessment.content.evaluation_receipt.query_rt_max
            == center + half_window
        )
        if len(matches) > 1:
            raise UntargetedCidNlRealDataRuntimeError(
                "group has duplicate recovery attempts"
            )
        return matches[0] if matches else None


def project_untargeted_cid_nl_completed_recovery(
    result: UntargetedCidNlRuntimeSampleResult,
    *,
    group_context: CidNlCellGroupContext,
) -> OwnerBackfillCompletedRecoveryProjection:
    """Project an existing completed empty recovery without executing it again."""
    if type(result) is not UntargetedCidNlRuntimeSampleResult:
        raise UntargetedCidNlRealDataRuntimeError("recovery requires a runtime result")
    replace(result.job)
    return _project_completed_recovery(
        result,
        group_context=group_context,
        acquisition_content=_canonical_acquisition_content,
        exact_traces=_exact_acquisition_traces,
    )


def _project_completed_recovery(
    result: UntargetedCidNlRuntimeSampleResult,
    *,
    group_context: CidNlCellGroupContext,
    acquisition_content: Callable[[TraceAcquisitionBatch], tuple[bytes, str]],
    exact_traces: Callable[
        [TraceAcquisitionBatch, XICRequest, str], tuple[AcquiredTrace, ...]
    ],
) -> OwnerBackfillCompletedRecoveryProjection:
    job = result.job
    replace(group_context)
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
        raise UntargetedCidNlRealDataRuntimeError("recovery crossed its owner group")
    recovery = result.recovery_for_group(center)
    if recovery is None:
        raise UntargetedCidNlRealDataRuntimeError(
            "initial assessment is not a recovery"
        )
    assessment = recovery.assessment
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
        != "untargeted-cid-nl-backfill-recovery"
        or not receipt.search_completed
        or not receipt.acquisition_supported
        or recovery.recovery_input is not None
    ):
        raise UntargetedCidNlRealDataRuntimeError(
            "recovery is not a completed empty epoch"
        )
    replace(result.result_ref)
    expected_result = _mint_sample_result_ref(
        job=job,
        result=result.adapter_result,
        recovery_input=result.recovery_input,
        recovery_attempt=None,
        recovery_attempts=result.recovery_attempts,
        acquisition=recovery.acquisition,
        acquisition_content=acquisition_content,
    )
    if result.result_ref != expected_result:
        raise UntargetedCidNlRealDataRuntimeError(
            "completed recovery lost its sealed runtime result"
        )
    expected_request = XICRequest(
        mz=job.method_subject.precursor_mz,
        rt_min=max(0.0, center - job.peak_config.nl_rt_anchor_half_window_min),
        rt_max=center + job.peak_config.nl_rt_anchor_half_window_min,
        ppm_tol=job.method_subject.precursor_tolerance_ppm,
    )
    exact = exact_traces(
        recovery.acquisition, expected_request, assessment.content.acquisition_source_id
    )
    if len(exact) != 1 or (
        receipt.query_rt_min != expected_request.rt_min
        or receipt.query_rt_max != expected_request.rt_max
    ):
        raise UntargetedCidNlRealDataRuntimeError(
            "recovery crossed its exact request coordinates"
        )
    (acquired,) = exact
    replace(acquired)
    digest = result.result_ref.acquisition_sha256
    assert digest is not None
    artifact = _mint_evidence_artifact_ref(
        producer_processing_request_id=job.processing_owners.request.processing_request_id,
        producer_processing_attempt_id=job.processing_owners.attempt.processing_attempt_id,
        semantic_contract=_COMPLETED_RECOVERY_CONTRACT,
        payload_bytes=_completed_recovery_projection_bytes(
            processing_owners=job.processing_owners,
            sample=job.sample,
            group_identity=group,
            group_center_rt=center,
            source_result_artifact=result.result_ref.artifact,
            initial_assessment_artifact=initial.artifact,
            recovery_assessment=assessment,
            trace_coordinate=acquired.coordinate,
            acquisition_sha256=digest,
        ),
        upstream_artifacts=(result.result_ref.artifact, assessment.artifact),
    )
    return OwnerBackfillCompletedRecoveryProjection(
        processing_owners=job.processing_owners,
        sample=job.sample,
        group_identity=group,
        group_center_rt=center,
        source_result_artifact=result.result_ref.artifact,
        initial_assessment_artifact=initial.artifact,
        recovery_assessment=assessment,
        trace_coordinate=acquired.coordinate,
        acquisition_sha256=digest,
        artifact=artifact,
    )


@dataclass(frozen=True, slots=True)
class UntargetedCidNlRuntimePartitionResult:
    sample_results: tuple[UntargetedCidNlRuntimeSampleResult, ...]
    worker_batch: RuntimeWorkerBatchReceipt
    acquisitions: tuple[RuntimeAcquisitionReceipt, ...]

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
    recovery_input: UntargetedCidNlRecoveryInput | None,
    recovery_attempt: UntargetedCidNlRecoveryAttemptResult | None,
    acquisition: TraceAcquisitionBatch | None,
    recovery_attempts: tuple[UntargetedCidNlRecoveryAttemptResult, ...] = (),
) -> UntargetedCidNlSampleResultRef:
    return _mint_sample_result_ref(
        job=job,
        result=result,
        recovery_input=recovery_input,
        recovery_attempt=recovery_attempt,
        acquisition=acquisition,
        recovery_attempts=recovery_attempts,
        acquisition_content=_canonical_acquisition_content,
    )


def _mint_sample_result_ref(
    *,
    job: UntargetedCidNlRealDataRuntimeJob,
    result: UntargetedCidNlRealDataAdapterResult,
    recovery_input: UntargetedCidNlRecoveryInput | None,
    recovery_attempt: UntargetedCidNlRecoveryAttemptResult | None,
    acquisition: TraceAcquisitionBatch | None,
    acquisition_content: Callable[[TraceAcquisitionBatch], tuple[bytes, str]],
    recovery_attempts: tuple[UntargetedCidNlRecoveryAttemptResult, ...] = (),
) -> UntargetedCidNlSampleResultRef:
    if recovery_attempt is not None:
        if recovery_attempts:
            raise UntargetedCidNlRealDataRuntimeError(
                "duplicate recovery input carriers"
            )
        recovery_attempts = (recovery_attempt,)
    if len(recovery_attempts) == 1:
        recovery_attempt = recovery_attempts[0]
    recovery_assessments = tuple(attempt.assessment for attempt in recovery_attempts)
    assessment = result.assessment_input.assessment
    projections = result.assessment_input.discovery_projections
    content = acquisition_content(acquisition) if acquisition is not None else None
    acquisition_sha256 = content[1] if content is not None else None
    if (
        recovery_attempt is not None
        and recovery_input != recovery_attempt.recovery_input
    ):
        raise UntargetedCidNlRealDataRuntimeError(
            "recovery input does not match the observed recovery attempt"
        )
    for observed in recovery_attempts:
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
                "recovery lost its sealed runtime result acquisition"
            )
    if len(recovery_attempts) > 1 and recovery_input is not None:
        raise UntargetedCidNlRealDataRuntimeError(
            "multiple recoveries cannot use a scalar recovery input"
        )
    recovery_assessment = (
        recovery_attempt.assessment
        if recovery_attempt is not None
        else (
            recovery_input.candidate_assessment if recovery_input is not None else None
        )
    )
    recovery_candidate_count = len(
        {
            peak.content.peak_observation_content_id
            for ref in recovery_assessments
            or ((recovery_assessment,) if recovery_assessment is not None else ())
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
        "recovery_assessment_artifact_id": (
            recovery_assessment.artifact.evidence_artifact_id
            if recovery_assessment is not None
            else None
        ),
        "recovery_candidate_count": recovery_candidate_count,
        "recovery_assessment_artifact_ids": tuple(
            ref.artifact.evidence_artifact_id for ref in recovery_assessments
        ),
        "recovery_query_bounds": tuple(
            (
                ref.content.evaluation_receipt.query_rt_min,
                ref.content.evaluation_receipt.query_rt_max,
            )
            for ref in recovery_assessments
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
                    *(ref.artifact for ref in recovery_assessments),
                    *(
                        (recovery_assessment.artifact,)
                        if recovery_assessment is not None
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
        recovery_assessment_artifact_id=(
            recovery_assessment.artifact.evidence_artifact_id
            if recovery_assessment is not None
            else None
        ),
        recovery_candidate_count=recovery_candidate_count,
        acquisition_sha256=acquisition_sha256,
        recovery_assessment_artifact_ids=tuple(
            ref.artifact.evidence_artifact_id for ref in recovery_assessments
        ),
        recovery_query_bounds=tuple(
            (
                ref.content.evaluation_receipt.query_rt_min,
                ref.content.evaluation_receipt.query_rt_max,
            )
            for ref in recovery_assessments
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
        acquired
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
    request_inputs = json.loads(request.normalized_scientific_inputs_json)
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
    expected_stems = tuple(
        binding.adapter_input_key for binding in request.acquisition_bindings
    )
    validate_candidate_universe_payload(
        universe,
        expected_sample_stems=expected_stems,
    )
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
    return pinned


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
project_untargeted_completed_recovery = project_untargeted_cid_nl_completed_recovery
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
    "project_untargeted_cid_nl_completed_recovery",
    "project_untargeted_completed_recovery",
]
