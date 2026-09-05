from __future__ import annotations

from pathlib import Path

from xic_extractor.diagnostics.row_completion_confidence_schema import (
    DISAGREEMENT_COLUMNS,
    NO_AUTHORITY_STATEMENT,
    SCHEMA_VERSION,
    SENTINEL_COLUMNS,
    SUMMARY_COLUMNS,
    artifact_descriptor,
    build_artifact_manifest,
    freshness_decision,
)


def test_artifact_descriptor_records_hash_relpath_size_and_rows(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "run" / "alignment_matrix.tsv"
    artifact.parent.mkdir()
    artifact.write_text("feature_family_id\tSampleA\nFAM1\t10\n", encoding="utf-8")

    descriptor = artifact_descriptor(
        artifact,
        root=tmp_path,
        run_id="synthetic_run_id",
        generation_context="synthetic_run",
    )

    assert descriptor.schema_version == SCHEMA_VERSION
    assert descriptor.run_id == "synthetic_run_id"
    assert descriptor.relpath == "run/alignment_matrix.tsv"
    assert descriptor.size_bytes == artifact.stat().st_size
    assert descriptor.row_count == 1
    assert len(descriptor.sha256) == 64
    assert descriptor.generation_context == "synthetic_run"


def test_artifact_descriptor_fails_for_outside_root(tmp_path: Path) -> None:
    artifact = tmp_path.parent / "alignment_matrix.tsv"
    artifact.write_text("feature_family_id\tSampleA\nFAM1\t10\n", encoding="utf-8")

    try:
        artifact_descriptor(
            artifact,
            root=tmp_path,
            run_id="synthetic_run_id",
            generation_context="synthetic_run",
        )
    except ValueError as exc:
        assert "outside root" in str(exc)
    else:
        raise AssertionError("artifact_descriptor should fail outside root")


def test_manifest_fails_closed_for_missing_required_artifact(tmp_path: Path) -> None:
    result = build_artifact_manifest(
        {"alignment_matrix": tmp_path / "missing.tsv"},
        root=tmp_path,
        run_id="synthetic_run_id",
        generation_context="synthetic_run",
    )

    assert result.run_ok is False
    assert result.gate_ok is False
    assert result.status == "INCONCLUSIVE"
    assert result.missing_evidence_code == "missing_required_artifact"
    assert "missing.tsv" in result.reason


def test_manifest_fails_closed_for_outside_root_artifact(tmp_path: Path) -> None:
    artifact = tmp_path.parent / "alignment_matrix.tsv"
    artifact.write_text("feature_family_id\tSampleA\nFAM1\t10\n", encoding="utf-8")

    result = build_artifact_manifest(
        {"alignment_matrix": artifact},
        root=tmp_path,
        run_id="synthetic_run_id",
        generation_context="synthetic_run",
    )

    assert result.run_ok is False
    assert result.gate_ok is False
    assert result.status == "INCONCLUSIVE"
    assert result.missing_evidence_code == "stale_artifact_manifest"
    assert "outside root" in result.reason


def test_manifest_fails_closed_for_unknown_generation_context(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "alignment_matrix.tsv"
    artifact.write_text("feature_family_id\tSampleA\nFAM1\t10\n", encoding="utf-8")

    result = build_artifact_manifest(
        {"alignment_matrix": artifact},
        root=tmp_path,
        run_id="synthetic_run_id",
        generation_context="unknown",
    )

    assert result.run_ok is False
    assert result.gate_ok is False
    assert result.status == "INCONCLUSIVE"
    assert result.missing_evidence_code == "stale_artifact_manifest"


def test_manifest_fails_closed_for_empty_required_artifact_set(tmp_path: Path) -> None:
    result = build_artifact_manifest(
        {},
        root=tmp_path,
        run_id="synthetic_run_id",
        generation_context="synthetic_run",
    )

    assert result.run_ok is False
    assert result.gate_ok is False
    assert result.status == "INCONCLUSIVE"
    assert result.missing_evidence_code == "missing_required_artifact"
    assert "empty" in result.reason


def test_freshness_decision_separates_artifact_only_from_raw_rerun() -> None:
    docs_only = freshness_decision("docs_only")
    metric_change = freshness_decision("benchmark_metric_logic")
    generation_change = freshness_decision("alignment_generation_code")
    product_gate = freshness_decision("product_gate_packet")
    unknown = freshness_decision("unclassified_change")

    assert docs_only.required_action == "no_rerun"
    assert metric_change.required_action == "artifact_only_rerun"
    assert generation_change.required_action == "fresh_fixture_cohort_required"
    assert (
        product_gate.required_action
        == "fresh_full_cohort_required_after_fixture_cohort"
    )
    assert unknown.required_action == "inconclusive"


def test_output_columns_include_status_reason_manifest_and_authority() -> None:
    assert SUMMARY_COLUMNS == (
        "schema_version",
        "run_id",
        "lane",
        "metric_name",
        "status",
        "current_value",
        "baseline_value",
        "delta",
        "direction",
        "evidence_source",
        "artifact_relpath",
        "artifact_sha256",
        "reason",
        "missing_evidence_code",
        "input_artifact_manifest",
        "no_authority_statement",
    )
    assert SENTINEL_COLUMNS == (
        "schema_version",
        "run_id",
        "rank",
        "case_id",
        "lane",
        "case_type",
        "feature_family_id",
        "sample_stem",
        "production_safety_status",
        "review_utility_status",
        "issue_class",
        "severity_score",
        "evidence_source",
        "recommended_action",
        "requires_manual_review",
        "reason",
    )
    assert DISAGREEMENT_COLUMNS == (
        "schema_version",
        "run_id",
        "disagreement_id",
        "external_tool",
        "external_run_id",
        "mapping_status",
        "sample_id",
        "sample_stem",
        "feature_family_id",
        "external_feature_id",
        "mz_delta",
        "rt_delta_min",
        "classification",
        "reason",
    )
    assert "diagnostic_only" in NO_AUTHORITY_STATEMENT
    assert "ProductWriter authority" in NO_AUTHORITY_STATEMENT
    assert "control-plane/status index" in NO_AUTHORITY_STATEMENT
