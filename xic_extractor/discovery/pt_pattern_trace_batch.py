"""Package-owned batch diagnosis for legacy and anchor-local Pt traces.

The legacy v2 diagnosis retains its full extraction-window semantics. The
anchor-local output is a separate diagnostic schema built from segmentation
evidence; it is not a canonical ``PeakHypothesis`` or an integration boundary.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from xic_extractor.discovery.pt_pattern_anchor_segmentation import (
    PositionAnchorBoundaryEvidence,
    PtPatternAnchorSegmentation,
    select_anchor_local_segments,
)
from xic_extractor.discovery.pt_pattern_trace_evidence import (
    ANCHOR_SEGMENTATION_METRIC_COLUMNS,
    ANCHOR_SEGMENTATION_SUMMARY_COLUMNS,
    METRIC_COLUMNS,
    POSITION_LABELS,
    SUMMARY_COLUMNS,
    TRACE_POINT_COLUMNS,
    combined_pt_pattern_status,
    diagnose_candidate_traces,
)
from xic_extractor.xic_models import XICTrace

ANCHOR_LOCAL_SCHEMA_VERSION = "pt_pattern_anchor_local_trace_diagnosis.v1"
ANCHOR_LOCAL_SUMMARY_COLUMNS = (
    *SUMMARY_COLUMNS[:-3],
    *ANCHOR_SEGMENTATION_SUMMARY_COLUMNS,
    *SUMMARY_COLUMNS[-3:],
)
ANCHOR_LOCAL_METRIC_COLUMNS = (
    *METRIC_COLUMNS[:-3],
    *ANCHOR_SEGMENTATION_METRIC_COLUMNS,
    *METRIC_COLUMNS[-3:],
)
ANCHOR_LOCAL_TRACE_POINT_COLUMNS = TRACE_POINT_COLUMNS


@dataclass(frozen=True, slots=True)
class PtPatternTraceBatchDiagnosis:
    legacy_summary_rows: tuple[dict[str, object], ...]
    legacy_metric_rows: tuple[dict[str, object], ...]
    legacy_trace_point_rows: tuple[dict[str, object], ...]
    anchor_local_summary_rows: tuple[dict[str, object], ...]
    anchor_local_metric_rows: tuple[dict[str, object], ...]
    anchor_local_trace_point_rows: tuple[dict[str, object], ...]
    anchor_segmentation_statuses: tuple[str, ...]
    anchor_boundary_review_statuses: tuple[str, ...]


def diagnose_pt_pattern_trace_batch(
    candidate_rows: Sequence[Mapping[str, str]],
    trace_by_key: Mapping[tuple[str, str], XICTrace],
    *,
    ppm: float,
    rt_half_window_sec: float,
    mz_source: Literal["expected", "observed"],
    active_fraction: float,
    min_pearson_r: float,
    max_apex_delta_sec: float,
    include_trace_points: bool,
) -> PtPatternTraceBatchDiagnosis:
    """Diagnose each context exactly once per declared output semantics."""

    legacy_summary_rows: list[dict[str, object]] = []
    legacy_metric_rows: list[dict[str, object]] = []
    legacy_trace_point_rows: list[dict[str, object]] = []
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
        legacy = diagnose_candidate_traces(
            row,
            context_traces,
            ppm=ppm,
            rt_half_window_sec=rt_half_window_sec,
            mz_source=mz_source,
            active_fraction=active_fraction,
            min_pearson_r=min_pearson_r,
            max_apex_delta_sec=max_apex_delta_sec,
            include_trace_points=include_trace_points,
        )
        legacy_summary_rows.append(legacy.summary_row)
        legacy_metric_rows.extend(legacy.metric_rows)
        legacy_trace_point_rows.extend(legacy.trace_point_rows)

        segmentation = select_anchor_local_segments(
            context_traces,
            anchor_rt_min=float(row["base_rt_apex_min"]),
        )
        segmentation_statuses.append(segmentation.status)
        boundary_review_statuses.append(segmentation.boundary_review_status)
        anchor = diagnose_candidate_traces(
            row,
            segmentation.trace_by_position,
            ppm=ppm,
            rt_half_window_sec=rt_half_window_sec,
            mz_source=mz_source,
            active_fraction=active_fraction,
            min_pearson_r=min_pearson_r,
            max_apex_delta_sec=max_apex_delta_sec,
            include_trace_points=include_trace_points,
        )
        anchor_summary = dict(anchor.summary_row)
        anchor_summary.update(_anchor_segmentation_summary_fields(segmentation))
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

        boundary_by_position = {
            item.position_label: item for item in segmentation.boundary_evidence
        }
        anchor_metric_rows.extend(
            _with_anchor_schema(
                dict(metric_row)
                | _anchor_segmentation_metric_fields(
                    boundary_by_position.get(
                        str(metric_row.get("position_label", ""))
                    )
                )
            )
            for metric_row in anchor.metric_rows
        )
        anchor_trace_point_rows.extend(
            _with_anchor_schema(row) for row in anchor.trace_point_rows
        )

    return PtPatternTraceBatchDiagnosis(
        legacy_summary_rows=tuple(legacy_summary_rows),
        legacy_metric_rows=tuple(legacy_metric_rows),
        legacy_trace_point_rows=tuple(legacy_trace_point_rows),
        anchor_local_summary_rows=tuple(anchor_summary_rows),
        anchor_local_metric_rows=tuple(anchor_metric_rows),
        anchor_local_trace_point_rows=tuple(anchor_trace_point_rows),
        anchor_segmentation_statuses=tuple(segmentation_statuses),
        anchor_boundary_review_statuses=tuple(boundary_review_statuses),
    )


def _anchor_segmentation_summary_fields(
    segmentation: PtPatternAnchorSegmentation,
) -> dict[str, object]:
    evidence = segmentation.boundary_evidence
    start_owners = tuple(
        item.position_label for item in evidence if item.owns_common_start
    )
    end_owners = tuple(
        item.position_label for item in evidence if item.owns_common_end
    )
    descending_right = tuple(
        item.position_label
        for item in evidence
        if item.common_right_trend == "descending"
    )
    common_duration_sec: float | str = ""
    if (
        segmentation.common_rt_start_min is not None
        and segmentation.common_rt_end_min is not None
    ):
        common_duration_sec = max(
            0.0,
            (
                segmentation.common_rt_end_min
                - segmentation.common_rt_start_min
            )
            * 60.0,
        )
    return {
        "anchor_segmentation_status": segmentation.status,
        "anchor_segmentation_reason": segmentation.reason,
        "anchor_segmentation_common_rt_start_min": _optional_value(
            segmentation.common_rt_start_min
        ),
        "anchor_segmentation_common_rt_end_min": _optional_value(
            segmentation.common_rt_end_min
        ),
        "anchor_segmentation_common_duration_sec": common_duration_sec,
        "anchor_segmentation_common_start_owner_labels": _labels(start_owners),
        "anchor_segmentation_common_end_owner_labels": _labels(end_owners),
        "anchor_segmentation_boundary_review_status": (
            segmentation.boundary_review_status
        ),
        "anchor_segmentation_boundary_review_position_labels": _labels(
            segmentation.boundary_review_position_labels
        ),
        "anchor_segmentation_max_left_clip_sec": _max_value(
            item.left_clip_sec for item in evidence
        ),
        "anchor_segmentation_max_right_clip_sec": _max_value(
            item.right_clip_sec for item in evidence
        ),
        "anchor_segmentation_min_common_retained_fraction": _min_value(
            item.common_retained_fraction for item in evidence
        ),
        "anchor_segmentation_max_common_left_residual_fraction": _max_value(
            item.common_left_residual_fraction for item in evidence
        ),
        "anchor_segmentation_max_common_right_residual_fraction": _max_value(
            item.common_right_residual_fraction for item in evidence
        ),
        "anchor_segmentation_descending_at_common_right_position_labels": _labels(
            descending_right
        ),
    }


def _anchor_segmentation_metric_fields(
    evidence: PositionAnchorBoundaryEvidence | None,
) -> dict[str, object]:
    if evidence is None:
        return {column: "" for column in ANCHOR_SEGMENTATION_METRIC_COLUMNS}
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
    return dict(row) | {"schema_version": ANCHOR_LOCAL_SCHEMA_VERSION}


def _optional_value(value: float | None) -> float | str:
    return "" if value is None else value


def _labels(values: Sequence[str]) -> str:
    return "|".join(values) if values else "none"


def _max_value(values: Iterable[float | None]) -> float | str:
    finite = tuple(float(value) for value in values if value is not None)
    return max(finite) if finite else ""


def _min_value(values: Iterable[float]) -> float | str:
    finite = tuple(float(value) for value in values)
    return min(finite) if finite else ""
