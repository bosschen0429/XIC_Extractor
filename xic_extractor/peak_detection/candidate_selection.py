from __future__ import annotations

from xic_extractor.peak_detection.decision_policy import (
    DecisionRecord,
    DecisionTerm,
    decision_blockers,
    decision_gate_terms,
)
from xic_extractor.peak_detection.evidence_facts import (
    CandidateEvidenceFacts,
    decision_semantics_from_candidate_facts,
)
from xic_extractor.peak_detection.scoring_models import (
    ScoredCandidate,
)

_SELECTION_FAR_DISTANCE_MAX_MIN = 0.75
_LOW_SCAN_STRONGER_CANDIDATE_AREA_RATIO = 15.0
_LOW_SCAN_STRONGER_CANDIDATE_MAX_SELECTION_DISTANCE_MIN = 0.35
_LOW_SCAN_STRONGER_CANDIDATE_EXTENDED_DISTANCE_MIN = 2.5
_STRICT_ANCHOR_AREA_MIN_DISTANCE_ADVANTAGE_MIN = 0.25
_PAIRED_ISTD_STRONG_CHROM_SEGMENT_DELTA_MAX_MIN = 0.25
_TRACE_STRENGTH_RANK = {"strong": 0, "moderate": 1, "weak": 2, "none": 3, "": 3}


def select_candidate_by_evidence(
    scored: list[ScoredCandidate],
    *,
    selection_rt: float | None = None,
    strict_selection_rt: bool = False,
) -> ScoredCandidate:
    if not scored:
        raise ValueError("select_candidate_by_evidence requires at least one candidate")
    _require_typed_facts(scored)
    return min(
        scored,
        key=lambda item: _typed_selection_key(
            item,
            scored,
            selection_rt=selection_rt,
            strict_selection_rt=strict_selection_rt,
        ),
    )


def _require_typed_facts(scored: list[ScoredCandidate]) -> None:
    missing = [item for item in scored if item.evidence_facts is None]
    if missing:
        raise ValueError("typed candidate evidence facts are required for selection")


def _typed_selection_key(
    item: ScoredCandidate,
    scored: list[ScoredCandidate],
    *,
    selection_rt: float | None,
    strict_selection_rt: bool,
) -> tuple[float, ...]:
    record = candidate_selection_decision_record(
        item,
        scored,
        selection_rt=selection_rt,
        strict_selection_rt=strict_selection_rt,
    )
    return _candidate_selection_ordering_key(record)


def _candidate_selection_ordering_key(record: DecisionRecord) -> tuple[float, ...]:
    return tuple(value for _name, value in (*record.gate, *record.tie_break))


def candidate_selection_decision_record(
    item: ScoredCandidate,
    scored: list[ScoredCandidate],
    *,
    selection_rt: float | None = None,
    strict_selection_rt: bool = False,
) -> DecisionRecord:
    facts = _facts(item)
    semantics = decision_semantics_from_candidate_facts(
        facts,
        count_no_ms2_as_detected=False,
    )
    reference = selection_rt if selection_rt is not None else facts.rt.rt_prior_min
    distance = (
        abs(facts.rt.selected_apex_rt_min - reference)
        if reference is not None
        else float("inf")
    )
    quality_penalty = (
        facts.selection_quality_penalty
        if facts.selection_quality_penalty is not None
        else float(facts.quality_penalty)
    )
    gate_terms = decision_gate_terms(semantics)
    gate: tuple[DecisionTerm, ...]
    tie_break: tuple[DecisionTerm, ...]
    if strict_selection_rt and selection_rt is not None:
        gate = (
            (
                "anchor_selection_completeness_rank",
                float(_typed_anchor_rank(facts, selection_rt=selection_rt)),
            ),
            *gate_terms,
        )
        tie_break = (
            (
                "strict_anchor_area_demotion_rank",
                float(
                    _typed_strict_anchor_area_demotion_rank(
                        item,
                        scored,
                        selection_rt=selection_rt,
                    )
                ),
            ),
            ("selection_quality_penalty", quality_penalty),
            ("chemical_evidence_rank", float(_chemical_rank(facts))),
            ("trace_strength_rank", float(_trace_strength_rank(facts))),
            ("selection_rt_distance", distance),
            ("negative_abundance", -facts.abundance),
        )
    else:
        gate = gate_terms
        tie_break = (
            (
                "abundance_demotion_rank",
                float(
                    _typed_abundance_demotion_rank(
                        item,
                        scored,
                        selection_rt=selection_rt,
                    )
                ),
            ),
            ("chemical_evidence_rank", float(_chemical_rank(facts))),
            ("trace_strength_rank", float(_trace_strength_rank(facts))),
            ("selection_quality_penalty", quality_penalty),
            ("selection_rt_distance", distance),
            ("rt_prior_distance", _rt_prior_distance(facts)),
            ("negative_abundance", -facts.abundance),
        )
    if facts.rt.role == "ISTD" and reference is not None and not strict_selection_rt:
        # Known-standard localization precedes trace-strength tie-breaks. Two
        # peaks sharing the configured NL are not interchangeable identities.
        # Reuse the anchor's physical-completeness/proximity rule; this neither
        # crops acquisition nor promotes a nearby peak without method support.
        gate = (
            (
                "istd_reference_completeness_rank",
                float(_typed_anchor_rank(facts, selection_rt=reference)),
            ),
            *gate,
        )
    # Only positive, peak-owned method support precedes RT hints. An unobserved
    # DDA product is not negative identity evidence, and a scan-free unrelated
    # peak must not beat the reference peak simply by having no MS2 opportunity.
    gate = (("chemical_evidence_rank", float(_chemical_rank(facts))), *gate)
    tie_break = tuple(term for term in tie_break if term[0] != "chemical_evidence_rank")
    return DecisionRecord(
        workflow="targeted_candidate_selection",
        unit_id=facts.candidate_id,
        required_evidence=(
            "typed_candidate_evidence_facts",
            "selected_apex_rt",
            "trace_evidence",
            "chemical_evidence",
            "rt_evidence",
            "boundary_evidence",
        ),
        decision_class=semantics.decision_class,
        blockers=decision_blockers(semantics),
        support=semantics.support_reasons,
        gate=gate,
        tie_break=tie_break,
        projection_authority="select_candidate_by_evidence",
    )


def _facts(item: ScoredCandidate) -> CandidateEvidenceFacts:
    facts = item.evidence_facts
    if facts is None:
        raise ValueError("typed candidate evidence facts are required for selection")
    return facts


def _typed_anchor_rank(facts: CandidateEvidenceFacts, *, selection_rt: float) -> int:
    if _strong_paired_istd_chrom_segment_anchor(facts):
        return 0
    complete_enough = (
        not facts.trace.hard_quality_flags
        and "low_scan_support" not in set(facts.trace.quality_flags)
        and facts.chemical.nl_match is not False
    )
    if complete_enough and _facts_interval_contains_rt(facts, selection_rt):
        return 0
    return 1


def _strong_paired_istd_chrom_segment_anchor(facts: CandidateEvidenceFacts) -> bool:
    if facts.rt.role != "Analyte":
        return False
    if not facts.boundary.chrom_peak_segment_present:
        return False
    if facts.rt.paired_istd_status != "close":
        return False
    if facts.rt.paired_istd_delta_min is None:
        return False
    if facts.rt.paired_istd_delta_min > _PAIRED_ISTD_STRONG_CHROM_SEGMENT_DELTA_MAX_MIN:
        return False
    if facts.trace.hard_quality_flags:
        return False
    if "low_scan_support" in set(facts.trace.quality_flags):
        return False
    if facts.trace.local_sn_quality != "strong":
        return False
    if (
        facts.trace.symmetry_quality == "poor"
        or facts.trace.width_quality == "poor"
        or facts.trace.noise_shape_quality == "poor"
    ):
        return False
    return True


def _typed_abundance_demotion_rank(
    item: ScoredCandidate,
    scored: list[ScoredCandidate],
    *,
    selection_rt: float | None,
) -> int:
    facts = _facts(item)
    if facts.abundance <= 0:
        return 1
    reference = selection_rt if selection_rt is not None else facts.rt.rt_prior_min
    for other in scored:
        if other is item:
            continue
        other_facts = _facts(other)
        stronger_area = facts.abundance * _LOW_SCAN_STRONGER_CANDIDATE_AREA_RATIO
        if other_facts.abundance < stronger_area:
            continue
        if reference is not None:
            other_distance = abs(other_facts.rt.selected_apex_rt_min - reference)
            if other_distance > _LOW_SCAN_STRONGER_CANDIDATE_EXTENDED_DISTANCE_MIN:
                continue
        if _chemical_rank(other_facts) <= _chemical_rank(facts):
            return 1
    return 0


def _typed_strict_anchor_area_demotion_rank(
    item: ScoredCandidate,
    scored: list[ScoredCandidate],
    *,
    selection_rt: float,
) -> int:
    facts = _facts(item)
    if facts.abundance <= 0:
        return 1
    distance = abs(facts.rt.selected_apex_rt_min - selection_rt)
    if distance <= _LOW_SCAN_STRONGER_CANDIDATE_MAX_SELECTION_DISTANCE_MIN:
        return 0
    stronger_area = facts.abundance * _LOW_SCAN_STRONGER_CANDIDATE_AREA_RATIO
    for other in scored:
        if other is item:
            continue
        other_facts = _facts(other)
        if other_facts.abundance < stronger_area:
            continue
        other_distance = abs(other_facts.rt.selected_apex_rt_min - selection_rt)
        if other_distance > _LOW_SCAN_STRONGER_CANDIDATE_MAX_SELECTION_DISTANCE_MIN:
            continue
        if distance - other_distance < _STRICT_ANCHOR_AREA_MIN_DISTANCE_ADVANTAGE_MIN:
            continue
        if _chemical_rank(other_facts) > _chemical_rank(facts):
            continue
        if other_facts.trace.hard_quality_flags:
            continue
        if "low_scan_support" in set(other_facts.trace.quality_flags):
            continue
        return 1
    return 0


def _chemical_rank(facts: CandidateEvidenceFacts) -> int:
    if facts.chemical.neutral_loss_required and facts.chemical.nl_match:
        return 0
    return 1


def _trace_strength_rank(facts: CandidateEvidenceFacts) -> int:
    return _TRACE_STRENGTH_RANK.get(facts.chemical.ms2_trace_strength, 3)


def _rt_prior_distance(facts: CandidateEvidenceFacts) -> float:
    if facts.rt.rt_prior_delta_min is None:
        return float("inf")
    return facts.rt.rt_prior_delta_min


def _facts_interval_contains_rt(
    facts: CandidateEvidenceFacts,
    selection_rt: float,
) -> bool:
    start = facts.rt.selected_apex_rt_min
    return abs(start - selection_rt) <= _SELECTION_FAR_DISTANCE_MAX_MIN
