from __future__ import annotations

import json
from pathlib import Path

import pytest

from xic_extractor.discovery import pt_pattern_review_packet as packet
from xic_extractor.discovery import pt_pattern_trace_batch as trace_batch
from xic_extractor.discovery import pt_pattern_trace_evidence as evidence
from xic_extractor.discovery.pt_pattern_artifact_contract import (
    pt_pattern_candidate_assignment_fingerprint,
    pt_pattern_trace_extraction_fingerprint,
)
from xic_extractor.discovery.pt_pattern_review_bundle import (
    load_verified_review_packet_bundle,
)
from xic_extractor.discovery.pt_pattern_trace_manifest import (
    default_trace_manifest_path,
    load_verified_trace_summary_bundle,
    trace_bundle_manifest_fields,
)
from xic_extractor.tabular_io import (
    file_sha256,
    format_diagnostic_value,
    read_delimited_rows,
    write_delimited_rows,
)


def _candidate_row(
    candidate_id: str,
    *,
    method_label: str = "pt_pattern",
    state: str = "pt_pattern_candidate",
) -> dict[str, str]:
    return {
        "sample_stem": "SampleA",
        "method_label": method_label,
        "pattern_id": "pt",
        "candidate_id": candidate_id,
        "candidate_state": state,
        "review_priority": "HIGH",
        "evidence_tier": "A",
        "evidence_score": "90",
        "base_feature_id": "base-1",
        "base_mz": "500.0",
        "base_rt_apex_min": "10.0",
        "pt_plus_1_feature_id": "plus1-1",
        "pt_plus_1_mz": "501.0020",
        "pt_plus_1_expected_mz": "501.0021",
        "pt_plus_2_feature_id": "plus2-1",
        "pt_plus_2_mz": "502.0021",
        "pt_plus_2_expected_mz": "502.0023",
        "pt_plus_4_feature_id": "plus4-1",
        "pt_plus_4_mz": "504.0050",
        "pt_plus_4_expected_mz": "504.0052",
        "cid_nl_base_status": "not_required",
        "cid_nl_base_join_mode": "not_required",
    }


def _summary_row(
    candidate_id: str,
    status: str,
    *,
    schema_version: str = trace_batch.ANCHOR_LOCAL_SCHEMA_VERSION,
    trace_extraction_sha256: str = "b" * 64,
) -> dict[str, str]:
    assignment_sha256 = pt_pattern_candidate_assignment_fingerprint(
        _candidate_row(candidate_id)
    )
    return {
        "schema_version": schema_version,
        "sample_stem": "SampleA",
        "candidate_id": candidate_id,
        "candidate_assignment_sha256": assignment_sha256,
        "trace_extraction_sha256": trace_extraction_sha256,
        "trace_artifact_role": (
            "anchor_local"
            if schema_version == trace_batch.ANCHOR_LOCAL_SCHEMA_VERSION
            else "full_context"
        ),
        "scan_pattern_seed_status": "full_pattern_seeded",
        "base_mz": "500.0",
        "base_rt_apex_min": "10.0",
        "rt_half_window_sec": "30",
        "ppm": "20",
        "mz_source": "expected",
        "trace_decision_basis": evidence.TRACE_DECISION_BASIS,
        "trace_smoothing_method": evidence.TRACE_SMOOTH_METHOD,
        "trace_smoothing_window_points": str(evidence.TRACE_SMOOTH_WINDOW_POINTS),
        "min_effective_smoothing_window_sec": "9",
        "min_median_rt_step_sec": "0.06",
        "max_trace_point_count": "31",
        "positions_requested": "4",
        "positions_with_points": "4",
        "positions_with_signal": "4",
        "non_base_positions_supported": "3",
        "max_abs_apex_delta_sec": "2",
        "min_pearson_r_to_base": "0.95",
        "min_active_overlap_fraction": "0.9",
        "min_shifted_pearson_r_to_base": "0.98",
        "max_abs_shape_shift_sec_to_base": "1",
        "raw_max_abs_apex_delta_sec": "2",
        "raw_min_pearson_r_to_base": "0.8",
        "raw_min_active_overlap_fraction": "0.7",
        "trace_ambiguity_status": "review_required",
        "trace_ambiguity_flags": (
            "shifted_only_positions=pt_plus_4;shifted_only_support_present"
        ),
        "shifted_only_position_labels": "pt_plus_4",
        "anchor_segmentation_status": "clear",
        "anchor_segmentation_reason": "one_anchor_segment_per_position",
        "anchor_segmentation_common_rt_start_min": "9.7",
        "anchor_segmentation_common_rt_end_min": "10.1",
        "anchor_segmentation_common_duration_sec": "24.0",
        "anchor_segmentation_common_start_owner_labels": "base",
        "anchor_segmentation_common_end_owner_labels": "pt_plus_4",
        "anchor_segmentation_boundary_review_status": "review_required",
        "anchor_segmentation_boundary_review_position_labels": "pt_plus_4",
        "anchor_segmentation_max_left_clip_sec": "6.0",
        "anchor_segmentation_max_right_clip_sec": "18.0",
        "anchor_segmentation_min_common_retained_fraction": "0.5",
        "anchor_segmentation_max_common_left_residual_fraction": "0.10",
        "anchor_segmentation_max_common_right_residual_fraction": "0.49",
        "anchor_segmentation_descending_at_common_right_position_labels": (
            "base|pt_plus_1|pt_plus_2|pt_plus_4"
        ),
        "trace_support_status": "supported",
        "combined_pt_pattern_status": status,
        "reason": "all_non_base_positions_trace_supported",
    }


def _metric_rows(
    candidate_id: str,
    *,
    schema_version: str = trace_batch.ANCHOR_LOCAL_SCHEMA_VERSION,
    trace_extraction_sha256: str = "b" * 64,
) -> list[dict[str, str]]:
    assignment_sha256 = pt_pattern_candidate_assignment_fingerprint(
        _candidate_row(candidate_id)
    )
    return [
        {
            "schema_version": schema_version,
            "sample_stem": "SampleA",
            "candidate_id": candidate_id,
            "candidate_assignment_sha256": assignment_sha256,
            "trace_extraction_sha256": trace_extraction_sha256,
            "trace_artifact_role": (
                "anchor_local"
                if schema_version == trace_batch.ANCHOR_LOCAL_SCHEMA_VERSION
                else "full_context"
            ),
            "position_label": label,
            "target_mz": {
                "base": "500",
                "pt_plus_1": "501.002",
                "pt_plus_2": "502.002",
                "pt_plus_4": "504.005",
            }[label],
            "rt_min": "9.5",
            "rt_max": "10.5",
            "ppm": "20",
            "trace_decision_basis": evidence.TRACE_DECISION_BASIS,
            "trace_smoothing_method": evidence.TRACE_SMOOTH_METHOD,
            "trace_smoothing_window_points": str(evidence.TRACE_SMOOTH_WINDOW_POINTS),
            "point_count": "31",
            "median_rt_step_sec": "0.06",
            "effective_smoothing_window_sec": "9",
            "raw_max_intensity": "100",
            "raw_apex_delta_sec_to_base": "0" if label == "base" else "2",
            "raw_smoothed_apex_delta_sec": "0.5",
            "raw_pearson_r_to_base": "1" if label == "base" else "0.8",
            "raw_active_overlap_fraction": "1" if label == "base" else "0.7",
            "max_intensity": "100",
            "apex_delta_sec_to_base": "0" if label == "base" else "2",
            "pearson_r_to_base": "1" if label == "base" else "0.95",
            "active_overlap_fraction": "1" if label == "base" else "0.9",
            "shifted_pearson_r_to_base": "1" if label == "base" else "0.98",
            "shape_shift_sec_to_base": "0" if label == "base" else "1",
            **(
                {
                    "anchor_segment_rt_start_min": {
                        "base": "9.7",
                        "pt_plus_1": "9.6",
                        "pt_plus_2": "9.6",
                        "pt_plus_4": "9.6",
                    }[label],
                    "anchor_segment_rt_end_min": {
                        "base": "10.2",
                        "pt_plus_1": "10.4",
                        "pt_plus_2": "10.2",
                        "pt_plus_4": "10.1",
                    }[label],
                    "anchor_segment_duration_sec": {
                        "base": "30",
                        "pt_plus_1": "48",
                        "pt_plus_2": "36",
                        "pt_plus_4": "30",
                    }[label],
                    "anchor_segment_class": (
                        "shoulder_candidate" if label == "pt_plus_4" else "peak"
                    ),
                    "anchor_segment_boundary_stop_reason": (
                        "review_boundary"
                        if label == "pt_plus_4"
                        else "baseline_crossing"
                    ),
                    "anchor_segment_owns_common_start": str(
                        label == "base"
                    ).lower(),
                    "anchor_segment_owns_common_end": str(
                        label == "pt_plus_4"
                    ).lower(),
                    "anchor_segment_left_clip_sec": {
                        "base": "0",
                        "pt_plus_1": "6",
                        "pt_plus_2": "6",
                        "pt_plus_4": "6",
                    }[label],
                    "anchor_segment_right_clip_sec": {
                        "base": "6",
                        "pt_plus_1": "18",
                        "pt_plus_2": "6",
                        "pt_plus_4": "0",
                    }[label],
                    "anchor_segment_common_retained_fraction": {
                        "base": "0.8",
                        "pt_plus_1": "0.5",
                        "pt_plus_2": "0.666667",
                        "pt_plus_4": "0.8",
                    }[label],
                    "anchor_segment_common_left_residual_fraction": (
                        "0.10" if label == "base" else "0.05"
                    ),
                    "anchor_segment_common_right_residual_fraction": (
                        "0.49" if label == "pt_plus_4" else "0.05"
                    ),
                    "anchor_segment_common_left_trend": "ascending",
                    "anchor_segment_common_right_trend": "descending",
                }
                if schema_version == trace_batch.ANCHOR_LOCAL_SCHEMA_VERSION
                else {}
            ),
            "trace_quality_flags": (
                "shifted_only_support" if label == "pt_plus_4" else "none"
            ),
            "trace_support_status": "supported",
            "reason": "unit_fixture",
        }
        for label in evidence.POSITION_LABELS
    ]


def _nonclear_anchor_metric_rows(candidate_id: str) -> list[dict[str, str]]:
    rows = _metric_rows(candidate_id)
    blank_numeric_fields = (
        "rt_min",
        "rt_max",
        "median_rt_step_sec",
        "effective_smoothing_window_sec",
        "raw_max_intensity",
        "raw_apex_rt_min",
        "raw_apex_delta_sec_to_base",
        "raw_smoothed_apex_delta_sec",
        "raw_height_ratio_to_base",
        "raw_pearson_r_to_base",
        "raw_active_overlap_fraction",
        "max_intensity",
        "apex_rt_min",
        "apex_delta_sec_to_base",
        "height_ratio_to_base",
        "pearson_r_to_base",
        "active_overlap_fraction",
        "shifted_pearson_r_to_base",
        "shape_shift_sec_to_base",
    )
    for row in rows:
        row["point_count"] = "0"
        for field in blank_numeric_fields:
            row[field] = ""
        for field in evidence.ANCHOR_SEGMENTATION_METRIC_COLUMNS:
            row[field] = ""
        if row["position_label"] == "base":
            row["trace_support_status"] = "missing_signal"
            row["reason"] = "missing_signal"
        else:
            row["trace_support_status"] = "missing_points"
            row["reason"] = "no_trace_points"
        row["trace_quality_flags"] = "none"
    return rows


def _apply_anchor_projection(
    summary_row: dict[str, str],
    metric_rows: list[dict[str, str]],
) -> None:
    status = str(summary_row["anchor_segmentation_status"])
    projected_metrics = trace_batch.project_anchor_local_metric_rows(
        metric_rows,
        segmentation_status=status,
    )
    for metric_row, projected_metric in zip(
        metric_rows,
        projected_metrics,
        strict=True,
    ):
        metric_row.update(
            {
                field: format_diagnostic_value(value)
                for field, value in projected_metric.items()
            }
        )
    summary_row.update(
        {
            field: format_diagnostic_value(value)
            for field, value in (
                trace_batch.project_anchor_segmentation_summary_fields(
                    segmentation_status=status,
                    segmentation_reason=str(
                        summary_row["anchor_segmentation_reason"]
                    ),
                    metric_rows=projected_metrics,
                ).items()
            )
        }
    )


def _write_trace_bundle_fixture(
    *,
    candidates_csv: Path,
    summary_csv: Path,
    metrics_csv: Path,
    summary_rows: list[dict[str, str]],
    metric_rows: list[dict[str, str]],
    project_call_fields: bool = True,
    project_anchor_fields: bool = True,
) -> Path:
    schema_version = summary_rows[0]["schema_version"]
    artifact_role = summary_rows[0]["trace_artifact_role"]
    is_anchor = schema_version == trace_batch.ANCHOR_LOCAL_SCHEMA_VERSION
    candidate_assignments = tuple(
        (row["candidate_id"], row["candidate_assignment_sha256"])
        for row in summary_rows
    )
    candidates_csv_sha256 = file_sha256(candidates_csv).lower()
    trace_extraction_sha256 = pt_pattern_trace_extraction_fingerprint(
        candidates_csv_sha256=candidates_csv_sha256,
        raw_sha256="c" * 64,
        source_sample_stem="SampleA",
        limit=len(summary_rows),
        offset=0,
        candidate_assignments=candidate_assignments,
        ppm=20.0,
        rt_half_window_sec=30.0,
        mz_source="expected",
        active_fraction=0.1,
        min_pearson_r=0.7,
        max_apex_delta_sec=30.0,
        trace_decision_basis=evidence.TRACE_DECISION_BASIS,
        trace_smoothing_method=evidence.TRACE_SMOOTH_METHOD,
        trace_smoothing_window_points=evidence.TRACE_SMOOTH_WINDOW_POINTS,
        anchor_segmentation_selector=trace_batch.ANCHOR_SEGMENTATION_SELECTOR,
    )
    for row in [*summary_rows, *metric_rows]:
        row["trace_extraction_sha256"] = trace_extraction_sha256
    if project_call_fields:
        metrics_by_candidate: dict[str, list[dict[str, str]]] = {}
        for metric_row in metric_rows:
            metrics_by_candidate.setdefault(
                str(metric_row["candidate_id"]),
                [],
            ).append(metric_row)
        for summary_row in summary_rows:
            candidate_metrics = metrics_by_candidate[
                str(summary_row["candidate_id"])
            ]
            base_metric = next(
                row
                for row in candidate_metrics
                if row["position_label"] == "base"
            )
            for metric_row in candidate_metrics:
                metric_row.update(
                    evidence.trace_metric_call_fields(
                        metric_row,
                        base_metric_row=base_metric,
                        min_pearson_r=0.7,
                        max_apex_delta_sec=30.0,
                    )
                )
            aggregate = evidence.trace_summary_aggregate_fields(candidate_metrics)
            segmentation_status = str(
                summary_row.get("anchor_segmentation_status", "")
            ).strip()
            if segmentation_status and segmentation_status != "clear":
                segmentation_reason = str(
                    summary_row.get("anchor_segmentation_reason", "")
                ).strip()
                aggregate.update(
                    {
                        "trace_support_status": "review",
                        "combined_pt_pattern_status": (
                            "pt_pattern_candidate_review"
                        ),
                        "reason": (
                            f"anchor_segmentation_{segmentation_status}:"
                            f"{segmentation_reason}"
                        ),
                    }
                )
            summary_row.update(
                {
                    field: format_diagnostic_value(value)
                    for field, value in aggregate.items()
                }
            )
    if is_anchor and project_anchor_fields:
        for summary_row in summary_rows:
            candidate_id = str(summary_row["candidate_id"])
            indexes = [
                index
                for index, metric_row in enumerate(metric_rows)
                if str(metric_row["candidate_id"]) == candidate_id
            ]
            candidate_metrics = [metric_rows[index] for index in indexes]
            segmentation_status = str(
                summary_row["anchor_segmentation_status"]
            )
            projected_metrics = trace_batch.project_anchor_local_metric_rows(
                candidate_metrics,
                segmentation_status=segmentation_status,
            )
            for index, projected_metric in zip(
                indexes,
                projected_metrics,
                strict=True,
            ):
                metric_rows[index].update(
                    {
                        field: format_diagnostic_value(value)
                        for field, value in projected_metric.items()
                    }
                )
            projected_summary = (
                trace_batch.project_anchor_segmentation_summary_fields(
                    segmentation_status=segmentation_status,
                    segmentation_reason=str(
                        summary_row["anchor_segmentation_reason"]
                    ),
                    metric_rows=projected_metrics,
                )
            )
            summary_row.update(
                {
                    field: format_diagnostic_value(value)
                    for field, value in projected_summary.items()
                }
            )
    write_delimited_rows(
        summary_csv,
        summary_rows,
        (
            trace_batch.ANCHOR_LOCAL_SUMMARY_COLUMNS
            if is_anchor
            else evidence.SUMMARY_COLUMNS
        ),
    )
    write_delimited_rows(
        metrics_csv,
        metric_rows,
        (
            trace_batch.ANCHOR_LOCAL_METRIC_COLUMNS
            if is_anchor
            else evidence.METRIC_COLUMNS
        ),
    )
    manifest = {
        "schema_version": schema_version,
        "diagnostic_only": True,
        "raw_sha256": "c" * 64,
        "source_sample_stem": "SampleA",
        "limit": len(summary_rows),
        "offset": 0,
        "ppm": 20.0,
        "rt_half_window_sec": 30.0,
        "mz_source": "expected",
        "active_fraction": 0.1,
        "min_pearson_r": 0.7,
        "max_apex_delta_sec": 30.0,
        "trace_decision_basis": evidence.TRACE_DECISION_BASIS,
        "trace_smoothing_method": evidence.TRACE_SMOOTH_METHOD,
        "trace_smoothing_window_points": evidence.TRACE_SMOOTH_WINDOW_POINTS,
        "anchor_segmentation_selector": trace_batch.ANCHOR_SEGMENTATION_SELECTOR,
        "trace_points_written": False,
    }
    manifest.update(
        trace_bundle_manifest_fields(
            schema_version=schema_version,
            artifact_role=artifact_role,
            trace_extraction_sha256=trace_extraction_sha256,
            candidates_csv_sha256=candidates_csv_sha256,
            summary_csv=summary_csv,
            metrics_csv=metrics_csv,
            trace_points_csv=None,
            candidate_assignments=candidate_assignments,
        )
    )
    manifest_path = default_trace_manifest_path(
        summary_csv,
        artifact_role=artifact_role,
    )
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def test_build_review_packet_rows_samples_per_status_and_keeps_pending_oracle() -> None:
    rows = packet.build_review_packet_rows(
        candidate_rows=[
            _candidate_row("cand-supported-a"),
            _candidate_row("cand-supported-b"),
            _candidate_row("cand-partial"),
        ],
        summary_rows=[
            _summary_row("cand-supported-a", "pt_pattern_detected_diagnostic"),
            _summary_row("cand-supported-b", "pt_pattern_detected_diagnostic"),
            _summary_row("cand-partial", "pt_pattern_candidate_partial"),
        ],
        metric_rows=[
            *_metric_rows("cand-supported-a"),
            *_metric_rows("cand-supported-b"),
            *_metric_rows("cand-partial"),
        ],
        packet_label="unit_packet",
        source_label="pt_only",
        max_per_status=1,
    )

    assert [row["candidate_id"] for row in rows] == [
        "cand-supported-a",
        "cand-partial",
    ]
    assert rows[0]["schema_version"] == packet.ANCHOR_LOCAL_SCHEMA_VERSION
    assert rows[0]["readiness_tier"] == "diagnostic_only"
    assert rows[0]["evidence_owner"] == packet.EVIDENCE_OWNER
    assert rows[0]["review_bucket"] == "detected_diagnostic_review"
    assert rows[0]["human_review_decision"] == ""
    assert rows[0]["oracle_status"] == "pending_human_review"
    assert rows[0]["pt_plus_4_trace_support_status"] == "supported"
    assert "base_trace_support_status" not in rows[0]
    assert rows[0]["trace_decision_basis"] == evidence.TRACE_DECISION_BASIS
    assert rows[0]["min_shifted_pearson_r_to_base"] == "0.98"
    assert rows[0]["max_abs_shape_shift_sec_to_base"] == "1"
    assert rows[0]["anchor_segmentation_boundary_review_status"] == "review_required"
    assert rows[0]["anchor_segmentation_common_end_owner_labels"] == "pt_plus_4"
    assert rows[0]["anchor_segmentation_max_right_clip_sec"] == "18.0"
    assert rows[0]["trace_ambiguity_status"] == "review_required"
    assert rows[0]["trace_ambiguity_flags"] == (
        "shifted_only_positions=pt_plus_4;shifted_only_support_present"
    )
    assert rows[0]["shifted_only_position_labels"] == "pt_plus_4"
    assert rows[0]["min_effective_smoothing_window_sec"] == "9"
    assert rows[0]["min_median_rt_step_sec"] == "0.06"
    assert rows[0]["max_trace_point_count"] == "31"
    assert rows[0]["raw_min_pearson_r_to_base"] == "0.8"
    assert rows[0]["pt_plus_4_trace_quality_flags"] == "shifted_only_support"
    assert rows[0]["pt_plus_4_effective_smoothing_window_sec"] == "9"
    assert rows[0]["pt_plus_4_raw_smoothed_apex_delta_sec"] == "0.5"
    assert rows[0]["pt_plus_4_shifted_pearson_r_to_base"] == "0.98"
    assert rows[0]["pt_plus_4_shape_shift_sec_to_base"] == "1"
    assert rows[0]["pt_plus_4_raw_active_overlap_fraction"] == "0.7"


def test_review_packet_rejects_summary_candidate_missing_from_candidates() -> None:
    with pytest.raises(ValueError, match="summary candidate_id values missing"):
        packet.build_review_packet_rows(
            candidate_rows=[_candidate_row("cand-present")],
            summary_rows=[
                _summary_row("cand-missing", "pt_pattern_detected_diagnostic")
            ],
            metric_rows=_metric_rows("cand-missing"),
            packet_label="missing-candidate",
            source_label="pt_only",
            max_per_status=1,
        )


@pytest.mark.parametrize(
    ("field", "conflicting_value"),
    [
        ("sample_stem", "SampleB"),
        ("base_mz", "501.0"),
        ("base_rt_apex_min", "10.5"),
    ],
)
def test_review_packet_rejects_candidate_summary_identity_mismatch(
    field: str,
    conflicting_value: str,
) -> None:
    summary = _summary_row("cand", "pt_pattern_detected_diagnostic")
    summary[field] = conflicting_value

    with pytest.raises(ValueError, match=rf"{field}.*does not match"):
        packet.build_review_packet_rows(
            candidate_rows=[_candidate_row("cand")],
            summary_rows=[summary],
            metric_rows=_metric_rows("cand"),
            packet_label="identity-mismatch",
            source_label="pt_only",
            max_per_status=1,
        )


def test_review_packet_join_is_independent_of_input_row_order() -> None:
    candidate_a = _candidate_row("cand-a")
    candidate_b = _candidate_row("cand-b")
    candidate_b.update(
        {
            "base_mz": "600.0",
            "base_rt_apex_min": "11.0",
            "pt_plus_1_mz": "601.0020",
            "pt_plus_1_expected_mz": "601.0021",
            "pt_plus_2_mz": "602.0021",
            "pt_plus_2_expected_mz": "602.0023",
            "pt_plus_4_mz": "604.0050",
            "pt_plus_4_expected_mz": "604.0052",
        }
    )
    summary_a = _summary_row("cand-a", "pt_pattern_detected_diagnostic")
    summary_b = _summary_row("cand-b", "pt_pattern_detected_diagnostic")
    summary_b["base_mz"] = "600.0"
    summary_b["base_rt_apex_min"] = "11.0"
    summary_b["candidate_assignment_sha256"] = (
        pt_pattern_candidate_assignment_fingerprint(candidate_b)
    )
    metrics_b = _metric_rows("cand-b")
    for metric in metrics_b:
        metric["candidate_assignment_sha256"] = summary_b["candidate_assignment_sha256"]

    forward = packet.build_review_packet_rows(
        candidate_rows=[candidate_a, candidate_b],
        summary_rows=[summary_a, summary_b],
        metric_rows=[*_metric_rows("cand-a"), *metrics_b],
        packet_label="ordered",
        source_label="pt_only",
        max_per_status=2,
    )
    reversed_rows = packet.build_review_packet_rows(
        candidate_rows=[candidate_b, candidate_a],
        summary_rows=[summary_b, summary_a],
        metric_rows=[*reversed(metrics_b), *_metric_rows("cand-a")],
        packet_label="ordered",
        source_label="pt_only",
        max_per_status=2,
    )

    assert forward == reversed_rows


def test_review_packet_sorts_valid_zero_rt_before_later_rt() -> None:
    zero_rt_candidate = _candidate_row("zero-rt")
    zero_rt_candidate["base_rt_apex_min"] = "0.0"
    zero_rt_summary = _summary_row(
        "zero-rt",
        "pt_pattern_detected_diagnostic",
    )
    zero_rt_summary["base_rt_apex_min"] = "0.0"
    zero_rt_summary["candidate_assignment_sha256"] = (
        pt_pattern_candidate_assignment_fingerprint(zero_rt_candidate)
    )
    later_candidate = _candidate_row("later-rt")
    later_candidate["base_rt_apex_min"] = "1.0"
    later_summary = _summary_row(
        "later-rt",
        "pt_pattern_detected_diagnostic",
    )
    later_summary["base_rt_apex_min"] = "1.0"
    later_summary["candidate_assignment_sha256"] = (
        pt_pattern_candidate_assignment_fingerprint(later_candidate)
    )
    later_metrics = _metric_rows("later-rt")
    zero_rt_metrics = _metric_rows("zero-rt")
    for metric in later_metrics:
        metric["candidate_assignment_sha256"] = later_summary[
            "candidate_assignment_sha256"
        ]
    for metric in zero_rt_metrics:
        metric["candidate_assignment_sha256"] = zero_rt_summary[
            "candidate_assignment_sha256"
        ]

    rows = packet.build_review_packet_rows(
        candidate_rows=[later_candidate, zero_rt_candidate],
        summary_rows=[later_summary, zero_rt_summary],
        metric_rows=[*later_metrics, *zero_rt_metrics],
        packet_label="zero_rt_order",
        source_label="pt_only",
        max_per_status=2,
    )

    assert [row["candidate_id"] for row in rows] == ["zero-rt", "later-rt"]


def test_review_packet_rejects_duplicate_summary_candidate_id() -> None:
    summary = _summary_row("cand", "pt_pattern_detected_diagnostic")
    with pytest.raises(ValueError, match="duplicate summary candidate_id values"):
        packet.build_review_packet_rows(
            candidate_rows=[_candidate_row("cand")],
            summary_rows=[summary, dict(summary)],
            metric_rows=_metric_rows("cand"),
            packet_label="duplicate-summary",
            source_label="pt_only",
            max_per_status=1,
        )


def test_review_packet_rejects_missing_metric_candidate() -> None:
    with pytest.raises(ValueError, match="metric candidate_id set must exactly"):
        packet.build_review_packet_rows(
            candidate_rows=[_candidate_row("cand-a"), _candidate_row("cand-b")],
            summary_rows=[
                _summary_row("cand-a", "pt_pattern_detected_diagnostic"),
                _summary_row("cand-b", "pt_pattern_candidate_partial"),
            ],
            metric_rows=_metric_rows("cand-a"),
            packet_label="missing-metric-candidate",
            source_label="pt_only",
            max_per_status=1,
        )


def test_review_packet_rejects_unexpected_metric_candidate() -> None:
    with pytest.raises(ValueError, match="metric candidate_id set must exactly"):
        packet.build_review_packet_rows(
            candidate_rows=[_candidate_row("cand")],
            summary_rows=[_summary_row("cand", "pt_pattern_detected_diagnostic")],
            metric_rows=[*_metric_rows("cand"), *_metric_rows("unexpected")],
            packet_label="unexpected-metric-candidate",
            source_label="pt_only",
            max_per_status=1,
        )


@pytest.mark.parametrize(
    ("field", "value", "expected_error"),
    [
        ("candidate_assignment_sha256", "c" * 64, "candidate_assignment_sha256"),
        ("trace_extraction_sha256", "c" * 64, "trace_extraction_sha256"),
        ("trace_artifact_role", "full_context", "trace_artifact_role"),
        ("schema_version", "", "schema_version"),
        ("sample_stem", "WrongSample", "sample_stem"),
    ],
)
def test_review_packet_rejects_stale_or_untyped_metric_lineage(
    field: str,
    value: str,
    expected_error: str,
) -> None:
    metrics = _metric_rows("cand")
    metrics[0][field] = value

    with pytest.raises(ValueError, match=expected_error):
        packet.build_review_packet_rows(
            candidate_rows=[_candidate_row("cand")],
            summary_rows=[_summary_row("cand", "pt_pattern_detected_diagnostic")],
            metric_rows=metrics,
            packet_label="stale-lineage",
            source_label="pt_only",
            max_per_status=1,
        )


def test_review_packet_rejects_header_only_trace_artifacts() -> None:
    with pytest.raises(ValueError, match="at least one candidate row"):
        packet.build_review_packet_rows(
            candidate_rows=[],
            summary_rows=[],
            metric_rows=[],
            packet_label="empty",
            source_label="pt_only",
            max_per_status=1,
        )


@pytest.mark.parametrize(
    ("mutate", "expected_error"),
    [
        ("missing", "metric position_label set must be exactly"),
        ("unknown", "metric position_label set must be exactly"),
        ("duplicate", "duplicate metric candidate/position values"),
    ],
)
def test_review_packet_rejects_invalid_metric_position_set(
    mutate: str,
    expected_error: str,
) -> None:
    metrics = _metric_rows("cand")
    if mutate == "missing":
        metrics.pop()
    elif mutate == "unknown":
        metrics[-1]["position_label"] = "pt_plus_3"
    else:
        metrics.append(dict(metrics[-1]))

    with pytest.raises(ValueError, match=expected_error):
        packet.build_review_packet_rows(
            candidate_rows=[_candidate_row("cand")],
            summary_rows=[_summary_row("cand", "pt_pattern_detected_diagnostic")],
            metric_rows=metrics,
            packet_label="invalid-metric-position-set",
            source_label="pt_only",
            max_per_status=1,
        )


def test_review_packet_cli_writes_csv_and_sidecar(tmp_path: Path) -> None:
    candidates_csv = tmp_path / "pt_pattern_candidates.csv"
    summary_csv = tmp_path / "pt_pattern_trace_summary.csv"
    metrics_csv = tmp_path / "pt_pattern_trace_metrics.csv"

    write_delimited_rows(
        candidates_csv,
        [_candidate_row("cand-supported")],
        packet.CANDIDATE_REQUIRED_COLUMNS,
    )
    trace_manifest = _write_trace_bundle_fixture(
        candidates_csv=candidates_csv,
        summary_csv=summary_csv,
        metrics_csv=metrics_csv,
        summary_rows=[_summary_row("cand-supported", "pt_pattern_detected_diagnostic")],
        metric_rows=_metric_rows("cand-supported"),
    )

    from tools.diagnostics import pt_pattern_review_packet as cli

    assert (
        cli.main(
            [
                "--candidates-csv",
                str(candidates_csv),
                "--trace-summary-csv",
                str(summary_csv),
                "--trace-metrics-csv",
                str(metrics_csv),
                "--trace-manifest-json",
                str(trace_manifest),
                "--output-dir",
                str(tmp_path / "review_packet"),
                "--packet-label",
                "unit_packet",
                "--source-label",
                "pt_only",
            ]
        )
        == 0
    )

    rows = read_delimited_rows(
        tmp_path / "review_packet" / "pt_pattern_manual_review_packet.csv",
        required_columns=packet.PACKET_COLUMNS,
        delimiter=",",
    )
    assert rows[0]["human_review_decision"] == ""
    assert rows[0]["oracle_status"] == "pending_human_review"

    sidecar = json.loads(
        (tmp_path / "review_packet" / "pt_pattern_manual_review_packet.json").read_text(
            encoding="utf-8"
        )
    )
    assert sidecar["schema_version"] == packet.ANCHOR_LOCAL_SCHEMA_VERSION
    assert sidecar["readiness_tier"] == "diagnostic_only"
    assert sidecar["packet_row_count"] == 1
    assert sidecar["packet_manifest_version"] == packet.PACKET_MANIFEST_VERSION
    assert sidecar["trace_artifact_role"] == "anchor_local"
    assert sidecar["human_review_decision_values"] == [
        "accepted",
        "rejected",
        "uncertain",
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("anchor_segmentation_common_rt_end_min", "10.0"),
        ("anchor_segmentation_max_right_clip_sec", "33"),
        ("anchor_segmentation_common_end_owner_labels", "base"),
        ("anchor_segmentation_boundary_review_status", "clear"),
    ],
)
def test_verified_anchor_bundle_rejects_summary_not_projected_from_metrics(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    candidate_id = "cand-anchor-projection"
    candidates_csv = tmp_path / "pt_pattern_candidates.csv"
    summary_csv = tmp_path / "pt_pattern_trace_summary.csv"
    metrics_csv = tmp_path / "pt_pattern_trace_metrics.csv"
    write_delimited_rows(
        candidates_csv,
        [_candidate_row(candidate_id)],
        packet.CANDIDATE_REQUIRED_COLUMNS,
    )
    summary = _summary_row(candidate_id, "pt_pattern_detected_diagnostic")
    metrics = _metric_rows(candidate_id)
    _apply_anchor_projection(summary, metrics)
    summary[field] = value
    manifest_json = _write_trace_bundle_fixture(
        candidates_csv=candidates_csv,
        summary_csv=summary_csv,
        metrics_csv=metrics_csv,
        summary_rows=[summary],
        metric_rows=metrics,
        project_anchor_fields=False,
    )

    with pytest.raises(ValueError, match="anchor projection"):
        load_verified_trace_summary_bundle(
            summary_csv,
            expected_artifact_role="anchor_local",
            metrics_csv=metrics_csv,
            manifest_json=manifest_json,
            candidates_csv=candidates_csv,
        )


def test_verified_anchor_bundle_accepts_producer_legal_nonclear_override(
    tmp_path: Path,
) -> None:
    candidate_id = "cand-anchor-missing"
    candidates_csv = tmp_path / "pt_pattern_candidates.csv"
    summary_csv = tmp_path / "pt_pattern_trace_summary.csv"
    metrics_csv = tmp_path / "pt_pattern_trace_metrics.csv"
    write_delimited_rows(
        candidates_csv,
        [_candidate_row(candidate_id)],
        packet.CANDIDATE_REQUIRED_COLUMNS,
    )
    summary = _summary_row(candidate_id, "pt_pattern_candidate_review")
    segmentation_reason = "missing_anchor_segment:pt_plus_4"
    summary.update(
        {
            "anchor_segmentation_status": "missing_anchor_segment",
            "anchor_segmentation_reason": segmentation_reason,
            "anchor_segmentation_common_rt_start_min": "",
            "anchor_segmentation_common_rt_end_min": "",
            "anchor_segmentation_common_duration_sec": "",
            "anchor_segmentation_common_start_owner_labels": "none",
            "anchor_segmentation_common_end_owner_labels": "none",
            "anchor_segmentation_boundary_review_status": "unavailable",
            "anchor_segmentation_boundary_review_position_labels": "none",
            "anchor_segmentation_max_left_clip_sec": "",
            "anchor_segmentation_max_right_clip_sec": "",
            "anchor_segmentation_min_common_retained_fraction": "",
            "anchor_segmentation_max_common_left_residual_fraction": "",
            "anchor_segmentation_max_common_right_residual_fraction": "",
            "anchor_segmentation_descending_at_common_right_position_labels": (
                "none"
            ),
            "trace_support_status": "review",
            "combined_pt_pattern_status": "pt_pattern_candidate_review",
            "reason": (
                "anchor_segmentation_missing_anchor_segment:"
                f"{segmentation_reason}"
            ),
        }
    )
    metrics = _nonclear_anchor_metric_rows(candidate_id)
    manifest_json = _write_trace_bundle_fixture(
        candidates_csv=candidates_csv,
        summary_csv=summary_csv,
        metrics_csv=metrics_csv,
        summary_rows=[summary],
        metric_rows=metrics,
    )

    bundle = load_verified_trace_summary_bundle(
        summary_csv,
        expected_artifact_role="anchor_local",
        metrics_csv=metrics_csv,
        manifest_json=manifest_json,
        candidates_csv=candidates_csv,
    )

    assert bundle.summary_rows[0]["trace_support_status"] == "review"


@pytest.mark.parametrize("mutation", ["call", "boundary_evidence"])
def test_verified_anchor_bundle_rejects_invalid_nonclear_envelope(
    tmp_path: Path,
    mutation: str,
) -> None:
    candidate_id = "cand-anchor-invalid-nonclear"
    candidates_csv = tmp_path / "pt_pattern_candidates.csv"
    summary_csv = tmp_path / "pt_pattern_trace_summary.csv"
    metrics_csv = tmp_path / "pt_pattern_trace_metrics.csv"
    write_delimited_rows(
        candidates_csv,
        [_candidate_row(candidate_id)],
        packet.CANDIDATE_REQUIRED_COLUMNS,
    )
    summary = _summary_row(candidate_id, "pt_pattern_candidate_review")
    segmentation_reason = "missing_anchor_segment:pt_plus_4"
    summary.update(
        {
            "anchor_segmentation_status": "missing_anchor_segment",
            "anchor_segmentation_reason": segmentation_reason,
            "anchor_segmentation_common_rt_start_min": "",
            "anchor_segmentation_common_rt_end_min": "",
            "anchor_segmentation_common_duration_sec": "",
            "anchor_segmentation_common_start_owner_labels": "none",
            "anchor_segmentation_common_end_owner_labels": "none",
            "anchor_segmentation_boundary_review_status": "unavailable",
            "anchor_segmentation_boundary_review_position_labels": "none",
            "anchor_segmentation_max_left_clip_sec": "",
            "anchor_segmentation_max_right_clip_sec": "",
            "anchor_segmentation_min_common_retained_fraction": "",
            "anchor_segmentation_max_common_left_residual_fraction": "",
            "anchor_segmentation_max_common_right_residual_fraction": "",
            "anchor_segmentation_descending_at_common_right_position_labels": (
                "none"
            ),
            "trace_support_status": "review",
            "combined_pt_pattern_status": "pt_pattern_candidate_review",
            "reason": (
                "anchor_segmentation_missing_anchor_segment:"
                f"{segmentation_reason}"
            ),
        }
    )
    metrics = _nonclear_anchor_metric_rows(candidate_id)
    aggregate = evidence.trace_summary_aggregate_fields(metrics)
    aggregate.update(
        {
            "trace_support_status": "review",
            "combined_pt_pattern_status": "pt_pattern_candidate_review",
            "reason": (
                "anchor_segmentation_missing_anchor_segment:"
                f"{segmentation_reason}"
            ),
        }
    )
    summary.update(
        {
            field: format_diagnostic_value(value)
            for field, value in aggregate.items()
        }
    )
    if mutation == "call":
        summary["combined_pt_pattern_status"] = "pt_pattern_detected_diagnostic"
    else:
        metrics[0]["anchor_segment_rt_start_min"] = "9.8"
    manifest_json = _write_trace_bundle_fixture(
        candidates_csv=candidates_csv,
        summary_csv=summary_csv,
        metrics_csv=metrics_csv,
        summary_rows=[summary],
        metric_rows=metrics,
        project_call_fields=False,
        project_anchor_fields=False,
    )

    with pytest.raises(ValueError, match="aggregate fields|boundary evidence"):
        load_verified_trace_summary_bundle(
            summary_csv,
            expected_artifact_role="anchor_local",
            metrics_csv=metrics_csv,
            manifest_json=manifest_json,
            candidates_csv=candidates_csv,
        )


@pytest.mark.parametrize(
    ("field", "value", "expected_error"),
    [
        (
            "raw_smoothed_apex_delta_sec",
            "garbage",
            "finite numeric value",
        ),
        ("raw_max_intensity", "nan", "finite numeric value"),
        ("raw_max_intensity", "-1", "must be non-negative"),
        (
            "raw_active_overlap_fraction",
            "1.1",
            "must be between 0 and 1",
        ),
        ("point_count", "3.5", "non-negative integer"),
        (
            "anchor_segment_common_left_residual_fraction",
            "garbage",
            "finite numeric value",
        ),
    ],
)
def test_verified_trace_bundle_rejects_invalid_metric_numeric_domains(
    tmp_path: Path,
    field: str,
    value: str,
    expected_error: str,
) -> None:
    candidate_id = "cand-invalid-numeric"
    candidates_csv = tmp_path / "pt_pattern_candidates.csv"
    summary_csv = tmp_path / "pt_pattern_trace_summary.csv"
    metrics_csv = tmp_path / "pt_pattern_trace_metrics.csv"
    write_delimited_rows(
        candidates_csv,
        [_candidate_row(candidate_id)],
        packet.CANDIDATE_REQUIRED_COLUMNS,
    )
    summary = _summary_row(candidate_id, "pt_pattern_detected_diagnostic")
    metrics = _metric_rows(candidate_id)
    _apply_anchor_projection(summary, metrics)
    metrics[0][field] = value
    manifest_json = _write_trace_bundle_fixture(
        candidates_csv=candidates_csv,
        summary_csv=summary_csv,
        metrics_csv=metrics_csv,
        summary_rows=[summary],
        metric_rows=metrics,
        project_call_fields=False,
        project_anchor_fields=False,
    )

    with pytest.raises(ValueError, match=expected_error):
        load_verified_trace_summary_bundle(
            summary_csv,
            expected_artifact_role="anchor_local",
            metrics_csv=metrics_csv,
            manifest_json=manifest_json,
            candidates_csv=candidates_csv,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("anchor_segment_duration_sec", "51"),
        ("anchor_segment_left_clip_sec", "4"),
        ("anchor_segment_right_clip_sec", "22"),
        ("anchor_segment_common_retained_fraction", "0.6"),
    ],
)
def test_verified_anchor_bundle_rejects_internally_impossible_segment_metrics(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    candidate_id = "cand-impossible-anchor-metric"
    candidates_csv = tmp_path / "pt_pattern_candidates.csv"
    summary_csv = tmp_path / "pt_pattern_trace_summary.csv"
    metrics_csv = tmp_path / "pt_pattern_trace_metrics.csv"
    write_delimited_rows(
        candidates_csv,
        [_candidate_row(candidate_id)],
        packet.CANDIDATE_REQUIRED_COLUMNS,
    )
    summary = _summary_row(candidate_id, "pt_pattern_detected_diagnostic")
    metrics = _metric_rows(candidate_id)
    _apply_anchor_projection(summary, metrics)
    metrics[1][field] = value
    manifest_json = _write_trace_bundle_fixture(
        candidates_csv=candidates_csv,
        summary_csv=summary_csv,
        metrics_csv=metrics_csv,
        summary_rows=[summary],
        metric_rows=metrics,
        project_anchor_fields=False,
    )

    with pytest.raises(ValueError, match="anchor projection"):
        load_verified_trace_summary_bundle(
            summary_csv,
            expected_artifact_role="anchor_local",
            metrics_csv=metrics_csv,
            manifest_json=manifest_json,
            candidates_csv=candidates_csv,
        )


def test_verified_anchor_bundle_rejects_hidden_boundary_owner(
    tmp_path: Path,
) -> None:
    candidate_id = "cand-false-boundary-owner"
    candidates_csv = tmp_path / "pt_pattern_candidates.csv"
    summary_csv = tmp_path / "pt_pattern_trace_summary.csv"
    metrics_csv = tmp_path / "pt_pattern_trace_metrics.csv"
    write_delimited_rows(
        candidates_csv,
        [_candidate_row(candidate_id)],
        packet.CANDIDATE_REQUIRED_COLUMNS,
    )
    summary = _summary_row(candidate_id, "pt_pattern_detected_diagnostic")
    metrics = _metric_rows(candidate_id)
    _apply_anchor_projection(summary, metrics)
    metrics[2].update(
        {
            "anchor_segment_rt_start_min": "9.7",
            "anchor_segment_duration_sec": "30",
            "anchor_segment_owns_common_start": "false",
            "anchor_segment_left_clip_sec": "0",
            "anchor_segment_common_retained_fraction": "0.8",
        }
    )
    manifest_json = _write_trace_bundle_fixture(
        candidates_csv=candidates_csv,
        summary_csv=summary_csv,
        metrics_csv=metrics_csv,
        summary_rows=[summary],
        metric_rows=metrics,
        project_anchor_fields=False,
    )

    with pytest.raises(ValueError, match="anchor projection"):
        load_verified_trace_summary_bundle(
            summary_csv,
            expected_artifact_role="anchor_local",
            metrics_csv=metrics_csv,
            manifest_json=manifest_json,
            candidates_csv=candidates_csv,
        )


@pytest.mark.parametrize(
    ("field", "value", "expected_error"),
    [
        ("base_mz", "nan", "base_mz must be finite and positive"),
        (
            "base_rt_apex_min",
            "-1",
            "base_rt_apex_min must be finite and non-negative",
        ),
    ],
)
def test_verified_trace_bundle_rejects_invalid_summary_identity_coordinates(
    tmp_path: Path,
    field: str,
    value: str,
    expected_error: str,
) -> None:
    candidate_id = "cand-invalid-summary-coordinate"
    candidates_csv = tmp_path / "pt_pattern_candidates.csv"
    summary_csv = tmp_path / "pt_pattern_trace_summary.csv"
    metrics_csv = tmp_path / "pt_pattern_trace_metrics.csv"
    write_delimited_rows(
        candidates_csv,
        [_candidate_row(candidate_id)],
        packet.CANDIDATE_REQUIRED_COLUMNS,
    )
    summary = _summary_row(candidate_id, "pt_pattern_detected_diagnostic")
    summary[field] = value
    manifest_json = _write_trace_bundle_fixture(
        candidates_csv=candidates_csv,
        summary_csv=summary_csv,
        metrics_csv=metrics_csv,
        summary_rows=[summary],
        metric_rows=_metric_rows(candidate_id),
    )

    with pytest.raises(ValueError, match=expected_error):
        load_verified_trace_summary_bundle(
            summary_csv,
            expected_artifact_role="anchor_local",
            metrics_csv=metrics_csv,
            manifest_json=manifest_json,
            candidates_csv=candidates_csv,
        )


def test_verified_anchor_bundle_rejects_common_interval_away_from_anchor(
    tmp_path: Path,
) -> None:
    candidate_id = "cand-shifted-anchor-interval"
    candidates_csv = tmp_path / "pt_pattern_candidates.csv"
    summary_csv = tmp_path / "pt_pattern_trace_summary.csv"
    metrics_csv = tmp_path / "pt_pattern_trace_metrics.csv"
    write_delimited_rows(
        candidates_csv,
        [_candidate_row(candidate_id)],
        packet.CANDIDATE_REQUIRED_COLUMNS,
    )
    summary = _summary_row(candidate_id, "pt_pattern_detected_diagnostic")
    metrics = _metric_rows(candidate_id)
    _apply_anchor_projection(summary, metrics)
    summary["anchor_segmentation_common_rt_start_min"] = "11.7"
    summary["anchor_segmentation_common_rt_end_min"] = "12.1"
    for metric in metrics:
        metric["anchor_segment_rt_start_min"] = format_diagnostic_value(
            float(metric["anchor_segment_rt_start_min"]) + 2.0
        )
        metric["anchor_segment_rt_end_min"] = format_diagnostic_value(
            float(metric["anchor_segment_rt_end_min"]) + 2.0
        )
    manifest_json = _write_trace_bundle_fixture(
        candidates_csv=candidates_csv,
        summary_csv=summary_csv,
        metrics_csv=metrics_csv,
        summary_rows=[summary],
        metric_rows=metrics,
        project_anchor_fields=False,
    )

    with pytest.raises(ValueError, match="must contain base_rt_apex_min"):
        load_verified_trace_summary_bundle(
            summary_csv,
            expected_artifact_role="anchor_local",
            metrics_csv=metrics_csv,
            manifest_json=manifest_json,
            candidates_csv=candidates_csv,
        )


def test_verified_anchor_bundle_allows_segment_outside_anchor_local_trace_bounds(
    tmp_path: Path,
) -> None:
    candidate_id = "cand-segment-outside-window"
    candidates_csv = tmp_path / "pt_pattern_candidates.csv"
    summary_csv = tmp_path / "pt_pattern_trace_summary.csv"
    metrics_csv = tmp_path / "pt_pattern_trace_metrics.csv"
    write_delimited_rows(
        candidates_csv,
        [_candidate_row(candidate_id)],
        packet.CANDIDATE_REQUIRED_COLUMNS,
    )
    summary = _summary_row(candidate_id, "pt_pattern_detected_diagnostic")
    metrics = _metric_rows(candidate_id)
    _apply_anchor_projection(summary, metrics)
    summary.update(
        {
            "anchor_segmentation_max_left_clip_sec": "42",
            "anchor_segmentation_min_common_retained_fraction": "0.285714",
        }
    )
    metrics[1].update(
        {
            "anchor_segment_rt_start_min": "9",
            "anchor_segment_duration_sec": "84",
            "anchor_segment_left_clip_sec": "42",
            "anchor_segment_common_retained_fraction": "0.285714",
        }
    )
    manifest_json = _write_trace_bundle_fixture(
        candidates_csv=candidates_csv,
        summary_csv=summary_csv,
        metrics_csv=metrics_csv,
        summary_rows=[summary],
        metric_rows=metrics,
        project_anchor_fields=False,
    )

    bundle = load_verified_trace_summary_bundle(
        summary_csv,
        expected_artifact_role="anchor_local",
        metrics_csv=metrics_csv,
        manifest_json=manifest_json,
        candidates_csv=candidates_csv,
    )

    assert bundle.summary_rows[0]["anchor_segmentation_common_rt_start_min"] == (
        "9.7"
    )
    assert bundle.metric_rows[1]["anchor_segment_rt_start_min"] == "9"


def test_anchor_projection_rounds_primitives_before_derived_fields(
    tmp_path: Path,
) -> None:
    candidate_id = "cand-rounding-boundary"
    candidates_csv = tmp_path / "pt_pattern_candidates.csv"
    summary_csv = tmp_path / "pt_pattern_trace_summary.csv"
    metrics_csv = tmp_path / "pt_pattern_trace_metrics.csv"
    write_delimited_rows(
        candidates_csv,
        [_candidate_row(candidate_id)],
        packet.CANDIDATE_REQUIRED_COLUMNS,
    )
    summary = _summary_row(candidate_id, "pt_pattern_detected_diagnostic")
    metrics = _metric_rows(candidate_id)
    intervals = {
        "base": (9.926523896858458, 10.1),
        "pt_plus_1": (9.85585210329681, 10.038005422314543),
        "pt_plus_2": (9.8, 10.030055220378808),
        "pt_plus_4": (9.85, 10.08),
    }
    for metric in metrics:
        start, end = intervals[metric["position_label"]]
        metric["anchor_segment_rt_start_min"] = str(start)
        metric["anchor_segment_rt_end_min"] = str(end)
    _apply_anchor_projection(summary, metrics)
    manifest_json = _write_trace_bundle_fixture(
        candidates_csv=candidates_csv,
        summary_csv=summary_csv,
        metrics_csv=metrics_csv,
        summary_rows=[summary],
        metric_rows=metrics,
    )

    bundle = load_verified_trace_summary_bundle(
        summary_csv,
        expected_artifact_role="anchor_local",
        metrics_csv=metrics_csv,
        manifest_json=manifest_json,
        candidates_csv=candidates_csv,
    )

    assert bundle.summary_rows[0][
        "anchor_segmentation_min_common_retained_fraction"
    ]


def test_verified_trace_bundle_recomputes_position_call_from_scalar_evidence(
    tmp_path: Path,
) -> None:
    candidate_id = "cand-forged-supported-call"
    candidates_csv = tmp_path / "pt_pattern_candidates.csv"
    summary_csv = tmp_path / "pt_pattern_trace_summary.csv"
    metrics_csv = tmp_path / "pt_pattern_trace_metrics.csv"
    write_delimited_rows(
        candidates_csv,
        [_candidate_row(candidate_id)],
        packet.CANDIDATE_REQUIRED_COLUMNS,
    )
    metrics = _metric_rows(candidate_id)
    base_metric = metrics[0]
    for metric in metrics:
        metric.update(
            evidence.trace_metric_call_fields(
                metric,
                base_metric_row=base_metric,
                min_pearson_r=0.7,
                max_apex_delta_sec=30.0,
            )
        )
    forged_metric = metrics[1]
    forged_metric.update(
        {
            "apex_delta_sec_to_base": "999",
            "pearson_r_to_base": "-1",
            "active_overlap_fraction": "0",
            "shifted_pearson_r_to_base": "-1",
            "trace_quality_flags": "none",
            "trace_support_status": "supported",
            "reason": "rt_and_shape_supported",
        }
    )
    summary = _summary_row(candidate_id, "pt_pattern_detected_diagnostic")
    summary.update(
        {
            field: format_diagnostic_value(value)
            for field, value in evidence.trace_summary_aggregate_fields(
                metrics
            ).items()
        }
    )
    manifest_json = _write_trace_bundle_fixture(
        candidates_csv=candidates_csv,
        summary_csv=summary_csv,
        metrics_csv=metrics_csv,
        summary_rows=[summary],
        metric_rows=metrics,
        project_call_fields=False,
    )

    with pytest.raises(ValueError, match="call fields are not the deterministic"):
        load_verified_trace_summary_bundle(
            summary_csv,
            expected_artifact_role="anchor_local",
            metrics_csv=metrics_csv,
            manifest_json=manifest_json,
            candidates_csv=candidates_csv,
        )


@pytest.mark.parametrize(
    ("field", "value", "expected_error"),
    [
        ("ppm", 0.0, "ppm must be > 0"),
        ("rt_half_window_sec", 0.0, "rt_half_window_sec must be > 0"),
        ("active_fraction", 1.1, "active_fraction must be in"),
        ("min_pearson_r", 1.1, "min_pearson_r must be in"),
        ("max_apex_delta_sec", -1.0, "max_apex_delta_sec must be >= 0"),
    ],
)
def test_verified_trace_bundle_rejects_out_of_domain_manifest_settings(
    tmp_path: Path,
    field: str,
    value: float,
    expected_error: str,
) -> None:
    candidate_id = "cand-invalid-setting"
    candidates_csv = tmp_path / "pt_pattern_candidates.csv"
    summary_csv = tmp_path / "pt_pattern_trace_summary.csv"
    metrics_csv = tmp_path / "pt_pattern_trace_metrics.csv"
    write_delimited_rows(
        candidates_csv,
        [_candidate_row(candidate_id)],
        packet.CANDIDATE_REQUIRED_COLUMNS,
    )
    manifest_json = _write_trace_bundle_fixture(
        candidates_csv=candidates_csv,
        summary_csv=summary_csv,
        metrics_csv=metrics_csv,
        summary_rows=[
            _summary_row(candidate_id, "pt_pattern_detected_diagnostic")
        ],
        metric_rows=_metric_rows(candidate_id),
    )
    manifest = json.loads(manifest_json.read_text(encoding="utf-8"))
    manifest[field] = value
    manifest_json.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=expected_error):
        load_verified_trace_summary_bundle(
            summary_csv,
            expected_artifact_role="anchor_local",
            metrics_csv=metrics_csv,
            manifest_json=manifest_json,
            candidates_csv=candidates_csv,
        )


def test_review_packet_uses_current_full_columns_for_full_trace_summary(
    tmp_path: Path,
) -> None:
    candidates_csv = tmp_path / "pt_pattern_candidates.csv"
    summary_csv = tmp_path / "pt_pattern_trace_summary.csv"
    metrics_csv = tmp_path / "pt_pattern_trace_metrics.csv"
    write_delimited_rows(
        candidates_csv,
        [_candidate_row("cand-supported")],
        packet.CANDIDATE_REQUIRED_COLUMNS,
    )
    summary_rows = [
        _summary_row(
            "cand-supported",
            "pt_pattern_detected_diagnostic",
            schema_version=evidence.SCHEMA_VERSION,
        )
    ]
    metric_rows = _metric_rows(
        "cand-supported",
        schema_version=evidence.SCHEMA_VERSION,
    )
    trace_manifest = _write_trace_bundle_fixture(
        candidates_csv=candidates_csv,
        summary_csv=summary_csv,
        metrics_csv=metrics_csv,
        summary_rows=summary_rows,
        metric_rows=metric_rows,
    )

    outputs = packet.run_review_packet(
        candidates_csv=candidates_csv,
        trace_summary_csv=summary_csv,
        trace_metrics_csv=metrics_csv,
        output_dir=tmp_path / "legacy_packet",
        packet_label="legacy",
        source_label="pt_only",
        max_per_status=1,
        trace_manifest_json=trace_manifest,
    )
    rows = read_delimited_rows(
        outputs["packet_csv"],
        required_columns=packet.LEGACY_PACKET_COLUMNS,
        delimiter=",",
    )
    assert rows[0]["schema_version"] == packet.SCHEMA_VERSION
    assert "anchor_segmentation_status" not in rows[0]


def test_saved_review_packet_rejects_stale_trace_manifest_fingerprint(
    tmp_path: Path,
) -> None:
    candidates_csv = tmp_path / "pt_pattern_candidates.csv"
    summary_csv = tmp_path / "pt_pattern_trace_summary.csv"
    metrics_csv = tmp_path / "pt_pattern_trace_metrics.csv"
    write_delimited_rows(
        candidates_csv,
        [_candidate_row("cand-stale-manifest")],
        packet.CANDIDATE_REQUIRED_COLUMNS,
    )
    summary_rows = [
        _summary_row(
            "cand-stale-manifest",
            "pt_pattern_detected_diagnostic",
            schema_version=evidence.SCHEMA_VERSION,
        )
    ]
    metric_rows = _metric_rows(
        "cand-stale-manifest",
        schema_version=evidence.SCHEMA_VERSION,
    )
    trace_manifest = _write_trace_bundle_fixture(
        candidates_csv=candidates_csv,
        summary_csv=summary_csv,
        metrics_csv=metrics_csv,
        summary_rows=summary_rows,
        metric_rows=metric_rows,
    )
    outputs = packet.run_review_packet(
        candidates_csv=candidates_csv,
        trace_summary_csv=summary_csv,
        trace_metrics_csv=metrics_csv,
        output_dir=tmp_path / "packet",
        packet_label="stale-manifest",
        source_label="pt_only",
        max_per_status=1,
        trace_manifest_json=trace_manifest,
    )

    trace_payload = json.loads(trace_manifest.read_text(encoding="utf-8"))
    trace_payload["ppm"] = 21.0
    trace_manifest.write_text(
        json.dumps(trace_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    packet_manifest = json.loads(outputs["sidecar_json"].read_text(encoding="utf-8"))
    packet_manifest["input_sha256"]["trace_manifest_json"] = file_sha256(
        trace_manifest
    ).lower()
    outputs["sidecar_json"].write_text(
        json.dumps(packet_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="trace_extraction_sha256 is stale"):
        load_verified_review_packet_bundle(outputs["packet_csv"])


def test_review_packet_rejects_unknown_trace_schema() -> None:
    summary = _summary_row("cand", "pt_pattern_detected_diagnostic")
    summary["schema_version"] = "pt_pattern_trace_diagnosis.v999"

    with pytest.raises(ValueError, match="unsupported trace summary schema_version"):
        packet.build_review_packet_rows(
            candidate_rows=[_candidate_row("cand")],
            summary_rows=[summary],
            metric_rows=_metric_rows("cand"),
            packet_label="unknown",
            source_label="pt_only",
            max_per_status=1,
        )


def test_review_packet_rejects_partial_anchor_summary_schema() -> None:
    summary = _summary_row("cand", "pt_pattern_detected_diagnostic")
    summary.pop("anchor_segmentation_status")

    with pytest.raises(ValueError, match="missing anchor segmentation columns"):
        packet.build_review_packet_rows(
            candidate_rows=[_candidate_row("cand")],
            summary_rows=[summary],
            metric_rows=_metric_rows("cand"),
            packet_label="partial-anchor-summary",
            source_label="pt_only",
            max_per_status=1,
        )


def test_review_packet_rejects_partial_anchor_metric_schema() -> None:
    metrics = _metric_rows("cand")
    metrics[0].pop("anchor_segment_rt_start_min")

    with pytest.raises(ValueError, match="missing anchor segmentation columns"):
        packet.build_review_packet_rows(
            candidate_rows=[_candidate_row("cand")],
            summary_rows=[_summary_row("cand", "pt_pattern_detected_diagnostic")],
            metric_rows=metrics,
            packet_label="partial-anchor-metric",
            source_label="pt_only",
            max_per_status=1,
        )
