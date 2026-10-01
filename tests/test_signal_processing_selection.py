from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.peak_detection.candidate_scoring import score_candidate
from xic_extractor.peak_detection.peak_integration import find_peak_and_area
from xic_extractor.peak_detection.scoring_models import ScoringContext


def _cfg() -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=Path("."),
        dll_dir=Path("."),
        output_csv=Path("out.csv"),
        diagnostics_csv=Path("diag.csv"),
        smooth_window=11,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.1,
        ms2_precursor_tol_da=1.6,
        nl_min_intensity_ratio=0.01,
        resolver_mode="savgol",
        resolver_chrom_threshold=0.05,
        resolver_min_search_range_min=0.04,
        resolver_min_relative_height=0.05,
        resolver_min_absolute_height=25.0,
        resolver_min_ratio_top_edge=1.3,
        resolver_peak_duration_min=0.03,
        resolver_peak_duration_max=1.00,
        resolver_min_scans=5,
    )


def test_find_peak_and_area_without_scoring_context_unchanged() -> None:
    rt = np.linspace(0, 10, 501)
    y = 100 * np.exp(-((rt - 5) / 0.2) ** 2) + 1
    result = find_peak_and_area(rt, y, _cfg())
    assert result.status == "OK"
    assert result.peak is not None
    assert abs(result.peak.rt - 5.0) < 0.05


@pytest.mark.parametrize(
    "resolver_mode",
    ["savgol", "local_minimum"],
)
def test_find_peak_and_area_with_scoring_returns_same_best_for_clean_peak(
    resolver_mode: str,
) -> None:
    rt = np.linspace(0, 10, 501)
    y = 100 * np.exp(-((rt - 5) / 0.2) ** 2) + 1

    def ctx_builder(candidate) -> ScoringContext:
        return ScoringContext(
            rt_array=rt,
            intensity_array=y,
            apex_index=candidate.selection_apex_index,
            half_width_ratio=1.0,
            fwhm_ratio=1.0,
            ms2_present=True,
            nl_match=True,
            rt_prior=5.0,
            rt_prior_sigma=0.1,
            rt_min=0.0,
            rt_max=10.0,
            dirty_matrix=False,
        )

    config = _cfg()
    config = config.__class__(**{**config.__dict__, "resolver_mode": resolver_mode})

    result = find_peak_and_area(rt, y, config, scoring_context_builder=ctx_builder)
    assert result.status == "OK"
    assert result.peak is not None
    assert abs(result.peak.rt - 5.0) < 0.05
    assert result.score_breakdown[:4] == (
        ("Final Confidence", "HIGH"),
        ("Caps", ""),
        ("Raw Score", "125"),
        (
            "Support",
            "strict_nl_ok; rt_prior_close; local_sn_strong; shape_clean; trace_clean",
        ),
    )


def test_local_minimum_preferred_rt_selects_nearest_region() -> None:
    rt = np.linspace(8.7, 9.3, 601)
    y = 950 * np.exp(-((rt - 8.93) / 0.035) ** 2)
    y += 520 * np.exp(-((rt - 9.08) / 0.03) ** 2)
    y += 20.0

    config = _cfg()
    config = config.__class__(
        **{
            **config.__dict__,
            "resolver_mode": "local_minimum",
            "resolver_chrom_threshold": 0.02,
            "resolver_min_search_range_min": 0.03,
            "resolver_min_relative_height": 0.08,
            "resolver_min_absolute_height": 80.0,
            "resolver_min_ratio_top_edge": 1.2,
            "resolver_peak_duration_min": 0.02,
            "resolver_peak_duration_max": 0.30,
            "resolver_min_scans": 7,
        }
    )

    result = find_peak_and_area(rt, y, config, preferred_rt=9.08)

    assert result.status == "OK"
    assert result.peak is not None
    assert result.peak.rt == pytest.approx(9.08, abs=0.02)


def test_find_peak_and_area_rejects_retired_arbitrated_resolver() -> None:
    rt = np.linspace(8.0, 8.30, 31)
    y = 1500 * np.exp(-0.5 * ((rt - 7.96) / 0.06) ** 2) + 5.0
    config = _cfg()
    config = config.__class__(
        **{
            **config.__dict__,
            "resolver_mode": "arbitrated",
            "resolver_peak_duration_max": 0.50,
        }
    )

    with pytest.raises(ValueError, match="arbitrated resolver mode is retired"):
        find_peak_and_area(rt, y, config)


def test_local_minimum_relaxed_search_relaxes_region_filters_for_preferred_rt() -> None:
    rt = np.linspace(8.0, 10.0, 401)
    y = 1000 * np.exp(-((rt - 8.48) / 0.04) ** 2)
    y += 80 * np.exp(-((rt - 9.03) / 0.05) ** 2)
    y += 5.0

    config = _cfg()
    config = config.__class__(
        **{
            **config.__dict__,
            "resolver_mode": "local_minimum",
            "resolver_min_relative_height": 0.10,
            "resolver_min_absolute_height": 25.0,
            "resolver_min_ratio_top_edge": 1.2,
            "resolver_peak_duration_min": 0.03,
            "resolver_peak_duration_max": 1.00,
            "resolver_min_scans": 5,
        }
    )

    result = find_peak_and_area(
        rt,
        y,
        config,
        preferred_rt=9.03,
    )

    assert result.status == "OK"
    assert result.peak is not None
    assert result.peak.rt == pytest.approx(9.03, abs=0.03)


def test_relaxed_search_relaxes_duration_cap_for_broad_preferred_peak() -> None:
    rt = np.linspace(8.0, 10.0, 401)
    y = 320000 * np.exp(-((rt - 9.0) / 0.30) ** 2)
    y += 500.0

    config = _cfg()
    config = config.__class__(
        **{
            **config.__dict__,
            "resolver_mode": "local_minimum",
            "resolver_chrom_threshold": 0.05,
            "resolver_min_search_range_min": 0.04,
            "resolver_min_relative_height": 0.05,
            "resolver_min_absolute_height": 25.0,
            "resolver_min_ratio_top_edge": 1.3,
            "resolver_peak_duration_min": 0.03,
            "resolver_peak_duration_max": 1.00,
            "resolver_min_scans": 5,
        }
    )

    result = find_peak_and_area(
        rt,
        y,
        config,
        preferred_rt=9.0,
    )

    assert result.status == "OK"
    assert result.peak is not None
    assert result.peak.rt == pytest.approx(9.0, abs=0.03)


def test_local_minimum_detects_broad_peak_without_preferred_rt_relaxed_search() -> None:
    rt = np.linspace(8.0, 10.0, 401)
    y = 320000 * np.exp(-((rt - 9.0) / 0.30) ** 2)
    y += 500.0

    config = _cfg()
    config = config.__class__(
        **{
            **config.__dict__,
            "resolver_mode": "local_minimum",
            "resolver_chrom_threshold": 0.05,
            "resolver_min_search_range_min": 0.04,
            "resolver_min_relative_height": 0.05,
            "resolver_min_absolute_height": 25.0,
            "resolver_min_ratio_top_edge": 1.3,
            "resolver_peak_duration_min": 0.03,
            "resolver_peak_duration_max": 1.00,
            "resolver_min_scans": 5,
        }
    )

    result = find_peak_and_area(rt, y, config)

    assert result.status == "OK"
    assert result.peak is not None
    assert result.peak.rt == pytest.approx(9.0, abs=0.03)
    assert len(result.candidates) == 1
    assert "too_broad" in result.candidates[0].quality_flags


def test_local_minimum_flagged_candidate_scores_lower_confidence() -> None:
    rt = np.linspace(8.0, 10.0, 401)
    y = 320000 * np.exp(-((rt - 9.0) / 0.30) ** 2)
    y += 500.0

    def ctx_builder(candidate) -> ScoringContext:
        return ScoringContext(
            rt_array=rt,
            intensity_array=y,
            apex_index=candidate.selection_apex_index,
            half_width_ratio=1.0,
            fwhm_ratio=1.0,
            ms2_present=True,
            nl_match=True,
            rt_prior=9.0,
            rt_prior_sigma=0.1,
            rt_min=8.0,
            rt_max=10.0,
            dirty_matrix=False,
        )

    config = _cfg()
    config = config.__class__(
        **{
            **config.__dict__,
            "resolver_mode": "local_minimum",
            "resolver_chrom_threshold": 0.05,
            "resolver_min_search_range_min": 0.04,
            "resolver_min_relative_height": 0.05,
            "resolver_min_absolute_height": 25.0,
            "resolver_min_ratio_top_edge": 1.3,
            "resolver_peak_duration_min": 0.03,
            "resolver_peak_duration_max": 1.00,
            "resolver_min_scans": 5,
        }
    )

    result = find_peak_and_area(rt, y, config, scoring_context_builder=ctx_builder)

    assert result.status == "OK"
    assert result.peak is not None
    assert result.confidence == "LOW"
    assert result.reason is not None
    assert "hard_local_quality_conflict" in result.reason


def test_recovery_path_preserves_scoring_metadata() -> None:
    rt = np.linspace(8.0, 10.0, 401)
    y = 1000 * np.exp(-((rt - 8.48) / 0.04) ** 2)
    y += 80 * np.exp(-((rt - 9.03) / 0.05) ** 2)

    def ctx_builder(candidate) -> ScoringContext:
        return ScoringContext(
            rt_array=rt,
            intensity_array=y,
            apex_index=candidate.selection_apex_index,
            half_width_ratio=1.0,
            fwhm_ratio=1.0,
            ms2_present=False,
            nl_match=False,
            rt_prior=9.03,
            rt_prior_sigma=0.02,
            rt_min=8.0,
            rt_max=10.0,
            dirty_matrix=False,
        )

    result = find_peak_and_area(
        rt,
        y,
        _cfg(),
        preferred_rt=9.03,
        scoring_context_builder=ctx_builder,
        istd_confidence_note="ISTD anchor was LOW",
    )

    assert result.status == "OK"
    assert result.peak is not None
    assert result.peak.rt == pytest.approx(9.03, abs=0.02)
    assert result.confidence is not None
    assert result.reason is not None
    assert len(result.severities) == 10


def test_scored_selection_honors_valid_preferred_rt_anchor() -> None:
    rt = np.linspace(9.6, 10.4, 401)
    y = 300 * np.exp(-((rt - 10.00) / 0.03) ** 2)
    y += 300 * np.exp(-((rt - 10.25) / 0.03) ** 2)
    y += 2.0

    def ctx_builder(candidate) -> ScoringContext:
        return ScoringContext(
            rt_array=rt,
            intensity_array=y,
            apex_index=candidate.selection_apex_index,
            half_width_ratio=1.0,
            fwhm_ratio=1.0,
            ms2_present=True,
            nl_match=True,
            rt_prior=10.25,
            rt_prior_sigma=1.0,
            rt_min=9.6,
            rt_max=10.4,
            dirty_matrix=False,
        )

    result = find_peak_and_area(
        rt,
        y,
        _cfg(),
        preferred_rt=10.00,
        strict_preferred_rt=False,
        scoring_context_builder=ctx_builder,
    )

    assert result.status == "OK"
    assert result.peak is not None
    assert result.peak.rt == pytest.approx(10.00, abs=0.02)


def test_scored_selection_ignores_tiny_preferred_rt_anchor_peak() -> None:
    rt = np.linspace(9.6, 10.4, 401)
    y = 20 * np.exp(-((rt - 10.00) / 0.03) ** 2)
    y += 300 * np.exp(-((rt - 10.25) / 0.03) ** 2)
    y += 2.0

    def ctx_builder(candidate) -> ScoringContext:
        return ScoringContext(
            rt_array=rt,
            intensity_array=y,
            apex_index=candidate.selection_apex_index,
            half_width_ratio=1.0,
            fwhm_ratio=1.0,
            ms2_present=True,
            nl_match=True,
            rt_prior=10.25,
            rt_prior_sigma=0.05,
            rt_min=9.6,
            rt_max=10.4,
            dirty_matrix=False,
        )

    result = find_peak_and_area(
        rt,
        y,
        _cfg(),
        preferred_rt=10.00,
        strict_preferred_rt=False,
        scoring_context_builder=ctx_builder,
    )

    assert result.status == "OK"
    assert result.peak is not None
    assert result.peak.rt == pytest.approx(10.25, abs=0.02)


def test_typed_recovery_selection_ignores_legacy_confidence_monkeypatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rt = np.linspace(8.0, 10.0, 401)
    y = 1000 * np.exp(-((rt - 8.48) / 0.04) ** 2)
    y += 80 * np.exp(-((rt - 9.03) / 0.05) ** 2)

    def ctx_builder(candidate) -> ScoringContext:
        return ScoringContext(
            rt_array=rt,
            intensity_array=y,
            apex_index=candidate.selection_apex_index,
            half_width_ratio=1.0,
            fwhm_ratio=1.0,
            ms2_present=True,
            nl_match=True,
            rt_prior=None,
            rt_prior_sigma=None,
            rt_min=8.0,
            rt_max=10.0,
            dirty_matrix=False,
        )

    def _score_candidate(
        candidate, ctx, prior_rt, istd_confidence_note=None, **evidence_context
    ):
        from xic_extractor.peak_detection.scoring_models import Confidence

        confidence = (
            Confidence.HIGH
            if candidate.selection_apex_rt < 9.0
            else Confidence.VERY_LOW
        )
        return replace(
            score_candidate(
                candidate,
                ctx,
                prior_rt,
                istd_confidence_note,
                **evidence_context,
            ),
            confidence=confidence,
            reason=confidence.value,
        )

    monkeypatch.setattr(
        "xic_extractor.peak_detection.peak_integration.score_candidate",
        _score_candidate,
    )

    result = find_peak_and_area(
        rt,
        y,
        _cfg(),
        preferred_rt=9.03,
        scoring_context_builder=ctx_builder,
    )

    assert result.status == "OK"
    assert result.peak is not None
    assert result.peak.rt == pytest.approx(9.03, abs=0.02)


def test_recovery_candidate_uses_single_scored_selection_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rt = np.linspace(8.0, 10.0, 401)
    y = 1000 * np.exp(-((rt - 8.48) / 0.04) ** 2)
    y += 80 * np.exp(-((rt - 9.03) / 0.05) ** 2)
    calls: list[int] = []

    def ctx_builder(candidate) -> ScoringContext:
        return ScoringContext(
            rt_array=rt,
            intensity_array=y,
            apex_index=candidate.selection_apex_index,
            half_width_ratio=1.0,
            fwhm_ratio=1.0,
            ms2_present=True,
            nl_match=True,
            rt_prior=None,
            rt_prior_sigma=None,
            rt_min=8.0,
            rt_max=10.0,
            dirty_matrix=False,
        )

    def _select_once(scored, *, selection_rt=None, strict_selection_rt=False):
        calls.append(len(scored))
        if selection_rt is None:
            return max(scored, key=lambda item: item.candidate.selection_apex_intensity)
        return min(
            scored,
            key=lambda item: abs(item.candidate.selection_apex_rt - selection_rt),
        )

    monkeypatch.setattr(
        "xic_extractor.peak_detection.peak_integration.select_candidate_by_evidence",
        _select_once,
    )

    result = find_peak_and_area(
        rt,
        y,
        _cfg(),
        preferred_rt=9.03,
        scoring_context_builder=ctx_builder,
    )

    assert result.status == "OK"
    assert result.peak is not None
    assert result.peak.rt == pytest.approx(9.03, abs=0.02)
    assert calls == [2]


def test_find_peak_and_area_passes_context_prior_into_scoring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rt = np.linspace(9.6, 10.4, 401)
    y = 300 * np.exp(-((rt - 10.00) / 0.03) ** 2)
    y += 300 * np.exp(-((rt - 10.25) / 0.03) ** 2)
    y += 2.0
    observed_prior_rts: list[float | None] = []

    def ctx_builder(candidate) -> ScoringContext:
        return ScoringContext(
            rt_array=rt,
            intensity_array=y,
            apex_index=candidate.selection_apex_index,
            half_width_ratio=1.0,
            fwhm_ratio=1.0,
            ms2_present=True,
            nl_match=True,
            rt_prior=10.25,
            rt_prior_sigma=0.05,
            rt_min=9.6,
            rt_max=10.4,
            dirty_matrix=False,
        )

    def _score_candidate(
        candidate, ctx, prior_rt, istd_confidence_note=None, **evidence_context
    ):
        observed_prior_rts.append(prior_rt)
        return score_candidate(
            candidate,
            ctx,
            prior_rt,
            istd_confidence_note,
            **evidence_context,
        )

    monkeypatch.setattr(
        "xic_extractor.peak_detection.peak_integration.score_candidate",
        _score_candidate,
    )

    result = find_peak_and_area(
        rt,
        y,
        _cfg(),
        preferred_rt=10.00,
        strict_preferred_rt=False,
        scoring_context_builder=ctx_builder,
    )

    assert result.status == "OK"
    assert observed_prior_rts
    assert set(observed_prior_rts) == {10.25}


def test_find_peak_and_area_uses_paired_istd_anchor_in_typed_evidence() -> None:
    rt = np.linspace(9.8, 11.6, 901)
    y = 80 * np.exp(-((rt - 10.20) / 0.03) ** 2)
    y += 1000 * np.exp(-((rt - 11.25) / 0.04) ** 2)
    y += 2.0

    def ctx_builder(candidate) -> ScoringContext:
        return ScoringContext(
            rt_array=rt,
            intensity_array=y,
            apex_index=candidate.selection_apex_index,
            half_width_ratio=1.0,
            fwhm_ratio=1.0,
            ms2_present=True,
            nl_match=False,
            rt_prior=None,
            rt_prior_sigma=None,
            rt_min=9.8,
            rt_max=11.6,
            dirty_matrix=False,
            ms2_trace_strength="weak",
            trigger_scan_count=1,
            strict_nl_scan_count=0,
        )

    config = _cfg()
    config = config.__class__(**{**config.__dict__, "resolver_mode": "local_minimum"})

    result = find_peak_and_area(
        rt,
        y,
        config,
        scoring_context_builder=ctx_builder,
        evidence_role="Analyte",
        istd_pair="ISTD",
        paired_istd_anchor_rt=10.0,
    )

    assert result.status == "OK"
    assert result.peak is not None
    assert result.peak.rt == pytest.approx(10.20, abs=0.03)
    assert result.paired_istd_anchor_rt == 10.0
    far_score = max(
        result.candidate_scores,
        key=lambda score: score.candidate.selection_apex_rt,
    )
    assert far_score.evidence_facts is not None
    assert far_score.evidence_facts.rt.role == "Analyte"
    assert far_score.evidence_facts.rt.istd_pair == "ISTD"
    assert far_score.evidence_facts.rt.paired_istd_anchor_rt_min == 10.0
    assert far_score.evidence_facts.rt.paired_istd_delta_min == pytest.approx(1.25)
    assert far_score.evidence_facts.rt.paired_istd_status == "far"
    assert far_score.evidence_facts.chemical.nl_match is False
    assert far_score.evidence_facts.area == far_score.candidate.peak.area
    assert far_score.confidence == "VERY_LOW"
    assert "paired_istd_rt_mismatch_policy" in far_score.reason
