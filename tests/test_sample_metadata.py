from pathlib import Path

import pytest

from xic_extractor.sample_metadata import (
    SAMPLE_METADATA_COLUMNS,
    SAMPLE_METADATA_SCHEMA_VERSION,
    SampleMetadataError,
    is_sample_metadata_source,
    load_sample_metadata,
    parse_sample_metadata,
)


def test_parse_sample_metadata_validates_roles_and_injection_order() -> None:
    rows = parse_sample_metadata(
        [
            _row(
                sample_name="TumorBC001_DNA",
                raw_stem="TumorBC001_DNA",
                injection_order="1",
                sample_role="sample",
                batch_id="batch-a",
                matrix_type="DNA",
                group="tumor",
            ),
            _row(
                sample_name="QC1",
                raw_stem="Breast_Cancer_Tissue_pooled_QC1",
                injection_order="2",
                sample_role="qc",
            ),
        ]
    )

    assert rows[0].sample_role == "sample"
    assert rows[0].injection_order == 1


@pytest.mark.parametrize(
    ("written", "read"),
    [
        ("study_sample", "sample"),
        ("pooled_qc", "qc"),
        ("calibrator", "std"),
        ("solvent", "other"),
        ("system_suitability", "other"),
        ("QC", "qc"),
        ("unknown", None),
        ("", None),
    ],
)
def test_earlier_role_names_are_read_as_current_roles(written, read) -> None:
    (row,) = parse_sample_metadata([_row(sample_name="S1", sample_role=written)])
    assert row.sample_role == read


def test_sample_metadata_rejects_an_unknown_role_word() -> None:
    with pytest.raises(SampleMetadataError, match="unsupported sample_role"):
        parse_sample_metadata([_row(sample_name="S1", sample_role="wash")])


def test_sample_metadata_rejects_duplicate_raw_stem() -> None:
    with pytest.raises(SampleMetadataError, match="duplicate raw_stem"):
        parse_sample_metadata(
            [
                _row(sample_name="S1", raw_stem="Raw1"),
                _row(sample_name="S2", raw_stem="Raw1"),
            ]
        )


def test_sample_metadata_rejects_sample_name_raw_stem_alias_collision() -> None:
    with pytest.raises(SampleMetadataError, match="sample metadata alias collision"):
        parse_sample_metadata(
            [
                _row(sample_name="S1", raw_stem="Raw1", injection_order="1"),
                _row(sample_name="Raw1", raw_stem="Raw2", injection_order="1"),
            ]
        )


def test_sample_metadata_rejects_raw_stem_sample_name_alias_collision() -> None:
    with pytest.raises(SampleMetadataError, match="sample metadata alias collision"):
        parse_sample_metadata(
            [
                _row(sample_name="Raw1", raw_stem="Raw2"),
                _row(sample_name="S1", raw_stem="Raw1"),
            ]
        )


def test_sample_metadata_allows_exclusion_without_a_reason() -> None:
    rows = parse_sample_metadata(
        [_row(sample_name="S1", sample_role="sample", excluded="TRUE")]
    )
    assert rows[0].excluded is True
    assert rows[0].exclusion_reason == ""


def test_load_sample_metadata_rejects_wrong_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "sample_metadata.tsv"
    _write_tsv(
        path,
        [
            _row(
                schema_version="legacy",
                sample_name="S1",
                sample_role="sample",
            )
        ],
    )

    with pytest.raises(SampleMetadataError, match="unsupported schema_version"):
        load_sample_metadata(path)


def test_is_sample_metadata_source_detects_schema_header(tmp_path: Path) -> None:
    sample_metadata_path = tmp_path / "sample_metadata.tsv"
    legacy_order_path = tmp_path / "SampleInfo.csv"
    _write_tsv(sample_metadata_path, [_row(sample_name="S1")])
    legacy_order_path.write_text(
        "Sample_Name,Injection_Order\nS1,1\n",
        encoding="utf-8",
    )

    assert is_sample_metadata_source(sample_metadata_path) is True
    assert is_sample_metadata_source(legacy_order_path) is False


def _row(**overrides: str) -> dict[str, str]:
    row = {header: "" for header in SAMPLE_METADATA_COLUMNS}
    row["schema_version"] = SAMPLE_METADATA_SCHEMA_VERSION
    row["sample_role"] = "unknown"
    row.update(overrides)
    return row


def _write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    path.write_text(
        "\t".join(SAMPLE_METADATA_COLUMNS)
        + "\n"
        + "\n".join(
            "\t".join(row.get(header, "") for header in SAMPLE_METADATA_COLUMNS)
            for row in rows
        )
        + "\n",
        encoding="utf-8",
    )
