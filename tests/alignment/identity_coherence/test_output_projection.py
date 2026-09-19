from dataclasses import replace

import pytest

from xic_extractor.alignment.identity_coherence.candidate_matcher import (
    match_request_to_candidate,
)
from xic_extractor.alignment.identity_coherence.models import (
    CellEvidenceResult,
    IdentityDecisionSummary,
    RtCenterResult,
    SeedCandidateEvidence,
    SeedGateResult,
)
from xic_extractor.alignment.identity_coherence.output import (
    project_cell_evidence_row,
    project_control_row,
    project_decision_row,
    project_request_row,
)
from xic_extractor.alignment.identity_coherence.request_builder import (
    build_identity_coherence_request,
)
from xic_extractor.alignment.identity_coherence.schema import (
    IDENTITY_COHERENCE_CELL_EVIDENCE_COLUMNS,
    IDENTITY_COHERENCE_CONTROL_COLUMNS,
    IDENTITY_COHERENCE_DECISION_COLUMNS,
    IDENTITY_COHERENCE_REQUEST_COLUMNS,
    AreaHeightStatus,
    BaselineAuditStatus,
    CellAssessmentStatus,
    CellIdentityBasis,
    CellIdentityTier,
    DecisionReason,
    EvidenceStage,
    FragmentMatchStatus,
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


def _seed_candidate() -> SeedCandidateEvidence:
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


def _request():
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
    match = match_request_to_candidate(request, _seed_candidate())
    return replace(
        request,
        request_candidate_identity_status=match.request_candidate_identity_status,
    )


def _seed_gate() -> SeedGateResult:
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
    match = match_request_to_candidate(request, _seed_candidate())
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


def _decision() -> IdentityDecisionSummary:
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
        center=RtCenterResult(
            center_rt_min=5.0,
            center_rt_sec=300.0,
            center_decision=RtCenterDecision.RECENTERED_STABLE,
            center_candidate_count=3,
            center_drift_sec=0.0,
        ),
        coherent_fraction=0.375,
        infrastructure_blocked_sample_count=0,
        data_quality_reject_sample_count=0,
        forbidden_evidence_seen=False,
        forbidden_evidence_used=False,
    )


def _cell() -> CellEvidenceResult:
    return CellEvidenceResult(
        decision_id="DEC-1",
        identity_family_id="IDF-1",
        sample_id="RAW-2",
        candidate_id="CAND-2",
        cell_assessment_status=CellAssessmentStatus.ASSESSED,
        cell_identity_tier=CellIdentityTier.TIER1,
        cell_identity_basis=CellIdentityBasis.RT_FRAGMENT_SUPPORT,
        fragment_observation_mode="cid_neutral_loss",
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


def test_project_request_row_uses_frozen_request_schema_values():
    seed_gate = _seed_gate()
    row = project_request_row(
        replace(
            seed_gate,
            resolved_request=replace(
                seed_gate.resolved_request,
                request_builder_flags=(
                    "fragment_profile_hash_unavailable",
                    "legacy_single_tag_disagrees_with_matched_tags",
                ),
            ),
        )
    )

    assert row["request_id"] == "REQ-1"
    assert row["decision_id"] == "DEC-1"
    assert row["seed_candidate_id"] == "CAND-1"
    assert row["seed_sample"] == "RAW-1"
    assert row["fragment_observation_mode"] == "cid_neutral_loss"
    assert row["precursor_mz"] == "500"
    assert row["product_mz"] == "384"
    assert row["fragment_tags"] == "MeR;dR"
    assert row["fragment_tag_match_policy"] == "all_request_tags_supported"
    assert row["request_candidate_identity_status"] == "match"
    expected_flags = (
        "fragment_profile_hash_unavailable;"
        "legacy_single_tag_disagrees_with_matched_tags"
    )
    assert (
        row["request_builder_flags"]
        == expected_flags
    )


def test_project_request_row_rejects_complete_not_assessed_frozen_row():
    seed_gate = _seed_gate()
    bad_seed_gate = replace(
        seed_gate,
        resolved_request=replace(
            seed_gate.resolved_request,
            request_candidate_identity_status=(
                RequestCandidateIdentityStatus.NOT_ASSESSED
            ),
        ),
    )

    with pytest.raises(ValueError, match="complete request .* not_assessed"):
        project_request_row(bad_seed_gate)


def test_project_request_row_allows_incomplete_not_assessed_frozen_row():
    seed_gate = _seed_gate()
    incomplete_seed_gate = replace(
        seed_gate,
        resolved_request=replace(
            seed_gate.resolved_request,
            request_identity_completeness_status=(
                RequestIdentityCompletenessStatus.MISSING_PRODUCT_MZ
            ),
            request_candidate_identity_status=(
                RequestCandidateIdentityStatus.NOT_ASSESSED
            ),
        ),
    )

    row = project_request_row(incomplete_seed_gate)

    assert row["request_identity_completeness_status"] == "missing_product_mz"
    assert row["request_candidate_identity_status"] == "not_assessed"


def test_project_decision_row_uses_frozen_decision_schema_values():
    row = project_decision_row(_decision())

    assert row["decision_id"] == "DEC-1"
    assert row["identity_family_id"] == "IDF-1"
    assert row["decision"] == "would_primary_provisional_identity_family_support"
    assert row["min_non_seed_coherent_samples"] == "2"
    assert row["forbidden_evidence_used"] == "false"
    assert row["coherent_fraction"] == "0.375"


def test_project_decision_row_rejects_forbidden_evidence_used():
    summary = replace(_decision(), forbidden_evidence_used=True)

    with pytest.raises(ValueError, match="forbidden_evidence_used"):
        project_decision_row(summary)


def test_project_cell_evidence_row_uses_frozen_cell_schema_values():
    row = project_cell_evidence_row(_cell())

    assert row["decision_id"] == "DEC-1"
    assert row["sample_id"] == "RAW-2"
    assert row["cell_identity_tier"] == "tier1"
    assert row["fragment_match_status"] == "pass"
    assert row["fragment_tags_supported"] == "MeR;dR"
    assert row["rt_delta_center_sec"] == "3.25"
    assert row["coherent_count_contribution"] == "true"
    assert row["tier12_count_contribution"] == "true"


def test_project_control_row_is_pass_through_but_schema_limited():
    row = project_control_row(
        {
            "control_id": "CTRL-1",
            "control_type": "positive_targeted_istd",
            "control_pass": True,
            "extra": "ignored",
        }
    )

    assert row["control_id"] == "CTRL-1"
    assert row["control_type"] == "positive_targeted_istd"
    assert row["control_pass"] == "true"
    assert "extra" not in row


def test_projectors_preserve_contract_column_order():
    assert tuple(project_request_row(_seed_gate())) == (
        IDENTITY_COHERENCE_REQUEST_COLUMNS
    )
    assert tuple(project_decision_row(_decision())) == (
        IDENTITY_COHERENCE_DECISION_COLUMNS
    )
    assert tuple(project_cell_evidence_row(_cell())) == (
        IDENTITY_COHERENCE_CELL_EVIDENCE_COLUMNS
    )
    assert tuple(
        project_control_row(
            {
                "control_id": "CTRL-1",
                "control_type": "positive_targeted_istd",
            }
        )
    ) == IDENTITY_COHERENCE_CONTROL_COLUMNS
