from pathlib import Path

import pytest

from xic_extractor.configuration import targeted_method_files as files
from xic_extractor.configuration.models import ConfigError
from xic_extractor.configuration.targets import read_targets
from xic_extractor.settings_schema import CANONICAL_SETTINGS_DEFAULTS, METHOD_KEYS


def rows():
    common = {
        "mz": "250",
        "rt_min": "1",
        "rt_max": "3",
        "ppm_tol": "5",
        "neutral_loss_da": "",
        "nl_ppm_warn": "",
        "nl_ppm_max": "",
        "is_istd": "false",
        "istd_pair": "",
    }
    return [
        {
            **common,
            "label": "standard",
            "is_istd": "true",
            "isotope_label_type": "deuterated",
            "note": "標準品",
        },
        {
            **common,
            "label": "analyte",
            "istd_pair": "standard",
            "paired_rt_relation": "istd_not_later_than_pair",
            "sample_applicability": "rna_containing",
            "note": "方法 A",
        },
    ]


def test_complete_method_roundtrip_without_batch_machine_or_resource_state(tmp_path):
    settings = {
        **CANONICAL_SETTINGS_DEFAULTS,
        "data_dir": "missing-batch",
        "dll_dir": "missing-dll",
        "nl_min_intensity_ratio": "0.07",
    }
    method = files.create_targeted_method(settings, rows())
    folder = tmp_path / "method-a"
    saved = files.save_targeted_method(method, folder)
    loaded = files.load_targeted_method(folder)
    assert (
        dict(loaded.settings)
        == dict(saved.settings)
        == {key: settings[key] for key in METHOD_KEYS}
    )
    assert loaded.targets == method.targets == read_targets(folder / "targets.csv")
    assert loaded.targets[1].istd_pair == "standard"
    assert loaded.targets[1].sample_applicability == "rna_containing"
    assert loaded.target_rows[1]["note"] == "方法 A"
    assert set(folder.iterdir()) == {folder / "settings.csv", folder / "targets.csv"}
    with pytest.raises(TypeError):
        loaded.settings["dirty_matrix_mode"] = "true"


def test_new_method_replaces_every_method_value_and_targets(tmp_path):
    folder = tmp_path / "method"
    a = files.create_targeted_method({"dirty_matrix_mode": "true"}, rows())
    files.save_targeted_method(a, folder)
    b_rows = [dict(rows()[0], label="other-standard")]
    b = files.create_targeted_method({}, b_rows)
    files.save_targeted_method(b, folder)
    loaded = files.load_targeted_method(folder)
    assert (
        loaded.settings["dirty_matrix_mode"]
        == CANONICAL_SETTINGS_DEFAULTS["dirty_matrix_mode"]
    )
    assert [target.label for target in loaded.targets] == ["other-standard"]


@pytest.mark.parametrize(
    "resource",
    [
        "rt_prior_library_path",
        "target_pair_rt_calibration_path",
        "model_selection_expected_diff_approval_registry",
        "targeted_ms1_shape_identity_support_tsv",
    ],
)
def test_portable_method_rejects_resources_instead_of_dropping_them(resource):
    settings = {resource: "reference.tsv"}
    with pytest.raises(ConfigError, match=resource):
        files.create_targeted_method(settings, rows())
    assert settings == {resource: "reference.tsv"}


def test_invalid_istd_relation_uses_existing_owner():
    invalid = rows()
    invalid[1]["istd_pair"] = "missing"
    with pytest.raises(ConfigError, match="must reference"):
        files.create_targeted_method({}, invalid)


def test_incomplete_or_non_method_settings_are_not_accepted(tmp_path):
    folder = tmp_path / "method"
    files.save_targeted_method(files.create_targeted_method({}, rows()), folder)
    settings_file = folder / "settings.csv"
    settings_file.write_text("key,value\ndirty_matrix_mode,true\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="缺少"):
        files.load_targeted_method(folder)
    with pytest.raises(ConfigError, match="尚未支援"):
        files.create_targeted_method({"new_science_policy": "on"}, rows())


def test_failed_whole_method_replacement_restores_original(tmp_path, monkeypatch):
    folder = tmp_path / "method"
    original = files.create_targeted_method({}, rows())
    files.save_targeted_method(original, folder)
    before = {
        name: (folder / name).read_bytes() for name in ("settings.csv", "targets.csv")
    }
    replacement = files.create_targeted_method({"dirty_matrix_mode": "true"}, rows())
    real_replace = files.os.replace

    def fail_publish(source, destination):
        if Path(destination) == folder and ".backup-" not in Path(source).name:
            raise OSError("publish failed")
        real_replace(source, destination)

    monkeypatch.setattr(files.os, "replace", fail_publish)
    with pytest.raises(OSError, match="publish failed"):
        files.save_targeted_method(replacement, folder)
    assert {name: (folder / name).read_bytes() for name in before} == before
    assert files.load_targeted_method(folder).targets == original.targets
    assert list(tmp_path.iterdir()) == [folder]


def test_saving_cannot_destroy_an_existing_workspace(tmp_path):
    folder = tmp_path / "workspace"
    folder.mkdir()
    unrelated = folder / "batch.csv"
    unrelated.write_text("keep", encoding="utf-8")
    with pytest.raises(ConfigError, match="僅可覆寫"):
        files.save_targeted_method(files.create_targeted_method({}, rows()), folder)
    assert unrelated.read_text(encoding="utf-8") == "keep"


def test_failed_second_csv_write_leaves_existing_method_unchanged(
    tmp_path, monkeypatch
):
    folder = tmp_path / "method"
    method = files.create_targeted_method({}, rows())
    files.save_targeted_method(method, folder)
    before = {path.name: path.read_bytes() for path in folder.iterdir()}
    real_write = files._write_csv

    def fail_targets(path, fields, table):
        if path.name == "targets.csv":
            raise OSError("targets write failed")
        real_write(path, fields, table)

    monkeypatch.setattr(files, "_write_csv", fail_targets)
    with pytest.raises(OSError, match="targets write failed"):
        files.save_targeted_method(method, folder)
    assert {path.name: path.read_bytes() for path in folder.iterdir()} == before
    assert list(tmp_path.iterdir()) == [folder]


def _workspace(tmp_path, *, settings=None):
    import csv

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()
    values = {
        **CANONICAL_SETTINGS_DEFAULTS,
        "data_dir": str(raw_dir),
        "dll_dir": str(dll_dir),
        **(settings or {}),
    }
    with (config_dir / "settings.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=("key", "value"))
        writer.writeheader()
        writer.writerows({"key": key, "value": value} for key, value in values.items())
    return config_dir


def test_prepared_method_receipt_binds_hash_and_survives_source_edits(tmp_path):
    from xic_extractor.configuration.hashing import compute_config_hash
    from xic_extractor.configuration.loader import (
        load_config,
        prepare_targeted_config,
    )
    from xic_extractor.output.method_manifest import (
        MethodManifestContext,
        load_method_manifest_for_replay,
        write_method_manifest,
    )

    config_dir = _workspace(tmp_path, settings={"dirty_matrix_mode": "true"})
    method_dir = tmp_path / "method"
    method = files.create_targeted_method({"dirty_matrix_mode": "false"}, rows())
    files.save_targeted_method(method, method_dir)
    prepared = prepare_targeted_config(config_dir, method_dir=method_dir)
    receipt = prepared.input_config_dir
    assert prepared.config.dirty_matrix_mode is False
    assert prepared.config.data_dir == tmp_path / "raw"
    assert prepared.config.dll_dir == tmp_path / "dll"
    assert prepared.config.config_hash == compute_config_hash(
        receipt / "targets.csv", receipt / "settings.csv"
    )
    manifest = write_method_manifest(
        prepared.config,
        prepared.targets,
        context=MethodManifestContext(
            entrypoint="xic-extractor-cli",
            base_dir=tmp_path,
            config_dir=receipt,
            settings_overrides={},
            output_mode="csv_only",
        ),
    )
    (method_dir / "targets.csv").write_text("changed", encoding="utf-8")
    (config_dir / "settings.csv").write_text("changed", encoding="utf-8")
    replay = load_method_manifest_for_replay(manifest)
    reloaded, targets = load_config(replay.config_dir, output_dir=manifest.parent)
    assert reloaded.config_hash == prepared.config.config_hash
    assert tuple(targets) == prepared.targets
    assert reloaded.output_csv == prepared.config.output_csv


def test_new_batch_does_not_inherit_injection_order_and_explicit_order_is_frozen(
    tmp_path,
):
    from xic_extractor.configuration.loader import prepare_targeted_config

    old_order = tmp_path / "old-order.csv"
    old_order.write_text("Sample_Name,Injection_Order\nold,1\n", encoding="utf-8")
    config_dir = _workspace(
        tmp_path, settings={"injection_order_source": str(old_order)}
    )
    method_dir = tmp_path / "method"
    files.save_targeted_method(files.create_targeted_method({}, rows()), method_dir)
    batch_two = tmp_path / "batch-two"
    batch_two.mkdir()
    with pytest.raises(ConfigError, match="不能沿用原注射順序"):
        prepare_targeted_config(
            config_dir,
            method_dir=method_dir,
            settings_overrides={"data_dir": str(batch_two)},
        )
    assert not (tmp_path / "output").exists()
    new_order = tmp_path / "new-order.csv"
    original = b"Sample_Name,Injection_Order\nnew,1\n"
    new_order.write_bytes(original)
    prepared = prepare_targeted_config(
        config_dir,
        method_dir=method_dir,
        settings_overrides={
            "data_dir": str(batch_two),
            "injection_order_source": str(new_order),
        },
    )
    new_order.write_text("changed", encoding="utf-8")
    assert prepared.config.data_dir == batch_two
    assert prepared.config.injection_order_source.read_bytes() == original
    assert prepared.config.injection_order_source.parent == prepared.input_config_dir


def test_preparation_rejects_workspace_and_explicit_resource_bindings(tmp_path):
    from xic_extractor.configuration.loader import prepare_targeted_config

    config_dir = _workspace(tmp_path)
    method_dir = tmp_path / "method"
    files.save_targeted_method(files.create_targeted_method({}, rows()), method_dir)
    with pytest.raises(ConfigError, match="targeted_ms1_shape_identity_support_tsv"):
        prepare_targeted_config(
            config_dir,
            method_dir=method_dir,
            settings_overrides={
                "targeted_ms1_shape_identity_support_tsv": "support.tsv"
            },
        )
    assert not (tmp_path / "output").exists()
    current = {**CANONICAL_SETTINGS_DEFAULTS, "rt_prior_library_path": "prior.csv"}
    with pytest.raises(ConfigError, match="rt_prior_library_path"):
        files.apply_targeted_method(current, files.create_targeted_method({}, rows()))


def test_preparation_rejects_retired_prior_before_freezing_inputs(
    tmp_path,
):
    from xic_extractor.configuration.loader import prepare_targeted_config

    order = tmp_path / "order.csv"
    order.write_text("Sample_Name,Injection_Order\nA,1\n", encoding="utf-8")
    config_dir = _workspace(
        tmp_path,
        settings={
            "injection_order_source": str(order),
            "rt_prior_library_path": "prior.csv",
        },
    )
    original = (config_dir / "settings.csv").read_bytes()
    with pytest.raises(ConfigError, match="rt_prior_library_path"):
        prepare_targeted_config(config_dir)
    assert (config_dir / "settings.csv").read_bytes() == original
    assert not (tmp_path / "output").exists()
