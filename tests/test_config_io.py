import pytest

from xic_extractor.configuration.models import ConfigError
from xic_workbench.config_io import (
    read_settings,
    read_targets,
    write_settings,
    write_targets,
)


def test_json_settings_and_targets_have_one_state_file(tmp_path):
    write_settings({"data_dir": "local", "parallel_workers": "3"}, config_dir=tmp_path)
    rows = [
        {
            "label": "IS",
            "mz": "258.1085",
            "expected_rt_min": "9",
            "cid_nl_tag": "dR",
            "role": "istd",
            "istd_pair": "",
        }
    ]
    write_targets(rows, config_dir=tmp_path)
    assert read_settings(config_dir=tmp_path)["data_dir"] == "local"
    assert read_targets(config_dir=tmp_path) == rows
    assert {p.name for p in tmp_path.iterdir()} == {"workspace.json"}


def test_old_csv_inputs_are_not_read_or_rewritten(tmp_path):
    originals = {}
    for name in ("settings.csv", "targets.csv", "settings.example.csv"):
        path = tmp_path / name
        path.write_text("invalid legacy content", encoding="utf-8")
        originals[path] = path.read_bytes()
    assert read_targets(config_dir=tmp_path) == []
    write_settings({"parallel_workers": "2"}, config_dir=tmp_path)
    assert all(path.read_bytes() == content for path, content in originals.items())
    assert read_settings(config_dir=tmp_path)["parallel_workers"] == "2"


def test_save_merges_partial_values_and_preserves_existing_runtime_and_resources(
    tmp_path,
):
    write_settings(
        {
            "data_dir": "local",
            "parallel_workers": "1",
            "injection_order_source": "prior.csv",
        },
        config_dir=tmp_path,
    )
    write_settings({"dll_dir": "new-dll"}, config_dir=tmp_path)
    values = read_settings(config_dir=tmp_path)
    assert values["data_dir"] == "local"
    assert values["parallel_workers"] == "1"
    assert values["injection_order_source"] == "prior.csv"
    assert values["dll_dir"] == "new-dll"


def test_failed_atomic_replace_preserves_active_settings_and_cleans_temporary(
    tmp_path, monkeypatch
):
    write_settings({"dll_dir": "original-dll"}, config_dir=tmp_path)
    path = tmp_path / "workspace.json"
    before = path.read_bytes()

    def failed_replace(*_args):
        raise OSError("simulated interrupted save")

    monkeypatch.setattr(
        "xic_extractor.configuration.workspace_state.os.replace", failed_replace
    )
    with pytest.raises(OSError, match="interrupted"):
        write_settings({"dll_dir": "new-dll"}, config_dir=tmp_path)
    assert path.read_bytes() == before
    assert {p.name for p in tmp_path.iterdir()} == {"workspace.json"}


def test_invalid_save_preserves_active_file(tmp_path):
    write_settings({}, config_dir=tmp_path)
    path = tmp_path / "workspace.json"
    before = path.read_bytes()
    with pytest.raises(ConfigError):
        write_settings({"signal_processing_window_points": "16"}, config_dir=tmp_path)
    assert path.read_bytes() == before


def test_retired_setting_is_rejected_without_changing_work(tmp_path):
    write_settings({"dll_dir": "original-dll"}, config_dir=tmp_path)
    before = (tmp_path / "workspace.json").read_bytes()
    with pytest.raises(ConfigError, match="resolver_mode"):
        write_settings({"resolver_mode": "legacy_savgol"}, config_dir=tmp_path)
    assert (tmp_path / "workspace.json").read_bytes() == before


def test_preferences_replace_atomically_without_duplicate_backup(tmp_path, monkeypatch):
    from xic_workbench.config_io import read_preferences, write_preferences

    write_preferences({"view": "review"}, config_dir=tmp_path)
    write_preferences({"theme": "dark"}, config_dir=tmp_path)
    assert read_preferences(config_dir=tmp_path) == {"view": "review", "theme": "dark"}
    assert {p.name for p in tmp_path.iterdir()} == {"workbench.json"}
    before = (tmp_path / "workbench.json").read_bytes()

    def failed_replace(*_args):
        raise OSError("interrupted")

    monkeypatch.setattr("xic_workbench.config_io.os.replace", failed_replace)
    with pytest.raises(OSError, match="interrupted"):
        write_preferences({"view": "untargeted"}, config_dir=tmp_path)
    assert (tmp_path / "workbench.json").read_bytes() == before
    assert {p.name for p in tmp_path.iterdir()} == {"workbench.json"}


def test_work_state_saves_batch_and_targets_in_one_atomic_generation(
    tmp_path, monkeypatch
):
    import json

    from xic_extractor.configuration.workspace_state import save_workspace

    save_workspace(tmp_path, settings={"data_dir": "old"}, targets=[{"label": "old"}])
    before = (tmp_path / "workspace.json").read_bytes()

    def fail(*args):
        raise OSError("interrupted")

    with monkeypatch.context() as patch:
        patch.setattr("xic_extractor.configuration.workspace_state.os.replace", fail)
        with pytest.raises(OSError):
            save_workspace(
                tmp_path, settings={"data_dir": "new"}, targets=[{"label": "new"}]
            )
    assert (tmp_path / "workspace.json").read_bytes() == before
    save_workspace(tmp_path, settings={"data_dir": "new"}, targets=[{"label": "new"}])
    state = json.loads((tmp_path / "workspace.json").read_text())
    assert set(state) == {"version", "application", "batch", "targets"}
    assert state["batch"]["data_dir"] == "new"
    assert state["targets"] == [{"label": "new"}]
