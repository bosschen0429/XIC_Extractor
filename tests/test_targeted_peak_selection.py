import inspect
from dataclasses import replace

import numpy as np
import pytest

from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.evidence.neutral_loss import CandidateMS2Evidence
from xic_extractor.extraction import targeted_peak_selection
from xic_extractor.extraction.trace_context import targeted_extraction_trace_group
from xic_extractor.ms2_scan_models import Ms2Window
from xic_extractor.peak_detection.models import (
    PeakCandidate,
    PeakCandidateScore,
    PeakDetectionResult,
    PeakResult,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    enumerate_physical_segment_universe,
    prepare_physical_signal,
)
from xic_extractor.peak_detection.traces import Trace, targeted_trace_group


@pytest.mark.parametrize("local_peaks", [(), (8.5,), (7.505,), (8.5, 9.2)])
def test_paired_selection_constrains_qualified_population_and_holds_ambiguity(
    tmp_path, local_peaks
):
    config = _config(tmp_path)
    target = replace(_target(), cid_nl_tag="dR")
    rt = np.arange(5.0, 11.0, 0.005)
    intensity = 100.0 + 10000.0 * np.exp(-0.5 * ((rt - 6.0) / 0.04) ** 2)
    for apex in local_peaks:
        intensity += 2000.0 * np.exp(-0.5 * ((rt - apex) / 0.04) ** 2)
    prepared = prepare_physical_signal(rt, intensity)
    assessment = targeted_peak_selection.assess_targeted_candidate_population(
        target=target,
        config=config,
        trace_group=targeted_extraction_trace_group(
            sample_name="sample",
            target=target,
            config=config,
            rt=rt,
            intensity=intensity,
            rt_min=5.0,
            rt_max=11.0,
            expected_rt_min=8.5,
        ),
        prepared_result=prepared,
        universe=enumerate_physical_segment_universe(prepared),
        selection_window=(5.0, 11.0),
        preferred_rt=8.5,
        strict_preferred_rt=False,
        method_window=Ms2Window("sample", 5.0, 11.0, ()),
    )
    ranked = assessment.rank_candidates(
        scoring_context_builder=None,
        istd_confidence_note=None,
        paired_istd_anchor_rt=8.5,
    )
    assert len(ranked.candidates) == len(local_peaks)
    assert all(abs(c.selection_apex_rt - 8.5) <= 1.0 for c in ranked.candidates)
    result = assessment.complete_result(ranked, scoring_context_builder=None)
    if not local_peaks:
        assert result.reported_peak_area is None
    elif len(local_peaks) == 1:
        assert result.reported_rt == pytest.approx(local_peaks[0])
        if local_peaks[0] == 7.505:
            # The pair window admits an apex; it must not clip the peak's area.
            assert result.peak.peak_start < 7.5
        assert (
            "paired_istd_multiple_candidates"
            not in result.targeted_product_projection.conflict_reasons
        )
    else:
        assert result.targeted_product_projection.counted_detection is False
        assert (
            "paired_istd_multiple_candidates"
            in result.targeted_product_projection.conflict_reasons
        )


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
        candidate_ms2_evidence={selected: _ms2_evidence()},
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
            candidate_ms2_evidence={
                candidate: CandidateMS2Evidence(
                    ms2_present=False,
                    nl_match=False,
                    nl_status="NO_MS2",
                    trigger_scan_count=0,
                    strict_nl_scan_count=0,
                    best_loss_ppm=None,
                    best_scan_rt=None,
                    best_product_base_ratio=None,
                    alignment_source="none",
                )
            },
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


@pytest.mark.parametrize(
    "sample_name,shift,scale,selected_index",
    [("SampleA_RNA", 0.0, 1.0, 0), ("SampleB_RNA", 0.2, 1.7, 1)],
)
@pytest.mark.parametrize("reuse_evaluation", [False, True])
def test_handoff_preserves_the_selected_candidate_through_workbook(
    tmp_path, monkeypatch, sample_name, shift, scale, selected_index, reuse_evaluation
) -> None:
    from xic_extractor.configuration.signal_processing_profile import (
        resolve_signal_processing_profile,
    )
    from xic_extractor.extraction.result_assembly import build_extraction_result
    from xic_extractor.peak_detection.physical_peak_candidate_adapter import (
        answer_selected_physical_candidate,
        physical_candidates_result,
    )
    from xic_extractor.peak_detection.physical_peak_evaluation import (
        enumerate_physical_segment_universe,
        prepare_physical_signal,
    )

    config = _config(tmp_path)
    target = replace(
        _target(sample_applicability="rna_containing"), rt_min=7.0, rt_max=10.5
    )
    rt = np.linspace(7.0, 10.5, 701)
    intensity = 30.0 + scale * (
        1000.0 * np.exp(-0.5 * ((rt - (8.0 + shift)) / 0.08) ** 2)
        + 3000.0 * np.exp(-0.5 * ((rt - (9.0 + shift)) / 0.09) ** 2)
    )
    prepared = prepare_physical_signal(
        rt, intensity, profile=resolve_signal_processing_profile()
    )
    universe = enumerate_physical_segment_universe(
        prepared, request_context_rt_start_min=7.0, request_context_rt_end_min=10.5
    )
    candidates = physical_candidates_result(prepared, universe).candidates
    assert len(candidates) == 2
    # Audit annotations cannot rebind a candidate to a different physical peak.
    candidates = tuple(
        replace(candidate, proposal_sources=("physical_segment:unrelated_audit",))
        for candidate in candidates
    )
    first, second = candidates
    selected = candidates[selected_index]
    evaluations = None
    if reuse_evaluation:
        evaluation = answer_selected_physical_candidate(
            prepared,
            universe,
            selected,
            acquisition_rt_window=(7.0, 10.5),
            selection_rt_window=(7.0, 10.5),
        )
        evaluations = {selected: evaluation}

        def unexpected_question(*args, **kwargs):
            pytest.fail("selection must reuse its completed candidate answer")

        monkeypatch.setattr(
            "xic_extractor.peak_detection.physical_peak_candidate_adapter."
            "mint_physical_peak_question",
            unexpected_question,
        )
        monkeypatch.setattr(
            "xic_extractor.peak_detection.physical_peak_candidate_adapter."
            "answer_physical_peak_question",
            unexpected_question,
        )
    first_ms2 = replace(_ms2_evidence(), best_scan_rt=first.peak.rt)
    second_ms2 = replace(_ms2_evidence(), best_scan_rt=second.peak.rt)
    # The handoff consumes the first-stage choice, even when another candidate
    # has stronger confidence and more strict-NL support labels.
    peak_result = PeakDetectionResult(
        status="OK",
        peak=selected.peak,
        n_points=len(rt),
        max_smoothed=float(intensity.max()),
        n_prominent_peaks=2,
        candidates=candidates,
        confidence="VERY_LOW" if selected_index == 0 else "HIGH",
        reason="decision: candidate selected",
        candidate_scores=(
            _score(
                first,
                confidence="VERY_LOW",
                raw_score=20,
                support_labels=("strict_nl_ok",),
                concern_labels=("rt_centrality_poor", "shape_poor"),
            ),
            _score(
                second,
                confidence="HIGH",
                raw_score=120,
                support_labels=("strict_nl_ok", "candidate_aligned_ms2_nl"),
                concern_labels=(),
            ),
        ),
    )
    trace_group = targeted_extraction_trace_group(
        config=config,
        sample_name=sample_name,
        target=target,
        rt=rt,
        intensity=intensity,
        rt_min=7.0,
        rt_max=10.5,
        expected_rt_min=selected.peak.rt,
    )
    handoff = targeted_peak_selection.select_targeted_peak(
        config=config,
        target=target,
        peak_result=peak_result,
        trace_group=trace_group,
        candidate_ms2_evidence={first: first_ms2, second: second_ms2},
        prepared_result=prepared,
        universe=universe,
        selection_rt_min=7.0,
        selection_rt_max=10.5,
        candidate_evaluations=evaluations,
    )
    if reuse_evaluation:
        assert handoff.physical_evaluation == evaluation
    assert handoff.selected_hypothesis is not None
    assert handoff.selected_hypothesis.audit.selected is True
    assert handoff.selected_hypothesis.trace_group_id == trace_group.trace_group_id
    assert handoff.trace_group == trace_group
    assert handoff.selection_decision is not None
    assert (
        handoff.selection_decision.selected_candidate_id
        == handoff.selected_hypothesis.hypothesis_id
    )
    assert handoff.candidate_ms2_evidence == (first_ms2, second_ms2)[selected_index]
    assert handoff.peak_result.confidence == peak_result.confidence
    assert f"{selected.peak.rt:.5f}" in handoff.selected_hypothesis.hypothesis_id
    assert handoff.selected_hypothesis.integration.rt_apex_min == pytest.approx(
        selected.peak.rt
    )
    assert handoff.peak_result.peak == selected.peak
    assert handoff.selected_hypothesis.integration.area_ms1_morphology == pytest.approx(
        selected.peak.area
    )
    result = build_extraction_result(
        peak_result=handoff.peak_result,
        nl_result=None,
        candidate_ms2_evidence=handoff.candidate_ms2_evidence,
        target=target,
        candidate=selected,
        scoring_context_builder=None,
        selected_hypothesis=handoff.selected_hypothesis,
        selection_decision=handoff.selection_decision,
        physical_evaluation=handoff.physical_evaluation,
        acquisition_trace=trace_group.primary_trace,
        sample_name=sample_name,
    )
    assert result.reported_rt == pytest.approx(selected.peak.rt)
    assert result.reported_peak_area == pytest.approx(selected.peak.area)
    assert result.reported_peak_start == pytest.approx(selected.peak.peak_start)
    assert result.reported_peak_end == pytest.approx(selected.peak.peak_end)

    from openpyxl import load_workbook

    from xic_extractor.extraction.result_models import FileResult, RunOutput
    from xic_extractor.output.excel_pipeline import write_excel_from_run_output

    output_path = tmp_path / "second.xlsx"
    write_excel_from_run_output(
        config,
        [target],
        RunOutput([FileResult(sample_name, {target.label: result})], []),
        output_path=output_path,
    )
    workbook = load_workbook(output_path, read_only=True, data_only=True)
    try:
        rows = workbook["XIC Results"].iter_rows(values_only=True)
        row = dict(zip(next(rows), next(rows), strict=True))
    finally:
        workbook.close()
    for field, value, digits in (
        ("RT", selected.peak.rt, 4),
        ("Area", selected.peak.area, 2),
        ("PeakStart", selected.peak.peak_start, 4),
        ("PeakEnd", selected.peak.peak_end, 4),
    ):
        assert row[field] == float(f"{value:.{digits}f}")


@pytest.mark.parametrize("has_signal", [False, True])
def test_no_selected_candidate_preserves_window_and_unavailability(has_signal) -> None:
    from xic_extractor.peak_detection.physical_peak_candidate_adapter import (
        answer_selected_physical_candidate,
    )
    from xic_extractor.peak_detection.physical_peak_evaluation import (
        enumerate_physical_segment_universe,
        prepare_physical_signal,
    )

    rt = np.linspace(7.0, 10.5, 701)
    intensity = (
        30.0 + 1000.0 * np.exp(-0.5 * ((rt - 8.0) / 0.08) ** 2)
        if has_signal
        else np.zeros_like(rt)
    )
    prepared = prepare_physical_signal(rt, intensity)
    universe = enumerate_physical_segment_universe(prepared)
    evaluation = answer_selected_physical_candidate(
        prepared,
        universe,
        None,
        acquisition_rt_window=(7.0, 10.5),
        selection_rt_window=(9.5, 10.5),
    )

    assert evaluation.question.query_rt_min == (10.0 if has_signal else 8.75)
    assert evaluation.question.admissible_segments == ()
    assert evaluation.state == "unavailable"
    assert evaluation.reason == (
        "no_selection" if has_signal else "no_signal_above_baseline"
    )
    assert evaluation.integration is None
    assert evaluation.answer.selected_segment is None


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
