"""I/O boundary for the Pt blind-review overlay artifacts."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from xic_extractor.discovery.pt_pattern_artifact_contract import (
    PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE,
    PT_PATTERN_TRACE_FULL_CONTEXT_ROLE,
)
from xic_extractor.discovery.pt_pattern_blind_overlay_call import (
    ANCHOR_LOCAL_SCHEMA_VERSION,
    LEGACY_MACHINE_CALL_COLUMNS,
    MACHINE_CALL_COLUMNS,
    SMOOTH_METHOD,
    SMOOTH_WINDOW_POINTS,
    machine_accept_candidate_ids,
    machine_call_row,
    validate_machine_call_rows,
)
from xic_extractor.discovery.pt_pattern_review_bundle import (
    BLIND_OVERLAY_SUMMARY_FILE_NAME,
    load_verified_review_packet_bundle,
)
from xic_extractor.discovery.pt_pattern_review_packet import (
    EVIDENCE_OWNER,
    READINESS_TIER,
)
from xic_extractor.discovery.pt_pattern_trace_evidence import (
    POSITION_LABELS as TRACE_POSITION_LABELS,
)
from xic_extractor.discovery.pt_pattern_trace_evidence import (
    TRACE_DECISION_BASIS,
    TRACE_POINT_COLUMNS,
)
from xic_extractor.discovery.pt_pattern_trace_manifest import (
    default_trace_manifest_path,
    default_trace_summary_path,
    load_trace_bundle_manifest,
    load_verified_trace_summary_bundle,
    validate_cross_role_trace_request_identity,
    validate_trace_points_manifest,
)
from xic_extractor.tabular_io import (
    file_sha256,
    read_delimited_rows,
    write_delimited_rows,
)

TRACE_POINT_REQUIRED_COLUMNS = TRACE_POINT_COLUMNS


def run_blind_overlay(
    *,
    review_packet_csv: Path,
    trace_points_csv: Path,
    output_dir: Path,
    title: str,
    context_trace_points_csv: Path | None = None,
    review_packet_manifest_json: Path | None = None,
    trace_manifest_json: Path | None = None,
    context_trace_manifest_json: Path | None = None,
) -> dict[str, Path]:
    # Lazy import keeps the renderer independent from file-system ownership.
    from xic_extractor.discovery.pt_pattern_blind_overlay import (
        VISUAL_TIME_SMOOTH_WINDOW_SEC,
        _load_traces_by_candidate,
        render_blind_overlay_html,
    )

    review_bundle = load_verified_review_packet_bundle(
        review_packet_csv,
        review_packet_manifest_json=review_packet_manifest_json,
        trace_manifest_json=trace_manifest_json,
    )
    packet_rows = review_bundle.rows
    resolved_packet_manifest = review_bundle.review_packet_manifest_json
    packet_trace_extraction_sha256 = review_bundle.trace_extraction_sha256
    packet_artifact_role = review_bundle.trace_artifact_role
    resolved_trace_manifest = review_bundle.trace_manifest_json
    if packet_artifact_role == PT_PATTERN_TRACE_FULL_CONTEXT_ROLE:
        if (
            context_trace_points_csv is not None
            or context_trace_manifest_json is not None
        ):
            raise ValueError("full review packet must not provide context trace points")
    elif packet_artifact_role != PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE:
        raise ValueError(
            f"unsupported packet trace artifact role: {packet_artifact_role!r}"
        )
    if context_trace_manifest_json is not None and context_trace_points_csv is None:
        raise ValueError("context trace manifest requires context trace-points CSV")

    trace_points = read_delimited_rows(
        trace_points_csv,
        required_columns=TRACE_POINT_REQUIRED_COLUMNS,
        exact_columns=TRACE_POINT_REQUIRED_COLUMNS,
        delimiter=",",
        encoding="utf-8-sig",
    )
    packet_identities = {
        str(row["candidate_id"]): (
            str(row["candidate_assignment_sha256"]),
            str(row["sample_stem"]),
        )
        for row in packet_rows
    }
    primary_trace_manifest = load_trace_bundle_manifest(resolved_trace_manifest)
    primary_trace_extraction_sha256 = validate_trace_points_manifest(
        manifest=primary_trace_manifest,
        trace_points_csv=trace_points_csv,
        trace_point_rows=trace_points,
        expected_artifact_role=packet_artifact_role,
        expected_packet_identities=packet_identities,
        required_complete_candidate_ids=machine_accept_candidate_ids(packet_rows),
        expected_metric_rows=review_bundle.trace_summary_bundle.metric_rows,
    )
    if primary_trace_extraction_sha256 != packet_trace_extraction_sha256:
        raise ValueError("review packet and primary trace extraction mismatch")
    _validate_trace_bundle_rows(
        packet_rows=packet_rows,
        trace_rows=trace_points,
        label="trace-points",
    )
    traces_by_candidate = _load_traces_by_candidate(trace_points)
    context_traces_by_candidate = traces_by_candidate
    resolved_context_trace_manifest: Path | None = None
    if context_trace_points_csv is not None:
        if packet_artifact_role != PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE:
            raise ValueError("context trace points require an anchor-local packet")
        resolved_context_trace_manifest = (
            context_trace_manifest_json
            or default_trace_manifest_path(
                context_trace_points_csv,
                artifact_role=PT_PATTERN_TRACE_FULL_CONTEXT_ROLE,
            )
        )
        context_trace_points = read_delimited_rows(
            context_trace_points_csv,
            required_columns=TRACE_POINT_REQUIRED_COLUMNS,
            exact_columns=TRACE_POINT_REQUIRED_COLUMNS,
            delimiter=",",
            encoding="utf-8-sig",
        )
        context_trace_manifest = load_trace_bundle_manifest(
            resolved_context_trace_manifest
        )
        context_trace_bundle = load_verified_trace_summary_bundle(
            default_trace_summary_path(
                context_trace_points_csv,
                artifact_role=PT_PATTERN_TRACE_FULL_CONTEXT_ROLE,
            ),
            expected_artifact_role=PT_PATTERN_TRACE_FULL_CONTEXT_ROLE,
            manifest_json=resolved_context_trace_manifest,
        )
        validate_cross_role_trace_request_identity(
            review_bundle.trace_summary_bundle.metric_rows,
            context_trace_bundle.metric_rows,
        )
        context_trace_extraction_sha256 = validate_trace_points_manifest(
            manifest=context_trace_manifest,
            trace_points_csv=context_trace_points_csv,
            trace_point_rows=context_trace_points,
            expected_artifact_role=PT_PATTERN_TRACE_FULL_CONTEXT_ROLE,
            expected_packet_identities=packet_identities,
            expected_metric_rows=context_trace_bundle.metric_rows,
        )
        if context_trace_extraction_sha256 != packet_trace_extraction_sha256:
            raise ValueError("context and anchor trace extraction mismatch")
        _validate_trace_bundle_rows(
            packet_rows=packet_rows,
            trace_rows=context_trace_points,
            label="context trace-points",
        )
        context_traces_by_candidate = _load_traces_by_candidate(context_trace_points)
    machine_rows = tuple(machine_call_row(row) for row in packet_rows)
    validate_machine_call_rows(machine_rows)

    output_dir.mkdir(parents=True, exist_ok=True)
    html_path = output_dir / "pt_pattern_blind_overlay_gallery.html"
    machine_calls_csv = output_dir / "pt_pattern_machine_calls_sealed.csv"
    summary_json = output_dir / BLIND_OVERLAY_SUMMARY_FILE_NAME

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
    machine_schema_version = str(machine_rows[0]["schema_version"])
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
        "schema_version": machine_schema_version,
        "readiness_tier": READINESS_TIER,
        "evidence_owner": EVIDENCE_OWNER,
        "trace_artifact_role": packet_artifact_role,
        "trace_extraction_sha256": packet_trace_extraction_sha256,
        "review_packet_csv": str(review_packet_csv),
        "review_packet_csv_sha256": review_packet_sha256,
        "review_packet_manifest_json": str(resolved_packet_manifest),
        "review_packet_manifest_json_sha256": file_sha256(
            resolved_packet_manifest
        ).lower(),
        "trace_points_csv": str(trace_points_csv),
        "trace_points_csv_sha256": trace_points_sha256,
        "trace_manifest_json": str(resolved_trace_manifest),
        "trace_manifest_json_sha256": file_sha256(resolved_trace_manifest).lower(),
        "output_dir": str(output_dir),
        "case_count": len(packet_rows),
        "context_trace_points_csv": (
            str(context_trace_points_csv)
            if context_trace_points_csv is not None
            else None
        ),
        "context_trace_points_csv_sha256": context_trace_points_sha256,
        "context_trace_manifest_json": (
            str(resolved_context_trace_manifest)
            if resolved_context_trace_manifest is not None
            else None
        ),
        "context_trace_manifest_json_sha256": (
            file_sha256(resolved_context_trace_manifest).lower()
            if resolved_context_trace_manifest is not None
            else ""
        ),
        "smooth_method": SMOOTH_METHOD,
        "smooth_window_points": SMOOTH_WINDOW_POINTS,
        "time_smooth_window_sec": VISUAL_TIME_SMOOTH_WINDOW_SEC,
        "trace_decision_basis": TRACE_DECISION_BASIS,
        "gallery_mode": (
            "anchor_local_with_full_context"
            if context_trace_points_csv is not None
            else (
                "anchor_local_only"
                if packet_artifact_role == PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE
                else "full_context_only"
            )
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
        expected_sample = sample_by_candidate.get(candidate_id)
        if "sample_stem" in trace_row:
            observed_sample = str(trace_row.get("sample_stem", "")).strip()
            if expected_sample is not None and observed_sample != expected_sample:
                raise ValueError(
                    f"{label} row {row_index}: sample_stem does not match review "
                    f"packet for {candidate_id}"
                )
        positions_by_candidate.setdefault(candidate_id, set()).add(position_label)

    packet_candidate_ids = set(sample_by_candidate)
    trace_candidate_ids = set(positions_by_candidate)
    unexpected_candidates = sorted(trace_candidate_ids - packet_candidate_ids)
    if unexpected_candidates:
        raise ValueError(
            f"{label} candidate_id set contains rows outside review packet: "
            + ",".join(unexpected_candidates[:5])
        )

    expected_positions = set(TRACE_POSITION_LABELS)
    for candidate_id in sorted(trace_candidate_ids):
        positions = positions_by_candidate[candidate_id]
        unexpected = sorted(positions - expected_positions)
        if not unexpected:
            continue
        raise ValueError(
            f"{label} {candidate_id}: unsupported position_label values: "
            + ",".join(unexpected)
        )
