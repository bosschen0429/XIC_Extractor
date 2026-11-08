"""Identification methods stored as a readable worksheet table."""

import pytest
from openpyxl import Workbook, load_workbook

from xic_extractor.alignment_explanation.errors import ExplanationError
from xic_extractor.configuration.identification_method_table import (
    METHOD_TABLE_SHEET,
)
from xic_extractor.configuration.models import ConfigError
from xic_extractor.configuration.targeted_identification import (
    identification_methods_payload,
)
from xic_extractor.configuration.targeted_method_files import (
    create_targeted_method,
    load_target_table,
    load_targeted_method,
    save_target_table,
    save_targeted_method,
)
from xic_extractor.output.sheet_identification import build_identification_sheets
from xic_inspector.sources.targeted_workbook import TargetedWorkbookSource

REGISTRY = {
    "Pt-pair": {
        "combine_mode": "all",
        "mz_tolerance_ppm": 15,
        "conditions": [
            {"kind": "cid_nl", "tag": "dR"},
            {
                "kind": "delta_mass",
                "min_shape_r": 0.8,
                "positions": [
                    {"label": "quant", "offset_mz": 0},
                    {
                        "label": "light",
                        "offset_mz": -1.9981,
                        "ratio_min": 0.5,
                        "ratio_max": 2,
                        "compare_shape": True,
                    },
                    {"label": "重", "offset_mz": 1.9958},
                ],
            },
        ],
    },
    "Either": {
        "combine_mode": "any",
        "conditions": [
            {"kind": "cid_nl", "tag": "R"},
            {"kind": "cid_nl", "tag": "MeR"},
        ],
    },
}


def target(label="target", method="Pt-pair"):
    return {
        "label": label,
        "mz": "500",
        "expected_rt_min": "2",
        "method": method,
        "role": "analyte",
        "istd_pair": "",
    }


def admitted_method():
    return create_targeted_method(
        {},
        [target(), target("other", "Either"), target("plain", "")],
        identification_methods=REGISTRY,
    )


def result_workbook(tmp_path, admitted):
    book = Workbook()
    sheet = book.active
    sheet.title = "Targets"
    sheet.append(["Label", "Role", "m/z", "RT min", "RT max", "Method"])
    for item in admitted.targets:
        sheet.append([item.label, "Analyte", 500, 2, 2, item.method_name])
    metadata = book.create_sheet("Run Metadata")
    metadata.append(["Key", "Value"])
    metadata.append(["targeted_output_schema_version", "targeted_output"])
    results = book.create_sheet("XIC Results")
    results.append(
        [
            "SampleName",
            "Target",
            "RT",
            "Area",
            "PeakStart",
            "PeakEnd",
            "Reason",
            "Review",
            "Review Reason",
            "Counted Detection",
        ]
    )
    for item in admitted.targets:
        results.append(
            ["sample", item.label, None, None, None, None, "fixture", "", "", "FALSE"]
        )
    build_identification_sheets(
        book, admitted.targets, {"conditions": [], "positions": []}
    )
    path = tmp_path / "result.xlsx"
    book.save(path)
    book.close()
    return path


def test_portable_method_roundtrips_one_row_per_condition_ion(tmp_path):
    admitted = admitted_method()
    path = tmp_path / "portable.xlsx"
    save_targeted_method(admitted, path)

    loaded = load_targeted_method(path)
    assert loaded.targets == admitted.targets
    assert loaded.identification_methods == admitted.identification_methods
    book = load_workbook(path, read_only=True)
    try:
        rows = list(book[METHOD_TABLE_SHEET].values)
    finally:
        book.close()
    header = rows[0]
    keyed = [dict(zip(header, row, strict=True)) for row in rows[1:]]
    # Pt-pair: one CID-NL row + three ions; Either: two CID-NL rows.
    assert [(r["Method"], r["Condition"], r["Position"]) for r in keyed] == [
        ("Pt-pair", "1", None),
        ("Pt-pair", "2", "quant"),
        ("Pt-pair", "2", "light"),
        ("Pt-pair", "2", "重"),
        ("Either", "1", None),
        ("Either", "2", None),
    ]
    assert keyed[2]["Delta m/z"] == "-1.9981"


def test_result_definitions_are_read_back_by_inspector_without_rewrite(tmp_path):
    admitted = admitted_method()
    path = result_workbook(tmp_path, admitted)
    original = path.read_bytes()

    source = TargetedWorkbookSource(path)

    assert source.identification_methods == identification_methods_payload(
        admitted.identification_methods
    )
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    "cell,value,message",
    [
        ("B3", "any", "disagree"),
        ("D2", 3, "Condition numbers"),
        ("E4", "cid_nl", "mixes Kind"),
    ],
    ids=["method-fields-disagree", "condition-gap", "mixed-kind"],
)
@pytest.mark.parametrize("format_name", ["portable", "result"])
def test_inconsistent_definition_rows_are_rejected(
    tmp_path, format_name, cell, value, message
):
    admitted = admitted_method()
    if format_name == "portable":
        path = tmp_path / "portable.xlsx"
        save_targeted_method(admitted, path)
        reader, error = load_targeted_method, ConfigError
    else:
        path = result_workbook(tmp_path, admitted)
        reader, error = TargetedWorkbookSource, ExplanationError
    book = load_workbook(path)
    book[METHOD_TABLE_SHEET][cell] = value
    book.save(path)
    book.close()

    with pytest.raises(error, match=message):
        reader(path)


def test_legacy_cid_nl_tag_column_is_read_as_method(tmp_path):
    legacy = {**target(method=""), "cid_nl_tag": "dR"}
    del legacy["method"]
    path = tmp_path / "legacy.csv"
    path.write_text(
        ",".join(legacy) + "\n" + ",".join(legacy.values()) + "\n", encoding="utf-8"
    )
    from xic_extractor.configuration.targets import _read_targets

    assert _read_targets(path)[0].cid_nl_tag == "dR"

    xlsx = tmp_path / "legacy.xlsx"
    book = Workbook()
    book.active.title = "Targets"
    book.active.append(list(legacy))
    book.active.append(list(legacy.values()))
    book.save(xlsx)
    rows = load_target_table(xlsx)
    assert rows[0]["method"] == "dR" and "cid_nl_tag" not in rows[0]
    save_target_table(rows, xlsx)
    assert load_target_table(xlsx)[0]["method"] == "dR"


def test_table_with_both_method_columns_is_rejected():
    with pytest.raises(ConfigError, match="both method and cid_nl_tag"):
        create_targeted_method({}, [{**target(method="dR"), "cid_nl_tag": "R"}])
