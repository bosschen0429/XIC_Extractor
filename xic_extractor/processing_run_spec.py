"""CID-NL Discovery run inputs and the settings check at their entry points."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xic_extractor.discovery.settings import DiscoverySettings

from xic_extractor.configuration.mass_recalibration import (
    MassRecalibration,
)
from xic_extractor.configuration.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
    SignalProcessingProfile,
)
from xic_extractor.processing_identity import (
    AcquisitionSourceIdentity,
)
from xic_extractor.validation_primitives import (
    require_integer,
    require_text,
)

_WEIGHT_KEYS = (
    "ms1_peak_present",
    "seed_event_per",
    "seed_event_max",
    "rt_aligned",
    "rt_near",
    "rt_shifted",
    "product_intensity_high",
    "product_intensity_med",
    "area_high",
    "area_med",
    "scan_support_high",
    "scan_support_low",
)
_THRESHOLD_FLOAT_KEYS = (
    "rt_aligned_max_min",
    "rt_near_max_min",
    "rt_shifted_max_min",
    "product_intensity_high_min",
    "product_intensity_med_min",
    "area_high_min",
    "area_med_min",
    "scan_support_high_score_min",
    "scan_support_low_score_max",
)


@dataclass(frozen=True, slots=True)
class ProcessingAcquisitionInput:
    """One RAW file, its fingerprinted content."""

    raw_path: Path
    acquisition_source: AcquisitionSourceIdentity


@dataclass(frozen=True, slots=True)
class ProcessingRunSpec:
    """One Discovery run, compiled before any RAW handle is opened."""

    acquisition_inputs: tuple[ProcessingAcquisitionInput, ...]
    settings: DiscoverySettings
    signal_processing_profile: SignalProcessingProfile = (
        DEFAULT_SIGNAL_PROCESSING_PROFILE
    )


def validate_cid_nl_discovery_settings(settings: DiscoverySettings) -> None:
    """Reject settings a preset file, CLI or Workbench form can produce."""

    from xic_extractor.configuration.cid_nl_profile import NeutralLossProfile
    from xic_extractor.discovery.evidence_config import (
        DiscoveryEvidenceProfile,
        DiscoveryEvidenceThresholds,
        DiscoveryEvidenceWeights,
    )
    from xic_extractor.discovery.settings import DiscoverySettings
    from xic_extractor.settings_schema import RESOLVER_MODES

    if not isinstance(settings, DiscoverySettings):
        raise TypeError("settings must be DiscoverySettings")
    profiles = settings.neutral_loss_profiles
    if not isinstance(profiles, tuple) or not profiles:
        raise ValueError("neutral_loss_profiles must be a non-empty tuple")
    if any(not isinstance(profile, NeutralLossProfile) for profile in profiles):
        raise TypeError("neutral_loss_profiles must contain NeutralLossProfile")
    tags = tuple(
        require_text(profile.tag, field="neutral loss tag") for profile in profiles
    )
    if len(tags) != len(set(tags)):
        raise ValueError("neutral_loss_profiles contains duplicate tags")
    if settings.selected_tag_names != tags:
        raise ValueError("selected_tag_names must exactly match neutral-loss profiles")
    if settings.tag_combine_mode not in {"single", "union", "intersection"}:
        raise ValueError("unsupported tag_combine_mode")
    if settings.tag_combine_mode == "single" and len(profiles) != 1:
        raise ValueError("single tag_combine_mode requires exactly one profile")
    if settings.tag_combine_mode in {"union", "intersection"} and len(profiles) < 2:
        raise ValueError(
            f"{settings.tag_combine_mode} tag_combine_mode "
            "requires at least two profiles"
        )
    for profile in profiles:
        value = profile.neutral_loss_da
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("neutral_loss_da must be numeric")
        if not math.isfinite(float(value)) or float(value) <= 0.0:
            raise ValueError("neutral_loss_da must be finite and positive")
    canonical_masses = tuple(
        float(profile.neutral_loss_da).hex() for profile in profiles
    )
    if len(canonical_masses) != len(set(canonical_masses)):
        raise ValueError(
            "neutral_loss_profiles contains distinct tags with the same canonical mass"
        )
    positive_fields = (
        "nl_tolerance_ppm",
        "precursor_mz_tolerance_ppm",
        "product_mz_tolerance_ppm",
        "product_search_ppm",
        "ms2_precursor_tol_da",
        "nl_min_intensity_ratio",
        "ms1_search_padding_min",
    )
    for field in positive_fields:
        value = getattr(settings, field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{field} must be numeric")
        if not math.isfinite(float(value)) or float(value) <= 0.0:
            raise ValueError(f"{field} must be finite and positive")
    if not isinstance(settings.mass_recalibration, MassRecalibration):
        raise TypeError("mass_recalibration must be a MassRecalibration policy")
    for field in ("rt_min", "rt_max"):
        value = getattr(settings, field)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{field} must be numeric or None")
        if not math.isfinite(float(value)) or float(value) < 0.0:
            raise ValueError(f"{field} must be finite and non-negative")
    if (
        settings.rt_min is not None
        and settings.rt_max is not None
        and settings.rt_min > settings.rt_max
    ):
        raise ValueError("rt_min must not exceed rt_max")
    require_text(settings.resolver_mode, field="resolver_mode")
    if settings.resolver_mode not in RESOLVER_MODES:
        raise ValueError("unsupported resolver_mode")
    evidence = settings.evidence_profile
    if not isinstance(evidence, DiscoveryEvidenceProfile):
        raise TypeError("evidence_profile must be DiscoveryEvidenceProfile")
    if evidence.name != "default":
        raise ValueError("unsupported Discovery evidence profile")
    if not isinstance(evidence.weights, DiscoveryEvidenceWeights):
        raise TypeError("evidence weights must be DiscoveryEvidenceWeights")
    if not isinstance(evidence.thresholds, DiscoveryEvidenceThresholds):
        raise TypeError("evidence thresholds must be DiscoveryEvidenceThresholds")
    for key in _WEIGHT_KEYS:
        require_integer(
            getattr(evidence.weights, key), field=key, type_error_factory=TypeError
        )
    for key in _THRESHOLD_FLOAT_KEYS:
        value = getattr(evidence.thresholds, key)
        if not math.isfinite(float(value)):
            raise ValueError(f"{key} must be finite")
    if evidence.thresholds.scan_support_target <= 0:
        raise ValueError("scan_support_target must be positive")


__all__ = [
    "ProcessingAcquisitionInput",
    "ProcessingRunSpec",
    "validate_cid_nl_discovery_settings",
]
