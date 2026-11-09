from pathlib import Path

import numpy as np
import pytest

from tests.cid_nl_raw_fixture import TEST_PROFILE
from tests.discovery_ms1_capture_fixtures import (
    _channel,
    _FakeRaw,
    _gaussian,
    _seed,
    _settings,
)
from xic_extractor.discovery.csv_reader import read_discovery_peaks_csv
from xic_extractor.discovery.csv_writer import write_discovery_peaks_csv
from xic_extractor.discovery.ms1_capture import capture_ms1_peaks
from xic_extractor.precursor_evidence import Ms1PrecursorObservation
from xic_extractor.xic_models import XICTrace


@pytest.mark.parametrize("centroids", [(500.013,), (500.013, 500.019), ()])
def test_post_measurement_centroids_do_not_decide_peak_identity(
    tmp_path: Path, centroids: tuple[float, ...]
) -> None:
    expected = 1
    batches = []

    class Raw(_FakeRaw):
        def scan_window_for_request(self, request):
            return tuple(
                int(np.argmin(abs(self.rt - rt))) + 1
                for rt in (request.rt_min, request.rt_max)
            )

        def extract_xic_many(self, requests):
            traces = super().extract_xic_many(requests)
            result = []
            for request, trace in zip(requests, traces, strict=True):
                intensity = trace.intensity.copy()
                # Different extraction windows include different tail signal.
                intensity[100] += (request.mz - 500.0) * 300
                result.append(XICTrace.from_arrays(trace.rt, intensity))
            return tuple(result)

        def read_ms1_mass_windows(self, requests):
            batches.append(requests)
            return tuple(
                Ms1PrecursorObservation(
                    scan,
                    float(self.rt[scan - 1]),
                    tuple(mz for mz in centroids if low <= mz <= high),
                )
                for scan, low, high in requests
            )

    rt = np.linspace(8.4, 9.6, 241)
    raw = Raw(rt=rt, intensity=_gaussian(rt, center=9.0, sigma=0.06, height=1000))
    settings = _settings(precursor_mz_tolerance_ppm=30)
    groups = tuple(
        _channel(
            (
                _seed(
                    scan_number=10 + index,
                    rt=9.0,
                    precursor_mz=mz,
                    product_mz=mz - 116.0474,
                    product_intensity=10000 * (index + 1),
                ),
            )
        )
        for index, mz in enumerate((500.0, 500.024, 500.012))
    )
    result = capture_ms1_peaks(
        raw,
        groups,
        acquisition_source=raw.acquisition_source,
        signal_processing_profile=TEST_PROFILE,
        settings=settings,
        ms1_centroid_mz=lambda *_: None,
    )
    # The three windows read the same signal except one scan of window-edge
    # tail, so they are one peak, decided from the acquired traces alone.
    assert len(result.peaks) == expected
    # Candidate construction runs no post-area centroid reconciliation read.
    assert batches == []
    published = read_discovery_peaks_csv(
        write_discovery_peaks_csv(tmp_path / "candidates.csv", result.peaks)
    )
    assert len(published) == expected
    assert {c.peak_id: c.ms1_area for c in published} == {
        c.peak_id: c.ms1_area for c in result.peaks
    }
    assert len(result.physical_evaluations) == expected
    assert sorted(
        scan for candidate in result.peaks for scan in candidate.event_scan_ids
    ) == [10, 11, 12]
    for candidate, binding in zip(result.peaks, result.physical_bindings, strict=True):
        assert (
            candidate.ms1_area == binding.physical_evaluation.facts.area_counts_seconds
        )
        assert candidate.ms1_area == pytest.approx(
            1000 * 0.06 * np.sqrt(2 * np.pi) * 60, rel=0.06
        )


class _CentroidRaw(_FakeRaw):
    """Each window sums the centroids inside it; ions are (centroid m/z, scale)."""

    def __init__(self, ions, *, edge_scans=(), edge_mz=None):
        rt = np.linspace(8.4, 9.6, 241)
        super().__init__(
            rt=rt, intensity=_gaussian(rt, center=9.0, sigma=0.06, height=1000)
        )
        self.ions = ions
        self.edge_scans = frozenset(edge_scans)
        self.edge_mz = edge_mz

    def extract_xic_many(self, requests):
        super().extract_xic_many(requests)
        traces = []
        for request in requests:
            half_width = request.mz * request.ppm_tol * 1e-6
            low, high = request.mz - half_width, request.mz + half_width
            intensity = np.zeros_like(self.rt)
            for index, ion in enumerate(self.ions):
                mz, scale = ion
                centroids = np.full(len(self.rt), mz)
                if index == 0 and self.edge_mz is not None:
                    # The first ion's centroid jitters to one window edge.
                    centroids[list(self.edge_scans)] = self.edge_mz
                inside = (centroids >= low) & (centroids <= high)
                intensity = intensity + np.where(inside, self.intensity * scale, 0.0)
            traces.append(XICTrace.from_arrays(self.rt, intensity))
        return tuple(traces)


def _window_seed(scan: int, mz: float):
    return _seed(
        scan_number=scan,
        rt=9.0,
        precursor_mz=mz,
        product_mz=mz - 116.0474,
        product_intensity=10000.0,
    )


@pytest.mark.parametrize("reverse", [False, True])
def test_one_ion_in_two_windows_is_one_peak_whatever_the_input_order(
    reverse: bool,
) -> None:
    # One ion at 500.000; its centroid jitters to 500.011 on six scans near the
    # apex, past the upper edge of the 499.995 window only. The second window
    # sits 25 ppm away, beyond the precursor tolerance of the first.
    raw = _CentroidRaw(((500.0, 1.0),), edge_scans=range(96, 102), edge_mz=500.011)
    groups = tuple(
        _channel((_window_seed(scan, mz),))
        for scan, mz in ((10, 499.995), (11, 500.0075))
    )
    result = capture_ms1_peaks(
        raw,
        groups[::-1] if reverse else groups,
        acquisition_source=raw.acquisition_source,
        signal_processing_profile=TEST_PROFILE,
        settings=_settings(),
        ms1_centroid_mz=lambda *_: None,
    )

    (peak,) = result.peaks
    assert peak.event_scan_ids == (10, 11)
    # The kept measurement is the window that loses no centroid.
    assert peak.xic_mz == pytest.approx(500.0075)
    assert peak.peak_id == "TumorBC2312_DNA@mz500.0075_rt9.0000"
    (record,) = result.physical_evaluations
    assert peak.ms1_area == record.evaluation.facts.area_counts_seconds
    # Every event is judged against that one peak; the 499.995 precursor lies
    # 25 ppm from it and is located but not attached.
    reasons = {
        item.event.scan_number: item.unattached_reason for item in result.attachments
    }
    assert reasons == {10: "mass_outside_peak", 11: ""}


def test_distinct_ions_in_overlapping_windows_stay_apart() -> None:
    # 13C/15N-like neighbours 25 ppm apart: each window holds one ion, so the
    # two traces never read an equal intensity.
    raw = _CentroidRaw(((500.0, 1.0), (500.0125, 0.13)))
    groups = tuple(
        _channel((_window_seed(scan, mz),))
        for scan, mz in ((10, 499.995), (11, 500.0135))
    )
    result = capture_ms1_peaks(
        raw,
        groups,
        acquisition_source=raw.acquisition_source,
        signal_processing_profile=TEST_PROFILE,
        settings=_settings(),
        ms1_centroid_mz=lambda *_: None,
    )

    assert sorted(peak.event_scan_ids for peak in result.peaks) == [(10,), (11,)]
    areas = sorted(peak.ms1_area for peak in result.peaks)
    assert areas[0] == pytest.approx(0.13 * areas[1])
