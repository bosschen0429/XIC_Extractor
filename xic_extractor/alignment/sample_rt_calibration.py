"""One measured-anchor RT map for batch grouping, localization and replay.

ISTDs and agreed ordinary features establish the same cohort-median reference.
Robust local-linear regression estimates each injection's RT-dependent offset;
bounded identity landmarks retain local measured evidence without moving the
neighboring fitted positions. Measured peaks and areas remain unchanged.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from statistics import median

import numpy as np

from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner

# Operational support floors, not a universal mathematical LOESS minimum.
# Count acquired RT positions, not ion aliases, interpolation knots or samples.
_MIN_LOESS_POSITIONS = 8
_MIN_ORDINARY_COHORT_FRACTION = 0.5
_ROBUST_RESIDUAL_CUTOFF = 6.0


@dataclass(frozen=True)
class RtCalibrationPolicy:
    loess_fraction: float = 0.3
    front_knot_count: int = 2
    perturbation_mad_multiplier: float = 5.0
    perturbation_min_shift_min: float = 0.1

    def __post_init__(self) -> None:
        if not 0 < self.loess_fraction <= 1:
            raise ValueError("loess_fraction must be in (0, 1]")
        if self.front_knot_count < 1:
            raise ValueError("front_knot_count must be at least 1")
        values = (
            self.perturbation_mad_multiplier,
            self.perturbation_min_shift_min,
        )
        if not all(math.isfinite(v) and v >= 0 for v in values):
            raise ValueError("calibration tolerances must be finite and nonnegative")


@dataclass(frozen=True)
class PriorInjection:
    """Injection-timeline context that can explain a measured front shift."""

    pre_flagged: bool
    # Previous run is a short method under the timeline policy, at any idle.
    after_short_run: bool
    # The latest full-length run before this one used another method.
    after_method_change: bool


@dataclass(frozen=True)
class CalibrationKnot:
    """One measured correspondence's same-cohort reference position."""

    knot_id: str
    reference_rt_min: float
    feature_ids: tuple[str, ...]
    sample_count: int


@dataclass(frozen=True)
class RtEstimateSupport:
    """Frozen, distinct qualified observations; never reconstructed from knots."""

    positions_min: tuple[float, ...]
    median_shift_min: float
    shift_spread_min: float
    fit_spread_min: float | None


@dataclass(frozen=True)
class RtEstimate:
    """One coordinate answer and its measured support, not a probability."""

    rt_min: float
    method: str
    anchor_count: int | None = None
    anchor_range_min: tuple[float, float] | None = None
    residual_spread_min: float | None = None
    nearest_anchor_distance_min: float | None = None
    outside_anchor_range_min: float | None = None
    fit_status: str | None = None

    @property
    def rt_weight(self) -> float:
        # Weak coordinates remain usable, with half the RT influence. This is
        # separate from measured signal quality and does not downweight mass.
        return 0.5 if self.method in ("median_shift", "boundary_shift") else 1.0


@dataclass(frozen=True)
class SampleRtCalibration:
    sample_stem: str
    status: str
    # (observed_rt_min, reference_rt_min), strictly increasing in both.
    knots: tuple[tuple[float, float], ...]
    front_shift_min: float | None
    prior_injection: PriorInjection | None
    perturbation_status: str
    anchor_count: int = 0
    # (label, observed, reference, fitted-minus-reference), all RTs in minutes.
    standard_residuals: tuple[tuple[str, float, float, float], ...] = ()
    estimate_support: RtEstimateSupport | None = None

    @property
    def reliable_observed_range(self) -> tuple[float, float] | None:
        if not self.knots:
            return None
        return self.knots[0][0], self.knots[-1][0]

    def calibrated_rt(self, observed_rt_min: float) -> float | None:
        """Common-coordinate RT; None outside this sample's anchor span."""
        return interpolate_calibrated_rt_min(observed_rt_min, self.knots)

    def estimate_rt(self, observed_rt_min: float) -> RtEstimate | None:
        """Prefer the bounded fit; use explicit constant shifts where it cannot answer.

        Outside a usable curve, retain its nearest endpoint's offset. Unlike
        splicing in the global median offset, this preserves continuity, order
        and a unique inverse while leaving every supported coordinate unchanged.
        Historical maps without observation facts never acquire new support.
        """
        calibrated = self.calibrated_rt(observed_rt_min)
        support = self.estimate_support
        if calibrated is not None:
            if support is None:
                return RtEstimate(calibrated, "retained_map")
            method = "loess"
        elif support is None:
            return None
        elif self.knots:
            observed, reference = (
                self.knots[0] if observed_rt_min < self.knots[0][0] else self.knots[-1]
            )
            calibrated = observed_rt_min + reference - observed
            method = "boundary_shift"
        else:
            calibrated = observed_rt_min + support.median_shift_min
            method = "median_shift"
        lower, upper = (
            (self.knots[0][0], self.knots[-1][0])
            if self.knots
            else (support.positions_min[0], support.positions_min[-1])
        )
        index = bisect_right(support.positions_min, observed_rt_min)
        nearest = min(
            abs(observed_rt_min - point)
            for point in support.positions_min[max(0, index - 1) : index + 1]
        )
        return RtEstimate(
            calibrated,
            method,
            len(support.positions_min),
            (support.positions_min[0], support.positions_min[-1]),
            support.shift_spread_min
            if method == "median_shift"
            else support.fit_spread_min,
            nearest,
            max(0.0, lower - observed_rt_min, observed_rt_min - upper),
            self.status,
        )

    def estimate_observed_rt(self, common_rt_min: float) -> RtEstimate | None:
        """Invert the same piecewise map; never choose a branch by candidate fit."""
        observed = interpolate_calibrated_rt_min(
            common_rt_min, tuple((reference, rt) for rt, reference in self.knots)
        )
        if observed is None:
            if self.estimate_support is None:
                return None
            if self.knots:
                raw, reference = (
                    self.knots[0]
                    if common_rt_min < self.knots[0][1]
                    else self.knots[-1]
                )
                observed = common_rt_min - reference + raw
            else:
                observed = common_rt_min - self.estimate_support.median_shift_min
        estimate = self.estimate_rt(observed)
        return replace(estimate, rt_min=observed) if estimate is not None else None


def interpolate_calibrated_rt_min(
    value: float, knots: Sequence[Sequence[float]]
) -> float | None:
    """Interpolate an owner-validated monotone map without extrapolation."""
    if len(knots) < 2 or not knots[0][0] <= value <= knots[-1][0]:
        return None
    right = min(bisect_right(knots, value, key=lambda knot: knot[0]), len(knots) - 1)
    left_x, left_y = knots[right - 1]
    right_x, right_y = knots[right]
    return left_y + (value - left_x) / (right_x - left_x) * (right_y - left_y)


@dataclass(frozen=True)
class BatchRtCalibration:
    policy: RtCalibrationPolicy
    knots: tuple[CalibrationKnot, ...]
    samples: Mapping[str, SampleRtCalibration]
    front_shift_center_min: float | None
    front_shift_mad_min: float | None
    perturbation_tolerance_min: float | None

    @classmethod
    def from_coordinate_knots(
        cls, knots_by_sample: Sequence[tuple[str, tuple[tuple[float, float], ...]]]
    ) -> BatchRtCalibration:
        """Retain an already chosen map without claiming a new fit or perturbation."""
        return cls(
            policy=RtCalibrationPolicy(),
            knots=(),
            samples={
                sample: SampleRtCalibration(
                    sample, "calibrated", knots, None, None, "unmeasured"
                )
                for sample, knots in knots_by_sample
            },
            front_shift_center_min=None,
            front_shift_mad_min=None,
            perturbation_tolerance_min=None,
        )

    def corrected_rt_min(self, owner: SampleLocalMS1Owner) -> float:
        """Use a bounded sample calibration, retaining raw RT outside its span."""
        return self.common_rt_min(owner.sample_stem, owner.owner_apex_rt)

    def has_support(self, sample_stem: str) -> bool:
        sample = self.samples.get(sample_stem)
        return sample is not None and bool(sample.knots)

    def common_rt_min(self, sample_stem: str, observed_rt: float) -> float:
        sample = self.samples.get(sample_stem)
        calibrated = sample.calibrated_rt(observed_rt) if sample else None
        return observed_rt if calibrated is None else calibrated

    def offset_min(self, sample_stem: str, common_rt: float) -> float:
        sample = self.samples.get(sample_stem)
        observed = (
            interpolate_calibrated_rt_min(
                common_rt,
                tuple((reference, rt) for rt, reference in sample.knots),
            )
            if sample
            else None
        )
        return 0.0 if observed is None else observed - common_rt


def build_batch_rt_calibration(
    detected_rt_by_feature: Mapping[str, Mapping[str, float]],
    *,
    sample_stems: Sequence[str],
    prior_injections: Mapping[str, PriorInjection],
    policy: RtCalibrationPolicy,
    sample_cohorts: Mapping[str, str] | None = None,
    measured_istds: Sequence[tuple[str, str, float]] = (),
) -> BatchRtCalibration:
    """Fit one map from same-cohort measured correspondences and ISTDs.

    Ordinary correspondences need at least half their cohort and two peers.
    Measured identity anchors need a peer, not ordinary-feature prevalence.
    Predicted, interpolated or backfilled coordinates are never training data.
    Timeline facts explain measured perturbation; they do not fit the map.
    """
    samples = tuple(sorted(set(sample_stems)))
    knots, observations_by_knot, standards, ordinary_samples = _anchor_coordinates(
        detected_rt_by_feature, samples, sample_cohorts, measured_istds
    )
    sample_knots: dict[str, tuple[tuple[float, float], ...]] = {}
    anchor_counts: dict[str, int] = {}
    estimate_support: dict[str, RtEstimateSupport | None] = {}
    for sample in samples:
        points = tuple(
            (
                observations_by_knot[knot.knot_id][sample],
                knot.reference_rt_min,
            )
            for knot in knots
            if sample in observations_by_knot[knot.knot_id]
        )
        # Conflicting measured standard order cannot be resolved by fitting the
        # same sparse standards to themselves. Ordinary corroboration is a
        # distinct source; without it, keep the contradiction unavailable.
        ordered_points = tuple(sorted(points))
        sample_knots[sample] = (
            ordered_points
            if sample not in ordinary_samples and not _is_usable(ordered_points)
            else _fit_sample_knots(
                points,
                policy.loess_fraction,
                standard_points=tuple(
                    (observed, reference)
                    for _, observed, reference in standards.get(sample, ())
                ),
            )
        )
        anchor_counts[sample] = len({x for x, _ in points})
        locations = _distinct_anchor_positions(points)
        if len(locations) < 2:
            estimate_support[sample] = None
            continue
        shifts = [reference - observed for observed, reference in locations]
        shift = float(median(shifts))
        fitted_knots = sample_knots[sample] if _is_usable(sample_knots[sample]) else ()
        fit_residuals = [
            abs(fitted - reference)
            for observed, reference in locations
            if (fitted := interpolate_calibrated_rt_min(observed, fitted_knots))
            is not None
        ]
        estimate_support[sample] = RtEstimateSupport(
            tuple(observed for observed, _ in locations),
            shift,
            float(median(abs(value - shift) for value in shifts)),
            float(median(fit_residuals)) if fit_residuals else None,
        )
    usable = {s for s, k in sample_knots.items() if _is_usable(k)}

    front_knots = knots[: policy.front_knot_count]
    front_shifts: dict[str, float] = {}
    for sample in sorted(usable):
        observed = [
            observations_by_knot[knot.knot_id][sample] - knot.reference_rt_min
            for knot in front_knots
            if sample in observations_by_knot[knot.knot_id]
        ]
        if len(observed) == len(front_knots):
            front_shifts[sample] = float(median(observed))
    center = float(median(front_shifts.values())) if front_shifts else None
    mad = (
        float(median(abs(v - center) for v in front_shifts.values()))
        if center is not None
        else None
    )
    tolerance = (
        max(policy.perturbation_mad_multiplier * mad, policy.perturbation_min_shift_min)
        if mad is not None
        else None
    )

    calibrations: dict[str, SampleRtCalibration] = {}
    for sample in samples:
        prior = prior_injections.get(sample)
        front = front_shifts.get(sample)
        calibrations[sample] = SampleRtCalibration(
            sample_stem=sample,
            status=_sample_status(sample_knots[sample], sample in usable),
            knots=sample_knots[sample] if sample in usable else (),
            front_shift_min=front,
            prior_injection=prior,
            perturbation_status=_perturbation_status(prior, front, center, tolerance),
            anchor_count=anchor_counts[sample],
            estimate_support=estimate_support[sample],
            standard_residuals=tuple(
                (label, observed, reference, fitted - reference)
                for label, observed, reference in standards.get(sample, ())
                if sample in usable
                and (
                    fitted := interpolate_calibrated_rt_min(
                        observed, sample_knots[sample]
                    )
                )
                is not None
            ),
        )
    return BatchRtCalibration(
        policy=policy,
        knots=knots,
        samples=calibrations,
        front_shift_center_min=center,
        front_shift_mad_min=mad,
        perturbation_tolerance_min=tolerance,
    )


def _anchor_coordinates(
    detected_rt_by_feature: Mapping[str, Mapping[str, float]],
    samples: tuple[str, ...],
    sample_cohorts: Mapping[str, str] | None,
    measured_istds: Sequence[tuple[str, str, float]],
) -> tuple[
    tuple[CalibrationKnot, ...],
    dict[str, dict[str, float]],
    dict[str, list[tuple[str, float, float]]],
    frozenset[str],
]:
    sample_set = set(samples)
    cohorts = (
        dict(sample_cohorts)
        if sample_cohorts is not None
        else dict.fromkeys(samples, "")
    )
    cohort_sizes: dict[str, int] = defaultdict(int)
    for sample in samples:
        if sample in cohorts:
            cohort_sizes[cohorts[sample]] += 1
    known: dict[str, dict[str, float]] = defaultdict(dict)
    for label, sample, observed_rt in measured_istds:
        known[label][sample] = observed_rt
    anchors: list[tuple[float, str, dict[str, float], bool]] = []
    seen: set[tuple[tuple[str, float], ...]] = set()
    for is_standard, features in ((False, detected_rt_by_feature), (True, known)):
        for feature_id, rt_by_sample in sorted(features.items()):
            by_cohort: dict[str, dict[str, float]] = defaultdict(dict)
            for sample, rt in rt_by_sample.items():
                if sample in sample_set and sample in cohorts and math.isfinite(rt):
                    by_cohort[cohorts[sample]][sample] = rt
            for cohort, observed in by_cohort.items():
                required = (
                    2
                    if is_standard
                    else max(
                        3,
                        math.ceil(cohort_sizes[cohort] * _MIN_ORDINARY_COHORT_FRACTION),
                    )
                )
                if len(observed) < required:
                    continue
                signature = tuple(sorted(observed.items()))
                if not is_standard and signature in seen:
                    continue
                seen.add(signature)
                anchors.append(
                    (
                        float(median(observed.values())),
                        feature_id,
                        observed,
                        is_standard,
                    )
                )
    anchors.sort(key=lambda item: (item[0], item[1], tuple(sorted(item[2]))))
    knots: list[CalibrationKnot] = []
    observations_by_knot: dict[str, dict[str, float]] = {}
    standards: dict[str, list[tuple[str, float, float]]] = defaultdict(list)
    ordinary_samples: set[str] = set()
    for index, (reference, feature, observed, is_standard) in enumerate(anchors):
        knot_id = f"K{index + 1:03d}"
        knots.append(
            CalibrationKnot(
                knot_id=knot_id,
                reference_rt_min=reference,
                feature_ids=(feature,),
                sample_count=len(observed),
            )
        )
        observations_by_knot[knot_id] = observed
        if is_standard:
            for sample, rt in observed.items():
                standards[sample].append((feature, rt, reference))
        else:
            ordinary_samples.update(observed)
    return (
        tuple(knots),
        observations_by_knot,
        dict(standards),
        frozenset(ordinary_samples),
    )


def _distinct_anchor_positions(
    points: Sequence[tuple[float, float]],
) -> tuple[tuple[float, float], ...]:
    # Both strong and weak estimates give each acquired position one vote.
    by_rt: dict[float, list[float]] = defaultdict(list)
    for observed, reference in points:
        by_rt[observed].append(reference)
    return tuple(sorted((x, float(median(values))) for x, values in by_rt.items()))


def _fit_sample_knots(
    points: Sequence[tuple[float, float]],
    fraction: float,
    *,
    standard_points: Sequence[tuple[float, float]] = (),
) -> tuple[tuple[float, float], ...]:
    locations = _distinct_anchor_positions(points)
    if len(locations) < _MIN_LOESS_POSITIONS:
        return ()
    x, reference = np.asarray(locations, dtype=float).T
    y = reference - x
    dx = x[None, :] - x[:, None]
    count = min(len(x), max(_MIN_LOESS_POSITIONS, math.ceil(len(x) * fraction)))
    base_radius = np.partition(abs(dx), count - 1, axis=1)[:, count - 1]
    robust = np.ones(len(x))
    fitted = y.copy()
    for iteration in range(4):
        supported = robust > 0
        support_count = int(np.count_nonzero(supported))
        if support_count < 2 or (
            iteration == 3 and support_count < _MIN_LOESS_POSITIONS
        ):
            return ()
        # Robust iterations can readmit temporarily downweighted points. Only
        # the final map must retain eight; do not stop at a transient rejection.
        # Expand a neighborhood when rejected anchors would fill its support.
        support_index = min(_MIN_LOESS_POSITIONS, support_count) - 1
        support_radius = np.partition(abs(dx[:, supported]), support_index, axis=1)[
            :, support_index
        ]
        radius = np.maximum(
            np.maximum(base_radius, support_radius), np.finfo(float).eps
        )
        z = dx / radius[:, None]
        kernel = np.maximum(0, 1 - np.minimum(abs(z) / 1.000001, 1) ** 3) ** 3
        weights = kernel * robust[None, :]
        sw = weights.sum(axis=1)
        sx = (weights * z).sum(axis=1)
        sxx = (weights * z * z).sum(axis=1)
        sy, sxy = weights @ y, (weights * z) @ y
        determinant = sw * sxx - sx * sx
        ranked = determinant > np.finfo(float).eps * np.maximum(sw * sxx, 1)
        if not np.all(ranked):
            return ()  # No silent constant/interpolation substitute for LOESS.
        fitted = (sy * sxx - sxy * sx) / determinant
        if iteration == 3:
            break
        residual = y - fitted
        if float(np.max(abs(residual))) < 1e-10:
            break
        scale = max(float(np.median(abs(residual))), 1e-10)
        u = abs(residual) / (_ROBUST_RESIDUAL_CUTOFF * scale)
        robust = np.maximum(0, 1 - u * u) ** 2
        robust[u >= 1] = 0
    supported_x = x[robust > 0]
    if len(supported_x) < _MIN_LOESS_POSITIONS:
        return ()
    knots = tuple(
        (float(a), float(b))
        for a, b in zip(x, x + fitted, strict=True)
        if supported_x[0] <= a <= supported_x[-1]
    )
    # Identity cannot rescue an unsupported/non-monotone trend or extend its
    # span. Its local influence uses the same residual scale as robust fitting,
    # estimated only from retained positions so rejected pairs cannot inflate it.
    if not standard_points or not _is_usable(knots):
        return knots
    limit = _ROBUST_RESIDUAL_CUTOFF * float(np.median(abs((y - fitted)[robust > 0])))
    return _bound_standard_landmarks(knots, standard_points, limit)


def _bound_standard_landmarks(
    knots: tuple[tuple[float, float], ...],
    standard_points: Sequence[tuple[float, float]],
    adjustment_limit: float,
) -> tuple[tuple[float, float], ...]:
    """Retain identity locally, without extra support or a second RT map."""
    by_rt: dict[float, list[float]] = defaultdict(list)
    for observed, reference in standard_points:
        by_rt[observed].append(reference)
    proposed = [reference for _, reference in knots]
    for index, (observed, reference) in enumerate(knots):
        if observed not in by_rt:
            continue
        value = max(
            reference - adjustment_limit,
            min(float(median(by_rt[observed])), reference + adjustment_limit),
        )
        lower = knots[index - 1][1] if index else -math.inf
        upper = knots[index + 1][1] if index + 1 < len(knots) else math.inf
        if lower < value < upper:
            proposed[index] = value
    # Evaluate neighboring proposals together, not in label/insertion order.
    # Each proposal also fits between the original neighbors, so reverting a
    # conflicting proposal cannot invalidate an accepted one.
    result = []
    for index, (observed, reference) in enumerate(knots):
        lower = proposed[index - 1] if index else -math.inf
        upper = proposed[index + 1] if index + 1 < len(knots) else math.inf
        value = proposed[index] if lower < proposed[index] < upper else reference
        result.append((observed, value))
    return tuple(result)


class BatchRtCalibrationAccumulator:
    """Retain compact bootstrap anchors and fit once after the last partition."""

    def __init__(
        self, *, sample_stems: Sequence[str], sample_cohorts: Mapping[str, str]
    ) -> None:
        self._samples = tuple(sample_stems)
        self._cohorts = sample_cohorts
        self._observations: dict[str, dict[str, float]] = {}

    def add_groups(self, groups: Sequence[Sequence[SampleLocalMS1Owner]]) -> None:
        for group in groups:
            # These are upstream-owned observation facts, not a second peak
            # assessment. A retained MS1-only or ambiguous member may still be
            # grouped, but cannot establish other samples' RT coordinates.
            anchors = tuple(
                owner
                for owner in group
                if owner.primary_identity_event is not None
                and not owner.identity_conflict
                and not owner.is_provisional
                and owner.quantification_eligible
            )
            if not anchors:
                continue
            key = min(owner.owner_id for owner in anchors)
            self._observations[key] = {
                owner.sample_stem: owner.owner_apex_rt for owner in anchors
            }

    def freeze(
        self, *, measured_istds: Sequence[tuple[str, str, float]] = ()
    ) -> BatchRtCalibration:
        return build_batch_rt_calibration(
            self._observations,
            sample_stems=self._samples,
            sample_cohorts=self._cohorts,
            measured_istds=measured_istds,
            prior_injections={},
            policy=RtCalibrationPolicy(),
        )


def _is_usable(knots: tuple[tuple[float, float], ...]) -> bool:
    return len(knots) >= _MIN_LOESS_POSITIONS and all(
        left[0] < right[0] and left[1] < right[1]
        for left, right in zip(knots, knots[1:], strict=False)
    )


def _sample_status(knots: tuple[tuple[float, float], ...], usable: bool) -> str:
    if usable:
        return "calibrated"
    if len(knots) < _MIN_LOESS_POSITIONS:
        return "insufficient_anchors"
    return "non_monotonic_anchors"


def _perturbation_status(
    prior: PriorInjection | None,
    front_shift: float | None,
    center: float | None,
    tolerance: float | None,
) -> str:
    if front_shift is None or center is None or tolerance is None:
        return "unmeasured"
    if abs(front_shift - center) > tolerance:
        if prior is None:
            return "outlier_without_timeline"
        if prior.pre_flagged or prior.after_short_run or prior.after_method_change:
            return "confirmed"
        return "unexplained_outlier"
    if prior is not None and prior.pre_flagged:
        return "flagged_unconfirmed"
    return "not_perturbed"
