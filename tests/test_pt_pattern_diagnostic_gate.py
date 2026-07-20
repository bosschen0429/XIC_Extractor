from __future__ import annotations

import json
from pathlib import Path

import pytest

from xic_extractor.discovery import pt_pattern_diagnostic_gate as gate
from xic_extractor.discovery import pt_pattern_oracle_promotion as promotion
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


def _trace_summary_row(
    candidate_id: str,
    *,
    status: str = "pt_pattern_detected_diagnostic",
    supported: str = "3",
    apex_delta: str = "0.5",
    pearson: str = "0.95",
    overlap: str = "0.80",
) -> dict[str, str]:
    row = {column: "" for column in evidence.SUMMARY_COLUMNS}
    row.update(
        {
            "schema_version": evidence.SCHEMA_VERSION,
            "sample_stem": "SampleA",
            "candidate_id": candidate_id,
            "scan_pattern_seed_status": "full_pattern_seeded",
            "base_mz": "500.0",
            "base_rt_apex_min": "10.0",
            "trace_decision_basis": evidence.TRACE_DECISION_BASIS,
            "trace_smoothing_method": evidence.TRACE_SMOOTH_METHOD,
            "trace_smoothing_window_points": str(
                evidence.TRACE_SMOOTH_WINDOW_POINTS
            ),
            "non_base_positions_supported": supported,
            "max_abs_apex_delta_sec": apex_delta,
            "min_pearson_r_to_base": pearson,
            "min_active_overlap_fraction": overlap,
            "trace_support_status": "supported",
            "combined_pt_pattern_status": status,
            "reason": "unit",
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


def test_diagnostic_gate_replays_zero_false_positive_rule(
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

    outputs = gate.run_diagnostic_gate(
        review_packet_csvs=[review_csv],
        oracle_promotion_csv=_write_csv(
            tmp_path / "oracle.csv",
            oracle_rows,
            promotion.PROMOTION_COLUMNS,
        ),
        output_dir=tmp_path / "gate",
        gate_id="unit_gate",
    )

    rows = read_delimited_rows(
        outputs["gate_rows_csv"],
        required_columns=gate.GATE_COLUMNS,
        delimiter=",",
    )
    calls = {row["candidate_id"]: row["diagnostic_gate_call"] for row in rows}
    reasons = {row["candidate_id"]: row["gate_reason"] for row in rows}
    assert calls == {
        "accept-a": "accept",
        "accept-b": "accept",
        "reject-shape": "reject",
        "reject-status": "reject",
        "reject-apex": "reject",
    }
    assert reasons["reject-shape"] == "pearson_below_threshold"
    assert reasons["reject-status"] == "combined_status_not_detected"
    assert reasons["reject-apex"] == "apex_delta_above_threshold"

    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    assert summary["readiness_tier"] == "diagnostic_only"
    assert summary["rule_id"] == (
        "pt_v2_fullpattern_apex_le_0p6s_or_1p25scan_pearson_ge_0p9_overlap_ge_0p5"
        "_shiftedreviewonly_shiftedpearson_ge_0p85_shapeshift_le_45s"
    )
    assert summary["max_abs_apex_delta_scan_step_multiplier"] == 1.25
    assert summary["row_count"] == 5
    assert summary["diagnostic_gate_call_counts"] == {"accept": 2, "reject": 3}
    assert summary["replay_outcome_counts"] == {
        "true_negative": 3,
        "true_positive": 2,
    }
    assert summary["replay_status"] == "diagnostic_gate_replay_passed"
    assert summary["changes_matrix"] is False
    assert summary["changes_counted_detection"] is False
    assert summary["changes_export"] is False


def test_diagnostic_gate_rejects_missing_oracle_review_packet() -> None:
    review_row = _review_row("missing")
    with pytest.raises(ValueError, match="missing review packet"):
        gate.build_diagnostic_gate_rows(
            review_rows=[],
            oracle_rows=[_oracle_row(review_row, oracle_call="accept")],
            review_packet_sha256_by_row_id={},
            gate_id="unit_gate",
        )


def test_diagnostic_gate_rejects_oracle_replay_over_synthetic_trace_row(
    tmp_path: Path,
) -> None:
    trace_summary_csv = _write_csv(
        tmp_path / "trace_summary.csv",
        [_trace_summary_row("synthetic-only")],
        evidence.SUMMARY_COLUMNS,
    )
    synthetic_identity = _review_row("synthetic-only")
    oracle_csv = _write_csv(
        tmp_path / "oracle.csv",
        [_oracle_row(synthetic_identity, oracle_call="accept")],
        promotion.PROMOTION_COLUMNS,
    )

    with pytest.raises(ValueError, match="requires an exact review packet"):
        gate.run_diagnostic_gate(
            review_packet_csvs=[],
            trace_summary_csvs=[
                gate.TraceSummaryInput(
                    source_label="pt_only",
                    path=trace_summary_csv,
                )
            ],
            oracle_promotion_csv=oracle_csv,
            output_dir=tmp_path / "gate",
            gate_id="synthetic_oracle",
        )


def test_diagnostic_gate_accepts_true_anchor_v3_packet(tmp_path: Path) -> None:
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

    outputs = gate.run_diagnostic_gate(
        review_packet_csvs=[review_csv],
        oracle_promotion_csv=oracle_csv,
        output_dir=tmp_path / "gate_v3",
        gate_id="anchor_v3",
    )

    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    assert summary["replay_status"] == "diagnostic_gate_replay_passed"


def test_diagnostic_gate_rejects_partial_anchor_v3_packet(tmp_path: Path) -> None:
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

    with pytest.raises(ValueError, match="v3 requires all anchor-local columns"):
        gate.run_diagnostic_gate(
            review_packet_csvs=[review_csv],
            output_dir=tmp_path / "gate_partial_v3",
            gate_id="partial_v3",
        )


def test_diagnostic_gate_rejects_stale_review_packet_hash(tmp_path: Path) -> None:
    review_row = _review_row("stale-hash")
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
                review_packet_sha256="C" * 64,
            )
        ],
        promotion.PROMOTION_COLUMNS,
    )

    with pytest.raises(ValueError, match="review_packet_csv_sha256"):
        gate.run_diagnostic_gate(
            review_packet_csvs=[review_csv],
            oracle_promotion_csv=oracle_csv,
            output_dir=tmp_path / "gate_stale_hash",
            gate_id="stale_hash",
        )


def test_diagnostic_gate_rejects_stale_review_identity_or_status(
    tmp_path: Path,
) -> None:
    review_row = _review_row("stale-status")
    review_csv = _write_csv(
        tmp_path / "review.csv",
        [review_row],
        packet.LEGACY_PACKET_COLUMNS,
    )
    oracle_row = _oracle_row(
        review_row,
        oracle_call="accept",
        review_packet_sha256=file_sha256(review_csv),
    )
    oracle_row["review_packet_combined_pt_pattern_status"] = (
        "pt_pattern_candidate_partial"
    )
    oracle_csv = _write_csv(
        tmp_path / "oracle.csv",
        [oracle_row],
        promotion.PROMOTION_COLUMNS,
    )

    with pytest.raises(
        ValueError,
        match="review_packet_combined_pt_pattern_status",
    ):
        gate.run_diagnostic_gate(
            review_packet_csvs=[review_csv],
            oracle_promotion_csv=oracle_csv,
            output_dir=tmp_path / "gate_stale_status",
            gate_id="stale_status",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [("base_mz", "501.0"), ("base_rt_apex_min", "11.0")],
)
def test_diagnostic_gate_rejects_stale_review_coordinates(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    review_row = _review_row("stale-coordinate")
    review_csv = _write_csv(
        tmp_path / "review.csv",
        [review_row],
        packet.LEGACY_PACKET_COLUMNS,
    )
    oracle_row = _oracle_row(
        review_row,
        oracle_call="accept",
        review_packet_sha256=file_sha256(review_csv),
    )
    oracle_row[field] = value
    oracle_csv = _write_csv(
        tmp_path / "oracle.csv",
        [oracle_row],
        promotion.PROMOTION_COLUMNS,
    )

    with pytest.raises(ValueError, match=field):
        gate.run_diagnostic_gate(
            review_packet_csvs=[review_csv],
            oracle_promotion_csv=oracle_csv,
            output_dir=tmp_path / "gate_stale_coordinate",
            gate_id="stale_coordinate",
        )


def test_diagnostic_gate_can_read_full_trace_summary_inputs(
    tmp_path: Path,
) -> None:
    trace_summary_csv = _write_csv(
        tmp_path / "trace_summary.csv",
        [
            _trace_summary_row("accept-a"),
            _trace_summary_row("reject-shape", pearson="0.84"),
        ],
        evidence.SUMMARY_COLUMNS,
    )

    outputs = gate.run_diagnostic_gate(
        review_packet_csvs=[],
        trace_summary_csvs=[
            gate.TraceSummaryInput(source_label="pt_only", path=trace_summary_csv)
        ],
        output_dir=tmp_path / "gate",
        gate_id="unit_trace_gate",
    )

    rows = read_delimited_rows(
        outputs["gate_rows_csv"],
        required_columns=gate.GATE_COLUMNS,
        delimiter=",",
    )
    assert [row["packet_row_id"] for row in rows] == [
        "pt_only|accept-a",
        "pt_only|reject-shape",
    ]
    assert [row["diagnostic_gate_call"] for row in rows] == [
        "accept",
        "reject",
    ]


def test_diagnostic_gate_uses_scan_aware_apex_threshold(
    tmp_path: Path,
) -> None:
    trace_summary_csv = _write_csv(
        tmp_path / "trace_summary.csv",
        [
            _trace_summary_row(
                "accept-one-scan",
                apex_delta="1.0",
                pearson="0.982299",
                overlap="0.952381",
            )
            | {
                "min_effective_smoothing_window_sec": "6.0",
                "min_median_rt_step_sec": "1.2",
            }
        ],
        evidence.SUMMARY_COLUMNS,
    )

    outputs = gate.run_diagnostic_gate(
        review_packet_csvs=[],
        trace_summary_csvs=[
            gate.TraceSummaryInput(source_label="pt_only", path=trace_summary_csv)
        ],
        output_dir=tmp_path / "gate",
        gate_id="unit_trace_gate",
    )

    rows = read_delimited_rows(
        outputs["gate_rows_csv"],
        required_columns=gate.GATE_COLUMNS,
        delimiter=",",
    )
    assert rows[0]["diagnostic_gate_call"] == "accept"
    assert rows[0]["max_abs_apex_delta_sec_threshold"] == "1.5"

    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    assert summary["row_count"] == 1
    assert summary["review_packet_csvs"] == []
    assert summary["trace_summary_csvs"] == [f"pt_only={trace_summary_csv}"]
    assert summary["replay_status"] == "no_oracle_replay"
    assert summary["changes_matrix"] is False


def test_diagnostic_gate_accepts_legacy_trace_summary_without_review_flags(
    tmp_path: Path,
) -> None:
    trace_summary_csv = _write_csv(
        tmp_path / "legacy_trace_summary.csv",
        [_trace_summary_row("accept-a")],
        gate.TRACE_SUMMARY_GATE_REQUIRED_COLUMNS,
    )

    outputs = gate.run_diagnostic_gate(
        review_packet_csvs=[],
        trace_summary_csvs=[
            gate.TraceSummaryInput(source_label="pt_only", path=trace_summary_csv)
        ],
        output_dir=tmp_path / "gate",
        gate_id="unit_legacy_trace_gate",
    )

    rows = read_delimited_rows(
        outputs["gate_rows_csv"],
        required_columns=gate.GATE_COLUMNS,
        delimiter=",",
    )
    assert rows[0]["packet_row_id"] == "pt_only|accept-a"
    assert rows[0]["diagnostic_gate_call"] == "accept"


def test_diagnostic_gate_cli_writes_outputs(tmp_path: Path) -> None:
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

    from tools.diagnostics import pt_pattern_diagnostic_gate as cli

    assert cli.main(
        [
            "--review-packet-csv",
            str(review_csv),
            "--oracle-promotion-csv",
            str(oracle_csv),
            "--output-dir",
            str(tmp_path / "gate"),
            "--gate-id",
            "unit_gate",
            "--require-zero-fpfn",
        ]
    ) == 0

    summary = json.loads(
        (
            tmp_path
            / "gate"
            / "pt_pattern_diagnostic_gate_summary.json"
        ).read_text(encoding="utf-8")
    )
    assert summary["replay_status"] == "diagnostic_gate_replay_passed"


def test_diagnostic_gate_cli_accepts_trace_summary_input(tmp_path: Path) -> None:
    trace_summary_csv = _write_csv(
        tmp_path / "trace_summary.csv",
        [_trace_summary_row("accept-a")],
        evidence.SUMMARY_COLUMNS,
    )

    from tools.diagnostics import pt_pattern_diagnostic_gate as cli

    assert cli.main(
        [
            "--trace-summary-input",
            f"pt_only={trace_summary_csv}",
            "--output-dir",
            str(tmp_path / "gate"),
            "--gate-id",
            "unit_trace_gate",
        ]
    ) == 0

    summary = json.loads(
        (
            tmp_path
            / "gate"
            / "pt_pattern_diagnostic_gate_summary.json"
        ).read_text(encoding="utf-8")
    )
    assert summary["diagnostic_gate_call_counts"] == {"accept": 1}


def test_diagnostic_gate_cli_can_fail_zero_fpfn_requirement(tmp_path: Path) -> None:
    review_row = _review_row("false-positive")
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
                oracle_call="reject",
                review_packet_sha256=file_sha256(review_csv),
            )
        ],
        promotion.PROMOTION_COLUMNS,
    )

    from tools.diagnostics import pt_pattern_diagnostic_gate as cli

    assert cli.main(
        [
            "--review-packet-csv",
            str(review_csv),
            "--oracle-promotion-csv",
            str(oracle_csv),
            "--output-dir",
            str(tmp_path / "gate"),
            "--gate-id",
            "unit_gate",
            "--require-zero-fpfn",
        ]
    ) == 3


def _write_csv(
    path: Path,
    rows: list[dict[str, str]],
    fieldnames: tuple[str, ...],
) -> Path:
    write_delimited_rows(path, rows, fieldnames)
    return path
