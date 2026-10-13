import json
from pathlib import Path

import pytest

from scripts import validate_sample_metadata
from xic_extractor.sample_metadata import (
    SAMPLE_METADATA_COLUMNS,
    SAMPLE_METADATA_SCHEMA_VERSION,
    SampleMetadataError,
    is_sample_metadata_source,
    load_sample_metadata,
    parse_sample_metadata,
    sample_metadata_to_injection_order,
    summarize_sample_metadata,
)


def test_parse_sample_metadata_validates_roles_and_injection_order() -> None:
    rows = parse_sample_metadata(
        [
            _row(
                sample_name="TumorBC001_DNA",
                raw_stem="TumorBC001_DNA",
                injection_order="1",
                sample_role="study_sample",
                batch_id="batch-a",
                matrix_type="DNA",
                group="tumor",
            ),
            _row(
                sample_name="QC1",
                raw_stem="Breast_Cancer_Tissue_pooled_QC1",
                injection_order="2",
                sample_role="pooled_qc",
            ),
        ]
    )

    assert rows[0].sample_role == "study_sample"
    assert rows[0].injection_order == 1
    assert sample_metadata_to_injection_order(rows) == {
        "TumorBC001_DNA": 1,
        "QC1": 2,
        "Breast_Cancer_Tissue_pooled_QC1": 2,
    }
    assert summarize_sample_metadata(rows) == {
        "schema_version": SAMPLE_METADATA_SCHEMA_VERSION,
        "sample_count": 2,
        "with_injection_order_count": 2,
        "excluded_count": 0,
        "role_counts": {
            "blank": 0,
            "calibrator": 0,
            "pooled_qc": 1,
            "qc": 0,
            "solvent": 0,
            "study_sample": 1,
            "system_suitability": 0,
            "unknown": 0,
        },
    }


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


def test_sample_metadata_rejects_excluded_without_reason() -> None:
    with pytest.raises(
        SampleMetadataError,
        match="excluded samples require exclusion_reason",
    ):
        parse_sample_metadata(
            [
                _row(
                    sample_name="S1",
                    sample_role="study_sample",
                    excluded="TRUE",
                )
            ]
        )


def test_load_sample_metadata_rejects_wrong_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "sample_metadata.tsv"
    _write_tsv(
        path,
        [
            _row(
                schema_version="legacy",
                sample_name="S1",
                sample_role="study_sample",
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


def test_validate_sample_metadata_cli_prints_summary_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "sample_metadata.tsv"
    _write_tsv(
        path,
        [
            _row(
                sample_name="S1",
                raw_stem="Raw1",
                sample_role="study_sample",
                injection_order="1",
            )
        ],
    )

    assert validate_sample_metadata.main([str(path), "--summary-json"]) == 0
    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert summary["sample_count"] == 1
    assert summary["injection_order_alias_count"] == 2


def test_validate_sample_metadata_cli_reports_validation_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "sample_metadata.tsv"
    _write_tsv(
        path,
        [
            _row(
                sample_name="S1",
                sample_role="unsupported",
            )
        ],
    )

    assert validate_sample_metadata.main([str(path)]) == 2
    captured = capsys.readouterr()
    assert "unsupported sample_role" in captured.err


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
