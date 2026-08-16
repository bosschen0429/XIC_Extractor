from __future__ import annotations

import tomllib
from importlib.resources import files


def test_builtin_dna_dr_toml_is_packaged_resource() -> None:
    resource = files("xic_extractor.presets.data").joinpath("dna_dr.toml")

    assert resource.is_file()
    text = resource.read_text(encoding="utf-8")
    assert 'name = "DNA dR"' in text
    assert 'name = "DNA_dR"' in text
    assert "116.0474" in text
    assert "standard_peak_backfill" not in text
    assert tomllib.loads(text)["alignment"] == {}


def test_dna_dr_value_matches_deoxyribose_neutral_loss_residue_mass() -> None:
    resource = files("xic_extractor.presets.data").joinpath("dna_dr.toml")
    parsed = tomllib.loads(resource.read_text(encoding="utf-8"))
    tag = parsed["tag"][0]

    mono = {
        "C": 12.0,
        "H": 1.00782503223,
        "O": 15.99491461957,
    }
    # C5H8O3 is the neutral-loss residue, not free deoxyribose mass.
    expected = 5 * mono["C"] + 8 * mono["H"] + 3 * mono["O"]
    observed = tag["value"]
    ppm_error = abs(observed - expected) / expected * 1_000_000

    assert tag["strategy"] == "neutral_loss"
    assert tag["name"] == "DNA_dR"
    assert ppm_error < 1.0
