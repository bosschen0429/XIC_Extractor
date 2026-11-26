"""Fill settled rows' missing samples with peaks already measured there.

Final grouping scores only peaks with their own method evidence. An MS1-only
peak then enters a row only in a sample the row lacks, localized exactly as a
Backfill revisit localizes it: the row's trajectory forecast with its frozen
drift, bounded by held-out direct-reference residuals. A peak whose apex lies
within that bound qualifies, nearest first; without one, a peak whose
integration bounds contain the query does. A peak that several rows localize
goes to the row it lies nearest relative to that row's bound. Ties are broken
by measured peak facts, never by identifiers or input order, and every choice
between qualified alternatives is recorded as accepted competition pointing at
the alternative. A row admits its fills best localized first while its
precursor masses stay within the full-group tolerance. MS1-only peaks that no
row takes stay single, and Backfill revisits the samples left empty.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from xic_extractor.alignment.backfill.cid_nl_planning import (
    BACKFILL_HALF_WINDOW_MIN,
    group_drift_offsets,
)
from xic_extractor.alignment.cid_nl_grouping import (
    CID_NL_GROUPING_RULES,
    has_cid_nl_activation_support,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSamplePeakGroupHypothesis,
    hypothesis_from_membership,
)
from xic_extractor.alignment.feature_membership_inference import (
    SparseFeatureDecision,
)
from xic_extractor.alignment.membership_selection import (
    AcceptedMembershipRisk,
    comparable_distance,
    measured_owner_key,
    owner_facts,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.alignment.peak_trajectories import PeakTrajectories
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.mass_accuracy import within_ppm


@dataclass(frozen=True, slots=True)
class _Claim:
    """One row's localized choice of a peak in a sample that row lacks."""

    distance: float
    row_key: tuple[object, ...]
    row: int
    location_basis: str
    runner_up: SampleLocalMS1Owner | None
    runner_up_distance: float | None


@dataclass(frozen=True, slots=True)
class _Fill:
    peak: SampleLocalMS1Owner
    location_basis: str
    distance: float
    alternative: tuple[tuple[str, ...], ...] | None
    alternative_distance: float | None


def _localized_peaks(
    peaks: Sequence[SampleLocalMS1Owner], query_rt: float, tolerance_min: float
) -> list[SampleLocalMS1Owner]:
    """Qualified peaks, best first: apex within the bound, else holding the query."""

    def gap(peak: SampleLocalMS1Owner) -> float:
        return comparable_distance(abs(peak.peak.rt_apex_min - query_rt))

    near = [p for p in peaks if gap(p) <= comparable_distance(tolerance_min)]
    if near:
        return sorted(near, key=lambda p: (gap(p), measured_owner_key(p)))
    return sorted(
        (p for p in peaks if p.peak.rt_left_min <= query_rt <= p.peak.rt_right_min),
        key=measured_owner_key,
    )


def _with_fills(
    row: CrossSamplePeakGroupHypothesis,
    fills: Sequence[_Fill],
    owners_by_id: Mapping[str, SampleLocalMS1Owner],
) -> CrossSamplePeakGroupHypothesis:
    basis = row.inference_basis
    assert basis is not None
    owners = tuple(
        sorted(
            (*row.owners, *(fill.peak for fill in fills)),
            key=lambda owner: (owner.sample_stem, owner.owner_id),
        )
    )
    owner_ids = tuple(owner.owner_id for owner in owners)
    feature_key = next(
        (d.feature_key for d in row.inference_decisions if d.feature_key),
        f"window:{basis.owner_ids[0]}",
    )

    def risk(fill: _Fill) -> AcceptedMembershipRisk | None:
        if fill.alternative is None or fill.alternative_distance is None:
            return None
        return AcceptedMembershipRisk(
            "evaluated_alternative",
            "initial",
            owner_facts(owners_by_id, owner_ids),
            tuple(sorted(owner_facts(owners_by_id, g) for g in fill.alternative)),
            (),
            fill.distance,
            fill.alternative_distance,
            fill.alternative_distance - fill.distance,
            fill.location_basis,
            "localized_fill;nearest_within_backfill_tolerance",
            (),
            ("accepted_competition",),
        )

    decisions = (
        *row.inference_decisions,
        *(
            SparseFeatureDecision(
                "window",
                fill.peak.sample_stem,
                fill.peak.owner_id,
                feature_key,
                "assigned",
                f"localized_fill:{fill.location_basis}",
                accepted_risk=risk(fill),
            )
            for fill in fills
        ),
    )
    return hypothesis_from_membership(
        owners,
        replace(basis, owner_ids=owner_ids),
        decisions,
        CID_NL_GROUPING_RULES,
    )


def fill_rows_from_measured_peaks(
    rows: Sequence[CrossSamplePeakGroupHypothesis],
    peaks: Sequence[SampleLocalMS1Owner],
    *,
    trajectories: PeakTrajectories,
    rt_frame: SampleRtFrame,
    sample_universe: frozenset[str],
    config: AlignmentConfig,
) -> tuple[tuple[CrossSamplePeakGroupHypothesis, ...], tuple[SampleLocalMS1Owner, ...]]:
    """The rows with their localized fills, and the MS1-only peaks left single."""
    by_sample: dict[str, list[SampleLocalMS1Owner]] = defaultdict(list)
    for peak in peaks:
        if peak.quantification_eligible:
            by_sample[peak.sample_stem].append(peak)
    claims: dict[str, list[_Claim]] = defaultdict(list)
    for index, row in enumerate(rows):
        basis = row.inference_basis
        if row.disposition != "established" or (
            basis is not None and basis.known_identity_label is not None
        ):
            continue
        reference = frozenset(
            owner.sample_stem
            for owner in row.owners
            if has_cid_nl_activation_support(owner)
        )
        missing = tuple(sorted(sample_universe - {o.sample_stem for o in row.owners}))
        if len(reference) < 2 or not missing:
            continue
        locations = trajectories.trajectory_for(row.owners).backfill_locations(
            missing,
            reference_samples=reference,
            rt_frame=rt_frame,
            backfill_half_window_min=BACKFILL_HALF_WINDOW_MIN,
            drift_offsets=group_drift_offsets(row, rt_frame),
        )
        masses = [owner.peak.mz for owner in row.owners]
        low, high = min(masses), max(masses)
        row_key = tuple(sorted(measured_owner_key(owner) for owner in row.owners))
        for sample, localized in locations.items():
            if localized.apex_tolerance_sec is None:
                continue
            tolerance = localized.apex_tolerance_sec / 60.0
            query = localized.location.query_rt
            ranked = _localized_peaks(
                [
                    p
                    for p in by_sample.get(sample, ())
                    if within_ppm(
                        max(high, p.peak.mz),
                        min(low, p.peak.mz),
                        config.precursor_mz_tolerance_ppm,
                    )
                ],
                query,
                tolerance,
            )
            if not ranked:
                continue
            distances = [
                comparable_distance(abs(p.peak.rt_apex_min - query) / tolerance)
                for p in ranked
            ]
            runner_up = ranked[1] if len(ranked) > 1 else None
            claims[ranked[0].owner_id].append(
                _Claim(
                    distances[0],
                    row_key,
                    index,
                    localized.location.basis,
                    runner_up,
                    distances[1] if runner_up is not None else None,
                )
            )
    owners_by_id = {
        owner.owner_id: owner
        for owner in (*peaks, *(o for row in rows for o in row.owners))
    }
    fills: dict[int, list[_Fill]] = defaultdict(list)
    for owner_id, entries in claims.items():
        entries.sort(key=lambda claim: (claim.distance, claim.row_key))
        chosen = entries[0]
        peak = owners_by_id[owner_id]
        row_owner_ids = tuple(o.owner_id for o in rows[chosen.row].owners)
        alternative: tuple[tuple[str, ...], ...] | None = None
        alternative_distance = None
        if len(entries) > 1:
            other = entries[1]
            alternative = (
                row_owner_ids,
                (*(o.owner_id for o in rows[other.row].owners), owner_id),
            )
            alternative_distance = other.distance
        elif chosen.runner_up is not None:
            alternative = ((*row_owner_ids, chosen.runner_up.owner_id),)
            alternative_distance = chosen.runner_up_distance
        fills[chosen.row].append(
            _Fill(
                peak,
                chosen.location_basis,
                chosen.distance,
                alternative,
                alternative_distance,
            )
        )
    admitted = {
        index: kept
        for index, added in fills.items()
        if (kept := _admitted(rows[index], added, config.precursor_mz_tolerance_ppm))
    }
    placed = {fill.peak.owner_id for kept in admitted.values() for fill in kept}
    return (
        tuple(
            _with_fills(row, admitted[index], owners_by_id)
            if index in admitted
            else row
            for index, row in enumerate(rows)
        ),
        tuple(peak for peak in peaks if peak.owner_id not in placed),
    )


def _admitted(
    row: CrossSamplePeakGroupHypothesis,
    fills: Sequence[_Fill],
    tolerance_ppm: float,
) -> list[_Fill]:
    """Best-localized fills first, while the row keeps its full precursor tolerance."""
    masses = [owner.peak.mz for owner in row.owners]
    low, high = min(masses), max(masses)
    kept = []
    for fill in sorted(fills, key=lambda f: (f.distance, measured_owner_key(f.peak))):
        mz = fill.peak.peak.mz
        if within_ppm(max(high, mz), min(low, mz), tolerance_ppm):
            kept.append(fill)
            low, high = min(low, mz), max(high, mz)
    return kept
