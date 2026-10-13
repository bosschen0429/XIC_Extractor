"""Project the shared CID-NL relation into one target's physical peak evidence.

Discovery supplies measured products; the CID-NL owner attributes their support
to the complete peak population. Ranking, qualification and reporting consume
these completed answers, never a second broad-precursor neutral-loss search.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from xic_extractor.configuration.models import Target
from xic_extractor.evidence.neutral_loss import CandidateMS2Evidence, MS2ProductEvidence
from xic_extractor.ms2_scan_models import Ms2Window
from xic_extractor.ms2_trace_evidence import MS2TracePoint, summarize_ms2_trace
from xic_extractor.peak_detection.cid_nl_method_assessment import CidNlMethodRelations
from xic_extractor.peak_detection.physical_peak_models import PhysicalPeakEvaluation
from xic_extractor.precursor_evidence import recorded_precursor_references

if TYPE_CHECKING:
    from xic_extractor.extraction.result_models import ExtractionResult


def build_targeted_peak_evidence(
    *,
    target: Target,
    window: Ms2Window,
    products: tuple[MS2ProductEvidence, ...],
    relations: CidNlMethodRelations,
    candidate_apex_rts: tuple[float, ...],
) -> tuple[CandidateMS2Evidence, ...]:
    """Keep acquisition opportunity separate from positive, peak-owned support.

    A recorded reference at the target mass establishes a relevant MS2 query,
    not a successful fragmentation or chemical absence. Inferred/coisolated
    products remain inspectable but cannot become this target's own CID signal.
    """
    triggers = tuple(
        (event.scan_number, event.scan.rt)
        for event in window.events
        if event.scan is not None
        and event.parse_error is None
        and any(
            abs(mz - target.mz) <= relations.policy.precursor_tolerance_da
            for mz, _basis in recorded_precursor_references(event.scan, target.ppm_tol)
        )
    )
    answers = []
    for index, apex_rt in enumerate(candidate_apex_rts):
        interval = relations.candidate_intervals[index]
        trigger_count = len(
            {
                scan
                for scan, rt in triggers
                if interval.rt_left_min <= rt <= interval.rt_right_min
            }
        )
        supported = tuple(
            products[event]
            for event, peak in relations.individual_relations
            if peak == index
        )
        best = min(
            supported,
            key=lambda p: (p.observed_loss_error_ppm or 0.0, -p.product_intensity),
            default=None,
        )
        related = tuple(
            event
            for event, product in enumerate(products)
            if interval.rt_left_min <= product.scan_rt <= interval.rt_right_min
        )
        reason = ""
        if not supported:
            if any(event in relations.set_relation_event_indexes for event in related):
                reason = "ambiguous_peak_assignment"
            elif related:
                assessment = relations.event_queries[related[0]].precursor
                reason = (
                    assessment.status
                    if not assessment.supports_nl
                    else "unassigned_precursor"
                )
            elif trigger_count:
                reason = "no_target_product_support"
        trace = summarize_ms2_trace(
            tuple(
                MS2TracePoint(
                    p.scan_rt,
                    p.product_intensity,
                    p.product_base_ratio,
                    p.observed_loss_error_ppm or 0.0,
                )
                for p in supported
            ),
            candidate_apex_rt=apex_rt,
            trigger_scan_count=trigger_count,
        )
        answers.append(
            CandidateMS2Evidence(
                ms2_present=trigger_count > 0,
                nl_match=bool(supported),
                nl_status="OK"
                if supported
                else "NL_FAIL"
                if trigger_count
                else "NO_MS2",
                trigger_scan_count=trigger_count,
                strict_nl_scan_count=len(supported),
                best_loss_ppm=best.observed_loss_error_ppm if best else None,
                best_scan_rt=best.scan_rt if best else None,
                best_product_base_ratio=best.product_base_ratio if best else None,
                alignment_source="region" if trigger_count else "none",
                diagnostic_product_absence_reason=reason,
                trace=trace,
                ms1_peak_group_source="physical_segment",
                ms1_peak_group_rt_min=interval.rt_left_min,
                ms1_peak_group_rt_max=interval.rt_right_min,
                ms1_peak_group_trigger_scan_count=trigger_count,
                ms1_peak_group_strict_nl_scan_count=len(supported),
                ms1_peak_group_strict_nl_event_count=int(bool(supported)),
                best_product_evidence=best,
            )
        )
    return tuple(answers)


def reselect_targeted_peak_evidence(
    result: ExtractionResult, evaluation: PhysicalPeakEvaluation | None
) -> ExtractionResult:
    """Move the selected evidence with a finalized member of the retained universe.

    Backfill already returns one of these physical evaluations. It does not
    acquire new MS2 or establish a new evidence population at publication time.
    """
    index = next(
        (
            i
            for i, candidate in enumerate(result.candidate_physical_evaluations)
            if candidate is evaluation
        ),
        None,
    )
    return replace(
        result,
        selected_cid_nl_candidate_index=index,
        candidate_ms2_evidence=(
            result.cid_nl_candidate_evidence[index]
            if index is not None and result.cid_nl_candidate_evidence
            else None
        ),
    )
