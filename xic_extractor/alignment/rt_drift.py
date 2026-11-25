"""Batch RT anchors and the drift that the general RT map cannot carry.

General calibration moves every compound at one RT together. A few compounds
also follow the batch's chromatographic state (B, a smooth trend along
injection order) and a sample-specific shift (M) that only some compounds
feel. One accumulator collects each method channel's unique peaks; against
the frozen general map it finds the channels that drift beyond it and freezes
their B shapes and M before final grouping. Groups then fit two coefficients
against them.
Everything here is an in-memory calculation on measured RTs.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from statistics import median
from typing import TYPE_CHECKING

import numpy as np

from xic_extractor.alignment.sample_rt_calibration import BatchRtCalibration

if TYPE_CHECKING:
    from xic_extractor.alignment.rt_anchors import RtAnchorObservation

_DRIFT_SPREAD_SEC = 12.0
_SAMPLE_SHIFT_SPREAD_SEC = 8.0
_SYNC_CORRELATION = 0.75
_MIN_COMMON_SAMPLES = 6
_FAMILY_RT_MIN = 0.05
_FAMILY_SEARCH_MIN = 5.0
_SHAPE_BANDWIDTH_MIN = 1.0
_TRACK_RT_WINDOW_MIN = 2.0 * _SHAPE_BANDWIDTH_MIN


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
    detrended offsets define the anchors' M, the starting point that rows
    then vote from. ``coefficients`` is the channel's own fit
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
        """B for one group; see ``batch_state_lookup`` for repeated queries."""
        return self.batch_state_lookup()(owner_ids, rt_min)

    def batch_state_lookup(
        self,
    ) -> Callable[[Iterable[str], float], Mapping[str, float]]:
        """Prepare anchors once; the lookup gives a group's B.

        A group uses the shape of the anchor sharing most of its owners
        (nearest in RT, then channel, on ties); other groups borrow shapes
        weighted by a Gaussian of RT distance, rescaled to a unit range.
        A borrowed shape depends only on RT, so this lookup returns the same
        mapping for every query at that exact RT; callers must not mutate it.
        """
        anchors = self.anchors
        if not anchors:
            return lambda _owners, _rt: {}
        samples = sorted(set().union(*(a.shape for a in anchors)))
        matrix = np.array([[a.shape.get(s, np.nan) for s in samples] for a in anchors])
        present = ~np.isnan(matrix)
        values = np.where(present, matrix, 0.0)
        references = np.array([a.reference_rt_min for a in anchors])
        by_owner: dict[str, list[int]] = defaultdict(list)
        for index, anchor in enumerate(anchors):
            for owner_id in anchor.owner_ids:
                by_owner[owner_id].append(index)
        borrowed: dict[float, Mapping[str, float]] = {}

        def lookup(owner_ids: Iterable[str], rt_min: float) -> Mapping[str, float]:
            shared: dict[int, int] = defaultdict(int)
            for owner_id in owner_ids:
                for index in by_owner.get(owner_id, ()):
                    shared[index] += 1
            if shared:
                overlap = max(shared.values())
                own = min(
                    (i for i, count in shared.items() if count == overlap),
                    key=lambda i: (abs(references[i] - rt_min), anchors[i].channel),
                )
                return anchors[own].shape
            if rt_min not in borrowed:
                borrowed[rt_min] = borrow(rt_min)
            return borrowed[rt_min]

        def borrow(rt_min: float) -> Mapping[str, float]:
            weights = np.exp(-0.5 * ((references - rt_min) / _SHAPE_BANDWIDTH_MIN) ** 2)
            if weights.sum() <= 0:
                nearest = min(
                    range(len(anchors)),
                    key=lambda i: (abs(references[i] - rt_min), anchors[i].channel),
                )
                return anchors[nearest].shape
            total = weights @ present
            blended = weights @ values
            return _unit_range(
                {
                    sample: float(blended[index] / total[index])
                    for index, sample in enumerate(samples)
                    if total[index] > 0
                }
            )

        return lookup

    def displacement_lookup(self) -> Callable[[float, str, str], tuple[float, ...]]:
        """Prepare anchors' drift once; the lookup returns second-minus-first
        displacements predicted by each anchor near an RT."""
        prepared = sorted(
            (
                (
                    anchor.reference_rt_min,
                    self.sample_offsets(anchor.coefficients, anchor.shape),
                )
                for anchor in self.anchors
            ),
            key=lambda item: item[0],
        )
        references = [reference for reference, _ in prepared]

        def displacements(rt_min: float, first: str, second: str) -> tuple[float, ...]:
            def relative_rt(reference: float) -> float:
                return reference - rt_min

            values = set()
            low = bisect_left(references, -_TRACK_RT_WINDOW_MIN, key=relative_rt)
            high = bisect_right(references, _TRACK_RT_WINDOW_MIN, key=relative_rt)
            for _, offsets in prepared[low:high]:
                if first in offsets and second in offsets:
                    value = offsets[second] - offsets[first]
                    if value:
                        values.add(value)
            return tuple(sorted(values))

        return displacements

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
        sample_cohorts: Mapping[str, str],
        injection_order: Mapping[str, int],
        qc_samples: frozenset[str] = frozenset(),
    ) -> None:
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
        self,
        calibration: BatchRtCalibration,
        shift_rows: Iterable[Mapping[str, float]] = (),
    ) -> RtDriftModel:
        """Freeze B shapes and M against the general map, once per batch."""
        channels: dict[str, dict[str, float]] = defaultdict(dict)
        owners: dict[str, set[str]] = defaultdict(set)
        for (channel, sample), by_owner in sorted(self._observed.items()):
            if len(by_owner) == 1:
                ((owner_id, rt),) = by_owner.items()
                channels[channel][sample] = rt
                owners[channel].add(owner_id)
        return fit_rt_drift_model(
            channels,
            calibration,
            injection_order=self._orders,
            sample_cohorts=self._cohorts,
            qc_samples=self._qc,
            channel_owner_ids={key: frozenset(value) for key, value in owners.items()},
            shift_rows=shift_rows,
        )


def fit_rt_drift_model(
    channels: Mapping[str, Mapping[str, float]],
    calibration: BatchRtCalibration,
    *,
    injection_order: Mapping[str, int],
    sample_cohorts: Mapping[str, str] | None = None,
    qc_samples: frozenset[str] = frozenset(),
    channel_owner_ids: Mapping[str, frozenset[str]] | None = None,
    shift_rows: Iterable[Mapping[str, float]] = (),
) -> RtDriftModel:
    """Drift anchors are channels the general map leaves spread out and that
    another elution family reproduces; freeze their B shapes and M.

    ``shift_rows`` are direct-evidence rows in the general map; when given,
    they vote M and every anchor is fitted against that vote.
    """
    cohorts = dict(sample_cohorts or {})
    unsettled = _unsettled_channels(
        _calibrated(channels, calibration), injection_order, cohorts
    )
    drifting = frozenset(_synchronized(unsettled, _families(channels)))
    calibrated = _calibrated({key: channels[key] for key in drifting}, calibration)
    return _drift_model(
        calibrated,
        injection_order=injection_order,
        qc_samples=qc_samples,
        cohorts=cohorts,
        families=_families(calibrated),
        owner_ids=channel_owner_ids or {},
        shift_rows=shift_rows,
    )


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
    """Co-eluting channels form one family: their apices coincide per sample.

    Compare on the samples both channels observe, since drifting channels
    observed in different samples have different medians. Without common
    samples, compare medians.
    """
    centers = sorted(
        (float(median(values.values())), channel)
        for channel, values in channels.items()
        if values
    )
    parent = {channel: channel for _, channel in centers}

    def root(channel: str) -> str:
        while parent[channel] != channel:
            parent[channel] = parent[parent[channel]]
            channel = parent[channel]
        return channel

    for index, (center, first) in enumerate(centers):
        for other_center, second in centers[index + 1 :]:
            if other_center - center > _FAMILY_SEARCH_MIN:
                break
            common = set(channels[first]) & set(channels[second])
            gap = (
                median(abs(channels[first][s] - channels[second][s]) for s in common)
                if common
                else other_center - center
            )
            if gap <= _FAMILY_RT_MIN:
                parent[root(second)] = root(first)
    family: dict[str, int] = {}
    roots: dict[str, int] = {}
    for _, channel in centers:
        family[channel] = roots.setdefault(root(channel), len(roots))
    return family


def _robust_spread_sec(values: Sequence[float]) -> float:
    center = median(values)
    return 1.4826 * float(median(abs(v - center) for v in values)) * 60.0


def _cohort_residuals(
    values: Mapping[str, float],
    orders: Mapping[str, int],
    cohorts: Mapping[str, str],
) -> dict[str, float]:
    """Offsets from the same-cohort median, as calibration references are."""
    by_cohort: dict[str, list[float]] = defaultdict(list)
    for sample, rt in values.items():
        by_cohort[cohorts.get(sample, "")].append(rt)
    centers = {cohort: median(rts) for cohort, rts in by_cohort.items()}
    return {
        sample: rt - centers[cohorts.get(sample, "")]
        for sample, rt in values.items()
        if sample in orders
    }


def _unsettled_channels(
    calibrated: Mapping[str, Mapping[str, float]],
    orders: Mapping[str, int],
    cohorts: Mapping[str, str],
) -> dict[str, dict[str, float]]:
    """Residuals of channels whose spread the general map leaves large."""
    residuals = {
        channel: _cohort_residuals(values, orders, cohorts)
        for channel, values in calibrated.items()
    }
    return {
        channel: values
        for channel, values in residuals.items()
        if len(values) >= 2
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
    cohorts: Mapping[str, str],
    families: Mapping[str, int],
    owner_ids: Mapping[str, frozenset[str]],
    shift_rows: Iterable[Mapping[str, float]] = (),
) -> RtDriftModel:
    if not calibrated:
        return RtDriftModel()
    residual = {
        channel: _cohort_residuals(values, injection_order, cohorts)
        for channel, values in calibrated.items()
    }
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

    anchor_shift = shift(None)
    sample_shift = _row_voted_shift(anchor_shift, shift_rows, injection_order)
    anchors = []
    for channel in sorted(residual):
        values = residual[channel]
        own_shift = (
            shift(families[channel]) if sample_shift is anchor_shift else sample_shift
        )
        response = _slope(
            [own_shift[s] for s in values if s in own_shift],
            [detrended[channel][s] for s in values if s in own_shift],
        )
        trend = _smooth(
            {
                s: v - (response * own_shift[s] if response else 0.0)
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


ROW_MIN_SAMPLES = 20
_ROW_NEIGHBORS = 8
_MIN_ROW_RESPONSE = 0.3
_MIN_ROW_VOTES = 3


def _row_voted_shift(
    base: dict[str, float],
    rows: Iterable[Mapping[str, float]],
    injection_order: Mapping[str, int],
) -> dict[str, float]:
    """Let rows of direct-evidence peaks vote each sample's shift M.

    The anchors' M (``base``) rests on the few channels that hold one peak per
    sample, so some samples are voted by one or two of them. A row is one
    compound's general-map RT per sample, already separated from same-mass
    neighbours, which gives every sample many more voters. Each row is compared
    with its injection-order neighbours, so a slow trend cancels; a row votes
    when that local offset follows the anchors' M, and its vote is the offset
    divided by its own response. A sample with too few votes keeps the anchors'
    M; without a voting row, ``base`` itself is returned.
    """
    voters = []
    for row in rows:
        members = sorted(
            (s for s in row if s in injection_order),
            key=lambda s: (injection_order[s], s),
        )
        if len(members) < ROW_MIN_SAMPLES:
            continue
        neighbors = {
            sample: sorted(
                (other for other in members if other != sample),
                key=lambda other: (
                    abs(injection_order[other] - injection_order[sample]),
                    injection_order[other],
                    other,
                ),
            )[:_ROW_NEIGHBORS]
            for sample in members
        }
        local = {
            sample: row[sample] - float(median(row[n] for n in neighbors[sample]))
            for sample in members
        }
        known = [
            sample
            for sample in members
            if sample in base and all(n in base for n in neighbors[sample])
        ]
        if len(known) < _MIN_COMMON_SAMPLES:
            continue
        shift = [
            base[sample] - float(median(base[n] for n in neighbors[sample]))
            for sample in known
        ]
        offsets = [local[sample] for sample in known]
        response = _slope(shift, offsets)
        if (
            response >= _MIN_ROW_RESPONSE
            and _spearman(shift, offsets) >= _SYNC_CORRELATION
        ):
            voters.append((local, neighbors, response))
    if not voters:
        return base
    refined = dict(base)
    for _ in range(_BACKFIT_ITERATIONS):
        updated = {}
        for sample, current in refined.items():
            votes = [
                local[sample] / response
                + float(median(refined[n] for n in neighbors[sample]))
                for local, neighbors, response in voters
                if sample in local and all(n in refined for n in neighbors[sample])
            ]
            updated[sample] = (
                float(median(votes)) if len(votes) >= _MIN_ROW_VOTES else current
            )
        converged = all(abs(updated[s] - refined[s]) < 1e-9 for s in refined)
        refined = updated
        if converged:
            break
    return refined


def _slope(x: Sequence[float] | np.ndarray, y: Sequence[float] | np.ndarray) -> float:
    """Theil-Sen slope; zero without at least two distinct x values."""
    return _slope_lookup(x)(y)


def _slope_lookup(
    x: Sequence[float] | np.ndarray,
) -> Callable[[Sequence[float] | np.ndarray], float]:
    """Prepare the fixed regressor pairs once for repeated residual fits."""
    xs = np.asarray(x, dtype=float)
    first, second = upper_pairs(len(xs))
    dx = xs[second] - xs[first]
    distinct = dx != 0
    first, second, dx = first[distinct], second[distinct], dx[distinct]

    def slope(y: Sequence[float] | np.ndarray) -> float:
        if not len(dx):
            return 0.0
        ys = np.asarray(y, dtype=float)
        slopes = (ys[second] - ys[first]) / dx
        middle = len(slopes) // 2
        if len(slopes) % 2:
            slopes.partition(middle)
            return float(slopes[middle])
        slopes.partition((middle - 1, middle))
        return float((slopes[middle - 1] + slopes[middle]) / 2.0)

    return slope


_UPPER_PAIRS: dict[int, tuple[np.ndarray, np.ndarray]] = {}


def upper_pairs(size: int) -> tuple[np.ndarray, np.ndarray]:
    """Index pairs i < j in ``itertools.combinations`` order, prepared once."""
    if size not in _UPPER_PAIRS:
        _UPPER_PAIRS[size] = np.triu_indices(size, 1)
    return _UPPER_PAIRS[size]


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
        time_slope, shift_slope = _slope_lookup(bs), _slope_lookup(ms)
        a = time_slope(ys)
        c = 0.0
        for _ in range(_BACKFIT_ITERATIONS):
            c_next = shift_slope(ys - a * bs)
            a_next = time_slope(ys - c_next * ms)
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
