"""Same-scan Pt isotope-envelope evidence and hypothesis conflicts."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np

from xic_extractor.discovery.delta_mass_patterns import PT_PATTERN
from xic_extractor.mass_accuracy import mz_window, signed_ppm_error
from xic_extractor.ms1_scan_index import MS1Scan, validate_ms1_scan_index
from xic_extractor.validation_primitives import require_finite_real, require_integer

Plus4SupportStatus = Literal[
    "not_evaluable",
    "missing",
    "censored_below_noise",
    "mixed_missing_and_below_noise",
    "partial_above_noise",
    "full_above_noise",
]
ConflictResolutionStatus = Literal[
    "unique_strict_support",
    "unique_core_support",
    "ambiguous_multiple_strict_support",
    "ambiguous_multiple_core_support",
    "no_same_scan_support",
]

_CORE_LABELS = ("base", "pt_plus_1", "pt_plus_2")
_ALL_LABELS = (*_CORE_LABELS, "pt_plus_4")
_DELTA_BY_LABEL = {
    position.label: position.delta_da for position in PT_PATTERN.positions
}


@dataclass(frozen=True, slots=True)
class PtPatternSeed:
    seed_id: str
    source_set: str
    base_mz: float
    rt_center_min: float | None = None
    rt_half_window_sec: float | None = None


@dataclass(frozen=True, slots=True)
class SameScanSettings:
    mass_tolerance_ppm: float = 20.0
    noise_level: float = 25_000.0
    minimum_joint_scans: int = 2

    def __post_init__(self) -> None:
        mass_tolerance = require_finite_real(
            self.mass_tolerance_ppm,
            name="same-scan mass_tolerance_ppm",
        )
        if mass_tolerance <= 0:
            raise ValueError("same-scan mass_tolerance_ppm must be > 0")
        noise_level = require_finite_real(
            self.noise_level,
            name="same-scan noise_level",
        )
        if noise_level < 0:
            raise ValueError("same-scan noise_level must be >= 0")
        minimum_joint_scans = require_integer(
            self.minimum_joint_scans,
            name="minimum_joint_scans",
        )
        if minimum_joint_scans < 1:
            raise ValueError("minimum_joint_scans must be at least one")


_DEFAULT_SAME_SCAN_SETTINGS = SameScanSettings()


@dataclass(frozen=True, slots=True)
class ObservedPeak:
    mz: float
    intensity: float
    signed_ppm_error: float


@dataclass(frozen=True, slots=True)
class SameScanObservation:
    scan_number: int
    rt_min: float
    peaks: Mapping[str, ObservedPeak]
    core_centroid_present: bool
    full_centroid_present: bool
    core_above_noise: bool
    full_above_noise: bool


@dataclass(frozen=True, slots=True)
class SameScanEnvelopeEvidence:
    seed: PtPatternSeed
    selected_scan_count: int
    core_centroid_scan_count: int
    full_centroid_scan_count: int
    core_above_noise_scan_count: int
    full_above_noise_scan_count: int
    longest_consecutive_core_scans: int
    longest_consecutive_full_scans: int
    strict_full_supported: bool
    core_plus4_conditional_supported: bool
    plus4_support_status: Plus4SupportStatus
    shared_apex_scan_number: int | None
    shared_apex_rt_min: float | None
    shared_apex_base_over_plus1: float | None
    shared_apex_plus2_over_plus1: float | None
    shared_apex_plus4_over_plus1: float | None
    core_sum_base_over_plus1: float | None
    core_sum_plus2_over_plus1: float | None
    core_sum_plus4_over_plus1: float | None
    core_min_pairwise_pearson: float | None
    median_core_ppm_spread: float | None
    median_full_ppm_spread: float | None
    observations: tuple[SameScanObservation, ...]


@dataclass(frozen=True, slots=True)
class CandidateFeatureClaims:
    candidate_id: str
    feature_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ConflictComponent:
    component_id: str
    candidate_ids: tuple[str, ...]
    shared_feature_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResolvedConflictComponent:
    component: ConflictComponent
    resolution_status: ConflictResolutionStatus
    preferred_candidate_id: str | None

    @property
    def component_id(self) -> str:
        return self.component.component_id

    @property
    def candidate_ids(self) -> tuple[str, ...]:
        return self.component.candidate_ids


def evaluate_same_scan_envelope(
    index: Sequence[MS1Scan],
    seed: PtPatternSeed,
    settings: SameScanSettings = _DEFAULT_SAME_SCAN_SETTINGS,
) -> SameScanEnvelopeEvidence:
    """Measure fixed-mass Pt positions on shared MS1 scan IDs."""

    _validate_seed(seed)
    validate_ms1_scan_index(index, require_nonempty=True)
    observations = tuple(
        _observe_scan(scan, seed.base_mz, settings)
        for scan in index
        if _scan_is_selected(scan, seed)
    )
    core = tuple(item for item in observations if item.core_above_noise)
    full = tuple(item for item in observations if item.full_above_noise)
    shared_apex = max(
        core,
        key=lambda item: item.peaks["pt_plus_1"].intensity,
        default=None,
    )
    plus4_status = _plus4_status(core, settings.noise_level)
    longest_core = _longest_true_run(observations, "core_above_noise")
    longest_full = _longest_true_run(observations, "full_above_noise")
    return SameScanEnvelopeEvidence(
        seed=seed,
        selected_scan_count=len(observations),
        core_centroid_scan_count=sum(
            item.core_centroid_present for item in observations
        ),
        full_centroid_scan_count=sum(
            item.full_centroid_present for item in observations
        ),
        core_above_noise_scan_count=len(core),
        full_above_noise_scan_count=len(full),
        longest_consecutive_core_scans=longest_core,
        longest_consecutive_full_scans=longest_full,
        strict_full_supported=longest_full >= settings.minimum_joint_scans,
        core_plus4_conditional_supported=longest_core >= settings.minimum_joint_scans,
        plus4_support_status=plus4_status,
        shared_apex_scan_number=shared_apex.scan_number if shared_apex else None,
        shared_apex_rt_min=shared_apex.rt_min if shared_apex else None,
        shared_apex_base_over_plus1=_apex_ratio(shared_apex, "base"),
        shared_apex_plus2_over_plus1=_apex_ratio(shared_apex, "pt_plus_2"),
        shared_apex_plus4_over_plus1=_apex_ratio(shared_apex, "pt_plus_4"),
        core_sum_base_over_plus1=_sum_ratio(core, "base"),
        core_sum_plus2_over_plus1=_sum_ratio(core, "pt_plus_2"),
        core_sum_plus4_over_plus1=_sum_ratio(core, "pt_plus_4"),
        core_min_pairwise_pearson=_minimum_core_pearson(core),
        median_core_ppm_spread=_median_ppm_spread(core, _CORE_LABELS),
        median_full_ppm_spread=_median_ppm_spread(full, _ALL_LABELS),
        observations=observations,
    )


def build_conflict_components(
    claims: Sequence[CandidateFeatureClaims],
) -> tuple[ConflictComponent, ...]:
    """Group candidate hypotheses connected by shared feature claims."""

    claims_by_id: dict[str, CandidateFeatureClaims] = {}
    for claim in claims:
        candidate_id = _required_claim_identity(
            claim.candidate_id,
            field="candidate_id",
        )
        if candidate_id in claims_by_id:
            raise ValueError(f"duplicate candidate_id: {candidate_id}")
        claims_by_id[candidate_id] = CandidateFeatureClaims(
            candidate_id=candidate_id,
            feature_ids=claim.feature_ids,
        )

    feature_to_candidates: dict[str, set[str]] = {}
    for claim in claims_by_id.values():
        for feature_id in set(claim.feature_ids):
            feature_to_candidates.setdefault(feature_id, set()).add(claim.candidate_id)
    graph: dict[str, set[str]] = {candidate_id: set() for candidate_id in claims_by_id}
    for owner_ids in feature_to_candidates.values():
        if len(owner_ids) < 2:
            continue
        for candidate_id in owner_ids:
            graph[candidate_id].update(owner_ids - {candidate_id})

    raw_components: list[tuple[str, ...]] = []
    unseen = set(graph)
    while unseen:
        start = min(unseen)
        stack = [start]
        connected: set[str] = set()
        while stack:
            candidate_id = stack.pop()
            if candidate_id in connected:
                continue
            connected.add(candidate_id)
            stack.extend(sorted(graph[candidate_id] - connected, reverse=True))
        unseen -= connected
        if len(connected) > 1:
            raw_components.append(tuple(sorted(connected)))

    components: list[ConflictComponent] = []
    for component_index, component_candidate_ids in enumerate(
        sorted(raw_components), start=1
    ):
        shared = tuple(
            sorted(
                feature_id
                for feature_id, owners in feature_to_candidates.items()
                if len(set(component_candidate_ids) & owners) > 1
            )
        )
        components.append(
            ConflictComponent(
                component_id=f"conflict_{component_index:04d}",
                candidate_ids=component_candidate_ids,
                shared_feature_ids=shared,
            )
        )
    return tuple(components)


def _required_claim_identity(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty")
    return value.strip()


def resolve_conflict_components(
    components: Sequence[ConflictComponent],
    *,
    strict_support_by_candidate: Mapping[str, bool],
    core_support_by_candidate: Mapping[str, bool],
) -> tuple[ResolvedConflictComponent, ...]:
    """Classify conflicts, nominating a candidate only from full strict support."""

    resolved = []
    for component in components:
        strict = tuple(
            candidate_id
            for candidate_id in component.candidate_ids
            if strict_support_by_candidate.get(candidate_id, False)
        )
        core = tuple(
            candidate_id
            for candidate_id in component.candidate_ids
            if core_support_by_candidate.get(candidate_id, False)
        )
        if len(strict) == 1:
            status: ConflictResolutionStatus = "unique_strict_support"
            preferred = strict[0]
        elif len(strict) > 1:
            status = "ambiguous_multiple_strict_support"
            preferred = None
        elif len(core) == 1:
            status = "unique_core_support"
            # Core-only support is useful diagnostic evidence, but Discovery
            # requires the full base/+1/+2/+4 pattern before it may nominate a
            # preferred hypothesis.
            preferred = None
        elif len(core) > 1:
            status = "ambiguous_multiple_core_support"
            preferred = None
        else:
            status = "no_same_scan_support"
            preferred = None
        resolved.append(
            ResolvedConflictComponent(
                component=component,
                resolution_status=status,
                preferred_candidate_id=preferred,
            )
        )
    return tuple(resolved)


def _observe_scan(
    scan: MS1Scan,
    base_mz: float,
    settings: SameScanSettings,
) -> SameScanObservation:
    peaks = {
        label: peak
        for label in _ALL_LABELS
        if (
            peak := _best_peak(
                scan,
                expected_mz=base_mz + _DELTA_BY_LABEL[label],
                tolerance_ppm=settings.mass_tolerance_ppm,
            )
        )
        is not None
    }
    core_centroid = all(label in peaks for label in _CORE_LABELS)
    full_centroid = all(label in peaks for label in _ALL_LABELS)
    core_above = core_centroid and all(
        peaks[label].intensity >= settings.noise_level for label in _CORE_LABELS
    )
    full_above = full_centroid and all(
        peaks[label].intensity >= settings.noise_level for label in _ALL_LABELS
    )
    return SameScanObservation(
        scan_number=scan.scan_number,
        rt_min=scan.rt,
        peaks=peaks,
        core_centroid_present=core_centroid,
        full_centroid_present=full_centroid,
        core_above_noise=core_above,
        full_above_noise=full_above,
    )


def _best_peak(
    scan: MS1Scan,
    *,
    expected_mz: float,
    tolerance_ppm: float,
) -> ObservedPeak | None:
    lower, upper = mz_window(expected_mz, tolerance_ppm)
    left = int(np.searchsorted(scan.masses, lower, side="left"))
    right = int(np.searchsorted(scan.masses, upper, side="right"))
    if left == right:
        return None
    masses = scan.masses[left:right]
    intensities = scan.intensities[left:right]
    valid = np.flatnonzero(
        np.isfinite(masses) & np.isfinite(intensities) & (intensities > 0)
    )
    if valid.size == 0:
        return None
    offset = int(valid[np.argmax(intensities[valid])])
    mz = float(masses[offset])
    return ObservedPeak(
        mz=mz,
        intensity=float(intensities[offset]),
        signed_ppm_error=signed_ppm_error(mz, expected_mz),
    )


def _scan_is_selected(scan: MS1Scan, seed: PtPatternSeed) -> bool:
    if seed.rt_center_min is None:
        return True
    half_window_min = (seed.rt_half_window_sec or 0.0) / 60.0
    return abs(scan.rt - seed.rt_center_min) <= half_window_min + 1e-12


def _longest_true_run(
    observations: Sequence[SameScanObservation],
    attribute: Literal["core_above_noise", "full_above_noise"],
) -> int:
    longest = 0
    current = 0
    for observation in observations:
        current = current + 1 if getattr(observation, attribute) else 0
        longest = max(longest, current)
    return longest


def _plus4_status(
    core: Sequence[SameScanObservation],
    noise_level: float,
) -> Plus4SupportStatus:
    if not core:
        return "not_evaluable"
    present = [item.peaks.get("pt_plus_4") for item in core]
    observed = [peak for peak in present if peak is not None]
    if not observed:
        return "missing"
    above_noise = sum(peak.intensity >= noise_level for peak in observed)
    if above_noise == 0:
        if len(observed) < len(core):
            return "mixed_missing_and_below_noise"
        return "censored_below_noise"
    if above_noise == len(core):
        return "full_above_noise"
    return "partial_above_noise"


def _apex_ratio(
    observation: SameScanObservation | None,
    numerator_label: str,
) -> float | None:
    if observation is None:
        return None
    numerator = observation.peaks.get(numerator_label)
    denominator = observation.peaks.get("pt_plus_1")
    if numerator is None or denominator is None or denominator.intensity <= 0:
        return None
    return numerator.intensity / denominator.intensity


def _sum_ratio(
    observations: Sequence[SameScanObservation],
    numerator_label: str,
) -> float | None:
    denominator = sum(item.peaks["pt_plus_1"].intensity for item in observations)
    if denominator <= 0:
        return None
    numerator = sum(
        item.peaks[numerator_label].intensity
        for item in observations
        if numerator_label in item.peaks
    )
    return numerator / denominator


def _minimum_core_pearson(
    observations: Sequence[SameScanObservation],
) -> float | None:
    if len(observations) < 3:
        return None
    vectors = {
        label: np.asarray(
            [item.peaks[label].intensity for item in observations],
            dtype=np.float64,
        )
        for label in _CORE_LABELS
    }
    pairs = (
        ("base", "pt_plus_1"),
        ("base", "pt_plus_2"),
        ("pt_plus_1", "pt_plus_2"),
    )
    correlations = []
    for left, right in pairs:
        if np.std(vectors[left]) == 0 or np.std(vectors[right]) == 0:
            continue
        value = float(np.corrcoef(vectors[left], vectors[right])[0, 1])
        if math.isfinite(value):
            correlations.append(value)
    return min(correlations) if correlations else None


def _median_ppm_spread(
    observations: Sequence[SameScanObservation],
    labels: Sequence[str],
) -> float | None:
    spreads = [
        float(np.std([item.peaks[label].signed_ppm_error for label in labels]))
        for item in observations
        if all(label in item.peaks for label in labels)
    ]
    return float(np.median(spreads)) if spreads else None


def _validate_seed(seed: PtPatternSeed) -> None:
    if not seed.seed_id.strip() or not seed.source_set.strip():
        raise ValueError("Pt pattern seed identifiers must be non-empty")
    base_mz = require_finite_real(seed.base_mz, name="Pt pattern base m/z")
    if base_mz <= 0:
        raise ValueError("Pt pattern base m/z must be finite and positive")
    if seed.rt_center_min is not None and seed.rt_half_window_sec is None:
        raise ValueError("RT-bounded Pt pattern seed requires a half-window")
    if seed.rt_center_min is not None:
        rt_center = require_finite_real(
            seed.rt_center_min,
            name="Pt pattern RT center",
        )
        if rt_center < 0:
            raise ValueError("Pt pattern RT center must be finite and non-negative")
    if seed.rt_half_window_sec is not None:
        rt_window = require_finite_real(
            seed.rt_half_window_sec,
            name="Pt pattern RT half-window",
        )
        if rt_window <= 0:
            raise ValueError("Pt pattern RT half-window must be finite and positive")
