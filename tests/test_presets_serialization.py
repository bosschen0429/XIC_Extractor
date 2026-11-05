from __future__ import annotations

import math
import tomllib
from pathlib import Path

import pytest

from xic_extractor.presets import (
    Preset,
    PresetError,
    PresetTag,
    apply_to_alignment,
    apply_to_delta_mass_discovery,
    apply_to_discovery,
    load_preset,
)
from xic_extractor.presets.loader import DISCOVERY_TUNING_OVERRIDE_KEYS
from xic_extractor.presets.serialization import save_preset


def test_save_builtin_neutral_loss_preset_roundtrips(tmp_path: Path) -> None:
    destination = tmp_path / "dna_dr.toml"

    saved = save_preset(load_preset("dna_dr"), destination)

    assert saved == load_preset(destination)


@pytest.mark.parametrize(
    "preset_name",
    ("dna_dr", "pt_pattern", "pt_pattern_cid_nl_dr"),
)
def test_all_builtin_presets_roundtrip_their_existing_owner_settings(
    tmp_path: Path,
    preset_name: str,
) -> None:
    original = load_preset(preset_name)
    reloaded = save_preset(original, tmp_path / f"{preset_name}.toml")

    assert reloaded.name == original.name
    assert reloaded.description == original.description
    assert reloaded.tags == original.tags
    assert reloaded.combine_mode == original.combine_mode
    assert apply_to_alignment(reloaded) == apply_to_alignment(original)
    if any(tag.strategy == "delta_mass" for tag in original.tags):
        assert apply_to_delta_mass_discovery(reloaded) == apply_to_delta_mass_discovery(
            original
        )
    else:
        assert apply_to_discovery(reloaded) == apply_to_discovery(original)


def test_neutral_loss_export_materializes_full_effective_tuning_and_overrides(
    tmp_path: Path,
) -> None:
    original = load_preset("dna_dr")
    destination = tmp_path / "effective.toml"

    reloaded = save_preset(
        original,
        destination,
        discovery_overrides={
            "rt_max": 42.5,
            "resolver_mode": "legacy_savgol",
        },
    )

    expected = apply_to_discovery(
        original,
        explicit_tuning_overrides={
            "rt_max": 42.5,
            "resolver_mode": "legacy_savgol",
        },
    )
    assert apply_to_discovery(reloaded) == expected
    parsed = tomllib.loads(destination.read_text(encoding="utf-8"))
    assert set(parsed["discovery"]) == set(DISCOVERY_TUNING_OVERRIDE_KEYS) - {
        "rt_min",
        "mass_recalibration",
    }
    assert "alignment" not in parsed


def test_auto_range_export_omits_bounds_and_can_clear_explicit_crop(
    tmp_path: Path,
) -> None:
    original = load_preset("dna_dr")
    cropped = save_preset(
        original,
        tmp_path / "crop.toml",
        discovery_overrides={"rt_min": 1.0, "rt_max": 75.0},
    )
    restored = save_preset(
        cropped,
        tmp_path / "auto.toml",
        discovery_overrides={"rt_min": None, "rt_max": None},
    )
    parsed = tomllib.loads((tmp_path / "auto.toml").read_text(encoding="utf-8"))
    assert "rt_min" not in parsed["discovery"]
    assert "rt_max" not in parsed["discovery"]
    settings = apply_to_discovery(restored)
    assert settings.rt_min is None and settings.rt_max is None


def test_custom_multi_tag_union_roundtrip(tmp_path: Path) -> None:
    original = Preset(
        name="A/B custom",
        description="Union with explicit tuning",
        tags=(
            PresetTag("neutral_loss", "A", value=100.25),
            PresetTag("neutral_loss", "B", value=120.5),
        ),
        combine_mode="union",
        discovery_overrides={"nl_tolerance_ppm": 12.0},
        alignment_overrides={},
        source="test",
    )

    reloaded = save_preset(
        original,
        tmp_path / "multi.toml",
        discovery_overrides={"ms1_search_padding_min": 0.8},
    )

    assert apply_to_discovery(reloaded) == apply_to_discovery(
        original,
        explicit_tuning_overrides={"ms1_search_padding_min": 0.8},
    )
    assert apply_to_alignment(reloaded) == apply_to_alignment(original)
    assert reloaded.tags == original.tags


def test_pt_export_uses_delta_mass_projection_for_explicit_overrides(
    tmp_path: Path,
) -> None:
    original = load_preset("pt_pattern")

    reloaded = save_preset(
        original,
        tmp_path / "pt.toml",
        discovery_overrides={
            "delta_mass_mz_tolerance_ppm": 31.5,
            "apex_floor_sec": 18.0,
        },
    )

    assert apply_to_delta_mass_discovery(reloaded) == apply_to_delta_mass_discovery(
        Preset(
            name=original.name,
            description=original.description,
            tags=original.tags,
            combine_mode=original.combine_mode,
            discovery_overrides={
                **original.discovery_overrides,
                "delta_mass_mz_tolerance_ppm": 31.5,
                "apex_floor_sec": 18.0,
            },
            alignment_overrides=original.alignment_overrides,
            source=original.source,
        )
    )


def test_special_characters_in_metadata_and_tags_survive_roundtrip(
    tmp_path: Path,
) -> None:
    original = Preset(
        name='測試 "preset"\\line\n下一行',
        description='描述 "quoted"\\path\n含 Unicode\x7f',
        tags=(PresetTag("neutral_loss", 'tag "one"\\x\n二', value=116.0474),),
        combine_mode="single",
        discovery_overrides={},
        alignment_overrides={},
        source="test",
    )

    reloaded = save_preset(original, tmp_path / "unicode.toml")

    assert reloaded.name == original.name
    assert reloaded.description == original.description
    assert reloaded.tags == original.tags


@pytest.mark.parametrize(
    "invalid_preset",
    (
        Preset(
            name="Unknown discovery",
            description="invalid",
            tags=(PresetTag("neutral_loss", "A", value=100.0),),
            combine_mode="single",
            discovery_overrides={"unknown": 1.0},
            alignment_overrides={},
            source="test",
        ),
        Preset(
            name="Unknown alignment",
            description="invalid",
            tags=(PresetTag("neutral_loss", "A", value=100.0),),
            combine_mode="single",
            discovery_overrides={},
            alignment_overrides={"unknown": 1.0},
            source="test",
        ),
        Preset(
            name="Non-finite tag",
            description="invalid",
            tags=(PresetTag("neutral_loss", "A", value=math.nan),),
            combine_mode="single",
            discovery_overrides={},
            alignment_overrides={},
            source="test",
        ),
    ),
)
def test_invalid_export_preserves_existing_destination(
    tmp_path: Path,
    invalid_preset: Preset,
) -> None:
    destination = tmp_path / "existing.toml"
    previous = b"keep these bytes exactly"
    destination.write_bytes(previous)

    with pytest.raises((PresetError, ValueError)):
        save_preset(invalid_preset, destination)

    assert destination.read_bytes() == previous


@pytest.mark.parametrize("resolver_mode", ["savgol", "legacy_savgol"])
def test_resolver_input_exports_only_canonical_savgol(
    tmp_path: Path, resolver_mode: str
) -> None:
    """Remove the alias case only after existing user presets have migrated."""
    original = load_preset("dna_dr")
    settings = apply_to_discovery(
        original, explicit_tuning_overrides={"resolver_mode": resolver_mode}
    )
    assert settings.resolver_mode == "savgol"
    reloaded = save_preset(
        original,
        tmp_path / "savgol.toml",
        discovery_overrides={"resolver_mode": resolver_mode},
    )
    parsed = tomllib.loads((tmp_path / "savgol.toml").read_text(encoding="utf-8"))
    assert parsed["discovery"]["resolver_mode"] == "savgol"
    assert apply_to_discovery(reloaded) == settings
    # Exercise saved user input through the TOML loader, not only apply overrides.
    saved_text = (tmp_path / "savgol.toml").read_text(encoding="utf-8")
    (tmp_path / "savgol.toml").write_text(
        saved_text.replace(
            'resolver_mode = "savgol"', f'resolver_mode = "{resolver_mode}"'
        ),
        encoding="utf-8",
    )
    assert (
        load_preset(tmp_path / "savgol.toml").discovery_overrides["resolver_mode"]
        == "savgol"
    )
