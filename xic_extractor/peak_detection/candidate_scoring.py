from __future__ import annotations

from typing import Any

from xic_extractor.peak_detection.evidence_facts import (
    build_candidate_evidence_facts,
    decision_semantics_from_candidate_facts,
    projected_confidence_from_candidate_facts,
    projected_reason_from_candidate_facts,
)
from xic_extractor.peak_detection.peak_scoring_evidence import (
    ConfidenceCap,
    EvidenceSignal,
    score_evidence,
)
from xic_extractor.peak_detection.scoring_cwt_support import (
    CWT_SAME_APEX_SUPPORT_POINTS,
    has_same_apex_cwt_support,
)
from xic_extractor.peak_detection.scoring_metrics import (
    LABEL_LOCAL_SN,
    LABEL_NL,
    LABEL_NOISE_SHAPE,
    LABEL_PEAK_WIDTH,
    LABEL_RT_CENTRALITY,
    LABEL_RT_PRIOR,
    LABEL_SYMMETRY,
    local_sn_severity,
    nl_support_severity,
    noise_shape_severity,
    outside_rt_window,
    peak_width_severity,
    rt_centrality_severity,
    rt_prior_severity,
    symmetry_severity,
)
from xic_extractor.peak_detection.scoring_models import (
    ScoredCandidate,
    ScoringContext,
    confidence_from_value,
)
from xic_extractor.peak_detection.scoring_quality import (
    candidate_quality_penalty,
    candidate_selection_quality_penalty,
    has_adap_like_quality_flags,
    trace_quality_cap_required,
    trace_quality_severities,
)


def score_candidate(
    candidate: Any,
    ctx: ScoringContext,
    prior_rt: float | None,
    istd_confidence_note: str | None = None,
    *,
    evidence_role: str = "",
    istd_pair: str = "",
    paired_istd_anchor_rt: float | None = None,
) -> ScoredCandidate:
    quality_penalty, quality_notes = candidate_quality_penalty(candidate)
    selection_quality_penalty = quality_penalty + candidate_selection_quality_penalty(
        candidate
    )
    severities: list[tuple[int, str]] = [
        symmetry_severity(ctx.half_width_ratio),
        local_sn_severity(
            ctx.intensity_array,
            ctx.apex_index,
            ctx.dirty_matrix,
            baseline=ctx.baseline_array,
            residual_mad=ctx.residual_mad,
        ),
        (
            nl_support_severity(ctx.ms2_present, ctx.nl_match)
            if ctx.neutral_loss_required
            else (0, LABEL_NL)
        ),
        rt_prior_severity(
            candidate.selection_apex_rt,
            ctx.rt_prior,
            ctx.rt_prior_sigma,
        ),
        rt_centrality_severity(candidate.selection_apex_rt, ctx.rt_min, ctx.rt_max),
        noise_shape_severity(ctx.intensity_array),
        peak_width_severity(ctx.fwhm_ratio),
        *trace_quality_severities(candidate),
    ]
    positive, negative, caps = _evidence_from_context(
        candidate,
        ctx,
        severities,
        quality_penalty,
    )
    evidence_score = score_evidence(positive=positive, negative=negative, caps=caps)
    evidence_facts = build_candidate_evidence_facts(
        candidate,
        ctx,
        role=evidence_role,
        istd_pair=istd_pair,
        paired_istd_anchor_rt_min=paired_istd_anchor_rt,
    )
    semantics = decision_semantics_from_candidate_facts(
        evidence_facts,
        count_no_ms2_as_detected=ctx.count_no_ms2_as_detected,
    )
    confidence = confidence_from_value(
        projected_confidence_from_candidate_facts(evidence_facts, semantics)
    )
    reason = projected_reason_from_candidate_facts(evidence_facts, semantics)
    # Contextual reasons carry quality through facts; empty-context scoring
    # also exposes the detailed legacy quality note to its current consumers.
    if (
        quality_notes
        and not evidence_role
        and not istd_pair
        and paired_istd_anchor_rt is None
    ):
        reason = f"{reason}; {'; '.join(quality_notes)}"
    if istd_confidence_note:
        reason = f"{reason}; {istd_confidence_note}"
    return ScoredCandidate(
        candidate=candidate,
        severities=tuple(severities),
        confidence=confidence,
        reason=reason,
        prior_rt=prior_rt,
        quality_penalty=quality_penalty,
        selection_quality_penalty=selection_quality_penalty,
        prefer_rt_prior_tiebreak=ctx.prefer_rt_prior_tiebreak,
        evidence_score=evidence_score,
        evidence_facts=evidence_facts,
    )


def _evidence_from_context(
    candidate: Any,
    ctx: ScoringContext,
    severities: list[tuple[int, str]],
    quality_penalty: int,
) -> tuple[list[EvidenceSignal], list[EvidenceSignal], list[ConfidenceCap]]:
    positive: list[EvidenceSignal] = []
    negative: list[EvidenceSignal] = []
    caps: list[ConfidenceCap] = []

    if not ctx.neutral_loss_required:
        positive.append(EvidenceSignal("no_nl_required", 10))
    elif ctx.ms2_present and ctx.nl_match:
        positive.append(EvidenceSignal("strict_nl_ok", 30))
        if ctx.ms2_trace_strength == "strong":
            if _is_sparse_apex_fallback_ms2(candidate, ctx):
                negative.append(EvidenceSignal("sparse_apex_ms2", 8))
            else:
                positive.append(EvidenceSignal("ms2_trace_strong", 10))
        elif ctx.ms2_trace_strength == "moderate":
            positive.append(EvidenceSignal("ms2_trace_moderate", 5))
        elif ctx.ms2_trace_strength == "weak":
            negative.append(EvidenceSignal("ms2_trace_weak", 8))
    elif ctx.ms2_present and not ctx.nl_match:
        negative.append(EvidenceSignal("nl_fail", 45))
        caps.append(ConfidenceCap("nl_fail_cap", "VERY_LOW"))
    else:
        negative.append(EvidenceSignal("no_ms2", 25))
        caps.append(ConfidenceCap("no_ms2_cap", "LOW"))

    severity_by_label = {label: severity for severity, label in severities}
    rt_prior_close = False
    if ctx.rt_prior is not None:
        rt_severity = severity_by_label[LABEL_RT_PRIOR]
        if rt_severity == 0:
            positive.append(EvidenceSignal("rt_prior_close", 15))
            rt_prior_close = True
            if ctx.prefer_rt_prior_tiebreak:
                positive.append(EvidenceSignal("paired_istd_aligned", 20))
        elif rt_severity == 1:
            negative.append(EvidenceSignal("rt_prior_borderline", 15))
        else:
            negative.append(EvidenceSignal("rt_prior_far", 35))

    rt_centrality = severity_by_label[LABEL_RT_CENTRALITY]
    if rt_centrality == 1:
        negative.append(EvidenceSignal("rt_centrality_borderline", 10))
    elif rt_centrality == 2:
        negative.append(EvidenceSignal("rt_centrality_poor", 20))
        if (
            outside_rt_window(candidate.selection_apex_rt, ctx.rt_min, ctx.rt_max)
            and not rt_prior_close
        ):
            caps.append(ConfidenceCap("rt_window_cap", "VERY_LOW"))

    local_sn = severity_by_label[LABEL_LOCAL_SN]
    if local_sn == 0:
        positive.append(EvidenceSignal("local_sn_strong", 10))
    elif local_sn == 1:
        negative.append(EvidenceSignal("local_sn_borderline", 10))
    else:
        negative.append(EvidenceSignal("local_sn_poor", 25))

    shape = max(
        severity_by_label[LABEL_SYMMETRY],
        severity_by_label[LABEL_PEAK_WIDTH],
    )
    if shape == 0:
        positive.append(EvidenceSignal("shape_clean", 10))
    elif shape == 1:
        negative.append(EvidenceSignal("shape_borderline", 10))
    else:
        negative.append(EvidenceSignal("shape_poor", 20))

    noise_shape = severity_by_label[LABEL_NOISE_SHAPE]
    if noise_shape == 1:
        negative.append(EvidenceSignal("noise_shape_borderline", 10))
    elif noise_shape == 2:
        negative.append(EvidenceSignal("noise_shape_poor", 20))

    if not has_adap_like_quality_flags(tuple(getattr(candidate, "quality_flags", ()))):
        positive.append(EvidenceSignal("trace_clean", 10))

    has_cwt_same_apex_support = (
        has_same_apex_cwt_support(candidate) and _has_cwt_chemical_support(ctx)
    )
    if has_cwt_same_apex_support:
        positive.append(
            EvidenceSignal(
                "cwt_same_apex_support",
                CWT_SAME_APEX_SUPPORT_POINTS,
            )
        )

    trace_evidence = {
        "low scan support": ("low_scan_support", 15),
        "low trace continuity": ("low_trace_continuity", 10),
        "poor edge recovery": ("poor_edge_recovery", 10),
    }
    active_trace_flags: list[str] = []
    for severity, label in severities:
        if severity == 0 or label not in trace_evidence:
            continue
        evidence_label, points = trace_evidence[label]
        negative.append(EvidenceSignal(evidence_label, points))
        active_trace_flags.append(evidence_label)
    if trace_quality_cap_required(
        active_trace_flags,
        has_cwt_same_apex_support=has_cwt_same_apex_support,
    ):
        caps.append(ConfidenceCap("trace_quality_cap", "MEDIUM"))

    if quality_penalty > 0:
        negative.append(EvidenceSignal("hard_quality_flag", 25 * quality_penalty))
        caps.append(ConfidenceCap("hard_quality_flag_cap", "MEDIUM"))

    return positive, negative, caps


def _is_sparse_apex_fallback_ms2(candidate: Any, ctx: ScoringContext) -> bool:
    if ctx.ms2_alignment_source != "apex_fallback":
        return False
    flags = {str(flag) for flag in getattr(candidate, "quality_flags", ())}
    if "low_scan_support" not in flags:
        return False
    trigger_count = ctx.trigger_scan_count
    strict_count = ctx.strict_nl_scan_count
    if trigger_count is None and strict_count is None:
        return False
    return (trigger_count is not None and trigger_count <= 2) or (
        strict_count is not None and strict_count <= 2
    )


def _has_cwt_chemical_support(ctx: ScoringContext) -> bool:
    if not ctx.neutral_loss_required:
        return True
    return ctx.ms2_present and ctx.nl_match
