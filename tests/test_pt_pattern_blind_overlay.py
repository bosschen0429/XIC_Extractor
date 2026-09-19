from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.pt_pattern_trace_fixtures import write_overlay_lineage_bundle
from tools.diagnostics import pt_pattern_blind_overlay as overlay_cli
from xic_extractor.discovery import pt_pattern_blind_overlay as overlay
from xic_extractor.discovery import pt_pattern_blind_overlay_call as overlay_call
from xic_extractor.discovery import pt_pattern_review_packet as packet
from xic_extractor.discovery import pt_pattern_trace_batch as trace_batch
from xic_extractor.discovery import pt_pattern_trace_evidence as evidence
from xic_extractor.discovery.pt_pattern_review_bundle import (
    load_verified_blind_overlay_bundle,
)
from xic_extractor.tabular_io import (
    file_sha256,
    format_diagnostic_value,
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
            "schema_version": packet.SCHEMA_VERSION,
            "readiness_tier": "diagnostic_only",
            "evidence_owner": packet.EVIDENCE_OWNER,
            "packet_label": "unit_packet",
            "source_label": "pt_only",
            "packet_row_id": f"pt_only|{candidate_id}",
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
            "min_effective_smoothing_window_sec": "9.0",
            "min_median_rt_step_sec": "0.6",
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
            "anchor_segmentation_common_rt_start_min": "9.7",
            "anchor_segmentation_common_rt_end_min": "10.2",
            "anchor_segmentation_common_duration_sec": "30.0",
            "anchor_segmentation_common_start_owner_labels": "base",
            "anchor_segmentation_common_end_owner_labels": "pt_plus_4",
            "anchor_segmentation_boundary_review_status": "clear",
            "anchor_segmentation_boundary_review_position_labels": "none",
            "anchor_segmentation_max_left_clip_sec": "6.0",
            "anchor_segmentation_max_right_clip_sec": "12.0",
            "anchor_segmentation_min_common_retained_fraction": "0.625",
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
        "pt_plus_1": 80.0,
        "pt_plus_2": 60.0,
        "pt_plus_4": 40.0,
    }.items():
        for index in range(31):
            rt = 9.85 + index * 0.01
            intensity = scale * max(0.0, 1.0 - abs(index - 15) / 15.0)
            rows.append(
                {
                    "candidate_id": candidate_id,
                    "position_label": label,
                    "rt_min": format_diagnostic_value(rt),
                    "intensity": format_diagnostic_value(intensity),
                }
            )
    return rows


def _refresh_primary_trace_bindings(
    bundle: dict[str, Path | None],
    *,
    trace_points_csv: Path | None = None,
    trace_metrics_csv: Path | None = None,
    trace_summary_csv: Path | None = None,
) -> None:
    trace_manifest_path = bundle["trace_manifest_json"]
    packet_manifest_path = bundle["review_packet_manifest_json"]
    assert isinstance(trace_manifest_path, Path)
    assert isinstance(packet_manifest_path, Path)
    trace_manifest = json.loads(trace_manifest_path.read_text(encoding="utf-8"))
    if trace_points_csv is not None:
        trace_manifest["output_sha256"]["trace_points_csv"] = file_sha256(
            trace_points_csv
        ).lower()
    if trace_metrics_csv is not None:
        metrics_sha256 = file_sha256(trace_metrics_csv).lower()
        trace_manifest["output_sha256"]["metrics_csv"] = metrics_sha256
    if trace_summary_csv is not None:
        trace_manifest["output_sha256"]["summary_csv"] = file_sha256(
            trace_summary_csv
        ).lower()
    trace_manifest_path.write_text(
        json.dumps(trace_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    packet_manifest = json.loads(packet_manifest_path.read_text(encoding="utf-8"))
    if trace_metrics_csv is not None:
        packet_manifest["input_sha256"]["trace_metrics_csv"] = file_sha256(
            trace_metrics_csv
        ).lower()
    if trace_summary_csv is not None:
        packet_manifest["input_sha256"]["trace_summary_csv"] = file_sha256(
            trace_summary_csv
        ).lower()
    packet_manifest["input_sha256"]["trace_manifest_json"] = file_sha256(
        trace_manifest_path
    ).lower()
    packet_manifest_path.write_text(
        json.dumps(packet_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _limit_review_packet_to_one_row(
    review_packet: Path,
    bundle: dict[str, Path | None],
) -> None:
    packet_rows = read_delimited_rows(
        review_packet,
        required_columns=packet.PACKET_COLUMNS,
        exact_columns=packet.PACKET_COLUMNS,
        delimiter=",",
        encoding="utf-8-sig",
    )
    assert len(packet_rows) == 2
    write_delimited_rows(review_packet, packet_rows[:1], packet.PACKET_COLUMNS)

    packet_manifest_path = bundle["review_packet_manifest_json"]
    assert isinstance(packet_manifest_path, Path)
    packet_manifest = json.loads(packet_manifest_path.read_text(encoding="utf-8"))
    packet_manifest["max_per_status"] = 1
    packet_manifest["packet_row_count"] = 1
    packet_manifest["outputs"]["packet_csv_sha256"] = file_sha256(
        review_packet
    ).lower()
    packet_manifest_path.write_text(
        json.dumps(packet_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _replace_candidate_trace_intensity(
    trace_points: Path,
    *,
    candidate_id: str,
    value: str,
) -> None:
    rows = read_delimited_rows(
        trace_points,
        required_columns=evidence.TRACE_POINT_COLUMNS,
        exact_columns=evidence.TRACE_POINT_COLUMNS,
        delimiter=",",
        encoding="utf-8-sig",
    )
    target = next(row for row in rows if row["candidate_id"] == candidate_id)
    target["intensity"] = value
    write_delimited_rows(trace_points, rows, evidence.TRACE_POINT_COLUMNS)


def test_machine_call_is_sealed_outside_blind_html(tmp_path: Path) -> None:
    review_packet = tmp_path / "packet.csv"
    trace_points = tmp_path / "trace_points.csv"
    write_overlay_lineage_bundle(
        review_packet_csv=review_packet,
        packet_rows=[_packet_row()],
        trace_points_csv=trace_points,
        trace_point_rows=_trace_points(),
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
    assert "最大右側截斷: 12 sec" in html
    assert "共同右界仍下降: base|pt_plus_1|pt_plus_2|pt_plus_4" in html
    assert "9 sec" in html
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
    assert machine_rows[0]["schema_version"] == overlay.SCHEMA_VERSION
    assert machine_rows[0]["machine_call"] == "accept"
    assert (
        machine_rows[0]["machine_call_reason"]
        == "full_pattern_close_apex_shape_overlap"
    )
    assert machine_rows[0]["min_shifted_pearson_r_to_base"] == "1"
    assert machine_rows[0]["max_abs_shape_shift_sec_to_base"] == "0"
    assert machine_rows[0]["min_effective_smoothing_window_sec"] == "9"
    assert machine_rows[0]["min_median_rt_step_sec"] == "0.6"
    assert machine_rows[0]["max_trace_point_count"] == "31"
    assert machine_rows[0]["anchor_segmentation_boundary_review_status"] == "clear"
    assert machine_rows[0]["anchor_segmentation_common_end_owner_labels"] == (
        "pt_plus_4"
    )
    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    assert summary["schema_version"] == overlay.SCHEMA_VERSION
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


def test_blind_overlay_accepts_packet_subset_of_complete_trace_bundle(
    tmp_path: Path,
) -> None:
    review_packet = tmp_path / "packet.csv"
    trace_points = tmp_path / "trace_points.csv"
    context_trace_points = tmp_path / "context_trace_points.csv"
    first_id = "cand-first"
    second_id = "cand-second"
    bundle = write_overlay_lineage_bundle(
        review_packet_csv=review_packet,
        packet_rows=[_packet_row(first_id), _packet_row(second_id)],
        trace_points_csv=trace_points,
        trace_point_rows=[*_trace_points(first_id), *_trace_points(second_id)],
        context_trace_points_csv=context_trace_points,
        context_trace_point_rows=[
            *_trace_points(first_id),
            *_trace_points(second_id),
        ],
    )
    _limit_review_packet_to_one_row(review_packet, bundle)

    outputs = overlay.run_blind_overlay(
        review_packet_csv=review_packet,
        trace_points_csv=trace_points,
        context_trace_points_csv=context_trace_points,
        output_dir=tmp_path / "overlay",
        title="Bounded packet from complete trace bundle",
    )

    verified = load_verified_blind_overlay_bundle(outputs["summary_json"])
    assert len(verified.review_packet.rows) == 1
    html = outputs["gallery_html"].read_text(encoding="utf-8")
    assert first_id in html
    assert second_id not in html


def test_blind_overlay_validates_non_packet_primary_trace_rows(
    tmp_path: Path,
) -> None:
    review_packet = tmp_path / "packet.csv"
    trace_points = tmp_path / "trace_points.csv"
    first_id = "cand-first"
    second_id = "cand-second"
    bundle = write_overlay_lineage_bundle(
        review_packet_csv=review_packet,
        packet_rows=[_packet_row(first_id), _packet_row(second_id)],
        trace_points_csv=trace_points,
        trace_point_rows=[*_trace_points(first_id), *_trace_points(second_id)],
    )
    _limit_review_packet_to_one_row(review_packet, bundle)
    _replace_candidate_trace_intensity(
        trace_points,
        candidate_id=second_id,
        value="nan",
    )
    _refresh_primary_trace_bindings(bundle, trace_points_csv=trace_points)

    with pytest.raises(ValueError, match="finite non-negative"):
        overlay.run_blind_overlay(
            review_packet_csv=review_packet,
            trace_points_csv=trace_points,
            output_dir=tmp_path / "overlay",
            title="Invalid non-packet primary trace",
        )


def test_blind_overlay_validates_non_packet_context_trace_rows(
    tmp_path: Path,
) -> None:
    review_packet = tmp_path / "packet.csv"
    trace_points = tmp_path / "trace_points.csv"
    context_trace_points = tmp_path / "context_trace_points.csv"
    first_id = "cand-first"
    second_id = "cand-second"
    bundle = write_overlay_lineage_bundle(
        review_packet_csv=review_packet,
        packet_rows=[_packet_row(first_id), _packet_row(second_id)],
        trace_points_csv=trace_points,
        trace_point_rows=[*_trace_points(first_id), *_trace_points(second_id)],
        context_trace_points_csv=context_trace_points,
        context_trace_point_rows=[
            *_trace_points(first_id),
            *_trace_points(second_id),
        ],
    )
    _limit_review_packet_to_one_row(review_packet, bundle)
    _replace_candidate_trace_intensity(
        context_trace_points,
        candidate_id=second_id,
        value="nan",
    )
    context_manifest_path = bundle["context_trace_manifest_json"]
    assert isinstance(context_manifest_path, Path)
    context_manifest = json.loads(context_manifest_path.read_text(encoding="utf-8"))
    context_manifest["output_sha256"]["trace_points_csv"] = file_sha256(
        context_trace_points
    ).lower()
    context_manifest_path.write_text(
        json.dumps(context_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="finite non-negative"):
        overlay.run_blind_overlay(
            review_packet_csv=review_packet,
            trace_points_csv=trace_points,
            context_trace_points_csv=context_trace_points,
            output_dir=tmp_path / "overlay",
            title="Invalid non-packet context trace",
        )


def test_blind_overlay_rejects_packet_row_id_not_projected_from_sources(
    tmp_path: Path,
) -> None:
    review_packet = tmp_path / "packet.csv"
    trace_points = tmp_path / "trace_points.csv"
    bundle = write_overlay_lineage_bundle(
        review_packet_csv=review_packet,
        packet_rows=[_packet_row()],
        trace_points_csv=trace_points,
        trace_point_rows=_trace_points(),
    )
    rows = read_delimited_rows(
        review_packet,
        required_columns=packet.PACKET_COLUMNS,
        exact_columns=packet.PACKET_COLUMNS,
        delimiter=",",
    )
    rows[0]["packet_row_id"] = "unit_packet|Case 07|cand-supported"
    write_delimited_rows(review_packet, rows, packet.PACKET_COLUMNS)
    packet_manifest_path = bundle["review_packet_manifest_json"]
    assert isinstance(packet_manifest_path, Path)
    packet_manifest = json.loads(packet_manifest_path.read_text(encoding="utf-8"))
    packet_manifest["outputs"]["packet_csv_sha256"] = file_sha256(
        review_packet
    ).lower()
    packet_manifest_path.write_text(
        json.dumps(packet_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="deterministic projection"):
        overlay.run_blind_overlay(
            review_packet_csv=review_packet,
            trace_points_csv=trace_points,
            output_dir=tmp_path / "overlay",
            title="Forged packet row identity",
        )


def test_blind_overlay_case_label_is_presentation_order_not_join_identity(
    tmp_path: Path,
) -> None:
    review_packet = tmp_path / "packet.csv"
    trace_points = tmp_path / "trace_points.csv"
    candidate_id = "Case 07"
    write_overlay_lineage_bundle(
        review_packet_csv=review_packet,
        packet_rows=[_packet_row(candidate_id=candidate_id)],
        trace_points_csv=trace_points,
        trace_point_rows=_trace_points(candidate_id),
    )

    outputs = overlay.run_blind_overlay(
        review_packet_csv=review_packet,
        trace_points_csv=trace_points,
        output_dir=tmp_path / "overlay",
        title="Unit Blind Overlay",
    )

    html = outputs["gallery_html"].read_text(encoding="utf-8")
    assert "<h2>案例 Case 01</h2>" in html
    assert "<h2>案例 Case 07</h2>" not in html


def test_blind_overlay_rejects_current_full_packet_without_ambiguity_columns(
    tmp_path: Path,
) -> None:
    review_packet = tmp_path / "packet.csv"
    trace_points = tmp_path / "trace_points.csv"
    incomplete_columns = tuple(
        column
        for column in packet.PACKET_COLUMNS
        if column
        not in ({
            "trace_ambiguity_status",
            "trace_ambiguity_flags",
            "shifted_only_position_labels",
        } | set(evidence.ANCHOR_SEGMENTATION_SUMMARY_COLUMNS))
    )
    incomplete_row = {
        column: value
        for column, value in _packet_row().items()
        if column in incomplete_columns
    }
    incomplete_row["schema_version"] = packet.SCHEMA_VERSION
    bundle = write_overlay_lineage_bundle(
        review_packet_csv=review_packet,
        packet_rows=[incomplete_row],
        trace_points_csv=trace_points,
        trace_point_rows=_trace_points(),
    )
    write_delimited_rows(review_packet, [incomplete_row], incomplete_columns)
    packet_manifest_path = bundle["review_packet_manifest_json"]
    packet_manifest = json.loads(packet_manifest_path.read_text(encoding="utf-8"))
    packet_manifest["outputs"]["packet_csv_sha256"] = file_sha256(
        review_packet
    ).lower()
    packet_manifest_path.write_text(
        json.dumps(packet_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="current schema columns mismatch"):
        overlay.run_blind_overlay(
            review_packet_csv=review_packet,
            trace_points_csv=trace_points,
            output_dir=tmp_path / "overlay",
            title="Unit Blind Overlay",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("min_pearson_r_to_base", "0.94"),
        ("combined_pt_pattern_status", "insufficient_non_base_support"),
    ],
)
def test_blind_overlay_rejects_packet_not_projected_from_recorded_sources(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    review_packet = tmp_path / "packet.csv"
    trace_points = tmp_path / "trace_points.csv"
    bundle = write_overlay_lineage_bundle(
        review_packet_csv=review_packet,
        packet_rows=[_packet_row()],
        trace_points_csv=trace_points,
        trace_point_rows=_trace_points(),
    )
    packet_rows = read_delimited_rows(
        review_packet,
        required_columns=packet.PACKET_COLUMNS,
        exact_columns=packet.PACKET_COLUMNS,
        delimiter=",",
    )
    packet_rows[0][field] = value
    write_delimited_rows(review_packet, packet_rows, packet.PACKET_COLUMNS)
    packet_manifest_path = bundle["review_packet_manifest_json"]
    assert isinstance(packet_manifest_path, Path)
    packet_manifest = json.loads(packet_manifest_path.read_text(encoding="utf-8"))
    packet_manifest["outputs"]["packet_csv_sha256"] = file_sha256(
        review_packet
    ).lower()
    packet_manifest_path.write_text(
        json.dumps(packet_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="deterministic projection"):
        overlay.run_blind_overlay(
            review_packet_csv=review_packet,
            trace_points_csv=trace_points,
            output_dir=tmp_path / "overlay",
            title="Tampered packet projection",
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("readiness_tier", "production_ready", "readiness_tier mismatch"),
        ("evidence_owner", "matrix", "evidence_owner mismatch"),
        ("oracle_status", "manual_oracle_promoted", "oracle_status mismatch"),
    ],
)
def test_blind_overlay_rejects_packet_manifest_envelope_mutation(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    review_packet = tmp_path / "packet.csv"
    trace_points = tmp_path / "trace_points.csv"
    bundle = write_overlay_lineage_bundle(
        review_packet_csv=review_packet,
        packet_rows=[_packet_row()],
        trace_points_csv=trace_points,
        trace_point_rows=_trace_points(),
    )
    packet_manifest_path = bundle["review_packet_manifest_json"]
    assert isinstance(packet_manifest_path, Path)
    packet_manifest = json.loads(packet_manifest_path.read_text(encoding="utf-8"))
    packet_manifest[field] = value
    packet_manifest_path.write_text(
        json.dumps(packet_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=message):
        overlay.run_blind_overlay(
            review_packet_csv=review_packet,
            trace_points_csv=trace_points,
            output_dir=tmp_path / "overlay",
            title="Tampered packet manifest envelope",
        )


def test_blind_overlay_rejects_packet_source_hash_not_bound_to_trace_manifest(
    tmp_path: Path,
) -> None:
    review_packet = tmp_path / "packet.csv"
    trace_points = tmp_path / "trace_points.csv"
    bundle = write_overlay_lineage_bundle(
        review_packet_csv=review_packet,
        packet_rows=[_packet_row()],
        trace_points_csv=trace_points,
        trace_point_rows=_trace_points(),
    )
    packet_manifest_path = bundle["review_packet_manifest_json"]
    assert isinstance(packet_manifest_path, Path)
    packet_manifest = json.loads(packet_manifest_path.read_text(encoding="utf-8"))
    packet_manifest["input_sha256"]["trace_summary_csv"] = "f" * 64
    packet_manifest_path.write_text(
        json.dumps(packet_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="differs from trace manifest"):
        overlay.run_blind_overlay(
            review_packet_csv=review_packet,
            trace_points_csv=trace_points,
            output_dir=tmp_path / "overlay",
            title="Tampered packet source hash",
        )


@pytest.mark.parametrize("mutation", ["unknown_position", "extra_candidate"])
def test_blind_overlay_rejects_invalid_local_trace_bundle(
    tmp_path: Path,
    mutation: str,
) -> None:
    review_packet = tmp_path / "packet.csv"
    trace_points = tmp_path / "trace_points.csv"
    rows = _trace_points()
    if mutation == "unknown_position":
        rows[-1]["position_label"] = "pt_plus_3"
    else:
        rows.extend(_trace_points("extra-candidate"))
    write_overlay_lineage_bundle(
        review_packet_csv=review_packet,
        packet_rows=[_packet_row()],
        trace_points_csv=trace_points,
        trace_point_rows=rows,
    )

    with pytest.raises(
        ValueError,
        match=(
            "outside review packet|outside trace summary|unsupported position_label|"
            "candidate assignments do not match summary rows|outside metrics|"
            "candidate assignments do not match the recorded candidate slice"
        ),
    ):
        overlay.run_blind_overlay(
            review_packet_csv=review_packet,
            trace_points_csv=trace_points,
            output_dir=tmp_path / "overlay",
            title="Invalid trace bundle",
        )


@pytest.mark.parametrize("missing_position", [None, "pt_plus_4"])
def test_blind_overlay_renders_non_accept_candidates_with_empty_position_traces(
    tmp_path: Path,
    missing_position: str | None,
) -> None:
    review_packet = tmp_path / "packet.csv"
    trace_points = tmp_path / "trace_points.csv"
    rows = (
        []
        if missing_position is None
        else [
            row
            for row in _trace_points()
            if row["position_label"] != missing_position
        ]
    )
    write_overlay_lineage_bundle(
        review_packet_csv=review_packet,
        packet_rows=[
            _packet_row(status="pt_pattern_candidate_incomplete")
        ],
        trace_points_csv=trace_points,
        trace_point_rows=rows,
    )

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
    rows = [dict(row) | {"sample_stem": "WrongSample"} for row in _trace_points()]
    write_overlay_lineage_bundle(
        review_packet_csv=review_packet,
        packet_rows=[_packet_row()],
        trace_points_csv=trace_points,
        trace_point_rows=rows,
    )

    with pytest.raises(ValueError, match="sample_stem (does not match|mismatch)"):
        overlay.run_blind_overlay(
            review_packet_csv=review_packet,
            trace_points_csv=trace_points,
            output_dir=tmp_path / "overlay",
            title="Mismatched sample",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rt_min", ""),
        ("rt_min", "nan"),
        ("rt_min", "-1"),
        ("intensity", ""),
        ("intensity", "inf"),
        ("intensity", "-1"),
    ],
)
def test_blind_overlay_rejects_invalid_trace_point_values(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    review_packet = tmp_path / "packet.csv"
    trace_points = tmp_path / "trace_points.csv"
    rows = _trace_points()
    rows[0][field] = value
    write_overlay_lineage_bundle(
        review_packet_csv=review_packet,
        packet_rows=[_packet_row()],
        trace_points_csv=trace_points,
        trace_point_rows=rows,
    )

    with pytest.raises(ValueError, match="finite non-negative"):
        overlay.run_blind_overlay(
            review_packet_csv=review_packet,
            trace_points_csv=trace_points,
            output_dir=tmp_path / "overlay",
            title="Invalid trace point",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("raw_height_ratio_to_base", "0.123"),
        ("height_ratio_to_base", "0.456"),
        ("apex_rt_min", "9.99"),
    ],
)
def test_blind_overlay_rejects_point_derived_metric_tamper_with_refreshed_hashes(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    review_packet = tmp_path / "packet.csv"
    trace_points = tmp_path / "trace_points.csv"
    bundle = write_overlay_lineage_bundle(
        review_packet_csv=review_packet,
        packet_rows=[_packet_row()],
        trace_points_csv=trace_points,
        trace_point_rows=_trace_points(),
    )
    metrics_csv = tmp_path / "pt_pattern_trace_metrics.csv"
    metric_rows = read_delimited_rows(
        metrics_csv,
        required_columns=trace_batch.ANCHOR_LOCAL_METRIC_COLUMNS,
        exact_columns=trace_batch.ANCHOR_LOCAL_METRIC_COLUMNS,
        delimiter=",",
    )
    plus1 = next(row for row in metric_rows if row["position_label"] == "pt_plus_1")
    plus1[field] = value
    write_delimited_rows(
        metrics_csv,
        metric_rows,
        trace_batch.ANCHOR_LOCAL_METRIC_COLUMNS,
    )
    _refresh_primary_trace_bindings(bundle, trace_metrics_csv=metrics_csv)

    with pytest.raises(
        ValueError,
        match=(
            "deterministic projection of trace points|"
            "does not match raw trace points"
        ),
    ):
        overlay.run_blind_overlay(
            review_packet_csv=review_packet,
            trace_points_csv=trace_points,
            output_dir=tmp_path / "overlay",
            title="Point-derived metric tamper",
        )


def test_blind_overlay_rejects_target_mz_rebinding_with_refreshed_hashes(
    tmp_path: Path,
) -> None:
    review_packet = tmp_path / "packet.csv"
    trace_points = tmp_path / "trace_points.csv"
    bundle = write_overlay_lineage_bundle(
        review_packet_csv=review_packet,
        packet_rows=[_packet_row()],
        trace_points_csv=trace_points,
        trace_point_rows=_trace_points(),
    )
    metrics_csv = tmp_path / "pt_pattern_trace_metrics.csv"
    metric_rows = read_delimited_rows(
        metrics_csv,
        required_columns=trace_batch.ANCHOR_LOCAL_METRIC_COLUMNS,
        exact_columns=trace_batch.ANCHOR_LOCAL_METRIC_COLUMNS,
        delimiter=",",
    )
    plus1 = next(row for row in metric_rows if row["position_label"] == "pt_plus_1")
    plus1["target_mz"] = "777.123"
    write_delimited_rows(
        metrics_csv,
        metric_rows,
        trace_batch.ANCHOR_LOCAL_METRIC_COLUMNS,
    )
    _refresh_primary_trace_bindings(bundle, trace_metrics_csv=metrics_csv)

    with pytest.raises(
        ValueError,
        match="target_mz is not the deterministic projection",
    ):
        overlay.run_blind_overlay(
            review_packet_csv=review_packet,
            trace_points_csv=trace_points,
            output_dir=tmp_path / "overlay",
            title="Rebound target m/z",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [("base_mz", "501"), ("base_rt_apex_min", "10.5")],
)
def test_blind_overlay_rejects_summary_identity_rebinding_with_refreshed_hashes(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    review_packet = tmp_path / "packet.csv"
    trace_points = tmp_path / "trace_points.csv"
    bundle = write_overlay_lineage_bundle(
        review_packet_csv=review_packet,
        packet_rows=[_packet_row()],
        trace_points_csv=trace_points,
        trace_point_rows=_trace_points(),
    )
    summary_csv = tmp_path / "pt_pattern_trace_summary.csv"
    summary_rows = read_delimited_rows(
        summary_csv,
        required_columns=trace_batch.ANCHOR_LOCAL_SUMMARY_COLUMNS,
        exact_columns=trace_batch.ANCHOR_LOCAL_SUMMARY_COLUMNS,
        delimiter=",",
    )
    summary_rows[0][field] = value
    write_delimited_rows(
        summary_csv,
        summary_rows,
        trace_batch.ANCHOR_LOCAL_SUMMARY_COLUMNS,
    )
    _refresh_primary_trace_bindings(bundle, trace_summary_csv=summary_csv)

    with pytest.raises(ValueError, match=rf"{field} does not match"):
        overlay.run_blind_overlay(
            review_packet_csv=review_packet,
            trace_points_csv=trace_points,
            output_dir=tmp_path / "overlay",
            title="Rebound summary identity",
        )


@pytest.mark.parametrize("mutation", ["intensity_basis", "unsorted_rt", "duplicate_rt"])
def test_blind_overlay_rejects_trace_point_structure_tamper_with_refreshed_hashes(
    tmp_path: Path,
    mutation: str,
) -> None:
    review_packet = tmp_path / "packet.csv"
    trace_points = tmp_path / "trace_points.csv"
    bundle = write_overlay_lineage_bundle(
        review_packet_csv=review_packet,
        packet_rows=[_packet_row()],
        trace_points_csv=trace_points,
        trace_point_rows=_trace_points(),
    )
    point_rows = read_delimited_rows(
        trace_points,
        required_columns=evidence.TRACE_POINT_COLUMNS,
        exact_columns=evidence.TRACE_POINT_COLUMNS,
        delimiter=",",
    )
    if mutation == "intensity_basis":
        point_rows[0]["intensity_basis"] = "decision_trace"
    elif mutation == "unsorted_rt":
        point_rows[0], point_rows[1] = point_rows[1], point_rows[0]
    else:
        point_rows[1]["rt_min"] = point_rows[0]["rt_min"]
    write_delimited_rows(trace_points, point_rows, evidence.TRACE_POINT_COLUMNS)
    _refresh_primary_trace_bindings(bundle, trace_points_csv=trace_points)

    with pytest.raises(
        ValueError,
        match="intensity_basis must be|strictly increasing and unique",
    ):
        overlay.run_blind_overlay(
            review_packet_csv=review_packet,
            trace_points_csv=trace_points,
            output_dir=tmp_path / "overlay",
            title="Trace point structure tamper",
        )


def test_blind_overlay_rejects_swapped_primary_and_context_manifests(
    tmp_path: Path,
) -> None:
    review_packet = tmp_path / "packet.csv"
    primary = tmp_path / "anchor.csv"
    context = tmp_path / "full.csv"
    paths = write_overlay_lineage_bundle(
        review_packet_csv=review_packet,
        packet_rows=[_packet_row()],
        trace_points_csv=primary,
        trace_point_rows=_trace_points(),
        context_trace_points_csv=context,
        context_trace_point_rows=_trace_points(),
    )

    with pytest.raises(ValueError, match="trace_manifest_json sha256 mismatch"):
        overlay.run_blind_overlay(
            review_packet_csv=review_packet,
            trace_points_csv=primary,
            trace_manifest_json=paths["context_trace_manifest_json"],
            context_trace_points_csv=context,
            context_trace_manifest_json=paths["trace_manifest_json"],
            output_dir=tmp_path / "overlay",
            title="Swapped manifests",
        )


def test_blind_overlay_accepts_context_trace_bundle_with_missing_position(
    tmp_path: Path,
) -> None:
    review_packet = tmp_path / "packet.csv"
    local_trace_points = tmp_path / "local.csv"
    context_trace_points = tmp_path / "context.csv"
    write_overlay_lineage_bundle(
        review_packet_csv=review_packet,
        packet_rows=[_packet_row()],
        trace_points_csv=local_trace_points,
        trace_point_rows=_trace_points(),
        context_trace_points_csv=context_trace_points,
        context_trace_point_rows=[
            row for row in _trace_points() if row["position_label"] != "base"
        ],
    )

    outputs = overlay.run_blind_overlay(
        review_packet_csv=review_packet,
        trace_points_csv=local_trace_points,
        context_trace_points_csv=context_trace_points,
        output_dir=tmp_path / "overlay",
        title="Incomplete context",
    )

    assert outputs["gallery_html"].is_file()


def test_blind_overlay_rejects_context_point_tamper_with_refreshed_manifest_hash(
    tmp_path: Path,
) -> None:
    review_packet = tmp_path / "packet.csv"
    local_trace_points = tmp_path / "local.csv"
    context_trace_points = tmp_path / "context.csv"
    bundle = write_overlay_lineage_bundle(
        review_packet_csv=review_packet,
        packet_rows=[_packet_row()],
        trace_points_csv=local_trace_points,
        trace_point_rows=_trace_points(),
        context_trace_points_csv=context_trace_points,
        context_trace_point_rows=_trace_points(),
    )
    context_rows = read_delimited_rows(
        context_trace_points,
        required_columns=evidence.TRACE_POINT_COLUMNS,
        exact_columns=evidence.TRACE_POINT_COLUMNS,
        delimiter=",",
    )
    context_rows[0]["intensity"] = "999"
    write_delimited_rows(
        context_trace_points,
        context_rows,
        evidence.TRACE_POINT_COLUMNS,
    )
    context_manifest_path = bundle["context_trace_manifest_json"]
    assert isinstance(context_manifest_path, Path)
    context_manifest = json.loads(
        context_manifest_path.read_text(encoding="utf-8")
    )
    context_manifest["output_sha256"]["trace_points_csv"] = file_sha256(
        context_trace_points
    ).lower()
    context_manifest_path.write_text(
        json.dumps(context_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match=(
            "deterministic projection of trace points|"
            "does not match raw trace points"
        ),
    ):
        overlay.run_blind_overlay(
            review_packet_csv=review_packet,
            trace_points_csv=local_trace_points,
            context_trace_points_csv=context_trace_points,
            output_dir=tmp_path / "overlay",
            title="Tampered full-context trace",
        )


def test_blind_overlay_rejects_context_target_rebinding_with_refreshed_hash(
    tmp_path: Path,
) -> None:
    review_packet = tmp_path / "packet.csv"
    local_trace_points = tmp_path / "local.csv"
    context_trace_points = tmp_path / "context.csv"
    bundle = write_overlay_lineage_bundle(
        review_packet_csv=review_packet,
        packet_rows=[_packet_row()],
        trace_points_csv=local_trace_points,
        trace_point_rows=_trace_points(),
        context_trace_points_csv=context_trace_points,
        context_trace_point_rows=_trace_points(),
    )
    context_metrics = tmp_path / "pt_pattern_context_trace_metrics.csv"
    metric_rows = read_delimited_rows(
        context_metrics,
        required_columns=evidence.METRIC_COLUMNS,
        exact_columns=evidence.METRIC_COLUMNS,
        delimiter=",",
    )
    plus4 = next(row for row in metric_rows if row["position_label"] == "pt_plus_4")
    plus4["target_mz"] = "888.456"
    write_delimited_rows(context_metrics, metric_rows, evidence.METRIC_COLUMNS)
    context_manifest_path = bundle["context_trace_manifest_json"]
    assert isinstance(context_manifest_path, Path)
    context_manifest = json.loads(
        context_manifest_path.read_text(encoding="utf-8")
    )
    context_manifest["output_sha256"]["metrics_csv"] = file_sha256(
        context_metrics
    ).lower()
    context_manifest_path.write_text(
        json.dumps(context_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match=(
            "do not use the same candidate/position target_mz|"
            "target_mz is not the deterministic projection"
        ),
    ):
        overlay.run_blind_overlay(
            review_packet_csv=review_packet,
            trace_points_csv=local_trace_points,
            context_trace_points_csv=context_trace_points,
            output_dir=tmp_path / "overlay",
            title="Rebound full-context target",
        )


def test_blind_overlay_rejects_accept_packet_with_empty_local_bundle(
    tmp_path: Path,
) -> None:
    review_packet = tmp_path / "packet.csv"
    local_trace_points = tmp_path / "local.csv"
    context_trace_points = tmp_path / "context.csv"
    bundle = write_overlay_lineage_bundle(
        review_packet_csv=review_packet,
        packet_rows=[_packet_row()],
        trace_points_csv=local_trace_points,
        trace_point_rows=_trace_points(),
        context_trace_points_csv=context_trace_points,
        context_trace_point_rows=_trace_points(),
    )
    write_delimited_rows(local_trace_points, [], evidence.TRACE_POINT_COLUMNS)
    trace_manifest_path = bundle["trace_manifest_json"]
    assert isinstance(trace_manifest_path, Path)
    trace_manifest = json.loads(trace_manifest_path.read_text(encoding="utf-8"))
    trace_manifest["output_sha256"]["trace_points_csv"] = file_sha256(
        local_trace_points
    ).lower()
    trace_manifest_path.write_text(
        json.dumps(trace_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    packet_manifest_path = bundle["review_packet_manifest_json"]
    assert isinstance(packet_manifest_path, Path)
    packet_manifest = json.loads(packet_manifest_path.read_text(encoding="utf-8"))
    packet_manifest["input_sha256"]["trace_manifest_json"] = file_sha256(
        trace_manifest_path
    ).lower()
    packet_manifest_path.write_text(
        json.dumps(packet_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="row count does not match|requires trace points for every Pt position",
    ):
        overlay.run_blind_overlay(
            review_packet_csv=review_packet,
            trace_points_csv=local_trace_points,
            context_trace_points_csv=context_trace_points,
            output_dir=tmp_path / "overlay",
            title="Empty local trace bundle",
        )


def test_blind_overlay_rejects_unknown_packet_schema(tmp_path: Path) -> None:
    review_packet = tmp_path / "packet.csv"
    trace_points = tmp_path / "trace_points.csv"
    row = _packet_row()
    row["schema_version"] = "unsupported_pt_pattern_manual_review_packet"
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

    with pytest.raises(ValueError, match="current schema columns mismatch"):
        overlay.run_blind_overlay(
            review_packet_csv=review_packet,
            trace_points_csv=trace_points,
            output_dir=tmp_path / "overlay",
            title="Partial schema",
        )


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
    context_rows = _trace_points()
    context_rows.append(
        {
            "candidate_id": "cand-supported",
            "position_label": "base",
            "rt_min": "10.4",
            "intensity": "250",
        }
    )
    write_overlay_lineage_bundle(
        review_packet_csv=review_packet,
        packet_rows=[_packet_row()],
        trace_points_csv=local_trace_points,
        trace_point_rows=_trace_points(),
        context_trace_points_csv=context_trace_points,
        context_trace_point_rows=context_rows,
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
    assert html.count("10.400 min") == 1
    assert "+1 apex = 1" in html
    assert "背景圖與峰型圖採各自最大值正規化" in html
    assert "overflow-wrap: anywhere" in html
    assert "box-sizing: border-box" in html

    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    assert summary["gallery_mode"] == "anchor_local_with_full_context"
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
