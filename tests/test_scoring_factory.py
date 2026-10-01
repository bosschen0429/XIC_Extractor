from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.extraction.rt_prior_library import LibraryEntry
from xic_extractor.extraction.scoring_factory import (
    allow_prepass_anchor,
    build_scoring_context_factory,
    paired_istd_fwhm,
    selected_candidate,
    selected_shape_metrics,
)
from xic_extractor.peak_detection.baseline import asls_baseline, asls_baseline_on_rt
from xic_extractor.peak_detection.evidence_facts import build_candidate_evidence_facts
from xic_extractor.peak_detection.models import (
    PeakCandidate,
    PeakDetectionResult,
    PeakResult,
)
from xic_extractor.peak_detection.ms1_morphology import (
    compute_shape_metrics,
    gaussian15_positive_asls_residual_trace,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    prepare_physical_signal,
)
from xic_extractor.peak_detection.scoring_metrics import local_sn_severity


def test_factory_uses_delta_rt_library_and_exposes_prior_metadata() -> None:
    factory = build_scoring_context_factory(
        config=_config(dirty_matrix_mode=True),
        injection_order={},
        istd_rts_by_sample={},
        rt_prior_library={
            ("Analyte-A", "analyte"): LibraryEntry(
                config_hash="abcd1234",
                target_label="Analyte-A",
                role="analyte",
                istd_pair="ISTD-A",
                median_delta_rt=0.25,
                sigma_delta_rt=0.05,
                median_abs_rt=None,
                sigma_abs_rt=None,
                n_samples=10,
                updated_at="2026-04-20T00:00:00",
            )
        },
    )

    builder = factory(
        target=_target(label="Analyte-A", is_istd=False, istd_pair="ISTD-A"),
        sample_name="S2",
        rt=np.linspace(9.9, 10.5, 7),
        intensity=np.array([0.0, 1.0, 4.0, 8.0, 4.0, 1.0, 0.0]),
        prepared_result=_prepared(
            np.linspace(9.9, 10.5, 7), np.array([0.0, 1.0, 4.0, 8.0, 4.0, 1.0, 0.0])
        ),
        istd_rt_in_this_sample=10.0,
        paired_istd_fwhm=2.0,
        nl_result=None,
    )

    ctx = builder(_candidate(apex_index=3))

    assert ctx.rt_prior == pytest.approx(10.25)
    assert ctx.rt_prior_sigma == pytest.approx(0.05)
    assert ctx.dirty_matrix is True
    assert ctx.prefer_rt_prior_tiebreak is True
    assert getattr(builder, "rt_prior") == pytest.approx(10.25)
    assert getattr(builder, "prior_source") == "delta_rt_library"


def test_selected_candidate_and_prepass_anchor_reject_quality_flags() -> None:
    candidate = _candidate(apex_index=1, quality_flags=("too_broad",))
    peak_result = PeakDetectionResult(
        status="OK",
        peak=candidate.peak,
        n_points=3,
        max_smoothed=10.0,
        n_prominent_peaks=1,
        candidates=(candidate,),
    )

    assert selected_candidate(peak_result) is candidate
    assert allow_prepass_anchor(peak_result) is False


def test_prepass_anchor_allows_soft_adap_like_quality_flags() -> None:
    candidate = _candidate(
        apex_index=1,
        quality_flags=("low_trace_continuity", "poor_edge_recovery"),
    )
    peak_result = PeakDetectionResult(
        status="OK",
        peak=candidate.peak,
        candidates=(candidate,),
        n_points=3,
        max_smoothed=20.0,
        n_prominent_peaks=1,
    )

    assert selected_candidate(peak_result) is candidate
    assert allow_prepass_anchor(peak_result) is True


def test_prepass_anchor_allows_adap_equivalent_legacy_flags() -> None:
    candidate = _candidate(
        apex_index=1,
        quality_flags=(
            "low_scan_count",
            "low_scan_support",
            "low_top_edge_ratio",
            "poor_edge_recovery",
        ),
    )
    peak_result = PeakDetectionResult(
        status="OK",
        peak=candidate.peak,
        candidates=(candidate,),
        n_points=3,
        max_smoothed=20.0,
        n_prominent_peaks=1,
    )

    assert selected_candidate(peak_result) is candidate
    assert allow_prepass_anchor(peak_result) is True


@pytest.mark.parametrize("window_points", [7, 15])
@pytest.mark.parametrize("sample_count", [101, 301, 901])
def test_same_prepared_shape_is_shared_by_analyte_and_selected_istd(
    window_points: int, sample_count: int
) -> None:
    target = _target(label="Analyte-A", is_istd=False, istd_pair="ISTD-A")
    rt = np.linspace(0.0, 2.0, sample_count)
    intensity = _gaussian(rt) + 20.0 + 5.0 * rt
    prepared_result = prepare_physical_signal(
        rt, intensity, profile=SignalProcessingProfile(window_points=window_points)
    )
    candidate = _candidate(apex_index=sample_count // 2)
    metrics = selected_shape_metrics(
        prepared_result, _peak_result(candidate, sample_count)
    )
    assert metrics is not None
    factory = build_scoring_context_factory(
        config=_config(signal_processing_window_points=window_points),
        injection_order={},
        istd_rts_by_sample={},
        rt_prior_library={},
    )
    ctx = factory(
        target=target,
        sample_name="SampleA",
        rt=rt,
        intensity=intensity,
        prepared_result=prepared_result,
        istd_rt_in_this_sample=1.0,
        paired_istd_fwhm=paired_istd_fwhm(target, {"ISTD-A": metrics}),
        nl_result=None,
    )(candidate)
    prepared = prepared_result.prepared
    assert prepared is not None
    expected = compute_shape_metrics(
        rt, np.asarray(prepared.positive_signal), candidate.selection_apex_index
    )
    assert metrics == expected
    assert ctx.half_width_ratio == metrics[0]
    assert ctx.fwhm_ratio == 1.0
    assert build_candidate_evidence_facts(candidate, ctx).trace.width_quality == "clean"
    # These carriers describe the existing S/N view, independent of shape.
    assert ctx.active_trace_source == "gaussian15_positive_asls_residual"


def test_missing_paired_width_retains_neutral_width_quality() -> None:
    rt = np.linspace(0.0, 2.0, 301)
    target = _target(label="Analyte-A", is_istd=False, istd_pair="ISTD-A")
    candidate = _candidate(apex_index=150)
    factory = build_scoring_context_factory(
        config=_config(),
        injection_order={},
        istd_rts_by_sample={},
        rt_prior_library={},
    )
    ctx = factory(
        target=target,
        sample_name="SampleA",
        rt=rt,
        intensity=_gaussian(rt),
        prepared_result=_prepared(rt, _gaussian(rt)),
        istd_rt_in_this_sample=1.0,
        paired_istd_fwhm=paired_istd_fwhm(target, {}),
        nl_result=None,
    )(candidate)

    assert ctx.fwhm_ratio is None
    assert build_candidate_evidence_facts(candidate, ctx).trace.width_quality == "clean"


@pytest.mark.parametrize(
    "window_points,sample_count",
    [(window, count) for window in (7, 15) for count in range(5, window)],
)
def test_missing_prepared_shape_keeps_legacy_active_sn(
    window_points: int, sample_count: int
) -> None:
    rt = np.linspace(0.0, 2.0, sample_count)
    intensity = _gaussian(rt)
    prepared_result = prepare_physical_signal(
        rt, intensity, profile=SignalProcessingProfile(window_points=window_points)
    )
    assert prepared_result.prepared is None
    assert prepared_result.baseline_unclipped is None
    candidate = _candidate(apex_index=sample_count // 2)
    factory = build_scoring_context_factory(
        config=_config(), injection_order={}, istd_rts_by_sample={}, rt_prior_library={}
    )
    ctx = factory(
        target=_target(label="Analyte-A", is_istd=False, istd_pair="ISTD-A"),
        sample_name="SampleA",
        rt=rt,
        intensity=intensity,
        prepared_result=prepared_result,
        istd_rt_in_this_sample=None,
        paired_istd_fwhm=0.2,
        nl_result=None,
    )(candidate)
    assert ctx.half_width_ratio == 1.0
    assert ctx.fwhm_ratio is None
    expected_baseline = asls_baseline(intensity)
    residual = intensity - expected_baseline
    expected_mad = float(np.median(np.abs(residual - np.median(residual))))
    np.testing.assert_array_equal(
        ctx.intensity_array,
        gaussian15_positive_asls_residual_trace(intensity, expected_baseline),
    )
    np.testing.assert_array_equal(ctx.baseline_array, np.zeros(sample_count))
    assert ctx.residual_mad == expected_mad
    assert ctx.active_trace_source == "gaussian15_positive_asls_residual"
    assert (
        selected_shape_metrics(prepared_result, _peak_result(candidate, len(rt)))
        is None
    )
    available = _prepared(rt, intensity)
    no_peak = PeakDetectionResult(
        status="PEAK_NOT_FOUND",
        peak=None,
        n_points=len(rt),
        max_smoothed=0.0,
        n_prominent_peaks=0,
        candidates=(),
    )
    assert selected_shape_metrics(available, no_peak) is None


@pytest.mark.parametrize("window_points", [7, 15])
@pytest.mark.parametrize("dirty_matrix", [False, True])
def test_rebuilt_factories_reuse_unclipped_rt_baseline_with_expected_sn(
    monkeypatch: pytest.MonkeyPatch, window_points: int, dirty_matrix: bool
) -> None:
    import xic_extractor.peak_detection.physical_peak_evaluation as physical_owner
    import xic_extractor.peak_detection.scoring_metrics as sn_owner

    rt = np.linspace(0.0, 2.0, 301)
    intensity = 100.0 * np.exp(-0.5 * ((rt - 1.0) / 0.1) ** 2)
    intensity += np.random.default_rng(20260926).normal(0.0, 1.0, len(rt))
    expected_baseline = _uniform_rt_baseline_oracle(rt, intensity)
    assert np.any(expected_baseline < 0.0)
    residual = intensity - expected_baseline
    expected_mad = float(np.median(np.abs(residual - np.median(residual))))
    expected_active = gaussian15_positive_asls_residual_trace(
        intensity, expected_baseline
    )
    clipped_residual = intensity - np.maximum(expected_baseline, 0.0)
    clipped_mad = float(
        np.median(np.abs(clipped_residual - np.median(clipped_residual)))
    )
    assert clipped_mad != pytest.approx(expected_mad)
    solves = {"physical": 0, "sn": 0}

    def counted_physical(*args, **kwargs):
        solves["physical"] += 1
        return asls_baseline_on_rt(*args, **kwargs)

    def counted_sn(*args, **kwargs):
        solves["sn"] += 1
        return asls_baseline(*args, **kwargs)

    monkeypatch.setattr(physical_owner, "asls_baseline_on_rt", counted_physical)
    monkeypatch.setattr(sn_owner, "asls_baseline", counted_sn)
    prepared_result = prepare_physical_signal(
        rt, intensity, profile=SignalProcessingProfile(window_points=window_points)
    )
    assert prepared_result.prepared is not None
    np.testing.assert_allclose(
        prepared_result.prepared.baseline, np.maximum(expected_baseline, 0.0),
        rtol=2e-6, atol=1e-7,
    )
    for _ in range(2):
        factory = build_scoring_context_factory(
            config=_config(dirty_matrix_mode=dirty_matrix),
            injection_order={}, istd_rts_by_sample={}, rt_prior_library={},
        )
        builder = factory(
            target=_target(label="Analyte-A", is_istd=False, istd_pair=""),
            sample_name="SampleA", rt=rt, intensity=intensity,
            prepared_result=prepared_result, istd_rt_in_this_sample=None,
            paired_istd_fwhm=None, nl_result=None,
        )
        for apex in (150, 151):
            candidate = _candidate(apex_index=apex)
            ctx = builder(candidate)
            np.testing.assert_allclose(
                ctx.intensity_array, expected_active, rtol=2e-6, atol=1e-7
            )
            np.testing.assert_array_equal(ctx.baseline_array, np.zeros(len(rt)))
            assert ctx.residual_mad == pytest.approx(expected_mad, rel=2e-6)
            assert ctx.active_trace_source == "gaussian15_positive_asls_residual"
            assert ctx.morphology_trace_method == "gaussian_15"
            assert ctx.morphology_trace_window_points == 15
            facts = build_candidate_evidence_facts(candidate, ctx).trace
            assert facts.local_sn_ratio == pytest.approx(
                expected_active[apex] / expected_mad, rel=2e-6
            )
            assert local_sn_severity(
                ctx.intensity_array, apex, dirty_matrix,
                baseline=ctx.baseline_array, residual_mad=ctx.residual_mad,
            ) == local_sn_severity(
                expected_active, apex, dirty_matrix,
                baseline=np.zeros(len(rt)), residual_mad=expected_mad,
            )
    np.testing.assert_allclose(
        prepared_result.baseline_unclipped, expected_baseline, rtol=2e-6, atol=1e-7
    )
    live_residual = intensity - np.asarray(prepared_result.baseline_unclipped)
    live_mad = float(np.median(np.abs(live_residual - np.median(live_residual))))
    assert ctx.residual_mad == live_mad
    assert solves == {"physical": 1, "sn": 0}


@pytest.mark.parametrize(
    "absence",
    ["asls_failure", "physical_smoothing_failure", "nonpositive_raw", "restored"],
)
def test_missing_live_baseline_keeps_standalone_sn(
    monkeypatch: pytest.MonkeyPatch, absence: str
) -> None:
    import xic_extractor.peak_detection.physical_peak_evaluation as physical_owner
    import xic_extractor.peak_detection.scoring_metrics as sn_owner

    rt = np.linspace(0.0, 2.0, 31)
    intensity = _gaussian(rt)
    if absence == "nonpositive_raw":
        intensity = -np.linspace(1.0, 2.0, len(rt))
    expected_baseline = asls_baseline(intensity)
    residual = intensity - expected_baseline
    expected_mad = float(np.median(np.abs(residual - np.median(residual))))

    def failed_owner(*_args, **_kwargs):
        raise ValueError("controlled owner failure")

    if absence == "asls_failure":
        monkeypatch.setattr(physical_owner, "asls_baseline_on_rt", failed_owner)
    elif absence == "physical_smoothing_failure":
        monkeypatch.setattr(
            physical_owner, "canonical_gaussian_signal_traces", failed_owner
        )
    prepared_result = _prepared(rt, intensity)
    if absence == "restored":
        prepared_result = replace(prepared_result, baseline_unclipped=None)
    assert prepared_result.baseline_unclipped is None
    if absence != "restored":
        assert prepared_result.prepared is None
    calls = 0

    def counted_sn(*args, **kwargs):
        nonlocal calls
        calls += 1
        return asls_baseline(*args, **kwargs)

    monkeypatch.setattr(sn_owner, "asls_baseline", counted_sn)
    factory = build_scoring_context_factory(
        config=_config(), injection_order={}, istd_rts_by_sample={}, rt_prior_library={}
    )
    ctx = factory(
        target=_target(label="Analyte-A", is_istd=False, istd_pair=""),
        sample_name="SampleA", rt=rt, intensity=intensity,
        prepared_result=prepared_result, istd_rt_in_this_sample=None,
        paired_istd_fwhm=None, nl_result=None,
    )(_candidate(apex_index=15))
    np.testing.assert_array_equal(
        ctx.intensity_array,
        gaussian15_positive_asls_residual_trace(intensity, expected_baseline),
    )
    assert ctx.residual_mad == expected_mad
    assert calls == 1


@pytest.mark.parametrize(
    "intensity",
    [np.array([0.0, 1.0, 0.0]), np.array([0.0, 1.0, np.nan, 1.0, 0.0])],
)
def test_missing_sn_cache_keeps_raw_context(intensity: np.ndarray) -> None:
    rt = np.arange(len(intensity), dtype=float)
    factory = build_scoring_context_factory(
        config=_config(), injection_order={}, istd_rts_by_sample={}, rt_prior_library={}
    )
    ctx = factory(
        target=_target(label="Analyte-A", is_istd=False, istd_pair=""),
        sample_name="SampleA", rt=rt, intensity=intensity,
        prepared_result=_prepared(rt, intensity), istd_rt_in_this_sample=None,
        paired_istd_fwhm=None, nl_result=None,
    )(_candidate(apex_index=1))
    np.testing.assert_array_equal(ctx.intensity_array, intensity)
    assert ctx.baseline_array is None
    assert ctx.residual_mad is None
    assert ctx.active_trace_source == "raw"
    assert ctx.morphology_trace_method == ""
    assert ctx.morphology_trace_window_points is None


@pytest.mark.parametrize("error", [TypeError, ValueError, FloatingPointError])
def test_scoring_smoothing_failure_retains_raw_sn_fallback(
    monkeypatch: pytest.MonkeyPatch, error: type[Exception]
) -> None:
    import xic_extractor.extraction.scoring_factory as factory_owner

    rt = np.linspace(0.0, 2.0, 31)
    intensity = _gaussian(rt)
    expected_baseline = _uniform_rt_baseline_oracle(rt, intensity)
    residual = intensity - expected_baseline
    expected_mad = float(np.median(np.abs(residual - np.median(residual))))
    prepared_result = _prepared(rt, intensity)

    def fail_smoothing(*_args, **_kwargs):
        raise error("controlled smoothing failure")

    monkeypatch.setattr(
        factory_owner, "gaussian15_positive_asls_residual_trace", fail_smoothing
    )
    factory = build_scoring_context_factory(
        config=_config(), injection_order={}, istd_rts_by_sample={}, rt_prior_library={}
    )
    ctx = factory(
        target=_target(label="Analyte-A", is_istd=False, istd_pair=""),
        sample_name="SampleA", rt=rt, intensity=intensity,
        prepared_result=prepared_result, istd_rt_in_this_sample=None,
        paired_istd_fwhm=None, nl_result=None,
    )(_candidate(apex_index=15))
    np.testing.assert_array_equal(ctx.intensity_array, intensity)
    np.testing.assert_allclose(
        ctx.baseline_array, expected_baseline, rtol=2e-6, atol=1e-7
    )
    assert ctx.residual_mad == pytest.approx(expected_mad, rel=2e-6)
    assert ctx.active_trace_source == "raw"
    assert ctx.morphology_trace_method == ""
    assert ctx.morphology_trace_window_points is None


def _uniform_rt_baseline_oracle(rt: np.ndarray, intensity: np.ndarray) -> np.ndarray:
    """Independent dense normal equations for the uniform RT AsLS objective."""
    spacing = float(rt[1] - rt[0])
    np.testing.assert_allclose(np.diff(rt), spacing, rtol=1e-12)
    curvature = np.diff(np.eye(len(rt)), n=2, axis=0)
    # lambda = 1 minutes^4; trapezoidal data cells halve the endpoint weights.
    penalty = (curvature.T @ curvature) / spacing**4
    cells = np.ones(len(rt))
    cells[[0, -1]] = 0.5
    weights = np.ones(len(rt))
    for _ in range(10):
        data_weights = cells * weights
        baseline = np.linalg.solve(
            penalty + np.diag(data_weights), data_weights * intensity
        )
        weights = np.where(intensity > baseline, 0.01, 0.99)
    return baseline


def _gaussian(rt: np.ndarray, *, sigma: float = 0.1) -> np.ndarray:
    return 1000.0 * np.exp(-0.5 * ((rt - 1.0) / sigma) ** 2)


def _peak_result(candidate: PeakCandidate, sample_count: int) -> PeakDetectionResult:
    return PeakDetectionResult(
        status="OK",
        peak=candidate.peak,
        n_points=sample_count,
        max_smoothed=1000.0,
        n_prominent_peaks=1,
        candidates=(candidate,),
    )


def _config(**overrides: object) -> ExtractionConfig:
    config = ExtractionConfig(
        data_dir=Path("."),
        dll_dir=Path("."),
        output_csv=Path("out.csv"),
        diagnostics_csv=Path("diag.csv"),
        smooth_window=7,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.1,
        ms2_precursor_tol_da=1.6,
        nl_min_intensity_ratio=0.01,
        injection_order_source=None,
        rolling_window_size=1,
        dirty_matrix_mode=False,
        rt_prior_library_path=None,
        emit_score_breakdown=False,
        config_hash="abcd1234",
    )
    return ExtractionConfig(**{**config.__dict__, **overrides})


def _target(*, label: str, is_istd: bool, istd_pair: str) -> Target:
    return Target(
        label=label,
        mz=100.0,
        rt_min=9.0,
        rt_max=11.0,
        ppm_tol=20.0,
        neutral_loss_da=116.0474,
        nl_ppm_warn=20.0,
        nl_ppm_max=50.0,
        is_istd=is_istd,
        istd_pair=istd_pair,
    )


def _candidate(
    *, apex_index: int, quality_flags: tuple[str, ...] = ()
) -> PeakCandidate:
    peak = PeakResult(
        rt=10.0,
        intensity=8.0,
        intensity_smoothed=8.0,
        area=20.0,
        peak_start=9.9,
        peak_end=10.1,
    )
    candidate = PeakCandidate(
        peak=peak,
        selection_apex_rt=10.0,
        selection_apex_intensity=8.0,
        selection_apex_index=apex_index,
        raw_apex_rt=10.0,
        raw_apex_intensity=8.0,
        raw_apex_index=apex_index,
        prominence=7.0,
    )
    if quality_flags:
        object.__setattr__(candidate, "quality_flags", quality_flags)
    return candidate


def _prepared(rt: np.ndarray, intensity: np.ndarray):
    return prepare_physical_signal(
        rt, intensity, profile=SignalProcessingProfile(window_points=3)
    )
