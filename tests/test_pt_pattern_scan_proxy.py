"""Smoke tests for the Task 7 scan-level Pt pattern proxy.

Per the task brief, the one required step is a proxy smoke test that emits
``DETECTED_FEATURES_COLUMNS`` exactly. These tests build fake
``Ms1ScanRecord`` scan data directly (no real RAW file or Thermo DLL) and
drive the proxy's pure-Python scan/group/feature/CSV pipeline end-to-end, so
the suite runs the same on any machine regardless of RAW/DLL availability.
"""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

from tools.diagnostics.pt_pattern_scan_proxy import (
    PROXY_SOURCE_LABEL,
    Ms1ScanRecord,
    detected_feature_rows,
    features_from_groups,
    group_scan_hits,
    scan_pattern_hits,
    write_detected_features_csv,
)
from xic_extractor.discovery.delta_mass_csv import DETECTED_FEATURES_COLUMNS

_BASE_MZ = 500.0
_PLUS_1_MZ = _BASE_MZ + 1.0021
_PLUS_2_MZ = _BASE_MZ + 2.0023
_PLUS_4_MZ = _BASE_MZ + 4.0052


def _fake_full_pattern_scan(scan_number: int, rt_min: float) -> Ms1ScanRecord:
    """A scan containing all four Pt positions plus an unrelated peak."""

    masses = np.array(
        [_BASE_MZ, _PLUS_1_MZ, _PLUS_2_MZ, _PLUS_4_MZ, 650.1234],
        dtype=float,
    )
    intensities = np.array([1000.0, 400.0, 150.0, 40.0, 999.0], dtype=float)
    return Ms1ScanRecord(
        scan_number=scan_number,
        rt_min=rt_min,
        masses=masses,
        intensities=intensities,
    )


def _fake_partial_pattern_scan(scan_number: int, rt_min: float) -> Ms1ScanRecord:
    """A scan missing pt_plus_4 -- must not produce a scan hit."""

    masses = np.array([_BASE_MZ, _PLUS_1_MZ, _PLUS_2_MZ], dtype=float)
    intensities = np.array([1000.0, 400.0, 150.0], dtype=float)
    return Ms1ScanRecord(
        scan_number=scan_number,
        rt_min=rt_min,
        masses=masses,
        intensities=intensities,
    )


def test_scan_pattern_hits_requires_all_three_non_base_positions() -> None:
    full_scan = _fake_full_pattern_scan(scan_number=100, rt_min=10.00)
    partial_scan = _fake_partial_pattern_scan(scan_number=101, rt_min=10.01)

    full_hits = scan_pattern_hits(
        full_scan,
        lookup_ppm=50.0,
        min_base_intensity=0.0,
        min_position_intensity=0.0,
        max_candidates_per_scan=500,
    )
    partial_hits = scan_pattern_hits(
        partial_scan,
        lookup_ppm=50.0,
        min_base_intensity=0.0,
        min_position_intensity=0.0,
        max_candidates_per_scan=500,
    )

    assert len(full_hits) == 1
    hit = full_hits[0]
    assert hit.base_mz == _BASE_MZ
    assert set(hit.position_mz) == {"pt_plus_1", "pt_plus_2", "pt_plus_4"}
    assert partial_hits == []


def test_full_pipeline_emits_detected_features_columns_exactly(
    tmp_path: Path,
) -> None:
    scans = [
        _fake_full_pattern_scan(scan_number=100, rt_min=10.00),
        _fake_full_pattern_scan(scan_number=101, rt_min=10.01),
        _fake_full_pattern_scan(scan_number=102, rt_min=10.02),
    ]

    all_hits = [
        hit
        for scan in scans
        for hit in scan_pattern_hits(
            scan,
            lookup_ppm=50.0,
            min_base_intensity=0.0,
            min_position_intensity=0.0,
            max_candidates_per_scan=500,
        )
    ]
    assert len(all_hits) == 3

    groups = group_scan_hits(all_hits, group_width_da=0.005, rt_gap_split_min=0.20)
    assert len(groups) == 1
    assert len(groups[0]) == 3

    features = features_from_groups(groups)
    # One pseudo-feature per Pt position (base + 3 non-base) for this group.
    assert len(features) == 4
    assert {feature.label for feature in features} == {
        "base",
        "pt_plus_1",
        "pt_plus_2",
        "pt_plus_4",
    }

    rows = detected_feature_rows(features, sample_stem="TestSample")
    csv_path = tmp_path / "detected_features.csv"
    write_detected_features_csv(csv_path, rows)

    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        data_rows = list(reader)

    assert tuple(header) == DETECTED_FEATURES_COLUMNS
    assert len(data_rows) == 4
    source_index = DETECTED_FEATURES_COLUMNS.index("source")
    assert all(row[source_index] == PROXY_SOURCE_LABEL for row in data_rows)


def test_detected_feature_rows_use_proxy_source_and_conservative_placeholders() -> (
    None
):
    scans = [_fake_full_pattern_scan(scan_number=100, rt_min=10.00)]
    hits = [
        hit
        for scan in scans
        for hit in scan_pattern_hits(
            scan,
            lookup_ppm=50.0,
            min_base_intensity=0.0,
            min_position_intensity=0.0,
            max_candidates_per_scan=500,
        )
    ]
    groups = group_scan_hits(hits, group_width_da=0.005, rt_gap_split_min=0.20)
    features = features_from_groups(groups)

    rows = detected_feature_rows(features, sample_stem="TestSample")

    assert len(rows) == 4
    for row in rows:
        assert row["source"] == PROXY_SOURCE_LABEL
        assert row["sample_stem"] == "TestSample"
        # Conservative, honestly-non-passing placeholders: not one of the
        # matcher's accepted "supported" trace qualities, and below its
        # shape_score floor -- see module docstring for the reasoning.
        assert row["trace_quality"] == "REVIEW"
        assert row["shape_score"] == 0.0
        assert set(row) == set(DETECTED_FEATURES_COLUMNS)


def test_group_scan_hits_splits_on_rt_gap() -> None:
    scans = [
        _fake_full_pattern_scan(scan_number=100, rt_min=10.00),
        _fake_full_pattern_scan(scan_number=101, rt_min=10.01),
        # Large RT gap -> separate chromatographic region / separate group.
        _fake_full_pattern_scan(scan_number=200, rt_min=15.00),
    ]
    hits = [
        hit
        for scan in scans
        for hit in scan_pattern_hits(
            scan,
            lookup_ppm=50.0,
            min_base_intensity=0.0,
            min_position_intensity=0.0,
            max_candidates_per_scan=500,
        )
    ]

    groups = group_scan_hits(hits, group_width_da=0.005, rt_gap_split_min=0.20)

    assert len(groups) == 2
    assert len(groups[0]) == 2
    assert len(groups[1]) == 1


def test_write_detected_features_csv_header_only_when_no_rows(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "empty" / "detected_features.csv"

    write_detected_features_csv(csv_path, [])

    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        remaining = list(reader)

    assert tuple(header) == DETECTED_FEATURES_COLUMNS
    assert remaining == []
