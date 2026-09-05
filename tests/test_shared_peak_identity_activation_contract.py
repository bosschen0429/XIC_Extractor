from __future__ import annotations

from xic_extractor.alignment.shared_peak_identity_explanation import (
    activation_contract as activation,
)


def test_family_required_tag_gate_directly_blocks_family_promotion() -> None:
    rows = activation.build_activation_decision_rows(
        [
            _support_row(
                missing_machine_evidence="family_required_tag_gate",
                observed_machine_metrics=(
                    "dda_missing_nl_policy_status=family_required_tag_not_observed"
                ),
            ),
        ],
    )

    row = rows[0]
    assert row["activation_status"] == "auto_block"
    assert row["activation_action"] == "activate_fail"
    assert row["product_label_candidate"] == "fail"
    assert row["product_effect"] == "block_family_promotion"
    assert row["hard_product_block"] == "TRUE"
    assert row["contract_rule_id"] == "family_required_tag_gate"


def test_wrong_peak_conflict_directly_blocks_rescue_cell() -> None:
    rows = activation.build_activation_decision_rows(
        [
            _support_row(
                observed_machine_metrics=(
                    "ms1_pattern_status=conflict;"
                    "ms1_pattern_reason="
                    "family_ms1_overlay_competing_peak_matches_family_consensus"
                ),
            ),
        ],
    )

    row = rows[0]
    assert row["activation_status"] == "auto_block"
    assert row["activation_action"] == "block_rescue"
    assert row["product_effect"] == "block_rescue_cell"
    assert row["contract_rule_id"] == "wrong_peak_conflict"


def test_qc_conflict_alone_does_not_block_supported_peak_hypothesis() -> None:
    rows = activation.build_activation_decision_rows(
        [
            _support_row(
                observed_machine_metrics=(
                    "peak_hypothesis_id=FAM001::mode_1;"
                    "peak_hypothesis_authority_source="
                    "typed_mode_hypothesis_assignment;"
                    "ms1_pattern_status=supportive;"
                    "qc_ms1_reference_status=conflict"
                ),
            ),
        ],
    )

    row = rows[0]
    assert row["activation_status"] == "auto_activate"
    assert row["contract_rule_id"] == "machine_observed_sufficient_positive_identity"
    assert row["activation_unit_scope"] == "peak_hypothesis"


def test_qc_conflict_with_sample_pattern_conflict_blocks_rescue_cell() -> None:
    rows = activation.build_activation_decision_rows(
        [
            _support_row(
                evidence_support_status="machine_observed_conflict",
                missing_machine_evidence="pattern_metric_not_supportive",
                observed_machine_metrics=(
                    "peak_hypothesis_id=FAM001::mode_1;"
                    "qc_ms1_reference_status=conflict;"
                    "candidate_ms2_pattern_status=conflict"
                ),
            ),
        ],
    )

    row = rows[0]
    assert row["activation_status"] == "auto_block"
    assert row["activation_action"] == "block_rescue"
    assert row["contract_rule_id"] == "wrong_peak_conflict"


def test_rt_mode_conflict_directly_blocks_rescue_cell() -> None:
    rows = activation.build_activation_decision_rows(
        [
            _support_row(
                evidence_support_status="machine_observed_conflict",
                missing_machine_evidence="rt_mode_not_supportive",
                observed_machine_metrics=(
                    "rt_mode_status=mode_conflict;"
                    "selected_mode_role=non_tag_outlier;"
                    "family_mode_class=tag_backed_core_with_outlier_modes"
                ),
            ),
        ],
    )

    row = rows[0]
    assert row["activation_status"] == "auto_block"
    assert row["activation_action"] == "block_rescue"
    assert row["product_effect"] == "block_rescue_cell"
    assert row["contract_rule_id"] == "wrong_peak_conflict"
    assert "rt_mode_status=mode_conflict" in row["source_evidence_tokens"]


def test_peak_hypothesis_cross_mode_rescue_directly_blocks_rescue_cell() -> None:
    rows = activation.build_activation_decision_rows(
        [
            _support_row(
                feature_family_id="FAM011810",
                evidence_support_status="machine_observed_conflict",
                missing_machine_evidence="peak_hypothesis_not_supportive",
                observed_machine_metrics=(
                    "peak_hypothesis_id=FAM011810::irt_green_core;"
                    "peak_hypothesis_status=cross_mode_rescue_blocked;"
                    "product_selection_action=block_cross_mode_rescue;"
                    "product_selection_blocker=cross_mode_rescue"
                ),
            ),
        ],
    )

    row = rows[0]
    assert row["activation_status"] == "auto_block"
    assert row["activation_action"] == "block_rescue"
    assert row["product_effect"] == "block_rescue_cell"
    assert row["contract_rule_id"] == "wrong_peak_conflict"
    assert row["candidate_container_id"] == "FAM011810"
    assert row["peak_hypothesis_id"] == "FAM011810::irt_green_core"
    assert row["activation_unit_scope"] == "sample_cell"
    assert (
        "peak_hypothesis_status=cross_mode_rescue_blocked"
        in row["source_evidence_tokens"]
    )


def test_peak_hypothesis_split_required_blocks_family_promotion() -> None:
    rows = activation.build_activation_decision_rows(
        [
            _support_row(
                evidence_support_status="machine_observed_conflict",
                observed_machine_metrics=(
                    "peak_hypothesis_status=mode_split_required;"
                    "product_selection_action=require_mode_split_before_product;"
                    "product_selection_blocker=mode_split_required"
                ),
            ),
        ],
    )

    row = rows[0]
    assert row["activation_status"] == "auto_block"
    assert row["activation_action"] == "activate_fail"
    assert row["product_effect"] == "block_family_promotion"
    assert row["contract_rule_id"] == "peak_hypothesis_split_required"
    assert row["candidate_container_id"] == "FAM001"
    assert row["peak_hypothesis_id"] == ""
    assert row["activation_unit_scope"] == "candidate_container"


def test_peak_hypothesis_core_becomes_activation_unit_when_available() -> None:
    rows = activation.build_activation_decision_rows(
        [
            _support_row(
                feature_family_id="FAM011810",
                evidence_support_status="machine_observed_sufficient",
                observed_machine_metrics=(
                    "peak_hypothesis_id=FAM011810::irt_blue_core;"
                    "peak_hypothesis_authority_source="
                    "typed_mode_hypothesis_assignment;"
                    "peak_hypothesis_status=product_candidate_core;"
                    "product_selection_action=select_mode_peak_hypothesis;"
                    "ms1_pattern_status=supportive;"
                    "candidate_ms2_pattern_status=supportive"
                ),
            ),
        ],
    )

    row = rows[0]
    assert row["activation_status"] == "auto_activate"
    assert row["peak_hypothesis_id"] == "FAM011810::irt_blue_core"
    assert row["activation_unit_scope"] == "peak_hypothesis"


def test_peak_hypothesis_core_without_typed_authority_requires_review() -> None:
    rows = activation.build_activation_decision_rows(
        [
            _support_row(
                feature_family_id="FAM011810",
                evidence_support_status="machine_observed_sufficient",
                observed_machine_metrics=(
                    "peak_hypothesis_id=FAM011810::legacy_mode_1;"
                    "peak_hypothesis_status=product_candidate_core;"
                    "product_selection_action=select_mode_peak_hypothesis;"
                    "peak_hypothesis_reason="
                    "selected_mode_is_product_peak_hypothesis_candidate;"
                    "ms1_pattern_status=supportive;"
                    "candidate_ms2_pattern_status=supportive"
                ),
            ),
        ],
    )

    row = rows[0]
    assert row["activation_status"] == "review_required"
    assert row["product_effect"] == "review_only"
    assert row["contract_rule_id"] == ("peak_hypothesis_authority_not_product_facing")
    assert row["required_review_reason"] == (
        "typed_mode_hypothesis_or_locked_oracle_required"
    )


def test_peak_hypothesis_tailing_confounded_requires_review() -> None:
    rows = activation.build_activation_decision_rows(
        [
            _support_row(
                evidence_support_status="machine_observed_sufficient",
                observed_machine_metrics=(
                    "peak_hypothesis_status=tailing_review_only;"
                    "product_selection_action=require_tailing_review;"
                    "ms1_pattern_status=supportive;"
                    "candidate_ms2_pattern_status=supportive"
                ),
            ),
        ],
    )

    row = rows[0]
    assert row["activation_status"] == "review_required"
    assert row["activation_action"] == "require_review"
    assert row["product_effect"] == "review_only"
    assert row["contract_rule_id"] == "peak_hypothesis_tailing_review_only"


def test_peak_hypothesis_raw_overlay_split_requires_review() -> None:
    rows = activation.build_activation_decision_rows(
        [
            _support_row(
                evidence_support_status="machine_observed_partial",
                observed_machine_metrics=(
                    "peak_hypothesis_status=raw_mode_review_only;"
                    "product_selection_action=require_raw_mode_review;"
                    "rt_mode_evidence_level=raw_selected_apex_modes"
                ),
            ),
        ],
    )

    row = rows[0]
    assert row["activation_status"] == "review_required"
    assert row["activation_action"] == "require_review"
    assert row["product_effect"] == "review_only"
    assert row["contract_rule_id"] == "peak_hypothesis_raw_mode_review_only"
    assert row["required_review_reason"] == "raw_mode_review_only"


def test_raw_overlay_wrong_peak_conflict_blocks_before_review_only_boundary() -> None:
    rows = activation.build_activation_decision_rows(
        [
            _support_row(
                evidence_support_status="machine_observed_conflict",
                observed_machine_metrics=(
                    "peak_hypothesis_status=raw_mode_review_only;"
                    "product_selection_action=require_raw_mode_review;"
                    "product_selection_blocker=raw_mode_review_only;"
                    "qc_ms1_reference_status=conflict;"
                    "ms1_pattern_status=conflict"
                ),
            ),
        ],
    )

    row = rows[0]
    assert row["activation_status"] == "auto_block"
    assert row["activation_action"] == "block_rescue"
    assert row["contract_rule_id"] == "wrong_peak_conflict"


def test_raw_overlay_positive_with_candidate_aligned_ms1_ms2_can_activate() -> None:
    rows = activation.build_activation_decision_rows(
        [
            _support_row(
                evidence_support_status="machine_observed_sufficient",
                observed_machine_metrics=(
                    "peak_hypothesis_id=FAM001::raw_mode_1;"
                    "peak_hypothesis_authority_source=raw_or_overlay_review_only;"
                    "peak_hypothesis_status=raw_mode_review_only;"
                    "product_selection_action=require_raw_mode_review;"
                    "product_selection_blocker=raw_mode_review_only;"
                    "ms1_pattern_status=supportive;"
                    "candidate_ms2_pattern_status=supportive"
                ),
            ),
        ],
    )

    row = rows[0]
    assert row["activation_status"] == "auto_activate"
    assert row["contract_rule_id"] == "machine_observed_sufficient_positive_identity"
    assert row["activation_unit_scope"] == "peak_hypothesis"


def test_raw_overlay_positive_without_candidate_ms2_stays_confidence_only() -> None:
    rows = activation.build_activation_decision_rows(
        [
            _support_row(
                evidence_support_status="machine_observed_sufficient",
                observed_machine_metrics=(
                    "peak_hypothesis_id=FAM001::raw_mode_1;"
                    "peak_hypothesis_authority_source=raw_or_overlay_review_only;"
                    "peak_hypothesis_status=raw_mode_review_only;"
                    "product_selection_action=require_raw_mode_review;"
                    "product_selection_blocker=raw_mode_review_only;"
                    "ms1_pattern_status=supportive;"
                    "candidate_ms2_pattern_status=not_observed;"
                    "dda_missing_nl_policy_status=not_dispositive"
                ),
            ),
        ],
    )

    row = rows[0]
    assert row["activation_status"] == "confidence_only"
    assert row["contract_rule_id"] == "dda_missing_nl_not_dispositive"


def test_dda_non_dispositive_only_demotes_confidence() -> None:
    rows = activation.build_activation_decision_rows(
        [
            _support_row(
                observed_machine_metrics=(
                    "dda_missing_nl_policy_status=not_dispositive;"
                    "family_ms2_required_tag_status=observed_in_family;"
                    "candidate_ms2_pattern_status=not_observed"
                ),
            ),
        ],
    )

    row = rows[0]
    assert row["activation_status"] == "confidence_only"
    assert row["activation_action"] == "demote_confidence"
    assert row["product_label_candidate"] == "unchanged"
    assert row["product_effect"] == "confidence_demote_only"


def test_matrix_rt_drift_support_requires_shape_before_activation() -> None:
    rows = activation.build_activation_decision_rows(
        [
            _support_row(
                shape_basis_status="machine_proxy",
                missing_machine_evidence="formal_shape_metric",
                observed_machine_metrics="matrix_rt_drift_status=drift_supported",
            ),
        ],
    )

    row = rows[0]
    assert row["activation_status"] == "review_required"
    assert row["activation_action"] == "require_review"
    assert row["contract_rule_id"] == "matrix_rt_drift_requires_shape_support"


def test_machine_observed_sufficient_positive_identity_can_activate_pass() -> None:
    rows = activation.build_activation_decision_rows(
        [
            _support_row(
                evidence_support_status="machine_observed_sufficient",
                observed_machine_metrics=(
                    "peak_hypothesis_id=FAM001::mode_1;"
                    "peak_hypothesis_authority_source="
                    "typed_mode_hypothesis_assignment;"
                    "ms1_pattern_status=supportive;"
                    "candidate_ms2_pattern_status=supportive"
                ),
            ),
        ],
    )

    row = rows[0]
    assert row["activation_status"] == "auto_activate"
    assert row["activation_action"] == "activate_pass"
    assert row["product_label_candidate"] == "pass"
    assert row["product_effect"] == "accept_label_or_rescue"
    assert row["activation_unit_scope"] == "peak_hypothesis"


def test_positive_identity_without_peak_hypothesis_requires_review() -> None:
    rows = activation.build_activation_decision_rows(
        [
            _support_row(
                evidence_support_status="machine_observed_sufficient",
                observed_machine_metrics=(
                    "ms1_pattern_status=supportive;"
                    "candidate_ms2_pattern_status=supportive"
                ),
            ),
        ],
    )

    row = rows[0]
    assert row["activation_status"] == "review_required"
    assert row["activation_action"] == "require_review"
    assert row["product_label_candidate"] == "unchanged"
    assert row["activation_unit_scope"] == "legacy_family_row"
    assert row["contract_rule_id"] == "peak_hypothesis_unit_required"


def test_activation_acceptance_requires_current_full_cohort_and_regression_pass() -> (
    None
):
    decision_rows = activation.build_activation_decision_rows(
        [
            _support_row(feature_family_id="FAM001", sample_id="S1"),
            _support_row(feature_family_id="FAM002", sample_id="S2"),
        ],
    )

    failed = activation.summarize_activation_acceptance(
        decision_rows,
        blast_radius_current=False,
        assessed_full_cohort_rows=100,
        must_not_regress_status="pass",
    )
    passed = activation.summarize_activation_acceptance(
        decision_rows,
        blast_radius_current=True,
        assessed_full_cohort_rows=100,
        must_not_regress_status="pass",
    )

    assert failed["acceptance_status"] == "fail"
    assert "blast_radius_not_current" in failed["hard_fail_reasons"]
    assert passed["acceptance_status"] == "pass"
    assert passed["max_allowed_product_affecting_rows"] == "2"
    assert passed["assessed_rows_basis"] == "activation_decision_rows_fallback"


def test_activation_infers_full_cohort_blast_radius_denominator() -> None:
    assessed_rows, basis = activation.infer_full_cohort_assessed_rows(
        [
            {
                "scope": "all_available_full_cohort",
                "artifact_id": "full_cohort_alignment_cells",
                "assessed_row_count": "1854020",
            },
            {
                "scope": "all_available_full_cohort",
                "artifact_id": "full_cohort_alignment_cells",
                "assessed_row_count": "1854020",
            },
            {
                "scope": "overall",
                "artifact_id": "combined_alignment_cells",
                "assessed_row_count": "1873180",
            },
        ],
    )

    assert assessed_rows == 1854020
    assert basis == "blast_radius_summary:all_available_full_cohort:assessed_row_count"


def test_must_not_regress_expectations_pass_expected_rows() -> None:
    decision_rows = activation.build_activation_decision_rows(
        [
            _support_row(feature_family_id="FAM001", sample_id="S1"),
            _support_row(
                feature_family_id="FAM002",
                sample_id="S2",
                observed_machine_metrics=(
                    "ms1_pattern_reason="
                    "family_ms1_overlay_competing_peak_matches_family_consensus"
                ),
            ),
        ],
    )

    status, failures = activation.evaluate_must_not_regress(
        decision_rows,
        [
            {
                "expectation_id": "positive_pass",
                "feature_family_id": "FAM001",
                "sample_id": "S1",
                "allowed_activation_statuses": "auto_activate",
                "allowed_contract_rule_ids": (
                    "machine_observed_sufficient_positive_identity"
                ),
                "allowed_product_label_candidates": "pass",
            },
            {
                "expectation_id": "wrong_peak_block",
                "feature_family_id": "FAM002",
                "sample_id": "S2",
                "allowed_activation_statuses": "auto_block",
                "allowed_contract_rule_ids": "wrong_peak_conflict",
                "allowed_product_label_candidates": "fail",
            },
        ],
    )

    assert status == "pass"
    assert failures == ()


def test_raw_mode_benchmark_representatives_pass_must_not_regress() -> None:
    decision_rows = activation.build_activation_decision_rows(
        [
            _support_row(
                feature_family_id="FAM001473",
                sample_id="TumorBC2312_DNA",
                evidence_support_status="machine_observed_conflict",
                observed_machine_metrics=(
                    "peak_hypothesis_id=FAM001473::raw_mode_1;"
                    "peak_hypothesis_status=raw_mode_review_only;"
                    "product_selection_action=require_raw_mode_review;"
                    "product_selection_blocker=raw_mode_review_only;"
                    "qc_ms1_reference_status=conflict;"
                    "ms1_pattern_status=conflict"
                ),
                missing_machine_evidence="pattern_metric_not_supportive",
            ),
            _raw_overlay_positive_row("FAM002625", "TumorBC2263_DNA"),
            _raw_overlay_positive_row("FAM005937", "BenignfatBC1055_DNA"),
            _raw_overlay_positive_row("FAM005937", "BenignfatBC1151_DNA"),
            _support_row(
                feature_family_id="FAM019990",
                sample_id="TumorBC2312_DNA",
                observed_machine_metrics=(
                    "peak_hypothesis_id=FAM019990::raw_mode_1;"
                    "peak_hypothesis_status=raw_mode_review_only;"
                    "product_selection_action=require_raw_mode_review;"
                    "product_selection_blocker=raw_mode_review_only;"
                    "ms1_pattern_status=supportive;"
                    "candidate_ms2_pattern_status=not_observed;"
                    "dda_missing_nl_policy_status=not_dispositive"
                ),
            ),
        ],
    )

    status, failures = activation.evaluate_must_not_regress(
        decision_rows,
        [
            {
                "expectation_id": "FAM000144_mapped_tumor_wrong_peak_block",
                "feature_family_id": "FAM001473",
                "sample_id": "TumorBC2312_DNA",
                "allowed_activation_statuses": "auto_block",
                "allowed_contract_rule_ids": "wrong_peak_conflict",
                "allowed_product_label_candidates": "fail",
            },
            {
                "expectation_id": "d3_n6_meda_mapped_tumor_pass",
                "feature_family_id": "FAM002625",
                "sample_id": "TumorBC2263_DNA",
                "allowed_activation_statuses": "auto_activate",
                "allowed_contract_rule_ids": (
                    "machine_observed_sufficient_positive_identity"
                ),
                "allowed_product_label_candidates": "pass",
            },
            {
                "expectation_id": "FAM000610_mapped_benign1055_pass",
                "feature_family_id": "FAM005937",
                "sample_id": "BenignfatBC1055_DNA",
                "allowed_activation_statuses": "auto_activate",
                "allowed_contract_rule_ids": (
                    "machine_observed_sufficient_positive_identity"
                ),
                "allowed_product_label_candidates": "pass",
            },
            {
                "expectation_id": "FAM000610_mapped_benign1151_pass",
                "feature_family_id": "FAM005937",
                "sample_id": "BenignfatBC1151_DNA",
                "allowed_activation_statuses": "auto_activate",
                "allowed_contract_rule_ids": (
                    "machine_observed_sufficient_positive_identity"
                ),
                "allowed_product_label_candidates": "pass",
            },
            {
                "expectation_id": "FAM001658_mapped_low_intensity_not_hard_fail",
                "feature_family_id": "FAM019990",
                "sample_id": "TumorBC2312_DNA",
                "allowed_activation_statuses": (
                    "review_required;auto_activate;confidence_only"
                ),
                "allowed_product_label_candidates": "unchanged;pass",
                "disallowed_activation_statuses": "auto_block",
            },
        ],
    )

    assert status == "pass"
    assert failures == ()


def test_must_not_regress_expectations_report_failures() -> None:
    decision_rows = activation.build_activation_decision_rows(
        [_support_row(feature_family_id="FAM001", sample_id="S1")],
    )

    status, failures = activation.evaluate_must_not_regress(
        decision_rows,
        [
            {
                "expectation_id": "should_not_activate",
                "feature_family_id": "FAM001",
                "sample_id": "S1",
                "disallowed_activation_statuses": "auto_activate",
            },
            {
                "expectation_id": "missing_row",
                "feature_family_id": "FAM999",
                "sample_id": "S9",
            },
        ],
    )

    assert status == "fail"
    assert "should_not_activate:activation_status=auto_activate:disallowed" in failures
    assert "missing_row:missing_decision" in failures


def test_activation_acceptance_fails_when_product_affecting_rows_exceed_threshold() -> (
    None
):
    decision_rows = activation.build_activation_decision_rows(
        [
            _support_row(feature_family_id=f"FAM{i:03d}", sample_id="S1")
            for i in range(3)
        ],
    )

    summary = activation.summarize_activation_acceptance(
        decision_rows,
        blast_radius_current=True,
        assessed_full_cohort_rows=100,
        must_not_regress_status="pass",
    )

    assert summary["acceptance_status"] == "fail"
    assert "product_affecting_rows_exceed_threshold" in summary["hard_fail_reasons"]


def _raw_overlay_positive_row(feature_family_id: str, sample_id: str) -> dict[str, str]:
    return _support_row(
        feature_family_id=feature_family_id,
        sample_id=sample_id,
        evidence_support_status="machine_observed_sufficient",
        observed_machine_metrics=(
            f"peak_hypothesis_id={feature_family_id}::raw_mode_1;"
            "peak_hypothesis_authority_source=raw_or_overlay_review_only;"
            "peak_hypothesis_status=raw_mode_review_only;"
            "product_selection_action=require_raw_mode_review;"
            "product_selection_blocker=raw_mode_review_only;"
            "ms1_pattern_status=supportive;"
            "candidate_ms2_pattern_status=supportive"
        ),
    )


def _support_row(
    *,
    feature_family_id: str = "FAM001",
    sample_id: str = "S1",
    machine_current_label: str = "rescued",
    evidence_support_status: str = "machine_observed_sufficient",
    rt_basis_status: str = "machine_observed",
    shape_basis_status: str = "machine_observed",
    pattern_basis_status: str = "machine_observed",
    opportunity_basis_status: str = "machine_observed",
    negative_evidence_basis_status: str = "not_applicable",
    negative_evidence_class: str = "",
    negative_evidence_detail: str = "",
    observed_machine_metrics: str = "",
    missing_machine_evidence: str = "",
) -> dict[str, str]:
    if not observed_machine_metrics and evidence_support_status == (
        "machine_observed_sufficient"
    ):
        observed_machine_metrics = (
            f"peak_hypothesis_id={feature_family_id}::mode_1;"
            "peak_hypothesis_authority_source=typed_mode_hypothesis_assignment"
        )
    return {
        "feature_family_id": feature_family_id,
        "sample_id": sample_id,
        "machine_current_label": machine_current_label,
        "evidence_support_status": evidence_support_status,
        "rt_basis_status": rt_basis_status,
        "shape_basis_status": shape_basis_status,
        "pattern_basis_status": pattern_basis_status,
        "opportunity_basis_status": opportunity_basis_status,
        "negative_evidence_basis_status": negative_evidence_basis_status,
        "negative_evidence_class": negative_evidence_class,
        "negative_evidence_detail": negative_evidence_detail,
        "observed_machine_metrics": observed_machine_metrics,
        "missing_machine_evidence": missing_machine_evidence,
    }
