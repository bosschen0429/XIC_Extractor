from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.pt_pattern_trace_fixtures import write_overlay_lineage_bundle
from xic_extractor.discovery import pt_pattern_blind_overlay as overlay
from xic_extractor.discovery import pt_pattern_oracle_promotion as promotion
from xic_extractor.discovery import pt_pattern_oracle_refresh as refresh
from xic_extractor.discovery import pt_pattern_review_packet as packet
from xic_extractor.discovery import pt_pattern_trace_evidence as evidence
from xic_extractor.tabular_io import (
    file_sha256,
    read_delimited_rows,
    write_delimited_rows,
)


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
            "candidate_assignment_sha256": "a" * 64,
            "trace_extraction_sha256": "b" * 64,
            "trace_artifact_role": "anchor_local",
            "base_mz": "500.0",
            "base_rt_apex_min": "10.0",
            "trace_decision_basis": evidence.TRACE_DECISION_BASIS,
            "trace_smoothing_method": evidence.TRACE_SMOOTH_METHOD,
            "trace_smoothing_window_points": str(evidence.TRACE_SMOOTH_WINDOW_POINTS),
            "combined_pt_pattern_status": "pt_pattern_detected_diagnostic",
            "non_base_positions_supported": "3",
            "max_abs_apex_delta_sec": "2.0",
            "min_pearson_r_to_base": "0.95",
            "min_active_overlap_fraction": "0.90",
            "trace_ambiguity_status": "clear",
            "anchor_segmentation_status": "clear",
            "anchor_segmentation_boundary_review_status": "clear",
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
            "sample_stem": "SampleA",
            "candidate_id": candidate_id,
            "candidate_assignment_sha256": "a" * 64,
            "trace_extraction_sha256": "b" * 64,
            "trace_artifact_role": "anchor_local",
            "machine_call": machine_call,
            "machine_call_reason": "unit_machine_reason",
            "trace_decision_basis": evidence.TRACE_DECISION_BASIS,
            "trace_smoothing_method": evidence.TRACE_SMOOTH_METHOD,
            "trace_smoothing_window_points": str(evidence.TRACE_SMOOTH_WINDOW_POINTS),
            "combined_pt_pattern_status": "pt_pattern_detected_diagnostic",
            "non_base_positions_supported": "3",
            "max_abs_apex_delta_sec": "2.0",
            "min_pearson_r_to_base": "0.95",
            "min_active_overlap_fraction": "0.90",
            "trace_ambiguity_status": "clear",
            "anchor_segmentation_status": "clear",
            "anchor_segmentation_boundary_review_status": "clear",
            "oracle_status": "pending_human_review",
        }
    )
    return row


def _anchor_review_packet_row(candidate_id: str) -> dict[str, str]:
    row = {column: "" for column in packet.PACKET_COLUMNS}
    row.update(_review_packet_row(candidate_id))
    row.update(
        {
            "schema_version": packet.SCHEMA_VERSION,
            "trace_artifact_role": "anchor_local",
            "trace_ambiguity_status": "clear",
            "anchor_segmentation_status": "clear",
            "anchor_segmentation_boundary_review_status": "clear",
        }
    )
    return row


def _anchor_machine_call_row(candidate_id: str) -> dict[str, str]:
    row = {column: "" for column in overlay.MACHINE_CALL_COLUMNS}
    row.update(_machine_call_row(candidate_id))
    row.update(
        {
            "schema_version": overlay.SCHEMA_VERSION,
            "trace_artifact_role": "anchor_local",
            "trace_ambiguity_status": "clear",
            "anchor_segmentation_status": "clear",
            "anchor_segmentation_boundary_review_status": "clear",
        }
    )
    return row


def _write_packet_bundle(
    review_packet_csv: Path,
    review_rows: list[dict[str, str]],
) -> Path:
    trace_points_csv = review_packet_csv.parent / "trace_points.csv"
    write_overlay_lineage_bundle(
        review_packet_csv=review_packet_csv,
        packet_rows=review_rows,
        trace_points_csv=trace_points_csv,
        trace_point_rows=[
            {
                "candidate_id": row["candidate_id"],
                "position_label": position_label,
                "rt_min": "10.0",
                "intensity": "100.0",
            }
            for row in review_rows
            for position_label in evidence.POSITION_LABELS
        ],
    )
    outputs = overlay.run_blind_overlay(
        review_packet_csv=review_packet_csv,
        trace_points_csv=trace_points_csv,
        output_dir=review_packet_csv.parent / "overlay",
        title="Oracle refresh fixture",
    )
    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    return Path(summary["outputs"]["machine_calls_csv"])


def test_oracle_refresh_keeps_unproven_old_labels_pending_and_marks_stale_rows(
    tmp_path: Path,
) -> None:
    adjudicated_csv = tmp_path / "adjudicated.csv"
    review_packet_csv = tmp_path / "review_packet.csv"
    write_delimited_rows(
        adjudicated_csv,
        [
            _adjudicated_row("carried"),
            _adjudicated_row("stale"),
        ],
        promotion.LEGACY_ADJUDICATED_REQUIRED_COLUMNS,
    )
    review_rows = [
        _review_packet_row("carried"),
        _review_packet_row("new"),
    ]
    review_rows[1]["non_base_positions_supported"] = "1"
    machine_calls_csv = _write_packet_bundle(review_packet_csv, review_rows)

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
        refresh.PENDING_STATUS,
        refresh.PENDING_STATUS,
    ]
    assert draft_rows[0]["adjudicated_call"] == ""
    assert draft_rows[0]["user_call"] == ""
    assert draft_rows[0]["source_adjudicated_call"] == "accept"
    assert draft_rows[0]["review_packet_csv_sha256"] == file_sha256(
        review_packet_csv
    )
    assert draft_rows[0]["blind_overlay_summary_json_sha256"] == file_sha256(
        machine_calls_csv.parent / "pt_pattern_blind_overlay_summary.json"
    )
    assert draft_rows[0]["provenance_status"] == (
        "old_oracle_exact_artifact_identity_unproven"
    )
    assert draft_rows[1]["adjudicated_call"] == ""
    assert draft_rows[1]["machine_call"] == "uncertain"
    assert draft_rows[1]["machine_call_status"] == "current_machine_call_matched"

    pending_rows = read_delimited_rows(
        outputs["pending_review_csv"],
        required_columns=refresh.PENDING_REVIEW_COLUMNS,
        delimiter=",",
    )
    assert [row["candidate_id"] for row in pending_rows] == ["carried", "new"]
    header = outputs["pending_review_csv"].read_text(encoding="utf-8").splitlines()[0]
    assert "machine_call" not in header

    stale_rows = read_delimited_rows(
        outputs["stale_rows_csv"],
        required_columns=refresh.STALE_ORACLE_COLUMNS,
        delimiter=",",
    )
    assert [row["candidate_id"] for row in stale_rows] == ["stale"]

    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    assert summary["current_review_row_count"] == 2
    assert summary["old_oracle_row_count"] == 2
    assert summary["carryover_label_count"] == 0
    assert summary["pending_human_review_count"] == 2
    assert summary["stale_old_oracle_row_count"] == 1
    assert summary["promotable_now"] is False
    assert summary["changes_matrix"] is False
    assert summary["changes_counted_detection"] is False


def test_oracle_refresh_draft_can_be_human_completed_and_promoted(
    tmp_path: Path,
) -> None:
    adjudicated_csv = tmp_path / "adjudicated.csv"
    review_packet_csv = tmp_path / "review_packet.csv"
    write_delimited_rows(
        adjudicated_csv,
        [],
        promotion.LEGACY_ADJUDICATED_REQUIRED_COLUMNS,
    )
    machine_calls_csv = _write_packet_bundle(
        review_packet_csv,
        [_review_packet_row("new")],
    )
    refresh_outputs = refresh.run_oracle_refresh(
        adjudicated_oracle_csv=adjudicated_csv,
        review_packet_csvs=[review_packet_csv],
        machine_call_csvs=[machine_calls_csv],
        output_dir=tmp_path / "refresh",
        refresh_packet_id="unit_refresh",
    )
    draft_rows = read_delimited_rows(
        refresh_outputs["refresh_draft_csv"],
        required_columns=refresh.REFRESH_DRAFT_COLUMNS,
        exact_columns=refresh.REFRESH_DRAFT_COLUMNS,
        delimiter=",",
    )
    draft_rows[0]["adjudicated_call"] = "reject"
    draft_rows[0]["user_call"] = "reject"
    draft_rows[0]["adjudication_basis"] = "fresh_human_review"
    write_delimited_rows(
        refresh_outputs["refresh_draft_csv"],
        draft_rows,
        refresh.REFRESH_DRAFT_COLUMNS,
    )

    promotion_outputs = promotion.run_oracle_promotion(
        adjudicated_oracle_csv=refresh_outputs["refresh_draft_csv"],
        review_packet_csvs=[review_packet_csv],
        blind_overlay_summary_jsons=[
            machine_calls_csv.parent / "pt_pattern_blind_overlay_summary.json"
        ],
        output_dir=tmp_path / "promotion",
        oracle_packet_id="unit_oracle",
    )

    verified = promotion.load_verified_oracle_promotion_bundle(
        promotion_outputs["promotion_packet_csv"]
    )
    assert [row["oracle_call"] for row in verified.rows] == ["reject"]


def test_oracle_refresh_rejects_completed_draft_as_same_output_input(
    tmp_path: Path,
) -> None:
    adjudicated_csv = tmp_path / "adjudicated.csv"
    review_packet_csv = tmp_path / "review_packet.csv"
    write_delimited_rows(
        adjudicated_csv,
        [],
        promotion.LEGACY_ADJUDICATED_REQUIRED_COLUMNS,
    )
    machine_calls_csv = _write_packet_bundle(
        review_packet_csv,
        [_review_packet_row("new")],
    )
    output_dir = tmp_path / "refresh"
    outputs = refresh.run_oracle_refresh(
        adjudicated_oracle_csv=adjudicated_csv,
        review_packet_csvs=[review_packet_csv],
        machine_call_csvs=[machine_calls_csv],
        output_dir=output_dir,
        refresh_packet_id="unit_refresh",
    )
    draft_rows = read_delimited_rows(
        outputs["refresh_draft_csv"],
        required_columns=refresh.REFRESH_DRAFT_COLUMNS,
        exact_columns=refresh.REFRESH_DRAFT_COLUMNS,
        delimiter=",",
    )
    draft_rows[0]["adjudicated_call"] = "reject"
    draft_rows[0]["user_call"] = "reject"
    draft_rows[0]["adjudication_basis"] = "fresh_human_review"
    write_delimited_rows(
        outputs["refresh_draft_csv"],
        draft_rows,
        refresh.REFRESH_DRAFT_COLUMNS,
    )
    completed_bytes = outputs["refresh_draft_csv"].read_bytes()

    with pytest.raises(ValueError, match="input/output paths must be disjoint"):
        refresh.run_oracle_refresh(
            adjudicated_oracle_csv=outputs["refresh_draft_csv"],
            review_packet_csvs=[review_packet_csv],
            machine_call_csvs=[machine_calls_csv],
            output_dir=output_dir,
            refresh_packet_id="unit_refresh_rerun",
        )

    assert outputs["refresh_draft_csv"].read_bytes() == completed_bytes


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


@pytest.mark.parametrize("field", ("candidate_id", "packet_label", "source_label"))
def test_oracle_refresh_marks_machine_identity_mismatch_as_provenance_conflict(
    field: str,
) -> None:
    current_row = _review_packet_row("current")
    machine_row = _machine_call_row("current")
    machine_row[field] = f"wrong-{field}"

    result = refresh.build_oracle_refresh_rows(
        adjudicated_rows=[],
        review_packet_rows=[current_row],
        machine_call_rows=[machine_row],
        refresh_packet_id="unit_refresh",
    )

    draft_row = result["draft_rows"][0]
    assert draft_row["refresh_status"] == refresh.CONFLICT_STATUS
    assert draft_row["provenance_status"] == ("current_machine_call_identity_mismatch")
    assert draft_row["machine_call_status"] == (
        "current_machine_call_provenance_conflict"
    )
    assert draft_row["machine_call_status"] != "current_machine_call_matched"
    assert draft_row["machine_call"] == ""


@pytest.mark.parametrize(
    "field",
    ("candidate_assignment_sha256", "trace_extraction_sha256"),
)
def test_oracle_refresh_marks_stale_machine_lineage_as_conflict(field: str) -> None:
    current_row = _review_packet_row("current")
    machine_row = _machine_call_row("current")
    machine_row[field] = "c" * 64

    result = refresh.build_oracle_refresh_rows(
        adjudicated_rows=[],
        review_packet_rows=[current_row],
        machine_call_rows=[machine_row],
        refresh_packet_id="unit_refresh",
    )

    assert result["draft_rows"][0]["refresh_status"] == refresh.CONFLICT_STATUS
    assert result["draft_rows"][0]["machine_call"] == ""


def test_oracle_refresh_rejects_extra_machine_rows() -> None:
    with pytest.raises(ValueError, match="outside review packet"):
        refresh.build_oracle_refresh_rows(
            adjudicated_rows=[],
            review_packet_rows=[_review_packet_row("current")],
            machine_call_rows=[
                _machine_call_row("current"),
                _machine_call_row("extra"),
            ],
            refresh_packet_id="unit_refresh",
        )


def test_oracle_refresh_rejects_machine_csv_not_bound_by_sibling_summary(
    tmp_path: Path,
) -> None:
    adjudicated_csv = tmp_path / "adjudicated.csv"
    write_delimited_rows(
        adjudicated_csv,
        [],
        promotion.LEGACY_ADJUDICATED_REQUIRED_COLUMNS,
    )
    review_rows = [_review_packet_row("current")]
    review_packet_csv = tmp_path / "review_packet.csv"
    sealed_machine_csv = _write_packet_bundle(review_packet_csv, review_rows)
    copied_machine_csv = sealed_machine_csv.parent / "copied_machine.csv"
    copied_machine_rows = read_delimited_rows(
        sealed_machine_csv,
        required_columns=overlay.MACHINE_CALL_COLUMNS,
        delimiter=",",
    )
    write_delimited_rows(
        copied_machine_csv,
        copied_machine_rows,
        overlay.MACHINE_CALL_COLUMNS,
    )

    with pytest.raises(ValueError, match="binds a different machine-call"):
        refresh.run_oracle_refresh(
            adjudicated_oracle_csv=adjudicated_csv,
            review_packet_csvs=[review_packet_csv],
            machine_call_csvs=[copied_machine_csv],
            output_dir=tmp_path / "refresh",
            refresh_packet_id="unsealed_machine",
        )


def test_oracle_refresh_rejects_machine_call_bound_to_different_packet_artifact(
    tmp_path: Path,
) -> None:
    adjudicated_csv = tmp_path / "adjudicated.csv"
    write_delimited_rows(
        adjudicated_csv,
        [],
        promotion.LEGACY_ADJUDICATED_REQUIRED_COLUMNS,
    )
    packet_a = tmp_path / "packet_a" / "review_packet.csv"
    packet_a.parent.mkdir()
    machine_a = _write_packet_bundle(
        packet_a,
        [_review_packet_row("same-logical-row")],
    )
    packet_b = tmp_path / "packet_b" / "review_packet.csv"
    packet_b.parent.mkdir()
    changed_row = _review_packet_row("same-logical-row")
    changed_row["base_mz"] = "501.0"
    _write_packet_bundle(packet_b, [changed_row])

    outputs = refresh.run_oracle_refresh(
        adjudicated_oracle_csv=adjudicated_csv,
        review_packet_csvs=[packet_b],
        machine_call_csvs=[machine_a],
        output_dir=tmp_path / "refresh",
        refresh_packet_id="packet_swap",
    )

    rows = read_delimited_rows(
        outputs["refresh_draft_csv"],
        required_columns=refresh.REFRESH_DRAFT_COLUMNS,
        delimiter=",",
    )
    assert rows[0]["refresh_status"] == refresh.CONFLICT_STATUS
    assert rows[0]["provenance_status"] == (
        "current_machine_call_packet_sha256_mismatch"
    )
    assert rows[0]["machine_call_status"] == (
        "current_machine_call_provenance_conflict"
    )
    assert rows[0]["machine_call"] == ""


def test_oracle_refresh_reads_current_anchor_packet_and_machine_schema(
    tmp_path: Path,
) -> None:
    adjudicated_csv = tmp_path / "legacy_adjudicated.csv"
    review_packet_csv = tmp_path / "anchor_packet.csv"
    write_delimited_rows(
        adjudicated_csv,
        [],
        promotion.LEGACY_ADJUDICATED_REQUIRED_COLUMNS,
    )
    review_rows = [_anchor_review_packet_row("anchor")]
    machine_calls_csv = _write_packet_bundle(review_packet_csv, review_rows)

    outputs = refresh.run_oracle_refresh(
        adjudicated_oracle_csv=adjudicated_csv,
        review_packet_csvs=[review_packet_csv],
        machine_call_csvs=[machine_calls_csv],
        output_dir=tmp_path / "refresh",
        refresh_packet_id="anchor_refresh",
    )

    rows = read_delimited_rows(
        outputs["refresh_draft_csv"],
        required_columns=refresh.REFRESH_DRAFT_COLUMNS,
        delimiter=",",
    )
    assert rows[0]["candidate_id"] == "anchor"
    assert rows[0]["machine_call_status"] == "current_machine_call_matched"


def test_oracle_refresh_rejects_partial_anchor_machine_schema(tmp_path: Path) -> None:
    adjudicated_csv = tmp_path / "legacy_adjudicated.csv"
    review_packet_csv = tmp_path / "anchor_packet.csv"
    write_delimited_rows(
        adjudicated_csv,
        [],
        promotion.LEGACY_ADJUDICATED_REQUIRED_COLUMNS,
    )
    review_rows = [_anchor_review_packet_row("anchor")]
    machine_calls_csv = _write_packet_bundle(review_packet_csv, review_rows)
    missing_column = "anchor_segmentation_status"
    machine_columns = tuple(
        column for column in overlay.MACHINE_CALL_COLUMNS if column != missing_column
    )
    machine_rows = read_delimited_rows(
        machine_calls_csv,
        required_columns=overlay.MACHINE_CALL_COLUMNS,
        delimiter=",",
    )
    write_delimited_rows(machine_calls_csv, machine_rows, machine_columns)
    summary_json = machine_calls_csv.parent / "pt_pattern_blind_overlay_summary.json"
    summary = json.loads(summary_json.read_text(encoding="utf-8"))
    summary["outputs"]["machine_calls_csv_sha256"] = file_sha256(machine_calls_csv)
    summary_json.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="missing required columns|exact schema|current schema columns",
    ):
        refresh.run_oracle_refresh(
            adjudicated_oracle_csv=adjudicated_csv,
            review_packet_csvs=[review_packet_csv],
            machine_call_csvs=[machine_calls_csv],
            output_dir=tmp_path / "refresh",
            refresh_packet_id="anchor_refresh",
        )


def test_oracle_refresh_rejects_duplicate_current_packet_rows() -> None:
    current_row = _review_packet_row("dup")

    with pytest.raises(ValueError, match="duplicate review packet.*packet_row_id"):
        refresh.build_oracle_refresh_rows(
            adjudicated_rows=[],
            review_packet_rows=[current_row, current_row],
            machine_call_rows=[],
            refresh_packet_id="unit_refresh",
        )


def test_oracle_refresh_cli_writes_refresh_artifacts(tmp_path: Path) -> None:
    adjudicated_csv = tmp_path / "adjudicated.csv"
    review_packet_csv = tmp_path / "review_packet.csv"
    write_delimited_rows(
        adjudicated_csv,
        [_adjudicated_row("carried")],
        promotion.LEGACY_ADJUDICATED_REQUIRED_COLUMNS,
    )
    review_rows = [_review_packet_row("carried")]
    machine_calls_csv = _write_packet_bundle(review_packet_csv, review_rows)

    from tools.diagnostics import pt_pattern_oracle_refresh as cli

    assert (
        cli.main(
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
        )
        == 0
    )

    summary = json.loads(
        (tmp_path / "refresh" / "pt_pattern_oracle_refresh_summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert summary["promotable_now"] is False
    assert summary["carryover_label_count"] == 0
    assert summary["pending_human_review_count"] == 1
