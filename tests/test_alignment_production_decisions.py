import math
from dataclasses import fields, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from xic_extractor.alignment.backfill_peak_fact_provenance import (
    BACKFILL_PROVENANCE_WRITER_SCHEMA_VERSION,
    BackfillCellProjection,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.matrix import (
    AlignedCell,
    AlignmentMatrix,
    AlignmentRowLike,
)
from xic_extractor.alignment.production_decisions import (
    ALIGNMENT_DECISION_CONFIG_FIELDS,
    ALIGNMENT_DECISION_CONFIG_ID_PREFIX,
    PRODUCTION_CELL_DECISION_ID_PREFIX,
    PRODUCTION_CELL_DECISION_SCHEMA_VERSION,
    alignment_decision_config_projection,
    build_production_decisions,
    production_cell_decision_projection,
    production_cell_decision_record,
)
from xic_extractor.alignment.promotion_policy import (
    BACKFILL_MS1_PATTERN_BLOCKED_REASON,
    PRIMARY_IDENTITY_RETAINED_BACKFILL_REVIEW_REASON,
)
from xic_extractor.peak_detection.hypotheses import IntegrationResult

_PRODUCTION_CELL_DECISION_FIELDS = (
    "owner_backfill_cell_content_id",
    "alignment_decision_config_id",
    "feature_family_id",
    "sample_stem",
    "raw_status",
    "production_status",
    "backfill_tier",
    "write_matrix_value",
    "matrix_value",
    "blank_reason",
    "matrix_value_source",
)
_FINAL_CELL_PROJECTION_FIELDS = (
    "group_hypothesis_id",
    "public_family_id",
    "sample_stem",
    "profile_id",
    "selected_work_unit_key",
    "selected_fact_content_id",
    "owner_backfill_cell_content_id",
    "production_cell_decision_id",
    "cell_status",
    "consolidation_state",
    "consolidation_winner_group_hypothesis_id",
    "consolidation_source_group_hypothesis_id",
    "matrix_value_counts_seconds",
    "matrix_value_source",
    "writer_schema_version",
)
_EXPECTED_ALIGNMENT_DECISION_CONFIG_FIELDS = (
    "preferred_ppm",
    "max_ppm",
    "preferred_rt_sec",
    "max_rt_sec",
    "product_mz_tolerance_ppm",
    "observed_loss_tolerance_ppm",
    "mz_bucket_neighbor_radius",
    "anchor_priorities",
    "anchor_min_evidence_score",
    "anchor_min_seed_events",
    "anchor_min_scan_support_score",
    "duplicate_fold_ppm",
    "duplicate_fold_rt_sec",
    "duplicate_fold_product_ppm",
    "duplicate_fold_observed_loss_ppm",
    "duplicate_fold_min_detected_overlap",
    "duplicate_fold_min_shared_detected_count",
    "duplicate_fold_min_detected_jaccard",
    "duplicate_fold_min_present_overlap",
    "owner_window_overlap_fraction",
    "owner_apex_close_sec",
    "owner_tail_seed_guard_sec",
    "owner_tail_max_secondary_ratio",
    "owner_backfill_min_detected_samples",
    "identity_rt_candidate_window_sec",
    "rt_unit",
    "fragmentation_model",
)


def test_alignment_decision_config_identity_covers_exact_27_fields() -> None:
    projection = alignment_decision_config_projection(AlignmentConfig())

    assert ALIGNMENT_DECISION_CONFIG_FIELDS == (
        _EXPECTED_ALIGNMENT_DECISION_CONFIG_FIELDS
    )
    assert tuple(field.name for field in fields(projection)) == (
        _EXPECTED_ALIGNMENT_DECISION_CONFIG_FIELDS
    )
    assert projection.alignment_decision_config_id.startswith(
        ALIGNMENT_DECISION_CONFIG_ID_PREFIX
    )

    alternatives: dict[str, object] = {
        name: (
            ("MEDIUM",)
            if name == "anchor_priorities"
            else "changed"
            if name in {"rt_unit", "fragmentation_model"}
            else getattr(projection, name) + 1
        )
        for name in _EXPECTED_ALIGNMENT_DECISION_CONFIG_FIELDS
    }
    for name, value in alternatives.items():
        assert (
            replace(projection, **{name: value}).alignment_decision_config_id
            != projection.alignment_decision_config_id
        )


def test_production_cell_projection_binds_live_cell_config_value_and_source() -> None:
    matrix = _matrix(
        clusters=(_feature("FAM001", evidence="owner_complete_link;owner_count=2"),),
        cells=(
            _cell("s1", "FAM001", "detected", 100.0),
            _cell("s2", "FAM001", "detected", 95.0),
        ),
        sample_order=("s1", "s2"),
    )
    config = AlignmentConfig()
    decision = build_production_decisions(matrix, config).cell("FAM001", "s1")

    projection = production_cell_decision_projection(
        decision,
        cell=matrix.cells[0],
        config=config,
        owner_backfill_cell_content_id=("owner-backfill-authority-cell:" + "0" * 64),
    )

    assert projection.production_cell_decision_id.startswith(
        PRODUCTION_CELL_DECISION_ID_PREFIX
    )
    assert PRODUCTION_CELL_DECISION_SCHEMA_VERSION == (
        "production_cell_decision_projection"
    )
    assert PRODUCTION_CELL_DECISION_ID_PREFIX == "production-cell-decision:"
    assert projection.matrix_value == matrix.cells[0].matrix_area
    assert projection.matrix_value_source == matrix.cells[0].matrix_area_source
    assert tuple(field.name for field in fields(projection)) == (
        _PRODUCTION_CELL_DECISION_FIELDS
    )
    for field_name in _PRODUCTION_CELL_DECISION_FIELDS:
        mutated = replace(
            projection,
            **{
                field_name: _different_projection_value(getattr(projection, field_name))
            },
        )
        assert (
            mutated.production_cell_decision_id
            != projection.production_cell_decision_id
        ), field_name


def test_final_cell_projection_identity_covers_every_field_and_writer_version() -> None:
    projection = BackfillCellProjection(
        group_hypothesis_id="group-hypothesis:1",
        public_family_id="public-family:1",
        sample_stem="sample-1",
        profile_id="signal-profile:" + "1" * 64,
        selected_work_unit_key="work-unit:1",
        selected_fact_content_id="physical-facts:" + "2" * 64,
        owner_backfill_cell_content_id=("owner-backfill-authority-cell:" + "3" * 64),
        production_cell_decision_id="production-cell-decision:" + "4" * 64,
        cell_status="detected",
        consolidation_state="not_consolidated",
        consolidation_winner_group_hypothesis_id="",
        consolidation_source_group_hypothesis_id="",
        matrix_value_counts_seconds=123.5,
        matrix_value_source="trapezoid_counts_seconds_on_canonical_signal",
    )

    assert projection.writer_schema_version == (
        BACKFILL_PROVENANCE_WRITER_SCHEMA_VERSION
    )
    assert tuple(field.name for field in fields(projection)) == (
        _FINAL_CELL_PROJECTION_FIELDS
    )
    for field_name in _FINAL_CELL_PROJECTION_FIELDS:
        mutated = replace(
            projection,
            **{
                field_name: _different_projection_value(getattr(projection, field_name))
            },
        )
        assert mutated.cell_projection_id != projection.cell_projection_id, field_name


def test_detected_and_supported_backfill_write_numeric_values():
    matrix = _matrix(
        clusters=(_feature("FAM001", evidence="owner_complete_link;owner_count=2"),),
        cells=(
            _cell("s1", "FAM001", "detected", 100.0),
            _cell("s4", "FAM001", "detected", 95.0),
            _cell("s2", "FAM001", "backfilled", 90.0),
            _cell("s3", "FAM001", "backfilled", 80.0),
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
    assert decisions.cell("FAM001", "s2").production_status == "accepted_backfill"
    assert decisions.cell("FAM001", "s2").backfill_tier == "accepted_backfill"
    assert decisions.cell("FAM001", "s3").write_matrix_value is True
    assert decisions.cell("FAM001", "s3").production_status == "accepted_backfill"
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
        ("backfill_tier_rank", 0.0),
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
            _cell("s2", "FAM001", "backfilled", 90.0),
        ),
        sample_order=("s1", "s2"),
    )

    decisions = build_production_decisions(matrix, AlignmentConfig())

    assert decisions.cell("FAM001", "s1").write_matrix_value is False
    assert decisions.cell("FAM001", "s1").blank_reason == "missing_row_identity_support"
    assert decisions.cell("FAM001", "s2").write_matrix_value is False
    assert decisions.cell("FAM001", "s2").production_status == "review_backfill"
    assert decisions.row("FAM001").include_in_primary_matrix is False
    assert decisions.row("FAM001").identity_decision == "provisional_discovery"
    assert decisions.row("FAM001").identity_reason == "single_sample_local_owner"
    assert "single_sample_local_owner" in decisions.row("FAM001").row_flags


def test_targeted_metadata_does_not_authorize_untargeted_matrix_write() -> None:
    cells = (
        _cell("s1", "FAM001", "detected", 100.0),
        _cell("s2", "FAM001", "backfilled", 90.0),
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
            _cell("s2", "FAM001", "backfilled", 90.0),
        ),
        sample_order=("s1", "s2"),
    )

    decisions = build_production_decisions(matrix, AlignmentConfig())
    detected_record = decisions.cell("FAM001", "s1").decision_record
    backfill_record = decisions.cell("FAM001", "s2").decision_record

    assert detected_record.decision_class == "not_counted"
    assert detected_record.blockers == ("missing_row_identity_support",)
    assert detected_record.gate == (
        ("decision_class_rank", 2.0),
        ("matrix_value_projection_rank", 1.0),
        ("blocker_count", 1.0),
    )
    assert backfill_record.decision_class == "review"
    assert backfill_record.blockers == ("missing_row_identity_support",)
    assert backfill_record.support == (
        "raw_status:backfilled",
        "production_status:review_backfill",
        "backfill_tier:review_backfill",
    )


def test_backfill_heavy_row_needs_multiple_detected_owners_for_primary_promotion():
    matrix = _matrix(
        clusters=(_feature("FAM001", evidence="owner_complete_link;owner_count=2"),),
        cells=(
            _cell("s1", "FAM001", "detected", 100.0),
            _cell("s2", "FAM001", "backfilled", 90.0, backfill_evidence=False),
            _cell("s3", "FAM001", "backfilled", 80.0, backfill_evidence=False),
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
    assert decisions.row("FAM001").quantifiable_backfill_count == 2
    assert decisions.row("FAM001").include_in_primary_matrix is False


def test_scan_support_only_backfill_keeps_seeds_and_reviews_backfills():
    matrix = _matrix(
        clusters=(_feature("FAM001", evidence="owner_complete_link;owner_count=2"),),
        cells=(
            _cell("seed1", "FAM001", "detected", 100.0),
            _cell("seed2", "FAM001", "detected", 95.0),
            *tuple(
                _cell(
                    f"rescue{i:02d}",
                    "FAM001",
                    "backfilled",
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
    assert decisions.row("FAM001").accepted_backfill_count == 0
    assert decisions.row("FAM001").review_backfill_count == 83
    assert "backfill_cell_evidence_required" in decisions.row("FAM001").row_flags
    assert "backfill_rescue_review_only" in decisions.row("FAM001").row_flags
    assert decisions.cell("FAM001", "seed1").write_matrix_value is True
    assert decisions.cell("FAM001", "rescue01").production_status == "review_backfill"
    assert decisions.cell("FAM001", "rescue01").write_matrix_value is False
    assert decisions.cell("FAM001", "rescue01").blank_reason == (
        BACKFILL_MS1_PATTERN_BLOCKED_REASON
    )


def test_production_cell_decision_record_keeps_review_backfill_out_of_matrix() -> None:
    matrix = _matrix(
        clusters=(_feature("FAM001", evidence="owner_complete_link;owner_count=2"),),
        cells=(
            _cell("seed1", "FAM001", "detected", 100.0),
            _cell("seed2", "FAM001", "detected", 95.0),
            *tuple(
                _cell(
                    f"rescue{i:02d}",
                    "FAM001",
                    "backfilled",
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

    record = (
        build_production_decisions(
            matrix,
            AlignmentConfig(),
        )
        .cell("FAM001", "rescue01")
        .decision_record
    )

    assert record.decision_class == "review"
    assert record.blockers == (BACKFILL_MS1_PATTERN_BLOCKED_REASON,)
    assert record.gate == (
        ("decision_class_rank", 1.0),
        ("matrix_value_projection_rank", 1.0),
        ("blocker_count", 1.0),
    )
    assert record.tie_break == (
        ("production_status_rank", 2.0),
        ("backfill_tier_rank", 2.0),
        ("matrix_value_present_rank", 1.0),
    )


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


def test_backfill_without_identity_support_is_review_only_and_row_is_excluded():
    matrix = _matrix(
        clusters=(_feature("FAM001", evidence="", has_anchor=False),),
        cells=(
            _cell("s1", "FAM001", "backfilled", 90.0),
            _cell("s2", "FAM001", "absent", None),
        ),
        sample_order=("s1", "s2"),
    )

    decisions = build_production_decisions(matrix, AlignmentConfig())

    assert decisions.cell("FAM001", "s1").write_matrix_value is False
    assert decisions.cell("FAM001", "s1").production_status == "review_backfill"
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
            _cell("s2", "FAM001", "backfilled", 90.0),
        ),
        sample_order=("s1", "s2"),
    )

    decisions = build_production_decisions(matrix, AlignmentConfig())

    assert decisions.cell("FAM001", "s2").production_status == "review_backfill"
    assert decisions.row("FAM001").include_in_primary_matrix is False
    assert set(decisions.row("FAM001").row_flags) == {
        "single_sample_local_owner",
        "rescue_only",
        "duplicate_claim_pressure",
        "rescue_only_review",
    }


def test_backfilled_with_incomplete_peak_fields_is_review_only():
    matrix = _matrix(
        clusters=(_feature("FAM001", evidence="owner_complete_link;owner_count=2"),),
        cells=(
            _cell(
                "s1",
                "FAM001",
                "backfilled",
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
    assert decision.production_status == "review_backfill"
    assert decision.backfill_tier == "review_backfill"
    assert decision.blank_reason == "incomplete_peak"


def test_backfilled_outside_max_rt_is_review_only():
    config = AlignmentConfig()
    matrix = _matrix(
        clusters=(_feature("FAM001", evidence="owner_complete_link;owner_count=2"),),
        cells=(
            _cell(
                "s1",
                "FAM001",
                "backfilled",
                90.0,
                rt_delta_sec=config.max_rt_sec + 0.1,
                backfill_evidence=False,
            ),
        ),
        sample_order=("s1",),
    )

    decision = build_production_decisions(matrix, config).cell("FAM001", "s1")

    assert decision.write_matrix_value is False
    assert decision.production_status == "review_backfill"
    assert decision.backfill_tier == "review_backfill"
    assert decision.blank_reason == "rt_outside_max"


def test_backfilled_outside_preferred_without_drift_is_review_only():
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
                "backfilled",
                80.0,
                rt_delta_sec=20.0,
                backfill_evidence=False,
            ),
        ),
        sample_order=("d1", "d2", "d3", "rescued_same_peak"),
    )

    decisions = build_production_decisions(matrix, config)
    decision = decisions.cell("FAM001", "rescued_same_peak")

    assert decision.write_matrix_value is False
    assert decision.production_status == "review_backfill"
    assert decision.backfill_tier == "review_backfill"
    assert decision.matrix_value is None
    assert decision.blank_reason == "rt_outside_preferred"
    assert decisions.row("FAM001").accepted_backfill_count == 0


def test_bool_area_is_not_a_valid_production_matrix_area():
    matrix = _matrix(
        clusters=(_feature("FAM001", evidence="single_sample_local_owner"),),
        cells=(
            _cell("detected_bool", "FAM001", "detected", True),
            _cell("rescued_bool", "FAM001", "backfilled", True),
        ),
        sample_order=("detected_bool", "rescued_bool"),
    )

    decisions = build_production_decisions(matrix, AlignmentConfig())

    detected = decisions.cell("FAM001", "detected_bool")
    backfilled = decisions.cell("FAM001", "rescued_bool")
    assert detected.write_matrix_value is False
    assert detected.production_status == "blank"
    assert detected.blank_reason == "invalid_area"
    assert backfilled.write_matrix_value is False
    assert backfilled.production_status == "rejected_backfill"
    assert backfilled.backfill_tier == "rejected_backfill"
    assert backfilled.blank_reason == "invalid_area"


def _different_projection_value(value: object) -> object:
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value + 1
    if isinstance(value, str):
        return f"{value}-mutated"
    raise AssertionError(f"unsupported projection field value: {value!r}")


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
        and status in {"detected", "backfilled"}
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
            else f"{sample_stem}#1"
            if status == "detected"
            else None
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


def _backfill_evidence_fields(*, status: str, enabled: bool) -> dict[str, object]:
    if status != "backfilled" or not enabled:
        return {}
    return {
        "backfill_ms2_trigger_scan_count": 3,
        "backfill_strict_nl_scan_count": 1,
        "backfill_ms2_trace_strength": "moderate",
    }


def _positive_area_value(value: float | bool | None) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return value
