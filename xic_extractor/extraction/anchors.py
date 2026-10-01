from __future__ import annotations

from dataclasses import replace

from xic_extractor.configuration.models import Target
from xic_extractor.output.messages import DiagnosticRecord
from xic_extractor.peak_detection.models import PeakDetectionResult

PAIRED_TARGET_ANCHOR_PEAK_DELTA_MAX_MIN: float = 0.25
PAIRED_TARGET_NL_ANCHOR_REFERENCE_DELTA_MAX_MIN: float = 0.5
PAIRED_FALLBACK_ISTD_PEAK_DELTA_MAX_MIN: float = 0.5
ANCHOR_PEAK_DELTA_WARN_MIN: float = 0.5
ANCHOR_MISMATCH_EVIDENCE_POINTS: int = 45


def paired_anchor_mismatch_diagnostic(
    sample_name: str,
    target: Target,
    peak_result: PeakDetectionResult,
    *,
    reference_rt: float | None,
    anchor_rt: float | None,
    anchor_used: bool = False,
    strict_preferred_rt: bool,
) -> DiagnosticRecord | None:
    if (
        not strict_preferred_rt
        or reference_rt is None
        or peak_result.peak is None
    ):
        return None

    peak = peak_result.peak
    if anchor_rt is not None and anchor_used:
        expected_rt = anchor_rt
        anchor_label = "target NL anchor"
        allowed_delta = PAIRED_TARGET_ANCHOR_PEAK_DELTA_MAX_MIN
        secondary_note = f"; ISTD anchor at {reference_rt:.3f} min"
    elif anchor_rt is not None:
        expected_rt = anchor_rt
        anchor_label = "target RT reference"
        allowed_delta = PAIRED_FALLBACK_ISTD_PEAK_DELTA_MAX_MIN
        secondary_note = f"; ISTD anchor at {reference_rt:.3f} min"
    else:
        expected_rt = reference_rt
        anchor_label = "ISTD anchor"
        allowed_delta = PAIRED_FALLBACK_ISTD_PEAK_DELTA_MAX_MIN
        secondary_note = "; no target NL anchor"

    delta = abs(peak.rt - expected_rt)
    if delta <= allowed_delta:
        return None

    return DiagnosticRecord(
        sample_name=sample_name,
        target_label=target.label,
        issue="ANCHOR_RT_MISMATCH",
        reason=(
            f"Paired analyte peak RT {peak.rt:.3f} min deviates "
            f"{delta:.2f} min from {anchor_label} at "
            f"{expected_rt:.3f} min (allowed ±{allowed_delta:.2f} min)"
            f"{secondary_note}; retained with downgraded confidence for manual review"
        ),
    )


def apply_anchor_mismatch_penalty(
    peak_result: PeakDetectionResult,
    mismatch_reason: str,
) -> PeakDetectionResult:
    reason = (
        "decision: review only, not counted; "
        "cap: VERY_LOW due to anchor mismatch; "
        f"concerns: anchor mismatch; {mismatch_reason}"
    )
    if peak_result.reason:
        details = _reason_without_decision(peak_result.reason)
        if details:
            reason = f"{reason}; {details}"
    confidence = anchor_mismatch_confidence(peak_result.confidence)
    return replace(
        peak_result,
        confidence=confidence,
        reason=reason,
        score_breakdown=_anchor_mismatch_score_breakdown(
            peak_result.score_breakdown,
            confidence,
        ),
    )


def anchor_mismatch_confidence(_confidence: str | None) -> str:
    return "VERY_LOW"


def _anchor_mismatch_score_breakdown(
    score_breakdown: tuple[tuple[str, str], ...],
    confidence: str,
) -> tuple[tuple[str, str], ...]:
    if not score_breakdown:
        return ()

    existing = dict(score_breakdown)
    updates = {
        "Final Confidence": confidence,
        "Caps": _append_semicolon_label(
            existing.get("Caps", ""),
            "anchor_mismatch_cap",
        ),
        "Concerns": _append_semicolon_label(
            existing.get("Concerns", ""),
            "anchor_mismatch",
        ),
        "Negative Points": _add_numeric_text(
            existing.get("Negative Points", ""),
            ANCHOR_MISMATCH_EVIDENCE_POINTS,
        ),
        "Raw Score": _add_numeric_text(
            existing.get("Raw Score", ""),
            -ANCHOR_MISMATCH_EVIDENCE_POINTS,
        ),
    }
    return tuple((label, updates.get(label, value)) for label, value in score_breakdown)


def _reason_without_decision(reason: str) -> str:
    parts = [part.strip() for part in reason.split(";") if part.strip()]
    filtered = [part for part in parts if not part.startswith("decision:")]
    return "; ".join(filtered)


def _append_semicolon_label(value: str, label: str) -> str:
    labels = [part.strip() for part in value.split(";") if part.strip()]
    if label not in labels:
        labels.append(label)
    return "; ".join(labels)


def _add_numeric_text(value: str, delta: int) -> str:
    if not value:
        return value
    try:
        return str(int(float(value)) + delta)
    except ValueError:
        return value
