from __future__ import annotations

import csv
from pathlib import Path

import openpyxl
import pytest

from tests.sample_metadata_sequence_parsing_fixtures import (
    _create_synthetic_docx,
)
from xic_extractor.sample_metadata import (
    SampleMetadataError,
    load_and_match_sample_metadata,
    normalize_for_matching,
    parse_sequence_file,
)


def _create_synthetic_xlsx(path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "RunSequence"
    ws.append(["Notes: Sequence run 2026-01"])
    ws.append(
        [
            "Injection_Order",
            "Sample_Name",
            "File_Name",
            "Sample_Type",
            "Batch",
        ]
    )
    ws.append([1, "Pooled_QC_1", "QC_P1", "QC", "BatchA"])
    ws.append([2, "Patient_01_DNA", "P01_DNA", "Sample", "BatchA"])
    ws.append([3, "Patient_02_DNA", "P02_DNA", "Sample", "BatchA"])
    wb.save(path)


def test_normalize_for_matching() -> None:
    assert normalize_for_matching("TumorBC2257_DNA.raw") == "tumorbc2257dna"
    assert normalize_for_matching("Tumor tissue BC2257_DNA") == "tumorbc2257dna"
    assert normalize_for_matching(
        "TumorBC2286_DNAandRNA.raw"
    ) == normalize_for_matching("Tumor tissue BC2286_DNA +RNA")
    assert normalize_for_matching(
        "Breast_Cancer_Tissue_pooled_QC1.raw"
    ) == normalize_for_matching("Breast Cancer Tissue_pooled_QC_1")


@pytest.mark.parametrize("index_header", ["ID", ""])
def test_parse_docx_sequence(tmp_path: Path, index_header: str) -> None:
    docx_file = tmp_path / "test_run.docx"
    _create_synthetic_docx(docx_file, index_header=index_header)

    rows = parse_sequence_file(docx_file)
    assert len(rows) == 4

    # Row 1: Blank
    assert rows[0].injection_order == 1
    assert rows[0].raw_stem == "Blank_1"
    assert rows[0].sample_role == "blank"

    # Row 2: Pooled QC
    assert rows[1].injection_order == 2
    assert rows[1].raw_stem == "QC_Pool_1"
    assert rows[1].sample_role == "qc"

    # Row 3: Tumor
    assert rows[2].injection_order == 3
    assert rows[2].raw_stem == "Tumor tissue BC001_DNA"
    assert rows[2].sample_role == "sample"
    assert rows[2].group == ""

    # Row 4: Normal
    assert rows[3].injection_order == 4
    assert rows[3].group == ""


def test_word_sequence_uses_shared_match_statuses(tmp_path: Path, monkeypatch) -> None:
    docx_file = tmp_path / "test_run.docx"
    _create_synthetic_docx(docx_file)

    from xic_extractor import sequence_document as sequence_manifest

    owner_parse = sequence_manifest.parse_sequence_docx
    parse_calls = 0

    def counted_parse(path: Path):
        nonlocal parse_calls
        parse_calls += 1
        return owner_parse(path)

    monkeypatch.setattr(sequence_manifest, "parse_sequence_docx", counted_parse)

    report = load_and_match_sample_metadata(
        docx_file,
        ("Blank_1", "QC_Pool_1", "Unknown_RAW"),
    )

    assert report.matched_raw_stems == ("Blank_1", "QC_Pool_1")
    assert report.unmatched_raw_stems == ("Unknown_RAW",)
    assert report.ambiguous_sequence_names == ()
    assert report.unmatched_sequence_names == (
        "Tumor tissue BC001_DNA",
        "Normal tissue BC001_DNA",
    )
    assert [row.injection_order for row in report.rows] == [1, 2, None]
    assert parse_calls == 1


def test_word_sequence_does_not_assign_ambiguous_raw_order(tmp_path: Path) -> None:
    docx_file = tmp_path / "test_run.docx"
    _create_synthetic_docx(docx_file)

    report = load_and_match_sample_metadata(
        docx_file,
        ("Blank_1", "Blank1"),
    )

    assert report.matched_raw_stems == ()
    assert report.unmatched_raw_stems == ("Blank_1", "Blank1")
    assert report.ambiguous_sequence_names == ("Blank_1",)
    assert [row.injection_order for row in report.rows] == [None, None]


def test_parse_xlsx_sequence(tmp_path: Path) -> None:
    xlsx_file = tmp_path / "test_run.xlsx"
    _create_synthetic_xlsx(xlsx_file)

    rows = parse_sequence_file(xlsx_file)
    assert len(rows) == 3

    assert rows[0].injection_order == 1
    assert rows[0].sample_role == "qc"
    assert rows[0].batch_id == "BatchA"

    assert rows[1].injection_order == 2
    assert rows[1].sample_name == "Patient_01_DNA"
    assert rows[1].raw_stem == "P01_DNA"
    assert rows[1].sample_role == "sample"


def test_sequence_disagreements_are_reported_as_written(tmp_path: Path) -> None:
    # A stale sequence: two rows share number 7, and one row's file name and
    # sample text name different samples. The second BC2303 text is a real
    # duplicate, so de-duplicating names must not hide the disagreement.
    sequence = tmp_path / "sequence.xlsx"
    book = openpyxl.Workbook()
    for row in (
        ["No", "File Name", "Sample"],
        [6, "TumorBC2303_DNA", "Tumor tissue BC2303_DNA"],
        [7, "TumorBC2312_DNA", "Tumor tissue BC2303_DNA"],
        [7, "NormalBC2259_DNA", "Normal tissue BC2259_DNA"],
    ):
        book.active.append(row)
    book.save(sequence)

    report = load_and_match_sample_metadata(sequence, ("TumorBC2312_DNA",))

    assert [(c.kind, c.sequence_names, c.detail) for c in report.conflicts] == [
        ("duplicate_order", ("TumorBC2312_DNA", "NormalBC2259_DNA"), "7"),
        ("name_mismatch", ("TumorBC2312_DNA",), "BC2312 / BC2303"),
    ]


def test_parse_generic_csv_sequence(tmp_path: Path) -> None:
    csv_file = tmp_path / "plain_sequence.csv"
    with csv_file.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Order", "Raw File", "Sample Name", "Role"])
        writer.writerow([1, "Blank_A.raw", "Blank A", "Blank"])
        writer.writerow([2, "Sample_01.raw", "Sample 01", "Sample"])

    rows = parse_sequence_file(csv_file)
    assert len(rows) == 2
    assert rows[0].injection_order == 1
    assert rows[0].raw_stem == "Blank_A"
    assert rows[0].sample_role == "blank"
    assert rows[1].injection_order == 2
    assert rows[1].sample_role == "sample"


@pytest.mark.parametrize("suffix", [".csv", ".tsv", ".xlsx"])
def test_parse_tabular_sequence_resolves_exact_alias_collisions(
    tmp_path: Path,
    suffix: str,
) -> None:
    sequence_file = tmp_path / f"alias_collisions{suffix}"
    _write_sequence_table(
        sequence_file,
        ["Batch ID", "Sample Type", "Sample ID", "Injection Order"],
        [["BatchA", "QC", "QC1", 7]],
    )

    rows = parse_sequence_file(sequence_file)

    assert [
        (
            row.sample_name,
            row.raw_stem,
            row.injection_order,
            row.sample_role,
            row.batch_id,
        )
        for row in rows
    ] == [("QC1", "QC1", 7, "qc", "BatchA")]


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
def test_parse_tabular_sequence_rejects_ambiguous_identity_headers(
    tmp_path: Path,
    suffix: str,
) -> None:
    sequence_file = tmp_path / f"ambiguous_identity{suffix}"
    _write_sequence_table(
        sequence_file,
        ["Sample ID", "Sample Name", "Injection Order"],
        [["QC1", "QC One", 7]],
    )

    with pytest.raises(SampleMetadataError, match="標頭語義不明確"):
        parse_sequence_file(sequence_file)


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
def test_parse_tabular_sequence_rejects_missing_identity_header(
    tmp_path: Path,
    suffix: str,
) -> None:
    sequence_file = tmp_path / f"missing_identity{suffix}"
    _write_sequence_table(
        sequence_file,
        ["Batch ID", "Injection Order"],
        [["BatchA", 7]],
    )

    with pytest.raises(SampleMetadataError, match="缺少樣品識別標頭"):
        parse_sequence_file(sequence_file)


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
def test_parse_tabular_sequence_rejects_header_without_sample_rows(
    tmp_path: Path,
    suffix: str,
) -> None:
    sequence_file = tmp_path / f"header_only{suffix}"
    _write_sequence_table(
        sequence_file,
        ["Sample ID", "Injection Order"],
        [],
    )

    with pytest.raises(SampleMetadataError, match="沒有有效的樣品資料列"):
        parse_sequence_file(sequence_file)


def _write_sequence_table(
    path: Path,
    headers: list[str],
    rows: list[list[str | int]],
) -> None:
    if path.suffix == ".xlsx":
        workbook = openpyxl.Workbook()
        worksheet = workbook.active
        worksheet.append(headers)
        for row in rows:
            worksheet.append(row)
        workbook.save(path)
        return

    delimiter = "\t" if path.suffix == ".tsv" else ","
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter=delimiter)
        writer.writerow(headers)
        writer.writerows(rows)
