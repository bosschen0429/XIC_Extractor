from __future__ import annotations

from pathlib import Path

from xic_extractor.alignment.shared_peak_identity_explanation import (
    machine_evidence_support,
)
from xic_extractor.alignment.shared_peak_identity_explanation.machine_artifacts import (
    MachineMatch,
)
from xic_extractor.alignment.shared_peak_identity_explanation.shadow_labels import (
    build_shadow_alignment_summary,
    build_shadow_label_rows,
    build_shadow_readiness,
)
from xic_extractor.alignment.shared_peak_identity_explanation.writers import (
    write_shadow_outputs,
)


def test_shadow_labels_map_manual_evidence_classes_without_promotion() -> None:
    rows = build_shadow_label_rows(
        [
            _explanation(
                "row-pass-low",
                manual_label="pass",
                evidence_gap_class="machine_too_conservative_low_opportunity",
            ),
            _explanation(
                "row-fail-conflict",
                manual_label="fail",
                evidence_gap_class="machine_too_permissive_rt_pattern_conflict",
            ),
            _explanation(
                "row-shape-bad",
                manual_label="human_unjudgeable",
                evidence_gap_class="human_unjudgeable_shape_bad",
            ),
            _explanation(
                "row-context",
                manual_label="not_applicable",
                evidence_gap_class="delta_mass_related_context_only",
            ),
        ]
    )

    by_id = {row["oracle_row_id"]: row for row in rows}
    assert by_id["row-pass-low"]["shadow_label"] == "low_opportunity_supported"
    assert by_id["row-pass-low"]["shadow_alignment_status"] == "aligned"
    assert by_id["row-fail-conflict"]["shadow_label"] == "rt_pattern_conflict_blocked"
    assert by_id["row-fail-conflict"]["shadow_alignment_status"] == "aligned"
    assert by_id["row-shape-bad"]["shadow_alignment_status"] == "unjudgeable"
    assert by_id["row-context"]["shadow_alignment_status"] == "context_only"
    assert {row["diagnostic_only"] for row in rows} == {"TRUE"}


def test_shadow_readiness_is_exploratory_when_blast_radius_is_unassessed() -> None:
    shadow_rows = build_shadow_label_rows(
        [
            _explanation(
                "row-pass-low",
                manual_label="pass",
                evidence_gap_class="machine_too_conservative_low_opportunity",
            ),
            _explanation(
                "row-fail-conflict",
                manual_label="fail",
                evidence_gap_class="machine_too_permissive_rt_pattern_conflict",
            ),
        ]
    )

    readiness = build_shadow_readiness(
        run_facts={
            **_slice1_run_facts(),
            "blast_radius_assessed": "not_assessed",
            "max_overfit_risk": "unassessed",
        },
        shadow_rows=shadow_rows,
        machine_evidence_support_rows=[
            _support_row(
                "row-pass-low",
                evidence_support_status="machine_proxy_only",
                missing_machine_evidence="formal_shape_metric",
            )
        ],
    )

    assert readiness["shadow_gate_status"] == "exploratory_only"
    assert readiness["machine_only_labeler_ready"] == "FALSE"
    assert readiness["machine_evidence_basis"] == "machine_proxy_or_manual_derived"
    assert readiness["semantic_generalization_evidence"] == (
        "seed_only_manual_oracle_derived"
    )
    assert "blast_radius_not_current" in readiness["clear_answer"]
    assert "overfit_risk_unassessed" in readiness["clear_answer"]


def test_shadow_readiness_can_be_shadow_ready_candidate_after_current_low_risk() -> (
    None
):
    shadow_rows = build_shadow_label_rows(
        [
            _explanation(
                "row-pass",
                manual_label="pass",
                evidence_gap_class="machine_too_conservative_shape_or_pattern_unmodeled",
            ),
            _explanation(
                "row-fail",
                manual_label="fail",
                evidence_gap_class="machine_too_permissive_scope_rule_conflict",
            ),
        ]
    )

    readiness = build_shadow_readiness(
        run_facts={
            **_slice1_run_facts(),
            "blast_radius_assessed": "present_current",
            "max_overfit_risk": "low",
            "blast_radius_stale_artifact_count": "0",
        },
        shadow_rows=shadow_rows,
        machine_evidence_support_rows=[
            _support_row(
                "row-pass",
                evidence_support_status="machine_observed_sufficient",
            ),
            _support_row(
                "row-fail",
                evidence_support_status="machine_observed_sufficient",
            ),
        ],
    )

    assert readiness["shadow_gate_status"] == "shadow_ready_candidate"
    assert readiness["machine_only_labeler_ready"] == "TRUE"
    assert readiness["readiness_label"] == "diagnostic_only"
    assert readiness["machine_evidence_coverage_fraction"] == "1.000000"


def test_shadow_readiness_does_not_promote_without_machine_observed_evidence() -> None:
    shadow_rows = build_shadow_label_rows(
        [
            _explanation(
                "row-pass",
                manual_label="pass",
                evidence_gap_class="machine_too_conservative_shape_or_pattern_unmodeled",
            )
        ]
    )

    readiness = build_shadow_readiness(
        run_facts={
            **_slice1_run_facts(),
            "blast_radius_assessed": "present_current",
            "max_overfit_risk": "low",
            "blast_radius_stale_artifact_count": "0",
        },
        shadow_rows=shadow_rows,
        machine_evidence_support_rows=[
            _support_row(
                "row-pass",
                evidence_support_status="machine_proxy_only",
                missing_machine_evidence="formal_shape_metric",
            )
        ],
    )

    assert readiness["shadow_gate_status"] == "exploratory_only"
    assert "machine_evidence_not_sufficient" in readiness["clear_answer"]


def test_shadow_readiness_preserves_first_seen_unique_machine_evidence_blockers() -> (
    None
):
    shadow_rows = build_shadow_label_rows(
        [
            _explanation(
                "row-pass",
                manual_label="pass",
                evidence_gap_class="machine_too_conservative_shape_or_pattern_unmodeled",
            ),
            _explanation(
                "row-suspect",
                manual_label="suspect",
                evidence_gap_class="rt_drift_policy_gap",
            ),
        ]
    )

    readiness = build_shadow_readiness(
        run_facts={**_slice1_run_facts(), "blast_radius_assessed": "not_assessed"},
        shadow_rows=shadow_rows,
        machine_evidence_support_rows=[
            _support_row(
                "row-pass",
                evidence_support_status="machine_proxy_only",
                missing_machine_evidence=(
                    "formal_shape_metric;formal_pattern_metric;formal_shape_metric"
                ),
            ),
            _support_row(
                "row-suspect",
                evidence_support_status="blocked_missing_metric",
                missing_machine_evidence=(
                    "formal_pattern_metric;matrix_rt_drift_policy"
                ),
            ),
        ],
    )

    assert readiness["machine_evidence_blockers"] == (
        "formal_shape_metric;formal_pattern_metric;matrix_rt_drift_policy"
    )


def test_machine_evidence_support_marks_manual_derived_metric_gaps() -> None:
    explanations = [
        {
            **_explanation(
                "row-pass",
                manual_label="pass",
                evidence_gap_class="machine_too_conservative_low_opportunity",
            ),
            "manual_reason_tags": (
                "rt_close;shape_complete;pattern_similar;low_intensity;"
                "dda_stochastic_missing"
            ),
            "manual_scope": "reviewed_cell",
        }
    ]
    shadow_rows = build_shadow_label_rows(explanations)

    rows = machine_evidence_support.build_machine_evidence_support_rows(
        explanations=explanations,
        shadow_rows=shadow_rows,
        machine_matches={
            "row-pass": (
                _machine_match(
                    source_role="rescued_cell",
                    sample_level=True,
                    row={
                        "status": "backfilled",
                        "apex_rt": "1.0",
                        "peak_start_rt": "0.9",
                        "peak_end_rt": "1.1",
                        "rt_delta_sec": "0.0",
                        "trace_quality": "owner_backfill",
                        "scan_support_score": "0.25",
                        "reason": "low scan support",
                    },
                ),
                _machine_match(
                    source_role="selected_peak",
                    sample_level=False,
                    row={
                        "feature_family_id": "row",
                        "neutral_loss_tag": "loss_116",
                        "family_product_mz": "123.0",
                        "family_observed_neutral_loss_da": "116.0",
                    },
                ),
            )
        },
    )

    row = rows[0]
    assert row["rt_basis_status"] == "machine_observed"
    assert row["shape_basis_status"] == "mixed"
    assert row["pattern_basis_status"] == "mixed"
    assert row["opportunity_basis_status"] == "mixed"
    assert row["evidence_support_status"] == "machine_proxy_only"
    assert "formal_shape_metric" in row["missing_machine_evidence"]
    assert "formal_pattern_metric" in row["missing_machine_evidence"]
    assert (
        "cid_nl_pattern_context=family_level_present" in row["observed_machine_metrics"]
    )
    assert "neutral_loss_tag=loss_116" in row["observed_machine_metrics"]
    assert "zhang_2014_eic_quality" in row["literature_support_refs"]
    assert "neutral_loss_product_ion_annotation" in row["literature_support_refs"]
    assert "koelmel_2017_iterative_exclusion" in row["literature_support_refs"]


def test_machine_evidence_support_uses_cwt_and_tier2_as_observed_metrics() -> None:
    explanations = [
        {
            **_explanation(
                "FAM001|S1",
                manual_label="pass",
                evidence_gap_class="machine_too_conservative_low_opportunity",
            ),
            "feature_family_id": "FAM001",
            "sample_id": "S1",
            "manual_reason_tags": (
                "rt_close;shape_complete;pattern_similar;low_intensity;"
                "dda_stochastic_missing"
            ),
        }
    ]
    shadow_rows = build_shadow_label_rows(explanations)

    rows = machine_evidence_support.build_machine_evidence_support_rows(
        explanations=explanations,
        shadow_rows=shadow_rows,
        machine_matches={
            "FAM001|S1": (
                _machine_match(
                    source_role="rescued_cell",
                    sample_level=True,
                    row={
                        "status": "backfilled",
                        "apex_rt": "1.0",
                        "peak_start_rt": "0.9",
                        "peak_end_rt": "1.1",
                        "rt_delta_sec": "0.0",
                        "trace_quality": "owner_backfill",
                        "scan_support_score": "0.25",
                        "reason": "low scan support",
                    },
                ),
            )
        },
        cwt_shape_evidence={
            ("FAM001", "S1"): {
                "cwt_status": "OK",
                "cwt_shape_status": "cwt_near_expected",
                "cwt_apex_delta_sec": "0.01",
                "cwt_boundary_width_sec": "5.0",
                "cwt_prominence": "1000",
                "cwt_region_scan_count": "3",
                "cwt_quality_flags": "",
            }
        },
        tier2_trace_evidence={
            "FAM001": {
                "raw_trace_reread_status": "pass",
                "scan_support_score": "1",
                "trace_scan_count": "7",
                "scan_availability_score": "1",
                "trace_signal_to_noise_proxy": "4.0",
                "trace_apex_prominence_score": "0.5",
                "challenge_blockers": "",
            }
        },
    )

    row = rows[0]
    assert row["shape_basis_status"] == "machine_observed"
    assert row["opportunity_basis_status"] == "machine_observed"
    assert row["evidence_support_status"] == "machine_observed_partial"
    assert "formal_shape_metric" not in row["missing_machine_evidence"]
    assert "intensity_opportunity_metric" not in row["missing_machine_evidence"]
    assert "formal_pattern_metric" in row["missing_machine_evidence"]
    assert "dda_opportunity_policy" in row["missing_machine_evidence"]
    assert "cwt_status=OK" in row["observed_machine_metrics"]
    assert "tier2_raw_trace_status=pass" in row["observed_machine_metrics"]


def test_dda_missing_nl_policy_can_be_non_dispositive_with_ms1_support() -> None:
    explanations = [
        {
            **_explanation(
                "FAM001|S1",
                manual_label="pass",
                evidence_gap_class="machine_too_conservative_low_opportunity",
            ),
            "feature_family_id": "FAM001",
            "sample_id": "S1",
            "manual_reason_tags": (
                "rt_close;shape_complete;pattern_similar;low_intensity;"
                "dda_stochastic_missing"
            ),
        }
    ]
    shadow_rows = build_shadow_label_rows(explanations)

    rows = machine_evidence_support.build_machine_evidence_support_rows(
        explanations=explanations,
        shadow_rows=shadow_rows,
        machine_matches={
            "FAM001|S1": (
                _machine_match(
                    source_role="rescued_cell",
                    sample_level=True,
                    row={
                        "status": "backfilled",
                        "apex_rt": "1.0",
                        "peak_start_rt": "0.9",
                        "peak_end_rt": "1.1",
                        "rt_delta_sec": "0.0",
                        "trace_quality": "owner_backfill",
                        "scan_support_score": "1",
                        "reason": "supported",
                    },
                ),
            )
        },
        cwt_shape_evidence={
            ("FAM001", "S1"): {
                "cwt_status": "OK",
                "cwt_shape_status": "cwt_near_expected",
                "cwt_apex_delta_sec": "0.01",
                "cwt_boundary_width_sec": "5.0",
                "cwt_prominence": "1000",
                "cwt_region_scan_count": "3",
                "cwt_quality_flags": "",
            }
        },
        tier2_trace_evidence={
            "FAM001": {
                "raw_trace_reread_status": "pass",
                "scan_support_score": "1",
                "trace_scan_count": "7",
                "scan_availability_score": "1",
                "trace_signal_to_noise_proxy": "4.0",
                "trace_apex_prominence_score": "0.5",
                "challenge_blockers": "",
            }
        },
        candidate_ms2_pattern_evidence={
            ("FAM001", "S1"): {
                "candidate_ms2_pattern_status": "not_observed",
                "candidate_ms2_evidence_level": "sample_boundary_no_observed_pattern",
                "raw_ms2_trigger_scan_count": "3",
                "raw_ms2_strict_nl_scan_count": "0",
                "raw_ms2_trace_strength": "moderate",
                "raw_ms2_diagnostic_product_absence_reason": "no_strict_nl_match",
            },
            ("FAM001", "S2"): {
                "candidate_ms2_pattern_status": "supportive",
                "candidate_ms2_evidence_level": "sample_boundary_aligned",
                "raw_ms2_trigger_scan_count": "3",
                "raw_ms2_strict_nl_scan_count": "1",
                "raw_ms2_trace_strength": "strong",
                "raw_ms2_diagnostic_product_absence_reason": "",
            },
        },
        ms1_pattern_coherence_evidence={
            ("FAM001", "S1"): {
                "ms1_pattern_status": "supportive",
                "ms1_pattern_evidence_level": "sample_constellation",
                "apex_coherence_sec": "1.0",
                "boundary_overlap_score": "0.9",
                "shape_correlation_score": "0.8",
                "relative_pattern_stability_score": "0.8",
                "local_interference_score": "0.05",
                "constellation_peak_count": "3",
                "reference_peak_count": "3",
                "drift_compatible_status": "compatible",
                "cell_height": "26000",
                "reason": "ms1_pattern_supports_identity",
                "diagnostic_only": "TRUE",
            }
        },
    )

    row = rows[0]
    assert "dda_opportunity_policy" not in row["missing_machine_evidence"]
    assert row["opportunity_basis_status"] == "machine_observed"
    assert (
        "dda_missing_nl_policy_status=not_dispositive"
        in row["observed_machine_metrics"]
    )
    assert "candidate_ms2_raw_trigger_scan_count=3" in row["observed_machine_metrics"]
    assert (
        "family_ms2_required_tag_status=observed_in_family"
        in row["observed_machine_metrics"]
    )


def test_ms1_overlay_height_closes_intensity_opportunity_without_tier2() -> None:
    explanations = [
        {
            **_explanation(
                "FAM001|S1",
                manual_label="pass",
                evidence_gap_class="machine_too_conservative_low_opportunity",
            ),
            "feature_family_id": "FAM001",
            "sample_id": "S1",
            "manual_reason_tags": "low_intensity",
        }
    ]
    shadow_rows = build_shadow_label_rows(explanations)

    rows = machine_evidence_support.build_machine_evidence_support_rows(
        explanations=explanations,
        shadow_rows=shadow_rows,
        machine_matches={
            "FAM001|S1": (
                _machine_match(
                    source_role="rescued_cell",
                    sample_level=True,
                    row={
                        "status": "backfilled",
                        "apex_rt": "1.0",
                        "peak_start_rt": "0.9",
                        "peak_end_rt": "1.1",
                        "rt_delta_sec": "0.0",
                        "trace_quality": "owner_backfill",
                        "scan_support_score": "1",
                        "reason": "supported",
                    },
                ),
            )
        },
        ms1_pattern_coherence_evidence={
            ("FAM001", "S1"): {
                "ms1_pattern_status": "supportive",
                "ms1_pattern_evidence_level": "trace_constellation",
                "apex_coherence_sec": "1.0",
                "boundary_overlap_score": "0.9",
                "shape_correlation_score": "0.95",
                "relative_pattern_stability_score": "0.9",
                "local_interference_score": "0.05",
                "constellation_peak_count": "3",
                "reference_peak_count": "3",
                "drift_compatible_status": "compatible",
                "shape_metric_source": "family_ms1_overlay_raw_trace",
                "cell_height": "26000",
                "local_window_max_intensity": "26000",
                "trace_max_intensity": "26000",
                "reason": "family_ms1_overlay_shape_supported",
                "diagnostic_only": "TRUE",
            }
        },
    )

    row = rows[0]
    assert row["opportunity_basis_status"] == "machine_observed"
    assert row["evidence_support_status"] == "machine_observed_sufficient"
    assert "intensity_opportunity_metric" not in row["missing_machine_evidence"]
    assert (
        "ms1_intensity_opportunity_status=supported_by_raw_overlay_height"
        in row["observed_machine_metrics"]
    )


def test_dda_missing_nl_policy_requires_family_level_required_tag() -> None:
    explanations = [
        {
            **_explanation(
                "FAM001|S1",
                manual_label="pass",
                evidence_gap_class="machine_too_conservative_low_opportunity",
            ),
            "feature_family_id": "FAM001",
            "sample_id": "S1",
            "manual_reason_tags": (
                "rt_close;shape_complete;pattern_similar;low_intensity;"
                "dda_stochastic_missing"
            ),
        }
    ]
    shadow_rows = build_shadow_label_rows(explanations)

    rows = machine_evidence_support.build_machine_evidence_support_rows(
        explanations=explanations,
        shadow_rows=shadow_rows,
        machine_matches={
            "FAM001|S1": (
                _machine_match(
                    source_role="rescued_cell",
                    sample_level=True,
                    row={
                        "status": "backfilled",
                        "apex_rt": "1.0",
                        "peak_start_rt": "0.9",
                        "peak_end_rt": "1.1",
                        "rt_delta_sec": "0.0",
                        "trace_quality": "owner_backfill",
                        "scan_support_score": "1",
                        "reason": "supported",
                    },
                ),
            )
        },
        tier2_trace_evidence={
            "FAM001": {
                "raw_trace_reread_status": "pass",
                "scan_support_score": "1",
                "trace_scan_count": "7",
                "scan_availability_score": "1",
                "trace_signal_to_noise_proxy": "4.0",
                "trace_apex_prominence_score": "0.5",
                "challenge_blockers": "",
            }
        },
        candidate_ms2_pattern_evidence={
            ("FAM001", "S1"): {
                "candidate_ms2_pattern_status": "not_observed",
                "candidate_ms2_evidence_level": "sample_boundary_no_observed_pattern",
                "raw_ms2_trigger_scan_count": "3",
                "raw_ms2_strict_nl_scan_count": "0",
                "raw_ms2_trace_strength": "moderate",
                "raw_ms2_diagnostic_product_absence_reason": "no_strict_nl_match",
            }
        },
        ms1_pattern_coherence_evidence={
            ("FAM001", "S1"): {
                "ms1_pattern_status": "supportive",
                "ms1_pattern_evidence_level": "sample_constellation",
                "apex_coherence_sec": "1.0",
                "boundary_overlap_score": "0.9",
                "shape_correlation_score": "0.8",
                "relative_pattern_stability_score": "0.8",
                "local_interference_score": "0.05",
                "constellation_peak_count": "3",
                "reference_peak_count": "3",
                "drift_compatible_status": "compatible",
                "cell_height": "26000",
                "reason": "ms1_pattern_supports_identity",
                "diagnostic_only": "TRUE",
            }
        },
    )

    row = rows[0]
    assert "family_required_tag_gate" in row["missing_machine_evidence"]
    assert row["evidence_support_status"] == "machine_observed_conflict"
    assert (
        "dda_missing_nl_policy_status=family_required_tag_not_observed"
        in row["observed_machine_metrics"]
    )


def test_dda_missing_nl_policy_is_not_required_when_sample_tag_observed() -> None:
    explanations = [
        {
            **_explanation(
                "FAM001|S1",
                manual_label="pass",
                evidence_gap_class="machine_too_conservative_low_opportunity",
            ),
            "feature_family_id": "FAM001",
            "sample_id": "S1",
            "manual_reason_tags": (
                "rt_close;shape_complete;pattern_similar;low_intensity;"
                "dda_stochastic_missing"
            ),
        }
    ]
    shadow_rows = build_shadow_label_rows(explanations)

    rows = machine_evidence_support.build_machine_evidence_support_rows(
        explanations=explanations,
        shadow_rows=shadow_rows,
        machine_matches={
            "FAM001|S1": (
                _machine_match(
                    source_role="rescued_cell",
                    sample_level=True,
                    row={
                        "status": "backfilled",
                        "apex_rt": "1.0",
                        "peak_start_rt": "0.9",
                        "peak_end_rt": "1.1",
                        "rt_delta_sec": "0.0",
                        "trace_quality": "owner_backfill",
                        "scan_support_score": "1",
                        "reason": "supported",
                    },
                ),
            )
        },
        candidate_ms2_pattern_evidence={
            ("FAM001", "S1"): {
                "candidate_ms2_pattern_status": "supportive",
                "candidate_ms2_evidence_level": "sample_boundary_aligned",
                "raw_ms2_trigger_scan_count": "3",
                "raw_ms2_strict_nl_scan_count": "1",
                "raw_ms2_trace_strength": "moderate",
                "raw_ms2_diagnostic_product_absence_reason": "",
            }
        },
    )

    row = rows[0]
    assert "dda_opportunity_policy" not in row["missing_machine_evidence"]
    assert "family_required_tag_gate" not in row["missing_machine_evidence"]
    assert row["opportunity_basis_status"] == "machine_observed"
    assert (
        "dda_missing_nl_policy_status=sample_required_tag_observed"
        in row["observed_machine_metrics"]
    )


def test_dda_missing_nl_policy_accepts_existing_family_tag_context() -> None:
    explanations = [
        {
            **_explanation(
                "FAM001|S1",
                manual_label="pass",
                evidence_gap_class="machine_too_conservative_low_opportunity",
            ),
            "feature_family_id": "FAM001",
            "sample_id": "S1",
            "manual_reason_tags": (
                "rt_close;shape_complete;pattern_similar;low_intensity;"
                "dda_stochastic_missing"
            ),
        }
    ]
    shadow_rows = build_shadow_label_rows(explanations)

    rows = machine_evidence_support.build_machine_evidence_support_rows(
        explanations=explanations,
        shadow_rows=shadow_rows,
        machine_matches={
            "FAM001|S1": (
                _machine_match(
                    source_role="rescued_cell",
                    sample_level=True,
                    row={
                        "status": "backfilled",
                        "apex_rt": "1.0",
                        "peak_start_rt": "0.9",
                        "peak_end_rt": "1.1",
                        "rt_delta_sec": "0.0",
                        "trace_quality": "owner_backfill",
                        "scan_support_score": "1",
                        "reason": "supported",
                    },
                ),
                _machine_match(
                    source_role="selected_peak",
                    sample_level=False,
                    row={
                        "feature_family_id": "FAM001",
                        "neutral_loss_tag": "DNA_dR",
                        "family_product_mz": "229.041",
                        "family_observed_neutral_loss_da": "116.048",
                    },
                ),
            )
        },
        candidate_ms2_pattern_evidence={
            ("FAM001", "S1"): {
                "candidate_ms2_pattern_status": "not_observed",
                "candidate_ms2_evidence_level": "sample_boundary_no_observed_pattern",
                "raw_ms2_trigger_scan_count": "3",
                "raw_ms2_strict_nl_scan_count": "0",
                "raw_ms2_trace_strength": "moderate",
                "raw_ms2_diagnostic_product_absence_reason": "no_strict_nl_match",
            }
        },
        ms1_pattern_coherence_evidence={
            ("FAM001", "S1"): {
                "ms1_pattern_status": "supportive",
                "ms1_pattern_evidence_level": "sample_constellation",
                "apex_coherence_sec": "1.0",
                "boundary_overlap_score": "0.9",
                "shape_correlation_score": "0.8",
                "relative_pattern_stability_score": "0.8",
                "local_interference_score": "0.05",
                "constellation_peak_count": "3",
                "reference_peak_count": "3",
                "drift_compatible_status": "compatible",
                "cell_height": "26000",
                "reason": "ms1_pattern_supports_identity",
                "diagnostic_only": "TRUE",
            }
        },
    )

    row = rows[0]
    assert "family_required_tag_gate" not in row["missing_machine_evidence"]
    assert "dda_opportunity_policy" not in row["missing_machine_evidence"]
    assert (
        "cid_nl_pattern_context=family_level_present" in row["observed_machine_metrics"]
    )
    assert (
        "dda_missing_nl_policy_status=not_dispositive"
        in row["observed_machine_metrics"]
    )


def test_dda_missing_nl_policy_accepts_product_outside_diagnostic_window() -> None:
    explanations = [
        {
            **_explanation(
                "FAM001|S1",
                manual_label="pass",
                evidence_gap_class="machine_too_conservative_low_opportunity",
            ),
            "feature_family_id": "FAM001",
            "sample_id": "S1",
            "manual_reason_tags": (
                "rt_close;shape_complete;pattern_similar;low_intensity;"
                "dda_stochastic_missing"
            ),
        }
    ]
    shadow_rows = build_shadow_label_rows(explanations)

    rows = machine_evidence_support.build_machine_evidence_support_rows(
        explanations=explanations,
        shadow_rows=shadow_rows,
        machine_matches={
            "FAM001|S1": (
                _machine_match(
                    source_role="rescued_cell",
                    sample_level=True,
                    row={
                        "status": "backfilled",
                        "apex_rt": "1.0",
                        "peak_start_rt": "0.9",
                        "peak_end_rt": "1.1",
                        "rt_delta_sec": "0.0",
                        "trace_quality": "owner_backfill",
                        "scan_support_score": "1",
                        "reason": "supported",
                    },
                ),
                _machine_match(
                    source_role="selected_peak",
                    sample_level=False,
                    row={
                        "feature_family_id": "FAM001",
                        "neutral_loss_tag": "DNA_dR",
                        "family_product_mz": "229.041",
                        "family_observed_neutral_loss_da": "116.048",
                    },
                ),
            )
        },
        candidate_ms2_pattern_evidence={
            ("FAM001", "S1"): {
                "candidate_ms2_pattern_status": "not_observed",
                "candidate_ms2_evidence_level": "sample_boundary_no_observed_pattern",
                "raw_ms2_trigger_scan_count": "7",
                "raw_ms2_strict_nl_scan_count": "0",
                "raw_ms2_trace_strength": "none",
                "raw_ms2_diagnostic_product_absence_reason": (
                    "product_outside_diagnostic_window"
                ),
            }
        },
        ms1_pattern_coherence_evidence={
            ("FAM001", "S1"): {
                "ms1_pattern_status": "supportive",
                "ms1_pattern_evidence_level": "trace_constellation",
                "apex_coherence_sec": "1.0",
                "boundary_overlap_score": "0.9",
                "shape_correlation_score": "0.8",
                "relative_pattern_stability_score": "0.8",
                "local_interference_score": "0.05",
                "constellation_peak_count": "3",
                "reference_peak_count": "3",
                "drift_compatible_status": "compatible",
                "shape_metric_source": "family_ms1_overlay_raw_trace",
                "cell_height": "26000",
                "reason": "ms1_pattern_supports_identity",
                "diagnostic_only": "TRUE",
            }
        },
    )

    row = rows[0]
    assert "dda_opportunity_policy" not in row["missing_machine_evidence"]
    assert (
        "candidate_ms2_raw_absence_reason=product_outside_diagnostic_window"
        in row["observed_machine_metrics"]
    )
    assert (
        "dda_missing_nl_policy_status=not_dispositive"
        in row["observed_machine_metrics"]
    )


def test_wrong_peak_conflict_closes_pattern_without_marking_shape_bad() -> None:
    explanations = [
        {
            **_explanation(
                "FAM001|S1",
                manual_label="fail",
                evidence_gap_class="machine_too_permissive_rt_pattern_conflict",
            ),
            "feature_family_id": "FAM001",
            "sample_id": "S1",
            "manual_reason_tags": "rt_too_far;pattern_mismatch;shape_normal",
        }
    ]
    shadow_rows = build_shadow_label_rows(explanations)

    rows = machine_evidence_support.build_machine_evidence_support_rows(
        explanations=explanations,
        shadow_rows=shadow_rows,
        machine_matches={
            "FAM001|S1": (
                _machine_match(
                    source_role="rescued_cell",
                    sample_level=True,
                    row={
                        "status": "backfilled",
                        "apex_rt": "6.56",
                        "peak_start_rt": "6.2",
                        "peak_end_rt": "7.7",
                        "rt_delta_sec": "-39.0",
                        "trace_quality": "owner_backfill",
                        "scan_support_score": "1",
                        "reason": "supported",
                    },
                ),
            )
        },
        ms1_pattern_coherence_evidence={
            ("FAM001", "S1"): {
                "ms1_pattern_status": "conflict",
                "ms1_pattern_evidence_level": "trace_constellation",
                "apex_coherence_sec": "39.0",
                "boundary_overlap_score": "0.7",
                "shape_correlation_score": "0.9",
                "relative_pattern_stability_score": "0.8",
                "local_interference_score": "0.05",
                "constellation_peak_count": "2",
                "reference_peak_count": "3",
                "drift_compatible_status": "compatible",
                "shape_metric_source": "family_ms1_overlay_raw_trace",
                "family_ms1_overlay_verdict": "ms1_shape_supports_family_backfill",
                "cell_height": "2200000",
                "local_window_max_intensity": "2200000",
                "trace_max_intensity": "2200000",
                "cell_to_local_window_max_ratio": "1",
                "local_window_to_global_max_ratio": "1",
                "family_ms1_overlay_trace_data_json": "overlay.json",
                "peak_quality_vector_status": "supportive",
                "peak_quality_vector_basis": "family_ms1_overlay_raw_trace_vector",
                "reason": "family_ms1_overlay_competing_peak_matches_family_consensus",
                "diagnostic_only": "TRUE",
            }
        },
    )

    row = rows[0]
    assert "rt_pattern_conflict_gate" not in row["missing_machine_evidence"]
    assert "pattern_metric_not_supportive" not in row["missing_machine_evidence"]
    assert "shape_metric_not_supportive" not in row["missing_machine_evidence"]
    assert row["shape_basis_status"] == "machine_observed"
    assert row["pattern_basis_status"] == "machine_observed"
    assert (
        "ms1_pattern_reason="
        "family_ms1_overlay_competing_peak_matches_family_consensus"
        in row["observed_machine_metrics"]
    )


def test_sample_negative_evidence_sidecar_closes_scope_fail_gap() -> None:
    explanations = [
        {
            **_explanation(
                "FAM001|S1",
                manual_label="fail",
                evidence_gap_class="machine_too_permissive_scope_rule_conflict",
            ),
            "feature_family_id": "FAM001",
            "sample_id": "S1",
            "manual_reason_tags": "scope_derived_unmentioned_fail",
            "manual_scope": "scope_derived_unmentioned_fail",
        }
    ]
    shadow_rows = build_shadow_label_rows(explanations)

    rows = machine_evidence_support.build_machine_evidence_support_rows(
        explanations=explanations,
        shadow_rows=shadow_rows,
        machine_matches={},
        sample_negative_evidence={
            ("FAM001", "S1"): {
                "negative_evidence_class": "local_peak_not_decisive",
                "negative_evidence_detail": "ugly_shape",
                "negative_evidence_level": "machine_observed",
                "reason": "local_region_not_decisive",
                "diagnostic_only": "TRUE",
            }
        },
    )

    row = rows[0]
    assert row["negative_evidence_basis_status"] == "machine_observed"
    assert row["negative_evidence_class"] == "local_peak_not_decisive"
    assert row["negative_evidence_detail"] == "ugly_shape"
    assert row["scope_basis_status"] == "machine_observed"
    assert "manual_scope_policy" not in row["missing_machine_evidence"]
    assert "sample_level_negative_evidence" not in row["missing_machine_evidence"]
    assert "sample_level_machine_observation" not in row["missing_machine_evidence"]
    assert row["evidence_support_status"] == "machine_observed_sufficient"


def test_machine_evidence_support_marks_no_missing_machine_basis_sufficient() -> None:
    explanations = [
        {
            **_explanation(
                "FAM001|S1",
                manual_label="pass",
                evidence_gap_class="machine_agrees_with_manual",
            ),
            "feature_family_id": "FAM001",
            "sample_id": "S1",
            "manual_reason_tags": "rt_close;shape_complete;pattern_similar",
        }
    ]
    shadow_rows = build_shadow_label_rows(explanations)

    rows = machine_evidence_support.build_machine_evidence_support_rows(
        explanations=explanations,
        shadow_rows=shadow_rows,
        machine_matches={
            "FAM001|S1": (
                _machine_match(
                    source_role="rescued_cell",
                    sample_level=True,
                    row={
                        "status": "backfilled",
                        "apex_rt": "1.0",
                        "peak_start_rt": "0.9",
                        "peak_end_rt": "1.1",
                        "rt_delta_sec": "0.0",
                        "trace_quality": "owner_backfill",
                    },
                ),
                _machine_match(
                    source_role="selected_peak",
                    sample_level=False,
                    row={
                        "feature_family_id": "FAM001",
                        "neutral_loss_tag": "loss_116",
                        "family_product_mz": "230.0",
                        "family_observed_neutral_loss_da": "116.0",
                    },
                ),
            )
        },
        cwt_shape_evidence={
            ("FAM001", "S1"): {
                "cwt_status": "OK",
                "cwt_shape_status": "cwt_near_expected",
                "cwt_apex_delta_sec": "0.01",
                "cwt_boundary_width_sec": "5.0",
                "cwt_prominence": "1000",
                "cwt_region_scan_count": "3",
                "cwt_quality_flags": "",
            }
        },
        candidate_ms2_pattern_evidence={
            ("FAM001", "S1"): {
                "candidate_ms2_pattern_status": "supportive",
                "candidate_ms2_evidence_level": "sample_boundary_aligned",
            }
        },
    )

    row = rows[0]
    assert row["manual_derived_facts"] == "pattern_similar;rt_close;shape_complete"
    assert row["missing_machine_evidence"] == ""
    assert row["evidence_support_status"] == "machine_observed_sufficient"


def test_ms1_pattern_coherence_can_close_formal_pattern_metric() -> None:
    explanations = [
        {
            **_explanation(
                "FAM001|S1",
                manual_label="pass",
                evidence_gap_class="machine_too_conservative_shape_or_pattern_unmodeled",
            ),
            "feature_family_id": "FAM001",
            "sample_id": "S1",
            "manual_reason_tags": "rt_close;pattern_similar",
        }
    ]
    shadow_rows = build_shadow_label_rows(explanations)

    rows = machine_evidence_support.build_machine_evidence_support_rows(
        explanations=explanations,
        shadow_rows=shadow_rows,
        machine_matches={
            "FAM001|S1": (
                _machine_match(
                    source_role="rescued_cell",
                    sample_level=True,
                    row={
                        "status": "backfilled",
                        "apex_rt": "6.04",
                        "peak_start_rt": "5.90",
                        "peak_end_rt": "6.20",
                        "rt_delta_sec": "0.0",
                        "trace_quality": "owner_backfill",
                    },
                ),
            )
        },
        ms1_pattern_coherence_evidence={
            ("FAM001", "S1"): {
                "ms1_pattern_status": "supportive",
                "ms1_pattern_evidence_level": "sample_constellation",
                "apex_coherence_sec": "3.0",
                "boundary_overlap_score": "0.82",
                "shape_correlation_score": "0.91",
                "relative_pattern_stability_score": "0.77",
                "local_interference_score": "0.05",
                "constellation_peak_count": "3",
                "reference_peak_count": "3",
                "drift_compatible_status": "compatible",
                "reason": "unit_test_ms1_constellation",
                "diagnostic_only": "TRUE",
            }
        },
    )

    row = rows[0]
    assert row["pattern_basis_status"] == "machine_observed"
    assert "formal_pattern_metric" not in row["missing_machine_evidence"]
    assert "ms1_pattern_status=supportive" in row["observed_machine_metrics"]
    assert row["evidence_support_status"] == "machine_observed_sufficient"


def test_qc_ms1_reference_can_close_formal_pattern_metric() -> None:
    explanations = [
        {
            **_explanation(
                "FAM001|S1",
                manual_label="pass",
                evidence_gap_class="machine_too_conservative_shape_or_pattern_unmodeled",
            ),
            "feature_family_id": "FAM001",
            "sample_id": "S1",
            "manual_reason_tags": "pattern_similar",
        }
    ]
    shadow_rows = build_shadow_label_rows(explanations)

    rows = machine_evidence_support.build_machine_evidence_support_rows(
        explanations=explanations,
        shadow_rows=shadow_rows,
        machine_matches={
            "FAM001|S1": (
                _machine_match(
                    source_role="rescued_cell",
                    sample_level=True,
                    row={"status": "backfilled"},
                ),
            )
        },
        qc_ms1_pattern_reference_evidence={
            ("FAM001", "S1"): {
                "qc_reference_status": "partial_support",
                "qc_reference_evidence_level": "qc_consensus_with_local_qc_overlay",
                "qc_reference_policy": "qc_consensus_with_local_support",
                "local_qc_reference_status": "supportive",
                "qc_consensus_status": "partial_support",
                "qc_reference_conflict_status": "none",
                "nearest_qc_sample_stem": "Breast_Cancer_Tissue_pooled_QC5",
                "nearest_qc_injection_order_delta": "2",
                "target_qc_apex_abs_delta_sec": "2.32",
                "target_qc_shape_similarity": "0.666",
                "target_local_window_to_global_max_ratio": "1",
                "nearest_qc_local_window_to_global_max_ratio": "1",
            }
        },
    )

    row = rows[0]
    assert row["pattern_basis_status"] == "machine_observed"
    assert "formal_pattern_metric" not in row["missing_machine_evidence"]
    assert "qc_ms1_reference_status=partial_support" in row["observed_machine_metrics"]
    assert row["evidence_support_status"] == "machine_observed_sufficient"


def test_qc_ms1_reference_conflict_cannot_override_sample_ms1_support() -> None:
    explanations = [
        {
            **_explanation(
                "FAM001|S1",
                manual_label="pass",
                evidence_gap_class="machine_too_conservative_shape_or_pattern_unmodeled",
            ),
            "feature_family_id": "FAM001",
            "sample_id": "S1",
            "manual_reason_tags": "pattern_partial",
        }
    ]
    shadow_rows = build_shadow_label_rows(explanations)

    rows = machine_evidence_support.build_machine_evidence_support_rows(
        explanations=explanations,
        shadow_rows=shadow_rows,
        machine_matches={
            "FAM001|S1": (
                _machine_match(
                    source_role="rescued_cell",
                    sample_level=True,
                    row={"status": "backfilled"},
                ),
            )
        },
        ms1_pattern_coherence_evidence={
            ("FAM001", "S1"): {
                "ms1_pattern_status": "supportive",
                "ms1_pattern_evidence_level": "trace_constellation",
                "apex_coherence_sec": "3.0",
                "boundary_overlap_score": "0.82",
                "shape_correlation_score": "0.91",
                "relative_pattern_stability_score": "0.77",
                "local_interference_score": "0.05",
                "constellation_peak_count": "3",
                "reference_peak_count": "3",
                "drift_compatible_status": "compatible",
                "reason": "unit_test_ms1_constellation",
                "diagnostic_only": "TRUE",
            }
        },
        qc_ms1_pattern_reference_evidence={
            ("FAM001", "S1"): {
                "qc_reference_status": "conflict",
                "qc_reference_evidence_level": "qc_consensus_with_local_qc_overlay",
                "qc_reference_policy": "qc_consensus_with_local_conflict",
                "local_qc_reference_status": "conflict",
                "qc_consensus_status": "conflict",
                "qc_reference_conflict_status": "none",
                "nearest_qc_sample_stem": "Breast_Cancer_Tissue_pooled_QC5",
                "nearest_qc_injection_order_delta": "2",
                "target_qc_apex_abs_delta_sec": "37.39",
                "target_qc_shape_similarity": "0.96",
                "target_local_window_to_global_max_ratio": "1",
                "nearest_qc_local_window_to_global_max_ratio": "1",
            }
        },
    )

    row = rows[0]
    assert row["pattern_basis_status"] == "machine_observed"
    assert "pattern_metric_not_supportive" not in row["missing_machine_evidence"]
    assert row["evidence_support_status"] == "machine_observed_sufficient"
    assert "ms1_pattern_status=supportive" in row["observed_machine_metrics"]
    assert "qc_ms1_reference_status=conflict" in row["observed_machine_metrics"]


def test_legacy_nearest_only_qc_reference_stays_context_only() -> None:
    explanations = [
        {
            **_explanation(
                "FAM001|S1",
                manual_label="pass",
                evidence_gap_class="machine_too_conservative_shape_or_pattern_unmodeled",
            ),
            "feature_family_id": "FAM001",
            "sample_id": "S1",
            "manual_reason_tags": "pattern_similar",
        }
    ]
    shadow_rows = build_shadow_label_rows(explanations)

    rows = machine_evidence_support.build_machine_evidence_support_rows(
        explanations=explanations,
        shadow_rows=shadow_rows,
        machine_matches={
            "FAM001|S1": (
                _machine_match(
                    source_role="rescued_cell",
                    sample_level=True,
                    row={"status": "backfilled"},
                ),
            )
        },
        qc_ms1_pattern_reference_evidence={
            ("FAM001", "S1"): {
                "qc_reference_status": "partial_support",
                "qc_reference_evidence_level": "nearest_complete_peak_qc_overlay",
                "nearest_qc_sample_stem": "Breast_Cancer_Tissue_pooled_QC5",
                "nearest_qc_injection_order_delta": "2",
                "target_qc_apex_abs_delta_sec": "2.32",
                "target_qc_shape_similarity": "0.666",
                "target_local_window_to_global_max_ratio": "1",
                "nearest_qc_local_window_to_global_max_ratio": "1",
            }
        },
    )

    row = rows[0]
    assert row["pattern_basis_status"] == "manual_oracle_derived"
    assert "formal_pattern_metric" in row["missing_machine_evidence"]
    assert "qc_ms1_reference_status=partial_support" in row["observed_machine_metrics"]


def test_ms1_overlay_shape_metric_can_close_formal_shape_metric() -> None:
    explanations = [
        {
            **_explanation(
                "FAM001|S1",
                manual_label="pass",
                evidence_gap_class="machine_too_conservative_shape_or_pattern_unmodeled",
            ),
            "feature_family_id": "FAM001",
            "sample_id": "S1",
            "manual_reason_tags": "shape_complete;pattern_similar",
        }
    ]
    shadow_rows = build_shadow_label_rows(explanations)

    rows = machine_evidence_support.build_machine_evidence_support_rows(
        explanations=explanations,
        shadow_rows=shadow_rows,
        machine_matches={
            "FAM001|S1": (
                _machine_match(
                    source_role="rescued_cell",
                    sample_level=True,
                    row={
                        "status": "backfilled",
                        "apex_rt": "6.04",
                        "peak_start_rt": "5.90",
                        "peak_end_rt": "6.20",
                        "rt_delta_sec": "0.0",
                        "trace_quality": "owner_backfill",
                    },
                ),
            )
        },
        ms1_pattern_coherence_evidence={
            ("FAM001", "S1"): {
                "ms1_pattern_status": "supportive",
                "ms1_pattern_evidence_level": "trace_constellation",
                "apex_coherence_sec": "3.0",
                "boundary_overlap_score": "0.82",
                "shape_correlation_score": "0.94",
                "relative_pattern_stability_score": "0.77",
                "local_interference_score": "0.05",
                "constellation_peak_count": "3",
                "reference_peak_count": "3",
                "drift_compatible_status": "compatible",
                "reason": "family_ms1_overlay_shape_supported",
                "diagnostic_only": "TRUE",
                "shape_metric_source": "family_ms1_overlay_raw_trace",
                "family_ms1_overlay_verdict": "ms1_shape_supports_family_backfill",
                "local_window_to_global_max_ratio": "0.98",
                "local_window_apex_delta_sec": "0.6",
                "global_trace_apex_delta_sec": "1.2",
                "family_ms1_overlay_trace_data_json": "fam001_overlay_trace_data.json",
                "peak_quality_vector_status": "supportive",
                "peak_quality_vector_basis": "family_ms1_overlay_raw_trace_vector",
                "peak_quality_trace_point_count": "7",
                "peak_quality_boundary_point_count": "3",
                "peak_quality_signal_to_noise_proxy": "12",
                "peak_quality_fwhm_sec": "8",
                "peak_quality_sharpness_score": "0.88",
                "peak_quality_zigzag_score": "0",
                "peak_quality_tailing_ratio": "1.1",
                "peak_quality_boundary_margin_ratio": "70",
                "peak_quality_feature_count": "6",
                "peak_quality_vector_reason": (
                    "raw_trace_peak_quality_vector_supportive"
                ),
            }
        },
    )

    row = rows[0]
    assert row["shape_basis_status"] == "machine_observed"
    assert "formal_shape_metric" not in row["missing_machine_evidence"]
    assert "formal_pattern_metric" not in row["missing_machine_evidence"]
    assert (
        "ms1_shape_metric_source=family_ms1_overlay_raw_trace"
        in row["observed_machine_metrics"]
    )
    assert (
        "ms1_peak_quality_vector_basis=family_ms1_overlay_raw_trace_vector"
        in row["observed_machine_metrics"]
    )
    assert row["evidence_support_status"] == "machine_observed_sufficient"


def test_direct_ms1_pattern_shape_score_does_not_close_formal_shape_metric() -> None:
    explanations = [
        {
            **_explanation(
                "FAM001|S1",
                manual_label="pass",
                evidence_gap_class="machine_too_conservative_shape_or_pattern_unmodeled",
            ),
            "feature_family_id": "FAM001",
            "sample_id": "S1",
            "manual_reason_tags": "shape_complete;pattern_similar",
        }
    ]
    shadow_rows = build_shadow_label_rows(explanations)

    rows = machine_evidence_support.build_machine_evidence_support_rows(
        explanations=explanations,
        shadow_rows=shadow_rows,
        machine_matches={
            "FAM001|S1": (
                _machine_match(
                    source_role="rescued_cell",
                    sample_level=True,
                    row={
                        "status": "backfilled",
                        "apex_rt": "6.04",
                        "peak_start_rt": "5.90",
                        "peak_end_rt": "6.20",
                        "rt_delta_sec": "0.0",
                        "trace_quality": "owner_backfill",
                    },
                ),
            )
        },
        ms1_pattern_coherence_evidence={
            ("FAM001", "S1"): {
                "ms1_pattern_status": "supportive",
                "ms1_pattern_evidence_level": "sample_constellation",
                "apex_coherence_sec": "3.0",
                "boundary_overlap_score": "0.82",
                "shape_correlation_score": "0.94",
                "relative_pattern_stability_score": "0.77",
                "local_interference_score": "0.05",
                "constellation_peak_count": "3",
                "reference_peak_count": "3",
                "drift_compatible_status": "compatible",
                "reason": "unit_test_ms1_constellation",
                "diagnostic_only": "TRUE",
            }
        },
    )

    row = rows[0]
    assert "formal_shape_metric" in row["missing_machine_evidence"]
    assert "formal_pattern_metric" not in row["missing_machine_evidence"]
    assert row["evidence_support_status"] == "machine_observed_partial"


def test_ms1_overlay_without_peak_quality_vector_does_not_close_shape() -> None:
    explanations = [
        {
            **_explanation(
                "FAM001|S1",
                manual_label="pass",
                evidence_gap_class="machine_too_conservative_shape_or_pattern_unmodeled",
            ),
            "feature_family_id": "FAM001",
            "sample_id": "S1",
            "manual_reason_tags": "shape_complete;pattern_similar",
        }
    ]
    shadow_rows = build_shadow_label_rows(explanations)

    rows = machine_evidence_support.build_machine_evidence_support_rows(
        explanations=explanations,
        shadow_rows=shadow_rows,
        machine_matches={
            "FAM001|S1": (
                _machine_match(
                    source_role="rescued_cell",
                    sample_level=True,
                    row={
                        "status": "backfilled",
                        "apex_rt": "6.04",
                        "peak_start_rt": "5.90",
                        "peak_end_rt": "6.20",
                        "rt_delta_sec": "0.0",
                        "trace_quality": "owner_backfill",
                    },
                ),
            )
        },
        ms1_pattern_coherence_evidence={
            ("FAM001", "S1"): {
                "ms1_pattern_status": "supportive",
                "ms1_pattern_evidence_level": "trace_constellation",
                "apex_coherence_sec": "3.0",
                "boundary_overlap_score": "0.82",
                "shape_correlation_score": "0.94",
                "relative_pattern_stability_score": "0.77",
                "local_interference_score": "0.05",
                "constellation_peak_count": "3",
                "reference_peak_count": "3",
                "drift_compatible_status": "compatible",
                "reason": "family_ms1_overlay_shape_supported",
                "diagnostic_only": "TRUE",
                "shape_metric_source": "family_ms1_overlay_raw_trace",
                "family_ms1_overlay_verdict": "ms1_shape_supports_family_backfill",
                "local_window_to_global_max_ratio": "0.98",
                "local_window_apex_delta_sec": "0.6",
                "global_trace_apex_delta_sec": "1.2",
                "family_ms1_overlay_trace_data_json": "legacy_overlay.json",
            }
        },
    )

    row = rows[0]
    assert row["shape_basis_status"] == "mixed"
    assert "formal_shape_metric" in row["missing_machine_evidence"]
    assert "formal_pattern_metric" not in row["missing_machine_evidence"]


def test_raw_ms1_overlay_low_shape_local_peak_is_inconclusive() -> None:
    explanations = [
        {
            **_explanation(
                "FAM001|S1",
                manual_label="pass",
                evidence_gap_class="machine_too_conservative_shape_or_pattern_unmodeled",
            ),
            "feature_family_id": "FAM001",
            "sample_id": "S1",
            "manual_reason_tags": "shape_complete;pattern_similar",
        }
    ]
    shadow_rows = build_shadow_label_rows(explanations)

    rows = machine_evidence_support.build_machine_evidence_support_rows(
        explanations=explanations,
        shadow_rows=shadow_rows,
        machine_matches={
            "FAM001|S1": (
                _machine_match(
                    source_role="rescued_cell",
                    sample_level=True,
                    row={
                        "status": "backfilled",
                        "apex_rt": "6.04",
                        "peak_start_rt": "5.90",
                        "peak_end_rt": "6.20",
                        "rt_delta_sec": "0.0",
                        "trace_quality": "owner_backfill",
                    },
                ),
            )
        },
        ms1_pattern_coherence_evidence={
            ("FAM001", "S1"): {
                "ms1_pattern_status": "partial_support",
                "ms1_pattern_evidence_level": "trace_constellation",
                "apex_coherence_sec": "3.0",
                "boundary_overlap_score": "0.82",
                "shape_correlation_score": "0.16",
                "relative_pattern_stability_score": "0.77",
                "local_interference_score": "0.05",
                "constellation_peak_count": "3",
                "reference_peak_count": "3",
                "drift_compatible_status": "compatible",
                "reason": (
                    "family_ms1_overlay_shape_metric_inconclusive_apex_or_height"
                ),
                "diagnostic_only": "TRUE",
                "shape_metric_source": "family_ms1_overlay_raw_trace",
                "family_ms1_overlay_verdict": "ms1_shape_supports_family_backfill",
                "local_window_to_global_max_ratio": "1",
                "local_window_apex_delta_sec": "5",
                "global_trace_apex_delta_sec": "5",
                "family_ms1_overlay_trace_data_json": "fam001_overlay_trace_data.json",
                "peak_quality_vector_status": "partial_support",
                "peak_quality_vector_basis": "family_ms1_overlay_raw_trace_vector",
                "peak_quality_trace_point_count": "7",
                "peak_quality_boundary_point_count": "3",
                "peak_quality_signal_to_noise_proxy": "2",
                "peak_quality_fwhm_sec": "8",
                "peak_quality_sharpness_score": "0.88",
                "peak_quality_zigzag_score": "0",
                "peak_quality_tailing_ratio": "1.1",
                "peak_quality_boundary_margin_ratio": "70",
                "peak_quality_feature_count": "6",
                "peak_quality_vector_reason": (
                    "raw_trace_peak_quality_vector_partial_low_signal_to_noise_proxy"
                ),
            }
        },
    )

    row = rows[0]
    assert "formal_shape_metric" not in row["missing_machine_evidence"]
    assert "formal_pattern_metric" not in row["missing_machine_evidence"]
    assert "shape_metric_not_supportive" not in row["missing_machine_evidence"]
    assert "pattern_metric_not_supportive" not in row["missing_machine_evidence"]
    assert "shape_metric_inconclusive_apex_or_height" in row["missing_machine_evidence"]
    assert (
        "pattern_metric_inconclusive_apex_or_height" in row["missing_machine_evidence"]
    )
    assert row["evidence_support_status"] == "machine_observed_partial"


def test_ms1_pattern_coherence_conflict_fails_closed() -> None:
    explanations = [
        {
            **_explanation(
                "FAM001|S1",
                manual_label="fail",
                evidence_gap_class="machine_too_permissive_rt_pattern_conflict",
            ),
            "feature_family_id": "FAM001",
            "sample_id": "S1",
            "manual_reason_tags": "pattern_mismatch",
        }
    ]
    shadow_rows = build_shadow_label_rows(explanations)

    rows = machine_evidence_support.build_machine_evidence_support_rows(
        explanations=explanations,
        shadow_rows=shadow_rows,
        machine_matches={
            "FAM001|S1": (
                _machine_match(
                    source_role="rescued_cell",
                    sample_level=True,
                    row={
                        "status": "backfilled",
                        "apex_rt": "6.04",
                        "peak_start_rt": "5.90",
                        "peak_end_rt": "6.20",
                        "rt_delta_sec": "0.0",
                        "trace_quality": "owner_backfill",
                    },
                ),
            )
        },
        ms1_pattern_coherence_evidence={
            ("FAM001", "S1"): {
                "ms1_pattern_status": "supportive",
                "ms1_pattern_evidence_level": "sample_constellation",
                "apex_coherence_sec": "3.0",
                "boundary_overlap_score": "0.82",
                "shape_correlation_score": "0.91",
                "relative_pattern_stability_score": "0.77",
                "local_interference_score": "0.05",
                "constellation_peak_count": "3",
                "reference_peak_count": "3",
                "drift_compatible_status": "compatible",
                "reason": "unit_test_ms1_constellation",
                "diagnostic_only": "TRUE",
            }
        },
    )

    row = rows[0]
    assert row["pattern_basis_status"] == "machine_observed"
    assert row["missing_machine_evidence"] == "pattern_metric_not_supportive"
    assert row["evidence_support_status"] == "machine_observed_conflict"


def test_machine_evidence_support_preserves_unique_missing_evidence_order() -> None:
    explanations = [
        {
            **_explanation(
                "FAM001|S1",
                manual_label="pass",
                evidence_gap_class="machine_too_conservative_shape_or_pattern_unmodeled",
            ),
            "feature_family_id": "FAM001",
            "sample_id": "S1",
            "manual_reason_tags": "pattern_similar",
        }
    ]
    shadow_rows = build_shadow_label_rows(explanations)

    rows = machine_evidence_support.build_machine_evidence_support_rows(
        explanations=explanations,
        shadow_rows=shadow_rows,
        machine_matches={
            "FAM001|S1": (
                _machine_match(
                    source_role="rescued_cell",
                    sample_level=True,
                    row={
                        "status": "backfilled",
                        "apex_rt": "6.04",
                        "peak_start_rt": "5.90",
                        "peak_end_rt": "6.20",
                        "rt_delta_sec": "0.0",
                        "trace_quality": "owner_backfill",
                    },
                ),
            )
        },
        candidate_ms2_pattern_evidence={
            ("FAM001", "S1"): {
                "candidate_ms2_pattern_status": "conflict",
                "candidate_ms2_evidence_level": "sample_candidate_aligned",
            }
        },
        ms1_pattern_coherence_evidence={
            ("FAM001", "S1"): {
                "ms1_pattern_status": "conflict",
                "ms1_pattern_evidence_level": "sample_constellation",
                "apex_coherence_sec": "3.0",
                "boundary_overlap_score": "0.82",
                "shape_correlation_score": "0.91",
                "relative_pattern_stability_score": "0.77",
                "local_interference_score": "0.05",
                "constellation_peak_count": "3",
                "reference_peak_count": "3",
                "drift_compatible_status": "compatible",
                "reason": "unit_test_ms1_constellation",
                "diagnostic_only": "TRUE",
            }
        },
    )

    row = rows[0]
    assert row["missing_machine_evidence"] == "pattern_metric_not_supportive"
    assert row["evidence_support_status"] == "machine_observed_conflict"


def test_matrix_rt_drift_policy_can_close_drift_possible_blocker() -> None:
    explanations = [
        {
            **_explanation(
                "FAM001|S1",
                manual_label="suspect",
                evidence_gap_class="rt_drift_policy_gap",
            ),
            "feature_family_id": "FAM001",
            "sample_id": "S1",
            "manual_reason_tags": "rt_drift_possible;pattern_similar",
        }
    ]
    shadow_rows = build_shadow_label_rows(explanations)

    rows = machine_evidence_support.build_machine_evidence_support_rows(
        explanations=explanations,
        shadow_rows=shadow_rows,
        machine_matches={
            "FAM001|S1": (
                _machine_match(
                    source_role="rescued_cell",
                    sample_level=True,
                    row={
                        "status": "backfilled",
                        "apex_rt": "6.04",
                        "peak_start_rt": "5.90",
                        "peak_end_rt": "6.20",
                        "rt_delta_sec": "72.0",
                        "trace_quality": "owner_backfill",
                    },
                ),
            )
        },
        ms1_pattern_coherence_evidence={
            ("FAM001", "S1"): {
                "ms1_pattern_status": "supportive",
                "ms1_pattern_evidence_level": "sample_constellation",
                "apex_coherence_sec": "3.0",
                "boundary_overlap_score": "0.82",
                "shape_correlation_score": "0.91",
                "relative_pattern_stability_score": "0.77",
                "local_interference_score": "0.05",
                "constellation_peak_count": "3",
                "reference_peak_count": "3",
                "drift_compatible_status": "compatible",
                "reason": "unit_test_ms1_constellation",
                "diagnostic_only": "TRUE",
            }
        },
        matrix_rt_drift_policy_evidence={
            ("FAM001", "S1"): {
                "matrix_rt_drift_status": "drift_supported",
                "drift_evidence_level": "sample_istd_aligned",
                "raw_rt_delta_sec": "72.0",
                "drift_corrected_delta_sec": "4.0",
                "matrix_shift_sec": "68.0",
                "drift_reference_count": "4",
                "drift_reference_source": "sample_istd_trend",
                "drift_compatible_status": "compatible",
                "reason": "unit_test_matrix_drift_policy",
                "diagnostic_only": "TRUE",
            }
        },
    )

    row = rows[0]
    assert row["rt_basis_status"] == "machine_observed"
    assert "matrix_rt_drift_policy" not in row["missing_machine_evidence"]
    assert "matrix_rt_drift_status=drift_supported" in row["observed_machine_metrics"]
    assert row["evidence_support_status"] == "machine_observed_sufficient"


def test_rt_mode_conflict_fails_closed_against_manual_pass() -> None:
    explanations = [
        {
            **_explanation(
                "FAM001|S1",
                manual_label="pass",
                evidence_gap_class="machine_too_permissive_rt_pattern_conflict",
            ),
            "feature_family_id": "FAM001",
            "sample_id": "S1",
            "manual_reason_tags": "shape_complete;pattern_similar;rt_drift_possible",
        }
    ]
    shadow_rows = build_shadow_label_rows(explanations)

    rows = machine_evidence_support.build_machine_evidence_support_rows(
        explanations=explanations,
        shadow_rows=shadow_rows,
        machine_matches={
            "FAM001|S1": (
                _machine_match(
                    source_role="rescued_cell",
                    sample_level=True,
                    row={
                        "status": "backfilled",
                        "apex_rt": "6.02",
                        "peak_start_rt": "5.90",
                        "peak_end_rt": "6.20",
                        "rt_delta_sec": "72.0",
                        "trace_quality": "owner_backfill",
                    },
                ),
            )
        },
        rt_mode_evidence={
            ("FAM001", "S1"): {
                "rt_mode_status": "mode_conflict",
                "rt_mode_evidence_level": "irt_selected_apex_modes",
                "selected_mode_id": "mode_outlier",
                "selected_mode_role": "non_tag_outlier",
                "selected_mode_tag_status": "no_tag_observed",
                "family_mode_class": "tag_backed_core_with_outlier_modes",
                "family_mode_count": "2",
                "tag_bearing_mode_count": "1",
                "selected_mode_cell_count": "1",
                "selected_mode_sample_type_counts": "Tumor:1",
                "selected_mode_status_counts": "rescued:1",
                "raw_selected_rt": "6.02",
                "normalized_selected_rt": "6.01",
                "selected_mode_raw_rt_range_min": "0",
                "selected_mode_normalized_rt_range_min": "0",
                "family_raw_rt_range_min": "2.0",
                "family_normalized_rt_range_min": "1.9",
                "reason": "selected_mode_not_tag_bearing_core",
                "diagnostic_only": "TRUE",
            }
        },
    )

    row = rows[0]
    assert row["rt_basis_status"] == "machine_observed"
    assert "rt_mode_not_supportive" in row["missing_machine_evidence"]
    assert "rt_mode_status=mode_conflict" in row["observed_machine_metrics"]
    assert row["evidence_support_status"] == "machine_observed_conflict"


def test_peak_hypothesis_selection_fails_closed_against_manual_pass() -> None:
    explanations = [
        {
            **_explanation(
                "FAM011810|TumorBC2263_DNA",
                manual_label="pass",
                evidence_gap_class="machine_too_permissive_rt_pattern_conflict",
            ),
            "feature_family_id": "FAM011810",
            "sample_id": "TumorBC2263_DNA",
            "manual_reason_tags": "shape_complete;pattern_similar;rt_drift_possible",
        }
    ]
    shadow_rows = build_shadow_label_rows(explanations)

    rows = machine_evidence_support.build_machine_evidence_support_rows(
        explanations=explanations,
        shadow_rows=shadow_rows,
        machine_matches={
            "FAM011810|TumorBC2263_DNA": (
                _machine_match(
                    source_role="rescued_cell",
                    sample_level=True,
                    row={
                        "status": "backfilled",
                        "apex_rt": "6.02",
                        "peak_start_rt": "5.90",
                        "peak_end_rt": "6.20",
                        "rt_delta_sec": "72.0",
                        "trace_quality": "owner_backfill",
                    },
                ),
            )
        },
        peak_hypothesis_selection={
            ("FAM011810", "TumorBC2263_DNA"): {
                "peak_hypothesis_id": "FAM011810::irt_green_core",
                "peak_hypothesis_status": "cross_mode_rescue_blocked",
                "product_unit_scope": "sample_cell",
                "selected_mode_id": "irt_green_core",
                "selected_mode_role": "non_tag_outlier",
                "selected_mode_tag_status": "no_tag_observed",
                "family_mode_class": "tag_backed_core_with_outlier_modes",
                "family_mode_count": "3",
                "tag_bearing_mode_count": "1",
                "product_selection_action": "block_cross_mode_rescue",
                "product_selection_blocker": "cross_mode_rescue",
                "reason": "selected_cell_belongs_to_non_core_rt_mode",
                "diagnostic_only": "TRUE",
            }
        },
    )

    row = rows[0]
    assert "peak_hypothesis_not_supportive" in row["missing_machine_evidence"]
    assert (
        "peak_hypothesis_status=cross_mode_rescue_blocked"
        in row["observed_machine_metrics"]
    )
    assert (
        "product_selection_action=block_cross_mode_rescue"
        in row["observed_machine_metrics"]
    )
    assert row["evidence_support_status"] == "machine_observed_conflict"


def test_rt_close_policy_can_close_drift_possible_blocker() -> None:
    explanations = [
        {
            **_explanation(
                "FAM001|S1",
                manual_label="suspect",
                evidence_gap_class="rt_drift_policy_gap",
            ),
            "feature_family_id": "FAM001",
            "sample_id": "S1",
            "manual_reason_tags": "rt_drift_possible;pattern_similar",
        }
    ]
    shadow_rows = build_shadow_label_rows(explanations)

    rows = machine_evidence_support.build_machine_evidence_support_rows(
        explanations=explanations,
        shadow_rows=shadow_rows,
        machine_matches={
            "FAM001|S1": (
                _machine_match(
                    source_role="rescued_cell",
                    sample_level=True,
                    row={
                        "status": "backfilled",
                        "apex_rt": "7.96",
                        "peak_start_rt": "7.20",
                        "peak_end_rt": "8.80",
                        "rt_delta_sec": "51.2",
                        "trace_quality": "owner_backfill",
                    },
                ),
            )
        },
        ms1_pattern_coherence_evidence={
            ("FAM001", "S1"): {
                "ms1_pattern_status": "supportive",
                "ms1_pattern_evidence_level": "sample_constellation",
                "apex_coherence_sec": "3.0",
                "boundary_overlap_score": "0.82",
                "shape_correlation_score": "0.91",
                "relative_pattern_stability_score": "0.77",
                "local_interference_score": "0.05",
                "constellation_peak_count": "3",
                "reference_peak_count": "3",
                "drift_compatible_status": "compatible",
                "reason": "unit_test_ms1_constellation",
                "diagnostic_only": "TRUE",
            }
        },
        matrix_rt_drift_policy_evidence={
            ("FAM001", "S1"): {
                "matrix_rt_drift_status": "rt_close",
                "drift_evidence_level": "family_consensus_aligned",
                "raw_rt_delta_sec": "51.2",
                "drift_corrected_delta_sec": "51.2",
                "matrix_shift_sec": "0",
                "drift_reference_count": "1",
                "drift_reference_source": "alignment_cell_rt_delta",
                "drift_compatible_status": "compatible",
                "reason": "alignment_rt_within_preferred_window",
                "diagnostic_only": "TRUE",
            }
        },
    )

    row = rows[0]
    assert "matrix_rt_drift_policy" not in row["missing_machine_evidence"]
    assert "matrix_rt_drift_status=rt_close" in row["observed_machine_metrics"]
    assert row["evidence_support_status"] == "machine_observed_sufficient"


def test_machine_evidence_support_keeps_cid_nl_below_sample_ms2_pattern() -> None:
    explanations = [
        {
            **_explanation(
                "FAM001|S1",
                manual_label="fail",
                evidence_gap_class="machine_too_permissive_rt_pattern_conflict",
            ),
            "feature_family_id": "FAM001",
            "sample_id": "S1",
            "manual_reason_tags": "rt_too_far;pattern_mismatch;shape_normal",
        }
    ]
    shadow_rows = build_shadow_label_rows(explanations)

    rows = machine_evidence_support.build_machine_evidence_support_rows(
        explanations=explanations,
        shadow_rows=shadow_rows,
        machine_matches={
            "FAM001|S1": (
                _machine_match(
                    source_role="rescued_cell",
                    sample_level=True,
                    row={
                        "status": "backfilled",
                        "apex_rt": "18.4783",
                        "rt_delta_sec": "'-82.5403",
                        "trace_quality": "owner_backfill",
                    },
                ),
                _machine_match(
                    source_role="selected_peak",
                    sample_level=False,
                    row={
                        "feature_family_id": "FAM001",
                        "neutral_loss_tag": "loss_116",
                        "family_product_mz": "230.0",
                        "family_observed_neutral_loss_da": "116.0",
                    },
                ),
            )
        },
        cwt_shape_evidence={
            ("FAM001", "S1"): {
                "cwt_status": "OK",
                "cwt_shape_status": "cwt_near_expected",
                "cwt_apex_delta_sec": "0.01",
                "cwt_boundary_width_sec": "5.0",
                "cwt_prominence": "1000",
                "cwt_region_scan_count": "3",
                "cwt_quality_flags": "",
            }
        },
        candidate_ms2_pattern_evidence={
            ("FAM001", "S1"): {
                "candidate_ms2_pattern_status": "not_available",
                "candidate_ms2_evidence_level": "not_available",
            }
        },
    )

    row = rows[0]
    assert row["pattern_basis_status"] == "mixed"
    assert row["evidence_support_status"] == "machine_observed_partial"
    assert "formal_pattern_metric" in row["missing_machine_evidence"]
    assert "shape_metric_not_supportive" not in row["missing_machine_evidence"]
    assert "candidate_aligned_ms2_pattern" in row["missing_machine_evidence"]
    assert "rt_pattern_conflict_gate" not in row["missing_machine_evidence"]
    assert "family_ms2_pattern_context" not in row["missing_machine_evidence"]
    assert (
        "rt_preferred_window_status=outside_preferred_window"
        in row["observed_machine_metrics"]
    )
    assert "neutral_loss_tag=loss_116" in row["observed_machine_metrics"]
    assert (
        "cid_nl_pattern_context=family_level_present" in row["observed_machine_metrics"]
    )


def test_candidate_ms2_pattern_sidecar_can_close_pattern_mismatch() -> None:
    explanations = [
        {
            **_explanation(
                "FAM001|S1",
                manual_label="fail",
                evidence_gap_class="machine_too_permissive_rt_pattern_conflict",
            ),
            "feature_family_id": "FAM001",
            "sample_id": "S1",
            "manual_reason_tags": "rt_too_far;pattern_mismatch;shape_normal",
        }
    ]
    shadow_rows = build_shadow_label_rows(explanations)

    rows = machine_evidence_support.build_machine_evidence_support_rows(
        explanations=explanations,
        shadow_rows=shadow_rows,
        machine_matches={
            "FAM001|S1": (
                _machine_match(
                    source_role="rescued_cell",
                    sample_level=True,
                    row={
                        "status": "backfilled",
                        "apex_rt": "18.4783",
                        "rt_delta_sec": "'-82.5403",
                        "trace_quality": "owner_backfill",
                    },
                ),
            )
        },
        cwt_shape_evidence={
            ("FAM001", "S1"): {
                "cwt_status": "OK",
                "cwt_shape_status": "cwt_near_expected",
                "cwt_apex_delta_sec": "0.01",
                "cwt_boundary_width_sec": "5.0",
                "cwt_prominence": "1000",
                "cwt_region_scan_count": "3",
                "cwt_quality_flags": "",
            }
        },
        candidate_ms2_pattern_evidence={
            ("FAM001", "S1"): {
                "candidate_ms2_pattern_status": "conflict",
                "candidate_ms2_evidence_level": "sample_candidate_aligned",
                "candidate_ms2_similarity_score": "0.12",
                "matched_product_count": "0",
                "matched_neutral_loss_count": "0",
                "apex_ms2_delta_sec": "4.0",
                "ms2_alignment_source": "unit_test_sidecar",
                "source_neutral_loss_mass_error_ppm": "25",
                "nl_ppm_warn": "20",
                "nl_ppm_max": "50",
            }
        },
    )

    row = rows[0]
    assert row["pattern_basis_status"] == "machine_observed"
    assert row["missing_machine_evidence"] == ""
    assert row["evidence_support_status"] == "machine_observed_sufficient"
    assert "candidate_ms2_pattern_status=conflict" in row["observed_machine_metrics"]
    assert (
        "candidate_ms2_evidence_level=sample_candidate_aligned"
        in row["observed_machine_metrics"]
    )
    assert (
        "candidate_ms2_source_neutral_loss_mass_error_ppm=25"
        in row["observed_machine_metrics"]
    )
    assert "candidate_ms2_nl_ppm_warn=20" in row["observed_machine_metrics"]
    assert "candidate_ms2_nl_ppm_max=50" in row["observed_machine_metrics"]


def test_qc_ms1_reference_can_close_pattern_mismatch() -> None:
    explanations = [
        {
            **_explanation(
                "FAM001|S1",
                manual_label="fail",
                evidence_gap_class="machine_too_permissive_rt_pattern_conflict",
            ),
            "feature_family_id": "FAM001",
            "sample_id": "S1",
            "manual_reason_tags": "rt_too_far;pattern_mismatch",
        }
    ]
    shadow_rows = build_shadow_label_rows(explanations)

    rows = machine_evidence_support.build_machine_evidence_support_rows(
        explanations=explanations,
        shadow_rows=shadow_rows,
        machine_matches={
            "FAM001|S1": (
                _machine_match(
                    source_role="rescued_cell",
                    sample_level=True,
                    row={"status": "backfilled", "rt_delta_sec": "'-82.5403"},
                ),
            )
        },
        qc_ms1_pattern_reference_evidence={
            ("FAM001", "S1"): {
                "qc_reference_status": "conflict",
                "qc_reference_evidence_level": "qc_consensus_with_local_qc_overlay",
                "qc_reference_policy": "qc_consensus_with_local_conflict",
                "local_qc_reference_status": "conflict",
                "qc_consensus_status": "conflict",
                "qc_reference_conflict_status": "none",
                "nearest_qc_sample_stem": "Breast_Cancer_Tissue_pooled_QC5",
                "nearest_qc_injection_order_delta": "31",
                "target_qc_apex_abs_delta_sec": "78.3054",
                "target_qc_shape_similarity": "0.259605",
                "target_local_window_to_global_max_ratio": "1",
                "nearest_qc_local_window_to_global_max_ratio": "1",
            }
        },
    )

    row = rows[0]
    assert row["pattern_basis_status"] == "machine_observed"
    assert row["missing_machine_evidence"] == ""
    assert row["evidence_support_status"] == "machine_observed_sufficient"
    assert "qc_ms1_reference_status=conflict" in row["observed_machine_metrics"]
    assert "candidate_aligned_ms2_pattern" not in row["missing_machine_evidence"]


def test_candidate_ms2_pattern_sidecar_conflict_stays_fail_closed() -> None:
    explanations = [
        {
            **_explanation(
                "FAM001|S1",
                manual_label="fail",
                evidence_gap_class="machine_too_permissive_rt_pattern_conflict",
            ),
            "feature_family_id": "FAM001",
            "sample_id": "S1",
            "manual_reason_tags": "pattern_mismatch",
        }
    ]
    shadow_rows = build_shadow_label_rows(explanations)

    rows = machine_evidence_support.build_machine_evidence_support_rows(
        explanations=explanations,
        shadow_rows=shadow_rows,
        machine_matches={
            "FAM001|S1": (
                _machine_match(
                    source_role="rescued_cell",
                    sample_level=True,
                    row={"status": "backfilled"},
                ),
            )
        },
        candidate_ms2_pattern_evidence={
            ("FAM001", "S1"): {
                "candidate_ms2_pattern_status": "supportive",
                "candidate_ms2_evidence_level": "sample_candidate_aligned",
            }
        },
    )

    row = rows[0]
    assert row["pattern_basis_status"] == "machine_observed"
    assert row["missing_machine_evidence"] == "pattern_metric_not_supportive"
    assert row["evidence_support_status"] == "machine_observed_conflict"


def test_shadow_writer_outputs_readiness_report_and_summary(tmp_path: Path) -> None:
    prior_outputs = _write_prior_outputs(tmp_path)
    shadow_rows = build_shadow_label_rows(
        [
            _explanation(
                "row-pass",
                manual_label="pass",
                evidence_gap_class="machine_too_conservative_shape_or_pattern_unmodeled",
            )
        ]
    )
    summary_rows = build_shadow_alignment_summary(shadow_rows)
    readiness = build_shadow_readiness(
        run_facts={
            **_slice1_run_facts(),
            "blast_radius_assessed": "not_assessed",
            "max_overfit_risk": "unassessed",
        },
        shadow_rows=shadow_rows,
    )

    outputs = write_shadow_outputs(
        output_dir=tmp_path,
        prior_outputs=prior_outputs,
        shadow_rows=shadow_rows,
        summary_rows=summary_rows,
        readiness_row=readiness,
        machine_evidence_support_rows=[
            _support_row(
                "row-pass",
                evidence_support_status="machine_proxy_only",
                missing_machine_evidence="formal_shape_metric",
            )
        ],
    )

    assert outputs["shadow_labels"].name == "shared_peak_identity_shadow_labels.tsv"
    assert outputs["shadow_alignment_summary"].name == (
        "shared_peak_identity_shadow_alignment_summary.tsv"
    )
    assert (
        outputs["shadow_readiness"].name == "shared_peak_identity_shadow_readiness.tsv"
    )
    assert outputs["machine_evidence_support"].name == (
        "shared_peak_identity_machine_evidence_support.tsv"
    )
    report = (tmp_path / "shared_peak_identity_shadow_report.md").read_text(
        encoding="utf-8"
    )
    assert "exploratory_only" in report
    assert "production_ready" not in report
    assert "formal_shape_metric" in report
    assert "Machine Evidence Provenance" in report


def _explanation(
    oracle_row_id: str,
    *,
    manual_label: str,
    evidence_gap_class: str,
) -> dict[str, str]:
    return {
        "oracle_row_id": oracle_row_id,
        "feature_family_id": oracle_row_id.split("-", 1)[0],
        "sample_id": "sample",
        "manual_label": manual_label,
        "manual_confidence": "high",
        "machine_current_label": "backfilled",
        "machine_match_status": "single_match",
        "evidence_gap_class": evidence_gap_class,
        "manual_reason_tags": "",
        "manual_scope": "reviewed_cell",
    }


def _support_row(
    oracle_row_id: str,
    *,
    evidence_support_status: str,
    missing_machine_evidence: str = "",
) -> dict[str, str]:
    return {
        "oracle_row_id": oracle_row_id,
        "evidence_support_status": evidence_support_status,
        "missing_machine_evidence": missing_machine_evidence,
        "shape_basis_status": "machine_observed",
        "pattern_basis_status": "machine_observed",
        "opportunity_basis_status": "machine_observed",
        "scope_basis_status": "not_applicable",
    }


def _machine_match(
    *,
    source_role: str,
    sample_level: bool,
    row: dict[str, str],
) -> MachineMatch:
    return MachineMatch(
        evidence_source="alignment_cells" if sample_level else "alignment_review",
        source_role=source_role,
        source_artifact=Path("artifact.tsv"),
        source_artifact_sha256="A" * 64,
        source_row_id="artifact.tsv:1",
        row=row,
        sample_level=sample_level,
    )


def _slice1_run_facts() -> dict[str, str]:
    return {
        "run_facts_schema_version": "shared_peak_identity_run_facts",
        "slice": "slice1",
        "seed_rows_total": "2",
        "seed_rows_explained": "2",
        "seed_rows_unexplained": "0",
        "seed_rows_inconclusive": "0",
        "vocabulary_special_casing_detected": "FALSE",
        "blast_radius_assessed": "present_current",
        "blast_radius_stale_artifact_count": "0",
        "max_overfit_risk": "low",
        "durable_oracle_path": "oracle.tsv",
        "durable_oracle_sha256": "A" * 64,
    }


def _write_prior_outputs(tmp_path: Path) -> dict[str, Path]:
    paths = {
        "oracle": tmp_path / "shared_peak_identity_manual_oracle.tsv",
        "evidence_vectors": tmp_path / "shared_peak_identity_evidence_vectors.tsv",
        "explanations": tmp_path / "shared_peak_identity_explanations.tsv",
        "run_facts": tmp_path / "shared_peak_identity_run_facts.tsv",
        "report": tmp_path / "shared_peak_identity_explanation_report.md",
    }
    for path in paths.values():
        path.write_text("placeholder\n", encoding="utf-8")
    return paths
