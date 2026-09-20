"""In-process and pool harnesses for untargeted real-data extraction."""

from __future__ import annotations

import importlib
from dataclasses import dataclass

from xic_extractor.acquisition_contract import TraceAcquisitionBatch
from xic_extractor.alignment.cell_observations import (
    compose_cid_nl_predecessor_cells,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.conservative_backfill import (
    assess_conservative_backfill_group,
)
from xic_extractor.alignment.cross_sample_peak_groups import (
    construct_cross_sample_peak_group_hypotheses,
)
from xic_extractor.alignment.hypothesis_owner_adapter import (
    compose_cid_nl_peak_hypotheses_from_assessment,
    owner_from_cid_nl_assessment_support,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.alignment.untargeted_cid_nl_golden_path import (
    UntargetedCidNlRecoveryInput,
)
from xic_extractor.evidence_artifacts import EvidenceArtifactRef
from xic_extractor.execution_runtime_finalization import (
    RuntimeResourceCleanupOutcome,
)
from xic_extractor.extraction.untargeted_cid_nl_real_data_adapter import (
    UntargetedCidNlRealDataAdapterResult,
    UntargetedCidNlRecoveryAttemptResult,
    UntargetedCidNlRecoveryTraceBatch,
    UntargetedCidNlSampleTraceBatch,
    _UntargetedRealDataWorkObserver,
    acquire_untargeted_cid_nl_recovery_trace_batch,
    acquire_untargeted_cid_nl_sample_trace_batch,
    adapt_current_discovery_sample_subject,
    recover_untargeted_cid_nl_sample_subject,
)
from xic_extractor.extraction.untargeted_real_data_models import (
    UntargetedCidNlRealDataRuntimeError,
    UntargetedCidNlRealDataRuntimeJob,
    _request_subject_candidates,
    _request_subject_physical_records,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    is_cid_nl_missing_ms1_rejection,
    is_unsearched_cid_nl_discovery_assessment,
)
from xic_extractor.processing_identity import resolve_request_acquisition_source
from xic_extractor.raw_reader import (
    RawFileHandle,
    RawReaderOpenError,
    _ensure_assemblies_loaded,
    open_raw,
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


def _runtime_delegate(name: str, fallback):
    import sys

    rt = sys.modules.get("xic_extractor.extraction.untargeted_real_data_runtime")
    if rt is not None and hasattr(rt, name):
        return getattr(rt, name)
    return fallback


def _open_job_raw(job: UntargetedCidNlRealDataRuntimeJob) -> RawFileHandle:
    active_open = _runtime_delegate("open_raw", open_raw)
    expectation = job.acquisition_source_expectation
    if expectation is not None:
        return active_open(
            job.raw_path,
            job.dll_dir,
            expected_acquisition_source=expectation.source,
            _expected_path_identity=expectation.path_identity,
        )
    return active_open(
        job.raw_path,
        job.dll_dir,
        expected_acquisition_source=resolve_request_acquisition_source(
            processing_owners=job.processing_owners, sample=job.sample
        ),
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
            state.open_attempted = True
            raw = _open_job_raw(job)
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
            phase.initial_open_attempted = True
            raw = _open_job_raw(job)
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
            active_acquire = _runtime_delegate(
                "acquire_untargeted_cid_nl_sample_trace_batch",
                acquire_untargeted_cid_nl_sample_trace_batch,
            )
            trace_batch = active_acquire(
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
    retained_hypotheses_by_assessment = {}
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
        retained_hypotheses_by_assessment[
            (
                state.job.sample.identity.sample_identity_id,
                assessment.artifact.content_identity.artifact_content_id,
            )
        ] = hypotheses
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
        retained_hypotheses_by_assessment=retained_hypotheses_by_assessment,
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
                or not (
                    assessment.content.method_result == "HOLD"
                    or is_unsearched_cid_nl_discovery_assessment(assessment)
                    or is_cid_nl_missing_ms1_rejection(assessment)
                )
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
        state.open_attempted = True
        raw = _open_job_raw(job)
        state.opened = True
        active_acquire = _runtime_delegate(
            "acquire_untargeted_cid_nl_recovery_trace_batch",
            acquire_untargeted_cid_nl_recovery_trace_batch,
        )
        trace_batch = active_acquire(
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
                initial_assessment=(
                    result.assessment_input.assessment
                    if is_cid_nl_missing_ms1_rejection(
                        result.assessment_input.assessment
                    )
                    else None
                ),
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
        phase.recovery_open_attempted = True
        raw = _open_job_raw(first)
        phase.recovery_opened = True
        active_acquire = _runtime_delegate(
            "acquire_untargeted_cid_nl_recovery_trace_batch",
            acquire_untargeted_cid_nl_recovery_trace_batch,
        )
        trace_batch = active_acquire(
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
                    initial_assessment=(
                        result.assessment_input.assessment
                        if is_cid_nl_missing_ms1_rejection(
                            result.assessment_input.assessment
                        )
                        else None
                    ),
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


@dataclass(slots=True)
class _SampleInitialWorkerRequest:
    jobs: tuple[UntargetedCidNlRealDataRuntimeJob, ...]
    initial_resource_key: str | None
    partition_candidates: bool


@dataclass(slots=True)
class _SampleInitialWorkerResponse:
    adapter_results: tuple[UntargetedCidNlRealDataAdapterResult | None, ...]
    observer_artifacts: tuple[tuple[EvidenceArtifactRef, ...], ...]
    observer_has_acquisitions: tuple[bool, ...]
    initial_open_attempted: bool
    initial_opened: bool
    initial_cleanup_outcome: RuntimeResourceCleanupOutcome
    initial_acquisition: TraceAcquisitionBatch | None
    errors: tuple[Exception | None, ...]


def _execute_sample_initial_worker(
    request: _SampleInitialWorkerRequest,
) -> _SampleInitialWorkerResponse:
    first_job = request.jobs[0]
    _ensure_assemblies_loaded(first_job.dll_dir, importlib.import_module)
    states = tuple(
        _RuntimeJobState(
            job=job,
            resource_key=None,
            initial_requires_raw=bool(_request_subject_candidates(job)),
            observer=_UntargetedRealDataWorkObserver(job.processing_owners),
        )
        for job in request.jobs
    )
    phase = _BatchSamplePhaseState(
        states=states,
        initial_resource_key=request.initial_resource_key,
        recovery_resource_key=None,
    )
    _execute_initial_assessment_batch(
        phase,
        partition_candidates=request.partition_candidates,
    )
    return _SampleInitialWorkerResponse(
        adapter_results=tuple(s.adapter_result for s in states),
        observer_artifacts=tuple(s.observer.observed_artifacts() for s in states),
        observer_has_acquisitions=tuple(
            s.observer.acquisition is not None for s in states
        ),
        initial_open_attempted=phase.initial_open_attempted,
        initial_opened=phase.initial_opened,
        initial_cleanup_outcome=phase.initial_cleanup_outcome,
        initial_acquisition=phase.initial_acquisition,
        errors=tuple(s.error for s in states),
    )


@dataclass(slots=True)
class _SampleRecoveryWorkerRequest:
    jobs: tuple[UntargetedCidNlRealDataRuntimeJob, ...]
    recovery_group_center_rts_by_key: dict[str, tuple[float, ...]]
    recovery_inputs_by_key: dict[str, UntargetedCidNlRecoveryInput | None]
    adapter_results_by_key: dict[str, UntargetedCidNlRealDataAdapterResult | None]
    recovery_resource_key: str | None


@dataclass(slots=True)
class _SampleRecoveryWorkerResponse:
    recovery_attempts_by_key: dict[
        str, tuple[UntargetedCidNlRecoveryAttemptResult, ...]
    ]
    recovery_inputs_by_key: dict[str, UntargetedCidNlRecoveryInput | None]
    observer_artifacts: tuple[tuple[EvidenceArtifactRef, ...], ...]
    observer_has_acquisitions: tuple[bool, ...]
    recovery_open_attempted: bool
    recovery_opened: bool
    recovery_cleanup_outcome: RuntimeResourceCleanupOutcome
    recovery_acquisition: TraceAcquisitionBatch | None
    errors: tuple[Exception | None, ...]


def _execute_sample_recovery_worker(
    request: _SampleRecoveryWorkerRequest,
) -> _SampleRecoveryWorkerResponse:
    first_job = request.jobs[0]
    _ensure_assemblies_loaded(first_job.dll_dir, importlib.import_module)
    states = tuple(
        _RuntimeJobState(
            job=job,
            resource_key=None,
            initial_requires_raw=False,
            observer=_UntargetedRealDataWorkObserver(job.processing_owners),
            adapter_result=request.adapter_results_by_key.get(job.work_unit_key),
            recovery_input=request.recovery_inputs_by_key.get(job.work_unit_key),
            recovery_group_center_rts=request.recovery_group_center_rts_by_key.get(
                job.work_unit_key, ()
            ),
        )
        for job in request.jobs
    )
    phase = _BatchSamplePhaseState(
        states=states,
        initial_resource_key=None,
        recovery_resource_key=request.recovery_resource_key,
    )
    _execute_recovery_batch(phase)
    return _SampleRecoveryWorkerResponse(
        recovery_attempts_by_key={
            s.job.work_unit_key: s.recovery_attempts
            for s in states
            if s.recovery_attempts
        },
        recovery_inputs_by_key={
            s.job.work_unit_key: s.recovery_input
            for s in states
            if s.recovery_input is not None
        },
        observer_artifacts=tuple(s.observer.observed_artifacts() for s in states),
        observer_has_acquisitions=tuple(
            s.observer.acquisition is not None for s in states
        ),
        recovery_open_attempted=phase.recovery_open_attempted,
        recovery_opened=phase.recovery_opened,
        recovery_cleanup_outcome=phase.recovery_cleanup_outcome,
        recovery_acquisition=phase.recovery_acquisition,
        errors=tuple(s.error for s in states),
    )


__all__ = [
    "_BatchSamplePhaseState",
    "_RuntimeJobState",
    "_SampleInitialWorkerRequest",
    "_SampleInitialWorkerResponse",
    "_SampleRecoveryWorkerRequest",
    "_SampleRecoveryWorkerResponse",
    "_execute_initial_assessment",
    "_execute_initial_assessment_batch",
    "_execute_recovery",
    "_execute_recovery_batch",
    "_execute_sample_initial_worker",
    "_execute_sample_recovery_worker",
    "_open_job_raw",
    "_prepare_owner_authorized_recoveries",
    "_prepare_subject_authorized_recoveries",
]
