"""One measured-anchor RT map for batch grouping, localization and replay.

ISTDs and agreed ordinary features establish the same cohort-median reference.
Robust local-linear regression estimates each injection's RT-dependent offset;
it does not subtract a local injection trend or change measured peaks/areas.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import median

import numpy as np

from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner


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

    @property
    def reliable_observed_range(self) -> tuple[float, float] | None:
        if not self.knots:
            return None
        return self.knots[0][0], self.knots[-1][0]

    def calibrated_rt(self, observed_rt_min: float) -> float | None:
        """Common-coordinate RT; None outside this sample's anchor span."""
        return interpolate_calibrated_rt_min(observed_rt_min, self.knots)


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

    Ordinary correspondences need two peers, not a batch-size activation gate.
    Measured identity anchors need no ordinary-feature prevalence assumption.
    Predicted, interpolated or backfilled coordinates are never training data.
    Timeline facts explain measured perturbation; they do not fit the map.
    """
    samples = tuple(sorted(set(sample_stems)))
    knots, observations_by_knot, standards, ordinary_samples = _anchor_coordinates(
        detected_rt_by_feature, samples, sample_cohorts, measured_istds
    )
    sample_knots: dict[str, tuple[tuple[float, float], ...]] = {}
    anchor_counts: dict[str, int] = {}
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
            else _fit_sample_knots(points, policy.loess_fraction)
        )
        anchor_counts[sample] = len({x for x, _ in points})
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
            for observed in by_cohort.values():
                if len(observed) < (1 if is_standard else 3):
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


def _fit_sample_knots(
    points: Sequence[tuple[float, float]], fraction: float
) -> tuple[tuple[float, float], ...]:
    # Multiple ion views at one acquired position are not independent votes.
    by_rt: dict[float, list[float]] = defaultdict(list)
    for observed, reference in points:
        by_rt[observed].append(reference)
    locations = sorted((x, float(median(values))) for x, values in by_rt.items())
    if len(locations) < 2:
        return tuple(locations)
    x, reference = np.asarray(locations, dtype=float).T
    if len(locations) == 2:
        return tuple(locations)
    y = reference - x
    dx = x[None, :] - x[:, None]
    count = min(len(x), max(4, math.ceil(len(x) * fraction)))
    radius = np.partition(abs(dx), count - 1, axis=1)[:, count - 1]
    radius = np.maximum(radius, np.finfo(float).eps)
    z = dx / radius[:, None]
    kernel = np.maximum(0, 1 - np.minimum(abs(z) / 1.000001, 1) ** 3) ** 3
    robust = np.ones(len(x))
    fitted = y.copy()
    for iteration in range(4):
        weights = kernel * robust[None, :]
        sw = weights.sum(axis=1)
        sx = (weights * z).sum(axis=1)
        sxx = (weights * z * z).sum(axis=1)
        sy, sxy = weights @ y, (weights * z) @ y
        determinant = sw * sxx - sx * sx
        supported = robust > 0
        fallback = np.interp(x, x[supported], y[supported])
        fitted = np.divide(sy, sw, out=fallback, where=sw > 0)
        ranked = determinant > np.finfo(float).eps * np.maximum(sw * sxx, 1)
        fitted[ranked] = (
            sy[ranked] * sxx[ranked] - sxy[ranked] * sx[ranked]
        ) / determinant[ranked]
        if iteration == 3:
            break
        residual = y - fitted
        if float(np.max(abs(residual))) < 1e-10:
            break
        scale = max(float(np.median(abs(residual))), 1e-10)
        u = abs(residual) / (6 * scale)
        robust = np.maximum(0, 1 - u * u) ** 2
        robust[u >= 1] = 0
    supported_x = x[robust > 0]
    return tuple(
        (float(a), float(b))
        for a, b in zip(x, x + fitted, strict=True)
        if supported_x[0] <= a <= supported_x[-1]
    )


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
            if not group:
                continue
            key = min(owner.owner_id for owner in group)
            self._observations[key] = {
                owner.sample_stem: owner.owner_apex_rt for owner in group
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
    return len(knots) >= 2 and all(
        left[0] < right[0] and left[1] < right[1]
        for left, right in zip(knots, knots[1:], strict=False)
    )


def _sample_status(knots: tuple[tuple[float, float], ...], usable: bool) -> str:
    if usable:
        return "calibrated"
    if len(knots) < 2:
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
