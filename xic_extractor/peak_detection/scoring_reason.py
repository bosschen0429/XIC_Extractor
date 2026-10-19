from __future__ import annotations

from xic_extractor.peak_detection.peak_scoring_evidence import EvidenceScore

_EVIDENCE_REASON_LABELS = {
    "strict_nl_ok": "strict NL OK",
    "no_nl_required": "no NL required",
    "rt_prior_close": "RT prior close",
    "ms2_trace_strong": "MS2 trace strong",
    "ms2_trace_moderate": "MS2 trace moderate",
    "cwt_same_apex_support": "CWT same-apex support",
    "local_sn_strong": "local S/N strong",
    "shape_clean": "shape clean",
    "trace_clean": "trace clean",
    "paired_area_ratio_plausible": "paired area ratio plausible",
    "nl_fail": "nl fail",
    "no_ms2": "no MS2",
    "rt_prior_far": "rt prior far",
    "rt_prior_borderline": "rt prior borderline",
    "rt_centrality_borderline": "RT centrality borderline",
    "rt_centrality_poor": "RT centrality poor",
    "local_sn_borderline": "local S/N borderline",
    "local_sn_poor": "local S/N poor",
    "shape_borderline": "shape borderline",
    "shape_poor": "shape poor",
    "noise_shape_borderline": "noise shape borderline",
    "noise_shape_poor": "noise shape poor",
    "anchor_mismatch": "anchor mismatch",
    "ms2_trace_weak": "MS2 trace weak",
    "sparse_apex_ms2": "sparse apex MS2",
    "low_scan_support": "low scan support",
    "low_trace_continuity": "low trace continuity",
    "poor_edge_recovery": "poor edge recovery",
    "hard_quality_flag": "hard quality flag",
}

_CAP_REASON_LABELS = {
    "nl_fail_cap": ("VERY_LOW", "nl fail"),
    "no_ms2_cap": ("LOW", "no MS2"),
    "anchor_mismatch_cap": ("VERY_LOW", "anchor mismatch"),
    "zero_area_cap": ("VERY_LOW", "zero area"),
    "rt_window_cap": ("VERY_LOW", "target RT window"),
    "trace_quality_cap": ("MEDIUM", "trace quality"),
    "hard_quality_flag_cap": ("MEDIUM", "hard quality flag"),
}


def score_breakdown_fields(
    evidence_score: EvidenceScore | None,
) -> tuple[tuple[str, str], ...]:
    if evidence_score is None:
        return ()
    return (
        ("Final Confidence", evidence_score.confidence),
        ("Caps", "; ".join(evidence_score.cap_labels)),
        ("Raw Score", str(evidence_score.raw_score)),
        ("Support", "; ".join(evidence_score.support_labels)),
        ("Concerns", "; ".join(evidence_score.concern_labels)),
        ("Base Score", str(evidence_score.base_score)),
        ("Positive Points", str(evidence_score.positive_points)),
        ("Negative Points", str(evidence_score.negative_points)),
    )
