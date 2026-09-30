from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from threading import Event
from time import perf_counter, sleep

from xic_extractor.execution.workers import execute_process_jobs


def test_cancellation_before_dispatch_does_not_create_workers() -> None:
    def forbidden_executor(**kwargs):
        raise AssertionError("cancelled batch must not create an executor")

    report = execute_process_jobs(
        (0, 1, 2),
        worker=lambda job: job,
        error_factory=lambda job, exc: exc,
        is_terminal_failure=lambda result: isinstance(result, Exception),
        max_workers=2,
        executor_factory=forbidden_executor,
        should_cancel=lambda: True,
    )

    assert report.cancellation_requested
    assert report.first_failure is None
    assert report.submitted_job_ordinals == ()
    assert report.unsubmitted_job_ordinals == (0, 1, 2)


def test_cancellation_cancels_queued_jobs_and_drains_running_work() -> None:
    started = Event()
    release = Event()
    closed = Event()
    futures = []

    def worker(job):
        assert job == 0, "queued and unsubmitted jobs must not start"
        started.set()
        try:
            assert release.wait(5), "queued cancellation must release active work"
            return 10
        finally:
            closed.set()

    class Executor:
        def __init__(self, **kwargs):
            self.pool = ThreadPoolExecutor(max_workers=1)

        def submit(self, fn, job):
            future = self.pool.submit(fn, job)
            futures.append(future)
            if job == 0:
                assert started.wait(5)
            else:
                future.add_done_callback(lambda _future: release.set())
            return future

        def shutdown(self, **kwargs):
            self.pool.shutdown(**kwargs)

    report = execute_process_jobs(
        (0, 1, 2, 3),
        worker=worker,
        error_factory=lambda job, exc: exc,
        is_terminal_failure=lambda result: isinstance(result, Exception),
        max_workers=2,
        executor_factory=Executor,
        should_cancel=lambda: len(futures) == 2,
    )

    assert closed.is_set()
    assert report.cancellation_requested
    assert report.first_failure is None
    assert report.completed_results == (10,)
    assert report.completed_job_ordinals == (0,)
    assert report.cancelled_job_ordinals == (1,)
    assert report.in_flight_job_ordinals == ()
    assert report.unsubmitted_job_ordinals == (2, 3)


@dataclass(frozen=True)
class _Job:
    index: int


@dataclass(frozen=True)
class _Failure:
    index: int
    message: str


@dataclass(frozen=True)
class _DeliveryReceipt:
    ordinal: int
    value: int


def _spawn_worker(job: _Job) -> int:
    if job.index == 0:
        raise RuntimeError("owner boom")
    sleep(3.0)
    return job.index


class _ImmediateFailureExecutor:
    def __init__(self, *args, **kwargs) -> None:
        self.futures: list[Future] = []
        self.shutdown_calls: list[tuple[bool, bool]] = []

    def submit(self, worker, job):
        future = Future()
        self.futures.append(future)
        if job.index == 0:
            future.set_result(_Failure(index=0, message="owner boom"))
        return future

    def shutdown(self, *, wait: bool, cancel_futures: bool = False) -> None:
        self.shutdown_calls.append((wait, cancel_futures))


def test_terminal_failure_cancels_pending_and_preserves_census() -> None:
    executor = _ImmediateFailureExecutor()

    report = execute_process_jobs(
        tuple(_Job(index=index) for index in range(5)),
        worker=lambda job: job,
        error_factory=lambda job, exc: _Failure(job.index, str(exc)),
        is_terminal_failure=lambda result: isinstance(result, _Failure),
        max_workers=3,
        executor_factory=lambda **kwargs: executor,
    )

    assert report.first_failure == _Failure(index=0, message="owner boom")
    assert report.submitted_job_ordinals == (0, 1, 2)
    assert report.completed_results == ()
    assert report.completed_job_ordinals == ()
    assert report.failed_job_ordinals == (0,)
    assert report.cancelled_job_ordinals == (1, 2)
    assert report.in_flight_job_ordinals == ()
    assert report.unsubmitted_job_ordinals == (3, 4)
    assert report.observed_results == (report.first_failure,)
    assert executor.shutdown_calls == [(False, True)]


def test_worker_failure_remains_failure_when_cancellation_is_requested() -> None:
    executor = _ImmediateFailureExecutor()
    report = execute_process_jobs(
        tuple(_Job(index=index) for index in range(4)),
        worker=lambda job: job,
        error_factory=lambda job, exc: _Failure(job.index, str(exc)),
        is_terminal_failure=lambda result: isinstance(result, _Failure),
        max_workers=2,
        executor_factory=lambda **kwargs: executor,
        should_cancel=lambda: len(executor.futures) == 2,
    )
    assert report.cancellation_requested
    assert report.first_failure == _Failure(0, "owner boom")
    assert report.failed_job_ordinals == (0,)
    assert report.cancelled_job_ordinals == (1,)
    assert report.unsubmitted_job_ordinals == (2, 3)


def test_cancellation_drains_active_work_even_when_another_job_failed() -> None:
    class Executor:
        def __init__(self):
            self.futures = []

        def submit(self, worker, job):
            future = Future()
            self.futures.append(future)
            if job.index == 0:
                future.set_result(_Failure(0, "owner boom"))
            elif job.index == 1:
                future.set_running_or_notify_cancel()
            return future

        def shutdown(self, *, wait, cancel_futures):
            if wait:
                self.futures[1].set_result(10)

    executor = Executor()
    report = execute_process_jobs(
        tuple(_Job(index=index) for index in range(4)),
        worker=lambda job: job,
        error_factory=lambda job, exc: _Failure(job.index, str(exc)),
        is_terminal_failure=lambda result: isinstance(result, _Failure),
        max_workers=3,
        executor_factory=lambda **kwargs: executor,
        should_cancel=lambda: len(executor.futures) == 3,
    )
    assert report.first_failure == _Failure(0, "owner boom")
    assert report.completed_results == (10,)
    assert report.in_flight_job_ordinals == ()
    assert report.cancelled_job_ordinals == (2,)
    assert report.unsubmitted_job_ordinals == (3,)


def test_stop_during_wait_drains_active_work_when_a_job_fails(monkeypatch) -> None:
    stop = Event()
    futures = []
    shutdown_calls = []

    class Executor:
        def submit(self, worker, job):
            future = Future()
            if job.index < 2:
                future.set_running_or_notify_cancel()
            futures.append(future)
            return future

        def shutdown(self, *, wait, cancel_futures):
            shutdown_calls.append((wait, cancel_futures))
            if wait:
                futures[1].set_result(10)

    def wait_with_stop(pending, **kwargs):
        assert not stop.is_set()
        stop.set()
        futures[0].set_result(_Failure(0, "owner boom"))
        return {futures[0]}, set(pending) - {futures[0]}

    monkeypatch.setattr("xic_extractor.execution.workers.wait", wait_with_stop)
    report = execute_process_jobs(
        tuple(_Job(index=index) for index in range(4)),
        worker=lambda job: job,
        error_factory=lambda job, exc: _Failure(job.index, str(exc)),
        is_terminal_failure=lambda result: isinstance(result, _Failure),
        max_workers=3,
        executor_factory=lambda **kwargs: Executor(),
        should_cancel=stop.is_set,
    )

    assert shutdown_calls == [(True, True)]
    assert futures[1].done()
    assert report.cancellation_requested
    assert report.first_failure == _Failure(0, "owner boom")
    assert report.completed_results == (10,)
    assert report.in_flight_job_ordinals == ()
    assert report.cancelled_job_ordinals == (2,)
    assert report.unsubmitted_job_ordinals == (3,)


def test_success_results_are_returned_in_input_order() -> None:
    class _ReverseCompletionExecutor:
        def __init__(self, *args, **kwargs) -> None:
            self.shutdown_calls: list[tuple[bool, bool]] = []

        def submit(self, worker, job):
            future = Future()
            future.set_result(job.index * 10)
            return future

        def shutdown(self, *, wait: bool, cancel_futures: bool = False) -> None:
            self.shutdown_calls.append((wait, cancel_futures))

    executor = _ReverseCompletionExecutor()
    report = execute_process_jobs(
        (_Job(0), _Job(1), _Job(2)),
        worker=lambda job: job.index * 10,
        error_factory=lambda job, exc: _Failure(job.index, str(exc)),
        is_terminal_failure=lambda result: isinstance(result, _Failure),
        max_workers=2,
        executor_factory=lambda **kwargs: executor,
    )

    assert report.first_failure is None
    assert report.submitted_job_ordinals == (0, 1, 2)
    assert report.completed_results == (0, 10, 20)
    assert report.completed_job_ordinals == (0, 1, 2)
    assert report.failed_job_ordinals == ()
    assert report.observed_results == (0, 10, 20)
    assert executor.shutdown_calls == [(True, False)]


def test_success_delivery_replaces_payloads_with_input_order_receipts() -> None:
    class _ImmediateExecutor:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def submit(self, worker, job):
            future = Future()
            future.set_result(job.index * 10)
            return future

        def shutdown(self, *, wait: bool, cancel_futures: bool = False) -> None:
            pass

    delivered: list[tuple[int, int]] = []

    def deliver(ordinal: int, result: int) -> _DeliveryReceipt:
        delivered.append((ordinal, result))
        return _DeliveryReceipt(ordinal=ordinal, value=result)

    report = execute_process_jobs(
        (_Job(0), _Job(1), _Job(2)),
        worker=lambda job: job.index * 10,
        error_factory=lambda job, exc: _Failure(job.index, str(exc)),
        is_terminal_failure=lambda result: isinstance(result, _Failure),
        max_workers=2,
        executor_factory=_ImmediateExecutor,
        success_result_delivery=deliver,
    )

    assert delivered == [(0, 0), (1, 10), (2, 20)]
    assert report.observed_results == (
        _DeliveryReceipt(0, 0),
        _DeliveryReceipt(1, 10),
        _DeliveryReceipt(2, 20),
    )
    assert report.completed_results == report.observed_results
    assert report.completed_job_ordinals == (0, 1, 2)


def test_success_delivery_failure_becomes_late_terminal_failure() -> None:
    class _ImmediateExecutor:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def submit(self, worker, job):
            future = Future()
            future.set_result(job.index * 10)
            return future

        def shutdown(self, *, wait: bool, cancel_futures: bool = False) -> None:
            pass

    def deliver(ordinal: int, result: int) -> _DeliveryReceipt:
        if ordinal == 1:
            raise ValueError("transaction rejected")
        return _DeliveryReceipt(ordinal=ordinal, value=result)

    report = execute_process_jobs(
        (_Job(0), _Job(1), _Job(2), _Job(3)),
        worker=lambda job: job.index * 10,
        error_factory=lambda job, exc: _Failure(job.index, str(exc)),
        is_terminal_failure=lambda result: isinstance(result, _Failure),
        max_workers=2,
        executor_factory=_ImmediateExecutor,
        success_result_delivery=deliver,
    )

    assert report.first_failure == _Failure(1, "transaction rejected")
    assert report.observed_results == (
        _DeliveryReceipt(0, 0),
        _Failure(1, "transaction rejected"),
    )
    assert report.completed_job_ordinals == (0,)
    assert report.failed_job_ordinals == (1,)
    assert report.unsubmitted_job_ordinals == (2, 3)


def test_completed_work_is_preserved_when_failure_arrives_in_same_wait_batch() -> None:
    class _MixedExecutor:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def submit(self, worker, job):
            future = Future()
            if job.index == 0:
                future.set_result(_Failure(0, "owner boom"))
            else:
                future.set_result(job.index * 10)
            return future

        def shutdown(self, *, wait: bool, cancel_futures: bool = False) -> None:
            pass

    report = execute_process_jobs(
        (_Job(0), _Job(1), _Job(2)),
        worker=lambda job: job.index * 10,
        error_factory=lambda job, exc: _Failure(job.index, str(exc)),
        is_terminal_failure=lambda result: isinstance(result, _Failure),
        max_workers=3,
        executor_factory=_MixedExecutor,
    )

    assert report.first_failure == _Failure(0, "owner boom")
    assert report.submitted_job_ordinals == (0, 1, 2)
    assert report.completed_results == (10, 20)
    assert report.completed_job_ordinals == (1, 2)
    assert report.failed_job_ordinals == (0,)
    assert report.observed_results == (_Failure(0, "owner boom"), 10, 20)


def test_same_batch_terminal_failures_are_all_preserved() -> None:
    class _MultipleFailureExecutor:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def submit(self, worker, job):
            future = Future()
            if job.index < 2:
                future.set_result(_Failure(job.index, f"owner boom {job.index}"))
            else:
                future.set_result(job.index * 10)
            return future

        def shutdown(self, *, wait: bool, cancel_futures: bool = False) -> None:
            pass

    progress: list[object] = []
    report = execute_process_jobs(
        (_Job(0), _Job(1), _Job(2)),
        worker=lambda job: job.index * 10,
        error_factory=lambda job, exc: _Failure(job.index, str(exc)),
        is_terminal_failure=lambda result: isinstance(result, _Failure),
        max_workers=3,
        executor_factory=_MultipleFailureExecutor,
        progress_callback=progress.append,
    )

    assert report.first_failure == _Failure(0, "owner boom 0")
    assert report.submitted_job_ordinals == (0, 1, 2)
    assert report.observed_results == (
        _Failure(0, "owner boom 0"),
        _Failure(1, "owner boom 1"),
        20,
    )
    assert report.completed_results == (20,)
    assert report.completed_job_ordinals == (2,)
    assert report.failed_job_ordinals == (0, 1)
    assert report.cancelled_job_ordinals == ()
    assert report.in_flight_job_ordinals == ()
    assert report.unsubmitted_job_ordinals == ()
    assert progress == list(report.observed_results)


def test_future_finishing_during_cancellation_is_observed() -> None:
    class _FinishesBeforeCancellationFuture(Future):
        def cancel(self) -> bool:
            self.set_result(10)
            return False

    class _CancellationRaceExecutor:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def submit(self, worker, job):
            if job.index == 0:
                future = Future()
                future.set_result(_Failure(0, "owner boom"))
                return future
            return _FinishesBeforeCancellationFuture()

        def shutdown(self, *, wait: bool, cancel_futures: bool = False) -> None:
            pass

    report = execute_process_jobs(
        (_Job(0), _Job(1), _Job(2)),
        worker=lambda job: job.index * 10,
        error_factory=lambda job, exc: _Failure(job.index, str(exc)),
        is_terminal_failure=lambda result: isinstance(result, _Failure),
        max_workers=2,
        executor_factory=_CancellationRaceExecutor,
    )

    assert report.first_failure == _Failure(0, "owner boom")
    assert report.submitted_job_ordinals == (0, 1)
    assert report.observed_results == (_Failure(0, "owner boom"), 10)
    assert report.completed_results == (10,)
    assert report.completed_job_ordinals == (1,)
    assert report.failed_job_ordinals == (0,)
    assert report.cancelled_job_ordinals == ()
    assert report.in_flight_job_ordinals == ()
    assert report.unsubmitted_job_ordinals == (2,)


def test_spawn_failure_does_not_drain_slow_unsubmitted_jobs() -> None:
    started = perf_counter()
    report = execute_process_jobs(
        (_Job(0), _Job(1), _Job(2)),
        worker=_spawn_worker,
        error_factory=lambda job, exc: _Failure(job.index, str(exc)),
        is_terminal_failure=lambda result: isinstance(result, _Failure),
        max_workers=1,
    )
    elapsed = perf_counter() - started

    assert report.first_failure == _Failure(0, "owner boom")
    assert report.submitted_job_ordinals == (0,)
    assert report.failed_job_ordinals == (0,)
    assert report.unsubmitted_job_ordinals == (1, 2)
    assert elapsed < 2.0
