from __future__ import annotations

import json
from pathlib import Path

import pytest

from xic_extractor.discovery import pt_pattern_blind_overlay as overlay
from xic_extractor.discovery import pt_pattern_oracle_promotion as promotion
from xic_extractor.discovery import pt_pattern_oracle_refresh as refresh
from xic_extractor.discovery import pt_pattern_review_packet as packet
from xic_extractor.discovery import pt_pattern_trace_evidence as evidence
from xic_extractor.tabular_io import read_delimited_rows, write_delimited_rows


def _review_packet_row(
    candidate_id: str,
    *,
    source_label: str = "pt_only",
) -> dict[str, str]:
    row = {column: "" for column in packet.PACKET_COLUMNS}
    row.update(
        {
            "schema_version": packet.SCHEMA_VERSION,
            "readiness_tier": "diagnostic_only",
            "evidence_owner": packet.EVIDENCE_OWNER,
            "packet_label": "unit_packet",
            "source_label": source_label,
            "packet_row_id": f"{source_label}|{candidate_id}",
            "sample_stem": "SampleA",
            "candidate_id": candidate_id,
            "base_mz": "500.0",
            "base_rt_apex_min": "10.0",
            "trace_decision_basis": evidence.TRACE_DECISION_BASIS,
            "trace_smoothing_method": evidence.TRACE_SMOOTH_METHOD,
            "trace_smoothing_window_points": str(
                evidence.TRACE_SMOOTH_WINDOW_POINTS
            ),
            "combined_pt_pattern_status": "pt_pattern_detected_diagnostic",
            "non_base_positions_supported": "3",
            "max_abs_apex_delta_sec": "2.0",
            "min_pearson_r_to_base": "0.95",
            "min_active_overlap_fraction": "0.90",
            "oracle_status": "pending_human_review",
        }
    )
    return row


def _adjudicated_row(
    candidate_id: str,
    *,
    source_label: str = "pt_only",
    adjudicated_call: str = "accept",
) -> dict[str, str]:
    return {
        "dataset": source_label,
        "case_index": "1",
        "adjudicated_call": adjudicated_call,
        "adjudication_basis": "unit_adjudication",
        "user_call": adjudicated_call,
        "machine_call": adjudicated_call,
        "note_optional": "unit note",
        "machine_call_reason": "unit_machine_reason",
        "base_mz": "500.0",
        "base_rt_apex_min": "10.0",
        "candidate_id": candidate_id,
        "packet_row_id": f"{source_label}|{candidate_id}",
    }


def _machine_call_row(
    candidate_id: str,
    *,
    source_label: str = "pt_only",
    machine_call: str = "accept",
) -> dict[str, str]:
    row = {column: "" for column in overlay.MACHINE_CALL_COLUMNS}
    row.update(
        {
            "schema_version": overlay.SCHEMA_VERSION,
            "readiness_tier": "diagnostic_only",
            "evidence_owner": packet.EVIDENCE_OWNER,
            "packet_label": "unit_packet",
            "source_label": source_label,
            "packet_row_id": f"{source_label}|{candidate_id}",
            "candidate_id": candidate_id,
            "machine_call": machine_call,
            "machine_call_reason": "unit_machine_reason",
            "trace_decision_basis": evidence.TRACE_DECISION_BASIS,
            "trace_smoothing_method": evidence.TRACE_SMOOTH_METHOD,
            "trace_smoothing_window_points": str(
                evidence.TRACE_SMOOTH_WINDOW_POINTS
            ),
            "combined_pt_pattern_status": "pt_pattern_detected_diagnostic",
            "non_base_positions_supported": "3",
            "max_abs_apex_delta_sec": "2.0",
            "min_pearson_r_to_base": "0.95",
            "min_active_overlap_fraction": "0.90",
            "oracle_status": "pending_human_review",
        }
    )
    return row


def test_oracle_refresh_builds_carryover_pending_and_stale_rows(
    tmp_path: Path,
) -> None:
    adjudicated_csv = tmp_path / "adjudicated.csv"
    review_packet_csv = tmp_path / "review_packet.csv"
    machine_calls_csv = tmp_path / "machine_calls.csv"
    write_delimited_rows(
        adjudicated_csv,
        [
            _adjudicated_row("carried"),
            _adjudicated_row("stale"),
        ],
        promotion.ADJUDICATED_REQUIRED_COLUMNS,
    )
    write_delimited_rows(
        review_packet_csv,
        [
            _review_packet_row("carried"),
            _review_packet_row("new"),
        ],
        packet.PACKET_COLUMNS,
    )
    write_delimited_rows(
        machine_calls_csv,
        [
            _machine_call_row("carried"),
            _machine_call_row("new", machine_call="reject"),
        ],
        overlay.MACHINE_CALL_COLUMNS,
    )

    outputs = refresh.run_oracle_refresh(
        adjudicated_oracle_csv=adjudicated_csv,
        review_packet_csvs=[review_packet_csv],
        machine_call_csvs=[machine_calls_csv],
        output_dir=tmp_path / "refresh",
        refresh_packet_id="unit_refresh",
    )

    draft_rows = read_delimited_rows(
        outputs["refresh_draft_csv"],
        required_columns=refresh.REFRESH_DRAFT_COLUMNS,
        delimiter=",",
    )
    assert [row["refresh_status"] for row in draft_rows] == [
        refresh.CARRYOVER_STATUS,
        refresh.PENDING_STATUS,
    ]
    assert draft_rows[0]["adjudicated_call"] == "accept"
    assert draft_rows[0]["adjudication_basis"] == (
        "v2_refresh_carryover_from_v1_manual_oracle"
    )
    assert draft_rows[1]["adjudicated_call"] == ""
    assert draft_rows[1]["machine_call"] == "reject"
    assert draft_rows[1]["machine_call_status"] == "current_machine_call_matched"

    pending_rows = read_delimited_rows(
        outputs["pending_review_csv"],
        required_columns=refresh.PENDING_REVIEW_COLUMNS,
        delimiter=",",
    )
    assert [row["candidate_id"] for row in pending_rows] == ["new"]
    header = outputs["pending_review_csv"].read_text(encoding="utf-8").splitlines()[0]
    assert "machine_call" not in header

    stale_rows = read_delimited_rows(
        outputs["stale_v1_rows_csv"],
        required_columns=refresh.STALE_ORACLE_COLUMNS,
        delimiter=",",
    )
    assert [row["candidate_id"] for row in stale_rows] == ["stale"]

    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    assert summary["current_review_row_count"] == 2
    assert summary["old_oracle_row_count"] == 2
    assert summary["carryover_label_count"] == 1
    assert summary["pending_human_review_count"] == 1
    assert summary["stale_old_oracle_row_count"] == 1
    assert summary["promotable_now"] is False
    assert summary["changes_matrix"] is False
    assert summary["changes_counted_detection"] is False


def test_oracle_refresh_marks_candidate_mismatch_as_conflict() -> None:
    current_row = _review_packet_row("current")
    old_row = _adjudicated_row("old")
    old_row["packet_row_id"] = current_row["packet_row_id"]

    result = refresh.build_oracle_refresh_rows(
        adjudicated_rows=[old_row],
        review_packet_rows=[current_row],
        machine_call_rows=[_machine_call_row("current")],
        refresh_packet_id="unit_refresh",
    )

    assert result["draft_rows"][0]["refresh_status"] == refresh.CONFLICT_STATUS
    assert result["draft_rows"][0]["adjudicated_call"] == ""
    assert result["pending_rows"][0]["candidate_id"] == "current"


def test_oracle_refresh_rejects_duplicate_current_packet_rows() -> None:
    current_row = _review_packet_row("dup")

    with pytest.raises(ValueError, match="duplicate review packet_row_id"):
        refresh.build_oracle_refresh_rows(
            adjudicated_rows=[],
            review_packet_rows=[current_row, current_row],
            machine_call_rows=[],
            refresh_packet_id="unit_refresh",
        )


def test_oracle_refresh_cli_writes_refresh_artifacts(tmp_path: Path) -> None:
    adjudicated_csv = tmp_path / "adjudicated.csv"
    review_packet_csv = tmp_path / "review_packet.csv"
    machine_calls_csv = tmp_path / "machine_calls.csv"
    write_delimited_rows(
        adjudicated_csv,
        [_adjudicated_row("carried")],
        promotion.ADJUDICATED_REQUIRED_COLUMNS,
    )
    write_delimited_rows(
        review_packet_csv,
        [_review_packet_row("carried")],
        packet.PACKET_COLUMNS,
    )
    write_delimited_rows(
        machine_calls_csv,
        [_machine_call_row("carried")],
        overlay.MACHINE_CALL_COLUMNS,
    )

    from tools.diagnostics import pt_pattern_oracle_refresh as cli

    assert cli.main(
        [
            "--adjudicated-oracle-csv",
            str(adjudicated_csv),
            "--review-packet-csv",
            str(review_packet_csv),
            "--machine-calls-csv",
            str(machine_calls_csv),
            "--output-dir",
            str(tmp_path / "refresh"),
            "--refresh-packet-id",
            "unit_refresh",
        ]
    ) == 0

    summary = json.loads(
        (tmp_path / "refresh" / "pt_pattern_oracle_refresh_summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert summary["promotable_now"] is True
    assert summary["carryover_label_count"] == 1
