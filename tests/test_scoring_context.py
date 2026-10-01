from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.evidence.neutral_loss import CandidateMS2Evidence, NLResult
from xic_extractor.extraction.rt_prior_library import LibraryEntry
from xic_extractor.extraction.scoring_factory import build_scoring_context_factory
from xic_extractor.ms2_trace_evidence import MS2TraceEvidence
from xic_extractor.peak_detection.candidate_scoring import score_candidate
from xic_extractor.peak_detection.candidate_selection import (
    select_candidate_by_evidence,
)
from xic_extractor.peak_detection.ms1_morphology import (
    DEFAULT_GAUSSIAN15_WINDOW_POINTS,
    MS1_MORPHOLOGY_AREA_SOURCE,
    MS1_MORPHOLOGY_TRACE_METHOD,
)
from xic_extractor.peak_detection.peak_integration import find_peak_and_area
from xic_extractor.peak_detection.physical_peak_evaluation import (
    prepare_physical_signal,
)


def test_istd_context_uses_rolling_median_prior() -> None:
    factory = build_scoring_context_factory(
        config=_config(),
        injection_order={"S1": 1, "S2": 2, "S3": 3},
        istd_rts_by_sample={"ISTD-A": {"S1": 9.8, "S2": 10.0, "S3": 10.2}},
        rt_prior_library={},
    )

    builder = factory(
        target=_target(label="ISTD-A", is_istd=True, istd_pair=""),
        sample_name="S2",
        rt=np.linspace(9.7, 10.3, 7),
        intensity=np.array([0.0, 1.0, 4.0, 7.0, 4.0, 1.0, 0.0]),
        prepared_result=_prepared(
            np.linspace(9.7, 10.3, 7), np.array([0.0, 1.0, 4.0, 7.0, 4.0, 1.0, 0.0])
        ),
        istd_rt_in_this_sample=None,
        paired_istd_fwhm=None,
        nl_result=None,
    )

    ctx = builder(SimpleNamespace(selection_apex_index=3))

    assert ctx.rt_prior == pytest.approx(10.0)
    assert ctx.rt_prior_sigma is None
    assert ctx.dirty_matrix is False
    assert ctx.prefer_rt_prior_tiebreak is False
    assert ctx.active_trace_source == MS1_MORPHOLOGY_AREA_SOURCE
    assert ctx.morphology_trace_method == MS1_MORPHOLOGY_TRACE_METHOD
    assert ctx.morphology_trace_window_points == DEFAULT_GAUSSIAN15_WINDOW_POINTS
    assert ctx.baseline_array is not None
    assert np.all(ctx.baseline_array == 0.0)


def test_scoring_context_uses_configured_ms1_morphology_smoothing_window() -> None:
    factory = build_scoring_context_factory(
        config=_config(ms1_morphology_smoothing_window_points=7),
        injection_order={},
        istd_rts_by_sample={},
        rt_prior_library={},
    )

    builder = factory(
        target=_target(label="ISTD-A", is_istd=True, istd_pair=""),
        sample_name="S2",
        rt=np.linspace(9.7, 10.3, 9),
        intensity=np.array([0.0, 1.0, 4.0, 7.0, 10.0, 7.0, 4.0, 1.0, 0.0]),
        prepared_result=_prepared(
            np.linspace(9.7, 10.3, 9),
            np.array([0.0, 1.0, 4.0, 7.0, 10.0, 7.0, 4.0, 1.0, 0.0]),
        ),
        istd_rt_in_this_sample=None,
        paired_istd_fwhm=None,
        nl_result=None,
    )

    ctx = builder(SimpleNamespace(selection_apex_index=4))

    assert ctx.morphology_trace_method == MS1_MORPHOLOGY_TRACE_METHOD
    assert ctx.morphology_trace_window_points == 7


def test_analyte_context_uses_delta_rt_library_and_shape_ratio() -> None:
    library = {
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
    }
    factory = build_scoring_context_factory(
        config=_config(dirty_matrix_mode=True),
        injection_order={},
        istd_rts_by_sample={},
        rt_prior_library=library,
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
        nl_result=NLResult(
            status="OK",
            best_ppm=10.0,
            best_scan_rt=10.2,
            valid_ms2_scan_count=3,
            parse_error_count=0,
            matched_scan_count=3,
        ),
    )

    ctx = builder(SimpleNamespace(selection_apex_index=3))

    assert ctx.rt_prior == pytest.approx(10.25)
    assert ctx.rt_prior_sigma == pytest.approx(0.05)
    assert ctx.ms2_present is True
    assert ctx.nl_match is True
    assert ctx.fwhm_ratio is not None
    assert ctx.fwhm_ratio > 0
    assert ctx.dirty_matrix is True
    assert ctx.prefer_rt_prior_tiebreak is True


def test_context_without_injection_order_or_library_has_no_prior() -> None:
    factory = build_scoring_context_factory(
        config=_config(),
        injection_order={},
        istd_rts_by_sample={},
        rt_prior_library={},
    )

    builder = factory(
        target=_target(label="Analyte-A", is_istd=False, istd_pair="ISTD-A"),
        sample_name="S-missing",
        rt=np.linspace(9.9, 10.5, 7),
        intensity=np.array([0.0, 1.0, 4.0, 8.0, 4.0, 1.0, 0.0]),
        prepared_result=_prepared(
            np.linspace(9.9, 10.5, 7), np.array([0.0, 1.0, 4.0, 8.0, 4.0, 1.0, 0.0])
        ),
        istd_rt_in_this_sample=None,
        paired_istd_fwhm=None,
        nl_result=NLResult(
            status="NO_MS2",
            best_ppm=None,
            best_scan_rt=None,
            valid_ms2_scan_count=0,
            parse_error_count=0,
            matched_scan_count=0,
        ),
    )

    ctx = builder(SimpleNamespace(selection_apex_index=3))

    assert ctx.rt_prior is None
    assert ctx.rt_prior_sigma is None
    assert ctx.ms2_present is False
    assert ctx.nl_match is False
    assert ctx.prefer_rt_prior_tiebreak is False


def test_scoring_context_uses_candidate_ms2_evidence_not_target_window_nl() -> None:
    factory = build_scoring_context_factory(
        config=_config(),
        injection_order={},
        istd_rts_by_sample={},
        rt_prior_library={},
    )
    candidate = SimpleNamespace(selection_apex_index=3)
    builder = factory(
        target=_target(label="Analyte-A", is_istd=False, istd_pair=""),
        sample_name="S2",
        rt=np.linspace(9.9, 10.5, 7),
        intensity=np.array([0.0, 1.0, 4.0, 8.0, 4.0, 1.0, 0.0]),
        prepared_result=_prepared(
            np.linspace(9.9, 10.5, 7), np.array([0.0, 1.0, 4.0, 8.0, 4.0, 1.0, 0.0])
        ),
        istd_rt_in_this_sample=None,
        paired_istd_fwhm=None,
        nl_result=NLResult(
            status="OK",
            best_ppm=1.0,
            best_scan_rt=10.2,
            valid_ms2_scan_count=3,
            parse_error_count=0,
            matched_scan_count=3,
        ),
        candidate_ms2_evidence_builder=lambda _candidate: CandidateMS2Evidence(
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

    ctx = builder(candidate)

    assert ctx.ms2_present is False
    assert ctx.nl_match is False


def test_scoring_context_keeps_trigger_without_strict_nl_match() -> None:
    factory = build_scoring_context_factory(
        config=_config(),
        injection_order={},
        istd_rts_by_sample={},
        rt_prior_library={},
    )
    builder = factory(
        target=_target(label="Analyte-A", is_istd=False, istd_pair=""),
        sample_name="S2",
        rt=np.linspace(9.9, 10.5, 7),
        intensity=np.array([0.0, 1.0, 4.0, 8.0, 4.0, 1.0, 0.0]),
        prepared_result=_prepared(
            np.linspace(9.9, 10.5, 7), np.array([0.0, 1.0, 4.0, 8.0, 4.0, 1.0, 0.0])
        ),
        istd_rt_in_this_sample=None,
        paired_istd_fwhm=None,
        nl_result=None,
        candidate_ms2_evidence_builder=lambda _candidate: CandidateMS2Evidence(
            ms2_present=True,
            nl_match=False,
            nl_status="NL_FAIL",
            trigger_scan_count=1,
            strict_nl_scan_count=0,
            best_loss_ppm=None,
            best_scan_rt=None,
            best_product_base_ratio=None,
            alignment_source="region",
        ),
    )

    ctx = builder(SimpleNamespace(selection_apex_index=3))

    assert ctx.ms2_present is True
    assert ctx.nl_match is False


def test_scoring_context_copies_candidate_ms2_trace_strength() -> None:
    factory = build_scoring_context_factory(
        config=_config(),
        injection_order={},
        istd_rts_by_sample={},
        rt_prior_library={},
    )
    builder = factory(
        target=_target(label="Analyte-A", is_istd=False, istd_pair=""),
        sample_name="S2",
        rt=np.linspace(9.9, 10.5, 7),
        intensity=np.array([0.0, 1.0, 4.0, 8.0, 4.0, 1.0, 0.0]),
        prepared_result=_prepared(
            np.linspace(9.9, 10.5, 7), np.array([0.0, 1.0, 4.0, 8.0, 4.0, 1.0, 0.0])
        ),
        istd_rt_in_this_sample=None,
        paired_istd_fwhm=None,
        nl_result=None,
        candidate_ms2_evidence_builder=lambda _candidate: CandidateMS2Evidence(
            ms2_present=True,
            nl_match=True,
            nl_status="OK",
            trigger_scan_count=2,
            strict_nl_scan_count=2,
            best_loss_ppm=1.0,
            best_scan_rt=10.1,
            best_product_base_ratio=0.5,
            alignment_source="region",
            ms1_peak_group_source="gaussian15_ms1_peak_group",
            ms1_peak_group_rt_min=10.0,
            ms1_peak_group_rt_max=10.2,
            ms1_peak_group_trigger_scan_count=2,
            ms1_peak_group_strict_nl_scan_count=2,
            ms1_peak_group_strict_nl_event_count=1,
            outside_ms1_peak_group_trigger_scan_count=1,
            outside_ms1_peak_group_strict_nl_scan_count=0,
            trace=MS2TraceEvidence(
                product_point_count=2,
                product_apex_rt=10.1,
                product_apex_delta_min=0.0,
                product_height=200.0,
                product_area=10.0,
                trace_continuity=1.0,
                strength="strong",
            ),
        ),
    )

    ctx = builder(SimpleNamespace(selection_apex_index=3))

    assert ctx.ms2_trace_strength == "strong"
    assert ctx.ms2_alignment_source == "region"
    assert ctx.trigger_scan_count == 2
    assert ctx.strict_nl_scan_count == 2
    assert ctx.ms1_peak_group_source == "gaussian15_ms1_peak_group"
    assert ctx.ms1_peak_group_rt_min == 10.0
    assert ctx.ms1_peak_group_rt_max == 10.2
    assert ctx.ms1_peak_group_trigger_scan_count == 2
    assert ctx.ms1_peak_group_strict_nl_scan_count == 2
    assert ctx.ms1_peak_group_strict_nl_event_count == 1
    assert ctx.outside_ms1_peak_group_trigger_scan_count == 1
    assert ctx.outside_ms1_peak_group_strict_nl_scan_count == 0


def test_strict_nl_candidate_beats_candidate_with_trigger_but_failed_nl() -> None:
    factory = build_scoring_context_factory(
        config=_config(),
        injection_order={},
        istd_rts_by_sample={},
        rt_prior_library={},
    )
    candidate_with_nl = _candidate(apex_index=3, apex_rt=10.1, intensity=70.0)
    candidate_without_nl = _candidate(apex_index=4, apex_rt=10.2, intensity=100.0)

    def _candidate_ms2_evidence(candidate: SimpleNamespace) -> CandidateMS2Evidence:
        return CandidateMS2Evidence(
            ms2_present=True,
            nl_match=candidate is candidate_with_nl,
            nl_status="OK" if candidate is candidate_with_nl else "NL_FAIL",
            trigger_scan_count=1,
            strict_nl_scan_count=1 if candidate is candidate_with_nl else 0,
            best_loss_ppm=1.0 if candidate is candidate_with_nl else None,
            best_scan_rt=10.1 if candidate is candidate_with_nl else None,
            best_product_base_ratio=0.5 if candidate is candidate_with_nl else None,
            alignment_source="region",
        )

    builder = factory(
        target=_target(label="Analyte-A", is_istd=False, istd_pair=""),
        sample_name="S2",
        rt=np.linspace(9.8, 10.4, 7),
        intensity=np.array([0.0, 1.0, 4.0, 8.0, 7.0, 1.0, 0.0]),
        prepared_result=_prepared(
            np.linspace(9.8, 10.4, 7), np.array([0.0, 1.0, 4.0, 8.0, 7.0, 1.0, 0.0])
        ),
        istd_rt_in_this_sample=None,
        paired_istd_fwhm=None,
        nl_result=NLResult(
            status="OK",
            best_ppm=1.0,
            best_scan_rt=10.2,
            valid_ms2_scan_count=3,
            parse_error_count=0,
            matched_scan_count=3,
        ),
        candidate_ms2_evidence_builder=_candidate_ms2_evidence,
    )
    scored = [
        score_candidate(candidate_with_nl, builder(candidate_with_nl), prior_rt=None),
        score_candidate(
            candidate_without_nl,
            builder(candidate_without_nl),
            prior_rt=None,
        ),
    ]

    selected = select_candidate_by_evidence(scored)

    assert selected.candidate is candidate_with_nl


def test_scoring_context_caches_asls_without_retained_intermediate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_count = 0

    def _fake_asls(values: np.ndarray) -> np.ndarray:
        nonlocal call_count
        call_count += 1
        return np.zeros_like(values)

    monkeypatch.setattr(
        "xic_extractor.peak_detection.scoring_metrics.asls_baseline",
        _fake_asls,
    )

    rt = np.linspace(9.6, 10.4, 401)
    intensity = 300 * np.exp(-(((rt - 10.00) / 0.03) ** 2))
    intensity += 300 * np.exp(-(((rt - 10.25) / 0.03) ** 2))
    intensity += 2.0

    factory = build_scoring_context_factory(
        config=_config(),
        injection_order={},
        istd_rts_by_sample={},
        rt_prior_library={},
    )
    builder = factory(
        target=_target(label="Analyte-A", is_istd=False, istd_pair="ISTD-A"),
        sample_name="S2",
        rt=rt,
        intensity=intensity,
        prepared_result=replace(_prepared(rt, intensity), baseline_unclipped=None),
        istd_rt_in_this_sample=None,
        paired_istd_fwhm=None,
        nl_result=None,
    )

    result = find_peak_and_area(
        rt,
        intensity,
        _config(),
        scoring_context_builder=builder,
    )

    assert result.status == "OK"
    assert call_count == 1


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


def _candidate(*, apex_index: int, apex_rt: float, intensity: float) -> SimpleNamespace:
    return SimpleNamespace(
        selection_apex_index=apex_index,
        selection_apex_rt=apex_rt,
        selection_apex_intensity=intensity,
        quality_flags=(),
    )


def _prepared(rt: np.ndarray, intensity: np.ndarray):
    return prepare_physical_signal(
        rt, intensity, profile=SignalProcessingProfile(window_points=3)
    )
