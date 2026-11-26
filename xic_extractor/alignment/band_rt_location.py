"""Locate missing samples using the accepted band's measured members."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np

from xic_extractor.alignment.rt_frame import RtFrame

_TRAJECTORY_NEIGHBORS = 12
_TREND_SCALE_FLOOR_MIN = 0.5 / 60.0
_TREND_TOLERANCE_MIN = 1e-8 / 60.0


@dataclass(frozen=True, slots=True)
class PeakLocation:
    query_rt: float
    basis: str
    observed: bool = False

    @property
    def observed_rt(self) -> float | None:
        """A forecast alone does not establish a different observed peak."""
        return self.query_rt if self.observed else None


@dataclass(frozen=True, slots=True)
class BackfillPeakLocation:
    """One trajectory location and its bounded empirical RT compatibility."""

    location: PeakLocation
    apex_tolerance_sec: float | None

    @property
    def reliable(self) -> bool:
        return self.apex_tolerance_sec is not None


@dataclass(frozen=True, slots=True)
class BandRtLocator:
    sample_apices: tuple[tuple[str, float], ...]
    _positions: Mapping[str, float] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_positions", dict(self.sample_apices))

    def paired_istd_locations(
        self,
        sample_stems: Sequence[str],
        *,
        reference_samples: frozenset[str],
        rt_frame: RtFrame,
        backfill_half_window_min: float,
        paired_istd_rts: Mapping[str, float],
    ) -> Mapping[str, BackfillPeakLocation]:
        """Localize missing samples from confirmed same-cohort paired offsets.

        Missing paired support supplies no location. Excessive peer scatter
        retains the paired coordinate but does not authorize recovery there.
        """
        paired_locations: dict[str, BackfillPeakLocation] = {}
        paired_samples = [
            sample
            for sample in sample_stems
            if sample in paired_istd_rts and sample not in self._positions
        ]
        paired_cohorts = {rt_frame.sample_cohorts.get(s) for s in paired_samples}
        offsets_by_cohort: dict[str | None, list[float]] = defaultdict(list)
        for peer, rt in self.sample_apices:
            cohort = rt_frame.sample_cohorts.get(peer)
            if (
                peer in reference_samples
                and peer in paired_istd_rts
                and cohort in paired_cohorts
            ):
                offsets_by_cohort[cohort].append(rt - paired_istd_rts[peer])
        paired_summaries = {}
        half_window_sec = backfill_half_window_min * 60.0
        for cohort, offsets in offsets_by_cohort.items():
            if len(offsets) < 2:
                continue
            residuals_sec = [
                abs(value - float(np.median(offsets[:i] + offsets[i + 1 :]))) * 60.0
                for i, value in enumerate(offsets)
            ]
            residual_p90 = float(np.quantile(residuals_sec, 0.9))
            paired_summaries[cohort] = (
                float(np.median(offsets)),
                min(max(12.0, residual_p90), half_window_sec)
                if residual_p90 <= half_window_sec
                else None,
            )
        for sample in paired_samples:
            summary = paired_summaries.get(rt_frame.sample_cohorts.get(sample))
            if summary is not None:
                offset, tolerance = summary
                paired_locations[sample] = BackfillPeakLocation(
                    PeakLocation(
                        max(0.0, paired_istd_rts[sample] + offset), "paired_istd"
                    ),
                    tolerance,
                )
        return paired_locations

    def backfill_locations(
        self,
        sample_stems: Sequence[str],
        *,
        reference_samples: frozenset[str],
        rt_frame: RtFrame,
        backfill_half_window_min: float,
        paired_istd_rts: Mapping[str, float] | None = None,
        drift_offsets: Mapping[str, float] | None = None,
    ) -> Mapping[str, BackfillPeakLocation]:
        """Prepare row residuals once, not once per missing sample.

        ``drift_offsets`` is the group's modeled drift per sample; forecasts
        are fitted with it removed and the target's drift added back.

        Only direct reference samples contribute held-out residuals. The same
        trajectory estimator supplies both the prediction and its empirical
        check; observed MS1 correspondence is not replaced by that forecast.
        Scatter larger than the acquisition half-window means localization is
        unreliable, not permission to enlarge the accepted RT interval.
        A forecast extended by MS1-only positions must also agree with a
        supported direct-reference trend; disagreement leaves it unlocated.
        """
        paired_locations = (
            self.paired_istd_locations(
                sample_stems,
                reference_samples=reference_samples,
                rt_frame=rt_frame,
                backfill_half_window_min=backfill_half_window_min,
                paired_istd_rts=paired_istd_rts,
            )
            if paired_istd_rts is not None
            else {}
        )
        locations = {
            sample: self.locate(
                sample,
                rt_frame=rt_frame,
                backfill_half_window_min=backfill_half_window_min,
                drift_offsets=drift_offsets,
            )
            for sample in sample_stems
            if sample not in paired_locations
        }
        needed_cohorts = {
            rt_frame.sample_cohorts[sample]
            for sample, location in locations.items()
            if not location.observed and sample in rt_frame.sample_cohorts
        }
        tolerance_by_cohort = self._empirical_tolerances(
            reference_samples=reference_samples,
            needed_cohorts=needed_cohorts,
            rt_frame=rt_frame,
            backfill_half_window_min=backfill_half_window_min,
            drift_offsets=drift_offsets,
        )
        result = {
            sample: BackfillPeakLocation(
                location,
                min(12.0, backfill_half_window_min * 60.0)
                if location.observed
                else tolerance_by_cohort.get(rt_frame.sample_cohorts.get(sample, "")),
            )
            for sample, location in locations.items()
        }
        reference_points = tuple(
            point for point in self.sample_apices if point[0] in reference_samples
        )
        if len(reference_points) < len(self.sample_apices):
            forecast_cohorts = {
                rt_frame.sample_cohorts[sample]
                for sample, localized in result.items()
                if localized.reliable
                and not localized.location.observed
                and sample in rt_frame.sample_cohorts
            }
            reference_orders: dict[str, set[int]] = defaultdict(set)
            for sample, _ in reference_points:
                cohort = rt_frame.sample_cohorts.get(sample)
                order = rt_frame.injection_order.get(sample)
                if cohort in forecast_cohorts and order is not None:
                    assert cohort is not None
                    reference_orders[cohort].add(order)
            comparable_cohorts = {
                cohort
                for cohort, orders in reference_orders.items()
                if len(orders) >= 3
            }
            reference = BandRtLocator(reference_points)
            reference_tolerances = reference._empirical_tolerances(
                reference_samples=reference_samples,
                needed_cohorts=comparable_cohorts,
                rt_frame=rt_frame,
                backfill_half_window_min=backfill_half_window_min,
                drift_offsets=drift_offsets,
            )
            for sample, localized in result.items():
                reference_tolerance = reference_tolerances.get(
                    rt_frame.sample_cohorts.get(sample, "")
                )
                if (
                    localized.location.observed
                    or localized.apex_tolerance_sec is None
                    or reference_tolerance is None
                ):
                    continue
                direct = reference.locate(
                    sample,
                    rt_frame=rt_frame,
                    backfill_half_window_min=backfill_half_window_min,
                    drift_offsets=drift_offsets,
                )
                if abs(localized.location.query_rt - direct.query_rt) * 60.0 > max(
                    localized.apex_tolerance_sec, reference_tolerance
                ):
                    result[sample] = BackfillPeakLocation(localized.location, None)
        result.update(paired_locations)
        return {sample: result[sample] for sample in sample_stems}

    def _empirical_tolerances(
        self,
        *,
        reference_samples: frozenset[str],
        needed_cohorts: set[str],
        rt_frame: RtFrame,
        backfill_half_window_min: float,
        drift_offsets: Mapping[str, float] | None = None,
    ) -> dict[str, float]:
        """Bound each cohort once using held-out direct-reference residuals."""
        residuals: dict[str, list[float]] = defaultdict(list)
        for sample, observed_rt in self.sample_apices:
            cohort = rt_frame.sample_cohorts.get(sample)
            if sample not in reference_samples or cohort not in needed_cohorts:
                continue
            peers = tuple(
                point
                for point in self.sample_apices
                if point[0] != sample
                and rt_frame.sample_cohorts.get(point[0]) == cohort
            )
            if not peers:
                continue
            held_out = BandRtLocator(peers).locate(
                sample,
                rt_frame=rt_frame,
                backfill_half_window_min=backfill_half_window_min,
                drift_offsets=drift_offsets,
            )
            assert cohort is not None
            residuals[cohort].append(abs(held_out.query_rt - observed_rt) * 60.0)
        tolerances = {}
        for cohort, values in residuals.items():
            if len(values) < 2:
                continue
            residual_p90 = float(np.quantile(values, 0.9))
            half_window_sec = backfill_half_window_min * 60.0
            if residual_p90 <= half_window_sec:
                tolerances[cohort] = min(max(12.0, residual_p90), half_window_sec)
        return tolerances

    def locate(
        self,
        sample_stem: str,
        *,
        rt_frame: RtFrame,
        backfill_half_window_min: float,
        drift_offsets: Mapping[str, float] | None = None,
    ) -> PeakLocation:
        """Use an observed point or fit the unobserved part of this trajectory.

        Method-supported owners and linked MS1-only peaks contribute
        positions. The query stays in the target sample's RT coordinates;
        fitting never supplies method eligibility.
        """
        if sample_stem in self._positions:
            return PeakLocation(
                self._positions[sample_stem], "trajectory_observed", True
            )
        return self._estimate(
            sample_stem,
            rt_frame=rt_frame,
            backfill_half_window_min=backfill_half_window_min,
            drift_offsets=drift_offsets,
        )

    def _estimate(
        self,
        sample_stem: str,
        *,
        rt_frame: RtFrame,
        backfill_half_window_min: float,
        drift_offsets: Mapping[str, float] | None = None,
    ) -> PeakLocation:
        order = rt_frame.injection_order.get(sample_stem)
        cohort = rt_frame.sample_cohorts.get(sample_stem)
        offsets = (
            drift_offsets if drift_offsets and sample_stem in drift_offsets else None
        )
        nearest = sorted(
            (
                (sample, rt)
                for sample, rt in self.sample_apices
                if order is not None
                and cohort is not None
                and sample in rt_frame.injection_order
                and rt_frame.sample_cohorts.get(sample) == cohort
                and (offsets is None or sample in offsets)
            ),
            key=lambda point: (
                abs(rt_frame.injection_order[point[0]] - (order or 0)),
                rt_frame.injection_order[point[0]],
                point[0],
            ),
        )[:_TRAJECTORY_NEIGHBORS]
        if order is None or not nearest:
            return PeakLocation(
                float(np.median([rt for _, rt in self.sample_apices])),
                "trajectory_reference",
            )
        common = [rt_frame.estimate_rt(sample, rt) for sample, rt in nearest]
        common_available = all(value is not None for value in common)
        if not common_available:
            offsets = None
        coordinates = (
            [
                value.rt_min - (offsets[sample] if offsets is not None else 0.0)
                for (sample, _), value in zip(nearest, common, strict=True)
                if value is not None
            ]
            if common_available
            else [rt for _, rt in nearest]
        )
        fitted = _fit_rt_trajectory(
            [rt_frame.injection_order[sample] for sample, _ in nearest],
            coordinates,
            order,
            backfill_half_window_min=backfill_half_window_min,
        )
        if common_available:
            target_estimate = rt_frame.estimate_observed_rt(
                sample_stem,
                fitted.query_rt
                + (offsets[sample_stem] if offsets is not None else 0.0),
            )
            if target_estimate is not None:
                weak = target_estimate.rt_weight < 1.0 or any(
                    value is not None and value.rt_weight < 1.0 for value in common
                )
                return PeakLocation(
                    max(0.0, target_estimate.rt_min),
                    f"{'estimated' if weak else 'calibrated'}_{fitted.basis}",
                )
            fitted = _fit_rt_trajectory(
                [rt_frame.injection_order[sample] for sample, _ in nearest],
                [rt for _, rt in nearest],
                order,
                backfill_half_window_min=backfill_half_window_min,
            )
        return PeakLocation(max(0.0, fitted.query_rt), fitted.basis)


def _fit_rt_trajectory(
    orders: Sequence[int],
    rts: Sequence[float],
    query_order: int,
    *,
    backfill_half_window_min: float,
) -> PeakLocation:
    """Robust kernel-weighted linear trend, so steep drift is not lagged.

    Fewer than three neighbors, or neighbors at one order, leave no slope to fit;
    their median is used. Outside the support, residual scatter amplified by
    fit leverage must fit inside the actual search half-window. Otherwise use
    the fitted value at the nearest supported injection, not an unbounded
    slope or a different group. This risk proxy is not a calibrated confidence
    interval and does not establish peak identity.
    """
    offsets = np.asarray(orders, dtype=float) - query_order
    values = np.asarray(rts, dtype=float)
    spread = float(np.std(offsets))
    if len(values) < 3 or spread < 1e-10:
        return PeakLocation(float(np.median(values)), "trajectory_fit")
    design = np.column_stack([np.ones(len(offsets)), offsets / spread])
    kernel = (
        1.0 / (1.0 + np.abs(offsets) / max(float(np.max(np.abs(offsets))), 1e-10))
    ) ** 2
    weights = kernel
    beta = np.zeros(2)
    for _ in range(80):
        fit_weights = weights
        root = np.sqrt(weights)
        next_beta = np.linalg.lstsq(design * root[:, None], values * root, rcond=None)[
            0
        ]
        residual = values - design @ next_beta
        scale = max(
            _TREND_SCALE_FLOOR_MIN,
            1.4826 * float(np.median(np.abs(residual - np.median(residual)))),
        )
        weights = kernel * np.minimum(
            1.0, 1.345 * scale / np.maximum(np.abs(residual), 1e-12)
        )
        converged = float(np.max(np.abs(next_beta - beta))) < _TREND_TOLERANCE_MIN
        beta = next_beta
        if converged:
            break
    basis = "trajectory_fit"
    query_rt = float(beta[0])
    if min(orders) > query_order or max(orders) < query_order:
        covariance = np.linalg.pinv(design.T @ (fit_weights[:, None] * design))
        risk = scale * float(np.sqrt(1.0 + max(0.0, covariance[0, 0])))
        if risk > backfill_half_window_min:
            boundary = min(max(query_order, min(orders)), max(orders))
            query_rt += float(beta[1]) * (boundary - query_order) / spread
            basis = "trajectory_fit_at_support_boundary"
    return PeakLocation(query_rt, basis)
