import json
from dataclasses import replace
from pathlib import Path

import pytest

import xic_extractor.output.alignment.pipeline_outputs as pipeline_outputs_module
from tests.alignment_pipeline_helpers import matrix as _matrix
from tests.alignment_pipeline_helpers import owner_cell, writer_batch, writer_group
from tests.alignment_pipeline_helpers import peak_config as _peak_config
from xic_extractor.execution.timing import TimingRecorder
from xic_extractor.output.alignment.pipeline_outputs import (
    alignment_metadata,
    output_paths,
)


def test_alignment_metadata_records_method_and_output_contract() -> None:
    peak_config = _peak_config()

    metadata = alignment_metadata(
        discovery_batch_index=Path("batch.csv"),
        raw_dir=Path("raw"),
        dll_dir=Path("dll"),
        owner_backfill_xic_backend="raw",
        output_level="validation",
        peak_config=peak_config,
    )

    assert metadata["schema_version"] == "alignment-results"
    assert metadata["feature_id_policy"] == "report_scoped_measured_member_order"
    assert metadata["matrix_value_policy"] == "canonical_signal_profile_selected_area"
    assert metadata["baseline_integration_method"] == "asls"
    assert metadata["backfill_scope"] == "full-audit"
    assert metadata["output_scope"] == "full-audit"
    assert metadata["selected_family_source"] == ""
    assert metadata["scope_warning"] == ""
    profile = json.loads(metadata["signal_processing_profile_json"])
    assert profile["schema_version"] == "signal_processing_profile"
    assert profile["method"] == "gaussian"
    assert profile["window_points"] == 9
    assert profile["physical_signal_formula"] == (
        "gaussian_raw_minus_nonnegative_asls_rt_raw_positive"
    )


def test_alignment_metadata_records_baseline_integration_method() -> None:
    peak_config = replace(_peak_config(), baseline_integration_method="asls")

    metadata = alignment_metadata(
        discovery_batch_index=Path("batch.csv"),
        raw_dir=Path("raw"),
        dll_dir=Path("dll"),
        owner_backfill_xic_backend="raw",
        output_level="machine",
        peak_config=peak_config,
    )

    assert metadata["baseline_integration_method"] == "asls"


def test_alignment_metadata_records_backfill_scope() -> None:
    metadata = alignment_metadata(
        discovery_batch_index=Path("batch.csv"),
        raw_dir=Path("raw"),
        dll_dir=Path("dll"),
        owner_backfill_xic_backend="raw",
        output_level="validation",
        peak_config=_peak_config(),
        backfill_scope="selected-families",
        output_scope="diagnostic_only",
        selected_family_count=2,
        selected_family_source="tsv:selected.tsv",
        scope_warning="diagnostic_only_incomplete_scope",
    )

    assert metadata["backfill_scope"] == "selected-families"
    assert metadata["output_scope"] == "diagnostic_only"
    assert metadata["selected_family_count"] == "2"
    assert metadata["selected_family_source"] == "tsv:selected.tsv"
    assert metadata["scope_warning"] == "diagnostic_only_incomplete_scope"


def test_validation_minimal_outputs_keep_gate_artifacts_without_debug_surfaces(
    tmp_path: Path,
) -> None:
    outputs = output_paths(
        tmp_path,
        output_level="validation-minimal",
        emit_alignment_cells=False,
        emit_alignment_status_matrix=False,
    )

    assert outputs.matrix_tsv == tmp_path / "alignment_matrix.tsv"
    assert outputs.review_tsv == tmp_path / "alignment_review.tsv"
    assert outputs.backfill_cell_evidence_tsv == (
        tmp_path / "alignment_backfill_cell_evidence.tsv"
    )
    assert outputs.cells_tsv is None
    assert outputs.status_matrix_tsv is None
    assert outputs.run_metadata_json == tmp_path / "alignment_run_metadata.json"


def test_target_snapshot_binding_requests_existing_run_metadata_sidecar(
    tmp_path: Path,
) -> None:
    outputs = output_paths(
        tmp_path,
        output_level="validation-minimal",
        emit_alignment_cells=False,
        emit_alignment_status_matrix=False,
        emit_target_snapshot_metadata=True,
    )

    assert outputs.run_metadata_json == tmp_path / "alignment_run_metadata.json"


def test_writer_timing_observations_are_truthful_with_live_output(
    tmp_path: Path,
) -> None:
    outputs = output_paths(
        tmp_path / "out",
        output_level="validation-minimal",
        emit_alignment_cells=False,
        emit_alignment_status_matrix=False,
    )
    live_path = tmp_path / "timing.live.json"
    recorder = TimingRecorder(
        "alignment",
        live_output_path=live_path,
        instrument_writers=True,
        timer=_Timer([0.0, 1.0] * 200),
    )
    matrix = _matrix(("Sample_A", "Sample_B"))

    _write(
        outputs,
        matrix,
        metadata={},
        timing_recorder=recorder,
    )

    observations = recorder.to_json_dict()["measurement_observations"]
    by_metric = {}
    for row in observations:
        by_metric.setdefault(row["metric"], []).append(row)
    # One shared render writes every file, so per-file render time is unknown.
    assert all(
        row["value"] is None and row["availability"] == "unavailable"
        for row in by_metric["writer_render_time_sec"]
    )
    assert all(
        row["value"] is None and row["availability"] == "unavailable"
        for row in by_metric["writer_temp_write_time_sec"]
    )
    assert all(
        row["value"] is not None and row["availability"] == "measured"
        for row in by_metric["writer_rename_time_sec"]
    )


def test_writer_instrumentation_preserves_output_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline_outputs = output_paths(
        tmp_path / "baseline",
        output_level="validation-minimal",
        emit_alignment_cells=False,
        emit_alignment_status_matrix=False,
    )
    candidate_outputs = output_paths(
        tmp_path / "candidate",
        output_level="validation-minimal",
        emit_alignment_cells=False,
        emit_alignment_status_matrix=False,
    )
    matrix = _matrix(("Sample_A", "Sample_B"))
    writer_args = (matrix,)
    writer_kwargs = {"metadata": {}}
    _write(
        baseline_outputs,
        *writer_args,
        **writer_kwargs,
    )

    import xic_extractor.processing_identity as identity

    def reject_diagnostic_hash(*_args, **_kwargs):
        raise OSError("diagnostic hash unavailable")

    monkeypatch.setattr(identity, "fingerprint_file_content", reject_diagnostic_hash)

    _write(
        candidate_outputs,
        *writer_args,
        **writer_kwargs,
        timing_recorder=TimingRecorder("alignment", instrument_writers=True),
    )

    baseline_files = {
        path.name: path
        for path in baseline_outputs.__dict__.values()
        if isinstance(path, Path)
    }
    candidate_files = {
        path.name: path
        for path in candidate_outputs.__dict__.values()
        if isinstance(path, Path)
    }
    assert baseline_files.keys() == candidate_files.keys()
    assert all(
        baseline_files[name].read_bytes() == candidate_files[name].read_bytes()
        for name in baseline_files
    )


def test_production_and_machine_plans_write_identical_matrix_bytes(
    tmp_path: Path,
) -> None:
    production_outputs = output_paths(
        tmp_path / "production",
        output_level="production",
        emit_alignment_cells=False,
        emit_alignment_status_matrix=False,
    )
    machine_outputs = output_paths(
        tmp_path / "machine",
        output_level="machine",
        emit_alignment_cells=False,
        emit_alignment_status_matrix=False,
    )
    matrix = writer_batch(
        ("Sample_A", "Sample_B", "Sample_C"),
        writer_group(owner_cell(), owner_cell(), owner_cell(None)),
    )
    writer_kwargs = {"metadata": {}}

    _write(
        production_outputs,
        matrix,
        **writer_kwargs,
    )
    _write(
        machine_outputs,
        matrix,
        **writer_kwargs,
    )

    assert production_outputs.matrix_tsv is not None
    assert machine_outputs.matrix_tsv is not None
    production_bytes = production_outputs.matrix_tsv.read_bytes()
    assert production_bytes.splitlines()[-1].endswith(b"\t")
    assert production_bytes == machine_outputs.matrix_tsv.read_bytes()


def test_outer_transaction_restores_prior_outputs_without_leaking_temp_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "out"
    matrix_path = output_dir / "alignment_matrix.tsv"
    status_path = output_dir / "alignment_matrix_status.tsv"
    outputs = pipeline_outputs_module.AlignmentRunOutputs(
        matrix_tsv=matrix_path,
        status_matrix_tsv=status_path,
    )
    output_dir.mkdir(parents=True)
    matrix_path.write_bytes(b"prior matrix")
    status_path.write_bytes(b"prior status")

    write_tsvs = pipeline_outputs_module.write_alignment_tsvs

    def fail_after_rendering(*args, **kwargs):
        write_tsvs(*args, **kwargs)
        raise RuntimeError("injected status writer failure")

    monkeypatch.setattr(
        pipeline_outputs_module,
        "write_alignment_tsvs",
        fail_after_rendering,
    )
    matrix = _matrix(("Sample_A", "Sample_B"))

    with pytest.raises(RuntimeError, match="injected status writer failure"):
        _write(
            outputs,
            matrix,
            metadata={},
        )

    assert matrix_path.read_bytes() == b"prior matrix"
    assert status_path.read_bytes() == b"prior status"
    leftovers = tuple(
        path
        for path in output_dir.iterdir()
        if path.name.endswith((".tmp", ".bak", ".tmp.tmp"))
    )
    assert leftovers == ()


def test_matrix_only_output_plan_writes_only_the_matrix(tmp_path: Path) -> None:
    outputs = output_paths(
        tmp_path / "out",
        output_level="production",
        emit_alignment_cells=False,
        emit_alignment_status_matrix=False,
    )

    _write(outputs, _matrix(("Sample_A", "Sample_B")), metadata={})

    assert sorted(path.name for path in (tmp_path / "out").iterdir()) == [
        "alignment_matrix.tsv"
    ]


def test_one_pass_writes_every_requested_file(tmp_path: Path) -> None:
    samples = ("Sample_A", "Sample_B", "Sample_C")
    batch = writer_batch(
        samples,
        writer_group(
            owner_cell(area=120.0),
            owner_cell("backfilled", area=80.0),
            owner_cell(None, no_write="not_searched"),
            candidates=(("Sample_A", "Sample_A#1"),),
        ),
        writer_group(
            owner_cell(area=None),
            owner_cell(None, no_write="unresolved"),
            owner_cell(area=40.0),
            destination="archive",
            has_anchor=False,
        ),
        writer_group(
            owner_cell(area=10.0),
            owner_cell(area=20.0),
            owner_cell(None, no_write="searched_no_observation"),
            center_mz=612.25,
        ),
    )
    directory = tmp_path / "out"
    outputs = pipeline_outputs_module.AlignmentRunOutputs(
        review_tsv=directory / "alignment_review.tsv",
        matrix_tsv=directory / "alignment_matrix.tsv",
        backfill_cell_evidence_tsv=directory / "alignment_backfill_cell_evidence.tsv",
        cells_tsv=directory / "alignment_cells.tsv",
        status_matrix_tsv=directory / "alignment_matrix_status.tsv",
        run_metadata_json=directory / "alignment_run_metadata.json",
    )

    row_count = _write(outputs, batch, metadata={"schema_version": "test"})

    assert row_count == 3
    assert sorted(path.name for path in directory.iterdir()) == [
        "alignment_backfill_cell_evidence.tsv",
        "alignment_cells.tsv",
        "alignment_matrix.tsv",
        "alignment_matrix_status.tsv",
        "alignment_review.tsv",
        "alignment_run_metadata.json",
    ]
    line_counts = {
        path.name: len(path.read_text(encoding="utf-8").splitlines())
        for path in directory.glob("*.tsv")
    }
    assert line_counts == {
        "alignment_backfill_cell_evidence.tsv": 3,
        "alignment_cells.tsv": 10,
        "alignment_matrix.tsv": 3,
        "alignment_matrix_status.tsv": 4,
        "alignment_review.tsv": 4,
    }


def _write(outputs, batch, *, metadata, timing_recorder=None) -> int:
    return pipeline_outputs_module.write_completed_untargeted_outputs_atomic(
        outputs,
        batch,
        metadata=metadata,
        timing_recorder=timing_recorder or TimingRecorder.disabled("alignment"),
    )


class _Timer:
    def __init__(self, values: list[float]) -> None:
        self._values = values
        self._index = 0

    def __call__(self) -> float:
        value = self._values[self._index]
        self._index += 1
        return value
