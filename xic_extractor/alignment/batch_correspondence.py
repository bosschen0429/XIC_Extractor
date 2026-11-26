"""Pair the features of independently grouped batches.

Each declared batch is grouped alone: injection order, neighbors, calibration
and drift exist only within a batch. This module is the one step relating
batches. It works on each batch's finished features (one per Matrix row) in
that batch's own reference coordinate.

1. Measured ISTDs give a coarse prediction: their offsets interpolated
   linearly, the nearest endpoint's offset outside them. The coarse window's
   half-width is ``k`` times the largest coarse ISTD leave-one-out error, at
   least ``floor``.
2. Features at the batch's ordinary-anchor prevalence pair as anchors when the
   window holds exactly one same-channel feature in both directions. Anchors
   whose RTs agree within ``family`` in both batches vote once.
3. The ISTDs and anchors fit the sample-calibration owner's robust map from
   the batch's coordinate to the reference batch's.
4. Every feature then pairs through that map, again only when unique in both
   directions; every same-channel feature of a batch competes. The search
   window follows RT: at a reference-batch RT its half-width is ``k`` times the
   map's ISTD leave-one-out errors interpolated between the neighboring ISTDs
   (the nearest ISTD's outside them), at least ``floor``. A feature's window is
   taken at its own reference-batch RT.

Pairing is a property of the feature sets, not of IDs, input order or which
batch is listed first. Ambiguity is reported, never resolved by choosing.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from statistics import median
from typing import Literal

from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.sample_rt_calibration import (
    BatchRtCalibration,
    RtCalibrationPolicy,
    RtEstimateSupport,
    SampleRtCalibration,
    fit_sample_rt_calibration,
    interpolate_calibrated_rt_min,
    ordinary_anchor_min_samples,
)
from xic_extractor.mass_accuracy import within_ppm

SEARCH_WINDOW_MULTIPLIER = 2.0
SEARCH_WINDOW_FLOOR_MIN = 0.2
ELUTION_FAMILY_MIN = 0.05

PairingState = Literal[
    "paired",
    "unpaired_no_candidate",
    "unpaired_outside_window",
    "unpaired_ambiguous",
    "unpaired_no_map",
]
AnchorExclusion = Literal[
    "no_candidate",
    "outside_window",
    "ambiguous",
    "counterpart_below_threshold",
    "competitor",
]


@dataclass(frozen=True, slots=True)
class BatchFeature:
    """One batch's finished feature in that batch's reference coordinate."""

    batch_id: str
    key: str
    neutral_loss_tag: str
    precursor_mz: float
    reference_rt_min: float
    sample_count: int
    known_identity_label: str | None = None


@dataclass(frozen=True, slots=True)
class BatchIstd:
    batch_id: str
    label: str
    reference_rt_min: float


@dataclass(frozen=True, slots=True)
class GroupedBatch:
    """All facts one finished batch contributes to correspondence."""

    batch_id: str
    sample_count: int
    features: tuple[BatchFeature, ...]
    istds: tuple[BatchIstd, ...]

    @classmethod
    def from_groups(
        cls,
        batch_id: str,
        sample_count: int,
        groups: Iterable[CrossSamplePeakGroupHypothesis],
        calibration: BatchRtCalibration | None,
    ) -> GroupedBatch:
        return cls(
            batch_id,
            sample_count,
            tuple(
                batch_feature(batch_id, group, calibration)
                for group in groups
                if group.destination == "matrix"
            ),
            batch_istds(batch_id, calibration),
        )


@dataclass(frozen=True, slots=True)
class CorrespondenceAnchor:
    """One vote: (batch RT, reference-batch RT) and what measured it."""

    rt_min: float
    reference_rt_min: float
    istd_label: str | None
    feature_keys: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class ExcludedAnchor:
    key: str
    rt_min: float
    reason: AnchorExclusion


@dataclass(frozen=True, slots=True)
class BatchMap:
    """One non-reference batch's map onto the reference batch."""

    batch_id: str
    coarse_istds: tuple[tuple[str, float, float], ...]
    coarse_loo_error_min: Mapping[str, float]
    coarse_half_width_min: float | None
    anchors: tuple[CorrespondenceAnchor, ...]
    excluded_anchors: tuple[ExcludedAnchor, ...]
    calibration: SampleRtCalibration | None
    loo_error_min: Mapping[str, float]
    search_window: tuple[tuple[float, float], ...]

    def search_half_width_at(self, reference_rt_min: float) -> float:
        return _interpolate(self.search_window, reference_rt_min)

    def to_reference(self, rt_min: float) -> float | None:
        if self.calibration is None:
            return None
        estimate = self.calibration.estimate_rt(rt_min)
        return None if estimate is None else estimate.rt_min

    def from_reference(self, reference_rt_min: float) -> float | None:
        if self.calibration is None:
            return None
        estimate = self.calibration.estimate_observed_rt(reference_rt_min)
        return None if estimate is None else estimate.rt_min


@dataclass(frozen=True, slots=True)
class FeatureCorrespondence:
    batch_id: str
    key: str
    state: PairingState
    partners: tuple[tuple[str, str], ...]
    reference_rt_min: float | None
    competitors: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class BatchCorrespondence:
    reference_batch: str
    maps: tuple[BatchMap, ...]
    features: tuple[FeatureCorrespondence, ...]

    def map_for(self, batch_id: str) -> BatchMap | None:
        return next((item for item in self.maps if item.batch_id == batch_id), None)

    def to_reference_rt(self, batch_id: str, rt_min: float) -> float | None:
        """A batch coordinate in the reference batch's; None without a map."""
        if batch_id == self.reference_batch:
            return rt_min
        batch_map = self.map_for(batch_id)
        return None if batch_map is None else batch_map.to_reference(rt_min)

    def rows(self) -> tuple[tuple[tuple[str, str], ...], ...]:
        """Each reference feature with its partners; other features alone."""
        rows = []
        for feature in self.features:
            member = (feature.batch_id, feature.key)
            if feature.batch_id == self.reference_batch:
                rows.append((member, *feature.partners))
            elif feature.state != "paired":
                rows.append((member,))
        return tuple(rows)


def choose_reference_batch(
    sample_counts: Mapping[str, int],
    latest_acquisition: Callable[[str], object] | None = None,
) -> str:
    """The batch with the most samples; on a tie, the latest acquired.

    ``latest_acquisition`` is read only for tied batches, so a run without a
    tie never opens RAW headers for this.
    """
    most = max(sample_counts.values())
    tied = sorted(batch for batch, count in sample_counts.items() if count == most)
    if len(tied) == 1:
        return tied[0]
    if latest_acquisition is None:
        raise ValueError("tied batch sizes need acquisition times")
    times = {batch: latest_acquisition(batch) for batch in tied}
    latest = max(times.values())  # type: ignore[type-var]
    winners = [batch for batch in tied if times[batch] == latest]
    if len(winners) != 1:
        raise ValueError("reference batch is ambiguous: equal size and acquisition")
    return winners[0]


def pair_grouped_batches(
    batches: Sequence[GroupedBatch],
    *,
    latest_acquisition: Callable[[str], object],
    precursor_tolerance_ppm: float,
) -> tuple[BatchCorrespondence, tuple[BatchFeature, ...]]:
    sizes = {batch.batch_id: batch.sample_count for batch in batches}
    features = tuple(item for batch in batches for item in batch.features)
    return correspond_batches(
        features,
        (item for batch in batches for item in batch.istds),
        batch_sizes=sizes,
        reference_batch=choose_reference_batch(sizes, latest_acquisition),
        precursor_tolerance_ppm=precursor_tolerance_ppm,
    ), features


def correspond_batches(
    features: Iterable[BatchFeature],
    istds: Iterable[BatchIstd],
    *,
    batch_sizes: Mapping[str, int],
    reference_batch: str,
    precursor_tolerance_ppm: float,
    multiplier: float = SEARCH_WINDOW_MULTIPLIER,
    floor_min: float = SEARCH_WINDOW_FLOOR_MIN,
) -> BatchCorrespondence:
    by_batch: dict[str, list[BatchFeature]] = {batch: [] for batch in batch_sizes}
    for feature in features:
        by_batch[feature.batch_id].append(feature)
    for values in by_batch.values():
        values.sort(key=lambda item: (item.reference_rt_min, item.precursor_mz))
    istd_rt = {(istd.batch_id, istd.label): istd.reference_rt_min for istd in istds}
    reference = by_batch[reference_batch]
    channel = _channel_matcher(precursor_tolerance_ppm)

    maps = []
    decisions: dict[tuple[str, str], FeatureCorrespondence] = {}
    reference_partners: dict[str, list[tuple[str, str]]] = {
        feature.key: [] for feature in reference
    }
    reference_reasons: dict[str, list[tuple[PairingState, tuple]]] = {
        feature.key: [] for feature in reference
    }
    for batch in sorted(set(batch_sizes) - {reference_batch}):
        batch_features = by_batch[batch]
        batch_map = _fit_batch_map(
            batch,
            batch_features,
            reference,
            istd_rt,
            reference_batch=reference_batch,
            batch_sizes=batch_sizes,
            channel=channel,
            multiplier=multiplier,
            floor_min=floor_min,
        )
        maps.append(batch_map)
        by_identity = _identity_pairs(batch_features, reference)
        identity_partners = {partner.key for partner in by_identity.values()}
        unbound = [item for item in batch_features if item.key not in by_identity]
        unbound_reference = [
            item for item in reference if item.key not in identity_partners
        ]
        forward = batch_map.to_reference if batch_map.calibration is not None else None
        reverse = (
            batch_map.from_reference if batch_map.calibration is not None else None
        )

        def half_width(
            item: BatchFeature,
            batch_map: BatchMap = batch_map,
            forward: Callable[[float], float | None] | None = forward,
        ) -> float | None:
            position = (
                item.reference_rt_min
                if item.batch_id == reference_batch
                else None
                if forward is None
                else forward(item.reference_rt_min)
            )
            return (
                None if position is None else batch_map.search_half_width_at(position)
            )

        for feature in batch_features:
            identity_partner = by_identity.get(feature.key)
            if identity_partner is not None:
                decisions[(batch, feature.key)] = FeatureCorrespondence(
                    batch,
                    feature.key,
                    "paired",
                    ((reference_batch, identity_partner.key),),
                    None if forward is None else forward(feature.reference_rt_min),
                    (),
                )
                reference_partners[identity_partner.key].append((batch, feature.key))
                continue
            if forward is None or reverse is None:
                decisions[(batch, feature.key)] = FeatureCorrespondence(
                    batch, feature.key, "unpaired_no_map", (), None, ()
                )
                continue
            match_state, partner, rivals = _match(
                feature,
                unbound_reference,
                unbound,
                forward,
                reverse,
                half_width,
                channel,
            )
            decisions[(batch, feature.key)] = FeatureCorrespondence(
                batch,
                feature.key,
                match_state,
                () if partner is None else ((reference_batch, partner.key),),
                forward(feature.reference_rt_min),
                tuple((reference_batch, item.key) for item in rivals),
            )
            if partner is not None:
                reference_partners[partner.key].append((batch, feature.key))
        for feature in unbound_reference:
            if forward is None or reverse is None:
                reference_reasons[feature.key].append(("unpaired_no_map", ()))
                continue
            match_state, _partner, rivals = _match(
                feature,
                unbound,
                unbound_reference,
                reverse,
                forward,
                half_width,
                channel,
            )
            reference_reasons[feature.key].append(
                (match_state, tuple((batch, item.key) for item in rivals))
            )
    for feature in reference:
        partners = tuple(sorted(reference_partners[feature.key]))
        if partners:
            state: PairingState = "paired"
            competitors: tuple[tuple[str, str], ...] = ()
        else:
            state, competitors = min(
                reference_reasons[feature.key] or [("unpaired_no_candidate", ())],
                key=lambda item: _UNPAIRED_PRIORITY[item[0]],
            )
        decisions[(reference_batch, feature.key)] = FeatureCorrespondence(
            reference_batch,
            feature.key,
            state,
            partners,
            feature.reference_rt_min,
            competitors,
        )
    ordered = sorted(
        decisions.values(),
        key=lambda item: (item.batch_id != reference_batch, item.batch_id, item.key),
    )
    return BatchCorrespondence(reference_batch, tuple(maps), tuple(ordered))


_UNPAIRED_PRIORITY: dict[str, int] = {
    "paired": 0,
    "unpaired_ambiguous": 1,
    "unpaired_outside_window": 2,
    "unpaired_no_candidate": 3,
    "unpaired_no_map": 4,
}


def _channel_matcher(
    tolerance_ppm: float,
) -> Callable[[BatchFeature, BatchFeature], bool]:
    def same_channel(feature: BatchFeature, other: BatchFeature) -> bool:
        return feature.neutral_loss_tag == other.neutral_loss_tag and (
            within_ppm(feature.precursor_mz, other.precursor_mz, tolerance_ppm)
            and within_ppm(other.precursor_mz, feature.precursor_mz, tolerance_ppm)
        )

    return same_channel


def _window(
    feature: BatchFeature,
    others: Sequence[BatchFeature],
    predicted: float,
    half_width: float,
    channel: Callable[[BatchFeature, BatchFeature], bool],
) -> tuple[bool, list[BatchFeature]]:
    same = [other for other in others if channel(feature, other)]
    return bool(same), [
        other for other in same if abs(other.reference_rt_min - predicted) <= half_width
    ]


def _match(
    feature: BatchFeature,
    others: Sequence[BatchFeature],
    own: Sequence[BatchFeature],
    forward: Callable[[float], float | None],
    reverse: Callable[[float], float | None],
    half_width: Callable[[BatchFeature], float | None],
    channel: Callable[[BatchFeature, BatchFeature], bool],
) -> tuple[PairingState, BatchFeature | None, list[BatchFeature]]:
    predicted = forward(feature.reference_rt_min)
    width = half_width(feature)
    if predicted is None or width is None:
        return "unpaired_no_map", None, []
    any_channel, found = _window(feature, others, predicted, width, channel)
    if not any_channel:
        return "unpaired_no_candidate", None, []
    if not found:
        return "unpaired_outside_window", None, []
    if len(found) > 1:
        return "unpaired_ambiguous", None, found
    partner = found[0]
    back = reverse(partner.reference_rt_min)
    back_width = half_width(partner)
    if back is None or back_width is None:
        return "unpaired_no_map", None, []
    _, returned = _window(partner, own, back, back_width, channel)
    if [item.key for item in returned] != [feature.key]:
        return (
            "unpaired_ambiguous",
            None,
            [partner, *(item for item in returned if item.key != feature.key)],
        )
    return "paired", partner, []


def _offset_predictor(
    points: Sequence[tuple[float, float]],
) -> Callable[[float], float]:
    """Linear offset interpolation over ``(x, y)``; endpoint offsets outside."""
    ordered = sorted(points)

    def predict(x: float) -> float:
        if x <= ordered[0][0]:
            return x - ordered[0][0] + ordered[0][1]
        if x >= ordered[-1][0]:
            return x - ordered[-1][0] + ordered[-1][1]
        for (x0, y0), (x1, y1) in zip(ordered, ordered[1:], strict=False):
            if x0 <= x <= x1:
                offset0, offset1 = x0 - y0, x1 - y1
                fraction = (x - x0) / (x1 - x0) if x1 > x0 else 0.0
                return x - (offset0 + fraction * (offset1 - offset0))
        raise AssertionError("unreachable")

    return predict


def _interpolate(points: Sequence[tuple[float, float]], x: float) -> float:
    """Linear interpolation over sorted ``(x, y)``; the nearest y outside."""
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
        if x <= x1:
            return y0 + (x - x0) / (x1 - x0) * (y1 - y0) if x1 > x0 else y1
    return points[-1][1]


def _fit_batch_map(
    batch: str,
    batch_features: Sequence[BatchFeature],
    reference: Sequence[BatchFeature],
    istd_rt: Mapping[tuple[str, str], float],
    *,
    reference_batch: str,
    batch_sizes: Mapping[str, int],
    channel: Callable[[BatchFeature, BatchFeature], bool],
    multiplier: float,
    floor_min: float,
) -> BatchMap:
    labels = sorted(
        label
        for (owner, label) in istd_rt
        if owner == batch and (reference_batch, label) in istd_rt
    )
    istds = tuple(
        (label, istd_rt[(batch, label)], istd_rt[(reference_batch, label)])
        for label in labels
    )
    if len(istds) < 2:
        return BatchMap(batch, istds, {}, None, (), (), None, {}, ())
    coarse_loo = {
        label: abs(
            _offset_predictor([(x, y) for other, x, y in istds if other != label])(x)
            - y
        )
        for label, x, y in istds
    }
    coarse_half_width = max(floor_min, multiplier * max(coarse_loo.values()))
    forward = _offset_predictor([(x, y) for _, x, y in istds])
    reverse = _offset_predictor([(y, x) for _, x, y in istds])

    batch_floor = ordinary_anchor_min_samples(batch_sizes[batch])
    reference_floor = ordinary_anchor_min_samples(batch_sizes[reference_batch])
    pairs: list[tuple[BatchFeature, BatchFeature]] = []
    excluded: list[ExcludedAnchor] = []
    for feature in batch_features:
        if feature.sample_count < batch_floor:
            continue
        state, partner, competitors = _match(
            feature,
            reference,
            batch_features,
            forward,
            reverse,
            lambda _item: coarse_half_width,
            channel,
        )
        if partner is not None and partner.sample_count < reference_floor:
            excluded.append(
                ExcludedAnchor(
                    feature.key, feature.reference_rt_min, "counterpart_below_threshold"
                )
            )
        elif partner is not None:
            pairs.append((feature, partner))
        else:
            excluded.append(
                ExcludedAnchor(
                    feature.key,
                    feature.reference_rt_min,
                    "competitor"
                    if state == "unpaired_ambiguous"
                    and any(item.batch_id == batch for item in competitors)
                    else _ANCHOR_EXCLUSION[state],
                )
            )
    anchors = _elution_families(istds, pairs)
    calibration = _fit(batch, anchors)
    loo = {}
    for label, x, y in istds:
        held_out = _fit(
            batch, tuple(anchor for anchor in anchors if anchor.istd_label != label)
        )
        estimate = held_out.estimate_rt(x) if held_out is not None else None
        if estimate is not None:
            loo[label] = abs(estimate.rt_min - y)
    search_window = (
        tuple(
            sorted(
                (y, max(floor_min, multiplier * loo[label]))
                for label, _x, y in istds
                if label in loo
            )
        )
        if loo
        else tuple(sorted((y, coarse_half_width) for _label, _x, y in istds))
    )
    return BatchMap(
        batch_id=batch,
        coarse_istds=istds,
        coarse_loo_error_min=coarse_loo,
        coarse_half_width_min=coarse_half_width,
        anchors=anchors,
        excluded_anchors=tuple(excluded),
        calibration=calibration,
        loo_error_min=loo,
        search_window=search_window,
    )


_ANCHOR_EXCLUSION: dict[str, AnchorExclusion] = {
    "unpaired_no_candidate": "no_candidate",
    "unpaired_outside_window": "outside_window",
    "unpaired_ambiguous": "ambiguous",
    "unpaired_no_map": "outside_window",
}


def _elution_families(
    istds: Sequence[tuple[str, float, float]],
    pairs: Sequence[tuple[BatchFeature, BatchFeature]],
) -> tuple[CorrespondenceAnchor, ...]:
    """Anchors agreeing within the family tolerance in both batches vote once.

    A family containing an ISTD is that ISTD's measured position.
    """
    votes: list[CorrespondenceAnchor] = [
        CorrespondenceAnchor(x, y, label, ()) for label, x, y in istds
    ] + [
        CorrespondenceAnchor(
            feature.reference_rt_min,
            partner.reference_rt_min,
            None,
            ((feature.batch_id, feature.key), (partner.batch_id, partner.key)),
        )
        for feature, partner in pairs
    ]
    parent = list(range(len(votes)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for left in range(len(votes)):
        for right in range(left + 1, len(votes)):
            a, b = votes[left], votes[right]
            if (
                abs(a.rt_min - b.rt_min) <= ELUTION_FAMILY_MIN
                and abs(a.reference_rt_min - b.reference_rt_min) <= ELUTION_FAMILY_MIN
            ):
                parent[find(left)] = find(right)
    families: dict[int, list[CorrespondenceAnchor]] = {}
    for index, vote in enumerate(votes):
        families.setdefault(find(index), []).append(vote)
    anchors: list[CorrespondenceAnchor] = []
    for members in families.values():
        standards = sorted(
            (vote for vote in members if vote.istd_label is not None),
            key=lambda vote: vote.istd_label or "",
        )
        keys = tuple(sorted(key for vote in members for key in vote.feature_keys))
        if standards:
            anchors.extend(
                CorrespondenceAnchor(
                    vote.rt_min, vote.reference_rt_min, vote.istd_label, keys
                )
                for vote in standards
            )
        else:
            anchors.append(
                CorrespondenceAnchor(
                    float(median(vote.rt_min for vote in members)),
                    float(median(vote.reference_rt_min for vote in members)),
                    None,
                    keys,
                )
            )
    return tuple(sorted(anchors, key=lambda item: (item.rt_min, item.reference_rt_min)))


def _fit(
    batch: str, anchors: Sequence[CorrespondenceAnchor]
) -> SampleRtCalibration | None:
    if len({anchor.rt_min for anchor in anchors}) < 2:
        return None
    return fit_sample_rt_calibration(
        batch,
        tuple((anchor.rt_min, anchor.reference_rt_min) for anchor in anchors),
        fraction=RtCalibrationPolicy().loess_fraction,
        standards=tuple(
            (anchor.istd_label, anchor.rt_min, anchor.reference_rt_min)
            for anchor in anchors
            if anchor.istd_label is not None
        ),
        has_ordinary_support=any(anchor.istd_label is None for anchor in anchors),
    )


def _identity_pairs(
    features: Sequence[BatchFeature], reference: Sequence[BatchFeature]
) -> dict[str, BatchFeature]:
    """Features bound to the same known ISTD in both batches, by identity."""
    by_label: dict[str, BatchFeature] = {}
    for feature in reference:
        label = feature.known_identity_label
        if label is not None and by_label.setdefault(label, feature) is not feature:
            raise ValueError(f"two Matrix features bound to one identity: {label}")
    pairs: dict[str, BatchFeature] = {}
    seen: set[str] = set()
    for feature in features:
        label = feature.known_identity_label
        if label is None:
            continue
        if label in seen:
            raise ValueError(f"two Matrix features bound to one identity: {label}")
        seen.add(label)
        if label in by_label:
            pairs[feature.key] = by_label[label]
    return pairs


def batch_feature(
    batch_id: str,
    group: CrossSamplePeakGroupHypothesis,
    calibration: BatchRtCalibration | None,
) -> BatchFeature:
    """A Matrix group's channel and its members' median batch coordinate.

    A member without a calibrated estimate keeps its measured apex.
    """
    positions = []
    for owner in group.owners:
        observed = owner.peak.rt_apex_min
        sample = (
            calibration.samples.get(owner.raw_stem) if calibration is not None else None
        )
        estimate = sample.estimate_rt(observed) if sample is not None else None
        positions.append(observed if estimate is None else estimate.rt_min)
    return BatchFeature(
        batch_id=batch_id,
        key=group.group_hypothesis_id,
        neutral_loss_tag=group.neutral_loss_tag,
        precursor_mz=group.group_center_mz,
        reference_rt_min=float(median(positions)),
        sample_count=len({owner.raw_stem for owner in group.owners}),
        known_identity_label=None
        if group.inference_basis is None
        else group.inference_basis.known_identity_label,
    )


def batch_istds(
    batch_id: str, calibration: BatchRtCalibration | None
) -> tuple[BatchIstd, ...]:
    """The batch's measured ISTD knots, by identity label."""
    return tuple(
        BatchIstd(batch_id, knot.istd_label, knot.reference_rt_min)
        for knot in (calibration.knots if calibration is not None else ())
        if knot.istd_label is not None
    )


def compose_calibrations(
    calibrations: Mapping[str, BatchRtCalibration | None],
    correspondence: BatchCorrespondence,
) -> BatchRtCalibration | None:
    """One replayable calibration in the reference batch's coordinate.

    Each sample's map is followed by its batch's map; knots stay in their
    own batch's reference coordinate. A batch without a map keeps its own
    coordinate. Weak estimates outside a sample's knots keep their endpoint
    and median-shift semantics on the composed values.
    """
    present = {batch: item for batch, item in calibrations.items() if item}
    if not present:
        return None
    samples: dict[str, SampleRtCalibration] = {}
    for batch, calibration in sorted(present.items()):
        batch_map = correspondence.map_for(batch)
        for stem, sample in calibration.samples.items():
            samples[stem] = (
                sample
                if batch_map is None or batch_map.calibration is None
                else _compose_sample(sample, batch_map)
            )
    reference = present.get(correspondence.reference_batch)
    base = reference or next(iter(present.values()))
    return BatchRtCalibration(
        policy=base.policy,
        knots=tuple(
            knot for _batch, item in sorted(present.items()) for knot in item.knots
        ),
        samples=dict(sorted(samples.items())),
        front_shift_center_min=None
        if reference is None
        else reference.front_shift_center_min,
        front_shift_mad_min=None
        if reference is None
        else reference.front_shift_mad_min,
        perturbation_tolerance_min=None
        if reference is None
        else reference.perturbation_tolerance_min,
    )


def _compose_sample(
    sample: SampleRtCalibration, batch_map: BatchMap
) -> SampleRtCalibration:
    to_reference = batch_map.to_reference
    knots = list(sample.knots)
    if knots and batch_map.calibration is not None:
        lower, upper = knots[0][1], knots[-1][1]
        inverse = tuple((reference, observed) for observed, reference in knots)
        for x, _y in batch_map.calibration.knots:
            if lower < x < upper:
                observed = interpolate_calibrated_rt_min(x, inverse)
                if observed is not None:
                    knots.append((observed, x))
    composed = tuple(
        sorted(
            {
                observed: value
                for observed, reference in knots
                if (value := to_reference(reference)) is not None
            }.items()
        )
    )
    support = sample.estimate_support
    if support is not None:
        middle = float(median(support.positions_min))
        shifted = to_reference(middle + support.median_shift_min)
        support = RtEstimateSupport(
            support.positions_min,
            support.median_shift_min if shifted is None else shifted - middle,
            support.shift_spread_min,
            support.fit_spread_min,
        )
    return replace(sample, knots=composed, estimate_support=support)
