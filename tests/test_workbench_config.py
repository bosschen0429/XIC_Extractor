from __future__ import annotations

from xic_workbench import config_io, discovery_config_io


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
