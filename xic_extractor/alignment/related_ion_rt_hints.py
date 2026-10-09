"""Same-sample related-ion search hints, never identity or quantitative evidence."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import numpy as np

from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.feature_membership_inference import BackfillRtPrediction

# Singly charged M+1-compatible spacing only proposes a relationship. Repeated
# coelution and stable relative response are also required; none identifies it.
_MASS_SPACING_DA = 1.00335483507
_MIN_PAIRED_SAMPLES = 20
_COELUTION_MIN = 3.0 / 60.0
_MIN_COELUTION_FRACTION = 0.95
_MAX_RELATIVE_RATIO_SCATTER = 0.25


@dataclass(frozen=True, slots=True)
class _ObservedIonGroup:
    group_id: str
    mz: float
    # Compact projection deliberately retains no trace, integration or authority.
    observations: Mapping[str, tuple[float, float]]


class RelatedIonRtHintAccumulator:
    """Learn once from frozen groups across partitions; return missing-cell hints.

    Query positions come only from an actually observed partner in the same
    sample. The absent target contributes neither RT nor intensity at query time.
    No RAW access, membership mutation, annotation, or partner-area projection.
    """

    def __init__(
        self,
        *,
        sample_cohorts: Mapping[str, str],
        mass_tolerance_ppm: float,
    ) -> None:
        self._cohorts = dict(sample_cohorts)
        self._mass_tolerance = mass_tolerance_ppm / 1e6
        self._groups: dict[str, _ObservedIonGroup] = {}

    def add_groups(self, groups: Iterable[CrossSamplePeakGroupHypothesis]) -> None:
        for group in groups:
            if (
                group.disposition != "established"
                or len(group.owners) < _MIN_PAIRED_SAMPLES
            ):
                continue
            self._groups[group.group_hypothesis_id] = _ObservedIonGroup(
                group.group_hypothesis_id,
                group.family_center_mz,
                {o.sample_stem: (o.owner_apex_rt, o.owner_area) for o in group.owners},
            )

    def freeze(self) -> dict[tuple[str, str], BackfillRtPrediction]:
        groups = sorted(self._groups.values(), key=lambda g: (g.mz, g.group_id))
        masses = [g.mz for g in groups]
        proposals: dict[tuple[str, str], list[tuple[float, str]]] = defaultdict(list)
        for i, low in enumerate(groups):
            # |high-low-spacing| / high <= tolerance, without an all-pairs scan.
            lower = (low.mz + _MASS_SPACING_DA) / (1.0 + self._mass_tolerance)
            upper = (low.mz + _MASS_SPACING_DA) / (1.0 - self._mass_tolerance)
            start = max(i + 1, bisect_left(masses, lower))
            stop = bisect_right(masses, upper)
            for high in groups[start:stop]:
                for target, partner in ((low, high), (high, low)):
                    self._propose(target, partner, proposals)
        hints = {}
        for key, values in sorted(proposals.items()):
            positions = [rt for rt, _ in values]
            if max(positions) - min(positions) > _COELUTION_MIN:
                continue
            hints[key] = BackfillRtPrediction(
                query_rt=max(0.0, float(np.median(positions))),
                basis="related_ion_coelution:"
                + ";".join(sorted(partner for _, partner in values)),
            )
        return hints

    def _propose(
        self,
        target: _ObservedIonGroup,
        partner: _ObservedIonGroup,
        proposals: dict[tuple[str, str], list[tuple[float, str]]],
    ) -> None:
        shared_by_cohort: dict[str, list[str]] = defaultdict(list)
        for sample in sorted(target.observations.keys() & partner.observations.keys()):
            cohort = self._cohorts.get(sample)
            if (
                cohort is not None
                and target.observations[sample][1] > 0.0
                and partner.observations[sample][1] > 0.0
            ):
                shared_by_cohort[cohort].append(sample)
        for cohort, samples in shared_by_cohort.items():
            if len(samples) < _MIN_PAIRED_SAMPLES:
                continue
            offsets = np.array(
                [
                    target.observations[s][0] - partner.observations[s][0]
                    for s in samples
                ]
            )
            if (
                float(np.mean(np.abs(offsets) <= _COELUTION_MIN))
                < _MIN_COELUTION_FRACTION
            ):
                continue
            ratios = np.array(
                [
                    target.observations[s][1] / partner.observations[s][1]
                    for s in samples
                ]
            )
            center = float(np.median(ratios))
            scatter = float(1.4826 * np.median(np.abs(ratios - center)) / center)
            if scatter > _MAX_RELATIVE_RATIO_SCATTER:
                continue
            offset = float(np.median(offsets))
            for sample in sorted(
                partner.observations.keys() - target.observations.keys()
            ):
                rt, area = partner.observations[sample]
                if self._cohorts.get(sample) == cohort and area > 0.0:
                    proposals[target.group_id, sample].append(
                        (rt + offset, partner.group_id)
                    )
