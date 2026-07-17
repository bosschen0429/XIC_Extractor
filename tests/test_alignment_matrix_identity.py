from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.matrix import AlignedCell, AlignmentMatrix
from xic_extractor.alignment.matrix_identity import (
    MatrixIdentityRowDecision,
    build_matrix_identity_decisions,
    matrix_identity_row_decision_record,
)
from xic_extractor.alignment.promotion_policy import (
    ANCHOR_OWN_MAX_MS1_SUPPORT_REASON,
    CELL_EVIDENCE_SUPPORTED_REASON,
    DDA_LIMITED_MS2_SHAPE_REASON,
    HIGH_BACKFILL_CAPPED_FLAG,
    PRIMARY_IDENTITY_RETAINED_BACKFILL_REVIEW_REASON,
    RESCUE_ONLY_BLOCKED_REASON,
)
from xic_extractor.peak_detection.hypotheses import IntegrationResult

_DEFAULT_SCAN_SUPPORT = object()


def test_owner_complete_link_requires_two_detected_identity_cells() -> None:
    matrix = _matrix(
        _feature("FAM001", evidence="owner_complete_link;owner_count=2"),
        (
            _cell("s1", "FAM001", "detected", 100.0),
            _cell("s2", "FAM001", "rescued", 90.0, backfill_evidence=False),
            _cell("s3", "FAM001", "rescued", 80.0, backfill_evidence=False),
        ),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")

    assert decision.include_in_primary_matrix is False
    assert decision.identity_decision == "provisional_discovery"
    assert decision.identity_reason == "insufficient_detected_identity_support"
    assert decision.quantifiable_detected_count == 1
    assert decision.quantifiable_rescue_count == 2
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
            _cell("s3", "FAM001", "rescued", 80.0),
        ),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")

    assert decision.include_in_primary_matrix is True
    assert decision.identity_decision == "production_family"
    assert decision.identity_confidence == "high"
    assert decision.primary_evidence == "owner_complete_link"
    assert decision.quantifiable_detected_count == 2


def test_matrix_identity_decision_record_explains_row_projection_policy() -> None:
    matrix = _matrix(
        _feature("FAM001", evidence="owner_complete_link;owner_count=2"),
        (
            _cell("s1", "FAM001", "detected", 100.0),
            _cell("s2", "FAM001", "detected", 90.0),
            _cell("s3", "FAM001", "rescued", 80.0),
        ),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")
    record = matrix_identity_row_decision_record(decision)

    assert decision.decision_record == record
    assert record.workflow == "alignment_matrix_identity_row"
    assert record.unit_id == "FAM001"
    assert record.required_evidence == (
        "matrix_identity_row",
        "cell_quality_decisions",
        "row_identity_evidence",
        "backfill_promotion_policy",
    )
    assert record.decision_class == "accepted"
    assert record.blockers == ()
    assert record.support == (
        "identity_decision:production_family",
        "identity_confidence:high",
        "include_in_primary_matrix",
        "primary_evidence:owner_complete_link",
        "detected_identity_cells:2",
        "quantifiable_rescue_cells:1",
    )
    assert record.gate == (
        ("decision_class_rank", 0.0),
        ("primary_matrix_projection_rank", 0.0),
        ("blocker_count", 0.0),
    )
    assert record.tie_break == (
        ("identity_confidence_rank", 0.0),
        ("negative_detected_identity_cells", -2.0),
        ("negative_quantifiable_rescue_cells", -1.0),
        ("duplicate_pressure_count", 0.0),
        ("ambiguous_owner_count", 0.0),
    )
    assert record.projection_authority == "build_matrix_identity_decisions"
    assert not hasattr(record, "key")


def test_extreme_dr_backfill_dependency_with_cell_evidence_promotes() -> None:
    matrix = _matrix(
        _feature("FAM001", evidence="owner_complete_link;owner_count=2"),
        (
            _cell("seed1", "FAM001", "detected", 100.0),
            _cell("seed2", "FAM001", "detected", 95.0),
            *tuple(
                _cell(f"rescue{i:02d}", "FAM001", "rescued", 80.0 + i)
                for i in range(1, 84)
            ),
        ),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")

    assert decision.include_in_primary_matrix is True
    assert decision.identity_decision == "production_family"
    assert decision.identity_confidence == "medium"
    assert decision.identity_reason == CELL_EVIDENCE_SUPPORTED_REASON
    assert decision.quantifiable_detected_count == 2
    assert decision.quantifiable_rescue_count == 83
    assert "high_backfill_dependency" in decision.row_flags
    assert HIGH_BACKFILL_CAPPED_FLAG in decision.row_flags


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
                    "rescued",
                    80.0 + i,
                    backfill_evidence=False,
                )
                for i in range(1, 84)
            ),
        ),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")

    assert decision.include_in_primary_matrix is True
    assert decision.identity_decision == "production_family"
    assert decision.identity_confidence == "review"
    assert (
        decision.identity_reason
        == PRIMARY_IDENTITY_RETAINED_BACKFILL_REVIEW_REASON
    )
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
                    "rescued",
                    80.0 + i,
                    backfill_evidence=False,
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


def test_single_detected_seed_with_same_peak_evidence_promotes() -> None:
    matrix = _matrix(
        _feature("FAM001", evidence="owner_complete_link;owner_count=1"),
        (
            _cell("seed1", "FAM001", "detected", 100.0),
            _cell("rescue1", "FAM001", "rescued", 90.0),
            _cell("rescue2", "FAM001", "rescued", 80.0),
        ),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")

    assert decision.include_in_primary_matrix is True
    assert decision.identity_decision == "production_family"
    assert decision.identity_confidence == "medium"
    assert decision.identity_reason == CELL_EVIDENCE_SUPPORTED_REASON
    assert decision.quantifiable_detected_count == 1
    assert decision.quantifiable_rescue_count == 2
    assert "single_detected_seed" in decision.row_flags
    assert HIGH_BACKFILL_CAPPED_FLAG in decision.row_flags


def test_single_detected_same_peak_support_keeps_ambiguous_owner_review() -> None:
    matrix = _matrix(
        _feature("FAM001", evidence="owner_complete_link;owner_count=1"),
        (
            _cell("seed1", "FAM001", "detected", 100.0),
            _cell("rescue1", "FAM001", "rescued", 90.0),
            _cell("rescue2", "FAM001", "rescued", 80.0),
            _cell("ambiguous1", "FAM001", "ambiguous_ms1_owner", 70.0),
        ),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")

    assert decision.include_in_primary_matrix is False
    assert decision.identity_decision == "provisional_discovery"
    assert decision.identity_reason == "insufficient_detected_identity_support"
    assert "ambiguous_ms1_owner_pressure" in decision.row_flags
    assert HIGH_BACKFILL_CAPPED_FLAG not in decision.row_flags


def test_same_peak_product_authority_downgrades_duplicate_pressure_to_warning() -> None:
    matrix = _matrix(
        _feature("FAM001", evidence="owner_complete_link;owner_count=1"),
        (
            _cell("seed1", "FAM001", "detected", 100.0),
            _cell("rescue1", "FAM001", "rescued", 90.0),
            _cell("rescue2", "FAM001", "rescued", 80.0),
            _cell("dup1", "FAM001", "duplicate_assigned", 70.0),
            _cell("dup2", "FAM001", "duplicate_assigned", 60.0),
        ),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")

    assert decision.include_in_primary_matrix is True
    assert decision.identity_decision == "production_family"
    assert decision.identity_reason == CELL_EVIDENCE_SUPPORTED_REASON
    assert decision.quantifiable_detected_count == 1
    assert decision.quantifiable_rescue_count == 2
    assert decision.duplicate_assigned_count == 2
    assert "duplicate_claim_pressure" not in decision.row_flags
    assert "same_peak_multi_claim" in decision.row_flags
    assert HIGH_BACKFILL_CAPPED_FLAG in decision.row_flags


def test_one_detected_area_only_rescue_is_not_provisional_retention_candidate() -> None:
    matrix = _matrix(
        _feature("FAM001", evidence="owner_complete_link;owner_count=1"),
        (
            _cell("seed1", "FAM001", "detected", 100.0),
            _cell(
                "rescue1",
                "FAM001",
                "rescued",
                90.0,
                trace_quality="owner_backfill",
                scan_support_score=None,
                backfill_evidence=False,
            ),
            _cell(
                "rescue2",
                "FAM001",
                "rescued",
                80.0,
                trace_quality="owner_backfill",
                scan_support_score=None,
                backfill_evidence=False,
            ),
        ),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")

    assert decision.include_in_primary_matrix is False
    assert decision.identity_decision == "provisional_discovery"
    assert "single_detected_seed" in decision.row_flags
    assert "provisional_retention_candidate" not in decision.row_flags
    assert "skip_expensive_evidence" not in decision.row_flags


def test_review_only_one_detected_rescue_is_not_provisional_candidate() -> None:
    matrix = _matrix(
        _feature(
            "FAM001",
            evidence="owner_complete_link;owner_count=1",
            review_only=True,
        ),
        (
            _cell("seed1", "FAM001", "detected", 100.0),
            _cell("rescue1", "FAM001", "rescued", 90.0),
            _cell("rescue2", "FAM001", "rescued", 80.0),
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
                _cell(f"rescue{i:02d}", "FAM001", "rescued", 80.0 + i)
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


def test_weak_seed_dr_backfill_dependency_with_cell_evidence_is_supported() -> None:
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
                _cell(f"rescue{i}", "FAM001", "rescued", 70.0 + i)
                for i in range(1, 7)
            ),
            _cell("absent", "FAM001", "absent", None),
        ),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")

    assert decision.include_in_primary_matrix is True
    assert decision.identity_decision == "production_family"
    assert decision.identity_confidence == "medium"
    assert decision.identity_reason == DDA_LIMITED_MS2_SHAPE_REASON
    assert decision.quantifiable_detected_count == 3
    assert decision.quantifiable_rescue_count == 6
    assert "weak_seed_backfill_dependency" in decision.row_flags
    assert "rescue_heavy" in decision.row_flags
    assert HIGH_BACKFILL_CAPPED_FLAG in decision.row_flags


def test_known_drift_beyond_local_apex_window_does_not_demote_family() -> None:
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
            _cell(
                "drifted",
                "FAM001",
                "rescued",
                80.0,
                trace_quality="owner_backfill",
                scan_support_score=0.8,
                rt_delta_sec=-240.0,
                backfill_drift_supported=True,
            ),
            *tuple(
                _cell(
                    f"rescue{i}",
                    "FAM001",
                    "rescued",
                    70.0 + i,
                    trace_quality="owner_backfill",
                    scan_support_score=0.8,
                )
                for i in range(1, 5)
            ),
        ),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")

    assert decision.include_in_primary_matrix is True
    assert decision.identity_reason == DDA_LIMITED_MS2_SHAPE_REASON
    assert HIGH_BACKFILL_CAPPED_FLAG in decision.row_flags


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
                    "rescued",
                    70.0 + i,
                    rt_delta_sec=120.0,
                    backfill_evidence=False,
                )
                for i in range(1, 7)
            ),
        ),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")

    assert decision.include_in_primary_matrix is True
    assert decision.identity_confidence == "review"
    assert (
        decision.identity_reason
        == PRIMARY_IDENTITY_RETAINED_BACKFILL_REVIEW_REASON
    )
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
                _cell(f"rescue{i}", "FAM001", "rescued", 70.0 + i)
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


def test_owner_events_tolerate_single_weak_seed_with_two_trusted() -> None:
    matrix = _matrix(
        _feature(
            "FAM001",
            evidence="owner_complete_link;owner_count=3",
            members=(
                _owner("seed1#candidate", evidence_score=55),
                _owner("seed2#candidate"),
                _owner("seed3#candidate"),
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
                _cell(f"rescue{i}", "FAM001", "rescued", 70.0 + i)
                for i in range(1, 7)
            ),
            _cell("absent", "FAM001", "absent", None),
        ),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")

    assert decision.include_in_primary_matrix is True
    assert decision.identity_confidence == "medium"
    assert decision.primary_evidence == "owner_complete_link"
    assert decision.identity_reason == DDA_LIMITED_MS2_SHAPE_REASON
    assert "weak_seed_backfill_dependency" not in decision.row_flags
    assert "weak_seed_tolerated" in decision.row_flags
    assert HIGH_BACKFILL_CAPPED_FLAG in decision.row_flags


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
                    "rescued",
                    70.0 + i,
                    trace_quality="review",
                    scan_support_score=None,
                    backfill_evidence=False,
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
    assert (
        decision.identity_reason
        == PRIMARY_IDENTITY_RETAINED_BACKFILL_REVIEW_REASON
    )
    assert "weak_seed_backfill_dependency" in decision.row_flags
    assert "backfill_cell_evidence_required" in decision.row_flags


def test_matrix_identity_decision_record_ignores_seed_evidence_score_payload() -> None:
    base = _weak_seed_backfill_decision(low_event_seed_score=80)
    adversarial = _weak_seed_backfill_decision(low_event_seed_score=100)

    base_record = matrix_identity_row_decision_record(base)
    adversarial_record = matrix_identity_row_decision_record(adversarial)

    assert base_record.gate == adversarial_record.gate
    assert base_record.tie_break == adversarial_record.tie_break
    assert base_record.blockers == adversarial_record.blockers
    assert base_record.support == adversarial_record.support
    assert all("evidence_score" not in name for name, _value in base_record.gate)
    assert all(
        "evidence_score" not in name for name, _value in base_record.tie_break
    )
    assert not hasattr(base_record, "key")


def test_matrix_identity_ignores_targeted_only_priority_metadata() -> None:
    base = build_matrix_identity_decisions(
        _matrix(
            _feature("FAM001", evidence="single_sample_local_owner"),
            (
                _cell("s1", "FAM001", "detected", 100.0),
                _cell("s2", "FAM001", "rescued", 90.0),
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
                _cell("s2", "FAM001", "rescued", 90.0),
            ),
        ),
        AlignmentConfig(),
    ).row("FAM001")

    assert adversarial == base
    assert adversarial.include_in_primary_matrix is False
    assert adversarial.identity_decision == "provisional_discovery"


def test_weak_seed_gate_reads_owner_events_when_trusted_support_is_thin() -> None:
    matrix = _matrix(
        _feature(
            "FAM001",
            evidence="owner_complete_link;owner_count=3",
            members=(
                _owner("seed1#candidate", evidence_score=55),
                _owner("seed2#candidate", nl_ppm=12.0),
                _owner("seed3#candidate"),
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
                _cell(f"rescue{i}", "FAM001", "rescued", 70.0 + i)
                for i in range(1, 7)
            ),
            _cell("absent", "FAM001", "absent", None),
        ),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")

    assert decision.include_in_primary_matrix is True
    assert decision.identity_reason == DDA_LIMITED_MS2_SHAPE_REASON


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
                    "rescued",
                    80.0 + i,
                    trace_quality="review",
                    scan_support_score=None,
                    backfill_evidence=False,
                )
                for i in range(1, 84)
            ),
        ),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")

    assert decision.include_in_primary_matrix is True
    assert decision.identity_confidence == "review"
    assert (
        decision.identity_reason
        == PRIMARY_IDENTITY_RETAINED_BACKFILL_REVIEW_REASON
    )
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
                    "rescued",
                    80.0 + i,
                    trace_quality="owner_backfill",
                    scan_support_score=None,
                    backfill_evidence=False,
                )
                for i in range(1, 84)
            ),
        ),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")

    assert decision.include_in_primary_matrix is True
    assert decision.identity_confidence == "review"
    assert (
        decision.identity_reason
        == PRIMARY_IDENTITY_RETAINED_BACKFILL_REVIEW_REASON
    )
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
                    "rescued",
                    80.0 + i,
                    region_review_reason="neighboring_ms1_interference",
                )
                for i in range(1, 84)
            ),
        ),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")

    assert decision.include_in_primary_matrix is True
    assert decision.identity_confidence == "review"
    assert (
        decision.identity_reason
        == PRIMARY_IDENTITY_RETAINED_BACKFILL_REVIEW_REASON
    )
    assert "backfill_cell_evidence_required" in decision.row_flags
    assert "neighboring_ms1_interference" in decision.row_flags


def test_single_sample_local_owner_is_audit_even_with_rescue() -> None:
    matrix = _matrix(
        _feature("FAM001", evidence="single_sample_local_owner"),
        (
            _cell("s1", "FAM001", "detected", 100.0),
            _cell("s2", "FAM001", "rescued", 90.0),
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


def test_rescue_only_backfill_only_family_is_audit() -> None:
    matrix = _matrix(
        _feature("FAM001", evidence="owner_complete_link;owner_count=2"),
        (
            _cell("s1", "FAM001", "rescued", 100.0),
            _cell("s2", "FAM001", "rescued", 90.0),
        ),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")

    assert decision.include_in_primary_matrix is False
    assert decision.identity_decision == "audit_family"
    assert decision.identity_reason == RESCUE_ONLY_BLOCKED_REASON
    assert "rescue_only" in decision.row_flags


def test_matrix_identity_decision_record_marks_rescue_only_as_not_counted() -> None:
    matrix = _matrix(
        _feature("FAM001", evidence="owner_complete_link;owner_count=2"),
        (
            _cell("s1", "FAM001", "rescued", 100.0),
            _cell("s2", "FAM001", "rescued", 90.0),
        ),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")
    record = decision.decision_record

    assert record.decision_class == "not_counted"
    assert record.blockers == (RESCUE_ONLY_BLOCKED_REASON, "rescue_only")
    assert record.gate == (
        ("decision_class_rank", 2.0),
        ("primary_matrix_projection_rank", 1.0),
        ("blocker_count", 2.0),
    )


def test_matrix_identity_decision_record_marks_ambiguous_owner_as_ambiguous() -> None:
    matrix = _matrix(
        _feature("FAM001", evidence="owner_complete_link;owner_count=2"),
        (_cell("s1", "FAM001", "ambiguous_ms1_owner", 100.0),),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")
    record = decision.decision_record

    assert record.decision_class == "ambiguous"
    assert record.blockers == (
        "ambiguous_only",
        "ambiguous_ms1_owner_pressure",
        "zero_present",
    )


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


def test_artificial_adduct_annotation_does_not_demote_supported_family() -> None:
    matrix = _matrix(
        _feature(
            "FAM001",
            evidence="owner_complete_link;owner_count=5",
            artificial_adduct_role="related_annotation",
        ),
        (
            _cell("s1", "FAM001", "detected", 100.0),
            _cell("s2", "FAM001", "detected", 90.0),
        ),
    )

    decision = build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")

    assert decision.identity_decision == "production_family"
    assert decision.include_in_primary_matrix is True


def _matrix(feature: object, cells: tuple[AlignedCell, ...]) -> AlignmentMatrix:
    return AlignmentMatrix(
        clusters=(feature,),
        cells=cells,
        sample_order=tuple(cell.sample_stem for cell in cells),
    )


def _feature(
    feature_family_id: str,
    *,
    evidence: str,
    review_only: bool = False,
    has_anchor: bool = True,
    neutral_loss_tag: str = "DNA_dR",
    **extra: object,
) -> SimpleNamespace:
    return SimpleNamespace(
        feature_family_id=feature_family_id,
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
    region_review_reason: str = "",
    backfill_evidence: bool = True,
    backfill_drift_supported: bool = False,
    selected_integration: IntegrationResult | None = None,
) -> AlignedCell:
    has_area = area is not None
    if (
        selected_integration is None
        and status in {"detected", "rescued"}
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
        source_raw_file=Path(f"{sample_stem}.raw") if status == "detected" else None,
        reason=status,
        region_review_reason=region_review_reason,
        selected_integration=selected_integration,
        **_backfill_evidence_fields(
            status=status,
            enabled=backfill_evidence,
            drift_supported=backfill_drift_supported,
        ),
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
                    "rescued",
                    70.0 + i,
                    trace_quality="review",
                    scan_support_score=None,
                    backfill_evidence=False,
                )
                for i in range(1, 7)
            ),
            _cell("absent", "FAM001", "absent", None),
        ),
    )
    return build_matrix_identity_decisions(matrix, AlignmentConfig()).row("FAM001")


def _backfill_evidence_fields(
    *,
    status: str,
    enabled: bool,
    drift_supported: bool,
) -> dict[str, object]:
    if status != "rescued" or not enabled:
        return {}
    fields: dict[str, object] = {
        "backfill_ms1_pattern_status": "supportive",
        "backfill_ms1_pattern_evidence_level": "trace_constellation",
        "backfill_qc_reference_status": "supportive",
        "backfill_qc_reference_evidence_level": "qc_consensus_with_local_qc_overlay",
        "backfill_candidate_ms2_pattern_status": "partial_support",
        "backfill_candidate_ms2_evidence_level": "sample_candidate_aligned",
        "backfill_ms2_trigger_scan_count": 3,
        "backfill_strict_nl_scan_count": 1,
        "backfill_ms2_trace_strength": "moderate",
        "backfill_evidence_reason": ANCHOR_OWN_MAX_MS1_SUPPORT_REASON,
        **_product_authority_fields(),
    }
    if drift_supported:
        fields.update(
            {
                "backfill_matrix_rt_drift_status": "drift_supported",
                "backfill_drift_evidence_level": "sample_istd_aligned",
                "backfill_drift_compatible_status": "compatible",
                "backfill_drift_corrected_delta_sec": 4.0,
            }
        )
    return fields


def _product_authority_fields() -> dict[str, object]:
    return {
        "backfill_ms1_product_authority_status": "product_authorized",
        "backfill_ms1_product_authority_scope": "feature_family_sample",
        "backfill_ms1_product_authority_source": "unit_test_reviewed_allowlist",
        "backfill_ms1_product_authority_reason": "unit_test_authorized",
        "backfill_ms1_product_authority_evidence_sha256": "unit-test-ms1-sha256",
        "backfill_candidate_ms2_product_authority_status": "product_authorized",
        "backfill_candidate_ms2_product_authority_scope": "feature_family_sample",
        "backfill_candidate_ms2_product_authority_source": (
            "unit_test_reviewed_allowlist"
        ),
        "backfill_candidate_ms2_product_authority_reason": "unit_test_authorized",
        "backfill_candidate_ms2_product_authority_evidence_sha256": (
            "unit-test-ms2-sha256"
        ),
    }


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
