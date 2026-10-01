from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np

from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.evidence.neutral_loss import CandidateMS2Evidence
from xic_extractor.extraction.peak_candidate_boundaries import (
    PeakCandidateBoundaryRow,
    append_peak_candidate_boundary_rows_from_hypotheses,
)
from xic_extractor.extraction.peak_candidate_table import (
    PeakCandidateTableRow,
    append_peak_candidate_rows_from_hypotheses,
    build_peak_candidate_audit_hypotheses,
    with_product_selected_marker,
)
from xic_extractor.peak_detection.cwt import add_cwt_proposals_for_audit
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.models import PeakCandidate, PeakDetectionResult
from xic_extractor.peak_detection.selected_envelope_diagnostics import (
    SelectedEnvelopeDiagnosticRow,
)
from xic_extractor.peak_detection.selected_envelope_projection import (
    selected_envelope_diagnostic_row_from_hypothesis,
)
from xic_extractor.peak_detection.traces import TraceGroup


def append_peak_audit_rows(
    *,
    peak_candidate_rows: list[PeakCandidateTableRow] | None,
    peak_candidate_boundary_rows: list[PeakCandidateBoundaryRow] | None,
    selected_envelope_diagnostic_rows: (
        list[SelectedEnvelopeDiagnosticRow] | None
    ) = None,
    config: ExtractionConfig,
    sample_name: str,
    target: Target,
    peak_result: PeakDetectionResult,
    candidate_ms2_builder: Callable[[PeakCandidate], CandidateMS2Evidence | None],
    rt: Any,
    intensity: Any,
    trace_group: TraceGroup | None = None,
    product_selected_candidate_id: str | None = None,
    product_selected_hypothesis: PeakHypothesis | None = None,
    scoring_context_builder: Callable[[PeakCandidate], Any] | None = None,
    istd_confidence_note: str | None = None,
) -> None:
    if (
        not config.emit_peak_candidates
        or (peak_candidate_rows is None and peak_candidate_boundary_rows is None)
    ):
        return
    audit_peak_result = add_cwt_proposals_for_audit(
        peak_result,
        rt,
        intensity,
        config,
    )
    hypotheses = build_peak_candidate_audit_hypotheses(
        config=config,
        sample_name=sample_name,
        target=target,
        peak_result=peak_result,
        candidate_ms2_builder=candidate_ms2_builder,
        audit_peak_result=audit_peak_result,
        rt=rt,
        intensity=intensity,
        trace_group=trace_group,
        scoring_context_builder=scoring_context_builder,
        istd_confidence_note=istd_confidence_note,
        include_candidate_ms2_evidence=peak_candidate_rows is not None,
    )
    hypotheses = with_product_selected_marker(
        hypotheses,
        product_selected_candidate_id,
        selected_hypothesis=product_selected_hypothesis,
    )
    append_peak_candidate_rows_from_hypotheses(
        peak_candidate_rows,
        config,
        sample_name,
        hypotheses,
    )
    append_peak_candidate_boundary_rows_from_hypotheses(
        peak_candidate_boundary_rows,
        config,
        sample_name,
        target,
        hypotheses,
        rt=rt,
        intensity=intensity,
        trace_group=trace_group,
    )
    append_selected_envelope_diagnostic_rows_from_hypotheses(
        selected_envelope_diagnostic_rows,
        config,
        sample_name,
        hypotheses,
        rt=rt,
        intensity=intensity,
        trace_group=trace_group,
        product_selected_hypothesis=product_selected_hypothesis,
    )


def append_selected_envelope_diagnostic_rows_from_hypotheses(
    rows: list[SelectedEnvelopeDiagnosticRow] | None,
    config: ExtractionConfig,
    sample_name: str,
    hypotheses: tuple[PeakHypothesis, ...],
    *,
    rt: Any,
    intensity: Any,
    trace_group: TraceGroup | None = None,
    product_selected_hypothesis: PeakHypothesis | None = None,
) -> None:
    if not config.emit_peak_candidates or rows is None:
        return
    selected_hypothesis = product_selected_hypothesis
    if selected_hypothesis is None:
        selected_hypotheses = tuple(
            hypothesis for hypothesis in hypotheses if hypothesis.audit.selected
        )
        if len(selected_hypotheses) != 1:
            return
        selected_hypothesis = selected_hypotheses[0]
    trace_rt = trace_group.primary_trace.rt if trace_group is not None else rt
    trace_intensity = (
        trace_group.primary_trace.intensity if trace_group is not None else intensity
    )
    context_start, context_end = _trace_context_bounds(trace_rt)
    rows.append(
        selected_envelope_diagnostic_row_from_hypothesis(
            sample_name=sample_name,
            hypothesis=selected_hypothesis,
            rt_values=trace_rt,
            intensity_values=trace_intensity,
            quantitation_context_rt_start=context_start,
            quantitation_context_rt_end=context_end,
        )
    )


def _trace_context_bounds(rt: Any) -> tuple[float, float]:
    values = np.asarray(rt, dtype=float)
    if values.ndim != 1 or len(values) < 2:
        raise ValueError("selected-envelope diagnostics require at least 2 RT scans")
    return float(values[0]), float(values[-1])
