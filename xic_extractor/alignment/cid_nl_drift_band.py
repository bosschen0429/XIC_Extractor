"""Fix a recurring CID-NL row's direct members from their drift-corrected RT band.

After the batch drift model is frozen, a scalar CID-NL channel whose direct peaks
recur in many samples has one expected corrected RT. Each sample's direct member
is chosen against the band estimated from the other samples' sole direct peaks:
the nearest direct apex inside the band, else a direct envelope covering the
band centre. A direct peak far outside the band keeps its CID-NL evidence but
does not occupy the row. Samples the row lacks are filled afterwards by the
Backfill localization shared by every settled row.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from math import floor
from statistics import median

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.feature_membership_inference import (
    FeatureMembershipGroup,
    SparseFeatureDecision,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.alignment.rt_drift import DriftAnchor, DriftCoefficients
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.mass_accuracy import within_ppm

_BAND_MIN_REFERENCES = 21
_REFERENCE_METHODS = ("loess", "retained_map")


@dataclass(frozen=True, slots=True)
class BandCandidate:
    """One measured peak; apex and integration limits are in corrected minutes."""

    owner_id: str
    quantifiable: bool
    method: str | None
    apex: float | None
    left: float | None
    right: float | None


@dataclass(frozen=True, slots=True)
class BandSlot:
    sample: str
    peers: int
    center: float | None
    radius: float | None
    selected: str | None
    reason: str
    candidates: tuple[BandCandidate, ...]


def _decide(
    sample: str,
    candidates: tuple[BandCandidate, ...],
    peers: int,
    center: float | None,
    radius: float | None,
) -> BandSlot:
    chosen = None
    reason = "insufficient_peers"
    if center is not None and radius is not None:
        ranked = sorted(
            (abs(c.apex - center), c.owner_id)
            for c in candidates
            if c.apex is not None and abs(c.apex - center) <= radius
        )
        reason = "direct_apex"
        if not ranked:
            ranked = [
                (0.0, c.owner_id)
                for c in candidates
                if c.quantifiable
                and c.left is not None
                and c.right is not None
                and c.left <= center <= c.right
            ]
            reason = "direct_envelope"
        if ranked:
            if len(ranked) == 1 or ranked[0][0] < ranked[1][0]:
                chosen = ranked[0][1]
            else:
                reason = "competing_candidates"
        else:
            reason = "no_compatible_peak"
    return BandSlot(sample, peers, center, radius, chosen, reason, candidates)


def assign_drift_band(
    owners: Sequence[SampleLocalMS1Owner],
    samples: Sequence[str],
    rt_frame: SampleRtFrame,
    anchor: DriftAnchor | None,
) -> tuple[BandSlot, ...]:
    """Estimate the band from in-range direct peers, excluding the query sample.

    Only direct peaks are candidates; MS1-only peaks never occupy a band slot.
    """
    drift = rt_frame.drift_model
    by_sample: dict[str, list[BandCandidate]] = defaultdict(list)
    for o in owners:
        if o.primary_identity_event is None:
            continue
        s, p = o.sample_stem, o.peak
        offset = (
            anchor.coefficients.offset_min(
                anchor.shape.get(s), drift.sample_shift_min.get(s) if drift else None
            )
            if anchor
            else 0.0
        )
        est = rt_frame.membership_coordinate(o) if offset is not None else None
        left = rt_frame.calibrated_rt_min(s, p.rt_left_min)
        right = rt_frame.calibrated_rt_min(s, p.rt_right_min)
        by_sample[s].append(
            BandCandidate(
                o.owner_id,
                o.quantification_eligible,
                est.method if est else None,
                est.rt_min - offset if est and offset is not None else None,
                left - offset if left is not None and offset is not None else None,
                right - offset if right is not None and offset is not None else None,
            )
        )
    references = []
    for s, cs in by_sample.items():
        if (
            len(cs) == 1
            and cs[0].method in _REFERENCE_METHODS
            and cs[0].apex is not None
        ):
            references.append((cs[0].apex, s))
    references.sort()
    rank = {s: i for i, (_, s) in enumerate(references)}

    def quantile(q: float, n: int, skip: int) -> float:
        pos = (n - 1) * q
        j = floor(pos)
        k = min(j + 1, n - 1)
        a = references[j + int(j >= skip)][0]
        b = references[k + int(k >= skip)][0]
        return a + (b - a) * (pos - j)

    slots = []
    for s in samples:
        skip = rank.get(s, len(references))
        n = len(references) - int(s in rank)

        center = radius = None
        if len(references) >= _BAND_MIN_REFERENCES:
            center = quantile(0.5, n, skip)
            radius = quantile(0.95, n, skip) - quantile(0.05, n, skip)
        slots.append(
            _decide(
                s,
                tuple(sorted(by_sample[s], key=lambda c: c.owner_id)),
                n,
                center,
                radius,
            )
        )
    return tuple(slots)


def _scalar_channel(
    owners: Sequence[SampleLocalMS1Owner],
    rt_frame: SampleRtFrame,
    config: AlignmentConfig,
    known_identity_bindings: Sequence[tuple[str, str]],
) -> bool:
    """One calibrated scalar CID-NL channel whose owners carry no known identity."""
    if rt_frame.drift_source != "batch_anchor_calibration":
        return False
    owner_ids = {o.owner_id for o in owners}
    bindings = (*known_identity_bindings, *rt_frame.known_identity_bindings)
    if any(o.cid_nl_composite_assessment is not None for o in owners) or any(
        owner_id in owner_ids for owner_id, _ in bindings
    ):
        return False
    events = [
        o.primary_identity_event for o in owners if o.primary_identity_event is not None
    ]
    if len({e.neutral_loss_tag for e in events}) > 1:
        return False
    for values, ppm in (
        ([o.peak.mz for o in owners], config.precursor_mz_tolerance_ppm),
        ([e.product_mz for e in events], config.product_mz_tolerance_ppm),
    ):
        if values:
            center = median(values)
            if any(not within_ppm(v, center, ppm) for v in values):
                return False
    return True


def drift_band_membership(
    owners: Sequence[SampleLocalMS1Owner],
    *,
    channel: str,
    samples: Sequence[str],
    rt_frame: SampleRtFrame,
    config: AlignmentConfig,
    known_identity_bindings: Sequence[tuple[str, str]] = (),
) -> tuple[FeatureMembershipGroup, tuple[SparseFeatureDecision, ...]] | None:
    """The channel's band row and each member's reason, or None to group ordinarily.

    ``channel`` is the workset's method subject key; the drift anchor of a single
    subject supplies its corrected coordinate. A row whose precursor masses span
    more than the tolerance that ordinary membership enforces is not fixed.
    """
    if not _scalar_channel(owners, rt_frame, config, known_identity_bindings):
        return None
    drift = rt_frame.drift_model
    anchor = (
        next((a for a in drift.anchors if a.channel == channel), None)
        if drift
        else None
    )
    chosen = {
        slot.selected: slot
        for slot in assign_drift_band(owners, samples, rt_frame, anchor)
        if slot.selected is not None
    }
    if not chosen:
        return None
    ids = tuple(sorted(chosen))
    precursors = {o.owner_id: o.peak.mz for o in owners if o.owner_id in chosen}
    if not within_ppm(
        max(precursors.values()),
        min(precursors.values()),
        config.precursor_mz_tolerance_ppm,
    ):
        return None
    basis = FeatureMembershipGroup(
        ids,
        "assigned",
        "window_level_rt_band",
        "corrected_cid_nl_reference_band",
        rt_drift=anchor.coefficients if anchor else DriftCoefficients(),
    )
    decisions = tuple(
        SparseFeatureDecision(
            "window",
            chosen[k].sample,
            k,
            f"window:{ids[0]}",
            "assigned",
            chosen[k].reason,
            rt_basis="drift_corrected",
        )
        for k in ids
    )
    return basis, decisions
