from __future__ import annotations

import json

import pytest

from xic_workbench import config_io, discovery_config_io
from xic_workbench.analysis_workspace import AnalysisWorkspace


def test_read_discovery_config_defaults_output_to_repo_output(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(discovery_config_io, "ROOT", tmp_path)
    monkeypatch.setattr(discovery_config_io, "CONFIG_DIR", tmp_path / "config")

    config = discovery_config_io.read_discovery_config()

    assert config["output_dir"] == str(tmp_path / "output")


def test_read_discovery_config_blank_output_falls_back_to_default(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(discovery_config_io, "ROOT", tmp_path)
    monkeypatch.setattr(discovery_config_io, "CONFIG_DIR", tmp_path / "config")
    discovery_config_io.write_discovery_config(
        {"raw_dir": "R", "dll_dir": "D", "output_dir": ""}
    )

    config = discovery_config_io.read_discovery_config()

    assert config["output_dir"] == str(tmp_path / "output")
    assert config["raw_dir"] == "R"
    assert config["dll_dir"] == "D"


def test_read_discovery_config_preserves_custom_output(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(discovery_config_io, "ROOT", tmp_path)
    monkeypatch.setattr(discovery_config_io, "CONFIG_DIR", tmp_path / "config")
    custom = str(tmp_path / "my_out")
    discovery_config_io.write_discovery_config(
        {"raw_dir": "R", "dll_dir": "D", "output_dir": custom}
    )

    config = discovery_config_io.read_discovery_config()

    assert config["output_dir"] == custom


def test_target_write_fieldnames_preserves_extra_order() -> None:
    fieldnames = config_io._target_write_fieldnames(
        [
            {"Target": "A", "extra_b": "1", "extra_a": "2"},
            {"extra_a": "3", "extra_c": "4"},
        ],
    )

    assert fieldnames[: len(config_io.TARGET_WRITE_FIELDS)] == list(
        config_io.TARGET_WRITE_FIELDS,
    )
    assert fieldnames[-3:] == ["extra_b", "extra_a", "extra_c"]


def test_discovery_defaults_use_bundled_shared_targets(monkeypatch, tmp_path):
    monkeypatch.setattr(discovery_config_io, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(
        discovery_config_io,
        "targets_example_path",
        lambda: tmp_path / "bundle" / "targets.example.csv",
    )
    assert discovery_config_io.read_discovery_config()["targets_csv"] == str(
        tmp_path / "bundle" / "targets.example.csv"
    )
    discovery_config_io.write_discovery_config({"targets_csv": ""})
    assert discovery_config_io.read_discovery_config()["targets_csv"] == ""


def test_legacy_preference_dll_migrates_once_preserving_effective_value_and_json(
    tmp_path,
):
    config_io.write_settings({"dll_dir": "canonical-old"}, config_dir=tmp_path)
    preference = {
        "dll_dir": "formerly-effective",
        "theme": "dark",
        "view": "targeted",
        "future_key": [1, 2],
    }
    discovery = {
        "dll_dir": "discovery-old",
        "preset": "dna_dr",
        "custom_roles": {"A": "istd"},
        "overrides": {"ppm": 12},
        "future_discovery": 3,
    }
    preference_path = tmp_path / "workbench.json"
    discovery_path = tmp_path / "discovery_gui.json"
    preference_path.write_text(json.dumps(preference), encoding="utf-8")
    discovery_path.write_text(json.dumps(discovery), encoding="utf-8")

    resolved = config_io.read_resolved_settings(config_dir=tmp_path)
    assert resolved.values["dll_dir"] == "formerly-effective"
    assert any(
        "dll_migration=workbench.json" in receipt for receipt in resolved.receipts
    )
    assert json.loads(preference_path.read_text()) == {
        key: value for key, value in preference.items() if key != "dll_dir"
    }
    assert json.loads(discovery_path.read_text()) == {
        key: value for key, value in discovery.items() if key != "dll_dir"
    }
    assert (
        json.loads(preference_path.with_suffix(".json.bak").read_text()) == preference
    )
    assert json.loads(discovery_path.with_suffix(".json.bak").read_text()) == discovery
    assert config_io.migrate_legacy_dll_settings(config_dir=tmp_path) == ()

    config_io.write_settings({"dll_dir": "canonical-new"}, config_dir=tmp_path)
    assert config_io.read_settings(config_dir=tmp_path)["dll_dir"] == "canonical-new"
    assert (
        discovery_config_io.read_discovery_config(config_dir=tmp_path)["dll_dir"]
        == "canonical-new"
    )
    assert "dll_dir" not in config_io.read_preferences(config_dir=tmp_path)


@pytest.mark.parametrize(
    "canonical,expected",
    [
        (None, "discovery-dll"),
        ("", "discovery-dll"),
        ("<thermo-dll-dir>", "discovery-dll"),
        ("real-canonical", "real-canonical"),
    ],
)
def test_legacy_discovery_dll_is_only_fallback_for_missing_canonical(
    tmp_path, canonical, expected
):
    if canonical is not None:
        config_io.write_settings({"dll_dir": canonical}, config_dir=tmp_path)
    path = tmp_path / "discovery_gui.json"
    path.write_text(
        json.dumps({"dll_dir": "discovery-dll", "custom_roles": {"B": "sample"}}),
        encoding="utf-8",
    )
    discovery = discovery_config_io.read_discovery_config(config_dir=tmp_path)
    assert discovery["dll_dir"] == expected
    assert discovery["custom_roles"] == {"B": "sample"}
    assert "dll_dir" not in json.loads(path.read_text())


def test_discovery_save_routes_dll_to_canonical_and_preserves_unrelated_controls(
    tmp_path,
):
    path = tmp_path / "discovery_gui.json"
    path.write_text(
        json.dumps(
            {
                "custom_roles": {"A": "istd"},
                "sequence_source": "sequence.csv",
                "future_key": "keep",
            }
        ),
        encoding="utf-8",
    )
    discovery_config_io.write_discovery_config(
        {"dll_dir": "shared-dll", "raw_dir": "new-batch"}, config_dir=tmp_path
    )
    assert config_io.read_settings(config_dir=tmp_path)["dll_dir"] == "shared-dll"
    payload = json.loads(path.read_text())
    assert "dll_dir" not in payload
    assert payload["custom_roles"] == {"A": "istd"}
    assert payload["sequence_source"] == "sequence.csv"
    assert payload["future_key"] == "keep"
    assert payload["raw_dir"] == "new-batch"


def test_preferences_write_only_theme_view_and_preserves_existing_other_keys(tmp_path):
    path = tmp_path / "workbench.json"
    path.write_text(
        json.dumps({"theme": "light", "view": "targeted", "future_key": "keep"}),
        encoding="utf-8",
    )
    preferences = config_io.write_preferences({"theme": "dark"}, config_dir=tmp_path)
    assert preferences == {"theme": "dark", "view": "targeted", "future_key": "keep"}
    with pytest.raises(ValueError, match="settings.csv"):
        config_io.write_preferences(
            {"dll_dir": "not-authorized-source"}, config_dir=tmp_path
        )
    assert config_io.read_preferences(config_dir=tmp_path) == preferences


def test_launch_dll_override_is_transient_and_both_workflows_share_it(tmp_path):
    config_io.write_settings({"dll_dir": "persisted-dll"}, config_dir=tmp_path)
    before = (tmp_path / "settings.csv").read_bytes()
    workspace = AnalysisWorkspace(tmp_path, {"dll_dir": "launch-dll"})
    try:
        bootstrap = workspace.bootstrap()
        assert bootstrap["settings"]["dll_dir"] == "launch-dll"
        assert bootstrap["untargeted"]["dll_dir"] == "launch-dll"
        assert bootstrap["targeted_method"]["provenance"]["dll_dir"] == "launch"
        assert (tmp_path / "settings.csv").read_bytes() == before
        assert (
            config_io.read_settings(config_dir=tmp_path)["dll_dir"] == "persisted-dll"
        )
    finally:
        workspace.jobs.close()
