from __future__ import annotations

import csv
from pathlib import Path
from types import SimpleNamespace

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.matrix import AlignedCell, AlignmentMatrix
from xic_extractor.alignment.output_rows import cells_by_cluster, row_id
from xic_extractor.alignment.owner_clustering import OwnerAlignedFeature
from xic_extractor.alignment.ownership_models import IdentityEvent, SampleLocalMS1Owner
from xic_extractor.alignment.primary_consolidation import (
    _connected_components,
    _family_stats,
    _near_duplicate_candidate_pairs,
    consolidate_primary_family_rows,
)
from xic_extractor.alignment.production_decisions import build_production_decisions
from xic_extractor.peak_detection.hypotheses import IntegrationResult
from xic_extractor.peak_detection.ms1_morphology import MS1_MORPHOLOGY_AREA_SOURCE


def test_consolidation_promotes_one_primary_row_from_duplicate_claim_family():
    matrix = AlignmentMatrix(
        clusters=(
            _feature("FAM001", rt=8.40),
            _feature("FAM002", rt=8.55),
            _feature("FAM003", rt=8.70),
        ),
        sample_order=("s1", "s2", "s3"),
        cells=(
            _cell("s1", "FAM001", "detected", 100.0, apex=8.40),
            _duplicate_cell(
                "s2", "FAM001", winner="FAM002", original="rescued", apex=8.55
            ),
            _duplicate_cell(
                "s3", "FAM001", winner="FAM003", original="rescued", apex=8.70
            ),
            _duplicate_cell(
                "s1", "FAM002", winner="FAM001", original="rescued", apex=8.40
            ),
            _cell("s2", "FAM002", "detected", 200.0, apex=8.55),
            _duplicate_cell(
                "s3", "FAM002", winner="FAM003", original="rescued", apex=8.70
            ),
            _duplicate_cell(
                "s1", "FAM003", winner="FAM001", original="rescued", apex=8.40
            ),
            _duplicate_cell(
                "s2", "FAM003", winner="FAM002", original="rescued", apex=8.55
            ),
            _cell("s3", "FAM003", "detected", 300.0, apex=8.70),
        ),
    )

    consolidated = consolidate_primary_family_rows(matrix, AlignmentConfig())
    decisions = build_production_decisions(consolidated, AlignmentConfig())
    primary_rows = [
        row.feature_family_id
        for row in decisions.rows.values()
        if row.include_in_primary_matrix
    ]

    assert primary_rows == ["FAM002"]
    assert decisions.row("FAM002").accepted_cell_count == 3
    assert decisions.row("FAM001").include_in_primary_matrix is False
    assert decisions.row("FAM003").include_in_primary_matrix is False
    winner_cells = {
        cell.sample_stem: cell
        for cell in consolidated.cells
        if cell.cluster_id == "FAM002"
    }
    assert [
        (sample, cell.status, cell.area) for sample, cell in winner_cells.items()
    ] == [
        ("s1", "detected", 100.0),
        ("s2", "detected", 200.0),
        ("s3", "detected", 300.0),
    ]
    assert _feature_by_id(consolidated, "FAM002").family_center_rt == 8.55


def test_consolidation_merges_shared_ms1_peak_across_nl_evidence_tags():
    matrix = AlignmentMatrix(
        clusters=(
            _feature(
                "FAM001",
                product_mz=384.0,
                observed_loss=116.0,
                neutral_loss_tag="DNA_dR",
            ),
            _feature(
                "FAM002",
                product_mz=368.0,
                observed_loss=132.0,
                neutral_loss_tag="DNA_R",
            ),
        ),
        sample_order=("s1", "s2"),
        cells=(
            _cell("s1", "FAM001", "detected", 100.0, apex=8.50),
            _duplicate_cell(
                "s2", "FAM001", winner="FAM002", original="rescued", apex=8.50
            ),
            _duplicate_cell(
                "s1", "FAM002", winner="FAM001", original="rescued", apex=8.50
            ),
            _cell("s2", "FAM002", "detected", 200.0, apex=8.50),
        ),
    )

    consolidated = consolidate_primary_family_rows(matrix, AlignmentConfig())
    decisions = build_production_decisions(consolidated, AlignmentConfig())

    primary_rows = [
        row.feature_family_id
        for row in decisions.rows.values()
        if row.include_in_primary_matrix
    ]

    assert primary_rows == ["FAM001"]
    assert decisions.row("FAM001").accepted_cell_count == 2
    assert decisions.row("FAM002").include_in_primary_matrix is False
    evidence = _feature_by_id(consolidated, "FAM001").evidence
    assert "primary_family_consolidated;family_count=2" in evidence
    assert "shared_ms1_peak_nl_tags=DNA_R,DNA_dR" in evidence


def test_same_tag_product_distinct_rows_do_not_merge_by_shared_ms1_peak():
    matrix = AlignmentMatrix(
        clusters=(
            _feature(
                "FAM_EXACT",
                mz=301.165,
                rt=23.35,
                product_mz=185.116,
                observed_loss=116.049,
                neutral_loss_tag="DNA_dR",
                evidence="owner_complete_link;owner_count=2",
            ),
            _feature(
                "FAM_SHIFTED",
                mz=301.171,
                rt=23.36,
                product_mz=185.123,
                observed_loss=116.048,
                neutral_loss_tag="DNA_dR",
                evidence="owner_complete_link;owner_count=2",
            ),
        ),
        sample_order=("s1", "s2", "s3", "s4"),
        cells=(
            _cell("s1", "FAM_EXACT", "detected", 100.0, apex=23.35),
            _cell("s2", "FAM_EXACT", "detected", 110.0, apex=23.35),
            _duplicate_cell(
                "s3",
                "FAM_EXACT",
                winner="FAM_SHIFTED",
                original="rescued",
                apex=23.36,
            ),
            _duplicate_cell(
                "s4",
                "FAM_EXACT",
                winner="FAM_SHIFTED",
                original="rescued",
                apex=23.36,
            ),
            _duplicate_cell(
                "s1",
                "FAM_SHIFTED",
                winner="FAM_EXACT",
                original="rescued",
                apex=23.35,
            ),
            _duplicate_cell(
                "s2",
                "FAM_SHIFTED",
                winner="FAM_EXACT",
                original="rescued",
                apex=23.35,
            ),
            _cell("s3", "FAM_SHIFTED", "detected", 200.0, apex=23.36),
            _cell("s4", "FAM_SHIFTED", "detected", 210.0, apex=23.36),
        ),
    )

    consolidated = consolidate_primary_family_rows(matrix, AlignmentConfig())
    rows = {
        _feature_by_id(consolidated, "FAM_EXACT").feature_family_id: _feature_by_id(
            consolidated,
            "FAM_EXACT",
        ),
        _feature_by_id(consolidated, "FAM_SHIFTED").feature_family_id: _feature_by_id(
            consolidated,
            "FAM_SHIFTED",
        ),
    }
    decisions = build_production_decisions(consolidated, AlignmentConfig())

    assert rows["FAM_EXACT"].consolidation_state == "not_consolidated"
    assert rows["FAM_SHIFTED"].consolidation_state == "not_consolidated"
    assert rows["FAM_EXACT"].family_product_mz == 185.116
    assert rows["FAM_SHIFTED"].family_product_mz == 185.123
    assert decisions.row("FAM_EXACT").include_in_primary_matrix is True
    assert decisions.row("FAM_SHIFTED").include_in_primary_matrix is True


def test_consolidation_prefers_stronger_sample_peak_over_weak_detected_peak():
    matrix = AlignmentMatrix(
        clusters=(
            _feature("FAM001", rt=8.50),
            _feature("FAM002", rt=8.90),
        ),
        sample_order=("s1", "s2"),
        cells=(
            _cell("s1", "FAM001", "detected", 100.0, apex=8.50),
            _duplicate_cell(
                "s1", "FAM002", winner="FAM001", original="rescued", apex=8.90
            ),
            _cell("s2", "FAM002", "detected", 500.0, apex=8.90),
            _duplicate_cell(
                "s2", "FAM001", winner="FAM002", original="rescued", apex=8.90
            ),
        ),
    )
    strong = next(
        cell
        for cell in matrix.cells
        if cell.sample_stem == "s1" and cell.cluster_id == "FAM002"
    )
    strong = AlignedCell(
        sample_stem=strong.sample_stem,
        cluster_id=strong.cluster_id,
        status=strong.status,
        area=1000.0,
        apex_rt=strong.apex_rt,
        height=strong.height,
        peak_start_rt=strong.peak_start_rt,
        peak_end_rt=strong.peak_end_rt,
        rt_delta_sec=strong.rt_delta_sec,
        trace_quality=strong.trace_quality,
        scan_support_score=strong.scan_support_score,
        source_candidate_id=strong.source_candidate_id,
        source_raw_file=strong.source_raw_file,
        reason=strong.reason,
        selected_integration=_integration(raw_area=1000.0, asls_area=1000.0),
    )
    matrix = AlignmentMatrix(
        clusters=matrix.clusters,
        sample_order=matrix.sample_order,
        cells=(
            matrix.cells[0],
            strong,
            matrix.cells[2],
            matrix.cells[3],
        ),
    )

    consolidated = consolidate_primary_family_rows(matrix, AlignmentConfig())
    winner_id = "FAM001"
    winner_cells = {
        cell.sample_stem: cell
        for cell in consolidated.cells
        if cell.cluster_id == winner_id
    }

    assert winner_cells["s1"].area == 1000.0
    assert winner_cells["s1"].status == "rescued"


def test_consolidation_prefers_detected_over_same_apex_rescued_duplicate():
    matrix = AlignmentMatrix(
        clusters=(
            _feature("FAM001", rt=8.50),
            _feature("FAM002", rt=8.50),
        ),
        sample_order=("s1", "s2"),
        cells=(
            _cell("s1", "FAM001", "detected", 100.0, apex=8.50),
            _duplicate_cell(
                "s1",
                "FAM002",
                winner="FAM001",
                original="rescued",
                apex=8.50,
                area=1000.0,
            ),
            _duplicate_cell(
                "s2",
                "FAM001",
                winner="FAM002",
                original="rescued",
                apex=8.50,
                area=1000.0,
            ),
            _cell("s2", "FAM002", "detected", 200.0, apex=8.50),
        ),
    )

    consolidated = consolidate_primary_family_rows(matrix, AlignmentConfig())
    winner_cells = {
        cell.sample_stem: cell
        for cell in consolidated.cells
        if cell.cluster_id == "FAM001"
    }

    assert [
        (sample, cell.status, cell.area) for sample, cell in winner_cells.items()
    ] == [
        ("s1", "detected", 100.0),
        ("s2", "detected", 200.0),
    ]


def test_near_duplicate_rescue_heavy_primary_family_is_demoted_to_audit():
    matrix = AlignmentMatrix(
        clusters=(
            _feature(
                "FAM_STRONG",
                mz=301.165,
                rt=23.35,
                product_mz=185.116,
                evidence="owner_complete_link;owner_count=4",
            ),
            _feature(
                "FAM_RESCUE",
                mz=301.171,
                rt=24.08,
                product_mz=185.123,
                evidence="owner_complete_link;owner_count=4",
            ),
        ),
        sample_order=("s1", "s2", "s3", "s4"),
        cells=(
            _cell("s1", "FAM_STRONG", "detected", 1000.0, apex=23.35),
            _cell("s2", "FAM_STRONG", "detected", 950.0, apex=23.36),
            _cell("s3", "FAM_STRONG", "detected", 900.0, apex=23.37),
            _cell("s4", "FAM_STRONG", "rescued", 850.0, apex=23.38),
            _cell("s1", "FAM_RESCUE", "detected", 100.0, apex=24.08),
            _cell("s2", "FAM_RESCUE", "rescued", 110.0, apex=24.09),
            _cell("s3", "FAM_RESCUE", "rescued", 120.0, apex=24.10),
            _cell("s4", "FAM_RESCUE", "rescued", 130.0, apex=24.11),
        ),
    )

    consolidated = consolidate_primary_family_rows(matrix, AlignmentConfig())
    decisions = build_production_decisions(consolidated, AlignmentConfig())

    assert decisions.row("FAM_STRONG").include_in_primary_matrix is True
    assert decisions.row("FAM_RESCUE").include_in_primary_matrix is False
    loser = _feature_by_id(consolidated, "FAM_RESCUE")
    assert loser.review_only is True
    assert (
        "primary_family_consolidation_loser;winner=FAM_STRONG"
        in loser.evidence
    )
    assert [
        cell.sample_stem
        for cell in consolidated.cells
        if cell.cluster_id == "FAM_RESCUE"
    ] == ["s1", "s2", "s3", "s4"]


def test_near_duplicate_candidate_pairs_prune_impossible_pairs() -> None:
    matrix = AlignmentMatrix(
        clusters=(
            _feature("FAM_STRONG", rt=10.00),
            _feature("FAM_RESCUE", rt=10.50),
            _feature("FAM_FAR_RT", rt=20.00),
            _feature("FAM_OTHER_NL", rt=10.40, neutral_loss_tag="DNA_R"),
            _feature("FAM_LOW_STATS", rt=10.40),
        ),
        sample_order=("s1", "s2", "s3"),
        cells=(
            _cell("s1", "FAM_STRONG", "detected", 1000.0, apex=10.00),
            _cell("s2", "FAM_STRONG", "detected", 900.0, apex=10.01),
            _cell("s3", "FAM_STRONG", "rescued", 800.0, apex=10.02),
            _cell("s1", "FAM_RESCUE", "detected", 100.0, apex=10.50),
            _cell("s2", "FAM_RESCUE", "rescued", 90.0, apex=10.51),
            _cell("s3", "FAM_RESCUE", "rescued", 80.0, apex=10.52),
            _cell("s1", "FAM_FAR_RT", "detected", 100.0, apex=20.00),
            _cell("s2", "FAM_FAR_RT", "rescued", 90.0, apex=20.01),
            _cell("s3", "FAM_FAR_RT", "rescued", 80.0, apex=20.02),
            _cell("s1", "FAM_OTHER_NL", "detected", 100.0, apex=10.40),
            _cell("s2", "FAM_OTHER_NL", "rescued", 90.0, apex=10.41),
            _cell("s3", "FAM_OTHER_NL", "rescued", 80.0, apex=10.42),
            _cell("s1", "FAM_LOW_STATS", "detected", 100.0, apex=10.40),
        ),
    )
    grouped_cells = cells_by_cluster(matrix)
    clusters_by_id = {row_id(cluster): cluster for cluster in matrix.clusters}
    stats_by_id = {
        cluster_id: _family_stats(grouped_cells.get(cluster_id, ()))
        for cluster_id in clusters_by_id
    }

    pairs = set(
        _near_duplicate_candidate_pairs(
            matrix,
            clusters_by_id=clusters_by_id,
            stats_by_id=stats_by_id,
            config=AlignmentConfig(),
        ),
    )

    assert ("FAM_RESCUE", "FAM_STRONG") in pairs
    assert ("FAM_FAR_RT", "FAM_STRONG") not in pairs
    assert ("FAM_OTHER_NL", "FAM_STRONG") not in pairs
    assert ("FAM_LOW_STATS", "FAM_STRONG") not in pairs


def test_consolidated_winner_carries_source_family_seed_events():
    matrix = AlignmentMatrix(
        clusters=(
            _owner_feature("FAM001", "s1", rt=8.40),
            _owner_feature("FAM002", "s2", rt=8.55),
            _owner_feature("FAM003", "s3", rt=8.70),
        ),
        sample_order=(
            "s1",
            "s2",
            "s3",
            "r1",
            "r2",
            "r3",
            "r4",
            "r5",
            "r6",
        ),
        cells=(
            _cell("s1", "FAM001", "detected", 100.0, apex=8.40),
            _duplicate_cell(
                "s2", "FAM001", winner="FAM002", original="rescued", apex=8.55
            ),
            _duplicate_cell(
                "s3", "FAM001", winner="FAM003", original="rescued", apex=8.70
            ),
            _duplicate_cell(
                "s1", "FAM002", winner="FAM001", original="rescued", apex=8.40
            ),
            _cell("s2", "FAM002", "detected", 200.0, apex=8.55),
            _duplicate_cell(
                "s3", "FAM002", winner="FAM003", original="rescued", apex=8.70
            ),
            _duplicate_cell(
                "s1", "FAM003", winner="FAM001", original="rescued", apex=8.40
            ),
            _duplicate_cell(
                "s2", "FAM003", winner="FAM002", original="rescued", apex=8.55
            ),
            _cell("s3", "FAM003", "detected", 300.0, apex=8.70),
            *tuple(
                _duplicate_cell(
                    f"r{i}",
                    "FAM001",
                    winner="FAM002",
                    original="rescued",
                    apex=8.55,
                    area=50.0 + i,
                )
                for i in range(1, 7)
            ),
        ),
    )

    consolidated = consolidate_primary_family_rows(matrix, AlignmentConfig())
    decisions = build_production_decisions(consolidated, AlignmentConfig())
    winner = _feature_by_id(consolidated, "FAM002")

    assert tuple(owner.sample_stem for owner in winner.owners) == ("s1", "s2", "s3")
    assert decisions.row("FAM002").include_in_primary_matrix is True
    assert decisions.row("FAM002").identity_reason == "owner_complete_link"
    assert "weak_seed_backfill_dependency" not in decisions.row("FAM002").row_flags
    assert "weak_seed_tolerated" not in decisions.row("FAM002").row_flags
    assert winner.consolidation_state == "primary_winner"
    assert winner.consolidation_source_group_hypothesis_id == (
        "GROUP-FAM001;GROUP-FAM002;GROUP-FAM003"
    )
    winner_cells = {
        cell.sample_stem: cell
        for cell in consolidated.cells
        if cell.cluster_id == "FAM002"
    }
    assert winner_cells["s1"].consolidation_state == "moved_to_primary_winner"
    assert winner_cells["s1"].consolidation_source_group_hypothesis_id == (
        "GROUP-FAM001"
    )
    assert winner_cells["s1"].consolidation_winner_group_hypothesis_id == (
        "GROUP-FAM002"
    )


def test_consolidation_loser_audit_is_review_tsv_visible(tmp_path: Path):
    from xic_extractor.alignment.tsv_writer import write_alignment_review_tsv

    matrix = AlignmentMatrix(
        clusters=(
            _feature("FAM001", rt=8.40),
            _feature("FAM002", rt=8.55),
        ),
        sample_order=("s1", "s2"),
        cells=(
            _cell("s1", "FAM001", "detected", 100.0, apex=8.40),
            _duplicate_cell(
                "s2", "FAM001", winner="FAM002", original="rescued", apex=8.55
            ),
            _duplicate_cell(
                "s1", "FAM002", winner="FAM001", original="rescued", apex=8.40
            ),
            _cell("s2", "FAM002", "detected", 200.0, apex=8.55),
        ),
    )

    consolidated = consolidate_primary_family_rows(matrix, AlignmentConfig())
    decisions = build_production_decisions(consolidated, AlignmentConfig())
    rows = _review_rows_by_feature(
        write_alignment_review_tsv(
            tmp_path / "review.tsv",
            consolidated,
            production_decisions=decisions,
        ),
    )

    assert rows["FAM001"]["include_in_primary_matrix"] == "FALSE"
    assert "family_consolidation_loser" in rows["FAM001"]["row_flags"].split(";")
    assert (
        "primary_family_consolidation_loser;winner=FAM002"
        in rows["FAM001"]["family_evidence"]
    )
    assert rows["FAM001"]["consolidation_state"] == "primary_loser"
    assert rows["FAM001"]["consolidation_winner_group_hypothesis_id"] == (
        "GROUP-FAM002"
    )
    assert rows["FAM002"]["include_in_primary_matrix"] == "TRUE"
    assert rows["FAM002"]["consolidation_state"] == "primary_winner"
    assert "primary_family_consolidated;family_count=2" in (
        rows["FAM002"]["family_evidence"]
    )


def test_connected_components_preserves_sorted_traversal_and_skips_singletons() -> None:
    graph = {
        "B": {"C", "A"},
        "A": {"B"},
        "C": {"B"},
        "E": {"D"},
        "D": {"E"},
        "F": set(),
    }

    assert _connected_components(graph) == (("A", "B", "C"), ("D", "E"))


def _feature(
    feature_family_id: str,
    *,
    mz: float = 500.0,
    rt: float = 8.5,
    product_mz: float = 384.0,
    observed_loss: float = 116.0,
    neutral_loss_tag: str = "DNA_dR",
    evidence: str = "single_sample_local_owner",
) -> SimpleNamespace:
    return SimpleNamespace(
        feature_family_id=feature_family_id,
        neutral_loss_tag=neutral_loss_tag,
        family_center_mz=mz,
        family_center_rt=rt,
        family_product_mz=product_mz,
        family_observed_neutral_loss_da=observed_loss,
        has_anchor=True,
        event_cluster_ids=(f"OWN-{feature_family_id}",),
        event_member_count=1,
        evidence=evidence,
        review_only=False,
        group_hypothesis_id=f"GROUP-{feature_family_id}",
        public_family_id=feature_family_id,
        group_construction_role="successor_projection_adapter",
        group_delivery_role="successor_delivery_protocol",
        group_membership_source="cross_sample_peak_group_hypothesis",
        consolidation_state="not_consolidated",
        consolidation_winner_group_hypothesis_id="",
        consolidation_source_group_hypothesis_id="",
    )


def _owner_feature(feature_family_id: str, sample_stem: str, *, rt: float):
    return OwnerAlignedFeature(
        feature_family_id=feature_family_id,
        neutral_loss_tag="DNA_dR",
        family_center_mz=500.0,
        family_center_rt=rt,
        family_product_mz=384.0,
        family_observed_neutral_loss_da=116.0,
        has_anchor=True,
        owners=(_owner(feature_family_id, sample_stem, rt=rt),),
        evidence="single_sample_local_owner",
        group_hypothesis_id=f"GROUP-{feature_family_id}",
        public_family_id=feature_family_id,
        group_construction_role="successor_constructor",
        group_delivery_role="owner_aligned_feature_compatibility_facade",
        group_membership_source="owner_aligned_feature_successor_projection",
        consolidation_state="not_consolidated",
        consolidation_winner_group_hypothesis_id="",
        consolidation_source_group_hypothesis_id="",
    )


def _owner(
    feature_family_id: str,
    sample_stem: str,
    *,
    rt: float,
) -> SampleLocalMS1Owner:
    return SampleLocalMS1Owner(
        owner_id=f"OWN-{feature_family_id}",
        sample_stem=sample_stem,
        raw_file=f"{sample_stem}.raw",
        precursor_mz=500.0,
        owner_apex_rt=rt,
        owner_peak_start_rt=rt - 0.05,
        owner_peak_end_rt=rt + 0.05,
        owner_area=100.0,
        owner_height=100.0,
        primary_identity_event=IdentityEvent(
            candidate_id=f"{sample_stem}#{feature_family_id}",
            sample_stem=sample_stem,
            raw_file=f"{sample_stem}.raw",
            neutral_loss_tag="DNA_dR",
            precursor_mz=500.0,
            product_mz=384.0,
            observed_neutral_loss_da=116.0,
            seed_rt=rt,
            evidence_score=80,
            seed_event_count=2,
        ),
        supporting_events=(),
        identity_conflict=False,
        assignment_reason="test owner",
    )


def _cell(
    sample_stem: str,
    cluster_id: str,
    status: str,
    area: float,
    *,
    apex: float,
) -> AlignedCell:
    return AlignedCell(
        sample_stem=sample_stem,
        cluster_id=cluster_id,
        status=status,  # type: ignore[arg-type]
        area=area,
        apex_rt=apex,
        height=100.0,
        peak_start_rt=apex - 0.05,
        peak_end_rt=apex + 0.05,
        rt_delta_sec=0.0,
        trace_quality="clean",
        scan_support_score=0.9,
        source_candidate_id=f"{sample_stem}#{cluster_id}",
        source_raw_file=Path(f"{sample_stem}.raw"),
        reason=status,
        selected_integration=_integration(raw_area=area, asls_area=area),
        group_hypothesis_id=f"GROUP-{cluster_id}",
        public_family_id=cluster_id,
        group_construction_role="successor_projection_adapter",
        group_delivery_role="successor_delivery_protocol",
        group_membership_source="cross_sample_peak_group_hypothesis",
        gap_fill_state=(
            "observed_member" if status == "detected" else "gap_fill_rescued"
        ),
        gap_fill_reason=(
            "local_owner_detected"
            if status == "detected"
            else "group_centered_query_detected"
        ),
        missing_observation_state=(
            "observed" if status == "detected" else "queried_and_detected"
        ),
        group_claim_state="unclaimed_or_winner",
        consolidation_state="not_consolidated",
    )


def _duplicate_cell(
    sample_stem: str,
    cluster_id: str,
    *,
    winner: str,
    original: str,
    apex: float,
    area: float = 100.0,
) -> AlignedCell:
    source = _cell(sample_stem, cluster_id, original, area, apex=apex)
    return AlignedCell(
        sample_stem=source.sample_stem,
        cluster_id=source.cluster_id,
        status="duplicate_assigned",
        area=source.area,
        apex_rt=source.apex_rt,
        height=source.height,
        peak_start_rt=source.peak_start_rt,
        peak_end_rt=source.peak_end_rt,
        rt_delta_sec=source.rt_delta_sec,
        trace_quality=source.trace_quality,
        scan_support_score=source.scan_support_score,
        source_candidate_id=source.source_candidate_id,
        source_raw_file=source.source_raw_file,
        reason=(
            "duplicate MS1 peak claim; "
            f"winner={winner}; original_status={original}"
        ),
        selected_integration=source.selected_integration,
    )


def _integration(*, raw_area: float, asls_area: float) -> IntegrationResult:
    return IntegrationResult(
        rt_left_min=8.45,
        rt_apex_min=8.5,
        rt_right_min=8.55,
        raw_apex_rt_min=8.5,
        rt_width_min=0.1,
        height_raw=100.0,
        height_smoothed=100.0,
        area_raw_counts_seconds=raw_area,
        area_baseline_corrected=asls_area,
        baseline_type="asls",
        area_ms1_morphology=asls_area,
        ms1_morphology_area_source=MS1_MORPHOLOGY_AREA_SOURCE,
        boundary_sources=("test_primary_consolidation",),
    )


def _feature_by_id(matrix: AlignmentMatrix, feature_id: str):
    return next(
        cluster
        for cluster in matrix.clusters
        if getattr(cluster, "feature_family_id", None) == feature_id
    )


def _review_rows_by_feature(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return {
            row["feature_family_id"]: row
            for row in csv.DictReader(handle, delimiter="\t")
        }
