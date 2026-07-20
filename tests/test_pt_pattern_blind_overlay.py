from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.diagnostics import pt_pattern_blind_overlay as overlay_cli
from xic_extractor.discovery import pt_pattern_blind_overlay as overlay
from xic_extractor.discovery import pt_pattern_blind_overlay_call as overlay_call
from xic_extractor.discovery import pt_pattern_review_packet as packet
from xic_extractor.discovery import pt_pattern_trace_evidence as evidence
from xic_extractor.tabular_io import (
    file_sha256,
    read_delimited_rows,
    write_delimited_rows,
)


def _packet_row(
    candidate_id: str = "cand-supported",
    *,
    status: str = "pt_pattern_detected_diagnostic",
) -> dict[str, str]:
    row = {column: "" for column in packet.PACKET_COLUMNS}
    row.update(
        {
            "schema_version": packet.ANCHOR_LOCAL_SCHEMA_VERSION,
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
            "trace_smoothing_window_points": str(evidence.TRACE_SMOOTH_WINDOW_POINTS),
            "min_effective_smoothing_window_sec": "0.90",
            "max_trace_point_count": "31",
            "combined_pt_pattern_status": status,
            "non_base_positions_supported": "3",
            "max_abs_apex_delta_sec": "0.5",
            "min_pearson_r_to_base": "0.95",
            "min_active_overlap_fraction": "0.90",
            "min_shifted_pearson_r_to_base": "0.98",
            "max_abs_shape_shift_sec_to_base": "1.0",
            "trace_ambiguity_status": "clear",
            "anchor_segmentation_status": "clear",
            "anchor_segmentation_reason": "one_anchor_segment_per_position",
            "anchor_segmentation_common_rt_start_min": "9.8",
            "anchor_segmentation_common_rt_end_min": "10.2",
            "anchor_segmentation_common_duration_sec": "24.0",
            "anchor_segmentation_common_start_owner_labels": "base",
            "anchor_segmentation_common_end_owner_labels": "pt_plus_4",
            "anchor_segmentation_boundary_review_status": "clear",
            "anchor_segmentation_boundary_review_position_labels": "",
            "anchor_segmentation_max_left_clip_sec": "3.0",
            "anchor_segmentation_max_right_clip_sec": "34.0",
            "anchor_segmentation_min_common_retained_fraction": "0.48",
            "anchor_segmentation_max_common_left_residual_fraction": "0.10",
            "anchor_segmentation_max_common_right_residual_fraction": "0.49",
            "anchor_segmentation_descending_at_common_right_position_labels": (
                "base|pt_plus_1|pt_plus_2|pt_plus_4"
            ),
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
    assert "+4.005213" in html
    assert "<svg" in html
    assert "原始儀器 trace" in html
    assert "Gaussian15 點數平滑 trace" in html
    assert "20 秒時間平滑 trace" in html
    assert html.count("normalized Pt pattern traces") == 3
    assert "密封機器判斷" in html
    assert "decision metrics" in html
    assert "有效平滑窗" in html
    assert "Anchor 局部邊界證據" in html
    assert "僅作 assessability gate" in html
    assert "review_required 只會導向" in html
    assert "uncertain，不作正向 accept/reject evidence" in html
    assert "共同右界 owner: pt_plus_4" in html
    assert "最大右側截斷: 34.0 sec" in html
    assert "共同右界仍下降: base|pt_plus_1|pt_plus_2|pt_plus_4" in html
    assert "0.90 sec" in html
    assert (
        '<meta name="viewport" content="width=device-width, initial-scale=1">' in html
    )
    assert "盲測疊圖" in html
    assert "原始儀器 trace" in html
    assert "每條 trace 均以自身最大值正規化" in html
    assert ".svg-wrap svg" in html
    assert "height: auto" in html
    assert "header { position: static;" in html
    assert html.count('class="panel-legend"') == 3
    assert 'stroke-dasharray="9 4"' in html
    assert 'stroke-dasharray="3 3"' in html
    assert 'stroke-dasharray="12 3 2 3"' in html

    machine_rows = read_delimited_rows(
        outputs["machine_calls_csv"],
        required_columns=overlay.MACHINE_CALL_COLUMNS,
        delimiter=",",
    )
    assert machine_rows[0]["schema_version"] == overlay.ANCHOR_LOCAL_SCHEMA_VERSION
    assert machine_rows[0]["machine_call"] == "accept"
    assert (
        machine_rows[0]["machine_call_reason"]
        == "full_pattern_close_apex_shape_overlap"
    )
    assert machine_rows[0]["min_shifted_pearson_r_to_base"] == "0.98"
    assert machine_rows[0]["max_abs_shape_shift_sec_to_base"] == "1.0"
    assert machine_rows[0]["min_effective_smoothing_window_sec"] == "0.90"
    assert machine_rows[0]["max_trace_point_count"] == "31"
    assert machine_rows[0]["anchor_segmentation_boundary_review_status"] == "clear"
    assert machine_rows[0]["anchor_segmentation_common_end_owner_labels"] == (
        "pt_plus_4"
    )

    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    assert summary["schema_version"] == overlay.ANCHOR_LOCAL_SCHEMA_VERSION
    assert summary["blind_to_machine_calls_in_html"] is True
    assert summary["oracle_status"] == "pending_human_review"
    assert summary["smooth_method"] == evidence.TRACE_SMOOTH_METHOD
    assert summary["smooth_window_points"] == 15
    assert summary["time_smooth_window_sec"] == 20.0
    assert summary["trace_decision_basis"] == evidence.TRACE_DECISION_BASIS
    assert summary["review_packet_csv_sha256"] == file_sha256(review_packet)
    assert summary["trace_points_csv_sha256"] == file_sha256(trace_points)
    assert summary["context_trace_points_csv_sha256"] == ""
    assert summary["outputs"]["gallery_html_sha256"] == file_sha256(
        outputs["gallery_html"]
    )
    assert summary["outputs"]["machine_calls_csv_sha256"] == file_sha256(
        outputs["machine_calls_csv"]
    )
    assert summary["smooth_applies_to"] == (
        "sealed_machine_calls_use_packet_decision_metrics;"
        "gallery_html_shows_raw_point_smoothed_time_smoothed"
    )


def test_blind_overlay_preserves_original_case_id_from_packet_row_id(
    tmp_path: Path,
) -> None:
    review_packet = tmp_path / "packet.csv"
    trace_points = tmp_path / "trace_points.csv"
    row = _packet_row()
    row["packet_row_id"] = "unit_packet|Case 07|cand-supported"
    write_delimited_rows(review_packet, [row], packet.PACKET_COLUMNS)
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
    assert "<h2>案例 Case 07</h2>" in html
    assert "<h2>案例 Case 01</h2>" not in html


def test_blind_overlay_accepts_legacy_packet_without_ambiguity_columns(
    tmp_path: Path,
) -> None:
    review_packet = tmp_path / "packet.csv"
    trace_points = tmp_path / "trace_points.csv"
    legacy_columns = tuple(
        column
        for column in packet.PACKET_COLUMNS
        if column
        not in ({
            "trace_ambiguity_status",
            "trace_ambiguity_flags",
            "shifted_only_position_labels",
        } | set(evidence.ANCHOR_SEGMENTATION_SUMMARY_COLUMNS))
    )
    legacy_row = {
        column: value
        for column, value in _packet_row().items()
        if column in legacy_columns
    }
    legacy_row["schema_version"] = packet.SCHEMA_VERSION
    write_delimited_rows(review_packet, [legacy_row], legacy_columns)
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

    machine_rows = read_delimited_rows(
        outputs["machine_calls_csv"],
        required_columns=overlay.LEGACY_MACHINE_CALL_COLUMNS,
        delimiter=",",
    )
    assert machine_rows[0]["schema_version"] == overlay.SCHEMA_VERSION
    legacy_summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    assert legacy_summary["schema_version"] == overlay.SCHEMA_VERSION
    assert machine_rows[0]["machine_call"] == "accept"
    assert machine_rows[0]["trace_ambiguity_status"] == ""
    assert machine_rows[0]["trace_ambiguity_flags"] == ""
    assert machine_rows[0]["shifted_only_position_labels"] == ""
    assert "anchor_segmentation_boundary_review_status" not in machine_rows[0]
    assert "anchor_segmentation_common_end_owner_labels" not in machine_rows[0]


@pytest.mark.parametrize("mutation", ["unknown_position", "extra_candidate"])
def test_blind_overlay_rejects_invalid_local_trace_bundle(
    tmp_path: Path,
    mutation: str,
) -> None:
    review_packet = tmp_path / "packet.csv"
    trace_points = tmp_path / "trace_points.csv"
    write_delimited_rows(review_packet, [_packet_row()], packet.PACKET_COLUMNS)
    rows = _trace_points()
    if mutation == "unknown_position":
        rows[-1]["position_label"] = "pt_plus_3"
    else:
        rows.extend(_trace_points("extra-candidate"))
    write_delimited_rows(trace_points, rows, overlay.TRACE_POINT_REQUIRED_COLUMNS)

    with pytest.raises(
        ValueError,
        match="outside review packet|unsupported position_label",
    ):
        overlay.run_blind_overlay(
            review_packet_csv=review_packet,
            trace_points_csv=trace_points,
            output_dir=tmp_path / "overlay",
            title="Invalid trace bundle",
        )


@pytest.mark.parametrize("missing_position", [None, "pt_plus_4"])
def test_blind_overlay_renders_candidates_with_empty_position_traces(
    tmp_path: Path,
    missing_position: str | None,
) -> None:
    review_packet = tmp_path / "packet.csv"
    trace_points = tmp_path / "trace_points.csv"
    write_delimited_rows(review_packet, [_packet_row()], packet.PACKET_COLUMNS)
    rows = (
        []
        if missing_position is None
        else [
            row
            for row in _trace_points()
            if row["position_label"] != missing_position
        ]
    )
    write_delimited_rows(trace_points, rows, overlay.TRACE_POINT_REQUIRED_COLUMNS)

    outputs = overlay.run_blind_overlay(
        review_packet_csv=review_packet,
        trace_points_csv=trace_points,
        output_dir=tmp_path / "overlay",
        title="Incomplete trace bundle",
    )

    html = outputs["gallery_html"].read_text(encoding="utf-8")
    assert "cand-supported" in html
    if missing_position is None:
        assert "No trace points" in html


def test_blind_overlay_rejects_trace_sample_mismatch(tmp_path: Path) -> None:
    review_packet = tmp_path / "packet.csv"
    trace_points = tmp_path / "trace_points.csv"
    write_delimited_rows(review_packet, [_packet_row()], packet.PACKET_COLUMNS)
    rows = [dict(row) | {"sample_stem": "WrongSample"} for row in _trace_points()]
    write_delimited_rows(
        trace_points,
        rows,
        (*overlay.TRACE_POINT_REQUIRED_COLUMNS, "sample_stem"),
    )

    with pytest.raises(ValueError, match="sample_stem does not match"):
        overlay.run_blind_overlay(
            review_packet_csv=review_packet,
            trace_points_csv=trace_points,
            output_dir=tmp_path / "overlay",
            title="Mismatched sample",
        )


def test_blind_overlay_accepts_context_trace_bundle_with_missing_position(
    tmp_path: Path,
) -> None:
    review_packet = tmp_path / "packet.csv"
    local_trace_points = tmp_path / "local.csv"
    context_trace_points = tmp_path / "context.csv"
    write_delimited_rows(review_packet, [_packet_row()], packet.PACKET_COLUMNS)
    write_delimited_rows(
        local_trace_points,
        _trace_points(),
        overlay.TRACE_POINT_REQUIRED_COLUMNS,
    )
    write_delimited_rows(
        context_trace_points,
        [row for row in _trace_points() if row["position_label"] != "base"],
        overlay.TRACE_POINT_REQUIRED_COLUMNS,
    )

    outputs = overlay.run_blind_overlay(
        review_packet_csv=review_packet,
        trace_points_csv=local_trace_points,
        context_trace_points_csv=context_trace_points,
        output_dir=tmp_path / "overlay",
        title="Incomplete context",
    )

    assert outputs["gallery_html"].is_file()


def test_blind_overlay_accepts_empty_local_bundle_with_full_context(
    tmp_path: Path,
) -> None:
    review_packet = tmp_path / "packet.csv"
    local_trace_points = tmp_path / "local.csv"
    context_trace_points = tmp_path / "context.csv"
    write_delimited_rows(review_packet, [_packet_row()], packet.PACKET_COLUMNS)
    write_delimited_rows(
        local_trace_points,
        [],
        overlay.TRACE_POINT_REQUIRED_COLUMNS,
    )
    write_delimited_rows(
        context_trace_points,
        _trace_points(),
        overlay.TRACE_POINT_REQUIRED_COLUMNS,
    )

    outputs = overlay.run_blind_overlay(
        review_packet_csv=review_packet,
        trace_points_csv=local_trace_points,
        context_trace_points_csv=context_trace_points,
        output_dir=tmp_path / "overlay",
        title="Empty local trace bundle",
    )

    html = outputs["gallery_html"].read_text(encoding="utf-8")
    assert html.count("No trace points") == 2
    assert outputs["machine_calls_csv"].is_file()


def test_blind_overlay_rejects_unknown_packet_schema(tmp_path: Path) -> None:
    review_packet = tmp_path / "packet.csv"
    trace_points = tmp_path / "trace_points.csv"
    row = _packet_row()
    row["schema_version"] = "pt_pattern_manual_review_packet.v999"
    write_delimited_rows(review_packet, [row], packet.PACKET_COLUMNS)
    write_delimited_rows(
        trace_points,
        _trace_points(),
        overlay.TRACE_POINT_REQUIRED_COLUMNS,
    )

    with pytest.raises(ValueError, match="unsupported schema_version"):
        overlay.run_blind_overlay(
            review_packet_csv=review_packet,
            trace_points_csv=trace_points,
            output_dir=tmp_path / "overlay",
            title="Unknown schema",
        )


def test_blind_overlay_rejects_partial_anchor_packet_schema(tmp_path: Path) -> None:
    review_packet = tmp_path / "packet.csv"
    trace_points = tmp_path / "trace_points.csv"
    missing_column = "anchor_segmentation_status"
    partial_columns = tuple(
        column for column in packet.PACKET_COLUMNS if column != missing_column
    )
    partial_row = {
        column: value
        for column, value in _packet_row().items()
        if column in partial_columns
    }
    write_delimited_rows(review_packet, [partial_row], partial_columns)
    write_delimited_rows(
        trace_points,
        _trace_points(),
        overlay.TRACE_POINT_REQUIRED_COLUMNS,
    )

    with pytest.raises(ValueError, match="requires all anchor-local columns"):
        overlay.run_blind_overlay(
            review_packet_csv=review_packet,
            trace_points_csv=trace_points,
            output_dir=tmp_path / "overlay",
            title="Partial schema",
        )


def test_blind_overlay_rejects_mixed_packet_schema_rows() -> None:
    anchor_row = _packet_row()
    legacy_row = {
        column: value
        for column, value in anchor_row.items()
        if column in packet.LEGACY_PACKET_COLUMNS
    }
    legacy_row["schema_version"] = packet.SCHEMA_VERSION
    legacy_row["candidate_id"] = "legacy-candidate"
    legacy_row["packet_row_id"] = "pt_only|legacy-candidate"

    with pytest.raises(ValueError, match="must use one schema_version"):
        overlay_call.validate_packet_rows((anchor_row, legacy_row))


def test_blind_overlay_rejects_duplicate_packet_row_id() -> None:
    first = _packet_row("candidate-a")
    second = _packet_row("candidate-b")
    second["packet_row_id"] = first["packet_row_id"]

    with pytest.raises(ValueError, match="duplicate review packet packet_row_id"):
        overlay_call.validate_packet_rows((first, second))


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


@pytest.mark.parametrize(
    ("review_field", "expected_reason"),
    [
        ("trace_ambiguity_status", "trace_ambiguity_review_required"),
        (
            "anchor_segmentation_boundary_review_status",
            "anchor_segmentation_boundary_review_required",
        ),
    ],
)
def test_machine_call_routes_review_required_evidence_to_uncertain(
    review_field: str,
    expected_reason: str,
) -> None:
    call, reason = overlay.classify_machine_call(
        _packet_row() | {review_field: "review_required"}
    )

    assert (call, reason) == ("uncertain", expected_reason)
    assert overlay_call.MACHINE_CALL_SCOPE == "trace_evidence_disposition_only"


@pytest.mark.parametrize(
    ("field", "value", "expected_reason"),
    [
        ("trace_ambiguity_status", "", "trace_ambiguity_not_clear"),
        ("trace_ambiguity_status", "unknown", "trace_ambiguity_not_clear"),
        ("anchor_segmentation_status", "", "anchor_segmentation_not_clear"),
        (
            "anchor_segmentation_status",
            "missing_anchor_segment",
            "anchor_segmentation_not_clear",
        ),
        (
            "anchor_segmentation_boundary_review_status",
            "unavailable",
            "anchor_segmentation_boundary_not_clear",
        ),
        (
            "anchor_segmentation_boundary_review_status",
            "unknown",
            "anchor_segmentation_boundary_not_clear",
        ),
    ],
)
def test_anchor_machine_call_fails_closed_unless_assessability_is_clear(
    field: str,
    value: str,
    expected_reason: str,
) -> None:
    call, reason = overlay.classify_machine_call(_packet_row() | {field: value})

    assert (call, reason) == ("uncertain", expected_reason)


def test_machine_call_rejects_shifted_shape_review_only_packet() -> None:
    call, reason = overlay.classify_machine_call(
        _packet_row()
        | {
            "max_abs_apex_delta_sec": "24.0",
            "min_pearson_r_to_base": "-0.20",
            "min_active_overlap_fraction": "0.90",
            "min_shifted_pearson_r_to_base": "0.92",
            "max_abs_shape_shift_sec_to_base": "24.0",
        }
    )

    assert (call, reason) == ("reject", "shifted_shape_review_only")


def test_machine_call_rejects_shifted_shape_above_hard_cap() -> None:
    call, reason = overlay.classify_machine_call(
        _packet_row()
        | {
            "max_abs_apex_delta_sec": "75.0",
            "min_pearson_r_to_base": "-0.20",
            "min_active_overlap_fraction": "0.90",
            "min_shifted_pearson_r_to_base": "0.98",
            "max_abs_shape_shift_sec_to_base": "75.0",
        }
    )

    assert (call, reason) == (
        "reject",
        "shape_shift_above_threshold",
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

    assert [rt for rt, _intensity in smoothed] == [rt for rt, _intensity in raw_points]
    assert smoothed[2][1] < 100.0
    assert smoothed[1][1] > 0.0
    assert smoothed[3][1] > 0.0
    assert overlay.classify_machine_call(_packet_row())[0] == "accept"


def test_time_smoothed_trace_uses_seconds_not_point_count() -> None:
    raw_points = tuple(
        (10.0 + index * 0.001, 100.0 if index == 50 else 0.0) for index in range(101)
    )

    point_smoothed = overlay.smooth_trace_points(raw_points, window_points=15)
    time_smoothed = overlay.smooth_trace_points_by_time(
        raw_points,
        window_sec=4.0,
    )

    assert [rt for rt, _intensity in time_smoothed] == [
        rt for rt, _intensity in raw_points
    ]
    assert time_smoothed[50][1] < point_smoothed[50][1]
    assert time_smoothed[25][1] > point_smoothed[25][1]


def test_blind_overlay_separates_context_shape_and_shared_scale(
    tmp_path: Path,
) -> None:
    review_packet = tmp_path / "packet.csv"
    local_trace_points = tmp_path / "local_trace_points.csv"
    context_trace_points = tmp_path / "context_trace_points.csv"
    write_delimited_rows(review_packet, [_packet_row()], packet.PACKET_COLUMNS)
    write_delimited_rows(
        local_trace_points,
        _trace_points(),
        overlay.TRACE_POINT_REQUIRED_COLUMNS,
    )
    context_rows = _trace_points()
    context_rows.append(
        {
            "candidate_id": "cand-supported",
            "position_label": "base",
            "rt_min": "11.0",
            "intensity": "250",
        }
    )
    write_delimited_rows(
        context_trace_points,
        context_rows,
        overlay.TRACE_POINT_REQUIRED_COLUMNS,
    )

    outputs = overlay.run_blind_overlay(
        review_packet_csv=review_packet,
        trace_points_csv=local_trace_points,
        context_trace_points_csv=context_trace_points,
        output_dir=tmp_path / "overlay",
        title="Anchor-local Blind Overlay",
    )

    html = outputs["gallery_html"].read_text(encoding="utf-8")
    assert "全視窗背景（各自最大值 = 1）" in html
    assert "Anchor 局部峰型（各自最大值 = 1）" in html
    assert "Anchor 局部強度（+1 apex = 1）" in html
    assert html.count('aria-label="normalized Pt pattern traces"') == 2
    assert html.count('aria-label="shared-scale Pt pattern traces"') == 1
    assert html.count("11.000 min") == 1
    assert "+1 apex = 1" in html
    assert "背景圖與峰型圖採各自最大值正規化" in html
    assert "overflow-wrap: anywhere" in html
    assert "box-sizing: border-box" in html

    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    assert summary["gallery_mode"] == "anchor_local_semantic"
    assert "full_context_point_smoothed" in summary["smooth_applies_to"]
    assert summary["context_trace_points_csv_sha256"] == file_sha256(
        context_trace_points
    )


def test_shared_scale_svg_fails_closed_without_plus1_signal() -> None:
    traces = {
        "base": ((9.9, 0.0), (10.0, 100.0), (10.1, 0.0)),
        "pt_plus_1": ((9.9, 0.0), (10.0, 0.0), (10.1, 0.0)),
        "pt_plus_2": ((9.9, 0.0), (10.0, 40.0), (10.1, 0.0)),
        "pt_plus_4": ((9.9, 0.0), (10.0, 20.0), (10.1, 0.0)),
    }

    svg = overlay._render_overlay_svg(traces, normalization="plus1_apex")

    assert 'aria-label="shared-scale unavailable"' in svg
    assert "+1 apex is missing or zero" in svg
    assert "+1 apex = 1" not in svg


def test_shared_scale_uses_plus1_apex_as_common_denominator() -> None:
    traces = {
        "base": ((9.9, 0.0), (10.0, 200.0), (10.1, 0.0)),
        "pt_plus_1": ((9.9, 0.0), (10.0, 100.0), (10.1, 0.0)),
        "pt_plus_2": ((9.9, 0.0), (10.0, 50.0), (10.1, 0.0)),
        "pt_plus_4": ((9.9, 0.0), (10.0, 25.0), (10.1, 0.0)),
    }

    svg = overlay._render_overlay_svg(traces, normalization="plus1_apex")

    assert 'aria-label="shared-scale Pt pattern traces"' in svg
    assert "+1 apex = 1; y max = 2.00" in svg


def test_cli_passes_optional_context_trace_points(
    tmp_path: Path,
    monkeypatch,
) -> None:
    review_packet = tmp_path / "packet.csv"
    local_trace_points = tmp_path / "local.csv"
    context_trace_points = tmp_path / "context.csv"
    for path in (review_packet, local_trace_points, context_trace_points):
        path.write_text("placeholder", encoding="utf-8")

    captured: dict[str, object] = {}

    def fake_run_blind_overlay(**kwargs):
        captured.update(kwargs)
        output_dir = kwargs["output_dir"]
        return {
            "gallery_html": output_dir / "gallery.html",
            "machine_calls_csv": output_dir / "calls.csv",
            "summary_json": output_dir / "summary.json",
        }

    monkeypatch.setattr(overlay_cli, "run_blind_overlay", fake_run_blind_overlay)

    exit_code = overlay_cli.main(
        [
            "--review-packet-csv",
            str(review_packet),
            "--trace-points-csv",
            str(local_trace_points),
            "--context-trace-points-csv",
            str(context_trace_points),
            "--output-dir",
            str(tmp_path / "overlay"),
            "--title",
            "Anchor local",
        ]
    )

    assert exit_code == 0
    assert captured["trace_points_csv"] == local_trace_points.resolve()
    assert captured["context_trace_points_csv"] == context_trace_points.resolve()
