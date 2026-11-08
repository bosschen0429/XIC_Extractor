"""Configured delta-mass methods admit settings at the preset boundary."""

import pytest

from xic_extractor.presets.apply import apply_to_delta_mass_discovery
from xic_extractor.presets.models import Preset, PresetTag


@pytest.mark.parametrize("name", ["pt_pattern", "pt_pattern_cid_nl_dr"])
def test_pt_discovery_restores_mass_coordinates_without_relaxing_gates(name):
    from xic_extractor.configuration.mass_recalibration import MassRecalibration
    from xic_extractor.presets.apply import resolve_delta_mass_discovery

    strategy = resolve_delta_mass_discovery(name)
    assert strategy.mass_recalibration is MassRecalibration.EASY_IC_RESTORE
    assert strategy.settings.mz_tolerance_ppm == 20
    assert strategy.settings.min_shape_r == 0.9
    assert strategy.settings.apex_floor_sec == 1
    assert strategy.settings.scan_interval_multiplier == 1.25


@pytest.mark.parametrize(
    "bad_position",
    [
        {"label": 3, "delta_da": 7, "ratio_min": 0.4, "ratio_max": 0.6},
        {"label": "heavy", "delta_da": True, "ratio_min": 0.4, "ratio_max": 0.6},
        {"label": "heavy", "delta_da": 7, "ratio_min": "0.4", "ratio_max": 0.6},
    ],
)
def test_preset_rejects_non_numeric_positions_and_non_text_labels(bad_position):
    preset = Preset(
        name="synthetic",
        description="synthetic",
        tags=(
            PresetTag(strategy="delta_mass", name="synthetic", pattern_id="synthetic"),
        ),
        combine_mode="single",
        discovery_overrides={
            "positions": [
                {"label": "base", "delta_da": 0, "ratio_min": 1, "ratio_max": 1},
                bad_position,
            ],
            "anchor_positions": ["base"],
            "discriminating_positions": [],
        },
        alignment_overrides={},
        source="synthetic",
    )
    with pytest.raises(ValueError):
        apply_to_delta_mass_discovery(preset)
