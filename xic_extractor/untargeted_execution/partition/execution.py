"""Execute Untargeted partition acquisition and assessment."""

from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.configuration.execution_profile import DEFAULT_ALIGNMENT_WORKERS
from xic_extractor.execution.workers import WorkerAdmissionPool
from xic_extractor.raw_reader import ensure_assemblies_loaded
from xic_extractor.untargeted_execution.partition.models import (
    UntargetedInitialSampleResult,
    UntargetedPartitionError,
    UntargetedPartitionJob,
    UntargetedPartitionResult,
    UntargetedPreparedGrouping,
    UntargetedSampleResult,
    _request_subject_candidates,
)
from xic_extractor.untargeted_execution.partition.workers import (
    _backfill_support_context,
    _BatchSamplePhaseState,
    _execute_backfill_sample_phase,
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
    partition_key: str,
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
        if progress_callback is not None:
            progress_callback(
                {
                    "event": "partition_progress",
                    "stage": stage,
                    "partition_key": partition_key,
                    "completed": completed,
                    "total": total,
                }
            )

    states: list[_RuntimeJobState] = []
    for job in jobs:
        try:
            initial_requires_raw = bool(_request_subject_candidates(job))
            error = None
        except Exception as exc:
            initial_requires_raw = False
            error = exc
        states.append(
            _RuntimeJobState(
                job=job,
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
        group[0].job.sample.sample_name: group for group in sample_groups
    }
    phase_templates = backfill_templates or tuple(
        group[0].job for group in sample_groups
    )
    batch_phases: list[_BatchSamplePhaseState] = []
    for first in phase_templates:
        sample_states = initial_groups_by_sample.get(first.sample.sample_name, ())
        sample_id = first.sample.sample_name
        initial_resource_key: str | None = (
            f"acquisition-session:{partition_key}:{sample_id}:initial"
        )
        backfill_resource_key: str | None = (
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
            phase.job.sample.sample_name: phase for phase in batch_phases
        }

        def initial(
            sample_states: tuple[_RuntimeJobState, ...],
        ) -> _SampleInitialWorkerResponse:
            return _execute_initial_sample_phase(
                phases_by_sample[sample_states[0].job.sample.sample_name],
            )

        def backfill(
            sample_states: tuple[_RuntimeJobState, ...],
        ) -> _SampleBackfillWorkerResponse | None:
            phase = phases_by_sample.get(sample_states[0].job.sample.sample_name)
            if phase is not None:
                return _execute_backfill_sample_phase(phase)
            return None

        def on_sample_error(
            sample_states: tuple[_RuntimeJobState, ...], exc: Exception
        ) -> None:
            for state in sample_states:
                if state.error is None:
                    state.error = exc
            worker_pool.stop(exc)

        def on_sample_completed(
            stage: str,
            comp: int,
            tot: int,
            sample_states: tuple[_RuntimeJobState, ...],
        ) -> None:
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
            sample_id = sample_states[0].job.sample.sample_name
            phase = phases_by_sample[sample_id]
            return _SampleInitialWorkerRequest(
                jobs=tuple(s.job for s in sample_states),
                initial_resource_key=phase.initial_resource_key,
            )

        def handle_initial_response(
            comp: int,
            tot: int,
            sample_states: tuple[_RuntimeJobState, ...],
            resp: _SampleInitialWorkerResponse | None,
        ) -> None:
            if resp is not None:
                sample_id = sample_states[0].job.sample.sample_name
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
            sample_id = sample_states[0].job.sample.sample_name
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
                sample_id = sample_states[0].job.sample.sample_name
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
                        stop_on_error=True,
                    )
                else:
                    worker_pool.run_windowed(
                        sample_groups,
                        initial,
                        on_completed=handle_initial_response,
                        on_error=on_sample_error,
                        should_cancel=should_cancel,
                        stop_on_error=True,
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
                            sample_states[0].job.sample.sample_name
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
                            stop_on_error=True,
                        )
                    else:
                        worker_pool.run_windowed(
                            backfill_groups,
                            backfill,
                            on_completed=handle_backfill_response,
                            on_error=on_sample_error,
                            should_cancel=should_cancel,
                            stop_on_error=True,
                        )
        except Exception as exc:
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
        sample_id = state.job.sample.sample_name
        grouped.setdefault(sample_id, []).append(state)
    groups = tuple(tuple(group) for group in grouped.values())
    for group in groups:
        first = group[0].job
        if any(
            state.job.sample != first.sample
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


__all__ = ("execute_partition",)
