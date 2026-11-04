from __future__ import annotations

from types import SimpleNamespace

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.matrix import AlignedCell, AlignmentMatrix
from xic_extractor.alignment.matrix_identity import (
    MatrixIdentityRowDecision,
    build_matrix_identity_decisions,
)
from xic_extractor.alignment.promotion_policy import (
    BACKFILL_ONLY_BLOCKED_REASON,
    HIGH_BACKFILL_CAPPED_FLAG,
    PRIMARY_IDENTITY_RETAINED_BACKFILL_REVIEW_REASON,
)
from xic_extractor.peak_detection.hypotheses import IntegrationResult

_DEFAULT_SCAN_SUPPORT = object()


def test_owner_complete_link_requires_two_detected_identity_cells() -> None:
    matrix = _matrix(
        _feature("FAM001", evidence="owner_complete_link;owner_count=2"),
        (
            _cell("s1", "FAM001", "detected", 100.0),
            _cell("s2", "FAM001", "backfilled", 90.0),
            _cell("s3", "FAM001", "backfilled", 80.0),
        ),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")

    assert decision.include_in_primary_matrix is False
    assert decision.identity_decision == "provisional_discovery"
    assert decision.identity_reason == "insufficient_detected_identity_support"
    assert decision.quantifiable_detected_count == 1
    assert decision.quantifiable_backfill_count == 2
    assert "single_detected_seed" in decision.row_flags
    assert "rescue_heavy" in decision.row_flags
    assert "provisional_retention_candidate" not in decision.row_flags
    assert "skip_expensive_evidence" not in decision.row_flags


def test_owner_complete_link_with_two_detected_cells_is_production_family() -> None:
    matrix = _matrix(
        _feature("FAM001", evidence="owner_complete_link;owner_count=2"),
        (
            _cell("s1", "FAM001", "detected", 100.0),
            _cell("s2", "FAM001", "detected", 90.0),
            _cell("s3", "FAM001", "backfilled", 80.0),
        ),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")

    assert decision.include_in_primary_matrix is True
    assert decision.identity_decision == "production_family"
    assert decision.identity_confidence == "high"
    assert decision.primary_evidence == "owner_complete_link"
    assert decision.quantifiable_detected_count == 2


def test_targeted_istd_trajectory_does_not_promote_one_detected_owner() -> None:
    matrix = _matrix(
        _feature(
            "FAM001",
            evidence="owner_targeted_istd_trajectory;owner_count=8",
        ),
        (
            _cell("s1", "FAM001", "detected", 100.0),
            _cell("s2", "FAM001", "backfilled", 90.0),
            _cell("s3", "FAM001", "backfilled", 80.0),
        ),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")

    assert decision.include_in_primary_matrix is False
    assert decision.identity_decision == "provisional_discovery"
    assert decision.primary_evidence == "owner_targeted_istd_trajectory"
    assert decision.identity_reason == "insufficient_detected_identity_support"


def test_targeted_istd_trajectory_two_detected_uses_multi_sample_policy() -> None:
    matrix = _matrix(
        _feature(
            "FAM001",
            evidence="owner_targeted_istd_trajectory;owner_count=8",
        ),
        (
            _cell("s1", "FAM001", "detected", 100.0),
            _cell("s2", "FAM001", "detected", 90.0),
            _cell("s3", "FAM001", "backfilled", 80.0),
        ),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")

    assert decision.include_in_primary_matrix is True
    assert decision.primary_evidence == "multi_sample_detected"
    assert decision.identity_reason == "multi_sample_detected"


def test_full_batch_continuity_uses_existing_multi_sample_identity_policy() -> None:
    matrix = _matrix(
        _feature("FAM001", evidence="owner_full_batch_continuity;owner_count=3"),
        (
            _cell("s1", "FAM001", "detected", 100.0),
            _cell("s2", "FAM001", "detected", 90.0),
            _cell("s3", "FAM001", "detected", 80.0),
        ),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")

    assert decision.include_in_primary_matrix is True
    assert decision.identity_decision == "production_family"
    assert decision.identity_confidence == "medium"
    assert decision.primary_evidence == "multi_sample_detected"
    assert decision.identity_reason == "multi_sample_detected"


def test_scan_support_only_high_backfill_retains_seed_identity() -> None:
    matrix = _matrix(
        _feature("FAM001", evidence="owner_complete_link;owner_count=2"),
        (
            _cell("seed1", "FAM001", "detected", 100.0),
            _cell("seed2", "FAM001", "detected", 95.0),
            *tuple(
                _cell(
                    f"rescue{i:02d}",
                    "FAM001",
                    "backfilled",
                    80.0 + i,
                )
                for i in range(1, 84)
            ),
        ),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")

    assert decision.include_in_primary_matrix is True
    assert decision.identity_decision == "production_family"
    assert decision.identity_confidence == "review"
    assert decision.identity_reason == PRIMARY_IDENTITY_RETAINED_BACKFILL_REVIEW_REASON
    assert "backfill_cell_evidence_required" in decision.row_flags
    assert "backfill_rescue_review_only" in decision.row_flags
    assert "missing_independent_backfill_identity_evidence" in decision.row_flags


def test_single_detected_seed_does_not_enter_policy_promotion() -> None:
    matrix = _matrix(
        _feature("FAM001", evidence="owner_complete_link;owner_count=1"),
        (
            _cell("seed1", "FAM001", "detected", 100.0),
            *tuple(
                _cell(
                    f"rescue{i:02d}",
                    "FAM001",
                    "backfilled",
                    80.0 + i,
                )
                for i in range(1, 8)
            ),
        ),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")

    assert decision.include_in_primary_matrix is False
    assert decision.identity_decision == "provisional_discovery"
    assert decision.identity_reason == "extreme_backfill_dependency"
    assert "single_detected_seed" in decision.row_flags
    assert "provisional_retention_candidate" not in decision.row_flags
    assert "skip_expensive_evidence" not in decision.row_flags
    assert "high_backfill_dependency" in decision.row_flags
    assert HIGH_BACKFILL_CAPPED_FLAG not in decision.row_flags


def test_single_detected_same_peak_support_keeps_ambiguous_owner_review() -> None:
    matrix = _matrix(
        _feature("FAM001", evidence="owner_complete_link;owner_count=1"),
        (
            _cell("seed1", "FAM001", "detected", 100.0),
            _cell("rescue1", "FAM001", "backfilled", 90.0),
            _cell("rescue2", "FAM001", "backfilled", 80.0),
            _cell("ambiguous1", "FAM001", "ambiguous_ms1_owner", 70.0),
        ),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")

    assert decision.include_in_primary_matrix is False
    assert decision.identity_decision == "provisional_discovery"
    assert decision.identity_reason == "insufficient_detected_identity_support"
    assert "ambiguous_ms1_owner_pressure" in decision.row_flags
    assert HIGH_BACKFILL_CAPPED_FLAG not in decision.row_flags


def test_one_detected_area_only_backfill_is_not_provisional_retention_candidate() -> (
    None
):
    matrix = _matrix(
        _feature("FAM001", evidence="owner_complete_link;owner_count=1"),
        (
            _cell("seed1", "FAM001", "detected", 100.0),
            _cell(
                "rescue1",
                "FAM001",
                "backfilled",
                90.0,
                trace_quality="owner_backfill",
                scan_support_score=None,
            ),
            _cell(
                "rescue2",
                "FAM001",
                "backfilled",
                80.0,
                trace_quality="owner_backfill",
                scan_support_score=None,
            ),
        ),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")

    assert decision.include_in_primary_matrix is False
    assert decision.identity_decision == "provisional_discovery"
    assert "single_detected_seed" in decision.row_flags
    assert "provisional_retention_candidate" not in decision.row_flags
    assert "skip_expensive_evidence" not in decision.row_flags


def test_review_only_one_detected_backfill_is_not_provisional_candidate() -> None:
    matrix = _matrix(
        _feature(
            "FAM001",
            evidence="owner_complete_link;owner_count=1",
            review_only=True,
        ),
        (
            _cell("seed1", "FAM001", "detected", 100.0),
            _cell("rescue1", "FAM001", "backfilled", 90.0),
            _cell("rescue2", "FAM001", "backfilled", 80.0),
        ),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")

    assert decision.include_in_primary_matrix is False
    assert decision.identity_decision == "audit_family"
    assert decision.identity_reason == "review_only"
    assert "single_detected_seed" in decision.row_flags
    assert "provisional_retention_candidate" not in decision.row_flags
    assert "skip_expensive_evidence" not in decision.row_flags


def test_high_detected_dr_family_can_still_use_backfill() -> None:
    matrix = _matrix(
        _feature("FAM001", evidence="owner_complete_link;owner_count=20"),
        (
            *tuple(
                _cell(f"seed{i:02d}", "FAM001", "detected", 100.0 + i)
                for i in range(1, 21)
            ),
            *tuple(
                _cell(f"rescue{i:02d}", "FAM001", "backfilled", 80.0 + i)
                for i in range(1, 66)
            ),
        ),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")

    assert decision.include_in_primary_matrix is True
    assert decision.identity_decision == "production_family"
    assert decision.identity_reason == "owner_complete_link"
    assert "high_backfill_dependency" not in decision.row_flags
    assert "rescue_heavy" in decision.row_flags


def test_unexplained_rt_delta_beyond_preferred_blocks_backfill_promotion() -> None:
    matrix = _matrix(
        _feature(
            "FAM001",
            evidence="owner_complete_link;owner_count=3",
            members=(
                _candidate("seed1#candidate", evidence_score=55),
                _candidate("seed2#candidate", seed_event_count=1),
                _candidate("seed3#candidate", nl_ppm=12.0),
            ),
        ),
        (
            _cell(
                "seed1",
                "FAM001",
                "detected",
                100.0,
                source_candidate_id="seed1#candidate",
            ),
            _cell(
                "seed2",
                "FAM001",
                "detected",
                95.0,
                source_candidate_id="seed2#candidate",
            ),
            _cell(
                "seed3",
                "FAM001",
                "detected",
                90.0,
                source_candidate_id="seed3#candidate",
            ),
            *tuple(
                _cell(
                    f"rescue{i}",
                    "FAM001",
                    "backfilled",
                    70.0 + i,
                    rt_delta_sec=120.0,
                )
                for i in range(1, 7)
            ),
        ),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")

    assert decision.include_in_primary_matrix is True
    assert decision.identity_confidence == "review"
    assert decision.quantifiable_backfill_count == 0
    assert decision.review_backfill_count == 6
    assert decision.identity_reason == PRIMARY_IDENTITY_RETAINED_BACKFILL_REVIEW_REASON
    assert "backfill_cell_evidence_required" in decision.row_flags
    assert "missing_independent_backfill_identity_evidence" in decision.row_flags


def test_adequate_seed_dr_backfill_dependency_remains_production() -> None:
    matrix = _matrix(
        _feature(
            "FAM001",
            evidence="owner_complete_link;owner_count=3",
            members=(
                _candidate("seed1#candidate"),
                _candidate("seed2#candidate"),
                _candidate("seed3#candidate"),
            ),
        ),
        (
            _cell(
                "seed1",
                "FAM001",
                "detected",
                100.0,
                source_candidate_id="seed1#candidate",
            ),
            _cell(
                "seed2",
                "FAM001",
                "detected",
                95.0,
                source_candidate_id="seed2#candidate",
            ),
            _cell(
                "seed3",
                "FAM001",
                "detected",
                90.0,
                source_candidate_id="seed3#candidate",
            ),
            *tuple(
                _cell(f"rescue{i}", "FAM001", "backfilled", 70.0 + i)
                for i in range(1, 7)
            ),
            _cell("absent", "FAM001", "absent", None),
        ),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")

    assert decision.include_in_primary_matrix is True
    assert decision.identity_decision == "production_family"
    assert decision.identity_reason == "owner_complete_link"
    assert "weak_seed_backfill_dependency" not in decision.row_flags
    assert "rescue_heavy" in decision.row_flags


def test_high_score_low_event_seeds_do_not_bypass_weak_seed_gate() -> None:
    matrix = _matrix(
        _feature(
            "FAM001",
            evidence="owner_complete_link;owner_count=3",
            members=(
                _owner("seed1#candidate", seed_event_count=1),
                _owner("seed2#candidate", seed_event_count=1),
                _owner("seed3#candidate", evidence_score=55),
            ),
        ),
        (
            _cell(
                "seed1",
                "FAM001",
                "detected",
                100.0,
                source_candidate_id="seed1#candidate",
            ),
            _cell(
                "seed2",
                "FAM001",
                "detected",
                95.0,
                source_candidate_id="seed2#candidate",
            ),
            _cell(
                "seed3",
                "FAM001",
                "detected",
                90.0,
                source_candidate_id="seed3#candidate",
            ),
            *tuple(
                _cell(
                    f"rescue{i}",
                    "FAM001",
                    "backfilled",
                    70.0 + i,
                    trace_quality="review",
                    scan_support_score=None,
                )
                for i in range(1, 7)
            ),
            _cell("absent", "FAM001", "absent", None),
        ),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")

    assert decision.include_in_primary_matrix is True
    assert decision.identity_decision == "production_family"
    assert decision.identity_confidence == "review"
    assert decision.identity_reason == PRIMARY_IDENTITY_RETAINED_BACKFILL_REVIEW_REASON
    assert "weak_seed_backfill_dependency" in decision.row_flags
    assert "backfill_cell_evidence_required" in decision.row_flags


def test_matrix_identity_ignores_targeted_only_priority_metadata() -> None:
    base = build_matrix_identity_decisions(
        _matrix(
            _feature("FAM001", evidence="single_sample_local_owner"),
            (
                _cell("s1", "FAM001", "detected", 100.0),
                _cell("s2", "FAM001", "backfilled", 90.0),
            ),
        ),
        AlignmentConfig(),
    ).row("FAM001")
    adversarial = build_matrix_identity_decisions(
        _matrix(
            _feature(
                "FAM001",
                evidence="single_sample_local_owner",
                paired_istd_status="rt_close",
                paired_istd_rt_delta_min=0.01,
                targeted_priority_rank=0,
                targeted_projection_authority="TargetedProductProjection",
            ),
            (
                _cell("s1", "FAM001", "detected", 100.0),
                _cell("s2", "FAM001", "backfilled", 90.0),
            ),
        ),
        AlignmentConfig(),
    ).row("FAM001")

    assert adversarial == base
    assert adversarial.include_in_primary_matrix is False
    assert adversarial.identity_decision == "provisional_discovery"


def test_rt_local_apex_low_interference_alone_does_not_promote() -> None:
    matrix = _matrix(
        _feature(
            "FAM001",
            evidence="owner_complete_link;owner_count=2",
        ),
        (
            _cell("seed1", "FAM001", "detected", 100.0),
            _cell("seed2", "FAM001", "detected", 95.0),
            *tuple(
                _cell(
                    f"rescue{i:02d}",
                    "FAM001",
                    "backfilled",
                    80.0 + i,
                    trace_quality="review",
                    scan_support_score=None,
                )
                for i in range(1, 84)
            ),
        ),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")

    assert decision.include_in_primary_matrix is True
    assert decision.identity_confidence == "review"
    assert decision.identity_reason == PRIMARY_IDENTITY_RETAINED_BACKFILL_REVIEW_REASON
    assert "backfill_cell_evidence_required" in decision.row_flags
    assert "missing_independent_backfill_identity_evidence" in decision.row_flags


def test_owner_backfill_label_without_independent_support_does_not_promote() -> None:
    matrix = _matrix(
        _feature(
            "FAM001",
            evidence="owner_complete_link;owner_count=2",
        ),
        (
            _cell("seed1", "FAM001", "detected", 100.0),
            _cell("seed2", "FAM001", "detected", 95.0),
            *tuple(
                _cell(
                    f"rescue{i:02d}",
                    "FAM001",
                    "backfilled",
                    80.0 + i,
                    trace_quality="owner_backfill",
                    scan_support_score=None,
                )
                for i in range(1, 84)
            ),
        ),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")

    assert decision.include_in_primary_matrix is True
    assert decision.identity_confidence == "review"
    assert decision.identity_reason == PRIMARY_IDENTITY_RETAINED_BACKFILL_REVIEW_REASON
    assert "backfill_cell_evidence_required" in decision.row_flags
    assert "missing_independent_backfill_identity_evidence" in decision.row_flags


def test_neighboring_ms1_interference_blocks_backfill_promotion() -> None:
    matrix = _matrix(
        _feature("FAM001", evidence="owner_complete_link;owner_count=2"),
        (
            _cell("seed1", "FAM001", "detected", 100.0),
            _cell("seed2", "FAM001", "detected", 95.0),
            *tuple(
                _cell(
                    f"rescue{i:02d}",
                    "FAM001",
                    "backfilled",
                    80.0 + i,
                    reason="neighboring_ms1_interference",
                )
                for i in range(1, 84)
            ),
        ),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")

    assert decision.include_in_primary_matrix is True
    assert decision.identity_confidence == "review"
    assert decision.identity_reason == PRIMARY_IDENTITY_RETAINED_BACKFILL_REVIEW_REASON
    assert "backfill_cell_evidence_required" in decision.row_flags
    assert "neighboring_ms1_interference" in decision.row_flags


def test_single_sample_local_owner_is_audit_even_with_backfill() -> None:
    matrix = _matrix(
        _feature("FAM001", evidence="single_sample_local_owner"),
        (
            _cell("s1", "FAM001", "detected", 100.0),
            _cell("s2", "FAM001", "backfilled", 90.0),
        ),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")

    assert decision.include_in_primary_matrix is False
    assert decision.identity_decision == "provisional_discovery"
    assert decision.identity_reason == "single_sample_local_owner"
    assert "single_sample_local_owner" in decision.row_flags


def test_anchored_single_detected_family_is_provisional_discovery() -> None:
    matrix = _matrix(
        _feature("FAM001", evidence="", has_anchor=True),
        (_cell("s1", "FAM001", "detected", 100.0),),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")

    assert decision.include_in_primary_matrix is False
    assert decision.identity_decision == "provisional_discovery"
    assert decision.identity_reason == "anchored_single_detected_phase_a"
    assert "anchored_single_detected" in decision.row_flags


def test_weak_nonzero_detected_support_is_provisional_discovery() -> None:
    matrix = _matrix(
        _feature("FAM001", evidence="", has_anchor=False),
        (_cell("s1", "FAM001", "detected", 100.0),),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")

    assert decision.include_in_primary_matrix is False
    assert decision.identity_decision == "provisional_discovery"
    assert decision.identity_reason == "insufficient_detected_identity_support"


def test_backfill_only_backfill_only_family_is_audit() -> None:
    matrix = _matrix(
        _feature("FAM001", evidence="owner_complete_link;owner_count=2"),
        (
            _cell("s1", "FAM001", "backfilled", 100.0),
            _cell("s2", "FAM001", "backfilled", 90.0),
        ),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")

    assert decision.include_in_primary_matrix is False
    assert decision.identity_decision == "audit_family"
    assert decision.identity_reason == BACKFILL_ONLY_BLOCKED_REASON
    assert "rescue_only" in decision.row_flags


def test_duplicate_pressure_above_detected_support_is_audit() -> None:
    matrix = _matrix(
        _feature("FAM001", evidence="owner_complete_link;owner_count=2"),
        (
            _cell("s1", "FAM001", "detected", 100.0),
            _cell("s2", "FAM001", "duplicate_assigned", 90.0),
            _cell("s3", "FAM001", "duplicate_assigned", 80.0),
        ),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")

    assert decision.include_in_primary_matrix is False
    assert decision.identity_decision == "audit_family"
    assert decision.identity_reason == "duplicate_claim_pressure"
    assert "duplicate_claim_pressure" in decision.row_flags


def test_family_consolidation_loser_is_audit_only() -> None:
    matrix = _matrix(
        _feature(
            "FAM001",
            evidence=(
                "owner_complete_link;owner_count=2;"
                "primary_family_consolidation_loser;winner=FAM999"
            ),
            review_only=True,
        ),
        (
            _cell("s1", "FAM001", "detected", 100.0),
            _cell("s2", "FAM001", "detected", 90.0),
        ),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")

    assert decision.include_in_primary_matrix is False
    assert decision.identity_reason == "review_only"
    assert "family_consolidation_loser" in decision.row_flags


def test_multi_tag_support_does_not_promote_single_sample_family() -> None:
    matrix = _matrix(
        _feature(
            "FAM001",
            evidence="single_sample_local_owner",
            matched_tag_count=3,
        ),
        (_cell("s1", "FAM001", "detected", 100.0),),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")

    assert decision.identity_decision == "provisional_discovery"
    assert decision.include_in_primary_matrix is False


def _matrix(feature: object, cells: tuple[AlignedCell, ...]) -> AlignmentMatrix:
    return AlignmentMatrix(
        clusters=(feature,),
        cells=cells,
        sample_order=tuple(cell.sample_stem for cell in cells),
    )


def _feature(
    feature_id: str,
    *,
    evidence: str,
    review_only: bool = False,
    has_anchor: bool = True,
    neutral_loss_tag: str = "DNA_dR",
    **extra: object,
) -> SimpleNamespace:
    return SimpleNamespace(
        feature_id=feature_id,
        neutral_loss_tag=neutral_loss_tag,
        family_center_mz=500.123,
        family_center_rt=8.49,
        family_product_mz=384.076,
        family_observed_neutral_loss_da=116.047,
        has_anchor=has_anchor,
        event_cluster_ids=("OWN-s1-000001",),
        event_member_count=1,
        evidence=evidence,
        review_only=review_only,
        **extra,
    )


def _cell(
    sample_stem: str,
    cluster_id: str,
    status: str,
    area: float | None,
    *,
    source_candidate_id: str | None = None,
    trace_quality: str | None = None,
    scan_support_score: float | None | object = _DEFAULT_SCAN_SUPPORT,
    rt_delta_sec: float = 0.0,
    reason: str | None = None,
    selected_integration: IntegrationResult | None = None,
) -> AlignedCell:
    has_area = area is not None
    if (
        selected_integration is None
        and status in {"detected", "backfilled"}
        and _positive_area(area)
    ):
        selected_integration = _integration(raw_area=area, asls_area=area)
    actual_scan_support = (
        (0.8 if has_area else None)
        if scan_support_score is _DEFAULT_SCAN_SUPPORT
        else scan_support_score
    )
    return AlignedCell(
        sample_stem=sample_stem,
        cluster_id=cluster_id,
        status=status,  # type: ignore[arg-type]
        area=area,
        apex_rt=8.49 if has_area else None,
        height=100.0 if has_area else None,
        peak_start_rt=8.4 if has_area else None,
        peak_end_rt=8.6 if has_area else None,
        rt_delta_sec=rt_delta_sec if has_area else None,
        trace_quality=(
            trace_quality
            if trace_quality is not None
            else ("clean" if has_area else status)
        ),
        scan_support_score=actual_scan_support,  # type: ignore[arg-type]
        source_candidate_id=(
            source_candidate_id
            if source_candidate_id is not None
            else f"{sample_stem}#{cluster_id}"
        ),
        reason=status if reason is None else reason,
        selected_integration=selected_integration,
    )


def _positive_area(value: float | None) -> bool:
    return value is not None and value > 0


def _integration(*, raw_area: float, asls_area: float) -> IntegrationResult:
    return IntegrationResult(
        rt_left_min=8.4,
        rt_apex_min=8.49,
        rt_right_min=8.6,
        raw_apex_rt_min=8.49,
        rt_width_min=0.2,
        height_raw=100.0,
        height_smoothed=100.0,
        area_raw_counts_seconds=raw_area,
        area_baseline_corrected=asls_area,
        baseline_type="asls",
        boundary_sources=("test_fixture",),
        area_ms1_morphology=asls_area,
        ms1_morphology_area_source=(
            "gaussian15_positive_asls_residual" if asls_area is not None else ""
        ),
    )


def _weak_seed_backfill_decision(
    *,
    low_event_seed_score: int,
) -> MatrixIdentityRowDecision:
    matrix = _matrix(
        _feature(
            "FAM001",
            evidence="owner_complete_link;owner_count=3",
            members=(
                _owner(
                    "seed1#candidate",
                    seed_event_count=1,
                    evidence_score=low_event_seed_score,
                ),
                _owner(
                    "seed2#candidate",
                    seed_event_count=1,
                    evidence_score=low_event_seed_score,
                ),
                _owner("seed3#candidate", evidence_score=55),
            ),
        ),
        (
            _cell(
                "seed1",
                "FAM001",
                "detected",
                100.0,
                source_candidate_id="seed1#candidate",
            ),
            _cell(
                "seed2",
                "FAM001",
                "detected",
                95.0,
                source_candidate_id="seed2#candidate",
            ),
            _cell(
                "seed3",
                "FAM001",
                "detected",
                90.0,
                source_candidate_id="seed3#candidate",
            ),
            *tuple(
                _cell(
                    f"rescue{i}",
                    "FAM001",
                    "backfilled",
                    70.0 + i,
                    trace_quality="review",
                    scan_support_score=None,
                )
                for i in range(1, 7)
            ),
            _cell("absent", "FAM001", "absent", None),
        ),
    )
    return build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")


def _candidate(
    candidate_id: str,
    *,
    evidence_score: int = 80,
    seed_event_count: int = 3,
    nl_ppm: float = 3.0,
    scan_support: float = 0.8,
) -> SimpleNamespace:
    return SimpleNamespace(
        candidate_id=candidate_id,
        evidence_score=evidence_score,
        seed_event_count=seed_event_count,
        neutral_loss_mass_error_ppm=nl_ppm,
        ms1_scan_support_score=scan_support,
    )


def _owner(
    candidate_id: str,
    *,
    evidence_score: int = 80,
    seed_event_count: int = 3,
    nl_ppm: float = 3.0,
) -> SimpleNamespace:
    return SimpleNamespace(
        all_events=(
            _candidate(
                candidate_id,
                evidence_score=evidence_score,
                seed_event_count=seed_event_count,
                nl_ppm=nl_ppm,
            ),
        ),
    )
