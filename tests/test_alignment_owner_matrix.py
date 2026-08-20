import csv
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from tests.test_alignment_owner_backfill import _feature
from tests.test_alignment_owner_clustering import _owner
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.matrix import AlignedCell
from xic_extractor.alignment.owner_area import median_owner_area
from xic_extractor.alignment.owner_clustering import OwnerAlignedFeature
from xic_extractor.alignment.owner_matrix import (
    ambiguous_records_by_sample,
    build_owner_alignment_matrix,
)
from xic_extractor.alignment.ownership_models import AmbiguousOwnerRecord
from xic_extractor.alignment.production_decisions import build_production_decisions
from xic_extractor.peak_detection.integration_audit import CellIntegrationAuditSummary
from xic_extractor.peak_detection.integration_result import IntegrationResult
from xic_extractor.peak_detection.region_audit import PeakRegionAuditSummary


def _rescue_integration(
    *,
    area: float,
    apex: float = 8.55,
    left: float = 8.50,
    right: float = 8.60,
) -> IntegrationResult:
    return IntegrationResult(
        rt_left_min=left,
        rt_apex_min=apex,
        rt_right_min=right,
        raw_apex_rt_min=apex,
        rt_width_min=right - left,
        height_raw=90.0,
        height_smoothed=90.0,
        area_raw_counts_seconds=area,
        boundary_sources=("test_owner_backfill",),
        area_ms1_morphology=area,
        ms1_morphology_area_source="gaussian15_positive_asls_residual",
    )


def test_owner_matrix_writes_detected_rescued_ambiguous_and_absent_cells() -> None:
    feature = _feature()
    rescued = AlignedCell(
        sample_stem="sample-b",
        cluster_id=feature.feature_family_id,
        status="rescued",
        area=90.0,
        apex_rt=8.51,
        height=40.0,
        peak_start_rt=8.45,
        peak_end_rt=8.55,
        rt_delta_sec=0.6,
        trace_quality="owner_backfill",
        scan_support_score=None,
        source_candidate_id=None,
        source_raw_file=None,
        reason="owner-centered MS1 backfill",
        selected_integration=_rescue_integration(area=90.0),
    )
    ambiguous = AmbiguousOwnerRecord(
        ambiguity_id="AMB-sample-c-000001",
        sample_stem="sample-c",
        candidate_ids=("sample-c#1", "sample-c#2"),
        reason="owner_multiplet_ambiguity",
    )

    matrix = build_owner_alignment_matrix(
        (feature,),
        sample_order=("sample-a", "sample-b", "sample-c", "sample-d"),
        ambiguous_by_sample=ambiguous_records_by_sample((ambiguous,)),
        rescued_cells=(rescued,),
    )

    by_sample = {cell.sample_stem: cell for cell in matrix.cells}
    assert by_sample["sample-a"].status == "detected"
    assert by_sample["sample-a"].area == 1000.0
    assert by_sample["sample-a"].group_hypothesis_id == "FAM000001"
    assert by_sample["sample-a"].public_family_id == "FAM000001"
    assert by_sample["sample-a"].group_construction_role == "successor_constructor"
    assert (
        by_sample["sample-a"].group_delivery_role
        == "owner_aligned_feature_compatibility_facade"
    )
    assert (
        by_sample["sample-a"].group_membership_source
        == "owner_aligned_feature_successor_projection"
    )
    assert by_sample["sample-a"].gap_fill_state == "observed_member"
    assert by_sample["sample-a"].gap_fill_reason == "local_owner_detected"
    assert by_sample["sample-a"].missing_observation_state == "observed"
    assert by_sample["sample-a"].group_claim_state == "unclaimed_or_winner"
    assert by_sample["sample-a"].selected_integration is not None
    assert by_sample["sample-a"].selected_integration.area_raw_counts_seconds == 1000.0
    assert by_sample["sample-a"].matrix_area == 900.0
    assert by_sample["sample-a"].source_candidate_id == "sample-a#a"
    assert by_sample["sample-b"].status == "rescued"
    assert by_sample["sample-b"].group_hypothesis_id == "FAM000001"
    assert by_sample["sample-b"].gap_fill_state == "gap_fill_rescued"
    assert by_sample["sample-c"].status == "ambiguous_ms1_owner"
    assert by_sample["sample-c"].gap_fill_state == "not_filled"
    assert by_sample["sample-c"].missing_observation_state == "ambiguous_observation"
    assert by_sample["sample-c"].gap_fill_reason == "not_requested_ambiguous_owner"
    assert by_sample["sample-c"].area is None
    assert "sample-c#1;sample-c#2" in by_sample["sample-c"].reason
    assert by_sample["sample-d"].status == "absent"
    assert by_sample["sample-d"].gap_fill_state == "not_filled"
    assert by_sample["sample-d"].missing_observation_state == "missing_not_observed"
    assert by_sample["sample-d"].gap_fill_reason == "not_requested_no_gap_fill"
    assert by_sample["sample-d"].source_raw_file is None
    assert matrix.clusters == (feature,)
    assert matrix.sample_order == ("sample-a", "sample-b", "sample-c", "sample-d")


def test_owner_matrix_censors_endpoint_apex_owner_without_backfill_request() -> None:
    owner = replace(
        _owner("sample-a", "edge"),
        quantification_eligible=False,
        quantification_exclusion_reason="owner_apex_on_integration_boundary",
    )
    feature = replace(_feature(), owners=(owner,))

    matrix = build_owner_alignment_matrix(
        (feature,),
        sample_order=("sample-a",),
        ambiguous_by_sample={},
        rescued_cells=(),
    )

    cell = matrix.cells[0]
    assert cell.status == "unchecked"
    assert cell.area is None
    assert cell.matrix_area is None
    assert cell.selected_integration is None
    assert cell.trace_quality == "owner_apex_on_integration_boundary"
    assert cell.gap_fill_reason == "owner_quantification_excluded"
    assert cell.missing_observation_state == "missing_unchecked"
    decision = build_production_decisions(matrix, AlignmentConfig()).cell(
        feature.feature_family_id,
        "sample-a",
    )
    assert decision.write_matrix_value is False
    assert decision.matrix_value is None


def test_owner_area_median_ignores_censored_owner() -> None:
    feature = replace(
        _feature(),
        owners=(
            replace(
                _owner("sample-a", "edge"),
                owner_area=1.0,
                quantification_eligible=False,
            ),
            replace(_owner("sample-b", "normal"), owner_area=1000.0),
        ),
    )

    assert median_owner_area(feature) == 1000.0


def test_owner_matrix_allows_valid_rescue_to_replace_censored_owner() -> None:
    owner = replace(
        _owner("sample-a", "edge"),
        quantification_eligible=False,
        quantification_exclusion_reason="owner_apex_on_integration_boundary",
    )
    feature = replace(
        _feature(),
        owners=(owner,),
        confirm_local_owners_with_backfill=False,
    )
    rescued = AlignedCell(
        sample_stem="sample-a",
        cluster_id=feature.feature_family_id,
        status="rescued",
        area=900.0,
        apex_rt=8.55,
        height=90.0,
        peak_start_rt=8.50,
        peak_end_rt=8.60,
        rt_delta_sec=3.0,
        trace_quality="owner_backfill",
        scan_support_score=None,
        source_candidate_id=None,
        source_raw_file=None,
        reason="owner-centered MS1 backfill",
        selected_integration=_rescue_integration(area=900.0),
    )

    matrix = build_owner_alignment_matrix(
        (feature,),
        sample_order=("sample-a",),
        ambiguous_by_sample={},
        rescued_cells=(rescued,),
    )

    assert matrix.cells[0].status == "rescued"
    assert matrix.cells[0].area == 900.0
    assert matrix.cells[0].matrix_area == 900.0


def test_owner_matrix_censors_endpoint_rescue_before_matrix_write() -> None:
    feature = replace(
        _feature(),
        owners=(),
        confirm_local_owners_with_backfill=False,
    )
    rescued = AlignedCell(
        sample_stem="sample-a",
        cluster_id=feature.feature_family_id,
        status="rescued",
        area=900.0,
        apex_rt=8.50,
        height=90.0,
        peak_start_rt=8.50,
        peak_end_rt=8.60,
        rt_delta_sec=0.0,
        trace_quality="owner_backfill",
        scan_support_score=None,
        source_candidate_id=None,
        source_raw_file=None,
        reason="owner-centered MS1 backfill",
        selected_integration=_rescue_integration(
            area=900.0,
            apex=8.50,
            left=8.50,
            right=8.60,
        ),
    )

    matrix = build_owner_alignment_matrix(
        (feature,),
        sample_order=("sample-a",),
        ambiguous_by_sample={},
        rescued_cells=(rescued,),
    )

    cell = matrix.cells[0]
    assert cell.status == "unchecked"
    assert cell.area is None
    assert cell.matrix_area is None
    assert cell.trace_quality == "owner_apex_on_integration_boundary"
    assert cell.gap_fill_reason == "owner_quantification_excluded"
    decision = build_production_decisions(matrix, AlignmentConfig()).cell(
        feature.feature_family_id,
        "sample-a",
    )
    assert decision.write_matrix_value is False
    assert decision.matrix_value is None


def test_owner_matrix_accepts_delivery_contract_feature_without_legacy_dto() -> None:
    owner = _owner("sample-a", "a", apex_rt=8.5)
    feature = SimpleNamespace(
        feature_family_id="FAM_CONTRACT",
        cluster_id="FAM_CONTRACT",
        neutral_loss_tag="NL116",
        family_center_mz=500.0,
        family_center_rt=8.5,
        family_product_mz=383.9526,
        family_observed_neutral_loss_da=116.0474,
        has_anchor=True,
        owners=(owner,),
        members=(owner,),
        event_cluster_ids=(owner.owner_id,),
        event_member_count=len(owner.all_events),
        evidence="test_delivery_contract",
        identity_conflict=False,
        review_only=False,
        confirm_local_owners_with_backfill=False,
        backfill_seed_centers=(),
        ambiguous_sample_stem=None,
        ambiguous_candidate_ids=(),
        group_hypothesis_id="GROUP_CONTRACT",
        public_family_id="FAM_CONTRACT",
        group_construction_role="successor_projection_adapter",
        group_delivery_role="successor_delivery_protocol",
        group_membership_source="cross_sample_peak_group_hypothesis",
        consolidation_state="not_consolidated",
        consolidation_winner_group_hypothesis_id="",
        consolidation_source_group_hypothesis_id="",
    )

    matrix = build_owner_alignment_matrix(
        (feature,),
        sample_order=("sample-a", "sample-b"),
        ambiguous_by_sample={},
        rescued_cells=(),
    )

    assert not isinstance(feature, OwnerAlignedFeature)
    assert matrix.clusters == (feature,)
    by_sample = {cell.sample_stem: cell for cell in matrix.cells}
    assert by_sample["sample-a"].status == "detected"
    assert by_sample["sample-a"].cluster_id == "FAM_CONTRACT"
    assert by_sample["sample-a"].group_hypothesis_id == "GROUP_CONTRACT"
    assert by_sample["sample-a"].public_family_id == "FAM_CONTRACT"
    assert by_sample["sample-a"].group_delivery_role == "successor_delivery_protocol"
    assert by_sample["sample-b"].status == "absent"


def test_owner_matrix_preserves_unchecked_backfill_outcome(tmp_path: Path) -> None:
    from xic_extractor.alignment.tsv_writer import write_alignment_cells_tsv

    feature = _feature()
    unchecked = AlignedCell(
        sample_stem="sample-b",
        cluster_id=feature.feature_family_id,
        status="unchecked",
        area=None,
        apex_rt=None,
        height=None,
        peak_start_rt=None,
        peak_end_rt=None,
        rt_delta_sec=None,
        trace_quality="owner_backfill_not_detected",
        scan_support_score=None,
        source_candidate_id=None,
        source_raw_file=None,
        reason="owner-centered MS1 backfill query found no accepted peak",
        backfill_seed_mz=500.0,
        backfill_seed_rt=8.5,
        backfill_request_rt_min=5.5,
        backfill_request_rt_max=11.5,
        backfill_request_ppm=20.0,
    )

    matrix = build_owner_alignment_matrix(
        (feature,),
        sample_order=("sample-a", "sample-b", "sample-c"),
        ambiguous_by_sample={},
        rescued_cells=(unchecked,),
    )

    by_sample = {cell.sample_stem: cell for cell in matrix.cells}
    assert by_sample["sample-b"].status == "unchecked"
    assert by_sample["sample-b"].gap_fill_state == "not_filled"
    assert by_sample["sample-b"].gap_fill_reason == "query_attempt_not_detected"
    assert by_sample["sample-b"].missing_observation_state == "missing_unchecked"
    assert by_sample["sample-b"].matrix_area is None
    assert by_sample["sample-c"].status == "absent"
    assert by_sample["sample-c"].gap_fill_reason == "not_requested_no_gap_fill"

    cells_path = write_alignment_cells_tsv(tmp_path / "alignment_cells.tsv", matrix)
    rows = _cell_rows_by_feature_sample(cells_path)
    assert rows[("FAM000001", "sample-b")]["status"] == "unchecked"
    assert rows[("FAM000001", "sample-b")]["gap_fill_state"] == "not_filled"
    assert rows[("FAM000001", "sample-b")]["gap_fill_reason"] == (
        "query_attempt_not_detected"
    )
    assert rows[("FAM000001", "sample-b")]["missing_observation_state"] == (
        "missing_unchecked"
    )


def test_owner_matrix_detected_cell_does_not_invent_raw_path() -> None:
    matrix = build_owner_alignment_matrix(
        (_feature(),),
        sample_order=("sample-a",),
        ambiguous_by_sample={},
        rescued_cells=(),
    )

    assert matrix.cells[0].source_raw_file is None
    assert isinstance(Path("sample-a.raw"), Path)


def test_owner_matrix_carries_detected_owner_region_audit() -> None:
    owner = replace(
        _owner("sample-a", "a"),
        region_audit=PeakRegionAuditSummary(
            candidate_count=3,
            selected_proposal_sources=("local_minimum", "cwt_ridge"),
            selected_merge_note="region_first_safe_merge",
            shadow_status="evaluated",
            shadow_verdict="merge_suggested",
            merge_suggestion_source="adjacent_wis_local_minimum_merge",
            area_ratio=1.08,
            selected_interval_count=2,
            selected_interval_gap_max_min=0.03,
            local_mixture_diagnostic="one_envelope_supported",
            local_mixture_reason="adjacent WIS intervals support one envelope",
            review_reason="same envelope",
            decision_status="evaluated",
            decision_class="merge_suggested",
            product_action="safe_merge_eligible",
            promotion_reason="adjacent_wis_local_minimum_merge",
            baseline_method="asls",
            integration_audit=CellIntegrationAuditSummary(
                raw_area=1000.0,
                area_baseline_corrected=800.0,
                area_uncertainty=50.0,
                baseline_type="asls",
                baseline_score=0.8,
                uncertainty_fraction=0.05,
                baseline_fraction=0.8,
                integration_scan_count=7,
            ),
        ),
    )
    feature = replace(_feature(), owners=(owner,))

    matrix = build_owner_alignment_matrix(
        (feature,),
        sample_order=("sample-a",),
        ambiguous_by_sample={},
        rescued_cells=(),
    )

    cell = matrix.cells[0]
    assert cell.status == "detected"
    assert cell.region_candidate_count == 3
    assert cell.region_selected_proposal_sources == ("local_minimum", "cwt_ridge")
    assert cell.region_shadow_status == "evaluated"
    assert cell.region_shadow_verdict == "merge_suggested"
    assert cell.region_decision_status == "evaluated"
    assert cell.region_decision_class == "merge_suggested"
    assert cell.region_product_action == "safe_merge_eligible"
    assert cell.region_promotion_reason == "adjacent_wis_local_minimum_merge"
    assert cell.region_baseline_method == "asls"
    assert cell.region_local_mixture_diagnostic == "one_envelope_supported"
    assert cell.integration_audit is not None
    assert cell.integration_audit.baseline_fraction == 0.8


def test_owner_matrix_uses_backfill_confirmation_for_severe_low_local_owner() -> None:
    low_owner = replace(_owner("sample-a", "low"), owner_area=10.0)
    feature = OwnerAlignedFeature(
        feature_family_id="FAM000001",
        neutral_loss_tag="NL116",
        family_center_mz=500.0,
        family_center_rt=8.5,
        family_product_mz=383.9526,
        family_observed_neutral_loss_da=116.0474,
        has_anchor=True,
        owners=(
            low_owner,
            _owner("sample-b", "normal"),
            _owner("sample-c", "normal"),
        ),
        evidence="owner_complete_link;owner_count=3",
        confirm_local_owners_with_backfill=True,
    )
    rescued = AlignedCell(
        sample_stem="sample-a",
        cluster_id=feature.feature_family_id,
        status="rescued",
        area=900.0,
        apex_rt=8.55,
        height=90.0,
        peak_start_rt=8.50,
        peak_end_rt=8.60,
        rt_delta_sec=3.0,
        trace_quality="owner_backfill",
        scan_support_score=None,
        source_candidate_id=None,
        source_raw_file=None,
        reason="owner-centered MS1 backfill",
        selected_integration=_rescue_integration(area=900.0),
    )

    matrix = build_owner_alignment_matrix(
        (feature,),
        sample_order=("sample-a",),
        ambiguous_by_sample={},
        rescued_cells=(rescued,),
    )

    assert matrix.cells[0].status == "rescued"
    assert matrix.cells[0].area == 900.0
    assert matrix.cells[0].gap_fill_state == "gap_fill_rescued"
    assert matrix.cells[0].missing_observation_state == "queried_and_detected"
    assert matrix.cells[0].gap_fill_reason == "group_centered_query_detected"
    assert "superseded low local owner" in matrix.cells[0].reason


def test_owner_matrix_keeps_detected_cell_when_local_owner_is_not_low_outlier() -> None:
    feature = replace(_feature(), confirm_local_owners_with_backfill=True)
    rescued = AlignedCell(
        sample_stem="sample-a",
        cluster_id=feature.feature_family_id,
        status="rescued",
        area=4000.0,
        apex_rt=8.55,
        height=400.0,
        peak_start_rt=8.50,
        peak_end_rt=8.60,
        rt_delta_sec=3.0,
        trace_quality="owner_backfill",
        scan_support_score=None,
        source_candidate_id=None,
        source_raw_file=None,
        reason="owner-centered MS1 backfill",
        selected_integration=_rescue_integration(area=4000.0),
    )

    matrix = build_owner_alignment_matrix(
        (feature,),
        sample_order=("sample-a",),
        ambiguous_by_sample={},
        rescued_cells=(rescued,),
    )

    assert matrix.cells[0].status == "detected"
    assert matrix.cells[0].area == 1000.0


def test_owner_matrix_ambiguous_review_feature_does_not_contaminate_other_features():
    detected_feature = _feature()
    ambiguous_feature = replace(
        _feature(review_only=True),
        feature_family_id="FAM000002",
        owners=(),
        ambiguous_sample_stem="sample-b",
        ambiguous_candidate_ids=("sample-b#1", "sample-b#2"),
    )

    matrix = build_owner_alignment_matrix(
        (detected_feature, ambiguous_feature),
        sample_order=("sample-a", "sample-b"),
        ambiguous_by_sample={},
        rescued_cells=(),
    )

    cells = {
        (cell.cluster_id, cell.sample_stem): cell
        for cell in matrix.cells
    }
    assert cells[("FAM000001", "sample-b")].status == "absent"
    assert cells[("FAM000002", "sample-b")].status == "ambiguous_ms1_owner"


def _cell_rows_by_feature_sample(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return {
            (row["feature_family_id"], row["sample_stem"]): row
            for row in csv.DictReader(handle, delimiter="\t")
        }
