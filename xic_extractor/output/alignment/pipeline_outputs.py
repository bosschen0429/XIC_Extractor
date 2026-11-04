from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from xic_extractor.alignment.membership_selection import AcceptedMembershipRisk

if TYPE_CHECKING:
    from xic_extractor.alignment.result.application_result import (
        FinalizedUntargetApplicationResult,
        FinalizedUntargetBatchApplicationResult,
    )
    from xic_extractor.evidence_artifacts import EvidenceLocator

from xic_extractor.alignment.backfill.evaluation_coverage import (
    BackfillEvaluationCoverageBatch,
)
from xic_extractor.alignment.backfill.peak_fact_provenance import (
    BACKFILL_PEAK_FACT_PROVENANCE_SCHEMA_VERSION,
    EMPTY_BACKFILL_PEAK_FACT_PROVENANCE_BATCH,
    BackfillPeakFactProvenanceBatch,
    render_backfill_peak_fact_provenance_tsv,
)
from xic_extractor.alignment.backfill.scope import (
    REQUEST_PLAN_VERSION,
)
from xic_extractor.alignment.config import (
    AlignmentConfig,
    BackfillXicBackend,
)
from xic_extractor.alignment.matrix import AlignmentMatrix
from xic_extractor.alignment.production_decisions import ProductionDecisionSet
from xic_extractor.alignment.untargeted_finalized_matrix import (
    CompletedUntargetBatchWriterInputs,
    completed_untarget_batch_sample_order,
    iter_completed_untarget_batch_writer_inputs,
    project_completed_untarget_batch_writer_inputs,
)
from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.configuration.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
    SignalProcessingProfile,
    serialize_signal_processing_profile,
)
from xic_extractor.execution.timing import TimingRecorder
from xic_extractor.output.alignment.output_levels import (
    AlignmentOutputLevel,
    artifact_names_for_output_level,
)
from xic_extractor.output.alignment.tsv_writer import (
    write_alignment_backfill_cell_evidence_tsv,
    write_alignment_backfill_seed_audit_tsv,
    write_alignment_cells_tsv,
    write_alignment_matrix_tsv,
    write_alignment_review_tsv,
    write_alignment_status_matrix_tsv,
    write_alignment_tsv_row_batches,
)


@dataclass(frozen=True)
class AlignmentRunOutputs:
    review_tsv: Path | None = None
    matrix_tsv: Path | None = None
    owner_backfill_peak_fact_provenance_tsv: Path | None = None
    backfill_cell_evidence_tsv: Path | None = None
    cells_tsv: Path | None = None
    backfill_seed_audit_tsv: Path | None = None
    status_matrix_tsv: Path | None = None
    run_metadata_json: Path | None = None
    owner_backfill_evaluation_coverage: BackfillEvaluationCoverageBatch | None = None
    # No live workflow result is published. The run closes its partition
    # store before returning, so a result handed back here would load its
    # children and assessments from a deleted spool. ``evidence`` carries the
    # durable locator instead.
    evidence: EvidenceLocator | None = None


def output_paths(
    output_dir: Path,
    *,
    output_level: AlignmentOutputLevel,
    emit_alignment_cells: bool,
    emit_alignment_status_matrix: bool,
    emit_alignment_backfill_seed_audit: bool = False,
    emit_target_snapshot_metadata: bool = False,
) -> AlignmentRunOutputs:
    artifacts = set(artifact_names_for_output_level(output_level))
    if emit_alignment_cells:
        artifacts.add("alignment_cells.tsv")
    if emit_alignment_status_matrix:
        artifacts.add("alignment_matrix_status.tsv")
    return AlignmentRunOutputs(
        review_tsv=(
            output_dir / "alignment_review.tsv"
            if "alignment_review.tsv" in artifacts
            else None
        ),
        matrix_tsv=(
            output_dir / "alignment_matrix.tsv"
            if "alignment_matrix.tsv" in artifacts
            else None
        ),
        owner_backfill_peak_fact_provenance_tsv=(
            output_dir / "alignment_owner_backfill_peak_fact_provenance.tsv"
            if "alignment_owner_backfill_peak_fact_provenance.tsv" in artifacts
            else None
        ),
        cells_tsv=(
            output_dir / "alignment_cells.tsv"
            if "alignment_cells.tsv" in artifacts
            else None
        ),
        backfill_cell_evidence_tsv=(
            output_dir / "alignment_backfill_cell_evidence.tsv"
            if "alignment_backfill_cell_evidence.tsv" in artifacts
            else None
        ),
        backfill_seed_audit_tsv=(
            output_dir / "alignment_owner_backfill_seed_audit.tsv"
            if emit_alignment_backfill_seed_audit
            else None
        ),
        status_matrix_tsv=(
            output_dir / "alignment_matrix_status.tsv"
            if "alignment_matrix_status.tsv" in artifacts
            else None
        ),
        run_metadata_json=(
            output_dir / "alignment_run_metadata.json"
            if emit_target_snapshot_metadata or "alignment_review.tsv" in artifacts
            else None
        ),
    )


def alignment_metadata(
    *,
    discovery_batch_index: Path,
    raw_dir: Path,
    dll_dir: Path,
    owner_backfill_xic_backend: BackfillXicBackend,
    output_level: AlignmentOutputLevel,
    peak_config: ExtractionConfig,
    owner_backfill_window_strategy: str = "exact",
    owner_backfill_superwindow_span_factor: int = 2,
    backfill_scope: str = "full-audit",
    output_scope: str = "full-audit",
    selected_family_count: int = 0,
    selected_family_source: str = "",
    request_plan_version: str = REQUEST_PLAN_VERSION,
    audit_evidence_mode: str = "full",
    requested_audit_evidence_mode: str = "auto",
    heavy_audit_enabled: bool = True,
    audit_evidence_mode_reason: str = "",
    scope_warning: str = "",
    signal_processing_profile: SignalProcessingProfile = (
        DEFAULT_SIGNAL_PROCESSING_PROFILE
    ),
    owner_backfill_peak_fact_provenance_row_count: int = 0,
) -> dict[str, str]:
    if not isinstance(signal_processing_profile, SignalProcessingProfile):
        raise TypeError("signal_processing_profile must be a SignalProcessingProfile")
    if (
        isinstance(owner_backfill_peak_fact_provenance_row_count, bool)
        or not isinstance(owner_backfill_peak_fact_provenance_row_count, int)
        or owner_backfill_peak_fact_provenance_row_count < 0
    ):
        raise ValueError(
            "owner_backfill_peak_fact_provenance_row_count must be non-negative"
        )
    metadata = {
        "schema_version": "alignment-results",
        "discovery_batch_index": str(discovery_batch_index),
        "raw_dir": str(raw_dir),
        "dll_dir": str(dll_dir),
        "owner_backfill_xic_backend": owner_backfill_xic_backend,
        "owner_backfill_window_strategy": owner_backfill_window_strategy,
        "owner_backfill_superwindow_span_factor": str(
            owner_backfill_superwindow_span_factor,
        ),
        "output_level": output_level,
        "resolver_mode": peak_config.resolver_mode,
        "baseline_integration_method": peak_config.baseline_integration_method,
        "backfill_scope": backfill_scope,
        "output_scope": output_scope,
        "selected_family_count": str(selected_family_count),
        "selected_family_source": selected_family_source,
        "require_discovery_owner_closure": "TRUE",
        "request_plan_version": request_plan_version,
        "audit_evidence_mode": audit_evidence_mode,
        "requested_audit_evidence_mode": requested_audit_evidence_mode,
        "heavy_audit_enabled": str(heavy_audit_enabled),
        "audit_evidence_mode_reason": audit_evidence_mode_reason,
        "scope_warning": scope_warning,
        "feature_id_policy": "report_scoped_measured_member_order",
        "matrix_split_evaluation_status": "complete_no_product_ready_split",
        "matrix_projection_status": "not_projection",
        "matrix_center_rt_basis": "cross_sample_owner_arithmetic_mean",
        "matrix_center_weight_basis": "unique_owner_observations_equal_weight",
        "matrix_value_policy": "canonical_signal_profile_selected_area",
        "signal_processing_profile_json": serialize_signal_processing_profile(
            signal_processing_profile
        ).decode("utf-8"),
        "owner_backfill_peak_fact_provenance_schema_version": (
            BACKFILL_PEAK_FACT_PROVENANCE_SCHEMA_VERSION
        ),
        "owner_backfill_peak_fact_provenance_row_count": str(
            owner_backfill_peak_fact_provenance_row_count
        ),
    }
    return metadata


def write_completed_untargeted_outputs_atomic(
    outputs: AlignmentRunOutputs,
    completed: FinalizedUntargetBatchApplicationResult,
    *,
    metadata: dict[str, str],
    alignment_config: AlignmentConfig,
    timing_recorder: TimingRecorder,
    consume_child: Callable[[FinalizedUntargetApplicationResult], None] | None = None,
    ms1_apex_mz_by_candidate: Mapping[str, float] | None = None,
    membership_risks_by_owner: Mapping[str, AcceptedMembershipRisk] | None = None,
    before_write: Callable[[], None] | None = None,
) -> int:
    """Publish completed matrix facts through the selected atomic output path."""
    stream_outputs = supports_streamed_untargeted_outputs(outputs)
    writer_inputs = None
    if not stream_outputs:
        with timing_recorder.stage("alignment.project_outputs"):
            writer_inputs = project_completed_untarget_batch_writer_inputs(
                completed,
                consume_child=consume_child,
                ms1_apex_mz_by_candidate=ms1_apex_mz_by_candidate,
                membership_risks_by_owner=membership_risks_by_owner,
            )
    if before_write is not None:
        before_write()
    with timing_recorder.stage("alignment.write_outputs"):
        if stream_outputs:
            return write_streamed_untargeted_outputs_atomic(
                outputs,
                iter_completed_untarget_batch_writer_inputs(
                    completed,
                    consume_child=consume_child,
                    ms1_apex_mz_by_candidate=ms1_apex_mz_by_candidate,
                    membership_risks_by_owner=membership_risks_by_owner,
                ),
                sample_order=completed_untarget_batch_sample_order(completed),
                metadata=metadata,
                alignment_config=alignment_config,
                timing_recorder=timing_recorder,
            )
        assert writer_inputs is not None
        write_outputs_atomic(
            outputs,
            writer_inputs.matrix,
            metadata=metadata,
            alignment_config=alignment_config,
            backfill_peak_fact_provenance=EMPTY_BACKFILL_PEAK_FACT_PROVENANCE_BATCH,
            production_decisions=writer_inputs.production_decisions,
            timing_recorder=timing_recorder,
        )
        return len(writer_inputs.matrix.clusters)


def write_outputs_atomic(
    outputs: AlignmentRunOutputs,
    matrix: AlignmentMatrix,
    *,
    metadata: dict[str, str],
    alignment_config: AlignmentConfig,
    backfill_peak_fact_provenance: BackfillPeakFactProvenanceBatch = (
        EMPTY_BACKFILL_PEAK_FACT_PROVENANCE_BATCH
    ),
    production_decisions: ProductionDecisionSet,
    timing_recorder: TimingRecorder | None = None,
) -> None:
    if not isinstance(
        backfill_peak_fact_provenance,
        BackfillPeakFactProvenanceBatch,
    ):
        raise TypeError("owner_backfill_peak_fact_provenance must be a typed batch")
    metadata = _metadata_bound_to_peak_fact_provenance(
        metadata,
        row_count=len(backfill_peak_fact_provenance.rows),
    )
    if not isinstance(production_decisions, ProductionDecisionSet):
        raise TypeError("production_decisions must be owner-minted")
    output_paths_and_writers: list[tuple[Path, Callable[[Path], Path]]] = []
    recorder = (
        timing_recorder
        if timing_recorder is not None and timing_recorder.instrument_writers
        else TimingRecorder.disabled("writer")
    )
    if outputs.matrix_tsv is not None:
        output_paths_and_writers.append(
            (
                outputs.matrix_tsv,
                lambda path: write_alignment_matrix_tsv(
                    path,
                    matrix,
                    alignment_config=alignment_config,
                    production_decisions=production_decisions,
                ),
            ),
        )
    if outputs.review_tsv is not None:
        output_paths_and_writers.append(
            (
                outputs.review_tsv,
                lambda path: write_alignment_review_tsv(
                    path,
                    matrix,
                    alignment_config=alignment_config,
                    production_decisions=production_decisions,
                ),
            ),
        )
    if outputs.cells_tsv is not None:
        output_paths_and_writers.append(
            (outputs.cells_tsv, lambda path: write_alignment_cells_tsv(path, matrix)),
        )
    if outputs.backfill_cell_evidence_tsv is not None:
        output_paths_and_writers.append(
            (
                outputs.backfill_cell_evidence_tsv,
                lambda path: write_alignment_backfill_cell_evidence_tsv(
                    path,
                    matrix,
                    production_decisions=production_decisions,
                ),
            ),
        )
    if outputs.backfill_seed_audit_tsv is not None:
        output_paths_and_writers.append(
            (
                outputs.backfill_seed_audit_tsv,
                lambda path: write_alignment_backfill_seed_audit_tsv(
                    path,
                    matrix,
                ),
            ),
        )
    if outputs.owner_backfill_peak_fact_provenance_tsv is not None:
        output_paths_and_writers.append(
            (
                outputs.owner_backfill_peak_fact_provenance_tsv,
                lambda path: _write_backfill_peak_fact_provenance_temp(
                    path,
                    backfill_peak_fact_provenance,
                ),
            )
        )
    if outputs.status_matrix_tsv is not None:
        output_paths_and_writers.append(
            (
                outputs.status_matrix_tsv,
                lambda path: write_alignment_status_matrix_tsv(path, matrix),
            )
        )
    if outputs.run_metadata_json is not None:
        output_paths_and_writers.append(
            (
                outputs.run_metadata_json,
                lambda path: _write_metadata_json(path, metadata),
            ),
        )
    _write_artifacts_atomic(
        output_paths_and_writers,
        recorder=recorder,
    )


def supports_streamed_untargeted_outputs(outputs: AlignmentRunOutputs) -> bool:
    """TSV-only finalized output needs no global cell or decision population."""

    return outputs.matrix_tsv is not None and not any(
        (
            outputs.status_matrix_tsv,
            outputs.backfill_seed_audit_tsv,
        )
    )


def write_streamed_untargeted_outputs_atomic(
    outputs: AlignmentRunOutputs,
    batches: Iterable[CompletedUntargetBatchWriterInputs],
    *,
    sample_order: tuple[str, ...],
    metadata: dict[str, str],
    alignment_config: AlignmentConfig,
    timing_recorder: TimingRecorder,
) -> int:
    """Render finalized rows once, then use the existing publication transaction."""

    if not supports_streamed_untargeted_outputs(outputs):
        raise ValueError("requested output consumer requires a materialized matrix")
    row_paths = {
        name: path
        for name, path in (
            ("matrix", outputs.matrix_tsv),
            ("review", outputs.review_tsv),
            ("backfill", outputs.backfill_cell_evidence_tsv),
            ("cells", outputs.cells_tsv),
        )
        if path is not None
    }
    paths = list(row_paths.values())
    if outputs.owner_backfill_peak_fact_provenance_tsv is not None:
        paths.append(outputs.owner_backfill_peak_fact_provenance_tsv)
    if outputs.run_metadata_json is not None:
        paths.append(outputs.run_metadata_json)
    row_count = 0

    def render_all(temp_paths: Mapping[Path, Path]) -> None:
        nonlocal row_count
        row_count = write_alignment_tsv_row_batches(
            {name: temp_paths[path] for name, path in row_paths.items()},
            batches,
            sample_order=sample_order,
            alignment_config=alignment_config,
        )
        if outputs.owner_backfill_peak_fact_provenance_tsv is not None:
            _write_backfill_peak_fact_provenance_temp(
                temp_paths[outputs.owner_backfill_peak_fact_provenance_tsv],
                EMPTY_BACKFILL_PEAK_FACT_PROVENANCE_BATCH,
            )
        if outputs.run_metadata_json is not None:
            _write_metadata_json(
                temp_paths[outputs.run_metadata_json],
                _metadata_bound_to_peak_fact_provenance(
                    metadata,
                    row_count=len(EMPTY_BACKFILL_PEAK_FACT_PROVENANCE_BATCH.rows),
                ),
            )

    _write_artifacts_atomic(
        [(path, None) for path in paths],
        recorder=(
            timing_recorder
            if timing_recorder.instrument_writers
            else TimingRecorder.disabled("writer")
        ),
        render_all=render_all,
    )
    return row_count


def _write_artifacts_atomic(
    output_paths_and_writers: Sequence[tuple[Path, Callable[[Path], Path] | None]],
    *,
    recorder: TimingRecorder,
    render_all: Callable[[Mapping[Path, Path]], None] | None = None,
) -> None:
    temp_paths = [
        _temp_path(final_path) for final_path, _writer in output_paths_and_writers
    ]
    backup_paths = [
        _backup_path(final_path) for final_path, _writer in output_paths_and_writers
    ]
    backups: list[tuple[Path, Path]] = []
    replaced_paths: list[Path] = []
    try:
        if render_all is not None:
            with recorder.stage("writer.streamed_rows.render"):
                render_all(
                    {path: _temp_path(path) for path, _ in output_paths_and_writers}
                )
        for final_path, writer in output_paths_and_writers:
            temp_path = _temp_path(final_path)
            artifact = _writer_artifact_name(final_path)
            render_elapsed = None
            if render_all is None:
                with recorder.stage(f"writer.{artifact}.render") as stage:
                    assert writer is not None
                    writer(temp_path)
                    stage.metrics["bytes"] = (
                        temp_path.stat().st_size if temp_path.exists() else None
                    )
                    stage.metrics["availability"] = "partial"
                    stage.metrics["reason"] = (
                        "renderer combines serialization and temp-file write"
                    )
                render_elapsed = stage.elapsed_sec
            rendered_bytes = temp_path.stat().st_size if temp_path.exists() else None
            recorder.observe(
                "writer_render_time_sec",
                value=render_elapsed,
                scope=artifact,
                unit="seconds",
                source="alignment.pipeline_outputs",
                availability="partial" if render_all is None else "unavailable",
                reason=(
                    "render timing includes the renderer's temp-file write"
                    if render_all is None
                    else (
                        "one shared streamed render is measured by "
                        "writer.streamed_rows.render"
                    )
                ),
            )
            recorder.observe(
                "writer_render_bytes",
                value=rendered_bytes,
                scope=artifact,
                unit="bytes",
                source="alignment.pipeline_outputs",
                availability="partial",
                reason=("render bytes include the renderer's temp-file write"),
            )
            recorder.observe(
                "writer_temp_write_time_sec",
                value=None,
                scope=artifact,
                unit="seconds",
                source="alignment.pipeline_outputs",
                availability="unavailable",
                reason=(
                    "renderer combines serialization and temp-file write; "
                    "phase cannot be separated without changing writer behavior"
                ),
            )
            recorder.observe(
                "writer_temp_write_bytes",
                value=None,
                scope=artifact,
                unit="bytes",
                source="alignment.pipeline_outputs",
                availability="unavailable",
                reason=(
                    "renderer combines serialization and temp-file write; "
                    "phase cannot be separated without changing writer behavior"
                ),
            )
        for final_path, _writer in output_paths_and_writers:
            backup_path = _backup_path(final_path)
            backup_path.unlink(missing_ok=True)
            if final_path.exists():
                final_path.replace(backup_path)
                backups.append((final_path, backup_path))
        for final_path, _writer in output_paths_and_writers:
            temp_path = _temp_path(final_path)
            artifact = _writer_artifact_name(final_path)
            with recorder.stage(f"writer.{artifact}.rename") as stage:
                temp_path.replace(final_path)
                renamed_bytes = (
                    final_path.stat().st_size if final_path.exists() else None
                )
                stage.metrics["bytes"] = renamed_bytes
            recorder.observe(
                "writer_rename_time_sec",
                value=stage.elapsed_sec,
                scope=artifact,
                unit="seconds",
                source="alignment.pipeline_outputs",
            )
            recorder.observe(
                "writer_rename_bytes",
                value=renamed_bytes,
                scope=artifact,
                unit="bytes",
                source="alignment.pipeline_outputs",
            )
            replaced_paths.append(final_path)
        for _final_path, backup_path in backups:
            with suppress(OSError):
                backup_path.unlink(missing_ok=True)
    except Exception:
        for final_path in replaced_paths:
            final_path.unlink(missing_ok=True)
        for final_path, backup_path in reversed(backups):
            if backup_path.exists():
                final_path.unlink(missing_ok=True)
                backup_path.replace(final_path)
        for temp_path in temp_paths:
            temp_path.unlink(missing_ok=True)
        for backup_path in backup_paths:
            backup_path.unlink(missing_ok=True)
        raise


def _metadata_nonnegative_int(metadata: dict[str, str], key: str) -> int:
    value = metadata.get(key, "")
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"alignment metadata {key} must be an integer") from exc
    if parsed < 0:
        raise ValueError(f"alignment metadata {key} must be non-negative")
    return parsed


def _metadata_bound_to_peak_fact_provenance(
    metadata: dict[str, str],
    *,
    row_count: int,
) -> dict[str, str]:
    key = "owner_backfill_peak_fact_provenance_row_count"
    bound = dict(metadata)
    if key in bound and _metadata_nonnegative_int(bound, key) != row_count:
        raise ValueError(
            "alignment metadata owner_backfill_peak_fact_provenance_row_count "
            "does not match the typed provenance batch"
        )
    bound[key] = str(row_count)
    return bound


def _write_backfill_peak_fact_provenance_temp(
    path: Path,
    batch: BackfillPeakFactProvenanceBatch,
) -> Path:
    """Write owner-rendered bytes into the outer transaction's one temp path."""

    payload = render_backfill_peak_fact_provenance_tsv(batch)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _temp_path(final_path: Path) -> Path:
    return final_path.with_name(f"{final_path.name}.tmp")


def _write_metadata_json(path: Path, metadata: dict[str, str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _backup_path(final_path: Path) -> Path:
    return final_path.with_name(f"{final_path.name}.bak")


def _writer_artifact_name(path: Path) -> str:
    """Return a stable diagnostic name without exposing absolute paths."""

    name = path.name
    if name.endswith(".tsv"):
        name = name[:-4]
    elif name.endswith(".json"):
        name = name[:-5]
    elif name.endswith(".xlsx"):
        name = name[:-5]
    elif name.endswith(".html"):
        name = name[:-5]
    return name.replace(".", "_").replace("-", "_")
