import numpy as np

from xic_extractor.raw_superwindow_source import RawSuperWindowSource
from xic_extractor.xic_models import XICRequest, XICTrace


def test_raw_superwindow_source_merges_overlaps_and_crops_to_request_order() -> None:
    raw = FakeSuperWindowRawHandle()
    source = RawSuperWindowSource(raw, superwindow_span_factor=2)

    traces = source.extract_xic_many(
        (
            XICRequest(mz=200.0, rt_min=2.0, rt_max=4.0, ppm_tol=10.0),
            XICRequest(mz=100.0, rt_min=1.0, rt_max=3.0, ppm_tol=10.0),
        )
    )

    assert raw.call_count == 1
    assert [
        (request.mz, request.rt_min, request.rt_max) for request in raw.batches[0]
    ] == [
        (100.0, 1.0, 4.0),
        (200.0, 1.0, 4.0),
    ]
    assert traces[0].rt.tolist() == [2.0, 3.0, 4.0]
    assert traces[0].intensity.tolist() == [2002.0, 2003.0, 2004.0]
    assert traces[1].rt.tolist() == [1.0, 2.0, 3.0]
    assert traces[1].intensity.tolist() == [1001.0, 1002.0, 1003.0]


def test_raw_superwindow_source_respects_span_limit() -> None:
    raw = FakeSuperWindowRawHandle()
    source = RawSuperWindowSource(raw, superwindow_span_factor=1)

    source.extract_xic_many(
        (
            XICRequest(mz=100.0, rt_min=1.0, rt_max=3.0, ppm_tol=10.0),
            XICRequest(mz=200.0, rt_min=2.0, rt_max=4.0, ppm_tol=10.0),
        )
    )

    assert raw.call_count == 2


class FakeSuperWindowRawHandle:
    def __init__(self) -> None:
        self.call_count = 0
        self.batches: list[tuple[XICRequest, ...]] = []

    @property
    def raw_chromatogram_call_count(self) -> int:
        return self.call_count

    def scan_window_for_request(self, request: XICRequest) -> tuple[int, int]:
        return int(round(request.rt_min)), int(round(request.rt_max))

    def retention_time_for_scan(self, scan_number: int) -> float:
        return float(scan_number)

    def extract_xic_many(
        self,
        requests: tuple[XICRequest, ...],
    ):
        self.call_count += 1
        self.batches.append(requests)
        traces = []
        for request in requests:
            rt = np.arange(
                int(round(request.rt_min)),
                int(round(request.rt_max)) + 1,
                dtype=float,
            )
            traces.append(
                XICTrace.from_arrays(
                    rt,
                    request.mz * 10.0 + rt,
                )
            )
        return tuple(traces)
