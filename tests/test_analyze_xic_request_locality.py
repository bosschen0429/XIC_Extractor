import csv
from pathlib import Path

from scripts import analyze_xic_request_locality as locality


def test_summarize_locality_reports_original_sorted_and_upper_bound_calls(
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "sample-a.raw"
    raw_path.write_text("raw", encoding="utf-8")
    records = (
        locality.RequestRecord(
            stage="build_owners",
            sample_stem="sample-a",
            mz=100.0,
            rt_min=0.0,
            rt_max=2.0,
            ppm_tol=20.0,
        ),
        locality.RequestRecord(
            stage="build_owners",
            sample_stem="sample-a",
            mz=200.0,
            rt_min=4.0,
            rt_max=6.0,
            ppm_tol=20.0,
        ),
        locality.RequestRecord(
            stage="build_owners",
            sample_stem="sample-a",
            mz=101.0,
            rt_min=0.0,
            rt_max=2.0,
            ppm_tol=20.0,
        ),
    )

    summary = locality.summarize_locality(
        records,
        raw_paths={"sample-a": raw_path},
        dll_dir=tmp_path / "dll",
        batch_size=2,
        open_raw_func=fake_open_raw,
    )

    assert summary["request_count"] == 3
    assert summary["original_chunk_call_count"] == 3
    assert summary["sorted_chunk_call_count"] == 2
    assert summary["upper_bound_call_count"] == 2
    assert summary["unique_scan_window_count"] == 2


def test_summarize_locality_reports_request_census_categories(
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "sample-a.raw"
    raw_path.write_text("raw", encoding="utf-8")
    records = (
        locality.RequestRecord(
            stage="owner_backfill",
            sample_stem="sample-a",
            mz=100.0,
            rt_min=0.0,
            rt_max=2.0,
            ppm_tol=20.0,
        ),
        locality.RequestRecord(
            stage="owner_backfill",
            sample_stem="sample-a",
            mz=100.0,
            rt_min=0.0,
            rt_max=2.0,
            ppm_tol=20.0,
        ),
        locality.RequestRecord(
            stage="owner_backfill",
            sample_stem="sample-a",
            mz=101.0,
            rt_min=0.0,
            rt_max=2.0,
            ppm_tol=20.0,
        ),
        locality.RequestRecord(
            stage="owner_backfill",
            sample_stem="sample-a",
            mz=100.001,
            rt_min=0.1,
            rt_max=2.1,
            ppm_tol=20.0,
        ),
        locality.RequestRecord(
            stage="owner_backfill",
            sample_stem="sample-a",
            mz=500.0,
            rt_min=10.0,
            rt_max=12.0,
            ppm_tol=20.0,
        ),
    )

    summary = locality.summarize_locality(
        records,
        raw_paths={"sample-a": raw_path},
        dll_dir=tmp_path / "dll",
        batch_size=64,
        open_raw_func=fake_open_raw,
        near_mz_ppm=20.0,
        near_rt_sec=30.0,
    )

    assert summary["request_count"] == 5
    assert summary["unique_exact_request_key_count"] == 4
    assert summary["exact_duplicate_group_count"] == 1
    assert summary["exact_duplicate_excess_request_count"] == 1
    assert summary["same_scan_window_group_count"] == 1
    assert summary["same_scan_window_request_count"] == 3
    assert summary["near_redundant_group_count"] == 1
    assert summary["near_redundant_unique_key_count"] == 2
    assert summary["samples"]["sample-a"]["top_exact_duplicate_keys"] == [
        {
            "count": 2,
            "key": {
                "mz": 100.0,
                "ppm_tol": 20.0,
                "rt_max": 2.0,
                "rt_min": 0.0,
                "sample_stem": "sample-a",
            },
        },
    ]


def test_summarize_locality_same_scan_window_ignores_exact_duplicates(
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "sample-a.raw"
    raw_path.write_text("raw", encoding="utf-8")
    records = (
        locality.RequestRecord(
            stage="owner_backfill",
            sample_stem="sample-a",
            mz=100.0,
            rt_min=0.0,
            rt_max=2.0,
            ppm_tol=20.0,
        ),
        locality.RequestRecord(
            stage="owner_backfill",
            sample_stem="sample-a",
            mz=100.0,
            rt_min=0.0,
            rt_max=2.0,
            ppm_tol=20.0,
        ),
    )

    summary = locality.summarize_locality(
        records,
        raw_paths={"sample-a": raw_path},
        dll_dir=tmp_path / "dll",
        batch_size=64,
        open_raw_func=fake_open_raw,
    )

    assert summary["exact_duplicate_group_count"] == 1
    assert summary["same_scan_window_group_count"] == 0
    assert summary["same_scan_window_unique_key_count"] == 0


def test_summarize_locality_keeps_transitive_near_redundant_groups(
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "sample-a.raw"
    raw_path.write_text("raw", encoding="utf-8")
    records = (
        locality.RequestRecord(
            stage="owner_backfill",
            sample_stem="sample-a",
            mz=100.0,
            rt_min=0.8,
            rt_max=1.2,
            ppm_tol=20.0,
        ),
        locality.RequestRecord(
            stage="owner_backfill",
            sample_stem="sample-a",
            mz=100.001,
            rt_min=1.2,
            rt_max=1.6,
            ppm_tol=20.0,
        ),
        locality.RequestRecord(
            stage="owner_backfill",
            sample_stem="sample-a",
            mz=100.002,
            rt_min=1.6,
            rt_max=2.0,
            ppm_tol=20.0,
        ),
    )

    summary = locality.summarize_locality(
        records,
        raw_paths={"sample-a": raw_path},
        dll_dir=tmp_path / "dll",
        batch_size=64,
        open_raw_func=fake_open_raw,
        near_mz_ppm=15.0,
        near_rt_sec=30.0,
    )

    assert summary["near_redundant_group_count"] == 1
    assert summary["near_redundant_unique_key_count"] == 3
    assert summary["near_redundant_excess_key_count"] == 2


def test_collects_build_owner_and_backfill_requests_from_artifacts(
    tmp_path: Path,
) -> None:
    candidate_csv = tmp_path / "sample-a-candidates.csv"
    _write_csv(
        candidate_csv,
        (
            {
                "sample_stem": "sample-a",
                "raw_file": str(tmp_path / "sample-a.raw"),
                "candidate_id": "sample-a#1",
                "precursor_mz": "100.0",
                "best_seed_rt": "5.0",
                "ms1_apex_rt": "",
            },
        ),
    )
    batch_index = tmp_path / "batch.csv"
    _write_csv(
        batch_index,
        (
            {
                "sample_stem": "sample-a",
                "raw_file": str(tmp_path / "sample-a.raw"),
                "candidate_csv": str(candidate_csv),
            },
            {
                "sample_stem": "sample-b",
                "raw_file": str(tmp_path / "sample-b.raw"),
                "candidate_csv": str(tmp_path / "sample-b-candidates.csv"),
            },
        ),
    )
    review_tsv = tmp_path / "alignment_review.tsv"
    _write_tsv(
        review_tsv,
        (
            {
                "feature_id": "FAM000001",
                "family_center_mz": "300.0",
                "family_center_rt": "8.0",
            },
        ),
    )
    cells_tsv = tmp_path / "alignment_cells.tsv"
    _write_tsv(
        cells_tsv,
        (
            {
                "feature_id": "FAM000001",
                "sample_stem": "sample-a",
                "status": "detected",
            },
        ),
    )

    batch = locality.read_batch_index(batch_index, tmp_path)
    build_requests = locality.collect_build_owner_requests(
        batch,
        max_rt_sec=120.0,
        preferred_ppm=20.0,
    )
    backfill_requests = locality.collect_owner_backfill_requests(
        batch,
        alignment_review=review_tsv,
        alignment_cells=cells_tsv,
        max_rt_sec=120.0,
        preferred_ppm=20.0,
        owner_backfill_min_detected_samples=1,
    )

    assert build_requests == (
        locality.RequestRecord(
            stage="build_owners",
            sample_stem="sample-a",
            mz=100.0,
            rt_min=3.0,
            rt_max=7.0,
            ppm_tol=20.0,
        ),
    )
    assert backfill_requests == (
        locality.RequestRecord(
            stage="owner_backfill",
            sample_stem="sample-b",
            mz=300.0,
            rt_min=6.0,
            rt_max=10.0,
            ppm_tol=20.0,
        ),
    )


class FakeRawHandle:
    def __init__(self, _path: Path, _dll_dir: Path) -> None:
        self._raw_file = self

    def __enter__(self) -> "FakeRawHandle":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def ScanNumberFromRetentionTime(self, rt: float) -> int:
        return int(round(rt * 10.0))


def fake_open_raw(path: Path, dll_dir: Path) -> FakeRawHandle:
    return FakeRawHandle(path, dll_dir)


def _write_csv(path: Path, rows: tuple[dict[str, str], ...]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_tsv(path: Path, rows: tuple[dict[str, str], ...]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
