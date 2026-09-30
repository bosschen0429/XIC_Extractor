import csv

import pytest

from gui.config_io import read_settings, read_targets, write_settings, write_targets


@pytest.fixture()
def tmp_config(tmp_path):
    (tmp_path / "settings.csv").write_text(
        "key,value,description\ndata_dir,C:\\data,資料目錄\ndll_dir,C:\\dll,DLL路徑\n"
        "signal_processing_method,gaussian,method\n"
        "signal_processing_window_points,15,window\n",
        encoding="utf-8-sig",
    )
    (tmp_path / "targets.csv").write_text(
        "label,mz,rt_min,rt_max,ppm_tol,neutral_loss_da,nl_ppm_warn,nl_ppm_max,is_istd,istd_pair\n"
        "5-hmdC,258.1085,8.0,10.0,20,116.0474,20,50,false,d3-5-hmdC\n"
        "d3-5-hmdC,261.1273,8.0,10.0,20,116.0474,20,50,true,\n",
        encoding="utf-8-sig",
    )
    return tmp_path


def test_read_settings(tmp_config, monkeypatch):
    monkeypatch.setattr("gui.config_io.CONFIG_DIR", tmp_config)
    assert read_settings()["signal_processing_window_points"] == "15"


def test_saved_worker_budget_reaches_shared_execution_profile(tmp_config, monkeypatch):
    from xic_extractor.configuration.execution_profile import resolve_execution_profile

    monkeypatch.setattr("gui.config_io.CONFIG_DIR", tmp_config)
    settings = read_settings()
    settings["parallel_workers"] = "3"
    write_settings(settings)
    profile = resolve_execution_profile(read_settings())
    assert profile.parallel_workers == 3
    assert profile.alignment_workers == 4


def test_write_settings_round_trips(tmp_config, monkeypatch):
    monkeypatch.setattr("gui.config_io.CONFIG_DIR", tmp_config)
    write_settings(
        {
            "data_dir": "C:\\new",
            "dll_dir": "C:\\d",
            "signal_processing_method": "gaussian",
            "signal_processing_window_points": "21",
        }
    )
    assert read_settings()["data_dir"] == "C:\\new"


def test_write_settings_preserves_description(tmp_config, monkeypatch):
    monkeypatch.setattr("gui.config_io.CONFIG_DIR", tmp_config)
    write_settings(
        {
            "data_dir": "C:\\x",
            "dll_dir": "C:\\y",
            "signal_processing_method": "gaussian",
            "signal_processing_window_points": "15",
        }
    )
    rows = list(
        csv.DictReader((tmp_config / "settings.csv").open(encoding="utf-8-sig"))
    )
    assert {row["key"]: row["description"] for row in rows}["data_dir"] == "資料目錄"


def test_write_settings_backfills_descriptions_for_current_settings(
    tmp_config, monkeypatch
):
    monkeypatch.setattr("gui.config_io.CONFIG_DIR", tmp_config)
    write_settings(
        {
            "data_dir": "C:\\x",
            "dll_dir": "C:\\y",
            "signal_processing_method": "gaussian",
            "signal_processing_window_points": "15",
            "ms2_precursor_tol_da": "0.5",
            "nl_min_intensity_ratio": "0.01",
            "count_no_ms2_as_detected": "false",
        }
    )

    rows = list(
        csv.DictReader((tmp_config / "settings.csv").open(encoding="utf-8-sig"))
    )

    descriptions = {row["key"]: row["description"] for row in rows}
    assert descriptions["signal_processing_method"]
    assert descriptions["signal_processing_window_points"]
    assert descriptions["count_no_ms2_as_detected"]


def test_read_targets(tmp_config, monkeypatch):
    monkeypatch.setattr("gui.config_io.CONFIG_DIR", tmp_config)
    assert read_targets()[0]["label"] == "5-hmdC"


def test_write_targets_round_trips(tmp_config, monkeypatch):
    monkeypatch.setattr("gui.config_io.CONFIG_DIR", tmp_config)
    write_targets(
        [
            {
                "label": "5-hmdC",
                "mz": "258.1085",
                "rt_min": "8.0",
                "rt_max": "10.0",
                "ppm_tol": "20",
                "neutral_loss_da": "116.0474",
                "nl_ppm_warn": "20",
                "nl_ppm_max": "50",
                "is_istd": "false",
                "istd_pair": "d3-5-hmdC",
            }
        ]
    )
    assert read_targets()[0]["label"] == "5-hmdC"


def test_write_targets_round_trips_with_istd_fields(tmp_config, monkeypatch):
    monkeypatch.setattr("gui.config_io.CONFIG_DIR", tmp_config)
    write_targets(
        [
            {
                "label": "5-hmdC",
                "mz": "258.1085",
                "rt_min": "8.0",
                "rt_max": "10.0",
                "ppm_tol": "20",
                "neutral_loss_da": "116.0474",
                "nl_ppm_warn": "20",
                "nl_ppm_max": "50",
                "is_istd": "false",
                "istd_pair": "d3-5-hmdC",
            }
        ]
    )
    targets = read_targets()
    assert targets[0]["is_istd"] == "false"
    assert targets[0]["istd_pair"] == "d3-5-hmdC"


def test_read_targets_backward_compat_missing_istd_cols(tmp_path, monkeypatch):
    """Old CSV without is_istd/istd_pair columns reads without error."""
    (tmp_path / "targets.csv").write_text(
        "label,mz,rt_min,rt_max,ppm_tol,neutral_loss_da,nl_ppm_warn,nl_ppm_max\n"
        "5-hmdC,258.1085,8,10,20,116.0474,20,50\n",
        encoding="utf-8-sig",
    )
    monkeypatch.setattr("gui.config_io.CONFIG_DIR", tmp_path)
    targets = read_targets()
    assert targets[0]["label"] == "5-hmdC"
    assert targets[0].get("is_istd", "false") == "false"


def test_read_settings_uses_example_when_missing_without_creating_runtime_copy(
    tmp_path, monkeypatch
):
    """read_settings() 可讀取範本，但不主動產生 settings.csv。"""
    (tmp_path / "settings.example.csv").write_text(
        "key,value,description\ndata_dir,/placeholder,資料目錄\n",
        encoding="utf-8-sig",
    )
    monkeypatch.setattr("gui.config_io.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("gui.config_io._BUNDLE_CONFIG", tmp_path)
    result = read_settings()
    assert result["data_dir"] == "/placeholder"
    assert not (tmp_path / "settings.csv").exists()


def test_read_targets_uses_example_when_missing_without_creating_runtime_copy(
    tmp_path, monkeypatch
):
    """read_targets() 可讀取範本，但不主動產生 targets.csv。"""
    (tmp_path / "targets.example.csv").write_text(
        "label,mz,rt_min,rt_max,ppm_tol,neutral_loss_da,nl_ppm_warn,nl_ppm_max\n"
        "ExA,258.1085,8.0,10.0,20,116.0474,20,50\n",
        encoding="utf-8-sig",
    )
    monkeypatch.setattr("gui.config_io.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("gui.config_io._BUNDLE_CONFIG", tmp_path)
    result = read_targets()
    assert result[0]["label"] == "ExA"
    assert not (tmp_path / "targets.csv").exists()


def test_write_settings_creates_runtime_copy_from_values_and_example_descriptions(
    tmp_path, monkeypatch
):
    (tmp_path / "settings.example.csv").write_text(
        "key,value,description\ndata_dir,/placeholder,資料目錄\n",
        encoding="utf-8-sig",
    )
    monkeypatch.setattr("gui.config_io.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("gui.config_io._BUNDLE_CONFIG", tmp_path)

    write_settings({"data_dir": "C:\\real"})

    rows = list(
        csv.DictReader((tmp_path / "settings.csv").open(encoding="utf-8-sig"))
    )
    assert rows == [
        {"key": "data_dir", "value": "C:\\real", "description": "資料目錄"}
    ]


def test_write_targets_creates_runtime_copy_only_on_save(tmp_path, monkeypatch):
    monkeypatch.setattr("gui.config_io.CONFIG_DIR", tmp_path)

    write_targets(
        [
            {
                "label": "ExA",
                "mz": "258.1085",
                "rt_min": "8.0",
                "rt_max": "10.0",
                "ppm_tol": "20",
                "neutral_loss_da": "116.0474",
                "nl_ppm_warn": "20",
                "nl_ppm_max": "50",
            }
        ]
    )

    assert (tmp_path / "targets.csv").exists()
    assert read_targets()[0]["label"] == "ExA"
