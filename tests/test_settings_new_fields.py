"""Effective scientific strategy is recorded, never a settings override."""

import json
from pathlib import Path

from xic_extractor.configuration.settings import (
    build_extraction_config,
    resolve_settings,
)
from xic_extractor.output.metadata import build_metadata_rows
from xic_extractor.settings_schema import TARGETED_STRATEGY


def test_run_metadata_records_effective_shared_strategy(tmp_path):
    values, parsed, warnings = resolve_settings(
        {"data_dir": str(tmp_path), "dll_dir": str(tmp_path)}
    )
    config = build_extraction_config(parsed, values, Path("settings.csv"), tmp_path)
    metadata = dict(build_metadata_rows(config))
    receipt = json.loads(metadata["targeted_strategy"])
    assert receipt["strategy"] == TARGETED_STRATEGY
    assert receipt["candidate_coverage"] == "complete_acquisition_exact_mass"
    assert receipt["cid_nl_recipes"]["dR"]["nl_tolerance_ppm"] == 20
    assert set(receipt["cid_nl_recipes"]) == {"dR", "R", "MeR"}
    assert not warnings


def test_home_relative_directories_reach_runtime_as_expanded_paths(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("raw", "vendor"):
        (tmp_path / name).mkdir()
    values, parsed, _ = resolve_settings({"data_dir": "~/raw", "dll_dir": "~/vendor"})
    config = build_extraction_config(parsed, values, Path("settings.csv"), tmp_path)
    assert config.data_dir == tmp_path / "raw"
    assert config.dll_dir == tmp_path / "vendor"
