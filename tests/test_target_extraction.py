from dataclasses import replace

import numpy as np
import pytest

import xic_extractor.extraction.target_extraction as target_extraction
from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.configuration.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
)
from xic_extractor.evidence.neutral_loss import CandidateMS2Evidence, NLResult
from xic_extractor.extraction.istd_prepass import extract_istd_anchors_only
from xic_extractor.extraction.ms2_selection import selected_candidate_ms2_evidence
from xic_extractor.extraction.result_models import ExtractionResult
from xic_extractor.extraction.rt_windows import get_rt_window
from xic_extractor.extraction.scoring_factory import build_scoring_context_factory
from xic_extractor.ms2_scan_models import Ms2Scan, Ms2ScanEvent
from xic_extractor.output.targeted_product_model import (
    TargetedProductProjection,
)
from xic_extractor.peak_detection.baseline import asls_baseline, asls_baseline_on_rt
from xic_extractor.peak_detection.evidence_facts import build_candidate_evidence_facts
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
from xic_extractor.peak_detection.selection_decision import (
    PeakHypothesisSelectionDecision,
)

_OPEN_RAW_SEAM = "xic_extractor.extraction.target_extraction.open_raw"


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


@pytest.mark.parametrize("resolver_mode", ["savgol", "local_minimum"])
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
    select_targeted_peak = target_extraction.select_targeted_peak

    def _handoff(**kwargs):
        captured["handoff_inputs"] = kwargs
        return select_targeted_peak(**kwargs)

    monkeypatch.setattr(target_extraction, "select_targeted_peak", _handoff)

    acquired_rt = np.linspace(8.0, 8.8, 41)
    acquired_intensity = 10.0 + 100.0 * np.exp(-((acquired_rt - 8.4) ** 2) / 0.002)

    class _Raw:
        def extract_xic(self, *_args, **_kwargs):
            return acquired_rt, acquired_intensity

    monkeypatch.setattr(
        "xic_extractor.peak_detection.candidate_search.find_peak_candidates",
        lambda *_args, **_kwargs: pytest.fail(
            "formal Targeted must not invoke a legacy resolver finder"
        ),
    )
    monkeypatch.setattr(
        target_extraction,
        "check_target_nl",
        lambda *_args, **_kwargs: NLResult("NO_MS2", None, None, 0, 0, 0),
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
            )
            if with_scoring
            else None
        ),
    )

    selected = captured["selected_hypothesis"]
    decision = captured["selection_decision"]
    assert isinstance(selected, PeakHypothesis)
    assert isinstance(decision, PeakHypothesisSelectionDecision)
    assert selected.audit.selected is True
    assert decision.selected_candidate_id == selected.hypothesis_id
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
    expected_physical_calls = 1
    assert physical_calls == {
        "prepare": expected_physical_calls,
        "enumerate": expected_physical_calls,
    }
    handoff_inputs = captured["handoff_inputs"]
    assert (
        target_extraction.selected_candidate(handoff_inputs["peak_result"])
        == selected_candidate_value
    )
    trace_group = handoff_inputs["trace_group"]
    assert trace_group.expected_rt_min == pytest.approx(8.5)
    np.testing.assert_array_equal(trace_group.primary_trace.rt, prepared.rt)
    np.testing.assert_array_equal(trace_group.primary_trace.intensity, prepared.raw)
    assert captured["acquisition_trace"].sample_name == "SampleA"
    assert captured["acquisition_trace"].mz == target.mz
    assert anchor_rt == pytest.approx(8.5)
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


@pytest.mark.parametrize("route", ["paired", "nl_analyte", "nl_istd", "prepass_none"])
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
    uses_anchor = route in {"nl_analyte", "nl_istd"}
    target = Target(
        label="ISTD",
        mz=258.1085,
        rt_min=0.0,
        rt_max=2.0,
        ppm_tol=20.0,
        neutral_loss_da=116.0474 if uses_anchor else None,
        nl_ppm_warn=20.0 if uses_anchor else None,
        nl_ppm_max=50.0 if uses_anchor else None,
        is_istd=route != "nl_analyte",
        istd_pair="",
    )
    rt = np.linspace(0.0, 2.0, 201)
    center = 0.9 if route == "nl_analyte" else 1.0
    sigma = 0.1 if route == "nl_analyte" else 0.04
    intensity = 1000.0 * np.exp(-0.5 * ((rt - center) / sigma) ** 2)
    if route == "nl_istd":
        intensity[np.abs(rt - center) > 0.2] = 0.0
    elif route != "nl_analyte":
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
    monkeypatch.setattr(_OPEN_RAW_SEAM, lambda *_args, **_kwargs: raw)
    monkeypatch.setattr(
        "xic_extractor.extraction.istd_prepass.open_raw", lambda *_args, **_kwargs: raw
    )
    import xic_extractor.peak_detection.physical_peak_evaluation as physical_owner
    import xic_extractor.peak_detection.scoring_metrics as sn_owner

    solves = {"physical": [], "sn": []}

    def _physical_asls(rt_values, values, **kwargs):
        solves["physical"].append(tuple(values))
        return asls_baseline_on_rt(rt_values, values, **kwargs)

    def _sn_asls(values, **kwargs):
        solves["sn"].append(tuple(values))
        return asls_baseline(values, **kwargs)

    monkeypatch.setattr(physical_owner, "asls_baseline_on_rt", _physical_asls)
    monkeypatch.setattr(sn_owner, "asls_baseline", _sn_asls)
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
        config=config, injection_order={}, istd_rts_by_sample={}
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
    baseline = asls_baseline(np.asarray(prepared.raw))
    legacy = gaussian15_positive_asls_residual_trace(np.asarray(prepared.raw), baseline)
    legacy_shape = compute_shape_metrics(
        np.asarray(prepared.rt), legacy, candidate.selection_apex_index
    )
    assert expected != pytest.approx(raw_shape)
    assert expected != pytest.approx(legacy_shape)
    expected_standalone = [
        tuple(kwargs["intensity"])
        for kwargs, _ in builders
        if kwargs["prepared_result"].baseline_unclipped is None
    ]
    assert solves["sn"] == expected_standalone
    assert solves["physical"] == [
        tuple(result.prepared.raw)
        for result in prepared_results
        if result.prepared is not None
    ]
    for kwargs, builder in builders:
        physical = kwargs["prepared_result"].prepared
        if physical is None:
            continue
        np.testing.assert_array_equal(kwargs["rt"], physical.rt)
        np.testing.assert_array_equal(kwargs["intensity"], physical.raw)
        oracle_baseline = np.asarray(kwargs["prepared_result"].baseline_unclipped)
        residual = np.asarray(physical.raw) - oracle_baseline
        oracle_mad = float(np.median(np.abs(residual - np.median(residual))))
        oracle_active = gaussian15_positive_asls_residual_trace(
            np.asarray(physical.raw), oracle_baseline
        )
        for apex in (int(np.argmax(physical.positive_signal)), 0):
            bound_candidate = replace(candidate, selection_apex_index=apex)
            ctx = replace(builder(candidate), apex_index=apex)
            np.testing.assert_array_equal(ctx.intensity_array, oracle_active)
            assert ctx.residual_mad == oracle_mad
            facts = build_candidate_evidence_facts(bound_candidate, ctx).trace
            if oracle_mad > 0:
                assert facts.local_sn_ratio == (
                    oracle_active[bound_candidate.selection_apex_index] / oracle_mad
                )
        if tuple(physical.rt) == tuple(prepared.rt):
            assert builder(candidate).half_width_ratio == expected[0]
    assert solves["sn"] == expected_standalone
    expected_calls = 2 if route == "paired" else 1
    assert len(prepared_results) == len(enumerations) == expected_calls
    np.testing.assert_allclose(acquisitions, [(0.0, 4.0)] * expected_calls)
    if uses_anchor:
        # The one acquired context covers both flanks; the NL anchor remains
        # a candidate-apex restriction rather than a truncation of the trace.
        assert 0.8 <= candidate.selection_apex_rt <= 1.2
        assert len(builders) == 1


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
                selection_apex_index=4,
                raw_apex_rt=3.0,
                raw_apex_intensity=90.0,
                raw_apex_index=4,
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
                selection_apex_index=2,
                raw_apex_rt=1.0,
                raw_apex_intensity=60.0,
                raw_apex_index=2,
                prominence=35.0,
                region_scan_count=3,
                region_duration_min=2.0,
                region_edge_ratio=3.0 / 35.0,
                region_trace_continuity=1.0,
                proposal_sources=("physical_segment:segment:earlier",),
            ),
        ),
        n_points=7,
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

    assert result == PeakCandidatesResult(status, (), 7, None, 0)


def test_physical_candidate_projection_preserves_empty_universe(
    physical_candidate_input,
) -> None:
    prepared, universe = physical_candidate_input

    result = physical_candidates_result(prepared, replace(universe, segments=()))

    assert result == PeakCandidatesResult("PEAK_NOT_FOUND", (), 7, 75.0, 0)


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
        "xic_extractor.extraction.rt_windows.find_nl_anchor_rt",
        lambda *_args, **_kwargs: None,
    )

    rt_min, rt_max, anchor_used, anchor_rt = get_rt_window(
        object(),
        target,
        config,
        sample_key="fixture",
        target_reference_rt=17.0,
    )

    assert (rt_min, rt_max) == pytest.approx((16.6, 17.4))
    assert anchor_used is False
    assert anchor_rt is None


@pytest.mark.parametrize("search_margin", [2.0, 0.05])
def test_istd_peak_survives_displaced_or_missing_nl_anchor(
    tmp_path, search_margin: float
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
        ms2_precursor_tol_da=1.1,
        nl_min_intensity_ratio=0.01,
        nl_rt_anchor_search_margin_min=search_margin,
        nl_rt_anchor_half_window_min=1.0,
        nl_fallback_half_window_min=0.5,
    )
    target = Target(
        label="ISTD",
        mz=289.0841,
        rt_min=16.0,
        rt_max=18.0,
        ppm_tol=20.0,
        neutral_loss_da=116.0474,
        nl_ppm_warn=20.0,
        nl_ppm_max=50.0,
        is_istd=True,
        istd_pair="",
    )
    rt = np.linspace(14.0, 20.0, 601)
    intensity = 20.0 + 1e6 * np.exp(-0.5 * ((rt - 16.4) / 0.1) ** 2)

    class _Raw:
        def extract_xic(self, _mz, rt_min, rt_max, _ppm):
            mask = (rt >= rt_min) & (rt <= rt_max)
            return rt[mask], intensity[mask]

        def iter_ms2_scans(self, rt_min, rt_max):
            # A different precursor with the same NL wins the broad DDA search.
            for scan_id, scan_rt, mz, height in (
                (1, 15.2, 288.066, 1e8),
                (2, 16.4, target.mz, 1e6),
            ):
                if rt_min <= scan_rt <= rt_max:
                    yield Ms2ScanEvent(
                        Ms2Scan(
                            scan_id,
                            scan_rt,
                            mz,
                            np.array([mz - 116.0474]),
                            np.array([height]),
                            height,
                        ),
                        None,
                        scan_id,
                    )

    raw = _Raw()
    window = get_rt_window(
        raw,
        target,
        config,
        sample_key="fixture",
        target_reference_rt=17.0,
    )
    assert window[2:] == ((True, 15.2) if search_margin == 2.0 else (False, None))
    results = {}
    target_extraction.extract_one_target(
        raw,
        config,
        "fixture",
        target,
        reference_rt=None,
        results=results,
        diagnostics=[],
        scoring_context_factory=build_scoring_context_factory(
            config=config,
            injection_order={"fixture": 1},
            istd_rts_by_sample={},
        ),
    )
    result = results[target.label]
    assert result.reported_rt == pytest.approx(16.4, abs=0.01)
    assert result.reported_peak_area is not None and result.reported_peak_area > 0
    assert result.physical_evaluation is not None
    assert result.physical_evaluation.state == "available"
    assert result.candidate_ms2_evidence is not None
    assert result.candidate_ms2_evidence.nl_status == "OK"


def test_paired_analyte_fallback_window_uses_completed_current_reference(
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
        "xic_extractor.extraction.rt_windows.find_nl_anchor_rt",
        lambda *_args, **_kwargs: None,
    )

    rt_min, rt_max, anchor_used, anchor_rt = get_rt_window(
        object(),
        target,
        config,
        sample_key="fixture",
        paired_istd_credible=True,
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
        "xic_extractor.extraction.rt_windows.find_nl_anchor_rt",
        lambda *_args, **_kwargs: 18.20,
    )

    rt_min, rt_max, anchor_used, anchor_rt = get_rt_window(
        object(),
        target,
        config,
        sample_key="fixture",
        paired_istd_credible=True,
        target_reference_rt=16.45,
    )

    assert (rt_min, rt_max) == pytest.approx((16.05, 16.85))
    assert anchor_used is False
    assert anchor_rt == pytest.approx(16.45)


def test_unpaired_target_keeps_nl_anchor_without_pair_distance_guard(
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
        "xic_extractor.extraction.rt_windows.find_nl_anchor_rt",
        lambda *_args, **_kwargs: 18.20,
    )

    rt_min, rt_max, anchor_used, anchor_rt = get_rt_window(
        object(),
        target,
        config,
        sample_key="fixture",
        paired_istd_credible=False,
        target_reference_rt=17.0,
    )

    assert (rt_min, rt_max) == pytest.approx((17.2, 19.2))
    assert anchor_used is True
    assert anchor_rt == pytest.approx(18.20)


@pytest.mark.parametrize("neutral_loss", [None, 116.0474])
@pytest.mark.parametrize("offset", [0.2, -0.15, 0.0])
@pytest.mark.parametrize("pair_label", ["ISTD", "d3-Analyte"])
def test_current_pair_reference_preserves_method_offset_for_all_lanes(
    neutral_loss,
    offset,
    pair_label,
) -> None:
    target = Target(
        label="Analyte",
        mz=258.1085,
        rt_min=10.0 + offset - 1.0,
        rt_max=10.0 + offset + 1.0,
        ppm_tol=20.0,
        neutral_loss_da=neutral_loss,
        nl_ppm_warn=20.0,
        nl_ppm_max=50.0,
        is_istd=False,
        istd_pair=pair_label,
    )
    paired = replace(
        target, label=pair_label, rt_min=9.0, rt_max=11.0, is_istd=True, istd_pair=""
    )
    reference = target_extraction.current_target_reference_rt(
        target,
        reference_rt=10.5,
        paired_target=paired,
    )
    assert reference == pytest.approx(10.5 + offset)
    assert target_extraction.current_target_reference_rt(
        target,
        reference_rt=None,
        paired_target=paired,
        sample_drift=0.3,
    ) == pytest.approx(10.3 + offset)
    assert target_extraction.current_target_reference_rt(
        target,
        reference_rt=None,
        paired_target=paired,
    ) == pytest.approx(10.0 + offset)


@pytest.mark.parametrize("neutral_loss_da", [None, 116.047344])
def test_same_paired_method_selects_each_samples_current_shift(
    tmp_path, neutral_loss_da: float | None
) -> None:
    config = ExtractionConfig(
        data_dir=tmp_path,
        dll_dir=tmp_path,
        output_csv=tmp_path / "results.csv",
        diagnostics_csv=tmp_path / "diagnostics.csv",
        smooth_window=15,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.1,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
        nl_fallback_half_window_min=0.25,
    )
    target = Target(
        label="Analyte",
        mz=258.1085,
        rt_min=10.5,
        rt_max=12.5,
        ppm_tol=20.0,
        neutral_loss_da=neutral_loss_da,
        nl_ppm_warn=10.0 if neutral_loss_da is not None else None,
        nl_ppm_max=20.0 if neutral_loss_da is not None else None,
        is_istd=False,
        istd_pair="ISTD",
    )
    paired = replace(
        target, label="ISTD", is_istd=True, istd_pair="", rt_min=9.0, rt_max=11.0
    )
    rt = np.arange(7.0, 16.0001, 0.01)
    # Identical competing physical peaks in both samples: neither trace height
    # nor a cached previous sample can choose the answer. The independently
    # specified apex follows observed ISTD drift plus the given 1.5-min offset.
    intensity = 100.0 + sum(
        10_000.0 * np.exp(-0.5 * ((rt - center) / 0.06) ** 2) for center in (11.2, 11.9)
    )

    class Raw:
        def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
            mask = (rt >= rt_min - 1e-9) & (rt <= rt_max + 1e-9)
            return rt[mask], intensity[mask]

        def iter_ms2_scans(self, rt_min, rt_max):
            return iter(())

    for sample, observed_istd, expected in (
        ("SampleA", 10.4, 11.9),
        ("SampleB", 9.7, 11.2),
    ):
        istd = ExtractionResult(
            peak_result=_peak_result(_candidate(rt=observed_istd)),
            nl=None,
            target_label="ISTD",
            role="ISTD",
        )
        reference = target_extraction.credible_istd_anchor_rt(istd)
        assert reference == observed_istd
        results = {"ISTD": istd}
        consumer_reference = target_extraction.extract_one_target(
            Raw(),
            config,
            sample,
            target,
            reference_rt=reference,
            paired_target=paired,
            strict_preferred_rt=True,
            istd_rt_in_this_sample=reference,
            results=results,
            diagnostics=[],
        )
        assert consumer_reference == pytest.approx(expected)
        assert results["Analyte"].reported_rt == pytest.approx(expected, abs=0.01)


@pytest.fixture
def physical_candidate_input() -> tuple[PreparedSignalResult, PhysicalSegmentUniverse]:
    profile = DEFAULT_SIGNAL_PROCESSING_PROFILE
    projection = PreparedSignalProjection(
        schema_version="prepared_signal",
        trace_content_id="trace:test",
        profile_id=profile.profile_id,
        prepared_signal_id="prepared:test",
        trace_point_count=7,
        state="available",
        reason="available",
    )
    # Both test segments have acquired flanks outside their intervals.
    prepared = PreparedSignal(
        projection=projection,
        profile=profile,
        rt=(-1.0, 0.0, 1.0, 2.0, 3.0, 4.0, 5.0),
        raw=(10.0, 11.0, 60.0, 21.0, 90.0, 14.0, 10.0),
        baseline=(10.0,) * 7,
        smoothed=(10.0, 12.0, 45.0, 13.0, 75.0, 11.0, 10.0),
        positive_signal=(0.0, 2.0, 35.0, 3.0, 65.0, 1.0, 0.0),
    )
    segments = (
        PhysicalSegment(
            physical_segment_id="segment:later",
            start_index=3,
            end_index=6,
            apex_index=4,
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
            start_index=1,
            end_index=4,
            apex_index=2,
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
            context_rt_start_min=-1.0,
            context_rt_end_min=5.0,
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


@pytest.mark.parametrize(
    "trace_case", ["clipped", "repeated_clipped", "complete", "remote_clipped"]
)
def test_target_reacquires_only_clipped_peak_inside_original_selection(
    tmp_path, monkeypatch, trace_case: str
) -> None:
    config = ExtractionConfig(
        data_dir=tmp_path,
        dll_dir=tmp_path,
        output_csv=tmp_path / "results.csv",
        diagnostics_csv=tmp_path / "diagnostics.csv",
        smooth_window=15,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.1,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
        nl_fallback_half_window_min=0.25,
    )
    target = Target(
        label="Analyte",
        mz=258.1085,
        rt_min=8.0,
        rt_max=10.0,
        ppm_tol=20.0,
        neutral_loss_da=116.047344,
        nl_ppm_warn=10.0,
        nl_ppm_max=20.0,
        is_istd=False,
        istd_pair="ISTD",
    )
    full_rt = np.arange(3.0, 18.0001, 0.02)
    center = 10.6 if trace_case == "remote_clipped" else 9.94
    offset = full_rt - center
    sigma = np.where(offset < 0.0, 0.2, 4.0)
    if trace_case == "complete":
        center = 9.8
        offset = full_rt - center
        sigma = np.full_like(full_rt, 0.08)
    full_intensity = 100.0 + 10_000.0 * np.exp(-0.5 * (offset / sigma) ** 2)

    class Raw:
        def __init__(self):
            self.requests = []
            self.traces = []
            self.ms2_windows = []

        def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
            self.requests.append((mz, rt_min, rt_max, ppm_tol))
            mask = (full_rt >= rt_min - 1e-9) & (full_rt <= rt_max + 1e-9)
            trace = (full_rt[mask], full_intensity[mask])
            if trace_case == "repeated_clipped" and self.traces:
                trace = self.traces[0]
            self.traces.append(trace)
            return trace

        def iter_ms2_scans(self, rt_min, rt_max):
            self.ms2_windows.append((rt_min, rt_max))
            return iter(())

    raw = Raw()
    calls = {"prepare": 0, "enumerate": 0}
    preparations = []
    universes = []
    real_prepare = target_extraction.prepare_physical_signal
    real_enumerate = target_extraction.enumerate_physical_segment_universe

    def prepare(*args, **kwargs):
        calls["prepare"] += 1
        result = real_prepare(*args, **kwargs)
        preparations.append(result)
        return result

    def enumerate_segments(*args, **kwargs):
        calls["enumerate"] += 1
        result = real_enumerate(*args, **kwargs)
        universes.append(result)
        return result

    monkeypatch.setattr(target_extraction, "prepare_physical_signal", prepare)
    monkeypatch.setattr(
        target_extraction, "enumerate_physical_segment_universe", enumerate_segments
    )
    results = {}
    anchor = target_extraction.extract_one_target(
        raw,
        config,
        "Sample",
        target,
        reference_rt=9.75,
        paired_target=replace(target, label="ISTD", is_istd=True, istd_pair=""),
        strict_preferred_rt=True,
        istd_rt_in_this_sample=9.75,
        results=results,
        diagnostics=[],
    )
    result = results[target.label]
    assert anchor == 9.75
    assert raw.requests[0] == (target.mz, 6.0, 12.0, target.ppm_tol)
    assert (8.0, 10.0) in raw.ms2_windows
    initial = universes[0]
    eligible = tuple(s for s in initial.segments if 9.5 <= s.apex_rt_min <= 10.0)
    clipped = tuple(
        s
        for s in eligible
        if s.start_index == 0
        or s.end_index == preparations[0].projection.trace_point_count
    )
    needs_extension = trace_case in {"clipped", "repeated_clipped"}
    assert bool(clipped) is needs_extension
    assert len(raw.requests) == (2 if needs_extension else 1)
    expected_preparations = 2 if trace_case == "clipped" else 1
    assert calls == {
        "prepare": expected_preparations,
        "enumerate": expected_preparations,
    }
    if needs_extension:
        assert physical_candidates_result(preparations[0], initial).candidates == ()
        assert raw.requests[1] == (target.mz, 4.0, 14.0, target.ppm_tol)
    assert result.acquisition_trace is not None
    assert result.acquisition_trace.rt_min == raw.requests[-1][1]
    assert result.acquisition_trace.rt_max == raw.requests[-1][2]
    np.testing.assert_array_equal(result.acquisition_trace.rt, raw.traces[-1][0])
    np.testing.assert_array_equal(result.acquisition_trace.intensity, raw.traces[-1][1])
    assert result.physical_evaluation is not None
    if trace_case in {"repeated_clipped", "remote_clipped"}:
        assert result.peak is None
        assert result.physical_evaluation.state == "unavailable"
        assert result.physical_evaluation.facts is None
        assert result.reported_peak_area is None
    else:
        assert result.peak is not None
        assert 9.5 <= result.peak.rt <= 10.0
        assert result.peak.rt == pytest.approx(center, abs=0.08)
        facts = result.physical_evaluation.facts
        assert facts is not None
        assert result.selected_hypothesis is not None
        assert (
            result.selected_hypothesis.integration.area_ms1_morphology
            == facts.area_counts_seconds
        )
        if trace_case == "clipped":
            assert facts.right_rt_min > 12.0
