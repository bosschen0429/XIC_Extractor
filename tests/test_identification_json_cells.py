"""Lossless method definitions across Excel's per-cell text boundary."""

import json

import pytest
from openpyxl import Workbook, load_workbook

from xic_extractor.alignment_explanation.storage import ExplanationError
from xic_extractor.configuration.json_cells import decode_json_cells, encode_json_cells
from xic_extractor.configuration.models import ConfigError
from xic_extractor.configuration.targeted_identification import (
    identification_methods_payload,
)
from xic_extractor.configuration.targeted_method_files import (
    create_targeted_method,
    load_targeted_method,
    save_targeted_method,
)
from xic_extractor.output.sheet_identification import build_identification_sheets
from xic_inspector.sources.targeted_workbook import TargetedWorkbookSource


def condition(index):
    return {
        "kind": "delta_mass",
        "positions": [
            {
                "label": "quant" if j == 0 else f"channel_{j}",
                "offset_mz": j * (1 + index * 0.001),
            }
            for j in range(8)
        ],
    }


def method(registry):
    return create_targeted_method(
        {},
        [
            {
                "label": "target",
                "mz": "500",
                "expected_rt_min": "2",
                "cid_nl_tag": "",
                "role": "analyte",
                "istd_pair": "",
            }
        ],
        identification_methods=registry,
        default_method=next(iter(registry)),
    )


def result_workbook(tmp_path, admitted):
    book = Workbook()
    targets = book.active
    targets.title = "Targets"
    targets.append(["Label", "Role", "m/z", "RT min", "RT max", "Method"])
    targets.append(["target", "Analyte", 500, 2, 2, admitted.default_method])
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
    results.append(
        [
            "sample",
            "target",
            None,
            None,
            None,
            None,
            "serialization fixture",
            "",
            "",
            "FALSE",
        ]
    )
    build_identification_sheets(
        book, admitted.targets, {"conditions": [], "positions": []}
    )
    path = tmp_path / "result.xlsx"
    book.save(path)
    book.close()
    return path


def test_large_registry_roundtrips_portable_method(tmp_path):
    admitted = method({f"Method{i}": {"conditions": [condition(i)]} for i in range(32)})
    payload = identification_methods_payload(admitted.identification_methods)
    assert len(json.dumps(payload)) > 32767
    path = tmp_path / "portable.xlsx"
    save_targeted_method(admitted, path)
    loaded = load_targeted_method(path)
    assert loaded.targets == admitted.targets
    assert loaded.identification_methods == admitted.identification_methods
    assert loaded.default_method == admitted.default_method
    assert len(loaded.identification_methods) == 32


def test_large_definition_is_readable_in_saved_inspector(tmp_path):
    admitted = method(
        {
            "Large OR": {
                "combine_mode": "any",
                "conditions": [condition(i) for i in range(40)],
            }
        }
    )
    path = result_workbook(tmp_path, admitted)
    original = path.read_bytes()
    source = TargetedWorkbookSource(path)
    assert len(source.identification_methods["Large OR"]["conditions"]) == 40
    assert source.identification_methods["Large OR"]["conditions"][-1]["positions"][-1][
        "offset_mz"
    ] == pytest.approx(7.273)
    assert source.identification_methods == identification_methods_payload(
        admitted.identification_methods
    )
    assert path.read_bytes() == original


def test_definition_fragment_starting_with_equals_is_literal_text(tmp_path):
    definition = {"conditions": [condition(0)]}
    definition["conditions"][0]["label"] = "MARKER"
    encoded = json.dumps(
        identification_methods_payload(
            method({"Formula-safe": definition}).identification_methods
        )["Formula-safe"],
        ensure_ascii=True,
    )
    prefix_size = encoded.index("MARKER")
    definition["conditions"][0]["label"] = (
        "a" * (32767 - prefix_size) + "=SUM(A1:A2)測試😀"
    )
    admitted = method({"Formula-safe": definition})
    path = result_workbook(tmp_path, admitted)
    book = load_workbook(path, read_only=True, data_only=False)
    try:
        rows = list(book["Identification Methods"].values)
        assert rows[2][2].startswith("=")
        assert book["Identification Methods"]["C3"].data_type == "s"
        assert all(len(row[2]) <= 32767 and row[2].isascii() for row in rows[1:])
    finally:
        book.close()
    assert TargetedWorkbookSource(
        path
    ).identification_methods == identification_methods_payload(
        admitted.identification_methods
    )


def test_unicode_json_cells_reassemble_in_part_order():
    original = {"text": "測試😀" * 12000}
    cells = encode_json_cells(original)
    assert len(cells) > 1
    assert all(len(cell) <= 32767 and cell.isascii() for cell in cells)
    assert decode_json_cells(reversed(list(enumerate(cells, start=1)))) == original


@pytest.mark.parametrize(
    "parts",
    [[(1, "{"), (3, "}")], [(1, "{}"), (1, "{}")], [(0, "{}")], [("bad", "{}")]],
)
def test_missing_or_duplicate_or_invalid_parts_are_rejected(parts):
    with pytest.raises(ValueError):
        decode_json_cells(parts)


@pytest.mark.parametrize("format_name", ["portable", "result"])
@pytest.mark.parametrize("invalid_part", [1, 3], ids=["duplicate", "missing"])
def test_malformed_saved_fragments_are_rejected(tmp_path, format_name, invalid_part):
    admitted = method(
        {
            "Large": {
                "combine_mode": "any",
                "conditions": [condition(i) for i in range(40)],
            }
        }
    )
    if format_name == "portable":
        path = tmp_path / "portable.xlsx"
        save_targeted_method(admitted, path)
        book = load_workbook(path)
        second = next(
            row[0]
            for row in book["Method"].iter_rows()
            if row[0].value == "identification_methods.2"
        )
        second.value = f"identification_methods.{invalid_part}"
        reader, error = load_targeted_method, ConfigError
    else:
        path = result_workbook(tmp_path, admitted)
        book = load_workbook(path)
        assert book["Identification Methods"]["B3"].value == 2
        book["Identification Methods"]["B3"] = invalid_part
        reader, error = TargetedWorkbookSource, ExplanationError
    book.save(path)
    book.close()
    with pytest.raises(error, match="unique|contiguous"):
        reader(path)
