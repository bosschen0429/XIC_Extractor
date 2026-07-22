from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections.abc import Mapping, Sequence
from pathlib import Path

from xic_extractor.discovery import pt_pattern_review_packet as packet
from xic_extractor.discovery import pt_pattern_trace_batch as trace_batch
from xic_extractor.discovery import pt_pattern_trace_evidence as trace_evidence
from xic_extractor.discovery.pt_pattern_artifact_contract import (
    PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE,
    PT_PATTERN_TRACE_FULL_CONTEXT_ROLE,
    pt_pattern_candidate_assignment_fingerprint,
    pt_pattern_trace_extraction_fingerprint,
)
from xic_extractor.discovery.pt_pattern_trace_manifest import (
    default_trace_manifest_path,
)
from xic_extractor.tabular_io import (
    file_sha256,
    format_diagnostic_value,
    read_delimited_rows,
    write_delimited_rows,
)


def full_context_trace_summary_row(
    *,
    candidate_id: str,
    candidate_assignment_sha256: str,
    sample_stem: str = "SampleA",
    base_mz: str = "500.0",
    base_rt_apex_min: str = "10.0",
    **overrides: object,
) -> dict[str, str]:
    """Return one complete, current full-context trace summary fixture row."""

    row = {column: "" for column in trace_evidence.SUMMARY_COLUMNS}
    row.update(
        {
            "schema_version": trace_evidence.SCHEMA_VERSION,
            "sample_stem": sample_stem,
            "candidate_id": candidate_id,
            "candidate_assignment_sha256": candidate_assignment_sha256,
            "trace_artifact_role": PT_PATTERN_TRACE_FULL_CONTEXT_ROLE,
            "scan_pattern_seed_status": "full_pattern_seeded",
            "base_mz": base_mz,
            "base_rt_apex_min": base_rt_apex_min,
            "rt_half_window_sec": "30",
            "ppm": "20",
            "mz_source": "expected",
            "trace_decision_basis": trace_evidence.TRACE_DECISION_BASIS,
            "trace_smoothing_method": trace_evidence.TRACE_SMOOTH_METHOD,
            "trace_smoothing_window_points": str(
                trace_evidence.TRACE_SMOOTH_WINDOW_POINTS
            ),
            "min_median_rt_step_sec": "0.06",
            "min_effective_smoothing_window_sec": "9",
            "max_trace_point_count": "31",
            "positions_requested": "4",
            "positions_with_points": "4",
            "positions_with_signal": "4",
            "non_base_positions_supported": "3",
            "max_abs_apex_delta_sec": "0.4",
            "min_pearson_r_to_base": "0.95",
            "min_active_overlap_fraction": "0.70",
            "min_shifted_pearson_r_to_base": "0.96",
            "max_abs_shape_shift_sec_to_base": "2.0",
            "raw_max_abs_apex_delta_sec": "0.4",
            "raw_min_pearson_r_to_base": "0.95",
            "raw_min_active_overlap_fraction": "0.70",
            "trace_ambiguity_status": "clear",
            "trace_ambiguity_flags": "none",
            "shifted_only_position_labels": "none",
            "trace_support_status": "supported",
            "combined_pt_pattern_status": "pt_pattern_detected_diagnostic",
            "reason": "supported",
        }
    )
    row.update({key: str(value) for key, value in overrides.items()})
    return row


def write_overlay_lineage_bundle(
    *,
    review_packet_csv: Path,
    packet_rows: Sequence[dict[str, str]],
    trace_points_csv: Path,
    trace_point_rows: Sequence[dict[str, str]],
    context_trace_points_csv: Path | None = None,
    context_trace_point_rows: Sequence[dict[str, str]] | None = None,
    project_metrics_from_trace_points: bool = True,
) -> dict[str, Path | None]:
    """Write a self-consistent current packet/trace manifest fixture bundle."""

    if not packet_rows:
        raise ValueError("fixture packet_rows must not be empty")
    packet_schema = str(packet_rows[0].get("schema_version", "")).strip()
    try:
        packet_role = packet.PACKET_SCHEMA_ROLE[packet_schema]
    except KeyError as exc:
        raise ValueError(
            f"unsupported fixture packet schema: {packet_schema!r}"
        ) from exc
    if context_trace_points_csv is None and context_trace_point_rows is not None:
        raise ValueError("context rows require context path")
    if context_trace_points_csv is not None and context_trace_point_rows is None:
        raise ValueError("context path requires context rows")

    packet_candidate_rows = [
        _candidate_row_from_packet(row) for row in packet_rows
    ]
    candidates_csv = review_packet_csv.parent / "pt_pattern_candidates.csv"
    write_delimited_rows(
        candidates_csv,
        packet_candidate_rows,
        packet.CANDIDATE_REQUIRED_COLUMNS,
    )
    all_candidate_ids = {
        str(row.get("candidate_id", "")).strip()
        for row in [*packet_rows, *trace_point_rows, *(context_trace_point_rows or ())]
    }
    if "" in all_candidate_ids:
        raise ValueError("fixture candidate_id must be non-empty")
    assignment_by_candidate = {
        str(row["candidate_id"]): pt_pattern_candidate_assignment_fingerprint(row)
        for row in packet_candidate_rows
    }
    for candidate_id in sorted(all_candidate_ids - assignment_by_candidate.keys()):
        assignment_by_candidate[candidate_id] = hashlib.sha256(
            f"pt-pattern-test-assignment|{candidate_id}".encode("utf-8")
        ).hexdigest()

    candidate_assignments = tuple(sorted(assignment_by_candidate.items()))
    candidates_csv_sha256 = file_sha256(candidates_csv).lower()
    raw_sha256 = "d" * 64
    trace_extraction_sha256 = pt_pattern_trace_extraction_fingerprint(
        candidates_csv_sha256=candidates_csv_sha256,
        raw_sha256=raw_sha256,
        source_sample_stem="SampleA",
        limit=max(1, len(candidate_assignments)),
        offset=0,
        candidate_assignments=candidate_assignments,
        ppm=20.0,
        rt_half_window_sec=30.0,
        mz_source="expected",
        active_fraction=0.1,
        min_pearson_r=0.7,
        max_apex_delta_sec=30.0,
        trace_decision_basis=trace_evidence.TRACE_DECISION_BASIS,
        trace_smoothing_method=trace_evidence.TRACE_SMOOTH_METHOD,
        trace_smoothing_window_points=trace_evidence.TRACE_SMOOTH_WINDOW_POINTS,
        anchor_segmentation_selector=trace_batch.ANCHOR_SEGMENTATION_SELECTOR,
    )

    for row in packet_rows:
        candidate_id = str(row["candidate_id"])
        row["sample_stem"] = str(row.get("sample_stem", "") or "SampleA")
        row["candidate_assignment_sha256"] = assignment_by_candidate[candidate_id]
        row["trace_extraction_sha256"] = trace_extraction_sha256
        row["trace_artifact_role"] = packet_role

    primary_trace_schema = (
        trace_batch.ANCHOR_LOCAL_SCHEMA_VERSION
        if packet_role == PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE
        else trace_evidence.SCHEMA_VERSION
    )
    trace_summary_csv = review_packet_csv.parent / (
        "pt_pattern_anchor_local_trace_summary.csv"
        if packet_role == PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE
        else "pt_pattern_trace_summary.csv"
    )
    trace_metrics_csv = review_packet_csv.parent / (
        "pt_pattern_anchor_local_trace_metrics.csv"
        if packet_role == PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE
        else "pt_pattern_trace_metrics.csv"
    )
    summary_rows = [
        _summary_row_from_packet(
            row,
            schema_version=primary_trace_schema,
            artifact_role=packet_role,
        )
        for row in packet_rows
    ]
    metric_rows = [
        metric_row
        for row in packet_rows
        for metric_row in _metric_rows_from_packet(
            row,
            schema_version=primary_trace_schema,
            artifact_role=packet_role,
        )
    ]
    _prepare_trace_rows(
        summary_rows,
        schema_version=primary_trace_schema,
        artifact_role=packet_role,
        assignment_by_candidate=assignment_by_candidate,
        trace_extraction_sha256=trace_extraction_sha256,
    )
    _prepare_trace_rows(
        metric_rows,
        schema_version=primary_trace_schema,
        artifact_role=packet_role,
        assignment_by_candidate=assignment_by_candidate,
        trace_extraction_sha256=trace_extraction_sha256,
    )
    if project_metrics_from_trace_points:
        _project_metric_raw_stats_from_trace_points(metric_rows, trace_point_rows)
        _project_metric_evidence_from_trace_points(metric_rows, trace_point_rows)
    _project_trace_summaries_from_metrics(summary_rows, metric_rows)
    summary_columns = (
        trace_batch.ANCHOR_LOCAL_SUMMARY_COLUMNS
        if packet_role == PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE
        else trace_evidence.SUMMARY_COLUMNS
    )
    metric_columns = (
        trace_batch.ANCHOR_LOCAL_METRIC_COLUMNS
        if packet_role == PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE
        else trace_evidence.METRIC_COLUMNS
    )
    write_delimited_rows(trace_summary_csv, summary_rows, summary_columns)
    write_delimited_rows(trace_metrics_csv, metric_rows, metric_columns)

    _prepare_trace_rows(
        trace_point_rows,
        schema_version=primary_trace_schema,
        artifact_role=packet_role,
        assignment_by_candidate=assignment_by_candidate,
        trace_extraction_sha256=trace_extraction_sha256,
    )
    write_delimited_rows(
        trace_points_csv,
        trace_point_rows,
        trace_evidence.TRACE_POINT_COLUMNS,
    )
    primary_manifest = _write_trace_manifest(
        trace_points_csv=trace_points_csv,
        summary_csv=trace_summary_csv,
        metrics_csv=trace_metrics_csv,
        schema_version=primary_trace_schema,
        artifact_role=packet_role,
        trace_extraction_sha256=trace_extraction_sha256,
        candidate_assignments=candidate_assignments,
        candidates_csv=candidates_csv,
        candidates_csv_sha256=candidates_csv_sha256,
        raw_sha256=raw_sha256,
    )

    context_manifest: Path | None = None
    if context_trace_points_csv is not None:
        assert context_trace_point_rows is not None
        context_summary_csv: Path | None = None
        context_metrics_csv: Path | None = None
        if packet_role == PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE:
            context_summary_csv = (
                context_trace_points_csv.parent / "pt_pattern_trace_summary.csv"
            )
            context_metrics_csv = (
                context_trace_points_csv.parent / "pt_pattern_trace_metrics.csv"
            )
            context_summary_rows = [
                _summary_row_from_packet(
                    row,
                    schema_version=trace_evidence.SCHEMA_VERSION,
                    artifact_role=PT_PATTERN_TRACE_FULL_CONTEXT_ROLE,
                )
                for row in packet_rows
            ]
            context_metric_rows = [
                metric_row
                for row in packet_rows
                for metric_row in _metric_rows_from_packet(
                    row,
                    schema_version=trace_evidence.SCHEMA_VERSION,
                    artifact_role=PT_PATTERN_TRACE_FULL_CONTEXT_ROLE,
                )
            ]
            _prepare_trace_rows(
                context_summary_rows,
                schema_version=trace_evidence.SCHEMA_VERSION,
                artifact_role=PT_PATTERN_TRACE_FULL_CONTEXT_ROLE,
                assignment_by_candidate=assignment_by_candidate,
                trace_extraction_sha256=trace_extraction_sha256,
            )
            _prepare_trace_rows(
                context_metric_rows,
                schema_version=trace_evidence.SCHEMA_VERSION,
                artifact_role=PT_PATTERN_TRACE_FULL_CONTEXT_ROLE,
                assignment_by_candidate=assignment_by_candidate,
                trace_extraction_sha256=trace_extraction_sha256,
            )
            _project_metric_raw_stats_from_trace_points(
                context_metric_rows,
                context_trace_point_rows,
            )
            _project_metric_evidence_from_trace_points(
                context_metric_rows,
                context_trace_point_rows,
            )
            _project_trace_summaries_from_metrics(
                context_summary_rows,
                context_metric_rows,
            )
            write_delimited_rows(
                context_summary_csv,
                context_summary_rows,
                trace_evidence.SUMMARY_COLUMNS,
            )
            write_delimited_rows(
                context_metrics_csv,
                context_metric_rows,
                trace_evidence.METRIC_COLUMNS,
            )
        _prepare_trace_rows(
            context_trace_point_rows,
            schema_version=trace_evidence.SCHEMA_VERSION,
            artifact_role=PT_PATTERN_TRACE_FULL_CONTEXT_ROLE,
            assignment_by_candidate=assignment_by_candidate,
            trace_extraction_sha256=trace_extraction_sha256,
        )
        write_delimited_rows(
            context_trace_points_csv,
            context_trace_point_rows,
            trace_evidence.TRACE_POINT_COLUMNS,
        )
        # A full-context packet plus a separate context trace is intentionally
        # invalid. Do not overwrite the primary full-context manifest while
        # constructing that negative fixture; the consumer must reject the
        # role matrix before it attempts to load a context manifest.
        if packet_role == PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE:
            assert context_summary_csv is not None
            assert context_metrics_csv is not None
            context_manifest = _write_trace_manifest(
                trace_points_csv=context_trace_points_csv,
                summary_csv=context_summary_csv,
                metrics_csv=context_metrics_csv,
                schema_version=trace_evidence.SCHEMA_VERSION,
                artifact_role=PT_PATTERN_TRACE_FULL_CONTEXT_ROLE,
                trace_extraction_sha256=trace_extraction_sha256,
                candidate_assignments=candidate_assignments,
                candidates_csv=candidates_csv,
                candidates_csv_sha256=candidates_csv_sha256,
                raw_sha256=raw_sha256,
            )

    max_per_status = max(1, len(packet_rows))
    projected_packet_rows = packet.build_review_packet_rows(
        candidate_rows=packet_candidate_rows,
        summary_rows=summary_rows,
        metric_rows=metric_rows,
        packet_label=str(packet_rows[0]["packet_label"]),
        source_label=str(packet_rows[0]["source_label"]),
        max_per_status=max_per_status,
    )
    packet_columns = (
        packet.PACKET_COLUMNS
        if packet_role == PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE
        else packet.LEGACY_PACKET_COLUMNS
    )
    write_delimited_rows(review_packet_csv, projected_packet_rows, packet_columns)
    packet_manifest_path = packet.default_review_packet_manifest_path(review_packet_csv)
    packet_manifest = {
        "schema_version": packet_schema,
        "packet_manifest_version": packet.PACKET_MANIFEST_VERSION,
        "readiness_tier": packet.READINESS_TIER,
        "evidence_owner": packet.EVIDENCE_OWNER,
        "packet_label": str(packet_rows[0]["packet_label"]),
        "source_label": str(packet_rows[0]["source_label"]),
        "candidates_csv": str(candidates_csv),
        "trace_summary_csv": str(trace_summary_csv),
        "trace_metrics_csv": str(trace_metrics_csv),
        "trace_artifact_role": packet_role,
        "trace_extraction_sha256": trace_extraction_sha256,
        "trace_manifest_json": str(primary_manifest),
        "max_per_status": max_per_status,
        "packet_row_count": len(projected_packet_rows),
        "input_sha256": {
            "candidates_csv": candidates_csv_sha256,
            "trace_summary_csv": file_sha256(trace_summary_csv).lower(),
            "trace_metrics_csv": file_sha256(trace_metrics_csv).lower(),
            "trace_manifest_json": file_sha256(primary_manifest).lower(),
        },
        "oracle_status": "pending_human_review",
        "diagnostic_only": True,
        "outputs": {
            "packet_csv": str(review_packet_csv),
            "packet_csv_sha256": file_sha256(review_packet_csv).lower(),
        },
    }
    packet_manifest_path.write_text(
        json.dumps(packet_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "review_packet_manifest_json": packet_manifest_path,
        "trace_manifest_json": primary_manifest,
        "context_trace_manifest_json": context_manifest,
    }


def write_verified_oracle_promotion_bundle(
    *,
    review_packet_csv: Path,
    trace_points_csv: Path,
    oracle_call_by_packet_row_id: Mapping[str, str],
    output_dir: Path,
) -> dict[str, Path]:
    """Build the real review -> overlay -> promotion chain for consumer tests."""

    from xic_extractor.discovery import pt_pattern_blind_overlay as overlay
    from xic_extractor.discovery import pt_pattern_oracle_promotion as promotion

    overlay_outputs = overlay.run_blind_overlay(
        review_packet_csv=review_packet_csv,
        trace_points_csv=trace_points_csv,
        output_dir=output_dir / "overlay",
        title="Verified promotion fixture",
    )
    review_rows = read_delimited_rows(
        review_packet_csv,
        required_columns=(
            "source_label",
            "packet_row_id",
            "candidate_id",
            "base_mz",
            "base_rt_apex_min",
        ),
        delimiter=",",
    )
    machine_rows = read_delimited_rows(
        overlay_outputs["machine_calls_csv"],
        required_columns=("packet_row_id", "machine_call", "machine_call_reason"),
        delimiter=",",
    )
    machine_by_id = {row["packet_row_id"]: row for row in machine_rows}
    observed_ids = {row["packet_row_id"] for row in review_rows}
    if set(oracle_call_by_packet_row_id) != observed_ids:
        raise ValueError("fixture oracle calls must exactly cover the review packet")
    review_sha256 = file_sha256(review_packet_csv)
    overlay_sha256 = file_sha256(overlay_outputs["summary_json"])
    adjudicated_rows: list[dict[str, str]] = []
    for case_index, review_row in enumerate(review_rows, start=1):
        packet_row_id = review_row["packet_row_id"]
        oracle_call = str(oracle_call_by_packet_row_id[packet_row_id]).strip()
        machine_row = machine_by_id[packet_row_id]
        adjudicated_rows.append(
            {
                "dataset": review_row["source_label"],
                "case_index": str(case_index),
                "adjudicated_call": oracle_call,
                "adjudication_basis": "verified_consumer_fixture",
                "user_call": oracle_call,
                "machine_call": machine_row["machine_call"],
                "note_optional": "",
                "machine_call_reason": machine_row["machine_call_reason"],
                "base_mz": review_row["base_mz"],
                "base_rt_apex_min": review_row["base_rt_apex_min"],
                "candidate_id": review_row["candidate_id"],
                "packet_row_id": packet_row_id,
                "review_packet_csv_sha256": review_sha256,
                "blind_overlay_summary_json_sha256": overlay_sha256,
            }
        )
    adjudicated_csv = output_dir / "adjudicated.csv"
    output_dir.mkdir(parents=True, exist_ok=True)
    write_delimited_rows(
        adjudicated_csv,
        adjudicated_rows,
        promotion.ADJUDICATED_REQUIRED_COLUMNS,
    )
    return promotion.run_oracle_promotion(
        adjudicated_oracle_csv=adjudicated_csv,
        review_packet_csvs=[review_packet_csv],
        blind_overlay_summary_jsons=[overlay_outputs["summary_json"]],
        output_dir=output_dir / "promotion",
        oracle_packet_id="verified_consumer_fixture",
    )


def write_trace_summary_lineage_bundle(
    *,
    trace_summary_csv: Path,
    summary_rows: Sequence[dict[str, str]],
    trace_metrics_csv: Path | None = None,
    metric_rows: Sequence[dict[str, str]] | None = None,
    candidate_rows: Sequence[Mapping[str, str]] | None = None,
    artifact_role: str = PT_PATTERN_TRACE_FULL_CONTEXT_ROLE,
) -> dict[str, Path]:
    """Write a self-consistent current summary/metrics/manifest fixture."""

    if not summary_rows:
        raise ValueError("fixture summary_rows must not be empty")
    if artifact_role == PT_PATTERN_TRACE_FULL_CONTEXT_ROLE:
        schema_version = trace_evidence.SCHEMA_VERSION
        summary_columns = trace_evidence.SUMMARY_COLUMNS
        metric_columns = trace_evidence.METRIC_COLUMNS
        metrics_name = "pt_pattern_trace_metrics.csv"
    elif artifact_role == PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE:
        schema_version = trace_batch.ANCHOR_LOCAL_SCHEMA_VERSION
        summary_columns = trace_batch.ANCHOR_LOCAL_SUMMARY_COLUMNS
        metric_columns = trace_batch.ANCHOR_LOCAL_METRIC_COLUMNS
        metrics_name = "pt_pattern_anchor_local_trace_metrics.csv"
    else:
        raise ValueError(f"unsupported fixture artifact role: {artifact_role!r}")
    resolved_metrics_csv = trace_metrics_csv or trace_summary_csv.parent / metrics_name
    prepared_metric_rows = (
        list(metric_rows)
        if metric_rows is not None
        else [
            metric_row
            for summary_row in summary_rows
            for metric_row in _metric_rows_from_packet(
                summary_row,
                schema_version=schema_version,
                artifact_role=artifact_role,
            )
        ]
    )
    source_samples = {
        str(row.get("sample_stem", "") or "SampleA") for row in summary_rows
    }
    if len(source_samples) != 1:
        raise ValueError("fixture summary rows must use one sample_stem")
    source_sample_stem = next(iter(source_samples))
    prepared_candidate_rows = [
        {
            column: str(candidate_row.get(column, ""))
            for column in packet.CANDIDATE_REQUIRED_COLUMNS
        }
        for candidate_row in (
            candidate_rows
            if candidate_rows is not None
            else [_candidate_row_from_packet(row) for row in summary_rows]
        )
    ]
    summary_candidate_ids = {str(row["candidate_id"]) for row in summary_rows}
    candidate_ids = {str(row["candidate_id"]) for row in prepared_candidate_rows}
    if candidate_ids != summary_candidate_ids:
        raise ValueError("fixture candidate rows must match summary candidate IDs")
    candidates_csv = trace_summary_csv.parent / "pt_pattern_candidates.csv"
    write_delimited_rows(
        candidates_csv,
        prepared_candidate_rows,
        packet.CANDIDATE_REQUIRED_COLUMNS,
    )
    assignment_by_candidate = {
        str(row["candidate_id"]): pt_pattern_candidate_assignment_fingerprint(row)
        for row in prepared_candidate_rows
    }
    candidate_assignments = tuple(sorted(assignment_by_candidate.items()))
    candidates_csv_sha256 = file_sha256(candidates_csv).lower()
    raw_sha256 = "d" * 64
    trace_extraction_sha256 = pt_pattern_trace_extraction_fingerprint(
        candidates_csv_sha256=candidates_csv_sha256,
        raw_sha256=raw_sha256,
        source_sample_stem=source_sample_stem,
        limit=max(1, len(candidate_assignments)),
        offset=0,
        candidate_assignments=candidate_assignments,
        ppm=20.0,
        rt_half_window_sec=30.0,
        mz_source="expected",
        active_fraction=0.1,
        min_pearson_r=0.7,
        max_apex_delta_sec=30.0,
        trace_decision_basis=trace_evidence.TRACE_DECISION_BASIS,
        trace_smoothing_method=trace_evidence.TRACE_SMOOTH_METHOD,
        trace_smoothing_window_points=trace_evidence.TRACE_SMOOTH_WINDOW_POINTS,
        anchor_segmentation_selector=trace_batch.ANCHOR_SEGMENTATION_SELECTOR,
    )
    _prepare_trace_rows(
        summary_rows,
        schema_version=schema_version,
        artifact_role=artifact_role,
        assignment_by_candidate=assignment_by_candidate,
        trace_extraction_sha256=trace_extraction_sha256,
    )
    _prepare_trace_rows(
        prepared_metric_rows,
        schema_version=schema_version,
        artifact_role=artifact_role,
        assignment_by_candidate=assignment_by_candidate,
        trace_extraction_sha256=trace_extraction_sha256,
    )
    _project_trace_summaries_from_metrics(summary_rows, prepared_metric_rows)
    write_delimited_rows(trace_summary_csv, summary_rows, summary_columns)
    write_delimited_rows(
        resolved_metrics_csv,
        prepared_metric_rows,
        metric_columns,
    )
    manifest_json = _write_trace_manifest(
        trace_points_csv=None,
        summary_csv=trace_summary_csv,
        metrics_csv=resolved_metrics_csv,
        schema_version=schema_version,
        artifact_role=artifact_role,
        trace_extraction_sha256=trace_extraction_sha256,
        candidate_assignments=candidate_assignments,
        candidates_csv=candidates_csv,
        candidates_csv_sha256=candidates_csv_sha256,
        raw_sha256=raw_sha256,
        source_sample_stem=source_sample_stem,
    )
    return {
        "trace_summary_csv": trace_summary_csv,
        "trace_metrics_csv": resolved_metrics_csv,
        "trace_manifest_json": manifest_json,
        "candidates_csv": candidates_csv,
    }


def _candidate_row_from_packet(packet_row: Mapping[str, str]) -> dict[str, str]:
    candidate_id = str(packet_row["candidate_id"])
    base_mz = float(packet_row.get("base_mz", "500") or 500.0)
    values = {
        "sample_stem": str(packet_row.get("sample_stem", "") or "SampleA"),
        "method_label": str(packet_row.get("method_label", "") or "pt_pattern"),
        "pattern_id": "pt",
        "candidate_id": candidate_id,
        "base_feature_id": f"{candidate_id}:base",
        "base_mz": str(base_mz),
        "base_rt_apex_min": str(
            packet_row.get("base_rt_apex_min", "") or "10.0"
        ),
        "pt_plus_1_feature_id": f"{candidate_id}:plus1",
        "pt_plus_1_mz": str(base_mz + 1.002111),
        "pt_plus_1_expected_mz": str(base_mz + 1.002111),
        "pt_plus_2_feature_id": f"{candidate_id}:plus2",
        "pt_plus_2_mz": str(base_mz + 2.002271),
        "pt_plus_2_expected_mz": str(base_mz + 2.002271),
        "pt_plus_4_feature_id": f"{candidate_id}:plus4",
        "pt_plus_4_mz": str(base_mz + 4.005213),
        "pt_plus_4_expected_mz": str(base_mz + 4.005213),
        "candidate_state": str(
            packet_row.get("candidate_state", "") or "pt_pattern_candidate"
        ),
        "review_priority": str(packet_row.get("review_priority", "")),
        "evidence_tier": str(packet_row.get("evidence_tier", "")),
        "evidence_score": str(packet_row.get("evidence_score", "")),
        "cid_nl_base_status": str(packet_row.get("cid_nl_base_status", "")),
        "cid_nl_base_join_mode": str(
            packet_row.get("cid_nl_base_join_mode", "")
        ),
    }
    return {
        column: values.get(column, "")
        for column in packet.CANDIDATE_REQUIRED_COLUMNS
    }


def _summary_row_from_packet(
    packet_row: Mapping[str, str],
    *,
    schema_version: str,
    artifact_role: str,
) -> dict[str, str]:
    columns = (
        trace_batch.ANCHOR_LOCAL_SUMMARY_COLUMNS
        if artifact_role == PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE
        else trace_evidence.SUMMARY_COLUMNS
    )
    row = {column: str(packet_row.get(column, "")) for column in columns}
    row.update(
        {
            "schema_version": schema_version,
            "sample_stem": str(packet_row.get("sample_stem", "") or "SampleA"),
            "candidate_id": str(packet_row["candidate_id"]),
            "trace_artifact_role": artifact_role,
            "scan_pattern_seed_status": str(
                packet_row.get("scan_pattern_seed_status", "")
                or "full_pattern_seeded"
            ),
            "rt_half_window_sec": str(
                packet_row.get("rt_half_window_sec", "") or "30"
            ),
            "ppm": str(packet_row.get("ppm", "") or "20"),
            "mz_source": str(packet_row.get("mz_source", "") or "expected"),
            "trace_decision_basis": str(
                packet_row.get("trace_decision_basis", "")
                or trace_evidence.TRACE_DECISION_BASIS
            ),
            "trace_smoothing_method": str(
                packet_row.get("trace_smoothing_method", "")
                or trace_evidence.TRACE_SMOOTH_METHOD
            ),
            "trace_smoothing_window_points": str(
                packet_row.get("trace_smoothing_window_points", "")
                or trace_evidence.TRACE_SMOOTH_WINDOW_POINTS
            ),
            "reason": str(packet_row.get("trace_reason", "")),
        }
    )
    if artifact_role == PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE:
        row["anchor_segmentation_status"] = str(
            packet_row.get("anchor_segmentation_status", "") or "clear"
        )
        if row["anchor_segmentation_status"] == "clear":
            row["anchor_segmentation_reason"] = str(
                packet_row.get("anchor_segmentation_reason", "")
                or "one_anchor_segment_per_position"
            )
    return row


def _metric_rows_from_packet(
    packet_row: Mapping[str, str],
    *,
    schema_version: str,
    artifact_role: str,
) -> list[dict[str, str]]:
    columns = (
        trace_batch.ANCHOR_LOCAL_METRIC_COLUMNS
        if artifact_role == PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE
        else trace_evidence.METRIC_COLUMNS
    )
    supported_count = int(
        float(packet_row.get("non_base_positions_supported", "") or 3)
    )
    combined_status = str(
        packet_row.get("combined_pt_pattern_status", "")
        or "pt_pattern_detected_diagnostic"
    )
    summary_flags = {
        flag
        for flag in str(packet_row.get("trace_ambiguity_flags", "none")).split(";")
        if flag and flag != "none"
    }
    shifted_only_labels = {
        label
        for label in str(
            packet_row.get("shifted_only_position_labels", "none")
        ).split(",")
        if label and label != "none"
    }
    point_count = str(packet_row.get("max_trace_point_count", "") or "31")
    median_rt_step_sec = str(
        packet_row.get("min_median_rt_step_sec", "") or "0.06"
    )
    effective_smoothing_window_sec = str(
        packet_row.get("min_effective_smoothing_window_sec", "") or "9"
    )
    min_pearson = str(packet_row.get("min_pearson_r_to_base", "") or "0.95")
    min_overlap = str(
        packet_row.get("min_active_overlap_fraction", "") or "0.70"
    )
    min_shifted = str(
        packet_row.get("min_shifted_pearson_r_to_base", "") or "0.96"
    )
    max_apex_delta = str(
        packet_row.get("max_abs_apex_delta_sec", "") or "0.4"
    )
    max_shape_shift = str(
        packet_row.get("max_abs_shape_shift_sec_to_base", "") or "2.0"
    )
    anchor_status = str(
        packet_row.get("anchor_segmentation_status", "") or "clear"
    )
    common_start = float(
        packet_row.get("anchor_segmentation_common_rt_start_min", "") or 9.8
    )
    common_end = float(
        packet_row.get("anchor_segmentation_common_rt_end_min", "") or 10.2
    )
    start_owners = set(
        str(
            packet_row.get(
                "anchor_segmentation_common_start_owner_labels",
                "base",
            )
            or "base"
        ).split("|")
    )
    end_owners = set(
        str(
            packet_row.get(
                "anchor_segmentation_common_end_owner_labels",
                "pt_plus_4",
            )
            or "pt_plus_4"
        ).split("|")
    )
    boundary_review_labels = set(
        label
        for label in str(
            packet_row.get(
                "anchor_segmentation_boundary_review_position_labels",
                "none",
            )
            or "none"
        ).split("|")
        if label != "none"
    )
    descending_right_labels = set(
        label
        for label in str(
            packet_row.get(
                "anchor_segmentation_descending_at_common_right_position_labels",
                "none",
            )
            or "none"
        ).split("|")
        if label != "none"
    )
    max_left_clip = float(
        packet_row.get("anchor_segmentation_max_left_clip_sec", "") or 6.0
    )
    max_right_clip = float(
        packet_row.get("anchor_segmentation_max_right_clip_sec", "") or 18.0
    )
    clip_extreme_label = next(
        (
            label
            for label in trace_evidence.POSITION_LABELS
            if label not in start_owners and label not in end_owners
        ),
        "pt_plus_1",
    )
    common_duration_sec = (common_end - common_start) * 60.0
    max_left_residual = float(
        packet_row.get(
            "anchor_segmentation_max_common_left_residual_fraction",
            "",
        )
        or 0.10
    )
    max_right_residual = float(
        packet_row.get(
            "anchor_segmentation_max_common_right_residual_fraction",
            "",
        )
        or 0.49
    )
    base_mz = float(packet_row.get("base_mz", "") or 500.0)
    base_rt = float(packet_row.get("base_rt_apex_min", "") or 10.0)
    target_mz_by_position = {
        "base": base_mz,
        "pt_plus_1": base_mz + 1.002111,
        "pt_plus_2": base_mz + 2.002271,
        "pt_plus_4": base_mz + 4.005213,
    }
    output: list[dict[str, str]] = []
    for position_index, position_label in enumerate(trace_evidence.POSITION_LABELS):
        row = {column: "" for column in columns}
        is_base = position_label == "base"
        non_base_index = position_index - 1
        is_supported = is_base or non_base_index < supported_count
        max_intensity = "100" if is_base else str(80 - (non_base_index * 20))
        if combined_status == "pt_pattern_candidate_base_missing" and is_base:
            max_intensity = ""
        elif (
            combined_status == "pt_pattern_candidate_incomplete"
            and position_label == "pt_plus_4"
        ):
            max_intensity = ""
        quality_flags: set[str] = set()
        if position_label in shifted_only_labels:
            quality_flags.add("shifted_only_support")
        for flag in summary_flags:
            if flag in {
                "low_scan_density",
                "short_effective_smoothing_window",
                "raw_smoothed_apex_mismatch",
                "raw_shape_weak_for_shifted_support",
                "smoothed_shape_weak_for_shifted_support",
                "large_shape_shift",
                "plus4_window_tail_dominance",
            } and position_label == "pt_plus_1":
                quality_flags.add(flag)
            if flag == "plus4_shifted_only_support" and position_label == "pt_plus_4":
                quality_flags.add(flag)
        shape_shift = "0" if is_base else max_shape_shift
        if "position_shift_direction_mixed" in summary_flags and not is_base:
            shape_shift = (
                f"-{max_shape_shift}"
                if position_label == "pt_plus_1"
                else max_shape_shift
            )
        row.update(
            {
                "schema_version": schema_version,
                "sample_stem": str(
                    packet_row.get("sample_stem", "") or "SampleA"
                ),
                "candidate_id": str(packet_row["candidate_id"]),
                "trace_artifact_role": artifact_role,
                "position_label": position_label,
                "target_mz": format_diagnostic_value(
                    target_mz_by_position[position_label]
                ),
                "rt_min": format_diagnostic_value(max(0.0, base_rt - 0.1)),
                "rt_max": format_diagnostic_value(base_rt + 0.1),
                "ppm": str(packet_row.get("ppm", "") or "20"),
                "trace_decision_basis": str(
                    packet_row.get("trace_decision_basis", "")
                    or trace_evidence.TRACE_DECISION_BASIS
                ),
                "trace_smoothing_method": str(
                    packet_row.get("trace_smoothing_method", "")
                    or trace_evidence.TRACE_SMOOTH_METHOD
                ),
                "trace_smoothing_window_points": str(
                    packet_row.get("trace_smoothing_window_points", "")
                    or trace_evidence.TRACE_SMOOTH_WINDOW_POINTS
                ),
                "point_count": point_count,
                "median_rt_step_sec": median_rt_step_sec,
                "effective_smoothing_window_sec": effective_smoothing_window_sec,
                "raw_max_intensity": max_intensity,
                "raw_apex_delta_sec_to_base": "0" if is_base else max_apex_delta,
                "raw_pearson_r_to_base": "1" if is_base else min_pearson,
                "raw_active_overlap_fraction": "1" if is_base else min_overlap,
                "max_intensity": max_intensity,
                "apex_delta_sec_to_base": "0" if is_base else max_apex_delta,
                "pearson_r_to_base": (
                    "1"
                    if is_base
                    else (
                        "0.5"
                        if position_label in shifted_only_labels
                        else (min_pearson if is_supported else "0")
                    )
                ),
                "active_overlap_fraction": (
                    "1" if is_base else (min_overlap if is_supported else "0")
                ),
                "shifted_pearson_r_to_base": (
                    "1" if is_base else (min_shifted if is_supported else "0")
                ),
                "shape_shift_sec_to_base": shape_shift,
                "trace_quality_flags": (
                    ";".join(sorted(quality_flags)) if quality_flags else "none"
                ),
                "trace_support_status": (
                    "supported" if is_supported else "review"
                ),
                "reason": "fixture_metric_projection",
            }
        )
        if (
            artifact_role == PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE
            and anchor_status == "clear"
        ):
            left_clip = (
                0.0
                if position_label in start_owners
                else (
                    max_left_clip
                    if position_label == clip_extreme_label
                    else min(max_left_clip, 6.0)
                )
            )
            right_clip = (
                0.0
                if position_label in end_owners
                else (
                    max_right_clip
                    if position_label == clip_extreme_label
                    else min(max_right_clip, 6.0)
                )
            )
            segment_start = common_start - (left_clip / 60.0)
            segment_end = common_end + (right_clip / 60.0)
            segment_duration_sec = common_duration_sec + left_clip + right_clip
            row.update(
                {
                    "anchor_segment_rt_start_min": str(segment_start),
                    "anchor_segment_rt_end_min": str(segment_end),
                    "anchor_segment_duration_sec": str(segment_duration_sec),
                    "anchor_segment_class": (
                        "shoulder_candidate"
                        if position_label in boundary_review_labels
                        else "peak"
                    ),
                    "anchor_segment_boundary_stop_reason": (
                        "review_boundary"
                        if position_label in boundary_review_labels
                        else "baseline_crossing"
                    ),
                    "anchor_segment_owns_common_start": str(
                        position_label in start_owners
                    ).lower(),
                    "anchor_segment_owns_common_end": str(
                        position_label in end_owners
                    ).lower(),
                    "anchor_segment_left_clip_sec": str(left_clip),
                    "anchor_segment_right_clip_sec": str(right_clip),
                    "anchor_segment_common_retained_fraction": str(
                        common_duration_sec / segment_duration_sec
                    ),
                    "anchor_segment_common_left_residual_fraction": str(
                        max_left_residual
                        if position_label == "base"
                        else 0.05
                    ),
                    "anchor_segment_common_right_residual_fraction": str(
                        max_right_residual
                        if position_label == "pt_plus_4"
                        else 0.05
                    ),
                    "anchor_segment_common_left_trend": "ascending",
                    "anchor_segment_common_right_trend": (
                        "descending"
                        if position_label in descending_right_labels
                        else "flat"
                    ),
                }
            )
        output.append(row)
    base_metric = output[0]
    for row in output:
        row.update(
            trace_evidence.trace_metric_call_fields(
                row,
                base_metric_row=base_metric,
                min_pearson_r=0.7,
                max_apex_delta_sec=30.0,
            )
        )
    if (
        artifact_role == PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE
        and anchor_status == "clear"
    ):
        output = [
            {
                field: format_diagnostic_value(value)
                for field, value in row.items()
            }
            for row in trace_batch.project_anchor_local_metric_rows(
                output,
                segmentation_status=anchor_status,
            )
        ]
    return output


def _project_trace_summaries_from_metrics(
    summary_rows: Sequence[dict[str, str]],
    metric_rows: Sequence[dict[str, str]],
) -> None:
    metrics_by_candidate: dict[str, list[dict[str, str]]] = {}
    for metric_row in metric_rows:
        metrics_by_candidate.setdefault(
            str(metric_row["candidate_id"]),
            [],
        ).append(metric_row)
    for summary_row in summary_rows:
        aggregate = trace_evidence.trace_summary_aggregate_fields(
            metrics_by_candidate[str(summary_row["candidate_id"])]
        )
        segmentation_status = str(
            summary_row.get("anchor_segmentation_status", "")
        ).strip()
        if segmentation_status and segmentation_status != "clear":
            segmentation_reason = str(
                summary_row.get("anchor_segmentation_reason", "")
            ).strip()
            aggregate.update(
                {
                    "trace_support_status": "review",
                    "combined_pt_pattern_status": "pt_pattern_candidate_review",
                    "reason": (
                        f"anchor_segmentation_{segmentation_status}:"
                        f"{segmentation_reason}"
                    ),
                }
            )
        if segmentation_status:
            summary_row.update(
                {
                    field: format_diagnostic_value(value)
                    for field, value in (
                        trace_batch.project_anchor_segmentation_summary_fields(
                            segmentation_status=segmentation_status,
                            segmentation_reason=str(
                                summary_row.get("anchor_segmentation_reason", "")
                            ),
                            metric_rows=metrics_by_candidate[
                                str(summary_row["candidate_id"])
                            ],
                        ).items()
                    )
                }
            )
        summary_row.update(
            {
                field: format_diagnostic_value(value)
                for field, value in aggregate.items()
            }
        )


def _project_metric_raw_stats_from_trace_points(
    metric_rows: Sequence[dict[str, str]],
    trace_point_rows: Sequence[Mapping[str, str]],
) -> None:
    """Keep overlay fixtures faithful to the producer's raw point projection."""

    metrics_by_pair = {
        (str(row["candidate_id"]), str(row["position_label"])): row
        for row in metric_rows
    }
    points_by_pair: dict[tuple[str, str], list[tuple[float, float]]] = {}
    point_row_counts: dict[tuple[str, str], int] = {}
    invalid_pairs: set[tuple[str, str]] = set()
    for point_row in trace_point_rows:
        pair = (
            str(point_row.get("candidate_id", "")),
            str(point_row.get("position_label", "")),
        )
        if pair not in metrics_by_pair:
            continue
        point_row_counts[pair] = point_row_counts.get(pair, 0) + 1
        try:
            rt_min = float(str(point_row.get("rt_min", "")))
            intensity = float(str(point_row.get("intensity", "")))
        except ValueError:
            invalid_pairs.add(pair)
            continue
        if not (
            math.isfinite(rt_min)
            and math.isfinite(intensity)
            and rt_min >= 0.0
            and intensity >= 0.0
        ):
            invalid_pairs.add(pair)
            continue
        points_by_pair.setdefault(pair, []).append((rt_min, intensity))

    apex_by_candidate: dict[str, float] = {}
    empty_signal_fields = (
        "median_rt_step_sec",
        "effective_smoothing_window_sec",
        "raw_max_intensity",
        "raw_apex_rt_min",
        "raw_apex_delta_sec_to_base",
        "raw_smoothed_apex_delta_sec",
        "raw_height_ratio_to_base",
        "raw_pearson_r_to_base",
        "raw_active_overlap_fraction",
        "max_intensity",
        "apex_rt_min",
        "apex_delta_sec_to_base",
        "height_ratio_to_base",
        "pearson_r_to_base",
        "active_overlap_fraction",
        "shifted_pearson_r_to_base",
        "shape_shift_sec_to_base",
    )
    for pair, metric_row in metrics_by_pair.items():
        points = sorted(points_by_pair.get(pair, []))
        metric_row["point_count"] = str(point_row_counts.get(pair, 0))
        if not points:
            metric_row["rt_min"] = ""
            metric_row["rt_max"] = ""
            for field in empty_signal_fields:
                metric_row[field] = ""
            continue
        rt_values = [rt_min for rt_min, _intensity in points]
        intensity_values = [intensity for _rt_min, intensity in points]
        max_index = max(
            range(len(intensity_values)),
            key=intensity_values.__getitem__,
        )
        median_step_sec = (
            statistics.median(
                (later - earlier) * 60.0
                for earlier, later in zip(
                    rt_values,
                    rt_values[1:],
                    strict=False,
                )
            )
            if len(rt_values) >= 2
            else None
        )
        apex_rt_min = rt_values[max_index]
        metric_row.update(
            {
                "rt_min": format_diagnostic_value(rt_values[0]),
                "rt_max": format_diagnostic_value(rt_values[-1]),
                "median_rt_step_sec": format_diagnostic_value(median_step_sec),
                "effective_smoothing_window_sec": format_diagnostic_value(
                    min(len(points), trace_evidence.TRACE_SMOOTH_WINDOW_POINTS)
                    * median_step_sec
                    if median_step_sec is not None
                    else None
                ),
                "raw_max_intensity": format_diagnostic_value(
                    intensity_values[max_index]
                ),
                "raw_apex_rt_min": format_diagnostic_value(apex_rt_min),
            }
        )
        if pair[1] == "base":
            apex_by_candidate[pair[0]] = apex_rt_min

    rows_by_candidate: dict[str, list[dict[str, str]]] = {}
    for metric_row in metric_rows:
        rows_by_candidate.setdefault(str(metric_row["candidate_id"]), []).append(
            metric_row
        )
    for candidate_id, candidate_rows in rows_by_candidate.items():
        base_row = next(
            row for row in candidate_rows if row["position_label"] == "base"
        )
        base_apex = apex_by_candidate.get(candidate_id)
        for metric_row in candidate_rows:
            raw_apex = metric_row.get("raw_apex_rt_min", "")
            if raw_apex and base_apex is not None:
                metric_row["raw_apex_delta_sec_to_base"] = (
                    format_diagnostic_value((float(raw_apex) - base_apex) * 60.0)
                )
            elif (candidate_id, metric_row["position_label"]) not in invalid_pairs:
                metric_row["raw_apex_delta_sec_to_base"] = ""
            metric_row.update(
                trace_evidence.trace_metric_call_fields(
                    metric_row,
                    base_metric_row=base_row,
                    min_pearson_r=0.7,
                    max_apex_delta_sec=30.0,
                )
            )


def _project_metric_evidence_from_trace_points(
    metric_rows: Sequence[dict[str, str]],
    trace_point_rows: Sequence[Mapping[str, str]],
) -> None:
    metrics_by_candidate: dict[str, list[dict[str, str]]] = {}
    metric_pairs: set[tuple[str, str]] = set()
    for metric_row in metric_rows:
        candidate_id = str(metric_row["candidate_id"])
        position_label = str(metric_row["position_label"])
        metrics_by_candidate.setdefault(candidate_id, []).append(metric_row)
        metric_pairs.add((candidate_id, position_label))

    invalid_candidates: set[str] = set()
    filtered_points: list[Mapping[str, str]] = []
    for point_row in trace_point_rows:
        candidate_id = str(point_row.get("candidate_id", ""))
        position_label = str(point_row.get("position_label", ""))
        if (candidate_id, position_label) not in metric_pairs:
            continue
        try:
            rt_min = float(str(point_row.get("rt_min", "")))
            intensity = float(str(point_row.get("intensity", "")))
        except ValueError:
            invalid_candidates.add(candidate_id)
            continue
        if not (
            math.isfinite(rt_min)
            and math.isfinite(intensity)
            and rt_min >= 0.0
            and intensity >= 0.0
        ):
            invalid_candidates.add(candidate_id)
            continue
        filtered_points.append(point_row)

    for candidate_id, candidate_rows in metrics_by_candidate.items():
        if candidate_id in invalid_candidates:
            continue
        candidate_points = [
            row
            for row in filtered_points
            if str(row.get("candidate_id", "")) == candidate_id
        ]
        projected_rows = trace_evidence.trace_metric_evidence_fields_from_points(
            candidate_rows,
            candidate_points,
            active_fraction=0.1,
            min_pearson_r=0.7,
            max_apex_delta_sec=30.0,
        )
        for metric_row, projected in zip(
            candidate_rows,
            projected_rows,
            strict=True,
        ):
            metric_row.update(projected)


def _prepare_trace_rows(
    rows: Sequence[dict[str, str]],
    *,
    schema_version: str,
    artifact_role: str,
    assignment_by_candidate: Mapping[str, str],
    trace_extraction_sha256: str,
) -> None:
    for row in rows:
        candidate_id = str(row["candidate_id"])
        row.update(
            {
                "schema_version": schema_version,
                "sample_stem": str(row.get("sample_stem", "") or "SampleA"),
                "candidate_assignment_sha256": assignment_by_candidate[candidate_id],
                "trace_extraction_sha256": trace_extraction_sha256,
                "trace_artifact_role": artifact_role,
                "intensity_basis": trace_evidence.RAW_TRACE_BASIS,
            }
        )
        semantic_defaults = {
            "rt_half_window_sec": "30",
            "ppm": "20",
            "mz_source": "expected",
            "trace_decision_basis": trace_evidence.TRACE_DECISION_BASIS,
            "trace_smoothing_method": trace_evidence.TRACE_SMOOTH_METHOD,
            "trace_smoothing_window_points": str(
                trace_evidence.TRACE_SMOOTH_WINDOW_POINTS
            ),
        }
        for field, value in semantic_defaults.items():
            if field in row and not str(row.get(field, "")).strip():
                row[field] = value


def _write_trace_manifest(
    *,
    trace_points_csv: Path | None,
    summary_csv: Path | None = None,
    metrics_csv: Path | None = None,
    schema_version: str,
    artifact_role: str,
    trace_extraction_sha256: str,
    candidate_assignments: Sequence[tuple[str, str]],
    candidates_csv: Path,
    candidates_csv_sha256: str,
    raw_sha256: str,
    source_sample_stem: str = "SampleA",
) -> Path:
    artifact_path = trace_points_csv or summary_csv
    if artifact_path is None:
        raise ValueError("fixture trace manifest requires an artifact path")
    manifest_path = default_trace_manifest_path(
        artifact_path,
        artifact_role=artifact_role,
    )
    manifest = {
        "schema_version": schema_version,
        "trace_bundle_manifest_version": "pt_pattern_trace_bundle_manifest.v1",
        "trace_artifact_role": artifact_role,
        "trace_extraction_sha256": trace_extraction_sha256,
        "candidates_csv": str(candidates_csv),
        "candidates_csv_sha256": candidates_csv_sha256,
        "raw_sha256": raw_sha256,
        "source_sample_stem": source_sample_stem,
        "limit": max(1, len(candidate_assignments)),
        "offset": 0,
        "ppm": 20.0,
        "rt_half_window_sec": 30.0,
        "mz_source": "expected",
        "active_fraction": 0.1,
        "min_pearson_r": 0.7,
        "max_apex_delta_sec": 30.0,
        "trace_decision_basis": trace_evidence.TRACE_DECISION_BASIS,
        "trace_smoothing_method": trace_evidence.TRACE_SMOOTH_METHOD,
        "trace_smoothing_window_points": trace_evidence.TRACE_SMOOTH_WINDOW_POINTS,
        "anchor_segmentation_selector": trace_batch.ANCHOR_SEGMENTATION_SELECTOR,
        "candidate_assignments": [
            {
                "candidate_id": candidate_id,
                "candidate_assignment_sha256": assignment_sha256,
            }
            for candidate_id, assignment_sha256 in candidate_assignments
        ],
        "trace_points_written": trace_points_csv is not None,
        "diagnostic_only": True,
        "output_sha256": {
            "summary_csv": (
                file_sha256(summary_csv).lower() if summary_csv else "b" * 64
            ),
            "metrics_csv": (
                file_sha256(metrics_csv).lower() if metrics_csv else "c" * 64
            ),
            "trace_points_csv": (
                file_sha256(trace_points_csv).lower()
                if trace_points_csv is not None
                else None
            ),
        },
        "output_schema_versions": {
            "summary_csv": schema_version,
            "metrics_csv": schema_version,
            "trace_points_csv": (
                schema_version if trace_points_csv is not None else None
            ),
        },
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest_path
