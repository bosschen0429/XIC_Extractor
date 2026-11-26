"""CID-NL Discovery run inputs and the settings check at their entry points."""

from __future__ import annotations

import math
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xic_extractor.discovery.settings import DiscoverySettings

from xic_extractor.configuration.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
    SignalProcessingProfile,
)
from xic_extractor.processing_identity import (
    AcquisitionSourceIdentity,
)
from xic_extractor.validation_primitives import require_text


@dataclass(frozen=True, slots=True)
class ProcessingAcquisitionInput:
    """One RAW file, its fingerprinted content and its blank library snapshot."""

    raw_path: Path
    acquisition_source: AcquisitionSourceIdentity
    blank_library_content: str | None = dataclass_field(default=None, repr=False)


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

    from xic_extractor.settings_schema import RESOLVER_MODES

    profiles = settings.neutral_loss_profiles
    if not profiles:
        raise ValueError("neutral_loss_profiles must be non-empty")
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
        "blank_ppm_tol",
        "blank_fold_change_threshold",
    )
    for field in positive_fields:
        value = getattr(settings, field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{field} must be numeric")
        if not math.isfinite(float(value)) or float(value) <= 0.0:
            raise ValueError(f"{field} must be finite and positive")
    if not isinstance(settings.enable_blank_qualification, bool):
        raise TypeError("enable_blank_qualification must be a boolean")
    if settings.blank_library_path is not None:
        if not isinstance(settings.blank_library_path, str):
            raise TypeError("blank_library_path must be text or None")
        if not settings.blank_library_path:
            raise ValueError("blank_library_path must be non-empty text or None")
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


__all__ = [
    "ProcessingAcquisitionInput",
    "ProcessingRunSpec",
    "validate_cid_nl_discovery_settings",
]
