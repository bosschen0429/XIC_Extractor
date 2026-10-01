from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from xic_extractor.configuration.models import Target
from xic_extractor.extraction.result_assembly import reproject_extraction_result
from xic_extractor.peak_detection.selection_decision import (
    PeakHypothesisSelectionDecision,
)
from xic_extractor.target_sample_applicability import target_sample_exclusion_reasons

if TYPE_CHECKING:
    from xic_extractor.extraction.result_models import ExtractionResult, RunOutput

MIN_PAIRED_AREA_RATIO_REFERENCE_POINTS = 3
PAIRED_AREA_RATIO_REFERENCE_BASIS = "leave_one_sample_out_counted_area_over_istd_area"
PAIRED_AREA_RATIO_BASIS = (
    "leave_one_sample_out_median_plus_minus_3_scaled_mad_area_over_istd_area"
)
PAIRED_AREA_RATIO_ROBUST_BASIS = PAIRED_AREA_RATIO_BASIS
PAIRED_ISTD_RT_SUPPORT_MAX_DELTA_MIN = 1.0
PAIRED_AREA_RATIO_SUPPORT_REASON = "paired_area_ratio_support"
PAIRED_ISTD_RT_SUPPORT_REASON = "paired_istd_rt_within_1min_support"
_SCALED_MAD_FACTOR = 1.4826
_ROBUST_MAD_WINDOW_MULTIPLIER = 3.0


@dataclass(frozen=True)
class PairedAreaRatioReferencePoint:
    sample_name: str
    ratio: float


@dataclass(frozen=True)
class PairedAreaRatioAssessment:
    status: str
    observed_ratio: float | None = None
    reference_n: int = 0
    reference_min: float | None = None
    reference_median: float | None = None
    reference_max: float | None = None
    basis: str = ""
    robust_status: str = ""
    robust_reference_min: float | None = None
    robust_reference_median: float | None = None
    robust_reference_max: float | None = None
    robust_reference_mad: float | None = None
    robust_basis: str = ""

    @property
    def within_reference(self) -> bool:
        return self.status == "within_robust_range"


def apply_paired_area_ratio_projection(
    output: RunOutput,
    *,
    targets: Sequence[Target],
) -> RunOutput:
    targets_by_label = {target.label: target for target in targets}
    references = paired_area_ratio_references(output.file_results, targets=targets)
    for file_result in output.file_results:
        updated_results: dict[str, ExtractionResult] = {}
        for label, result in file_result.results.items():
            target = targets_by_label.get(label)
            if target is None:
                updated_results[label] = result
                continue
            updated_results[label] = result_with_paired_area_ratio_projection(
                result,
                target=target,
                sample_name=file_result.sample_name,
                file_results=file_result.results,
                references=references,
            )
        file_result.results = updated_results
    return output


def paired_area_ratio_references(
    file_results: Sequence[object],
    *,
    targets: Sequence[Target],
) -> dict[tuple[str, str], tuple[PairedAreaRatioReferencePoint, ...]]:
    references: dict[tuple[str, str], list[PairedAreaRatioReferencePoint]] = {}
    projection_targets = _paired_area_ratio_projection_targets(targets)
    for file_result in file_results:
        sample_name = str(getattr(file_result, "sample_name", ""))
        results = getattr(file_result, "results", {})
        if not isinstance(results, Mapping):
            continue
        for target in projection_targets:
            if target_sample_exclusion_reasons(target, sample_name):
                continue
            result = results.get(target.label)
            paired_istd_result = results.get(target.istd_pair)
            if not (
                _counted_detection(result)
                and _counted_or_finite_istd(paired_istd_result)
            ):
                continue
            area = _positive_float(getattr(result, "reported_peak_area", None))
            paired_area = _positive_float(
                getattr(paired_istd_result, "reported_peak_area", None)
            )
            if area is None or paired_area is None:
                continue
            references.setdefault((target.label, target.istd_pair), []).append(
                PairedAreaRatioReferencePoint(
                    sample_name=sample_name,
                    ratio=area / paired_area,
                )
            )
    return {key: tuple(value) for key, value in references.items()}


def _paired_area_ratio_projection_targets(
    targets: Sequence[Target],
) -> tuple[Target, ...]:
    return tuple(
        target
        for target in targets
        if not target.is_istd and target.istd_pair
    )


def result_with_paired_area_ratio_projection(
    result: ExtractionResult,
    *,
    target: Target,
    sample_name: str,
    file_results: Mapping[str, ExtractionResult],
    references: Mapping[tuple[str, str], tuple[PairedAreaRatioReferencePoint, ...]],
) -> ExtractionResult:
    if not _needs_paired_area_ratio_support(result, target, sample_name):
        return result
    paired_istd_result = file_results.get(target.istd_pair)
    if not _paired_istd_rt_within_delta(result, paired_istd_result):
        return result
    assessment = assess_paired_area_ratio(
        result,
        target=target,
        sample_name=sample_name,
        paired_istd_result=paired_istd_result,
        references=references,
    )
    if not assessment.within_reference:
        return result
    selection_decision = _selection_decision_with_run_level_pair_support(result)
    if selection_decision is None:
        return result
    return reproject_extraction_result(
        result,
        target=target,
        sample_name=sample_name,
        selection_decision=selection_decision,
    )


def assess_paired_area_ratio(
    result: object,
    *,
    target: Target,
    sample_name: str,
    paired_istd_result: object | None,
    references: Mapping[tuple[str, str], tuple[PairedAreaRatioReferencePoint, ...]],
) -> PairedAreaRatioAssessment:
    if target.is_istd or not target.istd_pair:
        return PairedAreaRatioAssessment(status="not_applicable")
    paired_area = _positive_float(
        getattr(paired_istd_result, "reported_peak_area", None)
    )
    if paired_area is None:
        return PairedAreaRatioAssessment(status="missing_istd_area")
    area = _positive_float(getattr(result, "reported_peak_area", None))
    if area is None:
        return PairedAreaRatioAssessment(status="missing_target_area")
    observed = area / paired_area
    reference = tuple(
        point.ratio
        for point in references.get((target.label, target.istd_pair), ())
        if point.sample_name != sample_name and _positive_float(point.ratio) is not None
    )
    if len(reference) < MIN_PAIRED_AREA_RATIO_REFERENCE_POINTS:
        return PairedAreaRatioAssessment(
            status="inconclusive",
            observed_ratio=observed,
            reference_n=len(reference),
            basis=PAIRED_AREA_RATIO_BASIS,
            robust_status="inconclusive",
            robust_basis=PAIRED_AREA_RATIO_ROBUST_BASIS,
        )
    return assess_paired_area_ratio_values(
        observed_ratio=observed,
        reference_ratios=reference,
    )


def assess_paired_area_ratio_values(
    *,
    observed_ratio: float,
    reference_ratios: Sequence[float],
) -> PairedAreaRatioAssessment:
    ordered = sorted(reference_ratios)
    ref_min = ordered[0]
    ref_max = ordered[-1]
    reference_median = _median(ordered)
    robust_median, robust_mad, robust_min, robust_max = _robust_ratio_window(ordered)
    status = (
        "within_robust_range"
        if robust_min <= observed_ratio <= robust_max
        else "outside_robust_range"
    )
    return PairedAreaRatioAssessment(
        status=status,
        observed_ratio=observed_ratio,
        reference_n=len(reference_ratios),
        reference_min=ref_min,
        reference_median=reference_median,
        reference_max=ref_max,
        basis=PAIRED_AREA_RATIO_BASIS,
        robust_status=status,
        robust_reference_min=robust_min,
        robust_reference_median=robust_median,
        robust_reference_max=robust_max,
        robust_reference_mad=robust_mad,
        robust_basis=PAIRED_AREA_RATIO_ROBUST_BASIS,
    )


def _needs_paired_area_ratio_support(
    result: ExtractionResult,
    target: Target,
    sample_name: str,
) -> bool:
    rt = _positive_float(result.reported_rt)
    area = _positive_float(result.reported_peak_area)
    if (
        target.is_istd
        or not target.istd_pair
        or target_sample_exclusion_reasons(target, sample_name)
        or _counted_detection(result)
        or _has_selected_envelope_not_counted_guard(result)
        or getattr(result, "role", "").upper() != "ANALYTE"
        or rt is None
        or area is None
        or not (target.rt_min <= rt <= target.rt_max)
        or getattr(result, "quality_flags", ())
    ):
        return False
    if getattr(result, "nl_token", None) not in {"NL_FAIL", "NO_MS2"}:
        return False
    support = _support_reasons(result)
    return "ms1_coherent" in support


def _selection_decision_with_run_level_pair_support(
    result: ExtractionResult,
) -> PeakHypothesisSelectionDecision | None:
    decision = result.selection_decision
    if decision is None:
        return None
    support = tuple(
        dict.fromkeys(
            (
                *decision.support_reasons,
                PAIRED_ISTD_RT_SUPPORT_REASON,
                PAIRED_AREA_RATIO_SUPPORT_REASON,
            )
        )
    )
    evidence_sources = tuple(
        dict.fromkeys(
            (
                *decision.evidence_sources,
                "run_level_paired_istd_rt",
                "run_level_paired_area_ratio",
            )
        )
    )
    return replace(
        decision,
        support_reasons=support,
        evidence_sources=evidence_sources,
        legacy_projection_status="successor_owned",
        compatibility_oracle="successor_evidence_decision_semantics",
    )


def _support_reasons(result: ExtractionResult) -> set[str]:
    reasons: set[str] = set()
    decision = result.selection_decision
    if decision is not None:
        reasons.update(decision.support_reasons)
    projection = result.targeted_product_projection
    if projection is not None:
        reasons.update(projection.support_reasons)
    selected = result.selected_hypothesis
    semantics = (
        selected.evidence.decision_semantics if selected is not None else None
    )
    if semantics is not None:
        reasons.update(semantics.support_reasons)
    return reasons


def _has_selected_envelope_not_counted_guard(result: ExtractionResult) -> bool:
    decision = result.selection_decision
    if decision is None:
        return False
    return any(
        reason.startswith("selected_envelope_boundary_")
        for reason in decision.not_counted_reasons
    )


def _paired_istd_rt_within_delta(
    result: ExtractionResult,
    paired_istd_result: object | None,
) -> bool:
    rt = _positive_float(getattr(result, "reported_rt", None))
    paired_rt = _positive_float(getattr(paired_istd_result, "reported_rt", None))
    if paired_rt is None:
        paired_rt = _positive_float(
            getattr(result.peak_result, "paired_istd_anchor_rt", None)
        )
    if rt is None or paired_rt is None:
        return False
    return abs(rt - paired_rt) <= PAIRED_ISTD_RT_SUPPORT_MAX_DELTA_MIN


def _counted_detection(result: object | None) -> bool:
    projection = getattr(result, "targeted_product_projection", None)
    if projection is None:
        return False
    return bool(getattr(projection, "counted_detection", False))


def _counted_or_finite_istd(result: object | None) -> bool:
    if _counted_detection(result):
        return True
    return (
        _positive_float(getattr(result, "reported_rt", None)) is not None
        and _positive_float(getattr(result, "reported_peak_area", None)) is not None
    )


def _positive_float(value: object | None) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2.0


def _robust_ratio_window(values: Sequence[float]) -> tuple[float, float, float, float]:
    median = _median(values)
    mad = _median(tuple(abs(value - median) for value in values))
    half_width = _ROBUST_MAD_WINDOW_MULTIPLIER * _SCALED_MAD_FACTOR * mad
    return median, mad, median - half_width, median + half_width
