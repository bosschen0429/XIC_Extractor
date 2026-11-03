"""Compare retained Targeted regions in their chromatographic batch context.

Measurement, reference plausibility and identity preference are separate. A
ragged top is not a missing measurement; a large unrelated contour is not a
retention-time calibrator merely because it dominates its own XIC.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import cached_property
from statistics import median

import numpy as np

from xic_extractor.peak_detection.physical_peak_models import (
    PhysicalSegment,
    PreparedSignal,
)
from xic_extractor.peak_detection.selection import ANCHOR_SELECTION_MIN_INTENSITY_RATIO


@dataclass(frozen=True)
class TargetedRegionContext:
    segment: PhysicalSegment
    acquired_positive_count: int
    acquired_coverage: float
    longest_acquired_fraction: float
    acquired_rt_start_min: float | None
    acquired_rt_end_min: float | None
    acquired_modulation_observed: bool
    observed_component_count: int
    body_support: float
    width_min: float
    contour: tuple[float, ...]
    measurement_available: bool
    chemical_support: bool = False
    chemical_set_membership: bool = False

    @property
    def independent_body_supported(self) -> bool:
        return self.measurement_available and self.acquired_modulation_observed

    def contains_acquired_rt(self, rt_min: float) -> bool:
        return (
            self.acquired_rt_start_min is not None
            and self.acquired_rt_end_min is not None
            and self.acquired_rt_start_min <= rt_min <= self.acquired_rt_end_min
        )


def describe_targeted_region(
    prepared: PreparedSignal,
    segment: PhysicalSegment,
    *,
    measurement_available: bool,
    chemical_support: bool = False,
    chemical_set_membership: bool = False,
    constituent_segments: tuple[PhysicalSegment, ...] | None = None,
) -> TargetedRegionContext:
    """Retain body support and shape, not just the apex's quality category."""
    left, right = segment.start_index, segment.end_index
    raw = np.asarray(prepared.raw[left:right])
    acquired = raw > np.asarray(prepared.baseline[left:right])
    longest = current = 0
    for hit in acquired:
        current = current + 1 if hit else 0
        longest = max(longest, current)
    coverage = float(np.mean(acquired))
    longest_fraction = longest / len(acquired)
    signal = np.asarray(prepared.positive_signal[left:right])
    rt = np.asarray(prepared.rt[left:right])
    half_height = signal >= segment.height / 2
    width = float(np.ptp(rt[half_height])) if np.count_nonzero(half_height) > 1 else 0.0
    span = segment.right_rt_min - segment.left_rt_min
    positions = (rt - segment.left_rt_min) / span
    contour = np.interp(np.linspace(0, 1, 33), positions, signal) / segment.height
    return TargetedRegionContext(
        segment=segment,
        acquired_positive_count=int(np.count_nonzero(acquired)),
        acquired_coverage=coverage,
        longest_acquired_fraction=longest_fraction,
        acquired_rt_start_min=float(rt[acquired][0]) if np.any(acquired) else None,
        acquired_rt_end_min=float(rt[acquired][-1]) if np.any(acquired) else None,
        acquired_modulation_observed=_acquired_modulation_observed(raw),
        observed_component_count=(
            sum(
                segment.left_rt_min <= child.apex_rt_min <= segment.right_rt_min
                and _acquired_modulation_observed(
                    np.asarray(prepared.raw[child.start_index : child.end_index])
                )
                for child in constituent_segments
            )
            if constituent_segments is not None
            else 1
        ),
        body_support=segment.height * coverage * longest_fraction,
        width_min=width,
        contour=tuple(float(v) for v in contour),
        measurement_available=measurement_available,
        chemical_support=chemical_support,
        chemical_set_membership=chemical_set_membership,
    )


def _acquired_modulation_observed(raw: np.ndarray) -> bool:
    changes = np.diff(raw)
    return (
        bool(np.any(np.maximum.accumulate(changes > 0) & (changes < 0)))
        if len(changes)
        else False
    )


@dataclass(frozen=True)
class TargetedPopulationContext:
    sample_name: str
    target_label: str
    mz: float
    ppm_tol: float
    reference_rt_min: float
    regions: tuple[TargetedRegionContext, ...]
    reference_istd_rt_min: float | None = None
    # Capture's sample-local prior is the fallback when batch transfer has no
    # independent support; it is not the origin for another displacement.
    sample_reference_rt_min: float | None = None

    @cached_property
    def comparison_regions(self) -> tuple[TargetedRegionContext, ...]:
        measurable = tuple(r for r in self.regions if r.independent_body_supported)
        positive = tuple(
            r for r in measurable if r.chemical_support or r.chemical_set_membership
        )
        return _noncontained_regions(positive or measurable)

    @cached_property
    def competitors(self) -> tuple[TargetedRegionContext, ...]:
        population = self.comparison_regions
        if not population:
            return ()
        maximum = max(r.body_support for r in population)
        return tuple(
            r
            for r in population
            if r.body_support >= maximum * ANCHOR_SELECTION_MIN_INTENSITY_RATIO
        )

    @cached_property
    def calibration_region(self) -> TargetedRegionContext | None:
        # Paired references already live in the sample frame. Their offsets
        # cannot calibrate displacement from the method frame for other targets.
        if self.reference_istd_rt_min is not None:
            return None
        regions = self.competitors
        if len(regions) != 1:
            return None
        region = regions[0]
        # An unresolved total remains measurable, but its changing mixture of
        # lobes is not a localized homologue or a precise RT calibration point.
        return region if region.observed_component_count == 1 else None


def _noncontained_regions(
    regions: tuple[TargetedRegionContext, ...],
) -> tuple[TargetedRegionContext, ...]:
    """Prefer complete measurements within the remaining identity alternatives."""
    outer = []
    rightmost = -1
    for region in sorted(
        regions, key=lambda r: (r.segment.start_index, -r.segment.end_index)
    ):
        if region.segment.end_index > rightmost:
            outer.append(region)
            rightmost = region.segment.end_index
    return tuple(outer)


def _same_ion(
    left: TargetedPopulationContext, right: TargetedPopulationContext
) -> bool:
    return (
        abs(left.mz - right.mz)
        <= min(left.mz * left.ppm_tol, right.mz * right.ppm_tol) / 1e6
    )


def _overlaps(left: tuple[float, float], right: tuple[float, float]) -> bool:
    return max(left[0], right[0]) <= min(left[1], right[1])


def _offset_bounds(
    population: TargetedPopulationContext, region: TargetedRegionContext
) -> tuple[float, float]:
    return (
        region.segment.left_rt_min - population.reference_rt_min,
        region.segment.right_rt_min - population.reference_rt_min,
    )


@dataclass(frozen=True)
class TargetedPeerRegionTransfer:
    peer: TargetedPopulationContext
    assigned_region: TargetedRegionContext
    displacement_intervals_min: tuple[tuple[float, float], ...]

    def matches(
        self, region: TargetedRegionContext, peer_region: TargetedRegionContext
    ) -> bool:
        if (
            region.acquired_rt_start_min is None
            or region.acquired_rt_end_min is None
            or peer_region.acquired_rt_start_min is None
            or peer_region.acquired_rt_end_min is None
        ):
            return False
        return any(
            _overlaps(
                (region.acquired_rt_start_min, region.acquired_rt_end_min),
                (
                    peer_region.acquired_rt_start_min + left,
                    peer_region.acquired_rt_end_min + right,
                ),
            )
            for left, right in self.displacement_intervals_min
        )


@dataclass(frozen=True)
class TargetedBatchContext:
    populations: tuple[TargetedPopulationContext, ...]

    @cached_property
    def population_by_cell(self) -> dict[tuple[str, str], TargetedPopulationContext]:
        return {(p.sample_name, p.target_label): p for p in self.populations}

    @cached_property
    def _populations_by_sample(self) -> dict[str, list[TargetedPopulationContext]]:
        samples: dict[str, list[TargetedPopulationContext]] = {}
        for population in self.populations:
            samples.setdefault(population.sample_name, []).append(population)
        return samples

    @cached_property
    def _populations_by_target(self) -> dict[str, list[TargetedPopulationContext]]:
        targets: dict[str, list[TargetedPopulationContext]] = {}
        for population in self.populations:
            targets.setdefault(population.target_label, []).append(population)
        return targets

    @cached_property
    def _seeded_targets(self) -> frozenset[str]:
        return frozenset(
            p.target_label
            for p in self.populations
            if p.calibration_region is not None
            and _offset_bounds(p, p.competitors[0])[0]
            <= 0
            <= _offset_bounds(p, p.competitors[0])[1]
        )

    @cached_property
    def _retention_by_cell(
        self,
    ) -> dict[tuple[str, str], tuple[tuple[float, float], ...]]:
        return {
            (p.sample_name, p.target_label): self._retention_intervals(p)
            for p in self.populations
        }

    def retention_intervals(
        self, population: TargetedPopulationContext
    ) -> tuple[tuple[float, float], ...]:
        return self._retention_by_cell.get(
            (population.sample_name, population.target_label), ()
        )

    def _retention_intervals(
        self, population: TargetedPopulationContext
    ) -> tuple[tuple[float, float], ...]:
        # Reference-overlapping observations seed tentative homologues. Without
        # this independent link, unrelated peaks can fabricate a coherent but
        # false RT-shift mode (including several different precursor masses).
        anchors = tuple(
            p
            for p in self._populations_by_sample[population.sample_name]
            if p.target_label in self._seeded_targets
            and not _same_ion(p, population)
            and p.calibration_region is not None
        )
        # Two labels for the same ion are not independent corroboration. Keep
        # multiple supported shift modes, rather than imposing one global shift.
        return tuple(
            _offset_bounds(p, p.competitors[0])
            for p in anchors
            if any(
                not _same_ion(p, other)
                and _overlaps(
                    _offset_bounds(p, p.competitors[0]),
                    _offset_bounds(other, other.competitors[0]),
                )
                for other in anchors
            )
        )

    def plausible(
        self, population: TargetedPopulationContext, region: TargetedRegionContext
    ) -> bool:
        # A baseline bow on constant/monotonic raw background is a measurable
        # residual, not an independent chromatographic body. Preserve its
        # measurement/method facts without promoting it after a real remote
        # peak has been ruled out.
        if not region.independent_body_supported:
            return False
        if population.reference_istd_rt_min is not None:
            # An existing method-specific paired-position owner governs this
            # reference. A derived batch model must not overrule that stronger
            # input before the canonical coelution assessment is performed.
            return True
        if region.contains_acquired_rt(population.reference_rt_min):
            return True
        intervals = self.retention_intervals(population)
        if not intervals:
            # No supported transfer model: preserve a provisional mapping and
            # expose that missing context, not a universal guessed RT limit.
            return True
        margin = self.retention_uncertainty_min(population)
        limits = (
            min(left for left, _ in intervals) - margin,
            max(right for _, right in intervals) + margin,
        )
        return _overlaps(_offset_bounds(population, region), limits)

    def retention_uncertainty_min(self, population: TargetedPopulationContext) -> float:
        return self._retention_uncertainty_by_ion[(population.mz, population.ppm_tol)]

    @cached_property
    def _retention_uncertainty_by_ion(self) -> dict[tuple[float, float], float]:
        # The batch-wide uncertainty excludes this ion, not a particular cell.
        # All samples/assays with the same mass window consume the same answer.
        ions = {(p.mz, p.ppm_tol): p for p in self.populations}
        return {
            ion: self._retention_uncertainty(population)
            for ion, population in ions.items()
        }

    def _retention_uncertainty(self, population: TargetedPopulationContext) -> float:
        # Supported shift modes are not exact physical exclusion limits. Carry
        # the widths and between-sample spread of independent, reference-linked
        # bodies into plausibility. Supplied references remain uncertain priors.
        offsets: dict[str, list[float]] = {}
        widths = []
        for sample in self._populations_by_sample.values():
            external = tuple(
                (p, region)
                for p in sample
                if p.target_label in self._seeded_targets
                and not _same_ion(p, population)
                if (region := p.calibration_region) is not None
            )
            for p, region in external:
                if not any(
                    not _same_ion(p, other)
                    and _overlaps(
                        _offset_bounds(p, region), _offset_bounds(other, other_region)
                    )
                    for other, other_region in external
                ):
                    continue
                offsets.setdefault(p.target_label, []).append(
                    region.segment.apex_rt_min - p.reference_rt_min
                )
                widths.append(region.width_min)
        return max((*widths, *(max(v) - min(v) for v in offsets.values())), default=0.0)

    @cached_property
    def _conflicting_segments_by_cell(
        self,
    ) -> dict[tuple[str, str], frozenset[PhysicalSegment]]:
        return {
            (p.sample_name, p.target_label): self._conflicting_segments(p)
            for p in self.populations
        }

    def _conflicting_segments(
        self, population: TargetedPopulationContext
    ) -> frozenset[PhysicalSegment]:
        # Consume one cell's pairwise evidence, then retain its answer rather
        # than every sample-pair/anchor tuple for the lifetime of the batch.
        transfers = self._peer_region_transfers(population)
        return frozenset(
            region.segment
            for region in population.regions
            if region.independent_body_supported
            and self.plausible(population, region)
            and self._conflicts_with_peers(region, transfers)
        )

    def _peer_region_transfers(
        self, population: TargetedPopulationContext
    ) -> tuple[TargetedPeerRegionTransfer, ...]:
        if population.reference_istd_rt_min is not None:
            return ()  # The paired-position owner governs this stronger prior.
        supported = self.retention_intervals(population)
        anchors = tuple(
            (p, region)
            for p in self._populations_by_sample[population.sample_name]
            if p.target_label in self._seeded_targets and not _same_ion(p, population)
            if (region := p.calibration_region) is not None
            and any(
                _overlaps(_offset_bounds(p, region), interval) for interval in supported
            )
        )
        transfers = []
        for peer in self._populations_by_target[population.target_label]:
            if peer.sample_name == population.sample_name:
                continue
            chosen = self._local_choices[(peer.sample_name, peer.target_label)]
            peer_supported = self.retention_intervals(peer)
            # An uncertain remote assignment must not teach other samples its
            # identity. Require direct corroboration, not the broad fallback
            # uncertainty hull used to preserve provisional measurements.
            if (
                chosen is None
                or not self._identity_specific(peer, chosen)
                or not any(
                    _overlaps(_offset_bounds(peer, chosen), interval)
                    for interval in peer_supported
                )
            ):
                continue
            displacements = []
            for anchor, region in anchors:
                other = self.population_by_cell.get(
                    (peer.sample_name, anchor.target_label)
                )
                other_region = other.calibration_region if other is not None else None
                if other is None or other_region is None:
                    continue
                if not any(
                    _overlaps(_offset_bounds(other, other_region), interval)
                    for interval in peer_supported
                ):
                    continue
                # Preserve signed sample-pair modes and observed boundary
                # uncertainty. Between-sample drift is already accounted for
                # here; do not add that same spread as a symmetric RT allowance.
                displacements.append(
                    (
                        region.segment.left_rt_min - other_region.segment.right_rt_min,
                        region.segment.right_rt_min - other_region.segment.left_rt_min,
                    )
                )
            if displacements:
                transfers.append(
                    TargetedPeerRegionTransfer(peer, chosen, tuple(displacements))
                )
        return tuple(transfers)

    def cross_sample_region_conflict(
        self, population: TargetedPopulationContext, region: TargetedRegionContext
    ) -> bool:
        return (
            region.segment
            in self._conflicting_segments_by_cell[
                (population.sample_name, population.target_label)
            ]
        )

    @staticmethod
    def _conflicts_with_peers(
        region: TargetedRegionContext,
        transfers: tuple[TargetedPeerRegionTransfer, ...],
    ) -> bool:
        if not transfers:
            return False
        for transfer in transfers:
            if transfer.matches(region, transfer.assigned_region):
                return False
            # A single acquired peer detection cannot mint quantity, but it
            # still locates an alternate ion band. Do not discard it before
            # asking whether the current candidate belongs to that other band.
            if not any(
                other != transfer.assigned_region
                and other.acquired_positive_count > 0
                and other.acquired_modulation_observed
                and transfer.matches(region, other)
                for other in transfer.peer.regions
            ):
                return False
        return True

    def select(
        self, population: TargetedPopulationContext, *, is_istd: bool = False
    ) -> TargetedRegionContext | None:
        plausible = tuple(
            r
            for r in population.regions
            if r.independent_body_supported
            and self.plausible(population, r)
            and (is_istd or not self.cross_sample_region_conflict(population, r))
        )
        if not plausible:
            return None
        population = replace(population, regions=plausible)
        if is_istd:
            # Known standards retain their reference identity preference. Poor
            # chromatography affects review, not whether a contour was measured.
            positive = tuple(r for r in plausible if r.chemical_support)
            return min(
                positive or plausible,
                key=lambda r: (
                    abs(r.segment.apex_rt_min - population.reference_rt_min),
                    -r.body_support,
                    r.segment.left_rt_min,
                    r.segment.right_rt_min,
                ),
            )
        candidates = self._reference_competitors(population)
        peers = tuple(
            (p, r)
            for p in self._populations_by_target[population.target_label]
            if p.sample_name != population.sample_name
            if (r := self._local_choices[(p.sample_name, p.target_label)]) is not None
            and self._identity_specific(p, r)
        )
        reference = self.reference_rt(population)
        if not peers:
            direct = tuple(r for r in candidates if r.contains_acquired_rt(reference))
            if direct:
                # Without a homologous peer, an actually observed body at the
                # supplied RT is stronger identity evidence than an unrelated
                # large contour. Missing points at the top do not break a body
                # bracketed by acquired observations.
                candidates = direct
        if peers:
            reference += median(
                r.segment.apex_rt_min - self.reference_rt(p) for p, r in peers
            )
        return min(
            candidates,
            key=lambda r: (
                self._match_cost(r, reference, tuple(peer for _, peer in peers)),
                -r.body_support,
                r.segment.apex_rt_min,
                r.segment.left_rt_min,
                r.segment.right_rt_min,
            ),
        )

    @cached_property
    def _local_choices(self) -> dict[tuple[str, str], TargetedRegionContext | None]:
        return {
            (p.sample_name, p.target_label): self._local_choice(p)
            for p in self.populations
        }

    def _local_choice(
        self, population: TargetedPopulationContext
    ) -> TargetedRegionContext | None:
        population = replace(
            population,
            regions=tuple(
                r for r in population.regions if self.plausible(population, r)
            ),
        )
        candidates = self._reference_competitors(population)
        reference = self.reference_rt(population)
        direct = tuple(r for r in candidates if r.contains_acquired_rt(reference))
        return min(
            direct or candidates,
            key=lambda r: (
                abs(r.segment.apex_rt_min - reference),
                -r.body_support,
                r.segment.apex_rt_min,
            ),
            default=None,
        )

    @cached_property
    def _reference_rts_by_cell(self) -> dict[tuple[str, str], float]:
        references = {}
        for population in self.populations:
            # The supplied paired reference is already sample-local.
            intervals = (
                self.retention_intervals(population)
                if population.reference_istd_rt_min is None
                else ()
            )
            references[(population.sample_name, population.target_label)] = (
                population.reference_rt_min
                + median((left + right) / 2 for left, right in intervals)
                if intervals
                else population.sample_reference_rt_min
                if population.sample_reference_rt_min is not None
                else population.reference_rt_min
            )
        return references

    def reference_rt(self, population: TargetedPopulationContext) -> float:
        """A sample-local ranking prior, not a hard admissibility window."""
        return self._reference_rts_by_cell[
            (population.sample_name, population.target_label)
        ]

    def reference_rts(self, population: TargetedPopulationContext) -> tuple[float, ...]:
        """Same-ion references in the sample's supported retention frame."""
        return tuple(
            sorted(
                {
                    self.reference_rt(p)
                    for p in self._populations_by_sample[population.sample_name]
                    if _same_ion(p, population)
                }
            )
        )

    def _reference_competitors(
        self, population: TargetedPopulationContext
    ) -> tuple[TargetedRegionContext, ...]:
        references = self.reference_rts(population)
        regions = tuple(r for r in population.regions if r.independent_body_supported)
        # Set evidence keeps each possible lobe in the identity comparison;
        # it does not give any lobe individual support or force their sum.
        positive = tuple(
            r for r in regions if r.chemical_support or r.chemical_set_membership
        )
        regions = positive or regions
        reference = self.reference_rt(population)
        if len(references) > 1:
            # A large lobe better explained by another declared RT must not set
            # the abundance floor for this target. This is an interpretation
            # preference after method/context assessment, not a deletion of the
            # original candidates. Missing own regions fall back to a shared,
            # explicitly uncertain interpretation rather than a forced ND.
            own = tuple(
                r
                for r in regions
                if min(
                    references, key=lambda ref: (abs(ref - r.segment.apex_rt_min), ref)
                )
                == reference
            )
            if own:
                regions = own
            # A total spanning several references cannot erase an available
            # valley-bounded interpretation specific to this target. If no
            # such interpretation exists, retain the shared total with review.
            specific = tuple(
                r
                for r in regions
                if r.contains_acquired_rt(reference)
                and sum(
                    r.segment.left_rt_min <= ref <= r.segment.right_rt_min
                    for ref in references
                )
                == 1
            )
            if specific:
                regions = specific
        eligible = replace(population, regions=regions)
        prominent = eligible.competitors
        # A weaker body at the reference remains an identity alternative when
        # another sample consumes this population; abundance is not a veto.
        return tuple(
            r
            for r in eligible.comparison_regions
            if r in prominent or r.contains_acquired_rt(reference)
        )

    def competing_regions(
        self, population: TargetedPopulationContext
    ) -> tuple[TargetedRegionContext, ...]:
        return self._reference_competitors(
            replace(
                population,
                regions=tuple(
                    r for r in population.regions if self.plausible(population, r)
                ),
            )
        )

    def _identity_specific(
        self, population: TargetedPopulationContext, region: TargetedRegionContext
    ) -> bool:
        return (
            sum(
                region.segment.left_rt_min <= ref <= region.segment.right_rt_min
                for ref in self.reference_rts(population)
            )
            < 2
        )

    @staticmethod
    def _match_cost(
        region: TargetedRegionContext,
        reference: float,
        peers: tuple[TargetedRegionContext, ...],
    ) -> float:
        if not peers:
            return abs(region.segment.apex_rt_min - reference)
        width = median(p.width_min for p in peers)
        if width <= 0 or region.width_min <= 0:
            return abs(region.segment.apex_rt_min - reference)
        # Keep RT displacement, width and the complete normalized contour in
        # the comparison. None is an admissibility or ideal-shape gate.
        rt_difference = abs(region.segment.apex_rt_min - reference) / width
        width_difference = abs(float(np.log(region.width_min / width)))
        contour_difference = median(
            float(np.sqrt(np.mean((np.asarray(region.contour) - p.contour) ** 2)))
            for p in peers
        )
        return rt_difference + width_difference + contour_difference
