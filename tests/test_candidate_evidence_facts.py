import numpy as np
import pytest

import xic_extractor.peak_detection.evidence_facts as evidence_facts_module
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
    gaussian15_positive_asls_residual_metrics,
)
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
        prefer_rt_prior_tiebreak=True,
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

    assert facts.facts_version == "candidate_evidence_facts_v1"
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
    assert facts.rt.prefer_rt_prior_tiebreak is True
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

    facts = build_candidate_evidence_facts(
        candidate,
        ctx,
        role="Analyte",
        istd_pair="ISTD",
        paired_istd_anchor_rt_min=7.8,
    )

    assert facts.rt.paired_istd_delta_min == 0.7
    assert facts.rt.paired_istd_status == "close"


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
        prefer_rt_prior_tiebreak=True,
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
        "paired_istd_aligned",
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
