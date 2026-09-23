from __future__ import annotations

from xic_extractor.alignment.shared_peak_identity_explanation.blast_radius import (
    BlastRadiusClassProfile,
    build_class_profiles,
)


def test_build_class_profiles_separates_seed_and_context_rows() -> None:
    explanations = [
        _explanation(
            oracle_row_id="FAM001|S1",
            family="FAM001",
            sample="S1",
            manual_label="pass",
            evidence_gap_class="machine_too_conservative_low_opportunity",
            machine_current_label="absent",
            machine_match_status="single_match",
            manual_reason_tags="low_intensity;dda_stochastic_missing",
        ),
        _explanation(
            oracle_row_id="FAM001|__family_context__",
            family="FAM001",
            sample="__family_context__",
            manual_label="not_applicable",
            evidence_gap_class="delta_mass_related_context_only",
            machine_current_label="not_applicable",
            machine_match_status="not_applicable",
            manual_reason_tags="delta_mass_related",
        ),
        _explanation(
            oracle_row_id="FAM002|__scope_rule__",
            family="FAM002",
            sample="__scope_rule__",
            manual_label="not_applicable",
            evidence_gap_class="machine_too_permissive_scope_rule_conflict",
            machine_current_label="not_applicable",
            machine_match_status="not_applicable",
            manual_reason_tags="scope_derived_unmentioned_fail",
        ),
    ]
    evidence_vectors = [
        _evidence(
            oracle_row_id="FAM001|S1",
            family="FAM001",
            sample="S1",
            source_role="rescued_cell",
            machine_current_label="absent",
            machine_blockers="no_local_MS1_owner",
            intensity_status="low_but_visible",
            dda_opportunity_status="low_intensity_stochastic_not_observed",
        )
    ]

    profiles = build_class_profiles(explanations, evidence_vectors)

    profile = profiles["machine_too_conservative_low_opportunity"]
    assert isinstance(profile, BlastRadiusClassProfile)
    assert profile.seed_oracle_row_ids == ("FAM001|S1",)
    assert profile.context_oracle_row_ids == ()
    assert profile.seed_feature_family_ids == ("FAM001",)
    assert profile.seed_sample_keys == ("FAM001|S1",)
    assert "absent_machine_cell" in profile.machine_prerequisites
    assert "low_opportunity_machine_context" in profile.machine_prerequisites
    assert "manual_label:pass" in profile.manual_prerequisites
    assert "manual_tag:low_intensity" in profile.manual_prerequisites

    context_profile = profiles["delta_mass_related_context_only"]
    assert context_profile.seed_oracle_row_ids == ()
    assert context_profile.context_oracle_row_ids == ("FAM001|__family_context__",)
    assert context_profile.seed_feature_family_ids == ()
    assert context_profile.seed_sample_keys == ()
    assert context_profile.machine_prerequisites == ("context_only",)

    scope_rule_profile = profiles["machine_too_permissive_scope_rule_conflict"]
    assert scope_rule_profile.seed_oracle_row_ids == ()
    assert scope_rule_profile.context_oracle_row_ids == ("FAM002|__scope_rule__",)
    assert "manual_tag:scope_derived_unmentioned_fail" in (
        scope_rule_profile.manual_prerequisites
    )


def test_build_class_profiles_keeps_machine_and_manual_prerequisites_separate() -> None:
    explanations = [
        _explanation(
            oracle_row_id="FAM003|S2",
            family="FAM003",
            sample="S2",
            manual_label="fail",
            evidence_gap_class="machine_too_permissive_rt_pattern_conflict",
            machine_current_label="backfilled",
            machine_match_status="ambiguous_multiple_matches",
            manual_reason_tags="rt_too_far;pattern_mismatch;shape_normal",
        )
    ]
    evidence_vectors = [
        _evidence(
            oracle_row_id="FAM003|S2",
            family="FAM003",
            sample="S2",
            source_role="rescued_cell",
            machine_current_label="backfilled",
            rt_context_status="conflicting",
            pattern_conflict_status="rt_pattern_conflict",
        )
    ]

    profile = build_class_profiles(explanations, evidence_vectors)[
        "machine_too_permissive_rt_pattern_conflict"
    ]

    assert "positive_machine_cell" in profile.machine_prerequisites
    assert "ambiguous_machine_match" in profile.machine_prerequisites
    assert "rt_pattern_conflict" in profile.machine_prerequisites
    assert "manual_label:fail" in profile.manual_prerequisites
    assert "manual_tag:pattern_mismatch" in profile.manual_prerequisites
    assert all(
        not prerequisite.startswith("manual_")
        for prerequisite in profile.machine_prerequisites
    )


def _explanation(
    *,
    oracle_row_id: str,
    family: str,
    sample: str,
    manual_label: str,
    evidence_gap_class: str,
    machine_current_label: str,
    machine_match_status: str,
    manual_reason_tags: str,
) -> dict[str, str]:
    return {
        "oracle_row_id": oracle_row_id,
        "feature_family_id": family,
        "sample_id": sample,
        "manual_label": manual_label,
        "manual_label_source": "direct_eic_ms2_review",
        "manual_scope": "reviewed_cell",
        "manual_reason_tags": manual_reason_tags,
        "machine_current_label": machine_current_label,
        "machine_match_status": machine_match_status,
        "machine_blockers": "",
        "evidence_gap_class": evidence_gap_class,
    }


def _evidence(
    *,
    oracle_row_id: str,
    family: str,
    sample: str,
    source_role: str,
    machine_current_label: str,
    machine_blockers: str = "",
    rt_context_status: str = "supportive",
    pattern_conflict_status: str = "none",
    intensity_status: str = "sufficient",
    dda_opportunity_status: str = "observed",
) -> dict[str, str]:
    return {
        "oracle_row_id": oracle_row_id,
        "feature_family_id": family,
        "sample_id": sample,
        "source_role": source_role,
        "machine_current_label": machine_current_label,
        "machine_blockers": machine_blockers,
        "rt_context_status": rt_context_status,
        "pattern_conflict_status": pattern_conflict_status,
        "intensity_status": intensity_status,
        "dda_opportunity_status": dda_opportunity_status,
    }
