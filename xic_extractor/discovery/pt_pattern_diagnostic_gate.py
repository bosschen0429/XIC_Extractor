"""Diagnostic gate replay for Pt pattern review packets."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.artifact_paths import require_disjoint_artifact_paths
from xic_extractor.discovery.pt_pattern_artifact_contract import (
    PT_PATTERN_CANDIDATE_ASSIGNMENT_SHA256_FIELD,
    PT_PATTERN_TRACE_ARTIFACT_ROLE_FIELD,
    PT_PATTERN_TRACE_EXTRACTION_SHA256_FIELD,
    PT_PATTERN_TRACE_FULL_CONTEXT_ROLE,
    PT_PATTERN_TRACE_SCHEMA_ROLE,
)
from xic_extractor.discovery.pt_pattern_evidence_rule import (
    DEFAULT_APEX_DELTA_MAX_SEC,
    DEFAULT_OVERLAP_MIN,
    DEFAULT_PEARSON_MIN,
    DEFAULT_SHAPE_SHIFT_MAX_SEC,
    DEFAULT_SHIFTED_PEARSON_MIN,
    MIN_SUPPORTED_POSITIONS,
    REQUIRED_COMBINED_STATUS,
    PtPatternEvidenceThresholds,
    effective_max_abs_apex_delta_sec,
    evaluate_pt_pattern_evidence,
    pt_pattern_rule_id,
)
from xic_extractor.discovery.pt_pattern_oracle_promotion import (
    load_verified_oracle_promotion_bundle,
)
from xic_extractor.discovery.pt_pattern_oracle_replay_contract import (
    load_review_packets,
    validate_oracle_promotion_binding,
)
from xic_extractor.discovery.pt_pattern_review_packet import PACKET_SCHEMA_ROLE
from xic_extractor.discovery.pt_pattern_trace_bundle_reader import (
    VerifiedTraceSummaryBundle,
    load_verified_trace_summary_bundle,
)
from xic_extractor.tabular_io import (
    file_sha256,
    optional_float,
    write_delimited_rows,
)

SCHEMA_VERSION = "pt_pattern_diagnostic_gate.v2"
READINESS_TIER = "diagnostic_only"
EVIDENCE_OWNER = "xic_extractor.discovery.pt_pattern_diagnostic_gate"
AUTHORITY_SCOPE = "diagnostic_gate_replay_only"

TRACE_SUMMARY_GATE_REQUIRED_COLUMNS = (
    "sample_stem",
    "candidate_id",
    "scan_pattern_seed_status",
    "base_mz",
    "base_rt_apex_min",
    "trace_decision_basis",
    "trace_smoothing_method",
    "trace_smoothing_window_points",
    "non_base_positions_supported",
    "max_abs_apex_delta_sec",
    "min_pearson_r_to_base",
    "min_active_overlap_fraction",
    "min_shifted_pearson_r_to_base",
    "max_abs_shape_shift_sec_to_base",
    "trace_support_status",
    "combined_pt_pattern_status",
)


@dataclass(frozen=True)
class TraceSummaryInput:
    source_label: str
    path: Path


GATE_COLUMNS = (
    "schema_version",
    "readiness_tier",
    "evidence_owner",
    "authority_scope",
    "gate_id",
    "rule_id",
    "packet_row_id",
    "source_label",
    "input_schema_version",
    "sample_stem",
    "candidate_id",
    "candidate_assignment_sha256",
    "trace_extraction_sha256",
    "trace_artifact_role",
    "diagnostic_gate_call",
    "diagnostic_detection_status",
    "gate_reason",
    "required_combined_status",
    "min_non_base_positions_supported",
    "max_abs_apex_delta_sec_threshold",
    "min_pearson_r_to_base_threshold",
    "min_active_overlap_fraction_threshold",
    "min_shifted_pearson_r_to_base_threshold",
    "max_abs_shape_shift_sec_to_base_threshold",
    "combined_pt_pattern_status",
    "non_base_positions_supported",
    "max_abs_apex_delta_sec",
    "min_pearson_r_to_base",
    "min_active_overlap_fraction",
    "min_shifted_pearson_r_to_base",
    "max_abs_shape_shift_sec_to_base",
    "base_mz",
    "base_rt_apex_min",
    "oracle_call",
    "oracle_detection_status",
    "replay_outcome",
)


def run_diagnostic_gate(
    *,
    review_packet_csvs: Sequence[Path] = (),
    trace_summary_csvs: Sequence[TraceSummaryInput] = (),
    output_dir: Path,
    gate_id: str,
    oracle_promotion_csv: Path | None = None,
    apex_delta_max_sec: float = DEFAULT_APEX_DELTA_MAX_SEC,
    pearson_min: float = DEFAULT_PEARSON_MIN,
    overlap_min: float = DEFAULT_OVERLAP_MIN,
    shifted_pearson_min: float | None = None,
    shape_shift_max_sec: float | None = None,
) -> dict[str, Path]:
    gate_rows_csv = output_dir / "pt_pattern_diagnostic_gate_rows.csv"
    summary_json = output_dir / "pt_pattern_diagnostic_gate_summary.json"
    input_paths = [
        *review_packet_csvs,
        *(item.path for item in trace_summary_csvs),
    ]
    if oracle_promotion_csv is not None:
        input_paths.append(oracle_promotion_csv)
    require_disjoint_artifact_paths(
        input_paths=input_paths,
        output_paths=(gate_rows_csv, summary_json),
        label="Pt pattern diagnostic gate",
    )
    thresholds = _thresholds(
        apex_delta_max_sec=apex_delta_max_sec,
        pearson_min=pearson_min,
        overlap_min=overlap_min,
        shifted_pearson_min=shifted_pearson_min,
        shape_shift_max_sec=shape_shift_max_sec,
    )
    loaded_review_packets = load_review_packets(review_packet_csvs)
    review_rows = list(loaded_review_packets.rows)
    trace_summary_rows, trace_summary_bundles = _read_trace_summary_inputs(
        trace_summary_csvs
    )
    gate_input_rows = [*review_rows, *trace_summary_rows]
    if not gate_input_rows:
        raise ValueError(
            "at least one review packet CSV or trace summary CSV is required"
        )
    oracle_bundle = (
        load_verified_oracle_promotion_bundle(oracle_promotion_csv)
        if oracle_promotion_csv is not None
        else None
    )
    oracle_rows = oracle_bundle.rows if oracle_bundle is not None else ()
    gate_rows = build_diagnostic_gate_rows(
        review_rows=gate_input_rows,
        oracle_rows=oracle_rows,
        review_packet_sha256_by_row_id=(loaded_review_packets.sha256_by_packet_row_id),
        gate_id=gate_id,
        thresholds=thresholds,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    write_delimited_rows(gate_rows_csv, gate_rows, GATE_COLUMNS)

    call_counts = Counter(str(row["diagnostic_gate_call"]) for row in gate_rows)
    replay_counts = Counter(
        str(row["replay_outcome"]) for row in gate_rows if row["replay_outcome"]
    )
    oracle_counts = Counter(
        str(row["oracle_call"]) for row in gate_rows if row["oracle_call"]
    )
    summary = {
        "schema_version": SCHEMA_VERSION,
        "readiness_tier": READINESS_TIER,
        "evidence_owner": EVIDENCE_OWNER,
        "authority_scope": AUTHORITY_SCOPE,
        "gate_id": gate_id,
        "rule_id": pt_pattern_rule_id(thresholds),
        "required_combined_status": thresholds.required_combined_status,
        "min_non_base_positions_supported": (
            thresholds.min_non_base_positions_supported
        ),
        "max_abs_apex_delta_sec": thresholds.max_abs_apex_delta_sec,
        "max_abs_apex_delta_scan_step_multiplier": (
            thresholds.max_abs_apex_delta_scan_step_multiplier
        ),
        "min_pearson_r_to_base": thresholds.min_pearson_r_to_base,
        "min_active_overlap_fraction": thresholds.min_active_overlap_fraction,
        "min_shifted_pearson_r_to_base": (thresholds.min_shifted_pearson_r_to_base),
        "max_abs_shape_shift_sec_to_base": (thresholds.max_abs_shape_shift_sec_to_base),
        "review_packet_csvs": [str(path) for path in review_packet_csvs],
        "review_packet_csv_sha256s": {
            str(path): file_sha256(path) for path in review_packet_csvs
        },
        "trace_summary_csvs": [
            f"{item.source_label}={item.path}" for item in trace_summary_csvs
        ],
        "trace_summary_csv_sha256s": {
            f"{item.source_label}={item.path}": file_sha256(item.path)
            for item in trace_summary_csvs
        },
        "trace_metrics_csvs": [
            str(bundle.metrics_csv) for bundle in trace_summary_bundles
        ],
        "trace_metrics_csv_sha256s": {
            str(bundle.metrics_csv): file_sha256(bundle.metrics_csv)
            for bundle in trace_summary_bundles
        },
        "trace_manifest_jsons": [
            str(bundle.manifest_json) for bundle in trace_summary_bundles
        ],
        "trace_manifest_json_sha256s": {
            str(bundle.manifest_json): file_sha256(bundle.manifest_json)
            for bundle in trace_summary_bundles
        },
        "oracle_promotion_csv": (
            str(oracle_bundle.packet_csv.resolve()) if oracle_bundle else ""
        ),
        "oracle_promotion_csv_sha256": (
            oracle_bundle.packet_csv_sha256 if oracle_bundle else ""
        ),
        "oracle_promotion_summary_json": (
            str(oracle_bundle.summary_json) if oracle_bundle else ""
        ),
        "oracle_promotion_summary_json_sha256": (
            oracle_bundle.summary_json_sha256 if oracle_bundle else ""
        ),
        "oracle_packet_id": (
            oracle_bundle.oracle_packet_id if oracle_bundle else ""
        ),
        "oracle_bundle_row_count": (
            len(oracle_bundle.rows) if oracle_bundle else 0
        ),
        "row_count": len(gate_rows),
        "diagnostic_gate_call_counts": dict(sorted(call_counts.items())),
        "oracle_call_counts": dict(sorted(oracle_counts.items())),
        "replay_outcome_counts": dict(sorted(replay_counts.items())),
        "replay_status": _replay_status(replay_counts, oracle_rows),
        "changes_matrix": False,
        "changes_counted_detection": False,
        "changes_export": False,
        "outputs": {
            "gate_rows_csv": str(gate_rows_csv),
        },
    }
    summary_json.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {"gate_rows_csv": gate_rows_csv, "summary_json": summary_json}


def build_diagnostic_gate_rows(
    *,
    review_rows: Sequence[Mapping[str, str]],
    oracle_rows: Sequence[Mapping[str, str]] = (),
    review_packet_sha256_by_row_id: Mapping[str, str] | None = None,
    gate_id: str,
    apex_delta_max_sec: float = DEFAULT_APEX_DELTA_MAX_SEC,
    pearson_min: float = DEFAULT_PEARSON_MIN,
    overlap_min: float = DEFAULT_OVERLAP_MIN,
    thresholds: PtPatternEvidenceThresholds | None = None,
) -> tuple[dict[str, object], ...]:
    if not gate_id.strip():
        raise ValueError("gate_id must be non-empty")
    thresholds = thresholds or _thresholds(
        apex_delta_max_sec=apex_delta_max_sec,
        pearson_min=pearson_min,
        overlap_min=overlap_min,
    )
    by_packet_row_id = _unique_review_rows(review_rows)
    oracle_by_packet_row_id = _unique_oracle_rows(oracle_rows)
    missing = sorted(set(oracle_by_packet_row_id) - set(by_packet_row_id))
    if missing:
        raise ValueError("oracle row missing review packet: " + ", ".join(missing[:5]))
    packet_sha256_by_row_id = review_packet_sha256_by_row_id or {}
    for packet_row_id, oracle in oracle_by_packet_row_id.items():
        review_packet_sha256 = packet_sha256_by_row_id.get(packet_row_id)
        if review_packet_sha256 is None:
            raise ValueError(
                f"{packet_row_id}: oracle replay requires an exact review packet"
            )
        validate_oracle_promotion_binding(
            oracle_row=oracle,
            review_row=by_packet_row_id[packet_row_id],
            review_packet_csv_sha256=review_packet_sha256,
        )

    rows: list[dict[str, object]] = []
    for review in sorted(
        review_rows,
        key=lambda row: (
            _required_text(row, "source_label"),
            _optional_float_sort_value(row.get("base_rt_apex_min")),
            _optional_float_sort_value(row.get("base_mz")),
            _required_text(row, "packet_row_id"),
        ),
    ):
        input_schema_version, assignment_sha256, extraction_sha256, artifact_role = (
            _validated_input_lineage(review)
        )
        packet_row_id = _required_text(review, "packet_row_id")
        decision = evaluate_pt_pattern_evidence(
            review,
            thresholds=thresholds,
        )
        effective_apex_threshold = effective_max_abs_apex_delta_sec(
            review,
            thresholds=thresholds,
        )
        oracle = oracle_by_packet_row_id.get(packet_row_id, {})
        oracle_call = str(oracle.get("oracle_call", "")).strip().lower()
        rows.append(
            {
                "schema_version": SCHEMA_VERSION,
                "readiness_tier": READINESS_TIER,
                "evidence_owner": EVIDENCE_OWNER,
                "authority_scope": AUTHORITY_SCOPE,
                "gate_id": gate_id,
                "rule_id": pt_pattern_rule_id(thresholds),
                "packet_row_id": packet_row_id,
                "source_label": review.get("source_label", ""),
                "input_schema_version": input_schema_version,
                "sample_stem": review.get("sample_stem", ""),
                "candidate_id": review.get("candidate_id", ""),
                "candidate_assignment_sha256": assignment_sha256,
                "trace_extraction_sha256": extraction_sha256,
                "trace_artifact_role": artifact_role,
                "diagnostic_gate_call": decision.call,
                "diagnostic_detection_status": decision.detection_status,
                "gate_reason": decision.reason,
                "required_combined_status": thresholds.required_combined_status,
                "min_non_base_positions_supported": (
                    thresholds.min_non_base_positions_supported
                ),
                "max_abs_apex_delta_sec_threshold": (effective_apex_threshold),
                "min_pearson_r_to_base_threshold": (thresholds.min_pearson_r_to_base),
                "min_active_overlap_fraction_threshold": (
                    thresholds.min_active_overlap_fraction
                ),
                "min_shifted_pearson_r_to_base_threshold": (
                    thresholds.min_shifted_pearson_r_to_base
                ),
                "max_abs_shape_shift_sec_to_base_threshold": (
                    thresholds.max_abs_shape_shift_sec_to_base
                ),
                "combined_pt_pattern_status": review.get(
                    "combined_pt_pattern_status", ""
                ),
                "non_base_positions_supported": review.get(
                    "non_base_positions_supported", ""
                ),
                "max_abs_apex_delta_sec": review.get("max_abs_apex_delta_sec", ""),
                "min_pearson_r_to_base": review.get("min_pearson_r_to_base", ""),
                "min_active_overlap_fraction": review.get(
                    "min_active_overlap_fraction", ""
                ),
                "min_shifted_pearson_r_to_base": review.get(
                    "min_shifted_pearson_r_to_base", ""
                ),
                "max_abs_shape_shift_sec_to_base": review.get(
                    "max_abs_shape_shift_sec_to_base", ""
                ),
                "base_mz": review.get("base_mz", ""),
                "base_rt_apex_min": review.get("base_rt_apex_min", ""),
                "oracle_call": oracle_call,
                "oracle_detection_status": oracle.get("oracle_detection_status", ""),
                "replay_outcome": (
                    _case_outcome(oracle_call, decision.call) if oracle_call else ""
                ),
            }
        )
    return tuple(rows)


def _optional_float_sort_value(value: object) -> float:
    parsed = optional_float(value)
    return float("inf") if parsed is None else parsed


def diagnostic_gate_call(
    row: Mapping[str, object],
    *,
    apex_delta_max_sec: float = DEFAULT_APEX_DELTA_MAX_SEC,
    pearson_min: float = DEFAULT_PEARSON_MIN,
    overlap_min: float = DEFAULT_OVERLAP_MIN,
    shifted_pearson_min: float | None = None,
    shape_shift_max_sec: float | None = None,
) -> tuple[str, str]:
    decision = evaluate_pt_pattern_evidence(
        row,
        thresholds=_thresholds(
            apex_delta_max_sec=apex_delta_max_sec,
            pearson_min=pearson_min,
            overlap_min=overlap_min,
            shifted_pearson_min=shifted_pearson_min,
            shape_shift_max_sec=shape_shift_max_sec,
        ),
    )
    return decision.call, decision.reason


def _read_trace_summary_inputs(
    inputs: Sequence[TraceSummaryInput],
) -> tuple[list[dict[str, str]], tuple[VerifiedTraceSummaryBundle, ...]]:
    rows: list[dict[str, str]] = []
    bundles: list[VerifiedTraceSummaryBundle] = []
    for item in inputs:
        source_label = item.source_label.strip()
        if not source_label:
            raise ValueError("trace summary source_label must be non-empty")
        bundle = load_verified_trace_summary_bundle(
            item.path,
            expected_artifact_role=PT_PATTERN_TRACE_FULL_CONTEXT_ROLE,
        )
        bundles.append(bundle)
        rows.extend(
            _trace_summary_gate_row(row, source_label=source_label)
            for row in bundle.summary_rows
        )
    return rows, tuple(bundles)


def _trace_summary_gate_row(
    row: Mapping[str, str],
    *,
    source_label: str,
) -> dict[str, str]:
    candidate_id = _required_text(row, "candidate_id")
    return {
        "schema_version": row.get("schema_version", ""),
        "source_label": source_label,
        "packet_row_id": f"{source_label}|{candidate_id}",
        "sample_stem": row.get("sample_stem", ""),
        "candidate_id": candidate_id,
        "candidate_assignment_sha256": row.get(
            PT_PATTERN_CANDIDATE_ASSIGNMENT_SHA256_FIELD,
            "",
        ),
        "trace_extraction_sha256": row.get(
            PT_PATTERN_TRACE_EXTRACTION_SHA256_FIELD,
            "",
        ),
        "trace_artifact_role": row.get(
            PT_PATTERN_TRACE_ARTIFACT_ROLE_FIELD,
            "",
        ),
        "combined_pt_pattern_status": row.get("combined_pt_pattern_status", ""),
        "non_base_positions_supported": row.get("non_base_positions_supported", ""),
        "max_abs_apex_delta_sec": row.get("max_abs_apex_delta_sec", ""),
        "min_pearson_r_to_base": row.get("min_pearson_r_to_base", ""),
        "min_active_overlap_fraction": row.get("min_active_overlap_fraction", ""),
        "min_shifted_pearson_r_to_base": row.get("min_shifted_pearson_r_to_base", ""),
        "max_abs_shape_shift_sec_to_base": row.get(
            "max_abs_shape_shift_sec_to_base", ""
        ),
        "min_median_rt_step_sec": row.get("min_median_rt_step_sec", ""),
        "min_effective_smoothing_window_sec": row.get(
            "min_effective_smoothing_window_sec", ""
        ),
        "trace_smoothing_window_points": row.get("trace_smoothing_window_points", ""),
        "base_mz": row.get("base_mz", ""),
        "base_rt_apex_min": row.get("base_rt_apex_min", ""),
    }


def _validated_input_lineage(
    row: Mapping[str, str],
) -> tuple[str, str, str, str]:
    schema_version = _required_text(row, "schema_version")
    artifact_role = _required_text(row, PT_PATTERN_TRACE_ARTIFACT_ROLE_FIELD)
    expected_role = {
        **PT_PATTERN_TRACE_SCHEMA_ROLE,
        **PACKET_SCHEMA_ROLE,
    }.get(schema_version)
    if expected_role is None:
        raise ValueError(
            f"unsupported diagnostic gate input schema: {schema_version!r}"
        )
    if artifact_role != expected_role:
        raise ValueError("diagnostic gate input schema/trace_artifact_role mismatch")
    assignment_sha256 = _required_lower_sha256(
        row,
        PT_PATTERN_CANDIDATE_ASSIGNMENT_SHA256_FIELD,
    )
    extraction_sha256 = _required_lower_sha256(
        row,
        PT_PATTERN_TRACE_EXTRACTION_SHA256_FIELD,
    )
    return schema_version, assignment_sha256, extraction_sha256, artifact_role


def _required_lower_sha256(row: Mapping[str, str], field: str) -> str:
    value = _required_text(row, field)
    if len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _unique_review_rows(
    rows: Sequence[Mapping[str, str]],
) -> dict[str, Mapping[str, str]]:
    by_id: dict[str, Mapping[str, str]] = {}
    for row in rows:
        packet_row_id = _required_text(row, "packet_row_id")
        if packet_row_id in by_id:
            raise ValueError(f"duplicate review packet_row_id: {packet_row_id}")
        by_id[packet_row_id] = row
    return by_id


def _unique_oracle_rows(
    rows: Sequence[Mapping[str, str]],
) -> dict[str, Mapping[str, str]]:
    by_id: dict[str, Mapping[str, str]] = {}
    for row in rows:
        packet_row_id = _required_text(row, "packet_row_id")
        if packet_row_id in by_id:
            raise ValueError(f"duplicate oracle packet_row_id: {packet_row_id}")
        oracle_call = _required_text(row, "oracle_call").lower()
        if oracle_call not in {"accept", "reject"}:
            raise ValueError(f"unsupported oracle_call: {oracle_call!r}")
        by_id[packet_row_id] = row
    return by_id


def _case_outcome(oracle_call: str, predicted_call: str) -> str:
    if oracle_call == "accept" and predicted_call == "accept":
        return "true_positive"
    if oracle_call == "reject" and predicted_call == "accept":
        return "false_positive"
    if oracle_call == "reject" and predicted_call == "reject":
        return "true_negative"
    if oracle_call == "accept" and predicted_call == "reject":
        return "false_negative"
    raise ValueError(f"unsupported call pair: {oracle_call!r}, {predicted_call!r}")


def _replay_status(
    replay_counts: Mapping[str, int],
    oracle_rows: Sequence[Mapping[str, str]],
) -> str:
    if not oracle_rows:
        return "no_oracle_replay"
    if (
        int(replay_counts.get("false_positive", 0)) == 0
        and int(replay_counts.get("false_negative", 0)) == 0
    ):
        return "diagnostic_gate_replay_passed"
    return "diagnostic_gate_replay_failed"


def _thresholds(
    apex_delta_max_sec: float,
    pearson_min: float,
    overlap_min: float,
    shifted_pearson_min: float | None = None,
    shape_shift_max_sec: float | None = None,
) -> PtPatternEvidenceThresholds:
    return PtPatternEvidenceThresholds(
        required_combined_status=REQUIRED_COMBINED_STATUS,
        min_non_base_positions_supported=MIN_SUPPORTED_POSITIONS,
        max_abs_apex_delta_sec=apex_delta_max_sec,
        min_pearson_r_to_base=pearson_min,
        min_active_overlap_fraction=overlap_min,
        min_shifted_pearson_r_to_base=(
            DEFAULT_SHIFTED_PEARSON_MIN
            if shifted_pearson_min is None
            else shifted_pearson_min
        ),
        max_abs_shape_shift_sec_to_base=(
            DEFAULT_SHAPE_SHIFT_MAX_SEC
            if shape_shift_max_sec is None
            else shape_shift_max_sec
        ),
    )


def _required_text(row: Mapping[str, str], key: str) -> str:
    value = str(row.get(key, "")).strip()
    if not value:
        raise ValueError(f"{key} must be non-empty")
    return value
