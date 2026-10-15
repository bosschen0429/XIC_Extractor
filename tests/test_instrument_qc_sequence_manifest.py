import csv
import json
from pathlib import Path

import pytest

from xic_extractor.instrument_qc import sequence_manifest_writers
from xic_extractor.instrument_qc.classification import InstrumentQCClass
from xic_extractor.instrument_qc.sequence_manifest import (
    ManifestMatchStatus,
    SequenceDocEntry,
    build_sequence_manifest_from_entries,
    parse_sequence_docx,
    parse_sequence_tables,
)
from xic_extractor.instrument_qc.sequence_manifest_writers import (
    INJECTION_ORDER_COLUMNS,
    MANIFEST_TSV_COLUMNS,
    write_injection_order_csv,
    write_sequence_manifest_json,
    write_sequence_manifest_markdown,
    write_sequence_manifest_tsv,
)
from xic_extractor.sample_metadata import (
    SAMPLE_METADATA_SCHEMA_VERSION,
    load_sample_metadata,
)


def test_sequence_table_produces_manifest_rows_with_classes_and_matches() -> None:
    entries = parse_sequence_tables(
        [
            [
                ["ID", "File Name", "Instrument Method", "Sample", "Inj"],
                [
                    "4",
                    "SDOLEK-pretest",
                    "20260105 SDOLEK",
                    "50 ppb SDO/LEK; untargeted",
                    "2",
                ],
                [
                    "5",
                    "250ppb_Mix_STDs",
                    "20260105 STDs_ddMS2_CIDwHCD_EASY-IC",
                    "Mix 73 STDs",
                    "10",
                ],
                ["6", "Blank_3", "20260105 SB Check", "DIW", "20"],
            ],
        ],
        source_doc="method.docx",
    )

    rows = build_sequence_manifest_from_entries(
        entries,
        raw_stems=("SDOLEK-pretest", "250ppb_Mix_STDs", "Blank_3"),
    )

    assert [(row.raw_stem, row.injection_order) for row in rows] == [
        ("SDOLEK-pretest", 4),
        ("250ppb_Mix_STDs", 5),
        ("Blank_3", 6),
    ]
    assert [row.instrument_qc_class for row in rows] == [
        InstrumentQCClass.SDOLEK,
        InstrumentQCClass.MIX_STDS,
        InstrumentQCClass.BLANK,
    ]
    assert all(row.match_status == ManifestMatchStatus.MATCHED for row in rows)
    assert rows[0].doc_display_name == "SDOLEK-pretest"
    assert rows[0].source_section == "table:1:row:2"


def test_method_detail_table_sets_sdolek_whcd_activation() -> None:
    entries = parse_sequence_tables(
        [
            [
                ["ID", "File Name", "Instrument Method", "Sample", "Inj"],
                [
                    "4",
                    "SDOLEK-pretest",
                    "20260105 SDOLEK",
                    "50 ppb SDO/LEK; untargeted",
                    "2",
                ],
            ],
            [
                ["Method-3", "20260105 SDOLEK", "1-6(source)"],
                [
                    "MS1 m/z 200 - 700",
                    "ddMS2 OT (wHCD) R=15,000",
                    "wHCD: 40,50,60 (stepped)",
                ],
            ],
        ],
        source_doc="method.docx",
    )

    rows = build_sequence_manifest_from_entries(
        entries,
        raw_stems=("SDOLEK-pretest",),
    )

    assert rows[0].instrument_method == "20260105 SDOLEK"
    assert rows[0].activation_method == "wHCD"


def test_parse_sequence_docx_reports_corrupt_docx_as_value_error(
    tmp_path: Path,
) -> None:
    method_doc = tmp_path / "not-really.docx"
    method_doc.write_text("not a zip", encoding="utf-8")

    try:
        parse_sequence_docx(method_doc)
    except ValueError as exc:
        assert "unable to parse method DOCX" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_method_detail_table_sets_mixed_std_activation() -> None:
    entries = parse_sequence_tables(
        [
            [
                ["ID", "File Name", "Instrument Method", "Sample", "Inj"],
                [
                    "5",
                    "250ppb_Mix_STDs",
                    "20260105 STD_ddMS2_CIDwHCD_EASY-IC",
                    "Mix STDs",
                    "10",
                ],
            ],
            [
                ["Method-4", "20260105 STD_ddMS2_CIDwHCD_EASY-IC"],
                [
                    "分析方法同時包含CID與wHCD碎裂模式",
                    "ddMS2 OT (CID)",
                    "ddMS2 OT (wHCD)",
                ],
            ],
        ],
        source_doc="method.docx",
    )

    rows = build_sequence_manifest_from_entries(
        entries,
        raw_stems=("250ppb_Mix_STDs",),
    )

    assert rows[0].activation_method == "CIDwHCD"


def test_sequence_manifest_keeps_unmatched_docs_rows() -> None:
    entry = SequenceDocEntry(
        source_doc="method.docx",
        source_section="table:1:row:2",
        doc_display_name="SDOLEK-7",
        injection_order=120,
        instrument_method="20260105 SDOLEK",
        sample_description="50 ppb SDO/LEK",
        injection_volume="2",
    )

    rows = build_sequence_manifest_from_entries(
        (entry,),
        raw_stems=("SDOLEK-1",),
    )

    assert rows[0].raw_stem == "SDOLEK-7"
    assert rows[0].match_status == ManifestMatchStatus.UNMATCHED
    assert "No RAW stem" in rows[0].match_reason


def test_repeated_doc_display_names_match_suffix_raws_by_order() -> None:
    entries = parse_sequence_tables(
        [
            [
                ["ID", "File Name", "Instrument Method", "Sample", "Inj"],
                ["5", "250ppb_Mix_STDs", "20260105 STDs", "Mix STDs", "10"],
                ["44", "250ppb_Mix_STDs", "20260105 STDs", "Mix STDs", "10"],
                ["78", "250ppb_Mix_STDs", "20260105 STDs", "Mix STDs", "10"],
            ],
        ],
        source_doc="method.docx",
    )

    rows = build_sequence_manifest_from_entries(
        entries,
        raw_stems=("250ppb_Mix_STDs", "250ppb_Mix_STDs_1", "250ppb_Mix_STDs_2"),
    )

    assert [(row.raw_stem, row.injection_order) for row in rows] == [
        ("250ppb_Mix_STDs", 5),
        ("250ppb_Mix_STDs_1", 44),
        ("250ppb_Mix_STDs_2", 78),
    ]
    assert all(row.match_status == ManifestMatchStatus.MATCHED for row in rows)
    assert "sequence order" in rows[1].match_reason


def test_sequence_manifest_keeps_raw_only_instrument_qc_rows() -> None:
    rows = build_sequence_manifest_from_entries(
        (),
        raw_stems=("SDOLEK-pretest-1", "TumorBC2257_DNA"),
    )

    assert len(rows) == 1
    assert rows[0].raw_stem == "SDOLEK-pretest-1"
    assert rows[0].doc_display_name == ""
    assert rows[0].match_status == ManifestMatchStatus.UNMATCHED
    assert rows[0].instrument_qc_class == InstrumentQCClass.SDOLEK


def test_raw_only_sdolek_rows_use_unique_doc_method_default() -> None:
    entries = parse_sequence_tables(
        [
            [
                ["ID", "File Name", "Instrument Method", "Sample", "Inj"],
                ["4", "SDOLEK-pretest", "20260105 SDOLEK", "50 ppb", "2"],
            ],
            [
                ["Method-3", "20260105 SDOLEK"],
                ["ddMS2 OT (wHCD)", "wHCD: 40,50,60 (stepped)"],
            ],
        ],
        source_doc="method.docx",
    )

    rows = build_sequence_manifest_from_entries(
        entries,
        raw_stems=("SDOLEK-pretest", "SDOLEK-pretest-1"),
    )

    raw_only = [row for row in rows if row.raw_stem == "SDOLEK-pretest-1"][0]
    assert raw_only.match_status == ManifestMatchStatus.UNMATCHED
    assert raw_only.instrument_method == "20260105 SDOLEK"
    assert raw_only.activation_method == "wHCD"


def test_istd_sample_description_does_not_become_mix_stds() -> None:
    entries = parse_sequence_tables(
        [
            [
                ["ID", "File Name", "Instrument Method", "Sample", "Inj"],
                [
                    "8",
                    "Tumor tissue BC2257_DNA",
                    "20260105 Breast Cancer_ddMS2-CID_Tissue",
                    "20260103 Tumor tissue BC2257_DNA +ISTDs",
                    "20",
                ],
            ],
        ],
        source_doc="method.docx",
    )

    rows = build_sequence_manifest_from_entries(
        entries,
        raw_stems=("TumorBC2257_DNA",),
    )

    assert rows[0].instrument_qc_class == InstrumentQCClass.UNKNOWN


def test_sequence_manifest_marks_ambiguous_matches() -> None:
    entry = SequenceDocEntry(
        source_doc="method.docx",
        source_section="table:1:row:2",
        doc_display_name="SDOLEK-1",
        injection_order=24,
        instrument_method="20260105 SDOLEK",
        sample_description="50 ppb SDO/LEK",
        injection_volume="2",
    )

    rows = build_sequence_manifest_from_entries(
        (entry,),
        raw_stems=("SDOLEK-1", "SDOLEK_1"),
    )

    assert rows[0].match_status == ManifestMatchStatus.AMBIGUOUS
    assert "multiple RAW stems" in rows[0].match_reason


def test_sequence_manifest_writers_emit_audit_and_injection_order(
    tmp_path: Path,
) -> None:
    entries = parse_sequence_tables(
        [
            [
                ["ID", "File Name", "Instrument Method", "Sample", "Inj"],
                ["4", "SDOLEK-pretest", "20260105 SDOLEK", "50 ppb", "2"],
                ["5", "SDOLEK-missing", "20260105 SDOLEK", "50 ppb", "2"],
            ],
        ],
        source_doc="method.docx",
    )
    rows = build_sequence_manifest_from_entries(
        entries,
        raw_stems=("SDOLEK-pretest",),
    )

    manifest_tsv = tmp_path / "manifest.tsv"
    order_csv = tmp_path / "order.csv"
    manifest_json = tmp_path / "manifest.json"
    manifest_md = tmp_path / "manifest.md"
    write_sequence_manifest_tsv(manifest_tsv, rows)
    write_injection_order_csv(order_csv, rows)
    write_sequence_manifest_json(manifest_json, rows)
    write_sequence_manifest_markdown(manifest_md, rows)

    with manifest_tsv.open(encoding="utf-8") as handle:
        manifest_rows = list(csv.DictReader(handle, delimiter="\t"))
    assert manifest_rows[0]["doc_display_name"] == "SDOLEK-pretest"
    assert manifest_rows[1]["match_status"] == "unmatched"

    with order_csv.open(encoding="utf-8") as handle:
        order_rows = list(csv.DictReader(handle))
    assert order_rows == [{"Sample_Name": "SDOLEK-pretest", "Injection_Order": "4"}]

    payload = json.loads(manifest_json.read_text(encoding="utf-8"))
    assert payload["source_contract"] == "method_docs_only"
    assert payload["summary"]["match_status_counts"] == {
        "matched": 1,
        "unmatched": 1,
    }
    assert "SDOLEK-missing" in manifest_md.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("name", "display_name"),
    (
        ("=1+1", "'=1+1"),
        ("+1+1", "'+1+1"),
        ("-1+1", "'-1+1"),
        ("@SUM(1,1)", "'@SUM(1,1)"),
        ("'=literal", "'=literal"),
        ("Sample Ω", "Sample Ω"),
    ),
)
def test_manifest_escapes_display_text_without_changing_machine_handoffs(
    tmp_path: Path, name: str, display_name: str
) -> None:
    entries = parse_sequence_tables(
        [
            [
                ["ID", "File Name", "Instrument Method", "Sample", "Inj"],
                ["4", name, "20260105 SDOLEK", "50 ppb", "2"],
            ]
        ],
        source_doc="=source.docx",
    )
    rows = build_sequence_manifest_from_entries(entries, raw_stems=(name,))
    manifest = tmp_path / "manifest.tsv"
    write_sequence_manifest_tsv(manifest, rows)
    with manifest.open(encoding="utf-8", newline="") as handle:
        saved = next(csv.DictReader(handle, delimiter="\t"))
    assert saved["source_doc"] == "'=source.docx"
    assert saved["raw_stem"] == saved["doc_display_name"] == display_name
    assert saved["injection_order"] == "4"

    order = tmp_path / "order.csv"
    write_injection_order_csv(order, rows)
    with order.open(encoding="utf-8", newline="") as handle:
        assert list(csv.DictReader(handle)) == [
            {"Sample_Name": name, "Injection_Order": "4"}
        ]
    metadata = tmp_path / "metadata.tsv"
    sequence_manifest_writers.write_sample_metadata_tsv(metadata, rows)
    restored = load_sample_metadata(metadata)
    assert len(restored) == 1
    assert restored[0].sample_name == restored[0].raw_stem == name
    json_path = tmp_path / "manifest.json"
    write_sequence_manifest_json(json_path, rows)
    saved = json.loads(json_path.read_text(encoding="utf-8"))["rows"][0]
    assert saved["source_doc"] == "=source.docx"
    assert saved["raw_stem"] == saved["doc_display_name"] == name


def test_sequence_manifest_writes_sample_metadata_for_actual_raw_rows(
    tmp_path: Path,
) -> None:
    entries = parse_sequence_tables(
        [
            [
                ["ID", "File Name", "Instrument Method", "Sample", "Inj"],
                ["4", "SDOLEK-pretest", "20260105 SDOLEK", "50 ppb", "2"],
                ["5", "SDOLEK-missing", "20260105 SDOLEK", "50 ppb", "2"],
                ["6", "Blank_3", "20260105 SB Check", "DIW", "20"],
            ],
        ],
        source_doc="method.docx",
    )
    rows = build_sequence_manifest_from_entries(
        entries,
        raw_stems=("SDOLEK-pretest", "SDOLEK-pretest-1", "Blank_3"),
    )

    sample_metadata_tsv = tmp_path / "instrument_qc_sample_metadata.tsv"
    assert hasattr(sequence_manifest_writers, "write_sample_metadata_tsv")
    sequence_manifest_writers.write_sample_metadata_tsv(sample_metadata_tsv, rows)

    with sample_metadata_tsv.open(encoding="utf-8") as handle:
        metadata_rows = list(csv.DictReader(handle, delimiter="\t"))
    assert metadata_rows
    assert hasattr(sequence_manifest_writers, "SAMPLE_METADATA_OUTPUT_COLUMNS")
    assert list(metadata_rows[0].keys()) == list(
        sequence_manifest_writers.SAMPLE_METADATA_OUTPUT_COLUMNS
    )
    by_sample = {row["sample_name"]: row for row in metadata_rows}
    assert set(by_sample) == {"SDOLEK-pretest", "SDOLEK-pretest-1", "Blank_3"}
    assert by_sample["SDOLEK-pretest"]["schema_version"] == (
        SAMPLE_METADATA_SCHEMA_VERSION
    )
    assert by_sample["SDOLEK-pretest"]["injection_order"] == "4"
    assert by_sample["SDOLEK-pretest"]["sample_role"] == "system_suitability"
    assert by_sample["SDOLEK-pretest"]["matrix_type"] == "instrument_qc"
    assert by_sample["SDOLEK-pretest"]["group"] == "SDOLEK"
    assert by_sample["SDOLEK-pretest-1"]["injection_order"] == ""
    assert by_sample["Blank_3"]["sample_role"] == "blank"
    assert "SDOLEK-missing" not in by_sample


def test_sequence_manifest_writers_emit_header_only_empty_outputs(
    tmp_path: Path,
) -> None:
    manifest_tsv = tmp_path / "nested" / "manifest.tsv"
    order_csv = tmp_path / "nested" / "order.csv"

    write_sequence_manifest_tsv(manifest_tsv, [])
    write_injection_order_csv(order_csv, [])

    with manifest_tsv.open(encoding="utf-8") as handle:
        assert list(csv.reader(handle, delimiter="\t")) == [MANIFEST_TSV_COLUMNS]
    with order_csv.open(encoding="utf-8") as handle:
        assert list(csv.reader(handle)) == [INJECTION_ORDER_COLUMNS]
