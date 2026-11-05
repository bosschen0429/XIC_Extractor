from pathlib import Path

import numpy as np
import pytest

from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.peak_detection.baseline import asls_baseline, asls_baseline_on_rt
from xic_extractor.peak_detection.candidate_scoring import (
    candidate_score_summary,
    score_candidate,
)
from xic_extractor.peak_detection.evidence_facts import (
    build_candidate_evidence_facts,
    decision_semantics_from_candidate_facts,
    projected_reason_from_candidate_facts,
)
from xic_extractor.peak_detection.models import PeakCandidate, PeakResult
from xic_extractor.peak_detection.ms1_morphology import (
    gaussian15_morphology_trace,
    gaussian15_positive_asls_residual_trace,
)
from xic_extractor.peak_detection.peak_integration import find_peak_and_area
from xic_extractor.peak_detection.physical_peak_codec import (
    rehydrate_physical_peak_evaluation,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    answer_physical_peak_question,
    enumerate_physical_segment_universe,
    evaluate_physical_peak,
    mint_physical_peak_question,
    prepare_physical_signal,
    validate_bounded_physical_peak_evaluation,
)
from xic_extractor.peak_detection.scoring_models import ScoringContext
from xic_extractor.peak_detection.signal_quality import SignalQuality


def test_seed_485_separates_required_residual_first_and_legacy_formulas() -> None:
    rt = np.linspace(0.0, 2.0, 121)
    rng = np.random.default_rng(485)
    background = 25 + 45 * rt + 120 * (rt - 1.05) ** 2 + 18 * np.sin(4.5 * rt + 0.3)
    peak = 145 * np.exp(-0.5 * ((rt - 0.82) / 0.075) ** 2) + 145 * np.exp(
        -0.5 * ((rt - 1.18) / 0.075) ** 2
    )
    raw = background + peak + rng.normal(0, 14, len(rt))
    baseline = asls_baseline(raw)
    required = np.maximum(gaussian15_morphology_trace(raw) - baseline, 0.0)
    residual_first = gaussian15_positive_asls_residual_trace(raw, baseline)

    canonical_baseline = np.maximum(asls_baseline_on_rt(rt, raw, lam=1.0), 0.0)
    canonical_signal = np.maximum(
        gaussian15_morphology_trace(raw) - canonical_baseline, 0.0
    )
    prepared_result = prepare_physical_signal(
        rt, raw, profile=SignalProcessingProfile(window_points=15)
    )
    assert prepared_result.prepared is not None
    np.testing.assert_allclose(
        prepared_result.prepared.baseline,
        canonical_baseline,
        rtol=0.0,
        atol=1e-12,
    )
    np.testing.assert_allclose(
        prepared_result.prepared.smoothed,
        gaussian15_morphology_trace(raw),
        rtol=0.0,
        atol=1e-12,
    )
    np.testing.assert_allclose(
        prepared_result.prepared.positive_signal,
        canonical_signal,
        rtol=0.0,
        atol=1e-12,
    )
    required_facts = _twenty_percent_characterization(rt, required)
    residual_first_facts = _twenty_percent_characterization(rt, residual_first)
    # Frozen AsLS-derived areas need a cross-runner numerical tolerance.
    # Use the baseline solver's 1e-9 relative equivalence bound, not bitwise
    # precision; the formulas differ by >1000 and their indices remain exact.
    assert required_facts[:3] == (48, 41, 56)
    assert required_facts[3] == pytest.approx(
        1062.1744946180106,
        rel=1e-9,
        abs=1e-12,
    )
    assert residual_first_facts[:3] == (4, 0, 33)
    assert residual_first_facts[3] == pytest.approx(
        2245.7044316702472,
        rel=1e-9,
        abs=1e-12,
    )

    legacy = find_peak_and_area(
        rt,
        raw,
        _legacy_peak_config(),
        preferred_rt=0.8,
    )
    assert legacy.status == "OK"
    assert legacy.peak is not None
    assert (
        legacy.peak.rt,
        legacy.peak.peak_start,
        legacy.peak.peak_end,
    ) == pytest.approx(
        (0.8, 0.0, 0.9833333333333333),
        rel=0.0,
        abs=1e-12,
    )
    assert legacy.peak.area == pytest.approx(
        7410.08344021538,
        rel=1e-9,
        abs=1e-12,
    )


def test_physical_question_containment_is_closed_complete_and_owner_derived() -> None:
    profile = SignalProcessingProfile(window_points=3)
    rt = np.linspace(0.0, 3.0, 31)
    raw = (
        10.0
        + 100.0 * np.exp(-0.5 * ((rt - 0.8) / 0.2) ** 2)
        + 80.0 * np.exp(-0.5 * ((rt - 2.2) / 0.2) ** 2)
    )
    prepared = prepare_physical_signal(rt, raw, profile=profile)
    universe = enumerate_physical_segment_universe(prepared)
    assert len(universe.segments) == 2

    zero = mint_physical_peak_question(
        prepared.projection,
        universe,
        query_rt_min=1.5,
    )
    one = mint_physical_peak_question(
        prepared.projection,
        universe,
        query_rt_min=0.8,
    )
    assert zero.admissible_segments == ()
    assert one.admissible_segments == (universe.segments[0],)
    assert answer_physical_peak_question(prepared, universe, zero).reason == (
        "no_selection"
    )
    assert answer_physical_peak_question(prepared, universe, one).state == "available"

    overlap_rt = np.linspace(0.0, 3.0, 301)
    # The analytical valley is about 27% of either apex: clear enough to split,
    # but above the 10% return, so both closed intervals share that valley.
    overlap_raw = sum(
        100.0 * np.exp(-0.5 * ((overlap_rt - center) / 0.2) ** 2)
        for center in (1.0, 1.8)
    )
    overlap_prepared = prepare_physical_signal(
        overlap_rt,
        overlap_raw,
        profile=profile,
    )
    overlap_universe = enumerate_physical_segment_universe(overlap_prepared)
    assert len(overlap_universe.segments) == 2
    shared_boundary = overlap_universe.segments[0].right_rt_min
    assert shared_boundary == overlap_universe.segments[1].left_rt_min
    multiple = mint_physical_peak_question(
        overlap_prepared.projection,
        overlap_universe,
        query_rt_min=shared_boundary,
    )
    assert multiple.admissible_segments == overlap_universe.segments
    multiple_answer = answer_physical_peak_question(
        overlap_prepared,
        overlap_universe,
        multiple,
    )
    assert (multiple_answer.state, multiple_answer.reason) == (
        "unavailable",
        "ambiguous_segment_candidates",
    )


def test_physical_owner_mints_closed_unique_admissible_bounded_facts() -> None:
    rt = np.linspace(0.0, 3.0, 31)
    raw = (
        10.0
        + 100.0 * np.exp(-0.5 * ((rt - 0.8) / 0.2) ** 2)
        + 80.0 * np.exp(-0.5 * ((rt - 2.2) / 0.2) ** 2)
    )
    profile = SignalProcessingProfile(window_points=3)
    prepared = prepare_physical_signal(
        rt,
        raw,
        profile=profile,
    )
    evaluation = evaluate_physical_peak(
        prepared,
        query_rt_min=0.8,
        request_context_rt_start_min=0.7,
        request_context_rt_end_min=0.9,
    )
    universe = evaluation.segment_universe

    assert len(universe.segments) == 2
    assert universe.context_rt_start_min == 0.0
    assert universe.context_rt_end_min == 3.0
    assert evaluation.question.admissible_segments == (universe.segments[0],)
    assert evaluation.state == "available"
    assert evaluation.facts is not None
    assert universe.prepared == evaluation.prepared
    assert evaluation.question.universe == universe
    assert evaluation.answer.question == evaluation.question
    assert evaluation.answer.selected_segment == universe.segments[0]
    assert evaluation.facts.area_counts_seconds > 0.0
    assert evaluation.integration is not None
    selected = universe.segments[0]
    selected_raw = raw[selected.start_index : selected.end_index]
    selected_rt = rt[selected.start_index : selected.end_index]
    raw_apex_offset = int(np.argmax(selected_raw))
    assert evaluation.integration.raw_apex_rt_min == selected_rt[raw_apex_offset]
    assert evaluation.integration.height_raw == selected_raw[raw_apex_offset]
    assert evaluation.integration.area_raw_counts_seconds == pytest.approx(
        np.trapezoid(selected_raw, selected_rt) * 60.0
    )
    assert evaluation.integration.raw_scan_indices == ()
    assert evaluation.operations == (
        "trace_evaluation",
        "asls_baseline",
        "gaussian_smoothing",
        "segment_enumeration",
        "integration",
        "raw_descriptor_projection",
    )
    assert not hasattr(evaluation, "rt")
    validate_bounded_physical_peak_evaluation(evaluation)
    assert (
        rehydrate_physical_peak_evaluation(
            rt,
            raw,
            profile=profile,
            expected=evaluation,
        )
        == evaluation
    )


def test_physical_owner_excludes_noncontaining_segments_from_admissibility() -> None:
    rt = np.linspace(0.0, 3.0, 31)
    raw = (
        10.0
        + 100.0 * np.exp(-0.5 * ((rt - 0.8) / 0.2) ** 2)
        + 80.0 * np.exp(-0.5 * ((rt - 2.2) / 0.2) ** 2)
    )
    prepared = prepare_physical_signal(
        rt,
        raw,
        profile=SignalProcessingProfile(window_points=3),
    )
    evaluation = evaluate_physical_peak(
        prepared,
        query_rt_min=0.8,
    )
    universe = evaluation.segment_universe

    assert len(universe.segments) == 2
    assert evaluation.question.admissible_segments == (universe.segments[0],)
    assert evaluation.state == "available"


def test_short_trace_is_unavailable_without_smoothing_or_silent_shrink() -> None:
    prepared = prepare_physical_signal(
        [3.0],
        [10.0],
        profile=SignalProcessingProfile(window_points=15),
    )
    evaluation = evaluate_physical_peak(
        prepared,
        query_rt_min=3.0,
    )

    assert evaluation.state == "unavailable"
    assert evaluation.reason == "insufficient_points_for_profile"
    assert evaluation.operations == ("trace_evaluation",)
    assert evaluation.segment_universe.context_rt_start_min == 3.0
    assert evaluation.segment_universe.context_rt_end_min == 3.0
    assert prepared.prepared is None


def test_zero_point_trace_has_terminal_question_using_live_request_bounds() -> None:
    prepared = prepare_physical_signal(
        [],
        [],
        profile=SignalProcessingProfile(window_points=15),
    )

    evaluation = evaluate_physical_peak(
        prepared,
        query_rt_min=1.5,
        request_context_rt_start_min=1.0,
        request_context_rt_end_min=2.0,
    )

    assert evaluation.state == "unavailable"
    assert evaluation.reason == "no_signal_above_baseline"
    assert evaluation.segment_universe.context_rt_start_min == 1.0
    assert evaluation.segment_universe.context_rt_end_min == 2.0
    assert evaluation.answer.question == evaluation.question
    assert evaluation.operations == ("trace_evaluation",)


def test_malformed_trace_has_failed_terminal_question_and_rehydrates() -> None:
    rt = [float("nan"), 1.0, 2.0]
    raw = [1.0, 2.0, 1.0]
    profile = SignalProcessingProfile(window_points=3)
    prepared = prepare_physical_signal(rt, raw, profile=profile)

    evaluation = evaluate_physical_peak(
        prepared,
        query_rt_min=1.5,
        request_context_rt_start_min=1.0,
        request_context_rt_end_min=2.0,
    )

    assert evaluation.state == "failed"
    assert evaluation.reason == "nonfinite_trace"
    assert evaluation.segment_universe.segments == ()
    assert evaluation.facts is None
    assert evaluation.integration is None
    assert evaluation.operations == ("trace_evaluation",)
    replay = rehydrate_physical_peak_evaluation(
        rt,
        raw,
        profile=profile,
        expected=evaluation,
        request_context_rt_start_min=1.0,
        request_context_rt_end_min=2.0,
    )
    assert replay.state == "failed"
    assert replay.reason == "nonfinite_trace"
    np.testing.assert_array_equal(replay.prepared.rt, rt)
    assert replay.prepared.raw == tuple(raw)
    assert replay.facts is None


def test_nonpositive_trace_is_unavailable_before_asls_or_smoothing() -> None:
    prepared = prepare_physical_signal(
        np.arange(5.0),
        np.zeros(5),
        profile=SignalProcessingProfile(window_points=3),
    )

    assert prepared.projection.state == "unavailable"
    assert prepared.projection.reason == "no_signal_above_baseline"
    assert prepared.operations == ("trace_evaluation",)
    assert prepared.prepared is None


def test_build_candidate_evidence_facts_from_scoring_context() -> None:
    rt = np.asarray([8.3, 8.4, 8.5, 8.6, 8.7])
    intensity = np.asarray([100.0, 500.0, 1000.0, 520.0, 120.0])
    candidate = _candidate(
        8.5,
        proposal_sources=("local_minimum", "centwave_cwt"),
        quality_flags=("low_trace_continuity",),
        cwt_best_scale=4.0,
        cwt_ridge_persistence=0.6,
    )
    ctx = ScoringContext(
        rt_array=rt,
        intensity_array=intensity,
        apex_index=2,
        half_width_ratio=1.0,
        fwhm_ratio=1.0,
        ms2_present=True,
        nl_match=True,
        rt_prior=8.45,
        rt_prior_sigma=0.1,
        rt_min=8.0,
        rt_max=9.0,
        ms2_trace_strength="strong",
        ms2_alignment_source="region",
        trigger_scan_count=3,
        strict_nl_scan_count=2,
        signal_quality=SignalQuality(
            "available",
            noise_sigma=5.0,
            signal_to_noise=181.0,
            low_signal_to_noise=False,
        ),
        active_trace_source="gaussian15_positive_asls_residual",
        morphology_trace_method="gaussian_15",
        morphology_trace_window_points=15,
    )

    facts = build_candidate_evidence_facts(
        candidate,
        ctx,
        role="Analyte",
        istd_pair="ISTD",
    )

    assert facts.facts_version == "candidate_evidence_facts"
    assert facts.candidate_id.startswith("local_minimum;centwave_cwt|8.50000")
    assert facts.abundance == candidate.peak.area
    assert facts.trace.local_sn_quality == "strong"
    assert facts.trace.active_trace_source == "gaussian15_positive_asls_residual"
    assert facts.trace.morphology_trace_method == "gaussian_15"
    assert facts.trace.morphology_trace_window_points == 15
    assert facts.trace.symmetry_quality == "clean"
    assert facts.trace.width_quality == "clean"
    assert facts.trace.noise_shape_quality == "clean"
    assert facts.trace.quality_flags == ("low_trace_continuity",)
    assert facts.chemical.ms2_present is True
    assert facts.chemical.nl_match is True
    assert facts.chemical.ms2_trace_strength == "strong"
    assert facts.chemical.acquisition_opportunity == "observed"
    assert facts.rt.rt_prior_delta_min == 0.05
    assert facts.rt.rt_prior_status == "close"
    assert facts.rt.window_status == "inside"

    assert facts.boundary.cwt_same_apex_observed is True
    assert facts.boundary.cwt_best_scale == 4.0


def test_candidate_evidence_facts_preserve_gaussian15_ms1_peak_group_scope() -> None:
    candidate = _candidate(8.5, proposal_sources=("chrom_peak_segment",))
    ctx = ScoringContext(
        rt_array=np.asarray([8.3, 8.4, 8.5, 8.6, 8.7]),
        intensity_array=np.asarray([100.0, 500.0, 1000.0, 520.0, 120.0]),
        apex_index=2,
        half_width_ratio=1.0,
        fwhm_ratio=1.0,
        ms2_present=True,
        nl_match=True,
        rt_prior=8.5,
        rt_prior_sigma=0.1,
        rt_min=8.0,
        rt_max=9.0,
        ms2_trace_strength="strong",
        ms2_alignment_source="region",
        trigger_scan_count=2,
        strict_nl_scan_count=2,
        ms1_peak_group_source="gaussian15_ms1_peak_group",
        ms1_peak_group_rt_min=8.4,
        ms1_peak_group_rt_max=8.6,
        ms1_peak_group_trigger_scan_count=2,
        ms1_peak_group_strict_nl_scan_count=2,
        ms1_peak_group_strict_nl_event_count=1,
        outside_ms1_peak_group_trigger_scan_count=1,
        outside_ms1_peak_group_strict_nl_scan_count=1,
        signal_quality=SignalQuality(
            "available",
            noise_sigma=5.0,
            signal_to_noise=181.0,
            low_signal_to_noise=False,
        ),
    )

    facts = build_candidate_evidence_facts(candidate, ctx, role="Analyte")

    assert facts.chemical.ms1_peak_group_source == "gaussian15_ms1_peak_group"
    assert facts.chemical.ms1_peak_group_rt_min == 8.4
    assert facts.chemical.ms1_peak_group_rt_max == 8.6
    assert facts.chemical.ms1_peak_group_trigger_scan_count == 2
    assert facts.chemical.ms1_peak_group_strict_nl_scan_count == 2
    assert facts.chemical.ms1_peak_group_strict_nl_event_count == 1
    assert facts.chemical.outside_ms1_peak_group_trigger_scan_count == 1
    assert facts.chemical.outside_ms1_peak_group_strict_nl_scan_count == 1


def test_candidate_facts_drive_semantics_and_projection_without_score_labels() -> None:
    rt = np.asarray([8.3, 8.4, 8.5, 8.6, 8.7])
    intensity = np.asarray([100.0, 500.0, 1000.0, 520.0, 120.0])
    candidate = _candidate(8.5, proposal_sources=("local_minimum",))
    ctx = ScoringContext(
        rt_array=rt,
        intensity_array=intensity,
        apex_index=2,
        half_width_ratio=1.0,
        fwhm_ratio=1.0,
        ms2_present=True,
        nl_match=True,
        rt_prior=8.5,
        rt_prior_sigma=0.1,
        rt_min=8.0,
        rt_max=9.0,
        ms2_trace_strength="moderate",
        signal_quality=SignalQuality(
            "available",
            noise_sigma=5.0,
            signal_to_noise=181.0,
            low_signal_to_noise=False,
        ),
    )
    facts = build_candidate_evidence_facts(candidate, ctx, role="ISTD", istd_pair="")

    semantics = decision_semantics_from_candidate_facts(facts)

    assert semantics.decision_class == "accepted"
    assert "ms1_coherent" in semantics.support_reasons
    assert "candidate_aligned_ms2_nl" in semantics.support_reasons
    assert projected_reason_from_candidate_facts(facts, semantics).startswith(
        "decision: accepted"
    )


def test_paired_istd_anchor_delta_is_typed_rt_evidence() -> None:
    candidate = _candidate(8.5, proposal_sources=("local_minimum",))
    ctx = ScoringContext(
        rt_array=np.asarray([8.3, 8.4, 8.5, 8.6, 8.7]),
        intensity_array=np.asarray([100.0, 500.0, 1000.0, 520.0, 120.0]),
        apex_index=2,
        half_width_ratio=1.0,
        fwhm_ratio=1.0,
        ms2_present=False,
        nl_match=False,
        rt_prior=8.5,
        rt_prior_sigma=0.1,
        rt_min=8.0,
        rt_max=9.0,
        signal_quality=SignalQuality(
            "available",
            noise_sigma=5.0,
            signal_to_noise=181.0,
            low_signal_to_noise=False,
        ),
    )

    scored = score_candidate(
        candidate,
        ctx,
        prior_rt=ctx.rt_prior,
        evidence_role="Analyte",
        istd_pair="ISTD",
        paired_istd_anchor_rt=7.8,
    )

    facts = scored.evidence_facts
    assert facts is not None
    assert facts.rt.paired_istd_delta_min == 0.7
    assert facts.rt.paired_istd_status == "close"
    assert facts.rt.role == "Analyte"
    assert facts.rt.istd_pair == "ISTD"
    assert facts.chemical.ms2_present is False
    assert "missing_ms2_policy_not_counted" in scored.reason

    summary = candidate_score_summary(scored)
    assert summary.evidence_facts is not None
    assert summary.evidence_facts.rt.paired_istd_delta_min == 0.7
    assert summary.evidence_facts.rt.role == "Analyte"
    assert summary.evidence_facts.chemical.ms2_present is False
    assert summary.evidence_facts.area == candidate.peak.area
    assert summary.reason == scored.reason
    assert scored.evidence_score is not None
    assert summary.raw_score == scored.evidence_score.raw_score
    assert summary.support_labels == scored.evidence_score.support_labels
    assert summary.concern_labels == scored.evidence_score.concern_labels
    assert summary.cap_labels == scored.evidence_score.cap_labels


def test_paired_istd_anchor_projects_one_role_aware_rt_support_reason() -> None:
    candidate = _candidate(8.5, proposal_sources=("local_minimum",))
    ctx = ScoringContext(
        rt_array=np.asarray([8.3, 8.4, 8.5, 8.6, 8.7]),
        intensity_array=np.asarray([100.0, 500.0, 1000.0, 520.0, 120.0]),
        apex_index=2,
        half_width_ratio=1.0,
        fwhm_ratio=1.0,
        ms2_present=True,
        nl_match=True,
        rt_prior=8.5,
        rt_prior_sigma=0.1,
        rt_min=8.0,
        rt_max=9.0,
        signal_quality=SignalQuality(
            "available",
            noise_sigma=5.0,
            signal_to_noise=181.0,
            low_signal_to_noise=False,
        ),
    )
    facts = build_candidate_evidence_facts(
        candidate,
        ctx,
        role="Analyte",
        istd_pair="ISTD",
        paired_istd_anchor_rt_min=8.45,
    )

    semantics = decision_semantics_from_candidate_facts(facts)

    assert facts.rt.rt_prior_status == "close"
    assert facts.rt.paired_istd_status == "close"
    assert semantics.support_reasons.count("role_aware_rt_support") == 1
    assert "paired_istd_rt_support" not in semantics.support_reasons


def test_nl_fail_with_paired_istd_rt_mismatch_retains_review_evidence() -> None:
    candidate = _candidate(11.25, proposal_sources=("local_minimum",))
    ctx = ScoringContext(
        rt_array=np.asarray([11.1, 11.2, 11.25, 11.3, 11.4]),
        intensity_array=np.asarray([100.0, 500.0, 1000.0, 520.0, 120.0]),
        apex_index=2,
        half_width_ratio=1.0,
        fwhm_ratio=1.0,
        ms2_present=True,
        nl_match=False,
        rt_prior=11.25,
        rt_prior_sigma=0.1,
        rt_min=11.0,
        rt_max=11.5,
        signal_quality=SignalQuality(
            "available",
            noise_sigma=5.0,
            signal_to_noise=181.0,
            low_signal_to_noise=False,
        ),
        trigger_scan_count=1,
        strict_nl_scan_count=0,
    )
    facts = build_candidate_evidence_facts(
        candidate,
        ctx,
        role="Analyte",
        istd_pair="ISTD",
        paired_istd_anchor_rt_min=10.0,
    )

    semantics = decision_semantics_from_candidate_facts(facts)

    assert facts.rt.paired_istd_delta_min == 1.25
    assert facts.rt.paired_istd_status == "far"
    assert semantics.decision_class == "review"
    assert "anchor_conflict" in semantics.conflict_reasons
    assert semantics.not_counted_reasons == ()
    assert "candidate_aligned_ms2_nl" not in semantics.support_reasons


def _twenty_percent_characterization(
    rt: np.ndarray,
    signal: np.ndarray,
) -> tuple[int, int, int, float]:
    apex_index = int(np.argmax(signal))
    threshold = float(signal[apex_index]) * 0.2
    left_index = apex_index
    right_index = apex_index
    while left_index > 0 and signal[left_index - 1] >= threshold:
        left_index -= 1
    while right_index < len(signal) - 1 and signal[right_index + 1] >= threshold:
        right_index += 1
    area = float(
        np.trapezoid(
            signal[left_index : right_index + 1],
            rt[left_index : right_index + 1],
        )
        * 60.0
    )
    return apex_index, left_index, right_index, area


def _legacy_peak_config() -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=Path("raw"),
        dll_dir=Path("dll"),
        output_csv=Path("output/xic_results.csv"),
        diagnostics_csv=Path("output/xic_diagnostics.csv"),
        smooth_window=15,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.10,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
    )


def _candidate(
    rt: float,
    *,
    proposal_sources: tuple[str, ...] = ("local_minimum",),
    quality_flags: tuple[str, ...] = (),
    cwt_best_scale: float | None = None,
    cwt_ridge_persistence: float | None = None,
) -> PeakCandidate:
    peak = PeakResult(
        rt=rt,
        intensity=1200.0,
        intensity_smoothed=1100.0,
        area=1234.0,
        peak_start=rt - 0.1,
        peak_end=rt + 0.1,
    )
    return PeakCandidate(
        peak=peak,
        selection_apex_rt=rt,
        selection_apex_intensity=1100.0,
        selection_apex_index=2,
        raw_apex_rt=rt,
        raw_apex_intensity=1200.0,
        raw_apex_index=2,
        prominence=900.0,
        quality_flags=quality_flags,
        region_scan_count=8,
        region_duration_min=0.2,
        region_edge_ratio=0.8,
        region_trace_continuity=0.9,
        cwt_best_scale=cwt_best_scale,
        cwt_ridge_persistence=cwt_ridge_persistence,
        proposal_sources=proposal_sources,
    )
