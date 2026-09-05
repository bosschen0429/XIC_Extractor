from dataclasses import replace

import pytest

from xic_extractor.alignment.identity_coherence.models import (
    CandidateTrace,
    CellCandidateEvidence,
    IdentityCoherenceConfig,
    SeedCandidateEvidence,
    ShapeReferenceResult,
)
from xic_extractor.alignment.identity_coherence.schema import (
    CellIdentityTier,
    EvidenceStage,
    ShapeAuditStatus,
    ShapeReferenceBasis,
    ShapeStatus,
    WidthStatus,
)
from xic_extractor.alignment.identity_coherence.shape import (
    compare_shape_to_reference,
    create_seed_shape_reference,
    estimate_shape_reference,
    normalize_trace_for_shape,
)


def _seed_candidate(
    candidate_id: str,
    *,
    evidence_stage: EvidenceStage | str = EvidenceStage.PRE_BACKFILL,
) -> SeedCandidateEvidence:
    return SeedCandidateEvidence(
        candidate_id=candidate_id,
        precursor_mz=500.0,
        product_mz=384.0,
        cid_observed_loss_da=116.0,
        fragment_tags=("dR", "MeR"),
        best_seed_rt=7.80,
        ms1_scan_support_score=0.75,
        evidence_stage=evidence_stage,
    )


def _trace(
    intensities: tuple[float, ...],
    *,
    start: float = 7.75,
    end: float = 7.85,
    audit: ShapeAuditStatus | str = ShapeAuditStatus.PASS,
) -> CandidateTrace:
    count = len(intensities)
    step = (end - start) / (count - 1)
    return CandidateTrace(
        rt_min=tuple(start + step * i for i in range(count)),
        intensity=intensities,
        shape_audit_status=audit,
    )


def _candidate(
    candidate_id: str,
    *,
    sample_id: str = "S2",
    intensities: tuple[float, ...] = (0, 1, 3, 7, 10, 7, 3, 1, 0),
    start: float = 7.75,
    apex: float = 7.80,
    end: float = 7.85,
    point_count: int | None = 9,
    evidence_stage: EvidenceStage | str = EvidenceStage.PRE_BACKFILL,
    duplicate_loser: bool = False,
    owner_assignment_status: str = "primary",
    blocked_reason: str = "",
    data_quality_reason: str = "",
    audit: ShapeAuditStatus | str = ShapeAuditStatus.PASS,
) -> CellCandidateEvidence:
    return CellCandidateEvidence(
        sample_id=sample_id,
        candidate_evidence=_seed_candidate(candidate_id, evidence_stage=evidence_stage),
        apex_rt=apex,
        peak_start_rt=start,
        peak_end_rt=end,
        area=100.0,
        height=20.0,
        point_count=point_count,
        owner_assignment_status=owner_assignment_status,
        duplicate_loser=duplicate_loser,
        blocked_reason=blocked_reason,
        data_quality_reason=data_quality_reason,
        trace=_trace(intensities, start=start, end=end, audit=audit),
    )


def test_normalize_trace_for_shape_uses_candidate_boundaries_and_unit_norm():
    config = IdentityCoherenceConfig()
    candidate = _candidate(
        "C1",
        intensities=(100, 101, 103, 107, 110, 107, 103, 101, 100),
    )

    normalized = normalize_trace_for_shape(candidate, config)

    assert normalized.shape_status is ShapeStatus.PASS
    assert len(normalized.normalized_intensity) == config.shape.resample_points
    assert max(normalized.normalized_intensity) > 0.0
    assert pytest.approx(
        sum(value * value for value in normalized.normalized_intensity),
        rel=1e-12,
    ) == 1.0


def test_normalize_trace_for_shape_rejects_low_raw_point_count():
    config = IdentityCoherenceConfig()
    candidate = _candidate("LOW", intensities=(0, 1, 2, 1, 0), point_count=5)

    normalized = normalize_trace_for_shape(candidate, config)

    assert normalized.shape_status is ShapeStatus.LOW_POINTS
    assert normalized.normalized_intensity == ()


def test_normalize_trace_for_shape_rejects_zero_signal_after_baseline_subtraction():
    config = IdentityCoherenceConfig()
    candidate = _candidate("ZERO", intensities=(5, 5, 5, 5, 5, 5, 5))

    normalized = normalize_trace_for_shape(candidate, config)

    assert normalized.shape_status is ShapeStatus.ZERO_SIGNAL
    assert normalized.normalized_intensity == ()


def test_normalize_trace_for_shape_fails_generic_fail_audit():
    config = IdentityCoherenceConfig()
    normalized = normalize_trace_for_shape(
        _candidate("BAD", audit=ShapeAuditStatus.FAIL),
        config,
    )

    assert normalized.shape_status is ShapeStatus.FAIL
    assert normalized.shape_audit_status is ShapeAuditStatus.FAIL


def test_estimate_shape_reference_prefers_tier1_supported_medoid_on_tie():
    config = IdentityCoherenceConfig()
    result = estimate_shape_reference(
        (
            _candidate("TIER1", sample_id="S2"),
            _candidate("MORPH", sample_id="S3"),
            _candidate("OTHER", sample_id="S4"),
        ),
        config,
        seed_sample_id="S1",
        tier_by_candidate_id={
            "TIER1": CellIdentityTier.TIER1,
            "MORPH": CellIdentityTier.RT_ONLY,
            "OTHER": CellIdentityTier.RT_ONLY,
        },
        center_rt_min=7.80,
    )

    assert result.shape_reference_basis is ShapeReferenceBasis.TIER1_SUPPORTED_MEDOID
    assert result.shape_reference_candidate_id == "TIER1"
    assert result.candidate_count == 3
    assert result.non_seed_candidate_count == 3


def test_estimate_shape_reference_allows_morphology_rt_medoid_when_no_tier1():
    config = IdentityCoherenceConfig()
    result = estimate_shape_reference(
        (
            _candidate("CELL_REFERENCE", sample_id="S2"),
            _candidate("BACKFILL_DECISION", sample_id="S3"),
            _candidate("IDENTITY_SETTLEMENT", sample_id="S4"),
        ),
        config,
        seed_sample_id="S1",
        tier_by_candidate_id={},
        center_rt_min=7.80,
    )

    assert result.shape_reference_basis is ShapeReferenceBasis.MORPHOLOGY_RT_MEDOID
    assert result.shape_reference_candidate_id in {
        "CELL_REFERENCE",
        "BACKFILL_DECISION",
        "IDENTITY_SETTLEMENT",
    }


def test_estimate_shape_reference_respects_morphology_rt_medoid_opt_out():
    config = IdentityCoherenceConfig(
        shape=replace(
            IdentityCoherenceConfig().shape,
            allow_morphology_rt_medoid=False,
        )
    )
    result = estimate_shape_reference(
        (
            _candidate("CELL_REFERENCE", sample_id="S2"),
            _candidate("BACKFILL_DECISION", sample_id="S3"),
            _candidate("IDENTITY_SETTLEMENT", sample_id="S4"),
        ),
        config,
        seed_sample_id="S1",
        tier_by_candidate_id={},
        center_rt_min=7.80,
    )

    assert result.shape_reference_basis is ShapeReferenceBasis.NONE
    assert result.normalized_intensity == ()
    assert result.candidate_count == 3
    assert result.non_seed_candidate_count == 3


def test_estimate_shape_reference_accepts_string_tier_map_values():
    config = IdentityCoherenceConfig()
    result = estimate_shape_reference(
        (
            _candidate("Z_TIER1", sample_id="S2"),
            _candidate("A_MORPH", sample_id="S3"),
            _candidate("B_MORPH", sample_id="S4"),
        ),
        config,
        seed_sample_id="S1",
        tier_by_candidate_id={"Z_TIER1": "tier1"},
        center_rt_min=7.80,
    )

    assert result.shape_reference_basis is ShapeReferenceBasis.TIER1_SUPPORTED_MEDOID
    assert result.shape_reference_candidate_id == "Z_TIER1"


def test_estimate_shape_reference_requires_non_seed_candidates():
    config = IdentityCoherenceConfig()
    result = estimate_shape_reference(
        (
            _candidate("SEED", sample_id="S1"),
            _candidate("S2", sample_id="S2"),
        ),
        config,
        seed_sample_id="S1",
        tier_by_candidate_id={},
        center_rt_min=7.80,
    )

    assert result.shape_reference_basis is ShapeReferenceBasis.NONE
    assert result.shape_reference_candidate_id == ""
    assert result.normalized_intensity == ()


def test_estimate_shape_reference_excludes_seed_from_prototype_medoid():
    config = IdentityCoherenceConfig()
    result = estimate_shape_reference(
        (
            _candidate("SEED", sample_id="S1"),
            _candidate("CELL_REFERENCE", sample_id="S2"),
            _candidate("BACKFILL_DECISION", sample_id="S3"),
            _candidate("IDENTITY_SETTLEMENT", sample_id="S4"),
        ),
        config,
        seed_sample_id="S1",
        tier_by_candidate_id={"SEED": CellIdentityTier.TIER1},
        center_rt_min=7.80,
    )

    assert result.shape_reference_basis is ShapeReferenceBasis.MORPHOLOGY_RT_MEDOID
    assert result.shape_reference_candidate_id in {
        "CELL_REFERENCE",
        "BACKFILL_DECISION",
        "IDENTITY_SETTLEMENT",
    }
    assert result.shape_reference_candidate_id != "SEED"
    assert result.seed_fallback_used is False


def test_estimate_shape_reference_accepts_public_string_stage_values():
    config = IdentityCoherenceConfig()
    result = estimate_shape_reference(
        (
            _candidate("CELL_REFERENCE", sample_id="S2"),
            _candidate("BACKFILL_DECISION", sample_id="S3"),
            _candidate("IDENTITY_SETTLEMENT", sample_id="S4"),
            _candidate("BACKFILL", sample_id="S5", evidence_stage="backfill_only"),
        ),
        config,
        seed_sample_id="S1",
        tier_by_candidate_id={},
        center_rt_min=7.80,
    )

    assert result.shape_reference_basis is ShapeReferenceBasis.MORPHOLOGY_RT_MEDOID
    assert result.shape_reference_candidate_id in {
        "CELL_REFERENCE",
        "BACKFILL_DECISION",
        "IDENTITY_SETTLEMENT",
    }


def test_estimate_shape_reference_excludes_candidates_outside_rt_gate():
    config = IdentityCoherenceConfig()
    result = estimate_shape_reference(
        (
            _candidate("CELL_REFERENCE", sample_id="S2"),
            _candidate("BACKFILL_DECISION", sample_id="S3"),
            _candidate("IDENTITY_SETTLEMENT", sample_id="S4"),
            _candidate("FAR", sample_id="S5", start=9.75, apex=9.80, end=9.85),
        ),
        config,
        seed_sample_id="S1",
        tier_by_candidate_id={},
        center_rt_min=7.80,
    )

    assert result.shape_reference_basis is ShapeReferenceBasis.MORPHOLOGY_RT_MEDOID
    assert result.shape_reference_candidate_id in {
        "CELL_REFERENCE",
        "BACKFILL_DECISION",
        "IDENTITY_SETTLEMENT",
    }


def test_create_seed_shape_reference_marks_seed_fallback():
    config = IdentityCoherenceConfig()
    result = create_seed_shape_reference(
        _candidate("SEED", sample_id="S1"),
        config,
    )

    assert result.shape_reference_basis is ShapeReferenceBasis.SEED_FALLBACK
    assert result.shape_reference_candidate_id == "SEED"
    assert result.seed_fallback_used is True


def test_compare_shape_to_reference_passes_similar_trace_with_width_sanity():
    config = IdentityCoherenceConfig()
    reference = ShapeReferenceResult(
        shape_reference_basis=ShapeReferenceBasis.MORPHOLOGY_RT_MEDOID,
        shape_reference_candidate_id="REF",
        normalized_intensity=normalize_trace_for_shape(
            _candidate("REF"),
            config,
        ).normalized_intensity,
        candidate_count=3,
        non_seed_candidate_count=3,
    )

    result = compare_shape_to_reference(
        _candidate("QUERY"),
        reference,
        config,
        width_sanity_status=WidthStatus.PASS,
    )

    assert result.shape_status is ShapeStatus.PASS
    assert result.shape_similarity_cosine == pytest.approx(1.0)
    assert result.shape_reference_basis is ShapeReferenceBasis.MORPHOLOGY_RT_MEDOID
    assert result.shape_reference_candidate_id == "REF"
    assert result.shape_fallback_used is False


def test_compare_shape_to_reference_requires_width_sanity_pass():
    config = IdentityCoherenceConfig()
    reference = create_seed_shape_reference(_candidate("SEED", sample_id="S1"), config)

    result = compare_shape_to_reference(
        _candidate("QUERY"),
        reference,
        config,
        width_sanity_status=WidthStatus.NOT_ASSESSED,
    )

    assert result.shape_status is ShapeStatus.NOT_ASSESSED
    assert result.shape_similarity_cosine is None


def test_compare_shape_to_reference_fails_reliable_bad_audit():
    config = IdentityCoherenceConfig()
    reference = create_seed_shape_reference(_candidate("SEED", sample_id="S1"), config)

    result = compare_shape_to_reference(
        _candidate("BAD", audit=ShapeAuditStatus.SHOULDER),
        reference,
        config,
        width_sanity_status=WidthStatus.PASS,
    )

    assert result.shape_status is ShapeStatus.FAIL
    assert result.shape_audit_status is ShapeAuditStatus.SHOULDER
