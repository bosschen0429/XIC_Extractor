from __future__ import annotations

import pytest

from xic_extractor.diagnostics.shared_peak_identity_explanation.schema import (
    ALLOWED_BY_FIELD,
    BLAST_RADIUS_MANIFEST_COLUMNS,
    BLAST_RADIUS_MANIFEST_SCHEMA_VERSION,
    BLAST_RADIUS_SUMMARY_COLUMNS,
    BLAST_RADIUS_SUMMARY_SCHEMA_VERSION,
    HYPOTHESIS_CONSISTENCY_COLUMNS,
    HYPOTHESIS_CONSISTENCY_SCHEMA_VERSION,
    HYPOTHESIS_CONSISTENCY_SUMMARY_COLUMNS,
    HYPOTHESIS_CONSISTENCY_SUMMARY_SCHEMA_VERSION,
    MACHINE_EVIDENCE_SUPPORT_COLUMNS,
    MACHINE_EVIDENCE_SUPPORT_SCHEMA_VERSION,
    MANUAL_REASON_TAGS,
    SHADOW_ALIGNMENT_SUMMARY_COLUMNS,
    SHADOW_ALIGNMENT_SUMMARY_SCHEMA_VERSION,
    SHADOW_LABEL_COLUMNS,
    SHADOW_LABEL_SCHEMA_VERSION,
    SHADOW_READINESS_COLUMNS,
    SHADOW_READINESS_SCHEMA_VERSION,
    validate_semicolon_tokens,
    validate_source_row_ids,
    validate_token,
)


def test_schema_allows_slice0_sentinel_tokens() -> None:
    validate_token("not_applicable", "manual_label")
    validate_token("not_applicable", "machine_current_label")
    validate_token("not_applicable", "machine_match_status")
    validate_token("slice0", "slice")
    validate_token("not_run_slice0", "blast_radius_assessed")
    validate_token("unassessed", "max_overfit_risk")


def test_semicolon_tokens_reject_unknown_or_whitespace() -> None:
    validate_semicolon_tokens(
        "rt_close;shape_complete",
        field="manual_reason_tags",
        allowed_tokens=MANUAL_REASON_TAGS,
    )
    with pytest.raises(ValueError):
        validate_semicolon_tokens(
            "rt_close;bad token",
            field="manual_reason_tags",
            allowed_tokens=MANUAL_REASON_TAGS,
        )
    with pytest.raises(ValueError):
        validate_semicolon_tokens(
            "rt_close;unknown",
            field="manual_reason_tags",
            allowed_tokens=MANUAL_REASON_TAGS,
        )


def test_matched_source_row_ids_are_dynamic_traceable_ids() -> None:
    validate_source_row_ids(
        "alignment_cells.tsv:1;alignment_review.tsv:2",
        {"alignment_cells.tsv:1", "alignment_review.tsv:2"},
    )
    with pytest.raises(ValueError):
        validate_source_row_ids("alignment_cells.tsv:999", {"alignment_cells.tsv:1"})
    assert "matched_source_row_ids" not in ALLOWED_BY_FIELD


def test_slice1_blast_radius_schema_constants_match_contract() -> None:
    assert (
        BLAST_RADIUS_MANIFEST_SCHEMA_VERSION
        == "shared_peak_identity_blast_radius_manifest"
    )
    assert BLAST_RADIUS_MANIFEST_COLUMNS == (
        "manifest_schema_version",
        "artifact_id",
        "artifact_role",
        "artifact_path",
        "artifact_sha256",
        "expected_artifact_sha256",
        "freshness_basis",
        "artifact_schema_version",
        "artifact_status",
        "row_count",
        "sample_count",
        "family_count",
        "available_required_fields",
        "missing_required_fields",
        "generated_from_existing_artifact",
        "notes",
    )
    assert (
        BLAST_RADIUS_SUMMARY_SCHEMA_VERSION
        == "shared_peak_identity_blast_radius_summary"
    )
    assert BLAST_RADIUS_SUMMARY_COLUMNS == (
        "summary_schema_version",
        "scope",
        "artifact_id",
        "evidence_gap_class",
        "seed_count",
        "context_row_count",
        "non_seed_same_family_count",
        "assessed_row_count",
        "all_available_row_count",
        "compatible_row_count",
        "unavailable_field_count",
        "contradictory_count",
        "ambiguous_machine_match_count",
        "compatible_fraction",
        "contradictory_fraction",
        "ambiguous_fraction",
        "unavailable_fraction",
        "overfit_risk",
        "example_oracle_row_ids",
        "example_feature_family_ids",
    )


def test_slice1_tokens_are_allowed() -> None:
    for token in {
        "present_current",
        "present_hash_unpinned",
        "present_stale_hash_mismatch",
        "present_missing_required_fields",
        "missing",
        "schema_unsupported",
        "not_assessed",
        "unavailable",
    }:
        validate_token(token, "artifact_status")

    for token in {
        "seed",
        "non_seed_same_family",
        "all_available_fixture_cohort",
        "all_available_full_cohort",
        "manual_label",
        "overall",
    }:
        validate_token(token, "scope")

    for token in {
        "manual_oracle_fixture",
        "alignment_review",
        "alignment_cells",
        "tier2_trace_sidecar",
        "identity_diagnostic",
        "targeted_context",
        "blast_radius_context",
    }:
        validate_token(token, "artifact_role")

    for token in {
        "slice0_evidence_vector",
        "expected_blast_radius_manifest",
        "not_available",
    }:
        validate_token(token, "freshness_basis")

    for token in {"none", "low", "medium", "high", "unassessed"}:
        validate_token(token, "overfit_risk")

    validate_token("slice1", "slice")
    validate_token("present_current", "blast_radius_assessed")


def test_shadow_shadow_label_schema_constants_match_contract() -> None:
    assert SHADOW_LABEL_SCHEMA_VERSION == "shared_peak_identity_shadow_label"
    assert SHADOW_LABEL_COLUMNS == (
        "shadow_label_schema_version",
        "oracle_row_id",
        "feature_family_id",
        "sample_id",
        "manual_label",
        "manual_confidence",
        "machine_current_label",
        "machine_match_status",
        "evidence_gap_class",
        "shadow_label",
        "shadow_alignment_status",
        "manual_machine_direction",
        "evidence_chain_gap",
        "required_evidence_to_promote",
        "diagnostic_only",
    )
    assert (
        SHADOW_ALIGNMENT_SUMMARY_SCHEMA_VERSION
        == "shared_peak_identity_shadow_alignment_summary"
    )
    assert SHADOW_ALIGNMENT_SUMMARY_COLUMNS == (
        "shadow_summary_schema_version",
        "scope",
        "manual_label",
        "row_count",
        "aligned_count",
        "partial_count",
        "contradicted_count",
        "unjudgeable_count",
        "context_only_count",
        "unresolved_count",
        "alignment_fraction",
        "dominant_gap_classes",
        "recommended_next_action",
    )
    assert SHADOW_READINESS_SCHEMA_VERSION == "shared_peak_identity_shadow_readiness"
    assert SHADOW_READINESS_COLUMNS == (
        "shadow_readiness_schema_version",
        "shadow_mode",
        "shadow_gate_status",
        "readiness_label",
        "seed_rows_total",
        "shadow_rows_total",
        "aligned_or_partial_rows",
        "contradicted_rows",
        "context_only_rows",
        "human_unjudgeable_rows",
        "alignment_fraction",
        "blast_radius_assessed",
        "max_overfit_risk",
        "blast_radius_stale_artifact_count",
        "semantic_generalization_evidence",
        "machine_evidence_basis",
        "machine_evidence_supported_rows",
        "machine_observed_partial_rows",
        "machine_observed_conflict_rows",
        "machine_proxy_only_rows",
        "manual_oracle_derived_rows",
        "machine_evidence_coverage_fraction",
        "machine_evidence_blockers",
        "machine_only_labeler_ready",
        "clear_answer",
        "next_action",
    )
    assert (
        MACHINE_EVIDENCE_SUPPORT_SCHEMA_VERSION
        == "shared_peak_identity_machine_evidence_support"
    )
    assert MACHINE_EVIDENCE_SUPPORT_COLUMNS == (
        "machine_evidence_support_schema_version",
        "oracle_row_id",
        "feature_family_id",
        "sample_id",
        "manual_label",
        "machine_current_label",
        "shadow_label",
        "shadow_alignment_status",
        "status_label_alignment_status",
        "rt_basis_status",
        "shape_basis_status",
        "pattern_basis_status",
        "opportunity_basis_status",
        "scope_basis_status",
        "negative_evidence_basis_status",
        "negative_evidence_class",
        "negative_evidence_detail",
        "observed_machine_metrics",
        "manual_derived_facts",
        "missing_machine_evidence",
        "literature_support_refs",
        "evidence_support_status",
        "diagnostic_only",
    )


def test_shadow_shadow_label_tokens_are_allowed() -> None:
    for token in {
        "manual_like_pass_candidate",
        "manual_like_suspect_candidate",
        "manual_like_fail_candidate",
        "low_opportunity_supported",
        "rt_pattern_conflict_blocked",
        "human_unjudgeable_like",
        "delta_mass_context_only",
        "unresolved_gap",
    }:
        validate_token(token, "shadow_label")

    for token in {
        "aligned",
        "partial",
        "contradicted",
        "unjudgeable",
        "context_only",
        "unresolved",
    }:
        validate_token(token, "shadow_alignment_status")

    for token in {
        "machine_agrees",
        "machine_too_conservative",
        "machine_too_permissive",
        "ambiguous_policy",
        "context_only",
        "unresolved",
    }:
        validate_token(token, "manual_machine_direction")

    for token in {
        "shadow_ready_candidate",
        "exploratory_only",
        "blocked_by_vocabulary",
        "blocked_by_overfit_risk",
    }:
        validate_token(token, "shadow_gate_status")

    validate_token("diagnostic_only", "readiness_label")
    validate_token("shadow_label_alignment", "shadow_mode")

    for token in {
        "proxy_agrees",
        "proxy_partial",
        "proxy_contradicts",
        "not_available",
        "not_evaluable",
        "context_only",
    }:
        validate_token(token, "status_label_alignment_status")

    for field in {
        "rt_basis_status",
        "shape_basis_status",
        "pattern_basis_status",
        "opportunity_basis_status",
        "scope_basis_status",
    }:
        for token in {
            "machine_observed",
            "machine_proxy",
            "manual_oracle_derived",
            "mixed",
            "not_available",
            "not_applicable",
        }:
            validate_token(token, field)

    for token in {
        "machine_observed_sufficient",
        "machine_observed_partial",
        "machine_observed_conflict",
        "machine_proxy_only",
        "manual_derived_only",
        "blocked_missing_metric",
        "not_evaluable",
        "context_only",
    }:
        validate_token(token, "evidence_support_status")

    for token in {
        "machine_observed_sufficient",
        "machine_observed_partial",
        "machine_proxy_or_manual_derived",
        "not_assessed",
    }:
        validate_token(token, "machine_evidence_basis")


def test_hypothesis_consistency_schema_tokens_are_allowed() -> None:
    assert (
        HYPOTHESIS_CONSISTENCY_SCHEMA_VERSION
        == "shared_peak_identity_hypothesis_consistency"
    )
    assert HYPOTHESIS_CONSISTENCY_COLUMNS[:8] == (
        "hypothesis_consistency_schema_version",
        "feature_family_id",
        "sample_stem",
        "peak_hypothesis_id",
        "peak_hypothesis_status",
        "product_unit_scope",
        "product_selection_action",
        "product_selection_blocker",
    )
    assert (
        HYPOTHESIS_CONSISTENCY_SUMMARY_SCHEMA_VERSION
        == "shared_peak_identity_hypothesis_consistency_summary"
    )
    assert HYPOTHESIS_CONSISTENCY_SUMMARY_COLUMNS[:6] == (
        "hypothesis_consistency_summary_schema_version",
        "scope",
        "row_count",
        "consistent_count",
        "conflict_count",
        "incomplete_count",
    )
    validate_token("full_matrix", "scope")
    validate_token("sidecar_key_union", "scope")
    validate_token("sample_required_tag_observed", "family_required_tag_status")
    validate_token("dda_missing_nl_not_dispositive", "ms2_opportunity_status")
    validate_token("consistent", "evidence_consistency_status")
    validate_token("peak_hypothesis_ready", "split_readiness_status")
    validate_token("split_peak_hypothesis", "hypothesis_next_action")
    validate_token("review_required", "consistency_gate_status")
    with pytest.raises(ValueError):
        validate_token("family_identity_ready", "split_readiness_status")


def test_slice1_schema_rejects_noncanonical_review_drift_tokens() -> None:
    for field, token in (
        ("freshness_basis", "slice0_evidence_vectors"),
        ("freshness_basis", "expected_manifest"),
        ("freshness_basis", "missing_expected_hash"),
        ("freshness_basis", "not_applicable"),
        ("artifact_role", "slice0_explanations"),
        ("artifact_role", "slice0_evidence_vectors"),
        ("artifact_role", "candidate_gate_fixture_cohort"),
        ("artifact_role", "tier2_coherence_fixture_cohort"),
    ):
        with pytest.raises(ValueError):
            validate_token(token, field)
