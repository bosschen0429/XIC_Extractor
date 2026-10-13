from pathlib import Path

import pytest

from xic_extractor.configuration import targeted_method_files as files
from xic_extractor.configuration.models import ConfigError
from xic_extractor.settings_schema import CANONICAL_SETTINGS_DEFAULTS, METHOD_KEYS


def test_shipped_examples_use_current_method_defaults_and_shared_targets(tmp_path):
    from xic_extractor.configuration.csv_io import read_target_draft_rows
    from xic_extractor.configuration.paths import targets_template_path

    resources = targets_template_path().parent
    method_path = resources / "targeted-method.xlsx"
    targets_path = resources / "targets.xlsx"
    originals = [path.read_bytes() for path in (method_path, targets_path)]
    method = files.load_targeted_method(method_path)
    target_rows = files.load_target_table(targets_path)
    assert dict(method.settings) == {key: "cid-nl" for key in METHOD_KEYS}
    assert (
        list(method.target_rows)
        == target_rows
        == read_target_draft_rows(targets_template_path())
    )
    target_rows[0]["expected_rt_min"] = "9.25"
    changed = files.create_targeted_method(method.settings, target_rows)
    copy = tmp_path / "my-method.xlsx"
    files.save_targeted_method(changed, copy)
    assert files.load_targeted_method(copy).target_rows[0]["expected_rt_min"] == "9.25"
    assert [path.read_bytes() for path in (method_path, targets_path)] == originals


def rows():
    common = {
        "mz": "250",
        "expected_rt_min": "2",
        "cid_nl_tag": "dR",
        "role": "analyte",
        "istd_pair": "",
    }
    return [
        {
            **common,
            "label": "standard",
            "role": "istd",
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
    }
    method = files.create_targeted_method(settings, rows())
    folder = tmp_path / "method-a.xlsx"
    saved = files.save_targeted_method(method, folder)
    loaded = files.load_targeted_method(folder)
    assert dict(loaded.settings) == dict(saved.settings) == {"strategy": "cid-nl"}
    assert loaded.targets == method.targets
    assert loaded.targets[1].istd_pair == "standard"
    assert loaded.targets[1].sample_applicability == "rna_containing"
    assert loaded.target_rows[1]["note"] == "方法 A"
    assert folder.is_file()
    with pytest.raises(TypeError):
        loaded.settings["dirty_matrix_mode"] = "true"


def test_new_method_replaces_every_method_value_and_targets(tmp_path):
    folder = tmp_path / "method.xlsx"
    a = files.create_targeted_method({}, rows())
    files.save_targeted_method(a, folder)
    b_rows = [dict(rows()[0], label="other-standard")]
    b = files.create_targeted_method({}, b_rows)
    files.save_targeted_method(b, folder)
    loaded = files.load_targeted_method(folder)
    assert loaded.settings["strategy"] == "cid-nl"
    assert [target.label for target in loaded.targets] == ["other-standard"]


@pytest.mark.parametrize(
    "resource",
    [
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
    folder = tmp_path / "method.xlsx"
    files.save_targeted_method(files.create_targeted_method({}, rows()), folder)
    import openpyxl

    book = openpyxl.load_workbook(folder)
    book["Method"].delete_rows(2)
    book.save(folder)
    book.close()
    with pytest.raises(ConfigError, match="缺少"):
        files.load_targeted_method(folder)
    with pytest.raises(ConfigError, match="unknown"):
        files.create_targeted_method({"new_science_policy": "on"}, rows())


def test_failed_whole_method_replacement_restores_original(tmp_path, monkeypatch):
    folder = tmp_path / "method.xlsx"
    original = files.create_targeted_method({}, rows())
    files.save_targeted_method(original, folder)
    before = folder.read_bytes()
    replacement = files.create_targeted_method({}, rows())
    real_replace = files.os.replace

    def fail_publish(source, destination):
        if Path(destination) == folder:
            raise OSError("publish failed")
        real_replace(source, destination)

    monkeypatch.setattr(files.os, "replace", fail_publish)
    with pytest.raises(OSError, match="publish failed"):
        files.save_targeted_method(replacement, folder)
    assert folder.read_bytes() == before
    assert files.load_targeted_method(folder).targets == original.targets
    assert list(tmp_path.iterdir()) == [folder]


def test_saving_cannot_destroy_an_existing_workspace(tmp_path):
    folder = tmp_path / "workspace"
    folder.mkdir()
    unrelated = folder / "batch.csv"
    unrelated.write_text("keep", encoding="utf-8")
    with pytest.raises(ConfigError, match="僅可寫入"):
        files.save_targeted_method(files.create_targeted_method({}, rows()), folder)
    assert unrelated.read_text(encoding="utf-8") == "keep"


def test_failed_workbook_write_leaves_existing_method_unchanged(tmp_path, monkeypatch):
    import openpyxl

    path = tmp_path / "method.xlsx"
    method = files.create_targeted_method({}, rows())
    files.save_targeted_method(method, path)
    before = path.read_bytes()

    def fail_save(*args):
        raise OSError("workbook write failed")

    monkeypatch.setattr(openpyxl.Workbook, "save", fail_save)
    with pytest.raises(OSError, match="write failed"):
        files.save_targeted_method(method, path)
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


def _workspace(tmp_path, *, settings=None):
    from xic_extractor.configuration.workspace_state import save_workspace

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
    save_workspace(config_dir, settings=values)
    return config_dir


def test_prepared_method_receipt_binds_hash_and_survives_source_edits(tmp_path):
    from xic_extractor.configuration.hashing import compute_config_hash
    from xic_extractor.configuration.loader import (
        load_config_snapshot,
        prepare_targeted_config,
    )

    config_dir = _workspace(tmp_path, settings={})
    method_dir = tmp_path / "method.xlsx"
    method = files.create_targeted_method({}, rows())
    files.save_targeted_method(method, method_dir)
    prepared = prepare_targeted_config(config_dir, method_path=method_dir)
    receipt = prepared.input_config_dir
    assert prepared.config.signal_processing_method == "gaussian"
    assert prepared.config.data_dir == tmp_path / "raw"
    assert prepared.config.dll_dir == tmp_path / "dll"
    assert prepared.config.config_hash == compute_config_hash(
        receipt / "targets.csv", receipt / "settings.csv"
    )
    method_dir.write_text("changed", encoding="utf-8")
    (config_dir / "workspace.json").write_text("changed", encoding="utf-8")
    output_dir = prepared.config.output_csv.parent
    reloaded, targets = load_config_snapshot(receipt, output_dir=output_dir)
    assert reloaded.config_hash == prepared.config.config_hash
    assert tuple(targets) == prepared.targets
    prepared.cleanup()
    assert not receipt.exists()


def test_new_batch_does_not_inherit_injection_order_and_explicit_order_is_frozen(
    tmp_path,
):
    from xic_extractor.configuration.loader import prepare_targeted_config

    old_order = tmp_path / "old-order.csv"
    old_order.write_text("Sample_Name,Injection_Order\nold,1\n", encoding="utf-8")
    config_dir = _workspace(
        tmp_path, settings={"injection_order_source": str(old_order)}
    )
    method_dir = tmp_path / "method.xlsx"
    files.save_targeted_method(files.create_targeted_method({}, rows()), method_dir)
    batch_two = tmp_path / "batch-two"
    batch_two.mkdir()
    with pytest.raises(ConfigError, match="不能沿用原注射順序"):
        prepare_targeted_config(
            config_dir,
            method_path=method_dir,
            settings_overrides={"data_dir": str(batch_two)},
        )
    assert not (tmp_path / "output").exists()
    new_order = tmp_path / "new-order.csv"
    original = b"Sample_Name,Injection_Order\nnew,1\n"
    new_order.write_bytes(original)
    prepared = prepare_targeted_config(
        config_dir,
        method_path=method_dir,
        settings_overrides={
            "data_dir": str(batch_two),
            "injection_order_source": str(new_order),
        },
    )
    new_order.write_text("changed", encoding="utf-8")
    assert prepared.config.data_dir == batch_two
    assert prepared.config.injection_order_source.read_bytes() == original
    assert prepared.config.injection_order_source.parent == prepared.input_config_dir


@pytest.mark.parametrize("damage", ["formula", "duplicate", "missing", "bad_pair"])
def test_workbook_rejects_lossy_or_invalid_input(tmp_path, damage):
    import openpyxl

    path = tmp_path / "method.xlsx"
    files.save_targeted_method(files.create_targeted_method({}, rows()), path)
    book = openpyxl.load_workbook(path)
    sheet = book["Targets"]
    headers = [cell.value for cell in sheet[1]]
    if damage == "formula":
        sheet.cell(2, headers.index("mz") + 1, "=250+1")
    elif damage == "duplicate":
        sheet.cell(1, 2, "label")
    elif damage == "missing":
        sheet.cell(1, 2, "not_mz")
    else:
        sheet.cell(3, headers.index("istd_pair") + 1, "missing")
    book.save(path)
    book.close()
    with pytest.raises(ConfigError):
        files.load_targeted_method(path)


def test_target_table_preserves_precision_text_and_metadata(tmp_path):
    table = rows()
    table[0]["mz"] = "250.1234567890123456789"
    table[0]["note"] = "=not a formula"
    path = tmp_path / "targets.xlsx"
    files.save_target_table(table, path)
    loaded = files.load_target_table(path)
    assert loaded[0]["mz"] == table[0]["mz"]
    assert loaded[0]["note"] == "=not a formula"
    assert loaded[1]["paired_rt_relation"] == table[1]["paired_rt_relation"]
    assert loaded[1]["sample_applicability"] == "rna_containing"


@pytest.mark.parametrize(
    "setting",
    [{"count_no_ms2_as_detected": "true"}, {"signal_processing_method": "savgol"}],
)
def test_fixed_policy_cannot_be_silently_dropped_on_method_save(setting):
    with pytest.raises(ConfigError, match="Retired or unknown"):
        files.create_targeted_method(setting, rows())


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
def test_workbook_rejects_retired_target_fields(tmp_path, field):
    import openpyxl

    path = tmp_path / "method.xlsx"
    files.save_targeted_method(files.create_targeted_method({}, rows()), path)
    book = openpyxl.load_workbook(path)
    sheet = book["Targets"]
    sheet.cell(1, sheet.max_column + 1, field)
    book.save(path)
    book.close()
    with pytest.raises(ConfigError, match=field):
        files.load_targeted_method(path)


@pytest.mark.parametrize(
    "key,value",
    [("strategy", "legacy"), ("ms2_precursor_tol_da", "0.75"), ("dll_dir", "local")],
)
def test_method_sheet_rejects_retired_or_nonmethod_settings(tmp_path, key, value):
    import openpyxl

    path = tmp_path / "method.xlsx"
    files.save_targeted_method(files.create_targeted_method({}, rows()), path)
    book = openpyxl.load_workbook(path)
    book["Method"]["A2"] = key
    book["Method"]["B2"] = value
    book.save(path)
    book.close()
    with pytest.raises(ConfigError):
        files.load_targeted_method(path)
