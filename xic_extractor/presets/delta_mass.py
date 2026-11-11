"""Compose a Delta Mass discovery strategy from a named preset."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from xic_extractor.configuration.mass_recalibration import resolve_mass_recalibration
from xic_extractor.presets.apply import (
    _positive_float_setting,
    apply_to_delta_mass_discovery,
)
from xic_extractor.presets.loader import load_preset
from xic_extractor.presets.models import Preset, PresetError

if TYPE_CHECKING:
    from xic_extractor.discovery.delta_mass_strategy import DeltaMassDiscoveryStrategy


def resolve_delta_mass_discovery(
    preset: str | Preset, *, label: str | None = None
) -> DeltaMassDiscoveryStrategy:
    """Resolve a preset into the method and detector settings for one run.

    ``preset`` is a built-in name, a TOML path, or an already admitted preset
    (for example a Workbench method draft); ``label`` names it in the sidecar.
    """
    from xic_extractor.discovery.delta_mass_strategy import DeltaMassDiscoveryStrategy

    if isinstance(preset, str):
        label = label or preset
        preset = load_preset(preset)
    preset_name = label or preset.name
    settings = apply_to_delta_mass_discovery(preset)
    detector = preset.discovery_overrides.get("detector", {})
    if not isinstance(detector, Mapping):
        raise PresetError("discovery.detector must be a table")
    unknown = set(detector) - {"smoothing_window_sec"}
    if unknown:
        raise PresetError(f"unknown delta-mass detector setting: {sorted(unknown)[0]}")
    smoothing = _positive_float_setting(detector, "smoothing_window_sec", 6.6)
    return DeltaMassDiscoveryStrategy(
        label=preset_name,
        settings=settings,
        cid_nl_composite=any(tag.strategy == "neutral_loss" for tag in preset.tags),
        smoothing_window_sec=smoothing,
        mass_recalibration=resolve_mass_recalibration(
            preset.discovery_overrides.get("mass_recalibration", "none")
        ),
    )
