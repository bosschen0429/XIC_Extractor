from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xic_extractor.raw_reader import open_raw


@dataclass(frozen=True)
class BatchArtifacts:
    sample_order: tuple[str, ...]
    raw_paths: dict[str, Path]
    candidate_csvs: dict[str, Path]


@dataclass(frozen=True)
class RequestRecord:
    stage: str
    sample_stem: str
    mz: float
    rt_min: float
    rt_max: float
    ppm_tol: float


RequestKey = tuple[str, float, float, float, float]


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    batch = read_batch_index(args.discovery_batch_index, args.raw_dir)
    build_requests = collect_build_owner_requests(
        batch,
        max_rt_sec=args.max_rt_sec,
        preferred_ppm=args.preferred_ppm,
    )
    result: dict[str, Any] = {
        "raw_xic_batch_size": args.raw_xic_batch_size,
        "stages": {
            "build_owners": summarize_locality(
                build_requests,
                raw_paths=batch.raw_paths,
                dll_dir=args.dll_dir,
                batch_size=args.raw_xic_batch_size,
                near_mz_ppm=args.near_mz_ppm,
                near_rt_sec=args.near_rt_sec,
                example_limit=args.census_example_limit,
            )
        },
    }
    if args.alignment_review is not None and args.alignment_cells is not None:
        backfill_requests = collect_owner_backfill_requests(
            batch,
            alignment_review=args.alignment_review,
            alignment_cells=args.alignment_cells,
            max_rt_sec=args.max_rt_sec,
            preferred_ppm=args.preferred_ppm,
            owner_backfill_min_detected_samples=(
                args.owner_backfill_min_detected_samples
            ),
        )
        result["stages"]["owner_backfill"] = summarize_locality(
            backfill_requests,
            raw_paths=batch.raw_paths,
            dll_dir=args.dll_dir,
            batch_size=args.raw_xic_batch_size,
            near_mz_ppm=args.near_mz_ppm,
            near_rt_sec=args.near_rt_sec,
            example_limit=args.census_example_limit,
        )
    text = json.dumps(result, indent=2, sort_keys=True)
    print(text)
    if args.output_json is not None:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(text + "\n", encoding="utf-8")
    return 0


def read_batch_index(path: Path, raw_dir: Path) -> BatchArtifacts:
    rows = _read_delimited_rows(path, delimiter=",")
    sample_order: list[str] = []
    raw_paths: dict[str, Path] = {}
    candidate_csvs: dict[str, Path] = {}
    for row in rows:
        sample_stem = _machine_text(row.get("sample_stem", ""))
        if not sample_stem:
            continue
        sample_order.append(sample_stem)
        raw_file = Path(_machine_text(row.get("raw_file", "")))
        raw_name = raw_file.name if raw_file.name else f"{sample_stem}.raw"
        raw_paths[sample_stem] = raw_dir / raw_name
        candidate_csv = Path(_machine_text(row.get("candidate_csv", "")))
        if not candidate_csv.is_absolute():
            candidate_csv = path.parent / candidate_csv
        candidate_csvs[sample_stem] = candidate_csv
    return BatchArtifacts(
        sample_order=tuple(sample_order),
        raw_paths=raw_paths,
        candidate_csvs=candidate_csvs,
    )


def collect_build_owner_requests(
    batch: BatchArtifacts,
    *,
    max_rt_sec: float,
    preferred_ppm: float,
) -> tuple[RequestRecord, ...]:
    rt_window_min = max_rt_sec / 60.0
    records: list[RequestRecord] = []
    for sample_stem in batch.sample_order:
        candidate_csv = batch.candidate_csvs.get(sample_stem)
        if candidate_csv is None or not candidate_csv.is_file():
            continue
        for row in _read_delimited_rows(candidate_csv, delimiter=","):
            seed_rt = _first_float(row, ("best_seed_rt", "ms1_apex_rt"))
            mz = _optional_float(row.get("precursor_mz", ""))
            if seed_rt is None or mz is None:
                continue
            records.append(
                RequestRecord(
                    stage="build_owners",
                    sample_stem=sample_stem,
                    mz=mz,
                    rt_min=seed_rt - rt_window_min,
                    rt_max=seed_rt + rt_window_min,
                    ppm_tol=preferred_ppm,
                )
            )
    return tuple(records)


def collect_owner_backfill_requests(
    batch: BatchArtifacts,
    *,
    alignment_review: Path,
    alignment_cells: Path,
    max_rt_sec: float,
    preferred_ppm: float,
    owner_backfill_min_detected_samples: int,
) -> tuple[RequestRecord, ...]:
    detected_by_feature: dict[str, set[str]] = defaultdict(set)
    for row in _read_delimited_rows(alignment_cells, delimiter="\t"):
        sample_stem = row.get("sample_stem", "")
        if row.get("status") == "detected" and sample_stem:
            detected_by_feature[row.get("feature_family_id", "")].add(sample_stem)

    rt_window_min = max_rt_sec / 60.0
    records: list[RequestRecord] = []
    for row in _read_delimited_rows(alignment_review, delimiter="\t"):
        feature_id = row.get("feature_family_id", "")
        detected_samples = detected_by_feature.get(feature_id, set())
        if len(detected_samples) < owner_backfill_min_detected_samples:
            continue
        mz = _optional_float(row.get("family_center_mz", ""))
        rt = _optional_float(row.get("family_center_rt", ""))
        if mz is None or rt is None:
            continue
        for sample_stem in batch.sample_order:
            if sample_stem in detected_samples:
                continue
            if sample_stem not in batch.raw_paths:
                continue
            records.append(
                RequestRecord(
                    stage="owner_backfill",
                    sample_stem=sample_stem,
                    mz=mz,
                    rt_min=rt - rt_window_min,
                    rt_max=rt + rt_window_min,
                    ppm_tol=preferred_ppm,
                )
            )
    return tuple(records)


def summarize_locality(
    records: Sequence[RequestRecord],
    *,
    raw_paths: dict[str, Path],
    dll_dir: Path,
    batch_size: int,
    near_mz_ppm: float = 20.0,
    near_rt_sec: float = 30.0,
    example_limit: int = 10,
    open_raw_func: Callable[[Path, Path], Any] = open_raw,
) -> dict[str, Any]:
    if batch_size < 1:
        raise ValueError("raw_xic_batch_size must be >= 1")
    if near_mz_ppm <= 0:
        raise ValueError("near_mz_ppm must be > 0")
    if near_rt_sec < 0:
        raise ValueError("near_rt_sec must be >= 0")
    if example_limit < 0:
        raise ValueError("census_example_limit must be >= 0")
    by_sample: dict[str, list[RequestRecord]] = defaultdict(list)
    for record in records:
        by_sample[record.sample_stem].append(record)

    sample_summaries: dict[str, dict[str, int]] = {}
    totals = {
        "request_count": 0,
        "original_chunk_call_count": 0,
        "sorted_chunk_call_count": 0,
        "upper_bound_call_count": 0,
        "unique_scan_window_count": 0,
        "unique_exact_request_key_count": 0,
        "exact_duplicate_group_count": 0,
        "exact_duplicate_excess_request_count": 0,
        "same_scan_window_group_count": 0,
        "same_scan_window_request_count": 0,
        "same_scan_window_unique_key_count": 0,
        "same_scan_window_excess_key_count": 0,
        "near_redundant_group_count": 0,
        "near_redundant_unique_key_count": 0,
        "near_redundant_excess_key_count": 0,
    }
    for sample_stem, sample_records in by_sample.items():
        raw_path = raw_paths.get(sample_stem)
        if raw_path is None:
            continue
        with open_raw_func(raw_path, dll_dir) as raw:
            windows = tuple(_scan_window(raw, record) for record in sample_records)
        original_calls = _chunked_unique_count(windows, batch_size)
        sorted_windows = tuple(sorted(windows))
        sorted_calls = _chunked_unique_count(sorted_windows, batch_size)
        upper_bound = len(set(windows))
        exact_census = _summarize_exact_request_keys(
            sample_records,
            limit=example_limit,
        )
        same_scan_window_census = _summarize_same_scan_windows(
            sample_records,
            windows,
            limit=example_limit,
        )
        near_redundant_census = _summarize_near_redundant_keys(
            sample_records,
            near_mz_ppm=near_mz_ppm,
            near_rt_sec=near_rt_sec,
            limit=example_limit,
        )
        sample_summary = {
            "request_count": len(windows),
            "original_chunk_call_count": original_calls,
            "sorted_chunk_call_count": sorted_calls,
            "upper_bound_call_count": upper_bound,
            "unique_scan_window_count": upper_bound,
            **exact_census,
            **same_scan_window_census,
            **near_redundant_census,
        }
        sample_summaries[sample_stem] = sample_summary
        for key in totals:
            totals[key] += sample_summary[key]
    totals["sample_count"] = len(sample_summaries)
    return {**totals, "samples": sample_summaries}


def _scan_window(raw: Any, record: RequestRecord) -> tuple[int, int]:
    raw_file = getattr(raw, "_raw_file", raw)
    return (
        int(raw_file.ScanNumberFromRetentionTime(record.rt_min)),
        int(raw_file.ScanNumberFromRetentionTime(record.rt_max)),
    )


def _chunked_unique_count(windows: tuple[tuple[int, int], ...], chunk_size: int) -> int:
    total = 0
    for index in range(0, len(windows), chunk_size):
        total += len(set(windows[index : index + chunk_size]))
    return total


def _summarize_exact_request_keys(
    records: Sequence[RequestRecord],
    *,
    limit: int,
) -> dict[str, Any]:
    counts = Counter(_request_key(record) for record in records)
    duplicate_items = sorted(
        ((key, count) for key, count in counts.items() if count > 1),
        key=lambda item: (-item[1], item[0]),
    )
    return {
        "unique_exact_request_key_count": len(counts),
        "exact_duplicate_group_count": len(duplicate_items),
        "exact_duplicate_excess_request_count": sum(
            count - 1 for _key, count in duplicate_items
        ),
        "top_exact_duplicate_keys": [
            {"key": _request_key_payload(key), "count": count}
            for key, count in duplicate_items[:limit]
        ],
    }


def _summarize_same_scan_windows(
    records: Sequence[RequestRecord],
    windows: tuple[tuple[int, int], ...],
    *,
    limit: int,
) -> dict[str, Any]:
    grouped: dict[tuple[int, int], list[RequestRecord]] = defaultdict(list)
    for record, window in zip(records, windows, strict=True):
        grouped[window].append(record)
    redundant_groups: list[
        tuple[tuple[int, int], list[RequestRecord], set[RequestKey]]
    ] = []
    for window, group in grouped.items():
        keys = {_request_key(record) for record in group}
        if len(keys) > 1:
            redundant_groups.append((window, group, keys))
    redundant_groups.sort(
        key=lambda item: (-len(item[1]), -len(item[2]), item[0]),
    )
    return {
        "same_scan_window_group_count": len(redundant_groups),
        "same_scan_window_request_count": sum(
            len(group) for _window, group, _keys in redundant_groups
        ),
        "same_scan_window_unique_key_count": sum(
            len(keys) for _window, _group, keys in redundant_groups
        ),
        "same_scan_window_excess_key_count": sum(
            len(keys) - 1 for _window, _group, keys in redundant_groups
        ),
        "top_same_scan_window_groups": [
            {
                "scan_window": {
                    "start_scan": window[0],
                    "end_scan": window[1],
                },
                "request_count": len(group),
                "unique_exact_request_key_count": len(keys),
                "example_keys": [
                    _request_key_payload(key) for key in sorted(keys)[:limit]
                ],
            }
            for window, group, keys in redundant_groups[:limit]
        ],
    }


def _summarize_near_redundant_keys(
    records: Sequence[RequestRecord],
    *,
    near_mz_ppm: float,
    near_rt_sec: float,
    limit: int,
) -> dict[str, Any]:
    unique_records = tuple(_unique_key_records(records))
    parent = list(range(len(unique_records)))
    indexed_by_rt_center = tuple(
        sorted(
            enumerate(unique_records),
            key=lambda item: (_rt_center(item[1]), _request_key(item[1])),
        )
    )

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    near_rt_min = near_rt_sec / 60.0
    for sorted_left_index, (left_index, left) in enumerate(indexed_by_rt_center):
        left_center = _rt_center(left)
        for sorted_right_index in range(
            sorted_left_index + 1,
            len(indexed_by_rt_center),
        ):
            right_index, right = indexed_by_rt_center[sorted_right_index]
            if _rt_center(right) - left_center > near_rt_min:
                break
            if _near_redundant(left, right, near_mz_ppm, near_rt_sec):
                union(left_index, right_index)

    components: dict[int, list[RequestRecord]] = defaultdict(list)
    for index, record in enumerate(unique_records):
        components[find(index)].append(record)
    redundant_components = [
        component for component in components.values() if len(component) > 1
    ]
    redundant_components.sort(
        key=lambda component: (-len(component), _request_key(component[0])),
    )
    return {
        "near_redundant_group_count": len(redundant_components),
        "near_redundant_unique_key_count": sum(
            len(component) for component in redundant_components
        ),
        "near_redundant_excess_key_count": sum(
            len(component) - 1 for component in redundant_components
        ),
        "top_near_redundant_groups": [
            _near_redundant_group_payload(component, limit=limit)
            for component in redundant_components[:limit]
        ],
    }


def _unique_key_records(records: Sequence[RequestRecord]) -> tuple[RequestRecord, ...]:
    seen: set[tuple[str, float, float, float, float]] = set()
    unique: list[RequestRecord] = []
    for record in records:
        key = _request_key(record)
        if key in seen:
            continue
        seen.add(key)
        unique.append(record)
    return tuple(unique)


def _near_redundant(
    left: RequestRecord,
    right: RequestRecord,
    near_mz_ppm: float,
    near_rt_sec: float,
) -> bool:
    if left.sample_stem != right.sample_stem:
        return False
    if _request_key(left) == _request_key(right):
        return False
    if _ppm(left.mz, right.mz) > near_mz_ppm:
        return False
    return abs(_rt_center(left) - _rt_center(right)) * 60.0 <= near_rt_sec


def _near_redundant_group_payload(
    component: list[RequestRecord],
    *,
    limit: int,
) -> dict[str, Any]:
    sorted_component = sorted(component, key=_request_key)
    mz_values = [record.mz for record in sorted_component]
    rt_centers = [_rt_center(record) for record in sorted_component]
    return {
        "request_count": len(sorted_component),
        "sample_stem": sorted_component[0].sample_stem,
        "mz_min": min(mz_values),
        "mz_max": max(mz_values),
        "rt_center_min": min(rt_centers),
        "rt_center_max": max(rt_centers),
        "example_keys": [
            _request_key_payload(_request_key(record))
            for record in sorted_component[:limit]
        ],
    }


def _request_key(record: RequestRecord) -> RequestKey:
    return (
        record.sample_stem,
        record.mz,
        record.rt_min,
        record.rt_max,
        record.ppm_tol,
    )


def _request_key_payload(key: RequestKey) -> dict[str, float | str]:
    sample_stem, mz, rt_min, rt_max, ppm_tol = key
    return {
        "sample_stem": sample_stem,
        "mz": mz,
        "rt_min": rt_min,
        "rt_max": rt_max,
        "ppm_tol": ppm_tol,
    }


def _rt_center(record: RequestRecord) -> float:
    return (record.rt_min + record.rt_max) / 2.0


def _ppm(left: float, right: float) -> float:
    denominator = max(abs(left), 1e-12)
    return abs(left - right) / denominator * 1_000_000.0


def _read_delimited_rows(path: Path, *, delimiter: str) -> tuple[dict[str, str], ...]:
    with path.open(newline="", encoding="utf-8") as handle:
        return tuple(csv.DictReader(handle, delimiter=delimiter))


def _first_float(row: dict[str, str], columns: tuple[str, ...]) -> float | None:
    for column in columns:
        value = _optional_float(row.get(column, ""))
        if value is not None:
            return value
    return None


def _optional_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(_machine_text(value))
    except ValueError:
        return None


def _machine_text(value: str) -> str:
    if len(value) >= 2 and value[0] == "'" and value[1] in ("=", "+", "-", "@"):
        return value[1:]
    return value


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze RAW XIC request scan-window locality.",
    )
    parser.add_argument("--discovery-batch-index", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--dll-dir", type=Path, required=True)
    parser.add_argument("--alignment-review", type=Path)
    parser.add_argument("--alignment-cells", type=Path)
    parser.add_argument("--max-rt-sec", type=float, default=180.0)
    parser.add_argument("--preferred-ppm", type=float, default=20.0)
    parser.add_argument("--raw-xic-batch-size", type=int, default=64)
    parser.add_argument("--near-mz-ppm", type=float, default=20.0)
    parser.add_argument("--near-rt-sec", type=float, default=30.0)
    parser.add_argument("--census-example-limit", type=int, default=10)
    parser.add_argument("--owner-backfill-min-detected-samples", type=int, default=1)
    parser.add_argument("--output-json", type=Path)
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
