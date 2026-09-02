import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import xic_extractor.alignment.pipeline as pipeline_module
import xic_extractor.alignment.pipeline_outputs as pipeline_outputs_module
from tests.alignment_pipeline_helpers import FakeRawOpener
from tests.alignment_pipeline_helpers import matrix as _matrix
from tests.alignment_pipeline_helpers import owner_edge_evidence as _edge_evidence
from tests.alignment_pipeline_helpers import (
    patch_owner_pipeline_to_matrix as _patch_owner_pipeline_to_matrix,
)
from tests.alignment_pipeline_helpers import peak_config as _peak_config
from tests.alignment_pipeline_helpers import write_batch as _write_batch
from tests.test_alignment_owner_clustering import _owner
from xic_extractor.alignment import AlignmentConfig
from xic_extractor.alignment import artifact_write_receipts as receipts_module
from xic_extractor.alignment.artifact_write_receipts import (
    AlignmentArtifactFingerprintBatch,
    AlignmentArtifactFingerprintReceipt,
)
from xic_extractor.alignment.drift_evidence import (
    build_targeted_istd_identity_anchor,
)
from xic_extractor.alignment.pipeline_outputs import alignment_metadata, output_paths
from xic_extractor.alignment.production_decisions import build_production_decisions
from xic_extractor.configuration.models import Target
from xic_extractor.configuration.target_identity import mint_target_list_snapshot
from xic_extractor.diagnostics.timing import TimingRecorder
from xic_extractor.processing_identity import (
    FileContentFingerprint,
    fingerprint_file_content,
)


def test_alignment_metadata_records_baseline_audit_method() -> None:
    peak_config = replace(_peak_config(), baseline_audit_method="asls")

    metadata = alignment_metadata(
        discovery_batch_index=Path("batch.csv"),
        raw_dir=Path("raw"),
        dll_dir=Path("dll"),
        owner_backfill_xic_backend="raw",
        output_level="validation",
        peak_config=peak_config,
    )

    assert metadata["schema_version"] == "alignment-results-v3"
    assert (
        metadata["cross_sample_peak_group_policy"]
        == "cross_sample_peak_group_hypothesis_v1"
    )
    assert metadata["public_family_id_policy"] == "fam_compatibility_id"
    assert (
        metadata["group_delivery_policy"]
        == "owner_group_delivery_successor_projection_v1"
    )
    assert metadata["gap_fill_policy"] == "missing_observation_gap_fill_v1"
    assert (
        metadata["legacy_owner_backfill_role"]
        == "owner_backfill_as_gap_fill_materialization"
    )
    assert (
        metadata["pre_backfill_projection_policy"]
        == "pre_backfill_successor_projection_required_when_enabled"
    )
    assert (
        metadata["matrix_value_policy"] == "canonical_signal_profile_selected_area_v1"
    )
    assert metadata["baseline_audit_method"] == "asls"
    assert metadata["owner_build_xic_backend"] == "raw"
    assert metadata["backfill_scope"] == "full-audit"
    assert metadata["output_scope"] == "full-audit"
    assert metadata["selected_family_source"] == ""
    assert metadata["scope_warning"] == ""
    assert metadata["signal_processing_profile_schema_version"] == (
        "signal_processing_profile_v1"
    )
    assert metadata["signal_processing_profile_id"].startswith("signal-profile.v1:")
    assert len(metadata["signal_processing_profile_hash"]) == 64
    assert metadata["signal_processing_method"] == "gaussian"
    assert metadata["signal_processing_window_points"] == "15"
    assert metadata["physical_signal_formula"] == (
        "gaussian_raw_minus_asls_raw_positive_v1"
    )
    assert metadata["owner_backfill_peak_fact_provenance_schema_version"] == (
        "alignment_owner_backfill_peak_fact_provenance_v2"
    )
    assert "_v1" not in metadata["owner_backfill_peak_fact_provenance_schema_version"]
    assert metadata["owner_backfill_peak_fact_provenance_row_count"] == "0"


def test_alignment_metadata_versions_targeted_istd_anchor_shape() -> None:
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

    assert metadata["schema_version"] == "alignment-results-v4"
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
        skipped_evidence_predicate_version="p7-test",
    )

    assert metadata["backfill_scope"] == "selected-families"
    assert metadata["output_scope"] == "diagnostic_only"
    assert metadata["selected_family_count"] == "2"
    assert metadata["selected_family_source"] == "tsv:selected.tsv"
    assert metadata["scope_warning"] == "diagnostic_only_incomplete_scope"
    assert metadata["skipped_evidence_predicate_version"] == "p7-test"


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
    assert metadata["request_plan_version"] == "p7-owner-backfill-request-plan-v1"
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
        emit_skipped_evidence_ledger=True,
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
    assert outputs.workbook is None
    assert outputs.review_html is None
    assert outputs.edge_evidence_tsv is None
    assert outputs.status_matrix_tsv is None
    assert outputs.event_to_owner_tsv is None
    assert outputs.ambiguous_owners_tsv is None
    assert (
        outputs.skipped_evidence_ledger_tsv == tmp_path / "skipped_evidence_ledger.tsv"
    )
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


def test_pipeline_target_anchor_metadata_requires_typed_owner_anchor() -> None:
    fake_lookup = SimpleNamespace(
        istd_identity_anchor=SimpleNamespace(
            target_config_identity="forged",
            target_snapshot=SimpleNamespace(snapshot_id="forged-snapshot"),
        )
    )
    with pytest.raises(TypeError, match="owner-minted"):
        pipeline_module._typed_targeted_istd_anchor(fake_lookup)

    target = Target(
        label="d3-5-hmdC",
        mz=261.127276,
        rt_min=8.0,
        rt_max=10.0,
        ppm_tol=20.0,
        neutral_loss_da=116.0474,
        nl_ppm_warn=20.0,
        nl_ppm_max=50.0,
        is_istd=True,
        istd_pair="",
        isotope_label_type="deuterated",
        paired_rt_relation="none",
        sample_applicability="all",
    )
    anchor = build_targeted_istd_identity_anchor(
        mint_target_list_snapshot(
            (target,),
            identity_namespace="test-pipeline-targeted-istd",
        ),
        observed_labels=("d3-5-hmdC",),
    )
    assert pipeline_module._typed_targeted_istd_anchor(
        SimpleNamespace(istd_identity_anchor=anchor)
    ) is anchor


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
        ownership=SimpleNamespace(assignments=(), ambiguous_records=()),
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
        ownership=SimpleNamespace(assignments=(), ambiguous_records=()),
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
        ownership=SimpleNamespace(assignments=(), ambiguous_records=()),
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
        "ownership": SimpleNamespace(assignments=(), ambiguous_records=()),
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
        "ownership": SimpleNamespace(assignments=(), ambiguous_records=()),
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
        "ownership": SimpleNamespace(assignments=(), ambiguous_records=()),
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
            ownership=SimpleNamespace(assignments=(), ambiguous_records=()),
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
        ownership=SimpleNamespace(assignments=(), ambiguous_records=()),
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
            ownership=SimpleNamespace(assignments=(), ambiguous_records=()),
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
            ownership=SimpleNamespace(assignments=(), ambiguous_records=()),
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


def test_pipeline_debug_flags_write_optional_outputs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("Sample_A",))
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
    _patch_owner_pipeline_to_matrix(monkeypatch)

    outputs = pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=raw_dir,
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        emit_alignment_cells=True,
        emit_alignment_integration_audit=True,
        emit_alignment_backfill_seed_audit=True,
        emit_alignment_backfill_candidate_audit=True,
        emit_alignment_status_matrix=True,
        raw_opener=FakeRawOpener(),
    )

    assert outputs.cells_tsv == tmp_path / "out" / "alignment_cells.tsv"
    assert (
        outputs.integration_audit_tsv
        == tmp_path / "out" / "alignment_cell_integration_audit.tsv"
    )
    assert (
        outputs.backfill_seed_audit_tsv
        == tmp_path / "out" / "alignment_owner_backfill_seed_audit.tsv"
    )
    assert (
        outputs.backfill_candidate_audit_tsv
        == tmp_path / "out" / "alignment_owner_backfill_candidate_audit.tsv"
    )
    assert outputs.status_matrix_tsv == tmp_path / "out" / "alignment_matrix_status.tsv"
    assert outputs.matrix_identity_tsv is None
    assert outputs.cells_tsv.exists()
    assert outputs.integration_audit_tsv.exists()
    assert outputs.backfill_seed_audit_tsv.exists()
    assert outputs.backfill_candidate_audit_tsv.exists()
    assert outputs.status_matrix_tsv.exists()


def _status_matrix_sample_columns(
    status_matrix_tsv: Path,
    sample_set: set[str],
) -> list[str]:
    header = status_matrix_tsv.read_text(encoding="utf-8").splitlines()[0]
    return [column for column in header.split("\t") if column in sample_set]


def test_pipeline_orders_sample_columns_by_injection_order(
    tmp_path: Path,
    monkeypatch,
) -> None:
    samples = ("Sample_C", "Sample_A", "Sample_X", "Sample_B")
    batch_index = _write_batch(tmp_path, samples)
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    for stem in samples:
        (raw_dir / f"{stem}.raw").write_text("raw", encoding="utf-8")
    injection_csv = tmp_path / "instrument_qc_injection_order.csv"
    injection_csv.write_text(
        "Sample_Name,Injection_Order\nSample_A,1\nSample_B,2\nSample_C,3\n",
        encoding="utf-8",
    )
    _patch_owner_pipeline_to_matrix(monkeypatch)

    outputs = pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=raw_dir,
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        emit_alignment_status_matrix=True,
        sample_column_injection_order=injection_csv,
        raw_opener=FakeRawOpener(),
    )

    # earliest->latest injected first; unlisted Sample_X kept at the end.
    assert _status_matrix_sample_columns(
        outputs.status_matrix_tsv,
        set(samples),
    ) == ["Sample_A", "Sample_B", "Sample_C", "Sample_X"]


def test_pipeline_reads_injection_order_once_for_grouping_and_matrix_columns(
    tmp_path: Path,
    monkeypatch,
) -> None:
    samples = ("Sample_C", "Sample_A", "Sample_X", "Sample_B")
    batch_index = _write_batch(tmp_path, samples)
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    for stem in samples:
        (raw_dir / f"{stem}.raw").write_text("raw", encoding="utf-8")
    injection_csv = tmp_path / "instrument_qc_injection_order.csv"
    injection_order = {"Sample_A": 1, "Sample_B": 2, "Sample_C": 3}
    read_paths = []
    captured = {}
    real_cluster_owners = pipeline_module.cluster_sample_local_owners
    _patch_owner_pipeline_to_matrix(monkeypatch)
    owners = (
        _owner("Sample_A", "a", apex_rt=10.00),
        _owner("Sample_B", "b", apex_rt=10.75),
        _owner("Sample_C", "c", apex_rt=11.50),
    )

    def fake_read_injection_order(path: Path):
        read_paths.append(path)
        return injection_order, "test_injection_order"

    def fake_owner_matrix(features, *, sample_order, **kwargs):
        captured["features"] = features
        return _matrix(sample_order)

    monkeypatch.setattr(
        pipeline_module,
        "_read_sample_column_injection_order",
        fake_read_injection_order,
    )
    monkeypatch.setattr(
        pipeline_module,
        "build_sample_local_owners",
        lambda candidates, **kwargs: SimpleNamespace(
            owners=owners,
            assignments=(),
            ambiguous_records=(),
        ),
    )
    monkeypatch.setattr(
        pipeline_module,
        "cluster_sample_local_owners",
        real_cluster_owners,
    )
    monkeypatch.setattr(
        pipeline_module,
        "build_owner_alignment_matrix",
        fake_owner_matrix,
    )

    outputs = pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=raw_dir,
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        emit_alignment_status_matrix=True,
        sample_column_injection_order=injection_csv,
        raw_opener=FakeRawOpener(),
    )

    assert read_paths == [injection_csv]
    assert len(captured["features"]) == 1
    assert (
        captured["features"][0].evidence
        == "owner_injection_trajectory;owner_count=3"
    )
    assert _status_matrix_sample_columns(
        outputs.status_matrix_tsv,
        set(samples),
    ) == ["Sample_A", "Sample_B", "Sample_C", "Sample_X"]


def test_pipeline_orders_sample_columns_by_sample_metadata_v1(
    tmp_path: Path,
    monkeypatch,
) -> None:
    samples = ("Sample_C", "Sample_A", "Sample_X", "Sample_B")
    batch_index = _write_batch(tmp_path, samples)
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    for stem in samples:
        (raw_dir / f"{stem}.raw").write_text("raw", encoding="utf-8")
    sample_metadata_tsv = tmp_path / "sample_metadata.tsv"
    sample_metadata_tsv.write_text(
        "\t".join(
            (
                "schema_version",
                "sample_name",
                "raw_stem",
                "injection_order",
                "sample_role",
                "batch_id",
                "prep_batch_id",
                "matrix_type",
                "group",
                "excluded",
                "exclusion_reason",
            )
        )
        + "\n"
        + "\n".join(
            (
                "sample_metadata_v1\tAlpha\tSample_A\t1\tstudy_sample\tB1\t\tplasma\tcase\tFALSE\t",
                "sample_metadata_v1\tBeta\tSample_B\t2\tqc\tB1\t\tplasma\tqc\tFALSE\t",
                "sample_metadata_v1\tGamma\tSample_C\t3\tblank\tB1\t\tplasma\tblank\tFALSE\t",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    _patch_owner_pipeline_to_matrix(monkeypatch)

    outputs = pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=raw_dir,
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        emit_alignment_status_matrix=True,
        sample_column_injection_order=sample_metadata_tsv,
        raw_opener=FakeRawOpener(),
    )

    assert _status_matrix_sample_columns(
        outputs.status_matrix_tsv,
        set(samples),
    ) == ["Sample_A", "Sample_B", "Sample_C", "Sample_X"]


def test_pipeline_keeps_input_sample_order_without_injection_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    samples = ("Sample_C", "Sample_A", "Sample_B")
    batch_index = _write_batch(tmp_path, samples)
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    for stem in samples:
        (raw_dir / f"{stem}.raw").write_text("raw", encoding="utf-8")
    _patch_owner_pipeline_to_matrix(monkeypatch)

    outputs = pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=raw_dir,
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        emit_alignment_status_matrix=True,
        raw_opener=FakeRawOpener(),
    )

    assert _status_matrix_sample_columns(
        outputs.status_matrix_tsv,
        set(samples),
    ) == ["Sample_C", "Sample_A", "Sample_B"]


def test_pipeline_writes_run_metadata_sidecar_for_p7_scoped_runs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Package HOLD/pre-RAW behavior has separate guard coverage; this metadata
    # oracle is intentionally scoped below the product-authority seam.
    monkeypatch.setattr(
        pipeline_module,
        "require_product_quantitative_projection_authority",
        lambda **_kwargs: None,
    )
    batch_index = _write_batch(tmp_path, ("Sample_A", "Sample_B"))
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
    (raw_dir / "Sample_B.raw").write_text("raw", encoding="utf-8")
    _patch_owner_pipeline_to_matrix(monkeypatch)

    outputs = pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=raw_dir,
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        raw_opener=FakeRawOpener(),
        backfill_scope="production-equivalent",
    )

    assert outputs.run_metadata_json == tmp_path / "out" / "alignment_run_metadata.json"
    payload = json.loads(outputs.run_metadata_json.read_text(encoding="utf-8"))
    assert payload["backfill_scope"] == "production-equivalent"
    assert payload["audit_evidence_mode"] == "none"
    assert payload["requested_audit_evidence_mode"] == "auto"
    assert payload["request_plan_version"] == "p7-owner-backfill-request-plan-v1"


def test_pipeline_enables_region_audit_only_when_alignment_cells_are_emitted(
    tmp_path: Path,
    monkeypatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("Sample_A",))
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
    calls = {}

    monkeypatch.setattr(
        pipeline_module,
        "build_sample_local_owners",
        lambda candidates, **kwargs: SimpleNamespace(
            owners=("owner",),
            assignments=(),
            ambiguous_records=(),
        ),
    )
    monkeypatch.setattr(
        pipeline_module,
        "cluster_sample_local_owners",
        lambda owners, **kwargs: (
            "feature",
        ),
    )
    monkeypatch.setattr(
        pipeline_module,
        "review_only_features_from_ambiguous_records",
        lambda records, *, start_index: (),
    )

    def fake_owner_backfill(features, **kwargs):
        calls["emit_region_audit"] = kwargs["emit_region_audit"]
        return ()

    monkeypatch.setattr(
        pipeline_module,
        "build_owner_backfill_cells",
        fake_owner_backfill,
    )
    monkeypatch.setattr(
        pipeline_module,
        "build_owner_alignment_matrix",
        lambda features, *, sample_order, **kwargs: _matrix(sample_order),
    )

    pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=raw_dir,
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        emit_alignment_cells=True,
        raw_opener=FakeRawOpener(),
    )

    assert calls["emit_region_audit"] is True


def test_pipeline_enables_region_audit_when_integration_sidecar_is_emitted(
    tmp_path: Path,
    monkeypatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("Sample_A",))
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
    calls = {}

    monkeypatch.setattr(
        pipeline_module,
        "build_sample_local_owners",
        lambda candidates, **kwargs: SimpleNamespace(
            owners=("owner",),
            assignments=(),
            ambiguous_records=(),
        ),
    )
    monkeypatch.setattr(
        pipeline_module,
        "cluster_sample_local_owners",
        lambda owners, **kwargs: (
            "feature",
        ),
    )
    monkeypatch.setattr(
        pipeline_module,
        "review_only_features_from_ambiguous_records",
        lambda records, *, start_index: (),
    )

    def fake_owner_backfill(features, **kwargs):
        calls["emit_region_audit"] = kwargs["emit_region_audit"]
        return ()

    monkeypatch.setattr(
        pipeline_module,
        "build_owner_backfill_cells",
        fake_owner_backfill,
    )
    monkeypatch.setattr(
        pipeline_module,
        "build_owner_alignment_matrix",
        lambda features, *, sample_order, **kwargs: _matrix(sample_order),
    )

    outputs = pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=raw_dir,
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        emit_alignment_integration_audit=True,
        raw_opener=FakeRawOpener(),
    )

    assert outputs.cells_tsv is None
    assert outputs.integration_audit_tsv is not None
    assert calls["emit_region_audit"] is True


def test_pipeline_backfill_seed_sidecar_does_not_force_alignment_cells_or_region_audit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("Sample_A",))
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
    calls = {}

    monkeypatch.setattr(
        pipeline_module,
        "build_sample_local_owners",
        lambda candidates, **kwargs: SimpleNamespace(
            owners=("owner",),
            assignments=(),
            ambiguous_records=(),
        ),
    )
    monkeypatch.setattr(
        pipeline_module,
        "cluster_sample_local_owners",
        lambda owners, **kwargs: (
            "feature",
        ),
    )
    monkeypatch.setattr(
        pipeline_module,
        "review_only_features_from_ambiguous_records",
        lambda records, *, start_index: (),
    )

    def fake_owner_backfill(features, **kwargs):
        calls["emit_region_audit"] = kwargs["emit_region_audit"]
        return ()

    monkeypatch.setattr(
        pipeline_module,
        "build_owner_backfill_cells",
        fake_owner_backfill,
    )
    monkeypatch.setattr(
        pipeline_module,
        "build_owner_alignment_matrix",
        lambda features, *, sample_order, **kwargs: _matrix(sample_order),
    )

    outputs = pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=raw_dir,
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        emit_alignment_backfill_seed_audit=True,
        raw_opener=FakeRawOpener(),
    )

    assert outputs.cells_tsv is None
    assert outputs.backfill_seed_audit_tsv is not None
    assert outputs.backfill_seed_audit_tsv.exists()
    assert outputs.backfill_candidate_audit_tsv is None
    assert calls["emit_region_audit"] is False


def test_pipeline_backfill_candidate_audit_requires_own_flag(
    tmp_path: Path,
    monkeypatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("Sample_A",))
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
    calls = {}

    monkeypatch.setattr(
        pipeline_module,
        "build_sample_local_owners",
        lambda candidates, **kwargs: SimpleNamespace(
            owners=("owner",),
            assignments=(),
            ambiguous_records=(),
        ),
    )
    monkeypatch.setattr(
        pipeline_module,
        "cluster_sample_local_owners",
        lambda owners, **kwargs: (
            "feature",
        ),
    )
    monkeypatch.setattr(
        pipeline_module,
        "review_only_features_from_ambiguous_records",
        lambda records, *, start_index: (),
    )

    def fake_owner_backfill(features, **kwargs):
        calls["emit_region_audit"] = kwargs["emit_region_audit"]
        return ()

    monkeypatch.setattr(
        pipeline_module,
        "build_owner_backfill_cells",
        fake_owner_backfill,
    )
    monkeypatch.setattr(
        pipeline_module,
        "build_owner_alignment_matrix",
        lambda features, *, sample_order, **kwargs: _matrix(sample_order),
    )

    outputs = pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=raw_dir,
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        emit_alignment_backfill_candidate_audit=True,
        raw_opener=FakeRawOpener(),
    )

    assert outputs.cells_tsv is None
    assert outputs.backfill_seed_audit_tsv is None
    assert outputs.backfill_candidate_audit_tsv is not None
    assert outputs.backfill_candidate_audit_tsv.exists()
    assert calls["emit_region_audit"] is False


def test_run_alignment_production_level_writes_matrix_only(
    tmp_path: Path,
    monkeypatch,
) -> None:
    outputs = _run_minimal_alignment(
        tmp_path,
        monkeypatch,
        output_level="production",
    )

    names = sorted(path.name for path in (tmp_path / "out").iterdir())
    assert names == ["alignment_matrix.tsv"]
    assert outputs.workbook is None
    assert outputs.matrix_tsv == tmp_path / "out" / "alignment_matrix.tsv"
    assert outputs.matrix_identity_tsv is None
    assert outputs.review_html is None
    assert outputs.review_tsv is None


def test_run_alignment_debug_level_writes_machine_and_debug_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    outputs = _run_minimal_alignment(tmp_path, monkeypatch, output_level="debug")

    names = sorted(path.name for path in (tmp_path / "out").iterdir())
    assert names == [
        "alignment_cells.tsv",
        "alignment_matrix.tsv",
        "alignment_matrix_identity.tsv",
        "alignment_matrix_status.tsv",
        "alignment_owner_backfill_peak_fact_provenance.tsv",
        "alignment_results.xlsx",
        "alignment_review.tsv",
        "ambiguous_ms1_owners.tsv",
        "event_to_ms1_owner.tsv",
        "owner_edge_evidence.tsv",
        "review_report.html",
    ]
    assert outputs.event_to_owner_tsv is not None
    assert outputs.ambiguous_owners_tsv is not None
    assert outputs.edge_evidence_tsv is not None


def test_run_alignment_output_level_does_not_emit_integration_audit_by_default(
    tmp_path: Path,
    monkeypatch,
) -> None:
    outputs = _run_minimal_alignment(tmp_path, monkeypatch, output_level="validation")

    names = sorted(path.name for path in (tmp_path / "out").iterdir())
    assert "alignment_cell_integration_audit.tsv" not in names
    assert "alignment_owner_backfill_seed_audit.tsv" not in names
    assert "alignment_owner_backfill_candidate_audit.tsv" not in names
    assert outputs.integration_audit_tsv is None
    assert outputs.backfill_seed_audit_tsv is None
    assert outputs.backfill_candidate_audit_tsv is None


def test_run_alignment_validation_minimal_writes_machine_gate_surface_only(
    tmp_path: Path,
    monkeypatch,
) -> None:
    outputs = _run_minimal_alignment(
        tmp_path,
        monkeypatch,
        output_level="validation-minimal",
    )

    names = sorted(path.name for path in (tmp_path / "out").iterdir())
    assert names == [
        "alignment_backfill_cell_evidence.tsv",
        "alignment_matrix.tsv",
        "alignment_matrix_identity.tsv",
        "alignment_owner_backfill_peak_fact_provenance.tsv",
        "alignment_review.tsv",
    ]
    assert outputs.matrix_tsv == tmp_path / "out" / "alignment_matrix.tsv"
    assert (
        outputs.matrix_identity_tsv
        == tmp_path / "out" / "alignment_matrix_identity.tsv"
    )
    assert outputs.review_tsv == tmp_path / "out" / "alignment_review.tsv"
    assert outputs.backfill_cell_evidence_tsv == (
        tmp_path / "out" / "alignment_backfill_cell_evidence.tsv"
    )
    assert outputs.cells_tsv is None
    assert outputs.workbook is None
    assert outputs.review_html is None
    assert outputs.edge_evidence_tsv is None
    assert outputs.status_matrix_tsv is None


def test_validation_minimal_auto_mode_does_not_enable_heavy_region_audit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("Sample_A",))
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
    calls = {}

    monkeypatch.setattr(
        pipeline_module,
        "build_sample_local_owners",
        lambda candidates, **kwargs: SimpleNamespace(
            owners=("owner",),
            assignments=(),
            ambiguous_records=(),
        ),
    )
    monkeypatch.setattr(
        pipeline_module,
        "cluster_sample_local_owners",
        lambda owners, **kwargs: (
            "feature",
        ),
    )
    monkeypatch.setattr(
        pipeline_module,
        "review_only_features_from_ambiguous_records",
        lambda records, *, start_index: (),
    )

    def fake_owner_backfill(features, **kwargs):
        calls["emit_region_audit"] = kwargs["emit_region_audit"]
        calls["audit_evidence_mode"] = kwargs["audit_evidence_mode"]
        return ()

    monkeypatch.setattr(
        pipeline_module,
        "build_owner_backfill_cells",
        fake_owner_backfill,
    )
    monkeypatch.setattr(
        pipeline_module,
        "build_owner_alignment_matrix",
        lambda features, *, sample_order, **kwargs: _matrix(sample_order),
    )

    pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=raw_dir,
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        output_level="validation-minimal",
        raw_opener=FakeRawOpener(),
    )

    assert calls["emit_region_audit"] is False
    assert calls["audit_evidence_mode"] == "none"


def test_run_alignment_validation_level_writes_owner_edge_evidence_tsv(
    tmp_path: Path,
    monkeypatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("Sample_A",))
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
    _patch_owner_pipeline_to_matrix(monkeypatch)

    def fake_cluster_owners(
        owners,
        *,
        config,
        sample_universe=None,
        injection_order=None,
        drift_lookup=None,
        edge_evidence_sink=None,
    ):
        edge_evidence_sink.append(_edge_evidence())
        return ()

    monkeypatch.setattr(
        pipeline_module,
        "cluster_sample_local_owners",
        fake_cluster_owners,
    )

    outputs = pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=raw_dir,
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        output_level="validation",
        raw_opener=FakeRawOpener(),
    )

    assert outputs.edge_evidence_tsv == tmp_path / "out" / "owner_edge_evidence.tsv"
    lines = outputs.edge_evidence_tsv.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("left_owner_id\tright_owner_id\t")
    assert lines[1].startswith("OWN-Sample_A-000001\tOWN-Sample_B-000001\t")


def test_run_alignment_default_writes_matrix_only(
    tmp_path: Path,
    monkeypatch,
) -> None:
    outputs = _run_minimal_alignment(tmp_path, monkeypatch)

    names = sorted(path.name for path in (tmp_path / "out").iterdir())
    assert names == ["alignment_matrix.tsv"]
    assert outputs.owner_backfill_peak_fact_provenance_tsv is None


def _run_minimal_alignment(
    tmp_path: Path,
    monkeypatch,
    *,
    output_level: str | None = None,
) -> pipeline_module.AlignmentRunOutputs:
    batch_index = _write_batch(tmp_path, ("Sample_A",))
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
    _patch_owner_pipeline_to_matrix(monkeypatch)
    kwargs = {}
    if output_level is not None:
        kwargs["output_level"] = output_level
    return pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=raw_dir,
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        raw_opener=FakeRawOpener(),
        **kwargs,
    )
