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

from pathlib import Path

import pytest

from xic_extractor.presets import PresetError, list_presets, load_preset
from xic_extractor.presets.apply import apply_to_delta_mass_discovery
from xic_extractor.presets.models import Preset, PresetTag


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


def test_apply_to_delta_mass_discovery_rejects_multiple_delta_mass_tags() -> None:
    """Reproduces the reviewer's finding: a directly-constructed ``Preset``
    bypasses loader-level composite-shape validation, so
    ``apply_to_delta_mass_discovery()`` must reject this shape itself.
    """
    preset = Preset(
        name="Two delta_mass tags",
        description="Invalid: two delta_mass tags",
        tags=(
            PresetTag(strategy="delta_mass", name="pt_a", pattern_id="pt"),
            PresetTag(strategy="delta_mass", name="pt_b", pattern_id="pt"),
        ),
        combine_mode="union",
        discovery_overrides={"method_label": "pt_pattern"},
        alignment_overrides={},
        source="test",
    )

    with pytest.raises(PresetError, match="more than one 'delta_mass' tag"):
        apply_to_delta_mass_discovery(preset)


def test_apply_to_delta_mass_discovery_rejects_stray_neutral_loss_tag() -> None:
    """A stray neutral_loss tag alongside delta_mass must require
    combine_mode == "intersection", mirroring the loader's composite-shape
    rule, even when the ``Preset`` was constructed directly.
    """
    preset = Preset(
        name="Delta mass plus stray neutral loss",
        description="Invalid: neutral_loss tag with non-intersection combine_mode",
        tags=(
            PresetTag(strategy="delta_mass", name="pt_a", pattern_id="pt"),
            PresetTag(strategy="neutral_loss", name="DNA_dR", value=116.0474),
        ),
        combine_mode="union",
        discovery_overrides={"method_label": "pt_pattern"},
        alignment_overrides={},
        source="test",
    )

    with pytest.raises(PresetError, match='combine_mode = "intersection"'):
        apply_to_delta_mass_discovery(preset)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("delta_mass_mz_tolerance_ppm", -1.0),
        ("delta_mass_mz_tolerance_ppm", float("nan")),
        ("delta_mass_rt_tolerance_sec", -1.0),
        ("delta_mass_rt_tolerance_sec", float("nan")),
    ],
)
def test_apply_to_delta_mass_discovery_rejects_invalid_positive_tolerances(
    field: str,
    value: float,
) -> None:
    preset = Preset(
        name="Invalid Pt tolerance",
        description="Invalid numeric gate",
        tags=(PresetTag(strategy="delta_mass", name="pt", pattern_id="pt"),),
        combine_mode="single",
        discovery_overrides={"method_label": "pt_pattern", field: value},
        alignment_overrides={},
        source="test",
    )

    with pytest.raises(PresetError, match=field):
        apply_to_delta_mass_discovery(preset)


def test_apply_to_delta_mass_discovery_rejects_unsupported_ratio_basis() -> None:
    preset = Preset(
        name="Unsupported Pt ratio basis",
        description="Only the implemented height-and-area basis is valid",
        tags=(PresetTag(strategy="delta_mass", name="pt", pattern_id="pt"),),
        combine_mode="single",
        discovery_overrides={
            "method_label": "pt_pattern",
            "delta_mass_ratio_basis": "height_only",
        },
        alignment_overrides={},
        source="test",
    )

    with pytest.raises(PresetError, match="delta_mass_ratio_basis"):
        apply_to_delta_mass_discovery(preset)


@pytest.mark.parametrize(
    ("direct_tags", "toml_tag", "message"),
    (
        (
            (
                PresetTag(
                    strategy="delta_mass",
                    name="pt",
                    value=999.0,
                    pattern_id="pt",
                ),
            ),
            """
[[tag]]
strategy = "delta_mass"
name = "pt"
pattern_id = "pt"
value = 999.0
""",
            "must not set 'value'",
        ),
        (
            (
                PresetTag(strategy="delta_mass", name="pt", pattern_id="pt"),
                PresetTag(
                    strategy="neutral_loss",
                    name="DNA_dR",
                    value=116.0474,
                    pattern_id="not_allowed",
                ),
            ),
            """
[[tag]]
strategy = "delta_mass"
name = "pt"
pattern_id = "pt"

[[tag]]
strategy = "neutral_loss"
name = "DNA_dR"
value = 116.0474
pattern_id = "not_allowed"
""",
            "must not set 'pattern_id'",
        ),
    ),
)
def test_delta_mass_tag_field_shape_is_equally_fail_closed_for_direct_and_loader(
    tmp_path: Path,
    direct_tags: tuple[PresetTag, ...],
    toml_tag: str,
    message: str,
) -> None:
    combine_mode = "single" if len(direct_tags) == 1 else "intersection"
    preset = Preset(
        name="Invalid tag field shape",
        description="Direct Preset must obey the TOML loader contract",
        tags=direct_tags,
        combine_mode=combine_mode,  # type: ignore[arg-type]
        discovery_overrides={"method_label": "pt_pattern"},
        alignment_overrides={},
        source="test",
    )
    with pytest.raises(PresetError, match=message):
        apply_to_delta_mass_discovery(preset)

    preset_path = tmp_path / "invalid_tag_field_shape.toml"
    preset_path.write_text(
        (
            'name = "Invalid tag field shape"\n'
            'description = "Loader must obey the same contract"\n'
            f'combine_mode = "{combine_mode}"\n'
            f"{toml_tag}\n"
            "[discovery]\n"
            'method_label = "pt_pattern"\n'
        ),
        encoding="utf-8",
    )
    with pytest.raises(PresetError, match=message):
        load_preset(preset_path)


@pytest.mark.parametrize(
    ("tag_name", "tag_value"),
    (("other_loss", 116.0474), ("DNA_dR", 100.0)),
)
def test_apply_to_delta_mass_discovery_rejects_unimplemented_composite_semantics(
    tag_name: str,
    tag_value: float,
) -> None:
    preset = Preset(
        name="Unsupported Pt composite",
        description="Only exact Pt + DNA_dR is implemented",
        tags=(
            PresetTag(strategy="delta_mass", name="pt", pattern_id="pt"),
            PresetTag(
                strategy="neutral_loss",
                name=tag_name,
                value=tag_value,
            ),
        ),
        combine_mode="intersection",
        discovery_overrides={"method_label": "pt_pattern+unsupported"},
        alignment_overrides={},
        source="test",
    )

    with pytest.raises(PresetError, match="requires exactly neutral_loss"):
        apply_to_delta_mass_discovery(preset)


def test_loader_rejects_unimplemented_delta_mass_composite_semantics(
    tmp_path: Path,
) -> None:
    preset_path = tmp_path / "unsupported_pt_composite.toml"
    preset_path.write_text(
        """
name = "Unsupported Pt composite"
description = "Not implemented"
combine_mode = "intersection"

[[tag]]
strategy = "delta_mass"
name = "Pt pattern"
pattern_id = "pt"

[[tag]]
strategy = "neutral_loss"
name = "other_loss"
value = 116.0474

[discovery]
method_label = "pt_pattern+other"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(PresetError, match="requires exactly neutral_loss"):
        load_preset(preset_path)
