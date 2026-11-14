from __future__ import annotations

from pathlib import Path

import pytest

from xic_extractor.configuration.models import ConfigError


def test_config_round_trip_preserves_target_precision_and_hidden_metadata(tmp_path):
    from xic_workbench.config_io import read_targets, write_targets

    rows = [
        dict(
            label="standard",
            mz="258.108500",
            expected_rt_min="8.000",
            cid_nl_loss_da="116.0474",
            role="istd",
            istd_pair="",
            source_note="preserve me",
        )
    ]
    write_targets(rows, config_dir=tmp_path)
    restored = read_targets(config_dir=tmp_path)
    assert restored[0]["mz"] == "258.108500"
    assert restored[0]["expected_rt_min"] == "8.000"
    assert restored[0]["source_note"] == "preserve me"


def test_settings_round_trip_rejects_hidden_science_without_migrating(tmp_path):
    from xic_workbench.config_io import read_settings, write_settings

    settings = {
        "data_dir": "C:/RAW",
        "dll_dir": "C:/Thermo",
        "injection_order_source": "",
        "parallel_workers": "",
    }
    write_settings(settings, config_dir=tmp_path)
    with pytest.raises(ConfigError, match="dirty_matrix_mode"):
        write_settings({**settings, "dirty_matrix_mode": "true"}, config_dir=tmp_path)
    assert read_settings(config_dir=tmp_path) == settings
    (tmp_path / "settings.csv").write_text(
        "key,value\nold_unknown,keep-not-effective\n", encoding="utf-8"
    )
    write_settings(settings, config_dir=tmp_path)
    assert "old_unknown" in (tmp_path / "settings.csv").read_text()


def test_invalid_targets_leave_saved_input_unchanged(tmp_path):
    from xic_workbench.config_io import write_targets

    row = dict(
        label="standard",
        mz="258",
        expected_rt_min="8",
        cid_nl_loss_da="116.0474",
        role="istd",
        istd_pair="",
    )
    write_targets([row], config_dir=tmp_path)
    before = (tmp_path / "workspace.json").read_bytes()
    with pytest.raises(ConfigError):
        write_targets([{**row, "rt_min": "1"}], config_dir=tmp_path)
    assert (tmp_path / "workspace.json").read_bytes() == before


def test_failed_target_import_leaves_the_callers_rows_unchanged(tmp_path: Path) -> None:
    from xic_workbench.target_import import import_targets

    source = tmp_path / "malformed.csv"
    source.write_text("label,mz\nBroken\n", encoding="utf-8")
    current_rows = [{"label": "Keep", "mz": "250.100000"}]

    with pytest.raises(ConfigError, match="fewer values than columns"):
        import_targets(source)

    assert current_rows == [{"label": "Keep", "mz": "250.100000"}]


def test_target_import_entrypoint_preserves_draft_precision_and_hidden_metadata(
    tmp_path: Path,
) -> None:
    from xic_workbench.target_import import import_targets

    source = tmp_path / "targets.tsv"
    source.write_text(
        "label\tmz\tsource_note\nTarget A\t250.100000\tkeep me\n",
        encoding="utf-8",
    )

    assert import_targets(source) == [
        {
            "label": "Target A",
            "mz": "250.100000",
            "source_note": "keep me",
        }
    ]


def test_target_import_entrypoint_uses_explicit_targets_sheet(tmp_path):
    from tests.targeted_fixtures import targeted_rows
    from xic_extractor.configuration.target_list_files import (
        create_target_list,
        save_target_list,
    )
    from xic_workbench.target_import import import_targets

    source = tmp_path / "targets.xlsx"
    save_target_list(create_target_list(targeted_rows()), source)
    imported = import_targets(source)
    assert imported[0]["role"] == "istd"
    assert imported[1]["istd_pair"] == "standard"
    assert imported[1]["expected_rt_min"] == "2"


def test_excel_target_import_without_target_rows_is_rejected(tmp_path: Path) -> None:
    import openpyxl

    from xic_workbench.target_import import import_targets

    source = tmp_path / "no_targets.xlsx"
    workbook = openpyxl.Workbook()
    workbook.active.title = "Summary"
    workbook.save(source)
    workbook.close()

    with pytest.raises(ConfigError, match="需要 Targets 工作表"):
        import_targets(source)


def test_targeted_fields_are_batch_and_machine_only():
    from xic_workbench.targeted_config import setting_fields

    fields = {field["key"]: field for field in setting_fields()}
    assert set(fields) == {
        "data_dir",
        "dll_dir",
        "injection_order_source",
    }
    assert fields["data_dir"]["scope"] == "batch"
    assert fields["dll_dir"]["scope"] == "environment"


@pytest.mark.parametrize("extension", ["xlsx", "csv", "tsv"])
def test_uploaded_lists_read_targets_only(tmp_path, extension):
    import base64
    import csv

    from tests.targeted_fixtures import targeted_rows
    from xic_extractor.configuration import target_list_files as files
    from xic_workbench.target_import import preview_target_upload

    path = tmp_path / f"arbitrary-name.{extension}"
    if extension == "xlsx":
        files.save_target_list(files.create_target_list(targeted_rows()), path)
    else:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=list(
                    dict.fromkeys(key for row in targeted_rows() for key in row)
                ),
                delimiter="\t" if extension == "tsv" else ",",
            )
            writer.writeheader()
            writer.writerows(targeted_rows())
    result = preview_target_upload(
        path.name, base64.b64encode(path.read_bytes()).decode()
    )
    assert set(result) == {"targets", "delta_mass"}
    assert [row["label"] for row in result["targets"]] == ["standard", "analyte"]
    assert result["targets"][1]["istd_pair"] == "standard"


def test_uploaded_earlier_method_workbook_is_rejected(tmp_path):
    import base64

    import openpyxl

    from tests.targeted_fixtures import targeted_rows
    from xic_extractor.configuration.target_list_files import (
        create_target_list,
        save_target_list,
    )
    from xic_workbench.target_import import preview_target_upload

    path = tmp_path / "broken.xlsx"
    save_target_list(create_target_list(targeted_rows()), path)
    book = openpyxl.load_workbook(path)
    book.create_sheet("Method").append(["key", "value"])
    book.save(path)
    book.close()
    with pytest.raises(ConfigError, match="舊格式"):
        preview_target_upload(path.name, base64.b64encode(path.read_bytes()).decode())


@pytest.mark.parametrize(
    "name, content",
    [
        ("file.xls", "AA=="),
        ("file.xlsx", "!"),
        ("file.xlsx", "AA=="),
        ("file.csv", "YQ==" * 400000),
    ],
    ids=["extension", "base64", "workbook", "oversized"],
)
def test_upload_rejects_invalid_or_oversized_input(name, content):
    from xic_workbench.target_import import preview_target_upload

    with pytest.raises(ConfigError):
        preview_target_upload(name, content)
