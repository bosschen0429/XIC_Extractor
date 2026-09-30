from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from xic_extractor.config import ExtractionConfig, Target
from xic_extractor.extraction.targeted_peak_hypotheses import (
    build_targeted_peak_hypotheses as build_peak_hypotheses,
)
from xic_extractor.peak_detection.baseline import (
    BaselineMethod,
    integrate_with_baseline,
)
from xic_extractor.peak_detection.boundaries import (
    BoundaryCandidateContext,
    BoundaryHypothesis,
    enumerate_boundary_hypotheses,
)
from xic_extractor.peak_detection.boundary_scoring import score_boundary_hypothesis
from xic_extractor.peak_detection.cwt import add_cwt_proposals_for_audit
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.interval_selection import (
    WeightedInterval,
    select_weighted_nonoverlap_intervals,
)
from xic_extractor.peak_detection.models import PeakDetectionResult
from xic_extractor.peak_detection.trace_quality import trace_continuity_score
from xic_extractor.peak_detection.traces import TraceGroup
from xic_extractor.sample_groups import classify_sample_group

PeakCandidateBoundaryRow = dict[str, str]
CWT_LEGACY_AUDIT_FILTER_REASON = "legacy_cwt_width_not_real_cwt"


def build_peak_candidate_boundary_rows(
    *,
    sample_name: str,
    target_label: str,
    role: str,
    istd_pair: str,
    resolver_mode: str,
    peak_result: PeakDetectionResult,
    rt: Any,
    intensity: Any,
    trace_group: TraceGroup | None = None,
    target_mz: float | None = None,
    group: str | None = None,
    baseline_integration_method: BaselineMethod = "asls",
) -> list[PeakCandidateBoundaryRow]:
    sample_group = group or classify_sample_group(sample_name)
    trace_rt = trace_group.primary_trace.rt if trace_group is not None else rt
    trace_intensity = (
        trace_group.primary_trace.intensity if trace_group is not None else intensity
    )
    hypotheses = build_peak_hypotheses(
        sample_name=sample_name,
        target_label=target_label,
        role=role,
        istd_pair=istd_pair,
        resolver_mode=resolver_mode,
        peak_result=peak_result,
        rt=trace_rt,
        intensity=trace_intensity,
        trace_group=trace_group,
        baseline_integration_method=baseline_integration_method,
    )
    return build_peak_candidate_boundary_rows_from_hypotheses(
        sample_name=sample_name,
        target_mz=target_mz,
        hypotheses=hypotheses,
        rt=trace_rt,
        intensity=trace_intensity,
        group=sample_group,
        baseline_integration_method=baseline_integration_method,
    )


def build_peak_candidate_boundary_rows_from_hypotheses(
    *,
    sample_name: str,
    target_mz: float | None,
    hypotheses: tuple[PeakHypothesis, ...],
    rt: Any,
    intensity: Any,
    group: str | None = None,
    baseline_integration_method: BaselineMethod = "asls",
) -> list[PeakCandidateBoundaryRow]:
    sample_group = group or classify_sample_group(sample_name)
    rows: list[PeakCandidateBoundaryRow] = []
    for hypothesis in hypotheses:
        boundaries = enumerate_boundary_hypotheses(
            rt,
            intensity,
            _boundary_candidate_context_from_hypothesis(hypothesis),
            candidate_id=hypothesis.hypothesis_id,
        )
        reference = _candidate_interval_reference(boundaries)
        candidate_rows = [
            _row_from_boundary(
                sample_name=sample_name,
                group=sample_group,
                target_mz=target_mz,
                hypothesis=hypothesis,
                boundary=boundary,
                reference=reference,
                rt=rt,
                intensity=intensity,
                baseline_integration_method=baseline_integration_method,
            )
            for boundary in boundaries
        ]
        _apply_boundary_audit_rank(candidate_rows)
        rows.extend(candidate_rows)
    _apply_boundary_nonoverlap_selection(rows)
    return rows


def append_peak_candidate_boundary_rows(
    rows: list[PeakCandidateBoundaryRow] | None,
    config: ExtractionConfig,
    sample_name: str,
    target: Target,
    peak_result: PeakDetectionResult,
    *,
    rt: Any,
    intensity: Any,
    trace_group: TraceGroup | None = None,
    audit_peak_result: PeakDetectionResult | None = None,
) -> None:
    if not config.emit_peak_candidates or rows is None:
        return
    audited = audit_peak_result
    if audited is None:
        audited = add_cwt_proposals_for_audit(
            peak_result,
            rt,
            intensity,
            config,
        )
    append_peak_candidate_boundary_rows_from_hypotheses(
        rows,
        config,
        sample_name,
        target,
        build_peak_hypotheses(
            sample_name=sample_name,
            target_label=target.label,
            role="ISTD" if target.is_istd else "Analyte",
            istd_pair=target.istd_pair,
            resolver_mode=config.resolver_mode,
            peak_result=audited,
            rt=(
                trace_group.primary_trace.rt if trace_group is not None else rt
            ),
            intensity=(
                trace_group.primary_trace.intensity
                if trace_group is not None
                else intensity
            ),
            trace_group=trace_group,
            baseline_integration_method=cast(
                BaselineMethod,
                config.baseline_integration_method,
            ),
        ),
        rt=rt,
        intensity=intensity,
        trace_group=trace_group,
    )


def append_peak_candidate_boundary_rows_from_hypotheses(
    rows: list[PeakCandidateBoundaryRow] | None,
    config: ExtractionConfig,
    sample_name: str,
    target: Target,
    hypotheses: tuple[PeakHypothesis, ...],
    *,
    rt: Any,
    intensity: Any,
    trace_group: TraceGroup | None = None,
    group: str | None = None,
) -> None:
    if not config.emit_peak_candidates or rows is None:
        return
    trace_rt = trace_group.primary_trace.rt if trace_group is not None else rt
    trace_intensity = (
        trace_group.primary_trace.intensity if trace_group is not None else intensity
    )
    rows.extend(
        build_peak_candidate_boundary_rows_from_hypotheses(
            sample_name=sample_name,
            target_mz=target.mz,
            hypotheses=hypotheses,
            rt=trace_rt,
            intensity=trace_intensity,
            group=group,
            baseline_integration_method=cast(
                BaselineMethod,
                config.baseline_integration_method,
            ),
        )
    )


def _row_from_boundary(
    *,
    sample_name: str,
    group: str,
    target_mz: float | None,
    hypothesis: PeakHypothesis,
    boundary: BoundaryHypothesis,
    reference: BoundaryHypothesis,
    rt: Any,
    intensity: Any,
    baseline_integration_method: BaselineMethod,
) -> PeakCandidateBoundaryRow:
    area_delta = boundary.area_raw_counts_seconds - reference.area_raw_counts_seconds
    width_delta = boundary.width_min - reference.width_min
    baseline = integrate_with_baseline(
        intensity,
        rt,
        boundary.left_index,
        boundary.right_index,
        baseline_method=baseline_integration_method,
    )
    boundary_score = score_boundary_hypothesis(
        boundary,
        reference,
        baseline_score=baseline.baseline_score,
        trace_continuity=trace_continuity_score(
            intensity,
            left=boundary.left_index,
            right=boundary.right_index,
        ),
    )
    return {
        "sample_name": sample_name,
        "group": group,
        "target_label": hypothesis.target_label,
        "target_mz": _format_optional_float(target_mz),
        "role": hypothesis.role,
        "istd_pair": hypothesis.istd_pair,
        "analysis_mode": hypothesis.analysis_mode,
        "resolver_mode": hypothesis.resolver_mode,
        "candidate_id": hypothesis.hypothesis_id,
        "proposal_sources": _join(hypothesis.audit.proposal_sources),
        "selected_candidate": "TRUE" if hypothesis.audit.selected else "FALSE",
        "boundary_id": boundary.boundary_id,
        "boundary_sources": _join(boundary.sources),
        "cwt_audit_filter_reason": _cwt_audit_filter_reason(boundary),
        "rt_left_min": _format_float(boundary.rt_left_min),
        "rt_apex_min": _format_float(boundary.rt_apex_min),
        "rt_right_min": _format_float(boundary.rt_right_min),
        "rt_width_min": _format_float(boundary.width_min),
        "area_raw_counts_seconds": _format_float(
            boundary.area_raw_counts_seconds,
            digits=2,
        ),
        "area_baseline_corrected": _format_float(
            baseline.area_baseline_corrected,
            digits=2,
        ),
        "area_uncertainty": _format_optional_float(baseline.area_uncertainty),
        "area_uncertainty_formula_version": (
            baseline.area_uncertainty_formula_version
        ),
        "baseline_residual_mad": _format_optional_float(baseline.baseline_residual_mad),
        "area_uncertainty_noise_source": baseline.area_uncertainty_noise_source,
        "baseline_type": baseline.baseline_type,
        "baseline_score": _format_optional_float(baseline.baseline_score),
        "boundary_audit_score": str(boundary_score.score),
        "boundary_audit_rank": "",
        "boundary_audit_top": "",
        "boundary_nonoverlap_selected": "",
        "boundary_nonoverlap_note": "",
        "boundary_nonoverlap_blocker_id": "",
        "boundary_support_labels": _join(boundary_score.support_labels),
        "boundary_concern_labels": _join(boundary_score.concern_labels),
        "scan_count": str(boundary.scan_count),
        "area_delta_vs_candidate_interval": _format_float(area_delta, digits=2),
        "area_ratio_vs_candidate_interval": _format_optional_float(
            _safe_ratio(
                boundary.area_raw_counts_seconds,
                reference.area_raw_counts_seconds,
            )
        ),
        "width_delta_vs_candidate_interval": _format_float(width_delta),
        "is_candidate_interval": "TRUE"
        if "candidate_interval" in boundary.sources
        else "FALSE",
    }


def _cwt_audit_filter_reason(boundary: BoundaryHypothesis) -> str:
    return (
        CWT_LEGACY_AUDIT_FILTER_REASON
        if boundary.sources == ("cwt_width",)
        else ""
    )


def _apply_boundary_audit_rank(rows: list[PeakCandidateBoundaryRow]) -> None:
    ranked = sorted(
        enumerate(rows),
        key=lambda item: (
            -int(item[1]["boundary_audit_score"]),
            item[1]["is_candidate_interval"] != "TRUE",
            item[0],
        ),
    )
    for rank, (_index, row) in enumerate(ranked, start=1):
        row["boundary_audit_rank"] = str(rank)
        row["boundary_audit_top"] = "TRUE" if rank == 1 else "FALSE"


def _apply_boundary_nonoverlap_selection(rows: list[PeakCandidateBoundaryRow]) -> None:
    for row in rows:
        row["boundary_nonoverlap_selected"] = "FALSE"
        row["boundary_nonoverlap_note"] = "not_candidate_top"
        row["boundary_nonoverlap_blocker_id"] = ""

    top_rows = [row for row in rows if row["boundary_audit_top"] == "TRUE"]
    selected_intervals = _weighted_nonoverlap_selection(top_rows)
    selected_ids = {row["boundary_id"] for row in selected_intervals}

    for row in top_rows:
        if row["boundary_id"] not in selected_ids:
            blockers = [
                selected
                for selected in selected_intervals
                if _boundaries_overlap(row, selected)
            ]
            if blockers:
                row["boundary_nonoverlap_note"] = _overlap_rejection_note(
                    row,
                    blockers,
                )
                row["boundary_nonoverlap_blocker_id"] = _join(
                    tuple(blocker["boundary_id"] for blocker in blockers)
                )
            else:
                row["boundary_nonoverlap_note"] = "not_selected_by_weighted_interval"
            continue
        row["boundary_nonoverlap_selected"] = "TRUE"
        row["boundary_nonoverlap_note"] = "selected_nonoverlap"


def _weighted_nonoverlap_selection(
    rows: list[PeakCandidateBoundaryRow],
) -> list[PeakCandidateBoundaryRow]:
    by_id = {row["boundary_id"]: row for row in rows}
    intervals = tuple(_weighted_interval_from_row(row) for row in rows)
    return [
        by_id[interval.item_id]
        for interval in select_weighted_nonoverlap_intervals(intervals)
    ]


def _weighted_interval_from_row(
    row: PeakCandidateBoundaryRow,
) -> WeightedInterval:
    return WeightedInterval(
        item_id=row["boundary_id"],
        left=float(row["rt_left_min"]),
        right=float(row["rt_right_min"]),
        weight=int(row["boundary_audit_score"]),
        selected_priority=1 if row["selected_candidate"] == "TRUE" else 0,
        candidate_interval_priority=1
        if row["is_candidate_interval"] == "TRUE"
        else 0,
    )


def _overlap_rejection_note(
    row: PeakCandidateBoundaryRow,
    blockers: list[PeakCandidateBoundaryRow],
) -> str:
    row_score = int(row["boundary_audit_score"])
    if any(
        int(blocker["boundary_audit_score"]) >= row_score
        for blocker in blockers
    ):
        return "overlaps_higher_score"
    return "overlaps_weighted_selection"


def _boundaries_overlap(
    first: PeakCandidateBoundaryRow,
    second: PeakCandidateBoundaryRow,
) -> bool:
    first_left = float(first["rt_left_min"])
    first_right = float(first["rt_right_min"])
    second_left = float(second["rt_left_min"])
    second_right = float(second["rt_right_min"])
    return max(first_left, second_left) < min(first_right, second_right)


def _boundary_candidate_context_from_hypothesis(
    hypothesis: PeakHypothesis,
) -> BoundaryCandidateContext:
    return BoundaryCandidateContext(
        selection_apex_rt=hypothesis.integration.rt_apex_min,
        rt_left_min=hypothesis.integration.rt_left_min,
        rt_right_min=hypothesis.integration.rt_right_min,
        cwt_best_scale=hypothesis.evidence.cwt_best_scale,
        proposal_sources=hypothesis.audit.proposal_sources,
    )


def _candidate_interval_reference(
    boundaries: Sequence[BoundaryHypothesis],
) -> BoundaryHypothesis:
    for boundary in boundaries:
        if "candidate_interval" in boundary.sources:
            return boundary
    if not boundaries:
        raise ValueError("boundary rows require at least one boundary hypothesis")
    return boundaries[0]


def _safe_ratio(value: float, reference: float) -> float | None:
    if reference == 0:
        return None
    return value / reference


def _format_float(value: float, *, digits: int = 5) -> str:
    return f"{value:.{digits}f}"


def _format_optional_float(value: float | None) -> str:
    if value is None:
        return ""
    return _format_float(value)


def _join(values: tuple[str, ...]) -> str:
    return ";".join(values)
