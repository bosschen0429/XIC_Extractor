"""Verify method composition support in alignment and SQLite explanation."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from scripts.run_alignment import _parse_args, _resolve_preset
from tests.alignment_explanation_fixtures import explanation_summary
from xic_extractor.alignment_explanation.reader import (
    read_alignment_explanation,
)
from xic_extractor.alignment_explanation.storage import (
    EXPLANATION_ARTIFACT_NAME,
)
from xic_extractor.alignment_explanation.writer import (
    ExplanationReportWriter,
)
from xic_extractor.configuration.cid_nl_profile import NeutralLossProfile
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.presets import PresetTag, load_preset
from xic_extractor.presets.apply import bind_cid_nl_recipe
from xic_extractor.untargeted_execution.input_planning import (
    is_supported_alignment_discovery,
)


def test_is_supported_alignment_discovery_accepts_single_and_multi_tag_settings():
    dna_dr = load_preset("dna_dr")
    multi_tag = replace(
        dna_dr,
        name="Multi NL",
        combine_mode="union",
        tags=(
            PresetTag(strategy="neutral_loss", name="dR", value=116.0474),
            PresetTag(strategy="neutral_loss", name="R", value=132.0423),
        ),
    )
    for preset in (dna_dr, multi_tag):
        recipe = bind_cid_nl_recipe(preset, "align_only")
        assert is_supported_alignment_discovery(recipe.discovery_settings) is True

    settings = DiscoverySettings(
        neutral_loss_profile=NeutralLossProfile(
            tag="DNA_dR",
            neutral_loss_da=116.0474,
        )
    )
    assert is_supported_alignment_discovery(settings) is True


def test_run_alignment_script_resolves_supported_preset():
    args = _parse_args(
        [
            "--preset",
            "dna_dr",
            "--discovery-batch-index",
            "discovery_batch_index.csv",
            "--raw-dir",
            "raws",
            "--dll-dir",
            "dll",
        ]
    )
    preset, recipe = _resolve_preset(args)
    assert preset.name == "DNA dR"
    assert is_supported_alignment_discovery(recipe.discovery_settings) is True


def test_sqlite_diagnostic_roundtrip(tmp_path: Path):
    db_path = tmp_path / EXPLANATION_ARTIFACT_NAME
    assert EXPLANATION_ARTIFACT_NAME == "alignment_explanation.sqlite"

    with ExplanationReportWriter(db_path) as writer:
        writer.finalize(
            explanation_summary(
                {"processing_algorithm_version": "discovery"},
            )
        )

    rows_generic = read_alignment_explanation(db_path)
    rows_cid_nl = read_alignment_explanation(db_path)
    assert rows_generic == rows_cid_nl
    assert rows_generic["diagnostic_only"] is True
    assert rows_generic["processing_algorithm_version"] == "discovery"
