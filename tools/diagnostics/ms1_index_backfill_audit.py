from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from scripts import analyze_xic_request_locality as locality
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.ms1_index_source import (
    MS1IndexIntensityMode,
    RawSuperWindowSource,
    build_ms1_scan_index,
    extract_index_xic,
    read_ms1_scan_index_npz,
    write_ms1_scan_index_npz,
)
from xic_extractor.config import ExtractionConfig
from xic_extractor.ms1_scan_index import MS1ScanIndexProvenance
from xic_extractor.raw_reader import open_raw
from xic_extractor.settings_schema import CANONICAL_SETTINGS_DEFAULTS
from xic_extractor.signal_processing import find_peak_and_area
from xic_extractor.tabular_io import file_sha256, write_tsv
from xic_extractor.xic_models import XICRequest, XICTrace

_MODES: tuple[MS1IndexIntensityMode, ...] = ("max", "sum")
_VENDOR_EXTRACTION_MODES = ("direct", "raw-superwindow")


@dataclass(frozen=True)
class AuditOutputs:
    summary_tsv: Path
    examples_tsv: Path
    json_path: Path


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    outputs, result = run_backfill_audit(
        discovery_batch_index=args.discovery_batch_index,
        raw_dir=args.raw_dir,
        dll_dir=args.dll_dir,
        alignment_review=args.alignment_review,
        alignment_cells=args.alignment_cells,
        output_dir=args.output_dir,
        sample_count=args.sample_count,
        request_count_per_sample=args.request_count_per_sample,
        example_limit=args.example_limit,
        max_rt_sec=args.max_rt_sec,
        preferred_ppm=args.preferred_ppm,
        owner_backfill_min_detected_samples=args.owner_backfill_min_detected_samples,
        index_cache_dir=args.index_cache_dir,
        cache_warm_repeats=args.cache_warm_repeats,
        vendor_extraction_mode=args.vendor_extraction_mode,
    )
    print(f"Summary TSV: {outputs.summary_tsv}")
    print(f"Examples TSV: {outputs.examples_tsv}")
    print(f"Audit JSON: {outputs.json_path}")
    print(json.dumps(result["aggregate"], indent=2, sort_keys=True))
    return 0


def run_backfill_audit(
    *,
    discovery_batch_index: Path,
    raw_dir: Path,
    dll_dir: Path,
    alignment_review: Path,
    alignment_cells: Path,
    output_dir: Path,
    sample_count: int | None = None,
    request_count_per_sample: int | None = None,
    example_limit: int = 20,
    max_rt_sec: float = AlignmentConfig().max_rt_sec,
    preferred_ppm: float = AlignmentConfig().preferred_ppm,
    owner_backfill_min_detected_samples: int = 1,
    index_cache_dir: Path | None = None,
    cache_warm_repeats: int = 1,
    vendor_extraction_mode: str = "direct",
) -> tuple[AuditOutputs, dict[str, Any]]:
    batch = locality.read_batch_index(discovery_batch_index, raw_dir)
    records = locality.collect_owner_backfill_requests(
        batch,
        alignment_review=alignment_review,
        alignment_cells=alignment_cells,
        max_rt_sec=max_rt_sec,
        preferred_ppm=preferred_ppm,
        owner_backfill_min_detected_samples=owner_backfill_min_detected_samples,
    )
    records_by_sample = _records_by_sample(records)
    sample_stems = tuple(records_by_sample)
    if sample_count is not None:
        sample_stems = sample_stems[:sample_count]

    peak_config = _peak_config(raw_dir, dll_dir)
    sample_summaries: list[dict[str, Any]] = []
    examples: list[dict[str, Any]] = []
    for sample_stem in sample_stems:
        sample_records = _limited_records(
            records_by_sample[sample_stem],
            request_count_per_sample,
        )
        raw_path = batch.raw_paths.get(sample_stem)
        if raw_path is None or not raw_path.is_file() or not sample_records:
            continue
        requests = tuple(
            XICRequest(
                mz=record.mz,
                rt_min=record.rt_min,
                rt_max=record.rt_max,
                ppm_tol=record.ppm_tol,
            )
            for record in sample_records
        )
        with open_raw(raw_path, dll_dir) as raw:
            index_cache_path = (
                _index_cache_path(index_cache_dir, sample_stem)
                if index_cache_dir is not None
                else None
            )
            summary, sample_examples = audit_requests_for_sample(
                sample_stem=sample_stem,
                raw=raw,
                requests=requests,
                peak_config=peak_config,
                example_limit=example_limit,
                index_cache_path=index_cache_path,
                raw_path=raw_path,
                cache_warm_repeats=cache_warm_repeats,
                vendor_extraction_mode=vendor_extraction_mode,
            )
        sample_summaries.append(summary)
        examples.extend(sample_examples)

    aggregate = _aggregate_sample_summaries(sample_summaries)
    result = {
        "config": {
            "discovery_batch_index": str(discovery_batch_index),
            "raw_dir": str(raw_dir),
            "dll_dir": str(dll_dir),
            "alignment_review": str(alignment_review),
            "alignment_cells": str(alignment_cells),
            "sample_count": sample_count,
            "request_count_per_sample": request_count_per_sample,
            "example_limit": example_limit,
            "max_rt_sec": max_rt_sec,
            "preferred_ppm": preferred_ppm,
            "owner_backfill_min_detected_samples": (
                owner_backfill_min_detected_samples
            ),
            "index_cache_dir": str(index_cache_dir) if index_cache_dir else "",
            "cache_warm_repeats": cache_warm_repeats,
            "vendor_extraction_mode": vendor_extraction_mode,
        },
        "aggregate": aggregate,
        "samples": [_public_sample_summary(sample) for sample in sample_summaries],
        "examples": examples,
    }
    outputs = AuditOutputs(
        summary_tsv=output_dir / "ms1_index_backfill_audit_summary.tsv",
        examples_tsv=output_dir / "ms1_index_backfill_audit_examples.tsv",
        json_path=output_dir / "ms1_index_backfill_audit.json",
    )
    _write_outputs(outputs, result)
    return outputs, result


def audit_requests_for_sample(
    *,
    sample_stem: str,
    raw: Any,
    requests: tuple[XICRequest, ...],
    peak_config: ExtractionConfig,
    example_limit: int = 20,
    index_cache_path: Path | None = None,
    raw_path: Path | None = None,
    cache_warm_repeats: int = 1,
    vendor_extraction_mode: str = "direct",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if cache_warm_repeats < 1:
        raise ValueError("cache_warm_repeats must be >= 1")
    vendor_started = time.perf_counter()
    vendor_traces = _extract_vendor_traces(
        raw,
        requests,
        extraction_mode=vendor_extraction_mode,
    )
    vendor_extract_sec = time.perf_counter() - vendor_started

    index_started = time.perf_counter()
    index = build_ms1_scan_index(raw)
    index_build_sec = time.perf_counter() - index_started
    cache_write_sec: float | None = None
    cache_load_secs: list[float] = []
    warm_index: tuple[Any, ...] | None = None
    cache_manifest: dict[str, Any] | None = None
    if index_cache_path is not None:
        write_started = time.perf_counter()
        provenance = (
            MS1ScanIndexProvenance(
                sample_stem=sample_stem,
                source_path=str(raw_path.resolve()),
                source_sha256=file_sha256(raw_path),
            )
            if raw_path is not None and raw_path.is_file()
            else None
        )
        write_ms1_scan_index_npz(
            index_cache_path,
            index,
            provenance=provenance,
        )
        cache_write_sec = time.perf_counter() - write_started
        cache_manifest = _write_index_cache_manifest(
            index_cache_path,
            sample_stem=sample_stem,
            raw_path=raw_path,
            index=index,
        )
        for _repeat in range(cache_warm_repeats):
            load_started = time.perf_counter()
            warm_index = read_ms1_scan_index_npz(index_cache_path)
            cache_load_secs.append(time.perf_counter() - load_started)

    mode_summaries: dict[str, dict[str, Any]] = {}
    examples: list[dict[str, Any]] = []
    mode_extract_sec: dict[str, float] = {}
    warm_mode_extract_sec: dict[str, float] = {}
    for mode in _MODES:
        local_started = time.perf_counter()
        local_traces = tuple(
            extract_index_xic(raw, index, request, intensity_mode=mode)
            for request in requests
        )
        mode_extract_sec[mode] = time.perf_counter() - local_started
        warm_local_traces: tuple[XICTrace, ...] | None = None
        if warm_index is not None:
            warm_started = time.perf_counter()
            warm_local_traces = tuple(
                extract_index_xic(raw, warm_index, request, intensity_mode=mode)
                for request in requests
            )
            warm_mode_extract_sec[mode] = time.perf_counter() - warm_started
        comparisons = tuple(
            _compare_request(
                vendor,
                local,
                request,
                peak_config=peak_config,
            )
            for vendor, local, request in zip(
                vendor_traces,
                local_traces,
                requests,
                strict=True,
            )
        )
        summary = _summarize_comparisons(comparisons)
        summary["local_extract_sec"] = mode_extract_sec[mode]
        summary["cold_index_total_sec"] = index_build_sec + mode_extract_sec[mode]
        if cache_load_secs:
            warm_load_median = _percentile(cache_load_secs, 50) or 0.0
            summary["warm_cache_load_sec_median"] = warm_load_median
            summary["warm_cache_load_sec_min"] = min(cache_load_secs)
            summary["warm_cache_load_repeat_count"] = len(cache_load_secs)
            summary["warm_cache_local_extract_sec"] = warm_mode_extract_sec[mode]
            summary["warm_cache_total_sec"] = (
                warm_load_median + warm_mode_extract_sec[mode]
            )
            summary["warm_cache_matches_cold_index"] = (
                _traces_equal(local_traces, warm_local_traces)
                if warm_local_traces is not None
                else False
            )
        mode_summaries[mode] = summary
        examples.extend(
            _example_rows(
                sample_stem=sample_stem,
                mode=mode,
                comparisons=comparisons,
                limit=example_limit,
            )
        )
    return (
        {
            "sample_stem": sample_stem,
            "request_count": len(requests),
            "vendor_extract_sec": vendor_extract_sec,
            "vendor_extraction_mode": vendor_extraction_mode,
            "index_build_sec": index_build_sec,
            "cache_write_sec": cache_write_sec,
            "cache_path": str(index_cache_path) if index_cache_path else "",
            "cache_manifest": cache_manifest or {},
            "ms1_scan_count": len(index),
            "modes": mode_summaries,
        },
        sorted(examples, key=_example_sort_key, reverse=True)[:example_limit],
    )


def _extract_vendor_traces(
    raw: Any,
    requests: tuple[XICRequest, ...],
    *,
    extraction_mode: str = "direct",
) -> tuple[XICTrace, ...]:
    if extraction_mode == "raw-superwindow":
        return RawSuperWindowSource(raw).extract_xic_many(requests)
    if extraction_mode != "direct":
        raise ValueError(f"unsupported vendor extraction mode: {extraction_mode}")
    if hasattr(raw, "extract_xic_many"):
        return tuple(raw.extract_xic_many(requests))
    traces: list[XICTrace] = []
    for request in requests:
        traces.append(
            XICTrace.from_arrays(
                *raw.extract_xic(
                    request.mz,
                    request.rt_min,
                    request.rt_max,
                    request.ppm_tol,
                )
            )
        )
    return tuple(traces)


def _compare_request(
    vendor: XICTrace,
    local: XICTrace,
    request: XICRequest,
    *,
    peak_config: ExtractionConfig,
) -> dict[str, Any]:
    length_match = len(vendor.rt) == len(local.rt)
    rt_grid_equal = bool(length_match and np.array_equal(vendor.rt, local.rt))
    preferred_rt = (request.rt_min + request.rt_max) / 2.0
    vendor_result = find_peak_and_area(
        vendor.rt,
        vendor.intensity,
        peak_config,
        preferred_rt=preferred_rt,
        strict_preferred_rt=False,
    )
    local_result = find_peak_and_area(
        local.rt,
        local.intensity,
        peak_config,
        preferred_rt=preferred_rt,
        strict_preferred_rt=False,
    )
    vendor_peak = vendor_result.peak
    local_peak = local_result.peak
    vendor_area: float | None
    local_area: float | None
    area_relative_delta: float | None
    apex_delta_min: float | None
    if vendor_peak is not None and local_peak is not None:
        vendor_area = vendor_peak.area
        local_area = local_peak.area
        area_relative_delta = (
            abs(local_area - vendor_area) / abs(vendor_area)
            if vendor_area not in (None, 0)
            else None
        )
        apex_delta_min = abs(local_peak.rt - vendor_peak.rt)
    else:
        vendor_area = vendor_peak.area if vendor_peak is not None else None
        local_area = local_peak.area if local_peak is not None else None
        area_relative_delta = None
        apex_delta_min = None
    return {
        "mz": request.mz,
        "rt_min": request.rt_min,
        "rt_max": request.rt_max,
        "ppm_tol": request.ppm_tol,
        "trace_length_match": length_match,
        "rt_grid_equal": rt_grid_equal,
        "vendor_status": vendor_result.status,
        "local_status": local_result.status,
        "peak_status_match": vendor_result.status == local_result.status,
        "both_ok": vendor_peak is not None and local_peak is not None,
        "vendor_area": vendor_area,
        "local_area": local_area,
        "area_relative_delta": area_relative_delta,
        "apex_delta_min": apex_delta_min,
    }


def _summarize_comparisons(comparisons: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    area_deltas = [
        item["area_relative_delta"]
        for item in comparisons
        if item["area_relative_delta"] is not None
    ]
    apex_deltas = [
        item["apex_delta_min"]
        for item in comparisons
        if item["apex_delta_min"] is not None
    ]
    return {
        "request_count": len(comparisons),
        "trace_length_match_count": sum(
            1 for item in comparisons if item["trace_length_match"]
        ),
        "rt_grid_equal_count": sum(1 for item in comparisons if item["rt_grid_equal"]),
        "peak_status_match_count": sum(
            1 for item in comparisons if item["peak_status_match"]
        ),
        "peak_both_ok_count": sum(1 for item in comparisons if item["both_ok"]),
        "apex_close_0_01_min_count": sum(
            1 for value in apex_deltas if value <= 0.01
        ),
        "apex_close_0_05_min_count": sum(
            1 for value in apex_deltas if value <= 0.05
        ),
        "apex_delta_min_median": _percentile(apex_deltas, 50),
        "apex_delta_min_p95": _percentile(apex_deltas, 95),
        "area_relative_delta_median": _percentile(area_deltas, 50),
        "area_relative_delta_p95": _percentile(area_deltas, 95),
        "area_relative_delta_max": max(area_deltas) if area_deltas else None,
        "_apex_delta_values": apex_deltas,
        "_area_relative_delta_values": area_deltas,
    }


def _aggregate_sample_summaries(samples: list[dict[str, Any]]) -> dict[str, Any]:
    aggregate: dict[str, Any] = {
        "sample_count": len(samples),
        "request_count": sum(int(item["request_count"]) for item in samples),
        "vendor_extract_sec": sum(
            float(item["vendor_extract_sec"]) for item in samples
        ),
        "index_build_sec": sum(float(item["index_build_sec"]) for item in samples),
        "cache_write_sec": sum(
            float(item.get("cache_write_sec") or 0.0) for item in samples
        ),
        "modes": {},
    }
    for mode in _MODES:
        mode_rows = [item["modes"][mode] for item in samples]
        aggregate["modes"][mode] = _aggregate_mode_rows(mode_rows)
    return aggregate


def _aggregate_mode_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    sum_keys = {
        "request_count",
        "trace_length_match_count",
        "rt_grid_equal_count",
        "peak_status_match_count",
        "peak_both_ok_count",
        "apex_close_0_01_min_count",
        "apex_close_0_05_min_count",
    }
    result: dict[str, Any] = {
        key: sum(int(row.get(key) or 0) for row in rows) for key in sum_keys
    }
    result["local_extract_sec"] = sum(float(row["local_extract_sec"]) for row in rows)
    result["cold_index_total_sec"] = sum(
        float(row.get("cold_index_total_sec") or 0.0) for row in rows
    )
    result["warm_cache_load_sec_median"] = sum(
        float(row.get("warm_cache_load_sec_median") or 0.0) for row in rows
    )
    result["warm_cache_load_sec_min"] = sum(
        float(row.get("warm_cache_load_sec_min") or 0.0) for row in rows
    )
    result["warm_cache_local_extract_sec"] = sum(
        float(row.get("warm_cache_local_extract_sec") or 0.0) for row in rows
    )
    result["warm_cache_total_sec"] = sum(
        float(row.get("warm_cache_total_sec") or 0.0) for row in rows
    )
    result["warm_cache_matches_cold_index_count"] = sum(
        1 for row in rows if row.get("warm_cache_matches_cold_index") is True
    )
    apex_deltas = [
        value
        for row in rows
        for value in row.get("_apex_delta_values", ())
    ]
    area_deltas = [
        value
        for row in rows
        for value in row.get("_area_relative_delta_values", ())
    ]
    result["apex_delta_min_median"] = _percentile(apex_deltas, 50)
    result["apex_delta_min_p95"] = _percentile(apex_deltas, 95)
    result["area_relative_delta_median"] = _percentile(area_deltas, 50)
    result["area_relative_delta_p95"] = _percentile(area_deltas, 95)
    result["area_relative_delta_max"] = max(area_deltas) if area_deltas else None
    return result


def _public_sample_summary(sample: dict[str, Any]) -> dict[str, Any]:
    public = dict(sample)
    public["modes"] = {
        mode: _public_mode_summary(summary)
        for mode, summary in sample["modes"].items()
    }
    return public


def _public_mode_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in summary.items() if not key.startswith("_")}


def _example_rows(
    *,
    sample_stem: str,
    mode: str,
    comparisons: tuple[dict[str, Any], ...],
    limit: int,
) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    rows = []
    for item in comparisons:
        if (
            item["peak_status_match"]
            and (item["area_relative_delta"] or 0.0) <= 0.10
            and (item["apex_delta_min"] or 0.0) <= 0.05
            and item["rt_grid_equal"]
        ):
            continue
        rows.append(
            {
                "sample_stem": sample_stem,
                "mode": mode,
                **item,
            }
        )
    return sorted(rows, key=_example_sort_key, reverse=True)[:limit]


def _example_sort_key(row: dict[str, Any]) -> tuple[float, float, int]:
    status_penalty = 0 if row.get("peak_status_match") else 1
    return (
        float(row.get("area_relative_delta") or 0.0),
        float(row.get("apex_delta_min") or 0.0),
        status_penalty,
    )


def _records_by_sample(
    records: tuple[locality.RequestRecord, ...],
) -> dict[str, tuple[locality.RequestRecord, ...]]:
    grouped: dict[str, list[locality.RequestRecord]] = defaultdict(list)
    for record in records:
        grouped[record.sample_stem].append(record)
    return {sample: tuple(items) for sample, items in grouped.items()}


def _limited_records(
    records: tuple[locality.RequestRecord, ...],
    limit: int | None,
) -> tuple[locality.RequestRecord, ...]:
    if limit is None or limit >= len(records):
        return records
    if limit < 1:
        return ()
    if limit == 1:
        return (records[0],)
    indexes = np.linspace(0, len(records) - 1, num=limit, dtype=int)
    return tuple(records[int(index)] for index in indexes)


def _write_outputs(outputs: AuditOutputs, result: dict[str, Any]) -> None:
    outputs.summary_tsv.parent.mkdir(parents=True, exist_ok=True)
    outputs.json_path.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_summary_tsv(outputs.summary_tsv, result)
    _write_examples_tsv(outputs.examples_tsv, result["examples"])


def _write_summary_tsv(path: Path, result: dict[str, Any]) -> None:
    rows: list[dict[str, Any]] = []
    for mode, summary in result["aggregate"]["modes"].items():
        rows.append({"scope": "aggregate", "sample_stem": "", "mode": mode, **summary})
    for sample in result["samples"]:
        base = {
            "scope": "sample",
            "sample_stem": sample["sample_stem"],
            "vendor_extract_sec": sample["vendor_extract_sec"],
            "vendor_extraction_mode": sample.get("vendor_extraction_mode", ""),
            "index_build_sec": sample["index_build_sec"],
            "cache_write_sec": sample.get("cache_write_sec"),
            "cache_path": sample.get("cache_path", ""),
            "ms1_scan_count": sample["ms1_scan_count"],
        }
        for mode, summary in sample["modes"].items():
            rows.append({**base, "mode": mode, **summary})
    fieldnames = tuple(dict.fromkeys(key for row in rows for key in row))
    write_tsv(path, rows, fieldnames, formatter=_format_tsv_value)


def _write_examples_tsv(path: Path, examples: list[dict[str, Any]]) -> None:
    fieldnames = tuple(dict.fromkeys(key for row in examples for key in row))
    if not fieldnames:
        with path.open("w", newline="", encoding="utf-8") as handle:
            handle.write("\n")
        return
    write_tsv(path, examples, fieldnames, formatter=_format_tsv_value)


def _format_tsv_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _index_cache_path(index_cache_dir: Path, sample_stem: str) -> Path:
    safe_sample = "".join(
        character if character.isalnum() or character in {"-", "_"} else "_"
        for character in sample_stem
    )
    return index_cache_dir / f"{safe_sample}.ms1_index.npz"


def _write_index_cache_manifest(
    index_cache_path: Path,
    *,
    sample_stem: str,
    raw_path: Path | None,
    index: tuple[Any, ...],
) -> dict[str, Any]:
    manifest_path = index_cache_path.with_suffix(".manifest.json")
    raw_stat = raw_path.stat() if raw_path is not None and raw_path.exists() else None
    manifest = {
        "schema_version": "ms1_index_cache_manifest",
        "sample_stem": sample_stem,
        "cache_path": str(index_cache_path),
        "raw_path": str(raw_path) if raw_path is not None else "",
        "raw_size_bytes": raw_stat.st_size if raw_stat is not None else "",
        "raw_mtime_ns": raw_stat.st_mtime_ns if raw_stat is not None else "",
        "ms1_scan_count": len(index),
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def _traces_equal(
    left: tuple[XICTrace, ...],
    right: tuple[XICTrace, ...] | None,
) -> bool:
    if right is None or len(left) != len(right):
        return False
    return all(
        np.array_equal(left_trace.rt, right_trace.rt)
        and np.array_equal(left_trace.intensity, right_trace.intensity)
        for left_trace, right_trace in zip(left, right, strict=True)
    )


def _peak_config(raw_dir: Path, dll_dir: Path) -> ExtractionConfig:
    defaults = CANONICAL_SETTINGS_DEFAULTS
    return ExtractionConfig(
        data_dir=raw_dir,
        dll_dir=dll_dir,
        output_csv=Path("xic_results.csv"),
        diagnostics_csv=Path("xic_diagnostics.csv"),
        smooth_window=int(defaults["smooth_window"]),
        smooth_polyorder=int(defaults["smooth_polyorder"]),
        ms1_morphology_smoothing_window_points=int(
            defaults["ms1_morphology_smoothing_window_points"]
        ),
        peak_rel_height=float(defaults["peak_rel_height"]),
        peak_min_prominence_ratio=float(defaults["peak_min_prominence_ratio"]),
        ms2_precursor_tol_da=float(defaults["ms2_precursor_tol_da"]),
        nl_min_intensity_ratio=float(defaults["nl_min_intensity_ratio"]),
        resolver_mode="local_minimum",
        resolver_chrom_threshold=float(defaults["resolver_chrom_threshold"]),
        resolver_min_search_range_min=float(defaults["resolver_min_search_range_min"]),
        resolver_min_relative_height=float(defaults["resolver_min_relative_height"]),
        resolver_min_absolute_height=float(defaults["resolver_min_absolute_height"]),
        resolver_min_ratio_top_edge=float(defaults["resolver_min_ratio_top_edge"]),
        resolver_peak_duration_min=float(defaults["resolver_peak_duration_min"]),
        resolver_peak_duration_max=float(defaults["resolver_peak_duration_max"]),
        resolver_min_scans=int(defaults["resolver_min_scans"]),
    )


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    clean = [float(value) for value in values if value is not None]
    if not clean:
        return None
    return float(np.percentile(np.asarray(clean, dtype=float), percentile))


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit owner-backfill vendor XIC versus MS1-index XIC.",
    )
    parser.add_argument("--discovery-batch-index", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--dll-dir", type=Path, required=True)
    parser.add_argument("--alignment-review", type=Path, required=True)
    parser.add_argument("--alignment-cells", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--sample-count", type=int)
    parser.add_argument("--request-count-per-sample", type=int)
    parser.add_argument("--example-limit", type=int, default=20)
    parser.add_argument(
        "--max-rt-sec",
        type=float,
        default=AlignmentConfig().max_rt_sec,
    )
    parser.add_argument(
        "--preferred-ppm",
        type=float,
        default=AlignmentConfig().preferred_ppm,
    )
    parser.add_argument(
        "--owner-backfill-min-detected-samples",
        type=int,
        default=1,
    )
    parser.add_argument("--index-cache-dir", type=Path)
    parser.add_argument("--cache-warm-repeats", type=int, default=1)
    parser.add_argument(
        "--vendor-extraction-mode",
        choices=_VENDOR_EXTRACTION_MODES,
        default="direct",
        help=(
            "Vendor trace oracle. Use raw-superwindow to compare against the "
            "current owner_backfill locality strategy instead of exact-window "
            "batch extraction."
        ),
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
