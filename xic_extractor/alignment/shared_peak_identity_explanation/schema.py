from __future__ import annotations

from collections.abc import Mapping, Sequence

ORACLE_SCHEMA_VERSION = "shared_peak_identity_manual_oracle_v1"
EVIDENCE_SCHEMA_VERSION = "shared_peak_identity_evidence_v1"
EXPLANATION_SCHEMA_VERSION = "shared_peak_identity_explanation_v1"
RUN_FACTS_SCHEMA_VERSION = "shared_peak_identity_run_facts_v1"
BLAST_RADIUS_MANIFEST_SCHEMA_VERSION = (
    "shared_peak_identity_blast_radius_manifest_v1"
)
BLAST_RADIUS_SUMMARY_SCHEMA_VERSION = "shared_peak_identity_blast_radius_summary_v1"
SHADOW_LABEL_SCHEMA_VERSION = "shared_peak_identity_shadow_label_v1"
SHADOW_ALIGNMENT_SUMMARY_SCHEMA_VERSION = (
    "shared_peak_identity_shadow_alignment_summary_v1"
)
V2_READINESS_SCHEMA_VERSION = "shared_peak_identity_v2_readiness_v1"
MACHINE_EVIDENCE_SUPPORT_SCHEMA_VERSION = (
    "shared_peak_identity_machine_evidence_support_v2"
)
WRONG_PEAK_ROOT_CAUSE_SCHEMA_VERSION = (
    "shared_peak_identity_wrong_peak_root_cause_v2"
)
RT_MODE_EVIDENCE_SCHEMA_VERSION = "shared_peak_identity_rt_mode_evidence_v1"
PEAK_HYPOTHESIS_SELECTION_SCHEMA_VERSION = (
    "shared_peak_identity_peak_hypothesis_selection_v1"
)
HYPOTHESIS_CONSISTENCY_SCHEMA_VERSION = (
    "shared_peak_identity_hypothesis_consistency_v1"
)
HYPOTHESIS_CONSISTENCY_SUMMARY_SCHEMA_VERSION = (
    "shared_peak_identity_hypothesis_consistency_summary_v1"
)

ORACLE_COLUMNS = (
    "oracle_schema_version",
    "oracle_row_id",
    "feature_family_id",
    "sample_id",
    "manual_label",
    "manual_label_source",
    "manual_confidence",
    "manual_scope",
    "manual_scope_rule_id",
    "manual_reason_tags",
    "reviewed_eic",
    "reviewed_ms2_pattern",
    "reviewed_nl_or_product_pattern",
    "reviewed_intensity_opportunity",
    "dda_opportunity_basis",
    "related_family_id",
    "manual_review_note",
    "manual_review_source",
    "manual_reviewed_at",
)

EVIDENCE_VECTOR_COLUMNS = (
    "evidence_schema_version",
    "evidence_record_id",
    "oracle_row_id",
    "feature_family_id",
    "sample_id",
    "evidence_source",
    "source_role",
    "source_artifact",
    "source_artifact_sha256",
    "source_row_id",
    "machine_current_label",
    "machine_reason",
    "machine_blockers",
    "candidate_apex_rt",
    "family_reference_rt",
    "seed_delta_sec",
    "family_consensus_delta_sec",
    "matrix_local_delta_sec",
    "rt_context_status",
    "shape_status",
    "apex_clarity_status",
    "single_peak_region_status",
    "peak_completeness_status",
    "boundary_reference_status",
    "seed_rescued_boundary_overlap",
    "rescued_pairwise_boundary_overlap_min",
    "family_consensus_boundary_overlap",
    "pattern_similarity_status",
    "matched_product_count",
    "matched_neutral_loss_count",
    "pattern_conflict_status",
    "delta_mass_context",
    "intensity_status",
    "dda_opportunity_status",
    "fragmentation_observation_status",
    "scan_availability_status",
    "metric_availability_status",
)

EXPLANATION_COLUMNS = (
    "explanation_schema_version",
    "oracle_row_id",
    "feature_family_id",
    "sample_id",
    "manual_label",
    "manual_label_source",
    "manual_confidence",
    "manual_scope",
    "manual_reason_tags",
    "machine_current_label",
    "machine_reason",
    "machine_match_status",
    "matched_source_row_ids",
    "machine_source_role",
    "machine_blockers",
    "evidence_gap_class",
    "secondary_gap_tags",
    "explanation_status",
    "smallest_missing_fact",
    "recommended_next_action",
    "source_roles_seen",
    "source_artifacts",
)

RUN_FACTS_COLUMNS = (
    "run_facts_schema_version",
    "slice",
    "seed_rows_total",
    "seed_rows_explained",
    "seed_rows_unexplained",
    "seed_rows_inconclusive",
    "vocabulary_special_casing_detected",
    "blast_radius_assessed",
    "blast_radius_stale_artifact_count",
    "max_overfit_risk",
    "durable_oracle_path",
    "durable_oracle_sha256",
)

BLAST_RADIUS_MANIFEST_COLUMNS = (
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

BLAST_RADIUS_SUMMARY_COLUMNS = (
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

SHADOW_LABEL_COLUMNS = (
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

SHADOW_ALIGNMENT_SUMMARY_COLUMNS = (
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

V2_READINESS_COLUMNS = (
    "v2_readiness_schema_version",
    "v2_mode",
    "v2_gate_status",
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

MACHINE_EVIDENCE_SUPPORT_COLUMNS = (
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

WRONG_PEAK_ROOT_CAUSE_COLUMNS = (
    "wrong_peak_root_cause_schema_version",
    "feature_family_id",
    "sample_id",
    "machine_current_label",
    "root_cause_class",
    "secondary_root_cause_tokens",
    "selection_failure_mode",
    "selected_cell_status",
    "selected_area",
    "selected_apex_rt",
    "selected_peak_start_rt",
    "selected_peak_end_rt",
    "selected_rt_delta_sec",
    "selected_cell_height",
    "selected_local_window_max_intensity",
    "selected_cell_to_local_window_max_ratio",
    "selected_shape_correlation_score",
    "selected_qc_reference_status",
    "selected_qc_reference_sample",
    "selected_qc_reference_apex_abs_delta_sec",
    "selected_qc_reference_shape_similarity",
    "selected_ms2_pattern_status",
    "selected_ms2_trigger_scan_count",
    "selected_ms2_strict_nl_scan_count",
    "family_center_rt",
    "trace_data_status",
    "trace_data_artifact",
    "alternate_peak_status",
    "alternate_peak_rt",
    "alternate_peak_intensity",
    "alternate_peak_relative_intensity",
    "alternate_peak_delta_from_selected_sec",
    "alternate_peak_delta_from_family_center_sec",
    "alternate_peak_basis",
    "recommended_next_action",
    "diagnostic_only",
)

RT_MODE_EVIDENCE_COLUMNS = (
    "rt_mode_evidence_schema_version",
    "feature_family_id",
    "sample_stem",
    "rt_mode_status",
    "rt_mode_evidence_level",
    "selected_mode_id",
    "selected_mode_role",
    "selected_mode_tag_status",
    "family_mode_class",
    "family_mode_count",
    "tag_bearing_mode_count",
    "selected_mode_cell_count",
    "selected_mode_sample_type_counts",
    "selected_mode_status_counts",
    "raw_selected_rt",
    "normalized_selected_rt",
    "selected_mode_raw_rt_range_min",
    "selected_mode_normalized_rt_range_min",
    "family_raw_rt_range_min",
    "family_normalized_rt_range_min",
    "reason",
    "diagnostic_only",
)

PEAK_HYPOTHESIS_SELECTION_COLUMNS = (
    "peak_hypothesis_selection_schema_version",
    "feature_family_id",
    "sample_stem",
    "peak_hypothesis_id",
    "peak_hypothesis_status",
    "product_unit_scope",
    "selected_mode_id",
    "selected_mode_role",
    "selected_mode_tag_status",
    "family_mode_class",
    "family_mode_count",
    "tag_bearing_mode_count",
    "product_selection_action",
    "product_selection_blocker",
    "reason",
    "diagnostic_only",
)

HYPOTHESIS_CONSISTENCY_COLUMNS = (
    "hypothesis_consistency_schema_version",
    "feature_family_id",
    "sample_stem",
    "peak_hypothesis_id",
    "peak_hypothesis_status",
    "product_unit_scope",
    "product_selection_action",
    "product_selection_blocker",
    "ms1_pattern_status",
    "ms1_pattern_evidence_level",
    "qc_reference_status",
    "qc_reference_evidence_level",
    "matrix_rt_drift_status",
    "drift_evidence_level",
    "drift_compatible_status",
    "candidate_ms2_pattern_status",
    "candidate_ms2_evidence_level",
    "family_required_tag_status",
    "ms2_opportunity_status",
    "evidence_consistency_status",
    "split_readiness_status",
    "consistency_blockers",
    "missing_evidence",
    "evidence_sources_seen",
    "hypothesis_next_action",
    "diagnostic_only",
)

HYPOTHESIS_CONSISTENCY_SUMMARY_COLUMNS = (
    "hypothesis_consistency_summary_schema_version",
    "scope",
    "row_count",
    "consistent_count",
    "conflict_count",
    "incomplete_count",
    "split_required_count",
    "review_only_count",
    "not_available_count",
    "product_candidate_ready_count",
    "hard_blocker_count",
    "consistency_gate_status",
    "dominant_blockers",
    "next_action",
    "diagnostic_only",
)

MANUAL_REASON_TAGS = frozenset(
    {
        "rt_close",
        "rt_too_far",
        "rt_drift_possible",
        "single_plausible_peak",
        "shape_complete",
        "shape_normal",
        "shape_bad",
        "pattern_similar",
        "pattern_partial",
        "pattern_mismatch",
        "low_intensity",
        "dda_stochastic_missing",
        "boundary_consistent",
        "boundary_ambiguous",
        "delta_mass_related",
        "scope_derived_unmentioned_fail",
        "human_unjudgeable",
    }
)

EVIDENCE_GAP_CLASSES = frozenset(
    {
        "machine_agrees_with_manual",
        "machine_too_conservative_low_opportunity",
        "machine_too_conservative_shape_or_pattern_unmodeled",
        "machine_too_permissive_rt_pattern_conflict",
        "machine_too_permissive_scope_rule_conflict",
        "boundary_reference_ambiguous",
        "rt_drift_policy_gap",
        "human_unjudgeable_shape_bad",
        "delta_mass_related_context_only",
        "unexplained_machine_manual_gap",
    }
)

ALLOWED_BY_FIELD: dict[str, frozenset[str]] = {
    "manual_label": frozenset(
        {"pass", "suspect", "fail", "human_unjudgeable", "not_applicable"}
    ),
    "manual_label_source": frozenset(
        {
            "direct_eic_ms2_review",
            "direct_eic_only_review",
            "scope_rule_unmentioned_fail",
            "family_all_reviewed_rule",
            "derived_from_related_family_context",
        }
    ),
    "manual_confidence": frozenset(
        {"high", "medium", "low", "unjudgeable", "not_applicable"}
    ),
    "manual_scope": frozenset(
        {
            "reviewed_cell",
            "reviewed_family_all_cells",
            "reviewed_family_named_cells_only",
            "scope_derived_unmentioned_fail",
            "family_level_context",
        }
    ),
    "evidence_source": frozenset(
        {
            "manual_oracle",
            "alignment_review",
            "alignment_cells",
            "candidate_gate_sidecar",
            "tier2_trace_sidecar",
            "identity_coherence_sidecar",
            "targeted_benchmark_context",
            "blast_radius_manifest",
        }
    ),
    "source_role": frozenset(
        {
            "manual_oracle",
            "selected_peak",
            "rescued_cell",
            "candidate_gate_family_context",
            "tier2_raw_reread",
            "identity_coherence_diagnostic",
            "targeted_context",
            "blast_radius_context",
        }
    ),
    "machine_source_role": frozenset(
        {
            "selected_peak",
            "rescued_cell",
            "candidate_gate_family_context",
            "tier2_raw_reread",
            "identity_coherence_diagnostic",
            "targeted_context",
            "blast_radius_context",
            "not_available",
            "not_applicable",
        }
    ),
    "machine_match_status": frozenset(
        {
            "no_match",
            "single_match",
            "ambiguous_multiple_matches",
            "missing_required_key",
            "not_applicable",
        }
    ),
    "rt_context_status": frozenset(
        {
            "supportive",
            "conflicting",
            "drift_possible",
            "ambiguous",
            "not_assessed",
            "unavailable",
        }
    ),
    "shape_status": frozenset(
        {
            "complete",
            "acceptable",
            "distorted",
            "low_intensity_but_coherent",
            "noisy_unjudgeable",
            "not_assessed",
            "unavailable",
            "ambiguous",
        }
    ),
    "apex_clarity_status": frozenset(
        {"clear", "weak", "ambiguous", "not_assessed", "unavailable"}
    ),
    "single_peak_region_status": frozenset(
        {
            "single_plausible_peak",
            "multiple_plausible_peaks",
            "flat_or_noisy_region",
            "not_assessed",
            "unavailable",
        }
    ),
    "peak_completeness_status": frozenset(
        {
            "complete",
            "clipped",
            "partial",
            "not_assessed",
            "unavailable",
            "ambiguous",
        }
    ),
    "boundary_reference_status": frozenset(
        {
            "seed_consistent",
            "rescued_pairwise_consistent",
            "family_consensus_consistent",
            "reference_disagreement",
            "not_assessed",
            "unavailable",
        }
    ),
    "pattern_similarity_status": frozenset(
        {
            "similar",
            "partial_similar",
            "mismatch",
            "not_observed",
            "not_assessed",
            "unavailable",
            "ambiguous",
        }
    ),
    "pattern_conflict_status": frozenset(
        {
            "none",
            "rt_pattern_conflict",
            "pattern_only_conflict",
            "not_assessed",
            "unavailable",
        }
    ),
    "delta_mass_context": frozenset(
        {"none", "related_family_context_only", "not_assessed", "unavailable"}
    ),
    "intensity_status": frozenset(
        {
            "sufficient",
            "low_but_visible",
            "too_low_to_assess",
            "not_assessed",
            "unavailable",
        }
    ),
    "dda_opportunity_status": frozenset(
        {
            "observed",
            "low_intensity_stochastic_not_observed",
            "expected_but_missing",
            "not_assessed",
            "not_applicable",
            "unavailable",
        }
    ),
    "fragmentation_observation_status": frozenset(
        {"observed", "not_observed", "conflicting", "not_assessed", "unavailable"}
    ),
    "scan_availability_status": frozenset(
        {"sufficient", "low", "not_assessed", "unavailable"}
    ),
    "metric_availability_status": frozenset(
        {
            "complete",
            "partial",
            "schema_missing",
            "artifact_missing",
            "stale_hash_mismatch",
            "not_assessed",
        }
    ),
    "negative_evidence_basis_status": frozenset(
        {
            "machine_observed",
            "manual_oracle_derived",
            "mixed",
            "not_applicable",
            "not_available",
            "inconclusive",
        }
    ),
    "negative_evidence_class": frozenset(
        {
            "no_candidate_ms1_evidence",
            "pattern_mismatch",
            "rt_not_explained",
            "local_peak_not_decisive",
            "not_applicable",
            "not_available",
        }
    ),
    "slice": frozenset({"slice0", "slice1"}),
    "blast_radius_assessed": frozenset(
        {
            "present_current",
            "fixture_cohort_not_assessed",
            "full_cohort_not_assessed",
            "stale_hash_mismatch",
            "manifest_missing",
            "not_run_slice0",
            "not_assessed",
        }
    ),
    "artifact_status": frozenset(
        {
            "present_current",
            "present_hash_unpinned",
            "present_stale_hash_mismatch",
            "present_missing_required_fields",
            "missing",
            "schema_unsupported",
            "not_assessed",
            "unavailable",
        }
    ),
    "scope": frozenset(
        {
            "seed",
            "non_seed_same_family",
            "all_available_fixture_cohort",
            "all_available_full_cohort",
            "manual_label",
            "overall",
            "full_matrix",
            "sidecar_key_union",
        }
    ),
    "artifact_role": frozenset(
        {
            "manual_oracle_fixture",
            "alignment_review",
            "alignment_cells",
            "tier2_trace_sidecar",
            "identity_diagnostic",
            "targeted_context",
            "blast_radius_context",
        }
    ),
    "freshness_basis": frozenset(
        {
            "slice0_evidence_vector",
            "expected_blast_radius_manifest",
            "not_available",
        }
    ),
    "max_overfit_risk": frozenset(
        {"none", "low", "medium", "high", "unassessed"}
    ),
    "overfit_risk": frozenset({"none", "low", "medium", "high", "unassessed"}),
    "shadow_label": frozenset(
        {
            "manual_like_pass_candidate",
            "manual_like_suspect_candidate",
            "manual_like_fail_candidate",
            "low_opportunity_supported",
            "rt_pattern_conflict_blocked",
            "human_unjudgeable_like",
            "delta_mass_context_only",
            "unresolved_gap",
        }
    ),
    "shadow_alignment_status": frozenset(
        {
            "aligned",
            "partial",
            "contradicted",
            "unjudgeable",
            "context_only",
            "unresolved",
        }
    ),
    "manual_machine_direction": frozenset(
        {
            "machine_agrees",
            "machine_too_conservative",
            "machine_too_permissive",
            "ambiguous_policy",
            "context_only",
            "unresolved",
        }
    ),
    "v2_gate_status": frozenset(
        {
            "shadow_ready_candidate",
            "exploratory_only",
            "blocked_by_vocabulary",
            "blocked_by_overfit_risk",
        }
    ),
    "readiness_label": frozenset({"diagnostic_only"}),
    "v2_mode": frozenset({"shadow_label_alignment"}),
    "status_label_alignment_status": frozenset(
        {
            "proxy_agrees",
            "proxy_partial",
            "proxy_contradicts",
            "not_available",
            "not_evaluable",
            "context_only",
        }
    ),
    "rt_basis_status": frozenset(
        {
            "machine_observed",
            "machine_proxy",
            "manual_oracle_derived",
            "mixed",
            "not_available",
            "not_applicable",
        }
    ),
    "shape_basis_status": frozenset(
        {
            "machine_observed",
            "machine_proxy",
            "manual_oracle_derived",
            "mixed",
            "not_available",
            "not_applicable",
        }
    ),
    "pattern_basis_status": frozenset(
        {
            "machine_observed",
            "machine_proxy",
            "manual_oracle_derived",
            "mixed",
            "not_available",
            "not_applicable",
        }
    ),
    "opportunity_basis_status": frozenset(
        {
            "machine_observed",
            "machine_proxy",
            "manual_oracle_derived",
            "mixed",
            "not_available",
            "not_applicable",
        }
    ),
    "scope_basis_status": frozenset(
        {
            "machine_observed",
            "machine_proxy",
            "manual_oracle_derived",
            "mixed",
            "not_available",
            "not_applicable",
        }
    ),
    "evidence_support_status": frozenset(
        {
            "machine_observed_sufficient",
            "machine_observed_partial",
            "machine_observed_conflict",
            "machine_proxy_only",
            "manual_derived_only",
            "blocked_missing_metric",
            "not_evaluable",
            "context_only",
        }
    ),
    "machine_evidence_basis": frozenset(
        {
            "machine_observed_sufficient",
            "machine_observed_partial",
            "machine_proxy_or_manual_derived",
            "not_assessed",
        }
    ),
    "evidence_gap_class": EVIDENCE_GAP_CLASSES,
    "explanation_status": frozenset(
        {"explained", "partially_explained", "unexplained", "inconclusive"}
    ),
    "recommended_next_action": frozenset(
        {
            "no_action",
            "inspect_manual_eic",
            "inspect_ms2_pattern",
            "add_shape_metric",
            "add_pattern_metric",
            "add_opportunity_metric",
            "check_boundary_reference",
            "check_blast_radius",
            "flag_for_v2_gate_review",
        }
    ),
    "rt_mode_status": frozenset(
        {
            "mode_supported",
            "mode_conflict",
            "mode_split_required",
            "consolidation_no_go",
            "tailing_confounded",
            "raw_mode_review_only",
            "inconclusive",
            "not_available",
        }
    ),
    "rt_mode_evidence_level": frozenset(
        {
            "irt_selected_apex_modes",
            "raw_selected_apex_modes",
            "mode_assignment_summary",
            "not_available",
        }
    ),
    "selected_mode_role": frozenset(
        {
            "single_mode",
            "tag_bearing_core",
            "tag_bearing_outlier",
            "non_tag_outlier",
            "split_mode",
            "mixed_mode",
            "tailing_confounded",
            "raw_non_tag_outlier",
            "raw_split_review",
            "unknown",
        }
    ),
    "selected_mode_tag_status": frozenset(
        {
            "tag_supported",
            "family_tag_supported",
            "no_tag_observed",
            "family_tag_absent",
            "unknown",
        }
    ),
    "family_mode_class": frozenset(
        {
            "rt_mode_pure",
            "tag_backed_core_with_outlier_modes",
            "irt_refined_mode_split",
            "tailing_confounded",
            "consolidation_no_go",
            "inconclusive",
        }
    ),
    "peak_hypothesis_status": frozenset(
        {
            "product_candidate_core",
            "cross_mode_rescue_blocked",
            "mode_split_required",
            "consolidation_no_go",
            "tailing_review_only",
            "raw_mode_review_only",
            "inconclusive",
            "not_available",
        }
    ),
    "product_unit_scope": frozenset(
        {
            "mode_level",
            "candidate_container",
            "sample_cell",
            "review_only",
            "not_available",
        }
    ),
    "qc_reference_policy": frozenset(
        {
            "qc_consensus_with_local_support",
            "qc_consensus_fallback_valid_qc",
            "qc_consensus_with_local_conflict",
            "qc_consensus_conflict_review",
            "qc_consensus_mixed_review",
            "nearest_valid_qc_local_condition_only",
            "local_qc_uninformative",
            "not_available",
        }
    ),
    "qc_consensus_status": frozenset(
        {
            "supportive",
            "partial_support",
            "conflict",
            "mixed_conflict",
            "inconclusive",
            "not_available",
        }
    ),
    "qc_reference_conflict_status": frozenset(
        {
            "none",
            "local_vs_consensus_conflict",
            "local_qc_uninformative",
            "consensus_mixed_conflict",
            "consensus_missing",
        }
    ),
    "product_selection_action": frozenset(
        {
            "select_mode_peak_hypothesis",
            "block_cross_mode_rescue",
            "block_family_promotion",
            "require_mode_split_before_product",
            "require_tailing_review",
            "require_raw_mode_review",
            "require_review",
            "no_product_action",
        }
    ),
    "product_selection_blocker": frozenset(
        {
            "none",
            "cross_mode_rescue",
            "mode_split_required",
            "consolidation_no_go",
            "tailing_confounded",
            "raw_mode_review_only",
            "inconclusive_mode_evidence",
            "not_available",
        }
    ),
    "family_required_tag_status": frozenset(
        {
            "sample_required_tag_observed",
            "family_required_tag_observed",
            "family_required_tag_not_observed",
            "not_observed",
            "not_available",
        }
    ),
    "ms2_opportunity_status": frozenset(
        {
            "required_tag_observed",
            "family_required_tag_observed",
            "dda_missing_nl_not_dispositive",
            "expected_but_missing",
            "not_observed_review",
            "not_available",
            "not_applicable",
            "conflict",
        }
    ),
    "evidence_consistency_status": frozenset(
        {
            "consistent",
            "conflict",
            "incomplete",
            "split_required",
            "review_only",
            "not_available",
        }
    ),
    "split_readiness_status": frozenset(
        {
            "peak_hypothesis_ready",
            "mode_split_required",
            "cross_mode_rescue_blocked",
            "consolidation_no_go",
            "review_required",
            "incomplete_evidence",
            "not_available",
        }
    ),
    "hypothesis_next_action": frozenset(
        {
            "no_action",
            "inspect_conflict",
            "add_missing_sidecar",
            "split_peak_hypothesis",
            "keep_review_only",
            "block_product_activation",
            "inspect_ms2_opportunity",
        }
    ),
    "consistency_gate_status": frozenset(
        {"pass", "review_required", "blocked"}
    ),
}


def split_semicolon(value: str) -> tuple[str, ...]:
    return tuple(part for part in str(value or "").split(";") if part)


def validate_token(value: str, field: str) -> None:
    allowed = ALLOWED_BY_FIELD.get(field)
    if allowed is None:
        return
    if value not in allowed:
        raise ValueError(f"{field}: unsupported token {value!r}")


def validate_semicolon_tokens(
    value: str,
    *,
    field: str,
    allowed_tokens: set[str] | frozenset[str],
) -> None:
    for token in split_semicolon(value):
        if token.strip() != token or any(char.isspace() for char in token):
            raise ValueError(f"{field}: invalid whitespace in token {token!r}")
        if token not in allowed_tokens:
            raise ValueError(f"{field}: unsupported token {token!r}")


def validate_source_row_ids(value: str, valid_source_row_ids: set[str]) -> None:
    for source_row_id in split_semicolon(value):
        if source_row_id.strip() != source_row_id or any(
            char.isspace() for char in source_row_id
        ):
            raise ValueError(f"matched_source_row_ids: invalid id {source_row_id!r}")
        if source_row_id not in valid_source_row_ids:
            raise ValueError(
                f"matched_source_row_ids: unknown source row id {source_row_id!r}"
            )


def validate_row_tokens(row: Mapping[str, str]) -> None:
    for field in ALLOWED_BY_FIELD:
        if field in row and row[field] != "":
            validate_token(row[field], field)
    if "manual_reason_tags" in row:
        validate_semicolon_tokens(
            row["manual_reason_tags"],
            field="manual_reason_tags",
            allowed_tokens=MANUAL_REASON_TAGS,
        )
    if "secondary_gap_tags" in row:
        validate_semicolon_tokens(
            row["secondary_gap_tags"],
            field="secondary_gap_tags",
            allowed_tokens=MANUAL_REASON_TAGS,
        )


def require_columns(row: Mapping[str, str], columns: Sequence[str], label: str) -> None:
    missing = [column for column in columns if column not in row]
    if missing:
        raise ValueError(f"{label}: missing required columns: {', '.join(missing)}")
