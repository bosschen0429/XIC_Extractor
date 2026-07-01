"""Contract tests for Pt pattern preset support (Task 1 RED phase).

Covers the ``pt_pattern`` and ``pt_pattern_cid_nl_dr`` builtin presets
introduced in Task 4. ``pt_pattern`` is Pt-only discovery; ``pt_pattern_cid_nl_dr``
is the intersection strategy requiring an existing CID-NL(dR) candidate for
the Pt base feature. Expected to fail on import/collection until Task 4
implements preset support.

``method_label`` is exposed via ``apply_to_delta_mass_discovery(preset)``,
which returns a ``DeltaMassPatternSettings`` (plan lines 522-528, 729-733).
It is not a bare attribute on ``Preset`` itself -- ``Preset`` only carries the
raw TOML ``[discovery]`` keys through ``discovery_overrides`` (plan lines
742-746; ``xic_extractor/presets/models.py``).
"""

from __future__ import annotations

import pytest

from xic_extractor.presets import PresetError, list_presets, load_preset
from xic_extractor.presets.apply import apply_to_delta_mass_discovery


def test_pt_pattern_preset_is_listed_as_a_builtin_preset() -> None:
    assert "pt_pattern" in list_presets()
    assert "pt_pattern_cid_nl_dr" in list_presets()


def test_pt_pattern_preset_loads_with_method_label_pt_pattern() -> None:
    preset = load_preset("pt_pattern")

    assert preset.name
    assert preset.source == "builtin:pt_pattern"
    settings = apply_to_delta_mass_discovery(preset)
    assert settings.method_label == "pt_pattern"


def test_pt_pattern_cid_nl_dr_preset_loads_with_composite_method_label() -> None:
    preset = load_preset("pt_pattern_cid_nl_dr")

    assert preset.name
    assert preset.source == "builtin:pt_pattern_cid_nl_dr"
    settings = apply_to_delta_mass_discovery(preset)
    assert settings.method_label == "pt_pattern+cid_nl_dr"


def test_pt_pattern_preset_discovery_overrides_carries_raw_method_label() -> None:
    preset = load_preset("pt_pattern")

    assert preset.discovery_overrides["method_label"] == "pt_pattern"


def test_missing_preset_error_lists_pt_pattern_presets_as_available() -> None:
    with pytest.raises(PresetError, match="pt_pattern"):
        load_preset("not-a-real-preset")
