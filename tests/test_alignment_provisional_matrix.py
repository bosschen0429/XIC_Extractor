from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import xic_extractor.alignment.pipeline_outputs as pipeline_outputs
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.matrix import AlignedCell, AlignmentMatrix
from xic_extractor.alignment.pipeline_outputs import AlignmentRunOutputs
from xic_extractor.alignment.provisional_matrix import (
    PROVISIONAL_VALIDATION_STATUS,
    build_provisional_matrix_projection,
)
from xic_extractor.peak_detection.integration_result import IntegrationResult
from xic_extractor.peak_detection.ms1_morphology import MS1_MORPHOLOGY_AREA_SOURCE


def test_projection_keeps_full_group_breadth_and_cell_provenance() -> None:
    matrix = AlignmentMatrix(
        clusters=(
            _group("GRP-A", "FAM000001", mz=100.123456789, rt=1.25),
            _group("GRP-B", "FAM000002", mz=900.987654321, rt=28.75),
        ),
        cells=(
            _cell(
                "sample-b",
                "GRP-A",
                "FAM000001",
                status="detected",
                area=100.0,
                source_candidate_id="sample-b#candidate-1",
            ),
            _cell(
                "sample-a",
                "GRP-A",
                "FAM000001",
                status="rescued",
                area=80.0,
            ),
            _cell(
                "sample-a",
                "GRP-B",
                "FAM000002",
                status="detected",
                area=25.0,
                source_candidate_id="sample-a#candidate-2",
            ),
        ),
        sample_order=("sample-b", "sample-a"),
    )

    projection = build_provisional_matrix_projection(matrix)

    assert projection.matrix_header == (
        "group_hypothesis_id",
        "public_family_id",
        "Mz",
        "RT",
        "sample-b",
        "sample-a",
    )
    assert projection.matrix_rows == (
        {
            "group_hypothesis_id": "GRP-A",
            "public_family_id": "FAM000001",
            "Mz": "100.123456789",
            "RT": "1.25",
            "sample-b": "100",
            "sample-a": "80",
        },
        {
            "group_hypothesis_id": "GRP-B",
            "public_family_id": "FAM000002",
            "Mz": "900.987654321",
            "RT": "28.75",
            "sample-b": "",
            "sample-a": "25",
        },
    )
    assert [row["row_identity_status"] for row in projection.identity_rows] == [
        "provisional",
        "provisional",
    ]
    assert [row["cell_state"] for row in projection.provenance_rows] == [
        "observed",
        "backfill_candidate",
        "missing",
        "observed",
    ]
    assert {
        row["matrix_value_basis"]
        for row in projection.provenance_rows
        if row["matrix_value"]
    } == {MS1_MORPHOLOGY_AREA_SOURCE}
    assert projection.summary == {
        "schema_version": "untargeted_provisional_matrix_summary_v1",
        "matrix_schema_version": "untargeted_provisional_matrix_v1",
        "validation_status": PROVISIONAL_VALIDATION_STATUS,
        "row_identity_status": "provisional",
        "identity_settlement_status": "not_run",
        "product_authority": False,
        "sample_count": 2,
        "row_count": 2,
        "detected_cell_count": 2,
        "backfill_candidate_cell_count": 1,
        "quant_available_cell_count": 3,
        "matrix_blank_cell_count": 1,
    }


def test_projection_rejects_cell_bound_to_a_different_group_owner() -> None:
    matrix = AlignmentMatrix(
        clusters=(_group("GRP-A", "FAM000001", mz=100.0, rt=1.0),),
        cells=(
            _cell(
                "sample-a",
                "GRP-A",
                "FAM000001",
                status="detected",
                area=10.0,
                source_candidate_id="sample-a#candidate",
                cell_group_hypothesis_id="GRP-B",
            ),
        ),
        sample_order=("sample-a",),
    )

    with pytest.raises(ValueError, match="cell group_hypothesis_id"):
        build_provisional_matrix_projection(matrix)


def test_provisional_outputs_publish_as_one_atomic_set(tmp_path: Path) -> None:
    matrix = AlignmentMatrix(
        clusters=(_group("GRP-A", "FAM000001", mz=100.0, rt=1.0),),
        cells=(
            _cell(
                "sample-a",
                "GRP-A",
                "FAM000001",
                status="detected",
                area=10.0,
                source_candidate_id="sample-a#candidate",
            ),
        ),
        sample_order=("sample-a",),
    )
    outputs = _provisional_outputs(tmp_path)

    pipeline_outputs.write_outputs_atomic(
        outputs,
        matrix,
        metadata={},
        ownership=SimpleNamespace(assignments=(), ambiguous_records=()),
        alignment_config=AlignmentConfig(),
    )

    assert outputs.provisional_matrix_tsv is not None
    assert outputs.provisional_identity_tsv is not None
    assert outputs.provisional_cell_provenance_tsv is not None
    assert outputs.provisional_summary_json is not None
    assert outputs.provisional_matrix_tsv.is_file()
    assert outputs.provisional_identity_tsv.is_file()
    assert outputs.provisional_cell_provenance_tsv.is_file()
    assert outputs.provisional_summary_json.is_file()


def test_provisional_outputs_reject_any_incomplete_bundle(tmp_path: Path) -> None:
    outputs = AlignmentRunOutputs(
        provisional_identity_tsv=tmp_path / "provisional_row_identity.tsv",
    )

    with pytest.raises(ValueError, match="must be emitted atomically"):
        pipeline_outputs.write_outputs_atomic(
            outputs,
            AlignmentMatrix(clusters=(), cells=(), sample_order=()),
            metadata={},
            ownership=SimpleNamespace(assignments=(), ambiguous_records=()),
            alignment_config=AlignmentConfig(),
        )


def test_provisional_output_failure_leaves_no_partial_final_set(
    tmp_path: Path,
    monkeypatch,
) -> None:
    matrix = AlignmentMatrix(
        clusters=(_group("GRP-A", "FAM000001", mz=100.0, rt=1.0),),
        cells=(
            _cell(
                "sample-a",
                "GRP-A",
                "FAM000001",
                status="detected",
                area=10.0,
                source_candidate_id="sample-a#candidate",
            ),
        ),
        sample_order=("sample-a",),
    )
    outputs = _provisional_outputs(tmp_path)

    def fail_summary(*_args, **_kwargs):
        raise OSError("summary write failed")

    monkeypatch.setattr(
        pipeline_outputs,
        "write_provisional_summary_json",
        fail_summary,
    )

    with pytest.raises(OSError, match="summary write failed"):
        pipeline_outputs.write_outputs_atomic(
            outputs,
            matrix,
            metadata={},
            ownership=SimpleNamespace(assignments=(), ambiguous_records=()),
            alignment_config=AlignmentConfig(),
        )

    assert not any(path.exists() for path in _output_paths(outputs))


def _group(group_id: str, family_id: str, *, mz: float, rt: float) -> object:
    return SimpleNamespace(
        cluster_id=family_id,
        feature_family_id=family_id,
        group_hypothesis_id=group_id,
        public_family_id=family_id,
        family_center_mz=mz,
        family_center_rt=rt,
        neutral_loss_tag="DNA_dR",
        members=(SimpleNamespace(owner_id=f"owner:{group_id}"),),
        review_only=False,
        identity_conflict=False,
        consolidation_state="not_consolidated",
        group_construction_role="successor_constructor",
        group_membership_source="cross_sample_peak_group_hypothesis",
    )


def _cell(
    sample_stem: str,
    group_id: str,
    family_id: str,
    *,
    status: str,
    area: float,
    source_candidate_id: str | None = None,
    cell_group_hypothesis_id: str | None = None,
) -> AlignedCell:
    return AlignedCell(
        sample_stem=sample_stem,
        cluster_id=family_id,
        status=status,  # type: ignore[arg-type]
        area=area,
        apex_rt=1.0,
        height=area,
        peak_start_rt=0.9,
        peak_end_rt=1.1,
        rt_delta_sec=0.0,
        trace_quality="clean" if status == "detected" else "owner_backfill",
        scan_support_score=0.8 if status == "detected" else None,
        source_candidate_id=source_candidate_id,
        source_raw_file=(
            Path(f"{sample_stem}.raw") if source_candidate_id is not None else None
        ),
        reason=status,
        selected_integration=_integration(area),
        group_hypothesis_id=cell_group_hypothesis_id or group_id,
        public_family_id=family_id,
        group_construction_role="successor_constructor",
        group_membership_source="cross_sample_peak_group_hypothesis",
        backfill_seed_mz=100.0 if status == "rescued" else None,
        backfill_seed_rt=1.0 if status == "rescued" else None,
    )


def _integration(area: float) -> IntegrationResult:
    return IntegrationResult(
        rt_left_min=0.9,
        rt_apex_min=1.0,
        rt_right_min=1.1,
        raw_apex_rt_min=1.0,
        rt_width_min=0.2,
        height_raw=area,
        height_smoothed=area,
        area_raw_counts_seconds=area,
        area_baseline_corrected=area,
        baseline_type="asls",
        boundary_sources=("test",),
        area_ms1_morphology=area,
        ms1_morphology_area_source=MS1_MORPHOLOGY_AREA_SOURCE,
    )


def _provisional_outputs(tmp_path: Path) -> AlignmentRunOutputs:
    return AlignmentRunOutputs(
        provisional_matrix_tsv=tmp_path / "provisional_alignment_matrix.tsv",
        provisional_identity_tsv=tmp_path / "provisional_row_identity.tsv",
        provisional_cell_provenance_tsv=(
            tmp_path / "provisional_cell_provenance.tsv"
        ),
        provisional_summary_json=tmp_path / "provisional_matrix_summary.json",
    )


def _output_paths(outputs: AlignmentRunOutputs) -> tuple[Path, ...]:
    paths = (
        outputs.provisional_matrix_tsv,
        outputs.provisional_identity_tsv,
        outputs.provisional_cell_provenance_tsv,
        outputs.provisional_summary_json,
    )
    assert all(path is not None for path in paths)
    return tuple(path for path in paths if path is not None)
