import pickle
from concurrent.futures import Future
from pathlib import Path

from xic_extractor.alignment.identity_coherence.models import (
    IdentityCoherenceTraceRequest,
    IdentityCoherenceTraceResult,
)
from xic_extractor.alignment.process_backend import (
    IdentityTraceSampleJob,
    IdentityTraceSampleResult,
    extract_identity_trace_sample_job,
    run_identity_trace_process,
)
from xic_extractor.xic_models import XICRequest, XICTrace


def test_timed_process_raw_source_records_batch_calls() -> None:
    import xic_extractor.alignment.process_execution as process_module
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

    stats = process_module.TimedProcessStats(sample_stem="Sample_A")
    source = process_module.TimedProcessRawSource(BatchSource(), stats=stats)

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
    import xic_extractor.alignment.process_execution as process_module
    from xic_extractor.xic_models import XICRequest

    class WindowSource:
        def scan_window_for_request(self, request):
            return (int(request.rt_min), int(request.rt_max))

    stats = process_module.TimedProcessStats(sample_stem="Sample_A")
    source = process_module.TimedProcessRawSource(WindowSource(), stats=stats)

    window = source.scan_window_for_request(
        XICRequest(mz=258.0, rt_min=8.0, rt_max=9.0, ppm_tol=20.0)
    )

    assert window == (8, 9)


def test_timed_process_raw_source_delegates_retention_time_lookup() -> None:
    import xic_extractor.alignment.process_execution as process_module

    class WindowSource:
        def retention_time_for_scan(self, scan_number):
            return scan_number / 100.0

    stats = process_module.TimedProcessStats(sample_stem="Sample_A")
    source = process_module.TimedProcessRawSource(WindowSource(), stats=stats)

    assert source.retention_time_for_scan(812) == 8.12


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
