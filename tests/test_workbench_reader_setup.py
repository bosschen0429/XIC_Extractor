"""Reader setup uses an existing installation without rewriting user intent."""

import pytest

from tests.test_xic_workbench import _client
from xic_extractor import raw_reader, thermo_reader_install
from xic_workbench.config_io import read_settings, write_dll_dir
from xic_workbench.workspace import ReviewWorkspace


@pytest.fixture
def installed_reader(tmp_path, monkeypatch):
    installed = tmp_path / "managed-reader"
    installed.mkdir()
    for name in raw_reader.EXPECTED_THERMO_DLLS:
        (installed / name).write_bytes(b"fixture assembly")
    monkeypatch.setattr(
        thermo_reader_install, "default_thermo_reader_dir", lambda: installed
    )
    monkeypatch.setattr(raw_reader, "dotnet_runtime_errors", lambda: [])
    return installed


@pytest.mark.parametrize("choice", ["legacy", "custom", "launch-override"])
def test_bootstrap_selects_installed_reader_only_for_missing_or_legacy_defaults(
    tmp_path, installed_reader, choice
):
    config = tmp_path / "config"
    selected = "C:/Xcalibur/system/programs"
    defaults = {}
    if choice == "custom":
        custom = tmp_path / "custom-reader"
        custom.mkdir()
        for name in raw_reader.EXPECTED_THERMO_DLLS:
            (custom / name).write_bytes(b"custom assembly")
        selected = str(custom)
    if choice == "launch-override":
        defaults["dll_dir"] = str(tmp_path / "explicit-reader")
    write_dll_dir(selected, config_dir=config)
    before = (config / "workspace.json").read_bytes()
    with _client(ReviewWorkspace(config_dir=config, defaults=defaults)) as request:
        status, data, _ = request("/api/config")
        assert status == 200
        expected = (
            str(installed_reader)
            if choice == "legacy"
            else defaults.get("dll_dir", selected)
        )
        assert data["settings"]["dll_dir"] == expected
        assert data["untargeted"]["dll_dir"] == expected
        assert (config / "workspace.json").read_bytes() == before


def test_using_installed_reader_replaces_an_old_launch_override_and_persists(
    tmp_path, installed_reader
):
    config = tmp_path / "config"
    workspace = ReviewWorkspace(
        config_dir=config, defaults={"dll_dir": "missing-reader"}
    )
    with _client(workspace) as request:
        status, data, _ = request("/api/config")
        assert status == 200 and data["thermo_reader"]["installed_dir"] == str(
            installed_reader
        )
        status, selected, _ = request("/api/thermo-reader/use-installed", {})
        assert status == 200 and selected["dll_dir"] == str(installed_reader)
        assert selected["thermo_reader"]["dll_errors"] == []
        assert read_settings(config_dir=config)["dll_dir"] == str(installed_reader)
        assert not workspace.defaults.get("dll_dir")
        assert request("/api/config")[1]["settings"]["dll_dir"] == str(installed_reader)
