"""Paired isotope-ISTD co-elution for Target analytes.

A spiked isotope-labelled ISTD elutes with its analyte, apart from a small
method offset: the difference between their configured expected RTs, measured
on standards. Both sit on the same chromatographic drift, so the offset holds
across injections even when absolute RT does not.

The analyte apex must lie within the ISTD's own half-prominence width of the
ISTD apex plus that offset. The width is read from the ISTD peak of the same
injection: poor chromatography widens it, while a sharp ISTD keeps a closely
eluting isomer with identical fragments (N4-medC beside 5-medC) out.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from xic_extractor.peak_detection.ms1_morphology import compute_shape_metrics

if TYPE_CHECKING:
    from xic_extractor.extraction.result_models import ExtractionResult


def istd_half_width_min(result: ExtractionResult | None) -> float | None:
    """Half-prominence width of the ISTD's reported physical peak, in minutes."""
    evaluation = None if result is None else result.physical_evaluation
    segment = None if evaluation is None else evaluation.answer.selected_segment
    if evaluation is None or segment is None:
        return None
    raw = np.asarray(evaluation.prepared.raw, dtype=float)
    if segment.end_index - segment.start_index < 3:
        return None
    apex = segment.start_index + int(
        np.argmax(raw[segment.start_index : segment.end_index])
    )
    _ratio, width = compute_shape_metrics(
        np.asarray(evaluation.prepared.rt, dtype=float), raw, apex
    )
    return width


def coelutes_with_istd(
    analyte_rt: float, expected_rt: float, istd_half_width: float
) -> bool:
    """Whether an analyte apex sits at its ISTD-derived position."""
    return abs(analyte_rt - expected_rt) <= istd_half_width
