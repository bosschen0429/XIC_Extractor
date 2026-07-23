import csv
import json
from pathlib import Path

import pytest

from tools.diagnostics.alignment_decision_report_io import read_json, read_tsv


def test_read_tsv_requires_declared_columns(tmp_path: Path) -> None:
    path = tmp_path / "input.tsv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("feature_family_id",),
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerow({"feature_family_id": "FAM001"})

    with pytest.raises(ValueError, match="missing required columns: identity_decision"):
        read_tsv(path, required_columns=("feature_family_id", "identity_decision"))


def test_read_tsv_returns_fieldnames_and_rows(tmp_path: Path) -> None:
    path = tmp_path / "input.tsv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("feature_family_id", "identity_decision"),
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerow(
            {
                "feature_family_id": "FAM001",
                "identity_decision": "production_family",
            }
        )

    table = read_tsv(path, required_columns=("feature_family_id",))

    assert table.fieldnames == ("feature_family_id", "identity_decision")
    assert table.rows == (
        {
            "feature_family_id": "FAM001",
            "identity_decision": "production_family",
        },
    )


def test_read_tsv_rejects_duplicate_columns(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.tsv"
    path.write_text(
        "feature_family_id\tfeature_family_id\nFAM001\tFAM002\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate columns: feature_family_id"):
        read_tsv(path, required_columns=("feature_family_id",))


def test_read_tsv_rejects_blank_columns(tmp_path: Path) -> None:
    path = tmp_path / "blank.tsv"
    path.write_text(
        "feature_family_id\t\nFAM001\tignored\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="blank columns at positions: 2"):
        read_tsv(path, required_columns=("feature_family_id",))


def test_read_tsv_keeps_utf8_bom_visible_to_schema_validation(tmp_path: Path) -> None:
    path = tmp_path / "bom.tsv"
    path.write_text(
        "feature_family_id\tidentity_decision\nFAM001\tproduction_family\n",
        encoding="utf-8-sig",
    )

    with pytest.raises(
        ValueError,
        match="missing required columns: feature_family_id",
    ):
        read_tsv(path, required_columns=("feature_family_id",))


def test_read_tsv_preserves_current_ragged_row_representation(tmp_path: Path) -> None:
    path = tmp_path / "ragged.tsv"
    path.write_text(
        "feature_family_id\tidentity_decision\n"
        "FAM001\n"
        "FAM002\tproduction_family\textra\n",
        encoding="utf-8",
    )

    table = read_tsv(path, required_columns=("feature_family_id",))

    assert table.rows == (
        {
            "feature_family_id": "FAM001",
            "identity_decision": None,
        },
        {
            "feature_family_id": "FAM002",
            "identity_decision": "production_family",
            None: ["extra"],
        },
    )


def test_read_tsv_wraps_missing_file_error(tmp_path: Path) -> None:
    path = tmp_path / "missing.tsv"

    with pytest.raises(ValueError, match=r"could not read TSV: .*missing\.tsv"):
        read_tsv(path, required_columns=())


def test_read_json_requires_object_root(tmp_path: Path) -> None:
    path = tmp_path / "input.json"
    path.write_text(json.dumps(["not", "object"]), encoding="utf-8")

    with pytest.raises(ValueError, match="JSON root must be an object"):
        read_json(path)
