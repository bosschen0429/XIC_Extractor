from __future__ import annotations

import csv
from pathlib import Path
from types import SimpleNamespace

import pytest

from xic_extractor.alignment.claim_registry import apply_ms1_peak_claim_registry
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleContractError,
    CrossSampleGroupMemberSubject,
    mint_cross_sample_group_identity,
)
from xic_extractor.alignment.matrix import AlignedCell, AlignmentMatrix
from xic_extractor.processing_identity import (
    canonical_identity_basis_bytes,
    typed_identity_digest,
)

_REQUEST_ID = typed_identity_digest(
    "xic.processing-request.v1:",
    canonical_identity_basis_bytes({"schema_version": "claim-registry-test"}),
)
_SAMPLES = ("sample-a", "sample-b", "sample-c")


def test_claim_registry_requires_owner_backed_physical_identity_for_exact_peak(
) -> None:
    matrix = _matrix(
        clusters=(
            _feature("FAM000001", detected_count=10),
            _feature("FAM000002", detected_count=2),
        ),
        cells=(
            _cell("sample-a", "FAM000001", "detected"),
            _cell("sample-a", "FAM000002", "rescued"),
        ),
    )

    result = apply_ms1_peak_claim_registry(matrix, AlignmentConfig())
    cells = _cells_by_feature(result)

    assert cells["FAM000001"].status == "detected"
    assert cells["FAM000001"].area == 1000.0
    assert cells["FAM000002"].status == "rescued"
    assert cells["FAM000002"].area == 1000.0
    assert cells["FAM000002"].apex_rt == 8.5
    assert cells["FAM000002"].peak_start_rt == 8.4
    assert cells["FAM000002"].peak_end_rt == 8.6
    assert cells["FAM000002"].group_claim_state == ""
    assert cells["FAM000002"].claim_winner_group_hypothesis_id == ""
    assert cells["FAM000002"].claim_source_group_hypothesis_id == ""


def test_claim_registry_does_not_claim_cross_tag_exact_geometry_without_physical_id(
) -> None:
    matrix = _matrix(
        clusters=(
            _feature("FAM000001", neutral_loss_tag="DNA_dR", detected_count=10),
            _feature("FAM000002", neutral_loss_tag="DNA_base", detected_count=2),
        ),
        cells=(
            _cell("sample-a", "FAM000001", "detected"),
            _cell("sample-a", "FAM000002", "detected"),
        ),
    )

    result = apply_ms1_peak_claim_registry(matrix, AlignmentConfig())

    assert _cells_by_feature(result)["FAM000002"].status == "detected"


def test_claim_registry_support_does_not_replace_owner_closed_physical_identity(
) -> None:
    matrix = _matrix(
        clusters=(
            _feature("FAM000001", detected_count=40),
            _feature("FAM000002", detected_count=1),
        ),
        cells=(
            _cell("sample-a", "FAM000001", "rescued"),
            _cell("sample-a", "FAM000002", "detected"),
            _cell("sample-b", "FAM000001", "detected"),
            _cell("sample-c", "FAM000001", "detected"),
        ),
    )

    result = apply_ms1_peak_claim_registry(matrix, AlignmentConfig())
    cells = _cells_by_feature_sample(result)

    assert cells[("FAM000001", "sample-a")].status == "rescued"
    assert cells[("FAM000002", "sample-a")].status == "detected"


def test_claim_registry_detected_status_does_not_create_physical_identity_claim(
) -> None:
    matrix = _matrix(
        clusters=(
            _feature("FAM000001", detected_count=5),
            _feature("FAM000002", detected_count=5),
        ),
        cells=(
            _cell("sample-a", "FAM000001", "rescued"),
            _cell("sample-a", "FAM000002", "detected"),
        ),
    )

    result = apply_ms1_peak_claim_registry(matrix, AlignmentConfig())
    cells = _cells_by_feature(result)

    assert cells["FAM000001"].status == "rescued"
    assert cells["FAM000002"].status == "detected"


def test_claim_registry_review_only_feature_cannot_create_unclosed_exact_claim(
) -> None:
    matrix = _matrix(
        clusters=(
            _feature("FAM000001", detected_count=1),
            _feature("FAM000002", detected_count=99, review_only=True),
        ),
        cells=(
            _cell("sample-a", "FAM000001", "rescued"),
            _cell("sample-a", "FAM000002", "detected"),
        ),
    )

    result = apply_ms1_peak_claim_registry(matrix, AlignmentConfig())
    cells = _cells_by_feature(result)

    assert cells["FAM000001"].status == "rescued"
    assert cells["FAM000002"].status == "detected"


def test_claim_registry_does_not_reopen_distinct_physical_exact_rounding() -> None:
    matrix = _matrix(
        clusters=(
            _feature("FAM000001", mz=500.0, detected_count=10),
            _feature("FAM000002", mz=500.0, detected_count=9),
        ),
        cells=(
            _cell("sample-a", "FAM000001", "detected"),
            _cell("sample-a", "FAM000002", "detected"),
        ),
    )

    result = apply_ms1_peak_claim_registry(
        matrix,
        AlignmentConfig(duplicate_fold_ppm=5.0),
    )

    assert _cells_by_feature(result)["FAM000002"].status == "detected"


def test_claim_registry_keeps_distinct_peak_window_claims() -> None:
    matrix = _matrix(
        clusters=(
            _feature("FAM000001", mz=500.0, detected_count=10),
            _feature("FAM000002", mz=500.01, detected_count=9),
            _feature("FAM000003", mz=500.0, detected_count=8),
        ),
        cells=(
            _cell("sample-a", "FAM000001", "detected", start=8.4, end=8.6),
            _cell(
                "sample-a",
                "FAM000002",
                "detected",
                apex=8.7,
                start=8.6,
                end=8.8,
            ),
            _cell("sample-a", "FAM000003", "detected", start=8.58, end=8.78),
        ),
    )

    result = apply_ms1_peak_claim_registry(
        matrix,
        AlignmentConfig(
            duplicate_fold_ppm=5.0,
            owner_window_overlap_fraction=0.5,
        ),
    )

    assert {cell.status for cell in result.cells} == {"detected"}


def test_claim_registry_complete_link_prevents_chain_claiming() -> None:
    matrix = _matrix(
        clusters=(
            _feature("FAM000001", mz=500.000, detected_count=30),
            _feature("FAM000002", mz=500.001, detected_count=20),
            _feature("FAM000003", mz=500.002, detected_count=10),
        ),
        cells=(
            _cell("sample-a", "FAM000001", "detected", apex=8.500),
            _cell("sample-a", "FAM000002", "detected", apex=8.525),
            _cell("sample-a", "FAM000003", "detected", apex=8.550),
        ),
    )

    result = apply_ms1_peak_claim_registry(
        matrix,
        AlignmentConfig(owner_apex_close_sec=2.0),
    )
    cells = _cells_by_feature(result)

    assert cells["FAM000001"].status == "detected"
    assert cells["FAM000002"].status == "duplicate_assigned"
    assert cells["FAM000003"].status == "detected"


def test_claim_registry_ignores_incomplete_peak_claims() -> None:
    matrix = _matrix(
        clusters=(
            _feature("FAM000001", detected_count=10),
            _feature("FAM000002", detected_count=9),
        ),
        cells=(
            _cell("sample-a", "FAM000001", "detected"),
            _cell("sample-a", "FAM000002", "detected", area=None),
        ),
    )

    result = apply_ms1_peak_claim_registry(matrix, AlignmentConfig())

    assert result is matrix
    assert [cell.status for cell in result.cells] == ["detected", "detected"]


def test_claim_candidates_do_not_inherit_stale_cluster_stats_or_review_state() -> None:
    def matrix(*, order: tuple[str, ...], include_trailing: bool) -> AlignmentMatrix:
        clusters_by_id = {
            "FAM000001": _feature(
                "FAM000001",
                detected_count=2,
                event_member_count=2,
                event_cluster_count=2,
            ),
            "FAM000002": _feature(
                "FAM000002",
                detected_count=1,
                event_member_count=99,
                event_cluster_count=99,
            ),
            "FAM000003": _feature(
                "FAM000003",
                detected_count=3,
                event_member_count=3,
                event_cluster_count=3,
                review_only=True,
            ),
        }
        clusters = tuple(clusters_by_id[feature_id] for feature_id in order)
        if include_trailing:
            clusters = (*clusters, _feature("FAM000004", mz=700.0, detected_count=0))
        return _matrix(
            clusters=clusters,
            cells=(
                _cell("sample-a", "FAM000001", "detected", apex=8.500),
                _cell("sample-b", "FAM000001", "detected", apex=8.500),
                _cell("sample-a", "FAM000002", "detected", apex=8.501),
                _cell("sample-a", "FAM000003", "detected", apex=8.502),
                _cell("sample-b", "FAM000003", "detected", apex=8.502),
                _cell("sample-c", "FAM000003", "detected"),
            ),
        )

    def assignments(result: AlignmentMatrix) -> tuple[tuple[str, str, str, str], ...]:
        return tuple(
            (
                cell.cluster_id,
                cell.sample_stem,
                cell.status,
                cell.claim_winner_group_hypothesis_id,
            )
            for cell in result.cells
        )

    baseline = apply_ms1_peak_claim_registry(
        matrix(order=("FAM000001", "FAM000002", "FAM000003"), include_trailing=False),
        AlignmentConfig(),
    )
    reversed_order = apply_ms1_peak_claim_registry(
        matrix(order=("FAM000003", "FAM000002", "FAM000001"), include_trailing=False),
        AlignmentConfig(),
    )
    with_unrelated_trailing = apply_ms1_peak_claim_registry(
        matrix(order=("FAM000001", "FAM000002", "FAM000003"), include_trailing=True),
        AlignmentConfig(),
    )

    assert assignments(baseline) == assignments(reversed_order)
    assert assignments(baseline) == assignments(with_unrelated_trailing)
    cells = _cells_by_feature_sample(baseline)
    assert cells[("FAM000001", "sample-a")].status == "detected"
    assert cells[("FAM000002", "sample-a")].status == "duplicate_assigned"
    assert cells[("FAM000003", "sample-a")].status == "duplicate_assigned"


def test_claim_registry_filters_render_only_and_unclosed_repair_cells() -> None:
    visible = _feature("FAM000001", detected_count=10)
    historical = _feature("FAM000002", detected_count=9)
    historical.cross_sample_state = "render_only"
    historical.group_hypothesis_id = ""
    historical.processing_request_id = ""
    historical.member_subjects = ()
    historical.identity_basis_bytes = b""
    unclosed_repair = _feature("FAM000003", detected_count=8)
    unclosed_repair.owners = ()
    matrix = _matrix(
        clusters=(visible, historical, unclosed_repair),
        cells=(
            _cell("sample-a", "FAM000001", "detected"),
            _cell("sample-a", "FAM000002", "rescued"),
            _cell(
                "sample-a",
                "FAM000003",
                "rescued",
                gap_fill_state="gap_fill_rescued",
                missing_observation_state="queried_and_detected",
            ),
        ),
    )

    result = apply_ms1_peak_claim_registry(matrix, AlignmentConfig())

    assert result is matrix
    assert [cell.status for cell in result.cells] == [
        "detected",
        "rescued",
        "rescued",
    ]


def test_claim_ingress_rejects_duplicate_active_canonical_population() -> None:
    visible = _feature("FAM000001", detected_count=10)
    duplicate = _feature("FAM000002", detected_count=9)
    duplicate.group_hypothesis_id = visible.group_hypothesis_id
    duplicate.processing_request_id = visible.processing_request_id
    duplicate.member_subjects = visible.member_subjects
    duplicate.identity_basis_bytes = visible.identity_basis_bytes
    duplicate.disposition = visible.disposition
    matrix = _matrix(
        clusters=(visible, duplicate),
        cells=(_cell("sample-a", "FAM000001", "detected"),),
    )

    with pytest.raises(CrossSampleContractError, match="duplicates a group identity"):
        apply_ms1_peak_claim_registry(matrix, AlignmentConfig())


def test_claim_ingress_rejects_forged_active_identity_basis() -> None:
    forged = _feature("FAM000001", detected_count=10)
    forged.identity_basis_bytes = forged.identity_basis_bytes + b" "
    matrix = _matrix(
        clusters=(forged,),
        cells=(_cell("sample-a", "FAM000001", "detected"),),
    )

    with pytest.raises(CrossSampleContractError, match="canonical identity"):
        apply_ms1_peak_claim_registry(matrix, AlignmentConfig())


def test_claim_registry_claims_are_sample_local() -> None:
    matrix = _matrix(
        clusters=(
            _feature("FAM000001", detected_count=10),
            _feature("FAM000002", detected_count=1),
        ),
        cells=(
            _cell("sample-a", "FAM000001", "detected"),
            _cell("sample-b", "FAM000002", "detected"),
        ),
    )

    result = apply_ms1_peak_claim_registry(matrix, AlignmentConfig())

    assert result is matrix
    assert {cell.status for cell in result.cells} == {"detected"}


def test_claim_registry_all_review_only_exact_geometry_remains_value_only() -> None:
    matrix = _matrix(
        clusters=(
            _feature("FAM000001", detected_count=10, review_only=True),
            _feature("FAM000002", detected_count=9, review_only=True),
        ),
        cells=(
            _cell("sample-a", "FAM000001", "detected"),
            _cell("sample-a", "FAM000002", "rescued"),
        ),
    )

    result = apply_ms1_peak_claim_registry(matrix, AlignmentConfig())
    cells = _cells_by_feature(result)

    assert cells["FAM000001"].status == "detected"
    assert cells["FAM000001"].group_claim_state == ""
    assert cells["FAM000001"].claim_winner_group_hypothesis_id == ""
    assert cells["FAM000001"].claim_source_group_hypothesis_id == ""
    assert cells["FAM000002"].status == "rescued"
    assert cells["FAM000002"].group_claim_state == ""


def test_claim_registry_unclosed_exact_geometry_keeps_cells_tsv_values(
    tmp_path: Path,
) -> None:
    from xic_extractor.alignment.tsv_writer import write_alignment_cells_tsv

    matrix = _matrix(
        clusters=(
            _feature("FAM000001", detected_count=10),
            _feature("FAM000002", detected_count=2),
        ),
        cells=(
            _cell("sample-a", "FAM000001", "detected"),
            _cell("sample-a", "FAM000002", "rescued"),
        ),
    )

    result = apply_ms1_peak_claim_registry(matrix, AlignmentConfig())
    rows = _rows_by_feature(write_alignment_cells_tsv(tmp_path / "cells.tsv", result))

    assert rows["FAM000001"]["status"] == "detected"
    assert rows["FAM000002"]["status"] == "rescued"
    assert rows["FAM000002"]["area"] == "1000"
    assert rows["FAM000002"]["apex_rt"] == "8.5"
    assert rows["FAM000002"]["peak_start_rt"] == "8.4"
    assert rows["FAM000002"]["peak_end_rt"] == "8.6"
    assert rows["FAM000002"]["group_claim_state"] == ""
    assert rows["FAM000002"]["claim_winner_group_hypothesis_id"] == ""
    assert rows["FAM000002"]["claim_source_group_hypothesis_id"] == ""


def _matrix(*, clusters, cells) -> AlignmentMatrix:
    return AlignmentMatrix(
        clusters=clusters,
        cells=cells,
        sample_order=tuple(dict.fromkeys(cell.sample_stem for cell in cells)),
    )


def _feature(
    feature_id: str,
    *,
    mz: float = 500.0,
    neutral_loss_tag: str = "DNA_dR",
    detected_count: int,
    event_member_count: int | None = None,
    event_cluster_count: int | None = None,
    review_only: bool = False,
):
    event_cluster_count = (
        detected_count if event_cluster_count is None else event_cluster_count
    )
    members = tuple(_member(feature_id, sample) for sample in _SAMPLES)
    identity = mint_cross_sample_group_identity(
        processing_request_id=_REQUEST_ID,
        member_subjects=members,
    )
    return SimpleNamespace(
        feature_family_id=feature_id,
        neutral_loss_tag=neutral_loss_tag,
        family_center_mz=mz,
        family_center_rt=8.5,
        family_product_mz=384.0,
        family_observed_neutral_loss_da=116.0,
        has_anchor=not review_only,
        event_cluster_ids=tuple(
            f"OWN-{feature_id}-{index}" for index in range(event_cluster_count)
        ),
        event_member_count=(
            detected_count if event_member_count is None else event_member_count
        ),
        evidence="owner_complete_link",
        review_only=review_only,
        owners=tuple(
            SimpleNamespace(sample_stem=sample, cross_sample_member_subject=member)
            for sample, member in zip(_SAMPLES, members, strict=True)
        ),
        group_hypothesis_id=identity.group_hypothesis_id,
        public_family_id=feature_id,
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


def _member(feature_id: str, sample_stem: str) -> CrossSampleGroupMemberSubject:
    subject_id = f"xic.peak-observation-content.v1:claim-{feature_id}-{sample_stem}"
    sample_id = f"xic.sample.v1:claim-{sample_stem}"
    basis = canonical_identity_basis_bytes(
        {
            "schema_version": "group_member_subject_v1",
            "processing_request_id": _REQUEST_ID,
            "canonical_sample_identity_id": sample_id,
            "subject": {"kind": "physical_observation", "id": subject_id},
        }
    )
    return CrossSampleGroupMemberSubject(
        member_subject_id=typed_identity_digest("xic.group-member-subject.v1:", basis),
        processing_request_id=_REQUEST_ID,
        canonical_sample_identity_id=sample_id,
        subject_kind="physical_observation",
        subject_id=subject_id,
        physical_observation_ids=(subject_id,),
        basis_bytes=basis,
    )


def _group_id(feature_id: str) -> str:
    return mint_cross_sample_group_identity(
        processing_request_id=_REQUEST_ID,
        member_subjects=tuple(_member(feature_id, sample) for sample in _SAMPLES),
    ).group_hypothesis_id


def _cell(
    sample: str,
    feature_id: str,
    status: str,
    *,
    area: float | None = 1000.0,
    apex: float = 8.5,
    start: float = 8.4,
    end: float = 8.6,
    gap_fill_state: str = "",
    missing_observation_state: str = "",
) -> AlignedCell:
    return AlignedCell(
        sample_stem=sample,
        cluster_id=feature_id,
        status=status,  # type: ignore[arg-type]
        area=area,
        apex_rt=apex,
        height=100.0,
        peak_start_rt=start,
        peak_end_rt=end,
        rt_delta_sec=0.0,
        trace_quality=status,
        scan_support_score=None,
        source_candidate_id=f"{sample}#{feature_id}",
        source_raw_file=None,
        reason=f"{status} reason",
        gap_fill_state=gap_fill_state,
        missing_observation_state=missing_observation_state,
    )


def _cells_by_feature(matrix: AlignmentMatrix) -> dict[str, AlignedCell]:
    return {cell.cluster_id: cell for cell in matrix.cells}


def _cells_by_feature_sample(
    matrix: AlignmentMatrix,
) -> dict[tuple[str, str], AlignedCell]:
    return {(cell.cluster_id, cell.sample_stem): cell for cell in matrix.cells}


def _rows_by_feature(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return {
            row["feature_family_id"]: row
            for row in csv.DictReader(handle, delimiter="\t")
        }
