from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest

from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.extraction.result_models import FileResult, RawFileExtractionResult


def test_raw_job_runner_reports_progress_and_cancels_pending_futures(
    tmp_path: Path,
) -> None:
    from xic_extractor.extraction.jobs import (
        RawFileJob,
        run_raw_file_jobs,
    )

    config = _config(tmp_path)
    jobs = [
        RawFileJob(index, tmp_path / f"{name}.raw", config, (_target("Analyte"),))
        for index, name in enumerate(["A", "B", "C"], start=1)
    ]
    submitted: list[str] = []
    pending_future = Future()
    progress_calls: list[tuple[int, int, str]] = []
    shutdown_calls = []

    class FakeExecutor:
        def __init__(self, *, max_workers, mp_context) -> None:
            self.max_workers = max_workers

        def shutdown(self, *, wait, cancel_futures=False):
            shutdown_calls.append((wait, cancel_futures))

        def submit(self, worker, job):
            submitted.append(job.raw_path.name)
            future = Future()
            if job.raw_path.name == "A.raw":
                future.set_result(_raw_result(job.raw_index, job.raw_path.stem))
                return future
            return pending_future

    def _executor_factory(*, max_workers, mp_context):
        return FakeExecutor(max_workers=max_workers, mp_context=mp_context)

    run_raw_file_jobs(
        jobs,
        max_workers=2,
        should_stop=lambda: bool(progress_calls),
        progress_callback=lambda current, total, filename: progress_calls.append(
            (current, total, filename)
        ),
        total=len(jobs),
        executor_factory=_executor_factory,
    )

    assert progress_calls == [(1, 3, "A.raw")]
    assert submitted == ["A.raw", "B.raw"]
    assert pending_future.cancelled()
    assert shutdown_calls == [(True, True)]


def test_completed_raw_jobs_do_not_wait_for_idle_worker_teardown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from xic_extractor.extraction import jobs as execution

    closed = Event()
    shutdown_calls = []

    def acquired(job):
        closed.set()
        return _raw_result(job.raw_index, job.raw_path.stem)

    class Executor(ThreadPoolExecutor):
        def __init__(self, *, max_workers, mp_context):
            super().__init__(max_workers=max_workers)

        def shutdown(self, *, wait=True, cancel_futures=False):
            assert closed.is_set()
            shutdown_calls.append((wait, cancel_futures))
            super().shutdown(wait=wait, cancel_futures=cancel_futures)

    monkeypatch.setattr(execution, "extract_raw_file_job", acquired)
    result = execution.run_raw_file_jobs(
        [execution.RawFileJob(1, tmp_path / "A.raw", _config(tmp_path), ())],
        max_workers=1,
        executor_factory=Executor,
        progress_callback=lambda *_: (
            closed.is_set() or pytest.fail("RAW is still open")
        ),
    )
    assert result == [_raw_result(1, "A")]
    assert shutdown_calls == [(False, False)]


def test_raw_process_collector_does_not_schedule_when_already_cancelled(
    tmp_path: Path,
) -> None:
    from xic_extractor.extraction.process_backend import (
        collect_raw_file_results_process,
    )

    config = replace(_config(tmp_path), parallel_mode="process", parallel_workers=2)
    calls = []

    def _runner(jobs, **_kwargs):
        calls.extend(jobs)
        return []

    results = collect_raw_file_results_process(
        config,
        (_target("Analyte"),),
        [tmp_path / "A.raw"],
        should_stop=lambda: True,
        runner=_runner,
    )

    assert results == []
    assert calls == []


def _config(tmp_path: Path) -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=tmp_path / "raw",
        dll_dir=tmp_path / "dll",
        output_csv=tmp_path / "output" / "xic_results.csv",
        diagnostics_csv=tmp_path / "output" / "xic_diagnostics.csv",
        smooth_window=15,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.10,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
    )


def _target(label: str) -> Target:
    return Target(
        label=label,
        mz=258.1085,
        rt_min=8.0,
        rt_max=10.0,
        ppm_tol=20.0,
        neutral_loss_da=None,
        nl_ppm_warn=None,
        nl_ppm_max=None,
        is_istd=False,
        istd_pair="",
    )


def _raw_result(raw_index: int, sample_name: str) -> RawFileExtractionResult:
    return RawFileExtractionResult(
        raw_index=raw_index,
        sample_name=sample_name,
        file_result=FileResult(sample_name=sample_name, results={}),
        diagnostics=[],
    )
