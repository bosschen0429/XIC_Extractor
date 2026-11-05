from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

_SOFT_TRACE_QUALITY_FLAGS = {"low_trace_continuity", "poor_edge_recovery"}
_HARD_LOCAL_CONCERN_LABELS = {
    "hard_quality_flag",
    "low_scan_support",
    "rt_centrality_poor",
    "shape_poor",
}
_CHROM_PEAK_SEGMENT_SOURCE = "chrom_peak_segment"
_CWT_PROPOSAL_SOURCE = "centwave_cwt"

EvidenceSource = Literal[
    "targeted_peak",
    "discovery_candidate",
    "alignment_cell",
]
DecisionClass = Literal["accepted", "review", "not_counted", "excluded", "ambiguous"]


@dataclass(frozen=True)
class CommonEvidence:
    source: EvidenceSource
    provenance: str = ""
    ms1_peak_found: bool | None = None
    ms1_apex_rt_min: float | None = None
    ms1_area: float | None = None
    ms1_height: float | None = None
    ms1_peak_rt_start: float | None = None
    ms1_peak_rt_end: float | None = None
    rt_delta_min: float | None = None
    trace_quality: str = ""
    scan_support_score: float | None = None
    neutral_loss_tag: str = ""
    configured_neutral_loss_da: float | None = None
    observed_neutral_loss_da: float | None = None
    neutral_loss_error_ppm: float | None = None
    seed_event_count: int | None = None
    ms2_present: bool | None = None
    nl_match: bool | None = None
    ms2_trace_strength: str = ""
    evidence_score: int | None = None
    review_priority: str = ""
    reason: str = ""


@dataclass(frozen=True)
class EvidenceDecisionSemantics:
    decision_class: DecisionClass
    support_reasons: tuple[str, ...] = ()
    conflict_reasons: tuple[str, ...] = ()
    review_reasons: tuple[str, ...] = ()
    not_counted_reasons: tuple[str, ...] = ()
    exclusion_reasons: tuple[str, ...] = ()
    ambiguity_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvidenceSignalSet:
    support_labels: tuple[str, ...] = ()
    concern_labels: tuple[str, ...] = ()
    proposal_sources: tuple[str, ...] = ()
    quality_flags: tuple[str, ...] = ()
    ms2_present: bool | None = None
    nl_match: bool | None = None
    raw_score: float | None = None
    cap_labels: tuple[str, ...] = ()
    reason: str = ""
    rejection_reason: str = ""
    count_no_ms2_as_detected: bool = False


def classify_evidence_consistency(signals: EvidenceSignalSet) -> tuple[str, ...]:
    support = _label_set(signals.support_labels)
    concerns = _label_set(signals.concern_labels)
    sources = _label_set(signals.proposal_sources)
    quality_flags = _label_set(signals.quality_flags)

    labels: list[str] = []
    has_shape_context = (
        "shape_clean" in support
        or _CWT_PROPOSAL_SOURCE in sources
        or _CHROM_PEAK_SEGMENT_SOURCE in sources
        or "cwt_same_apex_support" in support
        or "chrom_peak_segment_context" in support
    )
    hard_quality_flags = quality_flags - _SOFT_TRACE_QUALITY_FLAGS
    trace_context_ok = "trace_clean" in support or not hard_quality_flags
    ms1_coherent = (
        "local_sn_strong" in support and trace_context_ok and has_shape_context
    )
    hard_local_conflict = bool(hard_quality_flags) or bool(
        concerns & _HARD_LOCAL_CONCERN_LABELS
    )
    if hard_local_conflict:
        labels.append("hard_local_quality_conflict")
    elif ms1_coherent:
        labels.append("ms1_coherent")

    missing_ms2 = signals.ms2_present is False or "no_ms2" in concerns
    if missing_ms2:
        labels.append("missing_ms2")

    nl_failed = signals.nl_match is False or "nl_fail" in concerns
    score_not_negative = signals.raw_score is None or signals.raw_score >= 0
    if nl_failed:
        if (
            ms1_coherent
            and not hard_local_conflict
            and not missing_ms2
            and score_not_negative
        ):
            labels.append("plausible_nl_dropout")
        else:
            labels.append("hard_nl_conflict")

    return tuple(labels)


def decision_semantics_from_signal_set(
    signals: EvidenceSignalSet,
) -> EvidenceDecisionSemantics:
    support = _label_set(signals.support_labels)
    concerns = _label_set(signals.concern_labels)
    sources = _label_set(signals.proposal_sources)
    quality_flags = _label_set(signals.quality_flags)
    caps = _label_set(signals.cap_labels)
    consistency = classify_evidence_consistency(signals)

    support_reasons = _decision_support_reasons(support, sources, consistency)
    conflict_reasons = _decision_conflict_reasons(
        concerns,
        caps,
        quality_flags,
        consistency,
    )
    review_reasons = _decision_review_reasons(concerns, caps, consistency)
    not_counted_reasons = (
        ("missing_ms2_policy_not_counted",)
        if "no_ms2_cap" in caps and not signals.count_no_ms2_as_detected
        else ()
    )
    exclusion_reasons = _decision_exclusion_reasons(concerns, caps)
    ambiguity_reasons = _decision_ambiguity_reasons(signals, concerns)

    if (
        "cwt_boundary_morphology_context" in support_reasons
        and len(support_reasons) == 1
    ):
        review_reasons = _append_unique(
            review_reasons,
            "cwt_requires_correlated_evidence",
        )
    if "chrom_peak_segment_context" in support_reasons and len(support_reasons) == 1:
        review_reasons = _append_unique(
            review_reasons,
            "chrom_peak_segment_requires_correlated_evidence",
        )

    if exclusion_reasons:
        decision_class: DecisionClass = "excluded"
    elif ambiguity_reasons:
        decision_class = "ambiguous"
    elif not_counted_reasons:
        decision_class = "not_counted"
    elif conflict_reasons or review_reasons:
        decision_class = "review"
    elif support_reasons:
        decision_class = "accepted"
    else:
        decision_class = "review"
        review_reasons = _append_unique(review_reasons, "insufficient_typed_evidence")

    return EvidenceDecisionSemantics(
        decision_class=decision_class,
        support_reasons=support_reasons,
        conflict_reasons=conflict_reasons,
        review_reasons=review_reasons,
        not_counted_reasons=not_counted_reasons,
        exclusion_reasons=exclusion_reasons,
        ambiguity_reasons=ambiguity_reasons,
    )


def _decision_support_reasons(
    support: frozenset[str],
    sources: frozenset[str],
    consistency: tuple[str, ...],
) -> tuple[str, ...]:
    reasons: list[str] = []
    if "ms1_coherent" in consistency:
        reasons.append("ms1_coherent")
    if "strict_nl_ok" in support or "nl_match" in support:
        reasons.append("candidate_aligned_ms2_nl")
    if "rt_prior_close" in support or "paired_istd_rt_close" in support:
        reasons.append("role_aware_rt_support")
    if "paired_area_ratio_plausible" in support:
        reasons.append("paired_area_ratio_support")
    if _CWT_PROPOSAL_SOURCE in sources or "cwt_same_apex_support" in support:
        reasons.append("cwt_boundary_morphology_context")
    if _CHROM_PEAK_SEGMENT_SOURCE in sources:
        reasons.append("chrom_peak_segment_context")
    return tuple(reasons)


def _decision_conflict_reasons(
    concerns: frozenset[str],
    caps: frozenset[str],
    quality_flags: frozenset[str],
    consistency: tuple[str, ...],
) -> tuple[str, ...]:
    reasons: list[str] = []
    if "hard_local_quality_conflict" in consistency:
        reasons.append("hard_local_quality_conflict")
    if "hard_nl_conflict" in consistency:
        reasons.append("candidate_aligned_ms2_nl_conflict")
    if caps & {"rt_window_cap"} or concerns & {
        "rt_prior_far",
        "rt_prior_borderline",
        "rt_centrality_poor",
        "rt_centrality_borderline",
    }:
        reasons.append("targeted_rt_conflict")
    if caps & {"anchor_mismatch_cap"} or "anchor_mismatch" in concerns:
        reasons.append("anchor_conflict")
    if caps & {"trace_quality_cap", "hard_quality_flag_cap"} or concerns & {
        "shape_poor",
        "shape_borderline",
        "noise_shape_poor",
        "noise_shape_borderline",
        "low_scan_support",
        "low_trace_continuity",
        "poor_edge_recovery",
        "hard_quality_flag",
    }:
        reasons.append("trace_morphology_conflict")
    if quality_flags - _SOFT_TRACE_QUALITY_FLAGS:
        reasons.append("hard_quality_flag_conflict")
    return tuple(dict.fromkeys(reasons))


def _decision_review_reasons(
    concerns: frozenset[str],
    caps: frozenset[str],
    consistency: tuple[str, ...],
) -> tuple[str, ...]:
    reasons: list[str] = []
    if "missing_ms2" in consistency or "no_ms2_cap" in caps or "no_ms2" in concerns:
        reasons.append("missing_ms2_not_observed")
    if "plausible_nl_dropout" in consistency:
        reasons.append("plausible_nl_dropout_review")
    if caps & {"rt_window_cap"} or concerns & {
        "rt_prior_far",
        "rt_prior_borderline",
        "rt_centrality_poor",
        "rt_centrality_borderline",
    }:
        reasons.append("targeted_rt_review")
    if caps & {"trace_quality_cap", "hard_quality_flag_cap"}:
        reasons.append("trace_morphology_review")
    if caps & {"anchor_mismatch_cap"} or "anchor_mismatch" in concerns:
        reasons.append("paired_istd_rt_review")
    return tuple(reasons)


def _decision_exclusion_reasons(
    concerns: frozenset[str],
    caps: frozenset[str],
) -> tuple[str, ...]:
    reasons: list[str] = []
    if concerns & {"wrong_identity", "physically_implausible"}:
        reasons.append("explicit_identity_exclusion")
    if "explicit_exclusion_cap" in caps:
        reasons.append("explicit_exclusion_cap")
    return tuple(reasons)


def _decision_ambiguity_reasons(
    signals: EvidenceSignalSet,
    concerns: frozenset[str],
) -> tuple[str, ...]:
    reasons: list[str] = []
    reason_text = " ".join((signals.reason, signals.rejection_reason)).lower()
    if "ambiguous" in reason_text or "unresolved_tie" in concerns:
        reasons.append("ambiguous_evidence")
    return tuple(reasons)


def _append_unique(values: tuple[str, ...], value: str) -> tuple[str, ...]:
    if value in values:
        return values
    return (*values, value)


def _label_set(values: tuple[str, ...]) -> frozenset[str]:
    return frozenset(_normalize_label(value) for value in values if value)


def _normalize_label(value: str) -> str:
    return value.strip().lower().replace(" ", "_").replace("-", "_").replace("/", "_")
