from dataclasses import dataclass

from xic_extractor.discovery.evidence_config import (
    DEFAULT_EVIDENCE_PROFILE,
    DiscoveryEvidenceThresholds,
)
from xic_extractor.discovery.models import DiscoveryCandidate, DiscoverySettings


@dataclass(frozen=True)
class DiscoveryEvidence:
    score: int
    tier: str
    ms2_support: str
    ms1_support: str
    rt_alignment: str


def score_discovery_evidence(
    candidate: DiscoveryCandidate,
    *,
    settings: DiscoverySettings,
) -> DiscoveryEvidence:
    profile = settings.evidence_profile
    weights = profile.weights
    thresholds = profile.thresholds

    ms2_support = classify_ms2_support(candidate, thresholds=thresholds)
    ms1_support = classify_ms1_support(candidate, thresholds=thresholds)
    rt_alignment = classify_rt_alignment(candidate, thresholds=thresholds)

    score = 0
    if candidate.ms1_peak_found:
        score += weights.ms1_peak_present
    else:
        score += weights.ms1_peak_absent

    score += min(
        weights.seed_event_max,
        max(0, candidate.seed_event_count) * weights.seed_event_per,
    )

    if candidate.ms1_seed_delta_min is not None:
        delta = abs(candidate.ms1_seed_delta_min)
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

    if candidate.ms1_area is not None:
        if candidate.ms1_area >= thresholds.area_high_min:
            score += weights.area_high
        elif candidate.ms1_area >= thresholds.area_med_min:
            score += weights.area_med

    if candidate.ms1_scan_support_score is not None:
        if candidate.ms1_scan_support_score >= thresholds.scan_support_high_score_min:
            score += weights.scan_support_high
        elif (
            candidate.ms1_peak_found
            and candidate.ms1_scan_support_score
            <= thresholds.scan_support_low_score_max
        ):
            score += weights.scan_support_low
    elif candidate.ms1_peak_found:
        if candidate.ms1_trace_quality.upper() in {"GOOD", "CLEAN"}:
            score += weights.legacy_trace_quality_high
        elif candidate.ms1_trace_quality.upper() in {"POOR", "MISSING"}:
            score += weights.legacy_trace_quality_low

    score = min(100, max(0, score))
    return DiscoveryEvidence(
        score=score,
        tier=evidence_tier_from_score(score),
        ms2_support=ms2_support,
        ms1_support=ms1_support,
        rt_alignment=rt_alignment,
    )


def classify_ms2_support(
    candidate: DiscoveryCandidate,
    *,
    thresholds: DiscoveryEvidenceThresholds = DEFAULT_EVIDENCE_PROFILE.thresholds,
) -> str:
    if (
        candidate.seed_event_count >= 3
        or (
            candidate.seed_event_count >= 2
            and candidate.ms2_product_max_intensity
            >= thresholds.product_intensity_med_min
        )
        or candidate.ms2_product_max_intensity >= thresholds.product_intensity_high_min
    ):
        return "strong"
    if (
        candidate.seed_event_count >= 2
        or candidate.ms2_product_max_intensity >= thresholds.product_intensity_med_min
    ):
        return "moderate"
    return "weak"


def classify_ms1_support(
    candidate: DiscoveryCandidate,
    *,
    thresholds: DiscoveryEvidenceThresholds = DEFAULT_EVIDENCE_PROFILE.thresholds,
) -> str:
    if not candidate.ms1_peak_found or candidate.ms1_area is None:
        return "missing"
    scan_support = candidate.ms1_scan_support_score
    if scan_support is None:
        trace_quality = candidate.ms1_trace_quality.upper()
        high_scan_support = trace_quality in {"GOOD", "CLEAN"}
        low_scan_support = trace_quality in {"POOR", "MISSING"}
    else:
        high_scan_support = scan_support >= thresholds.scan_support_high_score_min
        low_scan_support = scan_support <= thresholds.scan_support_low_score_max
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
    candidate: DiscoveryCandidate,
    *,
    thresholds: DiscoveryEvidenceThresholds = DEFAULT_EVIDENCE_PROFILE.thresholds,
) -> str:
    if candidate.ms1_seed_delta_min is None:
        return "missing"
    delta = abs(candidate.ms1_seed_delta_min)
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
