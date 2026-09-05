import pytest

from xic_extractor.alignment.raw_sources import RawSourceTimingStats, TimedRawSource


def test_timed_raw_source_records_batch_calls() -> None:
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

    stats = RawSourceTimingStats(
        sample_stem="Sample_A",
        stage="alignment.build_owners.extract_xic",
    )
    source = TimedRawSource(BatchSource(), stats=stats)

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


def test_timed_raw_source_records_batch_failure_metrics() -> None:
    from xic_extractor.xic_models import XICRequest

    class FailingBatchSource:
        def __init__(self) -> None:
            self.raw_chromatogram_call_count = 0

        def extract_xic_many(self, requests):
            self.raw_chromatogram_call_count += 1
            raise RuntimeError("batch failed")

        def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
            raise AssertionError("fallback should not be used")

    stats = RawSourceTimingStats(
        sample_stem="Sample_A",
        stage="alignment.build_owners.extract_xic",
    )
    source = TimedRawSource(FailingBatchSource(), stats=stats)

    with pytest.raises(RuntimeError, match="batch failed"):
        source.extract_xic_many(
            (
                XICRequest(mz=258.0, rt_min=8.0, rt_max=9.0, ppm_tol=20.0),
                XICRequest(mz=259.0, rt_min=8.0, rt_max=9.0, ppm_tol=20.0),
            )
        )

    assert stats.extract_xic_count == 2
    assert stats.extract_xic_batch_count == 1
    assert stats.raw_chromatogram_call_count == 1
    assert stats.point_count == 0
    assert stats.elapsed_sec >= 0


def test_timed_raw_source_delegates_scan_window_lookup() -> None:
    from xic_extractor.xic_models import XICRequest

    class WindowSource:
        def scan_window_for_request(self, request):
            return (int(request.rt_min), int(request.rt_max))

    stats = RawSourceTimingStats(
        sample_stem="Sample_A",
        stage="alignment.build_owners.extract_xic",
    )
    source = TimedRawSource(WindowSource(), stats=stats)

    window = source.scan_window_for_request(
        XICRequest(mz=258.0, rt_min=8.0, rt_max=9.0, ppm_tol=20.0)
    )

    assert window == (8, 9)
