from __future__ import annotations

from xic_extractor.peak_detection.peak_scoring_evidence import EvidenceScore
from xic_extractor.peak_detection.scoring_models import Confidence

_EVIDENCE_REASON_LABELS = {
    "strict_nl_ok": "strict NL OK",
    "no_nl_required": "no NL required",
    "rt_prior_close": "RT prior close",
    "paired_istd_aligned": "paired ISTD aligned",
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


def build_reason(
    signals: list[tuple[int, str]],
    istd_confidence_note: str | None,
    extra_notes: list[str] | None = None,
) -> str:
    concerns = [(severity, label) for severity, label in signals if severity >= 1]
    if not concerns and istd_confidence_note is None and not extra_notes:
        return "all checks passed"

    parts: list[str] = []
    if concerns:
        concerns.sort(key=lambda pair: -pair[0])
        phrase = "; ".join(
            f"{label} ({'major' if severity == 2 else 'minor'})"
            for severity, label in concerns
        )
        parts.append(f"concerns: {phrase}")
    if extra_notes:
        parts.extend(extra_notes)
    if istd_confidence_note is not None:
        parts.append(istd_confidence_note)
    return "; ".join(parts)


def build_evidence_reason(
    evidence_score: EvidenceScore,
    istd_confidence_note: str | None,
    extra_notes: list[str] | None = None,
    *,
    count_no_ms2_as_detected: bool = False,
) -> str:
    parts: list[str] = []
    if _is_review_only_evidence(
        evidence_score,
        count_no_ms2_as_detected=count_no_ms2_as_detected,
    ):
        parts.append("decision: review only, not counted")
    else:
        parts.append("decision: accepted")

    for cap in evidence_score.cap_labels:
        max_confidence, cap_name = _CAP_REASON_LABELS.get(
            cap, ("VERY_LOW", cap.removesuffix("_cap").replace("_", " "))
        )
        parts.append(f"cap: {max_confidence} due to {cap_name}")

    if evidence_score.support_labels:
        support = "; ".join(
            _EVIDENCE_REASON_LABELS.get(label, label)
            for label in evidence_score.support_labels[:3]
        )
        parts.append(f"support: {support}")

    if evidence_score.concern_labels:
        concerns = "; ".join(
            _EVIDENCE_REASON_LABELS.get(label, label)
            for label in evidence_score.concern_labels[:4]
        )
        parts.append(f"concerns: {concerns}")

    if extra_notes:
        parts.extend(extra_notes)

    if istd_confidence_note is not None:
        parts.append(istd_confidence_note)

    return "; ".join(parts) if parts else "all checks passed"


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


def _is_review_only_evidence(
    evidence_score: EvidenceScore,
    *,
    count_no_ms2_as_detected: bool,
) -> bool:
    if evidence_score.confidence == Confidence.VERY_LOW.value:
        return True
    return "no_ms2_cap" in evidence_score.cap_labels and not count_no_ms2_as_detected
