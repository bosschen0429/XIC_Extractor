from __future__ import annotations

import json
from pathlib import Path

from xic_extractor.discovery import pt_pattern_blind_overlay as overlay
from xic_extractor.discovery import pt_pattern_review_packet as packet
from xic_extractor.discovery import pt_pattern_trace_evidence as evidence
from xic_extractor.tabular_io import read_delimited_rows, write_delimited_rows


def _packet_row(
    candidate_id: str = "cand-supported",
    *,
    status: str = "pt_pattern_detected_diagnostic",
) -> dict[str, str]:
    row = {column: "" for column in packet.PACKET_COLUMNS}
    row.update(
        {
            "schema_version": packet.SCHEMA_VERSION,
            "readiness_tier": "diagnostic_only",
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
            "non_base_positions_supported": "3",
            "max_abs_apex_delta_sec": "2.0",
            "min_pearson_r_to_base": "0.95",
            "min_active_overlap_fraction": "0.90",
            "oracle_status": "pending_human_review",
        }
    )
    return row


def _trace_points(candidate_id: str = "cand-supported") -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for label, scale in {
        "base": 100.0,
        "pt_plus_1": 40.0,
        "pt_plus_2": 20.0,
        "pt_plus_4": 10.0,
    }.items():
        for rt, intensity in [("9.9", "0"), ("10.0", str(scale)), ("10.1", "0")]:
            rows.append(
                {
                    "candidate_id": candidate_id,
                    "position_label": label,
                    "rt_min": rt,
                    "intensity": intensity,
                }
            )
    return rows


def test_machine_call_is_sealed_outside_blind_html(tmp_path: Path) -> None:
    review_packet = tmp_path / "packet.csv"
    trace_points = tmp_path / "trace_points.csv"
    write_delimited_rows(review_packet, [_packet_row()], packet.PACKET_COLUMNS)
    write_delimited_rows(
        trace_points,
        _trace_points(),
        overlay.TRACE_POINT_REQUIRED_COLUMNS,
    )

    outputs = overlay.run_blind_overlay(
        review_packet_csv=review_packet,
        trace_points_csv=trace_points,
        output_dir=tmp_path / "overlay",
        title="Unit Blind Overlay",
    )

    html = outputs["gallery_html"].read_text(encoding="utf-8")
    assert "pt_pattern_detected_diagnostic" not in html
    assert "machine_call" not in html
    assert ">accept<" not in html
    assert "+4.0052" in html
    assert "<svg" in html
    assert "Gaussian15-smoothed" in html

    machine_rows = read_delimited_rows(
        outputs["machine_calls_csv"],
        required_columns=overlay.MACHINE_CALL_COLUMNS,
        delimiter=",",
    )
    assert machine_rows[0]["machine_call"] == "accept"
    assert (
        machine_rows[0]["machine_call_reason"]
        == "all_positions_trace_supported_with_close_apex_and_shape"
    )

    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    assert summary["blind_to_machine_calls_in_html"] is True
    assert summary["oracle_status"] == "pending_human_review"
    assert summary["smooth_method"] == evidence.TRACE_SMOOTH_METHOD
    assert summary["smooth_window_points"] == 15
    assert summary["trace_decision_basis"] == evidence.TRACE_DECISION_BASIS
    assert summary["smooth_applies_to"] == "gallery_html_and_sealed_machine_calls"


def test_machine_call_marks_weak_partial_cases_uncertain_or_reject() -> None:
    uncertain_call, uncertain_reason = overlay.classify_machine_call(
        _packet_row(
            "partial",
            status="pt_pattern_candidate_partial",
        )
        | {
            "non_base_positions_supported": "2",
            "min_pearson_r_to_base": "0.55",
        }
    )
    reject_call, reject_reason = overlay.classify_machine_call(
        _packet_row(
            "incomplete",
            status="pt_pattern_candidate_incomplete",
        )
    )

    assert (uncertain_call, uncertain_reason) == (
        "uncertain",
        "requires_visual_or_human_review",
    )
    assert (reject_call, reject_reason) == (
        "reject",
        "pt_pattern_candidate_incomplete",
    )


def test_machine_call_refuses_stale_raw_basis_packet() -> None:
    call, reason = overlay.classify_machine_call(
        _packet_row()
        | {
            "trace_decision_basis": "raw_vendor_trace",
            "trace_smoothing_method": "",
            "trace_smoothing_window_points": "",
        }
    )

    assert (call, reason) == (
        "uncertain",
        "unsupported_or_stale_trace_decision_basis",
    )


def test_visual_overlay_smooths_trace_points_without_changing_machine_call() -> None:
    raw_points = (
        (9.8, 0.0),
        (9.9, 0.0),
        (10.0, 100.0),
        (10.1, 0.0),
        (10.2, 0.0),
    )

    smoothed = overlay.smooth_trace_points(raw_points, window_points=5)

    assert [rt for rt, _intensity in smoothed] == [
        rt for rt, _intensity in raw_points
    ]
    assert smoothed[2][1] < 100.0
    assert smoothed[1][1] > 0.0
    assert smoothed[3][1] > 0.0
    assert overlay.classify_machine_call(_packet_row())[0] == "accept"
