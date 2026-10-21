"""Configured delta-mass methods admit settings at the preset boundary."""

import pytest

from xic_extractor.presets.apply import apply_to_delta_mass_discovery
from xic_extractor.presets.models import Preset, PresetTag


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
