from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

import openpyxl
import pytest

from xic_extractor.sample_metadata import (
    SampleMetadata,
    SampleMetadataError,
    load_and_match_sample_metadata,
    load_sample_metadata,
    match_sequence_to_raw_stems,
    normalize_for_matching,
    parse_sequence_file,
)


def _create_synthetic_docx(path: Path) -> None:
    """Create a minimal synthetic docx with a sequence table and a remark table."""
    xml_content = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:tbl>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Parameter</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Value</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Col Temp</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>40 C</w:t></w:r></w:p></w:tc>
      </w:tr>
    </w:tbl>
    <w:tbl>
      <w:tr>
        <w:tc><w:p><w:r><w:t>ID</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Sample</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Storage</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:p><w:r><w:t>999</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Inventory only</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Freezer A</w:t></w:r></w:p></w:tc>
      </w:tr>
    </w:tbl>
    <w:tbl>
      <w:tr>
        <w:tc><w:p><w:r><w:t>ID</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>File Name</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Instrument Method</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Sample</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Inj</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Remark: Start of Batch 1</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:p><w:r><w:t>1</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Blank_1</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Method_Blank</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>DIW</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>20</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:p><w:r><w:t>2</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>QC_Pool_1</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Method_CID</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Pooled QC</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>10</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:p><w:r><w:t>3</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Tumor tissue BC001_DNA</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Method_CID</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>BC001 Tumor Tissue + ISTDs</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>20</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:p><w:r><w:t>4</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Normal tissue BC001_DNA</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Method_CID</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>BC001 Normal Tissue + ISTDs</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>20</w:t></w:r></w:p></w:tc>
      </w:tr>
    </w:tbl>
  </w:body>
</w:document>"""

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", xml_content)
    path.write_bytes(buf.getvalue())


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


def test_parse_docx_sequence(tmp_path: Path) -> None:
    docx_file = tmp_path / "test_run.docx"
    _create_synthetic_docx(docx_file)

    rows = parse_sequence_file(docx_file)
    assert len(rows) == 4

    # Row 1: Blank
    assert rows[0].injection_order == 1
    assert rows[0].raw_stem == "Blank_1"
    assert rows[0].sample_role == "blank"

    # Row 2: Pooled QC
    assert rows[1].injection_order == 2
    assert rows[1].raw_stem == "QC_Pool_1"
    assert rows[1].sample_role == "pooled_qc"

    # Row 3: Tumor
    assert rows[2].injection_order == 3
    assert rows[2].raw_stem == "Tumor tissue BC001_DNA"
    assert rows[2].sample_role == "study_sample"
    assert rows[2].group == "Tumor"

    # Row 4: Normal
    assert rows[3].injection_order == 4
    assert rows[3].group == "Normal"


def test_word_sequence_uses_shared_match_statuses(tmp_path: Path, monkeypatch) -> None:
    docx_file = tmp_path / "test_run.docx"
    _create_synthetic_docx(docx_file)

    from xic_extractor.instrument_qc import sequence_manifest

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
    assert rows[0].sample_role == "pooled_qc"
    assert rows[0].batch_id == "BatchA"

    assert rows[1].injection_order == 2
    assert rows[1].sample_name == "Patient_01_DNA"
    assert rows[1].raw_stem == "P01_DNA"
    assert rows[1].sample_role == "study_sample"


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
    assert rows[1].sample_role == "study_sample"


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


def test_match_sequence_to_raw_stems() -> None:
    seq_rows = (
        SampleMetadata(
            sample_name="Tumor tissue BC001_DNA",
            raw_stem="Tumor tissue BC001_DNA",
            injection_order=8,
            sample_role="study_sample",
            group="Tumor",
        ),
        SampleMetadata(
            sample_name="Breast Cancer Tissue_pooled_QC_1",
            raw_stem="Breast Cancer Tissue_pooled_QC_1",
            injection_order=7,
            sample_role="pooled_qc",
            group="QC",
        ),
    )

    raw_stems = ["Breast_Cancer_Tissue_pooled_QC1", "TumorBC001_DNA", "Unknown_Sample"]
    matched = match_sequence_to_raw_stems(seq_rows, raw_stems)

    assert len(matched) == 3
    # Matched QC1
    assert matched[0].raw_stem == "Breast_Cancer_Tissue_pooled_QC1"
    assert matched[0].injection_order == 7
    assert matched[0].sample_role == "pooled_qc"

    # Matched Tumor
    assert matched[1].raw_stem == "TumorBC001_DNA"
    assert matched[1].injection_order == 8
    assert matched[1].group == "Tumor"

    # Unmatched fallback
    assert matched[2].raw_stem == "Unknown_Sample"
    assert matched[2].injection_order is None


def test_real_research_sequence_files() -> None:
    docx_path = Path(
        r"E:\Research\NTU cancer\2025台大乳癌組織數據for Jia"
        r"\20260105中研院台大Breast cancer tissue\20260105 中研院分析.docx"
    )
    if not docx_path.is_file():
        return

    rows = load_sample_metadata(docx_path)
    assert len(rows) >= 114

    raw_dir = Path(r"C:\Xcalibur\data\20260106_CSMU_NAA_Tissue_R")
    if raw_dir.is_dir():
        raw_stems = [p.stem for p in sorted(raw_dir.glob("*.raw"))]
        if len(raw_stems) == 85:
            matched = match_sequence_to_raw_stems(rows, raw_stems)
            assert len(matched) == 85
            matched_orders = [m for m in matched if m.injection_order is not None]
            assert len(matched_orders) == 85
            qc1 = next(m for m in matched if "qc1" in m.raw_stem.lower())
            assert qc1.injection_order == 7
            assert qc1.sample_role == "pooled_qc"
