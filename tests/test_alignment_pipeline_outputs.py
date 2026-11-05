import csv
import json
from dataclasses import replace
from pathlib import Path

import pytest

import xic_extractor.output.alignment.pipeline_outputs as pipeline_outputs_module
from tests.alignment_pipeline_helpers import finalized_writer_decisions
from tests.alignment_pipeline_helpers import matrix as _matrix
from tests.alignment_pipeline_helpers import peak_config as _peak_config
from xic_extractor.alignment import AlignmentConfig
from xic_extractor.alignment.backfill.peak_fact_provenance import (
    BackfillPeakFactProvenanceBatch,
    run_profile_row,
)
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
    deserialize_signal_processing_profile,
)
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
    assert metadata["owner_backfill_peak_fact_provenance_schema_version"] == (
        "alignment_owner_backfill_peak_fact_provenance"
    )
    assert "_v1" not in metadata["owner_backfill_peak_fact_provenance_schema_version"]
    assert metadata["owner_backfill_peak_fact_provenance_row_count"] == "0"


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


def test_alignment_metadata_records_audit_evidence_mode() -> None:
    metadata = alignment_metadata(
        discovery_batch_index=Path("batch.csv"),
        raw_dir=Path("raw"),
        dll_dir=Path("dll"),
        owner_backfill_xic_backend="raw",
        output_level="validation",
        peak_config=_peak_config(),
        backfill_scope="production-equivalent",
        output_scope="production-equivalent",
        audit_evidence_mode="none",
        requested_audit_evidence_mode="auto",
        heavy_audit_enabled=False,
        audit_evidence_mode_reason="production_equivalent_default_no_audit",
    )

    assert metadata["audit_evidence_mode"] == "none"
    assert metadata["requested_audit_evidence_mode"] == "auto"
    assert metadata["heavy_audit_enabled"] == "False"
    assert (
        metadata["request_plan_version"]
        == "alignment-parity-scoped-owner-backfill-request-plan"
    )
    assert (
        metadata["audit_evidence_mode_reason"]
        == "production_equivalent_default_no_audit"
    )


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
    assert outputs.owner_backfill_peak_fact_provenance_tsv == (
        tmp_path / "alignment_owner_backfill_peak_fact_provenance.tsv"
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
    AlignmentConfig()

    pipeline_outputs_module.write_outputs_atomic(
        outputs,
        matrix,
        metadata={},
        production_decisions=finalized_writer_decisions(matrix),
        timing_recorder=recorder,
    )

    observations = recorder.to_json_dict()["measurement_observations"]
    by_metric = {}
    for row in observations:
        by_metric.setdefault(row["metric"], []).append(row)
    assert all(
        row["value"] is not None and row["availability"] == "partial"
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
    AlignmentConfig()
    writer_args = (matrix,)
    writer_kwargs = {
        "metadata": {},
        "production_decisions": finalized_writer_decisions(matrix),
    }
    pipeline_outputs_module.write_outputs_atomic(
        baseline_outputs,
        *writer_args,
        **writer_kwargs,
    )

    import xic_extractor.processing_identity as identity

    def reject_diagnostic_hash(*_args, **_kwargs):
        raise OSError("diagnostic hash unavailable")

    monkeypatch.setattr(identity, "fingerprint_file_content", reject_diagnostic_hash)

    pipeline_outputs_module.write_outputs_atomic(
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
    detected_matrix = _matrix(("Sample_A", "Sample_B", "Sample_C"))
    matrix = replace(
        detected_matrix,
        cells=(
            *detected_matrix.cells[:2],
            replace(
                detected_matrix.cells[2],
                status="absent",
                area=None,
                reason="no accepted observation",
            ),
        ),
    )
    AlignmentConfig()
    decisions = finalized_writer_decisions(matrix)
    writer_kwargs = {
        "metadata": {},
        "production_decisions": replace(
            decisions,
            rows={
                key: replace(decision, include_in_primary_matrix=True)
                for key, decision in decisions.rows.items()
            },
        ),
    }

    pipeline_outputs_module.write_outputs_atomic(
        production_outputs,
        matrix,
        **writer_kwargs,
    )
    pipeline_outputs_module.write_outputs_atomic(
        machine_outputs,
        matrix,
        **writer_kwargs,
    )

    assert production_outputs.matrix_tsv is not None
    assert machine_outputs.matrix_tsv is not None
    production_bytes = production_outputs.matrix_tsv.read_bytes()
    assert production_bytes.splitlines()[-1].endswith(b"\t")
    assert production_bytes == machine_outputs.matrix_tsv.read_bytes()


def test_writer_binds_provenance_row_count_into_persisted_metadata(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "out"
    outputs = pipeline_outputs_module.AlignmentRunOutputs(
        owner_backfill_peak_fact_provenance_tsv=(
            output_dir / "alignment_owner_backfill_peak_fact_provenance.tsv"
        ),
        run_metadata_json=output_dir / "alignment_run_metadata.json",
    )
    matrix = _matrix(("Sample_A", "Sample_B"))
    AlignmentConfig()
    profile = SignalProcessingProfile(window_points=21)

    pipeline_outputs_module.write_outputs_atomic(
        outputs,
        matrix,
        metadata={},
        production_decisions=finalized_writer_decisions(matrix),
        backfill_peak_fact_provenance=BackfillPeakFactProvenanceBatch(
            (run_profile_row(profile),)
        ),
    )

    assert outputs.run_metadata_json is not None
    persisted = json.loads(outputs.run_metadata_json.read_text(encoding="utf-8"))
    assert persisted["owner_backfill_peak_fact_provenance_row_count"] == "1"
    assert outputs.owner_backfill_peak_fact_provenance_tsv is not None
    with outputs.owner_backfill_peak_fact_provenance_tsv.open(
        encoding="utf-8", newline=""
    ) as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        assert reader.fieldnames == ["row_kind", "schema_version", "profile_json"]
        (row,) = reader
    assert row["row_kind"] == "run_profile"
    assert deserialize_signal_processing_profile(row["profile_json"]) == profile


def test_writer_rejects_provenance_row_count_mismatch_before_final_writes(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "out"
    outputs = pipeline_outputs_module.AlignmentRunOutputs(
        owner_backfill_peak_fact_provenance_tsv=(
            output_dir / "alignment_owner_backfill_peak_fact_provenance.tsv"
        ),
        run_metadata_json=output_dir / "alignment_run_metadata.json",
    )
    final_paths = (
        outputs.owner_backfill_peak_fact_provenance_tsv,
        outputs.run_metadata_json,
    )
    for path in final_paths:
        assert path is not None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"prior output")
    matrix = _matrix(("Sample_A", "Sample_B"))
    AlignmentConfig()

    with pytest.raises(ValueError, match="does not match the typed provenance batch"):
        pipeline_outputs_module.write_outputs_atomic(
            outputs,
            matrix,
            metadata={"owner_backfill_peak_fact_provenance_row_count": "0"},
            production_decisions=finalized_writer_decisions(matrix),
        )

    assert all(
        path is not None and path.read_bytes() == b"prior output"
        for path in final_paths
    )
    assert not tuple(output_dir.glob("*.tmp"))
    assert not tuple(output_dir.glob("*.bak"))


def test_provenance_outer_transaction_never_nests_or_leaks_temp_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "out"
    provenance_path = output_dir / "alignment_owner_backfill_peak_fact_provenance.tsv"
    status_path = output_dir / "alignment_matrix_status.tsv"
    outputs = pipeline_outputs_module.AlignmentRunOutputs(
        owner_backfill_peak_fact_provenance_tsv=provenance_path,
        status_matrix_tsv=status_path,
    )
    output_dir.mkdir(parents=True)
    provenance_path.write_bytes(b"prior provenance")
    status_path.write_bytes(b"prior status")
    original_write_bytes = Path.write_bytes
    nested_temp_attempts: list[Path] = []

    def fail_if_nested_temp(path: Path, payload: bytes) -> int:
        if path.name.endswith(".tmp.tmp"):
            nested_temp_attempts.append(path)
            original_write_bytes(path, payload[:1])
            raise RuntimeError("nested provenance temp write")
        return original_write_bytes(path, payload)

    def fail_later_writer(*_args, **_kwargs):
        raise RuntimeError("injected status writer failure")

    monkeypatch.setattr(Path, "write_bytes", fail_if_nested_temp)
    monkeypatch.setattr(
        pipeline_outputs_module,
        "write_alignment_status_matrix_tsv",
        fail_later_writer,
    )
    matrix = _matrix(("Sample_A", "Sample_B"))
    AlignmentConfig()

    with pytest.raises(RuntimeError, match="injected status writer failure"):
        pipeline_outputs_module.write_outputs_atomic(
            outputs,
            matrix,
            metadata={},
            production_decisions=finalized_writer_decisions(matrix),
        )

    assert nested_temp_attempts == []
    assert provenance_path.read_bytes() == b"prior provenance"
    assert status_path.read_bytes() == b"prior status"
    leftovers = tuple(
        path
        for path in output_dir.iterdir()
        if path.name.endswith((".tmp", ".bak", ".tmp.tmp"))
    )
    assert leftovers == ()


class _Timer:
    def __init__(self, values: list[float]) -> None:
        self._values = values
        self._index = 0

    def __call__(self) -> float:
        value = self._values[self._index]
        self._index += 1
        return value
