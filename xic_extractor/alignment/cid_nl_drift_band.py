"""Assign a recurring CID-NL row from its peer-supported, drift-corrected RT band."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from math import floor

from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.alignment.rt_drift import DriftAnchor, RtDriftModel
from xic_extractor.alignment.sample_rt_calibration import BatchRtCalibration


@dataclass(frozen=True, slots=True)
class BandCandidate:
    """One measured peak; apex and integration limits are in corrected minutes."""

    owner_id: str
    direct: bool
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
        direct = [c for c in candidates if c.direct]
        unseen = any(c.apex is None for c in direct)
        inside = [
            c
            for c in candidates
            if c.apex is not None and abs(c.apex - center) <= radius
        ]
        ranked = [
            (abs(c.apex - center), c.owner_id)
            for c in inside
            if c.direct and c.apex is not None
        ]
        reason = "direct_apex"
        if not ranked and not unseen:
            ranked = [
                (abs(c.apex - center), c.owner_id)
                for c in inside
                if not c.direct
                and c.quantifiable
                and c.apex is not None
                and c.method in ("loess", "retained_map")
            ]
            reason = "ms1_only_apex"
            if not ranked:
                envelopes = [
                    c
                    for c in candidates
                    if c.quantifiable
                    and c.left is not None
                    and c.right is not None
                    and c.left <= center <= c.right
                ]
                supported = [c for c in envelopes if c.direct]
                ranked = [(0.0, c.owner_id) for c in (supported or envelopes)]
                reason = "direct_envelope" if supported else "ms1_only_envelope"
        if ranked:
            ranked.sort()
            if len(ranked) == 1 or ranked[0][0] < ranked[1][0]:
                chosen = ranked[0][1]
            else:
                reason = "competing_candidates"
        else:
            reason = "unknown_direct_coordinate" if unseen else "no_compatible_peak"
    return BandSlot(sample, peers, center, radius, chosen, reason, candidates)


def assign_drift_band(
    owners: Sequence[SampleLocalMS1Owner],
    samples: Sequence[str],
    calibration: BatchRtCalibration,
    drift: RtDriftModel | None,
    anchor: DriftAnchor | None,
) -> tuple[BandSlot, ...]:
    """Estimate the band from in-range direct peers, excluding the query sample."""
    by_sample: dict[str, list[BandCandidate]] = defaultdict(list)
    for o in owners:
        s, p = o.sample_stem, o.peak
        cal = calibration.samples.get(s)
        offset = (
            anchor.coefficients.offset_min(
                anchor.shape.get(s), drift.sample_shift_min.get(s) if drift else None
            )
            if anchor
            else 0.0
        )
        est = (
            cal.estimate_rt(p.rt_apex_min)
            if cal is not None and offset is not None
            else None
        )
        left = cal.calibrated_rt(p.rt_left_min) if cal is not None else None
        right = cal.calibrated_rt(p.rt_right_min) if cal is not None else None
        by_sample[s].append(
            BandCandidate(
                o.owner_id,
                o.primary_identity_event is not None,
                o.quantification_eligible,
                est.method if est else None,
                est.rt_min - offset if est and offset is not None else None,
                left - offset if left is not None and offset is not None else None,
                right - offset if right is not None and offset is not None else None,
            )
        )
    references = []
    for s, cs in by_sample.items():
        ds = [c for c in cs if c.direct]
        if (
            len(ds) == 1
            and ds[0].method in ("loess", "retained_map")
            and ds[0].apex is not None
        ):
            references.append((ds[0].apex, s))
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
        if len(references) >= 21:
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
