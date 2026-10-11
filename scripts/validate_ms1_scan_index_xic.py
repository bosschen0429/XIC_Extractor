from __future__ import annotations

import argparse
import csv
import json
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.peak_detection.peak_integration import find_peak_and_area
from xic_extractor.raw_reader import open_raw
from xic_extractor.settings_schema import CANONICAL_SETTINGS_DEFAULTS
from xic_extractor.xic_models import XICRequest, XICTrace


@dataclass(frozen=True)
class MS1Scan:
    scan_number: int
    rt: float
    masses: np.ndarray
    intensities: np.ndarray


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    rows = _read_batch_rows(args.discovery_batch_index)
    sample_rows = rows[: args.sample_count]
    peak_config = _peak_config(args.raw_dir, args.dll_dir)
    summaries = []
    aggregate = _empty_aggregate()
    for row in sample_rows:
        sample_stem = _machine_text(row["sample_stem"])
        raw_name = Path(_machine_text(row["raw_file"])).name
        raw_path = args.raw_dir / raw_name
        candidate_csv = Path(_machine_text(row["candidate_csv"]))
        requests = _candidate_requests(
            candidate_csv,
            max_rt_sec=args.max_rt_sec,
            preferred_ppm=args.preferred_ppm,
            limit=args.request_count,
        )
        summary = validate_sample(
            raw_path=raw_path,
            dll_dir=args.dll_dir,
            requests=requests,
            peak_config=peak_config,
        )
        summary["sample_stem"] = sample_stem
        summaries.append(summary)
        _add_to_aggregate(aggregate, summary)
    result = {"samples": summaries, "aggregate": _finalize_aggregate(aggregate)}
    text = json.dumps(result, indent=2, sort_keys=True)
    print(text)
    if args.output_json is not None:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(text + "\n", encoding="utf-8")
    return 0


def validate_sample(
    *,
    raw_path: Path,
    dll_dir: Path,
    requests: tuple[XICRequest, ...],
    peak_config: ExtractionConfig,
) -> dict[str, Any]:
    with open_raw(raw_path, dll_dir) as raw:
        vendor_started = time.perf_counter()
        vendor_traces = tuple(
            XICTrace.from_arrays(*raw.extract_xic(
                request.mz,
                request.rt_min,
                request.rt_max,
                request.ppm_tol,
            ))
            for request in requests
        )
        vendor_sec = time.perf_counter() - vendor_started

        index_started = time.perf_counter()
        index = build_ms1_scan_index(raw)
        index_sec = time.perf_counter() - index_started

        local_started = time.perf_counter()
        local_traces = tuple(
            extract_index_xic(raw, index, request) for request in requests
        )
        local_sec = time.perf_counter() - local_started

    trace_metrics = tuple(
        compare_traces(vendor, local)
        for vendor, local in zip(vendor_traces, local_traces, strict=True)
    )
    peak_metrics = tuple(
        compare_peaks(vendor, local, request, peak_config)
        for vendor, local, request in zip(
            vendor_traces,
            local_traces,
            requests,
            strict=True,
        )
    )
    return {
        "request_count": len(requests),
        "vendor_extract_sec": vendor_sec,
        "index_build_sec": index_sec,
        "local_extract_sec": local_sec,
        "ms1_scan_count": len(index),
        **_summarize_trace_metrics(trace_metrics),
        **_summarize_peak_metrics(peak_metrics),
    }


def build_ms1_scan_index(raw: Any) -> tuple[MS1Scan, ...]:
    raw_file = getattr(raw, "_raw_file", raw)
    header = raw_file.RunHeaderEx
    scans: list[MS1Scan] = []
    for scan_number in range(int(header.FirstSpectrum), int(header.LastSpectrum) + 1):
        filter_obj = raw_file.GetFilterForScanNumber(scan_number)
        if str(getattr(filter_obj, "MSOrder", "")) != "Ms":
            continue
        masses, intensities = _scan_arrays(raw_file, scan_number)
        if masses.ndim != 1 or masses.shape != intensities.shape:
            continue
        scans.append(
            MS1Scan(
                scan_number=scan_number,
                rt=float(raw_file.RetentionTimeFromScanNumber(scan_number)),
                masses=masses,
                intensities=intensities,
            )
        )
    return tuple(scans)


def extract_index_xic(
    raw: Any,
    index: tuple[MS1Scan, ...],
    request: XICRequest,
) -> XICTrace:
    raw_file = getattr(raw, "_raw_file", raw)
    start_scan = int(raw_file.ScanNumberFromRetentionTime(request.rt_min))
    end_scan = int(raw_file.ScanNumberFromRetentionTime(request.rt_max))
    tolerance = request.mz * request.ppm_tol / 1e6
    rt_values: list[float] = []
    intensity_values: list[float] = []
    for scan in index:
        if scan.scan_number < start_scan or scan.scan_number > end_scan:
            continue
        left = int(np.searchsorted(scan.masses, request.mz - tolerance, side="left"))
        right = int(np.searchsorted(scan.masses, request.mz + tolerance, side="right"))
        intensity = (
            float(np.max(scan.intensities[left:right])) if right > left else 0.0
        )
        rt_values.append(scan.rt)
        intensity_values.append(intensity)
    return XICTrace.from_arrays(rt_values, intensity_values)


def compare_traces(vendor: XICTrace, local: XICTrace) -> dict[str, Any]:
    length_match = len(vendor.rt) == len(local.rt)
    rt_grid_equal = bool(length_match and np.array_equal(vendor.rt, local.rt))
    if length_match:
        delta = np.abs(vendor.intensity - local.intensity)
        max_abs_delta = float(np.max(delta)) if len(delta) else 0.0
        vendor_sum = float(np.sum(vendor.intensity))
        local_sum = float(np.sum(local.intensity))
        sum_ratio = local_sum / vendor_sum if vendor_sum else None
        correlation = _correlation(vendor.intensity, local.intensity)
    else:
        max_abs_delta = None
        sum_ratio = None
        correlation = None
    return {
        "length_match": length_match,
        "rt_grid_equal": rt_grid_equal,
        "max_abs_intensity_delta": max_abs_delta,
        "intensity_sum_ratio": sum_ratio,
        "correlation": correlation,
    }


def compare_peaks(
    vendor: XICTrace,
    local: XICTrace,
    request: XICRequest,
    peak_config: ExtractionConfig,
) -> dict[str, Any]:
    preferred_rt = (request.rt_min + request.rt_max) / 2.0
    vendor_peak = find_peak_and_area(
        vendor.rt,
        vendor.intensity,
        peak_config,
        preferred_rt=preferred_rt,
        strict_preferred_rt=False,
    )
    local_peak = find_peak_and_area(
        local.rt,
        local.intensity,
        peak_config,
        preferred_rt=preferred_rt,
        strict_preferred_rt=False,
    )
    status_match = vendor_peak.status == local_peak.status
    if vendor_peak.peak is None or local_peak.peak is None:
        return {
            "status_match": status_match,
            "both_ok": False,
            "apex_delta_min": None,
            "area_relative_delta": None,
        }
    area = vendor_peak.peak.area
    area_delta = (
        abs(local_peak.peak.area - area) / abs(area)
        if area is not None and area != 0
        else None
    )
    return {
        "status_match": status_match,
        "both_ok": True,
        "apex_delta_min": abs(local_peak.peak.rt - vendor_peak.peak.rt),
        "area_relative_delta": area_delta,
    }


def _scan_arrays(raw_file: Any, scan_number: int) -> tuple[np.ndarray, np.ndarray]:
    if hasattr(raw_file, "GetSegmentedScanFromScanNumber"):
        scan = raw_file.GetSegmentedScanFromScanNumber(scan_number, None)
        masses = getattr(scan, "Positions", [])
    else:
        scan = raw_file.GetSimplifiedScan(scan_number)
        masses = getattr(scan, "Masses", [])
    return (
        np.asarray(masses, dtype=float),
        np.asarray(getattr(scan, "Intensities", []), dtype=float),
    )


def _candidate_requests(
    candidate_csv: Path,
    *,
    max_rt_sec: float,
    preferred_ppm: float,
    limit: int,
) -> tuple[XICRequest, ...]:
    rt_window_min = max_rt_sec / 60.0
    requests: list[XICRequest] = []
    with candidate_csv.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            seed_rt = _first_float(row, ("best_seed_rt", "ms1_apex_rt"))
            mz = _optional_float(row.get("precursor_mz", ""))
            if seed_rt is None or mz is None:
                continue
            requests.append(
                XICRequest(
                    mz=mz,
                    rt_min=seed_rt - rt_window_min,
                    rt_max=seed_rt + rt_window_min,
                    ppm_tol=preferred_ppm,
                )
            )
            if len(requests) >= limit:
                break
    return tuple(requests)


def _summarize_trace_metrics(metrics: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    ratios = [
        item["intensity_sum_ratio"]
        for item in metrics
        if item["intensity_sum_ratio"] is not None
    ]
    correlations = [
        item["correlation"]
        for item in metrics
        if item["correlation"] is not None
    ]
    max_deltas = [
        item["max_abs_intensity_delta"]
        for item in metrics
        if item["max_abs_intensity_delta"] is not None
    ]
    return {
        "trace_length_match_count": sum(1 for item in metrics if item["length_match"]),
        "rt_grid_equal_count": sum(1 for item in metrics if item["rt_grid_equal"]),
        "intensity_sum_ratio_median": _percentile(ratios, 50),
        "correlation_median": _percentile(correlations, 50),
        "max_abs_intensity_delta_p95": _percentile(max_deltas, 95),
    }


def _summarize_peak_metrics(metrics: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    apex_deltas = [
        item["apex_delta_min"]
        for item in metrics
        if item["apex_delta_min"] is not None
    ]
    area_deltas = [
        item["area_relative_delta"]
        for item in metrics
        if item["area_relative_delta"] is not None
    ]
    return {
        "peak_status_match_count": sum(1 for item in metrics if item["status_match"]),
        "peak_both_ok_count": sum(1 for item in metrics if item["both_ok"]),
        "apex_close_0_01_min_count": sum(
            1 for value in apex_deltas if value <= 0.01
        ),
        "apex_close_0_05_min_count": sum(
            1 for value in apex_deltas if value <= 0.05
        ),
        "apex_delta_min_max": max(apex_deltas) if apex_deltas else None,
        "area_relative_delta_median": _percentile(area_deltas, 50),
        "area_relative_delta_p95": _percentile(area_deltas, 95),
        "area_relative_delta_max": max(area_deltas) if area_deltas else None,
    }


def _empty_aggregate() -> dict[str, list[Any]]:
    return {
        "request_count": [],
        "vendor_extract_sec": [],
        "index_build_sec": [],
        "local_extract_sec": [],
        "ms1_scan_count": [],
        "trace_length_match_count": [],
        "rt_grid_equal_count": [],
        "peak_status_match_count": [],
        "peak_both_ok_count": [],
        "apex_close_0_01_min_count": [],
        "apex_close_0_05_min_count": [],
        "area_relative_delta_median": [],
        "area_relative_delta_p95": [],
        "area_relative_delta_max": [],
    }


def _add_to_aggregate(aggregate: dict[str, list[Any]], summary: dict[str, Any]) -> None:
    for key in aggregate:
        aggregate[key].append(summary.get(key))


def _finalize_aggregate(aggregate: dict[str, list[Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    sum_keys = {
        "request_count",
        "vendor_extract_sec",
        "index_build_sec",
        "local_extract_sec",
        "ms1_scan_count",
        "trace_length_match_count",
        "rt_grid_equal_count",
        "peak_status_match_count",
        "peak_both_ok_count",
        "apex_close_0_01_min_count",
        "apex_close_0_05_min_count",
    }
    for key, values in aggregate.items():
        clean = [value for value in values if value is not None]
        if key in sum_keys:
            result[key] = sum(clean) if clean else 0
        else:
            result[key] = _percentile(clean, 50)
    return result


def _peak_config(raw_dir: Path, dll_dir: Path) -> ExtractionConfig:
    defaults = CANONICAL_SETTINGS_DEFAULTS
    return ExtractionConfig(
        data_dir=raw_dir,
        dll_dir=dll_dir,
        output_csv=Path("xic_results.csv"),
        diagnostics_csv=Path("xic_diagnostics.csv"),
        smooth_window=15,
        smooth_polyorder=3,
        ms1_morphology_smoothing_window_points=int(
            defaults["signal_processing_window_points"]
        ),
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.10,
        ms2_precursor_tol_da=float(defaults["ms2_precursor_tol_da"]),
        nl_min_intensity_ratio=float(defaults["nl_min_intensity_ratio"]),
        resolver_mode="local_minimum",
    )


def _read_batch_rows(path: Path) -> tuple[dict[str, str], ...]:
    with path.open(newline="", encoding="utf-8") as handle:
        return tuple(csv.DictReader(handle))


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


def _correlation(left: np.ndarray, right: np.ndarray) -> float | None:
    if len(left) == 0 or len(right) == 0:
        return None
    if float(np.std(left)) == 0.0 or float(np.std(right)) == 0.0:
        return None
    return float(np.corrcoef(left, right)[0, 1])


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    return float(np.percentile(np.asarray(values, dtype=float), percentile))


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate local MS1 scan-index XIC against vendor XIC.",
    )
    parser.add_argument("--discovery-batch-index", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--dll-dir", type=Path, required=True)
    parser.add_argument("--sample-count", type=int, default=1)
    parser.add_argument("--request-count", type=int, default=100)
    parser.add_argument("--max-rt-sec", type=float, default=180.0)
    parser.add_argument("--preferred-ppm", type=float, default=20.0)
    parser.add_argument("--output-json", type=Path)
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
