from xic_extractor.discovery.settings import DiscoverySettings


def score_discovery_evidence(
    *,
    attached_event_count: int,
    ms1_event_delta_min: float,
    ms2_product_max_intensity: float,
    ms1_area: float,
    ms1_scan_support_score: float | None,
    settings: DiscoverySettings,
) -> int:
    """One peak's evidence score, 0-100, from its MS2 events and MS1 peak."""
    profile = settings.evidence_profile
    weights = profile.weights
    thresholds = profile.thresholds

    score = weights.ms1_peak_present
    score += min(
        weights.seed_event_max,
        max(0, attached_event_count) * weights.seed_event_per,
    )

    delta = abs(ms1_event_delta_min)
    if delta <= thresholds.rt_aligned_max_min:
        score += weights.rt_aligned
    elif delta <= thresholds.rt_near_max_min:
        score += weights.rt_near
    elif delta <= thresholds.rt_shifted_max_min:
        score += weights.rt_shifted

    if ms2_product_max_intensity >= thresholds.product_intensity_high_min:
        score += weights.product_intensity_high
    elif ms2_product_max_intensity >= thresholds.product_intensity_med_min:
        score += weights.product_intensity_med

    if ms1_area >= thresholds.area_high_min:
        score += weights.area_high
    elif ms1_area >= thresholds.area_med_min:
        score += weights.area_med

    if ms1_scan_support_score is not None:
        if ms1_scan_support_score >= thresholds.scan_support_high_score_min:
            score += weights.scan_support_high
        elif ms1_scan_support_score <= thresholds.scan_support_low_score_max:
            score += weights.scan_support_low

    return min(100, max(0, score))
