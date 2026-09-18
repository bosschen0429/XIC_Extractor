"""Verify method composition extensibility across run spec, alignment,
and SQLite explanation.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path

from scripts.run_alignment import _resolve_preset
from tests.test_processing_run_spec import _compile_paths, _raws
from xic_extractor.diagnostics.cid_nl_explanation import (
    GENERIC_REPORT_FILENAME,
    read_alignment_curves,
    read_alignment_explanation,
    read_cid_nl_curves,
    read_cid_nl_explanation,
)
from xic_extractor.diagnostics.cid_nl_explanation_projection import (
    write_alignment_explanation,
    write_cid_nl_explanation,
)
from xic_extractor.discovery.models import DiscoverySettings, NeutralLossProfile
from xic_extractor.extraction.untargeted_cid_nl_production_batch import (
    is_supported_alignment_discovery,
)
from xic_extractor.presets import PresetTag, load_preset
from xic_extractor.processing_run_spec import (
    DiscoveryMethodSpec,
    GenericDiscoveryMethodSpec,
    compile_processing_run_spec,
    deserialize_processing_run_spec,
    serialize_processing_run_spec,
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

    @property
    def method_hash(self) -> str:
        return "mock_delta_mass_hash_123"

    @property
    def method_id(self) -> str:
        return "mock_delta_mass_v1"


def test_custom_discovery_method_spec_satisfies_protocol():
    spec = MockDeltaMassDiscoveryMethodSpec()
    assert isinstance(spec, DiscoveryMethodSpec)
    assert spec.method == "delta_mass"
    assert spec.schema_version == "1.0.0"
    assert spec.method_id == "mock_delta_mass_v1"


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
    assert run_spec.run_spec_hash is not None
    assert len(run_spec.run_spec_hash) == 64
    assert run_spec.run_spec_id.startswith("processing-run.v1:")

    # Serialization and deserialization roundtrip
    serialized = serialize_processing_run_spec(run_spec)
    deserialized = deserialize_processing_run_spec(serialized)
    assert deserialized.run_spec_hash == run_spec.run_spec_hash
    assert deserialized.discovery_method.method == "delta_mass"


def test_is_supported_alignment_discovery_accepts_composite_and_multi_tag():
    # Builtin single-tag preset
    dna_dr = load_preset("dna_dr")
    assert is_supported_alignment_discovery(dna_dr) is True

    # Builtin composite Pt pattern + CID-NL preset
    pt_composite = load_preset("pt_pattern_cid_nl_dr")
    assert is_supported_alignment_discovery(pt_composite) is True

    # Multi-tag neutral loss preset (union or intersection)
    multi_tag = replace(
        dna_dr,
        name="Multi NL",
        combine_mode="union",
        tags=(
            PresetTag(strategy="neutral_loss", name="dR", value=116.0474),
            PresetTag(strategy="neutral_loss", name="R", value=132.0423),
        ),
    )
    assert is_supported_alignment_discovery(multi_tag) is True

    # DiscoverySettings
    settings = DiscoverySettings(
        neutral_loss_profile=NeutralLossProfile(
            tag="DNA_dR",
            neutral_loss_da=116.0474,
        )
    )
    assert is_supported_alignment_discovery(settings) is True

    # DiscoveryMethodSpec
    custom_spec = MockDeltaMassDiscoveryMethodSpec()
    assert is_supported_alignment_discovery(custom_spec) is True

    # Unsupported strategy
    unsupported = replace(
        dna_dr,
        name="Unsupported",
        tags=(PresetTag(strategy="future_quantum_orbitrap", name="Q", value=999.0),),
    )
    assert is_supported_alignment_discovery(unsupported) is False


def test_run_alignment_script_resolves_supported_preset():
    args = argparse.Namespace(preset="dna_dr", owner_build_xic_backend=None)
    preset, recipe = _resolve_preset(args)
    assert preset.name == "DNA dR"
    assert is_supported_alignment_discovery(recipe.discovery_settings) is True


def test_generic_sqlite_diagnostic_aliases_and_roundtrip(tmp_path: Path):
    db_path = tmp_path / GENERIC_REPORT_FILENAME
    assert GENERIC_REPORT_FILENAME == "alignment_explanation.sqlite"
    assert read_alignment_explanation is read_cid_nl_explanation
    assert read_alignment_curves is read_cid_nl_curves
    assert write_alignment_explanation is write_cid_nl_explanation

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA user_version = 1")
        conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, payload TEXT)")
        conn.execute(
            "CREATE TABLE candidates (candidate_id TEXT, sample_id TEXT, payload TEXT)"
        )
        conn.execute("CREATE TABLE records (kind TEXT, record_id TEXT, payload TEXT)")
        conn.execute(
            "CREATE TABLE links ("
            "candidate_id TEXT, sample_id TEXT, kind TEXT, record_id TEXT"
            ")"
        )
        summary_payload = json.dumps(
            {
                "schema_version": 1,
                "diagnostic_only": True,
                "completion": "post_matrix_publication",
                "method": "discovery",
            }
        )
        conn.execute("INSERT INTO metadata VALUES ('summary', ?)", (summary_payload,))
        conn.commit()

    rows_generic = read_alignment_explanation(db_path)
    rows_legacy = read_cid_nl_explanation(db_path)
    assert rows_generic == rows_legacy
    assert rows_generic["diagnostic_only"] is True
    assert rows_generic["method"] == "discovery"
