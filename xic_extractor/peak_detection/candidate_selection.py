from __future__ import annotations

import math

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
    CONFIDENCE_RANK,
    Confidence,
    ScoredCandidate,
)

_SELECTION_QUALITY_DISTANCE_WEIGHT_MIN = 0.05
_SELECTION_DISTANCE_POINTS_PER_MIN = 60.0
_SELECTION_QUALITY_POINTS_PER_UNIT = 10.0
_SELECTION_FAR_DISTANCE_MAX_MIN = 0.75
_LOW_SCAN_DEMOTION_SCORE_PENALTY = 80.0
_LOW_SCAN_STRONGER_CANDIDATE_INTENSITY_RATIO = 2.0
_LOW_SCAN_STRONGER_CANDIDATE_AREA_RATIO = 15.0
_LOW_SCAN_MAX_CONFIDENCE_RANK_GAP = 1
_LOW_SCAN_CONFIDENCE_DEMOTION = 2
_LOW_SCAN_STRONGER_CANDIDATE_MAX_SELECTION_DISTANCE_MIN = 0.35
_LOW_SCAN_STRONGER_CANDIDATE_EXTENDED_DISTANCE_MIN = 2.5
_STRICT_ANCHOR_AREA_MIN_DISTANCE_ADVANTAGE_MIN = 0.25
_DOMINANT_STRICT_NL_AREA_RATIO = 100.0
_DOMINANT_STRICT_NL_MAX_SELECTION_DISTANCE_MIN = 3.0
_DOMINANT_STRICT_NL_DEMOTION_SCORE_PENALTY = 200.0
_PAIRED_ISTD_STRONG_CHROM_SEGMENT_DELTA_MAX_MIN = 0.25
_MS2_TRACE_SELECTION_POINTS = {
    "ms2_trace_strong": 10.0,
    "ms2_trace_moderate": 5.0,
    "ms2_trace_weak": -8.0,
}
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


def select_candidate_with_confidence(
    scored: list[ScoredCandidate],
    *,
    selection_rt: float | None = None,
    strict_selection_rt: bool = False,
) -> ScoredCandidate:
    if not scored:
        raise ValueError(
            "select_candidate_with_confidence requires at least one candidate"
        )

    low_scan_demotions = set()
    dominant_area_demotions = set()
    if not strict_selection_rt:
        low_scan_demotions = _low_scan_demotion_ids(
            scored,
            selection_rt=selection_rt,
        )
        dominant_area_demotions = _dominant_area_demotion_ids(
            scored,
            selection_rt=selection_rt,
        )
    selection_demotion_penalties = {
        candidate_id: _LOW_SCAN_DEMOTION_SCORE_PENALTY
        for candidate_id in low_scan_demotions
    }
    for candidate_id in dominant_area_demotions:
        selection_demotion_penalties[candidate_id] = max(
            selection_demotion_penalties.get(candidate_id, 0.0),
            _DOMINANT_STRICT_NL_DEMOTION_SCORE_PENALTY,
        )

    def key(
        scored_candidate: ScoredCandidate,
    ) -> tuple[float, ...]:
        candidate = scored_candidate.candidate
        selection_reference = selection_rt
        if selection_reference is None:
            selection_reference = scored_candidate.prior_rt
        distance = (
            abs(candidate.selection_apex_rt - selection_reference)
            if selection_reference is not None
            else float("inf")
        )
        confidence_rank = CONFIDENCE_RANK[scored_candidate.confidence]
        selection_demotion_penalty = selection_demotion_penalties.get(
            id(scored_candidate),
            0.0,
        )
        selection_demoted = selection_demotion_penalty > 0
        if selection_demoted:
            confidence_rank += _LOW_SCAN_CONFIDENCE_DEMOTION
        selection_quality_penalty = (
            scored_candidate.selection_quality_penalty
            if scored_candidate.selection_quality_penalty is not None
            else float(scored_candidate.quality_penalty)
        )
        if strict_selection_rt and selection_rt is not None:
            return (
                float(
                    _anchor_selection_completeness_rank(
                        scored_candidate,
                        selection_rt=selection_rt,
                    )
                ),
                distance,
                float(confidence_rank),
                selection_quality_penalty,
                -_ms2_trace_selection_tiebreak(scored_candidate),
                -candidate.selection_apex_intensity,
            )
        if selection_reference is not None:
            if scored_candidate.evidence_score is None:
                adjusted_distance = distance + (
                    selection_quality_penalty * _SELECTION_QUALITY_DISTANCE_WEIGHT_MIN
                )
                return (
                    float(confidence_rank),
                    adjusted_distance,
                    selection_quality_penalty,
                    distance,
                    -candidate.selection_apex_intensity,
                )
            effective_score = _effective_score(
                scored_candidate,
                distance,
                demotion_penalty=selection_demotion_penalty,
            )
            ms2_trace_tiebreak = _ms2_trace_selection_tiebreak(scored_candidate)
            if distance > _SELECTION_FAR_DISTANCE_MAX_MIN and not selection_demoted:
                if selection_demotion_penalties:
                    return (
                        1.0,
                        -effective_score,
                        distance,
                        selection_quality_penalty,
                        -ms2_trace_tiebreak,
                        -candidate.selection_apex_intensity,
                    )
                return (
                    1.0,
                    distance,
                    -effective_score,
                    selection_quality_penalty,
                    -ms2_trace_tiebreak,
                    -candidate.selection_apex_intensity,
                )
            if selection_demoted:
                return (
                    1.0,
                    -effective_score,
                    distance,
                    selection_quality_penalty,
                    -ms2_trace_tiebreak,
                    -candidate.selection_apex_intensity,
                )
            return (
                0.0,
                -effective_score,
                distance,
                selection_quality_penalty,
                -ms2_trace_tiebreak,
                -candidate.selection_apex_intensity,
            )
        return (
            float(confidence_rank),
            selection_quality_penalty,
            0.0,
            0.0,
            -candidate.selection_apex_intensity,
        )

    return min(scored, key=key)


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
        if facts.rt.role == "ISTD":
            # Within one eligibility class, direct chemical support precedes
            # the number of review notes, including rolling-RT disagreement.
            chemical_term = next(
                term for term in tie_break if term[0] == "chemical_evidence_rank"
            )
            gate = (
                gate_terms[0],
                (
                    "hard_quality_conflict_rank",
                    float(bool(facts.trace.hard_quality_flags)),
                ),
                chemical_term,
                gate_terms[1],
            )
            tie_break = tuple(term for term in tie_break if term != chemical_term)
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
    if not facts.chemical.neutral_loss_required:
        return 1
    if facts.chemical.ms2_present and facts.chemical.nl_match:
        return 0
    if facts.chemical.ms2_present and facts.chemical.nl_match is False:
        return 3
    return 2


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


def _low_scan_demotion_ids(
    scored: list[ScoredCandidate],
    *,
    selection_rt: float | None,
) -> set[int]:
    demotions: set[int] = set()
    for scored_candidate in scored:
        if not _has_candidate_flag(scored_candidate, "low_scan_support"):
            continue
        if _has_much_stronger_supported_alternative(
            scored_candidate,
            scored,
            selection_rt=selection_rt,
        ):
            demotions.add(id(scored_candidate))
    return demotions


def _dominant_area_demotion_ids(
    scored: list[ScoredCandidate],
    *,
    selection_rt: float | None,
) -> set[int]:
    demotions: set[int] = set()
    for scored_candidate in scored:
        if _has_dominant_strict_nl_alternative(
            scored_candidate,
            scored,
            selection_rt=selection_rt,
        ):
            demotions.add(id(scored_candidate))
    return demotions


def _has_much_stronger_supported_alternative(
    low_scan_candidate: ScoredCandidate,
    scored: list[ScoredCandidate],
    *,
    selection_rt: float | None,
) -> bool:
    chosen_rank = CONFIDENCE_RANK[low_scan_candidate.confidence]
    chosen_penalty = _selection_penalty_value(low_scan_candidate)
    chosen_intensity = float(low_scan_candidate.candidate.selection_apex_intensity)
    chosen_abundance = _candidate_abundance(low_scan_candidate)
    if chosen_intensity <= 0 and chosen_abundance <= 0:
        return False

    reference = selection_rt
    if reference is None:
        reference = low_scan_candidate.prior_rt
    for candidate in scored:
        if candidate is low_scan_candidate:
            continue
        if _has_candidate_flag(candidate, "low_scan_support"):
            continue
        if _selection_penalty_value(candidate) > chosen_penalty:
            continue
        candidate_distance = (
            abs(candidate.candidate.selection_apex_rt - reference)
            if reference is not None
            else 0.0
        )
        close_intensity_support = (
            reference is None
            or candidate_distance
            <= _LOW_SCAN_STRONGER_CANDIDATE_MAX_SELECTION_DISTANCE_MIN
        ) and float(candidate.candidate.selection_apex_intensity) >= (
            chosen_intensity * _LOW_SCAN_STRONGER_CANDIDATE_INTENSITY_RATIO
        )
        extended_area_support = (
            reference is None
            or candidate_distance <= _LOW_SCAN_STRONGER_CANDIDATE_EXTENDED_DISTANCE_MIN
        ) and _candidate_abundance(candidate) >= (
            chosen_abundance * _LOW_SCAN_STRONGER_CANDIDATE_AREA_RATIO
        )
        if not close_intensity_support and not extended_area_support:
            continue
        confidence_gap_too_large = CONFIDENCE_RANK[candidate.confidence] > (
            chosen_rank + _LOW_SCAN_MAX_CONFIDENCE_RANK_GAP
        )
        if confidence_gap_too_large and not (
            extended_area_support and _has_evidence_support(candidate, "strict_nl_ok")
        ):
            continue
        return True
    return False


def _has_dominant_strict_nl_alternative(
    scored_candidate: ScoredCandidate,
    scored: list[ScoredCandidate],
    *,
    selection_rt: float | None,
) -> bool:
    chosen_abundance = _candidate_abundance(scored_candidate)
    if chosen_abundance <= 0:
        return False

    reference = selection_rt
    if reference is None:
        reference = scored_candidate.prior_rt
    if reference is None:
        return False

    chosen_distance = abs(scored_candidate.candidate.selection_apex_rt - reference)
    if chosen_distance > _SELECTION_FAR_DISTANCE_MAX_MIN:
        return False

    chosen_penalty = _selection_penalty_value(scored_candidate)
    for candidate in scored:
        if candidate is scored_candidate:
            continue
        if candidate.confidence is Confidence.VERY_LOW:
            continue
        if _has_candidate_flag(candidate, "low_scan_support"):
            continue
        if _selection_penalty_value(candidate) > chosen_penalty:
            continue
        candidate_distance = abs(candidate.candidate.selection_apex_rt - reference)
        if candidate_distance > _DOMINANT_STRICT_NL_MAX_SELECTION_DISTANCE_MIN:
            continue
        if not _has_evidence_support(candidate, "strict_nl_ok"):
            continue
        if _candidate_abundance(candidate) < (
            chosen_abundance * _DOMINANT_STRICT_NL_AREA_RATIO
        ):
            continue
        return True
    return False


def _has_candidate_flag(scored_candidate: ScoredCandidate, flag: str) -> bool:
    flags = getattr(scored_candidate.candidate, "quality_flags", ())
    return flag in {str(candidate_flag) for candidate_flag in flags}


def _anchor_selection_completeness_rank(
    scored_candidate: ScoredCandidate,
    *,
    selection_rt: float,
) -> int:
    complete_enough = (
        scored_candidate.confidence is not Confidence.VERY_LOW
        and not any(
            _has_candidate_flag(scored_candidate, flag)
            for flag in ("low_scan_support", "too_short", "low_scan_count")
        )
    )
    if complete_enough and _candidate_contains_rt(scored_candidate, selection_rt):
        return 0
    return 1


def _candidate_contains_rt(
    scored_candidate: ScoredCandidate,
    selection_rt: float,
) -> bool:
    peak = getattr(scored_candidate.candidate, "peak", None)
    if peak is None:
        return False
    peak_start = getattr(peak, "peak_start", None)
    peak_end = getattr(peak, "peak_end", None)
    if peak_start is None or peak_end is None:
        return False
    try:
        start = float(peak_start)
        end = float(peak_end)
    except (TypeError, ValueError):
        return False
    return start <= selection_rt <= end


def _has_evidence_support(scored_candidate: ScoredCandidate, label: str) -> bool:
    evidence_score = scored_candidate.evidence_score
    if evidence_score is None:
        return False
    return label in {
        str(support_label) for support_label in evidence_score.support_labels
    }


def _selection_penalty_value(scored_candidate: ScoredCandidate) -> float:
    if scored_candidate.selection_quality_penalty is not None:
        return scored_candidate.selection_quality_penalty
    return float(scored_candidate.quality_penalty)


def _candidate_abundance(scored_candidate: ScoredCandidate) -> float:
    peak = getattr(scored_candidate.candidate, "peak", None)
    area = getattr(peak, "area", None)
    area_value = 0.0
    if area is not None:
        try:
            area_value = float(area)
        except (TypeError, ValueError):
            area_value = 0.0
    else:
        area_value = 0.0
    if _is_finite(area_value) and area_value > 0:
        return area_value

    try:
        intensity_value = float(scored_candidate.candidate.selection_apex_intensity)
    except (TypeError, ValueError):
        return 0.0
    if _is_finite(intensity_value) and intensity_value > 0:
        return intensity_value
    return 0.0


def _effective_score(
    scored_candidate: ScoredCandidate,
    distance: float,
    *,
    demotion_penalty: float = 0.0,
) -> float:
    raw_score = (
        _selection_raw_score(scored_candidate)
        if scored_candidate.evidence_score is not None
        else 50.0 - float(CONFIDENCE_RANK[scored_candidate.confidence]) * 20.0
    )
    selection_quality_penalty = _selection_penalty_value(scored_candidate)
    return (
        raw_score
        - (distance * _SELECTION_DISTANCE_POINTS_PER_MIN)
        - (selection_quality_penalty * _SELECTION_QUALITY_POINTS_PER_UNIT)
        - demotion_penalty
    )


def _selection_raw_score(scored_candidate: ScoredCandidate) -> float:
    evidence_score = scored_candidate.evidence_score
    if evidence_score is None:
        return 50.0 - float(CONFIDENCE_RANK[scored_candidate.confidence]) * 20.0

    return float(evidence_score.raw_score) - _ms2_trace_selection_tiebreak(
        scored_candidate
    )


def _ms2_trace_selection_tiebreak(scored_candidate: ScoredCandidate) -> float:
    evidence_score = scored_candidate.evidence_score
    if evidence_score is None:
        return 0.0

    points = 0.0
    for label in evidence_score.support_labels:
        points += _MS2_TRACE_SELECTION_POINTS.get(str(label), 0.0)
    for label in evidence_score.concern_labels:
        points += _MS2_TRACE_SELECTION_POINTS.get(str(label), 0.0)
    return points


def _is_finite(value: float) -> bool:
    return math.isfinite(float(value))
