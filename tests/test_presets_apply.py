from __future__ import annotations

from dataclasses import FrozenInstanceError, fields

import pytest

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.discovery.models import DiscoverySettings, NeutralLossProfile
from xic_extractor.presets import (
    PresetError,
    apply_to_alignment,
    apply_to_discovery,
    bind_cid_nl_recipe,
    filter_cid_nl_recipe_presets,
    list_presets,
)
from xic_extractor.presets.loader import load_preset
from xic_extractor.presets.models import Preset, PresetTag


def test_cid_nl_preset_filter_matches_the_package_binder() -> None:
    available = list_presets()
    filtered = filter_cid_nl_recipe_presets(available)

    assert filtered
    assert "dna_dr" in filtered
    assert "dna_dr_product_ready" in filtered
    assert "pt_pattern" not in filtered
    assert "pt_pattern_cid_nl_dr" not in filtered
    for name in filtered:
        bind_cid_nl_recipe(load_preset(name), "discovery_only")
    for name in set(available) - set(filtered):
        with pytest.raises((PresetError, ValueError)):
            bind_cid_nl_recipe(load_preset(name), "discovery_only")


def test_apply_to_discovery_builds_single_dr_settings() -> None:
    preset = load_preset("dna_dr")
    expected_profile = NeutralLossProfile("DNA_dR", 116.0474)
    expected_defaults = DiscoverySettings(
        neutral_loss_profiles=(expected_profile,),
        selected_tag_names=("DNA_dR",),
        tag_combine_mode="single",
    )

    settings = apply_to_discovery(preset)

    assert settings == expected_defaults


def test_apply_to_discovery_merges_preset_then_explicit_tuning() -> None:
    preset = Preset(
        name="Custom DNA dR",
        description="Custom preset",
        tags=(PresetTag(strategy="neutral_loss", name="DNA_dR", value=116.0474),),
        combine_mode="single",
        discovery_overrides={"rt_max": 30.0, "nl_tolerance_ppm": 15.0},
        alignment_overrides={},
        source="test",
    )

    settings = apply_to_discovery(
        preset,
        explicit_tuning_overrides={"rt_max": 20.0, "ms2_precursor_tol_da": 0.75},
    )

    assert settings.rt_max == pytest.approx(20.0)
    assert settings.nl_tolerance_ppm == pytest.approx(15.0)
    assert settings.ms2_precursor_tol_da == pytest.approx(0.75)


def test_apply_to_alignment_returns_owner_backend_only() -> None:
    preset = load_preset("dna_dr")

    alignment_config, run_overrides = apply_to_alignment(preset)

    assert alignment_config == AlignmentConfig()
    assert run_overrides == {"owner_build_xic_backend": "raw"}


def test_apply_to_alignment_returns_product_ready_runtime_options() -> None:
    preset = load_preset("dna_dr_product_ready")

    alignment_config, run_overrides = apply_to_alignment(preset)

    assert alignment_config == AlignmentConfig()
    assert run_overrides == {"owner_build_xic_backend": "raw-super-window"}


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("owner_build_xic_backend", "fast-ish", "owner_build_xic_backend"),
    ],
)
def test_apply_to_alignment_rejects_invalid_runtime_options(
    field: str,
    value: object,
    message: str,
) -> None:
    preset = Preset(
        name="Invalid alignment",
        description="Invalid alignment",
        tags=(PresetTag(strategy="neutral_loss", name="DNA_dR", value=116.0474),),
        combine_mode="single",
        discovery_overrides={},
        alignment_overrides={field: value},
        source="test",
    )

    with pytest.raises(PresetError, match=message):
        apply_to_alignment(preset)


@pytest.mark.parametrize(
    "field",
    [
        "standard_peak_backfill",
        "standard_peak_backfill_chunk_size",
        "standard_peak_backfill_publication_mode",
        "standard_peak_backfill_write_gallery",
        "standard_peak_backfill_reuse_existing",
        "standard_peak_backfill_min_shape_r",
        "backfill_expansion_productization",
        "backfill_expansion_reuse_existing_raw_overlay",
        "backfill_expansion_reuse_existing_shift_aware",
        "backfill_expansion_render_shift_aware_images",
        "backfill_expansion_min_shape_r",
    ],
)
def test_apply_to_alignment_rejects_retired_normal_route_fields(field: str) -> None:
    preset = Preset(
        name="Retired route",
        description="Retired route",
        tags=(PresetTag(strategy="neutral_loss", name="DNA_dR", value=116.0474),),
        combine_mode="single",
        discovery_overrides={},
        alignment_overrides={field: True},
        source="test",
    )

    with pytest.raises(PresetError, match=f"{field}.*diagnostic-only"):
        apply_to_alignment(preset)


def test_bind_cid_nl_recipe_preserves_owner_backend() -> None:
    preset = Preset(
        name="Indexed owner construction",
        description="Owner construction is independent of Standard Peak",
        tags=(PresetTag(strategy="neutral_loss", name="DNA_dR", value=116.0474),),
        combine_mode="single",
        discovery_overrides={},
        alignment_overrides={
            "owner_build_xic_backend": "ms1-index",
        },
        source="test",
    )

    _, runtime_options = apply_to_alignment(preset)
    recipe = bind_cid_nl_recipe(preset, "full")

    assert runtime_options == {"owner_build_xic_backend": "ms1-index"}
    assert recipe.owner_build_xic_backend == "ms1_index"


def test_bind_cid_nl_recipe_closes_the_generic_matrix_method_surface() -> None:
    recipe = bind_cid_nl_recipe(load_preset("dna_dr"), "full")

    assert recipe.route == "full"
    assert recipe.discovery_settings == apply_to_discovery(load_preset("dna_dr"))
    assert recipe.alignment_config == AlignmentConfig()
    assert recipe.owner_build_xic_backend == "raw"
    assert {item.name for item in fields(recipe)} == {
        "route",
        "discovery_settings",
        "alignment_config",
        "owner_build_xic_backend",
    }
    with pytest.raises(FrozenInstanceError):
        setattr(recipe, "route", "discovery_only")


def test_bind_cid_nl_recipe_applies_only_recipe_owned_overrides() -> None:
    recipe = bind_cid_nl_recipe(
        load_preset("dna_dr_product_ready"),
        "align_only",
        recipe_overrides={
            "rt_max": 20.0,
            "owner_build_xic_backend": "ms1-index",
        },
    )

    assert recipe.discovery_settings.rt_max == pytest.approx(20.0)
    assert recipe.owner_build_xic_backend == "ms1_index"

    with pytest.raises(PresetError, match="unknown CID-NL recipe override"):
        bind_cid_nl_recipe(
            load_preset("dna_dr"),
            "full",
            recipe_overrides={"raw_dir": "not-recipe-content"},
        )


def test_bind_cid_nl_recipe_preserves_route_compatibility_boundaries() -> None:
    multi_tag = Preset(
        name="Two CID-NL tags",
        description="Generic full-universe preset",
        tags=(
            PresetTag(strategy="neutral_loss", name="A", value=100.0),
            PresetTag(strategy="neutral_loss", name="B", value=120.0),
        ),
        combine_mode="union",
        discovery_overrides={},
        alignment_overrides={},
        source="test",
    )

    generic_recipe = bind_cid_nl_recipe(multi_tag, "full")
    assert len(generic_recipe.discovery_settings.neutral_loss_profiles) == 2
    with pytest.raises(ValueError, match="single-tag CID-NL preset"):
        bind_cid_nl_recipe(multi_tag, "bounded_mode_track")
    with pytest.raises(PresetError, match="neutral-loss preset"):
        bind_cid_nl_recipe(load_preset("pt_pattern"), "discovery_only")
