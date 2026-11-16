"""Batch RT anchors and the drift that the general RT map cannot carry.

General calibration moves every compound at one RT together. A few compounds
also follow the batch's chromatographic state (B, a smooth trend along
injection order) and a sample-specific shift (M) that only some compounds
feel. One accumulator collects low-ambiguity anchor observations; freezing
fits the general map without the drifting channels and freezes B shapes and
M before final grouping. Groups then fit two coefficients against them.
Everything here is an in-memory calculation on measured RTs.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from statistics import median

import numpy as np

from xic_extractor.alignment.sample_rt_calibration import (
    BatchRtCalibration,
    RtCalibrationPolicy,
    build_batch_rt_calibration,
)

# A channel whose general-calibrated RT keeps this much robust spread drifts
# beyond the shared map.
_DRIFT_SPREAD_SEC = 12.0
# Sample shift is per-sample scatter around the batch trend that another
# elution family reproduces; smooth segment-level deviations are not M.
_SAMPLE_SHIFT_SPREAD_SEC = 8.0
# Drift is batch drift only when another elution family moves with it.
_SYNC_CORRELATION = 0.75
_MIN_COMMON_SAMPLES = 6
# Isotopes, adducts and co-eluting ions share an elution family and one vote.
_FAMILY_RT_MIN = 0.05
# Groups without their own anchor borrow shapes from anchors nearby in RT.
_SHAPE_BANDWIDTH_MIN = 1.0
# Pattern correspondence considers the displacement of anchors this close.
_TRACK_RT_WINDOW_MIN = 2.0 * _SHAPE_BANDWIDTH_MIN


@dataclass(frozen=True, slots=True)
class RtAnchorObservation:
    """One sample's only direct-supported, quantifiable peak in a channel."""

    channel: str
    sample_stem: str
    owner_id: str
    rt_min: float


@dataclass(frozen=True, slots=True)
class DriftCoefficients:
    """A group's response to batch state (unit shape) and sample shift (min)."""

    time: float = 0.0
    sample_shift: float = 0.0

    @property
    def nonzero_count(self) -> int:
        return int(self.time != 0.0) + int(self.sample_shift != 0.0)

    def offset_min(
        self, batch_state: float | None, sample_shift: float | None
    ) -> float | None:
        """Predicted drift, or None when a used regressor is unknown."""
        offset = 0.0
        if self.time:
            if batch_state is None:
                return None
            offset += self.time * batch_state
        if self.sample_shift:
            if sample_shift is None:
                return None
            offset += self.sample_shift * sample_shift
        return offset


@dataclass(frozen=True, slots=True)
class DriftAnchor:
    """A channel that drifts beyond the general map, with its frozen B shape.

    ``shape`` is the channel's injection-order trend after removing its
    sample-shift response, scaled to a unit 5-95% range and oriented to rise
    with injection order. ``sample_shift_member`` marks channels whose
    detrended offsets define M. ``coefficients`` is the channel's own fit
    against its shape and M, used to predict its displacement between samples.
    """

    channel: str
    reference_rt_min: float
    family: int
    sample_shift_member: bool
    shape: Mapping[str, float]
    coefficients: DriftCoefficients = DriftCoefficients()
    owner_ids: frozenset[str] = field(default_factory=frozenset, compare=False)


@dataclass(frozen=True, slots=True)
class RtDriftModel:
    """Frozen B shapes and M for one batch; unknown M is absent, not zero."""

    anchors: tuple[DriftAnchor, ...] = ()
    sample_shift_min: Mapping[str, float] = field(default_factory=dict)
    batch_state_basis: str = "none"

    def batch_state(
        self, owner_ids: Iterable[str], rt_min: float
    ) -> Mapping[str, float]:
        """B for a group: its own anchor's shape, else shapes borrowed by RT."""
        if not self.anchors:
            return {}
        members = frozenset(owner_ids)
        overlap = max(len(members & anchor.owner_ids) for anchor in self.anchors)
        if overlap:
            own = min(
                (a for a in self.anchors if len(members & a.owner_ids) == overlap),
                key=lambda a: (abs(a.reference_rt_min - rt_min), a.channel),
            )
            return own.shape
        weights = [
            math.exp(-0.5 * ((a.reference_rt_min - rt_min) / _SHAPE_BANDWIDTH_MIN) ** 2)
            for a in self.anchors
        ]
        if sum(weights) <= 0:
            nearest = min(
                self.anchors,
                key=lambda a: (abs(a.reference_rt_min - rt_min), a.channel),
            )
            return nearest.shape
        samples = set().union(*(a.shape for a in self.anchors))
        blended = {}
        for sample in samples:
            pairs = [
                (w, a.shape[sample])
                for w, a in zip(weights, self.anchors, strict=True)
                if sample in a.shape
            ]
            total = sum(w for w, _ in pairs)
            if total > 0:
                blended[sample] = sum(w * v for w, v in pairs) / total
        return _unit_range(blended)

    def displacements(
        self, rt_min: float, first: str, second: str
    ) -> tuple[float, ...]:
        """Second-minus-first drift predicted by each anchor near this RT."""
        values = set()
        for anchor in self.anchors:
            if abs(anchor.reference_rt_min - rt_min) > _TRACK_RT_WINDOW_MIN:
                continue
            offsets = self.sample_offsets(anchor.coefficients, anchor.shape)
            if first in offsets and second in offsets:
                value = offsets[second] - offsets[first]
                if value:
                    values.add(value)
        return tuple(sorted(values))

    def sample_offsets(
        self, coefficients: DriftCoefficients, batch_state: Mapping[str, float]
    ) -> dict[str, float]:
        """A group's modeled drift for every sample whose regressors are known."""
        samples = set(batch_state) | set(self.sample_shift_min)
        offsets = {}
        for sample in samples:
            offset = coefficients.offset_min(
                batch_state.get(sample), self.sample_shift_min.get(sample)
            )
            if offset is not None:
                offsets[sample] = offset
        return offsets


class BatchRtModelAccumulator:
    """Retain compact anchor observations and freeze the batch model once."""

    def __init__(
        self,
        *,
        sample_stems: Sequence[str],
        sample_cohorts: Mapping[str, str],
        injection_order: Mapping[str, int],
        qc_samples: frozenset[str] = frozenset(),
    ) -> None:
        self._samples = tuple(sample_stems)
        self._cohorts = sample_cohorts
        self._orders = injection_order
        self._qc = qc_samples
        self._observed: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)

    def add_observations(self, observations: Iterable[RtAnchorObservation]) -> None:
        for item in observations:
            self._observed[(item.channel, item.sample_stem)][item.owner_id] = (
                item.rt_min
            )

    def freeze(
        self, *, measured_istds: Sequence[tuple[str, str, float]] = ()
    ) -> tuple[BatchRtCalibration, RtDriftModel]:
        channels: dict[str, dict[str, float]] = defaultdict(dict)
        owners: dict[str, set[str]] = defaultdict(set)
        # Several supported peaks leave that sample unknown; never pick one.
        for (channel, sample), by_owner in sorted(self._observed.items()):
            if len(by_owner) == 1:
                ((owner_id, rt),) = by_owner.items()
                channels[channel][sample] = rt
                owners[channel].add(owner_id)
        return fit_batch_rt_model(
            channels,
            sample_stems=self._samples,
            sample_cohorts=self._cohorts,
            injection_order=self._orders,
            qc_samples=self._qc,
            measured_istds=measured_istds,
            channel_owner_ids={key: frozenset(value) for key, value in owners.items()},
        )


def fit_batch_rt_model(
    channels: Mapping[str, Mapping[str, float]],
    *,
    sample_stems: Sequence[str],
    sample_cohorts: Mapping[str, str] | None,
    injection_order: Mapping[str, int],
    qc_samples: frozenset[str] = frozenset(),
    measured_istds: Sequence[tuple[str, str, float]] = (),
    channel_owner_ids: Mapping[str, frozenset[str]] | None = None,
) -> tuple[BatchRtCalibration, RtDriftModel]:
    """Fit, refit without channels the map cannot carry, then freeze B and M.

    A channel left with large residual spread either drifts beyond the map or
    mixes peaks across samples; neither can anchor the general map. Those that
    another elution family reproduces are drift anchors.
    """

    def fit(selected: Mapping[str, Mapping[str, float]]) -> BatchRtCalibration:
        return build_batch_rt_calibration(
            selected,
            sample_stems=sample_stems,
            sample_cohorts=sample_cohorts,
            measured_istds=measured_istds,
            prior_injections={},
            policy=RtCalibrationPolicy(),
        )

    first = fit(channels)
    unsettled = _unsettled_channels(_calibrated(channels, first), injection_order)
    drifting = frozenset(_synchronized(unsettled, _families(channels)))
    calibration = (
        fit({key: value for key, value in channels.items() if key not in unsettled})
        if unsettled
        else first
    )
    calibrated = _calibrated({key: channels[key] for key in drifting}, calibration)
    model = _drift_model(
        calibrated,
        injection_order=injection_order,
        qc_samples=qc_samples,
        families=_families(calibrated),
        owner_ids=channel_owner_ids or {},
    )
    return calibration, model


def _calibrated(
    channels: Mapping[str, Mapping[str, float]], calibration: BatchRtCalibration
) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for channel, by_sample in channels.items():
        values = {}
        for sample, rt in by_sample.items():
            fitted = calibration.samples.get(sample)
            value = fitted.calibrated_rt(rt) if fitted is not None else None
            if value is not None:
                values[sample] = value
        if values:
            result[channel] = values
    return result


def _families(channels: Mapping[str, Mapping[str, float]]) -> dict[str, int]:
    centers = sorted(
        (float(median(values.values())), channel)
        for channel, values in channels.items()
        if values
    )
    family: dict[str, int] = {}
    index = -1
    previous = -math.inf
    for center, channel in centers:
        if center - previous > _FAMILY_RT_MIN:
            index += 1
        family[channel] = index
        previous = center
    return family


def _robust_spread_sec(values: Sequence[float]) -> float:
    center = median(values)
    return 1.4826 * float(median(abs(v - center) for v in values)) * 60.0


def _unsettled_channels(
    calibrated: Mapping[str, Mapping[str, float]], orders: Mapping[str, int]
) -> dict[str, dict[str, float]]:
    """Residuals of channels whose spread the general map leaves large."""
    return {
        channel: {
            sample: rt - median(values.values())
            for sample, rt in values.items()
            if sample in orders
        }
        for channel, values in calibrated.items()
        if len(values) >= _MIN_COMMON_SAMPLES
        and _robust_spread_sec(list(values.values())) >= _DRIFT_SPREAD_SEC
    }


def _synchronized(
    residuals: Mapping[str, Mapping[str, float]], families: Mapping[str, int]
) -> set[str]:
    """Channels correlated with a channel of another family, positively."""
    keys = sorted(residuals)
    linked: set[str] = set()
    for position, first in enumerate(keys):
        for second in keys[position + 1 :]:
            if families[first] == families[second]:
                continue
            common = sorted(set(residuals[first]) & set(residuals[second]))
            if len(common) < _MIN_COMMON_SAMPLES:
                continue
            correlation = _spearman(
                [residuals[first][s] for s in common],
                [residuals[second][s] for s in common],
            )
            if correlation >= _SYNC_CORRELATION:
                linked.update((first, second))
    return linked


def _spearman(left: Sequence[float], right: Sequence[float]) -> float:
    a, b = _ranks(left), _ranks(right)
    if np.std(a) == 0 or np.std(b) == 0:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def _ranks(values: Sequence[float]) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    order = np.argsort(array, kind="mergesort")
    ranks = np.empty(len(array))
    ranks[order] = np.arange(len(array), dtype=float)
    for value in np.unique(array):
        tied = array == value
        if tied.sum() > 1:
            ranks[tied] = ranks[tied].mean()
    return ranks


def _smooth(values: Mapping[str, float], orders: Mapping[str, int]) -> dict[str, float]:
    """Running median along injection order, interpolated to every sample."""
    known = sorted((orders[s], v) for s, v in values.items() if s in orders)
    if not known:
        return {}
    width = max(3, round(len(known) / 6))
    width += 1 - width % 2
    half = width // 2
    xs = np.array([x for x, _ in known], dtype=float)
    smoothed = np.array(
        [
            float(np.median([v for _, v in known[max(0, i - half) : i + half + 1]]))
            for i in range(len(known))
        ]
    )
    return {
        sample: float(np.interp(order, xs, smoothed))
        for sample, order in orders.items()
    }


def _unit_range(shape: Mapping[str, float]) -> dict[str, float]:
    if not shape:
        return {}
    values = np.asarray(list(shape.values()), dtype=float)
    low, high = np.percentile(values, [5, 95])
    scale = float(high - low)
    if scale <= 0:
        return dict.fromkeys(shape, 0.0)
    center = float(np.median(values))
    return {sample: (value - center) / scale for sample, value in shape.items()}


def _drift_model(
    calibrated: Mapping[str, Mapping[str, float]],
    *,
    injection_order: Mapping[str, int],
    qc_samples: frozenset[str],
    families: Mapping[str, int],
    owner_ids: Mapping[str, frozenset[str]],
) -> RtDriftModel:
    if not calibrated:
        return RtDriftModel()
    residual = {
        channel: {
            sample: rt - median(values.values())
            for sample, rt in values.items()
            if sample in injection_order
        }
        for channel, values in calibrated.items()
    }
    # Pooled QC has a fixed matrix, so its trend is batch state alone.
    qc_basis = any(
        sum(sample in qc_samples for sample in values) >= 2
        for values in residual.values()
    )
    detrended = {}
    scatter = {}
    for channel, values in residual.items():
        qc = {s: v for s, v in values.items() if s in qc_samples}
        trend = (
            _smooth(qc, injection_order)
            if len(qc) >= 2
            else _smooth(values, injection_order)
        )
        detrended[channel] = {s: v - trend[s] for s, v in values.items()}
        local = _smooth(values, injection_order)
        scatter[channel] = {s: v - local[s] for s, v in values.items()}
    members = _synchronized(
        {
            channel: values
            for channel, values in scatter.items()
            if _robust_spread_sec(list(values.values())) >= _SAMPLE_SHIFT_SPREAD_SEC
        },
        families,
    )
    by_family: dict[int, list[str]] = defaultdict(list)
    for channel in sorted(members):
        by_family[families[channel]].append(channel)

    def shift(excluded_family: int | None) -> dict[str, float]:
        family_offsets: dict[str, list[float]] = defaultdict(list)
        for family, channels in by_family.items():
            if family == excluded_family:
                continue
            per_sample: dict[str, list[float]] = defaultdict(list)
            for channel in channels:
                for sample, value in detrended[channel].items():
                    per_sample[sample].append(value)
            for sample, values in per_sample.items():
                family_offsets[sample].append(float(median(values)))
        return {s: float(median(v)) for s, v in family_offsets.items()}

    sample_shift = shift(None)
    anchors = []
    for channel in sorted(residual):
        values = residual[channel]
        own_shift = shift(families[channel])
        response = _slope(
            [own_shift[s] for s in values if s in own_shift],
            [detrended[channel][s] for s in values if s in own_shift],
        )
        trend = _smooth(
            {
                s: v - response * own_shift[s]
                for s, v in values.items()
                if s in own_shift or response == 0.0
            },
            injection_order,
        )
        shape = _unit_range(trend)
        if (
            shape
            and _spearman(
                [shape[s] for s in sorted(shape)],
                [injection_order[s] for s in sorted(shape)],
            )
            < 0
        ):
            shape = {s: -v for s, v in shape.items()}
        anchors.append(
            DriftAnchor(
                channel,
                float(median(calibrated[channel].values())),
                families[channel],
                channel in members,
                shape,
                _own_fit(
                    [
                        (rt, shape.get(sample), sample_shift.get(sample))
                        for sample, rt in calibrated[channel].items()
                    ]
                ),
                owner_ids.get(channel, frozenset()),
            )
        )
    return RtDriftModel(
        tuple(anchors),
        sample_shift,
        "qc" if qc_basis else "samples",
    )


def _slope(x: Sequence[float] | np.ndarray, y: Sequence[float] | np.ndarray) -> float:
    """Theil-Sen slope; zero without at least two distinct x values."""
    if len(x) < 2:
        return 0.0
    xs, ys = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    dx = xs[None, :] - xs[:, None]
    dy = ys[None, :] - ys[:, None]
    upper = np.triu(np.ones_like(dx, dtype=bool), k=1) & (dx != 0)
    if not upper.any():
        return 0.0
    return float(np.median(dy[upper] / dx[upper]))


# A coefficient needs enough members and enough regressor spread to be
# estimated; otherwise it stays zero.
MIN_COEFFICIENT_MEMBERS = 6
_MIN_BATCH_STATE_RANGE = 0.25
_MIN_SAMPLE_SHIFT_RANGE_MIN = 10.0 / 60.0
_BACKFIT_ITERATIONS = 20


def candidate_coefficients(
    points: Sequence[tuple[float, float | None, float | None]],
) -> tuple[DriftCoefficients, ...]:
    """Robust intercept fits of RT on B, on M and on both.

    Each point is (RT, B, M) with unknown regressors as None; a point lacking a
    model's regressor does not enter that model's estimate. Theil-Sen slopes
    carry their own intercept; the two-regressor fit alternates them.
    """
    models = []
    with_b = [(y, b) for y, b, _ in points if b is not None]
    with_m = [(y, m) for y, _, m in points if m is not None]
    both = [(y, b, m) for y, b, m in points if b is not None and m is not None]
    time_ok = _enough(with_b, _MIN_BATCH_STATE_RANGE)
    shift_ok = _enough(with_m, _MIN_SAMPLE_SHIFT_RANGE_MIN)
    if time_ok:
        models.append(DriftCoefficients(time=_slope(*_columns(with_b))))
    if shift_ok:
        models.append(DriftCoefficients(sample_shift=_slope(*_columns(with_m))))
    if (
        time_ok
        and shift_ok
        and _enough([(y, b) for y, b, _ in both], _MIN_BATCH_STATE_RANGE)
        and _enough([(y, m) for y, _, m in both], _MIN_SAMPLE_SHIFT_RANGE_MIN)
    ):
        ys = np.array([p[0] for p in both])
        bs = np.array([p[1] for p in both])
        ms = np.array([p[2] for p in both])
        a = _slope(bs, ys)
        c = 0.0
        for _ in range(_BACKFIT_ITERATIONS):
            c_next = _slope(ms, ys - a * bs)
            a_next = _slope(bs, ys - c_next * ms)
            converged = abs(a_next - a) < 1e-9 and abs(c_next - c) < 1e-9
            a, c = a_next, c_next
            if converged:
                break
        models.append(DriftCoefficients(time=a, sample_shift=c))
    return tuple(model for model in models if model.nonzero_count)


def _own_fit(
    points: Sequence[tuple[float, float | None, float | None]],
) -> DriftCoefficients:
    """The candidate leaving the least robust residual spread; zero if none."""
    best = (_robust_spread_sec([y for y, _, _ in points]), 0, DriftCoefficients())
    for model in candidate_coefficients(points):
        adjusted = [
            y - offset
            for y, b, m in points
            if (offset := model.offset_min(b, m)) is not None
        ]
        if len(adjusted) >= MIN_COEFFICIENT_MEMBERS:
            best = min(
                best,
                (_robust_spread_sec(adjusted), model.nonzero_count, model),
                key=lambda item: (item[0], item[1]),
            )
    return best[2]


def _columns(pairs: Sequence[tuple[float, float]]) -> tuple[list[float], list[float]]:
    return [x for _, x in pairs], [y for y, _ in pairs]


def _enough(pairs: Sequence[tuple[float, float]], min_range: float) -> bool:
    if len(pairs) < MIN_COEFFICIENT_MEMBERS:
        return False
    xs = [x for _, x in pairs]
    return max(xs) - min(xs) >= min_range
