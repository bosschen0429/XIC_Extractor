from __future__ import annotations

from pathlib import Path

from xic_extractor.diagnostics.row_completion_confidence_external import (
    validate_external_feature_table,
)


def test_external_pregate_is_not_available_when_absent() -> None:
    result = validate_external_feature_table(None)

    assert result.status == "not_available"
    assert result.mapping_status == "not_available"


def test_external_pregate_fails_missing_required_column(tmp_path: Path) -> None:
    table = tmp_path / "external.tsv"
    table.write_text(
        "external_tool\texternal_run_id\tsample_id\nMZmine\trun1\tS1\n",
        encoding="utf-8",
    )

    result = validate_external_feature_table(table)

    assert result.status == "FAIL"
    assert result.missing_evidence_code == "missing_required_column"
    assert "external_tool_version" in result.reason


def test_external_pregate_marks_schema_only_table_as_mapping_unavailable(
    tmp_path: Path,
) -> None:
    table = tmp_path / "external.tsv"
    table.write_text(
        "external_tool\texternal_run_id\texternal_tool_version\t"
        "external_adapter_version\tsample_id\tfeature_id\tmz\trt\t"
        "area_or_intensity\tarea_or_intensity_semantics\t"
        "mz_tolerance_unit\trt_tolerance_unit\tduplicate_feature_policy\t"
        "missing_value_policy\n"
        "MZmine\trun1\t4.9.14\tcontract\tS1\tF1\t100.1\t5.2\t"
        "12345\tarea\tppm\tminutes\tbest_quality_score\tblank_is_missing\n",
        encoding="utf-8",
    )

    result = validate_external_feature_table(table)

    assert result.status == "schema_valid"
    assert result.mapping_status == "schema_valid_mapping_quality_unavailable"
    assert result.row_count == 1
