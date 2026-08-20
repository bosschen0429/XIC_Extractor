from __future__ import annotations

import math
from dataclasses import dataclass, field

from .schema import (
    AreaHeightStatus,
    BaselineAuditStatus,
    CellAssessmentStatus,
    CellIdentityBasis,
    CellIdentityTier,
    EvidenceStage,
    FragmentMatchStatus,
    FragmentObservationMode,
    FragmentTagMatchPolicy,
    IdentityDecision,
    NonRtIdentityResult,
    RequestCandidateIdentityStatus,
    RequestIdentityCompletenessStatus,
    RtCenterDecision,
    RtGateStatus,
    SeedGateClass,
    SeedRejectReason,
    ShapeAuditStatus,
    ShapeReferenceBasis,
    ShapeStatus,
    WeakBasisReason,
    WidthStatus,
)

_TRACE_RESULT_STATUSES = frozenset(
    {
        "pass",
        "blocked_infrastructure",
        "data_quality_reject",
        "not_assessed",
    }
)
_TRACE_RESULT_STATUSES_REQUIRING_REASON = frozenset(
    {
        "blocked_infrastructure",
        "data_quality_reject",
    }
)


def _require_non_empty_text(value: object, field_name: str) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        raise ValueError(f"{field_name} must be non-empty")
    return text


def _require_finite_positive(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be finite positive")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric <= 0:
        raise ValueError(f"{field_name} must be finite positive")
    return numeric


def _require_finite_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be finite")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{field_name} must be finite")
    return numeric


def _require_finite_nonnegative(value: object, field_name: str) -> float:
    numeric = _require_finite_number(value, field_name)
    if numeric < 0:
        raise ValueError(f"{field_name} must be nonnegative")
    return numeric


def _require_nonnegative_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a nonnegative integer")
    return value


@dataclass(frozen=True)
class CidNeutralLossConstraint:
    cid_observed_loss_da: float | None
    cid_observed_loss_tolerance_ppm: float | None


@dataclass(frozen=True)
class FragmentIdentity:
    fragment_observation_mode: FragmentObservationMode | None
    precursor_mz: float | None
    product_mz: float | None
    fragment_tags: tuple[str, ...]
    fragment_tag_match_policy: FragmentTagMatchPolicy
    precursor_tolerance_ppm: float | None
    product_tolerance_ppm: float | None
    fragment_profile_id: str
    fragment_profile_hash: str
    mode_constraint: CidNeutralLossConstraint


@dataclass(frozen=True)
class IdentityCoherenceRequest:
    request_id: str
    decision_id: str
    seed_candidate_id: str
    seed_sample: str | None
    identity: FragmentIdentity
    request_identity_completeness_status: RequestIdentityCompletenessStatus
    request_candidate_identity_status: RequestCandidateIdentityStatus
    request_builder_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class CandidateIdentityMatch:
    request_candidate_identity_status: RequestCandidateIdentityStatus
    precursor_error_ppm: float | None
    product_error_ppm: float | None
    cid_observed_loss_error_ppm: float | None
    cid_observed_loss_error_da: float | None
    missing_fields: tuple[str, ...] = ()
    mismatch_fields: tuple[str, ...] = ()
    fragment_tags_supported: tuple[str, ...] = ()


@dataclass(frozen=True)
class SeedCandidateEvidence:
    candidate_id: str
    precursor_mz: float | None
    product_mz: float | None
    cid_observed_loss_da: float | None
    fragment_tags: tuple[str, ...]
    best_seed_rt: float | None
    ms1_scan_support_score: float | None
    evidence_stage: EvidenceStage = EvidenceStage.PRE_BACKFILL


@dataclass(frozen=True)
class SeedGateConfig:
    min_ms1_scan_support_score: float = 0.50
    require_seed_rt_inside_owner_peak: bool = True


@dataclass(frozen=True)
class SeedGateResult:
    resolved_request: IdentityCoherenceRequest
    seed_gate_class: SeedGateClass
    seed_reject_reason: SeedRejectReason | None
    candidate_match: CandidateIdentityMatch
    review_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class PromotionConfig:
    min_total_coherent_samples: int = 3
    min_non_seed_coherent_samples: int = 2
    min_non_seed_tier12_identity_samples: int = 2


@dataclass(frozen=True)
class RtConfig:
    max_rt_sec: float = 180.0
    preferred_rt_sec: float = 60.0
    seed_center_candidate_sec: float = 30.0
    max_center_drift_sec: float = 30.0


@dataclass(frozen=True)
class ShapeConfig:
    min_points: int = 7
    resample_points: int = 25
    min_cosine: float = 0.85
    prototype_min_candidates: int = 3
    prototype_min_non_seed_candidates: int = 2
    allow_seed_shape_fallback: bool = True
    allow_morphology_rt_medoid: bool = True


@dataclass(frozen=True)
class WidthConfig:
    prototype_min_candidates: int = 3
    min_ratio: float = 0.50
    max_ratio: float = 2.00


@dataclass(frozen=True)
class CandidateTrace:
    rt_min: tuple[float, ...]
    intensity: tuple[float, ...]
    shape_audit_status: ShapeAuditStatus = ShapeAuditStatus.UNAVAILABLE

    def __post_init__(self) -> None:
        if len(self.rt_min) != len(self.intensity):
            raise ValueError("rt_min and intensity must have the same length")


@dataclass(frozen=True)
class EngineeringConfig:
    max_infrastructure_blocked_fraction: float = 0.05
    max_projected_full_cohort_identity_xic_requests: int | None = None

    def __post_init__(self) -> None:
        blocked_fraction = _require_finite_nonnegative(
            self.max_infrastructure_blocked_fraction,
            "max_infrastructure_blocked_fraction",
        )
        if blocked_fraction > 1.0:
            raise ValueError("max_infrastructure_blocked_fraction must be <= 1")
        object.__setattr__(
            self,
            "max_infrastructure_blocked_fraction",
            blocked_fraction,
        )

        budget = self.max_projected_full_cohort_identity_xic_requests
        if budget is None:
            return
        if isinstance(budget, bool) or not isinstance(budget, int) or budget < 0:
            raise ValueError(
                "max_projected_full_cohort_identity_xic_requests must be nonnegative"
            )


@dataclass(frozen=True)
class IdentityCoherenceTraceRequest:
    decision_id: str
    request_id: str
    sample_id: str
    candidate_id: str
    precursor_mz: float
    ppm_tolerance: float
    rt_min: float
    rt_max: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "decision_id",
            _require_non_empty_text(self.decision_id, "decision_id"),
        )
        object.__setattr__(
            self,
            "request_id",
            _require_non_empty_text(self.request_id, "request_id"),
        )
        object.__setattr__(
            self,
            "sample_id",
            _require_non_empty_text(self.sample_id, "sample_id"),
        )
        object.__setattr__(
            self,
            "candidate_id",
            _require_non_empty_text(self.candidate_id, "candidate_id"),
        )
        object.__setattr__(
            self,
            "precursor_mz",
            _require_finite_positive(self.precursor_mz, "precursor_mz"),
        )
        object.__setattr__(
            self,
            "ppm_tolerance",
            _require_finite_positive(self.ppm_tolerance, "ppm_tolerance"),
        )
        rt_min = _require_finite_number(self.rt_min, "rt_min")
        rt_max = _require_finite_number(self.rt_max, "rt_max")
        if rt_min > rt_max:
            raise ValueError("rt_min must be <= rt_max")
        object.__setattr__(self, "rt_min", rt_min)
        object.__setattr__(self, "rt_max", rt_max)


@dataclass(frozen=True)
class IdentityCoherenceTraceResult:
    request: IdentityCoherenceTraceRequest
    trace: CandidateTrace | None
    status: str
    blocked_reason: str = ""
    raw_xic_request_count: int = 0
    raw_chromatogram_call_count: int = 0
    xic_point_count: int = 0
    elapsed_sec: float = 0.0

    def __post_init__(self) -> None:
        status = _require_non_empty_text(self.status, "status")
        if status not in _TRACE_RESULT_STATUSES:
            raise ValueError(f"unsupported trace result status: {status}")
        object.__setattr__(self, "status", status)
        blocked_reason = (
            "" if self.blocked_reason is None else str(self.blocked_reason).strip()
        )
        if status == "pass" and blocked_reason:
            raise ValueError("pass status cannot have blocked_reason")
        if status in _TRACE_RESULT_STATUSES_REQUIRING_REASON and not blocked_reason:
            raise ValueError(f"{status} requires blocked_reason")
        object.__setattr__(
            self,
            "blocked_reason",
            blocked_reason,
        )
        object.__setattr__(
            self,
            "raw_xic_request_count",
            _require_nonnegative_int(
                self.raw_xic_request_count,
                "raw_xic_request_count",
            ),
        )
        object.__setattr__(
            self,
            "raw_chromatogram_call_count",
            _require_nonnegative_int(
                self.raw_chromatogram_call_count,
                "raw_chromatogram_call_count",
            ),
        )
        point_count = _require_nonnegative_int(
            self.xic_point_count,
            "xic_point_count",
        )
        if self.trace is None and point_count != 0:
            raise ValueError("xic_point_count must be 0 when trace is None")
        if self.trace is not None and point_count != len(self.trace.rt_min):
            raise ValueError("xic_point_count must equal trace length")
        object.__setattr__(self, "xic_point_count", point_count)
        elapsed = _require_finite_number(self.elapsed_sec, "elapsed_sec")
        if elapsed < 0:
            raise ValueError("elapsed_sec must be nonnegative")
        object.__setattr__(self, "elapsed_sec", elapsed)


@dataclass(frozen=True)
class IdentityCoherenceResult:
    request: IdentityCoherenceRequest
    decision: IdentityDecisionSummary
    cells: tuple[CellEvidenceResult, ...]

    def __post_init__(self) -> None:
        if self.request.decision_id != self.decision.decision_id:
            raise ValueError("decision_id mismatch between request and decision")
        if self.request.seed_candidate_id != self.decision.seed_candidate_id:
            raise ValueError(
                "seed_candidate_id mismatch between request and decision"
            )
        if self.request.seed_sample != self.decision.seed_sample:
            raise ValueError("seed_sample mismatch between request and decision")
        for cell in self.cells:
            if cell.decision_id != self.decision.decision_id:
                raise ValueError("decision_id mismatch between cell and decision")
            if cell.identity_family_id != self.decision.identity_family_id:
                raise ValueError(
                    "identity_family_id mismatch between cell and decision"
                )


@dataclass(frozen=True)
class IdentityCoherenceConfig:
    seed_gate: SeedGateConfig = field(default_factory=SeedGateConfig)
    promotion: PromotionConfig = field(default_factory=PromotionConfig)
    rt: RtConfig = field(default_factory=RtConfig)
    shape: ShapeConfig = field(default_factory=ShapeConfig)
    width: WidthConfig = field(default_factory=WidthConfig)
    engineering: EngineeringConfig = field(default_factory=EngineeringConfig)


@dataclass(frozen=True)
class CellCandidateEvidence:
    sample_id: str
    candidate_evidence: SeedCandidateEvidence
    apex_rt: float | None
    peak_start_rt: float | None
    peak_end_rt: float | None
    area: float | None
    height: float | None
    point_count: int | None = None
    owner_assignment_status: str = "primary"
    duplicate_loser: bool = False
    forbidden_evidence_seen: bool = False
    blocked_reason: str = ""
    data_quality_reason: str = ""
    trace: CandidateTrace | None = None


@dataclass(frozen=True)
class PrototypeWidthResult:
    width_status: WidthStatus
    prototype_width_sec: float | None
    candidate_count: int
    non_seed_candidate_count: int
    width_candidate_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class WidthAssessmentResult:
    width_status: WidthStatus
    width_ratio_to_prototype: float | None


@dataclass(frozen=True)
class ShapeReferenceResult:
    shape_reference_basis: ShapeReferenceBasis
    shape_reference_candidate_id: str
    normalized_intensity: tuple[float, ...]
    candidate_count: int
    non_seed_candidate_count: int
    seed_fallback_used: bool = False


@dataclass(frozen=True)
class ShapeComparisonResult:
    shape_status: ShapeStatus
    shape_similarity_cosine: float | None
    shape_reference_basis: ShapeReferenceBasis
    shape_reference_candidate_id: str
    shape_fallback_used: bool
    shape_audit_status: ShapeAuditStatus


@dataclass(frozen=True)
class RtCenterResult:
    center_rt_min: float
    center_rt_sec: float
    center_decision: RtCenterDecision
    center_candidate_count: int
    center_drift_sec: float


@dataclass(frozen=True)
class CellEvidenceResult:
    decision_id: str
    identity_family_id: str
    sample_id: str
    candidate_id: str
    cell_assessment_status: CellAssessmentStatus
    cell_identity_tier: CellIdentityTier
    cell_identity_basis: CellIdentityBasis
    fragment_observation_mode: FragmentObservationMode
    fragment_match_status: FragmentMatchStatus
    fragment_tags_supported: tuple[str, ...]
    rt_delta_center_sec: float | None
    rt_gate_status: RtGateStatus
    shape_status: ShapeStatus
    shape_similarity_cosine: float | None
    shape_reference_basis: ShapeReferenceBasis
    shape_reference_candidate_id: str
    shape_fallback_used: bool
    shape_audit_status: ShapeAuditStatus
    width_status: WidthStatus
    width_ratio_to_prototype: float | None
    baseline_audit_status: BaselineAuditStatus
    area_height_status: AreaHeightStatus
    non_rt_identity_result: NonRtIdentityResult
    coherent_count_contribution: bool
    tier12_count_contribution: bool
    blocked_reason: str
    data_quality_reason: str
    forbidden_evidence_seen: bool


@dataclass(frozen=True)
class IdentityDecisionSummary:
    decision_id: str
    identity_family_id: str
    seed_candidate_id: str
    seed_sample: str | None
    seed_gate_class: SeedGateClass
    request_identity_completeness_status: RequestIdentityCompletenessStatus
    request_candidate_identity_status: RequestCandidateIdentityStatus
    decision: IdentityDecision
    decision_reason: str
    total_coherent_sample_count: int
    non_seed_coherent_sample_count: int
    tier12_non_seed_identity_sample_count: int
    tier1_fragment_confirmed_sample_count: int
    tier2_shape_supported_sample_count: int
    tier2_seed_shape_fallback_sample_count: int
    tier3_width_only_sample_count: int
    min_total_coherent_samples: int
    min_non_seed_coherent_samples: int
    min_non_seed_tier12_identity_samples: int
    weak_basis_reason: WeakBasisReason
    shape_reference_basis: ShapeReferenceBasis
    shape_reference_candidate_id: str
    prototype_width_sec: float | None
    center_rt_source: str
    center: RtCenterResult
    coherent_fraction: float | None
    infrastructure_blocked_sample_count: int
    data_quality_reject_sample_count: int
    forbidden_evidence_seen: bool
    forbidden_evidence_used: bool
