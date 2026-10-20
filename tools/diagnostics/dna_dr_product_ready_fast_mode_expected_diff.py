"""Build an expected-diff packet for dna_dr_product_ready fast-mode candidates."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "dna_dr_product_ready_fast_mode_expected_diff"
PUBLIC_FILES = (
    "alignment_matrix.tsv",
    "alignment_review.tsv",
    "alignment_matrix_identity.tsv",
    "alignment_backfill_cell_evidence.tsv",
)
MATRIX_COORD_COLUMNS = {"Mz", "RT"}
CELL_NUMERIC_COLUMNS = (
    "area",
    "primary_matrix_area",
    "apex_rt",
    "height",
    "peak_start_rt",
    "peak_end_rt",
)
CELL_STATUS_COLUMNS = (
    "status",
    "production_cell_status",
    "backfill_tier",
    "write_matrix_value",
    "include_in_primary_matrix",
    "identity_decision",
)
REVIEW_COUNT_COLUMNS = (
    "detected_count",
    "absent_count",
    "unchecked_count",
    "duplicate_assigned_count",
    "ambiguous_ms1_owner_count",
    "quantifiable_detected_count",
    "quantifiable_backfill_count",
    "accepted_cell_count",
    "accepted_backfill_count",
    "review_backfill_count",
)
REVIEW_STATUS_COLUMNS = (
    "identity_decision",
    "identity_confidence",
    "primary_evidence",
    "identity_reason",
    "include_in_primary_matrix",
    "row_flags",
    "reason",
)
IDENTITY_STATUS_COLUMNS = (
    "row_identity_basis",
    "split_evaluation_status",
    "projection_status",
    "source_feature_family_ids",
    "evidence_status",
)
TIMING_STAGES = (
    "alignment.build_owners",
    "alignment.owner_backfill",
    "alignment.write_outputs",
    "standard_peak.global_overlay_batch",
    "standard_peak.consolidation",
)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    candidates = _parse_named_paths(args.candidate)
    audits = _parse_named_paths(args.audit or ())
    packet = build_expected_diff_packet(
        exact_dir=args.exact_dir,
        candidates=candidates,
        audits=audits,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    packet_json = args.output_dir / "dna_dr_product_ready_fast_mode_expected_diff.json"
    packet_json.write_text(
        json.dumps(packet, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    summary_tsv = args.output_dir / "dna_dr_product_ready_fast_mode_expected_diff.tsv"
    _write_summary_tsv(summary_tsv, packet)
    print(f"Expected-diff packet JSON: {packet_json}")
    print(f"Expected-diff summary TSV: {summary_tsv}")
    return 0


def build_expected_diff_packet(
    *,
    exact_dir: Path,
    candidates: Mapping[str, Path],
    audits: Mapping[str, Path] | None = None,
) -> dict[str, Any]:
    exact = _load_alignment_output(exact_dir)
    audit_map = dict(audits or {})
    candidate_packets = {
        name: _candidate_packet(
            name=name,
            exact=exact,
            candidate=_load_alignment_output(path),
            audit_json=audit_map.get(name),
        )
        for name, path in candidates.items()
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "workload": "FIXTURE_COHORT",
        "lane": "fast_mode_candidate_expected_diff",
        "default_behavior_changed": False,
        "exact_dir": str(exact_dir.resolve()),
        "public_files": list(PUBLIC_FILES),
        "candidates": candidate_packets,
    }


def _candidate_packet(
    *,
    name: str,
    exact: dict[str, Any],
    candidate: dict[str, Any],
    audit_json: Path | None,
) -> dict[str, Any]:
    public_hashes = _public_hash_compare(exact, candidate)
    matrix = _matrix_diff(exact, candidate)
    cells = _cell_evidence_diff(exact, candidate)
    review = _review_diff(exact, candidate)
    identity = _identity_diff(exact, candidate)
    timing = _timing_diff(exact, candidate)
    audit = _audit_summary(audit_json) if audit_json is not None else {}
    result_status, reasons = _classify_candidate(
        public_hashes=public_hashes,
        matrix=matrix,
        cells=cells,
        review=review,
        identity=identity,
        timing=timing,
        audit=audit,
    )
    return {
        "candidate_dir": str(candidate["dir"]),
        "result_status": result_status,
        "decision": (
            "keep_for_explicit_fast_mode_review"
            if result_status == "fast_mode_candidate"
            else "diagnostic_only_do_not_wire_to_default"
        ),
        "decision_reasons": reasons,
        "public_hashes": public_hashes,
        "matrix_diff": matrix,
        "cell_evidence_diff": cells,
        "review_diff": review,
        "matrix_identity_diff": identity,
        "timing_diff": timing,
        "ms1_index_audit": audit,
        "rollback_path": (
            "omit fast-mode config; exact dna_dr_product_ready remains default"
        ),
        "candidate_name": name,
    }


def _load_alignment_output(directory: Path) -> dict[str, Any]:
    directory = directory.resolve()
    missing = [name for name in PUBLIC_FILES if not (directory / name).is_file()]
    if missing:
        raise ValueError(f"{directory}: missing required files: {', '.join(missing)}")
    matrix_rows, matrix_fields = _read_tsv(directory / "alignment_matrix.tsv")
    review_rows, _review_fields = _read_tsv(directory / "alignment_review.tsv")
    identity_rows, _identity_fields = _read_tsv(
        directory / "alignment_matrix_identity.tsv",
    )
    cell_rows, _cell_fields = _read_tsv(
        directory / "alignment_backfill_cell_evidence.tsv",
    )
    return {
        "dir": directory,
        "hashes": {name: _sha256(directory / name) for name in PUBLIC_FILES},
        "matrix_rows": matrix_rows,
        "matrix_fields": matrix_fields,
        "review_by_family": _rows_by_key(review_rows, ("feature_family_id",)),
        "identity_by_peak": _rows_by_key(identity_rows, ("peak_hypothesis_id",)),
        "cell_by_family_sample": _rows_by_key(
            cell_rows,
            ("feature_family_id", "sample_stem"),
        ),
        "matrix_by_peak": _matrix_by_peak(matrix_rows, matrix_fields, identity_rows),
        "timing": _timing_summary(directory / "timing.json"),
    }


def _public_hash_compare(
    exact: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    files: dict[str, bool] = {}
    for name in PUBLIC_FILES:
        files[name] = exact["hashes"].get(name) == candidate["hashes"].get(name)
    return {
        "all_match": all(files.values()),
        "files": files,
    }


def _matrix_diff(
    exact: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    exact_by_peak = exact["matrix_by_peak"]
    cand_by_peak = candidate["matrix_by_peak"]
    exact_keys = set(exact_by_peak)
    cand_keys = set(cand_by_peak)
    common = sorted(exact_keys & cand_keys)
    sample_columns = sorted(
        (set(exact["matrix_fields"]) | set(candidate["matrix_fields"]))
        - MATRIX_COORD_COLUMNS,
    )
    missing_cells = 0
    extra_cells = 0
    both_numeric = 0
    abs_deltas: list[float] = []
    relative_deltas: list[float] = []
    exact_values: list[float] = []
    cand_values: list[float] = []
    for peak_id in common:
        exact_row = exact_by_peak[peak_id]
        cand_row = cand_by_peak[peak_id]
        for sample in sample_columns:
            exact_value = _float_or_none(exact_row.get(sample))
            cand_value = _float_or_none(cand_row.get(sample))
            if exact_value is not None and cand_value is None:
                missing_cells += 1
            elif exact_value is None and cand_value is not None:
                extra_cells += 1
            elif exact_value is not None and cand_value is not None:
                both_numeric += 1
                exact_values.append(exact_value)
                cand_values.append(cand_value)
                abs_deltas.append(abs(cand_value - exact_value))
                if exact_value != 0:
                    relative_deltas.append(
                        abs(cand_value - exact_value) / abs(exact_value),
                    )
    return {
        "exact_row_count": len(exact_keys),
        "candidate_row_count": len(cand_keys),
        "common_peak_hypothesis_count": len(common),
        "missing_peak_hypothesis_count": len(exact_keys - cand_keys),
        "extra_peak_hypothesis_count": len(cand_keys - exact_keys),
        "missing_matrix_cell_count": missing_cells,
        "extra_matrix_cell_count": extra_cells,
        "common_numeric_cell_count": both_numeric,
        "area_abs_delta_median": _percentile(abs_deltas, 50),
        "area_abs_delta_p95": _percentile(abs_deltas, 95),
        "area_relative_delta_median": _percentile(relative_deltas, 50),
        "area_relative_delta_p95": _percentile(relative_deltas, 95),
        "area_correlation": _correlation(exact_values, cand_values),
    }


def _cell_evidence_diff(
    exact: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    return _row_set_diff(
        exact_rows=exact["cell_by_family_sample"],
        candidate_rows=candidate["cell_by_family_sample"],
        status_columns=CELL_STATUS_COLUMNS,
        numeric_columns=CELL_NUMERIC_COLUMNS,
    )


def _identity_diff(
    exact: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    return _row_set_diff(
        exact_rows=exact["identity_by_peak"],
        candidate_rows=candidate["identity_by_peak"],
        status_columns=IDENTITY_STATUS_COLUMNS,
        numeric_columns=(),
    )


def _review_diff(
    exact: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    exact_rows = exact["review_by_family"]
    cand_rows = candidate["review_by_family"]
    base = _row_set_diff(
        exact_rows=exact_rows,
        candidate_rows=cand_rows,
        status_columns=REVIEW_STATUS_COLUMNS,
        numeric_columns=(),
    )
    exact_totals = _sum_review_counts(exact_rows.values())
    cand_totals = _sum_review_counts(cand_rows.values())
    base["counted_detection_delta"] = {
        key: cand_totals.get(key, 0.0) - exact_totals.get(key, 0.0)
        for key in REVIEW_COUNT_COLUMNS
    }
    return base


def _row_set_diff(
    *,
    exact_rows: Mapping[tuple[str, ...], Mapping[str, str]],
    candidate_rows: Mapping[tuple[str, ...], Mapping[str, str]],
    status_columns: Sequence[str],
    numeric_columns: Sequence[str],
) -> dict[str, Any]:
    exact_keys = set(exact_rows)
    cand_keys = set(candidate_rows)
    common = sorted(exact_keys & cand_keys)
    status_delta_counts: dict[str, int] = {}
    for column in status_columns:
        count = sum(
            1
            for key in common
            if (
                _text(exact_rows[key].get(column))
                != _text(candidate_rows[key].get(column))
            )
        )
        if count:
            status_delta_counts[column] = count
    numeric: dict[str, dict[str, Any]] = {}
    for column in numeric_columns:
        abs_deltas: list[float] = []
        relative_deltas: list[float] = []
        missing = 0
        extra = 0
        for key in common:
            exact_value = _float_or_none(exact_rows[key].get(column))
            cand_value = _float_or_none(candidate_rows[key].get(column))
            if exact_value is not None and cand_value is None:
                missing += 1
            elif exact_value is None and cand_value is not None:
                extra += 1
            elif exact_value is not None and cand_value is not None:
                abs_deltas.append(abs(cand_value - exact_value))
                if exact_value != 0:
                    relative_deltas.append(
                        abs(cand_value - exact_value) / abs(exact_value),
                    )
        numeric[column] = {
            "missing_value_count": missing,
            "extra_value_count": extra,
            "abs_delta_median": _percentile(abs_deltas, 50),
            "abs_delta_p95": _percentile(abs_deltas, 95),
            "relative_delta_median": _percentile(relative_deltas, 50),
            "relative_delta_p95": _percentile(relative_deltas, 95),
        }
    return {
        "exact_key_count": len(exact_keys),
        "candidate_key_count": len(cand_keys),
        "common_key_count": len(common),
        "missing_key_count": len(exact_keys - cand_keys),
        "extra_key_count": len(cand_keys - exact_keys),
        "status_delta_counts": status_delta_counts,
        "numeric_drift": numeric,
    }


def _timing_diff(
    exact: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    exact_timing = exact["timing"]
    cand_timing = candidate["timing"]
    stage_delta = {}
    for stage in TIMING_STAGES:
        exact_sec = exact_timing.get("stage_max_sec", {}).get(stage)
        cand_sec = cand_timing.get("stage_max_sec", {}).get(stage)
        stage_delta[stage] = {
            "exact_sec": exact_sec,
            "candidate_sec": cand_sec,
            "delta_sec": (
                cand_sec - exact_sec
                if exact_sec is not None and cand_sec is not None
                else None
            ),
            "speedup_ratio": (
                exact_sec / cand_sec
                if exact_sec is not None and cand_sec not in (None, 0)
                else None
            ),
        }
    return {
        "exact_top_records": exact_timing.get("top_records", []),
        "candidate_top_records": cand_timing.get("top_records", []),
        "stage_delta": stage_delta,
    }


def _timing_summary(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"stage_max_sec": {}, "top_records": []}
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("records", [])
    stage_max: dict[str, float] = defaultdict(float)
    simple_records = []
    for record in records:
        stage = _text(record.get("stage"))
        elapsed = _float_or_none(record.get("elapsed_sec")) or 0.0
        stage_max[stage] = max(stage_max[stage], elapsed)
        simple_records.append({"stage": stage, "elapsed_sec": elapsed})
    return {
        "stage_max_sec": dict(stage_max),
        "top_records": sorted(
            simple_records,
            key=lambda item: item["elapsed_sec"],
            reverse=True,
        )[:10],
    }


def _audit_summary(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"status": "missing", "path": str(path)}
    payload = json.loads(path.read_text(encoding="utf-8"))
    aggregate = payload.get("aggregate", {})
    modes = aggregate.get("modes", {}) if isinstance(aggregate, Mapping) else {}
    return {
        "status": "present",
        "path": str(path.resolve()),
        "config": payload.get("config", {}),
        "sample_count": aggregate.get("sample_count"),
        "request_count": aggregate.get("request_count"),
        "vendor_extract_sec": aggregate.get("vendor_extract_sec"),
        "index_build_sec": aggregate.get("index_build_sec"),
        "cache_write_sec": aggregate.get("cache_write_sec"),
        "modes": {
            mode: {
                key: summary.get(key)
                for key in (
                    "request_count",
                    "rt_grid_equal_count",
                    "peak_status_match_count",
                    "peak_both_ok_count",
                    "apex_delta_min_median",
                    "apex_delta_min_p95",
                    "area_relative_delta_median",
                    "area_relative_delta_p95",
                    "area_relative_delta_max",
                    "warm_cache_total_sec",
                    "cold_index_total_sec",
                )
            }
            for mode, summary in modes.items()
            if isinstance(summary, Mapping)
        },
    }


def _classify_candidate(
    *,
    public_hashes: Mapping[str, Any],
    matrix: Mapping[str, Any],
    cells: Mapping[str, Any],
    review: Mapping[str, Any],
    identity: Mapping[str, Any],
    timing: Mapping[str, Any],
    audit: Mapping[str, Any],
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if not public_hashes.get("all_match"):
        reasons.append("public_output_hash_diff")
    if matrix.get("missing_peak_hypothesis_count") or matrix.get(
        "extra_peak_hypothesis_count",
    ):
        reasons.append("matrix_identity_key_coverage_changed")
    if matrix.get("missing_matrix_cell_count") or matrix.get("extra_matrix_cell_count"):
        reasons.append("matrix_cell_coverage_changed")
    if cells.get("status_delta_counts"):
        reasons.append("cell_status_or_authority_delta")
    if review.get("status_delta_counts"):
        reasons.append("review_identity_reason_delta")
    if identity.get("status_delta_counts"):
        reasons.append("matrix_identity_sidecar_delta")
    owner_delta = (
        timing.get("stage_delta", {})
        .get("alignment.owner_backfill", {})
        .get("delta_sec")
    )
    if owner_delta is None:
        reasons.append("target_stage_timing_missing")
    elif owner_delta >= 0:
        reasons.append("target_stage_not_faster")
    if _audit_has_large_area_drift(audit):
        reasons.append("ms1_index_area_drift_too_large_for_exact_default")
    if reasons:
        return "diagnostic_only", reasons
    return "fast_mode_candidate", ["public_hash_parity_and_target_stage_faster"]


def _audit_has_large_area_drift(audit: Mapping[str, Any]) -> bool:
    modes = audit.get("modes", {})
    if not isinstance(modes, Mapping):
        return False
    for summary in modes.values():
        if not isinstance(summary, Mapping):
            continue
        p95 = _float_or_none(summary.get("area_relative_delta_p95"))
        median = _float_or_none(summary.get("area_relative_delta_median"))
        if (p95 is not None and p95 > 0.25) or (median is not None and median > 0.10):
            return True
    return False


def _matrix_by_peak(
    matrix_rows: Sequence[Mapping[str, str]],
    matrix_fields: Sequence[str],
    identity_rows: Sequence[Mapping[str, str]],
) -> dict[str, Mapping[str, str]]:
    rows_by_peak: dict[str, Mapping[str, str]] = {}
    for identity in identity_rows:
        peak_id = _text(identity.get("peak_hypothesis_id"))
        row_index = int(_float_or_none(identity.get("matrix_row_index")) or 0)
        if not peak_id or row_index < 1 or row_index > len(matrix_rows):
            continue
        row = dict(matrix_rows[row_index - 1])
        for field in matrix_fields:
            row.setdefault(field, "")
        rows_by_peak[peak_id] = row
    return rows_by_peak


def _rows_by_key(
    rows: Sequence[Mapping[str, str]],
    columns: Sequence[str],
) -> dict[tuple[str, ...], Mapping[str, str]]:
    output: dict[tuple[str, ...], Mapping[str, str]] = {}
    for row in rows:
        key = tuple(_text(row.get(column)) for column in columns)
        if all(key):
            output[key] = row
    return output


def _sum_review_counts(rows: Sequence[Mapping[str, str]]) -> dict[str, float]:
    totals = {column: 0.0 for column in REVIEW_COUNT_COLUMNS}
    for row in rows:
        for column in REVIEW_COUNT_COLUMNS:
            totals[column] += _float_or_none(row.get(column)) or 0.0
    return totals


def _read_tsv(path: Path) -> tuple[list[dict[str, str]], tuple[str, ...]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        rows = list(reader)
        return rows, tuple(reader.fieldnames or ())


def _write_summary_tsv(path: Path, packet: Mapping[str, Any]) -> None:
    rows = []
    for name, candidate in packet["candidates"].items():
        matrix = candidate["matrix_diff"]
        timing = candidate["timing_diff"]["stage_delta"].get(
            "alignment.owner_backfill",
            {},
        )
        rows.append(
            {
                "candidate_name": name,
                "result_status": candidate["result_status"],
                "decision": candidate["decision"],
                "reason_count": len(candidate["decision_reasons"]),
                "reasons": ";".join(candidate["decision_reasons"]),
                "public_hash_all_match": candidate["public_hashes"]["all_match"],
                "exact_matrix_rows": matrix["exact_row_count"],
                "candidate_matrix_rows": matrix["candidate_row_count"],
                "missing_matrix_cells": matrix["missing_matrix_cell_count"],
                "extra_matrix_cells": matrix["extra_matrix_cell_count"],
                "matrix_area_relative_delta_p95": matrix["area_relative_delta_p95"],
                "owner_backfill_exact_sec": timing.get("exact_sec"),
                "owner_backfill_candidate_sec": timing.get("candidate_sec"),
                "owner_backfill_delta_sec": timing.get("delta_sec"),
            },
        )
    fieldnames = tuple(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.startswith("'"):
        text = text[1:]
    try:
        parsed = float(text)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    clean = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    rank = (len(clean) - 1) * percentile / 100.0
    low = int(math.floor(rank))
    high = int(math.ceil(rank))
    if low == high:
        return clean[low]
    weight = rank - low
    return clean[low] * (1.0 - weight) + clean[high] * weight


def _correlation(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    left_centered = [value - left_mean for value in left]
    right_centered = [value - right_mean for value in right]
    numerator = sum(a * b for a, b in zip(left_centered, right_centered, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left_centered))
    right_norm = math.sqrt(sum(value * value for value in right_centered))
    if left_norm == 0.0 or right_norm == 0.0:
        return None
    return numerator / (left_norm * right_norm)


def _parse_named_paths(values: Sequence[str]) -> dict[str, Path]:
    output: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"expected NAME=PATH, got: {value}")
        name, raw_path = value.split("=", 1)
        name = name.strip()
        if not name:
            raise ValueError(f"empty candidate name in: {value}")
        output[name] = Path(raw_path)
    return output


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exact-dir", type=Path, required=True)
    parser.add_argument(
        "--candidate",
        action="append",
        required=True,
        help="Candidate alignment output as NAME=PATH. Repeat for multiple candidates.",
    )
    parser.add_argument(
        "--audit",
        action="append",
        help="Optional MS1-index audit JSON as NAME=PATH. Repeat as needed.",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
