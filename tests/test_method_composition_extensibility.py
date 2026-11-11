"""Verify method composition extensibility across run spec, alignment,
and SQLite explanation.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from scripts.run_alignment import _parse_args, _resolve_preset
from tests.alignment_explanation_fixtures import explanation_summary
from tests.processing_run_spec_fixtures import _compile_paths, _raws
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
from xic_extractor.processing_run_spec import (
    GenericDiscoveryMethodSpec,
    GenericMethodSettings,
    compile_processing_run_spec,
    deserialize_processing_run_spec,
    serialize_processing_run_spec,
)
from xic_extractor.untargeted_execution.input_planning import (
    is_supported_alignment_discovery,
)


@dataclass(frozen=True)
class MockDeltaMassDiscoverySettings:
    pattern_id: str = "pt"
    mz_tolerance_ppm: float = 20.0
    rt_tolerance_sec: float = 30.0


@dataclass(frozen=True)
class MockDeltaMassDiscoveryMethodSpec:
    settings: MockDeltaMassDiscoverySettings = MockDeltaMassDiscoverySettings()
    method: str = "delta_mass"
    schema_version: str = "1.0.0"


def test_compile_processing_run_spec_with_custom_discovery_method(tmp_path: Path):
    raw_paths = _raws(tmp_path)
    base_spec = _compile_paths(raw_paths)
    inputs = base_spec.acquisition_inputs
    signal_profile = base_spec.signal_processing_profile
    custom_spec = GenericDiscoveryMethodSpec(
        settings=MockDeltaMassDiscoverySettings(),
        method="delta_mass",
        schema_version="1.0.0",
    )

    run_spec = compile_processing_run_spec(
        acquisition_inputs=inputs,
        signal_processing_profile=signal_profile,
        discovery_method=custom_spec,
    )

    assert run_spec.discovery_method.method == "delta_mass"

    # Serialization and deserialization roundtrip preserves spec equality
    # and attribute access
    serialized = serialize_processing_run_spec(run_spec)
    deserialized = deserialize_processing_run_spec(serialized)
    assert deserialized.discovery_method.method == "delta_mass"
    assert deserialized == run_spec
    assert isinstance(deserialized.discovery_method.settings, GenericMethodSettings)
    assert deserialized.discovery_method.settings.pattern_id == "pt"
    assert deserialized.discovery_method.settings["pattern_id"] == "pt"
    assert deserialized.discovery_method.settings == MockDeltaMassDiscoverySettings()


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
                review_only=writer.review_only,
            )
        )

    rows_generic = read_alignment_explanation(db_path)
    rows_cid_nl = read_alignment_explanation(db_path)
    assert rows_generic == rows_cid_nl
    assert rows_generic["diagnostic_only"] is True
    assert rows_generic["processing_algorithm_version"] == "discovery"
