from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from xic_extractor.alignment import claim_registry as claim_registry_module
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleGroupMemberSubject,
    mint_cross_sample_group_identity,
)
from xic_extractor.alignment.matrix import AlignedCell, AlignmentMatrix
from xic_extractor.processing_identity import (
    canonical_identity_basis_bytes,
    typed_identity_digest,
)

_TEST_PROCESSING_REQUEST_ID = typed_identity_digest(
    "xic.processing-request.v1:",
    canonical_identity_basis_bytes({"schema_version": "claim-registry-hot-path-test"}),
)
_CLAIM_SAMPLES = ("sample-a", *(f"sample-{index:02d}" for index in range(12)))


@dataclass(frozen=True)
class ClaimRegistryOperationCounts:
    exact_peak_key_calls: int
    compatible_claim_calls: int
    group_sort_key_calls: int
    winner_sort_key_calls: int
    duplicate_replacement_calls: int


def test_claim_registry_hot_path_many_samples_single_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matrix = _matrix(
        clusters=tuple(
            _feature(f"FAM{index:06d}", mz=500.0 + index)
            for index in range(12)
        ),
        cells=tuple(
            _cell(f"sample-{index:02d}", f"FAM{index:06d}", "detected")
            for index in range(12)
        ),
    )

    result, counts = _apply_with_operation_counts(matrix, monkeypatch)

    assert result is matrix
    assert [cell.status for cell in result.cells] == ["detected"] * 12
    assert counts == ClaimRegistryOperationCounts(
        exact_peak_key_calls=0,
        compatible_claim_calls=0,
        group_sort_key_calls=0,
        winner_sort_key_calls=0,
        duplicate_replacement_calls=0,
    )


def test_claim_registry_reuses_family_stats_across_sample_cells(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matrix = _matrix(
        clusters=(_feature("FAM000001"),),
        cells=tuple(
            _cell(f"sample-{index:02d}", "FAM000001", "detected")
            for index in range(12)
        ),
    )
    original_count_status = claim_registry_module.count_status
    count_status_calls = 0

    def count_status(cells: Any, status: str) -> int:
        nonlocal count_status_calls
        count_status_calls += 1
        return original_count_status(cells, status)

    monkeypatch.setattr(claim_registry_module, "count_status", count_status)

    result = claim_registry_module.apply_ms1_peak_claim_registry(
        matrix,
        AlignmentConfig(),
    )

    assert result is matrix
    assert count_status_calls == 1


def test_claim_registry_hot_path_one_sample_many_compatible_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matrix = _matrix(
        clusters=tuple(
            _feature(
                f"FAM{index:06d}",
                mz=500.0 + index * 0.0001,
                detected_count=12 - index,
            )
            for index in range(6)
        ),
        cells=tuple(
            _cell(
                "sample-a",
                f"FAM{index:06d}",
                "detected",
                apex=8.5 + index * 0.001,
                start=8.4 + index * 0.001,
                end=8.6 + index * 0.001,
                area=1000.0 + index,
            )
            for index in range(6)
        ),
    )

    result, counts = _apply_with_operation_counts(matrix, monkeypatch)
    cells = {cell.cluster_id: cell for cell in result.cells}

    assert cells["FAM000000"].status == "detected"
    assert {
        cell.status for feature_id, cell in cells.items() if feature_id != "FAM000000"
    } == {"duplicate_assigned"}
    assert counts == ClaimRegistryOperationCounts(
        exact_peak_key_calls=12,
        compatible_claim_calls=15,
        group_sort_key_calls=5,
        winner_sort_key_calls=12,
        duplicate_replacement_calls=1,
    )


def test_claim_registry_hot_path_sparse_mz_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matrix = _matrix(
        clusters=tuple(
            _feature(f"FAM{index:06d}", mz=500.0 + index * 0.01)
            for index in range(10)
        ),
        cells=tuple(
            _cell(
                "sample-a",
                f"FAM{index:06d}",
                "detected",
                apex=8.5 + index * 0.001,
                area=1000.0 + index,
            )
            for index in range(10)
        ),
    )

    result, counts = _apply_with_operation_counts(matrix, monkeypatch)

    assert result is matrix
    assert [cell.status for cell in result.cells] == ["detected"] * 10
    assert counts == ClaimRegistryOperationCounts(
        exact_peak_key_calls=20,
        compatible_claim_calls=0,
        group_sort_key_calls=0,
        winner_sort_key_calls=0,
        duplicate_replacement_calls=0,
    )


def test_claim_registry_hot_path_exact_duplicate_outside_fuzzy_mz_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matrix = _matrix(
        clusters=(
            _feature("FAM000001", mz=500.0, detected_count=10),
            _feature("FAM000002", mz=500.01, detected_count=9),
        ),
        cells=(
            _cell("sample-a", "FAM000001", "detected"),
            _cell("sample-a", "FAM000002", "detected"),
        ),
    )

    result, counts = _apply_with_operation_counts(
        matrix,
        monkeypatch,
        config=AlignmentConfig(duplicate_fold_ppm=5.0),
    )
    cells = {cell.cluster_id: cell for cell in result.cells}

    assert cells["FAM000001"].status == "detected"
    assert cells["FAM000002"].status == "detected"
    assert counts == ClaimRegistryOperationCounts(
        exact_peak_key_calls=4,
        compatible_claim_calls=1,
        group_sort_key_calls=0,
        winner_sort_key_calls=0,
        duplicate_replacement_calls=0,
    )


def test_claim_winner_is_invariant_to_display_label_permutation() -> None:
    def winner_group_id(*, left_label: str, right_label: str) -> str:
        left = _feature("canonical-left", detected_count=10)
        right = _feature("canonical-right", detected_count=10)
        left.feature_family_id = left_label
        left.public_family_id = left_label
        right.feature_family_id = right_label
        right.public_family_id = right_label
        result = claim_registry_module.apply_ms1_peak_claim_registry(
            _matrix(
                clusters=(left, right),
                cells=(
                    _cell("sample-a", left_label, "detected"),
                    _cell("sample-a", right_label, "detected"),
                ),
            ),
            AlignmentConfig(),
        )
        winner_cell = next(cell for cell in result.cells if cell.status == "detected")
        return next(
            cluster.group_hypothesis_id
            for cluster in result.clusters
            if cluster.feature_family_id == winner_cell.cluster_id
        )

    assert winner_group_id(left_label="FAM-A", right_label="FAM-Z") == (
        winner_group_id(left_label="FAM-Z", right_label="FAM-A")
    )


def _apply_with_operation_counts(
    matrix: AlignmentMatrix,
    monkeypatch: pytest.MonkeyPatch,
    *,
    config: AlignmentConfig | None = None,
) -> tuple[AlignmentMatrix, ClaimRegistryOperationCounts]:
    counts = {
        "exact_peak_key": 0,
        "compatible_claim": 0,
        "group_sort_key": 0,
        "winner_sort_key": 0,
        "duplicate_replacements": 0,
    }
    original_exact_peak_key = claim_registry_module._exact_peak_key
    original_claim_classifier = claim_registry_module.classify_cross_sample_claim_edge
    original_group_sort_key = claim_registry_module._group_sort_key
    original_winner_sort_key = claim_registry_module._winner_sort_key
    original_duplicate_replacements = claim_registry_module._duplicate_replacements

    def exact_peak_key(cell: AlignedCell) -> tuple[float, float, float, float]:
        counts["exact_peak_key"] += 1
        return original_exact_peak_key(cell)

    def classify_claim_edge(facts: Any, *, config: AlignmentConfig) -> str:
        counts["compatible_claim"] += 1
        return original_claim_classifier(facts, config=config)

    def group_sort_key(group: Any) -> tuple[object, ...]:
        counts["group_sort_key"] += 1
        return original_group_sort_key(group)

    def winner_sort_key(candidate: Any) -> tuple[object, ...]:
        counts["winner_sort_key"] += 1
        return original_winner_sort_key(candidate)

    def duplicate_replacements(candidates: list[Any]) -> dict[int, AlignedCell]:
        counts["duplicate_replacements"] += 1
        return original_duplicate_replacements(candidates)

    monkeypatch.setattr(claim_registry_module, "_exact_peak_key", exact_peak_key)
    monkeypatch.setattr(
        claim_registry_module,
        "classify_cross_sample_claim_edge",
        classify_claim_edge,
    )
    monkeypatch.setattr(claim_registry_module, "_group_sort_key", group_sort_key)
    monkeypatch.setattr(claim_registry_module, "_winner_sort_key", winner_sort_key)
    monkeypatch.setattr(
        claim_registry_module,
        "_duplicate_replacements",
        duplicate_replacements,
    )

    result = claim_registry_module.apply_ms1_peak_claim_registry(
        matrix,
        config or AlignmentConfig(),
    )
    return result, ClaimRegistryOperationCounts(
        exact_peak_key_calls=counts["exact_peak_key"],
        compatible_claim_calls=counts["compatible_claim"],
        group_sort_key_calls=counts["group_sort_key"],
        winner_sort_key_calls=counts["winner_sort_key"],
        duplicate_replacement_calls=counts["duplicate_replacements"],
    )


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
    detected_count: int = 10,
    event_member_count: int | None = None,
    event_cluster_count: int | None = None,
):
    event_cluster_count = (
        detected_count if event_cluster_count is None else event_cluster_count
    )
    identity = _identity(feature_id)
    return SimpleNamespace(
        feature_family_id=feature_id,
        neutral_loss_tag="DNA_dR",
        family_center_mz=mz,
        family_center_rt=8.5,
        family_product_mz=384.0,
        family_observed_neutral_loss_da=116.0,
        has_anchor=True,
        event_cluster_ids=tuple(
            f"OWN-{feature_id}-{index}" for index in range(event_cluster_count)
        ),
        event_member_count=(
            detected_count if event_member_count is None else event_member_count
        ),
        evidence="owner_complete_link",
        review_only=False,
        owners=tuple(
            SimpleNamespace(sample_stem=sample, cross_sample_member_subject=member)
            for sample, member in zip(
                _CLAIM_SAMPLES,
                identity.member_subjects,
                strict=True,
            )
        ),
        group_hypothesis_id=identity.group_hypothesis_id,
        public_family_id=feature_id,
        processing_request_id=identity.processing_request_id,
        member_subjects=identity.member_subjects,
        identity_basis_bytes=identity.basis_bytes,
        disposition=identity.disposition,
    )


def _identity(feature_id: str):
    return mint_cross_sample_group_identity(
        processing_request_id=_TEST_PROCESSING_REQUEST_ID,
        member_subjects=tuple(
            _member(feature_id, sample) for sample in _CLAIM_SAMPLES
        ),
    )


def _member(feature_id: str, sample_stem: str) -> CrossSampleGroupMemberSubject:
    subject_id = f"xic.peak-observation-content.v1:claim-{feature_id}-{sample_stem}"
    sample_id = f"xic.sample.v1:claim-{sample_stem}"
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


def _cell(
    sample: str,
    feature_id: str,
    status: str,
    *,
    area: float | None = 1000.0,
    apex: float = 8.5,
    start: float = 8.4,
    end: float = 8.6,
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
    )
