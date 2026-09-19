from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tests.pt_pattern_trace_fixtures import (
    write_overlay_lineage_bundle,
    write_verified_oracle_promotion_bundle,
)
from xic_extractor.discovery import pt_pattern_oracle_promotion as promotion
from xic_extractor.discovery import pt_pattern_oracle_thresholds as thresholds
from xic_extractor.discovery import pt_pattern_review_packet as packet
from xic_extractor.discovery import pt_pattern_trace_evidence as evidence
from xic_extractor.discovery.pt_pattern_artifact_contract import (
    PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE,
)
from xic_extractor.tabular_io import (
    read_delimited_rows,
    write_delimited_rows,
)

UNIT_REVIEW_SHA256 = "A" * 64
UNIT_OVERLAY_SUMMARY_SHA256 = "B" * 64


def _review_row(
    candidate_id: str,
    *,
    status: str = "pt_pattern_detected_diagnostic",
    supported: str = "3",
    apex_delta: str = "0.5",
    pearson: str = "0.95",
    overlap: str = "0.80",
    anchor_local: bool = False,
) -> dict[str, str]:
    columns = packet.PACKET_COLUMNS
    row = {column: "" for column in columns}
    row.update(
        {
            "schema_version": packet.SCHEMA_VERSION,
            "readiness_tier": packet.READINESS_TIER,
            "evidence_owner": packet.EVIDENCE_OWNER,
            "packet_label": "unit_packet",
            "source_label": "pt_only",
            "packet_row_id": f"pt_only|{candidate_id}",
            "sample_stem": "SampleA",
            "candidate_id": candidate_id,
            "candidate_assignment_sha256": hashlib.sha256(
                f"threshold-test-assignment|{candidate_id}".encode("utf-8")
            ).hexdigest(),
            "trace_extraction_sha256": "e" * 64,
            "trace_artifact_role": PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE,
            "base_mz": "500.0",
            "base_rt_apex_min": "10.0",
            "trace_decision_basis": evidence.TRACE_DECISION_BASIS,
            "trace_smoothing_method": evidence.TRACE_SMOOTH_METHOD,
            "trace_smoothing_window_points": str(evidence.TRACE_SMOOTH_WINDOW_POINTS),
            "combined_pt_pattern_status": status,
            "non_base_positions_supported": supported,
            "max_abs_apex_delta_sec": apex_delta,
            "min_pearson_r_to_base": pearson,
            "min_active_overlap_fraction": overlap,
            "oracle_status": "pending_human_review",
        }
    )
    row.update(
        {
            "trace_ambiguity_status": "clear",
            "anchor_segmentation_status": "clear",
            "anchor_segmentation_boundary_review_status": "clear",
        }
    )
    return row


def _oracle_row(
    review_row: dict[str, str],
    *,
    oracle_call: str,
    review_packet_sha256: str = UNIT_REVIEW_SHA256,
) -> dict[str, str]:
    candidate_id = review_row["candidate_id"]
    row = {column: "" for column in promotion.PROMOTION_COLUMNS}
    row.update(
        {
            "schema_version": promotion.SCHEMA_VERSION,
            "readiness_tier": promotion.READINESS_TIER,
            "evidence_owner": promotion.EVIDENCE_OWNER,
            "authority_scope": promotion.AUTHORITY_SCOPE,
            "oracle_packet_id": "unit_oracle",
            "oracle_status": "manual_oracle_promoted",
            "oracle_call": oracle_call,
            "oracle_detection_status": promotion.ORACLE_DETECTION_STATUS,
            "packet_row_id": review_row["packet_row_id"],
            "review_packet_csv_sha256": review_packet_sha256,
            "blind_overlay_summary_json_sha256": UNIT_OVERLAY_SUMMARY_SHA256,
            "dataset": "pt_only",
            "case_index": candidate_id,
            "sample_stem": review_row["sample_stem"],
            "candidate_id": candidate_id,
            "base_mz": "500.0",
            "base_rt_apex_min": "10.0",
            "adjudication_basis": "unit",
            "human_review_call": oracle_call,
            "machine_call": oracle_call,
            "machine_call_reason": "unit",
            "review_packet_schema_version": review_row["schema_version"],
            "review_packet_readiness_tier": review_row["readiness_tier"],
            "review_packet_oracle_status": review_row["oracle_status"],
            "review_packet_combined_pt_pattern_status": review_row[
                "combined_pt_pattern_status"
            ],
            "provenance_status": "exact_review_evidence_bundle_matched",
        }
    )
    return row


def test_threshold_calibration_selects_zero_false_positive_rule() -> None:
    review_rows = [
        _review_row("accept-a", pearson="0.92", overlap="0.75"),
        _review_row("accept-b", pearson="0.97", overlap="0.90"),
        _review_row("reject-shape", pearson="0.84", overlap="0.90"),
        _review_row(
            "reject-status",
            status="pt_pattern_candidate_partial",
            pearson="0.99",
            overlap="0.99",
        ),
        _review_row("reject-apex", apex_delta="15.0", pearson="0.99"),
    ]
    oracle_rows = [
        _oracle_row(
            review_row,
            oracle_call=oracle_call,
        )
        for review_row, oracle_call in zip(
            review_rows,
            ("accept", "accept", "reject", "reject", "reject"),
            strict=True,
        )
    ]

    cases = thresholds.build_calibration_cases(
        oracle_rows=oracle_rows,
        review_rows=review_rows,
        review_packet_sha256_by_row_id={
            row["packet_row_id"]: UNIT_REVIEW_SHA256 for row in review_rows
        },
    )
    threshold_rows = thresholds.evaluate_threshold_grid(
        cases=cases,
        calibration_id="unit_thresholds",
    )
    recommended = threshold_rows[0]
    assert recommended["rule_id"] == (
        "pt_fullpattern_status_pt_pattern_detected_diagnostic_positions_ge_3"
        "_apex_floor_le_0p6s_or_scan_multiplier_1p25_pearson_ge_0p9_overlap_ge_0p5"
        "_shiftedreviewonly_shiftedpearson_ge_0p85_shapeshift_le_45s"
    )
    assert recommended["min_shifted_pearson_r_to_base"] == 0.85
    assert recommended["max_abs_shape_shift_sec_to_base"] == 45.0
    assert recommended["true_positive"] == 2
    assert recommended["false_positive"] == 0
    assert recommended["false_negative"] == 0

    audit_rows = thresholds.build_case_audit_rows(
        cases=cases,
        recommended_rule=recommended,
        calibration_id="unit_thresholds",
    )
    assert [row["calibration_outcome"] for row in audit_rows].count(
        "true_positive"
    ) == 2
    assert [row["calibration_outcome"] for row in audit_rows].count(
        "true_negative"
    ) == 3

    assert thresholds._calibration_status(recommended) == (
        "threshold_candidate_ready_for_review"
    )


def test_threshold_calibration_rejects_missing_review_packet() -> None:
    review_row = _review_row("missing")
    with pytest.raises(ValueError, match="missing review packet"):
        thresholds.build_calibration_cases(
            oracle_rows=[_oracle_row(review_row, oracle_call="accept")],
            review_rows=[],
            review_packet_sha256_by_row_id={},
        )


def test_threshold_calibration_accepts_current_anchor_packet(
    tmp_path: Path,
) -> None:
    review_row = _review_row("anchor", anchor_local=True)
    review_csv = _write_review_bundle(
        tmp_path / "anchor" / "review.csv",
        [review_row],
    )
    oracle_csv = _write_verified_promotion(
        tmp_path / "oracle",
        review_csv=review_csv,
        oracle_call="reject",
    )

    outputs = thresholds.run_threshold_calibration(
        oracle_promotion_csv=oracle_csv,
        review_packet_csvs=[review_csv],
        output_dir=tmp_path / "thresholds",
        calibration_id="anchor",
    )

    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    assert summary["row_count"] == 1


def test_threshold_calibration_rejects_partial_anchor_packet(
    tmp_path: Path,
) -> None:
    review_row = _review_row("partial", anchor_local=True)
    partial_columns = tuple(
        column
        for column in packet.PACKET_COLUMNS
        if column != evidence.ANCHOR_SEGMENTATION_SUMMARY_COLUMNS[0]
    )
    review_csv = _write_csv(
        tmp_path / "review_partial.csv",
        [review_row],
        partial_columns,
    )
    valid_review = _write_review_bundle(
        tmp_path / "promotion_source" / "review.csv",
        [_review_row("promotion-source")],
    )
    oracle_csv = _write_verified_promotion(
        tmp_path / "oracle_partial",
        review_csv=valid_review,
        oracle_call="reject",
    )

    with pytest.raises(ValueError, match="current schema columns mismatch"):
        thresholds.run_threshold_calibration(
            oracle_promotion_csv=oracle_csv,
            review_packet_csvs=[review_csv],
            output_dir=tmp_path / "thresholds_partial",
            calibration_id="partial",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "unsupported_pt_pattern_oracle_promotion_packet"),
        ("readiness_tier", "production_ready"),
        ("authority_scope", "detector_activation"),
        ("oracle_status", "pending_human_review"),
        ("oracle_detection_status", "detected"),
        ("provenance_status", "review_packet_row_matched"),
        ("human_review_call", "reject"),
        ("oracle_packet_id", ""),
        ("review_packet_csv_sha256", "C" * 64),
        ("blind_overlay_summary_json_sha256", "not-a-sha256"),
        ("candidate_id", "different-candidate"),
        ("sample_stem", "DifferentSample"),
        ("base_mz", "501.0"),
        ("base_rt_apex_min", "11.0"),
        ("review_packet_schema_version", "unsupported_review_packet"),
        ("review_packet_readiness_tier", "production_ready"),
        ("review_packet_oracle_status", "resolved"),
        (
            "review_packet_combined_pt_pattern_status",
            "pt_pattern_candidate_partial",
        ),
    ],
)
def test_threshold_calibration_rejects_stale_or_invalid_oracle_binding(
    field: str,
    value: str,
) -> None:
    review_row = _review_row("binding")
    oracle_row = _oracle_row(review_row, oracle_call="accept")
    oracle_row[field] = value

    with pytest.raises(ValueError, match=field):
        thresholds.build_calibration_cases(
            oracle_rows=[oracle_row],
            review_rows=[review_row],
            review_packet_sha256_by_row_id={
                review_row["packet_row_id"]: UNIT_REVIEW_SHA256
            },
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("readiness_tier", "production_ready"),
        ("evidence_owner", "other.owner"),
        ("oracle_status", "resolved"),
    ],
)
def test_threshold_calibration_rejects_invalid_actual_review_status(
    field: str,
    value: str,
) -> None:
    review_row = _review_row("invalid-review")
    review_row[field] = value
    oracle_row = _oracle_row(review_row, oracle_call="accept")

    with pytest.raises(ValueError, match=field):
        thresholds.build_calibration_cases(
            oracle_rows=[oracle_row],
            review_rows=[review_row],
            review_packet_sha256_by_row_id={
                review_row["packet_row_id"]: UNIT_REVIEW_SHA256
            },
        )


def test_threshold_calibration_cli_writes_outputs(tmp_path: Path) -> None:
    review_row = _review_row("accept-a")
    review_csv = _write_review_bundle(
        tmp_path / "review.csv",
        [review_row],
    )
    oracle_csv = _write_verified_promotion(
        tmp_path / "oracle",
        review_csv=review_csv,
        oracle_call="reject",
    )

    from tools.diagnostics import pt_pattern_oracle_thresholds as cli

    assert (
        cli.main(
            [
                "--oracle-promotion-csv",
                str(oracle_csv),
                "--review-packet-csv",
                str(review_csv),
                "--output-dir",
                str(tmp_path / "thresholds"),
                "--calibration-id",
                "unit_thresholds",
            ]
        )
        == 0
    )

    summary = json.loads(
        (
            tmp_path / "thresholds" / "pt_pattern_oracle_threshold_summary.json"
        ).read_text(encoding="utf-8")
    )
    assert summary["row_count"] == 1
    assert summary["threshold_candidate_count"] > 0


def _write_csv(
    path: Path,
    rows: list[dict[str, str]],
    fieldnames: tuple[str, ...],
) -> Path:
    write_delimited_rows(path, rows, fieldnames)
    return path


def _write_review_bundle(
    path: Path,
    rows: list[dict[str, str]],
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_overlay_lineage_bundle(
        review_packet_csv=path,
        packet_rows=rows,
        trace_points_csv=path.parent / "pt_pattern_trace_points.csv",
        trace_point_rows=[],
    )
    return path


def _write_verified_promotion(
    output_dir: Path,
    *,
    review_csv: Path,
    oracle_call: str,
) -> Path:
    review_rows = read_delimited_rows(
        review_csv,
        required_columns=("packet_row_id",),
        delimiter=",",
    )
    outputs = write_verified_oracle_promotion_bundle(
        review_packet_csv=review_csv,
        trace_points_csv=review_csv.parent / "pt_pattern_trace_points.csv",
        oracle_call_by_packet_row_id={
            row["packet_row_id"]: oracle_call for row in review_rows
        },
        output_dir=output_dir,
    )
    return outputs["promotion_packet_csv"]
