import csv

import pytest

from xic_extractor.configuration.csv_io import TARGET_FIELDS
from xic_extractor.configuration.hashing import compute_target_config_hash
from xic_extractor.configuration.loader import load_config_snapshot
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
        "method": "dR",
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
        writer = csv.DictWriter(handle, fieldnames=TARGET_FIELDS)
        writer.writeheader()
        writer.writerow(_target_row())


def test_load_config_snapshot_freezes_intent_hashes_and_fixed_strategy(tmp_path):
    _write_valid_config(tmp_path / "config")
    config, targets = load_config_snapshot(tmp_path / "config")
    assert config.output_csv == tmp_path / "output" / "xic_results.csv"
    assert config.signal_processing_method == "gaussian"
    assert config.signal_processing_window_points == 9
    assert config.target_config_hash == compute_target_config_hash(
        tmp_path / "config/targets.csv"
    )
    assert targets[0].expected_rt_min == 9
    assert targets[0].cid_nl_tag == "dR"


def test_snapshot_never_falls_back_to_examples(tmp_path):
    _write_valid_config(tmp_path)
    (tmp_path / "settings.csv").rename(tmp_path / "settings.example.csv")
    with pytest.raises(ConfigError, match="missing"):
        load_config_snapshot(tmp_path)


@pytest.mark.parametrize("name", ["settings", "targets"])
def test_missing_input_is_actionable(tmp_path, name):
    _write_valid_config(tmp_path)
    (tmp_path / f"{name}.csv").unlink()
    with pytest.raises(ConfigError, match="missing"):
        load_config_snapshot(tmp_path)


@pytest.mark.parametrize(
    "key",
    [
        "parallel_mode",
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
    assert set(values) == {
        "data_dir",
        "dll_dir",
        "injection_order_source",
        "parallel_workers",
    }
    assert warnings == []


@pytest.mark.parametrize("workers", ["", "2", "5"])
def test_targeted_and_discovery_use_system_budget_not_saved_worker_value(
    tmp_path, workers, monkeypatch
):
    from xic_extractor.configuration.execution_profile import load_execution_profile
    from xic_extractor.configuration.loader import prepare_targeted_config
    from xic_extractor.configuration.workspace_state import save_workspace
    from xic_workbench.config_io import read_settings as read_work_settings
    from xic_workbench.config_io import write_settings

    config_dir = tmp_path / "config"
    save_workspace(
        config_dir,
        settings={"data_dir": str(tmp_path), "dll_dir": str(tmp_path)},
        targets=[_target_row()],
    )
    write_settings({"parallel_workers": workers}, config_dir=config_dir)
    assert read_work_settings(config_dir=config_dir)["parallel_workers"] == ""
    monkeypatch.setattr(
        "xic_extractor.configuration.execution_profile.runtime_root", lambda: tmp_path
    )
    prepared = prepare_targeted_config(config_dir)
    try:
        assert (
            prepared.config.parallel_workers
            == load_execution_profile().parallel_workers
        )
        if workers:
            assert load_execution_profile(
                parallel_workers=int(workers)
            ).parallel_workers == int(workers)
    finally:
        prepared.cleanup()


@pytest.mark.parametrize("workers", ["0", "-1", "1.5", "bad"])
def test_targeted_rejects_invalid_worker_budget(workers):
    with pytest.raises(ConfigError, match="parallel_workers.*integer >= 1"):
        resolve_settings({"parallel_workers": workers})


@pytest.mark.parametrize("tag", ["dR", "R", "MeR"])
def test_target_chemistry_resolves_from_shared_recipe(tag):
    target = parse_target_rows([_target_row(method=tag)])[0]
    policy = targeted_tag_profile(tag)
    assert target.neutral_loss_da == policy.neutral_loss_profiles[0].neutral_loss_da
    assert target.ppm_tol == policy.precursor_mz_tolerance_ppm
    assert target.nl_ppm_max == policy.nl_tolerance_ppm


def test_blank_tag_admits_ms1_only_without_imposing_a_neutral_loss_mass():
    target = parse_target_rows([_target_row(mz="100", method=" ", role="analyte")])[0]
    assert target.cid_nl_tag == ""
    assert target.neutral_loss_da is None
    assert target.nl_ppm_warn is None and target.nl_ppm_max is None
    assert target.ppm_tol == 20.0
    assert target.expected_rt_min == target.rt_min == target.rt_max == 9.0
    assert not target.is_istd and target.istd_pair == ""


@pytest.mark.parametrize(
    "key,value",
    [
        ("mz", "nan"),
        ("mz", "0"),
        ("expected_rt_min", "-1"),
        ("expected_rt_min", "inf"),
        ("method", "unknown"),
        ("role", "standard"),
        ("label", ""),
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
        note="retain",
    )
    parsed = parse_target_rows([standard, analyte])
    assert parsed[1].istd_pair == "ISTD"
    unpaired = parse_target_rows([dict(analyte, istd_pair="")])
    assert unpaired[0].istd_pair == ""
    assert not unpaired[0].is_istd
    for bad in [
        dict(analyte, istd_pair="missing"),
        dict(standard, istd_pair="ISTD"),
    ]:
        with pytest.raises(ConfigError):
            parse_target_rows([bad])
    with pytest.raises(ConfigError, match="unique"):
        parse_target_rows([standard, standard])


@pytest.mark.parametrize(
    "name", ["targets.csv", "targets.xlsx", "targeted-method.xlsx"]
)
def test_templates_and_untargeted_istd_projection_share_new_format(name):
    from xic_extractor.configuration.paths import targets_template_path

    root = targets_template_path().parent
    targets = read_targets(root / "targets.csv")
    selected = read_istd_targets(root / name)
    assert selected.targets == tuple(t for t in targets if t.is_istd)
    assert len(targets) == len(selected.targets) + selected.skipped_count


@pytest.mark.parametrize(
    "losses,expected_tags,count",
    [
        ((116.0474,), {"dR"}, 6),
        ((132.0423,), {"R"}, 1),
        ((116.0474, 132.0423), {"dR", "R"}, 7),
        ((146.0579,), set(), 0),
    ],
)
def test_istd_selection_respects_method_chemistry(losses, expected_tags, count):
    from xic_extractor.configuration.cid_nl_profile import NeutralLossProfile
    from xic_extractor.configuration.paths import targets_template_path

    shared = read_istd_targets(targets_template_path())
    selected = shared.for_method(
        tuple(NeutralLossProfile(f"custom-{i}", loss) for i, loss in enumerate(losses))
    )
    assert len(shared.targets) == 7
    assert len(selected.targets) == count
    assert {t.cid_nl_tag for t in selected.targets} == expected_tags
    assert len(selected.method_skipped) == 7 - count
    assert selected.skipped_count == 7
    assert all(t.is_istd for t in selected.method_skipped)
