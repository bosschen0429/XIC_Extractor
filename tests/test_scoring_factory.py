from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.extraction.scoring_factory import (
    build_scoring_context_factory,
    is_credible_istd_peak,
    paired_istd_fwhm,
    selected_candidate,
    selected_shape_metrics,
)
from xic_extractor.peak_detection.baseline import asls_baseline_on_rt
from xic_extractor.peak_detection.evidence_facts import build_candidate_evidence_facts
from xic_extractor.peak_detection.models import (
    PeakCandidate,
    PeakDetectionResult,
    PeakResult,
)
from xic_extractor.peak_detection.ms1_morphology import (
    compute_shape_metrics,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    prepare_physical_signal,
)
from xic_extractor.peak_detection.signal_quality import (
    candidate_signal_quality,
    short_scale_noise_sigma,
)


@pytest.mark.parametrize(
    "sample,anchor,reference", [("S1", 11.8, 12.0), ("S2", 13.8, 14.0)]
)
def test_factory_scores_analyte_against_current_sample_reference(
    sample, anchor, reference
):
    factory = build_scoring_context_factory(
        config=_config(),
    )
    rt = np.linspace(reference - 0.3, reference + 0.3, 7)
    intensity = np.array([0.0, 1.0, 4.0, 8.0, 4.0, 1.0, 0.0])
    builder = factory(
        target=_target(label="Analyte-A", is_istd=False, istd_pair="ISTD-A"),
        sample_name=sample,
        rt=rt,
        intensity=intensity,
        prepared_result=_prepared(rt, intensity),
        istd_rt_in_this_sample=anchor,
        target_reference_rt=reference,
        paired_istd_fwhm=2.0,
    )
    candidate = replace(_candidate(apex_index=3), selection_apex_rt=reference)
    ctx = builder(candidate)
    assert ctx.rt_prior == reference
    assert (ctx.rt_min, ctx.rt_max) == (rt[0], rt[-1])
    assert ctx.rt_prior_sigma is None
    assert builder.prior_source == "paired_istd"
    facts = build_candidate_evidence_facts(candidate, ctx)
    assert facts.rt.rt_prior_status == "close"
    assert facts.rt.window_status != "outside"
    far = replace(candidate, selection_apex_rt=reference - 1.5)
    assert build_candidate_evidence_facts(far, builder(far)).rt.rt_prior_status == "far"


def test_selected_candidate_and_credible_istd_peak_reject_quality_flags() -> None:
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
    assert is_credible_istd_peak(peak_result) is False


def test_credible_istd_peak_allows_soft_adap_like_quality_flags() -> None:
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
    assert is_credible_istd_peak(peak_result) is True


def test_credible_istd_peak_allows_adap_equivalent_legacy_flags() -> None:
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
    assert is_credible_istd_peak(peak_result) is True


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
    )
    ctx = factory(
        target=target,
        sample_name="SampleA",
        rt=rt,
        intensity=intensity,
        prepared_result=prepared_result,
        istd_rt_in_this_sample=1.0,
        paired_istd_fwhm=paired_istd_fwhm(target, {"ISTD-A": metrics}),
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
    )
    ctx = factory(
        target=target,
        sample_name="SampleA",
        rt=rt,
        intensity=_gaussian(rt),
        prepared_result=_prepared(rt, _gaussian(rt)),
        istd_rt_in_this_sample=1.0,
        paired_istd_fwhm=paired_istd_fwhm(target, {}),
    )(candidate)

    assert ctx.fwhm_ratio is None
    assert build_candidate_evidence_facts(candidate, ctx).trace.width_quality == "clean"


@pytest.mark.parametrize(
    "window_points,sample_count",
    [(window, count) for window in (7, 15) for count in range(5, window)],
)
def test_missing_prepared_shape_keeps_quality_unknown(
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
    factory = build_scoring_context_factory(config=_config())
    ctx = factory(
        target=_target(label="Analyte-A", is_istd=False, istd_pair="ISTD-A"),
        sample_name="SampleA",
        rt=rt,
        intensity=intensity,
        prepared_result=prepared_result,
        istd_rt_in_this_sample=None,
        paired_istd_fwhm=0.2,
    )(candidate)
    assert ctx.half_width_ratio == 1.0
    assert ctx.fwhm_ratio is None
    np.testing.assert_array_equal(ctx.intensity_array, intensity)
    assert ctx.signal_quality is not None
    assert ctx.signal_quality.signal_to_noise is None
    assert ctx.active_trace_source == "raw"
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
def test_rebuilt_factories_share_physical_baseline_and_raw_noise(
    monkeypatch, window_points
):
    import xic_extractor.peak_detection.physical_peak_evaluation as physical_owner

    rt = np.linspace(0.0, 2.0, 301)
    raw = _gaussian(rt) + np.random.default_rng(42).normal(0.0, 1.0, len(rt))
    solves = 0

    def counted(*args, **kwargs):
        nonlocal solves
        solves += 1
        return asls_baseline_on_rt(*args, **kwargs)

    monkeypatch.setattr(physical_owner, "asls_baseline_on_rt", counted)
    result = prepare_physical_signal(
        rt, raw, profile=SignalProcessingProfile(window_points=window_points)
    )
    assert result.prepared is not None
    for _ in range(2):
        factory = build_scoring_context_factory(config=_config())
        builder = factory(
            target=_target(label="A", is_istd=False, istd_pair=""),
            sample_name="S",
            rt=rt,
            intensity=raw,
            prepared_result=result,
            istd_rt_in_this_sample=None,
            paired_istd_fwhm=None,
        )
        for apex in (150, 151):
            candidate = _quality_candidate(apex)
            ctx = builder(candidate)
            expected = candidate_signal_quality(
                candidate, rt, raw, np.asarray(result.prepared.baseline)
            )
            assert ctx.signal_quality == expected
            assert expected.noise_sigma == short_scale_noise_sigma(rt, raw)
            assert expected.signal_to_noise == pytest.approx(
                max(0.0, raw[apex] - result.prepared.baseline[apex])
                / expected.noise_sigma
            )
            assert (
                build_candidate_evidence_facts(candidate, ctx).trace.local_sn_ratio
                == expected.signal_to_noise
            )
    assert solves == 1


@pytest.mark.parametrize(
    "absence",
    ["asls_failure", "physical_smoothing_failure", "nonpositive_raw", "restored"],
)
def test_missing_baseline_never_triggers_another_solve(monkeypatch, absence):
    import xic_extractor.peak_detection.physical_peak_evaluation as physical_owner

    rt = np.linspace(0.0, 2.0, 31)
    raw = _gaussian(rt)
    if absence == "nonpositive_raw":
        raw = -np.linspace(1.0, 2.0, len(rt))

    def failed(*args, **kwargs):
        raise ValueError("controlled failure")

    if absence == "asls_failure":
        monkeypatch.setattr(physical_owner, "asls_baseline_on_rt", failed)
    elif absence == "physical_smoothing_failure":
        monkeypatch.setattr(physical_owner, "canonical_gaussian_signal_traces", failed)
    result = _prepared(rt, raw)
    if absence == "restored":
        result = replace(result, baseline_unclipped=None)
    factory = build_scoring_context_factory(config=_config())
    candidate = _quality_candidate(15)
    ctx = factory(
        target=_target(label="A", is_istd=False, istd_pair=""),
        sample_name="S",
        rt=rt,
        intensity=raw,
        prepared_result=result,
        istd_rt_in_this_sample=None,
        paired_istd_fwhm=None,
    )(candidate)
    np.testing.assert_array_equal(ctx.intensity_array, raw)
    assert ctx.signal_quality is not None
    if absence == "restored":
        assert result.prepared is not None
        assert ctx.signal_quality == candidate_signal_quality(
            candidate, rt, raw, np.asarray(result.prepared.baseline)
        )
    else:
        assert ctx.signal_quality.signal_to_noise is None


@pytest.mark.parametrize("error", [TypeError, ValueError, FloatingPointError])
def test_morphology_failure_does_not_change_raw_signal_quality(monkeypatch, error):
    import xic_extractor.extraction.scoring_factory as factory_owner

    rt = np.linspace(0.0, 2.0, 31)
    raw = _gaussian(rt)
    result = _prepared(rt, raw)
    assert result.prepared is not None
    candidate = _quality_candidate(15)
    expected = candidate_signal_quality(
        candidate, rt, raw, np.asarray(result.prepared.baseline)
    )

    def failed(*args, **kwargs):
        raise error("controlled smoothing failure")

    monkeypatch.setattr(
        factory_owner, "gaussian15_positive_asls_residual_trace", failed
    )
    factory = build_scoring_context_factory(config=_config())
    ctx = factory(
        target=_target(label="A", is_istd=False, istd_pair=""),
        sample_name="S",
        rt=rt,
        intensity=raw,
        prepared_result=result,
        istd_rt_in_this_sample=None,
        paired_istd_fwhm=None,
    )(candidate)
    np.testing.assert_array_equal(ctx.intensity_array, raw)
    assert ctx.signal_quality == expected
    assert ctx.active_trace_source == "raw"


def _quality_candidate(apex: int) -> PeakCandidate:
    candidate = _candidate(apex_index=apex)
    return replace(
        candidate, peak=replace(candidate.peak, peak_start=0.0, peak_end=2.0)
    )


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
