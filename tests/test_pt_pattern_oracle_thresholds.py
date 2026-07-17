from __future__ import annotations

import json
from pathlib import Path

import pytest

from xic_extractor.discovery import pt_pattern_oracle_promotion as promotion
from xic_extractor.discovery import pt_pattern_oracle_thresholds as thresholds
from xic_extractor.discovery import pt_pattern_review_packet as packet
from xic_extractor.discovery import pt_pattern_trace_evidence as evidence
from xic_extractor.tabular_io import (
    file_sha256,
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
    columns = packet.PACKET_COLUMNS if anchor_local else packet.LEGACY_PACKET_COLUMNS
    row = {column: "" for column in columns}
    row.update(
        {
            "schema_version": (
                packet.ANCHOR_LOCAL_SCHEMA_VERSION
                if anchor_local
                else packet.SCHEMA_VERSION
            ),
            "readiness_tier": packet.READINESS_TIER,
            "evidence_owner": packet.EVIDENCE_OWNER,
            "packet_label": "unit_packet",
            "source_label": "pt_only",
            "packet_row_id": f"pt_only|{candidate_id}",
            "sample_stem": "SampleA",
            "candidate_id": candidate_id,
            "base_mz": "500.0",
            "base_rt_apex_min": "10.0",
            "trace_decision_basis": evidence.TRACE_DECISION_BASIS,
            "trace_smoothing_method": evidence.TRACE_SMOOTH_METHOD,
            "trace_smoothing_window_points": str(
                evidence.TRACE_SMOOTH_WINDOW_POINTS
            ),
            "combined_pt_pattern_status": status,
            "non_base_positions_supported": supported,
            "max_abs_apex_delta_sec": apex_delta,
            "min_pearson_r_to_base": pearson,
            "min_active_overlap_fraction": overlap,
            "oracle_status": "pending_human_review",
        }
    )
    if anchor_local:
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


def test_threshold_calibration_selects_zero_false_positive_rule(
    tmp_path: Path,
) -> None:
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
    review_csv = _write_csv(
        tmp_path / "review.csv",
        review_rows,
        packet.LEGACY_PACKET_COLUMNS,
    )
    review_sha256 = file_sha256(review_csv)
    oracle_rows = [
        _oracle_row(
            review_row,
            oracle_call=oracle_call,
            review_packet_sha256=review_sha256,
        )
        for review_row, oracle_call in zip(
            review_rows,
            ("accept", "accept", "reject", "reject", "reject"),
            strict=True,
        )
    ]

    outputs = thresholds.run_threshold_calibration(
        oracle_promotion_csv=_write_csv(
            tmp_path / "oracle.csv",
            oracle_rows,
            promotion.PROMOTION_COLUMNS,
        ),
        review_packet_csvs=[review_csv],
        output_dir=tmp_path / "thresholds",
        calibration_id="unit_thresholds",
    )

    threshold_rows = read_delimited_rows(
        outputs["threshold_candidates_csv"],
        required_columns=thresholds.THRESHOLD_COLUMNS,
        delimiter=",",
    )
    recommended = threshold_rows[0]
    assert recommended["rule_id"] == (
        "pt_v2_fullpattern_apex_le_0p6s_or_1p25scan_pearson_ge_0p9_overlap_ge_0p5"
        "_shiftedreviewonly_shiftedpearson_ge_0p85_shapeshift_le_45s"
    )
    assert recommended["min_shifted_pearson_r_to_base"] == "0.85"
    assert recommended["max_abs_shape_shift_sec_to_base"] == "45"
    assert recommended["true_positive"] == "2"
    assert recommended["false_positive"] == "0"
    assert recommended["false_negative"] == "0"

    audit_rows = read_delimited_rows(
        outputs["case_audit_csv"],
        required_columns=thresholds.CASE_AUDIT_COLUMNS,
        delimiter=",",
    )
    assert [row["calibration_outcome"] for row in audit_rows].count(
        "true_positive"
    ) == 2
    assert [row["calibration_outcome"] for row in audit_rows].count(
        "true_negative"
    ) == 3

    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    assert summary["readiness_tier"] == "diagnostic_only"
    assert summary["calibration_status"] == "threshold_candidate_ready_for_review"
    assert summary["recommended_rule"]["false_positive"] == 0
    assert summary["recommended_rule"]["max_abs_shape_shift_sec_to_base"] == 45.0
    assert summary["changes_matrix"] is False
    assert summary["changes_counted_detection"] is False


def test_threshold_calibration_rejects_missing_review_packet() -> None:
    review_row = _review_row("missing")
    with pytest.raises(ValueError, match="missing review packet"):
        thresholds.build_calibration_cases(
            oracle_rows=[_oracle_row(review_row, oracle_call="accept")],
            review_rows=[],
            review_packet_sha256_by_row_id={},
        )


def test_threshold_calibration_accepts_true_anchor_v3_packet(
    tmp_path: Path,
) -> None:
    review_row = _review_row("anchor-v3", anchor_local=True)
    review_csv = _write_csv(
        tmp_path / "review_v3.csv",
        [review_row],
        packet.PACKET_COLUMNS,
    )
    oracle_csv = _write_csv(
        tmp_path / "oracle_v3.csv",
        [
            _oracle_row(
                review_row,
                oracle_call="accept",
                review_packet_sha256=file_sha256(review_csv),
            )
        ],
        promotion.PROMOTION_COLUMNS,
    )

    outputs = thresholds.run_threshold_calibration(
        oracle_promotion_csv=oracle_csv,
        review_packet_csvs=[review_csv],
        output_dir=tmp_path / "thresholds_v3",
        calibration_id="anchor_v3",
    )

    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    assert summary["row_count"] == 1


def test_threshold_calibration_rejects_partial_anchor_v3_packet(
    tmp_path: Path,
) -> None:
    review_row = _review_row("partial-v3", anchor_local=True)
    partial_columns = (
        *packet.LEGACY_PACKET_COLUMNS,
        evidence.ANCHOR_SEGMENTATION_SUMMARY_COLUMNS[0],
    )
    review_csv = _write_csv(
        tmp_path / "review_partial_v3.csv",
        [review_row],
        partial_columns,
    )
    oracle_csv = _write_csv(
        tmp_path / "oracle_partial_v3.csv",
        [
            _oracle_row(
                review_row,
                oracle_call="accept",
                review_packet_sha256=file_sha256(review_csv),
            )
        ],
        promotion.PROMOTION_COLUMNS,
    )

    with pytest.raises(ValueError, match="v3 requires all anchor-local columns"):
        thresholds.run_threshold_calibration(
            oracle_promotion_csv=oracle_csv,
            review_packet_csvs=[review_csv],
            output_dir=tmp_path / "thresholds_partial_v3",
            calibration_id="partial_v3",
        )


def test_threshold_calibration_rejects_mixed_packet_schemas(
    tmp_path: Path,
) -> None:
    legacy_row = _review_row("legacy-v2")
    anchor_row = _review_row("anchor-v3", anchor_local=True)
    legacy_csv = _write_csv(
        tmp_path / "review_v2.csv",
        [legacy_row],
        packet.LEGACY_PACKET_COLUMNS,
    )
    anchor_csv = _write_csv(
        tmp_path / "review_v3.csv",
        [anchor_row],
        packet.PACKET_COLUMNS,
    )
    oracle_csv = _write_csv(
        tmp_path / "oracle.csv",
        [
            _oracle_row(
                legacy_row,
                oracle_call="accept",
                review_packet_sha256=file_sha256(legacy_csv),
            )
        ],
        promotion.PROMOTION_COLUMNS,
    )

    with pytest.raises(ValueError, match="must use one schema_version"):
        thresholds.run_threshold_calibration(
            oracle_promotion_csv=oracle_csv,
            review_packet_csvs=[legacy_csv, anchor_csv],
            output_dir=tmp_path / "thresholds_mixed",
            calibration_id="mixed",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "pt_pattern_oracle_promotion_packet.v1"),
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
        ("review_packet_schema_version", packet.ANCHOR_LOCAL_SCHEMA_VERSION),
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
    review_csv = _write_csv(
        tmp_path / "review.csv",
        [review_row],
        packet.LEGACY_PACKET_COLUMNS,
    )
    oracle_csv = _write_csv(
        tmp_path / "oracle.csv",
        [
            _oracle_row(
                review_row,
                oracle_call="accept",
                review_packet_sha256=file_sha256(review_csv),
            )
        ],
        promotion.PROMOTION_COLUMNS,
    )

    from tools.diagnostics import pt_pattern_oracle_thresholds as cli

    assert cli.main(
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
    ) == 0

    summary = json.loads(
        (
            tmp_path
            / "thresholds"
            / "pt_pattern_oracle_threshold_summary.json"
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
