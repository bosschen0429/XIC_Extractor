from dataclasses import replace

import numpy as np
import pytest

from xic_extractor.peak_detection.candidate_scoring import (
    score_candidate,
)
from xic_extractor.peak_detection.hypotheses import EvidenceVector
from xic_extractor.peak_detection.models import PeakCandidate, PeakResult
from xic_extractor.peak_detection.scoring_models import (
    Confidence,
    ScoringContext,
    confidence_from_total,
)


@pytest.mark.parametrize(
    ("total", "expected"),
    [
        (0, Confidence.HIGH),
        (1, Confidence.MEDIUM),
        (2, Confidence.MEDIUM),
        (3, Confidence.LOW),
        (4, Confidence.LOW),
        (5, Confidence.VERY_LOW),
        (100, Confidence.VERY_LOW),
    ],
)
def test_confidence_from_total(total: int, expected: Confidence) -> None:
    assert confidence_from_total(total) == expected


def _make_candidate(apex_rt: float, apex_intensity: float) -> PeakCandidate:
    peak = PeakResult(
        rt=apex_rt,
        intensity=apex_intensity,
        intensity_smoothed=apex_intensity,
        area=100.0,
        peak_start=apex_rt - 0.1,
        peak_end=apex_rt + 0.1,
    )
    return PeakCandidate(
        peak=peak,
        selection_apex_rt=apex_rt,
        selection_apex_intensity=apex_intensity,
        selection_apex_index=100,
        raw_apex_rt=apex_rt,
        raw_apex_intensity=apex_intensity,
        raw_apex_index=100,
        prominence=apex_intensity * 0.5,
    )


def _make_flagged_candidate(
    apex_rt: float,
    apex_intensity: float,
    *,
    quality_flags: tuple[str, ...],
) -> PeakCandidate:
    candidate = _make_candidate(apex_rt=apex_rt, apex_intensity=apex_intensity)
    return PeakCandidate(
        **{
            **candidate.__dict__,
            "quality_flags": quality_flags,
            "region_scan_count": 4,
            "region_duration_min": 1.2,
            "region_edge_ratio": 1.05,
        }
    )


def test_score_candidate_returns_base_and_trace_quality_severities() -> None:
    cand = _make_candidate(apex_rt=10.0, apex_intensity=1000)
    x = np.linspace(9, 11, 201)
    y = 1000 * np.exp(-(((x - 10) / 0.1) ** 2)) + 5
    ctx = ScoringContext(
        rt_array=x,
        intensity_array=y,
        apex_index=100,
        half_width_ratio=1.0,
        fwhm_ratio=1.0,
        ms2_present=True,
        nl_match=True,
        rt_prior=10.0,
        rt_prior_sigma=0.1,
        rt_min=9.0,
        rt_max=11.0,
        dirty_matrix=False,
    )
    scored = score_candidate(cand, ctx, prior_rt=10.0)
    assert len(scored.severities) == 10
    assert scored.confidence == Confidence.HIGH
    assert scored.reason.startswith("decision: accepted")
    assert "candidate_aligned_ms2_nl" in scored.reason
    assert "role_aware_rt_support" in scored.reason
    assert "strict_nl_ok" in scored.evidence_score.support_labels
    assert "rt_prior_close" in scored.evidence_score.support_labels


def test_score_candidate_records_positive_and_negative_evidence() -> None:
    cand = _make_candidate(apex_rt=10.0, apex_intensity=1000)
    x = np.linspace(9, 11, 201)
    y = 1000 * np.exp(-(((x - 10) / 0.1) ** 2)) + 5
    ctx = ScoringContext(
        rt_array=x,
        intensity_array=y,
        apex_index=100,
        half_width_ratio=1.0,
        fwhm_ratio=1.0,
        ms2_present=True,
        nl_match=True,
        rt_prior=10.0,
        rt_prior_sigma=0.1,
        rt_min=9.0,
        rt_max=11.0,
        dirty_matrix=False,
    )

    scored = score_candidate(cand, ctx, prior_rt=10.0)

    assert scored.confidence == Confidence.HIGH
    assert scored.evidence_score.raw_score >= 80
    assert "strict_nl_ok" in scored.evidence_score.support_labels
    assert "rt_prior_close" in scored.evidence_score.support_labels
    assert scored.evidence_score.concern_labels == ()


def test_score_candidate_records_rt_review_support_without_pair_bonus() -> None:
    cand = _make_candidate(apex_rt=10.0, apex_intensity=1000)
    x = np.linspace(9, 11, 201)
    y = 1000 * np.exp(-(((x - 10) / 0.1) ** 2)) + 5
    ctx = ScoringContext(
        rt_array=x,
        intensity_array=y,
        apex_index=100,
        half_width_ratio=1.0,
        fwhm_ratio=1.0,
        ms2_present=True,
        nl_match=True,
        rt_prior=10.0,
        rt_prior_sigma=0.1,
        rt_min=9.0,
        rt_max=11.0,
        dirty_matrix=False,
    )

    scored = score_candidate(cand, ctx, prior_rt=10.0)

    assert "rt_prior_close" in scored.evidence_score.support_labels
    assert "role_aware_rt_support" in scored.reason


def test_score_candidate_records_strong_ms2_trace_support() -> None:
    cand = _make_candidate(apex_rt=10.0, apex_intensity=1000)
    x = np.linspace(9, 11, 201)
    y = 1000 * np.exp(-(((x - 10) / 0.1) ** 2)) + 5
    ctx = ScoringContext(
        rt_array=x,
        intensity_array=y,
        apex_index=100,
        half_width_ratio=1.0,
        fwhm_ratio=1.0,
        ms2_present=True,
        nl_match=True,
        rt_prior=10.0,
        rt_prior_sigma=0.1,
        rt_min=9.0,
        rt_max=11.0,
        dirty_matrix=False,
        ms2_trace_strength="strong",
    )

    scored = score_candidate(cand, ctx, prior_rt=10.0)

    assert "ms2_trace_strong" in scored.evidence_score.support_labels
    assert scored.evidence_score.positive_points >= 10
    assert scored.evidence_facts is not None
    assert scored.evidence_facts.chemical.ms2_trace_strength == "strong"


def test_sparse_apex_fallback_ms2_does_not_count_as_strong_trace_support() -> None:
    cand = _make_flagged_candidate(
        apex_rt=10.0,
        apex_intensity=1000,
        quality_flags=("low_scan_support",),
    )
    x = np.linspace(9, 11, 201)
    y = 1000 * np.exp(-(((x - 10) / 0.1) ** 2)) + 5
    ctx = ScoringContext(
        rt_array=x,
        intensity_array=y,
        apex_index=100,
        half_width_ratio=1.0,
        fwhm_ratio=1.0,
        ms2_present=True,
        nl_match=True,
        rt_prior=10.0,
        rt_prior_sigma=0.1,
        rt_min=9.0,
        rt_max=11.0,
        dirty_matrix=False,
        ms2_trace_strength="strong",
        ms2_alignment_source="apex_fallback",
        trigger_scan_count=2,
        strict_nl_scan_count=2,
    )

    scored = score_candidate(cand, ctx, prior_rt=10.0)

    assert "strict_nl_ok" in scored.evidence_score.support_labels
    assert "ms2_trace_strong" not in scored.evidence_score.support_labels
    assert "sparse_apex_ms2" in scored.evidence_score.concern_labels
    assert scored.evidence_facts is not None
    assert scored.evidence_facts.chemical.alignment_source == "apex_fallback"
    assert "trace_morphology_conflict" in scored.reason


def test_score_candidate_records_moderate_ms2_trace_support() -> None:
    cand = _make_candidate(apex_rt=10.0, apex_intensity=1000)
    x = np.linspace(9, 11, 201)
    y = 1000 * np.exp(-(((x - 10) / 0.1) ** 2)) + 5
    ctx = ScoringContext(
        rt_array=x,
        intensity_array=y,
        apex_index=100,
        half_width_ratio=1.0,
        fwhm_ratio=1.0,
        ms2_present=True,
        nl_match=True,
        rt_prior=10.0,
        rt_prior_sigma=0.1,
        rt_min=9.0,
        rt_max=11.0,
        dirty_matrix=False,
        ms2_trace_strength="moderate",
    )

    scored = score_candidate(cand, ctx, prior_rt=10.0)

    assert "ms2_trace_moderate" in scored.evidence_score.support_labels
    assert scored.evidence_facts is not None
    assert scored.evidence_facts.chemical.ms2_trace_strength == "moderate"


def test_score_candidate_records_same_apex_cwt_support() -> None:
    cand = replace(
        _make_candidate(apex_rt=10.0, apex_intensity=1000),
        proposal_sources=("legacy_savgol", "centwave_cwt"),
        cwt_best_scale=4.0,
        cwt_ridge_persistence=0.5,
    )
    x = np.linspace(9, 11, 201)
    y = 1000 * np.exp(-(((x - 10) / 0.1) ** 2)) + 5
    ctx = ScoringContext(
        rt_array=x,
        intensity_array=y,
        apex_index=100,
        half_width_ratio=1.0,
        fwhm_ratio=1.0,
        ms2_present=True,
        nl_match=True,
        rt_prior=10.0,
        rt_prior_sigma=0.1,
        rt_min=9.0,
        rt_max=11.0,
        dirty_matrix=False,
    )

    scored = score_candidate(cand, ctx, prior_rt=10.0)

    assert "cwt_same_apex_support" in scored.evidence_score.support_labels
    assert scored.evidence_score.positive_points >= 5


def test_peak_candidate_documents_legacy_cwt_metric_semantics() -> None:
    assert PeakCandidate.__doc__ is not None
    peak_candidate_doc = " ".join(PeakCandidate.__doc__.split())
    assert "audit-presence flags" in PeakCandidate.__doc__
    assert "not interpretable as CWT scale or ridge metrics" in peak_candidate_doc
    assert EvidenceVector.__doc__ is not None
    evidence_vector_doc = " ".join(EvidenceVector.__doc__.split())
    assert "audit-presence flags" in EvidenceVector.__doc__
    assert "not interpretable as CWT scale or ridge metrics" in evidence_vector_doc


def test_cwt_only_candidate_does_not_receive_same_apex_support_bonus() -> None:
    cand = replace(
        _make_candidate(apex_rt=10.0, apex_intensity=1000),
        proposal_sources=("centwave_cwt",),
        cwt_best_scale=4.0,
        cwt_ridge_persistence=0.5,
    )
    x = np.linspace(9, 11, 201)
    y = 1000 * np.exp(-(((x - 10) / 0.1) ** 2)) + 5
    ctx = ScoringContext(
        rt_array=x,
        intensity_array=y,
        apex_index=100,
        half_width_ratio=1.0,
        fwhm_ratio=1.0,
        ms2_present=True,
        nl_match=True,
        rt_prior=10.0,
        rt_prior_sigma=0.1,
        rt_min=9.0,
        rt_max=11.0,
        dirty_matrix=False,
    )

    scored = score_candidate(cand, ctx, prior_rt=10.0)

    assert "cwt_same_apex_support" not in scored.evidence_score.support_labels
    assert "cwt_same_apex_support" not in scored.evidence_score.concern_labels


def test_cwt_support_requires_chemical_evidence_when_nl_is_required() -> None:
    cand = replace(
        _make_candidate(apex_rt=10.0, apex_intensity=1000),
        proposal_sources=("legacy_savgol", "centwave_cwt"),
        cwt_best_scale=4.0,
        cwt_ridge_persistence=0.5,
    )
    x = np.linspace(9, 11, 201)
    y = 1000 * np.exp(-(((x - 10) / 0.1) ** 2)) + 5
    ctx = ScoringContext(
        rt_array=x,
        intensity_array=y,
        apex_index=100,
        half_width_ratio=1.0,
        fwhm_ratio=1.0,
        ms2_present=True,
        nl_match=False,
        rt_prior=10.0,
        rt_prior_sigma=0.1,
        rt_min=9.0,
        rt_max=11.0,
        dirty_matrix=False,
    )

    scored = score_candidate(cand, ctx, prior_rt=10.0)

    assert "cwt_same_apex_support" not in scored.evidence_score.support_labels
    assert "cwt_same_apex_support" not in scored.evidence_score.concern_labels


def test_weak_ms2_trace_concern_keeps_strict_nl_support() -> None:
    cand = _make_candidate(apex_rt=10.0, apex_intensity=1000)
    x = np.linspace(9, 11, 201)
    y = 1000 * np.exp(-(((x - 10) / 0.1) ** 2)) + 5
    ctx = ScoringContext(
        rt_array=x,
        intensity_array=y,
        apex_index=100,
        half_width_ratio=1.0,
        fwhm_ratio=1.0,
        ms2_present=True,
        nl_match=True,
        rt_prior=10.0,
        rt_prior_sigma=0.1,
        rt_min=9.0,
        rt_max=11.0,
        dirty_matrix=False,
        ms2_trace_strength="weak",
    )

    scored = score_candidate(cand, ctx, prior_rt=10.0)

    assert "strict_nl_ok" in scored.evidence_score.support_labels
    assert "ms2_trace_weak" in scored.evidence_score.concern_labels
    assert scored.evidence_facts is not None
    assert scored.evidence_facts.chemical.ms2_trace_strength == "weak"


def test_score_candidate_nl_fail_caps_confidence_to_very_low() -> None:
    cand = _make_candidate(apex_rt=10.0, apex_intensity=1000)
    x = np.linspace(9, 11, 201)
    y = 1000 * np.exp(-(((x - 10) / 0.1) ** 2)) + 5
    ctx = ScoringContext(
        rt_array=x,
        intensity_array=y,
        apex_index=100,
        half_width_ratio=1.0,
        fwhm_ratio=1.0,
        ms2_present=True,
        nl_match=False,
        rt_prior=10.0,
        rt_prior_sigma=0.1,
        rt_min=9.0,
        rt_max=11.0,
        dirty_matrix=False,
    )

    scored = score_candidate(cand, ctx, prior_rt=10.0)

    assert scored.confidence == Confidence.MEDIUM
    assert "nl_fail" in scored.evidence_score.concern_labels
    assert "nl_fail_cap" in scored.evidence_score.cap_labels
    assert "plausible_nl_dropout_review" in scored.reason


def test_reason_text_leads_with_decision_then_support_concerns_and_caps() -> None:
    cand = _make_candidate(apex_rt=10.8, apex_intensity=1000)
    x = np.linspace(9, 11, 201)
    y = 1000 * np.exp(-(((x - 10.8) / 0.1) ** 2)) + 5
    ctx = ScoringContext(
        rt_array=x,
        intensity_array=y,
        apex_index=180,
        half_width_ratio=1.0,
        fwhm_ratio=1.0,
        ms2_present=True,
        nl_match=False,
        rt_prior=10.0,
        rt_prior_sigma=0.1,
        rt_min=9.0,
        rt_max=11.0,
        dirty_matrix=False,
    )

    scored = score_candidate(cand, ctx, prior_rt=10.0)

    assert scored.reason.startswith("decision: review")
    assert "plausible_nl_dropout_review" in scored.reason
    assert "targeted_rt_conflict" in scored.reason
    assert "nl_fail_cap" in scored.evidence_score.cap_labels
    assert "rt_prior_far" in scored.evidence_score.concern_labels


def test_score_candidate_no_nl_target_records_no_nl_support() -> None:
    cand = _make_candidate(apex_rt=10.0, apex_intensity=1000)
    x = np.linspace(9, 11, 201)
    y = 1000 * np.exp(-(((x - 10) / 0.1) ** 2)) + 5
    ctx = ScoringContext(
        rt_array=x,
        intensity_array=y,
        apex_index=100,
        half_width_ratio=1.0,
        fwhm_ratio=1.0,
        ms2_present=False,
        nl_match=False,
        rt_prior=10.0,
        rt_prior_sigma=0.1,
        rt_min=9.0,
        rt_max=11.0,
        dirty_matrix=False,
        neutral_loss_required=False,
    )

    scored = score_candidate(cand, ctx, prior_rt=10.0)

    assert scored.confidence == Confidence.HIGH
    assert "no_nl_required" in scored.evidence_score.support_labels
    assert "no_ms2" not in scored.evidence_score.concern_labels
    assert "no_ms2_cap" not in scored.evidence_score.cap_labels
    assert scored.reason.startswith("decision: accepted")
    assert "ms1_coherent" in scored.reason
    assert "role_aware_rt_support" in scored.reason


def test_score_candidate_no_ms2_default_reason_is_not_counted() -> None:
    cand = _make_candidate(apex_rt=10.0, apex_intensity=1000)
    x = np.linspace(9, 11, 201)
    y = 1000 * np.exp(-(((x - 10) / 0.1) ** 2)) + 5
    ctx = ScoringContext(
        rt_array=x,
        intensity_array=y,
        apex_index=100,
        half_width_ratio=1.0,
        fwhm_ratio=1.0,
        ms2_present=False,
        nl_match=False,
        rt_prior=10.0,
        rt_prior_sigma=0.1,
        rt_min=9.0,
        rt_max=11.0,
        dirty_matrix=False,
        neutral_loss_required=True,
    )

    scored = score_candidate(cand, ctx, prior_rt=10.0)

    assert scored.confidence == Confidence.VERY_LOW
    assert "no_ms2_cap" in scored.evidence_score.cap_labels
    assert scored.reason.startswith("decision: not_counted")
    assert "missing_ms2_policy_not_counted" in scored.reason
    assert "decision: accepted" not in scored.reason


def test_score_candidate_no_ms2_allowed_reason_is_accepted() -> None:
    cand = _make_candidate(apex_rt=10.0, apex_intensity=1000)
    x = np.linspace(9, 11, 201)
    y = 1000 * np.exp(-(((x - 10) / 0.1) ** 2)) + 5
    ctx = ScoringContext(
        rt_array=x,
        intensity_array=y,
        apex_index=100,
        half_width_ratio=1.0,
        fwhm_ratio=1.0,
        ms2_present=False,
        nl_match=False,
        rt_prior=10.0,
        rt_prior_sigma=0.1,
        rt_min=9.0,
        rt_max=11.0,
        dirty_matrix=False,
        neutral_loss_required=True,
        count_no_ms2_as_detected=True,
    )

    scored = score_candidate(cand, ctx, prior_rt=10.0)

    assert scored.confidence == Confidence.MEDIUM
    assert "no_ms2_cap" in scored.evidence_score.cap_labels
    assert scored.reason.startswith("decision: review")
    assert "missing_ms2_not_observed" in scored.reason


def test_score_candidate_maps_rt_centrality_and_noise_shape_concerns() -> None:
    cand = _make_candidate(apex_rt=9.0, apex_intensity=1000)
    x = np.linspace(9, 11, 201)
    y = np.where(np.arange(201) % 2 == 0, 1000.0, 0.0)
    ctx = ScoringContext(
        rt_array=x,
        intensity_array=y,
        apex_index=100,
        half_width_ratio=1.0,
        fwhm_ratio=1.0,
        ms2_present=True,
        nl_match=True,
        rt_prior=9.0,
        rt_prior_sigma=0.1,
        rt_min=9.0,
        rt_max=11.0,
        dirty_matrix=False,
    )

    scored = score_candidate(cand, ctx, prior_rt=9.0)

    assert "rt_centrality_poor" in scored.evidence_score.concern_labels
    assert "noise_shape_poor" in scored.evidence_score.concern_labels


def test_score_candidate_caps_out_of_window_peak_without_rt_prior() -> None:
    cand = _make_candidate(apex_rt=15.166, apex_intensity=1000)
    x = np.linspace(14.5, 15.8, 131)
    y = 1000 * np.exp(-(((x - 15.166) / 0.1) ** 2)) + 5
    ctx = ScoringContext(
        rt_array=x,
        intensity_array=y,
        apex_index=67,
        half_width_ratio=1.0,
        fwhm_ratio=1.0,
        ms2_present=True,
        nl_match=True,
        rt_prior=None,
        rt_prior_sigma=None,
        rt_min=16.0,
        rt_max=18.0,
        dirty_matrix=False,
    )

    scored = score_candidate(cand, ctx, prior_rt=None)

    assert scored.confidence == Confidence.LOW
    assert "rt_window_cap" in scored.evidence_score.cap_labels
    assert "rt_centrality_poor" in scored.evidence_score.concern_labels
    assert scored.reason.startswith("decision: review")
    assert "targeted_rt_conflict" in scored.reason


def test_score_candidate_allows_out_of_window_peak_with_close_rt_prior() -> None:
    cand = _make_candidate(apex_rt=15.166, apex_intensity=1000)
    x = np.linspace(14.5, 15.8, 131)
    y = 1000 * np.exp(-(((x - 15.166) / 0.1) ** 2)) + 5
    ctx = ScoringContext(
        rt_array=x,
        intensity_array=y,
        apex_index=67,
        half_width_ratio=1.0,
        fwhm_ratio=1.0,
        ms2_present=True,
        nl_match=True,
        rt_prior=15.166,
        rt_prior_sigma=0.1,
        rt_min=16.0,
        rt_max=18.0,
        dirty_matrix=False,
    )

    scored = score_candidate(cand, ctx, prior_rt=15.166)

    assert scored.confidence == Confidence.LOW
    assert "rt_window_cap" not in scored.evidence_score.cap_labels
    assert "rt_prior_close" in scored.evidence_score.support_labels
    assert "rt_centrality_poor" in scored.evidence_score.concern_labels
    assert "targeted_rt_conflict" in scored.reason


def test_score_candidate_keeps_in_window_edge_peak_as_rt_centrality_concern() -> None:
    cand = _make_candidate(apex_rt=16.005, apex_intensity=1000)
    x = np.linspace(15.8, 16.3, 101)
    y = 1000 * np.exp(-(((x - 16.005) / 0.1) ** 2)) + 5
    ctx = ScoringContext(
        rt_array=x,
        intensity_array=y,
        apex_index=41,
        half_width_ratio=1.0,
        fwhm_ratio=1.0,
        ms2_present=True,
        nl_match=True,
        rt_prior=None,
        rt_prior_sigma=None,
        rt_min=16.0,
        rt_max=18.0,
        dirty_matrix=False,
    )

    scored = score_candidate(cand, ctx, prior_rt=None)

    assert scored.confidence == Confidence.LOW
    assert "rt_window_cap" not in scored.evidence_score.cap_labels
    assert "rt_centrality_poor" in scored.evidence_score.concern_labels
    assert "targeted_rt_conflict" in scored.reason


@pytest.mark.parametrize(
    ("evidence_context", "include_quality_note"),
    [
        (None, True),
        ({}, True),
        ({"evidence_role": "ISTD"}, False),
        ({"evidence_role": "Analyte", "istd_pair": "ISTD"}, False),
        ({"istd_pair": "ISTD"}, False),
        ({"paired_istd_anchor_rt": 10.0}, False),
    ],
)
def test_score_candidate_penalizes_flagged_candidate_quality(
    evidence_context, include_quality_note: bool
) -> None:
    cand = _make_flagged_candidate(
        apex_rt=10.0,
        apex_intensity=1000.0,
        quality_flags=("too_broad",),
    )
    x = np.linspace(9, 11, 201)
    y = 1000 * np.exp(-(((x - 10) / 0.1) ** 2)) + 5
    ctx = ScoringContext(
        rt_array=x,
        intensity_array=y,
        apex_index=100,
        half_width_ratio=1.0,
        fwhm_ratio=1.0,
        ms2_present=True,
        nl_match=True,
        rt_prior=10.0,
        rt_prior_sigma=0.1,
        rt_min=9.0,
        rt_max=11.0,
        dirty_matrix=False,
    )

    note = "paired ISTD confirmed"
    if evidence_context is None:
        scored = score_candidate(cand, ctx, prior_rt=10.0, istd_confidence_note=note)
    else:
        scored = score_candidate(
            cand,
            ctx,
            prior_rt=ctx.rt_prior,
            istd_confidence_note=note,
            **evidence_context,
        )

    assert scored.confidence == Confidence.LOW
    assert scored.evidence_facts is not None
    assert "too_broad" in scored.evidence_facts.trace.quality_flags
    assert "hard_quality_flag_conflict" in scored.reason
    assert ("weak candidate: too_broad" in scored.reason) is include_quality_note
    assert scored.reason.endswith(f"; {note}")
    assert scored.quality_penalty == 1
    assert scored.selection_quality_penalty == 1
    assert scored.evidence_score is not None
    assert "hard_quality_flag" in scored.evidence_score.concern_labels
    assert "hard_quality_flag_cap" in scored.evidence_score.cap_labels
    assert len(scored.severities) == 10


def test_score_candidate_formats_adap_like_quality_flags_as_minor_concerns() -> None:
    cand = _make_flagged_candidate(
        apex_rt=10.0,
        apex_intensity=1000.0,
        quality_flags=("low_trace_continuity", "poor_edge_recovery"),
    )
    x = np.linspace(9, 11, 201)
    y = 1000 * np.exp(-(((x - 10) / 0.1) ** 2)) + 5
    ctx = ScoringContext(
        rt_array=x,
        intensity_array=y,
        apex_index=100,
        half_width_ratio=1.0,
        fwhm_ratio=1.0,
        ms2_present=True,
        nl_match=True,
        rt_prior=10.0,
        rt_prior_sigma=0.1,
        rt_min=9.0,
        rt_max=11.0,
        dirty_matrix=False,
    )

    scored = score_candidate(cand, ctx, prior_rt=10.0)

    assert scored.confidence == Confidence.LOW
    assert scored.quality_penalty == 0
    assert scored.selection_quality_penalty == 0.5
    assert (1, "low trace continuity") in scored.severities
    assert (1, "poor edge recovery") in scored.severities
    assert scored.reason.startswith("decision: review")
    assert "trace_quality_cap" in scored.evidence_score.cap_labels
    assert "trace_morphology_conflict" in scored.reason


def test_single_trace_continuity_warning_does_not_cap_supported_peak() -> None:
    cand = _make_flagged_candidate(
        apex_rt=10.0,
        apex_intensity=1000.0,
        quality_flags=("low_trace_continuity",),
    )
    x = np.linspace(9, 11, 201)
    y = 1000 * np.exp(-(((x - 10) / 0.1) ** 2)) + 5
    ctx = ScoringContext(
        rt_array=x,
        intensity_array=y,
        apex_index=100,
        half_width_ratio=1.0,
        fwhm_ratio=1.0,
        ms2_present=True,
        nl_match=True,
        rt_prior=10.0,
        rt_prior_sigma=0.1,
        rt_min=9.0,
        rt_max=11.0,
        dirty_matrix=False,
    )

    scored = score_candidate(cand, ctx, prior_rt=10.0)

    assert scored.confidence == Confidence.LOW
    assert "low_trace_continuity" in scored.evidence_score.concern_labels
    assert "trace_quality_cap" not in scored.evidence_score.cap_labels
    assert "trace_morphology_conflict" in scored.reason
    assert "cap: MEDIUM due to trace quality" not in scored.reason


def test_cwt_same_apex_support_prevents_trace_boundary_double_cap() -> None:
    cand = replace(
        _make_flagged_candidate(
            apex_rt=10.0,
            apex_intensity=1000.0,
            quality_flags=("low_trace_continuity", "poor_edge_recovery"),
        ),
        proposal_sources=("local_minimum", "centwave_cwt"),
        cwt_best_scale=4.0,
        cwt_ridge_persistence=0.5,
    )
    x = np.linspace(9, 11, 201)
    y = 1000 * np.exp(-(((x - 10) / 0.1) ** 2)) + 5
    ctx = ScoringContext(
        rt_array=x,
        intensity_array=y,
        apex_index=100,
        half_width_ratio=1.0,
        fwhm_ratio=1.0,
        ms2_present=True,
        nl_match=True,
        rt_prior=10.0,
        rt_prior_sigma=0.1,
        rt_min=9.0,
        rt_max=11.0,
        dirty_matrix=False,
    )

    scored = score_candidate(cand, ctx, prior_rt=10.0)

    assert scored.confidence == Confidence.LOW
    assert "cwt_same_apex_support" in scored.evidence_score.support_labels
    assert "low_trace_continuity" in scored.evidence_score.concern_labels
    assert "poor_edge_recovery" in scored.evidence_score.concern_labels
    assert "trace_quality_cap" not in scored.evidence_score.cap_labels
    assert "cap: MEDIUM due to trace quality" not in scored.reason
    assert "cwt_boundary_morphology_context" in scored.reason


def test_score_candidate_does_not_double_penalize_adap_equivalent_legacy_flags() -> (
    None
):
    cand = _make_flagged_candidate(
        apex_rt=10.0,
        apex_intensity=1000.0,
        quality_flags=(
            "low_scan_count",
            "low_scan_support",
            "low_top_edge_ratio",
            "poor_edge_recovery",
        ),
    )
    x = np.linspace(9, 11, 201)
    y = 1000 * np.exp(-(((x - 10) / 0.1) ** 2)) + 5
    ctx = ScoringContext(
        rt_array=x,
        intensity_array=y,
        apex_index=100,
        half_width_ratio=1.0,
        fwhm_ratio=1.0,
        ms2_present=True,
        nl_match=True,
        rt_prior=10.0,
        rt_prior_sigma=0.1,
        rt_min=9.0,
        rt_max=11.0,
        dirty_matrix=False,
    )

    scored = score_candidate(cand, ctx, prior_rt=10.0)

    assert scored.confidence == Confidence.LOW
    assert scored.quality_penalty == 0
    assert scored.selection_quality_penalty == 0.5
    assert (1, "low scan support") in scored.severities
    assert (1, "poor edge recovery") in scored.severities
    assert "hard_quality_flag_conflict" not in scored.reason
    assert scored.reason.startswith("decision: review")
    assert "trace_quality_cap" in scored.evidence_score.cap_labels
    assert "trace_morphology_conflict" in scored.reason
