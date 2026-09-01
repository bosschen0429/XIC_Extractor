"""Activated Alignment entry for pure single-provider Untargeted CID-NL."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import replace
from pathlib import Path

from openpyxl import Workbook

from xic_extractor.alignment.artifact_write_receipts import (
    _mint_alignment_artifact_fingerprint_batch,
    _mint_alignment_artifact_fingerprint_receipt,
)
from xic_extractor.alignment.backfill_evidence_contract import (
    ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.csv_io import DiscoveryBatchInput
from xic_extractor.alignment.matrix_output_contract import (
    serialize_public_matrix_table,
)
from xic_extractor.alignment.output_levels import AlignmentOutputLevel
from xic_extractor.alignment.owner_backfill_peak_fact_provenance import (
    EMPTY_OWNER_BACKFILL_PEAK_FACT_PROVENANCE_BATCH,
    render_owner_backfill_peak_fact_provenance_tsv,
)
from xic_extractor.alignment.owner_build_route_receipt import (
    OwnerBuildRouteReceipt,
)
from xic_extractor.alignment.pipeline_outputs import AlignmentRunOutputs
from xic_extractor.alignment.raw_sources import existing_raw_paths
from xic_extractor.alignment.tsv_writer import (
    ALIGNMENT_CELLS_COLUMNS,
    ALIGNMENT_OWNER_BACKFILL_SEED_AUDIT_COLUMNS,
    ALIGNMENT_REVIEW_COLUMNS,
)
from xic_extractor.alignment.untargeted_cid_nl_artifact_delivery import (
    UntargetedCidNlArtifactPlan,
    UntargetedCidNlDeliveryReceipt,
    compile_untargeted_cid_nl_artifact_plan,
)
from xic_extractor.alignment.untargeted_finalized_matrix import (
    FinalizedUntargetMatrix,
    FinalizedUntargetMatrixRow,
    project_completed_untarget_batch_matrix,
)
from xic_extractor.alignment.workflow import CompletedUntargetBatchWorkflowResult
from xic_extractor.config import ExtractionConfig
from xic_extractor.diagnostics.timing import TimingRecorder
from xic_extractor.discovery.batch_contract import DiscoveryCandidateUniverseReceipt
from xic_extractor.discovery.models import DiscoveryCandidate, DiscoverySettings
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.execution_runtime_owner import (
    CoordinatorTerminalRecord,
    RuntimeArtifactIoSample,
    RuntimeMetricAvailability,
    RuntimeProcessTreePeakRssSample,
    RuntimeTelemetrySamples,
    RuntimeWorkerPeakRssSample,
    runtime_observation_event_count,
    runtime_started_worker_keys,
)
from xic_extractor.extraction.untargeted_cid_nl_production_batch import (
    build_untargeted_cid_nl_production_batch,
    compose_untargeted_cid_nl_production_result,
    execute_untargeted_cid_nl_production_batch,
)
from xic_extractor.processing_identity import fingerprint_file_content
from xic_extractor.sample_metadata import SampleMetadata


def run_untargeted_cid_nl_alignment(
    *,
    discovery_batch: DiscoveryBatchInput,
    candidates_by_sample: Mapping[str, tuple[DiscoveryCandidate, ...]],
    raw_dir: Path,
    dll_dir: Path,
    output_dir: Path,
    settings: DiscoverySettings,
    peak_config: ExtractionConfig,
    alignment_config: AlignmentConfig,
    output_level: AlignmentOutputLevel,
    emit_provisional_matrix: bool = False,
    emit_alignment_cells: bool = False,
    emit_alignment_status_matrix: bool = False,
    emit_alignment_integration_audit: bool = False,
    emit_alignment_backfill_seed_audit: bool = False,
    emit_alignment_backfill_candidate_audit: bool = False,
    capture_artifact_fingerprints: bool = False,
    owner_build_route_receipt: OwnerBuildRouteReceipt,
    raw_workers: int = 1,
    raw_xic_batch_size: int = 1,
    timing_recorder: TimingRecorder | None = None,
) -> AlignmentRunOutputs:
    """Run one sealed batch and write only its completed finalized result."""

    artifact_plan = compile_untargeted_cid_nl_artifact_plan(
        output_dir=output_dir,
        output_level=output_level,
        emit_provisional_matrix=emit_provisional_matrix,
        emit_alignment_cells=emit_alignment_cells,
        emit_alignment_status_matrix=emit_alignment_status_matrix,
        emit_alignment_integration_audit=emit_alignment_integration_audit,
        emit_alignment_backfill_seed_audit=emit_alignment_backfill_seed_audit,
        emit_alignment_backfill_candidate_audit=emit_alignment_backfill_candidate_audit,
        capture_artifact_fingerprints=capture_artifact_fingerprints,
    )
    raw_paths = existing_raw_paths(
        sample_order=discovery_batch.sample_order,
        raw_files=discovery_batch.raw_files,
        raw_dir=raw_dir,
    )
    if set(raw_paths) != set(discovery_batch.sample_order):
        missing = sorted(set(discovery_batch.sample_order) - set(raw_paths))
        raise ValueError(
            "CID-NL production requires RAW for every sample: " + ", ".join(missing)
        )
    receipts: dict[str, DiscoveryCandidateUniverseReceipt] = {}
    for sample_stem in discovery_batch.sample_order:
        receipt = discovery_batch.candidate_receipts[sample_stem]
        if not isinstance(receipt, DiscoveryCandidateUniverseReceipt):
            raise ValueError(
                "CID-NL production requires owner-closed Discovery receipts"
            )
        receipts[sample_stem] = receipt
    metadata = tuple(
        SampleMetadata(
            sample_name=sample_stem,
            raw_stem=sample_stem,
            injection_order=index,
            sample_role="study_sample",
        )
        for index, sample_stem in enumerate(discovery_batch.sample_order, start=1)
    )
    batch = build_untargeted_cid_nl_production_batch(
        raw_paths=tuple(raw_paths[sample] for sample in discovery_batch.sample_order),
        sample_metadata=metadata,
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample=candidates_by_sample,
        discovery_receipts_by_sample=receipts,
        dll_dir=dll_dir,
        sample_identity_namespace=f"alignment:{output_dir.resolve()}",
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    recorder = timing_recorder or TimingRecorder.disabled("alignment")
    with recorder.stage("alignment.untargeted_cid_nl_runtime") as stage:
        stage.metrics["raw_workers"] = raw_workers
        stage.metrics["raw_xic_batch_size"] = raw_xic_batch_size
        runtime = execute_untargeted_cid_nl_production_batch(
            batch,
            telemetry_provider=_coarse_runtime_telemetry,
            recovery_marker=OwnerPathRuntimeMarker(
                output_dir / ".cid_nl_runtime.json",
                "untargeted-cid-nl-alignment",
            ),
            progress_callback=recorder.heartbeat_event,
        )
    completed = compose_untargeted_cid_nl_production_result(
        batch,
        runtime,
        alignment_config=alignment_config,
    )
    return write_completed_untargeted_cid_nl_outputs(
        completed,
        artifact_plan=artifact_plan,
        owner_build_route_receipt=owner_build_route_receipt,
    )


def _coarse_runtime_telemetry(
    coordinator: CoordinatorTerminalRecord,
) -> RuntimeTelemetrySamples:
    event_count = runtime_observation_event_count(coordinator)
    worker_keys = runtime_started_worker_keys(coordinator)
    reason = "not_collected_by_alignment_foreground_runner"
    return RuntimeTelemetrySamples(
        event_monotonic_offsets_ns=(0,) * event_count,
        artifact_io=RuntimeArtifactIoSample(0, 0, 0, 0),
        process_tree_peak_rss=RuntimeProcessTreePeakRssSample(
            availability=RuntimeMetricAvailability.UNSUPPORTED,
            reason=reason,
        ),
        worker_peak_rss=tuple(
            RuntimeWorkerPeakRssSample(
                worker_key=key,
                availability=RuntimeMetricAvailability.UNSUPPORTED,
                reason=reason,
            )
            for key in worker_keys
        ),
    )


def write_completed_untargeted_cid_nl_outputs(
    completed: CompletedUntargetBatchWorkflowResult,
    *,
    artifact_plan: UntargetedCidNlArtifactPlan,
    owner_build_route_receipt: OwnerBuildRouteReceipt,
) -> AlignmentRunOutputs:
    if type(completed) is not CompletedUntargetBatchWorkflowResult:
        raise TypeError("writer requires the completed Untarget batch authority result")
    if not isinstance(owner_build_route_receipt, OwnerBuildRouteReceipt):
        raise TypeError("writer requires the validated owner build route receipt")
    if not isinstance(artifact_plan, UntargetedCidNlArtifactPlan):
        raise TypeError("writer requires an owner-compiled ArtifactPlan")
    matrix = project_completed_untarget_batch_matrix(completed)
    paths = artifact_plan.outputs
    output_dir = paths.matrix_tsv.parent if paths.matrix_tsv is not None else Path.cwd()
    paths = replace(
        paths,
        finalized_cell_provenance_tsv=output_dir
        / "alignment_finalized_cell_provenance.tsv",
    )
    matrix_rows = tuple(row for row in matrix.rows if row.destination == "matrix")
    payloads: dict[Path, bytes] = {}
    if paths.matrix_tsv is not None:
        payloads[paths.matrix_tsv] = _matrix_tsv(matrix.sample_stems, matrix_rows)
    assert paths.finalized_cell_provenance_tsv is not None
    payloads[paths.finalized_cell_provenance_tsv] = _provenance_tsv(matrix.rows)
    if paths.matrix_identity_tsv is not None:
        payloads[paths.matrix_identity_tsv] = _identity_tsv(matrix_rows)
    if paths.owner_backfill_peak_fact_provenance_tsv is not None:
        payloads[paths.owner_backfill_peak_fact_provenance_tsv] = (
            render_owner_backfill_peak_fact_provenance_tsv(
                EMPTY_OWNER_BACKFILL_PEAK_FACT_PROVENANCE_BATCH
            )
        )
    if paths.review_tsv is not None:
        payloads[paths.review_tsv] = _legacy_review_tsv(matrix.rows)
    if paths.cells_tsv is not None:
        payloads[paths.cells_tsv] = _legacy_cells_tsv(matrix.rows)
    if paths.backfill_seed_audit_tsv is not None:
        payloads[paths.backfill_seed_audit_tsv] = _legacy_seed_audit_tsv(matrix.rows)
    if paths.backfill_cell_evidence_tsv is not None:
        payloads[paths.backfill_cell_evidence_tsv] = _tsv_bytes(
            (ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,)
        )
    if paths.review_html is not None:
        payloads[paths.review_html] = _review_html(matrix).encode("utf-8")
    if paths.workbook is not None:
        payloads[paths.workbook] = _workbook_bytes(matrix)
    if paths.provisional_matrix_tsv is not None:
        assert paths.provisional_matrix_tsv is not None
        assert paths.provisional_identity_tsv is not None
        assert paths.provisional_cell_provenance_tsv is not None
        assert paths.provisional_summary_json is not None
        payloads[paths.provisional_matrix_tsv] = _matrix_tsv(
            matrix.sample_stems, matrix.rows
        )
        payloads[paths.provisional_identity_tsv] = _identity_tsv(matrix.rows)
        payloads[paths.provisional_cell_provenance_tsv] = _provenance_tsv(matrix.rows)
        payloads[paths.provisional_summary_json] = json.dumps(
            {
                "schema_version": "untargeted_cid_nl_finalized_matrix_v1",
                "row_count": len(matrix.rows),
                "matrix_row_count": len(matrix_rows),
                "archive_row_count": len(matrix.rows) - len(matrix_rows),
            },
            sort_keys=True,
            indent=2,
        ).encode("utf-8")
    _write_payloads_atomic(payloads)
    artifact_paths = tuple(
        sorted(
            (
                path
                for field in paths.__dataclass_fields__
                if isinstance((path := getattr(paths, field)), Path) and path.is_file()
            ),
            key=str,
        )
    )
    fingerprint_batch = (
        _mint_alignment_artifact_fingerprint_batch(
            artifact_paths=artifact_paths,
            receipts=tuple(
                _mint_alignment_artifact_fingerprint_receipt(
                    artifact_path=path,
                    fingerprint=fingerprint_file_content(path),
                )
                for path in artifact_paths
            ),
        )
        if artifact_plan.capture_artifact_fingerprints
        else None
    )
    passthrough = {
        field: getattr(paths, field)
        for field in paths.__dataclass_fields__
        if field
        not in {
            "artifact_fingerprint_batch",
            "owner_build_route_receipt",
            "owner_backfill_evaluation_coverage",
            "finalized_delivery_receipt",
        }
    }
    return AlignmentRunOutputs(
        **passthrough,
        artifact_fingerprint_batch=fingerprint_batch,
        owner_build_route_receipt=owner_build_route_receipt,
        finalized_delivery_receipt=UntargetedCidNlDeliveryReceipt(
            authority_scope=artifact_plan.authority_scope,
            authority_manifest_sha256=artifact_plan.authority_manifest_sha256,
            artifact_paths=artifact_paths,
        ),
    )


def _matrix_tsv(
    sample_stems: tuple[str, ...],
    rows: Sequence[FinalizedUntargetMatrixRow],
) -> bytes:
    table = serialize_public_matrix_table(
        (
            {
                "Mz": row.mz,
                "RT": row.rt_min,
                **{cell.sample_stem: cell.value for cell in row.cells},
            }
            for row in rows
        ),
        sample_columns=sample_stems,
    )
    return _tsv_bytes(
        (table.header,)
        + tuple(tuple(row[column] for column in table.header) for row in table.rows)
    )


def _identity_tsv(rows: Sequence[FinalizedUntargetMatrixRow]) -> bytes:
    return _tsv_bytes(
        (
            (
                "identity_schema_version",
                "untarget_row_key_id",
                "group_hypothesis_id",
                "destination",
                "Mz",
                "RT",
                "presentation_formula_version",
                "physical_observation_ids",
                "lineage_edge_ids",
            ),
        )
        + tuple(
            (
                "untargeted_cid_nl_matrix_identity_v1",
                row.row_id,
                row.group_hypothesis_id,
                row.destination,
                format(row.mz, ".12g"),
                format(row.rt_min, ".12g"),
                row.presentation_formula_version,
                "|".join(row.physical_observation_ids),
                "|".join(row.lineage_edge_ids),
            )
            for row in rows
        )
    )


def _legacy_review_tsv(rows: Sequence[FinalizedUntargetMatrixRow]) -> bytes:
    records = []
    for row in rows:
        record: dict[str, object] = {
            column: "" for column in ALIGNMENT_REVIEW_COLUMNS
        }
        record.update(
            feature_family_id=row.row_id,
            family_center_mz=row.mz,
            family_center_rt=row.rt_min,
            detected_count=sum(cell.value is not None for cell in row.cells),
            absent_count=sum(cell.value is None for cell in row.cells),
            include_in_primary_matrix=row.destination == "matrix",
            reason=row.destination,
        )
        records.append(record)
    return _mapping_tsv(ALIGNMENT_REVIEW_COLUMNS, records)


def _legacy_cells_tsv(rows: Sequence[FinalizedUntargetMatrixRow]) -> bytes:
    records = []
    for row in rows:
        for cell in row.cells:
            record: dict[str, object] = {
                column: "" for column in ALIGNMENT_CELLS_COLUMNS
            }
            record.update(
                feature_family_id=row.row_id,
                sample_stem=cell.sample_stem,
                status="detected" if cell.value is not None else "absent",
                area=cell.value,
                primary_matrix_area=cell.value,
                primary_matrix_area_source=cell.decision.source,
                family_center_mz=row.mz,
                family_center_rt=row.rt_min,
                reason=cell.reason,
            )
            records.append(record)
    return _mapping_tsv(ALIGNMENT_CELLS_COLUMNS, records)


def _legacy_seed_audit_tsv(rows: Sequence[FinalizedUntargetMatrixRow]) -> bytes:
    records = []
    for row in rows:
        for cell in row.cells:
            record: dict[str, object] = {
                column: "" for column in ALIGNMENT_OWNER_BACKFILL_SEED_AUDIT_COLUMNS
            }
            record.update(
                feature_family_id=row.row_id,
                sample_stem=cell.sample_stem,
                status="detected" if cell.value is not None else "absent",
                area=cell.value,
                family_center_mz=row.mz,
                family_center_rt=row.rt_min,
                reason=cell.reason,
            )
            records.append(record)
    return _mapping_tsv(ALIGNMENT_OWNER_BACKFILL_SEED_AUDIT_COLUMNS, records)


def _mapping_tsv(
    columns: Sequence[str], records: Sequence[Mapping[str, object]]
) -> bytes:
    return _tsv_bytes(
        (tuple(columns),)
        + tuple(
            tuple(record.get(column, "") for column in columns) for record in records
        )
    )


def _provenance_tsv(rows: Sequence[FinalizedUntargetMatrixRow]) -> bytes:
    records = [
        (
            "row_id",
            "row_route",
            "sample_stem",
            "variant",
            "reason",
            "value",
            "value_source",
            "decision_id",
            "evidence_id",
            "lineage_edge_ids",
        )
    ]
    for row in rows:
        for cell in row.cells:
            evidence = cell.decision.evidence
            evidence_id = next(
                (
                    getattr(evidence, field)
                    for field in (
                        "cell_observation_id",
                        "hypothesis_id",
                        "candidate_id",
                    )
                    if getattr(evidence, field, "")
                ),
                type(evidence).__name__,
            )
            records.append(
                (
                    row.row_id,
                    row.destination,
                    cell.sample_stem,
                    cell.variant,
                    cell.reason,
                    "" if cell.value is None else format(cell.value, ".12g"),
                    cell.decision.source,
                    cell.decision.decision_id,
                    str(evidence_id),
                    "|".join(row.lineage_edge_ids),
                )
            )
    return _tsv_bytes(tuple(records))


def _tsv_bytes(rows: Sequence[Sequence[object]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def _review_html(matrix: FinalizedUntargetMatrix) -> str:
    body = "".join(
        f"<tr><td>{row.row_id}</td><td>{row.destination}</td>"
        f"<td>{row.mz:.8g}</td><td>{row.rt_min:.8g}</td></tr>"
        for row in matrix.rows
    )
    return (
        "<!doctype html><meta charset='utf-8'><table><tr><th>row_id</th>"
        "<th>destination</th><th>Mz</th><th>RT</th></tr>" + body + "</table>"
    )


def _workbook_bytes(matrix: FinalizedUntargetMatrix) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Matrix"
    sheet.append(("Mz", "RT", *matrix.sample_stems))
    for row in matrix.rows:
        if row.destination == "matrix":
            sheet.append((row.mz, row.rt_min, *(cell.value for cell in row.cells)))
    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def _write_payloads_atomic(payloads: Mapping[Path, bytes]) -> None:
    temps: list[tuple[Path, Path]] = []
    backups: list[tuple[Path, Path]] = []
    replaced: list[Path] = []
    try:
        for final, payload in payloads.items():
            final.parent.mkdir(parents=True, exist_ok=True)
            temp = final.with_name(f".{final.name}.tmp")
            temp.write_bytes(payload)
            temps.append((temp, final))
        for _temp, final in temps:
            backup = final.with_name(f".{final.name}.bak")
            backup.unlink(missing_ok=True)
            if final.exists():
                final.replace(backup)
                backups.append((final, backup))
        for temp, final in temps:
            temp.replace(final)
            replaced.append(final)
        for _final, backup in backups:
            backup.unlink(missing_ok=True)
    except Exception:
        for final in replaced:
            final.unlink(missing_ok=True)
        for final, backup in reversed(backups):
            if backup.exists():
                final.unlink(missing_ok=True)
                backup.replace(final)
        raise
    finally:
        for temp, _final in temps:
            temp.unlink(missing_ok=True)
        for _final, backup in backups:
            with suppress(OSError):
                backup.unlink(missing_ok=True)


__all__ = [
    "run_untargeted_cid_nl_alignment",
    "write_completed_untargeted_cid_nl_outputs",
]
