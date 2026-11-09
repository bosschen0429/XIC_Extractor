from dataclasses import dataclass, replace

from xic_extractor.discovery.evidence_config import (
    DEFAULT_EVIDENCE_PROFILE,
    DiscoveryEvidenceThresholds,
)
from xic_extractor.discovery.models import DiscoveryPeak
from xic_extractor.discovery.settings import DiscoverySettings


@dataclass(frozen=True)
class DiscoveryEvidence:
    score: int
    tier: str
    ms2_support: str
    ms1_support: str
    rt_alignment: str


def score_discovery_evidence(
    candidate: DiscoveryPeak,
    *,
    settings: DiscoverySettings,
) -> DiscoveryEvidence:
    profile = settings.evidence_profile
    weights = profile.weights
    thresholds = profile.thresholds

    ms2_support = classify_ms2_support(candidate, thresholds=thresholds)
    ms1_support = classify_ms1_support(candidate, thresholds=thresholds)
    rt_alignment = classify_rt_alignment(candidate, thresholds=thresholds)

    score = weights.ms1_peak_present
    score += min(
        weights.seed_event_max,
        max(0, candidate.attached_event_count) * weights.seed_event_per,
    )

    delta = abs(candidate.ms1_event_delta_min)
    if delta <= thresholds.rt_aligned_max_min:
        score += weights.rt_aligned
    elif delta <= thresholds.rt_near_max_min:
        score += weights.rt_near
    elif delta <= thresholds.rt_shifted_max_min:
        score += weights.rt_shifted

    if candidate.ms2_product_max_intensity >= thresholds.product_intensity_high_min:
        score += weights.product_intensity_high
    elif candidate.ms2_product_max_intensity >= thresholds.product_intensity_med_min:
        score += weights.product_intensity_med

    if candidate.ms1_area >= thresholds.area_high_min:
        score += weights.area_high
    elif candidate.ms1_area >= thresholds.area_med_min:
        score += weights.area_med

    scan_support = candidate.ms1_scan_support_score
    if scan_support is not None:
        if scan_support >= thresholds.scan_support_high_score_min:
            score += weights.scan_support_high
        elif scan_support <= thresholds.scan_support_low_score_max:
            score += weights.scan_support_low

    score = min(100, max(0, score))
    return DiscoveryEvidence(
        score=score,
        tier=evidence_tier_from_score(score),
        ms2_support=ms2_support,
        ms1_support=ms1_support,
        rt_alignment=rt_alignment,
    )


def classify_ms2_support(
    candidate: DiscoveryPeak,
    *,
    thresholds: DiscoveryEvidenceThresholds = DEFAULT_EVIDENCE_PROFILE.thresholds,
) -> str:
    if (
        candidate.attached_event_count >= 3
        or (
            candidate.attached_event_count >= 2
            and candidate.ms2_product_max_intensity
            >= thresholds.product_intensity_med_min
        )
        or candidate.ms2_product_max_intensity >= thresholds.product_intensity_high_min
    ):
        return "strong"
    if (
        candidate.attached_event_count >= 2
        or candidate.ms2_product_max_intensity >= thresholds.product_intensity_med_min
    ):
        return "moderate"
    return "weak"


def classify_ms1_support(
    candidate: DiscoveryPeak,
    *,
    thresholds: DiscoveryEvidenceThresholds = DEFAULT_EVIDENCE_PROFILE.thresholds,
) -> str:
    scan_support = candidate.ms1_scan_support_score
    high_scan_support = (
        scan_support is not None
        and scan_support >= thresholds.scan_support_high_score_min
    )
    low_scan_support = (
        scan_support is None or scan_support <= thresholds.scan_support_low_score_max
    )
    if (
        candidate.ms1_area >= thresholds.ms1_support_strong_area_min
        and high_scan_support
    ):
        return "strong"
    if candidate.ms1_area >= thresholds.ms1_support_moderate_area_min or (
        high_scan_support and not low_scan_support
    ):
        return "moderate"
    return "weak"


def classify_rt_alignment(
    candidate: DiscoveryPeak,
    *,
    thresholds: DiscoveryEvidenceThresholds = DEFAULT_EVIDENCE_PROFILE.thresholds,
) -> str:
    delta = abs(candidate.ms1_event_delta_min)
    if delta <= thresholds.rt_aligned_max_min:
        return "aligned"
    if delta <= thresholds.rt_near_max_min:
        return "near"
    return "shifted"


def evidence_tier_from_score(score: int) -> str:
    if score >= 80:
        return "A"
    if score >= 60:
        return "B"
    if score >= 40:
        return "C"
    if score >= 20:
        return "D"
    return "E"


def score_peak_evidence(
    peaks: tuple[DiscoveryPeak, ...],
    *,
    settings: DiscoverySettings,
) -> tuple[DiscoveryPeak, ...]:
    scored: list[DiscoveryPeak] = []
    for peak in peaks:
        evidence = score_discovery_evidence(peak, settings=settings)
        scored.append(
            replace(
                peak,
                evidence_score=evidence.score,
                evidence_tier=evidence.tier,
                ms2_support=evidence.ms2_support,
                ms1_support=evidence.ms1_support,
                rt_alignment=evidence.rt_alignment,
            )
        )
    return tuple(scored)
