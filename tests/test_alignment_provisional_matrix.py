from __future__ import annotations

import gc
import hashlib
import tracemalloc
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace

import pytest

import xic_extractor.output.alignment.pipeline_outputs as pipeline_outputs
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.matrix import AlignedCell, AlignmentMatrix
from xic_extractor.alignment.production_decisions import build_production_decisions
from xic_extractor.diagnostics.timing import TimingRecorder
from xic_extractor.output.alignment.pipeline_outputs import AlignmentRunOutputs
from xic_extractor.output.alignment.provisional_matrix import (
    PROVISIONAL_VALIDATION_STATUS,
    build_provisional_matrix_projection,
    write_provisional_identity_tsv,
    write_provisional_matrix_tsv,
    write_provisional_provenance_tsv,
    write_provisional_summary_json,
)
from xic_extractor.peak_detection.integration_result import IntegrationResult
from xic_extractor.peak_detection.ms1_morphology import MS1_MORPHOLOGY_AREA_SOURCE


def test_projection_keeps_full_group_breadth_and_cell_provenance() -> None:
    matrix = _representative_matrix()

    projection = _projection(matrix)

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
        "schema_version": "untargeted_provisional_matrix_summary",
        "matrix_schema_version": "untargeted_provisional_matrix",
        "validation_status": PROVISIONAL_VALIDATION_STATUS,
        "row_identity_status": "provisional",
        "identity_settlement_status": "not_run",
        "product_authority": False,
        "discovery_owner_closure": True,
        "backfill_scope": "full-audit",
        "selected_family_count": 0,
        "sample_count": 2,
        "row_count": 2,
        "detected_cell_count": 2,
        "backfill_candidate_cell_count": 1,
        "quant_available_cell_count": 3,
        "matrix_blank_cell_count": 1,
    }


def test_projection_retains_compact_state_instead_of_dense_row_grid() -> None:
    group_count = 40
    sample_order = tuple(f"sample-{index:03d}" for index in range(60))
    groups = tuple(
        _group(
            f"GRP-{index:03d}",
            f"FAM{index:06d}",
            mz=100.0 + index,
            rt=1.0 + index,
        )
        for index in range(group_count)
    )
    cells = (
        _cell(
            sample_order[0],
            "GRP-000",
            "FAM000000",
            status="detected",
            area=10.0,
        ),
        _cell(
            sample_order[-1],
            f"GRP-{group_count - 1:03d}",
            f"FAM{group_count - 1:06d}",
            status="backfilled",
            area=5.0,
        ),
    )

    projection = _projection(
        AlignmentMatrix(clusters=groups, cells=cells, sample_order=sample_order)
    )

    retained_fields = {field.name for field in fields(projection)}
    assert retained_fields.isdisjoint(
        {"matrix_rows", "identity_rows", "provenance_rows"}
    )
    assert len(projection._cells_by_key) == len(cells)
    assert len(projection._row_counts) == group_count
    assert projection.summary["matrix_blank_cell_count"] == (
        group_count * len(sample_order) - len(cells)
    )


def test_projection_has_bounded_no_raw_peak_allocation() -> None:
    observed = _allocation_probe()

    assert observed["dense_row_count"] == 40_000
    assert observed["retained_cell_count"] == 0
    assert observed["retained_row_count_entries"] == 400
    assert observed["materialized_row_count"] == 40_000
    assert observed["materialized_provenance_peak_bytes"] > (
        observed["compact_projection_peak_bytes"] * 20
    )


def test_streamed_provisional_artifacts_match_frozen_materializing_hashes(
    tmp_path: Path,
) -> None:
    projection = _projection(_representative_matrix())
    paths = (
        write_provisional_matrix_tsv(tmp_path / "matrix.tsv", projection),
        write_provisional_identity_tsv(tmp_path / "identity.tsv", projection),
        write_provisional_provenance_tsv(tmp_path / "provenance.tsv", projection),
        write_provisional_summary_json(tmp_path / "summary.json", projection),
    )

    observed = {
        path.name: (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size)
        for path in paths
    }
    assert observed == {
        "matrix.tsv": (
            "d45016f91a57f5161f888f9e0d23273a194d9badca072a01d9566982b194349f",
            143,
        ),
        "identity.tsv": (
            "579991fa8be54f06134845790a72c423f99147e26ba9e548a548563558520761",
            864,
        ),
        "provenance.tsv": (
            "b1d1b7234d76937b7f3080be10d4d7db97bd26b41f0f25d828777311a66ae3bf",
            1191,
        ),
        "summary.json": (
            "f254a7f32b0b11904ce5fb8a1d76b944c4589aef73105194d7bd3f959b7c3cab",
            567,
        ),
    }


def test_provisional_writer_timing_reports_only_owner_supported_phases(
    tmp_path: Path,
) -> None:
    outputs = _provisional_outputs(tmp_path)
    recorder = TimingRecorder("alignment", instrument_writers=True)
    matrix = _representative_matrix()
    config = AlignmentConfig()

    pipeline_outputs.write_outputs_atomic(
        outputs,
        matrix,
        metadata=_provisional_metadata(),
        alignment_config=config,
        production_decisions=build_production_decisions(matrix, config),
        timing_recorder=recorder,
    )

    payload = recorder.to_json_dict()
    observations = payload["measurement_observations"]
    expected_scopes = {
        "alignment_owner_backfill_peak_fact_provenance",
        "provisional_alignment_matrix",
        "provisional_row_identity",
        "provisional_cell_provenance",
        "provisional_matrix_summary",
    }
    provisional_scopes = expected_scopes - {
        "alignment_owner_backfill_peak_fact_provenance"
    }
    projection_records = [
        row
        for row in payload["records"]
        if row["stage"] == "writer.provisional_matrix.projection"
    ]
    assert len(projection_records) == 1
    assert projection_records[0]["elapsed_sec"] >= 0.0
    for metric in ("writer_projection_time_sec",):
        rows = [row for row in observations if row["metric"] == metric]
        assert {row["scope"] for row in rows} == provisional_scopes
        assert all(row["availability"] == "unavailable" for row in rows)
        assert all(row["value"] is None for row in rows)
    for metric in ("writer_temp_write_time_sec",):
        rows = [row for row in observations if row["metric"] == metric]
        assert {row["scope"] for row in rows} == expected_scopes
        assert all(row["availability"] == "unavailable" for row in rows)
        assert all(row["value"] is None for row in rows)

    render_rows = [
        row for row in observations if row["metric"] == "writer_render_time_sec"
    ]
    assert {row["scope"] for row in render_rows} == expected_scopes
    assert all(row["availability"] == "partial" for row in render_rows)
    assert all(row["value"] is not None for row in render_rows)

    for metric in ("writer_rename_time_sec", "writer_hash_time_sec"):
        rows = [row for row in observations if row["metric"] == metric]
        assert {row["scope"] for row in rows} == expected_scopes
        assert all(row["availability"] == "measured" for row in rows)
        assert all(row["value"] is not None for row in rows)

    byte_expectations = {
        "writer_render_bytes": ("partial", True),
        "writer_temp_write_bytes": ("unavailable", False),
        "writer_rename_bytes": ("measured", True),
        "writer_hash_bytes": ("measured", True),
    }
    for metric, (availability, has_value) in byte_expectations.items():
        rows = [row for row in observations if row["metric"] == metric]
        assert {row["scope"] for row in rows} == expected_scopes
        assert all(row["availability"] == availability for row in rows)
        assert all((row["value"] is not None) == has_value for row in rows)
    projection_byte_rows = [
        row for row in observations if row["metric"] == "writer_projection_bytes"
    ]
    assert {row["scope"] for row in projection_byte_rows} == provisional_scopes
    assert all(row["availability"] == "unavailable" for row in projection_byte_rows)
    assert all(row["value"] is None for row in projection_byte_rows)


def test_streamed_pipeline_matches_frozen_formula_and_unquantified_bytes(
    tmp_path: Path,
) -> None:
    matrix = AlignmentMatrix(
        clusters=(
            _group("GRP-FORMULA", "FAM000001", mz=100.0, rt=1.0),
            _group("GRP-UNQUANT", "FAM000002", mz=200.0, rt=2.0),
        ),
        cells=(
            _cell(
                "=sample",
                "GRP-FORMULA",
                "FAM000001",
                status="detected",
                area=10.0,
                source_candidate_id="+candidate",
            ),
            _cell(
                "=sample",
                "GRP-UNQUANT",
                "FAM000002",
                status="detected",
                area=0.0,
                source_candidate_id="=unquantified",
            ),
            _cell(
                "sample-b",
                "GRP-UNQUANT",
                "FAM000002",
                status="backfilled",
                area=0.0,
            ),
        ),
        sample_order=("=sample", "sample-b"),
    )
    streamed_outputs = _provisional_outputs(tmp_path / "streamed")
    config = AlignmentConfig()
    pipeline_outputs.write_outputs_atomic(
        streamed_outputs,
        matrix,
        metadata=_provisional_metadata(),
        alignment_config=config,
        production_decisions=build_production_decisions(matrix, config),
        timing_recorder=TimingRecorder("alignment", instrument_writers=True),
    )

    observed = {
        path.name: (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size)
        for path in _output_paths(streamed_outputs)
    }
    assert observed == {
        "provisional_alignment_matrix.tsv": (
            "aba2973588cfa44c841e29e448f71a2c998508db2d42d86a94f33a02844fa077",
            123,
        ),
        "provisional_row_identity.tsv": (
            "5f809444a041c4f5535b038ab3847cc9a626b0e915f413be0c43eb00694396cd",
            849,
        ),
        "provisional_cell_provenance.tsv": (
            "211cbf94e4c30476383888a9f44960d4b2ba4a6ed3fc21493703004893815a92",
            1145,
        ),
        "provisional_matrix_summary.json": (
            "2c8d27d8c99a92610a1b03cb7af50bcf23bb9cb42adf959804d6464c28c50851",
            567,
        ),
        "alignment_owner_backfill_peak_fact_provenance.tsv": (
            "6d02dc85860f193e16866b9f613d0d4bb9dae9d39d72be15849969a92082a3c4",
            562,
        ),
    }

    assert streamed_outputs.provisional_matrix_tsv is not None
    assert streamed_outputs.provisional_cell_provenance_tsv is not None
    assert "\t'=sample\tsample-b\n" in (
        streamed_outputs.provisional_matrix_tsv.read_text(encoding="utf-8")
    )
    provenance_text = streamed_outputs.provisional_cell_provenance_tsv.read_text(
        encoding="utf-8"
    )
    assert "\t'+candidate\t" in provenance_text
    assert "observed_unquantified" in provenance_text
    assert "backfill_unquantified" in provenance_text


def test_projection_retains_unquantified_groups_and_counts_observations() -> None:
    matrix = AlignmentMatrix(
        clusters=(_group("GRP-A", "FAM000001", mz=100.0, rt=1.0),),
        cells=(
            _cell(
                "sample-a",
                "GRP-A",
                "FAM000001",
                status="detected",
                area=0.0,
                source_candidate_id="sample-a#candidate",
            ),
            _cell(
                "sample-b",
                "GRP-A",
                "FAM000001",
                status="backfilled",
                area=0.0,
            ),
        ),
        sample_order=("sample-a", "sample-b"),
    )

    projection = _projection(matrix)

    assert len(projection.matrix_rows) == 1
    assert projection.matrix_rows[0]["sample-a"] == ""
    assert projection.matrix_rows[0]["sample-b"] == ""
    assert projection.identity_rows[0]["detected_cell_count"] == "1"
    assert projection.identity_rows[0]["backfill_candidate_cell_count"] == "1"
    assert projection.identity_rows[0]["quant_available_cell_count"] == "0"
    assert projection.identity_rows[0]["matrix_blank_cell_count"] == "2"
    assert [row["cell_state"] for row in projection.provenance_rows] == [
        "observed_unquantified",
        "backfill_unquantified",
    ]
    assert projection.summary["row_count"] == 1
    assert projection.summary["detected_cell_count"] == 1
    assert projection.summary["backfill_candidate_cell_count"] == 1
    assert projection.summary["quant_available_cell_count"] == 0
    assert projection.summary["matrix_blank_cell_count"] == 2


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
        _projection(matrix)


def test_projection_rejects_reserved_sample_column() -> None:
    matrix = AlignmentMatrix(
        clusters=(_group("GRP-A", "FAM000001", mz=100.0, rt=1.0),),
        cells=(),
        sample_order=("public_family_id",),
    )

    with pytest.raises(ValueError, match="collides with metadata columns"):
        _projection(matrix)


def test_projection_rejects_unknown_cell_status() -> None:
    cell = _cell(
        "sample-a",
        "GRP-A",
        "FAM000001",
        status="detected",
        area=10.0,
    )
    object.__setattr__(cell, "status", "future_status")
    matrix = AlignmentMatrix(
        clusters=(_group("GRP-A", "FAM000001", mz=100.0, rt=1.0),),
        cells=(cell,),
        sample_order=("sample-a",),
    )

    with pytest.raises(ValueError, match="unsupported provisional cell status"):
        _projection(matrix)


def test_provisional_tsvs_escape_formula_headers_and_values(tmp_path: Path) -> None:
    matrix = AlignmentMatrix(
        clusters=(_group("GRP-A", "FAM000001", mz=100.0, rt=1.0),),
        cells=(
            _cell(
                "=sample",
                "GRP-A",
                "FAM000001",
                status="detected",
                area=10.0,
                source_candidate_id="+candidate",
            ),
        ),
        sample_order=("=sample",),
    )
    projection = _projection(matrix)
    matrix_path = write_provisional_matrix_tsv(tmp_path / "matrix.tsv", projection)
    identity_path = write_provisional_identity_tsv(
        tmp_path / "identity.tsv",
        projection,
    )
    provenance_path = write_provisional_provenance_tsv(
        tmp_path / "provenance.tsv",
        projection,
    )

    assert "\t'=sample\n" in matrix_path.read_text(encoding="utf-8")
    assert "\t'+candidate\t" in provenance_path.read_text(encoding="utf-8")
    assert identity_path.is_file()


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
    config = AlignmentConfig()

    pipeline_outputs.write_outputs_atomic(
        outputs,
        matrix,
        metadata=_provisional_metadata(),
        alignment_config=config,
        production_decisions=build_production_decisions(matrix, config),
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
        owner_backfill_peak_fact_provenance_tsv=(
            tmp_path / "alignment_owner_backfill_peak_fact_provenance.tsv"
        ),
        provisional_identity_tsv=tmp_path / "provisional_row_identity.tsv",
    )
    matrix = AlignmentMatrix(clusters=(), cells=(), sample_order=())
    config = AlignmentConfig()

    with pytest.raises(ValueError, match="must be emitted atomically"):
        pipeline_outputs.write_outputs_atomic(
            outputs,
            matrix,
            metadata=_provisional_metadata(),
            alignment_config=config,
            production_decisions=build_production_decisions(matrix, config),
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
    config = AlignmentConfig()

    with pytest.raises(OSError, match="summary write failed"):
        pipeline_outputs.write_outputs_atomic(
            outputs,
            matrix,
            metadata=_provisional_metadata(),
            alignment_config=config,
            production_decisions=build_production_decisions(matrix, config),
        )

    assert not any(path.exists() for path in _output_paths(outputs))


def _representative_matrix() -> AlignmentMatrix:
    return AlignmentMatrix(
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
                status="backfilled",
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


def _allocation_probe() -> dict[str, int]:
    group_count = 400
    sample_count = 100
    groups = tuple(
        _group(
            f"GRP-{index:04d}",
            f"FAM{index:06d}",
            mz=100.0 + index,
            rt=1.0 + index,
        )
        for index in range(group_count)
    )
    samples = tuple(f"sample-{index:03d}" for index in range(sample_count))
    matrix = AlignmentMatrix(clusters=groups, cells=(), sample_order=samples)

    gc.collect()
    tracemalloc.start()
    projection = _projection(matrix)
    _, compact_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    gc.collect()
    tracemalloc.start()
    materialized = projection.provenance_rows
    _, materialized_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    return {
        "dense_row_count": group_count * sample_count,
        "retained_cell_count": len(projection._cells_by_key),
        "retained_row_count_entries": len(projection._row_counts),
        "compact_projection_peak_bytes": compact_peak,
        "materialized_provenance_peak_bytes": materialized_peak,
        "materialized_row_count": len(materialized),
    }


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
        reason=status,
        selected_integration=_integration(area),
        group_hypothesis_id=cell_group_hypothesis_id or group_id,
        public_family_id=family_id,
        group_construction_role="successor_constructor",
        group_membership_source="cross_sample_peak_group_hypothesis",
        backfill_seed_mz=100.0 if status == "backfilled" else None,
        backfill_seed_rt=1.0 if status == "backfilled" else None,
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
        owner_backfill_peak_fact_provenance_tsv=(
            tmp_path / "alignment_owner_backfill_peak_fact_provenance.tsv"
        ),
        provisional_matrix_tsv=tmp_path / "provisional_alignment_matrix.tsv",
        provisional_identity_tsv=tmp_path / "provisional_row_identity.tsv",
        provisional_cell_provenance_tsv=(tmp_path / "provisional_cell_provenance.tsv"),
        provisional_summary_json=tmp_path / "provisional_matrix_summary.json",
    )


def _output_paths(outputs: AlignmentRunOutputs) -> tuple[Path, ...]:
    paths = (
        outputs.owner_backfill_peak_fact_provenance_tsv,
        outputs.provisional_matrix_tsv,
        outputs.provisional_identity_tsv,
        outputs.provisional_cell_provenance_tsv,
        outputs.provisional_summary_json,
    )
    assert all(path is not None for path in paths)
    return tuple(path for path in paths if path is not None)


def _projection(matrix: AlignmentMatrix):
    return build_provisional_matrix_projection(
        matrix,
        backfill_scope="full-audit",
        discovery_owner_closure=True,
        selected_family_count=0,
    )


def _provisional_metadata() -> dict[str, str]:
    return {
        "backfill_scope": "full-audit",
        "selected_family_count": "0",
        "require_discovery_owner_closure": "TRUE",
    }
