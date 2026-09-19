"""Package-owned batch diagnosis for anchor-local Pt traces."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from xic_extractor.discovery.pt_pattern_anchor_segmentation import (
    PositionAnchorBoundaryEvidence,
    select_anchor_local_segments,
)
from xic_extractor.discovery.pt_pattern_artifact_contract import (
    PT_PATTERN_ANCHOR_LOCAL_METRIC_COLUMNS,
    PT_PATTERN_ANCHOR_LOCAL_SUMMARY_COLUMNS,
    PT_PATTERN_ANCHOR_LOCAL_TRACE_POINT_COLUMNS,
    PT_PATTERN_ANCHOR_LOCAL_TRACE_SUMMARY_SCHEMA_VERSION,
    PT_PATTERN_ANCHOR_SEGMENTATION_METRIC_COLUMNS,
    PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE,
)
from xic_extractor.discovery.pt_pattern_trace_evidence import (
    POSITION_LABELS,
    combined_pt_pattern_status,
    diagnose_candidate_traces,
)
from xic_extractor.tabular_io import format_diagnostic_value
from xic_extractor.xic_models import XICTrace

ANCHOR_LOCAL_SUMMARY_COLUMNS = PT_PATTERN_ANCHOR_LOCAL_SUMMARY_COLUMNS
ANCHOR_LOCAL_METRIC_COLUMNS = PT_PATTERN_ANCHOR_LOCAL_METRIC_COLUMNS
ANCHOR_LOCAL_TRACE_POINT_COLUMNS = PT_PATTERN_ANCHOR_LOCAL_TRACE_POINT_COLUMNS

SCHEMA_VERSION = PT_PATTERN_ANCHOR_LOCAL_TRACE_SUMMARY_SCHEMA_VERSION
ANCHOR_SEGMENTATION_SELECTOR = "anchor_containing_chrom_peak_segment"


@dataclass(frozen=True, slots=True)
class PtPatternTraceBatchDiagnosis:
    summary_rows: tuple[dict[str, object], ...]
    metric_rows: tuple[dict[str, object], ...]
    trace_point_rows: tuple[dict[str, object], ...]
    anchor_segmentation_statuses: tuple[str, ...]
    anchor_boundary_review_statuses: tuple[str, ...]


def diagnose_pt_pattern_trace_batch(
    candidate_rows: Sequence[Mapping[str, str]],
    trace_by_key: Mapping[tuple[str, str], XICTrace],
    *,
    trace_extraction_sha256: str,
    ppm: float,
    rt_half_window_sec: float,
    mz_source: Literal["expected", "observed"],
    active_fraction: float,
    min_pearson_r: float,
    max_apex_delta_sec: float,
    include_trace_points: bool,
) -> PtPatternTraceBatchDiagnosis:
    """Diagnose each context exactly once per declared output semantics."""

    anchor_summary_rows: list[dict[str, object]] = []
    anchor_metric_rows: list[dict[str, object]] = []
    anchor_trace_point_rows: list[dict[str, object]] = []
    segmentation_statuses: list[str] = []
    boundary_review_statuses: list[str] = []

    for row in candidate_rows:
        candidate_id = str(row["candidate_id"]).strip()
        context_traces = {
            label: trace_by_key.get((candidate_id, label), XICTrace.empty())
            for label in POSITION_LABELS
        }
        segmentation = select_anchor_local_segments(
            context_traces,
            anchor_rt_min=float(row["base_rt_apex_min"]),
        )
        segmentation_statuses.append(segmentation.status)
        anchor = diagnose_candidate_traces(
            row,
            segmentation.trace_by_position,
            trace_extraction_sha256=trace_extraction_sha256,
            trace_artifact_role=PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE,
            ppm=ppm,
            rt_half_window_sec=rt_half_window_sec,
            mz_source=mz_source,
            active_fraction=active_fraction,
            min_pearson_r=min_pearson_r,
            max_apex_delta_sec=max_apex_delta_sec,
            include_trace_points=include_trace_points,
        )
        boundary_by_position = {
            item.position_label: item for item in segmentation.boundary_evidence
        }
        projected_anchor_metrics = project_anchor_local_metric_rows(
            tuple(
                dict(metric_row)
                | _anchor_segmentation_metric_fields(
                    boundary_by_position.get(
                        str(metric_row.get("position_label", ""))
                    )
                )
                for metric_row in anchor.metric_rows
            ),
            segmentation_status=segmentation.status,
        )
        anchor_summary_fields = project_anchor_segmentation_summary_fields(
            segmentation_status=segmentation.status,
            segmentation_reason=segmentation.reason,
            metric_rows=projected_anchor_metrics,
        )
        boundary_review_statuses.append(
            str(anchor_summary_fields["anchor_segmentation_boundary_review_status"])
        )
        anchor_summary = dict(anchor.summary_row)
        anchor_summary.update(anchor_summary_fields)
        if segmentation.status != "clear":
            anchor_summary.update(
                {
                    "trace_support_status": "review",
                    "combined_pt_pattern_status": combined_pt_pattern_status("review"),
                    "reason": (
                        f"anchor_segmentation_{segmentation.status}:"
                        f"{segmentation.reason}"
                    ),
                }
            )
        anchor_summary_rows.append(_with_anchor_schema(anchor_summary))

        anchor_metric_rows.extend(
            _with_anchor_schema(metric_row)
            for metric_row in projected_anchor_metrics
        )
        anchor_trace_point_rows.extend(
            _with_anchor_schema(row) for row in anchor.trace_point_rows
        )

    return PtPatternTraceBatchDiagnosis(
        summary_rows=tuple(anchor_summary_rows),
        metric_rows=tuple(anchor_metric_rows),
        trace_point_rows=tuple(anchor_trace_point_rows),
        anchor_segmentation_statuses=tuple(segmentation_statuses),
        anchor_boundary_review_statuses=tuple(boundary_review_statuses),
    )


def project_anchor_local_metric_rows(
    metric_rows: Sequence[Mapping[str, object]],
    *,
    segmentation_status: str,
) -> tuple[dict[str, object], ...]:
    """Project anchor metric derivations from CSV-precision segment bounds."""

    output = [dict(row) for row in metric_rows]
    if segmentation_status != "clear":
        return tuple(output)
    by_position = {
        str(row.get("position_label", "")).strip(): row for row in output
    }
    if set(by_position) != set(POSITION_LABELS):
        raise ValueError(
            "anchor metric projection requires exactly one row per Pt position"
        )
    starts = {
        position: _csv_precision_float(
            by_position[position].get("anchor_segment_rt_start_min"),
            field="anchor_segment_rt_start_min",
        )
        for position in POSITION_LABELS
    }
    ends = {
        position: _csv_precision_float(
            by_position[position].get("anchor_segment_rt_end_min"),
            field="anchor_segment_rt_end_min",
        )
        for position in POSITION_LABELS
    }
    common_start = max(starts.values())
    common_end = min(ends.values())
    if common_start >= common_end:
        raise ValueError("clear anchor metric projection requires a positive interval")
    common_duration_sec = (common_end - common_start) * 60.0
    for position in POSITION_LABELS:
        row = by_position[position]
        start = starts[position]
        end = ends[position]
        duration_sec = (end - start) * 60.0
        if duration_sec <= 0.0:
            raise ValueError("anchor segment interval must have positive duration")
        row.update(
            {
                "anchor_segment_rt_start_min": start,
                "anchor_segment_rt_end_min": end,
                "anchor_segment_duration_sec": duration_sec,
                "anchor_segment_owns_common_start": str(
                    start == common_start
                ).lower(),
                "anchor_segment_owns_common_end": str(
                    end == common_end
                ).lower(),
                "anchor_segment_left_clip_sec": (
                    common_start - start
                )
                * 60.0,
                "anchor_segment_right_clip_sec": (
                    end - common_end
                )
                * 60.0,
                "anchor_segment_common_retained_fraction": (
                    common_duration_sec / duration_sec
                ),
            }
        )
    return tuple(output)


def project_anchor_segmentation_summary_fields(
    *,
    segmentation_status: str,
    segmentation_reason: str,
    metric_rows: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Project anchor summary fields from canonical per-position metrics."""

    if segmentation_status != "clear":
        return {
            "anchor_segmentation_status": segmentation_status,
            "anchor_segmentation_reason": segmentation_reason,
            "anchor_segmentation_common_rt_start_min": "",
            "anchor_segmentation_common_rt_end_min": "",
            "anchor_segmentation_common_duration_sec": "",
            "anchor_segmentation_common_start_owner_labels": "none",
            "anchor_segmentation_common_end_owner_labels": "none",
            "anchor_segmentation_boundary_review_status": "unavailable",
            "anchor_segmentation_boundary_review_position_labels": "none",
            "anchor_segmentation_max_left_clip_sec": "",
            "anchor_segmentation_max_right_clip_sec": "",
            "anchor_segmentation_min_common_retained_fraction": "",
            "anchor_segmentation_max_common_left_residual_fraction": "",
            "anchor_segmentation_max_common_right_residual_fraction": "",
            "anchor_segmentation_descending_at_common_right_position_labels": (
                "none"
            ),
        }
    if segmentation_reason != "one_anchor_segment_per_position":
        raise ValueError("clear anchor segmentation reason mismatch")
    projected = project_anchor_local_metric_rows(
        metric_rows,
        segmentation_status=segmentation_status,
    )
    by_position = {
        str(row.get("position_label", "")).strip(): row for row in projected
    }
    ordered_rows = [by_position[position] for position in POSITION_LABELS]
    starts = [
        _csv_precision_float(
            row["anchor_segment_rt_start_min"],
            field="anchor_segment_rt_start_min",
        )
        for row in ordered_rows
    ]
    ends = [
        _csv_precision_float(
            row["anchor_segment_rt_end_min"],
            field="anchor_segment_rt_end_min",
        )
        for row in ordered_rows
    ]
    common_start = max(starts)
    common_end = min(ends)
    start_owners = [
        position
        for position, row in zip(POSITION_LABELS, ordered_rows, strict=True)
        if str(row["anchor_segment_owns_common_start"]) == "true"
    ]
    end_owners = [
        position
        for position, row in zip(POSITION_LABELS, ordered_rows, strict=True)
        if str(row["anchor_segment_owns_common_end"]) == "true"
    ]
    boundary_review_labels = [
        position
        for position, row in zip(POSITION_LABELS, ordered_rows, strict=True)
        if (
            str(row["anchor_segment_owns_common_start"]) == "true"
            or str(row["anchor_segment_owns_common_end"]) == "true"
        )
        and (
            str(row.get("anchor_segment_class", ""))
            in {"shoulder_candidate", "low_scan_segment"}
            or "review"
            in str(row.get("anchor_segment_boundary_stop_reason", ""))
        )
    ]
    descending_right = [
        position
        for position, row in zip(POSITION_LABELS, ordered_rows, strict=True)
        if str(row.get("anchor_segment_common_right_trend", "")) == "descending"
    ]
    return {
        "anchor_segmentation_status": segmentation_status,
        "anchor_segmentation_reason": segmentation_reason,
        "anchor_segmentation_common_rt_start_min": common_start,
        "anchor_segmentation_common_rt_end_min": common_end,
        "anchor_segmentation_common_duration_sec": (
            common_end - common_start
        )
        * 60.0,
        "anchor_segmentation_common_start_owner_labels": _labels(start_owners),
        "anchor_segmentation_common_end_owner_labels": _labels(end_owners),
        "anchor_segmentation_boundary_review_status": (
            "review_required" if boundary_review_labels else "clear"
        ),
        "anchor_segmentation_boundary_review_position_labels": _labels(
            boundary_review_labels
        ),
        "anchor_segmentation_max_left_clip_sec": max(
            _csv_precision_float(
                row["anchor_segment_left_clip_sec"],
                field="anchor_segment_left_clip_sec",
            )
            for row in ordered_rows
        ),
        "anchor_segmentation_max_right_clip_sec": max(
            _csv_precision_float(
                row["anchor_segment_right_clip_sec"],
                field="anchor_segment_right_clip_sec",
            )
            for row in ordered_rows
        ),
        "anchor_segmentation_min_common_retained_fraction": min(
            _csv_precision_float(
                row["anchor_segment_common_retained_fraction"],
                field="anchor_segment_common_retained_fraction",
            )
            for row in ordered_rows
        ),
        "anchor_segmentation_max_common_left_residual_fraction": (
            _max_optional_csv_values(
                ordered_rows,
                "anchor_segment_common_left_residual_fraction",
            )
        ),
        "anchor_segmentation_max_common_right_residual_fraction": (
            _max_optional_csv_values(
                ordered_rows,
                "anchor_segment_common_right_residual_fraction",
            )
        ),
        "anchor_segmentation_descending_at_common_right_position_labels": (
            _labels(descending_right)
        ),
    }


def _csv_precision_float(value: object, *, field: str) -> float:
    try:
        parsed = float(str(value))
    except ValueError as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{field} must be finite")
    return float(format_diagnostic_value(parsed))


def _max_optional_csv_values(
    rows: Sequence[Mapping[str, object]],
    field: str,
) -> float | str:
    values = [
        _csv_precision_float(value, field=field)
        for row in rows
        if str(value := row.get(field, "")).strip()
    ]
    return max(values) if values else ""


def _anchor_segmentation_metric_fields(
    evidence: PositionAnchorBoundaryEvidence | None,
) -> dict[str, object]:
    if evidence is None:
        return {column: "" for column in PT_PATTERN_ANCHOR_SEGMENTATION_METRIC_COLUMNS}
    return {
        "anchor_segment_rt_start_min": evidence.segment_rt_start_min,
        "anchor_segment_rt_end_min": evidence.segment_rt_end_min,
        "anchor_segment_duration_sec": evidence.segment_duration_sec,
        "anchor_segment_class": evidence.segment_class,
        "anchor_segment_boundary_stop_reason": evidence.boundary_stop_reason,
        "anchor_segment_owns_common_start": str(evidence.owns_common_start).lower(),
        "anchor_segment_owns_common_end": str(evidence.owns_common_end).lower(),
        "anchor_segment_left_clip_sec": evidence.left_clip_sec,
        "anchor_segment_right_clip_sec": evidence.right_clip_sec,
        "anchor_segment_common_retained_fraction": evidence.common_retained_fraction,
        "anchor_segment_common_left_residual_fraction": _optional_value(
            evidence.common_left_residual_fraction
        ),
        "anchor_segment_common_right_residual_fraction": _optional_value(
            evidence.common_right_residual_fraction
        ),
        "anchor_segment_common_left_trend": evidence.common_left_trend,
        "anchor_segment_common_right_trend": evidence.common_right_trend,
    }


def _with_anchor_schema(row: Mapping[str, object]) -> dict[str, object]:
    return dict(row) | {"schema_version": SCHEMA_VERSION}


def _optional_value(value: float | None) -> float | str:
    return "" if value is None else value


def _labels(values: Sequence[str]) -> str:
    return "|".join(values) if values else "none"
