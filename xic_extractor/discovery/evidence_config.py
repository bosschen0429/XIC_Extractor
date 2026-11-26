from dataclasses import dataclass
from typing import Literal

DiscoveryEvidenceProfileName = Literal["default"]


@dataclass(frozen=True)
class DiscoveryEvidenceWeights:
    ms1_peak_present: int = 25
    seed_event_per: int = 8
    seed_event_max: int = 25
    rt_aligned: int = 15
    rt_near: int = 10
    rt_shifted: int = 5
    product_intensity_high: int = 10
    product_intensity_med: int = 5
    area_high: int = 10
    area_med: int = 5
    scan_support_high: int = 5
    scan_support_low: int = -10


@dataclass(frozen=True)
class DiscoveryEvidenceThresholds:
    rt_aligned_max_min: float = 0.05
    rt_near_max_min: float = 0.20
    rt_shifted_max_min: float = 0.40
    product_intensity_high_min: float = 100_000.0
    product_intensity_med_min: float = 10_000.0
    area_high_min: float = 1_000_000.0
    area_med_min: float = 100_000.0
    scan_support_target: int = 10
    scan_support_high_score_min: float = 0.8
    scan_support_low_score_max: float = 0.2


@dataclass(frozen=True)
class DiscoveryEvidenceProfile:
    name: DiscoveryEvidenceProfileName
    weights: DiscoveryEvidenceWeights
    thresholds: DiscoveryEvidenceThresholds


DEFAULT_EVIDENCE_PROFILE = DiscoveryEvidenceProfile(
    name="default",
    weights=DiscoveryEvidenceWeights(),
    thresholds=DiscoveryEvidenceThresholds(),
)
