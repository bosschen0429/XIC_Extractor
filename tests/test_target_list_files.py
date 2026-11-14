from pathlib import Path

import pytest

from tests.targeted_fixtures import targeted_rows
from xic_extractor.configuration import target_list_files as files
from xic_extractor.configuration.models import ConfigError
from xic_extractor.settings_schema import CANONICAL_SETTINGS_DEFAULTS


def test_editing_a_copy_leaves_the_shipped_example_unchanged(tmp_path):
    from xic_extractor.configuration.paths import targets_template_path

    example = targets_template_path()
    original = example.read_bytes()
    target_list = files.load_target_list(example)
    rows = [dict(row) for row in target_list.target_rows]
    rows[0]["expected_rt_min"] = "9.25"
    copy = tmp_path / "my-list.xlsx"
    files.save_target_list(files.create_target_list(rows), copy)
    assert files.load_target_list(copy).target_rows[0]["expected_rt_min"] == "9.25"
    assert example.read_bytes() == original


def test_written_list_explains_its_own_columns_in_header_notes(tmp_path):
    import openpyxl

    from xic_extractor.configuration.csv_io import TARGET_FIELDS
    from xic_extractor.configuration.delta_mass_table import DELTA_MASS_HEADERS
    from xic_extractor.configuration.paths import targets_template_path

    rows = [dict(row, delta_mass_mz="+1.0021") for row in targeted_rows()]
    written = tmp_path / "with-delta.xlsx"
    files.save_target_list(files.create_target_list(rows), written)
    # The shipped example must be written by the same writer, notes included.
    for path, sheets in [
        (written, {"Targets": TARGET_FIELDS, "Delta Mass": DELTA_MASS_HEADERS}),
        (targets_template_path(), {"Targets": TARGET_FIELDS}),
    ]:
        book = openpyxl.load_workbook(path)
        for sheet, owned in sheets.items():
            notes = {cell.value: cell.comment for cell in book[sheet][1]}
            assert all(notes[name] for name in owned), (path, sheet)
            # A user's own column is kept as written, without a note.
            assert not any(notes[name] for name in set(notes) - set(owned))


@pytest.mark.parametrize("loss", ["116.0474", ""])
def test_list_roundtrip_keeps_targets_and_extra_columns(tmp_path, loss):
    target_rows = [dict(row, cid_nl_loss_da=loss) for row in targeted_rows()]
    target_list = files.create_target_list(target_rows)
    path = tmp_path / "list-a.xlsx"
    files.save_target_list(target_list, path)
    loaded = files.load_target_list(path)
    assert loaded.targets == target_list.targets
    assert loaded.targets[1].istd_pair == "standard"
    assert loaded.target_rows[1]["note"] == "方法 A"
    assert loaded.target_rows[1]["cid_nl_loss_da"] == loss
    assert (loaded.targets[1].neutral_loss_da is None) == (loss == "")
    assert loaded.delta_mass is None
    with pytest.raises(TypeError):
        loaded.target_rows[0]["mz"] = "1"


def test_saving_replaces_the_whole_list(tmp_path):
    path = tmp_path / "list.xlsx"
    files.save_target_list(files.create_target_list(targeted_rows()), path)
    b_rows = [dict(targeted_rows()[0], label="other-standard")]
    files.save_target_list(files.create_target_list(b_rows), path)
    loaded = files.load_target_list(path)
    assert [target.label for target in loaded.targets] == ["other-standard"]


def _sheet(path, name, rows):
    import openpyxl

    book = openpyxl.load_workbook(path)
    sheet = book.create_sheet(name)
    for row in rows:
        sheet.append(row)
    book.save(path)
    book.close()


@pytest.mark.parametrize("retired", ["Method", "Identification Methods"])
def test_earlier_method_workbook_is_rejected_with_the_fix(tmp_path, retired):
    path = tmp_path / "old.xlsx"
    files.save_target_list(files.create_target_list(targeted_rows()), path)
    _sheet(path, retired, [["key", "value"], ["strategy", "cid-nl"]])
    with pytest.raises(ConfigError, match=f"舊格式.*{retired}.*刪除") as error:
        files.load_target_list(path)
    assert "Delta Mass Settings" in str(error.value)


def test_unknown_sheet_is_rejected(tmp_path):
    path = tmp_path / "list.xlsx"
    files.save_target_list(files.create_target_list(targeted_rows()), path)
    _sheet(path, "Notes", [["anything"]])
    with pytest.raises(ConfigError, match="Targets"):
        files.load_target_list(path)


def test_shared_delta_mass_settings_roundtrip_in_their_own_sheet(tmp_path):
    import openpyxl

    rows = targeted_rows()
    rows[0]["delta_mass_mz"] = "+1.0034"
    payload = {
        "settings": {"mz_tolerance_ppm": 7.5},
        "ions": {
            rows[0]["label"]: [
                {
                    "label": "M+1",
                    "offset_mz": 1.0034,
                    "ratio_min": 0.2,
                    "ratio_max": 5.0,
                    "compare_shape": True,
                }
            ]
        },
    }
    path = tmp_path / "delta.xlsx"
    files.save_target_list(files.create_target_list(rows, delta_mass=payload), path)
    book = openpyxl.load_workbook(path, read_only=True)
    try:
        assert book.sheetnames == ["Targets", "Delta Mass", "Delta Mass Settings"]
    finally:
        book.close()
    loaded = files.load_target_list(path)
    assert loaded.delta_mass["settings"]["mz_tolerance_ppm"] == 7.5
    assert loaded.delta_mass["ions"][rows[0]["label"]][0]["label"] == "M+1"


def test_unknown_delta_mass_setting_is_rejected(tmp_path):
    path = tmp_path / "list.xlsx"
    files.save_target_list(files.create_target_list(targeted_rows()), path)
    _sheet(path, "Delta Mass Settings", [["key", "value"], ["strategy", "cid-nl"]])
    with pytest.raises(ConfigError, match="不支援的設定 'strategy'"):
        files.load_target_list(path)


def test_invalid_istd_relation_uses_existing_owner():
    invalid = targeted_rows()
    invalid[1]["istd_pair"] = "missing"
    with pytest.raises(ConfigError, match="must reference"):
        files.create_target_list(invalid)


def test_failed_replacement_restores_original(tmp_path, monkeypatch):
    folder = tmp_path / "list.xlsx"
    original = files.create_target_list(targeted_rows())
    files.save_target_list(original, folder)
    before = folder.read_bytes()
    replacement = files.create_target_list(targeted_rows())
    real_replace = files.os.replace

    def fail_publish(source, destination):
        if Path(destination) == folder:
            raise OSError("publish failed")
        real_replace(source, destination)

    monkeypatch.setattr(files.os, "replace", fail_publish)
    with pytest.raises(OSError, match="publish failed"):
        files.save_target_list(replacement, folder)
    assert folder.read_bytes() == before
    assert files.load_target_list(folder).targets == original.targets
    assert list(tmp_path.iterdir()) == [folder]


def test_saving_cannot_destroy_an_existing_workspace(tmp_path):
    folder = tmp_path / "workspace"
    folder.mkdir()
    unrelated = folder / "batch.csv"
    unrelated.write_text("keep", encoding="utf-8")
    with pytest.raises(ConfigError, match="僅可寫入"):
        files.save_target_list(files.create_target_list(targeted_rows()), folder)
    assert unrelated.read_text(encoding="utf-8") == "keep"


def test_failed_workbook_write_leaves_existing_list_unchanged(tmp_path, monkeypatch):
    import openpyxl

    path = tmp_path / "list.xlsx"
    target_list = files.create_target_list(targeted_rows())
    files.save_target_list(target_list, path)
    before = path.read_bytes()

    def fail_save(*args):
        raise OSError("workbook write failed")

    monkeypatch.setattr(openpyxl.Workbook, "save", fail_save)
    with pytest.raises(OSError, match="write failed"):
        files.save_target_list(target_list, path)
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


def test_prepared_list_receipt_survives_source_edits(tmp_path):
    from xic_extractor.configuration.loader import (
        load_config_snapshot,
        prepare_targeted_config,
    )

    config_dir = _workspace(tmp_path, settings={})
    list_path = tmp_path / "list.xlsx"
    target_list = files.create_target_list(targeted_rows())
    files.save_target_list(target_list, list_path)
    prepared = prepare_targeted_config(config_dir, target_list_path=list_path)
    receipt = prepared.input_config_dir
    assert prepared.config.signal_processing_method == "gaussian"
    assert prepared.config.data_dir == tmp_path / "raw"
    assert prepared.config.dll_dir == tmp_path / "dll"
    list_path.write_text("changed", encoding="utf-8")
    (config_dir / "workspace.json").write_text("changed", encoding="utf-8")
    output_dir = prepared.config.output_csv.parent
    reloaded, targets = load_config_snapshot(receipt, output_dir=output_dir)
    assert reloaded.target_config_hash == prepared.config.target_config_hash
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
    list_path = tmp_path / "list.xlsx"
    files.save_target_list(files.create_target_list(targeted_rows()), list_path)
    batch_two = tmp_path / "batch-two"
    batch_two.mkdir()
    with pytest.raises(ConfigError, match="不能沿用原注射順序"):
        prepare_targeted_config(
            config_dir,
            target_list_path=list_path,
            settings_overrides={"data_dir": str(batch_two)},
        )
    assert not (tmp_path / "output").exists()
    new_order = tmp_path / "new-order.csv"
    original = b"Sample_Name,Injection_Order\nnew,1\n"
    new_order.write_bytes(original)
    prepared = prepare_targeted_config(
        config_dir,
        target_list_path=list_path,
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

    path = tmp_path / "list.xlsx"
    files.save_target_list(files.create_target_list(targeted_rows()), path)
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
        files.load_target_list(path)


def test_target_table_preserves_precision_text_and_metadata(tmp_path):
    table = targeted_rows()
    table[0]["mz"] = "250.1234567890123456789"
    table[0]["note"] = "=not a formula"
    path = tmp_path / "targets.xlsx"
    files.save_target_list(files.create_target_list(table), path)
    loaded = files.load_target_list(path).target_rows
    assert loaded[0]["mz"] == table[0]["mz"]
    assert loaded[0]["note"] == "=not a formula"
    assert loaded[1]["paired_rt_relation"] == table[1]["paired_rt_relation"]


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

    path = tmp_path / "list.xlsx"
    files.save_target_list(files.create_target_list(targeted_rows()), path)
    book = openpyxl.load_workbook(path)
    sheet = book["Targets"]
    sheet.cell(1, sheet.max_column + 1, field)
    book.save(path)
    book.close()
    with pytest.raises(ConfigError, match=field):
        files.load_target_list(path)
