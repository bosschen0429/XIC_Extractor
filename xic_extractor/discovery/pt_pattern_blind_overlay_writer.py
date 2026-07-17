"""I/O boundary for the Pt blind-review overlay artifacts."""

from __future__ import annotations

import json
from pathlib import Path

from xic_extractor.discovery.pt_pattern_blind_overlay_call import (
    ANCHOR_LOCAL_SCHEMA_VERSION,
    LEGACY_MACHINE_CALL_COLUMNS,
    MACHINE_CALL_COLUMNS,
    PACKET_REQUIRED_COLUMNS,
    SCHEMA_VERSION,
    SMOOTH_METHOD,
    SMOOTH_WINDOW_POINTS,
    machine_call_row,
    validate_packet_rows,
)
from xic_extractor.discovery.pt_pattern_review_packet import (
    EVIDENCE_OWNER,
    READINESS_TIER,
)
from xic_extractor.discovery.pt_pattern_trace_evidence import TRACE_DECISION_BASIS
from xic_extractor.tabular_io import read_delimited_rows, write_delimited_rows

TRACE_POINT_REQUIRED_COLUMNS = (
    "candidate_id",
    "position_label",
    "rt_min",
    "intensity",
)


def run_blind_overlay(
    *,
    review_packet_csv: Path,
    trace_points_csv: Path,
    output_dir: Path,
    title: str,
    context_trace_points_csv: Path | None = None,
) -> dict[str, Path]:
    # Lazy import keeps the renderer independent from file-system ownership.
    from xic_extractor.discovery.pt_pattern_blind_overlay import (
        VISUAL_TIME_SMOOTH_WINDOW_SEC,
        _load_traces_by_candidate,
        render_blind_overlay_html,
    )

    packet_rows = read_delimited_rows(
        review_packet_csv,
        required_columns=PACKET_REQUIRED_COLUMNS,
        delimiter=",",
        encoding="utf-8-sig",
    )
    validate_packet_rows(packet_rows)
    trace_points = read_delimited_rows(
        trace_points_csv,
        required_columns=TRACE_POINT_REQUIRED_COLUMNS,
        delimiter=",",
        encoding="utf-8-sig",
    )
    traces_by_candidate = _load_traces_by_candidate(trace_points)
    context_traces_by_candidate = traces_by_candidate
    if context_trace_points_csv is not None:
        context_trace_points = read_delimited_rows(
            context_trace_points_csv,
            required_columns=TRACE_POINT_REQUIRED_COLUMNS,
            delimiter=",",
            encoding="utf-8-sig",
        )
        context_traces_by_candidate = _load_traces_by_candidate(context_trace_points)
    machine_rows = tuple(machine_call_row(row) for row in packet_rows)

    output_dir.mkdir(parents=True, exist_ok=True)
    html_path = output_dir / "pt_pattern_blind_overlay_gallery.html"
    machine_calls_csv = output_dir / "pt_pattern_machine_calls_sealed.csv"
    summary_json = output_dir / "pt_pattern_blind_overlay_summary.json"

    html_path.write_text(
        render_blind_overlay_html(
            packet_rows=packet_rows,
            traces_by_candidate=traces_by_candidate,
            title=title,
            context_traces_by_candidate=context_traces_by_candidate,
            has_separate_context=context_trace_points_csv is not None,
        ),
        encoding="utf-8",
    )
    machine_schema_version = (
        str(machine_rows[0]["schema_version"])
        if machine_rows
        else SCHEMA_VERSION
    )
    machine_columns = (
        MACHINE_CALL_COLUMNS
        if machine_schema_version == ANCHOR_LOCAL_SCHEMA_VERSION
        else LEGACY_MACHINE_CALL_COLUMNS
    )
    write_delimited_rows(machine_calls_csv, machine_rows, machine_columns)

    summary = {
        "schema_version": (
            ANCHOR_LOCAL_SCHEMA_VERSION
            if context_trace_points_csv is not None
            or machine_schema_version == ANCHOR_LOCAL_SCHEMA_VERSION
            else SCHEMA_VERSION
        ),
        "readiness_tier": READINESS_TIER,
        "evidence_owner": EVIDENCE_OWNER,
        "review_packet_csv": str(review_packet_csv),
        "trace_points_csv": str(trace_points_csv),
        "output_dir": str(output_dir),
        "case_count": len(packet_rows),
        "context_trace_points_csv": (
            str(context_trace_points_csv)
            if context_trace_points_csv is not None
            else None
        ),
        "smooth_method": SMOOTH_METHOD,
        "smooth_window_points": SMOOTH_WINDOW_POINTS,
        "time_smooth_window_sec": VISUAL_TIME_SMOOTH_WINDOW_SEC,
        "trace_decision_basis": TRACE_DECISION_BASIS,
        "gallery_mode": (
            "anchor_local_semantic"
            if context_trace_points_csv is not None
            else "legacy_trace_modes"
        ),
        "smooth_applies_to": (
            "sealed_machine_calls_use_packet_decision_metrics;"
            "gallery_html_shows_full_context_point_smoothed;"
            "anchor_local_point_smoothed_own_max;"
            "anchor_local_point_smoothed_plus1_shared_scale"
            if context_trace_points_csv is not None
            else "sealed_machine_calls_use_packet_decision_metrics;"
            "gallery_html_shows_raw_point_smoothed_time_smoothed"
        ),
        "sealed_machine_calls_written": True,
        "blind_to_machine_calls_in_html": True,
        "oracle_status": "pending_human_review",
        "diagnostic_only": True,
        "outputs": {
            "gallery_html": str(html_path),
            "machine_calls_csv": str(machine_calls_csv),
        },
    }
    summary_json.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "gallery_html": html_path,
        "machine_calls_csv": machine_calls_csv,
        "summary_json": summary_json,
    }
