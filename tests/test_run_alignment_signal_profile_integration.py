from __future__ import annotations

import csv
import json
import multiprocessing
import os
import pickle
from collections.abc import Mapping, Sequence
from concurrent.futures import Future
from dataclasses import dataclass, fields, is_dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
import openpyxl
import pytest

import xic_extractor.alignment.backfill_transaction as backfill_transaction_module
import xic_extractor.alignment.pipeline as pipeline_module
import xic_extractor.alignment.process_backend as process_backend_module
import xic_extractor.alignment.process_execution as process_execution_module
from scripts import run_alignment
from tests.alignment_pipeline_helpers import write_owner_closed_partial_batch
from xic_extractor.alignment.claim_registry import apply_ms1_peak_claim_registry
from xic_extractor.alignment.matrix import AlignedCell, AlignmentMatrix
from xic_extractor.alignment.output_levels import artifact_names_for_output_level
from xic_extractor.alignment.owner_backfill_peak_fact_provenance import (
    SELECTED_INTEGRATION_AUTHORITY_FIELDS,
    compose_owner_backfill_peak_fact_provenance,
    owner_backfill_authority_cell_projection,
    owner_backfill_cell_projection,
    read_owner_backfill_peak_fact_provenance_tsv,
    selected_integration_authority_projection,
)
from xic_extractor.alignment.owner_matrix import build_owner_alignment_matrix
from xic_extractor.alignment.ownership import OwnershipBuildResult
from xic_extractor.alignment.pipeline_outputs import (
    AlignmentRunOutputs,
    write_outputs_atomic,
)
from xic_extractor.alignment.pre_backfill_consolidation import (
    recenter_pre_backfill_identity_families,
)
from xic_extractor.alignment.primary_consolidation import (
    consolidate_primary_family_rows,
)
from xic_extractor.alignment.process_backend import (
    OwnerBackfillSampleJob,
    OwnerBackfillSampleResult,
    OwnerBuildProcessOutput,
    OwnerBuildSampleJob,
    OwnerBuildSampleResult,
)
from xic_extractor.alignment.production_decisions import (
    build_production_decisions as build_live_production_decisions,
)
from xic_extractor.alignment.production_decisions import (
    production_cell_decision_projection,
)
from xic_extractor.peak_detection.integration_audit import CellIntegrationAuditSummary
from xic_extractor.peak_detection.integration_result import IntegrationResult
from xic_extractor.peak_detection.signal_processing_profile import (
    resolve_signal_processing_profile,
)
from xic_extractor.processing_identity import build_acquisition_source_identity
from xic_extractor.xic_models import XICRequest, XICTrace

_DETERMINISTIC_ARTIFACTS = (
    "review_report.html",
    "alignment_matrix.tsv",
    "alignment_matrix_identity.tsv",
    "alignment_owner_backfill_peak_fact_provenance.tsv",
    "alignment_review.tsv",
    "alignment_cells.tsv",
    "alignment_matrix_status.tsv",
    "event_to_ms1_owner.tsv",
    "ambiguous_ms1_owners.tsv",
    "owner_edge_evidence.tsv",
)
_AUDIT_MODE_METADATA_KEYS = frozenset(
    {
        "audit_evidence_mode",
        "requested_audit_evidence_mode",
        "heavy_audit_enabled",
        "audit_evidence_mode_reason",
    }
)
_AUDIT_SHEET_WHITELIST = frozenset(
    {
        "region_decision_status",
        "region_decision_class",
        "region_product_action",
        "region_promotion_reason",
        "region_baseline_method",
    }
)
_ALIGNMENT_CELLS_AUDIT_WHITELIST = frozenset(
    {
        "region_candidate_count",
        "region_selected_proposal_sources",
        "region_selected_merge_note",
        "region_shadow_status",
        "region_shadow_verdict",
        "region_merge_suggestion_source",
        "region_area_ratio",
        "region_selected_interval_count",
        "region_selected_interval_gap_max_min",
        "region_local_mixture_diagnostic",
        "region_local_mixture_reason",
        "region_review_reason",
        "region_decision_status",
        "region_decision_class",
        "region_product_action",
        "region_promotion_reason",
        "region_baseline_method",
    }
)
_MUTABLE_PERSISTED_AUDIT_FIELDS = (
    "area_uncertainty",
    "area_uncertainty_formula_version",
    "baseline_residual_mad",
    "area_uncertainty_noise_source",
    "baseline_score",
    "uncertainty_fraction",
    "baseline_fraction",
    "integration_scan_count",
)
_AUDIT_MODE_HARD_ZERO_SUMMARY_FIELDS = (
    "raw_area",
    "area_baseline_corrected",
    "baseline_type",
)
_UNPERSISTED_ASLS_SUMMARY_FIELDS = (
    "area_baseline_corrected_asls",
    "baseline_score_asls",
)
_EXCLUDED_SELECTED_INTEGRATION_AUDIT_FIELDS = (
    "area_uncertainty",
    "area_uncertainty_formula_version",
    "baseline_residual_mad",
    "area_uncertainty_noise_source",
    "baseline_score",
)
_SAMPLE_ORDER = ("Sample_A", "Sample_B", "Sample_C", "Sample_D")


@dataclass(frozen=True)
class _Run:
    output_dir: Path
    profile_rows: tuple[Any, ...]
    cell_rows: tuple[dict[str, str], ...]
    acquisition_counts: tuple[int, int, int]
    metadata: dict[str, str] | None


class _FakePackageRaw:
    raw_chromatogram_call_count: int
    open_count = 0
    logical_xic_count = 0
    vendor_call_count = 0

    def __init__(self, raw_path: Path) -> None:
        type(self).open_count += 1
        self.raw_path = raw_path
        self.raw_chromatogram_call_count = 0
        self.acquisition_source = build_acquisition_source_identity(
            raw_path,
            acquisition_format="thermo_raw",
            observation_namespace="thermo_raw_file_reader:Device.MS:1",
        )

    def __enter__(self) -> _FakePackageRaw:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def extract_xic(
        self,
        mz: float,
        rt_min: float,
        rt_max: float,
        ppm_tol: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        del mz, ppm_tol
        self.raw_chromatogram_call_count += 1
        type(self).logical_xic_count += 1
        type(self).vendor_call_count += 1
        return _trace_arrays(rt_min, rt_max)

    def extract_xic_many(
        self,
        requests: tuple[XICRequest, ...],
    ) -> tuple[XICTrace, ...]:
        self.raw_chromatogram_call_count += 1
        type(self).logical_xic_count += len(requests)
        type(self).vendor_call_count += 1
        return tuple(
            XICTrace.from_arrays(*_trace_arrays(request.rt_min, request.rt_max))
            for request in requests
        )


def _fake_package_open_raw(raw_path: Path, _dll_dir: Path, **_kwargs: Any):
    return _FakePackageRaw(raw_path)


def _legacy_recomputation_forbidden(*_args: Any, **_kwargs: Any):
    raise AssertionError("Owner Backfill legacy recomputation alias was used")


def _spawn_typed_worker(
    job: OwnerBuildSampleJob | OwnerBackfillSampleJob,
) -> OwnerBuildSampleResult | OwnerBackfillSampleResult:
    """Importable dispatcher executed only inside the real spawn child."""

    import xic_extractor.alignment.owner_backfill as owner_backfill_module
    import xic_extractor.raw_reader as raw_reader_module

    if multiprocessing.get_start_method() != "spawn":
        raise AssertionError("typed worker did not cross the spawn boundary")
    if os.getpid() == os.getppid():
        raise AssertionError("typed worker did not enter a child process")
    raw_reader_module.open_raw = _fake_package_open_raw
    owner_backfill_module.find_peak_and_area = _legacy_recomputation_forbidden
    owner_backfill_module.integration_from_peak = _legacy_recomputation_forbidden
    marker = job.raw_path.with_name(
        f"{job.raw_path.name}.{type(job).__name__}.{os.getpid()}.spawn"
    )
    marker.write_text("spawn\n", encoding="utf-8")

    if isinstance(job, OwnerBuildSampleJob):
        real_worker = process_backend_module.extract_owner_build_sample_job
        if real_worker is _spawn_typed_worker:
            raise AssertionError("spawn child inherited the parent worker patch")
        result = real_worker(job)
        if not isinstance(result, OwnerBuildSampleResult):
            raise AssertionError(f"unexpected OwnerBuild result: {result!r}")
        return result
    if isinstance(job, OwnerBackfillSampleJob):
        real_worker = process_backend_module.extract_owner_backfill_sample_job
        if real_worker is _spawn_typed_worker:
            raise AssertionError("spawn child inherited the parent worker patch")
        result = real_worker(job)
        if not isinstance(result, OwnerBackfillSampleResult):
            raise AssertionError(f"unexpected OwnerBackfill result: {result!r}")
        return result
    raise AssertionError(f"unexpected process job type: {type(job)!r}")


class _InlineTypedExecutor:
    jobs: list[OwnerBuildSampleJob | OwnerBackfillSampleJob] = []
    job_types: list[type[Any]] = []
    result_types: list[type[Any]] = []
    payload_sizes: list[int] = []
    results: list[OwnerBuildSampleResult | OwnerBackfillSampleResult] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs

    def submit(self, worker, job):
        if not isinstance(job, (OwnerBuildSampleJob, OwnerBackfillSampleJob)):
            raise AssertionError(f"unexpected process job type: {type(job)!r}")
        payload = pickle.dumps(job)
        assert b"XICTrace" not in payload
        assert b"numpy.ndarray" not in payload
        assert len(payload) < 100_000
        roundtripped_job = pickle.loads(payload)
        type(self).jobs.append(roundtripped_job)
        type(self).job_types.append(type(job))
        type(self).payload_sizes.append(len(payload))

        future: Future[Any] = Future()
        try:
            result = worker(roundtripped_job)
            if not isinstance(
                result,
                (OwnerBuildSampleResult, OwnerBackfillSampleResult),
            ):
                raise AssertionError(
                    f"unexpected process result type: {type(result)!r}"
                )
            result = pickle.loads(pickle.dumps(result))
            type(self).result_types.append(type(result))
            type(self).results.append(result)
            future.set_result(result)
        except BaseException as exc:
            future.set_exception(exc)
        return future

    def shutdown(self, *, wait: bool, cancel_futures: bool = False) -> None:
        del wait, cancel_futures


@pytest.fixture
def _highest_entry_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    raw_files: dict[str, Path] = {}
    for sample_stem in _SAMPLE_ORDER:
        raw_path = raw_dir / f"{sample_stem}.raw"
        raw_path.write_bytes(sample_stem.encode("utf-8"))
        raw_files[sample_stem] = raw_path
    batch_index = write_owner_closed_partial_batch(
        tmp_path,
        sample_order=_SAMPLE_ORDER,
        candidate_samples=("Sample_A", "Sample_B", "Sample_C"),
        raw_files=raw_files,
    )
    dll_dir = tmp_path / "dll"
    dll_dir.mkdir()

    monkeypatch.setattr(
        "xic_extractor.raw_reader.open_raw",
        _fake_package_open_raw,
    )
    monkeypatch.setattr(
        "xic_extractor.alignment.owner_backfill.find_peak_and_area",
        _legacy_recomputation_forbidden,
    )
    monkeypatch.setattr(
        "xic_extractor.alignment.owner_backfill.integration_from_peak",
        _legacy_recomputation_forbidden,
    )
    return batch_index, raw_dir, dll_dir


def test_highest_entry_signal_profile_serial_process_oracle(
    _highest_entry_fixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_index, raw_dir, dll_dir = _highest_entry_fixture
    auto_15 = _run_cli(
        batch_index,
        raw_dir,
        dll_dir,
        tmp_path / "auto-15",
        window_points=15,
        raw_workers=1,
        audit_mode="auto",
    )
    diagnostic_21 = _run_cli(
        batch_index,
        raw_dir,
        dll_dir,
        tmp_path / "diagnostic-21",
        window_points=21,
        raw_workers=1,
        audit_mode="auto",
    )
    serial_15 = _run_cli(
        batch_index,
        raw_dir,
        dll_dir,
        tmp_path / "serial-15-full",
        window_points=15,
        raw_workers=1,
        audit_mode="full",
    )

    _InlineTypedExecutor.jobs.clear()
    _InlineTypedExecutor.job_types.clear()
    _InlineTypedExecutor.result_types.clear()
    _InlineTypedExecutor.payload_sizes.clear()
    _InlineTypedExecutor.results.clear()

    monkeypatch.setattr(
        "xic_extractor.process_job_execution.ProcessPoolExecutor",
        _InlineTypedExecutor,
    )
    process_15 = _run_cli(
        batch_index,
        raw_dir,
        dll_dir,
        tmp_path / "process-15-full",
        window_points=15,
        raw_workers=2,
        audit_mode="full",
    )

    assert set(_InlineTypedExecutor.job_types) == {
        OwnerBuildSampleJob,
        OwnerBackfillSampleJob,
    }
    assert set(_InlineTypedExecutor.result_types) == {
        OwnerBuildSampleResult,
        OwnerBackfillSampleResult,
    }
    assert _InlineTypedExecutor.payload_sizes

    profile_15 = auto_15.profile_rows[0]
    profile_21 = diagnostic_21.profile_rows[0]
    assert profile_15.row_kind == profile_21.row_kind == "run_profile"
    assert profile_15.signal_processing_method == "gaussian"
    assert profile_21.signal_processing_method == "gaussian"
    assert profile_15.signal_processing_window_points == 15
    assert profile_21.signal_processing_window_points == 21
    assert profile_15.profile_id != profile_21.profile_id
    assert profile_15.profile_hash != profile_21.profile_hash

    physical_15 = _available_physical_row(auto_15)
    physical_21 = _available_physical_row(diagnostic_21)
    assert physical_15.fact_content_id != physical_21.fact_content_id
    assert physical_15.physical_answer_id != physical_21.physical_answer_id
    assert physical_15.area_counts_seconds != physical_21.area_counts_seconds
    assert _stable_physical_identity(physical_15) == _stable_physical_identity(
        physical_21
    )
    assert auto_15.acquisition_counts == diagnostic_21.acquisition_counts
    assert _work_counts(auto_15) == _work_counts(diagnostic_21)
    for name in (
        "alignment_matrix_status.tsv",
        "event_to_ms1_owner.tsv",
        "ambiguous_ms1_owners.tsv",
        "owner_edge_evidence.tsv",
    ):
        assert (auto_15.output_dir / name).read_bytes() == (
            diagnostic_21.output_dir / name
        ).read_bytes()
    assert _matrix_identity_rows(auto_15) == _matrix_identity_rows(diagnostic_21)
    assert _cell_identity_rows(auto_15) == _cell_identity_rows(diagnostic_21)
    cell_15 = _selected_cell_projection(auto_15)
    cell_21 = _selected_cell_projection(diagnostic_21)
    assert cell_15.profile_id != cell_21.profile_id
    assert cell_15.cell_projection_id != cell_21.cell_projection_id
    assert cell_15.matrix_value_counts_seconds != cell_21.matrix_value_counts_seconds
    assert cell_15.cell_status == cell_21.cell_status

    assert _authority_rows(auto_15) == _authority_rows(serial_15)
    for name in _DETERMINISTIC_ARTIFACTS:
        assert (auto_15.output_dir / name).read_bytes() == (
            serial_15.output_dir / name
        ).read_bytes()
    _assert_workbook_semantic_parity(
        auto_15.output_dir / "alignment_results.xlsx",
        serial_15.output_dir / "alignment_results.xlsx",
        allowed_value_pairs=frozenset(
            {
                ("auto", "full"),
                ("full_audit_legacy_audit_output", "explicit_full"),
            }
        ),
    )
    for name in _DETERMINISTIC_ARTIFACTS:
        assert (serial_15.output_dir / name).read_bytes() == (
            process_15.output_dir / name
        ).read_bytes()
    _assert_workbook_semantic_parity(
        serial_15.output_dir / "alignment_results.xlsx",
        process_15.output_dir / "alignment_results.xlsx",
    )
    process_matrix, process_config = _rebuild_process_matrix(
        tuple(_InlineTypedExecutor.jobs),
        tuple(_InlineTypedExecutor.results),
    )
    _assert_process_compact_projection(
        process_15,
        matrix=process_matrix,
        alignment_config=process_config,
    )


def test_audit_projection_field_classes_are_closed_and_adversarial(
    _highest_entry_fixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_index, raw_dir, dll_dir = _highest_entry_fixture
    monkeypatch.setattr(
        "xic_extractor.process_job_execution.ProcessPoolExecutor",
        _InlineTypedExecutor,
    )

    _clear_inline_executor_captures()
    _run_cli(
        batch_index,
        raw_dir,
        dll_dir,
        tmp_path / "audit-field-none",
        window_points=15,
        raw_workers=2,
        audit_mode="none",
    )
    none_jobs = tuple(_InlineTypedExecutor.jobs)
    none_results = tuple(_InlineTypedExecutor.results)

    _clear_inline_executor_captures()
    _run_cli(
        batch_index,
        raw_dir,
        dll_dir,
        tmp_path / "audit-field-full",
        window_points=15,
        raw_workers=2,
        audit_mode="full",
    )
    full_jobs = tuple(_InlineTypedExecutor.jobs)
    full_results = tuple(_InlineTypedExecutor.results)

    _assert_audit_field_class_matrix(
        tmp_path / "audit-field-mutations",
        none_jobs=none_jobs,
        none_results=none_results,
        full_jobs=full_jobs,
        full_results=full_results,
    )


def test_production_equivalent_metadata_audit_none_full_pair(
    _highest_entry_fixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Package HOLD/pre-RAW behavior has separate guard coverage; this audit-mode
    # oracle is intentionally scoped below the product-authority seam.
    monkeypatch.setattr(
        pipeline_module,
        "require_product_quantitative_projection_authority",
        lambda **_kwargs: None,
    )
    batch_index, raw_dir, dll_dir = _highest_entry_fixture
    audit_none = _run_cli(
        batch_index,
        raw_dir,
        dll_dir,
        tmp_path / "production-equivalent-none",
        window_points=15,
        raw_workers=1,
        audit_mode="none",
        backfill_scope="production-equivalent",
    )
    audit_full = _run_cli(
        batch_index,
        raw_dir,
        dll_dir,
        tmp_path / "production-equivalent-full",
        window_points=15,
        raw_workers=1,
        audit_mode="full",
        backfill_scope="production-equivalent",
    )

    exact_names = set(_expected_artifact_names("validation", "production-equivalent"))
    exact_names.difference_update(
        {
            "alignment_results.xlsx",
            "alignment_cells.tsv",
            "alignment_run_metadata.json",
        }
    )
    _assert_exact_artifacts(audit_none, audit_full, exact_names)
    _assert_tsv_diff_whitelist(
        audit_none.output_dir / "alignment_cells.tsv",
        audit_full.output_dir / "alignment_cells.tsv",
        allowed_columns=_ALIGNMENT_CELLS_AUDIT_WHITELIST,
    )
    _assert_audit_workbook_whitelist(
        audit_none.output_dir / "alignment_results.xlsx",
        audit_full.output_dir / "alignment_results.xlsx",
    )
    _assert_metadata_audit_whitelist(audit_none, audit_full)
    assert _authority_rows(audit_none) == _authority_rows(audit_full)
    assert audit_none.profile_rows == audit_full.profile_rows
    assert audit_none.acquisition_counts == audit_full.acquisition_counts
    assert _work_counts(audit_none) == _work_counts(audit_full)


def test_validation_minimal_audit_none_full_pair(
    _highest_entry_fixture,
    tmp_path: Path,
) -> None:
    batch_index, raw_dir, dll_dir = _highest_entry_fixture
    audit_none = _run_cli(
        batch_index,
        raw_dir,
        dll_dir,
        tmp_path / "validation-minimal-none",
        window_points=15,
        raw_workers=1,
        audit_mode="none",
        output_level="validation-minimal",
    )
    audit_full = _run_cli(
        batch_index,
        raw_dir,
        dll_dir,
        tmp_path / "validation-minimal-full",
        window_points=15,
        raw_workers=1,
        audit_mode="full",
        output_level="validation-minimal",
    )

    exact_names = set(_expected_artifact_names("validation-minimal", "full-audit"))
    exact_names.remove("alignment_backfill_cell_evidence.tsv")
    _assert_exact_artifacts(audit_none, audit_full, exact_names)
    _assert_tsv_diff_whitelist(
        audit_none.output_dir / "alignment_backfill_cell_evidence.tsv",
        audit_full.output_dir / "alignment_backfill_cell_evidence.tsv",
        allowed_columns=frozenset({"region_shadow_verdict"}),
    )
    assert audit_none.profile_rows == audit_full.profile_rows
    assert audit_none.metadata is audit_full.metadata is None
    assert audit_none.acquisition_counts == audit_full.acquisition_counts
    assert _work_counts(audit_none) == _work_counts(audit_full)


def test_real_windows_spawn_roundtrips_typed_owner_jobs_and_results(
    _highest_entry_fixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_index, raw_dir, dll_dir = _highest_entry_fixture
    parent_pid = os.getpid()
    captured_jobs: list[tuple[object, int]] = []
    captured_build_results: list[OwnerBuildSampleResult] = []
    captured_backfill_results: list[OwnerBackfillSampleResult] = []
    build_outputs: list[OwnerBuildProcessOutput] = []
    backfill_outputs: list[
        backfill_transaction_module.OwnerBackfillTransactionOutput
    ] = []
    real_build_collector = process_backend_module.collect_owner_build_results
    real_job_validator = process_execution_module._validate_process_job_payloads
    real_backfill_delivery = (
        backfill_transaction_module.OwnerBackfillSampleTransaction.deliver
    )
    real_backfill_finish = (
        backfill_transaction_module.OwnerBackfillSampleTransaction.finish
    )

    def capture_job(job: object) -> None:
        size = process_execution_module.process_job_payload_size_bytes(job)
        captured_jobs.append((job, size))

    def capture_jobs(jobs) -> None:
        real_job_validator(jobs)
        for job in jobs:
            capture_job(job)

    def capture_build_collector(results):
        materialized = tuple(results)
        captured_build_results.extend(materialized)
        output = real_build_collector(materialized)
        build_outputs.append(output)
        return output

    def capture_backfill_delivery(self, ordinal, result):
        captured_backfill_results.append(result)
        return real_backfill_delivery(self, ordinal, result)

    def capture_backfill_finish(self):
        output = real_backfill_finish(self)
        backfill_outputs.append(output)
        return output

    monkeypatch.setattr(
        process_backend_module,
        "extract_owner_build_sample_job",
        _spawn_typed_worker,
    )
    monkeypatch.setattr(
        process_backend_module,
        "extract_owner_backfill_sample_job",
        _spawn_typed_worker,
    )
    monkeypatch.setattr(
        process_execution_module,
        "_validate_process_job_payloads",
        capture_jobs,
    )
    monkeypatch.setattr(
        process_backend_module,
        "collect_owner_build_results",
        capture_build_collector,
    )
    monkeypatch.setattr(
        backfill_transaction_module.OwnerBackfillSampleTransaction,
        "deliver",
        capture_backfill_delivery,
    )
    monkeypatch.setattr(
        backfill_transaction_module.OwnerBackfillSampleTransaction,
        "finish",
        capture_backfill_finish,
    )

    run = _run_cli(
        batch_index,
        raw_dir,
        dll_dir,
        tmp_path / "real-spawn",
        window_points=15,
        raw_workers=2,
        audit_mode="full",
        output_level="validation-minimal",
    )

    assert {type(job) for job, _size in captured_jobs} == {
        OwnerBuildSampleJob,
        OwnerBackfillSampleJob,
    }
    assert all(size < 100_000 for _job, size in captured_jobs)
    assert all(not _contains_trace_payload(job) for job, _size in captured_jobs)
    profile = resolve_signal_processing_profile(signal_processing_window_points=15)
    backfill_jobs = [
        job for job, _size in captured_jobs if isinstance(job, OwnerBackfillSampleJob)
    ]
    assert backfill_jobs
    assert all(job.signal_processing_profile == profile for job in backfill_jobs)

    assert captured_build_results
    assert captured_backfill_results
    assert all(
        len(pickle.dumps(result)) < 250_000
        for result in (*captured_build_results, *captured_backfill_results)
    )
    assert all(
        not _contains_trace_payload(result)
        for result in (*captured_build_results, *captured_backfill_results)
    )
    prepared_peak_facts = tuple(
        prepared
        for result in captured_backfill_results
        for prepared in result.prepared_peak_facts
    )
    assert all(
        not result.peak_fact_observations for result in captured_backfill_results
    )
    assert any(
        prepared.physical_row.state == "available"
        and prepared.physical_row.profile_id == profile.profile_id
        for prepared in prepared_peak_facts
    )
    assert build_outputs and build_outputs[0].ownership.owners
    assert backfill_outputs and backfill_outputs[0].prepared_peak_facts
    assert run.profile_rows[0].profile_id == profile.profile_id

    markers = tuple(raw_dir.glob("*.spawn"))
    assert markers
    marker_job_types = {path.name.split(".")[2] for path in markers}
    marker_pids = {int(path.name.split(".")[3]) for path in markers}
    assert marker_job_types == {"OwnerBuildSampleJob", "OwnerBackfillSampleJob"}
    assert parent_pid not in marker_pids
    assert all(path.read_text(encoding="utf-8") == "spawn\n" for path in markers)


@pytest.mark.parametrize("output_level", ("production", "machine"))
def test_unapproved_point21_rejects_product_output_before_raw(
    _highest_entry_fixture,
    tmp_path: Path,
    output_level: str,
) -> None:
    batch_index, raw_dir, dll_dir = _highest_entry_fixture
    counts_before = (
        _FakePackageRaw.open_count,
        _FakePackageRaw.logical_xic_count,
        _FakePackageRaw.vendor_call_count,
    )
    output_dir = tmp_path / f"rejected-{output_level}"

    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--output-dir",
            str(output_dir),
            "--output-level",
            output_level,
            "--signal-processing-window-points",
            "21",
        ]
    )

    assert code == 2
    assert not output_dir.exists()
    assert counts_before == (
        _FakePackageRaw.open_count,
        _FakePackageRaw.logical_xic_count,
        _FakePackageRaw.vendor_call_count,
    )


def _run_cli(
    batch_index: Path,
    raw_dir: Path,
    dll_dir: Path,
    output_dir: Path,
    *,
    window_points: int,
    raw_workers: int,
    audit_mode: str,
    output_level: str = "validation",
    backfill_scope: str = "full-audit",
) -> _Run:
    counts_before = (
        _FakePackageRaw.open_count,
        _FakePackageRaw.logical_xic_count,
        _FakePackageRaw.vendor_call_count,
    )
    code = run_alignment.main(
        [
            "--discovery-batch-index",
            str(batch_index),
            "--raw-dir",
            str(raw_dir),
            "--dll-dir",
            str(dll_dir),
            "--output-dir",
            str(output_dir),
            "--output-level",
            output_level,
            "--raw-workers",
            str(raw_workers),
            "--backfill-scope",
            backfill_scope,
            "--audit-evidence-mode",
            audit_mode,
            "--signal-processing-method",
            "gaussian",
            "--signal-processing-window-points",
            str(window_points),
        ]
    )
    assert code == 0
    assert sorted(path.name for path in output_dir.iterdir()) == sorted(
        _expected_artifact_names(output_level, backfill_scope)
    )
    read_result = read_owner_backfill_peak_fact_provenance_tsv(
        output_dir / "alignment_owner_backfill_peak_fact_provenance.tsv"
    )
    assert read_result.projection_integrity == "verified"
    assert read_result.stage_claim == "unavailable_without_live_owner"
    assert read_result.physical_claim == "unavailable_without_live_owner"
    assert read_result.cell_projection_claim == "unavailable_without_live_owner"
    cells_path = output_dir / "alignment_cells.tsv"
    if cells_path.exists():
        with cells_path.open(encoding="utf-8", newline="") as handle:
            cell_rows = tuple(csv.DictReader(handle, delimiter="\t"))
    else:
        cell_rows = ()
    metadata_path = output_dir / "alignment_run_metadata.json"
    metadata = (
        json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata_path.exists()
        else None
    )
    counts_after = (
        _FakePackageRaw.open_count,
        _FakePackageRaw.logical_xic_count,
        _FakePackageRaw.vendor_call_count,
    )
    return _Run(
        output_dir=output_dir,
        profile_rows=read_result.rows,
        cell_rows=cell_rows,
        acquisition_counts=tuple(
            after - before for before, after in zip(counts_before, counts_after)
        ),
        metadata=metadata,
    )


def _expected_artifact_names(output_level: str, backfill_scope: str) -> tuple[str, ...]:
    names = list(artifact_names_for_output_level(output_level))
    if backfill_scope != "full-audit":
        names.extend(("skipped_evidence_ledger.tsv", "alignment_run_metadata.json"))
    return tuple(names)


def _trace_arrays(rt_min: float, rt_max: float) -> tuple[np.ndarray, np.ndarray]:
    rt = np.linspace(rt_min, rt_max, 121, dtype=np.float64)
    peak = 1_000.0 * np.exp(-0.5 * ((rt - 8.5) / 0.09) ** 2)
    shoulder = 180.0 * np.exp(-0.5 * ((rt - 8.63) / 0.045) ** 2)
    baseline = 8.0 + 0.4 * (rt - rt_min)
    return rt, baseline + peak + shoulder


def _available_physical_row(run: _Run):
    rows = [
        row
        for row in run.profile_rows
        if row.row_kind == "physical_question" and row.state == "available"
    ]
    assert rows, {
        "physical": [
            (row.state, row.reason, row.trace_point_count, row.admissible_segment_count)
            for row in run.profile_rows
            if row.row_kind == "physical_question"
        ],
        "cells": [
            (
                row.get("sample_stem"),
                row.get("status"),
                row.get("trace_quality"),
                row.get("reason"),
            )
            for row in run.cell_rows
        ],
    }
    return rows[0]


def _stable_physical_identity(row: Any) -> tuple[Any, ...]:
    return (
        row.work_unit_key,
        row.group_hypothesis_id,
        row.public_family_id,
        row.sample_stem,
        row.candidate_index,
        row.candidate_phase,
        row.request_identity_id,
        row.acquisition_source_id,
        row.selection_subject_id,
        row.query_rt_min,
    )


def _authority_rows(run: _Run) -> list[dict[str, str]]:
    path = run.output_dir / "alignment_cells.tsv"
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    fields = (
        "feature_family_id",
        "group_hypothesis_id",
        "public_family_id",
        "sample_stem",
        "status",
        "area",
        "matrix_area",
        "matrix_value_source",
        "selected_integration_content_id",
        "selected_integration_authority_content_id",
    )
    return [{field: row.get(field, "") for field in fields} for row in rows]


def _cell_identity_rows(run: _Run) -> list[tuple[str, ...]]:
    fields = (
        "feature_family_id",
        "group_hypothesis_id",
        "public_family_id",
        "sample_stem",
        "status",
        "source_candidate_id",
        "gap_fill_state",
        "missing_observation_state",
        "peak_hypothesis_status",
        "group_claim_state",
        "consolidation_state",
    )
    return [tuple(row.get(field, "") for field in fields) for row in run.cell_rows]


def _matrix_identity_rows(run: _Run) -> list[tuple[str, ...]]:
    fields = (
        "matrix_row_index",
        "peak_hypothesis_id",
        "row_identity_basis",
        "split_evaluation_status",
        "projection_status",
        "source_feature_family_ids",
        "source_feature_family_count",
        "evidence_status",
        "parent_peak_hypothesis_id",
        "child_peak_hypothesis_ids",
    )
    with (run.output_dir / "alignment_matrix_identity.tsv").open(
        encoding="utf-8", newline=""
    ) as handle:
        rows = csv.DictReader(handle, delimiter="\t")
        return [tuple(row.get(field, "") for field in fields) for row in rows]


def _selected_cell_projection(run: _Run):
    rows = [row for row in run.profile_rows if row.row_kind == "cell_projection"]
    assert len(rows) == 1
    return rows[0]


def _work_counts(run: _Run) -> tuple[int, int, int]:
    return tuple(
        sum(row.row_kind == kind for row in run.profile_rows)
        for kind in ("run_profile", "physical_question", "cell_projection")
    )


def _clear_inline_executor_captures() -> None:
    _InlineTypedExecutor.jobs.clear()
    _InlineTypedExecutor.job_types.clear()
    _InlineTypedExecutor.result_types.clear()
    _InlineTypedExecutor.payload_sizes.clear()
    _InlineTypedExecutor.results.clear()


def _rebuild_process_matrix(
    jobs: tuple[OwnerBuildSampleJob | OwnerBackfillSampleJob, ...],
    results: tuple[OwnerBuildSampleResult | OwnerBackfillSampleResult, ...],
) -> tuple[AlignmentMatrix, Any]:
    backfill_jobs = tuple(
        job for job in jobs if isinstance(job, OwnerBackfillSampleJob)
    )
    backfill_results = tuple(
        result for result in results if isinstance(result, OwnerBackfillSampleResult)
    )
    assert backfill_jobs
    assert backfill_results
    alignment_config = backfill_jobs[0].alignment_config
    assert all(job.alignment_config == alignment_config for job in backfill_jobs)
    features_by_id = {
        feature.feature_family_id: feature
        for job in backfill_jobs
        for feature in job.features
    }
    feature_order = tuple(features_by_id)
    process_output = process_backend_module.collect_owner_backfill_results(
        backfill_results,
        feature_order=feature_order,
        sample_order=_SAMPLE_ORDER,
    )
    matrix = build_owner_alignment_matrix(
        tuple(features_by_id.values()),
        sample_order=_SAMPLE_ORDER,
        ambiguous_by_sample={},
        rescued_cells=process_output.cells,
    )
    matrix = apply_ms1_peak_claim_registry(matrix, alignment_config)
    matrix = consolidate_primary_family_rows(matrix, alignment_config)
    matrix = recenter_pre_backfill_identity_families(matrix)
    return matrix, alignment_config


def _assert_audit_field_class_matrix(
    output_root: Path,
    *,
    none_jobs: tuple[OwnerBuildSampleJob | OwnerBackfillSampleJob, ...],
    none_results: tuple[OwnerBuildSampleResult | OwnerBackfillSampleResult, ...],
    full_jobs: tuple[OwnerBuildSampleJob | OwnerBackfillSampleJob, ...],
    full_results: tuple[OwnerBuildSampleResult | OwnerBackfillSampleResult, ...],
) -> None:
    none_matrix, none_config = _rebuild_process_matrix(none_jobs, none_results)
    full_matrix, full_config = _rebuild_process_matrix(full_jobs, full_results)
    assert none_config == full_config
    _assert_live_audit_mode_semantic_parity(none_matrix, full_matrix)

    audited_indexes = tuple(
        index
        for index, cell in enumerate(full_matrix.cells)
        if cell.integration_audit is not None
        and not cell.integration_audit.is_empty
        and isinstance(cell.selected_integration, IntegrationResult)
    )
    assert audited_indexes
    audited_index = audited_indexes[0]
    audited_cell = full_matrix.cells[audited_index]
    assert audited_cell.integration_audit is not None
    for field_name in _AUDIT_MODE_HARD_ZERO_SUMMARY_FIELDS:
        mutated_cell = replace(
            audited_cell,
            integration_audit=_mutate_dataclass_field(
                audited_cell.integration_audit,
                field_name,
            ),
        )
        with pytest.raises(AssertionError, match=field_name):
            _assert_live_audit_mode_semantic_parity(
                none_matrix,
                _matrix_with_cell(full_matrix, audited_index, mutated_cell),
            )
    for field_name in _EXCLUDED_SELECTED_INTEGRATION_AUDIT_FIELDS:
        assert isinstance(audited_cell.selected_integration, IntegrationResult)
        mutated_cell = replace(
            audited_cell,
            selected_integration=_mutate_dataclass_field(
                audited_cell.selected_integration,
                field_name,
            ),
        )
        with pytest.raises(AssertionError, match=field_name):
            _assert_live_audit_mode_semantic_parity(
                none_matrix,
                _matrix_with_cell(full_matrix, audited_index, mutated_cell),
            )

    prepared_peak_fact = next(
        item
        for result in full_results
        if isinstance(result, OwnerBackfillSampleResult)
        for item in result.prepared_peak_facts
        if item.selected_integration_authority_content_id is not None
        and item.physical_row.state == "available"
    )
    matching_indexes = tuple(
        index
        for index, cell in enumerate(full_matrix.cells)
        if (cell.cluster_id, cell.sample_stem)
        == (
            prepared_peak_fact.feature_family_id,
            prepared_peak_fact.stage.sample_stem,
        )
    )
    assert len(matching_indexes) == 1
    target_index = matching_indexes[0]
    target_cell = full_matrix.cells[target_index]
    integration = target_cell.selected_integration
    assert isinstance(integration, IntegrationResult)
    base_audit = CellIntegrationAuditSummary(
        raw_area=integration.area_raw_counts_seconds,
        area_baseline_corrected=integration.area_baseline_corrected,
        area_uncertainty=4.0,
        area_uncertainty_formula_version="baseline_residual_mad_v1",
        baseline_residual_mad=0.5,
        area_uncertainty_noise_source="asls_residual",
        baseline_type=integration.baseline_type or "asls",
        baseline_score=0.8,
        uncertainty_fraction=0.04,
        baseline_fraction=0.9,
        integration_scan_count=7,
        area_baseline_corrected_asls=integration.area_baseline_corrected,
        baseline_score_asls=0.7,
    )
    base_cell = replace(target_cell, integration_audit=base_audit)
    base_matrix = _matrix_with_cell(full_matrix, target_index, base_cell)

    summary_field_names = frozenset(
        field.name for field in fields(CellIntegrationAuditSummary)
    )
    summary_classes = (
        frozenset(_MUTABLE_PERSISTED_AUDIT_FIELDS),
        frozenset(_AUDIT_MODE_HARD_ZERO_SUMMARY_FIELDS),
        frozenset(_UNPERSISTED_ASLS_SUMMARY_FIELDS),
    )
    assert all(
        not left.intersection(right)
        for index, left in enumerate(summary_classes)
        for right in summary_classes[index + 1 :]
    )
    assert frozenset().union(*summary_classes) == summary_field_names
    assert (
        tuple(
            field.name
            for field in fields(IntegrationResult)
            if field.name not in SELECTED_INTEGRATION_AUTHORITY_FIELDS
        )
        == _EXCLUDED_SELECTED_INTEGRATION_AUDIT_FIELDS
    )

    stage = prepared_peak_fact.stage
    base_authority_ids = _audit_authority_ids(
        base_matrix,
        target_index=target_index,
        prepared_peak_fact=prepared_peak_fact,
        stage=stage,
        alignment_config=full_config,
    )
    base_decisions = build_live_production_decisions(base_matrix, full_config)
    base_directory = output_root / "base"
    _render_audit_artifact_projection(
        base_directory,
        base_matrix,
        alignment_config=full_config,
        production_decisions=base_decisions,
    )
    base_snapshot = _audit_artifact_snapshot(base_directory)

    for index, field_name in enumerate(_MUTABLE_PERSISTED_AUDIT_FIELDS):
        mutated_audit = _mutate_dataclass_field(base_audit, field_name)
        mutated_cell = replace(base_cell, integration_audit=mutated_audit)
        mutated_matrix = _matrix_with_cell(full_matrix, target_index, mutated_cell)
        _assert_audit_mode_semantic_parity(base_cell, mutated_cell)
        assert (
            _audit_authority_ids(
                mutated_matrix,
                target_index=target_index,
                prepared_peak_fact=prepared_peak_fact,
                stage=stage,
                alignment_config=full_config,
            )
            == base_authority_ids
        ), field_name
        mutated_decisions = build_live_production_decisions(
            mutated_matrix,
            full_config,
        )
        directory = output_root / f"persisted-{index:02d}-{field_name}"
        _render_audit_artifact_projection(
            directory,
            mutated_matrix,
            alignment_config=full_config,
            production_decisions=mutated_decisions,
        )
        snapshot = _audit_artifact_snapshot(directory)
        for artifact_name in (
            "alignment_cells.tsv",
            "alignment_results.xlsx",
            "alignment_run_metadata.json",
            "alignment_owner_backfill_peak_fact_provenance.tsv",
            "alignment_backfill_cell_evidence.tsv",
        ):
            assert snapshot[artifact_name] == base_snapshot[artifact_name], (
                field_name,
                artifact_name,
            )
        assert _changed_tsv_columns(
            base_directory / "alignment_cell_integration_audit.tsv",
            directory / "alignment_cell_integration_audit.tsv",
        ) == {field_name}

    for field_name in _AUDIT_MODE_HARD_ZERO_SUMMARY_FIELDS:
        mutated_cell = replace(
            base_cell,
            integration_audit=_mutate_dataclass_field(base_audit, field_name),
        )
        mutated_matrix = _matrix_with_cell(full_matrix, target_index, mutated_cell)
        assert (
            _audit_authority_ids(
                mutated_matrix,
                target_index=target_index,
                prepared_peak_fact=prepared_peak_fact,
                stage=stage,
                alignment_config=full_config,
            )
            == base_authority_ids
        ), field_name

    for field_name in _EXCLUDED_SELECTED_INTEGRATION_AUDIT_FIELDS:
        mutated_integration = _mutate_dataclass_field(integration, field_name)
        mutated_cell = replace(base_cell, selected_integration=mutated_integration)
        mutated_matrix = _matrix_with_cell(full_matrix, target_index, mutated_cell)
        assert (
            _audit_authority_ids(
                mutated_matrix,
                target_index=target_index,
                prepared_peak_fact=prepared_peak_fact,
                stage=stage,
                alignment_config=full_config,
            )
            == base_authority_ids
        ), field_name

    for index, field_name in enumerate(_UNPERSISTED_ASLS_SUMMARY_FIELDS):
        mutated_cell = replace(
            base_cell,
            integration_audit=_mutate_dataclass_field(base_audit, field_name),
        )
        mutated_matrix = _matrix_with_cell(full_matrix, target_index, mutated_cell)
        _assert_audit_mode_semantic_parity(base_cell, mutated_cell)
        assert (
            _audit_authority_ids(
                mutated_matrix,
                target_index=target_index,
                prepared_peak_fact=prepared_peak_fact,
                stage=stage,
                alignment_config=full_config,
            )
            == base_authority_ids
        ), field_name
        mutated_decisions = build_live_production_decisions(
            mutated_matrix,
            full_config,
        )
        directory = output_root / f"unpersisted-{index:02d}-{field_name}"
        _render_audit_artifact_projection(
            directory,
            mutated_matrix,
            alignment_config=full_config,
            production_decisions=mutated_decisions,
        )
        assert _audit_artifact_snapshot(directory) == base_snapshot, field_name


def _audit_authority_ids(
    matrix: AlignmentMatrix,
    *,
    target_index: int,
    prepared_peak_fact: Any,
    stage: Any,
    alignment_config: Any,
) -> tuple[str, str, str, str]:
    cell = matrix.cells[target_index]
    physical_row = prepared_peak_fact.physical_row
    integration = cell.selected_integration
    assert isinstance(integration, IntegrationResult)
    selected_integration = selected_integration_authority_projection(integration)
    authority_cell = owner_backfill_authority_cell_projection(
        stage=stage,
        cell=cell,
        profile_id=physical_row.profile_id,
        selected_fact_content_id=physical_row.fact_content_id,
        physical_answer_id=physical_row.physical_answer_id,
        physical_segment_id=physical_row.physical_segment_id,
    )
    production_decisions = build_live_production_decisions(
        matrix,
        alignment_config,
    )
    production_decision = production_cell_decision_projection(
        production_decisions.cell(cell.cluster_id, cell.sample_stem),
        cell=cell,
        config=alignment_config,
        owner_backfill_cell_content_id=authority_cell.owner_backfill_cell_content_id,
    )
    final_cell = owner_backfill_cell_projection(
        authority_cell=authority_cell,
        production_decision=production_decision,
    )
    return (
        selected_integration.selected_integration_authority_content_id,
        authority_cell.owner_backfill_cell_content_id,
        production_decision.production_cell_decision_id,
        final_cell.cell_projection_id,
    )


def _assert_audit_mode_semantic_parity(
    left: AlignedCell,
    right: AlignedCell,
) -> None:
    assert left.integration_audit is not None
    assert right.integration_audit is not None
    for field_name in _AUDIT_MODE_HARD_ZERO_SUMMARY_FIELDS:
        assert getattr(left.integration_audit, field_name) == getattr(
            right.integration_audit,
            field_name,
        ), field_name
    assert isinstance(left.selected_integration, IntegrationResult)
    assert isinstance(right.selected_integration, IntegrationResult)
    for field_name in _EXCLUDED_SELECTED_INTEGRATION_AUDIT_FIELDS:
        assert getattr(left.selected_integration, field_name) == getattr(
            right.selected_integration,
            field_name,
        ), field_name


def _assert_live_audit_mode_semantic_parity(
    audit_none: AlignmentMatrix,
    audit_full: AlignmentMatrix,
) -> None:
    none_by_key = {
        (cell.cluster_id, cell.sample_stem): cell for cell in audit_none.cells
    }
    full_by_key = {
        (cell.cluster_id, cell.sample_stem): cell for cell in audit_full.cells
    }
    assert none_by_key.keys() == full_by_key.keys()
    audited_cell_count = 0
    summary_sources = {
        "raw_area": "area_raw_counts_seconds",
        "area_baseline_corrected": "area_baseline_corrected",
        "baseline_type": "baseline_type",
    }
    assert tuple(summary_sources) == _AUDIT_MODE_HARD_ZERO_SUMMARY_FIELDS
    for key in none_by_key:
        none_cell = none_by_key[key]
        full_cell = full_by_key[key]
        assert none_cell.integration_audit is None
        none_integration = none_cell.selected_integration
        full_integration = full_cell.selected_integration
        assert isinstance(none_integration, IntegrationResult) == isinstance(
            full_integration,
            IntegrationResult,
        )
        if isinstance(none_integration, IntegrationResult):
            assert isinstance(full_integration, IntegrationResult)
            for field_name in _EXCLUDED_SELECTED_INTEGRATION_AUDIT_FIELDS:
                assert getattr(none_integration, field_name) == getattr(
                    full_integration,
                    field_name,
                ), field_name
        summary = full_cell.integration_audit
        if summary is None or summary.is_empty:
            continue
        audited_cell_count += 1
        assert isinstance(none_integration, IntegrationResult)
        for field_name, source_name in summary_sources.items():
            assert getattr(summary, field_name) == getattr(
                none_integration,
                source_name,
            ), field_name
    assert audited_cell_count > 0


def _matrix_with_cell(
    matrix: AlignmentMatrix,
    index: int,
    cell: AlignedCell,
) -> AlignmentMatrix:
    cells = list(matrix.cells)
    cells[index] = cell
    return replace(matrix, cells=tuple(cells))


def _mutate_dataclass_field(value: Any, field_name: str) -> Any:
    current = getattr(value, field_name)
    if current is None:
        replacement: object = 1.25
    elif isinstance(current, bool):
        replacement = not current
    elif isinstance(current, str):
        replacement = f"{current}-mutated"
    elif isinstance(current, (int, float)):
        replacement = current + 1
    else:
        raise AssertionError(f"unsupported mutation value: {current!r}")
    return replace(value, **{field_name: replacement})


def _render_audit_artifact_projection(
    directory: Path,
    matrix: AlignmentMatrix,
    *,
    alignment_config: Any,
    production_decisions: Any,
) -> None:
    directory.mkdir(parents=True)
    outputs = AlignmentRunOutputs(
        workbook=directory / "alignment_results.xlsx",
        cells_tsv=directory / "alignment_cells.tsv",
        backfill_cell_evidence_tsv=(directory / "alignment_backfill_cell_evidence.tsv"),
        integration_audit_tsv=(directory / "alignment_cell_integration_audit.tsv"),
        owner_backfill_peak_fact_provenance_tsv=(
            directory / "alignment_owner_backfill_peak_fact_provenance.tsv"
        ),
        run_metadata_json=directory / "alignment_run_metadata.json",
    )
    write_outputs_atomic(
        outputs,
        matrix,
        metadata={
            "audit_evidence_mode": "full",
            "requested_audit_evidence_mode": "full",
            "heavy_audit_enabled": "TRUE",
            "audit_evidence_mode_reason": "explicit_full",
        },
        ownership=OwnershipBuildResult(owners=(), assignments=(), ambiguous_records=()),
        alignment_config=alignment_config,
        production_decisions=production_decisions,
    )


def _audit_artifact_snapshot(directory: Path) -> dict[str, object]:
    return {
        "alignment_cells.tsv": (directory / "alignment_cells.tsv").read_bytes(),
        "alignment_cell_integration_audit.tsv": (
            directory / "alignment_cell_integration_audit.tsv"
        ).read_bytes(),
        "alignment_results.xlsx": _workbook_semantic_projection(
            directory / "alignment_results.xlsx"
        ),
        "alignment_run_metadata.json": (
            directory / "alignment_run_metadata.json"
        ).read_bytes(),
        "alignment_owner_backfill_peak_fact_provenance.tsv": (
            directory / "alignment_owner_backfill_peak_fact_provenance.tsv"
        ).read_bytes(),
        "alignment_backfill_cell_evidence.tsv": (
            directory / "alignment_backfill_cell_evidence.tsv"
        ).read_bytes(),
    }


def _workbook_semantic_projection(path: Path) -> tuple[object, ...]:
    workbook = openpyxl.load_workbook(path, data_only=False)
    return tuple(
        (
            sheet_name,
            tuple(
                tuple((cell.value, cell.data_type, cell.number_format) for cell in row)
                for row in workbook[sheet_name].iter_rows()
            ),
        )
        for sheet_name in workbook.sheetnames
    )


def _changed_tsv_columns(left: Path, right: Path) -> set[str]:
    with left.open(encoding="utf-8", newline="") as handle:
        left_reader = csv.DictReader(handle, delimiter="\t")
        left_fields = tuple(left_reader.fieldnames or ())
        left_rows = tuple(left_reader)
    with right.open(encoding="utf-8", newline="") as handle:
        right_reader = csv.DictReader(handle, delimiter="\t")
        right_fields = tuple(right_reader.fieldnames or ())
        right_rows = tuple(right_reader)
    assert left_fields == right_fields
    assert len(left_rows) == len(right_rows)
    return {
        field_name
        for left_row, right_row in zip(left_rows, right_rows, strict=True)
        for field_name in left_fields
        if left_row[field_name] != right_row[field_name]
    }


def _assert_exact_artifacts(
    left: _Run,
    right: _Run,
    names: set[str],
) -> None:
    for name in sorted(names):
        assert (left.output_dir / name).read_bytes() == (
            right.output_dir / name
        ).read_bytes(), name


def _assert_tsv_diff_whitelist(
    left: Path,
    right: Path,
    *,
    allowed_columns: frozenset[str],
) -> None:
    with left.open(encoding="utf-8", newline="") as handle:
        left_reader = csv.DictReader(handle, delimiter="\t")
        left_fields = tuple(left_reader.fieldnames or ())
        left_rows = tuple(left_reader)
    with right.open(encoding="utf-8", newline="") as handle:
        right_reader = csv.DictReader(handle, delimiter="\t")
        right_fields = tuple(right_reader.fieldnames or ())
        right_rows = tuple(right_reader)
    assert left_fields == right_fields
    assert allowed_columns <= set(left_fields)
    assert len(left_rows) == len(right_rows)
    changed_columns: set[str] = set()
    for left_row, right_row in zip(left_rows, right_rows, strict=True):
        for field in left_fields:
            if left_row[field] != right_row[field]:
                assert field in allowed_columns
                changed_columns.add(field)
    assert changed_columns <= allowed_columns


def _assert_metadata_audit_whitelist(left: _Run, right: _Run) -> None:
    assert left.metadata is not None
    assert right.metadata is not None
    assert tuple(left.metadata) == tuple(right.metadata)
    changed = {
        key for key in left.metadata if left.metadata[key] != right.metadata[key]
    }
    assert changed == _AUDIT_MODE_METADATA_KEYS
    for key in left.metadata:
        if key not in _AUDIT_MODE_METADATA_KEYS:
            assert left.metadata[key] == right.metadata[key]


def _assert_audit_workbook_whitelist(left: Path, right: Path) -> None:
    left_book = openpyxl.load_workbook(left, data_only=False)
    right_book = openpyxl.load_workbook(right, data_only=False)
    assert left_book.sheetnames == right_book.sheetnames
    changed_metadata_keys: set[str] = set()
    changed_audit_columns: set[str] = set()
    for sheet_name in left_book.sheetnames:
        left_sheet = left_book[sheet_name]
        right_sheet = right_book[sheet_name]
        assert left_sheet.max_row == right_sheet.max_row
        assert left_sheet.max_column == right_sheet.max_column
        assert tuple(left_sheet.merged_cells.ranges) == tuple(
            right_sheet.merged_cells.ranges
        )
        headers = tuple(cell.value for cell in left_sheet[1])
        assert headers == tuple(cell.value for cell in right_sheet[1])
        for row_index, (left_row, right_row) in enumerate(
            zip(left_sheet.iter_rows(), right_sheet.iter_rows(), strict=True),
            start=1,
        ):
            for column_index, (left_cell, right_cell) in enumerate(
                zip(left_row, right_row, strict=True),
                start=1,
            ):
                if left_cell.value == right_cell.value:
                    assert (left_cell.data_type, left_cell.number_format) == (
                        right_cell.data_type,
                        right_cell.number_format,
                    )
                    continue
                if sheet_name == "Audit" and row_index > 1:
                    header = headers[column_index - 1]
                    assert header in _AUDIT_SHEET_WHITELIST
                    assert left_cell.number_format == right_cell.number_format
                    assert "f" not in {left_cell.data_type, right_cell.data_type}
                    changed_audit_columns.add(str(header))
                    continue
                if sheet_name == "Metadata" and column_index == 2:
                    key = str(left_sheet.cell(row=row_index, column=1).value)
                    assert key == str(right_sheet.cell(row=row_index, column=1).value)
                    assert key in _AUDIT_MODE_METADATA_KEYS
                    assert left_cell.number_format == right_cell.number_format
                    assert "f" not in {left_cell.data_type, right_cell.data_type}
                    changed_metadata_keys.add(key)
                    continue
                raise AssertionError(
                    f"unexpected workbook diff at {sheet_name}!{left_cell.coordinate}"
                )
    assert changed_metadata_keys == _AUDIT_MODE_METADATA_KEYS
    assert changed_audit_columns <= _AUDIT_SHEET_WHITELIST


def _contains_trace_payload(value: object, seen: set[int] | None = None) -> bool:
    if isinstance(value, (np.ndarray, XICTrace)):
        return True
    if value is None or isinstance(value, (str, int, float, bool, bytes, Path)):
        return False
    seen = seen or set()
    identity = id(value)
    if identity in seen:
        return False
    seen.add(identity)
    if is_dataclass(value) and not isinstance(value, type):
        return any(
            _contains_trace_payload(getattr(value, field.name), seen)
            for field in fields(value)
        )
    if isinstance(value, Mapping):
        return any(
            _contains_trace_payload(key, seen) or _contains_trace_payload(item, seen)
            for key, item in value.items()
        )
    if isinstance(value, (Sequence, set, frozenset)):
        return any(_contains_trace_payload(item, seen) for item in value)
    return False


def _assert_workbook_semantic_parity(
    left: Path,
    right: Path,
    *,
    allowed_value_pairs: frozenset[tuple[Any, Any]] = frozenset(),
) -> None:
    left_book = openpyxl.load_workbook(left, data_only=False)
    right_book = openpyxl.load_workbook(right, data_only=False)
    observed_value_pairs: set[tuple[Any, Any]] = set()
    assert left_book.sheetnames == right_book.sheetnames
    for sheet_name in left_book.sheetnames:
        left_sheet = left_book[sheet_name]
        right_sheet = right_book[sheet_name]
        assert left_sheet.max_row == right_sheet.max_row
        assert left_sheet.max_column == right_sheet.max_column
        assert tuple(left_sheet.merged_cells.ranges) == tuple(
            right_sheet.merged_cells.ranges
        )
        for left_row, right_row in zip(
            left_sheet.iter_rows(),
            right_sheet.iter_rows(),
            strict=True,
        ):
            for left_cell, right_cell in zip(left_row, right_row, strict=True):
                assert (left_cell.data_type, left_cell.number_format) == (
                    right_cell.data_type,
                    right_cell.number_format,
                )
                if left_cell.value != right_cell.value:
                    pair = (left_cell.value, right_cell.value)
                    assert pair in allowed_value_pairs
                    observed_value_pairs.add(pair)
    assert observed_value_pairs == allowed_value_pairs


def _assert_process_compact_projection(
    run: _Run,
    *,
    matrix: AlignmentMatrix,
    alignment_config: Any,
) -> None:
    process_results = tuple(
        result
        for result in _InlineTypedExecutor.results
        if isinstance(result, OwnerBackfillSampleResult)
    )
    assert process_results
    assert all(not result.peak_fact_observations for result in process_results)
    prepared = next(
        item
        for result in process_results
        for item in result.prepared_peak_facts
        if item.physical_row.state == "available"
    )
    read_result = read_owner_backfill_peak_fact_provenance_tsv(
        run.output_dir / "alignment_owner_backfill_peak_fact_provenance.tsv"
    )
    assert prepared.physical_row in read_result.rows
    production_decisions = build_live_production_decisions(
        matrix,
        alignment_config,
    )
    composed = compose_owner_backfill_peak_fact_provenance(
        read_result,
        live_matrix=matrix,
        alignment_config=alignment_config,
        production_decisions=production_decisions,
    )
    assert all(
        claim.physical_claim == "unavailable_without_live_owner"
        for claim in composed.physical_claims
    )
    assert all(
        claim.cell_projection_claim == "unavailable_without_live_owner"
        for claim in composed.cell_claims
    )
