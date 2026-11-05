"""Assess one Targeted candidate population and complete its selected result."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, cast

import numpy as np

from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.configuration.targeted_strategy import (
    TARGETED_PRECURSOR_REFERENCE,
)
from xic_extractor.evidence.neutral_loss import (
    CandidateMS2Evidence,
    MS2ProductEvidence,
)
from xic_extractor.extraction.result_assembly import build_extraction_result
from xic_extractor.extraction.scoring_factory import (
    ScoringContextBuilder,
    selected_candidate,
)
from xic_extractor.extraction.targeted_peak_evidence import (
    build_targeted_peak_evidence,
    targeted_trigger_events,
)
from xic_extractor.extraction.targeted_peak_hypotheses import (
    build_targeted_peak_hypotheses as build_peak_hypotheses,
)
from xic_extractor.ms2_scan_models import Ms2Window
from xic_extractor.peak_detection.baseline import BaselineMethod
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlCandidateInterval,
    CidNlEvaluationReceipt,
    CidNlEventQuery,
    CidNlMethodRelations,
    evaluate_cid_nl_method_relations,
)
from xic_extractor.peak_detection.cid_nl_peak_support import DdaCidNlPeakSupportPolicy
from xic_extractor.peak_detection.hypotheses import (
    CandidateMS2EvidenceMap,
    PeakHypothesis,
)
from xic_extractor.peak_detection.models import (
    PeakCandidate,
    PeakCandidatesResult,
    PeakDetectionResult,
)
from xic_extractor.peak_detection.observations import (
    physical_peak_observation_integration,
)
from xic_extractor.peak_detection.peak_selection import select_peak_and_area
from xic_extractor.peak_detection.physical_peak_candidate_adapter import (
    answer_selected_physical_candidate,
    physical_candidates_result,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    qualified_physical_segments,
)
from xic_extractor.peak_detection.physical_peak_models import (
    PHYSICAL_PEAK_ENVELOPE_POLICY,
    PHYSICAL_PEAK_REFERENCE_POLICY,
    PhysicalPeakEvaluation,
    PhysicalSegment,
    PhysicalSegmentUniverse,
    PreparedSignalResult,
)
from xic_extractor.peak_detection.selection_decision import (
    PeakHypothesisSelectionDecision,
    selection_decision_from_hypothesis,
)
from xic_extractor.peak_detection.targeted_context import (
    TargetedPopulationContext,
    describe_targeted_region,
    observed_component_apex_rts,
)
from xic_extractor.peak_detection.traces import TraceGroup
from xic_extractor.precursor_evidence import PrecursorMassScope

if TYPE_CHECKING:
    from xic_extractor.evidence.targeted_identification import MethodCandidateEvidence
    from xic_extractor.extraction.result_models import ExtractionResult

_PRECURSOR_SCOPE_ROUNDOFF_DA = 1e-6


@dataclass(frozen=True, slots=True)
class TargetedCandidateAssessment:
    """Keep physical candidates, method evidence and selected membership aligned.

    Acquisition supplies a retained trace and MS2 window. This owner decides the
    exact method population and carries that same ordering through scoring and
    the completed result; consumers never reconstruct a selected method index.
    """

    target: Target
    config: ExtractionConfig
    trace_group: TraceGroup
    prepared_result: PreparedSignalResult
    universe: PhysicalSegmentUniverse
    candidates: PeakCandidatesResult
    selection_window: tuple[float, float]
    preferred_rt: float | None
    strict_preferred_rt: bool
    candidate_evaluations: Mapping[PeakCandidate, PhysicalPeakEvaluation]
    candidate_ms2_evidence: Mapping[PeakCandidate, CandidateMS2Evidence]
    method_products: tuple[MS2ProductEvidence, ...]
    method_receipt: CidNlEvaluationReceipt | None
    method_relations: CidNlMethodRelations | None
    method_trigger_events: tuple[tuple[int, float], ...] = ()
    method_assessments: Mapping[PeakCandidate, MethodCandidateEvidence] = field(
        default_factory=dict
    )
    paired_istd_fwhm: float | None = None
    istd_confidence_note: str | None = None
    paired_istd_anchor_rt: float | None = None

    def method_admits(self, candidate: PeakCandidate) -> bool:
        return (
            self.target.identification_method is None
            or self.method_assessments[candidate].accepted
        )

    def population_context(self) -> TargetedPopulationContext:
        prepared = self.prepared_result.prepared
        set_members = (
            self.method_relations.set_related_candidate_indexes
            if self.method_relations is not None
            else frozenset()
        )
        components = (
            observed_component_apex_rts(
                prepared,
                qualified_physical_segments(
                    prepared,
                    self.universe.segments,
                    selection_policy_version=PHYSICAL_PEAK_ENVELOPE_POLICY,
                ),
            )
            if prepared is not None
            else ()
        )
        return TargetedPopulationContext(
            sample_name=self.trace_group.sample_name,
            target_label=self.target.label,
            mz=self.target.mz,
            ppm_tol=self.target.ppm_tol,
            # Batch displacement is measured from method RTs, not from the
            # sample-drift-adjusted selection prior. Paired transfer remains
            # governed by its existing sample-local anchor.
            reference_rt_min=self.preferred_rt
            if self.paired_istd_anchor_rt is not None and self.preferred_rt is not None
            else (
                self.target.expected_rt_min
                if self.target.expected_rt_min is not None
                else (self.target.rt_min + self.target.rt_max) / 2
            ),
            regions=tuple(
                describe_targeted_region(
                    prepared,
                    candidate.physical_segment,
                    measurement_available=evaluation.state == "available"
                    and self.method_admits(candidate),
                    component_apex_rts=components,
                    chemical_support=(
                        (
                            bool(
                                self.target.identification_method
                                and self.target.identification_method.conditions
                            )
                            and self.method_admits(candidate)
                        )
                        or self.method_relations is not None
                        and index in self.method_relations.supported_candidate_indexes
                    ),
                    chemical_set_membership=index in set_members,
                )
                for index, (candidate, evaluation) in enumerate(
                    self.candidate_evaluations.items()
                )
                if prepared is not None and candidate.physical_segment is not None
            ),
            reference_istd_rt_min=self.paired_istd_anchor_rt,
            sample_reference_rt_min=self.preferred_rt,
        )

    def score_selected_segment(
        self,
        *,
        selected_segment: PhysicalSegment | None,
        competing_region_count: int,
        scoring_context_builder: ScoringContextBuilder | None,
        istd_confidence_note: str | None,
        paired_istd_anchor_rt: float | None,
    ) -> PeakDetectionResult:
        selected = next(
            (
                candidate
                for candidate, evaluation in self.candidate_evaluations.items()
                if evaluation.state == "available"
                and self.method_admits(candidate)
                and selected_segment is not None
                and candidate.physical_segment == selected_segment
            ),
            None,
        )
        if selected is None:
            return PeakDetectionResult(
                status="PEAK_NOT_FOUND",
                peak=None,
                n_points=self.candidates.n_points,
                max_smoothed=self.candidates.max_smoothed,
                n_prominent_peaks=0,
            )
        # Score and retain the complete measurable population. Quality describes
        # the selected measurement; it does not get another vote on its identity.
        measurable = tuple(
            candidate
            for candidate, evaluation in self.candidate_evaluations.items()
            if evaluation.state == "available" and self.method_admits(candidate)
        )
        candidate_population = replace(
            self.candidates,
            candidates=measurable,
            status="OK" if measurable else "PEAK_NOT_FOUND",
            n_prominent_peaks=competing_region_count,
        )

        def context_selector(scored, **_kwargs):
            return next(item for item in scored if item.candidate == selected)

        return select_peak_and_area(
            candidate_population,
            self.config,
            preferred_rt=(
                selected.selection_apex_rt
                if scoring_context_builder is None
                else self.preferred_rt
            ),
            strict_preferred_rt=(
                True if scoring_context_builder is None else self.strict_preferred_rt
            ),
            scoring_context_builder=scoring_context_builder,
            istd_confidence_note=istd_confidence_note,
            evidence_role="ISTD" if self.target.is_istd else "Analyte",
            istd_pair=self.target.istd_pair,
            paired_istd_anchor_rt=paired_istd_anchor_rt,
            evidence_selector=context_selector,
        )

    def complete_result(
        self,
        peak_result: PeakDetectionResult,
        *,
        scoring_context_builder: ScoringContextBuilder | None,
    ) -> ExtractionResult:
        selection = select_targeted_peak(
            config=self.config,
            target=self.target,
            peak_result=peak_result,
            trace_group=self.trace_group,
            candidate_ms2_evidence=self.candidate_ms2_evidence,
            prepared_result=self.prepared_result,
            universe=self.universe,
            selection_rt_min=self.selection_window[0],
            selection_rt_max=self.selection_window[1],
            candidate_evaluations=self.candidate_evaluations,
        )
        candidate = selected_candidate(selection.peak_result)
        result = build_extraction_result(
            peak_result=selection.peak_result,
            nl_result=None,
            candidate_ms2_evidence=selection.candidate_ms2_evidence,
            target=self.target,
            candidate=candidate,
            scoring_context_builder=scoring_context_builder,
            selected_hypothesis=selection.selected_hypothesis,
            selection_decision=selection.selection_decision,
            acquisition_trace=selection.trace_group.primary_trace,
            physical_evaluation=selection.physical_evaluation,
            candidate_physical_evaluations=tuple(self.candidate_evaluations.values()),
            cid_nl_products=self.method_products,
            cid_nl_evaluation_receipt=self.method_receipt,
            cid_nl_relations=self.method_relations,
            cid_nl_candidate_evidence=tuple(self.candidate_ms2_evidence.values()),
            selected_cid_nl_candidate_index=(
                tuple(self.candidate_evaluations).index(candidate)
                if candidate is not None and self.method_relations is not None
                else None
            ),
            sample_name=self.trace_group.sample_name,
            method_candidate_evidence=tuple(self.method_assessments.values()),
            selected_method_candidate_index=(
                tuple(self.candidate_evaluations).index(candidate)
                if candidate is not None
                and self.target.identification_method is not None
                else None
            ),
        )
        return replace(result, targeted_candidate_assessment=self)


def targeted_physical_peak_policy(target: Target, reference_rt: float | None) -> str:
    # Targeted MS1 and CID-NL share measurable-body admission. Method support and
    # reference identity are assessed after the full region population exists.
    if target.is_istd and reference_rt is not None:
        return PHYSICAL_PEAK_REFERENCE_POLICY
    return PHYSICAL_PEAK_ENVELOPE_POLICY


def assess_targeted_candidate_population(
    *,
    target: Target,
    config: ExtractionConfig,
    trace_group: TraceGroup,
    prepared_result: PreparedSignalResult,
    universe: PhysicalSegmentUniverse,
    selection_window: tuple[float, float],
    preferred_rt: float | None,
    strict_preferred_rt: bool,
    method_window: Ms2Window | None,
    method_seeds: tuple | None = None,
    retained_method_products: tuple[MS2ProductEvidence, ...] | None = None,
    retained_method_receipt: CidNlEvaluationReceipt | None = None,
    retained_trigger_events: tuple[tuple[int, float], ...] | None = None,
) -> TargetedCandidateAssessment:
    trace = trace_group.primary_trace
    physical_policy = targeted_physical_peak_policy(target, preferred_rt)
    candidates = physical_candidates_result(
        prepared_result, universe, selection_policy_version=physical_policy
    )
    evaluations = {
        candidate: answer_selected_physical_candidate(
            prepared_result,
            universe,
            candidate,
            acquisition_rt_window=(trace.rt_min, trace.rt_max),
            selection_rt_window=selection_window,
            selection_policy_version=physical_policy,
        )
        for candidate in candidates.candidates
    }
    receipt = None
    relations = None
    products: tuple[MS2ProductEvidence, ...] = ()
    candidate_ms2 = {}
    trigger_events: tuple[tuple[int, float], ...] = ()
    if target.neutral_loss_da is not None:
        if retained_method_products is None:
            if method_window is None:
                raise ValueError(
                    "CID-NL candidate assessment requires its acquired window"
                )
            products = _discovery_method_products(method_window, target, method_seeds)
        else:
            products = retained_method_products
        policy = DdaCidNlPeakSupportPolicy(
            precursor_tolerance_da=max(target.mz * target.ppm_tol / 1_000_000, 1e-9),
            neutral_loss_tolerance_ppm=target.nl_ppm_max or 20.0,
            precursor_reference_requirement=TARGETED_PRECURSOR_REFERENCE,
        )
        # The broad acquisition probe may include neighbouring precursor ions;
        # only the declared exact-mass method population enters this assessment.
        products = tuple(
            product
            for product in products
            if abs(product.precursor_mz - target.mz) <= policy.precursor_tolerance_da
        )
        if retained_method_receipt is not None:
            receipt = retained_method_receipt
        else:
            assert method_window is not None
            receipt = CidNlEvaluationReceipt(
                query_rt_min=min(method_window.rt_min, selection_window[0]),
                query_rt_max=max(method_window.rt_max, selection_window[1]),
                support_policy=policy,
                acquisition_supported=True,
                search_completed=True,
                reason_code=None,
            )
        trigger_events = (
            retained_trigger_events
            if retained_trigger_events is not None
            else targeted_trigger_events(
                method_window,
                target=target,
                precursor_tolerance_da=policy.precursor_tolerance_da,
            )
        )
        relations = evaluate_cid_nl_method_relations(
            candidate_intervals=tuple(
                CidNlCandidateInterval(
                    target.mz, candidate.peak.peak_start, candidate.peak.peak_end
                )
                for candidate in candidates.candidates
            ),
            event_queries=tuple(
                CidNlEventQuery(
                    product.assess_precursor_loss(
                        target.neutral_loss_da, policy.neutral_loss_tolerance_ppm
                    ),
                    product.scan_rt,
                    product.scan_rt
                    if product.ms1_precursor_observation is None
                    else product.ms1_precursor_observation.rt_min,
                )
                for product in products
            ),
            policy=policy,
        )
        answers = build_targeted_peak_evidence(
            target=target,
            window=method_window,
            trigger_events=trigger_events,
            products=products,
            relations=relations,
            candidate_apex_rts=tuple(
                c.selection_apex_rt for c in candidates.candidates
            ),
        )
        candidate_ms2 = dict(zip(candidates.candidates, answers, strict=True))
    return TargetedCandidateAssessment(
        target=target,
        config=config,
        trace_group=trace_group,
        prepared_result=prepared_result,
        universe=universe,
        candidates=candidates,
        selection_window=selection_window,
        preferred_rt=preferred_rt,
        strict_preferred_rt=strict_preferred_rt or target.neutral_loss_da is None,
        candidate_evaluations=evaluations,
        candidate_ms2_evidence=candidate_ms2,
        method_products=products,
        method_receipt=receipt,
        method_relations=relations,
        method_trigger_events=trigger_events,
    )


@dataclass(frozen=True)
class TargetedPeakSelection:
    peak_result: PeakDetectionResult
    physical_evaluation: PhysicalPeakEvaluation
    candidate_ms2_evidence: CandidateMS2Evidence | None
    selected_hypothesis: PeakHypothesis | None
    selection_decision: PeakHypothesisSelectionDecision | None
    trace_group: TraceGroup


def build_production_peak_hypotheses(
    *,
    config: ExtractionConfig,
    sample_name: str,
    target: Target,
    peak_result: PeakDetectionResult,
    candidate_ms2_evidence: CandidateMS2EvidenceMap | None = None,
    rt: object | None = None,
    intensity: object | None = None,
    trace_group: TraceGroup | None = None,
) -> tuple[PeakHypothesis, ...]:
    hypotheses = build_peak_hypotheses(
        sample_name=sample_name,
        target_label=target.label,
        role="ISTD" if target.is_istd else "Analyte",
        istd_pair=target.istd_pair,
        resolver_mode=config.resolver_mode,
        peak_result=peak_result,
        candidate_ms2_evidence=candidate_ms2_evidence,
        rt=rt,
        intensity=intensity,
        trace_group=trace_group,
        baseline_integration_method=cast(
            BaselineMethod, config.baseline_integration_method
        ),
        count_no_ms2_as_detected=config.count_no_ms2_as_detected,
        ms1_morphology_smoothing_window_points=(
            config.ms1_morphology_smoothing_window_points
        ),
    )
    return _with_final_selected_result_evidence(hypotheses, peak_result)


def selected_peak_hypothesis(
    hypotheses: tuple[PeakHypothesis, ...],
) -> PeakHypothesis | None:
    for hypothesis in hypotheses:
        if hypothesis.audit.selected:
            return hypothesis
    return None


def select_targeted_peak(
    *,
    config: ExtractionConfig,
    target: Target,
    peak_result: PeakDetectionResult,
    trace_group: TraceGroup,
    candidate_ms2_evidence: CandidateMS2EvidenceMap | None,
    prepared_result: PreparedSignalResult,
    universe: PhysicalSegmentUniverse,
    selection_rt_min: float,
    selection_rt_max: float,
    candidate_evaluations: Mapping[PeakCandidate, PhysicalPeakEvaluation] | None = None,
) -> TargetedPeakSelection:
    """Return selection facts synced with physical availability and integration."""
    trace = trace_group.primary_trace
    hypotheses = build_production_peak_hypotheses(
        config=config,
        sample_name=trace_group.sample_name,
        target=target,
        peak_result=peak_result,
        candidate_ms2_evidence=candidate_ms2_evidence,
        rt=np.asarray(trace.rt, dtype=float),
        intensity=np.asarray(trace.intensity, dtype=float),
        trace_group=trace_group,
    )
    selected_hypothesis = selected_peak_hypothesis(hypotheses)
    candidate = selected_candidate(peak_result)
    selected_ms2_evidence = (
        candidate_ms2_evidence.get(candidate)
        if candidate is not None and candidate_ms2_evidence is not None
        else None
    )
    physical_evaluation = answer_selected_physical_candidate(
        prepared_result,
        universe,
        candidate,
        acquisition_rt_window=(trace.rt_min, trace.rt_max),
        selection_rt_window=(selection_rt_min, selection_rt_max),
        candidate_evaluations=candidate_evaluations,
    )
    if physical_evaluation.state != "available":
        # The final physical owner withdraws the ranked value and selection authority.
        peak_result = replace(
            peak_result,
            status=(
                "NO_SIGNAL"
                if physical_evaluation.reason == "no_signal_above_baseline"
                else "PEAK_NOT_FOUND"
            ),
            peak=None,
            candidates=(),
            n_prominent_peaks=0,
            confidence=None,
            reason=physical_evaluation.reason,
            severities=(),
            score_breakdown=(),
            candidate_scores=(),
        )
        selected_ms2_evidence = None
        selected_hypothesis = None
    elif (
        selected_hypothesis is not None and physical_evaluation.integration is not None
    ):
        selected_hypothesis = replace(
            selected_hypothesis,
            integration=physical_peak_observation_integration(physical_evaluation),
        )
    return TargetedPeakSelection(
        peak_result=peak_result,
        physical_evaluation=physical_evaluation,
        candidate_ms2_evidence=selected_ms2_evidence,
        selected_hypothesis=selected_hypothesis,
        selection_decision=(
            selection_decision_from_hypothesis(
                selected_hypothesis,
                peak_result=peak_result,
            )
            if selected_hypothesis is not None
            else None
        ),
        trace_group=trace_group,
    )


def _with_final_selected_result_evidence(
    hypotheses: tuple[PeakHypothesis, ...],
    peak_result: PeakDetectionResult,
) -> tuple[PeakHypothesis, ...]:
    if peak_result.confidence is None and not peak_result.reason:
        return hypotheses
    updated: list[PeakHypothesis] = []
    for hypothesis in hypotheses:
        if not hypothesis.audit.selected:
            updated.append(hypothesis)
            continue
        updated.append(
            replace(
                hypothesis,
                evidence=replace(
                    hypothesis.evidence,
                    confidence=peak_result.confidence or hypothesis.evidence.confidence,
                    reason=peak_result.reason or hypothesis.evidence.reason,
                ),
            )
        )
    return tuple(updated)


def targeted_ms2_precursor_scope(targets: list[Target]) -> PrecursorMassScope | None:
    """Admit only MS2 events that can carry evidence for a declared CID-NL target.

    Method products and triggers keep a seed or recorded precursor only within
    the target's precursor tolerance; the margin absorbs float roundoff alone.
    """
    from xic_extractor.configuration.targeted_identification import target_cid_nl_tags

    nl_targets = [target for target in targets if target_cid_nl_tags(target)]
    if not nl_targets:
        return None
    return PrecursorMassScope.around(
        tuple(
            (
                target.mz,
                max(target.mz * target.ppm_tol / 1_000_000, 1e-9)
                + _PRECURSOR_SCOPE_ROUNDOFF_DA,
            )
            for target in nl_targets
        ),
        maximum_inference_radius_da=max(
            discovery_settings_for_tag(tag).ms2_precursor_tol_da
            for target in nl_targets
            for tag in target_cid_nl_tags(target)
        ),
    )


def prepare_targeted_method_seeds(
    window: Ms2Window, targets: list[Target]
) -> dict[str, tuple]:
    """Parse each chemistry once for the sample, independent of target count."""
    from pathlib import Path

    from xic_extractor.configuration.targeted_identification import target_cid_nl_tags
    from xic_extractor.discovery.ms2_seeds import discovery_seeds_from_scan

    prepared = {}
    for tag in dict.fromkeys(
        tag for target in targets for tag in target_cid_nl_tags(target)
    ):
        settings = discovery_settings_for_tag(tag)
        prepared[tag] = tuple(
            seed
            for event in window.events
            if event.parse_error is None and event.scan is not None
            for seed in discovery_seeds_from_scan(
                event.scan, raw_file=Path(window.sample_key), settings=settings
            )
        )
    return prepared


def _discovery_method_products(
    window: Ms2Window, target: Target, seeds: tuple | None
) -> tuple[MS2ProductEvidence, ...]:
    """Adapt shared Discovery events, preserving their recorded/inferred basis."""
    from xic_extractor.discovery.strict_seed_receipts import (
        resolve_discovery_scan_events,
    )

    if seeds is None:
        seeds = prepare_targeted_method_seeds(window, [target])[target.cid_nl_tag]
    base_by_scan = {
        event.scan_number: event.scan.base_peak
        for event in window.events
        if event.scan is not None
    }
    tolerance_da = target.mz * target.ppm_tol / 1e6
    # Resolve only within this target's mass question. A scan can carry distinct
    # valid recorded references for different targets of the same chemistry.
    seeds = resolve_discovery_scan_events(
        tuple(
            seed for seed in seeds if abs(seed.precursor_mz - target.mz) <= tolerance_da
        ),
        settings=discovery_settings_for_tag(target.cid_nl_tag),
        precursor_reference_requirement=TARGETED_PRECURSOR_REFERENCE,
    )
    assert target.neutral_loss_da is not None
    expected_product = target.mz - target.neutral_loss_da
    inference_tolerance = discovery_settings_for_tag(
        target.cid_nl_tag
    ).ms2_precursor_tol_da
    return tuple(
        MS2ProductEvidence(
            scan_rt=seed.rt,
            precursor_mz=seed.precursor_mz,
            product_mz=seed.product_mz,
            product_intensity=seed.product_intensity,
            product_base_ratio=seed.product_intensity / base_by_scan[seed.scan_number],
            target_product_ppm=abs(seed.product_mz - expected_product)
            / expected_product
            * 1e6,
            observed_loss_da=seed.observed_neutral_loss_da,
            observed_loss_error_ppm=seed.observed_loss_error_ppm,
            scan_number=seed.scan_number,
            acquisition_source_id=seed.acquisition_source_id,
            precursor_mz_basis=seed.precursor_mz_basis,
            scan_precursor_mz=seed.scan_precursor_mz,
            precursor_metadata=seed.precursor_metadata,
            ms1_precursor_observation=seed.ms1_precursor_observation,
            precursor_inference_tolerance_da=(
                inference_tolerance
                if seed.precursor_mz_basis == "product_plus_neutral_loss"
                else None
            ),
        )
        for seed in seeds
    )


def discovery_settings_for_tag(tag: str):
    from dataclasses import asdict

    from xic_extractor.configuration.targeted_strategy import targeted_tag_profile
    from xic_extractor.discovery.models import DiscoverySettings

    profile = targeted_tag_profile(tag)
    values = asdict(profile)
    values["neutral_loss_profiles"] = profile.neutral_loss_profiles
    return DiscoverySettings(**values)
