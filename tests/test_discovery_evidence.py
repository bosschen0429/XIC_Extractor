from dataclasses import replace

from tests.discovery_csv_fixtures import _peak
from xic_extractor.configuration.cid_nl_profile import NeutralLossProfile
from xic_extractor.discovery.evidence_config import (
    DEFAULT_EVIDENCE_PROFILE,
    DiscoveryEvidenceProfile,
    DiscoveryEvidenceThresholds,
    DiscoveryEvidenceWeights,
)
from xic_extractor.discovery.evidence_score import score_discovery_evidence
from xic_extractor.discovery.models import DiscoveryPeak
from xic_extractor.discovery.settings import DiscoverySettings


def test_default_evidence_profile_pins_weights() -> None:
    weights = DEFAULT_EVIDENCE_PROFILE.weights
    assert DEFAULT_EVIDENCE_PROFILE.name == "default"
    assert weights.ms1_peak_present == 25
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


def test_low_numeric_scan_support_lowers_the_score() -> None:
    candidate = replace(
        _candidate(),
        ms1_trace_quality="GOOD",
        ms1_scan_support_score=0.1,
    )

    evidence = score_discovery_evidence(candidate, settings=_settings())

    assert evidence.score == 56


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


def _candidate() -> DiscoveryPeak:
    return _peak(
        attached_event_count=2,
        ms1_apex_rt=7.84,
        ms1_area=500_000.0,
        ms2_product_max_intensity=50_000.0,
        ms1_event_delta_min=0.01,
        ms1_scan_support_score=0.8,
    )
