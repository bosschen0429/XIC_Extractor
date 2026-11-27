"""Execute Untargeted partition acquisition and assessment."""

from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from xic_extractor.alignment.backfill.cid_nl_planning import CidNlBackfillQuestion
from xic_extractor.alignment.backfill.conservative import (
    is_backfill_eligible_sample,
    minimum_direct_support_count,
)
from xic_extractor.alignment.cid_nl_initial_grouping import CidNlGrouping
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.execution.workers import WorkerAdmissionPool
from xic_extractor.raw_reader import ensure_assemblies_loaded
from xic_extractor.untargeted_execution.partition.cid_nl_region_acquisition import (
    plan_cid_nl_mass_regions,
)
from xic_extractor.untargeted_execution.partition.models import (
    UntargetedInitialSampleResult,
    UntargetedPartitionError,
    UntargetedPartitionJob,
    UntargetedPartitionResult,
    UntargetedSampleResult,
)
from xic_extractor.untargeted_execution.partition.workers import (
    _apply_backfill_questions,
    _BatchSamplePhaseState,
    _execute_backfill_sample_phase,
    _execute_initial_sample_phase,
    _execute_sample_initial_worker,
    _prepare_owner_authorized_backfills,
    _RuntimeJobState,
    _SampleBackfillWorkerResponse,
    _SampleInitialWorkerRequest,
    _SampleInitialWorkerResponse,
    plan_partition_backfills,
)


def execute_partition(
    jobs: tuple[UntargetedPartitionJob, ...],
    *,
    partition_key: str,
    alignment_config: AlignmentConfig,
    progress_callback: Callable[[Mapping[str, object]], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    pool: WorkerAdmissionPool,
    initial_only: bool = False,
    initial_results: tuple[UntargetedInitialSampleResult, ...] | None = None,
    prepare_grouping: Callable[
        [tuple[UntargetedInitialSampleResult, ...]], CidNlGrouping
    ]
    | None = None,
    backfill_questions: tuple[CidNlBackfillQuestion, ...] | None = None,
) -> UntargetedPartitionResult:
    """Execute a partition, optionally split at the batch-calibration seam.

    The initial phase owns acquisition; a resumed phase consumes its sealed
    assessments and performs only Backfill acquisition under the same attempt.
    A resumed phase receives the Backfill questions planned with its final
    grouping; an unsplit run plans them here.
    """
    work = _prepare_runtime_work(
        jobs,
        alignment_config=alignment_config,
        progress_callback=progress_callback,
        partition_key=partition_key,
        should_cancel=should_cancel,
        pool=pool,
        prepare_grouping=prepare_grouping,
        initial_only=initial_only,
        initial_results=initial_results,
        backfill_questions=backfill_questions,
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
    prepared_grouping: CidNlGrouping | None = None


def _prepare_runtime_work(
    jobs: tuple[UntargetedPartitionJob, ...],
    *,
    alignment_config: AlignmentConfig,
    progress_callback: Callable[[Mapping[str, object]], None] | None,
    partition_key: str,
    should_cancel: Callable[[], bool] | None = None,
    pool: WorkerAdmissionPool,
    initial_only: bool = False,
    initial_results: tuple[UntargetedInitialSampleResult, ...] | None = None,
    prepare_grouping: Callable[
        [tuple[UntargetedInitialSampleResult, ...]], CidNlGrouping
    ]
    | None = None,
    backfill_questions: tuple[CidNlBackfillQuestion, ...] | None = None,
) -> _RuntimeWork:

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

    states = [_RuntimeJobState(job) for job in jobs]

    if initial_results is not None:
        seeded = {
            result.job.work_unit_key: result.adapter_result
            for result in initial_results
        }
        for state in states:
            state.adapter_result = seeded[state.job.work_unit_key]

    sample_groups = _runtime_sample_groups(states)
    regions = (
        plan_cid_nl_mass_regions(
            (
                event
                for group in sample_groups
                for candidate in group[0].job.candidates
                for event in candidate.located_events
            ),
            settings=jobs[0].settings,
            peak_events=(
                candidate.located_events
                for group in sample_groups
                for candidate in group[0].job.candidates
            ),
            minimum_event_samples=minimum_direct_support_count(
                sum(
                    is_backfill_eligible_sample(group[0].job.sample)
                    for group in sample_groups
                )
            ),
        )
        if jobs and initial_results is None
        else ()
    )
    batch_phases: list[_BatchSamplePhaseState] = []
    for sample_states in sample_groups:
        first = sample_states[0].job
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
                regions=regions,
            )
        )

    observed_results: list[UntargetedSampleResult] = []

    def run() -> Exception | None:
        failure: Exception | None = None
        cancellation: InterruptedError | None = None
        if pool.initializer is None:
            pool.set_initializer(
                lambda: ensure_assemblies_loaded(
                    jobs[0].dll_dir, importlib.import_module
                )
            )
        stop_applied = False

        def stop(error: Exception) -> None:
            pool.stop(error)

        cancellation_observed = False

        def observe_cancellation() -> bool:
            nonlocal cancellation_observed
            requested = should_cancel is not None and should_cancel()
            cancellation_observed = cancellation_observed or requested
            return pool.observe_cancellation(lambda: requested)

        def apply_stop() -> None:
            nonlocal stop_applied
            if pool.stopped and not stop_applied:
                stop_applied = True
                for state in states:
                    if state.error is None:
                        state.error = pool.first_error

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
            pool.stop(exc)

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
                pool.stop(err)
            report_phase(stage, comp, tot)

        def make_process_initial_req(
            sample_states: tuple[_RuntimeJobState, ...],
        ) -> _SampleInitialWorkerRequest:
            sample_id = sample_states[0].job.sample.sample_name
            phase = phases_by_sample[sample_id]
            return _SampleInitialWorkerRequest(
                jobs=tuple(s.job for s in sample_states),
                initial_resource_key=phase.initial_resource_key,
                regions=phase.regions,
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
                if pool.backend == "process":
                    pool.run_windowed(
                        sample_groups,
                        _execute_sample_initial_worker,
                        task_arg_fn=make_process_initial_req,
                        on_completed=handle_initial_response,
                        on_error=on_sample_error,
                        should_cancel=should_cancel,
                        stop_on_error=True,
                    )
                else:
                    pool.run_windowed(
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
                            _apply_backfill_questions(
                                states,
                                plan_partition_backfills(jobs, work.prepared_grouping)
                                if backfill_questions is None
                                else backfill_questions,
                            )
                elif not initial_only and alignment_config.backfill_enabled:
                    _prepare_owner_authorized_backfills(
                        states,
                        alignment_config=alignment_config,
                    )
                if not initial_only and alignment_config.backfill_enabled:
                    pool.run_windowed(
                        _runtime_sample_groups(states),
                        backfill,
                        on_completed=handle_backfill_response,
                        on_error=on_sample_error,
                        should_cancel=should_cancel,
                        stop_on_error=True,
                    )
        except Exception as exc:
            stop(exc)
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
    return tuple(tuple(group) for group in grouped.values())


__all__ = ("execute_partition",)
