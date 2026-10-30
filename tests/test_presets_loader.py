from __future__ import annotations

from pathlib import Path

import pytest

from xic_extractor.presets import PresetError, list_presets, load_preset


def test_load_builtin_dna_dr_preset_uses_owner_backfill_only() -> None:
    preset = load_preset("dna_dr")

    assert preset.name == "DNA dR"
    assert preset.description.startswith("CID neutral-loss discovery")
    assert preset.combine_mode == "single"
    assert len(preset.tags) == 1
    tag = preset.tags[0]
    assert tag.strategy == "neutral_loss"
    assert tag.name == "DNA_dR"
    assert tag.value == pytest.approx(116.0474)
    assert preset.discovery_overrides == {}
    assert preset.alignment_overrides == {}
    assert preset.source == "builtin:dna_dr"


def test_list_presets_works_outside_repo_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)

    assert "dna_dr" in list_presets()
    assert "dna_dr_product_ready" in list_presets()


def test_load_builtin_dna_dr_product_ready_preset_stays_sample_universe_safe() -> None:
    preset = load_preset("dna_dr_product_ready")

    assert preset.name == "DNA dR Owner Backfill (compat)"
    assert preset.combine_mode == "single"
    assert preset.tags[0].name == "DNA_dR"
    assert preset.alignment_overrides == {}


def test_existing_path_toml_rejects_retired_owner_backend_option(
    tmp_path: Path,
) -> None:
    preset_path = tmp_path / "custom.toml"
    preset_path.write_text(
        """
name = "Custom"
description = "Custom neutral loss"
combine_mode = "single"

[[tag]]
strategy = "neutral_loss"
name = "NL116"
value = 116.0474

[discovery]
rt_max = 20.0

[alignment]
owner_build_xic_backend = "raw-super-window"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(PresetError, match="owner_build_xic_backend"):
        load_preset(preset_path)


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
def test_standard_peak_alignment_fields_fail_closed(
    tmp_path: Path,
    field: str,
) -> None:
    preset_path = tmp_path / f"{field}.toml"
    preset_path.write_text(
        "\n".join(
            [
                'name = "Retired"',
                'description = "Retired normal route"',
                'combine_mode = "single"',
                "",
                "[[tag]]",
                'strategy = "neutral_loss"',
                'name = "NL116"',
                "value = 116.0474",
                "",
                "[alignment]",
                f"{field} = true",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(PresetError, match=f"{field}.*diagnostic-only"):
        load_preset(preset_path)


def test_unknown_alignment_key_is_rejected(tmp_path: Path) -> None:
    preset_path = tmp_path / "preset.toml"
    preset_path.write_text(
        """
name = "Alignment"
description = "Alignment"
combine_mode = "single"

[[tag]]
strategy = "neutral_loss"
name = "NL116"
value = 116.0474

[alignment]
preferred_ppm = 10.0
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(PresetError, match="preferred_ppm"):
        load_preset(preset_path)


def test_missing_builtin_error_lists_available_presets() -> None:
    with pytest.raises(PresetError, match="dna_dr_product_ready"):
        load_preset("missing")
