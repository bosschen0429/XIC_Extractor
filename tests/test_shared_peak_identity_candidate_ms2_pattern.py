from __future__ import annotations

import csv
from collections.abc import Iterator
from contextlib import AbstractContextManager
from pathlib import Path

import numpy as np

from xic_extractor.diagnostics.shared_peak_identity_explanation import (
    candidate_ms2_pattern,
)
from xic_extractor.discovery.models import DISCOVERY_CANDIDATE_COLUMNS
from xic_extractor.raw_reader import Ms2Scan, Ms2ScanEvent

SOURCE_CANDIDATE_ID = "S1#100@mz257.125_p141.078"


def test_candidate_ms2_pattern_producer_joins_source_candidate_id_fail_closed(
    tmp_path: Path,
) -> None:
    cells = tmp_path / "alignment_cells.tsv"
    review = tmp_path / "alignment_review.tsv"
    batch_index = tmp_path / "discovery_batch_index.csv"
    sample_dir = tmp_path / "S1"
    sample_dir.mkdir()
    candidates = sample_dir / "discovery_candidates.csv"
    _write_tsv(
        cells,
        (
            "feature_family_id",
            "sample_stem",
            "status",
            "apex_rt",
            "peak_start_rt",
            "peak_end_rt",
            "source_candidate_id",
        ),
        [
            {
                "feature_family_id": "FAM001",
                "sample_stem": "S1",
                "status": "detected",
                "apex_rt": "10.02",
                "peak_start_rt": "9.9",
                "peak_end_rt": "10.1",
                "source_candidate_id": SOURCE_CANDIDATE_ID,
            },
            {
                "feature_family_id": "FAM002",
                "sample_stem": "S1",
                "status": "backfilled",
                "apex_rt": "11.0",
                "peak_start_rt": "10.9",
                "peak_end_rt": "11.1",
                "source_candidate_id": "",
            },
        ],
    )
    _write_tsv(
        review,
        (
            "feature_family_id",
            "family_center_mz",
            "family_product_mz",
            "family_observed_neutral_loss_da",
        ),
        [
            {
                "feature_family_id": "FAM001",
                "family_center_mz": "257.125",
                "family_product_mz": "141.077",
                "family_observed_neutral_loss_da": "116.047",
            },
            {
                "feature_family_id": "FAM002",
                "family_center_mz": "266.0",
                "family_product_mz": "150.0",
                "family_observed_neutral_loss_da": "116.0",
            },
        ],
    )
    _write_csv(
        batch_index,
        ("sample_stem", "raw_file", "candidate_csv"),
        [
            {
                "sample_stem": "S1",
                "raw_file": "S1.raw",
                "candidate_csv": str(candidates),
            }
        ],
    )
    _write_candidate_csv(candidates)

    rows = candidate_ms2_pattern.build_candidate_ms2_pattern_rows(
        alignment_cells_tsv=cells,
        alignment_review_tsv=review,
        discovery_batch_index_csv=batch_index,
        oracle_keys=(("FAM001", "S1"), ("FAM002", "S1")),
    )
    by_key = {(row["feature_family_id"], row["sample_stem"]): row for row in rows}

    supportive = by_key[("FAM001", "S1")]
    assert supportive["candidate_ms2_pattern_status"] == "supportive"
    assert supportive["candidate_ms2_evidence_level"] == "sample_candidate_aligned"
    assert supportive["source_candidate_id"] == SOURCE_CANDIDATE_ID
    assert supportive["matched_neutral_loss_count"] == "1"
    assert supportive["apex_ms2_delta_sec"] == "1.2"

    missing = by_key[("FAM002", "S1")]
    assert missing["candidate_ms2_pattern_status"] == "not_available"
    assert missing["candidate_ms2_evidence_level"] == "not_available"
    assert missing["reason"] == "source_candidate_id_missing"


def test_candidate_ms2_pattern_producer_reports_family_context_conflict(
    tmp_path: Path,
) -> None:
    cells = tmp_path / "alignment_cells.tsv"
    review = tmp_path / "alignment_review.tsv"
    batch_index = tmp_path / "discovery_batch_index.csv"
    sample_dir = tmp_path / "S1"
    sample_dir.mkdir()
    candidates = sample_dir / "discovery_candidates.csv"
    _write_tsv(
        cells,
        (
            "feature_family_id",
            "sample_stem",
            "status",
            "apex_rt",
            "peak_start_rt",
            "peak_end_rt",
            "source_candidate_id",
        ),
        [
            {
                "feature_family_id": "FAM001",
                "sample_stem": "S1",
                "status": "detected",
                "apex_rt": "10.02",
                "peak_start_rt": "9.9",
                "peak_end_rt": "10.1",
                "source_candidate_id": SOURCE_CANDIDATE_ID,
            }
        ],
    )
    _write_tsv(
        review,
        (
            "feature_family_id",
            "family_center_mz",
            "family_product_mz",
            "family_observed_neutral_loss_da",
        ),
        [
            {
                "feature_family_id": "FAM001",
                "family_center_mz": "257.125",
                "family_product_mz": "160.0",
                "family_observed_neutral_loss_da": "100.0",
            }
        ],
    )
    _write_csv(
        batch_index,
        ("sample_stem", "raw_file", "candidate_csv"),
        [
            {
                "sample_stem": "S1",
                "raw_file": "S1.raw",
                "candidate_csv": str(candidates),
            }
        ],
    )
    _write_candidate_csv(candidates)

    rows = candidate_ms2_pattern.build_candidate_ms2_pattern_rows(
        alignment_cells_tsv=cells,
        alignment_review_tsv=review,
        discovery_batch_index_csv=batch_index,
        oracle_keys=(("FAM001", "S1"),),
    )

    assert rows[0]["candidate_ms2_pattern_status"] == "conflict"
    assert rows[0]["reason"] == (
        "source_candidate_ms2_pattern_conflicts_with_family_context"
    )


def test_candidate_ms2_pattern_direct_candidate_uses_targeted_warn_band(
    tmp_path: Path,
) -> None:
    cells = tmp_path / "alignment_cells.tsv"
    review = tmp_path / "alignment_review.tsv"
    batch_index = tmp_path / "discovery_batch_index.csv"
    sample_dir = tmp_path / "S1"
    sample_dir.mkdir()
    candidates = sample_dir / "discovery_candidates.csv"
    _write_tsv(
        cells,
        (
            "feature_family_id",
            "sample_stem",
            "status",
            "apex_rt",
            "peak_start_rt",
            "peak_end_rt",
            "source_candidate_id",
        ),
        [
            {
                "feature_family_id": "FAM001",
                "sample_stem": "S1",
                "status": "detected",
                "apex_rt": "10.02",
                "peak_start_rt": "9.9",
                "peak_end_rt": "10.1",
                "source_candidate_id": SOURCE_CANDIDATE_ID,
            }
        ],
    )
    _write_tsv(
        review,
        (
            "feature_family_id",
            "family_center_mz",
            "family_product_mz",
            "family_observed_neutral_loss_da",
        ),
        [
            {
                "feature_family_id": "FAM001",
                "family_center_mz": "257.125",
                "family_product_mz": "141.078",
                "family_observed_neutral_loss_da": "116.047",
            }
        ],
    )
    _write_csv(
        batch_index,
        ("sample_stem", "raw_file", "candidate_csv"),
        [
            {
                "sample_stem": "S1",
                "raw_file": "S1.raw",
                "candidate_csv": str(candidates),
            }
        ],
    )
    _write_candidate_csv(candidates, neutral_loss_mass_error_ppm="25")

    rows = candidate_ms2_pattern.build_candidate_ms2_pattern_rows(
        alignment_cells_tsv=cells,
        alignment_review_tsv=review,
        discovery_batch_index_csv=batch_index,
        oracle_keys=(("FAM001", "S1"),),
    )

    row = rows[0]
    assert row["candidate_ms2_pattern_status"] == "partial_support"
    assert row["candidate_ms2_evidence_level"] == "sample_candidate_aligned"
    assert row["candidate_ms2_similarity_score"] == "0.5"
    assert row["nl_ppm_warn"] == "20"
    assert row["nl_ppm_max"] == "50"
    assert row["reason"] == (
        "source_candidate_neutral_loss_warn_band_matches_family_context"
    )


def test_candidate_ms2_pattern_direct_candidate_rejects_outside_targeted_max(
    tmp_path: Path,
) -> None:
    cells = tmp_path / "alignment_cells.tsv"
    review = tmp_path / "alignment_review.tsv"
    batch_index = tmp_path / "discovery_batch_index.csv"
    sample_dir = tmp_path / "S1"
    sample_dir.mkdir()
    candidates = sample_dir / "discovery_candidates.csv"
    _write_tsv(
        cells,
        (
            "feature_family_id",
            "sample_stem",
            "status",
            "apex_rt",
            "peak_start_rt",
            "peak_end_rt",
            "source_candidate_id",
        ),
        [
            {
                "feature_family_id": "FAM001",
                "sample_stem": "S1",
                "status": "detected",
                "apex_rt": "10.02",
                "peak_start_rt": "9.9",
                "peak_end_rt": "10.1",
                "source_candidate_id": SOURCE_CANDIDATE_ID,
            }
        ],
    )
    _write_tsv(
        review,
        (
            "feature_family_id",
            "family_center_mz",
            "family_product_mz",
            "family_observed_neutral_loss_da",
        ),
        [
            {
                "feature_family_id": "FAM001",
                "family_center_mz": "257.125",
                "family_product_mz": "141.078",
                "family_observed_neutral_loss_da": "116.047",
            }
        ],
    )
    _write_csv(
        batch_index,
        ("sample_stem", "raw_file", "candidate_csv"),
        [
            {
                "sample_stem": "S1",
                "raw_file": "S1.raw",
                "candidate_csv": str(candidates),
            }
        ],
    )
    _write_candidate_csv(candidates, neutral_loss_mass_error_ppm="55")

    rows = candidate_ms2_pattern.build_candidate_ms2_pattern_rows(
        alignment_cells_tsv=cells,
        alignment_review_tsv=review,
        discovery_batch_index_csv=batch_index,
        oracle_keys=(("FAM001", "S1"),),
    )

    row = rows[0]
    assert row["candidate_ms2_pattern_status"] == "conflict"
    assert row["candidate_ms2_similarity_score"] == "0"
    assert row["reason"] == "source_candidate_neutral_loss_outside_targeted_max_ppm"


def test_candidate_ms2_pattern_writer_uses_consumer_required_columns(
    tmp_path: Path,
) -> None:
    path = tmp_path / "candidate_ms2.tsv"
    rows = [
        {
            "feature_family_id": "FAM001",
            "sample_stem": "S1",
            "candidate_ms2_pattern_status": "supportive",
            "candidate_ms2_evidence_level": "sample_candidate_aligned",
        }
    ]

    candidate_ms2_pattern.write_candidate_ms2_pattern_rows(path, rows)

    with path.open(newline="", encoding="utf-8") as handle:
        fieldnames = tuple(csv.DictReader(handle, delimiter="\t").fieldnames or ())
    assert "feature_family_id" in fieldnames
    assert "sample_stem" in fieldnames
    assert "candidate_ms2_pattern_status" in fieldnames
    assert "candidate_ms2_evidence_level" in fieldnames


def test_candidate_ms2_pattern_raw_fallback_supports_source_missing_cell(
    tmp_path: Path,
) -> None:
    cells = tmp_path / "alignment_cells.tsv"
    review = tmp_path / "alignment_review.tsv"
    batch_index = tmp_path / "discovery_batch_index.csv"
    sample_dir = tmp_path / "S1"
    sample_dir.mkdir()
    candidates = sample_dir / "discovery_candidates.csv"
    _write_source_missing_fixture(cells, review, batch_index, candidates)

    rows = candidate_ms2_pattern.build_candidate_ms2_pattern_rows(
        alignment_cells_tsv=cells,
        alignment_review_tsv=review,
        discovery_batch_index_csv=batch_index,
        oracle_keys=(("FAM001", "S1"),),
        raw_scan_source_factory=lambda _sample: _RawContext(
            [
                _scan_event(
                    precursor_mz=257.125,
                    rt=10.0,
                    masses=[141.078],
                    intensities=[100.0],
                )
            ]
        ),
    )

    row = rows[0]
    assert row["candidate_ms2_pattern_status"] == "supportive"
    assert row["candidate_ms2_evidence_level"] == "sample_boundary_aligned"
    assert row["ms2_alignment_source"] == "raw_boundary_scan"
    assert row["raw_ms2_strict_nl_scan_count"] == "1"
    assert row["reason"] == "raw_boundary_ms2_pattern_matches_family_context"


def test_candidate_ms2_pattern_raw_fallback_uses_targeted_warn_band(
    tmp_path: Path,
) -> None:
    cells = tmp_path / "alignment_cells.tsv"
    review = tmp_path / "alignment_review.tsv"
    batch_index = tmp_path / "discovery_batch_index.csv"
    sample_dir = tmp_path / "S1"
    sample_dir.mkdir()
    candidates = sample_dir / "discovery_candidates.csv"
    _write_source_missing_fixture(cells, review, batch_index, candidates)
    precursor_mz = 257.125
    neutral_loss_da = 116.047
    warn_band_observed_loss = neutral_loss_da * (1.0 + 25.0 / 1_000_000.0)

    rows = candidate_ms2_pattern.build_candidate_ms2_pattern_rows(
        alignment_cells_tsv=cells,
        alignment_review_tsv=review,
        discovery_batch_index_csv=batch_index,
        oracle_keys=(("FAM001", "S1"),),
        raw_scan_source_factory=lambda _sample: _RawContext(
            [
                _scan_event(
                    precursor_mz=precursor_mz,
                    rt=10.0,
                    masses=[precursor_mz - warn_band_observed_loss],
                    intensities=[100.0],
                )
            ]
        ),
    )

    row = rows[0]
    assert row["candidate_ms2_pattern_status"] == "partial_support"
    assert row["candidate_ms2_evidence_level"] == "sample_boundary_aligned"
    assert row["candidate_ms2_similarity_score"] == "0.5"
    assert row["raw_ms2_best_loss_ppm"] == "25"
    assert row["reason"] == "raw_boundary_ms2_pattern_warn_band_matches_family_context"


def test_candidate_ms2_pattern_raw_fallback_reports_boundary_conflict(
    tmp_path: Path,
) -> None:
    cells = tmp_path / "alignment_cells.tsv"
    review = tmp_path / "alignment_review.tsv"
    batch_index = tmp_path / "discovery_batch_index.csv"
    sample_dir = tmp_path / "S1"
    sample_dir.mkdir()
    candidates = sample_dir / "discovery_candidates.csv"
    _write_source_missing_fixture(cells, review, batch_index, candidates)

    rows = candidate_ms2_pattern.build_candidate_ms2_pattern_rows(
        alignment_cells_tsv=cells,
        alignment_review_tsv=review,
        discovery_batch_index_csv=batch_index,
        oracle_keys=(("FAM001", "S1"),),
        raw_scan_source_factory=lambda _sample: _RawContext(
            [
                _scan_event(
                    precursor_mz=257.125,
                    rt=10.0,
                    masses=[150.0],
                    intensities=[100.0],
                )
            ]
        ),
    )

    row = rows[0]
    assert row["candidate_ms2_pattern_status"] == "conflict"
    assert row["candidate_ms2_evidence_level"] == "sample_boundary_aligned"
    assert row["raw_ms2_trigger_scan_count"] == "1"
    assert row["raw_ms2_strict_nl_scan_count"] == "0"
    assert row["reason"].startswith(
        "raw_boundary_ms2_trigger_without_expected_neutral_loss_product"
    )


def test_candidate_ms2_pattern_raw_fallback_does_not_make_low_product_conflict(
    tmp_path: Path,
) -> None:
    cells = tmp_path / "alignment_cells.tsv"
    review = tmp_path / "alignment_review.tsv"
    batch_index = tmp_path / "discovery_batch_index.csv"
    sample_dir = tmp_path / "S1"
    sample_dir.mkdir()
    candidates = sample_dir / "discovery_candidates.csv"
    _write_source_missing_fixture(cells, review, batch_index, candidates)

    rows = candidate_ms2_pattern.build_candidate_ms2_pattern_rows(
        alignment_cells_tsv=cells,
        alignment_review_tsv=review,
        discovery_batch_index_csv=batch_index,
        oracle_keys=(("FAM001", "S1"),),
        raw_scan_source_factory=lambda _sample: _RawContext(
            [
                _scan_event(
                    precursor_mz=257.125,
                    rt=10.0,
                    masses=[141.078, 150.0],
                    intensities=[0.5, 100.0],
                )
            ]
        ),
    )

    row = rows[0]
    assert row["candidate_ms2_pattern_status"] == "not_observed"
    assert row["candidate_ms2_evidence_level"] == "sample_boundary_no_observed_pattern"
    assert row["raw_ms2_diagnostic_product_absence_reason"] == (
        "product_below_intensity_floor"
    )
    assert row["reason"] == "raw_boundary_ms2_trigger_without_decisive_pattern"


def test_candidate_ms2_pattern_raw_fallback_keeps_no_ms2_as_not_observed(
    tmp_path: Path,
) -> None:
    cells = tmp_path / "alignment_cells.tsv"
    review = tmp_path / "alignment_review.tsv"
    batch_index = tmp_path / "discovery_batch_index.csv"
    sample_dir = tmp_path / "S1"
    sample_dir.mkdir()
    candidates = sample_dir / "discovery_candidates.csv"
    _write_source_missing_fixture(cells, review, batch_index, candidates)

    rows = candidate_ms2_pattern.build_candidate_ms2_pattern_rows(
        alignment_cells_tsv=cells,
        alignment_review_tsv=review,
        discovery_batch_index_csv=batch_index,
        oracle_keys=(("FAM001", "S1"),),
        raw_scan_source_factory=lambda _sample: _RawContext(
            [
                _scan_event(
                    precursor_mz=260.0,
                    rt=10.0,
                    masses=[141.078],
                    intensities=[100.0],
                )
            ]
        ),
    )

    row = rows[0]
    assert row["candidate_ms2_pattern_status"] == "not_observed"
    assert row["candidate_ms2_evidence_level"] == "sample_boundary_no_observed_pattern"
    assert row["raw_ms2_trigger_scan_count"] == "0"
    assert row["reason"] == "raw_boundary_ms2_not_observed"


def _write_candidate_csv(
    path: Path,
    *,
    neutral_loss_mass_error_ppm: str = "0.5",
) -> None:
    _write_csv(
        path,
        DISCOVERY_CANDIDATE_COLUMNS,
        [
            {
                "review_priority": "MEDIUM",
                "evidence_tier": "C",
                "evidence_score": "48",
                "ms2_support": "moderate",
                "ms1_support": "weak",
                "rt_alignment": "aligned",
                "discovery_candidate_state": "ms1_feature_nl_supported",
                "ms1_feature_row_id": "S1|DNA_dR|257.125|10.02",
                "candidate_id": SOURCE_CANDIDATE_ID,
                "feature_family_id": "S1@F001",
                "feature_family_size": "1",
                "precursor_mz": "257.125",
                "product_mz": "141.078",
                "observed_neutral_loss_da": "116.047",
                "best_seed_rt": "10.0",
                "seed_event_count": "1",
                "ms1_peak_found": "TRUE",
                "ms1_apex_rt": "10.02",
                "ms1_area": "1000",
                "ms2_product_max_intensity": "5000",
                "reason": "single MS2 NL seed; MS1 peak found",
                "raw_file": "S1.raw",
                "sample_stem": "S1",
                "best_ms2_scan_id": "100",
                "seed_scan_ids": "100",
                "neutral_loss_tag": "DNA_dR",
                "configured_neutral_loss_da": "116.047",
                "neutral_loss_mass_error_ppm": neutral_loss_mass_error_ppm,
                "neutral_loss_error_basis": "measured_scan_precursor_product",
                "precursor_mz_basis": "scan_precursor",
                "scan_precursor_mz": "257.125",
                "scan_precursor_delta_da": "0",
                "max_scan_precursor_abs_delta_da": "0",
                "rt_seed_min": "10.0",
                "rt_seed_max": "10.0",
                "ms1_search_rt_min": "9.5",
                "ms1_search_rt_max": "10.5",
                "ms1_seed_delta_min": "0.02",
                "ms1_peak_rt_start": "9.9",
                "ms1_peak_rt_end": "10.1",
                "ms1_height": "100",
                "ms1_trace_quality": "clean",
                "is_provisional": "FALSE",
                "ms1_scan_support_score": "1",
                "selected_tag_count": "1",
                "matched_tag_count": "1",
                "matched_tag_names": "DNA_dR",
                "primary_tag_name": "DNA_dR",
                "tag_combine_mode": "any",
                "tag_intersection_status": "single_tag",
                "tag_evidence_json": "{}",
            }
        ],
    )


def _write_source_missing_fixture(
    cells: Path,
    review: Path,
    batch_index: Path,
    candidates: Path,
) -> None:
    _write_tsv(
        cells,
        (
            "feature_family_id",
            "sample_stem",
            "status",
            "apex_rt",
            "peak_start_rt",
            "peak_end_rt",
            "source_candidate_id",
        ),
        [
            {
                "feature_family_id": "FAM001",
                "sample_stem": "S1",
                "status": "backfilled",
                "apex_rt": "10.0",
                "peak_start_rt": "9.9",
                "peak_end_rt": "10.1",
                "source_candidate_id": "",
            }
        ],
    )
    _write_tsv(
        review,
        (
            "feature_family_id",
            "family_center_mz",
            "family_product_mz",
            "family_observed_neutral_loss_da",
        ),
        [
            {
                "feature_family_id": "FAM001",
                "family_center_mz": "257.125",
                "family_product_mz": "141.078",
                "family_observed_neutral_loss_da": "116.047",
            }
        ],
    )
    _write_csv(
        batch_index,
        ("sample_stem", "raw_file", "candidate_csv"),
        [
            {
                "sample_stem": "S1",
                "raw_file": str(batch_index.parent / "S1.raw"),
                "candidate_csv": str(candidates),
            }
        ],
    )
    _write_candidate_csv(candidates)


def _scan_event(
    *,
    precursor_mz: float,
    rt: float,
    masses: list[float],
    intensities: list[float],
) -> Ms2ScanEvent:
    return Ms2ScanEvent(
        scan=Ms2Scan(
            scan_number=1,
            rt=rt,
            precursor_mz=precursor_mz,
            masses=np.asarray(masses, dtype=float),
            intensities=np.asarray(intensities, dtype=float),
            base_peak=max(intensities) if intensities else 0.0,
        ),
        parse_error=None,
        scan_number=1,
    )


class _RawContext(AbstractContextManager["_FakeRaw"]):
    def __init__(self, events: list[Ms2ScanEvent]) -> None:
        self.raw = _FakeRaw(events)

    def __enter__(self) -> "_FakeRaw":
        return self.raw

    def __exit__(self, *_args: object) -> None:
        return None


class _FakeRaw:
    def __init__(self, events: list[Ms2ScanEvent]) -> None:
        self.events = events

    def iter_ms2_scans(self, rt_min: float, rt_max: float) -> Iterator[Ms2ScanEvent]:
        for event in self.events:
            scan = event.scan
            if scan is None or rt_min <= scan.rt <= rt_max:
                yield event


def _write_tsv(
    path: Path,
    fieldnames: tuple[str, ...],
    rows: list[dict[str, str]],
) -> None:
    _write_delimited(path, fieldnames, rows, delimiter="\t")


def _write_csv(
    path: Path,
    fieldnames: tuple[str, ...],
    rows: list[dict[str, str]],
) -> None:
    _write_delimited(path, fieldnames, rows, delimiter=",")


def _write_delimited(
    path: Path,
    fieldnames: tuple[str, ...],
    rows: list[dict[str, str]],
    *,
    delimiter: str,
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter=delimiter)
        writer.writeheader()
        writer.writerows(rows)
