import numpy as np
import pytest

import xic_extractor.extraction.handoff_spine_runtime as handoff_spine_runtime
import xic_extractor.extraction.target_extraction as target_extraction
from xic_extractor.config import ExtractionConfig, Target
from xic_extractor.extraction.istd_recovery import IstdAnchorRecoveryDecision
from xic_extractor.extraction.ms2_selection import selected_candidate_ms2_evidence
from xic_extractor.extraction.rt_windows import get_rt_window
from xic_extractor.extractor import ExtractionResult
from xic_extractor.neutral_loss import CandidateMS2Evidence, NLResult
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.models import (
    PeakCandidate,
    PeakDetectionResult,
    PeakResult,
)
from xic_extractor.peak_detection.selection_decision import (
    PeakHypothesisSelectionDecision,
)
from xic_extractor.peak_detection.targeted_product_projection import (
    TargetedProductProjection,
)
from xic_extractor.rt_prior_library import LibraryEntry


def test_safe_merge_selected_candidate_builds_missing_ms2_evidence() -> None:
    candidate = _candidate(merge_note="region_first_safe_merge")
    evidence = _evidence("NO_MS2")
    calls: list[PeakCandidate] = []

    def _builder(value: PeakCandidate) -> CandidateMS2Evidence:
        calls.append(value)
        return evidence

    selected = selected_candidate_ms2_evidence(candidate, {}, _builder)

    assert selected is evidence
    assert calls == [candidate]


def test_non_safe_merge_selected_candidate_uses_existing_cache_only() -> None:
    candidate = _candidate()
    calls: list[PeakCandidate] = []

    def _builder(value: PeakCandidate) -> CandidateMS2Evidence:
        calls.append(value)
        return _evidence("NL_FAIL")

    selected = selected_candidate_ms2_evidence(candidate, {}, _builder)

    assert selected is None
    assert calls == []


def test_credible_istd_anchor_rt_uses_selected_ms1_reported_rt() -> None:
    candidate = _candidate(rt=9.2)
    result = ExtractionResult(
        peak_result=_peak_result(candidate),
        nl=None,
        target_label="ISTD",
        role="ISTD",
    )

    assert target_extraction.credible_istd_anchor_rt(result) == 9.2


def test_credible_istd_anchor_rt_rejects_missing_or_hard_quality_peak() -> None:
    flagged = _candidate(rt=9.2, quality_flags=("too_broad",))
    no_peak = PeakDetectionResult(
        status="PEAK_NOT_FOUND",
        peak=None,
        n_points=5,
        max_smoothed=10.0,
        n_prominent_peaks=0,
        candidates=(),
    )

    assert (
        target_extraction.credible_istd_anchor_rt(
            ExtractionResult(peak_result=_peak_result(flagged), nl=None)
        )
        is None
    )
    assert (
        target_extraction.credible_istd_anchor_rt(
            ExtractionResult(peak_result=no_peak, nl=None)
        )
        is None
    )


def test_credible_istd_anchor_rt_rejects_not_counted_projection() -> None:
    candidate = _candidate(rt=9.2)
    result = ExtractionResult(
        peak_result=_peak_result(candidate),
        nl=None,
        target_label="ISTD",
        role="ISTD",
        targeted_product_projection=TargetedProductProjection(
            product_state="not_counted",
            counted_detection=False,
            review_state="review_required",
            projection_reason=(
                "decision: not_counted; not_counted: selected_envelope_boundary_defer"
            ),
        ),
    )

    assert target_extraction.credible_istd_anchor_rt(result) is None


def test_extract_one_target_passes_selected_hypothesis_to_result_assembly(
    tmp_path,
    monkeypatch,
) -> None:
    config = ExtractionConfig(
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
        emit_peak_candidates=True,
        resolver_mode="region_first_safe_merge",
    )
    target = Target(
        label="Analyte",
        mz=258.1085,
        rt_min=8.0,
        rt_max=9.0,
        ppm_tol=20.0,
        neutral_loss_da=None,
        nl_ppm_warn=None,
        nl_ppm_max=None,
        is_istd=False,
        istd_pair="ISTD",
    )
    candidate = _candidate()
    peak_result = PeakDetectionResult(
        status="OK",
        peak=candidate.peak,
        n_points=5,
        max_smoothed=1200.0,
        n_prominent_peaks=1,
        candidates=(candidate,),
    )
    captured: dict[str, object] = {}

    class _Raw:
        def extract_xic(self, *_args, **_kwargs):
            return (
                np.asarray([8.3, 8.4, 8.5, 8.6, 8.7]),
                np.asarray([10.0, 100.0, 40.0, 20.0, 10.0]),
            )

    monkeypatch.setattr(
        "xic_extractor.extractor.find_peak_and_area",
        lambda *_args, **_kwargs: peak_result,
    )
    monkeypatch.setattr(
        target_extraction,
        "check_target_nl",
        lambda *_args, **_kwargs: NLResult("NO_MS2", None, None, 0, 0, 0),
    )
    monkeypatch.setattr(
        target_extraction,
        "recover_istd_anchor_peak_if_needed",
        lambda peak_result, **_kwargs: IstdAnchorRecoveryDecision(peak_result),
    )
    monkeypatch.setattr(
        target_extraction,
        "append_peak_audit_rows",
        lambda **_kwargs: None,
    )

    def _fake_build_extraction_result(**kwargs):
        captured.update(kwargs)
        return ExtractionResult(
            peak_result=kwargs["peak_result"],
            nl=kwargs["nl_result"],
            target_label=kwargs["target"].label,
        )

    monkeypatch.setattr(
        target_extraction,
        "build_extraction_result",
        _fake_build_extraction_result,
    )

    results: dict[str, ExtractionResult] = {}
    diagnostics = []
    target_extraction.extract_one_target(
        _Raw(),
        config,
        "SampleA",
        target,
        reference_rt=None,
        results=results,
        diagnostics=diagnostics,
        istd_rt_in_this_sample=8.55,
    )

    selected = captured["selected_hypothesis"]
    decision = captured["selection_decision"]
    model_selection_result = captured["model_selection_result"]
    assert isinstance(selected, PeakHypothesis)
    assert isinstance(decision, PeakHypothesisSelectionDecision)
    assert selected.audit.selected is True
    assert decision.selected_candidate_id == selected.hypothesis_id
    assert model_selection_result is not None
    assert model_selection_result.selection_status == "parity"
    assert model_selection_result.product_switch_allowed is True
    assert model_selection_result.selected_candidate_id == selected.hypothesis_id
    assert decision.legacy_projection_status == "active_policy_remaining"
    assert selected.target_label == "Analyte"
    assert captured["peak_result"].paired_istd_anchor_rt == 8.55
    assert isinstance(results["Analyte"], ExtractionResult)


def test_extract_one_target_promotes_selected_envelope_boundary_to_result_assembly(
    tmp_path,
    monkeypatch,
) -> None:
    config = ExtractionConfig(
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
        emit_peak_candidates=False,
        resolver_mode="region_first_safe_merge",
    )
    target = Target(
        label="Analyte",
        mz=258.1085,
        rt_min=8.0,
        rt_max=8.8,
        ppm_tol=20.0,
        neutral_loss_da=None,
        nl_ppm_warn=None,
        nl_ppm_max=None,
        is_istd=False,
        istd_pair="ISTD",
    )
    candidate = _candidate(
        rt=8.4,
        peak_start=8.1,
        peak_end=8.7,
    )
    peak_result = PeakDetectionResult(
        status="OK",
        peak=candidate.peak,
        n_points=9,
        max_smoothed=1200.0,
        n_prominent_peaks=1,
        candidates=(candidate,),
    )
    captured: dict[str, object] = {}

    class _Raw:
        def extract_xic(self, *_args, **_kwargs):
            return (
                np.asarray([8.0, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8]),
                np.asarray([10.0, 10.0, 12.0, 20.0, 60.0, 20.0, 12.0, 10.0, 10.0]),
            )

    monkeypatch.setattr(
        "xic_extractor.extractor.find_peak_and_area",
        lambda *_args, **_kwargs: peak_result,
    )
    monkeypatch.setattr(
        target_extraction,
        "check_target_nl",
        lambda *_args, **_kwargs: NLResult("NO_MS2", None, None, 0, 0, 0),
    )
    monkeypatch.setattr(
        target_extraction,
        "recover_istd_anchor_peak_if_needed",
        lambda peak_result, **_kwargs: IstdAnchorRecoveryDecision(peak_result),
    )
    monkeypatch.setattr(
        target_extraction,
        "append_peak_audit_rows",
        lambda **_kwargs: None,
    )

    def _fake_build_extraction_result(**kwargs):
        captured.update(kwargs)
        return ExtractionResult(
            peak_result=kwargs["peak_result"],
            nl=kwargs["nl_result"],
            target_label=kwargs["target"].label,
        )

    monkeypatch.setattr(
        target_extraction,
        "build_extraction_result",
        _fake_build_extraction_result,
    )

    results: dict[str, ExtractionResult] = {}
    diagnostics = []
    target_extraction.extract_one_target(
        _Raw(),
        config,
        "SampleA",
        target,
        reference_rt=None,
        results=results,
        diagnostics=diagnostics,
        istd_rt_in_this_sample=8.4,
    )

    selected = captured["selected_hypothesis"]
    assert isinstance(selected, PeakHypothesis)
    assert selected.integration.integration_method == "chrom_peak_segment_gaussian15"
    assert selected.integration.rt_left_min == pytest.approx(8.2)
    assert selected.integration.rt_right_min == pytest.approx(8.6)
    assert selected.integration.ms1_morphology_area_source == (
        "gaussian15_positive_asls_residual"
    )
    assert "chrom_peak_segment" in selected.integration.boundary_sources


def test_extract_one_target_passes_configured_ms1_window_to_boundary_policies(
    tmp_path,
    monkeypatch,
) -> None:
    config = ExtractionConfig(
        data_dir=tmp_path,
        dll_dir=tmp_path,
        output_csv=tmp_path / "xic_results.csv",
        diagnostics_csv=tmp_path / "diagnostics.csv",
        smooth_window=15,
        smooth_polyorder=3,
        ms1_morphology_smoothing_window_points=21,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.1,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
        emit_peak_candidates=False,
        resolver_mode="region_first_safe_merge",
    )
    target = Target(
        label="Analyte",
        mz=258.1085,
        rt_min=8.0,
        rt_max=8.8,
        ppm_tol=20.0,
        neutral_loss_da=None,
        nl_ppm_warn=None,
        nl_ppm_max=None,
        is_istd=False,
        istd_pair="ISTD",
    )
    candidate = _candidate(rt=8.4, peak_start=8.1, peak_end=8.7)
    peak_result = PeakDetectionResult(
        status="OK",
        peak=candidate.peak,
        n_points=9,
        max_smoothed=1200.0,
        n_prominent_peaks=1,
        candidates=(candidate,),
    )
    captured_windows: dict[str, int] = {}

    class _Raw:
        def extract_xic(self, *_args, **_kwargs):
            return (
                np.asarray([8.0, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8]),
                np.asarray(
                    [10.0, 10.0, 12.0, 20.0, 60.0, 20.0, 12.0, 10.0, 10.0]
                ),
            )

    def _raise_after_capturing_selected_envelope(*_args, **kwargs):
        captured_windows["selected_envelope"] = (
            kwargs["policy"].morphology_trace_window_points
        )
        raise ValueError("captured")

    def _raise_after_capturing_chrom_segment(*_args, **kwargs):
        captured_windows["chrom_segment"] = (
            kwargs["policy"].morphology_trace_window_points
        )
        raise ValueError("captured")

    monkeypatch.setattr(
        "xic_extractor.extractor.find_peak_and_area",
        lambda *_args, **_kwargs: peak_result,
    )
    monkeypatch.setattr(
        target_extraction,
        "check_target_nl",
        lambda *_args, **_kwargs: NLResult("NO_MS2", None, None, 0, 0, 0),
    )
    monkeypatch.setattr(
        target_extraction,
        "recover_istd_anchor_peak_if_needed",
        lambda peak_result, **_kwargs: IstdAnchorRecoveryDecision(peak_result),
    )
    monkeypatch.setattr(
        target_extraction,
        "append_peak_audit_rows",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        handoff_spine_runtime,
        "selected_envelope_promoted_hypothesis_from_hypothesis",
        _raise_after_capturing_selected_envelope,
    )
    monkeypatch.setattr(
        handoff_spine_runtime,
        "chrom_peak_segment_promoted_hypothesis_from_hypothesis",
        _raise_after_capturing_chrom_segment,
    )
    monkeypatch.setattr(
        target_extraction,
        "build_extraction_result",
        lambda **kwargs: ExtractionResult(
            peak_result=kwargs["peak_result"],
            nl=kwargs["nl_result"],
            target_label=kwargs["target"].label,
        ),
    )

    target_extraction.extract_one_target(
        _Raw(),
        config,
        "SampleA",
        target,
        reference_rt=None,
        results={},
        diagnostics=[],
        istd_rt_in_this_sample=8.4,
    )

    assert captured_windows == {
        "selected_envelope": 21,
        "chrom_segment": 21,
    }


def test_extract_one_target_uses_chrom_segment_before_selected_envelope_cap(
    tmp_path,
    monkeypatch,
) -> None:
    config = ExtractionConfig(
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
        emit_peak_candidates=False,
        resolver_mode="region_first_safe_merge",
    )
    target = Target(
        label="Analyte",
        mz=258.1085,
        rt_min=8.0,
        rt_max=8.5,
        ppm_tol=20.0,
        neutral_loss_da=None,
        nl_ppm_warn=None,
        nl_ppm_max=None,
        is_istd=False,
        istd_pair="ISTD",
    )
    candidate = _candidate(
        rt=8.4,
        peak_start=8.3,
        peak_end=8.5,
    )
    peak_result = PeakDetectionResult(
        status="OK",
        peak=candidate.peak,
        n_points=13,
        max_smoothed=1200.0,
        n_prominent_peaks=1,
        candidates=(candidate,),
    )
    captured: dict[str, object] = {}

    class _Raw:
        def extract_xic(self, *_args, **_kwargs):
            return (
                np.asarray(
                    [
                        8.0,
                        8.1,
                        8.2,
                        8.3,
                        8.4,
                        8.5,
                        8.6,
                        8.7,
                        8.8,
                        8.9,
                        9.0,
                        9.1,
                        9.2,
                    ]
                ),
                np.asarray(
                    [
                        10.0,
                        10.0,
                        12.0,
                        20.0,
                        60.0,
                        20.0,
                        12.0,
                        10.0,
                        10.0,
                        55.0,
                        90.0,
                        55.0,
                        10.0,
                    ]
                ),
            )

    monkeypatch.setattr(
        "xic_extractor.extractor.find_peak_and_area",
        lambda *_args, **_kwargs: peak_result,
    )
    monkeypatch.setattr(
        target_extraction,
        "check_target_nl",
        lambda *_args, **_kwargs: NLResult("NO_MS2", None, None, 0, 0, 0),
    )
    monkeypatch.setattr(
        target_extraction,
        "recover_istd_anchor_peak_if_needed",
        lambda peak_result, **_kwargs: IstdAnchorRecoveryDecision(peak_result),
    )
    monkeypatch.setattr(
        target_extraction,
        "append_peak_audit_rows",
        lambda **_kwargs: None,
    )

    def _fake_build_extraction_result(**kwargs):
        captured.update(kwargs)
        return ExtractionResult(
            peak_result=kwargs["peak_result"],
            nl=kwargs["nl_result"],
            target_label=kwargs["target"].label,
        )

    monkeypatch.setattr(
        target_extraction,
        "build_extraction_result",
        _fake_build_extraction_result,
    )

    results: dict[str, ExtractionResult] = {}
    diagnostics = []
    target_extraction.extract_one_target(
        _Raw(),
        config,
        "SampleA",
        target,
        reference_rt=None,
        results=results,
        diagnostics=diagnostics,
        istd_rt_in_this_sample=8.4,
    )

    selected = captured["selected_hypothesis"]
    decision = captured["selection_decision"]
    assert isinstance(selected, PeakHypothesis)
    assert isinstance(decision, PeakHypothesisSelectionDecision)
    assert selected.integration.integration_method == "chrom_peak_segment_gaussian15"
    assert decision.projected_confidence != "VERY_LOW"
    assert "selected_envelope_boundary_externalize" not in decision.not_counted_reasons


def test_extract_one_target_caps_non_accept_selected_envelope_boundary(
    tmp_path,
    monkeypatch,
) -> None:
    config = ExtractionConfig(
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
        emit_peak_candidates=False,
        resolver_mode="region_first_safe_merge",
    )
    target = Target(
        label="Analyte",
        mz=258.1085,
        rt_min=8.0,
        rt_max=8.5,
        ppm_tol=20.0,
        neutral_loss_da=None,
        nl_ppm_warn=None,
        nl_ppm_max=None,
        is_istd=False,
        istd_pair="ISTD",
    )
    candidate = _candidate(
        rt=8.4,
        peak_start=8.3,
        peak_end=8.5,
    )
    peak_result = PeakDetectionResult(
        status="OK",
        peak=candidate.peak,
        n_points=13,
        max_smoothed=1200.0,
        n_prominent_peaks=1,
        candidates=(candidate,),
    )
    captured: dict[str, object] = {}

    class _Raw:
        def extract_xic(self, *_args, **_kwargs):
            return (
                np.asarray(
                    [
                        8.0,
                        8.1,
                        8.2,
                        8.3,
                        8.4,
                        8.5,
                        8.6,
                        8.7,
                        8.8,
                        8.9,
                        9.0,
                        9.1,
                        9.2,
                    ]
                ),
                np.asarray(
                    [
                        10.0,
                        10.0,
                        12.0,
                        20.0,
                        60.0,
                        20.0,
                        12.0,
                        10.0,
                        10.0,
                        55.0,
                        90.0,
                        55.0,
                        10.0,
                    ]
                ),
            )

    monkeypatch.setattr(
        "xic_extractor.extractor.find_peak_and_area",
        lambda *_args, **_kwargs: peak_result,
    )
    monkeypatch.setattr(
        target_extraction,
        "check_target_nl",
        lambda *_args, **_kwargs: NLResult("NO_MS2", None, None, 0, 0, 0),
    )
    monkeypatch.setattr(
        target_extraction,
        "recover_istd_anchor_peak_if_needed",
        lambda peak_result, **_kwargs: IstdAnchorRecoveryDecision(peak_result),
    )
    monkeypatch.setattr(
        target_extraction,
        "append_peak_audit_rows",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        handoff_spine_runtime,
        "chrom_peak_segment_promoted_hypothesis_from_hypothesis",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("disabled")),
    )

    def _fake_build_extraction_result(**kwargs):
        captured.update(kwargs)
        return ExtractionResult(
            peak_result=kwargs["peak_result"],
            nl=kwargs["nl_result"],
            target_label=kwargs["target"].label,
        )

    monkeypatch.setattr(
        target_extraction,
        "build_extraction_result",
        _fake_build_extraction_result,
    )

    results: dict[str, ExtractionResult] = {}
    diagnostics = []
    target_extraction.extract_one_target(
        _Raw(),
        config,
        "SampleA",
        target,
        reference_rt=None,
        results=results,
        diagnostics=diagnostics,
        istd_rt_in_this_sample=8.4,
    )

    decision = captured["selection_decision"]
    assert isinstance(decision, PeakHypothesisSelectionDecision)
    assert decision.projected_confidence == "VERY_LOW"
    assert "selected_envelope_context_apex_conflict" in decision.review_reasons
    assert "selected_envelope_boundary_externalize" in decision.not_counted_reasons


def test_paired_analyte_fallback_window_uses_target_region_not_istd_rt(
    tmp_path,
    monkeypatch,
) -> None:
    config = ExtractionConfig(
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
        nl_fallback_half_window_min=0.4,
    )
    target = Target(
        label="Analyte",
        mz=258.1085,
        rt_min=16.0,
        rt_max=18.0,
        ppm_tol=20.0,
        neutral_loss_da=116.0474,
        nl_ppm_warn=20.0,
        nl_ppm_max=50.0,
        is_istd=False,
        istd_pair="ISTD",
    )
    monkeypatch.setattr(
        "xic_extractor.extractor.find_nl_anchor_rt",
        lambda *_args, **_kwargs: None,
    )

    rt_min, rt_max, anchor_used, anchor_rt = get_rt_window(
        object(),
        target,
        config,
        reference_rt=12.0,
    )

    assert (rt_min, rt_max) == pytest.approx((16.6, 17.4))
    assert anchor_used is False
    assert anchor_rt == pytest.approx(17.0)


def test_paired_analyte_fallback_window_uses_learned_target_reference(
    tmp_path,
    monkeypatch,
) -> None:
    config = ExtractionConfig(
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
        nl_fallback_half_window_min=0.4,
    )
    target = Target(
        label="Analyte",
        mz=258.1085,
        rt_min=16.0,
        rt_max=18.0,
        ppm_tol=20.0,
        neutral_loss_da=116.0474,
        nl_ppm_warn=20.0,
        nl_ppm_max=50.0,
        is_istd=False,
        istd_pair="ISTD",
    )
    monkeypatch.setattr(
        "xic_extractor.extractor.find_nl_anchor_rt",
        lambda *_args, **_kwargs: None,
    )

    rt_min, rt_max, anchor_used, anchor_rt = get_rt_window(
        object(),
        target,
        config,
        reference_rt=16.40,
        target_reference_rt=16.45,
    )

    assert (rt_min, rt_max) == pytest.approx((16.05, 16.85))
    assert anchor_used is False
    assert anchor_rt == pytest.approx(16.45)


def test_distant_target_nl_anchor_falls_back_to_target_reference_not_istd_rt(
    tmp_path,
    monkeypatch,
) -> None:
    config = ExtractionConfig(
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
        nl_fallback_half_window_min=0.4,
    )
    target = Target(
        label="Analyte",
        mz=258.1085,
        rt_min=16.0,
        rt_max=18.0,
        ppm_tol=20.0,
        neutral_loss_da=116.0474,
        nl_ppm_warn=20.0,
        nl_ppm_max=50.0,
        is_istd=False,
        istd_pair="ISTD",
    )
    monkeypatch.setattr(
        "xic_extractor.extractor.find_nl_anchor_rt",
        lambda *_args, **_kwargs: 18.20,
    )

    rt_min, rt_max, anchor_used, anchor_rt = get_rt_window(
        object(),
        target,
        config,
        reference_rt=16.40,
        target_reference_rt=16.45,
    )

    assert (rt_min, rt_max) == pytest.approx((16.05, 16.85))
    assert anchor_used is False
    assert anchor_rt == pytest.approx(16.45)


def test_distant_target_nl_anchor_falls_back_to_paired_istd_inside_target_window(
    tmp_path,
    monkeypatch,
) -> None:
    config = ExtractionConfig(
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
        nl_fallback_half_window_min=0.4,
    )
    target = Target(
        label="Analyte",
        mz=258.1085,
        rt_min=16.0,
        rt_max=18.0,
        ppm_tol=20.0,
        neutral_loss_da=116.0474,
        nl_ppm_warn=20.0,
        nl_ppm_max=50.0,
        is_istd=False,
        istd_pair="ISTD",
    )
    monkeypatch.setattr(
        "xic_extractor.extractor.find_nl_anchor_rt",
        lambda *_args, **_kwargs: 18.20,
    )

    rt_min, rt_max, anchor_used, anchor_rt = get_rt_window(
        object(),
        target,
        config,
        reference_rt=16.40,
        target_reference_rt=None,
    )

    assert (rt_min, rt_max) == pytest.approx((16.0, 16.8))
    assert anchor_used is False
    assert anchor_rt == pytest.approx(16.40)


def test_paired_target_reference_rt_uses_active_pair_delta() -> None:
    target = Target(
        label="Analyte",
        mz=258.1085,
        rt_min=16.0,
        rt_max=18.0,
        ppm_tol=20.0,
        neutral_loss_da=116.0474,
        nl_ppm_warn=20.0,
        nl_ppm_max=50.0,
        is_istd=False,
        istd_pair="ISTD",
    )
    library = {
        ("Analyte", "analyte"): LibraryEntry(
            config_hash="abc",
            target_label="Analyte",
            role="analyte",
            istd_pair="ISTD",
            median_delta_rt=0.05,
            sigma_delta_rt=0.01,
            median_abs_rt=None,
            sigma_abs_rt=None,
            n_samples=1,
            updated_at="fixture",
        )
    }

    expected = target_extraction.paired_target_reference_rt(
        target,
        reference_rt=16.40,
        rt_prior_library=library,
    )

    assert expected == pytest.approx(16.45)


def _candidate(
    *,
    rt: float = 10.0,
    area: float = 1000.0,
    peak_start: float | None = None,
    peak_end: float | None = None,
    quality_flags: tuple[str, ...] = (),
    merge_note: str = "",
) -> PeakCandidate:
    return PeakCandidate(
        peak=PeakResult(
            rt=rt,
            intensity=100.0,
            intensity_smoothed=100.0,
            area=area,
            peak_start=rt - 0.1 if peak_start is None else peak_start,
            peak_end=rt + 0.1 if peak_end is None else peak_end,
        ),
        selection_apex_rt=rt,
        selection_apex_intensity=100.0,
        selection_apex_index=1,
        raw_apex_rt=rt,
        raw_apex_intensity=100.0,
        raw_apex_index=1,
        prominence=50.0,
        quality_flags=quality_flags,
        merge_note=merge_note,
    )


def _peak_result(candidate: PeakCandidate) -> PeakDetectionResult:
    return PeakDetectionResult(
        status="OK",
        peak=candidate.peak,
        n_points=5,
        max_smoothed=1200.0,
        n_prominent_peaks=1,
        candidates=(candidate,),
    )


def _evidence(nl_status: str) -> CandidateMS2Evidence:
    return CandidateMS2Evidence(
        ms2_present=nl_status != "NO_MS2",
        nl_match=nl_status in {"OK", "WARN"},
        nl_status=nl_status,
        trigger_scan_count=1 if nl_status != "NO_MS2" else 0,
        strict_nl_scan_count=1 if nl_status in {"OK", "WARN"} else 0,
        best_loss_ppm=None,
        best_scan_rt=None,
        best_product_base_ratio=None,
        alignment_source="none",
    )
