"""Execute Untargeted partition acquisition and assessment under one attempt."""

from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from xic_extractor.acquisition_contract import TraceAcquisitionBatch
from xic_extractor.alignment.cid_nl_inputs import (
    CID_NL_ALIGNMENT_CONFIG,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.conservative_backfill import (
    ACTIVATION_ELIGIBLE_SAMPLE_ROLES,
    minimum_direct_support_count,
)
from xic_extractor.configuration.execution_profile import DEFAULT_ALIGNMENT_WORKERS
from xic_extractor.execution._finalization import (
    RuntimeResourceKind,
    RuntimeResourceOpenOutcome,
    RuntimeResourceReservation,
)
from xic_extractor.execution._lifecycle import (
    AttemptRuntime,
    AttemptTerminalOutcome,
    WorkUnitFailureCategory,
    WorkUnitFailureDetail,
    WorkUnitSettlement,
    WorkUnitTerminalOutcome,
)
from xic_extractor.execution._observability import (
    RuntimeMetricAvailability,
    RuntimeMetricScope,
    RuntimeObservabilityContractError,
    validate_runtime_acquisition_metrics,
)
from xic_extractor.execution.attempt import (
    RuntimeAcquisitionReceipt,
    RuntimeArtifactRegistrationReceipt,
    RuntimeFinalizationReceipt,
    RuntimeResourceSettlementReceipt,
    RuntimeTelemetryProvider,
    RuntimeWorkerBatchReceipt,
    execute_runtime_attempt,
    mint_work_unit_settlement,
)
from xic_extractor.execution.recovery import OwnerPathRuntimeMarker
from xic_extractor.execution.workers import WorkerAdmissionPool
from xic_extractor.extraction.untargeted_partition.cid_nl_acquisition import (
    _PartitionWorkObserver,
    _supports_direct_precursor_assessment,
)
from xic_extractor.extraction.untargeted_partition.models import (
    _RUNTIME_COMPATIBILITY_TOKEN,
    RUNTIME_CALL_COST_METRIC_NAMES,
    UntargetedInitialSampleResult,
    UntargetedPartitionError,
    UntargetedPartitionJob,
    UntargetedPartitionResult,
    UntargetedPreparedGrouping,
    UntargetedSampleResult,
    UntargetedSampleResultRef,
    _canonical_acquisition_content,
    _mint_sample_result_ref,
    _request_subject_candidates,
    _request_uses_subject_batch,
    _SampleResultContext,
)
from xic_extractor.extraction.untargeted_partition.workers import (
    _backfill_support_context,
    _BatchSamplePhaseState,
    _execute_backfill,
    _execute_backfill_batch,
    _execute_initial_assessment,
    _execute_initial_assessment_batch,
    _execute_sample_backfill_worker,
    _execute_sample_initial_worker,
    _prepare_group_authorized_backfills,
    _prepare_owner_authorized_backfills,
    _RuntimeJobState,
    _SampleBackfillWorkerRequest,
    _SampleBackfillWorkerResponse,
    _SampleInitialWorkerRequest,
    _SampleInitialWorkerResponse,
)
from xic_extractor.processing_identity import (
    canonical_current_attempt_artifact_closure,
    resolve_request_acquisition_source,
)
from xic_extractor.raw_reader import _ensure_assemblies_loaded
from xic_extractor.validation_primitives import require_owner_normalized_text


@dataclass(frozen=True, slots=True)
class UntargetedAttemptResult:
    finalization: RuntimeFinalizationReceipt
    sample_results: tuple[UntargetedSampleResult, ...]
    prepared_grouping: UntargetedPreparedGrouping | None = None

    @property
    def succeeded(self) -> bool:
        return (
            self.finalization.supervisor.terminal_outcome
            is AttemptTerminalOutcome.SUCCEEDED
        )


def runtime_call_cost(
    finalization: RuntimeFinalizationReceipt,
) -> dict[str, int]:
    """Project the five real-data runtime call-cost facts from its ledger."""

    if not isinstance(finalization, RuntimeFinalizationReceipt):
        raise UntargetedPartitionError(
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
            raise UntargetedPartitionError(
                f"runtime owner did not provide measured integral {name}"
            )
        projected[name] = int(matches[0].value)
    return projected


def execute_attempt(
    runtime: AttemptRuntime,
    jobs: tuple[UntargetedPartitionJob, ...],
    *,
    telemetry_provider: RuntimeTelemetryProvider,
    recovery_marker: OwnerPathRuntimeMarker,
    progress_callback: Callable[[Mapping[str, object]], None] | None = None,
    alignment_config: AlignmentConfig = CID_NL_ALIGNMENT_CONFIG,
    prepare_grouping: Callable[
        [tuple[UntargetedInitialSampleResult, ...]],
        UntargetedPreparedGrouping,
    ]
    | None = None,
) -> UntargetedAttemptResult:
    """Execute every request sample through one attempt and one finalization."""

    if not isinstance(runtime, AttemptRuntime):
        raise UntargetedPartitionError("runtime must be an AttemptRuntime")
    if not isinstance(alignment_config, AlignmentConfig):
        raise UntargetedPartitionError(
            "alignment_config must be an AlignmentConfig"
        )
    if not jobs or any(
        not isinstance(job, UntargetedPartitionJob) for job in jobs
    ):
        raise UntargetedPartitionError(
            "jobs must contain Untargeted CID-NL runtime jobs"
        )
    if not isinstance(recovery_marker, OwnerPathRuntimeMarker):
        raise UntargetedPartitionError(
            "recovery_marker must be OwnerPathRuntimeMarker"
        )
    attempt = jobs[0].processing_owners.attempt
    if any(job.processing_owners.attempt != attempt for job in jobs):
        raise UntargetedPartitionError(
            "all runtime jobs must share one processing attempt"
        )
    if runtime.manifest.attempt != attempt:
        raise UntargetedPartitionError(
            "jobs must match the runtime manifest attempt"
        )
    keys = tuple(job.work_unit_key for job in jobs)
    if len(keys) != len(set(keys)) or (
        not runtime.manifest_preflight_failed
        and runtime.manifest.work_unit_keys != keys
    ):
        raise UntargetedPartitionError(
            "runtime manifest must exactly match the declared real-data work units"
        )

    work = _prepare_runtime_work(
        jobs,
        alignment_config=alignment_config,
        progress_callback=progress_callback,
        prepare_grouping=prepare_grouping,
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
    return UntargetedAttemptResult(
        finalization=finalization,
        sample_results=(tuple(work.results) if succeeded else ()),
        prepared_grouping=work.prepared_grouping,
    )


def execute_partition(
    jobs: tuple[UntargetedPartitionJob, ...],
    *,
    partition_key: str,
    alignment_config: AlignmentConfig,
    progress_callback: Callable[[Mapping[str, object]], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    raw_workers: int = DEFAULT_ALIGNMENT_WORKERS,
    raw_worker_backend: str = "thread",
    pool: WorkerAdmissionPool | None = None,
    backfill_templates: tuple[UntargetedPartitionJob, ...] = (),
    prepare_grouping: Callable[
        [tuple[UntargetedInitialSampleResult, ...]],
        UntargetedPreparedGrouping,
    ]
    | None = None,
) -> UntargetedPartitionResult:
    """Execute one complete subject partition under the caller's runtime attempt."""
    _required_text(partition_key, field="partition_key")
    if pool is not None:
        raw_workers = pool.max_workers
        raw_worker_backend = pool.backend
    else:
        if type(raw_workers) is not int or raw_workers < 1:
            raise UntargetedPartitionError(
                "raw_workers must be a positive integer"
            )
        if raw_worker_backend not in {"process", "thread"}:
            raise UntargetedPartitionError(
                "raw_worker_backend must be 'process' or 'thread'"
            )
    if not isinstance(alignment_config, AlignmentConfig):
        raise UntargetedPartitionError(
            "alignment_config must be an AlignmentConfig"
        )
    if (
        not isinstance(jobs, tuple)
        or not jobs
        or any(not isinstance(job, UntargetedPartitionJob) for job in jobs)
    ):
        raise UntargetedPartitionError(
            "jobs must contain Untargeted CID-NL runtime jobs"
        )
    if any(
        job.processing_owners.attempt != jobs[0].processing_owners.attempt
        for job in jobs
    ):
        raise UntargetedPartitionError(
            "all runtime jobs must share one processing attempt"
        )
    if len({job.work_unit_key for job in jobs}) != len(jobs):
        raise UntargetedPartitionError(
            "partition work unit keys must be unique"
        )
    work = _prepare_runtime_work(
        jobs,
        alignment_config=alignment_config,
        progress_callback=progress_callback,
        partition_key=partition_key,
        should_cancel=should_cancel,
        raw_workers=raw_workers,
        raw_worker_backend=raw_worker_backend,
        pool=pool,
        backfill_templates=backfill_templates,
        prepare_grouping=prepare_grouping,
    )
    batch = work.provide_worker_batch(None)
    return UntargetedPartitionResult(
        tuple(work.results),
        batch,
        batch.acquisitions,
        work.prepared_grouping,
    )


@dataclass(slots=True)
class _RuntimeWork:
    reservations: list[RuntimeResourceReservation]
    provide_worker_batch: Callable[[object], RuntimeWorkerBatchReceipt]
    results: list[UntargetedSampleResult]
    acquisitions: list[TraceAcquisitionBatch]
    prepared_grouping: UntargetedPreparedGrouping | None = None


def _prepare_runtime_work(
    jobs: tuple[UntargetedPartitionJob, ...],
    *,
    alignment_config: AlignmentConfig,
    progress_callback: Callable[[Mapping[str, object]], None] | None,
    partition_key: str | None = None,
    should_cancel: Callable[[], bool] | None = None,
    raw_workers: int = DEFAULT_ALIGNMENT_WORKERS,
    raw_worker_backend: str = "thread",
    pool: WorkerAdmissionPool | None = None,
    backfill_templates: tuple[UntargetedPartitionJob, ...] = (),
    prepare_grouping: Callable[
        [tuple[UntargetedInitialSampleResult, ...]],
        UntargetedPreparedGrouping,
    ]
    | None = None,
) -> _RuntimeWork:
    if pool is not None:
        raw_workers = pool.max_workers
        raw_worker_backend = pool.backend
    elif raw_worker_backend not in {"process", "thread"}:
        raise UntargetedPartitionError(
            "raw_worker_backend must be 'process' or 'thread'"
        )
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
                if prepare_grouping is None
                else False
            )
            initial_raw_by_key[job.work_unit_key] = bool(bounded)
        except Exception as exc:
            direct_assessment_by_key[job.work_unit_key] = False
            initial_raw_by_key[job.work_unit_key] = False
            planning_errors[job.work_unit_key] = exc
    eligible_by_subject: dict[str, list[UntargetedPartitionJob]] = {}
    if prepare_grouping is None:
        for job in jobs:
            if (
                not job.sample.metadata.excluded
                and job.sample.metadata.sample_role in ACTIVATION_ELIGIBLE_SAMPLE_ROLES
            ):
                eligible_by_subject.setdefault(
                    job.method_subject.method_subject_id,
                    [],
                ).append(job)
    backfill_subject_ids = {
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
            backfill_may_need_raw = (
                error is None
                and (
                    prepare_grouping is not None
                    or job.method_subject.method_subject_id in backfill_subject_ids
                )
                and not initial_requires_raw
                and not job.sample.metadata.excluded
                and job.sample.metadata.sample_role in ACTIVATION_ELIGIBLE_SAMPLE_ROLES
            )
            if (
                partition_key is None
                and not _request_uses_subject_batch(job)
                and (initial_requires_raw or backfill_may_need_raw)
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
                        owner_conditional=(
                            not initial_requires_raw
                            or (
                                job.acquisition_cache_dir is not None
                                and job.acquisition_source_expectation is not None
                            )
                        ),
                    )
                )
        except Exception as exc:
            error = exc
        states.append(
            _RuntimeJobState(
                job=job,
                resource_key=resource_key,
                initial_requires_raw=initial_requires_raw,
                observer=_PartitionWorkObserver(job.processing_owners),
                error=error,
            )
        )

    sample_groups = _runtime_sample_groups(states)
    initial_groups_by_sample = {
        group[0].job.sample.identity.sample_identity_id: group
        for group in sample_groups
    }
    phase_templates = backfill_templates or tuple(
        group[0].job for group in sample_groups
    )
    batch_phases: list[_BatchSamplePhaseState] = []
    for first in phase_templates:
        sample_states = initial_groups_by_sample.get(
            first.sample.identity.sample_identity_id, ()
        )
        if partition_key is None and not _request_uses_subject_batch(first):
            continue
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
        backfill_resource_key = (
            f"acquisition-session:backfill:{sample_id}"
            if not first.sample.metadata.excluded
            and first.sample.metadata.sample_role in ACTIVATION_ELIGIBLE_SAMPLE_ROLES
            and (prepare_grouping is not None or backfill_subject_ids)
            else None
        )
        if partition_key is not None:
            initial_resource_key = (
                f"acquisition-session:{partition_key}:{sample_id}:initial"
            )
            backfill_resource_key = (
                f"acquisition-session:{partition_key}:{sample_id}:backfill"
            )
        for resource_key, phase, owner_conditional in (
            (initial_resource_key, "initial", False),
            (backfill_resource_key, "backfill", True),
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
                    owner_conditional=(
                        owner_conditional
                        or partition_key is not None
                        or (
                            first.acquisition_cache_dir is not None
                            and first.acquisition_source_expectation is not None
                        )
                    ),
                )
            )
        batch_phases.append(
            _BatchSamplePhaseState(
                states=sample_states,
                initial_resource_key=initial_resource_key,
                backfill_resource_key=backfill_resource_key,
                sample_template=first,
            )
        )

    observed_results: list[UntargetedSampleResult] = []
    observed_acquisitions: list[TraceAcquisitionBatch] = []

    def provide_worker_batch(_aggregate: object) -> RuntimeWorkerBatchReceipt:
        settlements: list[WorkUnitSettlement] = []
        resource_settlements: list[RuntimeResourceSettlementReceipt] = []
        artifact_registrations: list[RuntimeArtifactRegistrationReceipt] = []
        acquisitions: list[RuntimeAcquisitionReceipt] = []
        worker_pool = pool or WorkerAdmissionPool(
            max_workers=raw_workers,
            backend=raw_worker_backend,
            thread_name_prefix="cid-nl-sample",
            initializer=lambda: _ensure_assemblies_loaded(
                jobs[0].dll_dir, importlib.import_module
            ),
        )
        if worker_pool.initializer is None:
            worker_pool.set_initializer(
                lambda: _ensure_assemblies_loaded(
                    jobs[0].dll_dir, importlib.import_module
                )
            )
        stop_applied = False

        def stop(error: Exception) -> None:
            worker_pool.stop(error)

        def observe_cancellation() -> bool:
            return worker_pool.observe_cancellation(should_cancel)

        def apply_stop() -> None:
            nonlocal stop_applied
            if worker_pool.stopped and not stop_applied:
                stop_applied = True
                for state in states:
                    if state.error is None:
                        state.error = worker_pool.first_error

        phases_by_sample = {
            phase.job.sample.identity.sample_identity_id: phase
            for phase in batch_phases
        }

        def initial(sample_states: tuple[_RuntimeJobState, ...]) -> None:
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

        def backfill(sample_states: tuple[_RuntimeJobState, ...]) -> None:
            for state in sample_states:
                if (
                    partition_key is None
                    and not _request_uses_subject_batch(state.job)
                    and state.backfill_targets
                ):
                    _execute_backfill(state)
            phase = phases_by_sample.get(
                sample_states[0].job.sample.identity.sample_identity_id
            )
            if phase is not None:
                _execute_backfill_batch(phase)

        def on_sample_error(
            sample_states: tuple[_RuntimeJobState, ...], exc: Exception
        ) -> None:
            for state in sample_states:
                if state.error is None:
                    state.error = exc
            if partition_key is not None:
                worker_pool.stop(exc)

        def on_sample_thread_completed(
            stage: str,
            comp: int,
            tot: int,
            sample_states: tuple[_RuntimeJobState, ...],
            _res: object,
        ) -> None:
            if partition_key is not None:
                err = next(
                    (s.error for s in sample_states if s.error is not None),
                    None,
                )
                if err is not None:
                    worker_pool.stop(err)
            report_phase(stage, comp, tot)

        def make_process_initial_req(
            sample_states: tuple[_RuntimeJobState, ...],
        ) -> _SampleInitialWorkerRequest:
            sample_id = sample_states[0].job.sample.identity.sample_identity_id
            phase = phases_by_sample[sample_id]
            return _SampleInitialWorkerRequest(
                jobs=tuple(s.job for s in sample_states),
                initial_resource_key=phase.initial_resource_key,
                partition_candidates=partition_key is not None,
            )

        def handle_process_initial_resp(
            comp: int,
            tot: int,
            sample_states: tuple[_RuntimeJobState, ...],
            resp: _SampleInitialWorkerResponse,
        ) -> None:
            sample_id = sample_states[0].job.sample.identity.sample_identity_id
            phase = phases_by_sample[sample_id]
            phase.initial_open_attempted = resp.initial_open_attempted
            phase.initial_opened = resp.initial_opened
            phase.initial_cleanup_outcome = resp.initial_cleanup_outcome
            phase.initial_acquisition = resp.initial_acquisition
            for state, adapter_res, obs_artifacts, has_acq, err in zip(
                sample_states,
                resp.adapter_results,
                resp.observer_artifacts,
                resp.observer_has_acquisitions,
                resp.errors,
                strict=True,
            ):
                state.adapter_result = adapter_res
                for art in obs_artifacts:
                    state.observer.record_artifact(art)
                if (
                    has_acq
                    and resp.initial_acquisition is not None
                    and state.observer.initial_acquisition is None
                ):
                    state.observer.record_acquisition(resp.initial_acquisition)
                if err is not None and state.error is None:
                    state.error = err
            if partition_key is not None:
                err = next(
                    (s.error for s in sample_states if s.error is not None),
                    None,
                )
                if err is not None:
                    worker_pool.stop(err)
            report_phase("partition_initial", comp, tot)

        def skip_backfill(sample_states: tuple[_RuntimeJobState, ...]) -> bool:
            return not any(
                state.error is None and state.backfill_targets
                for state in sample_states
            )

        def make_process_backfill_req(
            sample_states: tuple[_RuntimeJobState, ...],
        ) -> _SampleBackfillWorkerRequest:
            sample_id = sample_states[0].job.sample.identity.sample_identity_id
            phase = phases_by_sample[sample_id]
            pending = tuple(
                state
                for state in sample_states
                if state.error is None and state.backfill_targets
            )
            return _SampleBackfillWorkerRequest(
                jobs=tuple(s.job for s in pending),
                backfill_targets_by_key={
                    s.job.work_unit_key: s.backfill_targets
                    for s in pending
                    if s.backfill_targets
                },
                backfill_inputs_by_key={
                    s.job.work_unit_key: s.backfill_input
                    for s in pending
                    if s.backfill_input is not None
                },
                backfill_support_by_key={
                    s.job.work_unit_key: _backfill_support_context(s.adapter_result)
                    for s in pending
                    if s.adapter_result is not None
                },
                backfill_resource_key=phase.backfill_resource_key,
            )

        def handle_process_backfill_resp(
            comp: int,
            tot: int,
            sample_states: tuple[_RuntimeJobState, ...],
            resp: _SampleBackfillWorkerResponse | None,
        ) -> None:
            if resp is not None:
                sample_id = sample_states[0].job.sample.identity.sample_identity_id
                phase = phases_by_sample[sample_id]
                phase.backfill_open_attempted = resp.backfill_open_attempted
                phase.backfill_opened = resp.backfill_opened
                phase.backfill_cleanup_outcome = resp.backfill_cleanup_outcome
                phase.backfill_acquisition = resp.backfill_acquisition
                states_by_key = {
                    state.job.work_unit_key: state for state in sample_states
                }
                for state, obs_artifacts, has_acq, err in zip(
                    (states_by_key[key] for key in resp.work_unit_keys),
                    resp.observer_artifacts,
                    resp.observer_has_acquisitions,
                    resp.errors,
                    strict=True,
                ):
                    key = state.job.work_unit_key
                    if key in resp.backfill_attempts_by_key:
                        state.backfill_attempts = resp.backfill_attempts_by_key[key]
                    if key in resp.backfill_inputs_by_key:
                        state.backfill_input = resp.backfill_inputs_by_key[key]
                    for art in obs_artifacts:
                        state.observer.record_artifact(art)
                    if (
                        has_acq
                        and resp.backfill_acquisition is not None
                        and state.observer.backfill_acquisition is None
                    ):
                        state.observer.record_acquisition(
                            resp.backfill_acquisition, backfill=True
                        )
                    if err is not None and state.error is None:
                        state.error = err
                if partition_key is not None:
                    err = next(
                        (s.error for s in sample_states if s.error is not None),
                        None,
                    )
                    if err is not None:
                        worker_pool.stop(err)
            report_phase("partition_backfill", comp, tot)

        try:
            if not observe_cancellation():
                if raw_worker_backend == "process":
                    worker_pool.run_windowed(
                        sample_groups,
                        _execute_sample_initial_worker,
                        task_arg_fn=make_process_initial_req,
                        on_completed=handle_process_initial_resp,
                        on_error=on_sample_error,
                        should_cancel=should_cancel,
                        stop_on_error=partition_key is not None,
                    )
                else:
                    worker_pool.run_windowed(
                        sample_groups,
                        initial,
                        on_completed=lambda c, t, s, r: on_sample_thread_completed(
                            "partition_initial", c, t, s, r
                        ),
                        on_error=on_sample_error,
                        should_cancel=should_cancel,
                        stop_on_error=partition_key is not None,
                    )
            if not observe_cancellation():
                if prepare_grouping is not None:
                    if all(
                        state.error is None and state.adapter_result is not None
                        for state in states
                    ):
                        work.prepared_grouping = prepare_grouping(
                            tuple(
                                UntargetedInitialSampleResult(
                                    state.job, state.adapter_result
                                )
                                for state in states
                                if state.adapter_result is not None
                            )
                        )
                        _prepare_group_authorized_backfills(
                            states,
                            prepared=work.prepared_grouping,
                            sample_templates=backfill_templates,
                        )
                else:
                    _prepare_owner_authorized_backfills(
                        states,
                        alignment_config=alignment_config,
                        sample_templates=backfill_templates,
                    )
                backfill_groups = _runtime_sample_groups(states)
                for sample_states in backfill_groups:
                    phase = phases_by_sample.get(
                        sample_states[0].job.sample.identity.sample_identity_id
                    )
                    if phase is not None:
                        phase.states = sample_states
                if raw_worker_backend == "process":
                    worker_pool.run_windowed(
                        backfill_groups,
                        _execute_sample_backfill_worker,
                        task_arg_fn=make_process_backfill_req,
                        skip_fn=skip_backfill,
                        on_completed=handle_process_backfill_resp,
                        on_error=on_sample_error,
                        should_cancel=should_cancel,
                        stop_on_error=partition_key is not None,
                    )
                else:
                    worker_pool.run_windowed(
                        backfill_groups,
                        backfill,
                        on_completed=lambda c, t, s, r: on_sample_thread_completed(
                            "partition_backfill", c, t, s, r
                        ),
                        on_error=on_sample_error,
                        should_cancel=should_cancel,
                        stop_on_error=partition_key is not None,
                    )
        except Exception as exc:
            if partition_key is None:
                raise
            stop(exc)
        finally:
            if pool is None:
                worker_pool.shutdown()
        apply_stop()

        result_context = _SampleResultContext(tuple(state.job for state in states))
        for completed, state in enumerate(states, 1):
            observe_cancellation()
            apply_stop()
            batch, sample_result, acquisition = _finalize_job_state(
                state,
                include_acquisition_receipt=(
                    partition_key is None and not _request_uses_subject_batch(state.job)
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
                        "backfill_attempted": bool(state.backfill_attempts),
                    }
                )
        for phase in batch_phases:
            resource_settlements.extend(_batch_phase_resource_receipts(phase))
            if partition_key is None:
                phase_acquisitions = _batch_phase_acquisition_receipts(phase)
                acquisitions.extend(phase_acquisitions)
                observed_acquisitions.extend(
                    acquisition
                    for acquisition in (
                        phase.initial_acquisition,
                        phase.backfill_acquisition,
                    )
                    if acquisition is not None
                )
            else:
                sample_id = phase.job.sample.identity.sample_identity_id
                for name, acquisition in (
                    ("initial", phase.initial_acquisition),
                    ("backfill", phase.backfill_acquisition),
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

    work = _RuntimeWork(
        reservations, provide_worker_batch, observed_results, observed_acquisitions
    )
    return work


def _batch_phase_resource_receipts(
    phase: _BatchSamplePhaseState,
) -> tuple[RuntimeResourceSettlementReceipt, ...]:
    attempt = phase.job.processing_owners.attempt
    receipts: list[RuntimeResourceSettlementReceipt] = []
    for resource_key, attempted, opened, cleanup in (
        (
            phase.initial_resource_key,
            phase.initial_open_attempted,
            phase.initial_opened,
            phase.initial_cleanup_outcome,
        ),
        (
            phase.backfill_resource_key,
            phase.backfill_open_attempted,
            phase.backfill_opened,
            phase.backfill_cleanup_outcome,
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


def _batch_phase_acquisition_receipts(
    phase: _BatchSamplePhaseState,
) -> tuple[RuntimeAcquisitionReceipt, ...]:
    acquisitions = tuple(
        acquisition
        for acquisition in (
            phase.initial_acquisition,
            phase.backfill_acquisition,
        )
        if acquisition is not None
    )
    owner_key = phase.job.work_unit_key
    return tuple(
        RuntimeAcquisitionReceipt(
            work_unit_key=owner_key,
            acquisition=acquisition,
        )
        for acquisition in acquisitions
    )


def _finalize_job_state(
    state: _RuntimeJobState,
    *,
    include_acquisition_receipt: bool = True,
    result_context: _SampleResultContext | None = None,
) -> tuple[
    RuntimeWorkerBatchReceipt,
    UntargetedSampleResult | None,
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

    result_ref: UntargetedSampleResultRef | None = None
    registered_artifacts = observed_artifacts
    if adapter_result is not None:
        try:
            # Backfill may reuse exact measured evidence from the initial result.
            # Equal artifacts are one lineage node; conflicting identities still fail.
            expected_lineage = canonical_current_attempt_artifact_closure(
                producer_attempt=job.processing_owners.attempt,
                artifacts=tuple(
                    dict.fromkeys(
                        (
                            *adapter_result.lineage_artifacts,
                            *(
                                artifact
                                for backfill in state.backfill_attempts
                                for artifact in backfill.lineage_artifacts
                            ),
                        )
                    )
                ),
            )
            if observed_artifacts != expected_lineage:
                raise UntargetedPartitionError(
                    "worker observer does not close the exact adapter/backfill result"
                )
            result_ref = _mint_sample_result_ref(
                job=job,
                result=adapter_result,
                backfill_input=state.backfill_input,
                backfill_attempt=None,
                backfill_attempts=state.backfill_attempts,
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
        state.error = UntargetedPartitionError(
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
        UntargetedSampleResult(
            job=job,
            adapter_result=adapter_result,
            result_ref=result_ref,
            backfill_input=state.backfill_input,
            backfill_attempts=state.backfill_attempts,
        )
        if success and adapter_result is not None and result_ref is not None
        else None
    )
    return (
        batch,
        sample_result,
        acquisition if include_acquisition_receipt else None,
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
            raise UntargetedPartitionError(
                "sample-local runtime jobs crossed their shared acquisition inputs"
            )
    return groups


def _failure_detail(
    job: UntargetedPartitionJob,
    error: Exception | None,
) -> WorkUnitFailureDetail:
    observed = error or UntargetedPartitionError("unknown worker failure")
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
        raise UntargetedPartitionError(
            "runtime acquisition metrics do not match observed RAW work"
        ) from exc


def _required_text(value: object, *, field: str) -> str:
    return require_owner_normalized_text(
        value,
        field=field,
        error_factory=UntargetedPartitionError,
    )



__all__ = (
    "UntargetedAttemptResult",
    "execute_attempt",
    "execute_partition",
    "runtime_call_cost",
)
