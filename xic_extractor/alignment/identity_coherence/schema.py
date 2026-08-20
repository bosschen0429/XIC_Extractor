from __future__ import annotations

from enum import StrEnum


class RequestIdentityCompletenessStatus(StrEnum):
    COMPLETE = "complete"
    MISSING_FRAGMENT_OBSERVATION_MODE = "missing_fragment_observation_mode"
    MISSING_PRECURSOR_MZ = "missing_precursor_mz"
    MISSING_PRODUCT_MZ = "missing_product_mz"
    MISSING_FRAGMENT_TAGS = "missing_fragment_tags"
    MISSING_TOLERANCE = "missing_tolerance"
    MISSING_MODE_SPECIFIC_CONSTRAINT = "missing_mode_specific_constraint"


class RequestCandidateIdentityStatus(StrEnum):
    NOT_ASSESSED = "not_assessed"
    MATCH = "match"
    MISSING_DISCOVERY_CANDIDATE_JOIN = "missing_discovery_candidate_join"
    MISSING_DIAGNOSTIC_FRAGMENT_EVIDENCE = "missing_diagnostic_fragment_evidence"
    REQUEST_CANDIDATE_IDENTITY_MISMATCH = "request_candidate_identity_mismatch"
    UNSUPPORTED_FRAGMENT_OBSERVATION_MODE = "unsupported_fragment_observation_mode"


class FragmentObservationMode(StrEnum):
    CID_NEUTRAL_LOSS = "cid_neutral_loss"


class FragmentTagMatchPolicy(StrEnum):
    ALL_REQUEST_TAGS_SUPPORTED = "all_request_tags_supported"


class ControlType(StrEnum):
    POSITIVE_TARGETED_ISTD = "positive_targeted_istd"
    IDENTITY_DECOY = "identity_decoy"


class ControlStatus(StrEnum):
    ASSESSED = "assessed"
    NOT_ASSESSED = "not_assessed"
    UNMAPPED = "unmapped"
    AMBIGUOUS_MAPPING = "ambiguous_mapping"


class PositiveControlMappingStatus(StrEnum):
    MAPPED = "mapped"
    UNMAPPED = "unmapped"
    AMBIGUOUS_MAPPING = "ambiguous_mapping"
    NOT_APPLICABLE = "not_applicable"


class DecoyGenerationMethod(StrEnum):
    RT_SHIFT = "rt_shift"
    MZ_SHIFT = "mz_shift"
    FRAGMENT_TAG_SHUFFLE = "fragment_tag_shuffle"


class EvidenceStage(StrEnum):
    PRE_BACKFILL = "pre_backfill"
    BACKFILL_ONLY = "backfill_only"
    POST_BACKFILL = "post_backfill"


class SeedGateClass(StrEnum):
    COHERENT_SEED = "coherent_seed"
    REVIEW_ONLY_SEED_GATE_FAILED = "review_only_seed_gate_failed"
    BLOCKED_SEED = "blocked_seed"


class SeedRejectReason(StrEnum):
    MISSING_REQUEST_IDENTITY_CONSTRAINT = "missing_request_identity_constraint"
    NO_QUANTIFIABLE_OWNER = "no_quantifiable_owner"
    MISSING_DISCOVERY_CANDIDATE_JOIN = "missing_discovery_candidate_join"
    MISSING_DIAGNOSTIC_FRAGMENT_EVIDENCE = "missing_diagnostic_fragment_evidence"
    AMBIGUOUS_OWNER = "ambiguous_owner"
    DUPLICATE_LOSER = "duplicate_loser"
    BACKFILL_ONLY_EVIDENCE = "backfill_only_evidence"
    NONFINITE_PEAK = "nonfinite_peak"
    SEED_RT_OUTSIDE_OWNER_PEAK = "seed_rt_outside_owner_peak"
    LOW_MS1_SCAN_SUPPORT = "low_ms1_scan_support"
    REQUEST_CANDIDATE_IDENTITY_MISMATCH = "request_candidate_identity_mismatch"
    UNSUPPORTED_FRAGMENT_OBSERVATION_MODE = "unsupported_fragment_observation_mode"
    MULTI_SEED_UNRESOLVED = "multi_seed_unresolved"


class IdentityDecision(StrEnum):
    WOULD_PRIMARY = "would_primary_provisional_identity_family_support"
    REVIEW_ONLY_SEED_GATE_FAILED = "review_only_seed_gate_failed"
    REVIEW_ONLY_RT_ONLY_SUPPORT = "review_only_rt_only_support"
    REVIEW_ONLY_INSUFFICIENT_SUPPORT = "review_only_insufficient_support"
    REVIEW_ONLY_CENTER_UNSTABLE = "review_only_center_unstable"
    REVIEW_ONLY_WEAK_BASIS_TIER3_ONLY = "review_only_weak_basis_tier3_only"
    REVIEW_ONLY_WEAK_BASIS_SINGLE_TIER12_PLUS_TIER3 = (
        "review_only_weak_basis_single_tier12_plus_tier3"
    )
    REVIEW_ONLY_MULTI_SEED_UNRESOLVED = (
        "review_only_multi_seed_unresolved"
    )
    BLOCKED_INFRASTRUCTURE = "blocked_infrastructure"


class WeakBasisReason(StrEnum):
    NONE = "none"
    TIER3_ONLY = "tier3_only"
    SINGLE_TIER12_PLUS_TIER3 = "single_tier12_plus_tier3"
    SEED_SHAPE_FALLBACK_ONLY = "seed_shape_fallback_only"
    RT_ONLY = "rt_only"


class RtCenterDecision(StrEnum):
    SEED_ANCHORED = "seed_anchored"
    RECENTERED_STABLE = "recentered_stable"
    CENTER_UNSTABLE_REVIEW_ONLY = "center_unstable_review_only"


class CellAssessmentStatus(StrEnum):
    ASSESSED = "assessed"
    BLOCKED = "blocked"
    DATA_QUALITY_REJECT = "data_quality_reject"
    NOT_ASSESSED = "not_assessed"


class CellIdentityTier(StrEnum):
    TIER1 = "tier1"
    TIER2 = "tier2"
    TIER3 = "tier3"
    RT_ONLY = "rt_only"
    BLOCKED = "blocked"
    DATA_QUALITY = "data_quality"


class CellIdentityBasis(StrEnum):
    RT_FRAGMENT_SUPPORT = "rt_fragment_support"
    RT_SHAPE_SIMILARITY = "rt_shape_similarity"
    RT_PROTOTYPE_WIDTH = "rt_prototype_width"
    NONE = "none"


class FragmentMatchStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    AMBIGUOUS = "ambiguous"
    NOT_ASSESSED = "not_assessed"


class RtGateStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    NOT_ASSESSED = "not_assessed"


class ShapeStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    LOW_POINTS = "low_points"
    ZERO_SIGNAL = "zero_signal"
    NOT_ASSESSED = "not_assessed"


class ShapeReferenceBasis(StrEnum):
    TIER1_SUPPORTED_MEDOID = "tier1_supported_medoid"
    MORPHOLOGY_RT_MEDOID = "morphology_rt_medoid"
    SEED_FALLBACK = "seed_fallback"
    NONE = "none"


class ShapeAuditStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    SHOULDER = "shoulder"
    BIMODAL = "bimodal"
    COELUTION = "coelution"
    SATURATED = "saturated"
    CLIPPED = "clipped"
    UNAVAILABLE = "unavailable"
    NOT_ASSESSED = "not_assessed"


class WidthStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    NOT_ASSESSED = "not_assessed"


class BaselineAuditStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNAVAILABLE = "unavailable"
    NOT_ASSESSED = "not_assessed"


class AreaHeightStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    NOT_ASSESSED = "not_assessed"


class NonRtIdentityResult(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    NOT_ASSESSED = "not_assessed"
    BLOCKED = "blocked"


class CellBlockedReason(StrEnum):
    BACKFILL_ONLY_EVIDENCE = "backfill_only_evidence"


class CellDataQualityReason(StrEnum):
    INVALID_PEAK_MORPHOLOGY = "invalid_peak_morphology"


class DecisionReason(StrEnum):
    TIER1_SUPPORT = "tier1_support"
    TIER2_SHAPE_SUPPORT = "tier2_shape_support"


IDENTITY_COHERENCE_REQUEST_COLUMNS: tuple[str, ...] = (
    "request_id",
    "decision_id",
    "seed_candidate_id",
    "seed_sample",
    "fragment_observation_mode",
    "precursor_mz",
    "product_mz",
    "fragment_tags",
    "fragment_tag_match_policy",
    "fragment_profile_id",
    "fragment_profile_hash",
    "precursor_tolerance_ppm",
    "product_tolerance_ppm",
    "cid_observed_loss_da",
    "cid_observed_loss_tolerance_ppm",
    "request_identity_completeness_status",
    "request_candidate_identity_status",
    "precursor_error_ppm",
    "product_error_ppm",
    "cid_observed_loss_error_ppm",
    "cid_observed_loss_error_da",
    "request_builder_flags",
)

IDENTITY_COHERENCE_DECISION_COLUMNS: tuple[str, ...] = (
    "decision_id",
    "identity_family_id",
    "seed_candidate_id",
    "seed_sample",
    "seed_gate_class",
    "decision",
    "decision_reason",
    "request_identity_completeness_status",
    "request_candidate_identity_status",
    "total_coherent_sample_count",
    "non_seed_coherent_sample_count",
    "tier12_non_seed_identity_sample_count",
    "tier1_fragment_confirmed_sample_count",
    "tier2_shape_supported_sample_count",
    "tier2_seed_shape_fallback_sample_count",
    "tier3_width_only_sample_count",
    "min_total_coherent_samples",
    "min_non_seed_coherent_samples",
    "min_non_seed_tier12_identity_samples",
    "weak_basis_reason",
    "shape_reference_basis",
    "shape_reference_candidate_id",
    "prototype_width_sec",
    "center_rt_sec",
    "center_rt_source",
    "coherent_fraction",
    "infrastructure_blocked_sample_count",
    "data_quality_reject_sample_count",
    "forbidden_evidence_used",
)

IDENTITY_COHERENCE_CELL_EVIDENCE_COLUMNS: tuple[str, ...] = (
    "decision_id",
    "identity_family_id",
    "sample_id",
    "candidate_id",
    "cell_assessment_status",
    "cell_identity_tier",
    "cell_identity_basis",
    "fragment_observation_mode",
    "fragment_match_status",
    "fragment_tags_supported",
    "rt_delta_center_sec",
    "rt_gate_status",
    "shape_status",
    "shape_similarity_cosine",
    "shape_reference_basis",
    "shape_reference_candidate_id",
    "shape_fallback_used",
    "shape_audit_status",
    "width_status",
    "width_ratio_to_prototype",
    "baseline_audit_status",
    "area_height_status",
    "non_rt_identity_result",
    "coherent_count_contribution",
    "tier12_count_contribution",
    "blocked_reason",
    "data_quality_reason",
    "forbidden_evidence_seen",
)

IDENTITY_COHERENCE_CONTROL_COLUMNS: tuple[str, ...] = (
    "control_id",
    "control_type",
    "control_name",
    "decision_id",
    "identity_family_id",
    "seed_candidate_id",
    "control_status",
    "control_expected_behavior",
    "control_observed_behavior",
    "control_pass",
    "control_failure_reason",
    "fragment_observation_mode",
    "decoy_generation_method",
    "decoy_source_request_id",
    "decoy_shift_value",
    "decoy_identity_constraint_changed",
    "positive_control_mapping_status",
    "positive_control_target_name",
    "positive_control_target_mz",
    "positive_control_target_rt_sec",
    "positive_control_mapping_error_ppm",
    "positive_control_mapping_delta_rt_sec",
    "control_notes",
)
