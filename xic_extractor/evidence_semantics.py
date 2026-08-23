from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from xic_extractor.neutral_loss import CandidateMS2Evidence
from xic_extractor.peak_detection.models import PeakCandidate, PeakCandidateScore

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
    source_id: str = ""
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
    confidence: str = ""
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
    compatibility_labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvidenceSignalSet:
    support_labels: tuple[str, ...] = ()
    concern_labels: tuple[str, ...] = ()
    proposal_sources: tuple[str, ...] = ()
    quality_flags: tuple[str, ...] = ()
    ms2_present: bool | None = None
    nl_match: bool | None = None
    raw_score: float | None = None
    confidence: str = ""
    cap_labels: tuple[str, ...] = ()
    reason: str = ""
    rejection_reason: str = ""
    count_no_ms2_as_detected: bool = False


def common_evidence_from_targeted_candidate(
    candidate: PeakCandidate,
    *,
    score: PeakCandidateScore | None = None,
    candidate_ms2_evidence: CandidateMS2Evidence | None = None,
    target_label: str = "",
) -> CommonEvidence:
    return common_evidence_from_peak_candidate(
        candidate,
        score=score,
        candidate_ms2_evidence=candidate_ms2_evidence,
        source="targeted_peak",
        source_id=target_label,
        provenance="selected_or_candidate",
    )


def common_evidence_from_peak_candidate(
    candidate: PeakCandidate,
    *,
    score: PeakCandidateScore | None = None,
    candidate_ms2_evidence: CandidateMS2Evidence | None = None,
    source: Literal["targeted_peak", "discovery_candidate"],
    source_id: str,
    provenance: str,
) -> CommonEvidence:
    """Project one captured peak without assigning workflow product authority."""

    return CommonEvidence(
        source=source,
        source_id=source_id,
        provenance=provenance,
        ms1_peak_found=True,
        ms1_apex_rt_min=candidate.selection_apex_rt,
        ms1_area=candidate.peak.area,
        ms1_height=candidate.raw_apex_intensity,
        ms1_peak_rt_start=candidate.peak.peak_start,
        ms1_peak_rt_end=candidate.peak.peak_end,
        trace_quality=_targeted_trace_quality(candidate),
        scan_support_score=_scan_support_from_candidate(candidate),
        neutral_loss_error_ppm=(
            candidate_ms2_evidence.best_loss_ppm
            if candidate_ms2_evidence is not None
            else None
        ),
        ms2_present=(
            candidate_ms2_evidence.ms2_present
            if candidate_ms2_evidence is not None
            else None
        ),
        nl_match=(
            candidate_ms2_evidence.nl_match
            if candidate_ms2_evidence is not None
            else None
        ),
        ms2_trace_strength=(
            candidate_ms2_evidence.trace.strength
            if candidate_ms2_evidence is not None
            else ""
        ),
        confidence=score.confidence if score is not None else "",
        evidence_score=score.raw_score if score is not None else None,
        reason=score.reason if score is not None else "",
    )


def common_evidence_from_discovery_candidate(candidate: Any) -> CommonEvidence:
    seed_event_count = _optional_int(_field(candidate, "seed_event_count"))
    neutral_loss_error_ppm = _optional_float(
        _field(candidate, "neutral_loss_mass_error_ppm")
    )
    return CommonEvidence(
        source="discovery_candidate",
        source_id=_text_field(candidate, "candidate_id"),
        provenance="original_discovery_seed",
        ms1_peak_found=_optional_bool(_field(candidate, "ms1_peak_found")),
        ms1_apex_rt_min=_optional_float(_field(candidate, "ms1_apex_rt")),
        ms1_area=_optional_float(_field(candidate, "ms1_area")),
        ms1_height=_optional_float(_field(candidate, "ms1_height")),
        ms1_peak_rt_start=_optional_float(_field(candidate, "ms1_peak_rt_start")),
        ms1_peak_rt_end=_optional_float(_field(candidate, "ms1_peak_rt_end")),
        rt_delta_min=_optional_float(_field(candidate, "ms1_seed_delta_min")),
        trace_quality=_text_field(candidate, "ms1_trace_quality"),
        scan_support_score=_optional_float(_field(candidate, "ms1_scan_support_score")),
        neutral_loss_tag=_text_field(candidate, "neutral_loss_tag"),
        configured_neutral_loss_da=_optional_float(
            _field(candidate, "configured_neutral_loss_da")
        ),
        observed_neutral_loss_da=_optional_float(
            _field(candidate, "observed_neutral_loss_da")
        ),
        neutral_loss_error_ppm=neutral_loss_error_ppm,
        seed_event_count=seed_event_count,
        ms2_present=seed_event_count is not None and seed_event_count > 0,
        nl_match=neutral_loss_error_ppm is not None,
        evidence_score=_optional_int(_field(candidate, "evidence_score")),
        review_priority=_text_field(candidate, "review_priority"),
        reason=_text_field(candidate, "reason"),
    )


def common_evidence_from_aligned_cell(
    cell: Any,
    *,
    neutral_loss_tag: str = "",
    family_id: str = "",
) -> CommonEvidence:
    status = _text_field(cell, "status")
    sample_stem = _text_field(cell, "sample_stem")
    provenance = status or "unknown"
    source_id = f"{family_id}:{sample_stem}" if family_id else sample_stem
    return CommonEvidence(
        source="alignment_cell",
        source_id=source_id,
        provenance=provenance,
        ms1_peak_found=status in {"detected", "rescued"},
        ms1_apex_rt_min=_optional_float(_field(cell, "apex_rt")),
        ms1_area=_optional_float(_field(cell, "area")),
        ms1_height=_optional_float(_field(cell, "height")),
        ms1_peak_rt_start=_optional_float(_field(cell, "peak_start_rt")),
        ms1_peak_rt_end=_optional_float(_field(cell, "peak_end_rt")),
        rt_delta_min=_delta_sec_to_min(_optional_float(_field(cell, "rt_delta_sec"))),
        trace_quality=_text_field(cell, "trace_quality"),
        scan_support_score=_optional_float(_field(cell, "scan_support_score")),
        neutral_loss_tag=neutral_loss_tag,
        reason=_text_field(cell, "reason"),
    )


def canonical_support_labels(evidence: CommonEvidence) -> tuple[str, ...]:
    labels: list[str] = []
    if evidence.ms1_peak_found:
        labels.append("ms1_peak")
    if _positive(evidence.ms1_area):
        labels.append("positive_area")
    if evidence.ms2_present:
        labels.append("ms2_present")
    if evidence.nl_match:
        labels.append("nl_match")
    if evidence.seed_event_count is not None and evidence.seed_event_count >= 2:
        labels.append("multi_seed")
    if evidence.scan_support_score is not None:
        labels.append("scan_support")
    return tuple(labels)


def canonical_concern_labels(evidence: CommonEvidence) -> tuple[str, ...]:
    labels: list[str] = []
    if evidence.ms1_peak_found is False:
        labels.append("missing_ms1_peak")
    if evidence.ms1_peak_found and not _positive(evidence.ms1_area):
        labels.append("non_positive_area")
    if evidence.nl_match is False:
        labels.append("nl_fail")
    if evidence.trace_quality and evidence.trace_quality != "clean":
        labels.append("trace_quality_review")
    if evidence.provenance in {"rescued", "owner_backfill"}:
        labels.append("backfill_provenance")
    return tuple(labels)


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
        "local_sn_strong" in support
        and trace_context_ok
        and has_shape_context
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
    not_counted_reasons = _decision_not_counted_reasons(
        signals,
        caps,
        support_reasons=support_reasons,
        consistency=consistency,
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
    if (
        "chrom_peak_segment_context" in support_reasons
        and len(support_reasons) == 1
    ):
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
        compatibility_labels=tuple(
            sorted(support | concerns | sources | caps | quality_flags)
        ),
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
    if (
        "rt_prior_close" in support
        or "paired_istd_aligned" in support
        or "paired_istd_rt_close" in support
    ):
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


def _decision_not_counted_reasons(
    signals: EvidenceSignalSet,
    caps: frozenset[str],
    *,
    support_reasons: tuple[str, ...],
    consistency: tuple[str, ...],
) -> tuple[str, ...]:
    reasons: list[str] = []
    confidence = signals.confidence.strip().upper()
    reason = signals.reason.strip().lower()
    legacy_review_only = confidence == "VERY_LOW" or "review only" in reason
    support = set(support_reasons)
    missing_ms2_policy_not_counted = (
        "no_ms2_cap" in caps and not signals.count_no_ms2_as_detected
    )
    hard_not_counted_caps = caps & {"zero_area_cap", "hard_quality_flag_cap"}
    strict_nl_supported = "candidate_aligned_ms2_nl" in support
    anchor_mismatch_policy_not_counted = (
        "anchor_mismatch_cap" in caps and not strict_nl_supported
    )
    if missing_ms2_policy_not_counted:
        hard_not_counted_caps = hard_not_counted_caps | {"no_ms2_cap"}
    if anchor_mismatch_policy_not_counted:
        hard_not_counted_caps = hard_not_counted_caps | {"anchor_mismatch_cap"}
    paired_supported_nl_dropout = (
        "nl_fail_cap" in caps
        and "plausible_nl_dropout" in consistency
        and "ms1_coherent" in support
        and "role_aware_rt_support" in support
        and "paired_area_ratio_support" in support
        and not hard_not_counted_caps
    )
    if paired_supported_nl_dropout:
        return ()

    if missing_ms2_policy_not_counted:
        reasons.append("missing_ms2_policy_not_counted")
        reasons.append("missing_ms2_compatibility_cap")
    if anchor_mismatch_policy_not_counted:
        reasons.append("paired_istd_rt_mismatch_policy")
        reasons.append("paired_istd_rt_mismatch_cap")

    legacy_not_counted_allowed = legacy_review_only and (
        "no_ms2_cap" not in caps
        or not signals.count_no_ms2_as_detected
        or bool(caps - {"no_ms2_cap"})
    )
    if legacy_not_counted_allowed:
        if not reasons or caps & {"zero_area_cap", "hard_quality_flag_cap"}:
            reasons.append("legacy_review_only_projection")
        if "zero_area_cap" in caps:
            reasons.append("zero_area_compatibility_cap")
        if "hard_quality_flag_cap" in caps:
            reasons.append("hard_quality_flag_compatibility_cap")
        if "anchor_mismatch_cap" in caps:
            reasons.append("anchor_mismatch_compatibility_cap")
    return tuple(dict.fromkeys(reasons))


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


def _targeted_trace_quality(candidate: PeakCandidate) -> str:
    return "review" if candidate.quality_flags else "clean"


def _scan_support_from_candidate(candidate: PeakCandidate) -> float | None:
    if candidate.region_scan_count is None:
        return None
    return float(candidate.region_scan_count)


def _field(item: Any, key: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(key)
    return getattr(item, key, None)


def _text_field(item: Any, key: str) -> str:
    value = _field(item, key)
    if value is None:
        return ""
    return str(value)


def _optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _delta_sec_to_min(value: float | None) -> float | None:
    if value is None:
        return None
    return value / 60.0


def _positive(value: float | None) -> bool:
    return value is not None and value > 0.0


def _label_set(values: tuple[str, ...]) -> frozenset[str]:
    return frozenset(_normalize_label(value) for value in values if value)


def _normalize_label(value: str) -> str:
    return (
        value.strip()
        .lower()
        .replace(" ", "_")
        .replace("-", "_")
        .replace("/", "_")
    )
