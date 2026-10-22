import json
from dataclasses import replace
from pathlib import Path

import pytest

import xic_extractor.output.alignment.pipeline_outputs as pipeline_outputs_module
from tests.alignment_pipeline_helpers import matrix as _matrix
from tests.alignment_pipeline_helpers import peak_config as _peak_config
from xic_extractor.alignment import AlignmentConfig
from xic_extractor.alignment.production_decisions import build_production_decisions
from xic_extractor.execution.timing import TimingRecorder
from xic_extractor.output.alignment import artifact_write_receipts as receipts_module
from xic_extractor.output.alignment.artifact_write_receipts import (
    AlignmentArtifactFingerprintBatch,
    AlignmentArtifactFingerprintReceipt,
)
from xic_extractor.output.alignment.pipeline_outputs import (
    alignment_metadata,
    output_paths,
)
from xic_extractor.processing_identity import (
    FileContentFingerprint,
    fingerprint_file_content,
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
    assert metadata["targeted_istd_anchor_identity"] == ""
    assert metadata["targeted_istd_target_snapshot_id"] == ""
    assert (
        metadata["cross_sample_peak_group_policy"]
        == "cross_sample_peak_group_hypothesis"
    )
    assert metadata["public_family_id_policy"] == "fam_compatibility_id"
    assert (
        metadata["group_delivery_policy"] == "owner_group_delivery_successor_projection"
    )
    assert metadata["gap_fill_policy"] == "missing_observation_gap_fill"
    assert (
        metadata["legacy_owner_backfill_role"]
        == "owner_backfill_as_gap_fill_materialization"
    )
    assert (
        metadata["pre_backfill_projection_policy"]
        == "pre_backfill_successor_projection_required_when_enabled"
    )
    assert metadata["matrix_value_policy"] == "canonical_signal_profile_selected_area"
    assert metadata["baseline_integration_method"] == "asls"
    assert metadata["owner_build_xic_backend"] == "raw"
    assert metadata["backfill_scope"] == "full-audit"
    assert metadata["output_scope"] == "full-audit"
    assert metadata["selected_family_source"] == ""
    assert metadata["scope_warning"] == ""
    assert metadata["signal_processing_profile_schema_version"] == (
        "signal_processing_profile"
    )
    assert metadata["signal_processing_profile_id"].startswith("signal-profile:")
    assert len(metadata["signal_processing_profile_hash"]) == 64
    assert metadata["signal_processing_method"] == "gaussian"
    assert metadata["signal_processing_window_points"] == "9"
    assert metadata["physical_signal_formula"] == (
        "gaussian_raw_minus_nonnegative_asls_rt_raw_positive"
    )
    assert metadata["owner_backfill_peak_fact_provenance_schema_version"] == (
        "alignment_owner_backfill_peak_fact_provenance"
    )
    assert "_v1" not in metadata["owner_backfill_peak_fact_provenance_schema_version"]
    assert metadata["owner_backfill_peak_fact_provenance_row_count"] == "0"


def test_alignment_metadata_keeps_targeted_istd_fields_in_current_shape() -> None:
    metadata = alignment_metadata(
        discovery_batch_index=Path("batch.csv"),
        raw_dir=Path("raw"),
        dll_dir=Path("dll"),
        owner_backfill_xic_backend="raw",
        output_level="validation",
        peak_config=_peak_config(),
        targeted_istd_anchor_identity="anchor-id",
        targeted_istd_target_snapshot_id="snapshot-id",
    )

    assert metadata["schema_version"] == "alignment-results"
    assert metadata["targeted_istd_anchor_identity"] == "anchor-id"
    assert metadata["targeted_istd_target_snapshot_id"] == "snapshot-id"


def test_alignment_metadata_rejects_partial_targeted_istd_binding() -> None:
    with pytest.raises(
        ValueError,
        match="anchor identity and target snapshot id must be provided together",
    ):
        alignment_metadata(
            discovery_batch_index=Path("batch.csv"),
            raw_dir=Path("raw"),
            dll_dir=Path("dll"),
            owner_backfill_xic_backend="raw",
            output_level="validation",
            peak_config=_peak_config(),
            targeted_istd_anchor_identity="anchor-id",
        )


def test_alignment_metadata_records_owner_build_xic_backend() -> None:
    metadata = alignment_metadata(
        discovery_batch_index=Path("batch.csv"),
        raw_dir=Path("raw"),
        dll_dir=Path("dll"),
        owner_build_xic_backend="ms1_index",
        owner_backfill_xic_backend="raw",
        output_level="validation",
        peak_config=_peak_config(),
    )

    assert metadata["owner_build_xic_backend"] == "ms1_index"


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
        skipped_evidence_predicate_version="alignment-parity-scoped-test",
    )

    assert metadata["backfill_scope"] == "selected-families"
    assert metadata["output_scope"] == "diagnostic_only"
    assert metadata["selected_family_count"] == "2"
    assert metadata["selected_family_source"] == "tsv:selected.tsv"
    assert metadata["scope_warning"] == "diagnostic_only_incomplete_scope"
    assert (
        metadata["skipped_evidence_predicate_version"] == "alignment-parity-scoped-test"
    )


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
    assert outputs.matrix_identity_tsv == tmp_path / "alignment_matrix_identity.tsv"
    assert outputs.review_tsv == tmp_path / "alignment_review.tsv"
    assert outputs.backfill_cell_evidence_tsv == (
        tmp_path / "alignment_backfill_cell_evidence.tsv"
    )
    assert outputs.owner_backfill_peak_fact_provenance_tsv == (
        tmp_path / "alignment_owner_backfill_peak_fact_provenance.tsv"
    )
    assert outputs.cells_tsv is None
    assert outputs.status_matrix_tsv is None
    assert outputs.run_metadata_json is None


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
    config = AlignmentConfig()

    pipeline_outputs_module.write_outputs_atomic(
        outputs,
        matrix,
        metadata={},
        alignment_config=config,
        production_decisions=build_production_decisions(matrix, config),
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
    assert all(
        row["value"] is not None and row["availability"] == "measured"
        for row in by_metric["writer_hash_time_sec"]
    )


@pytest.mark.parametrize(
    "timing_recorder",
    [None, TimingRecorder("alignment")],
)
def test_writer_hashing_requires_instrumentation_opt_in(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    timing_recorder: TimingRecorder | None,
) -> None:
    outputs = output_paths(
        tmp_path / "out",
        output_level="validation-minimal",
        emit_alignment_cells=False,
        emit_alignment_status_matrix=False,
    )

    def reject_fingerprint(*_args, **_kwargs):
        raise AssertionError("writer fingerprinting must remain opt-in")

    monkeypatch.setattr(
        pipeline_outputs_module,
        "fingerprint_file_content",
        reject_fingerprint,
    )
    matrix = _matrix(("Sample_A", "Sample_B"))
    config = AlignmentConfig()

    pipeline_outputs_module.write_outputs_atomic(
        outputs,
        matrix,
        metadata={},
        alignment_config=config,
        production_decisions=build_production_decisions(matrix, config),
        timing_recorder=timing_recorder,
    )

    assert outputs.matrix_tsv is not None and outputs.matrix_tsv.exists()


def test_writer_mints_exact_fingerprint_receipts_without_timing_instrumentation(
    tmp_path: Path,
) -> None:
    outputs = output_paths(
        tmp_path / "out",
        output_level="validation-minimal",
        emit_alignment_cells=False,
        emit_alignment_status_matrix=False,
    )
    matrix = _matrix(("Sample_A", "Sample_B"))
    config = AlignmentConfig()
    batch = pipeline_outputs_module.write_outputs_atomic(
        outputs,
        matrix,
        metadata={},
        alignment_config=config,
        production_decisions=build_production_decisions(matrix, config),
        capture_artifact_fingerprints=True,
    )

    assert isinstance(batch, AlignmentArtifactFingerprintBatch)
    receipts = batch.receipts
    output_files = {
        path.resolve() for path in outputs.__dict__.values() if isinstance(path, Path)
    }
    assert {receipt.artifact_path for receipt in receipts} == output_files
    assert all(
        receipt.artifact_name == receipt.artifact_path.name
        and receipt.fingerprint == fingerprint_file_content(receipt.artifact_path)
        for receipt in receipts
    )


def test_writer_fingerprint_batch_rejects_missing_duplicate_or_foreign_receipts(
    tmp_path: Path,
) -> None:
    first_outputs = output_paths(
        tmp_path / "first",
        output_level="validation-minimal",
        emit_alignment_cells=False,
        emit_alignment_status_matrix=False,
    )
    second_outputs = output_paths(
        tmp_path / "second",
        output_level="validation-minimal",
        emit_alignment_cells=False,
        emit_alignment_status_matrix=False,
    )
    matrix = _matrix(("Sample_A", "Sample_B"))
    config = AlignmentConfig()
    writer_kwargs = {
        "metadata": {},
        "alignment_config": config,
        "production_decisions": build_production_decisions(matrix, config),
        "capture_artifact_fingerprints": True,
    }
    first_batch = pipeline_outputs_module.write_outputs_atomic(
        first_outputs,
        matrix,
        **writer_kwargs,
    )
    second_batch = pipeline_outputs_module.write_outputs_atomic(
        second_outputs,
        matrix,
        **writer_kwargs,
    )
    assert isinstance(first_batch, AlignmentArtifactFingerprintBatch)
    assert isinstance(second_batch, AlignmentArtifactFingerprintBatch)

    with pytest.raises(ValueError, match="exactly cover"):
        receipts_module._mint_alignment_artifact_fingerprint_batch(
            artifact_paths=first_batch.artifact_paths,
            receipts=first_batch.receipts[:-1],
        )
    with pytest.raises(ValueError, match="receipt artifact paths must be unique"):
        receipts_module._mint_alignment_artifact_fingerprint_batch(
            artifact_paths=first_batch.artifact_paths,
            receipts=(first_batch.receipts[0],) * len(first_batch.receipts),
        )
    with pytest.raises(ValueError, match="exactly cover"):
        receipts_module._mint_alignment_artifact_fingerprint_batch(
            artifact_paths=first_batch.artifact_paths,
            receipts=(*first_batch.receipts[:-1], second_batch.receipts[-1]),
        )


def test_writer_receipt_capture_reuses_instrumented_hashes_without_output_changes(
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
    config = AlignmentConfig()
    writer_args = (matrix,)
    writer_kwargs = {
        "metadata": {},
        "alignment_config": config,
        "production_decisions": build_production_decisions(matrix, config),
    }
    pipeline_outputs_module.write_outputs_atomic(
        baseline_outputs,
        *writer_args,
        **writer_kwargs,
    )

    original_fingerprint = pipeline_outputs_module.fingerprint_file_content
    fingerprint_calls = 0

    def counted_fingerprint(*args, **kwargs):
        nonlocal fingerprint_calls
        fingerprint_calls += 1
        return original_fingerprint(*args, **kwargs)

    monkeypatch.setattr(
        pipeline_outputs_module,
        "fingerprint_file_content",
        counted_fingerprint,
    )
    batch = pipeline_outputs_module.write_outputs_atomic(
        candidate_outputs,
        *writer_args,
        **writer_kwargs,
        timing_recorder=TimingRecorder("alignment", instrument_writers=True),
        capture_artifact_fingerprints=True,
    )

    assert isinstance(batch, AlignmentArtifactFingerprintBatch)
    receipts = batch.receipts
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
    assert fingerprint_calls == len(receipts) == len(candidate_files)
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
    config = AlignmentConfig()
    decisions = build_production_decisions(matrix, config)
    writer_kwargs = {
        "metadata": {},
        "alignment_config": config,
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


def test_writer_fingerprint_failure_rolls_back_without_returning_receipts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outputs = output_paths(
        tmp_path / "out",
        output_level="validation-minimal",
        emit_alignment_cells=False,
        emit_alignment_status_matrix=False,
    )
    output_files = tuple(
        path for path in outputs.__dict__.values() if isinstance(path, Path)
    )
    for path in output_files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"prior output")

    original_fingerprint = pipeline_outputs_module.fingerprint_file_content
    fingerprint_calls = 0

    def fail_second_fingerprint(*args, **kwargs):
        nonlocal fingerprint_calls
        fingerprint_calls += 1
        if fingerprint_calls == 2:
            raise RuntimeError("fingerprint failed")
        return original_fingerprint(*args, **kwargs)

    monkeypatch.setattr(
        pipeline_outputs_module,
        "fingerprint_file_content",
        fail_second_fingerprint,
    )
    matrix = _matrix(("Sample_A", "Sample_B"))
    config = AlignmentConfig()

    with pytest.raises(RuntimeError, match="fingerprint failed"):
        pipeline_outputs_module.write_outputs_atomic(
            outputs,
            matrix,
            metadata={},
            alignment_config=config,
            production_decisions=build_production_decisions(matrix, config),
            capture_artifact_fingerprints=True,
        )

    assert fingerprint_calls == 2
    assert all(path.read_bytes() == b"prior output" for path in output_files)
    assert not tuple((tmp_path / "out").glob("*.tmp"))
    assert not tuple((tmp_path / "out").glob("*.bak"))


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
    config = AlignmentConfig()

    pipeline_outputs_module.write_outputs_atomic(
        outputs,
        matrix,
        metadata={},
        alignment_config=config,
        production_decisions=build_production_decisions(matrix, config),
    )

    assert outputs.run_metadata_json is not None
    persisted = json.loads(outputs.run_metadata_json.read_text(encoding="utf-8"))
    assert persisted["owner_backfill_peak_fact_provenance_row_count"] == "1"
    assert outputs.owner_backfill_peak_fact_provenance_tsv is not None
    assert (
        len(
            outputs.owner_backfill_peak_fact_provenance_tsv.read_text(
                encoding="utf-8"
            ).splitlines()
        )
        == 2
    )


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
    config = AlignmentConfig()

    with pytest.raises(ValueError, match="does not match the typed provenance batch"):
        pipeline_outputs_module.write_outputs_atomic(
            outputs,
            matrix,
            metadata={"owner_backfill_peak_fact_provenance_row_count": "0"},
            alignment_config=config,
            production_decisions=build_production_decisions(matrix, config),
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
    config = AlignmentConfig()

    with pytest.raises(RuntimeError, match="injected status writer failure"):
        pipeline_outputs_module.write_outputs_atomic(
            outputs,
            matrix,
            metadata={},
            alignment_config=config,
            production_decisions=build_production_decisions(matrix, config),
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


def test_alignment_artifact_fingerprint_receipt_cannot_be_caller_minted(
    tmp_path: Path,
) -> None:
    path = tmp_path / "artifact.tsv"
    path.write_bytes(b"")
    fingerprint = FileContentFingerprint(content_sha256="0" * 64, byte_size=0)

    with pytest.raises(TypeError, match="_mint_token"):
        AlignmentArtifactFingerprintReceipt(
            artifact_name=path.name,
            artifact_path=path.resolve(),
            fingerprint=fingerprint,
        )
    with pytest.raises(ValueError, match="must be minted by the Alignment writer"):
        AlignmentArtifactFingerprintReceipt(
            artifact_name=path.name,
            artifact_path=path.resolve(),
            fingerprint=fingerprint,
            _mint_token=object(),
        )
    with pytest.raises(TypeError, match="_mint_token"):
        AlignmentArtifactFingerprintBatch(artifact_paths=(), receipts=())


class _Timer:
    def __init__(self, values: list[float]) -> None:
        self._values = values
        self._index = 0

    def __call__(self) -> float:
        value = self._values[self._index]
        self._index += 1
        return value
