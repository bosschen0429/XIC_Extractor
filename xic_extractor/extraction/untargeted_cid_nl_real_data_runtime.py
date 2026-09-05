"""Atomic runtime owner for replayable Untargeted CID-NL real-data work."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.acquisition_contract import TraceAcquisitionBatch
from xic_extractor.alignment.conservative_backfill import (
    ACTIVATION_ELIGIBLE_SAMPLE_ROLES,
    minimum_direct_support_count,
)
from xic_extractor.alignment.cross_sample_peak_groups import (
    construct_cross_sample_peak_group_hypotheses,
)
from xic_extractor.alignment.hypothesis_owner_adapter import (
    compose_cid_nl_peak_hypotheses_from_assessment,
    owner_from_cid_nl_assessment_support,
)
from xic_extractor.alignment.untargeted_cid_nl_golden_path import (
    CID_NL_ALIGNMENT_CONFIG,
    UntargetedCidNlGoldenPathError,
    UntargetedCidNlRecoveryInput,
    build_untargeted_cid_nl_golden_path,
)
from xic_extractor.config import ExtractionConfig
from xic_extractor.discovery.batch_contract import (
    DiscoveryCandidateUniverseReceipt,
)
from xic_extractor.discovery.models import DiscoveryCandidate, DiscoverySettings
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
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    BATCH_SCHEMA_VERSION,
    REQUEST_INPUT_KEY,
    CidNlMethodSubject,
    CidNlMethodSubjectContractError,
    cid_nl_method_subject_batch_from_request,
)
from xic_extractor.processing_identity import (
    IdentityContractError,
    ProcessingOwnerReferences,
    canonical_current_attempt_artifact_closure,
    resolve_request_acquisition_source,
)
from xic_extractor.raw_reader import RawReaderOpenError, open_raw
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

SCHEMA_VERSION = "untargeted_cid_nl_real_data_runtime_result_v2"
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
        for field, kind in expected:
            if not isinstance(getattr(self, field), kind):
                raise UntargetedCidNlRealDataRuntimeError(
                    f"{field} has the wrong owner type"
                )
        if not isinstance(self.candidates, tuple) or any(
            not isinstance(item, DiscoveryCandidate) for item in self.candidates
        ):
            raise UntargetedCidNlRealDataRuntimeError(
                "candidates must contain current DiscoveryCandidate values"
            )
        _required_text(self.work_unit_key, field="work_unit_key")
        _validate_request_bound_inputs(self)


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
    recovery_attempt: UntargetedCidNlRecoveryAttemptResult | None = None

    @property
    def effective_assessment(self) -> CidNlMethodAssessmentRef:
        """Return the terminal assessment after any authorized recovery."""

        if self.recovery_attempt is not None:
            return self.recovery_attempt.assessment
        return self.adapter_result.assessment_input.assessment


@dataclass(slots=True)
class _RuntimeJobState:
    job: UntargetedCidNlRealDataRuntimeJob
    resource_key: str | None
    initial_requires_raw: bool
    observer: _UntargetedRealDataWorkObserver
    error: Exception | None = None
    adapter_result: UntargetedCidNlRealDataAdapterResult | None = None
    recovery_input: UntargetedCidNlRecoveryInput | None = None
    recovery_attempt: UntargetedCidNlRecoveryAttemptResult | None = None
    recovery_group_center_rt: float | None = None
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
) -> UntargetedCidNlRealDataRuntimeApplicationResult:
    """Execute every request sample through one attempt and one finalization."""

    if not isinstance(runtime, AttemptRuntime):
        raise UntargetedCidNlRealDataRuntimeError("runtime must be an AttemptRuntime")
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
        if sum(
            direct_assessment_by_key[job.work_unit_key] for job in subject_jobs
        )
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
                not _request_uses_subject_batch(job)
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
        if not _request_uses_subject_batch(sample_states[0].job):
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
                    owner_conditional=owner_conditional,
                )
            )
        batch_phases.append(
            _BatchSamplePhaseState(
                states=sample_states,
                initial_resource_key=initial_resource_key,
                recovery_resource_key=recovery_resource_key,
            )
        )

    recovery_marker.record_launch(runtime)
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
        for sample_states in sample_groups:
            if _request_uses_subject_batch(sample_states[0].job):
                _execute_initial_assessment_batch(
                    phases_by_sample[
                        sample_states[0].job.sample.identity.sample_identity_id
                    ]
                )
            else:
                for state in sample_states:
                    _execute_initial_assessment(state)

        _prepare_owner_authorized_recoveries(states)

        for state in states:
            if (
                not _request_uses_subject_batch(state.job)
                and state.recovery_group_center_rt is not None
            ):
                _execute_recovery(
                    state,
                    group_center_rt=state.recovery_group_center_rt,
                )
        for phase in batch_phases:
            _execute_recovery_batch(phase)

        acquisition_owner_keys = _batch_acquisition_owner_keys(batch_phases)
        for state in states:
            batch, sample_result, acquisition = _finalize_job_state(
                state,
                include_acquisition_receipt=(
                    not _request_uses_subject_batch(state.job)
                    or state.job.work_unit_key in acquisition_owner_keys
                ),
            )
            settlements.extend(batch.settlements)
            resource_settlements.extend(batch.resource_settlements)
            artifact_registrations.extend(batch.artifact_registrations)
            acquisitions.extend(batch.acquisitions)
            if sample_result is not None:
                observed_results.append(sample_result)
            if acquisition is not None:
                observed_acquisitions.append(acquisition)
            if progress_callback is not None:
                progress_callback(
                    {
                        "event": "sample_terminal",
                        "sample_stem": state.job.sample.metadata.raw_stem,
                        "work_unit_key": state.job.work_unit_key,
                        "succeeded": sample_result is not None,
                        "recovery_attempted": state.recovery_attempt is not None,
                    }
                )
        for phase in batch_phases:
            resource_settlements.extend(_batch_phase_resource_receipts(phase))
        return RuntimeWorkerBatchReceipt(
            settlements=tuple(settlements),
            resource_settlements=tuple(resource_settlements),
            artifact_registrations=tuple(artifact_registrations),
            acquisitions=tuple(acquisitions),
        )

    finalization = execute_runtime_attempt(
        runtime,
        reservations,
        worker_batch_provider=provide_worker_batch,
        telemetry_provider=telemetry_provider,
        persister=recovery_marker,
    )
    _validate_finalized_acquisition_metrics(
        finalization,
        tuple(observed_acquisitions),
    )
    succeeded = (
        finalization.supervisor.terminal_outcome is AttemptTerminalOutcome.SUCCEEDED
    )
    return UntargetedCidNlRealDataRuntimeApplicationResult(
        finalization=finalization,
        sample_results=(tuple(observed_results) if succeeded else ()),
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
            trace_batch = acquire_untargeted_cid_nl_sample_trace_batch(
                processing_owners=job.processing_owners,
                sample=job.sample,
                candidates=job.candidates,
                settings=job.settings,
                raw=raw,
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
) -> None:
    by_subject: dict[str, list[_RuntimeJobState]] = {}
    for state in states:
        by_subject.setdefault(
            state.job.method_subject.method_subject_id,
            [],
        ).append(state)
    for method_subject_id in sorted(by_subject):
        _prepare_subject_authorized_recoveries(
            tuple(by_subject[method_subject_id])
        )


def _prepare_subject_authorized_recoveries(
    states: tuple[_RuntimeJobState, ...],
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
    support_owners = []
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
        if len(hypotheses) != 1:
            return
        support_owners.append(
            owner_from_cid_nl_assessment_support(
                sample=state.job.sample,
                assessment=assessment,
                hypothesis=hypotheses[0],
            )
        )
    legacy_groups = construct_cross_sample_peak_group_hypotheses(
        tuple(support_owners),
        config=CID_NL_ALIGNMENT_CONFIG,
    )
    if len(legacy_groups) != 1:
        return
    first = states[0].job
    try:
        preliminary = build_untargeted_cid_nl_golden_path(
            processing_owners=first.processing_owners,
            method_subject=first.method_subject,
            sample_inputs=tuple(result.assessment_input for result in adapter_results),
            source_artifact_relpath=(
                "output/real_data_runtime/preliminary_untargeted_cid_nl_source.json"
            ),
        )
    except UntargetedCidNlGoldenPathError as exc:
        for state in states:
            state.error = exc
        return
    facts = preliminary.backfill_facts
    if not facts.may_attempt_recovery:
        return
    group = preliminary.spine.group
    if group is None or group.group_center_rt is None:
        error = UntargetedCidNlRealDataRuntimeError(
            "Backfill owner authorized recovery without a frozen RT group"
        )
        for state in states:
            state.error = error
        return
    eligible_ids = set(facts.activation_eligible_sample_ids)
    for state in states:
        result = state.adapter_result
        if result is None or state.error is not None:
            continue
        assessment = result.assessment_input.assessment
        sample_id = state.job.sample.identity.sample_identity_id
        if sample_id not in eligible_ids or assessment.content.method_result != "HOLD":
            continue
        if assessment.candidate_peaks:
            state.recovery_input = UntargetedCidNlRecoveryInput(
                sample=state.job.sample,
                candidate_assessment=assessment,
            )
            continue
        state.recovery_group_center_rt = group.group_center_rt


def _execute_recovery(
    state: _RuntimeJobState,
    *,
    group_center_rt: float,
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
        state.recovery_attempt = recover_untargeted_cid_nl_sample_subject(
            processing_owners=job.processing_owners,
            sample=job.sample,
            method_subject=job.method_subject,
            group_center_rt=group_center_rt,
            support_policy=(
                result.assessment_input.assessment.content.evaluation_receipt.support_policy
            ),
            peak_config=job.peak_config,
            raw=raw,
            _observer=state.observer,
        )
        state.recovery_input = state.recovery_attempt.recovery_input
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
                if state.error is None
                and state.recovery_group_center_rt is not None
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
                    state.recovery_group_center_rt,
                )
                for state in pending
                if state.recovery_group_center_rt is not None
            ),
            peak_config=first.peak_config,
            raw=raw,
        )
        phase.recovery_acquisition = trace_batch.acquisition
        for state in pending:
            result = state.adapter_result
            assert result is not None
            assert state.recovery_group_center_rt is not None
            state.recovery_attempt = recover_untargeted_cid_nl_sample_subject(
                processing_owners=state.job.processing_owners,
                sample=state.job.sample,
                method_subject=state.job.method_subject,
                group_center_rt=state.recovery_group_center_rt,
                support_policy=(
                    result.assessment_input.assessment.content.evaluation_receipt.support_policy
                ),
                peak_config=state.job.peak_config,
                raw=None,
                _observer=state.observer,
                _preacquired_traces=trace_batch,
            )
            state.recovery_input = state.recovery_attempt.recovery_input
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
                phase.recovery_cleanup_outcome = (
                    RuntimeResourceCleanupOutcome.FAILED
                )
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
                        state.recovery_attempt.lineage_artifacts
                        if state.recovery_attempt is not None
                        else ()
                    ),
                ),
            )
            if observed_artifacts != expected_lineage:
                raise UntargetedCidNlRealDataRuntimeError(
                    "worker observer does not close the exact adapter/recovery result"
                )
            result_ref = mint_untargeted_cid_nl_sample_result_ref(
                job=job,
                result=adapter_result,
                recovery_input=state.recovery_input,
                recovery_attempt=state.recovery_attempt,
                acquisition=acquisition,
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
            recovery_attempt=state.recovery_attempt,
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
) -> UntargetedCidNlSampleResultRef:
    assessment = result.assessment_input.assessment
    projections = result.assessment_input.discovery_projections
    acquisition_sha256 = (
        hashlib.sha256(_canonical_acquisition_bytes(acquisition)).hexdigest()
        if acquisition is not None
        else None
    )
    if (
        recovery_attempt is not None
        and recovery_input != recovery_attempt.recovery_input
    ):
        raise UntargetedCidNlRealDataRuntimeError(
            "recovery input does not match the observed recovery attempt"
        )
    recovery_assessment = (
        recovery_attempt.assessment
        if recovery_attempt is not None
        else (
            recovery_input.candidate_assessment if recovery_input is not None else None
        )
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
        "recovery_candidate_count": (
            len(recovery_assessment.candidate_peaks)
            if recovery_assessment is not None
            else 0
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
        upstream_artifacts=(
            assessment.artifact,
            *(projection.artifact for projection in projections),
            *(
                (recovery_assessment.artifact,)
                if recovery_assessment is not None
                and recovery_assessment.artifact != assessment.artifact
                else ()
            ),
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
        recovery_candidate_count=(
            len(recovery_assessment.candidate_peaks)
            if recovery_assessment is not None
            else 0
        ),
        acquisition_sha256=acquisition_sha256,
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


def _validate_request_bound_inputs(job: UntargetedCidNlRealDataRuntimeJob) -> None:
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
    subject_payload = request_inputs.get(REQUEST_INPUT_KEY)
    if (
        isinstance(subject_payload, dict)
        and subject_payload.get("schema_version") == BATCH_SCHEMA_VERSION
    ):
        try:
            batch = cid_nl_method_subject_batch_from_request(request)
        except CidNlMethodSubjectContractError as exc:
            raise UntargetedCidNlRealDataRuntimeError(
                "processing request has an invalid CID-NL subject batch"
            ) from exc
        matching_subjects = tuple(
            subject
            for subject in batch.subjects
            if subject.method_subject_id == job.method_subject.method_subject_id
        )
        if matching_subjects != (job.method_subject,):
            raise UntargetedCidNlRealDataRuntimeError(
                "method subject is not an exact member of the processing request"
            )
    elif subject_payload != job.method_subject.request_payload():
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


def _request_subject_candidates(
    job: UntargetedCidNlRealDataRuntimeJob,
) -> tuple[DiscoveryCandidate, ...]:
    request_inputs = json.loads(
        job.processing_owners.request.normalized_scientific_inputs_json
    )
    subject_payload = request_inputs.get(REQUEST_INPUT_KEY)
    if not (
        isinstance(subject_payload, dict)
        and subject_payload.get("schema_version") == BATCH_SCHEMA_VERSION
    ):
        return _bounded_subject_candidates(
            job.candidates,
            sample=job.sample,
            method_subject=job.method_subject,
            settings=job.settings,
        )
    try:
        batch = cid_nl_method_subject_batch_from_request(
            job.processing_owners.request
        )
    except CidNlMethodSubjectContractError as exc:
        raise UntargetedCidNlRealDataRuntimeError(
            "processing request has an invalid CID-NL subject batch"
        ) from exc
    member_ids = {
        candidate_id
        for candidate_id, method_subject_id in batch.candidate_memberships
        if method_subject_id == job.method_subject.method_subject_id
    }
    return tuple(
        candidate
        for candidate in job.candidates
        if candidate.candidate_id in member_ids
    )


def _request_uses_subject_batch(job: UntargetedCidNlRealDataRuntimeJob) -> bool:
    request_inputs = json.loads(
        job.processing_owners.request.normalized_scientific_inputs_json
    )
    subject_payload = request_inputs.get(REQUEST_INPUT_KEY)
    return (
        isinstance(subject_payload, dict)
        and subject_payload.get("schema_version") == BATCH_SCHEMA_VERSION
    )


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
