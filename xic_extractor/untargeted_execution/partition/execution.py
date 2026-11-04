"""Execute Untargeted partition acquisition and assessment."""

from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from xic_extractor.alignment.backfill.conservative import (
    ACTIVATION_ELIGIBLE_SAMPLE_ROLES,
    minimum_direct_support_count,
)
from xic_extractor.alignment.cid_nl_inputs import (
    CID_NL_ALIGNMENT_CONFIG,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.configuration.execution_profile import DEFAULT_ALIGNMENT_WORKERS
from xic_extractor.execution.workers import WorkerAdmissionPool
from xic_extractor.raw_reader import ensure_assemblies_loaded
from xic_extractor.untargeted_execution.partition.cid_nl_acquisition import (
    _has_recorded_precursor_reference,
)
from xic_extractor.untargeted_execution.partition.models import (
    UntargetedInitialSampleResult,
    UntargetedPartitionError,
    UntargetedPartitionJob,
    UntargetedPartitionResult,
    UntargetedPreparedGrouping,
    UntargetedSampleResult,
    _request_subject_candidates,
    _request_uses_subject_batch,
)
from xic_extractor.untargeted_execution.partition.workers import (
    _backfill_support_context,
    _BatchSamplePhaseState,
    _execute_backfill,
    _execute_backfill_sample_phase,
    _execute_initial_assessment,
    _execute_initial_sample_phase,
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
from xic_extractor.validation_primitives import require_owner_normalized_text


def execute_attempt(
    jobs: tuple[UntargetedPartitionJob, ...],
    *,
    progress_callback: Callable[[Mapping[str, object]], None] | None = None,
    alignment_config: AlignmentConfig = CID_NL_ALIGNMENT_CONFIG,
    prepare_grouping: Callable[
        [tuple[UntargetedInitialSampleResult, ...]],
        UntargetedPreparedGrouping,
    ]
    | None = None,
) -> UntargetedPartitionResult:
    """Execute every request sample together; any sample failure fails all."""

    if not isinstance(alignment_config, AlignmentConfig):
        raise UntargetedPartitionError("alignment_config must be an AlignmentConfig")
    if not jobs or any(not isinstance(job, UntargetedPartitionJob) for job in jobs):
        raise UntargetedPartitionError(
            "jobs must contain Untargeted CID-NL runtime jobs"
        )
    attempt = jobs[0].processing_owners.attempt
    if any(job.processing_owners.attempt != attempt for job in jobs):
        raise UntargetedPartitionError(
            "all runtime jobs must share one processing attempt"
        )
    keys = tuple(job.work_unit_key for job in jobs)
    if len(keys) != len(set(keys)):
        raise UntargetedPartitionError("runtime work unit keys must be unique")

    work = _prepare_runtime_work(
        jobs,
        alignment_config=alignment_config,
        progress_callback=progress_callback,
        prepare_grouping=prepare_grouping,
    )
    failure = work.run()
    return UntargetedPartitionResult(
        sample_results=(tuple(work.results) if failure is None else ()),
        failure=failure,
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
    initial_only: bool = False,
    initial_results: tuple[UntargetedInitialSampleResult, ...] | None = None,
    prepare_grouping: Callable[
        [tuple[UntargetedInitialSampleResult, ...]],
        UntargetedPreparedGrouping,
    ]
    | None = None,
) -> UntargetedPartitionResult:
    """Execute a partition, optionally split at the batch-calibration seam.

    The initial phase owns acquisition; a resumed phase consumes its sealed
    assessments and performs only Backfill acquisition under the same attempt.
    """
    _required_text(partition_key, field="partition_key")
    if pool is not None:
        raw_workers = pool.max_workers
        raw_worker_backend = pool.backend
    else:
        if type(raw_workers) is not int or raw_workers < 1:
            raise UntargetedPartitionError("raw_workers must be a positive integer")
        if raw_worker_backend not in {"process", "thread"}:
            raise UntargetedPartitionError(
                "raw_worker_backend must be 'process' or 'thread'"
            )
    if not isinstance(alignment_config, AlignmentConfig):
        raise UntargetedPartitionError("alignment_config must be an AlignmentConfig")
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
        raise UntargetedPartitionError("partition work unit keys must be unique")
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
        initial_only=initial_only,
        initial_results=initial_results,
    )
    failure = work.run()
    return UntargetedPartitionResult(
        tuple(work.results),
        failure,
        work.prepared_grouping,
    )


@dataclass(slots=True)
class _RuntimeWork:
    run: Callable[[], Exception | None]
    results: list[UntargetedSampleResult]
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
    initial_only: bool = False,
    initial_results: tuple[UntargetedInitialSampleResult, ...] | None = None,
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
                _has_recorded_precursor_reference(bounded)
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
        except Exception as exc:
            error = exc
        states.append(
            _RuntimeJobState(
                job=job,
                resource_key=resource_key,
                initial_requires_raw=initial_requires_raw,
                error=error,
            )
        )

    if initial_results is not None:
        seeded = {
            result.job.work_unit_key: result.adapter_result
            for result in initial_results
        }
        for state in states:
            state.adapter_result = seeded[state.job.work_unit_key]

    sample_groups = _runtime_sample_groups(states)
    initial_groups_by_sample = {
        group[0].job.sample.identity.sample_key: group for group in sample_groups
    }
    phase_templates = backfill_templates or tuple(
        group[0].job for group in sample_groups
    )
    batch_phases: list[_BatchSamplePhaseState] = []
    for first in phase_templates:
        sample_states = initial_groups_by_sample.get(
            first.sample.identity.sample_key, ()
        )
        if partition_key is None and not _request_uses_subject_batch(first):
            continue
        sample_id = first.sample.identity.sample_key
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
        if initial_only:
            backfill_resource_key = None
        if initial_results is not None:
            initial_resource_key = None
        batch_phases.append(
            _BatchSamplePhaseState(
                states=sample_states,
                initial_resource_key=initial_resource_key,
                backfill_resource_key=backfill_resource_key,
                sample_template=first,
            )
        )

    observed_results: list[UntargetedSampleResult] = []

    def run() -> Exception | None:
        failure: Exception | None = None
        cancellation: InterruptedError | None = None
        worker_pool = pool or WorkerAdmissionPool(
            max_workers=raw_workers,
            backend=raw_worker_backend,
            thread_name_prefix="cid-nl-sample",
            initializer=lambda: ensure_assemblies_loaded(
                jobs[0].dll_dir, importlib.import_module
            ),
        )
        if worker_pool.initializer is None:
            worker_pool.set_initializer(
                lambda: ensure_assemblies_loaded(
                    jobs[0].dll_dir, importlib.import_module
                )
            )
        stop_applied = False

        def stop(error: Exception) -> None:
            worker_pool.stop(error)

        cancellation_observed = False

        def observe_cancellation() -> bool:
            nonlocal cancellation_observed
            requested = should_cancel is not None and should_cancel()
            cancellation_observed = cancellation_observed or requested
            return worker_pool.observe_cancellation(lambda: requested)

        def apply_stop() -> None:
            nonlocal stop_applied
            if worker_pool.stopped and not stop_applied:
                stop_applied = True
                for state in states:
                    if state.error is None:
                        state.error = worker_pool.first_error

        phases_by_sample = {
            phase.job.sample.identity.sample_key: phase for phase in batch_phases
        }

        def initial(
            sample_states: tuple[_RuntimeJobState, ...],
        ) -> _SampleInitialWorkerResponse | None:
            if partition_key is not None or _request_uses_subject_batch(
                sample_states[0].job
            ):
                return _execute_initial_sample_phase(
                    phases_by_sample[sample_states[0].job.sample.identity.sample_key],
                    partition_candidates=partition_key is not None,
                )
            else:
                for state in sample_states:
                    _execute_initial_assessment(state)
            return None

        def backfill(
            sample_states: tuple[_RuntimeJobState, ...],
        ) -> _SampleBackfillWorkerResponse | None:
            for state in sample_states:
                if (
                    partition_key is None
                    and not _request_uses_subject_batch(state.job)
                    and state.backfill_targets
                ):
                    _execute_backfill(state)
            phase = phases_by_sample.get(
                sample_states[0].job.sample.identity.sample_key
            )
            if phase is not None:
                return _execute_backfill_sample_phase(phase)
            return None

        def on_sample_error(
            sample_states: tuple[_RuntimeJobState, ...], exc: Exception
        ) -> None:
            for state in sample_states:
                if state.error is None:
                    state.error = exc
            if partition_key is not None:
                worker_pool.stop(exc)

        def on_sample_completed(
            stage: str,
            comp: int,
            tot: int,
            sample_states: tuple[_RuntimeJobState, ...],
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
            sample_id = sample_states[0].job.sample.identity.sample_key
            phase = phases_by_sample[sample_id]
            return _SampleInitialWorkerRequest(
                jobs=tuple(s.job for s in sample_states),
                initial_resource_key=phase.initial_resource_key,
                partition_candidates=partition_key is not None,
            )

        def handle_initial_response(
            comp: int,
            tot: int,
            sample_states: tuple[_RuntimeJobState, ...],
            resp: _SampleInitialWorkerResponse | None,
        ) -> None:
            if resp is not None:
                sample_id = sample_states[0].job.sample.identity.sample_key
                resp.apply_to(phases_by_sample[sample_id])
            on_sample_completed("partition_initial", comp, tot, sample_states)

        def skip_backfill(sample_states: tuple[_RuntimeJobState, ...]) -> bool:
            return not any(
                state.error is None and state.backfill_targets
                for state in sample_states
            )

        def make_process_backfill_req(
            sample_states: tuple[_RuntimeJobState, ...],
        ) -> _SampleBackfillWorkerRequest:
            sample_id = sample_states[0].job.sample.identity.sample_key
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
                backfill_support_by_key={
                    s.job.work_unit_key: _backfill_support_context(s.adapter_result)
                    for s in pending
                    if s.adapter_result is not None
                },
                backfill_resource_key=phase.backfill_resource_key,
            )

        def handle_backfill_response(
            comp: int,
            tot: int,
            sample_states: tuple[_RuntimeJobState, ...],
            resp: _SampleBackfillWorkerResponse | None,
        ) -> None:
            if resp is not None:
                sample_id = sample_states[0].job.sample.identity.sample_key
                resp.apply_to(phases_by_sample[sample_id])
            on_sample_completed("partition_backfill", comp, tot, sample_states)

        try:
            if not observe_cancellation() and initial_results is None:
                if raw_worker_backend == "process":
                    worker_pool.run_windowed(
                        sample_groups,
                        _execute_sample_initial_worker,
                        task_arg_fn=make_process_initial_req,
                        on_completed=handle_initial_response,
                        on_error=on_sample_error,
                        should_cancel=should_cancel,
                        stop_on_error=partition_key is not None,
                    )
                else:
                    worker_pool.run_windowed(
                        sample_groups,
                        initial,
                        on_completed=handle_initial_response,
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
                        if not initial_only and alignment_config.backfill_enabled:
                            _prepare_group_authorized_backfills(
                                states,
                                prepared=work.prepared_grouping,
                                sample_templates=backfill_templates,
                            )
                elif not initial_only and alignment_config.backfill_enabled:
                    _prepare_owner_authorized_backfills(
                        states,
                        alignment_config=alignment_config,
                        sample_templates=backfill_templates,
                    )
                if not initial_only and alignment_config.backfill_enabled:
                    backfill_groups = _runtime_sample_groups(states)
                    for sample_states in backfill_groups:
                        phase = phases_by_sample.get(
                            sample_states[0].job.sample.identity.sample_key
                        )
                        if phase is not None:
                            phase.states = sample_states
                    if raw_worker_backend == "process":
                        worker_pool.run_windowed(
                            backfill_groups,
                            _execute_sample_backfill_worker,
                            task_arg_fn=make_process_backfill_req,
                            skip_fn=skip_backfill,
                            on_completed=handle_backfill_response,
                            on_error=on_sample_error,
                            should_cancel=should_cancel,
                            stop_on_error=partition_key is not None,
                        )
                    else:
                        worker_pool.run_windowed(
                            backfill_groups,
                            backfill,
                            on_completed=handle_backfill_response,
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

        for completed, state in enumerate(states, 1):
            observe_cancellation()
            apply_stop()
            sample_result, sample_failure = _finalize_job_state(
                state,
                cancellation_observed=cancellation_observed,
            )
            if sample_result is not None:
                observed_results.append(sample_result)
            if isinstance(sample_failure, InterruptedError):
                cancellation = cancellation or sample_failure
            elif failure is None:
                failure = sample_failure
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
        # A real failure outranks cancellation observed by other samples.
        return failure or cancellation

    work = _RuntimeWork(run, observed_results)
    return work


def _finalize_job_state(
    state: _RuntimeJobState,
    *,
    cancellation_observed: bool,
) -> tuple[UntargetedSampleResult | None, Exception | None]:
    """Return the sample result, or the failure that withholds it.

    An ``InterruptedError`` remains a cancellation only when cancellation was
    actually requested; otherwise it is an ordinary execution failure.
    """
    adapter_result = state.adapter_result
    if state.error is None and adapter_result is not None:
        return (
            UntargetedSampleResult(
                job=state.job,
                adapter_result=adapter_result,
                backfill_input=state.backfill_input,
                backfill_attempts=state.backfill_attempts,
            ),
            None,
        )
    if state.error is None:
        state.error = UntargetedPartitionError(
            "adapter returned no terminal scientific result"
        )
    error = state.error
    if isinstance(error, InterruptedError) and not cancellation_observed:
        message = " ".join(str(error).split()) or type(error).__name__
        return None, UntargetedPartitionError(f"{type(error).__name__}: {message}")
    return None, error


def _runtime_sample_groups(
    states: list[_RuntimeJobState],
) -> tuple[tuple[_RuntimeJobState, ...], ...]:
    grouped: dict[str, list[_RuntimeJobState]] = {}
    for state in states:
        sample_id = state.job.sample.identity.sample_key
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


def _required_text(value: object, *, field: str) -> str:
    return require_owner_normalized_text(
        value,
        field=field,
        error_factory=UntargetedPartitionError,
    )


__all__ = (
    "execute_attempt",
    "execute_partition",
)
