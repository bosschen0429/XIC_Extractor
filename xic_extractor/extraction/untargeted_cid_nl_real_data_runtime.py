"""Atomic runtime owner for replayable Untargeted CID-NL real-data work."""

from __future__ import annotations

import hashlib
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
from xic_extractor.alignment.cell_observations import (
    CidNlCellGroupContext,
    compose_cid_nl_predecessor_cells,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.conservative_backfill import (
    ACTIVATION_ELIGIBLE_SAMPLE_ROLES,
    assess_conservative_backfill_group,
    minimum_direct_support_count,
)
from xic_extractor.alignment.cross_sample_peak_groups import (
    construct_cross_sample_peak_group_hypotheses,
    validate_cross_sample_peak_group_identity,
)
from xic_extractor.alignment.hypothesis_owner_adapter import (
    compose_cid_nl_peak_hypotheses_from_assessment,
    owner_from_cid_nl_assessment_support,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.alignment.untargeted_cid_nl_golden_path import (
    CID_NL_ALIGNMENT_CONFIG,
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
from xic_extractor.execution_runtime import (
    AttemptRuntime,
    AttemptTerminalOutcome,
    WorkUnitFailureCategory,
    WorkUnitFailureDetail,
    WorkUnitSettlement,
    WorkUnitTerminalOutcome,
)
from xic_extractor.execution_runtime_finalization import (
    RuntimeResourceCleanupOutcome,
    RuntimeResourceKind,
    RuntimeResourceOpenOutcome,
    RuntimeResourceReservation,
)
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.execution_runtime_observability import (
    RuntimeMetricAvailability,
    RuntimeMetricScope,
    RuntimeObservabilityContractError,
    validate_runtime_acquisition_metrics,
)
from xic_extractor.execution_runtime_owner import (
    RuntimeAcquisitionReceipt,
    RuntimeArtifactRegistrationReceipt,
    RuntimeFinalizationReceipt,
    RuntimeResourceSettlementReceipt,
    RuntimeTelemetryProvider,
    RuntimeWorkerBatchReceipt,
    execute_runtime_attempt,
    mint_work_unit_settlement,
)
from xic_extractor.extraction.targeted_inputs import (
    TARGETED_SCIENTIFIC_SETTING_FIELDS,
)
from xic_extractor.extraction.untargeted_cid_nl_real_data_adapter import (
    UntargetedCidNlRealDataAdapterResult,
    UntargetedCidNlRecoveryAttemptResult,
    UntargetedCidNlRecoveryTraceBatch,
    UntargetedCidNlSampleTraceBatch,
    _bounded_subject_candidates,
    _supports_direct_precursor_assessment,
    _UntargetedRealDataWorkObserver,
    acquire_untargeted_cid_nl_recovery_trace_batch,
    acquire_untargeted_cid_nl_sample_trace_batch,
    adapt_current_discovery_sample_subject,
    recover_untargeted_cid_nl_sample_subject,
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
    IdentityContractError,
    ProcessingOwnerReferences,
    canonical_current_attempt_artifact_closure,
    resolve_request_acquisition_source,
)
from xic_extractor.raw_reader import RawReaderOpenError, open_raw
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity
from xic_extractor.xic_models import XICRequest

SCHEMA_VERSION = "untargeted_cid_nl_real_data_runtime_result_v3"
VALIDATION_STATUS = "diagnostic_only"
RUNTIME_CALL_COST_METRIC_NAMES = (
    "raw_open_count",
    "logical_xic_request_count",
    "xic_batch_count",
    "adapter_chromatogram_call_count",
    "trace_point_count",
)
_RUNTIME_COMPATIBILITY_TOKEN = "untargeted-cid-nl-real-data-runtime:v1"
_ARTIFACT_SEMANTIC_CONTRACT = _mint_artifact_semantic_contract_ref(
    owner_namespace="xic_extractor.untargeted_cid_nl_real_data_runtime",
    semantic_role="untargeted_cid_nl_real_data_sample_result",
    schema_version=SCHEMA_VERSION,
    validation_status=VALIDATION_STATUS,
    media_type="application/json",
    serialization_format="canonical-json-utf8-lf-v1",
)


class UntargetedCidNlRealDataRuntimeError(ValueError):
    """Raised when real-data runtime work cannot close atomically."""


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
    _request_subjects: CidNlMethodSubject | UntargetedCidNlMethodSubjectBatch = field(
        init=False, repr=False, compare=False
    )
    _request_candidates_by_subject: Mapping[str, tuple[DiscoveryCandidate, ...]] = (
        field(init=False, repr=False, compare=False)
    )
    _request_physical_records_by_subject: Mapping[
        str, tuple[DiscoveryPhysicalFactRecord, ...]
    ] = field(init=False, repr=False, compare=False)

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
        != "untargeted-cid-nl-backfill-recovery:v1"
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


class _RuntimeResultContext:
    """Reuse completed adapter snapshots within one synchronous owner phase."""

    def __init__(self, jobs: tuple[UntargetedCidNlRealDataRuntimeJob, ...]) -> None:
        self._jobs = {id(job): job for job in jobs}
        self._content: dict[int, tuple[TraceAcquisitionBatch, bytes, str]] = {}
        self._traces: dict[
            int,
            tuple[
                TraceAcquisitionBatch,
                dict[tuple[XICRequest, str], tuple[AcquiredTrace, ...]],
            ],
        ] = {}

    def acquisition_content(
        self, acquisition: TraceAcquisitionBatch
    ) -> tuple[bytes, str]:
        cached = self._content.get(id(acquisition))
        if cached is None:
            payload, digest = _canonical_acquisition_content(acquisition)
            cached = (acquisition, payload, digest)
            self._content[id(acquisition)] = cached
        return cached[1], cached[2]

    def exact_traces(
        self, acquisition: TraceAcquisitionBatch, request: XICRequest, source_id: str
    ) -> tuple[AcquiredTrace, ...]:
        cached = self._traces.get(id(acquisition))
        if cached is None:
            grouped: dict[tuple[XICRequest, str], list[AcquiredTrace]] = {}
            for acquired in acquisition.traces:
                key = (
                    acquired.coordinate.request,
                    acquired.coordinate.acquisition_source_id,
                )
                grouped.setdefault(key, []).append(acquired)
            cached = (
                acquisition,
                {key: tuple(values) for key, values in grouped.items()},
            )
            self._traces[id(acquisition)] = cached
        return cached[1].get((request, source_id), ())

    def project_completed_recovery(
        self,
        result: UntargetedCidNlRuntimeSampleResult,
        *,
        group_context: CidNlCellGroupContext,
    ) -> OwnerBackfillCompletedRecoveryProjection:
        if type(result) is not UntargetedCidNlRuntimeSampleResult:
            raise UntargetedCidNlRealDataRuntimeError(
                "recovery requires a runtime result"
            )
        if self._jobs.get(id(result.job)) is not result.job:
            raise UntargetedCidNlRealDataRuntimeError(
                "recovery result does not reuse an exact runtime batch job"
            )
        return _project_completed_recovery(
            result,
            group_context=group_context,
            acquisition_content=self.acquisition_content,
            exact_traces=self.exact_traces,
        )


@dataclass(slots=True)
class _RuntimeJobState:
    job: UntargetedCidNlRealDataRuntimeJob
    resource_key: str | None
    initial_requires_raw: bool
    observer: _UntargetedRealDataWorkObserver
    error: Exception | None = None
    adapter_result: UntargetedCidNlRealDataAdapterResult | None = None
    recovery_input: UntargetedCidNlRecoveryInput | None = None
    recovery_attempts: tuple[UntargetedCidNlRecoveryAttemptResult, ...] = ()
    recovery_group_center_rts: tuple[float, ...] = ()
    open_attempted: bool = False
    opened: bool = False
    cleanup_outcome: RuntimeResourceCleanupOutcome = (
        RuntimeResourceCleanupOutcome.SUCCEEDED
    )


@dataclass(slots=True)
class _BatchSamplePhaseState:
    states: tuple[_RuntimeJobState, ...]
    initial_resource_key: str | None
    recovery_resource_key: str | None
    initial_open_attempted: bool = False
    initial_opened: bool = False
    initial_cleanup_outcome: RuntimeResourceCleanupOutcome = (
        RuntimeResourceCleanupOutcome.SUCCEEDED
    )
    recovery_open_attempted: bool = False
    recovery_opened: bool = False
    recovery_cleanup_outcome: RuntimeResourceCleanupOutcome = (
        RuntimeResourceCleanupOutcome.SUCCEEDED
    )
    initial_acquisition: TraceAcquisitionBatch | None = None
    recovery_acquisition: TraceAcquisitionBatch | None = None


@dataclass(frozen=True, slots=True)
class UntargetedCidNlRealDataRuntimeApplicationResult:
    finalization: RuntimeFinalizationReceipt
    sample_results: tuple[UntargetedCidNlRuntimeSampleResult, ...]

    @property
    def succeeded(self) -> bool:
        return (
            self.finalization.supervisor.terminal_outcome
            is AttemptTerminalOutcome.SUCCEEDED
        )


def untargeted_cid_nl_runtime_call_cost(
    finalization: RuntimeFinalizationReceipt,
) -> dict[str, int]:
    """Project the five real-data runtime call-cost facts from its ledger."""

    if not isinstance(finalization, RuntimeFinalizationReceipt):
        raise UntargetedCidNlRealDataRuntimeError(
            "call cost requires an accepted RuntimeFinalizationReceipt"
        )
    attempt_id = (
        finalization.coordinator.state.runtime.manifest.attempt.processing_attempt_id
    )
    projected: dict[str, int] = {}
    for name in RUNTIME_CALL_COST_METRIC_NAMES:
        matches = tuple(
            metric
            for metric in finalization.observability_ledger.metrics
            if metric.name == name
            and metric.scope is RuntimeMetricScope.ATTEMPT
            and metric.scope_key == attempt_id
        )
        if (
            len(matches) != 1
            or matches[0].availability is not RuntimeMetricAvailability.MEASURED
            or isinstance(matches[0].value, bool)
            or not isinstance(matches[0].value, (int, float))
            or not float(matches[0].value).is_integer()
            or matches[0].value < 0
        ):
            raise UntargetedCidNlRealDataRuntimeError(
                f"runtime owner did not provide measured integral {name}"
            )
        projected[name] = int(matches[0].value)
    return projected


def execute_untargeted_cid_nl_real_data_runtime(
    runtime: AttemptRuntime,
    jobs: tuple[UntargetedCidNlRealDataRuntimeJob, ...],
    *,
    telemetry_provider: RuntimeTelemetryProvider,
    recovery_marker: OwnerPathRuntimeMarker,
    progress_callback: Callable[[Mapping[str, object]], None] | None = None,
    alignment_config: AlignmentConfig = CID_NL_ALIGNMENT_CONFIG,
) -> UntargetedCidNlRealDataRuntimeApplicationResult:
    """Execute every request sample through one attempt and one finalization."""

    if not isinstance(runtime, AttemptRuntime):
        raise UntargetedCidNlRealDataRuntimeError("runtime must be an AttemptRuntime")
    if not isinstance(alignment_config, AlignmentConfig):
        raise UntargetedCidNlRealDataRuntimeError(
            "alignment_config must be an AlignmentConfig"
        )
    if not jobs or any(
        not isinstance(job, UntargetedCidNlRealDataRuntimeJob) for job in jobs
    ):
        raise UntargetedCidNlRealDataRuntimeError(
            "jobs must contain Untargeted CID-NL runtime jobs"
        )
    if not isinstance(recovery_marker, OwnerPathRuntimeMarker):
        raise UntargetedCidNlRealDataRuntimeError(
            "recovery_marker must be OwnerPathRuntimeMarker"
        )
    attempt = jobs[0].processing_owners.attempt
    if any(job.processing_owners.attempt != attempt for job in jobs):
        raise UntargetedCidNlRealDataRuntimeError(
            "all runtime jobs must share one processing attempt"
        )
    if runtime.manifest.attempt != attempt:
        raise UntargetedCidNlRealDataRuntimeError(
            "jobs must match the runtime manifest attempt"
        )
    keys = tuple(job.work_unit_key for job in jobs)
    if len(keys) != len(set(keys)) or (
        not runtime.manifest_preflight_failed
        and runtime.manifest.work_unit_keys != keys
    ):
        raise UntargetedCidNlRealDataRuntimeError(
            "runtime manifest must exactly match the declared real-data work units"
        )

    work = _prepare_runtime_work(
        jobs, alignment_config=alignment_config, progress_callback=progress_callback
    )
    recovery_marker.record_launch(runtime)
    finalization = execute_runtime_attempt(
        runtime,
        work.reservations,
        worker_batch_provider=work.provide_worker_batch,
        telemetry_provider=telemetry_provider,
        persister=recovery_marker,
    )
    _validate_finalized_acquisition_metrics(
        finalization,
        tuple(work.acquisitions),
    )
    succeeded = (
        finalization.supervisor.terminal_outcome is AttemptTerminalOutcome.SUCCEEDED
    )
    return UntargetedCidNlRealDataRuntimeApplicationResult(
        finalization=finalization,
        sample_results=(tuple(work.results) if succeeded else ()),
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


def execute_untargeted_cid_nl_runtime_partition(
    jobs: tuple[UntargetedCidNlRealDataRuntimeJob, ...],
    *,
    partition_key: str,
    alignment_config: AlignmentConfig,
    progress_callback: Callable[[Mapping[str, object]], None] | None = None,
) -> UntargetedCidNlRuntimePartitionResult:
    """Execute one complete subject partition under the caller's runtime attempt."""
    _required_text(partition_key, field="partition_key")
    if not isinstance(alignment_config, AlignmentConfig):
        raise UntargetedCidNlRealDataRuntimeError(
            "alignment_config must be an AlignmentConfig"
        )
    if (
        not isinstance(jobs, tuple)
        or not jobs
        or any(not isinstance(job, UntargetedCidNlRealDataRuntimeJob) for job in jobs)
    ):
        raise UntargetedCidNlRealDataRuntimeError(
            "jobs must contain Untargeted CID-NL runtime jobs"
        )
    if any(
        job.processing_owners.attempt != jobs[0].processing_owners.attempt
        for job in jobs
    ):
        raise UntargetedCidNlRealDataRuntimeError(
            "all runtime jobs must share one processing attempt"
        )
    if len({job.work_unit_key for job in jobs}) != len(jobs):
        raise UntargetedCidNlRealDataRuntimeError(
            "partition work unit keys must be unique"
        )
    work = _prepare_runtime_work(
        jobs,
        alignment_config=alignment_config,
        progress_callback=progress_callback,
        partition_key=partition_key,
    )
    batch = work.provide_worker_batch(None)
    return UntargetedCidNlRuntimePartitionResult(
        tuple(work.results), batch, batch.acquisitions
    )


@dataclass(slots=True)
class _RuntimeWork:
    reservations: list[RuntimeResourceReservation]
    provide_worker_batch: Callable[[object], RuntimeWorkerBatchReceipt]
    results: list[UntargetedCidNlRuntimeSampleResult]
    acquisitions: list[TraceAcquisitionBatch]


def _prepare_runtime_work(
    jobs: tuple[UntargetedCidNlRealDataRuntimeJob, ...],
    *,
    alignment_config: AlignmentConfig,
    progress_callback: Callable[[Mapping[str, object]], None] | None,
    partition_key: str | None = None,
) -> _RuntimeWork:
    attempt = jobs[0].processing_owners.attempt

    def report_phase(stage: str, completed: int, total: int) -> None:
        if partition_key is not None and progress_callback is not None:
            progress_callback(
                {
                    "event": "partition_progress",
                    "stage": stage,
                    "partition_key": partition_key,
                    "completed": completed,
                    "total": total,
                }
            )

    direct_assessment_by_key: dict[str, bool] = {}
    initial_raw_by_key: dict[str, bool] = {}
    planning_errors: dict[str, Exception] = {}
    for job in jobs:
        try:
            bounded = _request_subject_candidates(job)
            direct_assessment_by_key[job.work_unit_key] = (
                _supports_direct_precursor_assessment(bounded)
            )
            initial_raw_by_key[job.work_unit_key] = bool(bounded)
        except Exception as exc:
            direct_assessment_by_key[job.work_unit_key] = False
            initial_raw_by_key[job.work_unit_key] = False
            planning_errors[job.work_unit_key] = exc
    eligible_by_subject: dict[str, list[UntargetedCidNlRealDataRuntimeJob]] = {}
    for job in jobs:
        if (
            not job.sample.metadata.excluded
            and job.sample.metadata.sample_role in ACTIVATION_ELIGIBLE_SAMPLE_ROLES
        ):
            eligible_by_subject.setdefault(
                job.method_subject.method_subject_id,
                [],
            ).append(job)
    recovery_subject_ids = {
        method_subject_id
        for method_subject_id, subject_jobs in eligible_by_subject.items()
        if sum(direct_assessment_by_key[job.work_unit_key] for job in subject_jobs)
        >= minimum_direct_support_count(len(subject_jobs))
    }

    states: list[_RuntimeJobState] = []
    reservations: list[RuntimeResourceReservation] = []
    for job in jobs:
        resource_key: str | None = None
        error: Exception | None = planning_errors.get(job.work_unit_key)
        initial_requires_raw = initial_raw_by_key[job.work_unit_key]
        try:
            recovery_may_need_raw = (
                error is None
                and job.method_subject.method_subject_id in recovery_subject_ids
                and not initial_requires_raw
                and not job.sample.metadata.excluded
                and job.sample.metadata.sample_role in ACTIVATION_ELIGIBLE_SAMPLE_ROLES
            )
            if (
                partition_key is None
                and not _request_uses_subject_batch(job)
                and (initial_requires_raw or recovery_may_need_raw)
            ):
                resource_key = f"acquisition-session:{job.work_unit_key}"
                source = resolve_request_acquisition_source(
                    processing_owners=job.processing_owners,
                    sample=job.sample,
                )
                reservations.append(
                    RuntimeResourceReservation(
                        attempt=attempt,
                        kind=RuntimeResourceKind.ACQUISITION_SESSION,
                        resource_key=resource_key,
                        worker_key=f"inline-acquisition:{job.work_unit_key}",
                        source_reference=source.acquisition_source_id,
                        compatibility_token=_RUNTIME_COMPATIBILITY_TOKEN,
                        owner_conditional=not initial_requires_raw,
                    )
                )
        except Exception as exc:
            error = exc
        states.append(
            _RuntimeJobState(
                job=job,
                resource_key=resource_key,
                initial_requires_raw=initial_requires_raw,
                observer=_UntargetedRealDataWorkObserver(job.processing_owners),
                error=error,
            )
        )

    sample_groups = _runtime_sample_groups(states)
    batch_phases: list[_BatchSamplePhaseState] = []
    for sample_states in sample_groups:
        if partition_key is None and not _request_uses_subject_batch(
            sample_states[0].job
        ):
            continue
        first = sample_states[0].job
        sample_id = first.sample.identity.sample_identity_id
        source = resolve_request_acquisition_source(
            processing_owners=first.processing_owners,
            sample=first.sample,
        )
        initial_resource_key = (
            f"acquisition-session:initial:{sample_id}"
            if any(state.initial_requires_raw for state in sample_states)
            else None
        )
        recovery_resource_key = (
            f"acquisition-session:recovery:{sample_id}"
            if not first.sample.metadata.excluded
            and first.sample.metadata.sample_role in ACTIVATION_ELIGIBLE_SAMPLE_ROLES
            and recovery_subject_ids
            else None
        )
        if partition_key is not None:
            initial_resource_key = (
                f"acquisition-session:{partition_key}:{sample_id}:initial"
            )
            recovery_resource_key = (
                f"acquisition-session:{partition_key}:{sample_id}:recovery"
            )
        for resource_key, phase, owner_conditional in (
            (initial_resource_key, "initial", False),
            (recovery_resource_key, "recovery", True),
        ):
            if resource_key is None:
                continue
            reservations.append(
                RuntimeResourceReservation(
                    attempt=attempt,
                    kind=RuntimeResourceKind.ACQUISITION_SESSION,
                    resource_key=resource_key,
                    worker_key=f"inline-acquisition:{phase}:{first.work_unit_key}",
                    source_reference=source.acquisition_source_id,
                    compatibility_token=_RUNTIME_COMPATIBILITY_TOKEN,
                    owner_conditional=owner_conditional or partition_key is not None,
                )
            )
        batch_phases.append(
            _BatchSamplePhaseState(
                states=sample_states,
                initial_resource_key=initial_resource_key,
                recovery_resource_key=recovery_resource_key,
            )
        )

    observed_results: list[UntargetedCidNlRuntimeSampleResult] = []
    observed_acquisitions: list[TraceAcquisitionBatch] = []

    def provide_worker_batch(_aggregate: object) -> RuntimeWorkerBatchReceipt:
        settlements: list[WorkUnitSettlement] = []
        resource_settlements: list[RuntimeResourceSettlementReceipt] = []
        artifact_registrations: list[RuntimeArtifactRegistrationReceipt] = []
        acquisitions: list[RuntimeAcquisitionReceipt] = []
        phases_by_sample = {
            phase.states[0].job.sample.identity.sample_identity_id: phase
            for phase in batch_phases
        }
        for completed, sample_states in enumerate(sample_groups, 1):
            if partition_key is not None or _request_uses_subject_batch(
                sample_states[0].job
            ):
                _execute_initial_assessment_batch(
                    phases_by_sample[
                        sample_states[0].job.sample.identity.sample_identity_id
                    ],
                    partition_candidates=partition_key is not None,
                )
            else:
                for state in sample_states:
                    _execute_initial_assessment(state)
            report_phase("partition_initial", completed, len(sample_groups))

        try:
            _prepare_owner_authorized_recoveries(
                states, alignment_config=alignment_config
            )
        except Exception as exc:
            if partition_key is None:
                raise
            for state in states:
                if state.error is None:
                    state.error = exc

        for state in states:
            if (
                partition_key is None
                and not _request_uses_subject_batch(state.job)
                and state.recovery_group_center_rts
            ):
                _execute_recovery(
                    state,
                )
        for completed, phase in enumerate(batch_phases, 1):
            _execute_recovery_batch(phase)
            report_phase("partition_recovery", completed, len(batch_phases))

        acquisition_owner_keys = (
            _batch_acquisition_owner_keys(batch_phases)
            if partition_key is None
            else frozenset()
        )
        result_context = _RuntimeResultContext(jobs)
        for completed, state in enumerate(states, 1):
            batch, sample_result, acquisition = _finalize_job_state(
                state,
                include_acquisition_receipt=(
                    partition_key is None
                    and (
                        not _request_uses_subject_batch(state.job)
                        or state.job.work_unit_key in acquisition_owner_keys
                    )
                ),
                result_context=result_context,
            )
            settlements.extend(batch.settlements)
            resource_settlements.extend(batch.resource_settlements)
            artifact_registrations.extend(batch.artifact_registrations)
            acquisitions.extend(batch.acquisitions)
            if sample_result is not None:
                observed_results.append(sample_result)
            if acquisition is not None:
                observed_acquisitions.append(acquisition)
            report_phase("partition_seal", completed, len(states))
            if progress_callback is not None and partition_key is None:
                progress_callback(
                    {
                        "event": "sample_terminal",
                        "sample_stem": state.job.sample.metadata.raw_stem,
                        "work_unit_key": state.job.work_unit_key,
                        "succeeded": sample_result is not None,
                        "recovery_attempted": bool(state.recovery_attempts),
                    }
                )
        for phase in batch_phases:
            resource_settlements.extend(_batch_phase_resource_receipts(phase))
            if partition_key is not None:
                sample_id = phase.states[0].job.sample.identity.sample_identity_id
                for name, acquisition in (
                    ("initial", phase.initial_acquisition),
                    ("recovery", phase.recovery_acquisition),
                ):
                    if acquisition is not None:
                        acquisitions.append(
                            RuntimeAcquisitionReceipt(
                                work_unit_key=f"{partition_key}:{sample_id}:{name}",
                                acquisition=acquisition,
                            )
                        )
        return RuntimeWorkerBatchReceipt(
            settlements=tuple(settlements),
            resource_settlements=tuple(resource_settlements),
            artifact_registrations=tuple(artifact_registrations),
            acquisitions=tuple(acquisitions),
        )

    return _RuntimeWork(
        reservations, provide_worker_batch, observed_results, observed_acquisitions
    )


def _execute_initial_assessment(state: _RuntimeJobState) -> None:
    job = state.job
    raw = None
    try:
        if state.error is None and state.initial_requires_raw:
            if state.resource_key is None:
                raise UntargetedCidNlRealDataRuntimeError(
                    "bounded subject is missing its acquisition reservation"
                )
            source = resolve_request_acquisition_source(
                processing_owners=job.processing_owners,
                sample=job.sample,
            )
            state.open_attempted = True
            raw = open_raw(
                job.raw_path,
                job.dll_dir,
                expected_acquisition_source=source,
            )
            state.opened = True
        if state.error is None:
            state.adapter_result = adapt_current_discovery_sample_subject(
                processing_owners=job.processing_owners,
                sample=job.sample,
                method_subject=job.method_subject,
                candidates=_request_subject_candidates(job),
                settings=job.settings,
                peak_config=job.peak_config,
                raw=raw,
                _observer=state.observer,
                signal_processing_profile=job.signal_processing_profile,
                physical_records=_request_subject_physical_records(job),
            )
    except RawReaderOpenError as exc:
        state.cleanup_outcome = (
            RuntimeResourceCleanupOutcome.FAILED
            if exc.cleanup_failed
            else RuntimeResourceCleanupOutcome.SUCCEEDED
        )
        state.error = exc
    except Exception as exc:
        state.error = exc
    finally:
        if raw is not None:
            try:
                raw.__exit__(None, None, None)
            except Exception as exc:
                state.cleanup_outcome = RuntimeResourceCleanupOutcome.FAILED
                if state.error is None:
                    state.error = exc


def _execute_initial_assessment_batch(
    phase: _BatchSamplePhaseState,
    *,
    partition_candidates: bool = False,
) -> None:
    states = phase.states
    leader = states[0]
    job = leader.job
    raw = None
    trace_batch: UntargetedCidNlSampleTraceBatch | None = None
    try:
        if any(state.initial_requires_raw and state.error is None for state in states):
            if phase.initial_resource_key is None:
                raise UntargetedCidNlRealDataRuntimeError(
                    "sample batch is missing its acquisition reservation"
                )
            source = resolve_request_acquisition_source(
                processing_owners=job.processing_owners,
                sample=job.sample,
            )
            phase.initial_open_attempted = True
            raw = open_raw(
                job.raw_path,
                job.dll_dir,
                expected_acquisition_source=source,
            )
            phase.initial_opened = True
            candidates = job.candidates
            if partition_candidates:
                candidate_ids = {
                    candidate.candidate_id
                    for state in states
                    if state.error is None
                    for candidate in _request_subject_candidates(state.job)
                }
                candidates = tuple(
                    candidate
                    for candidate in candidates
                    if candidate.candidate_id in candidate_ids
                )
            trace_batch = acquire_untargeted_cid_nl_sample_trace_batch(
                processing_owners=job.processing_owners,
                sample=job.sample,
                candidates=candidates,
                settings=job.settings,
                raw=raw,
                signal_processing_profile=job.signal_processing_profile,
            )
            phase.initial_acquisition = (
                trace_batch.acquisition if trace_batch is not None else None
            )
        for state in states:
            if state.error is not None:
                continue
            state.adapter_result = adapt_current_discovery_sample_subject(
                processing_owners=state.job.processing_owners,
                sample=state.job.sample,
                method_subject=state.job.method_subject,
                candidates=_request_subject_candidates(state.job),
                settings=state.job.settings,
                peak_config=state.job.peak_config,
                raw=raw,
                _observer=state.observer,
                _preacquired_traces=trace_batch,
                signal_processing_profile=state.job.signal_processing_profile,
                physical_records=_request_subject_physical_records(state.job),
            )
    except RawReaderOpenError as exc:
        phase.initial_cleanup_outcome = (
            RuntimeResourceCleanupOutcome.FAILED
            if exc.cleanup_failed
            else RuntimeResourceCleanupOutcome.SUCCEEDED
        )
        for state in states:
            if state.error is None:
                state.error = exc
    except Exception as exc:
        for state in states:
            if state.error is None:
                state.error = exc
    finally:
        if raw is not None:
            try:
                raw.__exit__(None, None, None)
            except Exception as exc:
                phase.initial_cleanup_outcome = RuntimeResourceCleanupOutcome.FAILED
                for state in states:
                    if state.error is None:
                        state.error = exc


def _prepare_owner_authorized_recoveries(
    states: list[_RuntimeJobState],
    *,
    alignment_config: AlignmentConfig,
) -> None:
    by_subject: dict[str, list[_RuntimeJobState]] = {}
    for state in states:
        by_subject.setdefault(
            state.job.method_subject.method_subject_id,
            [],
        ).append(state)
    for method_subject_id in sorted(by_subject):
        _prepare_subject_authorized_recoveries(
            tuple(by_subject[method_subject_id]), alignment_config=alignment_config
        )


def _prepare_subject_authorized_recoveries(
    states: tuple[_RuntimeJobState, ...],
    *,
    alignment_config: AlignmentConfig,
) -> None:
    if any(state.error is not None or state.adapter_result is None for state in states):
        return
    adapter_results = tuple(
        state.adapter_result for state in states if state.adapter_result is not None
    )
    if not any(
        result.assessment_input.assessment.content.method_result == "SUPPORT"
        for result in adapter_results
    ):
        return
    support_owners: list[SampleLocalMS1Owner] = []
    for state in states:
        result = state.adapter_result
        if result is None:
            continue
        assessment = result.assessment_input.assessment
        if assessment.content.method_result != "SUPPORT":
            continue
        hypotheses = compose_cid_nl_peak_hypotheses_from_assessment(
            processing_owners=state.job.processing_owners,
            sample=state.job.sample,
            assessment=assessment,
        )
        support_owners.extend(
            owner_from_cid_nl_assessment_support(
                sample=state.job.sample, assessment=assessment, hypothesis=hypothesis
            )
            for hypothesis in hypotheses
        )
    groups = construct_cross_sample_peak_group_hypotheses(
        tuple(support_owners),
        config=alignment_config,
    )
    first = states[0].job
    cells = compose_cid_nl_predecessor_cells(
        processing_owners=first.processing_owners,
        groups=groups,
        sample_assessments=tuple(
            (state.job.sample, result.assessment_input.assessment)
            for state, result in zip(states, adapter_results, strict=True)
        ),
        alignment_config=alignment_config,
    )
    for group in groups:
        facts = assess_conservative_backfill_group(
            tuple(
                cell
                for cell in cells
                if cell.content.group_hypothesis_id == group.group_hypothesis_id
            ),
            sample_population=tuple(state.job.sample for state in states),
        )
        if not facts.may_attempt_recovery:
            continue
        center = group.group_center_rt
        if center is None:
            raise UntargetedCidNlRealDataRuntimeError(
                "authorized recovery requires a frozen RT group"
            )
        for state in states:
            result = state.adapter_result
            assert result is not None
            assessment = result.assessment_input.assessment
            if (
                state.job.sample.identity.sample_identity_id
                not in facts.activation_eligible_sample_ids
                or assessment.content.method_result != "HOLD"
            ):
                continue
            if assessment.candidate_peaks:
                state.recovery_input = UntargetedCidNlRecoveryInput(
                    sample=state.job.sample,
                    candidate_assessment=assessment,
                )
            else:
                state.recovery_group_center_rts = tuple(
                    sorted((*state.recovery_group_center_rts, center))
                )


def _execute_recovery(
    state: _RuntimeJobState,
) -> None:
    job = state.job
    result = state.adapter_result
    if result is None:
        state.error = UntargetedCidNlRealDataRuntimeError(
            "recovery requires a completed initial assessment"
        )
        return
    if state.resource_key is None:
        state.error = UntargetedCidNlRealDataRuntimeError(
            "eligible recovery is missing its acquisition reservation"
        )
        return
    raw = None
    try:
        source = resolve_request_acquisition_source(
            processing_owners=job.processing_owners,
            sample=job.sample,
        )
        state.open_attempted = True
        raw = open_raw(
            job.raw_path,
            job.dll_dir,
            expected_acquisition_source=source,
        )
        state.opened = True
        trace_batch = acquire_untargeted_cid_nl_recovery_trace_batch(
            processing_owners=job.processing_owners,
            sample=job.sample,
            recovery_requests=tuple(
                (job.method_subject, center)
                for center in state.recovery_group_center_rts
            ),
            peak_config=job.peak_config,
            raw=raw,
        )
        for center in state.recovery_group_center_rts:
            recovery = recover_untargeted_cid_nl_sample_subject(
                processing_owners=job.processing_owners,
                sample=job.sample,
                method_subject=job.method_subject,
                group_center_rt=center,
                support_policy=result.assessment_input.assessment.content.evaluation_receipt.support_policy,
                peak_config=job.peak_config,
                raw=raw,
                _observer=state.observer,
                settings=job.settings,
                signal_processing_profile=job.signal_processing_profile,
                _preacquired_traces=trace_batch,
            )
            state.recovery_attempts += (recovery,)
        if len(state.recovery_attempts) == 1:
            state.recovery_input = state.recovery_attempts[0].recovery_input
    except RawReaderOpenError as exc:
        state.cleanup_outcome = (
            RuntimeResourceCleanupOutcome.FAILED
            if exc.cleanup_failed
            else RuntimeResourceCleanupOutcome.SUCCEEDED
        )
        state.error = exc
    except Exception as exc:
        state.error = exc
    finally:
        if raw is not None:
            try:
                raw.__exit__(None, None, None)
            except Exception as exc:
                state.cleanup_outcome = RuntimeResourceCleanupOutcome.FAILED
                if state.error is None:
                    state.error = exc


def _execute_recovery_batch(phase: _BatchSamplePhaseState) -> None:
    pending = tuple(
        sorted(
            (
                state
                for state in phase.states
                if state.error is None and state.recovery_group_center_rts
            ),
            key=lambda state: state.job.method_subject.method_subject_id,
        )
    )
    if not pending:
        return
    if phase.recovery_resource_key is None:
        error = UntargetedCidNlRealDataRuntimeError(
            "eligible recovery batch is missing its phase reservation"
        )
        for state in pending:
            state.error = error
        return
    first = pending[0].job
    raw = None
    trace_batch: UntargetedCidNlRecoveryTraceBatch | None = None
    try:
        source = resolve_request_acquisition_source(
            processing_owners=first.processing_owners,
            sample=first.sample,
        )
        phase.recovery_open_attempted = True
        raw = open_raw(
            first.raw_path,
            first.dll_dir,
            expected_acquisition_source=source,
        )
        phase.recovery_opened = True
        trace_batch = acquire_untargeted_cid_nl_recovery_trace_batch(
            processing_owners=first.processing_owners,
            sample=first.sample,
            recovery_requests=tuple(
                (
                    state.job.method_subject,
                    center,
                )
                for state in pending
                for center in state.recovery_group_center_rts
            ),
            peak_config=first.peak_config,
            raw=raw,
        )
        phase.recovery_acquisition = trace_batch.acquisition
        for state in pending:
            result = state.adapter_result
            assert result is not None
            for center in state.recovery_group_center_rts:
                recovery = recover_untargeted_cid_nl_sample_subject(
                    processing_owners=state.job.processing_owners,
                    sample=state.job.sample,
                    method_subject=state.job.method_subject,
                    group_center_rt=center,
                    support_policy=(
                        result.assessment_input.assessment.content.evaluation_receipt.support_policy
                    ),
                    peak_config=state.job.peak_config,
                    raw=raw if state.job.method_subject.composition else None,
                    settings=state.job.settings,
                    signal_processing_profile=state.job.signal_processing_profile,
                    _observer=state.observer,
                    _preacquired_traces=trace_batch,
                )
                state.recovery_attempts += (recovery,)
            if len(state.recovery_attempts) == 1:
                state.recovery_input = state.recovery_attempts[0].recovery_input
    except RawReaderOpenError as exc:
        phase.recovery_cleanup_outcome = (
            RuntimeResourceCleanupOutcome.FAILED
            if exc.cleanup_failed
            else RuntimeResourceCleanupOutcome.SUCCEEDED
        )
        for state in pending:
            state.error = exc
    except Exception as exc:
        for state in pending:
            state.error = exc
    finally:
        if raw is not None:
            try:
                raw.__exit__(None, None, None)
            except Exception as exc:
                phase.recovery_cleanup_outcome = RuntimeResourceCleanupOutcome.FAILED
                for state in pending:
                    if state.error is None:
                        state.error = exc


def _batch_phase_resource_receipts(
    phase: _BatchSamplePhaseState,
) -> tuple[RuntimeResourceSettlementReceipt, ...]:
    attempt = phase.states[0].job.processing_owners.attempt
    receipts: list[RuntimeResourceSettlementReceipt] = []
    for resource_key, attempted, opened, cleanup in (
        (
            phase.initial_resource_key,
            phase.initial_open_attempted,
            phase.initial_opened,
            phase.initial_cleanup_outcome,
        ),
        (
            phase.recovery_resource_key,
            phase.recovery_open_attempted,
            phase.recovery_opened,
            phase.recovery_cleanup_outcome,
        ),
    ):
        if resource_key is None:
            continue
        receipts.append(
            RuntimeResourceSettlementReceipt(
                attempt=attempt,
                resource_key=resource_key,
                open_outcome=(
                    RuntimeResourceOpenOutcome.OPENED
                    if opened
                    else (
                        RuntimeResourceOpenOutcome.OPEN_FAILED
                        if attempted
                        else RuntimeResourceOpenOutcome.CONDITION_NOT_ACTIVATED
                    )
                ),
                cleanup_outcome=cleanup,
            )
        )
    return tuple(receipts)


def _batch_acquisition_owner_keys(
    phases: list[_BatchSamplePhaseState],
) -> frozenset[str]:
    owners: set[str] = set()
    for phase in phases:
        for acquisition in (
            phase.initial_acquisition,
            phase.recovery_acquisition,
        ):
            if acquisition is None:
                continue
            owner = next(
                state
                for state in phase.states
                if state.observer.acquisition is acquisition
            )
            owners.add(owner.job.work_unit_key)
    return frozenset(owners)


def _finalize_job_state(
    state: _RuntimeJobState,
    *,
    include_acquisition_receipt: bool = True,
    result_context: _RuntimeResultContext | None = None,
) -> tuple[
    RuntimeWorkerBatchReceipt,
    UntargetedCidNlRuntimeSampleResult | None,
    TraceAcquisitionBatch | None,
]:
    job = state.job
    adapter_result = state.adapter_result
    acquisition = state.observer.acquisition

    try:
        observed_artifacts = state.observer.artifact_closure()
    except Exception as exc:
        observed_artifacts = state.observer.observed_artifacts()
        if state.error is None:
            state.error = exc

    result_ref: UntargetedCidNlSampleResultRef | None = None
    registered_artifacts = observed_artifacts
    if adapter_result is not None:
        try:
            expected_lineage = canonical_current_attempt_artifact_closure(
                producer_attempt=job.processing_owners.attempt,
                artifacts=(
                    *adapter_result.lineage_artifacts,
                    *(
                        artifact
                        for recovery in state.recovery_attempts
                        for artifact in recovery.lineage_artifacts
                    ),
                ),
            )
            if observed_artifacts != expected_lineage:
                raise UntargetedCidNlRealDataRuntimeError(
                    "worker observer does not close the exact adapter/recovery result"
                )
            result_ref = _mint_sample_result_ref(
                job=job,
                result=adapter_result,
                recovery_input=state.recovery_input,
                recovery_attempt=None,
                recovery_attempts=state.recovery_attempts,
                acquisition=acquisition,
                acquisition_content=(
                    result_context.acquisition_content
                    if result_context is not None
                    else _canonical_acquisition_content
                ),
            )
            registered_artifacts = canonical_current_attempt_artifact_closure(
                producer_attempt=job.processing_owners.attempt,
                artifacts=(*observed_artifacts, result_ref.artifact),
            )
        except Exception as exc:
            if state.error is None:
                state.error = exc

    success = (
        state.error is None and adapter_result is not None and result_ref is not None
    )
    if not success and state.error is None:
        state.error = UntargetedCidNlRealDataRuntimeError(
            "adapter returned no terminal scientific result"
        )
    settlement = mint_work_unit_settlement(
        attempt=job.processing_owners.attempt,
        work_unit_key=job.work_unit_key,
        outcome=(
            WorkUnitTerminalOutcome.SUCCEEDED
            if success
            else WorkUnitTerminalOutcome.FAILED
        ),
        owner_result_ref=(
            result_ref.artifact if success and result_ref is not None else None
        ),
        failure_detail=None if success else _failure_detail(job, state.error),
    )
    resource_receipts = (
        (
            RuntimeResourceSettlementReceipt(
                attempt=job.processing_owners.attempt,
                resource_key=state.resource_key,
                open_outcome=(
                    RuntimeResourceOpenOutcome.OPENED
                    if state.opened
                    else (
                        RuntimeResourceOpenOutcome.OPEN_FAILED
                        if state.open_attempted
                        else RuntimeResourceOpenOutcome.CONDITION_NOT_ACTIVATED
                    )
                ),
                cleanup_outcome=state.cleanup_outcome,
            ),
        )
        if state.resource_key is not None
        else ()
    )
    batch = RuntimeWorkerBatchReceipt(
        settlements=(settlement,),
        resource_settlements=resource_receipts,
        artifact_registrations=tuple(
            RuntimeArtifactRegistrationReceipt(
                work_unit_key=job.work_unit_key,
                artifact=artifact,
            )
            for artifact in registered_artifacts
        ),
        acquisitions=(
            (
                RuntimeAcquisitionReceipt(
                    work_unit_key=job.work_unit_key,
                    acquisition=acquisition,
                ),
            )
            if acquisition is not None and include_acquisition_receipt
            else ()
        ),
    )
    sample_result = (
        UntargetedCidNlRuntimeSampleResult(
            job=job,
            adapter_result=adapter_result,
            result_ref=result_ref,
            recovery_input=state.recovery_input,
            recovery_attempts=state.recovery_attempts,
        )
        if success and adapter_result is not None and result_ref is not None
        else None
    )
    return (
        batch,
        sample_result,
        acquisition if include_acquisition_receipt else None,
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
    return payload, hashlib.sha256(payload).hexdigest()


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
        # Preserve this sample's candidate order, independently of membership order.
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


def _runtime_sample_groups(
    states: list[_RuntimeJobState],
) -> tuple[tuple[_RuntimeJobState, ...], ...]:
    grouped: dict[str, list[_RuntimeJobState]] = {}
    for state in states:
        sample_id = state.job.sample.identity.sample_identity_id
        grouped.setdefault(sample_id, []).append(state)
    groups = tuple(tuple(group) for group in grouped.values())
    for group in groups:
        first = group[0].job
        if any(
            state.job.processing_owners != first.processing_owners
            or state.job.sample != first.sample
            or state.job.candidates != first.candidates
            or state.job.discovery_receipt != first.discovery_receipt
            or state.job.settings != first.settings
            or state.job.peak_config != first.peak_config
            or state.job.raw_path != first.raw_path
            or state.job.dll_dir != first.dll_dir
            for state in group[1:]
        ):
            raise UntargetedCidNlRealDataRuntimeError(
                "sample-local runtime jobs crossed their shared acquisition inputs"
            )
    return groups


def _failure_detail(
    job: UntargetedCidNlRealDataRuntimeJob,
    error: Exception | None,
) -> WorkUnitFailureDetail:
    observed = error or UntargetedCidNlRealDataRuntimeError("unknown worker failure")
    message = " ".join(str(observed).split()) or type(observed).__name__
    return WorkUnitFailureDetail(
        category=WorkUnitFailureCategory.EXECUTION_ERROR,
        stage="untargeted-cid-nl-real-data-adapter",
        source_reference=f"untargeted-cid-nl-real-data:{job.work_unit_key}",
        message=f"{type(observed).__name__}: {message}",
    )


def _validate_finalized_acquisition_metrics(
    finalization: RuntimeFinalizationReceipt,
    acquisitions: tuple[TraceAcquisitionBatch, ...],
) -> None:
    try:
        validate_runtime_acquisition_metrics(
            finalization.observability_ledger,
            acquisitions,
        )
    except RuntimeObservabilityContractError as exc:
        raise UntargetedCidNlRealDataRuntimeError(
            "runtime acquisition metrics do not match observed RAW work"
        ) from exc


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise UntargetedCidNlRealDataRuntimeError(
            f"{field} must be non-empty owner-normalized text"
        )
    return value


__all__ = (
    "RUNTIME_CALL_COST_METRIC_NAMES",
    "UntargetedCidNlRealDataRuntimeApplicationResult",
    "UntargetedCidNlRealDataRuntimeError",
    "UntargetedCidNlRealDataRuntimeJob",
    "UntargetedCidNlRuntimeSampleResult",
    "execute_untargeted_cid_nl_real_data_runtime",
    "untargeted_cid_nl_runtime_call_cost",
)
