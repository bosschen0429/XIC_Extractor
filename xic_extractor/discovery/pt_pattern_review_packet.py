"""Manual-review packet builder for Pt pattern candidates."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path

from xic_extractor.discovery.artifact_paths import require_disjoint_artifact_paths
from xic_extractor.discovery.pt_pattern_artifact_contract import (
    PT_PATTERN_CANDIDATE_ASSIGNMENT_COLUMNS,
    PT_PATTERN_CANDIDATE_ASSIGNMENT_SHA256_FIELD,
    PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE,
    PT_PATTERN_TRACE_ARTIFACT_ROLE_FIELD,
    PT_PATTERN_TRACE_EXTRACTION_SHA256_FIELD,
    PT_PATTERN_TRACE_SCHEMA_ROLE,
    validate_pt_candidate_trace_summary_identity,
)
from xic_extractor.discovery.pt_pattern_trace_batch import (
    SCHEMA_VERSION as TRACE_SCHEMA_VERSION,
)
from xic_extractor.discovery.pt_pattern_trace_bundle_reader import (
    load_trace_bundle_manifest,
    load_verified_trace_summary_bundle,
)
from xic_extractor.discovery.pt_pattern_trace_evidence import (
    ANCHOR_SEGMENTATION_METRIC_COLUMNS,
    ANCHOR_SEGMENTATION_SUMMARY_COLUMNS,
)
from xic_extractor.discovery.pt_pattern_trace_manifest import (
    validate_trace_manifest_identity,
    validate_trace_row_bundle,
)
from xic_extractor.tabular_io import (
    file_sha256,
    optional_float,
    read_delimited_rows,
    write_delimited_rows,
)

SCHEMA_VERSION = "pt_pattern_manual_review_packet"
PACKET_MANIFEST_VERSION = "pt_pattern_manual_review_packet_manifest"
PACKET_SCHEMA_ROLE = {SCHEMA_VERSION: PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE}
READINESS_TIER = "diagnostic_only"
EVIDENCE_OWNER = "xic_extractor.discovery.pt_pattern_trace_evidence"

SUMMARY_REQUIRED_COLUMNS = (
    "schema_version",
    "sample_stem",
    "candidate_id",
    "candidate_assignment_sha256",
    "trace_extraction_sha256",
    "trace_artifact_role",
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
    "raw_max_abs_apex_delta_sec",
    "raw_min_pearson_r_to_base",
    "raw_min_active_overlap_fraction",
    "trace_ambiguity_status",
    "trace_ambiguity_flags",
    "shifted_only_position_labels",
    "trace_support_status",
    "combined_pt_pattern_status",
    "reason",
)

CANDIDATE_REQUIRED_COLUMNS = (
    *PT_PATTERN_CANDIDATE_ASSIGNMENT_COLUMNS,
    "candidate_state",
    "review_priority",
    "evidence_tier",
    "evidence_score",
    "cid_nl_base_status",
    "cid_nl_base_join_mode",
)

METRIC_REQUIRED_COLUMNS = (
    "schema_version",
    "sample_stem",
    "candidate_id",
    "candidate_assignment_sha256",
    "trace_extraction_sha256",
    "trace_artifact_role",
    "position_label",
    "trace_decision_basis",
    "trace_smoothing_method",
    "trace_smoothing_window_points",
    "apex_delta_sec_to_base",
    "pearson_r_to_base",
    "active_overlap_fraction",
    "shifted_pearson_r_to_base",
    "shape_shift_sec_to_base",
    "raw_apex_delta_sec_to_base",
    "raw_smoothed_apex_delta_sec",
    "raw_pearson_r_to_base",
    "raw_active_overlap_fraction",
    "trace_quality_flags",
    "trace_support_status",
    "reason",
)

POSITION_LABELS = ("pt_plus_1", "pt_plus_2", "pt_plus_4")
METRIC_POSITION_LABELS = ("base", *POSITION_LABELS)
STATUS_ORDER = (
    "pt_pattern_detected_diagnostic",
    "pt_pattern_candidate_partial",
    "pt_pattern_candidate_incomplete",
    "pt_pattern_candidate_review",
    "pt_pattern_candidate_base_missing",
)

PACKET_COLUMNS = (
    "schema_version",
    "readiness_tier",
    "evidence_owner",
    "packet_label",
    "source_label",
    "packet_row_id",
    "review_bucket",
    "sample_stem",
    "candidate_id",
    "candidate_assignment_sha256",
    "trace_extraction_sha256",
    "trace_artifact_role",
    "method_label",
    "candidate_state",
    "review_priority",
    "evidence_tier",
    "evidence_score",
    "base_mz",
    "base_rt_apex_min",
    "cid_nl_base_status",
    "cid_nl_base_join_mode",
    "scan_pattern_seed_status",
    "trace_decision_basis",
    "trace_smoothing_method",
    "trace_smoothing_window_points",
    "min_median_rt_step_sec",
    "min_effective_smoothing_window_sec",
    "max_trace_point_count",
    "trace_support_status",
    "combined_pt_pattern_status",
    "non_base_positions_supported",
    "max_abs_apex_delta_sec",
    "min_pearson_r_to_base",
    "min_active_overlap_fraction",
    "min_shifted_pearson_r_to_base",
    "max_abs_shape_shift_sec_to_base",
    "raw_max_abs_apex_delta_sec",
    "raw_min_pearson_r_to_base",
    "raw_min_active_overlap_fraction",
    "trace_ambiguity_status",
    "trace_ambiguity_flags",
    "shifted_only_position_labels",
    *ANCHOR_SEGMENTATION_SUMMARY_COLUMNS,
    "trace_reason",
    "pt_plus_1_trace_support_status",
    "pt_plus_1_trace_quality_flags",
    "pt_plus_1_median_rt_step_sec",
    "pt_plus_1_effective_smoothing_window_sec",
    "pt_plus_1_pearson_r_to_base",
    "pt_plus_1_active_overlap_fraction",
    "pt_plus_1_apex_delta_sec_to_base",
    "pt_plus_1_shifted_pearson_r_to_base",
    "pt_plus_1_shape_shift_sec_to_base",
    "pt_plus_1_raw_smoothed_apex_delta_sec",
    "pt_plus_1_raw_pearson_r_to_base",
    "pt_plus_1_raw_active_overlap_fraction",
    "pt_plus_1_raw_apex_delta_sec_to_base",
    "pt_plus_2_trace_support_status",
    "pt_plus_2_trace_quality_flags",
    "pt_plus_2_median_rt_step_sec",
    "pt_plus_2_effective_smoothing_window_sec",
    "pt_plus_2_pearson_r_to_base",
    "pt_plus_2_active_overlap_fraction",
    "pt_plus_2_apex_delta_sec_to_base",
    "pt_plus_2_shifted_pearson_r_to_base",
    "pt_plus_2_shape_shift_sec_to_base",
    "pt_plus_2_raw_smoothed_apex_delta_sec",
    "pt_plus_2_raw_pearson_r_to_base",
    "pt_plus_2_raw_active_overlap_fraction",
    "pt_plus_2_raw_apex_delta_sec_to_base",
    "pt_plus_4_trace_support_status",
    "pt_plus_4_trace_quality_flags",
    "pt_plus_4_median_rt_step_sec",
    "pt_plus_4_effective_smoothing_window_sec",
    "pt_plus_4_pearson_r_to_base",
    "pt_plus_4_active_overlap_fraction",
    "pt_plus_4_apex_delta_sec_to_base",
    "pt_plus_4_shifted_pearson_r_to_base",
    "pt_plus_4_shape_shift_sec_to_base",
    "pt_plus_4_raw_smoothed_apex_delta_sec",
    "pt_plus_4_raw_pearson_r_to_base",
    "pt_plus_4_raw_active_overlap_fraction",
    "pt_plus_4_raw_apex_delta_sec_to_base",
    "human_review_decision",
    "human_review_note",
    "oracle_status",
)
PACKET_SCHEMA_COLUMNS = {SCHEMA_VERSION: PACKET_COLUMNS}


def build_review_packet_rows(
    *,
    candidate_rows: Sequence[Mapping[str, str]],
    summary_rows: Sequence[Mapping[str, str]],
    metric_rows: Sequence[Mapping[str, str]],
    packet_label: str,
    source_label: str,
    max_per_status: int,
) -> tuple[dict[str, object], ...]:
    packet_schema_version = _packet_schema_version(
        summary_rows=summary_rows,
        metric_rows=metric_rows,
    )
    source_schema_version = _required_text(summary_rows[0], "schema_version")
    trace_artifact_role = PT_PATTERN_TRACE_SCHEMA_ROLE[source_schema_version]
    if not packet_label.strip():
        raise ValueError("packet_label must be non-empty")
    if not source_label.strip():
        raise ValueError("source_label must be non-empty")
    if max_per_status <= 0:
        raise ValueError("max_per_status must be > 0")

    candidates_by_id = _unique_by_candidate_id(candidate_rows, label="candidate")
    summaries_by_id = _unique_by_candidate_id(summary_rows, label="summary")
    missing_candidate_ids = sorted(summaries_by_id.keys() - candidates_by_id.keys())
    if missing_candidate_ids:
        joined = ", ".join(missing_candidate_ids[:5])
        raise ValueError(
            "summary candidate_id values missing from candidates: " + joined
        )
    metrics_by_candidate = _metrics_by_candidate(metric_rows)
    _validate_metric_coverage(
        metrics_by_candidate=metrics_by_candidate,
        summary_candidate_ids=set(summaries_by_id),
    )
    for candidate_id, summary in summaries_by_id.items():
        validate_pt_candidate_trace_summary_identity(
            candidates_by_id[candidate_id],
            summary,
        )
    validate_trace_row_bundle(
        summary_rows=summary_rows,
        metric_rows=metric_rows,
        schema_version=source_schema_version,
        artifact_role=trace_artifact_role,
    )
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)

    for summary in summary_rows:
        candidate_id = _required_text(summary, "candidate_id")
        candidate = candidates_by_id[candidate_id]
        combined_status = _required_text(summary, "combined_pt_pattern_status")
        row = _packet_row(
            candidate=candidate,
            summary=summary,
            metrics_by_position=metrics_by_candidate[candidate_id],
            packet_label=packet_label,
            source_label=source_label,
            schema_version=packet_schema_version,
        )
        grouped[combined_status].append(row)

    output_rows: list[dict[str, object]] = []
    for status in STATUS_ORDER:
        status_rows = sorted(grouped.pop(status, []), key=_packet_sort_key)
        output_rows.extend(status_rows[:max_per_status])
    for status in sorted(grouped):
        status_rows = sorted(grouped[status], key=_packet_sort_key)
        output_rows.extend(status_rows[:max_per_status])
    return tuple(output_rows)


def run_review_packet(
    *,
    candidates_csv: Path,
    trace_summary_csv: Path,
    trace_metrics_csv: Path,
    output_dir: Path,
    packet_label: str,
    source_label: str,
    max_per_status: int,
    trace_manifest_json: Path | None = None,
) -> dict[str, Path]:
    packet_csv = output_dir / "pt_pattern_manual_review_packet.csv"
    sidecar_json = output_dir / "pt_pattern_manual_review_packet.json"
    input_paths = [candidates_csv, trace_summary_csv, trace_metrics_csv]
    if trace_manifest_json is not None:
        input_paths.append(trace_manifest_json)
    require_disjoint_artifact_paths(
        input_paths=input_paths,
        output_paths=(packet_csv, sidecar_json),
        label="Pt pattern review packet",
    )
    candidate_rows = read_delimited_rows(
        candidates_csv,
        required_columns=CANDIDATE_REQUIRED_COLUMNS,
        delimiter=",",
        encoding="utf-8-sig",
    )
    trace_bundle = load_verified_trace_summary_bundle(
        trace_summary_csv,
        metrics_csv=trace_metrics_csv,
        manifest_json=trace_manifest_json,
        candidates_csv=candidates_csv,
    )
    summary_rows = trace_bundle.summary_rows
    metric_rows = trace_bundle.metric_rows
    trace_artifact_role = trace_bundle.trace_artifact_role
    resolved_trace_manifest = trace_bundle.manifest_json
    trace_extraction_sha256 = trace_bundle.trace_extraction_sha256

    packet_rows = build_review_packet_rows(
        candidate_rows=candidate_rows,
        summary_rows=summary_rows,
        metric_rows=metric_rows,
        packet_label=packet_label,
        source_label=source_label,
        max_per_status=max_per_status,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    packet_schema_version = str(packet_rows[0]["schema_version"])
    write_delimited_rows(packet_csv, packet_rows, PACKET_COLUMNS)
    packet_csv_sha256 = file_sha256(packet_csv).lower()

    status_counts = Counter(
        str(row["combined_pt_pattern_status"]) for row in packet_rows
    )
    sidecar = {
        "schema_version": packet_schema_version,
        "readiness_tier": READINESS_TIER,
        "evidence_owner": EVIDENCE_OWNER,
        "packet_manifest_version": PACKET_MANIFEST_VERSION,
        "trace_artifact_role": trace_artifact_role,
        "trace_extraction_sha256": trace_extraction_sha256,
        "packet_label": packet_label,
        "source_label": source_label,
        "candidates_csv": str(candidates_csv),
        "trace_summary_csv": str(trace_summary_csv),
        "trace_metrics_csv": str(trace_metrics_csv),
        "trace_manifest_json": str(resolved_trace_manifest),
        "input_sha256": {
            "candidates_csv": file_sha256(candidates_csv).lower(),
            "trace_summary_csv": file_sha256(trace_summary_csv).lower(),
            "trace_metrics_csv": file_sha256(trace_metrics_csv).lower(),
            "trace_manifest_json": file_sha256(resolved_trace_manifest).lower(),
        },
        "output_dir": str(output_dir),
        "max_per_status": max_per_status,
        "packet_row_count": len(packet_rows),
        "combined_pt_pattern_status_counts": dict(sorted(status_counts.items())),
        "human_review_decision_values": ("accepted", "rejected", "uncertain"),
        "oracle_status": "pending_human_review",
        "diagnostic_only": True,
        "outputs": {
            "packet_csv": str(packet_csv),
            "packet_csv_sha256": packet_csv_sha256,
        },
    }
    sidecar_json.write_text(
        json.dumps(sidecar, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {"packet_csv": packet_csv, "sidecar_json": sidecar_json}


def default_review_packet_manifest_path(review_packet_csv: Path) -> Path:
    return review_packet_csv.parent / "pt_pattern_manual_review_packet.json"


def validate_review_packet_manifest(
    *,
    manifest: Mapping[str, object],
    review_packet_csv: Path,
    packet_rows: Sequence[Mapping[str, str]],
    trace_manifest_json: Path | None = None,
) -> tuple[str, str, Path]:
    """Validate the packet sidecar and return verified extraction SHA and role."""

    if not packet_rows:
        raise ValueError("review packet must contain at least one row")
    if str(manifest.get("packet_manifest_version", "")).strip() != (
        PACKET_MANIFEST_VERSION
    ):
        raise ValueError("unsupported review packet manifest version")
    if manifest.get("diagnostic_only") is not True:
        raise ValueError("review packet manifest diagnostic_only must be true")
    if str(manifest.get("readiness_tier", "")).strip() != READINESS_TIER:
        raise ValueError("review packet manifest readiness_tier mismatch")
    if str(manifest.get("evidence_owner", "")).strip() != EVIDENCE_OWNER:
        raise ValueError("review packet manifest evidence_owner mismatch")
    if str(manifest.get("oracle_status", "")).strip() != "pending_human_review":
        raise ValueError("review packet manifest oracle_status mismatch")
    schemas = {str(row.get("schema_version", "")).strip() for row in packet_rows}
    if len(schemas) != 1 or "" in schemas:
        raise ValueError("review packet rows must use one non-empty schema_version")
    schema_version = next(iter(schemas))
    expected_role = PACKET_SCHEMA_ROLE.get(schema_version)
    if expected_role is None:
        raise ValueError(
            f"unsupported review packet schema_version: {schema_version!r}"
        )
    if str(manifest.get("schema_version", "")).strip() != schema_version:
        raise ValueError("review packet manifest schema_version mismatch")
    artifact_role = str(manifest.get("trace_artifact_role", "")).strip()
    if artifact_role != expected_role:
        raise ValueError("review packet manifest trace_artifact_role mismatch")
    extraction_sha256 = _required_lower_sha256(
        manifest.get("trace_extraction_sha256"),
        label="review packet manifest trace_extraction_sha256",
    )
    for row_index, row in enumerate(packet_rows, start=2):
        if str(row.get("trace_artifact_role", "")).strip() != artifact_role:
            raise ValueError(
                f"review packet row {row_index}: trace_artifact_role mismatch"
            )
        if str(row.get("trace_extraction_sha256", "")).strip() != extraction_sha256:
            raise ValueError(
                f"review packet row {row_index}: trace_extraction_sha256 mismatch"
            )
        _required_lower_sha256(
            row.get("candidate_assignment_sha256"),
            label=(f"review packet row {row_index} candidate_assignment_sha256"),
        )
    outputs = manifest.get("outputs")
    if not isinstance(outputs, Mapping):
        raise ValueError("review packet manifest outputs must be an object")
    expected_packet_sha256 = _required_lower_sha256(
        outputs.get("packet_csv_sha256"),
        label="review packet manifest packet_csv_sha256",
    )
    if file_sha256(review_packet_csv).lower() != expected_packet_sha256:
        raise ValueError("review packet manifest packet_csv_sha256 mismatch")
    input_sha256 = manifest.get("input_sha256")
    if not isinstance(input_sha256, Mapping):
        raise ValueError("review packet manifest input_sha256 must be an object")
    expected_trace_manifest_sha256 = _required_lower_sha256(
        input_sha256.get("trace_manifest_json"),
        label="review packet manifest trace_manifest_json sha256",
    )
    recorded_trace_manifest = str(manifest.get("trace_manifest_json", "")).strip()
    if trace_manifest_json is None:
        if not recorded_trace_manifest:
            raise ValueError(
                "review packet manifest trace_manifest_json must be non-empty"
            )
        resolved_trace_manifest = Path(recorded_trace_manifest)
    else:
        resolved_trace_manifest = trace_manifest_json
    if file_sha256(resolved_trace_manifest).lower() != (expected_trace_manifest_sha256):
        raise ValueError("review packet manifest trace_manifest_json sha256 mismatch")
    trace_manifest = load_trace_bundle_manifest(resolved_trace_manifest)
    trace_identity = validate_trace_manifest_identity(
        trace_manifest,
        expected_artifact_role=artifact_role,
    )
    if trace_identity.trace_extraction_sha256 != extraction_sha256:
        raise ValueError("review packet trace manifest extraction mismatch")
    trace_outputs = trace_manifest.get("output_sha256")
    if not isinstance(trace_outputs, Mapping):
        raise ValueError("trace manifest output_sha256 must be an object")
    expected_input_sha256 = {
        "candidates_csv": trace_identity.candidates_csv_sha256,
        "trace_summary_csv": _required_lower_sha256(
            trace_outputs.get("summary_csv"),
            label="trace manifest summary_csv sha256",
        ),
        "trace_metrics_csv": _required_lower_sha256(
            trace_outputs.get("metrics_csv"),
            label="trace manifest metrics_csv sha256",
        ),
    }
    for field, expected_sha256 in expected_input_sha256.items():
        observed_sha256 = _required_lower_sha256(
            input_sha256.get(field),
            label=f"review packet manifest {field} sha256",
        )
        if observed_sha256 != expected_sha256:
            raise ValueError(
                f"review packet manifest {field} sha256 differs from trace manifest"
            )
    trace_assignments = dict(trace_identity.candidate_assignments)
    for row_index, row in enumerate(packet_rows, start=2):
        candidate_id = str(row.get("candidate_id", "")).strip()
        if (
            trace_assignments.get(candidate_id)
            != str(row.get("candidate_assignment_sha256", "")).strip()
        ):
            raise ValueError(
                f"review packet row {row_index}: assignment is outside trace manifest"
            )
        if str(row.get("sample_stem", "")).strip() != (
            trace_identity.source_sample_stem
        ):
            raise ValueError(
                f"review packet row {row_index}: sample_stem differs from "
                "trace manifest"
            )

    packet_labels = {str(row.get("packet_label", "")).strip() for row in packet_rows}
    source_labels = {str(row.get("source_label", "")).strip() for row in packet_rows}
    if packet_labels != {str(manifest.get("packet_label", "")).strip()}:
        raise ValueError("review packet manifest packet_label mismatch")
    if source_labels != {str(manifest.get("source_label", "")).strip()}:
        raise ValueError("review packet manifest source_label mismatch")
    if manifest.get("packet_row_count") != len(packet_rows):
        raise ValueError("review packet manifest packet_row_count mismatch")
    return extraction_sha256, artifact_role, resolved_trace_manifest


def _packet_row(
    *,
    candidate: Mapping[str, str],
    summary: Mapping[str, str],
    metrics_by_position: Mapping[str, Mapping[str, str]],
    packet_label: str,
    source_label: str,
    schema_version: str,
) -> dict[str, object]:
    candidate_id = _required_text(summary, "candidate_id")
    row: dict[str, object] = {
        "schema_version": schema_version,
        "readiness_tier": READINESS_TIER,
        "evidence_owner": EVIDENCE_OWNER,
        "packet_label": packet_label,
        "source_label": source_label,
        "packet_row_id": f"{source_label}|{candidate_id}",
        "review_bucket": _review_bucket(
            _required_text(summary, "combined_pt_pattern_status")
        ),
        "sample_stem": _required_text(summary, "sample_stem"),
        "candidate_id": candidate_id,
        "candidate_assignment_sha256": summary.get(
            PT_PATTERN_CANDIDATE_ASSIGNMENT_SHA256_FIELD,
            "",
        ),
        "trace_extraction_sha256": summary.get(
            PT_PATTERN_TRACE_EXTRACTION_SHA256_FIELD,
            "",
        ),
        "trace_artifact_role": summary.get(
            PT_PATTERN_TRACE_ARTIFACT_ROLE_FIELD,
            "",
        ),
        "method_label": candidate.get("method_label", ""),
        "candidate_state": candidate.get("candidate_state", ""),
        "review_priority": candidate.get("review_priority", ""),
        "evidence_tier": candidate.get("evidence_tier", ""),
        "evidence_score": candidate.get("evidence_score", ""),
        "base_mz": _required_text(summary, "base_mz"),
        "base_rt_apex_min": _required_text(summary, "base_rt_apex_min"),
        "cid_nl_base_status": candidate.get("cid_nl_base_status", ""),
        "cid_nl_base_join_mode": candidate.get("cid_nl_base_join_mode", ""),
        "scan_pattern_seed_status": summary.get("scan_pattern_seed_status", ""),
        "trace_decision_basis": summary.get("trace_decision_basis", ""),
        "trace_smoothing_method": summary.get("trace_smoothing_method", ""),
        "trace_smoothing_window_points": summary.get(
            "trace_smoothing_window_points", ""
        ),
        "min_median_rt_step_sec": summary.get("min_median_rt_step_sec", ""),
        "min_effective_smoothing_window_sec": summary.get(
            "min_effective_smoothing_window_sec", ""
        ),
        "max_trace_point_count": summary.get("max_trace_point_count", ""),
        "trace_support_status": summary.get("trace_support_status", ""),
        "combined_pt_pattern_status": summary.get("combined_pt_pattern_status", ""),
        "non_base_positions_supported": summary.get("non_base_positions_supported", ""),
        "max_abs_apex_delta_sec": summary.get("max_abs_apex_delta_sec", ""),
        "min_pearson_r_to_base": summary.get("min_pearson_r_to_base", ""),
        "min_active_overlap_fraction": summary.get("min_active_overlap_fraction", ""),
        "min_shifted_pearson_r_to_base": summary.get(
            "min_shifted_pearson_r_to_base", ""
        ),
        "max_abs_shape_shift_sec_to_base": summary.get(
            "max_abs_shape_shift_sec_to_base", ""
        ),
        "raw_max_abs_apex_delta_sec": summary.get("raw_max_abs_apex_delta_sec", ""),
        "raw_min_pearson_r_to_base": summary.get("raw_min_pearson_r_to_base", ""),
        "raw_min_active_overlap_fraction": summary.get(
            "raw_min_active_overlap_fraction", ""
        ),
        "trace_ambiguity_status": summary.get("trace_ambiguity_status", ""),
        "trace_ambiguity_flags": summary.get("trace_ambiguity_flags", ""),
        "shifted_only_position_labels": summary.get("shifted_only_position_labels", ""),
        **{
            column: summary.get(column, "")
            for column in ANCHOR_SEGMENTATION_SUMMARY_COLUMNS
        },
        "trace_reason": summary.get("reason", ""),
        "human_review_decision": "",
        "human_review_note": "",
        "oracle_status": "pending_human_review",
    }
    for position_label in POSITION_LABELS:
        metric = metrics_by_position.get(position_label, {})
        prefix = position_label
        row[f"{prefix}_trace_support_status"] = metric.get("trace_support_status", "")
        row[f"{prefix}_trace_quality_flags"] = metric.get("trace_quality_flags", "")
        row[f"{prefix}_median_rt_step_sec"] = metric.get("median_rt_step_sec", "")
        row[f"{prefix}_effective_smoothing_window_sec"] = metric.get(
            "effective_smoothing_window_sec", ""
        )
        row[f"{prefix}_pearson_r_to_base"] = metric.get("pearson_r_to_base", "")
        row[f"{prefix}_active_overlap_fraction"] = metric.get(
            "active_overlap_fraction", ""
        )
        row[f"{prefix}_apex_delta_sec_to_base"] = metric.get(
            "apex_delta_sec_to_base", ""
        )
        row[f"{prefix}_shifted_pearson_r_to_base"] = metric.get(
            "shifted_pearson_r_to_base", ""
        )
        row[f"{prefix}_shape_shift_sec_to_base"] = metric.get(
            "shape_shift_sec_to_base", ""
        )
        row[f"{prefix}_raw_smoothed_apex_delta_sec"] = metric.get(
            "raw_smoothed_apex_delta_sec", ""
        )
        row[f"{prefix}_raw_pearson_r_to_base"] = metric.get("raw_pearson_r_to_base", "")
        row[f"{prefix}_raw_active_overlap_fraction"] = metric.get(
            "raw_active_overlap_fraction", ""
        )
        row[f"{prefix}_raw_apex_delta_sec_to_base"] = metric.get(
            "raw_apex_delta_sec_to_base", ""
        )
    return row


def _packet_schema_version(
    *,
    summary_rows: Sequence[Mapping[str, str]],
    metric_rows: Sequence[Mapping[str, str]],
) -> str:
    summary_versions = {
        str(row.get("schema_version", "")).strip() for row in summary_rows
    }
    if not summary_versions:
        raise ValueError("trace summary must contain at least one candidate row")
    if len(summary_versions) != 1:
        raise ValueError("trace summary rows must use one schema_version")
    source_schema = next(iter(summary_versions))
    if source_schema != TRACE_SCHEMA_VERSION:
        raise ValueError(f"unsupported trace summary schema_version: {source_schema!r}")

    _validate_anchor_columns(
        summary_rows,
        columns=ANCHOR_SEGMENTATION_SUMMARY_COLUMNS,
        label="trace summary",
    )
    metric_versions = {
        str(row.get("schema_version", "")).strip() for row in metric_rows
    }
    if metric_versions != {source_schema}:
        raise ValueError("trace metric schema_version must match trace summary")
    _validate_anchor_columns(
        metric_rows,
        columns=ANCHOR_SEGMENTATION_METRIC_COLUMNS,
        label="trace metric",
    )
    return SCHEMA_VERSION


def _validate_anchor_columns(
    rows: Sequence[Mapping[str, str]],
    *,
    columns: Sequence[str],
    label: str,
) -> None:
    expected = set(columns)
    for row_index, row in enumerate(rows, start=2):
        present = expected.intersection(row)
        if present != expected:
            missing = sorted(expected - present)
            raise ValueError(
                f"{label} row {row_index} is missing anchor segmentation columns: "
                + ", ".join(missing)
            )


def _unique_by_candidate_id(
    rows: Sequence[Mapping[str, str]], *, label: str
) -> dict[str, Mapping[str, str]]:
    by_id: dict[str, Mapping[str, str]] = {}
    duplicates: list[str] = []
    for row in rows:
        candidate_id = _required_text(row, "candidate_id")
        if candidate_id in by_id:
            duplicates.append(candidate_id)
        by_id[candidate_id] = row
    if duplicates:
        joined = ", ".join(sorted(set(duplicates))[:5])
        raise ValueError(f"duplicate {label} candidate_id values: {joined}")
    return by_id


def _metrics_by_candidate(
    rows: Sequence[Mapping[str, str]],
) -> dict[str, dict[str, Mapping[str, str]]]:
    grouped: dict[str, dict[str, Mapping[str, str]]] = defaultdict(dict)
    duplicates: list[str] = []
    for row in rows:
        candidate_id = _required_text(row, "candidate_id")
        position_label = _required_text(row, "position_label")
        if position_label in grouped[candidate_id]:
            duplicates.append(f"{candidate_id}:{position_label}")
        grouped[candidate_id][position_label] = row
    if duplicates:
        joined = ", ".join(sorted(set(duplicates))[:5])
        raise ValueError(f"duplicate metric candidate/position values: {joined}")
    return grouped


def _validate_metric_coverage(
    *,
    metrics_by_candidate: Mapping[str, Mapping[str, Mapping[str, str]]],
    summary_candidate_ids: set[str],
) -> None:
    metric_candidate_ids = set(metrics_by_candidate)
    if metric_candidate_ids != summary_candidate_ids:
        missing = sorted(summary_candidate_ids - metric_candidate_ids)
        unexpected = sorted(metric_candidate_ids - summary_candidate_ids)
        details: list[str] = []
        if missing:
            details.append("missing=" + ",".join(missing[:5]))
        if unexpected:
            details.append("unexpected=" + ",".join(unexpected[:5]))
        raise ValueError(
            "metric candidate_id set must exactly match summary candidate_id set: "
            + "; ".join(details)
        )

    expected_positions = set(METRIC_POSITION_LABELS)
    for candidate_id in sorted(summary_candidate_ids):
        positions = set(metrics_by_candidate[candidate_id])
        if positions == expected_positions:
            continue
        missing = sorted(expected_positions - positions)
        unexpected = sorted(positions - expected_positions)
        details = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if unexpected:
            details.append("unexpected=" + ",".join(unexpected))
        raise ValueError(
            f"{candidate_id}: metric position_label set must be exactly "
            f"{','.join(METRIC_POSITION_LABELS)}: " + "; ".join(details)
        )


def _review_bucket(combined_status: str) -> str:
    if combined_status == "pt_pattern_detected_diagnostic":
        return "detected_diagnostic_review"
    if combined_status == "pt_pattern_candidate_partial":
        return "partial_support_review"
    if combined_status == "pt_pattern_candidate_incomplete":
        return "incomplete_signal_review"
    if combined_status == "pt_pattern_candidate_base_missing":
        return "base_missing_review"
    return "manual_review"


def _packet_sort_key(row: Mapping[str, object]) -> tuple[float, float, str]:
    return (
        _optional_float_sort_value(row.get("base_rt_apex_min")),
        _optional_float_sort_value(row.get("base_mz")),
        str(row.get("candidate_id", "")),
    )


def _optional_float_sort_value(value: object) -> float:
    parsed = optional_float(value)
    return float("inf") if parsed is None else parsed


def _required_text(row: Mapping[str, str], field: str) -> str:
    value = str(row.get(field, "")).strip()
    if not value:
        raise ValueError(f"missing required field {field!r}")
    return value


def _required_lower_sha256(value: object, *, label: str) -> str:
    normalized = str(value or "").strip()
    if re.fullmatch(r"[0-9a-f]{64}", normalized) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return normalized
