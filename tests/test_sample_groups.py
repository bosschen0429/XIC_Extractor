import pytest

from xic_extractor.sample_groups import (
    canonicalize_sample_groups,
    resolve_sample_group,
    sample_group_key,
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
        ("", "study_sample", "Sample"),
        ("", "blank", "Blank"),
        ("Other", "solvent", "Solvent"),
        ("", "unknown", "Other"),
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
