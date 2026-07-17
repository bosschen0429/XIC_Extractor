"""I/O boundary for the Pt blind-review overlay artifacts."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path

from xic_extractor.discovery.pt_pattern_blind_overlay_call import (
    ANCHOR_LOCAL_SCHEMA_VERSION,
    LEGACY_MACHINE_CALL_COLUMNS,
    LEGACY_PACKET_REQUIRED_COLUMNS,
    MACHINE_CALL_COLUMNS,
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
from xic_extractor.discovery.pt_pattern_trace_evidence import (
    POSITION_LABELS as TRACE_POSITION_LABELS,
)
from xic_extractor.discovery.pt_pattern_trace_evidence import TRACE_DECISION_BASIS
from xic_extractor.tabular_io import (
    file_sha256,
    optional_float,
    read_delimited_rows,
    write_delimited_rows,
)

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
        required_columns=LEGACY_PACKET_REQUIRED_COLUMNS,
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
    _validate_trace_bundle_rows(
        packet_rows=packet_rows,
        trace_rows=trace_points,
        label="trace-points",
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
        _validate_trace_bundle_rows(
            packet_rows=packet_rows,
            trace_rows=context_trace_points,
            label="context trace-points",
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

    review_packet_sha256 = file_sha256(review_packet_csv)
    trace_points_sha256 = file_sha256(trace_points_csv)
    context_trace_points_sha256 = (
        file_sha256(context_trace_points_csv)
        if context_trace_points_csv is not None
        else ""
    )
    gallery_html_sha256 = file_sha256(html_path)
    machine_calls_sha256 = file_sha256(machine_calls_csv)

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
        "review_packet_csv_sha256": review_packet_sha256,
        "trace_points_csv": str(trace_points_csv),
        "trace_points_csv_sha256": trace_points_sha256,
        "output_dir": str(output_dir),
        "case_count": len(packet_rows),
        "context_trace_points_csv": (
            str(context_trace_points_csv)
            if context_trace_points_csv is not None
            else None
        ),
        "context_trace_points_csv_sha256": context_trace_points_sha256,
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
            "gallery_html_sha256": gallery_html_sha256,
            "machine_calls_csv": str(machine_calls_csv),
            "machine_calls_csv_sha256": machine_calls_sha256,
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


def _validate_trace_bundle_rows(
    *,
    packet_rows: Sequence[Mapping[str, str]],
    trace_rows: Sequence[Mapping[str, str]],
    label: str,
) -> None:
    if not packet_rows:
        raise ValueError("review packet must contain at least one row")
    if not trace_rows:
        raise ValueError(f"{label} must contain at least one row")

    sample_by_candidate: dict[str, str] = {}
    for packet_row in packet_rows:
        candidate_id = str(packet_row.get("candidate_id", "")).strip()
        sample_stem = str(packet_row.get("sample_stem", "")).strip()
        if not candidate_id or not sample_stem:
            raise ValueError("review packet candidate_id/sample_stem must be non-empty")
        if candidate_id in sample_by_candidate:
            raise ValueError(f"duplicate review packet candidate_id: {candidate_id}")
        sample_by_candidate[candidate_id] = sample_stem

    positions_by_candidate: dict[str, set[str]] = {}
    for row_index, trace_row in enumerate(trace_rows, start=2):
        candidate_id = str(trace_row.get("candidate_id", "")).strip()
        position_label = str(trace_row.get("position_label", "")).strip()
        if not candidate_id or not position_label:
            raise ValueError(
                f"{label} row {row_index}: "
                "candidate_id/position_label must be non-empty"
            )
        rt_value = optional_float(trace_row.get("rt_min"))
        intensity = optional_float(trace_row.get("intensity"))
        if (
            rt_value is None
            or intensity is None
            or not math.isfinite(rt_value)
            or not math.isfinite(intensity)
        ):
            raise ValueError(
                f"{label} row {row_index}: rt_min/intensity must be finite numbers"
            )
        expected_sample = sample_by_candidate.get(candidate_id)
        if "sample_stem" in trace_row:
            observed_sample = str(trace_row.get("sample_stem", "")).strip()
            if observed_sample != expected_sample:
                raise ValueError(
                    f"{label} row {row_index}: sample_stem does not match review "
                    f"packet for {candidate_id}"
                )
        positions_by_candidate.setdefault(candidate_id, set()).add(position_label)

    packet_candidate_ids = set(sample_by_candidate)
    trace_candidate_ids = set(positions_by_candidate)
    if trace_candidate_ids != packet_candidate_ids:
        missing = sorted(packet_candidate_ids - trace_candidate_ids)
        unexpected = sorted(trace_candidate_ids - packet_candidate_ids)
        details: list[str] = []
        if missing:
            details.append("missing=" + ",".join(missing[:5]))
        if unexpected:
            details.append("unexpected=" + ",".join(unexpected[:5]))
        raise ValueError(
            f"{label} candidate_id set must exactly match review packet: "
            + "; ".join(details)
        )

    expected_positions = set(TRACE_POSITION_LABELS)
    for candidate_id in sorted(packet_candidate_ids):
        positions = positions_by_candidate[candidate_id]
        if positions == expected_positions:
            continue
        missing = sorted(expected_positions - positions)
        unexpected = sorted(positions - expected_positions)
        details = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if unexpected:
            details.append("unexpected=" + ",".join(unexpected))
        raise ValueError(
            f"{label} {candidate_id}: position_label set must be exactly "
            f"{','.join(TRACE_POSITION_LABELS)}: " + "; ".join(details)
        )
