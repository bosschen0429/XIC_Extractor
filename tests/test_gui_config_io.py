from __future__ import annotations

from pathlib import Path

from gui import config_io, discovery_config_io


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
    monkeypatch.setattr(config_io, "_BUNDLE_CONFIG", tmp_path / "bundle")
    assert discovery_config_io.read_discovery_config()["targets_csv"] == str(
        tmp_path / "bundle" / "targets.example.csv"
    )
    discovery_config_io.write_discovery_config({"targets_csv": ""})
    assert discovery_config_io.read_discovery_config()["targets_csv"] == ""


def test_discovery_source_loads_shared_list_and_refreshes_external_edits(
    qtbot, tmp_path
):
    from gui.sections.discovery_source_section import DiscoverySourceSection
    from xic_extractor.configuration.targets import read_istd_targets

    section = DiscoverySourceSection()
    qtbot.addWidget(section)
    source = config_io.targets_example_path()
    selection = read_istd_targets(source)
    assert str(len(selection.targets)) in section._targets_summary.text()
    assert str(selection.skipped_count) in section._targets_summary.text()
    path = tmp_path / "targets.csv"
    path.write_text(
        "label,mz,rt_min,rt_max,ppm_tol,neutral_loss_da,nl_ppm_warn,nl_ppm_max,is_istd,istd_pair\n"
        "OnlyAnalyte,100,1,2,5,,,,false,\n",
        encoding="utf-8",
    )
    section._fields["targets_csv"].set_value(str(path))
    assert "is_istd=TRUE" in section._targets_summary.text()
    assert any("內標清單" in message for message in section.missing_fields())
    path.write_text(path.read_text().replace("false", "TRUE"), encoding="utf-8")
    section._on_targets_changed()
    assert "已載入 1 筆內標" in section._targets_summary.text()
    assert not any("內標清單" in message for message in section.missing_fields())


def test_discovery_source_saves_copy_before_office_open(monkeypatch, qtbot, tmp_path):
    import gui.sections.discovery_source_section as module

    section = module.DiscoverySourceSection()
    qtbot.addWidget(section)
    source = config_io.targets_example_path()
    before = source.read_bytes()
    copied = tmp_path / "my_targets.csv"
    monkeypatch.setattr(
        module.QFileDialog, "getSaveFileName", lambda *a: (str(copied), "")
    )
    opened = []
    monkeypatch.setattr(
        module.QDesktopServices,
        "openUrl",
        lambda url: opened.append(url.toLocalFile()) or True,
    )
    section._open_targets()
    assert copied.read_bytes() == before == source.read_bytes()
    assert Path(opened[0]) == copied
    assert section.get_values()["targets_csv"] == str(copied)


def test_discovery_source_reports_malformed_external_target_list(qtbot, tmp_path):
    from gui.sections.discovery_source_section import DiscoverySourceSection

    path = tmp_path / "missing_column.csv"
    path.write_text("label,mz\nISTD,300\n", encoding="utf-8")
    section = DiscoverySourceSection()
    qtbot.addWidget(section)
    section._fields["targets_csv"].set_value(str(path))
    assert "required column is missing" in section._targets_summary.text()
    assert any("內標清單" in reason for reason in section.missing_fields())
    assert section._targets_section.is_expanded()


def test_discovery_source_compacts_reusable_settings_without_changing_values(
    qtbot, tmp_path
):
    from gui.sections.discovery_source_section import DiscoverySourceSection

    raw_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    raw_dir.mkdir()
    dll_dir.mkdir()
    section = DiscoverySourceSection()
    qtbot.addWidget(section)
    section.load(
        {
            "mode": "full",
            "raw_dir": str(raw_dir),
            "targets_csv": str(config_io.targets_example_path()),
            "dll_dir": str(dll_dir),
            "output_dir": str(tmp_path / "output"),
        }
    )
    before = section.get_values()

    assert not section._dll_section.is_expanded()
    assert not section._targets_section.is_expanded()
    section._dll_section.set_expanded(True)
    section._targets_section.set_expanded(True)
    section._dll_section.set_expanded(False)
    section._targets_section.set_expanded(False)

    assert section.get_values() == before

    section.load({**before, "dll_dir": str(tmp_path / "missing-dll")})
    assert section._dll_section.is_expanded()


def test_discovery_source_prepares_next_batch_once_and_preserves_method_context(
    qtbot, tmp_path
):
    from gui.sections.discovery_source_section import DiscoverySourceSection

    raw_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    raw_dir.mkdir()
    dll_dir.mkdir()
    batch_index = tmp_path / "discovery_batch_index.csv"
    batch_index.write_text("raw_path\n", encoding="utf-8")
    section = DiscoverySourceSection()
    qtbot.addWidget(section)
    section.load(
        {
            "mode": "align_only",
            "raw_dir": str(raw_dir),
            "discovery_batch_index": str(batch_index),
            "targets_csv": str(config_io.targets_example_path()),
            "dll_dir": str(dll_dir),
            "output_dir": str(tmp_path / "output"),
        }
    )
    before = section.get_values()
    requests: list[bool] = []
    section.new_batch_requested.connect(lambda: requests.append(True))

    section._new_batch_btn.click()

    assert requests == [True]
    assert section.get_values() == before

    changes: list[bool] = []
    section.changed.connect(lambda: changes.append(True))
    section.new_batch_requested.connect(section.prepare_next_batch)
    section._new_batch_btn.click()
    after = section.get_values()

    assert requests == [True, True]
    assert changes == [True]
    assert after["mode"] == before["mode"]
    assert after["dll_dir"] == before["dll_dir"]
    assert after["targets_csv"] == before["targets_csv"]
    assert after["raw_dir"] == ""
    assert after["raw_file"] == ""
    assert after["discovery_batch_index"] == ""
    assert after["output_dir"] == ""
