import inspect
from dataclasses import replace

import numpy as np
import pytest

from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.evidence.neutral_loss import CandidateMS2Evidence
from xic_extractor.extraction import targeted_peak_selection
from xic_extractor.extraction.trace_context import targeted_extraction_trace_group
from xic_extractor.peak_detection.models import (
    PeakCandidate,
    PeakCandidateScore,
    PeakDetectionResult,
    PeakResult,
)
from xic_extractor.peak_detection.traces import Trace, targeted_trace_group


def test_build_production_peak_hypotheses_maps_selected_ms2_only(tmp_path) -> None:
    target = _target()
    rt = np.asarray([8.3, 8.5, 8.7, 8.9, 9.1])
    intensity = np.asarray([10.0, 100.0, 20.0, 80.0, 10.0])
    trace_group = targeted_trace_group(
        Trace.from_arrays(
            sample_name="SampleA",
            mz=target.mz,
            rt=rt,
            intensity=intensity,
            rt_min=8.0,
            rt_max=9.2,
            ppm_tol=target.ppm_tol,
            source="unit_test",
        ),
        target_label=target.label,
        resolver_mode="local_minimum",
        role="Analyte",
        istd_pair=target.istd_pair,
    )
    selected = _candidate(8.50)
    rejected = _candidate(8.90)
    peak_result = PeakDetectionResult(
        status="OK",
        peak=selected.peak,
        n_points=11,
        max_smoothed=1200.0,
        n_prominent_peaks=2,
        candidates=(selected, rejected),
        candidate_scores=(
            _score(selected, confidence="HIGH"),
            _score(rejected, confidence="LOW"),
        ),
    )
    hypotheses = targeted_peak_selection.build_production_peak_hypotheses(
        config=_config(tmp_path),
        sample_name="SampleA",
        target=target,
        peak_result=peak_result,
        selected_candidate_ms2_evidence=_ms2_evidence(),
        rt=rt,
        intensity=intensity,
        trace_group=trace_group,
    )

    selected_hypothesis = targeted_peak_selection.selected_peak_hypothesis(hypotheses)
    rejected_hypothesis = next(
        hypothesis for hypothesis in hypotheses if not hypothesis.audit.selected
    )

    assert selected_hypothesis is not None
    assert selected_hypothesis.trace_group_id == trace_group.trace_group_id
    assert selected_hypothesis.integration.raw_scan_indices != ()
    assert selected_hypothesis.evidence.nl_status == "OK"
    assert rejected_hypothesis.evidence.nl_status == ""


def test_selected_peak_hypothesis_returns_none_without_selected_candidate(
    tmp_path,
) -> None:
    hypotheses = targeted_peak_selection.build_production_peak_hypotheses(
        config=_config(tmp_path),
        sample_name="SampleA",
        target=_target(),
        peak_result=PeakDetectionResult(
            status="PEAK_NOT_FOUND",
            peak=None,
            n_points=5,
            max_smoothed=10.0,
            n_prominent_peaks=0,
            candidates=(),
        ),
    )

    assert hypotheses == ()
    assert targeted_peak_selection.selected_peak_hypothesis(hypotheses) is None


def test_build_production_peak_hypotheses_passes_no_ms2_detection_policy(
    tmp_path,
) -> None:
    candidate = _candidate(8.50)
    peak_result = PeakDetectionResult(
        status="OK",
        peak=candidate.peak,
        n_points=11,
        max_smoothed=1200.0,
        n_prominent_peaks=1,
        candidates=(candidate,),
        candidate_scores=(
            _score(
                candidate,
                confidence="LOW",
                concern_labels=("no_ms2",),
                cap_labels=("no_ms2_cap",),
            ),
        ),
    )

    hypothesis = targeted_peak_selection.selected_peak_hypothesis(
        targeted_peak_selection.build_production_peak_hypotheses(
            config=replace(_config(tmp_path), count_no_ms2_as_detected=True),
            sample_name="SampleA",
            target=_target(),
            peak_result=peak_result,
            selected_candidate_ms2_evidence=CandidateMS2Evidence(
                ms2_present=False,
                nl_match=False,
                nl_status="NO_MS2",
                trigger_scan_count=0,
                strict_nl_scan_count=0,
                best_loss_ppm=None,
                best_scan_rt=None,
                best_product_base_ratio=None,
                alignment_source="none",
            ),
        )
    )

    assert hypothesis is not None
    assert hypothesis.evidence.decision_semantics is not None
    assert hypothesis.evidence.decision_semantics.decision_class == "review"
    assert hypothesis.evidence.decision_semantics.review_reasons == (
        "missing_ms2_not_observed",
    )
    assert hypothesis.evidence.decision_semantics.not_counted_reasons == ()


def test_selected_hypothesis_uses_final_peak_result_confidence_when_score_is_stale(
    tmp_path,
) -> None:
    candidate = _candidate(8.50)
    peak_result = PeakDetectionResult(
        status="OK",
        peak=candidate.peak,
        n_points=11,
        max_smoothed=1200.0,
        n_prominent_peaks=1,
        candidates=(candidate,),
        confidence="VERY_LOW",
        reason="decision: review only, not counted; cap: VERY_LOW",
        candidate_scores=(_score(candidate, confidence="HIGH"),),
    )

    selected = targeted_peak_selection.selected_peak_hypothesis(
        targeted_peak_selection.build_production_peak_hypotheses(
            config=_config(tmp_path),
            sample_name="SampleA",
            target=_target(),
            peak_result=peak_result,
        )
    )

    assert selected is not None
    assert selected.evidence.confidence == "VERY_LOW"
    assert selected.evidence.reason == (
        "decision: review only, not counted; cap: VERY_LOW"
    )


def test_hypothesis_policy_uses_model_selection_gate_for_selected_hypothesis(
    tmp_path,
) -> None:
    candidate = _candidate(8.50)
    peak_result = PeakDetectionResult(
        status="OK",
        peak=candidate.peak,
        n_points=11,
        max_smoothed=1200.0,
        n_prominent_peaks=1,
        candidates=(candidate,),
        confidence="HIGH",
        reason="decision: accepted",
        candidate_scores=(_score(candidate, confidence="HIGH"),),
    )

    handoff = targeted_peak_selection._select_targeted_hypothesis(
        config=_config(tmp_path),
        target=_target(),
        peak_result=peak_result,
        candidate_ms2_evidence=None,
        trace_group=targeted_extraction_trace_group(
            config=_config(tmp_path),
            sample_name="SampleA",
            target=_target(),
            rt=np.asarray([8.3, 8.5, 8.7]),
            intensity=np.asarray([10.0, 100.0, 20.0]),
            rt_min=8.0,
            rt_max=9.0,
            expected_rt_min=8.5,
        ),
    )

    assert handoff.model_selection_result is not None
    assert handoff.selected_hypothesis is not None
    assert handoff.selection_decision is not None
    assert handoff.model_selection_result.selection_status == "parity"
    assert handoff.model_selection_result.product_switch_allowed is True
    assert (
        "successor_selection_not_adopted"
        not in handoff.model_selection_result.diff_reasons
    )
    assert (
        handoff.selected_hypothesis.hypothesis_id
        == handoff.model_selection_result.selected_candidate_id
    )
    assert (
        handoff.selection_decision.selected_candidate_id
        == handoff.model_selection_result.selected_candidate_id
    )


def test_hypothesis_policy_keeps_trace_group_without_candidate_table_output(
    tmp_path,
) -> None:
    candidate = _candidate(8.50)
    peak_result = PeakDetectionResult(
        status="OK",
        peak=candidate.peak,
        n_points=11,
        max_smoothed=1200.0,
        n_prominent_peaks=1,
        candidates=(candidate,),
        confidence="HIGH",
        reason="decision: accepted",
        candidate_scores=(_score(candidate, confidence="HIGH"),),
    )
    rt = np.asarray([8.3, 8.5, 8.7, 9.2])
    trace_group = targeted_extraction_trace_group(
        config=_config(tmp_path),
        sample_name="SampleA",
        target=_target(),
        rt=rt,
        intensity=np.asarray([10.0, 100.0, 20.0, 15.0]),
        rt_min=8.0,
        rt_max=9.0,
        expected_rt_min=8.5,
    )
    handoff = targeted_peak_selection._select_targeted_hypothesis(
        config=_config(tmp_path),
        target=_target(),
        peak_result=peak_result,
        candidate_ms2_evidence=None,
        trace_group=trace_group,
    )

    assert trace_group.primary_trace.rt[-1] == pytest.approx(9.2)
    assert handoff.selected_hypothesis is not None
    assert handoff.selected_hypothesis.trace_group_id == trace_group.trace_group_id


def test_hypothesis_policy_falls_back_on_unapproved_successor_diff(
    tmp_path,
) -> None:
    legacy_selected = _candidate(8.50)
    successor_candidate = _candidate(8.55, area=1400.0)
    peak_result = PeakDetectionResult(
        status="OK",
        peak=legacy_selected.peak,
        n_points=11,
        max_smoothed=1200.0,
        n_prominent_peaks=2,
        candidates=(legacy_selected, successor_candidate),
        confidence="LOW",
        reason="decision: review",
        candidate_scores=(
            _score(legacy_selected, confidence="LOW"),
            _score(successor_candidate, confidence="HIGH"),
        ),
    )

    handoff = targeted_peak_selection._select_targeted_hypothesis(
        config=_config(tmp_path),
        target=_target(),
        peak_result=peak_result,
        candidate_ms2_evidence=None,
        trace_group=targeted_extraction_trace_group(
            config=_config(tmp_path),
            sample_name="SampleA",
            target=_target(),
            rt=np.asarray([8.3, 8.5, 8.55, 8.7]),
            intensity=np.asarray([10.0, 80.0, 100.0, 20.0]),
            rt_min=8.0,
            rt_max=9.0,
            expected_rt_min=8.5,
        ),
    )

    assert handoff.model_selection_result is not None
    assert handoff.selected_hypothesis is not None
    assert handoff.selection_decision is not None
    assert handoff.model_selection_result.selection_status == "expected_diff"
    assert handoff.model_selection_result.product_switch_allowed is False
    assert handoff.selected_hypothesis.audit.selected is True
    assert (
        handoff.selected_hypothesis.hypothesis_id
        == handoff.model_selection_result.legacy_selected_candidate_id
    )
    assert (
        handoff.model_selection_result.selected_candidate_id
        != handoff.selected_hypothesis.hypothesis_id
    )
    assert (
        handoff.selection_decision.selected_candidate_id
        == handoff.selected_hypothesis.hypothesis_id
    )


def test_hypothesis_policy_promotes_rna_containing_strict_nl_successor(
    tmp_path,
) -> None:
    legacy_selected = _candidate(12.76, area=49_350.0)
    successor_candidate = _candidate(13.08, area=5_589_246.0)
    peak_result = PeakDetectionResult(
        status="OK",
        peak=legacy_selected.peak,
        n_points=31,
        max_smoothed=5_600_000.0,
        n_prominent_peaks=2,
        candidates=(legacy_selected, successor_candidate),
        confidence="VERY_LOW",
        reason="decision: legacy picked small local artifact",
        candidate_scores=(
            _score(
                legacy_selected,
                confidence="VERY_LOW",
                raw_score=20,
                support_labels=("strict_nl_ok",),
                concern_labels=("rt_centrality_poor", "shape_poor"),
            ),
            _score(
                successor_candidate,
                confidence="HIGH",
                raw_score=120,
                support_labels=(
                    "strict_nl_ok",
                    "candidate_aligned_ms2_nl",
                    "local_sn_strong",
                    "trace_clean",
                ),
                concern_labels=(),
            ),
        ),
    )

    handoff = targeted_peak_selection._select_targeted_hypothesis(
        config=_config(tmp_path),
        target=_target(sample_applicability="rna_containing"),
        peak_result=peak_result,
        candidate_ms2_evidence=CandidateMS2Evidence(
            ms2_present=True,
            nl_match=True,
            nl_status="OK",
            trigger_scan_count=3,
            strict_nl_scan_count=3,
            best_loss_ppm=2.5,
            best_scan_rt=12.76,
            best_product_base_ratio=0.8,
            alignment_source="region",
        ),
        trace_group=targeted_extraction_trace_group(
            config=_config(tmp_path),
            sample_name="TumorBC2304_DNAandRNA",
            target=_target(sample_applicability="rna_containing"),
            rt=np.asarray([12.6, 12.76, 12.9, 13.08, 13.2]),
            intensity=np.asarray([10.0, 49_000.0, 2_000.0, 5_500_000.0, 3_000_000.0]),
            rt_min=12.5,
            rt_max=13.3,
            expected_rt_min=13.0,
        ),
    )

    assert handoff.model_selection_result is not None
    assert handoff.selected_hypothesis is not None
    assert handoff.model_selection_result.selection_status == "expected_diff"
    assert handoff.model_selection_result.product_switch_allowed is True
    assert (
        "successor_selection_not_adopted"
        not in handoff.model_selection_result.diff_reasons
    )
    assert "target_applicable_strict_nl_successor" in (
        handoff.model_selection_result.evidence_sources
    )
    assert handoff.selected_hypothesis.hypothesis_id == (
        handoff.model_selection_result.selected_candidate_id
    )
    assert "13.08000" in handoff.selected_hypothesis.hypothesis_id


def test_build_production_peak_hypotheses_uses_config_baseline_method(
    tmp_path,
) -> None:
    candidate = _candidate(8.50)
    rt = np.asarray([8.3, 8.5, 8.7])
    intensity = np.asarray([10.0, 100.0, 20.0])
    peak_result = PeakDetectionResult(
        status="OK",
        peak=candidate.peak,
        n_points=3,
        max_smoothed=1200.0,
        n_prominent_peaks=1,
        candidates=(candidate,),
    )

    with pytest.raises(ValueError, match="retired; use asls"):
        targeted_peak_selection.build_production_peak_hypotheses(
            config=replace(
                _config(tmp_path),
                baseline_integration_method="linear_edge",
            ),
            sample_name="SampleA",
            target=_target(),
            peak_result=peak_result,
            rt=rt,
            intensity=intensity,
        )


def test_production_handoff_runtime_has_no_cwt_audit_dependency() -> None:
    source = inspect.getsource(targeted_peak_selection)

    assert "add_cwt_proposals_for_audit" not in source
    assert "peak_candidate_table" not in source
    assert "peak_candidate_boundaries" not in source
    assert "peak_candidate_audit" not in source


def _config(tmp_path) -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=tmp_path,
        dll_dir=tmp_path,
        output_csv=tmp_path / "xic_results.csv",
        diagnostics_csv=tmp_path / "diagnostics.csv",
        smooth_window=15,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.1,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
        resolver_mode="local_minimum",
    )


def _target(*, sample_applicability: str = "all") -> Target:
    return Target(
        label="Analyte",
        mz=258.1085,
        rt_min=8.0,
        rt_max=9.0,
        ppm_tol=20.0,
        neutral_loss_da=116.0474,
        nl_ppm_warn=20.0,
        nl_ppm_max=50.0,
        is_istd=False,
        istd_pair="ISTD",
        sample_applicability=sample_applicability,
    )


def _candidate(
    rt: float,
    *,
    area: float = 1234.5,
    quality_flags: tuple[str, ...] = ("trace_continuity_ok",),
) -> PeakCandidate:
    peak = PeakResult(
        rt=rt,
        intensity=1200.0,
        intensity_smoothed=1100.0,
        area=area,
        peak_start=rt - 0.1,
        peak_end=rt + 0.1,
    )
    return PeakCandidate(
        peak=peak,
        selection_apex_rt=rt,
        selection_apex_intensity=1100.0,
        selection_apex_index=1,
        raw_apex_rt=rt,
        raw_apex_intensity=1200.0,
        raw_apex_index=1,
        prominence=700.0,
        quality_flags=quality_flags,
    )


def _score(
    candidate: PeakCandidate,
    *,
    confidence: str,
    raw_score: int | None = None,
    support_labels: tuple[str, ...] | None = None,
    concern_labels: tuple[str, ...] | None = None,
    cap_labels: tuple[str, ...] = (),
) -> PeakCandidateScore:
    return PeakCandidateScore(
        candidate=candidate,
        confidence=confidence,
        reason=f"decision: {confidence.lower()}",
        raw_score=(
            raw_score if raw_score is not None else 90 if confidence == "HIGH" else 40
        ),
        support_labels=(
            support_labels
            if support_labels is not None
            else ("strict_nl_ok",)
            if confidence == "HIGH"
            else ()
        ),
        concern_labels=(
            concern_labels
            if concern_labels is not None
            else ()
            if confidence == "HIGH"
            else ("low_score",)
        ),
        cap_labels=cap_labels,
    )


def _ms2_evidence() -> CandidateMS2Evidence:
    return CandidateMS2Evidence(
        ms2_present=True,
        nl_match=True,
        nl_status="OK",
        trigger_scan_count=1,
        strict_nl_scan_count=1,
        best_loss_ppm=1.2,
        best_scan_rt=8.5,
        best_product_base_ratio=0.4,
        alignment_source="region",
    )
