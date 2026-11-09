import math
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
    ms1_support_strong_area_min: float = 10_000_000.0
    ms1_support_moderate_area_min: float = 1_000_000.0
    scan_support_target: int = 10
    scan_support_high_score_min: float = 0.8
    scan_support_low_score_max: float = 0.2

    def __post_init__(self) -> None:
        float_fields = (
            "rt_aligned_max_min",
            "rt_near_max_min",
            "rt_shifted_max_min",
            "product_intensity_high_min",
            "product_intensity_med_min",
            "area_high_min",
            "area_med_min",
            "ms1_support_strong_area_min",
            "ms1_support_moderate_area_min",
            "scan_support_high_score_min",
            "scan_support_low_score_max",
        )
        for field in float_fields:
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{field} must be numeric")
            if not math.isfinite(float(value)):
                raise ValueError(f"{field} must be finite")
            if float(value) < 0.0:
                raise ValueError(f"{field} must be non-negative")

        if self.rt_aligned_max_min > self.rt_near_max_min:
            raise ValueError("Discovery RT thresholds must be ordered")
        if self.rt_near_max_min > self.rt_shifted_max_min:
            raise ValueError("Discovery RT thresholds must be ordered")
        if self.product_intensity_high_min < self.product_intensity_med_min:
            raise ValueError("Discovery product-intensity thresholds must be ordered")
        if self.area_high_min < self.area_med_min:
            raise ValueError("Discovery area thresholds must be ordered")
        if self.ms1_support_strong_area_min < self.ms1_support_moderate_area_min:
            raise ValueError("Discovery MS1-support thresholds must be ordered")
        if (
            self.scan_support_high_score_min > 1.0
            or self.scan_support_low_score_max > 1.0
        ):
            raise ValueError("Discovery scan-support score thresholds must be <= 1")
        if self.scan_support_low_score_max > self.scan_support_high_score_min:
            raise ValueError("Discovery scan-support score thresholds must be ordered")
        if isinstance(self.scan_support_target, bool) or not isinstance(
            self.scan_support_target, int
        ):
            raise TypeError("scan_support_target must be a non-bool integer")
        if self.scan_support_target <= 0:
            raise ValueError("scan_support_target must be positive")


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
