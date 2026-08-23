from __future__ import annotations

from dataclasses import dataclass

from xic_extractor.peak_detection.chrom_peak_segment_projection import (
    chrom_peak_segment_promoted_hypothesis_from_hypothesis,
)
from xic_extractor.peak_detection.chrom_peak_segments import ChromPeakSegmentPolicy
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.selected_envelope import (
    SelectedEnvelopeBoundaryEvaluation,
    SelectedEnvelopePolicy,
)
from xic_extractor.peak_detection.selected_envelope_projection import (
    selected_envelope_promoted_hypothesis_from_hypothesis,
)


@dataclass(frozen=True, slots=True)
class TargetedBoundaryPromotion:
    hypothesis: PeakHypothesis
    selected_envelope_evaluation: SelectedEnvelopeBoundaryEvaluation | None
    chrom_segment_boundary_accepted: bool


def promote_targeted_boundary_hypothesis(
    hypothesis: PeakHypothesis,
    *,
    rt: object,
    intensity: object,
    context_rt_start: float,
    context_rt_end: float,
    morphology_window_points: int,
) -> TargetedBoundaryPromotion:
    """Apply the existing Targeted Gaussian15 policy inside capture ownership."""

    selected_envelope_evaluation = None
    try:
        promoted_hypothesis, selected_envelope_evaluation = (
            selected_envelope_promoted_hypothesis_from_hypothesis(
                hypothesis,
                rt_values=rt,
                intensity_values=intensity,
                quantitation_context_rt_start=context_rt_start,
                quantitation_context_rt_end=context_rt_end,
                policy=SelectedEnvelopePolicy(
                    morphology_trace_window_points=morphology_window_points,
                ),
            )
        )
    except ValueError:
        promoted_hypothesis = hypothesis
    try:
        chrom_promoted_hypothesis, chrom_segment_projection = (
            chrom_peak_segment_promoted_hypothesis_from_hypothesis(
                hypothesis,
                rt_values=rt,
                intensity_values=intensity,
                quantitation_context_rt_start=context_rt_start,
                quantitation_context_rt_end=context_rt_end,
                selected_envelope_evaluation=selected_envelope_evaluation,
                policy=ChromPeakSegmentPolicy(
                    morphology_trace_window_points=morphology_window_points,
                ),
            )
        )
    except ValueError:
        chrom_promoted_hypothesis = hypothesis
        chrom_segment_projection = None
    chrom_segment_boundary_accepted = bool(
        chrom_segment_projection is not None and chrom_segment_projection.accepted
    )
    if chrom_segment_boundary_accepted:
        promoted_hypothesis = chrom_promoted_hypothesis
    return TargetedBoundaryPromotion(
        hypothesis=promoted_hypothesis,
        selected_envelope_evaluation=selected_envelope_evaluation,
        chrom_segment_boundary_accepted=chrom_segment_boundary_accepted,
    )


__all__ = (
    "TargetedBoundaryPromotion",
    "promote_targeted_boundary_hypothesis",
)
