import csv
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZipFile

import openpyxl
import pytest

from xic_extractor.sample_metadata import (
    SAMPLE_METADATA_COLUMNS,
    SampleMetadata,
    load_and_match_sample_metadata,
    match_sample_metadata,
)


def write_sequence(path, names):
    rows = [["ID", "File Name", "Instrument Method", "Sample", "Inj"]]
    rows.extend([order, name, "", name, "10"] for order, name in names)
    if path.suffix == ".docx":
        xml_rows = "".join(
            "<w:tr>"
            + "".join(
                f"<w:tc><w:p><w:r><w:t>{escape(str(cell))}</w:t></w:r></w:p></w:tc>"
                for cell in row
            )
            + "</w:tr>"
            for row in rows
        )
        with ZipFile(path, "w") as archive:
            archive.writestr(
                "word/document.xml",
                '<w:document xmlns:w="http://schemas.openxmlformats.org/'
                'wordprocessingml/2006/main"><w:body><w:tbl>'
                + xml_rows
                + "</w:tbl></w:body></w:document>",
            )
    elif path.suffix == ".xlsx":
        workbook = openpyxl.Workbook()
        for row in rows:
            workbook.active.append(row)
        workbook.save(path)
        workbook.close()
    else:
        with path.open("w", encoding="utf-8", newline="") as handle:
            csv.writer(handle).writerows(rows)


@pytest.mark.parametrize("suffix", [".csv", ".xlsx", ".docx"])
def test_formats_share_matching_and_repeated_injection_order(tmp_path, suffix):
    path = tmp_path / f"sequence{suffix}"
    # Deliberately shuffled rows and RAW paths: the printed injection order,
    # rather than either container's order, binds repeated names.
    write_sequence(
        path,
        [
            (30, "QC"),
            (3, "Liver tissue A1"),
            (10, "QC"),
            (4, "Sample A1.raw"),
            (20, "QC"),
            (40, "Missing"),
            (50, "Collision X"),
        ],
    )
    report = load_and_match_sample_metadata(
        path,
        ["QC_3", "LiverA1", "SampleA1", "QC_1", "QC_2", "Collision_X", "CollisionX"],
    )
    matched = {
        row.raw_stem: row.injection_order
        for row in report.rows
        if row.raw_stem in report.matched_raw_stems
    }
    assert matched == {"QC_3": 30, "LiverA1": 3, "SampleA1": 4, "QC_1": 10, "QC_2": 20}
    assert report.ambiguous_sequence_names == ("Collision X",)
    assert report.unmatched_sequence_names == ("Missing",)
    assert report.unmatched_raw_stems == ("Collision_X", "CollisionX")


def test_persisted_metadata_binds_only_exact_raw_stems(tmp_path: Path):
    path = tmp_path / "samples.tsv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=SAMPLE_METADATA_COLUMNS, delimiter="\t"
        )
        writer.writeheader()
        for name, stem, role, group, excluded, reason in [
            ("First", "Sample_A", "study_sample", "Case", "false", ""),
            ("Second", "samplea", "study_sample", "Control", "true", "bad injection"),
            ("QC_1", "QC", "qc", "", "true", "not the numbered RAW"),
        ]:
            writer.writerow(
                dict(
                    schema_version="sample_metadata",
                    sample_name=name,
                    raw_stem=stem,
                    sample_role=role,
                    group=group,
                    excluded=excluded,
                    exclusion_reason=reason,
                )
            )
    report = load_and_match_sample_metadata(path, ["Sample_A", "SampleA", "QC_1"])
    assert report.matched_raw_stems == ("Sample_A", "SampleA")
    assert report.unmatched_sequence_names == ("QC",)
    assert report.ambiguous_sequence_names == ()
    assert [(r.raw_stem, r.group, r.excluded) for r in report.rows] == [
        ("Sample_A", "Case", False),
        ("SampleA", "Control", True),
        ("QC_1", "", False),
    ]


def test_numbered_source_names_are_not_reassigned_by_order():
    rows = [
        SampleMetadata("First", "QC_2", injection_order=1),
        SampleMetadata("Second", "QC_1", injection_order=2),
    ]
    report = match_sample_metadata(rows, ["QC_1", "QC_2"])
    assert [(r.raw_stem, r.sample_name) for r in report.rows] == [
        ("QC_1", "Second"),
        ("QC_2", "First"),
    ]


@pytest.mark.parametrize("orders", [(1,), (1, 1), (1, 2, 3)])
def test_unproven_repeated_pairing_remains_ambiguous(orders):
    rows = [
        SampleMetadata(f"injection {i}", "QC", injection_order=order)
        for i, order in enumerate(orders)
    ]
    report = match_sample_metadata(rows, ["QC_1", "QC_2"])
    assert report.matched_raw_stems == ()
    assert len(report.ambiguous_sequence_names) == len(rows)


def test_alias_collisions_do_not_claim_a_raw_by_iteration_order():
    rows = [SampleMetadata("A", "Sample A"), SampleMetadata("B", "Sample_A")]
    report = match_sample_metadata(rows, ["SampleA"])
    assert report.matched_raw_stems == ()
    assert report.ambiguous_sequence_names == ("Sample A", "Sample_A")
