import pickle
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace

import pytest

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.identity_coherence.models import (
    IdentityCoherenceTraceRequest,
    IdentityCoherenceTraceResult,
)
from xic_extractor.alignment.ownership_models import (
    IdentityEvent,
    OwnerAssignment,
    SampleLocalMS1Owner,
)
from xic_extractor.alignment.process_backend import (
    AlignmentProcessExecutionError,
    IdentityTraceSampleJob,
    IdentityTraceSampleResult,
    OwnerBuildSampleJob,
    OwnerBuildSampleResult,
    OwnerBuildTimingStats,
    OwnerBuildWorkerError,
    extract_identity_trace_sample_job,
    run_identity_trace_process,
    run_owner_build_jobs,
    run_owner_build_process,
)
from xic_extractor.config import ExtractionConfig
from xic_extractor.xic_models import XICRequest, XICTrace


def test_owner_build_process_builds_pickleable_sample_jobs_and_merges_output(
    tmp_path: Path,
) -> None:
    owner_a = _owner("sample-a")
    owner_b = _owner("sample-b")
    unresolved_a = OwnerAssignment(
        "sample-a#missing",
        None,
        "unresolved",
        "missing_raw",
    )
    primary_a = OwnerAssignment("sample-a#1", owner_a.owner_id, "primary", "primary")
    primary_b = OwnerAssignment("sample-b#1", owner_b.owner_id, "primary", "primary")
    captured_jobs = []

    def fake_runner(jobs, *, max_workers):
        captured_jobs.extend(jobs)
        for job in jobs:
            pickle.loads(pickle.dumps(job))
        assert max_workers == 2
        return [
            OwnerBuildSampleResult(
                sample_index=2,
                sample_stem="sample-b",
                owners=(owner_b,),
                assignments=(primary_b,),
                ambiguous_records=(),
                timing_stats=(
                    OwnerBuildTimingStats(
                        sample_stem="sample-b",
                        elapsed_sec=2.0,
                        extract_xic_count=3,
                        point_count=30,
                    ),
                ),
            ),
            OwnerBuildSampleResult(
                sample_index=1,
                sample_stem="sample-a",
                owners=(owner_a,),
                assignments=(unresolved_a, primary_a),
                ambiguous_records=(),
                timing_stats=(
                    OwnerBuildTimingStats(
                        sample_stem="sample-a",
                        elapsed_sec=1.0,
                        extract_xic_count=2,
                        point_count=20,
                    ),
                ),
            ),
        ]

    output = run_owner_build_process(
        (_candidate("sample-a"), _candidate("sample-b")),
        sample_order=("sample-a", "sample-b"),
        raw_paths={
            "sample-a": tmp_path / "sample-a.raw",
            "sample-b": tmp_path / "sample-b.raw",
        },
        dll_dir=tmp_path / "dll",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(tmp_path, baseline_audit_method="asls"),
        max_workers=2,
        owner_build_xic_backend="ms1_index",
        emit_region_audit=True,
        region_audit_family_ids=frozenset({"sample-a@F0001"}),
        runner=fake_runner,
    )

    assert [job.sample_stem for job in captured_jobs] == ["sample-a", "sample-b"]
    assert [len(job.candidates) for job in captured_jobs] == [1, 1]
    assert {job.owner_build_xic_backend for job in captured_jobs} == {"ms1_index"}
    assert {job.emit_region_audit for job in captured_jobs} == {True}
    assert {job.region_audit_family_ids for job in captured_jobs} == {
        frozenset({"sample-a@F0001"})
    }
    assert {job.peak_config.baseline_audit_method for job in captured_jobs} == {"asls"}
    assert output.ownership.owners == (owner_a, owner_b)
    assert output.ownership.assignments == (unresolved_a, primary_a, primary_b)
    assert [stat.sample_stem for stat in output.timing_stats] == [
        "sample-a",
        "sample-b",
    ]


def test_owner_build_process_raises_worker_errors(tmp_path: Path) -> None:
    def fake_runner(jobs, *, max_workers):
        return [
            OwnerBuildWorkerError(
                sample_index=1,
                sample_stem="sample-a",
                raw_name="sample-a.raw",
                message="RuntimeError: boom",
            )
        ]

    with pytest.raises(
        AlignmentProcessExecutionError,
        match="sample-a.raw: RuntimeError: boom",
    ):
        run_owner_build_process(
            (_candidate("sample-a"),),
            sample_order=("sample-a",),
            raw_paths={"sample-a": tmp_path / "sample-a.raw"},
            dll_dir=tmp_path / "dll",
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(tmp_path),
            max_workers=2,
            runner=fake_runner,
        )


def test_owner_build_jobs_accepts_empty_job_list() -> None:
    assert run_owner_build_jobs((), max_workers=2) == []


def test_owner_build_process_handles_missing_raw_in_parent_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = _owner("sample-a")
    assignment = OwnerAssignment("sample-a#1", owner.owner_id, "primary", "primary")
    calls = {}
    seen = []

    def fake_build_sample_local_owners(candidates, **kwargs):
        calls["candidates"] = candidates
        calls.update(kwargs)
        return SimpleNamespace(
            owners=(owner,),
            assignments=(assignment,),
            ambiguous_records=(),
        )

    monkeypatch.setattr(
        "xic_extractor.alignment.process_backend.build_sample_local_owners",
        fake_build_sample_local_owners,
    )

    output = run_owner_build_process(
        (_candidate("sample-a"),),
        sample_order=("sample-a",),
        raw_paths={},
        dll_dir=tmp_path / "dll",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(tmp_path),
        max_workers=2,
        progress_callback=seen.append,
    )

    assert tuple(candidate.sample_stem for candidate in calls["candidates"]) == (
        "sample-a",
    )
    assert calls["raw_sources"] == {}
    assert output.ownership.owners == (owner,)
    assert output.ownership.assignments == (assignment,)
    assert output.timing_stats == ()
    assert [result.sample_stem for result in seen] == ["sample-a"]


def test_owner_build_jobs_wrap_executor_exceptions_as_build_errors(
    tmp_path: Path,
) -> None:
    job = OwnerBuildSampleJob(
        sample_index=1,
        sample_stem="sample-a",
        raw_path=tmp_path / "sample-a.raw",
        dll_dir=tmp_path / "dll",
        candidates=(_candidate("sample-a"),),
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(tmp_path),
    )

    results = run_owner_build_jobs(
        (job,),
        max_workers=1,
        executor_factory=_FailingExecutor,
    )

    assert results == [
        OwnerBuildWorkerError(
            sample_index=1,
            sample_stem="sample-a",
            raw_name="sample-a.raw",
            message="RuntimeError: executor boom",
        )
    ]


def test_owner_build_jobs_wrap_submit_exceptions_as_build_errors(
    tmp_path: Path,
) -> None:
    jobs = (
        OwnerBuildSampleJob(
            sample_index=1,
            sample_stem="sample-a",
            raw_path=tmp_path / "sample-a.raw",
            dll_dir=tmp_path / "dll",
            candidates=(_candidate("sample-a"),),
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(tmp_path),
        ),
        OwnerBuildSampleJob(
            sample_index=2,
            sample_stem="sample-b",
            raw_path=tmp_path / "sample-b.raw",
            dll_dir=tmp_path / "dll",
            candidates=(_candidate("sample-b"),),
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(tmp_path),
        ),
    )

    progress = []
    results = run_owner_build_jobs(
        jobs,
        max_workers=2,
        executor_factory=_SubmitFailingExecutor,
        progress_callback=progress.append,
    )

    assert results == [
        OwnerBuildWorkerError(
            sample_index=1,
            sample_stem="sample-a",
            raw_name="sample-a.raw",
            message="RuntimeError: submit boom",
        )
    ]
    assert progress == results


def test_owner_build_jobs_reject_unexpected_cancelled_census(tmp_path: Path) -> None:
    job = OwnerBuildSampleJob(
        sample_index=1,
        sample_stem="sample-a",
        raw_path=tmp_path / "sample-a.raw",
        dll_dir=tmp_path / "dll",
        candidates=(_candidate("sample-a"),),
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(tmp_path),
    )

    with pytest.raises(AlignmentProcessExecutionError, match=r"cancelled=\(0,\)"):
        run_owner_build_jobs(
            (job,),
            max_workers=1,
            executor_factory=_CancelledExecutor,
        )


def test_timed_process_raw_source_records_batch_calls() -> None:
    import xic_extractor.alignment.process_backend as process_module
    from xic_extractor.xic_models import XICRequest, XICTrace

    class BatchSource:
        def __init__(self) -> None:
            self.raw_chromatogram_call_count = 0

        def extract_xic_many(self, requests):
            self.raw_chromatogram_call_count += 1
            return tuple(
                XICTrace.from_arrays([request.rt_min], [request.mz])
                for request in requests
            )

        def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
            self.raw_chromatogram_call_count += 1
            return [rt_min], [mz]

    stats = process_module._TimedProcessStats(sample_stem="Sample_A")
    source = process_module._TimedProcessRawSource(BatchSource(), stats=stats)

    traces = source.extract_xic_many(
        (
            XICRequest(mz=258.0, rt_min=8.0, rt_max=9.0, ppm_tol=20.0),
            XICRequest(mz=259.0, rt_min=8.0, rt_max=9.0, ppm_tol=20.0),
        )
    )

    assert [trace.intensity.tolist() for trace in traces] == [[258.0], [259.0]]
    assert stats.extract_xic_count == 2
    assert stats.extract_xic_batch_count == 1
    assert stats.raw_chromatogram_call_count == 1
    assert stats.point_count == 2


def test_timed_process_raw_source_delegates_scan_window_lookup() -> None:
    import xic_extractor.alignment.process_backend as process_module
    from xic_extractor.xic_models import XICRequest

    class WindowSource:
        def scan_window_for_request(self, request):
            return (int(request.rt_min), int(request.rt_max))

    stats = process_module._TimedProcessStats(sample_stem="Sample_A")
    source = process_module._TimedProcessRawSource(WindowSource(), stats=stats)

    window = source.scan_window_for_request(
        XICRequest(mz=258.0, rt_min=8.0, rt_max=9.0, ppm_tol=20.0)
    )

    assert window == (8, 9)


def test_timed_process_raw_source_delegates_retention_time_lookup() -> None:
    import xic_extractor.alignment.process_backend as process_module

    class WindowSource:
        def retention_time_for_scan(self, scan_number):
            return scan_number / 100.0

    stats = process_module._TimedProcessStats(sample_stem="Sample_A")
    source = process_module._TimedProcessRawSource(WindowSource(), stats=stats)

    assert source.retention_time_for_scan(812) == 8.12


def _candidate(sample_stem: str):
    return SimpleNamespace(
        sample_stem=sample_stem,
        candidate_id=f"{sample_stem}#1",
    )


def _owner(sample_stem: str) -> SampleLocalMS1Owner:
    event = IdentityEvent(
        candidate_id=f"{sample_stem}#1",
        sample_stem=sample_stem,
        raw_file=f"{sample_stem}.raw",
        neutral_loss_tag="NL116",
        precursor_mz=500.0,
        product_mz=383.9526,
        observed_neutral_loss_da=116.0474,
        seed_rt=8.5,
        evidence_score=80,
        seed_event_count=2,
    )
    return SampleLocalMS1Owner(
        owner_id=f"OWN-{sample_stem}-000001",
        sample_stem=sample_stem,
        raw_file=f"{sample_stem}.raw",
        precursor_mz=500.0,
        owner_apex_rt=8.5,
        owner_peak_start_rt=8.4,
        owner_peak_end_rt=8.6,
        owner_area=100.0,
        owner_height=50.0,
        primary_identity_event=event,
        supporting_events=(),
        identity_conflict=False,
        assignment_reason="owner_exact_apex_match",
    )


class _FailingExecutor:
    def __init__(self, *args, **kwargs) -> None:
        self._future: Future = Future()
        self._future.set_exception(RuntimeError("executor boom"))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def submit(self, worker, job):
        return self._future


class _SubmitFailingExecutor:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def submit(self, worker, job):
        raise RuntimeError("submit boom")


class _CancelledExecutor:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def submit(self, worker, job):
        future = Future()
        future.cancel()
        future.set_running_or_notify_cancel()
        return future

    def shutdown(self, *, wait: bool, cancel_futures: bool = False) -> None:
        pass


def _peak_config(
    tmp_path: Path,
    *,
    baseline_audit_method: str = "",
) -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=tmp_path,
        dll_dir=tmp_path / "dll",
        output_csv=tmp_path / "xic_results.csv",
        diagnostics_csv=tmp_path / "xic_diagnostics.csv",
        smooth_window=3,
        smooth_polyorder=1,
        ms1_morphology_smoothing_window_points=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.01,
        ms2_precursor_tol_da=1.6,
        nl_min_intensity_ratio=0.01,
        baseline_audit_method=baseline_audit_method,
    )


def _identity_trace_request(
    sample_id: str,
    candidate_id: str,
) -> IdentityCoherenceTraceRequest:
    return IdentityCoherenceTraceRequest(
        decision_id=f"DEC-{candidate_id}",
        request_id=f"REQ-{candidate_id}",
        sample_id=sample_id,
        candidate_id=candidate_id,
        precursor_mz=500.0,
        ppm_tolerance=20.0,
        rt_min=5.0,
        rt_max=5.1,
    )


def test_run_identity_trace_process_builds_pickleable_jobs_and_groups_by_sample(
    tmp_path: Path,
) -> None:
    requests = (
        _identity_trace_request("sample-a", "A1"),
        _identity_trace_request("sample-b", "B1"),
        _identity_trace_request("sample-a", "A2"),
    )
    captured_jobs = []

    def fake_runner(jobs, *, max_workers):
        captured_jobs.extend(jobs)
        for job in jobs:
            pickle.loads(pickle.dumps(job))
        assert max_workers == 8
        return [
            IdentityTraceSampleResult(
                sample_index=job.sample_index,
                sample_stem=job.sample_stem,
                indexed_results=tuple(
                    (
                        index,
                        IdentityCoherenceTraceResult(
                            request=request,
                            trace=None,
                            status="blocked_infrastructure",
                            blocked_reason="test_only",
                        ),
                    )
                    for index, request in job.requests
                ),
            )
            for job in jobs
        ]

    run_identity_trace_process(
        requests,
        raw_paths={
            "sample-a": tmp_path / "sample-a.raw",
            "sample-b": tmp_path / "sample-b.raw",
        },
        dll_dir=tmp_path / "dll",
        max_workers=8,
        raw_xic_batch_size=64,
        runner=fake_runner,
    )

    assert [job.sample_stem for job in captured_jobs] == ["sample-a", "sample-b"]
    assert [job.raw_xic_batch_size for job in captured_jobs] == [64, 64]
    assert [index for index, _ in captured_jobs[0].requests] == [0, 2]
    assert [index for index, _ in captured_jobs[1].requests] == [1]


def test_run_identity_trace_process_preserves_request_order(tmp_path: Path) -> None:
    requests = (
        _identity_trace_request("sample-a", "A1"),
        _identity_trace_request("sample-b", "B1"),
    )

    def fake_runner(jobs, *, max_workers):
        results = []
        for job in reversed(tuple(jobs)):
            results.append(
                IdentityTraceSampleResult(
                    sample_index=job.sample_index,
                    sample_stem=job.sample_stem,
                    indexed_results=tuple(
                        (
                            index,
                            IdentityCoherenceTraceResult(
                                request=request,
                                trace=None,
                                status="blocked_infrastructure",
                                blocked_reason="test_only",
                            ),
                        )
                        for index, request in job.requests
                    ),
                )
            )
        return results

    output = run_identity_trace_process(
        requests,
        raw_paths={
            "sample-a": tmp_path / "sample-a.raw",
            "sample-b": tmp_path / "sample-b.raw",
        },
        dll_dir=tmp_path / "dll",
        max_workers=8,
        raw_xic_batch_size=64,
        runner=fake_runner,
    )

    assert [result.request.candidate_id for result in output.results] == [
        "A1",
        "B1",
    ]


def test_identity_trace_worker_uses_xic_request_shape(
    monkeypatch,
    tmp_path: Path,
) -> None:
    request = _identity_trace_request("sample-a", "A1")
    seen = {}

    class RawContext:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def extract_xic_many(self, requests):
            seen["requests"] = tuple(requests)
            assert all(isinstance(item, XICRequest) for item in requests)
            return tuple(
                XICTrace.from_arrays([5.0, 5.1], [0.0, 10.0]) for _ in requests
            )

    monkeypatch.setattr(
        "xic_extractor.raw_reader.open_raw",
        lambda raw_path, dll_dir: RawContext(),
    )

    result = extract_identity_trace_sample_job(
        IdentityTraceSampleJob(
            sample_index=1,
            sample_stem="sample-a",
            raw_path=tmp_path / "sample-a.raw",
            dll_dir=tmp_path / "dll",
            requests=((0, request),),
            raw_xic_batch_size=64,
        )
    )

    assert seen["requests"][0].mz == request.precursor_mz
    assert result.indexed_results[0][1].status == "pass"


def test_identity_trace_process_returns_blocked_results_on_extraction_error(
    monkeypatch,
    tmp_path: Path,
) -> None:
    requests = (
        _identity_trace_request("sample-a", "A1"),
        _identity_trace_request("sample-a", "A2"),
    )

    class PartiallyBrokenRawContext:
        def __init__(self):
            self.calls = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def extract_xic_many(self, requests):
            self.calls += 1
            if self.calls == 1:
                raise OSError("chunk unavailable")
            return tuple(
                XICTrace.from_arrays([5.0, 5.1], [0.0, 10.0]) for _ in requests
            )

    monkeypatch.setattr(
        "xic_extractor.raw_reader.open_raw",
        lambda raw_path, dll_dir: PartiallyBrokenRawContext(),
    )

    result = extract_identity_trace_sample_job(
        IdentityTraceSampleJob(
            sample_index=1,
            sample_stem="sample-a",
            raw_path=tmp_path / "sample-a.raw",
            dll_dir=tmp_path / "dll",
            requests=tuple(enumerate(requests)),
            raw_xic_batch_size=1,
        )
    )

    first = result.indexed_results[0][1]
    second = result.indexed_results[1][1]
    assert first.status == "blocked_infrastructure"
    assert first.blocked_reason == "raw_xic_extraction_error"
    assert second.status == "pass"
