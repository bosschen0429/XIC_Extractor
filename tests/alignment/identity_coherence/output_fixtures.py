from dataclasses import replace

from xic_extractor.alignment.identity_coherence.candidate_matcher import (
    match_request_to_candidate,
)
from xic_extractor.alignment.identity_coherence.models import (
    CellEvidenceResult,
    IdentityDecisionSummary,
    PrototypeWidthResult,
    RtCenterResult,
    SeedCandidateEvidence,
    SeedGateResult,
    ShapeReferenceResult,
)
from xic_extractor.alignment.identity_coherence.output import (
    IdentityCoherenceOutputRecord,
)
from xic_extractor.alignment.identity_coherence.request_builder import (
    build_identity_coherence_request,
)
from xic_extractor.alignment.identity_coherence.row_evaluator import (
    IdentityCoherenceRowResult,
)
from xic_extractor.alignment.identity_coherence.schema import (
    AreaHeightStatus,
    BaselineAuditStatus,
    CellAssessmentStatus,
    CellIdentityBasis,
    CellIdentityTier,
    DecisionReason,
    EvidenceStage,
    FragmentMatchStatus,
    FragmentObservationMode,
    IdentityDecision,
    NonRtIdentityResult,
    RequestCandidateIdentityStatus,
    RequestIdentityCompletenessStatus,
    RtCenterDecision,
    RtGateStatus,
    SeedGateClass,
    ShapeAuditStatus,
    ShapeReferenceBasis,
    ShapeStatus,
    WeakBasisReason,
    WidthStatus,
)


class CandidateLike:
    candidate_id = "CAND-1"
    sample_name = "RAW-1"
    sample_id = "RAW-1"
    precursor_mz = 500.0
    product_mz = 384.0
    observed_neutral_loss_da = 116.0
    matched_tag_names = ("MeR", "dR")
    neutral_loss_tag = "dR"


def seed_candidate() -> SeedCandidateEvidence:
    return SeedCandidateEvidence(
        candidate_id="CAND-1",
        precursor_mz=500.0,
        product_mz=384.0,
        cid_observed_loss_da=116.0,
        fragment_tags=("MeR", "dR"),
        best_seed_rt=5.0,
        ms1_scan_support_score=0.9,
        evidence_stage=EvidenceStage.PRE_BACKFILL,
    )


def seed_gate() -> SeedGateResult:
    request = build_identity_coherence_request(
        CandidateLike(),
        request_id="REQ-1",
        decision_id="DEC-1",
        precursor_tolerance_ppm=10.0,
        product_tolerance_ppm=10.0,
        cid_observed_loss_tolerance_ppm=10.0,
        fragment_profile_id="dna-cid",
        fragment_profile_hash="hash1",
    )
    match = match_request_to_candidate(request, seed_candidate())
    resolved = replace(
        request,
        request_candidate_identity_status=match.request_candidate_identity_status,
    )
    return SeedGateResult(
        resolved_request=resolved,
        seed_gate_class=SeedGateClass.COHERENT_SEED,
        seed_reject_reason=None,
        candidate_match=match,
        review_flags=(),
    )


def center() -> RtCenterResult:
    return RtCenterResult(
        center_rt_min=5.0,
        center_rt_sec=300.0,
        center_decision=RtCenterDecision.RECENTERED_STABLE,
        center_candidate_count=3,
        center_drift_sec=0.0,
    )


def decision_summary() -> IdentityDecisionSummary:
    return IdentityDecisionSummary(
        decision_id="DEC-1",
        identity_family_id="IDF-1",
        seed_candidate_id="CAND-1",
        seed_sample="RAW-1",
        seed_gate_class=SeedGateClass.COHERENT_SEED,
        request_identity_completeness_status=(
            RequestIdentityCompletenessStatus.COMPLETE
        ),
        request_candidate_identity_status=RequestCandidateIdentityStatus.MATCH,
        decision=IdentityDecision.WOULD_PRIMARY,
        decision_reason=DecisionReason.TIER1_SUPPORT.value,
        total_coherent_sample_count=3,
        non_seed_coherent_sample_count=2,
        tier12_non_seed_identity_sample_count=2,
        tier1_fragment_confirmed_sample_count=2,
        tier2_shape_supported_sample_count=0,
        tier2_seed_shape_fallback_sample_count=0,
        tier3_width_only_sample_count=0,
        min_total_coherent_samples=3,
        min_non_seed_coherent_samples=2,
        min_non_seed_tier12_identity_samples=2,
        weak_basis_reason=WeakBasisReason.NONE,
        shape_reference_basis=ShapeReferenceBasis.NONE,
        shape_reference_candidate_id="",
        prototype_width_sec=None,
        center_rt_source=RtCenterDecision.RECENTERED_STABLE.value,
        center=center(),
        coherent_fraction=0.375,
        infrastructure_blocked_sample_count=0,
        data_quality_reject_sample_count=0,
        forbidden_evidence_seen=False,
        forbidden_evidence_used=False,
    )


def cell() -> CellEvidenceResult:
    return CellEvidenceResult(
        decision_id="DEC-1",
        identity_family_id="IDF-1",
        sample_id="RAW-2",
        candidate_id="CAND-2",
        cell_assessment_status=CellAssessmentStatus.ASSESSED,
        cell_identity_tier=CellIdentityTier.TIER1,
        cell_identity_basis=CellIdentityBasis.RT_FRAGMENT_SUPPORT,
        fragment_observation_mode=FragmentObservationMode.CID_NEUTRAL_LOSS,
        fragment_match_status=FragmentMatchStatus.PASS,
        fragment_tags_supported=("MeR", "dR"),
        rt_delta_center_sec=3.25,
        rt_gate_status=RtGateStatus.PASS,
        shape_status=ShapeStatus.NOT_ASSESSED,
        shape_similarity_cosine=None,
        shape_reference_basis=ShapeReferenceBasis.NONE,
        shape_reference_candidate_id="",
        shape_fallback_used=False,
        shape_audit_status=ShapeAuditStatus.NOT_ASSESSED,
        width_status=WidthStatus.NOT_ASSESSED,
        width_ratio_to_prototype=None,
        baseline_audit_status=BaselineAuditStatus.NOT_ASSESSED,
        area_height_status=AreaHeightStatus.PASS,
        non_rt_identity_result=NonRtIdentityResult.PASS,
        coherent_count_contribution=True,
        tier12_count_contribution=True,
        blocked_reason="",
        data_quality_reason="",
        forbidden_evidence_seen=False,
    )


def output_record() -> IdentityCoherenceOutputRecord:
    row_result = IdentityCoherenceRowResult(
        center=center(),
        prototype_width=PrototypeWidthResult(
            width_status=WidthStatus.NOT_ASSESSED,
            prototype_width_sec=None,
            candidate_count=0,
            non_seed_candidate_count=0,
            width_candidate_ids=(),
        ),
        shape_reference=ShapeReferenceResult(
            shape_reference_basis=ShapeReferenceBasis.NONE,
            shape_reference_candidate_id="",
            normalized_intensity=(),
            candidate_count=0,
            non_seed_candidate_count=0,
            seed_fallback_used=False,
        ),
        cells=(cell(),),
        decision=decision_summary(),
    )
    return IdentityCoherenceOutputRecord(
        seed_gate=seed_gate(),
        row_result=row_result,
    )
