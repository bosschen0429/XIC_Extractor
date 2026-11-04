from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np

from xic_extractor.peak_detection.evidence_semantics import (
    EvidenceDecisionSemantics,
    EvidenceSignalSet,
    decision_semantics_from_signal_set,
)
from xic_extractor.peak_detection.scoring_metrics import (
    noise_shape_severity,
    peak_width_severity,
    rt_centrality_severity,
    rt_prior_severity,
    symmetry_severity,
)
from xic_extractor.peak_detection.scoring_quality import (
    candidate_quality_penalty,
    candidate_selection_quality_penalty,
    hard_quality_flags,
)

EvidenceQuality = Literal["strong", "clean", "borderline", "poor", "unknown"]
RtEvidenceStatus = Literal["missing", "close", "borderline", "far"]
RtWindowStatus = Literal["inside", "borderline", "outside", "unknown"]
AcquisitionOpportunity = Literal["observed", "not_observed", "not_required"]

FACTS_VERSION = "candidate_evidence_facts"
PAIRED_ISTD_MAX_DELTA_MIN = 1.0


@dataclass(frozen=True)
class TraceEvidenceFacts:
    local_sn_ratio: float | None = None
    local_sn_quality: EvidenceQuality = "unknown"
    baseline_method: str = ""
    residual_mad: float | None = None
    noise_sigma: float | None = None
    noise_source: str = ""
    active_trace_source: str = ""
    morphology_trace_method: str = ""
    morphology_trace_window_points: int | None = None
    symmetry_quality: EvidenceQuality = "unknown"
    width_quality: EvidenceQuality = "unknown"
    noise_shape_quality: EvidenceQuality = "unknown"
    scan_count: int | None = None
    duration_min: float | None = None
    edge_ratio: float | None = None
    continuity: float | None = None
    quality_flags: tuple[str, ...] = ()
    hard_quality_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChemicalEvidenceFacts:
    neutral_loss_required: bool = True
    ms2_present: bool | None = None
    nl_match: bool | None = None
    nl_status: str = ""
    ms2_trace_strength: str = ""
    loss_ppm: float | None = None
    ms2_rt_delta_min: float | None = None
    trigger_scan_count: int | None = None
    strict_nl_scan_count: int | None = None
    ms1_peak_group_source: str = ""
    ms1_peak_group_rt_min: float | None = None
    ms1_peak_group_rt_max: float | None = None
    ms1_peak_group_trigger_scan_count: int | None = None
    ms1_peak_group_strict_nl_scan_count: int | None = None
    ms1_peak_group_strict_nl_event_count: int | None = None
    outside_ms1_peak_group_trigger_scan_count: int | None = None
    outside_ms1_peak_group_strict_nl_scan_count: int | None = None
    alignment_source: str = ""
    product_absence_reason: str = ""
    acquisition_opportunity: AcquisitionOpportunity = "not_observed"


@dataclass(frozen=True)
class RtEvidenceFacts:
    selected_apex_rt_min: float
    rt_min: float | None = None
    rt_max: float | None = None
    rt_prior_min: float | None = None
    rt_prior_sigma_min: float | None = None
    rt_prior_delta_min: float | None = None
    rt_prior_status: RtEvidenceStatus = "missing"
    window_status: RtWindowStatus = "unknown"
    paired_istd_anchor_rt_min: float | None = None
    paired_istd_delta_min: float | None = None
    paired_istd_status: RtEvidenceStatus = "missing"
    role: str = ""
    istd_pair: str = ""


@dataclass(frozen=True)
class BoundaryEvidenceFacts:
    proposal_sources: tuple[str, ...] = ()
    cwt_same_apex_observed: bool = False
    cwt_best_scale: float | None = None
    cwt_ridge_persistence: float | None = None
    chrom_peak_segment_present: bool = False
    boundary_sources: tuple[str, ...] = ("candidate_interval",)


@dataclass(frozen=True)
class CandidateEvidenceFacts:
    facts_version: str
    candidate_id: str
    abundance: float
    area: float
    height: float
    trace: TraceEvidenceFacts
    chemical: ChemicalEvidenceFacts
    rt: RtEvidenceFacts
    boundary: BoundaryEvidenceFacts
    quality_penalty: int = 0
    selection_quality_penalty: float | None = None


def build_candidate_evidence_facts(
    candidate: Any,
    ctx: Any,
    *,
    role: str = "",
    istd_pair: str = "",
    paired_istd_anchor_rt_min: float | None = None,
) -> CandidateEvidenceFacts:
    quality_penalty, _ = candidate_quality_penalty(candidate)
    return CandidateEvidenceFacts(
        facts_version=FACTS_VERSION,
        candidate_id=_candidate_id(candidate),
        abundance=_candidate_abundance(candidate),
        area=_candidate_area(candidate),
        height=_candidate_height(candidate),
        trace=_trace_facts(candidate, ctx),
        chemical=_chemical_facts(candidate, ctx),
        rt=_rt_facts(
            candidate,
            ctx,
            role=role,
            istd_pair=istd_pair,
            paired_istd_anchor_rt_min=paired_istd_anchor_rt_min,
        ),
        boundary=_boundary_facts(candidate),
        quality_penalty=quality_penalty,
        selection_quality_penalty=candidate_selection_quality_penalty(candidate),
    )


def decision_semantics_from_candidate_facts(
    facts: CandidateEvidenceFacts,
    *,
    count_no_ms2_as_detected: bool = False,
) -> EvidenceDecisionSemantics:
    signals = _signal_set_from_facts(
        facts,
        count_no_ms2_as_detected=count_no_ms2_as_detected,
    )
    return decision_semantics_from_signal_set(signals)


def projected_confidence_from_candidate_facts(
    facts: CandidateEvidenceFacts,
    semantics: EvidenceDecisionSemantics | None = None,
) -> str:
    if semantics is None:
        semantics = decision_semantics_from_candidate_facts(facts)
    if semantics.decision_class in {"excluded", "not_counted", "ambiguous"}:
        return "VERY_LOW"
    if semantics.conflict_reasons:
        return "LOW"
    if semantics.review_reasons:
        return "MEDIUM"
    if semantics.decision_class == "accepted":
        return "HIGH"
    return "LOW"


def projected_reason_from_candidate_facts(
    facts: CandidateEvidenceFacts,
    semantics: EvidenceDecisionSemantics | None = None,
) -> str:
    if semantics is None:
        semantics = decision_semantics_from_candidate_facts(facts)
    details = (
        *semantics.support_reasons,
        *semantics.conflict_reasons,
        *semantics.review_reasons,
        *semantics.not_counted_reasons,
        *semantics.exclusion_reasons,
        *semantics.ambiguity_reasons,
    )
    if not details:
        return f"decision: {semantics.decision_class}"
    return f"decision: {semantics.decision_class}; evidence: {'; '.join(details)}"


def _signal_set_from_facts(
    facts: CandidateEvidenceFacts,
    *,
    count_no_ms2_as_detected: bool,
) -> EvidenceSignalSet:
    support: list[str] = []
    concerns: list[str] = []
    caps: list[str] = []

    chemical = facts.chemical
    if not chemical.neutral_loss_required:
        support.append("no_nl_required")
    elif chemical.ms2_present and chemical.nl_match:
        support.append("strict_nl_ok")
        if chemical.ms2_trace_strength == "strong":
            support.append("ms2_trace_strong")
        elif chemical.ms2_trace_strength == "moderate":
            support.append("ms2_trace_moderate")
        elif chemical.ms2_trace_strength == "weak":
            concerns.append("ms2_trace_weak")
    elif chemical.ms2_present and chemical.nl_match is False:
        concerns.append("nl_fail")
        caps.append("nl_fail_cap")
    else:
        concerns.append("no_ms2")
        caps.append("no_ms2_cap")

    rt_support_emitted = False
    if facts.rt.rt_prior_status == "close":
        support.append("rt_prior_close")
        rt_support_emitted = True
    elif facts.rt.rt_prior_status == "borderline":
        concerns.append("rt_prior_borderline")
    elif facts.rt.rt_prior_status == "far":
        concerns.append("rt_prior_far")

    if facts.rt.paired_istd_status == "close":
        if not rt_support_emitted:
            support.append("paired_istd_rt_close")
    elif facts.rt.paired_istd_status == "far":
        concerns.append("anchor_mismatch")
        caps.append("anchor_mismatch_cap")

    if facts.rt.window_status == "borderline":
        concerns.append("rt_centrality_borderline")
    elif facts.rt.window_status == "outside":
        concerns.append("rt_centrality_poor")
        if facts.rt.rt_prior_status != "close":
            caps.append("rt_window_cap")

    if facts.trace.local_sn_quality == "strong":
        support.append("local_sn_strong")
    elif facts.trace.local_sn_quality == "borderline":
        concerns.append("local_sn_borderline")
    elif facts.trace.local_sn_quality == "poor":
        concerns.append("local_sn_poor")

    shape_quality = _worst_quality(
        facts.trace.symmetry_quality,
        facts.trace.width_quality,
    )
    if shape_quality == "clean":
        support.append("shape_clean")
    elif shape_quality == "borderline":
        concerns.append("shape_borderline")
    elif shape_quality == "poor":
        concerns.append("shape_poor")

    if facts.trace.noise_shape_quality == "borderline":
        concerns.append("noise_shape_borderline")
    elif facts.trace.noise_shape_quality == "poor":
        concerns.append("noise_shape_poor")

    if not facts.trace.hard_quality_flags:
        support.append("trace_clean")
    for flag in facts.trace.quality_flags:
        if flag in {"low_scan_support", "low_trace_continuity", "poor_edge_recovery"}:
            concerns.append(flag)
    if facts.trace.hard_quality_flags:
        concerns.append("hard_quality_flag")
        caps.append("hard_quality_flag_cap")

    if facts.boundary.cwt_same_apex_observed and (
        not chemical.neutral_loss_required or bool(chemical.nl_match)
    ):
        support.append("cwt_same_apex_support")

    ms2_present_for_semantics = (
        facts.chemical.ms2_present if facts.chemical.neutral_loss_required else None
    )
    nl_match_for_semantics = (
        facts.chemical.nl_match
        if facts.chemical.neutral_loss_required and facts.chemical.ms2_present
        else None
    )
    return EvidenceSignalSet(
        support_labels=tuple(dict.fromkeys(support)),
        concern_labels=tuple(dict.fromkeys(concerns)),
        proposal_sources=facts.boundary.proposal_sources,
        quality_flags=facts.trace.hard_quality_flags,
        ms2_present=ms2_present_for_semantics,
        nl_match=nl_match_for_semantics,
        cap_labels=tuple(dict.fromkeys(caps)),
        count_no_ms2_as_detected=count_no_ms2_as_detected,
    )


def _trace_facts(candidate: Any, ctx: Any) -> TraceEvidenceFacts:
    local_ratio, local_quality, baseline_method, noise_source = _local_sn_facts(ctx)
    flags = tuple(
        dict.fromkeys(str(flag) for flag in getattr(candidate, "quality_flags", ()))
    )
    return TraceEvidenceFacts(
        local_sn_ratio=local_ratio,
        local_sn_quality=local_quality,
        baseline_method=baseline_method,
        noise_sigma=(
            ctx.signal_quality.noise_sigma
            if getattr(ctx, "signal_quality", None) is not None
            else None
        ),
        noise_source=noise_source,
        active_trace_source=str(getattr(ctx, "active_trace_source", "") or ""),
        morphology_trace_method=str(getattr(ctx, "morphology_trace_method", "") or ""),
        morphology_trace_window_points=getattr(
            ctx,
            "morphology_trace_window_points",
            None,
        ),
        symmetry_quality=_severity_quality(
            symmetry_severity(float(getattr(ctx, "half_width_ratio", math.nan)))[0],
            clean_label="clean",
        ),
        width_quality=_severity_quality(
            peak_width_severity(getattr(ctx, "fwhm_ratio", None))[0],
            clean_label="clean",
        ),
        noise_shape_quality=_severity_quality(
            noise_shape_severity(np.asarray(getattr(ctx, "intensity_array", [])))[0],
            clean_label="clean",
        ),
        scan_count=getattr(candidate, "region_scan_count", None),
        duration_min=getattr(candidate, "region_duration_min", None),
        edge_ratio=getattr(candidate, "region_edge_ratio", None),
        continuity=getattr(candidate, "region_trace_continuity", None),
        quality_flags=flags,
        hard_quality_flags=hard_quality_flags(flags),
    )


def _local_sn_facts(ctx: Any) -> tuple[float | None, EvidenceQuality, str, str]:
    quality = getattr(ctx, "signal_quality", None)
    if quality is None:
        return None, "unknown", "", ""
    return (
        quality.signal_to_noise,
        quality.scoring_quality,
        str(getattr(ctx, "baseline_method", "")),
        "rt_weighted_three_point_residual_sigma",
    )


def _chemical_facts(candidate: Any, ctx: Any) -> ChemicalEvidenceFacts:
    ms2_present = bool(getattr(ctx, "ms2_present", False))
    nl_match = bool(getattr(ctx, "nl_match", False))
    neutral_loss_required = bool(getattr(ctx, "neutral_loss_required", True))
    best_scan_rt = getattr(ctx, "best_ms2_scan_rt_min", None)
    selected_rt = float(getattr(candidate, "selection_apex_rt", math.nan))
    return ChemicalEvidenceFacts(
        neutral_loss_required=neutral_loss_required,
        ms2_present=ms2_present,
        nl_match=nl_match,
        nl_status=_nl_status(neutral_loss_required, ms2_present, nl_match),
        ms2_trace_strength=str(getattr(ctx, "ms2_trace_strength", "") or ""),
        loss_ppm=_optional_float(getattr(ctx, "best_loss_ppm", None)),
        ms2_rt_delta_min=(
            abs(selected_rt - float(best_scan_rt))
            if best_scan_rt is not None and _is_finite(selected_rt)
            else None
        ),
        trigger_scan_count=getattr(ctx, "trigger_scan_count", None),
        strict_nl_scan_count=getattr(ctx, "strict_nl_scan_count", None),
        ms1_peak_group_source=str(getattr(ctx, "ms1_peak_group_source", "") or ""),
        ms1_peak_group_rt_min=_optional_float(
            getattr(ctx, "ms1_peak_group_rt_min", None)
        ),
        ms1_peak_group_rt_max=_optional_float(
            getattr(ctx, "ms1_peak_group_rt_max", None)
        ),
        ms1_peak_group_trigger_scan_count=getattr(
            ctx,
            "ms1_peak_group_trigger_scan_count",
            None,
        ),
        ms1_peak_group_strict_nl_scan_count=getattr(
            ctx,
            "ms1_peak_group_strict_nl_scan_count",
            None,
        ),
        ms1_peak_group_strict_nl_event_count=getattr(
            ctx,
            "ms1_peak_group_strict_nl_event_count",
            None,
        ),
        outside_ms1_peak_group_trigger_scan_count=getattr(
            ctx,
            "outside_ms1_peak_group_trigger_scan_count",
            None,
        ),
        outside_ms1_peak_group_strict_nl_scan_count=getattr(
            ctx,
            "outside_ms1_peak_group_strict_nl_scan_count",
            None,
        ),
        alignment_source=str(getattr(ctx, "ms2_alignment_source", "") or ""),
        product_absence_reason=str(
            getattr(ctx, "diagnostic_product_absence_reason", "") or ""
        ),
        acquisition_opportunity=(
            "not_required"
            if not neutral_loss_required
            else "observed"
            if ms2_present
            else "not_observed"
        ),
    )


def _rt_facts(
    candidate: Any,
    ctx: Any,
    *,
    role: str,
    istd_pair: str,
    paired_istd_anchor_rt_min: float | None,
) -> RtEvidenceFacts:
    selected_rt = float(candidate.selection_apex_rt)
    rt_prior = getattr(ctx, "rt_prior", None)
    rt_prior_delta = (
        round(abs(selected_rt - float(rt_prior)), 10)
        if rt_prior is not None and _is_finite(float(rt_prior))
        else None
    )
    paired_istd_delta = (
        round(abs(selected_rt - float(paired_istd_anchor_rt_min)), 10)
        if paired_istd_anchor_rt_min is not None
        and _is_finite(float(paired_istd_anchor_rt_min))
        else None
    )
    rt_severity = rt_prior_severity(
        selected_rt,
        rt_prior,
        getattr(ctx, "rt_prior_sigma", None),
    )[0]
    centrality_severity = rt_centrality_severity(
        selected_rt,
        float(getattr(ctx, "rt_min", math.nan)),
        float(getattr(ctx, "rt_max", math.nan)),
    )[0]
    return RtEvidenceFacts(
        selected_apex_rt_min=selected_rt,
        rt_min=_optional_float(getattr(ctx, "rt_min", None)),
        rt_max=_optional_float(getattr(ctx, "rt_max", None)),
        rt_prior_min=_optional_float(rt_prior),
        rt_prior_sigma_min=_optional_float(getattr(ctx, "rt_prior_sigma", None)),
        rt_prior_delta_min=rt_prior_delta,
        rt_prior_status=_rt_prior_status(rt_prior, rt_severity),
        window_status=_window_status(centrality_severity),
        paired_istd_anchor_rt_min=paired_istd_anchor_rt_min,
        paired_istd_delta_min=paired_istd_delta,
        paired_istd_status=_paired_istd_status(
            role=role,
            delta_min=paired_istd_delta,
        ),
        role=role,
        istd_pair=istd_pair,
    )


def _boundary_facts(candidate: Any) -> BoundaryEvidenceFacts:
    proposal_sources = tuple(
        dict.fromkeys(
            str(source) for source in getattr(candidate, "proposal_sources", ())
        )
    )
    cwt_best_scale = getattr(candidate, "cwt_best_scale", None)
    cwt_ridge_persistence = getattr(candidate, "cwt_ridge_persistence", None)
    return BoundaryEvidenceFacts(
        proposal_sources=proposal_sources,
        cwt_same_apex_observed=(
            cwt_best_scale is not None
            or cwt_ridge_persistence is not None
            or "centwave_cwt" in proposal_sources
        ),
        cwt_best_scale=cwt_best_scale,
        cwt_ridge_persistence=cwt_ridge_persistence,
        chrom_peak_segment_present="chrom_peak_segment" in proposal_sources,
    )


def _candidate_id(candidate: Any) -> str:
    sources = ";".join(
        str(source) for source in getattr(candidate, "proposal_sources", ())
    )
    selected_rt = float(candidate.selection_apex_rt)
    peak = getattr(candidate, "peak", None)
    peak_start = getattr(peak, "peak_start", selected_rt)
    peak_end = getattr(peak, "peak_end", selected_rt)
    return "|".join(
        (
            sources,
            f"{selected_rt:.5f}",
            f"{float(peak_start):.5f}",
            f"{float(peak_end):.5f}",
        )
    )


def _candidate_abundance(candidate: Any) -> float:
    area = _candidate_area(candidate)
    if area > 0:
        return area
    return _candidate_height(candidate)


def _candidate_area(candidate: Any) -> float:
    peak = getattr(candidate, "peak", None)
    value_source = getattr(peak, "area", None)
    if value_source is None:
        value_source = getattr(candidate, "peak_area", None)
    if value_source is None:
        value_source = getattr(candidate, "area", None)
    if value_source is None:
        return 0.0
    try:
        value = float(value_source)
    except (TypeError, ValueError):
        return 0.0
    return value if _is_finite(value) else 0.0


def _candidate_height(candidate: Any) -> float:
    try:
        value = float(candidate.selection_apex_intensity)
    except (TypeError, ValueError):
        return 0.0
    return value if _is_finite(value) else 0.0


def _severity_quality(
    severity: int,
    *,
    clean_label: EvidenceQuality,
) -> EvidenceQuality:
    if severity <= 0:
        return clean_label
    if severity == 1:
        return "borderline"
    return "poor"


def _rt_prior_status(rt_prior: float | None, severity: int) -> RtEvidenceStatus:
    if rt_prior is None:
        return "missing"
    if severity <= 0:
        return "close"
    if severity == 1:
        return "borderline"
    return "far"


def _window_status(severity: int) -> RtWindowStatus:
    if severity <= 0:
        return "inside"
    if severity == 1:
        return "borderline"
    return "outside"


def _paired_istd_status(
    *,
    role: str,
    delta_min: float | None,
) -> RtEvidenceStatus:
    if role != "Analyte" or delta_min is None:
        return "missing"
    if delta_min <= PAIRED_ISTD_MAX_DELTA_MIN:
        return "close"
    return "far"


def _nl_status(
    neutral_loss_required: bool,
    ms2_present: bool,
    nl_match: bool,
) -> str:
    if not neutral_loss_required:
        return "NOT_REQUIRED"
    if ms2_present and nl_match:
        return "OK"
    if ms2_present and not nl_match:
        return "NL_FAIL"
    return "NO_MS2"


def _worst_quality(*values: EvidenceQuality) -> EvidenceQuality:
    rank = {"strong": 0, "clean": 0, "unknown": 1, "borderline": 2, "poor": 3}
    return max(values, key=lambda value: rank[value])


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if _is_finite(number) else None


def _is_finite(value: float) -> bool:
    return math.isfinite(float(value))
