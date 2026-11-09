from dataclasses import replace
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
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CidNlPhysicalCandidateFact,
    CidNlPhysicalProof,
    group_cid_nl_physical_subject_units,
)
from xic_extractor.precursor_evidence import Ms1PrecursorObservation
from xic_extractor.xic_models import XICTrace


@pytest.mark.parametrize(
    "centroids, expected",
    [
        ((500.013,), 2),
        ((500.013, 500.019), 2),
        ((), 2),
    ],
)
def test_distinct_mass_window_signals_are_not_closed_by_post_measurement_centroids(
    tmp_path: Path, centroids: tuple[float, ...], expected: int
) -> None:
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
    # 500.000 and 500.012 measure one peak within 30 ppm; 500.024 lies 48 ppm
    # from that anchor and stays separate, so measurements never chain.
    assert len(result.peaks) == expected
    # Mass-compatible centroids do not make distinct acquired signals equal.
    # Candidate construction no longer runs a post-area reconciliation read.
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


@pytest.mark.parametrize(
    "case, expected",
    [
        ("same", 1),
        ("different_centroid", 2),
        ("two_centroids", 2),
        ("different_peak", 2),
        ("different_sample", 2),
        ("unavailable", 2),
    ],
)
def test_physical_unit_uses_acquired_centroids_not_raw_equality_or_correlation(
    case: str, expected: int
) -> None:
    centroid = (500.013, 500.019) if case == "two_centroids" else (500.013,)
    observed = tuple(
        Ms1PrecursorObservation(i + 1, rt, centroid)
        for i, rt in enumerate((8.99, 9.0, 9.01))
    )
    left = CidNlPhysicalProof(
        acquisition_source_id="sample-a",
        apex_rt_min=9.0,
        left_rt_min=8.9,
        right_rt_min=9.1,
        xic_mz=500.012,
        xic_ppm_tol=30,
        rt=(8.9, 8.99, 9.0, 9.01, 9.1),
        raw=(1, 50, 100, 50, 1),
        apex_centroids=() if case == "unavailable" else observed,
    )
    right = replace(left, xic_mz=500.024, raw=(2, 50, 100, 50, 3))
    if case == "different_centroid":
        right = replace(
            right,
            raw=left.raw,
            apex_centroids=tuple(replace(o, centroid_mzs=(500.019,)) for o in observed),
        )
    elif case == "different_peak":
        right = replace(
            right,
            xic_mz=left.xic_mz,
            apex_rt_min=9.3,
            left_rt_min=9.2,
            right_rt_min=9.4,
        )
    elif case == "different_sample":
        right = replace(right, acquisition_source_id="sample-b")
    facts = tuple(
        CidNlPhysicalCandidateFact(str(i), p.xic_mz, 116.0474, p)
        for i, p in enumerate((left, right))
    )
    units = group_cid_nl_physical_subject_units(
        facts, composition=None, precursor_tolerance_ppm=30
    )
    assert len(units) == expected
