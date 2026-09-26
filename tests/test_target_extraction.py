from dataclasses import replace

import numpy as np
import pytest

import xic_extractor.extraction.target_extraction as target_extraction
from xic_extractor.config import ExtractionConfig, Target
from xic_extractor.extraction.istd_anchor_retry import IstdAnchorRetryDecision
from xic_extractor.extraction.istd_prepass import extract_istd_anchors_only
from xic_extractor.extraction.ms2_selection import selected_candidate_ms2_evidence
from xic_extractor.extraction.rt_windows import get_rt_window
from xic_extractor.extraction.scoring_factory import build_scoring_context_factory
from xic_extractor.extractor import ExtractionResult
from xic_extractor.ms2_scan_models import Ms2Scan, Ms2ScanEvent
from xic_extractor.neutral_loss import CandidateMS2Evidence, NLResult
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.models import (
    PeakCandidate,
    PeakCandidatesResult,
    PeakDetectionResult,
    PeakResult,
)
from xic_extractor.peak_detection.ms1_morphology import (
    compute_shape_metrics,
    gaussian15_positive_asls_residual_trace,
)
from xic_extractor.peak_detection.physical_peak_candidate_adapter import (
    physical_candidates_result,
    selected_physical_segment,
)
from xic_extractor.peak_detection.physical_peak_models import (
    PhysicalSegment,
    PhysicalSegmentUniverse,
    PreparedSignal,
    PreparedSignalProjection,
    PreparedSignalResult,
)
from xic_extractor.peak_detection.scoring_metrics import compute_local_sn_cache
from xic_extractor.peak_detection.selection_decision import (
    PeakHypothesisSelectionDecision,
)
from xic_extractor.peak_detection.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
)
from xic_extractor.peak_detection.targeted_product_projection import (
    TargetedProductProjection,
)
from xic_extractor.rt_prior_library import LibraryEntry


def test_selected_candidate_builds_missing_ms2_evidence() -> None:
    candidate = _candidate()
    evidence = _evidence("NO_MS2")
    calls: list[PeakCandidate] = []

    def _builder(value: PeakCandidate) -> CandidateMS2Evidence:
        calls.append(value)
        return evidence

    selected = selected_candidate_ms2_evidence(candidate, {}, _builder)

    assert selected is evidence
    assert calls == [candidate]


def test_selected_candidate_prefers_existing_ms2_cache() -> None:
    # The legacy finder deliberately points elsewhere. Formal Targeted must
    # select from the shared physical universe and carry that owner's apex.
    candidate = _candidate(rt=8.75, peak_start=8.7, peak_end=8.8)
    calls: list[PeakCandidate] = []

    def _builder(value: PeakCandidate) -> CandidateMS2Evidence:
        calls.append(value)
        return _evidence("NL_FAIL")

    cached = _evidence("NO_MS2")
    selected = selected_candidate_ms2_evidence(candidate, {candidate: cached}, _builder)

    assert selected is cached
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


@pytest.mark.parametrize("resolver_mode", ["legacy_savgol", "local_minimum"])
@pytest.mark.parametrize("is_istd", [False, True])
@pytest.mark.parametrize("with_scoring", [False, True])
def test_extract_one_target_uses_shared_physical_candidate_and_owner_apex(
    tmp_path,
    monkeypatch,
    resolver_mode: str,
    is_istd: bool,
    with_scoring: bool,
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
        resolver_mode=resolver_mode,
    )
    target = Target(
        label="ISTD" if is_istd else "Analyte",
        mz=258.1085,
        rt_min=8.0,
        rt_max=9.0,
        ppm_tol=20.0,
        neutral_loss_da=None,
        nl_ppm_warn=None,
        nl_ppm_max=None,
        is_istd=is_istd,
        istd_pair="" if is_istd else "ISTD",
    )
    captured: dict[str, object] = {}
    physical_calls = {"prepare": 0, "enumerate": 0}
    prepare_physical_signal = target_extraction.prepare_physical_signal
    enumerate_physical_segment_universe = (
        target_extraction.enumerate_physical_segment_universe
    )

    def _prepare(*args, **kwargs):
        physical_calls["prepare"] += 1
        result = prepare_physical_signal(*args, **kwargs)
        captured["prepared_result"] = result
        return result

    def _enumerate(*args, **kwargs):
        physical_calls["enumerate"] += 1
        result = enumerate_physical_segment_universe(*args, **kwargs)
        captured["universe"] = result
        return result

    monkeypatch.setattr(target_extraction, "prepare_physical_signal", _prepare)
    monkeypatch.setattr(
        target_extraction, "enumerate_physical_segment_universe", _enumerate
    )
    selected_handoff_peak = target_extraction.selected_handoff_peak

    def _handoff(**kwargs):
        captured["handoff_inputs"] = kwargs
        return selected_handoff_peak(**kwargs)

    monkeypatch.setattr(target_extraction, "selected_handoff_peak", _handoff)

    acquired_rt = np.linspace(8.0, 8.8, 41)
    acquired_intensity = 10.0 + 100.0 * np.exp(-((acquired_rt - 8.4) ** 2) / 0.002)
    audit_rt = 8.4 + 0.5 * (acquired_rt - 8.4) if is_istd else acquired_rt

    class _Raw:
        def extract_xic(self, *_args, **_kwargs):
            return acquired_rt, acquired_intensity

    monkeypatch.setattr(
        "xic_extractor.extractor.find_peak_and_area",
        lambda *_args, **_kwargs: pytest.fail(
            "formal Targeted must not invoke a legacy resolver finder"
        ),
    )
    monkeypatch.setattr(
        target_extraction,
        "check_target_nl",
        lambda *_args, **_kwargs: NLResult("NO_MS2", None, None, 0, 0, 0),
    )
    monkeypatch.setattr(
        target_extraction,
        "retry_istd_anchor_peak_if_needed",
        lambda peak_result, **_kwargs: IstdAnchorRetryDecision(
            peak_result,
            rt=audit_rt if is_istd else None,
            intensity=acquired_intensity if is_istd else None,
        ),
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
    shape_metrics_by_label = {}
    diagnostics = []
    anchor_rt = target_extraction.extract_one_target(
        _Raw(),
        config,
        "SampleA",
        target,
        reference_rt=None,
        results=results,
        diagnostics=diagnostics,
        istd_rt_in_this_sample=8.55,
        shape_metrics_by_label=shape_metrics_by_label,
        scoring_context_factory=(
            build_scoring_context_factory(
                config=config,
                injection_order={},
                istd_rts_by_sample={},
                rt_prior_library={},
            )
            if with_scoring
            else None
        ),
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
    assert decision.legacy_projection_status == (
        "successor_owned" if with_scoring else "active_policy_remaining"
    )
    assert selected.target_label == target.label
    assert captured["peak_result"].peak.rt == pytest.approx(8.4)
    selected_candidate_value = target_extraction.selected_candidate(
        captured["peak_result"]
    )
    assert selected_candidate_value is not None
    evaluation = captured["physical_evaluation"]
    universe = captured["universe"]
    prepared = captured["prepared_result"].prepared
    segment = next(
        segment
        for segment in universe.segments
        if segment.physical_segment_id == evaluation.answer.physical_segment_id
    )
    assert selected_candidate_value.proposal_sources == (
        f"physical_segment:{segment.physical_segment_id}",
    )
    assert selected_candidate_value.selection_apex_index == segment.apex_index
    assert selected_candidate_value.raw_apex_index == segment.apex_index
    assert evaluation.facts is not None
    assert evaluation.facts.physical_segment_id == segment.physical_segment_id
    assert evaluation.question.query_rt_min == pytest.approx(8.4)
    assert evaluation.question.admissible_segment_ids == (segment.physical_segment_id,)
    assert evaluation.segment_universe == universe
    assert evaluation.retained_prepared_signal == prepared
    expected_physical_calls = 2 if is_istd else 1
    assert physical_calls == {
        "prepare": expected_physical_calls,
        "enumerate": expected_physical_calls,
    }
    handoff_inputs = captured["handoff_inputs"]
    assert handoff_inputs["candidate"] == selected_candidate_value
    assert handoff_inputs["paired_istd_anchor_rt"] == 8.55
    assert handoff_inputs["expected_rt_min"] is None
    np.testing.assert_array_equal(handoff_inputs["rt"], prepared.rt)
    np.testing.assert_array_equal(handoff_inputs["intensity"], prepared.raw)
    assert captured["acquisition_trace"].sample_name == "SampleA"
    assert captured["acquisition_trace"].mz == target.mz
    assert anchor_rt is None
    assert selected.integration.area_ms1_morphology == pytest.approx(
        captured["physical_evaluation"].facts.area_counts_seconds
    )
    assert captured["peak_result"].paired_istd_anchor_rt == 8.55
    assert isinstance(results[target.label], ExtractionResult)
    expected_shape = compute_shape_metrics(
        np.asarray(prepared.rt),
        np.asarray(prepared.positive_signal),
        selected_candidate_value.selection_apex_index,
    )
    assert shape_metrics_by_label[target.label] == expected_shape
    if with_scoring:
        ctx = captured["scoring_context_builder"](selected_candidate_value)
        assert ctx.half_width_ratio == expected_shape[0]


@pytest.mark.parametrize(
    "route", ["paired", "context_extension", "wider_retry", "prepass_none"]
)
@pytest.mark.parametrize("window_points", [7, 15])
def test_targeted_shape_routes_use_cached_canonical_signal(
    tmp_path, monkeypatch, route: str, window_points: int
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
        signal_processing_window_points=window_points,
        nl_rt_anchor_half_window_min=0.2,
        nl_fallback_half_window_min=0.8,
    )
    uses_anchor = route in {"context_extension", "wider_retry"}
    target = Target(
        label="ISTD",
        mz=258.1085,
        rt_min=0.0,
        rt_max=2.0,
        ppm_tol=20.0,
        neutral_loss_da=116.0474 if uses_anchor else None,
        nl_ppm_warn=20.0 if uses_anchor else None,
        nl_ppm_max=50.0 if uses_anchor else None,
        is_istd=route != "context_extension",
        istd_pair="",
    )
    rt = np.linspace(0.0, 2.0, 201)
    center = (
        0.8 if route == "context_extension" else 0.5 if route == "wider_retry" else 1.0
    )
    sigma = 0.1 if route == "context_extension" else 0.04
    intensity = 1000.0 * np.exp(-0.5 * ((rt - center) / sigma) ** 2)
    if route == "wider_retry":
        intensity[np.abs(rt - center) > 0.2] = 0.0
    elif route != "context_extension":
        intensity += 20.0 + 5.0 * rt
    acquisitions = []

    class _Raw:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_xic(self, _mz, rt_min, rt_max, _ppm):
            acquisitions.append((rt_min, rt_max))
            mask = (rt >= rt_min - 1e-12) & (rt <= rt_max + 1e-12)
            return rt[mask], intensity[mask]

        def iter_ms2_scans(self, rt_min, rt_max):
            if uses_anchor and rt_min <= 1.0 <= rt_max:
                yield Ms2ScanEvent(
                    Ms2Scan(
                        1,
                        1.0,
                        target.mz,
                        np.array([target.mz - 116.0474]),
                        np.array([1000.0]),
                        1000.0,
                    ),
                    None,
                    1,
                )

    raw = _Raw()
    monkeypatch.setattr(
        "xic_extractor.extractor.open_raw", lambda *_args, **_kwargs: raw
    )
    monkeypatch.setattr(
        "xic_extractor.extraction.istd_prepass.open_raw", lambda *_args, **_kwargs: raw
    )
    prepared_results = []
    enumerations = []
    prepare = target_extraction.prepare_physical_signal
    enumerate_segments = target_extraction.enumerate_physical_segment_universe

    def _prepare(*args, **kwargs):
        result = prepare(*args, **kwargs)
        prepared_results.append(result)
        return result

    def _enumerate(*args, **kwargs):
        result = enumerate_segments(*args, **kwargs)
        enumerations.append(result)
        return result

    monkeypatch.setattr(target_extraction, "prepare_physical_signal", _prepare)
    monkeypatch.setattr(
        target_extraction, "enumerate_physical_segment_universe", _enumerate
    )
    true_factory = build_scoring_context_factory(
        config=config, injection_order={}, istd_rts_by_sample={}, rt_prior_library={}
    )
    builders = []

    def _factory(**kwargs):
        builder = true_factory(**kwargs)
        builders.append((kwargs, builder))
        return builder

    if route == "paired":
        analyte = replace(target, label="Analyte", is_istd=False, istd_pair="ISTD")
        file_result, _ = target_extraction.process_file(
            config,
            [target, analyte],
            tmp_path / "SampleA.raw",
            scoring_context_factory=_factory,
        )
        assert file_result.error is None
        result = file_result.results["ISTD"]
        analyte_candidate = target_extraction.selected_candidate(
            file_result.results["Analyte"].peak_result
        )
        assert analyte_candidate is not None
        analyte_kwargs, analyte_builder = next(
            (kwargs, builder)
            for kwargs, builder in builders
            if kwargs["target"].label == "Analyte"
        )
        assert analyte_kwargs["paired_istd_fwhm"] > 0
        assert analyte_builder(analyte_candidate).fwhm_ratio == 1.0
        shapes = {
            "ISTD": target_extraction.selected_shape_metrics(
                prepared_results[0], result.peak_result
            )
        }
    elif route == "prepass_none":
        prepass = extract_istd_anchors_only(config, [target], tmp_path / "SampleA.raw")
        assert prepass is not None
        _, results, _, shapes = prepass
        result = results[target.label]
        assert builders == []
    else:
        results = {}
        shapes = {}
        target_extraction.extract_one_target(
            raw,
            config,
            "SampleA",
            target,
            reference_rt=None,
            results=results,
            diagnostics=[],
            scoring_context_factory=_factory,
            shape_metrics_by_label=shapes,
        )
        result = results[target.label]
    candidate = target_extraction.selected_candidate(result.peak_result)
    assert candidate is not None
    prepared = prepared_results[-1].prepared
    assert prepared is not None
    assert prepared.profile.window_points == window_points
    expected = compute_shape_metrics(
        np.asarray(prepared.rt),
        np.asarray(prepared.positive_signal),
        candidate.selection_apex_index,
    )
    assert shapes[target.label] == expected
    raw_shape = compute_shape_metrics(
        np.asarray(prepared.rt),
        np.asarray(prepared.raw),
        candidate.selection_apex_index,
    )
    baseline, _ = compute_local_sn_cache(np.asarray(prepared.raw))
    legacy = gaussian15_positive_asls_residual_trace(np.asarray(prepared.raw), baseline)
    legacy_shape = compute_shape_metrics(
        np.asarray(prepared.rt), legacy, candidate.selection_apex_index
    )
    assert expected != pytest.approx(raw_shape)
    assert expected != pytest.approx(legacy_shape)
    for kwargs, builder in builders:
        physical = kwargs["prepared_result"].prepared
        if physical is None:
            continue
        np.testing.assert_array_equal(kwargs["rt"], physical.rt)
        np.testing.assert_array_equal(kwargs["intensity"], physical.raw)
        if tuple(physical.rt) == tuple(prepared.rt):
            assert builder(candidate).half_width_ratio == expected[0]
    expected_calls = 2 if route in {"paired", "context_extension", "wider_retry"} else 1
    assert len(prepared_results) == len(enumerations) == expected_calls
    if route == "context_extension":
        np.testing.assert_allclose(acquisitions, [(0.8, 1.2), (0.0, 2.0)])
    elif route == "wider_retry":
        np.testing.assert_allclose(acquisitions, [(0.8, 1.2), (0.2, 1.8)])
        # Initial, retry, and accepted-trace audit reuse the physical cache.
        assert len(builders) == 3


def test_physical_candidate_projection_preserves_owner_values_and_segment_order(
    physical_candidate_input,
) -> None:
    prepared, universe = physical_candidate_input

    result = physical_candidates_result(prepared, universe)

    assert result == PeakCandidatesResult(
        status="OK",
        candidates=(
            PeakCandidate(
                peak=PeakResult(3.0, 90.0, 75.0, 1200.0, 2.0, 4.0),
                selection_apex_rt=3.0,
                selection_apex_intensity=75.0,
                selection_apex_index=3,
                raw_apex_rt=3.0,
                raw_apex_intensity=90.0,
                raw_apex_index=3,
                prominence=65.0,
                region_scan_count=3,
                region_duration_min=2.0,
                region_edge_ratio=3.0 / 65.0,
                region_trace_continuity=1.0,
                proposal_sources=("physical_segment:segment:later",),
            ),
            PeakCandidate(
                peak=PeakResult(1.0, 60.0, 45.0, 600.0, 0.0, 2.0),
                selection_apex_rt=1.0,
                selection_apex_intensity=45.0,
                selection_apex_index=1,
                raw_apex_rt=1.0,
                raw_apex_intensity=60.0,
                raw_apex_index=1,
                prominence=35.0,
                region_scan_count=3,
                region_duration_min=2.0,
                region_edge_ratio=3.0 / 35.0,
                region_trace_continuity=1.0,
                proposal_sources=("physical_segment:segment:earlier",),
            ),
        ),
        n_points=5,
        max_smoothed=75.0,
        n_prominent_peaks=2,
    )


@pytest.mark.parametrize(
    ("state", "reason", "prepared_missing", "status"),
    [
        ("unavailable", "no_signal_above_baseline", False, "NO_SIGNAL"),
        ("unavailable", "insufficient_trace_points", False, "PEAK_NOT_FOUND"),
        ("failed", "context_unavailable", False, "PEAK_NOT_FOUND"),
        ("available", "available", True, "PEAK_NOT_FOUND"),
    ],
)
def test_physical_candidate_projection_preserves_unavailable_status(
    physical_candidate_input,
    state,
    reason,
    prepared_missing,
    status,
) -> None:
    prepared, universe = physical_candidate_input
    if prepared_missing:
        prepared = replace(prepared, prepared=None)
    universe = replace(universe, state=state, reason=reason)

    result = physical_candidates_result(prepared, universe)

    assert result == PeakCandidatesResult(status, (), 5, None, 0)


def test_physical_candidate_projection_preserves_empty_universe(
    physical_candidate_input,
) -> None:
    prepared, universe = physical_candidate_input

    result = physical_candidates_result(prepared, replace(universe, segments=()))

    assert result == PeakCandidatesResult("PEAK_NOT_FOUND", (), 5, 75.0, 0)


def test_physical_candidate_selection_binds_unique_owner_segment(
    physical_candidate_input,
) -> None:
    prepared, universe = physical_candidate_input
    candidates = physical_candidates_result(prepared, universe).candidates

    assert selected_physical_segment(None, universe) is None
    for candidate, segment in zip(candidates, universe.segments, strict=True):
        assert selected_physical_segment(candidate, universe) == segment
    repeated_source = replace(
        candidates[0],
        proposal_sources=(
            "other_proposal",
            "physical_segment:segment:later",
            "physical_segment:segment:later",
        ),
    )
    assert selected_physical_segment(repeated_source, universe) == universe.segments[0]


@pytest.mark.parametrize(
    "sources",
    [
        (),
        ("other_proposal",),
        ("physical_segment:missing",),
        ("physical_segment:segment:later", "physical_segment:segment:earlier"),
    ],
)
def test_physical_candidate_selection_rejects_nonunique_binding(
    physical_candidate_input,
    sources,
) -> None:
    prepared, universe = physical_candidate_input
    candidate = physical_candidates_result(prepared, universe).candidates[0]

    with pytest.raises(
        ValueError,
        match="^selected Targeted candidate must identify one physical segment$",
    ):
        selected_physical_segment(
            replace(candidate, proposal_sources=sources), universe
        )


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


@pytest.fixture
def physical_candidate_input() -> tuple[PreparedSignalResult, PhysicalSegmentUniverse]:
    profile = DEFAULT_SIGNAL_PROCESSING_PROFILE
    projection = PreparedSignalProjection(
        schema_version="prepared_signal",
        trace_content_id="trace:test",
        profile_id=profile.profile_id,
        prepared_signal_id="prepared:test",
        trace_point_count=5,
        state="available",
        reason="available",
    )
    prepared = PreparedSignal(
        projection=projection,
        profile=profile,
        rt=(0.0, 1.0, 2.0, 3.0, 4.0),
        raw=(11.0, 60.0, 21.0, 90.0, 14.0),
        baseline=(10.0,) * 5,
        smoothed=(12.0, 45.0, 13.0, 75.0, 11.0),
        positive_signal=(2.0, 35.0, 3.0, 65.0, 1.0),
    )
    segments = (
        PhysicalSegment(
            physical_segment_id="segment:later",
            start_index=2,
            end_index=5,
            apex_index=3,
            left_rt_min=2.0,
            right_rt_min=4.0,
            apex_rt_min=3.0,
            height=65.0,
            scan_support_count=3,
            area_counts_seconds=1200.0,
            segment_class="isolated_peak",
            boundary_stop_reason="baseline_return",
            evidence_sources=("owner_segment",),
        ),
        PhysicalSegment(
            physical_segment_id="segment:earlier",
            start_index=0,
            end_index=3,
            apex_index=1,
            left_rt_min=0.0,
            right_rt_min=2.0,
            apex_rt_min=1.0,
            height=35.0,
            scan_support_count=3,
            area_counts_seconds=600.0,
            segment_class="low_scan_segment",
            boundary_stop_reason="baseline_return",
            evidence_sources=("owner_segment",),
        ),
    )
    return (
        PreparedSignalResult(projection, prepared, ("trace_evaluation",)),
        PhysicalSegmentUniverse(
            schema_version="physical_segment_universe",
            prepared_signal_id=projection.prepared_signal_id,
            context_rt_start_min=0.0,
            context_rt_end_min=4.0,
            state="available",
            reason="available",
            segments=segments,
            segment_universe_id="universe:test",
        ),
    )


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
