from __future__ import annotations

from xic_extractor.configuration.models import Target
from xic_extractor.output.messages import DiagnosticRecord
from xic_extractor.peak_detection.models import PeakDetectionResult

PAIRED_FALLBACK_ISTD_PEAK_DELTA_MAX_MIN: float = 0.5


def paired_anchor_mismatch_diagnostic(
    sample_name: str,
    target: Target,
    peak_result: PeakDetectionResult,
    *,
    reference_rt: float | None,
    anchor_rt: float | None,
    strict_preferred_rt: bool,
) -> DiagnosticRecord | None:
    if not strict_preferred_rt or reference_rt is None or peak_result.peak is None:
        return None

    peak = peak_result.peak
    expected_rt = anchor_rt if anchor_rt is not None else reference_rt
    anchor_label = "target RT reference"
    allowed_delta = PAIRED_FALLBACK_ISTD_PEAK_DELTA_MAX_MIN
    secondary_note = f"; ISTD anchor at {reference_rt:.3f} min"

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
            f"{secondary_note}; retained as localization review evidence"
        ),
    )
