from __future__ import annotations

import csv
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import xic_extractor.alignment.primary_consolidation as primary_mod
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleGroupMemberSubject,
    CrossSampleNeighborhoodEdge,
    mint_cross_sample_group_identity,
)
from xic_extractor.alignment.matrix import AlignedCell, AlignmentMatrix
from xic_extractor.alignment.output_rows import row_id
from xic_extractor.alignment.owner_clustering import OwnerAlignedFeature
from xic_extractor.alignment.ownership_models import IdentityEvent, SampleLocalMS1Owner
from xic_extractor.alignment.primary_consolidation import (
    _connected_components,
    _Observation,
    _primary_consolidation_successor,
    _winner_cluster_id,
    consolidate_primary_family_rows,
)
from xic_extractor.alignment.production_decisions import build_production_decisions
from xic_extractor.peak_detection.hypotheses import IntegrationResult
from xic_extractor.peak_detection.ms1_morphology import MS1_MORPHOLOGY_AREA_SOURCE
from xic_extractor.processing_identity import (
    canonical_identity_basis_bytes,
    typed_identity_digest,
)

_TEST_PROCESSING_REQUEST_ID = typed_identity_digest(
    "xic.processing-request.v1:",
    canonical_identity_basis_bytes({"schema_version": "primary-consolidation-test"}),
)


def test_duplicate_claims_without_exact_member_closure_do_not_rewrite_groups():
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
    # Every duplicate cell names canonical groups, but the local owner records
    # only carry their own exact sample member.  A claim may not borrow an
    # unrelated sample member to make a primary membership mutation.
    assert consolidated == matrix


def test_primary_claims_ignore_reason_and_display_label_relabelling():
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

    baseline = consolidate_primary_family_rows(matrix, AlignmentConfig())
    relabelled = AlignmentMatrix(
        clusters=tuple(
            SimpleNamespace(
                **{
                    **vars(cluster),
                    "public_family_id": f"display-{cluster.feature_family_id}",
                }
            )
            for cluster in matrix.clusters
        ),
        sample_order=matrix.sample_order,
        cells=tuple(
            replace(
                cell,
                public_family_id=f"display-{cell.cluster_id}",
                reason="caller-display-label-is-not-a-primary-edge",
            )
            for cell in matrix.cells
        ),
    )

    relabelled_result = consolidate_primary_family_rows(
        relabelled,
        AlignmentConfig(),
    )

    assert baseline == matrix
    assert _canonical_matrix_snapshot(relabelled_result) == _canonical_matrix_snapshot(
        baseline
    )


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


def test_unclosed_primary_claim_does_not_move_stronger_same_sample_value():
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
            group_hypothesis_id=strong.group_hypothesis_id,
            public_family_id=strong.public_family_id,
            group_construction_role=strong.group_construction_role,
            group_delivery_role=strong.group_delivery_role,
            group_membership_source=strong.group_membership_source,
            gap_fill_state=strong.gap_fill_state,
            gap_fill_reason=strong.gap_fill_reason,
            missing_observation_state=strong.missing_observation_state,
            group_claim_state=strong.group_claim_state,
            consolidation_state=strong.consolidation_state,
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
    assert consolidated == matrix


def test_unclosed_primary_claim_does_not_replace_detected_same_apex_value():
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
    assert consolidated == matrix


def test_primary_duplicate_reason_rewrite_does_not_create_a_seed() -> None:
    matrix = AlignmentMatrix(
        clusters=(
            _feature("FAM001", rt=8.50),
            _feature("FAM002", rt=8.50),
        ),
        sample_order=("s1", "s2"),
        cells=(
            _cell("s1", "FAM001", "detected", 100.0, apex=8.50),
            replace(
                _duplicate_cell(
                    "s1",
                    "FAM002",
                    winner="FAM001",
                    original="rescued",
                    apex=8.50,
                ),
                reason="arbitrary rewritten prose must not become scientific input",
                claim_source_status="",
            ),
            _cell("s2", "FAM002", "detected", 200.0, apex=8.50),
        ),
    )

    assert consolidate_primary_family_rows(matrix, AlignmentConfig()) == matrix


def test_unclosed_near_duplicate_disposition_does_not_demote_a_group():
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
    assert consolidated == matrix


def test_primary_disposition_ingress_classifies_all_active_canonical_pairs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matrix = AlignmentMatrix(
        clusters=(
            _feature("FAM001", rt=8.00),
            _feature("FAM002", rt=8.05),
            _feature("FAM003", rt=8.10),
        ),
        sample_order=("FAM001", "FAM002", "FAM003"),
        cells=(
            _cell("FAM001", "FAM001", "detected", 100.0, apex=8.00),
            _cell("FAM002", "FAM002", "detected", 100.0, apex=8.05),
            _cell("FAM003", "FAM003", "detected", 100.0, apex=8.10),
        ),
    )
    calls: list[tuple[str, str]] = []
    original = primary_mod.classify_cross_sample_primary_disposition_edge

    def tracking_classifier(facts, *, config):
        calls.append(
            tuple(
                sorted(
                    (
                        facts.left_group.group_hypothesis_id,
                        facts.right_group.group_hypothesis_id,
                    )
                )
            )
        )
        return original(facts, config=config)

    monkeypatch.setattr(
        primary_mod,
        "classify_cross_sample_primary_disposition_edge",
        tracking_classifier,
    )

    assert consolidate_primary_family_rows(matrix, AlignmentConfig()) == matrix
    assert set(calls) == {
        tuple(
            sorted(
                (
                    matrix.clusters[left].group_hypothesis_id,
                    matrix.clusters[right].group_hypothesis_id,
                )
            )
        )
        for left, right in ((0, 1), (0, 2), (1, 2))
    }


def test_unclosed_primary_claim_does_not_flatten_source_owner_lineage():
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
    assert consolidated == matrix


def test_unclosed_primary_claim_emits_no_loser_render_adapter(tmp_path: Path):
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

    assert consolidated == matrix
    assert "family_consolidation_loser" not in rows["FAM001"]["row_flags"].split(";")
    assert rows["FAM001"]["consolidation_state"] == "not_consolidated"
    assert rows["FAM002"]["consolidation_state"] == "not_consolidated"


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


def test_primary_successor_same_sample_membership_is_atomic_no_application() -> None:
    canonical_sample_id = "xic.sample.v1:actual-s1"
    selected_member = _member(
        "selected",
        canonical_sample_id=canonical_sample_id,
    )
    removed_member = _member(
        "removed",
        canonical_sample_id=canonical_sample_id,
    )
    selected_identity = mint_cross_sample_group_identity(
        processing_request_id=_TEST_PROCESSING_REQUEST_ID,
        member_subjects=(selected_member,),
    )
    removed_identity = mint_cross_sample_group_identity(
        processing_request_id=_TEST_PROCESSING_REQUEST_ID,
        member_subjects=(removed_member,),
    )
    selected_cluster = _feature_with_identity(
        "FAM001",
        identity=selected_identity,
        sample_stem="s1",
    )
    removed_cluster = _feature_with_identity(
        "FAM002",
        identity=removed_identity,
        sample_stem="s1",
    )
    selected_cell = _cell("s1", "FAM001", "detected", 200.0, apex=8.5)

    application = _primary_consolidation_successor(
        ("FAM001", "FAM002"),
        clusters_by_id={
            "FAM001": selected_cluster,
            "FAM002": removed_cluster,
        },
        grouped_cells={"FAM001": (selected_cell,), "FAM002": ()},
        selected_by_sample={
            "s1": _Observation(
                cell=selected_cell,
                source_cluster_id="FAM001",
                original_status="detected",
            ),
        },
        edge_facts={},
    )

    assert application.status == "unassessable"
    assert application.successors == ()
    assert application.lineage == ()
    assert selected_cluster.member_subjects == (selected_member,)
    assert removed_cluster.member_subjects == (removed_member,)


def test_primary_successor_applies_unique_typed_same_sample_claim_closure() -> None:
    canonical_sample_id = "xic.sample.v1:actual-s1"
    winner_member = _member("winner", canonical_sample_id=canonical_sample_id)
    source_member = _member("source", canonical_sample_id=canonical_sample_id)
    winner_identity = mint_cross_sample_group_identity(
        processing_request_id=_TEST_PROCESSING_REQUEST_ID,
        member_subjects=(winner_member,),
    )
    source_identity = mint_cross_sample_group_identity(
        processing_request_id=_TEST_PROCESSING_REQUEST_ID,
        member_subjects=(source_member,),
    )
    winner_cluster = _feature_with_identity(
        "FAM001",
        identity=winner_identity,
        sample_stem="s1",
    )
    source_cluster = _feature_with_identity(
        "FAM002",
        identity=source_identity,
        sample_stem="s1",
    )
    source_cell = replace(
        _duplicate_cell(
            "s1",
            "FAM002",
            winner="FAM001",
            original="detected",
            apex=8.5,
        ),
        claim_source_group_hypothesis_id=source_identity.group_hypothesis_id,
        claim_winner_group_hypothesis_id=winner_identity.group_hypothesis_id,
    )
    matrix = AlignmentMatrix(
        clusters=(winner_cluster, source_cluster),
        cells=(source_cell,),
        sample_order=("s1",),
    )
    edge_facts = primary_mod._duplicate_claim_edges(
        matrix,
        {"FAM001": winner_cluster, "FAM002": source_cluster},
        AlignmentConfig(),
    )

    application = _primary_consolidation_successor(
        ("FAM001", "FAM002"),
        clusters_by_id={"FAM001": winner_cluster, "FAM002": source_cluster},
        grouped_cells={"FAM001": (), "FAM002": (source_cell,)},
        selected_by_sample={
            "s1": _Observation(
                cell=source_cell,
                source_cluster_id="FAM002",
                original_status="detected",
            )
        },
        edge_facts=edge_facts,
    )

    assert tuple(edge.disposition for edge in edge_facts.values()) == ("eligible",)
    assert application.status == "applied"
    assert application.successors[0].member_subjects == (winner_member,)
    # The kept member is the unchanged winner identity.  The removed source
    # member cannot invent a forwarding lineage edge, and no self-edge exists.
    assert application.lineage == ()


def test_primary_successor_keeps_complete_owner_membership_not_selected_value() -> None:
    left = _feature("FAM001", rt=8.50)
    right = _feature("FAM002", rt=8.55)
    selected_cell = _cell("FAM001", "FAM001", "detected", 100.0, apex=8.50)
    left_member = _identity("FAM001").member_subjects[0]
    right_member = _identity("FAM002").member_subjects[0]

    application = _primary_consolidation_successor(
        ("FAM001", "FAM002"),
        clusters_by_id={"FAM001": left, "FAM002": right},
        grouped_cells={"FAM001": (selected_cell,), "FAM002": ()},
        selected_by_sample={
            "FAM001": _Observation(
                cell=selected_cell,
                source_cluster_id="FAM001",
                original_status="detected",
            )
        },
        edge_facts={
            ("FAM001", "FAM002"): CrossSampleNeighborhoodEdge(
                left_member_subject_id=left_member.member_subject_id,
                right_member_subject_id=right_member.member_subject_id,
                disposition="eligible",
            )
        },
    )

    assert application.status == "applied"
    assert application.successors[0].member_subjects == tuple(
        sorted((left_member, right_member), key=lambda member: member.basis_bytes)
    )


def test_primary_winner_tiebreak_ignores_display_label_permutation() -> None:
    def winner_group_id(*, left_label: str, right_label: str) -> str:
        left = _feature("canonical-left")
        right = _feature("canonical-right")
        left.feature_family_id = left_label
        left.public_family_id = left_label
        right.feature_family_id = right_label
        right.public_family_id = right_label
        clusters_by_id = {left_label: left, right_label: right}
        grouped_cells = {
            left_label: (_cell("s1", left_label, "detected", 100.0, apex=8.5),),
            right_label: (_cell("s1", right_label, "detected", 100.0, apex=8.5),),
        }
        winner_id = _winner_cluster_id(
            (left_label, right_label),
            clusters_by_id,
            grouped_cells,
        )
        return clusters_by_id[winner_id].group_hypothesis_id

    assert winner_group_id(left_label="FAM-A", right_label="FAM-Z") == (
        winner_group_id(left_label="FAM-Z", right_label="FAM-A")
    )


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
    identity = _identity(feature_family_id)
    owners = (
        replace(
            _owner(feature_family_id, feature_family_id, rt=rt),
            cross_sample_member_subject=identity.member_subjects[0],
        ),
    )
    return SimpleNamespace(
        feature_family_id=feature_family_id,
        neutral_loss_tag=neutral_loss_tag,
        family_center_mz=mz,
        family_center_rt=rt,
        family_product_mz=product_mz,
        family_observed_neutral_loss_da=observed_loss,
        has_anchor=True,
        owners=owners,
        event_cluster_ids=(f"OWN-{feature_family_id}",),
        event_member_count=1,
        evidence=evidence,
        review_only=False,
        group_hypothesis_id=identity.group_hypothesis_id,
        public_family_id=feature_family_id,
        processing_request_id=identity.processing_request_id,
        member_subjects=identity.member_subjects,
        identity_basis_bytes=identity.basis_bytes,
        disposition=identity.disposition,
        group_construction_role="successor_projection_adapter",
        group_delivery_role="successor_delivery_protocol",
        group_membership_source="cross_sample_peak_group_hypothesis",
        consolidation_state="not_consolidated",
        consolidation_winner_group_hypothesis_id="",
        consolidation_source_group_hypothesis_id="",
    )


def _identity(feature_family_id: str):
    member = _member(feature_family_id)
    return mint_cross_sample_group_identity(
        processing_request_id=_TEST_PROCESSING_REQUEST_ID,
        member_subjects=(member,),
    )


def _member(
    feature_family_id: str,
    *,
    canonical_sample_id: str | None = None,
) -> CrossSampleGroupMemberSubject:
    subject_id = f"xic.peak-observation-content.v1:primary-{feature_family_id}"
    sample_id = canonical_sample_id or f"xic.sample.v1:primary-{feature_family_id}"
    basis = canonical_identity_basis_bytes(
        {
            "schema_version": "group_member_subject_v1",
            "processing_request_id": _TEST_PROCESSING_REQUEST_ID,
            "canonical_sample_identity_id": sample_id,
            "subject": {"kind": "physical_observation", "id": subject_id},
        }
    )
    return CrossSampleGroupMemberSubject(
        member_subject_id=typed_identity_digest("xic.group-member-subject.v1:", basis),
        processing_request_id=_TEST_PROCESSING_REQUEST_ID,
        canonical_sample_identity_id=sample_id,
        subject_kind="physical_observation",
        subject_id=subject_id,
        physical_observation_ids=(subject_id,),
        basis_bytes=basis,
    )


def _feature_with_identity(
    feature_family_id: str,
    *,
    identity,
    sample_stem: str,
) -> SimpleNamespace:
    feature = _feature(feature_family_id)
    owner = replace(
        feature.owners[0],
        sample_stem=sample_stem,
        raw_file=f"{sample_stem}.raw",
        cross_sample_member_subject=identity.member_subjects[0],
    )
    feature.owners = (owner,)
    feature.group_hypothesis_id = identity.group_hypothesis_id
    feature.processing_request_id = identity.processing_request_id
    feature.member_subjects = identity.member_subjects
    feature.identity_basis_bytes = identity.basis_bytes
    feature.disposition = identity.disposition
    return feature


def _owner_feature(feature_family_id: str, sample_stem: str, *, rt: float):
    identity = _identity(feature_family_id)
    return OwnerAlignedFeature(
        feature_family_id=feature_family_id,
        neutral_loss_tag="DNA_dR",
        family_center_mz=500.0,
        family_center_rt=rt,
        family_product_mz=384.0,
        family_observed_neutral_loss_da=116.0,
        has_anchor=True,
        owners=(
            replace(
                _owner(feature_family_id, sample_stem, rt=rt),
                cross_sample_member_subject=identity.member_subjects[0],
            ),
        ),
        evidence="single_sample_local_owner",
        group_hypothesis_id=identity.group_hypothesis_id,
        public_family_id=feature_family_id,
        processing_request_id=identity.processing_request_id,
        member_subjects=identity.member_subjects,
        identity_basis_bytes=identity.basis_bytes,
        disposition=identity.disposition,
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
        group_hypothesis_id=_identity(cluster_id).group_hypothesis_id,
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
        claim_winner_group_hypothesis_id="",
        claim_source_group_hypothesis_id=_identity(cluster_id).group_hypothesis_id,
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
        group_hypothesis_id=source.group_hypothesis_id,
        public_family_id=source.public_family_id,
        group_construction_role=source.group_construction_role,
        group_delivery_role=source.group_delivery_role,
        group_membership_source=source.group_membership_source,
        gap_fill_state=source.gap_fill_state,
        gap_fill_reason=source.gap_fill_reason,
        missing_observation_state=source.missing_observation_state,
        group_claim_state=source.group_claim_state,
        claim_source_status=original,
        claim_winner_group_hypothesis_id=_identity(winner).group_hypothesis_id,
        claim_source_group_hypothesis_id=_identity(cluster_id).group_hypothesis_id,
        consolidation_state=source.consolidation_state,
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


def _canonical_matrix_snapshot(matrix: AlignmentMatrix) -> tuple[object, ...]:
    """Identity and value surface; deliberately excludes display-only text."""

    return (
        tuple(
            (
                row_id(cluster),
                getattr(cluster, "group_hypothesis_id", ""),
                getattr(cluster, "processing_request_id", ""),
                tuple(
                    member.member_subject_id
                    for member in getattr(cluster, "member_subjects", ())
                ),
                getattr(cluster, "identity_basis_bytes", b""),
                getattr(cluster, "disposition", ""),
                tuple(getattr(cluster, "cross_sample_lineage", ())),
                getattr(cluster, "consolidation_state", ""),
            )
            for cluster in matrix.clusters
        ),
        tuple(
            (
                cell.sample_stem,
                cell.cluster_id,
                cell.status,
                cell.area,
                cell.apex_rt,
                cell.group_hypothesis_id,
                cell.claim_source_group_hypothesis_id,
                cell.claim_winner_group_hypothesis_id,
                cell.consolidation_state,
            )
            for cell in matrix.cells
        ),
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
