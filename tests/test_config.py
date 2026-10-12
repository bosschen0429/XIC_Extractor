import csv
from pathlib import Path

import pytest

from xic_extractor.configuration.csv_io import TARGET_WRITE_FIELDS, read_settings
from xic_extractor.configuration.hashing import (
    compute_config_hash,
    compute_target_config_hash,
)
from xic_extractor.configuration.loader import load_config
from xic_extractor.configuration.models import ConfigError
from xic_extractor.configuration.settings import fill_setting_defaults, resolve_settings
from xic_extractor.configuration.targeted_strategy import targeted_tag_profile
from xic_extractor.configuration.targets import (
    parse_target_rows,
    read_istd_targets,
    read_targets,
)
from xic_extractor.settings_schema import CANONICAL_SETTINGS_DEFAULTS


def _target_row(**overrides):
    return {
        "label": "ISTD",
        "mz": "258.1085",
        "expected_rt_min": "9",
        "cid_nl_tag": "dR",
        "role": "istd",
        "istd_pair": "",
        **overrides,
    }


def _write_valid_config(config_dir):
    config_dir.mkdir(parents=True, exist_ok=True)
    with (config_dir / "settings.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=("key", "value"))
        writer.writeheader()
        writer.writerows(
            {
                "key": key,
                "value": str(config_dir) if key in {"data_dir", "dll_dir"} else value,
            }
            for key, value in CANONICAL_SETTINGS_DEFAULTS.items()
        )
    with (config_dir / "targets.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=TARGET_WRITE_FIELDS)
        writer.writeheader()
        writer.writerow(_target_row())


def test_load_config_freezes_intent_hashes_and_fixed_strategy(tmp_path):
    _write_valid_config(tmp_path / "config")
    config, targets = load_config(tmp_path / "config")
    assert config.output_csv == tmp_path / "output" / "xic_results.csv"
    assert config.signal_processing_method == "gaussian"
    assert config.signal_processing_window_points == 15
    assert config.config_hash == compute_config_hash(
        tmp_path / "config/targets.csv", tmp_path / "config/settings.csv"
    )
    assert config.target_config_hash == compute_target_config_hash(
        tmp_path / "config/targets.csv"
    )
    assert targets[0].expected_rt_min == 9
    assert targets[0].cid_nl_tag == "dR"


def test_load_config_uses_examples(tmp_path):
    _write_valid_config(tmp_path)
    for name in ("settings", "targets"):
        (tmp_path / f"{name}.csv").rename(tmp_path / f"{name}.example.csv")
    assert load_config(tmp_path)[1][0].label == "ISTD"


@pytest.mark.parametrize("name", ["settings", "targets"])
def test_missing_input_is_actionable(tmp_path, name):
    _write_valid_config(tmp_path)
    (tmp_path / f"{name}.csv").unlink()
    with pytest.raises(ConfigError, match="missing"):
        load_config(tmp_path)


@pytest.mark.parametrize(
    "key",
    [
        "parallel_mode",
        "parallel_workers",
        "dirty_matrix_mode",
        "rolling_window_size",
        "nl_rt_anchor_search_margin_min",
        "nl_rt_anchor_half_window_min",
        "nl_fallback_half_window_min",
        "ms2_precursor_tol_da",
        "nl_min_intensity_ratio",
        "signal_processing_window_points",
        "unknown",
    ],
)
def test_settings_reject_all_hidden_overrides(key):
    with pytest.raises(ConfigError, match=key):
        resolve_settings({key: "1"})
    with pytest.raises(ConfigError, match=key):
        resolve_settings({}, {key: "1"})


def test_defaults_are_only_batch_and_machine():
    values, warnings = fill_setting_defaults({})
    assert values == CANONICAL_SETTINGS_DEFAULTS
    assert set(values) == {"data_dir", "dll_dir", "injection_order_source"}
    assert warnings == []


@pytest.mark.parametrize("tag", ["dR", "R", "MeR"])
def test_target_chemistry_resolves_from_shared_recipe(tag):
    target = parse_target_rows([_target_row(cid_nl_tag=tag)])[0]
    policy = targeted_tag_profile(tag)
    assert target.neutral_loss_da == policy.neutral_loss_profiles[0].neutral_loss_da
    assert target.ppm_tol == policy.precursor_mz_tolerance_ppm
    assert target.nl_ppm_max == policy.nl_tolerance_ppm


@pytest.mark.parametrize(
    "key,value",
    [
        ("mz", "nan"),
        ("mz", "0"),
        ("expected_rt_min", "-1"),
        ("expected_rt_min", "inf"),
        ("cid_nl_tag", "unknown"),
        ("role", "standard"),
        ("label", ""),
        ("sample_applicability", "invalid"),
    ],
)
def test_invalid_target_intent_is_rejected(key, value):
    with pytest.raises(ConfigError):
        parse_target_rows([_target_row(**{key: value})])


@pytest.mark.parametrize(
    "field",
    [
        "rt_min",
        "rt_max",
        "ppm_tol",
        "neutral_loss_da",
        "nl_ppm_warn",
        "nl_ppm_max",
        "is_istd",
    ],
)
def test_target_rejects_retired_fields_even_with_valid_intent(field):
    with pytest.raises(ConfigError, match=field):
        parse_target_rows([_target_row(**{field: "1"})])


def test_target_relationships_and_metadata():
    standard = _target_row()
    analyte = _target_row(
        label="analyte",
        role="analyte",
        istd_pair="ISTD",
        sample_applicability="rna_containing",
        note="retain",
    )
    parsed = parse_target_rows([standard, analyte])
    assert parsed[1].sample_applicability == "rna_containing"
    for bad in [
        dict(analyte, istd_pair=""),
        dict(analyte, istd_pair="missing"),
        dict(standard, istd_pair="ISTD"),
    ]:
        with pytest.raises(ConfigError):
            parse_target_rows([bad])
    with pytest.raises(ConfigError, match="unique"):
        parse_target_rows([standard, standard])


def test_templates_and_untargeted_istd_projection_share_new_format():
    root = Path(__file__).resolve().parents[1] / "config"
    assert set(read_settings(root / "settings.example.csv")) == set(
        CANONICAL_SETTINGS_DEFAULTS
    )
    targets = read_targets(root / "targets.example.csv")
    selected = read_istd_targets(root / "targets.example.csv")
    assert selected.targets == tuple(t for t in targets if t.is_istd)
    assert len(targets) == len(selected.targets) + selected.skipped_count
