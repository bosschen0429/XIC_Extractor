import numpy as np
import pytest

from xic_extractor.exclusion_list.background import (
    Ms1Centroids,
    find_background_windows,
    top_n_counts,
    whole_run_windows,
)

SCANS = 200
RT = np.arange(SCANS) * 0.05  # 0-10 min


def centroids(points):
    points = sorted(points)  # the RAW index yields centroids scan by scan
    scan, mz, intensity = (np.array(values) for values in zip(*points, strict=True))
    return Ms1Centroids(
        RT, scan.astype(np.int64), mz.astype(float), intensity.astype(float)
    )


def test_persistent_ion_spans_the_run_and_an_eluting_ion_gets_a_padded_window():
    points = [(s, 300.0, 1e6) for s in range(SCANS)]
    points += [(s, 450.0, 5e5) for s in range(60, 100)]

    windows = find_background_windows(
        centroids(points), run_end=10.0, intensity_threshold=25000
    )

    spans = {round(w.mz, 3): (w.start, w.stop) for w in windows}
    assert spans[300.0] == (0.0, 10.0)
    assert spans[450.0] == pytest.approx((RT[60] - 0.5, RT[99] + 0.5))


def test_ions_10_ppm_apart_stay_separate():
    light, heavy = 400.0, 400.0 * (1 + 10e-6)
    points = [(s, light, 1e6) for s in range(SCANS)]
    points += [(s, heavy, 1e6) for s in range(SCANS)]

    windows = find_background_windows(
        centroids(points), run_end=10.0, intensity_threshold=25000
    )

    assert sorted(round(w.mz, 5) for w in windows) == [round(light, 5), round(heavy, 5)]


def test_gaps_longer_than_12_scans_split_an_ion_into_two_windows():
    points = [(s, 500.0, 1e6) for s in [*range(10, 40), *range(120, 150)]]

    windows = find_background_windows(
        centroids(points), run_end=10.0, intensity_threshold=25000
    )

    assert len(windows) == 2
    merged = whole_run_windows(windows, run_end=10.0)
    assert [(w.start, w.stop, len(w.scans)) for w in merged] == [(0.0, 10.0, 60)]


def test_top_n_ranks_only_ions_inside_the_methods_scan_range():
    # 25 stronger ions above m/z 800 push the ion out of the top 20 in a
    # 145-1000 scan, but a 145-800 method never acquires them.
    points = [(s, 300.0, 1e5) for s in range(SCANS)]
    points += [(s, 900.0 + i, 1e6) for s in range(SCANS) for i in range(25)]
    data = centroids(points)
    windows = [
        w
        for w in find_background_windows(data, run_end=10.0, intensity_threshold=25000)
        if w.mz < 301
    ]

    assert top_n_counts(data, windows, (145.0, 1000.0)).tolist() == [0]
    assert top_n_counts(data, windows, (145.0, 800.0)).tolist() == [SCANS]
