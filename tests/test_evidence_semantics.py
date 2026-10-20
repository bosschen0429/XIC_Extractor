from __future__ import annotations

from xic_extractor.peak_detection.evidence_semantics import (
    EvidenceSignalSet,
    classify_evidence_consistency,
    decision_semantics_from_signal_set,
)


def test_consistency_classifier_splits_nl_dropout_from_conflict() -> None:
    plausible = classify_evidence_consistency(
        EvidenceSignalSet(
            support_labels=("local_sn_strong", "shape_clean", "trace_clean"),
            concern_labels=("nl_fail",),
            ms2_present=True,
            nl_match=False,
            raw_score=35,
        )
    )
    hard = classify_evidence_consistency(
        EvidenceSignalSet(
            support_labels=("local_sn_strong", "trace_clean"),
            concern_labels=("nl_fail", "shape_poor"),
            ms2_present=True,
            nl_match=False,
            raw_score=35,
        )
    )

    assert plausible == (
        "ms1_coherent",
        "plausible_nl_dropout",
    )
    assert hard == (
        "hard_local_quality_conflict",
        "hard_nl_conflict",
    )


def test_consistency_classifier_allows_cwt_as_shape_context_but_not_chemistry() -> None:
    labels = classify_evidence_consistency(
        EvidenceSignalSet(
            support_labels=("local_sn_strong", "trace_clean"),
            concern_labels=("nl_fail",),
            proposal_sources=("centwave_cwt", "local_minimum"),
            ms2_present=True,
            nl_match=False,
            raw_score=20,
        )
    )

    assert labels == (
        "ms1_coherent",
        "plausible_nl_dropout",
    )


def test_consistency_allows_chrom_segment_as_shape_context_not_chemistry(
) -> None:
    labels = classify_evidence_consistency(
        EvidenceSignalSet(
            support_labels=("local_sn_strong", "trace_clean"),
            concern_labels=("nl_fail",),
            proposal_sources=("chrom_peak_segment", "local_minimum"),
            ms2_present=True,
            nl_match=False,
            raw_score=20,
        )
    )

    assert labels == (
        "ms1_coherent",
        "plausible_nl_dropout",
    )


def test_consistency_soft_trace_flags_are_context_warning_with_cwt(
) -> None:
    labels = classify_evidence_consistency(
        EvidenceSignalSet(
            support_labels=("local_sn_strong", "cwt_same_apex_support"),
            concern_labels=("nl_fail", "low_trace_continuity"),
            proposal_sources=("centwave_cwt", "local_minimum"),
            quality_flags=("low_trace_continuity",),
            ms2_present=True,
            nl_match=False,
            raw_score=35,
        )
    )

    assert labels == (
        "ms1_coherent",
        "plausible_nl_dropout",
    )


def test_consistency_classifier_keeps_no_ms2_distinct_from_nl_dropout() -> None:
    labels = classify_evidence_consistency(
        EvidenceSignalSet(
            support_labels=("local_sn_strong", "shape_clean", "trace_clean"),
            concern_labels=("nl_fail", "no_ms2"),
            ms2_present=False,
            nl_match=False,
            raw_score=35,
        )
    )

    assert labels == (
        "ms1_coherent",
        "missing_ms2",
        "hard_nl_conflict",
    )


def test_decision_semantics_maps_coherent_multievidence_to_accepted() -> None:
    decision = decision_semantics_from_signal_set(
        EvidenceSignalSet(
            support_labels=(
                "strict_nl_ok",
                "rt_prior_close",
                "local_sn_strong",
                "trace_clean",
                "cwt_same_apex_support",
            ),
            proposal_sources=("legacy_savgol", "centwave_cwt"),
            ms2_present=True,
            nl_match=True,
            raw_score=95,
            confidence="HIGH",
            reason="decision: accepted",
        )
    )

    assert decision.decision_class == "accepted"
    assert decision.support_reasons == (
        "ms1_coherent",
        "candidate_aligned_ms2_nl",
        "role_aware_rt_support",
        "cwt_boundary_morphology_context",
    )
    assert decision.conflict_reasons == ()
    assert decision.not_counted_reasons == ()


def test_decision_semantics_projects_paired_area_ratio_support() -> None:
    decision = decision_semantics_from_signal_set(
        EvidenceSignalSet(
            support_labels=(
                "paired_area_ratio_plausible",
                "local_sn_strong",
                "shape_clean",
                "trace_clean",
            ),
            concern_labels=("nl_fail",),
            ms2_present=True,
            nl_match=False,
            raw_score=95,
            confidence="VERY_LOW",
            cap_labels=("nl_fail_cap",),
            reason="decision: review only, not counted",
        )
    )

    assert "ms1_coherent" in decision.support_reasons
    assert "paired_area_ratio_support" in decision.support_reasons
    assert "role_aware_rt_support" not in decision.support_reasons
    assert "plausible_nl_dropout_review" in decision.review_reasons
    assert decision.decision_class == "not_counted"
    assert decision.not_counted_reasons == ("legacy_review_only_projection",)


def test_decision_semantics_counts_paired_ratio_supported_nl_dropout_as_review(
) -> None:
    decision = decision_semantics_from_signal_set(
        EvidenceSignalSet(
            support_labels=(
                "paired_area_ratio_plausible",
                "paired_istd_rt_close",
                "local_sn_strong",
                "shape_clean",
                "trace_clean",
            ),
            concern_labels=("nl_fail",),
            ms2_present=True,
            nl_match=False,
            raw_score=95,
            confidence="VERY_LOW",
            cap_labels=("nl_fail_cap",),
            reason="decision: review only, not counted",
        )
    )

    assert decision.decision_class == "review"
    assert "ms1_coherent" in decision.support_reasons
    assert "role_aware_rt_support" in decision.support_reasons
    assert "paired_area_ratio_support" in decision.support_reasons
    assert "plausible_nl_dropout_review" in decision.review_reasons
    assert decision.not_counted_reasons == ()


def test_decision_semantics_maps_no_ms2_cap_to_not_counted_without_exclusion() -> None:
    decision = decision_semantics_from_signal_set(
        EvidenceSignalSet(
            support_labels=("local_sn_strong", "shape_clean", "trace_clean"),
            concern_labels=("no_ms2",),
            ms2_present=False,
            nl_match=False,
            raw_score=55,
            confidence="LOW",
            cap_labels=("no_ms2_cap",),
            reason="decision: accepted",
            count_no_ms2_as_detected=False,
        )
    )

    assert decision.decision_class == "not_counted"
    assert decision.review_reasons == ("missing_ms2_not_observed",)
    assert decision.not_counted_reasons == (
        "missing_ms2_policy_not_counted",
        "missing_ms2_compatibility_cap",
    )
    assert decision.exclusion_reasons == ()


def test_decision_semantics_honors_count_no_ms2_as_detected_policy() -> None:
    decision = decision_semantics_from_signal_set(
        EvidenceSignalSet(
            support_labels=("local_sn_strong", "shape_clean", "trace_clean"),
            concern_labels=("no_ms2",),
            ms2_present=False,
            nl_match=False,
            raw_score=55,
            confidence="LOW",
            cap_labels=("no_ms2_cap",),
            reason="decision: review only, not counted",
            count_no_ms2_as_detected=True,
        )
    )

    assert decision.decision_class == "review"
    assert decision.review_reasons == ("missing_ms2_not_observed",)
    assert decision.not_counted_reasons == ()
    assert decision.exclusion_reasons == ()


def test_decision_semantics_does_not_let_medium_caps_force_not_counted() -> None:
    decision = decision_semantics_from_signal_set(
        EvidenceSignalSet(
            support_labels=("strict_nl_ok", "local_sn_strong", "shape_clean"),
            concern_labels=("hard_quality_flag",),
            ms2_present=True,
            nl_match=True,
            raw_score=74,
            confidence="MEDIUM",
            cap_labels=("hard_quality_flag_cap",),
            reason="decision: accepted; cap: MEDIUM due to hard quality flag",
        )
    )

    assert decision.decision_class == "review"
    assert "trace_morphology_conflict" in decision.conflict_reasons
    assert decision.review_reasons == ("trace_morphology_review",)
    assert decision.not_counted_reasons == ()


def test_decision_semantics_keeps_cwt_as_context_not_standalone_authority() -> None:
    decision = decision_semantics_from_signal_set(
        EvidenceSignalSet(
            proposal_sources=("centwave_cwt",),
        )
    )

    assert decision.decision_class == "review"
    assert decision.support_reasons == ("cwt_boundary_morphology_context",)
    assert decision.review_reasons == ("cwt_requires_correlated_evidence",)


def test_decision_chrom_segment_is_context_not_standalone_authority(
) -> None:
    decision = decision_semantics_from_signal_set(
        EvidenceSignalSet(
            proposal_sources=("chrom_peak_segment",),
        )
    )

    assert decision.decision_class == "review"
    assert decision.support_reasons == ("chrom_peak_segment_context",)
    assert decision.review_reasons == (
        "chrom_peak_segment_requires_correlated_evidence",
    )


def test_decision_semantics_keeps_targeted_rt_conflict_as_review_not_exclusion(
) -> None:
    decision = decision_semantics_from_signal_set(
        EvidenceSignalSet(
            support_labels=("strict_nl_ok", "local_sn_strong", "shape_clean"),
            concern_labels=("rt_prior_far",),
            ms2_present=True,
            nl_match=True,
            raw_score=82,
            confidence="VERY_LOW",
            cap_labels=("rt_window_cap",),
            reason="decision: review only, not counted",
        )
    )

    assert decision.decision_class == "not_counted"
    assert "targeted_rt_conflict" in decision.conflict_reasons
    assert "targeted_rt_review" in decision.review_reasons
    assert decision.exclusion_reasons == ()


def test_decision_semantics_anchor_mismatch_cap_without_strict_nl_is_not_counted(
) -> None:
    decision = decision_semantics_from_signal_set(
        EvidenceSignalSet(
            support_labels=("local_sn_strong", "shape_clean", "trace_clean"),
            ms2_present=True,
            nl_match=True,
            raw_score=70,
            confidence="VERY_LOW",
            cap_labels=("anchor_mismatch_cap",),
            reason="decision: review only, not counted",
        )
    )

    assert decision.decision_class == "not_counted"
    assert decision.conflict_reasons == ("anchor_conflict",)
    assert decision.review_reasons == ("paired_istd_rt_review",)
    assert decision.not_counted_reasons == (
        "paired_istd_rt_mismatch_policy",
        "paired_istd_rt_mismatch_cap",
        "anchor_mismatch_compatibility_cap",
    )
    assert decision.exclusion_reasons == ()


def test_decision_anchor_mismatch_cap_with_strict_nl_skips_anchor_policy(
) -> None:
    decision = decision_semantics_from_signal_set(
        EvidenceSignalSet(
            support_labels=(
                "strict_nl_ok",
                "local_sn_strong",
                "shape_clean",
                "trace_clean",
            ),
            ms2_present=True,
            nl_match=True,
            raw_score=70,
            confidence="VERY_LOW",
            cap_labels=("anchor_mismatch_cap",),
            reason="decision: review only, not counted",
        )
    )

    # Strict-NL support sets strict_nl_supported, so the paired-ISTD anchor
    # not-counted policy is skipped; the row only stays not_counted via the
    # legacy VERY_LOW projection.
    assert decision.decision_class == "not_counted"
    assert "candidate_aligned_ms2_nl" in decision.support_reasons
    assert decision.conflict_reasons == ("anchor_conflict",)
    assert "paired_istd_rt_mismatch_policy" not in decision.not_counted_reasons
    assert decision.not_counted_reasons == (
        "legacy_review_only_projection",
        "anchor_mismatch_compatibility_cap",
    )


def test_decision_semantics_unions_zero_area_and_hard_quality_caps() -> None:
    decision = decision_semantics_from_signal_set(
        EvidenceSignalSet(
            support_labels=("strict_nl_ok", "local_sn_strong"),
            concern_labels=("hard_quality_flag",),
            ms2_present=True,
            nl_match=True,
            raw_score=10,
            confidence="VERY_LOW",
            cap_labels=("zero_area_cap", "hard_quality_flag_cap"),
            reason="decision: review only, not counted",
        )
    )

    assert decision.decision_class == "not_counted"
    assert "hard_local_quality_conflict" in decision.conflict_reasons
    assert decision.not_counted_reasons == (
        "legacy_review_only_projection",
        "zero_area_compatibility_cap",
        "hard_quality_flag_compatibility_cap",
    )


def test_decision_semantics_unions_anchor_mismatch_and_missing_ms2_policies() -> None:
    decision = decision_semantics_from_signal_set(
        EvidenceSignalSet(
            support_labels=("local_sn_strong", "shape_clean", "trace_clean"),
            concern_labels=("no_ms2",),
            ms2_present=False,
            nl_match=False,
            raw_score=40,
            confidence="VERY_LOW",
            cap_labels=("anchor_mismatch_cap", "no_ms2_cap"),
            reason="decision: review only, not counted",
            count_no_ms2_as_detected=False,
        )
    )

    assert decision.decision_class == "not_counted"
    assert decision.not_counted_reasons == (
        "missing_ms2_policy_not_counted",
        "missing_ms2_compatibility_cap",
        "paired_istd_rt_mismatch_policy",
        "paired_istd_rt_mismatch_cap",
        "anchor_mismatch_compatibility_cap",
    )
    assert "missing_ms2_not_observed" in decision.review_reasons
    assert "paired_istd_rt_review" in decision.review_reasons
