from dataclasses import dataclass, replace
from types import SimpleNamespace

from xic_extractor.peak_detection.candidate_selection import (
    select_candidate_with_confidence,
)
from xic_extractor.peak_detection.peak_scoring_evidence import EvidenceScore
from xic_extractor.peak_detection.scoring_models import (
    Confidence,
    ScoredCandidate,
)


@dataclass
class _FakePeak:
    selection_apex_rt: float
    selection_apex_intensity: float
    quality_flags: tuple[str, ...] = ()
    peak: object | None = None


def _sc(
    confidence: Confidence,
    apex_rt: float,
    intensity: float,
    prior: float | None,
    *,
    quality_penalty: int = 0,
    selection_quality_penalty: float | None = None,
    prefer_rt_prior_tiebreak: bool = False,
    quality_flags: tuple[str, ...] = (),
    area: float | None = None,
) -> ScoredCandidate:
    return ScoredCandidate(
        candidate=_FakePeak(
            apex_rt,
            intensity,
            quality_flags,
            SimpleNamespace(area=area) if area is not None else None,
        ),
        severities=tuple(),
        confidence=confidence,
        reason="",
        prior_rt=prior,
        quality_penalty=quality_penalty,
        selection_quality_penalty=selection_quality_penalty,
        prefer_rt_prior_tiebreak=prefer_rt_prior_tiebreak,
    )


def _score_for_selector(
    raw_score: int,
    *,
    support_labels: tuple[str, ...] = (),
    concern_labels: tuple[str, ...] = (),
    cap_labels: tuple[str, ...] = (),
) -> EvidenceScore:
    confidence = (
        "HIGH"
        if raw_score >= 80
        else "MEDIUM"
        if raw_score >= 60
        else "LOW"
        if raw_score >= 40
        else "VERY_LOW"
    )
    return EvidenceScore(
        base_score=50,
        positive_points=max(0, raw_score - 50),
        negative_points=max(0, 50 - raw_score),
        raw_score=raw_score,
        score_confidence=confidence,
        confidence=confidence,
        support_labels=support_labels,
        concern_labels=concern_labels,
        cap_labels=cap_labels,
    )


def test_selector_prefers_higher_confidence() -> None:
    a = _sc(Confidence.MEDIUM, 10.0, 1000, prior=10.0)
    b = _sc(Confidence.HIGH, 10.5, 500, prior=10.0)

    assert select_candidate_with_confidence([a, b]) is b


def test_selector_tiebreak_by_prior_distance() -> None:
    a = _sc(
        Confidence.HIGH,
        10.3,
        1000,
        prior=10.0,
        prefer_rt_prior_tiebreak=True,
    )
    b = _sc(
        Confidence.HIGH,
        10.05,
        500,
        prior=10.0,
        prefer_rt_prior_tiebreak=True,
    )

    assert select_candidate_with_confidence([a, b]) is b


def test_selector_final_tiebreak_by_intensity() -> None:
    a = _sc(
        Confidence.HIGH,
        10.1,
        1000,
        prior=10.0,
        prefer_rt_prior_tiebreak=True,
    )
    b = _sc(
        Confidence.HIGH,
        10.1,
        500,
        prior=10.0,
        prefer_rt_prior_tiebreak=True,
    )

    assert select_candidate_with_confidence([a, b]) is a


def test_selector_uses_effective_score_to_balance_distance_and_evidence() -> None:
    near_weak = _sc(
        Confidence.LOW,
        10.02,
        1000.0,
        10.0,
        selection_quality_penalty=0.5,
    )
    far_strong = _sc(
        Confidence.MEDIUM,
        10.40,
        2000.0,
        10.0,
        selection_quality_penalty=0.0,
    )
    near_weak = replace(near_weak, evidence_score=_score_for_selector(55))
    far_strong = replace(far_strong, evidence_score=_score_for_selector(70))

    assert (
        select_candidate_with_confidence(
            [near_weak, far_strong],
            selection_rt=10.0,
        )
        is near_weak
    )


def test_selector_does_not_let_far_peak_win_on_score_alone() -> None:
    near = _sc(
        Confidence.LOW,
        10.02,
        1000.0,
        10.0,
        selection_quality_penalty=0.0,
    )
    far = _sc(
        Confidence.MEDIUM,
        11.20,
        3000.0,
        10.0,
        selection_quality_penalty=0.0,
    )
    near = replace(near, evidence_score=_score_for_selector(45))
    far = replace(far, evidence_score=_score_for_selector(75))

    assert select_candidate_with_confidence([near, far], selection_rt=10.0) is near


def test_selector_uses_weighted_adap_like_selection_penalty() -> None:
    clean = _sc(
        Confidence.HIGH,
        10.10,
        500.0,
        10.0,
        quality_penalty=0,
        selection_quality_penalty=0.0,
    )
    weak = _sc(
        Confidence.HIGH,
        10.10,
        1000.0,
        10.0,
        quality_penalty=0,
        selection_quality_penalty=0.25,
    )

    assert select_candidate_with_confidence([clean, weak]) is clean


def test_selector_does_not_let_soft_quality_penalty_beat_large_rt_distance() -> None:
    far_clean = _sc(
        Confidence.HIGH,
        12.00,
        1000.0,
        10.0,
        quality_penalty=0,
        selection_quality_penalty=0.0,
    )
    near_flagged = _sc(
        Confidence.HIGH,
        10.00,
        500.0,
        10.0,
        quality_penalty=0,
        selection_quality_penalty=0.25,
    )

    assert select_candidate_with_confidence([far_clean, near_flagged]) is near_flagged


def test_selector_tiebreak_prefers_lower_quality_penalty() -> None:
    clean = _sc(Confidence.LOW, 10.10, 500.0, 10.0, quality_penalty=0)
    weak = _sc(Confidence.LOW, 10.10, 1000.0, 10.0, quality_penalty=1)

    assert select_candidate_with_confidence([clean, weak]) is clean


def test_selector_low_scan_anchor_spike_yields_to_much_stronger_candidate() -> None:
    spike = _sc(
        Confidence.MEDIUM,
        25.90,
        10_000.0,
        None,
        selection_quality_penalty=0.25,
        quality_flags=("low_scan_support",),
    )
    supported_peak = _sc(
        Confidence.LOW,
        26.15,
        31_000.0,
        None,
        selection_quality_penalty=0.25,
        quality_flags=("low_trace_continuity",),
    )

    assert (
        select_candidate_with_confidence(
            [spike, supported_peak],
            selection_rt=25.94,
        )
        is supported_peak
    )


def test_selector_low_scan_demotion_applies_to_evidence_score_candidates() -> None:
    spike = _sc(
        Confidence.MEDIUM,
        25.90,
        10_000.0,
        None,
        selection_quality_penalty=0.25,
        quality_flags=("low_scan_support",),
    )
    supported_peak = _sc(
        Confidence.LOW,
        26.15,
        31_000.0,
        None,
        selection_quality_penalty=0.25,
        quality_flags=("low_trace_continuity",),
    )
    spike = replace(spike, evidence_score=_score_for_selector(70))
    supported_peak = replace(supported_peak, evidence_score=_score_for_selector(45))

    assert (
        select_candidate_with_confidence(
            [spike, supported_peak],
            selection_rt=25.94,
        )
        is supported_peak
    )


def test_selector_demotes_low_scan_strict_nl_shoulder_for_area_supported_candidate(
) -> None:
    shoulder = _sc(
        Confidence.MEDIUM,
        25.896,
        12_000_000.0,
        None,
        selection_quality_penalty=0.25,
        quality_flags=("low_scan_support",),
        area=17_000_000.0,
    )
    broader_peak = _sc(
        Confidence.VERY_LOW,
        26.149,
        31_000_000.0,
        None,
        selection_quality_penalty=0.25,
        quality_flags=("low_trace_continuity",),
        area=335_000_000.0,
    )
    shoulder = replace(
        shoulder,
        evidence_score=_score_for_selector(
            110,
            support_labels=("strict_nl_ok", "ms2_trace_strong", "rt_prior_close"),
        ),
    )
    broader_peak = replace(
        broader_peak,
        evidence_score=_score_for_selector(
            37,
            support_labels=("strict_nl_ok", "local_sn_strong"),
        ),
    )

    assert (
        select_candidate_with_confidence(
            [shoulder, broader_peak],
            selection_rt=25.942,
        )
        is broader_peak
    )


def test_selector_keeps_low_scan_anchor_when_alternative_is_too_far() -> None:
    spike = _sc(
        Confidence.MEDIUM,
        25.90,
        10_000.0,
        None,
        selection_quality_penalty=0.25,
        quality_flags=("low_scan_support",),
    )
    far_peak = _sc(
        Confidence.LOW,
        27.00,
        100_000.0,
        None,
        selection_quality_penalty=0.25,
    )

    assert (
        select_candidate_with_confidence(
            [spike, far_peak],
            selection_rt=25.94,
        )
        is spike
    )


def test_selector_demotes_low_scan_near_prior_when_supported_area_is_much_larger(
) -> None:
    near_low_scan = _sc(
        Confidence.HIGH,
        25.90,
        12_000.0,
        None,
        selection_quality_penalty=0.25,
        quality_flags=("low_scan_support",),
        area=1_000_000.0,
    )
    larger_supported_peak = _sc(
        Confidence.MEDIUM,
        27.10,
        80_000.0,
        None,
        selection_quality_penalty=0.0,
        area=45_000_000.0,
    )
    near_low_scan = replace(
        near_low_scan,
        evidence_score=_score_for_selector(105),
    )
    larger_supported_peak = replace(
        larger_supported_peak,
        evidence_score=_score_for_selector(95),
    )

    assert (
        select_candidate_with_confidence(
            [near_low_scan, larger_supported_peak],
            selection_rt=25.94,
        )
        is larger_supported_peak
    )


def test_selector_keeps_low_scan_prior_when_large_alternative_is_outside_rt_context(
) -> None:
    near_low_scan = _sc(
        Confidence.HIGH,
        25.90,
        12_000.0,
        None,
        selection_quality_penalty=0.25,
        quality_flags=("low_scan_support",),
        area=1_000_000.0,
    )
    far_large_peak = _sc(
        Confidence.MEDIUM,
        29.00,
        200_000.0,
        None,
        selection_quality_penalty=0.0,
        area=120_000_000.0,
    )
    near_low_scan = replace(
        near_low_scan,
        evidence_score=_score_for_selector(105),
    )
    far_large_peak = replace(far_large_peak, evidence_score=_score_for_selector(95))

    assert (
        select_candidate_with_confidence(
            [near_low_scan, far_large_peak],
            selection_rt=25.94,
        )
        is near_low_scan
    )


def test_selector_demotes_near_prior_tiny_area_when_strict_nl_alternative_dominates(
) -> None:
    near_tiny_peak = _sc(
        Confidence.HIGH,
        26.26,
        35_000.0,
        None,
        selection_quality_penalty=0.0,
        area=100_000.0,
    )
    dominant_strict_nl_peak = _sc(
        Confidence.MEDIUM,
        24.18,
        30_000_000.0,
        None,
        selection_quality_penalty=0.0,
        area=480_000_000.0,
    )
    near_tiny_peak = replace(
        near_tiny_peak,
        evidence_score=_score_for_selector(
            105,
            support_labels=("strict_nl_ok", "rt_prior_close"),
        ),
    )
    dominant_strict_nl_peak = replace(
        dominant_strict_nl_peak,
        evidence_score=_score_for_selector(
            85,
            support_labels=("strict_nl_ok", "local_sn_strong"),
        ),
    )

    assert (
        select_candidate_with_confidence(
            [near_tiny_peak, dominant_strict_nl_peak],
            selection_rt=26.70,
        )
        is dominant_strict_nl_peak
    )


def test_selector_keeps_near_prior_tiny_area_when_dominant_alternative_lacks_strict_nl(
) -> None:
    near_tiny_peak = _sc(
        Confidence.HIGH,
        26.26,
        35_000.0,
        None,
        selection_quality_penalty=0.0,
        area=100_000.0,
    )
    dominant_ms1_only_peak = _sc(
        Confidence.MEDIUM,
        24.18,
        30_000_000.0,
        None,
        selection_quality_penalty=0.0,
        area=480_000_000.0,
    )
    near_tiny_peak = replace(
        near_tiny_peak,
        evidence_score=_score_for_selector(
            105,
            support_labels=("strict_nl_ok", "rt_prior_close"),
        ),
    )
    dominant_ms1_only_peak = replace(
        dominant_ms1_only_peak,
        evidence_score=_score_for_selector(
            85,
            support_labels=("local_sn_strong",),
        ),
    )

    assert (
        select_candidate_with_confidence(
            [near_tiny_peak, dominant_ms1_only_peak],
            selection_rt=26.70,
        )
        is near_tiny_peak
    )


def test_selector_keeps_near_prior_tiny_area_when_dominant_alternative_is_too_far(
) -> None:
    near_tiny_peak = _sc(
        Confidence.HIGH,
        26.26,
        35_000.0,
        None,
        selection_quality_penalty=0.0,
        area=100_000.0,
    )
    far_dominant_peak = _sc(
        Confidence.MEDIUM,
        23.20,
        30_000_000.0,
        None,
        selection_quality_penalty=0.0,
        area=480_000_000.0,
    )
    near_tiny_peak = replace(
        near_tiny_peak,
        evidence_score=_score_for_selector(
            105,
            support_labels=("strict_nl_ok", "rt_prior_close"),
        ),
    )
    far_dominant_peak = replace(
        far_dominant_peak,
        evidence_score=_score_for_selector(
            85,
            support_labels=("strict_nl_ok", "local_sn_strong"),
        ),
    )

    assert (
        select_candidate_with_confidence(
            [near_tiny_peak, far_dominant_peak],
            selection_rt=26.70,
        )
        is near_tiny_peak
    )


def test_strict_selector_keeps_rt_anchor_without_paired_ratio_plausibility() -> None:
    near_anchor_peak = _sc(
        Confidence.HIGH,
        16.43,
        16_000.0,
        None,
        selection_quality_penalty=0.0,
        area=1_600_000.0,
    )
    right_complete_peak = _sc(
        Confidence.HIGH,
        17.18,
        125_000.0,
        None,
        selection_quality_penalty=0.0,
        area=80_000_000.0,
    )
    near_anchor_peak = replace(
        near_anchor_peak,
        evidence_score=_score_for_selector(
            105,
            support_labels=("strict_nl_ok", "rt_prior_close"),
        ),
    )
    right_complete_peak = replace(
        right_complete_peak,
        evidence_score=_score_for_selector(
            125,
            support_labels=("local_sn_strong", "shape_clean", "trace_clean"),
        ),
    )

    assert (
        select_candidate_with_confidence(
            [near_anchor_peak, right_complete_peak],
            selection_rt=16.43,
            strict_selection_rt=True,
        )
        is near_anchor_peak
    )


def test_strict_selector_does_not_product_switch_on_paired_ratio_label() -> None:
    near_anchor_peak = _sc(
        Confidence.HIGH,
        16.43,
        16_000.0,
        None,
        selection_quality_penalty=0.0,
        area=1_600_000.0,
    )
    right_complete_peak = _sc(
        Confidence.VERY_LOW,
        17.18,
        125_000.0,
        None,
        selection_quality_penalty=0.0,
        area=80_000_000.0,
    )
    near_anchor_peak = replace(
        near_anchor_peak,
        evidence_score=_score_for_selector(
            105,
            support_labels=("strict_nl_ok", "rt_prior_close"),
        ),
    )
    right_complete_peak = replace(
        right_complete_peak,
        evidence_score=_score_for_selector(
            125,
            support_labels=(
                "paired_area_ratio_plausible",
                "local_sn_strong",
                "shape_clean",
                "trace_clean",
            ),
            concern_labels=("nl_fail",),
            cap_labels=("nl_fail_cap",),
        ),
    )

    assert (
        select_candidate_with_confidence(
            [near_anchor_peak, right_complete_peak],
            selection_rt=16.43,
            strict_selection_rt=True,
        )
        is near_anchor_peak
    )


def test_selector_with_paired_prior_evidence_prefers_prior_distance_before_quality(
) -> None:
    clean_far = _sc(
        Confidence.LOW,
        10.35,
        1000.0,
        10.0,
        quality_penalty=0,
        prefer_rt_prior_tiebreak=True,
    )
    weak_near = _sc(
        Confidence.LOW,
        10.03,
        700.0,
        10.0,
        quality_penalty=1,
        prefer_rt_prior_tiebreak=True,
    )

    assert select_candidate_with_confidence([clean_far, weak_near]) is weak_near


def test_selector_with_paired_prior_evidence_uses_effective_score() -> None:
    near_weaker = _sc(
        Confidence.HIGH,
        10.02,
        500.0,
        10.0,
        quality_penalty=0,
        selection_quality_penalty=0.0,
        prefer_rt_prior_tiebreak=True,
    )
    farther_stronger = _sc(
        Confidence.HIGH,
        10.15,
        1200.0,
        10.0,
        quality_penalty=0,
        selection_quality_penalty=0.0,
        prefer_rt_prior_tiebreak=True,
    )
    near_weaker = replace(near_weaker, evidence_score=_score_for_selector(82))
    farther_stronger = replace(
        farther_stronger,
        evidence_score=_score_for_selector(115),
    )

    assert (
        select_candidate_with_confidence([near_weaker, farther_stronger])
        is farther_stronger
    )
