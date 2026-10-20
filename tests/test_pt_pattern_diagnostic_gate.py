from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tests.pt_pattern_trace_fixtures import (
    write_overlay_lineage_bundle,
    write_trace_summary_lineage_bundle,
    write_verified_oracle_promotion_bundle,
)
from xic_extractor.discovery import pt_pattern_diagnostic_gate as gate
from xic_extractor.discovery import pt_pattern_oracle_promotion as promotion
from xic_extractor.discovery import pt_pattern_review_packet as packet
from xic_extractor.discovery import pt_pattern_trace_evidence as evidence
from xic_extractor.discovery.pt_pattern_artifact_contract import (
    PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE,
)
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
                f"gate-test-assignment|{candidate_id}".encode("utf-8")
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
            "trace_smoothing_window_points": str(evidence.TRACE_SMOOTH_WINDOW_POINTS),
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


def test_diagnostic_gate_replays_zero_false_positive_rule() -> None:
    review_rows = [
        _review_row("accept-a", pearson="0.92", overlap="0.75"),
        _review_row("accept-b", pearson="0.97", overlap="0.90"),
        _review_row("reject-shape", pearson="0.84", overlap="0.90"),
        _review_row(
            "reject-status",
            status="pt_pattern_candidate_partial",
            supported="2",
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

    rows = gate.build_diagnostic_gate_rows(
        review_rows=review_rows,
        oracle_rows=oracle_rows,
        review_packet_sha256_by_row_id={
            row["packet_row_id"]: UNIT_REVIEW_SHA256 for row in review_rows
        },
        gate_id="unit_gate",
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

    assert rows[0]["rule_id"] == (
        "pt_fullpattern_status_pt_pattern_detected_diagnostic_positions_ge_3"
        "_apex_floor_le_0p6s_or_scan_multiplier_1p25_pearson_ge_0p9_overlap_ge_0p5"
        "_shiftedreviewonly_shiftedpearson_ge_0p85_shapeshift_le_45s"
    )
    assert [row["replay_outcome"] for row in rows].count("true_positive") == 2
    assert [row["replay_outcome"] for row in rows].count("true_negative") == 3


def test_diagnostic_gate_rejects_missing_oracle_review_packet() -> None:
    review_row = _review_row("missing")
    with pytest.raises(ValueError, match="missing review packet"):
        gate.build_diagnostic_gate_rows(
            review_rows=[],
            oracle_rows=[_oracle_row(review_row, oracle_call="accept")],
            review_packet_sha256_by_row_id={},
            gate_id="unit_gate",
        )


def test_diagnostic_gate_sorts_valid_zero_rt_before_later_rt() -> None:
    zero_rt = _review_row("zero-rt")
    zero_rt["base_rt_apex_min"] = "0.0"
    later_rt = _review_row("later-rt")
    later_rt["base_rt_apex_min"] = "1.0"

    rows = gate.build_diagnostic_gate_rows(
        review_rows=[later_rt, zero_rt],
        gate_id="zero_rt_order",
    )

    assert [row["candidate_id"] for row in rows] == ["zero-rt", "later-rt"]


def test_diagnostic_gate_rejects_oracle_replay_over_synthetic_trace_row(
    tmp_path: Path,
) -> None:
    trace_summary_csv = _write_trace_summary_bundle(
        tmp_path / "trace_summary.csv",
        [_trace_summary_row("synthetic-only")],
    )
    _promotion_review, oracle_csv = _write_verified_review_and_promotion(
        tmp_path / "promotion_source",
        candidate_id="synthetic-only",
        oracle_call="reject",
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


def test_diagnostic_gate_accepts_current_anchor_packet(tmp_path: Path) -> None:
    review_csv, oracle_csv = _write_verified_review_and_promotion(
        tmp_path / "anchor",
        candidate_id="anchor",
        oracle_call="reject",
        anchor_local=True,
    )

    outputs = gate.run_diagnostic_gate(
        review_packet_csvs=[review_csv],
        oracle_promotion_csv=oracle_csv,
        output_dir=tmp_path / "gate",
        gate_id="anchor",
    )

    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    assert summary["replay_status"] == "diagnostic_gate_replay_passed"


def test_diagnostic_gate_rejects_partial_anchor_packet(tmp_path: Path) -> None:
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

    with pytest.raises(ValueError, match="current schema columns mismatch"):
        gate.run_diagnostic_gate(
            review_packet_csvs=[review_csv],
            output_dir=tmp_path / "gate_partial",
            gate_id="partial",
        )


def test_diagnostic_gate_rejects_stale_review_packet_hash(tmp_path: Path) -> None:
    review_row = _review_row("stale-hash")
    oracle_row = _oracle_row(
        review_row,
        oracle_call="accept",
        review_packet_sha256="C" * 64,
    )

    with pytest.raises(ValueError, match="review_packet_csv_sha256"):
        gate.build_diagnostic_gate_rows(
            review_rows=[review_row],
            oracle_rows=[oracle_row],
            review_packet_sha256_by_row_id={
                review_row["packet_row_id"]: UNIT_REVIEW_SHA256
            },
            gate_id="stale_hash",
        )


def test_diagnostic_gate_rejects_stale_review_identity_or_status(
    tmp_path: Path,
) -> None:
    review_row = _review_row("stale-status")
    oracle_row = _oracle_row(
        review_row,
        oracle_call="accept",
    )
    oracle_row["review_packet_combined_pt_pattern_status"] = (
        "pt_pattern_candidate_partial"
    )
    with pytest.raises(
        ValueError,
        match="review_packet_combined_pt_pattern_status",
    ):
        gate.build_diagnostic_gate_rows(
            review_rows=[review_row],
            oracle_rows=[oracle_row],
            review_packet_sha256_by_row_id={
                review_row["packet_row_id"]: UNIT_REVIEW_SHA256
            },
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
    oracle_row = _oracle_row(
        review_row,
        oracle_call="accept",
    )
    oracle_row[field] = value

    with pytest.raises(ValueError, match=field):
        gate.build_diagnostic_gate_rows(
            review_rows=[review_row],
            oracle_rows=[oracle_row],
            review_packet_sha256_by_row_id={
                review_row["packet_row_id"]: UNIT_REVIEW_SHA256
            },
            gate_id="stale_coordinate",
        )


def test_diagnostic_gate_can_read_full_trace_summary_inputs(
    tmp_path: Path,
) -> None:
    trace_summary_csv = _write_trace_summary_bundle(
        tmp_path / "trace_summary.csv",
        [
            _trace_summary_row("accept-a"),
            _trace_summary_row("reject-shape", pearson="0.84"),
        ],
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
    assert {row["input_schema_version"] for row in rows} == {evidence.SCHEMA_VERSION}
    assert {row["trace_artifact_role"] for row in rows} == {"full_context"}
    assert all(row["candidate_assignment_sha256"] for row in rows)
    assert len({row["trace_extraction_sha256"] for row in rows}) == 1


def test_diagnostic_gate_rejects_trace_summary_changed_after_manifest(
    tmp_path: Path,
) -> None:
    trace_summary_csv = _write_trace_summary_bundle(
        tmp_path / "trace_summary.csv",
        [_trace_summary_row("stale-summary")],
    )
    trace_summary_csv.write_text(
        trace_summary_csv.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="summary_csv sha256 mismatch"):
        gate.run_diagnostic_gate(
            trace_summary_csvs=[
                gate.TraceSummaryInput(
                    source_label="pt_only",
                    path=trace_summary_csv,
                )
            ],
            output_dir=tmp_path / "gate",
            gate_id="stale_trace_summary",
        )


def test_diagnostic_gate_rejects_trace_basis_changed_with_refreshed_file_hash(
    tmp_path: Path,
) -> None:
    trace_summary_csv = _write_trace_summary_bundle(
        tmp_path / "trace_summary.csv",
        [_trace_summary_row("wrong-basis")],
    )
    summary_rows = read_delimited_rows(
        trace_summary_csv,
        required_columns=evidence.SUMMARY_COLUMNS,
        exact_columns=evidence.SUMMARY_COLUMNS,
        delimiter=",",
    )
    summary_rows[0]["trace_decision_basis"] = "raw_vendor_trace"
    write_delimited_rows(trace_summary_csv, summary_rows, evidence.SUMMARY_COLUMNS)
    manifest_json = tmp_path / "pt_pattern_context_trace_diagnosis.json"
    manifest = json.loads(manifest_json.read_text(encoding="utf-8"))
    manifest["output_sha256"]["summary_csv"] = file_sha256(trace_summary_csv).lower()
    manifest_json.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="trace_decision_basis.*trace manifest"):
        gate.run_diagnostic_gate(
            trace_summary_csvs=[
                gate.TraceSummaryInput(source_label="pt_only", path=trace_summary_csv)
            ],
            output_dir=tmp_path / "gate",
            gate_id="wrong_basis",
        )


def test_diagnostic_gate_rejects_header_only_trace_metrics(
    tmp_path: Path,
) -> None:
    trace_summary_csv = _write_trace_summary_bundle(
        tmp_path / "trace_summary.csv",
        [_trace_summary_row("missing-metrics")],
    )
    metrics_csv = tmp_path / "pt_pattern_context_trace_metrics.csv"
    write_delimited_rows(metrics_csv, [], evidence.METRIC_COLUMNS)
    manifest_json = tmp_path / "pt_pattern_context_trace_diagnosis.json"
    manifest = json.loads(manifest_json.read_text(encoding="utf-8"))
    manifest["output_sha256"]["metrics_csv"] = file_sha256(metrics_csv).lower()
    manifest_json.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="candidate/position coverage mismatch"):
        gate.run_diagnostic_gate(
            trace_summary_csvs=[
                gate.TraceSummaryInput(source_label="pt_only", path=trace_summary_csv)
            ],
            output_dir=tmp_path / "gate",
            gate_id="missing_metrics",
        )


def test_diagnostic_gate_rejects_trace_manifest_role_conflict(
    tmp_path: Path,
) -> None:
    trace_summary_csv = _write_trace_summary_bundle(
        tmp_path / "trace_summary.csv",
        [_trace_summary_row("wrong-role")],
    )
    manifest_json = tmp_path / "pt_pattern_context_trace_diagnosis.json"
    manifest = json.loads(manifest_json.read_text(encoding="utf-8"))
    manifest["trace_artifact_role"] = "anchor_local"
    manifest_json.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="artifact role mismatch"):
        gate.run_diagnostic_gate(
            trace_summary_csvs=[
                gate.TraceSummaryInput(
                    source_label="pt_only",
                    path=trace_summary_csv,
                )
            ],
            output_dir=tmp_path / "gate",
            gate_id="wrong_trace_role",
        )


def test_diagnostic_gate_uses_scan_aware_apex_threshold(
    tmp_path: Path,
) -> None:
    trace_summary_csv = _write_trace_summary_bundle(
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


def test_diagnostic_gate_rejects_unsealed_legacy_trace_summary(
    tmp_path: Path,
) -> None:
    trace_summary_csv = _write_csv(
        tmp_path / "legacy_trace_summary.csv",
        [_trace_summary_row("accept-a")],
        gate.TRACE_SUMMARY_GATE_REQUIRED_COLUMNS,
    )

    with pytest.raises(ValueError):
        gate.run_diagnostic_gate(
            review_packet_csvs=[],
            trace_summary_csvs=[
                gate.TraceSummaryInput(source_label="pt_only", path=trace_summary_csv)
            ],
            output_dir=tmp_path / "gate",
            gate_id="unit_legacy_trace_gate",
        )


def test_diagnostic_gate_cli_writes_outputs(tmp_path: Path) -> None:
    review_csv, oracle_csv = _write_verified_review_and_promotion(
        tmp_path / "verified_input",
        candidate_id="accept-a",
        oracle_call="reject",
    )

    from tools.diagnostics import pt_pattern_diagnostic_gate as cli

    assert (
        cli.main(
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
        )
        == 0
    )

    summary = json.loads(
        (tmp_path / "gate" / "pt_pattern_diagnostic_gate_summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert summary["replay_status"] == "diagnostic_gate_replay_passed"


def test_diagnostic_gate_cli_accepts_trace_summary_input(tmp_path: Path) -> None:
    trace_summary_csv = _write_trace_summary_bundle(
        tmp_path / "trace_summary.csv",
        [_trace_summary_row("accept-a")],
    )

    from tools.diagnostics import pt_pattern_diagnostic_gate as cli

    assert (
        cli.main(
            [
                "--trace-summary-input",
                f"pt_only={trace_summary_csv}",
                "--output-dir",
                str(tmp_path / "gate"),
                "--gate-id",
                "unit_trace_gate",
            ]
        )
        == 0
    )

    summary = json.loads(
        (tmp_path / "gate" / "pt_pattern_diagnostic_gate_summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert summary["diagnostic_gate_call_counts"] == {"accept": 1}


def test_diagnostic_gate_cli_can_fail_zero_fpfn_requirement(tmp_path: Path) -> None:
    review_csv, oracle_csv = _write_verified_review_and_promotion(
        tmp_path / "verified_input",
        candidate_id="false-positive",
        oracle_call="accept",
    )

    from tools.diagnostics import pt_pattern_diagnostic_gate as cli

    assert (
        cli.main(
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
        )
        == 3
    )


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
        project_metrics_from_trace_points=False,
    )
    return path


def _write_trace_summary_bundle(
    path: Path,
    rows: list[dict[str, str]],
) -> Path:
    write_trace_summary_lineage_bundle(
        trace_summary_csv=path,
        summary_rows=rows,
    )
    return path


def _write_verified_review_and_promotion(
    output_dir: Path,
    *,
    candidate_id: str,
    oracle_call: str,
    anchor_local: bool = False,
) -> tuple[Path, Path]:
    review_csv = output_dir / "review" / "review.csv"
    review_csv.parent.mkdir(parents=True, exist_ok=True)
    trace_points_csv = review_csv.parent / "pt_pattern_trace_points.csv"
    write_overlay_lineage_bundle(
        review_packet_csv=review_csv,
        packet_rows=[_review_row(candidate_id, anchor_local=anchor_local)],
        trace_points_csv=trace_points_csv,
        trace_point_rows=[],
    )
    review_rows = read_delimited_rows(
        review_csv,
        required_columns=("packet_row_id",),
        delimiter=",",
    )
    promotion_outputs = write_verified_oracle_promotion_bundle(
        review_packet_csv=review_csv,
        trace_points_csv=trace_points_csv,
        oracle_call_by_packet_row_id={review_rows[0]["packet_row_id"]: oracle_call},
        output_dir=output_dir / "oracle",
    )
    return review_csv, promotion_outputs["promotion_packet_csv"]
