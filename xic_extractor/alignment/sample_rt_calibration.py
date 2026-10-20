"""Batch-level sample RT calibration onto a common anchor coordinate.

Anchors are features directly detected in nearly every sample, not only ISTDs.
Produces calibrated coordinates and perturbation confirmation. Diagnostic
grouping replay can use these coordinates; measured RTs and areas are preserved.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import median

from xic_extractor.alignment.drift_evidence import interpolate_calibrated_rt_min
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner


@dataclass(frozen=True)
class RtCalibrationPolicy:
    anchor_min_sample_fraction: float = 0.94
    knot_merge_tolerance_min: float = 0.1
    front_knot_count: int = 2
    perturbation_mad_multiplier: float = 5.0
    perturbation_min_shift_min: float = 0.1

    def __post_init__(self) -> None:
        if not 0 < self.anchor_min_sample_fraction <= 1:
            raise ValueError("anchor_min_sample_fraction must be in (0, 1]")
        if self.front_knot_count < 1:
            raise ValueError("front_knot_count must be at least 1")
        values = (
            self.knot_merge_tolerance_min,
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
    """Co-eluting anchor features collapsed to one reference position."""

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

    @property
    def reliable_observed_range(self) -> tuple[float, float] | None:
        if not self.knots:
            return None
        return self.knots[0][0], self.knots[-1][0]

    def calibrated_rt(self, observed_rt_min: float) -> float | None:
        """Common-coordinate RT; None outside this sample's anchor span."""
        return interpolate_calibrated_rt_min(observed_rt_min, self.knots)


@dataclass(frozen=True)
class BatchRtCalibration:
    policy: RtCalibrationPolicy
    knots: tuple[CalibrationKnot, ...]
    samples: Mapping[str, SampleRtCalibration]
    front_shift_center_min: float | None
    front_shift_mad_min: float | None
    perturbation_tolerance_min: float | None

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
) -> BatchRtCalibration:
    """Build piecewise sample knots from features detected batch-wide.

    ``detected_rt_by_feature`` holds directly detected apex RTs only; backfilled
    or inferred observations must not become anchors. The measured front shift
    decides a perturbation; the injection timeline only explains it.
    """
    samples = tuple(sorted(set(sample_stems)))
    knots, shifts_by_knot = _build_knots(detected_rt_by_feature, samples, policy)
    sample_knots: dict[str, tuple[tuple[float, float], ...]] = {}
    for sample in samples:
        sample_knots[sample] = tuple(
            (
                knot.reference_rt_min + shifts_by_knot[knot.knot_id][sample],
                knot.reference_rt_min,
            )
            for knot in knots
            if sample in shifts_by_knot[knot.knot_id]
        )
    usable = {s for s, k in sample_knots.items() if _is_usable(k)}

    front_knots = knots[: policy.front_knot_count]
    front_shifts: dict[str, float] = {}
    for sample in sorted(usable):
        observed = [
            shifts_by_knot[knot.knot_id][sample]
            for knot in front_knots
            if sample in shifts_by_knot[knot.knot_id]
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
        )
    return BatchRtCalibration(
        policy=policy,
        knots=knots,
        samples=calibrations,
        front_shift_center_min=center,
        front_shift_mad_min=mad,
        perturbation_tolerance_min=tolerance,
    )


def _build_knots(
    detected_rt_by_feature: Mapping[str, Mapping[str, float]],
    samples: tuple[str, ...],
    policy: RtCalibrationPolicy,
) -> tuple[tuple[CalibrationKnot, ...], dict[str, dict[str, float]]]:
    sample_set = set(samples)
    min_samples = math.ceil(policy.anchor_min_sample_fraction * len(samples))
    anchors: list[tuple[float, str, dict[str, float]]] = []
    for feature_id, rt_by_sample in detected_rt_by_feature.items():
        observed = {
            s: rt
            for s, rt in rt_by_sample.items()
            if s in sample_set and math.isfinite(rt)
        }
        if observed and len(observed) >= min_samples:
            anchors.append((float(median(observed.values())), feature_id, observed))
    anchors.sort(key=lambda item: (item[0], item[1]))

    # Isotope, adduct and fragment families of one compound share its RT; as
    # separate knots their per-sample order inverts and breaks monotonicity.
    groups: list[list[tuple[float, str, dict[str, float]]]] = []
    for anchor in anchors:
        if groups and anchor[0] - groups[-1][-1][0] <= policy.knot_merge_tolerance_min:
            groups[-1].append(anchor)
        else:
            groups.append([anchor])

    knots: list[CalibrationKnot] = []
    shifts_by_knot: dict[str, dict[str, float]] = {}
    for index, group in enumerate(groups):
        knot_id = f"K{index + 1:03d}"
        reference = float(median(ref for ref, _feature, _obs in group))
        shifts: dict[str, float] = {}
        for sample in samples:
            deltas = [obs[sample] - ref for ref, _f, obs in group if sample in obs]
            if deltas:
                shifts[sample] = float(median(deltas))
        knots.append(
            CalibrationKnot(
                knot_id=knot_id,
                reference_rt_min=reference,
                feature_ids=tuple(feature for _ref, feature, _obs in group),
                sample_count=len(shifts),
            )
        )
        shifts_by_knot[knot_id] = shifts
    return tuple(knots), shifts_by_knot


def _is_usable(knots: tuple[tuple[float, float], ...]) -> bool:
    return len(knots) >= 2 and all(
        left[0] < right[0] and left[1] < right[1]
        for left, right in zip(knots, knots[1:])
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
