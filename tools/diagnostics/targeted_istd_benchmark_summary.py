"""Summary gate calculations for the targeted ISTD benchmark."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from tools.diagnostics.targeted_istd_benchmark_matching import _is_active_tag
from tools.diagnostics.targeted_istd_benchmark_models import (
    AlignmentCell,
    BenchmarkSummary,
    BenchmarkThresholds,
    CandidateMatch,
    TargetDefinition,
    TargetedPoint,
    TargetedReliabilityPoint,
)
from tools.diagnostics.targeted_istd_benchmark_stats import (
    _mean,
    _median_abs,
    _pearson,
    _percentile_abs,
    _spearman,
)


def _summarize_target(
    target: TargetDefinition,
    points: tuple[TargetedPoint, ...],
    matches: tuple[CandidateMatch, ...],
    *,
    matrix: Mapping[str, Mapping[str, float]],
    cells: Mapping[tuple[str, str], AlignmentCell],
    thresholds: BenchmarkThresholds,
    reliability: Mapping[tuple[str, str], TargetedReliabilityPoint],
    strict_targeted_reliability: bool,
) -> BenchmarkSummary:
    positives = tuple(point for point in points if point.positive)
    reliability_summary = _reliability_summary(
        points,
        reliability=reliability,
        strict_targeted_reliability=strict_targeted_reliability,
    )
    benchmark_points = reliability_summary.clean_points if reliability else positives
    targeted_mean_rt = _mean(point.rt for point in benchmark_points)
    primary_matches = tuple(
        match for match in matches if match.include_in_primary_matrix
    )
    selected = primary_matches[0] if len(primary_matches) == 1 else None
    selected_family = selected.feature_id if selected else ""
    selected_matrix = matrix.get(selected_family, {})
    active_tag = _is_active_tag(target, thresholds)
    paired_logs: list[tuple[float, float]] = []
    rt_deltas: list[float] = []
    for point in benchmark_points:
        area = selected_matrix.get(point.sample_stem)
        if area is not None and area > 0 and point.area is not None:
            paired_logs.append((math.log10(point.area), math.log10(area)))
            cell = cells.get((selected_family, point.sample_stem))
            if cell is not None and cell.apex_rt is not None and point.rt is not None:
                rt_deltas.append(cell.apex_rt - point.rt)

    coverage_denominator_count = len(benchmark_points)
    coverage_minimum = max(
        0,
        coverage_denominator_count
        - max(1, math.ceil(coverage_denominator_count * 0.02)),
    )
    family_mean_rt_delta = (
        selected.family_center_rt - targeted_mean_rt
        if selected is not None and targeted_mean_rt is not None
        else None
    )
    pearson = _pearson(paired_logs)
    spearman = _spearman(paired_logs)
    median_rt_delta = _median_abs(rt_deltas)
    p95_rt_delta = _percentile_abs(rt_deltas, 0.95)
    warnings = _targeted_reliability_warnings(
        active_tag=active_tag,
        strict_targeted_reliability=strict_targeted_reliability,
        reliability_summary=reliability_summary,
    )
    failure_modes: tuple[str, ...]
    if "TARGETED_RELIABILITY_INCONCLUSIVE" in warnings:
        failure_modes = ()
    else:
        failure_modes = _failure_modes(
            active_tag=active_tag,
            primary_matches=primary_matches,
            untargeted_positive_count=len(selected_matrix),
            coverage_minimum=coverage_minimum,
            family_mean_rt_delta=family_mean_rt_delta,
            sample_rt_median_abs_delta=median_rt_delta,
            sample_rt_p95_abs_delta=p95_rt_delta,
            paired_area_n=len(paired_logs),
            pearson=pearson,
            spearman=spearman,
            thresholds=thresholds,
        )
    status = "FAIL" if failure_modes else "WARN" if warnings else "PASS"
    return BenchmarkSummary(
        target_label=target.label,
        role=target.role,
        active_tag=active_tag,
        neutral_loss_da=target.neutral_loss_da,
        target_mz=target.mz,
        target_rt_min=target.rt_min,
        target_rt_max=target.rt_max,
        targeted_positive_count=len(positives),
        targeted_total_count=len(points),
        targeted_mean_rt=targeted_mean_rt,
        candidate_match_count=len(matches),
        primary_match_count=len(primary_matches),
        primary_feature_ids=tuple(match.feature_id for match in primary_matches),
        selected_feature_id=selected_family,
        untargeted_positive_count=len(selected_matrix),
        coverage_minimum=coverage_minimum,
        paired_area_n=len(paired_logs),
        log_area_pearson=pearson,
        log_area_spearman=spearman,
        family_mean_rt_delta_min=family_mean_rt_delta,
        sample_rt_pair_n=len(rt_deltas),
        sample_rt_median_abs_delta_min=median_rt_delta,
        sample_rt_p95_abs_delta_min=p95_rt_delta,
        status=status,
        failure_modes=failure_modes,
        note=_note(active_tag, failure_modes, warnings),
        targeted_reliability_mode=(
            "strict"
            if strict_targeted_reliability
            else "annotate"
            if reliability
            else "not_provided"
        ),
        clean_targeted_positive_count=len(reliability_summary.clean_points),
        targeted_review_positive_count=(reliability_summary.review_positive_count),
        targeted_review_count=reliability_summary.review_count,
        targeted_negative_count=reliability_summary.negative_count,
        coverage_denominator_count=coverage_denominator_count,
        targeted_reliability_warning_modes=warnings,
    )


@dataclass(frozen=True)
class _ReliabilitySummary:
    clean_points: tuple[TargetedPoint, ...]
    review_positive_count: int
    review_count: int
    negative_count: int
    missing_count: int
    review_positive_detail_reasons: tuple[str, ...]


def _benchmark_points(
    points: tuple[TargetedPoint, ...],
    *,
    reliability: Mapping[tuple[str, str], TargetedReliabilityPoint],
    strict_targeted_reliability: bool,
) -> tuple[TargetedPoint, ...]:
    if not reliability:
        return points
    return _reliability_summary(
        points,
        reliability=reliability,
        strict_targeted_reliability=strict_targeted_reliability,
    ).clean_points


def _reliability_summary(
    points: tuple[TargetedPoint, ...],
    *,
    reliability: Mapping[tuple[str, str], TargetedReliabilityPoint],
    strict_targeted_reliability: bool,
) -> _ReliabilitySummary:
    if not reliability:
        positives = tuple(point for point in points if point.positive)
        return _ReliabilitySummary(
            clean_points=positives,
            review_positive_count=0,
            review_count=0,
            negative_count=0,
            missing_count=0,
            review_positive_detail_reasons=(),
        )

    clean: list[TargetedPoint] = []
    review_positive_count = 0
    review_count = 0
    negative_count = 0
    missing_count = 0
    review_positive_detail_reasons: list[str] = []
    for point in points:
        record = reliability.get((point.sample_stem, point.target_label))
        if record is None:
            if point.positive:
                if strict_targeted_reliability:
                    missing_count += 1
                else:
                    clean.append(point)
            continue
        if record.reliability_state == "benchmark_eligible":
            if point.positive:
                clean.append(point)
        elif record.reliability_state == "targeted_review_positive":
            if point.positive:
                review_positive_count += 1
                review_positive_detail_reasons.extend(
                    _review_positive_detail_reasons(record)
                )
        elif record.reliability_state == "targeted_review":
            if point.positive:
                review_count += 1
        elif record.reliability_state == "targeted_negative":
            negative_count += 1
    return _ReliabilitySummary(
        clean_points=tuple(clean),
        review_positive_count=review_positive_count,
        review_count=review_count,
        negative_count=negative_count,
        missing_count=missing_count,
        review_positive_detail_reasons=tuple(
            dict.fromkeys(review_positive_detail_reasons)
        ),
    )


def _targeted_reliability_warnings(
    *,
    active_tag: bool,
    strict_targeted_reliability: bool,
    reliability_summary: _ReliabilitySummary,
) -> tuple[str, ...]:
    warnings: list[str] = []
    if reliability_summary.review_positive_count:
        warnings.append("TARGETED_REVIEW_POSITIVE_EVIDENCE")
        warnings.extend(
            f"TARGETED_REVIEW_POSITIVE_REASON:{reason}"
            for reason in reliability_summary.review_positive_detail_reasons
        )
    if reliability_summary.review_count:
        warnings.append("TARGETED_REVIEW_EVIDENCE")
    if reliability_summary.missing_count:
        warnings.append("TARGETED_RELIABILITY_MISSING")
    if (
        active_tag
        and strict_targeted_reliability
        and len(reliability_summary.clean_points) < 3
    ):
        warnings.append("TARGETED_RELIABILITY_INCONCLUSIVE")
    return tuple(warnings)


def _review_positive_detail_reasons(
    record: TargetedReliabilityPoint,
) -> tuple[str, ...]:
    generic_reasons = {
        "low_confidence",
        "plausible_nl_dropout",
        "score_breakdown_unavailable",
    }
    return tuple(
        reason for reason in record.risk_reasons if reason not in generic_reasons
    )


def _failure_modes(
    *,
    active_tag: bool,
    primary_matches: tuple[CandidateMatch, ...],
    untargeted_positive_count: int,
    coverage_minimum: int,
    family_mean_rt_delta: float | None,
    sample_rt_median_abs_delta: float | None,
    sample_rt_p95_abs_delta: float | None,
    paired_area_n: int,
    pearson: float | None,
    spearman: float | None,
    thresholds: BenchmarkThresholds,
) -> tuple[str, ...]:
    if not active_tag:
        return ("FALSE_POSITIVE_TAG",) if primary_matches else ()
    failures: list[str] = []
    if len(primary_matches) == 0:
        failures.append("MISS")
    elif len(primary_matches) > 1:
        failures.append("SPLIT")
    if len(primary_matches) != 1:
        return tuple(failures)
    if untargeted_positive_count < coverage_minimum:
        failures.append("COVERAGE")
    if family_mean_rt_delta is None or abs(family_mean_rt_delta) > (
        thresholds.mean_rt_delta_max_min
    ):
        failures.append("DRIFT")
    if sample_rt_median_abs_delta is None or sample_rt_median_abs_delta > (
        thresholds.sample_rt_median_abs_delta_max_min
    ):
        failures.append("DRIFT")
    if sample_rt_p95_abs_delta is None or sample_rt_p95_abs_delta > (
        thresholds.sample_rt_p95_abs_delta_max_min
    ):
        failures.append("DRIFT")
    if paired_area_n < 3:
        failures.append("AREA_INSUFFICIENT")
    elif (
        pearson is None
        or pearson < thresholds.log_area_pearson_min
        or spearman is None
        or spearman < thresholds.log_area_spearman_min
    ):
        failures.append("AREA_MISMATCH")
    return tuple(dict.fromkeys(failures))


def _note(
    active_tag: bool,
    failure_modes: tuple[str, ...],
    warning_modes: tuple[str, ...],
) -> str:
    if not active_tag and not failure_modes:
        return "inactive tag excluded"
    if warning_modes and not failure_modes:
        return "strict gate warning"
    if not failure_modes:
        return "strict gate passed"
    return "strict gate failed"
