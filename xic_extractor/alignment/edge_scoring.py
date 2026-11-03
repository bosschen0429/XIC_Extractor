from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.drift_evidence import DriftPriorSource
from xic_extractor.alignment.grouping_evidence import (
    pair_ppm,
    peak_correspondence_conflict,
)
from xic_extractor.alignment.grouping_rules import (
    DuplicateContext,
    MethodPairEvidence,
    SeedSupportLevel,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.alignment.rt_frame import RtFrame

EdgeDecision = Literal["strong_edge", "weak_edge", "blocked_edge"]
HardGateFailureReason = str

RTEvidenceBasis = Literal["raw", "drift_corrected"]
OwnerQuality = Literal["clean", "weak", "tail_supported", "ambiguous_nearby"]


@dataclass(frozen=True)
class OwnerEdgeEvidence:
    """Pair decision; prior source records the lookup, rt_basis records its use.

    Corrected RT is absent when the trigger skips lookup or the lookup cannot
    assess the pair. Reasons distinguish raw sufficiency from unavailability.
    """

    left_owner_id: str
    right_owner_id: str
    left_sample_stem: str
    right_sample_stem: str
    neutral_loss_tag: str
    left_precursor_mz: float
    right_precursor_mz: float
    left_rt_min: float
    right_rt_min: float
    decision: EdgeDecision
    failure_reason: HardGateFailureReason | Literal[""]
    rt_raw_delta_sec: float
    rt_drift_corrected_delta_sec: float | None
    drift_prior_source: DriftPriorSource
    rt_basis: RTEvidenceBasis
    injection_order_gap: int | None
    owner_quality: OwnerQuality
    seed_support_level: SeedSupportLevel
    duplicate_context: DuplicateContext
    score: int
    reason: str
    failure_allows_rt_continuity: bool


def evaluate_owner_edge(
    left: SampleLocalMS1Owner,
    right: SampleLocalMS1Owner,
    *,
    config: AlignmentConfig,
    rt_frame: RtFrame,
    method_evidence: MethodPairEvidence,
) -> OwnerEdgeEvidence:
    rt_raw_delta_sec = abs(left.owner_apex_rt - right.owner_apex_rt) * 60.0
    drift_source = rt_frame.drift_source
    injection_order_gap = rt_frame.drift_injection_order_gap(
        left.sample_stem, right.sample_stem
    )
    duplicate_context = method_evidence.duplicate_context
    owner_quality = _owner_quality(left, right, duplicate_context)
    seed_support_level = method_evidence.seed_support_level

    failure, failure_allows_rt_continuity = _hard_gate_failure(
        left,
        right,
        config=config,
        method_evidence=method_evidence,
        rt_frame=rt_frame,
    )
    if failure:
        # Method permission applies only after all shared physical gates pass.
        corrected_delta_sec = (
            rt_frame.corrected_pair_delta_sec(left, right)
            if failure_allows_rt_continuity
            else None
        )
        return _evidence(
            left,
            right,
            decision="blocked_edge",
            failure_reason=failure,
            rt_raw_delta_sec=rt_raw_delta_sec,
            rt_drift_corrected_delta_sec=corrected_delta_sec,
            drift_prior_source=drift_source,
            rt_basis="drift_corrected" if corrected_delta_sec is not None else "raw",
            injection_order_gap=injection_order_gap,
            owner_quality=owner_quality,
            seed_support_level=seed_support_level,
            duplicate_context=duplicate_context,
            score=0,
            reason=f"blocked: {failure}",
            failure_allows_rt_continuity=failure_allows_rt_continuity,
        )

    score, decision, rt_basis, reason, corrected_delta_sec = _score_and_decide(
        rt_raw_delta_sec=rt_raw_delta_sec,
        corrected_delta_supplier=lambda: rt_frame.corrected_pair_delta_sec(left, right),
        has_direct_reference=rt_frame.has_direct_reference_pair(left, right),
        drift_prior_source=drift_source,
        config=config,
        seed_support_level=seed_support_level,
        owner_quality=owner_quality,
        duplicate_context=duplicate_context,
    )
    return _evidence(
        left,
        right,
        decision=decision,
        failure_reason="",
        rt_raw_delta_sec=rt_raw_delta_sec,
        rt_drift_corrected_delta_sec=corrected_delta_sec,
        drift_prior_source=drift_source,
        rt_basis=rt_basis,
        injection_order_gap=injection_order_gap,
        owner_quality=owner_quality,
        seed_support_level=seed_support_level,
        duplicate_context=duplicate_context,
        score=score,
        reason=reason,
    )


def _hard_gate_failure(
    left: SampleLocalMS1Owner,
    right: SampleLocalMS1Owner,
    *,
    rt_frame: RtFrame,
    config: AlignmentConfig,
    method_evidence: MethodPairEvidence,
) -> tuple[HardGateFailureReason | None, bool]:
    if left.sample_stem == right.sample_stem:
        return "same_sample", False
    if method_evidence.identity_failure:
        return method_evidence.identity_failure, False
    if pair_ppm(left.precursor_mz, right.precursor_mz) > config.max_ppm:
        return "precursor_mz_out_of_tolerance", False
    if (
        peak_correspondence_conflict(
            (left,),
            (right,),
            config,
            rt_frame=rt_frame,
        )
        is not None
    ):
        return "peak_correspondence_mismatch", False
    return (
        method_evidence.measurement_failure,
        method_evidence.measurement_failure_allows_rt_continuity,
    )


def _owner_quality(
    left: SampleLocalMS1Owner,
    right: SampleLocalMS1Owner,
    duplicate_context: DuplicateContext,
) -> OwnerQuality:
    if "ambiguous" in left.assignment_reason or "ambiguous" in right.assignment_reason:
        return "ambiguous_nearby"
    if left.owner_area <= 0 or right.owner_area <= 0:
        return "weak"
    if duplicate_context == "tail_assignment":
        return "tail_supported"
    return "clean"


def _score_and_decide(
    *,
    rt_raw_delta_sec: float,
    corrected_delta_supplier: Callable[[], float | None],
    has_direct_reference: bool,
    drift_prior_source: DriftPriorSource,
    config: AlignmentConfig,
    seed_support_level: SeedSupportLevel,
    owner_quality: OwnerQuality,
    duplicate_context: DuplicateContext,
) -> tuple[int, EdgeDecision, RTEvidenceBasis, str, float | None]:
    """Use frozen batch coordinates; legacy drift remains an optional prior."""
    if drift_prior_source == "batch_anchor_calibration":
        corrected = corrected_delta_supplier()
        if corrected is not None:
            # One authoritative coordinate, not competing raw/corrected votes.
            score = _score(
                rt_raw_delta_sec=corrected,
                rt_drift_corrected_delta_sec=None,
                config=config,
                seed_support_level=seed_support_level,
                owner_quality=owner_quality,
                duplicate_context=duplicate_context,
            )
            decision = _decision(
                rt_raw_delta_sec=corrected,
                rt_drift_corrected_delta_sec=None,
                drift_prior_source="none",
                owner_quality=owner_quality,
                seed_support_level=seed_support_level,
                score=score,
                config=config,
            )
            return (
                score,
                decision,
                "drift_corrected",
                _pass_reason(decision, score),
                corrected,
            )
    raw_score = _score(
        rt_raw_delta_sec=rt_raw_delta_sec,
        rt_drift_corrected_delta_sec=None,
        config=config,
        seed_support_level=seed_support_level,
        owner_quality=owner_quality,
        duplicate_context=duplicate_context,
    )
    raw_decision = _decision(
        rt_raw_delta_sec=rt_raw_delta_sec,
        rt_drift_corrected_delta_sec=None,
        drift_prior_source="none",
        owner_quality=owner_quality,
        seed_support_level=seed_support_level,
        score=raw_score,
        config=config,
    )
    raw_reason = _pass_reason(raw_decision, raw_score)
    if drift_prior_source == "none":
        return raw_score, raw_decision, "raw", raw_reason, None
    if raw_decision == "strong_edge" and not has_direct_reference:
        return (
            raw_score,
            raw_decision,
            "raw",
            raw_reason + ";drift=raw_sufficient",
            None,
        )
    if seed_support_level == "weak" or owner_quality in {"weak", "ambiguous_nearby"}:
        return (
            raw_score,
            raw_decision,
            "raw",
            raw_reason + ";drift=insufficient_non_rt_evidence",
            None,
        )
    rt_drift_corrected_delta_sec = corrected_delta_supplier()
    if rt_drift_corrected_delta_sec is None:
        return raw_score, raw_decision, "raw", raw_reason + ";drift=unavailable", None
    if (
        _drift_is_contradictory(
            rt_raw_delta_sec,
            rt_drift_corrected_delta_sec,
        )
        or rt_raw_delta_sec <= config.preferred_rt_sec < rt_drift_corrected_delta_sec
    ):
        return (
            raw_score,
            raw_decision,
            "raw",
            raw_reason + ";drift=raw_fallback",
            rt_drift_corrected_delta_sec,
        )

    score = _score(
        rt_raw_delta_sec=rt_raw_delta_sec,
        rt_drift_corrected_delta_sec=rt_drift_corrected_delta_sec,
        config=config,
        seed_support_level=seed_support_level,
        owner_quality=owner_quality,
        duplicate_context=duplicate_context,
    )
    decision = _decision(
        rt_raw_delta_sec=rt_raw_delta_sec,
        rt_drift_corrected_delta_sec=rt_drift_corrected_delta_sec,
        drift_prior_source=drift_prior_source,
        owner_quality=owner_quality,
        seed_support_level=seed_support_level,
        score=score,
        config=config,
    )
    return (
        score,
        decision,
        "drift_corrected",
        _pass_reason(decision, score),
        rt_drift_corrected_delta_sec,
    )


def _score(
    *,
    rt_raw_delta_sec: float,
    rt_drift_corrected_delta_sec: float | None,
    config: AlignmentConfig,
    seed_support_level: SeedSupportLevel,
    owner_quality: OwnerQuality,
    duplicate_context: DuplicateContext,
) -> int:
    score = 0
    if rt_raw_delta_sec <= config.preferred_rt_sec:
        score += 30
    elif rt_raw_delta_sec <= config.max_rt_sec:
        score += 10

    if rt_drift_corrected_delta_sec is not None:
        if rt_drift_corrected_delta_sec <= config.preferred_rt_sec:
            score += 35
        elif rt_drift_corrected_delta_sec <= config.max_rt_sec:
            score += 10
        if rt_raw_delta_sec - rt_drift_corrected_delta_sec >= 10.0:
            score += 10
        if rt_drift_corrected_delta_sec > rt_raw_delta_sec + 10.0:
            score -= 20

    if seed_support_level == "strong":
        score += 15
    elif seed_support_level == "moderate":
        score += 5
    else:
        score -= 30

    if owner_quality == "clean":
        score += 10
    elif owner_quality == "tail_supported":
        score += 5
    elif owner_quality == "weak":
        score -= 30
    else:
        score -= 20

    if duplicate_context in {"same_owner_events", "tail_assignment"}:
        score += 5
    return score


def _decision(
    *,
    rt_raw_delta_sec: float,
    rt_drift_corrected_delta_sec: float | None,
    drift_prior_source: DriftPriorSource,
    owner_quality: OwnerQuality,
    seed_support_level: SeedSupportLevel,
    score: int,
    config: AlignmentConfig,
) -> EdgeDecision:
    if drift_prior_source == "none" and rt_raw_delta_sec > config.preferred_rt_sec:
        return "weak_edge"
    if _drift_is_contradictory(rt_raw_delta_sec, rt_drift_corrected_delta_sec):
        return "weak_edge"
    if (
        drift_prior_source != "none"
        and rt_drift_corrected_delta_sec is not None
        and rt_drift_corrected_delta_sec <= config.preferred_rt_sec
        and owner_quality not in {"weak", "ambiguous_nearby"}
        and seed_support_level != "weak"
        and score >= 60
    ):
        return "strong_edge"
    if (
        drift_prior_source == "none"
        and rt_raw_delta_sec <= config.preferred_rt_sec
        and owner_quality not in {"weak", "ambiguous_nearby"}
        and seed_support_level != "weak"
        and score >= 55
    ):
        return "strong_edge"
    return "weak_edge"


def _drift_is_contradictory(
    rt_raw_delta_sec: float,
    rt_drift_corrected_delta_sec: float | None,
) -> bool:
    return (
        rt_drift_corrected_delta_sec is not None
        and rt_drift_corrected_delta_sec > rt_raw_delta_sec + 10.0
    )


def _evidence(
    left: SampleLocalMS1Owner,
    right: SampleLocalMS1Owner,
    *,
    decision: EdgeDecision,
    failure_reason: HardGateFailureReason | Literal[""],
    rt_raw_delta_sec: float,
    rt_drift_corrected_delta_sec: float | None,
    drift_prior_source: DriftPriorSource,
    rt_basis: RTEvidenceBasis,
    injection_order_gap: int | None,
    owner_quality: OwnerQuality,
    seed_support_level: SeedSupportLevel,
    duplicate_context: DuplicateContext,
    score: int,
    reason: str,
    failure_allows_rt_continuity: bool = False,
) -> OwnerEdgeEvidence:
    return OwnerEdgeEvidence(
        left_owner_id=left.owner_id,
        right_owner_id=right.owner_id,
        left_sample_stem=left.sample_stem,
        right_sample_stem=right.sample_stem,
        neutral_loss_tag=left.neutral_loss_tag,
        left_precursor_mz=left.precursor_mz,
        right_precursor_mz=right.precursor_mz,
        left_rt_min=left.owner_apex_rt,
        right_rt_min=right.owner_apex_rt,
        decision=decision,
        failure_reason=failure_reason,
        rt_raw_delta_sec=rt_raw_delta_sec,
        rt_drift_corrected_delta_sec=rt_drift_corrected_delta_sec,
        drift_prior_source=drift_prior_source,
        rt_basis=rt_basis,
        injection_order_gap=injection_order_gap,
        owner_quality=owner_quality,
        seed_support_level=seed_support_level,
        duplicate_context=duplicate_context,
        score=score,
        reason=reason,
        failure_allows_rt_continuity=failure_allows_rt_continuity,
    )


def _pass_reason(decision: EdgeDecision, score: int) -> str:
    return f"{decision}: score={score}"
