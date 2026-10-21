import csv
from collections import Counter
from pathlib import Path

import pytest

from xic_extractor.sample_groups import (
    canonicalize_sample_groups,
    resolve_sample_group,
    sample_group_key,
    suggest_sample_groups,
)
from xic_extractor.sample_metadata import SampleMetadata, parse_sample_metadata


@pytest.mark.parametrize(
    ("group", "role", "expected"),
    [
        (" Case   A ", "pooled_qc", "Case A"),
        ("QC", "study_sample", "QC"),
        ("Other", "qc", "QC"),
        (" other ", "pooled_qc", "QC"),
        ("", "qc", "QC"),
        ("", "study_sample", "Other"),
        ("", "blank", "Other"),
    ],
)
def test_group_uses_explicit_annotation_then_role(group, role, expected):
    metadata = SampleMetadata("TumorQC_1", "TumorQC_1", group=group, sample_role=role)
    assert resolve_sample_group(metadata) == expected


def test_missing_metadata_uses_supplied_role_not_name():
    assert resolve_sample_group() == "Other"
    assert resolve_sample_group(sample_role="pooled_qc") == "QC"


def test_group_key_and_first_display_spelling_are_shared():
    labels = [" Tumor ", " tumor ", "TUMOR", "Case  A", " case a ", "Other", ""]
    assert canonicalize_sample_groups(labels) == (
        "Tumor",
        "Tumor",
        "Tumor",
        "Case A",
        "Case A",
        "",
        "",
    )
    assert {sample_group_key(label) for label in labels[:3]} == {"tumor"}
    assert sample_group_key(" OTHER ") == sample_group_key("") == ""
    assert sample_group_key("QC") == "qc"
    rows = parse_sample_metadata(
        {
            "schema_version": "sample_metadata",
            "sample_name": f"S{i}",
            "raw_stem": f"S{i}",
            "group": group,
        }
        for i, group in enumerate(labels)
    )
    assert tuple(row.group for row in rows) == canonicalize_sample_groups(labels)


def test_suggestions_are_generic_and_do_not_apply_role_rules():
    names = ["Liver_AB123_1.raw", " liver-XY999-2.mzML", "QC_1", "ABC123_2.raw"]
    assert suggest_sample_groups(names) == {
        "Liver_AB123_1.raw": "Liver",
        " liver-XY999-2.mzML": "Liver",
        "QC_1": "QC",
        "ABC123_2.raw": "",
    }


def test_suggestions_for_real_sequence_names():
    # Minimal name-only fixture from the 118-injection Word sequence in #877.
    # Expected labels are the documented token-removal rule, not role inference.
    with (Path(__file__).parent / "fixtures" / "sample_group_names.tsv").open(
        encoding="utf-8", newline=""
    ) as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    assert len(rows) == 118
    names = [row["name"] for row in rows]
    suggestions = suggest_sample_groups(names)
    assert suggestions == {row["name"]: row["suggested_group"] for row in rows}
    assert suggest_sample_groups(names) == suggestions
    assert Counter(suggestions[name] for name in names) == {
        "Blank waste": 1,
        "Blank": 12,
        "SDOLEK pretest": 1,
        "250ppb Mix STDs": 4,
        "Breast Cancer Tissue pooled QC": 7,
        "Tumor tissue DNA": 28,
        "Tumor tissue RNA": 2,
        "Tumor tissue DNA +RNA": 2,
        "Normal tissue DNA": 32,
        "Benign fat DNA": 20,
        "SDOLEK": 6,
        "SDO posttest": 1,
        "ColumnWash": 1,
        "StopFlow": 1,
    }
