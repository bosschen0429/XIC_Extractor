import csv
import hashlib
from pathlib import Path

import pytest

from xic_extractor.tabular_io import (
    bool_value,
    file_sha256,
    format_diagnostic_value,
    format_spreadsheet_value,
    has_semicolon_token,
    identity_family_keys,
    numeric_equal,
    optional_float,
    optional_int,
    positive_int,
    read_delimited_rows,
    read_tsv_required,
    read_tsv_with_header,
    render_delimited_rows,
    required_indexes,
    rows_by_text_field,
    split_semicolon_labels,
    text_value,
    write_delimited_rows,
    write_tsv,
)


def test_spreadsheet_format_preserves_typed_numbers_and_empty_values() -> None:
    row = {
        "label": "=1+1",
        "count": -2,
        "offset": -0.123456789,
        "optional": None,
        "enabled": False,
    }
    text = render_delimited_rows(
        [row], tuple(row), delimiter="\t", formatter=format_spreadsheet_value
    )
    assert list(csv.DictReader(text.splitlines(), delimiter="\t")) == [
        {
            "label": "'=1+1",
            "count": "-2",
            "offset": "-0.123456789",
            "optional": "",
            "enabled": "False",
        }
    ]


def test_read_delimited_rows_fails_with_clear_missing_columns(
    tmp_path: Path,
) -> None:
    path = tmp_path / "rows.tsv"
    path.write_text("family_id\nFAM001\n", encoding="utf-8")

    with pytest.raises(ValueError, match="missing required columns: sample"):
        read_delimited_rows(path, required_columns=("family_id", "sample"))


@pytest.mark.parametrize(
    "header",
    (
        "family_id\tfamily_id\tsample\n",
        "family_id\tsample\tsample\n",
        "family_id\t\tsample\n",
        "family_id\t   \tsample\n",
    ),
)
def test_read_delimited_rows_rejects_duplicate_or_blank_headers(
    tmp_path: Path,
    header: str,
) -> None:
    path = tmp_path / "rows.tsv"
    path.write_text(header + "FAM001\tS1\tS1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate|blank"):
        read_delimited_rows(path, required_columns=("family_id", "sample"))


@pytest.mark.parametrize(
    ("header", "message"),
    (
        ("family_id\tsample\textra\n", "unexpected"),
        ("sample\tfamily_id\n", "column order differs"),
    ),
)
def test_read_delimited_rows_can_require_an_exact_versioned_schema(
    tmp_path: Path,
    header: str,
    message: str,
) -> None:
    path = tmp_path / "sealed.tsv"
    path.write_text(header + "FAM001\tS1\tvalue\n", encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        read_delimited_rows(
            path,
            required_columns=("family_id", "sample"),
            exact_columns=("family_id", "sample"),
        )


@pytest.mark.parametrize(
    ("data_row", "message"),
    (
        ("FAM001\n", "fewer cells"),
        ("FAM001\tS1\textra\n", "more cells"),
    ),
)
def test_exact_schema_rejects_truncated_or_overlong_rows(
    tmp_path: Path,
    data_row: str,
    message: str,
) -> None:
    path = tmp_path / "sealed.tsv"
    path.write_text("family_id\tsample\n" + data_row, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        read_delimited_rows(
            path,
            required_columns=("family_id", "sample"),
            exact_columns=("family_id", "sample"),
        )


@pytest.mark.parametrize("data_row", ("FAM001\n", "FAM001\tS1\textra\n"))
def test_subset_schema_still_rejects_ragged_rows(
    tmp_path: Path,
    data_row: str,
) -> None:
    path = tmp_path / "rows.tsv"
    path.write_text("family_id\tsample\n" + data_row, encoding="utf-8")

    with pytest.raises(ValueError, match="declared header"):
        read_delimited_rows(
            path,
            required_columns=("family_id",),
        )


def test_subset_schema_can_explicitly_pad_allowed_trailing_column(
    tmp_path: Path,
) -> None:
    path = tmp_path / "rows.tsv"
    path.write_text(
        "family_id\tsample\toptional_note\nFAM001\tS1\n",
        encoding="utf-8",
    )

    rows = read_delimited_rows(
        path,
        required_columns=("family_id", "sample", "optional_note"),
        allow_missing_trailing_columns=("optional_note",),
    )

    assert rows == [
        {"family_id": "FAM001", "sample": "S1", "optional_note": ""}
    ]


def test_trailing_padding_still_rejects_missing_required_cell(
    tmp_path: Path,
) -> None:
    path = tmp_path / "rows.tsv"
    path.write_text(
        "family_id\tsample\toptional_note\nFAM001\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="fewer cells"):
        read_delimited_rows(
            path,
            required_columns=("family_id", "sample", "optional_note"),
            allow_missing_trailing_columns=("optional_note",),
        )


def test_write_tsv_preserves_field_order_and_formats_values(tmp_path: Path) -> None:
    path = tmp_path / "rows.tsv"

    write_tsv(
        path,
        (
            {
                "family_id": "FAM001",
                "present": True,
                "missing": None,
                "score": 1.23456789,
            },
        ),
        ("family_id", "present", "missing", "score"),
    )

    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle, delimiter="\t"))
    assert rows == [
        ["family_id", "present", "missing", "score"],
        ["FAM001", "TRUE", "", "1.23457"],
    ]


def test_write_tsv_can_preserve_lf_line_terminator(tmp_path: Path) -> None:
    path = tmp_path / "rows.tsv"

    write_tsv(
        path,
        ({"family_id": "FAM001"},),
        ("family_id",),
        lineterminator="\n",
    )

    assert path.read_bytes() == b"family_id\nFAM001\n"


def test_write_tsv_can_preserve_utf8_sig_encoding(tmp_path: Path) -> None:
    path = tmp_path / "rows.tsv"

    write_tsv(
        path,
        ({"family_id": "FAM001"},),
        ("family_id",),
        encoding="utf-8-sig",
    )

    assert path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert path.read_text(encoding="utf-8-sig").splitlines() == [
        "family_id",
        "FAM001",
    ]


def test_write_tsv_can_raise_on_extra_fields(tmp_path: Path) -> None:
    path = tmp_path / "rows.tsv"

    with pytest.raises(ValueError, match="dict contains fields not in fieldnames"):
        write_tsv(
            path,
            ({"family_id": "FAM001", "unexpected": "value"},),
            ("family_id",),
            extrasaction="raise",
        )


def test_write_delimited_rows_supports_csv_output(tmp_path: Path) -> None:
    path = tmp_path / "rows.csv"

    write_delimited_rows(path, ({"sample": "S1", "value": 2.0},), ("sample", "value"))

    assert path.read_text(encoding="utf-8").splitlines() == [
        "sample,value",
        "S1,2",
    ]


def test_render_delimited_rows_uses_same_default_formatting() -> None:
    text = render_delimited_rows(
        (
            {
                "sample": "S1",
                "present": True,
                "missing": None,
                "score": 1.23456789,
            },
        ),
        ("sample", "present", "missing", "score"),
    )

    assert text == "sample,present,missing,score\r\nS1,TRUE,,1.23457\r\n"


def test_render_delimited_rows_can_preserve_lf_line_terminator() -> None:
    text = render_delimited_rows(
        ({"sample": "S1", "value": 2.0},),
        ("sample", "value"),
        delimiter="\t",
        lineterminator="\n",
    )

    assert text == "sample\tvalue\nS1\t2\n"


def test_render_delimited_rows_can_raise_on_extra_fields() -> None:
    with pytest.raises(ValueError, match="dict contains fields not in fieldnames"):
        render_delimited_rows(
            ({"sample": "S1", "unexpected": "value"},),
            ("sample",),
            extrasaction="raise",
        )


def test_format_diagnostic_value_hides_nan() -> None:
    assert format_diagnostic_value(float("nan")) == ""


def test_read_tsv_required_accepts_utf8_sig_and_validates_columns(
    tmp_path: Path,
) -> None:
    path = tmp_path / "rows.tsv"
    path.write_text(
        "\ufeffsample\ttarget\tvalue\nS1\tA\t1.2\n",
        encoding="utf-8",
    )

    assert read_tsv_required(path, ("sample", "target")) == (
        {"sample": "S1", "target": "A", "value": "1.2"},
    )

    with pytest.raises(ValueError, match="missing required columns: missing"):
        read_tsv_required(path, ("sample", "missing"))


def test_read_tsv_with_header_returns_header_and_rows(tmp_path: Path) -> None:
    path = tmp_path / "matrix.tsv"
    path.write_text("Mz\tRT\tS1\n100\t5\t42\n", encoding="utf-8")

    header, rows = read_tsv_with_header(path)

    assert header == ("Mz", "RT", "S1")
    assert rows == [{"Mz": "100", "RT": "5", "S1": "42"}]


def test_read_tsv_with_header_validates_required_columns_and_utf8_sig(
    tmp_path: Path,
) -> None:
    path = tmp_path / "evidence.tsv"
    path.write_text("\ufeffschema_version\trow_id\nv1\tR1\n", encoding="utf-8")

    header, rows = read_tsv_with_header(
        path,
        required_columns=("schema_version", "row_id"),
        encoding="utf-8-sig",
    )

    assert header == ("schema_version", "row_id")
    assert rows == [{"schema_version": "v1", "row_id": "R1"}]
    with pytest.raises(ValueError, match="missing required columns: missing"):
        read_tsv_with_header(
            path,
            required_columns=("schema_version", "missing"),
            encoding="utf-8-sig",
        )


def test_read_tsv_with_header_rejects_duplicate_optional_column(
    tmp_path: Path,
) -> None:
    path = tmp_path / "evidence.tsv"
    path.write_text(
        "schema_version\trow_id\tnote\tnote\nv1\tR1\tfirst\tsecond\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate columns: note"):
        read_tsv_with_header(
            path,
            required_columns=("schema_version", "row_id"),
        )


def test_file_sha256_supports_uppercase_and_lowercase_metadata(
    tmp_path: Path,
) -> None:
    path = tmp_path / "artifact.tsv"
    path.write_text("sample\tvalue\nS1\t1\n", encoding="utf-8")
    expected = hashlib.sha256(path.read_bytes()).hexdigest()

    assert file_sha256(path) == expected.upper()
    assert file_sha256(path, uppercase=False) == expected


def test_shared_scalar_parsers_match_diagnostic_loader_contracts() -> None:
    assert optional_float(" 1.25 ") == 1.25
    assert optional_float("'-8.6973") == -8.6973
    assert optional_float(float("inf")) is None
    assert optional_float("not numeric") is None
    assert optional_int("3.9") == 3
    assert optional_int("") is None
    assert positive_int("3") == 3
    assert positive_int("0") is None
    assert positive_int("bad") is None
    assert bool_value("TRUE") is True
    assert bool_value("yes") is True
    assert bool_value("0") is False
    assert bool_value("maybe") is None
    assert text_value(None) == ""
    assert text_value("  A  ") == "A"
    assert split_semicolon_labels(" a ; ; b ") == ["a", "b"]
    assert has_semicolon_token("alpha;beta", "beta") is True
    assert has_semicolon_token("alphabet;gamma", "alpha") is False
    assert numeric_equal("1.0000001", "1.0000002") is True
    assert numeric_equal("1.01", "1.02") is False
    assert numeric_equal(" raw ", "raw") is True
    assert numeric_equal("raw", "other") is False


def test_identity_family_keys_preserves_order_and_deduplicates() -> None:
    assert identity_family_keys(
        {
            "peak_hypothesis_id": " PH1 ",
            "source_feature_family_ids": "FAM001; PH1 ; FAM002;;FAM001",
        },
    ) == ("PH1", "FAM001", "FAM002")


def test_rows_by_text_field_preserves_group_order() -> None:
    rows = (
        {"family": "F1", "sample": "S1"},
        {"family": "F2", "sample": "S2"},
        {"family": "F1", "sample": "S3"},
        {"family": "", "sample": "ignored"},
    )

    grouped = rows_by_text_field(rows, "family")

    assert grouped == {
        "F1": (rows[0], rows[2]),
        "F2": (rows[1],),
    }


def test_required_indexes_reports_sheet_and_missing_columns() -> None:
    assert required_indexes(("Label", "m/z", None), ("Label", "m/z"), "Targets") == {
        "Label": 0,
        "m/z": 1,
    }

    with pytest.raises(ValueError, match="Targets.*missing required columns: Role"):
        required_indexes(("Label",), ("Label", "Role"), "Targets")
