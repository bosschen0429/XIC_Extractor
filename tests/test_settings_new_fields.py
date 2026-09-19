import csv
from pathlib import Path

from xic_extractor.config import (
    compute_config_hash,
    compute_target_config_hash,
    load_config,
)
from xic_extractor.settings_schema import (
    CANONICAL_SETTINGS_DEFAULTS,
    CANONICAL_SETTINGS_DESCRIPTIONS,
)
from xic_extractor.target_pair_rt_calibration import (
    TARGET_PAIR_RT_CALIBRATION_SCHEMA_VERSION,
    TargetPairRTCalibrationRow,
    write_target_pair_rt_calibration_tsv,
)

SETTINGS_FIELDS = ["key", "value", "description"]
TARGET_FIELDS = [
    "label",
    "mz",
    "rt_min",
    "rt_max",
    "ppm_tol",
    "neutral_loss_da",
    "nl_ppm_warn",
    "nl_ppm_max",
    "is_istd",
    "istd_pair",
]


def test_new_keys_present() -> None:
    for key, default in {
        "injection_order_source": "",
        "rolling_window_size": "5",
        "dirty_matrix_mode": "false",
        "rt_prior_library_path": "",
        "target_pair_rt_calibration_path": "",
        "emit_score_breakdown": "false",
        "emit_review_report": "false",
        "emit_peak_candidates": "false",
        "keep_intermediate_csv": "false",
        "model_selection_expected_diff_approval_registry": "",
        "targeted_ms1_shape_identity_support_tsv": "",
        "targeted_ms1_shape_identity_activation_policy": "limited_5hmdc_5medc",
        "ms1_morphology_smoothing_window_points": "15",
    }.items():
        assert CANONICAL_SETTINGS_DEFAULTS[key] == default


def test_new_settings_are_in_canonical_defaults_and_descriptions() -> None:
    for key in (
        "injection_order_source",
        "rolling_window_size",
        "dirty_matrix_mode",
        "rt_prior_library_path",
        "target_pair_rt_calibration_path",
        "emit_score_breakdown",
        "emit_review_report",
        "emit_peak_candidates",
        "keep_intermediate_csv",
        "model_selection_expected_diff_approval_registry",
        "targeted_ms1_shape_identity_support_tsv",
        "targeted_ms1_shape_identity_activation_policy",
        "ms1_morphology_smoothing_window_points",
    ):
        assert CANONICAL_SETTINGS_DESCRIPTIONS[key]
    assert "fallback" in CANONICAL_SETTINGS_DESCRIPTIONS["injection_order_source"]
    assert "RAW mtime" in CANONICAL_SETTINGS_DESCRIPTIONS["injection_order_source"]
    assert "developer/debug" in CANONICAL_SETTINGS_DESCRIPTIONS["rt_prior_library_path"]
    assert "leave empty" in CANONICAL_SETTINGS_DESCRIPTIONS["rt_prior_library_path"]
    assert "shadow auto-reselection" in CANONICAL_SETTINGS_DESCRIPTIONS[
        "target_pair_rt_calibration_path"
    ]
    assert "expected-diff" in CANONICAL_SETTINGS_DESCRIPTIONS[
        "model_selection_expected_diff_approval_registry"
    ]
    assert "targeted_ms1_shape_identity" in CANONICAL_SETTINGS_DESCRIPTIONS[
        "targeted_ms1_shape_identity_support_tsv"
    ]
    assert "limited_5hmdc_5medc" in CANONICAL_SETTINGS_DESCRIPTIONS[
        "targeted_ms1_shape_identity_activation_policy"
    ]
    assert "Gaussian15" in CANONICAL_SETTINGS_DESCRIPTIONS[
        "ms1_morphology_smoothing_window_points"
    ]


def test_load_config_parses_scoring_settings(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    data_dir.mkdir()
    dll_dir.mkdir()
    injection_order = tmp_path / "sample_info.csv"
    rt_library = tmp_path / "rt_prior_library.csv"
    calibration = tmp_path / "target_pair_rt_calibration.tsv"
    approval_registry = tmp_path / "expected_diff_approvals.tsv"
    shape_identity_support = tmp_path / "targeted_ms1_shape_identity.tsv"
    rows = {
        "data_dir": str(data_dir),
        "dll_dir": str(dll_dir),
        "smooth_window": "15",
        "smooth_polyorder": "3",
        "ms1_morphology_smoothing_window_points": "21",
        "peak_rel_height": "0.95",
        "peak_min_prominence_ratio": "0.10",
        "ms2_precursor_tol_da": "0.5",
        "nl_min_intensity_ratio": "0.01",
        "count_no_ms2_as_detected": "false",
        "injection_order_source": str(injection_order),
        "rolling_window_size": "7",
        "dirty_matrix_mode": "true",
        "rt_prior_library_path": str(rt_library),
        "target_pair_rt_calibration_path": str(calibration),
        "model_selection_expected_diff_approval_registry": str(approval_registry),
        "targeted_ms1_shape_identity_support_tsv": str(shape_identity_support),
        "targeted_ms1_shape_identity_activation_policy": "limited_5hmdc_5medc",
        "emit_score_breakdown": "true",
        "emit_review_report": "true",
        "emit_peak_candidates": "true",
        "keep_intermediate_csv": "true",
    }

    config_dir.mkdir()
    settings_path = config_dir / "settings.csv"
    with settings_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=SETTINGS_FIELDS)
        writer.writeheader()
        for key, value in rows.items():
            writer.writerow({"key": key, "value": value, "description": key})

    targets_path = config_dir / "targets.csv"
    with targets_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=TARGET_FIELDS)
        writer.writeheader()
        writer.writerow(
            {
                "label": "Analyte",
                "mz": "258.1085",
                "rt_min": "8.0",
                "rt_max": "10.0",
                "ppm_tol": "20",
                "neutral_loss_da": "116.0474",
                "nl_ppm_warn": "20",
                "nl_ppm_max": "50",
                "is_istd": "false",
                "istd_pair": "",
            }
        )
    _write_calibration(calibration, targets_path)

    config, _ = load_config(config_dir)

    assert config.injection_order_source == injection_order
    assert config.rolling_window_size == 7
    assert config.dirty_matrix_mode is True
    assert config.rt_prior_library_path == rt_library
    assert config.target_pair_rt_calibration_path == calibration
    assert config.model_selection_expected_diff_approval_registry == approval_registry
    assert config.targeted_ms1_shape_identity_support_tsv == shape_identity_support
    assert config.targeted_ms1_shape_identity_activation_policy == (
        "limited_5hmdc_5medc"
    )
    assert config.emit_score_breakdown is True
    assert config.emit_review_report is True
    assert config.emit_peak_candidates is True
    assert config.keep_intermediate_csv is True
    assert config.ms1_morphology_smoothing_window_points == 21
    assert config.config_hash == compute_config_hash(targets_path, settings_path)
    assert config.target_config_hash == compute_target_config_hash(targets_path)


def test_load_config_defaults_scoring_settings_for_legacy_settings_csv(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    data_dir.mkdir()
    dll_dir.mkdir()
    rows = {
        "data_dir": str(data_dir),
        "dll_dir": str(dll_dir),
        "smooth_window": "15",
        "smooth_polyorder": "3",
        "peak_rel_height": "0.95",
        "peak_min_prominence_ratio": "0.10",
        "ms2_precursor_tol_da": "0.5",
        "nl_min_intensity_ratio": "0.01",
        "count_no_ms2_as_detected": "false",
    }

    config_dir.mkdir()
    with (config_dir / "settings.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=SETTINGS_FIELDS)
        writer.writeheader()
        for key, value in rows.items():
            writer.writerow({"key": key, "value": value, "description": key})

    with (config_dir / "targets.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=TARGET_FIELDS)
        writer.writeheader()
        writer.writerow(
            {
                "label": "Analyte",
                "mz": "258.1085",
                "rt_min": "8.0",
                "rt_max": "10.0",
                "ppm_tol": "20",
                "neutral_loss_da": "116.0474",
                "nl_ppm_warn": "20",
                "nl_ppm_max": "50",
                "is_istd": "false",
                "istd_pair": "",
            }
        )

    config, _ = load_config(config_dir)

    assert config.injection_order_source is None
    assert config.rolling_window_size == 5
    assert config.dirty_matrix_mode is False
    assert config.rt_prior_library_path is None
    assert config.target_pair_rt_calibration_path is None
    assert config.model_selection_expected_diff_approval_registry is None
    assert config.targeted_ms1_shape_identity_support_tsv is None
    assert config.targeted_ms1_shape_identity_activation_policy == (
        "limited_5hmdc_5medc"
    )
    assert config.emit_score_breakdown is False
    assert config.emit_review_report is False
    assert config.emit_peak_candidates is False
    assert config.keep_intermediate_csv is False


def test_load_config_rejects_unknown_shape_identity_activation_policy(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    data_dir.mkdir()
    dll_dir.mkdir()
    rows = {
        **CANONICAL_SETTINGS_DEFAULTS,
        "data_dir": str(data_dir),
        "dll_dir": str(dll_dir),
        "targeted_ms1_shape_identity_activation_policy": "broad_default",
    }

    config_dir.mkdir()
    with (config_dir / "settings.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=SETTINGS_FIELDS)
        writer.writeheader()
        for key, value in rows.items():
            writer.writerow({"key": key, "value": value, "description": key})

    with (config_dir / "targets.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=TARGET_FIELDS)
        writer.writeheader()
        writer.writerow(
            {
                "label": "Analyte",
                "mz": "258.1085",
                "rt_min": "8.0",
                "rt_max": "10.0",
                "ppm_tol": "20",
                "neutral_loss_da": "116.0474",
                "nl_ppm_warn": "20",
                "nl_ppm_max": "50",
                "is_istd": "false",
                "istd_pair": "",
            }
        )

    try:
        load_config(config_dir)
    except Exception as exc:
        message = str(exc)
        assert "targeted_ms1_shape_identity_activation_policy" in message
        assert "explicit_support_tsv" in message
    else:
        raise AssertionError("unknown activation policy should fail config loading")


def _write_calibration(path: Path, targets_path: Path) -> None:
    write_target_pair_rt_calibration_tsv(
        path,
        [
            TargetPairRTCalibrationRow(
                schema_version=TARGET_PAIR_RT_CALIBRATION_SCHEMA_VERSION,
                target_config_hash=compute_target_config_hash(targets_path),
                source_artifact="mixstds.tsv",
                source_hash="sourcehash",
                source_hash_status="present",
                target_label="Analyte",
                paired_istd_label="ISTD",
                pair_rt_delta_min=0.25,
                delta_source="mixstds_clean_standard",
                point_count=6,
                rt_delta_median_min=0.25,
                rt_delta_mad_min=0.02,
                rt_delta_direction="target_later",
                isotope_label_type="deuterated",
                paired_rt_relation="istd_not_later_than_pair",
                calibration_status="usable",
                calibration_level="clean_standard_only",
                product_transfer_status="not_assessed",
            )
        ],
    )
