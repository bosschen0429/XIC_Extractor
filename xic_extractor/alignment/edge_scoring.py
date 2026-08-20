from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.owner_area import owner_quantification_eligible
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner

EdgeDecision = Literal["strong_edge", "weak_edge", "blocked_edge"]
HardGateFailureReason = Literal[
    "same_sample",
    "neutral_loss_tag_mismatch",
    "precursor_mz_out_of_tolerance",
    "product_mz_out_of_tolerance",
    "observed_loss_out_of_tolerance",
    "non_detected_owner",
    "ambiguous_owner",
    "identity_conflict",
    "backfill_bridge",
]
DriftPriorSource = Literal["targeted_istd_trend", "batch_istd_trend", "none"]
SUPPORTED_DRIFT_PRIOR_SOURCES = {"targeted_istd_trend", "batch_istd_trend", "none"}
_DETECTION_LIMIT_SNR = 3.0
_QUANTITATION_LIMIT_SNR = 10.0
_DISCOVERY_MODERATE_EVIDENCE_SCORE = 40
OwnerQuality = Literal["clean", "weak", "tail_supported", "ambiguous_nearby"]
SeedSupportLevel = Literal["strong", "moderate", "weak"]
DuplicateContext = Literal["none", "same_owner_events", "tail_assignment"]


class DriftLookupProtocol(Protocol):
    @property
    def source(self) -> DriftPriorSource: ...

    def sample_delta_min(self, sample_stem: str) -> float | None: ...

    def injection_order(self, sample_stem: str) -> int | None: ...


@dataclass(frozen=True)
class OwnerEdgeEvidence:
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
    injection_order_gap: int | None
    owner_quality: OwnerQuality
    seed_support_level: SeedSupportLevel
    duplicate_context: DuplicateContext
    score: int
    reason: str


def evaluate_owner_edge(
    left: SampleLocalMS1Owner,
    right: SampleLocalMS1Owner,
    *,
    config: AlignmentConfig,
    drift_lookup: DriftLookupProtocol | None = None,
    edge_depends_on_backfill: bool = False,
    left_detected_owner: bool = True,
    right_detected_owner: bool = True,
    left_ambiguous_owner: bool = False,
    right_ambiguous_owner: bool = False,
) -> OwnerEdgeEvidence:
    rt_raw_delta_sec = abs(left.owner_apex_rt - right.owner_apex_rt) * 60.0
    corrected_delta_sec = _drift_corrected_delta_sec(left, right, drift_lookup)
    drift_source = _drift_source(drift_lookup, corrected_delta_sec)
    injection_order_gap = _injection_order_gap(left, right, drift_lookup)
    duplicate_context = _duplicate_context(left, right, config)
    owner_quality = _owner_quality(left, right, duplicate_context)
    seed_support_level = _seed_support_level(left, right, config)

    failure = _hard_gate_failure(
        left,
        right,
        config=config,
        edge_depends_on_backfill=edge_depends_on_backfill,
        left_detected_owner=left_detected_owner,
        right_detected_owner=right_detected_owner,
        left_ambiguous_owner=left_ambiguous_owner,
        right_ambiguous_owner=right_ambiguous_owner,
    )
    if failure:
        return _evidence(
            left,
            right,
            decision="blocked_edge",
            failure_reason=failure,
            rt_raw_delta_sec=rt_raw_delta_sec,
            rt_drift_corrected_delta_sec=corrected_delta_sec,
            drift_prior_source=drift_source,
            injection_order_gap=injection_order_gap,
            owner_quality=owner_quality,
            seed_support_level=seed_support_level,
            duplicate_context=duplicate_context,
            score=0,
            reason=f"blocked: {failure}",
        )

    score = _score(
        rt_raw_delta_sec=rt_raw_delta_sec,
        rt_drift_corrected_delta_sec=corrected_delta_sec,
        config=config,
        seed_support_level=seed_support_level,
        owner_quality=owner_quality,
        duplicate_context=duplicate_context,
    )
    decision = _decision(
        rt_raw_delta_sec=rt_raw_delta_sec,
        rt_drift_corrected_delta_sec=corrected_delta_sec,
        drift_prior_source=drift_source,
        owner_quality=owner_quality,
        seed_support_level=seed_support_level,
        score=score,
        config=config,
    )
    return _evidence(
        left,
        right,
        decision=decision,
        failure_reason="",
        rt_raw_delta_sec=rt_raw_delta_sec,
        rt_drift_corrected_delta_sec=corrected_delta_sec,
        drift_prior_source=drift_source,
        injection_order_gap=injection_order_gap,
        owner_quality=owner_quality,
        seed_support_level=seed_support_level,
        duplicate_context=duplicate_context,
        score=score,
        reason=_pass_reason(decision, score),
    )


def _hard_gate_failure(
    left: SampleLocalMS1Owner,
    right: SampleLocalMS1Owner,
    *,
    config: AlignmentConfig,
    edge_depends_on_backfill: bool,
    left_detected_owner: bool,
    right_detected_owner: bool,
    left_ambiguous_owner: bool,
    right_ambiguous_owner: bool,
) -> HardGateFailureReason | None:
    if edge_depends_on_backfill:
        return "backfill_bridge"
    if not left_detected_owner or not right_detected_owner:
        return "non_detected_owner"
    if left_ambiguous_owner or right_ambiguous_owner:
        return "ambiguous_owner"
    if left.identity_conflict or right.identity_conflict:
        return "identity_conflict"
    if left.sample_stem == right.sample_stem:
        return "same_sample"
    if not left.neutral_loss_tag or left.neutral_loss_tag != right.neutral_loss_tag:
        return "neutral_loss_tag_mismatch"
    if _ppm(left.precursor_mz, right.precursor_mz) > config.max_ppm:
        return "precursor_mz_out_of_tolerance"

    left_event = left.primary_identity_event
    right_event = right.primary_identity_event
    if _ppm(left_event.product_mz, right_event.product_mz) > (
        config.product_mz_tolerance_ppm
    ):
        return "product_mz_out_of_tolerance"
    if (
        _ppm(
            left_event.observed_neutral_loss_da,
            right_event.observed_neutral_loss_da,
        )
        > config.observed_loss_tolerance_ppm
    ):
        return "observed_loss_out_of_tolerance"
    return None


def _drift_corrected_delta_sec(
    left: SampleLocalMS1Owner,
    right: SampleLocalMS1Owner,
    drift_lookup: DriftLookupProtocol | None,
) -> float | None:
    if drift_lookup is None:
        return None
    left_delta = drift_lookup.sample_delta_min(left.sample_stem)
    right_delta = drift_lookup.sample_delta_min(right.sample_stem)
    if left_delta is None or right_delta is None:
        return None
    left_corrected_rt = left.owner_apex_rt - left_delta
    right_corrected_rt = right.owner_apex_rt - right_delta
    return abs(left_corrected_rt - right_corrected_rt) * 60.0


def _drift_source(
    drift_lookup: DriftLookupProtocol | None,
    corrected_delta_sec: float | None,
) -> DriftPriorSource:
    if drift_lookup is None or corrected_delta_sec is None:
        return "none"
    source = drift_lookup.source
    if source not in SUPPORTED_DRIFT_PRIOR_SOURCES:
        expected = ", ".join(sorted(SUPPORTED_DRIFT_PRIOR_SOURCES))
        raise ValueError(
            f"Unsupported drift prior source: {source}. Expected one of: {expected}"
        )
    return source


def _injection_order_gap(
    left: SampleLocalMS1Owner,
    right: SampleLocalMS1Owner,
    drift_lookup: DriftLookupProtocol | None,
) -> int | None:
    if drift_lookup is None:
        return None
    left_order = drift_lookup.injection_order(left.sample_stem)
    right_order = drift_lookup.injection_order(right.sample_stem)
    if left_order is None or right_order is None:
        return None
    return abs(left_order - right_order)


def _seed_support_level(
    left: SampleLocalMS1Owner,
    right: SampleLocalMS1Owner,
    config: AlignmentConfig,
) -> SeedSupportLevel:
    """Rate the pair on the peaks Alignment resolved, not on Discovery's score.

    Discovery's ``evidence_score`` is dominated by whether Discovery itself
    found an MS1 peak.  Every owner exists because ownership re-extracted the
    XIC and did find one, so reading that score back here judges an owner by a
    search its own construction superseded.  Rate the resolved peaks instead,
    at the detection- and quantitation-limit conventions, and keep the MS2 seed
    count, which Alignment does not re-derive.  Owners whose integration never
    recorded a baseline residual keep the Discovery-score rating.
    """
    min_seed_count = min(
        left.primary_identity_event.seed_event_count,
        right.primary_identity_event.seed_event_count,
    )
    min_snr = _min_owner_peak_snr(left, right)
    if min_snr is None:
        return _discovery_seed_support_level(left, right, config)
    if min_snr >= _QUANTITATION_LIMIT_SNR and (
        min_seed_count >= config.anchor_min_seed_events
    ):
        return "strong"
    if min_snr >= _DETECTION_LIMIT_SNR and min_seed_count >= 1:
        return "moderate"
    return "weak"


def _discovery_seed_support_level(
    left: SampleLocalMS1Owner,
    right: SampleLocalMS1Owner,
    config: AlignmentConfig,
) -> SeedSupportLevel:
    min_score = min(
        left.primary_identity_event.evidence_score,
        right.primary_identity_event.evidence_score,
    )
    min_seed_count = min(
        left.primary_identity_event.seed_event_count,
        right.primary_identity_event.seed_event_count,
    )
    if (
        min_score >= config.anchor_min_evidence_score
        and min_seed_count >= config.anchor_min_seed_events
    ):
        return "strong"
    if min_score >= _DISCOVERY_MODERATE_EVIDENCE_SCORE and min_seed_count >= 1:
        return "moderate"
    return "weak"


def _min_owner_peak_snr(
    left: SampleLocalMS1Owner,
    right: SampleLocalMS1Owner,
) -> float | None:
    left_snr = _owner_peak_snr(left)
    right_snr = _owner_peak_snr(right)
    if left_snr is None or right_snr is None:
        return None
    return min(left_snr, right_snr)


def _owner_peak_snr(owner: SampleLocalMS1Owner) -> float | None:
    return owner_peak_snr(owner)


def owner_peak_snr(owner: SampleLocalMS1Owner) -> float | None:
    """Signal-to-noise of the peak Alignment resolved for this owner."""
    integration = owner.selected_integration
    if integration is None:
        return None
    residual = integration.baseline_residual_mad
    if residual is None or residual <= 0.0:
        return None
    return integration.height_raw / residual


def owner_peak_meets_quantitation_limit(owner: SampleLocalMS1Owner) -> bool:
    """Whether the resolved peak clears the quantitation-limit convention."""
    snr = owner_peak_snr(owner)
    return (
        owner_quantification_eligible(owner)
        and snr is not None
        and snr >= _QUANTITATION_LIMIT_SNR
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


def _duplicate_context(
    left: SampleLocalMS1Owner,
    right: SampleLocalMS1Owner,
    config: AlignmentConfig,
) -> DuplicateContext:
    if (
        left.assignment_reason == "owner_tail_assignment"
        or right.assignment_reason == "owner_tail_assignment"
        or _has_tail_seed(left, config)
        or _has_tail_seed(right, config)
    ):
        return "tail_assignment"
    if left.supporting_events or right.supporting_events:
        return "same_owner_events"
    return "none"


def _has_tail_seed(owner: SampleLocalMS1Owner, config: AlignmentConfig) -> bool:
    return any(
        abs(event.seed_rt - owner.owner_apex_rt) * 60.0 > config.owner_apex_close_sec
        for event in owner.supporting_events
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
    injection_order_gap: int | None,
    owner_quality: OwnerQuality,
    seed_support_level: SeedSupportLevel,
    duplicate_context: DuplicateContext,
    score: int,
    reason: str,
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
        injection_order_gap=injection_order_gap,
        owner_quality=owner_quality,
        seed_support_level=seed_support_level,
        duplicate_context=duplicate_context,
        score=score,
        reason=reason,
    )


def _pass_reason(decision: EdgeDecision, score: int) -> str:
    return f"{decision}: score={score}"


def _ppm(left: float, right: float) -> float:
    denominator = max(abs(left), 1e-12)
    return abs(left - right) / denominator * 1_000_000.0
