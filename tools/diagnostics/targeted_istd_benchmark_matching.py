"""Target-to-alignment matching for the targeted ISTD benchmark."""

from __future__ import annotations

from tools.diagnostics.targeted_istd_benchmark_models import (
    ISOTOPE_SHIFT_DA,
    AlignmentFeature,
    BenchmarkThresholds,
    CandidateMatch,
    TargetDefinition,
    TargetedPoint,
)
from tools.diagnostics.targeted_istd_benchmark_stats import _mean


def _match_target_to_alignment(
    target: TargetDefinition,
    features: tuple[AlignmentFeature, ...],
    *,
    target_points: tuple[TargetedPoint, ...],
    thresholds: BenchmarkThresholds,
) -> tuple[CandidateMatch, ...]:
    target_mean_rt = _mean(point.rt for point in target_points if point.positive)
    exact_matches = _match_target_to_alignment_with_shift(
        target,
        features,
        target_mean_rt=target_mean_rt,
        thresholds=thresholds,
        mass_shift_da=0.0,
    )
    if any(
        match.include_in_primary_matrix for match in exact_matches
    ) or not _is_active_tag(target, thresholds):
        return tuple(sorted(exact_matches, key=_match_sort_key))
    isotope_matches = tuple(
        match
        for mass_shift_da in (ISOTOPE_SHIFT_DA, -ISOTOPE_SHIFT_DA)
        for match in _match_target_to_alignment_with_shift(
            target,
            features,
            target_mean_rt=target_mean_rt,
            thresholds=thresholds,
            mass_shift_da=mass_shift_da,
        )
    )
    isotope_matches = _best_isotope_shift_matches(isotope_matches)
    return tuple(sorted((*exact_matches, *isotope_matches), key=_match_sort_key))


def _match_target_to_alignment_with_shift(
    target: TargetDefinition,
    features: tuple[AlignmentFeature, ...],
    *,
    target_mean_rt: float | None,
    thresholds: BenchmarkThresholds,
    mass_shift_da: float,
) -> tuple[CandidateMatch, ...]:
    shifted_mz = target.mz + mass_shift_da
    shifted_product_mz = target.product_mz + mass_shift_da
    match_type = "exact" if mass_shift_da == 0.0 else "isotope_shift"
    matches: list[CandidateMatch] = []
    for feature in features:
        mz_delta_ppm = _ppm_delta(shifted_mz, feature.family_center_mz)
        if abs(mz_delta_ppm) > target.ppm_tol:
            continue
        product_delta_ppm = _ppm_delta(
            shifted_product_mz,
            feature.family_product_mz,
        )
        if abs(product_delta_ppm) > target.ppm_tol:
            continue
        loss_delta_da = feature.family_observed_neutral_loss_da - target.neutral_loss_da
        if abs(loss_delta_da) > thresholds.active_neutral_loss_tolerance_da:
            continue
        rt_delta_sec = _target_rt_delta_sec(
            feature.family_center_rt,
            target,
            target_mean_rt,
        )
        if abs(rt_delta_sec) > thresholds.match_rt_sec:
            continue
        matches.append(
            CandidateMatch(
                target_label=target.label,
                feature_id=feature.feature_id,
                include_in_primary_matrix=feature.include_in_primary_matrix,
                family_center_mz=feature.family_center_mz,
                family_center_rt=feature.family_center_rt,
                family_product_mz=feature.family_product_mz,
                family_observed_neutral_loss_da=(
                    feature.family_observed_neutral_loss_da
                ),
                mz_delta_ppm=mz_delta_ppm,
                rt_delta_sec=rt_delta_sec,
                product_delta_ppm=product_delta_ppm,
                loss_delta_da=loss_delta_da,
                mass_shift_da=mass_shift_da,
                match_type=match_type,
                distance_score=max(
                    abs(mz_delta_ppm) / target.ppm_tol,
                    abs(product_delta_ppm) / target.ppm_tol,
                    abs(rt_delta_sec) / thresholds.match_rt_sec,
                ),
            )
        )
    return tuple(sorted(matches, key=_match_sort_key))


def _best_isotope_shift_matches(
    matches: tuple[CandidateMatch, ...],
) -> tuple[CandidateMatch, ...]:
    primary_matches = tuple(
        match for match in matches if match.include_in_primary_matrix
    )
    if not primary_matches:
        return matches
    best_shift = min(
        {match.mass_shift_da for match in primary_matches},
        key=lambda shift: (
            min(
                match.distance_score
                for match in primary_matches
                if match.mass_shift_da == shift
            ),
            abs(shift),
        ),
    )
    return tuple(match for match in matches if match.mass_shift_da == best_shift)


def _target_rt_delta_sec(
    rt: float,
    target: TargetDefinition,
    targeted_mean_rt: float | None,
) -> float:
    if target.rt_min <= rt <= target.rt_max:
        return 0.0
    if targeted_mean_rt is not None:
        return (rt - targeted_mean_rt) * 60.0
    if rt < target.rt_min:
        return (rt - target.rt_min) * 60.0
    return (rt - target.rt_max) * 60.0


def _ppm_delta(reference: float, observed: float) -> float:
    denominator = max(abs(reference), 1e-12)
    return (observed - reference) / denominator * 1_000_000.0


def _match_sort_key(match: CandidateMatch) -> tuple[object, ...]:
    return (
        0 if match.include_in_primary_matrix else 1,
        match.distance_score,
        abs(match.rt_delta_sec),
        abs(match.mz_delta_ppm),
        match.feature_id,
    )


def _is_active_tag(
    target: TargetDefinition,
    thresholds: BenchmarkThresholds,
) -> bool:
    active_masses = (
        thresholds.active_neutral_loss_da,
        *thresholds.additional_active_neutral_loss_das,
    )
    return any(
        abs(target.neutral_loss_da - active_mass)
        <= thresholds.active_neutral_loss_tolerance_da
        for active_mass in active_masses
    )
