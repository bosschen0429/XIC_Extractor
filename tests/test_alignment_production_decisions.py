import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.matrix import (
    AlignedCell,
    AlignmentMatrix,
    AlignmentRowLike,
)
from xic_extractor.alignment.production_decisions import (
    build_production_decisions,
    production_cell_decision_record,
)
from xic_extractor.alignment.promotion_policy import (
    ANCHOR_OWN_MAX_MS1_SUPPORT_REASON,
    BACKFILL_CELL_EVIDENCE_REQUIRED_FLAG,
    BACKFILL_HYPOTHESIS_BLOCKED_REASON,
    BACKFILL_MS1_PATTERN_BLOCKED_REASON,
    CELL_EVIDENCE_SUPPORTED_REASON,
    DDA_LIMITED_MS2_SHAPE_REASON,
    HIGH_BACKFILL_CAPPED_FLAG,
    PRIMARY_IDENTITY_RETAINED_BACKFILL_REVIEW_REASON,
    STANDARD_PEAK_GATE_MS1_SUPPORT_REASON,
)
from xic_extractor.peak_detection.hypotheses import IntegrationResult


def test_detected_and_supported_rescue_write_numeric_values():
    matrix = _matrix(
        clusters=(_feature("FAM001", evidence="owner_complete_link;owner_count=2"),),
        cells=(
            _cell("s1", "FAM001", "detected", 100.0),
            _cell("s4", "FAM001", "detected", 95.0),
            _cell("s2", "FAM001", "rescued", 90.0),
            _cell("s3", "FAM001", "rescued", 80.0),
        ),
        sample_order=("s1", "s4", "s2", "s3"),
    )

    decisions = build_production_decisions(matrix, AlignmentConfig())

    assert decisions.cell("FAM001", "s1").write_matrix_value is True
    assert decisions.cell("FAM001", "s1").production_status == "detected"
    assert decisions.cell("FAM001", "s1").matrix_value == 100.0
    assert decisions.cell("FAM001", "s4").write_matrix_value is True
    assert decisions.cell("FAM001", "s4").production_status == "detected"
    assert decisions.cell("FAM001", "s2").write_matrix_value is True
    assert decisions.cell("FAM001", "s2").production_status == "accepted_rescue"
    assert decisions.cell("FAM001", "s2").rescue_tier == "accepted_rescue"
    assert decisions.cell("FAM001", "s3").write_matrix_value is True
    assert decisions.cell("FAM001", "s3").production_status == "accepted_rescue"
    assert decisions.row("FAM001").include_in_primary_matrix is True
    assert decisions.row("FAM001").identity_decision == "production_family"
    assert decisions.row("FAM001").primary_evidence == "owner_complete_link"
    assert decisions.row("FAM001").quantifiable_detected_count == 2
    assert decisions.row("FAM001").row_flags == ()


def test_production_cell_decision_record_explains_matrix_value_projection() -> None:
    matrix = _matrix(
        clusters=(_feature("FAM001", evidence="owner_complete_link;owner_count=2"),),
        cells=(
            _cell("s1", "FAM001", "detected", 100.0),
            _cell("s2", "FAM001", "detected", 95.0),
        ),
        sample_order=("s1", "s2"),
    )

    decision = build_production_decisions(matrix, AlignmentConfig()).cell(
        "FAM001",
        "s1",
    )
    record = production_cell_decision_record(decision)

    assert decision.decision_record == record
    assert record.workflow == "alignment_production_cell"
    assert record.unit_id == "FAM001:s1"
    assert record.required_evidence == (
        "production_cell_decision",
        "cell_quality_decision",
        "matrix_identity_row_decision",
    )
    assert record.decision_class == "accepted"
    assert record.blockers == ()
    assert record.support == (
        "raw_status:detected",
        "production_status:detected",
        "write_matrix_value",
        "matrix_value_present",
    )
    assert record.gate == (
        ("decision_class_rank", 0.0),
        ("matrix_value_projection_rank", 0.0),
        ("blocker_count", 0.0),
    )
    assert record.tie_break == (
        ("production_status_rank", 0.0),
        ("rescue_tier_rank", 0.0),
        ("matrix_value_present_rank", 0.0),
    )
    assert record.projection_authority == "build_production_decisions"
    assert not hasattr(record, "key")


def test_production_matrix_value_uses_asls_selected_integration_area_when_present():
    matrix = _matrix(
        clusters=(_feature("FAM001", evidence="owner_complete_link;owner_count=2"),),
        cells=(
            _cell(
                "s1",
                "FAM001",
                "detected",
                100.0,
                selected_integration=_integration(raw_area=150.0, asls_area=120.0),
            ),
            _cell("s2", "FAM001", "detected", 95.0),
        ),
        sample_order=("s1", "s2"),
    )

    decisions = build_production_decisions(matrix, AlignmentConfig())

    assert decisions.row("FAM001").include_in_primary_matrix is True
    assert decisions.cell("FAM001", "s1").matrix_value == 120.0
    assert decisions.cell("FAM001", "s2").matrix_value == 95.0


def test_single_sample_local_owner_does_not_create_primary_identity():
    matrix = _matrix(
        clusters=(_feature("FAM001", evidence="single_sample_local_owner"),),
        cells=(
            _cell("s1", "FAM001", "detected", 100.0),
            _cell("s2", "FAM001", "rescued", 90.0),
        ),
        sample_order=("s1", "s2"),
    )

    decisions = build_production_decisions(matrix, AlignmentConfig())

    assert decisions.cell("FAM001", "s1").write_matrix_value is False
    assert decisions.cell("FAM001", "s1").blank_reason == "missing_row_identity_support"
    assert decisions.cell("FAM001", "s2").write_matrix_value is False
    assert decisions.cell("FAM001", "s2").production_status == "review_rescue"
    assert decisions.row("FAM001").include_in_primary_matrix is False
    assert decisions.row("FAM001").identity_decision == "provisional_discovery"
    assert decisions.row("FAM001").identity_reason == "single_sample_local_owner"
    assert "single_sample_local_owner" in decisions.row("FAM001").row_flags


def test_targeted_metadata_does_not_authorize_untargeted_matrix_write() -> None:
    cells = (
        _cell("s1", "FAM001", "detected", 100.0),
        _cell("s2", "FAM001", "rescued", 90.0),
    )
    base = build_production_decisions(
        _matrix(
            clusters=(_feature("FAM001", evidence="single_sample_local_owner"),),
            cells=cells,
            sample_order=("s1", "s2"),
        ),
        AlignmentConfig(),
    )
    adversarial = build_production_decisions(
        _matrix(
            clusters=(
                _feature(
                    "FAM001",
                    evidence="single_sample_local_owner",
                    paired_istd_status="rt_close",
                    paired_istd_rt_delta_min=0.01,
                    targeted_priority_rank=0,
                    targeted_projection_authority="TargetedProductProjection",
                ),
            ),
            cells=cells,
            sample_order=("s1", "s2"),
        ),
        AlignmentConfig(),
    )

    assert adversarial.row("FAM001") == base.row("FAM001")
    assert adversarial.cell("FAM001", "s1") == base.cell("FAM001", "s1")
    assert adversarial.cell("FAM001", "s1").write_matrix_value is False
    assert adversarial.cell("FAM001", "s1").blank_reason == (
        "missing_row_identity_support"
    )


def test_production_cell_decision_record_explains_missing_row_identity_blank() -> None:
    matrix = _matrix(
        clusters=(_feature("FAM001", evidence="single_sample_local_owner"),),
        cells=(
            _cell("s1", "FAM001", "detected", 100.0),
            _cell("s2", "FAM001", "rescued", 90.0),
        ),
        sample_order=("s1", "s2"),
    )

    decisions = build_production_decisions(matrix, AlignmentConfig())
    detected_record = decisions.cell("FAM001", "s1").decision_record
    rescue_record = decisions.cell("FAM001", "s2").decision_record

    assert detected_record.decision_class == "not_counted"
    assert detected_record.blockers == ("missing_row_identity_support",)
    assert detected_record.gate == (
        ("decision_class_rank", 2.0),
        ("matrix_value_projection_rank", 1.0),
        ("blocker_count", 1.0),
    )
    assert rescue_record.decision_class == "review"
    assert rescue_record.blockers == ("missing_row_identity_support",)
    assert rescue_record.support == (
        "raw_status:rescued",
        "production_status:review_rescue",
        "rescue_tier:review_rescue",
    )


def test_rescue_heavy_row_needs_multiple_detected_owners_for_primary_promotion():
    matrix = _matrix(
        clusters=(_feature("FAM001", evidence="owner_complete_link;owner_count=2"),),
        cells=(
            _cell("s1", "FAM001", "detected", 100.0),
            _cell("s2", "FAM001", "rescued", 90.0, backfill_evidence=False),
            _cell("s3", "FAM001", "rescued", 80.0, backfill_evidence=False),
        ),
        sample_order=("s1", "s2", "s3"),
    )

    decisions = build_production_decisions(matrix, AlignmentConfig())

    assert set(decisions.row("FAM001").row_flags) == {
        "single_detected_seed",
        "rescue_heavy",
        "rescue_only_review",
    }
    assert decisions.row("FAM001").accepted_cell_count == 0
    assert decisions.row("FAM001").quantifiable_detected_count == 1
    assert decisions.row("FAM001").quantifiable_rescue_count == 2
    assert decisions.row("FAM001").include_in_primary_matrix is False


def test_single_detected_product_authorized_same_peak_rescues_write_matrix_values():
    matrix = _matrix(
        clusters=(_feature("FAM001", evidence="owner_complete_link;owner_count=1"),),
        cells=(
            _cell("seed1", "FAM001", "detected", 100.0),
            _cell("rescue1", "FAM001", "rescued", 90.0),
            _cell("rescue2", "FAM001", "rescued", 80.0),
        ),
        sample_order=("seed1", "rescue1", "rescue2"),
    )

    decisions = build_production_decisions(matrix, AlignmentConfig())

    assert decisions.row("FAM001").include_in_primary_matrix is True
    assert decisions.row("FAM001").identity_decision == "production_family"
    assert decisions.row("FAM001").identity_reason == CELL_EVIDENCE_SUPPORTED_REASON
    assert decisions.row("FAM001").accepted_cell_count == 3
    assert decisions.row("FAM001").accepted_rescue_count == 2
    assert decisions.cell("FAM001", "seed1").write_matrix_value is True
    assert decisions.cell("FAM001", "rescue1").production_status == "accepted_rescue"
    assert decisions.cell("FAM001", "rescue1").write_matrix_value is True
    assert decisions.cell("FAM001", "rescue1").matrix_value == 90.0


def test_single_detected_ms1_same_peak_without_candidate_ms2_writes_rescue():
    matrix = _matrix(
        clusters=(_feature("FAM001", evidence="owner_complete_link;owner_count=1"),),
        cells=(
            _cell("seed1", "FAM001", "detected", 100.0),
            _cell(
                "rescue_ms1_only",
                "FAM001",
                "rescued",
                90.0,
                candidate_ms2_evidence=False,
            ),
        ),
        sample_order=("seed1", "rescue_ms1_only"),
    )

    decisions = build_production_decisions(matrix, AlignmentConfig())
    rescue = decisions.cell("FAM001", "rescue_ms1_only")

    assert decisions.row("FAM001").include_in_primary_matrix is True
    assert decisions.row("FAM001").identity_reason == CELL_EVIDENCE_SUPPORTED_REASON
    assert rescue.production_status == "accepted_rescue"
    assert rescue.write_matrix_value is True
    assert rescue.matrix_value == 90.0


def test_standard_peak_gate_ms1_authority_writes_rescue():
    matrix = _matrix(
        clusters=(_feature("FAM001", evidence="owner_complete_link;owner_count=1"),),
        cells=(
            _cell("seed1", "FAM001", "detected", 100.0),
            _cell(
                "standard_rescue",
                "FAM001",
                "rescued",
                90.0,
                candidate_ms2_evidence=False,
                backfill_ms1_reason=STANDARD_PEAK_GATE_MS1_SUPPORT_REASON,
            ),
        ),
        sample_order=("seed1", "standard_rescue"),
    )

    decisions = build_production_decisions(matrix, AlignmentConfig())
    rescue = decisions.cell("FAM001", "standard_rescue")

    assert decisions.row("FAM001").include_in_primary_matrix is True
    assert decisions.row("FAM001").identity_reason == CELL_EVIDENCE_SUPPORTED_REASON
    assert rescue.production_status == "accepted_rescue"
    assert rescue.write_matrix_value is True
    assert rescue.matrix_value == 90.0


def test_extreme_backfill_dependency_row_is_supported_with_capped_warning():
    matrix = _matrix(
        clusters=(_feature("FAM001", evidence="owner_complete_link;owner_count=2"),),
        cells=(
            _cell("seed1", "FAM001", "detected", 100.0),
            _cell("seed2", "FAM001", "detected", 95.0),
            *tuple(
                _cell(f"rescue{i:02d}", "FAM001", "rescued", 80.0 + i)
                for i in range(1, 84)
            ),
        ),
        sample_order=(
            "seed1",
            "seed2",
            *(f"rescue{i:02d}" for i in range(1, 84)),
        ),
    )

    decisions = build_production_decisions(matrix, AlignmentConfig())

    assert decisions.row("FAM001").include_in_primary_matrix is True
    assert decisions.row("FAM001").identity_decision == "production_family"
    assert decisions.row("FAM001").identity_reason == CELL_EVIDENCE_SUPPORTED_REASON
    assert "high_backfill_dependency" in decisions.row("FAM001").row_flags
    assert HIGH_BACKFILL_CAPPED_FLAG in decisions.row("FAM001").row_flags
    assert decisions.cell("FAM001", "seed1").write_matrix_value is True
    assert decisions.cell("FAM001", "rescue01").production_status == "accepted_rescue"
    assert decisions.cell("FAM001", "rescue01").write_matrix_value is True


def test_scan_support_only_backfill_keeps_seeds_and_reviews_rescues():
    matrix = _matrix(
        clusters=(_feature("FAM001", evidence="owner_complete_link;owner_count=2"),),
        cells=(
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
        sample_order=(
            "seed1",
            "seed2",
            *(f"rescue{i:02d}" for i in range(1, 84)),
        ),
    )

    decisions = build_production_decisions(matrix, AlignmentConfig())

    assert decisions.row("FAM001").include_in_primary_matrix is True
    assert decisions.row("FAM001").identity_reason == (
        PRIMARY_IDENTITY_RETAINED_BACKFILL_REVIEW_REASON
    )
    assert decisions.row("FAM001").identity_confidence == "review"
    assert decisions.row("FAM001").accepted_cell_count == 2
    assert decisions.row("FAM001").accepted_rescue_count == 0
    assert decisions.row("FAM001").review_rescue_count == 83
    assert "backfill_cell_evidence_required" in decisions.row("FAM001").row_flags
    assert "backfill_rescue_review_only" in decisions.row("FAM001").row_flags
    assert decisions.cell("FAM001", "seed1").write_matrix_value is True
    assert decisions.cell("FAM001", "rescue01").production_status == "review_rescue"
    assert decisions.cell("FAM001", "rescue01").write_matrix_value is False
    assert decisions.cell("FAM001", "rescue01").blank_reason == (
        BACKFILL_MS1_PATTERN_BLOCKED_REASON
    )


def test_production_cell_decision_record_keeps_review_rescue_out_of_matrix() -> None:
    matrix = _matrix(
        clusters=(_feature("FAM001", evidence="owner_complete_link;owner_count=2"),),
        cells=(
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
        sample_order=(
            "seed1",
            "seed2",
            *(f"rescue{i:02d}" for i in range(1, 84)),
        ),
    )

    record = build_production_decisions(
        matrix,
        AlignmentConfig(),
    ).cell("FAM001", "rescue01").decision_record

    assert record.decision_class == "review"
    assert record.blockers == (BACKFILL_MS1_PATTERN_BLOCKED_REASON,)
    assert record.gate == (
        ("decision_class_rank", 1.0),
        ("matrix_value_projection_rank", 1.0),
        ("blocker_count", 1.0),
    )
    assert record.tie_break == (
        ("production_status_rank", 2.0),
        ("rescue_tier_rank", 2.0),
        ("matrix_value_present_rank", 1.0),
    )


def test_weak_seed_backfill_dependency_row_is_supported_by_cell_evidence():
    matrix = _matrix(
        clusters=(
            _feature(
                "FAM001",
                evidence="owner_complete_link;owner_count=3",
                members=(
                    _candidate("seed1#candidate", evidence_score=55),
                    _candidate("seed2#candidate", seed_event_count=1),
                    _candidate("seed3#candidate", nl_ppm=12.0),
                ),
            ),
        ),
        cells=(
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
        sample_order=(
            "seed1",
            "seed2",
            "seed3",
            *(f"rescue{i}" for i in range(1, 7)),
            "absent",
        ),
    )

    decisions = build_production_decisions(matrix, AlignmentConfig())

    assert decisions.row("FAM001").include_in_primary_matrix is True
    assert decisions.row("FAM001").identity_decision == "production_family"
    assert decisions.row("FAM001").identity_reason == DDA_LIMITED_MS2_SHAPE_REASON
    assert "weak_seed_backfill_dependency" in decisions.row("FAM001").row_flags
    assert HIGH_BACKFILL_CAPPED_FLAG in decisions.row("FAM001").row_flags
    assert decisions.cell("FAM001", "seed1").write_matrix_value is True
    assert decisions.cell("FAM001", "rescue1").production_status == "accepted_rescue"
    assert decisions.cell("FAM001", "rescue1").write_matrix_value is True


def test_duplicate_claim_pressure_blocks_unconsolidated_primary_promotion():
    matrix = _matrix(
        clusters=(_feature("FAM001", evidence="owner_complete_link;owner_count=2"),),
        cells=(
            _cell("s1", "FAM001", "detected", 100.0),
            _cell("s2", "FAM001", "duplicate_assigned", 95.0),
            _cell("s3", "FAM001", "duplicate_assigned", 90.0),
        ),
        sample_order=("s1", "s2", "s3"),
    )

    decisions = build_production_decisions(matrix, AlignmentConfig())

    assert decisions.row("FAM001").row_flags == (
        "single_detected_seed",
        "duplicate_claim_pressure",
    )
    assert decisions.row("FAM001").include_in_primary_matrix is False


def test_same_peak_product_authority_writes_rescues_under_duplicate_pressure():
    matrix = _matrix(
        clusters=(_feature("FAM001", evidence="owner_complete_link;owner_count=1"),),
        cells=(
            _cell("seed1", "FAM001", "detected", 100.0),
            _cell("rescue1", "FAM001", "rescued", 90.0),
            _cell("rescue2", "FAM001", "rescued", 80.0),
            _cell("dup1", "FAM001", "duplicate_assigned", 70.0),
            _cell("dup2", "FAM001", "duplicate_assigned", 60.0),
        ),
        sample_order=("seed1", "rescue1", "rescue2", "dup1", "dup2"),
    )

    decisions = build_production_decisions(matrix, AlignmentConfig())

    assert decisions.row("FAM001").include_in_primary_matrix is True
    assert decisions.row("FAM001").identity_decision == "production_family"
    assert decisions.row("FAM001").identity_reason == CELL_EVIDENCE_SUPPORTED_REASON
    assert "duplicate_claim_pressure" not in decisions.row("FAM001").row_flags
    assert "same_peak_multi_claim" in decisions.row("FAM001").row_flags
    assert decisions.cell("FAM001", "seed1").write_matrix_value is True
    assert decisions.cell("FAM001", "rescue1").write_matrix_value is True
    assert decisions.cell("FAM001", "rescue1").production_status == "accepted_rescue"
    assert decisions.cell("FAM001", "rescue2").write_matrix_value is True
    assert decisions.cell("FAM001", "dup1").write_matrix_value is False
    assert decisions.cell("FAM001", "dup1").blank_reason == "duplicate_loser"
    assert decisions.cell("FAM001", "dup2").write_matrix_value is False
    assert decisions.cell("FAM001", "dup2").blank_reason == "duplicate_loser"


def test_rescue_without_identity_support_is_review_only_and_row_is_excluded():
    matrix = _matrix(
        clusters=(_feature("FAM001", evidence="", has_anchor=False),),
        cells=(
            _cell("s1", "FAM001", "rescued", 90.0),
            _cell("s2", "FAM001", "absent", None),
        ),
        sample_order=("s1", "s2"),
    )

    decisions = build_production_decisions(matrix, AlignmentConfig())

    assert decisions.cell("FAM001", "s1").write_matrix_value is False
    assert decisions.cell("FAM001", "s1").production_status == "review_rescue"
    assert decisions.cell("FAM001", "s1").blank_reason == "missing_row_identity_support"
    assert decisions.row("FAM001").include_in_primary_matrix is False
    assert set(decisions.row("FAM001").row_flags) == {
        "rescue_only",
        "rescue_only_review",
    }


def test_duplicate_ambiguous_absent_unchecked_and_invalid_areas_are_blank():
    matrix = _matrix(
        clusters=(_feature("FAM001", evidence="single_sample_local_owner"),),
        cells=(
            _cell("duplicate", "FAM001", "duplicate_assigned", 10.0),
            _cell("ambiguous", "FAM001", "ambiguous_ms1_owner", 20.0),
            _cell("absent", "FAM001", "absent", None),
            _cell("unchecked", "FAM001", "unchecked", None),
            _cell("zero", "FAM001", "detected", 0.0),
            _cell("nan", "FAM001", "detected", math.nan),
        ),
        sample_order=("duplicate", "ambiguous", "absent", "unchecked", "zero", "nan"),
    )

    decisions = build_production_decisions(matrix, AlignmentConfig())

    assert decisions.cell("FAM001", "duplicate").blank_reason == "duplicate_loser"
    assert decisions.cell("FAM001", "ambiguous").blank_reason == "ambiguous_ms1_owner"
    assert decisions.cell("FAM001", "absent").blank_reason == "absent"
    assert decisions.cell("FAM001", "unchecked").blank_reason == "unchecked"
    assert decisions.cell("FAM001", "zero").blank_reason == "invalid_area"
    assert decisions.cell("FAM001", "nan").blank_reason == "invalid_area"
    assert decisions.row("FAM001").include_in_primary_matrix is False


def test_identity_anchor_lost_row_is_excluded_until_review_passes():
    matrix = _matrix(
        clusters=(_feature("FAM001", evidence="single_sample_local_owner"),),
        cells=(
            _cell("s1", "FAM001", "duplicate_assigned", 100.0),
            _cell("s2", "FAM001", "rescued", 90.0),
        ),
        sample_order=("s1", "s2"),
    )

    decisions = build_production_decisions(matrix, AlignmentConfig())

    assert decisions.cell("FAM001", "s2").production_status == "review_rescue"
    assert decisions.row("FAM001").include_in_primary_matrix is False
    assert set(decisions.row("FAM001").row_flags) == {
        "single_sample_local_owner",
        "rescue_only",
        "duplicate_claim_pressure",
        "rescue_only_review",
    }


def test_rescued_with_incomplete_peak_fields_is_review_only():
    matrix = _matrix(
        clusters=(_feature("FAM001", evidence="owner_complete_link;owner_count=2"),),
        cells=(
            _cell(
                "s1",
                "FAM001",
                "rescued",
                90.0,
                apex_rt=None,
            ),
        ),
        sample_order=("s1",),
    )

    decision = build_production_decisions(
        matrix,
        AlignmentConfig(),
    ).cell("FAM001", "s1")

    assert decision.write_matrix_value is False
    assert decision.production_status == "review_rescue"
    assert decision.rescue_tier == "review_rescue"
    assert decision.blank_reason == "incomplete_peak"


def test_rescued_outside_max_rt_is_review_only():
    config = AlignmentConfig()
    matrix = _matrix(
        clusters=(_feature("FAM001", evidence="owner_complete_link;owner_count=2"),),
        cells=(
            _cell(
                "s1",
                "FAM001",
                "rescued",
                90.0,
                rt_delta_sec=config.max_rt_sec + 0.1,
                backfill_evidence=False,
            ),
        ),
        sample_order=("s1",),
    )

    decision = build_production_decisions(matrix, config).cell("FAM001", "s1")

    assert decision.write_matrix_value is False
    assert decision.production_status == "review_rescue"
    assert decision.rescue_tier == "review_rescue"
    assert decision.blank_reason == "rt_outside_max"


def test_product_authorized_same_peak_rt_outside_max_rescue_writes_matrix_value():
    config = AlignmentConfig(preferred_rt_sec=10.0, max_rt_sec=30.0)
    matrix = _matrix(
        clusters=(_feature("FAM001", evidence="owner_complete_link;owner_count=3"),),
        cells=(
            _cell("d1", "FAM001", "detected", 100.0),
            _cell("d2", "FAM001", "detected", 95.0),
            _cell("d3", "FAM001", "detected", 90.0),
            _cell(
                "rescued_same_peak",
                "FAM001",
                "rescued",
                80.0,
                rt_delta_sec=120.0,
                backfill_evidence=True,
            ),
        ),
        sample_order=("d1", "d2", "d3", "rescued_same_peak"),
    )

    decisions = build_production_decisions(matrix, config)
    decision = decisions.cell("FAM001", "rescued_same_peak")

    assert decision.write_matrix_value is True
    assert decision.production_status == "accepted_rescue"
    assert decision.rescue_tier == "accepted_rescue"
    assert decision.matrix_value == 80.0
    assert decisions.row("FAM001").accepted_rescue_count == 1


def test_product_authorized_same_peak_with_drift_evidence_writes_large_rt_delta():
    config = AlignmentConfig(preferred_rt_sec=10.0, max_rt_sec=30.0)
    matrix = _matrix(
        clusters=(_feature("FAM001", evidence="owner_complete_link;owner_count=3"),),
        cells=(
            _cell("d1", "FAM001", "detected", 100.0),
            _cell("d2", "FAM001", "detected", 95.0),
            _cell("d3", "FAM001", "detected", 90.0),
            _cell(
                "rescued_drift",
                "FAM001",
                "rescued",
                80.0,
                rt_delta_sec=240.0,
                backfill_evidence=True,
                backfill_drift_supported=True,
            ),
        ),
        sample_order=("d1", "d2", "d3", "rescued_drift"),
    )

    decision = build_production_decisions(matrix, config).cell(
        "FAM001",
        "rescued_drift",
    )

    assert decision.write_matrix_value is True
    assert decision.production_status == "accepted_rescue"
    assert decision.matrix_value == 80.0


def test_product_authorized_same_peak_wrong_hypothesis_stays_review_rescue():
    matrix = _matrix(
        clusters=(_feature("FAM001", evidence="owner_complete_link;owner_count=3"),),
        cells=(
            _cell("d1", "FAM001", "detected", 100.0),
            _cell("d2", "FAM001", "detected", 95.0),
            _cell(
                "rescued_wrong_claim",
                "FAM001",
                "rescued",
                80.0,
                group_claim_state="duplicate_loser",
                claim_winner_group_hypothesis_id="FAM_WINNER",
                claim_source_group_hypothesis_id="FAM001",
            ),
        ),
        sample_order=("d1", "d2", "rescued_wrong_claim"),
    )

    decisions = build_production_decisions(matrix, AlignmentConfig())
    rescue = decisions.cell("FAM001", "rescued_wrong_claim")

    assert decisions.row("FAM001").include_in_primary_matrix is True
    assert BACKFILL_CELL_EVIDENCE_REQUIRED_FLAG in decisions.row("FAM001").row_flags
    assert rescue.write_matrix_value is False
    assert rescue.production_status == "review_rescue"
    assert rescue.blank_reason == BACKFILL_HYPOTHESIS_BLOCKED_REASON


def test_product_authorized_claim_resolved_by_current_winner_writes_rescue():
    matrix = _matrix(
        clusters=(
            _feature("FAM001", evidence="owner_complete_link;owner_count=3"),
        ),
        cells=(
            _cell("d1", "FAM001", "detected", 100.0),
            _cell("d2", "FAM001", "detected", 95.0),
            _cell(
                "rescued_moved_to_winner",
                "FAM001",
                "rescued",
                80.0,
                group_hypothesis_id="FAM001",
                group_claim_state="duplicate_loser",
                claim_winner_group_hypothesis_id="FAM001",
                claim_source_group_hypothesis_id="FAM_LOSER",
                consolidation_state="moved_to_primary_winner",
            ),
        ),
        sample_order=("d1", "d2", "rescued_moved_to_winner"),
    )

    rescue = build_production_decisions(matrix, AlignmentConfig()).cell(
        "FAM001",
        "rescued_moved_to_winner",
    )

    assert rescue.write_matrix_value is True
    assert rescue.production_status == "accepted_rescue"
    assert rescue.matrix_value == 80.0


def test_product_authorized_same_peak_mode_split_blocker_stays_review_rescue():
    matrix = _matrix(
        clusters=(_feature("FAM001", evidence="owner_complete_link;owner_count=3"),),
        cells=(
            _cell("d1", "FAM001", "detected", 100.0),
            _cell("d2", "FAM001", "detected", 95.0),
            _cell(
                "rescued_mode_split",
                "FAM001",
                "rescued",
                80.0,
                peak_hypothesis_status="mode_split_required",
                product_selection_blocker="mode_split_required",
                rt_mode_status="mode_split_required",
            ),
        ),
        sample_order=("d1", "d2", "rescued_mode_split"),
    )

    decision = build_production_decisions(matrix, AlignmentConfig()).cell(
        "FAM001",
        "rescued_mode_split",
    )

    assert decision.write_matrix_value is False
    assert decision.production_status == "review_rescue"
    assert decision.blank_reason == BACKFILL_HYPOTHESIS_BLOCKED_REASON


def test_bool_area_is_not_a_valid_production_matrix_area():
    matrix = _matrix(
        clusters=(_feature("FAM001", evidence="single_sample_local_owner"),),
        cells=(
            _cell("detected_bool", "FAM001", "detected", True),
            _cell("rescued_bool", "FAM001", "rescued", True),
        ),
        sample_order=("detected_bool", "rescued_bool"),
    )

    decisions = build_production_decisions(matrix, AlignmentConfig())

    detected = decisions.cell("FAM001", "detected_bool")
    rescued = decisions.cell("FAM001", "rescued_bool")
    assert detected.write_matrix_value is False
    assert detected.production_status == "blank"
    assert detected.blank_reason == "invalid_area"
    assert rescued.write_matrix_value is False
    assert rescued.production_status == "rejected_rescue"
    assert rescued.rescue_tier == "rejected_rescue"
    assert rescued.blank_reason == "invalid_area"


def _matrix(
    *,
    clusters: tuple[AlignmentRowLike, ...],
    cells: tuple[AlignedCell, ...],
    sample_order: tuple[str, ...],
) -> AlignmentMatrix:
    return AlignmentMatrix(clusters=clusters, cells=cells, sample_order=sample_order)


def _feature(
    feature_family_id: str,
    *,
    evidence: str,
    has_anchor: bool = True,
    **extra: object,
) -> AlignmentRowLike:
    return cast(
        AlignmentRowLike,
        SimpleNamespace(
            feature_family_id=feature_family_id,
            neutral_loss_tag="DNA_dR",
            family_center_mz=500.123,
            family_center_rt=8.49,
            family_product_mz=384.076,
            family_observed_neutral_loss_da=116.047,
            has_anchor=has_anchor,
            event_cluster_ids=("OWN-s1-000001",),
            event_member_count=1,
            evidence=evidence,
            review_only=False,
            **extra,
        ),
    )


def _cell(
    sample_stem: str,
    cluster_id: str,
    status: str,
    area: float | bool | None,
    *,
    apex_rt: float | None = 8.49,
    height: float | None = 100.0,
    peak_start_rt: float | None = 8.4,
    peak_end_rt: float | None = 8.6,
    rt_delta_sec: float | None = 0.0,
    source_candidate_id: str | None = None,
    selected_integration: IntegrationResult | None = None,
    backfill_evidence: bool = True,
    backfill_ms1_reason: str = ANCHOR_OWN_MAX_MS1_SUPPORT_REASON,
    backfill_drift_supported: bool = False,
    candidate_ms2_evidence: bool = True,
    group_hypothesis_id: str = "",
    group_claim_state: str = "",
    claim_winner_group_hypothesis_id: str = "",
    claim_source_group_hypothesis_id: str = "",
    consolidation_state: str = "",
    peak_hypothesis_status: str = "",
    product_selection_blocker: str = "",
    rt_mode_status: str = "",
) -> AlignedCell:
    has_area = area is not None
    positive_area = _positive_area_value(area)
    if (
        selected_integration is None
        and status in {"detected", "rescued"}
        and positive_area is not None
    ):
        selected_integration = _integration(
            raw_area=positive_area,
            asls_area=positive_area,
        )
    return AlignedCell(
        sample_stem=sample_stem,
        cluster_id=cluster_id,
        status=status,  # type: ignore[arg-type]
        area=area,  # type: ignore[arg-type]
        apex_rt=apex_rt if has_area else None,
        height=height if has_area else None,
        peak_start_rt=peak_start_rt if has_area else None,
        peak_end_rt=peak_end_rt if has_area else None,
        rt_delta_sec=rt_delta_sec if has_area else None,
        trace_quality="clean" if has_area else status,
        scan_support_score=0.8 if has_area else None,
        source_candidate_id=(
            source_candidate_id
            if source_candidate_id is not None
            else f"{sample_stem}#1" if status == "detected" else None
        ),
        source_raw_file=Path(f"{sample_stem}.raw") if status == "detected" else None,
        reason=(
            "duplicate MS1 peak claim; winner=FAM000000; original_status=detected"
            if status == "duplicate_assigned"
            else status
        ),
        group_hypothesis_id=group_hypothesis_id,
        group_claim_state=group_claim_state,
        claim_winner_group_hypothesis_id=claim_winner_group_hypothesis_id,
        claim_source_group_hypothesis_id=claim_source_group_hypothesis_id,
        consolidation_state=consolidation_state,
        peak_hypothesis_status=peak_hypothesis_status,
        product_selection_blocker=product_selection_blocker,
        rt_mode_status=rt_mode_status,
        selected_integration=selected_integration,
        **cast(
            Any,
            _backfill_evidence_fields(
                status=status,
                enabled=backfill_evidence,
                ms1_reason=backfill_ms1_reason,
                drift_supported=backfill_drift_supported,
                candidate_ms2_evidence=candidate_ms2_evidence,
            ),
        ),
    )


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


def _integration(
    *,
    raw_area: float,
    asls_area: float | None,
    baseline_type: str = "asls",
) -> IntegrationResult:
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
        baseline_type=baseline_type,
        boundary_sources=("test",),
        area_ms1_morphology=asls_area,
        ms1_morphology_area_source=(
            "gaussian15_positive_asls_residual" if asls_area is not None else ""
        ),
    )


def _backfill_evidence_fields(
    *,
    status: str,
    enabled: bool,
    ms1_reason: str,
    drift_supported: bool,
    candidate_ms2_evidence: bool,
) -> dict[str, object]:
    if status != "rescued" or not enabled:
        return {}
    fields: dict[str, object] = {
        "backfill_ms1_pattern_status": "supportive",
        "backfill_ms1_pattern_evidence_level": "trace_constellation",
        "backfill_qc_reference_status": "supportive",
        "backfill_qc_reference_evidence_level": "qc_consensus_with_local_qc_overlay",
        "backfill_ms2_trigger_scan_count": 3,
        "backfill_strict_nl_scan_count": 1,
        "backfill_ms2_trace_strength": "moderate",
        "backfill_evidence_reason": ms1_reason,
        **_product_authority_fields(),
    }
    if not candidate_ms2_evidence:
        for key in tuple(fields):
            if key.startswith("backfill_candidate_ms2"):
                del fields[key]
    else:
        fields.update(
            {
                "backfill_candidate_ms2_pattern_status": "partial_support",
                "backfill_candidate_ms2_evidence_level": "sample_candidate_aligned",
            },
        )
    if drift_supported:
        fields.update(
            {
                "backfill_matrix_rt_drift_status": "drift_supported",
                "backfill_drift_evidence_level": "sample_istd_aligned",
                "backfill_drift_compatible_status": "compatible",
                "backfill_drift_corrected_delta_sec": 4.0,
            },
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


def _positive_area_value(value: float | bool | None) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return value
