from __future__ import annotations

from typing import Any

from xic_extractor.peak_detection.hypotheses import (
    PeakHypothesis,
    build_peak_hypotheses,
)


def build_targeted_peak_hypotheses(**values: Any) -> tuple[PeakHypothesis, ...]:
    """Convert current Targeted callers into the neutral hypothesis owner.

    Remove after the named candidate-table, boundary, and handoff callers receive
    owner-issued ``TargetSearchScope`` values directly.
    """

    if "analysis_mode" in values or "common_evidence_source_id" in values:
        raise TypeError(
            "Targeted hypothesis adapter owns analysis mode and evidence source"
        )
    target_label = values.get("target_label")
    if (
        not isinstance(target_label, str)
        or not target_label
        or target_label != target_label.strip()
    ):
        raise ValueError("Targeted hypothesis adapter requires a normalized label")
    return build_peak_hypotheses(
        analysis_mode="targeted",
        common_evidence_source_id=target_label,
        **values,
    )


__all__ = ("build_targeted_peak_hypotheses",)
