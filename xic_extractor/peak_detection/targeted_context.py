"""Compare retained Targeted regions in their chromatographic batch context.

Measurement, reference plausibility and identity preference are separate. A
ragged top is not a missing measurement; a large unrelated contour is not a
retention-time calibrator merely because it dominates its own XIC.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, replace
from functools import cached_property
from statistics import median

import numpy as np

from xic_extractor.mass_accuracy import within_both_mz_tolerances
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
    component_apex_rts: tuple[float, ...] | None = None,
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
            bisect_right(component_apex_rts, segment.right_rt_min)
            - bisect_left(component_apex_rts, segment.left_rt_min)
            if component_apex_rts is not None
            else 1
        ),
        body_support=segment.height * coverage * longest_fraction,
        width_min=width,
        contour=tuple(float(v) for v in contour),
        measurement_available=measurement_available,
        chemical_support=chemical_support,
        chemical_set_membership=chemical_set_membership,
    )


def observed_component_apex_rts(
    prepared: PreparedSignal, constituents: tuple[PhysicalSegment, ...]
) -> tuple[float, ...]:
    """Sorted apexes of constituents with acquired modulation, assessed once."""
    return tuple(
        sorted(
            child.apex_rt_min
            for child in constituents
            if _acquired_modulation_observed(
                np.asarray(prepared.raw[child.start_index : child.end_index])
            )
        )
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
    raw_stem: str
    target_label: str
    mz: float
    ppm_tol: float
    reference_rt_min: float
    regions: tuple[TargetedRegionContext, ...]
    reference_istd_rt_min: float | None = None
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
        if self.reference_istd_rt_min is not None:
            return None
        regions = self.competitors
        if len(regions) != 1:
            return None
        region = regions[0]
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
    return within_both_mz_tolerances(left.mz, left.ppm_tol, right.mz, right.ppm_tol)


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
class TargetedRegionSelection:
    """One identity choice and the retained scope of its method assessment."""

    selected_region: TargetedRegionContext | None
    admissible_regions: tuple[TargetedRegionContext, ...]
    competing_region_count: int


def _select_istd_reference_region(
    population: TargetedPopulationContext,
    *,
    competing_region_count: int,
) -> TargetedRegionSelection:
    reference = population.reference_rt_min

    def reference_order(region: TargetedRegionContext) -> tuple[float, ...]:
        return (
            abs(region.segment.apex_rt_min - reference),
            -region.body_support,
            region.segment.left_rt_min,
            region.segment.right_rt_min,
        )

    positive = tuple(r for r in population.regions if r.chemical_support)
    candidates = population.regions
    if positive:
        reference_body = min(positive, key=reference_order)
        candidates = tuple(
            r
            for r in candidates
            if r.chemical_support
            or r.body_support
            >= reference_body.body_support * ANCHOR_SELECTION_MIN_INTENSITY_RATIO
        )

    closest_extent = min(
        max(
            abs(r.segment.left_rt_min - reference),
            abs(r.segment.right_rt_min - reference),
        )
        for r in candidates
    )
    local = tuple(
        r
        for r in candidates
        if max(
            r.segment.left_rt_min - reference, reference - r.segment.right_rt_min, 0.0
        )
        <= closest_extent
    )
    positive = tuple(r for r in local if r.chemical_support)
    return TargetedRegionSelection(
        min(positive or local, key=reference_order), local, competing_region_count
    )


@dataclass(frozen=True)
class TargetedBatchContext:
    populations: tuple[TargetedPopulationContext, ...]

    @cached_property
    def population_by_cell(self) -> dict[tuple[str, str], TargetedPopulationContext]:
        return {(p.raw_stem, p.target_label): p for p in self.populations}

    @cached_property
    def _populations_by_sample(self) -> dict[str, list[TargetedPopulationContext]]:
        samples: dict[str, list[TargetedPopulationContext]] = {}
        for population in self.populations:
            samples.setdefault(population.raw_stem, []).append(population)
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
            (p.raw_stem, p.target_label): self._retention_intervals(p)
            for p in self.populations
        }

    def retention_intervals(
        self, population: TargetedPopulationContext
    ) -> tuple[tuple[float, float], ...]:
        return self._retention_by_cell.get(
            (population.raw_stem, population.target_label), ()
        )

    def _retention_intervals(
        self, population: TargetedPopulationContext
    ) -> tuple[tuple[float, float], ...]:
        anchors = tuple(
            p
            for p in self._populations_by_sample[population.raw_stem]
            if p.target_label in self._seeded_targets
            and not _same_ion(p, population)
            and p.calibration_region is not None
        )
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
        if not region.independent_body_supported:
            return False
        if population.reference_istd_rt_min is not None:
            return True
        if region.contains_acquired_rt(population.reference_rt_min):
            return True
        intervals = self.retention_intervals(population)
        if not intervals:
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
        ions = {(p.mz, p.ppm_tol): p for p in self.populations}
        return {
            ion: self._retention_uncertainty(population)
            for ion, population in ions.items()
        }

    def _retention_uncertainty(self, population: TargetedPopulationContext) -> float:
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
            (p.raw_stem, p.target_label): self._conflicting_segments(p)
            for p in self.populations
        }

    def _conflicting_segments(
        self, population: TargetedPopulationContext
    ) -> frozenset[PhysicalSegment]:
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
            return ()
        supported = self.retention_intervals(population)
        anchors = tuple(
            (p, region)
            for p in self._populations_by_sample[population.raw_stem]
            if p.target_label in self._seeded_targets and not _same_ion(p, population)
            if (region := p.calibration_region) is not None
            and any(
                _overlaps(_offset_bounds(p, region), interval) for interval in supported
            )
        )
        transfers = []
        for peer in self._populations_by_target[population.target_label]:
            if peer.raw_stem == population.raw_stem:
                continue
            chosen = self._local_choices[(peer.raw_stem, peer.target_label)]
            peer_supported = self.retention_intervals(peer)
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
                    (peer.raw_stem, anchor.target_label)
                )
                other_region = other.calibration_region if other is not None else None
                if other is None or other_region is None:
                    continue
                if not any(
                    _overlaps(_offset_bounds(other, other_region), interval)
                    for interval in peer_supported
                ):
                    continue
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
                (population.raw_stem, population.target_label)
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
    ) -> TargetedRegionSelection:
        admissible = tuple(
            r for r in population.regions if r.independent_body_supported
        )
        competing_region_count = len(self.competing_regions(population))
        plausible = tuple(
            r
            for r in admissible
            if self.plausible(population, r)
            and (is_istd or not self.cross_sample_region_conflict(population, r))
        )
        if not plausible:
            return TargetedRegionSelection(
                None, () if is_istd else admissible, competing_region_count
            )
        population = replace(population, regions=plausible)
        if is_istd:
            return _select_istd_reference_region(
                population, competing_region_count=competing_region_count
            )
        candidates = self._reference_competitors(population)
        peers = tuple(
            (p, r)
            for p in self._populations_by_target[population.target_label]
            if p.raw_stem != population.raw_stem
            if (r := self._local_choices[(p.raw_stem, p.target_label)]) is not None
            and self._identity_specific(p, r)
        )
        reference = self.reference_rt(population)
        if not peers:
            direct = tuple(r for r in candidates if r.contains_acquired_rt(reference))
            if direct:
                candidates = direct
        if peers:
            reference += median(
                r.segment.apex_rt_min - self.reference_rt(p) for p, r in peers
            )
        return TargetedRegionSelection(
            min(
                candidates,
                key=lambda r: (
                    self._match_cost(r, reference, tuple(peer for _, peer in peers)),
                    -r.body_support,
                    r.segment.apex_rt_min,
                    r.segment.left_rt_min,
                    r.segment.right_rt_min,
                ),
            ),
            admissible,
            competing_region_count,
        )

    @cached_property
    def _local_choices(self) -> dict[tuple[str, str], TargetedRegionContext | None]:
        return {
            (p.raw_stem, p.target_label): self._local_choice(p)
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
            intervals = (
                self.retention_intervals(population)
                if population.reference_istd_rt_min is None
                else ()
            )
            references[(population.raw_stem, population.target_label)] = (
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
            (population.raw_stem, population.target_label)
        ]

    def reference_rts(self, population: TargetedPopulationContext) -> tuple[float, ...]:
        """Same-ion references in the sample's supported retention frame."""
        return tuple(
            sorted(
                {
                    self.reference_rt(p)
                    for p in self._populations_by_sample[population.raw_stem]
                    if _same_ion(p, population)
                }
            )
        )

    def _reference_competitors(
        self, population: TargetedPopulationContext
    ) -> tuple[TargetedRegionContext, ...]:
        references = self.reference_rts(population)
        regions = tuple(r for r in population.regions if r.independent_body_supported)
        positive = tuple(
            r for r in regions if r.chemical_support or r.chemical_set_membership
        )
        regions = positive or regions
        reference = self.reference_rt(population)
        if len(references) > 1:
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
        rt_difference = abs(region.segment.apex_rt_min - reference) / width
        width_difference = abs(float(np.log(region.width_min / width)))
        contour_difference = median(
            float(np.sqrt(np.mean((np.asarray(region.contour) - p.contour) ** 2)))
            for p in peers
        )
        return rt_difference + width_difference + contour_difference
