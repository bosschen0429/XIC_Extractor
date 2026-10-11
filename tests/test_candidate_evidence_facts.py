import hashlib
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

import xic_extractor.peak_detection.evidence_facts as evidence_facts_module
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
    gaussian15_trace_window_evidence,
    integration_with_trace_evidence,
    peak_trace_evidence,
    projected_confidence_from_candidate_facts,
    projected_reason_from_candidate_facts,
)
from xic_extractor.peak_detection.integration_result import IntegrationResult
from xic_extractor.peak_detection.models import PeakCandidate, PeakResult
from xic_extractor.peak_detection.ms1_morphology import (
    gaussian15_morphology_trace,
    gaussian15_positive_asls_residual_metrics,
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
    validate_physical_peak_question_identity,
)
from xic_extractor.peak_detection.physical_peak_identity import (
    canonical_physical_peak_question_bytes,
    mint_physical_trace_content_id,
)
from xic_extractor.peak_detection.physical_peak_models import PhysicalPeakQuestion
from xic_extractor.peak_detection.scoring_models import ScoringContext


def test_trace_window_evidence_uses_shared_gaussian15_own_max_trace() -> None:
    rt = [0.0, 0.1, 0.2, 0.3, 0.4]

    normalized, facts = gaussian15_trace_window_evidence(
        rt,
        [0.0, 0.0, 100.0, 0.0, 0.0],
        query_apex_rt_min=0.2,
        half_window_min=0.11,
    )

    assert facts.status == "ok"
    assert facts.reason == "gaussian15_query_window_evidence"
    assert facts.query_apex_in_trace_window is True
    assert facts.trace_apex_rt_min == pytest.approx(0.2)
    assert facts.global_trace_apex_delta_min == pytest.approx(0.0)
    assert facts.local_apex_delta_min == pytest.approx(0.0)
    assert facts.local_to_global_max_ratio == pytest.approx(1.0)
    assert facts.local_max_intensity == pytest.approx(normalized.max_intensity)


def test_trace_window_coverage_survives_inconclusive_non_positive_signal() -> None:
    rt = [0.0, 0.1, 0.2]

    _normalized, facts = gaussian15_trace_window_evidence(
        rt,
        [0.0, 0.0, 0.0],
        query_apex_rt_min=0.1,
    )

    assert facts.status == "inconclusive"
    assert facts.reason == "non_positive_signal"
    assert facts.query_apex_in_trace_window is True
    assert facts.local_to_global_max_ratio is None


def test_trace_window_evidence_rejects_crossed_coordinate_cardinality() -> None:
    normalized, facts = gaussian15_trace_window_evidence(
        [0.0, 0.1, 0.2],
        [0.0, 10.0],
        query_apex_rt_min=0.1,
    )

    assert normalized.status == "inconclusive"
    assert normalized.reason == "length_mismatch"
    assert facts.status == "inconclusive"
    assert facts.reason == "length_mismatch"
    assert facts.query_apex_in_trace_window is False


def test_integration_trace_evidence_preserves_compatibility_fields() -> None:
    integration = IntegrationResult(
        rt_left_min=1.0,
        rt_apex_min=2.0,
        rt_right_min=3.0,
        raw_apex_rt_min=2.0,
        rt_width_min=2.0,
        height_raw=50.0,
        height_smoothed=45.0,
        area_raw_counts_seconds=4000.0,
        integration_method="raw_trapezoid",
        boundary_sources=("test_trace",),
        raw_scan_indices=(1, 2, 3),
    )

    enriched = integration_with_trace_evidence(
        integration,
        [0.0, 1.0, 2.0, 3.0, 4.0],
        [10.0, 12.0, 50.0, 12.0, 10.0],
    )

    assert enriched.area_raw_counts_seconds == 4000.0
    assert enriched.integration_method == "raw_trapezoid"
    assert enriched.boundary_sources == ("test_trace",)
    assert enriched.raw_scan_indices == (1, 2, 3)
    assert enriched.baseline_type == "asls"
    assert enriched.area_baseline_corrected is not None
    assert enriched.area_ms1_morphology is not None
    assert enriched.ms1_morphology_trace_method == "gaussian_15"


def test_integration_trace_evidence_records_exact_selected_scan_indices() -> None:
    integration = IntegrationResult(
        rt_left_min=1.0,
        rt_apex_min=2.0,
        rt_right_min=3.0,
        raw_apex_rt_min=2.0,
        rt_width_min=2.0,
        height_raw=50.0,
        height_smoothed=45.0,
        area_raw_counts_seconds=4000.0,
        integration_method="raw_trapezoid",
        boundary_sources=("test_trace",),
    )

    enriched = integration_with_trace_evidence(
        integration,
        [0.0, 1.0, 2.0, 3.0, 4.0],
        [10.0, 12.0, 50.0, 12.0, 10.0],
    )

    assert enriched.raw_scan_indices == (1, 2, 3)


def test_integration_only_evidence_skips_shape_projection(monkeypatch) -> None:
    integration = IntegrationResult(
        rt_left_min=1.0,
        rt_apex_min=2.0,
        rt_right_min=3.0,
        raw_apex_rt_min=2.0,
        rt_width_min=2.0,
        height_raw=50.0,
        height_smoothed=45.0,
        area_raw_counts_seconds=4000.0,
        boundary_sources=("alignment_owner",),
    )
    projection_calls: list[str] = []
    smoothing_calls = 0
    original_normalize = evidence_facts_module._normalized_precomputed_trace
    original_window = evidence_facts_module._trace_window_evidence_facts
    original_smoothing = evidence_facts_module.gaussian15_positive_asls_residual_trace

    def counted_normalize(*args, **kwargs):
        projection_calls.append("normalized_shape")
        return original_normalize(*args, **kwargs)

    def counted_window(*args, **kwargs):
        projection_calls.append("window")
        return original_window(*args, **kwargs)

    def counted_smoothing(*args, **kwargs):
        nonlocal smoothing_calls
        smoothing_calls += 1
        return original_smoothing(*args, **kwargs)

    monkeypatch.setattr(
        evidence_facts_module,
        "_normalized_precomputed_trace",
        counted_normalize,
    )
    monkeypatch.setattr(
        evidence_facts_module,
        "_trace_window_evidence_facts",
        counted_window,
    )
    monkeypatch.setattr(
        evidence_facts_module,
        "gaussian15_positive_asls_residual_trace",
        counted_smoothing,
    )

    enriched = integration_with_trace_evidence(
        integration,
        [0.0, 1.0, 2.0, 3.0, 4.0],
        [10.0, 12.0, 50.0, 12.0, 10.0],
    )

    assert projection_calls == []
    assert smoothing_calls == 1
    assert enriched.area_raw_counts_seconds == integration.area_raw_counts_seconds
    assert enriched.boundary_sources == integration.boundary_sources
    assert enriched.area_ms1_morphology is not None


def test_peak_trace_evidence_reuses_one_gaussian15_trace_for_shape_and_area(
    monkeypatch,
) -> None:
    integration = IntegrationResult(
        rt_left_min=1.0,
        rt_apex_min=2.0,
        rt_right_min=3.0,
        raw_apex_rt_min=2.0,
        rt_width_min=2.0,
        height_raw=50.0,
        height_smoothed=45.0,
        area_raw_counts_seconds=4000.0,
        boundary_sources=("owner_backfill_batch",),
    )
    rt = np.asarray([0.0, 1.0, 2.0, 3.0, 4.0])
    intensity = np.asarray([10.0, 12.0, 50.0, 12.0, 10.0])
    baseline = evidence_facts_module.asls_baseline(intensity)
    expected_morphology = gaussian15_positive_asls_residual_metrics(
        rt,
        intensity,
        baseline,
        1,
        4,
    )
    smoothing_calls = 0
    original = evidence_facts_module.gaussian15_positive_asls_residual_trace

    def counted_trace(*args, **kwargs):
        nonlocal smoothing_calls
        smoothing_calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(
        evidence_facts_module,
        "gaussian15_positive_asls_residual_trace",
        counted_trace,
    )

    facts = peak_trace_evidence(integration, rt, intensity)

    assert smoothing_calls == 1
    assert facts.smoothing_count == 1
    assert facts.integration_count == 1
    assert (
        facts.integration.area_ms1_morphology
        == expected_morphology.area_positive_asls_residual
    )
    assert facts.integration.area_raw_counts_seconds == 4000.0
    assert facts.integration.boundary_sources == ("owner_backfill_batch",)
    assert facts.normalized_shape_trace.is_usable
    assert facts.window.status == "ok"
    assert facts.window.query_apex_rt_min == 2.0


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
    prepared_result = prepare_physical_signal(rt, raw)
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
    assert required_facts[:3] == (48, 41, 56)
    assert required_facts[3] == pytest.approx(
        1062.1744946180106,
        rel=0.0,
        abs=1e-12,
    )
    assert residual_first_facts[:3] == (4, 0, 33)
    assert residual_first_facts[3] == pytest.approx(
        2245.7044316702472,
        rel=0.0,
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
        rel=0.0,
        abs=1e-12,
    )


def test_physical_question_containment_is_closed_complete_and_owner_derived() -> None:
    profile = SignalProcessingProfile(window_points=3)
    rt = np.linspace(0.0, 3.0, 31)
    raw = np.full(31, 10.0)
    raw[7:10] += [30.0, 100.0, 30.0]
    raw[21:24] += [25.0, 80.0, 25.0]
    prepared = prepare_physical_signal(rt, raw, profile=profile)
    universe = enumerate_physical_segment_universe(prepared)
    assert len(universe.segment_ids) == 2

    zero = mint_physical_peak_question(
        prepared.projection,
        universe,
        selection_subject_id="zero",
        query_rt_min=1.5,
    )
    one = mint_physical_peak_question(
        prepared.projection,
        universe,
        selection_subject_id="one",
        query_rt_min=0.8,
    )
    assert zero.admissible_segment_ids == ()
    assert one.admissible_segment_ids == (universe.segment_ids[0],)
    assert answer_physical_peak_question(prepared, universe, zero).reason == (
        "no_selection"
    )
    assert answer_physical_peak_question(prepared, universe, one).state == "available"

    overlap_rt = np.linspace(0.0, 3.0, 15)
    overlap_raw = np.zeros(15)
    overlap_raw[6] = 100.0
    # The two Gaussian3 supports return to a shared observed valley. A
    # shallower unresolved shoulder is one envelope, not two memberships.
    overlap_raw[10] = 60.0
    overlap_prepared = prepare_physical_signal(
        overlap_rt,
        overlap_raw,
        profile=profile,
    )
    overlap_universe = enumerate_physical_segment_universe(overlap_prepared)
    assert len(overlap_universe.segment_ids) == 2
    shared_boundary = overlap_universe.segments[0].right_rt_min
    assert shared_boundary == overlap_universe.segments[1].left_rt_min
    multiple = mint_physical_peak_question(
        overlap_prepared.projection,
        overlap_universe,
        selection_subject_id="multiple",
        query_rt_min=shared_boundary,
    )
    assert multiple.admissible_segment_ids == overlap_universe.segment_ids
    multiple_answer = answer_physical_peak_question(
        overlap_prepared,
        overlap_universe,
        multiple,
    )
    assert (multiple_answer.state, multiple_answer.reason) == (
        "unavailable",
        "ambiguous_segment_candidates",
    )

    adversarial = (
        (
            overlap_universe,
            _reseal_question(
                multiple,
                admissible_segment_ids=(overlap_universe.segment_ids[0],),
            ),
        ),
        (
            universe,
            _reseal_question(
                one,
                admissible_segment_ids=(universe.segment_ids[1],),
            ),
        ),
        (
            overlap_universe,
            _reseal_question(
                multiple,
                admissible_segment_ids=tuple(reversed(multiple.admissible_segment_ids)),
            ),
        ),
    )
    for owner_universe, forged_question in adversarial:
        with pytest.raises(ValueError, match="owner-admissible"):
            validate_physical_peak_question_identity(
                forged_question,
                universe=owner_universe,
            )


def test_physical_owner_mints_closed_unique_admissible_bounded_facts() -> None:
    rt = np.linspace(0.0, 3.0, 31)
    raw = np.full(31, 10.0)
    raw[7:10] += [30.0, 100.0, 30.0]
    raw[21:24] += [25.0, 80.0, 25.0]
    profile = SignalProcessingProfile(window_points=3)
    trace_id = mint_physical_trace_content_id(rt, raw)
    prepared = prepare_physical_signal(
        rt,
        raw,
        trace_content_id=trace_id,
        profile=profile,
    )
    evaluation = evaluate_physical_peak(
        prepared,
        selection_subject_id="opaque-owner-backfill-work:1",
        query_rt_min=0.8,
        request_context_rt_start_min=0.7,
        request_context_rt_end_min=0.9,
    )
    universe = evaluation.segment_universe

    assert len(universe.segments) == 2
    assert universe.context_rt_start_min == 0.0
    assert universe.context_rt_end_min == 3.0
    assert evaluation.question.admissible_segment_ids == (universe.segment_ids[0],)
    assert evaluation.state == "available"
    assert evaluation.facts is not None
    assert {
        evaluation.prepared.prepared_signal_id,
        universe.prepared_signal_id,
        evaluation.question.prepared_signal_id,
        evaluation.answer.prepared_signal_id,
        evaluation.facts.prepared_signal_id,
    } == {evaluation.prepared.prepared_signal_id}
    assert evaluation.facts.physical_segment_id == universe.segment_ids[0]
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
        "normalization",
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


def test_physical_trace_identity_normalizes_negative_zero() -> None:
    assert mint_physical_trace_content_id([-0.0], [-0.0]) == (
        mint_physical_trace_content_id([0.0], [0.0])
    )


def test_physical_owner_excludes_noncontaining_segments_from_admissibility() -> None:
    rt = np.linspace(0.0, 3.0, 31)
    raw = np.full(31, 10.0)
    raw[7:10] += [30.0, 100.0, 30.0]
    raw[21:24] += [25.0, 80.0, 25.0]
    prepared = prepare_physical_signal(
        rt,
        raw,
        profile=SignalProcessingProfile(window_points=3),
    )
    evaluation = evaluate_physical_peak(
        prepared,
        selection_subject_id="opaque-subject",
        query_rt_min=0.8,
    )
    universe = evaluation.segment_universe

    assert len(universe.segment_ids) == 2
    assert evaluation.question.admissible_segment_ids == (universe.segment_ids[0],)
    assert evaluation.state == "available"


def test_short_trace_is_unavailable_without_smoothing_or_silent_shrink() -> None:
    prepared = prepare_physical_signal(
        [3.0],
        [10.0],
        profile=SignalProcessingProfile(window_points=15),
    )
    evaluation = evaluate_physical_peak(
        prepared,
        selection_subject_id="opaque-short-trace",
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
        selection_subject_id="opaque-zero-trace",
        query_rt_min=1.5,
        request_context_rt_start_min=1.0,
        request_context_rt_end_min=2.0,
    )

    assert evaluation.state == "unavailable"
    assert evaluation.reason == "no_signal_above_baseline"
    assert evaluation.segment_universe.context_rt_start_min == 1.0
    assert evaluation.segment_universe.context_rt_end_min == 2.0
    assert evaluation.question.selection_subject_id == "opaque-zero-trace"
    assert (
        evaluation.answer.physical_question_id
        == evaluation.question.physical_question_id
    )
    assert evaluation.operations == ("trace_evaluation",)


def test_malformed_trace_has_failed_terminal_question_and_rehydrates() -> None:
    rt = [float("nan"), 1.0, 2.0]
    raw = [1.0, 2.0, 1.0]
    profile = SignalProcessingProfile(window_points=3)
    prepared = prepare_physical_signal(rt, raw, profile=profile)

    evaluation = evaluate_physical_peak(
        prepared,
        selection_subject_id="opaque-malformed-trace",
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
    assert (
        rehydrate_physical_peak_evaluation(
            rt,
            raw,
            profile=profile,
            expected=evaluation,
            request_context_rt_start_min=1.0,
            request_context_rt_end_min=2.0,
        )
        == evaluation
    )


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
        dirty_matrix=False,
        ms2_trace_strength="strong",
        ms2_alignment_source="region",
        trigger_scan_count=3,
        strict_nl_scan_count=2,
        baseline_array=np.asarray([80.0, 90.0, 95.0, 90.0, 85.0]),
        residual_mad=5.0,
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
        dirty_matrix=False,
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
        baseline_array=np.asarray([80.0, 90.0, 95.0, 90.0, 85.0]),
        residual_mad=5.0,
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
        dirty_matrix=False,
        ms2_trace_strength="moderate",
        baseline_array=np.asarray([80.0, 90.0, 95.0, 90.0, 85.0]),
        residual_mad=5.0,
    )
    facts = build_candidate_evidence_facts(candidate, ctx, role="ISTD", istd_pair="")

    semantics = decision_semantics_from_candidate_facts(facts)

    assert semantics.decision_class == "accepted"
    assert "ms1_coherent" in semantics.support_reasons
    assert "candidate_aligned_ms2_nl" in semantics.support_reasons
    assert projected_confidence_from_candidate_facts(facts, semantics) == "HIGH"
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
        dirty_matrix=False,
        baseline_array=np.asarray([80.0, 90.0, 95.0, 90.0, 85.0]),
        residual_mad=5.0,
    )

    scored = score_candidate(
        candidate,
        ctx,
        prior_rt=ctx.rt_prior,
        istd_confidence_note="paired ISTD confirmed",
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
    assert scored.confidence.value == "VERY_LOW"
    assert "missing_ms2_policy_not_counted" in scored.reason
    assert scored.reason.endswith("; paired ISTD confirmed")

    summary = candidate_score_summary(scored)
    assert summary.evidence_facts is not None
    assert summary.evidence_facts.rt.paired_istd_delta_min == 0.7
    assert summary.evidence_facts.rt.role == "Analyte"
    assert summary.evidence_facts.chemical.ms2_present is False
    assert summary.evidence_facts.area == candidate.peak.area
    assert summary.confidence == scored.confidence.value
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
        dirty_matrix=False,
        baseline_array=np.asarray([80.0, 90.0, 95.0, 90.0, 85.0]),
        residual_mad=5.0,
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
    assert not {
        "paired_istd_rt_close",
        "paired_istd_rt_close",
    }.intersection(semantics.compatibility_labels)


def test_nl_fail_with_paired_istd_rt_mismatch_is_not_counted() -> None:
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
        dirty_matrix=False,
        baseline_array=np.asarray([80.0, 90.0, 95.0, 90.0, 85.0]),
        residual_mad=5.0,
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
    assert semantics.decision_class == "not_counted"
    assert "anchor_conflict" in semantics.conflict_reasons
    assert "paired_istd_rt_mismatch_policy" in semantics.not_counted_reasons
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


def _reseal_question(
    question: PhysicalPeakQuestion,
    *,
    admissible_segment_ids: tuple[str, ...],
) -> PhysicalPeakQuestion:
    mutated = replace(
        question,
        admissible_segment_ids=admissible_segment_ids,
        physical_question_id="",
    )
    question_id = (
        "physical-question:"
        + hashlib.sha256(canonical_physical_peak_question_bytes(mutated)).hexdigest()
    )
    return replace(mutated, physical_question_id=question_id)


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
