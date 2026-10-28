from dataclasses import replace
from pathlib import Path

from xic_extractor.discovery.evidence_config import (
    DEFAULT_EVIDENCE_PROFILE,
    DiscoveryEvidenceProfile,
    DiscoveryEvidenceThresholds,
    DiscoveryEvidenceWeights,
)
from xic_extractor.discovery.evidence_score import score_discovery_evidence
from xic_extractor.discovery.models import (
    DiscoveryCandidate,
    DiscoverySettings,
    NeutralLossProfile,
)


def test_default_evidence_profile_pins_weights() -> None:
    weights = DEFAULT_EVIDENCE_PROFILE.weights
    assert DEFAULT_EVIDENCE_PROFILE.name == "default"
    assert weights.ms1_peak_present == 25
    assert weights.ms1_peak_absent == 5
    assert weights.seed_event_per == 8
    assert weights.seed_event_max == 25
    assert weights.rt_aligned == 15
    assert weights.rt_near == 10
    assert weights.rt_shifted == 5
    assert weights.product_intensity_high == 10
    assert weights.product_intensity_med == 5
    assert weights.area_high == 10
    assert weights.area_med == 5
    assert weights.scan_support_high == 5
    assert weights.scan_support_low == -10
    assert weights.legacy_trace_quality_high == 5
    assert weights.legacy_trace_quality_low == -10


def test_default_evidence_profile_pins_thresholds() -> None:
    thresholds = DEFAULT_EVIDENCE_PROFILE.thresholds
    assert thresholds.rt_aligned_max_min == 0.05
    assert thresholds.rt_near_max_min == 0.20
    assert thresholds.rt_shifted_max_min == 0.40
    assert thresholds.product_intensity_high_min == 100_000.0
    assert thresholds.product_intensity_med_min == 10_000.0
    assert thresholds.area_high_min == 1_000_000.0
    assert thresholds.area_med_min == 100_000.0
    assert thresholds.ms1_support_strong_area_min == 10_000_000.0
    assert thresholds.ms1_support_moderate_area_min == 1_000_000.0
    assert thresholds.scan_support_target == 10
    assert thresholds.scan_support_high_score_min == 0.8
    assert thresholds.scan_support_low_score_max == 0.2


def test_score_discovery_evidence_preserves_default_baseline() -> None:
    evidence = score_discovery_evidence(_candidate(), settings=_settings())

    assert evidence.score == 71
    assert evidence.tier == "B"
    assert evidence.ms2_support == "strong"
    assert evidence.ms1_support == "moderate"
    assert evidence.rt_alignment == "aligned"


def test_score_discovery_evidence_uses_custom_weight_from_settings() -> None:
    settings = _settings(
        weights=replace(
            DEFAULT_EVIDENCE_PROFILE.weights,
            ms1_peak_present=30,
        )
    )

    evidence = score_discovery_evidence(_candidate(), settings=settings)

    assert evidence.score == 76


def test_score_discovery_evidence_uses_custom_thresholds_from_settings() -> None:
    settings = _settings(
        thresholds=replace(
            DEFAULT_EVIDENCE_PROFILE.thresholds,
            rt_aligned_max_min=0.005,
        )
    )

    evidence = score_discovery_evidence(_candidate(), settings=settings)

    assert evidence.score == 66
    assert evidence.rt_alignment == "near"


def test_score_discovery_evidence_adds_high_numeric_scan_support() -> None:
    settings = _settings(
        weights=replace(
            DEFAULT_EVIDENCE_PROFILE.weights,
            scan_support_high=12,
        )
    )
    candidate = replace(
        _candidate(),
        ms1_trace_quality="POOR",
        ms1_scan_support_score=0.9,
    )

    evidence = score_discovery_evidence(candidate, settings=settings)

    assert evidence.score == 78
    assert evidence.ms1_support == "moderate"


def test_score_discovery_evidence_penalizes_low_numeric_scan_support() -> None:
    settings = _settings(
        weights=replace(
            DEFAULT_EVIDENCE_PROFILE.weights,
            scan_support_low=-12,
        )
    )
    candidate = replace(
        _candidate(),
        ms1_trace_quality="GOOD",
        ms1_scan_support_score=0.1,
    )

    evidence = score_discovery_evidence(candidate, settings=settings)

    assert evidence.score == 54
    assert evidence.ms1_support == "weak"


def test_score_discovery_evidence_keeps_mid_scan_support_neutral() -> None:
    candidate = replace(
        _candidate(),
        ms1_trace_quality="GOOD",
        ms1_scan_support_score=0.5,
    )

    evidence = score_discovery_evidence(candidate, settings=_settings())

    assert evidence.score == 66


def test_numeric_scan_support_takes_precedence_over_legacy_trace_quality() -> None:
    candidate = replace(
        _candidate(),
        ms1_trace_quality="GOOD",
        ms1_scan_support_score=0.1,
    )

    evidence = score_discovery_evidence(candidate, settings=_settings())

    assert evidence.score == 56


def test_legacy_trace_quality_fallback_scores_when_scan_support_missing() -> None:
    candidate = replace(
        _candidate(),
        ms1_trace_quality="GOOD",
        ms1_scan_support_score=None,
    )

    evidence = score_discovery_evidence(candidate, settings=_settings())

    assert evidence.score == 71
    assert evidence.ms1_support == "moderate"


def test_missing_ms1_peak_does_not_apply_low_scan_support_penalty() -> None:
    candidate = replace(
        _candidate(),
        ms1_peak_found=False,
        ms1_apex_rt=None,
        ms1_area=None,
        ms1_seed_delta_min=None,
        ms1_trace_quality="MISSING",
        ms1_scan_support_score=0.0,
    )

    evidence = score_discovery_evidence(candidate, settings=_settings())

    assert evidence.score == 26
    assert evidence.ms1_support == "missing"


def _settings(
    *,
    weights: DiscoveryEvidenceWeights | None = None,
    thresholds: DiscoveryEvidenceThresholds | None = None,
) -> DiscoverySettings:
    return DiscoverySettings(
        neutral_loss_profile=NeutralLossProfile("DNA_dR", 116.0474),
        evidence_profile=DiscoveryEvidenceProfile(
            name="default",
            weights=weights or DEFAULT_EVIDENCE_PROFILE.weights,
            thresholds=thresholds or DEFAULT_EVIDENCE_PROFILE.thresholds,
        ),
    )


def _candidate() -> DiscoveryCandidate:
    return DiscoveryCandidate(
        review_priority="MEDIUM",
        evidence_score=0,
        evidence_tier="E",
        ms2_support="weak",
        ms1_support="missing",
        rt_alignment="missing",
        candidate_id="Sample#1",
        precursor_mz=258.108,
        product_mz=142.061,
        observed_neutral_loss_da=116.0474,
        best_seed_rt=7.83,
        seed_event_count=2,
        ms1_peak_found=True,
        ms1_apex_rt=7.84,
        ms1_area=500_000.0,
        ms2_product_max_intensity=50_000.0,
        reason="",
        raw_file=Path("C:/x/Sample.raw"),
        sample_stem="Sample",
        best_ms2_scan_id=1,
        seed_scan_ids=(1,),
        neutral_loss_tag="DNA_dR",
        configured_neutral_loss_da=116.0474,
        neutral_loss_mass_error_ppm=2.0,
        rt_seed_min=7.80,
        rt_seed_max=7.86,
        ms1_search_rt_min=7.60,
        ms1_search_rt_max=8.06,
        ms1_seed_delta_min=0.01,
        ms1_peak_rt_start=7.70,
        ms1_peak_rt_end=7.98,
        ms1_height=4500.0,
        ms1_trace_quality="GOOD",
    )
