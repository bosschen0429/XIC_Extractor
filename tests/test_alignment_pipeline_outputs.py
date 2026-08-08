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
from xic_extractor.alignment import AlignmentConfig
from xic_extractor.alignment.pipeline_outputs import alignment_metadata, output_paths
from xic_extractor.diagnostics.timing import TimingRecorder


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
        metadata["matrix_value_policy"]
        == "gaussian15_positive_asls_residual_primary"
    )
    assert metadata["baseline_audit_method"] == "asls"
    assert metadata["owner_build_xic_backend"] == "raw"
    assert metadata["backfill_scope"] == "full-audit"
    assert metadata["output_scope"] == "full-audit"
    assert metadata["selected_family_source"] == ""
    assert metadata["scope_warning"] == ""


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


def test_write_outputs_reuses_production_decisions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    original = pipeline_outputs_module.build_production_decisions
    calls = 0

    def counted_build_production_decisions(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(
        pipeline_outputs_module,
        "build_production_decisions",
        counted_build_production_decisions,
    )
    outputs = output_paths(
        tmp_path,
        output_level="validation-minimal",
        emit_alignment_cells=False,
        emit_alignment_status_matrix=False,
    )

    pipeline_outputs_module.write_outputs_atomic(
        outputs,
        _matrix(("Sample_A", "Sample_B")),
        metadata={},
        ownership=SimpleNamespace(assignments=(), ambiguous_records=()),
        alignment_config=AlignmentConfig(),
    )

    assert calls == 1
    assert outputs.matrix_tsv is not None and outputs.matrix_tsv.exists()
    assert (
        outputs.matrix_identity_tsv is not None
        and outputs.matrix_identity_tsv.exists()
    )
    assert outputs.review_tsv is not None and outputs.review_tsv.exists()
    assert (
        outputs.backfill_cell_evidence_tsv is not None
        and outputs.backfill_cell_evidence_tsv.exists()
    )


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

    pipeline_outputs_module.write_outputs_atomic(
        outputs,
        _matrix(("Sample_A", "Sample_B")),
        metadata={},
        ownership=SimpleNamespace(assignments=(), ambiguous_records=()),
        alignment_config=AlignmentConfig(),
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

    pipeline_outputs_module.write_outputs_atomic(
        outputs,
        _matrix(("Sample_A", "Sample_B")),
        metadata={},
        ownership=SimpleNamespace(assignments=(), ambiguous_records=()),
        alignment_config=AlignmentConfig(),
        timing_recorder=timing_recorder,
    )

    assert outputs.matrix_tsv is not None and outputs.matrix_tsv.exists()


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
    assert (
        outputs.matrix_identity_tsv
        == tmp_path / "out" / "alignment_matrix_identity.tsv"
    )
    assert outputs.cells_tsv.exists()
    assert outputs.matrix_identity_tsv.exists()
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
        lambda owners, *, config, drift_lookup=None, edge_evidence_sink=None: (
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
        lambda owners, *, config, drift_lookup=None, edge_evidence_sink=None: (
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
        lambda owners, *, config, drift_lookup=None, edge_evidence_sink=None: (
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
        lambda owners, *, config, drift_lookup=None, edge_evidence_sink=None: (
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


def test_run_alignment_production_level_writes_user_artifacts_and_identity_tsv(
    tmp_path: Path,
    monkeypatch,
) -> None:
    outputs = _run_minimal_alignment(
        tmp_path,
        monkeypatch,
        output_level="production",
    )

    names = sorted(path.name for path in (tmp_path / "out").iterdir())
    # alignment_matrix.tsv is part of the production contract: it is the
    # downstream-consumed matrix every run must emit (see output_levels.py).
    assert names == [
        "alignment_matrix.tsv",
        "alignment_matrix_identity.tsv",
        "alignment_results.xlsx",
        "review_report.html",
    ]
    assert outputs.workbook == tmp_path / "out" / "alignment_results.xlsx"
    assert outputs.matrix_tsv == tmp_path / "out" / "alignment_matrix.tsv"
    assert (
        outputs.matrix_identity_tsv
        == tmp_path / "out" / "alignment_matrix_identity.tsv"
    )
    assert outputs.review_html == tmp_path / "out" / "review_report.html"
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
        lambda owners, *, config, drift_lookup=None, edge_evidence_sink=None: (
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


def test_run_alignment_default_stays_machine_until_owner_validation_acceptance(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _run_minimal_alignment(tmp_path, monkeypatch)

    names = sorted(path.name for path in (tmp_path / "out").iterdir())
    assert names == [
        "alignment_matrix.tsv",
        "alignment_matrix_identity.tsv",
        "alignment_results.xlsx",
        "alignment_review.tsv",
        "review_report.html",
    ]


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
