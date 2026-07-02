from __future__ import annotations

import json
from pathlib import Path

import pytest

from xic_extractor.discovery import pt_pattern_diagnostic_gate as gate
from xic_extractor.discovery import pt_pattern_oracle_promotion as promotion
from xic_extractor.discovery import pt_pattern_review_packet as packet
from xic_extractor.discovery import pt_pattern_trace_evidence as evidence
from xic_extractor.tabular_io import read_delimited_rows, write_delimited_rows


def _review_row(
    candidate_id: str,
    *,
    status: str = "pt_pattern_detected_diagnostic",
    supported: str = "3",
    apex_delta: str = "1.0",
    pearson: str = "0.95",
    overlap: str = "0.80",
) -> dict[str, str]:
    row = {column: "" for column in packet.PACKET_COLUMNS}
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
    return row


def _oracle_row(candidate_id: str, *, oracle_call: str) -> dict[str, str]:
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
            "oracle_detection_status": (
                "detected" if oracle_call == "accept" else "non_detected"
            ),
            "packet_row_id": f"pt_only|{candidate_id}",
            "dataset": "pt_only",
            "case_index": candidate_id,
            "sample_stem": "SampleA",
            "candidate_id": candidate_id,
            "base_mz": "500.0",
            "base_rt_apex_min": "10.0",
            "adjudication_basis": "unit",
            "human_review_call": oracle_call,
            "machine_call": oracle_call,
            "machine_call_reason": "unit",
            "review_packet_schema_version": packet.SCHEMA_VERSION,
            "review_packet_readiness_tier": packet.READINESS_TIER,
            "review_packet_oracle_status": "pending_human_review",
            "review_packet_combined_pt_pattern_status": (
                "pt_pattern_detected_diagnostic"
            ),
            "provenance_status": "review_packet_row_matched",
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
    oracle_rows = [
        _oracle_row("accept-a", oracle_call="accept"),
        _oracle_row("accept-b", oracle_call="accept"),
        _oracle_row("reject-shape", oracle_call="reject"),
        _oracle_row("reject-status", oracle_call="reject"),
        _oracle_row("reject-apex", oracle_call="reject"),
    ]

    outputs = gate.run_diagnostic_gate(
        review_packet_csvs=[
            _write_csv(tmp_path / "review.csv", review_rows, packet.PACKET_COLUMNS)
        ],
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
        "pt_v2_fullpattern_apex_le_10s_pearson_ge_0p9_overlap_ge_0p5"
    )
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
    with pytest.raises(ValueError, match="missing review packet"):
        gate.build_diagnostic_gate_rows(
            review_rows=[],
            oracle_rows=[_oracle_row("missing", oracle_call="accept")],
            gate_id="unit_gate",
        )


def test_diagnostic_gate_cli_writes_outputs(tmp_path: Path) -> None:
    review_csv = _write_csv(
        tmp_path / "review.csv",
        [_review_row("accept-a")],
        packet.PACKET_COLUMNS,
    )
    oracle_csv = _write_csv(
        tmp_path / "oracle.csv",
        [_oracle_row("accept-a", oracle_call="accept")],
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


def test_diagnostic_gate_cli_can_fail_zero_fpfn_requirement(tmp_path: Path) -> None:
    review_csv = _write_csv(
        tmp_path / "review.csv",
        [_review_row("false-positive")],
        packet.PACKET_COLUMNS,
    )
    oracle_csv = _write_csv(
        tmp_path / "oracle.csv",
        [_oracle_row("false-positive", oracle_call="reject")],
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
