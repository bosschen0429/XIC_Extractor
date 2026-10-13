import csv

import pytest

from xic_extractor.configuration.csv_io import read_settings as read_settings_csv
from xic_extractor.configuration.models import ConfigError
from xic_extractor.configuration.settings import resolve_settings
from xic_workbench.config_io import (
    read_settings,
    read_targets,
    write_settings,
    write_targets,
)

TARGET_CSV = (
    "label,mz,expected_rt_min,cid_nl_tag,role,istd_pair\n"
    "5-hmdC,258.1085,9,dR,analyte,d3-5-hmdC\n"
    "d3-5-hmdC,261.1273,9,dR,istd,\n"
)


@pytest.fixture()
def tmp_config(tmp_path):
    (tmp_path / "settings.csv").write_text(
        "key,value,description\ndata_dir,C:\\data,資料目錄\ndll_dir,C:\\dll,DLL路徑\n",
        encoding="utf-8-sig",
    )
    (tmp_path / "targets.csv").write_text(TARGET_CSV, encoding="utf-8-sig")
    return tmp_path


def test_read_settings(tmp_config, monkeypatch):
    monkeypatch.setattr("xic_workbench.config_io.CONFIG_DIR", tmp_config)
    assert read_settings()["data_dir"] == "C:\\data"


def test_write_settings_round_trips(tmp_config, monkeypatch):
    monkeypatch.setattr("xic_workbench.config_io.CONFIG_DIR", tmp_config)
    write_settings({"data_dir": "C:\\new", "dll_dir": "C:\\d"})
    assert read_settings()["data_dir"] == "C:\\new"
    assert read_settings()["dll_dir"] == "C:\\d"


def test_write_settings_preserves_description(tmp_config, monkeypatch):
    monkeypatch.setattr("xic_workbench.config_io.CONFIG_DIR", tmp_config)
    write_settings({"data_dir": "C:\\x", "dll_dir": "C:\\y"})
    with (tmp_config / "settings.csv").open(encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    assert {row["key"]: row["description"] for row in rows}["data_dir"] == "資料目錄"


def test_write_settings_backfills_descriptions_for_current_settings(tmp_config):
    write_settings({"injection_order_source": "order.csv"}, config_dir=tmp_config)
    with (tmp_config / "settings.csv").open(encoding="utf-8-sig") as handle:
        descriptions = {
            row["key"]: row["description"] for row in csv.DictReader(handle)
        }
    assert descriptions["injection_order_source"]


def test_read_targets(tmp_config, monkeypatch):
    monkeypatch.setattr("xic_workbench.config_io.CONFIG_DIR", tmp_config)
    assert read_targets()[0]["label"] == "5-hmdC"


def test_write_targets_round_trips_intent_and_relationships(tmp_config, monkeypatch):
    monkeypatch.setattr("xic_workbench.config_io.CONFIG_DIR", tmp_config)
    rows = list(csv.DictReader(TARGET_CSV.splitlines()))
    rows[0]["expected_rt_min"] = "9.1"
    write_targets(rows)
    saved = read_targets()
    assert saved[0]["label"] == "5-hmdC"
    assert saved[0]["expected_rt_min"] == "9.1"
    assert saved[0]["cid_nl_tag"] == "dR"
    assert saved[0]["role"] == "analyte"
    assert saved[0]["istd_pair"] == "d3-5-hmdC"
    assert saved[1]["role"] == "istd"
    assert saved[1]["istd_pair"] == ""


def test_read_settings_uses_example_without_creating_runtime_copy(
    tmp_path, monkeypatch
):
    (tmp_path / "settings.example.csv").write_text(
        "key,value,description\ndata_dir,/placeholder,資料目錄\n",
        encoding="utf-8-sig",
    )
    monkeypatch.setattr("xic_workbench.config_io.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("xic_workbench.config_io._BUNDLE_CONFIG", tmp_path)
    assert read_settings()["data_dir"] == "/placeholder"
    assert not (tmp_path / "settings.csv").exists()


def test_read_targets_uses_example_without_creating_runtime_copy(tmp_path, monkeypatch):
    (tmp_path / "targets.example.csv").write_text(TARGET_CSV, encoding="utf-8-sig")
    monkeypatch.setattr("xic_workbench.config_io.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("xic_workbench.config_io._BUNDLE_CONFIG", tmp_path)
    assert read_targets()[0]["label"] == "5-hmdC"
    assert not (tmp_path / "targets.csv").exists()


def test_write_settings_creates_runtime_copy_with_example_descriptions(
    tmp_path, monkeypatch
):
    (tmp_path / "settings.example.csv").write_text(
        "key,value,description\ndata_dir,/placeholder,資料目錄\n",
        encoding="utf-8-sig",
    )
    monkeypatch.setattr("xic_workbench.config_io.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("xic_workbench.config_io._BUNDLE_CONFIG", tmp_path)
    write_settings({"data_dir": "C:\\real"})
    with (tmp_path / "settings.csv").open(encoding="utf-8-sig") as handle:
        saved = {row["key"]: row for row in csv.DictReader(handle)}
    assert saved["data_dir"] == {
        "key": "data_dir",
        "value": "C:\\real",
        "description": "資料目錄",
    }
    assert set(saved) == {
        "data_dir",
        "dll_dir",
        "injection_order_source",
        "parallel_workers",
    }
    assert saved["parallel_workers"]["value"] == ""
    assert saved["parallel_workers"]["description"]


def test_write_targets_creates_runtime_copy_only_on_save(tmp_path, monkeypatch):
    monkeypatch.setattr("xic_workbench.config_io.CONFIG_DIR", tmp_path)
    write_targets(list(csv.DictReader(TARGET_CSV.splitlines())))
    assert (tmp_path / "targets.csv").exists()
    assert read_targets()[0]["label"] == "5-hmdC"


def test_csv_conflicts_are_rejected_in_ui_and_runtime_reader(tmp_path):
    path = tmp_path / "settings.csv"
    path.write_text("key,value\ndata_dir,first\ndata_dir,second\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="conflict"):
        resolve_settings(read_settings(config_dir=tmp_path))
    with pytest.raises(ConfigError, match="conflict"):
        read_settings_csv(path)


def test_equal_csv_duplicates_have_one_written_row_and_recovery_backup(tmp_path):
    path = tmp_path / "settings.csv"
    path.write_text(
        "key,value,description\ndata_dir,local,canonical\ndata_dir,local,duplicate\n",
        encoding="utf-8",
    )
    original = path.read_bytes()
    write_settings({"injection_order_source": "order.csv"}, config_dir=tmp_path)
    with path.open(encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    assert sum(row["key"] == "data_dir" for row in rows) == 1
    assert path.with_suffix(".csv.bak").read_bytes() == original


@pytest.mark.parametrize("workers", ["", "7"])
def test_save_merges_partial_batch_values_and_preserves_machine_settings(
    tmp_path, workers
):
    write_settings(
        {"data_dir": "local", "dll_dir": "dll", "parallel_workers": workers},
        config_dir=tmp_path,
    )
    write_settings({"injection_order_source": "order.csv"}, config_dir=tmp_path)
    values = read_settings(config_dir=tmp_path)
    assert values == {
        "data_dir": "local",
        "dll_dir": "dll",
        "injection_order_source": "order.csv",
        "parallel_workers": workers,
    }


def test_failed_atomic_replace_preserves_active_settings_and_cleans_temporary(
    tmp_path, monkeypatch
):
    write_settings({"data_dir": "first"}, config_dir=tmp_path)
    path = tmp_path / "settings.csv"
    before = path.read_bytes()

    def failed_replace(*_args):
        raise OSError("simulated interrupted save")

    monkeypatch.setattr("xic_workbench.config_io.os.replace", failed_replace)
    with pytest.raises(OSError, match="interrupted"):
        write_settings({"data_dir": "second"}, config_dir=tmp_path)
    assert path.read_bytes() == before
    assert list(tmp_path.glob(".settings-*.tmp")) == []


@pytest.mark.parametrize(
    "key",
    [
        "signal_processing_window_points",
        "dirty_matrix_mode",
        "resolver_mode",
    ],
)
def test_retired_settings_save_is_rejected_without_changing_active_file(tmp_path, key):
    write_settings({}, config_dir=tmp_path)
    path = tmp_path / "settings.csv"
    before = path.read_bytes()
    with pytest.raises(ConfigError, match="Retired or unknown"):
        write_settings({key: "1"}, config_dir=tmp_path)
    assert path.read_bytes() == before


def test_invalid_target_save_preserves_active_file(tmp_config):
    path = tmp_config / "targets.csv"
    before = path.read_bytes()
    with pytest.raises(ConfigError, match="unsupported target schema"):
        write_targets(
            [{"label": "old", "rt_min": "8", "rt_max": "10"}], config_dir=tmp_config
        )
    assert path.read_bytes() == before
