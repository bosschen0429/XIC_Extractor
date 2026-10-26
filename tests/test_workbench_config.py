from __future__ import annotations

import json

import pytest

from xic_workbench import config_io, discovery_config_io
from xic_workbench.analysis_workspace import AnalysisWorkspace


@pytest.mark.parametrize("preset", ["", "  ", None])
def test_blank_discovery_preset_uses_default_without_rewriting_config(tmp_path, preset):
    path = tmp_path / "discovery_gui.json"
    original = json.dumps({"preset": preset})
    path.write_text(original, encoding="utf-8")

    config = discovery_config_io.read_discovery_config(config_dir=tmp_path)

    assert config["preset"] == "dna_dr"
    assert path.read_text(encoding="utf-8") == original


def test_missing_discovery_preset_is_not_replaced_with_default(tmp_path):
    from xic_extractor.presets import PresetError, load_preset

    missing = str(tmp_path / "missing.toml")
    discovery_config_io.write_discovery_config({"preset": missing}, config_dir=tmp_path)
    config = discovery_config_io.read_discovery_config(config_dir=tmp_path)

    assert config["preset"] == missing
    with pytest.raises(PresetError, match="preset file not found"):
        load_preset(config["preset"])


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


def test_read_discovery_config_preserves_custom_output(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(discovery_config_io, "ROOT", tmp_path)
    monkeypatch.setattr(discovery_config_io, "CONFIG_DIR", tmp_path / "config")
    custom = str(tmp_path / "my_out")
    discovery_config_io.write_discovery_config(
        {"raw_dir": "R", "dll_dir": "D", "output_dir": custom}
    )

    config = discovery_config_io.read_discovery_config()

    assert config["output_dir"] == custom


def test_target_rows_preserve_extra_metadata_order(tmp_path):
    from tests.test_targeted_method_files import rows

    values = rows()
    values[0].update(extra_b="1", extra_a="2")
    config_io.write_targets(values, config_dir=tmp_path)
    saved = config_io.read_targets(config_dir=tmp_path)
    assert list(saved[0]) == list(values[0])
    assert saved == values


def test_discovery_defaults_use_bundled_shared_targets(monkeypatch, tmp_path):
    monkeypatch.setattr(discovery_config_io, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(
        discovery_config_io,
        "targets_template_path",
        lambda: tmp_path / "bundle" / "targets.csv",
    )
    assert discovery_config_io.read_discovery_config()["targets_csv"] == str(
        tmp_path / "bundle" / "targets.csv"
    )
    discovery_config_io.write_discovery_config({"targets_csv": ""})
    assert discovery_config_io.read_discovery_config()["targets_csv"] == ""


def test_invalid_targeted_values_do_not_block_workspace_or_untargeted_dll(tmp_path):
    from xic_extractor.configuration.workspace_state import save_workspace

    save_workspace(tmp_path, settings={"parallel_workers": "0"})
    workspace = AnalysisWorkspace(tmp_path, {})
    try:
        config = workspace.bootstrap()
        workspace.action("untargeted/save", {"dll_dir": "shared-dll"})
    finally:
        workspace.jobs.close()
    assert any(
        "parallel_workers" in warning
        for warning in config["targeted_method"]["warnings"]
    )
    saved = config_io.read_settings(config_dir=tmp_path)
    assert saved["dll_dir"] == "shared-dll"
    assert saved["parallel_workers"] == "0"


def test_untargeted_save_routes_dll_to_canonical_and_preserves_unrelated_controls(
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
    workspace = AnalysisWorkspace(tmp_path, {})
    try:
        workspace.action(
            "untargeted/save", {"dll_dir": "shared-dll", "raw_dir": "new-batch"}
        )
    finally:
        workspace.jobs.close()
    assert config_io.read_settings(config_dir=tmp_path)["dll_dir"] == "shared-dll"
    payload = json.loads(path.read_text())
    assert "dll_dir" not in payload
    # Legacy roles belong to the folder they were saved for.
    assert "custom_roles" not in payload
    assert payload["sequence_source"] == "sequence.csv"
    assert payload["raw_dir"] == "new-batch"


def test_untargeted_save_keeps_legacy_roles_for_their_own_folder(tmp_path):
    from xic_workbench.discovery_config_io import read_legacy_roles

    path = tmp_path / "discovery_gui.json"
    path.write_text(
        json.dumps({"raw_dir": str(tmp_path / "batch"), "custom_roles": {"A": "qc"}}),
        encoding="utf-8",
    )
    workspace = AnalysisWorkspace(tmp_path, {})
    try:
        workspace.action("untargeted/save", {"raw_dir": str(tmp_path / "batch")})
    finally:
        workspace.jobs.close()

    assert read_legacy_roles(tmp_path / "batch", config_dir=tmp_path) == {"A": "qc"}
    assert read_legacy_roles(tmp_path / "other", config_dir=tmp_path) == {}


def test_preferences_write_only_theme_view_and_preserves_existing_other_keys(tmp_path):
    path = tmp_path / "workbench.json"
    path.write_text(
        json.dumps({"theme": "light", "view": "targeted", "future_key": "keep"}),
        encoding="utf-8",
    )
    preferences = config_io.write_preferences({"theme": "dark"}, config_dir=tmp_path)
    assert preferences == {"theme": "dark", "view": "targeted", "future_key": "keep"}
    with pytest.raises(ValueError, match="應用設定"):
        config_io.write_preferences(
            {"dll_dir": "not-authorized-source"}, config_dir=tmp_path
        )
    assert config_io.read_preferences(config_dir=tmp_path) == preferences


def test_launch_dll_override_is_transient_and_both_workflows_share_it(tmp_path):
    config_io.write_settings({"dll_dir": "persisted-dll"}, config_dir=tmp_path)
    before = (tmp_path / "workspace.json").read_bytes()
    workspace = AnalysisWorkspace(tmp_path, {"dll_dir": "launch-dll"})
    try:
        bootstrap = workspace.bootstrap()
        assert bootstrap["settings"]["dll_dir"] == "launch-dll"
        assert bootstrap["untargeted"]["dll_dir"] == "launch-dll"
        assert any("dll_dir" in w for w in bootstrap["targeted_method"]["warnings"])
        assert (tmp_path / "workspace.json").read_bytes() == before
        assert (
            config_io.read_settings(config_dir=tmp_path)["dll_dir"] == "persisted-dll"
        )
    finally:
        workspace.jobs.close()


def test_configuration_reads_are_read_only_and_discovery_does_not_parse_targeted(
    tmp_path,
):
    # Legacy keys and invalid Targeted scalars are data here, not a migration trigger.
    paths = {
        "settings.csv": "key,value\nms2_precursor_tol_da,not-a-number\n",
        "workbench.json": json.dumps({"dll_dir": "old-preference", "theme": "dark"}),
        "discovery_gui.json": json.dumps(
            {"dll_dir": "old-discovery", "raw_dir": "batch"}
        ),
    }
    for name, text in paths.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    before = {name: (tmp_path / name).read_bytes() for name in paths}
    assert "ms2_precursor_tol_da" not in config_io.read_settings(config_dir=tmp_path)
    assert config_io.read_preferences(config_dir=tmp_path)["theme"] == "dark"
    assert (
        discovery_config_io.read_discovery_config(config_dir=tmp_path)["raw_dir"]
        == "batch"
    )
    assert {name: (tmp_path / name).read_bytes() for name in paths} == before
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(paths)


def test_bootstrap_resolves_effective_settings_once(tmp_path, monkeypatch):
    from xic_extractor.configuration import settings as settings_owner

    config_io.write_settings({"dll_dir": "saved-dll"}, config_dir=tmp_path)
    workspace = AnalysisWorkspace(
        tmp_path, {"dll_dir": "launch-dll", "raw_dir": "batch"}
    )
    calls = []
    validate = settings_owner.validate_settings_values

    def count_validation(values, *args):
        calls.append(dict(values))
        return validate(values, *args)

    monkeypatch.setattr(settings_owner, "validate_settings_values", count_validation)
    try:
        config = workspace.bootstrap()
        assert len(calls) == 1
        assert calls[0]["dll_dir"] == "launch-dll"
        assert calls[0]["data_dir"] == "batch"
        assert config["untargeted"]["dll_dir"] == config["settings"]["dll_dir"]
    finally:
        workspace.jobs.close()


def test_default_workspace_save_and_cli_share_isolated_settings(isolated_config_dir):
    from xic_extractor.configuration.execution_profile import load_execution_profile
    from xic_workbench.workspace import ReviewWorkspace

    workspace = ReviewWorkspace()
    try:
        workspace.analysis.action(
            "targeted/save",
            {
                "settings": {"parallel_workers": "3"},
                "targets": [
                    {
                        "label": "test",
                        "mz": "258.1085",
                        "expected_rt_min": "9.0",
                        "cid_nl_tag": "dR",
                        "role": "istd",
                        "istd_pair": "",
                    }
                ],
            },
        )
        workspace.analysis.action("untargeted/save", {"raw_dir": "test-raws"})
    finally:
        workspace.close()

    assert (isolated_config_dir / "workspace.json").is_file()
    assert config_io.read_settings()["parallel_workers"] == "3"
    assert load_execution_profile().parallel_workers == 3
    assert discovery_config_io.read_discovery_config()["raw_dir"] == "test-raws"


def test_reader_setup_installs_and_points_both_workflows_at_rawfilereader(
    tmp_path, monkeypatch
):
    from xic_extractor import thermo_reader_install

    installed = tmp_path / "RawFileReader-8"

    def install(destination=None, *, accept_license, fetch=None):
        if not accept_license:
            raise thermo_reader_install.ThermoReaderInstallError("accept license")
        installed.mkdir()
        for name in (
            "OpenMcdf.dll",
            "ThermoFisher.CommonCore.Data.dll",
            "ThermoFisher.CommonCore.RawFileReader.dll",
        ):
            (installed / name).write_bytes(b"")
        return installed

    monkeypatch.setattr(thermo_reader_install, "install_thermo_reader", install)
    config_io.write_settings(
        {"dll_dir": str(tmp_path / "xcalibur")}, config_dir=tmp_path
    )
    workspace = AnalysisWorkspace(tmp_path, {})
    try:
        assert workspace.bootstrap()["thermo_reader"]["dll_errors"]
        with pytest.raises(ValueError, match="accept license"):
            workspace.action("thermo-reader/install", {})

        result = workspace.action("thermo-reader/install", {"accept_license": True})

        assert result["thermo_reader"]["dll_errors"] == []
        bootstrap = workspace.bootstrap()
        assert bootstrap["settings"]["dll_dir"] == str(installed)
        assert bootstrap["untargeted"]["dll_dir"] == str(installed)
    finally:
        workspace.jobs.close()
