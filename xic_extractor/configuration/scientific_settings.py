"""Scientific settings bound into Targeted and Untargeted request identities."""

from __future__ import annotations

from typing import TYPE_CHECKING

from xic_extractor.configuration.mass_recalibration import MassRecalibration

if TYPE_CHECKING:
    from xic_extractor.configuration.models import ExtractionConfig

SCIENTIFIC_SETTING_FIELDS = (
    "ms2_precursor_tol_da",
    "nl_min_intensity_ratio",
    "ms1_morphology_smoothing_window_points",
    "count_no_ms2_as_detected",
)


def scientific_settings_payload(config: ExtractionConfig) -> dict[str, object]:
    """Request-identity settings; the vendor-mass policy is recorded by omission."""
    payload: dict[str, object] = {
        field: getattr(config, field) for field in SCIENTIFIC_SETTING_FIELDS
    }
    if config.mass_recalibration is not MassRecalibration.NONE:
        payload["mass_recalibration"] = config.mass_recalibration.value
    return payload
